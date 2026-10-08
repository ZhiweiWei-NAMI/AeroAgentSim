"""Full P5 kinematic gate and native PX4 contract for orchestrator execution."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest
from aerokernel import Fact, Instant, Journal, replay

from aeroagentsim.adapters.runner import AdapterRun
from aeroagentsim.packs.common import distance, vec
from aeroagentsim.packs.metrics import compute
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

from .conftest import SCENARIOS


def test_logistics_small_30_orders() -> None:
    scenario = load_scenario(SCENARIOS / "logistics-small.yaml")
    sim = Simulation(scenario)
    try:
        sim.start()
        sim.run_until(scenario.until_ns)
        report = compute(sim.kernel)["logistics"]
        assert report["released"] == report["delivered"] == report["accepted"] == 30
        assert report["on_time_rate"] == 1
        assert 0 < report["utilization"] <= 1
        assert report["energy_per_parcel_j"] > 0
        assert (
            len(
                sim.kernel.view().relations(
                    "packs.parcel.custodian", sim.kernel.view().instant
                )
            )
            == 30
        )
    finally:
        sim.close()


def test_px4_scenario_compiles_without_docker() -> None:
    scenario = load_scenario(SCENARIOS / "logistics-px4.yaml")
    generator = runpy.run_path(str(SCENARIOS / "generate.py"))
    assert generator["px4"](scenario.document["registry"]["digest"]) == dict(
        scenario.document
    )
    assert scenario.until_ns == 300_000_000_000
    assert scenario.engines["flight"]["plugin"] == "px4_gazebo"
    assert (
        scenario.engines["logistics"]["config"]["energy"]["source"]
        == "battery_fraction"
    )
    # Launch is receipt-driven: a fixed arm-to-takeoff delay lets PX4's
    # preflight auto-disarm expire before takeoff (the original regression).
    assert scenario.document["bindings"]["commands"] == []
    states = scenario.engines["launch"]["config"]["machines"][0]["states"]
    assert states["arming"]["transitions"][0] == {
        "to": "taking_off",
        "receipt": {"status": "succeeded", "children": ["arm"], "policy": "all"},
    }
    assert states["taking_off"]["transitions"][0]["receipt"]["children"] == ["takeoff"]


@pytest.mark.docker
def test_logistics_px4_native_parcel_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real telemetry/receipts gate pickup and delivery; no timer-only transfers."""
    from aeroagentsim.adapters import container

    monkeypatch.setattr(container, "_JOB_LABEL_VALUE", "p5f")
    scenario = load_scenario(SCENARIOS / "logistics-px4.yaml")
    path = tmp_path / "journal.jsonl"
    with AdapterRun(scenario, containers=True, journal=Journal(path)) as run:
        run.start()
        run.run_until(scenario.until_ns)
        view = run.kernel.view()
        assert view.instant.ns == 300_000_000_000
        transitions: dict[str, Instant] = {}
        commands: list[dict[str, Any]] = []
        for record in run.kernel.records[1:]:
            for m in project(record)["messages"]:
                if m["schemaId"] == "packs.logistics.transition":
                    transitions[m["payload"]["state"]] = Instant(
                        int(m["at"]["ns"]), m["at"]["microstep"]
                    )
                elif m["kind"] == "command":
                    commands.append(m)
        assert list(transitions) == [
            "queued",
            "pickup",
            "in_transit",
            "handoff",
            "delivered",
            "accepted",
        ], transitions
        times = {state: at.ns / 1e9 for state, at in transitions.items()}
        assert 56 <= times["in_transit"] <= 75
        assert 110 <= times["delivered"] <= 150
        assert times["accepted"] > times["delivered"]
        ref = next(r for r in scenario.manifest.entities if r.id == "carrier-1")
        assert [m["schemaId"].rsplit(".", 1)[-1] for m in commands] == [
            "arm",
            "takeoff",
            "goto",
            "goto",
            "land",
        ]
        for i, m in enumerate(commands):
            action = view.action(m["id"])
            assert action.status == "succeeded", action
            result = action.history[-1]["result"]
            assert result["status"] == "succeeded" and result["vehicle"] == "u1"
            if i + 1 < len(commands):
                assert result["available_ns"] <= int(commands[i + 1]["at"]["ns"])

        cfg = scenario.engines["logistics"]["config"]
        measurements = {}
        for state, facility, motion_index in (
            ("in_transit", "depot", 2),
            ("handoff", "locker", 3),
        ):
            transfer = transitions[state]
            dwell = scenario.initial[facility]["packs.facility.dwell_ns"]
            start = transfer.ns - dwell
            terminal = view.action(commands[motion_index]["id"]).history[-1]["result"]
            assert terminal["available_ns"] <= start
            target = vec(scenario.initial[facility]["packs.facility.position"])
            errors, speeds = [], []
            # Check the same 0.5s polling windows used by the live engine;
            # each sample is an actual committed native fact, never resampled.
            for at_ns in range(start, transfer.ns + 1, cfg["poll_ns"]):
                at = Instant(at_ns)
                pose_fact = view.field((ref, "he.aircraft.position_enu_m"), at)
                velocity = view.field((ref, "e1.px4.velocity"), at)
                armed = view.field((ref, "e1.px4.armed"), at)
                landed = view.field((ref, "e1.px4.landed"), at)
                assert isinstance(pose_fact, Fact) and isinstance(velocity, Fact)
                assert isinstance(armed, Fact) and armed.value is True
                assert isinstance(landed, Fact) and landed.value == "IN_AIR"
                error = distance(vec(pose_fact.value), target)
                speed = distance(vec(velocity.value), (0.0, 0.0, 0.0))
                assert error <= cfg["arrival_radius_m"]
                assert speed <= cfg["stopped_speed_m_s"]
                errors.append(error)
                speeds.append(speed)
            measurements[state] = {
                "tick_s": transfer.ns / 1e9,
                "position_error_m": errors[-1],
                "max_dwell_error_m": max(errors),
                "max_dwell_speed_m_s": max(speeds),
            }

        # Three explicit custodians, and independent acceptance preserves
        # destination custody. Retain full edge/field identities for replay.
        custody = {}
        for state, holder in (
            ("pickup", "depot"),
            ("in_transit", "carrier-1"),
            ("handoff", "locker"),
            ("accepted", "locker"),
        ):
            edges = view.relations("packs.parcel.custodian", transitions[state])
            assert len(edges) == 1 and edges[0].target.id == holder
            custody[state] = edges
        pose = view.field((ref, "he.aircraft.position_enu_m"), view.instant)
        assert isinstance(pose, Fact) and vec(pose.value)[0] > 38
        phase = view.field((ref, "packs.flight.phase"), view.instant)
        landed = view.field((ref, "e1.px4.landed"), view.instant)
        armed = view.field((ref, "e1.px4.armed"), view.instant)
        assert isinstance(phase, Fact) and phase.value == "landed"
        assert isinstance(landed, Fact) and landed.value == "ON_GROUND"
        assert isinstance(armed, Fact) and armed.value is False
        live = compute(run.kernel)
        (tmp_path / "measurements.json").write_text(
            json.dumps({"times_s": times, "transfers": measurements}, indent=2)
        )
    offline = replay(path)
    assert not offline.incomplete
    assert offline.view().instant == view.instant
    for state, edges in custody.items():
        assert (
            offline.view().relations("packs.parcel.custodian", transitions[state])
            == edges
        )
    for field in scenario.engines["flight"]["config"]["fields"].values():
        assert offline.view().field((ref, field), view.instant) == view.field(
            (ref, field), view.instant
        )
    for m in commands:
        assert offline.view().action(m["id"]) == view.action(m["id"])
    assert compute(offline) == live
    assert live["logistics"]["delivered"] == live["logistics"]["accepted"] == 1
