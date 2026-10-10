"""Battery use comes from observed motion; charge requires stopped pad presence."""

from pathlib import Path

import pytest

from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def test_motion_payload_and_headwind_drain_real_battery() -> None:
    energy = []
    for wind in (0.0, 5.0):
        document = parcel_document(fleet_size=1, wind_speed=wind)
        document["generators"]["fleet"]["facts"]["lowalt.payload_kg"] = 2.0
        document["engines"]["tasks"]["config"]["generators"][0]["arrivals"][
            "start_ns"
        ] = 100_000_000_000
        document["bindings"]["commands"] = [
            {
                "schema": "lowalt.motion.move_to",
                "target": "motion",
                "at_ns": 0,
                "payload": {"entity": "drone-1", "target": [-340.0, -220.0, 50.0]},
            }
        ]
        sim = Simulation(load_scenario(document, base=BASE))
        try:
            sim.start()
            sim.run_until(8_000_000_000)
            ref = next(r for r in sim.scenario.manifest.entities if r.id == "drone-1")
            view = sim.kernel.view()
            used = view.field((ref, "lowalt.energy_used_j"), view.instant).value
            soc = view.field((ref, "lowalt.soc"), view.instant).value
            joules = view.field((ref, "lowalt.motion_energy_j"), view.instant).value
            flown = view.field((ref, "lowalt.distance_m"), view.instant).value
            assert used > 1000 and flown == pytest.approx(100.0)
            assert joules == pytest.approx(soc * 240000)
            energy.append(used)
        finally:
            sim.close()
    assert energy[1] > energy[0]


def test_low_battery_charges_at_actual_pad_and_releases() -> None:
    document = parcel_document(fleet_size=1)
    facts = document["generators"]["fleet"]["facts"]
    facts.update(
        {
            "lowalt.soc": 0.1,
            "lowalt.capacity_j": 1000.0,
            "lowalt.motion_energy_j": 100.0,
        }
    )
    document["engines"]["motion"]["config"]["energy"]["capacity_j"] = 1000.0
    document["engines"]["tasks"]["config"]["generators"][0]["arrivals"]["start_ns"] = (
        100_000_000_000
    )
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(4_000_000_000)
        messages = [m for r in sim.kernel.records for m in project(r)["messages"]]
        started = [
            m for m in messages if m["schemaId"] == "lowalt.energy.charging_started"
        ]
        finished = [
            m for m in messages if m["schemaId"] == "lowalt.energy.charging_finished"
        ]
        assert len(started) == len(finished) == 1
        assert started[0]["payload"]["pad"] == "pad-1"
        assert finished[0]["payload"]["soc"] == pytest.approx(0.9)
        assert int(finished[0]["at"]["ns"]) > int(started[0]["at"]["ns"])
        assert any(m["schemaId"] == "lowalt.energy.low" for m in messages)
    finally:
        sim.close()
