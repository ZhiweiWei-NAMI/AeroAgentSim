"""One authoritative battery state coupled to observed motion and weather."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from typing import Any, cast

from aerokernel import Dependency, Partition
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import Charging
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.common import distance, finite, ns, read, vec
from aeroagentsim.platform.plugins import EngineBuild

from .core import SNAPSHOT_FIELDS, SnapshotIndex, refs, snapshot

PRODUCES = (
    "lowalt.soc",
    "lowalt.charging",
    "lowalt.charging_waiting",
    "lowalt.charging_pad",
    "lowalt.energy_used_j",
    "lowalt.distance_m",
    "lowalt.motion_energy_j",
)
RETURN_COMMAND = "lowalt.energy.return_to_pad"
EVENTS = (
    "lowalt.energy.charging_started",
    "lowalt.energy.charging_waiting",
    "lowalt.energy.charging_finished",
    "lowalt.control",
    "lowalt.energy.swap_started",
    "lowalt.energy.swap_finished",
)


class LowaltEnergy(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        self.snapshot_index = SnapshotIndex(build)
        self.tick = ns(self.cfg["tick_ns"], "energy tick", 1)
        self.coefficients = {
            k: finite(self.cfg[k], k, 0)
            for k in (
                "idle_w",
                "per_m_j",
                "speed_w_per_mps",
                "payload_w_per_kg",
                "headwind_w_per_mps",
            )
        }
        self.target = finite(self.cfg["charging_target_soc"], "charging target", 0)
        if not 0 < self.target <= 1:
            raise ValueError("charging requires 0 < charging_target_soc <= 1")
        self.radius = finite(self.cfg["charge_radius_m"], "charge radius", 0)
        self.charger = cast(
            Charging,
            load_algorithm(
                "charging",
                self.cfg["algorithms"]["charging"],
                self.cfg.get("algorithm_config", {}).get("charging"),
            ),
        )
        self.fleet = refs(build, "lowalt:UAV")
        self.by_id = {ref.id: ref for ref in self.fleet}
        self.previous: dict[str, tuple[float, float, float]] = {}
        self.previous_ns = 0
        self.swap_ends: dict[str, int] = {}
        self.returns: dict[str, str] = {}
        self.arrivals: dict[str, int] = {}
        # The reserve rule (a behaviour chain) asks for a return; the command
        # stays open until the aircraft has finished charging.
        self.waiting: dict[str, Command[dict[str, Any]]] = {}
        consumed = tuple(
            dict.fromkeys(
                SNAPSHOT_FIELDS + PRODUCES + ("lowalt.capacity_j", "lowalt.velocity")
            )
        )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=PRODUCES,
                consumes=tuple(Dependency(f) for f in consumed),
                emits=EVENTS,
                commands=(RETURN_COMMAND,),
                message_targets=("lowalt.events",),
                lifecycle=True,
            ),
            policies=policies(PRODUCES),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        initial = copy.deepcopy(self.build.initial)
        for ref in self.fleet:
            facts = initial[ref.id]
            capacity = finite(facts["lowalt.capacity_j"], "battery capacity", 1e-9)
            soc = finite(facts["lowalt.soc"], "initial soc", 0)
            if soc > 1 or not math.isclose(
                finite(facts["lowalt.motion_energy_j"], "battery energy", 0),
                soc * capacity,
            ):
                raise ValueError(
                    "initial SOC and battery energy must describe the same battery"
                )
            if facts["lowalt.charging"] is not False:
                raise ValueError("initial charging requires a running charging session")
            for field, value in (
                ("lowalt.charging_waiting", False), ("lowalt.charging_pad", ""),
            ):
                if field in facts and (
                    type(facts[field]) is not type(value) or facts[field] != value
                ):
                    raise ValueError(f"{field}: initial aircraft has no charging session")
                # Empty reservations are the energy owner's initial state,
                # rather than inferred replacements for authored pad inputs.
                facts[field] = value
            self.previous[ref.id] = vec(facts["lowalt.position"])
        for pad in refs(self.build, "lowalt:ChargingPad"):
            ns(initial[pad.id]["lowalt.site_capacity"], "charging pad capacity", 1)
        self.previous_ns = ctx.now.ns
        bootstrap_owned(ctx, replace(self.build, initial=initial))
        ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_energy": "tick"})

    def _emit(self, ctx: EngineContext, schema: str, **payload: Any) -> None:
        ctx.emit(schema, payload, topic="lowalt.events")

    def _poll(self, ctx: EngineContext) -> None:
        elapsed = (ctx.now.ns - self.previous_ns) / 1e9
        if elapsed <= 0:
            raise ValueError("energy samples must advance simulated time")
        self.previous_ns = ctx.now.ns
        state = snapshot(ctx, self.build, index=self.snapshot_index)
        pads = {p.id: p for p in state.pads}
        vehicles = {vehicle.id: vehicle for vehicle in state.vehicles}
        velocities = {
            ref.id: vec(read(ctx, ref, "lowalt.velocity")) for ref in self.fleet
        }
        speeds = {
            identity: math.sqrt(sum(v * v for v in velocity))
            for identity, velocity in velocities.items()
        }
        occupied = {p.id: p.occupancy for p in state.pads}
        charging_ids = {
            ref.id for ref in self.fleet if read(ctx, ref, "lowalt.charging")
        }
        for ref in self.fleet:
            pad = pads[self.returns[ref.id]] if ref.id in self.returns else None
            at_pad = (
                pad is not None
                and distance(vehicles[ref.id].position, pad.position) <= self.radius
                and speeds[ref.id] <= 1e-6
            )
            if at_pad:
                self.arrivals.setdefault(ref.id, ctx.now.ns)
            else:
                self.arrivals.pop(ref.id, None)
        # Complete existing sessions first, then admit stopped arrivals in FIFO
        # order. The local count includes writes not yet visible in ctx.view.
        ordered = sorted(
            self.fleet,
            key=lambda ref: (
                ref.id not in charging_ids,
                self.arrivals.get(ref.id, ctx.now.ns), ref.id,
            ),
        )
        for ref in ordered:
            position = vehicles[ref.id].position
            velocity = velocities[ref.id]
            moved = distance(position, self.previous[ref.id])
            self.previous[ref.id] = position
            speed = speeds[ref.id]
            soc = vehicles[ref.id].soc
            capacity = finite(read(ctx, ref, "lowalt.capacity_j"), "capacity", 1e-9)
            charging = ref.id in charging_ids
            pad = pads[self.returns[ref.id]] if ref.id in self.returns else None
            at_pad = (
                pad is not None
                and distance(position, pad.position) <= self.radius
                and speed <= 1e-6
            )
            if charging and not at_pad:
                raise ValueError("charging vehicle moved away from its selected pad")
            started = (
                not charging and at_pad and pad is not None
                and occupied[pad.id] < pad.capacity
            )
            if charging:
                assert pad is not None
                drain = 0.0
                if pad.id in self.cfg.get("swap_pads", ()):
                    drain = 0.0
                    ready = ctx.now.ns >= self.swap_ends[ref.id]
                    next_soc = self.target if ready else soc
                    if ready:
                        self._emit(
                            ctx,
                            "lowalt.energy.swap_finished",
                            vehicle=ref.id,
                            pad=pad.id,
                            soc=next_soc,
                        )
                        self.swap_ends.pop(ref.id)
                else:
                    next_soc = min(self.target, soc + pad.power_w * elapsed / capacity)
            else:
                headwind = (
                    max(0.0, -sum(w * v for w, v in zip(state.wind, velocity)) / speed)
                    if speed > 0
                    else 0.0
                )
                payload = finite(read(ctx, ref, "lowalt.payload_kg"), "payload", 0)
                c = self.coefficients
                requested_drain = c["per_m_j"] * moved + elapsed * (
                    c["idle_w"]
                    + c["speed_w_per_mps"] * speed
                    + c["payload_w_per_kg"] * payload
                    + c["headwind_w_per_mps"] * headwind
                )
                drain = min(soc * capacity, requested_drain)
                next_soc = (soc * capacity - drain) / capacity
            if started:
                assert pad is not None
                self._emit(
                    ctx,
                    "lowalt.energy.charging_started",
                    vehicle=ref.id,
                    pad=pad.id,
                    soc=next_soc,
                )
                if pad.id in self.cfg.get("swap_pads", ()):
                    self.swap_ends[ref.id] = ctx.now.ns + ns(
                        self.cfg["swap_duration_ns"], "swap duration", 1
                    )
                    self._emit(
                        ctx, "lowalt.energy.swap_started", vehicle=ref.id, pad=pad.id
                    )
                # Connection starts at this observed arrival, after the travel
                # drain. Only subsequent intervals receive pad energy.
                charging = True
                occupied[pad.id] += 1
            queued = at_pad and not charging
            if queued and not read(ctx, ref, "lowalt.charging_waiting"):
                assert pad is not None
                self._emit(
                    ctx, "lowalt.energy.charging_waiting", vehicle=ref.id,
                    pad=pad.id, capacity=pad.capacity, occupancy=occupied[pad.id],
                )
            if charging and next_soc >= self.target:
                assert pad is not None
                charging = False
                self._emit(
                    ctx,
                    "lowalt.energy.charging_finished",
                    vehicle=ref.id,
                    pad=pad.id,
                    soc=next_soc,
                )
                self._emit(
                    ctx,
                    "lowalt.control",
                    vehicle=ref.id,
                    action="release",
                    reason="charging finished",
                )
                self.returns.pop(ref.id)
                self.arrivals.pop(ref.id, None)
                occupied[pad.id] -= 1
                ctx.set(ref, "lowalt.charging_pad", "")
                waiting = self.waiting.pop(ref.id)
                ctx.succeed(
                    waiting, {"vehicle": ref.id, "pad": pad.id, "soc": next_soc}
                )
            ctx.set(ref, "lowalt.soc", next_soc)
            ctx.set(ref, "lowalt.motion_energy_j", next_soc * capacity)
            ctx.set(ref, "lowalt.charging", charging)
            ctx.set(ref, "lowalt.charging_waiting", queued)
            ctx.set(
                ref,
                "lowalt.energy_used_j",
                finite(read(ctx, ref, "lowalt.energy_used_j"), "energy used", 0)
                + drain,
            )
            ctx.set(
                ref,
                "lowalt.distance_m",
                finite(read(ctx, ref, "lowalt.distance_m"), "distance flown", 0)
                + moved,
            )

    def _return_to_pad(
        self, ctx: EngineContext, command: Command[dict[str, Any]]
    ) -> None:
        """Run the charging algorithm for one aircraft the reserve rule flagged."""
        identity = command.payload["vehicle"]
        if identity not in self.previous or identity in self.returns:
            ctx.reject(
                command, {"vehicle": identity, "reason": "no free aircraft to recall"}
            )
            return
        state = snapshot(ctx, self.build, index=self.snapshot_index)
        state = replace(
            state,
            pads=tuple(
                replace(
                    pad,
                    reserved=sum(p == pad.id for p in self.returns.values())
                    - pad.occupancy,
                )
                for pad in state.pads
            ),
        )
        flagged = replace(
            state, vehicles=tuple(v for v in state.vehicles if v.id == identity)
        )
        pads = {p.id: p for p in state.pads}
        proposals = self.charger.charge(flagged)
        if not proposals:
            ctx.reject(
                command, {"vehicle": identity, "reason": "no charging pad proposed"}
            )
            return
        proposal = proposals[0]
        if proposal.vehicle != identity or proposal.pad not in pads:
            raise ValueError("charging proposal must select a real vehicle and pad")
        pad = pads[proposal.pad]
        ctx.accept(command)
        ctx.execute(command)
        self.returns[identity] = pad.id
        ctx.set(self.by_id[identity], "lowalt.charging_pad", pad.id)
        self.waiting[identity] = command
        self._emit(
            ctx,
            "lowalt.control",
            vehicle=identity,
            action="return_to_pad",
            target=list(pad.position),
            reason="charging algorithm",
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            if delivery.message.kind == "command":
                self._return_to_pad(
                    ctx,
                    ctx.remember(delivery, lambda value: cast(dict[str, Any], value)),
                )
        if any(
            d.kind == "timer" and thaw(d.payload) == {"lowalt_energy": "tick"}
            for d in ctx.dirty
        ):
            self._poll(ctx)
            ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_energy": "tick"})


def build(context: EngineBuild) -> LowaltEnergy:
    return LowaltEnergy(context)
