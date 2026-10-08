"""Project committed WAL operations into the pinned viewer-feed/v1 shapes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.ids import EntityRef
from aerokernel.operations import (
    AssertEdge,
    CancelEdge,
    CloseEdge,
    Create,
    FactWrite,
    Remove,
    RetractFact,
)
from aerokernel.registry import MemoryRegistry
from aerokernel.time import Instant

from .storage import RunStorage


def instant(at: Instant) -> dict[str, Any]:
    return {"ns": str(at.ns), "microstep": at.microstep}


def entity(ref: EntityRef) -> dict[str, Any]:
    return {"id": ref.id, "generation": ref.generation}


def project(record: dict[str, Any]) -> dict[str, Any]:
    """Keep journal indices, including empty intent/control/seal records."""
    record = expand_record(record)
    result: dict[str, Any] = {
        "commitIndex": record["index"],
        "at": instant(decode_record(record["instant"])),
        "created": [],
        "removed": [],
        "facts": [],
        "retracted": [],
        "edges": [],
        "messages": [],
        "receipts": [],
    }
    created: set[tuple[str, int]] = set()
    routes = {
        item["message"]: item
        for item in record["items"]
        if item.get("kind") == "enqueue"
    }
    for item in record["items"]:
        if "proposal" in item:
            op = decode_record(item["proposal"])
            if isinstance(op, Create):
                key = (op.ref.id, op.ref.generation)
                if key not in created:
                    result["created"].append(
                        {**entity(op.ref), "typeId": op.ref.type_id}
                    )
                    created.add(key)
            elif isinstance(op, (AssertEdge, CloseEdge, CancelEdge)):
                edge = decode_record(item["edge"])
                result["edges"].append(
                    {
                        "edgeId": edge.edge_id,
                        "relationId": edge.relation_id,
                        "source": entity(edge.source),
                        "target": entity(edge.target),
                        "op": "assert" if isinstance(op, AssertEdge) else "close",
                    }
                )
            elif isinstance(op, Remove):
                result["removed"].append(entity(op.ref))
            elif isinstance(op, FactWrite):
                result["facts"].append(
                    {
                        "entity": entity(op.key[0]),
                        "fieldId": op.key[1],
                        "value": op.value,
                        "producer": item["partition"],
                        "validFrom": instant(op.valid.start),
                    }
                )
            elif isinstance(op, RetractFact):
                result["retracted"].append(
                    {"entity": entity(op.key[0]), "fieldId": op.key[1]}
                )
        if "message" in item and isinstance(item["message"], dict):
            message = decode_record(item["message"])
            if message.kind in {"command", "event"}:
                route = routes.get(message.id)
                projected = {
                    "id": message.id,
                    "kind": message.kind,
                    "schemaId": message.schema_id,
                    "source": message.source,
                    "at": instant(message.at),
                    "payload": json.loads(
                        json.dumps(item["message"]["fields"]["payload"])
                    ),
                }
                if "proposal" in item:
                    proposal = decode_record(item["proposal"])
                    destination = getattr(proposal, "target_or_topic", None)
                    if destination is not None:
                        projected[
                            "target" if message.kind == "command" else "topic"
                        ] = destination
                elif route is not None:
                    projected["target" if message.kind == "command" else "topic"] = (
                        route["recipient"]
                    )
                result["messages"].append(projected)
        if item.get("kind") == "receipt":
            receipt = {"commandId": item["command_id"], "status": item["status"]}
            if "result" in item:
                receipt["result"] = item["result"]
            result["receipts"].append(receipt)
        elif "receipt" in item:
            receipt = item["receipt"]
            result["receipts"].append(
                {
                    "commandId": receipt["command_id"],
                    "status": receipt["status"],
                    "result": receipt.get("result"),
                }
            )
    return result


def _unit(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if hasattr(value, "get"):
        symbol = value.get("symbol")
        return symbol if isinstance(symbol, str) else None
    return None


def _frame(field: str, scenario: dict[str, Any]) -> str | None:
    for binding in scenario["presentation"]:
        if field in {binding["positionField"], binding.get("orientationField")}:
            return str(binding["frame"])
    for engine in scenario["engines"].values():
        config = engine["config"]
        if field in {config.get("velocity_field"), config.get("sample_field")}:
            return (
                "enu"  # These local plugins explicitly declare ENU in their contracts.
            )
    return None


def header(directory: Path) -> dict[str, Any]:
    storage = RunStorage(directory)
    metadata = storage.metadata()
    registry = MemoryRegistry.from_data(
        json.loads((directory / "runtime.registry.json").read_text())
    )
    scenario = json.loads((directory / "scenario.json").read_text())
    types = {descriptor.id: descriptor for descriptor in registry.types}

    def ancestors(type_id: str) -> list[str]:
        result: list[str] = []
        pending = list(types[type_id].parents)
        while pending:
            parent = pending.pop(0)
            if parent not in result:
                result.append(parent)
                pending.extend(types[parent].parents)
        return result

    result: dict[str, Any] = {
        "contract": "aeroagentsim.viewer-feed/v1",
        "runId": metadata["id"],
        "registryDigest": metadata["registry_digest"],
        "types": [
            {"typeId": t.id, "displayName": t.id, "ancestors": ancestors(t.id)}
            for t in registry.types
        ],
        "fields": [
            {
                "fieldId": f.id,
                "displayName": f.id,
                "valueType": f.schema["type"],
                "unit": _unit(f.metadata.get("unit")),
                "frame": _frame(f.id, scenario),
                **({"role": f.metadata["role"]} if "role" in f.metadata else {}),
            }
            for f in registry.fields
        ],
        "presentation": scenario["presentation"],
        "start": {"ns": "0", "microstep": 0},
    }
    if "origin" in scenario:
        result["origin"] = scenario["origin"]
    entries = storage.entries
    if entries:
        record = storage.records(entries[-1]["index"], 1)[0]
        if metadata["status"] in {"completed", "stopped", "faulted"}:
            result["end"] = instant(decode_record(record["instant"]))
    return result
