"""Region previews are real compilation results and do not change draft state."""

import copy
import math
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.scene import compile_scene, extract_bounds, origin_distance, preview_geometry
from aeroagentsim.authoring.workspace import WorkspaceStore

SOURCE = Path(__file__).parent / "fixtures/wujiaochang.osm.xml"


def region(**changes: Any) -> dict[str, Any]:
    return {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 20.0, "level_height_m": 3.5, **changes}


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_preview_exact_shape_unknown_origin_and_no_files_written(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Region preview")
    before = files(store.root)
    preview = store.region(draft["id"], region(dry_run=True), SOURCE)["preview"]
    compiled = compile_scene(SOURCE, extract_bounds(SOURCE), alt=20.0, level_height_m=3.5)
    assert set(preview) == {
        "extract", "bounds", "origin", "previous_origin", "origin_shift_m", "buildings", "roads",
        "attribution", "height_source", "diagnostics", "geometry", "affected_entities", "network_invalidated", "city_conflict",
    }
    assert preview["origin"] == compiled["origin"]
    assert preview["bounds"] == compiled["bounds"]
    assert preview["previous_origin"] is None and preview["origin_shift_m"] is None
    assert preview["buildings"] == 3 and preview["roads"] == len(compiled["roads"]) == 1
    assert preview["attribution"] == compiled["attribution"]
    assert preview["diagnostics"] == compiled["diagnostics"]
    assert "missing" in preview["height_source"]
    assert preview["affected_entities"] == []
    assert preview["network_invalidated"] is False and preview["city_conflict"] is False
    assert store.get(draft["id"]) == draft and files(store.root) == before


def test_replacement_preview_exposes_origin_shift_positions_network_and_city(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Replace region")
    draft = store.region(draft["id"], region(), SOURCE)
    draft = store.place(draft["id"], {
        "id": "marker", "type": "aas:StudioFacility", "kind": "facility",
        "position": [0, 0, 0], "engine": "workflow",
    })
    draft["scenario"]["city"] = "huangpu"
    draft["scenario"]["entities"].append({"id": "no-position", "type": "aas:StudioFacility", "facts": {}})
    draft["network"] = {"edge_count": 1, "lane_count": 2}
    draft = store._write(draft)
    network = store.directory(draft["id"]) / "network.net.xml"
    network.write_text("Existing network must survive preview")
    west, south, east, north = extract_bounds(SOURCE)
    replacement = region(bounds=[west, south, (west + east) / 2, north], dry_run=True)
    before = files(store.root)
    preview = store.region(draft["id"], replacement, SOURCE)["preview"]
    assert preview["previous_origin"] == draft["scenario"]["origin"]
    assert preview["origin_shift_m"] == pytest.approx(origin_distance(preview["previous_origin"], preview["origin"]))
    assert preview["origin_shift_m"] > 100
    assert preview["affected_entities"] == ["marker"]
    assert preview["network_invalidated"] is True and preview["city_conflict"] is True
    assert files(store.root) == before
    replacement["dry_run"] = False
    updated = store.region(draft["id"], replacement, SOURCE)
    assert updated["revision"] == draft["revision"] + 1
    assert "network" not in updated and not network.exists()
    assert "dry_run" not in updated["region"]
    assert updated["scene"]["origin"] == preview["origin"] == updated["scenario"]["origin"]
    assert updated["scenario"]["entities"][0]["facts"]["aas.studio.position_enu_m"] == [0.0, 0.0, 0.0]


def test_origin_displacement_includes_altitude_and_wgs84_distance() -> None:
    origin = {"lat": 0.0, "lon": 0.0, "alt": 0.0}
    assert origin_distance(origin, origin) == 0.0
    assert origin_distance(origin, {**origin, "alt": 12.0}) == pytest.approx(12.0)
    assert origin_distance(origin, {**origin, "lon": 0.001}) == pytest.approx(111.31949, rel=1e-6)


@pytest.mark.parametrize("changes", [{"bounds": [0, 0, 1, 1]}, {"level_height_m": 0}, {"dry_run": "true"}])
def test_invalid_preview_preserves_workspace(tmp_path: Path, changes: dict[str, Any]) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Invalid preview")
    before = files(store.root)
    with pytest.raises((ValueError, TypeError)):
        store.region(draft["id"], region(**{"dry_run": True, **changes}), SOURCE)
    assert files(store.root) == before


def test_catalog_and_region_rest_contracts(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("REST preview")
    app = FastAPI()
    app.include_router(create_router(store, extracts={"fixture": SOURCE}))
    with TestClient(app) as client:
        extracts = client.get("/v1/studio/catalog").json()["extracts"]
        assert extracts == [{"id": "fixture", "name": "fixture", "label": "fixture", "bounds": extract_bounds(SOURCE)}]
        response = client.post(f"/v1/studio/workspaces/{draft['id']}/region", json=region(dry_run=True))
        assert response.status_code == 200 and response.json()["preview"]["buildings"] == 3
        invalid = client.post(f"/v1/studio/workspaces/{draft['id']}/region", json=region(dry_run=True, extract="missing"))
        assert invalid.status_code == 422
    assert store.get(draft["id"]) == draft


def test_catalog_keeps_unknown_bounds_absent_with_an_explicit_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "empty.osm.xml"
    source.write_text('<osm version="0.6"/>')
    app = FastAPI()
    app.include_router(create_router(WorkspaceStore(tmp_path / "workspaces", None), extracts={"empty": source, "fixture": SOURCE}))
    with TestClient(app) as client:
        response = client.get("/v1/studio/catalog")
        assert response.status_code == 200
        empty, known = response.json()["extracts"]
        assert empty["id"] == empty["label"] == "empty"
        assert "bounds" not in empty
        assert "no coordinate nodes" in empty["diagnostics"][0]
        assert known["bounds"] == extract_bounds(SOURCE)


def test_preview_buildings_and_roads_are_enu_relative_to_preview_origin(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("ENU preview")
    before = files(store.root)
    preview = store.region(draft["id"], region(dry_run=True), SOURCE)["preview"]
    geometry = preview["geometry"]
    compiled = compile_scene(SOURCE, extract_bounds(SOURCE), alt=20.0, level_height_m=3.5)
    original = [feature for feature in compiled["geojson"]["features"] if feature["geometry"]["type"] == "Polygon"]
    assert geometry["buildings"]["type"] == "FeatureCollection"
    assert len(geometry["buildings"]["features"]) == 3
    rad, a, e2 = math.pi / 180, 6378137.0, 6.69437999014e-3
    origin = preview["origin"]
    d = 1 - e2 * math.sin(origin["lat"] * rad) ** 2
    east_per_deg = a / math.sqrt(d) * math.cos(origin["lat"] * rad) * rad
    north_per_deg = a * (1 - e2) / d**1.5 * rad
    for source, feature in zip(original, geometry["buildings"]["features"]):
        assert source["id"] == feature["id"] and source["properties"] == feature["properties"]
        for (lon, lat), (east, north, up) in zip(source["geometry"]["coordinates"][0], feature["geometry"]["coordinates"][0]):
            assert origin["lon"] + east / east_per_deg == pytest.approx(lon, abs=1e-12)
            assert origin["lat"] + north / north_per_deg == pytest.approx(lat, abs=1e-12)
            assert up == 0.0
    assert geometry["roads"] == [{"id": road["id"], "points": road["points"]} for road in compiled["roads"]]
    assert geometry["truncated"] is False
    assert files(store.root) == before


@pytest.mark.parametrize("buildings,roads,truncated", [(3000, 3000, False), (3001, 3, True), (3, 3001, True), (3001, 3001, True)])
def test_preview_caps_each_geometry_collection_and_preserves_multipolygon_holes(buildings: int, roads: int, truncated: bool) -> None:
    feature = {"type": "Feature", "id": "with-hole", "properties": {"building": "yes"}, "geometry": {
        "type": "MultiPolygon", "coordinates": [[
            [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]],
            [[0.0002, 0.0002], [0.0004, 0.0002], [0.0004, 0.0004], [0.0002, 0.0004], [0.0002, 0.0002]],
        ]],
    }}
    scene = {"origin": {"lat": 0.0, "lon": 0.0, "alt": 20.0},
             "geojson": {"type": "FeatureCollection", "features": [feature] * buildings},
             "roads": [{"id": "road", "points": [[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]], "tags": {}}] * roads}
    before = copy.deepcopy(feature)
    geometry = preview_geometry(scene)
    assert len(geometry["buildings"]["features"]) == min(buildings, 3000)
    assert len(geometry["roads"]) == min(roads, 3000)
    assert geometry["truncated"] is truncated
    polygon = geometry["buildings"]["features"][0]["geometry"]["coordinates"][0]
    assert len(polygon) == 2 and polygon[0][0] == [0.0, 0.0, 0.0]
    assert polygon[0][1][0] == pytest.approx(111.319490793, abs=1e-8)
    assert polygon[1][0][0] > 0
    assert "height" not in geometry["buildings"]["features"][0]["properties"]
    assert feature == before
