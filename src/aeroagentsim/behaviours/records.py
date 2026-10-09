"""Portable, journaled orchestration descriptors (never private viewer state)."""

from __future__ import annotations

from typing import Any

TYPE = "aas:BehaviourInstance"
PREFIX = "aas.behaviour."
LIFECYCLES = ("created", "transitioned", "completed", "failed", "canceled")
EVENTS = tuple(
    PREFIX + name
    for name in (
        *LIFECYCLES,
        "predicate_evaluated",
        "conflict",
        "action_conflict",
        "action_started",
    )
)
INJECT = "aas.runtime.inject_event"


def record_schema(members: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members or {},
        "required": list(members or {}),
        "extra": True,
    }


def overlay() -> dict[str, Any]:
    """Explicit platform overlay installed only for scenarios enabling behaviours."""
    schemas = {
        "template_id": {"type": "string"},
        "binding_id": {"type": "string"},
        "roles": record_schema(),
        "state": {"type": "string"},
        "revision": {"type": "integer", "minimum": 0},
        "status": {"type": "string"},
        "children": record_schema(),
    }
    text = {"type": "string"}
    time = record_schema({"ns": text, "microstep": {"type": "integer", "minimum": 0}})
    interval = {
        "validFrom": time,
        "validTo": {**time, "nullable": True},
        "op": {"type": "string", "enum": ["assert", "close"]},
    }
    lifecycle = record_schema(
        {
            **interval,
            "instanceId": text,
            "templateId": text,
            "bindingId": text,
            "packageDigest": text,
            "roles": record_schema(),
            "lifecycle": {"type": "string", "enum": list(LIFECYCLES)},
            "state": text,
            "revision": {"type": "integer", "minimum": 0},
            "variables": record_schema(),
            "children": record_schema(),
            "transitionId": {"type": "string", "nullable": True},
            "trigger": record_schema(),
        }
    )
    truth = record_schema(
        {
            **interval,
            "contextId": text,
            "predicateId": text,
            "roles": record_schema(),
            "profile": {
                "type": "string",
                "enum": ["committed_reactive/v1", "aerograph_sampled/v1"],
            },
            "status": {
                "type": "string",
                "enum": ["known", "required_input", "invalid_input"],
            },
            "value": {"type": "boolean", "nullable": True},
            "diagnostics": {"type": "array", "items": record_schema()},
            "evaluatedAt": time,
            "readCut": record_schema(
                {"index": {"type": "integer", "minimum": 0}, "at": time}
            ),
            "acquired": record_schema(),
        }
    )
    return {
        "types": [{"id": TYPE, "parents": [], "abstract": False}],
        "fields": [
            {
                "id": PREFIX + name,
                "type": TYPE,
                "schema": schema,
                "metadata": {"role": "behaviour"},
            }
            for name, schema in schemas.items()
        ],
        "messages": [
            {
                "id": PREFIX + "sample_signal",
                "kind": "event",
                "schema": record_schema(
                    {
                        "predicate": text,
                        "from_ns": {"type": "integer"},
                        "to_ns": {"type": "integer"},
                    }
                ),
            },
            *[
                {
                    "id": event,
                    "kind": "event",
                    "schema": lifecycle
                    if event.removeprefix(PREFIX) in LIFECYCLES
                    else truth
                    if event == PREFIX + "predicate_evaluated"
                    else record_schema(),
                }
                for event in EVENTS
            ],
            {
                "id": INJECT,
                "kind": "command",
                "schema": record_schema(
                    {"injection_point": {"type": "string"}, "payload": record_schema()}
                ),
                "result_schema": record_schema(),
            },
        ],
    }


def instant(ns: int, microstep: int = 0) -> dict[str, Any]:
    return {"ns": str(ns), "microstep": microstep}
