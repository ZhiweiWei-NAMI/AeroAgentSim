"""Module gates for source geometry and native vegetation texture extraction."""
import hashlib
import io
import json
import unittest
from pathlib import Path

from PIL import Image

from city_environment_assets import decode_rgba_tiff
from city_environment_source import canonical_bytes, extract, ground_cover_kind, _rings


def sources(tags=None):
    nodes = [{"type": "node", "id": i + 1, "lat": lat, "lon": lon}
             for i, (lon, lat) in enumerate([(121, 31), (121.001, 31), (121.001, 31.001), (121, 31.001)])]
    osm = {"elements": nodes + [{"type": "way", "id": -900719925474099300,
                                "nodes": [1, 2, 3, 4, 1], "tags": tags or {"landuse": "grass"}}]}
    objects = {"schema_version": "aero-bench.urban-scene-objects/v1", "coordinate_frame": "ENU",
               "origin": {"frame": "WGS84->ECEF->ENU", "latitude_deg": 31, "longitude_deg": 121, "ellipsoid_height_m": 50},
               "buildings": [{"object_id": "building.way.1.component.0", "effective_geometry_sha256": "b" * 64,
                              "footprint_enu_m": [[2, 3], [4, 3], [4, 6], [2, 6], [2, 3]]}]}
    return osm, objects


def run(osm, objects):
    return extract(json.dumps(osm).encode(), json.dumps(objects).encode())


class SourceExtraction(unittest.TestCase):
    def test_repeats_and_binds_byte_identity_without_number_id_loss(self):
        osm, objects = sources()
        result = run(osm, objects)
        self.assertEqual(result, run(osm, objects))
        self.assertEqual(result["source"]["osmSha256"], hashlib.sha256(json.dumps(osm).encode()).hexdigest())
        self.assertEqual(result["greens"][0]["provenance"]["elementId"], "-900719925474099300")
        self.assertEqual(result["buildings"][0]["outline"], [[2, -3], [4, -3], [4, -6], [2, -6], [2, -3]])
        self.assertEqual(result["sourceTrees"], [])

    def test_uses_compiler_enu_at_arbitrary_origin(self):
        osm, objects = sources()
        ring = run(osm, objects)["greens"][0]["outline"]
        self.assertAlmostEqual(ring[0][0], 0)
        self.assertAlmostEqual(ring[0][1], 0)
        self.assertGreater(ring[1][0], 90)
        self.assertLess(ring[2][1], -100)
        osm, objects = sources()
        objects["origin"]["longitude_deg"] = -73
        for node in osm["elements"]:
            if node["type"] == "node":
                node["lon"] -= 194
        shifted = run(osm, objects)["greens"][0]["outline"]
        self.assertAlmostEqual(shifted[1][0], ring[1][0], places=5)
        self.assertAlmostEqual(shifted[2][1], ring[2][1], places=5)

    def test_preserves_source_tree_nodes_and_rejects_non_green_landuse(self):
        osm, objects = sources({"landuse": "construction", "aero_bench:geometry_kind": "polygon"})
        osm["elements"][0]["tags"] = {"natural": "tree"}
        result = run(osm, objects)
        self.assertEqual(result["greens"], [])
        self.assertEqual([cover["kind"] for cover in result["groundCovers"]], ["construction"])
        self.assertEqual(result["inspection"]["excludedTags"], {"landuse=construction": 1})
        self.assertEqual(result["sourceTrees"][0]["sourceElementId"], "1")

    def test_ground_cover_rules_distinguish_surfaces_without_treating_zoning_as_surface(self):
        cases = {
            "pitch": ({"leisure": "pitch"}, "pitch"),
            "construction": ({"landuse": "construction"}, "construction"),
            "brownfield": ({"landuse": "brownfield"}, "brownfield"),
            "parking": ({"amenity": "parking"}, "parking"),
            "pedestrian_area": ({"highway": "pedestrian"}, "pedestrian_area"),
            "square": ({"place": "square"}, "square"),
            "water": ({"natural": "water"}, "water"),
            "explicit_surface": ({"surface": "compacted"}, "explicit_surface"),
        }
        for name, (tags, expected) in cases.items():
            with self.subTest(name=name):
                osm, objects = sources({**tags, "aero_bench:geometry_kind": "polygon"})
                cover = run(osm, objects)["groundCovers"]
                self.assertEqual(len(cover), 1)
                self.assertEqual(cover[0]["kind"], expected)
                self.assertEqual(cover[0]["surface"], tags.get("surface"))
                self.assertEqual(cover[0]["provenance"]["tags"], {**tags, "aero_bench:geometry_kind": "polygon"})
        for tags in ({"landuse": "residential"}, {"landuse": "commercial"}, {"landuse": "retail"},
                     {"building": "yes", "surface": "concrete"}):
            with self.subTest(tags=tags):
                self.assertIsNone(ground_cover_kind(tags))
                osm, objects = sources({**tags, "aero_bench:geometry_kind": "polygon"})
                self.assertEqual(run(osm, objects)["groundCovers"], [])

    def test_reads_relation_holes_and_deduplicates_member_ways(self):
        osm, objects = sources()
        osm["elements"] += [{"type": "node", "id": i, "lat": lat, "lon": lon}
                            for i, lon, lat in [(5, 121.0002, 31.0002), (6, 121.0008, 31.0002),
                                               (7, 121.0008, 31.0008), (8, 121.0002, 31.0008)]]
        osm["elements"] += [{"type": "way", "id": 2, "nodes": [5, 6, 7]},
                            {"type": "way", "id": 3, "nodes": [7, 8, 5]},
                            {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "leisure": "park"},
                             "members": [{"type": "way", "ref": -900719925474099300, "role": "outer"},
                                         {"type": "way", "ref": 2, "role": "inner"}, {"type": "way", "ref": 3, "role": "inner"}]}]
        result = run(osm, objects)
        self.assertEqual(len(result["greens"]), 1)
        self.assertEqual(len(result["greens"][0]["holes"]), 1)
        self.assertEqual(result["greens"][0]["provenance"]["elementType"], "relation")

    def test_ground_cover_relation_keeps_hole_and_source_ids(self):
        osm, objects = sources({"landuse": "construction", "aero_bench:geometry_kind": "polygon"})
        osm["elements"] += [{"type": "node", "id": i, "lat": lat, "lon": lon}
                            for i, lon, lat in [(5, 121.0002, 31.0002), (6, 121.0008, 31.0002),
                                               (7, 121.0008, 31.0008), (8, 121.0002, 31.0008)]]
        osm["elements"] += [{"type": "way", "id": 2, "nodes": [5, 6, 7]},
                            {"type": "way", "id": 3, "nodes": [7, 8, 5]},
                            {"type": "relation", "id": 10,
                             "tags": {"type": "multipolygon", "landuse": "brownfield",
                                      "aero_bench:geometry_kind": "multipolygon",
                                      "aero_bench:source_type": "relation", "aero_bench:source_id": "original.10"},
                             "members": [{"type": "way", "ref": -900719925474099300, "role": "outer"},
                                         {"type": "way", "ref": 2, "role": "inner"},
                                         {"type": "way", "ref": 3, "role": "inner"}]}]
        result = run(osm, objects)
        covers = result["groundCovers"]
        self.assertEqual(len(covers), 1)
        self.assertEqual(covers[0]["id"], "osm:relation:10:0")
        self.assertEqual(covers[0]["kind"], "brownfield")
        self.assertEqual(len(covers[0]["holes"]), 1)
        self.assertEqual(covers[0]["provenance"]["sourceElementId"], "original.10")

    def test_exposes_missing_node_and_open_source_rings(self):
        osm, objects = sources()
        osm["elements"][-1]["nodes"][1] = 999
        with self.assertRaises(KeyError):
            run(osm, objects)
        with self.assertRaisesRegex(ValueError, "open or ambiguous"):
            _rings([[1, 2, 3]])

    def test_keeps_derived_source_identity(self):
        osm, objects = sources({"leisure": "garden", "aero_bench:source_type": "relation", "aero_bench:source_id": "987"})
        provenance = run(osm, objects)["greens"][0]["provenance"]
        self.assertEqual(provenance["sourceElementType"], "relation")
        self.assertEqual(provenance["sourceElementId"], "987")

    def test_default_candidate_preserves_published_greens_byte_for_byte(self):
        root = Path(__file__).resolve().parents[2]
        scene = json.loads((root / "frontend/public/city-presentation/default-scene-v1.json").read_text())
        pack = root / "frontend/public" / scene["mesh_pack"]["base_url"].lstrip("/")
        manifest = json.loads((pack / "manifest.json").read_text())
        osm_bytes = (pack / "assets" / manifest["source"]["sha256"]).read_bytes()
        objects_bytes = (root / "validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json").read_bytes()
        baseline = json.loads((root / "frontend/public/city-presentation/shanghai-source-environment-v1.json").read_text())
        candidate = extract(osm_bytes, objects_bytes)
        self.assertEqual(canonical_bytes(candidate["greens"]), canonical_bytes(baseline["greens"]))
        self.assertEqual(candidate["inspection"]["groundCoverCountsByKind"],
                         {"brownfield": 2, "construction": 11, "parking": 4, "pitch": 2})


class NativeAlphaExtraction(unittest.TestCase):
    def test_retains_each_original_alpha_value(self):
        image = Image.new("RGBA", (2, 2))
        pixels = [(10, 20, 30, 0), (40, 50, 60, 255), (70, 80, 90, 128), (100, 110, 120, 9)]
        image.putdata(pixels)
        buffer = io.BytesIO()
        image.save(buffer, format="TIFF", compression="raw")
        result = decode_rgba_tiff(buffer.getvalue())
        self.assertEqual([result.getpixel((x, y)) for y in range(2) for x in range(2)], pixels)

    def test_rejects_rgb_previews_and_source_channel_drift(self):
        image = Image.new("RGB", (2, 2))
        buffer = io.BytesIO()
        image.save(buffer, format="TIFF", compression="raw")
        with self.assertRaisesRegex(ValueError, "channel layout"):
            decode_rgba_tiff(buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
