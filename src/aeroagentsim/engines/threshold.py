"""Settled, pinned sampled comparison and relation-presence profiles."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from aerokernel import Activate, Dependency, Partition
from aerokernel.operations import SampleFrame
from aerokernel.relations import RelationDependency
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.values import FrozenValue, thaw, typed_equal

from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario.paths import source_path


class Threshold(ContextEngine):
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
        self.spec = next(
            s for s in build.manifest.samples if s.context_id == cfg["context"]
        )
        reference = cfg["native_reference"]
        if (
            set(reference) != {"path", "sha256"}
            or type(reference["path"]) is not str
            or type(reference["sha256"]) is not str
        ):
            raise ValueError("threshold.native_reference: path and sha256 required")
        path = source_path(reference["path"])
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != reference["sha256"]
        ):
            raise ValueError(
                "threshold.native_reference: missing source or hash mismatch"
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


def build(context: EngineBuild) -> Threshold:
    return Threshold(context)
