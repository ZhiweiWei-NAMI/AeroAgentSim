"""Recorded ingress reservations, timer firing, sealing and fault controls."""

from __future__ import annotations

from typing import Any

from .codec import decode_record, encode
from .errors import KernelError
from .ids import ItemRef, message_id
from .messages import CommandRequest, Dirty, Message
from .state import Store, Work
from .time import Cut, Instant
from .transactions import Candidate, eligibility
from .values import canonical_json, freeze, normalize


def reserve(
    store: Store,
    request: CommandRequest,
    budget: Any,
    *,
    ingress_source: str | None = None,
) -> tuple[Store, dict[str, Any], str]:
    """Reserve identity now, without publishing or invoking a future command."""
    content = canonical_json(encode(request), budget)
    if request.idempotency_key is not None:
        existing = store.idempotency.get(request.idempotency_key)
        if existing is not None:
            if content != existing[0]:
                raise KernelError(
                    "IDEMPOTENCY_CONFLICT", "key reused with different content"
                )
            return store, {}, existing[1]
    if store.sealed_ns is None or store.run_target is not None:
        raise KernelError("INGRESS_STATE", "submit requires a settled started run")
    boundary = (
        store.sealed_ns + 1 if request.ingress_at_ns is None else request.ingress_at_ns
    )
    if (
        type(boundary) is not int
        or boundary <= store.sealed_ns
        or request.at.ns < boundary
    ):
        raise KernelError(
            "INGRESS_BOUNDARY", "activation/publication must be beyond seal"
        )
    descriptor = store.registry.message(request.schema_id)
    if descriptor.kind != "command" or request.target not in store.partitions:
        raise KernelError(
            "COMMAND_ROUTE", "ingress requires a registered command target"
        )
    if request.schema_id not in store.partitions[request.target].commands:
        raise KernelError("COMMAND_UNSUPPORTED", "target does not accept command")
    store.registry.validate(descriptor.schema, request.payload, budget=budget)
    normalized = CommandRequest(
        request.schema_id,
        request.target,
        request.at,
        normalize(request.payload, budget),
        request.idempotency_key,
        boundary,
    )
    state = store.clone()
    key = (
        "ingress",
        store.manifest.ingress_source if ingress_source is None else ingress_source,
    )
    sequence = state.sequences.get(key, 0)
    state.sequences[key] = sequence + 1
    mid = message_id(store.manifest.run_id, store.manifest.epoch, *key, sequence)
    index = store.cut.index + 1
    origin = ItemRef(index, 0)
    data = {
        "kind": "reservation",
        "message_id": mid,
        "request": encode(normalized),
        "boundary": boundary,
        "sequence": sequence,
        "origin": encode(origin),
    }
    if ingress_source is not None:
        data["source"] = ingress_source
    state.pending_ingress[mid] = data
    state.actions.pending(mid, request.target, *key, origin)
    if request.idempotency_key is not None:
        state.idempotency[request.idempotency_key] = (content, mid)
    record = {
        "type": "ingress",
        "index": index,
        "instant": encode(store.cut.instant),
        "request": encode(request),
        "items": [data],
    }
    state.records.append(record)
    state.cuts.append(Cut(index, store.cut.instant))
    return state, record, mid


def publish_ingress(
    candidate: Candidate,
    request: CommandRequest,
    mid: str | None = None,
    data: dict[str, Any] | None = None,
) -> str:
    """Enqueue + submitted status + receipt in one boundary transaction."""
    state = candidate.state
    source = (
        "ingress",
        state.manifest.ingress_source
        if data is None
        else data.get("source", state.manifest.ingress_source),
    )
    if data is None:
        sequence = state.sequences.get(source, 0)
        state.sequences[source] = sequence + 1
        mid = message_id(state.manifest.run_id, state.manifest.epoch, *source, sequence)
        origin = candidate.add(
            {"kind": "offline_request", "request": encode(request), "message_id": mid}
        )
    else:
        sequence = data["sequence"]
        origin = decode_record(data["origin"])
    if mid is None:
        raise KernelError("INGRESS_ID", "reserved identity missing")
    state.registry.validate(
        state.registry.message(request.schema_id).schema,
        request.payload,
        budget=candidate.budget,
    )
    msg = Message(
        mid,
        "command",
        request.schema_id,
        *source,
        sequence,
        request.at,
        candidate.instant,
        freeze(request.payload, candidate.budget),
        (),
        None
        if data is None or "source_stamp" not in data
        else decode_record(data["source_stamp"]),
        origin,
        candidate.budget,
    )
    candidate.add(
        {"kind": "ingress_publish", "message": encode(msg), "origin": encode(origin)}
    )
    candidate.enqueue(msg, request.target, origin)
    state.pending_ingress.pop(mid, None)
    return mid


def boundary_control(candidate: Candidate) -> dict[str, Any]:
    """Consume due timers/reservations and generate only actual notifications."""
    state = candidate.state
    for mid, data in sorted(dict(state.pending_ingress).items()):
        if data["boundary"] <= candidate.instant.ns:
            if state.ingress_dependencies:
                from .ingress import safe_partitions

                target = (
                    state.actions.action(data["command_id"]).target
                    if data.get("cancel")
                    else decode_record(data["request"]).target
                )
                if target not in safe_partitions(state, candidate.instant.ns):
                    continue
            if data.get("cancel"):
                origin = decode_record(data["origin"])
                # The cancel message identity/sequence was already reserved.
                action = state.actions.action(data["command_id"])
                msg = Message(
                    mid,
                    "cancel",
                    state.messages[data["command_id"]].schema_id,
                    "ingress",
                    state.manifest.control_source,
                    data["sequence"],
                    candidate.instant,
                    candidate.instant,
                    freeze({"command_id": data["command_id"]}),
                    (origin,),
                    None,
                    origin,
                    candidate.budget,
                )
                state.messages[mid] = msg
                at = eligibility(
                    state,
                    action.target,
                    candidate.instant,
                    candidate.instant,
                    state.partitions[action.target].message_lag_ns,
                )
                enqueue = candidate.add(
                    {
                        "kind": "enqueue",
                        "message": mid,
                        "recipient": action.target,
                        "eligible": encode(at),
                        "origin": encode(origin),
                        "value": encode(msg),
                    }
                )
                state.work.append(Work(action.target, at, enqueue, mid))
                state.pending_ingress.pop(mid)
            else:
                publish_ingress(candidate, decode_record(data["request"]), mid, data)
    for key in sorted(state.timer_queue.take(candidate.instant)):
        try:
            timer = state.timers[key]
            due = decode_record(timer["due"])
            if not isinstance(due, Instant) or timer["state"] != "pending":
                raise ValueError("invalid active deadline")
        except (KeyError, TypeError, ValueError) as exc:
            raise KernelError(
                "TIMER_RECORD", "malformed active timer", timer_key=key
            ) from exc
        timer = {**timer, "state": "fired"}
        state.timers[key] = timer
        cause = candidate.add(
            {"kind": "timer_fire", "id": list(key), "timer": timer.copy()}
        )
        if timer.get("kind") == "validity":
            if len(key) != 3 or "key" not in timer:
                raise KernelError(
                    "TIMER_RECORD", "malformed validity timer", timer_key=key
                )
            field_key = decode_record(timer["key"])
            candidate.notify_field(field_key, cause, True)
        elif timer.get("kind") == "relation":
            from .relations import notify_relation

            if len(key) != 3 or "relation_id" not in timer:
                raise KernelError(
                    "TIMER_RECORD", "malformed relation deadline", timer_key=key
                )
            notify_relation(candidate, timer["relation_id"], cause, boundary=True)
        else:
            if (
                timer.get("kind") != "partition"
                or len(key) != 2
                or key[0] not in state.partitions
                or "payload" not in timer
            ):
                raise KernelError(
                    "TIMER_RECORD", "malformed partition timer", timer_key=key
                )
            at = eligibility(state, key[0], due, candidate.instant)
            dirty = Dirty(
                None,
                "timer",
                at,
                cause,
                True,
                freeze(timer["payload"], candidate.budget),
                candidate.budget,
            )
            state.work.append(Work(key[0], at, cause, dirty=dirty, timer_key=key))
            candidate.add(
                {
                    "kind": "timer_notification",
                    "recipient": key[0],
                    "notification": encode(dirty),
                }
            )
    record = {
        "type": "boundary",
        "index": candidate.index,
        "instant": encode(candidate.instant),
        "items": candidate.items,
    }
    state.records.append(record)
    if state.cut.index != candidate.index:
        state.cuts.append(Cut(candidate.index, candidate.instant))
    return record
