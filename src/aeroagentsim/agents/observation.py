"""Grant-limited committed observations with explicit validity and knowledge cuts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aerokernel import EntityRef
from aerokernel.sdk import EngineContext
from aerokernel.state import Fact
from aerokernel.values import thaw


@dataclass(frozen=True)
class FieldGrant:
    entity: EntityRef
    field: str


def build_observation(
    ctx: EngineContext,
    fields: tuple[FieldGrant, ...],
    relations: tuple[str, ...],
    event_schemas: tuple[str, ...],
    actions: tuple[str, ...],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "valid_at": {"ns": str(ctx.now.ns), "microstep": ctx.now.microstep},
        "known_at": {
            "index": ctx.view.cut.index,
            "ns": str(ctx.view.cut.instant.ns),
            "microstep": ctx.view.cut.instant.microstep,
        },
        "fields": [],
        "relations": [],
        "events": [],
        "actions": [],
    }
    for grant in fields:
        fact = ctx.view.field((grant.entity, grant.field), ctx.now, ctx.view.cut)
        row: dict[str, Any] = {"entity": grant.entity.to_data(), "field": grant.field}
        if isinstance(fact, Fact):
            ctx.inputs.append(fact.version)
            row.update(
                status="known",
                value=thaw(fact.value),
                producer=fact.producer,
                valid_from={
                    "ns": str(fact.valid.start.ns),
                    "microstep": fact.valid.start.microstep,
                },
                valid_to=None
                if fact.valid.end is None
                else {
                    "ns": str(fact.valid.end.ns),
                    "microstep": fact.valid.end.microstep,
                },
                available={
                    "ns": str(fact.available.ns),
                    "microstep": fact.available.microstep,
                },
                acquired={
                    "clock": fact.acquired.clock_id,
                    "numerator": str(fact.acquired.numerator),
                    "denominator": str(fact.acquired.denominator),
                    "mapping": fact.acquired.mapping_id,
                },
                version={
                    "index": fact.version.record_index,
                    "ordinal": fact.version.item_index,
                },
            )
        else:
            row["status"] = "absent"
        result["fields"].append(row)
    for relation in relations:
        edges = ctx.relations(relation)
        if any(edge.valid is None for edge in edges):
            raise RuntimeError("effective relation query returned canceled edge")
        result["relations"].append(
            {
                "schema": relation,
                "edges": [
                    {
                        "id": edge.edge_id,
                        "source": edge.source.to_data(),
                        "target": edge.target.to_data(),
                        "valid_from_ns": str(edge.valid.start.ns)
                        if edge.valid is not None
                        else None,
                        "valid_to_ns": None
                        if edge.valid is None or edge.valid.end is None
                        else str(edge.valid.end.ns),
                        "available_ns": str(edge.available.ns),
                    }
                    for edge in edges
                ],
            }
        )
    for delivery in ctx.inbox:
        message = delivery.message
        if message.kind == "event" and message.schema_id in event_schemas:
            result["events"].append(
                {
                    "id": message.id,
                    "schema": message.schema_id,
                    "payload": thaw(message.payload),
                    "occurrence_ns": str(message.at.ns),
                    "delivery_ns": str(delivery.instant.ns),
                }
            )
    for command_id in actions:
        action = ctx.view.action(command_id)
        result["actions"].append({"command_id": command_id, "status": action.status})
        if action.head is not None:
            ctx.inputs.append(action.head)
    return result
