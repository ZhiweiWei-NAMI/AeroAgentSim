from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.tasks.logistics import (
    LogisticsLedgerState,
    LogisticsOrdersService,
    OrderActorGrant,
    OrderRequest,
    OrderStatus,
    OrderTransition,
    replay_logistics_history,
)
from aero_bench.tasks.logistics.authoring import compile_authored_orders
from aero_bench.tasks.logistics.facilities import compile_authored_facilities

RUN_ACTORS = (
    OrderActorGrant(actor_id="business", role="business"),
    OrderActorGrant(actor_id="dispatcher", role="dispatcher"),
    OrderActorGrant(actor_id="aircraft.1", role="aircraft_agent"),
    OrderActorGrant(actor_id="aircraft.2", role="aircraft_agent"),
)

FACILITY_ORIGIN = "facility.tianhe"
FACILITY_DESTINATION = "facility.liede"
FACILITY_HUB = "facility.hub.cargo"

#: Full current frontend v3 facility capability records lifted to the canonical
#: FacilityCatalogue by compile_authored_facilities before any order lowering.
FULL_V3_FACILITIES = [
    {
        "id": FACILITY_ORIGIN,
        "name": "tianhe vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 0, "z": 0},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    },
    {
        "id": FACILITY_DESTINATION,
        "name": "liede vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 0, "z": 200},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": None,
        "charging": None,
    },
    {
        "id": FACILITY_HUB,
        "name": "cargo hub",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 200, "z": 0},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 1000, "throughputPerHourKg": 500},
        "charging": None,
    },
]

FRONTEND_ORDER_VALUES = {
    "id": "order.1",
    "sourceFacilityId": FACILITY_ORIGIN,
    "destinationFacilityId": FACILITY_DESTINATION,
    "hubHandoffFacilityId": FACILITY_HUB,
    "cargoKg": 12,
    "releaseAtS": 0,
    "deliverByS": 3600,
}


def _request(**overrides: object) -> OrderRequest:
    values = {
        "order_id": "order.1",
        "origin_facility_id": FACILITY_ORIGIN,
        "destination_facility_id": FACILITY_DESTINATION,
        "hub_handoff_facility_id": FACILITY_HUB,
        "cargo_mass_kg": 12.0,
        "release_time_s": 0.0,
        "deadline_s": 3600.0,
    }
    values.update(overrides)
    return OrderRequest.model_validate(values)


def _service() -> LogisticsOrdersService:
    return LogisticsOrdersService((_request(),), actor_grants=RUN_ACTORS)


def test_frontend_valid_identifiers_and_empty_order_pool() -> None:
    request = _request(
        order_id="Order:01",
        origin_facility_id="Hub:North",
        destination_facility_id="Port:South",
        hub_handoff_facility_id="Hub:North",
    )
    assert request.initial_state().order_id == "Order:01"
    assert LogisticsOrdersService((), actor_grants=RUN_ACTORS).ledger.orders == ()


def test_state_cannot_bypass_request_invariants_and_grants_are_unambiguous() -> None:
    state = _request().initial_state().model_dump()
    with pytest.raises(ValidationError, match="cargo_mass_kg"):
        type(_request().initial_state()).model_validate({**state, "cargo_mass_kg": -1.0})
    with pytest.raises(ValidationError, match="deadline"):
        type(_request().initial_state()).model_validate({**state, "deadline_s": -1.0})
    with pytest.raises(ValueError, match="grant ids"):
        LogisticsOrdersService((_request(),), actor_grants=(*RUN_ACTORS, RUN_ACTORS[0]))


def test_offer_targets_an_authorized_aircraft_agent() -> None:
    service = _service()
    with pytest.raises(ValueError, match="authorized aircraft agent"):
        service.apply(_offer(assignee_id="dispatcher"))
    service.apply(_offer())
    service.apply(_accept())
    with pytest.raises(ValueError, match="authorized aircraft agent"):
        service.apply(_assign(assignee_id="unlisted.aircraft"))


def _transition(
    event: str,
    *,
    transition_id: str = "transition.1",
    actor_id: str = "dispatcher",
    actor_role: str = "dispatcher",
    time_s: float = 10.0,
    expected_version: int = 0,
    assignee_id: str | None = "aircraft.1",
    capacity_kg: float | None = None,
    evidence_ref: str | None = None,
    handoff_facility_id: str | None = None,
    reason: str | None = None,
) -> OrderTransition:
    return OrderTransition(
        order_id="order.1",
        transition_id=transition_id,
        event=event,
        actor_id=actor_id,
        actor_role=actor_role,
        time_s=time_s,
        expected_version=expected_version,
        assignee_id=assignee_id,
        capacity_kg=capacity_kg,
        evidence_ref=evidence_ref,
        handoff_facility_id=handoff_facility_id,
        reason=reason,
    )


def _offer(**overrides: object) -> OrderTransition:
    values = {
        "event": "offer",
        "transition_id": "transition.offer",
        "actor_id": "dispatcher",
        "actor_role": "dispatcher",
        "time_s": 10.0,
        "expected_version": 0,
        "assignee_id": "aircraft.1",
    }
    values.update(overrides)
    return _transition(**values)


def _accept(**overrides: object) -> OrderTransition:
    values = {
        "event": "accept",
        "transition_id": "transition.accept",
        "actor_id": "aircraft.1",
        "actor_role": "aircraft_agent",
        "time_s": 20.0,
        "expected_version": 1,
        "assignee_id": "aircraft.1",
    }
    values.update(overrides)
    return _transition(**values)


def _assign(**overrides: object) -> OrderTransition:
    values = {
        "event": "assign",
        "transition_id": "transition.assign",
        "actor_id": "dispatcher",
        "actor_role": "dispatcher",
        "time_s": 30.0,
        "expected_version": 2,
        "assignee_id": "aircraft.1",
        "capacity_kg": 100.0,
    }
    values.update(overrides)
    return _transition(**values)


def _pick_up(**overrides: object) -> OrderTransition:
    values = {
        "event": "pick_up",
        "transition_id": "transition.pickup",
        "actor_id": "aircraft.1",
        "actor_role": "aircraft_agent",
        "time_s": 40.0,
        "expected_version": 3,
        "assignee_id": "aircraft.1",
        "evidence_ref": "evidence.scale.handoff",
    }
    values.update(overrides)
    return _transition(**values)


def _handoff(**overrides: object) -> OrderTransition:
    values = {
        "event": "handoff",
        "transition_id": "transition.handoff",
        "actor_id": "aircraft.1",
        "actor_role": "aircraft_agent",
        "time_s": 50.0,
        "expected_version": 4,
        "assignee_id": "aircraft.1",
        "evidence_ref": "evidence.hub.receipt",
        "handoff_facility_id": FACILITY_HUB,
    }
    values.update(overrides)
    return _transition(**values)


def _deliver(**overrides: object) -> OrderTransition:
    values = {
        "event": "deliver",
        "transition_id": "transition.deliver",
        "actor_id": "aircraft.1",
        "actor_role": "aircraft_agent",
        "time_s": 60.0,
        "expected_version": 5,
        "assignee_id": "aircraft.1",
        "evidence_ref": "evidence.dock.receipt",
    }
    values.update(overrides)
    return _transition(**values)


def _full_lifecycle(service: LogisticsOrdersService) -> None:
    service.apply(_offer())
    service.apply(_accept())
    service.apply(_assign())
    service.apply(_pick_up())
    service.apply(_handoff())
    service.apply(_deliver())


def test_full_lifecycle_reaches_delivered() -> None:
    service = _service()
    assert service.ledger.orders[0].status is OrderStatus.CREATED
    result = service.apply(_offer())
    assert result.order.status is OrderStatus.OFFERED
    assert result.order.version == 1
    assert result.order.assignee_id == "aircraft.1"
    service.apply(_accept())
    service.apply(_assign())
    picked_up = service.apply(_pick_up())
    assert picked_up.order.evidence_ref == "evidence.scale.handoff"
    assert picked_up.order.handoff_evidence_ref is None
    assert picked_up.order.hub_handoff_version is None
    handed_off = service.apply(_handoff())
    assert handed_off.order.status is OrderStatus.HANDED_OFF
    assert handed_off.order.handoff_evidence_ref == "evidence.hub.receipt"
    assert handed_off.order.hub_handoff_version == 5
    delivered = service.apply(_deliver())
    assert delivered.order.status is OrderStatus.DELIVERED
    assert delivered.order.version == 6
    assert delivered.order.assignee_id == "aircraft.1"
    assert delivered.order.capacity_kg == 100.0
    assert delivered.order.evidence_ref == "evidence.dock.receipt"
    # The required hub handoff evidence and data version are retained in the
    # delivered state (recency/retention), never dropped by delivery.
    assert delivered.order.handoff_evidence_ref == "evidence.hub.receipt"
    assert delivered.order.hub_handoff_version == 5
    assert delivered.order.hub_handoff_version < delivered.order.version
    assert service.ledger.version == 6
    assert len(service.ledger.history) == 6


def test_reject_offer_terminates_order() -> None:
    service = _service()
    service.apply(_offer())
    rejected = service.apply(
        _transition(
            "reject",
            transition_id="transition.reject",
            actor_id="aircraft.1",
            actor_role="aircraft_agent",
            time_s=20.0,
            expected_version=1,
            assignee_id="aircraft.1",
            reason="no_capacity",
        )
    )
    assert rejected.order.status is OrderStatus.REJECTED
    assert rejected.order.failure_reason == "no_capacity"


def test_business_can_cancel_created_and_offered_orders() -> None:
    service = _service()
    with pytest.raises(ValueError, match="assignee"):
        service.apply(_transition("cancel", transition_id="phantom.cancel", actor_id="business",
            actor_role="business", time_s=1.0, expected_version=0,
            assignee_id="ghost.unit", reason="client.cancellation"))
    assert service.ledger.version == 0
    cancelled = service.apply(
        _transition(
            "cancel",
            transition_id="transition.cancel-created",
            actor_id="business",
            actor_role="business",
            time_s=5.0,
            expected_version=0,
            assignee_id=None,
            reason="client.cancellation",
        )
    )
    assert cancelled.order.status is OrderStatus.CANCELLED

    service = _service()
    service.apply(_offer())
    cancelled = service.apply(
        _transition(
            "cancel",
            transition_id="transition.cancel-offered",
            actor_id="business",
            actor_role="business",
            time_s=20.0,
            expected_version=1,
            assignee_id="aircraft.1",
            reason="client.cancellation",
        )
    )
    assert cancelled.order.status is OrderStatus.CANCELLED


def test_dispatcher_fail_terminates_order() -> None:
    service = _service()
    service.apply(_offer())
    failed = service.apply(
        _transition(
            "fail",
            transition_id="transition.fail",
            actor_id="dispatcher",
            actor_role="dispatcher",
            time_s=20.0,
            expected_version=1,
            assignee_id="aircraft.1",
            reason="no_slot_available",
        )
    )
    assert failed.order.status is OrderStatus.FAILED
    assert failed.order.failure_reason == "no_slot_available"


def test_illegal_transition_is_rejected() -> None:
    service = _service()
    # Deliver from a bare created order is not a legal (status, event) pair.
    with pytest.raises(ValueError, match="invalid order transition"):
        service.apply(_deliver(expected_version=0))
    # Assign is only legal once the offered aircraft has accepted.
    service.apply(_offer())
    with pytest.raises(ValueError, match="invalid order transition"):
        service.apply(_assign(expected_version=1))


def test_unknown_order_is_rejected() -> None:
    service = _service()
    with pytest.raises(ValueError, match="unknown order"):
        service.apply(
            OrderTransition(
                order_id="order.missing",
                transition_id="transition.unknown",
                event="offer",
                actor_id="dispatcher",
                actor_role="dispatcher",
                time_s=10.0,
                expected_version=0,
                assignee_id="aircraft.1",
            )
        )


def test_duplicate_transition_id_is_rejected() -> None:
    service = _service()
    offer = _offer()
    service.apply(offer)
    with pytest.raises(ValueError, match="already processed"):
        service.apply(
            OrderTransition.model_validate({**offer.model_dump(), "time_s": 15.0})
        )


def test_version_race_is_rejected() -> None:
    service = _service()
    service.apply(_offer())
    stale = _accept(expected_version=0)
    with pytest.raises(ValueError, match="expected_version"):
        service.apply(stale)


def test_time_reversal_is_rejected() -> None:
    service = _service()
    service.apply(_offer(time_s=20.0))
    with pytest.raises(ValueError, match="time moved backwards"):
        service.apply(_accept(time_s=19.0))


def test_transition_before_release_is_rejected() -> None:
    # An order becomes actionable at its release time; a fresh order carries no
    # earlier history, so a transition before release is a time reversal.
    service = LogisticsOrdersService(
        (_request(release_time_s=100.0),),
        actor_grants=RUN_ACTORS,
    )
    with pytest.raises(ValueError, match="time moved backwards"):
        service.apply(_offer(time_s=50.0))


def test_wrong_assignee_identity_is_rejected() -> None:
    service = _service()
    service.apply(_offer())
    with pytest.raises(
        ValueError, match="only the assigned aircraft may advance"
    ):
        service.apply(
            _accept(actor_id="aircraft.2", assignee_id="aircraft.2")
        )
    with pytest.raises(ValueError, match="only the assigned aircraft"):
        service.apply(_accept(actor_id="aircraft.1"))
        service.apply(_assign())
        service.apply(
            _pick_up(actor_id="aircraft.2", assignee_id="aircraft.2")
        )

    handed_off_service = _service()
    handed_off_service.apply(_offer())
    handed_off_service.apply(_accept())
    handed_off_service.apply(_assign())
    handed_off_service.apply(_pick_up())
    with pytest.raises(ValueError, match="only the assigned aircraft"):
        handed_off_service.apply(
            _handoff(actor_id="aircraft.2", assignee_id="aircraft.2")
        )


def test_actor_not_authorized_for_declared_role_is_rejected() -> None:
    service = _service()
    with pytest.raises(ValueError, match="not authorized"):
        service.apply(
            _offer(actor_id="aircraft.1", actor_role="dispatcher")
        )
    with pytest.raises(ValueError, match="not authorized"):
        service.apply(_offer())
        service.apply(
            _accept(actor_id="unlisted.agent", actor_role="aircraft_agent")
        )


def test_cancel_cannot_be_issued_by_dispatcher() -> None:
    with pytest.raises(ValidationError, match="cannot be issued by dispatcher"):
        _transition(
            "cancel",
            actor_id="dispatcher",
            actor_role="dispatcher",
            assignee_id=None,
            reason="test",
        )


def test_evidence_gate_requires_external_reference() -> None:
    with pytest.raises(ValidationError, match="external evidence reference"):
        _pick_up(evidence_ref=None)
    with pytest.raises(
        ValidationError, match="evidence reference cannot be the transition"
    ):
        _deliver(evidence_ref="transition.deliver")
    with pytest.raises(ValidationError, match="external evidence reference"):
        _handoff(evidence_ref=None)
    with pytest.raises(
        ValidationError, match="evidence reference cannot be the transition"
    ):
        _handoff(evidence_ref="transition.handoff")


def test_handoff_requires_canonical_hub_facility() -> None:
    with pytest.raises(ValidationError, match="canonical hub facility"):
        _handoff(handoff_facility_id=None)


def test_assign_rejects_overcapacity_cargo() -> None:
    heavy = _request(cargo_mass_kg=150.0)
    service = LogisticsOrdersService((heavy,), actor_grants=RUN_ACTORS)
    service.apply(_offer())
    service.apply(_accept())
    with pytest.raises(ValueError, match="capacity"):
        service.apply(_assign(capacity_kg=100.0))


def test_direct_delivery_bypass_is_rejected() -> None:
    # A pickup at a vertiport may not jump straight to final delivery: the cargo
    # must first be handed off at the canonical hub and the handoff observed.
    service = _service()
    service.apply(_offer())
    service.apply(_accept())
    service.apply(_assign())
    service.apply(_pick_up())
    with pytest.raises(ValueError, match="invalid order transition"):
        service.apply(_deliver(expected_version=4))


def test_handoff_observation_at_wrong_facility_is_rejected() -> None:
    # The observed hub handoff is not trusted from a bare ID string: naming any
    # facility other than the canonical hub is rejected by the lifecycle.
    service = _service()
    service.apply(_offer())
    service.apply(_accept())
    service.apply(_assign())
    service.apply(_pick_up())
    with pytest.raises(ValueError, match="canonical hub"):
        service.apply(_handoff(handoff_facility_id=FACILITY_ORIGIN))


def test_endpoint_hub_lifecycle_reaches_delivered() -> None:
    # A destination hub is accepted as its own handoff ("documented as their own
    # handoff"): the parcel is picked up, observed at the hub, then delivered.
    request = _request(destination_facility_id=FACILITY_HUB)
    service = LogisticsOrdersService((request,), actor_grants=RUN_ACTORS)
    service.apply(_offer())
    service.apply(_accept())
    service.apply(_assign())
    service.apply(_pick_up())
    handed_off = service.apply(_handoff(handoff_facility_id=FACILITY_HUB))
    assert handed_off.order.hub_handoff_facility_id == FACILITY_HUB
    delivered = service.apply(_deliver())
    assert delivered.order.status is OrderStatus.DELIVERED
    assert delivered.order.hub_handoff_version == 5


def test_order_itinerary_reflects_hub_midpoint_and_endpoint() -> None:
    midpoint = _request().itinerary()
    assert midpoint.model_dump() == {
        "order_id": "order.1",
        "canonical_hub_facility_id": FACILITY_HUB,
        "legs": (
            {
                "sequence": 0,
                "from_facility_id": FACILITY_ORIGIN,
                "to_facility_id": FACILITY_HUB,
                "role": "source_to_hub",
            },
            {
                "sequence": 1,
                "from_facility_id": FACILITY_HUB,
                "to_facility_id": FACILITY_DESTINATION,
                "role": "hub_to_destination",
            },
        ),
    }
    destination_hub = _request(destination_facility_id=FACILITY_HUB).itinerary()
    assert [leg.role for leg in destination_hub.legs] == ["source_to_hub"]
    source_hub = _request(origin_facility_id=FACILITY_HUB).itinerary()
    assert [leg.role for leg in source_hub.legs] == ["hub_to_destination"]


def test_request_rejects_invalid_inputs() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        _request(destination_facility_id=FACILITY_ORIGIN)
    with pytest.raises(ValidationError, match="deadline must follow"):
        _request(release_time_s=10.0, deadline_s=5.0)
    with pytest.raises(ValidationError, match="finite and positive"):
        _request(cargo_mass_kg=0.0)
    with pytest.raises(ValidationError, match="finite and positive"):
        _request(cargo_mass_kg=-1.0)
    # Strict domain values are numbers; a string is not silently coerced.
    with pytest.raises(ValidationError, match="finite number"):
        _request(cargo_mass_kg="1")


def test_transition_rejects_invalid_inputs() -> None:
    with pytest.raises(ValidationError, match="finite and nonnegative"):
        _offer(time_s=-1.0)
    with pytest.raises(ValidationError, match="finite and positive"):
        _assign(capacity_kg=0.0)
    with pytest.raises(ValidationError, match="requires a reason"):
        _transition(
            "reject",
            transition_id="transition.reject",
            actor_id="aircraft.1",
            actor_role="aircraft_agent",
            time_s=20.0,
            expected_version=1,
            assignee_id="aircraft.1",
            reason=None,
        )
    with pytest.raises(ValidationError, match="cannot carry a handoff facility"):
        _transition(
            "pick_up",
            transition_id="transition.pickup",
            actor_id="aircraft.1",
            actor_role="aircraft_agent",
            time_s=40.0,
            expected_version=3,
            assignee_id="aircraft.1",
            evidence_ref="evidence.scale.handoff",
            handoff_facility_id=FACILITY_HUB,
        )


def test_terminal_order_cannot_be_mutated() -> None:
    service = _service()
    _full_lifecycle(service)
    with pytest.raises(ValueError, match="terminal order cannot be mutated"):
        service.apply(
            _transition(
                "fail",
                transition_id="transition.after-delivery",
                actor_id="dispatcher",
                actor_role="dispatcher",
                time_s=70.0,
                expected_version=6,
                assignee_id="aircraft.1",
                reason="late",
            )
        )

    rejected = _service()
    rejected.apply(_offer())
    rejected.apply(
        _transition(
            "reject",
            transition_id="transition.reject",
            actor_id="aircraft.1",
            actor_role="aircraft_agent",
            time_s=20.0,
            expected_version=1,
            assignee_id="aircraft.1",
            reason="no_capacity",
        )
    )
    with pytest.raises(ValueError, match="terminal order cannot be mutated"):
        rejected.apply(
            _offer(transition_id="transition.reoffer", expected_version=2)
        )


def test_delivered_snapshot_retains_full_history_chain() -> None:
    service = _service()
    _full_lifecycle(service)
    ledger = service.ledger
    # The canonical hub handoff Data Version is retained in the delivered
    # snapshot and every earlier version remains in the immutable hash chain
    # (nothing is pruned before the terminal version -- a full retention window).
    delivered = ledger.orders[0]
    assert delivered.hub_handoff_version == 5
    assert ledger.version == 6
    assert len(ledger.history) == 6
    assert {record.sequence for record in ledger.history} == {0, 1, 2, 3, 4, 5}


def test_history_replays_deterministically() -> None:
    service = _service()
    for transition in (
        _offer(),
        _accept(),
        _assign(),
        _pick_up(),
        _handoff(),
        _deliver(),
    ):
        service.apply(transition)
    ledger = replay_logistics_history(
        (_request(),),
        actor_grants=RUN_ACTORS,
        history=service.ledger.history,
    )
    assert ledger.version == 6
    assert ledger.orders[0].status is OrderStatus.DELIVERED
    assert ledger.orders[0].hub_handoff_version == 5


def test_ledger_snapshots_must_match_history_reduction() -> None:
    service = _service()
    _full_lifecycle(service)
    ledger = service.ledger
    with pytest.raises(ValueError, match="snapshots differ"):
        LogisticsLedgerState(orders=ledger.orders)
    with pytest.raises(ValueError, match="snapshots differ"):
        LogisticsLedgerState.model_validate({**ledger.model_dump(), "orders": (_request().initial_state(),)})
    with pytest.raises(ValueError, match="absent order"):
        LogisticsLedgerState.model_validate({**ledger.model_dump(), "orders": ()})
    altered = ledger.orders[0].model_copy(update={"handoff_evidence_ref": "unrelated.evidence"})
    with pytest.raises(ValueError, match="snapshots differ"):
        LogisticsLedgerState.model_validate({**ledger.model_dump(), "orders": (altered,)})


def test_real_v3_facility_list_to_canonical_catalogue_to_frontend_order_lifecycle() -> None:
    catalogue = compile_authored_facilities(FULL_V3_FACILITIES)
    (order,) = compile_authored_orders([FRONTEND_ORDER_VALUES], catalogue)
    assert order.hub_handoff_facility_id == FACILITY_HUB
    assert [leg.role for leg in order.itinerary().legs] == [
        "source_to_hub",
        "hub_to_destination",
    ]
    service = LogisticsOrdersService((order,), actor_grants=RUN_ACTORS)
    _full_lifecycle(service)
    delivered = service.ledger.orders[0]
    assert delivered.status is OrderStatus.DELIVERED
    assert delivered.hub_handoff_facility_id == FACILITY_HUB
    assert delivered.handoff_evidence_ref == "evidence.hub.receipt"
    assert delivered.hub_handoff_version == 5
    assert delivered.hub_handoff_version < delivered.version


def test_real_v3_charger_endpoint_is_rejected_for_a_parcel_order() -> None:
    charges = {
        "id": "facility.charge",
        "name": "east charger",
        "kind": "charger",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": -200, "z": 0},
        "rotationDeg": 0,
        "widthM": 10,
        "depthM": 8,
        "heightM": 3.2,
        "landing": None,
        "cargo": None,
        "charging": {
            "slots": 1,
            "powerW": 5000,
            "priceAmount": 1.2,
            "priceCurrency": "CNY",
            "priceUnit": "kWh",
        },
    }
    catalogue = compile_authored_facilities(
        [FULL_V3_FACILITIES[0], FULL_V3_FACILITIES[1], charges, FULL_V3_FACILITIES[2]]
    )
    with pytest.raises(ValueError, match="does not permit cargo transfer"):
        compile_authored_orders(
            [
                {
                    **FRONTEND_ORDER_VALUES,
                    "id": "order.charge",
                    "sourceFacilityId": "facility.charge",
                }
            ],
            catalogue,
        )
    with pytest.raises(ValueError, match="does not permit cargo transfer"):
        compile_authored_orders(
            [
                {
                    **FRONTEND_ORDER_VALUES,
                    "id": "order.charge.destination",
                    "destinationFacilityId": "facility.charge",
                }
            ],
            catalogue,
        )
