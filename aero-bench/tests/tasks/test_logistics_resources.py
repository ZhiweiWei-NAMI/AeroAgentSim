from __future__ import annotations

import math

import pytest

from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import compile_authored_facilities
from aero_bench.tasks.logistics.resources import (
    FacilityResourceLedger,
    FacilityResourceService,
    ReserveCargo,
    ReserveCharging,
    ReserveParking,
    ReserveProcessing,
    ReleaseCargo,
    ReleaseCharging,
    ReleaseParking,
    ReleaseProcessing,
    apply_resource_operation,
    cargo_kg_at,
    cargo_peak_kg,
    charging_cost,
    charging_occupied_at,
    charging_peak,
    empty_resource_ledger,
    ledger_canonical_json,
    parking_occupied_at,
    parking_peak,
    parse_resource_operation,
    processing_peak_rate,
    processing_rate_at,
    replay_resource_operations,
    throughput_processing_seconds,
)

EPSILON = 1e-6


def _catalogue():
    return compile_authored_facilities(
        [
            {
                "id": "vp.01",
                "name": "vertiport",
                "kind": "vertiport",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 0, "z": 0},
                "rotationDeg": 0,
                "widthM": 20,
                "depthM": 20,
                "heightM": 5,
                "landing": {"parkingSlots": 2, "movementsPerHour": 30},
                "cargo": None,
                "charging": {
                    "slots": 1,
                    "powerW": 6000,
                    "priceAmount": 1.2,
                    "priceCurrency": "CNY",
                    "priceUnit": "kWh",
                },
            },
            {
                "id": "hub.01",
                "name": "hub",
                "kind": "hub",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 40, "z": 0},
                "rotationDeg": 0,
                "widthM": 18,
                "depthM": 14,
                "heightM": 5.2,
                "landing": {"parkingSlots": 3, "movementsPerHour": 20},
                "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 60},
                "charging": None,
            },
            {
                "id": "ch.01",
                "name": "charger",
                "kind": "charger",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 80, "z": 0},
                "rotationDeg": 0,
                "widthM": 10,
                "depthM": 8,
                "heightM": 3.2,
                "landing": None,
                "cargo": None,
                "charging": {
                    "slots": 1,
                    "powerW": 5000,
                    "priceAmount": 1.5,
                    "priceCurrency": "USD",
                    "priceUnit": "kWh",
                },
            },
        ]
    )


def test_empty_ledger_has_no_occupancy_and_no_operations() -> None:
    ledger = empty_resource_ledger(_catalogue())
    assert ledger.parking == ()
    assert ledger.charging == ()
    assert ledger.cargo_storage == ()
    assert ledger.processing == ()
    assert ledger.processed_operation_ids == ()
    for facility_id in ("vp.01", "hub.01", "ch.01"):
        assert parking_peak(ledger, facility_id) == 0
        assert cargo_kg_at(ledger, facility_id, 5.0) == 0.0
        assert charging_peak(ledger, facility_id) == 0


def _park(op_id: str, res_id: str, *, start: float, end: float, facility: str = "vp.01", slots: int = 1):
    return ReserveParking(
        kind="reserve_parking",
        operation_id=op_id,
        reservation_id=res_id,
        facility_id=facility,
        start_s=start,
        end_s=end,
        slots=slots,
    )


def _release_park(op_id: str, res_id: str, facility: str = "vp.01"):
    return ReleaseParking(
        kind="release_parking", operation_id=op_id, facility_id=facility, reservation_id=res_id
    )


def _cargo(op_id: str, res_id: str, *, mass: float, start: float, end: float, facility: str = "hub.01"):
    return ReserveCargo(
        kind="reserve_cargo",
        operation_id=op_id,
        reservation_id=res_id,
        facility_id=facility,
        mass_kg=mass,
        start_s=start,
        end_s=end,
    )


def _release_cargo(op_id: str, res_id: str, facility: str = "hub.01"):
    return ReleaseCargo(
        kind="release_cargo", operation_id=op_id, facility_id=facility, reservation_id=res_id
    )


def _charge(op_id: str, res_id: str, *, delivered_wh: float, start: float, end: float, facility: str = "ch.01"):
    return ReserveCharging(
        kind="reserve_charging",
        operation_id=op_id,
        reservation_id=res_id,
        facility_id=facility,
        delivered_wh=delivered_wh,
        start_s=start,
        end_s=end,
    )


def _release_charge(op_id: str, res_id: str, facility: str = "ch.01"):
    return ReleaseCharging(
        kind="release_charging", operation_id=op_id, facility_id=facility, reservation_id=res_id
    )


def _processing(op_id: str, res_id: str, *, mass: float, start: float, end: float, facility: str = "hub.01"):
    return ReserveProcessing(
        kind="reserve_processing",
        operation_id=op_id,
        reservation_id=res_id,
        facility_id=facility,
        mass_kg=mass,
        start_s=start,
        end_s=end,
    )


def _release_processing(op_id: str, res_id: str, facility: str = "hub.01"):
    return ReleaseProcessing(
        kind="release_processing", operation_id=op_id, facility_id=facility, reservation_id=res_id
    )


def test_aggregate_cargo_overlap_rejects_beyond_capacity_though_each_under() -> None:
    catalogue = _catalogue()
    ledger = empty_resource_ledger(catalogue)
    ledger = apply_resource_operation(ledger, _cargo("c.0", "cr.0", mass=40, start=0, end=100))
    ledger = apply_resource_operation(ledger, _cargo("c.1", "cr.1", mass=40, start=50, end=150))
    assert cargo_peak_kg(ledger, "hub.01") == pytest.approx(80.0)
    # Each per-order 40 <= 100, but the third adds 40 into an overlap at ~80..100
    # pushing the aggregate to 120 > 100.
    with pytest.raises(ValueError, match="oversubscription"):
        apply_resource_operation(ledger, _cargo("c.2", "cr.2", mass=40, start=60, end=160))
    # A non-overlapping extra reservation fits.
    ledger = apply_resource_operation(ledger, _cargo("c.3", "cr.3", mass=10, start=200, end=300))
    assert cargo_peak_kg(ledger, "hub.01") == pytest.approx(80.0)


def test_aggregate_parking_overlap_rejects_beyond_declared_slots() -> None:
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _park("p.0", "pr.0", start=0, end=20))
    ledger = apply_resource_operation(ledger, _park("p.1", "pr.1", start=10, end=30))
    assert parking_peak(ledger, "vp.01") == 2
    with pytest.raises(ValueError, match="oversubscription"):
        apply_resource_operation(ledger, _park("p.2", "pr.2", start=15, end=25))


def test_adjacent_half_open_intervals_do_not_overlap() -> None:
    ledger = empty_resource_ledger(_catalogue())
    # [0,10) then [10,20): at t=10 the first has ended and the second starts.
    ledger = apply_resource_operation(ledger, _park("p.0", "pr.0", start=0, end=10))
    ledger = apply_resource_operation(ledger, _park("p.1", "pr.1", start=10, end=20))
    assert parking_peak(ledger, "vp.01") == 1
    assert parking_occupied_at(ledger, "vp.01", 9.999) == 1
    assert parking_occupied_at(ledger, "vp.01", 10.0) == 1

    cargo_ledger = empty_resource_ledger(_catalogue())
    cargo_ledger = apply_resource_operation(cargo_ledger, _cargo("c.0", "cr.0", mass=50, start=0, end=10))
    cargo_ledger = apply_resource_operation(cargo_ledger, _cargo("c.1", "cr.1", mass=60, start=10, end=20))
    assert cargo_peak_kg(cargo_ledger, "hub.01") == pytest.approx(60.0)


def test_identical_integer_times_on_boundary_share_capacity_correctly() -> None:
    # A slot ending exactly when another starts uses the same capacity unit at
    # most once: [0,10) + [10,20) on a single-slot facility is legal.
    vp = compile_authored_facilities(
        [
            {
                "id": "vp.1",
                "name": "single",
                "kind": "vertiport",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 0, "z": 0},
                "rotationDeg": 0,
                "widthM": 14,
                "depthM": 8,
                "heightM": 4,
                "landing": {"parkingSlots": 1, "movementsPerHour": 5},
                "cargo": None,
                "charging": None,
            }
        ]
    )
    ledger = empty_resource_ledger(vp)
    ledger = apply_resource_operation(ledger, _park("p.0", "pr.0", start=0, end=10, facility="vp.1"))
    ledger = apply_resource_operation(ledger, _park("p.1", "pr.1", start=10, end=20, facility="vp.1"))
    assert parking_peak(ledger, "vp.1") == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"mass_kg": 0},
        {"mass_kg": -1},
        {"mass_kg": math.nan},
        {"mass_kg": math.inf},
        {"mass_kg": True},
        {"delivered_wh": 0},
        {"delivered_wh": -1},
        {"delivered_wh": math.nan},
        {"delivered_wh": True},
        {"slots": 0},
        {"slots": -1},
        {"slots": True},
        {"end_s": math.nan},
        {"start_s": -1},
        {"start_s": math.nan},
    ],
)
def test_zero_negative_and_nonfinite_quantities_are_rejected(bad: dict[str, object]) -> None:
    if "mass_kg" in bad:
        with pytest.raises(ValueError):
            apply_resource_operation(
                empty_resource_ledger(_catalogue()),
                _cargo("c.0", "cr.0", mass=1.0, start=0, end=10).model_copy(update=bad),
            )
    elif "delivered_wh" in bad:
        with pytest.raises(ValueError):
            apply_resource_operation(
                empty_resource_ledger(_catalogue()),
                _charge("ch.0", "chr.0", delivered_wh=10, start=0, end=10).model_copy(update=bad),
            )
    elif "slots" in bad:
        with pytest.raises(ValueError):
            apply_resource_operation(
                empty_resource_ledger(_catalogue()),
                _park("p.0", "pr.0", start=0, end=10).model_copy(update=bad),
            )
    else:
        with pytest.raises(ValueError):
            apply_resource_operation(
                empty_resource_ledger(_catalogue()),
                _park("p.0", "pr.0", start=0, end=10).model_copy(update=bad),
            )
        with pytest.raises(ValueError):
            apply_resource_operation(
                empty_resource_ledger(_catalogue()),
                _cargo("c.0", "cr.0", mass=1, start=0, end=10).model_copy(update=bad),
            )


def test_empty_intervals_are_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        apply_resource_operation(
            empty_resource_ledger(_catalogue()), _park("p.0", "pr.0", start=10, end=10)
        )
    with pytest.raises(ValueError, match="follow"):
        apply_resource_operation(
            empty_resource_ledger(_catalogue()), _park("p.1", "pr.1", start=20, end=10)
        )


def test_charging_oversubscription() -> None:
    charger = compile_authored_facilities(
        [
            {
                "id": "ch.2",
                "name": "one pad",
                "kind": "charger",
                "placement": "ground",
                "buildingId": None,
                "supportHeightM": None,
                "position": {"x": 0, "z": 0},
                "rotationDeg": 0,
                "widthM": 10,
                "depthM": 8,
                "heightM": 3.2,
                "landing": None,
                "cargo": None,
                "charging": {
                    "slots": 1,
                    "powerW": 5000,
                    "priceAmount": 1,
                    "priceCurrency": "EUR",
                    "priceUnit": "kWh",
                },
            }
        ]
    )
    ledger = empty_resource_ledger(charger)
    ledger = apply_resource_operation(ledger, _charge("c.0", "cr.0", delivered_wh=50, start=0, end=50, facility="ch.2"))
    assert charging_peak(ledger, "ch.2") == 1
    with pytest.raises(ValueError, match="oversubscription"):
        apply_resource_operation(ledger, _charge("c.1", "cr.1", delivered_wh=50, start=25, end=75, facility="ch.2"))
    # Non-overlapping fits.
    ledger = apply_resource_operation(ledger, _charge("c.2", "cr.2", delivered_wh=50, start=60, end=100, facility="ch.2"))
    assert charging_peak(ledger, "ch.2") == 1


def test_charging_reservation_at_exact_power_bound_is_accepted() -> None:
    # The power bound is a bounded physical-feasibility cap on the declared Wh:
    # a 5 kW pad over one hour can deliver at most 5000 Wh. The exact bound is
    # accepted with numeric epsilon tolerance (never a strict > that rejects
    # round-off-identical values).
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(
        ledger, _charge("c.0", "cr.0", delivered_wh=5000, start=0, end=3600)
    )
    assert ledger.charging[0].delivered_wh == pytest.approx(5000.0)
    assert charging_peak(ledger, "ch.01") == 1


def test_charging_reservation_above_power_bound_is_rejected() -> None:
    # 5001 Wh from the same 5 kW pad in one hour would need more than the pad's
    # declared power; the impossible reservation is rejected during ledger
    # validation.
    with pytest.raises(ValueError, match="power upper bound"):
        apply_resource_operation(
            empty_resource_ledger(_catalogue()),
            _charge("c.1", "cr.1", delivered_wh=5001, start=0, end=3600),
        )


def test_charging_short_interval_cannot_declare_large_energy() -> None:
    # 2 Wh claimed over a 1-second window on the 5 kW pad is above its physical
    # upper bound (~1.39 Wh), so the reservation is rejected; a feasible 1 Wh
    # in the same short window is accepted.
    with pytest.raises(ValueError, match="power upper bound"):
        apply_resource_operation(
            empty_resource_ledger(_catalogue()),
            _charge("c.2", "cr.2", delivered_wh=2, start=0, end=1),
        )
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(
        ledger, _charge("c.3", "cr.3", delivered_wh=1, start=0, end=1)
    )
    assert charging_peak(ledger, "ch.01") == 1


def test_charging_power_bound_preserves_cost_units_and_adjacent_reservations() -> None:
    catalogue = _catalogue()
    # The bound is per-reservation, not aggregate: adjacent half-open
    # reservations each at their own exact power bound ([0,3600) -> 5000 Wh,
    # [3600,5400) -> 2500 Wh) both fit on the single 5 kW pad and the peak
    # stays 1.
    ledger = empty_resource_ledger(catalogue)
    ledger = apply_resource_operation(
        ledger, _charge("c.0", "cr.0", delivered_wh=5000, start=0, end=3600)
    )
    ledger = apply_resource_operation(
        ledger, _charge("c.1", "cr.1", delivered_wh=2500, start=3600, end=5400)
    )
    assert charging_peak(ledger, "ch.01") == 1
    # Cost derivation is unchanged: declared Wh / 1000 * price with the fixed
    # kWh unit and the facility's configured currency.
    cost = charging_cost(catalogue.require("ch.01"), ledger.charging[0])
    assert cost.amount == pytest.approx(7.5)  # 5 kWh @ USD 1.5
    assert cost.currency == "USD"
    assert cost.unit == "kWh"
    assert charging_cost(catalogue.require("ch.01"), ledger.charging[1]).amount == pytest.approx(3.75)


def test_ledger_snapshot_rejects_physically_impossible_charging() -> None:
    # Directly-built ledger snapshots go through the same ledger validation: a
    # 600 Wh claim over 60 s on the 5 kW pad (max ~83.33 Wh) is rejected the
    # same way the operation path rejects it.
    catalogue = _catalogue()
    base = empty_resource_ledger(catalogue).model_dump()
    with pytest.raises(ValueError, match="power upper bound"):
        FacilityResourceLedger.model_validate(
            {
                **base,
                "charging": (
                    {
                        "reservation_id": "res.9",
                        "facility_id": "ch.01",
                        "start_s": 0,
                        "end_s": 60,
                        "delivered_wh": 600,
                    },
                ),
            }
        )


def test_process_reservation_requires_declared_throughput_time() -> None:
    catalogue = _catalogue()
    hub = catalogue.require("hub.01")
    assert hub.cargo is not None
    minimum = throughput_processing_seconds(1.0, hub.cargo.throughput_per_hour_kg)
    assert minimum == pytest.approx(60.0)  # 1 kg @ 60 kg/h == 60 s
    # A window shorter than the derived processing time is per-order infeasible.
    with pytest.raises(ValueError, match="shorter"):
        apply_resource_operation(
            empty_resource_ledger(catalogue),
            _processing("p.0", "pr.0", mass=1.0, start=0, end=minimum - 1),
        )
    ledger = apply_resource_operation(
        empty_resource_ledger(catalogue),
        _processing("p.1", "pr.1", mass=1.0, start=0, end=minimum),
    )
    assert processing_rate_at(ledger, "hub.01", 1.0) == pytest.approx(1.0 / 60.0)


def test_processing_aggregate_rate_oversubscription() -> None:
    catalogue = _catalogue()
    # rate capacity = 60 kg/h = 1/60 kg/s. Two 0.25 kg / 30s windows overlapping
    # demand 1/120 each = 2/120 = 1/60 at the overlap (exactly at capacity); a
    # third concurrent window pushes the aggregate over the declared rate.
    ledger = empty_resource_ledger(catalogue)
    ledger = apply_resource_operation(ledger, _processing("p.0", "pr.0", mass=0.25, start=0, end=30))
    ledger = apply_resource_operation(ledger, _processing("p.1", "pr.1", mass=0.25, start=15, end=45))
    assert processing_rate_at(ledger, "hub.01", 20.0) == pytest.approx(1.0 / 60.0)
    with pytest.raises(ValueError, match="oversubscription"):
        apply_resource_operation(ledger, _processing("p.2", "pr.2", mass=0.25, start=10, end=40))


def test_release_unknown_and_double_release_are_rejected() -> None:
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _cargo("c.0", "cr.0", mass=10, start=0, end=100))
    with pytest.raises(ValueError, match="not active"):
        apply_resource_operation(ledger, _release_cargo("r.0", "missing.reservation"))
    ledger = apply_resource_operation(ledger, _release_cargo("r.1", "cr.0"))
    assert cargo_kg_at(ledger, "hub.01", 50.0) == 0.0
    with pytest.raises(ValueError, match="already released"):
        apply_resource_operation(ledger, _release_cargo("r.2", "cr.0"))
    with pytest.raises(ValueError, match="already released"):
        apply_resource_operation(ledger, _release_park("r.3", "cr.0"))


def test_release_cannot_take_inventory_negative() -> None:
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _cargo("c.0", "cr.0", mass=10, start=0, end=100))
    ledger = apply_resource_operation(ledger, _release_cargo("r.0", "cr.0"))
    # Releasing again would go negative and is rejected outright.
    with pytest.raises(ValueError, match="already released"):
        apply_resource_operation(ledger, _release_cargo("r.1", "cr.0"))
    for sampled in (0.0, 50.0, 99.999, 100.0):
        assert cargo_kg_at(ledger, "hub.01", sampled) >= 0.0
    assert cargo_peak_kg(ledger, "hub.01") == 0.0


def test_duplicate_operation_id_and_reservation_id_are_rejected() -> None:
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _park("op.1", "res.1", start=0, end=10))
    with pytest.raises(ValueError, match="operation_id was already processed"):
        apply_resource_operation(ledger, _park("op.1", "res.2", start=0, end=10))
    with pytest.raises(ValueError, match="already active"):
        apply_resource_operation(ledger, _park("op.2", "res.1", start=0, end=10))
    ledger = apply_resource_operation(ledger, _release_park("op.3", "res.1"))
    # A stale reservation id cannot be reused after release.
    with pytest.raises(ValueError, match="already used and released"):
        apply_resource_operation(ledger, _park("op.4", "res.1", start=0, end=10))


def test_reservation_ids_are_unique_across_resource_kinds() -> None:
    # A parking an a cargo reservation cannot share one id.
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _park("op.1", "shared", start=0, end=10))
    with pytest.raises(ValueError, match="already active"):
        apply_resource_operation(ledger, _cargo("op.2", "shared", mass=5, start=0, end=10))


def test_invalid_facility_references_are_rejected() -> None:
    ledger = empty_resource_ledger(_catalogue())
    with pytest.raises(ValueError, match="unknown facility reference"):
        apply_resource_operation(ledger, _park("op.1", "res.1", start=0, end=10, facility="nope"))
    with pytest.raises(ValueError, match="without landing"):
        apply_resource_operation(ledger, _park("op.2", "res.2", start=0, end=10, facility="ch.01"))
    with pytest.raises(ValueError, match="without hub cargo"):
        apply_resource_operation(ledger, _cargo("op.3", "res.3", mass=5, start=0, end=10, facility="vp.01"))


def test_charging_cost_uses_declared_wh_and_configured_price_per_kwh() -> None:
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _charge("c.0", "cr.0", delivered_wh=1000, start=0, end=720))
    reservation = ledger.charging[0]
    cost = charging_cost(_catalogue().require("ch.01"), reservation)
    assert cost.amount == pytest.approx(1.5)  # 1 kWh @ USD 1.5
    assert cost.currency == "USD"
    assert cost.unit == "kWh"
    # A smaller charge scales proportionally.
    ledger2 = apply_resource_operation(
        empty_resource_ledger(_catalogue()), _charge("c.1", "cr.1", delivered_wh=200, start=0, end=144)
    )
    assert charging_cost(_catalogue().require("ch.01"), ledger2.charging[0]).amount == pytest.approx(0.3)


def test_price_currency_is_a_configured_quantity_with_a_fixed_unit() -> None:
    catalogue = _catalogue()
    ledger = empty_resource_ledger(catalogue)
    ledger = apply_resource_operation(ledger, _charge("c.0", "cr.0", delivered_wh=1000, start=0, end=720))
    cost = charging_cost(catalogue.require("ch.01"), ledger.charging[0])
    assert cost.currency == "USD"
    # Unit cannot be inferred: the charged model keeps the explicit kWh unit.
    assert cost.unit == "kWh"
    with pytest.raises(ValueError):
        parse_resource_operation(
            {
                "kind": "reserve_charging",
                "operation_id": "x",
                "reservation_id": "y",
                "facility_id": "ch.01",
                "delivered_wh": 10,
                "start_s": 0,
                "end_s": 5,
                "priceUnit": "MWh",
            }
        )


def test_ledger_state_validates_direct_snapshots_like_the_operation_path() -> None:
    catalogue = _catalogue()
    base = empty_resource_ledger(catalogue).model_dump()
    with pytest.raises(ValueError, match="oversubscription"):
        FacilityResourceLedger.model_validate(
            {
                **base,
                "parking": (
                    {
                        "reservation_id": "res.1",
                        "facility_id": "vp.01",
                        "start_s": 0,
                        "end_s": 20,
                        "slots": 3,
                    },
                ),
            }
        )
    with pytest.raises(ValueError, match="unique"):
        FacilityResourceLedger.model_validate(
            {
                **base,
                "parking": (
                    {
                        "reservation_id": "same",
                        "facility_id": "vp.01",
                        "start_s": 0,
                        "end_s": 5,
                        "slots": 1,
                    },
                    {
                        "reservation_id": "same",
                        "facility_id": "vp.01",
                        "start_s": 5,
                        "end_s": 10,
                        "slots": 1,
                    },
                ),
            }
        )


def test_deterministic_canonical_serialization_and_replay() -> None:
    catalogue = _catalogue()
    operations = (
        _park("op.1", "res.1", start=0, end=10),
        _cargo("op.2", "res.2", mass=5, start=0, end=20),
        _charge("op.3", "res.3", delivered_wh=120, start=0, end=360),
        _processing("op.4", "res.4", mass=0.5, start=0, end=30),
    )
    replayed = replay_resource_operations(catalogue, operations)
    json_bytes = ledger_canonical_json(replayed)
    assert json_bytes == canonical_json_bytes(replayed.model_dump(mode="json"))
    # Replaying the identical operation stream yields the identical ledger.
    assert ledger_canonical_json(replay_resource_operations(catalogue, operations)) == json_bytes
    # A duplicate in the stream is an explicit error, never silently deduped.
    with pytest.raises(ValueError, match="operation_id was already processed"):
        replay_resource_operations(catalogue, (*operations, operations[0]))
    # The json is canonical JSON (compact, sorted keys).
    assert b", " not in json_bytes


def test_service_applies_sequence_deterministically() -> None:
    catalogue = _catalogue()
    service = FacilityResourceService(catalogue)
    assert service.ledger.processed_operation_ids == ()
    service.apply(_park("op.1", "res.1", start=0, end=10))
    service.apply(_cargo("op.2", "res.2", mass=5, start=0, end=10))
    first = service.ledger
    assert parking_peak(first, "vp.01") == 1
    assert cargo_kg_at(first, "hub.01", 5.0) == pytest.approx(5.0)
    with pytest.raises(ValueError):
        service.apply(_park("op.1", "res.9", start=0, end=10))  # duplicate op id
    assert service.ledger is first  # failed operations leave the ledger untouched


def test_parse_resource_operation_accepts_raw_json_shapes() -> None:
    raw = {
        "kind": "reserve_parking",
        "operation_id": "op.1",
        "reservation_id": "res.1",
        "facility_id": "vp.01",
        "start_s": 0,
        "end_s": 10,
        "slots": 1,
    }
    parsed = parse_resource_operation(raw)
    assert parsed.kind == "reserve_parking"
    assert parsed.operation_id == "op.1"
    with pytest.raises(ValueError):
        parse_resource_operation({**raw, "kind": "reserve_flight"})
    with pytest.raises(ValueError):
        parse_resource_operation({**raw, "extra": True})


def test_throughput_processing_seconds_rejects_bad_inputs() -> None:
    assert throughput_processing_seconds(1.0, 3600.0) == pytest.approx(1.0)
    assert throughput_processing_seconds(2.0, 1.0) == pytest.approx(7200.0)
    for mass, throughput in ((0, 1), (-1, 1), (float("nan"), 1), (1, 0), (1, -1), (1, float("inf")), (True, 1), (1, True)):
        with pytest.raises(ValueError):
            throughput_processing_seconds(mass, throughput)


def test_occupancy_queries_never_report_negative_mass_or_rate() -> None:
    ledger = empty_resource_ledger(_catalogue())
    ledger = apply_resource_operation(ledger, _cargo("c.0", "cr.0", mass=10, start=0, end=50))
    ledger = apply_resource_operation(ledger, _processing("p.0", "pr.0", mass=0.5, start=0, end=30))
    for sampled in (-1.0, 0.0, 25.0, 60.0):
        assert cargo_kg_at(ledger, "hub.01", sampled) >= 0.0
        assert processing_rate_at(ledger, "hub.01", sampled) >= 0.0
        assert parking_occupied_at(ledger, "vp.01", sampled) >= 0
        assert charging_occupied_at(ledger, "ch.01", sampled) >= 0
