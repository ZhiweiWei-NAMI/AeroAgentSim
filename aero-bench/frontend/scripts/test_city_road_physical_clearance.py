"""Small authored geometry probes for the canonical native-road gates."""
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from shapely.geometry import LineString, Polygon

from city_preview_coordinates import CityEnuProjection
from city_road_physical_clearance import (internal_lane_neighbours, lane_building_conflicts, lane_ribbon,
                                          surface_lane_coverage, walkway_vehicle_overlaps)


class PhysicalRoadGateTest(unittest.TestCase):
    def test_clipped_surface_cannot_hide_native_lane_gap(self):
        lane = {"id": "42_0", "kind": "shared", "width": 3.2, "shape": [[0, 0], [10, 0]]}
        ribbon = LineString(lane["shape"]).buffer(1.6, cap_style=2)
        clipped = ribbon.difference(Polygon([[4, -1], [6, -1], [6, 1], [4, 1]]))
        result = surface_lane_coverage([lane], clipped, Polygon())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertGreater(result["rows"][0]["missing_area_m2"], 3.9)

    def test_pedestrian_motor_permission_cannot_replace_dedicated_walkbed(self):
        lane = {"id": "42_0", "kind": "walk", "width": 2, "shape": [[0, 0], [10, 0]]}
        road = LineString(lane["shape"]).buffer(1, cap_style=2)
        self.assertEqual(surface_lane_coverage([lane], road, Polygon())["status"], "BLOCKED")
        self.assertEqual(surface_lane_coverage([lane], Polygon(), road)["status"], "PASS")

    def test_generated_walking_area_is_not_a_normal_lane_ribbon(self):
        area = {"id": ":j_w0_0", "kind": "walk", "width": 3.2, "shape": [[0, 0], [1, 0], [0, 1]]}
        self.assertEqual(surface_lane_coverage([area], Polygon(), Polygon())["status"], "PASS")

    def test_nonzero_native_length_does_not_make_a_collapsed_connector_renderable(self):
        origin = {"latitude_deg": 0., "longitude_deg": 0., "ellipsoid_height_m": 0.,
                  "amsl_m": 0., "geoid_undulation_m": 0.}
        network = ET.fromstring('''<net><location netOffset="0,0" projParameter="+proj=aeqd +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs" />
           <edge id=":j_0" function="internal"><lane id=":j_0_0" length="0.10" shape="0,0 0,0" /></edge></net>''')
        lane = {"id": ":j_0_0", "kind": "motor", "width": 3.2, "shape": [[0, 0]]}
        result = lane_building_conflicts(network, ET.fromstring('<osm />'), [lane], [], CityEnuProjection(network, origin))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["unrenderable_native_lanes"][0]["native_declared_length_m"], .1)
        self.assertEqual(result["pair_count"], 0)

    def test_default_width_offset_is_reported_with_original_source_tags(self):
        origin = {"latitude_deg": 0., "longitude_deg": 0., "ellipsoid_height_m": 0.,
                  "amsl_m": 0., "geoid_undulation_m": 0.}
        network = ET.fromstring('''<net><location netOffset="0,0" projParameter="+proj=aeqd +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs" />
           <edge id="42" from="a" to="b"><lane id="42_0" /></edge></net>''')
        source = ET.fromstring('''<osm><node id="a" lon="0" lat="0" /><node id="b" lon="0.0001" lat="0" />
           <way id="42"><nd ref="a" /><nd ref="b" /><tag k="highway" v="service" /></way></osm>''')
        building = SimpleNamespace(object_id="building.1", rendered_polygon=Polygon([[3, -3], [5, -3], [5, -1], [3, -1]]))
        lane = {"id": "42_0", "kind": "shared", "width": 3.2, "shape": [[0, -2], [10, -2]]}
        before = ET.tostring(source)
        result = lane_building_conflicts(network, source, [lane], [building], CityEnuProjection(network, origin))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["rows"][0]["classification"], "native-offset-spine-enters-building")
        self.assertFalse(result["rows"][0]["source_width_is_declared"])
        self.assertEqual(result["rows"][0]["source_tags"], {"highway": "service"})
        self.assertEqual(ET.tostring(source), before)


class InternalLaneRibbonTest(unittest.TestCase):
    ENTRY = [[-10, 0], [0, 0]]
    INTERNAL = [[0, 0], [5, 1]]
    EXIT = [[5, 1], [15, 1]]

    def test_flat_cap_reaching_behind_the_entering_lane_end_is_removed(self):
        flat = LineString(self.INTERNAL).buffer(1.6, cap_style=2, join_style=2)
        self.assertLess(flat.bounds[0], -0.3)
        ribbon = lane_ribbon(self.INTERNAL, 3.2, entry_shape=self.ENTRY)
        self.assertGreaterEqual(ribbon.bounds[0], -1e-9)
        # Only the corner triangle behind the entry line is removed.
        removed = flat.difference(ribbon).area
        self.assertAlmostEqual(removed, 0.5 * 1.6 * 0.32, places=2)

    def test_exit_line_trims_the_far_cap_and_leaves_the_middle(self):
        ribbon = lane_ribbon(self.INTERNAL, 3.2, exit_shape=self.EXIT)
        flat = LineString(self.INTERNAL).buffer(1.6, cap_style=2, join_style=2)
        middle = Polygon([[1, -3], [4, -3], [4, 4], [1, 4]])
        self.assertAlmostEqual(ribbon.intersection(middle).area, flat.intersection(middle).area)
        self.assertLess(ribbon.area, flat.area)

    def test_coverage_checks_the_aligned_internal_ribbon_and_still_blocks_gaps(self):
        lane = {"id": ":j_0_0", "function": "internal", "kind": "motor", "width": 3.2,
                "shape": self.INTERNAL, "entry_shape": self.ENTRY, "exit_shape": None}
        paved = lane_ribbon(self.INTERNAL, 3.2, entry_shape=self.ENTRY)
        self.assertEqual(surface_lane_coverage([lane], paved, Polygon())["status"], "PASS")
        self.assertEqual(surface_lane_coverage([{**lane, "entry_shape": None}], paved, Polygon())["status"], "BLOCKED")
        gap = paved.difference(Polygon([[2, -3], [3, -3], [3, 4], [2, 4]]))
        self.assertEqual(surface_lane_coverage([lane], gap, Polygon())["status"], "BLOCKED")

    def test_neighbours_follow_via_and_internal_connections(self):
        network = ET.fromstring("""<net>
            <connection from="a" to="b" fromLane="0" toLane="1" via=":j_0_0"/>
            <connection from=":j_0" to="b" fromLane="0" toLane="1"/>
            <connection from="a" to="c" fromLane="0" toLane="0"/></net>""")
        self.assertEqual(internal_lane_neighbours(network), {":j_0_0": ("a_0", "b_1")})


class WalkwayVehicleOverlapTest(unittest.TestCase):
    @staticmethod
    def network(walk_y: float) -> ET.Element:
        return ET.fromstring(f"""<net>
            <edge id="r"><lane id="r_0" index="0" allow="passenger" width="3.2" shape="0,0 20,0"/></edge>
            <edge id="s"><lane id="s_0" index="0" allow="pedestrian" width="2" shape="0,{walk_y} 20,{walk_y}"/></edge>
        </net>""")

    def test_sidewalk_on_a_carriageway_is_reported_with_its_edge(self):
        records = walkway_vehicle_overlaps(self.network(2.0))
        self.assertEqual([(item["lane_id"], item["edge_id"]) for item in records], [("s_0", "s")])
        self.assertAlmostEqual(records[0]["vehicle_lanes"][0]["overlap_m2"], (20 - 0.003) * (0.6 - 0.0015), places=3)  # walk eroded by half a seam

    def test_abutting_sidewalk_within_the_seam_is_not_an_overlap(self):
        self.assertEqual(walkway_vehicle_overlaps(self.network(2.6 - 0.0014)), [])
        self.assertEqual(len(walkway_vehicle_overlaps(self.network(2.6 - 0.002))), 1)


if __name__ == "__main__":
    unittest.main()
