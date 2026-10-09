"""Committed reactive Q6 evaluation with evidence and discontinuous baselines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from aerokernel import EntityRef
from aerokernel.ids import ItemRef, LocalCause
from aerokernel.sdk import EngineContext
from aerokernel.state import Fact
from aerokernel.values import thaw

from aeroagentsim.engines.predicate import nodes
from aeroagentsim.engines.predicate_ast import evaluate

from .records import PREFIX, instant


@dataclass
class Truth:
    value: bool | None
    status: str
    signature: object
    record: dict[str, Any]
    cause: ItemRef | LocalCause | None = None
    previous: bool | None = None

    def matches(self, edge: str) -> bool:
        if self.status != "known":
            return False
        return (
            self.value is True
            if edge == "while"
            else self.previous is False and self.value is True
            if edge == "entered"
            else self.previous is True and self.value is False
        )


class Evaluator:
    def __init__(self, dialect: str = "original") -> None:
        self.dialect = dialect
        self.truths: dict[str, Truth] = {}
        self.field_index: dict[tuple[EntityRef, str], set[str]] = {}
        self.entity_index: dict[EntityRef, set[str]] = {}
        self.relation_index: dict[str, set[str]] = {}
        self.registered: set[str] = set()
        self.contexts: dict[
            str, tuple[str, dict[str, Any], dict[str, EntityRef], str]
        ] = {}

    def affected(self, ctx: EngineContext) -> set[str]:
        """Dispatch committed changes through reverse indexes, never poll contexts."""
        result: set[str] = set()
        for dirty in ctx.dirty:
            if dirty.key is not None:
                result.update(self.field_index.get(dirty.key, ()))
            elif dirty.kind == "lifecycle":
                payload = cast(dict[str, Any], thaw(dirty.payload))
                result.update(
                    self.entity_index.get(EntityRef.from_data(payload["$ref"]), ())
                )
            elif dirty.kind == "relation":
                payload = cast(dict[str, Any], thaw(dirty.payload))
                result.update(self.relation_index.get(payload["relation_id"], ()))
        return result

    def run(
        self,
        ctx: EngineContext,
        identity: str,
        predicate_id: str,
        definition: dict[str, Any],
        roles: dict[str, EntityRef],
        *,
        force: bool = False,
        dialect: str | None = None,
    ) -> Truth:
        if identity not in self.registered:
            self.contexts[identity] = (
                predicate_id,
                definition,
                roles,
                dialect if dialect is not None else self.dialect,
            )
            for ref in roles.values():
                self.entity_index.setdefault(ref, set()).add(identity)
            for node in nodes(definition["expression"]):
                if "field" in node:
                    self.field_index.setdefault(
                        (roles[node["role"]], node["field"]), set()
                    ).add(identity)
                elif "relation" in node:
                    self.relation_index.setdefault(node["relation"], set()).add(
                        identity
                    )
            self.registered.add(identity)
        fields: dict[str, dict[str, Any]] = {}
        relations: dict[str, Any] = {}
        versions: list[object] = []
        acquired: dict[str, Any] = {}
        diagnostics: list[dict[str, Any]] = []
        for node in nodes(definition["expression"]):
            if "field" in node:
                role, field = node["role"], node["field"]
                ref = roles[role]
                ctx.get(ref, field)
                fact = ctx.view.field((ref, field), ctx.now)
                if isinstance(fact, Fact):
                    fields.setdefault(role, {})[field] = thaw(fact.value)
                    versions.append(fact.version)
                    acquired[role + "." + field] = {
                        "clockId": fact.acquired.clock_id,
                        "mappingId": fact.acquired.mapping_id,
                        "numerator": str(fact.acquired.numerator),
                        "denominator": str(fact.acquired.denominator),
                        "producer": fact.producer,
                    }
                else:
                    diagnostics.append(
                        {
                            "role": role,
                            "entity": ref.to_data(),
                            "field": field,
                            "reason": "no valid committed fact",
                            "producer": ctx.view._store.writers.get((ref, field)),
                        }
                    )
            elif "relation" in node:
                edges = ctx.relations(node["relation"])
                selected = [
                    e
                    for e in edges
                    if e.source == roles[node["sourceRole"]]
                    and e.target == roles[node["targetRole"]]
                ]
                relations[
                    node["relation"]
                    + "|"
                    + node["sourceRole"]
                    + "|"
                    + node["targetRole"]
                ] = bool(selected)
                versions.extend(e.version for e in selected)
        signature = (
            tuple(versions),
            tuple((name, ref) for name, ref in sorted(roles.items())),
            tuple(str(d) for d in diagnostics),
        )
        old = self.truths.get(identity)
        if old is not None and old.signature == signature and not force:
            return old
        value: Any = None
        status = "required_input" if diagnostics else "known"
        if not diagnostics:
            try:
                value = evaluate(
                    definition["expression"],
                    [
                        {
                            "t": ctx.now.ns,
                            "fields": fields,
                            "relations": relations,
                            "parameters": definition.get("parameters", {}),
                        }
                    ],
                    dialect if dialect is not None else self.dialect,
                )
                if value is not None and type(value) is not bool:
                    raise TypeError("predicate result must be Boolean")
                if value is None:
                    status = "required_input"
                    diagnostics.append(
                        {
                            "reason": "Q6 requires known operands",
                            "predicate": predicate_id,
                        }
                    )
            except (TypeError, ValueError, ArithmeticError) as exc:
                status = "invalid_input"
                diagnostics.append({"reason": str(exc), "predicate": predicate_id})
                value = None
        if old is not None:
            close = {
                **old.record,
                "op": "close",
                "validTo": instant(ctx.now.ns, ctx.now.microstep),
            }
            ctx.emit(PREFIX + "predicate_evaluated", close, topic=PREFIX + "records")
        record = {
            "contextId": identity,
            "predicateId": predicate_id,
            "roles": {r: {"$ref": ref.to_data()} for r, ref in roles.items()},
            "profile": definition["profile"],
            "status": status,
            "value": value,
            "diagnostics": diagnostics,
            "evaluatedAt": instant(ctx.now.ns, ctx.now.microstep),
            "readCut": {
                "index": ctx.view.cut.index,
                "at": instant(ctx.view.cut.instant.ns, ctx.view.cut.instant.microstep),
            },
            "acquired": acquired,
            "validFrom": instant(ctx.now.ns, ctx.now.microstep),
            "validTo": None,
            "op": "assert",
        }
        cause = ctx.emit(
            PREFIX + "predicate_evaluated", record, topic=PREFIX + "records"
        )
        truth = Truth(
            cast(bool | None, value),
            status,
            signature,
            record,
            cause,
            None
            if old is None
            or old.status != "known"
            or old.record["acquired"].keys() != acquired.keys()
            or any(
                (
                    old.record["acquired"][key]["clockId"],
                    old.record["acquired"][key]["mappingId"],
                    old.record["acquired"][key].get("producer"),
                )
                != (stamp["clockId"], stamp["mappingId"], stamp.get("producer"))
                for key, stamp in acquired.items()
            )
            else old.value,
        )
        self.truths[identity] = truth
        return truth
