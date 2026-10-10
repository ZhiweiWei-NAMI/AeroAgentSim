"""Operational predicates decide once; owned engines execute real commands."""

import copy
from pathlib import Path
from typing import Any

from aeroagentsim.packs.lowalt.rules import configure_detection, set_reserve
from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def _quiet(fleet_size: int = 1) -> dict[str, Any]:
    document = parcel_document(fleet_size=fleet_size)
    document["engines"]["tasks"]["config"]["generators"][0]["arrivals"]["start_ns"] = (
        100_000_000_000
    )
    return document


def _run(document: dict[str, Any], seconds: int = 10) -> list[dict[str, Any]]:
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(seconds * 10**9)
        return [project(r) for r in sim.kernel.iter_records()]
    finally:
        sim.close()


def _events(commits: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    events = [m for r in commits for m in r["messages"] if m["schemaId"] == schema]
    assert all(m["source"] == "behaviour" for m in events)
    return events


def test_battery_reserve_parameter_changes_outcome_and_completes_on_charge() -> None:
    outcomes = []
    for reserve in (20.0, 60.0):
        document = _quiet()
        set_reserve(document, reserve)
        document["generators"]["fleet"]["facts"].update(
            {"lowalt.soc": 0.5, "lowalt.motion_energy_j": 120000.0}
        )
        outcomes.append(_run(document, 25))
    assert not _events(outcomes[0], "lowalt.energy.low")
    assert len(_events(outcomes[1], "lowalt.energy.low")) == 1
    assert any(
        m["schemaId"] == "lowalt.energy.charging_finished"
        for r in outcomes[1]
        for m in r["messages"]
    )
    transitions = [
        c
        for r in outcomes[1]
        for c in r.get("chainInstances", [])
        if c.get("op") != "close"
    ]
    assert any(c.get("transitionId") == "charged" for c in transitions)
    recalled = [
        f
        for r in outcomes[1]
        for f in r["facts"]
        if f["fieldId"] == "lowalt.returning_to_charge"
    ]
    assert [f["value"] for f in recalled] == [False, True, False]
    assert all(f["producer"] == "behaviour" for f in recalled)
    # The low event and dispatch-inhibiting flag are in the real chain's cut.
    assert any(
        _events([r], "lowalt.energy.low")
        and any(
            f["fieldId"] == "lowalt.returning_to_charge" and f["value"]
            for f in r["facts"]
        )
        for r in outcomes[1]
    )


def test_no_fly_membership_emits_once_and_commands_reroute() -> None:
    document = _quiet()
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    zone["facts"].update(
        {
            "lowalt.zone_lower": [-260.0, -240.0, 0.0],
            "lowalt.zone_upper": [-220.0, -200.0, 100.0],
        }
    )
    commits = _run(document)
    assert len(_events(commits, "lowalt.airspace.geofence_entered")) == 1
    assert any(
        m["schemaId"] == "lowalt.control" and m["payload"]["action"] == "reroute"
        for r in commits
        for m in r["messages"]
    )
    assert any(
        p["predicateId"] == "inside_no_fly_zone" and p["value"] is True
        for r in commits
        for p in r.get("predicateTruth", [])
    )


def test_separation_parameter_uses_airborne_distances_and_real_receipt() -> None:
    counts = []
    for safety in (20.0, 40.0):
        document = _quiet(2)
        document["generators"]["fleet"]["starts"] = [
            [-240.0, -220.0, 80.0],
            [-210.0, -220.0, 80.0],
        ]
        document["behaviours"][0]["predicates"]["closer_than_safety_distance"][
            "parameters"
        ]["horizontal_m"] = safety
        commits = _run(document)
        counts.append(len(_events(commits, "lowalt.airspace.conflict")))
        if safety == 40.0:
            assert len(_events(commits, "lowalt.airspace.conflict_resolved")) == 1
    assert counts == [0, 1]
    document = _quiet(2)
    document["generators"]["fleet"]["starts"] = [
        [-240.0, -220.0, 50.0],
        [-239.0, -220.0, 50.0],
    ]
    assert not _events(_run(document), "lowalt.airspace.conflict")


def test_wind_parameter_changes_hold_and_exit_resumes() -> None:
    counts = []
    for limit in (10.0, 20.0):
        document = _quiet()
        document["behaviours"][0]["predicates"]["wind_above_limit"]["parameters"][
            "parcel_limit_mps"
        ] = limit
        document["engines"]["environment"]["config"]["profiles"]["weather"][
            "lowalt.wind"
        ] = {
            "mode": "gust",
            "value": [2.0, 0.0, 0.0],
            "gusts": [
                {"start_ns": 2 * 10**9, "end_ns": 4 * 10**9, "value": [15.0, 0.0, 0.0]}
            ],
        }
        commits = _run(document)
        counts.append(len(_events(commits, "lowalt.weather.hold")))
        assert len(_events(commits, "lowalt.weather.release")) == counts[-1]
    assert counts == [1, 0]


def test_overlapping_no_fly_zones_each_emit_once_on_their_own_entry() -> None:
    for starts_at in (0, 2 * 10**9):
        document = _quiet()
        # Slow physical egress keeps the drone inside both zones for this probe.
        document["engines"]["motion"]["config"]["max_speed_m_s"] = 0.1
        zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
        zone["facts"].update(
            {
                "lowalt.zone_lower": [-260.0, -240.0, 0.0],
                "lowalt.zone_upper": [-220.0, -200.0, 100.0],
            }
        )
        second = copy.deepcopy(zone)
        second["id"] = "second-no-fly"
        second["facts"]["lowalt.zone_start_ns"] = starts_at
        document["entities"].append(second)
        events = _events(_run(document, 4), "lowalt.airspace.geofence_entered")
        assert sorted(event["payload"]["zone"] for event in events) == [
            "central-no-fly",
            "second-no-fly",
        ]


def test_below_authored_reserve_never_dispatches_before_recall_flag() -> None:
    document = _quiet()
    set_reserve(document, 60.0)
    document["generators"]["fleet"]["facts"].update(
        {"lowalt.soc": 0.5, "lowalt.motion_energy_j": 120000.0}
    )
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group["arrivals"] = {
        "mode": "scheduled",
        "times_ns": [0] + [100 * 10**9] * (len(group["tasks"]) - 1),
    }
    commits = _run(document, 3)
    assert len(_events(commits, "lowalt.energy.low")) == 1
    assert not any(
        message["schemaId"] == "lowalt.task.assigned"
        for commit in commits
        for message in commit["messages"]
    )


def test_duplicate_resolution_preserves_original_chain_receipt() -> None:
    document = _quiet(2)
    document["generators"]["fleet"]["starts"] = [
        [-240.0, -220.0, 80.0],
        [-239.0, -220.0, 80.0],
    ]
    document["bindings"]["commands"] = [
        {
            "schema": "lowalt.airspace.resolve_conflict",
            "target": "airspace",
            "at_ns": 2 * 10**9,
            "payload": {"a": "drone-1", "b": "drone-2"},
        }
    ]
    commits = _run(document, 8)
    assert len(_events(commits, "lowalt.airspace.conflict_resolved")) == 1
    assert any(
        receipt["status"] == "rejected"
        and receipt.get("result", {}).get("action") == "already resolving"
        for commit in commits
        for receipt in commit["receipts"]
    )


def test_per_zone_instance_budget_excludes_previously_expanded_fleet() -> None:
    document = _quiet(1000)
    second = copy.deepcopy(
        next(e for e in document["entities"] if e["id"] == "central-no-fly")
    )
    second["id"] = "second-no-fly"
    document["entities"].append(second)
    configure_detection(document)
    budget = document["behaviours"][0]["budgets"]["max_instances"]
    assert budget == 5000
    document["entities"].append(
        {
            "id": "drone-1",
            "type": "lowalt:UAV",
            "facts": copy.deepcopy(document["generators"]["fleet"]["facts"]),
        }
    )
    configure_detection(document)
    assert document["behaviours"][0]["budgets"]["max_instances"] == budget
