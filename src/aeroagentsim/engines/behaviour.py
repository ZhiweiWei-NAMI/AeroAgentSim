"""One kernel-coordinated executor for compiled, automatically bound chains."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, cast

from aerokernel import (
    Activate,
    CommandRequest,
    Dependency,
    EntityRef,
    Instant,
    Interval,
    Partition,
    Stamp,
)
from aerokernel.engine import Engine
from aerokernel.ids import ItemRef, LocalCause
from aerokernel.messages import RequestCancel
from aerokernel.operations import CancelTimer, SampleFrame, ScheduleTimer
from aerokernel.relations import RelationDependency
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Absent, Fact
from aerokernel.values import FrozenValue, thaw, typed_equal

from aeroagentsim.behaviours.bindings import stable_id, tuples
from aeroagentsim.behaviours.compiler import compile_package
from aeroagentsim.behaviours.evaluation import Evaluator, Truth
from aeroagentsim.behaviours.records import EVENTS, INJECT, PREFIX, TYPE, instant
from aeroagentsim.platform.plugins import EngineBuild

from .common import bootstrap_owned, policies
from .predicate import Predicate, nodes

TERMINAL = {"succeeded", "failed", "rejected", "canceled"}


def expression_reads(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        if "field" in value and "role" in value:
            return [(value["role"], value["field"])]
        return [read for child in value.values() for read in expression_reads(child)]
    if isinstance(value, list):
        return [read for child in value for read in expression_reads(child)]
    return []


@dataclass
class Child:
    action: str
    schema: str | None = None
    command: str | None = None
    status: str = "pending"
    result: Any = None
    receipt: object = None
    deadline: str | None = None
    cancel_requested: bool = False


@dataclass
class Instance:
    ref: EntityRef
    package: dict[str, Any]
    digest: str
    binding: dict[str, Any]
    template: str
    roles: dict[str, EntityRef]
    variables: dict[str, Any]
    state: str
    revision: int = 0
    entry_revision: int = 0
    status: str = "dormant"
    children: dict[str, Child] = field(default_factory=dict)
    timers: dict[str, tuple[str, int]] = field(default_factory=dict)
    last_ns: int = -1
    transitions_at_ns: int = 0
    creations: int = 0
    published: bool = False
    continued: str | None = None
    prior_record: dict[str, Any] | None = None
    trigger: dict[str, Any] = field(default_factory=dict)
    deadline: str | None = None
    events: set[str] = field(default_factory=set)
    predicate_contexts: dict[str, str] = field(default_factory=dict)
    required_inputs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def chain(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.package["chains"][self.template])


class Behaviour(ContextEngine):
    """Immutable invocation cuts, explicit causes, no independent scheduler."""

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        cfg = build.config
        self.packages = [
            compile_package(
                p.get("document", p), build, source=p.get("source", "<inline>")
            )
            for p in cfg["packages"]
        ]
        self.instances: dict[str, Instance] = {}
        self.pending: deque[tuple[Instance, Child]] = deque()
        self.commands_to_children: dict[str, tuple[Instance, Child]] = {}
        self.deferred: list[tuple[EntityRef, dict[str, Any]]] = []
        self.evaluator = Evaluator()
        self.capabilities = cfg.get("capabilities", {})
        self.bind_keys: set[str] = set()
        self.bound_once = False
        self.selector_fields: set[str] = set()
        self.authored_events: set[str] = set()
        self.fired: set[tuple[str, str, object]] = set()
        self.boot_edges: list[dict[str, Any]] = []
        self.sampled: dict[str, dict[str, Any]] = {}
        self.sampled_causes: dict[str, ItemRef] = {}
        produces = set(cfg.get("produces", ())) | {
            PREFIX + name
            for name in (
                "template_id",
                "binding_id",
                "roles",
                "state",
                "revision",
                "status",
                "children",
            )
        }
        consumes = set(cfg.get("consumes", ()))
        relations = set(cfg.get("relation_consumes", ()))
        relation_produces = set(cfg.get("relation_produces", ()))
        relation_produces.update(
            r.relation_id
            for r in build.manifest.relation_rules
            if r.partition == build.id
        )
        emits = set(EVENTS) | set(cfg.get("emits", ()))
        targets = {PREFIX + "records", build.id} | set(cfg.get("targets", ()))
        subscribes = {PREFIX + "records"} | set(cfg.get("subscribes", ()))
        commands = {INJECT}
        for ir in self.packages:
            p = ir.document
            for deps in ir.dependencies.values():
                consumes.update(f for _, f in deps)
            for binding in p.get("bindings", []):
                for selector in binding["match"].values():
                    if "field" in selector:
                        consumes.add(selector["field"])
                        self.selector_fields.add(selector["field"])
                    if "relation" in selector:
                        relations.add(selector["relation"])
                if "episode_field" in binding:
                    consumes.add(binding["episode_field"])
                    self.selector_fields.add(binding["episode_field"])
                for value in binding.get("variables", {}).values():
                    for _, source_field in expression_reads(value):
                        consumes.add(source_field)
                        self.selector_fields.add(source_field)
            for definition in p.get("predicates", {}).values():
                relations.update(
                    n["relation"]
                    for n in nodes(definition["expression"])
                    if "relation" in n
                )
                if definition["profile"] == "aerograph_sampled/v1":
                    # A sampled predicate is supplied by the existing Q6 partition,
                    # never recalculated in this reactive executor.
                    adapter = definition.get("adapter")
                    if not isinstance(adapter, dict) or set(adapter) != {
                        "event",
                        "context",
                    }:
                        raise ValueError(
                            f"{ir.source}: $.predicates: sampled predicate requires adapter {{event, context}} and a pinned Q6 engine/bindings.samples"
                        )
                    subscribes.add(PREFIX + "sampled")
            for chain in p["chains"].values():
                triggers = [chain["trigger"], *[t["on"] for t in chain["transitions"]]]
                subscribes.update(t["event"] for t in triggers if "event" in t)
                self.authored_events.update(
                    t["event"] for t in triggers if "event" in t
                )
                for transition in chain["transitions"]:
                    for a in transition["actions"]:
                        if a["kind"] == "emit":
                            emits.add(a["schema"])
                            targets.add(a["topic"])
                        elif a["kind"] == "command":
                            command = self._capability(a)
                            emits.add(command["schema"])
                            targets.add(command["target"])
                        elif a["kind"] == "assert_relation":
                            relation_produces.add(a["relation"])
            for point in p.get("injection_points", []):
                commands.add(point["command"])
                emits.add(point["emits"])
                targets.add(point.get("topic", point["emits"]))
            for rule in p.get("conflicts", []):
                for selector in rule.get("match", {}).values():
                    if "field" in selector:
                        consumes.add(selector["field"])
                    if "relation" in selector:
                        relations.add(selector["relation"])
                emits.add(rule["emits"])
                targets.add(rule.get("topic", rule["emits"]))
            bootstrap = p.get("bootstrap_relations", {})
            self.boot_edges.extend(bootstrap.get("assertions", []))
            relation_produces.update(
                e["relation"] for e in bootstrap.get("assertions", [])
            )
        # Lifecycle subscriptions must include concrete descendants: kernel routes
        # typed lifecycle notifications by exact published type, not directory.
        lifecycle_types = tuple(t.id for t in build.registry.types if t.id != TYPE)
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=tuple(sorted(produces)),
                consumes=tuple(Dependency(f) for f in sorted(consumes)),
                emits=tuple(sorted(emits)),
                commands=tuple(sorted(commands)),
                subscribes=tuple(sorted(subscribes)),
                message_targets=tuple(sorted(targets)),
                lifecycle=True,
                lifecycle_reads=lifecycle_types,
                features=("relations",) if relations or relation_produces else (),
                relation_consumes=tuple(
                    RelationDependency(r) for r in sorted(relations | relation_produces)
                ),
                relation_produces=tuple(sorted(relation_produces)),
                message_lag_ns=cfg.get("message_lag_ns", 0),
            ),
            policies=policies(produces),
        )

    def _capability(self, action: dict[str, Any]) -> dict[str, Any]:
        if "capability" in action:
            if action["capability"] not in self.capabilities:
                raise ValueError(
                    f"command.capability: configure schema/target for {action['capability']}"
                )
            return cast(dict[str, Any], self.capabilities[action["capability"]])
        return action

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        ctx.ops.append(Activate(self.partition.id))

    def _expr(
        self,
        ctx: EngineContext,
        value: Any,
        instance: Instance,
        trigger: dict[str, Any],
    ) -> Any:
        if isinstance(value, list):
            return [self._expr(ctx, v, instance, trigger) for v in value]
        if not isinstance(value, dict):
            return value
        if "$role" in value:
            return {"$ref": instance.roles[value["$role"]].to_data()}
        if "$variable" in value:
            return instance.variables[value["$variable"]]
        if "$trigger" in value:
            selected: Any = trigger
            for part in value["$trigger"].split("."):
                selected = selected[part]
            return selected
        if "field" in value and "role" in value:
            result: Any = ctx.get(instance.roles[value["role"]], value["field"])
            if isinstance(result, Absent):
                raise ValueError(
                    f"binding.variables: missing actual {value['role']}.{value['field']}"
                )
            result = thaw(result)
            for part in value.get("path", []):
                result = result[part]
            return result
        return {k: self._expr(ctx, v, instance, trigger) for k, v in value.items()}

    def _ref(
        self,
        ctx: EngineContext,
        value: Any,
        instance: Instance,
        trigger: dict[str, Any],
    ) -> EntityRef:
        resolved = self._expr(ctx, value, instance, trigger)
        if isinstance(resolved, dict) and "$ref" in resolved:
            return EntityRef.from_data(resolved["$ref"])
        refs = [
            r
            for r, life in ctx.view._store.lives.items()
            if r.id == resolved
            and life.created.index <= ctx.view.cut.index
            and ctx.view._store.alive(r, ctx.view.cut, ctx.now)
        ]
        if len(refs) != 1:
            raise ValueError(
                f"behaviour.entity: expected unique live generation for {resolved}"
            )
        return refs[0]

    def _record(
        self,
        ctx: EngineContext,
        m: Instance,
        lifecycle: str,
        transition: str | None = None,
    ) -> None:
        ctx.get(m.ref, PREFIX + "revision")
        if m.prior_record is not None:
            ctx.emit(
                PREFIX + m.prior_record["lifecycle"],
                {
                    **m.prior_record,
                    "op": "close",
                    "validTo": instant(ctx.now.ns, ctx.now.microstep),
                },
                topic=PREFIX + "records",
            )
        children = {
            name: {"commandId": c.command, "status": c.status, "receipt": c.receipt}
            for name, c in m.children.items()
        }
        record = {
            "instanceId": m.ref.id,
            "templateId": m.template,
            "bindingId": m.binding["id"],
            "packageDigest": m.digest,
            "roles": {r: {"$ref": ref.to_data()} for r, ref in m.roles.items()},
            "lifecycle": lifecycle,
            "state": m.state,
            "revision": m.revision,
            "status": m.status,
            "requiredInputs": m.required_inputs,
            "variables": m.variables,
            "children": children,
            "transitionId": transition,
            "trigger": m.trigger,
            "validFrom": instant(ctx.now.ns, ctx.now.microstep),
            "validTo": None,
            "op": "assert",
        }
        cause = ctx.emit(PREFIX + lifecycle, record, topic=PREFIX + "records")
        ctx.inputs.append(cause)
        m.prior_record = record
        fields = {
            "template_id": m.template,
            "binding_id": m.binding["id"],
            "roles": record["roles"],
            "state": m.state,
            "revision": m.revision,
            "status": m.status,
            "children": children,
        }
        self.publish[m.ref] = fields

    def _truth(
        self, ctx: EngineContext, m: Instance, predicate: str, fresh: set[str]
    ) -> Truth:
        definition = m.package["predicates"][predicate]
        aliases = m.chain.get("predicate_roles", {}).get(predicate, {})
        roles = {name: m.roles[aliases.get(name, name)] for name in definition["roles"]}
        context = m.predicate_contexts.get(predicate)
        if context is None:
            context = stable_id(
                [
                    m.digest,
                    predicate,
                    {name: ref.to_data() for name, ref in sorted(roles.items())},
                ],
                "predicate",
            )
            m.predicate_contexts[predicate] = context
        return self._evaluate_truth(
            ctx,
            context,
            predicate,
            definition,
            roles,
            m.package.get("evaluator", {}).get("dialect", "original"),
            fresh,
        )

    def _evaluate_truth(
        self,
        ctx: EngineContext,
        context: str,
        predicate: str,
        definition: dict[str, Any],
        roles: dict[str, EntityRef],
        dialect: str,
        fresh: set[str],
    ) -> Truth:
        old = self.evaluator.truths.get(context)
        if definition["profile"] == "aerograph_sampled/v1":
            sampled = self.sampled.get(definition["adapter"]["context"])
            if sampled is None or any(
                sampled["roles"].get(name) != {"$ref": ref.to_data()}
                for name, ref in roles.items()
            ):
                signature = ("missing_sample", tuple(sorted(roles.items())))
                if old is not None and old.signature == signature:
                    return old
                at = instant(ctx.now.ns, ctx.now.microstep)
                if old is not None:
                    ctx.emit(
                        PREFIX + "predicate_evaluated",
                        {**old.record, "op": "close", "validTo": at},
                        topic=PREFIX + "records",
                    )
                record = {
                    "contextId": context,
                    "predicateId": predicate,
                    "profile": definition["profile"],
                    "roles": {
                        name: {"$ref": ref.to_data()} for name, ref in roles.items()
                    },
                    "status": "required_input",
                    "value": None,
                    "diagnostics": [
                        {
                            "reason": "no recorded sampled frame for this bound tuple",
                            "requiredContext": definition["adapter"]["context"],
                        }
                    ],
                    "evaluatedAt": at,
                    "readCut": {
                        "index": ctx.view.cut.index,
                        "at": instant(
                            ctx.view.cut.instant.ns, ctx.view.cut.instant.microstep
                        ),
                    },
                    "acquired": {},
                    "validFrom": at,
                    "validTo": None,
                    "op": "assert",
                }
                cause = ctx.emit(
                    PREFIX + "predicate_evaluated", record, topic=PREFIX + "records"
                )
                truth = Truth(None, "required_input", signature, record, cause)
                self.evaluator.truths[context] = truth
                fresh.add(context)
                return truth
            signature = sampled["sampleVersion"]
            if old is not None and old.signature == signature:
                return old
            if (
                old is not None
                and isinstance(old.signature, tuple)
                and old.signature[0] == "missing_sample"
            ):
                ctx.emit(
                    PREFIX + "predicate_evaluated",
                    {
                        **old.record,
                        "op": "close",
                        "validTo": instant(ctx.now.ns, ctx.now.microstep),
                    },
                    topic=PREFIX + "records",
                )
            record = {**sampled, "contextId": context, "predicateId": predicate}
            truth = Truth(
                record["value"],
                record["status"],
                signature,
                record,
                self.sampled_causes[definition["adapter"]["context"]],
                previous=None
                if old is None
                or old.status != "known"
                or {
                    key: (stamp["clockId"], stamp["mappingId"])
                    for key, stamp in old.record["acquired"].items()
                }
                != {
                    key: (stamp["clockId"], stamp["mappingId"])
                    for key, stamp in record["acquired"].items()
                }
                else old.value,
            )
            self.evaluator.truths[context] = truth
            fresh.add(context)
            return truth
        relevant = old is None or context in self.affected_contexts
        if relevant:
            truth = self.evaluator.run(
                ctx,
                context,
                predicate,
                definition,
                roles,
                dialect=dialect,
            )
            if truth is not old:
                fresh.add(context)
            return truth
        assert old is not None
        return old

    def _guard(
        self, ctx: EngineContext, m: Instance, predicates: list[str], fresh: set[str]
    ) -> bool:
        for predicate in predicates:
            truth = self._truth(ctx, m, predicate, fresh)
            if truth.cause is not None and truth.cause not in ctx.inputs:
                ctx.inputs.append(truth.cause)
            if truth.status != "known" or truth.value is not True:
                return False
        return True

    def _bind(self, ctx: EngineContext) -> None:
        affected = not self.bound_once or any(
            d.kind == "relation"
            or d.key is not None
            and d.key[1] in self.selector_fields
            or d.kind == "lifecycle"
            and EntityRef.from_data(
                cast(dict[str, Any], thaw(d.payload))["$ref"]
            ).type_id
            != TYPE
            for d in ctx.dirty
        )
        if not affected:
            return
        self.bound_once = True
        current: set[str] = set()
        for ir in self.packages:
            p = ir.document
            for binding in sorted(p["bindings"], key=lambda b: b["id"]):
                chain = p["chains"][binding["chain"]]
                for roles, edges in tuples(
                    ctx, self.build.registry, chain["roles"], binding["match"]
                ):
                    episode: Any = None
                    if binding["multiplicity"] == "once_per_relation":
                        episode = [list(cast(tuple[Any, ...], edge)) for edge in edges]
                    elif binding["multiplicity"] == "once_per_task_episode":
                        role = binding.get("episode_role", "task")
                        selected = ctx.get(roles[role], binding["episode_field"])
                        if isinstance(selected, Absent):
                            continue
                        episode = thaw(selected)
                    identity = stable_id(
                        [
                            self.build.manifest.run_id,
                            self.build.manifest.epoch,
                            ir.digest,
                            binding["id"],
                            binding["chain"],
                            {name: r.to_data() for name, r in sorted(roles.items())},
                            episode,
                        ]
                    )
                    current.add(identity)
                    if identity in self.bind_keys:
                        existing = self.instances[identity]
                        if existing.status == "waiting_inputs":
                            self._binding_values(ctx, existing)
                        continue
                    if (
                        len(
                            [
                                m
                                for m in self.instances.values()
                                if m.digest == ir.digest
                            ]
                        )
                        >= p["budgets"]["max_instances"]
                    ):
                        raise RuntimeError(
                            f"behaviour budget max_instances: package={ir.digest} binding={binding['id']} cut={ctx.view.cut.index}"
                        )
                    ref = EntityRef(
                        self.build.manifest.run_id,
                        self.build.manifest.epoch,
                        identity,
                        0,
                        TYPE,
                    )
                    m = Instance(
                        ref,
                        p,
                        ir.digest,
                        binding,
                        binding["chain"],
                        roles,
                        {},
                        chain["initial"],
                    )
                    self._binding_values(ctx, m)
                    m.continued = "activated"
                    ctx.create(ref)
                    self.instances[identity] = m
                    self.bind_keys.add(identity)
                    ctx.ops.append(Activate(self.partition.id))
        for identity, m in self.instances.items():
            if (
                identity not in current
                and m.binding["on_unbind"] == "close_after_cleanup"
                and m.status not in {"completed", "failed", "canceled"}
            ):
                if any(c.status not in TERMINAL for c in m.children.values()):
                    m.status = "cleanup"
                elif m.published:
                    m.status = "canceled"
                    m.revision += 1
                    for timer, _ in m.timers.values():
                        ctx.ops.append(CancelTimer(timer))
                    m.timers.clear()
                    if m.deadline is not None:
                        ctx.ops.append(CancelTimer(m.deadline))
                        m.deadline = None
                    self._record(ctx, m, "canceled")

    def _binding_values(self, ctx: EngineContext, m: Instance) -> None:
        """Await actual producer facts across creation waves, with visible diagnostics."""
        missing: list[dict[str, Any]] = []
        for name, expression in m.binding.get("variables", {}).items():
            for role, field_id in expression_reads(expression):
                ref = m.roles[role]
                if isinstance(ctx.get(ref, field_id), Absent):
                    missing.append(
                        {
                            "variable": name,
                            "entity": {"$ref": ref.to_data()},
                            "field": field_id,
                            "producer": ctx.view._store.writers.get((ref, field_id)),
                            "reason": "awaiting actual committed producer fact",
                        }
                    )
        old_status, old_missing = m.status, m.required_inputs
        m.required_inputs = missing
        if missing:
            m.status = "waiting_inputs"
        else:
            m.variables = {
                name: self._expr(ctx, expression, m, {})
                for name, expression in m.binding.get("variables", {}).items()
            }
            m.status = "dormant"
        if m.published and (old_status != m.status or old_missing != missing):
            m.revision += 1
            self._record(ctx, m, "transitioned")

    def _trigger(
        self,
        ctx: EngineContext,
        m: Instance,
        spec: dict[str, Any],
        fresh: set[str],
        events: list[tuple[str, dict[str, Any]]],
        timers: list[dict[str, Any]],
        receipts: list[tuple[str, str, str, Any]],
    ) -> dict[str, Any] | None:
        if "instance" in spec and m.continued == spec["instance"]:
            return {"instance": spec["instance"]}
        if "lifecycle" in spec:
            for dirty in ctx.dirty:
                if dirty.kind != "lifecycle":
                    continue
                ref = EntityRef.from_data(
                    cast(dict[str, Any], thaw(dirty.payload))["$ref"]
                )
                if ref in m.roles.values():
                    alive = ctx.view._store.alive(ref, ctx.view.cut, ctx.now)
                    if spec["lifecycle"] == ("created" if alive else "removed"):
                        return {
                            "lifecycle": spec["lifecycle"],
                            "entity": {"$ref": ref.to_data()},
                        }
            if spec["lifecycle"] == "created" and m.status == "dormant":
                return {"lifecycle": "created"}
        if "event" in spec:
            for schema, payload in events:
                if schema != spec["event"]:
                    continue
                correlation = spec.get("correlation", {})
                if all(
                    key in payload
                    and typed_equal(
                        payload[key], self._expr(ctx, value, m, {"payload": payload})
                    )
                    for key, value in correlation.items()
                ):
                    return {"event": schema, "payload": payload}
        if "predicate" in spec:
            truth = self._truth(ctx, m, spec["predicate"], fresh)
            context = truth.record.get("contextId")
            definition = m.package["predicates"][spec["predicate"]]
            native_edge = definition[
                "profile"
            ] == "aerograph_sampled/v1" and definition["expression"].get("op") in {
                "entered",
                "exited",
                "transition_event",
            }
            matched = (
                truth.value is True if native_edge else truth.matches(spec["edge"])
            )
            if context in fresh and matched:
                if truth.cause is not None:
                    ctx.inputs.append(truth.cause)
                return {
                    "predicate": spec["predicate"],
                    "evaluation": truth.record,
                    "edge": spec["edge"],
                }
        for category in ("timer", "deadline"):
            if category in spec:
                for payload in timers:
                    if (
                        payload["instance"] == m.ref.id
                        and payload.get(category) == spec[category]
                        and (
                            category == "deadline"
                            or payload["revision"] == m.entry_revision
                        )
                    ):
                        return payload
        if "receipt" in spec:
            actions = (
                spec["receipt"]
                if isinstance(spec["receipt"], list)
                else [spec["receipt"]]
            )
            statuses = (
                spec["status"] if isinstance(spec["status"], list) else [spec["status"]]
            )
            receipt_matches = [
                name in m.children
                and m.children[name].status in statuses
                and all(
                    isinstance(m.children[name].result, dict)
                    and key in m.children[name].result
                    and typed_equal(
                        m.children[name].result[key],
                        self._expr(
                            ctx, expected, m, {"result": m.children[name].result}
                        ),
                    )
                    for key, expected in spec.get("result_matches", {}).items()
                )
                for name in actions
            ]
            if (
                all(receipt_matches)
                if spec.get("policy", "all") == "all"
                else any(receipt_matches)
            ) and any(
                owner == m.ref.id
                and name in actions
                and status in statuses
                and receipt_matches[actions.index(name)]
                for owner, name, status, _ in receipts
            ):
                fresh_children = {
                    name
                    for owner, name, status, _ in receipts
                    if owner == m.ref.id and status in statuses
                }
                selected = next(
                    name
                    for name, matched in zip(actions, receipt_matches, strict=True)
                    if matched and name in fresh_children
                )
                child = m.children[selected]
                return {
                    "receipt": selected,
                    "status": child.status,
                    "result": child.result,
                    "command_id": child.command,
                }
        return None

    def _actions(
        self,
        ctx: EngineContext,
        m: Instance,
        actions: list[dict[str, Any]],
        trigger: dict[str, Any],
    ) -> None:
        for a in actions:
            kind = a["kind"]
            action_cause = ctx.emit(
                PREFIX + "action_started",
                {
                    "instanceId": m.ref.id,
                    "packageDigest": m.digest,
                    "revision": m.revision,
                    "transitionId": m.prior_record["transitionId"]
                    if m.prior_record
                    else None,
                    "actionId": a["id"],
                    "kind": kind,
                },
                topic=PREFIX + "records",
            )
            ctx.inputs.append(action_cause)
            if kind == "set":
                ref = self._ref(ctx, a["entity"], m, trigger)
                ctx.set(ref, a["field"], self._expr(ctx, a["value"], m, trigger))
            elif kind == "emit":
                ctx.emit(
                    a["schema"],
                    self._expr(ctx, a["payload"], m, trigger),
                    topic=a["topic"],
                )
            elif kind == "command":
                old = m.children.get(a["id"])
                if old is not None and old.status not in TERMINAL:
                    raise RuntimeError(
                        f"behaviour.command: child {a['id']} still active in {m.ref.id}"
                    )
                if any(
                    not ctx.view._store.alive(ref, ctx.view.cut, ctx.now)
                    for ref in m.roles.values()
                ):
                    raise ValueError(
                        f"behaviour.command: role generation removed for {m.ref.id}/{a['id']}"
                    )
                resolved = self._capability(a)
                child = Child(a["id"], resolved["schema"])
                m.children[a["id"]] = child
                ctx.submit(
                    CommandRequest(
                        resolved["schema"],
                        resolved["target"],
                        ctx.now,
                        self._expr(ctx, a["payload"], m, trigger),
                    )
                )
                self.pending.append((m, child))
                if "deadline_ns" in a:
                    limit = m.package["budgets"].get("max_timers_per_instance", 64)
                    if (
                        len(m.timers)
                        + sum(c.deadline is not None for c in m.children.values())
                        + (m.deadline is not None)
                        >= limit
                    ):
                        raise RuntimeError(
                            f"behaviour timer budget: {m.ref.id}/{a['id']}"
                        )
                    child.deadline = ctx.wake_at(
                        ctx.now.ns + a["deadline_ns"],
                        {
                            "instance": m.ref.id,
                            "revision": m.revision,
                            "deadline": a["id"],
                        },
                    )
            elif kind == "cancel_command":
                child = m.children[cast(str, a.get("command", a.get("child")))]
                if child.command is None:
                    raise ValueError(
                        "cancel_command: child command has no submitted receipt"
                    )
                descriptor = (
                    self.build.registry.message(child.schema)
                    if child.schema is not None
                    else None
                )
                if descriptor is not None and not descriptor.cancel_support:
                    raise ValueError(
                        "cancel_command: physical owner does not advertise cancellation"
                    )
                ctx.ops.append(RequestCancel(child.command, tuple(ctx.inputs)))
                if "timeout_ns" in a:
                    if child.deadline is not None:
                        ctx.ops.append(CancelTimer(child.deadline))
                    child.deadline = ctx.wake_at(
                        ctx.now.ns + a["timeout_ns"],
                        {
                            "instance": m.ref.id,
                            "revision": m.entry_revision,
                            "deadline": child.action,
                        },
                    )
            elif kind == "delay":
                limit = m.package["budgets"].get("max_timers_per_instance", 64)
                if (
                    len(m.timers)
                    + sum(c.deadline is not None for c in m.children.values())
                    + (m.deadline is not None)
                    >= limit
                ):
                    raise RuntimeError(f"behaviour timer budget: {m.ref.id}/{a['id']}")
                if a["duration_ns"] == 0:
                    timer = f"behaviour:{m.ref.id}:{m.entry_revision}:{a['id']}:{ctx.now.microstep}"
                    ctx.ops.append(
                        ScheduleTimer(
                            timer,
                            Instant(ctx.now.ns, ctx.now.microstep + 1),
                            {
                                "instance": m.ref.id,
                                "revision": m.entry_revision,
                                "timer": a["id"],
                            },
                            tuple(ctx.inputs),
                        )
                    )
                    m.timers[a["id"]] = (timer, m.entry_revision)
                    continue
                timer = ctx.wake_at(
                    ctx.now.ns + a["duration_ns"],
                    {
                        "instance": m.ref.id,
                        "revision": m.entry_revision,
                        "timer": a["id"],
                    },
                )
                m.timers[a["id"]] = (timer, m.entry_revision)
            elif kind == "cancel_timer":
                identity = a.get("timer", a.get("action"))
                if identity not in m.timers:
                    raise ValueError(f"cancel_timer: no active timer {identity}")
                ctx.ops.append(CancelTimer(m.timers.pop(identity)[0]))
            elif kind == "assert_relation":
                ctx.relate(
                    self._expr(ctx, a["edge_id"], m, trigger),
                    a["relation"],
                    self._ref(ctx, a["source"], m, trigger),
                    self._ref(ctx, a["target"], m, trigger),
                    valid=Interval(ctx.now, None),
                    acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
                )
            elif kind == "close_relation":
                ctx.unrelate(self._expr(ctx, a["edge_id"], m, trigger))
            elif kind == "create_entity":
                m.creations += 1
                if m.creations > m.package["budgets"].get(
                    "max_creations_per_instance", 64
                ):
                    raise RuntimeError(
                        f"behaviour creation budget: {m.ref.id}/{a['id']}"
                    )
                identity = self._expr(ctx, a["entity"], m, trigger)
                lives = [r for r in ctx.view._store.lives if r.id == identity]
                ref = EntityRef(
                    self.build.manifest.run_id,
                    self.build.manifest.epoch,
                    identity,
                    max((r.generation for r in lives), default=-1) + 1,
                    a["type"],
                )
                ctx.create(ref)
                self.deferred.append(
                    (ref, self._expr(ctx, a.get("facts", {}), m, trigger))
                )
                ctx.ops.append(Activate(self.partition.id))
            elif kind == "remove_entity":
                if any(c.status not in TERMINAL for c in m.children.values()):
                    raise ValueError(
                        "remove_entity: wait for real child cleanup receipts"
                    )
                cleanup = tuple(
                    ItemRef(
                        cast(dict[str, int], c.receipt)["record_index"],
                        cast(dict[str, int], c.receipt)["item_index"],
                    )
                    for c in m.children.values()
                    if c.receipt is not None
                )
                ctx.remove(self._ref(ctx, a["entity"], m, trigger), cleanup)
            elif kind == "complete":
                m.status = a.get("status", "completed")
            else:
                raise ValueError(f"behaviour.actions: unsupported {kind}")

    def _inject(self, ctx: EngineContext, delivery: Any) -> None:
        cmd = ctx.remember(delivery, lambda value: value)
        payload = cast(dict[str, Any], cmd.payload)
        matches = [
            point
            for ir in self.packages
            for point in ir.document.get("injection_points", [])
            if point["id"] == payload.get("injection_point")
            and point["command"] in {INJECT, delivery.message.schema_id}
            and point["target"] == self.build.id
        ]
        if len(matches) != 1:
            ctx.reject(cmd, {"reason": "undeclared or ambiguous injection point"})
            return
        point = matches[0]
        self.build.registry.validate(
            self.build.registry.message(point["emits"]).schema, payload["payload"]
        )
        ctx.accept(cmd)
        ctx.execute(cmd)
        ctx.emit(
            point["emits"],
            payload["payload"],
            topic=point.get("topic", point["emits"]),
            at=delivery.message.at,
            stamp=delivery.message.source_stamp,
        )
        ctx.succeed(cmd, {"injection_point": point["id"]})

    def on_inputs(self, ctx: EngineContext) -> None:
        self.publish: dict[EntityRef, dict[str, Any]] = {}
        for truth in self.evaluator.truths.values():
            if isinstance(truth.cause, LocalCause):
                truth.cause = None
        if self.boot_edges:
            for edge in self.boot_edges:
                ctx.relate(
                    edge["id"],
                    edge["relation"],
                    EntityRef.from_data(edge["source"]["$ref"]),
                    EntityRef.from_data(edge["target"]["$ref"]),
                    valid=Interval(ctx.now, None),
                    acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
                )
            self.boot_edges.clear()
            ctx.ops.append(Activate(self.partition.id))
        deferred, self.deferred = self.deferred, []
        for ref, facts in deferred:
            for slot, value in facts.items():
                ctx.set(ref, slot, value)
        events: list[tuple[str, dict[str, Any]]] = []
        for delivery in ctx.inbox:
            if delivery.message.kind == "command":
                self._inject(ctx, delivery)
            elif delivery.message.kind == "event":
                payload = cast(dict[str, Any], thaw(delivery.message.payload))
                if (
                    delivery.message.schema_id == PREFIX + "predicate_evaluated"
                    and payload.get("profile") == "aerograph_sampled/v1"
                ):
                    if payload.get("op") != "close":
                        self.sampled[payload["contextId"]] = {
                            **payload,
                            "sampleVersion": delivery.message.id,
                        }
                        self.sampled_causes[payload["contextId"]] = (
                            delivery.dispatch_ref
                        )
                elif delivery.message.schema_id == PREFIX + "predicate_evaluated":
                    recorded_truth = self.evaluator.truths.get(payload["contextId"])
                    if (
                        recorded_truth is not None
                        and payload.get("op") == "assert"
                        and typed_equal(recorded_truth.record, payload)
                    ):
                        recorded_truth.cause = delivery.message.origin
                elif (
                    delivery.message.schema_id in EVENTS
                    and delivery.message.schema_id not in self.authored_events
                ):
                    pass  # journal notifications are evidence, not authored triggers
                else:
                    events.append((delivery.message.schema_id, payload))
        timers: list[dict[str, Any]] = []
        receipts: list[tuple[str, str, str, Any]] = []
        for dirty in ctx.dirty:
            payload = cast(dict[str, Any], thaw(dirty.payload))
            if dirty.kind == "receipt":
                command, status = payload["command_id"], payload["status"]
                if status == "submitted":
                    m, child = self.pending.popleft()
                    child.command = command
                    self.commands_to_children[command] = (m, child)
                elif command not in self.commands_to_children:
                    continue  # own injection command receipts are not children
                m, child = self.commands_to_children[command]
                child.status, child.result = status, payload.get("result")
                child.receipt = {
                    "record_index": dirty.cause.record_index,
                    "item_index": dirty.cause.item_index,
                }
                receipts.append((m.ref.id, child.action, status, child.result))
                if m.published:
                    m.revision += 1
                    self._record(ctx, m, "transitioned")
                if status in TERMINAL and child.deadline is not None:
                    ctx.ops.append(CancelTimer(child.deadline))
                    child.deadline = None
            elif dirty.kind == "timer":
                timers.append(payload)
                if "deadline" in payload and payload["instance"] in self.instances:
                    instance = self.instances[payload["instance"]]
                    if payload["deadline"] == "instance":
                        instance.deadline = None
                    elif payload["deadline"] in instance.children:
                        instance.children[payload["deadline"]].deadline = None
                if "timer" in payload and payload["instance"] in self.instances:
                    self.instances[payload["instance"]].timers.pop(
                        payload["timer"], None
                    )
        self._bind(ctx)
        self.affected_contexts = self.evaluator.affected(ctx)
        fresh: set[str] = set()
        for context in sorted(self.affected_contexts):
            predicate_id, definition, roles, dialect = self.evaluator.contexts[context]
            indexed_previous = self.evaluator.truths[context]
            truth = self.evaluator.run(
                ctx, context, predicate_id, definition, roles, dialect=dialect
            )
            if truth is not indexed_previous:
                fresh.add(context)
        fired_conflicts: set[tuple[str, str, str, str]] = set()
        # Conflict rules use the identical Q6 evidence/edge contract.
        for ir in self.packages:
            for rule in ir.document.get("conflicts", []):
                for roles, _ in tuples(
                    ctx, self.build.registry, rule["roles"], rule.get("match", {})
                ):
                    predicate = rule["predicate"]
                    context = stable_id(
                        [
                            ir.digest,
                            rule["id"],
                            {name: r.to_data() for name, r in sorted(roles.items())},
                        ],
                        "conflict",
                    )
                    truth = self._evaluate_truth(
                        ctx,
                        context,
                        predicate,
                        ir.document["predicates"][predicate],
                        roles,
                        ir.document.get("evaluator", {}).get("dialect", "original"),
                        fresh,
                    )
                    emission_key = (
                        ir.digest,
                        rule["id"],
                        context,
                        repr(truth.signature),
                    )
                    native_edge = ir.document["predicates"][predicate][
                        "profile"
                    ] == "aerograph_sampled/v1" and ir.document["predicates"][
                        predicate
                    ]["expression"].get("op") in {
                        "entered",
                        "exited",
                        "transition_event",
                    }
                    matched = (
                        truth.status == "known" and truth.value is True
                        if native_edge
                        else truth.matches(rule["edge"])
                    )
                    if (
                        context in fresh
                        and matched
                        and emission_key not in fired_conflicts
                    ):
                        fired_conflicts.add(emission_key)
                        ctx.inputs.append(
                            truth.cause
                        ) if truth.cause is not None else None
                        payload = rule.get("payload")
                        if payload is None:
                            members = self.build.registry.message(rule["emits"]).schema[
                                "members"
                            ]
                            payload = {
                                name: {"$ref": roles[name].to_data()}
                                for name in members
                                if name in roles
                            }
                            if "actor" in members and len(roles) == 1:
                                payload["actor"] = {
                                    "$ref": next(iter(roles.values())).to_data()
                                }
                        conflict_cause = ctx.emit(
                            PREFIX + "conflict",
                            {
                                "ruleId": rule["id"],
                                "packageDigest": ir.digest,
                                "predicateId": predicate,
                                "evaluation": truth.record,
                                "roles": {
                                    r: {"$ref": ref.to_data()}
                                    for r, ref in roles.items()
                                },
                                "eventSchema": rule["emits"],
                            },
                            topic=PREFIX + "records",
                        )
                        ctx.inputs.append(conflict_cause)
                        ctx.emit(
                            rule["emits"],
                            payload,
                            topic=rule.get("topic", rule["emits"]),
                        )
        candidates: list[
            tuple[int, str, str, int, Instance, dict[str, Any], dict[str, Any]]
        ] = []
        for m in sorted(
            self.instances.values(), key=lambda m: (m.binding["id"], m.ref.id)
        ):
            if not m.published:
                life = ctx.view._store.lives.get(m.ref)
                if life is None or life.created.index > ctx.view.cut.index:
                    continue
                m.published = True
                self._record(ctx, m, "created")
                initial_trigger = m.chain["trigger"]
                if "timer" in initial_trigger:
                    after = initial_trigger["after_ns"]
                    payload = {
                        "instance": m.ref.id,
                        "revision": m.entry_revision,
                        "timer": initial_trigger["timer"],
                    }
                    if after == 0:
                        timer_id = f"behaviour:{m.ref.id}:initial:{ctx.now.microstep}"
                        ctx.ops.append(
                            ScheduleTimer(
                                timer_id,
                                Instant(ctx.now.ns, ctx.now.microstep + 1),
                                payload,
                                tuple(ctx.inputs),
                            )
                        )
                    else:
                        timer_id = ctx.wake_at(ctx.now.ns + after, payload)
                    m.timers[initial_trigger["timer"]] = (timer_id, m.entry_revision)
            if m.status == "cleanup" and all(
                c.status in TERMINAL for c in m.children.values()
            ):
                m.status = "canceled"
                m.revision += 1
                for timer, _ in m.timers.values():
                    ctx.ops.append(CancelTimer(timer))
                m.timers.clear()
                if m.deadline is not None:
                    ctx.ops.append(CancelTimer(m.deadline))
                    m.deadline = None
                self._record(ctx, m, "canceled")
            elif m.status == "cleanup":
                for child in m.children.values():
                    if (
                        child.command is not None
                        and child.schema is not None
                        and child.status not in TERMINAL
                        and not child.cancel_requested
                        and self.build.registry.message(child.schema).cancel_support
                    ):
                        ctx.ops.append(RequestCancel(child.command, tuple(ctx.inputs)))
                        child.cancel_requested = True
            if m.status in {
                "completed",
                "failed",
                "canceled",
                "cleanup",
                "waiting_inputs",
            }:
                continue
            if m.status == "dormant":
                trigger = self._trigger(
                    ctx, m, m.chain["trigger"], fresh, events, timers, receipts
                )
                if trigger is None or not self._guard(
                    ctx, m, m.chain.get("preconditions", []), fresh
                ):
                    continue
                m.status, m.continued, m.trigger = "active", "activated", trigger
                if "deadline_ns" in m.chain:
                    m.deadline = ctx.wake_at(
                        ctx.now.ns + m.chain["deadline_ns"],
                        {
                            "instance": m.ref.id,
                            "revision": m.entry_revision,
                            "deadline": "instance",
                        },
                    )
            for index, transition in enumerate(m.chain["transitions"]):
                if transition["from"] != m.state:
                    continue
                trigger = self._trigger(
                    ctx, m, transition["on"], fresh, events, timers, receipts
                )
                if trigger is not None and self._guard(
                    ctx,
                    m,
                    [transition["guard"]] if "guard" in transition else [],
                    fresh,
                ):
                    candidates.append(
                        (
                            -transition.get("priority", 0),
                            m.binding["id"],
                            m.ref.id,
                            index,
                            m,
                            transition,
                            trigger,
                        )
                    )
            m.continued = None
        processed: set[str] = set()
        writes: set[tuple[EntityRef, str]] = set()
        relation_edges = {
            edge.edge_id: (relation, edge.source, edge.target)
            for relation in self.partition.relation_produces
            for edge in ctx.relations(relation)
        }
        edge_slots: set[str] = set()
        for _, _, identity, _, m, transition, trigger in sorted(
            candidates, key=lambda c: c[:4]
        ):
            if identity in processed:
                continue
            processed.add(identity)
            proposed = {
                (self._ref(ctx, a["entity"], m, trigger), a["field"])
                for a in transition["actions"]
                if a["kind"] == "set"
            }
            staged_edges = dict(relation_edges)
            proposed_edges: set[str] = set()
            relation_conflict = False
            for action in transition["actions"]:
                if action["kind"] not in {"assert_relation", "close_relation"}:
                    continue
                edge_id = self._expr(ctx, action["edge_id"], m, trigger)
                if edge_id in proposed_edges or edge_id in edge_slots:
                    relation_conflict = True
                    break
                proposed_edges.add(edge_id)
                if action["kind"] == "close_relation":
                    if edge_id not in staged_edges:
                        raise ValueError(
                            f"close_relation: no committed active edge {edge_id}"
                        )
                    del staged_edges[edge_id]
                    continue
                if edge_id in staged_edges:
                    relation_conflict = True
                    break
                relation = action["relation"]
                source = self._ref(ctx, action["source"], m, trigger)
                target = self._ref(ctx, action["target"], m, trigger)
                staged_edges[edge_id] = (relation, source, target)
                descriptor = self.build.registry.relation(relation)
                same_relation = [
                    edge for edge in staged_edges.values() if edge[0] == relation
                ]
                for offset, endpoint, bounds in (
                    (1, source, descriptor.targets_per_source),
                    (2, target, descriptor.sources_per_target),
                ):
                    if (
                        bounds.maximum is not None
                        and sum(edge[offset] == endpoint for edge in same_relation)
                        > bounds.maximum
                    ):
                        relation_conflict = True
                if (
                    descriptor.identity_policy == "endpoint_pair"
                    and sum(edge[1:] == (source, target) for edge in same_relation) > 1
                ):
                    relation_conflict = True
            if proposed & writes or relation_conflict:
                ctx.emit(
                    PREFIX + "action_conflict",
                    {
                        "instanceId": identity,
                        "transitionId": transition["id"],
                        "reason": "slot reserved by an earlier candidate",
                        "packageDigest": m.digest,
                    },
                    topic=PREFIX + "records",
                )
                continue
            writes.update(proposed)
            edge_slots.update(proposed_edges)
            relation_edges = staged_edges
            if m.last_ns != ctx.now.ns:
                m.last_ns, m.transitions_at_ns = ctx.now.ns, 0
            m.transitions_at_ns += 1
            if (
                m.transitions_at_ns
                > m.package["budgets"]["max_transitions_per_instance_per_ns"]
            ):
                raise RuntimeError(
                    f"behaviour transition budget: instance={identity} transition={transition['id']} revision={m.revision} cause={trigger}"
                )
            for timer, _ in m.timers.values():
                ctx.ops.append(CancelTimer(timer))
            m.timers.clear()
            m.state, m.revision, m.trigger = transition["to"], m.revision + 1, trigger
            m.entry_revision += 1
            # Authored identity enters the causal graph before action proposals.
            self._record(ctx, m, "transitioned", transition["id"])
            self._actions(ctx, m, transition["actions"], trigger)
            if m.state in m.chain["terminal"] or m.status in {
                "completed",
                "failed",
                "canceled",
            }:
                policy = m.chain.get("completion_policy")
                if policy:
                    requirements = policy.get("children", [])
                    receipt_matches = [
                        name in m.children
                        and m.children[name].status in policy["statuses"]
                        for name in requirements
                    ]
                    if not (
                        all(receipt_matches)
                        if policy["policy"] == "all"
                        else any(receipt_matches)
                    ):
                        raise RuntimeError(
                            f"behaviour completion_policy: real child receipts unsatisfied for {identity}"
                        )
                if m.status not in {"completed", "failed", "canceled"}:
                    m.status = (
                        "failed" if m.state in {"failed", "rejected"} else "completed"
                    )
                if m.deadline is not None:
                    ctx.ops.append(CancelTimer(m.deadline))
                    m.deadline = None
                self._record(ctx, m, m.status, transition["id"])
            else:
                m.continued = "continued"
                ctx.ops.append(Activate(self.partition.id))

        for ref, fields in self.publish.items():
            for name, value in fields.items():
                ctx.set(ref, PREFIX + name, value)


def build(context: EngineBuild) -> Behaviour:
    return Behaviour(context)


def build_legacy_workflow(context: EngineBuild) -> Engine:
    """Pinned workflow/v1 compatibility mode, preserving historical WAL bytes."""
    from aeroagentsim.behaviours.compat import LegacyWorkflowExecutor

    return cast(Engine, LegacyWorkflowExecutor(context))


def build_legacy_threshold(context: EngineBuild) -> Engine:
    """Pinned sampled threshold/v1 compatibility mode in the shared runtime."""
    from aeroagentsim.behaviours.compat import LegacyThresholdExecutor

    return cast(Engine, LegacyThresholdExecutor(context))


class RecordedPredicate(Predicate):
    """Q6's single sampled adapter, extended with recorded portable truth events."""

    def __init__(self, context: EngineBuild) -> None:
        super().__init__(context)
        self.prior_record: dict[str, Any] | None = None
        self.partition = replace(
            self.partition,
            emits=tuple(
                sorted(set(self.partition.emits) | {PREFIX + "predicate_evaluated"})
            ),
            message_targets=tuple(
                sorted(set(self.partition.message_targets) | {PREFIX + "sampled"})
            ),
        )
        self.partitions = (self.partition,)

    def on_inputs(self, ctx: EngineContext) -> None:
        super().on_inputs(ctx)
        for operation in tuple(ctx.ops):
            if not isinstance(operation, SampleFrame):
                continue
            result = cast(dict[str, Any], thaw(cast(FrozenValue, operation.result)))
            at = instant(ctx.now.ns, ctx.now.microstep)
            if self.prior_record is not None:
                ctx.emit(
                    PREFIX + "predicate_evaluated",
                    {**self.prior_record, "op": "close", "validTo": at},
                    topic=PREFIX + "sampled",
                )
            acquired: dict[str, Any] = {}
            for role, field_id in self.fields:
                fact = ctx.view.field((self.spec.bindings[role], field_id), ctx.now)
                if isinstance(fact, Fact):
                    acquired[role + "." + field_id] = {
                        "clockId": fact.acquired.clock_id,
                        "mappingId": fact.acquired.mapping_id,
                        "numerator": str(fact.acquired.numerator),
                        "denominator": str(fact.acquired.denominator),
                        "producer": fact.producer,
                    }
            record = {
                "contextId": self.spec.context_id,
                "predicateId": self.target,
                "roles": {
                    role: {"$ref": ref.to_data()}
                    for role, ref in self.spec.bindings.items()
                },
                "profile": "aerograph_sampled/v1",
                "status": result["status"],
                "value": result["value"],
                "diagnostics": result["diagnostics"],
                "evaluatedAt": at,
                "readCut": {
                    "index": ctx.view.cut.index,
                    "at": instant(
                        ctx.view.cut.instant.ns, ctx.view.cut.instant.microstep
                    ),
                },
                "acquired": acquired,
                "validFrom": at,
                "validTo": None,
                "op": "assert",
            }
            ctx.emit(PREFIX + "predicate_evaluated", record, topic=PREFIX + "sampled")
            self.prior_record = record
