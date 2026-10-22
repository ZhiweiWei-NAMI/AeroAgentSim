"""Non-Rescue mission clients exercise only public commands and committed reads.

No test reaches into Tasks, Flight, Job, or the reservation controller. The
small client below is a real ContextEngine: its forwarding acknowledgement
reveals a child command ID only after the kernel routes the submitted receipt.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest
from aerokernel import CommandRequest, EntityRef, Fact, Instant, Partition, replay
from aerokernel.sdk import (
    Command,
    ContextEngine,
    EngineContext,
    dispatch_commands,
    handles,
)
from aerokernel.values import freeze, thaw

from aeroagentsim.mission_api import (
    DIVERT_SCHEMA,
    MISSION_COMMAND_SCHEMAS,
    RELEASE_SCHEMA,
    RESERVE_SCHEMA,
    RESUME_SCHEMA,
    DivertPayload,
    MissionPayload,
    MissionResult,
    ReleasePayload,
    ReservePayload,
    ResultCode,
    ResumePayload,
    build_payload,
    build_result,
    mission_descriptors,
    validate_payload,
    validate_result,
)
from aeroagentsim.packs.lowalt.template import SECOND, parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"
ISSUE_SCHEMA = "test.mission.issue"
TERMINAL = {"succeeded", "failed", "rejected", "canceled"}
DIVERSION = (-360.0, -220.0, 80.0)
ORIGIN = (-240.0, -120.0, 50.0)
DESTINATION = (-240.0, 120.0, 50.0)


class MissionClient(ContextEngine):
    """An independent caller, not an embedded Rescue participant or owner."""

    def __init__(self, build: EngineBuild) -> None:
        self.pending: deque[Command[Any]] = deque()
        super().__init__(Partition(
            build.id,
            build.id,
            commands=(ISSUE_SCHEMA,),
            emits=tuple(MISSION_COMMAND_SCHEMAS),
            message_targets=("tasks",),
        ))

    @handles(ISSUE_SCHEMA)
    def issue(self, ctx: EngineContext, command: Command[Any]) -> None:
        ctx.accept(command)
        ctx.execute(command)
        data = command.payload
        ctx.submit(CommandRequest(
            data["schema_id"], "tasks", ctx.now, json.loads(data["payload_json"]),
        ))
        # This fixture has exactly one category of child command. In particular,
        # it does not mix mission submissions with the owner's motion FIFO.
        self.pending.append(command)

    def on_inputs(self, ctx: EngineContext) -> None:
        dispatch_commands(
            self, ctx, tuple(d for d in ctx.inbox if d.message.kind == "command"),
        )
        for dirty in ctx.dirty:
            if dirty.kind == "receipt" and dirty.payload["status"] == "submitted":
                ctx.succeed(self.pending.popleft(), {
                    "command_id": dirty.payload["command_id"],
                })


class ClientCatalog(EngineCatalog):
    def build(self, plugin: str, context: EngineBuild) -> Any:
        if plugin == "test-mission-client":
            return MissionClient(context)
        return super().build(plugin, context)


def _document(*, release_ns: int = 0) -> dict[str, Any]:
    document = parcel_document(fleet_size=1, wind_speed=0.0)
    keep = {
        "weather", "vertiport-1", "customer-1", "pad-1", "central-no-fly", "task-1",
    }
    document["entities"] = [e for e in document["entities"] if e["id"] in keep]
    for entity in document["entities"]:
        if entity["id"] == "vertiport-1":
            entity["facts"]["lowalt.site_position"] = list(ORIGIN)
        elif entity["id"] == "customer-1":
            entity["facts"]["lowalt.site_position"] = list(DESTINATION)
    generator = document["engines"]["tasks"]["config"]["generators"][0]
    generator.update(
        tasks=["task-1"], origins=["vertiport-1"], destinations=["customer-1"],
        arrivals={"mode": "scheduled", "times_ns": [release_ns]},
    )
    document["registry"]["messages"].append({
        "id": ISSUE_SCHEMA,
        "kind": "command",
        "schema": {
            "type": "record",
            "members": {
                "schema_id": {"type": "string", "enum": list(MISSION_COMMAND_SCHEMAS)},
                "payload_json": {"type": "string"},
            },
            "required": ["schema_id", "payload_json"],
            "extra": False,
        },
        "result_schema": {
            "type": "record",
            "members": {"command_id": {"type": "string"}},
            "required": ["command_id"],
            "extra": False,
        },
    })
    for identity in ("mission-client", "other-client"):
        document["engines"][identity] = {"plugin": "test-mission-client", "config": {}}
    return document


@pytest.fixture
def simulation() -> Iterator[Callable[..., Simulation]]:
    opened: list[Simulation] = []

    def make(
        document: dict[str, Any] | None = None, *, provenance: str = "full",
    ) -> Simulation:
        sim = Simulation(
            load_scenario(_document() if document is None else document, base=BASE),
            catalog=ClientCatalog(), provenance=provenance,
        )
        opened.append(sim)
        sim.start()
        return sim

    try:
        yield make
    finally:
        for sim in reversed(opened):
            sim.close()


def _now(sim: Simulation) -> int:
    return sim.kernel.view().instant.ns


def _ref(sim: Simulation, identity: str = "drone-1") -> EntityRef:
    return next(r for r in sim.scenario.manifest.entities if r.id == identity)


def _field(sim: Simulation, field: str, identity: str = "drone-1") -> Any:
    view = sim.kernel.view()
    fact = view.field((_ref(sim, identity), field), view.instant)
    assert isinstance(fact, Fact), (identity, field)
    return thaw(fact.value)


def _events(sim: Simulation, schema: str) -> list[dict[str, Any]]:
    return [
        message
        for record in sim.kernel.iter_records()
        for message in project(record)["messages"]
        if message["schemaId"] == schema
    ]


def _until(sim: Simulation, predicate: Callable[[], bool], *, seconds: int = 60) -> None:
    limit = _now(sim) + seconds * SECOND
    while not predicate() and _now(sim) < limit:
        sim.run_until(min(limit, _now(sim) + SECOND))
    assert predicate(), f"condition not committed by {limit} ns"


def _terminal_data(sim: Simulation, command_id: str) -> dict[str, Any]:
    action = sim.kernel.action(command_id)
    assert action.status in TERMINAL, (command_id, action.status)
    # Feedback and rejected cancellation decisions can follow a terminal receipt;
    # history[-1] is not a general result API.
    receipt = next(
        item for item in reversed(action.history)
        if item["kind"] == "receipt" and item["status"] in TERMINAL
    )
    return cast(dict[str, Any], thaw(receipt["result"]))


def _issue(
    sim: Simulation, schema: str, payload: MissionPayload | dict[str, Any],
    *, client: str = "mission-client",
) -> str:
    at = _now(sim) + 1  # Host ingress cannot reopen an already sealed instant.
    wire = payload if isinstance(payload, dict) else build_payload(payload)
    wrapper = sim.kernel.submit(CommandRequest(
        ISSUE_SCHEMA, client, Instant(at),
        {"schema_id": schema, "payload_json": json.dumps(wire)},
    ))
    sim.run_until(at)
    assert sim.kernel.action(wrapper).status == "succeeded"
    command_id = _terminal_data(sim, wrapper)["command_id"]
    action = sim.kernel.action(command_id)
    assert action.source_kind == "partition" and action.source == client
    return str(command_id)


def _finish(sim: Simulation, command_id: str, *, seconds: int = 60) -> MissionResult:
    _until(sim, lambda: sim.kernel.action(command_id).status in TERMINAL, seconds=seconds)
    result = validate_result(_terminal_data(sim, command_id))
    assert result.command_id == command_id
    return result


def _expect(
    sim: Simulation, command_id: str, code: str = "ok", *, status: str = "succeeded",
) -> MissionResult:
    result = _finish(sim, command_id)
    assert sim.kernel.action(command_id).status == status
    assert result.code == ResultCode(code)
    return result


def _reserve_payload(sim: Simulation, request_id: str = "reserve", **changes: Any) -> ReservePayload:
    return ReservePayload(**{
        "vehicle": _ref(sim), "mission_id": "independent-mission", "request_id": request_id,
        "deadline_ns": _now(sim) + 90 * SECOND,
        "expires_ns": _now(sim) + 200 * SECOND,
        **changes,
    })


def _reserve(sim: Simulation, request_id: str = "reserve", **changes: Any) -> MissionResult:
    return _expect(sim, _issue(sim, RESERVE_SCHEMA, _reserve_payload(sim, request_id, **changes)))


def _operation(sim: Simulation, lease: MissionResult, request_id: str, **changes: Any) -> dict[str, Any]:
    assert lease.reservation_token
    return {
        "vehicle": lease.vehicle, "mission_id": lease.mission_id, "request_id": request_id,
        "deadline_ns": _now(sim) + 90 * SECOND,
        "reservation_token": lease.reservation_token,
        "expected_owner_revision": lease.owner_revision,
        **changes,
    }


def _divert(sim: Simulation, lease: MissionResult, request_id: str = "divert") -> str:
    return _issue(sim, DIVERT_SCHEMA, DivertPayload(
        **_operation(sim, lease, request_id), target=DIVERSION, frame="enu",
    ))


def _release(
    sim: Simulation, lease: MissionResult, disposition: str = "resume_saved",
    request_id: str = "release",
) -> str:
    return _issue(sim, RELEASE_SCHEMA, ReleasePayload(
        **_operation(sim, lease, request_id), disposition=disposition,
    ))


def _motion_proofs(sim: Simulation, result: MissionResult, *, moved: bool = False) -> None:
    assert result.downstream_command_ids
    assert result.downstream_receipt_refs
    records = {record["index"]: record for record in sim.kernel.iter_records()}
    messages = {
        message["id"]: message
        for record in records.values()
        for message in project(record)["messages"]
    }
    terminal_refs: set[tuple[int, int]] = set()
    for command_id in result.downstream_command_ids:
        action = sim.kernel.action(command_id)
        assert action.target == "motion" and action.status in TERMINAL
        for item in action.history:
            if item["kind"] == "receipt" and item["status"] in TERMINAL:
                terminal_refs.add((item["ref"]["record_index"], item["ref"]["item_index"]))
    assert set(result.downstream_receipt_refs) <= terminal_refs
    for record_index, item_index in result.downstream_receipt_refs:
        # Record IDs are not Python sequence offsets; item ordinals index ALL
        # items, not just receipts or operations (nor receipt_route copies).
        receipt = records[record_index]["items"][item_index]["receipt"]
        assert receipt["kind"] == "receipt" and receipt["status"] in TERMINAL
        assert receipt["command_id"] in result.downstream_command_ids
        assert receipt["ref"] == {"record_index": record_index, "item_index": item_index}
    assert any(
        sim.kernel.action(command_id).status == "succeeded"
        and messages[command_id]["schemaId"] == "lowalt.motion.hold"
        for command_id in result.downstream_command_ids
    )
    if moved:
        assert any(
            sim.kernel.action(command_id).status == "succeeded"
            and messages[command_id]["schemaId"] == "lowalt.motion.move_to"
            for command_id in result.downstream_command_ids
        )
    assert _field(sim, "lowalt.velocity") == pytest.approx([0.0, 0.0, 0.0])


@pytest.mark.parametrize("provenance", ["full", "lean"])
def test_nonrescue_lifecycle_restores_original_task_and_replays_without_execution(
    simulation: Callable[..., Simulation], monkeypatch: pytest.MonkeyPatch, provenance: str,
) -> None:
    sim = simulation(provenance=provenance)
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "started")
    assert _field(sim, "lowalt.payload_kg") == 0.0
    reserve_id = _issue(sim, RESERVE_SCHEMA, _reserve_payload(sim))
    assert sim.kernel.action(reserve_id).status != "succeeded"
    reserved = _expect(sim, reserve_id)
    assert reserved.owner_revision == 1
    assert _field(sim, "lowalt.task_state", "task-1") == "paused"
    _motion_proofs(sim, reserved)

    divert_id = _divert(sim, reserved)
    assert sim.kernel.action(divert_id).status != "succeeded"
    sim.run_until(_now(sim) + SECOND)
    assert sim.kernel.action(divert_id).status != "succeeded"
    diverted = _expect(sim, divert_id)
    assert diverted.owner_revision == 2
    assert diverted.reservation_token == reserved.reservation_token
    assert _field(sim, "lowalt.position") == pytest.approx(DIVERSION)
    assert _field(sim, "lowalt.task_state", "task-1") == "paused"
    assert not _events(sim, "lowalt.task.completed")
    _motion_proofs(sim, diverted, moved=True)

    release_id = _release(sim, diverted)
    released = _expect(sim, release_id)
    assert released.owner_revision == 3
    assert _field(sim, "lowalt.task_state", "task-1") == "started"
    assert _field(sim, "lowalt.payload_kg") == 0.0
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed")
    assert _field(sim, "lowalt.position") == pytest.approx(DESTINATION)
    assert _field(sim, "lowalt.payload_kg") == 0.0
    assert _field(sim, "lowalt.task_vehicle", "task-1") == "drone-1"
    assert len(_events(sim, "lowalt.task.picked_up")) == 1
    assert len(_events(sim, "lowalt.task.completed")) == 1
    assert len(_events(sim, "lowalt.task.resumed")) == 1
    assert not _events(sim, "lowalt.task.failed")

    live = sim.kernel.view()
    command_ids = (reserve_id, divert_id, release_id)
    expected_actions = {cid: sim.kernel.action(cid) for cid in command_ids}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("engine/provider executed during journal replay")

    with monkeypatch.context() as patch:
        patch.setattr(EngineCatalog, "build", forbidden)
        patch.setattr(ClientCatalog, "build", forbidden)
        for hook in ("reset", "advance", "react", "horizon"):
            patch.setattr(ContextEngine, hook, forbidden)
        patch.setattr("aeroagentsim.algorithms.load_algorithm", forbidden)
        recorded = replay(sim.kernel.journal.bytes)
    assert not recorded.incomplete
    assert recorded.records == sim.kernel.records
    assert {cid: recorded.action(cid) for cid in command_ids} == expected_actions
    replayed = recorded.view()
    for identity, field in (
        ("drone-1", "lowalt.position"), ("drone-1", "lowalt.velocity"),
        ("drone-1", "lowalt.payload_kg"), ("task-1", "lowalt.task_state"),
        ("task-1", "lowalt.task_vehicle"),
    ):
        key = (_ref(sim, identity), field)
        assert replayed.field(key, replayed.instant) == live.field(key, live.instant)


def test_remaining_original_work_waypoints_survive_divert_and_resume_saved(
    simulation: Callable[..., Simulation],
) -> None:
    document = _document()
    config = document["engines"]["tasks"]["config"]
    config["generators"][0]["kind"] = "inspection"
    waypoints = [(-240.0, -80.0, 50.0), (-240.0, 60.0, 50.0)]
    config["work_routes"] = {"inspection": {
        "algorithm": "patrol", "config": {"waypoints": [list(p) for p in waypoints], "close": False},
    }}
    sim = simulation(document)
    _until(sim, lambda: bool(_events(sim, "lowalt.inspection.observed")))
    assert [tuple(m["payload"]["position"]) for m in _events(sim, "lowalt.inspection.observed")] == waypoints[:1]
    assert _field(sim, "lowalt.task_state", "task-1") == "picked_up"
    carried = _field(sim, "lowalt.payload_kg")
    reserved = _reserve(sim)
    diverted = _expect(sim, _divert(sim, reserved))
    _expect(sim, _release(sim, diverted))
    assert _field(sim, "lowalt.task_state", "task-1") == "picked_up"
    assert _field(sim, "lowalt.payload_kg") == carried
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed", seconds=100)
    observed = _events(sim, "lowalt.inspection.observed")
    assert [tuple(m["payload"]["position"]) for m in observed] == [*waypoints, DESTINATION]
    assert len(_events(sim, "lowalt.task.picked_up")) == 1
    assert not _events(sim, "lowalt.task.failed")


def test_release_idle_fails_saved_unloaded_task_and_retires_token(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation()
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "started")
    reserved = _reserve(sim)
    released = _expect(sim, _release(sim, reserved, "idle"))
    assert released.owner_revision == 2
    assert _field(sim, "lowalt.task_state", "task-1") == "failed"
    assert _field(sim, "lowalt.task_vehicle", "task-1") == ""
    assert _field(sim, "lowalt.payload_kg") == 0.0
    sim.run_until(_now(sim) + 20 * SECOND)
    assert len(_events(sim, "lowalt.task.failed")) == 1
    assert not _events(sim, "lowalt.task.completed")
    stale = _issue(sim, RESUME_SCHEMA, ResumePayload(**_operation(sim, released, "stale")))
    result = _expect(sim, stale, "stale_token", status="rejected")
    assert result.owner_revision == released.owner_revision


def test_loaded_idle_release_is_protected_without_losing_original_payload(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation()
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "picked_up")
    carried = _field(sim, "lowalt.payload_kg")
    assert carried > 0
    reserved = _reserve(sim)
    rejected = _expect(sim, _release(sim, reserved, "idle"), "protected", status="rejected")
    assert rejected.owner_revision == reserved.owner_revision
    assert _field(sim, "lowalt.payload_kg") == carried
    assert _field(sim, "lowalt.task_state", "task-1") == "paused"
    _expect(sim, _release(sim, reserved, request_id="release-saved"))
    assert _field(sim, "lowalt.task_state", "task-1") == "picked_up"
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed")
    assert len(_events(sim, "lowalt.task.picked_up")) == 1
    assert _field(sim, "lowalt.payload_kg") == 0.0
    assert not _events(sim, "lowalt.task.failed")


def test_double_reserve_and_foreign_origin_cannot_take_an_active_token(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    reserved = _reserve(sim)
    again = _issue(sim, RESERVE_SCHEMA, _reserve_payload(sim, "reserve-again"))
    _expect(sim, again, "already_reserved", status="rejected")
    foreign = _issue(sim, RESUME_SCHEMA, ResumePayload(
        **_operation(sim, reserved, "foreign-resume"),
    ), client="other-client")
    _expect(sim, foreign, "unknown_token", status="rejected")
    unknown = _issue(sim, RESUME_SCHEMA, ResumePayload(**_operation(
        sim, reserved, "unknown-token", reservation_token="never-issued",
        expected_owner_revision=999,
    )))
    # A wrong capability is rejected before inspecting its claimed revision.
    rejected = _expect(sim, unknown, "unknown_token", status="rejected")
    assert rejected.owner_revision == reserved.owner_revision
    assert _field(sim, "lowalt.velocity") == pytest.approx([0.0, 0.0, 0.0])
    released = _expect(sim, _release(sim, reserved, "idle"))
    assert released.owner_revision == 2


def test_divert_before_reserve_is_rejected_without_motion_or_completion(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    before = _field(sim, "lowalt.position")
    command_id = _issue(sim, DIVERT_SCHEMA, DivertPayload(
        vehicle=_ref(sim), mission_id="independent-mission", request_id="no-reservation",
        deadline_ns=_now(sim) + 30 * SECOND, reservation_token="never-issued",
        expected_owner_revision=0, target=DIVERSION, frame="enu",
    ))
    result = _expect(sim, command_id, "not_reserved", status="rejected")
    assert result.owner_revision == 0
    assert not result.downstream_command_ids
    assert _field(sim, "lowalt.position") == before
    assert not _events(sim, "lowalt.motion.move_to")


def test_duplicate_and_conflicting_request_ids_do_not_reexecute_reservation(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    payload = _reserve_payload(sim)
    reserved = _expect(sim, _issue(sim, RESERVE_SCHEMA, payload))
    holds = len(_events(sim, "lowalt.motion.hold"))
    duplicate = _expect(sim, _issue(sim, RESERVE_SCHEMA, payload), "duplicate_request", status="rejected")
    conflict = _expect(sim, _issue(sim, RESERVE_SCHEMA, replace(
        payload, expires_ns=payload.expires_ns + SECOND,
    )), "request_conflict", status="rejected")
    assert duplicate.owner_revision == conflict.owner_revision == reserved.owner_revision
    assert len(_events(sim, "lowalt.motion.hold")) == holds
    # The duplicate namespace is the origin, not just an unqualified request ID.
    other = _issue(sim, RESERVE_SCHEMA, payload, client="other-client")
    _expect(sim, other, "already_reserved", status="rejected")


def test_stale_revisions_and_unsupported_frame_leave_current_revision_usable(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    reserved = _reserve(sim)
    for request_id, revision, frame, code in (
        ("old-revision", 0, "enu", "stale_revision"),
        ("wrong-frame", 1, "ned", "unsupported_frame"),
    ):
        command_id = _issue(sim, DIVERT_SCHEMA, DivertPayload(
            **_operation(sim, reserved, request_id, expected_owner_revision=revision),
            target=DIVERSION, frame=frame,
        ))
        rejected = _expect(sim, command_id, code, status="rejected")
        assert rejected.owner_revision == 1
    diverted = _expect(sim, _divert(sim, reserved))
    assert diverted.owner_revision == 2
    old = _issue(sim, RESUME_SCHEMA, ResumePayload(**_operation(sim, reserved, "old-resume")))
    _expect(sim, old, "stale_revision", status="rejected")
    released = _expect(sim, _release(sim, diverted, "idle"))
    assert released.owner_revision == 3


def test_resumed_idle_vehicle_remains_excluded_from_allocation_until_release(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=6 * SECOND))
    reserved = _reserve(sim)
    resumed = _expect(sim, _issue(sim, RESUME_SCHEMA, ResumePayload(
        **_operation(sim, reserved, "resume"),
    )))
    assert resumed.owner_revision == 2
    sim.run_until(15 * SECOND)
    assert _field(sim, "lowalt.task_state", "task-1") == "queued"
    assert not _events(sim, "lowalt.task.assigned")
    released = _expect(sim, _release(sim, resumed))
    assert released.owner_revision == 3
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed")
    assert len(_events(sim, "lowalt.task.assigned")) == 1


def test_idle_release_after_resume_preserves_live_charging_recall(
    simulation: Callable[..., Simulation],
) -> None:
    document = _document(release_ns=100 * SECOND)
    document["generators"]["fleet"]["starts"] = [list(ORIGIN)]
    document["generators"]["fleet"]["facts"].update({
        "lowalt.soc": 0.205, "lowalt.motion_energy_j": 49200.0,
    })
    sim = simulation(document)
    reserved = _reserve(sim)
    resumed = _expect(sim, _issue(sim, RESUME_SCHEMA, ResumePayload(
        **_operation(sim, reserved, "resume-before-recall"),
    )))
    _until(sim, lambda: (
        _field(sim, "lowalt.returning_to_charge") is True
        and bool(_events(sim, "lowalt.motion.move_to"))
        and any(abs(v) > 1e-6 for v in _field(sim, "lowalt.velocity"))
    ))
    assert _field(sim, "lowalt.payload_kg") == 0.0
    pad = _field(sim, "lowalt.site_position", "pad-1")
    assert _field(sim, "lowalt.position") != pad
    charge_move = _events(sim, "lowalt.motion.move_to")[-1]["id"]
    recall_id = _events(sim, "lowalt.energy.return_to_pad")[-1]["id"]
    holds = len(_events(sim, "lowalt.motion.hold"))
    rejected = _expect(sim, _release(sim, resumed, "idle"), "safety_paused", status="rejected")
    assert rejected.owner_revision == resumed.owner_revision
    assert rejected.reservation_token == resumed.reservation_token
    assert not rejected.downstream_command_ids and not rejected.downstream_receipt_refs
    assert len(_events(sim, "lowalt.motion.hold")) == holds
    assert sim.kernel.action(charge_move).status == "executing"
    assert sim.kernel.action(recall_id).status == "executing"
    _until(sim, lambda: _field(sim, "lowalt.returning_to_charge") is False)
    assert _events(sim, "lowalt.energy.charging_started")
    assert _events(sim, "lowalt.energy.charging_finished")
    assert sim.kernel.action(charge_move).status == "succeeded"
    assert sim.kernel.action(recall_id).status == "succeeded"
    assert _field(sim, "lowalt.position") == pytest.approx(pad)
    released = _expect(sim, _release(sim, rejected, "idle", request_id="idle-after-charging"))
    assert released.owner_revision == 3
    _expect(sim, _issue(sim, RESUME_SCHEMA, ResumePayload(
        **_operation(sim, released, "retired-after-charging"),
    )), "stale_token", status="rejected")


def test_lease_expiry_restores_idle_owner_before_allocation_and_rejects_old_token(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=6 * SECOND))
    expires = 8 * SECOND
    reserved = _reserve(sim, deadline_ns=5 * SECOND, expires_ns=expires)
    sim.run_until(expires - 1)
    assert _field(sim, "lowalt.task_state", "task-1") == "queued"
    assert not _events(sim, "lowalt.task.assigned")
    sim.run_until(expires + 12 * SECOND)
    assigned = _events(sim, "lowalt.task.assigned")
    assert assigned and all(int(m["at"]["ns"]) >= expires for m in assigned)
    expired = _issue(sim, RESUME_SCHEMA, ResumePayload(**_operation(sim, reserved, "expired")))
    _expect(sim, expired, "expired", status="rejected")


def test_divert_deadline_is_failure_not_arrival_and_cleanup_can_be_released(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=200 * SECOND))
    reserved = _reserve(sim)
    target = (-480.0, -220.0, 100.0)
    command_id = _issue(sim, DIVERT_SCHEMA, DivertPayload(
        **_operation(sim, reserved, "short-deadline", deadline_ns=_now(sim) + 3 * SECOND),
        target=target, frame="enu",
    ))
    expired = _expect(sim, command_id, "expired", status="failed")
    assert _field(sim, "lowalt.position") != list(target)
    assert not any(
        item["kind"] == "receipt" and item["status"] == "succeeded"
        for item in sim.kernel.action(command_id).history
    )
    released = _expect(sim, _release(sim, expired, "idle"))
    _motion_proofs(sim, released)
    assert released.owner_revision == 3


def test_forbidden_diversion_never_claims_arrival_and_retains_releasable_token(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    reserved = _reserve(sim)
    command_id = _issue(sim, DIVERT_SCHEMA, DivertPayload(
        **_operation(sim, reserved, "forbidden-target"), target=(0.0, 0.0, 50.0), frame="enu",
    ))
    blocked = _expect(sim, command_id, "route_unavailable", status="failed")
    assert blocked.owner_revision == 2
    assert _field(sim, "lowalt.position") != [0.0, 0.0, 50.0]
    assert not any(
        m["payload"]["position"] == [0.0, 0.0, 50.0]
        for m in _events(sim, "lowalt.motion.arrived")
    )
    _expect(sim, _release(sim, blocked, "idle"))


def test_enclosed_diversion_returns_typed_no_path_failure_and_keeps_token_releasable(
    simulation: Callable[..., Simulation],
) -> None:
    document = _document(release_ns=100 * SECOND)
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    document["entities"].remove(zone)
    # Overlapping walls enclose a legal endpoint, unlike a target inside a box.
    # CorridorAstar exhausts its search and raises an ordinary ValueError.
    for name, lower, upper in (
        ("west", [-30.0, -30.0, 0.0], [-20.0, 30.0, 100.0]),
        ("east", [20.0, -30.0, 0.0], [30.0, 30.0, 100.0]),
        ("south", [-30.0, -30.0, 0.0], [30.0, -20.0, 100.0]),
        ("north", [-30.0, 20.0, 0.0], [30.0, 30.0, 100.0]),
    ):
        document["entities"].append({
            "id": f"ring-{name}", "type": zone["type"],
            "facts": {**zone["facts"], "lowalt.zone_lower": lower, "lowalt.zone_upper": upper},
        })
    sim = simulation(document)
    reserved = _reserve(sim)
    before = _field(sim, "lowalt.position")
    command_id = _issue(sim, DIVERT_SCHEMA, DivertPayload(
        **_operation(sim, reserved, "enclosed-target"), target=(0.0, 0.0, 50.0), frame="enu",
    ))
    blocked = _expect(sim, command_id, "route_unavailable", status="failed")
    assert blocked.detail == "no route exists around the active no-fly zones"
    assert blocked.owner_revision == 2
    assert blocked.reservation_token == reserved.reservation_token
    assert any(item["kind"] == "feedback" for item in sim.kernel.action(command_id).history)
    assert _field(sim, "lowalt.position") == pytest.approx(before)
    assert not _events(sim, "lowalt.motion.move_to")
    _expect(sim, _release(sim, blocked, "idle"))


def test_egress_target_duplicate_waits_for_final_waypoint_receipt(
    simulation: Callable[..., Simulation],
) -> None:
    document = _document(release_ns=100 * SECOND)
    document["generators"]["fleet"]["starts"] = [[0.0, 0.0, 50.0]]
    zone = next(e for e in document["entities"] if e["id"] == "central-no-fly")
    zone["facts"].update({
        "lowalt.zone_lower": [-10.0, -10.0, 0.0],
        "lowalt.zone_upper": [10.0, 10.0, 100.0],
    })
    sim = simulation(document)
    # Let initial geofence detection/reroute settle before mission admission.
    sim.run_until(2 * SECOND)
    reserved = _reserve(sim)
    target = (-11.0, 0.0, 50.0)
    command_id = _issue(sim, DIVERT_SCHEMA, DivertPayload(
        **_operation(sim, reserved, "egress-target"), target=target, frame="enu",
    ))
    diverted = _expect(sim, command_id)
    # The default egress planner returns (target, target). Both real moves must
    # succeed; the first matching waypoint is not the final arrival evidence.
    moves = [
        message["id"] for message in _events(sim, "lowalt.motion.move_to")
        if message["id"] in diverted.downstream_command_ids
        and tuple(message["payload"]["target"]) == target
    ]
    assert len(moves) == 2
    assert all(sim.kernel.action(cid).status == "succeeded" for cid in moves)
    final_receipt = next(
        item for item in sim.kernel.action(moves[-1]).history
        if item["kind"] == "receipt" and item["status"] == "succeeded"
    )
    ref = final_receipt["ref"]
    assert (ref["record_index"], ref["item_index"]) in diverted.downstream_receipt_refs
    assert _field(sim, "lowalt.position") == pytest.approx(target)
    _motion_proofs(sim, diverted, moved=True)
    _expect(sim, _release(sim, diverted, "idle"))


@pytest.mark.parametrize("control", ["weather", "charging"])
def test_committed_safety_or_charging_control_blocks_new_external_reservation(
    simulation: Callable[..., Simulation], control: str,
) -> None:
    document = _document(release_ns=100 * SECOND)
    if control == "weather":
        document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"] = {
            "mode": "constant", "value": [14.0, 0.0, 0.0],
        }
        field, code = "lowalt.weather_held", "safety_paused"
    else:
        document["generators"]["fleet"]["facts"].update(
            {"lowalt.soc": 0.1, "lowalt.motion_energy_j": 24000.0},
        )
        field, code = "lowalt.charging", "protected"
    sim = simulation(document)
    _until(sim, lambda: bool(_field(sim, field)), seconds=10)
    command_id = _issue(sim, RESERVE_SCHEMA, _reserve_payload(sim))
    blocked = _expect(sim, command_id, code, status="rejected")
    assert blocked.owner_revision == 0
    assert blocked.reservation_token is None
    assert not blocked.downstream_command_ids
    assert _field(sim, field) is True
    assert not _events(sim, "lowalt.task.assigned")


def test_active_weather_interruption_fails_divert_but_preserves_releasable_reservation(
    simulation: Callable[..., Simulation],
) -> None:
    document = _document(release_ns=12 * SECOND)
    document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"] = {
        "mode": "gust", "value": [0.0, 0.0, 0.0],
        "gusts": [{"start_ns": 6 * SECOND, "end_ns": 9 * SECOND,
                   "value": [14.0, 0.0, 0.0]}],
    }
    sim = simulation(document)
    reserved = _reserve(sim)
    divert_id = _divert(sim, reserved)
    interrupted = _expect(sim, divert_id, "safety_paused", status="failed")
    assert interrupted.owner_revision == 2
    assert interrupted.reservation_token == reserved.reservation_token
    assert _field(sim, "lowalt.weather_held") is True
    assert _field(sim, "lowalt.position") != list(DIVERSION)
    assert not any(
        item["kind"] == "receipt" and item["status"] == "succeeded"
        for item in sim.kernel.action(divert_id).history
    )
    resume_id = _issue(sim, RESUME_SCHEMA, ResumePayload(
        **_operation(sim, interrupted, "resume-during-weather"),
    ))
    blocked = _expect(sim, resume_id, "safety_paused", status="rejected")
    assert blocked.owner_revision == 2
    assert not _events(sim, "lowalt.task.assigned")
    _until(sim, lambda: _field(sim, "lowalt.weather_held") is False, seconds=10)
    released = _expect(sim, _release(sim, interrupted))
    assert released.owner_revision == 3
    _motion_proofs(sim, released)
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed")
    assert len(_events(sim, "lowalt.task.assigned")) == 1
    assert not _events(sim, "lowalt.task.failed")
    assert not any(
        tuple(m["payload"]["position"]) == DIVERSION
        for m in _events(sim, "lowalt.motion.arrived")
    )


def test_wire_valid_crossfield_error_is_typed_rejection_not_kernel_fault(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    wire = build_payload(_reserve_payload(sim, "bad-expiry"))
    # Both values satisfy the portable integer schema. Their ordering is an API
    # validation rule, exercised through the real caller rather than bypassed.
    wire["expires_ns"] = wire["deadline_ns"] - SECOND
    command_id = _issue(sim, RESERVE_SCHEMA, wire)
    rejected = _expect(sim, command_id, "invalid_payload", status="rejected")
    assert rejected.vehicle == _ref(sim)
    assert rejected.mission_id == wire["mission_id"]
    assert rejected.request_id == wire["request_id"]
    assert "expires_ns" in rejected.detail
    assert rejected.owner_revision == 0
    assert rejected.reservation_token is None
    assert not rejected.downstream_command_ids
    assert not _events(sim, "lowalt.motion.hold")
    reserved = _reserve(sim, "valid-after-invalid")
    assert reserved.owner_revision == 1
    _motion_proofs(sim, reserved)


def test_pure_api_roundtrips_are_frozen_detached_and_descriptors_are_fresh() -> None:
    vehicle = EntityRef("api-run", "api-epoch", "vehicle", 7, "custom:Vehicle")
    reserve = ReservePayload(vehicle, "mission", "reserve", 10, 20)
    common = dict(vehicle=vehicle, mission_id="mission", request_id="operation",
                  deadline_ns=10, reservation_token="token", expected_owner_revision=1)
    payloads = (
        reserve, DivertPayload(**common, target=(1.0, 2.0, 3.0), frame="custom"),
        ResumePayload(**common), ReleasePayload(**common, disposition="resume_saved"),
    )
    for schema, payload in zip(MISSION_COMMAND_SCHEMAS, payloads, strict=True):
        wire = build_payload(payload)
        assert validate_payload(schema, freeze(wire)) == payload
        wire["vehicle"]["$ref"]["id"] = "mutated-wire"
        assert payload.vehicle == vehicle
    with pytest.raises(FrozenInstanceError):
        setattr(reserve, "mission_id", "mutated-dataclass")
    result = MissionResult(vehicle, "mission", "request", "command", ResultCode.OK, 1,
                           "token", ("move",), ((3, 4),))
    assert validate_result(freeze(build_result(result))) == result
    pristine = mission_descriptors()
    mutated = mission_descriptors()
    assert pristine == mutated and len(pristine) == 5
    assert {d["id"] for d in pristine if d["kind"] == "command"} == set(MISSION_COMMAND_SCHEMAS)
    assert all(d.get("cancel_support") is False for d in pristine if d["kind"] == "command")
    mutated[0]["schema"]["members"]["vehicle"]["target_type"] = "custom:Restricted"
    mutated[0]["result_schema"]["members"]["owner_revision"]["minimum"] = 999
    assert mission_descriptors() == pristine
    assert mutated[1:] == pristine[1:]


def test_pure_api_rejects_invalid_direct_dataclasses_and_result_records() -> None:
    vehicle = EntityRef("api-run", "api-epoch", "vehicle", 0, "custom:Vehicle")
    reserve = ReservePayload(vehicle, "mission", "request", 10, 20)
    common = dict(vehicle=vehicle, mission_id="mission", request_id="operation",
                  deadline_ns=10, reservation_token="token", expected_owner_revision=1)
    malformed = [
        replace(reserve, deadline_ns=True), replace(reserve, deadline_ns=0),
        replace(reserve, expires_ns=9), replace(reserve, expected_owner_revision=True),
        replace(reserve, expected_owner_revision=-1),
        DivertPayload(**common, target=(1.0, 2.0), frame="enu"),
        DivertPayload(**common, target=(True, 2.0, 3.0), frame="enu"),
        DivertPayload(**common, target=(float("inf"), 2.0, 3.0), frame="enu"),
        ReleasePayload(**common, disposition="drop-payload"),
    ]
    for payload in malformed:
        with pytest.raises(ValueError):
            build_payload(payload)
    wire = build_payload(reserve)
    with pytest.raises(ValueError):
        validate_payload(RESERVE_SCHEMA, {**wire, "unexpected": 1})
    wire["vehicle"] = {"$ref": {"id": "vehicle"}}
    with pytest.raises(ValueError):
        validate_payload(RESERVE_SCHEMA, wire)
    result = build_result(MissionResult(vehicle, "mission", "request", "command", ResultCode.OK, 1))
    for changes in ({"code": "unknown-code"}, {"owner_revision": True},
                    {"downstream_receipt_refs": [{"record_index": 3, "item_index": -1}]}):
        with pytest.raises(ValueError):
            validate_result({**result, **changes})


@pytest.mark.parametrize("changes", [
    {"generation": 1}, {"run_id": "stale-run"}, {"epoch": "stale-epoch"},
    {"type_id": "lowalt:Site"},
])
def test_full_entity_identity_precedes_claimed_revision_validation(
    simulation: Callable[..., Simulation], changes: dict[str, Any],
) -> None:
    sim = simulation(_document(release_ns=100 * SECOND))
    stale = replace(_ref(sim), **changes)
    command_id = _issue(sim, RESERVE_SCHEMA, _reserve_payload(
        sim, "stale-entity", vehicle=stale, expected_owner_revision=999,
    ))
    rejected = _expect(sim, command_id, "stale_entity", status="rejected")
    assert rejected.vehicle == stale and rejected.owner_revision == 0
    assert not rejected.downstream_command_ids
    assert _reserve(sim, "current-entity", expected_owner_revision=0).owner_revision == 1


@pytest.mark.parametrize("replacement", ["release", "resume"])
def test_active_divert_can_be_superseded_only_after_actual_hold_reconciliation(
    simulation: Callable[..., Simulation], replacement: str,
) -> None:
    sim = simulation()
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "started")
    reserved = _reserve(sim)
    divert_id = _divert(sim, reserved)
    _until(sim, lambda: any(abs(v) > 1e-6 for v in _field(sim, "lowalt.velocity")))
    assert sim.kernel.action(divert_id).status == "executing"
    active = validate_result(thaw(next(
        item["payload"] for item in reversed(sim.kernel.action(divert_id).history)
        if item["kind"] == "feedback"
    )))
    assert active.owner_revision == 2
    second = _issue(sim, DIVERT_SCHEMA, DivertPayload(
        **_operation(sim, active, "second-divert"), target=DIVERSION, frame="ned",
    ))
    _expect(sim, second, "busy", status="rejected")  # BUSY precedes frame policy.
    if replacement == "release":
        command_id = _release(sim, active)
    else:
        command_id = _issue(sim, RESUME_SCHEMA, ResumePayload(**_operation(sim, active, "resume")))
    assert sim.kernel.action(command_id).status != "succeeded"
    canceled = _expect(sim, divert_id, "canceled", status="failed")
    assert canceled.owner_revision == 2
    restored = _expect(sim, command_id)
    assert restored.owner_revision == 3
    _motion_proofs(sim, restored)
    assert _field(sim, "lowalt.task_state", "task-1") == "started"
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed")
    assert len(_events(sim, "lowalt.task.completed")) == 1
    assert not _events(sim, "lowalt.task.failed")


def test_fast_release_after_resume_does_not_claim_stop_or_rewind_running_task_motion(
    simulation: Callable[..., Simulation],
) -> None:
    sim = simulation()
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "picked_up")
    reserved = _reserve(sim)
    resumed = _expect(sim, _issue(sim, RESUME_SCHEMA, ResumePayload(
        **_operation(sim, reserved, "resume"),
    )))
    _until(sim, lambda: any(abs(v) > 1e-6 for v in _field(sim, "lowalt.velocity")))
    task_move = _events(sim, "lowalt.motion.move_to")[-1]["id"]
    assert sim.kernel.action(task_move).status == "executing"
    before = _field(sim, "lowalt.position")
    holds = len(_events(sim, "lowalt.motion.hold"))
    moves = len(_events(sim, "lowalt.motion.move_to"))
    released = _expect(sim, _release(sim, resumed))
    assert released.owner_revision == 3
    assert not released.downstream_command_ids and not released.downstream_receipt_refs
    assert sim.kernel.action(task_move).status == "executing"
    assert _field(sim, "lowalt.position") == pytest.approx(before)
    assert _field(sim, "lowalt.task_state", "task-1") == "picked_up"
    assert len(_events(sim, "lowalt.motion.hold")) == holds
    assert len(_events(sim, "lowalt.motion.move_to")) == moves
    assert len(_events(sim, "lowalt.task.resumed")) == 1
    _until(sim, lambda: _field(sim, "lowalt.task_state", "task-1") == "completed")
    assert len(_events(sim, "lowalt.task.picked_up")) == 1
    assert len(_events(sim, "lowalt.task.completed")) == 1
    assert not _events(sim, "lowalt.task.failed")
