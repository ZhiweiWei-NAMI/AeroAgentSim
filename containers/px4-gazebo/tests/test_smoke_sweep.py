#!/usr/bin/env python3
"""P2b sweep-client tests against the ACTUAL workspace smoke.py (absolute importlib).

No real sockets: Client/ready_client are replaced by a scripted FakeClient and
sys.argv is mocked; smoke.main() only writes a JSON file to a temp dir.

Run: PYTHONDONTWRITEBYTECODE=1 /mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python test_smoke_sweep.py
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SMOKE_PATH = Path(__file__).resolve().parents[1] / "smoke.py"
PHYSICS_STEP_NS = 4_000_000
PROFILE_SEQ = [
    {"runtime_total": 100, "command_dispatch": 10, "rpc_decode": 1},
    {"runtime_total": 200, "command_dispatch": 20, "rpc_decode": 2},
    {"runtime_total": 300, "command_dispatch": 30},
    {"runtime_total": 400, "command_dispatch": 40},
]

_spec = importlib.util.spec_from_file_location("actual_smoke", SMOKE_PATH)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def samples(sim_ns, offset_ms):
    """One vehicle's telemetry sample; position depends on the cadence offset."""
    return [
        {
            "vehicle": "v0",
            "sim_ns": sim_ns,
            "position_enu": [50.0 + offset_ms, 0.0, 10.0],
            "armed": False,
            "landed_state": "ON_GROUND",
            "freshness": {
                "gazebo_pose_age_sim_ns": 0,
                "mavsdk_receipt_age_wall_ns": {"v0": 1_000},
            },
        }
    ]


class FakeClient:
    """Scripted smoke.Client stand-in; never opens a socket."""

    fail_close = False

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.pending = set()
        self.closed = False
        self.profile_seq = list(PROFILE_SEQ)
        self.profiled_advances = 0

    def request(self, op, payload):
        if op == "hello":
            return {"protocol": smoke.PROTOCOL}
        if op == "reset":
            assert payload["warmup"] % PHYSICS_STEP_NS == 0
            return {
                "reached_sim_ns": 0,
                "physics_step_ns": PHYSICS_STEP_NS,
                "telemetry": samples(0, 0.0),
            }
        if op == "command":
            self.pending.add(payload["command_id"])
            return {"status": "accepted"}
        if op == "advance":
            target = payload["to_sim_ns"]
            updates = [
                {"command_id": cid, "status": "succeeded"}
                for cid in sorted(self.pending)
            ]
            self.pending = set()
            result = {
                "reached_sim_ns": target,
                "telemetry": samples(target, target // 1_000_000),
                "contacts": [],
                "command_updates": updates,
            }
            if self.profile_seq:
                result["profile_wall_ns"] = self.profile_seq.pop(0)
                self.profiled_advances += 1
            return result
        raise AssertionError(f"unexpected op {op}")

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close boom")


def run_main(argv, fake):
    """Patch ready_client + sys.argv, run smoke.main(), return (status, output)."""
    with tempfile.TemporaryDirectory(prefix="p2b-test-") as directory:
        out = Path(directory) / "out.json"
        full = ["smoke.py", "--port", "19000", "--output", str(out)] + list(argv)

        def fake_ready(args):
            return fake, {"protocol": smoke.PROTOCOL}

        buffer = io.StringIO()
        with mock.patch.object(smoke, "ready_client", fake_ready), mock.patch.object(
            sys, "argv", full
        ), contextlib.redirect_stdout(buffer):
            status = smoke.main()
        return status, json.loads(out.read_text()), buffer


class ParseStepMsTests(unittest.TestCase):
    def test_comma_and_single_forms(self):
        self.assertEqual(smoke.parse_step_ms("20"), [20])
        self.assertEqual(smoke.parse_step_ms("4,20,100,200"), [4, 20, 100, 200])
        self.assertEqual(smoke.parse_step_ms(" 4 , 20 "), [4, 20])

    def test_rejects_bad_values(self):
        for value in ("0", "-5", "abc", "", "4,", "20,20", "4,,20"):
            with self.assertRaises(argparse_type_error(), msg=value):
                smoke.parse_step_ms(value)


def argparse_type_error():
    return smoke.argparse.ArgumentTypeError


class SingleCadenceTests(unittest.TestCase):
    def test_historical_schema_and_same_cadence_comparison(self):
        status, output, _ = run_main(
            ["--step-ms", "4", "--runs", "2"], FakeClient("h", 1)
        )
        self.assertEqual(status, 0)
        self.assertEqual(output["step_ns"], 4_000_000)
        self.assertNotIn("cadences", output)
        self.assertEqual(len(output["runs"]), 2)
        self.assertTrue(all(r["success"] for r in output["runs"]))
        comparisons = output["trajectory_comparisons"]
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0]["position_rms_m"], 0.0)
        self.assertTrue(comparisons[0]["bitwise_identical_positions"])


class SweepGroupingTests(unittest.TestCase):
    def test_sweep_groups_comparisons_per_cadence_only(self):
        """Positions are cadence-dependent, so cross-cadence comparison would be nonzero."""
        status, output, _ = run_main(
            ["--step-ms", "4,8", "--runs", "2"], FakeClient("h", 1)
        )
        self.assertEqual(status, 0)
        self.assertNotIn("step_ns", output)
        self.assertNotIn("runs", output)
        self.assertNotIn("trajectory_comparisons", output)
        self.assertEqual([g["step_ms"] for g in output["cadences"]], [4, 8])
        self.assertEqual(
            [g["step_ns"] for g in output["cadences"]], [4_000_000, 8_000_000]
        )
        for group in output["cadences"]:
            self.assertEqual(len(group["runs"]), 2)
            self.assertTrue(all(r["success"] for r in group["runs"]))
            comparisons = group["trajectory_comparisons"]
            self.assertEqual(len(comparisons), 1)
            self.assertEqual(comparisons[0]["position_rms_m"], 0.0)
            self.assertTrue(comparisons[0]["bitwise_identical_positions"])


class CompareTests(unittest.TestCase):
    def test_compare_only_pairs_runs_and_reports_zero_for_identical(self):
        trajectory = lambda x: [  # noqa: E731
            {
                "sim_ns": 1_000_000,
                "telemetry": [{"vehicle": "v0", "position_enu": [x, 0, 10]}],
            }
        ]
        self.assertEqual(smoke.compare([{"trajectory": trajectory(1.0)}]), [])
        same = smoke.compare(
            [{"trajectory": trajectory(1.0)}, {"trajectory": trajectory(1.0)}]
        )
        self.assertEqual(same[0]["position_rms_m"], 0.0)
        self.assertTrue(same[0]["bitwise_identical_positions"])
        self.assertEqual(same[0]["shared_vehicle_samples"], 1)
        diff = smoke.compare(
            [{"trajectory": trajectory(1.0)}, {"trajectory": trajectory(4.0)}]
        )
        self.assertEqual(diff[0]["position_rms_m"], 3.0)
        self.assertEqual(diff[0]["position_max_m"], 3.0)
        self.assertFalse(diff[0]["bitwise_identical_positions"])


class InjectedFailureTests(unittest.TestCase):
    def test_run_failure_keeps_partial_output_and_marks_failed(self):
        def fake_run(args, number, step_ms, record):
            if number == 1:
                raise RuntimeError("injected")
            record["success"] = True

        with mock.patch.object(smoke, "run", side_effect=fake_run):
            status, output, _ = run_main(
                ["--step-ms", "4", "--runs", "3"], FakeClient("h", 1)
            )
        self.assertEqual(status, 1)
        # The loop aborts at the failing run: only started runs land in output.
        self.assertEqual(len(output["runs"]), 2)
        self.assertTrue(output["runs"][0]["success"])
        self.assertFalse(output["runs"][1]["success"])
        self.assertEqual(output["error"]["type"], "RuntimeError")
        self.assertIn("injected", output["error"]["message"])

    def test_sweep_run_failure_aborts_remaining_cadences_but_writes_output(self):
        def fake_run(args, number, step_ms, record):
            if step_ms == 8:
                raise RuntimeError("injected sweep")
            record["success"] = True

        with mock.patch.object(smoke, "run", side_effect=fake_run):
            status, output, _ = run_main(
                ["--step-ms", "4,8,16", "--runs", "1"], FakeClient("h", 1)
            )
        self.assertEqual(status, 1)
        self.assertEqual([g["step_ms"] for g in output["cadences"]], [4, 8])
        self.assertTrue(output["cadences"][0]["runs"][0]["success"])
        self.assertFalse(output["cadences"][1]["runs"][0]["success"])
        self.assertEqual(output["error"]["type"], "RuntimeError")


class CleanupFailureTests(unittest.TestCase):
    def test_cleanup_failure_marks_success_false(self):
        fake = FakeClient("h", 1)
        fake.fail_close = True
        status, output, _ = run_main(["--step-ms", "4", "--runs", "1"], fake)
        self.assertEqual(status, 1)
        record = output["runs"][0]
        self.assertFalse(record["success"])
        self.assertEqual(record["cleanup_error"]["type"], "RuntimeError")
        self.assertEqual(record["cleanup_error"]["message"], "close boom")
        self.assertEqual(output["error"]["type"], "RuntimeError")
        self.assertTrue(fake.closed)


class ProfileSummaryTests(unittest.TestCase):
    def test_summary_retains_only_actual_phases(self):
        profiles = [
            {"a": 100, "b": 1},
            {"a": 300},
            {"a": 200, "b": 3},
        ]
        summary = smoke.barrier_profile_summary(profiles)
        self.assertEqual(summary["advance_count"], 3)
        self.assertEqual(set(summary["phases"]), {"a", "b"})
        self.assertEqual(
            summary["phases"]["a"],
            {"count": 3, "min": 100, "median": 200, "p95": 300, "max": 300},
        )
        self.assertEqual(
            summary["phases"]["b"],
            {"count": 2, "min": 1, "median": 2, "p95": 3, "max": 3},
        )
        self.assertIsNone(smoke.barrier_profile_summary([]))
        self.assertIsNone(smoke.summarize([]))
        self.assertEqual(
            smoke.summarize([5]),
            {"min": 5, "median": 5, "p95": 5, "max": 5},
        )

    def test_barrier_profile_validation(self):
        self.assertIsNone(smoke.barrier_profile({"reached_sim_ns": 0}))
        profile = {"runtime_total": 1000}
        self.assertEqual(smoke.barrier_profile({"profile_wall_ns": profile}), profile)
        for bad in (
            [{"a": 1}],
            {"a": -1},
            {"a": 1.5},
            {"a": True},
            {1: 5},
        ):
            with self.assertRaises(ValueError, msg=repr(bad)):
                smoke.barrier_profile({"profile_wall_ns": bad})

    def test_run_records_summary_and_omits_absent_profile(self):
        fake = FakeClient("h", 1)
        status, output, _ = run_main(["--step-ms", "4", "--runs", "1"], fake)
        self.assertEqual(status, 0)
        record = output["runs"][0]
        self.assertEqual(fake.profiled_advances, 4)
        self.assertNotIn("barrier_profiles", record)
        self.assertIn("barrier_profile", record)
        summary = record["barrier_profile"]
        self.assertEqual(summary["advance_count"], 4)
        self.assertEqual(
            set(summary["phases"]), {"runtime_total", "command_dispatch", "rpc_decode"}
        )
        self.assertEqual(
            summary["phases"]["runtime_total"],
            {"count": 4, "min": 100, "median": 250, "p95": 400, "max": 400},
        )
        self.assertNotIn("telemetry_projection", summary["phases"])

    def test_run_without_profile_omits_barrier_profile_key(self):
        fake = FakeClient("h", 1)
        fake.profile_seq = []
        status, output, _ = run_main(["--step-ms", "4", "--runs", "1"], fake)
        self.assertEqual(status, 0)
        record = output["runs"][0]
        self.assertEqual(fake.profiled_advances, 0)
        self.assertNotIn("barrier_profile", record)
        self.assertNotIn("barrier_profiles", record)
        self.assertTrue(record["success"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
