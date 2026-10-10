"""Bounded first egress preserves normal guards, missions and physical receipts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aerokernel import Instant, Journal, replay
from aerokernel.sdk import EngineContext

from aeroagentsim.algorithms.builtins import CorridorAstar, Straight
from aeroagentsim.algorithms.protocols import Point, Snapshot, Zone
from aeroagentsim.algorithms.routes import (
    NoFlyRouteUnavailable,
    containing_no_fly,
    cruise_plan,
    plan_with_egress,
    route_avoids_no_fly,
)
from aeroagentsim.packs.geometry import segment_hits_box
from aeroagentsim.packs.lowalt.tasks import Job, LowaltTasks
from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def test_egress_exits_overlapping_boxes_and_prefers_roof_safe_vertical() -> None:
    state = Snapshot(
        0,
        (),
        (),
        (
            Zone("a", "no_fly", (-10, -10, 0), (10, 10, 100), True),
            Zone("b", "no_fly", (-5, -5, 0), (15, 15, 120), True),
        ),
    )
    start, goal = (0.0, 0.0, 50.0), (100.0, 0.0, 50.0)
    points = plan_with_egress(CorridorAstar(), state, start, goal, 125.0)
    assert points[0] == (0.0, 0.0, 125.0)
    assert not containing_no_fly(state, points[0])
    assert points[-1] == goal
    assert route_avoids_no_fly(state, start, points, initial_exit=True)
    assert not route_avoids_no_fly(state, start, points)
    assert not route_avoids_no_fly(
        state, start, (points[0], start, goal), initial_exit=True
    )
    assert not route_avoids_no_fly(
        state, start, (start, points[0], goal), initial_exit=True
    )


def test_initially_noncontaining_box_remains_protected_during_exit() -> None:
    state = Snapshot(
        0,
        (),
        (),
        (
            Zone("containing", "no_fly", (-10, -10, 0), (10, 10, 100), True),
            Zone("ceiling", "no_fly", (-4, -4, 105), (4, 4, 115), True),
        ),
    )
    start, goal = (0.0, 0.0, 50.0), (100.0, 0.0, 50.0)
    points = plan_with_egress(CorridorAstar(), state, start, goal, 125.0)
    assert points[0] != (0.0, 0.0, 125.0)
    assert points[0][2] >= 125.0
    assert not containing_no_fly(state, points[0])
    assert route_avoids_no_fly(state, start, points, initial_exit=True)
    ceiling = state.zones[1]
    assert not any(
        segment_hits_box(a, b, ceiling.lower, ceiling.upper)
        for a, b in zip((start,) + points, points)
    )


def test_impossible_bounded_exit_and_forbidden_goal_are_explicit() -> None:
    boxes = (
        Zone("inside", "no_fly", (-1, -1, 0), (1, 1, 1), True),
        Zone("east", "no_fly", (1.1, -3, 0), (3, 3, 3), True),
        Zone("west", "no_fly", (-3, -3, 0), (-1.1, 3, 3), True),
        Zone("north", "no_fly", (-3, 1.1, 0), (3, 3, 3), True),
        Zone("south", "no_fly", (-3, -3, 0), (3, -1.1, 3), True),
        Zone("above", "no_fly", (-3, -3, 1.1), (3, 3, 3), True),
    )
    state = Snapshot(0, (), (), boxes)
    with pytest.raises(NoFlyRouteUnavailable, match="bounded"):
        plan_with_egress(CorridorAstar(), state, (0, 0, 0), (10, 0, 0))
    with pytest.raises(NoFlyRouteUnavailable, match="destination"):
        plan_with_egress(CorridorAstar(), state, (0, 0, 0), (0, 0, 0.5))


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("upper", [False, True])
def test_closed_no_fly_faces_require_a_complete_exit(axis: int, upper: bool) -> None:
    zone = Zone("restricted", "no_fly", (-10.0, -10.0, 10.0), (10.0, 10.0, 100.0), True)
    state = Snapshot(0, (), (), (zone,))
    coordinates = [0.0, 0.0, 50.0]
    coordinates[axis] = (zone.upper if upper else zone.lower)[axis]
    face = (coordinates[0], coordinates[1], coordinates[2])
    goal = (30.0, 30.0, 150.0)
    assert containing_no_fly(state, face) == (zone.id,)
    assert not route_avoids_no_fly(state, face, (face,), initial_exit=True)
    points = plan_with_egress(CorridorAstar(), state, face, goal)
    assert points[-1] == goal
    assert not containing_no_fly(state, points[0])
    assert route_avoids_no_fly(state, face, points, initial_exit=True)
    with pytest.raises(NoFlyRouteUnavailable, match="destination"):
        plan_with_egress(CorridorAstar(), state, goal, face)


@pytest.mark.parametrize(
    "start,reason",
    [((-20.0, 0.0, 50.0), "crosses"), ((0.0, 0.0, 50.0), "bounded")],
)
def test_unsafe_planner_proposals_cannot_disable_the_normal_guard(
    start: Point, reason: str
) -> None:
    state = Snapshot(
        0, (), (), (Zone("restricted", "no_fly", (-10, -10, 0), (10, 10, 100), True),)
    )
    calls: list[Point] = []

    class UnsafePlanner:
        def plan(
            self, snapshot: Snapshot, start: Point, goal: Point
        ) -> tuple[Point, ...]:
            assert snapshot is state  # Never globally deactivate the containing zone.
            calls.append(start)
            return ((0.0, 0.0, 50.0), goal)

    with pytest.raises(NoFlyRouteUnavailable, match=reason):
        plan_with_egress(UnsafePlanner(), state, start, (20.0, 0.0, 50.0))
    assert calls  # Exercise proposal validation, not just candidate rejection.


@pytest.mark.parametrize("kind,active", [("no_fly", False), ("advisory", True)])
def test_inactive_and_other_zone_kinds_do_not_trigger_egress(
    kind: str, active: bool
) -> None:
    state = Snapshot(
        0, (), (), (Zone("ignored", kind, (-10, -10, 0), (10, 10, 100), active),)
    )
    start, goal = (0.0, 0.0, 50.0), (20.0, 0.0, 50.0)
    assert not containing_no_fly(state, start)
    assert plan_with_egress(Straight(), state, start, goal) == (goal,)
    assert route_avoids_no_fly(state, start, (goal,))


def test_normal_outside_routes_are_unchanged() -> None:
    state = Snapshot(
        0, (), (), (Zone("obstacle", "no_fly", (30, -5, 0), (60, 5, 100), True),)
    )
    start, goal = (0.0, 0.0, 50.0), (100.0, 0.0, 50.0)
    planner = CorridorAstar()
    assert plan_with_egress(planner, state, start, goal) == planner.plan(
        state, start, goal
    )
    assert plan_with_egress(planner, state, start, goal, 125.0) == cruise_plan(
        planner, state, start, goal, 125.0
    )


def _one_order() -> dict[str, Any]:
    document = parcel_document(fleet_size=1)
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group.update(tasks=["task-1"], origins=["vertiport-1"], destinations=["customer-1"])
    group["arrivals"] = {"mode": "scheduled", "times_ns": [0]}
    document["entities"] = [
        e
        for e in document["entities"]
        if e["type"] != "lowalt:Task" or e["id"] == "task-1"
    ]
    document["engines"]["tasks"]["config"]["batch_window_s"] = 0.0
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    zone["facts"].update(
        {
            "lowalt.zone_lower": [-260.0, -240.0, 0.0],
            "lowalt.zone_upper": [-220.0, -150.0, 100.0],
            "lowalt.zone_dynamic": True,
            "lowalt.zone_start_ns": 5 * 10**9,
            "lowalt.zone_end_ns": 70 * 10**9,
        }
    )
    return document


def _native_arrival_index(commits: list[dict[str, Any]], point: Point) -> int:
    """Require arrival plus a succeeded receipt for the matching real move command."""
    commands = {
        m["id"]: c["commitIndex"]
        for c in commits
        for m in c["messages"]
        if m["schemaId"] == "lowalt.motion.move_to"
        and m["payload"]["entity"] == "drone-1"
        and tuple(m["payload"]["target"]) == point
    }
    for commit in commits:
        arrived = any(
            m["schemaId"] == "lowalt.motion.arrived"
            and m["source"] == "motion"
            and m["payload"]["entity"] == "drone-1"
            and tuple(m["payload"]["position"]) == point
            for m in commit["messages"]
        )
        if not arrived:
            continue
        for receipt in commit["receipts"]:
            result = receipt.get("result") or {}
            if (
                receipt["commandId"] in commands
                and receipt["status"] == "succeeded"
                and result.get("entity") == "drone-1"
                and tuple(result.get("position", ())) == point
            ):
                assert commands[receipt["commandId"]] < commit["commitIndex"]
                return int(commit["commitIndex"])
    pytest.fail(f"No native arrival with a matching succeeded move receipt at {point}")


@pytest.mark.parametrize("cruise", [None, 125.0])
def test_dynamic_zone_egress_keeps_delivery_goal_and_completes_on_receipt(
    tmp_path: Path, monkeypatch: Any, cruise: float | None
) -> None:
    document = _one_order()
    if cruise is not None:
        document["engines"]["tasks"]["config"]["cruise_altitudes"] = {"parcel": cruise}
    egress = []
    original = LowaltTasks._route

    def capture(self: LowaltTasks, ctx: Any, flight: Any, goal: Any) -> bool:
        result = original(self, ctx, flight, goal)
        if flight.egress_waypoint is not None:
            egress.append((flight.purpose, flight.goal, flight.egress_waypoint))
        return result

    monkeypatch.setattr(LowaltTasks, "_route", capture)
    scenario = load_scenario(document, base=BASE)
    journal = tmp_path / "journal.jsonl"
    sim = Simulation(scenario, journal=Journal(journal))
    try:
        sim.start()
        sim.run_until(45 * 10**9)
        commits = [project(r) for r in sim.kernel.iter_records()]
        messages = [m for c in commits for m in c["messages"]]
        assert egress
        expected_goal = tuple(scenario.initial["customer-1"]["lowalt.site_position"])
        assert all(
            purpose == "task" and goal == expected_goal for purpose, goal, _ in egress
        )
        exit_point = egress[0][2]
        completed = [m for m in messages if m["schemaId"] == "lowalt.task.completed"]
        assert len(completed) == 1
        completion_index = next(
            c["commitIndex"] for c in commits if completed[0] in c["messages"]
        )
        assert (
            _native_arrival_index(commits, exit_point)
            < _native_arrival_index(commits, expected_goal)
            < completion_index
        )
        assert not any(m["schemaId"] == "lowalt.task.failed" for m in messages)
        ref = next(r for r in scenario.manifest.entities if r.id == "drone-1")
        view = sim.kernel.view()
        expected_position = view.field((ref, "lowalt.position"), view.instant).value
        assert expected_position == expected_goal
    finally:
        sim.close()
    recorded = replay(journal).view()
    assert (
        recorded.field((ref, "lowalt.position"), recorded.instant).value
        == expected_position
    )


def test_forbidden_destination_waits_then_resumes_after_deactivation(
    monkeypatch: Any,
) -> None:
    document = _one_order()
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    zone["facts"].update(
        {
            "lowalt.zone_lower": [-260.0, -140.0, 0.0],
            "lowalt.zone_upper": [-220.0, -100.0, 100.0],
            "lowalt.zone_end_ns": 20 * 10**9,
        }
    )
    waiting = []
    original = LowaltTasks._route

    def capture(self: LowaltTasks, ctx: Any, flight: Any, goal: Any) -> bool:
        result = original(self, ctx, flight, goal)
        if not result:
            waiting.append((flight.purpose, flight.goal, flight.route_blocked_reason))
        return result

    monkeypatch.setattr(LowaltTasks, "_route", capture)
    scenario = load_scenario(document, base=BASE)
    expected_goal = tuple(scenario.initial["customer-1"]["lowalt.site_position"])
    sim = Simulation(scenario)
    try:
        sim.start()
        sim.run_until(19 * 10**9)
        before_release = [
            m for r in sim.kernel.iter_records() for m in project(r)["messages"]
        ]
        assert waiting and all(
            purpose == "task" and goal == expected_goal and "destination" in reason
            for purpose, goal, reason in waiting
        )
        assert not any(
            m["schemaId"] in {"lowalt.task.completed", "lowalt.task.failed"}
            or (
                m["schemaId"] == "lowalt.motion.arrived"
                and tuple(m["payload"]["position"]) == expected_goal
            )
            for m in before_release
        )
        sim.run_until(40 * 10**9)
        commits = [project(r) for r in sim.kernel.iter_records()]
        messages = [m for c in commits for m in c["messages"]]
        completed = [m for m in messages if m["schemaId"] == "lowalt.task.completed"]
        assert len(completed) == 1 and int(completed[0]["at"]["ns"]) > 20 * 10**9
        completion_index = next(
            c["commitIndex"] for c in commits if completed[0] in c["messages"]
        )
        assert _native_arrival_index(commits, expected_goal) < completion_index
        assert not any(m["schemaId"] == "lowalt.task.failed" for m in messages)
    finally:
        sim.close()


def test_geofence_does_not_move_actively_charging_aircraft() -> None:
    document = _one_order()
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group["arrivals"]["times_ns"] = [100 * 10**9]
    document["generators"]["fleet"]["facts"].update(
        {"lowalt.soc": 0.1, "lowalt.motion_energy_j": 24000.0}
    )
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(12 * 10**9)
        commits = [project(r) for r in sim.kernel.iter_records()]
        messages = [m for r in commits for m in r["messages"]]
        assert any(m["schemaId"] == "lowalt.energy.charging_started" for m in messages)
        assert any(
            m["schemaId"] == "lowalt.airspace.geofence_entered" for m in messages
        )
        assert not any(m["schemaId"] == "lowalt.motion.move_to" for m in messages)
        positions = [
            f["value"]
            for r in commits
            for f in r["facts"]
            if f["fieldId"] == "lowalt.position"
        ]
        assert positions and all(tuple(p) == (-240.0, -220.0, 50.0) for p in positions)
    finally:
        sim.close()


def test_en_route_charger_egress_preserves_pad_goal_and_finishes_recall(
    monkeypatch: Any,
) -> None:
    document = _one_order()
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group["arrivals"]["times_ns"] = [100 * 10**9]
    document["generators"]["fleet"]["starts"] = [[-240.0, -220.0, 140.0]]
    document["generators"]["fleet"]["facts"].update(
        {"lowalt.soc": 0.1, "lowalt.motion_energy_j": 24000.0}
    )
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    zone["facts"].update(
        {
            "lowalt.zone_lower": [-260.0, -240.0, 90.0],
            "lowalt.zone_upper": [-220.0, -200.0, 150.0],
        }
    )
    egress = []
    original = LowaltTasks._route

    def capture(self: LowaltTasks, ctx: Any, flight: Any, goal: Any) -> bool:
        result = original(self, ctx, flight, goal)
        if flight.egress_waypoint is not None:
            egress.append((flight.purpose, flight.goal, flight.egress_waypoint))
        return result

    monkeypatch.setattr(LowaltTasks, "_route", capture)
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(90 * 10**9)
        commits = [project(r) for r in sim.kernel.iter_records()]
        messages = [m for r in commits for m in r["messages"]]
        assert egress and all(
            purpose == "charge" and goal == (-240.0, -220.0, 50.0)
            for purpose, goal, _ in egress
        )
        finished = [
            m for m in messages if m["schemaId"] == "lowalt.energy.charging_finished"
        ]
        assert len(finished) == 1
        finish_index = next(
            c["commitIndex"] for c in commits if finished[0] in c["messages"]
        )
        assert (
            _native_arrival_index(commits, egress[0][2])
            < _native_arrival_index(commits, (-240.0, -220.0, 50.0))
            < finish_index
        )
        recall_flags = [
            f["value"]
            for r in commits
            for f in r["facts"]
            if f["fieldId"] == "lowalt.returning_to_charge"
        ]
        assert recall_flags == [False, True, False]
    finally:
        sim.close()


@pytest.mark.parametrize(
    "algorithm,kind",
    [("patrol", "patrol"), ("survey", "inspection"), ("coverage", "inspection")],
)
def test_egress_preserves_every_original_work_waypoint_and_observation(
    tmp_path: Path, algorithm: str, kind: str
) -> None:
    document = _one_order()
    document["engines"]["tasks"]["config"]["generators"][0]["kind"] = kind
    authored = [(-200.0, -220.0, 50.0), (-200.0, -160.0, 50.0)]
    config: dict[str, Any] = {"waypoints": [list(p) for p in authored]}
    if algorithm == "patrol":
        config["close"] = False
    elif algorithm == "coverage":
        config = {
            "lower": [-200.0, -220.0],
            "upper": [-180.0, -180.0],
            "spacing_m": 20.0,
        }
        authored = [
            (-200.0, -220.0, 50.0),
            (-180.0, -220.0, 50.0),
            (-180.0, -200.0, 50.0),
            (-200.0, -200.0, 50.0),
            (-200.0, -180.0, 50.0),
            (-180.0, -180.0, 50.0),
        ]
    document["engines"]["tasks"]["config"]["work_routes"] = {
        kind: {"algorithm": algorithm, "config": config}
    }
    scenario = load_scenario(document, base=BASE)
    journal = tmp_path / "work.jsonl"
    sim = Simulation(scenario, journal=Journal(journal))
    try:
        sim.start()
        sim.run_until(65 * 10**9)
        commits = [project(r) for r in sim.kernel.iter_records()]
        messages = [m for c in commits for m in c["messages"]]
        completed = [m for m in messages if m["schemaId"] == "lowalt.task.completed"]
        assert len(completed) == 1
        complete_ns = int(completed[0]["at"]["ns"])
        assert complete_ns < 70 * 10**9  # The restriction is still active.
        goal = tuple(scenario.initial["customer-1"]["lowalt.site_position"])
        arrival_indices = [_native_arrival_index(commits, p) for p in (*authored, goal)]
        completion_index = next(
            c["commitIndex"] for c in commits if completed[0] in c["messages"]
        )
        assert all(a < b for a, b in zip(arrival_indices, arrival_indices[1:]))
        assert arrival_indices[-1] < completion_index
        schema = (
            "lowalt.patrol.visited"
            if kind == "patrol"
            else "lowalt.inspection.observed"
        )
        observed = [m for m in messages if m["schemaId"] == schema]
        assert [tuple(m["payload"]["position"]) for m in observed] == authored + [goal]
        observation_indices = [
            c["commitIndex"]
            for c in commits
            for m in c["messages"]
            if m["schemaId"] == schema
        ]
        assert all(a < o for a, o in zip(arrival_indices, observation_indices))
        # The emergency exit has a real receipt but credits no work observation.
        assert (
            _native_arrival_index(commits, (-261.0, -220.0, 50.0))
            < arrival_indices[0]
        )
        if kind != "patrol":
            entry = next(
                m for m in observed if tuple(m["payload"]["position"]) == authored[0]
            )
            assert (
                entry["payload"]["distance_m"] == 0.0
            )  # No transit/egress distance credited as work.
        ref = next(r for r in scenario.manifest.entities if r.id == "task-1")
        view = sim.kernel.view()
        assert view.field((ref, "lowalt.task_state"), view.instant).value == "completed"
    finally:
        sim.close()
    recorded = replay(journal).view()
    assert (
        recorded.field((ref, "lowalt.task_state"), recorded.instant).value
        == "completed"
    )


@pytest.mark.parametrize("action", ["altitude", "return_to_pad"])
def test_work_obligations_survive_charge_and_altitude_control(action: str) -> None:
    scenario = load_scenario(_one_order(), base=BASE)
    build = EngineBuild(
        "tasks",
        scenario.engines["tasks"]["config"],
        scenario.registry,
        scenario.manifest,
        scenario.manifest.entities,
        scenario.initial,
    )
    tasks = LowaltTasks(build)
    flight = tasks.flights["drone-1"]
    task_ref = next(r for r in scenario.manifest.entities if r.id == "task-1")
    flight.job = Job(task_ref, 0, phase="picked_up")
    obligations = (
        (-200.0, -220.0, 50.0),
        (-200.0, -160.0, 50.0),
        (-240.0, -120.0, 50.0),
    )
    flight.job.work_obligations = obligations
    flight.work_active = True
    flight.route, flight.goal = obligations, obligations[-1]
    requests = []
    # This unit test submits real command requests but fabricates NO receipts
    # or completion; it isolates metadata preservation at the interrupt cut.
    ctx = SimpleNamespace(
        now=Instant(10 * 10**9),
        get=lambda ref, field: (-240.0, -220.0, 50.0),
        submit=requests.append,
    )
    tasks._control(
        cast(EngineContext, ctx),
        {"vehicle": "drone-1", "action": action, "target": [-240.0, -220.0, 125.0]},
    )
    assert requests
    assert flight.job.work_obligations is obligations
    assert flight.saved_goal == obligations[-1]
    assert flight.job.phase == "picked_up"
