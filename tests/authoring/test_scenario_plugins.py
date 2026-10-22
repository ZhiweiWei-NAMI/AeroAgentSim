"""Public scenario plugins, deterministic discovery and generic template sources."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import aeroagentsim.plugin_api as plugin_api
from aeroagentsim.authoring.templates import TemplateRegistry, demo_source
from aeroagentsim.plugin_api import (
    API_VERSION,
    ApplyHook,
    KnobProvider,
    ScenarioPlugin,
    TemplateSource,
    discover_scenario_plugins,
)

BUILTIN_IDS = {
    "air-taxi", "city-day", "crop-spraying", "flood-rescue", "food-delivery",
    "medical-delivery", "parcel-delivery", "powerline-inspection",
    "security-patrol", "traffic-accident",
}
BUILTIN_HOOKS = {
    "lowalt.operator_mode", "traffic.accident_time", "traffic.decision_mode",
    "traffic.physics_hz", "traffic.wind_east", "traffic.wind_north",
    "rescue.participation", "rescue.observation", "rescue.radio_port",
    "rescue.radio", "rescue.deadline_s", "rescue.fleet_variation",
}
HOOK_PLUGIN_IDS = {"lowalt", "rescue", "traffic"}
HOOK_OWNERS = {
    "lowalt.operator_mode": "lowalt",
    "traffic.accident_time": "traffic",
    "traffic.decision_mode": "traffic",
    "traffic.physics_hz": "traffic",
    "traffic.wind_east": "traffic",
    "traffic.wind_north": "traffic",
    "rescue.participation": "rescue",
    "rescue.observation": "rescue",
    "rescue.radio_port": "rescue",
    "rescue.radio": "rescue",
    "rescue.deadline_s": "rescue",
    "rescue.fleet_variation": "rescue",
}


def _entry_point(name: str, loaded: Any) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=lambda: loaded)


def _entry_points(monkeypatch: pytest.MonkeyPatch, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.scenarios"
        return entries

    monkeypatch.setattr(plugin_api, "entry_points", query)


@pytest.fixture(autouse=True)
def _isolate_installed_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _entry_points(monkeypatch)


def _scenario(identifier: str) -> dict[str, Any]:
    return {
        "format": "aeroagentsim.scenario/v1",
        "id": identifier,
        "registry": {
            "snapshot": str(demo_source("traffic-accident") / "registry.snapshot.json")
        },
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {"test": {"plugin": "test", "config": {}}},
        "run": {
            "pacing": "fast", "seed": 1,
            "until_ns": 1_000_000_000, "advance_ns": 1_000_000_000,
        },
        "outputs": {"durability": "flush"},
    }


def _template(
    directory: Path,
    identifier: str = "custom",
    *,
    knobs: list[dict[str, Any]] | None = None,
    scenario: str = "scenario.yaml",
    operator: bool = False,
) -> Path:
    directory.mkdir(parents=True)
    document = _scenario(identifier)
    if operator:
        document["ingress_streams"] = [{
            "id": "operator", "engine_ids": ["test"], "mapping_id": "canonical",
            "initial_watermark_ns": document["run"]["until_ns"],
            "lateness": "reject", "timeout_s": 10.0,
        }]
    metadata = {
        "format": "aeroagentsim.template/v1",
        "id": identifier,
        "title": "External template",
        "summary": "A minimal independently packaged scenario.",
        "category": "mixed", "difficulty": "beginner", "duration_s": 1,
        "scenario": scenario,
        "observe": ["Inspect the seed.", "Inspect the run horizon."],
        "try": ["Change a knob.", "Compare the prepared document."],
        "kpis": [], "knobs": [] if knobs is None else knobs,
    }
    (directory / "scenario.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    (directory / "template.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    return directory


def _set_seed(document: dict[str, Any], base: Path, value: Any) -> None:
    document["run"]["seed"] = value


def test_public_descriptors_have_versioned_defaults_and_are_frozen(tmp_path: Path) -> None:
    assert API_VERSION == 1 and type(API_VERSION) is int
    source = TemplateSource(tmp_path)
    plugin = ScenarioPlugin("0_example-plugin")
    assert (source.directory, source.scenario, source.extra_knobs) == (tmp_path, None, None)
    assert (plugin.templates, plugin.apply_hooks, plugin.api_version) == ((), {}, 1)
    with pytest.raises(FrozenInstanceError):
        setattr(source, "directory", tmp_path / "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(plugin, "name", "changed")


def test_builtin_discovery_preserves_all_ids_hooks_and_separate_provenance() -> None:
    plugins = discover_scenario_plugins()
    assert isinstance(plugins, tuple)
    assert [plugin.name for plugin in plugins] == sorted(BUILTIN_IDS | HOOK_PLUGIN_IDS)
    by_name = {plugin.name: plugin for plugin in plugins}
    for identifier in BUILTIN_IDS:
        assert len(by_name[identifier].templates) == 1
        assert by_name[identifier].apply_hooks == {}
    for name in HOOK_PLUGIN_IDS:
        assert by_name[name].templates == ()
    assert {
        hook: plugin.name for plugin in plugins for hook in plugin.apply_hooks
    } == HOOK_OWNERS
    registry = TemplateRegistry()
    metadata = registry.list()
    assert [item["id"] for item in metadata] == sorted(BUILTIN_IDS)
    assert set(registry.hooks) == BUILTIN_HOOKS
    for item in metadata:
        assert registry.plugin_of(item["id"]) == item["id"]
        assert registry.get(item["id"]) == item
        assert not {"plugin", "plugin_name", "provenance"} & item.keys()

    root = Path(__file__).resolve().parents[2] / "scenarios" / "templates"
    root_registry = TemplateRegistry(root, plugins=plugins)
    assert {item["id"] for item in root_registry.list()} == BUILTIN_IDS
    assert all(root_registry.plugin_of(identifier) is None for identifier in BUILTIN_IDS)


def test_builtins_are_exactly_the_hook_and_template_plugins() -> None:
    from aeroagentsim.scenarios import BUILTINS

    assert set(BUILTINS) == BUILTIN_IDS | HOOK_PLUGIN_IDS
    assert len(BUILTINS) == 13
    assert all(callable(factory) for factory in BUILTINS.values())


def test_explicit_empty_plugins_disable_builtin_templates_and_hooks() -> None:
    registry = TemplateRegistry(plugins=iter(()))
    assert registry.list() == []
    assert registry.hooks == {}


def test_external_template_reuses_builtin_hook_only_when_explicitly_supplied(
    tmp_path: Path,
) -> None:
    source = _template(tmp_path / "bundle", "external", operator=True, knobs=[{
        "id": "run_mode", "label": "Run mode", "type": "choice",
        "default": "scheduled", "options": ["scheduled", "interactive"],
        "apply": "lowalt.operator_mode",
    }])
    before = (source / "scenario.yaml").read_bytes()
    external = ScenarioPlugin("external-pack", templates=(TemplateSource(source),))
    with pytest.raises(ValueError, match=r"unregistered apply hook.*lowalt\.operator_mode"):
        TemplateRegistry(plugins=(external,)).apply("external")

    lowalt = next(plugin for plugin in discover_scenario_plugins() if plugin.name == "lowalt")
    registry = TemplateRegistry(plugins=iter((external, lowalt)))
    assert [item["id"] for item in registry.list()] == ["external"]
    assert registry.plugin_of("external") == "external-pack"
    assert registry.directory_of("external") == source.resolve()
    with pytest.raises(KeyError):
        registry.plugin_of("missing")
    assert set(registry.hooks) == {"lowalt.operator_mode"}
    scheduled = registry.apply("external")
    interactive = registry.apply("external", {"run_mode": "interactive"})
    assert scheduled.document["ingress_streams"][0]["initial_watermark_ns"] == 1_000_000_000
    assert scheduled.document["ingress_streams"][0]["timeout_s"] == 10.0
    assert interactive.document["ingress_streams"][0]["initial_watermark_ns"] == 0
    assert "timeout_s" not in interactive.document["ingress_streams"][0]
    assert interactive.base == source
    assert interactive.knobs == {"run_mode": "interactive"}
    assert (source / "scenario.yaml").read_bytes() == before


@pytest.mark.parametrize("name", ["", "MixedCase", "-leading", "contains.dot", None])
def test_invalid_plugin_names_are_rejected(name: Any) -> None:
    with pytest.raises(ValueError, match="name"):
        ScenarioPlugin(name)


@pytest.mark.parametrize("name", ["", "demo", "demo.", "other.seed", "demo2.seed"])
def test_plugin_hooks_require_own_namespace_and_nonempty_suffix(name: str) -> None:
    hook: ApplyHook = _set_seed
    with pytest.raises(ValueError, match="namespace"):
        ScenarioPlugin("demo", apply_hooks={name: hook})


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0])
def test_unsupported_or_noninteger_api_versions_are_rejected(version: Any) -> None:
    with pytest.raises(ValueError, match="supported version"):
        ScenarioPlugin("demo", api_version=version)


def test_duplicate_template_ids_report_both_plugin_owners(tmp_path: Path) -> None:
    first = _template(tmp_path / "alpha", "shared")
    second = _template(tmp_path / "beta", "shared")
    plugins = (
        ScenarioPlugin("first-pack", templates=(TemplateSource(first),)),
        ScenarioPlugin("second-pack", templates=(TemplateSource(second),)),
    )
    with pytest.raises(ValueError, match="(?i)duplicate") as error:
        TemplateRegistry(plugins=plugins)
    assert all(name in str(error.value) for name in ("shared", "first-pack", "second-pack"))


def test_repeated_plugin_cannot_silently_overwrite_an_apply_hook() -> None:
    plugin = ScenarioPlugin("repeat", apply_hooks={"repeat.seed": _set_seed})
    with pytest.raises(ValueError, match="(?i)duplicate|already registered") as error:
        TemplateRegistry(plugins=(plugin, plugin))
    assert "repeat.seed" in str(error.value)


def test_caller_hook_mapping_mutation_cannot_inject_another_namespace() -> None:
    hooks: dict[str, ApplyHook] = {"owner.seed": _set_seed}
    plugin = ScenarioPlugin("owner", apply_hooks=hooks)
    hooks.clear()
    hooks["foreign.seed"] = _set_seed
    registry = TemplateRegistry(plugins=(plugin,))
    assert set(plugin.apply_hooks) == {"owner.seed"}
    assert registry.hooks == {"owner.seed": _set_seed}


@pytest.mark.parametrize("explicit_root", [False, True])
def test_generic_extra_knobs_work_with_plugin_sources_and_matched_roots(
    tmp_path: Path, explicit_root: bool,
) -> None:
    root = tmp_path / "templates"
    source = _template(root / "custom")
    seen = []
    extra = {
        "id": "seed", "label": "Seed", "type": "integer", "default": 3,
        "pointer": "/run/seed",
    }

    def provide(document: dict[str, Any]) -> list[dict[str, Any]]:
        seen.append(document["id"])
        return [extra]

    provider: KnobProvider = provide
    # The root scan must match a source by its resolved directory, not its spelling.
    aliased = source / ".." / source.name
    plugin = ScenarioPlugin("augment", templates=(TemplateSource(aliased, extra_knobs=provider),))
    registry = TemplateRegistry(root if explicit_root else None, plugins=(plugin,))
    assert registry.get("custom")["knobs"] == [extra]
    assert seen and set(seen) == {"custom"}
    prepared = registry.apply("custom", {"seed": 9})
    assert prepared.document["run"]["seed"] == 9
    assert prepared.knobs == {"seed": 9}
    assert registry.plugin_of("custom") == (None if explicit_root else "augment")


def test_explicit_root_excludes_plugin_templates_but_keeps_hooks_and_ad_hoc_registration(
    tmp_path: Path,
) -> None:
    external = _template(tmp_path / "installed" / "external", "external")
    local = _template(tmp_path / "root" / "local", "local", knobs=[{
        "id": "seed", "label": "Seed", "type": "integer", "default": 1,
        "apply": "manual.seed",
    }])
    plugin = ScenarioPlugin(
        "outside", templates=(TemplateSource(external),),
        apply_hooks={"outside.seed": _set_seed},
    )
    registry = TemplateRegistry(local.parent, plugins=(plugin,))
    assert [item["id"] for item in registry.list()] == ["local"]
    assert registry.plugin_of("local") is None
    assert registry.hooks["outside.seed"] is _set_seed
    with pytest.raises(ValueError, match="unregistered apply hook"):
        registry.apply("local")
    registry.register_apply_hook("manual.seed", _set_seed)
    assert registry.apply("local", {"seed": 7}).document["run"]["seed"] == 7


def test_explicit_root_symlink_keeps_manifest_id_and_resolves_asset_directory(
    tmp_path: Path,
) -> None:
    target = _template(tmp_path / "installed" / "assets", "local")
    root = tmp_path / "root"
    root.mkdir()
    (root / "local").symlink_to(target, target_is_directory=True)

    def extra_knobs(document: dict[str, Any]) -> list[dict[str, Any]]:
        assert document["id"] == "local"
        return [{
            "id": "seed", "label": "Seed", "type": "integer", "default": 5,
            "pointer": "/run/seed",
        }]

    plugin = ScenarioPlugin(
        "linked", templates=(TemplateSource(target, extra_knobs=extra_knobs),),
    )
    registry = TemplateRegistry(root, plugins=(plugin,))
    assert [item["id"] for item in registry.list()] == ["local"]
    assert registry.plugin_of("local") is None
    assert registry.directory_of("local") == target.resolve()
    prepared = registry.apply("local")
    assert prepared.base == target.resolve()
    assert prepared.document["run"]["seed"] == 5


def test_discovery_loads_instances_and_factories_and_installed_plugin_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = ScenarioPlugin("lowalt", apply_hooks={"lowalt.custom": _set_seed})
    instance = ScenarioPlugin("z-external")
    produced = ScenarioPlugin("a-external")
    calls = []

    def factory() -> ScenarioPlugin:
        calls.append(True)
        return produced

    _entry_points(
        monkeypatch, _entry_point("z-external", instance),
        _entry_point("lowalt", replacement), _entry_point("a-external", factory),
    )
    plugins = discover_scenario_plugins()
    assert isinstance(plugins, tuple)
    assert [plugin.name for plugin in plugins] == sorted(
        BUILTIN_IDS | HOOK_PLUGIN_IDS | {"a-external", "z-external"}
    )
    by_name = {plugin.name: plugin for plugin in plugins}
    assert by_name["lowalt"] is replacement
    assert by_name["z-external"] is instance
    assert by_name["a-external"] is produced
    assert calls == [True]


def test_duplicate_installed_names_are_not_treated_as_builtin_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ScenarioPlugin("lowalt")
    _entry_points(monkeypatch, _entry_point("lowalt", plugin), _entry_point("lowalt", plugin))
    with pytest.raises(ValueError, match="(?i)duplicate.*lowalt"):
        discover_scenario_plugins()


def test_installed_entry_point_replaces_builtin_flood_rescue_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = _template(tmp_path / "installed" / "flood-rescue", "flood-rescue")
    replacement = ScenarioPlugin("flood-rescue", templates=(TemplateSource(source),))
    _entry_points(monkeypatch, _entry_point("flood-rescue", replacement))
    plugins = discover_scenario_plugins()
    assert len(plugins) == len(BUILTIN_IDS | HOOK_PLUGIN_IDS)
    by_name = {plugin.name: plugin for plugin in plugins}
    assert by_name["flood-rescue"] is replacement
    registry = TemplateRegistry(plugins=plugins)
    assert registry.plugin_of("flood-rescue") == "flood-rescue"
    assert registry.directory_of("flood-rescue") == source.resolve()
    assert registry.apply("flood-rescue").document["id"] == "flood-rescue"


@pytest.mark.parametrize(
    "loaded,error,match",
    [
        (None, TypeError, "ScenarioPlugin"),
        (lambda: None, TypeError, "ScenarioPlugin"),
        (lambda required: None, TypeError, "external.*zero-argument"),
        (ScenarioPlugin("different"), ValueError, "name.*match"),
    ],
)
def test_invalid_entry_point_results_fail_closed(
    monkeypatch: pytest.MonkeyPatch, loaded: Any, error: type[Exception], match: str,
) -> None:
    _entry_points(monkeypatch, _entry_point("external", loaded))
    with pytest.raises(error, match=match):
        discover_scenario_plugins()


def test_absolute_scenario_override_uses_asset_base_but_cannot_escape_explicit_root(
    tmp_path: Path,
) -> None:
    source = _template(tmp_path / "workspace" / "templates" / "custom")
    with pytest.raises(ValueError, match="absolute"):
        TemplateSource(source, scenario=Path("scenario.yaml"))
    override = tmp_path / "external.yaml"
    document = _scenario("custom")
    document["run"]["seed"] = 17
    override.write_text(yaml.safe_dump(document), encoding="utf-8")
    plugin = ScenarioPlugin("override", templates=(TemplateSource(source, scenario=override),))
    prepared = TemplateRegistry(plugins=(plugin,)).apply("custom")
    assert prepared.document["run"]["seed"] == 17
    assert prepared.base == override.parent
    with pytest.raises(ValueError, match="escape|scope|within"):
        TemplateRegistry(source.parent, plugins=(plugin,))

    bounded = source.parent.parent / "assets" / "scenario.yaml"
    bounded.parent.mkdir()
    document["run"]["seed"] = 23
    bounded.write_text(yaml.safe_dump(document), encoding="utf-8")
    matched = ScenarioPlugin(
        "override", templates=(TemplateSource(source, scenario=bounded),),
    )
    registry = TemplateRegistry(source.parent, plugins=(matched,))
    prepared = registry.apply("custom")
    assert prepared.document["run"]["seed"] == 23
    assert prepared.base == bounded.parent
    assert registry.plugin_of("custom") is None


@pytest.mark.parametrize("escape", ["absolute", "parent", "symlink"])
def test_manifest_scenario_paths_cannot_escape_plugin_scope(tmp_path: Path, escape: str) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text(yaml.safe_dump(_scenario("unsafe")), encoding="utf-8")
    scenario = {
        "absolute": str(outside), "parent": "../../outside.yaml", "symlink": "linked.yaml",
    }[escape]
    source = _template(tmp_path / "plugins" / "unsafe", "unsafe", scenario=scenario)
    if escape == "symlink":
        (source / "linked.yaml").symlink_to(outside)
    plugin = ScenarioPlugin("unsafe", templates=(TemplateSource(source),))
    with pytest.raises(ValueError, match="relative|escape|scope|within"):
        TemplateRegistry(plugins=(plugin,)).apply("unsafe")
