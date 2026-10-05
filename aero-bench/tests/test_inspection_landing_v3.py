"""Versioned landing verification over mechanical, non-formal trace fixtures."""

import pytest
from pydantic import ValidationError

from aero_bench.tasks.inspection.formal_v2_contracts import (
    InspectionFormalEvidenceBundleV2,
    InspectionFormalVerifierConfigV3,
)
from aero_bench.tasks.inspection.formal_v2_verifier import verify_formal_v2
from tests.test_inspection_formal_v2 import (
    _build_bundle_dict,
    _component,
)


def _delayed_landing():
    raw = _build_bundle_dict()
    samples = raw["physical_trace"]["samples"]
    for index in (9, 10):
        samples[index]["landed"] = False
        samples[index]["in_air"] = True
        samples[index]["linear_velocity_enu_mps"]["z_m"] = 0.02
    samples[11]["linear_velocity_enu_mps"]["z_m"] = 0.0184
    return raw


def _landing(raw):
    return _component(
        verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw)),
        "landing",
    )


def test_contact_descent_and_delayed_confirmation_are_separate_witnesses() -> None:
    landing = _landing(_delayed_landing())
    assert landing.passed
    assert landing.witness.sample_sequences == (8, 9, 11)


@pytest.mark.parametrize("velocity", [0.0, 0.1])
def test_contact_without_prior_downward_motion_fails(velocity: float) -> None:
    raw = _delayed_landing()
    raw["physical_trace"]["samples"][8]["linear_velocity_enu_mps"]["z_m"] = velocity
    assert not _landing(raw).passed


def test_downward_telemetry_without_native_descent_fails() -> None:
    raw = _delayed_landing()
    raw["physical_trace"]["samples"][8].update(
        altitude_amsl_m=0.0, altitude_agl_m=0.0
    )
    assert not _landing(raw).passed


@pytest.mark.parametrize("index", [9, 10, 11])
@pytest.mark.parametrize("velocity", [-1.01, 1.01])
def test_grounded_speed_limit_is_retained_through_confirmation(
    index: int, velocity: float
) -> None:
    raw = _delayed_landing()
    raw["physical_trace"]["samples"][index]["linear_velocity_enu_mps"]["z_m"] = velocity
    assert not _landing(raw).passed


def test_exiting_pad_between_contact_and_confirmation_fails() -> None:
    raw = _delayed_landing()
    raw["physical_trace"]["samples"][10]["position_enu"]["x_m"] = 4.0
    assert not _landing(raw).passed


def test_current_policy_rejects_old_schema() -> None:
    raw = _delayed_landing()
    raw["policy"]["schema_version"] = "aero-bench.inspection-formal-policy/v2"
    with pytest.raises(ValidationError, match="schema_version"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_current_formal_config_rejects_old_schema() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        InspectionFormalVerifierConfigV3.model_validate(
            {"schema_version": "aero-bench.inspection-verifier/v2"}
        )
