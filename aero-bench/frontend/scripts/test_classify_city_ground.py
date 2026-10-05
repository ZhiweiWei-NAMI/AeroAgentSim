"""Focused regressions for the C6(a) ground classification script.

Covers: priority/partition area sums, open-ring road reuse, pinned-input
verification (missing/mismatched), unknown-tag reporting, and a real run on
the published default scene.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import Polygon, box

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_SPEC = importlib.util.spec_from_file_location("classify_city_ground", SCRIPT_DIR / "classify-city-ground.py")
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("Could not load classify-city-ground.py")
classify = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = classify
MODULE_SPEC.loader.exec_module(classify)

from city_surface_identity import displayed_surface_sha256  # noqa: E402


def ring_part(points: list[list[float]]) -> dict:
    return {"outline": points, "holes": []}


def closed(points: list[list[float]]) -> list[list[float]]:
    return [*points, points[0]]


# ---------------------------------------------------------------------------
# Pure partition-priority tests
# ---------------------------------------------------------------------------

class PartitionGroundTest(unittest.TestCase):
    def test_priority_and_sum_over_overlapping_classes(self):
        extent = box(0, 0, 10, 10)
        # roadbed and a green polygon fully overlap the same 4x4 square: roadbed wins.
        contested = Polygon([[0, 0], [4, 0], [4, 4], [0, 4]])
        extra_green = Polygon([[6, 6], [9, 6], [9, 9], [6, 9]])
        class_features = {
            "roadbed": [{"id": "road.roadbed[0]", "polygon": contested, "tags": {}}],
            "green": [{"id": "green:a", "polygon": contested, "tags": {}},
                      {"id": "green:b", "polygon": extra_green, "tags": {}}],
        }
        classes = classify.partition_ground(extent, class_features)
        self.assertAlmostEqual(classes["roadbed"]["geometry"].area, 16.0, places=9)
        # The contested part of green is fully claimed by roadbed; only the extra green remains.
        self.assertAlmostEqual(classes["green"]["geometry"].area, 9.0, places=9)
        self.assertEqual([f["id"] for f in classes["green"]["features"]], ["green:b"])
        total = sum(classes[name]["geometry"].area for name in [*classify.CLASS_ORDER, "unclassified"])
        self.assertAlmostEqual(total, extent.area, delta=extent.area * 1e-9)

    def test_unclassified_is_whatever_no_class_claims(self):
        extent = box(0, 0, 10, 10)
        building = Polygon([[1, 1], [3, 1], [3, 3], [1, 3]])
        classes = classify.partition_ground(extent, {"building": [{"id": "b1", "polygon": building, "tags": {}}]})
        self.assertAlmostEqual(classes["building"]["geometry"].area, 4.0, places=9)
        self.assertAlmostEqual(classes["unclassified"]["geometry"].area, 96.0, places=9)
        total = sum(classes[name]["geometry"].area for name in [*classify.CLASS_ORDER, "unclassified"])
        self.assertAlmostEqual(total, extent.area, delta=extent.area * 1e-9)

    def test_full_priority_chain_resolves_every_overlap_in_declared_order(self):
        extent = box(0, 0, 100, 10)
        # Eight unit-offset, fully-overlapping squares, one per class in CLASS_ORDER: only
        # the highest-priority one should keep any area.
        square = Polygon([[0, 0], [10, 0], [10, 10], [0, 10]])
        class_features = {name: [{"id": name, "polygon": square, "tags": {}}] for name in classify.CLASS_ORDER}
        classes = classify.partition_ground(extent, class_features)
        self.assertAlmostEqual(classes[classify.CLASS_ORDER[0]]["geometry"].area, 100.0, places=9)
        for name in classify.CLASS_ORDER[1:]:
            self.assertAlmostEqual(classes[name]["geometry"].area, 0.0, places=9)


# ---------------------------------------------------------------------------
# Road-surface open-ring reuse
# ---------------------------------------------------------------------------

class RoadSurfaceReuseTest(unittest.TestCase):
    def test_open_ring_roadbed_and_walkbed_parts_build_valid_polygons(self):
        road = {
            "roadbed": [ring_part([[0, 0], [4, 0], [4, 2], [0, 2]])],
            "walkbed": [ring_part([[4, 0], [6, 0], [6, 2], [4, 2]])],
        }
        roadbed_features = classify.road_class_features(road, "roadbed")
        walkbed_features = classify.road_class_features(road, "walkbed")
        self.assertEqual(len(roadbed_features), 1)
        self.assertAlmostEqual(roadbed_features[0]["polygon"].area, 8.0, places=9)
        self.assertEqual(roadbed_features[0]["id"], "road.roadbed[0]")
        self.assertAlmostEqual(walkbed_features[0]["polygon"].area, 4.0, places=9)

    def test_closed_ring_road_part_is_rejected(self):
        road = {"roadbed": [ring_part(closed([[0, 0], [4, 0], [4, 2], [0, 2]]))], "walkbed": []}
        with self.assertRaises(ValueError):
            classify.road_class_features(road, "roadbed")


# ---------------------------------------------------------------------------
# Raw-OSM tagged-area extraction and classification
# ---------------------------------------------------------------------------

ORIGIN = {"latitude_deg": 31.0, "longitude_deg": 121.0, "ellipsoid_height_m": 50.0}


def node(node_id: int, lon: float, lat: float, tags: dict | None = None) -> dict:
    entry = {"type": "node", "id": node_id, "lon": lon, "lat": lat}
    if tags:
        entry["tags"] = tags
    return entry


def way(way_id: int, node_ids: list[int], tags: dict) -> dict:
    return {"type": "way", "id": way_id, "nodes": node_ids, "tags": tags}


def square_nodes(base_id: int, lon0: float, lat0: float, size: float) -> tuple[list[dict], list[int]]:
    coordinates = [(lon0, lat0), (lon0 + size, lat0), (lon0 + size, lat0 + size), (lon0, lat0 + size)]
    nodes = [node(base_id + i, lon, lat) for i, (lon, lat) in enumerate(coordinates)]
    ids = [n["id"] for n in nodes] + [base_id]
    return nodes, ids


class ExtractTaggedAreasTest(unittest.TestCase):
    def test_classifies_known_and_unknown_tags_and_skips_non_area_and_green(self):
        elements = []
        way_id = 1

        def add_square(lon0, lat0, tags, geometry_kind="polygon"):
            nonlocal way_id
            full_tags = {**tags, "aero_bench:geometry_kind": geometry_kind}
            nodes, ring = square_nodes(way_id * 100, lon0, lat0, 0.001)
            elements.extend(nodes)
            elements.append(way(way_id, ring, full_tags))
            way_id += 1
            return way_id - 1

        residential_id = add_square(121.000, 31.000, {"landuse": "residential"})
        farmland_id = add_square(121.010, 31.000, {"landuse": "farmland"})  # unknown -> other_tagged
        water_id = add_square(121.020, 31.000, {"natural": "water"})
        square_place_id = add_square(121.030, 31.000, {"place": "square"})
        pedestrian_area_id = add_square(121.040, 31.000, {"highway": "pedestrian"})
        green_id = add_square(121.050, 31.000, {"landuse": "grass"})  # must be fully skipped
        add_square(121.060, 31.000, {"highway": "footway"}, geometry_kind="polyline")  # not an area
        untagged_id = add_square(121.070, 31.000, {"aero_bench:source_id": "x"})

        features = classify.extract_tagged_areas({"elements": elements}, ORIGIN)
        by_id = {f["elementId"]: f for f in features}

        self.assertNotIn(green_id, by_id, "green-tagged ways must not appear in the raw tagged-area scan")
        self.assertEqual(len(by_id), 6)  # residential, farmland, water, square, pedestrian, untagged

        self.assertEqual(classify.classify_area_tags(by_id[residential_id]["tags"]),
                         ("excluded_landuse", "landuse=residential"))
        self.assertEqual(classify.classify_area_tags(by_id[farmland_id]["tags"]),
                         ("other_tagged", "landuse=farmland"))
        self.assertEqual(classify.classify_area_tags(by_id[water_id]["tags"]), ("water", "natural=water"))
        self.assertEqual(classify.classify_area_tags(by_id[square_place_id]["tags"]), ("plaza", "place=square"))
        self.assertEqual(classify.classify_area_tags(by_id[pedestrian_area_id]["tags"]),
                         ("plaza", "highway=pedestrian"))
        self.assertEqual(classify.classify_area_tags(by_id[untagged_id]["tags"]), ("other_tagged", "untagged"))

    def test_open_ring_tagged_way_is_rejected(self):
        nodes, ring = square_nodes(1, 121.0, 31.0, 0.001)
        elements = [*nodes, way(1, ring[:-1], {"landuse": "residential", "aero_bench:geometry_kind": "polygon"})]
        with self.assertRaises(ValueError):
            classify.extract_tagged_areas({"elements": elements}, ORIGIN)

    def test_multipolygon_relation_builds_outer_minus_inner_and_hides_member_ways(self):
        outer_nodes, outer_ring = square_nodes(1, 121.000, 31.000, 0.004)
        inner_nodes, inner_ring = square_nodes(100, 121.001, 31.001, 0.001)
        elements = [*outer_nodes, *inner_nodes,
                   way(1, outer_ring, {"aero_bench:geometry_kind": "polygon"}),
                   way(2, inner_ring, {"aero_bench:geometry_kind": "polygon"}),
                   {"type": "relation", "id": 9, "tags": {"type": "multipolygon", "landuse": "construction",
                                                          "aero_bench:geometry_kind": "multipolygon"},
                    "members": [{"type": "way", "ref": 1, "role": "outer"},
                               {"type": "way", "ref": 2, "role": "inner"}]}]
        features = classify.extract_tagged_areas({"elements": elements}, ORIGIN)
        ids = {f["elementId"] for f in features}
        # Member ways 1 and 2 are boundary segments of the relation, not standalone areas.
        self.assertNotIn(1, ids)
        self.assertNotIn(2, ids)
        relation_features = [f for f in features if f["elementType"] == "relation"]
        self.assertEqual(len(relation_features), 1)
        self.assertEqual(classify.classify_area_tags(relation_features[0]["tags"]),
                         ("excluded_landuse", "landuse=construction"))
        outer_poly = relation_features[0]["polygon"]
        self.assertTrue(outer_poly.interiors, "hole must survive into the assembled polygon")

    def test_relation_hole_not_covered_by_outer_raises(self):
        outer_nodes, outer_ring = square_nodes(1, 121.000, 31.000, 0.001)
        stray_nodes, stray_ring = square_nodes(100, 121.050, 31.050, 0.001)
        elements = [*outer_nodes, *stray_nodes,
                   way(1, outer_ring, {"aero_bench:geometry_kind": "polygon"}),
                   way(2, stray_ring, {"aero_bench:geometry_kind": "polygon"}),
                   {"type": "relation", "id": 9, "tags": {"landuse": "construction",
                                                          "aero_bench:geometry_kind": "multipolygon"},
                    "members": [{"type": "way", "ref": 1, "role": "outer"},
                               {"type": "way", "ref": 2, "role": "inner"}]}]
        with self.assertRaises(ValueError):
            classify.extract_tagged_areas({"elements": elements}, ORIGIN)


# ---------------------------------------------------------------------------
# Pinned-input verification over a minimal, self-consistent scene fixture
# ---------------------------------------------------------------------------

class ScenePinVerificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=".classify-ground-test-", dir=str(classify.ROOT)))
        self.public = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._build_fixture()
        self._patch_public_asset()

    def _patch_public_asset(self):
        original = classify.public_asset

        def fake_public_asset(url: str) -> Path:
            if not isinstance(url, str) or not url.startswith("/"):
                raise ValueError(f"Scene asset must be an absolute public URL: {url!r}")
            return self.public.joinpath(*url.split("/")[1:])

        classify.public_asset = fake_public_asset
        self.addCleanup(setattr, classify, "public_asset", original)

    def _write(self, relative: str, data: bytes) -> Path:
        path = self.public / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _sha(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _build_fixture(self):
        osm = {"elements": [
            *square_nodes(1, 121.000, 31.000, 0.001)[0],
            way(1, square_nodes(1, 121.000, 31.000, 0.001)[1],
               {"landuse": "residential", "aero_bench:geometry_kind": "polygon"}),
        ]}
        osm_bytes = json.dumps(osm).encode("utf-8")
        osm_sha = self._sha(osm_bytes)
        self._write(f"osm2world/packs/test-scene/assets/{osm_sha}", osm_bytes)

        pack_manifest = {"source": {"sha256": osm_sha, "size_bytes": len(osm_bytes)},
                         "extent": {"west": -500.0, "east": 500.0, "south": -500.0, "north": 500.0}}
        pack_manifest_bytes = json.dumps(pack_manifest).encode("utf-8")
        pack_manifest_sha = self._sha(pack_manifest_bytes)
        self._write("osm2world/packs/test-scene/manifest.json", pack_manifest_bytes)

        objects_sha = "a" * 64
        environment = {
            "schemaVersion": "aero-bench.city-environment-source/v1",
            "source": {"osmSha256": osm_sha, "objectsSha256": objects_sha, "origin": ORIGIN},
            "buildings": [{"id": "building.way.1.component.0",
                          "outline": closed([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]),
                          "holes": [], "geometrySha256": "b" * 64}],
            "greens": [{"id": "osm:way:2:0",
                       "outline": closed([[10.0, 10.0], [12.0, 10.0], [12.0, 12.0], [10.0, 12.0]]),
                       "holes": [], "provenance": {"tags": {"landuse": "grass"}}}],
            "groundCovers": [{"id": "osm:way:1:0", "kind": "construction", "surface": None,
                              "outline": closed([[30.0, 30.0], [32.0, 30.0], [32.0, 32.0], [30.0, 32.0]]),
                              "holes": [], "provenance": {"tags": {"landuse": "construction"}}}],
            "sourceTrees": [],
            "inspection": {"taggedAreaCount": 1, "greenAreaCount": 1, "sourceTreeCount": 0, "excludedTags": {}},
        }
        environment_bytes = json.dumps(environment).encode("utf-8")
        environment_sha = self._sha(environment_bytes)
        self._write("city-presentation/test-environment-source.json", environment_bytes)

        roadbed = [ring_part([[-20.0, -20.0], [-16.0, -20.0], [-16.0, 20.0], [-20.0, 20.0]])]
        walkbed = [ring_part([[-16.0, -20.0], [-14.0, -20.0], [-14.0, 20.0], [-16.0, 20.0]])]
        surface_sha = displayed_surface_sha256(roadbed, walkbed)
        road = {"roadbed": roadbed, "walkbed": walkbed, "mesh_pack_source_sha256": osm_sha,
               "source_context": {"objects_json_sha256": objects_sha}, "displayed_surface_sha256": surface_sha}
        road_bytes = json.dumps(road).encode("utf-8")
        road_sha = self._sha(road_bytes)
        self._write("city-presentation/test-road-v3.json", road_bytes)

        self.scene = {
            "schema_version": "aero-bench.city-scene-preview/v1",
            "environment_source": {"sha256": environment_sha, "url": "/city-presentation/test-environment-source.json"},
            "mesh_pack": {"base_url": "/osm2world/packs/test-scene/", "manifest": {"sha256": pack_manifest_sha}},
            "road_assets": {"road": {"sha256": road_sha, "url": "/city-presentation/test-road-v3.json"},
                            "mesh_pack_manifest_sha256": pack_manifest_sha, "mesh_pack_source_sha256": osm_sha,
                            "displayed_surface_sha256": surface_sha},
        }
        self.scene_path = self._write("city-presentation/test-scene.json", json.dumps(self.scene).encode("utf-8"))

    def _rewrite_scene(self, mutate) -> Path:
        scene = json.loads(self.scene_path.read_text())
        mutate(scene)
        self.scene_path.write_text(json.dumps(scene))
        return self.scene_path

    def test_consistent_fixture_resolves_and_partitions_cleanly(self):
        report, classes, extent = classify.classify_scene(self.scene_path)
        self.assertTrue(report["partition_check"]["within_1e-6"])
        building_entry = next(c for c in report["classes"] if c["class"] == "building")
        self.assertEqual(building_entry["feature_count"], 1)
        excluded_entry = next(c for c in report["classes"] if c["class"] == "excluded_landuse")
        self.assertEqual(excluded_entry["feature_count"], 1)
        self.assertEqual(report["ground_cover_omission_check"]["status"], "not_measured")

    def test_measured_drawn_set_is_compared_with_the_scene_source(self):
        drawn_set = {"schema_version": "aero-bench.city-ground-cover-drawn-set/v1", "status": "pass",
                     "geometry_id": self.scene["road_assets"]["displayed_surface_sha256"],
                     "parser_accepted_ids": ["osm:way:1:0"],
                     "drawable_ids": ["osm:way:1:0"], "fully_clipped_ids": [],
                     "drawn_ids": ["osm:way:1:0"], "omitted_ids": [], "unexpected_ids": [],
                     "omission_count": 0}
        report, _, _ = classify.classify_scene(self.scene_path, drawn_set)
        self.assertEqual(report["ground_cover_omission_check"]["status"], "measured-pass")
        self.assertEqual(report["ground_cover_omission_check"]["omission_count"], 0)

    def test_drawn_set_mismatch_is_not_reported_as_zero_omissions(self):
        drawn_set = {"schema_version": "aero-bench.city-ground-cover-drawn-set/v1", "status": "pass",
                     "geometry_id": self.scene["road_assets"]["displayed_surface_sha256"],
                     "parser_accepted_ids": ["osm:way:99:0"],
                     "drawable_ids": ["osm:way:99:0"], "fully_clipped_ids": [],
                     "drawn_ids": ["osm:way:99:0"], "omitted_ids": [], "unexpected_ids": [],
                     "omission_count": 0}
        with self.assertRaisesRegex(ValueError, "parser set differs"):
            classify.classify_scene(self.scene_path, drawn_set)

    def test_drawn_set_must_match_surface_and_partition_every_accepted_id(self):
        base = {"schema_version": "aero-bench.city-ground-cover-drawn-set/v1", "status": "pass",
                "geometry_id": self.scene["road_assets"]["displayed_surface_sha256"],
                "parser_accepted_ids": ["osm:way:1:0"], "drawable_ids": ["osm:way:1:0"],
                "fully_clipped_ids": [], "drawn_ids": ["osm:way:1:0"], "omitted_ids": [],
                "unexpected_ids": [], "omission_count": 0}
        with self.assertRaisesRegex(ValueError, "different displayed surface"):
            classify.classify_scene(self.scene_path, {**base, "geometry_id": "0" * 64})
        with self.assertRaisesRegex(ValueError, "do not partition"):
            classify.classify_scene(self.scene_path, {**base, "drawable_ids": [], "drawn_ids": []})

    def test_missing_referenced_file_raises(self):
        (self.public / "city-presentation/test-road-v3.json").unlink()
        with self.assertRaises(FileNotFoundError):
            classify.classify_scene(self.scene_path)

    def test_road_sha256_mismatch_raises(self):
        self._rewrite_scene(lambda scene: scene["road_assets"]["road"].__setitem__("sha256", "0" * 64))
        with self.assertRaises(ValueError):
            classify.classify_scene(self.scene_path)

    def test_environment_source_sha256_mismatch_raises(self):
        self._rewrite_scene(lambda scene: scene["environment_source"].__setitem__("sha256", "0" * 64))
        with self.assertRaises(ValueError):
            classify.classify_scene(self.scene_path)

    def test_mesh_pack_manifest_sha256_mismatch_raises(self):
        self._rewrite_scene(lambda scene: scene["mesh_pack"]["manifest"].__setitem__("sha256", "0" * 64))
        with self.assertRaises(ValueError):
            classify.classify_scene(self.scene_path)

    def test_mesh_pack_source_sha256_disagreement_raises(self):
        self._rewrite_scene(lambda scene: scene["road_assets"].__setitem__("mesh_pack_source_sha256", "0" * 64))
        with self.assertRaises(ValueError):
            classify.classify_scene(self.scene_path)

    def test_displayed_surface_sha256_disagreement_raises(self):
        self._rewrite_scene(lambda scene: scene["road_assets"].__setitem__("displayed_surface_sha256", "0" * 64))
        with self.assertRaises(ValueError):
            classify.classify_scene(self.scene_path)


# ---------------------------------------------------------------------------
# Real run on the published default scene
# ---------------------------------------------------------------------------

class DefaultSceneSmokeTest(unittest.TestCase):
    def test_default_scene_partitions_cleanly(self):
        report, classes, extent = classify.classify_scene(classify.DEFAULT_SCENE)
        self.assertTrue(report["partition_check"]["within_1e-6"], report["partition_check"])
        counts = {entry["class"]: entry["feature_count"] for entry in report["classes"]}
        for required in ("roadbed", "walkbed", "building", "green"):
            self.assertGreater(counts[required], 0, required)
        self.assertEqual(report["rendering_omission_check"]["status"], "not_measured")
        self.assertEqual(report["ground_cover_omission_check"]["status"], "not_measured")
        self.assertEqual(report["source_supported_ground_cover_candidates"]["counts_by_kind"],
                         {"brownfield": 2, "construction": 11, "parking": 4, "pitch": 2})


if __name__ == "__main__":
    unittest.main()
