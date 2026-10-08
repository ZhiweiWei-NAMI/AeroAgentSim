"""Full P5 kinematic gate and native PX4 contract for orchestrator execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerokernel import Fact, Journal, replay

from aeroagentsim.adapters.runner import AdapterRun
from aeroagentsim.packs.common import vec
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
    assert scenario.until_ns == 300_000_000_000
    assert scenario.engines["flight"]["plugin"] == "px4_gazebo"
    assert (
        scenario.engines["logistics"]["config"]["energy"]["source"]
        == "battery_fraction"
    )


@pytest.mark.docker
def test_logistics_px4_native_parcel_and_replay(tmp_path: Path) -> None:
    """Real telemetry/receipts gate pickup and delivery; no timer-only transfers."""
    scenario = load_scenario(SCENARIOS / "logistics-px4.yaml")
    path = tmp_path / "journal.jsonl"
    with AdapterRun(scenario, containers=True, journal=Journal(path)) as run:
        run.start()
        run.run_until(scenario.until_ns)
        states = [
            (int(m["at"]["ns"]), m["payload"]["state"])
            for r in run.kernel.records[1:]
            for m in project(r)["messages"]
            if m["schemaId"] == "packs.logistics.transition"
        ]
        times = {state: at / 1e9 for at, state in states}
        # Reference windows are expectations for the configured schedule, not
        # observed timings asserted by the worker without a real native run.
        assert 56 <= times["in_transit"] <= 75
        assert 110 <= times["delivered"] <= 150
        assert times["accepted"] > times["delivered"]
        ref = next(r for r in scenario.manifest.entities if r.id == "carrier-1")
        pose = run.kernel.view().field(
            (ref, "he.aircraft.position_enu_m"), run.kernel.view().instant
        )
        assert isinstance(pose, Fact) and vec(pose.value)[0] > 38
        live = compute(run.kernel)
    offline = replay(path)
    assert not offline.incomplete
    assert compute(offline) == live
    assert live["logistics"]["delivered"] == live["logistics"]["accepted"] == 1
