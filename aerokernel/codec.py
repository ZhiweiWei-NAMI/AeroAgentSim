"""Closed, versioned encoding for kernel records, never arbitrary Python objects."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any


def encode(value: object) -> Any:
    """Encode an internal record while keeping portable payload trees distinct."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {
                field.name: encode(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode_record(value: Any) -> Any:
    """Decode only known kernel records, with explicit sequence fields."""
    from . import engine, ids, messages, operations, time, values

    classes = {
        name: cls
        for module in (engine, ids, messages, operations, time, values)
        for name, cls in vars(module).items()
        if isinstance(cls, type) and dataclasses.is_dataclass(cls)
    }
    if isinstance(value, list):
        return tuple(decode_record(v) for v in value)
    if not isinstance(value, dict):
        return value
    if "$type" not in value:
        return {
            k: v if k in {"payload", "value", "result"} else decode_record(v)
            for k, v in value.items()
        }
    if set(value) != {"$type", "fields"} or value["$type"] not in classes:
        raise ValueError("RECORD_TYPE: unsupported record")
    cls = classes[value["$type"]]
    fields = value["fields"]
    if set(fields) != {f.name for f in dataclasses.fields(cls)}:
        raise ValueError("RECORD_FIELDS: unexpected record fields")
    # Payloads are portable trees, not nested record encodings.
    payload_fields = {"payload", "value", "result"}
    kwargs = {
        k: v if k in payload_fields else decode_record(v) for k, v in fields.items()
    }
    return cls(**kwargs)
