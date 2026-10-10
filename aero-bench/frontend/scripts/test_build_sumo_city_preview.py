"""Route selection checks for the city presentation's SUMO demand."""

import json
import runpy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


BUILDER = runpy.run_path(str(Path(__file__).with_name("build-sumo-city-preview.py")))
dedicated_walk_edges = BUILDER["dedicated_walk_edges"]


def small_cycle_network() -> ET.Element:
    """Four lawful cycles exercise regional networks below the preview inventory cap."""
    return ET.fromstring('''<net>
      <edge id="e0"><lane id="e0_0" allow="all" length="200" shape="-100,0 100,0"/></edge>
      <edge id="e1"><lane id="e1_0" allow="all" length="200" shape="100,0 300,0"/></edge>
      <edge id="e2"><lane id="e2_0" allow="all" length="200" shape="100,200 300,200"/></edge>
      <edge id="e3"><lane id="e3_0" allow="all" length="200" shape="-100,200 100,200"/></edge>
      <connection from="e0" to="e1" fromLane="0" toLane="0"/>
      <connection from="e1" to="e2" fromLane="0" toLane="0"/>
      <connection from="e2" to="e3" fromLane="0" toLane="0"/>
      <connection from="e3" to="e0" fromLane="0" toLane="0"/>
    </net>''')


class DedicatedWalkEdgesTest(unittest.TestCase):
    def test_sidewalk_selection_obstacles_cover_motor_corridors_only(self) -> None:
        from shapely.geometry import Point
        build = BUILDER["motor_lane_surfaces"]
        network = ET.fromstring('''<net>
          <edge id="motor"><lane width="3.2" shape="0,0 20,0"/></edge>
          <edge id="walk"><lane allow="pedestrian" width="2" shape="0,5 20,5"/></edge>
          <edge id="cross" function="crossing"><lane allow="pedestrian" width="4" shape="10,-5 10,5"/></edge>
          <edge id="turn" function="internal"><lane width="3.2" shape="20,0 25,5"/></edge>
        </net>''')
        surfaces = build(network, lambda x, y: (x, y))
        self.assertEqual(len(surfaces), 2)
        self.assertTrue(any(s.covers(Point(10, 1.7)) for s in surfaces))
        self.assertFalse(any(s.covers(Point(10, 5)) for s in surfaces))

    def test_bicycle_uses_permitted_lane_after_sidewalk_and_counts_motor_lanes(self) -> None:
        from shapely.geometry import box
        from shapely.strtree import STRtree
        build = BUILDER["building_clear_bicycle_cycles"]
        network = ET.fromstring('''<net><edge id="e">
          <lane allow="pedestrian" shape="0,0 20,0"/>
          <lane allow="passenger bicycle" shape="0,5 20,5"/>
          <lane allow="passenger" shape="0,8 20,8"/>
          <lane allow="bus" shape="0,11 20,11"/>
        </edge></net>''')
        # Deterministic candidate routes isolate lane selection from graph search.
        original_spread = build.__globals__["spread_cycles"]
        build.__globals__["spread_cycles"] = lambda *_args, **_options: [["e"] for _ in range(6)]
        try:
            obstacles = [box(0, -.5, 20, .5)]
            self.assertEqual(
                build(network, lambda x, y: (x, y), obstacles, STRtree(obstacles)),
                tuple(range(6)),
            )
        finally:
            build.__globals__["spread_cycles"] = original_spread

    def test_bicycle_selection_uses_available_small_network_inventory(self) -> None:
        from shapely.strtree import STRtree
        build = BUILDER["building_clear_bicycle_cycles"]
        network = small_cycle_network()
        self.assertEqual(
            build(network, lambda x, y: (x, y), [], STRtree([]), required_count=4),
            (0, 1, 2, 3),
        )
        with self.assertRaisesRegex(ValueError, "fewer than 5"):
            build(network, lambda x, y: (x, y), [], STRtree([]), required_count=5)

    def test_selects_only_explicit_sidewalk_lanes(self) -> None:
        def edge(edge_id: str, allow: str | None, length: str) -> ET.Element:
            element = ET.Element("edge", {"id": edge_id})
            attributes = {"length": length}
            if allow is not None:
                attributes["allow"] = allow
            ET.SubElement(element, "lane", attributes)
            return element

        edges = {
            "road": edge("road", None, "90"),
            "sidewalk": edge("sidewalk", "pedestrian", "90"),
            "short": edge("short", "pedestrian", "30"),
            "excluded": edge("excluded", "pedestrian", "100"),
        }
        self.assertEqual(dedicated_walk_edges(edges, {"excluded"}), [("sidewalk", 90.0)])


class AuthoredDemandTest(unittest.TestCase):
    def parameters(self, *, seed=31, vehicles=2, pedestrians=2, bicycles=2):
        return BUILDER["configured_recording_parameters"](
            duration=30, excluded_walk_edges=set(),
            bicycle_cycle_indices=tuple(range(min(6, bicycles))), seed=seed,
            motor_vehicles=vehicles, pedestrians=pedestrians, bicycles=bicycles,
        )

    def test_default_profile_keeps_accepted_materialized_parameters_and_route_bytes(self) -> None:
        root = Path(__file__).resolve().parents[2]
        evidence = root / "validation/frontend-opus-20260930/ground/assets-v10/private-native"
        inputs = json.loads((evidence / "huangpu-canonical-traffic-v2.recording-inputs-v2.json").read_text())
        parameters = inputs["recording_parameters"]
        recomputed = BUILDER["demo_recording_parameters"](
            120, set(parameters["excluded_walk_edges"]), tuple(parameters["bicycle_cycle_indices"]),
        )
        self.assertEqual(recomputed, parameters)
        network = ET.parse(
            root / "validation/frontend-opus-20260930/ground/closed-loop-v6/round-3/refined/network.net.xml"
        ).getroot()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "routes.xml"
            BUILDER["write_routes"](
                output, network, set(parameters["excluded_walk_edges"]),
                tuple(parameters["bicycle_cycle_indices"]), parameters,
            )
            accepted = evidence / "huangpu-canonical-traffic-v2.authored-preflight.routes.xml"
            self.assertEqual(output.read_bytes(), accepted.read_bytes())

    def test_explicit_zero_categories_emit_no_actor_routes(self) -> None:
        parameters = self.parameters(seed=0, vehicles=0, pedestrians=0, bicycles=0)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "routes.xml"
            demand = BUILDER["write_routes"](output, ET.fromstring("<net/>"), set(), (), parameters)
            routes = ET.parse(output).getroot()
        self.assertEqual(demand, {"bicycle": 0})
        self.assertEqual(routes.findall("vehicle"), [])
        self.assertEqual(routes.findall("person"), [])
        self.assertEqual(parameters["display_requirements"], {
            "minimum_displayed_vehicle_count": 0,
            "minimum_displayed_person_count": 0,
        })

    def test_seed_and_counts_change_real_authored_route_bytes(self) -> None:
        network = ET.fromstring('''<net>
          <edge id="walk.0"><lane allow="pedestrian" length="100"/></edge>
          <edge id="walk.1"><lane allow="pedestrian" length="100"/></edge>
        </net>''')
        write_routes = BUILDER["write_routes"]
        original_spread = write_routes.__globals__["spread_cycles"]
        original_allows = write_routes.__globals__["route_allows"]
        write_routes.__globals__["spread_cycles"] = lambda _network, _kind, count, **_options: [
            (f"edge.{index}",) for index in range(count)
        ]
        write_routes.__globals__["route_allows"] = lambda *_: True
        try:
            first = self.parameters(seed=BUILDER["SEED"], vehicles=2, pedestrians=2, bicycles=2)
            second = self.parameters(seed=BUILDER["SEED"] + 1, vehicles=3, pedestrians=1, bicycles=2)
            with tempfile.TemporaryDirectory() as temporary:
                first_path = Path(temporary) / "first.xml"
                second_path = Path(temporary) / "second.xml"
                write_routes(first_path, network, set(), (0, 1), first)
                write_routes(second_path, network, set(), (0, 1), second)
                first_root = ET.parse(first_path).getroot()
                second_root = ET.parse(second_path).getroot()
                self.assertNotEqual(first_path.read_bytes(), second_path.read_bytes())
        finally:
            write_routes.__globals__["spread_cycles"] = original_spread
            write_routes.__globals__["route_allows"] = original_allows
        self.assertEqual(len(first_root.findall("vehicle")), 4)
        self.assertEqual(len(first_root.findall("person")), 2)
        self.assertEqual(len(second_root.findall("vehicle")), 5)
        self.assertEqual(len(second_root.findall("person")), 1)
        self.assertTrue(second_root.find("vehicle/route").attrib["edges"].startswith("edge.1 "))

    def test_small_network_reuses_available_motor_and_bicycle_cycles(self) -> None:
        network = small_cycle_network()
        available = BUILDER["spread_cycles"](
            network, "passenger", BUILDER["MOTOR_ROUTE_COUNT"], require_count=False,
        )
        self.assertEqual(len(available), 4)
        with self.assertRaisesRegex(ValueError, "4 distributed passenger cycles; 20 are required"):
            BUILDER["spread_cycles"](network, "passenger", BUILDER["MOTOR_ROUTE_COUNT"])

        parameters = self.parameters(
            seed=BUILDER["SEED"], vehicles=20, pedestrians=0, bicycles=4,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "routes.xml"
            demand = BUILDER["write_routes"](
                output, network, set(), (0, 1, 2, 3), parameters,
            )
            root = ET.parse(output).getroot()
        self.assertEqual(sum(demand[name] for name in demand if name != "bicycle"), 20)
        self.assertEqual(demand["bicycle"], 4)
        self.assertEqual(len(root.findall("vehicle")), 24)
        self.assertEqual(
            len({item.find("route").attrib["edges"] for item in root.findall("vehicle")}),
            4,
        )

    def test_unavailable_bicycle_index_fails_with_network_capacity(self) -> None:
        parameters = BUILDER["configured_recording_parameters"](
            duration=30, excluded_walk_edges=set(), bicycle_cycle_indices=(4,),
            seed=31, motor_vehicles=0, pedestrians=0, bicycles=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "routes.xml"
            with self.assertRaisesRegex(ValueError, "indices are unavailable.*4"):
                BUILDER["write_routes"](
                    output, small_cycle_network(), set(), (4,), parameters,
                )

    def test_nonzero_motor_demand_rejects_zero_cycle_capacity(self) -> None:
        parameters = self.parameters(vehicles=1, pedestrians=0, bicycles=0)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "routes.xml"
            with self.assertRaisesRegex(ValueError, "no lawful distributed passenger cycle"):
                BUILDER["write_routes"](output, ET.fromstring("<net/>"), set(), (), parameters)

    def test_duration_capacity_and_sumo_seed_limit_fail_before_execution(self) -> None:
        self.assertEqual(BUILDER["preview_schedule_limits"](30), {
            "vehicles": 20, "bicycles": 4, "pedestrians": 13,
        })
        with self.assertRaisesRegex(ValueError, "vehicles=21 exceeds 20"):
            self.parameters(vehicles=21, pedestrians=0, bicycles=0)
        with self.assertRaisesRegex(ValueError, "seed must be an integer"):
            self.parameters(seed=2**31, vehicles=0, pedestrians=0, bicycles=0)

    def test_materialized_workspace_identity_must_match_counts_and_seed(self) -> None:
        source = {
            "schema_version": "aero-bench.city-traffic-preview-demand/v1",
            "workspace_schema_version": "aero-bench.city-workspace/v2",
            "workspace_sha256": "a" * 64,
            "workspace_size_bytes": 200,
            "seed": 7,
            "traffic": {"vehicles": 1, "pedestrians": 0, "bicycles": 0},
        }
        parameters = BUILDER["configured_recording_parameters"](
            duration=30, excluded_walk_edges=set(), bicycle_cycle_indices=(), seed=7,
            motor_vehicles=1, pedestrians=0, bicycles=0, authoring_source=source,
        )
        BUILDER["validate_recording_parameters"](
            parameters, duration=30, excluded_walk_edges=set(), bicycle_cycle_indices=(),
        )
        parameters["authoring_source"] = {**source, "seed": 8}
        with self.assertRaisesRegex(ValueError, "does not match"):
            BUILDER["validate_recording_parameters"](
                parameters, duration=30, excluded_walk_edges=set(), bicycle_cycle_indices=(),
            )


if __name__ == "__main__":
    unittest.main()
