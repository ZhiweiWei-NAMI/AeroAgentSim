"""Model discovery shares admission without widening legacy duplicate rejection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest

import aeroagentsim.models as models
from aeroagentsim.extensions import DuplicateEntryPointError, discover


def _forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Discovery must not load or invoke this provider")


def _entry(
    name: Any, value: Any = _forbidden, *, loads: list[Any],
    distribution: str | None = "external-dist", error: Exception | None = None,
) -> Any:
    def load() -> Any:
        loads.append(name)
        if error is not None:
            raise error
        return value

    return SimpleNamespace(
        name=name, value="external.provider:factory",
        dist=SimpleNamespace(name=distribution, version=None), load=load,
    )


def _install(monkeypatch: pytest.MonkeyPatch, *entries: Any) -> list[str]:
    queries: list[str] = []

    def query(*, group: str) -> Any:
        assert group == "aeroagentsim.models"
        queries.append(group)
        return iter(entries)

    monkeypatch.setattr(models, "entry_points", query)
    return queries


def _build() -> models.ModelBuild:
    registry: Any = SimpleNamespace(field=_forbidden)
    return models.ModelBuild({}, {"idle_w": 1.0, "per_m_j": 2.0}, registry, ())


def _config(build: models.ModelBuild) -> dict[str, Any]:
    return {
        "step_ns": 1, "energy": build.energy,
        "max_speed_m_s": 1.0, "max_accel_m_s2": 1.0,
    }


@pytest.mark.parametrize("name", ["linear", "wind", "external", ""])
def test_selected_duplicate_keeps_legacy_error_and_all_conflict_evidence(
    monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    loads: list[Any] = []
    queries = _install(
        monkeypatch,
        _entry("unused", loads=loads), _entry("unused", loads=loads),
        _entry(name, loads=loads, distribution="first-dist"),
        _entry(name, loads=loads, distribution="second-dist"),
        _entry(name, loads=loads, distribution=None),
    )
    monkeypatch.setattr(models, "LinearConsumption", _forbidden)
    monkeypatch.setattr(models, "WindConsumption", _forbidden)
    catalog = models.ModelCatalog()
    assert queries == [] and catalog.conflicts == ()
    with pytest.raises(ValueError) as error:
        catalog.build(name, _build())
    assert type(error.value) is ValueError
    assert str(error.value) == f"duplicate aeroagentsim.models entry point {name!r}"
    cause = error.value.__cause__
    assert isinstance(cause, DuplicateEntryPointError)
    assert cause.conflict == catalog.conflicts[1]
    assert cause.conflict.group == "aeroagentsim.models"
    assert cause.conflict.name == name
    assert tuple(row.distribution for row in cause.conflict.entries) == (
        "first-dist", "second-dist", None,
    )
    assert all(row.version is None for row in cause.conflict.entries)
    assert queries == ["aeroagentsim.models"] and loads == []


def test_unselected_duplicates_are_tolerated_with_detached_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[Any] = []
    entries = (
        _entry("unused", loads=loads, distribution="same-dist"),
        _entry("unused", loads=loads, distribution="same-dist"),
        _entry("unused", loads=loads, distribution=None),
    )
    queries = _install(monkeypatch, *entries)
    catalog = models.ModelCatalog()
    assert isinstance(catalog.build("linear", _build()), models.LinearConsumption)
    assert queries == ["aeroagentsim.models"] and loads == []
    assert len(catalog.conflicts) == 1
    conflict = catalog.conflicts[0]
    assert conflict.group == "aeroagentsim.models" and conflict.name == "unused"
    assert tuple(row.distribution for row in conflict.entries) == (
        "same-dist", "same-dist", None,
    )
    assert all(row.version is None for row in conflict.entries)
    entries[0].dist.name = "changed"
    assert conflict.entries[0].distribution == "same-dist"
    with pytest.raises(FrozenInstanceError):
        setattr(conflict, "name", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(conflict.entries[0], "distribution", "changed")

    # A catalog still discovers on every build, rather than caching an installation.
    queries = _install(monkeypatch)
    assert isinstance(catalog.build("linear", _build()), models.LinearConsumption)
    assert catalog.conflicts == ()
    assert queries == ["aeroagentsim.models"] and loads == []


def test_implicit_linear_bypasses_discovery_and_installed_replacements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(models, "entry_points", _forbidden)
    monkeypatch.setattr(models, "discover", _forbidden)
    monkeypatch.setattr(models.ModelCatalog, "build", _forbidden)
    build = _build()
    motion = models.motion_model(_config(build), build.registry, build.entities)
    assert type(motion.consumption) is models.LinearConsumption
    assert motion.consumption.idle == build.energy["idle_w"]
    assert motion.consumption.per_m == build.energy["per_m_j"]


@pytest.mark.parametrize("name", ["linear", "wind", "External.Model"])
def test_explicit_selection_loads_only_the_installed_factory_and_never_the_builtin(
    monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    loads: list[Any] = []
    calls: list[models.ModelBuild] = []
    fields: list[str] = []
    build = _build()
    build.registry.field = fields.append
    replacement = SimpleNamespace(dependencies=("external.field",))

    def factory(context: models.ModelBuild) -> Any:
        calls.append(context)
        return replacement

    # No descriptor-name or API-version requirement is invented for factories.
    factory.name = "not a descriptor namespace"
    factory.api_version = None
    queries = _install(
        monkeypatch, _entry(name, factory, loads=loads),
        _entry("unused", loads=loads, error=RuntimeError("unselected import")),
        _entry("duplicate", loads=loads), _entry("duplicate", loads=loads),
        _entry("", loads=loads),
    )
    monkeypatch.setattr(models, "LinearConsumption", _forbidden)
    monkeypatch.setattr(models, "WindConsumption", _forbidden)
    selected_config = {"coefficient": 7}
    config = _config(build)
    config["consumption_model"] = {"plugin": name, "config": selected_config}
    motion = models.motion_model(config, build.registry, build.entities)
    assert motion.consumption is replacement
    assert len(calls) == 1
    assert calls[0].config is selected_config
    assert calls[0].energy is build.energy
    assert calls[0].registry is build.registry
    assert calls[0].entities is build.entities
    assert fields == ["external.field"]
    assert queries == ["aeroagentsim.models"] and loads == [name]


@pytest.mark.parametrize("stage", ["load", "factory", "dependency"])
@pytest.mark.parametrize("failure_type", [ImportError, TypeError, ValueError, RuntimeError, KeyError])
def test_broken_selected_replacement_fails_closed_with_unchanged_error(
    monkeypatch: pytest.MonkeyPatch, stage: str, failure_type: type[Exception],
) -> None:
    loads: list[Any] = []
    failure = failure_type("model provider failed")
    build = _build()

    def fail(*args: Any) -> Any:
        raise failure

    replacement = SimpleNamespace(dependencies=("external.field",))
    factory = fail if stage == "factory" else lambda context: replacement
    if stage == "dependency":
        build.registry.field = fail
    _install(monkeypatch, _entry(
        "linear", factory, loads=loads, error=failure if stage == "load" else None,
    ))
    monkeypatch.setattr(models, "LinearConsumption", _forbidden)
    with pytest.raises(failure_type) as error:
        models.ModelCatalog().build("linear", build)
    assert error.value is failure
    assert type(error.value) is failure_type
    assert str(error.value) == str(failure)
    assert loads == ["linear"]


@pytest.mark.parametrize("value", [None, 1, True, object()])
def test_nonfactory_preserves_native_type_error_without_builtin_fallback(
    monkeypatch: pytest.MonkeyPatch, value: Any,
) -> None:
    loads: list[Any] = []
    _install(monkeypatch, _entry("linear", value, loads=loads))
    monkeypatch.setattr(models, "LinearConsumption", _forbidden)
    with pytest.raises(TypeError) as error:
        models.ModelCatalog().build("linear", _build())
    assert type(error.value) is TypeError
    assert str(error.value) == f"'{type(value).__name__}' object is not callable"
    assert loads == ["linear"]


@pytest.mark.parametrize("name", ["missing", "", None, True, []])
def test_unknown_selection_keeps_legacy_message_despite_unrelated_duplicates(
    monkeypatch: pytest.MonkeyPatch, name: Any,
) -> None:
    loads: list[Any] = []
    _install(monkeypatch, _entry("unused", loads=loads), _entry("unused", loads=loads))
    with pytest.raises(ValueError) as error:
        models.ModelCatalog().build(name, _build())
    assert type(error.value) is ValueError
    assert str(error.value) == f"unknown aeroagentsim.models plugin {name!r}"
    assert loads == []


@pytest.mark.parametrize("name", ["", None, True])
def test_selected_malformed_entry_name_is_rejected_before_loading(
    monkeypatch: pytest.MonkeyPatch, name: Any,
) -> None:
    loads: list[Any] = []
    _install(monkeypatch, _entry(name, loads=loads))
    with pytest.raises(ValueError) as error:
        models.ModelCatalog().build(name, _build())
    assert str(error.value) == (
        f"aeroagentsim.models entry point {name!r}: name must be nonempty text"
    )
    assert loads == []


def test_selected_only_is_per_call_and_never_relaxes_eager_or_explicit_uniqueness() -> None:
    loads: list[Any] = []
    provider = object()
    entries = (
        _entry("unused", loads=loads), _entry("unused", loads=loads),
        _entry("selected", provider, loads=loads),
    )
    result = discover("example.providers", {}, entries=entries,
                      expected_type=object, load=False)
    conflicts = result.conflicts
    assert result.providers == () and loads == []
    assert result.resolve("selected", selected_only=True) is provider
    assert result.conflicts is conflicts
    assert loads == ["selected"]
    with pytest.raises(DuplicateEntryPointError) as error:
        result.resolve("unused", selected_only=True)
    assert error.value.conflict is conflicts[0]
    with pytest.raises(KeyError) as error:
        result.resolve("missing", selected_only=True)
    assert error.value.args == ("missing",)
    with pytest.raises(DuplicateEntryPointError):
        result.resolve("selected")
    with pytest.raises(DuplicateEntryPointError):
        result.require_unique()
    with pytest.raises(DuplicateEntryPointError):
        discover("example.providers", {}, entries=entries, expected_type=object)
    assert loads == ["selected"]


@pytest.mark.parametrize("malformed", [True, False])
def test_selected_only_does_not_skip_name_or_type_admission(malformed: bool) -> None:
    loads: list[Any] = []
    name = "MixedCase" if malformed else "valid"
    result = discover(
        "example.providers", {}, load=False, expected_type=str, factory_label=None,
        entries=(_entry("unused", loads=loads), _entry("unused", loads=loads),
                 _entry(name, object(), loads=loads)),
    )
    with pytest.raises(ValueError if malformed else TypeError) as error:
        result.resolve(name, selected_only=True)
    assert not isinstance(error.value, DuplicateEntryPointError)
    message = "name must match" if malformed else "expected a str"
    assert message in str(error.value)
    assert loads == ([] if malformed else [name])


@pytest.mark.parametrize(("module_name", "function_name", "family"), [
    ("plugin_api", "discover_scenario_plugins", "scenarios"),
    ("metrics_api", "discover_metric_plugins", "metrics"),
    ("platform.plugins", "EngineCatalog", "engines"),
    ("algorithms", "load_algorithm", "algorithms"),
    ("assistant_api", "discover_assistant_plugins", "assistant_operations"),
    ("presentation_api", "discover_presentation_plugins", "presentations"),
    ("preparation_api", "discover_preparers", "preparers"),
])
def test_other_families_still_reject_unrelated_duplicates_before_any_load(
    monkeypatch: pytest.MonkeyPatch, module_name: str, function_name: str, family: str,
) -> None:
    module = import_module(f"aeroagentsim.{module_name}")
    loads: list[Any] = []
    entries = (
        _entry("selected", loads=loads),
        _entry("unused", loads=loads), _entry("unused", loads=loads),
    )

    def query(*, group: str) -> tuple[Any, ...]:
        assert group == f"aeroagentsim.{family}"
        return entries

    monkeypatch.setattr(module, "entry_points", query)
    if family == "presentations":
        module.clear_presentation_plugin_cache()
    try:
        with pytest.raises(ValueError) as error:
            invoke = getattr(module, function_name)
            if family == "algorithms":
                invoke("allocation", "selected")
            elif family == "presentations":
                invoke(names=("selected",))
            else:
                invoke()
    finally:
        if family == "presentations":
            module.clear_presentation_plugin_cache()
    if family in ("presentations", "preparers"):
        cause = error.value
    else:
        assert type(error.value) is ValueError
        message = {
            "engines": "Duplicate aeroagentsim.engines entry point names",
            "algorithms": "duplicate aeroagentsim.algorithms entry point names",
        }.get(family, f"Duplicate aeroagentsim.{family} entry point name: 'unused'")
        assert str(error.value) == message
        cause = error.value.__cause__
    assert isinstance(cause, DuplicateEntryPointError)
    assert cause.conflict.group == f"aeroagentsim.{family}"
    assert cause.conflict.name == "unused"
    assert loads == []
