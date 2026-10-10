"""Focused module-level tests for the deterministic dwell/transition kernel.

The kernel (:mod:`aero_bench.tasks.logistics.dwell_eligibility`) is a pure
evaluation of one *contiguous closed-stage observation window* for pickup /
hub handoff / delivery / charge-start eligibility.  These tests use an honest
synthetic eligibility fixture built directly from the accepted package,
presence kernel and physical-observation record contracts (no PX4/Gazebo, no
executor, no resolver wiring).  Adversarial windows cover: wrong pad, gap,
collision, reordered time, forged profile, evidence laundering, unverified /
missing / wrong-verifier / forged provenance, run mismatch, order mismatch,
facility-kind mismatch and the ordered journal extraction contract.

The committed worktree lacks the separate executor-world-staging worker's
delivery (``aero_bench/world/resolved.py`` and the scene-state surface of
``aero_bench/runtime/contracts.py``); ``tests/tasks/conftest.py`` bridges that
import surface for the focused suite only.  The kernel still exercises every
accepted check against the immutable package and the presence kernel.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    LOGISTICS_SCHEMA_VERSION,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.dwell_eligibility import (
    DWELL_PROVENANCE_NOTE,
    DWELL_PROVENANCE_SCHEMA_VERSION,
    DWELL_TRANSITION_SCHEMA_VERSION,
    VERIFIED_PROVENANCE_PREREQUISITE,
    DeclaredDwellTransition,
    DwellEligibilityError,
    VerifiedDwellProvenance,
    assess_dwell_eligibility,
    collect_dwell_window,
    dwell_window_digest,
    journal_slice_eligibility,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import (
    AircraftPresenceProfile,
    MeasuredAircraftSample,
    PresenceEventBinding,
    PresenceTolerances,
    assess_facility_presence,
)
from aero_bench.tasks.logistics import physical_observations as _po_mod
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION,
    ObservationJournal,
    ObservationJournalRecord,
)
from aero_bench.tasks.logistics.physical_observations import (
    CLEARANCE_SCOPE_NOTE,
    DeclaredAircraftPoseReference,
    FacilityPadPhysicalObservation,
    LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION,
    SourceFrameOrigin,
    observation_digest_value,
)

_RUN_ID = "a" * 64
_SCENARIO_DIGEST = "b" * 64
_OTHER_RUN_ID = "c" * 64
_PROVENANCE_NOTE = _po_mod._OBSERVATION_PROVENANCE_NOTE
_TOLERANCES = PresenceTolerances(
    vertical_tolerance_m=0.05,
    horizontal_uncertainty_m=0.0,
    max_stationary_speed_m_s=0.5,
)


# ------------------------------------------------------------------ package


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
        "widthM": 18,  # renderer-feasible: materialises exactly 3 pads
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


def _order(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "order-1",
        "sourceFacilityId": "facility-1",
        "destinationFacilityId": "facility-2",
        "hubHandoffFacilityId": "hub-3",
        "cargoKg": 1,
        "releaseAtS": 0,
        "deliverByS": 600,
    }
    value.update(overrides)
    return value


def _scene(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "scene_id": "world.city-demo",
        "coordinate_frame": "scene_east_south_m",
        "origin_latitude_deg": 39.916,
        "origin_longitude_deg": 116.397,
        "origin_altitude_m": 43.0,
        "scene_source_sha256": "ab" * 32,
    }
    value.update(overrides)
    return value


def _package_document_fixture() -> dict[str, object]:
    return {
        "schema_version": LOGISTICS_SCHEMA_VERSION,
        "package_id": LOGISTICS_PACKAGE_ID,
        "task_id": "logistics.task",
        "verifier_id": "logistics.verifier",
        "scene": _scene(),
        "facilities": [
            _vertiport(),
            _vertiport(id="facility-2", widthM=18),
            _hub(),
            _charger(),
        ],
        "fleet": [
            {
                "id": "fleet-alpha",
                "assetId": "model:logistics-drone-v1",
                "count": 1,
                "homeFacilityId": "facility-1",
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 5,
            }
        ],
        "performanceProfiles": [
            {
                "fleetEntryId": "fleet-alpha",
                "sourceLabel": "operator estimate",
                "provenance": "selected-city v3 planning data",
                "aircraftBody": {"xM": 0.6, "yM": 0.6, "zM": 0.3},
                "cruiseSpeedMps": 15,
                "cruisePowerW": 900,
                "hoverPowerW": 700,
                "chargeEfficiency": 0.85,
            }
        ],
        "orders": [_order()],
        "noFlyZones": [
            {
                "id": "nofly.city-center",
                "name": "City center keep-out",
                "polygon": [
                    {"x": 10, "z": -20},
                    {"x": 30, "z": -20},
                    {"x": 20, "z": -40},
                ],
                "floorM": 0,
                "ceilingM": 120,
                "startsAtS": 0,
                "endsAtS": None,
                "source": {"kind": "manual", "label": "planner", "uri": None},
            }
        ],
        "actors": [
            {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
            {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
            {"actor_id": "logistics.business", "role": "business"},
        ],
    }


@pytest.fixture(scope="module")
def package():
    return lower_logistics_task_package(_package_document_fixture())


# --------------------------------------------------------- synthetic fixture


def _observation(
    package,
    *,
    at: SimulationTime,
    yaw_deg: float = 15.0,
    facility_id: str = "facility-1",
    pad_index: int = 0,
    aircraft_id: str = "fleet-alpha:1",
    vehicle_id: str = "uav.alpha",
    run_id: str = _RUN_ID,
    scenario_digest: str = _SCENARIO_DIGEST,
    provider_id: str = "flight",
) -> FacilityPadPhysicalObservation:
    """One honest synthetic closed-stage physical observation on a declared pad.

    The record is assembled from the exact accepted contracts: the canonical pad
    geometry, a scene-frame grounded sample, the declared canonical presence
    profile (package performance profile body extent + pose-reference
    calibration), the expected event binding and the real single-sample presence
    assessment (recomputed by the presence kernel), then a self-consistent
    canonical observation digest.
    """
    pad = facility_landing_pads(package.facilities.require(facility_id))[pad_index]
    profile = AircraftPresenceProfile(
        aircraft_id=vehicle_id,
        body_width_m=0.6,
        body_depth_m=0.3,
        pose_reference_above_contact_m=0.1,
    )
    source_event_id = f"state.{vehicle_id}.{at.tick}"
    sample = MeasuredAircraftSample(
        sample_ref=source_event_id,
        run_id=run_id,
        provider_id=provider_id,
        aircraft_id=vehicle_id,
        at=at,
        frame="scene_east_south_m",
        x=pad.x,
        y=pad.y + 0.1,
        z=pad.z,
        body_yaw_deg=yaw_deg,
        velocity_east_m_s=0.0,
        velocity_up_m_s=0.0,
        velocity_south_m_s=0.0,
        landed=True,
        in_air=False,
        provenance="caller_supplied",
        evidence_ref=source_event_id,
    )
    event_binding = PresenceEventBinding(
        run_id=run_id,
        provider_id=provider_id,
        aircraft_id=vehicle_id,
        at=at,
        evidence_ref=source_event_id,
    )
    assessment = assess_facility_presence(
        pad=pad,
        sample=sample,
        profile=profile,
        event=event_binding,
        tolerances=_TOLERANCES,
    )
    assert assessment.eligible, assessment.failure_code
    fields = {
        "schema_version": LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION,
        "facility_id": facility_id,
        "pad_index": pad_index,
        "aircraft_id": aircraft_id,
        "fleet_entry_id": "fleet-alpha",
        "visual_asset_id": "model:logistics-drone-v1",
        "provider_id": provider_id,
        "native_vehicle_id": vehicle_id,
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": at,
        "frame": "scene_east_south_m",
        "pad": pad,
        "sample": sample,
        "profile": profile,
        "event_binding": event_binding,
        "assessment": assessment,
        "source_event_id": source_event_id,
        "source_state_sample_digest": "ab" * 32,
        "source_scene_state_digest": f"{at.tick:02d}" * 32,
        "source_stage_barrier_digest": f"{at.tick:04d}" * 16,
        "source_evidence_path": "trajectory/evidences.jsonl",
        "source_evidence_sha256": "1" * 64,
        "source_frame_origin": SourceFrameOrigin(
            latitude_deg=39.916,
            longitude_deg=116.397,
            ellipsoid_height_m=43.0,
            scene_frame="scene_east_south_m",
        ),
        "single_point_in_time": True,
        "provenance_declared": "caller_supplied",
        "provenance_verified": False,
        "provenance_note": _PROVENANCE_NOTE,
        "measured_roll_deg": 0.0,
        "measured_pitch_deg": 0.0,
        "measured_yaw_deg": yaw_deg,
        "collision_contact": False,
        "footprint_basis": "yaw_only_declared_body_footprint",
        "clearance_note": CLEARANCE_SCOPE_NOTE,
        "observation_digest": "0" * 64,
    }
    candidate = FacilityPadPhysicalObservation.model_construct(**fields)
    fields["observation_digest"] = observation_digest_value(candidate)
    return FacilityPadPhysicalObservation(**fields)


def _dwell_tick(tick: int) -> SimulationTime:
    return SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)


def _dwell_window(package, *, ticks: tuple[int, ...], **kwargs) -> tuple[FacilityPadPhysicalObservation, ...]:
    return tuple(
        _observation(package, at=_dwell_tick(tick), **kwargs) for tick in ticks
    )


def _honest_pickup_window(package) -> tuple[FacilityPadPhysicalObservation, ...]:
    return _dwell_window(package, ticks=(1, 2, 3))


def _transition(**overrides: object) -> DeclaredDwellTransition:
    value: dict[str, object] = {
        "schema_version": DWELL_TRANSITION_SCHEMA_VERSION,
        "kind": "pickup",
        "run_id": _RUN_ID,
        "provider_id": "flight",
        "aircraft_id": "fleet-alpha:1",
        "facility_id": "facility-1",
        "pad_index": 0,
        "order_id": "order-1",
        "minimum_dwell_s": 2.0,
    }
    value.update(overrides)
    return DeclaredDwellTransition(**value)


def _pose_reference() -> DeclaredAircraftPoseReference:
    return DeclaredAircraftPoseReference(
        aircraft_id="fleet-alpha:1",
        pose_reference_above_contact_m=0.1,
    )


def _provenance(window, **overrides: object) -> VerifiedDwellProvenance:
    value: dict[str, object] = {
        "schema_version": DWELL_PROVENANCE_SCHEMA_VERSION,
        "run_id": _RUN_ID,
        "verifier_id": "logistics.verifier",
        "evidence_source": "independent_sealed_evidence",
        "provenance_verified": True,
        "window_digest": dwell_window_digest(window),
        "seal_digest": "9" * 64,
        "verification_note": DWELL_PROVENANCE_NOTE,
    }
    value.update(overrides)
    return VerifiedDwellProvenance(**value)


def _assess(
    package,
    window,
    transition,
    provenance,
) -> object:
    return assess_dwell_eligibility(
        transition=transition,
        observations=window,
        package=package,
        pose_reference=_pose_reference(),
        tolerances=_TOLERANCES,
        provenance=provenance,
    )


def _all_kernel_checks(assessment) -> dict[str, bool]:
    return {
        name: bool(getattr(assessment, name))
        for name, _ in (
            ("window_ok", ""),
            ("run_ok", ""),
            ("provider_ok", ""),
            ("scenario_ok", ""),
            ("aircraft_ok", ""),
            ("facility_ok", ""),
            ("pad_ok", ""),
            ("ingress_ok", ""),
            ("record_digest_ok", ""),
            ("canonical_facility_ok", ""),
            ("canonical_aircraft_ok", ""),
            ("canonical_pad_ok", ""),
            ("facility_kind_ok", ""),
            ("order_ok", ""),
            ("profile_ok", ""),
            ("assessment_ok", ""),
            ("collision_ok", ""),
            ("recompute_ok", ""),
            ("sequence_ok", ""),
            ("barrier_sequence_ok", ""),
            ("contiguity_ok", ""),
            ("dwell_ok", ""),
        )
    }


# ------------------------------------------------------------------ honest


def test_honest_pickup_window_is_eligible(package) -> None:
    window = _honest_pickup_window(package)
    transition = _transition()
    assessment = _assess(package, window, transition, _provenance(window))

    assert assessment.eligible is True
    assert assessment.failure_code is None
    assert assessment.window_size == 3
    assert assessment.dwell_seconds == pytest.approx(2.0)
    assert assessment.provenance_ok is True
    assert assessment.provenance_verified is True
    assert assessment.verifier_id == "logistics.verifier"
    assert assessment.window_start == _dwell_tick(1)
    assert assessment.window_end == _dwell_tick(3)
    assert all(ok for ok in _all_kernel_checks(assessment).values())
    assert "pickup dwell eligible" in assessment.message
    assert assessment.window_digest == dwell_window_digest(window)


def test_handoff_hub_window_is_eligible(package) -> None:
    window = _dwell_window(package, ticks=(1, 2, 3), facility_id="hub-3")
    transition = _transition(kind="handoff", facility_id="hub-3", order_id="order-1")
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is True
    assert assessment.facility_kind_ok is True
    assert assessment.order_ok is True


def test_charge_start_charger_window_is_eligible(package) -> None:
    window = _dwell_window(package, ticks=(1, 2, 3), facility_id="charger-x")
    transition = _transition(kind="charge_start", facility_id="charger-x", order_id=None)
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is True
    assert assessment.facility_kind_ok is True
    assert assessment.order_ok is True  # charging is facility-bound, never order-bound


def test_journal_slice_eligibility_is_eligible(package) -> None:
    window = _honest_pickup_window(package)
    records = tuple(
        ObservationJournalRecord(
            sequence=index,
            observation=observation,
            received_at=observation.at,
        )
        for index, observation in enumerate(window, start=1)
    )
    journal = ObservationJournal(
        schema_version=LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION,
        run_id=_RUN_ID,
        provider_id="flight",
        records=records,
    )
    collected = collect_dwell_window(
        journal=journal,
        aircraft_id="fleet-alpha:1",
        facility_id="facility-1",
        pad_index=0,
    )
    assert collected == window
    assessment = journal_slice_eligibility(
        journal=journal,
        transition=_transition(),
        package=package,
        pose_reference=_pose_reference(),
        tolerances=_TOLERANCES,
        provenance=_provenance(window),
    )
    assert assessment.eligible is True
    assert assessment.window_size == 3


# ------------------------------------------------------------- adversarial


def test_wrong_pad_is_rejected(package) -> None:
    window = _dwell_window(package, ticks=(1, 2, 3), pad_index=0)
    transition = _transition(pad_index=1)  # declared pad does not match window
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "pad_mismatch"
    assert assessment.pad_ok is False


def test_tick_gap_is_rejected(package) -> None:
    window = _dwell_window(package, ticks=(1, 3))  # tick 2 missing
    transition = _transition()
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "tick_gap"
    assert assessment.contiguity_ok is False


def test_collision_forged_record_is_rejected(package) -> None:
    window = list(_dwell_window(package, ticks=(1, 2, 3)))
    # A colliding airframe can never yield a physical observation record in the
    # adapter; a forged record carrying collision_contact=True must be rejected.
    forged = window[1].model_copy(update={"collision_contact": True})
    forged = forged.model_copy(
        update={"observation_digest": observation_digest_value(forged)}
    )
    window[1] = forged
    window = tuple(window)
    assessment = _assess(package, window, _transition(), _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "collision"
    assert assessment.collision_ok is False


def test_reordered_window_is_rejected(package) -> None:
    window = _dwell_window(package, ticks=(2, 1))  # non-increasing evidence set
    assessment = _assess(package, window, _transition(), _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "reordered_time"
    assert assessment.sequence_ok is False
    # A reordered window never carries a negative duration or invalid bounds.
    assert assessment.dwell_seconds is not None and assessment.dwell_seconds >= 0.0
    start_key = (assessment.window_start.tick, assessment.window_start.sim_time_ns)
    end_key = (assessment.window_end.tick, assessment.window_end.sim_time_ns)
    assert end_key >= start_key


def test_forged_profile_is_rejected(package) -> None:
    window = list(_dwell_window(package, ticks=(1, 2, 3)))
    forged_profile = window[1].profile.model_copy(update={"body_width_m": 0.61})
    forged = window[1].model_copy(update={"profile": forged_profile})
    forged = forged.model_copy(
        update={"observation_digest": observation_digest_value(forged)}
    )
    window[1] = forged
    window = tuple(window)
    assessment = _assess(package, window, _transition(), _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "profile_forged"
    assert assessment.profile_ok is False


def test_evidence_laundering_is_rejected(package) -> None:
    window = list(_dwell_window(package, ticks=(1, 2, 3)))
    # The ingress is structurally provenance_verified=False; any record claiming
    # otherwise is laundered evidence and is rejected before business success.
    laundered = window[1].model_copy(update={"provenance_verified": True})
    laundered = laundered.model_copy(
        update={"observation_digest": observation_digest_value(laundered)}
    )
    window[1] = laundered
    window = tuple(window)
    assessment = _assess(package, window, _transition(), _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "evidence_laundering"
    assert assessment.ingress_ok is False


def test_unverified_provenance_source_is_rejected(package) -> None:
    window = _honest_pickup_window(package)
    # Passing any ingress-derived object (for example one observation record
    # whose provenance_verified is structurally False) is explicit unverified
    # evidence, never a verified provenance.
    assessment = _assess(
        package, window, _transition(), provenance=window[0]
    )
    assert assessment.eligible is False
    assert assessment.failure_code == "unverified_provenance"
    assert assessment.provenance_verified is False
    assert VERIFIED_PROVENANCE_PREREQUISITE in assessment.message


def test_missing_provenance_is_rejected(package) -> None:
    window = _honest_pickup_window(package)
    assessment = _assess(package, window, _transition(), provenance=None)
    assert assessment.eligible is False
    assert assessment.failure_code == "missing_provenance"
    assert VERIFIED_PROVENANCE_PREREQUISITE in assessment.message


def test_wrong_verifier_is_rejected(package) -> None:
    window = _honest_pickup_window(package)
    provenance = _provenance(window, verifier_id="some.other.verifier")
    assessment = _assess(package, window, _transition(), provenance)
    assert assessment.eligible is False
    assert assessment.failure_code == "wrong_verifier"
    assert VERIFIED_PROVENANCE_PREREQUISITE in assessment.message


def test_forged_provenance_digest_is_rejected(package) -> None:
    window = _honest_pickup_window(package)
    # A provenance whose window_digest does not bind this exact ordered window.
    provenance = _provenance(window, window_digest="5" * 64)
    assessment = _assess(package, window, _transition(), provenance)
    assert assessment.eligible is False
    assert assessment.failure_code == "forged_provenance"
    assert VERIFIED_PROVENANCE_PREREQUISITE in assessment.message


def test_run_mismatch_is_rejected(package) -> None:
    window = _honest_pickup_window(package)
    transition = _transition(run_id=_OTHER_RUN_ID)
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "run_mismatch"
    assert assessment.run_ok is False


def test_facility_kind_mismatch_is_rejected(package) -> None:
    # A pickup can never occur at a standalone charger (no landing capability).
    window = _dwell_window(package, ticks=(1, 2, 3), facility_id="charger-x")
    transition = _transition(facility_id="charger-x", order_id="order-1")
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "facility_kind_mismatch"
    assert assessment.facility_kind_ok is False


def test_order_mismatch_is_rejected(package) -> None:
    window = _dwell_window(package, ticks=(1, 2, 3), facility_id="hub-3")
    # The handoff facility is the canonical hub, but the declared order is not
    # the package's hub-mediated handoff order for that hub.
    transition = _transition(kind="handoff", facility_id="hub-3", order_id="order-nope")
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "order_mismatch"
    assert assessment.order_ok is False


def test_insufficient_dwell_is_rejected(package) -> None:
    window = _honest_pickup_window(package)
    transition = _transition(minimum_dwell_s=5.0)  # window spans only 2 s
    assessment = _assess(package, window, transition, _provenance(window))
    assert assessment.eligible is False
    assert assessment.failure_code == "insufficient_dwell"
    assert assessment.dwell_ok is False


def test_empty_window_is_rejected(package) -> None:
    transition = _transition()
    assessment = _assess(package, (), transition, provenance=None)
    assert assessment.eligible is False
    assert assessment.failure_code == "empty_window"
    assert assessment.window_ok is False


def test_reordered_journal_extraction_raises(package) -> None:
    late = _observation(package, at=_dwell_tick(2))
    early = _observation(package, at=_dwell_tick(1))
    # A fabricated journal whose per-triple subsequence is not strictly
    # increasing must be a hard error, never silently re-sorted.
    journal = ObservationJournal(
        schema_version=LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION,
        run_id=_RUN_ID,
        provider_id="flight",
        records=(
            ObservationJournalRecord(sequence=1, observation=late, received_at=late.at),
            ObservationJournalRecord(sequence=2, observation=early, received_at=early.at),
        ),
    )
    with pytest.raises(DwellEligibilityError, match="not strictly time-ordered"):
        collect_dwell_window(
            journal=journal,
            aircraft_id="fleet-alpha:1",
            facility_id="facility-1",
            pad_index=0,
        )


def test_window_digest_binds_exact_ordered_digest_set(package) -> None:
    window = _dwell_window(package, ticks=(1, 2, 3))
    reordered = (window[0], window[2], window[1])
    assert dwell_window_digest(window) != dwell_window_digest(reordered)
    substituted = (window[1], window[0], window[2])
    assert dwell_window_digest(window) != dwell_window_digest(substituted)


# ------------------------------------------------------------- declaration


def test_charge_start_rejects_declared_order_id() -> None:
    with pytest.raises(ValueError, match="cannot carry an order_id"):
        _transition(kind="charge_start", order_id="order-1")


def test_order_stage_requires_declared_order_id() -> None:
    with pytest.raises(ValueError, match="requires an order_id"):
        _transition(kind="delivery", order_id=None)
