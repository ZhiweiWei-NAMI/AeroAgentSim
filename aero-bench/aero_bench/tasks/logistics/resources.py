"""Pure immutable resource-occupancy ledger for one facility catalogue.

This ledger records explicit ``reserve``/``release`` occupancy against declared
facility capabilities. Everything here is planning calculation labelled as
such; nothing here is a provider observation, a measured delivery, or sealed
artifact evidence. Any future claim of a measured delivery must bind an
external evidence reference separately (the order lifecycle does that); this
module only tracks declared occupancy.

Resource kinds (all with half-open intervals ``[start_s, end_s)``):

* ``parking`` - one or more physical landing pads at a landing-capable
  facility. Aggregate overlapping slot counts may not exceed the declared
  ``parking_slots``.
* ``charging`` - one charge pad at a charging-capable facility. The reservation
  carries a declared delivered-Wh quantity; the cost is derived from the
  facility's configured per-kWh price (never a live tariff lookup). No battery
  telemetry is fabricated. As a bounded physical-feasibility check the declared
  ``delivered_wh`` may never exceed the pad's power upper bound
  ``power_w * (end_s - start_s) / 3600``; that bound is a declared-capability
  corollary, never an invented measured battery draw.
* ``cargo_storage`` - kilograms stored at a hub. Aggregate overlapping mass may
  not exceed the declared ``storage_capacity_kg``.
* ``processing`` - hub processing derived from the declared throughput rate.
  A mass requires at least ``mass_kg / throughput_per_hour_kg`` seconds, and
  the aggregate processing rate over overlaps may not exceed the declared rate.

Semantics:

* Half-open intervals: ``[0, 10)`` and ``[10, 20)`` never overlap, so an
  interval ending at ``t`` and one starting at ``t`` do not share capacity.
* Reservation ids are unique for the lifetime of the ledger (never reused
  after a release). Operation ids are unique too, so re-applying a processed
  operation or an already-released reservation is an explicit error and replay
  of an identical operation stream is deterministic.
* A release removes exactly one active reservation; releasing a nonexistent or
  already-released reservation is rejected, so inventories can never go
  negative (occupancy is always the sum of positive active amounts).
* The ledger model re-derives every aggregate capacity invariant on
  construction, so any directly-built ledger snapshot that violates a declared
  capability is rejected the same way the operation path rejects it.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias, Union

from pydantic import Field, TypeAdapter, field_validator, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import (
    CurrencyCode,
    FacilityCapabilities,
    FacilityCatalogue,
    FacilityIdentifier,
)

EPSILON = 1e-9


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


def _positive_int(value: object, label: str) -> int:
    _reject_bool(value, label)
    if not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


class _IntervalFields(StrictModel):
    """Shared non-empty half-open interval fields with strict time values."""

    start_s: float
    end_s: float

    @field_validator("start_s", mode="before")
    @classmethod
    def _start_s_strict(cls, value: object) -> float:
        return _nonnegative(value, "start_s")

    @field_validator("end_s", mode="before")
    @classmethod
    def _end_s_strict(cls, value: object) -> float:
        return _finite(value, "end_s")

    @model_validator(mode="after")
    def interval_is_nonempty(self) -> "_IntervalFields":
        if self.end_s <= self.start_s:
            raise ValueError(
                "end_s must strictly follow start_s; empty half-open intervals "
                "are rejected"
            )
        return self


class ParkingReservation(_IntervalFields):
    """One parking-slot occupancy over a half-open interval."""

    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    slots: int
    holder: FacilityIdentifier | None = None

    @field_validator("slots", mode="before")
    @classmethod
    def _slots_strict(cls, value: object) -> int:
        return _positive_int(value, "slots")


class ChargingReservation(_IntervalFields):
    """One charge-pad occupancy plus the declared delivered-Wh quantity."""

    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    delivered_wh: float
    holder: FacilityIdentifier | None = None

    @field_validator("delivered_wh", mode="before")
    @classmethod
    def _delivered_wh_strict(cls, value: object) -> float:
        return _positive(value, "delivered_wh")


class CargoStorageReservation(_IntervalFields):
    """Kilograms stored at a hub over a half-open interval."""

    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    mass_kg: float

    @field_validator("mass_kg", mode="before")
    @classmethod
    def _mass_strict(cls, value: object) -> float:
        return _positive(value, "mass_kg")


class ProcessingReservation(_IntervalFields):
    """Hub processing occupancy with mass derived from declared throughput."""

    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    mass_kg: float

    @field_validator("mass_kg", mode="before")
    @classmethod
    def _mass_strict(cls, value: object) -> float:
        return _positive(value, "mass_kg")


class ChargingCost(StrictModel):
    """Derived charging cost. Planning calculation, never live billing."""

    amount: float
    currency: CurrencyCode
    unit: Literal["kWh"] = "kWh"

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_strict(cls, value: object) -> float:
        return _nonnegative(value, "amount")


class ReserveParking(_IntervalFields):
    kind: Literal["reserve_parking"]
    operation_id: FacilityIdentifier
    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    slots: int = 1
    holder: FacilityIdentifier | None = None

    @field_validator("slots", mode="before")
    @classmethod
    def _slots_strict(cls, value: object) -> int:
        return _positive_int(value, "slots")


class ReleaseParking(StrictModel):
    kind: Literal["release_parking"]
    operation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    reservation_id: FacilityIdentifier


class ReserveCharging(_IntervalFields):
    kind: Literal["reserve_charging"]
    operation_id: FacilityIdentifier
    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    delivered_wh: float
    holder: FacilityIdentifier | None = None

    @field_validator("delivered_wh", mode="before")
    @classmethod
    def _delivered_wh_strict(cls, value: object) -> float:
        return _positive(value, "delivered_wh")


class ReleaseCharging(StrictModel):
    kind: Literal["release_charging"]
    operation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    reservation_id: FacilityIdentifier


class ReserveCargo(_IntervalFields):
    kind: Literal["reserve_cargo"]
    operation_id: FacilityIdentifier
    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    mass_kg: float

    @field_validator("mass_kg", mode="before")
    @classmethod
    def _mass_strict(cls, value: object) -> float:
        return _positive(value, "mass_kg")


class ReleaseCargo(StrictModel):
    kind: Literal["release_cargo"]
    operation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    reservation_id: FacilityIdentifier


class ReserveProcessing(_IntervalFields):
    kind: Literal["reserve_processing"]
    operation_id: FacilityIdentifier
    reservation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    mass_kg: float

    @field_validator("mass_kg", mode="before")
    @classmethod
    def _mass_strict(cls, value: object) -> float:
        return _positive(value, "mass_kg")


class ReleaseProcessing(StrictModel):
    kind: Literal["release_processing"]
    operation_id: FacilityIdentifier
    facility_id: FacilityIdentifier
    reservation_id: FacilityIdentifier


ResourceOperation: TypeAlias = Annotated[
    Union[
        ReserveParking,
        ReleaseParking,
        ReserveCharging,
        ReleaseCharging,
        ReserveCargo,
        ReleaseCargo,
        ReserveProcessing,
        ReleaseProcessing,
    ],
    Field(discriminator="kind"),
]

_OPERATION_ADAPTER = TypeAdapter(ResourceOperation)


def parse_resource_operation(value: object) -> ResourceOperation:
    """Validate one raw operation (e.g. from JSON) against the union schema."""
    return _OPERATION_ADAPTER.validate_python(value)


class FacilityResourceLedger(StrictModel):
    """Immutable occupancy ledger bound to one facility catalogue.

    Each attribute is a tuple of active reservations. ``processed_operation_ids``
    tracks the applied operation ids so replay is deterministic and duplicates
    are rejected; ``released_reservation_ids`` tracks reservations that were
    released so a double release is a distinct explicit error.
    """

    catalogue: FacilityCatalogue
    parking: tuple[ParkingReservation, ...] = ()
    charging: tuple[ChargingReservation, ...] = ()
    cargo_storage: tuple[CargoStorageReservation, ...] = ()
    processing: tuple[ProcessingReservation, ...] = ()
    processed_operation_ids: tuple[FacilityIdentifier, ...] = ()
    released_reservation_ids: tuple[FacilityIdentifier, ...] = ()

    @model_validator(mode="after")
    def ledger_invariants(self) -> "FacilityResourceLedger":
        active = [
            reservation.reservation_id
            for reservation in (
                *self.parking,
                *self.charging,
                *self.cargo_storage,
                *self.processing,
            )
        ]
        if len(active) != len(set(active)):
            raise ValueError("reservation ids must be unique across the ledger")
        if set(active) & set(self.released_reservation_ids):
            raise ValueError(
                "released reservation ids cannot collide with active reservations"
            )
        if len(self.processed_operation_ids) != len(
            set(self.processed_operation_ids)
        ):
            raise ValueError("processed operation ids must be unique")
        self._verify_parking_capacity()
        self._verify_charging_capacity()
        self._verify_cargo_capacity()
        self._verify_processing_capacity()
        return self

    def _verify_parking_capacity(self) -> None:
        by_facility: dict[str, list[ParkingReservation]] = {}
        for reservation in self.parking:
            by_facility.setdefault(reservation.facility_id, []).append(reservation)
        for facility_id, reservations in by_facility.items():
            facility = self.catalogue.require(facility_id)
            landing = facility.landing
            if landing is None:
                raise ValueError(
                    f"parking reservation on facility without landing: {facility_id}"
                )
            events = [
                (reservation.start_s, float(reservation.slots))
                for reservation in reservations
            ] + [
                (reservation.end_s, -float(reservation.slots))
                for reservation in reservations
            ]
            if _max_concurrent_exceeded(events, float(landing.parking_slots)):
                raise ValueError(
                    f"parking oversubscription at {facility_id}: aggregate slots "
                    f"exceed the declared {landing.parking_slots}"
                )

    def _verify_charging_capacity(self) -> None:
        by_facility: dict[str, list[ChargingReservation]] = {}
        for reservation in self.charging:
            by_facility.setdefault(reservation.facility_id, []).append(reservation)
        for facility_id, reservations in by_facility.items():
            facility = self.catalogue.require(facility_id)
            charging = facility.charging
            if charging is None:
                raise ValueError(
                    f"charging reservation on facility without charging: {facility_id}"
                )
            # Bounded physical-feasibility check: a pad with declared power_w can
            # deliver at most power_w * duration/3600 Wh. This is a power upper
            # bound on the *declared* Wh, never an invented battery measurement.
            for reservation in reservations:
                bound_wh = charging.power_w * (
                    reservation.end_s - reservation.start_s
                ) / 3600.0
                if reservation.delivered_wh > bound_wh + EPSILON:
                    raise ValueError(
                        f"charging reservation at {facility_id}: delivered_wh "
                        f"{reservation.delivered_wh} exceeds the physical power "
                        f"upper bound of {bound_wh:.6f} Wh for the declared window"
                    )
            events = [
                (reservation.start_s, 1.0) for reservation in reservations
            ] + [
                (reservation.end_s, -1.0) for reservation in reservations
            ]
            if _max_concurrent_exceeded(events, float(charging.slots)):
                raise ValueError(
                    f"charging oversubscription at {facility_id}: aggregate slots "
                    f"exceed the declared {charging.slots}"
                )

    def _verify_cargo_capacity(self) -> None:
        by_facility: dict[str, list[CargoStorageReservation]] = {}
        for reservation in self.cargo_storage:
            by_facility.setdefault(reservation.facility_id, []).append(reservation)
        for facility_id, reservations in by_facility.items():
            facility = self.catalogue.require(facility_id)
            cargo = facility.cargo
            if cargo is None:
                raise ValueError(
                    f"cargo reservation on facility without hub cargo: {facility_id}"
                )
            events = [
                (reservation.start_s, reservation.mass_kg)
                for reservation in reservations
            ] + [
                (reservation.end_s, -reservation.mass_kg)
                for reservation in reservations
            ]
            if _max_concurrent_exceeded(events, cargo.storage_capacity_kg):
                raise ValueError(
                    f"cargo storage oversubscription at {facility_id}: aggregate "
                    f"kg exceed the declared {cargo.storage_capacity_kg}"
                )

    def _verify_processing_capacity(self) -> None:
        by_facility: dict[str, list[ProcessingReservation]] = {}
        for reservation in self.processing:
            by_facility.setdefault(reservation.facility_id, []).append(reservation)
        for facility_id, reservations in by_facility.items():
            facility = self.catalogue.require(facility_id)
            cargo = facility.cargo
            if cargo is None:
                raise ValueError(
                    f"processing reservation on facility without hub cargo: {facility_id}"
                )
            rate_capacity = cargo.throughput_per_hour_kg / 3600.0
            events: list[tuple[float, float]] = []
            for reservation in reservations:
                duration = reservation.end_s - reservation.start_s
                minimum = throughput_processing_seconds(
                    reservation.mass_kg, cargo.throughput_per_hour_kg
                )
                if duration < minimum - EPSILON:
                    raise ValueError(
                        f"processing window at {facility_id} is shorter than the "
                        f"declared-throughput time ({minimum:.6f}s)"
                    )
                rate = reservation.mass_kg / max(duration, EPSILON)
                events.append((reservation.start_s, rate))
                events.append((reservation.end_s, -rate))
            if _max_concurrent_exceeded(events, rate_capacity):
                raise ValueError(
                    f"processing oversubscription at {facility_id}: aggregate rate "
                    f"exceeds the declared throughput {cargo.throughput_per_hour_kg} kg/h"
                )


def empty_resource_ledger(catalogue: FacilityCatalogue) -> FacilityResourceLedger:
    """An empty ledger bound to the given immutable facility catalogue."""
    return FacilityResourceLedger(catalogue=catalogue)


def _max_concurrent_exceeded(events: list[tuple[float, float]], capacity: float) -> bool:
    events.sort(key=lambda item: (item[0], item[1]))
    occupied = 0.0
    for _, delta in events:
        occupied += delta
        if occupied > capacity + EPSILON:
            return True
    return False


def _active_reservation_ids(
    ledger: FacilityResourceLedger,
) -> set[str]:
    return {
        reservation.reservation_id
        for reservation in (
            *ledger.parking,
            *ledger.charging,
            *ledger.cargo_storage,
            *ledger.processing,
        )
    }


def _require_reservation_id_available(
    ledger: FacilityResourceLedger, reservation_id: str
) -> None:
    if reservation_id in _active_reservation_ids(ledger):
        raise ValueError(f"reservation_id is already active: {reservation_id}")
    if reservation_id in ledger.released_reservation_ids:
        raise ValueError(f"reservation_id was already used and released: {reservation_id}")


def _with_operation(
    ledger: FacilityResourceLedger,
    *,
    parking: tuple[ParkingReservation, ...] | None = None,
    charging: tuple[ChargingReservation, ...] | None = None,
    cargo_storage: tuple[CargoStorageReservation, ...] | None = None,
    processing: tuple[ProcessingReservation, ...] | None = None,
    operation_id: str,
    released_reservation_ids: tuple[str, ...] | None = None,
) -> FacilityResourceLedger:
    return FacilityResourceLedger(
        catalogue=ledger.catalogue,
        parking=ledger.parking if parking is None else parking,
        charging=ledger.charging if charging is None else charging,
        cargo_storage=(
            ledger.cargo_storage if cargo_storage is None else cargo_storage
        ),
        processing=ledger.processing if processing is None else processing,
        processed_operation_ids=(*ledger.processed_operation_ids, operation_id),
        released_reservation_ids=(
            ledger.released_reservation_ids
            if released_reservation_ids is None
            else released_reservation_ids
        ),
    )


def _release_from(
    reservations: tuple,
    facility_id: str,
    reservation_id: str,
) -> tuple:
    """Remove one active reservation; return the same tuple when absent."""
    for index, reservation in enumerate(reservations):
        if (
            reservation.reservation_id == reservation_id
            and reservation.facility_id == facility_id
        ):
            return (
                *reservations[:index],
                *reservations[index + 1 :],
            )
    return reservations


def apply_resource_operation(
    ledger: FacilityResourceLedger,
    operation: ResourceOperation,
) -> FacilityResourceLedger:
    """Apply one explicit operation and return the new immutable ledger.

    Re-applying a processed operation id is rejected; releasing a nonexistent
    or already-released reservation is rejected; capacity violations are
    rejected by the ledger model validator.
    """
    if operation.operation_id in ledger.processed_operation_ids:
        raise ValueError(
            f"operation_id was already processed: {operation.operation_id}"
        )
    kind = operation.kind
    if kind == "reserve_parking":
        _require_reservation_id_available(ledger, operation.reservation_id)
        ledger.catalogue.require(operation.facility_id)
        candidate = ParkingReservation(
            reservation_id=operation.reservation_id,
            facility_id=operation.facility_id,
            start_s=operation.start_s,
            end_s=operation.end_s,
            slots=operation.slots,
            holder=operation.holder,
        )
        return _with_operation(
            ledger,
            parking=(*ledger.parking, candidate),
            operation_id=operation.operation_id,
        )
    if kind == "release_parking":
        remaining = _release_from(
            ledger.parking,
            operation.facility_id,
            operation.reservation_id,
        )
        if remaining is ledger.parking:
            raise _release_error(ledger, operation.reservation_id)
        return _with_operation(
            ledger,
            parking=remaining,
            operation_id=operation.operation_id,
            released_reservation_ids=(
                *ledger.released_reservation_ids,
                operation.reservation_id,
            ),
        )
    if kind == "reserve_charging":
        _require_reservation_id_available(ledger, operation.reservation_id)
        ledger.catalogue.require(operation.facility_id)
        candidate = ChargingReservation(
            reservation_id=operation.reservation_id,
            facility_id=operation.facility_id,
            start_s=operation.start_s,
            end_s=operation.end_s,
            delivered_wh=operation.delivered_wh,
            holder=operation.holder,
        )
        return _with_operation(
            ledger,
            charging=(*ledger.charging, candidate),
            operation_id=operation.operation_id,
        )
    if kind == "release_charging":
        remaining = _release_from(
            ledger.charging,
            operation.facility_id,
            operation.reservation_id,
        )
        if remaining is ledger.charging:
            raise _release_error(ledger, operation.reservation_id)
        return _with_operation(
            ledger,
            charging=remaining,
            operation_id=operation.operation_id,
            released_reservation_ids=(
                *ledger.released_reservation_ids,
                operation.reservation_id,
            ),
        )
    if kind == "reserve_cargo":
        _require_reservation_id_available(ledger, operation.reservation_id)
        ledger.catalogue.require(operation.facility_id)
        candidate = CargoStorageReservation(
            reservation_id=operation.reservation_id,
            facility_id=operation.facility_id,
            start_s=operation.start_s,
            end_s=operation.end_s,
            mass_kg=operation.mass_kg,
        )
        return _with_operation(
            ledger,
            cargo_storage=(*ledger.cargo_storage, candidate),
            operation_id=operation.operation_id,
        )
    if kind == "release_cargo":
        remaining = _release_from(
            ledger.cargo_storage,
            operation.facility_id,
            operation.reservation_id,
        )
        if remaining is ledger.cargo_storage:
            raise _release_error(ledger, operation.reservation_id)
        return _with_operation(
            ledger,
            cargo_storage=remaining,
            operation_id=operation.operation_id,
            released_reservation_ids=(
                *ledger.released_reservation_ids,
                operation.reservation_id,
            ),
        )
    if kind == "reserve_processing":
        _require_reservation_id_available(ledger, operation.reservation_id)
        ledger.catalogue.require(operation.facility_id)
        candidate = ProcessingReservation(
            reservation_id=operation.reservation_id,
            facility_id=operation.facility_id,
            start_s=operation.start_s,
            end_s=operation.end_s,
            mass_kg=operation.mass_kg,
        )
        return _with_operation(
            ledger,
            processing=(*ledger.processing, candidate),
            operation_id=operation.operation_id,
        )
    if kind == "release_processing":
        remaining = _release_from(
            ledger.processing,
            operation.facility_id,
            operation.reservation_id,
        )
        if remaining is ledger.processing:
            raise _release_error(ledger, operation.reservation_id)
        return _with_operation(
            ledger,
            processing=remaining,
            operation_id=operation.operation_id,
            released_reservation_ids=(
                *ledger.released_reservation_ids,
                operation.reservation_id,
            ),
        )
    raise ValueError(f"unknown resource operation kind: {kind!r}")


def _release_error(
    ledger: FacilityResourceLedger, reservation_id: str
) -> ValueError:
    if reservation_id in ledger.released_reservation_ids:
        return ValueError(f"reservation was already released: {reservation_id}")
    return ValueError(f"reservation is not active: {reservation_id}")


def replay_resource_operations(
    catalogue: FacilityCatalogue,
    operations: tuple[ResourceOperation, ...],
) -> FacilityResourceLedger:
    """Replay a deterministic operation stream into the final ledger.

    Duplicate operation ids are rejected by :func:`apply_resource_operation`,
    so replaying the same stream always produces the identical ledger and any
    accidental duplicate is an explicit error.
    """
    ledger = empty_resource_ledger(catalogue)
    for operation in operations:
        ledger = apply_resource_operation(ledger, operation)
    return ledger


class FacilityResourceService:
    """Sequential operation service over an immutable ledger.

    Each :meth:`apply` advances to a new frozen
    :class:`FacilityResourceLedger`; the previous ledger remains untouched.
    """

    def __init__(self, catalogue: FacilityCatalogue) -> None:
        self._ledger = empty_resource_ledger(catalogue)

    @property
    def ledger(self) -> FacilityResourceLedger:
        return self._ledger

    def apply(self, operation: ResourceOperation) -> FacilityResourceLedger:
        self._ledger = apply_resource_operation(self._ledger, operation)
        return self._ledger


# ---------------------------------------------------------------------------
# Derived planning calculations and occupancy queries.
# ---------------------------------------------------------------------------


def throughput_processing_seconds(
    mass_kg: float, throughput_per_hour_kg: float
) -> float:
    """Minimum processing seconds derived from a declared throughput rate.

    ``mass_kg / throughput_per_hour_kg`` hours converted to seconds. This is a
    planning derivation from the declared capability; it is not a measurement.
    """
    if isinstance(mass_kg, bool) or isinstance(throughput_per_hour_kg, bool):
        raise ValueError("throughput inputs cannot be booleans")
    if not math.isfinite(float(mass_kg)) or float(mass_kg) <= 0:
        raise ValueError("mass_kg must be finite and positive")
    if (
        not isinstance(throughput_per_hour_kg, (int, float))
        or not math.isfinite(float(throughput_per_hour_kg))
        or float(throughput_per_hour_kg) <= 0
    ):
        raise ValueError("throughput_per_hour_kg must be finite and positive")
    return float(mass_kg) / float(throughput_per_hour_kg) * 3600.0


def charging_cost(
    facility: FacilityCapabilities,
    reservation: ChargingReservation,
) -> ChargingCost:
    """Derive the charging cost from the declared delivered Wh and price.

    The price is the configured quantity on the facility; there is no live
    tariff lookup and no battery telemetry is used. The cost result is a
    planning estimate for settlement labelling, never a billing claim.
    """
    charging = facility.charging
    if charging is None:
        raise ValueError(f"facility has no charging capability: {facility.facility_id}")
    if reservation.delivered_wh < 0:
        raise ValueError("delivered_wh must be nonnegative")
    kwh = reservation.delivered_wh / 1000.0
    return ChargingCost(
        amount=kwh * charging.price_amount,
        currency=charging.price_currency,
        unit="kWh",
    )


def parking_events(
    ledger: FacilityResourceLedger, facility_id: str
) -> tuple[tuple[float, int], ...]:
    """Sorted half-open occupancy events ``(time, delta_slots)``."""
    events = [
        (reservation.start_s, reservation.slots)
        for reservation in ledger.parking
        if reservation.facility_id == facility_id
    ] + [
        (reservation.end_s, -reservation.slots)
        for reservation in ledger.parking
        if reservation.facility_id == facility_id
    ]
    events.sort(key=lambda item: (item[0], item[1]))
    return tuple(events)


def parking_peak(ledger: FacilityResourceLedger, facility_id: str) -> int:
    events = sorted(
        parking_events(ledger, facility_id),
        key=lambda item: (item[0], item[1]),
    )
    occupied = 0
    peak = 0
    for _, delta in events:
        occupied += delta
        if occupied > peak:
            peak = occupied
    return peak


def parking_occupied_at(
    ledger: FacilityResourceLedger, facility_id: str, time_s: float
) -> int:
    """Parking slots active at ``time_s`` under half-open interval semantics."""
    return sum(
        1
        for reservation in ledger.parking
        if reservation.facility_id == facility_id
        and reservation.start_s <= time_s < reservation.end_s
        for _ in range(reservation.slots)
    )


def charging_peak(ledger: FacilityResourceLedger, facility_id: str) -> int:
    events = [
        (reservation.start_s, 1)
        for reservation in ledger.charging
        if reservation.facility_id == facility_id
    ] + [
        (reservation.end_s, -1)
        for reservation in ledger.charging
        if reservation.facility_id == facility_id
    ]
    events.sort(key=lambda item: (item[0], item[1]))
    occupied = 0
    peak = 0
    for _, delta in events:
        occupied += delta
        if occupied > peak:
            peak = occupied
    return peak


def charging_occupied_at(
    ledger: FacilityResourceLedger, facility_id: str, time_s: float
) -> int:
    return sum(
        1
        for reservation in ledger.charging
        if reservation.facility_id == facility_id
        and reservation.start_s <= time_s < reservation.end_s
    )


def cargo_kg_at(
    ledger: FacilityResourceLedger, facility_id: str, time_s: float
) -> float:
    """Kilograms stored at ``time_s``; always nonnegative by construction."""
    return sum(
        reservation.mass_kg
        for reservation in ledger.cargo_storage
        if reservation.facility_id == facility_id
        and reservation.start_s <= time_s < reservation.end_s
    )


def cargo_peak_kg(ledger: FacilityResourceLedger, facility_id: str) -> float:
    events = [
        (reservation.start_s, reservation.mass_kg)
        for reservation in ledger.cargo_storage
        if reservation.facility_id == facility_id
    ] + [
        (reservation.end_s, -reservation.mass_kg)
        for reservation in ledger.cargo_storage
        if reservation.facility_id == facility_id
    ]
    events.sort(key=lambda item: (item[0], item[1]))
    occupied = 0.0
    peak = 0.0
    for _, delta in events:
        occupied += delta
        if occupied > peak:
            peak = occupied
    return peak


def processing_rate_at(
    ledger: FacilityResourceLedger, facility_id: str, time_s: float
) -> float:
    """Aggregate processing rate (kg/s) active at ``time_s``; nonnegative."""
    return sum(
        reservation.mass_kg / (reservation.end_s - reservation.start_s)
        for reservation in ledger.processing
        if reservation.facility_id == facility_id
        and reservation.start_s <= time_s < reservation.end_s
    )


def processing_peak_rate(
    ledger: FacilityResourceLedger, facility_id: str
) -> float:
    events: list[tuple[float, float]] = []
    for reservation in ledger.processing:
        if reservation.facility_id != facility_id:
            continue
        rate = reservation.mass_kg / (reservation.end_s - reservation.start_s)
        events.append((reservation.start_s, rate))
        events.append((reservation.end_s, -rate))
    events.sort(key=lambda item: (item[0], item[1]))
    occupied = 0.0
    peak = 0.0
    for _, delta in events:
        occupied += delta
        if occupied > peak:
            peak = occupied
    return peak


def ledger_canonical_json(ledger: FacilityResourceLedger) -> bytes:
    """Deterministic canonical JSON encoding of the frozen ledger state."""
    return canonical_json_bytes(ledger.model_dump(mode="json"))


__all__ = [
    "CargoStorageReservation",
    "ChargingCost",
    "ChargingReservation",
    "FacilityResourceLedger",
    "FacilityResourceService",
    "ParkingReservation",
    "ProcessingReservation",
    "ReleaseCargo",
    "ReleaseCharging",
    "ReleaseParking",
    "ReleaseProcessing",
    "ReserveCargo",
    "ReserveCharging",
    "ReserveParking",
    "ReserveProcessing",
    "ResourceOperation",
    "apply_resource_operation",
    "cargo_kg_at",
    "cargo_peak_kg",
    "charging_cost",
    "charging_occupied_at",
    "charging_peak",
    "empty_resource_ledger",
    "ledger_canonical_json",
    "parking_events",
    "parking_occupied_at",
    "parking_peak",
    "parse_resource_operation",
    "processing_peak_rate",
    "processing_rate_at",
    "replay_resource_operations",
    "throughput_processing_seconds",
]
