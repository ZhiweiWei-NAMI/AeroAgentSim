"""Regressions for SUMO walking-area connections on a motor roadbed."""
from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parent))

from city_pedestrian_connections import (audit_crossing_endpoints,
                                         audit_pedestrian_walkbed_landings,
                                         build_pedestrian_connections,
                                         build_pedestrian_connection_paths,
                                         plan_pedestrian_connection_paths,
                                         _polygon_record)


def network() -> ET.Element:
    return ET.fromstring("""
      <net>
        <edge id=":near" function="walkingarea"><lane id=":near_0" index="0"/></edge>
        <edge id=":far" function="walkingarea"><lane id=":far_0" index="0"/></edge>
        <edge id=":decoy" function="walkingarea"><lane id=":decoy_0" index="0"/></edge>
        <edge id=":cross" function="crossing"><lane id=":cross_0" index="0"/></edge>
        <connection from=":near" to=":cross" fromLane="0" toLane="0"/>
        <connection from=":cross" to=":far" fromLane="0" toLane="0"/>
      </net>""")


CROSSING = {"id": ":cross_0", "shape": [[0, 0], [0, 3]], "width": 1.0}
NEAR = {"id": ":near", "shape": [[-1, -2], [1, -2], [1, 0], [-1, 0]]}
FAR = {"id": ":far", "shape": [[-1, 3], [1, 3], [1, 5], [-1, 5]]}
DECOY = {"id": ":decoy", "shape": [[-0.4, -0.3], [0.4, -0.3],
                                     [0.4, 0.3], [-0.4, 0.3]]}


def ported_network() -> ET.Element:
    source = network()
    left = ET.SubElement(source, "edge", {"id": "left"})
    ET.SubElement(left, "lane", {"id": "left_0", "index": "0"})
    ET.SubElement(left, "lane", {"id": "left_1", "index": "1"})
    right = ET.SubElement(source, "edge", {"id": "right"})
    ET.SubElement(right, "lane", {"id": "right_0", "index": "0"})
    ET.SubElement(source, "connection", {"from": "left", "to": ":near",
                                         "fromLane": "1", "toLane": "0"})
    ET.SubElement(source, "connection", {"from": ":far", "to": "right",
                                         "fromLane": "0", "toLane": "0"})
    return source


PORT_LANES = [
    {"id": "left_0", "kind": "motor", "width": 3, "shape": [[2, -3], [2, -1]]},
    {"id": "left_1", "kind": "walk", "width": 1.2, "shape": [[0, -3], [0, -1]]},
    {"id": "right_0", "kind": "walk", "width": 1.2, "shape": [[0, 4], [0, 6]]},
]


class PedestrianConnectionsTest(unittest.TestCase):
    def test_pedestrian_permission_on_motor_lane_does_not_prove_walkbed(self) -> None:
        source = ported_network()
        source.find("edge[@id='left']/lane[@id='left_1']").set("allow", "passenger pedestrian")
        lane = dict(PORT_LANES[1], kind="motor")
        path = {"destination_kind": "normal", "destination_lane_id": lane["id"],
                "destination_endpoint": "end", "width_m": 1.2,
                "walking_area_id": ":near", "source_crossing_id": CROSSING["id"],
                "source_crossing_endpoint": "start"}
        failures = audit_pedestrian_walkbed_landings(source, [lane], box(-2, -4, 2, 7), [path])
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0]["source_pedestrian_permission"])
        self.assertTrue(failures[0]["displayed_walkbed_covers_landing"])
        self.assertEqual(failures[0]["reason"], "source_route_has_no_exclusive_pedestrian_lane")
        plan = plan_pedestrian_connection_paths(source, [NEAR, FAR], [CROSSING],
                                                [PORT_LANES[0], lane, PORT_LANES[2]])
        self.assertFalse(any(item["source_normal_lane_id"] == lane["id"] for item in plan["candidates"]))

    def test_exclusive_walk_permission_requires_full_width_displayed_landing(self) -> None:
        source = ported_network()
        source.find("edge[@id='left']/lane[@id='left_1']").set("allow", "pedestrian")
        path = {"destination_kind": "normal", "destination_lane_id": "left_1",
                "destination_endpoint": "end", "width_m": 1.2,
                "walking_area_id": ":near", "source_crossing_id": CROSSING["id"],
                "source_crossing_endpoint": "start"}
        narrow = box(-.3, -4, .3, 7)
        failures = audit_pedestrian_walkbed_landings(source, PORT_LANES, narrow, [path])
        self.assertEqual(failures[0]["normal_point_to_walkbed_m"], 0)
        self.assertGreater(failures[0]["uncovered_landing_length_m"], .5)
        self.assertEqual(failures[0]["reason"], "normal_port_lacks_displayed_walkbed")
        self.assertEqual(audit_pedestrian_walkbed_landings(source, PORT_LANES,
                                                          box(-.6, -4, .6, 7), [path]), [])

    def test_unrestricted_source_route_and_fake_walk_classification_cannot_pass(self) -> None:
        path = {"destination_kind": "normal", "destination_lane_id": "left_1",
                "destination_endpoint": "end", "width_m": 1.2,
                "walking_area_id": ":near", "source_crossing_id": CROSSING["id"],
                "source_crossing_endpoint": "start"}
        failures = audit_pedestrian_walkbed_landings(ported_network(), PORT_LANES,
                                                     box(-2, -4, 2, 7), [path])
        self.assertTrue(failures[0]["source_pedestrian_permission"])
        self.assertFalse(failures[0]["exclusive_source_pedestrian_lane"])

    def test_mixed_precision_cannot_hide_a_real_corridor_gap(self) -> None:
        from shapely import set_precision
        road = box(-2, -2, 2, -.5032)
        walk = set_precision(box(-2, -.497, 2, 7), .001)
        results = [build_pedestrian_connection_paths(
            ported_network(), [NEAR, FAR], [CROSSING], PORT_LANES,
            road, surface, Polygon()) for surface in [walk, Polygon(walk.exterior.coords)]]
        self.assertEqual(len(results[0]["omissions"]), 1)
        self.assertEqual(results[0]["omissions"], results[1]["omissions"])


    def test_port_paths_bind_exact_walking_lanes_and_audit_complete_routes(self) -> None:
        roadbed = box(-3, -4, 3, 7)
        plan = plan_pedestrian_connection_paths(ported_network(), [NEAR, FAR],
                                                [CROSSING], PORT_LANES)
        self.assertEqual(len(plan["candidates"]), 2)
        self.assertEqual({item["source_normal_lane_id"] for item in plan["candidates"]},
                         {"left_1", "right_0"})
        self.assertTrue(all(item["source_kind"] ==
                            "derived-from-sumo-pedestrian-connection-ports-not-surveyed"
                            for item in plan["candidates"]))
        self.assertTrue(all(Polygon(item["corridor"]["outline"]).is_valid
                            for item in plan["candidates"]))
        result = build_pedestrian_connection_paths(ported_network(), [NEAR, FAR],
                                                    [CROSSING], PORT_LANES,
                                                    roadbed, Polygon(), Polygon())
        self.assertEqual(len(result["connection_paths"]), 2)
        self.assertEqual(result["omissions"], [])
        self.assertEqual(audit_crossing_endpoints(ported_network(), [NEAR, FAR],
                                                  [CROSSING], PORT_LANES,
                                                  roadbed, Polygon(), Polygon(),
                                                  [], result["connection_paths"]), [])
        forged = [dict(item) for item in result["connection_paths"]]
        forged[0]["corridor"] = {"outline": [[0, 0], [1, 0], [0, 1]], "holes": []}
        with self.assertRaisesRegex(ValueError, "corridor differs from SUMO ports"):
            audit_crossing_endpoints(ported_network(), [NEAR, FAR], [CROSSING],
                                     PORT_LANES, roadbed, Polygon(), Polygon(), [], forged)

    def test_path_requires_full_paved_corridor_and_no_building(self) -> None:
        source = ported_network()
        narrow = box(-0.3, -4, 0.3, 7)
        result = build_pedestrian_connection_paths(source, [NEAR, FAR], [CROSSING],
                                                    PORT_LANES, narrow, Polygon(), Polygon())
        self.assertEqual(len(result["connection_paths"]), 0)
        self.assertEqual([item["reason"] for item in result["omissions"]],
                         ["corridor_leaves_final_paving"] * 2)
        self.assertEqual(len(audit_crossing_endpoints(source, [NEAR, FAR], [CROSSING],
                                                      PORT_LANES, narrow, Polygon(),
                                                      Polygon(), [], [])), 2)
        wide = box(-3, -4, 3, 7)
        building = box(-0.2, -0.8, 0.2, -0.2)
        blocked = build_pedestrian_connection_paths(source, [NEAR, FAR], [CROSSING],
                                                     PORT_LANES, wide, Polygon(), building)
        self.assertEqual(len(blocked["connection_paths"]), 1)
        self.assertEqual(blocked["omissions"][0]["reason"], "corridor_intersects_building")

    def test_overlapping_other_walking_area_is_not_a_port_destination(self) -> None:
        roadbed = box(-3, -4, 3, 7)
        result = build_pedestrian_connection_paths(ported_network(), [NEAR, FAR, DECOY],
                                                    [CROSSING], PORT_LANES,
                                                    roadbed, Polygon(), Polygon())
        self.assertEqual(len(result["connection_paths"]), 2)
        self.assertEqual(result["omissions"], [])
        self.assertEqual({item["walking_area_id"] for item in result["connection_paths"]},
                         {":near", ":far"})

    def test_no_normal_area_uses_crossing_to_crossing_then_reaches_normal(self) -> None:
        source = ET.fromstring("""
          <net>
            <edge id=":left" function="walkingarea"><lane id=":left_0" index="0"/></edge>
            <edge id=":middle" function="walkingarea"><lane id=":middle_0" index="0"/></edge>
            <edge id=":right" function="walkingarea"><lane id=":right_0" index="0"/></edge>
            <edge id=":c0" function="crossing"><lane id=":c0_0" index="0"/></edge>
            <edge id=":c1" function="crossing"><lane id=":c1_0" index="0"/></edge>
            <edge id="walk-left"><lane id="walk-left_0" index="0"/></edge>
            <edge id="walk-right"><lane id="walk-right_0" index="0"/></edge>
            <connection from="walk-left" to=":left" fromLane="0" toLane="0"/>
            <connection from=":left" to=":c0" fromLane="0" toLane="0"/>
            <connection from=":c0" to=":middle" fromLane="0" toLane="0"/>
            <connection from=":middle" to=":c1" fromLane="0" toLane="0"/>
            <connection from=":c1" to=":right" fromLane="0" toLane="0"/>
            <connection from=":right" to="walk-right" fromLane="0" toLane="0"/>
          </net>""")
        areas = [{"id": ":left", "shape": [[-1, -2], [1, -2], [1, 0], [-1, 0]]},
                 {"id": ":middle", "shape": [[-1, 3], [1, 3], [1, 5], [-1, 5]]},
                 {"id": ":right", "shape": [[-1, 8], [1, 8], [1, 10], [-1, 10]]}]
        crossings = [{"id": ":c0_0", "shape": [[0, 0], [0, 3]], "width": 1},
                     {"id": ":c1_0", "shape": [[0, 5], [0, 8]], "width": 1}]
        lanes = [{"id": "walk-left_0", "kind": "walk", "width": 1,
                  "shape": [[0, -3], [0, -1]]},
                 {"id": "walk-right_0", "kind": "walk", "width": 1,
                  "shape": [[0, 9], [0, 11]]}]
        paved = box(-3, -4, 3, 12)
        plan = plan_pedestrian_connection_paths(source, areas, crossings, lanes)
        self.assertEqual(len(plan["candidates"]), 3)
        middle = next(item for item in plan["candidates"]
                      if item["walking_area_id"] == ":middle")
        self.assertEqual(middle["destination_kind"], "crossing")
        qualified = build_pedestrian_connection_paths(source, areas, crossings,
                                                      lanes, paved, Polygon(), Polygon())
        self.assertEqual(audit_crossing_endpoints(source, areas, crossings, lanes,
                                                  paved, Polygon(), Polygon(), [],
                                                  qualified["connection_paths"]), [])
        isolated = [item for item in qualified["connection_paths"]
                    if item["walking_area_id"] == ":middle"]
        self.assertEqual(len(audit_crossing_endpoints(source, areas, crossings, lanes,
                                                      paved, Polygon(), Polygon(), [],
                                                      isolated)), 4)

    def test_flat_connection_survives_empty_raised_walkbed(self) -> None:
        roadbed = box(-3, -3, 3, 6)
        result = build_pedestrian_connections(network(), [NEAR, FAR], [CROSSING],
                                               roadbed, Polygon(), Polygon())
        self.assertEqual([item["id"] for item in result["shared_areas"]],
                         [":far", ":near"])
        self.assertTrue(result["markings"])
        self.assertTrue(all(mark["kind"] == "pedestrian_connection_guide"
                            and mark["pattern"] == "dashed" and mark["width_m"] == 0.12
                            and mark["color"] == "white" for mark in result["markings"]))
        failures = audit_crossing_endpoints(network(), [NEAR, FAR], [CROSSING],
                                             [], roadbed, Polygon(), Polygon(),
                                             result["shared_areas"], [])
        self.assertEqual(len(failures), 2)
        self.assertTrue(all(item["reason"] == "crossing_endpoint_has_no_complete_pedestrian_port_path"
                            for item in failures))
        for record in result["shared_areas"]:
            self.assertEqual(record["crossing_ids"], [":cross_0"])
            self.assertFalse(record["exclusive_pedestrian_right_of_way"])
            original = Polygon(next(area["shape"] for area in (NEAR, FAR)
                                    if area["id"] == record["id"]))
            for part in record["polygons"]:
                shared = Polygon(part["outline"], part["holes"])
                self.assertLess(shared.difference(original.intersection(roadbed)).area, 1e-8)
        crossing_region = LineString(CROSSING["shape"]).buffer(
            CROSSING["width"] / 2, cap_style=2)
        for mark in result["markings"]:
            line = LineString(mark["shape"])
            self.assertLessEqual(line.length, 0.5002)
            paint = line.buffer(mark["width_m"] / 2, cap_style=2)
            self.assertLess(paint.difference(roadbed).area, 1e-5)
            self.assertLess(paint.intersection(crossing_region).area, 1e-5)

    def test_unconnected_area_does_not_create_shared_guide(self) -> None:
        result = build_pedestrian_connections(network(), [DECOY], [CROSSING],
                                               box(-3, -3, 3, 6), Polygon(), Polygon())
        self.assertEqual(result["shared_areas"], [])
        self.assertEqual(result["markings"], [])

    def test_one_walking_area_adjacent_to_two_crossings_has_one_record(self) -> None:
        source = network()
        second_edge = ET.SubElement(source, "edge", {"id": ":cross2", "function": "crossing"})
        ET.SubElement(second_edge, "lane", {"id": ":cross2_0", "index": "0"})
        ET.SubElement(source, "connection", {"from": ":near", "to": ":cross2",
                                             "fromLane": "0", "toLane": "0"})
        result = build_pedestrian_connections(source, [NEAR],
            [CROSSING, {"id": ":cross2_0", "shape": [[0.5, 0], [0.5, 3]], "width": 1}],
            box(-3, -3, 3, 6), Polygon(), Polygon())
        self.assertEqual(len(result["shared_areas"]), 1)
        self.assertEqual(result["shared_areas"][0]["crossing_ids"],
                         [":cross2_0", ":cross_0"])

    def test_crossing_end_must_match_its_own_connected_walking_area(self) -> None:
        far_nearby = {**NEAR, "shape": [[8, -2], [10, -2], [10, 0], [8, 0]]}
        records = build_pedestrian_connections(network(), [far_nearby, FAR, DECOY],
                                                [CROSSING], box(-3, -3, 12, 6),
                                                Polygon(), Polygon())["shared_areas"]
        records.append({"id": ":decoy", "polygons": [
            {"outline": DECOY["shape"], "holes": []}]})
        failures = audit_crossing_endpoints(network(), [far_nearby, FAR, DECOY],
                                            [CROSSING], [], box(-3, -3, 12, 6),
                                            Polygon(), Polygon(), records, [])
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["endpoint"], "start")
        self.assertEqual(failures[0]["connected_walking_area_ids"], [":near"])
        self.assertIsNone(failures[0]["connected_shared_distance_m"])

    def test_unrelated_raised_walkbed_cannot_serve_connected_area(self) -> None:
        far_nearby = {**NEAR, "shape": [[8, -2], [10, -2], [10, 0], [8, 0]]}
        raised = box(-0.5, -0.5, 0.5, 0.5)
        records = build_pedestrian_connections(network(), [far_nearby, FAR],
            [CROSSING], box(-2, -3, 12, 6), raised, Polygon())["shared_areas"]
        failures = audit_crossing_endpoints(network(), [far_nearby, FAR],
                                            [CROSSING], [], box(-2, -3, 12, 6),
                                            raised, Polygon(), records, [])
        self.assertEqual([item["endpoint"] for item in failures], ["start", "end"])
        self.assertIsNone(failures[0]["raised_walkbed_distance_m"])

    def test_bowtie_point_contact_does_not_guide_distant_component(self) -> None:
        source = ET.fromstring("""
          <net>
            <edge id=":wa" function="walkingarea"><lane id=":wa_0" index="0"/></edge>
            <edge id=":cross" function="crossing"><lane id=":cross_0" index="0"/></edge>
            <connection from=":cross" to=":wa" fromLane="0" toLane="0"/>
          </net>""")
        walking_area = {"id": ":wa", "shape": [[-2, 0], [2, 4], [-2, 4], [2, 0]]}
        crossing = {"id": ":cross_0", "shape": [[0, -2], [0, 0]], "width": 1}
        result = build_pedestrian_connections(source, [walking_area], [crossing],
                                               box(-4, -4, 4, 6), Polygon(), Polygon())
        self.assertEqual(len(result["shared_areas"]), 1)
        self.assertEqual(len(result["shared_areas"][0]["polygons"]), 1)
        self.assertTrue(all(max(point[1] for point in item["shape"]) <= 2.01
                            for item in result["markings"]))

    def test_bowtie_near_self_intersection_selects_one_root(self) -> None:
        source = ET.fromstring("""
          <net>
            <edge id=":wa" function="walkingarea"><lane id=":wa_0" index="0"/></edge>
            <edge id=":cross" function="crossing"><lane id=":cross_0" index="0"/></edge>
            <connection from=":cross" to=":wa" fromLane="0" toLane="0"/>
          </net>""")
        walking_area = {"id": ":wa", "shape": [[-2, 0], [2, 4], [-2, 4], [2, 0]]}
        crossing = {"id": ":cross_0", "shape": [[0, -0.2], [0, 1.8]], "width": 1}
        result = build_pedestrian_connections(source, [walking_area], [crossing],
                                               box(-4, -4, 4, 6), Polygon(), Polygon())
        self.assertEqual(len(result["shared_areas"][0]["polygons"]), 1)
        self.assertTrue(all(max(point[1] for point in item["shape"]) <= 2.01
                            for item in result["markings"]))

    def test_audit_uses_requested_distance_limit_for_connected_component(self) -> None:
        source = ET.fromstring("""
          <net>
            <edge id=":wa" function="walkingarea"><lane id=":wa_0" index="0"/></edge>
            <edge id=":cross" function="crossing"><lane id=":cross_0" index="0"/></edge>
            <connection from=":cross" to=":wa" fromLane="0" toLane="0"/>
          </net>""")
        walking_area = {"id": ":wa", "shape": [[-0.5, 3.7], [0.5, 3.7],
                                                [0.5, 5], [-0.5, 5]]}
        crossing = {"id": ":cross_0", "shape": [[0, 0], [0, 3]], "width": 1}
        shared = [{"id": ":wa", "polygons": [{"outline": walking_area["shape"], "holes": []}]}]
        loose = audit_crossing_endpoints(source, [walking_area], [crossing],
                                         [], box(-2, -1, 2, 6), Polygon(), Polygon(),
                                         shared, [], limit_m=1.0)
        self.assertEqual([item["endpoint"] for item in loose], ["start", "end"])
        self.assertAlmostEqual(loose[1]["connected_shared_distance_m"], 0.7)
        tight = audit_crossing_endpoints(source, [walking_area], [crossing],
                                         [], box(-2, -1, 2, 6), Polygon(), Polygon(),
                                         shared, [], limit_m=0.6)
        self.assertEqual([item["endpoint"] for item in tight], ["start", "end"])
        self.assertIsNone(tight[1]["connected_shared_distance_m"])

    def test_real_599408454_w1_sliver_survives_json_roundtrip(self) -> None:
        # From the valid roadbed clip in normalized-road.json. Four-decimal
        # rounding made this :599408454_w1 piece self-intersect.
        polygon = Polygon([
            (152.94, -367.6), (152.1390625000054, -366.93740625000447),
            (152.141, -366.939), (154.399, -368.169),
            (154.34494568262087, -368.3074549181992), (152.94, -367.6),
        ])
        self.assertTrue(polygon.is_valid)
        serialized = json.loads(json.dumps(_polygon_record(polygon)))
        restored = Polygon(serialized["outline"], serialized["holes"])
        self.assertTrue(restored.is_valid)
        self.assertAlmostEqual(restored.area, polygon.area, places=12)

    def test_raised_walkbed_can_serve_crossing_end_without_shared_polygon(self) -> None:
        roadbed = box(-3, -3, 3, 3)
        raised = box(-3, 3, 3, 6)
        result = build_pedestrian_connections(network(), [NEAR, FAR], [CROSSING],
                                               roadbed, raised, Polygon())
        self.assertEqual([item["id"] for item in result["shared_areas"]], [":near"])
        failures = audit_crossing_endpoints(network(), [NEAR, FAR], [CROSSING],
                                             [], roadbed, raised, Polygon(),
                                             result["shared_areas"], [])
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[1]["raised_walkbed_distance_m"], 0.0)

    def test_nearby_walkbed_does_not_replace_missing_direct_connection(self) -> None:
        source = network()
        source.remove(next(connection for connection in source.findall("connection")
                           if connection.get("from") == ":near"))
        raised = box(-3, -1, 3, 6)
        failures = audit_crossing_endpoints(source, [NEAR, FAR], [CROSSING],
                                            [], box(-3, -3, 3, 6), raised, Polygon(), [], [])
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["endpoint"], "start")
        self.assertEqual(failures[0]["reason"],
                         "crossing_endpoint_lacks_direct_walkingarea_connection")

    def test_building_and_roadbed_clips_do_not_extend_original_area(self) -> None:
        roadbed = box(-0.5, -3, 3, 6)
        building = box(0, -2, 0.4, -1)
        result = build_pedestrian_connections(network(), [NEAR], [CROSSING],
                                               roadbed, Polygon(), building)
        shared = result["shared_areas"][0]["polygons"]
        for part in shared:
            polygon = Polygon(part["outline"], part["holes"])
            self.assertLess(polygon.difference(Polygon(NEAR["shape"])
                                               .intersection(roadbed).difference(building)).area, 1e-8)
            self.assertLess(polygon.intersection(building).area, 1e-8)


if __name__ == "__main__":
    unittest.main()
