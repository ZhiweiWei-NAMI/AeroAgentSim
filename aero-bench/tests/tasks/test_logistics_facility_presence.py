"""Module-level tests for the bounded single-sample facility-presence kernel.

The kernel compares one *explicitly scene-frame-converted* measured aircraft
sample with one physical :class:`FacilityLandingPad`.  Every fixture here is
deliberately synthetic unit data: hand-written poses, velocities, yaws, pads,
bodies, bindings and tolerances with exact milestone values.  No fixture claims
to be real runtime telemetry, real PX4/Gazebo evidence, or a sealed artifact.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics.facility_geometry import (
    PAD_FRAME,
    FacilityLandingPad,
)
from aero_bench.tasks.logistics.facility_presence import (
    AircraftPresenceProfile,
    FacilityPresenceAssessment,
    FacilityPresenceError,
    MeasuredAircraftSample,
    PRESENCE_FRAME,
    PresenceEventBinding,
    PresenceTolerances,
    assess_facility_presence,
    require_grounded_presence,
)

RUN_ID = "a" * 64
PROVIDER_ID = "px4-gazebo"
AIRCRAFT_ID = "aircraft.1"
EVIDENCE_REF = "evidence.traj.7"
FACILITY_ID = "fac.vertiport.1"
PAD_INDEX = 0


def _pad(
    *,
    facility_id: str = FACILITY_ID,
    pad_index: int = PAD_INDEX,
    x: float = 100.0,
    y: float = 0.72,
    z: float = 50.0,
    width_m: float = 4.2,
    depth_m: float = 4.2,
    rotation_deg: float = 0.0,
) -> FacilityLandingPad:
    return FacilityLandingPad(
        facility_id=facility_id,
        pad_index=pad_index,
        x=x,
        y=y,
        z=z,
        width_m=width_m,
        depth_m=depth_m,
        rotation_deg=rotation_deg,
        frame=PAD_FRAME,
    )


def _profile(
    *,
    aircraft_id: str = AIRCRAFT_ID,
    body_width_m: float = 1.2,
    body_depth_m: float = 1.2,
    pose_reference_above_contact_m: float = 0.2,
) -> AircraftPresenceProfile:
    return AircraftPresenceProfile(
        aircraft_id=aircraft_id,
        body_width_m=body_width_m,
        body_depth_m=body_depth_m,
        pose_reference_above_contact_m=pose_reference_above_contact_m,
    )


def _binding(
    *,
    run_id: str = RUN_ID,
    provider_id: str = PROVIDER_ID,
    aircraft_id: str = AIRCRAFT_ID,
    tick: int = 7,
    sim_time_ns: int = 123_000_000,
    evidence_ref: str = EVIDENCE_REF,
) -> PresenceEventBinding:
    return PresenceEventBinding(
        run_id=run_id,
        provider_id=provider_id,
        aircraft_id=aircraft_id,
        at=SimulationTime(tick=tick, sim_time_ns=sim_time_ns),
        evidence_ref=evidence_ref,
    )


def _sample(
    *,
    sample_ref: str = "sample.7",
    run_id: str = RUN_ID,
    provider_id: str = PROVIDER_ID,
    aircraft_id: str = AIRCRAFT_ID,
    tick: int = 7,
    sim_time_ns: int = 123_000_000,
    x: float = 100.0,
    y: float = 0.92,
    z: float = 50.0,
    body_yaw_deg: float = 0.0,
    velocity_east_m_s: float = 0.0,
    velocity_up_m_s: float = 0.0,
    velocity_south_m_s: float = 0.0,
    landed: bool = True,
    in_air: bool = False,
    provenance: str = "caller_supplied",
    evidence_ref: str = EVIDENCE_REF,
    frame: str = PAD_FRAME,
) -> MeasuredAircraftSample:
    return MeasuredAircraftSample(
        sample_ref=sample_ref,
        run_id=run_id,
        provider_id=provider_id,
        aircraft_id=aircraft_id,
        at=SimulationTime(tick=tick, sim_time_ns=sim_time_ns),
        frame=frame,
        x=x,
        y=y,
        z=z,
        body_yaw_deg=body_yaw_deg,
        velocity_east_m_s=velocity_east_m_s,
        velocity_up_m_s=velocity_up_m_s,
        velocity_south_m_s=velocity_south_m_s,
        landed=landed,
        in_air=in_air,
        provenance=provenance,
        evidence_ref=evidence_ref,
    )


def _tolerances(
    *,
    vertical_tolerance_m: float = 0.05,
    horizontal_uncertainty_m: float = 0.05,
    max_stationary_speed_m_s: float = 0.5,
) -> PresenceTolerances:
    return PresenceTolerances(
        vertical_tolerance_m=vertical_tolerance_m,
        horizontal_uncertainty_m=horizontal_uncertainty_m,
        max_stationary_speed_m_s=max_stationary_speed_m_s,
    )


def _assess(**overrides: object):
    pad = overrides.pop("pad", _pad())
    sample = overrides.pop("sample", _sample())
    profile = overrides.pop("profile", _profile())
    event = overrides.pop("event", _binding())
    tolerances = overrides.pop("tolerances", _tolerances())
    assert not overrides
    return assess_facility_presence(
        pad=pad, sample=sample, profile=profile, event=event, tolerances=tolerances
    )


def test_grounded_centred_sample_is_eligible_on_axis_aligned_pad() -> None:
    assessment = _assess()
    assert isinstance(assessment, FacilityPresenceAssessment)
    assert assessment.eligible is True
    assert assessment.failure_code is None
    assert assessment.facility_id == FACILITY_ID
    assert assessment.pad_index == PAD_INDEX
    assert assessment.aircraft_id == AIRCRAFT_ID
    assert assessment.run_id == RUN_ID
    assert assessment.provider_id == PROVIDER_ID
    assert assessment.at == SimulationTime(tick=7, sim_time_ns=123_000_000)
    assert assessment.evidence_ref == EVIDENCE_REF
    assert assessment.sample_ref == "sample.7"
    assert assessment.frame == PRESENCE_FRAME
    assert assessment.frame_ok is True
    assert assessment.binding_ok is True
    assert assessment.landed_state_ok is True
    assert assessment.speed_ok is True
    assert assessment.horizontal_clearance_ok is True
    assert assessment.contact_height_ok is True
    # Measured geometry echoed exactly (no hidden epsilon); contact height is
    # compared within the declared vertical tolerance, so the 1-ulp rounding
    # of 0.92 - (0.72 + 0.2) is irrelevant to the verdict.
    assert assessment.local_x_m == 0.0
    assert assessment.local_z_m == 0.0
    assert assessment.contact_error_m == pytest.approx(0.0)
    assert assessment.body_yaw_deg == 0.0
    assert assessment.pad_local_body_half_x_m == 0.6
    assert assessment.pad_local_body_half_z_m == 0.6
    assert assessment.speed_magnitude_m_s == 0.0
    assert assessment.velocity_east_m_s == 0.0
    assert assessment.velocity_up_m_s == 0.0
    assert assessment.velocity_south_m_s == 0.0


def test_grounded_sample_offset_along_rotated_pad_local_axis_is_eligible() -> None:
    # Pad rotated 45 degrees and the aircraft is measured yaw-aligned with the
    # pad (body_yaw_deg == pad.rotation_deg).  A local-X offset of 1.0 m maps
    # to scene dx = cos45, dz = -sin45; the body must still fit inside the pad.
    pad = _pad(rotation_deg=45.0)
    sample = _sample(
        body_yaw_deg=45.0,
        x=100.0 + math.cos(math.radians(45.0)),
        z=50.0 - math.sin(math.radians(45.0)),
    )
    assessment = assess_facility_presence(
        pad=pad,
        sample=sample,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is True
    assert abs(assessment.local_x_m - 1.0) < 1e-12
    assert abs(assessment.local_z_m) < 1e-12
    assert assessment.pad_local_body_half_x_m == pytest.approx(0.6)
    assert assessment.pad_local_body_half_z_m == pytest.approx(0.6)


def test_rotated_pad_rejects_pose_inside_axis_aligned_box_but_outside_rectangle() -> None:
    # A 45-degree rotated 4.2 m square spans +/- 2.1*sqrt(2) ~ 2.97 m in scene
    # X and Z (its axis-aligned bounding box), but its corners reach the box
    # only diagonally.  (dx=2.5, dz=2.5) is inside that AABB yet outside the
    # diamond: local_z = (2.5 + 2.5)/sqrt(2) ~ 3.54 m > depth/2 + clearance.
    pad = _pad(rotation_deg=45.0)
    sample = _sample(body_yaw_deg=45.0, x=102.5, z=52.5)
    assessment = assess_facility_presence(
        pad=pad,
        sample=sample,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is False
    assert assessment.failure_code == "body_outside_pad"
    assert assessment.horizontal_clearance_ok is False
    assert assessment.local_z_m > pad.depth_m / 2.0


def test_rooftop_pad_requires_supported_contact_height() -> None:
    # A bound rooftop vertiport carries the declared support height in pad.y
    # (10.72 = 0.72 deck + 10.0 support).  A sample at ground body-centre
    # height must fail; a sample resting on the supported deck passes.
    pad = _pad(y=10.72)
    ground_level_sample = _sample(y=0.92)
    ground_level = assess_facility_presence(
        pad=pad,
        sample=ground_level_sample,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert ground_level.eligible is False
    assert ground_level.failure_code == "contact_height_mismatch"
    assert ground_level.contact_error_m == pytest.approx(-10.0)

    rooftop_sample = _sample(y=10.92)
    rooftop = assess_facility_presence(
        pad=pad,
        sample=rooftop_sample,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert rooftop.eligible is True


def test_body_too_wide_for_pad_is_rejected() -> None:
    profile = _profile(body_width_m=5.0)
    assessment = assess_facility_presence(
        pad=_pad(),
        sample=_sample(),
        profile=profile,
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is False
    assert assessment.failure_code == "body_outside_pad"
    assert assessment.pad_local_body_half_x_m == pytest.approx(2.5)


def test_body_depth_larger_than_pad_is_rejected() -> None:
    profile = _profile(body_depth_m=5.0)
    assessment = assess_facility_presence(
        pad=_pad(),
        sample=_sample(),
        profile=profile,
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is False
    assert assessment.failure_code == "body_outside_pad"
    assert assessment.pad_local_body_half_z_m == pytest.approx(2.5)


def test_horizontal_uncertainty_never_enlarges_the_pad_or_rescues_oversize() -> None:
    # Corollary of the DSH Blocker 1: the pad is the physical 4.2 m rectangle
    # and no declared uncertainty may enlarge it.  A 4.4 m body (0.2 m wider
    # than the pad) cannot physically land and stays rejected even with a
    # plausible 0.15 m position uncertainty; a 6 m body plus a 1.0 m margin is
    # still rejected; raising the uncertainty never turns a rejection into an
    # acceptance (the uncertainty is body-occupancy clearance, so it only ever
    # makes the check stricter).
    oversized_uncertain = _assess(
        profile=_profile(body_width_m=4.4, body_depth_m=4.4),
        tolerances=_tolerances(horizontal_uncertainty_m=0.15),
    )
    assert oversized_uncertain.eligible is False
    assert oversized_uncertain.failure_code == "body_outside_pad"

    oversized_exact = _assess(
        profile=_profile(body_width_m=4.4, body_depth_m=4.4),
        tolerances=_tolerances(horizontal_uncertainty_m=0.0),
    )
    assert oversized_exact.eligible is False

    worst_case = _assess(
        profile=_profile(body_width_m=6.0, body_depth_m=6.0),
        tolerances=_tolerances(horizontal_uncertainty_m=1.0),
    )
    assert worst_case.eligible is False
    assert worst_case.failure_code == "body_outside_pad"

    # Monotonicity, the strict direction: a borderline 4.0 m body on the 4.2 m
    # pad passes with zero uncertainty and is rejected once the declared
    # position uncertainty eats the clearance.  Increasing uncertainty can
    # never make a rejected body eligible again.
    marginal_exact = _assess(
        profile=_profile(body_width_m=4.0, body_depth_m=4.0),
        tolerances=_tolerances(horizontal_uncertainty_m=0.0),
    )
    assert marginal_exact.eligible is True
    marginal_uncertain = _assess(
        profile=_profile(body_width_m=4.0, body_depth_m=4.0),
        tolerances=_tolerances(horizontal_uncertainty_m=0.2),
    )
    assert marginal_uncertain.eligible is False
    assert marginal_uncertain.failure_code == "body_outside_pad"


def test_horizontal_uncertainty_shrinks_the_usable_boundary_as_clearance() -> None:
    # With zero uncertainty the 4.0 m body has exactly 0.1 m of usable centre
    # offset on the 4.2 m pad; with a 0.05 m declared uncertainty the usable
    # offset drops to exactly 0.05 m.  The boundary is a single strict
    # inequality in both cases.
    exact = _assess(
        profile=_profile(body_width_m=4.0),
        sample=_sample(x=100.0 + 0.1),
        tolerances=_tolerances(horizontal_uncertainty_m=0.0),
    )
    assert exact.eligible is True
    exact_over = _assess(
        profile=_profile(body_width_m=4.0),
        sample=_sample(x=100.0 + 0.11),
        tolerances=_tolerances(horizontal_uncertainty_m=0.0),
    )
    assert exact_over.eligible is False
    assert exact_over.failure_code == "body_outside_pad"

    cleared = _assess(
        profile=_profile(body_width_m=4.0),
        sample=_sample(x=100.0 + 0.04),
        tolerances=_tolerances(horizontal_uncertainty_m=0.05),
    )
    assert cleared.eligible is True
    cleared_over = _assess(
        profile=_profile(body_width_m=4.0),
        sample=_sample(x=100.0 + 0.06),
        tolerances=_tolerances(horizontal_uncertainty_m=0.05),
    )
    assert cleared_over.eligible is False
    assert cleared_over.failure_code == "body_outside_pad"


def test_contact_height_mismatch_is_rejected() -> None:
    sample = _sample(y=1.2)  # pad.y(0.72) + offset(0.2) = 0.92 expected
    assessment = assess_facility_presence(
        pad=_pad(),
        sample=sample,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is False
    assert assessment.failure_code == "contact_height_mismatch"
    assert assessment.contact_height_ok is False
    assert assessment.contact_error_m == pytest.approx(0.28)


def test_landed_state_is_explicitly_required() -> None:
    airborne = _sample(in_air=True)
    verdict = assess_facility_presence(
        pad=_pad(), sample=airborne, profile=_profile(), event=_binding(), tolerances=_tolerances()
    )
    assert verdict.eligible is False
    assert verdict.failure_code == "aircraft_not_landed"

    not_landed = _sample(landed=False)
    verdict = assess_facility_presence(
        pad=_pad(), sample=not_landed, profile=_profile(), event=_binding(), tolerances=_tolerances()
    )
    assert verdict.eligible is False
    assert verdict.failure_code == "aircraft_not_landed"

    # Contradictory state (landed with in_air) never establishes presence.
    contradictory = _sample(landed=True, in_air=True)
    verdict = assess_facility_presence(
        pad=_pad(), sample=contradictory, profile=_profile(), event=_binding(), tolerances=_tolerances()
    )
    assert verdict.eligible is False
    assert verdict.failure_code == "aircraft_not_landed"


def test_pure_vertical_speed_is_rejected_by_the_3d_magnitude_gate() -> None:
    # A body rising vertically at 3 m/s with zero horizontal velocity is fully
    # declarable now (velocity_up_m_s) and is rejected by the true 3D speed
    # magnitude check — the old horizontal-only ground-speed gate is gone.
    vertical = _sample(velocity_east_m_s=0.0, velocity_up_m_s=3.0, velocity_south_m_s=0.0)
    assessment = assess_facility_presence(
        pad=_pad(),
        sample=vertical,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(max_stationary_speed_m_s=0.5),
    )
    assert assessment.speed_magnitude_m_s == pytest.approx(3.0)
    assert assessment.eligible is False
    assert assessment.failure_code == "excessive_speed"
    assert assessment.speed_ok is False


def test_combined_3d_speed_uses_the_vector_magnitude_not_any_axis() -> None:
    # 1 m/s east + 2 m/s up => magnitude sqrt(5) ~ 2.236 m/s, rejected.  A slow
    # 3-axis vector within the threshold stays speed-ok: the gate is the full
    # 3D magnitude, never one horizontal channel.
    combined = _sample(
        velocity_east_m_s=1.0, velocity_up_m_s=2.0, velocity_south_m_s=0.0
    )
    assessment = assess_facility_presence(
        pad=_pad(),
        sample=combined,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(max_stationary_speed_m_s=0.5),
    )
    assert assessment.speed_magnitude_m_s == pytest.approx(math.sqrt(5.0))
    assert assessment.eligible is False
    assert assessment.failure_code == "excessive_speed"

    slow = _sample(
        velocity_east_m_s=0.1, velocity_up_m_s=0.2, velocity_south_m_s=0.1
    )
    slow_assessment = assess_facility_presence(
        pad=_pad(),
        sample=slow,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(max_stationary_speed_m_s=0.5),
    )
    assert slow_assessment.speed_magnitude_m_s == pytest.approx(
        math.sqrt(0.1**2 + 0.2**2 + 0.1**2)
    )
    assert slow_assessment.eligible is True
    assert slow_assessment.speed_ok is True


def test_velocity_axes_have_no_fake_defaults_and_no_ground_speed_field() -> None:
    # Every velocity axis is a required finite field: omitting any axis fails
    # validation (no silent zero for a "missing" axis), and the removed
    # horizontal-only ground_speed field is rejected by the strict model.
    raw = {
        "sample_ref": "sample.x",
        "run_id": RUN_ID,
        "provider_id": PROVIDER_ID,
        "aircraft_id": AIRCRAFT_ID,
        "at": {"tick": 7, "sim_time_ns": 123},
        "frame": PAD_FRAME,
        "x": 100.0,
        "y": 0.92,
        "z": 50.0,
        "body_yaw_deg": 0.0,
        "velocity_east_m_s": 0.0,
        "velocity_up_m_s": 0.0,
        "velocity_south_m_s": 0.0,
        "landed": True,
        "in_air": False,
        "provenance": "caller_supplied",
        "evidence_ref": EVIDENCE_REF,
    }
    for omitted in (
        "body_yaw_deg",
        "velocity_east_m_s",
        "velocity_up_m_s",
        "velocity_south_m_s",
    ):
        truncated = {key: value for key, value in raw.items() if key != omitted}
        with pytest.raises(ValidationError):
            MeasuredAircraftSample.model_validate(truncated)
    with pytest.raises(ValidationError):
        MeasuredAircraftSample.model_validate({**raw, "ground_speed_m_s": 0.0})


def test_pose_reference_offset_is_never_assumed_from_body_height() -> None:
    # The caller declares a 0.0 m pose-reference offset (reference is the pad
    # contact surface itself).  A sample whose y splits the difference assumes
    # body-half-height and must be rejected: the kernel uses only the declared
    # offset, never a guessed half-body-height.
    sample = _sample(y=0.92)  # 0.72 deck + 0.2 body-half-height assumption
    assessment = assess_facility_presence(
        pad=_pad(),
        sample=sample,
        profile=_profile(pose_reference_above_contact_m=0.0),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is False
    assert assessment.failure_code == "contact_height_mismatch"
    # sample.y(0.92) - (pad.y(0.72) + declared offset(0.0)) => about 0.2 m,
    # which is far beyond the declared 0.05 m vertical tolerance.
    assert assessment.contact_error_m == pytest.approx(0.2)


def test_nonfinite_values_fail_loudly_and_velocity_is_signed() -> None:
    for field, bad in (("x", math.nan), ("y", math.inf), ("z", math.nan)):
        with pytest.raises(ValidationError):
            _sample(**{field: bad})
    for field in ("velocity_east_m_s", "velocity_up_m_s", "velocity_south_m_s"):
        with pytest.raises(ValidationError):
            _sample(**{field: math.inf})
        with pytest.raises(ValidationError):
            _sample(**{field: math.nan})
    with pytest.raises(ValidationError):
        _sample(body_yaw_deg=math.inf)
    with pytest.raises(ValidationError):
        _sample(x=True)  # boolean is never a coordinate
    with pytest.raises(ValidationError):
        _tolerances(horizontal_uncertainty_m=-0.1)
    # Speed components are signed scene velocity: negative is a legal axis
    # direction (west / down / north), so the model accepts them while the 3D
    # magnitude gate still applies.
    westwards = _sample(velocity_east_m_s=-2.0)
    assert westwards.velocity_east_m_s == -2.0
    assert westwards.velocity_south_m_s == 0.0
    westwards_verdict = assess_facility_presence(
        pad=_pad(),
        sample=westwards,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert westwards_verdict.speed_magnitude_m_s == pytest.approx(2.0)
    assert westwards_verdict.eligible is False
    assert westwards_verdict.failure_code == "excessive_speed"


def test_missing_telemetry_fields_have_no_silent_defaults() -> None:
    raw = {
        "sample_ref": "sample.x",
        "run_id": RUN_ID,
        "provider_id": PROVIDER_ID,
        "aircraft_id": AIRCRAFT_ID,
        "at": {"tick": 7, "sim_time_ns": 123},
        "frame": PAD_FRAME,
        "x": 100.0,
        "y": 0.92,
        "z": 50.0,
        "body_yaw_deg": 0.0,
        "velocity_east_m_s": 0.0,
        "velocity_up_m_s": 0.0,
        "velocity_south_m_s": 0.0,
        "landed": True,
        "in_air": False,
        "provenance": "caller_supplied",
        "evidence_ref": EVIDENCE_REF,
    }
    for omitted in (
        "x",
        "body_yaw_deg",
        "velocity_east_m_s",
        "velocity_up_m_s",
        "velocity_south_m_s",
        "landed",
        "in_air",
        "provenance",
    ):
        truncated = {key: value for key, value in raw.items() if key != omitted}
        with pytest.raises(ValidationError):
            MeasuredAircraftSample.model_validate(truncated)


def test_identical_body_yaw_90_and_45_degrees_are_distinguished() -> None:
    # The same 2.0 x 4.0 body-local footprint on the same 4.2 x 4.2 axis-aligned
    # pad: yaw-aligned (delta 0) and yaw 90 (long axis along pad depth) both fit
    # inside the actual pad, but yaw 45 projects the conservative pad-local
    # half extents to 2.121 m on both axes — beyond the 2.1 m half pad — so it
    # is rejected.  The kernel never silently treats a yawed body as pad-aligned.
    aligned = _assess(
        profile=_profile(body_width_m=2.0, body_depth_m=4.0),
        sample=_sample(body_yaw_deg=0.0),
    )
    assert aligned.eligible is True
    assert aligned.pad_local_body_half_x_m == pytest.approx(1.0)
    assert aligned.pad_local_body_half_z_m == pytest.approx(2.0)

    quarter_turn = _assess(
        profile=_profile(body_width_m=2.0, body_depth_m=4.0),
        sample=_sample(body_yaw_deg=90.0),
    )
    assert quarter_turn.eligible is True
    assert quarter_turn.pad_local_body_half_x_m == pytest.approx(2.0)
    assert quarter_turn.pad_local_body_half_z_m == pytest.approx(1.0)

    diagonal = _assess(
        profile=_profile(body_width_m=2.0, body_depth_m=4.0),
        sample=_sample(body_yaw_deg=45.0),
    )
    assert diagonal.eligible is False
    assert diagonal.failure_code == "body_outside_pad"
    half = 0.5 * math.sqrt(2.0) * (2.0 + 1.0)  # cos45*1 + sin45*2 on both axes
    assert diagonal.pad_local_body_half_x_m == pytest.approx(half)
    assert diagonal.pad_local_body_half_z_m == pytest.approx(half)
    # Distinct assessed orientations must never share an assessment id even when
    # the verdict is the same.
    assert aligned.assessment_id != quarter_turn.assessment_id
    assert quarter_turn.assessment_id != diagonal.assessment_id


def test_rotated_pad_with_relative_body_yaw_is_assessed_explicitly() -> None:
    # Pad rotated 45 degrees.  A body measured yaw-aligned with the pad
    # (body_yaw_deg == 45, delta 0) fits; the identical body at scene yaw 0
    # (delta -45) projects 2.121 m half extents onto the pad axes and is
    # rejected.  Relative yaw is evaluated, never assumed from the pad.
    pad = _pad(rotation_deg=45.0)
    aligned = assess_facility_presence(
        pad=pad,
        sample=_sample(body_yaw_deg=45.0),
        profile=_profile(body_width_m=2.0, body_depth_m=4.0),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert aligned.eligible is True
    assert aligned.pad_local_body_half_x_m == pytest.approx(1.0)
    assert aligned.pad_local_body_half_z_m == pytest.approx(2.0)

    misaligned = assess_facility_presence(
        pad=pad,
        sample=_sample(body_yaw_deg=0.0),
        profile=_profile(body_width_m=2.0, body_depth_m=4.0),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert misaligned.eligible is False
    assert misaligned.failure_code == "body_outside_pad"
    half = 0.5 * math.sqrt(2.0) * (2.0 + 1.0)
    assert misaligned.pad_local_body_half_x_m == pytest.approx(half)
    assert misaligned.pad_local_body_half_z_m == pytest.approx(half)


def test_strict_bool_rejects_int_coercion_for_landed_state() -> None:
    with pytest.raises(ValidationError):
        _sample(landed=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _sample(in_air=0)  # type: ignore[arg-type]


def test_unknown_frame_is_rejected_at_the_type_boundary() -> None:
    with pytest.raises(ValidationError):
        _sample(frame="enu_m")
    pad = _pad()
    with pytest.raises(ValidationError):
        FacilityLandingPad(
            facility_id=FACILITY_ID,
            pad_index=PAD_INDEX,
            x=100.0,
            y=0.72,
            z=50.0,
            width_m=4.2,
            depth_m=4.2,
            rotation_deg=0.0,
            frame="some_other_frame",
        )


def test_invalid_provenance_declaration_is_rejected() -> None:
    # Only the two closed provenance declarations exist; a "verified" claim is
    # not a provenance kind this kernel can accept.
    with pytest.raises(ValidationError):
        _sample(provenance="verified_seal")


def test_stale_or_cross_run_sample_is_rejected_on_binding() -> None:
    stale_tick = _assess(sample=_sample(tick=6), event=_binding(tick=7))
    assert stale_tick.eligible is False
    assert stale_tick.failure_code == "identity_time_binding_mismatch"
    assert stale_tick.binding_ok is False

    stale_ns = _assess(
        sample=_sample(sim_time_ns=99), event=_binding(sim_time_ns=123_000_000)
    )
    assert stale_ns.eligible is False
    assert stale_ns.failure_code == "identity_time_binding_mismatch"

    cross_run = _assess(sample=_sample(run_id="b" * 64), event=_binding())
    assert cross_run.eligible is False
    assert cross_run.failure_code == "identity_time_binding_mismatch"


def test_wrong_provider_or_aircraft_sample_is_rejected() -> None:
    wrong_provider = _assess(
        sample=_sample(provider_id="sumo"),
        event=_binding(provider_id=PROVIDER_ID),
    )
    assert wrong_provider.eligible is False
    assert wrong_provider.failure_code == "identity_time_binding_mismatch"

    wrong_aircraft = _assess(
        sample=_sample(aircraft_id="aircraft.2"),
        event=_binding(aircraft_id=AIRCRAFT_ID),
    )
    assert wrong_aircraft.eligible is False
    assert wrong_aircraft.failure_code == "identity_time_binding_mismatch"

    # The declared body profile must also name the same aircraft.
    profile_mismatch = _assess(
        sample=_sample(aircraft_id=AIRCRAFT_ID),
        profile=_profile(aircraft_id="aircraft.9"),
        event=_binding(aircraft_id=AIRCRAFT_ID),
    )
    assert profile_mismatch.eligible is False
    assert profile_mismatch.failure_code == "identity_time_binding_mismatch"


def test_wrong_evidence_reference_is_rejected() -> None:
    mismatch = _assess(
        sample=_sample(evidence_ref="evidence.other"),
        event=_binding(evidence_ref=EVIDENCE_REF),
    )
    assert mismatch.eligible is False
    assert mismatch.failure_code == "identity_time_binding_mismatch"


def test_frame_is_echoed_on_every_assessment() -> None:
    for eligible in (True, False):
        assessment = (
            _assess()
            if eligible
            else _assess(sample=_sample(in_air=True))
        )
        assert assessment.frame == PRESENCE_FRAME
        assert assessment.frame_ok is True


def test_one_sample_only_and_no_dwell_claim() -> None:
    assessment = _assess()
    assert assessment.single_sample_evaluated == 1
    assert assessment.dwell_asserted is False
    # An eligible single sample proves presence at this instant only; it never
    # asserts that the airframe stayed there.
    assert assessment.dwell_asserted is False


def test_receipt_or_waypoint_input_is_not_an_acceptable_sample() -> None:
    # A "flight.land receipt" / "waypoint reached" shape has no measured pose,
    # measured 3D velocity, yaw, or explicit landed state, so it cannot even be
    # typed as a MeasuredAircraftSample.  Receipts never suffice here.
    receipt = {
        "sample_ref": "receipt.flight.land",
        "run_id": RUN_ID,
        "provider_id": PROVIDER_ID,
        "aircraft_id": AIRCRAFT_ID,
        "at": {"tick": 7, "sim_time_ns": 123},
        "frame": PAD_FRAME,
        "event": "flight.land",
        "waypoint": "fac.vertiport.1.pad0",
        "evidence_ref": EVIDENCE_REF,
    }
    with pytest.raises(ValidationError):
        MeasuredAircraftSample.model_validate(receipt)


def test_scene_frame_conversion_mapping_is_documented_east_up_minus_north() -> None:
    # The kernel's contract is that ENU (east, north, up) maps to the scene as
    # (x=east, y=up, z=-north) for both pose and velocity.  A synthetic ENU
    # point converted by that exact mapping lands on the pad centre and is
    # eligible; flipping the north sign (z=+north) drops the point 100 m below
    # the pad and must fail.  The ENU->scene yaw and velocity conversion is the
    # caller's explicit step documented on MeasuredAircraftSample.
    pad = _pad(x=10.0, z=50.0)
    enu_east_m, enu_north_m, enu_up_m = 10.0, -50.0, 0.92
    correct = _sample(x=enu_east_m, y=enu_up_m, z=-enu_north_m)
    assert (
        assess_facility_presence(
            pad=pad,
            sample=correct,
            profile=_profile(),
            event=_binding(),
            tolerances=_tolerances(),
        ).eligible
        is True
    )
    wrong_sign = _sample(x=enu_east_m, y=enu_up_m, z=enu_north_m)
    wrong_sign_verdict = assess_facility_presence(
        pad=pad,
        sample=wrong_sign,
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert wrong_sign_verdict.eligible is False
    assert wrong_sign_verdict.failure_code == "body_outside_pad"
    assert wrong_sign_verdict.local_z_m == pytest.approx(-100.0)


def test_provenance_is_echoed_but_never_verified() -> None:
    caller = _assess(sample=_sample(provenance="caller_supplied"))
    assert caller.eligible is True
    assert caller.provenance_declared == "caller_supplied"
    assert caller.provenance_verified is False

    sealed = _assess(sample=_sample(provenance="sealed_artifact_declared"))
    assert sealed.eligible is True
    assert sealed.provenance_declared == "sealed_artifact_declared"
    assert sealed.provenance_verified is False


def test_digest_hashes_complete_canonical_input_models_and_result() -> None:
    # Blocker 3 closure: the digest covers every pad coordinate/dimension/
    # rotation and the full expected event context, so verdict-preserving pad
    # changes still move the id, and two distinct failed expected contexts can
    # never collide.
    first = _assess()

    # Two physically different eligible pads, same measured sample, both
    # eligible -> verdict-preserving pad geometry change must change the id.
    huge_pad = _assess(pad=_pad(width_m=99.0, depth_m=99.0))
    assert huge_pad.eligible is True
    assert huge_pad.assessment_id != first.assessment_id

    # Same eligibility again with an opposite pad rotation (delta wraps to the
    # same projected extents for a square body), id still changes.
    flipped_pad = _assess(pad=_pad(rotation_deg=180.0))
    assert flipped_pad.eligible is True
    assert flipped_pad.assessment_id != first.assessment_id

    # Two distinct failed expected contexts (tick 7 vs tick 8) against one stale
    # tick-8 sample: both fail identity_time_binding_mismatch but must hash
    # their own expected context, so the ids differ and the record can say which
    # expectation failed.
    expect_7 = _assess(
        sample=_sample(tick=8), event=_binding(tick=7, sim_time_ns=2)
    )
    expect_8 = _assess(
        sample=_sample(tick=8), event=_binding(tick=8, sim_time_ns=2)
    )
    assert expect_7.failure_code == "identity_time_binding_mismatch"
    assert expect_8.failure_code == "identity_time_binding_mismatch"
    assert expect_7.assessment_id != expect_8.assessment_id

    # The measured yaw travels into the digest even when a square body keeps the
    # verdict identical.
    yawed = _assess(sample=_sample(body_yaw_deg=45.0))
    assert yawed.eligible is True
    assert yawed.assessment_id != first.assessment_id


def test_assessment_is_deterministic_and_sensitive_to_input_facts() -> None:
    first = _assess()
    second = _assess()
    assert first == second
    assert first.assessment_id == second.assessment_id
    assert len(first.assessment_id) == 64
    changed_tolerance = _assess(tolerances=_tolerances(max_stationary_speed_m_s=0.25))
    assert changed_tolerance.assessment_id != first.assessment_id
    changed_sample_ref = _assess(sample=_sample(sample_ref="sample.8"))
    assert changed_sample_ref.assessment_id != first.assessment_id
    changed_uncertainty = _assess(
        tolerances=_tolerances(horizontal_uncertainty_m=0.8)
    )
    assert changed_uncertainty.assessment_id != first.assessment_id
    changed_velocity = _assess(
        sample=_sample(velocity_east_m_s=0.1, velocity_up_m_s=0.0, velocity_south_m_s=0.0)
    )
    assert changed_velocity.assessment_id != first.assessment_id


def test_require_grounded_presence_returns_when_eligible_and_raises_otherwise() -> None:
    pad = _pad()
    sample = _sample()
    profile = _profile()
    event = _binding()
    tolerances = _tolerances()
    returned = require_grounded_presence(
        pad=pad, sample=sample, profile=profile, event=event, tolerances=tolerances
    )
    assert returned is not None
    assert returned.eligible is True

    with pytest.raises(FacilityPresenceError, match="not grounded on pad"):
        require_grounded_presence(
            pad=pad,
            sample=_sample(in_air=True),
            profile=profile,
            event=event,
            tolerances=tolerances,
        )


def test_zero_tolerances_accept_exact_boundaries_and_reject_one_ulp_overstep() -> None:
    # All declared tolerances are allowed to be exactly zero.  A 4.0 x 4.0 pad
    # with a 2.0 x 2.0 body has an exact fit envelope of +/- 1.0 m around the
    # pad centre.  A binary-exact sample whose body edge reaches the pad edge
    # (x=+1.0), whose contact height equals pad.y + declared offset, and whose
    # 3D speed is exactly the stationary threshold passes; moving that same body
    # one thousandth of a metre farther or exceeding the speed threshold fails
    # its single check.
    pad = _pad(width_m=4.0, depth_m=4.0, y=0.5)
    exact = _assess(
        pad=pad,
        sample=_sample(x=101.0, y=0.75),
        profile=_profile(
            body_width_m=2.0, body_depth_m=2.0, pose_reference_above_contact_m=0.25
        ),
        tolerances=_tolerances(
            vertical_tolerance_m=0.0,
            horizontal_uncertainty_m=0.0,
            max_stationary_speed_m_s=0.0,
        ),
    )
    assert exact.eligible is True
    assert exact.contact_error_m == 0.0
    assert exact.speed_ok is True
    assert exact.horizontal_clearance_ok is True
    assert exact.contact_height_ok is True

    overhang = _assess(
        pad=pad,
        sample=_sample(x=101.001, y=0.75),
        profile=_profile(
            body_width_m=2.0, body_depth_m=2.0, pose_reference_above_contact_m=0.25
        ),
        tolerances=_tolerances(
            vertical_tolerance_m=0.0,
            horizontal_uncertainty_m=0.0,
            max_stationary_speed_m_s=0.0,
        ),
    )
    assert overhang.eligible is False
    assert overhang.failure_code == "body_outside_pad"
    assert overhang.horizontal_clearance_ok is False

    moving = _assess(
        pad=pad,
        sample=_sample(x=101.0, y=0.75, velocity_east_m_s=0.001),
        profile=_profile(
            body_width_m=2.0, body_depth_m=2.0, pose_reference_above_contact_m=0.25
        ),
        tolerances=_tolerances(
            vertical_tolerance_m=0.0,
            horizontal_uncertainty_m=0.0,
            max_stationary_speed_m_s=0.0,
        ),
    )
    assert moving.eligible is False
    assert moving.failure_code == "excessive_speed"
    assert moving.speed_ok is False


def test_kernel_consumes_canonical_facility_landing_pads() -> None:
    # The kernel is fed the accepted geometry module's concrete physical pads
    # in scene_east_south_m (no synthetic geometry here).  The centre pad of a
    # three-slot axis-aligned vertiport row sits on the facility origin, and a
    # grounded sample yaw-aligned with that pad is eligible.
    from aero_bench.tasks.logistics.facilities import (
        FacilityCapabilities,
        FacilityPosition,
        LandingCapability,
    )
    from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads

    facility = FacilityCapabilities(
        facility_id=FACILITY_ID,
        name="test-vertiport",
        kind="vertiport",
        placement="ground",
        position=FacilityPosition(x=100.0, z=50.0),
        rotation_deg=0.0,
        width_m=18.0,
        depth_m=12.0,
        height_m=6.0,
        landing=LandingCapability(parking_slots=3, movements_per_hour=12.0),
    )
    pads = facility_landing_pads(facility)
    assert len(pads) == 3
    centre = pads[1]
    assert centre.pad_index == 1
    assert centre.frame == PRESENCE_FRAME
    assessment = assess_facility_presence(
        pad=centre,
        sample=_sample(
            x=centre.x,
            y=centre.y + 0.2,
            z=centre.z,
            body_yaw_deg=centre.rotation_deg,
        ),
        profile=_profile(),
        event=_binding(),
        tolerances=_tolerances(),
    )
    assert assessment.eligible is True
    assert assessment.facility_id == FACILITY_ID
    assert assessment.local_x_m == pytest.approx(0.0)
    assert assessment.local_z_m == pytest.approx(0.0)
    assert assessment.contact_error_m == pytest.approx(0.0)


def test_facility_identifier_contract_is_carried_through() -> None:
    # Facility identifiers that are valid under the facility authoring contract
    # (uppercase and ':' allowed by FacilityIdentifier) must round-trip through
    # the assessment, not be narrowed to the stricter config Identifier.
    pad = _pad(facility_id="FAC:vertiport.1")
    assessment = _assess(pad=pad)
    assert assessment.eligible is True
    assert assessment.facility_id == "FAC:vertiport.1"
    assert assessment.pad_index == PAD_INDEX
