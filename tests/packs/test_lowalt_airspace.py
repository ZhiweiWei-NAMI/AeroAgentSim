"""One observed conflict, a dynamic zone injection and a wind interruption."""

from pathlib import Path

import pytest

from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


@pytest.mark.parametrize("parked", [False, True])
def test_airborne_separation_distinguishes_a_parked_pad_neighbor(parked: bool) -> None:
    document = parcel_document(fleet_size=2)
    altitude = 50.0 if parked else 60.0
    document["generators"]["fleet"]["starts"] = [
        [-240.0, -220.0, altitude], [-239.0, -220.0, altitude + 1.0],
    ]
    document["engines"]["tasks"]["config"]["generators"][0]["arrivals"]["start_ns"] = (
        100_000_000_000
    )
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(3_000_000_000)
        messages = [m for r in sim.kernel.iter_records() for m in project(r)["messages"]]
        conflicts = [m for m in messages if m["schemaId"] == "lowalt.airspace.conflict"]
        assert bool(conflicts) is not parked
        if parked:
            assert not any(
                m["schemaId"] == "lowalt.control" and m["payload"]["action"] == "altitude"
                for m in messages
            )
    finally:
        sim.close()


def test_zone_schedule_injection_weather_and_conflict_resolution() -> None:
    document = parcel_document(fleet_size=2)
    document["generators"]["fleet"]["starts"] = [
        [-240.0, -220.0, 80.0],
        [-239.0, -220.0, 80.0],
    ]
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group["arrivals"]["start_ns"] = 100_000_000_000
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    zone["facts"].update(
        {
            "lowalt.zone_dynamic": True,
            "lowalt.zone_start_ns": 3_500_000_000,
            "lowalt.zone_end_ns": 8_500_000_000,
        }
    )
    document["bindings"]["commands"] = [
        {
            "schema": "lowalt.airspace.activate",
            "target": "airspace",
            "at_ns": 5_000_000_000,
            "payload": {
                "zone": zone["id"],
                "active": False,
                "reason": "incident cleared",
            },
        }
    ]
    document["engines"]["environment"]["config"]["profiles"]["weather"][
        "lowalt.wind"
    ] = {
        "mode": "gust",
        "value": [2.0, 0.0, 0.0],
        "gusts": [
            {
                "start_ns": 2_000_000_000,
                "end_ns": 4_000_000_000,
                "value": [15.0, 0.0, 0.0],
            }
        ],
    }
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(10_000_000_000)
        messages = [m for r in sim.kernel.records for m in project(r)["messages"]]
        schemas = {m["schemaId"] for m in messages}
        assert {
            "lowalt.airspace.conflict",
            "lowalt.airspace.conflict_resolved",
            "lowalt.weather.hold",
            "lowalt.weather.release",
        } <= schemas
        activations = [
            m for m in messages if m["schemaId"] == "lowalt.airspace.zone_activated"
        ]
        assert int(activations[0]["at"]["ns"]) == 3_500_000_000
        deactivated = [
            m for m in messages if m["schemaId"] == "lowalt.airspace.zone_deactivated"
        ]
        assert deactivated[0]["payload"]["reason"] == "incident cleared"
        assert any(
            m["schemaId"] == "lowalt.control" and m["payload"]["action"] == "altitude"
            for m in messages
        )
    finally:
        sim.close()
