"""Compile pinned packages once; fail at the authored path before world binding."""

from __future__ import annotations

import copy
import hashlib
import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, ClassVar, cast

import yaml
from aerokernel import EntityRef
from aerokernel.values import canonical_json

from aeroagentsim.engines.predicate import VERSION, nodes
from aeroagentsim.engines.predicate_ast import TEMPORAL, validate_ast
from aeroagentsim.platform.plugins import EngineBuild

from .records import EVENTS
from .schema import FORMAT, CompileError, PackageIR

ACTIONS = {
    "set": ({"entity", "field", "value"}, set()),
    "emit": ({"schema", "topic", "payload"}, set()),
    "command": ({"payload"}, {"schema", "target", "capability", "deadline_ns"}),
    "cancel_command": ({"command"}, {"timeout_ns"}),
    "delay": ({"duration_ns"}, set()),
    "cancel_timer": ({"timer"}, set()),
    "assert_relation": ({"edge_id", "relation", "source", "target"}, set()),
    "close_relation": ({"edge_id"}, set()),
    "create_entity": ({"entity", "type"}, {"facts"}),
    "remove_entity": ({"entity"}, set()),
    "complete": (set(), {"status"}),
}
PROFILES = {"committed_reactive/v1", "aerograph_sampled/v1"}


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class Compiler:
    def __init__(self, source: str, build: EngineBuild | None) -> None:
        self.source, self.build = source, build
        self.roles: dict[str, str] = {}
        self.variables: set[str] = set()
        self.variable_schemas: dict[str, Any] = {}
        self.trigger_schema: Any = None
        self.trigger_root: str | None = None
        self.predicates: dict[str, Any] = {}

    def fail(self, path: str, message: str) -> None:
        raise CompileError(self.source, path, message)

    def shape(
        self,
        value: Any,
        path: str,
        required: set[str],
        optional: set[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            self.fail(path, "expected mapping")
        if missing := required - value.keys():
            self.fail(path, f"missing {sorted(missing)}")
        if extra := value.keys() - required - (optional or set()):
            self.fail(
                path,
                f"unsupported keys {sorted(extra)}; supply an explicit compiled adapter",
            )
        return cast(dict[str, Any], value)

    def text(self, value: Any, path: str) -> str:
        if type(value) is not str or not value:
            self.fail(path, "nonempty string required")
        return cast(str, value)

    def integer(self, value: Any, path: str, minimum: int = 1) -> int:
        if type(value) is not int or value < minimum:
            self.fail(path, f"integer >= {minimum} required")
        return cast(int, value)

    def sequence(self, value: Any, path: str) -> list[Any]:
        if not isinstance(value, list):
            self.fail(path, "list required")
        return cast(list[Any], value)

    def checked(self, path: str, function: Any, *args: Any) -> Any:
        try:
            return function(*args)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(path, str(exc))

    def role_types(self, value: Any, path: str) -> dict[str, str]:
        result = self.shape(
            value, path, set(), set(value) if isinstance(value, dict) else set()
        )
        for role, type_id in result.items():
            self.text(role, path)
            self.text(type_id, path + "." + role)
            if self.build:
                self.checked(
                    path + "." + role, self.build.registry.is_a, type_id, type_id
                )
        return cast(dict[str, str], result)

    def expr(self, value: Any, path: str, schema: Any = None) -> None:
        def contains_expression(child: Any) -> bool:
            if isinstance(child, dict):
                return bool(
                    {"$role", "$variable", "$trigger", "$now_ns", "$microstep", "field"}
                    & child.keys()
                ) or any(contains_expression(item) for item in child.values())
            return isinstance(child, list) and any(
                contains_expression(item) for item in child
            )

        if schema and self.build and not contains_expression(value):
            self.checked(path, self.build.registry.validate, schema, value)
            return
        if isinstance(value, list):
            for i, child in enumerate(value):
                self.expr(
                    child, f"{path}[{i}]", schema.get("items") if schema else None
                )
        elif isinstance(value, dict):
            if "$now_ns" in value or "$microstep" in value:
                key = "$now_ns" if "$now_ns" in value else "$microstep"
                self.shape(value, path, {key})
                if value[key] is not True or schema and schema["type"] != "integer":
                    self.fail(
                        path,
                        "canonical instant component requires true and an integer destination",
                    )
                return
            if "$role" in value:
                self.shape(value, path, {"$role"})
                if value["$role"] not in self.roles:
                    self.fail(path, "unknown role")
                if schema and schema["type"] != "ref":
                    self.fail(path, "role expression requires ref schema")
                if (
                    schema
                    and "target_type" in schema
                    and self.build
                    and not self.build.registry.is_a(
                        self.roles[value["$role"]], schema["target_type"]
                    )
                ):
                    self.fail(path, "role incompatible with target type")
                return
            if "$variable" in value:
                self.shape(value, path, {"$variable"})
                if value["$variable"] not in self.variables:
                    self.fail(path, "undeclared variable in binding")
                actual = self.variable_schemas.get(value["$variable"])
                if schema and actual and actual.get("type") != schema.get("type"):
                    self.fail(path, "variable schema is incompatible with destination")
                return
            if "$trigger" in value:
                self.shape(value, path, {"$trigger"})
                parts = self.text(value["$trigger"], path).split(".")
                if parts[0] not in {
                    "payload",
                    "result",
                    "status",
                    "command_id",
                    "evaluation",
                    "event",
                }:
                    self.fail(
                        path, "select an actual trigger payload/result/evaluation"
                    )
                selected_schema = (
                    self.trigger_schema if parts[0] == self.trigger_root else None
                )
                if selected_schema is not None:
                    for part in parts[1:]:
                        if (
                            selected_schema.get("type") != "record"
                            or part not in selected_schema["members"]
                        ):
                            self.fail(
                                path,
                                "trigger selector is outside the actual message/result schema",
                            )
                        selected_schema = selected_schema["members"][part]
                    if schema and selected_schema["type"] != schema["type"]:
                        self.fail(
                            path,
                            "trigger result schema is incompatible with destination",
                        )
                return
            if "field" in value and "role" in value:
                self.shape(value, path, {"field", "role"}, {"path"})
                if value["role"] not in self.roles:
                    self.fail(path, "unknown field role")
                if self.build:
                    field = self.checked(
                        path, self.build.registry.field, value["field"]
                    )
                    if not self.build.registry.is_a(
                        self.roles[value["role"]], field.declaring_type
                    ):
                        self.fail(path, "field is incompatible with role type")
                    selected_schema = field.schema
                    for part in value.get("path", []):
                        if (
                            selected_schema["type"] == "record"
                            and part in selected_schema["members"]
                        ):
                            selected_schema = selected_schema["members"][part]
                        elif (
                            selected_schema["type"] in {"array", "vector"}
                            and type(part) is int
                        ):
                            selected_schema = selected_schema["items"]
                        else:
                            self.fail(
                                path, "field selector is outside its actual schema"
                            )
                    if schema and selected_schema["type"] != schema["type"]:
                        self.fail(
                            path, "field value schema is incompatible with destination"
                        )
                return
            if "relation_assertion_id" in value:
                self.shape(
                    value, path, {"relation_assertion_id", "source_role", "target_role"}
                )
                if (
                    value["source_role"] not in self.roles
                    or value["target_role"] not in self.roles
                ):
                    self.fail(
                        path,
                        "relation assertion expression requires bound endpoint roles",
                    )
                if schema and schema["type"] != "string":
                    self.fail(
                        path, "relation assertion ID requires a string destination"
                    )
                if self.build:
                    self.checked(
                        path,
                        self.build.registry.relation,
                        value["relation_assertion_id"],
                    )
                return
            if any(
                key in value
                for key in (
                    "stable_id",
                    "choose",
                    "selector",
                    "minimum_known_feasible_eta",
                    "relation_assertion_id",
                    "typed_choice",
                    "stable_entity_ref",
                )
            ):
                self.fail(
                    path,
                    "unsupported computation; use a typed command/event from an explicit domain computation owner",
                )
            members = schema.get("members", {}) if schema else {}
            if (
                schema
                and schema.get("type") == "record"
                and (
                    set(schema["required"]) - value.keys()
                    or not schema["extra"]
                    and value.keys() - members.keys()
                )
            ):
                self.fail(path, "payload schema member coverage mismatch")
            for name, child in value.items():
                self.expr(child, path + "." + name, members.get(name))
        elif schema and self.build:
            self.checked(path, self.build.registry.validate, schema, value)

    def predicate(self, value: Any, path: str) -> None:
        p = self.shape(
            value,
            path,
            {"profile", "roles", "expression"},
            {"parameters", "adapter", "use"},
        )
        if p["profile"] not in PROFILES:
            self.fail(path + ".profile", "unsupported evaluation profile")
        roles = self.role_types(p["roles"], path + ".roles")
        self.checked(path + ".expression", validate_ast, p["expression"], path)
        for node in nodes(p["expression"]):
            if p["profile"] == "committed_reactive/v1" and (
                node.get("op") in TEMPORAL or "time" in node
            ):
                self.fail(
                    path + ".expression",
                    "reactive profile requires stateless Boolean AST; use Q6 sampled adapter",
                )
            if "field" in node:
                role = node.get("role")
                if role not in roles:
                    self.fail(path + ".expression", f"unbound role {role}")
                if self.build:
                    descriptor = self.checked(
                        path + ".expression", self.build.registry.field, node["field"]
                    )
                    if not self.build.registry.is_a(
                        roles[cast(str, role)], descriptor.declaring_type
                    ):
                        self.fail(
                            path + ".expression",
                            "field incompatible with declared role",
                        )
            if "parameter" in node and node["parameter"] not in p.get("parameters", {}):
                self.fail(path + ".parameters", f"missing {node['parameter']}")
            if "relation" in node:
                if (
                    node.get("sourceRole") not in roles
                    or node.get("targetRole") not in roles
                ):
                    self.fail(
                        path + ".expression",
                        "relation requires bound source/target roles",
                    )
                if self.build:
                    self.checked(
                        path + ".expression",
                        self.build.registry.relation,
                        node["relation"],
                    )
        if p["profile"] == "aerograph_sampled/v1":
            adapter = self.shape(
                p.get("adapter"), path + ".adapter", {"event"}, {"context", "contexts"}
            )
            self.text(adapter["event"], path + ".adapter.event")
            if ("context" in adapter) == ("contexts" in adapter):
                self.fail(
                    path + ".adapter", "declare one context or a finite contexts pool"
                )
            contexts = adapter.get("contexts", [adapter.get("context")])
            for child in self.sequence(contexts, path + ".adapter.contexts"):
                self.text(child, path + ".adapter.contexts")
            if not contexts or len(set(contexts)) != len(contexts):
                self.fail(
                    path + ".adapter.contexts",
                    "nonempty unique finite contexts required",
                )

    def trigger(self, value: Any, path: str, *, initial: bool = False) -> None:
        categories = {
            "predicate",
            "event",
            "timer",
            "receipt",
            "lifecycle",
            "instance",
            "deadline",
        }
        t = self.shape(
            value,
            path,
            set(),
            categories
            | {"edge", "status", "policy", "correlation", "result_matches", "after_ns"},
        )
        if len(categories & t.keys()) != 1:
            self.fail(path, "exactly one explicit trigger category required")
        if "after_ns" in t:
            if not initial or "timer" not in t:
                self.fail(
                    path + ".after_ns",
                    "only template timer triggers schedule an initial delay",
                )
            self.integer(t["after_ns"], path + ".after_ns", 0)
        if initial and "timer" in t and "after_ns" not in t:
            self.fail(
                path + ".after_ns", "template timer trigger requires an explicit delay"
            )
        if initial and (
            {"receipt", "deadline"} & t.keys() or t.get("instance") == "continued"
        ):
            self.fail(
                path,
                "initial trigger requires an external/dependency/lifecycle/timer cause or instance activation",
            )
        if "predicate" in t:
            self.pred(t["predicate"], path)
            if t.get("edge") not in {"entered", "exited", "while"}:
                self.fail(path + ".edge", "declare entered/exited/while")
        if "receipt" in t and t.get("status") is None:
            self.fail(path + ".status", "explicit receipt statuses required")
        if "receipt" in t:
            statuses = t["status"] if isinstance(t["status"], list) else [t["status"]]
            if not statuses or set(statuses) - {
                "submitted",
                "accepted",
                "executing",
                "canceling",
                "succeeded",
                "failed",
                "rejected",
                "canceled",
            }:
                self.fail(path + ".status", "unknown receipt status")
            if t.get("policy", "all") not in {"all", "any"}:
                self.fail(path + ".policy", "all or any required")
        if "result_matches" in t:
            if "receipt" not in t:
                self.fail(
                    path + ".result_matches",
                    "receipt result correlation requires a receipt trigger",
                )
            self.shape(
                t["result_matches"],
                path + ".result_matches",
                set(),
                set(t["result_matches"]),
            )
            self.expr(t["result_matches"], path + ".result_matches")
        if "instance" in t and t["instance"] not in {"activated", "continued"}:
            self.fail(path, "instance trigger must be activated or continued")
        if "lifecycle" in t and t["lifecycle"] not in {"created", "removed"}:
            self.fail(path, "lifecycle trigger must be created or removed")
        if "event" in t and self.build:
            descriptor = self.checked(
                path + ".event", self.build.registry.message, t["event"]
            )
            if descriptor.kind != "event":
                self.fail(path, "event descriptor required")
        if "correlation" in t:
            if "requires_event" in t["correlation"]:
                self.fail(
                    path + ".correlation.requires_event",
                    "durable event prerequisites require explicit chain states",
                )
            self.expr(t["correlation"], path + ".correlation")

    def pred(self, name: Any, path: str) -> None:
        if name not in self.predicates:
            self.fail(path, f"unknown predicate {name}")

    def owned(
        self, entity: Any, slot: str, path: str, type_id: str | None = None
    ) -> None:
        if not self.build:
            return
        descriptor = self.checked(path, self.build.registry.field, slot)
        if isinstance(entity, dict) and "$role" in entity:
            type_id = self.roles[entity["$role"]]
            candidates = [
                r
                for r in self.build.entities
                if self.build.registry.is_a(r.type_id, type_id)
            ]
            candidates.append(
                EntityRef(
                    self.build.manifest.run_id,
                    self.build.manifest.epoch,
                    "__future__",
                    0,
                    type_id,
                )
            )
        else:
            candidates = [r for r in self.build.entities if r.id == entity]
            if type_id:
                candidates.append(
                    EntityRef(
                        self.build.manifest.run_id,
                        self.build.manifest.epoch,
                        entity if isinstance(entity, str) else "__future__",
                        0,
                        type_id,
                    )
                )
        if not candidates:
            self.fail(path, "declare target role/type")
        for ref in candidates:
            if not self.build.registry.is_a(
                ref.type_id, descriptor.declaring_type
            ) or slot not in self.build.owned_fields(ref):
                self.fail(
                    path,
                    f"{slot} is unowned/inapplicable for {ref.type_id}; command the configured field owner",
                )

    def action(self, value: Any, path: str) -> None:
        if not isinstance(value, dict) or value.get("kind") not in ACTIONS:
            self.fail(path, "unsupported action kind")
        kind = value["kind"]
        required, optional = ACTIONS[kind]
        a = self.shape(value, path, {"id", "kind"} | required, optional)
        self.text(a["id"], path + ".id")
        for key in {"duration_ns", "deadline_ns", "timeout_ns"} & a.keys():
            self.integer(a[key], path + "." + key, 0 if key == "duration_ns" else 1)
        if kind == "complete" and a.get("status", "completed") not in {
            "completed",
            "failed",
            "canceled",
        }:
            self.fail(
                path + ".status",
                "complete requires an explicit terminal lifecycle status",
            )
        if kind == "set":
            self.expr(
                a["entity"],
                path + ".entity",
                {"type": "ref"} if isinstance(a["entity"], dict) else None,
            )
            self.owned(a["entity"], a["field"], path + ".field")
            schema = (
                self.build.registry.field(a["field"]).schema if self.build else None
            )
            self.expr(a["value"], path + ".value", schema)
        elif kind in {"command", "emit"}:
            if kind == "emit" and a["schema"] in EVENTS:
                self.fail(
                    path + ".schema",
                    "behaviour record schemas are emitted exclusively by the executor",
                )
            resolved = a
            if kind == "command":
                if ("capability" in a) == ("schema" in a or "target" in a):
                    self.fail(path, "declare capability or explicit schema+target")
                if "capability" in a:
                    if self.build:
                        resolved = self.build.config.get("capabilities", {}).get(
                            a["capability"]
                        )
                        if not isinstance(resolved, dict) or set(resolved) != {
                            "schema",
                            "target",
                        }:
                            self.fail(
                                path + ".capability",
                                "configure exact schema/target capability",
                            )
                    else:
                        resolved = {}
                elif not {"schema", "target"} <= a.keys():
                    self.fail(path, "explicit schema and target required")
            if self.build:
                descriptor = self.checked(
                    path + ".schema", self.build.registry.message, resolved["schema"]
                )
                if descriptor.kind != ("command" if kind == "command" else "event"):
                    self.fail(path, "incorrect message kind")
                self.expr(a["payload"], path + ".payload", descriptor.schema)
            else:
                self.expr(a["payload"], path + ".payload")
        elif kind == "create_entity":
            if self.build:
                descriptor_t = next(
                    (t for t in self.build.registry.types if t.id == a["type"]), None
                )
                if descriptor_t is None or descriptor_t.abstract:
                    self.fail(path + ".type", "concrete registered type required")
                self.lifecycle(a["entity"], a["type"], path)
            self.expr(a["entity"], path + ".entity")
            for slot, val in a.get("facts", {}).items():
                self.owned(a["entity"], slot, path + ".facts." + slot, a["type"])
                self.expr(
                    val,
                    path + ".facts." + slot,
                    self.build.registry.field(slot).schema if self.build else None,
                )
        elif kind in {"assert_relation", "close_relation", "remove_entity"}:
            for key in required - {"relation"}:
                self.expr(a[key], path + "." + key)
            if kind == "assert_relation" and self.build:
                descriptor_r = self.checked(
                    path + ".relation", self.build.registry.relation, a["relation"]
                )
                for key, target_type in (
                    ("source", descriptor_r.source_type),
                    ("target", descriptor_r.target_type),
                ):
                    self.expr(
                        a[key],
                        path + "." + key,
                        {"type": "ref", "target_type": target_type},
                    )
                source = a["source"]
                source_type = (
                    self.roles[source["$role"]]
                    if isinstance(source, dict) and "$role" in source
                    else None
                )
                rules = [
                    r
                    for r in self.build.manifest.relation_rules
                    if r.relation_id == a["relation"]
                    and source_type
                    and self.build.registry.is_a(source_type, r.source_type)
                ]
                if not rules or any(
                    r.partition != self.build.id
                    for r in rules
                    if r.priority == max(s.priority for s in rules)
                ):
                    self.fail(
                        path + ".relation",
                        "configure this behaviour partition as relation writer",
                    )
            if kind == "remove_entity" and self.build:
                target = a["entity"]
                if isinstance(target, dict) and "$role" in target:
                    self.lifecycle(target, self.roles[target["$role"]], path)

    def lifecycle(self, identity: Any, type_id: str, path: str) -> None:
        assert self.build is not None
        rules = [
            r
            for r in self.build.manifest.lifecycle
            if self.build.registry.is_a(type_id, r.type_id)
            and (not isinstance(identity, str) or fnmatchcase(identity, r.id_pattern))
        ]
        if not rules:
            self.fail(path, "configure an explicit lifecycle controller domain")
        winners = [r for r in rules if r.priority == max(s.priority for s in rules)]
        if len(winners) != 1 or winners[0].partition != self.build.id:
            self.fail(
                path, "lifecycle controller is ambiguous or owned by another partition"
            )


def compile_package(
    document: dict[str, Any],
    build: EngineBuild | None = None,
    *,
    source: str = "<inline>",
) -> PackageIR:
    c = Compiler(source, build)
    p = copy.deepcopy(document)
    c.shape(
        p,
        "$",
        {"format", "id", "revision", "predicates", "chains", "bindings", "budgets"},
        {
            "registry",
            "evaluator",
            "conflicts",
            "injection_points",
            "bootstrap_relations",
            "imports",
            "feedback",
            "sampled_contexts",
            "requires_compiler_features",
        },
    )
    if p["format"] != FORMAT:
        c.fail("$.format", f"expected {FORMAT}")
    c.text(p["id"], "$.id")
    c.integer(p["revision"], "$.revision")
    registry_requirements = c.shape(
        p.get("registry", {}), "$.registry", set(), {"snapshot", "digest_ref"}
    )
    if (
        "digest_ref" in registry_requirements
        and registry_requirements["digest_ref"] != "scenario.registry_digest"
    ):
        c.fail(
            "$.registry.digest_ref",
            "registry requirements bind the run's pinned scenario.registry_digest",
        )
    if "snapshot" in registry_requirements:
        c.text(registry_requirements["snapshot"], "$.registry.snapshot")
    if p.get("evaluator", {}).get("version", VERSION) != VERSION:
        c.fail("$.evaluator.version", "unsupported evaluator revision")
    evaluator = c.shape(
        p.get("evaluator", {}),
        "$.evaluator",
        set(),
        {
            "version",
            "dialect",
            "native_references_ref",
            "source_sha256",
            "native_references",
        },
    )
    if evaluator.get("dialect", "original") not in {"original", "expanded"}:
        c.fail("$.evaluator.dialect", "supported Q6 dialects are original and expanded")
    if "source_sha256" in evaluator:
        from aeroagentsim.engines import predicate_ast

        assert predicate_ast.__file__ is not None
        if (
            hashlib.sha256(Path(predicate_ast.__file__).read_bytes()).hexdigest()
            != evaluator["source_sha256"]
        ):
            c.fail("$.evaluator.source_sha256", "pinned Q6 library digest mismatch")
    supported_features = {
        "relation tuple selectors",
        "predicate role aliases",
        "bootstrap relation wave",
        "receipt/event correlation and retained children",
        "positive-lag sampled-feedback partition",
        "finite sampled context pool",
    }
    for feature in c.sequence(
        p.get("requires_compiler_features", []), "$.requires_compiler_features"
    ):
        if feature not in supported_features:
            c.fail(
                "$.requires_compiler_features",
                f"unsupported required feature {feature!r}",
            )
    if "feedback" in p:
        feedback = c.shape(
            p["feedback"],
            "$.feedback",
            {"profile", "return_lag_ns", "partition"},
            {"reason"},
        )
        if feedback["profile"] != "aerograph_sampled/v1":
            c.fail("$.feedback.profile", "sampled feedback profile required")
        c.integer(feedback["return_lag_ns"], "$.feedback.return_lag_ns")
        c.text(feedback["partition"], "$.feedback.partition")
        if build is not None and feedback["partition"] != build.id:
            c.fail(
                "$.feedback.partition",
                "this profile delays the actual shared behaviour partition; name its engine ID",
            )
    pool_ids: set[str] = set()
    for i, context in enumerate(
        c.sequence(p.get("sampled_contexts", []), "$.sampled_contexts")
    ):
        path = f"$.sampled_contexts[{i}]"
        c.shape(
            context,
            path,
            {"id", "roles", "sources", "clocks", "predicates"},
            {"same_identity_roles"},
        )
        context_id = c.text(context["id"], path + ".id")
        if context_id in pool_ids:
            c.fail(path + ".id", "duplicate finite pool context")
        pool_ids.add(context_id)
        if set(context["roles"]) != set(context["sources"]) or set(
            context["roles"]
        ) != set(context["clocks"]):
            c.fail(path, "roles, sources and clocks must name the same finite tuple")
        for role, identity in context["roles"].items():
            c.text(identity, path + ".roles." + role)
            c.text(context["sources"][role], path + ".sources." + role)
            clock = c.sequence(context["clocks"][role], path + ".clocks." + role)
            if len(clock) != 2:
                c.fail(path + ".clocks." + role, "explicit clock/mapping pair required")
            for value in clock:
                c.text(value, path + ".clocks." + role)
        for group in context.get("same_identity_roles", []):
            if (
                any(role not in context["roles"] for role in group)
                or len({context["roles"][role] for role in group}) != 1
            ):
                c.fail(
                    path + ".same_identity_roles",
                    "aliases must bind the same declared identity",
                )
        for predicate_id in c.sequence(context["predicates"], path + ".predicates"):
            definition = p["predicates"].get(predicate_id)
            if (
                definition is None
                or definition["profile"] != "aerograph_sampled/v1"
                or set(definition["roles"]) != set(context["roles"])
            ):
                c.fail(
                    path + ".predicates",
                    "sampled predicate and matching role tuple required",
                )
            if build:
                refs = {ref.id: ref for ref in build.entities}
                for role, identity in context["roles"].items():
                    if identity not in refs or not build.registry.is_a(
                        refs[identity].type_id, definition["roles"][role]
                    ):
                        c.fail(
                            path + ".roles." + role,
                            "pool slot must bind a compatible predeclared entity generation",
                        )
            adapter = definition.setdefault(
                "adapter", {"event": "aas.behaviour.sample_signal", "contexts": []}
            )
            if "contexts" not in adapter:
                c.fail(path, "finite pool predicates use adapter.contexts")
            sample_id = context_id + "/" + predicate_id
            if sample_id not in adapter["contexts"]:
                adapter["contexts"].append(sample_id)
    if "bootstrap_relations" in p:
        bootstrap = c.shape(
            p["bootstrap_relations"],
            "$.bootstrap_relations",
            {"assertions"},
            {"owner", "acquired_ns", "valid_from_ns"},
        )
        for name in ("acquired_ns", "valid_from_ns"):
            if bootstrap.get(name, 0) != 0:
                c.fail(
                    "$.bootstrap_relations." + name,
                    "bootstrap relation stamps must be the actual initial canonical instant (0); inject later assertions as events",
                )
        if build and bootstrap.get("owner", build.id) != build.id:
            c.fail(
                "$.bootstrap_relations.owner",
                "must name the configured behaviour writer",
            )
        for i, assertion in enumerate(
            c.sequence(bootstrap["assertions"], "$.bootstrap_relations.assertions")
        ):
            apath = f"$.bootstrap_relations.assertions[{i}]"
            c.shape(assertion, apath, {"id", "relation", "source", "target"})
            if build:
                descriptor_r = c.checked(
                    apath, build.registry.relation, assertion["relation"]
                )
                for key, target_type in (
                    ("source", descriptor_r.source_type),
                    ("target", descriptor_r.target_type),
                ):
                    c.checked(
                        apath + "." + key,
                        build.registry.validate,
                        {"type": "ref", "target_type": target_type},
                        assertion[key],
                    )
    c.shape(
        p["budgets"],
        "$.budgets",
        {"max_transitions_per_instance_per_ns", "max_instances"},
        {"max_timers_per_instance", "max_creations_per_instance"},
    )
    for name, value in p["budgets"].items():
        c.integer(value, "$.budgets." + name)
    c.predicates = c.shape(
        p["predicates"],
        "$.predicates",
        set(),
        set(p["predicates"]) if isinstance(p["predicates"], dict) else set(),
    )
    c.shape(
        p["chains"],
        "$.chains",
        set(),
        set(p["chains"]) if isinstance(p["chains"], dict) else set(),
    )
    dependencies = {}
    for name, definition in c.predicates.items():
        c.predicate(definition, "$.predicates." + name)
        dependencies[name] = tuple(
            sorted(
                {
                    (n["role"], n["field"])
                    for n in nodes(definition["expression"])
                    if "field" in n
                }
            )
        )
    bindings = c.sequence(p["bindings"], "$.bindings")
    for i, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            c.fail(f"$.bindings[{i}]", "binding mapping required")
    ids = [b.get("id") for b in bindings]
    if len(set(ids)) != len(ids):
        c.fail("$.bindings", "duplicate binding IDs")
    for name, chain in p["chains"].items():
        path = "$.chains." + name
        c.shape(
            chain,
            path,
            {"roles", "trigger", "initial", "terminal", "states", "transitions"},
            {
                "preconditions",
                "variables",
                "transition_policy",
                "completion_policy",
                "deadline_ns",
                "predicate_roles",
            },
        )
        c.roles = c.role_types(chain["roles"], path + ".roles")
        if "deadline_ns" in chain:
            c.integer(chain["deadline_ns"], path + ".deadline_ns")
        if chain.get("variables"):
            c.fail(
                path + ".variables",
                "declare typed variables on bindings, where their sources are resolved",
            )
        variable_sets = [
            set(b.get("variables", {})) for b in bindings if b.get("chain") == name
        ]
        c.variables = set.intersection(*variable_sets) if variable_sets else set()
        c.variable_schemas = {}
        if build:
            for binding in bindings:
                if binding.get("chain") != name:
                    continue
                for key, expression in binding.get("variables", {}).items():
                    if isinstance(expression, dict) and "field" in expression:
                        schema = build.registry.field(expression["field"]).schema
                        for part in expression.get("path", []):
                            schema = (
                                schema["members"][part]
                                if schema["type"] == "record"
                                else schema["items"]
                            )
                        c.variable_schemas[key] = schema
        states = c.sequence(chain["states"], path + ".states")
        if (
            len(states) != len(set(states))
            or chain["initial"] not in states
            or set(chain["terminal"]) - set(states)
        ):
            c.fail(
                path + ".states",
                "unique states and known initial/terminal states required",
            )
        if chain.get("transition_policy", "first_enabled") != "first_enabled":
            c.fail(path + ".transition_policy", "supported policy is first_enabled")
        c.trigger(chain["trigger"], path + ".trigger", initial=True)
        for pred in chain.get("preconditions", []):
            c.pred(pred, path + ".preconditions")
        command_schemas: dict[str, str] = {}
        if build:
            for transition in chain["transitions"]:
                for action in transition["actions"]:
                    if action.get("kind") != "command":
                        continue
                    resolved = (
                        build.config.get("capabilities", {}).get(
                            action["capability"], {}
                        )
                        if "capability" in action
                        else action
                    )
                    if "schema" in resolved:
                        if (
                            action["id"] in command_schemas
                            and command_schemas[action["id"]] != resolved["schema"]
                        ):
                            c.fail(
                                path + ".transitions",
                                "reused child ID changes command schema",
                            )
                        command_schemas[action["id"]] = resolved["schema"]
        tids = []
        action_ids: set[str] = set()
        action_kinds: dict[str, set[str]] = {}
        for i, transition in enumerate(
            c.sequence(chain["transitions"], path + ".transitions")
        ):
            tpath = f"{path}.transitions[{i}]"
            c.shape(
                transition,
                tpath,
                {"id", "from", "on", "to", "actions"},
                {"guard", "priority"},
            )
            tids.append(c.text(transition["id"], tpath + ".id"))
            if transition["from"] not in states or transition["to"] not in states:
                c.fail(tpath, "unknown source/target state")
            if type(transition.get("priority", 0)) is not int:
                c.fail(tpath + ".priority", "integer required")
            c.trigger(transition["on"], tpath + ".on")
            if "guard" in transition:
                c.pred(transition["guard"], tpath + ".guard")
            c.trigger_schema, c.trigger_root = None, None
            event_trigger = transition["on"]
            if (
                event_trigger.get("instance") == "activated"
                and "event" in chain["trigger"]
            ):
                event_trigger = chain["trigger"]
            if build and "event" in event_trigger:
                c.trigger_root = "payload"
                c.trigger_schema = build.registry.message(event_trigger["event"]).schema
            if build and "receipt" in transition["on"]:
                selected = transition["on"]["receipt"]
                children = selected if isinstance(selected, list) else [selected]
                if children[0] in command_schemas:
                    c.trigger_root = "result"
                    c.trigger_schema = build.registry.message(
                        command_schemas[children[0]]
                    ).result_schema
            local_ids = []
            for j, action in enumerate(
                c.sequence(transition["actions"], tpath + ".actions")
            ):
                c.action(action, f"{tpath}.actions[{j}]")
                local_ids.append(action["id"])
                action_ids.add(action["id"])
                action_kinds.setdefault(action["id"], set()).add(action["kind"])
            if len(local_ids) != len(set(local_ids)):
                c.fail(tpath + ".actions", "duplicate authored action ID in transition")
        if len(tids) != len(set(tids)):
            c.fail(path + ".transitions", "duplicate transition ID")
        for i, transition in enumerate(chain["transitions"]):
            for key in {"timer", "receipt", "deadline"} & transition["on"].keys():
                values = transition["on"][key]
                selected = values if isinstance(values, list) else [values]
                expected_kind = "delay" if key == "timer" else "command"
                accepted = {
                    identity
                    for identity, kinds in action_kinds.items()
                    if kinds == {expected_kind}
                }
                if key == "deadline" and "deadline_ns" in chain:
                    accepted.add("instance")
                if key == "timer" and "timer" in chain["trigger"]:
                    accepted.add(chain["trigger"]["timer"])
                if set(selected) - accepted:
                    c.fail(
                        f"{path}.transitions[{i}].on.{key}",
                        "reference must name an actual compatible child/timer",
                    )
        if "completion_policy" in chain:
            policy = c.shape(
                chain["completion_policy"],
                path + ".completion_policy",
                {"children", "statuses", "policy"},
                {"terminal_states"},
            )
            if (
                policy["policy"] not in {"all", "any"}
                or not policy["children"]
                or set(policy["children"]) - action_ids
            ):
                c.fail(
                    path + ".completion_policy", "declare all/any with actual child IDs"
                )
            if "terminal_states" in policy and (
                not policy["terminal_states"]
                or set(policy["terminal_states"]) - set(chain["terminal"])
            ):
                c.fail(
                    path + ".completion_policy.terminal_states",
                    "select declared terminal states",
                )
    for i, binding in enumerate(bindings):
        path = f"$.bindings[{i}]"
        c.shape(
            binding,
            path,
            {"id", "chain", "match", "multiplicity", "on_unbind"},
            {"variables", "episode_field", "episode_role", "selection"},
        )
        if binding["chain"] not in p["chains"]:
            c.fail(path + ".chain", "unknown chain")
        c.roles = p["chains"][binding["chain"]]["roles"]
        c.variables = set(binding.get("variables", {}))
        c.trigger_schema, c.trigger_root = None, None
        if set(binding["match"]) - c.roles.keys():
            c.fail(path + ".match", "unknown role")
        for role, selector in binding["match"].items():
            c.shape(
                selector,
                path + ".match." + role,
                set(),
                {
                    "is_a",
                    "entity",
                    "field",
                    "equals",
                    "in",
                    "relation",
                    "source_role",
                    "target_role",
                },
            )
            if "relation" in selector and (
                {"source_role", "target_role"} - selector.keys()
                or selector["source_role"] not in c.roles
                or selector["target_role"] not in c.roles
            ):
                c.fail(
                    path + ".match." + role, "relation needs valid source/target roles"
                )
            if build and "is_a" in selector:
                compatible = c.checked(
                    path + ".match." + role,
                    build.registry.is_a,
                    selector["is_a"],
                    c.roles[role],
                )
                if not compatible and not build.registry.is_a(
                    c.roles[role], selector["is_a"]
                ):
                    c.fail(
                        path + ".match." + role + ".is_a",
                        "selector type is incompatible with the chain role",
                    )
            if "field" in selector:
                if not ({"equals", "in"} & selector.keys()):
                    c.fail(
                        path + ".match." + role,
                        "field selector requires an explicit equals/in constraint",
                    )
                if build:
                    descriptor_f = c.checked(
                        path + ".match." + role + ".field",
                        build.registry.field,
                        selector["field"],
                    )
                    if not build.registry.is_a(
                        c.roles[role], descriptor_f.declaring_type
                    ):
                        c.fail(
                            path + ".match." + role,
                            "selector field is incompatible with the role",
                        )
                    values = (
                        selector["in"] if "in" in selector else [selector["equals"]]
                    )
                    for value in values:
                        c.checked(
                            path + ".match." + role,
                            build.registry.validate,
                            descriptor_f.schema,
                            value,
                        )
        if binding["multiplicity"] not in {
            "once_per_entity",
            "once_per_relation",
            "once_per_task_episode",
        }:
            c.fail(path + ".multiplicity", "unsupported multiplicity")
        if (
            binding["multiplicity"] == "once_per_task_episode"
            and "episode_field" not in binding
        ):
            c.fail(path + ".episode_field", "explicit task episode field required")
        if binding["multiplicity"] == "once_per_relation" and not any(
            "relation" in selector for selector in binding["match"].values()
        ):
            c.fail(
                path + ".multiplicity",
                "once_per_relation requires an actual relation tuple selector",
            )
        if (
            binding["multiplicity"] == "once_per_task_episode"
            and binding.get("episode_role", "task") not in c.roles
        ):
            c.fail(
                path + ".episode_role",
                "select a declared role for the real task episode field",
            )
        if binding["on_unbind"] not in {"close_after_cleanup", "retain_until_terminal"}:
            c.fail(path + ".on_unbind", "explicit cleanup/retention policy required")
        if "selection" in binding:
            selection = c.shape(
                binding["selection"],
                path + ".selection",
                {"policy", "field", "role", "group_roles", "tie_break", "limit"},
            )
            c.integer(selection["limit"], path + ".selection.limit")
            c.sequence(selection["group_roles"], path + ".selection.group_roles")
            if (
                selection["policy"] != "minimum"
                or selection["tie_break"] != "EntityRef"
                or selection["limit"] != 1
            ):
                c.fail(
                    path + ".selection",
                    "supported selection is minimum with EntityRef ties and limit 1",
                )
            if (
                selection["role"] not in c.roles
                or not selection["group_roles"]
                or set(selection["group_roles"]) - c.roles.keys()
            ):
                c.fail(path + ".selection", "declare bound ranking and grouping roles")
            if build:
                field = c.checked(
                    path + ".selection.field", build.registry.field, selection["field"]
                )
                if field.schema["type"] not in {
                    "number",
                    "integer",
                } or not build.registry.is_a(
                    c.roles[selection["role"]], field.declaring_type
                ):
                    c.fail(
                        path + ".selection.field",
                        "ranking must read an applicable numeric field",
                    )
        for key, val in binding.get("variables", {}).items():

            def references_trigger_or_variable(value: Any) -> bool:
                return (
                    isinstance(value, dict)
                    and (
                        bool({"$trigger", "$variable"} & value.keys())
                        or any(
                            references_trigger_or_variable(child)
                            for child in value.values()
                        )
                    )
                    or isinstance(value, list)
                    and any(references_trigger_or_variable(child) for child in value)
                )

            if references_trigger_or_variable(val):
                c.fail(
                    path + ".variables." + key,
                    "binding variables require actual fields, roles or literals; trigger/cross-variable values require a transition",
                )
            c.expr(val, path + ".variables." + key)
    for i, rule in enumerate(p.get("conflicts", [])):
        path = f"$.conflicts[{i}]"
        c.shape(
            rule,
            path,
            {"id", "roles", "predicate", "edge", "emits"},
            {"match", "topic", "payload"},
        )
        c.roles = c.role_types(rule["roles"], path + ".roles")
        c.variables = set()
        c.trigger_schema, c.trigger_root = None, None
        c.pred(rule["predicate"], path + ".predicate")
        if set(c.predicates[rule["predicate"]]["roles"]) - c.roles.keys():
            c.fail(path + ".roles", "conflict rule must bind every predicate role")
        if rule["edge"] not in {"entered", "exited", "while"}:
            c.fail(path + ".edge", "entered/exited/while required")
        if build:
            message = c.checked(path + ".emits", build.registry.message, rule["emits"])
            if message.kind != "event":
                c.fail(path + ".emits", "conflicts emit typed events")
            if "payload" in rule:
                c.expr(rule["payload"], path + ".payload", message.schema)
    point_ids = []
    for i, point in enumerate(p.get("injection_points", [])):
        path = f"$.injection_points[{i}]"
        c.shape(
            point, path, {"id", "stream_id", "command", "target", "emits"}, {"topic"}
        )
        point_ids.append(point["id"])
        if point["emits"] in EVENTS:
            c.fail(
                path + ".emits",
                "injection points cannot fabricate runtime truth or lifecycle records",
            )
        if build:
            if point["target"] != build.id:
                c.fail(path + ".target", "must address this behaviour engine")
            for key, kind in (("command", "command"), ("emits", "event")):
                desc = c.checked(path + "." + key, build.registry.message, point[key])
                if desc.kind != kind:
                    c.fail(path + "." + key, f"expected {kind}")
    if len(point_ids) != len(set(point_ids)):
        c.fail("$.injection_points", "duplicate injection point IDs")
    # Binding/rule declaration order is not execution priority; IDs and explicit
    # priority select it. Preserve transition/action order, which is semantic.
    p["bindings"].sort(key=lambda binding: binding["id"])
    if "conflicts" in p:
        p["conflicts"].sort(key=lambda rule: rule["id"])
    return PackageIR(p, digest(p), source, dependencies)


class UniqueLoader(yaml.SafeLoader):
    yaml_implicit_resolvers: ClassVar[dict[Any, Any]] = {  # type: ignore[misc]
        key: [(tag, regex) for tag, regex in entries if tag != "tag:yaml.org,2002:bool"]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def _mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    pairs: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if type(key) is not str or key in pairs:
            raise ValueError(
                f"YAML line {key_node.start_mark.line + 1}: duplicate/nonstring key {key}"
            )
        pairs[key] = loader.construct_object(value_node)
    return pairs


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def resolve_packages(specs: list[Any], base: Path) -> list[dict[str, Any]]:
    """Resolve content-pinned imports from the scenario root, preserving closure."""
    result = []
    active: set[Path] = set()
    seen: dict[str, str] = {}

    def load(spec: Any, directory: Path, authored_path: str) -> None:
        if not isinstance(spec, dict):
            raise CompileError(
                str(directory), authored_path, "package mapping required"
            )
        source = str(directory) + ":" + authored_path
        if "path" in spec:
            if set(spec) != {"path", "sha256"}:
                raise CompileError(
                    source,
                    authored_path,
                    "external package requires exact path and sha256",
                )
            path = (directory / spec["path"]).resolve()
            if path in active:
                raise CompileError(str(path), "$", "cyclic package import")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
                raise CompileError(str(path), "$", "package sha256 mismatch")
            active.add(path)
            document = yaml.load(raw, Loader=UniqueLoader)
            source = str(path)
            directory = path.parent
        else:
            document = spec.get("document", spec)
            path = None
        if spec.get("format") != "aeroagentsim.behaviour-ir/v1":
            for i, imported in enumerate(document.get("imports", [])):
                load(imported, directory, f"$.imports[{i}]")
        ir = compile_package(document, source=source)
        if ir.document["id"] in seen:
            if seen[ir.document["id"]] != ir.digest:
                raise CompileError(
                    source, "$.id", "conflicting package identity in closure"
                )
        else:
            seen[ir.document["id"]] = ir.digest
            result.append(ir.to_data())
        if path is not None:
            active.remove(path)

    for i, spec in enumerate(specs):
        load(spec, base, f"$.behaviours[{i}]")
    return result
