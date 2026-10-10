from __future__ import annotations

import math

import pytest

from aero_bench.tasks.logistics.facilities import (
    ChargingCapability,
    FacilityCapabilities,
    FacilityCatalogue,
    LandingCapability,
    compile_authored_facilities,
)


def _vertiport(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "vp.01",
        "name": "vertiport 1",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 10, "z": -20},
        "rotationDeg": 0,
        "widthM": 20,
        "depthM": 20,
        "heightM": 5,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    }
    value.update(overrides)
    return value


def _hub(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "hub.01",
        "name": "cargo hub 1",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 30, "z": -40},
        "rotationDeg": 0,
        "widthM": 18,
        "depthM": 14,
        "heightM": 5.2,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 10000, "throughputPerHourKg": 10000},
        "charging": None,
    }
    value.update(overrides)
    return value


def _charger(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "ch.01",
        "name": "charger 1",
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


def test_current_v3_facilities_lower_without_losing_units_or_identity() -> None:
    catalogue = compile_authored_facilities([_vertiport(), _hub(), _charger()])
    assert [f.facility_id for f in catalogue.facilities] == ["vp.01", "hub.01", "ch.01"]
    landing = catalogue.require("vp.01").landing
    assert landing is not None
    assert landing.parking_slots == 3
    assert landing.movements_per_hour == 30.0
    cargo = catalogue.require("hub.01").cargo
    assert cargo is not None
    assert cargo.storage_capacity_kg == 10000.0
    assert cargo.throughput_per_hour_kg == 10000.0
    charging = catalogue.require("ch.01").charging
    assert charging is not None
    assert charging.slots == 1
    assert charging.power_w == 5000.0
    assert charging.price_amount == 1.2
    assert charging.price_currency == "CNY"
    assert charging.price_unit == "kWh"


def test_parking_slots_stay_apart_from_movements_per_hour() -> None:
    compiled = compile_authored_facilities(
        [_vertiport(landing={"parkingSlots": 2, "movementsPerHour": 240})]
    ).require("vp.01").landing
    assert compiled is not None
    assert compiled.parking_slots == 2
    assert compiled.movements_per_hour == 240.0
    # A charge slot reservation is not derived from either quantity.
    charging = ChargingCapability(
        slots=3, power_w=6000, price_amount=0.5, price_currency="USD", price_unit="kWh"
    )
    assert charging.slots == 3


@pytest.mark.parametrize(
    "patch",
    [
        {"widthM": math.nan},
        {"heightM": math.inf},
        {"depthM": -1},
        {"chargeSlots": 1},  # renamed charging field
        {"foo": "bar"},
    ],
)
def test_unknown_and_nonfinite_facility_keys_are_rejected(patch: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Extra inputs|finite|positive"):
        compile_authored_facilities([{**_vertiport(), **patch}])


@pytest.mark.parametrize(
    "patch",
    [
        {"parkingSlots": True},
        {"movementsPerHour": True},
        {"storageCapacityKg": False},
        {"powerW": True},
        {"priceAmount": False},
        {"widthM": True},
        {"slots": True},
    ],
)
def test_numeric_capabilities_reject_boolean_placeholders(
    patch: dict[str, object],
) -> None:
    if "parkingSlots" in patch:
        base = _vertiport()
        item = {**base, "landing": {**base["landing"], **patch}}  # type: ignore[arg-type]
    elif "movementsPerHour" in patch:
        base = _vertiport()
        item = {**base, "landing": {**base["landing"], **patch}}  # type: ignore[arg-type]
    elif "storageCapacityKg" in patch:
        base = _hub()
        item = {**base, "cargo": {**base["cargo"], **patch}}  # type: ignore[arg-type]
    elif "powerW" in patch or "priceAmount" in patch or "slots" in patch:
        base = _charger()
        item = {**base, "charging": {**base["charging"], **patch}}  # type: ignore[arg-type]
    else:
        item = {**_vertiport(), **patch}
    with pytest.raises(ValueError, match="boolean|must be"):
        compile_authored_facilities([item])


@pytest.mark.parametrize("patch", [{"parkingSlots": math.nan}, {"parkingSlots": 2.5}])
def test_parking_slots_must_be_positive_integers(patch: dict[str, object]) -> None:
    base = _vertiport()
    with pytest.raises(ValueError):
        compile_authored_facilities(
            [{**base, "landing": {**base["landing"], **patch}}]  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"priceUnit": "MWh"},
        {"priceUnit": "Wh"},
        {"priceUnit": "kwh"},
    ],
)
def test_charging_price_unit_must_be_exactly_kwh(patch: dict[str, object]) -> None:
    base = _charger()
    with pytest.raises(ValueError, match="kWh"):
        compile_authored_facilities(
            [{**base, "charging": {**base["charging"], **patch}}]  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "price_currency",
    ["cny", "US1", "CN", "CNYA", "1US"],
)
def test_currency_code_must_be_three_uppercase_letters(price_currency: str) -> None:
    base = _charger()
    with pytest.raises(ValueError, match="currency"):
        compile_authored_facilities(
            [
                {
                    **base,
                    "charging": {
                        **base["charging"],  # type: ignore[arg-type]
                        "priceCurrency": price_currency,
                    },
                }
            ]
        )


def test_rooftop_placement_must_be_a_vertiport_with_building_binding() -> None:
    rooftop = _vertiport(
        id="vp.roof",
        placement="rooftop",
        buildingId="building.42",
        supportHeightM=42.0,
    )
    compiled = compile_authored_facilities([rooftop]).require("vp.roof")
    assert compiled.placement == "rooftop"
    assert compiled.building_id == "building.42"
    assert compiled.support_height_m == 42.0

    with pytest.raises(ValueError, match="rooftop"):
        compile_authored_facilities([_hub(id="hub.2", placement="rooftop")])
    with pytest.raises(ValueError, match="rooftop"):
        compile_authored_facilities([_charger(id="ch.2", placement="rooftop")])
    with pytest.raises(ValueError, match="building_id"):
        compile_authored_facilities([_vertiport(id="vp.3", placement="rooftop")])


def test_ground_placement_cannot_bind_a_building() -> None:
    for item in (
        _vertiport(buildingId="building.1"),
        _vertiport(supportHeightM=5.0),
        _hub(buildingId="building.2"),
    ):
        with pytest.raises(ValueError, match="ground"):
            compile_authored_facilities([item])


def test_hub_must_be_ground_with_landing_and_cargo() -> None:
    with pytest.raises(ValueError, match="landing"):
        compile_authored_facilities([_hub(landing=None)])
    with pytest.raises(ValueError, match="cargo"):
        compile_authored_facilities([_hub(cargo=None)])
    with pytest.raises(ValueError, match="vertiport"):
        compile_authored_facilities([_hub(placement="rooftop", buildingId="b.1", supportHeightM=1)])


def test_charger_is_only_ground_charging() -> None:
    with pytest.raises(ValueError, match="charging"):
        compile_authored_facilities([_charger(charging=None)])
    with pytest.raises(ValueError, match="standalone charger"):
        compile_authored_facilities([_charger(landing=_vertiport()["landing"])])


def test_vertiport_must_declare_landing_and_no_cargo() -> None:
    with pytest.raises(ValueError, match="landing"):
        compile_authored_facilities([_vertiport(landing=None)])
    with pytest.raises(ValueError, match="cargo"):
        compile_authored_facilities([_vertiport(cargo=_hub()["cargo"])])


def test_duplicate_facility_ids_and_bad_references_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        compile_authored_facilities([_vertiport(), _vertiport(id="vp.01")])
    with pytest.raises(ValueError):
        compile_authored_facilities([_vertiport(id="bad id")])


def test_invalid_building_reference_pattern_is_rejected() -> None:
    with pytest.raises(ValueError):
        compile_authored_facilities(
            [_vertiport(id="vp.r", placement="rooftop", buildingId="building 42", supportHeightM=10)]
        )
    with pytest.raises(ValueError):
        compile_authored_facilities(
            [_vertiport(id="vp.r2", placement="rooftop", buildingId="-bad", supportHeightM=10)]
        )


def test_nested_unknown_keys_are_rejected() -> None:
    base = _charger()
    with pytest.raises(ValueError, match="Extra inputs"):
        compile_authored_facilities(
            [
                {
                    **base,
                    "charging": {
                        **base["charging"],  # type: ignore[arg-type]
                        "tariffUrl": "http://example.invalid/tariff",
                    },
                }
            ]
        )


def test_catalogue_lookup_and_require() -> None:
    catalogue = compile_authored_facilities([_vertiport()])
    assert catalogue.get("vp.01") is not None
    assert catalogue.get("missing") is None
    with pytest.raises(ValueError, match="unknown facility reference"):
        catalogue.require("missing")


def test_compile_rejects_non_list_and_empty_list() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        compile_authored_facilities({})
    assert compile_authored_facilities([]).facilities == ()


@pytest.mark.parametrize(
    ("item", "omitted"),
    [
        pytest.param(_vertiport(), "buildingId", id="buildingId"),
        pytest.param(_vertiport(), "supportHeightM", id="supportHeightM"),
        pytest.param(_vertiport(), "landing", id="landing"),
        pytest.param(_vertiport(), "cargo", id="cargo"),
        pytest.param(_charger(), "charging", id="charging"),
    ],
)
def test_omitting_a_required_nullable_authored_field_is_rejected(
    item: dict[str, object], omitted: str
) -> None:
    # Frontend v3 requires every key explicitly; a missing nullable field is a
    # strict-parity failure even though an explicit null is legal.
    del item[omitted]
    with pytest.raises(ValueError):
        compile_authored_facilities([item])


def test_required_nullable_authored_fields_accept_explicit_null() -> None:
    # The same fields are legal when present as null on a kind that allows it:
    # every v3 key is required, but null is an accepted explicit value.
    catalogue = compile_authored_facilities(
        [
            _vertiport(buildingId=None, supportHeightM=None, cargo=None, charging=None),
            _hub(buildingId=None, supportHeightM=None, charging=None),
            _charger(buildingId=None, supportHeightM=None, landing=None, cargo=None),
        ]
    )
    assert [f.facility_id for f in catalogue.facilities] == ["vp.01", "hub.01", "ch.01"]


def test_declared_geometry_is_not_falsely_verified() -> None:
    # The backend lowering carries the authored footprint as declared only; it
    # does not re-derive the frontend pad-layout geometry proof. A vertiport
    # whose parking count would exceed the frontend single-row fit is accepted
    # here because spatial verification is a frontend/scene concern.
    composed = _vertiport(id="vp.geo", widthM=12)
    landing = {**composed["landing"], "parkingSlots": 999}  # type: ignore[dict-item]
    compiled = compile_authored_facilities([{**composed, "landing": landing}]).require("vp.geo")
    assert compiled.width_m == 12.0
    assert compiled.landing is not None
    assert compiled.landing.parking_slots == 999
    assert compiled.position.x == 10.0
    assert compiled.rotation_deg == 0.0


def test_landing_capability_never_implies_more_pads() -> None:
    landing = LandingCapability(parking_slots=1, movements_per_hour=120.0)
    assert landing.parking_slots == 1
    assert landing.movements_per_hour == 120.0


def test_canonical_facility_model_is_frozen_and_extra_forbidden() -> None:
    facility = compile_authored_facilities([_vertiport()]).require("vp.01")
    with pytest.raises(ValueError):
        FacilityCapabilities.model_validate({**facility.model_dump(), "extra": 1})
    landing = facility.landing
    assert landing is not None
    with pytest.raises(ValueError, match="Extra inputs"):
        LandingCapability.model_validate({**landing.model_dump(), "bogus": 1})
