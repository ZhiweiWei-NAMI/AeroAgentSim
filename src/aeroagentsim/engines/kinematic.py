"""Configured ENU point-mass motion with bounded speed/acceleration and energy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from aerokernel import Activate, Dependency, EntityRef, Partition, Timing
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import Value, thaw

from aeroagentsim.models import Segment, finite, motion_model
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


def number(value: Any, path: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"kinematic.{path}: finite numeric value required")
    return float(value)


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
    canceling: bool = False
    integrated_ns: int = 0


class Kinematic(ContextEngine):
    """One fleet partition; work per fixed step is linear in configured entities.

    move_to starts from rest and follows an analytic triangular/trapezoidal speed
    profile. A busy replacement is rejected; hold/stop brake the actual velocity.
    Energy = idle_w * elapsed_s + per_m_j * integrated_distance_m, in joules.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        # Manifest authority is immutable for a reference/generation; resolve it
        # once rather than matching fleet-wide selectors at every native step.
        self.energy_ownership: dict[EntityRef, bool] = {}
        config = build.config
        allowed = {
            "model",
            "type_id",
            "position_field",
            "velocity_field",
            "energy_field",
            "max_speed_m_s",
            "max_accel_m_s2",
            "energy",
            "commands",
            "arrival_schema",
            "arrival_topic",
            "arrival_payload",
            "result_fields",
            "command_entity_key",
            "command_target_key",
            "step_ns",
            "lifecycle",
            "frame",
            "initial_state",
            "consumption_model",
            "reserve_j",
            "energy_lag_ns",
        }
        if set(config) - allowed:
            raise ValueError(
                f"kinematic.config: unknown keys {sorted(set(config) - allowed)}"
            )
        if config.get("model") != "enu_point_mass":
            raise ValueError("kinematic.model: explicitly select enu_point_mass")
        if type(config["lifecycle"]) is not bool:
            raise ValueError("kinematic.lifecycle: explicit bool required")
        for key in (
            "type_id",
            "position_field",
            "velocity_field",
            "energy_field",
            "arrival_schema",
            "arrival_topic",
            "command_entity_key",
            "command_target_key",
        ):
            if key in config and (type(config[key]) is not str or not config[key]):
                raise ValueError(f"kinematic.{key}: nonempty string required")
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
        self.max_speed = number(config["max_speed_m_s"], "max_speed_m_s")
        self.accel = number(config["max_accel_m_s2"], "max_accel_m_s2")
        energy = config["energy"]
        plugin = config.get("consumption_model", {"plugin": "linear"})["plugin"]
        required_energy = {"capacity_j"}
        if plugin in {"linear", "wind"}:
            required_energy |= {"idle_w", "per_m_j"}
        if required_energy - set(energy) or set(energy) - {
            "capacity_j",
            "idle_w",
            "per_m_j",
        }:
            raise ValueError(
                "kinematic.energy: declare capacity_j and the selected model's coefficients"
            )
        if set(frame) != {"convention", "unit", "transform_revision"}:
            raise ValueError("kinematic.frame: unknown or missing model contract")
        self.capacity = number(energy["capacity_j"], "energy.capacity_j")
        if build.id not in build.models:
            build.models[build.id] = motion_model(
                config, build.registry, build.entities
            )
        self.model = build.models[build.id]
        self.consumption_model = self.model.consumption
        self.reserve = finite(config.get("reserve_j", 0.0), "reserve_j")
        energy_lag = config.get("energy_lag_ns", 0)
        if type(energy_lag) is not int or energy_lag < 0:
            raise ValueError("kinematic.energy_lag_ns: nonnegative integer required")
        if "initial_state" in config:
            initial_state = config["initial_state"]
            if not isinstance(initial_state, dict) or set(initial_state) != {
                "position",
                "velocity",
                "energy_j",
            }:
                raise ValueError(
                    "kinematic.initial_state: declare position, velocity, energy_j"
                )
            vector(initial_state["position"])
            dynamic_velocity = vector(initial_state["velocity"])
            dynamic_energy = number(initial_state["energy_j"], "initial_state.energy_j")
            if (
                not 0 <= dynamic_energy <= self.capacity
                or math.sqrt(sum(v * v for v in dynamic_velocity)) > self.max_speed
            ):
                raise ValueError("kinematic.initial_state: exceeds model bounds")
        if any(
            not math.isfinite(v) or v <= 0
            for v in (self.max_speed, self.accel, self.capacity)
        ) or any(
            number(energy[key], f"energy.{key}") < 0
            for key in ("idle_w", "per_m_j")
            if key in energy
        ):
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
        self.entity_key = config.get("command_entity_key", "entity")
        self.target_key = config.get("command_target_key", "target")
        self.arrival_payload = config["arrival_payload"]
        self.result_fields = config["result_fields"]
        if set(self.result_fields) != {"entity", "position", "reason"} or any(
            type(v) is not str or not v for v in self.result_fields.values()
        ):
            raise ValueError(
                "kinematic.result_fields: declare entity, position, reason slot names"
            )
        if not isinstance(self.arrival_payload, dict) or any(
            type(v) is not str
            or v not in {"$entity", "$position"}
            and not v.startswith("$payload.")
            for v in self.arrival_payload.values()
        ):
            raise ValueError(
                "kinematic.arrival_payload: select $entity, $position or $payload.<slot>"
            )
        fields = (self.position_field, self.velocity_field, self.energy_field)
        for slot in fields:
            descriptor = build.registry.field(slot)
            if not build.registry.is_a(config["type_id"], descriptor.declaring_type):
                raise ValueError(f"kinematic.{slot}: model type does not support slot")
        for slot in fields[:2]:
            descriptor = build.registry.field(slot)
            if (
                descriptor.schema.get("type") != "vector"
                or descriptor.schema.get("length") != 3
            ):
                raise ValueError(f"kinematic.{slot}: model slot must be a three-vector")
            bound_frame = descriptor.metadata.get("frame")
            if isinstance(bound_frame, str) and bound_frame != frame["convention"]:
                raise ValueError(
                    f"kinematic.{slot}: bound descriptor frame conflicts with model"
                )
        arrival = build.registry.message(self.arrival_schema)
        if arrival.kind != "event" or arrival.schema.get("type") != "record":
            raise ValueError("kinematic.arrival_schema: typed record event required")
        arrival_members = arrival.schema["members"]
        if set(self.arrival_payload) - set(arrival_members) or set(
            arrival.schema.get("required", ())
        ) - set(self.arrival_payload):
            raise ValueError("kinematic.arrival_payload: does not cover event schema")
        for kind, schema_id in self.schemas.items():
            command = build.registry.message(schema_id)
            if command.kind != "command" or command.schema.get("type") != "record":
                raise ValueError("kinematic.commands: record command required")
            members = command.schema["members"]
            if self.entity_key not in members or members[self.entity_key][
                "type"
            ] not in {"string", "ref"}:
                raise ValueError(
                    "kinematic.command_entity_key: string ID or typed ref slot required"
                )
            if kind == "move_to" and (
                self.target_key not in members
                or members[self.target_key].get("type") != "vector"
                or members[self.target_key].get("length") != 3
            ):
                raise ValueError(
                    "kinematic.command_target_key: three-vector slot required"
                )
            if (
                command.result_schema is None
                or command.result_schema.get("type") != "record"
                or set(self.result_fields.values())
                - set(command.result_schema["members"])
            ):
                raise ValueError(
                    "kinematic.result_fields: result schema slots required"
                )
            for selector in self.arrival_payload.values():
                if selector.startswith("$payload.") and selector[9:] not in members:
                    raise ValueError(
                        "kinematic.arrival_payload: unknown command payload selector"
                    )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=tuple(
                    f
                    for f in fields
                    if any(
                        r.partition == build.id and f in r.fields
                        for r in build.manifest.rules
                    )
                    or any(
                        e.partition == build.id and e.field == f
                        for e in build.manifest.exact
                    )
                ),
                consumes=tuple(
                    Dependency(
                        f,
                        lag_ns=config.get("energy_lag_ns", 0)
                        if f == self.energy_field
                        else 0,
                    )
                    for f in dict.fromkeys(
                        self.consumption_model.dependencies
                        + (
                            (self.energy_field,)
                            if any(
                                r.partition != build.id
                                and self.energy_field in r.fields
                                for r in build.manifest.rules
                            )
                            or any(
                                e.partition != build.id and e.field == self.energy_field
                                for e in build.manifest.exact
                            )
                            else ()
                        )
                    )
                ),
                commands=tuple(self.schemas.values()),
                emits=(self.arrival_schema,),
                message_targets=(self.topic,),
                timing=Timing("fixed_step", config["step_ns"]),
                lifecycle=config["lifecycle"],
                lifecycle_reads=tuple(
                    t.id
                    for t in build.registry.types
                    if build.registry.is_a(t.id, config["type_id"])
                ),
            ),
            policies=policies(fields),
        )
        self.fleet: dict[str, Motion] = {}
        for ref in build.entities:
            if self._owns(ref):
                facts = build.initial[ref.id]
                energy_value = number(
                    facts[self.energy_field], f"initial.{ref.id}.energy"
                )
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

    def _owns(self, ref: EntityRef) -> bool:
        if not self.build.registry.is_a(ref.type_id, self.build.config["type_id"]):
            return False
        slots = {self.position_field, self.velocity_field}
        selected = set(self.build.owned_fields(ref))
        owned = slots & selected
        if not owned and self.energy_field in selected:
            raise ValueError(
                f"kinematic.ownership.{ref.id}: an energy-only binding requires an independent energy producer, not a trajectory engine"
            )
        if owned and owned != slots:
            raise ValueError(
                f"kinematic.ownership.{ref.id}: this trajectory model requires position and velocity authority together"
            )
        return owned == slots

    def _owns_energy(self, ref: EntityRef) -> bool:
        if ref not in self.energy_ownership:
            self.energy_ownership[ref] = self.energy_field in self.build.owned_fields(
                ref
            )
        return self.energy_ownership[ref]

    def _sync(self, ctx: EngineContext) -> None:
        """Consume actual lifecycle notifications, including new generations."""
        for dirty in ctx.dirty:
            if dirty.kind != "lifecycle":
                continue
            data = thaw(dirty.payload)
            if not isinstance(data, dict):
                raise TypeError("kinematic.lifecycle: typed entity ref required")
            ref = EntityRef.from_data(data["$ref"])
            if not self._owns(ref):
                continue
            life = ctx.view.lifecycle(ref)
            if life.removed is not None:
                self.energy_ownership.pop(ref, None)
                item = self.fleet.pop(ref.id, None)
                if item is not None and item.command is not None:
                    raise ValueError(
                        "kinematic.remove: cancel and brake active commands before removal"
                    )
                continue
            if ref.id in self.fleet and self.fleet[ref.id].ref == ref:
                continue
            initial = self.build.config.get("initial_state")
            if initial is None:
                raise ValueError(
                    "kinematic.initial_state: explicit model initialization required for dynamic generations"
                )
            position, velocity = (
                vector(initial["position"]),
                vector(initial["velocity"]),
            )
            energy = number(initial["energy_j"], "initial_state.energy_j")
            if (
                not 0 <= energy <= self.capacity
                or math.sqrt(sum(v * v for v in velocity)) > self.max_speed
            ):
                raise ValueError("kinematic.initial_state: exceeds model bounds")
            elapsed = (ctx.now.ns - life.created.instant.ns) / 1e9
            distance = math.sqrt(sum(v * v for v in velocity)) * elapsed
            if self._owns_energy(ref):
                displacement = tuple(v * elapsed for v in velocity)
                consumption = finite(
                    self.consumption_model.consumption(
                        ctx,
                        ref,
                        Segment(
                            (displacement[0], displacement[1], displacement[2]),
                            elapsed,
                            distance,
                        ),
                    ),
                    "consumption",
                )
                if consumption > energy and any(velocity):
                    raise ValueError(
                        f"kinematic.initial_state: dynamic generation {ref.id} depleted before first native boundary"
                    )
                energy = max(0.0, energy - consumption)
            else:
                energy = number(ctx.get(ref, self.energy_field), "stored energy")
            position = [p + v * elapsed for p, v in zip(position, velocity)]
            self.fleet[ref.id] = Motion(
                ref, position, velocity, energy, integrated_ns=ctx.now.ns
            )
            ctx.set(ref, self.position_field, position)
            ctx.set(ref, self.velocity_field, velocity)
            if self._owns_energy(ref):
                ctx.set(ref, self.energy_field, energy)

    def _result(self, **values: Any) -> dict[str, Any]:
        return {self.result_fields[key]: value for key, value in values.items()}

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def _control(
        self, ctx: EngineContext, command: Command[dict[str, Any]], kind: str
    ) -> None:
        subject = command.payload[self.entity_key]
        entity_id = (
            EntityRef.from_data(subject["$ref"]).id
            if isinstance(subject, dict)
            else subject
        )
        item = self.fleet.get(entity_id)
        if (
            item is None
            or isinstance(subject, dict)
            and EntityRef.from_data(subject["$ref"]) != item.ref
        ):
            ctx.reject(
                command, self._result(reason="entity is outside configured fleet")
            )
            return
        if not self._owns_energy(item.ref):
            item.energy = number(ctx.get(item.ref, self.energy_field), "stored energy")
        if kind == "move_to" and any(item.velocity):
            ctx.reject(
                command,
                self._result(
                    reason="move_to requires rest; issue hold/stop to brake first"
                ),
            )
            return
        if kind == "move_to" and item.command is not None:
            ctx.reject(
                command, self._result(reason="entity already executing a command")
            )
            return
        if item.energy <= 0:
            ctx.reject(command, self._result(reason="energy exhausted"))
            return
        # Reserve the declared model cost through the final integration boundary.
        # A route with insufficient real stored energy is rejected before execution.
        if kind == "move_to":
            goal = vector(command.payload[self.target_key])
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
        seconds = max(
            step_ns / 1e9, math.ceil(duration * 1e9 / step_ns) * step_ns / 1e9
        )
        if kind == "move_to":
            budget_delta = tuple(b - a for a, b in zip(item.position, goal))
        else:
            speed = math.sqrt(sum(v * v for v in item.velocity))
            budget_delta = tuple(
                v / speed * length if speed else 0.0 for v in item.velocity
            )
        cost = finite(
            self.consumption_model.budget(
                ctx,
                item.ref,
                (
                    Segment(
                        (budget_delta[0], budget_delta[1], budget_delta[2]),
                        seconds,
                        length,
                    ),
                ),
            ),
            "budget",
        )
        if cost + self.reserve > item.energy:
            ctx.reject(
                command,
                self._result(
                    reason=(
                        "energy reserve would be violated"
                        if self.reserve and cost <= item.energy
                        else "insufficient energy for bounded trajectory"
                    )
                ),
            )
            return
        if item.command is not None:
            ctx.fail(
                item.command,
                self._result(reason=f"interrupted by {kind}", position=item.position),
            )
        ctx.accept(command)
        ctx.execute(command)
        item.command = command
        item.origin = list(item.position)
        item.start_ns = ctx.now.ns
        item.completed = item.failed = False
        item.canceling = False
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
            item.target = vector(command.payload[self.target_key])
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
        activated = False
        retained_inputs = list(ctx.inputs)
        for item in self.fleet.values():
            dt = (ctx.now.ns - item.integrated_ns) / 1e9
            item.integrated_ns = ctx.now.ns
            ctx.inputs = retained_inputs + (
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
            if not self._owns_energy(item.ref):
                item.energy = number(
                    ctx.get(item.ref, self.energy_field), "stored energy"
                )
            delta = tuple(b - a for a, b in zip(previous, item.position))
            consumption = (
                finite(
                    self.consumption_model.consumption(
                        ctx,
                        item.ref,
                        Segment((delta[0], delta[1], delta[2]), dt, moved),
                    ),
                    "consumption",
                )
                if self._owns_energy(item.ref)
                else 0.0
            )
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
            if self._owns_energy(item.ref):
                ctx.set(item.ref, self.energy_field, item.energy)
        if activated:
            ctx.ops.append(Activate(self.partition.id))

    def on_inputs(self, ctx: EngineContext) -> None:
        # Preserve reads made by a model/wrapper before entering this hook;
        # exclude the automatically seeded fleet inbox/dirty causes, which each
        # independent control scopes below to avoid quadratic envelopes.
        automatic = {d.dispatch_ref for d in ctx.inbox} | {d.cause for d in ctx.dirty}
        retained_inputs = [cause for cause in ctx.inputs if cause not in automatic]
        self._sync(ctx)
        # Finish integrated outcomes before applying newly latched controls.
        for item in self.fleet.values():
            if item.command is None:
                continue
            ctx.inputs = retained_inputs + [
                dirty.cause for dirty in ctx.dirty if dirty.kind == "activation"
            ]
            ctx.get(item.ref, self.position_field)
            if item.failed:
                ctx.fail(
                    item.command,
                    self._result(reason="energy exhausted", position=item.position),
                )
                item.command = None
            elif item.completed:
                result = self._result(entity=item.ref.id, position=item.position)
                ctx.feedback(item.command, result)
                if item.canceling:
                    ctx.canceled(item.command)
                else:
                    ctx.succeed(item.command, result)
                if not item.braking:
                    ctx.emit(
                        self.arrival_schema,
                        {
                            key: item.ref.id
                            if selector == "$entity"
                            else item.position
                            if selector == "$position"
                            else item.command.payload[selector[9:]]
                            for key, selector in self.arrival_payload.items()
                        },
                        topic=self.topic,
                    )
                item.command = None
        reverse = {schema: kind for kind, schema in self.schemas.items()}
        for delivery in ctx.inbox:
            # Independent controls cite their own actual dispatch, preventing an
            # unrelated fleet inbox from generating quadratic causal envelopes.
            ctx.inputs = retained_inputs + [delivery.dispatch_ref]
            if delivery.message.kind == "cancel":
                cancel_payload = payload(thaw(delivery.message.payload))
                command = ctx.command(str(cancel_payload["command_id"]))
                canceled_item = next(
                    (
                        m
                        for m in self.fleet.values()
                        if m.command is not None and m.command.id == command.id
                    ),
                    None,
                )
                if (
                    canceled_item is None
                    or canceled_item.completed
                    or canceled_item.failed
                ):
                    ctx.decide_cancel(
                        command, delivery, False, "command is no longer integrating"
                    )
                    continue
                ctx.decide_cancel(command, delivery, True)
                item = canceled_item
                item.canceling = item.braking = True
                item.origin = list(item.position)
                item.start_ns = ctx.now.ns
                speed = math.sqrt(sum(v * v for v in item.velocity))
                item.direction = (
                    [v / speed for v in item.velocity] if speed else [0.0] * 3
                )
                item.ramp = speed / self.accel
                item.distance = speed * speed / (2 * self.accel)
                item.cruise = 0.0
                item.target = [
                    p + u * item.distance for p, u in zip(item.position, item.direction)
                ]
            else:
                self._control(
                    ctx,
                    ctx.remember(delivery, payload),
                    reverse[delivery.message.schema_id],
                )


def build(context: EngineBuild) -> Kinematic:
    return Kinematic(context)
