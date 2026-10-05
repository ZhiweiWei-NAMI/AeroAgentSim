"""Geometry tests for street lights placed at road-to-walkway curbs."""
import sys
import unittest
from pathlib import Path

from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parent))

from city_street_lamps import build_street_lamp_locations


class StreetLampPlacementTest(unittest.TestCase):
    def setUp(self):
        self.roadbed = box(0, 0, 120, 12)
        self.walkbed = box(0, 12.003, 120, 14.203)
        self.motor_core = box(1, 1, 119, 11)

    def build(self, **overrides):
        inputs = {
            "roadbed": self.roadbed,
            "walkbed": self.walkbed,
            "motor_core": self.motor_core,
            "crossing_cuts": [],
            "junctions": [],
            "building_footprints": [],
            "signal_points": [],
            "pedestrian_points": [],
        }
        inputs.update(overrides)
        return build_street_lamp_locations(**inputs)

    def test_overlapping_road_and_walk_surfaces_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            self.build(walkbed=box(0, 11.99, 120, 14.2))

    def test_surfaces_sharing_a_grid_boundary_are_not_an_overlap(self):
        self.assertTrue(self.build(walkbed=box(0, 12, 120, 14.2))["street_lamps"])

    def test_lamps_follow_continuous_curb_at_fixed_stations_and_face_the_road(self):
        layout = self.build()
        lamps = layout["street_lamps"]

        self.assertEqual(layout["stats"]["curb_boundary_count"], 1)
        self.assertEqual([lamp["source_boundary"]["station_m"] for lamp in lamps],
                         [8.0, 32.0, 56.0, 80.0, 104.0])
        self.assertEqual(len({lamp["source_boundary"]["id"] for lamp in lamps}), 1)
        for lamp in lamps:
            curb_x, curb_z = lamp["source_boundary"]["curb_point"]
            self.assertAlmostEqual(Point(lamp["x"], lamp["z"]).distance(Point(curb_x, curb_z)),
                                   0.6, places=3)
            self.assertAlmostEqual(lamp["z"], 12.603, places=3)
            self.assertAlmostEqual(lamp["rotation_deg"], -90.0, places=2)
            self.assertTrue(self.walkbed.covers(Point(lamp["x"], lamp["z"]).buffer(0.19,
                                                                                     quad_segs=16)))
            property_edge_clearance = self.walkbed.boundary.difference(self.roadbed.buffer(0.15))
            self.assertGreaterEqual(Point(lamp["x"], lamp["z"]).distance(property_edge_clearance)
                                    - 0.18, 1.2)

    def test_two_sides_face_their_adjacent_carriageway(self):
        walk = self.walkbed.union(box(0, -2.203, 120, -0.003))
        layout = self.build(walkbed=walk)

        self.assertEqual(layout["stats"]["curb_boundary_count"], 2)
        self.assertEqual(len(layout["street_lamps"]), 10)
        self.assertEqual({lamp["rotation_deg"] for lamp in layout["street_lamps"]},
                         {-90.0, 90.0})

    def test_crossing_and_crossing_end_wait_zone_omits_conflicting_station(self):
        crossing = box(31, 11.8, 33, 12.2)
        layout = self.build(crossing_cuts=[crossing])

        self.assertEqual(layout["stats"]["placed_count"], 4)
        omitted = [item for item in layout["omitted"]
                   if item["source_boundary"]["station_m"] == 32.0]
        self.assertEqual(len(omitted), 1)
        self.assertEqual(omitted[0]["reason"], "crossing_or_wait_zone")

    def test_junction_building_signal_and_pedestrian_clearances_are_explicit(self):
        cases = [
            ({"junctions": [box(31, 12.3, 33, 13)]}, "junction_clearance"),
            ({"building_footprints": [box(31, 13, 33, 15)]}, "building_clearance"),
            ({"signal_points": [(32, 12.603)]}, "signal_clearance"),
            ({"pedestrian_points": [Point(32, 12.603)]}, "pedestrian_clearance"),
        ]
        for values, reason in cases:
            with self.subTest(reason=reason):
                layout = self.build(**values)
                omitted = [item for item in layout["omitted"]
                           if item["source_boundary"]["station_m"] == 32.0]
                self.assertEqual(len(omitted), 1)
                self.assertEqual(omitted[0]["reason"], reason)
                self.assertEqual(layout["stats"]["placed_count"], 4)

    def test_motor_clearance_is_checked_for_the_whole_pole_footprint(self):
        layout = self.build(motor_core=box(1, 1, 119, 12.3))

        omitted = [item for item in layout["omitted"]
                   if item["source_boundary"]["station_m"] == 32.0]
        self.assertEqual(len(omitted), 1)
        self.assertEqual(omitted[0]["reason"], "motor_clearance")

    def test_pole_circle_must_fit_inside_walkbed(self):
        narrow = box(0, 12.003, 120, 12.72)
        layout = self.build(walkbed=narrow)

        self.assertEqual(layout["street_lamps"], [])
        self.assertEqual(layout["stats"]["placed_count"], 0)
        self.assertEqual(layout["stats"]["omitted_by_reason"],
                         {"pole_circle_outside_walkbed": 5})

    def test_lamp_keeps_a_clear_1_2m_pedestrian_strip_behind_the_fixture(self):
        narrow_walk = box(0, 12.003, 120, 13.503)
        layout = self.build(walkbed=narrow_walk)

        self.assertEqual(layout["street_lamps"], [])
        self.assertEqual(layout["stats"]["omitted_by_reason"],
                         {"minimum_clear_walk_width": 5})

    def test_no_shared_curb_means_zero_lamps_without_a_minimum_count_failure(self):
        separate_walk = box(200, 20, 210, 22)
        layout = self.build(walkbed=separate_walk)

        self.assertEqual(layout["street_lamps"], [])
        self.assertEqual(layout["stats"]["candidate_count"], 0)
        self.assertEqual(layout["stats"]["placed_count"], 0)

    def test_boundary_provenance_is_repeatable(self):
        first = self.build()["street_lamps"]
        second = self.build()["street_lamps"]

        self.assertEqual([lamp["source_boundary"] for lamp in first],
                         [lamp["source_boundary"] for lamp in second])


if __name__ == "__main__":
    unittest.main()
