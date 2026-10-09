"""Deterministic route/occupancy field owner, replaceable at run creation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from aerokernel import EntityRef, Partition, Timing
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import Value

from aeroagentsim.engines.common import policies
from aeroagentsim.packs.common import finite, ns, vec
from aeroagentsim.platform.plugins import EngineBuild

from .geometry import Polyline, Pose, blockers

FIELDS = tuple(
    "traffic.road." + slot
    for slot in (
        "position_enu_m",
        "velocity_enu_mps",
        "attitude_xyzw",
        "speed_mps",
        "route_id",
        "lane_id",
        "progress_m",
        "blocked_by",
        "safe_gap",
    )
)


def payload(value: Value) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("road command requires a typed record")
    return value


@dataclass
class Vehicle:
    ref: EntityRef
    route_id: str
    progress: float
    cruise: float
    direction: int
    stop: float
    loop: bool
    pose: Pose
    speed: float = 0.0
    blocked: tuple[str, ...] = ()
    command: Command[dict[str, Any]] | None = None
    safe_gap: bool | None = None


class RoadMotion(ContextEngine):
    """Stable identity order, complete fleet occupancy, swept short steps.

    Safety uses committed starting poses plus accepted earlier proposals in
    sorted identity order. The physical veto applies even after a clear-gap
    command receipt. Stop is a kinematic instantaneous stop, not impact dynamics.
    Loop wrapping is a declared discontinuity; bypass admission scans its whole
    corridor at 0.8 m clearance, then occupancy is rechecked on every step.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        cfg = build.config
        if set(cfg) != {
            "routes",
            "actors",
            "step_ns",
            "clearance_m",
            "bypass_clearance_m",
            "bypass_routes",
            "commands",
        }:
            raise ValueError(
                "road_motion.config: routes/actors/step/clearances/commands required"
            )
        self.period = ns(cfg["step_ns"], "road step", 1)
        self.clearance = finite(cfg["clearance_m"], "clearance", 0)
        self.bypass_clearance = finite(
            cfg["bypass_clearance_m"], "bypass clearance", self.clearance
        )
        self.routes = {r["id"]: Polyline(r["points_enu_m"]) for r in cfg["routes"]}
        if len(self.routes) != len(cfg["routes"]):
            raise ValueError("duplicate road route")
        self.lanes = {
            r["id"]: r["native_lane_id"] if "native_lane_id" in r else r["id"]
            for r in cfg["routes"]
        }
        self.bypass_routes: dict[str, str] = dict(cfg["bypass_routes"])
        for route in self.bypass_routes.values():
            self.routes[route]
        self.schemas: dict[str, str] = cfg["commands"]
        if set(self.schemas) != {"follow", "stop", "bypass"}:
            raise ValueError("road_motion.commands: follow/stop/bypass required")
        self.fleet: dict[str, Vehicle] = {}
        refs = {r.id: r for r in build.entities}
        for row in cfg["actors"]:
            ref = refs[row["id"]]
            if set(build.owned_fields(ref)) != set(FIELDS):
                raise ValueError(
                    f"road ownership {ref.id}: coupled physical fields required"
                )
            direction = row["direction"]
            if type(direction) is not int or direction not in {-1, 1}:
                raise ValueError("road direction must be +1 or -1")
            if type(row["loop"]) is not bool:
                raise TypeError("road loop must be a Boolean")
            line = self.routes[row["route_id"]]
            progress = finite(row["progress_m"], "initial progress", 0)
            pose = line.at(progress)
            self.fleet[ref.id] = Vehicle(
                ref,
                row["route_id"],
                progress,
                finite(row["speed_mps"], "road speed", 0),
                direction,
                finite(row["stop_progress_m"], "stop progress", 0),
                row["loop"],
                Pose(pose.position, pose.heading + (math.pi if direction < 0 else 0)),
            )
            if math.dist(vec(build.initial[ref.id][FIELDS[0]]), pose.position) > 1e-6:
                raise ValueError(
                    f"road initial pose disagrees with route/progress: {ref.id}"
                )
            line.at(self.fleet[ref.id].stop)
        if len(self.fleet) != len(cfg["actors"]):
            raise ValueError("duplicate road actor")
        self.integrated_ns = 0
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=FIELDS,
                commands=tuple(self.schemas.values()),
                lifecycle=True,
                timing=Timing("fixed_step", self.period),
            ),
            policies=policies(FIELDS),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        poses = {identity: v.pose for identity, v in self.fleet.items()}
        for identity, v in sorted(self.fleet.items()):
            if (
                self.build.manifest.controller(
                    v.ref, self.build.registry, self.build.partitions
                )
                != self.build.id
            ):
                raise ValueError(f"road actor lifecycle owner mismatch: {identity}")
            ctx.create(v.ref)
            v.blocked = blockers(identity, v.pose, poses, self.clearance)
            v.safe_gap = (
                not self.corridor(identity, self.routes[self.bypass_routes[identity]])
                if identity in self.bypass_routes
                else not v.blocked
            )
            self.publish(ctx, v)

    def corridor(self, identity: str, line: Polyline) -> tuple[str, ...]:
        poses = {key: v.pose for key, v in self.fleet.items()}
        result: set[str] = set()
        count = math.ceil(line.length / 0.5)
        for i in range(count + 1):
            result.update(
                blockers(
                    identity,
                    line.at(line.length * i / count),
                    poses,
                    self.bypass_clearance,
                )
            )
        return tuple(sorted(result))

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, payload)
            subject = EntityRef.from_data(command.payload["actor"]["$ref"])
            v = self.fleet.get(subject.id)
            if v is None or v.ref != subject:
                ctx.reject(
                    command, {"reason": "actor outside configured road generation"}
                )
                continue
            schema = delivery.message.schema_id
            if schema == self.schemas["stop"]:
                if v.command is not None:
                    ctx.fail(v.command, {"reason": "stopped by explicit command"})
                v.command, v.speed, v.cruise = None, 0.0, 0.0
                ctx.accept(command)
                ctx.execute(command)
                self.publish(ctx, v)
                ctx.succeed(command, {"position": list(v.pose.position)})
                continue
            route_id = command.payload["route_id"]
            if route_id not in self.routes or v.command is not None:
                ctx.reject(
                    command,
                    {"reason": "missing route or actor has an executing command"},
                )
                continue
            line = self.routes[route_id]
            progress = finite(command.payload["progress_m"], "command progress", 0)
            stop = finite(command.payload["stop_progress_m"], "command stop", 0)
            goal = line.at(progress)
            line.at(stop)
            if math.dist(goal.position, v.pose.position) > 0.05:
                ctx.reject(
                    command, {"reason": "route start is disconnected from actual pose"}
                )
                continue
            if schema == self.schemas["bypass"]:
                blocked = self.corridor(v.ref.id, line)
                if blocked:
                    ctx.reject(
                        command,
                        {
                            "reason": "borrow corridor occupied",
                            "blocked_by": list(blocked),
                        },
                    )
                    continue
            elif schema != self.schemas["follow"]:
                raise ValueError("unregistered road command")
            v.route_id, v.progress, v.stop = route_id, progress, stop
            v.cruise = finite(command.payload["speed_mps"], "command speed", 0)
            if type(command.payload["loop"]) is not bool:
                raise TypeError("command loop must be Boolean")
            v.loop, v.command = command.payload["loop"], command
            ctx.accept(command)
            ctx.execute(command)

    def publish(self, ctx: EngineContext, v: Vehicle) -> None:
        if v.safe_gap is None:
            raise TypeError(
                f"road occupancy must be computed before publication: {v.ref.id}"
            )
        values = (
            list(v.pose.position),
            [
                v.speed * math.cos(v.pose.heading),
                v.speed * math.sin(v.pose.heading),
                0.0,
            ],
            v.pose.quaternion,
            v.speed,
            v.route_id,
            self.lanes[v.route_id],
            v.progress,
            [{"$ref": self.fleet[i].ref.to_data()} for i in v.blocked],
            v.safe_gap,
        )
        for field, value in zip(FIELDS, values):
            ctx.set(v.ref, field, value)

    def step(self, ctx: EngineContext) -> None:
        dt = (ctx.now.ns - self.integrated_ns) / 1e9
        self.integrated_ns = ctx.now.ns
        poses = {key: v.pose for key, v in self.fleet.items()}
        # Retain the committed physical evidence used by the complete fleet query.
        for v in self.fleet.values():
            ctx.get(v.ref, FIELDS[0])
            ctx.get(v.ref, FIELDS[2])
        for identity in sorted(self.fleet):
            v = self.fleet[identity]
            v.speed, v.blocked = 0.0, ()
            if v.command is not None:
                line = self.routes[v.route_id]
                desired = v.progress + v.direction * v.cruise * dt
                if v.loop:
                    desired %= line.length
                elif v.direction > 0:
                    desired = min(v.stop, desired)
                else:
                    desired = max(v.stop, desired)
                proposed = line.at(desired)
                proposed = Pose(
                    proposed.position,
                    proposed.heading + (math.pi if v.direction < 0 else 0),
                )
                # Sweep prevents tunneling at large steps; loops explicitly break continuity.
                travel = abs(desired - v.progress)
                sweep = (
                    max(1, math.ceil(travel / 0.5))
                    if not v.loop or travel < line.length / 2
                    else 1
                )
                found: set[str] = set()
                for i in range(1, sweep + 1):
                    s = v.progress + (desired - v.progress) * i / sweep
                    sample = line.at(s)
                    sample = Pose(
                        sample.position,
                        sample.heading + (math.pi if v.direction < 0 else 0),
                    )
                    found.update(blockers(identity, sample, poses, self.clearance))
                v.blocked = tuple(sorted(found))
                if not v.blocked:
                    v.speed = v.cruise if desired != v.progress else 0.0
                    v.progress, v.pose = desired, proposed
                    if not v.loop and abs(v.progress - v.stop) <= 1e-9:
                        v.speed = 0.0
                        ctx.succeed(v.command, {"position": list(v.pose.position)})
                        v.command = None
            poses[identity] = v.pose
        for identity, v in sorted(self.fleet.items()):
            v.safe_gap = (
                not self.corridor(identity, self.routes[self.bypass_routes[identity]])
                if identity in self.bypass_routes
                else not v.blocked
            )
            self.publish(ctx, v)


def build(context: EngineBuild) -> RoadMotion:
    return RoadMotion(context)
