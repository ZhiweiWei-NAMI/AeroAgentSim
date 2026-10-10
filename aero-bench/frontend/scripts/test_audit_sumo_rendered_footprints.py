"""Retirement boundary and unit checks for the historical v1 motion audit."""

import copy
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).with_name("audit-sumo-rendered-footprints.py")
AUDIT = runpy.run_path(str(SCRIPT))


def contract_fixture() -> dict:
    """Return a small contract fixture, not simulation or acceptance evidence."""
    pins = {
        "placement_sha256": "placement",
        "objects_sha256": "objects",
        "render_manifest_sha256": "render-manifest",
        "network_sha256": "network",
    }
    objects = {
        "buildings": [
            {
                "object_id": "building.one",
                "footprint_enu_m": [[70, 70], [80, 70], [80, 80], [70, 80]],
            },
            {
                "object_id": "building.two",
                "footprint_enu_m": [[-80, 70], [-70, 70], [-70, 80], [-80, 80]],
            },
        ]
    }
    render_manifest = {
        "scene": {
            "objects_json_sha256": pins["objects_sha256"],
            "mesh_pack_source_sha256": "pack-source",
        },
        "counts": {"buildings": 2},
        "buildings": [
            {"object_id": "building.one"},
            {"object_id": "building.two"},
        ],
    }
    road = {
        "building_placement_sha256": pins["placement_sha256"],
        "traffic_recorded_building_placement_sha256": pins["placement_sha256"],
        "roadbed": [{"outline": [[-100, -100], [100, -100], [100, 100], [-100, 100]]}],
        "walkbed": [{"outline": [[-20, -20], [20, -20], [20, 20], [-20, 20]]}],
    }
    vehicles = [
        [f"sedan.{index}", -50 + (index % 10) * 5, -40 + (index // 10) * 5,
         0, "sedan"]
        for index in range(50)
    ]
    vehicles.append(["bicycle.0", 0, 0, 0, "bicycle"])
    traffic = {
        "building_placement_sha256": pins["placement_sha256"],
        "source_network_sha256": pins["network_sha256"],
        "mesh_pack_source_sha256": "pack-source",
        "visual_obstacle_basis": {
            "policy": AUDIT["UNION_POLICY"],
            "route_obstacle_basis": AUDIT["UNION_POLICY"],
            "objects_json_sha256": pins["objects_sha256"],
            "render_manifest_sha256": pins["render_manifest_sha256"],
            "pack_source_sha256": "pack-source",
            "rendered_footprint_count": 2,
            "placement_proxy_count": 2,
            "obstacle_union_count": 4,
        },
        "signals": [{"tls": "signal.0", "link": 0}],
        "vehicle_lane_use": {},
        "bicycle_lane_use": {"bicycle.0": ["motor_0"]},
        "person_route_edges": {"person.0": "walk"},
        "paved_bicycle_cycle_indices": [0, 1, 2, 3, 4, 5],
        "sumo_colliding_vehicle_ids": [],
        "frames": [
            {
                "second": 0.0,
                "vehicles": copy.deepcopy(vehicles),
                "persons": [["person.0", 0, 10, 0]],
                "tls": {"signal.0": "G"},
            },
            {
                "second": 10.25,
                "vehicles": copy.deepcopy(vehicles),
                "persons": [["person.0", 1, 10, 0]],
                "tls": {"signal.0": "r"},
            },
        ],
    }
    network = ET.fromstring(
        """<net>
          <edge id="motor"><lane id="motor_0"/></edge>
          <edge id="walk"><lane id="walk_0" allow="pedestrian"/></edge>
          <tlLogic id="signal.0" offset="0">
            <phase duration="10" state="G"/><phase duration="10" state="r"/>
          </tlLogic>
        </net>"""
    )
    return {
        "traffic": traffic,
        "road": road,
        "objects": objects,
        "render_manifest": render_manifest,
        "network": network,
        "pins": pins,
    }


class HistoricalAuditRetirementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.artifacts = contract_fixture()

    def audit_with(self, *, traffic=None, road=None, objects=None, network=None):
        base = self.artifacts
        return AUDIT["audit"](
            traffic if traffic is not None else base["traffic"],
            road if road is not None else base["road"],
            objects if objects is not None else base["objects"],
            base["render_manifest"],
            network if network is not None else base["network"],
            base["pins"],
        )

    def assert_failed_with(self, report, token: str) -> None:
        self.assertFalse(report["ok"], f"mutation was not detected: {token}")
        self.assertTrue(
            any(token in failure for failure in report["failures"]),
            f"expected failure containing {token!r}, got {report['failures']!r}",
        )

    def pin_failures(self, fixture=None) -> list[str]:
        fixture = fixture or self.artifacts
        failures = []
        AUDIT["check_pins"](
            fixture["traffic"], fixture["road"], fixture["render_manifest"],
            fixture["objects"], fixture["pins"], failures,
        )
        return failures

    def test_historical_entrypoint_has_no_implicit_active_quartet(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--scene", result.stderr)
        self.assertIn("--network", result.stderr)
        self.assertIn("--rendered-objects", result.stderr)
        self.assertIn("--render-objects-manifest", result.stderr)

    def test_retired_scenes_fail_closed_on_current_pack_drift(self) -> None:
        root = AUDIT["ROOT"]
        network = root / "validation/city-ground-roads-20260927/sumo/network.net.xml"
        objects = root / "validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json"
        manifest = root / "frontend/public/building-renders/shanghai-huangpu-east-v1/manifest.json"
        for scene_name in ("RETIRED_GROUND_SCENE", "RETIRED_WALKABLE_SCENE"):
            with self.subTest(scene=scene_name):
                with self.assertRaisesRegex(
                    ValueError, "Scene mesh pack manifest differs from its pinned file reference"
                ):
                    AUDIT["load_artifacts"](AUDIT[scene_name], network, objects, manifest)

    def test_unit_contract_fixture_passes_without_accepting_a_recording(self) -> None:
        report = self.audit_with()
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["rendered_footprints_checked"], 2)
        self.assertEqual(
            report["footprint_penetration"]["samples"],
            {"vehicle_point": 0, "vehicle_box": 0, "person_point": 0, "person_circle": 0},
        )

    def test_consistent_unit_inventory_passes_pin_check(self) -> None:
        self.assertEqual(self.pin_failures(), [])

    def test_manifest_count_mismatch_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["render_manifest"]["counts"]["buildings"] = 3
        self.assertIn(
            "render manifest building count differs from the source objects inventory",
            self.pin_failures(fixture),
        )

    def test_obstacle_basis_count_mismatch_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["traffic"]["visual_obstacle_basis"]["rendered_footprint_count"] = 3
        self.assertIn(
            "traffic obstacle basis does not cover all 2 rendered footprints",
            self.pin_failures(fixture),
        )

    def test_empty_source_inventory_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["objects"]["buildings"] = []
        self.assertIn("objects.json building inventory is empty", self.pin_failures(fixture))

    def test_objects_pin_mismatch_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["traffic"]["visual_obstacle_basis"]["objects_json_sha256"] = "stale-objects"
        self.assertIn(
            "traffic obstacle basis objects pin differs from the render manifest pin",
            self.pin_failures(fixture),
        )

    def test_render_manifest_pin_mismatch_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["traffic"]["visual_obstacle_basis"]["render_manifest_sha256"] = "stale-manifest"
        self.assertIn(
            "traffic obstacle basis render manifest pin is stale",
            self.pin_failures(fixture),
        )

    def test_missing_manifest_inventory_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        del fixture["render_manifest"]["buildings"]
        self.assertIn("render manifest building inventory is empty", self.pin_failures(fixture))

    def test_missing_manifest_building_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["render_manifest"]["buildings"].pop()
        self.assertIn(
            "render manifest actual building count differs from the source objects inventory",
            self.pin_failures(fixture),
        )

    def test_mismatched_manifest_identifier_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["render_manifest"]["buildings"][1]["object_id"] = "missing.source.id"
        self.assertIn(
            "render manifest building IDs differ from the source objects inventory",
            self.pin_failures(fixture),
        )

    def test_missing_pack_source_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        del fixture["render_manifest"]["scene"]["mesh_pack_source_sha256"]
        del fixture["traffic"]["mesh_pack_source_sha256"]
        del fixture["traffic"]["visual_obstacle_basis"]["pack_source_sha256"]
        self.assertIn(
            "render manifest mesh pack source pin is missing",
            self.pin_failures(fixture),
        )

    def test_objects_inventory_mismatch_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["objects"]["buildings"].pop()
        failures = self.pin_failures(fixture)
        self.assertIn(
            "render manifest actual building count differs from the source objects inventory",
            failures,
        )
        self.assertIn(
            "traffic obstacle basis does not cover all 1 rendered footprints",
            failures,
        )

    def test_duplicate_source_and_manifest_ids_are_rejected(self) -> None:
        fixture = copy.deepcopy(self.artifacts)
        fixture["objects"]["buildings"][1]["object_id"] = "building.one"
        fixture["render_manifest"]["buildings"][1]["object_id"] = "building.one"
        failures = self.pin_failures(fixture)
        self.assertIn("objects.json building inventory has duplicate object IDs", failures)
        self.assertIn("render manifest building inventory has duplicate object IDs", failures)

    def test_placement_and_render_pack_pin_drift_are_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["building_placement_sha256"] = "stale-placement"
        traffic["visual_obstacle_basis"]["pack_source_sha256"] = "stale-pack"
        report = self.audit_with(traffic=traffic)
        self.assert_failed_with(report, "traffic placement pin is stale")
        self.assert_failed_with(
            report,
            "traffic obstacle basis pack source differs from the render manifest or traffic source pin",
        )

    def test_stale_road_recording_pin_is_rejected(self) -> None:
        road = copy.deepcopy(self.artifacts["road"])
        road["traffic_recorded_building_placement_sha256"] = "stale-placement"
        self.assert_failed_with(
            self.audit_with(road=road),
            "road traffic_recorded placement pin is stale",
        )

    def test_downgraded_route_obstacle_basis_is_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["visual_obstacle_basis"]["route_obstacle_basis"] = "placement-proxies-only"
        self.assert_failed_with(
            self.audit_with(traffic=traffic),
            "routes were not recorded against the union obstacle basis",
        )

    def test_vehicle_inside_rendered_footprint_is_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["frames"][0]["vehicles"][0][1:3] = [75, -75]
        self.assert_failed_with(
            self.audit_with(traffic=traffic),
            "samples inside rendered building footprints",
        )

    def test_tampered_footprint_swallowing_a_sample_is_rejected(self) -> None:
        objects = copy.deepcopy(self.artifacts["objects"])
        objects["buildings"].append({
            "object_id": "building.tampered",
            "footprint_enu_m": [[-2, 2], [2, 2], [2, -2], [-2, -2]],
        })
        self.assert_failed_with(
            self.audit_with(objects=objects),
            "samples inside rendered building footprints",
        )

    def test_person_off_rendered_surface_is_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["frames"][0]["persons"][0][1:3] = [500, 500]
        self.assert_failed_with(
            self.audit_with(traffic=traffic),
            "person samples off the road/walkbed",
        )

    def test_required_actor_classes_are_rejected_when_missing(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        for frame in traffic["frames"]:
            frame["vehicles"] = [row for row in frame["vehicles"] if row[4] != "bicycle"]
        self.assert_failed_with(self.audit_with(traffic=traffic), "no bicycles remain")

        traffic = copy.deepcopy(self.artifacts["traffic"])
        for frame in traffic["frames"]:
            frame["persons"] = []
        self.assert_failed_with(self.audit_with(traffic=traffic), "no pedestrians remain")

    def test_minimum_displayed_vehicle_count_is_rejected_when_missing(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        for frame in traffic["frames"]:
            frame["vehicles"] = [row for row in frame["vehicles"] if row[4] == "bicycle"]
        self.assert_failed_with(self.audit_with(traffic=traffic), "fewer than 50 vehicles")

    def test_frozen_legal_signal_state_is_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["frames"][1]["tls"] = {"signal.0": "G"}
        self.assert_failed_with(
            self.audit_with(traffic=traffic),
            "network phase at the sample time",
        )

    def test_signal_state_outside_native_program_is_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["frames"][1]["tls"] = {"signal.0": "X"}
        self.assert_failed_with(
            self.audit_with(traffic=traffic),
            "outside the network phase programs",
        )

    def test_bicycle_on_pedestrian_only_lane_is_rejected(self) -> None:
        traffic = copy.deepcopy(self.artifacts["traffic"])
        traffic["bicycle_lane_use"]["bicycle.0"] = ["walk_0"]
        self.assert_failed_with(
            self.audit_with(traffic=traffic),
            "route legality violations",
        )

    def test_missing_or_duplicate_cycle_selection_is_rejected(self) -> None:
        for cycles in ([0, 1, 2], [0, 1, 2, 3, 4, 4]):
            with self.subTest(cycles=cycles):
                traffic = copy.deepcopy(self.artifacts["traffic"])
                traffic["paved_bicycle_cycle_indices"] = cycles
                self.assert_failed_with(
                    self.audit_with(traffic=traffic),
                    "painted bicycle cycle selection",
                )


if __name__ == "__main__":
    unittest.main()
