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
from aeroagentsim.algorithms.protocols import (
    Allocation,
    Assignment,
    PathPlanning,
    Point,
    Snapshot,
)
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.arrivals import arrival_times
from aeroagentsim.packs.common import distance, finite, ns, read, vec
from aeroagentsim.platform.plugins import EngineBuild

from .core import SNAPSHOT_FIELDS, SnapshotIndex, refs, snapshot
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
FOOD_FIELDS = (
    "lowalt.food_preparation_s",
    "lowalt.food_preparation_started_ns",
    "lowalt.food_expected_ready_ns",
    "lowalt.food_ready_ns",
    "lowalt.food_picked_up_ns",
    "lowalt.food_delivered_ns",
    "lowalt.food_deadline_ns",
)


@dataclass
class Job:
    ref: EntityRef
    release_ns: int | None
    vehicle: str | None = None
    phase: str = "unreleased"
    created_ns: int = 0
    # None means unplanned; () means every original work waypoint has a real
    # success receipt. Job ownership also preserves work across preemption.
    work_obligations: tuple[Point, ...] | None = None
    food_window_ns: int | None = None
    preparation_started_ns: int | None = None
    ready_ns: int | None = None


class _WorkPlanRecorder:
    """Retain the winning planner output, excluding emergency egress prefixes."""

    def __init__(self, planner: PathPlanning) -> None:
        self.planner = planner
        self.points: tuple[Point, ...] = ()
        self.start: Point | None = None

    def plan(self, snapshot: Snapshot, start: Point, goal: Point) -> tuple[Point, ...]:
        self.start = start
        self.points = self.planner.plan(snapshot, start, goal)
        return self.points


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
    waypoint_is_work: bool = False
    waypoint_is_connector: bool = False
    work_reconnect_goal: Point | None = None
    suspended: Job | None = None
    suspended_phase: str | None = None
    work_active: bool = False
    saved_route: tuple[tuple[float, float, float], ...] = ()
    saved_goal: tuple[float, float, float] | None = None
    paused_route: tuple[tuple[float, float, float], ...] | None = None
    transit_resume: bool = False
    cruise_altitude_m: float | None = None
    avoidance_resume_goal: tuple[float, float, float] | None = None
    avoidance_resume_purpose: str = "task"
    egress_waypoint: tuple[float, float, float] | None = None
    route_blocked_reason: str | None = None


class LowaltTasks(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        self.snapshot_index = SnapshotIndex(build)
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
        self.generators = self._configured_generators()
        self.pending: deque[Flight] = deque()
        self.moves: dict[str, Flight] = {}
        self.llm_pending = False
        self.llm_tasks: tuple[str, ...] = ()
        # Allocation batching: pending orders accumulate for batch_window_s and
        # the allocator then sees all of them (and all free drones) at once.
        window = finite(self.cfg.get("batch_window_s", 0.0), "batch window", 0)
        self.batch_ns = round(window * 1e9)
        self.last_batch_slot = -1
        # Cost-model constants handed to allocators (speed, energy per metre).
        self.vehicle_model = self.cfg.get("vehicle_model", {})
        self.motion = self.cfg["motion"]
        self.food_enabled = any(g["kind"] == "food" for g in self.cfg["generators"])
        self.radius = finite(self.cfg["arrival_radius_m"], "arrival radius", 0)
        # Reuse only the committed base snapshot within ONE engine callback.
        # Lazy reads preserve the causal scope of earlier receipts/releases.
        self.snapshot_ctx: EngineContext | None = None
        self.snapshot_state: Snapshot | None = None
        produced = TASK_FIELDS + CONSTANT_FIELDS + ("lowalt.payload_kg",)
        if self.food_enabled:
            produced += FOOD_FIELDS
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
                            "lowalt.returning_to_charge",
                            "lowalt.velocity",
                            "lowalt.task_created_ns",
                            "lowalt.task_payload_kg",
                            "lowalt.zone_start_ns",
                            "lowalt.zone_end_ns",
                            "lowalt.zone_dynamic",
                        )
                        + (FOOD_FIELDS if self.food_enabled else ())
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
                )
                + (
                    tuple(
                        "lowalt.food." + name
                        for name in (
                            "preparation_started",
                            "ready",
                            "preparation_delayed",
                            "delivered",
                        )
                    )
                    if self.food_enabled
                    else ()
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

    def _configured_generators(
        self,
    ) -> list[tuple[dict[str, Any], tuple[EntityRef, ...], float, int | None]]:
        """Validate authored inputs without starting the runtime or drawing RNG."""
        configured = []
        tasks: set[str] = set()
        for group in self.cfg["generators"]:
            kind = group["kind"]
            if kind not in TASK_KINDS:
                raise ValueError(f"unsupported task kind {kind}")
            payload_kg = finite(group["payload_kg"], "task payload", 0)
            pool = tuple(self.by_id[identity] for identity in group["tasks"])
            ns(group["priority"], "task priority")
            for ref in pool:
                if ref.id in tasks:
                    raise ValueError("task belongs to two generators")
                tasks.add(ref.id)
            if not group["origins"]:
                raise ValueError("task generator needs an origin")
            for origin in group["origins"]:
                if not any(site != origin for site in group["destinations"]):
                    raise ValueError("task generator needs distinct endpoints")
            for site in (*group["origins"], *group["destinations"]):
                if not self.build.registry.is_a(
                    self.by_id[site].type_id, "lowalt:Site"
                ):
                    raise ValueError("task endpoints must be sites")
            food_window_ns = None
            if kind == "food":
                if any(
                    not self.build.registry.is_a(ref.type_id, "lowalt:FoodOrder")
                    for ref in pool
                ):
                    raise ValueError("food tasks must be authored FoodOrder entities")
                for origin in group["origins"]:
                    if not self.build.registry.is_a(
                        self.by_id[origin].type_id, "lowalt:Restaurant"
                    ):
                        raise ValueError("food order origins must be restaurants")
                    facts = self.build.initial[origin]
                    finite(
                        facts["lowalt.food_preparation_s"], "food preparation", 0.001
                    )
                    ns(facts["lowalt.site_capacity"], "kitchen capacity", 1)
                food_window_ns = round(
                    finite(group["delivery_window_s"], "food delivery window", 0.001)
                    * 1e9
                )
            configured.append((group, pool, payload_kg, food_window_ns))
        if tasks != {r.id for r in refs(self.build, "lowalt:Task")}:
            raise ValueError("every authored task must belong to a generator")
        return configured

    def bootstrap(self, ctx: EngineContext) -> None:
        initial = copy.deepcopy(self.build.initial)
        for group, pool, payload_kg, food_window_ns in self.generators:
            kind = group["kind"]
            releases = arrival_times(group["arrivals"], len(pool), ctx.rng("arrivals"))
            for index, ref in enumerate(pool):
                # Authored slots beyond an explicit arrival cutoff never become
                # orders. None is a missing arrival, not an invented timestamp.
                release = releases[index] if index < len(releases) else None
                origin = ctx.rng("sites").choice(group["origins"])
                destinations = [s for s in group["destinations"] if s != origin]
                destination = ctx.rng("sites").choice(destinations)
                job = self.jobs[ref.id] = Job(ref, release)
                job.food_window_ns = food_window_ns
                for key, value in (
                    ("kind", kind),
                    ("priority", group["priority"]),
                    ("origin", origin),
                    ("destination", destination),
                    ("payload_kg", payload_kg),
                ):
                    initial[ref.id]["lowalt.task_" + key] = value
        bootstrap_owned(ctx, replace(self.build, initial=initial))
        for release in sorted(
            {j.release_ns for j in self.jobs.values() if j.release_ns is not None}
        ):
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
        flight.waypoint_is_work = bool(
            goal is not None
            and flight.purpose == "task"
            and flight.job is not None
            and flight.job.work_obligations
            and goal == flight.job.work_obligations[0]
            and goal != flight.egress_waypoint
        )
        flight.waypoint_is_connector = (
            goal is not None and goal == flight.work_reconnect_goal
        )
        flight.waypoint_start = vec(read(ctx, flight.vehicle, "lowalt.position"))
        ctx.submit(CommandRequest(schema, self.motion["target"], ctx.now, payload))
        flight.pending, flight.status = True, None
        self.pending.append(flight)

    def _snapshot(self, ctx: EngineContext) -> Snapshot:
        if self.snapshot_ctx is not ctx or self.snapshot_state is None:
            self.snapshot_ctx = ctx
            self.snapshot_state = snapshot(ctx, self.build, index=self.snapshot_index)
        return self.snapshot_state

    def _route(
        self, ctx: EngineContext, flight: Flight, goal: tuple[float, float, float]
    ) -> bool:
        start = vec(read(ctx, flight.vehicle, "lowalt.position"))
        state = self._snapshot(ctx)
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
        work_active_before = flight.work_active
        recorder: _WorkPlanRecorder | None = None
        obligations: tuple[Point, ...] = ()
        if (
            flight.job is not None
            and flight.job.phase == "picked_up"
            and flight.purpose == "task"
        ):
            kind = read(ctx, flight.job.ref, "lowalt.task_kind")
            if flight.job.work_obligations is not None:
                obligations = flight.job.work_obligations
                flight.work_active = True
            elif kind in self.work_planners:
                recorder = _WorkPlanRecorder(self.work_planners[kind])
                planner = recorder
                flight.work_active = True
        cruise = (
            flight.cruise_altitude_m
            if flight.cruise_altitude_m is not None
            else self.cfg.get("cruise_altitudes", {}).get(
                read(ctx, flight.vehicle, "lowalt.vehicle_class")
            )
        )
        from aeroagentsim.algorithms.routes import (
            NoFlyRouteUnavailable,
            containing_no_fly,
            plan_with_egress,
            route_avoids_no_fly,
        )

        target = obligations[0] if obligations else goal
        altitude = (
            None
            if recorder is not None
            or cruise is None
            or distance(start, target) <= self.radius
            else cruise
        )
        try:
            points = plan_with_egress(planner, state, start, target, altitude)
            if obligations:
                # The reconnecting path is disposable, the original routine is
                # not. Never jump straight to the final destination on replan.
                points += obligations[1:]
                if points[-1] != goal:
                    points += (goal,)
                if not route_avoids_no_fly(
                    state,
                    start,
                    points,
                    initial_exit=bool(containing_no_fly(state, start)),
                ):
                    raise NoFlyRouteUnavailable(
                        "remaining original work route is blocked"
                    )
        except ValueError as exc:
            if not isinstance(exc, NoFlyRouteUnavailable) and recorder is None:
                raise
            # Keep the original mission/charging goal and retry after a zone
            # change. This stopped flight waits; no arrival/completion is faked.
            flight.goal, flight.route = goal, ()
            flight.egress_waypoint = None
            flight.route_blocked_reason = str(exc)
            flight.work_active = work_active_before
            flight.replan = True
            return False
        if not points or distance(points[-1], goal) > self.radius:
            raise ValueError("path planner must reach requested goal")
        if recorder is not None:
            assert flight.job is not None
            original = recorder.points
            while original and original[0] == recorder.start:
                original = original[1:]
            flight.job.work_obligations = original
        flight.egress_waypoint = points[0] if containing_no_fly(state, start) else None
        flight.work_reconnect_goal = obligations[0] if obligations else None
        flight.route_blocked_reason = None
        flight.goal, flight.route = goal, tuple(points)
        flight.replan = False
        return True

    def _assign(self, ctx: EngineContext, assignments: tuple[Assignment, ...]) -> None:
        used_tasks: set[str] = set()
        used_vehicles: set[str] = set()
        vehicles = (
            {v.id: v for v in self._snapshot(ctx).vehicles} if assignments else {}
        )
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
            state = vehicles[proposal.vehicle]
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
            if action == "altitude" and flight.purpose == "charge":
                # Avoidance must resume the physical charging trip, not mark
                # the aircraft idle and leave the reserve recall open forever.
                flight.avoidance_resume_purpose = "charge"
                flight.avoidance_resume_goal = flight.goal
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
            if action == "altitude":
                # Keep the assigned separation layer on subsequent transit
                # legs, rather than immediately route back into the conflict.
                flight.cruise_altitude_m = (
                    max(target[2], flight.cruise_altitude_m)
                    if flight.cruise_altitude_m is not None
                    else target[2]
                )
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
        self.snapshot_ctx, self.snapshot_state = None, None
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
                            if flight.waypoint == flight.egress_waypoint:
                                flight.egress_waypoint = None
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
        flight.transit_resume = False
        obligations = flight.job.work_obligations
        if (
            not flight.waypoint_is_work
            or not obligations
            or flight.waypoint != obligations[0]
        ):
            return
        assert flight.waypoint_start is not None
        point = vec(read(ctx, flight.vehicle, "lowalt.position"))
        if distance(point, obligations[0]) > self.radius:
            return
        # Called only on a matching succeeded native command; reconnect/egress
        # points cannot consume original work or manufacture observations.
        flight.job.work_obligations = obligations[1:]
        if flight.waypoint == flight.work_reconnect_goal:
            flight.work_reconnect_goal = None
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
                # Arrival at an original asset waypoint is real, but its new
                # reconnection leg must not inflate surveyed/applied distance.
                payload["distance_m"] = 0.0 if flight.waypoint_is_connector else moved
            ctx.emit(schema, payload, topic="lowalt.events")

    def _operator(
        self, ctx: EngineContext, schema: str, payload: dict[str, Any]
    ) -> None:
        if schema == "lowalt.operator.restaurant_delay":
            restaurant = self.by_id[payload["restaurant"]]
            if not self.food_enabled or not self.build.registry.is_a(
                restaurant.type_id, "lowalt:Restaurant"
            ):
                raise ValueError(
                    "restaurant delay requires an authored food restaurant"
                )
            delay_s = finite(payload["delay_s"], "restaurant delay", 0.001)
            affected = []
            for job in self.jobs.values():
                if (
                    job.phase == "preparing"
                    and read(ctx, job.ref, "lowalt.task_origin") == restaurant.id
                ):
                    assert job.ready_ns is not None
                    job.ready_ns += round(delay_s * 1e9)
                    ctx.set(job.ref, "lowalt.food_expected_ready_ns", job.ready_ns)
                    affected.append(job.ref.id)
            ctx.emit(
                "lowalt.food.preparation_delayed",
                {"restaurant": restaurant.id, "delay_s": delay_s, "tasks": affected},
                topic="lowalt.events",
            )
        elif schema == "lowalt.operator.close_vertiport":
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
        elif schema in {
            "lowalt.operator.add_urgent_order",
            "lowalt.operator.investigate",
        }:
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
        else:
            raise ValueError(f"unsupported lowalt operator event: {schema}")

    def _prepare_food(self, ctx: EngineContext) -> None:
        """Kitchen slots prepare actual food orders before allocation is eligible."""
        for job in self.jobs.values():
            if job.phase != "preparing":
                continue
            assert job.ready_ns is not None and job.preparation_started_ns is not None
            if ctx.now.ns >= job.ready_ns:
                self._state(ctx, job, "queued")
                ctx.set(job.ref, "lowalt.food_ready_ns", ctx.now.ns)
                ctx.emit(
                    "lowalt.food.ready",
                    {
                        "task": job.ref.id,
                        "restaurant": read(ctx, job.ref, "lowalt.task_origin"),
                        "preparation_s": (ctx.now.ns - job.preparation_started_ns)
                        / 1e9,
                    },
                    topic="lowalt.events",
                )
        busy: dict[str, int] = {}
        for job in self.jobs.values():
            if job.phase == "preparing":
                restaurant = read(ctx, job.ref, "lowalt.task_origin")
                busy[restaurant] = busy.get(restaurant, 0) + 1
        for job in sorted(self.jobs.values(), key=lambda j: (j.created_ns, j.ref.id)):
            if job.phase != "awaiting_preparation":
                continue
            # A newly released order must commit before its kitchen transition.
            if read(ctx, job.ref, "lowalt.task_state") != "awaiting_preparation":
                continue
            restaurant = self.by_id[read(ctx, job.ref, "lowalt.task_origin")]
            if not read(ctx, restaurant, "lowalt.site_open"):
                continue
            if busy.get(restaurant.id, 0) >= read(
                ctx, restaurant, "lowalt.site_capacity"
            ):
                continue
            preparation_s = finite(
                read(ctx, restaurant, "lowalt.food_preparation_s"),
                "food preparation",
                0.001,
            )
            job.preparation_started_ns = ctx.now.ns
            job.ready_ns = ctx.now.ns + round(preparation_s * 1e9)
            self._state(ctx, job, "preparing")
            ctx.set(job.ref, "lowalt.food_preparation_started_ns", ctx.now.ns)
            ctx.set(job.ref, "lowalt.food_expected_ready_ns", job.ready_ns)
            busy[restaurant.id] = busy.get(restaurant.id, 0) + 1
            ctx.emit(
                "lowalt.food.preparation_started",
                {
                    "task": job.ref.id,
                    "restaurant": restaurant.id,
                    "ready_ns": job.ready_ns,
                },
                topic="lowalt.events",
            )

    def _poll(self, ctx: EngineContext) -> None:
        for job in self.jobs.values():
            if (
                job.phase == "unreleased"
                and job.release_ns is not None
                and ctx.now.ns >= job.release_ns
            ):
                job.created_ns = job.release_ns
                ctx.set(job.ref, "lowalt.task_created_ns", job.created_ns)
                self._state(
                    ctx, job, "awaiting_preparation" if job.food_window_ns else "queued"
                )
                if job.food_window_ns is not None:
                    ctx.set(
                        job.ref,
                        "lowalt.food_deadline_ns",
                        job.created_ns + job.food_window_ns,
                    )
                self._event(
                    ctx,
                    "created",
                    task=job.ref.id,
                    kind=read(ctx, job.ref, "lowalt.task_kind"),
                    priority=read(ctx, job.ref, "lowalt.task_priority"),
                    origin=read(ctx, job.ref, "lowalt.task_origin"),
                    destination=read(ctx, job.ref, "lowalt.task_destination"),
                )
        if self.food_enabled:
            self._prepare_food(ctx)
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
                    if (
                        flight.purpose == "avoidance"
                        and flight.avoidance_resume_purpose == "charge"
                    ):
                        flight.purpose = "charge"
                        flight.goal = flight.avoidance_resume_goal
                        flight.route = ()
                        flight.avoidance_resume_goal = None
                        flight.avoidance_resume_purpose = "task"
                        flight.replan = True
                    else:
                        flight.purpose = "task"
                        flight.transit_resume = bool(flight.saved_route)
                        flight.goal, flight.route = (
                            flight.saved_goal,
                            flight.saved_route,
                        )
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
                if flight.replan or (
                    not flight.route
                    and (flight.goal is None or bool(job.work_obligations))
                ):
                    if not self._route(ctx, flight, goal):
                        continue
                if (
                    not flight.route
                    and not job.work_obligations
                    and distance(
                        vec(read(ctx, flight.vehicle, "lowalt.position")), goal
                    )
                    <= self.radius
                    and flight.status == "succeeded"
                ):
                    if job.phase == "started":
                        self._state(ctx, job, "picked_up")
                        if job.food_window_ns is not None:
                            ctx.set(job.ref, "lowalt.food_picked_up_ns", ctx.now.ns)
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
                        if kind == "food":
                            deadline = read(ctx, job.ref, "lowalt.food_deadline_ns")
                            ready = read(ctx, job.ref, "lowalt.food_ready_ns")
                            ctx.set(job.ref, "lowalt.food_delivered_ns", ctx.now.ns)
                            ctx.emit(
                                "lowalt.food.delivered",
                                {
                                    "task": job.ref.id,
                                    "vehicle": flight.vehicle.id,
                                    "destination": read(
                                        ctx, job.ref, "lowalt.task_destination"
                                    ),
                                    "deadline_ns": deadline,
                                    "ready_to_delivery_s": (ctx.now.ns - ready) / 1e9,
                                    "on_time": ctx.now.ns <= deadline,
                                },
                                topic="lowalt.events",
                            )
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
                if not self._route(ctx, flight, flight.goal):
                    continue
            if flight.route:
                # Replan a remaining segment if a dynamic zone activated.
                goal = flight.route[0]
                from aeroagentsim.algorithms.routes import route_avoids_no_fly

                state = self._snapshot(ctx)
                exemptions = self.cfg.get("zone_exemptions", {})
                state = replace(
                    state,
                    zones=tuple(
                        z
                        for z in state.zones
                        if flight.vehicle.id not in exemptions.get(z.id, ())
                    ),
                )
                start = vec(read(ctx, flight.vehicle, "lowalt.position"))
                if not route_avoids_no_fly(
                    state, start, (goal,), initial_exit=goal == flight.egress_waypoint
                ):
                    assert flight.goal is not None
                    if not self._route(ctx, flight, flight.goal):
                        continue
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
        state = self._snapshot(ctx)
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
                    and not read(ctx, self.by_id[v.id], "lowalt.returning_to_charge")
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

        state = self._with_vehicle_model(ctx, state)
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
            elif self._batch_due(ctx):
                self._assign(ctx, self.allocator.allocate(state))

    def _batch_due(self, ctx: EngineContext) -> bool:
        """True at the first poll of each batch window (always without a window)."""
        if self.batch_ns <= 0:
            return True
        slot = ctx.now.ns // self.batch_ns
        if slot <= self.last_batch_slot:
            return False
        self.last_batch_slot = slot
        return True

    def _with_vehicle_model(self, ctx: EngineContext, state: Snapshot) -> Snapshot:
        """Attach speed, energy rate and battery capacity for cost estimates."""
        model = self.vehicle_model
        return replace(
            state,
            vehicles=tuple(
                replace(
                    v,
                    speed_m_s=finite(
                        model.get("speed_m_s", v.speed_m_s), "speed", 1e-9
                    ),
                    energy_per_m_j=finite(
                        model.get("energy_per_m_j", v.energy_per_m_j), "energy rate", 0
                    ),
                    capacity_j=finite(
                        read(ctx, self.by_id[v.id], "lowalt.capacity_j"), "capacity", 0
                    ),
                )
                for v in state.vehicles
            ),
        )


def build(context: EngineBuild) -> LowaltTasks:
    return LowaltTasks(context)
