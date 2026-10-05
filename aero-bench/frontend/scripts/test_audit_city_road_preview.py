"""Independent rejection tests for the current road presentation contract."""
import importlib.util
from pathlib import Path
import unittest
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))

spec = importlib.util.spec_from_file_location("road_audit", Path(__file__).with_name("audit-city-road-preview.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def polygon(x0, z0, x1, z1):
    return {"outline": [[x0,z0],[x1,z0],[x1,z1],[x0,z1]], "holes": []}


def fixture():
    road = {"schema_version": "aero-bench.city-road-preview/v2",
            "roadbed": [polygon(0,0,20,10)], "concrete_roadbed": [polygon(0,0,10,10)],
            "concrete_roadbed_area_m2": 100,
            "walkbed": [polygon(0,10,20,12)],
            "street_layout": {"source_kind": "sumo-topology-with-explicit-derived-urban-design",
                "road_height_m": .075, "sidewalk_height_m": .225, "derived_walkbed": [],
                "median_beds": [], "markings": [], "arrows": [], "pavement_edges": [[[0,10],[20,10],[20,12],[0,12],[0,10]]]},
            "road_surface_materials": {"concrete": {"texture": "concrete.jpg", "rendered_area_m2": 100,
                "texture_asset": {"url": "/osm2world/packs/test/assets/" + "a" * 64,
                                  "sha256": "a" * 64, "size_bytes": 32}}},
            "marking_widths_m": {"lane_edge": .14, "lane_divider": .13, "derived_direction_guide": .15},
            "direction_guides": [{"id": "guide:1", "source_way_id": "w1", "edge_ids": ["1", "-1"],
                "marking": "derived_direction_guide", "source_kind": "sumo-lane-topology-not-surveyed-marking",
                "width_m": .15, "shape": [[1,5],[19,5]]}]}
    road["street_layout"]["sidewalk_provenance"] = {
        "source_kind": "derived_osm_eligible_corridor_visual_geometry",
        "sumo_lane_topology_modified": False, "surface_precision_normalized": True,
        "sidewalk_kind": "derived_paved_sidewalk_not_surveyed",
        "median_kind": "derived_paved_narrow_enclosed_strip_not_surveyed",
        "crossing_cuts_affect": "curb_lines_only"}
    road["street_layout"]["sidewalk_stats"] = dict.fromkeys((
        "derived_walkbed_area_m2", "median_beds_area_m2", "sidewalk_width_m",
        "building_clearance_m", "vehicle_clearance_m", "precision_grid_m",
        "effective_building_clearance_m", "effective_vehicle_clearance_m", "raw_roadbed_area_m2",
        "roadbed_normalization_symmetric_difference_m2", "precision_clearance_margin_m",
        "road_walk_separation_m", "normalized_roadbed_area_m2", "normalized_source_walkbed_area_m2"), 0)
    road["street_layout"]["sidewalk_stats"].update({"precision_grid_m": .001,
        "precision_clearance_margin_m": .003, "road_walk_separation_m": .003,
        "effective_building_clearance_m": .003, "effective_vehicle_clearance_m": .003})
    network = ET.fromstring('<net><edge id="1" from="a" to="b"><lane id="1_0" width="3.2"/></edge>'
                            '<edge id="-1" from="b" to="a"><lane id="-1_0" width="3.2"/></edge></net>')
    osm = ET.fromstring('<osm><way id="1"><tag k="highway" v="tertiary"/></way></osm>')
    pack = {"batches": [{"layer": "roads", "material": {"base_color_texture": "concrete.jpg"}}]}
    pack["textures"] = {"concrete.jpg": {"sha256": "a" * 64, "size_bytes": 32}}
    return road, network, osm, pack, "/osm2world/packs/test/"


class RoadAuditTests(unittest.TestCase):
    def test_accepts_current_derived_geometry(self):
        module.audit_v2_surfaces(*fixture())

    def test_rejects_texture_substitution_digest_size_and_pack_root(self):
        for key, value in (("sha256", "b" * 64), ("size_bytes", 33),
                           ("url", "/osm2world/packs/other/assets/" + "a" * 64)):
            road, *rest = fixture()
            road["road_surface_materials"]["concrete"]["texture_asset"][key] = value
            with self.assertRaisesRegex(ValueError, "verified mesh-pack"):
                module.audit_v2_surfaces(road, *rest)

    def test_rejects_walkway_or_arrow_over_motor_boundary(self):
        for field, value in (("derived_walkbed", [polygon(0,0,1,1)]),
                             ("arrows", [{"id": "bad", "outline": [[40,0],[42,0],[40,3]]}])):
            road, *rest = fixture()
            road["street_layout"][field] = value
            with self.assertRaises(ValueError): module.audit_v2_surfaces(road, *rest)

    def test_rejects_floating_pavement_wall(self):
        road, *rest = fixture()
        road["street_layout"]["pavement_edges"] = [[[0, 10], [20, 10]]]
        module.audit_v2_surfaces(road, *rest)
        road["street_layout"]["pavement_edges"] = []
        with self.assertRaisesRegex(ValueError, "pavement edges"):
            module.audit_v2_surfaces(road, *rest)
        road["street_layout"]["pavement_edges"] = [[[0, 9], [20, 9]]]
        with self.assertRaisesRegex(ValueError, "Pavement wall"):
            module.audit_v2_surfaces(road, *rest)

    def test_rejects_old_contract(self):
        road, *rest = fixture()
        road["schema_version"] = "aero-bench.city-road-preview/v1"
        with self.assertRaisesRegex(ValueError, "v2"):
            module.audit_v2_surfaces(road, *rest)

    def test_rejects_concrete_outside_road_and_wrong_source_material(self):
        for key in ("geometry", "material"):
            road, *rest = fixture()
            if key == "geometry": road["concrete_roadbed"] = [polygon(15,0,25,10)]
            else: road["road_surface_materials"]["concrete"]["texture"] = "invented.jpg"
            with self.assertRaises(ValueError): module.audit_v2_surfaces(road, *rest)

    def test_rejects_wrong_direction_and_unmarked_service_road(self):
        road, network, osm, pack, base = fixture()
        network.findall("edge")[1].set("from", "c")
        with self.assertRaisesRegex(ValueError, "non-opposing"):
            module.audit_v2_surfaces(road, network, osm, pack, base)
        road, network, osm, pack, base = fixture()
        osm.find("way/tag").set("v", "service")
        with self.assertRaisesRegex(ValueError, "ineligible"):
            module.audit_v2_surfaces(road, network, osm, pack, base)

    def test_rejects_outside_guide_and_unearned_solid_marking(self):
        for key in ("geometry", "marking"):
            road, *rest = fixture()
            if key == "geometry": road["direction_guides"][0]["shape"] = [[0,-3],[20,-3]]
            else: road["direction_guides"][0]["marking"] = "single_yellow_solid"
            with self.assertRaises(ValueError): module.audit_v2_surfaces(road, *rest)


if __name__ == "__main__": unittest.main()
