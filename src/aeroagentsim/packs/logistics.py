"""Facility-constrained parcel workflows using real selected motion observations."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, cast

from aerokernel import (
    CommandRequest,
    Dependency,
    EntityRef,
    Interval,
    Partition,
    Stamp,
)
from aerokernel.messages import RequestCancel
from aerokernel.relations import RelationDependency
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild

from .arrivals import arrival_times
from .common import distance, finite, ns, read, vec
from .geometry import segment_hits_box


class AssignmentPolicy(Protocol):
    """Return an ordered subset of eligible carriers; feasibility stays authoritative."""

    def __call__(self, order: str, eligible: tuple[str, ...]) -> tuple[str, ...]: ...


def deterministic(order: str, eligible: tuple[str, ...]) -> tuple[str, ...]:
    """Stable carrier identity order, independent of dictionary insertion order."""
    return tuple(sorted(eligible))


def trajectory_seconds(length: float, speed: float, accel: float) -> float:
    ramp = min(speed / accel, math.sqrt(length / accel))
    return 2 * ramp + max(0.0, (length - accel * ramp * ramp) / speed)


@dataclass
class Job:
    ref: EntityRef
    parcel: EntityRef
    source: EntityRef
    destination: EntityRef
    deadline_ns: int
    state: str = "unreleased"
    release_ns: int | None = None
    carrier: EntityRef | None = None
    edge: str = ""
    revision: int = 0
    dwell_since: int | None = None
    energy_start: float | None = None
    move_id: str | None = None
    move_status: str | None = None
    pending_move: bool = False
    cancel_command: Command[dict[str, Any]] | None = None
    state_at_ns: int = -1


class Logistics(ContextEngine):
    """Pickup/handoff require stopped presence for the facility's declared dwell.

    A destination locker and both operation pads are reserved before assignment.
    Locker custody persists after acceptance. Cancellation preserves the actual
    custodian and releases the carrier only after native cancellation cleanup.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        c = self.cfg
        self.fields: dict[str, str] = dict(c["fields"])
        self.refs = {r.id: r for r in build.entities}
        self.fleet = tuple(self.refs[i] for i in c["fleet"])
        self.facilities = tuple(self.refs[i] for i in c["facilities"])
        self.restrictions = tuple(self.refs[i] for i in c["restrictions"])
        for ref in self.restrictions:
            if not build.registry.is_a(ref.type_id, c["restriction_type"]):
                raise ValueError(
                    "restriction entity must have declared AeroGraph restriction type"
                )
        self.jobs = {
            item["order"]: Job(
                self.refs[item["order"]],
                self.refs[item["parcel"]],
                self.refs[item["source"]],
                self.refs[item["destination"]],
                ns(item["deadline_ns"], "deadline"),
            )
            for item in c["orders"]
        }
        if len(self.jobs) != len(c["orders"]) or len(
            {j.parcel for j in self.jobs.values()}
        ) != len(self.jobs):
            raise ValueError("orders and parcels must be unique")
        if len(set(self.fleet)) != len(self.fleet) or len(set(self.facilities)) != len(
            self.facilities
        ):
            raise ValueError("fleet and facilities must be unique")
        for job in self.jobs.values():
            if (
                job.source == job.destination
                or job.source not in self.facilities
                or job.destination not in self.facilities
            ):
                raise ValueError(
                    "order endpoints must be distinct configured facilities"
                )
        self.tick = ns(c["poll_ns"], "poll period", 1)
        self.radius = finite(c["arrival_radius_m"], "arrival radius", 0)
        self.stopped = finite(c["stopped_speed_m_s"], "stopped threshold", 0)
        energy = c["energy"]
        self.speed = finite(energy["speed_m_s"], "speed", 1e-12)
        self.accel = finite(energy["accel_m_s2"], "acceleration", 1e-12)
        self.idle = finite(energy["idle_w"], "idle power", 0)
        self.per_m = finite(energy["per_m_j"], "energy per metre", 0)
        self.reserve = finite(energy["reserve_j"], "energy reserve", 0)
        self.energy_mode = energy["source"]
        if self.energy_mode not in {"joules", "battery_fraction"}:
            raise ValueError("energy source must be joules or battery_fraction")
        if self.energy_mode == "battery_fraction":
            finite(energy["capacity_j"], "declared battery capacity", 1e-12)
        self.motion_step = ns(energy["motion_step_ns"], "motion step", 1)
        self.policy: AssignmentPolicy | None
        if c["policy"] == "external":
            self.policy = None
        elif c["policy"] == "deterministic":
            self.policy = deterministic
        else:
            module, name = c["policy"].rsplit(":", 1)
            self.policy = cast(AssignmentPolicy, getattr(import_module(module), name))
        self.storage = {r.id: 0 for r in self.facilities}
        self.pads = {r.id: 0 for r in self.facilities}
        self.reserved = {r.id: 0 for r in self.facilities}
        for job in self.jobs.values():
            self.storage[job.source.id] += 1
            job.edge = f"custody/{job.parcel.id}/0"
        descriptor = build.registry.relation(c["custody_relation"])
        if descriptor.targets_per_source.maximum != 1:
            raise ValueError(
                "custody relation must enforce at most one custodian per parcel"
            )
        self.pending: deque[Job] = deque()
        self.moves: dict[str, Job] = {}
        self.timer: str | None = None
        self.produces = tuple(c["produces"])
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=self.produces,
                consumes=tuple(Dependency(f) for f in c["consumes"]),
                commands=tuple(c["commands"].values()),
                emits=(c["event_schema"], c["motion"]["schema"]),
                subscribes=(c["decision_topic"],),
                message_targets=(c["event_topic"], c["motion"]["target"]),
                lifecycle=True,
                rng_streams=("arrivals",),
                relation_produces=(c["custody_relation"],),
                relation_consumes=(RelationDependency(c["custody_relation"]),),
                features=("relations",),
            ),
            policies=policies(self.produces),
        )

    def _field(self, ctx: EngineContext, ref: EntityRef, key: str) -> Any:
        return read(ctx, ref, self.fields[key])

    def _energy(self, ctx: EngineContext, ref: EntityRef) -> float:
        value = self._field(ctx, ref, "energy")
        if self.energy_mode == "battery_fraction":
            fraction = finite(value["remaining_fraction"], "battery fraction", 0)
            if fraction > 1:
                raise ValueError("battery fraction exceeds one")
            return fraction * finite(
                self.cfg["energy"]["capacity_j"], "capacity", 1e-12
            )
        return finite(value, "stored energy", 0)

    def _event(self, ctx: EngineContext, job: Job, **extra: Any) -> None:
        ctx.emit(
            self.cfg["event_schema"],
            {
                "order": job.ref.id,
                "state": job.state,
                "carrier": None if job.carrier is None else job.carrier.id,
                "release_ns": job.release_ns,
                "deadline_ns": job.deadline_ns,
                **extra,
            },
            topic=self.cfg["event_topic"],
        )

    def _state(self, ctx: EngineContext, job: Job, state: str, **extra: Any) -> None:
        job.state, job.dwell_since = state, None
        job.state_at_ns = ctx.now.ns
        ctx.set(job.ref, self.fields["state"], state)
        self._event(ctx, job, **extra)

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        times = arrival_times(self.cfg["arrivals"], len(self.jobs), ctx.rng("arrivals"))
        for job, release in zip(self.jobs.values(), times):
            job.release_ns = release
            ctx.relate(
                job.edge,
                self.cfg["custody_relation"],
                job.parcel,
                job.source,
                acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
                valid=Interval(ctx.now, None),
            )
        for facility in self.facilities:
            # Authored initial storage must fit the declared physical locker capacity.
            capacity = ns(
                self.build.initial[facility.id][self.fields["locker_capacity"]],
                "locker capacity",
                1,
            )
            if self.storage[facility.id] > capacity:
                raise ValueError(
                    "initial parcel custody exceeds facility locker capacity"
                )
        self.timer = ctx.wake_at(self.tick, {"logistics": "poll"})

    def _transfer(self, ctx: EngineContext, job: Job, target: EntityRef) -> None:
        job.revision += 1
        edge = f"custody/{job.parcel.id}/{job.revision}"
        ctx.replace_relation(
            job.edge,
            edge,
            self.cfg["custody_relation"],
            job.parcel,
            target,
            acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
            valid=Interval(ctx.now, None),
        )
        job.edge = edge

    def _motion(self, ctx: EngineContext, job: Job, facility: EntityRef) -> None:
        assert job.carrier is not None
        motion = self.cfg["motion"]
        payload: dict[str, Any] = dict(motion["extra_payload"])
        payload[motion["entity_key"]] = job.carrier.id
        payload[motion["target_key"]] = list(
            vec(self._field(ctx, facility, "facility_position"))
        )
        ctx.submit(CommandRequest(motion["schema"], motion["target"], ctx.now, payload))
        job.move_id, job.move_status, job.pending_move = None, None, True
        self.pending.append(job)

    def _feasible(self, ctx: EngineContext, job: Job, carrier: EntityRef) -> str | None:
        if any(
            j.carrier == carrier
            and j.state in {"pickup", "in_transit", "handoff", "canceling"}
            for j in self.jobs.values()
        ):
            return "carrier_busy"
        if any(
            edge.target == carrier
            for edge in ctx.relations(self.cfg["custody_relation"])
        ):
            return "carrier_retains_parcel"
        if (
            distance(vec(self._field(ctx, carrier, "velocity")), (0.0, 0.0, 0.0))
            > self.stopped
        ):
            return "carrier_not_stopped"
        start = vec(self._field(ctx, carrier, "position"))
        pickup = vec(self._field(ctx, job.source, "facility_position"))
        drop = vec(self._field(ctx, job.destination, "facility_position"))
        for facility in (job.source, job.destination):
            capacity = ns(self._field(ctx, facility, "pad_capacity"), "pad capacity", 1)
            if self.pads[facility.id] >= capacity:
                return "pad_capacity"
        locker_capacity = ns(
            self._field(ctx, job.destination, "locker_capacity"), "locker capacity", 1
        )
        if (
            self.storage[job.destination.id] + self.reserved[job.destination.id]
            >= locker_capacity
        ):
            return "locker_capacity"
        durations = [
            trajectory_seconds(distance(a, b), self.speed, self.accel)
            for a, b in ((start, pickup), (pickup, drop), (drop, pickup))
        ]
        rounded = sum(
            max(
                self.motion_step / 1e9,
                math.ceil(d * 1e9 / self.motion_step) * self.motion_step / 1e9,
            )
            for d in durations
        )
        dwell = sum(
            ns(self._field(ctx, f, "dwell_ns"), "dwell", 1) / 1e9
            for f in (job.source, job.destination)
        )
        length = distance(start, pickup) + 2 * distance(pickup, drop)
        cost = (
            self.per_m * length
            + self.idle * (rounded + dwell + 6 * self.tick / 1e9)
            + self.reserve
        )
        if self._energy(ctx, carrier) < cost:
            return "energy_infeasible"
        route_end = ctx.now.ns + math.ceil(
            (rounded + dwell + 6 * self.tick / 1e9) * 1e9
        )
        for restriction in self.restrictions:
            active = self._field(ctx, restriction, "restriction_active")
            if type(active) is not bool:
                raise TypeError("restriction activity requires a real boolean source")
            if not active:
                continue
            beginning = ns(
                self._field(ctx, restriction, "restriction_start_ns"),
                "restriction start",
            )
            ending = ns(
                self._field(ctx, restriction, "restriction_end_ns"), "restriction end"
            )
            if ending <= beginning:
                raise ValueError("restriction interval must be nonempty")
            if ctx.now.ns >= ending or route_end < beginning:
                continue
            activities = self._field(ctx, restriction, "restriction_activities")
            if self.cfg["activity"] not in activities:
                continue
            lower = vec(self._field(ctx, restriction, "restriction_lower"))
            upper = vec(self._field(ctx, restriction, "restriction_upper"))
            if any(
                segment_hits_box(a, b, lower, upper)
                for a, b in ((start, pickup), (pickup, drop))
            ):
                return "activity_restriction"
        return None

    def _assign(self, ctx: EngineContext, job: Job, carrier: EntityRef) -> str | None:
        if job.state != "queued":
            return "order_not_queued"
        reason = self._feasible(ctx, job, carrier)
        if reason is not None:
            return reason
        job.carrier = carrier
        job.energy_start = self._energy(ctx, carrier)
        for f in (job.source, job.destination):
            self.pads[f.id] += 1
        self.reserved[job.destination.id] += 1
        self._state(ctx, job, "pickup", energy_start_j=job.energy_start)
        self._motion(ctx, job, job.source)
        return None

    def _presence(self, ctx: EngineContext, job: Job, facility: EntityRef) -> bool:
        assert job.carrier is not None
        arrived = (
            distance(
                vec(self._field(ctx, job.carrier, "position")),
                vec(self._field(ctx, facility, "facility_position")),
            )
            <= self.radius
        )
        stopped = (
            distance(vec(self._field(ctx, job.carrier, "velocity")), (0.0, 0.0, 0.0))
            <= self.stopped
        )
        if not arrived or not stopped or job.move_status != "succeeded":
            job.dwell_since = None
            return False
        if job.dwell_since is None:
            job.dwell_since = ctx.now.ns
        return ctx.now.ns - job.dwell_since >= ns(
            self._field(ctx, facility, "dwell_ns"), "facility dwell", 1
        )

    def _release_resources(self, job: Job) -> None:
        for f in (job.source, job.destination):
            self.pads[f.id] -= 1
        self.reserved[job.destination.id] -= 1

    def _decide(self, ctx: EngineContext, job: Job, accepted: Any) -> str | None:
        if type(accepted) is not bool:
            return "explicit_boolean_decision_required"
        if job.state != "delivered":
            return "parcel_not_delivered"
        self._state(ctx, job, "accepted" if accepted else "rejected")
        return None

    def _command(self, ctx: EngineContext, command: Command[dict[str, Any]]) -> None:
        payload = command.payload
        job = self.jobs.get(payload["order"])
        if job is None:
            ctx.reject(command, {"reason": "unknown_order"})
            return
        schema = command.delivery.message.schema_id
        reason: str | None
        if schema == self.cfg["commands"]["assign"]:
            carrier = next((r for r in self.fleet if r.id == payload["carrier"]), None)
            reason = (
                "unknown_carrier"
                if carrier is None
                else self._assign(ctx, job, carrier)
            )
        elif schema == self.cfg["commands"]["decide"]:
            reason = self._decide(ctx, job, payload["accepted"])
        elif schema == self.cfg["commands"]["cancel"]:
            if job.state == "queued":
                self._state(ctx, job, "canceled")
                reason = None
            elif job.state in {"pickup", "in_transit", "handoff"}:
                ctx.accept(command)
                ctx.execute(command)
                job.cancel_command = command
                self._state(ctx, job, "canceling")
                if job.move_id is not None and job.move_status not in {
                    "succeeded",
                    "failed",
                    "rejected",
                    "canceled",
                }:
                    ctx.ops.append(RequestCancel(job.move_id, tuple(ctx.inputs)))
                return
            else:
                reason = "order_not_cancelable"
        else:
            raise ValueError("undeclared logistics command")
        if reason is not None:
            ctx.reject(command, {"reason": reason})
        else:
            ctx.accept(command)
            ctx.execute(command)
            ctx.succeed(command, {})

    def on_inputs(self, ctx: EngineContext) -> None:
        for dirty in ctx.dirty:
            if dirty.kind == "receipt":
                data = cast(dict[str, Any], thaw(dirty.payload))
                cid, status = data["command_id"], data["status"]
                if status == "submitted":
                    job = self.pending.popleft()
                    self.moves[cid] = job
                    job.move_id, job.pending_move = cid, False
                    if job.state == "canceling":
                        ctx.ops.append(RequestCancel(cid, tuple(ctx.inputs)))
                if cid in self.moves:
                    self.moves[cid].move_status = status
        for delivery in ctx.inbox:
            incoming = thaw(delivery.message.payload)
            if not isinstance(incoming, dict):
                raise TypeError("pack messages must be typed records")
            data = cast(dict[str, Any], incoming)
            if delivery.message.kind == "command":
                self._command(
                    ctx, ctx.remember(delivery, lambda v: cast(dict[str, Any], v))
                )
            elif delivery.message.schema_id == self.cfg["decision_schema"]:
                reason = self._decide(ctx, self.jobs[data["order"]], data["accepted"])
                if reason is not None:
                    raise ValueError(f"business decision rejected: {reason}")
        if any(
            d.kind == "timer" and thaw(d.payload) == {"logistics": "poll"}
            for d in ctx.dirty
        ):
            self._poll(ctx)
            self.timer = ctx.wake_at(ctx.now.ns + self.tick, {"logistics": "poll"})

    def _poll(self, ctx: EngineContext) -> None:
        for job in self.jobs.values():
            if job.state_at_ns == ctx.now.ns:
                continue
            if (
                job.release_ns is not None
                and job.state == "unreleased"
                and ctx.now.ns >= job.release_ns
            ):
                self._state(ctx, job, "queued")
                continue
            if job.state == "queued" and self.policy is not None:
                eligible = tuple(
                    r.id for r in self.fleet if self._feasible(ctx, job, r) is None
                )
                selected = self.policy(job.ref.id, eligible)
                if len(selected) != len(set(selected)) or set(selected) - set(eligible):
                    raise ValueError("assignment policy returned an ineligible carrier")
                if selected:
                    self._assign(ctx, job, self.refs[selected[0]])
            elif job.state == "canceling":
                if not job.pending_move and job.move_status in {
                    "succeeded",
                    "canceled",
                    "failed",
                    "rejected",
                }:
                    self._release_resources(job)
                    self._state(ctx, job, "canceled")
                    assert job.cancel_command is not None
                    ctx.succeed(job.cancel_command, {})
                    job.cancel_command = None
            elif job.state in {"pickup", "in_transit"}:
                if job.move_status in {"failed", "rejected", "canceled"}:
                    self._release_resources(job)
                    self._state(ctx, job, "failed", reason=f"motion_{job.move_status}")
                else:
                    facility = job.source if job.state == "pickup" else job.destination
                    if self._presence(ctx, job, facility):
                        if job.state == "pickup":
                            assert job.carrier is not None
                            self._transfer(ctx, job, job.carrier)
                            self.storage[job.source.id] -= 1
                            self._state(ctx, job, "in_transit")
                            self._motion(ctx, job, job.destination)
                        else:
                            self._transfer(ctx, job, job.destination)
                            self.storage[job.destination.id] += 1
                            self._state(ctx, job, "handoff")
            elif job.state == "handoff":
                assert job.carrier is not None and job.energy_start is not None
                energy_used = job.energy_start - self._energy(ctx, job.carrier)
                if energy_used < 0:
                    raise ValueError(
                        "carrier energy increased without a modeled recharge"
                    )
                self._release_resources(job)
                self._state(ctx, job, "delivered", energy_used_j=energy_used)


def build(context: EngineBuild) -> Logistics:
    return Logistics(context)
