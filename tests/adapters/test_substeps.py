"""Wire fixtures verify substep aggregation; real flights live in tests/packs."""

from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Fact, Instant, KernelError

from aeroagentsim.adapters.runner import AdapterRun
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

from .fake_backend import FakeBackend

STEP = 20_000_000
BOUNDARY = 200_000_000
ROOT = Path(__file__).resolve().parents[2] / "scenarios" / "adapters"


def px4_sample(ns: int) -> dict[str, Any]:
    return {
        "vehicle": "u1",
        "sim_ns": ns,
        "position_enu": [1.0, 2.0, 3.0],
        "velocity_enu": [0.0, 0.0, 0.0],
        "attitude_quat": [0.0, 0.0, 0.0, 1.0],
        "battery": {"remaining_fraction": 0.9, "voltage_v": 16.0},
        "armed": ns >= BOUNDARY + STEP,
        "flight_mode": "HOLD",
        "landed_state": "ON_GROUND",
        "freshness": {"mavsdk_source_sim_ns": None},
    }


def response(request: dict[str, Any]) -> dict[str, Any] | None:
    if request["op"] == "hello":
        return {
            "protocol": request["protocol"],
            "major": 1,
            "minor": 0,
            "capabilities": {
                "timing": "lockstep",
                "exact_stop": True,
                "hold": True,
                "physics_step_ns": 4_000_000,
                "max_frame_bytes": 8 * 1024 * 1024,
                "vehicles": ["x500"],
                "sensors": ["gazebo_pose", "mavsdk_telemetry", "gazebo_contacts"],
                "actions": [
                    "arm",
                    "takeoff",
                    "goto_location",
                    "hold",
                    "land",
                    "disarm",
                ],
            },
        }
    if request["op"] == "reset":
        return {
            "reached_sim_ns": 0,
            "physics_step_ns": 4_000_000,
            "warmup_sim_ns": 10_000_000_000,
            "vehicles": ["u1"],
            "telemetry": [px4_sample(0)],
        }
    if request["op"] == "close":
        return {"closed": True}
    return None


def test_native_substeps_preserve_events_receipts_and_atomic_publication() -> None:
    pending: list[dict[str, Any]] = []

    def handle(request: dict[str, Any]) -> dict[str, Any]:
        common = response(request)
        if common is not None:
            return common
        if request["op"] == "command":
            pending.append(request["payload"])
            return {
                "status": "accepted",
                "command_id": pending[-1]["command_id"],
                "sim_ns": BOUNDARY,
            }
        ns = request["payload"]["to_sim_ns"]
        updates = []
        if pending and ns in (BOUNDARY + STEP, BOUNDARY + 2 * STEP):
            updates = [
                {
                    "command_id": pending[0]["command_id"],
                    "vehicle": "u1",
                    "action": "arm",
                    "sim_ns": ns,
                    "status": "running" if ns == BOUNDARY + STEP else "succeeded",
                }
            ]
        contacts = []
        if ns in (BOUNDARY + 2 * STEP, BOUNDARY + 8 * STEP):
            contacts = [
                {
                    # A delayed native contact predates the last communication
                    # boundary; its acquisition time must not be rewritten.
                    "sim_ns": 160_000_000
                    if ns == BOUNDARY + 2 * STEP
                    else ns - 4_000_000,
                    "topic": "contact",
                    "collision1": "a",
                    "collision2": "b",
                }
            ]
        return {
            "reached_sim_ns": ns,
            "telemetry": [px4_sample(ns)],
            "contacts": contacts,
            "command_updates": updates,
        }

    server = FakeBackend(handle)
    scenario = load_scenario(ROOT / "px4-flight.yaml")
    cfg = scenario.engines["flight"]["config"]
    cfg["host"], cfg["port"] = server.endpoint
    cfg["control_step_ns"] = STEP
    try:
        with AdapterRun(scenario) as run:
            run.start()
            cid = run.kernel.submit(
                CommandRequest(
                    "adapters.px4_gazebo.arm",
                    "flight",
                    Instant(1),
                    {"entity": "aircraft"},
                )
            )
            run.run_until(2 * BOUNDARY)
            advances = [
                r["payload"]["to_sim_ns"]
                for r in server.requests
                if r["op"] == "advance"
            ]
            assert advances == list(range(STEP, 2 * BOUNDARY + 1, STEP))
            result = run.kernel.view().action(cid).history[-1]["result"]
            assert result["sim_ns"] == BOUNDARY + 2 * STEP
            assert result["available_ns"] == 2 * BOUNDARY
            frames = [project(r) for r in run.kernel.records]
            events = [
                m
                for f in frames
                for m in f["messages"]
                if m["schemaId"] == "adapters.px4_gazebo.contact"
            ]
            assert [int(m["at"]["ns"]) for m in events] == [160_000_000, 356_000_000]
            assert all(m["payload"]["available_ns"] == 2 * BOUNDARY for m in events)
            assert {
                int(f["available"]["ns"]) for frame in frames for f in frame["facts"]
            } == {0, BOUNDARY, 2 * BOUNDARY}
    finally:
        server.close()


@pytest.mark.parametrize("bad", ["frontier", "future_event", "past_event"])
def test_bad_control_step_never_publishes_partial_observations(bad: str) -> None:
    def handle(request: dict[str, Any]) -> dict[str, Any]:
        common = response(request)
        if common is not None:
            return common
        ns = request["payload"]["to_sim_ns"]
        contacts = []
        if bad != "frontier":
            contacts = [
                {
                    "sim_ns": ns + 1 if bad == "future_event" else -1,
                    "topic": "contact",
                    "collision1": "a",
                    "collision2": "b",
                }
            ]
        return {
            "reached_sim_ns": ns + 1 if bad == "frontier" else ns,
            "telemetry": [px4_sample(ns)],
            "contacts": contacts,
            "command_updates": [],
        }

    server = FakeBackend(handle)
    scenario = load_scenario(ROOT / "px4-flight.yaml")
    cfg = scenario.engines["flight"]["config"]
    cfg["host"], cfg["port"] = server.endpoint
    cfg["control_step_ns"] = STEP
    try:
        with AdapterRun(scenario) as run:
            run.start()
            ref = scenario.manifest.entities[0]
            before = run.kernel.view().field((ref, "e1.px4.position"), Instant(0))
            with pytest.raises(KernelError):
                run.run_until(BOUNDARY)
            after = run.kernel.view().field((ref, "e1.px4.position"), Instant(BOUNDARY))
            assert isinstance(before, Fact) and isinstance(after, Fact)
            assert after.version == before.version
            assert len([r for r in server.requests if r["op"] == "advance"]) == 1
    finally:
        server.close()
