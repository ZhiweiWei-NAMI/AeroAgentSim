"""Strict lowering of the current v3 selected-city facility capabilities.

The v3 selected-city scenario declares physical facility capabilities
explicitly and never infers them:

* ``landing`` carries a concurrent pad count (``parking_slots``) separately
  from a declared ``movements_per_hour`` planning rate; one never implies the
  other.
* ``cargo`` (hubs only) carries storage in kilograms and throughput in
  kilograms per hour.
* ``charging`` carries slot count, watt power, and a price amount with its own
  three-letter currency and a fixed per-kWh unit. No live tariff is ever
  looked up; the price is the configured quantity carried here.

This module lowers the current authoring shape (camelCase field names) into
frozen canonical domain models that a future logistics provider can consume.
The lowering is deliberately strict: unknown keys, boolean placeholders,
non-finite numbers, missing or renamed units, and malformed references are
rejected. Rooftop binding (``building_id`` plus a positive support height for a
rooftop vertiport, and neither for a ground site) is validated as a reference
contract only. The geometric proof that the bound building exists and supports
the pad is a frontend/scene-verification concern and is never asserted or
fabricated here; the dimensions and position are carried as declared values
only.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import StrictModel

FacilityKind: TypeAlias = Literal["vertiport", "hub", "charger"]
FacilityPlacement: TypeAlias = Literal["ground", "rooftop"]
FacilityIdentifier: TypeAlias = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]
CurrencyCode: TypeAlias = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


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


def _strict_int(value: object, label: str) -> int:
    _reject_bool(value, label)
    if not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _positive_int(value: object, label: str) -> int:
    number = _strict_int(value, label)
    if number < 1:
        raise ValueError(f"{label} must be a positive integer")
    return number


class LandingCapability(StrictModel):
    """Landing-pad capability with the physical pad count and declared rate apart.

    ``parking_slots`` is the concurrent pad count the physical geometry lays
    out; ``movements_per_hour`` is a separate declared per-pad planning rate and never
    implies additional pads. The resource ledger reserves only the concurrent
    ``parking_slots`` occupancy; ``movements_per_hour`` stays a declared
    planning input for the routing planner. The authoring planner uses its
    reciprocal as intermediate handling time on each pad; concurrent pads can
    process independently. It is not a measured facility-wide flight rate.
    """

    parking_slots: int
    movements_per_hour: float

    @field_validator("parking_slots", mode="before")
    @classmethod
    def _parking_slots_strict(cls, value: object) -> int:
        return _positive_int(value, "parking_slots")

    @field_validator("movements_per_hour", mode="before")
    @classmethod
    def _movements_strict(cls, value: object) -> float:
        return _positive(value, "movements_per_hour")


class CargoCapability(StrictModel):
    """Hub cargo capability in SI kilogram and kg/hour units."""

    storage_capacity_kg: float
    throughput_per_hour_kg: float

    @field_validator("storage_capacity_kg", "throughput_per_hour_kg", mode="before")
    @classmethod
    def _cargo_strict(cls, value: object, info) -> float:
        return _positive(value, info.field_name)


class ChargingCapability(StrictModel):
    """Charging capability. Price carries its own currency and a fixed kWh unit.

    ``price_amount`` is the configured quantity per kilowatt-hour. It is never
    looked up at runtime and the unit must be exactly ``"kWh"``; a renamed or
    missing unit is rejected rather than inferred.
    """

    slots: int
    power_w: float
    price_amount: float
    price_currency: CurrencyCode
    price_unit: Literal["kWh"]

    @field_validator("slots", mode="before")
    @classmethod
    def _slots_strict(cls, value: object) -> int:
        return _positive_int(value, "slots")

    @field_validator("power_w", mode="before")
    @classmethod
    def _power_strict(cls, value: object) -> float:
        return _positive(value, "power_w")

    @field_validator("price_amount", mode="before")
    @classmethod
    def _price_strict(cls, value: object) -> float:
        return _nonnegative(value, "price_amount")

    @field_validator("price_currency", mode="before")
    @classmethod
    def _currency_strict(cls, value: object) -> str:
        _reject_bool(value, "price_currency")
        if not isinstance(value, str) or _CURRENCY_RE.fullmatch(value) is None:
            raise ValueError(
                "price_currency must be a three-letter uppercase currency code"
            )
        return value

    @field_validator("price_unit", mode="before")
    @classmethod
    def _price_unit_strict(cls, value: object) -> str:
        if value != "kWh":
            raise ValueError("price_unit must be exactly 'kWh'")
        return value


class FacilityPosition(StrictModel):
    """Declared local position in the selected scene. Not a spatial proof."""

    x: float
    z: float

    @field_validator("x", "z", mode="before")
    @classmethod
    def _coordinate_strict(cls, value: object, info) -> float:
        return _finite(value, info.field_name)


class FacilityCapabilities(StrictModel):
    """Canonical frozen facility used by future providers.

    Dimensions, position and rotation are carried exactly as authored. No
    footprint, pad-layout or building-support proof is re-derived here; spatial
    verification is owned by the frontend/scene pipeline.
    """

    facility_id: FacilityIdentifier
    name: str
    kind: FacilityKind
    placement: FacilityPlacement
    building_id: FacilityIdentifier | None = None
    support_height_m: float | None = None
    position: FacilityPosition
    rotation_deg: float
    width_m: float
    depth_m: float
    height_m: float
    landing: LandingCapability | None = None
    cargo: CargoCapability | None = None
    charging: ChargingCapability | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name_strict(cls, value: object) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("name must be a non-empty string")
        return value

    @field_validator("rotation_deg", "width_m", "depth_m", "height_m", mode="before")
    @classmethod
    def _size_strict(cls, value: object, info) -> float:
        if info.field_name in {"width_m", "depth_m", "height_m"}:
            return _positive(value, info.field_name)
        return _finite(value, info.field_name)

    @field_validator("support_height_m", mode="before")
    @classmethod
    def _support_strict(cls, value: object) -> float | None:
        if value is None:
            return None
        return _positive(value, "support_height_m")

    @model_validator(mode="after")
    def capability_contract_is_consistent(self) -> "FacilityCapabilities":
        placement = self.placement
        kind = self.kind
        if placement == "rooftop":
            if kind != "vertiport":
                raise ValueError("only a vertiport may be placed on a rooftop")
            if self.building_id is None or self.support_height_m is None:
                raise ValueError(
                    "rooftop placement must bind a building_id and support height"
                )
        else:
            if self.building_id is not None or self.support_height_m is not None:
                raise ValueError(
                    "ground placement cannot bind a building_id or support height"
                )
        if kind == "vertiport":
            if self.landing is None:
                raise ValueError("a vertiport must declare a landing capability")
            if self.cargo is not None:
                raise ValueError("a vertiport cannot declare hub cargo")
        elif kind == "hub":
            if placement != "ground":
                raise ValueError("a logistics hub can only be placed on the ground")
            if self.landing is None:
                raise ValueError("a hub must declare a landing capability")
            if self.cargo is None:
                raise ValueError("a hub must declare hub cargo")
        else:  # standalone charger
            if placement != "ground":
                raise ValueError("a standalone charger can only be placed on the ground")
            if self.landing is not None or self.cargo is not None:
                raise ValueError(
                    "a standalone charger cannot declare landing or hub cargo"
                )
            if self.charging is None:
                raise ValueError("a standalone charger must declare a charging capability")
        return self


class FacilityCatalogue(StrictModel):
    """Immutable set of facilities with unique ids (no geometry proof)."""

    facilities: tuple[FacilityCapabilities, ...] = ()

    @model_validator(mode="after")
    def facility_ids_are_unique(self) -> "FacilityCatalogue":
        facility_ids = [facility.facility_id for facility in self.facilities]
        if len(facility_ids) != len(set(facility_ids)):
            raise ValueError("facility ids must be unique")
        return self

    def get(self, facility_id: str) -> FacilityCapabilities | None:
        return next(
            (
                facility
                for facility in self.facilities
                if facility.facility_id == facility_id
            ),
            None,
        )

    def require(self, facility_id: str) -> FacilityCapabilities:
        """Resolve an explicit facility reference or reject it as invalid."""
        facility = self.get(facility_id)
        if facility is None:
            raise ValueError(f"unknown facility reference: {facility_id}")
        return facility


class AuthoredLanding(LandingCapability):
    """Landing capability under the current frontend camelCase field names."""

    parking_slots: int = Field(validation_alias="parkingSlots")
    movements_per_hour: float = Field(validation_alias="movementsPerHour")


class AuthoredCargo(CargoCapability):
    """Cargo capability under the current frontend camelCase field names."""

    storage_capacity_kg: float = Field(validation_alias="storageCapacityKg")
    throughput_per_hour_kg: float = Field(validation_alias="throughputPerHourKg")


class AuthoredCharging(ChargingCapability):
    """Charging capability under the current frontend camelCase field names."""

    slots: int = Field(validation_alias="slots")
    power_w: float = Field(validation_alias="powerW")
    price_amount: float = Field(validation_alias="priceAmount")
    price_currency: CurrencyCode = Field(validation_alias="priceCurrency")
    price_unit: Literal["kWh"] = Field(validation_alias="priceUnit")


class AuthoredFacility(FacilityCapabilities):
    """One v3 selected-city facility under current authoring field names.

    The frontend v3 authoring contract requires every key to be present
    explicitly; the nullable capability and binding fields accept an explicit
    ``null`` but cannot be omitted. These fields therefore carry no default in
    the authored model and a missing key is rejected.
    """

    facility_id: FacilityIdentifier = Field(validation_alias="id")
    building_id: FacilityIdentifier | None = Field(validation_alias="buildingId")
    support_height_m: float | None = Field(validation_alias="supportHeightM")
    rotation_deg: float = Field(validation_alias="rotationDeg")
    width_m: float = Field(validation_alias="widthM")
    depth_m: float = Field(validation_alias="depthM")
    height_m: float = Field(validation_alias="heightM")
    landing: AuthoredLanding | None = Field(validation_alias="landing")
    cargo: AuthoredCargo | None = Field(validation_alias="cargo")
    charging: AuthoredCharging | None = Field(validation_alias="charging")


def compile_authored_facilities(values: object) -> FacilityCatalogue:
    """Validate and lower the current authoring facility list into a catalogue.

    Accepts only the current v3 camelCase field names and never a mixed,
    renamed or unitless shape. Unknown keys, boolean placeholders and
    non-finite numbers are rejected here; spatial/building geometry stays a
    frontend/scene proof and is not asserted.
    """
    if not isinstance(values, list):
        raise ValueError("authoring facilities must be a JSON array")
    facilities = tuple(
        FacilityCapabilities.model_validate(
            AuthoredFacility.model_validate(value).model_dump()
        )
        for value in values
    )
    return FacilityCatalogue(facilities=facilities)


__all__ = [
    "AuthoredCargo",
    "AuthoredCharging",
    "AuthoredFacility",
    "AuthoredLanding",
    "CargoCapability",
    "ChargingCapability",
    "CurrencyCode",
    "FacilityCapabilities",
    "FacilityCatalogue",
    "FacilityIdentifier",
    "FacilityKind",
    "FacilityPlacement",
    "FacilityPosition",
    "LandingCapability",
    "compile_authored_facilities",
]
