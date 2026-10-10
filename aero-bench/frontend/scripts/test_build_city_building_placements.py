"""Focused checks for original fallback data and source-mesh envelopes."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
builder_path = Path(__file__).resolve().with_name("build-city-building-placements.py")
builder_spec = importlib.util.spec_from_file_location("city_building_placements", builder_path)
if builder_spec is None or builder_spec.loader is None:
    raise RuntimeError(f"Could not load placement builder from {builder_path}")
builder = importlib.util.module_from_spec(builder_spec)
builder_spec.loader.exec_module(builder)
from city_surface_identity import displayed_surface_sha256

BASELINE_PROJECTION_SHA256 = {
    "huangpu-night-building-placement-v1.json":
        "99574d71bb3792edf4092c6245455e856882cd6b5531e3de3c753636af12cda2",
    "shanghai-building-placement-v1.json":
        "64ea0ce3ca0496d05470a95355b1ea438b509adf6a03dc7f6003da9e29687531",
}


class SourceCollisionRectangleTest(unittest.TestCase):
    def test_rotated_envelope_covers_every_source_vertex_and_keeps_minimum_orientation(self):
        import math

        angle = math.radians(31)
        center_x, center_z = 24.5, -17.25
        points = []
        for local_x, local_z in ((-6, -2), (6, -2), (6, 2), (-6, 2)):
            points.append((center_x + math.cos(angle) * local_x - math.sin(angle) * local_z,
                           center_z + math.sin(angle) * local_x + math.cos(angle) * local_z))

        envelope = builder.source_collision_rectangle(points, "way/42", 3, 18)
        polygon = builder.placed_polygon(envelope)

        self.assertAlmostEqual(envelope["rotation_deg"], -31, places=2)
        self.assertAlmostEqual(envelope["width"], 12.02, places=2)
        self.assertAlmostEqual(envelope["depth"], 4.02, places=2)
        self.assertAlmostEqual(envelope["x"], center_x, places=2)
        self.assertAlmostEqual(envelope["z"], center_z, places=2)
        self.assertTrue(all(polygon.covers(Point(x, z)) for x, z in points))
        self.assertGreater(polygon.area, 48)

    def test_degenerate_source_mesh_fails_with_a_specific_error(self):
        with self.assertRaisesRegex(ValueError, "degenerate collision envelope"):
            builder.source_collision_rectangle([(0, 0), (1, 1), (2, 2)], "way/degenerate", 0, 5)


class CityPlacementIntegrationTest(unittest.TestCase):
    def test_fallback_placements_and_occluded_inventory_match_original_for_both_cities(self):
        repository = Path(__file__).resolve().parents[2]
        scene_names = ("huangpu-night-scene-v1.json", "shanghai-day-scene-v1.json")
        for scene_name in scene_names:
            with self.subTest(scene=scene_name):
                scene_path = repository / "frontend/public/city-presentation" / scene_name
                paths = builder.read_scene_paths(scene_path)
                baseline = json.loads(paths.placement.read_text(encoding="utf-8"))
                projection = {"placements": baseline["placements"],
                              "occluded_buildings": baseline["occluded_buildings"]}
                projection_bytes = json.dumps(projection, ensure_ascii=False,
                                              separators=(",", ":")).encode("utf-8")
                expected_sha = BASELINE_PROJECTION_SHA256[paths.placement.name]
                self.assertEqual(hashlib.sha256(projection_bytes).hexdigest(), expected_sha)

                manifest_bytes = paths.pack.read_bytes()
                manifest = json.loads(manifest_bytes)
                footprints, heights, road_surface, *_ = builder.building_geometry(
                    manifest, paths.pack.parent)
                placements, _areas, occluded, _inferred = builder.clipped_building_placements(
                    footprints, heights, road_surface)
                self.assertEqual(placements, baseline["placements"])
                self.assertEqual(occluded, baseline["occluded_buildings"])

    def test_displayed_roadbed_or_walkbed_geometry_mismatch_is_rejected(self):
        repository = Path(__file__).resolve().parents[2]
        scene = repository / "frontend/public/city-presentation/huangpu-night-scene-v1.json"
        paths = builder.read_scene_paths(scene)
        road_data = json.loads(paths.road.read_text(encoding="utf-8"))
        first_point = road_data["roadbed"][0]["outline"][0]
        road_data["roadbed"][0]["outline"][0] = [first_point[0] + 0.25, first_point[1]]
        self.assertNotEqual(
            road_data["displayed_surface_sha256"],
            displayed_surface_sha256(road_data["roadbed"], road_data["walkbed"]))
        with tempfile.TemporaryDirectory() as temporary:
            altered_road = Path(temporary) / "road-preview.json"
            altered_road.write_text(json.dumps(road_data), encoding="utf-8")
            altered_paths = SimpleNamespace(traffic=paths.traffic, pack=paths.pack,
                                            placement=paths.placement, road=altered_road)
            manifest_bytes = paths.pack.read_bytes()
            with self.assertRaisesRegex(ValueError, "Displayed road/walk surfaces differ"):
                builder.displayed_street_surface(altered_paths, json.loads(manifest_bytes), manifest_bytes)

    def test_current_placement_identity_update_preserves_recorded_sumo_and_flight_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            placement_path, road_path = root / "placement.json", root / "road.json"
            traffic_path, flight_path = root / "traffic.json", root / "flight.json"
            traffic_path.write_bytes(b"sumo evidence")
            flight_path.write_bytes(b"flight evidence")
            historical_sha = "a" * 64
            road_data = {"traffic_recorded_building_placement_sha256": historical_sha,
                         "roadbed": [{"outline": [[0, 0], [8, 0], [8, 5], [0, 5]], "holes": []}],
                         "walkbed": [{"outline": [[0, 6], [8, 6], [8, 8], [0, 8]], "holes": []}]}
            road_data["displayed_surface_sha256"] = displayed_surface_sha256(
                road_data["roadbed"], road_data["walkbed"])
            road_path.write_text(json.dumps(road_data), encoding="utf-8")
            before = (traffic_path.read_bytes(), flight_path.read_bytes())
            paths = SimpleNamespace(placement=placement_path, road=road_path)
            payload = {"schema_version": "aero-bench.city-building-placement/v1", "placements": []}

            placement_bytes = builder.write_placement_payload(paths, placement_path, payload)

            updated_road = json.loads(road_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_road["traffic_recorded_building_placement_sha256"], historical_sha)
            self.assertEqual(updated_road["building_placement_sha256"],
                             hashlib.sha256(placement_bytes).hexdigest())
            self.assertEqual((traffic_path.read_bytes(), flight_path.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
