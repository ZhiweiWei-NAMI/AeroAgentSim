from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aero_bench.tasks.logistics.energy import (
    BatteryState,
    EnergyActivitySample,
    EnergyRunResult,
    simulate_energy,
)
from aero_bench.tasks.logistics.facilities import FacilityCatalogue, compile_authored_facilities
from aero_bench.tasks.logistics.fleet import (
    AircraftUnit,
    compile_authored_fleet,
    compile_authored_performance_profiles,
)


def _catalogue() -> FacilityCatalogue:
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


def _fleet(catalogue: FacilityCatalogue, **entry_overrides: object):
    value: dict[str, object] = {
        "id": "uav-1",
        "assetId": "model:holybro-x500",
        "count": 1,
        "homeFacilityId": "facility-1",
        "batteryWh": 600,
        "reserveRatio": 0.2,
        "maxPayloadKg": 2,
    }
    value.update(entry_overrides)
    return compile_authored_fleet([value], catalogue)



def _truck(catalogue: FacilityCatalogue):
    """A single truck fleet entry for charge/cost boundary tests."""
    return _fleet(
        catalogue,
        id="truck.1",
        assetId="model:heavy-uav-2",
        homeFacilityId="ch.01",
        batteryWh=1000,
        reserveRatio=0.0,
        count=1,
    )

def _profile(fleet, **overrides: object):
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
    (profile,) = compile_authored_performance_profiles([value], fleet)
    return profile


def test_battery_state_is_immutable_and_stays_within_bounds() -> None:
    state = BatteryState(battery_wh=600, capacity_wh=600, reserve_wh=120)
    assert state.available_wh == pytest.approx(480)
    assert state.charging_room_wh == pytest.approx(0)
    with pytest.raises(ValidationError, match="frozen"):
        state.battery_wh = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="exceed capacity"):
        BatteryState(battery_wh=700, capacity_wh=600, reserve_wh=120)
    # A charge below the declared reserve is a valid physical state; the
    # reserve is a planning safety threshold, not the battery's physical floor.
    low = BatteryState(battery_wh=100, capacity_wh=600, reserve_wh=120)
    assert low.available_wh == pytest.approx(-20)
    assert low.charging_room_wh == pytest.approx(500)
    with pytest.raises(ValueError, match="nonnegative"):
        BatteryState(battery_wh=-1, capacity_wh=600, reserve_wh=120)
    with pytest.raises(ValueError, match="reserve_wh cannot exceed"):
        BatteryState(battery_wh=300, capacity_wh=600, reserve_wh=700)
    with pytest.raises(ValueError, match="finite"):
        BatteryState(battery_wh=float("nan"), capacity_wh=600, reserve_wh=120)
    with pytest.raises(ValueError, match="cannot be a boolean"):
        BatteryState(battery_wh=True, capacity_wh=600, reserve_wh=120)
    with pytest.raises(ValueError, match="must be positive"):
        BatteryState(battery_wh=0, capacity_wh=0, reserve_wh=0)


def test_sample_model_contract_is_explicit() -> None:
    # A ground charge must name its declared charging facility.
    with pytest.raises(ValueError, match="requires a declared facility_id"):
        EnergyActivitySample(activity="ground_charge", start_s=0, end_s=100)
    # Non-charge activities cannot carry a facility reference.
    with pytest.raises(ValueError, match="cannot carry a facility_id"):
        EnergyActivitySample(activity="cruise", start_s=0, end_s=100, facility_id="ch.01")
    # Empty and reversed intervals are rejected, never clamped or dropped.
    with pytest.raises(ValueError, match="strictly follow"):
        EnergyActivitySample(activity="cruise", start_s=100, end_s=100)
    with pytest.raises(ValueError, match="strictly follow"):
        EnergyActivitySample(activity="cruise", start_s=200, end_s=100)
    with pytest.raises(ValueError, match="finite"):
        EnergyActivitySample(activity="hover", start_s=float("nan"), end_s=100)
    with pytest.raises(ValueError, match="nonnegative"):
        EnergyActivitySample(activity="hover", start_s=-1, end_s=100)
    with pytest.raises(ValueError, match="cannot be a boolean"):
        EnergyActivitySample(activity="hover", start_s=True, end_s=100)


def test_cruise_and_hover_consume_only_declared_watts() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    cruise = simulate_energy(
        aircraft,
        profile,
        catalogue,
        [EnergyActivitySample(activity="cruise", start_s=0, end_s=600)],
    )
    assert cruise.cruise_energy_wh == pytest.approx(20)
    assert cruise.hover_energy_wh == pytest.approx(0)
    assert cruise.final_battery_wh == pytest.approx(580)
    assert cruise.total_duration_s == pytest.approx(600)

    hover = simulate_energy(
        aircraft,
        profile,
        catalogue,
        [EnergyActivitySample(activity="hover", start_s=0, end_s=3600)],
    )
    assert hover.hover_energy_wh == pytest.approx(210)
    assert hover.cruise_energy_wh == pytest.approx(0)
    assert hover.final_battery_wh == pytest.approx(390)


def test_reserve_is_a_declared_planning_threshold_not_a_physical_floor() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    # 120 W cruise for 14400 s draws exactly 480 Wh: 600 - 480 == reserve (120),
    # which is exactly on the line and therefore not "breached".
    at_reserve = simulate_energy(
        aircraft,
        profile,
        catalogue,
        [EnergyActivitySample(activity="cruise", start_s=0, end_s=14400)],
    )
    assert at_reserve.final_battery_wh == pytest.approx(120)
    assert at_reserve.reserve_breached is False
    # Crossing the line is physically feasible; it is recorded, never rejected.
    crossing = simulate_energy(
        aircraft,
        profile,
        catalogue,
        [
            EnergyActivitySample(activity="cruise", start_s=0, end_s=14400),
            EnergyActivitySample(activity="hover", start_s=14400, end_s=14401),
        ],
    )
    assert crossing.final_battery_wh == pytest.approx(120 - 210 / 3600, rel=1e-9)
    assert crossing.reserve_breached is True
    assert crossing.reserve_breached_steps == 1
    assert crossing.steps[1].reserve_breached is True


def test_zero_reserve_underflow_is_rejected() -> None:
    catalogue = _catalogue()
    unit = AircraftUnit(
        aircraft_id="uav-1:1",
        fleet_entry_id="uav-1",
        visual_asset_id="model:holybro-x500",
        ordinal=1,
        capacity_wh=50,
        reserve_wh=0,
        initial_battery_wh=10,
        max_payload_kg=1,
    )
    profile = _profile(_fleet(catalogue))
    # 120 W for 600 s draws 20 Wh > the 10 Wh available: physical underflow.
    with pytest.raises(ValueError, match="underflow"):
        simulate_energy(
            unit,
            profile,
            catalogue,
            [EnergyActivitySample(activity="cruise", start_s=0, end_s=600)],
        )


def test_charge_applies_declared_power_and_efficiency_to_capacity() -> None:
    catalogue = _catalogue()
    fleet = _truck(catalogue)
    (unit,) = fleet.expand_aircraft_units()
    profile = _profile(fleet, fleetEntryId="truck.1")
    # facility-1 charges at 1000 W for 1 h: 1000 Wh drawn from the grid and
    # 900 Wh credited to the battery at 0.9 efficiency.
    result = simulate_energy(
        unit,
        profile,
        catalogue,
        [
            EnergyActivitySample(
                activity="ground_charge",
                start_s=0,
                end_s=3600,
                facility_id="facility-1",
            )
        ],
        initial_wh=100,
    )
    assert result.supply_energy_wh == pytest.approx(1000)
    assert result.stored_energy_wh == pytest.approx(900)
    assert result.final_battery_wh == pytest.approx(1000)
    assert result.net_energy_wh == pytest.approx(900)
    assert len(result.steps) == 1
    samples = result.steps[0]
    assert samples.energy_delta_wh == pytest.approx(900)  # battery delta = stored
    assert samples.supply_wh == pytest.approx(1000)  # billed base = supply
    assert samples.battery_before_wh == pytest.approx(100)
    assert samples.battery_after_wh == pytest.approx(1000)


def test_charge_beyond_capacity_is_rejected_not_clamped() -> None:
    catalogue = _catalogue()
    fleet = _truck(catalogue)
    (unit,) = fleet.expand_aircraft_units()
    profile = _profile(fleet, fleetEntryId="truck.1")
    with pytest.raises(ValueError, match="refusing to clamp"):
        simulate_energy(
            unit,
            profile,
            catalogue,
            [
                EnergyActivitySample(
                    activity="ground_charge",
                    start_s=0,
                    end_s=3601,
                    facility_id="facility-1",
                )
            ],
            initial_wh=100,
        )


def test_ground_charge_requires_a_declared_charging_capability() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    with pytest.raises(ValueError, match="without a declared charging capability"):
        simulate_energy(
            aircraft,
            profile,
            catalogue,
            [
                EnergyActivitySample(
                    activity="ground_charge",
                    start_s=0,
                    end_s=3600,
                    facility_id="vp.01",
                )
            ],
        )


def test_cost_is_a_declared_price_planning_estimate_not_evidence() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    result = simulate_energy(
        aircraft,
        profile,
        catalogue,
        [EnergyActivitySample(activity="hover", start_s=0, end_s=3600)],
    )
    # Discharge-only runs carry no cost and no provider/evidence claims.
    assert result.costs == ()
    assert result.estimate_label == (
        "deterministic declared-parameter simulation; not measured telemetry"
    )
    truck = _truck(catalogue)
    (truck_unit,) = truck.expand_aircraft_units()
    truck_profile = _profile(truck, fleetEntryId="truck.1")
    charged = simulate_energy(
        truck_unit,
        truck_profile,
        catalogue,
        [
            EnergyActivitySample(
                activity="ground_charge",
                start_s=0,
                end_s=3600,
                facility_id="facility-1",
            )
        ],
        initial_wh=100,
    )
    (cost,) = charged.costs
    assert cost.facility_id == "facility-1"
    assert cost.supply_wh == pytest.approx(1000)  # 1000 W x 1 h = 1000 Wh grid
    assert cost.stored_wh == pytest.approx(900)  # battery-credited at 0.9
    assert cost.amount == pytest.approx(1.20)  # 1.0 kWh x 1.2 CNY/kWh
    assert cost.currency == "CNY"
    assert cost.unit == "kWh"
    assert cost.estimate_label == "declared-price planning estimate"
    # Energy and cost are separate result fields; no battery telemetry field exists.
    assert "telemetry" not in charged.model_dump()


def test_sample_intervals_must_be_time_ordered_and_non_overlapping() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    overlapping = [
        EnergyActivitySample(activity="cruise", start_s=0, end_s=100),
        EnergyActivitySample(activity="hover", start_s=90, end_s=190),
    ]
    with pytest.raises(ValueError, match="time-ordered"):
        simulate_energy(aircraft, profile, catalogue, overlapping)
    backwards = [
        EnergyActivitySample(activity="cruise", start_s=100, end_s=200),
        EnergyActivitySample(activity="hover", start_s=50, end_s=150),
    ]
    with pytest.raises(ValueError, match="time-ordered"):
        simulate_energy(aircraft, profile, catalogue, backwards)
    # Contiguous intervals (a later start equals the previous end) are allowed.
    contiguous = [
        EnergyActivitySample(activity="cruise", start_s=0, end_s=100),
        EnergyActivitySample(activity="hover", start_s=100, end_s=200),
    ]
    result = simulate_energy(aircraft, profile, catalogue, contiguous)
    assert result.total_duration_s == pytest.approx(200)
    # Gaps between intervals are allowed; durations still come from the samples.
    gapped = [
        EnergyActivitySample(activity="cruise", start_s=0, end_s=100),
        EnergyActivitySample(activity="hover", start_s=300, end_s=400),
    ]
    assert simulate_energy(aircraft, profile, catalogue, gapped).total_duration_s == pytest.approx(400)


def test_empty_sample_run_returns_declared_initial_state() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    empty = simulate_energy(aircraft, profile, catalogue, [])
    assert empty.samples_processed == 0
    assert empty.final_battery_wh == pytest.approx(aircraft.initial_battery_wh)
    assert empty.cruise_energy_wh == pytest.approx(0)
    assert empty.supply_energy_wh == pytest.approx(0)
    assert empty.stored_energy_wh == pytest.approx(0)
    assert empty.reserve_breached is False
    assert empty.total_duration_s == pytest.approx(0)
    assert empty.steps == ()


def test_explicit_initial_wh_is_respected_and_bounded() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    result = simulate_energy(aircraft, profile, catalogue, [], initial_wh=200)
    assert result.initial_wh == pytest.approx(200)
    assert result.final_battery_wh == pytest.approx(200)
    # Below the declared reserve is a valid physical start; it is flagged.
    low = simulate_energy(aircraft, profile, catalogue, [], initial_wh=119)
    assert low.final_battery_wh == pytest.approx(119)
    assert low.reserve_breached is True
    with pytest.raises(ValueError, match="nonnegative"):
        simulate_energy(aircraft, profile, catalogue, [], initial_wh=-1)
    with pytest.raises(ValueError, match="exceed capacity"):
        simulate_energy(aircraft, profile, catalogue, [], initial_wh=601)


def test_profile_and_aircraft_must_belong_to_the_same_fleet_entry() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    other = _fleet(catalogue, id="uav-2")
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(other, fleetEntryId="uav-2")
    with pytest.raises(ValueError, match="must belong to the aircraft's fleet entry"):
        simulate_energy(aircraft, profile, catalogue, [])


def test_energy_steps_use_supplied_times_verbatim_and_never_positions() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    supplied = [
        EnergyActivitySample(activity="cruise", start_s=123.0, end_s=723.0),
        EnergyActivitySample(activity="hover", start_s=723.0, end_s=1023.0),
    ]
    result = simulate_energy(aircraft, profile, catalogue, supplied)
    (first, second) = result.steps
    assert first.start_s == pytest.approx(123.0)
    assert first.end_s == pytest.approx(723.0)
    assert second.start_s == pytest.approx(723.0)
    assert second.end_s == pytest.approx(1023.0)
    assert result.total_duration_s == pytest.approx(900)
    # The simulation never generates positions or timestamp grids.
    assert "position" not in result.model_dump()
    assert all("position" not in step.model_dump() for step in result.steps)


def test_mixed_run_keeps_final_state_within_bounds_and_net_energy_consistent() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    result = simulate_energy(
        aircraft,
        profile,
        catalogue,
        [
            EnergyActivitySample(activity="cruise", start_s=0, end_s=600),      # -20 Wh
            EnergyActivitySample(activity="hover", start_s=600, end_s=1200),   # -35 Wh
            EnergyActivitySample(
                activity="ground_charge",
                start_s=1200,
                end_s=1260,
                facility_id="facility-1",
            ),  # +15 Wh stored (1000 W * 0.9 * 60 s / 3600); 16.667 Wh supply
        ],
    )
    supply_wh = 1000.0 * 60.0 / 3600.0
    assert result.cruise_energy_wh == pytest.approx(20)
    assert result.hover_energy_wh == pytest.approx(35)
    assert result.discharge_energy_wh == pytest.approx(55)
    assert result.supply_energy_wh == pytest.approx(supply_wh)
    assert result.stored_energy_wh == pytest.approx(15)
    assert result.net_energy_wh == pytest.approx(-40)
    assert result.final_battery_wh == pytest.approx(aircraft.initial_battery_wh - 40)
    assert result.reserve_wh <= result.final_battery_wh <= result.capacity_wh
    assert result.reserve_breached is False


def test_energy_results_are_frozen_canonical_models() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    (aircraft,) = fleet.expand_aircraft_units()
    profile = _profile(fleet)
    result = simulate_energy(aircraft, profile, catalogue, [])
    assert isinstance(result, EnergyRunResult)
    with pytest.raises(ValidationError, match="frozen"):
        result.final_battery_wh = 0  # type: ignore[misc]
