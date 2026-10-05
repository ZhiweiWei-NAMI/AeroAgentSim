"""Bounded deterministic order-arrival/event layer for a future logistics Provider.

The concrete gap addressed here: :class:`LogisticsOrdersService` is constructed
with a *fixed* set of ``OrderRequest`` values and has no dynamic arrival/create
API, yet a real logistics business Provider must be able to make orders become
available *during a run* and to record *newly submitted* orders.  This module is
the authoritative **business simulation input** for that gap; it is not physical
telemetry, not a fake flight, and it never claims actual transportation.
Physical pickup/handoff/delivery evidence stays exactly where the canonical
lifecycle puts it (``advance_order``) and is never fabricated here.

Native clock (no float authority):

* Commands, arrival-journal events, the snapshot and the clock-advance API all
  carry the canonical ``SimulationTime`` (integer ``tick`` + integer
  ``sim_time_ns``) from ``aero_bench.runtime.contracts``.  There is no float
  clock authority and no accumulated-seconds comparison anywhere in this module.
  The ``OrderRequest``/``OrderTransition`` float seconds are *domain* values
  that stay bound to the explicit native clock: the declared release gate is
  converted conservatively so it never releases earlier than the declared
  domain seconds (exact-decimal ceiling to integer nanoseconds), a lifecycle
  transition must bind exactly to the current harness time
  (``sim_time_ns / 1_000_000_000``), never to an earlier number, and native
  time never regresses in a single component -- clock advance, journal ordering
  and current-time checks reject a later tick with an earlier nanosecond (or
  vice versa) exactly like the canonical runtime ``EventLedger`` ordering.

One append-only causal journal:

* The arrival journal is the single sequence for **creation**, **one-time
  release** and **lifecycle application**.  Lifecycle application is recorded as
  a typed marker carrying the canonical ``LogisticsHistoryRecord`` returned by
  the canonical lifecycle; the marker is hash-bound into the journal so the
  causal order *creation -> release -> lifecycle* is enforced even when several
  records share one native timestamp.  The canonical lifecycle rules are never
  redefined here; ``advance_order`` remains the sole authority over order
  states, the lifecycle ``LogisticsOrdersService`` is reconstructed (never
  mutated through private attributes), and the existing canonical lifecycle
  history bytes stay unchanged when new orders arrive.

Baseline binding:

* The reserved package authoring namespace (``arrival.package:*`` /
  ``logistics.package`` / ``logistics.package.baseline``) is exempt from the
  grant check only when it matches the caller-supplied authoritative
  ``initial_requests`` exactly (count, order, request content, reserved event
  ids and reserved source, all at the declared initial native time).  A live
  request or an actor grant can never claim that namespace, even when granted
  the ``business`` role; recomputed journal hashes are never treated as
  authority over that binding.

Availability gate (unchanged from the declared request semantics):

* ``OrderRequest.release_time_s`` remains the real availability gate against
  the explicit current native time.  A predeclared or online order with a future
  release gate is registered but **hidden** from the available view and rejects
  offer/assignment until the gate is passed.
* Release is deterministic (registration order at a tick) and exact one-time: an
  order is released at the first native time whose ``sim_time_ns`` reaches the
  declared gate and never again.
* Online submission may schedule a future release only by declaring it in the
  ``OrderRequest`` (``release_time_s`` not earlier than the submission time).
  Backdated/future-dated commands, retrospective availability timestamps,
  unknown/duplicate/replayed arrivals, and role forgery are rejected before any
  partial state change.

Validation on this path reuses the canonical domains only:

* ``resolve_canonical_hub`` from ``facility_catalog`` for facility existence,
  cargo-transfer legality, the mandatory hub (Vertiport-to-Vertiport goods
  bypass is refused), and hub storage;
* the actual declared fleet payload capacities from ``Fleet.expand_aircraft_units``;
* the canonical ``OrderActorGrant`` set for introduction authority (the
  ``business`` role).  A caller-declared ``actor_role`` is never trusted over the
  grant.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_CEILING, Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import FacilityCatalogue
from aero_bench.tasks.logistics.facility_catalog import resolve_canonical_hub
from aero_bench.tasks.logistics.fleet import Fleet
from aero_bench.tasks.logistics.orders import (
    LogisticsHistoryRecord,
    LogisticsIdentifier,
    LogisticsLedgerState,
    LogisticsOrdersService,
    LogisticsTransitionResult,
    OrderActorGrant,
    OrderActorRole,
    OrderRequest,
    OrderState,
    OrderTransition,
    replay_logistics_history,
)

#: The only actor role the current domain business authority grants for
#: introducing order requests. A caller-declared role is never trusted over a
#: canonical ``OrderActorGrant``.
ORDER_INTRODUCING_ROLE: Literal["business"] = "business"

#: Declared source identity written onto predeclared package orders at baseline.
ORDER_PACKAGE_SOURCE = "logistics.package"

#: Reserved introducing-actor id recorded on baseline package creation events.
#: Baseline package orders are benchmark authoring input, not a live actor
#: command, so this id is bound to the caller-declared ``initial_requests`` and
#: is never required to appear in the actor grants (nor may it).
ORDER_PACKAGE_ACTOR_ID = "logistics.package.baseline"

_NANOSECONDS_PER_SECOND = 1_000_000_000
_RELEASE_EVENT_ID_PREFIX = "arrival.release:"
_PACKAGE_EVENT_ID_PREFIX = "arrival.package:"
_LIFECYCLE_MARKER_ID_PREFIX = "arrival.lifecycle:"
_GENESIS_HASH = "0" * 64
_ZERO_TIME = SimulationTime(tick=0, sim_time_ns=0)


# ---------------------------------------------------------------------------
# Native clock helpers (integer ticks / nanoseconds are the only authority).
# ---------------------------------------------------------------------------


def _time_before(left: SimulationTime, right: SimulationTime) -> bool:
    """Strict native-clock partial order matching the canonical runtime ledger.

    ``left`` is before ``right`` when *either* component is strictly behind
    (``left.tick < right.tick`` or ``left.sim_time_ns < right.sim_time_ns``).
    Native time never regresses in a single component, so an instant that moves
    to a later tick but an earlier nanosecond (or vice versa) is still *before*
    the reference and is rejected by every clock-advance, journal-ordering and
    current-time comparison below, exactly like ``EventLedger._time_before`` in
    the canonical runtime. Same-time is never ``before`` (a no-op may remain).
    """
    return left.tick < right.tick or left.sim_time_ns < right.sim_time_ns


def _native_seconds(time: SimulationTime) -> float:
    """Derive the domain float seconds a native clock instant represents."""
    return time.sim_time_ns / _NANOSECONDS_PER_SECOND


def _release_gate_ns(request: OrderRequest) -> int:
    """Bind a declared float release gate to a conservative integer nanosecond.

    The conversion never releases earlier than the declared domain seconds: the
    exact decimal spelling of the float (``Decimal(str(release_time_s))``) is
    multiplied by 1e9 and rounded *up* to the next integer nanosecond.  The
    float request value is never normalized and no arbitrary maximum bound or
    compatibility float API is added.  A gate like ``0.1 + 0.2``
    (``0.30000000000000004`` s) binds to ``300000001`` ns -- never to the
    round-tripped ``300000000`` ns that would release the order before the
    declared gate -- while an exact ``0.3`` still binds to ``300000000`` ns and
    any positive sub-nanosecond gate binds to a positive native instant instead
    of becoming available at zero.
    """
    seconds = Decimal(str(request.release_time_s))
    return int(
        (seconds * _NANOSECONDS_PER_SECOND).to_integral_value(rounding=ROUND_CEILING)
    )


# ---------------------------------------------------------------------------
# Append-only journal hash chaining (creation + one-time release + lifecycle).
# ---------------------------------------------------------------------------


def _arrival_record_hash(
    *,
    sequence: int,
    kind: Literal["created", "released", "lifecycle"],
    event_id: str,
    order_id: str,
    time: SimulationTime,
    order: OrderRequest | None,
    source_identity: str | None,
    actor_id: str | None,
    lifecycle_record: LogisticsHistoryRecord | None,
    previous_hash: str,
) -> str:
    """Deterministic hash-chaining body for one arrival journal record."""
    body = {
        "sequence": sequence,
        "kind": kind,
        "event_id": event_id,
        "order_id": order_id,
        "time": time.model_dump(mode="json"),
        "order": None if order is None else order.model_dump(mode="json"),
        "source_identity": source_identity,
        "actor_id": actor_id,
        "lifecycle_record": (
            None
            if lifecycle_record is None
            else lifecycle_record.model_dump(mode="json")
        ),
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _make_arrival_event(
    events: tuple["OrderArrivalEvent", ...],
    *,
    kind: Literal["created", "released", "lifecycle"],
    event_id: str,
    order_id: str,
    time: SimulationTime,
    order: OrderRequest | None = None,
    source_identity: str | None = None,
    actor_id: str | None = None,
    lifecycle_record: LogisticsHistoryRecord | None = None,
) -> "OrderArrivalEvent":
    previous_hash = events[-1].record_hash if events else _GENESIS_HASH
    sequence = len(events)
    record_hash = _arrival_record_hash(
        sequence=sequence,
        kind=kind,
        event_id=event_id,
        order_id=order_id,
        time=time,
        order=order,
        source_identity=source_identity,
        actor_id=actor_id,
        lifecycle_record=lifecycle_record,
        previous_hash=previous_hash,
    )
    return OrderArrivalEvent(
        sequence=sequence,
        kind=kind,
        event_id=event_id,
        order_id=order_id,
        time=time,
        order=order,
        source_identity=source_identity,
        actor_id=actor_id,
        lifecycle_record=lifecycle_record,
        previous_hash=previous_hash,
        record_hash=record_hash,
    )


def _validate_arrival_journal(
    events: tuple["OrderArrivalEvent", ...],
    *,
    current_time: SimulationTime,
) -> None:
    """Validate the single causal journal: chain, time, and object causality.

    Enforces, in one pass over the append-only sequence: a hash-bound chain,
    contiguous sequence numbers, unique event ids, a non-decreasing native clock,
    no event beyond the snapshot current time, and the object causal order
    ``created -> released -> lifecycle`` per order (including same-timestamp
    operations, where sequence position is the tie-break).  Post-pass it checks
    each release against the declared nanosecond gate and rejects a due order
    whose one-time release record is missing.
    """
    previous_hash = _GENESIS_HASH
    event_ids: set[str] = set()
    previous_time: SimulationTime | None = None
    created_events: dict[str, "OrderArrivalEvent"] = {}
    released_order_ids: set[str] = set()
    released_events: dict[str, "OrderArrivalEvent"] = {}

    for sequence, event in enumerate(events):
        if event.sequence != sequence:
            raise ValueError("arrival journal sequence is not contiguous")
        if event.previous_hash != previous_hash:
            raise ValueError("arrival journal hash chain is broken")
        expected_hash = _arrival_record_hash(
            sequence=event.sequence,
            kind=event.kind,
            event_id=event.event_id,
            order_id=event.order_id,
            time=event.time,
            order=event.order,
            source_identity=event.source_identity,
            actor_id=event.actor_id,
            lifecycle_record=event.lifecycle_record,
            previous_hash=event.previous_hash,
        )
        if event.record_hash != expected_hash:
            raise ValueError("arrival journal record hash is invalid")
        previous_hash = event.record_hash

        if event.event_id in event_ids:
            raise ValueError("arrival journal event ids must be unique")
        event_ids.add(event.event_id)

        if event.kind == "released":
            if event.event_id != f"{_RELEASE_EVENT_ID_PREFIX}{event.order_id}":
                raise ValueError(
                    "released arrival event must use the reserved release event "
                    "id for its order"
                )
        elif event.kind == "lifecycle":
            if event.event_id != (
                f"{_LIFECYCLE_MARKER_ID_PREFIX}"
                f"{event.lifecycle_record.transition.transition_id}"
            ):
                raise ValueError(
                    "lifecycle arrival marker must use the reserved marker id "
                    "for its canonical transition"
                )
        else:
            if event.event_id.startswith(
                (_RELEASE_EVENT_ID_PREFIX, _LIFECYCLE_MARKER_ID_PREFIX)
            ):
                raise ValueError(
                    "created arrival event cannot use a non-creation reserved "
                    "event id namespace"
                )

        if _time_before(current_time, event.time):
            raise ValueError(
                "arrival journal records an event beyond the current simulation "
                "time"
            )
        if previous_time is not None and _time_before(event.time, previous_time):
            raise ValueError(
                "arrival journal events must be temporally ordered; a later "
                "record cannot move into earlier history"
            )
        previous_time = event.time

        if event.kind == "created":
            if event.order_id in created_events:
                raise ValueError("arrival journal creates a duplicate order")
            if event.order is None:
                raise ValueError(
                    "created arrival event must carry the full OrderRequest"
                )
            created_events[event.order_id] = event
        elif event.kind == "released":
            if event.order_id not in created_events:
                raise ValueError(
                    "arrival journal releases an unknown or not-yet-created order"
                )
            if event.order_id in released_order_ids:
                raise ValueError(
                    f"arrival journal releases order {event.order_id} more than once"
                )
            released_order_ids.add(event.order_id)
            released_events[event.order_id] = event
        else:
            if event.lifecycle_record is None:
                raise ValueError(
                    "lifecycle arrival marker must carry the canonical record"
                )
            if event.order_id not in created_events:
                raise ValueError(
                    "arrival journal lifecycle marker precedes the order creation"
                )
            if event.order_id not in released_order_ids:
                raise ValueError(
                    "arrival journal lifecycle marker precedes the one-time release"
                )
            if (
                _native_seconds(event.time)
                != event.lifecycle_record.transition.time_s
            ):
                raise ValueError(
                    "arrival journal lifecycle marker time does not bind exactly "
                    "to its canonical transition time"
                )

    for order_id, created in created_events.items():
        gate_ns = _release_gate_ns(created.order)
        release = released_events.get(order_id)
        if release is not None:
            if _time_before(release.time, created.time):
                raise ValueError(
                    "arrival journal cannot release an order before its creation"
                )
            if release.time.sim_time_ns < gate_ns:
                raise ValueError(
                    f"arrival journal releases order {order_id} before its "
                    "declared availability gate"
                )
        elif current_time.sim_time_ns >= gate_ns:
            raise ValueError(
                f"order {order_id} is due at the current simulation time but has "
                "no one-time release record"
            )


# ---------------------------------------------------------------------------
# Baseline binding and introduction authority (B2 closure).
# ---------------------------------------------------------------------------


def _is_baseline_styled(event: "OrderArrivalEvent") -> bool:
    """True when a created event claims any part of the reserved package namespace."""
    return (
        event.kind == "created"
        and (
            event.actor_id == ORDER_PACKAGE_ACTOR_ID
            or event.source_identity == ORDER_PACKAGE_SOURCE
            or event.event_id.startswith(_PACKAGE_EVENT_ID_PREFIX)
        )
    )


def _validate_actor_grants(actor_grants: tuple[OrderActorGrant, ...]) -> None:
    """Reject actor grants that claim the reserved baseline actor namespace."""
    for grant in actor_grants:
        if grant.actor_id == ORDER_PACKAGE_ACTOR_ID:
            raise ValueError(
                "actor grants cannot claim the reserved baseline actor id "
                f"{ORDER_PACKAGE_ACTOR_ID}"
            )


def _validate_baseline_binding(
    *,
    events: tuple["OrderArrivalEvent", ...],
    initial_requests: tuple[OrderRequest, ...],
    initial_time: SimulationTime,
) -> None:
    """Bind the reserved baseline namespace to the authoritative ``initial_requests``.

    Any created journal event that claims the reserved package source, reserved
    baseline actor, or the reserved ``arrival.package:*`` event-id namespace must
    belong to the caller-supplied ``initial_requests`` exactly: same count, same
    registration order, identical request content, reserved event id/source/actor,
    and every baseline record at the declared initial native time.  A live event
    styled as baseline, or a live event time before ``initial_time``, is
    rejected even when its journal hash chain is valid.
    """
    baseline = tuple(event for event in events if _is_baseline_styled(event))
    if len(baseline) != len(initial_requests):
        raise ValueError(
            "arrival journal baseline registrations do not match the declared "
            "initial_requests: journal has "
            f"{len(baseline)}, declared {len(initial_requests)}"
        )
    for event in events:
        if _time_before(event.time, initial_time):
            raise ValueError(
                "arrival journal records an event before the declared initial "
                "simulation time"
            )
    for event, request in zip(baseline, initial_requests, strict=True):
        expected_event_id = f"{_PACKAGE_EVENT_ID_PREFIX}{request.order_id}"
        if event.event_id != expected_event_id:
            raise ValueError(
                "baseline event id does not match the declared initial request "
                f"order id {request.order_id}"
            )
        if event.order_id != request.order_id:
            raise ValueError(
                "baseline event order id does not match the declared initial "
                "request"
            )
        if event.order != request:
            raise ValueError(
                "baseline journal order content does not match the declared "
                "initial request exactly"
            )
        if event.actor_id != ORDER_PACKAGE_ACTOR_ID:
            raise ValueError(
                "baseline journal event must use the reserved baseline actor id"
            )
        if event.source_identity != ORDER_PACKAGE_SOURCE:
            raise ValueError(
                "baseline journal event must use the reserved package source "
                "identity"
            )
        if event.time != initial_time:
            raise ValueError(
                "baseline journal event time does not match the declared initial "
                "simulation time"
            )


def _validate_introduction_authority(
    events: tuple["OrderArrivalEvent", ...],
    actor_grants: tuple[OrderActorGrant, ...],
) -> None:
    """Re-verify every live-created journal order was introduced by a grant.

    Baseline package registrations are bound to the authoritative
    ``initial_requests`` (see :func:`_validate_baseline_binding`), so they are
    skipped here.  Every other ``created`` event must name an actor holding an
    exact ``business`` grant; role forgery recorded in the journal is rejected
    here the same way the live ``submit`` path rejects it.
    """
    for event in events:
        if event.kind != "created" or _is_baseline_styled(event):
            continue
        grant = next(
            (grant for grant in actor_grants if grant.actor_id == event.actor_id),
            None,
        )
        if grant is None or grant.role != ORDER_INTRODUCING_ROLE:
            raise ValueError(
                "arrival journal creation is not backed by an authorized "
                f"business grant: {event.actor_id}"
            )


def validate_order_for_arrival(
    catalogue: FacilityCatalogue,
    fleet: Fleet,
    order: OrderRequest,
) -> None:
    """Re-validate one canonical order against the canonical logistics domains.

    Uses exactly the canonical catalogue/hub routing (``resolve_canonical_hub``,
    which rejects unknown/non-cargo-transfer endpoints, an unknown or non-hub
    named handoff, and the Vertiport-to-Vertiport goods bypass, and checks hub
    storage), the actual declared fleet payload capacities, and rejects with a
    visible error instead of any fallback. No facility coordinates or routes are
    invented here.
    """
    if not isinstance(catalogue, FacilityCatalogue):
        raise TypeError("arrival validation requires the canonical FacilityCatalogue")
    if not isinstance(fleet, Fleet):
        raise TypeError("arrival validation requires the canonical Fleet")
    if fleet.catalogue != catalogue:
        raise ValueError(
            "arrival fleet must be bound to the same facility catalogue as the "
            "arrival layer"
        )
    declared_hub = order.hub_handoff_facility_id
    input_hub = (
        None
        if declared_hub in {order.origin_facility_id, order.destination_facility_id}
        else declared_hub
    )
    canonical_hub = resolve_canonical_hub(
        catalogue,
        order_id=order.order_id,
        origin_facility_id=order.origin_facility_id,
        destination_facility_id=order.destination_facility_id,
        hub_handoff_facility_id=input_hub,
        cargo_mass_kg=order.cargo_mass_kg,
    )
    if canonical_hub != declared_hub:
        raise ValueError(
            f"order {order.order_id} canonical hub {canonical_hub} does not "
            f"match the declared hub {declared_hub}"
        )
    if not any(
        unit.max_payload_kg >= order.cargo_mass_kg
        for unit in fleet.expand_aircraft_units()
    ):
        raise ValueError(
            f"order {order.order_id} cargo {order.cargo_mass_kg} kg has no "
            "declared aircraft with sufficient payload capacity"
        )


class OrderArrivalEvent(StrictModel):
    """One append-only, hash-chained arrival journal record.

    ``created`` carries the full canonical ``OrderRequest`` content, the
    introducing actor, the declared source identity and the native creation time.
    ``released`` records the exact one-time availability translation (its native
    time and the bound order only).  ``lifecycle`` is a typed marker carrying the
    canonical ``LogisticsHistoryRecord`` produced by ``advance_order``; it only
    records the canonical lifecycle, never redefines it.
    """

    sequence: Annotated[int, Field(ge=0)]
    kind: Literal["created", "released", "lifecycle"]
    event_id: LogisticsIdentifier
    order_id: LogisticsIdentifier
    time: SimulationTime
    order: OrderRequest | None = None
    source_identity: str | None = None
    actor_id: LogisticsIdentifier | None = None
    lifecycle_record: LogisticsHistoryRecord | None = None
    previous_hash: Sha256
    record_hash: Sha256

    @field_validator("source_identity")
    @classmethod
    def source_is_nonempty(cls, value: str | None) -> str | None:
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError("source_identity must be a non-empty string")
        return value

    @model_validator(mode="after")
    def arrival_event_contract(self) -> "OrderArrivalEvent":
        if self.kind == "created":
            if self.order is None:
                raise ValueError(
                    "created arrival event must carry the full OrderRequest"
                )
            if self.order.order_id != self.order_id:
                raise ValueError(
                    "created arrival event order_id must match its OrderRequest"
                )
            if self.source_identity is None or self.actor_id is None:
                raise ValueError(
                    "created arrival event requires a source identity and the "
                    "introducing actor"
                )
            if self.lifecycle_record is not None:
                raise ValueError("created arrival event cannot carry a lifecycle record")
        elif self.kind == "released":
            if (
                self.order is not None
                or self.source_identity is not None
                or self.actor_id is not None
                or self.lifecycle_record is not None
            ):
                raise ValueError(
                    "released arrival event cannot carry creation or lifecycle "
                    "payload"
                )
        else:
            if self.lifecycle_record is None:
                raise ValueError(
                    "lifecycle arrival marker must carry the canonical lifecycle "
                    "record"
                )
            if self.order is not None or self.source_identity is not None or self.actor_id is not None:
                raise ValueError(
                    "lifecycle arrival marker cannot carry creation payload"
                )
            if self.lifecycle_record.transition.order_id != self.order_id:
                raise ValueError(
                    "lifecycle arrival marker order_id must match its canonical "
                    "transition"
                )
        return self


class OrderArrivalCommand(StrictModel):
    """Explicit online submission of one canonical OrderRequest at an exact instant.

    ``time`` is the canonical native clock the command binds exactly to (the
    service requires ``command.time ==`` its current native clock; a tick or
    nanosecond mismatch is rejected).  The declared ``source_identity`` is
    preserved verbatim in the arrival journal so a later deterministic order
    generator can feed this layer reproducibly.  The declared ``actor_role`` is
    never trusted over the canonical actor grant.
    """

    command_id: LogisticsIdentifier
    actor_id: LogisticsIdentifier
    actor_role: OrderActorRole
    time: SimulationTime
    source_identity: Annotated[str, Field(min_length=1)]
    order: OrderRequest


class OrderArrivalSnapshot(StrictModel):
    """Immutable aggregate state of the arrival layer plus the canonical ledger.

    ``events`` is the single append-only arrival journal (creation + one-time
    release + lifecycle markers); ``ledger`` is the canonical lifecycle ledger
    (order pool and complete previous-lifecycle history), which is the authority
    for lifecycle states; ``time`` is the explicit native simulation clock.  The
    snapshot carries a deterministic ``canonical_digest`` over every field,
    including the native clock.
    """

    time: SimulationTime
    version: Annotated[int, Field(ge=0)] = 0
    events: tuple[OrderArrivalEvent, ...] = ()
    ledger: LogisticsLedgerState

    @model_validator(mode="after")
    def snapshot_is_consistent(self) -> "OrderArrivalSnapshot":
        if self.version != len(self.events):
            raise ValueError("arrival snapshot version must equal the journal length")
        _validate_arrival_journal(self.events, current_time=self.time)

        marker_records = tuple(
            event.lifecycle_record
            for event in self.events
            if event.kind == "lifecycle"
        )
        if marker_records != self.ledger.history:
            raise ValueError(
                "arrival journal lifecycle markers do not match the canonical "
                "lifecycle history"
            )

        created_ids = tuple(
            event.order_id for event in self.events if event.kind == "created"
        )
        if tuple(state.order_id for state in self.ledger.orders) != created_ids:
            raise ValueError(
                "arrival snapshot lifecycle ledger does not follow the arrival "
                "journal creation order"
            )
        return self

    def canonical_digest(self) -> str:
        """Deterministic SHA-256 over every canonical snapshot field."""
        return hashlib.sha256(
            canonical_json_bytes(self.model_dump(mode="json"))
        ).hexdigest()


class OrderArrivalService:
    """Deterministic arrival/event service over the canonical order lifecycle.

    The service owns the explicit current native clock, the append-only arrival
    journal (creation + one-time release + lifecycle markers), and a canonical
    :class:`LogisticsOrdersService` that is *reconstructed* (never mutated
    through private attributes) with the full current registered pool whenever a
    new order arrives.  Reconstructing replays every previously accepted life
    lifecycle transition, making the history consistency explicit and preserving
    all accepted prior history.

    This is authoritative business simulation input; it never fabricates
    telemetry, routes, coordinates or delivery evidence.  The canonical lifecycle
    (``advance_order``) remains the sole authority over order states.
    """

    def __init__(
        self,
        *,
        catalogue: FacilityCatalogue,
        fleet: Fleet,
        actor_grants: tuple[OrderActorGrant, ...],
        initial_requests: tuple[OrderRequest, ...],
        initial_time: SimulationTime = _ZERO_TIME,
    ) -> None:
        if not isinstance(catalogue, FacilityCatalogue):
            raise TypeError("OrderArrivalService requires the canonical FacilityCatalogue")
        if not isinstance(fleet, Fleet):
            raise TypeError("OrderArrivalService requires the canonical Fleet")
        if fleet.catalogue != catalogue:
            raise ValueError(
                "OrderArrivalService fleet must be bound to the arrival catalogue"
            )
        if not isinstance(initial_time, SimulationTime):
            raise TypeError("OrderArrivalService initial_time must be a SimulationTime")
        _validate_actor_grants(actor_grants)
        self._catalogue = catalogue
        self._fleet = fleet
        self._actor_grants = actor_grants
        self._time = initial_time
        self._events: tuple[OrderArrivalEvent, ...] = ()
        self._requests: tuple[OrderRequest, ...] = ()
        self._lifecycle_history: tuple[LogisticsHistoryRecord, ...] = ()

        seen_order_ids: set[str] = set()
        for request in initial_requests:
            if request.order_id in seen_order_ids:
                raise ValueError(
                    f"duplicate initial logistics order id: {request.order_id}"
                )
            seen_order_ids.add(request.order_id)
            validate_order_for_arrival(catalogue, fleet, request)
            self._events += (
                _make_arrival_event(
                    self._events,
                    kind="created",
                    event_id=f"{_PACKAGE_EVENT_ID_PREFIX}{request.order_id}",
                    order_id=request.order_id,
                    time=initial_time,
                    order=request,
                    source_identity=ORDER_PACKAGE_SOURCE,
                    actor_id=ORDER_PACKAGE_ACTOR_ID,
                ),
            )
            self._requests += (request,)

        # The canonical lifecycle service operates over exactly the current
        # registered pool; orders already due at the baseline become available
        # exactly once.
        self._rebuild_lifecycle_service()
        self._release_sweep()

    @classmethod
    def from_snapshot(
        cls,
        *,
        snapshot: OrderArrivalSnapshot,
        initial_requests: tuple[OrderRequest, ...],
        initial_time: SimulationTime,
        catalogue: FacilityCatalogue,
        fleet: Fleet,
        actor_grants: tuple[OrderActorGrant, ...],
    ) -> "OrderArrivalService":
        """Reconstruct a live service from a validated snapshot (resume/replay).

        ``initial_requests`` is the required, authoritative baseline declaration
        (``()`` when none) and ``initial_time`` the native time at which those
        baseline requests were registered; the reserved baseline events are bound
        to them exactly.  The append-only journal and the canonical lifecycle
        history are taken verbatim from the snapshot, the lifecycle service is
        rebuilt over the exact current registered pool (replaying every accepted
        transition), and the rebuilt ledger must equal the snapshot ledger.  No
        release is re-issued because the one-time releases are already recorded
        in the journal.
        """
        if not isinstance(snapshot, OrderArrivalSnapshot):
            raise TypeError("from_snapshot requires an OrderArrivalSnapshot")
        if not isinstance(catalogue, FacilityCatalogue):
            raise TypeError("from_snapshot requires the canonical FacilityCatalogue")
        if not isinstance(fleet, Fleet):
            raise TypeError("from_snapshot requires the canonical Fleet")
        if fleet.catalogue != catalogue:
            raise ValueError(
                "from_snapshot fleet must be bound to the arrival catalogue"
            )
        if not isinstance(initial_time, SimulationTime):
            raise TypeError("from_snapshot initial_time must be a SimulationTime")
        _validate_actor_grants(actor_grants)
        # Re-validate the snapshot body so a caller-supplied snapshot cannot
        # bypass journal sequence / release-availability / ledger-pool binding.
        OrderArrivalSnapshot.model_validate(snapshot.model_dump())
        _validate_baseline_binding(
            events=snapshot.events,
            initial_requests=initial_requests,
            initial_time=initial_time,
        )
        _validate_introduction_authority(snapshot.events, actor_grants)
        requests = tuple(
            event.order
            for event in snapshot.events
            if event.kind == "created" and event.order is not None
        )
        for event in snapshot.events:
            if event.kind == "created":
                assert event.order is not None
                validate_order_for_arrival(catalogue, fleet, event.order)

        instance = cls.__new__(cls)
        instance._catalogue = catalogue
        instance._fleet = fleet
        instance._actor_grants = actor_grants
        instance._time = snapshot.time
        instance._events = snapshot.events
        instance._requests = requests
        instance._lifecycle_history = snapshot.ledger.history
        instance._rebuild_lifecycle_service()
        if instance._service.ledger != snapshot.ledger:
            raise ValueError(
                "from_snapshot rebuilt ledger differs from the snapshot ledger"
            )
        return instance

    @property
    def catalogue(self) -> FacilityCatalogue:
        return self._catalogue

    @property
    def fleet(self) -> Fleet:
        return self._fleet

    @property
    def actor_grants(self) -> tuple[OrderActorGrant, ...]:
        return self._actor_grants

    @property
    def current_time(self) -> SimulationTime:
        """The explicit current native simulation clock (never a float)."""
        return self._time

    @property
    def snapshot(self) -> OrderArrivalSnapshot:
        """Immutable aggregate over the journal and the canonical lifecycle ledger."""
        return OrderArrivalSnapshot(
            time=self._time,
            version=len(self._events),
            events=self._events,
            ledger=self._service.ledger,
        )

    @property
    def digest(self) -> str:
        """Deterministic digest of the current snapshot (see snapshot.canonical_digest)."""
        return self.snapshot.canonical_digest()

    def registered_order_ids(self) -> tuple[str, ...]:
        return tuple(request.order_id for request in self._requests)

    def released_order_ids(self) -> tuple[str, ...]:
        return tuple(
            event.order_id for event in self._events if event.kind == "released"
        )

    def available_orders(self) -> tuple[OrderState, ...]:
        """Released (currently available) orders in canonical pool order.

        Orders whose declared release gate has not been passed by the current
        native clock are hidden from this view until their exact one-time
        release.
        """
        released = _released_order_ids(self._events)
        return tuple(
            state
            for state in self._service.ledger.orders
            if state.order_id in released
        )

    def submit(self, command: OrderArrivalCommand) -> OrderArrivalEvent:
        """Register one online canonical OrderRequest at the current native time.

        Rejects tick/nanosecond-mismatched commands (``command.time`` must equal
        the current native clock exactly), retrospective availability
        timestamps, unknown or duplicate command/order ids, reserved baseline /
        command namespace abuse, and role forgery, all *before* any state
        mutation.  The order is validated against the canonical catalogue/hub
        routing and the actual declared fleet payload capacities; the declared
        source identity is preserved verbatim in the journal.
        """
        if not isinstance(command, OrderArrivalCommand):
            raise TypeError("submit requires an OrderArrivalCommand")
        if command.time != self._time:
            raise ValueError(
                "order arrival command must carry exactly the current "
                f"simulation time {self._time}; backdated or future-dated "
                "commands are rejected"
            )
        if _release_gate_ns(command.order) < command.time.sim_time_ns:
            raise ValueError(
                f"order {command.order.order_id} release time cannot precede the "
                "submission time (no retrospective availability)"
            )
        if command.command_id.startswith(
            (
                _PACKAGE_EVENT_ID_PREFIX,
                _RELEASE_EVENT_ID_PREFIX,
                _LIFECYCLE_MARKER_ID_PREFIX,
            )
        ):
            raise ValueError(
                "arrival command_id uses a reserved arrival event namespace: "
                f"{command.command_id}"
            )
        if command.actor_id == ORDER_PACKAGE_ACTOR_ID:
            raise ValueError(
                "reserved baseline actor id cannot introduce an online order"
            )
        if command.source_identity == ORDER_PACKAGE_SOURCE:
            raise ValueError(
                "reserved baseline source identity cannot be claimed by a live "
                "command"
            )
        if any(event.event_id == command.command_id for event in self._events):
            raise ValueError(
                f"arrival command_id was already processed: {command.command_id}"
            )
        if any(request.order_id == command.order.order_id for request in self._requests):
            raise ValueError(
                f"logistics order is already registered: {command.order.order_id}"
            )
        self._require_introducer(command)
        validate_order_for_arrival(self._catalogue, self._fleet, command.order)

        # All validation passed; only now mutate append-only state.
        event = _make_arrival_event(
            self._events,
            kind="created",
            event_id=command.command_id,
            order_id=command.order.order_id,
            time=command.time,
            order=command.order,
            source_identity=command.source_identity,
            actor_id=command.actor_id,
        )
        self._events += (event,)
        self._requests += (command.order,)
        self._rebuild_lifecycle_service()
        self._release_sweep()
        return event

    def advance_time(self, time: SimulationTime) -> tuple[OrderArrivalEvent, ...]:
        """Advance the explicit native clock and release all orders due now.

        The clock only ever moves forward under the canonical (tick,
        nanosecond) partial order: moving to a later tick with an earlier
        nanosecond (or vice versa) is rejected as backdating, exactly like the
        canonical runtime ledger ordering.  Newly due orders are released
        exactly once in deterministic registration order; the returned tuple is
        the set of release events appended by this advance.
        """
        if not isinstance(time, SimulationTime):
            raise TypeError("advance_time requires a SimulationTime")
        if _time_before(time, self._time):
            raise ValueError(
                f"simulation time moved backwards: {time} < {self._time}"
            )
        self._time = time
        return self._release_sweep()

    def apply_lifecycle(
        self, transition: OrderTransition
    ) -> LogisticsTransitionResult:
        """Apply one lifecycle transition, delegating every rule to ``advance_order``.

        The arrival layer only *gates*: the order must be registered and already
        released (a future order cannot be offered or assigned), and the
        transition must bind exactly to the current harness time -- its float
        ``time_s`` must equal ``sim_time_ns / 1e9`` of the current native clock,
        never an earlier number.  All lifecycle state/evidence rules (including
        the requirement that physical evidence references differ from the command
        receipt) are enforced by the canonical lifecycle and are never relaxed
        here.  On success the canonical ``LogisticsHistoryRecord`` is appended to
        the journal as a typed lifecycle marker.
        """
        if not any(
            request.order_id == transition.order_id for request in self._requests
        ):
            raise ValueError(f"unknown logistics order: {transition.order_id}")
        if transition.order_id not in _released_order_ids(self._events):
            raise ValueError(
                f"order {transition.order_id} is not yet available; a future "
                "release cannot be offered or assigned before its declared "
                "availability gate"
            )
        expected_seconds = _native_seconds(self._time)
        if transition.time_s != expected_seconds:
            raise ValueError(
                f"order transition at {transition.time_s}s does not bind exactly "
                f"to the current simulation time {expected_seconds}s; lifecycle "
                "transitions must match the current harness time exactly"
            )
        result = self._service.apply(transition)
        self._events += (
            _make_arrival_event(
                self._events,
                kind="lifecycle",
                event_id=f"{_LIFECYCLE_MARKER_ID_PREFIX}{transition.transition_id}",
                order_id=transition.order_id,
                time=self._time,
                lifecycle_record=result.history_record,
            ),
        )
        self._lifecycle_history = self._service.ledger.history
        return result

    def _require_introducer(self, command: OrderArrivalCommand) -> None:
        if command.actor_id == ORDER_PACKAGE_ACTOR_ID:
            raise ValueError(
                "reserved baseline actor id cannot introduce an online order"
            )
        if command.source_identity == ORDER_PACKAGE_SOURCE:
            raise ValueError(
                "reserved baseline source identity cannot be claimed by a live "
                "command"
            )
        grant = next(
            (
                grant
                for grant in self._actor_grants
                if grant.actor_id == command.actor_id
            ),
            None,
        )
        if grant is None:
            raise ValueError(
                f"unknown actor attempting to introduce an order: {command.actor_id}"
            )
        if grant.role != command.actor_role:
            raise ValueError(
                "declared actor_role does not match the canonical actor grant for "
                f"{command.actor_id}"
            )
        if grant.role != ORDER_INTRODUCING_ROLE:
            raise ValueError(
                f"actor {command.actor_id} is not authorized to introduce orders; "
                f"only the {ORDER_INTRODUCING_ROLE!r} role may register an order"
            )

    def _rebuild_lifecycle_service(self) -> None:
        """Reconstruct the canonical service with the full current pool.

        Replays every previously accepted lifecycle transition so prior history
        is preserved and consistency is explicit. ``LogisticsOrdersService`` is
        never mutated through private attributes.
        """
        service = LogisticsOrdersService(
            requests=self._requests,
            actor_grants=self._actor_grants,
        )
        for record in self._lifecycle_history:
            service.apply(record.transition)
        self._service = service
        self._lifecycle_history = service.ledger.history

    def _release_sweep(self) -> tuple[OrderArrivalEvent, ...]:
        """Exact one-time release of every due, unreleased order (registration order)."""
        released_events: list[OrderArrivalEvent] = []
        released_ids = _released_order_ids(self._events)
        seen_event_ids = {event.event_id for event in self._events}
        for request in self._requests:
            if request.order_id in released_ids:
                continue
            if _release_gate_ns(request) <= self._time.sim_time_ns:
                event_id = f"{_RELEASE_EVENT_ID_PREFIX}{request.order_id}"
                if event_id in seen_event_ids:
                    raise ValueError(
                        f"arrival release event id collides with an existing event: {event_id}"
                    )
                event = _make_arrival_event(
                    self._events,
                    kind="released",
                    event_id=event_id,
                    order_id=request.order_id,
                    time=self._time,
                )
                self._events += (event,)
                seen_event_ids.add(event_id)
                released_events.append(event)
        return tuple(released_events)


def _released_order_ids(events: tuple[OrderArrivalEvent, ...]) -> frozenset[str]:
    return frozenset(event.order_id for event in events if event.kind == "released")


def replay_order_arrivals(
    *,
    catalogue: FacilityCatalogue,
    fleet: Fleet,
    actor_grants: tuple[OrderActorGrant, ...],
    initial_requests: tuple[OrderRequest, ...],
    initial_time: SimulationTime,
    events: tuple[OrderArrivalEvent, ...],
    current_time: SimulationTime,
) -> OrderArrivalSnapshot:
    """Deterministically rebuild a snapshot from the single append-only journal.

    The journal is the sole causal sequence: created -> one-time release ->
    lifecycle markers.  Every recorded order is re-validated against the
    canonical catalogue/hub routing and the actual declared fleet payload
    capacities; the journal hash chain, native-clock temporal ordering, one-time
    release gates and the canonical lifecycle history are all re-checked.  The
    reserved baseline namespace is bound exactly to the authoritative
    ``initial_requests`` (required, ``()`` when none) at ``initial_time``;
    introduction authority is re-verified for every live-created event.  Altered
    arrival content, reordered causal history, forged baseline styling, or a
    broken chain is rejected here or yields a different snapshot digest.
    """
    if not isinstance(catalogue, FacilityCatalogue):
        raise TypeError("replay_order_arrivals requires the canonical FacilityCatalogue")
    if not isinstance(fleet, Fleet):
        raise TypeError("replay_order_arrivals requires the canonical Fleet")
    if fleet.catalogue != catalogue:
        raise ValueError(
            "replay_order_arrivals fleet must be bound to the arrival catalogue"
        )
    if not isinstance(current_time, SimulationTime):
        raise TypeError("replay_order_arrivals current_time must be a SimulationTime")
    if not isinstance(initial_time, SimulationTime):
        raise TypeError("replay_order_arrivals initial_time must be a SimulationTime")
    _validate_actor_grants(actor_grants)
    _validate_baseline_binding(
        events=events,
        initial_requests=initial_requests,
        initial_time=initial_time,
    )
    _validate_introduction_authority(events, actor_grants)

    created_events = tuple(event for event in events if event.kind == "created")
    requests = tuple(event.order for event in created_events if event.order is not None)
    if len(requests) != len(created_events):
        raise ValueError("created arrival events must carry their full OrderRequest")
    for event in created_events:
        assert event.order is not None
        validate_order_for_arrival(catalogue, fleet, event.order)

    lifecycle_history = tuple(
        event.lifecycle_record
        for event in events
        if event.kind == "lifecycle" and event.lifecycle_record is not None
    )
    ledger = replay_logistics_history(
        requests,
        actor_grants=actor_grants,
        history=lifecycle_history,
    )
    return OrderArrivalSnapshot(
        time=current_time,
        version=len(events),
        events=events,
        ledger=ledger,
    )


__all__ = [
    "ORDER_INTRODUCING_ROLE",
    "ORDER_PACKAGE_ACTOR_ID",
    "ORDER_PACKAGE_SOURCE",
    "OrderArrivalCommand",
    "OrderArrivalEvent",
    "OrderArrivalService",
    "OrderArrivalSnapshot",
    "replay_order_arrivals",
    "validate_order_for_arrival",
]
