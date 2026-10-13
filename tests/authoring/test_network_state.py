from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.portable import FORMAT, unpack
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore

SOURCE = Path(__file__).parent / "fixtures/wujiaochang.osm.xml"


def test_network_state_is_one_string_in_create_get_summary_list_and_storage(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Network state")
    stored = json.loads((store.directory(draft["id"]) / "draft.json").read_text())
    for record in (draft, stored, store.get(draft["id"]), store.summary(draft["id"]), store.list()[0]):
        assert record["network_state"] == "none"
        assert "network_status" not in record
    assert yaml.safe_load(store.export(draft["id"]))["geography"]["network_state"] == "none"


@pytest.mark.parametrize("previous,expected", [({"status": "missing"}, "none"), ({"status": "stale", "reason": "region_changed"}, "stale")])
def test_be2_legacy_records_use_the_same_field_without_mutating_on_read(tmp_path: Path, previous: dict, expected: str) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("BE-2 record")
    draft["network_state"] = previous
    path = store.directory(draft["id"]) / "draft.json"
    path.write_text(json.dumps(draft))
    before = path.read_bytes()
    assert store.get(draft["id"])["network_state"] == expected
    assert store.summary(draft["id"])["network_state"] == expected
    assert path.read_bytes() == before
    updated = store._write(store.get(draft["id"]))
    assert updated["network_state"] == expected
    assert json.loads(path.read_text())["network_state"] == expected


def test_network_state_uses_real_files_and_source_and_survives_region_invalidation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Real network")
    params = {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 0, "level_height_m": 3}
    draft = store.region(draft["id"], params, SOURCE)
    assert draft["network_state"] == "none"
    directory = store.directory(draft["id"])

    def generate(osm: Path, output: Path) -> dict:
        assert osm.read_bytes() == (directory / "region.osm.xml").read_bytes()
        output.write_bytes(SOURCE.with_name("wujiaochang.net.xml").read_bytes())
        return {"edge_count": 1, "lane_count": 2}

    monkeypatch.setattr("aeroagentsim.authoring.scene.generate_sumo", generate)
    app = FastAPI()
    app.include_router(create_router(store, extracts={"fixture": SOURCE}))
    with TestClient(app) as client:
        url = f"/v1/studio/workspaces/{draft['id']}"
        assert client.post(url + "/network").status_code == 200
        assert client.get(url).json()["network_state"] == "generated"
    generated = store.get(draft["id"])
    assert generated["network_state"] == store.summary(draft["id"])["network_state"] == "generated"
    crop = directory / "region.osm.xml"
    crop_bytes = crop.read_bytes()
    crop.write_bytes(crop_bytes + b"\n")
    assert store.get(draft["id"])["network_state"] == "stale"
    crop.write_bytes(crop_bytes)
    assert store.get(draft["id"])["network_state"] == "generated"
    network = directory / "network.net.xml"
    network_bytes = network.read_bytes()
    network.unlink()
    assert store.get(draft["id"])["network_state"] == store.list()[0]["network_state"] == "stale"
    exported = yaml.safe_load(store.export(draft["id"]))
    assert exported["geography"]["network_state"] == "stale" and "network" not in exported["geography"]
    network.write_bytes(network_bytes)
    crop.unlink()
    assert store.get(draft["id"])["network_state"] == "stale"
    crop.write_bytes(crop_bytes)
    invalidated = store.region(draft["id"], {**params, "alt": 5}, SOURCE)
    assert invalidated["network_state"] == "stale"
    assert "network" not in invalidated and not network.exists()
    assert WorkspaceStore(store.root, None).get(draft["id"])["network_state"] == "stale"


@pytest.mark.parametrize("state,expected", [("none", "none"), ("stale", "stale"), ("generated", "generated"), ({"status": "missing"}, "none"), ({"status": "generated"}, "generated")])
def test_portable_network_state_normalizes_be2_and_preserves_canonical_values(state, expected: str) -> None:
    _, _, metadata = unpack({"format": FORMAT, "scenario": {}, "files": {}, "geography": {"network_state": state}})
    assert metadata == {"network_state": expected}


@pytest.mark.parametrize("state", [None, False, "unknown", "missing", {}, {"status": "unknown"}])
def test_portable_network_state_rejects_undeclared_values(state) -> None:
    with pytest.raises(ValueError, match="network_state"):
        unpack({"format": FORMAT, "scenario": {}, "files": {}, "geography": {"network_state": state}})


def test_forms_remove_fleet_catalogue_preview_and_apply_are_atomic(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    templates = TemplateRegistry()
    draft = store.create("Remove fleet form")
    operations = [
        {"id": "place", "op": "add_place", "params": {"id": "depot", "role": "depot", "at": "center"}},
        {"id": "destination", "op": "add_place", "params": {"id": "customer", "role": "customer", "at": [100, 0, 0]}},
        {"id": "fleet", "op": "add_fleet", "params": {"id": "logistics", "role": "parcel_drone", "count": 2, "start": "depot"}},
        {"id": "other", "op": "add_fleet", "params": {"id": "keeper", "role": "parcel_drone", "count": 1, "start": "depot"}},
        {"id": "orders", "op": "add_orders", "params": {"id": "orders", "kind": "parcel", "count": 1, "from": ["depot"], "to": ["customer"], "release": {"mode": "scheduled", "times_s": [0]}}},
    ]
    draft = store.operations(draft["id"], {"operations": operations}, templates=templates)
    original = copy.deepcopy(store.get(draft["id"]))
    directory = store.directory(draft["id"])
    before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    removal = {"id": "remove", "op": "remove_fleet", "params": {"fleet": "logistics"}}
    app = FastAPI()
    app.state.templates = templates
    app.include_router(create_router(store))
    with TestClient(app) as client:
        catalogue = client.get("/v1/studio/operations").json()
        spec = next(row for row in catalogue if row["op"] == "remove_fleet")
        assert spec["schema"]["required"] == ["fleet"]
        url = f"/v1/studio/workspaces/{draft['id']}/operations"
        preview = client.post(url, json={"operations": [removal], "revision": draft["revision"], "dry_run": True})
        assert preview.status_code == 200, preview.json()
        assert {row["id"] for row in preview.json()["diff"]["removed"] if row["kind"] == "entity"} == {"logistics-1", "logistics-2"}
        assert any(row.get("path") == "/engines/tasks/config/vehicle_task_kinds/logistics-1" for row in preview.json()["diff"]["removed"])
        assert store.get(draft["id"]) == original
        assert {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
        applied = client.post(url, json={"operations": [removal], "revision": draft["revision"]})
        assert applied.status_code == 200, applied.json()
    remaining = {entity["id"]: entity for entity in applied.json()["scenario"]["entities"]}
    assert remaining == {entity["id"]: entity for entity in original["scenario"]["entities"] if entity["id"] not in {"logistics-1", "logistics-2"}}
    assert applied.json()["network_state"] == "none"
