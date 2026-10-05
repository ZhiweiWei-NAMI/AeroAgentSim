import pytest

from aero_bench.tasks.logistics import OrderStatus
from aero_bench.tasks.logistics.authoring import (
    AuthoredOrderRequest,
    compile_authored_orders,
)
from aero_bench.tasks.logistics.facilities import (
    FacilityCatalogue,
    compile_authored_facilities,
)
from aero_bench.tasks.logistics.orders import OrderRequest


def _vertiport(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "facility-1",
        "name": "active vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 10, "z": -20},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    }
    value.update(overrides)
    return value


def _hub(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "hub-3",
        "name": "cargo hub",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 30, "z": -40},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 50},
        "charging": None,
    }
    value.update(overrides)
    return value


def _charger(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "charger-x",
        "name": "east charger",
        "kind": "charger",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 50, "z": -60},
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
    value.update(overrides)
    return value


#: Full current frontend v3 facility records: every key present, camelCase
#: capability fields, liftable to the canonical FacilityCatalogue by
#: compile_authored_facilities -- the single facility-shape parser.
FULL_V3_FACILITIES = [_vertiport(), _vertiport(id="facility-2"), _hub(), _charger()]

CATALOGUE = compile_authored_facilities(FULL_V3_FACILITIES)


def _request(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "order-1",
        "sourceFacilityId": "facility-1",
        "destinationFacilityId": "facility-2",
        "hubHandoffFacilityId": "hub-3",
        "cargoKg": 1,
        "releaseAtS": 0,
        "deliverByS": 600,
    }
    values.update(overrides)
    return values


def test_real_v3_facility_list_lowers_to_the_canonical_catalogue() -> None:
    catalogue = compile_authored_facilities(FULL_V3_FACILITIES)
    assert isinstance(catalogue, FacilityCatalogue)
    assert [facility.facility_id for facility in catalogue.facilities] == [
        "facility-1",
        "facility-2",
        "hub-3",
        "charger-x",
    ]
    landing = catalogue.require("facility-1").landing
    assert landing is not None
    assert landing.parking_slots == 3
    assert landing.movements_per_hour == 30.0
    cargo = catalogue.require("hub-3").cargo
    assert cargo is not None
    assert cargo.storage_capacity_kg == 100.0
    assert cargo.throughput_per_hour_kg == 50.0
    charging = catalogue.require("charger-x").charging
    assert charging is not None
    assert charging.slots == 1
    assert charging.price_currency == "CNY"
    assert charging.price_unit == "kWh"


def test_current_browser_order_lowers_to_the_domain_without_changing_units_or_identity() -> None:
    request = _request()
    (order,) = compile_authored_orders([request], CATALOGUE)
    assert isinstance(order, OrderRequest)
    assert order.order_id == request["id"]
    assert order.origin_facility_id == request["sourceFacilityId"]
    assert order.destination_facility_id == request["destinationFacilityId"]
    # The canonical hub is the dispatch-visible "required hub" resolved against
    # the authoritative canonical facility catalogue, never an unvalidated ID
    # string.
    assert order.hub_handoff_facility_id == "hub-3"
    assert order.cargo_mass_kg == 1
    assert order.release_time_s == 0
    assert order.deadline_s == 600
    itinerary = order.itinerary()
    assert [leg.role for leg in itinerary.legs] == ["source_to_hub", "hub_to_destination"]


def test_pre_hub_legacy_order_shape_is_rejected_not_compatibly_parsed() -> None:
    # The older stage-3 browser order omitted hubHandoffFacilityId. There is no
    # compatibility parser: the current explicit contract requires the key.
    legacy = _request()
    del legacy["hubHandoffFacilityId"]
    with pytest.raises(ValueError, match="hubHandoffFacilityId"):
        compile_authored_orders([legacy], CATALOGUE)


@pytest.mark.parametrize("patch", [
    {"cargoKg": -1}, {"cargoKg": "1"}, {"cargoKg": "1.0"}, {"releaseAtS": -1},
    {"deliverByS": 0}, {"destinationFacilityId": "facility-1"},
    {"status": "delivered"}, {"order_id": "another-order"},
])
def test_frontend_shape_uses_strict_domain_validation(patch: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        compile_authored_orders([{**_request(), **patch}], CATALOGUE)


def test_hub_membership_is_validated_not_trusted_from_the_id_string() -> None:
    with pytest.raises(ValueError, match="unknown hub facility"):
        compile_authored_orders([_request(hubHandoffFacilityId="not.in.catalog")], CATALOGUE)
    # A charger is not a hub even when the author names it as the handoff.
    with pytest.raises(ValueError, match="not a hub"):
        compile_authored_orders(
            [_request(hubHandoffFacilityId="charger-x")], CATALOGUE
        )


def test_direct_vertiport_to_vertiport_parcel_bypass_is_rejected() -> None:
    with pytest.raises(ValueError, match="bypass is forbidden"):
        compile_authored_orders([_request(hubHandoffFacilityId=None)], CATALOGUE)


def test_unknown_endpoint_or_charger_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown source facility"):
        compile_authored_orders([_request(sourceFacilityId="ghost")], CATALOGUE)
    with pytest.raises(ValueError, match="unknown destination facility"):
        compile_authored_orders([_request(destinationFacilityId="ghost")], CATALOGUE)
    with pytest.raises(ValueError, match="does not permit cargo transfer"):
        compile_authored_orders(
            [_request(sourceFacilityId="charger-x")], CATALOGUE
        )
    with pytest.raises(ValueError, match="does not permit cargo transfer"):
        compile_authored_orders(
            [_request(destinationFacilityId="charger-x")], CATALOGUE
        )


def test_endpoint_hub_is_accepted_as_its_own_handoff() -> None:
    # destinationFacilityId is a hub and hubHandoffFacilityId is null: the
    # destination hub is the canonical hub ("documented as its own handoff").
    (order,) = compile_authored_orders(
        [_request(destinationFacilityId="hub-3", hubHandoffFacilityId=None)],
        CATALOGUE,
    )
    assert order.hub_handoff_facility_id == "hub-3"
    assert [leg.role for leg in order.itinerary().legs] == ["source_to_hub"]


def test_hub_storage_capacity_is_checked_against_the_catalogue() -> None:
    with pytest.raises(ValueError, match="exceeds hub.*storage capacity"):
        compile_authored_orders([_request(cargoKg=101)], CATALOGUE)


def test_facility_catalogue_coherence_is_enforced_by_the_single_lowering() -> None:
    # Facility capability coherence is enforced once, in compile_authored_facilities
    # (the single facility-shape parser); the order lowering never re-parses a
    # second facility shape.
    with pytest.raises(ValueError, match="must declare hub cargo"):
        compile_authored_facilities([_hub(cargo=None)])
    with pytest.raises(ValueError, match="cannot declare hub cargo"):
        compile_authored_facilities([_vertiport(cargo=_hub()["cargo"])])
    with pytest.raises(ValueError, match="standalone charger"):
        compile_authored_facilities([_charger(landing=_vertiport()["landing"])])


def test_empty_and_duplicate_requests() -> None:
    assert compile_authored_orders([], CATALOGUE) == ()
    with pytest.raises(ValueError, match="JSON array"):
        compile_authored_orders({}, CATALOGUE)
    request = _request()
    with pytest.raises(ValueError, match="unique"):
        compile_authored_orders([request, request], CATALOGUE)


def test_lowering_requires_the_canonical_facility_catalogue() -> None:
    # Every mandatory domain field derives from the authored order document plus
    # the canonical facility catalogue; no facility is inferred.
    with pytest.raises(ValueError, match="canonical FacilityCatalogue"):
        compile_authored_orders([_request()], None)
    with pytest.raises(ValueError, match="unique"):
        compile_authored_facilities([_vertiport(), _vertiport(id="facility-1")])


def test_compile_orders_accepts_only_the_canonical_catalogue_not_a_second_shape() -> None:
    # A raw frontend facility records array or mapping is owned by
    # compile_authored_facilities; compile_authored_orders consumes the canonical
    # FacilityCatalogue and never re-parses a second facility shape.
    with pytest.raises(ValueError, match="canonical FacilityCatalogue"):
        compile_authored_orders([_request()], FULL_V3_FACILITIES)
    with pytest.raises(ValueError, match="canonical FacilityCatalogue"):
        compile_authored_orders([_request()], {"facilities": FULL_V3_FACILITIES})


def test_authoring_view_holds_back_execution_metadata() -> None:
    # Authoring-role visibility: the authored order document carries demand
    # fields only. Order status, evidence, assignee, capacity and failure reason
    # are absent (held back until the lifecycle activates them); they never leak
    # into the authoring payload.
    authored = AuthoredOrderRequest.model_validate(_request())
    payload = authored.model_dump()
    for held_back in (
        "status",
        "version",
        "last_time_s",
        "assignee_id",
        "capacity_kg",
        "evidence_ref",
        "handoff_evidence_ref",
        "hub_handoff_version",
        "failure_reason",
    ):
        assert held_back not in payload
    state = compile_authored_orders([_request()], CATALOGUE)[0].initial_state()
    assert state.status is OrderStatus.CREATED
    assert state.assignee_id is None
    assert state.evidence_ref is None
    assert state.handoff_evidence_ref is None
    assert state.hub_handoff_version is None
