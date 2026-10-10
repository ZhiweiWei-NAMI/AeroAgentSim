"""Committed geofences, scheduled activations, separation and wind controls."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from itertools import product
from typing import Any, cast

from aerokernel import Dependency, Partition
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import (
    Conflict,
    ConflictResolution,
    Point,
    Snapshot,
)
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.common import finite, ns, read, vec
from aeroagentsim.packs.geometry import segment_hits_box
from aeroagentsim.platform.plugins import EngineBuild

from .core import SNAPSHOT_FIELDS, refs, snapshot

PRODUCES = (
    "lowalt.membership",
    "lowalt.conflicts",
    "lowalt.wind_mps",
    "lowalt.breached_zones",
    "lowalt.conflict_partner",
    "lowalt.conflict_gap_m",
    "lowalt.zone_active",
    "lowalt.zone_lower",
    "lowalt.zone_upper",
)
EVENTS = (
    "lowalt.airspace.zone_activated",
    "lowalt.airspace.zone_deactivated",
    "lowalt.control",
)
RESOLVE_COMMAND = "lowalt.airspace.resolve_conflict"
CONTROL_COMMAND = "lowalt.airspace.flight_control"
# Facts this engine publishes for the shared rules; absent from authored
# entities, they start at their "nothing detected" value.
DETECTION_DEFAULTS: dict[str, Any] = {
    "lowalt.wind_mps": 0.0,
    "lowalt.breached_zones": [],
    "lowalt.conflict_partner": "",
    "lowalt.conflict_gap_m": [0.0, 0.0],
}


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
        self.resolver = cast(
            ConflictResolution,
            load_algorithm(
                "conflict_resolution",
                self.cfg["algorithms"]["conflict_resolution"],
                self.cfg.get("algorithm_config", {}).get("conflict_resolution"),
            ),
        )
        self.fleet = refs(build, "lowalt:UAV")
        self.by_fleet = {r.id for r in self.fleet}
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
        # Pairs currently inside the safety distance, and the resolve commands
        # that stay open until their pair separates again.
        self.conflicts: dict[tuple[str, str], Conflict] = {}
        self.pending: dict[tuple[str, str], tuple[Command[dict[str, Any]], str]] = {}
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
                commands=("lowalt.airspace.activate", RESOLVE_COMMAND, CONTROL_COMMAND),
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
            for field, default in DETECTION_DEFAULTS.items():
                initial[ref.id].setdefault(field, copy.deepcopy(default))
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
                        [
                            center[0] - extent[0] / 2,
                            center[1] - extent[1] / 2,
                            center[2],
                        ],
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
        positions = self._positions(ctx, state)
        wind_speed = math.sqrt(sum(x * x for x in state.wind))
        exemptions = self.cfg.get("zone_exemptions", {})
        for ref in self.fleet:
            point = positions[ref.id]
            members = sorted(
                z.id
                for z in state.zones
                if z.active
                and ref.id not in exemptions.get(z.id, ())
                and all(a <= p <= b for a, p, b in zip(z.lower, point, z.upper))
            )
            if members != read(ctx, ref, "lowalt.membership"):
                ctx.set(ref, "lowalt.membership", members)
            # No-fly and restricted zones the aircraft is in, or crossed since
            # the last sample. The geofence rule reads this committed list.
            breached = sorted(
                z.id
                for z in state.zones
                if z.active
                and ref.id not in exemptions.get(z.id, ())
                and z.kind in {"no_fly", "restricted"}
                and (
                    z.id in members
                    or segment_hits_box(self.previous[ref.id], point, z.lower, z.upper)
                )
            )
            if breached != read(ctx, ref, "lowalt.breached_zones"):
                ctx.set(ref, "lowalt.breached_zones", breached)
            self.previous[ref.id] = point
            if wind_speed != read(ctx, ref, "lowalt.wind_mps"):
                ctx.set(ref, "lowalt.wind_mps", wind_speed)
        conflicts = separation_conflicts(positions, self.horizontal, self.vertical)
        pairs = {(c.a, c.b): c for c in conflicts}
        self.conflicts = pairs
        for pair in sorted(self.pending.keys() - pairs.keys()):
            command, action = self.pending.pop(pair)
            ctx.succeed(command, {"a": pair[0], "b": pair[1], "action": action})
        # Each pair is reported once, by its first aircraft (an external
        # responder never reports; its partner does).
        leads: dict[str, Conflict] = {}
        for conflict in conflicts:
            lead = conflict.a if conflict.a in self.by_fleet else conflict.b
            if lead in self.by_fleet:
                leads.setdefault(lead, conflict)
        for ref in self.fleet:
            members = sorted(
                b if a == ref.id else a for a, b in pairs if ref.id in {a, b}
            )
            if members != read(ctx, ref, "lowalt.conflicts"):
                ctx.set(ref, "lowalt.conflicts", members)
            lead_conflict = leads.get(ref.id)
            partner = (
                ""
                if lead_conflict is None
                else lead_conflict.b
                if lead_conflict.a == ref.id
                else lead_conflict.a
            )
            gap = (
                [0.0, 0.0]
                if lead_conflict is None
                else [lead_conflict.horizontal_m, lead_conflict.vertical_m]
            )
            if partner != read(ctx, ref, "lowalt.conflict_partner"):
                ctx.set(ref, "lowalt.conflict_partner", partner)
            if gap != read(ctx, ref, "lowalt.conflict_gap_m"):
                ctx.set(ref, "lowalt.conflict_gap_m", gap)

    def _positions(self, ctx: EngineContext, state: Snapshot) -> dict[str, Point]:
        # External responders retain their own sole motion/battery owners. These
        # vehicles are monitored but never enter the lowalt allocation pool.
        positions = {v.id: v.position for v in state.vehicles}
        positions.update(
            {
                identity: vec(read(ctx, self.by_id[identity], item["position_field"]))
                for identity, item in self.external.items()
            }
        )
        return positions

    def _resolve(self, ctx: EngineContext, command: Command[dict[str, Any]]) -> None:
        """Ask the conflict-resolution slot to separate one reported pair."""
        pair = (command.payload["a"], command.payload["b"])
        conflict = self.conflicts.get(pair)
        ctx.accept(command)
        ctx.execute(command)
        if conflict is None:
            ctx.succeed(
                command, {"a": pair[0], "b": pair[1], "action": "observed separation"}
            )
            return
        state = snapshot(ctx, self.build)
        positions = self._positions(ctx, state)
        state = replace(
            state,
            monitored_positions=tuple((i, positions[i]) for i in self.external),
        )
        controlled = (
            ()
            if conflict.a in self.external and conflict.b in self.external
            else (conflict,)
        )
        actions = set()
        for resolution in self.resolver.resolve(state, controlled):
            if resolution.vehicle not in positions:
                raise ValueError("conflict resolution selected an unknown vehicle")
            payload: dict[str, Any] = {
                "vehicle": resolution.vehicle,
                "action": resolution.action,
                "reason": "separation conflict",
            }
            if resolution.action == "altitude" and resolution.altitude_m is not None:
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
            actions.add(resolution.action)
            if resolution.vehicle in self.external:
                # The traffic behaviour owns its responding UAV commands;
                # move lowalt aircraft around that protected responder.
                # Resolver proposals for external aircraft are not executed.
                continue
            self._emit(ctx, "lowalt.control", payload)
        self.pending[pair] = (
            command,
            ", ".join(sorted(actions)) or "observed separation",
        )

    def _flight_control(
        self, ctx: EngineContext, command: Command[dict[str, Any]]
    ) -> None:
        """Hold, release or re-route an aircraft on behalf of a rule chain."""
        data = command.payload
        if data["vehicle"] not in self.by_fleet or data["action"] not in {
            "hold",
            "release",
            "reroute",
        }:
            ctx.reject(command, {"vehicle": data["vehicle"], "action": data["action"]})
            return
        ctx.accept(command)
        ctx.execute(command)
        self._emit(
            ctx,
            "lowalt.control",
            {
                "vehicle": data["vehicle"],
                "action": data["action"],
                "reason": data["reason"],
            },
        )
        ctx.succeed(command, {"vehicle": data["vehicle"], "action": data["action"]})

    def on_inputs(self, ctx: EngineContext) -> None:
        overrides: dict[str, tuple[bool, str]] = {}
        commands = []
        for delivery in ctx.inbox:
            data = cast(dict[str, Any], thaw(delivery.message.payload))
            if delivery.message.kind == "command":
                command = ctx.remember(
                    delivery, lambda value: cast(dict[str, Any], value)
                )
                if delivery.message.schema_id == RESOLVE_COMMAND:
                    self._resolve(ctx, command)
                    continue
                if delivery.message.schema_id == CONTROL_COMMAND:
                    self._flight_control(ctx, command)
                    continue
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
