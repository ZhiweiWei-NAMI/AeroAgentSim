"""Coordinate calibration tests; these fixtures do not establish native dwell."""
import pytest
from pydantic import ValidationError

from aero_bench.tasks.logistics.physical_observations import DeclaredAircraftPoseReference
from tests.tasks.test_logistics_facility_presence import (
    _assess, _binding, _pad, _profile, _sample, _tolerances,
)
from tests.tasks.test_logistics_physical_observations import (
    context, _landed_context, _observe,
)


def test_root_below_contact_has_explicit_signed_calibration():
    # Existing x500 source: model-local +.24, feet bottom -.227.
    # Original BENCH pad top .15; sealed tick300 root up .136999871.
    calibration = -(0.24 - 0.227)
    profile = _profile(pose_reference_above_contact_m=calibration)
    declaration = DeclaredAircraftPoseReference(
        aircraft_id="uav.p02.carrier", pose_reference_above_contact_m=calibration,
    )
    assert declaration.pose_reference_above_contact_m == pytest.approx(-0.013)
    kwargs = dict(pad=_pad(y=0.15), sample=_sample(y=0.13699987109567124),
                  event=_binding(), tolerances=_tolerances(vertical_tolerance_m=0.001))
    assert _assess(profile=profile, **kwargs).eligible
    assert not _assess(profile=_profile(pose_reference_above_contact_m=0), **kwargs).eligible
    # A real displacement still fails the unchanged strict tolerance.
    kwargs["sample"] = _sample(y=0.14)
    assert not _assess(profile=profile, **kwargs).eligible


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, None])
def test_signed_calibration_is_required_finite_numeric(value):
    with pytest.raises(ValidationError):
        DeclaredAircraftPoseReference(aircraft_id="uav.p02.carrier",
                                     pose_reference_above_contact_m=value)
    with pytest.raises(ValidationError):
        _profile(pose_reference_above_contact_m=value)


def test_missing_calibration_has_no_half_height_default():
    with pytest.raises(ValidationError):
        DeclaredAircraftPoseReference(aircraft_id="uav.p02.carrier")


def test_closed_stage_adapter_keeps_signed_calibration_and_contact_gate(context):
    pad, scene, events = _landed_context(context, pose_reference_m=-0.013)
    tolerances = _tolerances(vertical_tolerance_m=0.001)
    reference = DeclaredAircraftPoseReference(
        aircraft_id="fleet-alpha:1", pose_reference_above_contact_m=-0.013,
    )
    observed = _observe(context, scene_state=scene, events=events,
                        pose_reference=reference, tolerances=tolerances)
    assert observed.sample.y == pytest.approx(pad.y - 0.013)
    assert observed.assessment.eligible
    wrong = DeclaredAircraftPoseReference(
        aircraft_id="fleet-alpha:1", pose_reference_above_contact_m=0,
    )
    rejected = _observe(context, scene_state=scene, events=events,
                        pose_reference=wrong, tolerances=tolerances)
    assert not rejected.assessment.eligible
    assert not rejected.assessment.contact_height_ok
