"""R1: v1 extraction equivalence and opt-in v2 scenario admission.

The before/after oracle is the old loader's expansion prelude, frozen here and
run through the unchanged generic compiler. It never calls the new preparation
driver, compatibility adapter or result validation, and needs no golden-digest fixture.
It checks preparation extraction, not an independent historical compiler build.
All cases stop at compilation: no engine, provider or native service is started.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

import aeroagentsim.authoring.templates as template_api
import aeroagentsim.plugin_api as plugin_api
import aeroagentsim.preparation_api as preparation_api
import aeroagentsim.scenario.preparation as preparation_driver
import aeroagentsim.scenarios.builtin as builtin_templates
from aeroagentsim.authoring.templates import PreparedTemplate, TemplateRegistry
from aeroagentsim.plugin_api import API_VERSION, ScenarioPlugin
from aeroagentsim.preparation_api import ReadOnlyResources
from aeroagentsim.scenario import Scenario, ScenarioError, load_scenario
from aeroagentsim.scenario.loader import UniqueLoader, _finite, obj, text
from aeroagentsim.scenario.paths import source_path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "scenarios" / "templates"
TRAFFIC = ROOT / "scenarios" / "demos" / "traffic-accident"
V1 = "aeroagentsim.scenario/v1"
V2 = "aeroagentsim.scenario/v2"
BUILTIN_IDS = (
    "air-taxi", "city-day", "crop-spraying", "flood-rescue", "food-delivery",
    "medical-delivery", "parcel-delivery", "powerline-inspection",
    "security-patrol", "traffic-accident",
)


@pytest.fixture(autouse=True)
def _isolate_installed_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    def scenario_entries(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.scenarios"
        return ()

    def preparer_entries(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.preparers"
        return ()

    def demo_source(name: str) -> Path:
        assert name == "traffic-accident"
        return TRAFFIC

    monkeypatch.setattr(plugin_api, "entry_points", scenario_entries)
    monkeypatch.setattr(preparation_api, "entry_points", preparer_entries)
    # Source-tree equivalence must not silently use stale installed package data.
    monkeypatch.setattr(
        builtin_templates, "_template_directory", lambda name: TEMPLATES / name
    )
    monkeypatch.setattr(template_api, "demo_source", demo_source)


@dataclass
class _LegacyPreparedDocument:
    # The historical loader had no PreparedScenario admission. Its frozen
    # prelude must not share the new result class's validation restrictions.
    document: dict[str, Any]


def _frozen_v1_preparation(
    authored_document: dict[str, Any],
    final_seed: int | None = None,
    read_only_resources: ReadOnlyResources | None = None,
    *,
    base: Path | None = None,
) -> _LegacyPreparedDocument:
    """Old loader prelude, not a call through lowalt.preparation.prepare.

    Keep the historical trigger and temporary snapshot-path rewrite independent
    of the new adapter's captured-resource route. Generic behaviour compilation
    remains in load_scenario for both branches. The final_seed argument only
    accommodates the new call signature; historical cases use the authored seed.
    """
    document = copy.deepcopy(authored_document)
    _finite(document, "scenario")
    directory = Path.cwd() if base is None else base
    if final_seed is not None:
        document["run"]["seed"] = final_seed
    task_extensions = any(
        isinstance(engine, dict) and engine.get("plugin") == "lowalt_tasks"
        and any(
            key in (engine.get("config") or {})
            for key in ("cooperation", "vehicle_task_kinds")
        )
        for engine in (document.get("engines") or {}).values()
    )
    if "algorithms" in document or "generators" in document or task_extensions:
        from aeroagentsim.packs.lowalt.template import expand

        snapshot = (document.get("registry") or {}).get("snapshot")
        if task_extensions and snapshot is not None:
            document["registry"]["snapshot"] = str(
                source_path(text(snapshot, "registry.snapshot"), directory)
            )
        try:
            # Deliberately use the old default path, not registry_snapshot=....
            expand(document)
        finally:
            if task_extensions and snapshot is not None:
                document["registry"]["snapshot"] = snapshot

    # These v1 checks moved out of the generic loader together with expansion.
    allowed = {
        "format", "id", "registry", "entities", "bindings", "engines",
        "presentation", "run", "outputs", "origin", "clock_mappings",
        "ingress_streams", "behaviours", "provenance", "algorithms",
        "generators", "event_narratives", "city",
    }
    if extra := set(document) - allowed:
        raise ScenarioError(f"scenario: unknown keys {sorted(extra)}")
    if "city" in document and document["city"] != "huangpu":
        raise ScenarioError("city: expected huangpu")
    if document.get("format") != V1:
        raise ScenarioError(f"format: expected {V1}")
    if "algorithms" in document:
        algorithms = obj(document["algorithms"], "algorithms")
        slots = {"allocation", "path_planning", "conflict_resolution", "charging"}
        if set(algorithms) - slots:
            raise ScenarioError("algorithms: unknown algorithm slot")
        for slot, algorithm in algorithms.items():
            text(algorithm, f"algorithms.{slot}")
    for index, entity in enumerate(document.get("entities", [])):
        if not isinstance(entity, dict):
            continue
        facts = entity.get("facts", {})
        if (
            "vehicle_class" in entity and isinstance(facts, dict)
            and "lowalt.vehicle_class" in facts
            and entity["vehicle_class"] != facts["lowalt.vehicle_class"]
        ):
            raise ScenarioError(
                f"entities[{index}].vehicle_class: must agree with lowalt.vehicle_class"
            )
    return _LegacyPreparedDocument(document)


def _json_tree(value: Any) -> Any:
    """Encode frozen kernel dataclasses without asdict's mappingproxy deepcopy."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, (datetime, date)):
        return {"$type": type(value).__name__, "iso": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {
                field.name: _json_tree(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("compatibility digest requires string mapping keys")
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_tree(child) for child in value]
    raise TypeError(f"unsupported compatibility value: {type(value).__name__}")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_tree(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _scenario_digests(scenario: Scenario) -> dict[str, str]:
    # Exact paths are intentional: both branches use the same source and base.
    # Only the additive preparation evidence is outside this legacy projection.
    sections = {
        "document": scenario.document,
        "source": scenario.source,
        "base": str(scenario.base),
        "compiled": scenario.compiled.to_data(),
        "effective_registry": scenario.registry.to_data(),
        "registry_digest": scenario.registry.digest,
        "manifest": scenario.manifest,
        "initial": scenario.initial,
        "engines": scenario.engines,
        "run": {
            "id": scenario.run_id, "seed": scenario.seed,
            "until_ns": scenario.until_ns, "pacing": scenario.pacing,
            "advance_ns": scenario.advance_ns, "provenance": scenario.provenance,
        },
        "clock_mappings": scenario.clock_mappings,
        "ingress": scenario.ingress,
        "ingress_streams": scenario.ingress_streams,
    }
    return {name: _digest(value) for name, value in sections.items()}


def _template_digests(prepared: PreparedTemplate) -> dict[str, str]:
    return {
        "authored_document": _digest(prepared.document),
        "base": _digest(str(prepared.base)),
        "knobs": _digest(prepared.knobs),
        "metadata": _digest(prepared.metadata),
        "run_metadata": _digest(prepared.run_metadata()),
    }


def _asset_digests(*directories: Path) -> dict[str, str]:
    paths = {
        path.resolve()
        for directory in directories
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
    assert paths, "compatibility corpus must contain real source assets"
    return {str(path): _bytes_digest(path.read_bytes()) for path in sorted(paths)}


def _scenario_path(identifier: str) -> Path:
    # Traffic's template directory contains metadata, not its scenario file.
    directory = TRAFFIC if identifier == "traffic-accident" else TEMPLATES / identifier
    return directory / "scenario.yaml"


def _read_document(path: Path) -> dict[str, Any]:
    document = yaml.load(path.read_bytes(), Loader=UniqueLoader)
    assert isinstance(document, dict)
    return document


def _assert_matches_legacy(
    document: dict[str, Any], base: Path, monkeypatch: pytest.MonkeyPatch,
) -> Scenario:
    original = _digest(document)
    current_input, legacy_input = copy.deepcopy(document), copy.deepcopy(document)
    current = load_scenario(current_input, base=base)
    assert _digest(current_input) == original
    with monkeypatch.context() as scoped:
        scoped.setattr(preparation_driver, "prepare_scenario", _frozen_v1_preparation)
        legacy = load_scenario(legacy_input, base=base)
    assert _digest(legacy_input) == original
    assert _digest(document) == original
    assert _scenario_digests(current) == _scenario_digests(legacy)
    assert current.document["format"] == V1
    assert "preparation" not in current.document
    assert current.preparation is not None
    identity = current.preparation.contribution_selections[0]
    assert (identity["name"], identity["api_version"]) == ("lowalt-legacy", 1)
    assert identity["final_seed"] == current.seed
    assert current.preparation.document is not current.document
    return current


def _minimal_document(format_id: str = V1) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": format_id,
        "id": "scenario-versions",
        "registry": {"snapshot": str(TRAFFIC / "registry.snapshot.json")},
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {},
        "run": {
            "pacing": "fast", "seed": 3,
            "until_ns": 1_000_000_000, "advance_ns": 1_000_000_000,
        },
        "outputs": {"durability": "flush"},
    }
    if format_id == V2:
        document["preparation"] = {"plugin": "explicit", "api_version": 1, "config": {}}
    return document


def _forbid_discovery() -> tuple[Any, ...]:
    pytest.fail("invalid selection or historical v1 input activated preparer discovery")


def test_compatibility_digests_preserve_number_types_and_list_order() -> None:
    assert _digest({"value": 1}) != _digest({"value": 1.0})
    assert _digest({"value": True}) != _digest({"value": 1})
    assert _digest(["first", "second"]) != _digest(["second", "first"])
    assert _digest({"a": 1, "b": 2}) == _digest({"b": 2, "a": 1})
    with pytest.raises(TypeError, match="unsupported compatibility value"):
        _digest(object())


@pytest.mark.parametrize("identifier", BUILTIN_IDS)
@pytest.mark.parametrize("source_kind", ("file", "mapping"))
def test_shipped_v1_sources_match_frozen_loader_prelude(
    identifier: str, source_kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _scenario_path(identifier)
    before = _asset_digests(path.parent, TEMPLATES / identifier)
    if source_kind == "mapping":
        _assert_matches_legacy(_read_document(path), path.parent, monkeypatch)
    else:
        current = load_scenario(path)
        with monkeypatch.context() as scoped:
            scoped.setattr(preparation_driver, "prepare_scenario", _frozen_v1_preparation)
            legacy = load_scenario(path)
        assert _scenario_digests(current) == _scenario_digests(legacy)
        assert current.source == path.read_text()
        assert current.base == path.parent
        assert current.document["format"] == V1
        assert "preparation" not in current.document
    assert _asset_digests(path.parent, TEMPLATES / identifier) == before


@pytest.mark.parametrize("identifier", BUILTIN_IDS)
@pytest.mark.parametrize("explicit_root", (False, True))
def test_shipped_v1_templates_match_frozen_loader_prelude(
    identifier: str, explicit_root: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _asset_digests(TEMPLATES / identifier, _scenario_path(identifier).parent)
    registry = TemplateRegistry(TEMPLATES if explicit_root else None)
    assert tuple(row["id"] for row in registry.list()) == BUILTIN_IDS
    assert registry.directory_of(identifier) == TEMPLATES / identifier
    assert registry.plugin_of(identifier) == (None if explicit_root else identifier)
    catalogue = _digest(registry.list())
    hooks = dict(registry.hooks)

    current_template = registry.apply(identifier)
    current_input = _digest(current_template.document)
    current = current_template.prepare()
    assert _digest(current_template.document) == current_input
    with monkeypatch.context() as scoped:
        scoped.setattr(preparation_driver, "prepare_scenario", _frozen_v1_preparation)
        legacy_template = registry.apply(identifier)
        legacy_input = _digest(legacy_template.document)
        legacy = legacy_template.prepare()
        assert _digest(legacy_template.document) == legacy_input

    assert _template_digests(current_template) == _template_digests(legacy_template)
    assert _scenario_digests(current) == _scenario_digests(legacy)
    assert current_template.metadata["format"] == "aeroagentsim.template/v1"
    assert current_template.document["format"] == V1
    assert "preparation" not in current_template.document
    assert current_template.base == _scenario_path(identifier).parent
    assert _digest(registry.list()) == catalogue
    assert registry.hooks == hooks
    assert _asset_digests(TEMPLATES / identifier, _scenario_path(identifier).parent) == before


@pytest.mark.parametrize(
    "identifier,knobs",
    [
        pytest.param("parcel-delivery", {"fleet_size": 2, "wind_speed": 4}, id="fleet-number"),
        pytest.param("security-patrol", {"run_mode": "interactive"}, id="patrol-interactive"),
        pytest.param("city-day", {"run_mode": "interactive"}, id="city-interactive"),
        pytest.param(
            "traffic-accident",
            {"accident_time_s": 5, "physics_hz": 2, "wind_east_mps": 3, "wind_north_mps": -2},
            id="traffic-physics-weather",
        ),
        pytest.param(
            "flood-rescue", {"participation": "all", "fleet_variation": "seeded", "radio": "off"},
            id="rescue-seeded",
        ),
        pytest.param("flood-rescue", {"fleet_variation": "authored"}, id="rescue-authored"),
    ],
)
def test_knob_applied_v1_documents_match_frozen_loader_prelude(
    identifier: str, knobs: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _asset_digests(TEMPLATES / identifier, _scenario_path(identifier).parent)
    registry = TemplateRegistry()
    overrides = _digest(knobs)
    current = registry.apply(identifier, knobs)
    with monkeypatch.context() as scoped:
        scoped.setattr(preparation_driver, "prepare_scenario", _frozen_v1_preparation)
        legacy = registry.apply(identifier, knobs)
    assert _template_digests(current) == _template_digests(legacy)
    _assert_matches_legacy(current.document, current.base, monkeypatch)
    assert _digest(knobs) == overrides
    assert _asset_digests(TEMPLATES / identifier, _scenario_path(identifier).parent) == before


def test_task_extension_trigger_keeps_old_relative_snapshot_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _scenario_path("parcel-delivery")
    before = _asset_digests(path.parent)
    # Isolate the third historical trigger: no algorithms or fleet declaration.
    document = _frozen_v1_preparation(_read_document(path), base=path.parent).document
    document.pop("algorithms")
    document.pop("generators")
    vehicle = next(row["id"] for row in document["entities"] if row["type"] == "lowalt:UAV")
    task_config = next(
        row["config"] for row in document["engines"].values()
        if row["plugin"] == "lowalt_tasks"
    )
    task_config["vehicle_task_kinds"] = {vehicle: ["parcel"]}
    assert document["registry"]["snapshot"] == "registry.snapshot.json"
    current = _assert_matches_legacy(document, path.parent, monkeypatch)
    assert current.document["registry"]["snapshot"] == "registry.snapshot.json"
    assert _asset_digests(path.parent) == before


@pytest.mark.parametrize("source_kind", ("file", "mapping"))
def test_v1_yaml_engine_date_matches_independent_legacy_admission(
    source_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _minimal_document()
    document["engines"] = yaml.load(
        "calendar:\n  plugin: calendar\n  config:\n    start_date: 2026-07-13\n",
        Loader=UniqueLoader,
    )
    expected_date = date(2026, 7, 13)
    assert type(document["engines"]["calendar"]["config"]["start_date"]) is date
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    source: Path | dict[str, Any]
    if source_kind == "file":
        source = tmp_path / "calendar.yaml"
        source.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    else:
        source = document
    current = load_scenario(source, base=tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(preparation_driver, "prepare_scenario", _frozen_v1_preparation)
        legacy = load_scenario(source, base=tmp_path)
    assert _scenario_digests(current) == _scenario_digests(legacy)
    assert current.document == document
    assert current.engines["calendar"]["config"]["start_date"] == expected_date
    assert type(current.engines["calendar"]["config"]["start_date"]) is date
    assert current.preparation is not None
    assert type(current.preparation.document["engines"]["calendar"]["config"]["start_date"]) is date
    identity = current.preparation.contribution_selections[0]
    assert identity["authored_document_digest"] == preparation_api.document_digest(
        document, legacy_yaml=True,
    )
    assert load_scenario(source, base=tmp_path).preparation == current.preparation


def test_v2_yaml_engine_date_is_rejected_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _minimal_document(V2)
    document["engines"] = yaml.load(
        "calendar:\n  plugin: calendar\n  config:\n    start_date: 2026-07-13\n",
        Loader=UniqueLoader,
    )
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match="expected finite JSON data"):
        load_scenario(document)


def test_v1_never_discovers_preparers_or_adds_selection_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    document = _minimal_document()
    before = _digest(document)
    scenario = load_scenario(document)
    assert scenario.document == document
    assert _digest(document) == before
    assert "preparation" not in scenario.document
    assert scenario.preparation is not None
    assert scenario.preparation.contribution_selections[0]["name"] == "lowalt-legacy"


@pytest.mark.parametrize("format_id", ("aeroagentsim.scenario/v0", "aeroagentsim.scenario/v3", "v2"))
def test_unsupported_scenario_versions_fail_before_discovery(
    format_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match="format: expected"):
        load_scenario(_minimal_document(format_id))


def test_v2_requires_explicit_preparation_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _minimal_document(V2)
    del document["preparation"]
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match="preparation"):
        load_scenario(document)


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param(None, id="null"),
        pytest.param("explicit", id="string"),
        pytest.param([], id="list"),
        pytest.param({}, id="empty"),
        pytest.param({"plugin": "explicit", "api_version": 1}, id="missing-config"),
        pytest.param({"plugin": "explicit", "config": {}}, id="missing-version"),
        pytest.param({"api_version": 1, "config": {}}, id="missing-plugin"),
        pytest.param({"plugin": "", "api_version": 1, "config": {}}, id="empty-plugin"),
        pytest.param({"plugin": True, "api_version": 1, "config": {}}, id="boolean-plugin"),
        pytest.param({"plugin": "explicit", "api_version": 1, "config": None}, id="null-config"),
        pytest.param({"plugin": "explicit", "api_version": 1, "config": []}, id="list-config"),
        pytest.param(
            {"plugin": "explicit", "api_version": 1, "config": {}, "resources": []},
            id="list-resources",
        ),
        pytest.param(
            {"plugin": "explicit", "api_version": 1, "config": {}, "typo": True},
            id="unknown-selection-key",
        ),
    ],
)
def test_malformed_v2_selection_fails_before_discovery(
    selection: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _minimal_document(V2)
    document["preparation"] = copy.deepcopy(selection)
    before = _digest(document)
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match="preparation"):
        load_scenario(document)
    assert _digest(document) == before


@pytest.mark.parametrize("version", (0, 2, True, "1", 1.0, None))
def test_v2_preparation_version_is_type_exact(
    version: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _minimal_document(V2)
    document["preparation"]["api_version"] = version
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match=r"preparation\.api_version"):
        load_scenario(document)


def test_v1_rejects_the_new_preparation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _minimal_document()
    document["preparation"] = {"plugin": "explicit", "api_version": 1, "config": {}}
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match=r"unknown keys.*preparation"):
        load_scenario(document)


def test_v2_rejects_unknown_top_level_keys_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _minimal_document(V2)
    document["preparation_typo"] = {}
    monkeypatch.setattr(preparation_driver, "discover_preparers", _forbid_discovery)
    with pytest.raises(ScenarioError, match=r"unknown keys.*preparation_typo"):
        load_scenario(document)


def test_v2_unknown_preparer_never_infers_legacy_expansion() -> None:
    document = _minimal_document(V2)
    document["preparation"]["plugin"] = "missing-preparer"
    document["generators"] = {}
    before = _digest(document)
    with pytest.raises(ScenarioError, match=r"preparation\.plugin.*unknown preparer.*missing-preparer"):
        load_scenario(document)
    assert _digest(document) == before


@pytest.mark.parametrize("name", ("explicit", "lowalt-legacy"))
def test_builtin_preparers_reject_unknown_configuration(name: str) -> None:
    document = _minimal_document(V2)
    document["preparation"].update(plugin=name, config={"unknown_option": True})
    before = _digest(document)
    with pytest.raises(ScenarioError, match=r"preparation\.config"):
        load_scenario(document)
    assert _digest(document) == before


@pytest.mark.parametrize("key", ("algorithms", "generators"))
def test_v2_explicit_does_not_silently_expand_legacy_shorthand(key: str) -> None:
    document = _minimal_document(V2)
    document[key] = {}
    before = _digest(document)
    with pytest.raises(ScenarioError, match="explicit.*legacy shorthand"):
        load_scenario(document)
    assert _digest(document) == before


def test_template_v1_can_reference_a_v2_explicit_scenario(tmp_path: Path) -> None:
    identifier = "v2-template"
    source = tmp_path / identifier
    source.mkdir()
    document = _minimal_document(V2)
    document["id"] = identifier
    document["registry"]["snapshot"] = "registry.snapshot.json"
    document["preparation"]["resources"] = {"note": "note.txt"}
    snapshot = (TRAFFIC / "registry.snapshot.json").read_bytes()
    note = b"authored read-only resource\n"
    (source / "registry.snapshot.json").write_bytes(snapshot)
    (source / "note.txt").write_bytes(note)
    (source / "authored-v2.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    metadata = {
        "format": "aeroagentsim.template/v1", "id": identifier,
        "title": "Explicit v2", "summary": "A v1 template selecting a v2 scenario.",
        "category": "mixed", "difficulty": "beginner", "duration_s": 1,
        "scenario": "authored-v2.yaml",
        "observe": ["Inspect the format.", "Inspect the final seed."],
        "try": ["Change the seed.", "Inspect preparation evidence."],
        "kpis": [],
        "knobs": [{
            "id": "seed", "label": "Seed", "type": "integer",
            "default": 3, "min": 0, "pointer": "/run/seed",
        }],
    }
    (source / "template.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
    )
    before = _asset_digests(source)
    registry = TemplateRegistry(tmp_path, plugins=())
    assert registry.get(identifier)["format"] == "aeroagentsim.template/v1"
    assert registry.plugin_of(identifier) is None
    prepared = registry.apply(identifier, {"seed": 7})
    authored = _digest(prepared.document)
    scenario = prepared.prepare(final_seed=0)
    assert prepared.base == source.resolve()
    assert prepared.document["format"] == V2
    assert prepared.document["run"]["seed"] == 7
    assert prepared.knobs == {"seed": 7}
    assert _digest(prepared.document) == authored
    assert scenario.document["format"] == V2
    assert scenario.document["preparation"] == document["preparation"]
    assert scenario.seed == scenario.document["run"]["seed"] == 0
    assert scenario.preparation is not None
    assert scenario.preparation.resource_manifest == {
        "note": _bytes_digest(note), "registry.snapshot": _bytes_digest(snapshot),
    }
    identity = scenario.preparation.contribution_selections[0]
    assert (identity["name"], identity["api_version"], identity["final_seed"]) == (
        "explicit", 1, 0,
    )
    assert _asset_digests(source) == before


def test_scenario_v2_does_not_bump_the_scenario_plugin_api() -> None:
    assert type(API_VERSION) is int and API_VERSION == 1
    plugin = ScenarioPlugin("v2-template-pack", api_version=1)
    assert plugin.api_version == 1
    with pytest.raises(ValueError, match="supported version"):
        ScenarioPlugin("v2-template-pack", api_version=2)
