"""Configured ENU point-mass motion with bounded speed/acceleration and energy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from aerokernel import Activate, EntityRef, Partition, Timing
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import Value, thaw

from aeroagentsim.platform.plugins import EngineBuild

from .common import bootstrap_owned, policies


def vector(value: Any) -> list[float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)
    ):
        raise ValueError(
            "kinematic: position/velocity/target must be a finite ENU three-vector"
        )
    return [float(x) for x in value]


def payload(value: Value) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("kinematic command: expected a typed record")
    return value


@dataclass
class Motion:
    ref: EntityRef
    position: list[float]
    velocity: list[float]
    energy: float
    command: Command[dict[str, Any]] | None = None
    origin: list[float] | None = None
    direction: list[float] | None = None
    target: list[float] | None = None
    start_ns: int = 0
    distance: float = 0.0
    ramp: float = 0.0
    cruise: float = 0.0
    braking: bool = False
    completed: bool = False
    failed: bool = False


class Kinematic(ContextEngine):
    """One fleet partition; work per fixed step is linear in configured entities.

    move_to starts from rest and follows an analytic triangular/trapezoidal speed
    profile. A busy replacement is rejected; hold/stop brake the actual velocity.
    Energy = idle_w * elapsed_s + per_m_j * integrated_distance_m, in joules.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        config = build.config
        frame = config["frame"]
        if (
            frame["convention"] != "enu"
            or frame["unit"] != "m"
            or not isinstance(frame["transform_revision"], str)
            or not frame["transform_revision"]
        ):
            raise ValueError(
                "kinematic.frame: explicitly bind ENU metres and a transform revision"
            )
        self.position_field = str(config["position_field"])
        self.velocity_field = str(config["velocity_field"])
        self.energy_field = str(config["energy_field"])
        self.max_speed = float(config["max_speed_m_s"])
        self.accel = float(config["max_accel_m_s2"])
        energy = config["energy"]
        self.capacity = float(energy["capacity_j"])
        self.idle = float(energy["idle_w"])
        self.per_m = float(energy["per_m_j"])
        if any(
            not math.isfinite(v) or v <= 0
            for v in (self.max_speed, self.accel, self.capacity)
        ) or any(not math.isfinite(v) or v < 0 for v in (self.idle, self.per_m)):
            raise ValueError(
                "kinematic: speed/acceleration/capacity must be positive; energy coefficients finite nonnegative"
            )
        self.schemas: dict[str, str] = dict(config["commands"])
        if set(self.schemas) != {"move_to", "hold", "stop"}:
            raise ValueError(
                "kinematic.commands: explicitly bind move_to, hold and stop schema IDs"
            )
        self.arrival_schema = str(config["arrival_schema"])
        self.topic = str(config["arrival_topic"])
        fields = (self.position_field, self.velocity_field, self.energy_field)
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=fields,
                commands=tuple(self.schemas.values()),
                emits=(self.arrival_schema,),
                message_targets=(self.topic,),
                timing=Timing("fixed_step", config["step_ns"]),
                lifecycle=bool(config["lifecycle"]),
            ),
            policies=policies(fields),
        )
        self.fleet: dict[str, Motion] = {}
        for ref in build.entities:
            if build.registry.is_a(ref.type_id, config["type_id"]):
                facts = build.initial[ref.id]
                energy_value = float(facts[self.energy_field])
                velocity = vector(facts[self.velocity_field])
                if (
                    not 0 <= energy_value <= self.capacity
                    or math.sqrt(sum(v * v for v in velocity)) > self.max_speed
                ):
                    raise ValueError(
                        f"kinematic.initial.{ref.id}: energy outside capacity or velocity exceeds max speed"
                    )
                self.fleet[ref.id] = Motion(
                    ref, vector(facts[self.position_field]), velocity, energy_value
                )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def _control(
        self, ctx: EngineContext, command: Command[dict[str, Any]], kind: str
    ) -> None:
        item = self.fleet.get(str(command.payload["entity"]))
        if item is None:
            ctx.reject(command, {"reason": "entity is outside configured fleet"})
            return
        if kind == "move_to" and any(item.velocity):
            ctx.reject(
                command,
                {"reason": "move_to requires rest; issue hold/stop to brake first"},
            )
            return
        if kind == "move_to" and item.command is not None:
            ctx.reject(command, {"reason": "entity already executing a command"})
            return
        if item.energy <= 0:
            ctx.reject(command, {"reason": "energy exhausted"})
            return
        # Reserve the declared model cost through the final integration boundary.
        # A route with insufficient real stored energy is rejected before execution.
        if kind == "move_to":
            goal = vector(command.payload["target"])
            length = math.sqrt(sum((a - b) ** 2 for a, b in zip(item.position, goal)))
            ramp = min(self.max_speed / self.accel, math.sqrt(length / self.accel))
            duration = 2 * ramp + max(
                0.0, (length - self.accel * ramp * ramp) / self.max_speed
            )
        else:
            speed = math.sqrt(sum(v * v for v in item.velocity))
            length, duration = speed * speed / (2 * self.accel), speed / self.accel
        step_ns = self.partition.timing.step_ns
        assert step_ns is not None
        cost = self.per_m * length + self.idle * max(
            step_ns / 1e9, math.ceil(duration * 1e9 / step_ns) * step_ns / 1e9
        )
        if cost > item.energy:
            ctx.reject(
                command, {"reason": "insufficient energy for bounded trajectory"}
            )
            return
        if item.command is not None:
            ctx.fail(
                item.command,
                {"reason": f"interrupted by {kind}", "position": item.position},
            )
        ctx.accept(command)
        ctx.execute(command)
        item.command = command
        item.origin = list(item.position)
        item.start_ns = ctx.now.ns
        item.completed = item.failed = False
        item.braking = kind != "move_to"
        if item.braking:
            speed = math.sqrt(sum(v * v for v in item.velocity))
            item.direction = [v / speed for v in item.velocity] if speed else [0.0] * 3
            item.ramp = speed / self.accel
            item.distance = speed * speed / (2 * self.accel)
            item.cruise = 0.0
            item.target = [
                p + u * item.distance for p, u in zip(item.position, item.direction)
            ]
        else:
            item.target = vector(command.payload["target"])
            delta = [b - a for a, b in zip(item.position, item.target)]
            item.distance = math.sqrt(sum(v * v for v in delta))
            item.direction = (
                [v / item.distance for v in delta] if item.distance else [0.0] * 3
            )
            item.ramp = min(
                self.max_speed / self.accel, math.sqrt(item.distance / self.accel)
            )
            item.cruise = max(
                0.0, (item.distance - self.accel * item.ramp**2) / self.max_speed
            )

    def step(self, ctx: EngineContext) -> None:
        dt = (ctx.now.ns - self.native_ns) / 1e9
        activated = False
        for item in self.fleet.values():
            ctx.inputs = (
                [] if item.command is None else [item.command.delivery.dispatch_ref]
            )
            previous = list(item.position)
            if item.command is None and any(item.velocity):
                item.position = [
                    p + v * dt for p, v in zip(item.position, item.velocity)
                ]
            if item.command is not None and not item.completed and not item.failed:
                assert (
                    item.origin is not None
                    and item.direction is not None
                    and item.target is not None
                )
                elapsed = (ctx.now.ns - item.start_ns) / 1e9
                peak = self.accel * item.ramp
                total = item.ramp if item.braking else 2 * item.ramp + item.cruise
                t = min(elapsed, total)
                if item.braking:
                    distance = peak * t - 0.5 * self.accel * t * t
                    speed = max(0.0, peak - self.accel * t)
                elif t < item.ramp:
                    distance, speed = 0.5 * self.accel * t * t, self.accel * t
                elif t < item.ramp + item.cruise:
                    distance = 0.5 * self.accel * item.ramp**2 + peak * (t - item.ramp)
                    speed = peak
                else:
                    remaining = total - t
                    distance, speed = (
                        item.distance - 0.5 * self.accel * remaining**2,
                        self.accel * remaining,
                    )
                item.position = [
                    a + u * distance for a, u in zip(item.origin, item.direction)
                ]
                item.velocity = [u * speed for u in item.direction]
                if elapsed >= total:
                    item.position, item.velocity = list(item.target), [0.0] * 3
                    item.completed = True
                    activated = True
            moved = math.sqrt(
                sum((a - b) ** 2 for a, b in zip(previous, item.position))
            )
            consumption = self.idle * dt + self.per_m * moved
            if (
                consumption > item.energy
                and item.command is None
                and any(item.velocity)
            ):
                raise ValueError(
                    f"kinematic.energy: uncommanded motion depleted {item.ref.id}; configure enough model energy or a braking command"
                )
            if consumption >= item.energy:
                item.energy = 0.0
                if item.command is not None and not item.completed:
                    item.failed = True
                    activated = True
            else:
                item.energy -= consumption
            ctx.set(item.ref, self.position_field, item.position)
            ctx.set(item.ref, self.velocity_field, item.velocity)
            ctx.set(item.ref, self.energy_field, item.energy)
        if activated:
            ctx.ops.append(Activate(self.partition.id))

    def on_inputs(self, ctx: EngineContext) -> None:
        # Finish integrated outcomes before applying newly latched controls.
        for item in self.fleet.values():
            if item.command is None:
                continue
            ctx.inputs = [
                dirty.cause for dirty in ctx.dirty if dirty.kind == "activation"
            ]
            ctx.get(item.ref, self.position_field)
            if item.failed:
                ctx.fail(
                    item.command,
                    {"reason": "energy exhausted", "position": item.position},
                )
                item.command = None
            elif item.completed:
                result = {"entity": item.ref.id, "position": item.position}
                ctx.feedback(item.command, result)
                ctx.succeed(item.command, result)
                if not item.braking:
                    ctx.emit(
                        self.arrival_schema,
                        {
                            **result,
                            "machine": item.command.payload.get("machine", item.ref.id),
                        },
                        topic=self.topic,
                    )
                item.command = None
        reverse = {schema: kind for kind, schema in self.schemas.items()}
        for delivery in ctx.inbox:
            # Independent controls cite their own actual dispatch, preventing an
            # unrelated fleet inbox from generating quadratic causal envelopes.
            ctx.inputs = [delivery.dispatch_ref]
            if delivery.message.kind == "cancel":
                cancel_payload = payload(thaw(delivery.message.payload))
                command = ctx.command(str(cancel_payload["command_id"]))
                ctx.decide_cancel(
                    command, delivery, False, "use hold/stop to perform bounded braking"
                )
            else:
                self._control(
                    ctx,
                    ctx.remember(delivery, payload),
                    reverse[delivery.message.schema_id],
                )


def build(context: EngineBuild) -> Kinematic:
    return Kinematic(context)
