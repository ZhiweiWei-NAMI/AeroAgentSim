"""Version-pinned AeroGraph AST evaluation over settled, committed kernel inputs."""

from __future__ import annotations

import copy
import hashlib
from bisect import bisect_left, bisect_right
from decimal import Decimal
from typing import Any, cast

from aerokernel import Activate, Dependency, Partition
from aerokernel.ids import FramePrefix, ItemRef
from aerokernel.operations import SampleFrame
from aerokernel.relations import RelationDependency
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.values import FrozenValue, canonical_json, thaw

from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario.paths import source_path

from .predicate_ast import TEMPORAL as AST_TEMPORAL
from .predicate_ast import evaluate, validate_ast

VERSION = "aerograph-predicate/1"
FORMAT = "aerograph.predicate-definition/v1"
NATIVE_SHA256 = {
    "original_runtime.js": "6e1f60e885208b333fb20d69d24b7c49e9a14aec7c8578917b0b1d30dc2a10e6",
    "expanded_runtime.js": "3780dc2f3d6fc7d7e01850595d10f60d98f378f9036e8722e4c7d820bd75f869",
}
TEMPORAL = {
    "hold",
    "all_window",
    "any_window",
    "count_window",
    "delta",
    "rate",
    "stable_window",
}


def digest(value: object) -> str:
    """Use the kernel's portable canonical JSON, including its terminal newline."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def prepare(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve a pinned definition closure and reject unsupported nodes at load."""
    required = {
        "version",
        "definitions",
        "definitions_sha256",
        "target",
        "context",
        "parameters",
        "temporal_unit",
        "event",
        "topic",
        "transition",
        "native_references",
    }
    if set(config) - required - {
        "field_roles",
        "relation_roles",
        "relation_profiles",
        "event_payload",
    } or required - set(config):
        raise ValueError(
            "predicate.config: declare version, definition closure, sample and event bindings"
        )
    identity = config["target"]
    if config["version"] != VERSION:
        raise ValueError(
            f"predicate {identity}: unsupported evaluator version {config['version']}"
        )
    definitions = config["definitions"]
    if (
        not isinstance(definitions, dict)
        or digest(definitions) != config["definitions_sha256"]
    ):
        raise ValueError(f"predicate {identity}: definition digest mismatch")
    if config["temporal_unit"] not in {"s", "ns"}:
        raise ValueError(
            f"predicate {identity}: explicit temporal_unit s or ns required"
        )
    if config["transition"] not in {"entered", "exited", "level"}:
        raise ValueError(
            f"predicate {identity}: unsupported transition {config['transition']}"
        )
    references = config["native_references"]
    if not isinstance(references, list) or not references:
        raise ValueError(
            f"predicate {identity}: pinned native evaluator sources required"
        )
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            raise ValueError(f"predicate {identity}: malformed native source reference")
        path = source_path(reference["path"])
        if NATIVE_SHA256.get(path.name) != reference["sha256"]:
            raise ValueError(
                f"predicate {identity}: unsupported native evaluator revision: {path.name}"
            )
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != reference["sha256"]
        ):
            raise ValueError(
                f"predicate {identity}: native source hash mismatch: {path}"
            )
    active: set[str] = set()
    resolved_nodes = 0

    def resolve(node: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        nonlocal resolved_nodes
        resolved_nodes += 1
        if depth > 128 or resolved_nodes > 100_000:
            raise ValueError(f"predicate {identity}: construct AST_budget exceeded")
        if "rule" in node:
            if node.get("referenceMode", "canonical") == "inline":
                return resolve(node["expression"], depth + 1)
            key = "rule:" + node["rule"]
            if key in active:
                raise ValueError(f"predicate {identity}: cyclic rule {key}")
            if key not in definitions:
                raise ValueError(f"predicate {identity}: unresolved rule {key}")
            active.add(key)
            result = resolve(definitions[key]["expression"], depth + 1)
            active.remove(key)
            return result
        result = dict(node)
        if "field" in result:
            field_roles = config.get("field_roles", {})
            if result["field"] in field_roles:
                result["role"] = field_roles[result["field"]]
            if not isinstance(result.get("role"), str):
                raise ValueError(
                    f"predicate {identity}: construct unbound_role: {result['field']}"
                )
        if "relation" in result:
            binding = config.get("relation_roles", {}).get(result["relation"], {})
            for side in ("sourceRole", "targetRole"):
                if side in binding:
                    result[side] = binding[side]
                if not isinstance(result.get(side), str):
                    raise TypeError(
                        f"predicate {identity}: construct unbound_relation_role: {result['relation']}.{side}"
                    )
        for key, value in result.items():
            if key == "literal":
                result[key] = copy.deepcopy(value)
                continue
            if isinstance(value, dict):
                result[key] = resolve(value, depth + 1)
            elif isinstance(value, list):
                result[key] = [
                    resolve(child, depth + 1) if isinstance(child, dict) else child
                    for child in value
                ]
        return result

    if identity not in definitions:
        raise ValueError(f"predicate {identity}: missing target definition")
    target = definitions[identity]
    if target.get("schemaVersion") != FORMAT or target.get("kind") not in {
        "predicate",
        "event",
    }:
        raise ValueError(
            f"predicate {identity}: canonical definition schema/kind required"
        )
    required_runtime = (
        "original_runtime.js"
        if target["execution"]["nativeDialect"] == "original_compact_ast"
        else "expanded_runtime.js"
    )
    if required_runtime not in {source_path(r["path"]).name for r in references}:
        raise ValueError(
            f"predicate {identity}: pin {required_runtime} for the selected native dialect"
        )
    target_expression = target["expression"]
    if target.get("applicability") is not None:
        raise ValueError(
            f"predicate {identity}: construct applicability unsupported in this revision"
        )
    if target_expression.get("op") == "transition_event":
        expected = {
            "op",
            "args",
            "contractId",
            "transition",
            "requiresSameRoleInstances",
            "requiresSameClock",
            "requiresPreviousAndCurrentFrames",
        }
        rules = target["inputs"]["rules"]
        if (
            set(target_expression) != expected
            or target_expression["args"] != []
            or target_expression["transition"] != "entered"
            or any(
                target_expression[key] is not True
                for key in (
                    "requiresSameRoleInstances",
                    "requiresSameClock",
                    "requiresPreviousAndCurrentFrames",
                )
            )
            or len(rules) != 1
            or rules[0] != "expanded:rule:" + target_expression["contractId"]
        ):
            raise ValueError(
                f"predicate {identity}: construct transition_event unsupported profile"
            )
        if config["transition"] != "level":
            raise ValueError(
                f"predicate {identity}: event AST already carries its transition; select level emission"
            )
        if definitions.get("rule:" + rules[0], {}).get("applicability") is not None:
            raise ValueError(
                f"predicate {identity}: construct applicability unsupported in this revision"
            )
        expression = {"op": "entered", "args": [resolve({"rule": rules[0]})]}
    else:
        expression = resolve(target_expression)
    validate_ast(expression, identity)
    dialect = target["execution"]["nativeDialect"]
    original_only = (
        {
            "if",
            "is_unknown",
            "in",
            "set_equal",
            "unique_count",
            "len",
            "sum",
            "norm",
            "distance",
            "clamp",
            "between",
        }
        | TEMPORAL
        | {"rise", "fall", "changed"}
    )
    expanded_only = {
        "all",
        "any",
        "implies",
        "contains",
        "count",
        "vector_norm",
        "cross",
        "same_identity",
        "date_time",
        "interval_overlap",
        "interval_contains",
    }
    unavailable = (
        expanded_only
        if dialect == "original_compact_ast"
        else original_only
        if dialect == "expanded_typed_ast"
        else set()
    )
    for node in nodes(expression):
        if dialect == "original_compact_ast" and (
            "relation" in node
            or "var" in node
            or "time" in node
            or "field" in node
            and node.get("path", [])
        ):
            construct = (
                "relation"
                if "relation" in node
                else "var"
                if "var" in node
                else "time"
                if "time" in node
                else "field_path"
            )
            raise ValueError(
                f"predicate {identity}: construct {construct} unsupported in native dialect {dialect}"
            )
        if "parameter" in node and node["parameter"] not in config["parameters"]:
            raise ValueError(
                f"predicate {identity}: undeclared parameter {node['parameter']}"
            )
        if node.get("op") in unavailable:
            raise ValueError(
                f"predicate {identity}: construct {node['op']} unsupported in native dialect {dialect}"
            )
    for node in nodes(expression):
        if node.get("op") in TEMPORAL | {
            "rise",
            "fall",
            "changed",
            "entered",
            "exited",
        }:
            # Native durations are never inferred from an arbitrary scalar expression.
            for arg in node["args"][1:]:
                if "literal" in arg:
                    value = arg["literal"]
                elif "parameter" in arg:
                    value = config["parameters"][arg["parameter"]]
                else:
                    raise ValueError(
                        f"predicate {identity}: construct computed_duration unsupported"
                    )
                if value is None:
                    arg.clear()
                    arg["literal"] = None
                    continue  # Native null gap explicitly means no maximum gap.
                if type(value) not in (int, float):
                    raise ValueError(f"predicate {identity}: duration must be numeric")
                ns = Decimal(str(value)) * (
                    1_000_000_000 if config["temporal_unit"] == "s" else 1
                )
                if ns < 0 or ns != ns.to_integral_value():
                    raise ValueError(
                        f"predicate {identity}: duration must map exactly to nonnegative ns"
                    )
                arg.clear()
                arg["literal"] = int(ns)
            if node.get("op") == "rate" and config["temporal_unit"] == "s":
                native_rate = copy.deepcopy(node)
                node.clear()
                node.update(
                    {"op": "mul", "args": [native_rate, {"literal": 1_000_000_000}]}
                )
        elif node.get("time") is True and config["temporal_unit"] == "s":
            node.clear()
            node.update(
                {"op": "div", "args": [{"time": True}, {"literal": 1_000_000_000}]}
            )
    return expression


def nodes(ast: dict[str, Any]) -> list[dict[str, Any]]:
    result = [ast]
    for key, value in ast.items():
        if key == "literal":
            continue
        if isinstance(value, dict):
            result.extend(nodes(value))
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    result.extend(nodes(child))
    return result


class SampleHistory:
    """Retain the exact anchor/predecessor closure a fixed AST can still read.

    Parameters are pinned for a sampled context. Nested windows recurse from
    the actual outer anchor, not from now minus the sum of their durations: an
    irregular sampling gap can put that anchor much further into the past.
    The unmodified native-compatible evaluator runs over this bounded prefix.
    """

    def __init__(self, ast: dict[str, Any], dialect: str) -> None:
        self.ast, self.dialect = ast, dialect
        self.frames: list[dict[str, Any]] = []
        self.times: list[int] = []

    def _first(self, node: dict[str, Any], lo: int, hi: int) -> int:
        first = hi
        op = node.get("op")
        if op is None:
            return hi
        if op in AST_TEMPORAL:
            if op in {"rise", "fall", "changed", "entered", "exited"}:
                lo = max(0, lo - 1)
            else:
                duration = node["args"][1]
                value = (
                    duration["literal"]
                    if "literal" in duration
                    else self.frames[-1]["parameters"].get(duration["parameter"])
                )
                if value is None:
                    return hi  # Native null window cannot reach historical input.
                if type(value) is not int or value < 0:
                    raise ValueError(
                        "temporal: exact nonnegative integer ns duration required"
                    )
                lo = max(0, bisect_right(self.times, self.times[lo] - value) - 1)
            first = lo
            children = [node["args"][0]]
            if "asScope" in node:
                children.append(node["asScope"])
        else:
            children = [
                child
                for key, value in node.items()
                if key != "literal"
                for child in (value if isinstance(value, list) else [value])
                if isinstance(child, dict)
            ]
        for child in children:
            first = min(first, self._first(child, lo, hi))
        return first

    def append(self, frame: dict[str, Any]) -> None:
        """Adopt one sampled input, dropping only permanently unreachable data."""
        time = frame["t"]
        if type(time) is not int or self.times and time <= self.times[-1]:
            raise ValueError("history: strictly increasing exact integer ns required")
        self.frames.append(frame)
        self.times.append(time)
        first = self._first(self.ast, len(self.frames) - 1, len(self.frames) - 1)
        if first:
            del self.frames[:first]
            del self.times[:first]

    def value(self) -> Any:
        return evaluate(self.ast, self.frames, self.dialect)


def sdk_frame_causes(ctx: EngineContext, prefix: FramePrefix, position: int) -> None:
    """Preserve SDK first-occurrence order across a prefix/explicit boundary.

    SampleFrame has already captured its raw (duplicate-preserving) causes.
    Emit/timers use SDK deduplication. K5's local frame index lets us check the
    few explicit inputs without expanding the whole prefix. Only an actual
    overlap requires a shortened prefix and explicit surviving tail.
    """
    rows = ctx.view._store.frames[prefix.context_id]
    indices = ctx.view._store.frame_indices[prefix.context_id]

    def member(cause: object) -> int | None:
        if not isinstance(cause, ItemRef):
            return None
        index = bisect_left(indices, cause.record_index, 0, prefix.count)
        return index if index < prefix.count and rows[index].version == cause else None

    earlier = {
        index for cause in ctx.inputs[:position] if (index := member(cause)) is not None
    }
    tail = [cause for cause in ctx.inputs[position + 1 :] if member(cause) is None]
    if earlier:
        boundary = min(earlier)
        segments: list[ItemRef | FramePrefix] = []
        if boundary:
            segments.append(
                FramePrefix(prefix.context_id, boundary, rows[boundary - 1].version)
            )
        segments.extend(
            rows[index].version
            for index in range(boundary, prefix.count)
            if index not in earlier
        )
        ctx.inputs[position:] = [*segments, *tail]
    else:
        ctx.inputs[position + 1 :] = tail


class Predicate(ContextEngine):
    """One pinned target/context; history and outputs remain journalled kernel data."""

    version = VERSION

    def __init__(self, build: EngineBuild) -> None:
        config = build.config
        self.ast = prepare(config)
        self.target = config["target"]
        definition = config["definitions"][self.target]
        dialect = definition["execution"]["nativeDialect"]
        self.dialect = "original" if dialect == "original_compact_ast" else "expanded"
        if dialect not in {
            "original_compact_ast",
            "expanded_typed_ast",
            "semantic_typed_ast",
            "capability_typed_ast",
        }:
            raise ValueError(
                f"predicate {self.target}: unsupported nativeDialect {dialect}"
            )
        self.transition = config["transition"]
        matches = [
            s
            for s in build.manifest.samples
            if s.context_id == config["context"] and s.partition == build.id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"predicate {self.target}: context requires one matching sample binding"
            )
        self.spec = matches[0]
        self.parameters = copy.deepcopy(config["parameters"])
        if thaw(cast(FrozenValue, self.spec.parameters)) != self.parameters:
            raise ValueError(
                f"predicate {self.target}: parameters must match pinned sample profile"
            )
        self.fields: set[tuple[str, str]] = set()
        self.relation_nodes: list[dict[str, Any]] = []
        self.relation_profiles: dict[str, dict[str, Any]] = copy.deepcopy(
            config.get("relation_profiles", {})
        )
        self.durations: set[int] = set()
        for node in nodes(self.ast):
            if "field" in node:
                role, field = node["role"], node["field"]
                if role not in self.spec.bindings:
                    raise ValueError(f"predicate {self.target}: unbound role {role}")
                descriptor = build.registry.field(field)
                if not build.registry.is_a(
                    self.spec.bindings[role].type_id, descriptor.declaring_type
                ):
                    raise ValueError(
                        f"predicate {self.target}: field {field} incompatible with role {role}"
                    )
                self.fields.add((role, field))
            if "parameter" in node and node["parameter"] not in self.parameters:
                raise ValueError(
                    f"predicate {self.target}: undeclared parameter {node['parameter']}"
                )
            if "relation" in node:
                descriptor_r = build.registry.relation(node["relation"])
                profile = self.relation_profiles.get(node["relation"])
                if (
                    not isinstance(profile, dict)
                    or set(profile) != {"source", "clock"}
                    or profile["source"] not in self.spec.upstream
                    or not isinstance(profile["clock"], list)
                    or len(profile["clock"]) != 2
                    or any(type(value) is not str for value in profile["clock"])
                ):
                    raise ValueError(
                        f"predicate {self.target}: relation {node['relation']} requires explicit source/clock profile"
                    )
                for side, type_id in (
                    ("source", descriptor_r.source_type),
                    ("target", descriptor_r.target_type),
                ):
                    role = node[side + "Role"]
                    if role not in self.spec.bindings or not build.registry.is_a(
                        self.spec.bindings[role].type_id, type_id
                    ):
                        raise ValueError(
                            f"predicate {self.target}: relation {node['relation']} incompatible {side} role {role}"
                        )
                self.relation_nodes.append(node)
            if node.get("op") in TEMPORAL and node["args"][1]["literal"] is not None:
                self.durations.add(node["args"][1]["literal"])
        self.schema, self.topic = config["event"], config["topic"]
        self.roles = {role for role, _ in self.fields} | {
            node[side]
            for node in self.relation_nodes
            for side in ("sourceRole", "targetRole")
        }
        self.payload = config.get(
            "event_payload",
            {"predicate": "$predicate", "from_ns": "$from_ns", "to_ns": "$to_ns"},
        )
        self.history = SampleHistory(self.ast, self.dialect)
        self._expected_count: int | None = None
        self._last_result: dict[str, Any] | None = None
        message = build.registry.message(self.schema)
        if message.kind != "event":
            raise ValueError(f"predicate {self.target}: typed event message required")
        self.registry = build.registry
        tokens = {"$predicate": "string", "$from_ns": "integer", "$to_ns": "integer"}
        members = message.schema.get("members", {})
        if set(self.payload) - set(members) or set(
            message.schema.get("required", ())
        ) - set(self.payload):
            raise ValueError(
                f"predicate {self.target}: event payload schema coverage mismatch"
            )
        for key, value in self.payload.items():
            if isinstance(value, str) and value.startswith("$"):
                if value not in tokens or members[key].get("type") != tokens[value]:
                    raise ValueError(
                        f"predicate {self.target}: incompatible event payload token {value}"
                    )
            else:
                build.registry.validate(members[key], value)
        super().__init__(
            Partition(
                build.id,
                build.id,
                consumes=tuple(
                    Dependency(field) for field in sorted({f for _, f in self.fields})
                ),
                relation_consumes=tuple(
                    RelationDependency(r)
                    for r in sorted({n["relation"] for n in self.relation_nodes})
                ),
                emits=(self.schema,),
                message_targets=(self.topic,),
                lifecycle_reads=tuple(
                    sorted({self.spec.bindings[role].type_id for role in self.roles})
                ),
                features=("sampled", "relations")
                if self.relation_nodes
                else ("sampled",),
            )
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        self.history = SampleHistory(self.ast, self.dialect)
        self._expected_count = None
        self._last_result = None
        ctx.ops.append(Activate(self.partition.id))

    def on_inputs(self, ctx: EngineContext) -> None:
        prefix = ctx.view.sample_frame_prefix(self.spec.context_id)
        # K5 exposes codec admission on the local Store, but not on StateView.
        # Inspect it once per invocation; never probe by publishing a bad cause.
        compact_causes = ctx.view._store.allow_frame_prefix
        prior = (
            ctx.view.sample_frames(self.spec.context_id)
            if not compact_causes or prefix.count != self._expected_count
            else ()
        )
        if prefix.count != self._expected_count:
            # Cold start or a noncommitted invocation: recover from actual frames,
            # never from a tentative callback result or a synthetic baseline.
            self.history = SampleHistory(self.ast, self.dialect)
            self._last_result = None
            for recorded in prior:
                self._last_result = cast(
                    dict[str, Any], thaw(cast(FrozenValue, recorded.frame.result))
                )
                self.history.append(self._last_result["input"])
        before = self._last_result
        cause_position = len(ctx.inputs)
        if compact_causes:
            if prefix.count:
                ctx.inputs.append(prefix)
        else:
            ctx.inputs.extend(recorded.version for recorded in prior)
        current: dict[str, Any] = {
            "t": ctx.now.ns,
            "fields": {},
            "relations": {},
            "parameters": self.parameters,
        }
        diagnostics: list[dict[str, Any]] = []
        for role in sorted(self.roles):
            life = ctx.view.lifecycle(self.spec.bindings[role], ctx.view.cut)
            if life.created.instant > ctx.now or (
                life.removed is not None and life.removed.instant <= ctx.now
            ):
                diagnostics.append(
                    {
                        "status": "required_input",
                        "role": role,
                        "reason": "selected entity generation is not alive",
                    }
                )
        for role, field in sorted(self.fields):
            ref = self.spec.bindings[role]
            ctx.get(ref, field)
            fact = ctx.view.field((ref, field), ctx.now, ctx.view.cut)
            if not isinstance(fact, Fact):
                diagnostics.append(
                    {
                        "status": "required_input",
                        "role": role,
                        "field": field,
                        "reason": "no valid committed fact",
                    }
                )
                continue
            if (
                fact.producer != self.spec.sources[role]
                or (fact.acquired.clock_id, fact.acquired.mapping_id)
                != self.spec.clocks[role]
            ):
                diagnostics.append(
                    {
                        "status": "invalid_input",
                        "role": role,
                        "field": field,
                        "reason": "source or clock mismatch",
                    }
                )
                continue
            current["fields"].setdefault(role, {})[field] = thaw(fact.value)
        for node in self.relation_nodes:
            relation = node["relation"]
            role = node["sourceRole"]
            profile = self.relation_profiles[relation]
            edges = ctx.relations(relation)
            selected = [
                edge
                for edge in edges
                if edge.source == self.spec.bindings[role]
                and edge.target == self.spec.bindings[node["targetRole"]]
            ]
            if any(
                edge.producer != profile["source"]
                or (edge.acquired.clock_id, edge.acquired.mapping_id)
                != tuple(profile["clock"])
                for edge in selected
            ):
                diagnostics.append(
                    {
                        "status": "invalid_input",
                        "relation": relation,
                        "reason": "source or clock mismatch",
                    }
                )
            else:
                # Fully committed relation queries are complete, including empty sets.
                current["relations"][
                    relation + "|" + role + "|" + node["targetRole"]
                ] = bool(selected)
        try:
            self.history.append(current)
            value = self.history.value()
        except (ValueError, TypeError, ArithmeticError) as exc:
            diagnostics.append({"status": "invalid_input", "reason": str(exc)})
            value = None
        if value is not None and type(value) is not bool:
            raise TypeError(f"predicate {self.target}: target must produce Boolean")
        if diagnostics:
            value = None
        status = (
            "invalid_input"
            if any(d["status"] == "invalid_input" for d in diagnostics)
            else "required_input"
            if value is None
            else "known"
        )
        if value is None and not diagnostics:
            diagnostics.append(
                {"status": status, "reason": "history or operator input undetermined"}
            )
        fire = (
            value is True
            if self.transition == "level"
            else bool(
                before is not None
                and before["status"] == "known"
                and status == "known"
                and (
                    before["value"] is False and value is True
                    if self.transition == "entered"
                    else before["value"] is True and value is False
                )
            )
        )
        result = {
            "target": self.target,
            "status": status,
            "value": value,
            "diagnostics": diagnostics,
            "input": current,
        }
        ctx.ops.append(
            SampleFrame(
                self.spec.context_id,
                ctx.now.ns,
                ctx.view.cut,
                self.spec.bindings,
                self.spec.clocks,
                result,
                self.spec.sources,
                tuple(ctx.inputs),
            )
        )
        if compact_causes and prefix.count:
            sdk_frame_causes(ctx, prefix, cause_position)
        if fire:
            tokens = {
                "$predicate": self.target,
                "$from_ns": before["input"]["t"] if before is not None else ctx.now.ns,
                "$to_ns": ctx.now.ns,
            }
            ctx.emit(
                self.schema,
                {
                    key: tokens[value]
                    if isinstance(value, str) and value in tokens
                    else value
                    for key, value in self.payload.items()
                },
                topic=self.topic,
            )
        # Schedule exact window boundaries only after real input changes. Timer-only
        # frames do not create an endless polling chain.
        changed = before is None or any(
            current[key] != before["input"][key] for key in ("fields", "relations")
        )
        if changed:
            for duration in sorted(self.durations):
                if duration > 0:
                    ctx.wake_at(
                        ctx.now.ns + duration,
                        {"target": self.target, "duration_ns": duration},
                    )
        self._last_result = result
        self._expected_count = prefix.count + 1


def build(context: EngineBuild) -> Predicate:
    return Predicate(context)
