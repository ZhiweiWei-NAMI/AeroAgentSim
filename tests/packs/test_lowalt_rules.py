"""The shared lowalt.rules package decides; engines execute and own their fields."""

from pathlib import Path

from aeroagentsim.packs.lowalt.rules import set_reserve
from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def _messages(reserve: float) -> list[dict]:  # type: ignore[type-arg]
    document = parcel_document(fleet_size=1)
    set_reserve(document, reserve)
    facts = document["generators"]["fleet"]["facts"]
    facts.update({"lowalt.soc": 0.5, "lowalt.motion_energy_j": 120000.0})
    document["engines"]["motion"]["config"]["energy"]["capacity_j"] = 240000.0
    document["engines"]["tasks"]["config"]["generators"][0]["arrivals"][
        "start_ns"
    ] = 100_000_000_000
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(6_000_000_000)
        return [m for r in sim.kernel.records for m in project(r)["messages"]]
    finally:
        sim.close()


def test_battery_reserve_parameter_changes_the_outcome() -> None:
    calm = _messages(20.0)
    strict = _messages(60.0)
    assert not any(m["schemaId"] == "lowalt.energy.low" for m in calm)
    low = [m for m in strict if m["schemaId"] == "lowalt.energy.low"]
    # Emitted once, by the chain (the engine no longer emits it).
    assert len(low) == 1
    assert any(m["schemaId"] == "lowalt.energy.charging_started" for m in strict)
    assert any(m["schemaId"] == "aas.behaviour.transitioned" for m in strict)
