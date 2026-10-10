"""Focused regressions for verified source-road extraction and centerline pairing."""

import hashlib
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from shapely import set_precision
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_SPEC = importlib.util.spec_from_file_location(
    "city_road_preview_builder", SCRIPT_DIR / "build-city-road-preview.py")
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("Could not load city road preview builder")
builder = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = builder
MODULE_SPEC.loader.exec_module(builder)


def synthetic_mesh_batch():
    triangles = [
        ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
        ((10, 0, 0), (11, 0, 0), (10, 0, 1)),
        ((20, 0, 0), (21, 0, 0), (20, 0, 1)),
        ((30, 0, 0), (31, 0, 0), (30, 0, 1)),
    ]
    vertices = np.asarray([point for triangle in triangles for point in triangle], dtype="<f4")
    index_values = np.arange(12, dtype="<u4")
    header = vertices.tobytes().ljust(12 * 32, b"\0")
    raw = header + index_values.tobytes()
    temporary = tempfile.TemporaryDirectory()
    pack_path = Path(temporary.name) / "manifest.json"
    asset_directory = pack_path.parent / "assets"
    asset_directory.mkdir()
    digest = hashlib.sha256(raw).hexdigest()
    (asset_directory / digest).write_bytes(raw)
    batch = {
        "vertices": 12,
        "indices": 12,
        "file": {"sha256": digest, "size_bytes": len(raw)},
        "ranges": [
            {"end": 1, "target": {"kind": "road", "id": "w100"}},
            {"end": 2, "target": {"kind": "road", "id": "w-1173568196440559872"}},
            {"end": 3, "target": {"kind": "road", "id": "w300"}},
            {"end": 4, "target": {"kind": "road", "id": "n42"}},
        ],
    }
    tags = {
        builder.osm_pack_identity("w100"): {"highway": "steps"},
        # OSM2World's JSON Number serialization rounds this ID in the ranges table.
        builder.osm_pack_identity("w-1173568196440559900"): {"highway": "tertiary"},
        builder.osm_pack_identity("w300"): {"highway": "footway"},
    }
    return temporary, pack_path, batch, tags


class ConcreteSourceSurfaceTest(unittest.TestCase):
    def test_source_mesh_vertices_use_the_explicit_coordinate_converter(self):
        temporary, path, batch, tags = synthetic_mesh_batch()
        self.addCleanup(temporary.cleanup)
        convert = lambda x, z: (x + 100, z - 20)
        asphalt = builder.horizontal_mesh_triangles(path, batch, point_transform=convert)
        concrete, _, _, _ = builder.concrete_motor_road_triangles(path, batch, tags,
            {builder.osm_pack_identity("n42")}, point_transform=convert)
        self.assertEqual(asphalt[0].bounds, (100, -20, 101, -19))
        self.assertEqual(concrete[0].bounds, (110, -20, 111, -19))
        self.assertEqual(concrete[-1].bounds, (130, -20, 131, -19))

    def test_source_building_clip_survives_millimetre_tiling(self):
        roof = box(0.0006, 0, 2, 200)
        touching_road = box(-2, 0, 0.0006, 200)
        # A millimetre overlay rounds the touching road edge into the source roof.
        rounded = Polygon(set_precision(touching_road, .001).exterior.coords)
        self.assertGreater(rounded.intersection(roof).area, .05)
        clipped = builder.clip_source_buildings(touching_road, roof)
        parts = builder.tiled_polygons(set_precision(clipped, .001), 60)
        rendered = unary_union([Polygon(part["outline"], part["holes"]) for part in parts])
        self.assertLess(rendered.intersection(roof).area, 1e-8)

    def test_small_corner_paving_is_not_discarded_by_tiling(self):
        corner = Polygon([(59.95, 0), (60.05, 0), (60.05, .2), (59.95, .2)])
        parts = builder.tiled_polygons(corner, 60)
        self.assertEqual(len(parts), 2)
        rendered = unary_union([Polygon(p["outline"], p["holes"]) for p in parts])
        self.assertLess(rendered.symmetric_difference(corner).area, 1e-9)


    def test_ground_filter_rejects_a_bridge_still_present_in_the_network(self):
        osm = ET.fromstring('<osm><way id="12"><tag k="highway" v="primary"/><tag k="bridge" v="yes"/></way></osm>')
        network = ET.fromstring('<net><edge id="12"/></net>')
        with self.assertRaisesRegex(ValueError, 'still contains'):
            builder.ground_mesh_targets(network, osm)

    def test_ground_filter_removes_flattened_bridge_by_identity_not_height(self):
        temporary, path, batch, _ = synthetic_mesh_batch()
        self.addCleanup(temporary.cleanup)
        allowed = {builder.osm_pack_identity("w-1173568196440559900")}
        surfaces = builder.horizontal_mesh_triangles(path, batch, allowed)
        self.assertEqual(len(surfaces), 1)
        self.assertEqual(surfaces[0].bounds, (10, 0, 11, 1))

    def test_ground_filter_excludes_junction_mesh_shared_with_removed_way(self):
        osm = ET.fromstring('''<osm>
          <way id="12"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/></way>
          <way id="13"><nd ref="2"/><nd ref="3"/><tag k="highway" v="primary"/></way>
        </osm>''')
        network = ET.fromstring('<net><edge id="12#0"/><edge id=":2_0" function="internal"/></net>')
        self.assertEqual(builder.ground_mesh_targets(network, osm), {('w', 12.), ('n', 1.)})

    def test_only_tagged_motor_road_range_is_selected_with_triangle_index_units(self):
        temporary, pack_path, batch, tags = synthetic_mesh_batch()
        self.addCleanup(temporary.cleanup)

        surfaces, source_way_ids, junction_ids, junction_triangles = \
            builder.concrete_motor_road_triangles(pack_path, batch, tags, set())

        self.assertEqual(source_way_ids, {"w-1173568196440559872"})
        self.assertEqual(junction_ids, set())
        self.assertEqual(junction_triangles, 0)
        self.assertEqual(len(surfaces), 1)
        self.assertEqual(surfaces[0].bounds, (10.0, 0.0, 11.0, 1.0))
        self.assertAlmostEqual(surfaces[0].area, 0.5)

    def test_concrete_junction_node_faces_are_included_only_for_source_road_nodes(self):
        temporary, pack_path, batch, tags = synthetic_mesh_batch()
        self.addCleanup(temporary.cleanup)

        surfaces, source_way_ids, junction_ids, junction_triangles = \
            builder.concrete_motor_road_triangles(pack_path, batch, tags, {("n", 42.0)})

        self.assertEqual(source_way_ids, {"w-1173568196440559872"})
        self.assertEqual(junction_ids, {"n42"})
        self.assertEqual(junction_triangles, 1)
        self.assertEqual(len(surfaces), 2)
        self.assertEqual(surfaces[1].bounds, (30.0, 0.0, 31.0, 1.0))

    def test_concrete_only_city_has_a_valid_source_road_footprint(self):
        temporary, pack_path, batch, tags = synthetic_mesh_batch()
        self.addCleanup(temporary.cleanup)
        batch["layer"] = "roads"
        batch["material"] = {"base_color_texture": builder.CONCRETE_ROAD_TEXTURE}
        batch["ranges"] = [
            {"end": 1, "target": {"kind": "road", "id": "w100"}},
            {"end": 2, "target": {"kind": "road", "id": "w-1173568196440559872"}},
            {"end": 3, "target": {"kind": "road", "id": "w300"}},
            {"end": 4, "target": {"kind": "road", "id": "n42"}},
        ]
        pack = {"objects": [
            {"id": "w100", "tags": {"highway": "steps"}},
            {"id": "w-1173568196440559900", "tags": {"highway": "tertiary"}},
            {"id": "w300", "tags": {"highway": "footway"}},
        ], "batches": [batch]}
        source_osm = ET.fromstring(
            '<osm><way id="1"><nd ref="42"/><tag k="highway" v="tertiary"/></way>'
            '<way id="2"><nd ref="42"/><tag k="highway" v="residential"/></way></osm>')

        asphalt, concrete, source_way_ids, junction_ids, junction_triangles = \
            builder.city_road_surfaces(pack_path, pack, "/asphalt.png", source_osm)

        self.assertEqual(asphalt, [])
        self.assertEqual(len(concrete), 2)
        self.assertEqual(source_way_ids, {"w-1173568196440559872"})
        self.assertEqual(junction_ids, {"n42"})
        self.assertEqual(junction_triangles, 1)

    def test_actual_verified_pack_contains_tagged_concrete_motor_road_surfaces(self):
        repository = SCRIPT_DIR.parents[1]
        scene = repository / "frontend/public/city-presentation/huangpu-night-scene-v1.json"
        network = repository / "validation/city-walking-continuity-20260927/normalized-urban/network.net.xml"
        paths = builder.read_scene_paths(scene)
        pack = json.loads(paths.pack.read_text(encoding="utf-8"))
        inputs = builder.read_inputs(scene, network, 60)
        source_osm = ET.parse(inputs.source_osm).getroot()

        asphalt, concrete, concrete_ways, junction_ids, junction_triangles = \
            builder.city_road_surfaces(paths.pack, pack, paths.road_surface_texture, source_osm)

        self.assertGreater(len(asphalt), 0)
        self.assertGreater(len(concrete), 0)
        self.assertIn("w-1120415005380151808", concrete_ways)
        self.assertEqual(len(junction_ids), 5)
        self.assertEqual(junction_triangles, 55)
        self.assertTrue(all(isinstance(surface, Polygon) and surface.area > 0 for surface in concrete))
        self.assertTrue(unary_union(concrete).covers(Point(-315.67, -112.34)))
        texture_asset = builder.pack_texture_asset(paths.pack, pack, builder.CONCRETE_ROAD_TEXTURE)
        self.assertIsNotNone(texture_asset)
        self.assertEqual(texture_asset["url"].rsplit("/", 1)[-1], texture_asset["sha256"])

    def test_unpublished_pack_manifest_copy_must_match_the_scene_pin(self):
        repository = SCRIPT_DIR.parents[1]
        scene = repository / "frontend/public/city-presentation/huangpu-night-scene-v1.json"
        published = builder.read_scene_paths(scene).pack
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory) / "manifest.json"
            staged.write_bytes(published.read_bytes())
            self.assertEqual(builder.read_scene_paths(scene, pack_manifest=staged).pack, staged)
            staged.write_bytes(published.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "differs from its pinned file reference"):
                builder.read_scene_paths(scene, pack_manifest=staged)


def lane(identifier, shape, width=3.2, kind="motor"):
    return {"id": identifier, "kind": kind, "width": width, "index": 0, "shape": shape}


def reverse_edge_pair(first_way="123", reverse_way="-123", offset=None, width=3.2,
                      reverse_width=None, kind="motor", first_shape=None, reverse_shape=None):
    reverse_width = width if reverse_width is None else reverse_width
    first_center = -width / 2 if offset is None else -offset
    reverse_center = reverse_width / 2 if offset is None else offset
    first_shape = [[0, first_center], [20, first_center]] if first_shape is None else first_shape
    reverse_shape = [[20, reverse_center], [0, reverse_center]] if reverse_shape is None else reverse_shape
    return [
        {"id": f"{first_way}#0", "from": "a", "to": "b", "function": "normal",
         "lanes": [lane(f"{first_way}#0_0", first_shape, width, kind)]},
        {"id": f"{reverse_way}#0", "from": "b", "to": "a", "function": "normal",
         "lanes": [lane(f"{reverse_way}#0_0", reverse_shape, reverse_width, kind)]},
    ]


class StreetLampCurbOffsetTest(unittest.TestCase):
    def test_offset_keeps_the_displayed_motor_band_geometry_off_the_road(self):
        from city_effective_fixtures import MOTOR_TOP_UP_M, POLE_CLEARANCE_M
        offset = builder.street_lamp_curb_offset()
        displayed = builder.displayed_fixture_triangles(builder.glb_triangles(builder.STREET_LAMP_MODEL), "street_lamp")
        band = builder.fixture_in_height_band(displayed, 0, MOTOR_TOP_UP_M)
        # A curb along x = -offset: the band geometry stays the pole clearance inside it.
        self.assertGreaterEqual(band.bounds[0] + offset["curb_offset_m"], POLE_CLEARANCE_M)
        self.assertEqual(offset["measured_motor_band_roadward_reach_m"], round(-band.bounds[0], 4))


class OpposingFlowSeparatorTest(unittest.TestCase):
    def test_same_source_way_reverse_edges_receive_dashed_direction_guide(self):
        guides = builder.bidirectional_direction_guides(
            reverse_edge_pair(), {"w123": "tertiary"})

        self.assertEqual(len(guides), 1)
        guide = guides[0]
        self.assertEqual(guide["source_way_id"], "w123")
        self.assertEqual(guide["marking"], "derived_direction_guide")
        self.assertEqual(guide["source_kind"], "sumo-lane-topology-not-surveyed-marking")
        self.assertEqual(guide["width_m"], 0.15)
        self.assertAlmostEqual(guide["shape"][0][1], 0)
        self.assertAlmostEqual(guide["shape"][-1][1], 0)

    def test_width_weighted_guide_lies_on_boundary_for_three_and_four_metre_lanes(self):
        guides = builder.bidirectional_direction_guides(
            reverse_edge_pair(width=3.0, reverse_width=4.0), {"w123": "tertiary"})

        self.assertEqual(len(guides), 1)
        self.assertTrue(all(abs(point[1]) <= 0.01 for point in guides[0]["shape"]))

    def test_width_weighted_guide_lies_on_boundary_for_3_2_and_3_4_metre_lanes(self):
        guides = builder.bidirectional_direction_guides(
            reverse_edge_pair(width=3.2, reverse_width=3.4), {"w123": "tertiary"})

        self.assertEqual(len(guides), 1)
        self.assertTrue(all(abs(point[1]) <= 0.01 for point in guides[0]["shape"]))

    def test_centerline_is_rejected_across_gap_larger_than_lane_half_widths(self):
        guides = builder.bidirectional_direction_guides(
            reverse_edge_pair(offset=2.0), {"w123": "tertiary"})

        self.assertEqual(guides, [])

    def test_guide_is_rejected_when_unequal_edges_leave_gap_over_tolerance(self):
        # Two 3.2 m lanes centered 3.4 m apart leave a 0.2 m gap between
        # their actual boundaries, even though their expanded corridors overlap.
        guides = builder.bidirectional_direction_guides(
            reverse_edge_pair(offset=1.7), {"w123": "tertiary"})

        self.assertEqual(guides, [])

    def test_actual_shanghai_polyline_rejects_gap_at_reverse_vertex(self):
        first_shape = [
            [-300.46, 130.14], [-298.82, 130.34], [-294.09, 131.6], [-294.01, 131.67],
        ]
        reverse_shape = [
            [-291.98, 129.18], [-292.59, 128.67], [-298.2, 127.19], [-300.06, 126.95],
        ]
        edges = reverse_edge_pair(first_way="512082850366489204",
                                  reverse_way="-512082850366489204",
                                  first_shape=first_shape, reverse_shape=reverse_shape)

        _, max_boundary_gap = builder.midpoint_line(first_shape, reverse_shape, 3.2, 3.2)
        self.assertGreater(max_boundary_gap, 0.238)
        self.assertEqual(builder.bidirectional_direction_guides(
            edges, {"w512082850366489204": "tertiary"}), [])

    def test_internal_minimum_center_distance_rejects_overlapping_lanes(self):
        first_shape = [[0, 0], [10, 0]]
        boundary_overlap = 3.2 - math.sqrt(3.2 ** 2 - 1.0)
        reverse_shape = [[11, math.sqrt(3.2 ** 2 - 1.0)],
                         [-1, math.sqrt(3.2 ** 2 - 1.0)]]
        edges = reverse_edge_pair(first_shape=first_shape, reverse_shape=reverse_shape)

        _, max_boundary_gap = builder.midpoint_line(first_shape, reverse_shape, 3.2, 3.2)
        self.assertAlmostEqual(max_boundary_gap, boundary_overlap, places=6)
        self.assertGreater(max_boundary_gap, builder.MAX_SHARED_BOUNDARY_GAP_M)
        self.assertEqual(builder.bidirectional_direction_guides(
            edges, {"w123": "tertiary"}), [])

    def test_separate_osm_ways_do_not_receive_invented_shared_centerline(self):
        edges = reverse_edge_pair(reverse_way="-456")

        self.assertEqual(builder.bidirectional_direction_guides(
            edges, {"w123": "tertiary", "w456": "tertiary"}), [])

    def test_nonreverse_topology_does_not_receive_centerline(self):
        edges = reverse_edge_pair()
        edges[1]["from"] = "c"

        self.assertEqual(builder.bidirectional_direction_guides(edges, {"w123": "tertiary"}), [])

    def test_narrow_and_minor_access_lanes_do_not_receive_direction_guides(self):
        for edges, highways in (
            (reverse_edge_pair(width=1.6), {"w123": "tertiary"}),
            (reverse_edge_pair(), {"w123": "service"}),
            (reverse_edge_pair(), {"w123": "living_street"}),
        ):
            with self.subTest(highways=highways, width=edges[0]["lanes"][0]["width"]):
                self.assertEqual(builder.bidirectional_direction_guides(edges, highways), [])


if __name__ == "__main__":
    unittest.main()
