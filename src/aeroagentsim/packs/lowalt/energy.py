"""One authoritative battery state coupled to observed motion and weather."""

from __future__ import annotations

import math
from typing import Any, cast

from aerokernel import Dependency, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import Charging
from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.common import distance, finite, ns, read, vec
from aeroagentsim.platform.plugins import EngineBuild

from .core import SNAPSHOT_FIELDS, refs, snapshot

PRODUCES = (
    "lowalt.soc",
    "lowalt.charging",
    "lowalt.energy_used_j",
    "lowalt.distance_m",
    "lowalt.motion_energy_j",
)
EVENTS = (
    "lowalt.energy.low",
    "lowalt.energy.charging_started",
    "lowalt.energy.charging_finished",
    "lowalt.control",
)


class LowaltEnergy(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
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
        self.threshold = finite(self.cfg["low_threshold"], "low threshold", 0)
        self.target = finite(self.cfg["charging_target_soc"], "charging target", 0)
        if not self.threshold < self.target <= 1:
            raise ValueError(
                "charging requires low_threshold < charging_target_soc <= 1"
            )
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
        self.previous: dict[str, tuple[float, float, float]] = {}
        self.previous_ns = 0
        self.returns: dict[str, str] = {}
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
                message_targets=("lowalt.events",),
                lifecycle=True,
            ),
            policies=policies(PRODUCES),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        for ref in self.fleet:
            facts = self.build.initial[ref.id]
            capacity = finite(facts["lowalt.capacity_j"], "battery capacity", 1e-9)
            soc = finite(facts["lowalt.soc"], "initial soc", 0)
            if soc > 1 or not math.isclose(
                finite(facts["lowalt.motion_energy_j"], "battery energy", 0),
                soc * capacity,
            ):
                raise ValueError(
                    "initial SOC and battery energy must describe the same battery"
                )
            self.previous[ref.id] = vec(facts["lowalt.position"])
        self.previous_ns = ctx.now.ns
        bootstrap_owned(ctx, self.build)
        ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_energy": "tick"})

    def _emit(self, ctx: EngineContext, schema: str, **payload: Any) -> None:
        ctx.emit(schema, payload, topic="lowalt.events")

    def _poll(self, ctx: EngineContext) -> None:
        elapsed = (ctx.now.ns - self.previous_ns) / 1e9
        if elapsed <= 0:
            raise ValueError("energy samples must advance simulated time")
        self.previous_ns = ctx.now.ns
        state = snapshot(ctx, self.build)
        pads = {p.id: p for p in state.pads}
        requested = {
            proposal.vehicle: proposal.pad for proposal in self.charger.charge(state)
        }
        for identity, pad_id in requested.items():
            if identity not in self.previous or pad_id not in pads:
                raise ValueError("charging proposal must select a real vehicle and pad")
        for ref in self.fleet:
            position = vec(read(ctx, ref, "lowalt.position"))
            velocity = vec(read(ctx, ref, "lowalt.velocity"))
            moved = distance(position, self.previous[ref.id])
            self.previous[ref.id] = position
            speed = math.sqrt(sum(v * v for v in velocity))
            soc = finite(read(ctx, ref, "lowalt.soc"), "soc", 0)
            capacity = finite(read(ctx, ref, "lowalt.capacity_j"), "capacity", 1e-9)
            charging = read(ctx, ref, "lowalt.charging")
            if ref.id in requested and ref.id not in self.returns:
                requested_pad = pads[requested[ref.id]]
                self.returns[ref.id] = requested_pad.id
                self._emit(
                    ctx,
                    "lowalt.control",
                    vehicle=ref.id,
                    action="return_to_pad",
                    target=list(requested_pad.position),
                    reason="charging algorithm",
                )
            pad = pads[self.returns[ref.id]] if ref.id in self.returns else None
            at_pad = (
                pad is not None
                and distance(position, pad.position) <= self.radius
                and speed <= 1e-6
            )
            if charging and not at_pad:
                raise ValueError("charging vehicle moved away from its selected pad")
            started = not charging and at_pad
            if charging:
                assert pad is not None
                drain = 0.0
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
                # Connection starts at this observed arrival, after the travel
                # drain. Only subsequent intervals receive pad energy.
                charging = True
            if (
                next_soc <= self.threshold < soc
                or ctx.now.ns == self.tick
                and soc <= self.threshold
            ):
                self._emit(ctx, "lowalt.energy.low", vehicle=ref.id, soc=next_soc)
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
            ctx.set(ref, "lowalt.soc", next_soc)
            ctx.set(ref, "lowalt.motion_energy_j", next_soc * capacity)
            ctx.set(ref, "lowalt.charging", charging)
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

    def on_inputs(self, ctx: EngineContext) -> None:
        if any(
            d.kind == "timer" and thaw(d.payload) == {"lowalt_energy": "tick"}
            for d in ctx.dirty
        ):
            self._poll(ctx)
            ctx.wake_at(ctx.now.ns + self.tick, {"lowalt_energy": "tick"})


def build(context: EngineBuild) -> LowaltEnergy:
    return LowaltEnergy(context)
