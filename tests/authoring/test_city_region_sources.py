"""Draft, version, run and review identify the same selected geometry."""

import copy
import json
from pathlib import Path

import pytest

from aeroagentsim.authoring.replay import attach, scene_header
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import header
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.template_execution import snapshot_template_scene

SOURCE = Path(__file__).parent / "fixtures/wujiaochang.osm.xml"


def selected(store: WorkspaceStore) -> dict:
    draft = store.create("Selected region")
    return store.region(draft["id"], {
        "extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 20.0, "level_height_m": 3.5,
    }, SOURCE)


def test_blank_huangpu_save_and_refresh_resolve_city_descriptor(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Blank city")
    document = copy.deepcopy(draft["scenario"])
    document["city"] = "huangpu"
    saved = store.save(draft["id"], {"scenario": document, "revision": draft["revision"]})
    assert saved["demo_console"]["scene"]["city"] == {
        "kind": "traffic-city", "url": "/v1/studio/demo-assets/scene.json",
    }
    assert saved["scene_source"]["kind"] == "fixed-city"
    reopened = WorkspaceStore(store.root, None).summary(draft["id"])
    assert reopened["demo_console"] == saved["demo_console"]
    assert reopened["scene_source"] == saved["scene_source"]
    document.pop("city")
    cleared = store.save(draft["id"], {"scenario": document, "revision": saved["revision"]})
    assert "demo_console" not in cleared and "scene_source" not in cleared


def test_unavailable_city_cannot_be_saved_and_legacy_draft_remains_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Unavailable city")
    document = copy.deepcopy(draft["scenario"])
    document["city"] = "huangpu"

    def unavailable(name):
        raise FileNotFoundError("Missing city scene.json")

    monkeypatch.setattr("aeroagentsim.authoring.demo.city_file", unavailable)
    with pytest.raises(FileNotFoundError, match="Missing city"):
        store.save(draft["id"], {"scenario": document, "revision": draft["revision"]})
    assert store.get(draft["id"]) == draft
    draft["scenario"] = document
    legacy = store._write(draft)
    summary = store.summary(draft["id"])
    assert summary["demo_console"]["city_available"] is False
    assert "Missing city" in summary["demo_console"]["city_error"]
    assert summary["demo_console"]["scene"]["city"]["url"].endswith("/scene.json")
    assert summary["revision"] == legacy["revision"]


def test_legacy_blank_city_is_resolved_on_read_without_changing_revision(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Legacy city")
    draft["scenario"]["city"] = "huangpu"
    draft = store._write(draft)
    path = store.directory(draft["id"]) / "draft.json"
    before = path.read_bytes()
    summary = store.summary(draft["id"])
    assert summary["demo_console"]["scene"]["city"]["kind"] == "traffic-city"
    assert summary["revision"] == draft["revision"] and path.read_bytes() == before


def test_region_city_conflict_is_located_and_cannot_freeze(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    draft["scenario"]["city"] = "huangpu"
    draft = store.save(draft["id"], {"scenario": draft["scenario"], "revision": draft["revision"]})
    assert "demo_console" not in draft
    validation = store.validate(draft["id"], revision=draft["revision"])
    assert validation["valid"] is False
    assert validation["issues"][0]["section"] == "space"
    assert validation["issues"][0]["path"] == "/city"
    assert "compiled region" in validation["issues"][0]["message"]
    assert "区域" in validation["issues"][0]["message"]
    with pytest.raises(ValueError, match="failed validation"):
        store.save_version(draft["id"], {"revision": draft["revision"]})
    assert not store.versions(draft["id"])


def test_frozen_region_source_matches_run_header_and_survives_draft_changes(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    assert store.validate(draft["id"])["valid"]
    frozen = store.save_version(draft["id"], {"revision": draft["revision"]})
    assert frozen["region"] == draft["region"]
    assert frozen["scene_source"] == draft["scene_source"]
    run = tmp_path / ("run-" + "a" * 32)
    RunStorage(run).prepare(load_scenario(frozen["scenario"], base=store.version_directory(draft["id"], frozen["version"])))
    attach(run, frozen, store.version_directory(draft["id"], frozen["version"]))
    draft["scene"]["origin"]["lon"] += 0.01
    draft["scene"]["attribution"] = "Later draft"
    store._write(draft)
    projected = header(run)
    served = scene_header(run, "http://localhost")
    assert projected["scene_source"] == served["scene_source"] == frozen["scene_source"]
    assert served["scene"]["origin"] == frozen["scene"]["origin"]
    assert served["scene"]["bounds"] == frozen["scene"]["bounds"]
    assert served["scene"]["city"]["url"].startswith("http://localhost/v1/studio/runs/")
    assert json.loads((run / "studio-buildings.geojson").read_bytes()) == frozen["scene"]["geojson"]


def test_legacy_conflicting_version_never_projects_huangpu_over_frozen_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    draft["scenario"]["city"] = "huangpu"
    run = tmp_path / ("run-" + "b" * 32)
    RunStorage(run).prepare(load_scenario(draft["scenario"], base=store.directory(draft["id"])))
    snapshot_template_scene("traffic-accident", {
        "city": "huangpu", "engines": {"capture": {"config": {"renderer": {"mode": "traffic_browser"}}}},
    }, run)
    assert (run / "console-scene.json").exists()
    attach(run, draft, store.directory(draft["id"]))
    assert not (run / "console-scene.json").exists()

    def forbidden(*args, **kwargs):
        raise AssertionError("Frozen region must not consult the fixed-city descriptor")

    monkeypatch.setattr("aeroagentsim.authoring.demo.city_presentation", forbidden)
    projected = header(run)
    assert "city" not in projected  # The REST adapter's Huangpu rewrite is not entered.
    assert projected["scene"]["city"]["kind"] == "geojson"
    assert projected["scene_source"]["conflicting_city"] == "huangpu"
    snapshot_template_scene("traffic-accident", {"city": "huangpu"}, run)
    assert not (run / "console-scene.json").exists()


def test_template_without_city_does_not_attach_city(tmp_path: Path) -> None:
    snapshot_template_scene("traffic-accident", {}, tmp_path)
    assert not (tmp_path / "console-scene.json").exists()
    assert not (tmp_path / "scene-source.json").exists()


def test_old_validated_conflict_cannot_be_saved_as_a_new_version(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    draft["scenario"]["city"] = "huangpu"
    draft = store._write(draft)
    draft["validation"] = {"valid": True, "revision": draft["revision"], "errors": []}
    store._write(draft)
    with pytest.raises(ValueError, match="city: fixed city conflicts"):
        store.save_version(draft["id"], {"revision": draft["revision"]})
    assert store.versions(draft["id"]) == []


def test_region_origin_cannot_disagree_with_the_scenario_origin(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    draft["scenario"]["origin"]["lon"] += 0.01
    saved = store.save(draft["id"], {"scenario": draft["scenario"], "revision": draft["revision"]})
    validation = store.validate(saved["id"], revision=saved["revision"])
    assert validation["valid"] is False
    assert validation["issues"][0]["path"] == "/origin"
    assert validation["issues"][0]["section"] == "space"


def test_derivation_uses_frozen_geometry_instead_of_mutable_draft(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    assert store.validate(draft["id"])["valid"]
    frozen = store.save_version(draft["id"], {"revision": draft["revision"]})
    draft["scene"]["attribution"] = "Later scene"
    store._write(draft)
    run = tmp_path / "run-source"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"studio_workspace": draft["id"], "workspace_version": frozen["version"]}))
    derived = store.derive(run, "Variant")
    assert derived["scene"] == frozen["scene"]
    assert derived["region"] == frozen["region"]
    assert derived["scene_source"] == frozen["scene_source"]


def test_region_modeling_parameters_are_part_of_the_version_digest(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = selected(store)
    assert store.validate(draft["id"])["valid"]
    first = store.save_version(draft["id"], {"revision": draft["revision"]})
    changed = store.region(draft["id"], {**draft["region"], "level_height_m": 4.0}, SOURCE)
    assert changed["scenario"] == draft["scenario"]
    assert store.validate(draft["id"])["valid"]
    second = store.save_version(draft["id"], {"revision": changed["revision"]})
    assert first["region"]["level_height_m"] == 3.5 and second["region"]["level_height_m"] == 4.0
    assert first["digest"] != second["digest"]
