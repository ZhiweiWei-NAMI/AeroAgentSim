"""Exported Studio inputs reopen independently, including their real geography."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.portable import FORMAT
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore

SOURCE = Path(__file__).parent / "fixtures/wujiaochang.osm.xml"


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def api(store: WorkspaceStore) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router(store, extracts={"fixture": SOURCE}))
    return app


@pytest.mark.parametrize("template", ["parcel-delivery", "food-delivery", "traffic-accident"])
def test_template_yaml_imports_into_a_fresh_workspace_without_source_files(tmp_path: Path, template: str) -> None:
    store = WorkspaceStore(tmp_path / "source", None)
    source = store.import_template(template, {}, registry=TemplateRegistry())
    exported = store.export(source["id"])
    bundle = yaml.safe_load(exported)
    assert bundle["format"] == FORMAT
    assert "registry.snapshot.json" in bundle["files"]
    assert not any(name.startswith("versions/") or name == "draft.json" for name in bundle["files"])
    target_store = WorkspaceStore(tmp_path / "independent", None)
    target = target_store.create("Import template")
    url = f"/v1/studio/workspaces/{target['id']}"
    with TestClient(api(target_store)) as client:
        response = client.post(url + "/import", json={"yaml": exported, "revision": target["revision"]})
        assert response.status_code == 200, response.json()
        imported = response.json()
        assert imported == client.get(url).json()
        assert imported["template"] == source["template"]
        assert imported["scenario"]["entities"] == source["scenario"]["entities"]
        assert client.post(url + "/validate", json={}).json()["valid"] is True
    assert (target_store.directory(target["id"]) / "registry.snapshot.json").read_bytes() == (
        store.directory(source["id"]) / "registry.snapshot.json"
    ).read_bytes()
    assert str(store.root) not in str(imported["scenario"])


def test_region_roundtrip_preserves_parameters_geometry_crop_and_real_network(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "source", None)
    source = store.create("Real region")
    params = {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 25, "level_height_m": 6}
    source = store.region(source["id"], params, SOURCE)
    source_url = f"/v1/studio/workspaces/{source['id']}"
    with TestClient(api(store)) as client:
        response = client.post(source_url + "/network")
        assert response.status_code == 200, response.json()
        assert response.json()["edge_count"] > 0 and response.json()["lane_count"] > 0
        exported = client.get(source_url + "/export").text
        source = client.get(source_url).json()
    target_store = WorkspaceStore(tmp_path / "target", None)
    target = target_store.create("Geography import")
    target_url = f"/v1/studio/workspaces/{target['id']}"
    with TestClient(api(target_store)) as client:
        response = client.post(target_url + "/import", json={"yaml": exported})
        assert response.status_code == 200, response.json()
        imported = response.json()
        assert imported["region"] == params
        assert imported["scene"] == source["scene"]
        assert imported["scene_source"] == source["scene_source"]
        assert imported["capabilities"]["region"] is True
        assert imported["capabilities"]["network"] is True
        assert imported["network_state"] == "generated"
        assert client.get(target_url + "/network.net.xml").status_code == 200
    for name in ("region.osm.xml", "network.net.xml"):
        assert (store.directory(source["id"]) / name).read_bytes() == (target_store.directory(target["id"]) / name).read_bytes()
    reopened = WorkspaceStore(target_store.root, None).summary(target["id"])
    assert reopened["region"] == params and reopened["network_state"] == "generated"


@pytest.mark.parametrize("failure", ["path", "reserved", "digest", "missing_crop", "origin", "scenario"])
def test_invalid_bundle_preserves_target_draft_and_all_resources(tmp_path: Path, failure: str) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    source = store.create("Export")
    source = store.region(source["id"], {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 12, "level_height_m": 4}, SOURCE)
    document = yaml.safe_load(store.export(source["id"]))
    target = store.create("Keep this draft")
    target = store.place(target["id"], {"id": "keeper", "type": "aas:StudioFacility", "kind": "facility", "position": [1, 2, 0], "engine": "workflow"})
    record = copy.deepcopy(document["files"]["registry.snapshot.json"])
    if failure == "path":
        document["files"]["../escape.json"] = record
    elif failure == "reserved":
        document["files"]["draft.json"] = record
    elif failure == "digest":
        document["files"]["registry.snapshot.json"]["sha256"] = "0" * 64
    elif failure == "missing_crop":
        del document["files"]["region.osm.xml"]
    elif failure == "origin":
        document["scenario"]["origin"]["alt"] = 13
    else:
        document["scenario"]["run"]["seed"] = None
    before = files(store.root)
    with TestClient(api(store)) as client:
        response = client.post(f"/v1/studio/workspaces/{target['id']}/import", json={"yaml": yaml.safe_dump(document), "revision": target["revision"]})
        assert response.status_code == 422, response.json()
        assert response.json()["detail"]
    assert files(store.root) == before


def test_export_opens_invalid_draft_for_repair_without_mutating_it(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Repair")
    draft["scenario"]["run"]["seed"] = None
    store._write(draft)
    before = files(store.root)
    with TestClient(api(store)) as client:
        response = client.get(f"/v1/studio/workspaces/{draft['id']}/export")
        assert response.status_code == 200
        assert yaml.safe_load(response.text)["scenario"]["run"]["seed"] is None
    assert files(store.root) == before


def test_bundle_cannot_overwrite_saved_versions_through_a_resource_symlink(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    source = store.create("Export resources")
    bundle = yaml.safe_load(store.export(source["id"]))
    target = store.create("Frozen versions")
    directory = store.directory(target["id"])
    frozen = directory / "versions" / "1"
    frozen.mkdir(parents=True)
    (frozen / "registry.snapshot.json").write_text("Preserve this frozen resource")
    (directory / "alias").symlink_to(frozen, target_is_directory=True)
    bundle["files"]["alias/registry.snapshot.json"] = bundle["files"]["registry.snapshot.json"]
    before = files(store.root)
    with pytest.raises(ValueError, match="reserved"):
        store.import_yaml(target["id"], yaml.safe_dump(bundle))
    assert files(store.root) == before
