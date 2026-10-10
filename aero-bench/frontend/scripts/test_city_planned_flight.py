"""Focused checks for the shared planned visual flight loop."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_planned_flight import planned_flight_loop

EXTENT = {"west": -600.0, "east": 600.0, "south": -600.0, "north": 600.0}


class PlannedFlightLoopTest(unittest.TestCase):
    def test_loop_clears_the_highest_roof_and_stays_inside_the_extent(self):
        buildings = [(0.0, 0.0, 30.0), (20.0, 10.0, 92.5), (400.0, 400.0, 10.0)]
        frames, audit = planned_flight_loop(buildings, EXTENT)
        self.assertEqual(len(frames), 601)
        self.assertEqual(audit["highest_building_m"], 92.5)
        self.assertGreaterEqual(audit["minimum_building_clearance_m"], 20)
        self.assertGreaterEqual(audit["minimum_aircraft_vertical_separation_m"], 10)
        for frame in frames:
            for _, x, z, up, *_ in frame:
                self.assertTrue(EXTENT["west"] < x < EXTENT["east"] and EXTENT["south"] < -z < EXTENT["north"])
                self.assertGreater(up, 92.5 + 20)
        self.assertLessEqual(max(audit["max_speed_mps"].values()), 15)

    def test_dense_center_is_clamped_to_the_extent_feasible_interval(self):
        extent = {
            "west": -239.78511872292995,
            "east": 209.81182863890092,
            "south": -240.96394031796186,
            "north": 130.52291624723892,
        }
        frames, audit = planned_flight_loop([(105.203347, 123.082143, 12.0)], extent)
        self.assertEqual(len(frames), 601)
        self.assertEqual(audit["visual_district_center_xz"], [105.203347, 123.082143])
        for frame in frames:
            for _, x, z, *_ in frame:
                self.assertGreater(x, extent["west"] + 10)
                self.assertLess(x, extent["east"] - 10)
                self.assertGreater(-z, extent["south"] + 10)
                self.assertLess(-z, extent["north"] - 10)

    def test_missing_buildings_and_cramped_extent_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "no building geometry"):
            planned_flight_loop([], EXTENT)
        with self.assertRaisesRegex(ValueError, "lacks room"):
            planned_flight_loop([(0.0, 0.0, 10.0)], {"west": -80.0, "east": 80.0, "south": -80.0, "north": 80.0})


if __name__ == "__main__":
    unittest.main()
