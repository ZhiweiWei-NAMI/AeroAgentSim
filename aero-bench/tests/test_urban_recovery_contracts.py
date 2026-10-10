from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.urban_recovery_demo.contracts import (
    AppliedWrench,
    CameraFrameRef,
    DemoRole,
    EngineWindow,
)


def test_groundstation_cannot_claim_uav_sensor_authority() -> None:
    role = {
        "agent_id": "agent.station", "role": "groundstation",
        "endpoint_id": "endpoint.station", "vehicle_id": None,
        "camera_id": None, "telemetry_observation_id": None,
        "safety_observation_id": None, "camera_observation_id": None,
        "mailbox_observation_id": "network.mailbox.endpoint.station",
    }
    DemoRole.model_validate(role)
    with pytest.raises(ValidationError, match="cannot own"):
        DemoRole.model_validate({**role, "vehicle_id": "uav.1"})


def test_engine_window_preserves_warmup_offset_and_sequence_closure() -> None:
    window = {
        "run_id": "a" * 64, "scenario_digest": "b" * 64,
        "provider_id": "flight", "at": SimulationTime(tick=1, sim_time_ns=200_000_000),
        "engine_start_ns": 10_000_000_000, "engine_end_ns": 10_200_000_000,
        "logical_origin_engine_ns": 10_000_000_000,
        "first_sequence": 2, "last_sequence": 52, "record_count": 50,
        "journal_sha256": "c" * 64, "plugin_sha256": "d" * 64,
    }
    EngineWindow.model_validate(window)
    with pytest.raises(ValidationError, match="sequence"):
        EngineWindow.model_validate({**window, "record_count": 49})
    with pytest.raises(ValidationError, match="mapping"):
        EngineWindow.model_validate({**window, "logical_origin_engine_ns": 0})


def test_contact_cannot_be_a_modelled_force() -> None:
    wrench = {
        "vehicle_id": "uav.1", "engine_link": "uav.1::base_link",
        "engine_sim_time_ns": 200_000_000, "component": "contact", "origin": "solver_measured",
        "frame": "ENU", "force_n": {"x": 0.0, "y": 0.0, "z": 10.0},
        "torque_nm": {"x": 0.0, "y": 0.0, "z": 0.0},
    }
    AppliedWrench.model_validate(wrench)
    with pytest.raises(ValidationError, match="solver"):
        AppliedWrench.model_validate({**wrench, "origin": "applied"})


def test_camera_rejects_stale_time_and_missing_pixel_identity() -> None:
    frame = {
        "schema_version": "aero-bench.gazebo-rgb-frame/v1",
        "run_id": "a" * 64, "scenario_digest": "b" * 64,
        "vehicle_id": "uav.1", "camera_id": "camera.1", "frame_id": "frame.1",
        "at": SimulationTime(tick=1, sim_time_ns=200_000_000),
        "engine_sim_time_ns": 10_200_000_000, "logical_origin_engine_ns": 10_000_000_000,
        "pose_sha256": "c" * 64, "intrinsics_sha256": "d" * 64, "image_sha256": "e" * 64,
        "size_bytes": 50000, "width": 640, "height": 480, "media_type": "image/png",
        "artifact_id": "artifact.camera.1", "selector": "frames/frame.1", "source": "gazebo.camera",
    }
    CameraFrameRef.model_validate(frame)
    with pytest.raises(ValidationError, match="header time"):
        CameraFrameRef.model_validate({**frame, "engine_sim_time_ns": 10_000_000_000})
    with pytest.raises(ValidationError):
        CameraFrameRef.model_validate({**frame, "source": "external_renderer"})
