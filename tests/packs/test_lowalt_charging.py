"""Real charging, pad queues and flight resumption for the round-3 P0s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aerokernel import Instant, Journal, replay
from aerokernel.sdk import EngineContext

from aeroagentsim.packs.lowalt.core import snapshot
from aeroagentsim.packs.lowalt.tasks import FOOD_FIELDS, Job, LowaltTasks
from aeroagentsim.packs.lowalt.template import SECOND, parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def _document(fleet: int = 2, slots: int = 1) -> dict[str, Any]:
    document = parcel_document(fleet_size=fleet, wind_speed=2.0)
    tasks = ["task-1", "task-2", "task-3"]
    keep = {"weather", "vertiport-1", "customer-1", "pad-1", *tasks}
    document["entities"] = [e for e in document["entities"] if e["id"] in keep]
    for entity in document["entities"]:
        if entity["id"] == "vertiport-1":
            entity["type"] = "lowalt:Restaurant"
            entity["facts"].update(
                {"lowalt.site_position": [80.0, 0.0, 0.0], "lowalt.food_preparation_s": 4.0}
            )
        elif entity["id"] == "customer-1":
            entity["facts"]["lowalt.site_position"] = [200.0, 0.0, 0.0]
        elif entity["id"] == "pad-1":
            entity["facts"].update(
                {"lowalt.site_position": [0.0, 0.0, 0.0], "lowalt.site_capacity": slots}
            )
        elif entity["id"] in tasks:
            entity["type"] = "lowalt:FoodOrder"
            entity["facts"]["lowalt.task_kind"] = "food"
    prototype = document["generators"]["fleet"]
    prototype["starts"] = [[80.0, 0.0, 0.0] for _ in range(fleet)]
    prototype["facts"].update({"lowalt.soc": 0.15, "lowalt.motion_energy_j": 36000.0})
    document["bindings"]["rules"].extend([
        {"writer": "tasks", "type": "lowalt:Restaurant", "fields": ["lowalt.food_preparation_s"]},
        {"writer": "tasks", "type": "lowalt:FoodOrder", "fields": list(FOOD_FIELDS)},
    ])
    document["engines"]["tasks"]["config"].update(
        cruise_altitudes={"parcel": 16.0},
        generators=[{
            "kind": "food", "tasks": tasks, "origins": ["vertiport-1"],
            "destinations": ["customer-1"], "priority": 1, "payload_kg": 1.0,
            "delivery_window_s": 240.0,
            "arrivals": {"mode": "scheduled", "times_ns": [10 * SECOND, 50 * SECOND, 90 * SECOND]},
        }],
    )
    return document


def _events(commits: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    return [m for c in commits for m in c["messages"] if m["schemaId"] == schema]


def _assert_capacity(commits: list[dict[str, Any]], slots: int) -> int:
    active: set[str] = set()
    peak = 0
    fields: dict[tuple[str, str], Any] = {}
    for commit in commits:
        for message in commit["messages"]:
            if message["schemaId"] == "lowalt.energy.charging_started":
                active.add(message["payload"]["vehicle"])
                peak = max(peak, len(active))
                assert len(active) <= slots
            elif message["schemaId"] == "lowalt.energy.charging_finished":
                active.remove(message["payload"]["vehicle"])
        for fact in commit["facts"]:
            fields[fact["entity"]["id"], fact["fieldId"]] = fact["value"]
        charging = {i for (i, f), v in fields.items() if f == "lowalt.charging" and v}
        queued = {i for (i, f), v in fields.items() if f == "lowalt.charging_waiting" and v}
        assert len(charging) <= slots
        assert charging.isdisjoint(queued)
        for identity in charging | queued:
            assert fields[identity, "lowalt.charging_pad"] == "pad-1"
            assert fields[identity, "lowalt.position"] == [0.0, 0.0, 0.0]
            assert fields[identity, "lowalt.velocity"] == [0.0, 0.0, 0.0]
    assert not active
    return peak


def test_two_low_battery_couriers_charge_release_and_deliver_after_altitude_avoidance() -> None:
    """T1-R3-001: both recalls end and meal dispatch recovers with one slot."""
    sim = Simulation(load_scenario(_document(), base=BASE))
    try:
        sim.start()
        sim.run_until(240 * SECOND)
        commits = [project(r) for r in sim.kernel.iter_records()]
        finished = _events(commits, "lowalt.energy.charging_finished")
        assert {m["payload"]["vehicle"] for m in finished} == {"drone-1", "drone-2"}
        assert len(finished) == 2
        assert all(m["payload"]["soc"] == pytest.approx(0.9) for m in finished)
        assert _assert_capacity(commits, 1) == 1
        controls = _events(commits, "lowalt.control")
        assert any(m["payload"]["action"] == "altitude" for m in controls)
        assert sum(m["payload"]["reason"] == "charging finished" for m in controls) == 2
        completed = _events(commits, "lowalt.task.completed")
        assert {m["payload"]["task"] for m in completed} == {"task-1", "task-2", "task-3"}
        assigned = _events(commits, "lowalt.task.assigned")
        assert {m["payload"]["vehicle"] for m in assigned} == {"drone-1", "drone-2"}
        assert not _events(commits, "lowalt.task.failed")
        view = sim.kernel.view()
        for ref in sim.scenario.manifest.entities:
            if ref.type_id == "lowalt:UAV":
                for field in ("charging", "charging_waiting", "returning_to_charge"):
                    assert view.field((ref, "lowalt." + field), view.instant).value is False
                assert view.field((ref, "lowalt.charging_pad"), view.instant).value == ""
    finally:
        sim.close()


@pytest.mark.parametrize("purpose", ["charge", "avoidance"])
def test_charge_completion_clears_detours_despite_a_later_soc_sample(purpose: str) -> None:
    """T1-R3-001: completion is authoritative after SoC has fallen below 90%."""
    scenario = load_scenario(_document(), base=BASE)
    engine = LowaltTasks(EngineBuild(
        "tasks", scenario.engines["tasks"]["config"], scenario.registry,
        scenario.manifest, scenario.manifest.entities, scenario.initial,
    ))
    flight = engine.flights["drone-1"]
    flight.job = Job(next(r for r in scenario.manifest.entities if r.id == "task-1"), 0, phase="picked_up")
    flight.purpose = purpose
    flight.goal = (0.0, 0.0, 16.0) if purpose == "avoidance" else (0.0, 0.0, 0.0)
    flight.charging_goal = flight.avoidance_resume_goal = (0.0, 0.0, 0.0)
    flight.avoidance_resume_purpose = "charge"
    flight.saved_goal = (200.0, 0.0, 0.0)
    ctx = cast(EngineContext, SimpleNamespace(now=Instant(100 * SECOND), get=lambda *_: 0.899))
    engine._charging_finished(ctx, "drone-1")
    assert flight.purpose == "task" and flight.goal == (200.0, 0.0, 0.0)
    assert flight.charging_goal is None and flight.avoidance_resume_goal is None
    assert flight.avoidance_resume_purpose == "task"
    assert flight.replan


@pytest.mark.parametrize("slots", [1, 2])
@pytest.mark.parametrize("swap", [False, True])
def test_pad_slots_queue_arrivals_and_survive_record_replay(
    tmp_path: Path, slots: int, swap: bool,
) -> None:
    """T1-R3-002: N slots serve N aircraft while the others visibly wait."""
    document = _document(fleet=3, slots=slots)
    prototype = document["generators"]["fleet"]
    prototype["starts"] = [[0.0, 0.0, 0.0]] * 3
    prototype["facts"].update({"lowalt.capacity_j": 24000.0, "lowalt.motion_energy_j": 3600.0})
    document["engines"]["motion"]["config"]["energy"]["capacity_j"] = 24000.0
    document["engines"]["tasks"]["config"]["generators"][0]["arrivals"]["times_ns"] = [100 * SECOND] * 3
    if swap:
        document["engines"]["energy"]["config"].update(swap_pads=["pad-1"], swap_duration_ns=2 * SECOND)
    energy_id = "charging-resource" if swap else "energy"
    if energy_id != "energy":
        document["engines"][energy_id] = document["engines"].pop("energy")
        for rule in document["bindings"]["rules"]:
            if rule["writer"] == "energy":
                rule["writer"] = energy_id
        document["engines"]["behaviour"]["config"]["capabilities"][
            "lowalt.energy.return_to_pad"
        ]["target"] = energy_id
    # A saved scene lacks the new observations; expansion installs descriptors
    # and writer bindings, and the real energy owner initializes idle state.
    for field in ("lowalt.charging_waiting", "lowalt.charging_pad"):
        prototype["facts"].pop(field)
        document["registry"]["fields"] = [f for f in document["registry"]["fields"] if f["id"] != field]
    path = tmp_path / "charging.jsonl"
    sim = Simulation(load_scenario(document, base=BASE), journal=Journal(path))
    try:
        sim.start()
        sim.run_until(2 * SECOND)
        cut = sim.kernel.view()
        ctx = cast(EngineContext, SimpleNamespace(
            now=cut.instant, get=lambda ref, field: cut.field((ref, field), cut.instant).value,
        ))
        energy = EngineBuild(
            energy_id, sim.scenario.engines[energy_id]["config"], sim.scenario.registry,
            sim.scenario.manifest, sim.scenario.manifest.entities, sim.scenario.initial,
        )
        pad = snapshot(ctx, energy).pads[0]
        assert (pad.capacity, pad.occupancy, pad.reserved) == (slots, slots, 3 - slots)
        waiting_refs = [
            r for r in sim.scenario.manifest.entities if r.type_id == "lowalt:UAV"
            and cut.field((r, "lowalt.charging_waiting"), cut.instant).value
        ]
        assert len(waiting_refs) == 3 - slots
        waiting_energy = {
            r: cut.field((r, "lowalt.motion_energy_j"), cut.instant).value for r in waiting_refs
        }
        sim.run_until(20 * SECOND)
        commits = [project(r) for r in sim.kernel.iter_records()]
        assert _assert_capacity(commits, slots) == slots
        queued = _events(commits, "lowalt.energy.charging_waiting")
        assert len(queued) == 3 - slots
        assert all(m["payload"]["occupancy"] == slots and m["payload"]["capacity"] == slots for m in queued)
        started = _events(commits, "lowalt.energy.charging_started")
        assert [m["payload"]["vehicle"] for m in started] == ["drone-1", "drone-2", "drone-3"]
        for ref in waiting_refs:
            arrival = next(m for m in started if m["payload"]["vehicle"] == ref.id)
            assert arrival["payload"]["soc"] * 24000.0 < waiting_energy[ref]
        recalls = _events(commits, "lowalt.energy.return_to_pad")
        end = sim.kernel.view()
        assert len(recalls) == 3
        assert all(end.action(m["id"]).status == "succeeded" for m in recalls)
        expected = {
            (ref, field): end.field((ref, field), end.instant).value
            for ref in sim.scenario.manifest.entities if ref.type_id == "lowalt:UAV"
            for field in ("lowalt.soc", "lowalt.charging", "lowalt.charging_waiting", "lowalt.charging_pad", "lowalt.returning_to_charge")
        }
    finally:
        sim.close()
    recorded = replay(path).view()
    assert {(ref, field): recorded.field((ref, field), recorded.instant).value for ref, field in expected} == expected
