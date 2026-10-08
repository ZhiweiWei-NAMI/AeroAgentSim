"""Physical/business separation, assignment constraints and temporal custody."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Fact, Instant, Interval, Journal, Stamp, replay
from aerokernel.errors import KernelError
from aerokernel.sdk import EngineContext

from aeroagentsim.packs.logistics import Logistics
from aeroagentsim.packs.metrics import compute, metrics
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

from .conftest import SCENARIOS, no_acceptance, simulation

SECOND = 1_000_000_000


def state(sim: Simulation) -> str:
    view = sim.kernel.view()
    ref = next(r for r in sim.scenario.manifest.entities if r.id == "order-1")
    fact = view.field((ref, "packs.order.state"), view.instant)
    assert isinstance(fact, Fact) and isinstance(fact.value, str)
    return fact.value


def test_known_run_replay_determinism_and_metrics(
    small: dict[str, Any], tmp_path: Path
) -> None:
    scenario = load_scenario(small, base=SCENARIOS)
    sim = Simulation(scenario, journal=Journal(tmp_path / "journal.jsonl"))
    sim.start()
    sim.run_until(20 * SECOND)
    assert state(sim) == "accepted"
    edges = sim.kernel.view().relations(
        "packs.parcel.custodian", sim.kernel.view().instant
    )
    assert len(edges) == 1 and edges[0].target.id == "locker"
    live = compute(sim.kernel)
    sim.close()
    assert metrics(tmp_path) == live
    offline = replay(tmp_path / "journal.jsonl")
    assert (
        offline.view().relations("packs.parcel.custodian", offline.view().instant)
        == edges
    )
    for ref in scenario.manifest.entities:
        for field in scenario.initial[ref.id]:
            assert offline.view().field(
                (ref, field), offline.view().instant
            ) == sim.kernel.view().field((ref, field), sim.kernel.view().instant)
    other = Simulation(scenario)
    other.start()
    other.run_until(20 * SECOND)
    assert sim.kernel.records == other.kernel.records
    other.close()
    report = live["logistics"]
    assert report["delivered"] == report["accepted"] == report["released"] == 1
    assert report["on_time_rate"] == 1
    # Independent hand calculation from the configured trapezoid, native grid
    # latching, two 1s dwell windows and the 0.5s delivery acknowledgment.
    assert report["mean_delivery_time_s"] == pytest.approx(10.5)
    # At delivery t=10.5 the real motion sample is from t=10.4: 9.4s
    # of idle draw after assignment plus 40m * 2J/m = 174J.
    assert report["energy_per_parcel_j"] == pytest.approx(174.0)
    assert report["utilization"] == pytest.approx(9.5 / 20)


@pytest.mark.parametrize("constraint", ["energy", "restriction", "capacity"])
def test_feasibility_blocks_before_assignment(
    small: dict[str, Any], constraint: str
) -> None:
    c = small["engines"]["logistics"]["config"]
    c["policy"] = "external"
    if constraint == "energy":
        next(e for e in small["entities"] if e["id"] == "carrier-1")["facts"][
            "packs.energy"
        ] = 500.0
    elif constraint == "restriction":
        r = next(e for e in small["entities"] if e["id"] == "restriction-1")
        r["facts"].update(
            {
                "packs.restriction.lower": [10.0, -10.0, 0.0],
                "packs.restriction.upper": [20.0, 10.0, 20.0],
            }
        )
    else:
        c["orders"].append(
            {
                "order": "order-2",
                "parcel": "parcel-2",
                "source": "locker",
                "destination": "depot",
                "deadline_ns": 100 * SECOND,
            }
        )
        c["arrivals"]["times_ns"].append(100 * SECOND)
        small["entities"] += [
            {
                "id": "order-2",
                "type": "oo:Order",
                "facts": {"packs.order.state": "unreleased"},
            },
            {"id": "parcel-2", "type": "aas:Parcel", "facts": {}},
        ]
        next(e for e in small["entities"] if e["id"] == "locker")["facts"][
            "packs.facility.locker_capacity"
        ] = 1
    sim = simulation(small)
    try:
        sim.start()
        sim.run_until(SECOND)
        cid = sim.kernel.submit(
            CommandRequest(
                "packs.logistics.assign",
                "logistics",
                Instant(SECOND + 1),
                {"order": "order-1", "carrier": "carrier-1"},
            )
        )
        sim.run_until(2 * SECOND)
        assert sim.kernel.view().action(cid).status == "rejected"
        reasons = {
            "energy": "energy_infeasible",
            "restriction": "activity_restriction",
            "capacity": "locker_capacity",
        }
        assert (
            sim.kernel.view().action(cid).history[-1]["result"]["reason"]
            == reasons[constraint]
        )
        assert state(sim) == "queued"
        assert compute(sim.kernel)["logistics"]["mean_delivery_time_s"] is None
    finally:
        sim.close()


def test_arrival_and_handoff_do_not_accept_business(small: dict[str, Any]) -> None:
    no_acceptance(small)
    sim = simulation(small)
    try:
        sim.start()
        # Premature business acceptance must receive a real rejected receipt.
        cid = sim.kernel.submit(
            CommandRequest(
                "packs.logistics.decide",
                "logistics",
                Instant(SECOND),
                {"order": "order-1", "accepted": True},
            )
        )
        sim.run_until(12 * SECOND)
        assert sim.kernel.view().action(cid).status == "rejected"
        assert state(sim) == "delivered"
        cid = sim.kernel.submit(
            CommandRequest(
                "packs.logistics.decide",
                "logistics",
                Instant(12 * SECOND + 1),
                {"order": "order-1", "accepted": False},
            )
        )
        sim.run_until(13 * SECOND)
        assert sim.kernel.view().action(cid).status == "succeeded"
        assert state(sim) == "rejected"
    finally:
        sim.close()


def test_pad_dwell_requires_continuous_stopped_presence(small: dict[str, Any]) -> None:
    next(e for e in small["entities"] if e["id"] == "depot")["facts"][
        "packs.facility.dwell_ns"
    ] = 15 * SECOND
    sim = simulation(small)
    try:
        sim.start()
        sim.run_until(10 * SECOND)
        assert state(sim) == "pickup"
        edges = sim.kernel.view().relations(
            "packs.parcel.custodian", sim.kernel.view().instant
        )
        assert edges[0].target.id == "depot"
    finally:
        sim.close()


def test_real_native_rejection_is_an_order_failure(small: dict[str, Any]) -> None:
    # A real competing host command occupies the native carrier before the
    # pack's pickup command dispatch; no backend success/failure is mocked.
    small["bindings"]["commands"] = [
        {
            "schema": "packs.motion.move_to",
            "target": "motion",
            "at_ns": SECOND,
            "payload": {"entity": "carrier-1", "target": [20.0, 0.0, 10.0]},
        }
    ]
    sim = simulation(small)
    try:
        sim.start()
        sim.run_until(3 * SECOND)
        assert state(sim) == "failed"
        assert compute(sim.kernel)["logistics"]["delivered"] == 0
        edges = sim.kernel.view().relations(
            "packs.parcel.custodian", sim.kernel.view().instant
        )
        assert edges[0].target.id == "depot"
    finally:
        sim.close()


def test_dwell_resets_when_real_motion_leaves_the_pad(small: dict[str, Any]) -> None:
    next(e for e in small["entities"] if e["id"] == "depot")["facts"][
        "packs.facility.dwell_ns"
    ] = 5 * SECOND
    small["bindings"]["commands"] = [
        {
            "schema": "packs.motion.move_to",
            "target": "motion",
            "at_ns": 2 * SECOND,
            "payload": {"entity": "carrier-1", "target": [5.0, 0.0, 10.0]},
        },
        {
            "schema": "packs.motion.move_to",
            "target": "motion",
            "at_ns": 6 * SECOND,
            "payload": {"entity": "carrier-1", "target": [0.0, 0.0, 10.0]},
        },
    ]
    sim = simulation(small)
    try:
        sim.start()
        sim.run_until(10 * SECOND)
        assert state(sim) == "pickup"
        # A stale first-arrival timestamp would have completed dwell by now.
        edges = sim.kernel.view().relations(
            "packs.parcel.custodian", sim.kernel.view().instant
        )
        assert edges[0].target.id == "depot"
        sim.run_until(14 * SECOND)
        assert state(sim) == "in_transit"
    finally:
        sim.close()


def test_cancel_waits_for_native_cleanup_and_keeps_custody(
    small: dict[str, Any],
) -> None:
    sim = simulation(small)
    try:
        sim.start()
        sim.run_until(5 * SECOND)
        assert state(sim) == "in_transit"
        cid = sim.kernel.submit(
            CommandRequest(
                "packs.logistics.cancel",
                "logistics",
                Instant(5 * SECOND + 1),
                {"order": "order-1"},
            )
        )
        sim.run_until(9 * SECOND)
        assert sim.kernel.view().action(cid).status == "succeeded"
        assert state(sim) == "canceled"
        edges = sim.kernel.view().relations(
            "packs.parcel.custodian", sim.kernel.view().instant
        )
        assert edges[0].target.id == "carrier-1"
        transitions = [
            m["payload"]["state"]
            for r in sim.kernel.records[1:]
            for m in project(r)["messages"]
            if m["schemaId"] == "packs.logistics.transition"
        ]
        assert "canceling" in transitions and "delivered" not in transitions
    finally:
        sim.close()


@pytest.mark.parametrize("future", [False, True])
def test_custody_cardinality_violation_is_kernel_rejected(
    small: dict[str, Any], monkeypatch: pytest.MonkeyPatch, future: bool
) -> None:
    original = Logistics.bootstrap

    def bad_bootstrap(self: Logistics, ctx: EngineContext) -> None:
        original(self, ctx)
        job = self.jobs["order-1"]
        ctx.relate(
            "illegal-second-custodian",
            self.cfg["custody_relation"],
            job.parcel,
            job.destination,
            valid=Interval(Instant(5 * SECOND) if future else ctx.now, None),
            acquired=Stamp("canonical", 0, 1, "canonical"),
        )

    monkeypatch.setattr(Logistics, "bootstrap", bad_bootstrap)
    sim = simulation(small)
    try:
        with pytest.raises(KernelError, match="RELATION_.*(MAX|CARDINALITY)"):
            sim.start()
    finally:
        sim.close()
