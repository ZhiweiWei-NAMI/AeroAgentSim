"""Seeded task releases and receipt-driven dispatch through the motion owner."""

from __future__ import annotations

import copy
import json
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, cast

from aerokernel import CommandRequest, Dependency, EntityRef, Instant, Partition
from aerokernel.operations import ScheduleTimer
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import Allocation, Assignment, PathPlanning
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.arrivals import arrival_times
from aeroagentsim.packs.common import distance, finite, ns, read, vec
from aeroagentsim.platform.plugins import EngineBuild

from .core import SNAPSHOT_FIELDS, refs, snapshot
from .overlay import TASK_KINDS

TASK_FIELDS = (
    "lowalt.task_state",
    "lowalt.task_kind",
    "lowalt.task_priority",
    "lowalt.task_origin",
    "lowalt.task_destination",
    "lowalt.task_created_ns",
    "lowalt.task_vehicle",
    "lowalt.task_payload_kg",
)
CONSTANT_FIELDS = (
    "lowalt.capacity_j",
    "lowalt.payload_capacity_kg",
    "lowalt.vehicle_class",
    "lowalt.site_position",
    "lowalt.site_open",
    "lowalt.site_capacity",
    "lowalt.pad_power_w",
    "lowalt.zone_kind",
    "lowalt.zone_lower",
    "lowalt.zone_upper",
    "lowalt.zone_start_ns",
    "lowalt.zone_end_ns",
    "lowalt.zone_dynamic",
)


@dataclass
class Job:
    ref: EntityRef
    release_ns: int
    vehicle: str | None = None
    phase: str = "unreleased"
    created_ns: int = 0


@dataclass
class Flight:
    vehicle: EntityRef
    job: Job | None = None
    route: tuple[tuple[float, float, float], ...] = ()
    goal: tuple[float, float, float] | None = None
    pending: bool = False
    command_id: str | None = None
    status: str | None = None
    purpose: str = "task"
    hold_until_ns: int = 0
    replan: bool = False
    deferred_brake: bool = False
    waypoint: tuple[float, float, float] | None = None
    waypoint_start: tuple[float, float, float] | None = None
    suspended: Job | None = None
    suspended_phase: str | None = None
    work_active: bool = False
    saved_route: tuple[tuple[float, float, float], ...] = ()
    saved_goal: tuple[float, float, float] | None = None
    paused_route: tuple[tuple[float, float, float], ...] | None = None
    transit_resume: bool = False


class LowaltTasks(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        self.tick = ns(self.cfg["tick_ns"], "task tick", 1)
        self.algorithms = self.cfg["algorithms"]
        configs = self.cfg.get("algorithm_config", {})
        self.allocator = cast(
            Allocation,
            load_algorithm(
                "allocation", self.algorithms["allocation"], configs.get("allocation")
            ),
        )
        self.planner = cast(
            PathPlanning,
            load_algorithm(
                "path_planning",
                self.algorithms["path_planning"],
                configs.get("path_planning"),
            ),
        )
        self.travel_planner = cast(
            PathPlanning, load_algorithm("path_planning", "corridor_astar")
        )
        self.work_planners = {
            kind: cast(
                PathPlanning,
                load_algorithm("path_planning", spec["algorithm"], spec["config"]),
            )
            for kind, spec in self.cfg.get("work_routes", {}).items()
        }
        self.site_busy: dict[str, list[int]] = {}
        self.pad_waits: set[tuple[str, str]] = set()
        self.flights = {r.id: Flight(r) for r in refs(build, "lowalt:UAV")}
        self.jobs: dict[str, Job] = {}
        self.by_id = {r.id: r for r in build.entities}
        self.pending: deque[Flight] = deque()
        self.moves: dict[str, Flight] = {}
        self.llm_pending = False
        self.llm_tasks: tuple[str, ...] = ()
        self.motion = self.cfg["motion"]
        self.radius = finite(self.cfg["arrival_radius_m"], "arrival radius", 0)
        produced = TASK_FIELDS + CONSTANT_FIELDS + ("lowalt.payload_kg",)
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=produced,
                consumes=tuple(
                    Dependency(f)
                    for f in dict.fromkeys(
                        SNAPSHOT_FIELDS
                        + (
                            "lowalt.velocity",
                            "lowalt.task_created_ns",
                            "lowalt.task_payload_kg",
                            "lowalt.zone_start_ns",
                            "lowalt.zone_end_ns",
                            "lowalt.zone_dynamic",
                        )
                    )
                ),
                emits=tuple(
                    "lowalt.task." + s
                    for s in (
                        "created",
                        "assigned",
                        "started",
                        "picked_up",
                        "completed",
                        "failed",
                        "preempted",
                        "resumed",
                        "replanned",
                    )
                )
                + (
                    self.motion["move_to"],
                    self.motion["hold"],
                    "lowalt.allocation.request",
                    "lowalt.inspection.observed",
                    "lowalt.spraying.applied",
                    "lowalt.patrol.visited",
                    "lowalt.site.closed",
                    "lowalt.mobility.pad_wait",
                ),
                subscribes=("lowalt.events", "lowalt.allocations")
                + (
                    ("langgraph-records",)
                    if self.algorithms["allocation"] == "llm"
                    else ()
                ),
                message_targets=(
                    "lowalt.events",
                    "lowalt.allocations",
                    self.motion["target"],
                ),
                lifecycle=True,
                rng_streams=("arrivals", "sites"),
            ),
            policies=policies(produced),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        initial = copy.deepcopy(self.build.initial)
        for group in self.cfg["generators"]:
            kind = group["kind"]
            if kind not in TASK_KINDS:
                raise ValueError(f"unsupported task kind {kind}")
            pool = [self.by_id[identity] for identity in group["tasks"]]
            releases = arrival_times(group["arrivals"], len(pool), ctx.rng("arrivals"))
            for ref, release in zip(pool, releases):
                if ref.id in self.jobs:
                    raise ValueError("task belongs to two generators")
                origin = ctx.rng("sites").choice(group["origins"])
                destinations = [s for s in group["destinations"] if s != origin]
                if not destinations:
                    raise ValueError("task generator needs distinct endpoints")
                destination = ctx.rng("sites").choice(destinations)
                for site in (origin, destination):
                    if not self.build.registry.is_a(
                        self.by_id[site].type_id, "lowalt:Site"
                    ):
                        raise ValueError("task endpoints must be sites")
                self.jobs[ref.id] = Job(ref, release)
                for key, value in (
                    ("kind", kind),
                    ("priority", group["priority"]),
                    ("origin", origin),
                    ("destination", destination),
                    ("payload_kg", group["payload_kg"]),
                ):
                    initial[ref.id]["lowalt.task_" + key] = value
        if set(self.jobs) != {r.id for r in refs(self.build, "lowalt:Task")}:
            raise ValueError("every authored task must belong to a generator")
        bootstrap_owned(ctx, replace(self.build, initial=initial))
        for release in sorted({j.release_ns for j in self.jobs.values()}):
            if release == ctx.now.ns:
                ctx.ops.append(
                    ScheduleTimer(
                        "lowalt/tasks/initial-release",
                        Instant(release, ctx.now.microstep + 1),
                        {"lowalt_tasks": "release"},
                        tuple(ctx.inputs),
                    )
                )
            else:
                ctx.wake_at(release, {"lowalt_tasks": "release"})
        ctx.wake_at(self.tick, {"lowalt_tasks": "tick"})

    def _event(self, ctx: EngineContext, phase: str, **payload: Any) -> None:
        ctx.emit("lowalt.task." + phase, payload, topic="lowalt.events")

    def _state(self, ctx: EngineContext, job: Job, phase: str) -> None:
        job.phase = phase
        ctx.set(job.ref, "lowalt.task_state", phase)

    def _submit(
        self,
        ctx: EngineContext,
        flight: Flight,
        goal: tuple[float, float, float] | None,
    ) -> None:
        if flight.pending or flight.command_id is not None:
            raise ValueError("motion has an outstanding command")
        schema = self.motion["hold"] if goal is None else self.motion["move_to"]
        payload: dict[str, Any] = {"entity": flight.vehicle.id}
        if goal is not None:
            payload["target"] = list(goal)
        flight.waypoint = goal
        flight.waypoint_start = vec(read(ctx, flight.vehicle, "lowalt.position"))
        ctx.submit(CommandRequest(schema, self.motion["target"], ctx.now, payload))
        flight.pending, flight.status = True, None
        self.pending.append(flight)

    def _route(
        self, ctx: EngineContext, flight: Flight, goal: tuple[float, float, float]
    ) -> None:
        start = vec(read(ctx, flight.vehicle, "lowalt.position"))
        state = snapshot(ctx, self.build)
        exemptions = self.cfg.get("zone_exemptions", {})
        state = replace(
            state,
            zones=tuple(
                z
                for z in state.zones
                if flight.vehicle.id not in exemptions.get(z.id, ())
            ),
        )
        planner = self.travel_planner if self.work_planners else self.planner
        if (
            flight.job is not None
            and flight.job.phase == "picked_up"
            and flight.purpose == "task"
        ):
            kind = read(ctx, flight.job.ref, "lowalt.task_kind")
            if kind in self.work_planners and not flight.work_active:
                planner = self.work_planners[kind]
                flight.work_active = True
        cruise = self.cfg.get("cruise_altitudes", {}).get(
            read(ctx, flight.vehicle, "lowalt.vehicle_class")
        )
        if (
            planner in self.work_planners.values()
            or cruise is None
            or distance(start, goal) <= self.radius
        ):
            points = planner.plan(state, start, goal)
        else:
            from aeroagentsim.algorithms.routes import cruise_plan

            points = cruise_plan(planner, state, start, goal, cruise)
        from aeroagentsim.packs.geometry import segment_hits_box

        if any(
            z.active and z.kind == "no_fly" and segment_hits_box(a, b, z.lower, z.upper)
            for a, b in zip((start,) + tuple(points), points)
            for z in state.zones
        ):
            raise ValueError("path proposal crosses an active no-fly zone")
        if not points or distance(points[-1], goal) > self.radius:
            raise ValueError("path planner must reach requested goal")
        flight.goal, flight.route = goal, tuple(points)
        flight.replan = False

    def _assign(self, ctx: EngineContext, assignments: tuple[Assignment, ...]) -> None:
        used_tasks: set[str] = set()
        used_vehicles: set[str] = set()
        for proposal in assignments:
            if proposal.task in used_tasks or proposal.vehicle in used_vehicles:
                raise ValueError("allocator duplicated task or vehicle")
            used_tasks.add(proposal.task)
            used_vehicles.add(proposal.vehicle)
            job, flight = self.jobs[proposal.task], self.flights[proposal.vehicle]
            if (
                job.phase != "queued"
                or flight.job is not None
                or flight.purpose != "task"
                or flight.pending
                or flight.command_id is not None
            ):
                raise ValueError("allocation proposal selects busy task or vehicle")
            state = next(
                v
                for v in snapshot(ctx, self.build).vehicles
                if v.id == proposal.vehicle
            )
            payload = finite(
                read(ctx, job.ref, "lowalt.task_payload_kg"), "task payload", 0
            )
            if (
                not state.available
                or not self._eligible(ctx, job, flight)
                or payload > state.payload_capacity_kg
                or state.soc <= self.cfg["dispatch_min_soc"]
            ):
                raise ValueError("allocation proposal violates vehicle eligibility")
            job.vehicle, flight.job = proposal.vehicle, job
            ctx.set(job.ref, "lowalt.task_vehicle", proposal.vehicle)
            self._state(ctx, job, "assigned")
            self._event(
                ctx,
                "assigned",
                task=job.ref.id,
                vehicle=proposal.vehicle,
                algorithm=self.algorithms["allocation"],
            )
            origin = self.by_id[read(ctx, job.ref, "lowalt.task_origin")]
            self._route(ctx, flight, vec(read(ctx, origin, "lowalt.site_position")))

    def _control(self, ctx: EngineContext, payload: dict[str, Any]) -> None:
        flight = self.flights[payload["vehicle"]]
        action = payload["action"]
        if action in {"release", "resume"}:
            flight.hold_until_ns = ctx.now.ns
            flight.replan = True
            return
        if action == "reroute":
            # The geofence rule: plan again, around active zones, from here.
            flight.replan = True
            self._brake(ctx, flight)
            return
        if action in {"return_to_pad", "altitude"}:
            if flight.purpose == "task":
                if flight.work_active:
                    flight.saved_route = (
                        flight.paused_route
                        if flight.paused_route is not None
                        else ((flight.waypoint,) if flight.waypoint is not None else ())
                        + flight.route
                    )
                    flight.saved_route = (
                        vec(read(ctx, flight.vehicle, "lowalt.position")),
                    ) + flight.saved_route
                else:
                    # A transit resumes from the new safe position. Returning
                    # to the pre-avoidance roof defeats altitude separation.
                    flight.saved_route = ()
                flight.paused_route = None
                flight.saved_goal = flight.goal
            target = vec(payload["target"])
            flight.route = ()
            # Returning to charge pauses the real task; it is resumed afterwards.
            flight.goal = target
            flight.purpose = "charge" if action == "return_to_pad" else "avoidance"
        elif action == "hold":
            if flight.purpose == "task" and flight.paused_route is None:
                flight.paused_route = (
                    (flight.waypoint,) if flight.waypoint is not None else ()
                ) + flight.route
            flight.hold_until_ns = (
                ns(payload["until_ns"], "hold end")
                if "until_ns" in payload
                else ctx.now.ns + self.tick
            )
        else:
            raise ValueError(f"unsupported lowalt control {action}")
        flight.replan = True
        self._brake(ctx, flight)

    def _brake(self, ctx: EngineContext, flight: Flight) -> None:
        if flight.pending:
            flight.deferred_brake = True
            return
        # hold interrupts the native command and has its own real stop receipt.
        if flight.command_id is not None:
            flight.command_id = None
        self._submit(ctx, flight, None)

    def on_inputs(self, ctx: EngineContext) -> None:
        for dirty in ctx.dirty:
            if dirty.kind != "receipt":
                continue
            data = cast(dict[str, Any], thaw(dirty.payload))
            cid, status = data["command_id"], data["status"]
            if status == "submitted":
                flight = self.pending.popleft()
                self.moves[cid] = flight
                flight.command_id, flight.pending = cid, False
            if cid in self.moves:
                flight = self.moves[cid]
                if flight.command_id == cid:
                    flight.status = status
                    if status in {"succeeded", "failed", "rejected", "canceled"}:
                        if status == "succeeded" and flight.waypoint is not None:
                            self._observation(ctx, flight)
                        flight.waypoint = None
                        flight.command_id = None
                        if status != "succeeded" and not flight.replan:
                            if flight.job is not None:
                                job = flight.job
                                self._state(ctx, job, "failed")
                                self._event(
                                    ctx,
                                    "failed",
                                    task=job.ref.id,
                                    vehicle=flight.vehicle.id,
                                    reason=str(data.get("result", status)),
                                )
                                flight.job = None
                            flight.route, flight.goal = (), None
                if status in {"succeeded", "failed", "rejected", "canceled"}:
                    self.moves.pop(cid)
        for flight in self.flights.values():
            if flight.deferred_brake and not flight.pending:
                flight.deferred_brake = False
                flight.command_id = None
                self._submit(ctx, flight, None)
        for delivery in ctx.inbox:
            payload = cast(dict[str, Any], thaw(delivery.message.payload))
            if (
                delivery.message.schema_id == "aas.langgraph.record"
                and payload["phase"] == "failure"
                and payload["decision_id"].startswith("lowalt_allocation/")
                and self.llm_pending
            ):
                self.llm_pending = False
                failure = json.loads(payload["data_json"])
                for identity in self.llm_tasks:
                    job = self.jobs[identity]
                    if job.phase == "queued":
                        self._state(ctx, job, "failed")
                        self._event(
                            ctx,
                            "failed",
                            task=identity,
                            reason="allocation decision: "
                            + failure["code"]
                            + ": "
                            + failure["message"],
                        )
            elif delivery.message.schema_id == "lowalt.airspace.zone_activated":
                self._zone_changed(ctx, payload["zone"])
            elif delivery.message.schema_id.startswith("lowalt.operator."):
                self._operator(ctx, delivery.message.schema_id, payload)
            elif delivery.message.schema_id == "lowalt.control":
                self._control(ctx, payload)
            elif delivery.message.schema_id == "lowalt.allocation.result":
                self.llm_pending = False
                rows = json.loads(payload["assignments_json"])
                self._assign(
                    ctx, tuple(Assignment(row["task"], row["vehicle"]) for row in rows)
                )
        timers = [thaw(d.payload) for d in ctx.dirty if d.kind == "timer"]
        if any(
            payload in ({"lowalt_tasks": "tick"}, {"lowalt_tasks": "release"})
            for payload in timers
        ):
            self._poll(ctx)
        if {"lowalt_tasks": "tick"} in timers:
            ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_tasks": "tick"})

    def _eligible(self, ctx: EngineContext, job: Job, flight: Flight) -> bool:
        if "vehicle_kinds" not in self.cfg:
            return True
        kind = read(ctx, job.ref, "lowalt.task_kind")
        vehicle_class = read(ctx, flight.vehicle, "lowalt.vehicle_class")
        return kind in self.cfg["vehicle_kinds"][vehicle_class]

    def _preempt(self, ctx: EngineContext) -> None:
        if not self.cfg.get("medical_preemption", False):
            return
        for urgent in sorted(self.jobs.values(), key=lambda j: j.ref.id):
            if (
                urgent.phase != "queued"
                or read(ctx, urgent.ref, "lowalt.task_state") != "queued"
                or read(ctx, urgent.ref, "lowalt.task_kind") != "medical"
            ):
                continue
            candidates = [
                f
                for f in self.flights.values()
                if f.job is not None
                and f.suspended is None
                and f.purpose == "task"
                and self._eligible(ctx, urgent, f)
                and read(ctx, f.job.ref, "lowalt.task_kind") == "parcel"
                and read(ctx, f.job.ref, "lowalt.task_priority")
                < read(ctx, urgent.ref, "lowalt.task_priority")
                and read(ctx, f.vehicle, "lowalt.payload_kg")
                + read(ctx, urgent.ref, "lowalt.task_payload_kg")
                <= read(ctx, f.vehicle, "lowalt.payload_capacity_kg")
                and not read(ctx, f.vehicle, "lowalt.weather_held")
            ]
            if not candidates:
                continue
            origin = vec(
                read(
                    ctx,
                    self.by_id[read(ctx, urgent.ref, "lowalt.task_origin")],
                    "lowalt.site_position",
                )
            )
            flight = min(
                candidates,
                key=lambda f: (
                    distance(vec(read(ctx, f.vehicle, "lowalt.position")), origin),
                    f.vehicle.id,
                ),
            )
            assert flight.job is not None
            paused = flight.job
            flight.suspended, flight.suspended_phase = paused, paused.phase
            self._state(ctx, paused, "paused")
            self._event(
                ctx,
                "preempted",
                task=paused.ref.id,
                vehicle=flight.vehicle.id,
                urgent_task=urgent.ref.id,
            )
            flight.job = urgent
            urgent.vehicle = flight.vehicle.id
            ctx.set(urgent.ref, "lowalt.task_vehicle", flight.vehicle.id)
            self._state(ctx, urgent, "assigned")
            self._event(
                ctx,
                "assigned",
                task=urgent.ref.id,
                vehicle=flight.vehicle.id,
                algorithm="priority_preemption",
            )
            flight.route, flight.goal = (), None
            flight.replan, flight.work_active = True, False
            self._brake(ctx, flight)

    def _zone_changed(self, ctx: EngineContext, identity: str) -> None:
        ref = self.by_id[identity]
        lower, upper = (
            vec(read(ctx, ref, "lowalt.zone_lower")),
            vec(read(ctx, ref, "lowalt.zone_upper")),
        )
        from aeroagentsim.packs.geometry import segment_hits_box

        for flight in self.flights.values():
            if flight.job is None or flight.vehicle.id in self.cfg.get(
                "zone_exemptions", {}
            ).get(identity, ()):
                continue
            start = vec(read(ctx, flight.vehicle, "lowalt.position"))
            points = (
                (flight.waypoint,) if flight.waypoint is not None else ()
            ) + flight.route
            if any(
                segment_hits_box(a, b, lower, upper)
                for a, b in zip((start,) + points, points)
            ):
                flight.replan = True
                flight.paused_route = None
                flight.route = ()
                self._event(
                    ctx,
                    "replanned",
                    task=flight.job.ref.id,
                    vehicle=flight.vehicle.id,
                    zone=identity,
                    reason="active no-fly zone",
                )
                self._brake(ctx, flight)

    def _observation(self, ctx: EngineContext, flight: Flight) -> None:
        if (
            flight.job is None
            or flight.job.phase != "picked_up"
            or not flight.work_active
            or flight.purpose != "task"
        ):
            return
        if flight.transit_resume:
            flight.transit_resume = False
            return
        assert flight.waypoint_start is not None
        point = vec(read(ctx, flight.vehicle, "lowalt.position"))
        moved = distance(flight.waypoint_start, point)
        if moved <= 1e-6:
            return
        kind = read(ctx, flight.job.ref, "lowalt.task_kind")
        schema = {
            "inspection": "lowalt.inspection.observed",
            "survey": "lowalt.inspection.observed",
            "spraying": "lowalt.spraying.applied",
            "patrol": "lowalt.patrol.visited",
        }.get(kind)
        if schema is not None:
            payload: dict[str, Any] = {
                "task": flight.job.ref.id,
                "vehicle": flight.vehicle.id,
                "position": list(point),
            }
            if kind != "patrol":
                payload["distance_m"] = moved
            ctx.emit(schema, payload, topic="lowalt.events")

    def _operator(
        self, ctx: EngineContext, schema: str, payload: dict[str, Any]
    ) -> None:
        if schema == "lowalt.operator.close_vertiport":
            identity = payload["site"]
            site = self.by_id[identity]
            if not self.build.registry.is_a(site.type_id, "lowalt:Vertiport"):
                raise ValueError("operator must close an actual vertiport")
            alternative = self.cfg["site_diversions"][identity]
            if not read(ctx, self.by_id[alternative], "lowalt.site_open"):
                raise ValueError("diversion vertiport is closed")
            ctx.set(site, "lowalt.site_open", False)
            ctx.emit("lowalt.site.closed", {"site": identity}, topic="lowalt.events")
            for job in self.jobs.values():
                if job.phase in {"completed", "failed"}:
                    continue
                changed = False
                for endpoint in ("origin", "destination"):
                    if read(ctx, job.ref, "lowalt.task_" + endpoint) == identity:
                        ctx.set(job.ref, "lowalt.task_" + endpoint, alternative)
                        changed = True
                if changed and job.vehicle is not None:
                    flight = self.flights[job.vehicle]
                    flight.replan = True
                    flight.route = ()
                    self._event(
                        ctx,
                        "replanned",
                        task=job.ref.id,
                        vehicle=flight.vehicle.id,
                        reason="vertiport closed",
                    )
                    self._brake(ctx, flight)
        else:
            job = self.jobs[payload["task"]]
            if job.phase != "unreleased":
                raise ValueError("injection requires an unused authored task")
            for endpoint in ("origin", "destination"):
                site = self.by_id[payload[endpoint]]
                if not self.build.registry.is_a(site.type_id, "lowalt:Site"):
                    raise ValueError("injection endpoints must be actual sites")
                ctx.set(job.ref, "lowalt.task_" + endpoint, site.id)
            kind = (
                "medical"
                if schema == "lowalt.operator.add_urgent_order"
                else "incident_response"
            )
            ctx.set(job.ref, "lowalt.task_kind", kind)
            ctx.set(job.ref, "lowalt.task_priority", 100)
            job.release_ns = ctx.now.ns
            # Release next tick, after endpoint/kind facts commit.

    def _poll(self, ctx: EngineContext) -> None:
        for job in self.jobs.values():
            if job.phase == "unreleased" and ctx.now.ns >= job.release_ns:
                job.created_ns = job.release_ns
                ctx.set(job.ref, "lowalt.task_created_ns", job.created_ns)
                self._state(ctx, job, "queued")
                self._event(
                    ctx,
                    "created",
                    task=job.ref.id,
                    kind=read(ctx, job.ref, "lowalt.task_kind"),
                    priority=read(ctx, job.ref, "lowalt.task_priority"),
                    origin=read(ctx, job.ref, "lowalt.task_origin"),
                    destination=read(ctx, job.ref, "lowalt.task_destination"),
                )
        self._preempt(ctx)
        for flight in self.flights.values():
            if flight.pending or flight.command_id is not None:
                continue
            if flight.job is None and flight.purpose == "task":
                continue
            held = read(ctx, flight.vehicle, "lowalt.weather_held")
            charging = read(ctx, flight.vehicle, "lowalt.charging")
            if held or charging or ctx.now.ns < flight.hold_until_ns:
                continue
            if (
                distance(
                    vec(read(ctx, flight.vehicle, "lowalt.velocity")), (0.0, 0.0, 0.0)
                )
                > 1e-6
            ):
                self._submit(ctx, flight, None)
                continue
            if flight.purpose in {"charge", "avoidance"}:
                if flight.goal is None:
                    raise ValueError("control route lacks a target")
                if (
                    distance(
                        vec(read(ctx, flight.vehicle, "lowalt.position")), flight.goal
                    )
                    <= self.radius
                ):
                    if (
                        flight.purpose == "charge"
                        and read(ctx, flight.vehicle, "lowalt.soc")
                        < self.cfg["charging_resume_soc"]
                    ):
                        continue
                    flight.purpose = "task"
                    flight.transit_resume = bool(flight.saved_route)
                    flight.goal, flight.route = flight.saved_goal, flight.saved_route
                    flight.saved_goal, flight.saved_route = None, ()
                    flight.replan = not bool(flight.route)
            if flight.job is None and flight.purpose == "task":
                continue
            if flight.paused_route is not None:
                flight.route = flight.paused_route
                flight.paused_route = None
                flight.replan = False
            if flight.purpose == "task":
                assert flight.job is not None
                job = flight.job
                if job.phase == "assigned":
                    self._state(ctx, job, "started")
                    self._event(
                        ctx, "started", task=job.ref.id, vehicle=flight.vehicle.id
                    )
                endpoint = "origin" if job.phase == "started" else "destination"
                goal = vec(
                    read(
                        ctx,
                        self.by_id[read(ctx, job.ref, "lowalt.task_" + endpoint)],
                        "lowalt.site_position",
                    )
                )
                if flight.replan or not flight.route and flight.goal is None:
                    self._route(ctx, flight, goal)
                if (
                    not flight.route
                    and distance(
                        vec(read(ctx, flight.vehicle, "lowalt.position")), goal
                    )
                    <= self.radius
                    and flight.status == "succeeded"
                ):
                    if job.phase == "started":
                        self._state(ctx, job, "picked_up")
                        payload = read(ctx, job.ref, "lowalt.task_payload_kg")
                        if (
                            flight.suspended is not None
                            and flight.suspended_phase == "picked_up"
                        ):
                            payload += read(
                                ctx, flight.suspended.ref, "lowalt.task_payload_kg"
                            )
                        ctx.set(flight.vehicle, "lowalt.payload_kg", payload)
                        self._event(
                            ctx, "picked_up", task=job.ref.id, vehicle=flight.vehicle.id
                        )
                        if read(ctx, job.ref, "lowalt.task_kind") == "passenger":
                            site = read(ctx, job.ref, "lowalt.task_origin")
                            self.site_busy.setdefault(site, []).append(
                                ctx.now.ns + ns(self.cfg["pad_dwell_ns"], "pad dwell")
                            )
                        destination = self.by_id[
                            read(ctx, job.ref, "lowalt.task_destination")
                        ]
                        self._route(
                            ctx,
                            flight,
                            vec(read(ctx, destination, "lowalt.site_position")),
                        )
                    else:
                        self._state(ctx, job, "completed")
                        remaining_payload = (
                            read(ctx, flight.suspended.ref, "lowalt.task_payload_kg")
                            if flight.suspended is not None
                            and flight.suspended_phase == "picked_up"
                            else 0.0
                        )
                        ctx.set(flight.vehicle, "lowalt.payload_kg", remaining_payload)
                        self._event(
                            ctx,
                            "completed",
                            task=job.ref.id,
                            vehicle=flight.vehicle.id,
                            latency_s=(ctx.now.ns - job.created_ns) / 1e9,
                        )
                        kind = read(ctx, job.ref, "lowalt.task_kind")
                        if kind == "passenger":
                            site = read(ctx, job.ref, "lowalt.task_destination")
                            self.site_busy.setdefault(site, []).append(
                                ctx.now.ns + ns(self.cfg["pad_dwell_ns"], "pad dwell")
                            )
                        flight.job, flight.goal = None, None
                        flight.work_active = False
                        if flight.suspended is not None:
                            paused = flight.suspended
                            assert flight.suspended_phase is not None
                            self._state(ctx, paused, flight.suspended_phase)
                            flight.job = paused
                            flight.suspended, flight.suspended_phase = None, None
                            flight.replan = True
                            self._event(
                                ctx,
                                "resumed",
                                task=paused.ref.id,
                                vehicle=flight.vehicle.id,
                            )
                        continue
            if flight.replan and flight.goal is not None:
                self._route(ctx, flight, flight.goal)
            if flight.route:
                # Replan a remaining segment if a dynamic zone activated.
                goal = flight.route[0]
                from aeroagentsim.packs.geometry import segment_hits_box

                state = snapshot(ctx, self.build)
                start = vec(read(ctx, flight.vehicle, "lowalt.position"))
                if any(
                    z.active
                    and z.kind == "no_fly"
                    and segment_hits_box(start, goal, z.lower, z.upper)
                    for z in state.zones
                ):
                    assert flight.goal is not None
                    self._route(ctx, flight, flight.goal)
                    goal = flight.route[0]
                if (
                    flight.job is not None
                    and flight.job.phase in {"started", "picked_up"}
                    and read(ctx, flight.job.ref, "lowalt.task_kind") == "passenger"
                    and len(flight.route) == 1
                ):
                    endpoint = (
                        "origin" if flight.job.phase == "started" else "destination"
                    )
                    site = read(ctx, flight.job.ref, "lowalt.task_" + endpoint)
                    capacity = read(ctx, self.by_id[site], "lowalt.site_capacity")
                    if capacity < 1:
                        raise ValueError("vertiport pad capacity must be positive")
                    # Landing reservations are retained through real arrival plus dwell.
                    reservations = sum(
                        1
                        for other in self.flights.values()
                        if other is not flight
                        and other.job is not None
                        and other.job.phase in {"started", "picked_up"}
                        and read(ctx, other.job.ref, "lowalt.task_kind") == "passenger"
                        and read(
                            ctx,
                            other.job.ref,
                            "lowalt.task_"
                            + (
                                "origin"
                                if other.job.phase == "started"
                                else "destination"
                            ),
                        )
                        == site
                        and other.command_id is not None
                        and not other.route
                    )
                    if (
                        reservations
                        + sum(end > ctx.now.ns for end in self.site_busy.get(site, ()))
                        >= capacity
                    ):
                        key = (flight.job.ref.id, site)
                        if key not in self.pad_waits:
                            self.pad_waits.add(key)
                            ctx.emit(
                                "lowalt.mobility.pad_wait",
                                {
                                    "task": flight.job.ref.id,
                                    "vehicle": flight.vehicle.id,
                                    "site": site,
                                },
                                topic="lowalt.events",
                            )
                        continue
                flight.route = flight.route[1:]
                self._submit(ctx, flight, goal)
        state = snapshot(ctx, self.build)
        # Facts proposed above become visible at the next cut; filter against
        # retained state too so a completed task cannot be allocated twice.
        state = replace(
            state,
            tasks=tuple(t for t in state.tasks if self.jobs[t.id].phase == "queued"),
            vehicles=tuple(
                replace(
                    v,
                    task_kinds=tuple(
                        self.cfg["vehicle_kinds"][
                            read(ctx, self.by_id[v.id], "lowalt.vehicle_class")
                        ]
                    )
                    if "vehicle_kinds" in self.cfg
                    else (),
                    available=v.available
                    and self.flights[v.id].job is None
                    and self.flights[v.id].purpose == "task"
                    and not self.flights[v.id].pending
                    and self.flights[v.id].command_id is None
                    and v.soc > self.cfg["dispatch_min_soc"],
                )
                for v in state.vehicles
            ),
        )
        from aeroagentsim.algorithms.builtins import eligible

        if any(eligible(t, v) for t in state.tasks for v in state.vehicles):
            if self.algorithms["allocation"] == "llm":
                if not self.llm_pending:
                    from dataclasses import asdict

                    ctx.emit(
                        "lowalt.allocation.request",
                        {
                            "tasks_json": json.dumps([asdict(t) for t in state.tasks]),
                            "vehicles_json": json.dumps(
                                [asdict(v) for v in state.vehicles]
                            ),
                        },
                        topic="lowalt.allocations",
                    )
                    self.llm_pending = True
                    self.llm_tasks = tuple(t.id for t in state.tasks)
            else:
                self._assign(ctx, self.allocator.allocate(state))


def build(context: EngineBuild) -> LowaltTasks:
    return LowaltTasks(context)
