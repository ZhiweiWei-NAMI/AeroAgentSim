"""Declarative, typed DES state machines with partition-owned timers and actions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from aerokernel import (
    Activate,
    CommandRequest,
    Dependency,
    EntityRef,
    Partition,
)
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Absent
from aerokernel.values import thaw, typed_equal

from aeroagentsim.platform.plugins import EngineBuild

from .common import bootstrap_owned, policies


@dataclass
class Machine:
    ref: EntityRef
    field: str
    state: str
    states: dict[str, Any]
    revision: int = 0


class Workflow(ContextEngine):
    """Transitions consume actual events/receipts/timers/selected field versions.

    One transition per machine per reaction avoids duplicate field writes. Timers
    carry state revisions; an obsolete timer cannot advance a later state. Child
    command IDs are learned only from kernel-routed submitted receipts, in the
    same FIFO order as this engine's authored command operations.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        config = build.config
        produces = tuple(config["produces"])
        consumes = tuple(config["consumes"])
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=produces,
                consumes=tuple(Dependency(field) for field in consumes),
                emits=tuple(config["emits"]),
                subscribes=tuple(config["subscribes"]),
                message_targets=tuple(config["targets"]),
                lifecycle=config["lifecycle"],
                message_lag_ns=config.get("message_lag_ns", 0),
            ),
            policies=policies(produces),
        )
        self.refs = {ref.id: ref for ref in build.entities}
        self.machines: dict[str, Machine] = {}
        self.pending: deque[tuple[str, int]] = deque()
        self.commands_to_machine: dict[str, tuple[str, int]] = {}
        self.deferred: list[tuple[EntityRef, dict[str, Any]]] = []
        for spec in config["machines"]:
            ref = self.refs[spec["entity"]]
            field, state = spec["field"], spec["initial"]
            if ref.id in self.machines:
                raise ValueError(
                    f"workflow.machines: duplicate machine entity {ref.id}"
                )
            if field not in produces or state not in spec["states"]:
                raise ValueError(
                    f"workflow.machines.{ref.id}: undeclared field or initial state"
                )
            if field in build.initial[ref.id] and build.initial[ref.id][field] != state:
                raise ValueError(
                    f"workflow.machines.{ref.id}.initial: conflicts with initial facts"
                )
            for name, definition in spec["states"].items():
                build.registry.validate(build.registry.field(field).schema, name)
                for transition in definition.get("transitions", []):
                    if transition["to"] not in spec["states"]:
                        raise ValueError(
                            f"workflow.{ref.id}.states.{name}: unknown transition target"
                        )
                    triggers = {"event", "receipt", "timer_ns", "predicate"} & set(
                        transition
                    )
                    if not triggers:
                        raise ValueError(
                            f"workflow.{ref.id}.states.{name}: transition needs a trigger"
                        )
                    if "timer_ns" in transition and (
                        type(transition["timer_ns"]) is not int
                        or transition["timer_ns"] <= 0
                    ):
                        raise ValueError(
                            f"workflow.{ref.id}.timer_ns: positive integer required"
                        )
                    if "event" in transition:
                        descriptor = build.registry.message(transition["event"])
                        if descriptor.kind != "event":
                            raise ValueError(
                                "workflow.event: requires an event descriptor"
                            )
                    if (
                        "predicate" in transition
                        and transition["predicate"]["field"] not in consumes
                    ):
                        raise ValueError(
                            "workflow.predicate.field: declare a consumed field"
                        )
                for action in definition.get("on_enter", []):
                    if action["kind"] in {"command", "emit"}:
                        descriptor = build.registry.message(action["schema"])
                        if action["schema"] not in config["emits"]:
                            raise ValueError(
                                "workflow.actions.schema: declare the emitted schema"
                            )
                        expected = "command" if action["kind"] == "command" else "event"
                        if descriptor.kind != expected:
                            raise ValueError(
                                f"workflow.actions.schema: requires {expected} descriptor"
                            )
                        build.registry.validate(descriptor.schema, action["payload"])
            self.machines[ref.id] = Machine(ref, field, state, spec["states"])

    def _schedule(self, ctx: EngineContext, machine: Machine) -> None:
        for index, transition in enumerate(
            machine.states[machine.state].get("transitions", [])
        ):
            if "timer_ns" in transition:
                ctx.wake_at(
                    ctx.now.ns + transition["timer_ns"],
                    {
                        "machine": machine.ref.id,
                        "revision": machine.revision,
                        "transition": index,
                    },
                )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        for machine in self.machines.values():
            if machine.field not in self.build.initial[machine.ref.id]:
                ctx.set(machine.ref, machine.field, machine.state)
            self._schedule(ctx, machine)
            self._actions(ctx, machine)

    def _actions(self, ctx: EngineContext, machine: Machine) -> None:
        for action in machine.states[machine.state].get("on_enter", []):
            kind = action["kind"]
            if kind == "command":
                ctx.submit(
                    CommandRequest(
                        action["schema"], action["target"], ctx.now, action["payload"]
                    )
                )
                self.pending.append((machine.ref.id, machine.revision))
            elif kind == "emit":
                ctx.emit(action["schema"], action["payload"], topic=action["topic"])
            elif kind == "set":
                ctx.set(self.refs[action["entity"]], action["field"], action["value"])
            elif kind == "create":
                entity_id = action["entity"]
                previous = self.refs.get(entity_id)
                generation = 0 if previous is None else previous.generation + 1
                ref = EntityRef(
                    self.build.manifest.run_id,
                    self.build.manifest.epoch,
                    entity_id,
                    generation,
                    action["type"],
                )
                self.refs[entity_id] = ref
                ctx.create(ref)
                self.deferred.append((ref, action["facts"]))
                ctx.ops.append(Activate(self.partition.id))
            elif kind == "remove":
                ctx.remove(self.refs[action["entity"]])
            else:
                raise ValueError(f"workflow.actions.kind: unsupported {kind!r}")

    def _predicate(self, ctx: EngineContext, spec: dict[str, Any]) -> bool:
        value = ctx.get(self.refs[spec["entity"]], spec["field"])
        if isinstance(value, Absent):
            raise TypeError(
                f"workflow.predicate: no valid source for {spec['entity']}.{spec['field']}"
            )
        observed = thaw(value)
        if spec["op"] == "eq":
            return typed_equal(observed, spec["value"])
        if type(observed) not in (int, float) or type(spec["value"]) not in (
            int,
            float,
        ):
            raise TypeError(
                "workflow.predicate: ordered comparison requires numeric inputs"
            )
        assert isinstance(observed, (int, float))
        if spec["op"] == "gte":
            return bool(observed >= spec["value"])
        if spec["op"] == "lte":
            return bool(observed <= spec["value"])
        raise ValueError(f"workflow.predicate.op: unsupported {spec['op']!r}")

    def on_inputs(self, ctx: EngineContext) -> None:
        for ref, facts in self.deferred:
            for field, value in facts.items():
                ctx.set(ref, field, value)
        self.deferred.clear()
        events: list[tuple[str, Any]] = [
            (d.message.schema_id, thaw(d.message.payload)) for d in ctx.inbox
        ]
        receipts: list[tuple[str, str, int]] = []
        timers: list[dict[str, Any]] = []
        for dirty in ctx.dirty:
            if dirty.kind == "receipt":
                data = thaw(dirty.payload)
                if not isinstance(data, dict):
                    raise TypeError(
                        "workflow.receipt: kernel receipt payload must be a record"
                    )
                command_id, status = str(data["command_id"]), str(data["status"])
                if status == "submitted":
                    if not self.pending:
                        raise ValueError(
                            "workflow.receipt: submitted receipt has no authored command"
                        )
                    self.commands_to_machine[command_id] = self.pending.popleft()
                if command_id not in self.commands_to_machine:
                    raise ValueError(
                        f"workflow.receipt: uncorrelated command {command_id}"
                    )
                owner, revision = self.commands_to_machine[command_id]
                receipts.append((owner, status, revision))
            elif dirty.kind == "timer":
                data = thaw(dirty.payload)
                if not isinstance(data, dict):
                    raise TypeError("workflow.timer: expected machine/revision payload")
                timers.append(data)
        for machine in self.machines.values():
            transitions = machine.states[machine.state].get("transitions", [])
            for index, transition in enumerate(transitions):
                matches = False
                if "event" in transition:
                    matches = any(
                        schema == transition["event"]
                        and (
                            not isinstance(data, dict)
                            or "machine" not in data
                            or data["machine"] == machine.ref.id
                        )
                        for schema, data in events
                    )
                elif "receipt" in transition:
                    matches = any(
                        owner == machine.ref.id
                        and revision == machine.revision
                        and status == transition["receipt"]
                        for owner, status, revision in receipts
                    )
                elif "timer_ns" in transition:
                    matches = any(
                        data["machine"] == machine.ref.id
                        and data["revision"] == machine.revision
                        and data["transition"] == index
                        for data in timers
                    )
                elif "predicate" in transition:
                    matches = any(
                        d.key is not None
                        and d.key[1] == transition["predicate"]["field"]
                        for d in ctx.dirty
                    )
                if matches and (
                    "predicate" not in transition
                    or self._predicate(ctx, transition["predicate"])
                ):
                    machine.state = transition["to"]
                    machine.revision += 1
                    ctx.set(machine.ref, machine.field, machine.state)
                    self._actions(ctx, machine)
                    self._schedule(ctx, machine)
                    break


def build(context: EngineBuild) -> Workflow:
    return Workflow(context)
