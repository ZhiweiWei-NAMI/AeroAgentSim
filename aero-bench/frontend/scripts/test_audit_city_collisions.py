"""Collision audit must cover the boxes actually shown by the city viewer."""
import runpy
import unittest
from pathlib import Path

audit = runpy.run_path(str(Path(__file__).with_name("audit-city-collisions.py")))


class BuildingProxyTest(unittest.TestCase):
    def test_complete_envelope_replaces_all_decomposed_parts(self):
        envelope = dict(building_id="whole", x=0, z=0, width=10, depth=10,
                        rotation_deg=0, base_y=0, height=20)
        partial = dict(envelope, width=2, depth=2)
        fallback = dict(partial, building_id="partial", x=20)
        boxes = audit["rendered_building_proxies"]({
            "complete_footprint_envelopes": [envelope],
            "placements": [partial, dict(partial, x=2), fallback]})
        self.assertEqual(boxes, [envelope, fallback])
        from shapely.geometry import Point
        actual = audit["rectangle"](boxes[0]["x"], boxes[0]["z"],
                                    boxes[0]["width"], boxes[0]["depth"], 0)
        self.assertTrue(actual.covers(Point(-4, 4)))


if __name__ == "__main__":
    unittest.main()
