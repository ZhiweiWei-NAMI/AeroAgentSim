"""Typed proposals, recipient dispatch envelopes and receipt-derived actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from .errors import KernelError
from .ids import FieldKey, ItemRef, LocalCause, validate_text
from .registry import MemoryRegistry
from .time import Instant, Stamp
from .values import FrozenValue, ResourceBudget, freeze, normalize, thaw

Cause = ItemRef | LocalCause


@dataclass(frozen=True)
class Emit:
    """Propose a message; the kernel supplies source, sequence and availability."""

    kind: str
    schema_id: str
    target_or_topic: str
    at: Instant
    payload: object
    causes: tuple[Cause, ...] = ()
    source_stamp: Stamp | None = None


@dataclass(frozen=True)
class CommandRequest:
    """Host ingress proposal without forgeable producer/publication stamps."""

    schema_id: str
    target: str
    at: Instant
    payload: object
    idempotency_key: str | None = None
    ingress_at_ns: int | None = None

    def __post_init__(self) -> None:
        validate_text(self.schema_id)
        validate_text(self.target)
        if not isinstance(self.at, Instant):
            raise KernelError("COMMAND_AT", "requested activation requires an Instant")
        if self.idempotency_key is not None:
            validate_text(self.idempotency_key)
        if self.ingress_at_ns is not None and (
            type(self.ingress_at_ns) is not int or self.ingress_at_ns < 0
        ):
            raise KernelError(
                "INGRESS_AT", "ingress boundary requires nonnegative integer"
            )


@dataclass(frozen=True)
class Receipt:
    """Target decision referencing actual dispatch and the current receipt head."""

    command_id: str
    status: str
    result: object = None
    causes: tuple[Cause, ...] = ()


@dataclass(frozen=True)
class Feedback:
    """Typed progress while executing/canceling; never implies completion."""

    command_id: str
    payload: object
    causes: tuple[Cause, ...] = ()


@dataclass(frozen=True)
class RequestCancel:
    """Origin/control cancellation request, separate from target acceptance."""

    command_id: str
    causes: tuple[Cause, ...] = ()


@dataclass(frozen=True)
class CancelDecision:
    """Target response to a dispatched nonrecursive cancel envelope."""

    command_id: str
    cancel_message_id: str
    accepted: bool
    reason: str | None = None
    causes: tuple[Cause, ...] = ()


@dataclass(frozen=True)
class Message:
    """Committed immutable message with kernel-assigned identity and timing."""

    id: str
    kind: str
    schema_id: str
    source_kind: str
    source: str
    sequence: int
    at: Instant
    available: Instant
    payload: FrozenValue
    causes: tuple[ItemRef, ...]
    source_stamp: Stamp | None = None
    origin: ItemRef | None = None
    resource_budget: ResourceBudget | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.schema_id, self.source):
            validate_text(value)
        if self.kind not in {"command", "event", "cancel"} or self.source_kind not in {
            "partition",
            "ingress",
            "kernel",
        }:
            raise KernelError("MESSAGE_KIND", "invalid message kind/source namespace")
        if type(self.sequence) is not int or self.sequence < 0:
            raise KernelError(
                "MESSAGE_SEQUENCE", "nonnegative integer sequence required"
            )
        if self.kind == "event" and self.at > self.available:
            raise KernelError(
                "MESSAGE_EVENT_TIME", "event occurrence follows publication"
            )
        object.__setattr__(
            self, "payload", freeze(thaw(self.payload), self.resource_budget)
        )

    def to_data(self) -> dict[str, Any]:
        """Encode a kernel message using the closed record codec."""
        from .codec import encode

        result: dict[str, Any] = encode(self)
        return result

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Message:
        """Decode exactly one committed message."""
        from .codec import decode_record

        result = decode_record(data)
        if not isinstance(result, cls):
            raise KernelError("MESSAGE_DATA", "expected a Message")
        return result


@dataclass(frozen=True)
class Delivery:
    """Actual recipient dispatch, distinct from publication and queue eligibility."""

    message: Message
    recipient: str
    instant: Instant
    enqueue_ref: ItemRef
    dispatch_ref: ItemRef

    def to_data(self) -> dict[str, Any]:
        """Encode the actual dispatch envelope."""
        from .codec import encode

        result: dict[str, Any] = encode(self)
        return result

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Delivery:
        """Decode a dispatch envelope without performing delivery."""
        from .codec import decode_record

        result = decode_record(data)
        if not isinstance(result, cls):
            raise KernelError("DELIVERY_DATA", "expected a Delivery")
        return result


@dataclass(frozen=True)
class Dirty:
    """A dispatched field/lifecycle/receipt/timer notification with typed cause."""

    key: FieldKey | None
    kind: str
    instant: Instant
    cause: ItemRef
    value_changed: bool = True
    payload: FrozenValue = None


@dataclass(frozen=True)
class ActionState:
    """Action status derives solely from its canonical receipt history."""

    command_id: str
    status: str
    target: str
    source_kind: str
    source: str
    head: ItemRef | None
    history: tuple[Mapping[str, Any], ...]
    origin: ItemRef | None = None


@dataclass(frozen=True)
class CancelRequestResult:
    """Queued means reserved cancellation identity, never acceptance or cleanup."""

    queued: bool
    message_id: str | None
    rejection_ref: ItemRef | None


TRANSITIONS = {
    "submitted": {"accepted", "rejected"},
    "accepted": {"executing", "failed", "canceling"},
    "executing": {"succeeded", "failed", "canceling"},
    "canceling": {"canceled", "failed"},
    "rejected": set(),
    "succeeded": set(),
    "failed": set(),
    "canceled": set(),
}


def _ref(ref: ItemRef | None) -> dict[str, int] | None:
    return (
        None
        if ref is None
        else {"record_index": ref.record_index, "item_index": ref.item_index}
    )


class Actions:
    """Private ordered action candidate state shared by live execution and replay."""

    def __init__(
        self, registry: MemoryRegistry, budget: ResourceBudget | None = None
    ) -> None:
        self.registry = registry
        self.budget = budget
        self.states: dict[str, ActionState] = {}
        self.schemas: dict[str, str] = {}
        self.dispatches: dict[str, Delivery] = {}
        self.cancel_heads: dict[str, ItemRef] = {}
        self.decisions: set[str] = set()

    def clone(self) -> Actions:
        """Detach mutable candidate indexes, retaining immutable record values."""
        other = Actions(self.registry, self.budget)
        other.states = self.states.copy()
        other.schemas = self.schemas.copy()
        other.dispatches = self.dispatches.copy()
        other.cancel_heads = self.cancel_heads.copy()
        other.decisions = self.decisions.copy()
        return other

    def action(self, id: str) -> ActionState:
        """Read explicit pending/submitted/terminal state; unknown IDs fail."""
        if id not in self.states:
            raise KernelError("ACTION_UNKNOWN", "unknown command ID")
        # Every exposed receipt/result tree is deeply immutable.
        state = self.states[id]
        history = tuple(
            cast(Mapping[str, Any], freeze(thaw(item), self.budget))
            for item in state.history
        )
        return replace(state, history=history)

    def pending(
        self,
        message_id: str,
        target: str,
        source_kind: str,
        source: str,
        origin: ItemRef,
    ) -> None:
        """Install a reservation once, or check its identical publishing identity."""
        if message_id in self.states:
            existing = self.states[message_id]
            if (
                existing.status,
                existing.target,
                existing.source_kind,
                existing.source,
                existing.origin,
            ) != ("pending", target, source_kind, source, origin):
                raise KernelError("ACTION_DUPLICATE", "reservation identity mismatch")
            return
        self.states[message_id] = ActionState(
            message_id, "pending", target, source_kind, source, None, (), origin
        )

    def submit(self, message: Message, ref: ItemRef) -> dict[str, Any]:
        """Enqueue creates submitted status and receipt together, never later."""
        state = self.action(message.id)
        if (
            state.status != "pending"
            or message.kind != "command"
            or (state.source_kind, state.source)
            != (message.source_kind, message.source)
        ):
            raise KernelError("ACTION_SUBMIT", "invalid submitted transition")
        self.schemas[message.id] = message.schema_id
        item = {
            "kind": "receipt",
            "command_id": message.id,
            "status": "submitted",
            "ref": _ref(ref),
            "origin": _ref(message.origin),
            "result": None,
        }
        self.states[message.id] = replace(
            state, status="submitted", head=ref, history=(item,)
        )
        return item

    def record_dispatch(self, delivery: Delivery) -> None:
        """Record intent-authorized delivery even if the target returns no receipt."""
        message = delivery.message
        if message.kind == "event":
            return
        if message.id in self.dispatches:
            raise KernelError("ACTION_DISPATCH", "command/cancel dispatched twice")
        if (
            message.kind == "command"
            and self.action(message.id).target != delivery.recipient
        ):
            raise KernelError("ACTION_TARGET", "dispatch to wrong action target")
        self.dispatches[message.id] = delivery

    def _authorized(
        self,
        partition: str,
        id: str,
        head: ItemRef | None,
        cause_refs: tuple[ItemRef, ...],
        message_id: str | None = None,
    ) -> ActionState:
        state = self.action(id)
        if state.target != partition:
            raise KernelError(
                "ACTION_TARGET", "only designated target can issue receipts"
            )
        if head is None or head not in cause_refs:
            raise KernelError("ACTION_HEAD", "decision must cite current receipt head")
        dispatch = self.dispatches.get(id if message_id is None else message_id)
        if (
            dispatch is None
            or dispatch.recipient != partition
            or dispatch.dispatch_ref not in cause_refs
        ):
            raise KernelError(
                "ACTION_DISPATCH", "decision must cite actual original/cancel dispatch"
            )
        return state

    def _learn(self, authorized: tuple[Delivery, ...]) -> None:
        for delivery in authorized:
            if (
                delivery.message.kind != "event"
                and delivery.message.id not in self.dispatches
            ):
                self.record_dispatch(delivery)

    def receipt(
        self,
        partition: str,
        op: Receipt,
        ref: ItemRef,
        authorized: tuple[Delivery, ...],
        cause_refs: tuple[ItemRef, ...],
    ) -> dict[str, Any]:
        """Validate one ordered target transition; terminal states cannot transition."""
        self._learn(authorized)
        state = self.action(op.command_id)
        if state.status == "canceling":
            if (
                state.target != partition
                or state.head not in cause_refs
                or self.cancel_heads.get(op.command_id) != state.head
            ):
                raise KernelError(
                    "ACTION_CANCEL_HEAD",
                    "completion must cite accepted cancel decision",
                )
        else:
            self._authorized(partition, op.command_id, state.head, cause_refs)
        if (
            op.status not in TRANSITIONS.get(state.status, set())
            or op.status == "canceling"
        ):
            raise KernelError("ACTION_TRANSITION", "illegal receipt transition")
        descriptor = self.registry.message(self.schemas[op.command_id])
        if op.result is not None or (
            op.status == "succeeded" and descriptor.result_schema is not None
        ):
            if descriptor.result_schema is None:
                raise KernelError("ACTION_RESULT", "command has no result schema")
            self.registry.validate(
                descriptor.result_schema, op.result, budget=self.budget
            )
        result = normalize(op.result, self.budget)
        item = {
            "kind": "receipt",
            "command_id": op.command_id,
            "status": op.status,
            "ref": _ref(ref),
            "origin": _ref(state.origin),
            "result": result,
        }
        self.states[op.command_id] = replace(
            state, status=op.status, head=ref, history=state.history + (item,)
        )
        return item

    def feedback(
        self,
        partition: str,
        op: Feedback,
        ref: ItemRef,
        authorized: tuple[Delivery, ...],
        cause_refs: tuple[ItemRef, ...],
    ) -> dict[str, Any]:
        """Feedback is typed progress and preserves the current receipt head."""
        self._learn(authorized)
        state = self.action(op.command_id)
        self._authorized(partition, op.command_id, state.head, cause_refs)
        if state.status not in {"executing", "canceling"}:
            raise KernelError(
                "ACTION_FEEDBACK", "feedback requires executing/canceling"
            )
        schema = self.registry.message(self.schemas[op.command_id]).feedback_schema
        if schema is None:
            raise KernelError("ACTION_FEEDBACK_SCHEMA", "no declared feedback schema")
        self.registry.validate(schema, op.payload, budget=self.budget)
        item = {
            "kind": "feedback",
            "command_id": op.command_id,
            "status": state.status,
            "ref": _ref(ref),
            "origin": _ref(state.origin),
            "payload": normalize(op.payload, self.budget),
        }
        self.states[op.command_id] = replace(state, history=state.history + (item,))
        return item

    def cancel_decision(
        self,
        partition: str,
        op: CancelDecision,
        ref: ItemRef,
        authorized: tuple[Delivery, ...],
        cause_refs: tuple[ItemRef, ...],
    ) -> dict[str, Any]:
        """Record target acceptance or rejection of a dispatched cancel."""
        self._learn(authorized)
        state = self.action(op.command_id)
        self._authorized(
            partition, op.command_id, state.head, cause_refs, op.cancel_message_id
        )
        cancel = self.dispatches[op.cancel_message_id].message
        if (
            cancel.kind != "cancel"
            or thaw(cancel.payload) != {"command_id": op.command_id}
            or op.cancel_message_id in self.decisions
            or type(op.accepted) is not bool
        ):
            raise KernelError("CANCEL_DECISION", "invalid or repeated cancel decision")
        if op.accepted and state.status not in {"accepted", "executing"}:
            raise KernelError(
                "CANCEL_STATUS", "completion/canceling excludes cancellation acceptance"
            )
        if op.reason is not None:
            validate_text(op.reason)
        status = "canceling" if op.accepted else state.status
        item = {
            "kind": "cancel_decision",
            "command_id": op.command_id,
            "cancel_message_id": op.cancel_message_id,
            "accepted": op.accepted,
            "reason": op.reason,
            "status": status,
            "ref": _ref(ref),
            "origin": _ref(state.origin),
        }
        self.decisions.add(op.cancel_message_id)
        self.states[op.command_id] = replace(
            state,
            status=status,
            head=ref if op.accepted else state.head,
            history=state.history + (item,),
        )
        if op.accepted:
            self.cancel_heads[op.command_id] = ref
        return item

    def to_data(self) -> dict[str, Any]:
        """Detached complete action indexes for diagnostic equality."""
        from .codec import encode

        return {
            "states": {k: encode(v) for k, v in self.states.items()},
            "schemas": self.schemas.copy(),
            "dispatches": {k: encode(v) for k, v in self.dispatches.items()},
            "cancel_heads": {k: encode(v) for k, v in self.cancel_heads.items()},
            "decisions": sorted(self.decisions),
        }

    @classmethod
    def from_data(cls, registry: MemoryRegistry, data: dict[str, Any]) -> Actions:
        """Restore diagnostic data; replay validates semantic records."""
        from .codec import decode_record

        result = cls(registry)
        result.states = {k: decode_record(v) for k, v in data["states"].items()}
        result.schemas = data["schemas"].copy()
        result.dispatches = {k: decode_record(v) for k, v in data["dispatches"].items()}
        result.cancel_heads = {
            k: decode_record(v) for k, v in data["cancel_heads"].items()
        }
        result.decisions = set(data["decisions"])
        return result
