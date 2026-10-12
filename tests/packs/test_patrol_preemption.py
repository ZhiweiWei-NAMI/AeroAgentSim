"""Configured patrol interrupts reuse receipt-driven suspension and work routes."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from aerokernel import Fact, Instant, Interval, Stamp
from aerokernel.ids import EntityRef, ItemRef
from aerokernel.messages import Dirty, Emit
from aerokernel.operations import FactWrite
from aerokernel.sdk import EngineContext
from aerokernel.state import StateView
from aerokernel.values import freeze, thaw

from aeroagentsim.packs.lowalt.tasks import Job, LowaltTasks
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import load_scenario

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/security-patrol"
SECOND = 10**9


@pytest.fixture
def patrol() -> tuple[LowaltTasks, dict[str, dict[str, Any]]]:
    document = yaml.safe_load((BASE / "scenario.yaml").read_text())
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group["tasks"] = ["patrol-1"]
    group["arrivals"]["times_ns"] = [0]
    document["entities"] = [e for e in document["entities"] if e["id"] not in {"patrol-2", "patrol-3"}]
    scenario = load_scenario(document, base=BASE)
    tasks = LowaltTasks(EngineBuild(
        "tasks", scenario.engines["tasks"]["config"], scenario.registry,
        scenario.manifest, scenario.manifest.entities, scenario.initial,
    ))
    facts = copy.deepcopy(scenario.initial)
    facts["weather"]["lowalt.wind"] = scenario.engines["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]["value"]
    flight = tasks.flights["drone-1"]
    waypoints = tasks.cfg["work_routes"]["patrol"]["config"]["waypoints"]
    remaining = tuple(tuple(point) for point in waypoints[2:])
    routine = Job(tasks.by_id["patrol-1"], 0, vehicle="drone-1", phase="picked_up", work_obligations=remaining)
    urgent = Job(tasks.by_id["investigation-1"], 56 * SECOND, phase="queued", created_ns=56 * SECOND)
    tasks.jobs = {routine.ref.id: routine, urgent.ref.id: urgent}
    for job in tasks.jobs.values():
        facts[job.ref.id]["lowalt.task_state"] = job.phase
    facts["drone-1"]["lowalt.position"] = waypoints[1]
    flight.job, flight.route = routine, remaining
    flight.goal = tuple(facts["guard-return"]["lowalt.site_position"])
    flight.work_active = True
    flight.command_id = "patrol-leg"
    flight.waypoint, flight.waypoint_start = remaining[0], tuple(waypoints[1])
    flight.waypoint_is_work = True
    tasks.moves["patrol-leg"] = flight
    return tasks, facts


def context(
    tasks: LowaltTasks, facts: dict[str, dict[str, Any]], at: int = 56,
    dirty: tuple[Dirty, ...] = (),
) -> EngineContext:
    now = Instant(at * SECOND)

    def field(key: tuple[EntityRef, str], valid_at: Instant) -> Fact:
        return Fact(
            key, freeze(facts[key[0].id][key[1]]), Stamp("canonical", now.ns, 1, "canonical"),
            now.ns, now, Interval(now, None), "fixture", ItemRef(1, 0),
        )

    view = cast(StateView, SimpleNamespace(instant=now, provenance="lean", field=field))
    return EngineContext(view, dirty=dirty, policies=tasks.policies)


def apply(ctx: EngineContext, facts: dict[str, dict[str, Any]]) -> None:
    for op in ctx.ops:
        if isinstance(op, FactWrite):
            facts[op.key[0].id][op.key[1]] = thaw(op.value)


def events(ctx: EngineContext) -> list[Emit]:
    return [op for op in ctx.ops if isinstance(op, Emit) and op.kind == "event"]


def receipt(command: str, status: str, at: int) -> Dirty:
    return Dirty(None, "receipt", Instant(at * SECOND), ItemRef(1, 0), payload={"command_id": command, "status": status})


def test_template_investigation_preempts_active_patrol_and_brakes(patrol: Any) -> None:
    tasks, facts = patrol
    flight = tasks.flights["drone-1"]
    routine, remaining = flight.job, flight.job.work_obligations
    ctx = context(tasks, facts)
    tasks._preempt(ctx)
    assert flight.suspended is routine
    assert flight.suspended_phase == "picked_up"
    assert routine.phase == "paused"
    assert routine.work_obligations is remaining
    assert flight.job is tasks.jobs["investigation-1"]
    assert flight.job.phase == "assigned"
    assert flight.route == () and flight.goal is None
    assert flight.pending and flight.command_id is None
    assert [op.schema_id for op in events(ctx)] == ["lowalt.task.preempted", "lowalt.task.assigned"]
    assert events(ctx)[0].payload["urgent_task"] == "investigation-1"
    hold = next(op for op in ctx.ops if isinstance(op, Emit) and op.kind == "command")
    assert hold.schema_id == tasks.motion["hold"]
    assert hold.payload == {"entity": "drone-1"}


@pytest.mark.parametrize("medical", [False, True])
def test_completion_resumes_suspended_work_and_payload(patrol: Any, medical: bool) -> None:
    tasks, facts = patrol
    if medical:
        tasks.cfg["medical_preemption"] = True
        facts["patrol-1"].update({"lowalt.task_kind": "parcel", "lowalt.task_payload_kg": 1.5})
        facts["investigation-1"].update({"lowalt.task_kind": "medical", "lowalt.task_payload_kg": 0.5})
        facts["drone-1"]["lowalt.payload_kg"] = 1.5
        tasks.jobs["patrol-1"].work_obligations = None
    flight = tasks.flights["drone-1"]
    routine, remaining = flight.job, flight.job.work_obligations
    preempt = context(tasks, facts)
    tasks._preempt(preempt)
    apply(preempt, facts)
    stopped = context(tasks, facts, 57, (
        receipt("urgent-hold", "submitted", 57),
        receipt("patrol-leg", "canceled", 57),
        receipt("urgent-hold", "succeeded", 57),
    ))
    tasks.on_inputs(stopped)
    assert flight.job.phase == "assigned"
    assert routine.phase == "paused"
    assert not any(op.schema_id == "lowalt.task.failed" for op in events(stopped))
    started = context(tasks, facts, 58)
    tasks._poll(started)
    apply(started, facts)
    assert flight.job.phase == "started"
    assert any(op.schema_id == "lowalt.task.started" for op in events(started))
    assert flight.pending

    # Isolate the completion cut: a carried urgent task reaches its real site.
    urgent = flight.job
    urgent.phase = "picked_up"
    facts[urgent.ref.id]["lowalt.task_state"] = "picked_up"
    destination = tuple(facts["incident-site"]["lowalt.site_position"])
    facts["drone-1"]["lowalt.position"] = list(destination)
    facts["drone-1"]["lowalt.payload_kg"] = 2.0 if medical else 0.0
    flight.route, flight.goal, flight.pending = (), destination, False
    flight.command_id, flight.waypoint = "urgent-arrival", destination
    flight.waypoint_start = tuple(facts["home"]["lowalt.site_position"])
    flight.waypoint_is_work = False
    tasks.pending.clear()
    tasks.moves["urgent-arrival"] = flight
    arrived = context(tasks, facts, 80, (receipt("urgent-arrival", "succeeded", 80),))
    tasks.on_inputs(arrived)
    tasks._poll(arrived)
    apply(arrived, facts)
    assert [op.schema_id for op in events(arrived)] == ["lowalt.task.completed", "lowalt.task.resumed"]
    assert flight.job is routine and routine.phase == "picked_up"
    assert flight.suspended is None and flight.suspended_phase is None
    assert facts["drone-1"]["lowalt.payload_kg"] == (1.5 if medical else 0.0)
    assert routine.work_obligations is remaining
    if not medical:
        goal = tuple(facts["guard-return"]["lowalt.site_position"])
        assert tasks._route(context(tasks, facts, 81), flight, goal)
        expected = remaining + ((goal,) if remaining[-1] != goal else ())
        assert flight.route[-len(expected):] == expected
        assert flight.work_reconnect_goal == remaining[0]


@pytest.mark.parametrize("restriction", ["disabled", "equal_priority", "wrong_kind", "payload", "weather", "ineligible", "suspended"])
def test_preemption_respects_config_priority_and_eligibility(patrol: Any, restriction: str) -> None:
    tasks, facts = patrol
    flight = tasks.flights["drone-1"]
    routine = flight.job
    if restriction == "disabled":
        tasks.cfg.pop("priority_preemption")
    elif restriction == "equal_priority":
        facts["investigation-1"]["lowalt.task_priority"] = 1
    elif restriction == "wrong_kind":
        facts["patrol-1"]["lowalt.task_kind"] = "parcel"
    elif restriction == "payload":
        facts["investigation-1"]["lowalt.task_payload_kg"] = 4.0
    elif restriction == "weather":
        facts["drone-1"]["lowalt.weather_held"] = True
    elif restriction == "ineligible":
        tasks.cfg["vehicle_task_kinds"] = {"drone-1": ["patrol"]}
    else:
        flight.suspended = routine
    ctx = context(tasks, facts)
    tasks._preempt(ctx)
    assert flight.job is routine and routine.phase == "picked_up"
    assert ctx.ops == []


def test_medical_switch_still_controls_parcel_preemption(patrol: Any) -> None:
    tasks, facts = patrol
    facts["patrol-1"]["lowalt.task_kind"] = "parcel"
    facts["investigation-1"]["lowalt.task_kind"] = "medical"
    disabled = context(tasks, facts)
    tasks._preempt(disabled)
    assert disabled.ops == []
    tasks.cfg["medical_preemption"] = True
    enabled = context(tasks, facts)
    tasks._preempt(enabled)
    assert [op.schema_id for op in events(enabled)] == ["lowalt.task.preempted", "lowalt.task.assigned"]
