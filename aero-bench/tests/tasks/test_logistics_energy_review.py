"""Regression expectations for the fleet/energy review findings (DSH fixes).

The original review file asserted the three findings as *counterexamples* (the
violating behaviour was expected). After the fixes this file is converted into
plain regression expectations: every test here now asserts the *desired*
behaviour that the fixes establish:

1. The frontend declared fleet fields strictly match ``AuthoredFleetEntry``
   (``frontend/src/city-selected-scenario.ts:172-184``): ``homeFacilityId``
   must be *explicitly present* even when null, and ``count`` follows the JS
   ``positiveInteger`` rule (``Number.isSafeInteger``) so ``2.0`` is valid,
   while bools / strings / non-integral / non-finite / unsafe (>2^53-1)
   values are rejected and never coerced.
2. ``ground_charge`` separates the grid-side supply Wh
   (``power_w * duration / 3600``) from the battery-credited stored Wh
   (``supply_wh * efficiency``). The battery delta uses ``stored_wh``; the
   declared per-kWh tariff prices ``supply_wh`` (meter-side declared
   electricity). ``EnergyRunResult`` and ``EnergyChargeCost`` expose both
   quantities explicitly and never claim a measured meter read.
3. ``reserve_wh`` is a planning safety threshold, not the physical battery
   minimum: initial charge may sit in ``[0, capacity_wh]`` including below the
   declared reserve, physically feasible cruise/hover may cross the reserve
   line (recorded as ``reserve_breached``), and only a physical underflow below
   ``0 Wh`` is rejected. A low-reserve battery must be able to charge.

Every number here comes from the deterministic declared-parameter simulation
arithmetic in ``aero_bench/tasks/logistics/energy.py``; nothing is measured
telemetry and no provider is executed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.tasks.logistics.energy import (
    BatteryState,
    EnergyActivitySample,
    EnergyChargeCost,
    EnergyRunResult,
    apply_energy_sample,
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
                "id": "ch.01",
                "name": "standalone charger 2kW",
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
                    "slots": 2,
                    "powerW": 2000,
                    "priceAmount": 0.8,
                    "priceCurrency": "CNY",
                    "priceUnit": "kWh",
                },
            },
            {
                "id": "ch.02",
                "name": "standalone charger 1kW",
                "kind": "charger",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 60, "z": -60},
                "rotationDeg": 0,
                "widthM": 6,
                "depthM": 4,
                "heightM": 3,
                "landing": None,
                "cargo": None,
                "charging": {
                    "slots": 2,
                    "powerW": 1000,
                    "priceAmount": 0.8,
                    "priceCurrency": "CNY",
                    "priceUnit": "kWh",
                },
            },
        ]
    )


def _fleet(catalogue: FacilityCatalogue, **overrides: object):
    """Authoring document with exactly the 7 frontend fleet keys (city-selected-scenario.ts:173)."""
    value: dict[str, object] = {
        "id": "uav.01",
        "assetId": "model:holybro-x500",
        "count": 1,
        "homeFacilityId": None,
        "batteryWh": 1000,
        "reserveRatio": 0.2,
        "maxPayloadKg": 2,
    }
    value.update(overrides)
    return compile_authored_fleet([value], catalogue)


def _profile(fleet, efficiency: float = 0.9, entry_id: str = "uav.01"):
    value: dict[str, object] = {
        "fleetEntryId": entry_id,
        "sourceLabel": "operator",
        "provenance": "review fixture",
        "aircraftBody": {"xM": 1.2, "yM": 0.5, "zM": 1.2},
        "cruiseSpeedMps": 15,
        "cruisePowerW": 120,
        "hoverPowerW": 210,
        "chargeEfficiency": efficiency,
    }
    (profile,) = compile_authored_performance_profiles([value], fleet)
    return profile


# ---------------------------------------------------------------------------
# Point 1: frontend declared-field strict match (homeFacilityId explicit,
# JS safe-integer count)
# ---------------------------------------------------------------------------


def test_p1_exact_7_key_document_with_explicit_null_home_matches_backend():
    """The exact frontend shape (all 7 keys, homeFacilityId: null) is accepted."""
    fleet = _fleet(_catalogue())
    (entry,) = fleet.entries
    assert entry.fleet_entry_id == "uav.01"
    assert entry.home_facility_id is None
    assert entry.battery_wh == 1000
    assert entry.reserve_ratio == 0.2
    assert entry.max_payload_kg == 2


def test_p1_missing_home_facility_id_key_is_rejected_like_frontend():
    """FIXED: the frontend requires the key to be present even when null
    (``Object.hasOwn`` over the 7-key list); the backend now rejects a document
    that omits ``homeFacilityId`` instead of silently defaulting it."""
    document = {
        "id": "uav.01",
        "assetId": "model:holybro-x500",
        "count": 1,
        "batteryWh": 1000,
        "reserveRatio": 0.2,
        "maxPayloadKg": 2,
    }  # homeFacilityId key removed entirely
    with pytest.raises((ValueError, ValidationError)):
        compile_authored_fleet([document], _catalogue())


def test_p1_count_accepts_2_0_like_js_safe_integer():
    """FIXED: frontend positiveInteger accepts 2.0 (Number.isSafeInteger(2.0) is
    true); the backend now accepts the same whole-number float and expands to 2."""
    fleet = _fleet(_catalogue(), count=2.0)
    (entry,) = fleet.entries
    assert entry.count == 2
    assert len(fleet.expand_aircraft_units()) == 2


@pytest.mark.parametrize(
    "bad_count",
    [
        True,  # bool
        "3",  # string must not be coerced
        1.5,  # non-integral
        float("nan"),  # non-finite
        float("inf"),  # non-finite
        (1 << 53),  # 9007199254740992 > Number.MAX_SAFE_INTEGER
        (1 << 53) + 1,  # unsafe integer (rounds to 2**53 as a float)
    ],
)
def test_p1_count_rejects_bool_string_nonintegral_nonfinite_unsafe(bad_count: object):
    """FIXED: backend matches the frontend safe-integer rule and never coerces strings."""
    with pytest.raises((ValueError, ValidationError)):
        _fleet(_catalogue(), count=bad_count)


def test_p1_extra_and_missing_keys_rejected_like_frontend():
    catalogue = _catalogue()
    with pytest.raises(ValidationError):
        _fleet(catalogue, note="extra key")
    document = {
        "id": "uav.01",
        "assetId": "model:holybro-x500",
        "count": 1,
        "homeFacilityId": None,
        "reserveRatio": 0.2,
        "maxPayloadKg": 2,
    }  # batteryWh missing - required by both sides
    with pytest.raises(ValidationError):
        compile_authored_fleet([document], catalogue)


# ---------------------------------------------------------------------------
# Point 2: supply Wh (power x time) vs post-efficiency stored Wh vs billing Wh
# ---------------------------------------------------------------------------


def _one_charge_run(efficiency: float, initial_wh: float, facility_id: str = "ch.01"):
    catalogue = _catalogue()
    fleet = _fleet(catalogue, batteryWh=1000, reserveRatio=0.0)
    profile = _profile(fleet, efficiency=efficiency)
    return simulate_energy(
        next(iter(fleet.expand_aircraft_units())),
        profile,
        catalogue,
        [
            EnergyActivitySample(
                activity="ground_charge",
                start_s=0,
                end_s=1000,
                facility_id=facility_id,
            )
        ],
        initial_wh=initial_wh,
    )


def test_p2_supply_and_stored_are_pairwise_exposed_in_the_run():
    """2000 W x 1000 s = 555.555... Wh supply; battery gains only x0.9 = 500 Wh.
    The run exposes the grid-side supply separately from the stored battery Wh."""
    run = _one_charge_run(efficiency=0.9, initial_wh=500.0)
    supply_wh = 2000.0 * 1000.0 / 3600.0
    assert run.supply_energy_wh == pytest.approx(supply_wh, rel=1e-12)
    assert run.stored_energy_wh == pytest.approx(supply_wh * 0.9, rel=1e-12)
    assert run.final_battery_wh == pytest.approx(1000.0, abs=1e-9)
    # Battery-side net uses stored_wh only (no discharge here).
    assert run.net_energy_wh == pytest.approx(run.stored_energy_wh, rel=1e-12)


def test_p2_billing_base_is_supply_wh_not_stored_wh():
    """FIXED: the declared 0.8 CNY/kWh price is billed on the meter-side supply
    Wh, not the post-efficiency battery Wh: 555.555.../1000*0.8 = 0.444... CNY."""
    run = _one_charge_run(efficiency=0.9, initial_wh=500.0)
    (cost,) = run.costs
    supply_wh = 2000.0 * 1000.0 / 3600.0
    assert cost.supply_wh == pytest.approx(supply_wh, rel=1e-12)
    assert cost.stored_wh == pytest.approx(supply_wh * 0.9, rel=1e-12)
    assert cost.amount == pytest.approx(supply_wh / 1000.0 * 0.8, rel=1e-12)
    # Without the fix the undercharge was exactly (1 - efficiency).
    assert cost.amount == pytest.approx(0.4444444444444444, rel=1e-12)


def test_p2_battery_delta_is_stored_not_supply():
    """The step's battery delta is the stored Wh (500), while the supply Wh
    (555.555...) is carried separately for the charge step."""
    run = _one_charge_run(efficiency=0.9, initial_wh=500.0)
    (step,) = run.steps
    supply_wh = 2000.0 * 1000.0 / 3600.0
    assert step.energy_delta_wh == pytest.approx(500.0, rel=1e-12)
    assert step.energy_delta_wh == pytest.approx(supply_wh * 0.9, rel=1e-12)
    assert step.supply_wh == pytest.approx(supply_wh, rel=1e-12)
    assert step.battery_before_wh == pytest.approx(500.0, rel=1e-12)
    assert step.battery_after_wh == pytest.approx(1000.0, rel=1e-12)


def test_p2_at_efficiency_1_supply_equals_stored_but_both_fields_exist():
    """At charge_efficiency = 1.0 the two quantities coincide; the schema still
    exposes them separately so the accounting is never ambiguous."""
    run = _one_charge_run(efficiency=1.0, initial_wh=500.0, facility_id="ch.02")
    power_w = 1000.0  # ch.02
    supply_wh = power_w * 1000.0 / 3600.0
    (cost,) = run.costs
    assert run.supply_energy_wh == pytest.approx(supply_wh, rel=1e-12)
    assert run.stored_energy_wh == pytest.approx(supply_wh, rel=1e-12)
    assert cost.supply_wh == cost.stored_wh
    assert cost.amount == pytest.approx(supply_wh / 1000.0 * 0.8, rel=1e-12)
    assert run.net_energy_wh == pytest.approx(supply_wh, rel=1e-12)


def test_p2_schema_exposes_supply_and_stored_fields_unambiguously():
    """Both the run result and the charge cost declare a supply (grid-side) Wh
    and a stored (battery-credited) Wh; the conflated field name is gone."""
    cost_fields = set(EnergyChargeCost.model_fields)
    assert "supply_wh" in cost_fields
    assert "stored_wh" in cost_fields
    assert "charged_wh" not in cost_fields
    run_fields = set(EnergyRunResult.model_fields)
    assert "supply_energy_wh" in run_fields
    assert "stored_energy_wh" in run_fields
    assert "charged_energy_wh" not in run_fields


def test_p2_full_cycle_bills_supply_and_credits_stored():
    """A 600 Wh battery filled from empty at efficiency 0.9 draws
    600/0.9 = 666.666... Wh from the grid; the battery is credited 600 Wh and
    the tariff prices the 666.666... Wh supply."""
    catalogue = _catalogue()
    fleet = _fleet(catalogue, id="uav.02", batteryWh=600, reserveRatio=0.0, count=1)
    profile = _profile(fleet, efficiency=0.9, entry_id="uav.02")
    run = simulate_energy(
        next(iter(fleet.expand_aircraft_units())),
        profile,
        catalogue,
        [
            EnergyActivitySample(
                activity="ground_charge",
                start_s=0,
                end_s=1200,
                facility_id="ch.01",
            )
        ],
        initial_wh=0.0,
    )
    grid_wh = 2000.0 * 1200.0 / 3600.0
    assert grid_wh == pytest.approx(600.0 / 0.9, rel=1e-12)
    assert run.supply_energy_wh == pytest.approx(grid_wh, rel=1e-12)
    assert run.stored_energy_wh == pytest.approx(600.0, rel=1e-12)
    assert run.final_battery_wh == pytest.approx(600.0, abs=1e-9)
    (cost,) = run.costs
    assert cost.supply_wh == pytest.approx(grid_wh, rel=1e-12)
    assert cost.stored_wh == pytest.approx(600.0, rel=1e-12)
    assert cost.amount == pytest.approx(grid_wh / 1000.0 * 0.8, rel=1e-12)


def test_p2_required_scenario_1000W_1h_efficiency_0_8():
    """Requirement anchor: capacity 1000 Wh, reserve 200 Wh, low initial 150 Wh.
    Charging 1000 W for 1 hour at efficiency 0.8 stores 800 Wh (battery delta)
    and bills the full 1000 Wh meter-side supply at the declared CNY/kWh price."""
    catalogue = _catalogue()
    fleet = _fleet(catalogue, batteryWh=1000, reserveRatio=0.2, count=1)
    unit = next(iter(fleet.expand_aircraft_units()))
    profile = _profile(fleet, efficiency=0.8)
    run = simulate_energy(
        unit,
        profile,
        catalogue,
        [
            EnergyActivitySample(
                activity="ground_charge",
                start_s=0,
                end_s=3600,
                facility_id="ch.02",  # 1000 W
            )
        ],
        initial_wh=150.0,
    )
    assert unit.capacity_wh == pytest.approx(1000.0, rel=1e-12)
    assert unit.reserve_wh == pytest.approx(200.0, rel=1e-12)
    assert run.supply_energy_wh == pytest.approx(1000.0, rel=1e-12)
    assert run.stored_energy_wh == pytest.approx(800.0, rel=1e-12)
    assert run.final_battery_wh == pytest.approx(950.0, rel=1e-12)  # 150 + 800
    (cost,) = run.costs
    assert cost.supply_wh == pytest.approx(1000.0, rel=1e-12)
    assert cost.stored_wh == pytest.approx(800.0, rel=1e-12)
    assert cost.amount == pytest.approx(1000.0 / 1000.0 * 0.8, rel=1e-12)
    assert run.net_energy_wh == pytest.approx(800.0, rel=1e-12)


# ---------------------------------------------------------------------------
# Point 3: reserve_wh is a planning safety threshold, not a physical floor
# ---------------------------------------------------------------------------


def test_p3_battery_state_allows_charge_below_reserve():
    """A 150 Wh battery inside a 1000 Wh cell with a 200 Wh declared reserve is
    a valid physical state; the planning deficit shows up as negative available_wh."""
    state = BatteryState(battery_wh=150, capacity_wh=1000, reserve_wh=200)
    assert state.battery_wh == pytest.approx(150)
    assert state.available_wh == pytest.approx(-50)
    assert state.charging_room_wh == pytest.approx(850)
    assert state.reserve_breached is True


def test_p3_aircraft_unit_allows_initial_charge_below_reserve():
    """FIXED: constructing an AircraftUnit below the declared reserve is valid
    (a drone that flew down to 150 Wh is exactly the low-battery charge case)."""
    unit = AircraftUnit(
        aircraft_id="uav.01:1",
        fleet_entry_id="uav.01",
        visual_asset_id="model:holybro-x500",
        home_facility_id=None,
        ordinal=1,
        capacity_wh=1000,
        reserve_wh=200,
        initial_battery_wh=150,
        max_payload_kg=2,
    )
    assert unit.initial_battery_wh == pytest.approx(150)
    assert unit.reserve_wh == pytest.approx(200)
    assert unit.initial_battery_wh < unit.reserve_wh


def test_p3_simulate_energy_starts_below_reserve_and_charges():
    """A low-reserve aircraft standing at a charger can be simulated: the run
    starts below reserve, records the breach, charges, and ends above reserve."""
    catalogue = _catalogue()
    fleet = _fleet(catalogue, batteryWh=1000, reserveRatio=0.2, count=1)
    unit = next(iter(fleet.expand_aircraft_units()))
    profile = _profile(fleet)
    run = simulate_energy(
        unit,
        profile,
        catalogue,
        [
            EnergyActivitySample(
                activity="ground_charge",
                start_s=0,
                end_s=1000,
                facility_id="ch.01",  # 2000 W, eta 0.9 -> stored 500 Wh
            )
        ],
        initial_wh=150.0,
    )
    assert run.initial_wh == pytest.approx(150.0)
    assert run.reserve_breached is True  # started below the declared reserve
    assert run.stored_energy_wh == pytest.approx(500.0, rel=1e-12)
    assert run.final_battery_wh == pytest.approx(650.0, rel=1e-12)  # 150 + 500
    (step,) = run.steps
    assert step.reserve_breached is False  # ended above the reserve line


def test_p3_discharge_can_cross_the_reserve_line_and_is_recorded():
    """Hovering 3600 s from 400 Wh at 210 W draws 210 Wh -> 190 Wh, which is
    physically feasible; the decline across the 200 Wh reserve is recorded."""
    catalogue = _catalogue()
    fleet = _fleet(catalogue)  # reserve = 1000 x 0.2 = 200 Wh
    unit = next(iter(fleet.expand_aircraft_units()))
    profile = _profile(fleet)
    state = BatteryState(battery_wh=400, capacity_wh=1000, reserve_wh=200)
    sample = EnergyActivitySample(activity="hover", start_s=0, end_s=3600)
    next_state, step, cost = apply_energy_sample(state, sample, profile, catalogue)
    assert cost is None
    assert next_state.battery_wh == pytest.approx(190.0, rel=1e-12)
    assert next_state.reserve_breached is True
    assert step.reserve_breached is True
    assert step.energy_delta_wh == pytest.approx(-210.0, rel=1e-12)
    run = simulate_energy(
        unit,
        profile,
        catalogue,
        [sample],
        initial_wh=400.0,
    )
    assert run.final_battery_wh == pytest.approx(190.0, rel=1e-12)
    assert run.reserve_breached is True
    assert run.reserve_breached_steps == 1
    assert run.hover_energy_wh == pytest.approx(210.0, rel=1e-12)


def test_p3_physical_underflow_below_zero_is_still_rejected():
    """The physical battery floor is 0 Wh: a draw that would push the state
    negative is rejected, never clamped."""
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    unit = next(iter(fleet.expand_aircraft_units()))
    profile = _profile(fleet)
    # hover draws 210 Wh > the 150 Wh available -> underflow below 0 Wh.
    with pytest.raises(ValueError, match="underflow"):
        simulate_energy(
            unit,
            profile,
            catalogue,
            [EnergyActivitySample(activity="hover", start_s=0, end_s=3600)],
            initial_wh=150.0,
        )


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
