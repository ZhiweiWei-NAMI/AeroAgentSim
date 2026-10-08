"""Actual integration bounds, braking, refusal and configured fleet capacity."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Instant
from aerokernel.state import Fact

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

POS = "he.aircraft.position_enu_m"
VEL = "aas.p1.velocity_enu_m_s"
ENERGY = "aas.p1.energy_j"


def test_bounded_flight_hold_and_stop_receipts(document: dict[str, Any]) -> None:
    simulation = Simulation(load_scenario(document))
    simulation.start()
    view = simulation.run_until(3_000_000_000)
    ref = simulation.scenario.manifest.entities[0]
    fact = view.field((ref, VEL), view.instant)
    assert isinstance(fact, Fact) and fact.value == (10.0, 0.0, 0.0)
    before = view.field((ref, POS), view.instant)
    assert isinstance(before, Fact)
    command = simulation.kernel.submit(
        CommandRequest(
            "aas.motion.hold",
            "motion",
            Instant(3_000_000_001),
            {"entity": ref.id, "machine": "manual"},
        )
    )
    simulation.run_until(5_200_000_000)
    assert simulation.kernel.action(command).status == "succeeded"
    view = simulation.kernel.view()
    velocity = view.field((ref, VEL), view.instant)
    assert isinstance(velocity, Fact) and velocity.value == (0.0, 0.0, 0.0)
    samples = [
        c
        for r in simulation.kernel.records
        for c in project(r)["facts"]
        if c["entity"]["id"] == ref.id and c["fieldId"] == VEL
    ]
    times: list[int] = []
    speeds: list[float] = []
    for sample in samples:
        at = int(sample["validFrom"]["ns"])
        if times and at == times[-1]:
            continue
        times.append(at)
        speeds.append(math.sqrt(sum(v * v for v in sample["value"])))
    assert max(speeds) <= 10.0
    for i in range(1, len(speeds)):
        assert (
            abs(speeds[i] - speeds[i - 1])
            <= 5.0 * ((times[i] - times[i - 1]) / 1e9) + 1e-9
        )
    stop = simulation.kernel.submit(
        CommandRequest(
            "aas.motion.stop",
            "motion",
            Instant(5_200_000_001),
            {"entity": ref.id, "machine": "manual"},
        )
    )
    simulation.run_until(5_400_000_000)
    assert simulation.kernel.action(stop).status == "succeeded"
    simulation.close()


def test_insufficient_energy_rejects_route_without_flight(
    document: dict[str, Any],
) -> None:
    document["entities"][0]["facts"][ENERGY] = 50.0
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(1_000_000_000)
    commits = [project(record) for record in simulation.kernel.records]
    assert any(
        receipt["status"] == "rejected"
        and receipt["result"]["reason"] == "insufficient energy for bounded trajectory"
        for c in commits
        for receipt in c["receipts"]
        if receipt.get("result")
    )
    assert not [
        m
        for c in commits
        for m in c["messages"]
        if m["schemaId"] == "aas.motion.arrived"
    ]
    simulation.close()


def test_thousand_entities_integrate_actual_initial_velocity() -> None:
    scenario = load_scenario(Path("scenarios/p1-scale.yaml"))
    simulation = Simulation(scenario)
    simulation.start()
    view = simulation.run_until(2_000_000_000)
    assert len(scenario.manifest.entities) == 1000
    for ref in scenario.manifest.entities:
        position = view.field((ref, POS), view.instant)
        energy = view.field((ref, ENERGY), view.instant)
        assert isinstance(position, Fact) and isinstance(energy, Fact)
        assert position.value[0] == 20.0  # type: ignore[index]
        assert energy.value == pytest.approx(99860.0)
    simulation.close()
