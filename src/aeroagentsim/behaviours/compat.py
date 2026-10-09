"""Pinned legacy interpreters: historical byte semantics, no conversion claim.

`LegacyWorkflowExecutor` and `LegacyThresholdExecutor` are the actual authored
DES machines and sampled-threshold interpreters moved here unchanged from the
retired `aeroagentsim.engines.workflow` / `aeroagentsim.engines.threshold`
modules. They are not reimplementations: comparators, WAL/journal bytes, timer
payloads, receipts and error messages are exactly the historical ones, so any
scenario pinned against those modules replays byte-identically under the
shared runtime through `build_legacy_workflow` / `build_legacy_threshold`.
"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
from dataclasses import field as data_field
from fnmatch import fnmatchcase
from typing import Any, cast

from aerokernel import (
    Activate,
    CommandRequest,
    Dependency,
    EntityRef,
    Partition,
    RelationDependency,
)
from aerokernel.messages import RequestCancel
from aerokernel.operations import CancelTimer, SampleFrame
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Absent, Fact
from aerokernel.values import FrozenValue, thaw, typed_equal

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import ScenarioError

TERMINAL = {"succeeded", "failed", "rejected", "canceled"}
RECEIPTS = TERMINAL | {"submitted", "accepted", "executing", "canceling"}


@dataclass
class Child:
    owner: str
    action: str
    command: str | None = None
    status: str = "pending"
    timeout: str | None = None


@dataclass
class Machine:
    ref: EntityRef
    field: str
    state: str
    states: dict[str, Any]
    revision: int = 0
    timers: dict[int, str] = data_field(default_factory=dict)
    children: dict[str, Child] = data_field(default_factory=dict)
    last_ns: int = -1
    transitions_at_ns: int = 0


class LegacyWorkflowExecutor(ContextEngine):
    """One transition per machine/reaction, first authored enabled transition wins.

    Trigger categories are OR; `guard` is an optional additional AND condition.
    Unmatched inbox events are consumed, not retained for a subsequent state.
    Timers belong to state entries; child identities survive state transitions.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        cfg = build.config
        allowed = {
            "produces",
            "consumes",
            "emits",
            "subscribes",
            "targets",
            "lifecycle",
            "machines",
            "message_lag_ns",
            "correlation_key",
            "simultaneous_policy",
            "max_entry_transitions",
        }
        if set(cfg) - allowed or type(cfg["lifecycle"]) is not bool:
            raise ValueError("workflow.config: unknown keys or non-boolean lifecycle")
        if cfg.get("simultaneous_policy", "authored_order") != "authored_order":
            raise ValueError(
                "workflow.simultaneous_policy: supported policy is authored_order"
            )
        self.correlation = cfg.get("correlation_key")
        if self.correlation is not None and (
            type(self.correlation) is not str or not self.correlation
        ):
            raise ValueError(
                "workflow.correlation_key: nonempty string or null required"
            )
        self.bound = cfg.get("max_entry_transitions", 64)
        if type(self.bound) is not int or self.bound <= 0:
            raise ValueError(
                "workflow.max_entry_transitions: positive integer required"
            )
        produces = tuple(cfg["produces"])
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=produces,
                consumes=tuple(Dependency(f) for f in cfg["consumes"]),
                emits=tuple(cfg["emits"]),
                subscribes=tuple(cfg["subscribes"]),
                message_targets=tuple(cfg["targets"]),
                lifecycle=cfg["lifecycle"],
                message_lag_ns=cfg.get("message_lag_ns", 0),
            ),
            policies=policies(produces),
        )
        self.refs = {r.id: r for r in build.entities}
        self.machines: dict[str, Machine] = {}
        self.pending: deque[Child] = deque()
        self.child_commands: dict[str, Child] = {}
        self.deferred: list[tuple[EntityRef, dict[str, Any]]] = []
        self.bootstrap_phase = False
        created_types = {
            a["entity"]: a["type"]
            for spec in cfg["machines"]
            for state in spec["states"].values()
            for a in state.get("on_enter", [])
            if a.get("kind") == "create"
        }
        types = {**{r.id: r.type_id for r in build.entities}, **created_types}
        for spec in cfg["machines"]:
            if set(spec) != {"entity", "field", "initial", "states"}:
                raise ValueError(
                    "workflow.machines: explicit entity/field/initial/states required"
                )
            ref = self.refs[spec["entity"]]
            slot, state = spec["field"], spec["initial"]
            if (
                ref.id in self.machines
                or slot not in produces
                or state not in spec["states"]
                or slot not in build.owned_fields(ref)
            ):
                raise ValueError(
                    "workflow.machines: duplicate identity or unowned field/unknown initial state"
                )
            if slot in build.initial[ref.id] and build.initial[ref.id][slot] != state:
                raise ValueError("workflow.initial: conflicts with initial facts")
            child_ids: set[str] = set()
            for name, definition in spec["states"].items():
                build.registry.validate(build.registry.field(slot).schema, name)
                if set(definition) - {"on_enter", "transitions"}:
                    raise ValueError("workflow.states: unknown keys")
                for index, a in enumerate(definition.get("on_enter", [])):
                    kind = a["kind"]
                    shapes = {
                        "command": ({"kind", "schema", "target", "payload"}, {"id"}),
                        "emit": ({"kind", "schema", "topic", "payload"}, set()),
                        "set": ({"kind", "entity", "field", "value"}, set()),
                        "create": ({"kind", "entity", "type", "facts"}, set()),
                        "remove": ({"kind", "entity"}, set()),
                        "cancel": ({"kind", "command", "timeout_ns"}, set()),
                    }
                    if kind not in shapes:
                        raise ValueError("workflow.actions.kind: unsupported action")
                    required, optional = shapes[kind]
                    if required - set(a) or set(a) - required - optional:
                        raise ValueError("workflow.actions: unknown or missing keys")
                    if kind in {"command", "emit"}:
                        descriptor = build.registry.message(a["schema"])
                        if (
                            descriptor.kind
                            != ("command" if kind == "command" else "event")
                            or a["schema"] not in cfg["emits"]
                            or a["target" if kind == "command" else "topic"]
                            not in cfg["targets"]
                        ):
                            raise ValueError(
                                "workflow.actions: undeclared schema or target"
                            )
                        build.registry.validate(descriptor.schema, a["payload"])
                        if kind == "command":
                            identity = a.get("id", f"{name}/{index}")
                            if (
                                type(identity) is not str
                                or not identity
                                or identity in child_ids
                            ):
                                raise ValueError(
                                    "workflow.actions.id: unique nonempty child identity required"
                                )
                            child_ids.add(identity)
                    elif kind in {"set", "create"}:
                        type_id = (
                            a["type"] if kind == "create" else types.get(a["entity"])
                        )
                        if type_id is None:
                            raise ValueError(
                                "workflow.actions.entity: declare bootstrap entity or create action type"
                            )
                        build.registry.is_a(type_id, type_id)
                        facts = (
                            {a["field"]: a["value"]} if kind == "set" else a["facts"]
                        )
                        candidate = EntityRef(
                            build.manifest.run_id,
                            build.manifest.epoch,
                            a["entity"],
                            0,
                            type_id,
                        )
                        for f, value in facts.items():
                            field_descriptor = build.registry.field(f)
                            if (
                                not build.registry.is_a(
                                    type_id, field_descriptor.declaring_type
                                )
                                or f not in build.owned_fields(candidate)
                                or (a["entity"] == ref.id and f == slot)
                            ):
                                raise ValueError(
                                    "workflow.actions.field: unowned/inapplicable or conflicts with machine state write"
                                )
                            build.registry.validate(field_descriptor.schema, value)
                        if kind == "create":
                            self._controller(candidate)
                    elif kind == "remove":
                        if a["entity"] not in types:
                            raise ValueError("workflow.remove: undeclared entity/type")
                        self._controller(
                            EntityRef(
                                build.manifest.run_id,
                                build.manifest.epoch,
                                a["entity"],
                                0,
                                types[a["entity"]],
                            )
                        )
                for t in definition.get("transitions", []):
                    if (
                        set(t)
                        - {
                            "to",
                            "event",
                            "receipt",
                            "timer_ns",
                            "predicate",
                            "guard",
                            "correlation_key",
                        }
                        or t["to"] not in spec["states"]
                        or not {"event", "receipt", "timer_ns", "predicate"} & set(t)
                    ):
                        raise ValueError(
                            "workflow.transition: unknown keys/target or no trigger"
                        )
                    if "timer_ns" in t and (
                        type(t["timer_ns"]) is not int or t["timer_ns"] <= 0
                    ):
                        raise ValueError("workflow.timer_ns: positive integer required")
                    if (
                        "event" in t
                        and build.registry.message(t["event"]).kind != "event"
                    ):
                        raise ValueError("workflow.event: event descriptor required")
                    if (
                        "correlation_key" in t
                        and t["correlation_key"] is not None
                        and (
                            type(t["correlation_key"]) is not str
                            or not t["correlation_key"]
                        )
                    ):
                        raise ValueError(
                            "workflow.correlation_key: nonempty string or null required"
                        )
                    if (
                        "receipt" in t
                        and isinstance(t["receipt"], str)
                        and t["receipt"] not in RECEIPTS
                    ):
                        raise ValueError("workflow.receipt: unknown receipt status")
                    if "receipt" in t and not isinstance(t["receipt"], (str, dict)):
                        raise ValueError(
                            "workflow.receipt: explicit status or child selector required"
                        )
                    for key in ("predicate", "guard"):
                        if key in t:
                            p = t[key]
                            if (
                                set(p) != {"entity", "field", "op", "value"}
                                or p["field"] not in cfg["consumes"]
                                or p["op"] not in {"eq", "ne", "gte", "lte", "gt", "lt"}
                            ):
                                raise ValueError(
                                    "workflow.predicate: declared consumed selector and comparison required"
                                )
                            build.registry.validate(
                                build.registry.field(p["field"]).schema, p["value"]
                            )
            # Cross-state references can target later-authored child actions.
            for definition in spec["states"].values():
                for a in definition.get("on_enter", []):
                    if a["kind"] == "cancel" and (
                        a["command"] not in child_ids
                        or type(a["timeout_ns"]) is not int
                        or a["timeout_ns"] <= 0
                    ):
                        raise ValueError(
                            "workflow.cancel: declared child and positive cleanup timeout required"
                        )
                for t in definition.get("transitions", []):
                    if "receipt" in t and isinstance(t["receipt"], dict):
                        r = t["receipt"]
                        if (
                            set(r) != {"status", "children", "policy"}
                            or r["status"] not in RECEIPTS
                            or r["policy"] not in {"all", "any"}
                            or not isinstance(r["children"], list)
                            or any(
                                type(identity) is not str for identity in r["children"]
                            )
                            or len(r["children"]) != len(set(r["children"]))
                            or not r["children"]
                            or set(r["children"]) - child_ids
                        ):
                            raise ValueError(
                                "workflow.receipt: explicit all/any declared children required"
                            )
            self.machines[ref.id] = Machine(ref, slot, state, spec["states"])

    def _controller(self, ref: EntityRef) -> None:
        candidates = [
            r
            for r in self.build.manifest.lifecycle
            if self.build.registry.is_a(ref.type_id, r.type_id)
            and fnmatchcase(ref.id, r.id_pattern)
        ]
        if not candidates or not self.partition.lifecycle:
            raise ValueError("workflow.lifecycle: undeclared create/remove authority")
        priority = max(r.priority for r in candidates)
        winners = [r.partition for r in candidates if r.priority == priority]
        if winners != [self.build.id]:
            raise ValueError("workflow.lifecycle: not selected controller")

    def _ref(self, ctx: EngineContext, identity: Any) -> EntityRef:
        if isinstance(identity, dict):
            candidates = [EntityRef.from_data(identity["$ref"])]
        elif self.bootstrap_phase:
            return self.refs[identity]
        else:
            candidates = [
                r
                for r in ctx.view._store.lives
                if r.id == identity
                and ctx.view._store.lives[r].created.index <= ctx.view.cut.index
            ]
        active = [
            r for r in candidates if ctx.view._store.alive(r, ctx.view.cut, ctx.now)
        ]
        if len(active) != 1:
            raise ValueError(
                f"workflow.entity: no unique committed live generation for {identity}"
            )
        return active[0]

    def _enter(self, ctx: EngineContext, m: Machine) -> None:
        for i, t in enumerate(m.states[m.state].get("transitions", [])):
            if "timer_ns" in t:
                m.timers[i] = ctx.wake_at(
                    ctx.now.ns + t["timer_ns"],
                    {"machine": m.ref.id, "revision": m.revision, "transition": i},
                )
        self._actions(ctx, m)
        if any("predicate" in t for t in m.states[m.state].get("transitions", [])):
            ctx.ops.append(Activate(self.partition.id))

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        self.bootstrap_phase = True
        try:
            for m in self.machines.values():
                if m.field not in self.build.initial[m.ref.id]:
                    ctx.set(m.ref, m.field, m.state)
                self._enter(ctx, m)
        finally:
            self.bootstrap_phase = False

    def _actions(self, ctx: EngineContext, m: Machine) -> None:
        for i, a in enumerate(m.states[m.state].get("on_enter", [])):
            kind = a["kind"]
            if kind == "command":
                identity = a.get("id", f"{m.state}/{i}")
                if (
                    identity in m.children
                    and m.children[identity].status not in TERMINAL
                ):
                    raise ValueError("workflow.command: child is still active")
                child = Child(m.ref.id, identity)
                m.children[identity] = child
                ctx.submit(
                    CommandRequest(a["schema"], a["target"], ctx.now, a["payload"])
                )
                self.pending.append(child)
            elif kind == "emit":
                ctx.emit(a["schema"], a["payload"], topic=a["topic"])
            elif kind == "set":
                ctx.set(self._ref(ctx, a["entity"]), a["field"], a["value"])
            elif kind == "create":
                lives = [
                    r
                    for r in ctx.view._store.lives
                    if r.id == a["entity"]
                    and ctx.view._store.lives[r].created.index <= ctx.view.cut.index
                ]
                if any(ctx.view._store.alive(r, ctx.view.cut, ctx.now) for r in lives):
                    raise ValueError("workflow.create: entity ID is already live")
                generation = max((r.generation for r in lives), default=-1) + 1
                ref = EntityRef(
                    self.build.manifest.run_id,
                    self.build.manifest.epoch,
                    a["entity"],
                    generation,
                    a["type"],
                )
                ctx.create(ref)
                self.deferred.append((ref, a["facts"]))
                ctx.ops.append(Activate(self.partition.id))
            elif kind == "remove":
                ctx.remove(self._ref(ctx, a["entity"]))
            elif kind == "cancel":
                cancel_child = m.children.get(a["command"])
                if cancel_child is None or cancel_child.command is None:
                    raise ValueError("workflow.cancel: child not yet correlated")
                current = ctx.view.action(cancel_child.command)
                if current.status in TERMINAL:
                    continue
                assert current.head is not None
                ctx.ops.append(RequestCancel(cancel_child.command, (current.head,)))
                cancel_child.timeout = ctx.wake_at(
                    ctx.now.ns + a["timeout_ns"], {"cancel": cancel_child.command}
                )

    def _predicate(self, ctx: EngineContext, p: dict[str, Any]) -> bool | None:
        observed = ctx.get(self._ref(ctx, p["entity"]), p["field"])
        if isinstance(observed, Absent):
            return None  # retain unknown; it cannot enable a transition
        value: Any = thaw(observed)
        if p["op"] == "eq":
            return typed_equal(value, p["value"])
        if p["op"] == "ne":
            return not typed_equal(value, p["value"])
        if type(value) not in (int, float) or type(p["value"]) not in (int, float):
            raise TypeError(
                "workflow.predicate: ordered comparison requires numeric inputs"
            )
        if p["op"] == "gte":
            return bool(value >= p["value"])
        if p["op"] == "lte":
            return bool(value <= p["value"])
        if p["op"] == "gt":
            return bool(value > p["value"])
        return bool(value < p["value"])

    def on_inputs(self, ctx: EngineContext) -> None:
        for ref, facts in self.deferred:
            for f, value in facts.items():
                ctx.set(ref, f, value)
        self.deferred.clear()
        events = [(d.message.schema_id, thaw(d.message.payload)) for d in ctx.inbox]
        receipts: list[tuple[str, str, str]] = []
        timers: list[dict[str, Any]] = []
        fired_cancellations = {
            cast(dict[str, Any], thaw(d.payload))["cancel"]
            for d in ctx.dirty
            if d.kind == "timer" and "cancel" in cast(dict[str, Any], thaw(d.payload))
        }
        for cid in fired_cancellations:
            self.child_commands[cid].timeout = None
        for dirty in ctx.dirty:
            if dirty.kind == "receipt":
                data = cast(dict[str, Any], thaw(dirty.payload))
                if not isinstance(data, dict):
                    raise TypeError("workflow.receipt: typed receipt required")
                cid, status = data["command_id"], data["status"]
                if status == "submitted":
                    child = self.pending.popleft()
                    child.command = cid
                    self.child_commands[cid] = child
                child = self.child_commands[cid]
                child.status = status
                receipts.append((child.owner, child.action, status))
                if status in TERMINAL and child.timeout is not None:
                    ctx.ops.append(CancelTimer(child.timeout))
                    child.timeout = None
            elif dirty.kind == "timer":
                data = cast(dict[str, Any], thaw(dirty.payload))
                if not isinstance(data, dict):
                    raise TypeError("workflow.timer: typed payload required")
                if "cancel" in data:
                    child = self.child_commands[data["cancel"]]
                    child.timeout = None
                    if ctx.view.action(data["cancel"]).status not in TERMINAL:
                        raise RuntimeError("workflow.cancel: cleanup deadline exceeded")
                else:
                    timers.append(data)
                    machine = self.machines[data["machine"]]
                    if data["revision"] == machine.revision:
                        machine.timers.pop(data["transition"], None)
        for m in self.machines.values():
            for i, t in enumerate(m.states[m.state].get("transitions", [])):
                correlation = t.get("correlation_key", self.correlation)
                matches = "event" in t and any(
                    schema == t["event"]
                    and (
                        correlation is None
                        or isinstance(data, dict)
                        and data.get(correlation) == m.ref.id
                    )
                    for schema, data in events
                )
                if "receipt" in t:
                    r = t["receipt"]
                    if isinstance(r, str):
                        matches |= any(
                            owner == m.ref.id and status == r
                            for owner, _, status in receipts
                        )
                    else:
                        states = [
                            m.children.get(identity) for identity in r["children"]
                        ]
                        selected = [
                            child is not None and child.status == r["status"]
                            for child in states
                        ]
                        matches |= (
                            all(selected) if r["policy"] == "all" else any(selected)
                        )
                if "timer_ns" in t:
                    matches |= any(
                        data["machine"] == m.ref.id
                        and data["revision"] == m.revision
                        and data["transition"] == i
                        for data in timers
                    )
                if "predicate" in t:
                    matches |= self._predicate(ctx, t["predicate"]) is True
                if (
                    not matches
                    or "guard" in t
                    and self._predicate(ctx, t["guard"]) is not True
                ):
                    continue
                if m.last_ns != ctx.now.ns:
                    m.last_ns = ctx.now.ns
                    m.transitions_at_ns = 0
                m.transitions_at_ns += 1
                if m.transitions_at_ns > self.bound:
                    raise RuntimeError(
                        "workflow.transition: bounded same-time entry limit exceeded"
                    )
                for timer in m.timers.values():
                    ctx.ops.append(CancelTimer(timer))
                m.timers.clear()
                m.state = t["to"]
                m.revision += 1
                ctx.set(m.ref, m.field, m.state)
                self._enter(ctx, m)
                break
        # Keep terminal child summaries on machines; discard command lookup after cleanup.
        for command, child in list(self.child_commands.items()):
            if child.status in TERMINAL:
                self.child_commands.pop(command, None)


class LegacyThresholdExecutor(ContextEngine):
    """Compare declared scalar/enum/vector slots or graph presence, never infer pose."""

    def __init__(self, build: EngineBuild) -> None:
        cfg = build.config
        allowed = {
            "field",
            "index",
            "value",
            "event",
            "topic",
            "context",
            "ast",
            "parameters",
            "native_reference",
            "event_payload",
        }
        if set(cfg) - allowed:
            raise ValueError("threshold.config: unknown keys")
        context = cfg["context"]
        matches = [s for s in build.manifest.samples if s.context_id == context]
        if len(matches) != 1:
            raise ScenarioError(
                f"engines.{build.id}.config.context: sampled context {context!r} "
                f"requires exactly one bindings.samples entry; found {len(matches)}"
            )
        self.spec = matches[0]
        if self.spec.partition != build.id:
            raise ScenarioError(
                f"engines.{build.id}.config.context: sampled context {context!r} "
                f"is bound to engine {self.spec.partition!r}"
            )
        ast = cfg["ast"]
        if set(ast) != {"op", "args"} or ast["op"] not in {
            "gte",
            "lte",
            "gt",
            "lt",
            "eq",
            "ne",
            "exists",
        }:
            raise ValueError("threshold.ast: unsupported sampled comparison profile")
        self.op = ast["op"]
        if not isinstance(ast["args"], list) or len(ast["args"]) != (
            1 if self.op == "exists" else 2
        ):
            raise ValueError("threshold.ast.args: incorrect selector/parameter count")
        left = ast["args"][0]
        self.relation: str | None = left.get("relation")
        self.field: str | None = None
        self.path: list[int | str] = []
        self.direction = left.get("direction", "outgoing")
        if self.relation is not None:
            if (
                set(left) - {"op", "relation", "role", "direction"}
                or left.get("op", "relation") != "relation"
                or left.get("role") != "subject"
                or {"field", "index"} & set(cfg)
                or self.direction not in {"outgoing", "incoming"}
            ):
                raise ValueError("threshold.ast: unsupported relation selector")
            build.registry.relation(self.relation)
        else:
            if (
                set(left) - {"op", "field", "role", "path"}
                or left.get("op", "field") != "field"
                or left.get("role") != "subject"
            ):
                raise ValueError("threshold.ast: unsupported field selector")
            self.field = left["field"]
            self.path = left.get("path", [])
            if not isinstance(self.path, list):
                raise ValueError("threshold.ast.path: explicit selector path required")
            schema = build.registry.field(self.field).schema
            for slot in self.path:
                if (
                    schema.get("type") in {"vector", "array"}
                    and type(slot) is int
                    and slot >= 0
                ):
                    if schema.get("type") == "vector" and slot >= schema["length"]:
                        raise ValueError(
                            "threshold.ast.path: vector index out of bounds"
                        )
                    schema = schema["items"]
                elif (
                    schema.get("type") == "record"
                    and type(slot) is str
                    and slot in schema["members"]
                ):
                    schema = schema["members"][slot]
                else:
                    raise ValueError(
                        "threshold.ast.path: selector incompatible with schema"
                    )
            if (
                cfg.get("field", self.field) != self.field
                or "index" in cfg
                and self.path != [cfg["index"]]
            ):
                raise ValueError(
                    "threshold.field/index: redundant selector does not match AST"
                )
        if (
            self.relation is None
            and self.op in {"gte", "lte", "gt", "lt"}
            and schema.get("type") not in {"integer", "number"}
        ):
            raise ValueError("threshold.ast: ordered selector must have numeric schema")
        self.threshold: Any = None
        if self.op != "exists":
            right = ast["args"][1]
            if (
                set(right) - {"op", "parameter"}
                or right.get("op", "parameter") != "parameter"
            ):
                raise ValueError("threshold.ast: parameter node required")
            self.threshold = cfg["parameters"][right["parameter"]]
            if "value" in cfg and not typed_equal(cfg["value"], self.threshold):
                raise ValueError(
                    "threshold.value: redundant value does not match parameter"
                )
            if self.op in {"gte", "lte", "gt", "lt"} and type(self.threshold) not in (
                int,
                float,
            ):
                raise ValueError(
                    "threshold.parameters: numeric comparison requires numeric input"
                )
        if thaw(cast(FrozenValue, self.spec.parameters)) != cfg["parameters"]:
            raise ValueError(
                "threshold.parameters: must match pinned sample parameters"
            )
        self.schema, self.topic = cfg["event"], cfg["topic"]
        self.payload = cfg.get(
            "event_payload",
            {"entity": "$entity", "from_ns": "$from_ns", "to_ns": "$to_ns"},
        )
        descriptor = build.registry.message(self.schema)
        if descriptor.kind != "event" or not isinstance(self.payload, dict):
            raise ValueError("threshold.event_payload: typed event payload required")
        members = descriptor.schema.get("members", {})
        if set(self.payload) - set(members) or set(
            descriptor.schema.get("required", ())
        ) - set(self.payload):
            raise ValueError("threshold.event_payload: schema member coverage mismatch")
        tokens = {
            "$entity": "string",
            "$subject_ref": "ref",
            "$from_ns": "integer",
            "$to_ns": "integer",
        }
        for key, value in self.payload.items():
            if isinstance(value, str) and value.startswith("$"):
                if value not in tokens or members[key].get("type") != tokens[value]:
                    raise ValueError(
                        "threshold.event_payload: incompatible selector/schema"
                    )
            else:
                build.registry.validate(members[key], value)
        super().__init__(
            Partition(
                build.id,
                build.id,
                consumes=() if self.field is None else (Dependency(self.field),),
                relation_consumes=()
                if self.relation is None
                else (RelationDependency(self.relation),),
                emits=(self.schema,),
                message_targets=(self.topic,),
                features=("sampled",)
                if self.relation is None
                else ("sampled", "relations"),
            )
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        # Empty graphs are real inputs: establish a settled initial relation frame.
        if self.relation is not None:
            ctx.ops.append(Activate(self.partition.id))

    def _value(
        self, ctx: EngineContext, role: str, ref: Any
    ) -> tuple[dict[str, Any] | None, Any]:
        if self.relation is not None:
            edges = [
                edge
                for edge in ctx.relations(self.relation)
                if (edge.source if self.direction == "outgoing" else edge.target) == ref
            ]
            if any(
                edge.producer != self.spec.sources[role]
                or (edge.acquired.clock_id, edge.acquired.mapping_id)
                != self.spec.clocks[role]
                for edge in edges
            ):
                return {
                    "status": "invalid_input",
                    "reason": "relation source or clock mismatch",
                }, None
            return None, bool(edges) if self.op == "exists" else len(edges)
        assert self.field is not None
        ctx.get(ref, self.field)
        fact = ctx.view.field((ref, self.field), ctx.now)
        if not isinstance(fact, Fact):
            return {
                "status": "required_input",
                "reason": "no current valid selected field",
            }, None
        if (
            fact.producer != self.spec.sources[role]
            or (fact.acquired.clock_id, fact.acquired.mapping_id)
            != self.spec.clocks[role]
        ):
            return {
                "status": "invalid_input",
                "reason": "source or native clock mismatch",
            }, None
        value: Any = thaw(fact.value)
        for slot in self.path:
            value = value[slot]
        return None, value

    def on_inputs(self, ctx: EngineContext) -> None:
        frames = ctx.view.sample_frames(self.spec.context_id)
        previous = frames[-1] if frames else None
        prior = (
            thaw(cast(FrozenValue, previous.frame.result))
            if previous is not None
            else None
        )
        if previous is not None:
            ctx.inputs.append(previous.version)
        states: dict[str, Any] = {}
        entered: list[tuple[Any, int]] = []
        for role, ref in self.spec.bindings.items():
            diagnostic, value = self._value(ctx, role, ref)
            if diagnostic is not None:
                states[role] = diagnostic
                continue
            if self.op in {"gte", "lte", "gt", "lt"} and type(value) not in (
                int,
                float,
            ):
                raise TypeError(
                    "threshold.field: ordered comparison requires numeric selected value"
                )
            if self.op == "eq":
                current = typed_equal(value, self.threshold)
            elif self.op == "ne":
                current = not typed_equal(value, self.threshold)
            elif self.op == "gte":
                current = value >= self.threshold
            elif self.op == "lte":
                current = value <= self.threshold
            elif self.op == "gt":
                current = value > self.threshold
            elif self.op == "lt":
                current = value < self.threshold
            else:
                current = bool(value) if self.relation is not None else True
            states[role] = {"status": "known", "value": current}
            before = (
                cast(dict[str, Any], prior["states"]).get(role)
                if isinstance(prior, dict)
                else None
            )
            if (
                previous is not None
                and isinstance(before, dict)
                and before.get("status") == "known"
                and before.get("value") is False
                and current
                and previous.frame.physical_ns < ctx.now.ns
            ):
                entered.append((ref, previous.frame.physical_ns))
        ctx.ops.append(
            SampleFrame(
                self.spec.context_id,
                ctx.now.ns,
                ctx.view.cut,
                self.spec.bindings,
                self.spec.clocks,
                {"states": states},
                self.spec.sources,
                tuple(ctx.inputs),
            )
        )
        for ref, previous_ns in entered:
            values = {
                "$entity": ref.id,
                "$subject_ref": {"$ref": ref.to_data()},
                "$from_ns": previous_ns,
                "$to_ns": ctx.now.ns,
            }
            ctx.emit(
                self.schema,
                {
                    key: values[value]
                    if isinstance(value, str) and value in values
                    else value
                    for key, value in self.payload.items()
                },
                topic=self.topic,
            )


def compile_workflow(config: dict[str, Any]) -> dict[str, Any]:
    """Descriptive IR for the legacy workflow config, as authored (no semantics claimed).

    Keys are validated for presence only; comparator strings are copied verbatim
    and interpreted exclusively by `LegacyWorkflowExecutor`.
    """
    return _workflow_ir(config)


def _workflow_ir(config: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "produces",
        "consumes",
        "emits",
        "subscribes",
        "targets",
        "lifecycle",
        "machines",
    ):
        if key not in config:
            raise ValueError(f"workflow.config: missing required key {key!r}")
    machines = []
    for spec in config["machines"]:
        states = [
            {
                "state": name,
                "actions": [
                    {"kind": action["kind"], "id": action.get("id")}
                    for action in definition.get("on_enter", [])
                ],
                "transitions": [
                    {
                        "to": transition["to"],
                        "trigger": next(
                            (
                                category
                                for category in (
                                    "event",
                                    "receipt",
                                    "timer_ns",
                                    "predicate",
                                )
                                if category in transition
                            ),
                            None,
                        ),
                        "comparator": (
                            transition.get("predicate", {}).get("op")
                            if isinstance(transition.get("predicate"), dict)
                            else None
                        ),
                    }
                    for transition in definition.get("transitions", [])
                ],
            }
            for name, definition in spec["states"].items()
        ]
        machines.append(
            {
                "entity": spec["entity"],
                "field": spec["field"],
                "initial": spec["initial"],
                "states": states,
            }
        )
    return {
        "kind": "legacy_workflow/v1",
        "config": copy.deepcopy(config),
        "correlation_key": config.get("correlation_key"),
        "max_entry_transitions": config.get("max_entry_transitions", 64),
        "simultaneous_policy": config.get("simultaneous_policy", "authored_order"),
        "machines": machines,
    }


def compile_threshold(config: dict[str, Any]) -> dict[str, Any]:
    """Descriptive IR for the legacy sampled threshold config, as authored.

    `comparator` copies the configured AST op verbatim; no comparator is
    reimplemented here — evaluation stays with `LegacyThresholdExecutor`.
    """
    return _threshold_ir(config)


def _threshold_ir(config: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "context",
        "ast",
        "event",
        "topic",
        "parameters",
    ):
        if key not in config:
            raise ValueError(f"threshold.config: missing required key {key!r}")
    ast = config["ast"]
    if (
        not isinstance(ast, dict)
        or set(ast) != {"op", "args"}
        or ast["op"] not in {"eq", "ne", "gt", "gte", "lt", "lte", "exists"}
    ):
        raise ValueError("threshold.ast: unsupported sampled comparison profile")
    args = ast["args"]
    if (
        not isinstance(args, list)
        or len(args) != (1 if ast["op"] == "exists" else 2)
        or any(not isinstance(node, dict) for node in args)
    ):
        raise ValueError("threshold.ast.args: explicit selector/parameter required")
    left = args[0]
    if ("field" in left) == ("relation" in left):
        raise ValueError("threshold.ast: select exactly one field or relation")
    right = args[1] if len(args) > 1 else None
    if right is not None and "parameter" not in right:
        raise ValueError("threshold.ast: explicit parameter required")
    return {
        "kind": "legacy_threshold/v1",
        "config": copy.deepcopy(config),
        "context": config["context"],
        "op": ast["op"],
        "comparator": ast["op"],
        "selector": (
            left.get("field", left.get("relation")) if isinstance(left, dict) else None
        ),
        "path": left.get("path", []) if isinstance(left, dict) else [],
        "direction": (
            left.get("direction", "outgoing") if isinstance(left, dict) else None
        ),
        "parameter": right["parameter"] if right is not None else None,
        "event": config["event"],
        "topic": config["topic"],
    }
