"""Reject crossing rebuilds that lose paths or publish unusable signal plans."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from city_crossing_groups import CrossingReplacement

spec = importlib.util.spec_from_file_location("complete_crossings", Path(__file__).with_name("complete-ground-city-crossings.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    before = ET.fromstring('''<net><edge id=":j_c0" function="crossing" crossingEdges="bus">
      <lane length="3.2"/></edge></net>''')
    after = ET.fromstring('''<net>
      <edge id="bus"/><edge id="main"/><edge id="out"/>
      <edge id=":j_w0" function="walkingarea"/><edge id=":j_w1" function="walkingarea"/>
      <edge id=":j_c0" function="crossing" crossingEdges="bus main"><lane length="16"/></edge>
      <tlLogic id="j"><phase state="Gr"/><phase state="rG"/></tlLogic>
      <connection from="main" to="out" tl="j" linkIndex="0"/>
      <connection from=":j_w0" to=":j_c0" tl="j" linkIndex="1"/>
      <connection from=":j_c0" to=":j_w1"/>
    </net>''')
    return before, after, [CrossingReplacement("j", ("bus",), ("bus", "main"), 4)]


class CrossingGateTest(unittest.TestCase):
    def test_connected_full_crossing_has_usable_signal(self):
        checks = module.validate_crossings(*fixture())
        self.assertEqual(checks[0]["after_length_m"], 16)

    def test_netconvert_success_cannot_hide_a_discarded_crossing(self):
        before, after, changes = fixture()
        after.remove(after.find("edge[@id=':j_c0']"))
        with self.assertRaisesRegex(ValueError, 'changed unexpectedly'):
            module.validate_crossings(before, after, changes)

    def test_all_red_pedestrian_plan_is_rejected(self):
        before, after, changes = fixture()
        for phase in after.findall("tlLogic/phase"):
            phase.set("state", "Gr")
        with self.assertRaisesRegex(ValueError, 'never receives green'):
            module.validate_crossings(before, after, changes)

    def test_conflicting_protected_green_is_rejected(self):
        before, after, changes = fixture()
        after.find("tlLogic/phase").set("state", "GG")
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            module.validate_crossings(before, after, changes)

    def test_protected_green_entering_crossed_edge_is_rejected(self):
        before, after, changes = fixture()
        vehicle = after.find("connection[@from='main']")
        vehicle.set("from", "out")
        vehicle.set("to", "main")
        after.find("tlLogic/phase").set("state", "GG")
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            module.validate_crossings(before, after, changes)


if __name__ == "__main__":
    unittest.main()
