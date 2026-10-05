"""Explicit deterministic declared-parameter energy SIMULATION model.

This module is a pure, deterministic simulation of one aircraft battery against
the operator-declared performance profile and the declared facility charging
capabilities. It is explicitly NOT measured telemetry and it is NOT a flight
fallback: every energy number is either a declared input or a straightforward
watt-hour conversion from declared watts and the supplied authoritative sample
interval. It never generates positions and it never invents sample timestamps;
each interval carries an explicit ``start_s``/``end_s`` supplied by the caller
and a run enforces strict time-ordered non-overlapping intervals.

Semantics:

* ``BatteryState`` is immutable. A run applies one interval at a time and each
  step returns a new state; the previous state is never mutated.
* The initial Wh is explicit and must sit in ``[0, capacity_wh]``. The declared
  ``reserve_wh`` is an operational safety (planning) threshold, NOT a physical
  minimum: a battery below the declared reserve is a valid physical state and
  is exactly the state a low-battery aircraft returns in to charge.
* ``cruise``/``hover`` activity draws the declared cruise/hover watts times the
  supplied duration. A draw that would push the state below zero
  (physical underflow) is rejected, never clamped. Crossing the declared
  reserve line is physically possible and is always recorded per-step and
  per-run (``reserve_breached``); it is not an error.
* ``ground_charge`` activity references a declared charging facility. The
  declared grid-side supply energy (``supply_wh = power_w * duration / 3600``)
  is kept separate from the battery-credited energy
  (``stored_wh = supply_wh * charge_efficiency``). The battery changes by
  ``stored_wh`` only; the per-kWh price is billed on ``supply_wh`` (meter-side
  declared electricity). Charging that would exceed the declared capacity is
  rejected, never clamped.
* Costs are derived only from the declared supply Wh, the declared charge
  efficiency and the declared per-kWh price configured on the charging
  facility. They are labeled planning estimates and returned separately from
  any provider evidence; nothing here claims real battery telemetry or a live
  meter read.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Sequence, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.tasks.logistics.facilities import (
    CurrencyCode,
    FacilityCatalogue,
    FacilityIdentifier,
)
from aero_bench.tasks.logistics.fleet import (
    AircraftUnit,
    FleetPerformanceProfile,
)

EnergyActivityKind: TypeAlias = Literal["cruise", "hover", "ground_charge"]
SIMULATION_LABEL = "deterministic declared-parameter simulation; not measured telemetry"
COST_LABEL = "declared-price planning estimate"
HOUR_SECONDS = 3600.0
EPSILON = 1e-9


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} cannot be a boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _nonnegative(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric < 0:
        raise ValueError(f"{label} must be nonnegative")
    return numeric


def _positive(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric <= 0:
        raise ValueError(f"{label} must be positive")
    return numeric


class BatteryState(StrictModel):
    """Immutable battery energy state with declared capacity and reserve.

    ``battery_wh`` is a physical battery state and any value in
    ``[0, capacity_wh]`` is valid, including below the declared ``reserve_wh``.
    ``reserve_wh`` is an operational safety (planning) threshold, not a
    physical minimum.
    """

    battery_wh: float
    capacity_wh: float
    reserve_wh: float

    @field_validator("capacity_wh", mode="before")
    @classmethod
    def capacity_is_positive(cls, value: object) -> float:
        return _positive(value, "capacity_wh")

    @field_validator("battery_wh", "reserve_wh", mode="before")
    @classmethod
    def battery_and_reserve_are_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @model_validator(mode="after")
    def battery_stays_within_bounds(self) -> "BatteryState":
        if self.reserve_wh > self.capacity_wh:
            raise ValueError("reserve_wh cannot exceed capacity_wh")
        if self.battery_wh > self.capacity_wh:
            raise ValueError("battery_wh cannot exceed capacity_wh")
        return self

    @property
    def available_wh(self) -> float:
        """Energy above the declared reserve line (negative when below it).

        This is a planning quantity: a negative value is the deficit below the
        declared safety reserve, never a physically impossible battery.
        """
        return self.battery_wh - self.reserve_wh

    @property
    def charging_room_wh(self) -> float:
        """Free energy room before the declared capacity, at least zero."""
        return self.capacity_wh - self.battery_wh

    @property
    def reserve_breached(self) -> bool:
        """True when this state sits below the declared safety reserve."""
        return self.battery_wh < self.reserve_wh


class EnergyActivitySample(StrictModel):
    """One declared activity interval with explicit, authoritative times.

    ``start_s``/``end_s`` are supplied by the caller; the duration
    (``end_s - start_s``) is the authoritative sample interval for this update.
    A ``ground_charge`` interval must name the facility whose declared charging
    capability powers and prices the charge.
    """

    activity: EnergyActivityKind
    start_s: float
    end_s: float
    facility_id: FacilityIdentifier | None = None

    @field_validator("start_s", mode="before")
    @classmethod
    def start_is_nonnegative(cls, value: object) -> float:
        return _nonnegative(value, "start_s")

    @field_validator("end_s", mode="before")
    @classmethod
    def end_is_finite(cls, value: object) -> float:
        return _finite(value, "end_s")

    @model_validator(mode="after")
    def interval_and_facility_contract(self) -> "EnergyActivitySample":
        if self.end_s <= self.start_s:
            raise ValueError(
                "end_s must strictly follow start_s; empty intervals are rejected"
            )
        if self.activity == "ground_charge":
            if self.facility_id is None:
                raise ValueError("ground_charge requires a declared facility_id")
        elif self.facility_id is not None:
            raise ValueError(
                f"{self.activity} cannot carry a facility_id"
            )
        return self

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class EnergyStep(StrictModel):
    """One applied interval in the simulation trace (not provider telemetry).

    ``energy_delta_wh`` is always the battery-side change: ``stored_wh`` for a
    charge, minus the drawn Wh for a discharge. ``supply_wh`` carries the
    declared grid-side electricity for a charge (``power_w * duration / 3600``)
    and is ``0`` for cruise/hover. ``reserve_breached`` records whether the
    battery after this step sits below the declared safety reserve.
    """

    sequence: Annotated[int, Field(ge=0)]
    activity: EnergyActivityKind
    start_s: float
    end_s: float
    duration_s: float
    energy_delta_wh: float
    battery_before_wh: float
    battery_after_wh: float
    supply_wh: float = 0.0
    reserve_breached: bool = False
    facility_id: FacilityIdentifier | None = None

    @field_validator("sequence", mode="before")
    @classmethod
    def sequence_is_nonnegative_integer(cls, value: object) -> int:
        _finite(value, "sequence")
        number = float(value)
        if not number.is_integer() or number < 0:
            raise ValueError("sequence must be a nonnegative integer")
        return int(number)

    @field_validator("duration_s", mode="before")
    @classmethod
    def duration_is_positive(cls, value: object) -> float:
        return _positive(value, "duration_s")

    @field_validator(
        "battery_before_wh", "battery_after_wh", "supply_wh", mode="before"
    )
    @classmethod
    def energy_quantities_are_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @field_validator("reserve_breached", mode="before")
    @classmethod
    def breach_flag_is_boolean(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise ValueError("reserve_breached must be a boolean")
        return value


class EnergyChargeCost(StrictModel):
    """Declared-price cost estimate for one charging interval.

    ``supply_wh`` is the declared meter-side electricity drawn
    (``charging.power_w * duration / 3600``) and is the billing base:
    ``amount = supply_wh / 1000 * price_amount`` (price is configured per kWh).
    ``stored_wh`` is the battery-credited energy
    (``supply_wh * charge_efficiency``). The two quantities are kept separate
    and are explicit declared arithmetic — never a pseudo measured meter read,
    never a live tariff lookup, and never a claim that a real charge happened.
    """

    facility_id: FacilityIdentifier
    supply_wh: float
    stored_wh: float
    amount: float
    currency: CurrencyCode
    unit: Literal["kWh"] = "kWh"
    estimate_label: Literal["declared-price planning estimate"] = COST_LABEL

    @field_validator("supply_wh", "stored_wh", "amount", mode="before")
    @classmethod
    def quantities_and_amount_are_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)


class EnergyRunResult(StrictModel):
    """Complete simulation result for one aircraft over an interval stream.

    Charging exposure is split unambiguously: ``supply_energy_wh`` is the total
    declared grid-side electricity (``power_w * duration / 3600``) that the
    charging tariff bills, while ``stored_energy_wh`` is the total
    battery-credited energy (``supply * charge_efficiency``) the battery
    actually gained. Battery-side accounting (``energy_delta_wh`` per step,
    ``discharge_energy_wh``, ``net_energy_wh``) always uses ``stored_wh``.

    ``reserve_breached`` is the run-level planning-violation status: it is True
    when the battery ever sat below the declared safety reserve (including a
    low-battery initial state); ``reserve_breached_steps`` counts the steps
    that ended below the reserve line.

    Energy and cost calculations are reported separately; the explicit estimate
    label and carried provenance make clear this is simulation bookkeeping, not
    measured telemetry.
    """

    aircraft_id: FacilityIdentifier
    fleet_entry_id: FacilityIdentifier
    profile_source_label: str
    profile_provenance: str
    capacity_wh: float
    reserve_wh: float
    initial_wh: float
    final_battery_wh: float
    cruise_energy_wh: float
    hover_energy_wh: float
    discharge_energy_wh: float
    supply_energy_wh: float
    stored_energy_wh: float
    net_energy_wh: float
    reserve_breached: bool
    reserve_breached_steps: Annotated[int, Field(ge=0)]
    total_duration_s: float
    samples_processed: Annotated[int, Field(ge=0)]
    estimate_label: Literal["deterministic declared-parameter simulation; not measured telemetry"] = (
        SIMULATION_LABEL
    )
    steps: tuple[EnergyStep, ...] = ()
    costs: tuple[EnergyChargeCost, ...] = ()


def apply_energy_sample(
    state: BatteryState,
    sample: EnergyActivitySample,
    profile: FleetPerformanceProfile,
    facilities: FacilityCatalogue,
    *,
    sequence: int = 0,
) -> tuple[BatteryState, EnergyStep, EnergyChargeCost | None]:
    """Apply one declared interval to an immutable battery state.

    The declared watts come from the verified profile (cruise/hover) or from
    the referenced facility's declared charging capability.

    * Charging keeps the grid-side supply Wh
      (``power_w * duration / 3600``) separate from the battery-credited
      stored Wh (``supply_wh * charge_efficiency``); the tariff prices the
      supply Wh.
    * Cruise/hover may cross the declared reserve line (physically feasible)
      and that breach is recorded on the step/run; only a physical underflow
      below 0 Wh is rejected. A charge above capacity is rejected, never
      clamped.
    """
    duration = sample.duration_s
    before = state.battery_wh
    if sample.activity == "ground_charge":
        assert sample.facility_id is not None
        facility = facilities.require(sample.facility_id)
        if facility.charging is None:
            raise ValueError(
                "ground_charge references a facility without a declared "
                "charging capability"
            )
        supply_wh = facility.charging.power_w * duration / HOUR_SECONDS
        stored_wh = supply_wh * profile.charge_efficiency
        if not math.isfinite(supply_wh) or not math.isfinite(stored_wh):
            raise ValueError("ground_charge supply/stored Wh must be finite")
        after = before + stored_wh
        if after > state.capacity_wh + EPSILON:
            raise ValueError(
                "ground_charge would exceed the declared battery capacity; "
                "refusing to clamp"
            )
        cost = EnergyChargeCost(
            facility_id=sample.facility_id,
            supply_wh=supply_wh,
            stored_wh=stored_wh,
            amount=supply_wh / 1000.0 * facility.charging.price_amount,
            currency=facility.charging.price_currency,
        )
    elif sample.activity == "cruise":
        drawn = profile.cruise_power_w * duration / HOUR_SECONDS
        if not math.isfinite(drawn):
            raise ValueError("cruise drawn Wh must be finite")
        after = before - drawn
        if after < -EPSILON:
            raise ValueError(
                "cruise would underflow the battery below 0 Wh; "
                "refusing to discharge"
            )
        supply_wh = 0.0
        cost = None
    else:  # hover
        drawn = profile.hover_power_w * duration / HOUR_SECONDS
        if not math.isfinite(drawn):
            raise ValueError("hover drawn Wh must be finite")
        after = before - drawn
        if after < -EPSILON:
            raise ValueError(
                "hover would underflow the battery below 0 Wh; "
                "refusing to discharge"
            )
        supply_wh = 0.0
        cost = None

    next_state = BatteryState(
        battery_wh=after,
        capacity_wh=state.capacity_wh,
        reserve_wh=state.reserve_wh,
    )
    step = EnergyStep(
        sequence=sequence,
        activity=sample.activity,
        start_s=sample.start_s,
        end_s=sample.end_s,
        duration_s=duration,
        energy_delta_wh=after - before,
        battery_before_wh=before,
        battery_after_wh=after,
        supply_wh=supply_wh,
        reserve_breached=next_state.reserve_breached,
        facility_id=sample.facility_id,
    )
    return next_state, step, cost


def simulate_energy(
    aircraft: AircraftUnit,
    profile: FleetPerformanceProfile,
    facilities: FacilityCatalogue,
    samples: Sequence[EnergyActivitySample],
    *,
    initial_wh: float | None = None,
) -> EnergyRunResult:
    """Deterministically simulate the declared interval stream for one aircraft.

    The intervals are applied in the order supplied and must be strictly
    time-ordered and non-overlapping (a later interval may start exactly when
    the previous one ends, or later). Initial Wh is explicit and defaults to the
    aircraft's declared initial battery; it must sit within the physical
    ``[0, capacity_wh]`` bounds (the declared reserve is a planning threshold,
    so an initial charge below it is valid). Ambiguous clamping decisions are
    always resolved by explicit rejection.
    """
    if profile.fleet_entry_id != aircraft.fleet_entry_id:
        raise ValueError(
            "performance profile must belong to the aircraft's fleet entry"
        )
    capacity_wh = aircraft.capacity_wh
    reserve_wh = aircraft.reserve_wh
    start_wh = aircraft.initial_battery_wh if initial_wh is None else initial_wh
    state = BatteryState(
        battery_wh=start_wh,
        capacity_wh=capacity_wh,
        reserve_wh=reserve_wh,
    )

    steps: list[EnergyStep] = []
    costs: list[EnergyChargeCost] = []
    cruise_energy_wh = 0.0
    hover_energy_wh = 0.0
    supply_energy_wh = 0.0
    stored_energy_wh = 0.0
    reserve_breached = state.reserve_breached
    reserve_breached_steps = 0
    previous_end: float | None = None
    first_start: float | None = None

    for sequence, sample in enumerate(samples):
        if first_start is None:
            first_start = sample.start_s
        if previous_end is not None and sample.start_s < previous_end - EPSILON:
            raise ValueError(
                "energy sample intervals must be time-ordered and "
                "non-overlapping"
            )
        previous_end = sample.end_s
        state, step, cost = apply_energy_sample(
            state,
            sample,
            profile,
            facilities,
            sequence=sequence,
        )
        steps.append(step)
        if cost is not None:
            costs.append(cost)
        if sample.activity == "cruise":
            cruise_energy_wh -= step.energy_delta_wh
        elif sample.activity == "hover":
            hover_energy_wh -= step.energy_delta_wh
        else:
            stored_energy_wh += step.energy_delta_wh
            supply_energy_wh += step.supply_wh
        if step.reserve_breached:
            reserve_breached_steps += 1
            reserve_breached = True

    discharge_energy_wh = cruise_energy_wh + hover_energy_wh
    total_duration_s = (
        (previous_end - first_start) if previous_end is not None and first_start is not None else 0.0
    )
    return EnergyRunResult(
        aircraft_id=aircraft.aircraft_id,
        fleet_entry_id=aircraft.fleet_entry_id,
        profile_source_label=profile.source_label,
        profile_provenance=profile.provenance,
        capacity_wh=capacity_wh,
        reserve_wh=reserve_wh,
        initial_wh=start_wh,
        final_battery_wh=state.battery_wh,
        cruise_energy_wh=cruise_energy_wh,
        hover_energy_wh=hover_energy_wh,
        discharge_energy_wh=discharge_energy_wh,
        supply_energy_wh=supply_energy_wh,
        stored_energy_wh=stored_energy_wh,
        net_energy_wh=stored_energy_wh - discharge_energy_wh,
        reserve_breached=reserve_breached,
        reserve_breached_steps=reserve_breached_steps,
        total_duration_s=total_duration_s,
        samples_processed=len(steps),
        steps=tuple(steps),
        costs=tuple(costs),
    )


__all__ = [
    "BatteryState",
    "EnergyActivityKind",
    "EnergyActivitySample",
    "EnergyChargeCost",
    "EnergyRunResult",
    "EnergyStep",
    "apply_energy_sample",
    "simulate_energy",
]
