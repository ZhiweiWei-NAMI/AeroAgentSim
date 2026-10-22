"""Shared admission, family composition and import-free installation inventory.

Engine factories remain unversioned callables: descriptor name/version rejection
applies to scenarios and metrics, not to a fabricated engine descriptor ABI.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import aeroagentsim.extensions.discovery as discovery_module
import aeroagentsim.metrics_api as metrics_api
import aeroagentsim.plugin_api as plugin_api
import aeroagentsim.scenarios as scenario_builtins
import aeroagentsim.services.plugins as inventory_service
from aeroagentsim.extensions import Contribution, DuplicateEntryPointError, discover
from aeroagentsim.metrics_api import MetricAccumulator, MetricDescriptor, MetricPlugin
from aeroagentsim.platform import plugins as engine_plugins
from aeroagentsim.plugin_api import ScenarioPlugin, TemplateSource

FAMILIES = ("scenarios", "metrics", "engines")
VERSIONED_FAMILIES = ("scenarios", "metrics")
MODULES = {
    "scenarios": plugin_api,
    "metrics": metrics_api,
    "engines": engine_plugins,
}


class SampleReducer:
    input_schemas = ("external.samples.",)

    def __init__(self, identity: str) -> None:
        self.descriptors = (MetricDescriptor(
            id=identity, label="Recorded samples", label_zh="已记录样本",
            unit="count", direction=None, aggregation="Count recorded samples.",
            missing_data_policy="None until an observed sample.",
        ),)

    def initial(self) -> None:
        return None

    def consume(self, state: Any, message: Any) -> None:
        raise AssertionError("Admission must not consume evidence")

    def snapshot(self, state: Any) -> dict[str, None]:
        return {self.descriptors[0].id: None}


def _forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Metadata/discovery must not invoke this provider")


def _provider(family: str, name: str) -> Any:
    if family == "scenarios":
        return ScenarioPlugin(name)
    if family == "metrics":
        return MetricPlugin(name, (SampleReducer(f"{name}.samples"),))

    def build(context: Any) -> Any:
        raise AssertionError("Engine discovery must not construct an engine")

    return build


def _entry(
    name: Any, value: Any, *, distribution: str | None = "acme-extensions",
    version: str | None = "1.0", entry_point: str = "external.provider:plugin",
    loads: list[str] | None = None,
) -> Any:
    def load() -> Any:
        if loads is not None:
            loads.append(name)
        return value

    return SimpleNamespace(
        name=name, value=entry_point,
        dist=SimpleNamespace(name=distribution, version=version), load=load,
    )


def _install(monkeypatch: pytest.MonkeyPatch, family: str, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == f"aeroagentsim.{family}"
        return entries

    monkeypatch.setattr(MODULES[family], "entry_points", query)


def _builtins(
    monkeypatch: pytest.MonkeyPatch, family: str, values: dict[str, Any],
) -> list[str]:
    imports: list[str] = []
    if family == "engines":
        def import_module(module: str) -> Any:
            imports.append(module)
            return SimpleNamespace(build=values[module])

        monkeypatch.setattr(engine_plugins, "import_module", import_module)
        monkeypatch.setattr(engine_plugins, "BUILTINS", {name: name for name in values})
    else:
        module = scenario_builtins if family == "scenarios" else metrics_api
        monkeypatch.setattr(module, "BUILTINS", values)
    return imports


def _discover(family: str) -> dict[str, Any]:
    if family == "scenarios":
        return {plugin.name: plugin for plugin in plugin_api.discover_scenario_plugins()}
    if family == "metrics":
        return {plugin.name: plugin for plugin in metrics_api.discover_metric_plugins()}
    catalog = engine_plugins.EngineCatalog()
    return {name: catalog.factory(name) for name in catalog.names()}


@pytest.mark.parametrize("family", FAMILIES)
def test_sorted_union_and_whole_installed_replacement(
    monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    alpha = _provider(family, "alpha")
    middle = _provider(family, "middle")
    zulu = _provider(family, "zulu")
    imports = _builtins(monkeypatch, family, {"middle": _forbidden, "alpha": alpha})
    loads: list[str] = []
    _install(monkeypatch, family, _entry("zulu", zulu, loads=loads),
             _entry("middle", middle, loads=loads))
    selected = _discover(family)
    assert tuple(selected) == ("alpha", "middle", "zulu")
    assert selected == {"alpha": alpha, "middle": middle, "zulu": zulu}
    assert selected["middle"] is middle
    assert loads == ["middle", "zulu"]
    assert imports == (["alpha"] if family == "engines" else [])


@pytest.mark.parametrize("family", VERSIONED_FAMILIES)
def test_zero_argument_factory_is_called_once(
    monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    _builtins(monkeypatch, family, {})
    provider = _provider(family, "external")
    calls: list[str] = []

    def factory() -> Any:
        calls.append("factory")
        return provider

    _install(monkeypatch, family, _entry("external", factory))
    assert _discover(family) == {"external": provider}
    assert calls == ["factory"]


@pytest.mark.parametrize("family", FAMILIES)
def test_duplicate_installed_names_fail_before_any_loading(
    monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    imports = _builtins(monkeypatch, family, {"aaa-builtin": _forbidden})
    loads: list[str] = []
    _install(
        monkeypatch, family, _entry("aaa-installed", _forbidden, loads=loads),
        _entry("external", _forbidden, distribution="first-dist", loads=loads),
        _entry("external", _forbidden, distribution="second-dist", loads=loads),
    )
    with pytest.raises(DuplicateEntryPointError) as error:
        _discover(family)
    assert isinstance(error.value, ValueError)
    assert all(text in str(error.value) for text in (
        f"aeroagentsim.{family}", "external", "first-dist", "second-dist",
    ))
    assert tuple(item.distribution for item in error.value.conflict.entries) == (
        "first-dist", "second-dist",
    )
    assert loads == [] and imports == []


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("value", [None, object(), 1, True])
def test_wrong_type_fails_without_builtin_fallback(
    monkeypatch: pytest.MonkeyPatch, family: str, value: Any,
) -> None:
    imports = _builtins(monkeypatch, family, {"external": _forbidden})
    _install(monkeypatch, family, _entry("external", value))
    expected = {
        "scenarios": "ScenarioPlugin", "metrics": "MetricPlugin",
        "engines": "engine factory must be callable",
    }[family]
    with pytest.raises(TypeError, match=expected):
        _discover(family)
    assert imports == []


@pytest.mark.parametrize("family", VERSIONED_FAMILIES)
@pytest.mark.parametrize("value", [lambda: object(), lambda required: required])
def test_invalid_zero_argument_factory_retains_type_error(
    monkeypatch: pytest.MonkeyPatch, family: str, value: Any,
) -> None:
    _builtins(monkeypatch, family, {})
    _install(monkeypatch, family, _entry("external", value))
    with pytest.raises(TypeError, match="zero-argument"):
        _discover(family)


@pytest.mark.parametrize("family", VERSIONED_FAMILIES)
@pytest.mark.parametrize("factory", [False, True])
def test_wrong_descriptor_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch, family: str, factory: bool,
) -> None:
    _builtins(monkeypatch, family, {})
    wrong = _provider(family, "different")
    _install(monkeypatch, family, _entry("external", (lambda: wrong) if factory else wrong))
    with pytest.raises(ValueError, match="plugin name 'different' does not match"):
        _discover(family)


@pytest.mark.parametrize("family", VERSIONED_FAMILIES)
@pytest.mark.parametrize("name", ["MixedCase", "contains.dot", "-leading"])
def test_versioned_entry_names_are_strict_before_loading(
    monkeypatch: pytest.MonkeyPatch, family: str, name: str,
) -> None:
    _builtins(monkeypatch, family, {})
    loads: list[str] = []
    _install(monkeypatch, family, _entry(name, _forbidden, loads=loads))
    with pytest.raises(ValueError, match="name must match"):
        _discover(family)
    assert loads == []


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("name", ["", None, True])
def test_non_names_are_rejected_before_loading(
    monkeypatch: pytest.MonkeyPatch, family: str, name: Any,
) -> None:
    _builtins(monkeypatch, family, {})
    loads: list[str] = []
    _install(monkeypatch, family, _entry(name, _forbidden, loads=loads))
    with pytest.raises(ValueError, match="name must"):
        _discover(family)
    assert loads == []


@pytest.mark.parametrize("family", VERSIONED_FAMILIES)
@pytest.mark.parametrize("version", [True, False, "1", 1.0, None, 0, 2])
@pytest.mark.parametrize("factory", [False, True])
def test_discovery_rechecks_bool_noninteger_and_unknown_versions(
    monkeypatch: pytest.MonkeyPatch, family: str, version: Any, factory: bool,
) -> None:
    _builtins(monkeypatch, family, {})
    malformed = _provider(family, "external")
    # Deliberately bypass construction to audit admission of a tampered instance.
    object.__setattr__(malformed, "api_version", version)
    _install(monkeypatch, family, _entry(
        "external", (lambda: malformed) if factory else malformed,
    ))
    with pytest.raises(ValueError, match="unsupported API version.*supported version is 1"):
        _discover(family)


@pytest.mark.parametrize("version", [True, False, "1", 1.0, None, 0, 2])
def test_engine_callable_contract_is_not_retroactively_versioned(
    monkeypatch: pytest.MonkeyPatch, version: Any,
) -> None:
    _builtins(monkeypatch, "engines", {})
    factory = _provider("engines", "external")
    factory.api_version = version
    factory.name = "a descriptive factory name, not a descriptor namespace"
    _install(monkeypatch, "engines", _entry("external", factory))
    assert _discover("engines") == {"external": factory}


def _template(directory: Path) -> TemplateSource:
    directory.mkdir()
    metadata = {
        "format": "aeroagentsim.template/v1", "id": "shared",
        "title": "Shared template", "summary": "Synthetic admission fixture.",
        "category": "mixed", "difficulty": "beginner", "duration_s": 1,
        "scenario": "scenario.yaml", "observe": ["One fact.", "Another fact."],
        "try": ["One change.", "Another change."], "knobs": [], "kpis": [],
    }
    (directory / "template.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    (directory / "scenario.yaml").write_text("{}\n", encoding="utf-8")
    return TemplateSource(directory)


@pytest.mark.parametrize("family", FAMILIES)
def test_contribution_id_collision_is_not_an_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, family: str,
) -> None:
    _builtins(monkeypatch, family, {})
    if family == "scenarios":
        from aeroagentsim.authoring.templates import TemplateRegistry

        first = ScenarioPlugin("alpha", templates=(_template(tmp_path / "first"),))
        second = ScenarioPlugin("beta", templates=(_template(tmp_path / "second"),))
        _install(monkeypatch, family, _entry("alpha", first), _entry("beta", second))
        with pytest.raises(ValueError, match="Duplicate template id 'shared'") as error:
            TemplateRegistry()
        assert "alpha" in str(error.value) and "beta" in str(error.value)
    elif family == "metrics":
        first = MetricPlugin("alpha", (SampleReducer("shared"),))
        second = MetricPlugin("beta", (SampleReducer("shared"),))
        _install(monkeypatch, family, _entry("alpha", first), _entry("beta", second))
        with pytest.raises(ValueError, match="Duplicate metric id: 'shared'"):
            MetricAccumulator()
    else:
        # Engines contribute the catalog key itself, not metric/template IDs.
        # Identical Python targets are still two declarations of that key.
        factory = _provider(family, "shared")
        _install(monkeypatch, family,
                 _entry("shared", factory, distribution="first-dist"),
                 _entry("shared", factory, distribution="second-dist"))
        with pytest.raises(DuplicateEntryPointError, match="shared"):
            engine_plugins.EngineCatalog()


def test_discovery_and_contribution_evidence_are_immutable_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScenarioPlugin("external")
    builtins = {"external": _forbidden}
    entry = _entry("external", provider)
    monkeypatch.setattr(discovery_module, "entry_points", lambda **kwargs: (entry,))
    result = discover(
        "aeroagentsim.scenarios", builtins, expected_type=ScenarioPlugin,
        api_version=1, name_of=lambda plugin: plugin.name, factory_label="scenario plugin",
    )
    assert result.providers == (provider,)
    assert result.contributions == (
        Contribution("aeroagentsim.scenarios", "external", "builtin", None, None, None),
        Contribution("aeroagentsim.scenarios", "external", "installed",
                     "acme-extensions", "1.0", "external.provider:plugin"),
    )
    entry.dist.name, entry.dist.version, entry.value = "changed", "2.0", "changed:plugin"
    builtins.clear()
    assert result.names == ("external",)
    assert result.contributions[1].distribution == "acme-extensions"
    assert result.contributions[1].version == "1.0"
    assert result.contributions[1].entry_point == "external.provider:plugin"
    with pytest.raises(FrozenInstanceError):
        setattr(result, "names", ())
    with pytest.raises(FrozenInstanceError):
        setattr(result.contributions[1], "version", "changed")


def test_metadata_mode_keeps_all_conflicts_without_loading() -> None:
    loads: list[str] = []
    entries = (
        _entry("external", _forbidden, distribution="same-dist", version="1", loads=loads),
        _entry("external", _forbidden, distribution="same-dist", version="2", loads=loads),
        _entry("external", _forbidden, distribution=None, version=None, loads=loads),
    )
    result = discover("aeroagentsim.scenarios", {"external": _forbidden},
                      entries=entries, expected_type=ScenarioPlugin, load=False)
    assert result.providers == () and result.names == ("external",)
    assert len(result.contributions) == 4
    assert tuple(record.version for record in result.conflicts[0].entries) == ("1", "2", None)
    with pytest.raises(FrozenInstanceError):
        setattr(result.conflicts[0], "entries", ())
    with pytest.raises(DuplicateEntryPointError):
        result.require_unique()
    with pytest.raises(DuplicateEntryPointError):
        result.resolve("external")
    assert loads == []


def test_metadata_mode_without_an_admission_contract_cannot_load() -> None:
    loads: list[str] = []
    result = discover(
        "aeroagentsim.scenarios", {}, load=False,
        entries=(_entry("external", _forbidden, loads=loads),),
    )
    with pytest.raises(TypeError, match="expected_type is required"):
        result.resolve("external")
    assert loads == []


@pytest.mark.parametrize("family", FAMILIES)
def test_family_descriptor_snapshots(
    monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    _builtins(monkeypatch, family, {})
    if family == "scenarios":
        hooks = {"external.sample": _forbidden}
        provider = ScenarioPlugin("external", apply_hooks=hooks)
        _install(monkeypatch, family, _entry("external", provider))
        selected = _discover(family)["external"]
        hooks.clear()
        assert tuple(selected.apply_hooks) == ("external.sample",)
        with pytest.raises(TypeError):
            selected.apply_hooks["external.other"] = _forbidden
    elif family == "metrics":
        provider = _provider(family, "external")
        _install(monkeypatch, family, _entry("external", provider))
        descriptor = _discover(family)["external"].reducers[0].descriptors[0]
        with pytest.raises(FrozenInstanceError):
            setattr(descriptor, "id", "changed")
    else:
        factory = _provider(family, "external")
        schema = {"type": "object", "properties": {"sample": {"type": "number"}}}
        factory.config_schema = schema
        _install(monkeypatch, family, _entry("external", factory))
        snapshot = engine_plugins.EngineCatalog().config_schema("external")
        schema["properties"]["sample"]["type"] = "string"
        assert snapshot == {"type": "object", "properties": {"sample": {"type": "number"}}}


def test_local_engine_catalog_registration_stays_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _builtins(monkeypatch, "engines", {})
    _install(monkeypatch, "engines")
    catalog = engine_plugins.EngineCatalog()
    factory = _provider("engines", "local")
    catalog.entries["local"] = _entry("local", factory)
    assert catalog.names() == ("local",)
    assert catalog.factory("local") is factory


def test_inventory_does_not_import_provider_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    families = (*FAMILIES, "algorithms", "models")
    module_names = tuple(f"r1_unimported_{family}_provider" for family in families)
    entries = {}
    for family, module in zip(families, module_names):
        monkeypatch.delitem(sys.modules, module, raising=False)
        (tmp_path / f"{module}.py").write_text(
            "raise AssertionError('Inventory imported a provider')\n", encoding="utf-8",
        )
        entries[f"aeroagentsim.{family}"] = (EntryPoint(
            name="external", value=f"{module}:plugin", group=f"aeroagentsim.{family}",
        ),)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(inventory_service, "entry_points", lambda *, group: entries[group])
    monkeypatch.setattr(plugin_api, "discover_scenario_plugins", _forbidden)
    monkeypatch.setattr(metrics_api, "discover_metric_plugins", _forbidden)
    monkeypatch.setattr(engine_plugins, "import_module", _forbidden)
    monkeypatch.setattr(metrics_api, "import_module", _forbidden)
    builtin_modules = set(engine_plugins.BUILTINS.values()) | {
        "aeroagentsim.scenarios.builtin", "aeroagentsim.packs.lowalt.scenario_hooks",
        "aeroagentsim.packs.lowalt.rescue_hooks",
        "aeroagentsim.packs.traffic_accident.scenario_hooks",
        "aeroagentsim.packs.inspection_metrics", "aeroagentsim.packs.logistics_metrics",
        "aeroagentsim.packs.lowalt.metrics", "aeroagentsim.packs.task_metrics",
        "aeroagentsim.packs.traffic_accident.metrics", "aeroagentsim.agents.provider",
    }
    before = set(sys.modules)
    inventory = inventory_service.plugin_inventory()
    assert inventory["metadata_only"] is True
    for family in families:
        assert any(row["name"] == "external" for row in inventory[family])
    assert not set(module_names) & set(sys.modules)
    assert not (set(sys.modules) - before) & builtin_modules
    assert all(row["templates"] == [] and row["apply_hooks"] == []
               for row in inventory["scenarios"])


def test_inventory_cold_import_keeps_runtime_and_providers_unimported() -> None:
    # A fresh interpreter prevents earlier suites' imports from hiding a leak.
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from aeroagentsim.services import plugins
plugins.entry_points = lambda *, group: ()
assert plugins.plugin_inventory()["metadata_only"] is True
for prefix in (
    "aeroagentsim.platform", "aeroagentsim.models", "aeroagentsim.engines",
    "aeroagentsim.agents", "aeroagentsim.packs", "aeroagentsim.scenarios.builtin",
    "aeroagentsim.algorithms.builtins", "aeroagentsim.algorithms.routes",
    "aeroagentsim.algorithms.llm",
):
    assert not any(name == prefix or name.startswith(prefix + ".")
                   for name in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).resolve().parents[2] / "src")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("family", (*FAMILIES, "algorithms", "models"))
def test_inventory_preserves_every_duplicate_distribution(
    monkeypatch: pytest.MonkeyPatch, family: str,
) -> None:
    entries = (
        _entry("external", _forbidden, distribution="first-dist"),
        _entry("external", _forbidden, distribution="second-dist"),
        _entry("external", _forbidden, distribution=None),
    )
    monkeypatch.setattr(inventory_service, "entry_points", lambda *, group: (
        entries if group == f"aeroagentsim.{family}" else ()
    ))
    rows = inventory_service.plugin_inventory()[family]
    matches = [row for row in rows if row["name"] == "external"]
    assert len(matches) == 1
    assert matches[0]["status"] == "duplicate"
    assert matches[0]["conflict"] == ["first-dist", "second-dist", None]
    assert [row["name"] for row in rows] == sorted(row["name"] for row in rows)


@pytest.mark.parametrize(("family", "name"), [
    ("scenarios", "lowalt"), ("metrics", "tasks"), ("engines", "records"),
    ("algorithms", "allocation.nearest"), ("models", "wind"),
])
def test_inventory_marks_installed_over_builtin_without_loading(
    monkeypatch: pytest.MonkeyPatch, family: str, name: str,
) -> None:
    entry = _entry(name, _forbidden)
    monkeypatch.setattr(inventory_service, "entry_points", lambda *, group: (
        (entry,) if group == f"aeroagentsim.{family}" else ()
    ))
    matches = [row for row in inventory_service.plugin_inventory()[family] if row["name"] == name]
    assert len(matches) == 1
    assert matches[0]["origin"] == "entry-point"
    assert matches[0]["distribution"] == "acme-extensions"
    assert matches[0]["status"] == "ok"
