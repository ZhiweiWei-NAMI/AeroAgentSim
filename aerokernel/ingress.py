"""Pinned live ingress policies and deterministic reservation processing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any, Literal

from .codec import encode
from .control import reserve
from .errors import KernelError
from .ids import validate_text
from .messages import CommandRequest
from .state import Store
from .time import ClockMapping, Cut, Instant, Stamp
from .values import ResourceBudget, canonical_json


@dataclass(frozen=True)
class IngressPolicy:
    """One declared closed-prefix stream and its bounded waiting policy."""

    initial_watermark_ns: int
    lateness: str = "reject"
    timeout_s: float = 1.0
    speed_ratio: float | None = None
    allowed_lateness_ns: int | None = None

    def __post_init__(self) -> None:
        if type(self.initial_watermark_ns) is not int or self.initial_watermark_ns < 0:
            raise KernelError("INGRESS_WATERMARK", "declare a nonnegative watermark")
        if self.lateness not in {"reject", "delay"}:
            raise KernelError("INGRESS_LATENESS", "declare reject or delay")
        if self.allowed_lateness_ns is not None and (
            type(self.allowed_lateness_ns) is not int or self.allowed_lateness_ns < 0
        ):
            raise KernelError(
                "INGRESS_LATENESS", "nonnegative integer ns bound required"
            )
        for name, value in (("timeout", self.timeout_s), ("ratio", self.speed_ratio)):
            if value is None and name == "ratio":
                continue
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
                or value <= 0
            ):
                raise KernelError("INGRESS_WAIT", "positive finite wait/ratio required")


@dataclass(frozen=True)
class IngressStream:
    """Pinned source, clock mapping and complete engine recipient domain."""

    id: str
    policy: IngressPolicy
    mapping_id: str
    engine_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_text(self.id)
        validate_text(self.mapping_id)
        if not isinstance(self.policy, IngressPolicy):
            raise KernelError("INGRESS_POLICY", "typed stream policy required")
        if (
            not isinstance(self.engine_ids, tuple)
            or not self.engine_ids
            or len(set(self.engine_ids)) != len(self.engine_ids)
        ):
            raise KernelError(
                "INGRESS_BINDING", "unique nonempty engine tuple required"
            )
        for engine_id in self.engine_ids:
            validate_text(engine_id)


@dataclass(frozen=True)
class IngressReceipt:
    """Admission result from an acknowledged WAL record, not execution success."""

    stream_id: str
    disposition: Literal["accepted", "delayed", "rejected"]
    journal_index: int
    mapped_ns: int
    command_id: str | None
    boundary_ns: int | None
    activation_ns: int | None
    delay_ns: int | None
    code: str | None


def receipt_for(record: dict[str, Any]) -> IngressReceipt:
    from .codec import decode_record

    item = record["items"][0] if record["items"] else None
    return IngressReceipt(
        record.get("stream_id", "default"),
        record["disposition"],
        record["index"],
        record["mapped_ns"],
        item["message_id"] if item is not None else None,
        item["boundary"] if item is not None else None,
        decode_record(item["request"]).at.ns if item is not None else None,
        record["delay_ns"] if item is not None else None,
        record.get("code"),
    )


def safe_partitions(store: Store, ns: int) -> tuple[str, ...]:
    """Watermark-safe dependency cones; empty declarations preserve legacy waves."""
    return tuple(
        p
        for p in store.partitions
        if all(store.watermarks[s] >= ns for s in store.ingress_dependencies.get(p, ()))
    )


def reserve_live(
    store: Store,
    request: CommandRequest,
    stamp: Stamp,
    policy: IngressPolicy,
    mappings: Mapping[str, ClockMapping],
    budget: ResourceBudget,
    *,
    stream: IngressStream | None = None,
    legacy: bool = False,
) -> tuple[Store, dict[str, Any], str | None]:
    """Reserve a mapped observation, recording actual rejection or displacement."""
    watermark = store.watermark_ns if stream is None else store.watermarks[stream.id]
    if watermark is None or store.sealed_ns is None or store.pending_intents:
        raise KernelError("INGRESS_STATE", "live ingress requires a settled prefix")
    if not isinstance(request, CommandRequest) or not isinstance(stamp, Stamp):
        raise KernelError("INGRESS_REQUEST", "typed command and source stamp required")
    if stamp.mapping_id not in mappings:
        raise KernelError("CLOCK_MAPPING", "unknown live ingress mapping")
    if stream is not None and stamp.mapping_id != stream.mapping_id:
        raise KernelError("CLOCK_MAPPING", "stamp differs from pinned stream mapping")
    mapped = mappings[stamp.mapping_id].map(stamp)
    content = canonical_json(
        {"request": encode(request), "source_stamp": encode(stamp)}, budget
    )
    stream_id = "default" if stream is None else stream.id
    if not legacy and request.idempotency_key is not None:
        prior = store.ingress_dedup.get((stream_id, request.idempotency_key))
        if prior is not None:
            if content != prior[0]:
                raise KernelError("IDEMPOTENCY_CONFLICT", "different original content")
            return store, {}, prior[1].command_id
    if (legacy or stream is None) and request.idempotency_key is not None:
        existing = store.idempotency.get(request.idempotency_key)
        if existing is not None:
            if content != existing[0]:
                raise KernelError("IDEMPOTENCY_CONFLICT", "different original content")
            return store, {}, existing[1]
    descriptor = store.registry.message(request.schema_id)
    if descriptor.kind != "command" or request.target not in store.partitions:
        raise KernelError("COMMAND_ROUTE", "live ingress requires a command target")
    if request.schema_id not in store.partitions[request.target].commands:
        raise KernelError("COMMAND_UNSUPPORTED", "target does not accept command")
    if (
        stream is not None
        and store.partitions[request.target].engine_id not in stream.engine_ids
    ):
        raise KernelError("INGRESS_BINDING", "target engine is outside stream domain")
    store.registry.validate(descriptor.schema, request.payload, budget=budget)
    late = mapped <= watermark or mapped <= store.sealed_ns
    policy_data = encode(policy)
    if legacy:
        policy_data["fields"].pop("allowed_lateness_ns")
    common = {
        "original_request": encode(request),
        "source_stamp": encode(stamp),
        "mapped_ns": mapped,
        "policy": policy_data,
    }
    if not legacy:
        common["stream_id"] = stream_id
    if late and policy.lateness == "reject":
        state = store.clone()
        record = {
            "type": "live_ingress_rejection",
            "index": store.cut.index + 1,
            "instant": encode(store.cut.instant),
            **common,
            "disposition": "rejected",
            "code": "LATE_INGRESS",
            "items": [],
        }
        state.records.append(record)
        state.cuts.append(Cut(record["index"], store.cut.instant))
        if not legacy and request.idempotency_key is not None:
            state.ingress_dedup[(stream_id, request.idempotency_key)] = (
                content,
                receipt_for(record),
            )
        return state, record, None
    boundary = max(mapped, store.cut.instant.ns + 1)
    if late:
        boundary = max(boundary, watermark + 1)
    if request.ingress_at_ns is not None:
        boundary = max(boundary, request.ingress_at_ns)
    activation = max(request.at.ns, boundary)
    adjusted = replace(
        request,
        at=Instant(
            activation, request.at.microstep if activation == request.at.ns else 0
        ),
        ingress_at_ns=boundary,
        idempotency_key=None,
    )
    reservable = store.clone()
    reservable.run_target = None
    state, reserved, mid = reserve(
        reservable,
        adjusted,
        budget,
        ingress_source=None if stream is None else "stream:" + stream.id,
    )
    state.run_target = store.run_target
    data = {**reserved["items"][0], "source_stamp": encode(stamp)}
    state.pending_ingress[mid] = data
    if (legacy or stream is None) and request.idempotency_key is not None:
        state.idempotency[request.idempotency_key] = (content, mid)
    record = {
        **reserved,
        **common,
        "type": "live_ingress",
        "items": [data],
        "disposition": "delayed" if late else "accepted",
        "delay_ns": boundary - mapped,
    }
    state.records = store.records.fork()
    state.records.append(record)
    receipt = receipt_for(record)
    state.ingress_receipts[mid] = receipt
    if not legacy:
        if request.idempotency_key is not None:
            state.ingress_dedup[(stream_id, request.idempotency_key)] = (
                content,
                receipt,
            )
    return state, record, mid
