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
from .overlay import COOP_OPTIONS, HANDOFF_FIELDS, INCIDENT_FIELDS, TASK_KINDS

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


class OperatorRejected(ValueError):
    """An admitted event whose requested business operation is ineligible."""


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
    # Actual operator message handles, retained across timer/receipt callbacks.
    # These explain business records; they never participate in allocation.
    operator_messages: tuple[str, ...] = ()
    last_message: str | None = None
    last_event_key: str | None = None
    spray_hold_until_ns: int | None = None


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
    charging_goal: Point | None = None
    avoidance_resume_goal: tuple[float, float, float] | None = None
    avoidance_resume_purpose: str = "task"
    control_resume_purpose: str = "task"
    egress_waypoint: tuple[float, float, float] | None = None
    route_blocked_reason: str | None = None


@dataclass
class Incident:
    ref: EntityRef
    config: dict[str, Any]
    phase: str = "pending"
    responder: str | None = None
    choice: str | None = None
    dwell_until_ns: int | None = None
    job: Job | None = None
    helper: str | None = None
    point: str | None = None
    handoff_phase: str = "idle"
    candidates: tuple[str, ...] = ()
    offers: dict[str, bool | None] | None = None
    sources: tuple[str, ...] = ()
    last_event_key: str | None = None


class LowaltTasks(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        preemption = self.cfg.get("priority_preemption", {})
        if not isinstance(preemption, dict) or any(
            kind not in TASK_KINDS
            or not isinstance(interruptible, list)
            or any(target not in TASK_KINDS for target in interruptible)
            for kind, interruptible in preemption.items()
        ):
            raise ValueError(
                "priority_preemption: map task kinds to interruptible kind lists"
            )
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
        self.business_event_sequence = 0
        self.pad_waits: set[tuple[str, str]] = set()
        self.flights = {r.id: Flight(r) for r in refs(build, "lowalt:UAV")}
        self.jobs: dict[str, Job] = {}
        self.by_id = {r.id: r for r in build.entities}
        from .cooperation import EVENTS, validate_config, validate_vehicle_task_kinds

        validate_vehicle_task_kinds(self.cfg, vehicles=set(self.flights))
        self.cooperation = self.cfg.get("cooperation")
        self.incidents: dict[str, Incident] = {}
        self.coop_pending: set[tuple[str, str, str]] = set()
        self.coop_decisions: dict[str, tuple[str, str, str]] = {}
        self.coop_steps: set[tuple[str, str]] = set()
        self.coop_resumed: set[str] = set()
        if self.cooperation is not None:
            validate_config(self.cooperation, vehicles=set(self.flights))
            self.incidents = {
                row["id"]: Incident(self.by_id[row["id"]], row)
                for row in self.cooperation["incidents"]
            }
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
        restaurant_type = any(type_row.id == "lowalt:Restaurant" for type_row in build.registry.types)
        self.food_enabled = any(g["kind"] == "food" for g in self.cfg["generators"]) or (restaurant_type and bool(refs(build, "lowalt:Restaurant")))
        self.radius = finite(self.cfg["arrival_radius_m"], "arrival radius", 0)
        # Reuse only the committed base snapshot within ONE engine callback.
        # Lazy reads preserve the causal scope of earlier receipts/releases.
        self.snapshot_ctx: EngineContext | None = None
        self.snapshot_state: Snapshot | None = None
        produced = TASK_FIELDS + CONSTANT_FIELDS + ("lowalt.payload_kg",)
        if self.cooperation is not None:
            produced += INCIDENT_FIELDS + HANDOFF_FIELDS
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
                        + (INCIDENT_FIELDS + HANDOFF_FIELDS if self.cooperation is not None else ())
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
                    "lowalt.parcel.dropoff_changed",
                    "lowalt.medical.prioritized",
                    "lowalt.inspection.defect_reported",
                    "lowalt.spraying.paused",
                    "lowalt.spraying.resumed",
                    "lowalt.operator.rejected",
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
                ) + (
                    tuple(EVENTS)
                    + ("lowalt.coop.decision_request",)
                    if self.cooperation is not None else ()
                ),
                subscribes=("lowalt.events", "lowalt.allocations")
                + (
                    ("langgraph-records",)
                    if self.algorithms["allocation"] == "llm" or (self.cooperation is not None and "provider" in self.cooperation)
                    else ()
                ) + (("lowalt.cooperation",) if self.cooperation is not None else ()),
                message_targets=(
                    "lowalt.events",
                    "lowalt.allocations",
                    self.motion["target"],
                ) + (("lowalt.cooperation",) if self.cooperation is not None else ()),
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
        for incident in self.incidents.values():
            at = round(incident.config["at_s"] * 1e9)
            if at == ctx.now.ns:
                ctx.ops.append(ScheduleTimer(
                    "lowalt/cooperation/" + incident.ref.id,
                    Instant(at, ctx.now.microstep + 1),
                    {"lowalt_cooperation": incident.ref.id}, tuple(ctx.inputs),
                ))
            else:
                ctx.wake_at(at, {"lowalt_cooperation": incident.ref.id})
            if "end_s" in incident.config:
                end = round(incident.config["end_s"] * 1e9)
                if end == ctx.now.ns:
                    ctx.ops.append(ScheduleTimer(
                        "lowalt/cooperation/" + incident.ref.id + "/end",
                        Instant(end, ctx.now.microstep + 1),
                        {"lowalt_cooperation_end": incident.ref.id}, tuple(ctx.inputs),
                    ))
                else:
                    ctx.wake_at(end, {"lowalt_cooperation_end": incident.ref.id})

    def _event(self, ctx: EngineContext, phase: str, **payload: Any) -> None:
        self._business_event(ctx, "lowalt.task." + phase, payload)

    def _business_event(
        self, ctx: EngineContext, schema: str, payload: dict[str, Any],
        *, sources: tuple[str, ...] = (),
    ) -> str | None:
        job = self.jobs.get(payload.get("task", ""))
        incident = self.incidents.get(payload.get("incident", ""))
        links = list(sources)
        if incident is not None:
            links.extend(incident.sources)
        if job is not None and job.operator_messages:
            links.extend(job.operator_messages)
            if job.last_message is not None:
                links.append(job.last_message)
        if links:
            # The kernel assigns message handles after the callback commits.
            # A recorded domain event key links successive emissions within
            # that callback as well as later receipts/ticks, without predicting
            # kernel IDs or influencing business decisions.
            self.business_event_sequence += 1
            event_key = f"{self.build.id}/{links[0]}/{self.business_event_sequence}"
            provenance = {"message_ids": list(dict.fromkeys(links)), "event_key": event_key}
            if job is not None and job.last_event_key is not None:
                provenance["parent_event_keys"] = [job.last_event_key]
            if incident is not None and incident.last_event_key is not None:
                provenance.setdefault("parent_event_keys", []).append(incident.last_event_key)
            payload = {**payload, "provenance": provenance}
            if job is not None:
                job.last_event_key = event_key
            if incident is not None:
                incident.last_event_key = event_key
        else:
            event_key = None
        ctx.emit(schema, payload, topic="lowalt.events")
        return event_key

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
            if action == "altitude" and (
                read(ctx, flight.vehicle, "lowalt.charging")
                or read(ctx, flight.vehicle, "lowalt.charging_waiting")
            ):
                # A control already in transit may arrive after connection or
                # queuing. Separation moves the airborne partner instead.
                return
            if action == "altitude" and flight.purpose == "charge":
                # Avoidance must resume the physical charging trip, not mark
                # the aircraft idle and leave the reserve recall open forever.
                flight.avoidance_resume_purpose = "charge"
                flight.avoidance_resume_goal = flight.goal
            if flight.purpose in {"task", "incident_response", "handoff_drop", "handoff_pickup"}:
                flight.control_resume_purpose = flight.purpose
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
            if action == "return_to_pad":
                flight.charging_goal = target
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

    def _restore_control(self, flight: Flight) -> None:
        flight.purpose = flight.control_resume_purpose
        flight.control_resume_purpose = "task"
        flight.transit_resume = bool(flight.saved_route)
        flight.goal, flight.route = flight.saved_goal, flight.saved_route
        flight.saved_goal, flight.saved_route = None, ()
        flight.replan = not bool(flight.route)

    def _charging_finished(self, ctx: EngineContext, identity: str) -> None:
        flight = self.flights[identity]
        if flight.charging_goal is None:
            raise ValueError("charging finished without a recalled flight")
        flight.charging_goal = None
        # Completion ends charging even if an altitude detour retained its
        # destination, and even if the next energy sample has already drained
        # the battery below the target. Only the energy owner completes it.
        resume = flight.purpose == "charge" or (
            flight.purpose == "avoidance"
            and flight.avoidance_resume_purpose == "charge"
        )
        flight.avoidance_resume_goal = None
        flight.avoidance_resume_purpose = "task"
        if resume:
            self._restore_control(flight)
            if flight.pending or flight.command_id is not None:
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
        self.coop_resumed.clear()
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
                            if flight.purpose in {"incident_response", "handoff_drop", "handoff_pickup"}:
                                flight.replan = True
                                flight.route = ()
                            elif flight.job is not None:
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
                            if not flight.replan:
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
            if self.cooperation is not None:
                if delivery.message.source == self.build.id and payload.get("incident") in self.incidents:
                    incident = self.incidents[payload["incident"]]
                    incident.sources = (delivery.message.id,)
                if self._coop_input(ctx, delivery.message.schema_id, payload, delivery.message.id):
                    continue
            if delivery.message.source == self.build.id and "task" in payload:
                job = self.jobs.get(payload["task"])
                if job is not None and job.operator_messages:
                    job.last_message = delivery.message.id
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
                if delivery.message.schema_id == "lowalt.operator.rejected":
                    continue
                try:
                    self._operator(ctx, delivery.message.schema_id, payload, delivery.message.id)
                except OperatorRejected as problem:
                    self._business_event(ctx, "lowalt.operator.rejected", {
                        "operation": delivery.message.schema_id, "reason": str(problem),
                    }, sources=(delivery.message.id,))
            elif delivery.message.schema_id == "lowalt.control":
                self._control(ctx, payload)
            elif delivery.message.schema_id == "lowalt.energy.charging_finished":
                self._charging_finished(ctx, payload["vehicle"])
            elif delivery.message.schema_id == "lowalt.allocation.result":
                self.llm_pending = False
                rows = json.loads(payload["assignments_json"])
                self._assign(
                    ctx, tuple(Assignment(row["task"], row["vehicle"]) for row in rows)
                )
        timers = [thaw(d.payload) for d in ctx.dirty if d.kind == "timer"]
        for payload in timers:
            if "lowalt_cooperation" in payload:
                incident = self.incidents[payload["lowalt_cooperation"]]
                if incident.phase == "pending":
                    self._coop_state(ctx, incident, "occurred")
                    ctx.set(incident.ref, "lowalt.incident_active", True)
                    self._business_event(ctx, "lowalt.incident.occurred", {
                        "incident": incident.ref.id, "kind": incident.config["kind"],
                        "position": incident.config["position"],
                    })
            if "lowalt_cooperation_end" in payload:
                incident = self.incidents[payload["lowalt_cooperation_end"]]
                # An end wake racing the activation timer (or a pending dwell)
                # must never leave the incident active afterwards.
                if incident.phase == "pending":
                    incident.dwell_until_ns = None
                if read(ctx, incident.ref, "lowalt.incident_active"):
                    ctx.set(incident.ref, "lowalt.incident_active", False)
                    self._business_event(ctx, "lowalt.incident.ended", {
                        "incident": incident.ref.id, "kind": incident.config["kind"],
                        "position": incident.config["position"],
                    })
            if "lowalt_coop_step" in payload:
                incident = self.incidents[payload["lowalt_coop_step"]]
                step = payload["step"]
                self.coop_steps.discard((incident.ref.id, step))
                if step == "decide":
                    self._coop_decide(ctx, incident)
                elif step == "select":
                    self._coop_select(ctx, incident)
                elif step == "start_helper":
                    self._coop_start_helper(ctx, incident)
                elif step == "response_due":
                    assert incident.responder is not None
                    flight = self.flights[incident.responder]
                    if (
                        flight.purpose == "incident_response"
                        and not flight.pending and flight.command_id is None
                        and not read(ctx, flight.vehicle, "lowalt.weather_held")
                        and not read(ctx, flight.vehicle, "lowalt.charging")
                        and ctx.now.ns >= flight.hold_until_ns
                        and all(abs(v) <= 1e-6 for v in vec(read(ctx, flight.vehicle, "lowalt.velocity")))
                    ):
                        self._coop_flight(ctx, flight)
        if any(
            payload in ({"lowalt_tasks": "tick"}, {"lowalt_tasks": "release"})
            for payload in timers
        ):
            self._poll(ctx)
        if {"lowalt_tasks": "tick"} in timers:
            ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_tasks": "tick"})

    def _eligible(self, ctx: EngineContext, job: Job, flight: Flight) -> bool:
        overrides = self.cfg.get("vehicle_task_kinds", {})
        if flight.vehicle.id in overrides:
            return read(ctx, job.ref, "lowalt.task_kind") in overrides[flight.vehicle.id]
        if "vehicle_kinds" not in self.cfg:
            return True
        kind = read(ctx, job.ref, "lowalt.task_kind")
        vehicle_class = read(ctx, flight.vehicle, "lowalt.vehicle_class")
        return kind in self.cfg["vehicle_kinds"][vehicle_class]

    def _vehicle_task_kinds(self, ctx: EngineContext, flight: Flight) -> tuple[str, ...]:
        overrides = self.cfg.get("vehicle_task_kinds", {})
        if flight.vehicle.id in overrides:
            return tuple(overrides[flight.vehicle.id])
        if "vehicle_kinds" in self.cfg:
            return tuple(self.cfg["vehicle_kinds"][read(ctx, flight.vehicle, "lowalt.vehicle_class")])
        return ()

    def _coop_state(self, ctx: EngineContext, incident: Incident, phase: str) -> None:
        incident.phase = phase
        ctx.set(incident.ref, "lowalt.incident_state", phase)

    def _coop_defer(self, ctx: EngineContext, incident: Incident, step: str, *, at_ns: int | None = None) -> None:
        key = (incident.ref.id, step)
        if key in self.coop_steps:
            return
        self.coop_steps.add(key)
        payload = {"lowalt_coop_step": incident.ref.id, "step": step}
        if at_ns is not None and at_ns > ctx.now.ns:
            ctx.wake_at(at_ns, payload)
        else:
            # State transitions commit separately: the kernel permits only one
            # write to each fact key in a wave.
            ctx.ops.append(ScheduleTimer(
                # A step can recur later in the run (e.g. a helper starts after its own task);
                # the instant keeps each timer id unique and deterministic.
                f"lowalt/cooperation/{incident.ref.id}/{step}/{ctx.now.ns}.{ctx.now.microstep}",
                Instant(ctx.now.ns, ctx.now.microstep + 1), payload, tuple(ctx.inputs),
            ))

    def _handoff_state(self, ctx: EngineContext, incident: Incident, phase: str) -> None:
        incident.handoff_phase = phase
        ctx.set(self.by_id["handoff-" + incident.ref.id], "lowalt.handoff_state", phase)

    def _handoff_payload(self, incident: Incident) -> dict[str, Any]:
        assert incident.job is not None and incident.responder is not None
        return {"incident": incident.ref.id, "handoff": "handoff-" + incident.ref.id,
                "task": incident.job.ref.id, "requester": incident.responder}

    def _coop_reserved(self, vehicle: str) -> bool:
        return any(
            (incident.responder == vehicle and incident.phase not in {"photographed", "reported", "decision_failed"})
            or (incident.helper == vehicle and incident.handoff_phase == "accepted")
            or (incident.helper == vehicle and incident.handoff_phase == "dropped")
            or (vehicle in incident.candidates and incident.handoff_phase == "requested")
            for incident in self.incidents.values()
        )

    def _coop_distance(self, ctx: EngineContext, vehicle: str, position: Any, *, horizontal: bool = False) -> float:
        point = vec(read(ctx, self.by_id[vehicle], "lowalt.position"))
        if not horizontal:
            return distance(point, vec(position))
        return ((point[0] - position[0]) ** 2 + (point[1] - position[1]) ** 2) ** 0.5

    def _coop_options(self, ctx: EngineContext, incident: Incident) -> tuple[list[str], dict[str, str]]:
        assert self.cooperation is not None and incident.responder is not None
        flight = self.flights[incident.responder]
        rules = self.cooperation["responder"]["rules"]
        job = flight.job
        blocked: dict[str, str] = {}
        if job is not None:
            kind = read(ctx, job.ref, "lowalt.task_kind")
            if kind in rules["protected_task_kinds"]:
                for option in ("respond_now", "request_handoff"):
                    blocked[option] = f"Protected task kind {kind} / 受保护的任务类别 {kind}"
            elif flight.suspended is not None:
                for option in ("respond_now", "request_handoff"):
                    blocked[option] = "A suspended task is already carried / 已有暂停任务"
            elif rules["require_replacement"]:
                blocked["respond_now"] = "Replacement required before interrupting this task / 中断任务前需要接替者"
            if job.phase not in {"assigned", "started", "picked_up"}:
                blocked["request_handoff"] = "Task is not transferable in its current state / 当前任务状态不允许交接"
        else:
            blocked["request_handoff"] = "No current task to transfer / 当前没有可交接任务"
            blocked["finish_then_respond"] = "No current task to finish / 当前没有待完成任务"
        if not self.cooperation["handoff_points"] and job is not None and job.phase == "picked_up":
            blocked["request_handoff"] = "No handoff point is configured / 未配置交接点"
        if incident.handoff_phase in {"declined", "failed"}:
            blocked["request_handoff"] = "The handoff ended without a helper / 交接已结束，没有协助者接替"
        if flight.purpose != "task" or read(ctx, flight.vehicle, "lowalt.charging") or read(ctx, flight.vehicle, "lowalt.weather_held") or read(ctx, flight.vehicle, "lowalt.returning_to_charge"):
            for option in COOP_OPTIONS[:-1]:
                blocked.setdefault(option, "Vehicle is under flight or charging control / 无人机正在接受飞行或充电控制")
        battery = read(ctx, flight.vehicle, "lowalt.soc") * 100
        reason = None
        if battery < rules["min_battery_pct"]:
            reason = f"Battery {battery:.1f}% below minimum {rules['min_battery_pct']}% / 电量低于最低要求"
        elif self._coop_distance(ctx, flight.vehicle.id, incident.config["position"]) > rules["max_response_distance_m"]:
            reason = "Incident beyond maximum response distance / 事件超出最大响应距离"
        if reason is not None:
            for option in COOP_OPTIONS[:-1]:
                blocked[option] = reason + ("; " + blocked[option] if option in blocked else "")
        return [option for option in COOP_OPTIONS if option not in blocked], blocked

    def _coop_ordered(self, allowed: list[str], *, after_handoff: bool = False) -> str:
        assert self.cooperation is not None
        order = self.cooperation["responder"]["order"]
        if after_handoff:
            order = order[order.index("request_handoff") + 1:] if "request_handoff" in order else order
            order = [option for option in order if option != "request_handoff"]
        return next((option for option in order if option in allowed), "report_only")

    def _coop_request(self, ctx: EngineContext, incident: Incident, vehicle: str, role: str, allowed: list[str], constraints: list[str]) -> None:
        assert self.cooperation is not None
        flight = self.flights[vehicle]
        job = flight.job
        state = {
            "vehicle": vehicle,
            "current_task": job.ref.id if job is not None else None,
            "task_kind": read(ctx, job.ref, "lowalt.task_kind") if job is not None else None,
            "task_state": job.phase if job is not None else None,
            "payload_kg": read(ctx, flight.vehicle, "lowalt.payload_kg"),
            "payload_capacity_kg": read(ctx, flight.vehicle, "lowalt.payload_capacity_kg"),
            "battery_pct": read(ctx, flight.vehicle, "lowalt.soc") * 100,
            "charging": read(ctx, flight.vehicle, "lowalt.charging"),
            "distance_to_incident_m": self._coop_distance(ctx, vehicle, incident.config["position"]),
            "distance_to_requester_m": self._coop_distance(ctx, vehicle, vec(read(ctx, self.by_id[incident.responder], "lowalt.position"))),
            "handoff_state": incident.handoff_phase,
        }
        if incident.job is not None:
            state["requested_task"] = incident.job.ref.id
            state["requested_payload_kg"] = read(ctx, incident.job.ref, "lowalt.task_payload_kg")
        self.coop_pending.add((incident.ref.id, vehicle, role))
        ctx.emit("lowalt.coop.decision_request", {
            "incident": incident.ref.id, "vehicle": vehicle, "role": role,
            "goal": self.cooperation["responder" if role == "responder" else "helpers"]["goal"],
            "state_json": json.dumps(state, ensure_ascii=False, allow_nan=False),
            "allowed": allowed, "constraints": constraints,
        }, topic="lowalt.cooperation")

    def _coop_input(self, ctx: EngineContext, schema: str, payload: dict[str, Any], source: str) -> bool:
        if schema == "lowalt.coop.decision_result":
            key = (payload["incident"], payload["vehicle"], payload["role"])
            if key not in self.coop_pending:
                return True
            self.coop_pending.remove(key)
            self.coop_decisions = {identity: request for identity, request in self.coop_decisions.items() if request != key}
            incident = self.incidents[key[0]]
            incident.sources = (source,)
            if key[2] == "responder":
                self._coop_decide(ctx, incident, payload["choice"], payload["reason"])
            else:
                self._coop_offer(ctx, incident, key[1], payload["choice"], payload["reason"])
            return True
        if schema == "aas.langgraph.record" and payload["decision_id"].startswith("lowalt_cooperation/"):
            if payload["phase"] == "observation":
                request = json.loads(payload["data_json"])["initial"]["trigger"]["payload"]
                key = (request["incident"], request["vehicle"], request["role"])
                if key in self.coop_pending:
                    self.coop_decisions[payload["decision_id"]] = key
            elif payload["phase"] == "failure":
                key = self.coop_decisions.pop(payload["decision_id"], None)
                if key is not None and key in self.coop_pending:
                    failure = json.loads(payload["data_json"])
                    self.coop_pending.remove(key)
                    incident = self.incidents[key[0]]
                    incident.sources = (source,)
                    self._coop_failed(ctx, incident, key[1], key[2], f"Model failure / 模型失败: {failure['code']}: {failure['message']}")
            return True
        return False

    def _coop_failed(self, ctx: EngineContext, incident: Incident, vehicle: str, role: str, reason: str) -> None:
        self._business_event(ctx, "lowalt.incident.decision_failed", {
            "incident": incident.ref.id, "vehicle": vehicle, "role": role, "mode": "agent", "reason": reason,
        })
        if role == "responder":
            incident.choice = None
            self._coop_state(ctx, incident, "decision_failed")
        else:
            assert incident.offers is not None
            incident.offers[vehicle] = None
            if len(incident.offers) == len(incident.candidates):
                self._coop_defer(ctx, incident, "select")

    def _coop_tick(self, ctx: EngineContext) -> None:
        assert self.cooperation is not None
        for flight in self.flights.values():
            self._coop_followup(ctx, flight)
        for incident in self.incidents.values():
            if incident.phase != "occurred" or incident.responder is not None or read(ctx, incident.ref, "lowalt.incident_state") != "occurred":
                continue
            for vehicle in self.cooperation["watchers"]["vehicles"]:
                if self._coop_reserved(vehicle):
                    continue
                gap = self._coop_distance(ctx, vehicle, incident.config["position"], horizontal=True)
                if gap > self.cooperation["watchers"]["notice_radius_m"]:
                    continue
                incident.responder = vehicle
                self._coop_state(ctx, incident, "noticed")
                ctx.set(incident.ref, "lowalt.incident_noticed_by", vehicle)
                ctx.set(incident.ref, "lowalt.incident_responder", vehicle)
                self._business_event(ctx, "lowalt.incident.noticed", {"incident": incident.ref.id, "vehicle": vehicle, "distance_m": gap})
                if self.cooperation["responder"]["mode"] == "agent":
                    self._coop_decide(ctx, incident)
                else:
                    self._coop_defer(ctx, incident, "decide")
                break

    def _coop_decide(self, ctx: EngineContext, incident: Incident, choice: str | None = None, reason: str | None = None, *, after_handoff: bool = False) -> None:
        assert self.cooperation is not None and incident.responder is not None
        allowed, blocked = self._coop_options(ctx, incident)
        mode = self.cooperation["responder"]["mode"]
        if mode == "agent" and choice is None:
            incident.choice = None
            rules = self.cooperation["responder"]["rules"]
            descriptions = {
                "respond_now": "Respond now; suspend and later resume any interruptible task / 立即响应；暂停并随后恢复可中断任务",
                "request_handoff": "Ask eligible helpers to take over the current task / 请求合格协助者接管当前任务",
                "finish_then_respond": "Complete the current task before responding / 完成当前任务后响应",
                "report_only": "Report this incident without flying to it / 仅上报事件",
            }
            constraints = [
                f"Responding requires at least {rules['min_battery_pct']}% battery and distance at most {rules['max_response_distance_m']} m / 响应要求达到最低电量且不超过最大距离",
                f"Protected task kinds cannot be interrupted or transferred: {rules['protected_task_kinds']} / 受保护任务类别不能中断或交接",
                "A helper must accept before you leave your delivery / 离开配送前必须有协助者接受" if rules["require_replacement"] else "An interruptible delivery may be suspended and resumed after responding / 可中断配送允许在响应后恢复",
            ]
            self._coop_request(ctx, incident, incident.responder, "responder", allowed, [descriptions[v] for v in allowed] + constraints + [f"{v}: {why}" for v, why in blocked.items()])
            return
        if after_handoff:
            choice = self._coop_ordered(allowed, after_handoff=True)
            reason = "No helper accepted; taking the next allowed action / 无协助者接受；采用下一允许选项"
        elif choice is None:
            choice = self._coop_ordered(allowed)
            reason = "First allowed action in the prescribed order / 按预设顺序采用首个允许选项"
        elif choice not in allowed:
            rejected = blocked.get(choice, "Invalid model choice / 无效模型选项")
            self._coop_failed(ctx, incident, incident.responder, "responder", f"{reason}; {rejected}")
            return
        incident.choice = choice
        self._business_event(ctx, "lowalt.incident.decision", {
            "incident": incident.ref.id, "vehicle": incident.responder, "mode": mode,
            "choice": choice, "reason": reason, "allowed": allowed, "blocked": blocked,
        })
        if choice == "report_only":
            self._coop_report(ctx, incident)
        elif choice == "respond_now":
            self._coop_begin_response(ctx, incident)
        elif choice == "request_handoff":
            self._coop_handoff(ctx, incident)

    def _coop_report(self, ctx: EngineContext, incident: Incident) -> None:
        self._coop_state(ctx, incident, "reported")
        self._business_event(ctx, "lowalt.incident.reported", {"incident": incident.ref.id, "vehicle": incident.responder})

    def _coop_begin_response(self, ctx: EngineContext, incident: Incident) -> None:
        assert incident.responder is not None
        flight = self.flights[incident.responder]
        if flight.job is not None:
            if flight.suspended is not None:
                raise ValueError("cooperation: cannot suspend a second task / 不能暂停第二个任务")
            flight.suspended, flight.suspended_phase = flight.job, flight.job.phase
            flight.job.operator_messages += incident.sources
            self._state(ctx, flight.job, "paused")
            self._event(ctx, "preempted", task=flight.job.ref.id, vehicle=flight.vehicle.id, urgent_task=incident.ref.id)
            flight.job = None
        flight.purpose, flight.route, flight.goal = "incident_response", (), None
        flight.paused_route = None
        flight.replan, flight.work_active = True, False
        self._brake(ctx, flight)

    def _coop_candidate(self, ctx: EngineContext, incident: Incident, identity: str) -> str | None:
        assert self.cooperation is not None and incident.job is not None and incident.responder is not None
        flight = self.flights[identity]
        if identity == incident.responder:
            return "Requester cannot take its own handoff / 请求者不能接替自身"
        if any(other is not incident and (
            other.responder == identity and other.phase not in {"photographed", "reported", "decision_failed"}
            or other.helper == identity and other.handoff_phase in {"accepted", "dropped"}
            or identity in other.candidates and other.handoff_phase == "requested"
        ) for other in self.incidents.values()):
            return "Vehicle already reserved for cooperation / 无人机已被其他协作占用"
        gap = self._coop_distance(ctx, identity, vec(read(ctx, self.by_id[incident.responder], "lowalt.position")))
        if gap > self.cooperation["helpers"]["ask_radius_m"]:
            return "Outside helper request radius / 超出协助请求范围"
        if read(ctx, flight.vehicle, "lowalt.soc") * 100 < self.cooperation["helpers"]["rules"]["min_battery_pct"]:
            return "Helper battery below minimum / 协助者电量低于最低要求"
        payload = read(ctx, incident.job.ref, "lowalt.task_payload_kg")
        if flight.job is None:
            payload += read(ctx, flight.vehicle, "lowalt.payload_kg")
        if payload > read(ctx, flight.vehicle, "lowalt.payload_capacity_kg"):
            return "Helper payload capacity insufficient / 协助者载荷能力不足"
        if flight.suspended is not None:
            return "Helper has a suspended task / 协助者已有暂停任务"
        return None

    def _coop_handoff(self, ctx: EngineContext, incident: Incident) -> None:
        assert self.cooperation is not None and incident.responder is not None
        incident.job = self.flights[incident.responder].job
        assert incident.job is not None
        incident.job.operator_messages += incident.sources
        incident.candidates = tuple(identity for identity in self.cooperation["helpers"]["vehicles"] if self._coop_candidate(ctx, incident, identity) is None)
        incident.offers = {}
        self._handoff_state(ctx, incident, "requested")
        ref = self.by_id["handoff-" + incident.ref.id]
        ctx.set(ref, "lowalt.handoff_task", incident.job.ref.id)
        ctx.set(ref, "lowalt.handoff_from", incident.responder)
        self._business_event(ctx, "lowalt.handoff.requested", {**self._handoff_payload(incident), "candidates": list(incident.candidates)})
        if not incident.candidates:
            self._coop_defer(ctx, incident, "select")
        for helper in incident.candidates:
            if self.cooperation["helpers"]["mode"] == "agent":
                self._coop_request(ctx, incident, helper, "helper", ["accept", "decline"], [
                    "accept: commit to taking over this task after your current task / 接受：承诺完成当前任务后接管请求任务",
                    "decline: keep your current schedule / 拒绝：保持当前安排",
                    "Payload capacity and minimum battery are enforced; task-kind restrictions do not block explicitly listed helpers / 引擎强制执行载荷和最低电量约束；名单中的协助者不受任务类别限制",
                    f"Minimum helper battery is {self.cooperation['helpers']['rules']['min_battery_pct']}% / 协助者最低电量要求",
                ])
            else:
                self._coop_offer(ctx, incident, helper)

    def _coop_offer(self, ctx: EngineContext, incident: Incident, helper: str, choice: str | None = None, reason: str | None = None) -> None:
        assert self.cooperation is not None and incident.offers is not None
        if helper in incident.offers or incident.handoff_phase != "requested":
            return
        mode = self.cooperation["helpers"]["mode"]
        if mode == "prescribed":
            flight = self.flights[helper]
            idle = flight.job is None and flight.purpose == "task" and not read(ctx, flight.vehicle, "lowalt.charging") and not read(ctx, flight.vehicle, "lowalt.returning_to_charge")
            accepted = self.cooperation["helpers"]["rules"]["accept_when"] == "always" or idle
            reason = "Prescribed helper accepts / 预设规则接受" if accepted else "Helper busy or charging / 协助者忙碌或正在充电"
        elif choice not in {"accept", "decline"}:
            self._coop_failed(ctx, incident, helper, "helper", f"{reason}; Invalid model choice / 无效模型选项")
            return
        else:
            accepted = choice == "accept"
        blocked = self._coop_candidate(ctx, incident, helper)
        if accepted and blocked is not None:
            if mode == "agent":
                self._coop_failed(ctx, incident, helper, "helper", "Offer re-check / 接受条件复核: " + blocked)
                return
            accepted, reason = False, "Offer re-check / 接受条件复核: " + blocked
        incident.offers[helper] = accepted
        self._business_event(ctx, "lowalt.handoff.offer", {**self._handoff_payload(incident), "helper": helper, "accepted": accepted, "reason": reason, "mode": mode})
        if len(incident.offers) == len(incident.candidates):
            self._coop_defer(ctx, incident, "select")

    def _coop_select(self, ctx: EngineContext, incident: Incident) -> None:
        assert self.cooperation is not None and incident.offers is not None and incident.responder is not None and incident.job is not None
        requester = self.flights[incident.responder]
        allowed, blocked = self._coop_options(ctx, incident)
        accepted = [identity for identity in incident.candidates if incident.offers[identity] is True and self._coop_candidate(ctx, incident, identity) is None]
        reason = None
        if requester.job is not incident.job or "request_handoff" not in allowed:
            reason = "Requester state changed before handoff acceptance / 接受交接前请求者状态已改变: " + blocked.get("request_handoff", "task changed / 任务已改变")
        elif not accepted:
            reason = "No eligible helper accepted / 没有合格协助者接受"
        points = [point for point in self.cooperation["handoff_points"] if read(ctx, self.by_id[point["id"]], "lowalt.site_open") and read(ctx, self.by_id[point["id"]], "lowalt.site_capacity") > 0]
        if incident.job.phase == "picked_up" and not points:
            reason = "No open handoff point / 没有开放交接点"
        if reason is not None:
            phase = "failed" if any(offer is None for offer in incident.offers.values()) else "declined"
            if phase == "failed":
                reason += "; Helper decision failed / 协助者决策失败"
            self._handoff_state(ctx, incident, phase)
            self._business_event(ctx, "lowalt.handoff." + phase, {**self._handoff_payload(incident), "reason": reason})
            self._coop_decide(ctx, incident, after_handoff=True)
            return
        requester_position = vec(read(ctx, requester.vehicle, "lowalt.position"))
        helper = accepted[0] if self.cooperation["helpers"]["selection"] == "first" else min(accepted, key=lambda identity: (self._coop_distance(ctx, identity, requester_position), identity))
        incident.helper = helper
        incident.point = min(points, key=lambda point: (distance(requester_position, vec(point["position"])), point["id"]))["id"] if incident.job.phase == "picked_up" else None
        ref = self.by_id["handoff-" + incident.ref.id]
        ctx.set(ref, "lowalt.handoff_to", helper)
        ctx.set(ref, "lowalt.handoff_point", incident.point or "")
        self._handoff_state(ctx, incident, "accepted")
        self._business_event(ctx, "lowalt.handoff.accepted", {**self._handoff_payload(incident), "helper": helper, "point": incident.point, "others": [identity for identity in accepted if identity != helper]})
        requester.paused_route = None
        requester.route, requester.goal, requester.replan = (), None, True
        if incident.point is None:
            requester.job = None
            self._state(ctx, incident.job, "paused")
            incident.job.vehicle = helper
            ctx.set(incident.job.ref, "lowalt.task_vehicle", helper)
            self._coop_begin_response(ctx, incident)
        else:
            requester.purpose, requester.work_active = "handoff_drop", False
            self._brake(ctx, requester)
        self._coop_defer(ctx, incident, "start_helper")

    def _coop_start_helper(self, ctx: EngineContext, incident: Incident) -> None:
        assert incident.helper is not None and incident.job is not None
        flight = self.flights[incident.helper]
        if flight.job is not None or flight.purpose != "task" or flight.pending or flight.command_id is not None or read(ctx, flight.vehicle, "lowalt.charging") or read(ctx, flight.vehicle, "lowalt.returning_to_charge"):
            return
        if incident.point is None:
            job = incident.job
            flight.job, job.vehicle = job, flight.vehicle.id
            self._state(ctx, job, "assigned")
            ctx.set(job.ref, "lowalt.task_vehicle", flight.vehicle.id)
            self._event(ctx, "assigned", task=job.ref.id, vehicle=flight.vehicle.id, algorithm="cooperation")
            self._handoff_state(ctx, incident, "transferred")
            self._business_event(ctx, "lowalt.handoff.transferred", {**self._handoff_payload(incident), "helper": flight.vehicle.id, "point": None})
            origin = self.by_id[read(ctx, job.ref, "lowalt.task_origin")]
            self._route(ctx, flight, vec(read(ctx, origin, "lowalt.site_position")))
        else:
            flight.purpose, flight.route, flight.goal = "handoff_pickup", (), None
            flight.replan = True

    def _coop_followup(self, ctx: EngineContext, flight: Flight) -> None:
        if flight.job is not None or flight.purpose != "task":
            return
        for incident in self.incidents.values():
            if incident.helper == flight.vehicle.id and incident.handoff_phase in {"accepted", "dropped"}:
                if not flight.pending and flight.command_id is None:
                    self._coop_defer(ctx, incident, "start_helper")
                return
            if incident.responder == flight.vehicle.id and incident.phase == "noticed" and incident.choice == "finish_then_respond":
                assert self.cooperation is not None
                rules = self.cooperation["responder"]["rules"]
                if read(ctx, flight.vehicle, "lowalt.soc") * 100 < rules["min_battery_pct"] or self._coop_distance(ctx, flight.vehicle.id, incident.config["position"]) > rules["max_response_distance_m"]:
                    if self.cooperation["responder"]["mode"] == "agent":
                        self._coop_decide(ctx, incident)
                    else:
                        self._coop_decide(ctx, incident, "report_only", "Response constraints no longer permit flight / 当前约束已不允许响应飞行")
                elif not read(ctx, flight.vehicle, "lowalt.charging") and not read(ctx, flight.vehicle, "lowalt.returning_to_charge"):
                    self._coop_begin_response(ctx, incident)
                return

    def _coop_flight(self, ctx: EngineContext, flight: Flight) -> bool:
        if flight.purpose not in {"incident_response", "handoff_drop", "handoff_pickup"}:
            return False
        incident = next(item for item in self.incidents.values() if (
            item.helper == flight.vehicle.id if flight.purpose == "handoff_pickup" else item.responder == flight.vehicle.id
        ) and (item.handoff_phase in {"accepted", "dropped"} if flight.purpose.startswith("handoff_") else item.phase in {"noticed", "responding"}))
        assert self.cooperation is not None
        if flight.purpose == "incident_response":
            position = incident.config["position"]
            goal = (float(position[0]), float(position[1]), float(self.cooperation["photograph"]["altitude_m"]))
        else:
            assert incident.point is not None
            goal = vec(read(ctx, self.by_id[incident.point], "lowalt.site_position"))
        current = vec(read(ctx, flight.vehicle, "lowalt.position"))
        arrived = not flight.route and flight.goal == goal and flight.status == "succeeded" and distance(current, goal) <= self.radius
        if arrived:
            if flight.purpose == "incident_response":
                if incident.dwell_until_ns is None:
                    incident.dwell_until_ns = ctx.now.ns + round(self.cooperation["photograph"]["dwell_s"] * 1e9)
                    self._coop_state(ctx, incident, "responding")
                    self._business_event(ctx, "lowalt.incident.responding", {"incident": incident.ref.id, "vehicle": flight.vehicle.id, "position": list(current)})
                    self._coop_defer(ctx, incident, "response_due", at_ns=incident.dwell_until_ns)
                    return True
                if ctx.now.ns >= incident.dwell_until_ns:
                    self._coop_state(ctx, incident, "photographed")
                    self._business_event(ctx, "lowalt.incident.photographed", {"incident": incident.ref.id, "vehicle": flight.vehicle.id, "position": list(incident.config["position"])})
                    flight.purpose, flight.goal, flight.replan = "task", None, False
                    if flight.suspended is not None:
                        job = flight.suspended
                        assert flight.suspended_phase is not None
                        self._state(ctx, job, flight.suspended_phase)
                        flight.job = job
                        flight.suspended, flight.suspended_phase = None, None
                        flight.replan = True
                        self.coop_resumed.add(flight.vehicle.id)
                        self._event(ctx, "resumed", task=job.ref.id, vehicle=flight.vehicle.id)
                return True
            assert incident.job is not None and incident.helper is not None
            job = incident.job
            amount = read(ctx, job.ref, "lowalt.task_payload_kg")
            if flight.purpose == "handoff_drop":
                self._state(ctx, job, "awaiting_transfer")
                ctx.set(job.ref, "lowalt.task_origin", incident.point)
                ctx.set(flight.vehicle, "lowalt.payload_kg", read(ctx, flight.vehicle, "lowalt.payload_kg") - amount)
                self._handoff_state(ctx, incident, "dropped")
                self._business_event(ctx, "lowalt.handoff.dropped", {**self._handoff_payload(incident), "helper": incident.helper, "point": incident.point})
                flight.job = None
                self._coop_begin_response(ctx, incident)
                return True
            if incident.handoff_phase != "dropped" or read(ctx, job.ref, "lowalt.task_state") != "awaiting_transfer":
                return True
            job.vehicle, flight.job = flight.vehicle.id, job
            self._state(ctx, job, "picked_up")
            ctx.set(job.ref, "lowalt.task_vehicle", flight.vehicle.id)
            ctx.set(flight.vehicle, "lowalt.payload_kg", read(ctx, flight.vehicle, "lowalt.payload_kg") + amount)
            self._event(ctx, "assigned", task=job.ref.id, vehicle=flight.vehicle.id, algorithm="cooperation")
            self._event(ctx, "picked_up", task=job.ref.id, vehicle=flight.vehicle.id)
            self._handoff_state(ctx, incident, "transferred")
            self._business_event(ctx, "lowalt.handoff.transferred", {**self._handoff_payload(incident), "helper": flight.vehicle.id, "point": incident.point})
            flight.purpose, flight.route, flight.goal, flight.replan = "task", (), None, True
            return True
        if flight.purpose == "incident_response" and incident.dwell_until_ns is not None:
            incident.dwell_until_ns = None
        if flight.replan or flight.goal != goal:
            if not self._route(ctx, flight, goal):
                return True
        if flight.route:
            target = flight.route[0]
            from aeroagentsim.algorithms.routes import route_avoids_no_fly
            state = self._snapshot(ctx)
            state = replace(state, zones=tuple(z for z in state.zones if flight.vehicle.id not in self.cfg.get("zone_exemptions", {}).get(z.id, ())))
            if not route_avoids_no_fly(state, current, (target,), initial_exit=target == flight.egress_waypoint):
                if not self._route(ctx, flight, goal):
                    return True
                target = flight.route[0]
            flight.route = flight.route[1:]
            self._submit(ctx, flight, target)
        return True

    def _preempt(self, ctx: EngineContext) -> None:
        rules = self.cfg.get("priority_preemption", {})
        if self.cfg.get("medical_preemption", False):
            rules = {**rules, "medical": [*rules.get("medical", ()), "parcel"]}
        if not rules:
            return
        for urgent in sorted(self.jobs.values(), key=lambda j: j.ref.id):
            if (
                urgent.phase != "queued"
                or read(ctx, urgent.ref, "lowalt.task_state") != "queued"
            ):
                continue
            interruptible = rules.get(read(ctx, urgent.ref, "lowalt.task_kind"), ())
            if not interruptible:
                continue
            candidates = [
                f
                for f in self.flights.values()
                if f.job is not None
                and f.suspended is None
                and f.purpose == "task"
                and not self._coop_reserved(f.vehicle.id)
                and self._eligible(ctx, urgent, f)
                and read(ctx, f.job.ref, "lowalt.task_kind") in interruptible
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
            self._business_event(ctx, schema, payload)

    def _operator(
        self, ctx: EngineContext, schema: str, payload: dict[str, Any], source: str
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
                    job.operator_messages += (source,)
                    job.ready_ns += round(delay_s * 1e9)
                    ctx.set(job.ref, "lowalt.food_expected_ready_ns", job.ready_ns)
                    affected.append(job.ref.id)
            delayed_key = self._business_event(
                ctx,
                "lowalt.food.preparation_delayed",
                {"restaurant": restaurant.id, "delay_s": delay_s, "tasks": affected},
                sources=(source,),
            )
            for identity in affected:
                self.jobs[identity].last_event_key = delayed_key
        elif schema in {"lowalt.operator.close_vertiport", "lowalt.operator.close_air_taxi_port"}:
            identity = self._target(payload["site"], "lowalt:Vertiport").id if isinstance(payload["site"], dict) else payload["site"]
            site = self.by_id[identity]
            if not self.build.registry.is_a(site.type_id, "lowalt:Vertiport"):
                raise ValueError("operator must close an actual vertiport")
            if schema == "lowalt.operator.close_air_taxi_port" and not read(ctx, site, "lowalt.site_open"):
                raise OperatorRejected("vertiport is already closed")
            alternative = self.cfg["site_diversions"][identity]
            if not read(ctx, self.by_id[alternative], "lowalt.site_open"):
                if schema == "lowalt.operator.close_air_taxi_port":
                    raise OperatorRejected("diversion vertiport is closed")
                raise ValueError("diversion vertiport is closed")
            ctx.set(site, "lowalt.site_open", False)
            closed_key = self._business_event(ctx, "lowalt.site.closed", {"site": identity}, sources=(source,))
            for job in self.jobs.values():
                if job.phase in {"completed", "failed"}:
                    continue
                changed = False
                for endpoint in ("origin", "destination"):
                    if read(ctx, job.ref, "lowalt.task_" + endpoint) == identity:
                        ctx.set(job.ref, "lowalt.task_" + endpoint, alternative)
                        changed = True
                if changed:
                    job.operator_messages += (source,)
                    job.last_event_key = closed_key
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
            job.operator_messages += (source,)
            # Release next tick, after endpoint/kind facts commit.
        elif schema in {
            "lowalt.operator.change_dropoff", "lowalt.operator.prioritize_medical",
            "lowalt.operator.report_defect", "lowalt.operator.pause_spraying",
        }:
            task = self._target(payload["task"], "lowalt:Task")
            job = self.jobs[task.id]
            kind = read(ctx, task, "lowalt.task_kind")
            expected = {
                "lowalt.operator.change_dropoff": "parcel",
                "lowalt.operator.prioritize_medical": "medical",
                "lowalt.operator.report_defect": "inspection",
                "lowalt.operator.pause_spraying": "spraying",
            }[schema]
            if kind != expected or job.phase in {"completed", "failed"}:
                raise OperatorRejected(f"{schema} requires an unfinished {expected} task")
            if schema == "lowalt.operator.change_dropoff":
                destination = self._target(payload["destination"], "lowalt:Customer")
                previous = read(ctx, task, "lowalt.task_destination")
                if destination.id == previous:
                    raise OperatorRejected("new drop-off must differ from the current destination")
                ctx.set(task, "lowalt.task_destination", destination.id)
                job.operator_messages += (source,)
                self._business_event(ctx, "lowalt.parcel.dropoff_changed", {
                    "task": task.id, "previous": previous, "destination": destination.id,
                })
                if job.vehicle is not None and job.phase == "picked_up":
                    flight = self.flights[job.vehicle]
                    flight.route, flight.replan = (), True
                    self._brake(ctx, flight)
            elif schema == "lowalt.operator.prioritize_medical":
                priority = ns(payload["priority"], "medical priority", 101)
                if priority <= read(ctx, task, "lowalt.task_priority"):
                    raise OperatorRejected("urgent request must increase the task priority")
                ctx.set(task, "lowalt.task_priority", priority)
                if job.phase == "unreleased":
                    job.release_ns = ctx.now.ns
                job.operator_messages += (source,)
                self._business_event(ctx, "lowalt.medical.prioritized", {"task": task.id, "priority": priority})
            elif schema == "lowalt.operator.report_defect":
                site = self._target(payload["site"], "lowalt:Site")
                if job.phase != "unreleased" or site.id != read(ctx, task, "lowalt.task_destination"):
                    raise OperatorRejected("defect report requires an unused inspection task ending at the reported asset")
                ctx.set(task, "lowalt.task_priority", 100)
                job.release_ns = ctx.now.ns
                job.operator_messages += (source,)
                self._business_event(ctx, "lowalt.inspection.defect_reported", {"task": task.id, "site": site.id})
            else:
                duration = finite(payload["duration_s"], "spraying pause", 1.0)
                job.spray_hold_until_ns = ctx.now.ns + round(duration * 1e9)
                job.operator_messages += (source,)
                self._business_event(ctx, "lowalt.spraying.paused", {"task": task.id, "until_ns": job.spray_hold_until_ns})
                if job.vehicle is not None:
                    self._control(ctx, {"vehicle": job.vehicle, "action": "hold", "until_ns": job.spray_hold_until_ns})
        else:
            raise ValueError(f"unsupported lowalt operator event: {schema}")

    def _target(self, value: dict[str, Any], type_id: str) -> EntityRef:
        """Resolve the exact authored generation; no coercion or guessed targets."""
        target = EntityRef.from_data(value["$ref"])
        if self.by_id.get(target.id) != target or not self.build.registry.is_a(target.type_id, type_id):
            raise OperatorRejected(f"operator target must be an authored {type_id}")
        return target

    def _prepare_food(self, ctx: EngineContext) -> None:
        """Kitchen slots prepare actual food orders before allocation is eligible."""
        for job in self.jobs.values():
            if job.phase != "preparing":
                continue
            assert job.ready_ns is not None and job.preparation_started_ns is not None
            if ctx.now.ns >= job.ready_ns:
                self._state(ctx, job, "queued")
                ctx.set(job.ref, "lowalt.food_ready_ns", ctx.now.ns)
                self._business_event(
                    ctx,
                    "lowalt.food.ready",
                    {
                        "task": job.ref.id,
                        "restaurant": read(ctx, job.ref, "lowalt.task_origin"),
                        "preparation_s": (ctx.now.ns - job.preparation_started_ns)
                        / 1e9,
                    },
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
        if self.cooperation is not None:
            self._coop_tick(ctx)
        self._preempt(ctx)
        for flight in self.flights.values():
            if flight.vehicle.id in self.coop_resumed:
                continue
            if flight.pending or flight.command_id is not None:
                continue
            if flight.job is None and flight.purpose == "task":
                continue
            if flight.job is not None and flight.job.spray_hold_until_ns is not None:
                if ctx.now.ns < flight.job.spray_hold_until_ns:
                    continue
                self._business_event(ctx, "lowalt.spraying.resumed", {"task": flight.job.ref.id})
                flight.job.spray_hold_until_ns = None
            held = read(ctx, flight.vehicle, "lowalt.weather_held")
            charging = read(ctx, flight.vehicle, "lowalt.charging")
            if (
                held or charging or read(ctx, flight.vehicle, "lowalt.charging_waiting")
                or ctx.now.ns < flight.hold_until_ns
            ):
                continue
            if (
                distance(
                    vec(read(ctx, flight.vehicle, "lowalt.velocity")), (0.0, 0.0, 0.0)
                )
                > 1e-6
            ):
                self._submit(ctx, flight, None)
                continue
            if self.cooperation is not None and self._coop_flight(ctx, flight):
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
                    if flight.purpose == "charge":
                        # Arrival is not completion: wait for the energy
                        # owner's charging_finished event and command receipt.
                        continue
                    if (
                        flight.purpose == "avoidance"
                        and flight.avoidance_resume_purpose == "charge"
                        and flight.charging_goal is not None
                    ):
                        flight.purpose = "charge"
                        flight.goal = flight.charging_goal
                        flight.route = ()
                        flight.avoidance_resume_goal = None
                        flight.avoidance_resume_purpose = "task"
                        flight.replan = True
                    else:
                        self._restore_control(flight)
            if self.cooperation is not None and self._coop_flight(ctx, flight):
                continue
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
                            self._business_event(
                                ctx,
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
                        if self.cooperation is not None:
                            self._coop_followup(ctx, flight)
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
                    task_kinds=self._vehicle_task_kinds(ctx, self.flights[v.id]),
                    available=v.available
                    and not read(ctx, self.by_id[v.id], "lowalt.returning_to_charge")
                    and self.flights[v.id].job is None
                    and self.flights[v.id].purpose == "task"
                    and not self._coop_reserved(v.id)
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
