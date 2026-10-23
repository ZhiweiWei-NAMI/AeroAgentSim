"""Remaining discovery boundaries keep their legacy selection and error contracts.

Models deliberately retain selected-name-only admission; engines already use the
shared helper. Neither migration nor catalog inspection may construct unselected
runtime providers.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import aeroagentsim.algorithms as algorithms
import aeroagentsim.assistant_api as assistant_api
import aeroagentsim.models as models
from aeroagentsim.extensions import DuplicateEntryPointError
from aeroagentsim.platform import plugins as engine_plugins
from aeroagentsim.services import algorithm_catalog

SITES = ("algorithms", "algorithm_catalog", "engines", "assistant")
GROUPS = {
    "algorithms": "aeroagentsim.algorithms",
    "algorithm_catalog": "aeroagentsim.algorithms",
    "engines": "aeroagentsim.engines",
    "assistant": "aeroagentsim.assistant_operations",
}
BUILTIN_NAMES = {
    "algorithms": "nearest", "algorithm_catalog": "nearest",
    "engines": "records", "assistant": "lowalt",
}


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
        name=name, value="external.provider:plugin",
        dist=SimpleNamespace(name=distribution, version=None), load=load,
    )


def _install(monkeypatch: pytest.MonkeyPatch, site: str, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == GROUPS[site]
        return entries

    target = {
        "algorithms": algorithms, "algorithm_catalog": algorithm_catalog.metadata,
        "engines": engine_plugins, "assistant": assistant_api,
    }[site]
    monkeypatch.setattr(target, "entry_points", query)
    # Engine-consumer identity inspection is not algorithm provider admission.
    monkeypatch.setattr(algorithm_catalog, "slot_consumers", lambda: {})


def _isolate_builtins(monkeypatch: pytest.MonkeyPatch, site: str) -> None:
    if site in ("algorithms", "algorithm_catalog"):
        monkeypatch.setattr(algorithms, "import_module", _forbidden)
    elif site == "engines":
        monkeypatch.setattr(engine_plugins, "BUILTINS", {"records": "never.import"})
        monkeypatch.setattr(engine_plugins, "import_module", _forbidden)
    else:
        monkeypatch.setattr(assistant_api, "BUILTINS", {"lowalt": _forbidden})


def _invoke(site: str, name: str | None = None) -> Any:
    selected = BUILTIN_NAMES[site] if name is None else name
    if site == "algorithms":
        return algorithms.load_algorithm("allocation", selected, {"limit": 7})
    if site == "algorithm_catalog":
        return algorithm_catalog.discover_algorithm_catalog()
    if site == "engines":
        return engine_plugins.EngineCatalog().factory(selected)
    return assistant_api.discover_assistant_plugins()


def _algorithm_factory(calls: list[int]) -> Any:
    class ExternalAlgorithm:
        label = "External allocation"
        description = "An installed implementation, not builtin metadata."

        def __init__(self, *, limit: int) -> None:
            calls.append(limit)

        def allocate(self, snapshot: Any) -> Any:
            return _forbidden()

    return ExternalAlgorithm


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("name", ["external", ""])
def test_duplicates_keep_legacy_errors_and_all_evidence_before_any_load(
    monkeypatch: pytest.MonkeyPatch, site: str, name: str,
) -> None:
    _isolate_builtins(monkeypatch, site)
    loads: list[Any] = []
    _install(
        monkeypatch, site, _entry("aaa-installed", loads=loads),
        _entry(name, loads=loads, distribution="first-dist"),
        _entry(name, loads=loads, distribution="second-dist"),
        _entry(name, loads=loads, distribution=None),
    )
    with pytest.raises(ValueError) as error:
        _invoke(site)
    assert type(error.value) is ValueError
    assert str(error.value) == {
        "algorithms": "duplicate aeroagentsim.algorithms entry point names",
        "algorithm_catalog": "Duplicate aeroagentsim.algorithms entry point names",
        "engines": "Duplicate aeroagentsim.engines entry point names",
        "assistant": f"Duplicate aeroagentsim.assistant_operations entry point name: {name!r}",
    }[site]
    cause = error.value.__cause__
    assert isinstance(cause, DuplicateEntryPointError)
    assert cause.conflict.group == GROUPS[site]
    assert cause.conflict.name == name
    assert tuple(row.distribution for row in cause.conflict.entries) == (
        "first-dist", "second-dist", None,
    )
    assert all(row.version is None for row in cause.conflict.entries)
    assert loads == []


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("name", ["", None, True])
def test_invalid_names_are_rejected_before_loading_even_unrelated_providers(
    monkeypatch: pytest.MonkeyPatch, site: str, name: Any,
) -> None:
    _isolate_builtins(monkeypatch, site)
    loads: list[Any] = []
    _install(monkeypatch, site, _entry("aaa-installed", loads=loads),
             _entry(name, loads=loads))
    with pytest.raises(ValueError) as error:
        _invoke(site)
    if site == "assistant":
        assert str(error.value) == f"assistant entry point {name!r}: invalid plugin name"
        assert isinstance(error.value.__cause__, ValueError)
    else:
        assert str(error.value) == (
            f"{GROUPS[site]} entry point {name!r}: name must be nonempty text"
        )
    assert loads == []


@pytest.mark.parametrize("site", ("algorithms", "algorithm_catalog"))
def test_slot_qualified_algorithm_requires_an_id_before_loading(
    monkeypatch: pytest.MonkeyPatch, site: str,
) -> None:
    _isolate_builtins(monkeypatch, site)
    loads: list[Any] = []
    _install(monkeypatch, site, _entry("aaa-installed", loads=loads),
             _entry("allocation.", loads=loads))
    with pytest.raises(ValueError) as error:
        _invoke(site)
    assert str(error.value) == "Algorithm entry point 'allocation.' has no id"
    assert loads == []


@pytest.mark.parametrize("site", SITES)
def test_installed_provider_replaces_the_whole_same_name_builtin(
    monkeypatch: pytest.MonkeyPatch, site: str,
) -> None:
    _isolate_builtins(monkeypatch, site)
    loads: list[Any] = []
    calls: list[int] = []
    name = BUILTIN_NAMES[site]
    if site == "assistant":
        replacement = assistant_api.AssistantOperationPlugin(name)
    elif site == "engines":
        replacement = _forbidden
    else:
        replacement = _algorithm_factory(calls)
    _install(monkeypatch, site, _entry(name, replacement, loads=loads))
    result = _invoke(site)
    if site == "algorithms":
        assert isinstance(result, replacement)
        assert calls == [7]
    elif site == "algorithm_catalog":
        row = next(row for row in result["allocation"] if row["id"] == name)
        assert row == {
            "id": name, "label": replacement.label,
            "description": replacement.description, "consumers": [],
        }
        assert calls == []  # No constructor call or inherited builtin translations.
    elif site == "engines":
        assert result is replacement
    else:
        assert result == (replacement,)
        assert result[0] is replacement
    assert loads == [name]


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("failure_type", [ImportError, TypeError, ValueError, RuntimeError])
def test_broken_installed_replacement_fails_closed_with_legacy_load_error(
    monkeypatch: pytest.MonkeyPatch, site: str, failure_type: type[Exception],
) -> None:
    _isolate_builtins(monkeypatch, site)
    loads: list[Any] = []
    name = BUILTIN_NAMES[site]
    failure = failure_type("provider import failed")
    _install(monkeypatch, site, _entry(name, loads=loads, error=failure))
    expected = ValueError if site == "algorithm_catalog" else failure_type
    with pytest.raises(expected) as error:
        _invoke(site)
    assert type(error.value) is expected
    if site == "algorithm_catalog":
        assert str(error.value) == (
            f"Cannot load algorithm entry point {name!r}: {failure_type.__name__}"
        )
        assert error.value.__cause__ is failure
    else:
        assert error.value is failure
        assert str(error.value) == "provider import failed"
    assert loads == [name]


@pytest.mark.parametrize("site", ("algorithms", "algorithm_catalog", "engines"))
def test_nonfactory_keeps_legacy_failure_without_builtin_fallback(
    monkeypatch: pytest.MonkeyPatch, site: str,
) -> None:
    _isolate_builtins(monkeypatch, site)
    loads: list[Any] = []
    name = BUILTIN_NAMES[site]
    _install(monkeypatch, site, _entry(name, None, loads=loads))
    expected = ValueError if site == "algorithm_catalog" else TypeError
    with pytest.raises(expected) as error:
        _invoke(site)
    assert str(error.value) == {
        "algorithms": "'NoneType' object is not callable",
        "algorithm_catalog": (
            "Entry point 'nearest' needs a slot-qualified name or an algorithm protocol method"
        ),
        "engines": "plugin 'records': engine factory must be callable",
    }[site]
    assert loads == [name]


def test_algorithm_loads_only_qualified_winner_and_passes_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_builtins(monkeypatch, "algorithms")
    loads: list[Any] = []
    calls: list[int] = []
    factory = _algorithm_factory(calls)
    _install(
        monkeypatch, "algorithms", _entry("nearest", loads=loads),
        _entry("allocation.nearest", factory, loads=loads),
        _entry("unused", loads=loads, error=RuntimeError("unselected import")),
    )
    assert isinstance(_invoke("algorithms"), factory)
    assert calls == [7] and loads == ["allocation.nearest"]


@pytest.mark.parametrize("source", ["qualified", "unqualified", "module"])
def test_algorithm_module_reference_remains_below_installed_lookup(
    monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    loads: list[Any] = []
    calls: list[int] = []
    imports: list[str] = []
    factory = _algorithm_factory(calls)
    name = "external.Legacy:Algorithm"

    def import_module(module: str) -> Any:
        imports.append(module)
        return SimpleNamespace(Algorithm=factory)

    monkeypatch.setattr(algorithms, "import_module", import_module)
    entries = []
    if source != "module":
        entries.append(_entry(name, factory if source == "unqualified" else _forbidden,
                              loads=loads))
    if source == "qualified":
        entries.append(_entry(f"allocation.{name}", factory, loads=loads))
    _install(monkeypatch, "algorithms", *entries)
    assert isinstance(_invoke("algorithms", name), factory)
    assert calls == [7]
    assert imports == (["external.Legacy"] if source == "module" else [])
    assert loads == {
        "qualified": [f"allocation.{name}"], "unqualified": [name], "module": [],
    }[source]


@pytest.mark.parametrize(("slot", "name", "module", "identity"), [
    ("allocation", "nearest", ".builtins", "Nearest"),
    ("allocation", "llm", ".llm", "LLMAllocation"),
    ("path_planning", "survey", ".routes", "SurveyRoute"),
])
def test_algorithm_builtin_fallback_imports_only_the_selected_module(
    monkeypatch: pytest.MonkeyPatch, slot: str, name: str, module: str, identity: str,
) -> None:
    calls: list[int] = []
    imports: list[tuple[str, str]] = []
    loads: list[Any] = []
    factory = _algorithm_factory(calls)
    factory.plan = _forbidden

    def import_module(selected: str, package: str) -> Any:
        imports.append((selected, package))
        return SimpleNamespace(**{identity: factory})

    monkeypatch.setattr(algorithms, "import_module", import_module)
    _install(monkeypatch, "algorithms", _entry("unused", loads=loads))
    assert isinstance(algorithms.load_algorithm(slot, name, {"limit": 7}), factory)
    assert imports == [(module, "aeroagentsim.algorithms")]
    assert calls == [7] and loads == []


def test_algorithm_protocol_failure_keeps_legacy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    loads: list[Any] = []
    factory = _algorithm_factory(calls)
    _install(monkeypatch, "algorithms", _entry("external", factory, loads=loads))
    with pytest.raises(TypeError) as error:
        algorithms.load_algorithm("path_planning", "external", {"limit": 7})
    assert str(error.value) == "external does not implement path_planning.plan"
    assert calls == [7] and loads == ["external"]


@pytest.mark.parametrize("reverse", [False, True])
def test_algorithm_catalog_classifies_without_constructing_and_keeps_precedence(
    monkeypatch: pytest.MonkeyPatch, reverse: bool,
) -> None:
    calls: list[int] = []
    unqualified = _algorithm_factory(calls)
    unqualified.plan = _forbidden
    qualified = _algorithm_factory(calls)
    qualified.label = "Qualified replacement"
    loads: list[Any] = []
    entries = [_entry("nearest", unqualified, loads=loads),
               _entry("allocation.nearest", qualified, loads=loads)]
    if reverse:
        entries.reverse()
    _install(monkeypatch, "algorithm_catalog", *entries)
    catalog = _invoke("algorithm_catalog")
    allocation = next(row for row in catalog["allocation"] if row["id"] == "nearest")
    planning = next(row for row in catalog["path_planning"] if row["id"] == "nearest")
    assert allocation["label"] == qualified.label
    assert planning["label"] == unqualified.label
    assert calls == []
    assert loads == [entry.name for entry in entries]


def test_engine_catalog_is_lazy_and_local_entries_remain_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_builtins(monkeypatch, "engines")
    loads: list[Any] = []
    _install(monkeypatch, "engines", _entry("unused", loads=loads))
    catalog = engine_plugins.EngineCatalog()
    assert catalog.names() == ("records", "unused")
    assert loads == []
    catalog.entries["Local.Factory"] = _entry("Local.Factory", _forbidden, loads=loads)
    assert catalog.factory("Local.Factory") is _forbidden
    assert loads == ["Local.Factory"]


@pytest.mark.parametrize("name", ["MixedCase", "contains.dot", "-leading"])
@pytest.mark.parametrize("builtin", [False, True])
def test_assistant_strict_names_keep_legacy_errors_before_any_load(
    monkeypatch: pytest.MonkeyPatch, name: str, builtin: bool,
) -> None:
    loads: list[Any] = []
    monkeypatch.setattr(assistant_api, "BUILTINS", {name: _forbidden} if builtin else {})
    entries = [_entry("aaa-installed", loads=loads)]
    if not builtin:
        entries.append(_entry(name, loads=loads))
    _install(monkeypatch, "assistant", *entries)
    with pytest.raises(ValueError) as error:
        _invoke("assistant")
    assert str(error.value) == f"assistant entry point {name!r}: invalid plugin name"
    assert isinstance(error.value.__cause__, ValueError)
    assert loads == []


def test_assistant_factories_are_called_once_in_sorted_union_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    loads: list[Any] = []
    providers = {name: assistant_api.AssistantOperationPlugin(name)
                 for name in ("alpha", "middle", "zulu")}

    def factory(name: str) -> Any:
        def build() -> assistant_api.AssistantOperationPlugin:
            calls.append(name)
            return providers[name]
        return build

    monkeypatch.setattr(assistant_api, "BUILTINS", {
        "alpha": factory("alpha"), "middle": _forbidden,
    })
    _install(monkeypatch, "assistant",
             _entry("zulu", factory("zulu"), loads=loads),
             _entry("middle", factory("middle"), loads=loads))
    assert _invoke("assistant") == tuple(providers.values())
    assert calls == ["alpha", "middle", "zulu"]
    assert loads == ["middle", "zulu"]


@pytest.mark.parametrize("version", [True, False, "1", 1.0, None, 0, 2])
@pytest.mark.parametrize("factory", [False, True])
def test_assistant_rechecks_descriptor_versions_with_legacy_message(
    monkeypatch: pytest.MonkeyPatch, version: Any, factory: bool,
) -> None:
    _isolate_builtins(monkeypatch, "assistant")
    loads: list[Any] = []
    provider = assistant_api.AssistantOperationPlugin("lowalt")
    object.__setattr__(provider, "api_version", version)
    _install(monkeypatch, "assistant", _entry(
        "lowalt", (lambda: provider) if factory else provider, loads=loads,
    ))
    with pytest.raises(ValueError) as error:
        _invoke("assistant")
    assert str(error.value) == (
        f"assistant plugin 'lowalt': unsupported API version {version!r}; supported version is 1"
    )
    assert loads == ["lowalt"]


@pytest.mark.parametrize("factory", [False, True])
def test_assistant_wrong_descriptor_name_keeps_legacy_message(
    monkeypatch: pytest.MonkeyPatch, factory: bool,
) -> None:
    _isolate_builtins(monkeypatch, "assistant")
    loads: list[Any] = []
    provider = assistant_api.AssistantOperationPlugin("different")
    _install(monkeypatch, "assistant", _entry(
        "lowalt", (lambda: provider) if factory else provider, loads=loads,
    ))
    with pytest.raises(ValueError) as error:
        _invoke("assistant")
    assert str(error.value) == (
        "aeroagentsim.assistant_operations entry point 'lowalt': "
        "plugin name 'different' does not match"
    )
    assert loads == ["lowalt"]


@pytest.mark.parametrize("value", [None, object(), 1, True])
@pytest.mark.parametrize("factory", [False, True])
@pytest.mark.parametrize("builtin", [False, True])
def test_assistant_wrong_descriptor_types_keep_legacy_message(
    monkeypatch: pytest.MonkeyPatch, value: Any, factory: bool, builtin: bool,
) -> None:
    _isolate_builtins(monkeypatch, "assistant")
    loads: list[Any] = []
    provider = (lambda: value) if factory else value
    if builtin:
        monkeypatch.setattr(assistant_api, "BUILTINS", {"lowalt": provider})
        _install(monkeypatch, "assistant")
    else:
        _install(monkeypatch, "assistant", _entry("lowalt", provider, loads=loads))
    with pytest.raises(TypeError) as error:
        _invoke("assistant")
    origin = (
        "builtin assistant plugin" if builtin
        else "aeroagentsim.assistant_operations entry point"
    )
    assert type(error.value) is TypeError
    assert str(error.value) == (
        f"{origin} 'lowalt': expected an AssistantOperationPlugin "
        "or a zero-argument callable returning one"
    )
    assert isinstance(error.value.__cause__, TypeError)
    assert loads == ([] if builtin else ["lowalt"])


def test_assistant_factory_type_errors_keep_legacy_message_and_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_builtins(monkeypatch, "assistant")
    failure = TypeError("factory requires configuration")
    loads: list[Any] = []

    def factory() -> Any:
        raise failure

    _install(monkeypatch, "assistant", _entry("lowalt", factory, loads=loads))
    with pytest.raises(TypeError) as error:
        _invoke("assistant")
    assert str(error.value) == (
        "aeroagentsim.assistant_operations entry point 'lowalt': "
        "zero-argument assistant plugin factory failed: factory requires configuration"
    )
    assert error.value.__cause__ is failure
    assert loads == ["lowalt"]


@pytest.mark.parametrize(("field", "value", "message"), [
    ("operations", [], "assistant plugin 'lowalt': operations must be a tuple of OperationSpec"),
    ("metadata", None, "assistant plugin metadata: mapping required"),
    ("read", object(), "assistant plugin read: AssistantReadSpec required"),
])
def test_assistant_still_revalidates_its_full_descriptor_contract(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any, message: str,
) -> None:
    _isolate_builtins(monkeypatch, "assistant")
    provider = assistant_api.AssistantOperationPlugin("lowalt")
    object.__setattr__(provider, field, value)
    loads: list[Any] = []
    _install(monkeypatch, "assistant", _entry("lowalt", provider, loads=loads))
    with pytest.raises(TypeError) as error:
        _invoke("assistant")
    assert str(error.value) == message
    assert loads == ["lowalt"]


def _model_build() -> models.ModelBuild:
    registry: Any = SimpleNamespace(field=_forbidden)
    return models.ModelBuild({}, {"idle_w": 1.0, "per_m_j": 2.0}, registry, ())


def test_models_intentionally_ignore_duplicates_of_unselected_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[Any] = []
    entries = (_entry("unused", loads=loads), _entry("unused", loads=loads))
    monkeypatch.setattr(models, "entry_points", lambda **kwargs: entries)
    assert isinstance(models.ModelCatalog().build("linear", _model_build()),
                      models.LinearConsumption)
    assert loads == []


def test_models_still_reject_selected_duplicates_before_any_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[Any] = []
    entries = (_entry("linear", loads=loads), _entry("linear", loads=loads))
    monkeypatch.setattr(models, "entry_points", lambda **kwargs: entries)
    with pytest.raises(ValueError) as error:
        models.ModelCatalog().build("linear", _model_build())
    assert str(error.value) == "duplicate aeroagentsim.models entry point 'linear'"
    assert loads == []


def test_models_only_load_and_build_the_explicit_installed_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = _model_build()
    loads: list[Any] = []
    calls: list[models.ModelBuild] = []
    replacement = SimpleNamespace(dependencies=())

    def factory(context: models.ModelBuild) -> Any:
        calls.append(context)
        return replacement

    entries = (_entry("linear", factory, loads=loads), _entry("unused", loads=loads))
    monkeypatch.setattr(models, "entry_points", lambda **kwargs: entries)
    assert models.ModelCatalog().build("linear", build) is replacement
    assert calls == [build] and calls[0] is build
    assert loads == ["linear"]


def test_implicit_legacy_model_never_queries_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(models, "entry_points", _forbidden)
    build = _model_build()
    motion = models.motion_model({
        "step_ns": 1, "energy": build.energy,
        "max_speed_m_s": 1.0, "max_accel_m_s2": 1.0,
    }, build.registry, ())
    assert isinstance(motion.consumption, models.LinearConsumption)


def test_discovery_site_imports_do_not_query_metadata_or_import_providers() -> None:
    script = """
import importlib.metadata
import sys
sys.path.insert(0, sys.argv[1])
def forbidden(**kwargs):
    raise AssertionError('Import-time entry-point discovery')
importlib.metadata.entry_points = forbidden
import aeroagentsim.algorithms
import aeroagentsim.assistant_api
import aeroagentsim.services.algorithm_catalog
import aeroagentsim.platform.plugins
for prefix in (
    'aeroagentsim.algorithms.builtins', 'aeroagentsim.algorithms.routes',
    'aeroagentsim.algorithms.llm', 'aeroagentsim.agents',
    'aeroagentsim.engines', 'aeroagentsim.packs',
):
    assert not any(name == prefix or name.startswith(prefix + '.')
                   for name in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).resolve().parents[2] / "src")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
