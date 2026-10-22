"""Private Tasks continuations behind the public mission command/receipt boundary."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, cast

from aerokernel import EntityRef, Instant
from aerokernel.messages import Delivery
from aerokernel.operations import ScheduleTimer
from aerokernel.sdk import Command, EngineContext

from aeroagentsim.mission_api import (
    MISSION_COMMAND_SCHEMAS,
    MISSION_STATUS_TOPIC,
    STATUS_SCHEMA,
    DivertPayload,
    MissionPayload,
    MissionResult,
    ReleasePayload,
    ReservePayload,
    ResultCode,
    ResumePayload,
    build_payload,
    build_result,
    validate_payload,
)
from aeroagentsim.packs.common import distance, read, vec

if TYPE_CHECKING:
    from .tasks import Flight, LowaltTasks

TERMINAL = {"succeeded", "failed", "rejected", "canceled"}


@dataclass
class Continuation:
    flight: Flight
    phase: str | None
    pause_pending: bool = False


@dataclass
class Operation:
    command: Command[MissionPayload]
    stopping: bool = True
    downstream: list[str] = field(default_factory=list)
    receipts: list[tuple[int, int]] = field(default_factory=list)
    arrival_command: str | None = None


@dataclass
class Reservation:
    token: str
    origin: tuple[str, str]
    anchor: Command[MissionPayload]
    expires_ns: int
    saved: Continuation
    active: Operation | None = None
    restored: bool = False
    expired: bool = False
    cleanup: bool = False
    stop_id: str | None = None
    stop_ok: bool = False
    stop_failed: bool = False
    motion: dict[str, str] = field(default_factory=dict)
    targets: dict[str, tuple[float, float, float] | None] = field(default_factory=dict)
    receipts: list[tuple[int, int]] = field(default_factory=list)


class MissionControl:
    """Single-owner state machine; never exported to an orchestration engine.

    All physical operations use Tasks._submit/_brake, preserving the existing
    motion-only submitted-receipt FIFO. A token remains excluded during cleanup,
    even after a deadline or a failed stop. No mission method writes payload.
    """

    def __init__(self, owner: LowaltTasks) -> None:
        self.owner = owner
        self.reservations: dict[str, Reservation] = {}
        self.revisions: dict[str, int] = {}
        self.retired: dict[str, ResultCode] = {}
        self.requests: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        self.changed: dict[str, EngineContext] = {}

    def reserved(self, vehicle: str) -> bool:
        return vehicle in self.reservations

    def excluded(self, ctx: EngineContext, vehicle: str) -> bool:
        return self.reserved(vehicle) or self.changed.get(vehicle) is ctx

    def blocks(self, ctx: EngineContext, flight: Flight) -> bool:
        """Let safety trips and explicitly resumed jobs use the original driver."""
        if self.changed.get(flight.vehicle.id) is ctx:
            return True
        entry = self.reservations.get(flight.vehicle.id)
        return entry is not None and not entry.restored and flight.purpose == "mission"

    def _revision(self, vehicle: EntityRef) -> int:
        return self.revisions.get(vehicle.id, 0)

    def _bump(self, vehicle: EntityRef) -> None:
        self.revisions[vehicle.id] = self._revision(vehicle) + 1

    def _result(
        self, command: Command[MissionPayload], code: ResultCode,
        entry: Reservation | None = None, operation: Operation | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        payload = command.payload
        return build_result(MissionResult(
            vehicle=payload.vehicle, mission_id=payload.mission_id,
            request_id=payload.request_id, command_id=command.id, code=code,
            owner_revision=self._revision(payload.vehicle),
            reservation_token=entry.token if entry is not None else None,
            downstream_command_ids=tuple(operation.downstream) if operation else (),
            downstream_receipt_refs=tuple(operation.receipts) if operation else (),
            detail=detail,
        ))

    def _event(
        self, ctx: EngineContext, entry: Reservation, code: ResultCode, detail: str,
    ) -> None:
        evidence = Operation(entry.anchor, downstream=list(entry.motion),
                             receipts=list(entry.receipts))
        ctx.emit(STATUS_SCHEMA, self._result(entry.anchor, code, entry, evidence, detail),
                 topic=MISSION_STATUS_TOPIC)

    def _finish(
        self, ctx: EngineContext, entry: Reservation, code: ResultCode,
        detail: str = "",
    ) -> None:
        operation = entry.active
        assert operation is not None
        result = self._result(operation.command, code, entry, operation, detail)
        if code == ResultCode.OK:
            ctx.succeed(operation.command, result)
        else:
            ctx.fail(operation.command, result)
        entry.active = None

    def _safety(self, ctx: EngineContext, flight: Flight) -> bool:
        return (
            flight.purpose in {"charge", "avoidance"}
            or any(read(ctx, flight.vehicle, name) for name in (
                "lowalt.weather_held", "lowalt.charging", "lowalt.charging_waiting",
                "lowalt.returning_to_charge",
            ))
            or ctx.now.ns < flight.hold_until_ns
        )

    def _protected(self, ctx: EngineContext, flight: Flight) -> bool:
        # M1 does not change the embedded Rescue helper. Its authored roster is
        # protected from external missions, including bids/plans not yet applied.
        rescue_vehicles = self.owner.cfg.get("rescue", {}).get("vehicles", {})
        protected_kinds = {"passenger"}
        replacement_required = False
        if self.owner.cooperation is not None:
            rules = self.owner.cooperation["responder"]["rules"]
            protected_kinds.update(rules["protected_task_kinds"])
            replacement_required = rules["require_replacement"]
        return (
            flight.vehicle.id in rescue_vehicles
            or self.owner._coop_reserved(flight.vehicle.id)
            or flight.suspended is not None
            or flight.purpose != "task"
            or (flight.job is not None and (
                replacement_required
                or read(ctx, flight.job.ref, "lowalt.task_kind") in protected_kinds
                or flight.job.phase not in {"assigned", "started", "picked_up"}
            ))
        )

    def _next_cut(self, ctx: EngineContext, vehicle: str) -> None:
        ctx.ops.append(ScheduleTimer(
            f"lowalt/mission/{vehicle}/{ctx.now.ns}.{ctx.now.microstep}/{len(ctx.ops)}",
            Instant(ctx.now.ns, ctx.now.microstep + 1),
            {"aas_mission_commit": vehicle}, tuple(ctx.inputs),
        ))

    def _save(self, ctx: EngineContext, flight: Flight) -> Continuation:
        saved = Continuation(replace(flight), flight.job.phase if flight.job else None)
        if flight.job is not None:
            # An allocation result may have assigned this same job earlier in
            # the inbox. Publish the pause at a separate cut: one fact key may
            # not be written twice in one kernel wave.
            flight.job.phase = "paused"
            saved.pause_pending = True
            self._next_cut(ctx, flight.vehicle.id)
        flight.job = None
        flight.purpose = "mission"
        flight.route, flight.goal, flight.paused_route = (), None, None
        flight.work_active = False
        flight.work_reconnect_goal, flight.egress_waypoint = None, None
        flight.replan = True
        self.changed[flight.vehicle.id] = ctx
        return saved

    def _restore(self, ctx: EngineContext, entry: Reservation, flight: Flight) -> None:
        if entry.restored:
            return
        saved = entry.saved.flight
        # Never resurrect an interrupted command, its receipt, an obsolete hold
        # snapshot, or a route from the vehicle's pre-diversion position (PR #20).
        motion = {
            "vehicle", "pending", "command_id", "status", "deferred_brake",
            "waypoint", "waypoint_start", "waypoint_is_work", "waypoint_is_connector",
            "route", "paused_route", "egress_waypoint", "work_reconnect_goal",
            "cruise_altitude_m",
        }
        for descriptor in fields(saved):
            if descriptor.name not in motion:
                setattr(flight, descriptor.name, getattr(saved, descriptor.name))
        if saved.cruise_altitude_m is not None:
            flight.cruise_altitude_m = max(
                saved.cruise_altitude_m, flight.cruise_altitude_m or saved.cruise_altitude_m,
            )
        flight.route, flight.paused_route = (), None
        flight.egress_waypoint, flight.work_reconnect_goal = None, None
        flight.transit_resume, flight.replan, flight.status = False, True, None
        if flight.job is not None:
            assert entry.saved.phase is not None
            self.owner._state(ctx, flight.job, entry.saved.phase)
            self.owner._event(ctx, "resumed", task=flight.job.ref.id,
                              vehicle=flight.vehicle.id)
        entry.restored = True
        self.changed[flight.vehicle.id] = ctx

    def _stop(self, ctx: EngineContext, entry: Reservation, flight: Flight) -> None:
        entry.stop_ok, entry.stop_failed = False, False
        if flight.purpose != "mission":
            # Release/expiry must not replace an energy owner's charging trip.
            # Reconcile it first, then obtain a fresh hold after safety restores
            # mission purpose. The token stays excluded in the meantime.
            return
        flight.route, flight.goal, flight.replan = (), None, True
        # A hold already awaiting submission/termination is sufficient. Do not
        # create a second deferred brake that could outlive release success.
        if flight.waypoint is None and (flight.pending or flight.command_id is not None):
            entry.stop_id = flight.command_id
            return
        self.owner._brake(ctx, flight)

    def _start(
        self, ctx: EngineContext, entry: Reservation,
        command: Command[MissionPayload], flight: Flight, *, include_motion: bool = True,
    ) -> None:
        ctx.accept(command)
        ctx.execute(command)
        self._bump(flight.vehicle)
        operation = entry.active = Operation(command)
        # Include the real interrupted command as well as the replacement hold.
        # Fast release after resume must not claim the new task's motion as its
        # own downstream effect: it deliberately neither stops nor waits on it.
        if include_motion:
            for cid, status in entry.motion.items():
                if status not in TERMINAL:
                    operation.downstream.append(cid)
            if flight.command_id is not None and flight.command_id not in entry.motion:
                cid = flight.command_id
                entry.motion[cid] = ctx.view.action(cid).status
                entry.targets[cid] = flight.waypoint
                operation.downstream.append(cid)
        ctx.wake_at(command.payload.deadline_ns, {"aas_mission": command.id})
        ctx.feedback(command, self._result(command, ResultCode.OK, entry, operation,
                                           "accepted; awaiting committed effects"))

    def handle(self, ctx: EngineContext, delivery: Delivery) -> bool:
        schema = delivery.message.schema_id
        if delivery.message.kind != "command" or schema not in MISSION_COMMAND_SCHEMAS:
            return False
        wire = ctx.remember(delivery, lambda value: cast(dict[str, Any], value))
        try:
            payload = validate_payload(schema, wire.payload)
        except ValueError as problem:
            # The registry validates the record shape before dispatch, but its
            # finite schema cannot express expiry/deadline cross-field ordering.
            vehicle = EntityRef.from_data(wire.payload["vehicle"]["$ref"])
            ctx.reject(wire, build_result(MissionResult(
                vehicle=vehicle, mission_id=wire.payload["mission_id"],
                request_id=wire.payload["request_id"], command_id=wire.id,
                code=ResultCode.INVALID_PAYLOAD, owner_revision=self._revision(vehicle),
                detail=str(problem),
            )))
            return True
        command = Command(delivery, payload)
        ctx.commands[command.id] = command
        origin = (delivery.message.source_kind, delivery.message.source)
        key = (*origin, payload.request_id)
        fingerprint = (schema, build_payload(payload))
        if key in self.requests:
            code = (ResultCode.DUPLICATE_REQUEST if self.requests[key] == fingerprint
                    else ResultCode.REQUEST_CONFLICT)
            ctx.reject(command, self._result(command, code))
            return True
        self.requests[key] = fingerprint
        flight = self.owner.flights.get(payload.vehicle.id)
        code = None
        if flight is None:
            code = ResultCode.UNKNOWN_VEHICLE
        elif flight.vehicle != payload.vehicle:
            code = ResultCode.STALE_ENTITY
        elif ctx.now.ns >= payload.deadline_ns:
            code = ResultCode.EXPIRED
        if code is not None:
            ctx.reject(command, self._result(command, code))
            return True
        assert flight is not None
        entry = self.reservations.get(flight.vehicle.id)
        if isinstance(payload, ReservePayload):
            if entry is not None:
                code = ResultCode.ALREADY_RESERVED
            elif self.changed.get(flight.vehicle.id) is ctx:
                code = ResultCode.BUSY
            elif (payload.expected_owner_revision is not None
                  and payload.expected_owner_revision != self._revision(flight.vehicle)):
                code = ResultCode.STALE_REVISION
            elif ctx.now.ns >= payload.expires_ns:
                code = ResultCode.EXPIRED
            elif self._protected(ctx, flight):
                code = ResultCode.PROTECTED
            elif self._safety(ctx, flight):
                code = ResultCode.SAFETY_PAUSED
            elif read(ctx, flight.vehicle, "lowalt.soc") <= self.owner.cfg["dispatch_min_soc"]:
                code = ResultCode.INSUFFICIENT_ENERGY
            elif read(ctx, flight.vehicle, "lowalt.payload_kg") > read(
                ctx, flight.vehicle, "lowalt.payload_capacity_kg",
            ):
                code = ResultCode.CAPACITY_EXCEEDED
            if code is not None:
                ctx.reject(command, self._result(command, code, entry))
                return True
            entry = Reservation(
                token=f"{self.owner.build.id}/mission/{command.id}", origin=origin,
                anchor=command, expires_ns=payload.expires_ns,
                saved=self._save(ctx, flight),
            )
            self.reservations[flight.vehicle.id] = entry
            self._start(ctx, entry, command, flight)
            self._stop(ctx, entry, flight)
            ctx.wake_at(payload.expires_ns, {"aas_mission_expiry": entry.token})
            return True
        if payload.reservation_token in self.retired:
            code = self.retired[payload.reservation_token]
        elif entry is None:
            code = ResultCode.NOT_RESERVED
        elif payload.reservation_token != entry.token or origin != entry.origin or payload.mission_id != entry.anchor.payload.mission_id:
            code = ResultCode.UNKNOWN_TOKEN
        elif entry.expired or ctx.now.ns >= entry.expires_ns:
            code = ResultCode.EXPIRED
        elif payload.expected_owner_revision != self._revision(flight.vehicle):
            code = ResultCode.STALE_REVISION
        elif self.changed.get(flight.vehicle.id) is ctx:
            code = ResultCode.BUSY
        elif entry.active is not None and not isinstance(entry.active.command.payload, DivertPayload):
            code = ResultCode.BUSY
        elif isinstance(payload, DivertPayload) and entry.active is not None:
            code = ResultCode.BUSY
        elif isinstance(payload, DivertPayload) and payload.frame != "enu":
            code = ResultCode.UNSUPPORTED_FRAME
        elif isinstance(payload, (DivertPayload, ResumePayload)) and self._safety(ctx, flight):
            code = ResultCode.SAFETY_PAUSED
        elif isinstance(payload, DivertPayload) and read(ctx, flight.vehicle, "lowalt.soc") <= self.owner.cfg["dispatch_min_soc"]:
            code = ResultCode.INSUFFICIENT_ENERGY
        elif isinstance(payload, DivertPayload) and read(ctx, flight.vehicle, "lowalt.payload_kg") > read(ctx, flight.vehicle, "lowalt.payload_capacity_kg"):
            code = ResultCode.CAPACITY_EXCEEDED
        elif isinstance(payload, ResumePayload) and entry.restored:
            code = ResultCode.BUSY
        elif isinstance(payload, ReleasePayload) and payload.disposition == "idle" and read(ctx, flight.vehicle, "lowalt.payload_kg") > 0:
            code = ResultCode.PROTECTED
        elif (isinstance(payload, ReleasePayload) and payload.disposition == "idle"
              and entry.restored and self._safety(ctx, flight)):
            # Re-saving a resumed safety trip would replace its purpose/goal
            # before _stop can defer to the ordinary charging/avoidance driver.
            code = ResultCode.SAFETY_PAUSED
        if code is not None:
            ctx.reject(command, self._result(command, code, entry))
            return True
        assert entry is not None
        if entry.active is not None:
            self._finish(ctx, entry, ResultCode.CANCELED,
                         "superseded by an owner-mediated resume/release")
        if entry.restored and isinstance(payload, ReleasePayload) and payload.disposition == "resume_saved":
            # Mission motion was already reconciled by resume. The running job
            # is not an outstanding mission motion and must not be rewound.
            self._start(ctx, entry, command, flight, include_motion=False)
            self._finish(ctx, entry, ResultCode.OK, "token retired; continuation already restored")
            self._retire(entry, flight)
            self.changed[flight.vehicle.id] = ctx
            return True
        if entry.restored:
            entry.saved = self._save(ctx, flight)
            entry.restored = False
        self._start(ctx, entry, command, flight)
        self._stop(ctx, entry, flight)
        return True

    def motion_receipt(
        self, ctx: EngineContext, flight: Flight, data: dict[str, Any],
    ) -> None:
        entry = self.reservations.get(flight.vehicle.id)
        if entry is None:
            return
        cid, status = data["command_id"], data["status"]
        if status == "submitted":
            entry.targets[cid] = flight.waypoint
            if flight.waypoint is None:
                entry.stop_id, entry.stop_ok, entry.stop_failed = cid, False, False
        entry.motion[cid] = status
        operation = entry.active
        if operation is not None:
            if cid not in operation.downstream:
                operation.downstream.append(cid)
        if status not in TERMINAL:
            return
        # Persist the receipt's own coordinates, as exposed by action.history,
        # rather than treating a notification's causal reference as its identity.
        receipt = data["ref"]
        ref = (receipt["record_index"], receipt["item_index"])
        if operation is not None:
            operation.receipts.append(ref)
        entry.receipts.append(ref)
        if cid == entry.stop_id:
            entry.stop_ok = status == "succeeded"
            entry.stop_failed = status != "succeeded"
            if entry.stop_failed and operation is None:
                self._event(ctx, entry, ResultCode.MOTION_FAILED,
                            "downstream cleanup hold failed; exclusion retained")
        if operation is None or not isinstance(operation.command.payload, DivertPayload):
            return
        target = entry.targets.get(cid)
        if operation.stopping or target is None:
            return
        if status != "succeeded":
            self._finish(ctx, entry, ResultCode.MOTION_FAILED, str(data.get("result", status)))
            self._stop(ctx, entry, flight)
        elif target == operation.command.payload.target and not flight.route:
            # Egress/work planners may visit the target before their final leg.
            operation.arrival_command = cid

    def control(self, ctx: EngineContext, payload: dict[str, Any]) -> None:
        entry = self.reservations.get(payload["vehicle"])
        if entry is None or entry.restored or payload["action"] in {"release", "resume"}:
            return
        if entry.active is not None:
            self._finish(ctx, entry, ResultCode.SAFETY_PAUSED,
                         "owner safety control: " + payload["action"])
        self._event(ctx, entry, ResultCode.SAFETY_PAUSED,
                    "reservation retained during " + payload["action"])
        flight = self.owner.flights[payload["vehicle"]]
        if flight.purpose == "mission":
            flight.route, flight.goal, flight.replan = (), None, True

    def _stopped(self, ctx: EngineContext, entry: Reservation, flight: Flight) -> bool:
        return (
            entry.stop_ok and not flight.pending and flight.command_id is None
            and not flight.deferred_brake
            and all(status in TERMINAL for status in entry.motion.values())
            and distance(vec(read(ctx, flight.vehicle, "lowalt.velocity")), (0., 0., 0.)) <= 1e-6
        )

    def _retire(self, entry: Reservation, flight: Flight) -> None:
        self.retired[entry.token] = ResultCode.EXPIRED if entry.expired else ResultCode.STALE_TOKEN
        del self.reservations[flight.vehicle.id]

    def _expire(self, ctx: EngineContext, entry: Reservation, flight: Flight) -> None:
        entry.expired, entry.cleanup = True, True
        self._bump(flight.vehicle)
        if entry.active is not None:
            self._finish(ctx, entry, ResultCode.EXPIRED, "reservation expired; cleanup retains exclusion")
        self._event(ctx, entry, ResultCode.EXPIRED, "lease expired; reconciling motion")
        if entry.restored:
            self._retire(entry, flight)
        elif flight.purpose == "mission":
            self._stop(ctx, entry, flight)

    def poll(self, ctx: EngineContext) -> None:
        for identity, entry in tuple(self.reservations.items()):
            flight = self.owner.flights[identity]
            if entry.saved.pause_pending:
                if self.changed.get(identity) is not ctx:
                    assert entry.saved.flight.job is not None
                    ctx.set(entry.saved.flight.job.ref, "lowalt.task_state", "paused")
                    entry.saved.pause_pending = False
                    self.changed[identity] = ctx
                    self._next_cut(ctx, identity)
                # Restoration/idle must commit after, never over, this pause.
                continue
            if not entry.expired and ctx.now.ns >= entry.expires_ns:
                self._expire(ctx, entry, flight)
                if identity not in self.reservations:
                    continue
            operation = entry.active
            if operation is not None and ctx.now.ns >= operation.command.payload.deadline_ns:
                reserving = isinstance(operation.command.payload, ReservePayload)
                self._finish(ctx, entry, ResultCode.EXPIRED, "command deadline reached")
                if reserving:
                    self._expire(ctx, entry, flight)
                elif flight.purpose == "mission":
                    self._stop(ctx, entry, flight)
                operation = None
            if entry.cleanup:
                if flight.purpose == "mission" and not self._safety(ctx, flight):
                    if self._stopped(ctx, entry, flight):
                        self._restore(ctx, entry, flight)
                        self._event(ctx, entry, ResultCode.EXPIRED,
                                    "expired token retired; saved continuation restored")
                        self._retire(entry, flight)
                    elif not flight.pending and flight.command_id is None and not entry.stop_failed:
                        self._stop(ctx, entry, flight)
                continue
            if operation is None:
                continue
            if (isinstance(operation.command.payload, (ReservePayload, DivertPayload))
                and self._safety(ctx, flight)):
                # A safety fact can commit before its control event is delivered.
                # Do not let an intervening hold receipt turn that pause into a
                # successful reservation or a new diversion.
                self._finish(ctx, entry, ResultCode.SAFETY_PAUSED,
                             "committed safety/charging pause; token retained")
                self._event(ctx, entry, ResultCode.SAFETY_PAUSED,
                            "reservation retained during committed safety pause")
                if flight.purpose == "mission":
                    self._stop(ctx, entry, flight)
                continue
            if operation.stopping:
                if entry.stop_failed:
                    self._finish(ctx, entry, ResultCode.MOTION_FAILED, "downstream hold did not succeed; token retained")
                    continue
                if not self._stopped(ctx, entry, flight):
                    if (flight.purpose == "mission" and not self._safety(ctx, flight)
                        and not flight.pending and flight.command_id is None
                        and not flight.deferred_brake):
                        self._stop(ctx, entry, flight)
                    continue
                payload = operation.command.payload
                if isinstance(payload, ReservePayload):
                    flight.replan = False
                    self._finish(ctx, entry, ResultCode.OK, "hold and reservation committed")
                    continue
                if flight.purpose != "mission" or self._safety(ctx, flight):
                    continue
                if isinstance(payload, (ResumePayload, ReleasePayload)):
                    if isinstance(payload, ResumePayload) or payload.disposition == "resume_saved":
                        self._restore(ctx, entry, flight)
                    else:
                        job = entry.saved.flight.job
                        if job is not None:
                            self.owner._state(ctx, job, "failed")
                            self.owner._event(ctx, "failed", task=job.ref.id, vehicle=identity,
                                              reason="mission release idle")
                            job.vehicle = None
                            ctx.set(job.ref, "lowalt.task_vehicle", "")
                        flight.purpose, flight.job = "task", None
                        flight.route, flight.goal, flight.paused_route = (), None, None
                        flight.replan, flight.work_active = False, False
                        self.changed[identity] = ctx
                    self._finish(ctx, entry, ResultCode.OK, "requested continuation/disposition committed")
                    if isinstance(payload, ReleasePayload):
                        self._retire(entry, flight)
                    continue
                operation.stopping = False
                flight.replan = True
            self._divert(ctx, entry, flight)

    def _divert(self, ctx: EngineContext, entry: Reservation, flight: Flight) -> None:
        operation = entry.active
        assert operation is not None and isinstance(operation.command.payload, DivertPayload)
        payload = operation.command.payload
        if flight.purpose != "mission" or self._safety(ctx, flight):
            self._finish(ctx, entry, ResultCode.SAFETY_PAUSED, "committed safety/charging pause; token retained")
            if flight.purpose == "mission":
                self._stop(ctx, entry, flight)
            return
        if flight.pending or flight.command_id is not None or flight.deferred_brake:
            return
        if operation.arrival_command is not None:
            if (not flight.route
                and distance(vec(read(ctx, flight.vehicle, "lowalt.position")), payload.target) <= self.owner.radius
                and distance(vec(read(ctx, flight.vehicle, "lowalt.velocity")), (0., 0., 0.)) <= 1e-6):
                self._finish(ctx, entry, ResultCode.OK, "downstream success and authoritative arrival committed")
            else:
                self._finish(ctx, entry, ResultCode.ARRIVAL_UNCONFIRMED, "downstream success without authoritative arrival")
                self._stop(ctx, entry, flight)
            return
        if flight.replan or not flight.route:
            if not self.owner._route(ctx, flight, payload.target):
                self._finish(ctx, entry, ResultCode.ROUTE_UNAVAILABLE,
                             flight.route_blocked_reason or "route unavailable")
                return
        # Revalidate the next segment against the latest committed zone state,
        # including activation while a reserved idle vehicle had no attached job.
        from aeroagentsim.algorithms.routes import route_avoids_no_fly

        state = self.owner._snapshot(ctx)
        exemptions = self.owner.cfg.get("zone_exemptions", {})
        state = replace(state, zones=tuple(
            z for z in state.zones if flight.vehicle.id not in exemptions.get(z.id, ())
        ))
        target = flight.route[0] if flight.route else payload.target
        if not route_avoids_no_fly(
            state, vec(read(ctx, flight.vehicle, "lowalt.position")), (target,),
            initial_exit=target == flight.egress_waypoint,
        ):
            if not self.owner._route(ctx, flight, payload.target):
                self._finish(ctx, entry, ResultCode.ROUTE_UNAVAILABLE,
                             flight.route_blocked_reason or "route unavailable")
                return
            target = flight.route[0] if flight.route else payload.target
        if flight.route:
            flight.route = flight.route[1:]
        self.owner._submit(ctx, flight, target)
