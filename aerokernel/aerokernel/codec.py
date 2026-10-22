"""Closed, versioned encoding for kernel records, never arbitrary Python objects."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any, cast

from .errors import KernelError


class RecordDecodeError(KernelError):
    """Malformed typed-record shape; still compatible with ValueError handlers."""


@lru_cache(maxsize=256)
def _field_names(cls: type[Any]) -> tuple[str, ...]:
    """Dataclass declarations are fixed; reuse their ordered wire shape."""
    return tuple(field.name for field in dataclasses.fields(cls))


@lru_cache(maxsize=256)
def _field_set(cls: type[Any]) -> frozenset[str]:
    return frozenset(_field_names(cls))


def encode(value: object) -> Any:
    """Encode an internal record while keeping portable payload trees distinct."""
    if isinstance(value, _DecodedRecord):
        return dict(value)
    if value is None or type(value) in (bool, int, float, str):
        return value
    if type(value) in (list, tuple):
        return [encode(v) for v in cast(list[object] | tuple[object, ...], value)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {
                name: encode(getattr(value, name)) for name in _field_names(type(value))
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
_HISTORICAL_DECLARATION_FIELDS = {
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


class _DecodedRecord(dict[str, Any]):
    """Internal read-only wire frame; retain its validated typed record once.

    The wire keys remain available to lifecycle readers. This wrapper is used
    for projections and locally constructed dispatches. Attributes are never
    written to WAL; JSON ingestion always validates ordinary wire dictionaries.
    """

    def __init__(self, wire: dict[str, Any]) -> None:
        super().__init__(wire)
        self.decoded = decode_record(wire)

    @classmethod
    def from_record(cls, record: Any) -> _DecodedRecord:
        """Encode an already validated immutable internal record once."""
        result = dict.__new__(cls)
        dict.__init__(result, encode(record))
        result.decoded = record
        return result


def decode_record(value: Any) -> Any:
    """Decode known record shapes, not journal admission or semantic verification.

    Portable payload trees remain opaque; only complete historical declaration
    shapes receive their original additive defaults.
    """
    if isinstance(value, _DecodedRecord):
        return value.decoded
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
    if (
        set(value) != {"$type", "fields"}
        or type(value["$type"]) is not str
        or value["$type"] not in classes
    ):
        raise RecordDecodeError("RECORD_TYPE", "unsupported record")
    cls = classes[value["$type"]]
    fields = value["fields"]
    expected = _field_set(cls)
    historical = _HISTORICAL_DECLARATION_FIELDS.get(value["$type"])
    if not isinstance(fields, dict) or (
        set(fields) != expected and (historical is None or set(fields) != historical)
    ):
        raise RecordDecodeError("RECORD_FIELDS", "unexpected record fields")
    # Payloads are portable trees, not nested record encodings.
    payload_fields = {
        "payload",
        "value",
        "result",
        "parameters",
        "configuration",
        "metadata",
    }
    try:
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
    except KernelError:
        # Preserve semantic constructor faults and resource-limit subclasses.
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RecordDecodeError("RECORD_FIELDS", "malformed record fields") from exc
