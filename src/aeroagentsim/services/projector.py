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
from aerokernel.values import thaw

from .storage import RunStorage
from .subjects import SubjectProjection


def instant(at: Instant) -> dict[str, Any]:
    return {"ns": str(at.ns), "microstep": at.microstep}


def entity(ref: EntityRef) -> dict[str, Any]:
    return {
        "id": ref.id,
        "generation": ref.generation if ref.generation < 2**53 else str(ref.generation),
    }


def lossless(value: Any) -> Any:
    """Tag large portable integers without changing kernel schema/value types."""
    if type(value) is int and abs(value) >= 2**53:
        return {"$integer": str(value)}
    if type(value) is float and abs(value) >= 2**53:
        return {"$number": repr(value)}
    if isinstance(value, dict):
        encoded = {key: lossless(child) for key, child in value.items()}
        if len(value) == 1 and set(value) & {"$integer", "$number", "$record"}:
            return {"$record": encoded}
        return encoded
    if isinstance(value, (tuple, list)):
        return [lossless(child) for child in value]
    return value


def stamp(value: Any) -> dict[str, str]:
    return {
        "clockId": value.clock_id,
        "mappingId": value.mapping_id,
        "numerator": str(value.numerator),
        "denominator": str(value.denominator),
    }


def project(
    record: dict[str, Any], *, subjects: SubjectProjection | None = None
) -> dict[str, Any]:
    """Keep journal indices, including empty intent/control/seal records."""
    record = expand_record(record)
    if subjects is not None:
        subjects.begin(record)
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
    for ordinal, item in enumerate(record["items"]):
        version = {"journalIndex": record["index"], "itemOrdinal": ordinal}
        available = result["at"]
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
                        "op": "assert"
                        if isinstance(op, AssertEdge)
                        else "close"
                        if isinstance(op, CloseEdge)
                        else "cancel",
                        "validFrom": None
                        if edge.valid is None
                        else instant(edge.valid.start),
                        "validTo": None
                        if edge.valid is None or edge.valid.end is None
                        else instant(edge.valid.end),
                        "acquired": stamp(edge.acquired),
                        "available": instant(edge.available),
                        "version": version,
                        "causes": item.get("causes", []),
                    }
                )
            elif isinstance(op, Remove):
                result["removed"].append(entity(op.ref))
            elif isinstance(op, FactWrite):
                result["facts"].append(
                    {
                        "entity": entity(op.key[0]),
                        "fieldId": op.key[1],
                        "value": lossless(op.value),
                        "producer": item["partition"],
                        "validFrom": instant(op.valid.start),
                        "validTo": None
                        if op.valid.end is None
                        else instant(op.valid.end),
                        "acquired": stamp(op.acquired),
                        "available": available,
                        "version": version,
                        "causes": item.get("causes", []),
                    }
                )
            elif isinstance(op, RetractFact):
                result["retracted"].append(
                    {
                        "entity": entity(op.key[0]),
                        "fieldId": op.key[1],
                        "validFrom": instant(op.valid.start),
                        "validTo": None
                        if op.valid.end is None
                        else instant(op.valid.end),
                        "available": available,
                        "version": version,
                        "reason": op.reason,
                        "causes": item.get("causes", []),
                    }
                )
        if "message" in item and isinstance(item["message"], dict):
            message = decode_record(item["message"])
            if message.kind in {"command", "event"}:
                route = routes.get(message.id)
                refs = _message_subjects(thaw(message.payload))
                if subjects is not None:
                    refs.extend(subjects.subjects(message))
                projected = {
                    "id": message.id,
                    "kind": message.kind,
                    "schemaId": message.schema_id,
                    "source": message.source,
                    "at": instant(message.at),
                    "payload": lossless(thaw(message.payload)),
                    "subjects": [
                        entity(ref) for ref in dict.fromkeys(refs)
                    ],
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
    for receipt in result["receipts"]:
        if "result" in receipt:
            receipt["result"] = lossless(receipt["result"])
    if subjects is not None:
        subjects.end(record)
    return result


def _message_subjects(payload: Any) -> list[EntityRef]:
    """Only explicit typed refs establish entity links; scalar names do not."""
    result: list[EntityRef] = []
    if isinstance(payload, dict):
        if set(payload) == {"$ref"}:
            result.append(EntityRef.from_data(payload["$ref"]))
        else:
            for child in payload.values():
                result.extend(_message_subjects(child))
    elif isinstance(payload, list):
        for child in payload:
            result.extend(_message_subjects(child))
    return result


def _unit(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if hasattr(value, "get"):
        symbol = value.get("symbol")
        return symbol if isinstance(symbol, str) else None
    return None


def _frame(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if hasattr(value, "get"):
        convention = value.get("convention")
        return convention if isinstance(convention, str) else None
    return None


def header(directory: Path) -> dict[str, Any]:
    storage = RunStorage(directory)
    metadata = storage.metadata()
    registry = MemoryRegistry.from_data(
        json.loads((directory / "runtime.registry.json").read_text())
    )
    scenario = json.loads((directory / "scenario.json").read_text())
    types = {descriptor.id: descriptor for descriptor in registry.types}
    # Browsing metadata comes from the run's pinned compilation, never today's
    # mutable AeroGraph checkout. It has no bearing on kernel inheritance.
    snapshot_path = directory / "registry.snapshot.json"
    raw_types = (
        json.loads(snapshot_path.read_text())["details"].get("raw_types", {})
        if snapshot_path.exists()
        else {}
    )

    def type_info(type_id: str) -> dict[str, Any]:
        source = raw_types.get(type_id, {})
        navigation = source.get("navigation")
        result = {
            "typeId": type_id,
            "displayName": source.get("name", type_id),
            "ancestors": ancestors(type_id),
        }
        if isinstance(navigation, dict) and isinstance(navigation.get("view"), str):
            result["directory"] = navigation["view"]
        return result

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
        "types": [type_info(t.id) for t in registry.types],
        "fields": [
            {
                "fieldId": f.id,
                "displayName": f.id,
                "valueType": f.schema["type"],
                "unit": _unit(f.metadata.get("unit")),
                "frame": _frame(f.metadata.get("frame")),
                "schema": thaw(f.schema),
                "metadata": thaw(f.metadata),
                **({"role": f.metadata["role"]} if "role" in f.metadata else {}),
            }
            for f in registry.fields
        ],
        "presentation": scenario.get("presentation", []),
        "start": {"ns": "0", "microstep": 0},
        "runtimeRegistry": lossless(registry.to_data()),
        "messages": lossless(registry.to_data()["messages"]),
    }
    subject_spec = scenario["registry"]
    declared = dict(subject_spec.get("message_subjects", {}))
    declared.update({
        item["id"]: item["subjects"]
        for item in subject_spec.get("messages", []) if "subjects" in item
    })
    if declared:
        result["messageSubjects"] = declared
    if "origin" in scenario:
        result["origin"] = scenario["origin"]
    entries = storage.entries
    if entries:
        record = storage.records(entries[-1]["index"], 1)[0]
        if metadata["status"] in {"completed", "stopped", "faulted", "interrupted"}:
            result["end"] = instant(decode_record(record["instant"]))
    return result
