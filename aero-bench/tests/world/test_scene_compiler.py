from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aero_bench.world.scene_compiler import (
    EnuBounds,
    SceneCompilationError,
    SceneOrigin,
    compile_urban_scene,
    crop_osm_dataset,
    load_osm_json,
    _enu_to_derived_node,
    _effective_vertical_geometry,
)


def _source_document() -> dict[str, object]:
    """A closed fixture including a crossing way and building multipolygons."""

    return {
        "version": 0.6,
        "generator": "scene-compiler-test",
        "elements": [
            {"type": "node", "id": 1, "lat": 0.0, "lon": -0.02},
            {"type": "node", "id": 2, "lat": 0.0, "lon": 0.02},
            # An enclosing area: no source vertex lies inside a 1 km crop.
            {"type": "node", "id": 3, "lat": -0.02, "lon": -0.02},
            {"type": "node", "id": 4, "lat": -0.02, "lon": 0.02},
            {"type": "node", "id": 5, "lat": 0.02, "lon": 0.02},
            {"type": "node", "id": 6, "lat": 0.02, "lon": -0.02},
            {"type": "node", "id": 7, "lat": -0.001, "lon": -0.001},
            {"type": "node", "id": 8, "lat": -0.001, "lon": 0.001},
            {"type": "node", "id": 9, "lat": 0.001, "lon": 0.001},
            {"type": "node", "id": 10, "lat": 0.001, "lon": -0.001},
            {"type": "node", "id": 11, "lat": -0.0002, "lon": -0.0002},
            {"type": "node", "id": 12, "lat": -0.0002, "lon": 0.0002},
            {"type": "node", "id": 13, "lat": 0.0002, "lon": 0.0002},
            {"type": "node", "id": 14, "lat": 0.0002, "lon": -0.0002},
            # Concave landuse crosses the crop in two disconnected strips.
            {"type": "node", "id": 15, "lat": -0.01, "lon": -0.01},
            {"type": "node", "id": 16, "lat": -0.01, "lon": 0.01},
            {"type": "node", "id": 17, "lat": -0.0044, "lon": 0.01},
            {"type": "node", "id": 18, "lat": -0.0044, "lon": -0.0044},
            {"type": "node", "id": 19, "lat": 0.0044, "lon": -0.0044},
            {"type": "node", "id": 20, "lat": 0.0044, "lon": 0.01},
            {"type": "node", "id": 21, "lat": 0.01, "lon": 0.01},
            {"type": "node", "id": 22, "lat": 0.01, "lon": -0.01},
            # A second, disconnected polygon produces a separate GEOS crop
            # component but retains the same source provenance through a relation.
            {"type": "node", "id": 23, "lat": -0.01, "lon": -0.01},
            {"type": "node", "id": 24, "lat": -0.01, "lon": -0.0044},
            {"type": "node", "id": 25, "lat": 0.01, "lon": -0.0044},
            {"type": "node", "id": 26, "lat": 0.01, "lon": -0.01},
            {
                "type": "way",
                "id": 100,
                "nodes": [1, 2],
                "tags": {"highway": "residential"},
            },
            {
                "type": "way",
                "id": 101,
                "nodes": [3, 4, 5, 6, 3],
                "tags": {"landuse": "commercial"},
            },
            {
                "type": "way",
                "id": 104,
                "nodes": [15, 16, 17, 18, 19, 20, 21, 22, 15],
                "tags": {"landuse": "grass"},
            },
            {
                "type": "way",
                "id": 105,
                "nodes": [23, 24, 25, 26, 23],
                "tags": {"landuse": "grass"},
            },
            {
                "type": "way",
                "id": 102,
                "nodes": [7, 8, 9, 10, 7],
                "tags": {"building": "yes", "height": "16"},
            },
            {
                "type": "way",
                "id": 103,
                "nodes": [11, 12, 13, 14, 11],
                "tags": {"building": "yes"},
            },
            {
                "type": "relation",
                "id": 204,
                "members": [
                    {"type": "way", "ref": 104, "role": "outer"},
                    {"type": "way", "ref": 105, "role": "outer"},
                ],
                "tags": {"type": "multipolygon", "landuse": "grass"},
            },
            {
                "type": "relation",
                "id": 200,
                "members": [{"type": "way", "ref": 102, "role": "outer"}],
                "tags": {"type": "multipolygon", "building": "yes", "building:levels": "4"},
            },
            {
                "type": "relation",
                "id": 201,
                "members": [{"type": "relation", "ref": 200, "role": ""}],
                "tags": {"type": "site", "name": "parent closure"},
            },
        ],
    }


def _write_source(tmp_path: Path, document: dict[str, object] | None = None) -> Path:
    source = tmp_path / "source.osm.json"
    source.write_text(
        json.dumps(document or _source_document(), separators=(",", ":")),
        encoding="utf-8",
    )
    return source


def _fixture_origin() -> SceneOrigin:
    return SceneOrigin(
        latitude_deg=0.0,
        longitude_deg=0.0,
        ellipsoid_height_m=50.0,
        geoid_undulation_m=30.0,
        amsl_m=20.0,
    )


def test_derived_boundary_node_roundtrips_at_the_compiler_ellipsoid_height() -> None:
    origin = SceneOrigin()
    east = -352.5138514132545
    north = -578.9425324190597
    node = _enu_to_derived_node(
        coordinate=(east, north),
        origin=origin,
        source_type="way",
        source_id=1,
        component_index=0,
        vertex_index=0,
    )
    projected = origin.enu_transform().geodetic_to_enu(
        longitude_deg=node["lon"],
        latitude_deg=node["lat"],
        altitude_m=origin.ellipsoid_height_m,
    )
    assert projected.x == pytest.approx(east, abs=1e-7)
    assert projected.y == pytest.approx(north, abs=1e-7)


def test_real_near_west_crop_keeps_effective_nodes_inside_enu_bounds(tmp_path: Path) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/public/osm2world/shanghai-hongqiao.osm.json"
    )
    origin = SceneOrigin()
    bounds = EnuBounds(
        min_east_m=-961.3755328838422,
        max_east_m=-352.5138514132545,
        min_north_m=-598.7150944099295,
        max_north_m=66.56847339581017,
    )
    result = compile_urban_scene(
        source_path=source,
        output_dir=tmp_path / "near-west",
        origin=origin,
        bounds=bounds,
    )
    effective = json.loads((result.output_dir / "osm/effective.osm.json").read_bytes())
    derived = [
        element for element in effective["elements"]
        if element["type"] == "node"
        and element.get("tags", {}).get("aero_bench:derived") == "geometry_crop_boundary"
    ]
    assert derived
    transform = origin.enu_transform()
    for element in derived:
        point = transform.geodetic_to_enu(
            longitude_deg=element["lon"],
            latitude_deg=element["lat"],
            altitude_m=origin.ellipsoid_height_m,
        )
        assert bounds.min_east_m - 1e-6 <= point.x <= bounds.max_east_m + 1e-6
        assert bounds.min_north_m - 1e-6 <= point.y <= bounds.max_north_m + 1e-6


def test_crop_selects_crossing_and_enclosing_geometry_with_reference_closure(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    dataset = load_osm_json(source)
    cropped = crop_osm_dataset(
        dataset,
        origin=_fixture_origin(),
        bounds=EnuBounds(-500.0, 500.0, -500.0, 500.0),
    )

    # Way 100 crosses the crop with neither endpoint inside it.  Way 101
    # encloses it while all vertices remain outside.  Both must be selected.
    assert {100, 101, 102, 103, 104, 105} <= cropped.way_ids
    assert {200, 201, 204} <= cropped.relation_ids
    assert {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14} <= cropped.node_ids


def test_compiler_emits_provenance_effective_geometry_and_native_sdf(
    tmp_path: Path,
) -> None:
    source = _write_source(tmp_path)
    source_bytes = source.read_bytes()
    provenance = tmp_path / "source.provenance.json"
    provenance.write_text(
        json.dumps({"sha256": hashlib.sha256(source_bytes).hexdigest()}),
        encoding="utf-8",
    )
    output = tmp_path / "compiled"

    result = compile_urban_scene(
        source_path=source,
        output_dir=output,
        source_provenance_path=provenance,
        origin=_fixture_origin(),
    )

    assert result.output_dir == output
    assert result.source_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert result.building_count == 2
    assert result.road_count == 1
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["origin"] == _fixture_origin().manifest_document()
    assert manifest["crop"]["enu_bounds_m"] == {
        "min_east_m": -500.0,
        "max_east_m": 500.0,
        "min_north_m": -500.0,
        "max_north_m": 500.0,
    }
    assert manifest["source"]["provenance"]["declared_source_sha256"] == result.source_sha256
    effective_extent = manifest["effective_render_task_enu_bounds_m"]
    sumo_extent = manifest["sumo_input_enu_bounds_m"]
    for extent in (effective_extent, sumo_extent):
        assert -500.0 - 1e-6 <= extent["min_east_m"] <= 500.0 + 1e-6
        assert -500.0 - 1e-6 <= extent["max_east_m"] <= 500.0 + 1e-6
        assert -500.0 - 1e-6 <= extent["min_north_m"] <= 500.0 + 1e-6
        assert -500.0 - 1e-6 <= extent["max_north_m"] <= 500.0 + 1e-6
    assert sumo_extent["min_east_m"] == pytest.approx(-500.0, abs=1e-5)
    assert sumo_extent["max_east_m"] == pytest.approx(500.0, abs=1e-5)
    assert manifest["closure_geometry_enu_bounds_m"]["min_east_m"] < -500.0

    source_json = json.loads((output / "audit" / "raw-closure.osm.json").read_text())
    source_xml = (output / "audit" / "raw-closure.osm").read_text(encoding="utf-8")
    cropped_elements = crop_osm_dataset(
        load_osm_json(source), origin=_fixture_origin()
    ).selected_elements()
    assert source_json["elements"] == cropped_elements
    assert '<way id="100">' in source_xml
    assert '<relation id="200">' in source_xml
    assert '<member type="way" ref="102" role="outer"/>' in source_xml

    effective = json.loads((output / "osm" / "effective.osm.json").read_text())
    effective_nodes = [item for item in effective["elements"] if item["type"] == "node"]
    assert effective["bounds"] == {
        "minlat": min(item["lat"] for item in effective_nodes),
        "minlon": min(item["lon"] for item in effective_nodes),
        "maxlat": max(item["lat"] for item in effective_nodes),
        "maxlon": max(item["lon"] for item in effective_nodes),
    }
    effective_derived_ways = [
        element
        for element in effective["elements"]
        if element["type"] == "way" and element["tags"].get("aero_bench:derived") == "geometry_crop"
    ]
    relation_ways = [
        element
        for element in effective_derived_ways
        if element["tags"].get("aero_bench:source_type") == "relation"
        and element["tags"].get("aero_bench:source_id") == "200"
    ]
    assert len(relation_ways) == 1
    assert relation_ways[0]["tags"]["height"] == "12"
    assert relation_ways[0]["tags"]["aero_bench:height_source"] == "derived_from_osm_building_levels"
    way_ways = [
        element
        for element in effective_derived_ways
        if element["tags"].get("aero_bench:source_type") == "way"
        and element["tags"].get("aero_bench:source_id") == "103"
    ]
    assert len(way_ways) == 1
    assert way_ways[0]["tags"]["height"] == "12"
    assert way_ways[0]["tags"]["aero_bench:height_source"] == "assumed_missing_osm_height"

    objects = json.loads((output / "metadata" / "objects.json").read_text())
    buildings = {item["object_id"]: item for item in objects["buildings"]}
    relation_building = buildings["building.relation.200.component.0"]
    way_building = buildings["building.way.103.component.0"]
    assert relation_building["top_enu_up_m"] == 12.0
    assert relation_building["simulation_assumption_ids"] == ["building_level_height_m"]
    assert way_building["simulation_assumption_ids"] == ["missing_building_height_m"]
    assert relation_building["effective_geometry_sha256"]

    sdf = (output / "gazebo" / "scene.sdf").read_text(encoding="utf-8")
    # Building SDF uses the exact source footprint vertices in a native SDF
    # polyline, rather than an inferred rectangle/grid or viewer geometry.
    assert "<polyline>" in sdf
    assert "<height>12</height>" in sdf
    assert "<point>-111.320363435406 -110.575148480719</point>" in sdf
    assert "<box>" not in sdf

    common = json.loads((output / "metadata" / "common-scene.json").read_text())
    assert common["gazebo"]["building_objects"] == objects["buildings"]
    assert common["ns3"]["obstruction_buildings"] == objects["buildings"]
    assert common["public_builder"]["renderer_input"] == "osm/effective.osm.json"
    assert common["public_builder"]["renderer_geometry_crop_enforced"] is True
    assert common["public_builder"]["raw_closure_renderer_input_forbidden"] is True
    assert common["public_builder"]["statement"].startswith("Use the official OSM2World")
    assert common["sumo"]["network_generation_input"] == "osm/sumo-network.osm"
    assert common["sumo"]["raw_closure_input_forbidden"] is True
    assert common["sumo"]["sumo_to_enu"]["identity_claim"] is False
    assert common["geometry_clipping_dependency"]["resolved_version"] == "2.0.7"
    # The concave landuse is retained as two legal derived polygon components;
    # it is not falsely joined across the crop's empty centre.
    area_components = common["derived_geometry"]["area_components"]
    assert len([item for item in area_components if item["source_id"] == 204]) == 2


def test_compiler_refuses_existing_output_and_hole_buildings(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(SceneCompilationError, match="already exists"):
        compile_urban_scene(source_path=source, output_dir=output, origin=_fixture_origin())

    source_document = _source_document()
    source_document["elements"].append(
        {
            "type": "relation",
            "id": 202,
            "members": [
                {"type": "way", "ref": 102, "role": "outer"},
                {"type": "way", "ref": 103, "role": "inner"},
            ],
            "tags": {"type": "multipolygon", "building": "yes"},
        }
    )
    hole_source = _write_source(tmp_path, source_document)
    with pytest.raises(SceneCompilationError, match="inner rings"):
        compile_urban_scene(
            source_path=hole_source,
            output_dir=tmp_path / "hole-refused",
            origin=_fixture_origin(),
        )


def test_building_levels_are_dimensionless_and_accept_trailing_decimal_point() -> None:
    assert _effective_vertical_geometry(
        tags={"building:levels": "14."}, label="building way/378107169",
        default_height_m=12.0, level_height_m=3.0,
    ) == (0.0, 42.0, "derived_from_osm_building_levels", ["building_level_height_m"])
    for invalid in ("8;6", "3 m"):
        with pytest.raises(SceneCompilationError, match="positive numeric level count"):
            _effective_vertical_geometry(
                tags={"building:levels": invalid}, label="building relation/19420005",
                default_height_m=12.0, level_height_m=3.0,
            )


def test_parser_rejects_missing_reference_and_netconvert_is_opt_in(tmp_path: Path) -> None:
    document = _source_document()
    next(element for element in document["elements"] if element.get("id") == 100)["nodes"] = [1, 999]
    invalid_source = _write_source(tmp_path, document)
    with pytest.raises(SceneCompilationError, match="missing node/999"):
        load_osm_json(invalid_source)

    source = _write_source(tmp_path)
    output = tmp_path / "without-netconvert"
    result = compile_urban_scene(source_path=source, output_dir=output, origin=_fixture_origin())
    assert result.netconvert_generated is False
    assert not (output / "sumo" / "network.net.xml").exists()
    command = json.loads((output / "sumo" / "netconvert-command.json").read_text())
    assert command["status"] == "not_invoked"
    assert command["command"][:3] == ["netconvert", "--osm-files", "osm/sumo-network.osm"]
    assert command["required_netconvert_version"] == "1.27.1"
    assert "--offset.disable-normalization" in command["command"]


def test_building_relation_with_two_outer_rings_keeps_separate_collision_components(tmp_path: Path) -> None:
    document = _source_document()
    document["elements"].extend(
        [
            {"type": "node", "id": 30, "lat": -0.001, "lon": 0.002},
            {"type": "node", "id": 31, "lat": -0.001, "lon": 0.003},
            {"type": "node", "id": 32, "lat": 0.001, "lon": 0.003},
            {"type": "node", "id": 33, "lat": 0.001, "lon": 0.002},
            {"type": "way", "id": 106, "nodes": [30, 31, 32, 33, 30]},
        ]
    )
    relation = next(item for item in document["elements"] if item.get("type") == "relation" and item.get("id") == 200)
    relation["members"].append({"type": "way", "ref": 106, "role": "outer"})
    source = _write_source(tmp_path, document)
    result = compile_urban_scene(source_path=source, output_dir=tmp_path / "two-outers", origin=_fixture_origin())
    scene = json.loads((result.output_dir / "metadata/objects.json").read_text())
    components = [item for item in scene["buildings"] if item["osm"] == {"type": "relation", "id": 200}]
    assert [item["component_index"] for item in components] == [0, 1]
    assert len({item["object_id"] for item in components}) == 2
    assert all(item["extrusion_height_m"] == 12.0 for item in components)
