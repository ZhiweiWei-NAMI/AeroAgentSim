"""Contract checks for selected static city presentation assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from aero_bench.authoring.presentation import (
    ROOT,
    _bigcity_tree_sha256,
    _read_ref,
    _strict_signal_inventory,
    _visual_asset_sources,
    _street_surface,
    _bind_concrete_texture,
    _placement_builder,
    _media_type,
    _verified_compiler_inputs,
    presentation_asset_identity,
)
from aero_bench.authoring.selection import SceneSelection
from aero_bench.serialization import canonical_json_bytes


def test_current_visual_library_is_bound_by_browser_paths_and_content() -> None:
    sources = _visual_asset_sources(ROOT / "frontend/public")
    inventory = {row["path"]: row for row, _ in sources}
    assert len(inventory) == len(sources)
    assert "/models/bigcity/manifest.json" in inventory
    assert "/models/bigcity/texture-aliases.json" in inventory
    assert "/models/incoming/furniture/glb/street_light_8.glb" in inventory
    assert "/models/incoming/furniture/glb/traffic_light_4.glb" in inventory
    assert "/models/incoming/furniture/glb/bus_stop_4.glb" in inventory
    assert "/models/incoming/urban-traffic/images/64d1d14365d8479458d03808ea6b95d5.webp" in inventory
    assert "/models/incoming/urban-traffic/images/f7a11eed4c9d2e047af8297ed9751e31.webp" in inventory
    assert sum(path.startswith("/models/bigcity/") for path in inventory) > 500
    assert all(row["sha256"] == hashlib.sha256(raw).hexdigest()
               and row["size_bytes"] == len(raw) for row, raw in sources)
    assert all(row["media_type"] == "model/gltf-binary" for row, _ in sources
               if str(row["path"]).endswith(".glb"))
    assert presentation_asset_identity()["bigcity_library_tree_sha256"] == \
        _bigcity_tree_sha256(sources)


def test_visual_inventory_rejects_a_mutated_declared_file(tmp_path: Path) -> None:
    path = tmp_path / "asset.webp"
    path.write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b"RIFF\x05\x00\x00\x00WEBP!")
    with pytest.raises(ValueError, match="SHA-256 differs"):
        _read_ref(path, original, "visual asset")


def test_glb_visual_asset_rejects_unverified_external_uri() -> None:
    document = json.dumps({"asset": {"version": "2.0"},
                           "buffers": [{"uri": "https://example.org/unpinned.bin"}]},
                          separators=(",", ":")).encode()
    padded = document + b" " * (-len(document) % 4)
    raw = (b"glTF" + (2).to_bytes(4, "little")
           + (20 + len(padded)).to_bytes(4, "little")
           + len(padded).to_bytes(4, "little") + b"JSON" + padded)
    with pytest.raises(ValueError, match="external URI"):
        _media_type(Path("traffic_light_4.glb"), raw)


def test_signal_inventory_rejects_wrong_network_or_nonfinite_coordinates() -> None:
    network = "a" * 64
    effective = "b" * 64
    signal = {
        "schema_version": "aero-bench.city-static-signal-inventory/v1",
        "source_network_sha256": network,
        "mesh_pack_source_sha256": effective,
        "signals": [{"id": "tls:a", "tls": "tls", "link": 0,
                     "x": 1.0, "z": 2.0, "heading": 90.0}],
    }
    _strict_signal_inventory(signal, network, effective)
    with pytest.raises(ValueError, match="identity"):
        _strict_signal_inventory(signal, "c" * 64, effective)
    signal["signals"][0]["x"] = float("nan")
    with pytest.raises(ValueError, match="geometry"):
        _strict_signal_inventory(signal, network, effective)


def test_static_road_requires_actual_surface_and_marking_geometry() -> None:
    builder = _placement_builder()
    road = {
        "schema_version": "aero-bench.city-static-road-candidate/v1",
        "mesh_pack_source_sha256": "a" * 64,
        "mesh_pack_manifest_sha256": "b" * 64,
        "roadbed": [{"outline": [[10, 0], [20, 0], [20, 5], [10, 5]], "holes": []}],
        "walkbed": [{"outline": [[10, 6], [20, 6], [20, 8], [10, 8]], "holes": []}],
        "concrete_roadbed": [], "lanes": [], "crossings": [], "curb_edges": [],
        "street_lamps": [], "junctions": [], "direction_guides": [],
        "street_layout": {"markings": [], "arrows": []},
    }
    road["displayed_surface_sha256"] = builder.displayed_surface_sha256(
        road["roadbed"], road["walkbed"])
    surface, surface_sha = _street_surface(road, "a" * 64, "b" * 64, builder)
    assert surface.area == 70
    assert surface_sha == road["displayed_surface_sha256"]
    changed = json.loads(json.dumps(road))
    changed["street_layout"].pop("markings")
    with pytest.raises(ValueError, match="markings"):
        _street_surface(changed, "a" * 64, "b" * 64, builder)
    changed = json.loads(json.dumps(road))
    changed["roadbed"][0]["outline"][0][0] += 1
    with pytest.raises(ValueError, match="identity"):
        _street_surface(changed, "a" * 64, "b" * 64, builder)


def test_concrete_texture_is_bound_only_for_rendered_concrete() -> None:
    texture = "/osm2world/style/textures/cc0textures/Concrete034/Concrete034_Color.jpg"
    reference = {"sha256": "a" * 64, "size_bytes": 123}
    material = {"concrete": {"texture": texture, "texture_asset": None}}
    empty = {"concrete_roadbed": [], "concrete_roadbed_area_m2": 0.0}
    assert _bind_concrete_texture(material, empty, {"textures": {}}, "b" * 64) == material
    assert material["concrete"]["texture_asset"] is None
    with pytest.raises(ValueError, match="without concrete geometry"):
        _bind_concrete_texture({"concrete": {"texture": texture, "texture_asset": reference}},
                               empty, {"textures": {texture: reference}}, "b" * 64)
    with pytest.raises(ValueError, match="unverified"):
        _bind_concrete_texture(material, {"concrete_roadbed": [{}], "concrete_roadbed_area_m2": 1},
                               {"textures": {texture: reference}}, "b" * 64)
    with_asset = {"concrete": {"texture": texture, "texture_asset": reference}}
    bound = _bind_concrete_texture(with_asset,
        {"concrete_roadbed": [{}], "concrete_roadbed_area_m2": 1},
        {"textures": {texture: reference}}, "b" * 64)
    assert bound["concrete"]["texture_asset"]["url"] == \
        f"/authoring/v1/scenes/{'b' * 64}/pack/assets/{'a' * 64}"


def test_new_static_placement_clearance_does_not_overlap_displayed_road() -> None:
    builder = _placement_builder()
    footprint = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    displayed = Polygon([(8, -1), (12, -1), (12, 11), (8, 11)])
    placements, _, _, _ = builder.clipped_building_placements(
        {"way/1": footprint}, {"way/1": [0.0, 12.0]}, displayed)
    assert placements
    assert all(builder.placed_polygon(item).intersection(displayed).area <= 0.01
               for item in placements)


def test_hidden_source_road_does_not_occlude_building_in_static_scene() -> None:
    builder = _placement_builder()
    footprint = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    hidden_source_road = footprint
    displayed_road = Polygon([(20, 0), (30, 0), (30, 5), (20, 5)])
    hidden_result, _, hidden_ids, _ = builder.clipped_building_placements(
        {"way/1": footprint}, {"way/1": [0.0, 12.0]}, hidden_source_road)
    shown_result, _, shown_ids, _ = builder.clipped_building_placements(
        {"way/1": footprint}, {"way/1": [0.0, 12.0]}, displayed_road)
    assert not hidden_result and hidden_ids == ["way/1"]
    assert shown_result and shown_ids == []
    assert all(builder.placed_polygon(item).intersection(displayed_road).area == 0
               for item in shown_result)


def test_inventory_schema_has_only_declared_fields() -> None:
    rows = [row for row, _ in _visual_asset_sources(ROOT / "frontend/public")]
    payload = {"schema_version": "aero-bench.city-visual-assets/v1", "assets": rows}
    decoded = json.loads(canonical_json_bytes(payload))
    assert set(decoded) == {"schema_version", "assets"}
    assert all(set(row) == {"path", "sha256", "size_bytes", "media_type"}
               for row in decoded["assets"])
    assert [row["path"] for row in decoded["assets"]] == sorted(
        row["path"] for row in decoded["assets"])


def test_compiler_receipt_binds_full_selection_and_every_output(tmp_path: Path) -> None:
    selection = SceneSelection.from_document({
        "schema_version": "aero-bench.scene-selection/v1",
        "source_id": "shanghai-central-osm-v1",
        "source_sha256": "a" * 64,
        "origin": {"latitude_deg": 31.2304, "longitude_deg": 121.4737,
                   "ellipsoid_height_m": 50.0, "geoid_undulation_m": 30.0, "amsl_m": 20.0},
        "bounds_enu_m": {"min_east_m": -10.0, "max_east_m": 10.0,
                         "min_north_m": -10.0, "max_north_m": 10.0},
    })
    (tmp_path / "authoring-selection.json").write_bytes(canonical_json_bytes(selection.to_document()))
    outputs = []
    for name, raw in (("osm/effective.osm.json", b"{}"),
                      ("osm/sumo-network.osm", b"<osm/>") ,
                      ("metadata/other.json", b"{}")):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        outputs.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(),
                        "byte_size": len(raw)})
    manifest = {
        "schema_version": "aero-bench.urban-scene-compiler/v1",
        "source": {"sha256": selection.source_sha256},
        "origin": selection.to_document()["origin"],
        "crop": {"enu_bounds_m": selection.bounds_enu_m.manifest_document()},
        "outputs": outputs,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    _, effective, sumo = _verified_compiler_inputs(selection, tmp_path)
    assert effective["path"] == "osm/effective.osm.json"
    assert sumo["path"] == "osm/sumo-network.osm"

    changed = json.loads(json.dumps(manifest))
    changed["origin"]["amsl_m"] = 21.0
    manifest_path.write_bytes(canonical_json_bytes(changed))
    with pytest.raises(ValueError, match="source or bounds"):
        _verified_compiler_inputs(selection, tmp_path)

    changed = json.loads(json.dumps(manifest))
    changed["outputs"].append(dict(changed["outputs"][0]))
    manifest_path.write_bytes(canonical_json_bytes(changed))
    with pytest.raises(ValueError, match="duplicate output"):
        _verified_compiler_inputs(selection, tmp_path)

    changed = json.loads(json.dumps(manifest))
    changed["outputs"][2]["byte_size"] += 1
    manifest_path.write_bytes(canonical_json_bytes(changed))
    with pytest.raises(ValueError, match="size differs"):
        _verified_compiler_inputs(selection, tmp_path)

    manifest_path.write_bytes(canonical_json_bytes(manifest))
    selected = selection.to_document()
    selected["source_id"] = "unregistered-other-source"
    (tmp_path / "authoring-selection.json").write_bytes(canonical_json_bytes(selected))
    with pytest.raises(ValueError, match="authoring selection differs"):
        _verified_compiler_inputs(selection, tmp_path)
