from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aero_bench.tasks.logistics.facilities import FacilityCatalogue, compile_authored_facilities
from aero_bench.tasks.logistics.fleet import (
    AircraftUnit,
    Fleet,
    FleetEntry,
    compile_authored_fleet,
    compile_authored_performance_profiles,
)


def _catalogue() -> FacilityCatalogue:
    """A canonical catalogue with a hub, a vertiport and a standalone charger."""
    return compile_authored_facilities(
        [
            {
                "id": "facility-1",
                "name": "cargo hub",
                "kind": "hub",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 0, "z": 0},
                "rotationDeg": 0,
                "widthM": 20,
                "depthM": 20,
                "heightM": 5,
                "landing": {"parkingSlots": 3, "movementsPerHour": 30},
                "cargo": {"storageCapacityKg": 10000, "throughputPerHourKg": 10000},
                "charging": {
                    "slots": 1,
                    "powerW": 1000,
                    "priceAmount": 1.2,
                    "priceCurrency": "CNY",
                    "priceUnit": "kWh",
                },
            },
            {
                "id": "vp.01",
                "name": "vertiport",
                "kind": "vertiport",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 10, "z": -20},
                "rotationDeg": 0,
                "widthM": 20,
                "depthM": 20,
                "heightM": 5,
                "landing": {"parkingSlots": 2, "movementsPerHour": 20},
                "cargo": None,
                "charging": None,
            },
            {
                "id": "ch.01",
                "name": "standalone charger",
                "kind": "charger",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 50, "z": -60},
                "rotationDeg": 0,
                "widthM": 6,
                "depthM": 4,
                "heightM": 3,
                "landing": None,
                "cargo": None,
                "charging": {
                    "slots": 1,
                    "powerW": 2000,
                    "priceAmount": 0.8,
                    "priceCurrency": "CNY",
                    "priceUnit": "kWh",
                },
            },
        ]
    )


def _fleet_entry(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "uav-1",
        "assetId": "model:holybro-x500",
        "count": 3,
        "homeFacilityId": "facility-1",
        "batteryWh": 600,
        "reserveRatio": 0.2,
        "maxPayloadKg": 2,
    }
    value.update(overrides)
    return value


def _profile(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "fleetEntryId": "uav-1",
        "sourceLabel": "operator",
        "provenance": "厂商样张",
        "aircraftBody": {"xM": 1.2, "yM": 0.5, "zM": 1.2},
        "cruiseSpeedMps": 15,
        "cruisePowerW": 120,
        "hoverPowerW": 210,
        "chargeEfficiency": 0.9,
    }
    value.update(overrides)
    return value


def test_current_v3_fleet_lowers_without_losing_identity_or_units() -> None:
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    (entry,) = fleet.entries
    assert entry.fleet_entry_id == "uav-1"
    assert entry.asset_id == "model:holybro-x500"
    assert entry.count == 3
    assert entry.home_facility_id == "facility-1"
    assert entry.battery_wh == pytest.approx(600)
    assert entry.reserve_ratio == pytest.approx(0.2)
    assert entry.max_payload_kg == pytest.approx(2)
    assert fleet.get("uav-1") == entry
    with pytest.raises(ValueError, match="unknown fleet entry"):
        fleet.require("missing")


def test_multi_entry_expansion_preserves_frontend_entry_order() -> None:
    catalogue = _catalogue()
    fleet = compile_authored_fleet(
        [
            _fleet_entry(id="uav-a", count=2),
            _fleet_entry(id="uav-b", count=1),
        ],
        catalogue,
    )
    units = fleet.expand_aircraft_units()
    assert [unit.aircraft_id for unit in units] == [
        "uav-a:1",
        "uav-a:2",
        "uav-b:1",
    ]


def test_aircraft_expansion_matches_the_frontend_planner_convention() -> None:
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    units = fleet.expand_aircraft_units()
    assert [unit.aircraft_id for unit in units] == ["uav-1:1", "uav-1:2", "uav-1:3"]
    for ordinal, unit in enumerate(units, start=1):
        assert unit.fleet_entry_id == "uav-1"
        assert unit.ordinal == ordinal
        assert unit.visual_asset_id == "model:holybro-x500"
        assert unit.home_facility_id == "facility-1"
        assert unit.capacity_wh == pytest.approx(600)
        assert unit.reserve_wh == pytest.approx(120)
        assert unit.initial_battery_wh == pytest.approx(600)
        assert unit.max_payload_kg == pytest.approx(2)
        # Visual asset identity is distinct from the aircraft runtime identity.
        assert unit.visual_asset_id != unit.aircraft_id
    # An explicitly built unit that breaks the planner id convention is rejected.
    with pytest.raises(ValueError, match="frontend planner id"):
        AircraftUnit(
            aircraft_id="uav-1:x",
            fleet_entry_id="uav-1",
            visual_asset_id="model:holybro-x500",
            ordinal=1,
            capacity_wh=600,
            reserve_wh=120,
            initial_battery_wh=600,
            max_payload_kg=2,
        )


def test_home_facility_references_are_bound_to_the_canonical_catalogue() -> None:
    catalogue = _catalogue()
    with pytest.raises(ValueError, match="unknown facility reference"):
        compile_authored_fleet([_fleet_entry(homeFacilityId="not-a-facility")], catalogue)
    # A null home facility is explicitly allowed (declared un-homed).
    fleet = compile_authored_fleet([_fleet_entry(homeFacilityId=None)], catalogue)
    assert fleet.entries[0].home_facility_id is None
    # Direct construction cannot bypass the catalogue binding either.
    with pytest.raises(ValueError, match="unknown facility reference"):
        Fleet(
            catalogue=catalogue,
            entries=(
                FleetEntry(
                    fleet_entry_id="uav-1",
                    asset_id="model:holybro-x500",
                    count=1,
                    home_facility_id="ghost",
                    battery_wh=600,
                    reserve_ratio=0.2,
                    max_payload_kg=2,
                ),
            ),
        )


def test_fleet_rejects_duplicates_and_non_array_input() -> None:
    catalogue = _catalogue()
    with pytest.raises(ValueError, match="unique"):
        compile_authored_fleet([_fleet_entry(), _fleet_entry()], catalogue)
    with pytest.raises(ValueError, match="JSON array"):
        compile_authored_fleet({}, catalogue)
    with pytest.raises(ValueError, match="FacilityCatalogue"):
        compile_authored_fleet([], object())


@pytest.mark.parametrize(
    "patch",
    [
        {"batteryWh": 0},
        {"batteryWh": -1},
        {"batteryWh": "600"},
        {"batteryWh": True},
        {"batteryWh": float("nan")},
        {"batteryWh": float("inf")},
        {"reserveRatio": -0.1},
        {"reserveRatio": 1.5},
        {"reserveRatio": "0.2"},
        {"reserveRatio": True},
        {"maxPayloadKg": -1},
        {"maxPayloadKg": "2"},
        {"maxPayloadKg": True},
        {"count": 0},
        {"count": 1.5},
        {"count": "3"},
        {"count": True},
        {"assetId": "holybro-x500"},
        {"assetId": "model:-x500"},
        {"assetId": "model:"},
        {"assetId": 1},
        {"id": "!bad"},
        {"id": "-leading"},
    ],
)
def test_fleet_rejects_bad_payload_battery_and_identity_params(patch: dict[str, object]) -> None:
    with pytest.raises((ValueError, ValidationError)):
        compile_authored_fleet([_fleet_entry(**patch)], _catalogue())


@pytest.mark.parametrize(
    "patch",
    [
        {"missing": "batteryWh"},
        {"missing": "assetId"},
        {"missing": "maxPayloadKg"},
        {"missing": "count"},
        {"missing": "reserveRatio"},
        {"missing": "homeFacilityId"},
    ],
)
def test_fleet_rejects_absent_required_params(patch: dict[str, object]) -> None:
    value = _fleet_entry()
    missing = str(patch["missing"])
    del value[missing]
    with pytest.raises(ValidationError):
        compile_authored_fleet([value], _catalogue())


def test_zero_payload_is_declared_not_inferred() -> None:
    # The frontend allows maxPayloadKg == 0; a zero-payload aircraft is valid.
    fleet = compile_authored_fleet([_fleet_entry(maxPayloadKg=0)], _catalogue())
    assert fleet.require("uav-1").max_payload_kg == pytest.approx(0)


def test_count_accepts_safe_integer_floats_like_js_positive_integer() -> None:
    # Frontend positiveInteger uses Number.isSafeInteger, so 2.0 is valid while
    # 1.5 / strings / bools / >2^53-1 are rejected (never coerced).
    fleet = compile_authored_fleet([_fleet_entry(count=2.0)], _catalogue())
    assert fleet.require("uav-1").count == 2
    assert len(fleet.expand_aircraft_units()) == 2


@pytest.mark.parametrize(
    "bad_count",
    [
        True,
        "3",
        1.5,
        float("nan"),
        float("inf"),
        (1 << 53),  # 9007199254740992 > Number.MAX_SAFE_INTEGER
    ],
)
def test_count_rejects_bool_string_nonintegral_nonfinite_and_unsafe(bad_count: object) -> None:
    with pytest.raises((ValueError, ValidationError)):
        compile_authored_fleet([_fleet_entry(count=bad_count)], _catalogue())


def test_declared_performance_profiles_lower_with_explicit_provenance() -> None:
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    (profile,) = compile_authored_performance_profiles([_profile()], fleet)
    assert profile.fleet_entry_id == "uav-1"
    assert profile.source_label == "operator"
    assert profile.provenance == "厂商样张"
    assert profile.aircraft_body.x_m == pytest.approx(1.2)
    assert profile.aircraft_body.y_m == pytest.approx(0.5)
    assert profile.aircraft_body.z_m == pytest.approx(1.2)
    assert profile.cruise_speed_mps == pytest.approx(15)
    assert profile.cruise_power_w == pytest.approx(120)
    assert profile.hover_power_w == pytest.approx(210)
    assert profile.charge_efficiency == pytest.approx(0.9)


def test_physics_is_never_inferred_from_geometry() -> None:
    # The declared body dimensions are carried verbatim; no GLB derivation exists.
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    (profile,) = compile_authored_performance_profiles([_profile()], fleet)
    body = profile.aircraft_body
    assert (body.x_m, body.y_m, body.z_m) == (1.2, 0.5, 1.2)
    # A profile cannot carry a GLB/display-scale field at all.
    with pytest.raises((ValueError, ValidationError)):
        compile_authored_performance_profiles([_profile(glbScale=2.0)], fleet)


def test_performance_profiles_must_reference_existing_fleet_entries() -> None:
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    with pytest.raises(ValueError, match="unknown fleet entry"):
        compile_authored_performance_profiles([_profile(fleetEntryId="ghost")], fleet)
    with pytest.raises(ValueError, match="unknown fleet entry"):
        fleet.require("ghost")


@pytest.mark.parametrize(
    "patch",
    [
        {"chargeEfficiency": 0},
        {"chargeEfficiency": -0.1},
        {"chargeEfficiency": 1.5},
        {"chargeEfficiency": "0.9"},
        {"chargeEfficiency": True},
        {"cruisePowerW": 0},
        {"cruisePowerW": -5},
        {"cruisePowerW": "120"},
        {"cruisePowerW": float("nan")},
        {"hoverPowerW": 0},
        {"hoverPowerW": -1},
        {"cruiseSpeedMps": 0},
        {"cruiseSpeedMps": -1},
        {"provenance": ""},
        {"provenance": 3},
        {"sourceLabel": ""},
        {"sourceLabel": True},
        {"aircraftBody": {"xM": 0, "yM": 0.5, "zM": 1.2}},
        {"aircraftBody": {"xM": 1.2, "yM": -1, "zM": 1.2}},
        {"aircraftBody": {"xM": 1.2, "yM": 0.5, "zM": "1.2"}},
        {"aircraftBody": {"xM": 1.2, "yM": 0.5}},
    ],
)
def test_performance_profiles_reject_bad_declared_physics(patch: dict[str, object]) -> None:
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    with pytest.raises((ValueError, ValidationError)):
        compile_authored_performance_profiles([_profile(**patch)], fleet)


def test_performance_profiles_reject_duplicate_fleet_entry_ids() -> None:
    fleet = compile_authored_fleet([_fleet_entry()], _catalogue())
    with pytest.raises(ValueError, match="unique"):
        compile_authored_performance_profiles([_profile(), _profile()], fleet)


def test_authored_fleet_rejects_extra_and_renamed_keys() -> None:
    catalogue = _catalogue()
    with pytest.raises(ValidationError, match="Extra inputs"):
        # extra=forbid rejects unknown authoring keys rather than ignoring them.
        compile_authored_fleet([_fleet_entry(extraKey=True)], catalogue)
    with pytest.raises(ValidationError):
        compile_authored_fleet([_fleet_entry(payloadKg=9)], catalogue)


def test_aircraft_reserve_is_a_planning_threshold_not_a_physical_floor() -> None:
    fleet = compile_authored_fleet(
        [_fleet_entry(batteryWh=600, reserveRatio=0.5, count=1)], _catalogue()
    )
    (unit,) = fleet.expand_aircraft_units()
    assert unit.reserve_wh == pytest.approx(300)
    # An initial charge below the declared reserve is a valid physical state
    # (a low-battery aircraft returning to a charger is exactly that case).
    low = AircraftUnit(
        aircraft_id="uav-1:1",
        fleet_entry_id="uav-1",
        visual_asset_id="model:holybro-x500",
        ordinal=1,
        capacity_wh=600,
        reserve_wh=300,
        initial_battery_wh=200,
        max_payload_kg=2,
    )
    assert low.initial_battery_wh == pytest.approx(200)
    assert low.initial_battery_wh < low.reserve_wh
    # Physical bounds are [0, capacity]: over-capacity and negative rejections stay.
    with pytest.raises(ValueError, match="exceed capacity"):
        AircraftUnit(
            aircraft_id="uav-1:1",
            fleet_entry_id="uav-1",
            visual_asset_id="model:holybro-x500",
            ordinal=1,
            capacity_wh=600,
            reserve_wh=300,
            initial_battery_wh=601,
            max_payload_kg=2,
        )
    with pytest.raises(ValueError, match="nonnegative"):
        AircraftUnit(
            aircraft_id="uav-1:1",
            fleet_entry_id="uav-1",
            visual_asset_id="model:holybro-x500",
            ordinal=1,
            capacity_wh=600,
            reserve_wh=300,
            initial_battery_wh=-1,
            max_payload_kg=2,
        )
