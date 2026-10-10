"""Source permissions and preserved authored classes are required before recording."""
import runpy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from city_native_route_capabilities import native_route_capabilities as count_capabilities

BUILDER=runpy.run_path(str(Path(__file__).with_name("build-sumo-city-preview.py")))


DEMO_COUNTS=BUILDER["demo_authored_vehicle_counts"]()
DEMO_REQUIREMENTS=BUILDER["DEMO_ROUTE_REQUIREMENTS"]

def native_route_capabilities(network, excluded):
    return count_capabilities(network, excluded, DEMO_COUNTS, DEMO_REQUIREMENTS)


def network_fixture():
    root=ET.fromstring('<net><edge id="ab" from="a" to="b"><lane id="ab_0" length="100"/><lane id="ab_1" length="100"/></edge><edge id="ba" from="b" to="a"><lane id="ba_0" length="100"/></edge></net>')
    for index in range(36):
        edge=ET.SubElement(root,"edge",{"id":f"walk{index}","from":f"p{index}","to":f"q{index}"})
        ET.SubElement(edge,"lane",{"id":f"walk{index}_0","allow":"pedestrian","length":"100"})
    return root


class NativeRouteCapabilitiesTest(unittest.TestCase):
    def test_actual_declared_classes_bidirection_multilane_and_walk_routes_pass(self):
        result=native_route_capabilities(network_fixture(),set())
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(result["bidirectional_motor_junction_pairs"],[["a","b"]])
        self.assertEqual(result["multiple_motor_lane_edge_ids"],["ab"])
        self.assertEqual(len(result["selected_dedicated_walk_edge_ids"]),36)
    def test_removed_vehicle_class_or_walk_route_is_blocked(self):
        root=network_fixture()
        for lane in root.findall('./edge/lane'):
            if lane.get("allow") != "pedestrian":lane.set("disallow","bus")
        self.assertEqual(native_route_capabilities(root,set())["status"],"BLOCKED")
        self.assertEqual(native_route_capabilities(network_fixture(),{"walk0"})["status"],"BLOCKED")
    def test_no_reverse_direction_or_no_multiple_lane_edge_is_blocked(self):
        root=network_fixture();root.remove(root.find("edge[@id='ba']"))
        self.assertEqual(native_route_capabilities(root,set())["status"],"BLOCKED")
        root=network_fixture();edge=root.find("edge[@id='ab']");edge.remove(edge.findall("lane")[1])
        self.assertEqual(native_route_capabilities(root,set())["status"],"BLOCKED")
    def test_edge_permission_is_not_connection_permission(self):
        root=ET.fromstring('<net><edge id="a"><lane id="a_0" allow="passenger bus"/></edge><edge id="b"><lane id="b_0" allow="passenger bus"/></edge><edge id=":i" function="internal"><lane id=":i_0" allow="passenger"/></edge><connection from="a" to="b" fromLane="0" toLane="0" via=":i_0"/></net>')
        self.assertTrue(BUILDER["route_allows"](root,("a","b"),"passenger"))
        self.assertFalse(BUILDER["route_allows"](root,("a","b"),"bus"))
    def test_generic_zero_class_demand_has_no_six_class_or_demo_street_gate(self):
        root=ET.fromstring('<net><edge id="one" from="a" to="b"><lane id="one_0" allow="passenger" length="10"/></edge></net>')
        requirements={"require_multiple_motor_lane_edge":False,"require_bidirectional_motor_roads":False,
                      "minimum_authored_person_routes":0}
        result=count_capabilities(root,set(),{"sedan":1,"bus":0,"bicycle":0},requirements)
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(set(result["classes"]),{"passenger"})
        self.assertEqual(count_capabilities(root,set(),{"sedan":1,"bus":1},requirements)["status"],"BLOCKED")
    def test_missing_connection_is_blocked(self):
        root=ET.fromstring('<net><edge id="a"><lane id="a_0"/></edge><edge id="b"><lane id="b_0"/></edge></net>')
        self.assertFalse(BUILDER["route_allows"](root,("a","b"),"passenger"))


if __name__ == "__main__":
    unittest.main()
