import unittest
import xml.etree.ElementTree as ET

from city_ground_junction_completion import (collapsed_passthrough_pads, consumed_crop_remnants, crop_rectangle,
                                             on_crop_boundary, road_end_completion, turnarounds_over_walkways)

ORIGIN = {"latitude_deg": 31.2288, "longitude_deg": 121.481, "ellipsoid_height_m": 50.0}
CROP = {"min_east_m": -500.0, "max_east_m": 500.0, "min_north_m": -500.0, "max_north_m": 500.0}


class Projection:
    """Native x = east and y = north; the viewer frame is (east, -north)."""
    def point(self, x, y):
        return round(x, 2), round(-y, 2)

    def shape(self, value):
        result = []
        for token in value.split():
            point = list(self.point(*map(float, token.split(","))))
            if not result or point != result[-1]:
                result.append(point)
        return result


def network(junctions, edges, connections=()):
    root = ET.Element("net")
    for junction_id, x, y in junctions:
        ET.SubElement(root, "junction", id=junction_id, x=str(x), y=str(y), type="priority")
    for edge_id, start, end, lanes in edges:
        edge = ET.SubElement(root, "edge", id=edge_id, **{"from": start, "to": end})
        for index, (width, allow, shape, *length) in enumerate(lanes):
            ET.SubElement(edge, "lane", id=f"{edge_id}_{index}", index=str(index), width=str(width), allow=allow,
                          shape=shape, length=str(length[0] if length else 10))
    for attributes in connections:
        ET.SubElement(root, "connection", **attributes)
    return root


def designed(edge_id, *allowed, shape="0,0 1,0"):
    return {"edge_id": edge_id, "lanes": [{"lane_index": str(index), "allowed": list(permitted), "shape": shape}
                                          for index, permitted in enumerate(allowed)]}


class CropTest(unittest.TestCase):
    def test_reads_the_declared_rectangle_and_rejects_another_origin(self):
        scene = {"schema_version": "aero-bench.urban-scene-common/v1", "origin": dict(ORIGIN),
                 "crop": {"enu_bounds_m": {"min_east_m": -500, "max_east_m": 500, "min_north_m": -500, "max_north_m": 500}}}
        self.assertEqual(crop_rectangle(scene, ORIGIN), CROP)
        with self.assertRaisesRegex(ValueError, "origins differ"):
            crop_rectangle({**scene, "origin": {**ORIGIN, "latitude_deg": 31.0}}, ORIGIN)
        with self.assertRaisesRegex(ValueError, "finite rectangle"):
            crop_rectangle({**scene, "crop": {"enu_bounds_m": {**CROP, "max_east_m": -500}}}, ORIGIN)

    def test_boundary_is_the_rectangle_outline_only(self):
        self.assertTrue(on_crop_boundary(500.0, 12.0, CROP))
        self.assertTrue(on_crop_boundary(-3.0, -499.96, CROP))
        self.assertFalse(on_crop_boundary(499.9, 12.0, CROP))
        self.assertFalse(on_crop_boundary(510.0, 500.0, CROP))


class RoadEndTest(unittest.TestCase):
    walk_and_drive = (["pedestrian"], ["passenger"])

    def road(self, end_x, *extra_edges):
        """Road 7 from junction `in` (also joined by road 8) to the end node `end`."""
        return network([("end", end_x, 20), ("in", 0, 20), ("far", 0, 90), ("x", 40, 60)],
                       [("-7", "in", "end", []), ("--7", "end", "in", []), ("-8", "in", "far", []), *extra_edges])

    def plan(self, reverse=walk_and_drive):
        return [designed("-7", *self.walk_and_drive), designed("--7", *reverse), designed("-8", ["passenger"])]

    def test_crop_cut_road_end_becomes_an_outer_fringe_node(self):
        nodes, crossings, records = road_end_completion(self.road(500), self.plan(), Projection(), CROP)
        self.assertEqual([(node.get("id"), node.get("fringe")) for node in nodes], [("end", "outer")])
        self.assertEqual(len(crossings), 0)
        self.assertEqual(records[0]["rule"], "crop-cut-road-end")
        self.assertEqual(records[0]["enu_m"], [500, 20])

    def test_interior_road_end_with_two_sidewalks_gets_an_unmarked_crossing(self):
        nodes, crossings, records = road_end_completion(self.road(40), self.plan(), Projection(), CROP)
        self.assertEqual(len(nodes), 0)
        self.assertEqual([(c.get("node"), c.get("edges"), c.get("priority")) for c in crossings],
                         [("end", "--7 -7", "false")])
        self.assertEqual(records[0]["rule"], "interior-road-end-crossing")

    def test_no_rule_for_a_one_sided_sidewalk_or_a_real_junction(self):
        self.assertEqual(road_end_completion(self.road(40), self.plan(reverse=(["passenger"],)), Projection(), CROP)[2], [])
        # Two different roads meeting on the crop line are a junction, not a cut road end.
        junction = network([("cut", 500, 20), ("a", 450, 20), ("b", 450, 40)], [("-7", "a", "cut", []), ("-8", "cut", "b", [])])
        self.assertEqual(road_end_completion(junction, [designed("-7", ["passenger"]), designed("-8", ["passenger"])],
                                             Projection(), CROP)[2], [])

    def test_undesigned_edges_do_not_count_towards_a_road_end(self):
        # Road 9 is excluded from the design, so `end` is still the end of road 7 only.
        records = road_end_completion(self.road(40, ("-9", "end", "x", [])), self.plan(), Projection(), CROP)[2]
        self.assertEqual([row["junction_id"] for row in records], ["end"])


class PassThroughTest(unittest.TestCase):
    def kinked(self, internal_shape):
        return network([("a", 0, 0), ("k", 10, 0), ("b", 20, 1)],
                       [("-1", "a", "k", [(3.2, "passenger", "0,0 10,0")]),
                        ("-2", "k", "b", [(3.2, "passenger", "10,0 20,1")]),
                        (":k_0", "", "", [(3.2, "passenger", internal_shape, 0.1)])],
                       [{"from": "-1", "to": "-2", "fromLane": "0", "toLane": "0", "via": ":k_0_0", "dir": "s"}])

    def test_collapsed_straight_pass_through_gets_a_lane_width_pad(self):
        pads, records = collapsed_passthrough_pads(self.kinked("10,0 10,0"), Projection())
        self.assertEqual(len(pads), 1)
        corners = [tuple(map(float, token.split(","))) for token in pads[0].get("shape").split()]
        self.assertEqual(len(corners), 4)
        for x, y in corners:
            self.assertLessEqual(abs(x - 10), 1.6 * 2 ** .5 + 1e-9)
            self.assertLessEqual(abs(y), 1.6 * 2 ** .5 + 1e-9)
        self.assertEqual((records[0]["pad_along_m"], records[0]["pad_across_m"]), (3.2, 3.2))
        self.assertAlmostEqual(records[0]["heading_change_deg"], 5.711, places=3)

    def test_multi_lane_pass_through_gets_one_pad_spanning_the_carriageway(self):
        net = network([("a", 0, 0), ("k", 10, 0), ("b", 20, 0)],
                      [("-1", "a", "k", [(3.2, "passenger", "0,1.6 10,1.6"), (3.2, "passenger", "0,-1.6 10,-1.6")]),
                       ("-2", "k", "b", [(3.2, "passenger", "10,1.6 20,1.6"), (3.2, "passenger", "10,-1.6 20,-1.6")]),
                       (":k_0", "", "", [(3.2, "passenger", "10,1.6 10,1.6", 0.1), (3.2, "passenger", "10,-1.6 10,-1.6", 0.1)])],
                      [{"from": "-1", "to": "-2", "fromLane": str(i), "toLane": str(i), "via": f":k_0_{i}", "dir": "s"}
                       for i in (0, 1)])
        pads, records = collapsed_passthrough_pads(net, Projection())
        self.assertEqual(len(pads), 1)
        corners = sorted(tuple(map(float, token.split(","))) for token in pads[0].get("shape").split())
        self.assertEqual(corners, [(8.4, -3.2), (8.4, 3.2), (11.6, -3.2), (11.6, 3.2)])
        self.assertEqual(records[0]["internal_lanes"], [":k_0_0", ":k_0_1"])
        self.assertEqual((records[0]["pad_along_m"], records[0]["pad_across_m"]), (3.2, 6.4))

    def test_drawable_internal_lane_needs_no_pad(self):
        self.assertEqual(collapsed_passthrough_pads(self.kinked("9,0 11,0.1"), Projection()), ([], []))

    def test_reciprocal_straight_movements_share_the_measured_lane_end_pad(self):
        net = network([("a", 0, 0), ("k", 10, 0), ("b", 20, 0)],
                      [("-1", "a", "k", [(3.2, "passenger", "0,1.6 10,1.6"),
                                            (3.2, "passenger", "0,4.8 10,4.8")]),
                       ("-2", "k", "b", [(3.2, "passenger", "10,1.6 20,1.6"),
                                            (3.2, "passenger", "10,4.8 20,4.8")]),
                       ("--2", "b", "k", [(3.2, "bus bicycle", "20,-1.6 10,-1.6")]),
                       ("--1", "k", "a", [(3.2, "bus bicycle", "10,-1.6 0,-1.6")]),
                       (":k_0", "", "", [(3.2, "passenger", "10,1.6 10,1.6", 0.1),
                                             (3.2, "passenger", "10,4.8 10,4.8", 0.1)]),
                       (":k_2", "", "", [(3.2, "bus bicycle", "10,-1.6 10,-1.6", 0.1)])],
                      [{"from": "-1", "to": "-2", "fromLane": str(index), "toLane": str(index),
                        "via": f":k_0_{index}", "dir": "s"} for index in (0, 1)] +
                      [{"from": "--2", "to": "--1", "fromLane": "0", "toLane": "0",
                        "via": ":k_2_0", "dir": "s"}])
        pads, records = collapsed_passthrough_pads(net, Projection())
        corners = sorted(tuple(map(float, token.split(","))) for token in pads[0].get("shape").split())
        self.assertEqual(corners, [(8.4, -3.2), (8.4, 6.4), (11.6, -3.2), (11.6, 6.4)])
        self.assertEqual(records[0]["pad_across_m"], 9.6)
        self.assertEqual(records[0]["movements"], [["--2", "--1"], ["-1", "-2"]])

    def test_second_straight_movement_must_be_reciprocal(self):
        net = self.kinked("10,0 10,0")
        extra = ET.SubElement(net, "edge", id="-3", **{"from": "c", "to": "k"})
        ET.SubElement(extra, "lane", id="-3_0", index="0", width="3.2", allow="passenger",
                      shape="10,10 10,0", length="10")
        internal = ET.SubElement(net, "edge", id=":k_2")
        ET.SubElement(internal, "lane", id=":k_2_0", index="0", width="3.2", allow="passenger",
                      shape="10,0 10,0", length="0.1")
        ET.SubElement(net, "connection", **{"from": "-3", "to": "-2", "fromLane": "0", "toLane": "0",
                                             "via": ":k_2_0", "dir": "s"})
        with self.assertRaisesRegex(ValueError, "no junction completion rule applies"):
            collapsed_passthrough_pads(net, Projection())

    def test_collapsed_turn_is_not_silently_padded(self):
        net = self.kinked("10,0 10,0")
        net.find("connection").set("dir", "l")
        with self.assertRaisesRegex(ValueError, "no junction completion rule applies"):
            collapsed_passthrough_pads(net, Projection())


class CropRemnantTest(unittest.TestCase):
    def test_remnant_placed_off_its_design_inside_the_junction_is_reported(self):
        net = network([("cut", 500, 0), ("j", 498.5, 0)],
                      [("-5", "j", "cut", [(3.2, "passenger", "496.3,0.5 496.1,0.5", 0.2)])])
        rows = consumed_crop_remnants(net, [designed("-5", ["passenger"], shape="498.5,0 500,0")], {"cut"}, Projection())
        self.assertEqual([(row["edge_id"], row["junction_id"]) for row in rows], [("-5", "j")])
        self.assertGreater(rows[0]["lanes"][0]["native_offset_from_design_m"], 1.6)

    def test_trimmed_remnant_on_its_spine_is_kept(self):
        net = network([("cut", 500, 0), ("j", 490, 0)],
                      [("-5", "j", "cut", [(3.2, "passenger", "494,0 500,0", 6)])])
        self.assertEqual(consumed_crop_remnants(net, [designed("-5", ["passenger"], shape="490,0 500,0")], {"cut"}, Projection()), [])

    def test_interior_edges_are_never_remnants(self):
        net = network([("a", 0, 0), ("j", 1.5, 0)], [("-5", "j", "a", [(3.2, "passenger", "-2,0.5 -2.2,0.5", 0.2)])])
        self.assertEqual(consumed_crop_remnants(net, [designed("-5", ["passenger"], shape="1.5,0 0,0")], {"cut"}, Projection()), [])


class TurnaroundOverWalkwayTest(unittest.TestCase):
    def junction(self, loop_shape, walk_allow="pedestrian"):
        """Cycle road 4 ends at junction `e` beside footway 6; its turnaround loop is `:e_0`."""
        return network([("e", 0, 0), ("s", 0, -20), ("w", -20, 1.6), ("x", 20, 1.6)],
                       [("-4", "s", "e", [(2.5, "bicycle", "0,-20 0,-2")]),
                        ("--4", "e", "s", [(2.5, "bicycle", "0.2,-2 0.2,-20")]),
                        ("-6", "w", "e", [(2.0, walk_allow, "-20,1.6 0,1.6")]),
                        (":e_0", "", "", [(2.5, "bicycle", loop_shape, 1.0)])],
                       [{"from": "-4", "to": "--4", "fromLane": "0", "toLane": "0", "via": ":e_0_0", "dir": "t"}])

    def test_turnaround_loop_over_a_walkway_is_reported(self):
        rows = turnarounds_over_walkways(self.junction("0,-2 0,0.5 0.2,0.5 0.2,-2"))
        self.assertEqual([(row["junction_id"], row["internal_lane"]) for row in rows], [("e", ":e_0_0")])
        self.assertEqual(rows[0]["movement"], {"from": "-4", "to": "--4", "fromLane": "0", "toLane": "0"})
        self.assertGreater(rows[0]["crossed_walkways"][0]["overlap_m2"], 0)

    def test_loop_within_the_seam_or_beside_a_road_is_not(self):
        # The loop ribbon reaches y = -0.648 + 1.25 = 0.602, inside the 3 mm seam of the footway at y >= 0.6.
        self.assertEqual(turnarounds_over_walkways(self.junction("0,-2 0,-0.648 0.2,-0.648 0.2,-2")), [])
        self.assertNotEqual(turnarounds_over_walkways(self.junction("0,-2 0,-0.64 0.2,-0.64 0.2,-2")), [])
        self.assertEqual(turnarounds_over_walkways(self.junction("0,-2 0,0.5 0.2,0.5 0.2,-2", "passenger")), [])


if __name__ == "__main__":
    unittest.main()
