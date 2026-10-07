"""Strict lowering of the current v3 selected-city fleet and performance profiles.

The v3 selected-city scenario declares a fleet as explicit ``id``/``assetId``
entries with ``count``, an explicit ``homeFacilityId`` (``null`` is allowed but
the key must be present, matching the frontend 7-key object contract), a
battery capacity in Wh, a reserve ratio in ``[0, 1]`` and a maximum payload in
kg. Nothing here is inferred: the battery, reserve, payload, and physics
numbers are declared authoring inputs, and physics is never derived from a GLB
display scale or from any mesh. Facility references are bound against the
canonical ``FacilityCatalogue``; an unknown ``homeFacilityId`` is rejected
exactly the way the frontend authoring parser rejects it.

Aircraft runtime identity is separate from the visual asset id. The frontend
planner expands each fleet entry into stable unit ids of the form
``<entry.id>:<ordinal>`` (1-based); :meth:`Fleet.expand_aircraft_units`
reproduces that convention exactly and keeps ``visual_asset_id`` distinct.

Declared performance profiles are lowered with explicit provenance labels
(``source_label``, ``provenance``), positive body dimensions, speed and watts,
and a charge efficiency in ``(0, 1]``. These are estimates declared by an
operator unless separately verified; this backend never derives dynamics.
"""

from __future__ import annotations

import math
from typing import Annotated, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.tasks.logistics.facilities import FacilityCatalogue, FacilityIdentifier

FleetIdentifier: TypeAlias = FacilityIdentifier
AssetIdentifier: TypeAlias = Annotated[
    str, Field(pattern=r"^model:[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]
# JavaScript Number.MAX_SAFE_INTEGER = 2**53 - 1. ``count`` matches the
# frontend ``positiveInteger`` rule (Number.isSafeInteger): 2.0 is accepted,
# while bools, strings, non-integral / non-finite values and integers beyond
# the safe range are rejected and never coerced.
SAFE_INTEGER_MAX = (1 << 53) - 1


def _reject_bool(value: object, label: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{label} cannot be a boolean")


def _finite(value: object, label: str) -> float:
    _reject_bool(value, label)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _positive(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric <= 0:
        raise ValueError(f"{label} must be positive")
    return numeric


def _nonnegative(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric < 0:
        raise ValueError(f"{label} must be nonnegative")
    return numeric


def _ratio(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric < 0 or numeric > 1:
        raise ValueError(f"{label} must be between 0 and 1")
    return numeric


def _positive_int(value: object, label: str) -> int:
    _reject_bool(value, label)
    # JS ``finite()`` only accepts a real ``number``; strings/bools are never
    # coerced. A "safe integer" is any number whose value is an integer within
    # Number.MAX_SAFE_INTEGER, so 2.0 is valid while 1.5 and 2**53 are not.
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a safe integer number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    if not numeric.is_integer():
        raise ValueError(f"{label} must be an integer (2.0 is valid; 1.5 is not)")
    if numeric > SAFE_INTEGER_MAX:
        raise ValueError(f"{label} exceeds the JS safe-integer range")
    if numeric < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(numeric)


class FleetEntry(StrictModel):
    """One declared fleet entry, frozen and never inferred from geometry.

    ``fleet_entry_id`` is the authoring document id (``id`` in the browser).
    ``asset_id`` is the visual asset reference (``assetId``, ``model:...``) and
    is kept distinct from the generated aircraft runtime identity.
    """

    fleet_entry_id: FleetIdentifier
    asset_id: AssetIdentifier
    count: int
    home_facility_id: FacilityIdentifier | None = None
    battery_wh: float
    reserve_ratio: float
    max_payload_kg: float

    @field_validator("count", mode="before")
    @classmethod
    def count_is_positive_integer(cls, value: object) -> int:
        return _positive_int(value, "count")

    @field_validator("battery_wh", mode="before")
    @classmethod
    def battery_is_positive(cls, value: object) -> float:
        return _positive(value, "battery_wh")

    @field_validator("reserve_ratio", mode="before")
    @classmethod
    def reserve_is_ratio(cls, value: object) -> float:
        return _ratio(value, "reserve_ratio")

    @field_validator("max_payload_kg", mode="before")
    @classmethod
    def payload_is_nonnegative(cls, value: object) -> float:
        return _nonnegative(value, "max_payload_kg")


class AuthoredFleetEntry(FleetEntry):
    """One v3 selected-city fleet entry under the current authoring field names.

    ``homeFacilityId`` is required exactly like the frontend protocol: the key
    must be *explicitly present* in the JSON object, even when its value is
    ``null`` (an explicitly declared un-homed aircraft). A document that omits
    the key entirely is rejected, matching
    ``frontend/src/city-selected-scenario.ts`` ``object(...)``/``parseFleetEntry``.
    """

    fleet_entry_id: FleetIdentifier = Field(validation_alias="id")
    asset_id: AssetIdentifier = Field(validation_alias="assetId")
    count: int = Field(validation_alias="count")
    home_facility_id: FacilityIdentifier | None = Field(
        validation_alias="homeFacilityId"
    )
    battery_wh: float = Field(validation_alias="batteryWh")
    reserve_ratio: float = Field(validation_alias="reserveRatio")
    max_payload_kg: float = Field(validation_alias="maxPayloadKg")


class AircraftUnit(StrictModel):
    """One physical drone produced by expanding a fleet entry.

    The runtime aircraft identity ``aircraft_id`` exactly matches the frontend
    planner convention ``<fleet_entry_id>:<ordinal>`` and is distinct from
    ``visual_asset_id``.

    ``reserve_wh`` is the declared operational safety reserve (a planning
    threshold), not a physical minimum: ``initial_battery_wh`` is a physical
    battery state and any value in ``[0, capacity_wh]`` is valid, including a
    low-battery aircraft returning below the declared reserve to charge.
    """

    aircraft_id: FleetIdentifier
    fleet_entry_id: FleetIdentifier
    visual_asset_id: AssetIdentifier
    home_facility_id: FacilityIdentifier | None = None
    ordinal: Annotated[int, Field(ge=1)]
    capacity_wh: float
    reserve_wh: float
    initial_battery_wh: float
    max_payload_kg: float

    @field_validator("ordinal", mode="before")
    @classmethod
    def ordinal_is_positive_integer(cls, value: object) -> int:
        return _positive_int(value, "ordinal")

    @field_validator("capacity_wh", mode="before")
    @classmethod
    def capacity_is_positive(cls, value: object) -> float:
        return _positive(value, "capacity_wh")

    @field_validator("reserve_wh", "max_payload_kg", "initial_battery_wh", mode="before")
    @classmethod
    def battery_and_payload_are_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @model_validator(mode="after")
    def aircraft_contract_is_consistent(self) -> "AircraftUnit":
        if self.aircraft_id != f"{self.fleet_entry_id}:{self.ordinal}":
            raise ValueError(
                "aircraft_id must be the frontend planner id "
                f"'{self.fleet_entry_id}:<ordinal>'"
            )
        if not math.isfinite(self.capacity_wh) or self.capacity_wh <= 0:
            raise ValueError("capacity_wh must be finite and positive")
        if not math.isfinite(self.reserve_wh) or self.reserve_wh < 0:
            raise ValueError("reserve_wh must be finite and nonnegative")
        if self.reserve_wh > self.capacity_wh:
            raise ValueError("reserve_wh cannot exceed capacity_wh")
        # Physical battery bounds only: [0, capacity_wh]. The declared reserve
        # is a planning safety threshold, so an initial charge below it is
        # valid (a low-battery aircraft waiting at a charger).
        if not math.isfinite(self.initial_battery_wh):
            raise ValueError("initial_battery_wh must be finite")
        if self.initial_battery_wh > self.capacity_wh:
            raise ValueError("initial_battery_wh cannot exceed capacity_wh")
        return self


class Fleet(StrictModel):
    """Immutable aggregate of fleet entries bound to the canonical facility catalogue."""

    catalogue: FacilityCatalogue = FacilityCatalogue()
    entries: tuple[FleetEntry, ...] = ()

    @model_validator(mode="after")
    def fleet_entries_are_bound_and_unique(self) -> "Fleet":
        entry_ids = [entry.fleet_entry_id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("fleet entry ids must be unique")
        for entry in self.entries:
            if entry.home_facility_id is not None:
                self.catalogue.require(entry.home_facility_id)
        return self

    def get(self, fleet_entry_id: str) -> FleetEntry | None:
        return next(
            (
                entry
                for entry in self.entries
                if entry.fleet_entry_id == fleet_entry_id
            ),
            None,
        )

    def require(self, fleet_entry_id: str) -> FleetEntry:
        entry = self.get(fleet_entry_id)
        if entry is None:
            raise ValueError(f"unknown fleet entry reference: {fleet_entry_id}")
        return entry

    def expand_aircraft_units(self) -> tuple[AircraftUnit, ...]:
        """Expand each entry into stable unit ids matching the frontend planner.

        The frontend expands ``count`` units per entry as ``<entry.id>:<index>``
        with a 1-based index; this function is the exact backend equivalent.
        Initial battery is the declared full capacity; the declared reserve is
        ``battery_wh * reserve_ratio``.
        """
        return tuple(
            AircraftUnit(
                aircraft_id=f"{entry.fleet_entry_id}:{index}",
                fleet_entry_id=entry.fleet_entry_id,
                visual_asset_id=entry.asset_id,
                home_facility_id=entry.home_facility_id,
                ordinal=index,
                capacity_wh=entry.battery_wh,
                reserve_wh=entry.battery_wh * entry.reserve_ratio,
                initial_battery_wh=entry.battery_wh,
                max_payload_kg=entry.max_payload_kg,
            )
            for entry in self.entries
            for index in range(1, entry.count + 1)
        )


def compile_authored_fleet(values: object, catalogue: FacilityCatalogue) -> Fleet:
    """Validate and lower the current authoring fleet list into a bound Fleet.

    Accepts only the current v3 camelCase field names. Unknown keys, boolean
    placeholders, non-finite numbers, malformed identifiers and unknown
    ``homeFacilityId`` references are rejected; spatial/building geometry is a
    frontend/scene proof and is never asserted here.
    """
    if not isinstance(values, list):
        raise ValueError("authoring fleet must be a JSON array")
    if not isinstance(catalogue, FacilityCatalogue):
        raise ValueError("fleet home facilities must bind a FacilityCatalogue")
    entries = tuple(
        FleetEntry.model_validate(
            AuthoredFleetEntry.model_validate(value).model_dump()
        )
        for value in values
    )
    return Fleet(catalogue=catalogue, entries=entries)


class AircraftBody(StrictModel):
    """Declared body extent in metres; never derived from GLB display scale."""

    x_m: float
    y_m: float
    z_m: float

    @field_validator("x_m", "y_m", "z_m", mode="before")
    @classmethod
    def _body_strict(cls, value: object, info) -> float:
        return _positive(value, info.field_name)


class AuthoredAircraftBody(AircraftBody):
    """Aircraft body under the current frontend camelCase axis names."""

    x_m: float = Field(validation_alias="xM")
    y_m: float = Field(validation_alias="yM")
    z_m: float = Field(validation_alias="zM")


class FleetPerformanceProfile(StrictModel):
    """User-declared performance estimate for one fleet entry.

    ``source_label`` and ``provenance`` carry where the numbers came from. All
    dynamics are declared estimates; nothing here is inferred from a GLB or any
    mesh and nothing here is measured telemetry.
    """

    fleet_entry_id: FleetIdentifier
    source_label: str
    provenance: str
    aircraft_body: AircraftBody
    cruise_speed_mps: float
    cruise_power_w: float
    hover_power_w: float
    charge_efficiency: float

    @field_validator("source_label", "provenance", mode="before")
    @classmethod
    def _label_strict(cls, value: object, info) -> str:
        _reject_bool(value, info.field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value

    @field_validator("cruise_speed_mps", "cruise_power_w", "hover_power_w", mode="before")
    @classmethod
    def _physics_strict(cls, value: object, info) -> float:
        return _positive(value, info.field_name)

    @field_validator("charge_efficiency", mode="before")
    @classmethod
    def _efficiency_strict(cls, value: object) -> float:
        numeric = _finite(value, "charge_efficiency")
        if numeric <= 0 or numeric > 1:
            raise ValueError("charge_efficiency must be in (0, 1]")
        return numeric


class AuthoredPerformanceProfile(FleetPerformanceProfile):
    """One v3 performance profile under the current authoring field names."""

    fleet_entry_id: FleetIdentifier = Field(validation_alias="fleetEntryId")
    source_label: str = Field(validation_alias="sourceLabel")
    provenance: str = Field(validation_alias="provenance")
    aircraft_body: AuthoredAircraftBody = Field(validation_alias="aircraftBody")
    cruise_speed_mps: float = Field(validation_alias="cruiseSpeedMps")
    cruise_power_w: float = Field(validation_alias="cruisePowerW")
    hover_power_w: float = Field(validation_alias="hoverPowerW")
    charge_efficiency: float = Field(validation_alias="chargeEfficiency")


def compile_authored_performance_profiles(
    values: object, fleet: Fleet
) -> tuple[FleetPerformanceProfile, ...]:
    """Validate and lower the current authoring performance profile list.

    Each profile must reference an existing fleet entry (``fleetEntryId``) and
    profile ids must be unique. The declarations are carried verbatim with their
    provenance labels; no dynamics are derived here.
    """
    if not isinstance(values, list):
        raise ValueError("authoring performance profiles must be a JSON array")
    if not isinstance(fleet, Fleet):
        raise ValueError("performance profiles must bind a Fleet")
    profiles = tuple(
        FleetPerformanceProfile.model_validate(
            AuthoredPerformanceProfile.model_validate(value).model_dump()
        )
        for value in values
    )
    profile_ids = [profile.fleet_entry_id for profile in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("performance profile fleet entry ids must be unique")
    for profile in profiles:
        fleet.require(profile.fleet_entry_id)
    return profiles


__all__ = [
    "AircraftBody",
    "AircraftUnit",
    "AssetIdentifier",
    "AuthoredAircraftBody",
    "AuthoredFleetEntry",
    "AuthoredPerformanceProfile",
    "Fleet",
    "FleetEntry",
    "FleetIdentifier",
    "FleetPerformanceProfile",
    "compile_authored_fleet",
    "compile_authored_performance_profiles",
]
