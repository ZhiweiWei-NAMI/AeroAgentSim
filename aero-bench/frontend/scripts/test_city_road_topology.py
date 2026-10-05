"""Zebra markings come from OSM tags or controlled SUMO intersections."""

import unittest
import xml.etree.ElementTree as ET

from city_road_topology import (crossing_junction_id, lane_kind, motor_neighbors,
                                visible_crossing_junctions, zebra_crossing_nodes)


class LaneKindTest(unittest.TestCase):
    def test_permissions_select_the_kind_and_disallow_all_is_closed(self) -> None:
        kind = lambda **attributes: lane_kind(ET.Element("lane", **attributes))
        self.assertEqual(kind(), "motor")
        self.assertEqual(kind(disallow="pedestrian"), "motor")
        self.assertEqual(kind(allow="bicycle"), "cycle")
        self.assertEqual(kind(allow="pedestrian"), "walk")
        self.assertEqual(kind(disallow="all"), "closed")


class CrossingTopologyTest(unittest.TestCase):
    def test_only_explicit_zebra_markings_are_renderable(self) -> None:
        osm = ET.fromstring("""<osm>
          <node id="a"><tag k="crossing" v="zebra" /></node>
          <node id="b"><tag k="highway" v="crossing" /></node>
          <node id="c"><tag k="highway" v="crossing" /><tag k="crossing" v="unmarked" /></node>
          <node id="d"><tag k="crossing_ref" v="zebra" /></node>
          <node id="e"><tag k="crossing:markings" v="zebra" /></node>
        </osm>""")
        self.assertEqual(zebra_crossing_nodes(osm), {"a", "d", "e"})
        self.assertEqual(crossing_junction_id(":a_c0"), "a")

    def test_controlled_multiway_junctions_are_marked(self) -> None:
        network = ET.fromstring("""<net>
          <edge id="ab" from="a" to="b"><lane id="ab_0" /></edge>
          <edge id="bc" from="b" to="c"><lane id="bc_0" /></edge>
          <edge id="bd" from="b" to="d"><lane id="bd_0" /></edge>
          <edge id="ce" from="c" to="e"><lane id="ce_0" /></edge>
          <junction id="b" type="traffic_light" />
          <junction id="c" type="traffic_light" />
          <junction id="d" type="priority" />
        </net>""")
        self.assertEqual(motor_neighbors(network)["b"], {"a", "c", "d"})
        self.assertEqual(visible_crossing_junctions(network, ET.fromstring("<osm />")), {"b"})


if __name__ == "__main__":
    unittest.main()
