"""Committed geofences, scheduled activations, separation and wind controls."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from itertools import product
from typing import Any, cast

from aerokernel import Dependency, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import Conflict, ConflictResolution, Point
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.common import finite, ns, read, vec
from aeroagentsim.packs.geometry import segment_hits_box
from aeroagentsim.platform.plugins import EngineBuild

from .core import SNAPSHOT_FIELDS, refs, snapshot

PRODUCES = (
    "lowalt.membership",
    "lowalt.conflicts",
    "lowalt.weather_held",
    "lowalt.zone_active",
    "lowalt.zone_lower",
    "lowalt.zone_upper",
)
EVENTS = (
    "lowalt.airspace.conflict",
    "lowalt.airspace.conflict_resolved",
    "lowalt.airspace.geofence_entered",
    "lowalt.airspace.zone_activated",
    "lowalt.airspace.zone_deactivated",
    "lowalt.weather.hold",
    "lowalt.weather.release",
    "lowalt.control",
)


def separation_conflicts(
    positions: dict[str, Point], horizontal_m: float, vertical_m: float
) -> tuple[Conflict, ...]:
    """Only inspect neighboring 3D hash cells; emit each unordered pair once."""
    buckets: dict[tuple[int, int, int], list[str]] = {}
    result = []
    for identity, point in sorted(positions.items()):
        cell = (
            math.floor(point[0] / horizontal_m),
            math.floor(point[1] / horizontal_m),
            math.floor(point[2] / vertical_m),
        )
        for delta in product((-1, 0, 1), repeat=3):
            neighbor = (cell[0] + delta[0], cell[1] + delta[1], cell[2] + delta[2])
            for other in buckets.get(neighbor, ()):
                q = positions[other]
                h, v = (
                    math.hypot(point[0] - q[0], point[1] - q[1]),
                    abs(point[2] - q[2]),
                )
                if h < horizontal_m and v < vertical_m:
                    result.append(Conflict(other, identity, h, v))
        buckets.setdefault(cell, []).append(identity)
    return tuple(sorted(result, key=lambda c: (c.a, c.b)))


class LowaltAirspace(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        self.tick = ns(self.cfg["tick_ns"], "airspace tick", 1)
        self.horizontal = finite(
            self.cfg["separation_horizontal_m"], "horizontal separation", 1e-9
        )
        self.vertical = finite(
            self.cfg["separation_vertical_m"], "vertical separation", 1e-9
        )
        self.wind_limits = {
            k: finite(v, "wind limit", 0) for k, v in self.cfg["wind_limits"].items()
        }
        self.resolver = cast(
            ConflictResolution,
            load_algorithm(
                "conflict_resolution",
                self.cfg["algorithms"]["conflict_resolution"],
                self.cfg.get("algorithm_config", {}).get("conflict_resolution"),
            ),
        )
        self.fleet = refs(build, "lowalt:UAV")
        self.zones = {r.id: r for r in refs(build, "lowalt:AirspaceZone")}
        self.activation_events = self.cfg["activation_events"]
        for item in self.activation_events:
            if (
                set(item) != {"schema", "topic", "zone", "active"}
                or item["zone"] not in self.zones
                or type(item["active"]) is not bool
            ):
                raise ValueError("activation_events require schema/topic/zone/active")
            if build.registry.message(item["schema"]).kind != "event":
                raise ValueError("zone activation source must be a registered event")
        self.override: dict[str, bool] = {}
        self.conflicts: set[tuple[str, str]] = set()
        self.actions: dict[tuple[str, str], str] = {}
        self.previous: dict[str, Point] = {}
        self.external = {
            item["id"]: item for item in self.cfg.get("external_vehicles", ())
        }
        self.by_id = {r.id: r for r in build.entities}
        consumed = tuple(
            dict.fromkeys(
                SNAPSHOT_FIELDS
                + PRODUCES
                + (
                    "lowalt.vehicle_class",
                    "lowalt.zone_start_ns",
                    "lowalt.zone_end_ns",
                    "lowalt.zone_dynamic",
                    *[item["position_field"] for item in self.external.values()],
                    *[
                        item["field"]
                        for item in self.cfg.get("dynamic_zone_centers", {}).values()
                    ],
                )
            )
        )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=PRODUCES,
                consumes=tuple(Dependency(f) for f in consumed),
                commands=("lowalt.airspace.activate",),
                emits=EVENTS
                + tuple(
                    sorted(
                        {
                            item["motion"][key]
                            for item in self.external.values()
                            for key in ("move_to", "hold")
                        }
                    )
                ),
                subscribes=tuple(
                    sorted(
                        {"lowalt.events"} | {i["topic"] for i in self.activation_events}
                    )
                ),
                message_targets=("lowalt.events",)
                + tuple(
                    sorted(
                        {item["motion"]["target"] for item in self.external.values()}
                    )
                ),
                lifecycle=True,
            ),
            policies=policies(PRODUCES),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        initial = copy.deepcopy(self.build.initial)
        for ref in self.fleet:
            self.previous[ref.id] = vec(initial[ref.id]["lowalt.position"])
            vehicle_class = initial[ref.id]["lowalt.vehicle_class"]
            if vehicle_class not in self.wind_limits:
                raise ValueError(
                    f"missing wind limit for vehicle class {vehicle_class}"
                )
        for ref in self.zones.values():
            facts = initial[ref.id]
            start = ns(facts["lowalt.zone_start_ns"], "zone start")
            end = ns(facts["lowalt.zone_end_ns"], "zone end", 1)
            if end <= start:
                raise ValueError("zone interval must be nonempty")
            lower, upper = (
                vec(facts["lowalt.zone_lower"]),
                vec(facts["lowalt.zone_upper"]),
            )
            if any(a >= b for a, b in zip(lower, upper)):
                raise ValueError("zone lower bounds must precede upper bounds")
            facts["lowalt.zone_active"] = (
                ref.id not in self.cfg.get("event_only_zones", ())
                and start <= ctx.now.ns < end
            )
            for boundary in (start, end):
                if boundary > ctx.now.ns:
                    ctx.wake_at(boundary, {"lowalt_airspace": "boundary"})
        bootstrap_owned(ctx, replace(self.build, initial=initial))
        ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_airspace": "tick"})

    def _emit(self, ctx: EngineContext, schema: str, payload: dict[str, Any]) -> None:
        ctx.emit(schema, payload, topic="lowalt.events")

    def _zones(
        self, ctx: EngineContext, overrides: dict[str, tuple[bool, str]]
    ) -> dict[str, bool]:
        active = {}
        for identity, ref in self.zones.items():
            before = read(ctx, ref, "lowalt.zone_active")
            if identity in overrides:
                value, reason = overrides[identity]
                if not read(ctx, ref, "lowalt.zone_dynamic"):
                    raise ValueError("event activation requires a dynamic zone")
                self.override[identity] = value
                if value and identity in self.cfg.get("dynamic_zone_centers", {}):
                    authored = self.cfg["dynamic_zone_centers"][identity]
                    center = vec(
                        read(ctx, self.by_id[authored["entity"]], authored["field"])
                    )
                    extent = vec(authored["dimensions_m"])
                    ctx.set(
                        ref,
                        "lowalt.zone_lower",
                        [center[0] - extent[0] / 2, center[1] - extent[1] / 2, center[2]],
                    )
                    ctx.set(
                        ref,
                        "lowalt.zone_upper",
                        [
                            center[0] + extent[0] / 2,
                            center[1] + extent[1] / 2,
                            center[2] + extent[2],
                        ],
                    )
            else:
                start = read(ctx, ref, "lowalt.zone_start_ns")
                end = read(ctx, ref, "lowalt.zone_end_ns")
                value = (
                    self.override[identity]
                    if identity in self.override and ctx.now.ns < end
                    else identity not in self.cfg.get("event_only_zones", ())
                    and start <= ctx.now.ns < end
                )
                reason = "active interval"
            active[identity] = value
            if value != before:
                ctx.set(ref, "lowalt.zone_active", value)
                self._emit(
                    ctx,
                    "lowalt.airspace.zone_activated"
                    if value
                    else "lowalt.airspace.zone_deactivated",
                    {"zone": identity, "reason": reason},
                )
        return active

    def _poll(self, ctx: EngineContext, overrides: dict[str, tuple[bool, str]]) -> None:
        active = self._zones(ctx, overrides)
        state = snapshot(ctx, self.build)
        state = replace(
            state, zones=tuple(replace(z, active=active[z.id]) for z in state.zones)
        )
        # External responders retain their own sole motion/battery owners. These
        # vehicles are monitored but never enter the lowalt allocation pool.
        positions = {v.id: v.position for v in state.vehicles}
        positions.update(
            {
                identity: vec(read(ctx, self.by_id[identity], item["position_field"]))
                for identity, item in self.external.items()
            }
        )
        wind_speed = math.sqrt(sum(x * x for x in state.wind))
        for ref in self.fleet:
            point = positions[ref.id]
            exemptions = self.cfg.get("zone_exemptions", {})
            members = sorted(
                z.id
                for z in state.zones
                if z.active
                and ref.id not in exemptions.get(z.id, ())
                and all(a <= p <= b for a, p, b in zip(z.lower, point, z.upper))
            )
            before = read(ctx, ref, "lowalt.membership")
            if members != before:
                ctx.set(ref, "lowalt.membership", members)
            for zone in state.zones:
                if (
                    zone.active
                    and ref.id not in exemptions.get(zone.id, ())
                    and zone.kind in {"no_fly", "restricted"}
                    and zone.id not in before
                    and segment_hits_box(
                        self.previous[ref.id], point, zone.lower, zone.upper
                    )
                ):
                    self._emit(
                        ctx,
                        "lowalt.airspace.geofence_entered",
                        {"vehicle": ref.id, "zone": zone.id},
                    )
            self.previous[ref.id] = point
            held = wind_speed > self.wind_limits[read(ctx, ref, "lowalt.vehicle_class")]
            if held != read(ctx, ref, "lowalt.weather_held"):
                ctx.set(ref, "lowalt.weather_held", held)
                self._emit(
                    ctx,
                    "lowalt.weather.hold" if held else "lowalt.weather.release",
                    {
                        "vehicle": ref.id,
                        "reason": "wind exceeds vehicle limit"
                        if held
                        else "wind within vehicle limit",
                        "wind_mps": wind_speed,
                    },
                )
                self._emit(
                    ctx,
                    "lowalt.control",
                    {
                        "vehicle": ref.id,
                        "action": "hold" if held else "release",
                        "reason": "weather",
                    },
                )
        conflicts = separation_conflicts(positions, self.horizontal, self.vertical)
        pairs = {(c.a, c.b) for c in conflicts}
        new = tuple(c for c in conflicts if (c.a, c.b) not in self.conflicts)
        for conflict in new:
            self._emit(
                ctx,
                "lowalt.airspace.conflict",
                {
                    "a": conflict.a,
                    "b": conflict.b,
                    "horizontal_m": conflict.horizontal_m,
                    "vertical_m": conflict.vertical_m,
                },
            )
        for a, b in sorted(self.conflicts - pairs):
            self._emit(
                ctx,
                "lowalt.airspace.conflict_resolved",
                {
                    "a": a,
                    "b": b,
                    "action": self.actions.pop((a, b), "observed separation"),
                },
            )
        for ref in self.fleet:
            members = sorted(
                b if a == ref.id else a for a, b in pairs if ref.id in {a, b}
            )
            if members != read(ctx, ref, "lowalt.conflicts"):
                ctx.set(ref, "lowalt.conflicts", members)
        if new:
            monitored = tuple(
                (identity, positions[identity]) for identity in self.external
            )
            controlled_conflicts = tuple(
                c
                for c in conflicts
                if c.a not in self.external or c.b not in self.external
            )
            resolutions = self.resolver.resolve(
                replace(state, monitored_positions=monitored), controlled_conflicts
            )
            action_by_vehicle = {}
            for resolution in resolutions:
                if resolution.vehicle not in positions:
                    raise ValueError("conflict resolution selected an unknown vehicle")
                action_by_vehicle[resolution.vehicle] = resolution.action
                payload: dict[str, Any] = {
                    "vehicle": resolution.vehicle,
                    "action": resolution.action,
                    "reason": "separation conflict",
                }
                if (
                    resolution.action == "altitude"
                    and resolution.altitude_m is not None
                ):
                    p = positions[resolution.vehicle]
                    payload["target"] = [
                        p[0],
                        p[1],
                        finite(resolution.altitude_m, "resolution altitude"),
                    ]
                elif resolution.action == "hold":
                    payload["until_ns"] = ctx.now.ns + math.ceil(
                        finite(resolution.hold_s, "resolution hold", 1e-9) * 1e9
                    )
                else:
                    raise ValueError("conflict resolver must propose altitude or hold")
                if resolution.vehicle in self.external:
                    # The traffic behaviour owns its responding UAV commands;
                    # move lowalt aircraft around that protected responder.
                    # Resolver proposals for external aircraft are not executed.
                    continue
                self._emit(ctx, "lowalt.control", payload)
            for conflict in new:
                self.actions[(conflict.a, conflict.b)] = (
                    ", ".join(
                        sorted(
                            {
                                action_by_vehicle[i]
                                for i in (conflict.a, conflict.b)
                                if i in action_by_vehicle
                            }
                        )
                    )
                    or "observed separation"
                )
        self.conflicts = pairs

    def on_inputs(self, ctx: EngineContext) -> None:
        overrides: dict[str, tuple[bool, str]] = {}
        commands = []
        for delivery in ctx.inbox:
            data = cast(dict[str, Any], thaw(delivery.message.payload))
            if delivery.message.kind == "command":
                command = ctx.remember(
                    delivery, lambda value: cast(dict[str, Any], value)
                )
                identity = data["zone"]
                if identity not in self.zones or not read(
                    ctx, self.zones[identity], "lowalt.zone_dynamic"
                ):
                    ctx.reject(command)
                else:
                    ctx.accept(command)
                    overrides[identity] = (data["active"], data["reason"])
                    commands.append(command)
            else:
                for binding in self.activation_events:
                    if delivery.message.schema_id == binding["schema"]:
                        overrides[binding["zone"]] = (
                            binding["active"],
                            "event " + binding["schema"],
                        )
        timers = [thaw(d.payload) for d in ctx.dirty if d.kind == "timer"]
        if overrides or any(
            isinstance(t, dict) and "lowalt_airspace" in t for t in timers
        ):
            self._poll(ctx, overrides)
        for command in commands:
            ctx.execute(command)
            ctx.succeed(
                command,
                {"zone": command.payload["zone"], "active": command.payload["active"]},
            )
        if {"lowalt_airspace": "tick"} in timers:
            ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_airspace": "tick"})


def build(context: EngineBuild) -> LowaltAirspace:
    return LowaltAirspace(context)
