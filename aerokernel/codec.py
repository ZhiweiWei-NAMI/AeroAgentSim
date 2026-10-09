"""Closed, versioned encoding for kernel records, never arbitrary Python objects."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any, cast


def encode(value: object) -> Any:
    """Encode an internal record while keeping portable payload trees distinct."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if type(value) in (list, tuple):
        return [encode(v) for v in cast(list[object] | tuple[object, ...], value)]
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
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [encode(v) for v in value]
    return value


@lru_cache(maxsize=1)
def _record_classes() -> dict[str, type[Any]]:
    from . import (
        engine,
        ids,
        ingress,
        messages,
        operations,
        relations,
        sampling,
        state,
        time,
        values,
    )

    return {
        name: cls
        for module in (
            engine,
            ids,
            ingress,
            messages,
            operations,
            relations,
            sampling,
            state,
            time,
            values,
        )
        for name, cls in vars(module).items()
        if isinstance(cls, type) and dataclasses.is_dataclass(cls)
    }


# Complete declaration shapes issued by journals 1.1/1.2. Only these historical
# shapes may omit additive declarations; arbitrary missing fields still fail.
_M1_DECLARATION_FIELDS = {
    "IngressPolicy": frozenset(
        {"initial_watermark_ns", "lateness", "timeout_s", "speed_ratio"}
    ),
    "Timing": frozenset({"mode", "step_ns", "origin_ns", "latch"}),
    "Partition": frozenset(
        {
            "id",
            "engine_id",
            "produces",
            "consumes",
            "commands",
            "emits",
            "subscribes",
            "timing",
            "reactive",
            "lifecycle",
            "rng_streams",
            "lifecycle_reads",
            "features",
            "message_targets",
            "message_lag_ns",
        }
    ),
    "Horizon": frozenset(
        {
            "reached",
            "native_reached_ns",
            "next_wakeup_ns",
            "output_lb_ns",
            "grant_limit_ns",
            "input_cut",
        }
    ),
}


def decode_record(value: Any) -> Any:
    """Decode only known kernel records, with explicit sequence fields."""
    classes = _record_classes()
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
    expected = {f.name for f in dataclasses.fields(cls)}
    historical = _M1_DECLARATION_FIELDS.get(value["$type"])
    if set(fields) != expected and (historical is None or set(fields) != historical):
        raise ValueError("RECORD_FIELDS: unexpected record fields")
    # Payloads are portable trees, not nested record encodings.
    payload_fields = {
        "payload",
        "value",
        "result",
        "parameters",
        "configuration",
        "metadata",
    }
    kwargs = {
        k: (
            v
            if k in payload_fields
            else {role: decode_record(entry) for role, entry in v.items()}
            if k in {"bindings", "sources", "clocks"}
            else decode_record(v)
        )
        for k, v in fields.items()
    }
    if cls.__name__ == "ActionState":
        kwargs["history"] = tuple(fields["history"])
    return cls(**kwargs)
