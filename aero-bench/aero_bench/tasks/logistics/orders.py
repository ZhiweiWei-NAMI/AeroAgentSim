"""Strict logistics order lifecycle for the AERO_BENCH authoring/planning domain.

Stage 3 defines the immutable order lifecycle that Stage 4 planning algorithms
and providers build on. This module is deliberately an authoring/planning domain
component, not a Provider: it never fabricates telemetry, delivery evidence, or
Run IDs. Pickup and delivery confirmations bind an explicit external
observation/evidence reference supplied by the transition actor; whether that
reference corresponds to a real sealed artifact is a Stage 4 provider/verifier
concern and is not asserted here.

Hub-mediated routing (current frontend ``city-selected-logistics`` v2):

* Every order carries a dispatch-visible canonical hub (``hub_handoff_facility_id``)
  resolved at authoring time against an explicitly supplied facility catalog. An
  endpoint may itself be a hub (a hub endpoint is its own handoff); otherwise the
  hub is a declared midpoint. A Vertiport-to-Vertiport goods bypass is rejected
  during authoring lowering, never silently inferred here or by a consumer.
* The lifecycle exposes an immutable ``OrderItinerary`` (source -> hub ->
  destination with consecutive duplicates removed) so a future provider binds
  its own routing/evidence to explicit legs and the hub handoff boundary. The
  domain never fabricates trajectories or provider success.
* A final delivery is only reachable after an observed hub handoff: the
  ``handoff`` transition must name the canonical hub and bind an external
  hub-observation evidence reference. The delivered state retains the external
  handoff evidence and the exact data version at which it was recorded
  (``handoff_evidence_ref`` / ``hub_handoff_version``), so the authoritative
  "required hub" data version is never dropped before delivery.

Authority rules remain intact: every mandatory field is derived from a filed,
authoritative Signed-out authoring document, and pickup/handoff/delivery
confirmations still require explicit external evidence references that can never
be the transition's own command receipt.

Design choices (documented for consumers):

* All logistics time quantities are float seconds. The order request names
  ``release_time_s`` and ``deadline_s`` in seconds, so transitions use the same
  unit rather than ``SimulationTime``. Monotonic time is enforced per order and
  no transition may precede the order release time.
* Assignment is carried explicitly: the dispatcher offers a specific aircraft
  (``assignee_id``), that aircraft accepts, then the dispatcher assigns with a
  declared ``capacity_kg``. The pure function never infers a path or battery
  state; capacity is the only physical feasibility check and is declared at
  assignment time.
* Every transition is an immutable ``OrderTransition`` carrying a stable
  ``transition_id``, the actor id/role that issued it, the expected order
  version (optimistic concurrency), the effective assignee, and per-event
  payload (capacity, evidence reference, handoff facility, reason).
* Recency/retention: the ledger hash chain retains every data version (nothing
  is pruned before the current version, because pruning would break hash
  integrity), and optimistic concurrency means only the current recorded version
  is writable; a transition naming any held-back earlier version is rejected.
"""

from __future__ import annotations

import hashlib
import math
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Sha256, StrictModel
from aero_bench.serialization import canonical_json_bytes


OrderEvent: TypeAlias = Literal[
    "offer",
    "accept",
    "reject",
    "assign",
    "pick_up",
    "handoff",
    "deliver",
    "cancel",
    "fail",
]

OrderActorRole: TypeAlias = Literal["business", "dispatcher", "aircraft_agent"]
LogisticsIdentifier: TypeAlias = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


def _finite_positive(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _finite_nonnegative(value: float, name: str) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


class OrderRequest(StrictModel):
    """Immutable authoring request for one logistics order.

    Required fields: order id, origin and destination facility ids (different),
    the canonical hub through which the parcel must be handed off, cargo mass in
    kilograms, release time in seconds, and a deadline in seconds that strictly
    follows the release time.

    ``hub_handoff_facility_id`` is the dispatch-visible "required hub" resolved
    at authoring time against the explicitly supplied facility catalog. It may
    equal an endpoint when that endpoint is itself a hub (a hub endpoint is its
    own handoff); otherwise it names a distinct hub midpoint. The lowering (not
    this model) rejects the Vertiport-to-Vertiport goods bypass; this model only
    asserts identifier/measure invariants.
    """

    order_id: LogisticsIdentifier
    origin_facility_id: LogisticsIdentifier
    destination_facility_id: LogisticsIdentifier
    hub_handoff_facility_id: LogisticsIdentifier
    cargo_mass_kg: float
    release_time_s: float
    deadline_s: float

    @field_validator("cargo_mass_kg", mode="before")
    @classmethod
    def cargo_mass_is_numeric(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("cargo_mass_kg must be a finite number")
        return value

    @field_validator("release_time_s", mode="before")
    @classmethod
    def release_time_is_numeric(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("release_time_s must be a finite number")
        return value

    @field_validator("deadline_s", mode="before")
    @classmethod
    def deadline_is_numeric(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("deadline_s must be a finite number")
        return value

    @field_validator("cargo_mass_kg")
    @classmethod
    def positive_mass(cls, value: float) -> float:
        return _finite_positive(value, "cargo_mass_kg")

    @field_validator("release_time_s")
    @classmethod
    def nonnegative_release(cls, value: float) -> float:
        return _finite_nonnegative(value, "release_time_s")

    @field_validator("deadline_s")
    @classmethod
    def positive_deadline(cls, value: float) -> float:
        return _finite_positive(value, "deadline_s")

    @model_validator(mode="after")
    def order_contract_is_consistent(self) -> "OrderRequest":
        if self.origin_facility_id == self.destination_facility_id:
            raise ValueError(
                "order origin and destination facilities must be different"
            )
        if self.deadline_s <= self.release_time_s:
            raise ValueError("order deadline must follow the release time")
        return self

    def initial_state(self) -> "OrderState":
        """The actionable order begins at its release time with no assignee."""
        return OrderState(
            order_id=self.order_id,
            origin_facility_id=self.origin_facility_id,
            destination_facility_id=self.destination_facility_id,
            hub_handoff_facility_id=self.hub_handoff_facility_id,
            cargo_mass_kg=self.cargo_mass_kg,
            release_time_s=self.release_time_s,
            deadline_s=self.deadline_s,
            last_time_s=self.release_time_s,
        )

    def itinerary(self) -> "OrderItinerary":
        """Immutable source -> hub -> destination legs (hub endpoints collapse)."""
        return build_order_itinerary(self)


class OrderStatus(str, Enum):
    CREATED = "created"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    ASSIGNED = "assigned"
    PICKED_UP = "picked_up"
    HANDED_OFF = "handed_off"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderActorGrant(StrictModel):
    """Declaration that an actor id may act under one logistics role."""

    actor_id: LogisticsIdentifier
    role: OrderActorRole


#: Which roles may issue each transition event.
_EVENT_ROLES: dict[str, tuple[OrderActorRole, ...]] = {
    "offer": ("dispatcher",),
    "assign": ("dispatcher",),
    "accept": ("aircraft_agent",),
    "reject": ("aircraft_agent",),
    "pick_up": ("aircraft_agent",),
    "handoff": ("aircraft_agent",),
    "deliver": ("aircraft_agent",),
    "cancel": ("business",),
    "fail": ("dispatcher", "business"),
}

#: Transitions that must name the effective assignee (the aircraft involved).
_ASSIGNEE_REQUIRED_EVENTS = frozenset(
    {"offer", "accept", "assign", "reject", "pick_up", "handoff", "deliver", "fail"}
)

#: Transitions that bind an explicit external observation/evidence reference.
_EVIDENCE_EVENTS = frozenset({"pick_up", "handoff", "deliver"})

#: Transitions that require a failure/cancellation reason.
_REASON_EVENTS = frozenset({"reject", "cancel", "fail"})


class OrderTransition(StrictModel):
    """One immutable lifecycle transition on a logistics order.

    The transition carries the stable order id and its own stable transition id,
    the actor id/role, monotonic transition time in seconds, the expected order
    version for optimistic concurrency, and the effective assignee. Pickup,
    hub handoff and delivery bind an explicit external evidence reference that
    can never simply be the transition's own command-receipt id. The ``handoff``
    transition additionally names the facility at which the hub observation was
    bound; the lifecycle requires it to be the order's canonical hub, so a handoff
    recorded at any other facility is rejected rather than trusted from an ID
    string.
    """

    order_id: LogisticsIdentifier
    transition_id: LogisticsIdentifier
    event: OrderEvent
    actor_id: LogisticsIdentifier
    actor_role: OrderActorRole
    time_s: float
    expected_version: Annotated[int, Field(ge=0)]
    assignee_id: LogisticsIdentifier | None = None
    capacity_kg: float | None = None
    evidence_ref: LogisticsIdentifier | None = None
    handoff_facility_id: LogisticsIdentifier | None = None
    reason: LogisticsIdentifier | None = None

    @field_validator("time_s")
    @classmethod
    def nonnegative_time(cls, value: float) -> float:
        return _finite_nonnegative(value, "time_s")

    @field_validator("capacity_kg")
    @classmethod
    def positive_capacity(cls, value: float | None) -> float | None:
        if value is None:
            return value
        return _finite_positive(value, "capacity_kg")

    @model_validator(mode="after")
    def transition_fields_are_consistent(self) -> "OrderTransition":
        if self.actor_role not in _EVENT_ROLES[self.event]:
            raise ValueError(
                f"{self.event} cannot be issued by {self.actor_role}"
            )
        if self.event in _ASSIGNEE_REQUIRED_EVENTS and self.assignee_id is None:
            raise ValueError(f"{self.event} requires the effective assignee")
        if self.event == "assign":
            if self.capacity_kg is None:
                raise ValueError("assign requires a declared cargo capacity")
        elif self.capacity_kg is not None:
            raise ValueError(f"{self.event} cannot carry a capacity payload")
        if self.event == "handoff" and self.handoff_facility_id is None:
            raise ValueError("handoff requires the canonical hub facility")
        if self.event != "handoff" and self.handoff_facility_id is not None:
            raise ValueError(f"{self.event} cannot carry a handoff facility")
        if self.event in _EVIDENCE_EVENTS:
            if self.evidence_ref is None:
                raise ValueError(
                    f"{self.event} requires an external evidence reference"
                )
            if self.evidence_ref == self.transition_id:
                raise ValueError(
                    "evidence reference cannot be the transition's own "
                    "command receipt"
                )
        elif self.evidence_ref is not None:
            raise ValueError(f"{self.event} cannot carry an evidence reference")
        if self.event in _REASON_EVENTS and self.reason is None:
            raise ValueError(f"{self.event} requires a reason")
        if self.event not in _REASON_EVENTS and self.reason is not None:
            raise ValueError(f"{self.event} cannot carry a reason")
        return self


_TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.DELIVERED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    }
)


class OrderState(StrictModel):
    """Immutable per-order lifecycle state.

    ``last_time_s`` tracks the most recent transition time and is initialized to
    the release time, so no transition may precede release. ``assignee_id`` is
    the current aircraft once the order is offered; ``capacity_kg`` is the
    dispatcher-declared assignment capacity; ``evidence_ref`` is the external
    observation/evidence reference bound at pickup and delivery. The canonical
    hub (``hub_handoff_facility_id``) is retained across every data version;
    ``handoff_evidence_ref`` and ``hub_handoff_version`` retain the external hub
    observation and the exact data version at which the required hub handoff was
    recorded, so a delivered order always shows the retained hub handoff Data
    Version before the delivery version.
    """

    order_id: LogisticsIdentifier
    origin_facility_id: LogisticsIdentifier
    destination_facility_id: LogisticsIdentifier
    hub_handoff_facility_id: LogisticsIdentifier
    cargo_mass_kg: float
    release_time_s: float
    deadline_s: float
    status: OrderStatus = OrderStatus.CREATED
    version: Annotated[int, Field(ge=0)] = 0
    last_time_s: float
    assignee_id: LogisticsIdentifier | None = None
    capacity_kg: float | None = None
    evidence_ref: LogisticsIdentifier | None = None
    handoff_evidence_ref: LogisticsIdentifier | None = None
    hub_handoff_version: Annotated[int, Field(ge=1)] | None = None
    failure_reason: LogisticsIdentifier | None = None

    @field_validator("last_time_s")
    @classmethod
    def nonnegative_last_time(cls, value: float) -> float:
        return _finite_nonnegative(value, "last_time_s")

    @model_validator(mode="after")
    def order_state_invariants(self) -> "OrderState":
        _finite_positive(self.cargo_mass_kg, "cargo_mass_kg")
        _finite_nonnegative(self.release_time_s, "release_time_s")
        _finite_positive(self.deadline_s, "deadline_s")
        if self.origin_facility_id == self.destination_facility_id:
            raise ValueError("order origin and destination facilities must be different")
        if self.deadline_s <= self.release_time_s:
            raise ValueError("order deadline must follow the release time")
        if self.status is OrderStatus.CREATED:
            if self.version != 0:
                raise ValueError("created order must have version zero")
            if any(
                value is not None
                for value in (
                    self.assignee_id,
                    self.capacity_kg,
                    self.evidence_ref,
                    self.handoff_evidence_ref,
                    self.hub_handoff_version,
                    self.failure_reason,
                )
            ):
                raise ValueError("created order cannot carry execution state")
        elif self.version == 0:
            raise ValueError("non-created order must have a positive version")
        if self.status in {
            OrderStatus.OFFERED,
            OrderStatus.ACCEPTED,
            OrderStatus.ASSIGNED,
            OrderStatus.PICKED_UP,
            OrderStatus.HANDED_OFF,
            OrderStatus.DELIVERED,
        } and self.assignee_id is None:
            raise ValueError("active order must carry an assignee")
        if self.status in {OrderStatus.OFFERED, OrderStatus.ACCEPTED} and any(
            value is not None
            for value in (
                self.capacity_kg,
                self.evidence_ref,
                self.handoff_evidence_ref,
                self.hub_handoff_version,
                self.failure_reason,
            )
        ):
            raise ValueError("offer-state cannot carry capacity, evidence, or failure")
        if self.status is OrderStatus.ASSIGNED and (
            self.capacity_kg is None
            or self.evidence_ref is not None
            or self.handoff_evidence_ref is not None
            or self.hub_handoff_version is not None
            or self.failure_reason is not None
        ):
            raise ValueError("assigned order must carry capacity without evidence")
        if self.status is OrderStatus.PICKED_UP and (
            self.capacity_kg is None
            or self.evidence_ref is None
            or self.handoff_evidence_ref is not None
            or self.hub_handoff_version is not None
            or self.failure_reason is not None
        ):
            raise ValueError("picked-up order must carry capacity and pickup evidence")
        if self.status is OrderStatus.HANDED_OFF and (
            self.capacity_kg is None
            or self.evidence_ref is None
            or self.handoff_evidence_ref is None
            or self.hub_handoff_version is None
            or self.failure_reason is not None
        ):
            raise ValueError(
                "handed-off order must carry capacity, pickup and hub handoff evidence"
            )
        if self.status is OrderStatus.DELIVERED and (
            self.capacity_kg is None
            or self.evidence_ref is None
            or self.handoff_evidence_ref is None
            or self.hub_handoff_version is None
            or self.failure_reason is not None
        ):
            raise ValueError(
                "delivered order must retain capacity, pickup, hub handoff and delivery evidence"
            )
        if self.hub_handoff_version is not None:
            if self.status is OrderStatus.HANDED_OFF and self.hub_handoff_version != self.version:
                raise ValueError("handed-off order hub handoff version must match the recorded version")
            if self.status is OrderStatus.DELIVERED and self.hub_handoff_version >= self.version:
                raise ValueError("delivered order must retain a hub handoff version before delivery")
        if self.status in {
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.FAILED,
        } and self.failure_reason is None:
            raise ValueError("terminal unsuccessful order requires a reason")
        if self.capacity_kg is not None and self.cargo_mass_kg > self.capacity_kg:
            raise ValueError("cargo mass cannot exceed the assigned capacity")
        if self.last_time_s < self.release_time_s:
            raise ValueError("order last time cannot precede the release time")
        return self

    def itinerary(self) -> "OrderItinerary":
        """Immutable source -> hub -> destination legs for the live state."""
        return build_order_itinerary(self)


def _time_before(left: float, right: float) -> bool:
    return left < right


def _next_status(status: OrderStatus, event: str) -> OrderStatus:
    """Return the successor status for a legal (status, event) pair."""
    if status is OrderStatus.CREATED:
        if event == "offer":
            return OrderStatus.OFFERED
        if event == "cancel":
            return OrderStatus.CANCELLED
    if status is OrderStatus.OFFERED:
        if event == "accept":
            return OrderStatus.ACCEPTED
        if event == "reject":
            return OrderStatus.REJECTED
        if event == "cancel":
            return OrderStatus.CANCELLED
        if event == "fail":
            return OrderStatus.FAILED
    if status is OrderStatus.ACCEPTED:
        if event == "assign":
            return OrderStatus.ASSIGNED
        if event == "cancel":
            return OrderStatus.CANCELLED
        if event == "fail":
            return OrderStatus.FAILED
    if status is OrderStatus.ASSIGNED:
        if event == "pick_up":
            return OrderStatus.PICKED_UP
        if event == "cancel":
            return OrderStatus.CANCELLED
        if event == "fail":
            return OrderStatus.FAILED
    if status is OrderStatus.PICKED_UP:
        if event == "handoff":
            return OrderStatus.HANDED_OFF
        if event == "fail":
            return OrderStatus.FAILED
    if status is OrderStatus.HANDED_OFF:
        if event == "deliver":
            return OrderStatus.DELIVERED
        if event == "fail":
            return OrderStatus.FAILED
    raise ValueError(f"invalid order transition: {status.value} -> {event}")


def _validate_actor(
    state: OrderState,
    transition: OrderTransition,
    actor_grants: tuple[OrderActorGrant, ...],
) -> None:
    grant = next(
        (item for item in actor_grants if item.actor_id == transition.actor_id),
        None,
    )
    if grant is None or grant.role != transition.actor_role:
        raise ValueError("actor id is not authorized for the declared actor role")
    expected_roles = _EVENT_ROLES[transition.event]
    if transition.actor_role not in expected_roles:
        raise ValueError(
            f"{transition.event} cannot be issued by {transition.actor_role}"
        )
    if (
        transition.event in {"accept", "reject", "pick_up", "handoff", "deliver"}
        and state.assignee_id != transition.actor_id
    ):
        raise ValueError(
            "only the assigned aircraft may advance an active logistics order"
        )
    if transition.event in {"offer", "assign"} and transition.assignee_id is not None:
        assignee_grant = next(
            (item for item in actor_grants if item.actor_id == transition.assignee_id),
            None,
        )
        if assignee_grant is None or assignee_grant.role != "aircraft_agent":
            raise ValueError("assignment target must be an authorized aircraft agent")


def _updated_state(state: OrderState, **updates: object) -> OrderState:
    values = state.model_dump()
    values.update(updates)
    return OrderState.model_validate(values)


def _validate_transition_state(
    state: OrderState,
    transition: OrderTransition,
) -> OrderStatus:
    if state.order_id != transition.order_id:
        raise ValueError("order transition targets another order")
    if state.status in _TERMINAL_STATUSES:
        raise ValueError("terminal order cannot be mutated")
    if transition.expected_version != state.version:
        raise ValueError(
            "expected_version does not match the current order version"
        )
    if _time_before(transition.time_s, state.last_time_s):
        raise ValueError("order transition time moved backwards")
    if _time_before(transition.time_s, state.release_time_s):
        raise ValueError("order transition precedes the release time")
    return _next_status(state.status, transition.event)


def _reduce_order(
    state: OrderState, transition: OrderTransition, next_status: OrderStatus
) -> OrderState:
    """Pure state reduction, also used to check ledger snapshot consistency."""
    if transition.assignee_id is not None:
        if (transition.event == "cancel" or state.assignee_id is not None) and transition.assignee_id != state.assignee_id:
            raise ValueError(
                "transition assignee does not match the current order assignee"
            )
    if transition.event in _EVIDENCE_EVENTS and transition.evidence_ref is None:
        raise ValueError(
            "pickup, hub handoff and delivery require external evidence reference"
        )
    if transition.event == "handoff":
        if transition.handoff_facility_id != state.hub_handoff_facility_id:
            raise ValueError(
                "hub handoff facility does not match the canonical hub"
            )
    if transition.event == "assign":
        if transition.capacity_kg is None:
            raise ValueError("assignment requires a declared capacity")
        if state.cargo_mass_kg > transition.capacity_kg:
            raise ValueError("cargo mass exceeds the assigned aircraft capacity")

    updates: dict[str, object] = {
        "status": next_status,
        "version": state.version + 1,
        "last_time_s": transition.time_s,
    }
    if transition.event == "offer":
        updates["assignee_id"] = transition.assignee_id
    elif transition.event == "reject":
        updates["failure_reason"] = transition.reason
    elif transition.event == "assign":
        updates["capacity_kg"] = transition.capacity_kg
    elif transition.event == "handoff":
        updates["handoff_evidence_ref"] = transition.evidence_ref
        updates["hub_handoff_version"] = state.version + 1
    elif transition.event in _EVIDENCE_EVENTS:
        updates["evidence_ref"] = transition.evidence_ref
    elif transition.event in {"cancel", "fail"}:
        updates["failure_reason"] = transition.reason
    return _updated_state(state, **updates)


def advance_order(
    state: OrderState,
    transition: OrderTransition,
    *,
    actor_grants: tuple[OrderActorGrant, ...],
) -> OrderState:
    """Apply one legal, authorized transition; evidence remains externally verified."""
    next_status = _validate_transition_state(state, transition)
    _validate_actor(state, transition, actor_grants)
    return _reduce_order(state, transition, next_status)


def _history_record_hash(
    *,
    sequence: int,
    transition: OrderTransition,
    previous_hash: str,
) -> str:
    body = {
        "sequence": sequence,
        "transition": transition.model_dump(mode="json"),
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _make_history_record(
    *,
    history: tuple["LogisticsHistoryRecord", ...],
    transition: OrderTransition,
) -> "LogisticsHistoryRecord":
    previous_hash = history[-1].record_hash if history else "0" * 64
    sequence = len(history)
    return LogisticsHistoryRecord(
        sequence=sequence,
        transition=transition,
        previous_hash=previous_hash,
        record_hash=_history_record_hash(
            sequence=sequence,
            transition=transition,
            previous_hash=previous_hash,
        ),
    )


def _validate_history_chain(
    history: tuple["LogisticsHistoryRecord", ...],
) -> None:
    previous_hash = "0" * 64
    for sequence, record in enumerate(history):
        if record.sequence != sequence:
            raise ValueError("logistics history sequence is not contiguous")
        if record.previous_hash != previous_hash:
            raise ValueError("logistics history hash chain is broken")
        expected_hash = _history_record_hash(
            sequence=record.sequence,
            transition=record.transition,
            previous_hash=record.previous_hash,
        )
        if record.record_hash != expected_hash:
            raise ValueError("logistics history record hash is invalid")
        previous_hash = record.record_hash


class LogisticsHistoryRecord(StrictModel):
    """Hash-chained ledger entry preserving one immutable transition."""

    sequence: Annotated[int, Field(ge=0)]
    transition: OrderTransition
    previous_hash: Sha256
    record_hash: Sha256


class LogisticsTransitionResult(StrictModel):
    transition: OrderTransition
    order: OrderState
    history_record: LogisticsHistoryRecord


class LogisticsLedgerState(StrictModel):
    """Immutable aggregate of orders and their transition record."""

    orders: tuple[OrderState, ...] = ()
    version: Annotated[int, Field(ge=0)] = 0
    processed_transition_ids: tuple[LogisticsIdentifier, ...] = ()
    history: tuple[LogisticsHistoryRecord, ...] = ()

    @model_validator(mode="after")
    def ledger_is_consistent(self) -> "LogisticsLedgerState":
        order_ids = [order.order_id for order in self.orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("ledger order ids must be unique")
        if len(self.processed_transition_ids) != len(
            set(self.processed_transition_ids)
        ):
            raise ValueError("processed transition ids must be unique")
        _validate_history_chain(self.history)
        if self.version != len(self.history):
            raise ValueError("ledger version must equal the history length")
        history_ids = tuple(
            record.transition.transition_id for record in self.history
        )
        if self.processed_transition_ids != history_ids:
            raise ValueError(
                "processed transition ids must match the history chain"
            )
        # A valid hash chain does not establish that the supplied snapshots are
        # its result. Replay the state reductions here. Actor grants and request
        # provenance still require replay_logistics_history with external inputs.
        replayed = {
            order.order_id: OrderRequest(
                order_id=order.order_id,
                origin_facility_id=order.origin_facility_id,
                destination_facility_id=order.destination_facility_id,
                hub_handoff_facility_id=order.hub_handoff_facility_id,
                cargo_mass_kg=order.cargo_mass_kg,
                release_time_s=order.release_time_s,
                deadline_s=order.deadline_s,
            ).initial_state()
            for order in self.orders
        }
        for record in self.history:
            transition = record.transition
            state = replayed.get(transition.order_id)
            if state is None:
                raise ValueError("logistics history names an absent order")
            next_status = _validate_transition_state(state, transition)
            replayed[transition.order_id] = _reduce_order(state, transition, next_status)
        if tuple(replayed[order.order_id] for order in self.orders) != self.orders:
            raise ValueError("logistics order snapshots differ from replayed history")
        return self


class OrderItineraryLeg(StrictModel):
    """One immutable network leg of a hub-mediated order itinerary.

    A leg carries only facility identities and its role; timing, energy, distance
    and evidence are provider/planning concerns and are never fabricated here.
    The hub boundary is explicit: a ``source_to_hub`` leg ends at the canonical
    hub and a ``hub_to_destination`` leg starts there, so a future provider can
    bind pickup evidence to the first leg, the observed hub handoff to the
    canonical hub, and delivery evidence to the last leg.
    """

    sequence: Annotated[int, Field(ge=0)]
    from_facility_id: LogisticsIdentifier
    to_facility_id: LogisticsIdentifier
    role: Literal["source_to_hub", "hub_to_destination"]


class OrderItinerary(StrictModel):
    """Immutable hub-mediated itinerary for one order.

    The parcel is handed off at ``canonical_hub_facility_id`` and may only be
    delivered after the required observed hub handoff. ``legs`` are ordered
    source -> hub -> destination with consecutive duplicates removed; a hub
    endpoint collapses the adjacent leg. This is the planning face of the
    dispatch-visible "required hub": the same hub the dispatch resource routes on.
    """

    order_id: LogisticsIdentifier
    canonical_hub_facility_id: LogisticsIdentifier
    legs: tuple[OrderItineraryLeg, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def itinerary_is_consistent(self) -> "OrderItinerary":
        if tuple(leg.sequence for leg in self.legs) != tuple(range(len(self.legs))):
            raise ValueError("itinerary leg sequences must be contiguous")
        for leg in self.legs:
            if leg.from_facility_id == leg.to_facility_id:
                raise ValueError("itinerary leg must connect distinct facilities")
            if leg.role == "source_to_hub" and leg.to_facility_id != self.canonical_hub_facility_id:
                raise ValueError("source_to_hub itinerary leg must end at the canonical hub")
            if leg.role == "hub_to_destination" and leg.from_facility_id != self.canonical_hub_facility_id:
                raise ValueError("hub_to_destination itinerary leg must start at the canonical hub")
        for left, right in zip(self.legs, self.legs[1:]):
            if left.to_facility_id != right.from_facility_id:
                raise ValueError("itinerary legs must be contiguous at the hub")
        return self


def build_order_itinerary(order: OrderRequest | OrderState) -> OrderItinerary:
    """Derive immutable legs for one hub-mediated order.

    The parcel path is source -> canonical hub -> destination with consecutive
    duplicate facility ids removed (a hub endpoint collapses the adjacent leg).
    The canonical hub is always the handoff boundary and is never silently
    inferred here.
    """
    hub = order.hub_handoff_facility_id
    legs: list[OrderItineraryLeg] = []
    if hub != order.origin_facility_id:
        legs.append(
            OrderItineraryLeg(
                sequence=len(legs),
                from_facility_id=order.origin_facility_id,
                to_facility_id=hub,
                role="source_to_hub",
            )
        )
    if hub != order.destination_facility_id:
        legs.append(
            OrderItineraryLeg(
                sequence=len(legs),
                from_facility_id=hub,
                to_facility_id=order.destination_facility_id,
                role="hub_to_destination",
            )
        )
    return OrderItinerary(
        order_id=order.order_id,
        canonical_hub_facility_id=hub,
        legs=tuple(legs),
    )


class LogisticsOrdersService:
    """Order lifecycle service over a small ledger.

    This is an authoring/planning helper, not a provider. It rejects duplicate
    transition ids, delegates state transitions to the pure
    :func:`advance_order`, and appends hash-chained history records for later
    Stage 4 evidence binding.
    """

    def __init__(
        self,
        requests: tuple[OrderRequest, ...],
        *,
        actor_grants: tuple[OrderActorGrant, ...],
    ) -> None:
        request_ids = [request.order_id for request in requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("logistics order request ids must be unique")
        grant_ids = [grant.actor_id for grant in actor_grants]
        if len(grant_ids) != len(set(grant_ids)):
            raise ValueError("logistics actor grant ids must be unique")
        self._orders = tuple(request.initial_state() for request in requests)
        self._actor_grants = actor_grants
        self._history: tuple[LogisticsHistoryRecord, ...] = ()

    @property
    def ledger(self) -> LogisticsLedgerState:
        return LogisticsLedgerState(
            orders=self._orders,
            version=len(self._history),
            processed_transition_ids=tuple(
                record.transition.transition_id for record in self._history
            ),
            history=self._history,
        )

    def apply(self, transition: OrderTransition) -> LogisticsTransitionResult:
        if any(
            transition.transition_id == record.transition.transition_id
            for record in self._history
        ):
            raise ValueError("transition_id was already processed")
        index = next(
            (
                index
                for index, order in enumerate(self._orders)
                if order.order_id == transition.order_id
            ),
            None,
        )
        if index is None:
            raise ValueError(f"unknown order: {transition.order_id}")
        next_order = advance_order(
            self._orders[index],
            transition,
            actor_grants=self._actor_grants,
        )
        record = _make_history_record(
            history=self._history,
            transition=transition,
        )
        self._orders = (
            *self._orders[:index],
            next_order,
            *self._orders[index + 1 :],
        )
        self._history = (*self._history, record)
        return LogisticsTransitionResult(
            transition=transition,
            order=next_order,
            history_record=record,
        )


def replay_logistics_history(
    requests: tuple[OrderRequest, ...],
    *,
    actor_grants: tuple[OrderActorGrant, ...],
    history: tuple[LogisticsHistoryRecord, ...],
) -> LogisticsLedgerState:
    """Replay the authoritative history and compare every transition."""
    _validate_history_chain(history)
    service = LogisticsOrdersService(requests, actor_grants=actor_grants)
    for record in history:
        result = service.apply(record.transition)
        if result.history_record != record:
            raise ValueError("logistics history transition does not replay")
    return service.ledger


__all__ = [
    "LogisticsHistoryRecord",
    "LogisticsLedgerState",
    "LogisticsOrdersService",
    "LogisticsTransitionResult",
    "OrderActorGrant",
    "OrderActorRole",
    "OrderEvent",
    "OrderItinerary",
    "OrderItineraryLeg",
    "OrderRequest",
    "OrderState",
    "OrderStatus",
    "OrderTransition",
    "advance_order",
    "build_order_itinerary",
    "replay_logistics_history",
]
