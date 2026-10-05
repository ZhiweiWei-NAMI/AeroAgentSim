"""Regressions for topology-derived road markings and lane arrows."""
from __future__ import annotations

import unittest

from shapely.geometry import LineString, Polygon, box

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from city_street_markings import build_street_markings, clip_street_markings_to_roadbed, rendered_marking_footprint


def lane(edge_id: str, index: int, z: float, *, width: float = 3.2,
         points: list[list[float]] | None = None, kind: str = "motor") -> dict:
    if points is None:
        points = [[-40.0, z], [0.0, z], [60.0, z]]
    return {"id": f"{edge_id}_{index}", "index": index, "kind": kind,
            "width": width, "shape": points}


def edge(edge_id: str, source: str, target: str, lanes: list[dict],
         highway: str = "primary") -> dict:
    return {"id": edge_id, "from": source, "to": target,
            "lanes": lanes, "highway": highway}


def guide(edge_ids: list[str], z: float = 0.0) -> dict:
    return {"id": f"guide:{edge_ids[0]}", "edge_ids": edge_ids,
            "marking": "derived_direction_guide",
            "source_kind": "sumo-lane-topology-not-surveyed-marking",
            "shape": [[-40.0, z], [0.0, z], [60.0, z]]}


def markings_of(result: dict, kind: str) -> list[dict]:
    return [item for item in result["markings"] if item["kind"] == kind]


def line_of(item: dict) -> LineString:
    return LineString(item["shape"])


class StreetMarkingsTest(unittest.TestCase):
    def test_stop_paint_does_not_overlap_opposing_yellow_guide(self):
        forward = edge("east", "a", "b", [lane("east", 0, 1.5)])
        reverse = edge("west", "b", "a", [lane("west", 0, -1.6,
            points=[[60, -1.6], [0, -1.6], [-40, -1.6]])])
        result = build_street_markings([forward, reverse], [],
            [{"from": "east", "to": "west", "fromLane": "0", "toLane": "0", "tl": "b", "dir": "t"}],
            [guide(["east", "west"])])
        stops = markings_of(result, "stop_line")
        self.assertTrue(stops)
        for stop in stops:
            for yellow in markings_of(result, "centerline"):
                self.assertLess(rendered_marking_footprint(stop["shape"], stop["width_m"])
                    .intersection(rendered_marking_footprint(yellow["shape"], yellow["width_m"])).area, 1e-8)

    def test_unequal_multilane_road_gets_only_adjacent_dividers(self):
        road = edge("road", "a", "b", [
            lane("road", 0, 4.7, width=3.0),
            lane("road", 1, 1.35, width=3.7),
            lane("road", 2, -2.25, width=3.5),
        ])
        result = build_street_markings([road], [], [], [])
        dividers = markings_of(result, "lane_divider")
        self.assertEqual({tuple(item["source_lane_ids"]) for item in dividers},
                         {("road_0", "road_1"), ("road_1", "road_2")})
        self.assertTrue(all(item["pattern"] == "dashed" for item in dividers))
        divider_z = sorted({round(line_of(item).centroid.y, 2) for item in dividers})
        self.assertEqual(divider_z, [-0.5, 3.2],
                         "lane dividers must use each adjacent lane's actual width")
        # A one-way carriageway has its right and far-left edges, but no line
        # is inferred through the gaps between unequal lane widths.
        outer = markings_of(result, "lane_edge")
        self.assertEqual({tuple(item["source_lane_ids"]) for item in outer},
                         {("road_0",), ("road_2",)})
        self.assertTrue(all(item["color"] == "white" and item["pattern"] == "solid"
                            for item in outer))

    def test_outer_paint_fits_flush_lane_roadbed(self):
        road = edge("east", "a", "b", [lane("east", 0, 0)])
        result = build_street_markings([road], [], [], [])
        surface = LineString(road["lanes"][0]["shape"]).buffer(1.6, cap_style=2)
        result = clip_street_markings_to_roadbed(result, surface)
        outer = markings_of(result, "lane_edge")
        self.assertEqual(len(outer), 2)
        self.assertTrue(all(line_of(item).length > 90 for item in outer))
        for item in outer:
            self.assertLess(line_of(item).buffer(item["width_m"] / 2, cap_style=2)
                            .difference(surface).area, 1e-6)

    def test_two_way_guide_has_single_yellow_and_no_center_white_edges(self):
        forward = edge("east", "west-node", "east-node", [lane("east", 0, 1.6)])
        reverse = edge("west", "east-node", "west-node", [
            lane("west", 0, -1.6, points=[[60.0, -1.6], [0.0, -1.6], [-40.0, -1.6]])
        ])
        result = build_street_markings([forward, reverse], [], [],
                                       [guide(["east", "west"])])
        yellow = markings_of(result, "centerline")
        self.assertEqual(len(yellow), 1)
        self.assertEqual(yellow[0]["color"], "yellow")
        self.assertEqual(yellow[0]["pattern"], "solid")
        self.assertEqual(yellow[0]["source_lane_ids"], ["east_0", "west_0"])
        outer = markings_of(result, "lane_edge")
        self.assertEqual(len(outer), 2)
        self.assertTrue(all(abs(line_of(item).centroid.y) > 2.8 for item in outer))
        without_guide = build_street_markings([forward, reverse], [], [], [])
        self.assertEqual(len(markings_of(without_guide, "lane_edge")), 2,
                         "reverse shared-lane matching must suppress the center-side white edges")

    def test_four_motor_lanes_split_double_yellow_by_quarter_metre(self):
        forward = edge("east", "a", "b", [
            lane("east", 0, 4.8), lane("east", 1, 1.6),
        ])
        reverse = edge("west", "b", "a", [
            lane("west", 0, -4.8, points=[[60.0, -4.8], [0.0, -4.8], [-40.0, -4.8]]),
            lane("west", 1, -1.6, points=[[60.0, -1.6], [0.0, -1.6], [-40.0, -1.6]]),
        ])
        result = build_street_markings([forward, reverse], [], [],
                                       [guide(["east", "west"])])
        yellow = markings_of(result, "centerline")
        self.assertEqual(len(yellow), 2)
        self.assertTrue(all(item["color"] == "yellow" and item["pattern"] == "solid"
                            for item in yellow))
        self.assertEqual(len({item["semantic_group_id"] for item in yellow}), 1)
        z_values = sorted(line_of(item).centroid.y for item in yellow)
        self.assertAlmostEqual(z_values[1] - z_values[0], 0.25, places=2)
        white = markings_of(result, "lane_edge")
        self.assertEqual(len(white), 2)
        self.assertTrue(all(abs(line_of(item).centroid.y) > 6.0 for item in white))

    def test_controlled_approach_has_stop_line_and_final_solid_divider(self):
        controlled = edge("signal-road", "a", "junction", [
            lane("signal-road", 0, 1.6), lane("signal-road", 1, -1.6),
        ])
        node = {"id": "junction", "type": "traffic_light",
                "shape": [[58.0, -4.0], [62.0, -4.0], [62.0, 4.0], [58.0, 4.0]]}
        result = build_street_markings([controlled], [node], [
            {"from": "signal-road", "to": "out", "fromLane": "0",
             "toLane": "0", "tl": "sig1", "dir": "s"},
        ], [])
        stop_lines = markings_of(result, "stop_line")
        self.assertEqual(len(stop_lines), 1)
        stop = line_of(stop_lines[0])
        self.assertEqual(stop_lines[0]["source_lane_ids"], ["signal-road_0"])
        self.assertGreaterEqual(stop.length, 2.8)
        self.assertLess(stop.centroid.x, 58.0)
        divider = markings_of(result, "lane_divider")
        solid = [line_of(item) for item in divider if item["pattern"] == "solid"]
        dashed = [line_of(item) for item in divider if item["pattern"] == "dashed"]
        self.assertTrue(solid)
        self.assertTrue(dashed)
        self.assertGreaterEqual(sum(line.length for line in solid), 8.0)
        self.assertLess(max(line.bounds[2] for line in solid), 58.0)

    def test_left_right_and_straight_connections_draw_arrows_but_unknown_is_omitted(self):
        road = edge("approach", "a", "node", [
            lane("approach", 0, 1.6), lane("approach", 1, -1.6),
            lane("approach", 2, -4.8), lane("approach", 3, -8.0),
        ])
        connections = [
            {"from": "approach", "to": "left-out", "fromLane": "0", "toLane": "0", "dir": "l"},
            {"from": "approach", "to": "right-out", "fromLane": "1", "toLane": "0", "dir": "r"},
            {"from": "approach", "to": "straight-out", "fromLane": "2", "toLane": "0", "dir": "s"},
            {"from": "approach", "to": "u-turn", "fromLane": "3", "toLane": "0", "dir": "t"},
        ]
        result = build_street_markings([road], [], connections, [])
        arrows = result["arrows"]
        self.assertEqual({item["movement"] for item in arrows}, {"l", "r", "s"})
        self.assertEqual({item["source_lane_ids"][0] for item in arrows},
                         {"approach_0", "approach_1", "approach_2"})
        self.assertTrue(all(len(item["outline"]) >= 4 for item in arrows))
        road_corridors = {lane_id: LineString(item["shape"]).buffer(1.49)
                          for lane_id, item in ((lane["id"], lane) for lane in road["lanes"])}
        for item in arrows:
            outline = Polygon(item["outline"])
            lane_corridor = road_corridors[item["source_lane_ids"][0]]
            self.assertTrue(lane_corridor.covers(outline))

    def test_uppercase_partial_turns_preserve_source_and_follow_east_south_axes(self):
        edges = [
            edge("east-left", "a", "b", [lane("east-left", 0, 0)]),
            edge("east-right", "c", "d", [lane("east-right", 0, 8)]),
            edge("south-left", "e", "f", [lane("south-left", 0, 0,
                points=[[0.0, -40.0], [0.0, 0.0], [0.0, 60.0]])]),
            edge("south-right", "g", "h", [lane("south-right", 0, 8,
                points=[[8.0, -40.0], [8.0, 0.0], [8.0, 60.0]])]),
        ]
        connections = [
            {"from": "east-left", "to": "out1", "fromLane": "0", "toLane": "0", "dir": "L"},
            {"from": "east-left", "to": "out1b", "fromLane": "0", "toLane": "0", "dir": "l"},
            {"from": "east-right", "to": "out2", "fromLane": "0", "toLane": "0", "dir": "R"},
            {"from": "south-left", "to": "out3", "fromLane": "0", "toLane": "0", "dir": "L"},
            {"from": "south-right", "to": "out4", "fromLane": "0", "toLane": "0", "dir": "R"},
        ]
        result = build_street_markings(edges, [], connections, [])
        arrows = {item["source_lane_ids"][0]: item for item in result["arrows"]}
        self.assertEqual(set(arrows), {"east-left_0", "east-right_0", "south-left_0", "south-right_0"})
        self.assertEqual(arrows["east-left_0"]["movement"], "l")
        self.assertEqual(arrows["east-left_0"]["original_connection_direction"], "L")
        self.assertEqual(arrows["east-left_0"]["source_connection_directions"], ["L", "l"])
        self.assertEqual(len(arrows["east-left_0"]["source_connections"]), 2)
        self.assertEqual(arrows["east-right_0"]["movement"], "r")
        self.assertEqual(arrows["east-right_0"]["original_connection_direction"], "R")
        self.assertLess(Polygon(arrows["east-left_0"]["outline"]).centroid.y,
                        arrows["east-left_0"]["position"][1])
        self.assertGreater(Polygon(arrows["east-right_0"]["outline"]).centroid.y,
                           arrows["east-right_0"]["position"][1])
        self.assertGreater(Polygon(arrows["south-left_0"]["outline"]).centroid.x,
                           arrows["south-left_0"]["position"][0])
        self.assertLess(Polygon(arrows["south-right_0"]["outline"]).centroid.x,
                        arrows["south-right_0"]["position"][0])

    def test_unknown_connection_never_produces_an_arrow(self):
        road = edge("approach", "a", "node", [lane("approach", 0, -3.0)])
        result = build_street_markings([road], [], [
            {"from": "approach", "to": "u", "fromLane": "0", "toLane": "0", "dir": "t"},
            {"from": "approach", "to": "u", "fromLane": "0", "toLane": "0", "dir": "x"},
        ], [])
        self.assertEqual(result["arrows"], [])
        self.assertEqual(result["profile"]["unrendered_movement_codes"]["t"]["meaning"], "U-turn")
        self.assertEqual(result["profile"]["counts"]["turnaround_connections_omitted"], 1)
        self.assertEqual(result["profile"]["counts"]["other_direction_connections_omitted"], 1)

    def test_dashed_markings_are_explicit_segments_in_profile(self):
        road = edge("road", "a", "b", [
            lane("road", 0, 1.6), lane("road", 1, -1.6),
        ])
        result = build_street_markings([road], [], [], [])
        dashed = markings_of(result, "lane_divider")
        self.assertTrue(dashed)
        self.assertTrue(all(len(item["shape"]) >= 2 for item in dashed))
        self.assertIn("one explicit dash segment each",
                      result["profile"]["rules"]["dash_geometry"])

    def test_markings_stop_before_junction_and_work_with_negative_coordinates(self):
        road = edge("curved", "a", "b", [lane("curved", 0, 0, points=[
            [-120.0, -55.0], [-90.0, -54.0], [-60.0, -45.0], [-30.0, -20.0],
        ])])
        node = {"id": "b", "type": "priority",
                "shape": [[-34.0, -22.0], [-24.0, -22.0], [-24.0, -17.0], [-34.0, -17.0]]}
        result = build_street_markings([road], [node], [], [])
        for item in result["markings"]:
            if item["kind"] == "lane_edge":
                overlap = LineString(item["shape"]).intersection(
                    Polygon(node["shape"]).buffer(0.08))
                self.assertLessEqual(overlap.length, 0.002)
        self.assertEqual(result["profile"]["source_kind"], "derived-urban-design-profile")
        self.assertFalse(result["profile"]["surveyed_markings"])
        self.assertEqual(result["profile"]["traffic_standard_compliance"], "not_asserted")

    def test_three_lane_asymmetry_does_not_guess_centerline_style(self):
        forward = edge("east", "a", "b", [
            lane("east", 0, 3.2), lane("east", 1, 0.0),
        ])
        reverse = edge("west", "b", "a", [
            lane("west", 0, -3.2, points=[[60.0, -3.2], [0.0, -3.2], [-40.0, -3.2]])
        ])
        result = build_street_markings([forward, reverse], [], [],
                                       [guide(["east", "west"])])
        self.assertEqual(markings_of(result, "centerline"), [])

    def test_invalid_nonzero_junction_shape_is_repaired_and_reported(self):
        road = edge("road", "a", "b", [lane("road", 0, 0)])
        self_crossing = {"id": "node", "type": "priority",
                         "shape": [[0.0, 0.0], [4.0, 4.0], [0.0, 4.0], [3.0, 0.0]]}
        result = build_street_markings([road], [self_crossing], [], [])
        self.assertEqual(result["profile"]["counts"]["repaired_junction_shapes"], 1)

    def test_final_roadbed_clips_white_line_ribbons_and_keeps_provenance(self):
        roadbed = box(0, -2, 4, 2).union(box(6, -2, 10, 2))
        source = {"kind": "lane_divider", "color": "white", "pattern": "dashed",
                  "width_m": 0.12, "shape": [[-1, 0], [11, 0]],
                  "source_lane_ids": ["edge_0", "edge_1"], "source_kind": "sumo-derived"}
        qualified = clip_street_markings_to_roadbed(
            {"markings": [source], "arrows": [], "profile": {"counts": {}}}, roadbed)
        self.assertEqual(len(qualified["markings"]), 2)
        self.assertTrue(all(item["pattern"] == "dashed" for item in qualified["markings"]))
        self.assertTrue(all(item["source_lane_ids"] == ["edge_0", "edge_1"]
                            and item["source_kind"] == "sumo-derived"
                            for item in qualified["markings"]))
        for item in qualified["markings"]:
            painted = LineString(item["shape"]).buffer(item["width_m"] / 2, cap_style=2)
            self.assertLessEqual(painted.difference(roadbed).area, 1e-6)
        clip_report = qualified["profile"]["final_roadbed_clip"]
        self.assertEqual(clip_report["clipped_markings"], 1)
        self.assertEqual(clip_report["marking_clips"][0]["reason"],
                         "paint_footprint_clipped_to_final_displayed_roadbed")

    def test_final_roadbed_omits_incomplete_arrows_as_whole_polygons(self):
        roadbed = Polygon([(0, -2), (10, -2), (10, 2), (0, 2)])
        source = {"kind": "turn_arrow", "movement": "l", "color": "white",
                  "outline": [[1, -0.2], [2, -0.2], [2, 0.2], [1, 0.2], [1, -0.2]],
                  "original_connection_direction": "L",
                  "source_connection_directions": ["L"],
                  "source_connections": [{"from": "edge", "fromLane": "0", "dir": "L"}],
                  "source_lane_ids": ["edge_0"], "source_kind": "sumo-derived"}
        outside = {**source, "outline": [[9.7, -0.2], [10.7, -0.2], [10.7, 0.2],
                                          [9.7, 0.2], [9.7, -0.2]]}
        qualified = clip_street_markings_to_roadbed(
            {"markings": [], "arrows": [source, outside], "profile": {"counts": {}}}, roadbed)
        self.assertEqual(qualified["arrows"], [source])
        omitted = qualified["profile"]["final_roadbed_clip"]["arrow_omissions"]
        self.assertEqual(len(omitted), 1)
        self.assertEqual(omitted[0]["reason"], "complete_arrow_does_not_fit_final_displayed_roadbed")
        self.assertEqual(omitted[0]["source_connections"], source["source_connections"])
        self.assertEqual(omitted[0]["original_connection_direction"], "L")

    def test_final_roadbed_omits_a_yellow_double_line_pair_together(self):
        roadbed = Polygon([(0, -2), (10, -2), (10, 0.08), (8.5, 0.08),
                           (8.5, 2), (0, 2)])
        common = {"kind": "centerline", "color": "yellow", "pattern": "solid",
                  "width_m": 0.12, "source_lane_ids": ["east_0", "west_0"],
                  "semantic_group_id": "guide:east-west:yellow-double:0",
                  "source_kind": "sumo-lane-topology-not-surveyed-marking"}
        pair = [
            {**common, "shape": [[1, -0.125], [9, -0.125]]},
            {**common, "shape": [[1, 0.125], [9, 0.125]]},
        ]
        qualified = clip_street_markings_to_roadbed(
            {"markings": pair, "arrows": [], "profile": {"counts": {}}}, roadbed)
        self.assertEqual(qualified["markings"], [])
        omissions = qualified["profile"]["final_roadbed_clip"]["marking_omissions"]
        self.assertEqual(len(omissions), 2)
        self.assertTrue(all(item["reason"] ==
                            "yellow_double_line_pair_does_not_fit_final_displayed_roadbed"
                            for item in omissions))
        self.assertTrue(all(item["pair_source_lane_ids"] == ["east_0", "west_0"]
                            for item in omissions))

    def test_renderer_footprint_detects_miter_at_roadbed_hole(self):
        shape = [[-1., 0.], [0., 0.], [0., 1.]]
        roadbed = box(-2, -2, 2, 2).difference(box(.048, -.052, .052, -.048))
        self.assertEqual(LineString(shape).buffer(.06, cap_style=2).difference(roadbed).area, 0.)
        self.assertGreater(rendered_marking_footprint(shape, .12).difference(roadbed).area, 0.)

if __name__ == "__main__":
    unittest.main()
