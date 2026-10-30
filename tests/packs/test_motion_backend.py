"""The flight-physics backend is an explicit, fail-closed frontend choice."""

from __future__ import annotations

from pathlib import Path

import pytest

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.packs.traffic_accident.presentation_controls import (
    CONTROL_READERS,
    read_motion_backend,
)
from aeroagentsim.packs.traffic_accident.scenario_hooks import _physics_hz
from aeroagentsim.presentation_controls import Known, Unavailable

PX4 = "px4_gazebo"


def test_kinematic_is_the_builtin_default_that_replaces_gazebo() -> None:
    prepared = TemplateRegistry().apply(
        "traffic-accident", {"motion_backend": "kinematic"}
    )
    assert prepared.knobs["motion_backend"] == "kinematic"
    assert prepared.document["engines"]["air_motion"]["plugin"] == "kinematic"
    result = read_motion_backend(prepared.document)
    assert isinstance(result, Known) and result.value == "kinematic"


def test_px4_backend_fails_closed_with_the_profile_contract() -> None:
    with pytest.raises(ValueError, match="PX4/Gazebo service configuration") as error:
        TemplateRegistry().apply("traffic-accident", {"motion_backend": PX4})
    assert "PX4 service URL" in str(error.value)


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="knob motion_backend"):
        TemplateRegistry().apply("traffic-accident", {"motion_backend": "sim"})


def test_physics_frequency_tolerates_a_native_air_owner() -> None:
    document = {
        "engines": {
            "road_motion": {"plugin": "traffic_road_motion", "config": {}},
            "native_air": {"plugin": PX4, "config": {"step_ns": 1}},
        },
        "run": {"advance_ns": 1},
    }
    _physics_hz(document, Path("."), 2)
    assert document["engines"]["native_air"]["config"]["step_ns"] == 500_000_000
    assert document["engines"]["road_motion"]["config"]["step_ns"] == 500_000_000
    assert document["run"]["advance_ns"] == 500_000_000


def test_reader_recognises_an_external_native_air_owner() -> None:
    document = {"engines": {"native_air": {"plugin": PX4, "config": {}}}}
    result = read_motion_backend(document)
    assert isinstance(result, Known) and result.value == PX4


def test_reader_reports_unavailable_without_a_supported_owner() -> None:
    assert isinstance(read_motion_backend({"engines": {}}), Unavailable)
    assert "traffic.motion_backend" in CONTROL_READERS


def test_reader_rejects_ambiguous_dual_motion_owners() -> None:
    from aeroagentsim.packs.lowalt.presentation_controls import (
        read_motion_backend as read_lowalt_motion_backend,
    )

    assert isinstance(
        read_motion_backend(
            {
                "engines": {
                    "air_motion": {"plugin": "kinematic", "config": {}},
                    "native_air": {"plugin": PX4, "config": {}},
                }
            }
        ),
        Unavailable,
    )
    assert isinstance(
        read_lowalt_motion_backend(
            {
                "engines": {
                    "motion": {"plugin": "kinematic", "config": {}},
                    "native_air": {"plugin": PX4, "config": {}},
                }
            }
        ),
        Unavailable,
    )

