"""Pinned live ingress policies and deterministic reservation processing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any

from .codec import encode
from .control import reserve
from .errors import KernelError
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

    def __post_init__(self) -> None:
        if type(self.initial_watermark_ns) is not int or self.initial_watermark_ns < 0:
            raise KernelError("INGRESS_WATERMARK", "declare a nonnegative watermark")
        if self.lateness not in {"reject", "delay"}:
            raise KernelError("INGRESS_LATENESS", "declare reject or delay")
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


def reserve_live(
    store: Store,
    request: CommandRequest,
    stamp: Stamp,
    policy: IngressPolicy,
    mappings: Mapping[str, ClockMapping],
    budget: ResourceBudget,
) -> tuple[Store, dict[str, Any], str | None]:
    """Reserve a mapped observation, recording actual rejection or displacement."""
    if store.watermark_ns is None or store.sealed_ns is None or store.pending_intents:
        raise KernelError("INGRESS_STATE", "live ingress requires a settled prefix")
    if not isinstance(request, CommandRequest) or not isinstance(stamp, Stamp):
        raise KernelError("INGRESS_REQUEST", "typed command and source stamp required")
    if stamp.mapping_id not in mappings:
        raise KernelError("CLOCK_MAPPING", "unknown live ingress mapping")
    mapped = mappings[stamp.mapping_id].map(stamp)
    content = canonical_json(
        {"request": encode(request), "source_stamp": encode(stamp)}, budget
    )
    if request.idempotency_key is not None:
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
    store.registry.validate(descriptor.schema, request.payload, budget=budget)
    late = mapped <= store.watermark_ns or mapped <= store.sealed_ns
    common = {
        "original_request": encode(request),
        "source_stamp": encode(stamp),
        "mapped_ns": mapped,
        "policy": encode(policy),
    }
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
        return state, record, None
    boundary = max(mapped, store.cut.instant.ns + 1)
    if late:
        boundary = max(boundary, store.watermark_ns + 1)
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
    state, reserved, mid = reserve(reservable, adjusted, budget)
    state.run_target = store.run_target
    data = {**reserved["items"][0], "source_stamp": encode(stamp)}
    state.pending_ingress[mid] = data
    if request.idempotency_key is not None:
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
    return state, record, mid
