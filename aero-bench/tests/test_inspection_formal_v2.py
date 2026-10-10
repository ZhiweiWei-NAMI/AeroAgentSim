from __future__ import annotations

import base64
import copy
import hashlib
import struct
from types import SimpleNamespace
import zlib

import pytest
from pydantic import ValidationError

from aero_bench.config.models import NamedValue
from aero_bench.serialization import canonical_json_bytes
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.inspection.contracts import WorkOrderStatus
from aero_bench.tasks.inspection.formal_v2_contracts import (
    CanonicalInspectionReportV2,
    CapturedFrameEvidenceV2,
    DeliveredReportOutcomeV2,
    DurablyBufferedReportOutcomeV2,
    FORMAL_V2_COMPONENT_IDS,
    InspectionFormalEvidenceBundleV2,
    InspectionFormalVerifierConfigV3,
    InspectionMissionContextV2,
    PhysicalTraceV2,
)
from aero_bench.tasks.inspection.formal_v2_sealed import _report_and_network
from aero_bench.tasks.inspection.formal_v2_verifier import verify_formal_v2


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _rgb_png(*, width: int = 2, height: int = 2) -> bytes:
    pixels = b"".join(b"\x00" + bytes((16, 32, 48)) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        )
        + _png_chunk(b"IDAT", zlib.compress(pixels, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _frame(
    *,
    frame_id: str,
    frame_sequence: int,
    capture_time_s: float,
    target_id: str,
    trace_sample_sequence: int,
    distance_m: float = 10.0,
    view_angle_deg: float = 5.0,
) -> dict[str, object]:
    image = _rgb_png()
    return CapturedFrameEvidenceV2.issue(
        frame_id=frame_id,
        frame_sequence=frame_sequence,
        capture_time_s=capture_time_s,
        observation_id=f"observation.{frame_id}",
        target_id=target_id,
        sensor_id="sensor.main",
        vehicle_id="vehicle.main",
        trace_sample_sequence=trace_sample_sequence,
        distance_m=distance_m,
        view_angle_deg=view_angle_deg,
        selector=f"frames/{frame_id}",
        engine_sim_time_ns=round(capture_time_s * 1_000_000_000),
        logical_origin_engine_ns=0,
        payload_digest=hashlib.sha256(f"payload:{frame_id}".encode()).hexdigest(),
        image_sha256=hashlib.sha256(image).hexdigest(),
        size_bytes=len(image),
        width=2,
        height=2,
        media_type="image/png",
        source="gazebo.camera",
        capture_pose_source="gazebo.pose.private_digest",
        camera_pose_sha256=hashlib.sha256(
            f"camera:{frame_id}".encode()
        ).hexdigest(),
        target_pose_sha256=hashlib.sha256(
            f"target:{frame_id}".encode()
        ).hexdigest(),
        image_base64=base64.b64encode(image).decode("ascii"),
    ).model_dump(mode="python")


def _sample(
    sequence: int,
    time_s: float,
    *,
    x: float,
    y: float,
    agl: float,
    vx: float,
    vy: float,
    vz: float,
    armed: bool,
    in_air: bool,
    landed: bool,
    ground_contact: bool,
    wx: float = 0.0,
    wy: float = 0.0,
    wz: float = 0.0,
) -> dict[str, object]:
    return {
        "sequence": sequence,
        "sim_time_s": time_s,
        "position_enu": {"x_m": x, "y_m": y, "z_m": agl},
        "altitude_amsl_m": agl,
        "terrain_altitude_amsl_m": 0.0,
        "altitude_agl_m": agl,
        "orientation_world": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
        "linear_velocity_enu_mps": {"x_m": vx, "y_m": vy, "z_m": vz},
        "angular_velocity_body_rps": {"x_m": wx, "y_m": wy, "z_m": wz},
        "armed": armed,
        "in_air": in_air,
        "landed": landed,
        "ground_contact": ground_contact,
    }


def _issue_context(raw_context: dict[str, object]) -> dict[str, object]:
    body = {
        key: value
        for key, value in raw_context.items()
        if key not in {"schema_version", "context_digest"}
    }
    return InspectionMissionContextV2.issue(**body).model_dump(mode="python")


def _issue_report(raw_report: dict[str, object]) -> dict[str, object]:
    body = {
        key: value
        for key, value in raw_report.items()
        if key not in {"schema_version", "report_digest", "report_size_bytes"}
    }
    return CanonicalInspectionReportV2.issue(**body).model_dump(mode="python")


def _set_captured_frames(
    raw: dict[str, object], frames: list[dict[str, object]]
) -> None:
    raw["captured_frames"] = tuple(frames)


def _set_trace_samples(
    raw: dict[str, object], samples: list[dict[str, object]]
) -> None:
    raw["physical_trace"]["samples"] = tuple(samples)
    raw["physical_trace"]["trace_end_sequence"] = samples[-1]["sequence"]
    raw["physical_trace"]["trace_end_time_s"] = samples[-1]["sim_time_s"]


def _refresh_report_and_outcome(raw: dict[str, object]) -> None:
    raw["report"] = _issue_report(raw["report"])
    raw["network_outcome"]["report_digest"] = raw["report"][
        "transport_artifact_digest"
    ]
    raw["network_outcome"]["report_size_bytes"] = raw["report"][
        "transport_artifact_size_bytes"
    ]


def _insert_trace_sample(
    raw: dict[str, object], insert_index: int, sample: dict[str, object]
) -> None:
    samples = [copy.deepcopy(item) for item in raw["physical_trace"]["samples"]]
    samples.insert(insert_index, copy.deepcopy(sample))
    for sequence, trace_sample in enumerate(samples):
        trace_sample["sequence"] = sequence
    _set_trace_samples(raw, samples)
    frames = [copy.deepcopy(frame) for frame in raw["captured_frames"]]
    for frame in frames:
        if frame["trace_sample_sequence"] >= insert_index:
            frame["trace_sample_sequence"] += 1
    _set_captured_frames(raw, frames)
    events = [copy.deepcopy(event) for event in raw["physical_trace"]["contact_events"]]
    for event in events:
        if event["sequence"] >= insert_index:
            event["sequence"] += 1
    raw["physical_trace"]["contact_events"] = tuple(events)


def _build_bundle_dict() -> dict[str, object]:
    scenario_digest = hashlib.sha256(b"inspection-formal-v2").hexdigest()
    raw = {
        "policy": {
            "schema_version": "aero-bench.inspection-formal-policy/v3",
            "scenario_digest": scenario_digest,
            "selected_launch_id": "launch.main",
            "vehicle_id": "vehicle.main",
            "sensor_id": "sensor.main",
            "target_ids": ("target.alpha", "target.bravo"),
            "geofence_ids": ("zone.geofence",),
            "no_fly_ids": ("zone.no-fly",),
            "minimum_takeoff_agl_m": 10.5,
            "minimum_horizontal_ground_track_m": 50.5,
            "startup_clearance_margin_m": 5.0,
            "minimum_distinct_frames_per_target": 2,
            "dwell_s": 1.0,
            "max_trace_gap_s": 1.2,
            "return_radius_m": 3.0,
            "landing_max_agl_m": 0.5,
            "landing_max_vertical_speed_mps": 1.0,
            "stopped_linear_speed_mps": 0.1,
            "stopped_angular_speed_rps": 0.1,
            "stopped_dwell_s": 1.0,
            "terminal_disarm_required": True,
            "network_deadline_s": 12.0,
            "accepted_outcome_kinds": ("delivered", "durably_buffered"),
            "minimum_buffer_retention_s": 20.0,
            "minimum_detection_f1": 0.9,
        },
        "mission_context": {},
        "physical_trace": {},
        "captured_frames": (),
        "report": {},
        "defect_truth": {
            "schema_version": "aero-bench.inspection-defect-truth/v2",
            "scenario_digest": scenario_digest,
            "truths": (
                {"defect_id": "defect.alpha", "target_id": "target.alpha"},
                {"defect_id": "defect.bravo", "target_id": "target.bravo"},
            ),
        },
        "network_outcome": {},
    }
    raw["mission_context"] = _issue_context(
        {
            "world_frame": "enu",
            "scenario_digest": scenario_digest,
            "selected_launch_id": "launch.main",
            "vehicle_id": "vehicle.main",
            "sensor_id": "sensor.main",
            "launch_pad": {
                "launch_id": "launch.main",
                "pose_world": {
                    "position_enu": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                    "orientation_world": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
                },
                "geometry": {"radius_m": 1.5, "height_m": 0.21},
            },
            "camera": {
                "sensor_id": "sensor.main",
                "vehicle_id": "vehicle.main",
                "mount_vehicle_to_sensor": {
                    "position_enu": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                    "orientation_world": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
                },
                "horizontal_fov_deg": 60.0,
                "vertical_fov_deg": 40.0,
                "resolution_width_px": 2,
                "resolution_height_px": 2,
                "optical_convention": "camera_forward_plus_x__image_right_plus_y__image_up_plus_z",
            },
            "targets": (
                {
                    "target_id": "target.alpha",
                    "pose_world": {
                        "position_enu": {"x_m": 80.0, "y_m": -10.0, "z_m": 15.0},
                        "orientation_world": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
                    },
                    "box_geometry": {"size_x_m": 2.0, "size_y_m": 2.0, "size_z_m": 2.0},
                    "surface_normal_target": {"x": -1.0, "y": 0.0, "z": 0.0},
                    "arrival_radius_m": 8.0,
                    "min_observation_range_m": 1.0,
                    "max_observation_range_m": 30.0,
                    "max_surface_view_angle_deg": 20.0,
                    "fov_margin_deg": 2.0,
                },
                {
                    "target_id": "target.bravo",
                    "pose_world": {
                        "position_enu": {"x_m": 80.0, "y_m": 10.0, "z_m": 15.0},
                        "orientation_world": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
                    },
                    "box_geometry": {"size_x_m": 2.0, "size_y_m": 2.0, "size_z_m": 2.0},
                    "surface_normal_target": {"x": -1.0, "y": 0.0, "z": 0.0},
                    "arrival_radius_m": 8.0,
                    "min_observation_range_m": 1.0,
                    "max_observation_range_m": 30.0,
                    "max_surface_view_angle_deg": 20.0,
                    "fov_margin_deg": 2.0,
                },
            ),
            "geofences": (
                {
                    "prism_id": "zone.geofence",
                    "vertices_enu": (
                        {"east_m": -20.0, "north_m": -30.0},
                        {"east_m": 120.0, "north_m": -30.0},
                        {"east_m": 120.0, "north_m": 30.0},
                        {"east_m": -20.0, "north_m": 30.0},
                    ),
                    "vertical_reference": "amsl",
                    "min_altitude_m": -5.0,
                    "max_altitude_m": 50.0,
                },
            ),
            "no_fly_prisms": (
                {
                    "prism_id": "zone.no-fly",
                    "vertices_enu": (
                        {"east_m": 30.0, "north_m": -2.0},
                        {"east_m": 40.0, "north_m": -2.0},
                        {"east_m": 40.0, "north_m": 2.0},
                        {"east_m": 30.0, "north_m": 2.0},
                    ),
                    "vertical_reference": "amsl",
                    "min_altitude_m": -5.0,
                    "max_altitude_m": 50.0,
                },
            ),
            "uncertainty": {
                "horizontal_position_uncertainty_m": 0.2,
                "vertical_position_uncertainty_m": 0.1,
                "terrain_uncertainty_m": 0.1,
            },
        }
    )
    samples = [
        _sample(
            0,
            0.0,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=False,
            in_air=False,
            landed=True,
            ground_contact=True,
        ),
        _sample(
            1,
            1.2,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=False,
            landed=True,
            ground_contact=True,
        ),
        _sample(
            2,
            2.4,
            x=0.0,
            y=0.0,
            agl=12.0,
            vx=0.0,
            vy=0.0,
            vz=6.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            3,
            3.6,
            x=55.0,
            y=-25.0,
            agl=12.0,
            vx=20.0,
            vy=-10.0,
            vz=0.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            4,
            4.8,
            x=60.0,
            y=-10.0,
            agl=15.0,
            vx=4.0,
            vy=0.0,
            vz=1.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            5,
            6.0,
            x=60.0,
            y=-10.0,
            agl=15.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            6,
            7.2,
            x=60.0,
            y=10.0,
            agl=15.0,
            vx=0.0,
            vy=15.0,
            vz=0.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            7,
            8.4,
            x=60.0,
            y=10.0,
            agl=15.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            8,
            9.6,
            x=2.0,
            y=0.0,
            agl=2.0,
            vx=-15.0,
            vy=-8.0,
            vz=-0.6,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
        _sample(
            9,
            10.8,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=-1.0,
            vy=0.0,
            vz=-0.3,
            armed=True,
            in_air=False,
            landed=True,
            ground_contact=True,
        ),
        _sample(
            10,
            12.0,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.05,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=False,
            landed=True,
            ground_contact=True,
            wz=0.05,
        ),
        _sample(
            11,
            13.2,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.04,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=False,
            landed=True,
            ground_contact=True,
            wz=0.04,
        ),
        _sample(
            12,
            14.4,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=False,
            in_air=False,
            landed=True,
            ground_contact=True,
        ),
    ]
    raw["physical_trace"] = {
        "schema_version": "aero-bench.inspection-physical-trace/v2",
        "vehicle_id": "vehicle.main",
        "trace_start_sequence": 0,
        "trace_end_sequence": 12,
        "trace_start_time_s": 0.0,
        "trace_end_time_s": 14.4,
        "maximum_gap_s": 1.2,
        "contacts_complete": True,
        "samples": tuple(samples),
        "contact_events": (
            {
                "sequence": 0,
                "sim_time_s": 0.0,
                "counterparty_kind": "launch_pad",
                "counterparty_id": "launch.main",
                "phase": "began",
            },
            {
                "sequence": 0,
                "sim_time_s": 0.0,
                "counterparty_kind": "ground",
                "counterparty_id": "ground.zone",
                "phase": "began",
            },
            {
                "sequence": 2,
                "sim_time_s": 2.4,
                "counterparty_kind": "launch_pad",
                "counterparty_id": "launch.main",
                "phase": "ended",
            },
            {
                "sequence": 2,
                "sim_time_s": 2.4,
                "counterparty_kind": "ground",
                "counterparty_id": "ground.zone",
                "phase": "ended",
            },
            {
                "sequence": 9,
                "sim_time_s": 10.8,
                "counterparty_kind": "launch_pad",
                "counterparty_id": "launch.main",
                "phase": "began",
            },
            {
                "sequence": 9,
                "sim_time_s": 10.8,
                "counterparty_kind": "ground",
                "counterparty_id": "ground.zone",
                "phase": "began",
            },
        ),
    }
    frames = [
        _frame(
            frame_id="frame.alpha.1",
            frame_sequence=0,
            capture_time_s=4.8,
            target_id="target.alpha",
            trace_sample_sequence=4,
            distance_m=10.1,
        ),
        _frame(
            frame_id="frame.alpha.2",
            frame_sequence=1,
            capture_time_s=6.0,
            target_id="target.alpha",
            trace_sample_sequence=5,
            distance_m=10.2,
        ),
        _frame(
            frame_id="frame.bravo.1",
            frame_sequence=2,
            capture_time_s=7.2,
            target_id="target.bravo",
            trace_sample_sequence=6,
            distance_m=10.3,
        ),
        _frame(
            frame_id="frame.bravo.2",
            frame_sequence=3,
            capture_time_s=8.4,
            target_id="target.bravo",
            trace_sample_sequence=7,
            distance_m=10.4,
        ),
    ]
    raw["captured_frames"] = tuple(frames)
    raw["report"] = _issue_report(
        {
            "report_id": "report.main",
            "reported_at_s": 8.6,
            "qualifying_frame_ids": (
                "frame.alpha.1",
                "frame.alpha.2",
                "frame.bravo.1",
                "frame.bravo.2",
            ),
            "transport_artifact_digest": hashlib.sha256(
                b"formal-report-transport"
            ).hexdigest(),
            "transport_artifact_size_bytes": 512,
            "detections": (
                {
                    "detection_id": "detect.alpha",
                    "defect_id": "defect.alpha",
                    "target_id": "target.alpha",
                    "frame_id": "frame.alpha.1",
                },
                {
                    "detection_id": "detect.bravo",
                    "defect_id": "defect.bravo",
                    "target_id": "target.bravo",
                    "frame_id": "frame.bravo.1",
                },
            ),
        }
    )
    raw["network_outcome"] = DeliveredReportOutcomeV2.model_validate(
        {
            "outcome_kind": "delivered",
            "ownership_kind": "provider",
            "report_digest": raw["report"]["transport_artifact_digest"],
            "report_size_bytes": raw["report"]["transport_artifact_size_bytes"],
            "delivered_at_s": 9.0,
        }
    ).model_dump(mode="python")
    return raw


def _build_bundle() -> InspectionFormalEvidenceBundleV2:
    return InspectionFormalEvidenceBundleV2.model_validate(_build_bundle_dict())


def _component(result, component_id: str):
    return next(
        component
        for component in result.components
        if component.component_id == component_id
    )


def _buffered_outcome(
    report: dict[str, object], **overrides: object
) -> dict[str, object]:
    payload = {
        "outcome_kind": "durably_buffered",
        "ownership_kind": "provider",
        "provider_buffer_id": "buffer.main",
        "report_digest": report["transport_artifact_digest"],
        "report_size_bytes": report["transport_artifact_size_bytes"],
        "buffered_at_s": 9.0,
        "storage_uri": "provider://buffer/main",
        "terminal_presence": True,
        "expires_at_s": 40.0,
        "retention_guarantee_s": 25.0,
    }
    payload.update(overrides)
    return DurablyBufferedReportOutcomeV2.model_validate(payload).model_dump(
        mode="python"
    )


def test_verify_formal_v2_passes_complete_physical_mission() -> None:
    result = verify_formal_v2(_build_bundle())
    assert result.passed is True
    assert result.semantic_f1 == pytest.approx(1.0)
    assert all(component.passed for component in result.components)


def test_formal_v2_config_json_round_trip_preserves_declared_tuples() -> None:
    config = InspectionFormalVerifierConfigV3.model_validate(
        {
            "schema_version": "aero-bench.inspection-verifier/v3",
            "package_id": "inspection.v1",
            "vehicle_id": "vehicle.main",
            "sensor_id": "sensor.main",
            "target_ids": ("target.alpha", "target.bravo"),
            "geofence_ids": ("region.geofence",),
            "no_fly_ids": ("region.no-fly",),
            "required_tool_ids": ("flight.arm",),
            "goal_bindings": tuple(
                {
                    "component_id": component_id,
                    "goal_id": f"goal.formal.{component_id}",
                }
                for component_id in FORMAL_V2_COMPONENT_IDS
            ),
            "minimum_takeoff_agl_m": 12.5,
            "minimum_horizontal_ground_track_m": 50.1,
            "startup_clearance_margin_m": 0.5,
            "minimum_distinct_frames_per_target": 2,
            "dwell_s": 2.0,
            "max_trace_gap_s": 0.75,
            "return_radius_m": 3.0,
            "launch_pad_height_m": 0.15,
            "landing_max_agl_m": 0.5,
            "landing_max_vertical_speed_mps": 0.35,
            "stopped_linear_speed_mps": 0.25,
            "stopped_angular_speed_rps": 0.15,
            "stopped_dwell_s": 2.0,
            "terminal_disarm_required": True,
            "network_delivery_required": True,
            "network_deadline_s": 540.0,
            "accepted_outcome_kinds": ("delivered",),
            "minimum_buffer_retention_s": 60.0,
            "minimum_detection_f1": 1.0,
            "uncertainty": {
                "horizontal_position_uncertainty_m": 0.05,
                "vertical_position_uncertainty_m": 0.08,
                "terrain_uncertainty_m": 0.05,
            },
        }
    )

    assert (
        InspectionFormalVerifierConfigV3.model_validate(
            config.model_dump(mode="json")
        )
        == config
    )


def test_mission_context_issue_accepts_typed_nested_contracts() -> None:
    context = InspectionMissionContextV2.model_validate(
        _build_bundle_dict()["mission_context"]
    )
    issued = InspectionMissionContextV2.issue(
        world_frame=context.world_frame,
        scenario_digest=context.scenario_digest,
        selected_launch_id=context.selected_launch_id,
        vehicle_id=context.vehicle_id,
        sensor_id=context.sensor_id,
        launch_pad=context.launch_pad,
        camera=context.camera,
        targets=context.targets,
        geofences=context.geofences,
        no_fly_prisms=context.no_fly_prisms,
        uncertainty=context.uncertainty,
    )

    assert issued == context


def test_noncanonical_quaternion_sign_rejected_by_contract_validation() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][2]["orientation_world"] = {
        "w": -1.0,
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
    }
    with pytest.raises(ValidationError, match="canonical sign representation"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_startup_inside_target_arrival_fails_initial_component() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][0]["position_enu"]["x_m"] = 74.0
    raw["physical_trace"]["samples"][0]["position_enu"]["y_m"] = -10.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "initial_outside_arrival")
    assert component.passed is False
    assert "already inside" in component.reason


def test_startup_uncertainty_boundary_fails_initial_component() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][0]["position_enu"]["x_m"] = 66.9
    raw["physical_trace"]["samples"][0]["position_enu"]["y_m"] = -10.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "initial_outside_arrival")
    assert component.passed is False
    assert "already inside" in component.reason


@pytest.mark.parametrize(
    ("factory", "match"),
    (
        (
            lambda raw: PhysicalTraceV2.model_validate(
                {**raw["physical_trace"], "business_state": "completed"}
            ),
            "Extra inputs are not permitted",
        ),
        (
            lambda raw: CanonicalInspectionReportV2.model_validate(
                {**raw["report"], "acknowledged": True}
            ),
            "Extra inputs are not permitted",
        ),
        (
            lambda raw: InspectionFormalEvidenceBundleV2.model_validate(
                {**raw, "run": {"completed": True}}
            ),
            "Extra inputs are not permitted",
        ),
    ),
)
def test_strict_models_reject_ack_and_business_extras(factory, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        factory(_build_bundle_dict())


def test_unknown_truth_target_rejected_by_bundle_schema() -> None:
    raw = _build_bundle_dict()
    raw["defect_truth"]["truths"] = (
        {"defect_id": "defect.alpha", "target_id": "target.alpha"},
        {"defect_id": "defect.bravo", "target_id": "target.ghost"},
    )
    with pytest.raises(
        ValidationError,
        match="defect truth target_id must belong to the policy and mission context",
    ):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_duplicate_report_defect_ids_rejected_by_bundle_schema() -> None:
    raw = _build_bundle_dict()
    raw["report"]["detections"] = raw["report"]["detections"] + (
        {
            "detection_id": "detect.alpha.duplicate",
            "defect_id": "defect.alpha",
            "target_id": "target.alpha",
            "frame_id": "frame.alpha.2",
        },
    )
    _refresh_report_and_outcome(raw)
    with pytest.raises(ValidationError, match="report defect_ids must be unique"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_exact_takeoff_agl_threshold_fails() -> None:
    raw = _build_bundle_dict()
    for sample in raw["physical_trace"]["samples"][2:8]:
        sample["altitude_amsl_m"] = 10.7
        sample["altitude_agl_m"] = 10.7
        sample["position_enu"]["z_m"] = 10.7
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "takeoff_agl")
    assert component.passed is False
    assert "takeoff AGL threshold" in component.reason


def test_prearmed_startup_fails_armed_after_start() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][0]["armed"] = True
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "armed_after_start")
    assert component.passed is False
    assert "initial sample must remain disarmed" in component.reason


@pytest.mark.parametrize(
    ("mutator", "schema"),
    (
        (
            lambda raw: [
                sample.__setitem__("armed", False)
                for sample in raw["physical_trace"]["samples"][2:8]
            ],
            False,
        ),
        (
            lambda raw: [
                sample.__setitem__("ground_contact", True)
                for sample in raw["physical_trace"]["samples"][2:8]
            ],
            True,
        ),
    ),
)
def test_disarmed_or_contacting_altitude_does_not_count_as_takeoff(
    mutator, schema: bool
) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    if schema:
        with pytest.raises(
            ValidationError,
            match="sample ground_contact must exactly match active ground or launch-pad contacts",
        ):
            InspectionFormalEvidenceBundleV2.model_validate(raw)
        return
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "takeoff_agl")
    assert component.passed is False
    assert "without ground contact" in component.reason


def test_exact_ground_track_threshold_fails() -> None:
    raw = _build_bundle_dict()
    raw["mission_context"]["uncertainty"]["horizontal_position_uncertainty_m"] = 0.0
    raw["mission_context"]["targets"][0]["max_observation_range_m"] = 35.0
    raw["mission_context"] = _issue_context(raw["mission_context"])
    raw["physical_trace"]["samples"][3]["position_enu"]["x_m"] = 45.0
    raw["physical_trace"]["samples"][3]["position_enu"]["y_m"] = 0.0
    raw["physical_trace"]["samples"][4]["position_enu"]["x_m"] = 50.0
    raw["physical_trace"]["samples"][4]["position_enu"]["y_m"] = 0.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "pre_observation_ground_track")
    assert component.passed is False
    assert "does not strictly exceed" in component.reason


def test_ground_track_after_observation_does_not_count() -> None:
    raw = _build_bundle_dict()
    raw["mission_context"]["targets"][0]["pose_world"]["position_enu"]["x_m"] = 50.0
    raw["mission_context"] = _issue_context(raw["mission_context"])
    raw["physical_trace"]["samples"][3]["position_enu"]["x_m"] = 25.0
    raw["physical_trace"]["samples"][4]["position_enu"]["x_m"] = 30.0
    raw["physical_trace"]["samples"][5]["position_enu"]["x_m"] = 30.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "pre_observation_ground_track")
    assert component.passed is False
    assert "pre-observation" in component.reason


def test_pre_takeoff_motion_does_not_count_toward_ground_track() -> None:
    raw = _build_bundle_dict()
    sample = raw["physical_trace"]["samples"][2]
    sample["altitude_amsl_m"] = 0.0
    sample["altitude_agl_m"] = 0.0
    sample["position_enu"]["z_m"] = 0.0
    sample["linear_velocity_enu_mps"]["z_m"] = 0.0
    sample["in_air"] = False
    sample["landed"] = True
    sample["ground_contact"] = True
    sample = raw["physical_trace"]["samples"][3]
    sample["altitude_amsl_m"] = 0.0
    sample["altitude_agl_m"] = 0.0
    sample["position_enu"]["z_m"] = 0.0
    sample["linear_velocity_enu_mps"]["z_m"] = 0.0
    sample["in_air"] = False
    sample["landed"] = True
    sample["ground_contact"] = True
    raw["physical_trace"]["contact_events"] = (
        raw["physical_trace"]["contact_events"][0],
        raw["physical_trace"]["contact_events"][1],
        raw["physical_trace"]["contact_events"][2],
        {
            "sequence": 4,
            "sim_time_s": 4.8,
            "counterparty_kind": "ground",
            "counterparty_id": "ground.zone",
            "phase": "ended",
        },
        raw["physical_trace"]["contact_events"][4],
        raw["physical_trace"]["contact_events"][5],
    )
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "pre_observation_ground_track")
    assert component.passed is False
    assert "pre-observation horizontal ground track" in component.reason


def test_post_takeoff_taxi_does_not_count_toward_ground_track() -> None:
    raw = _build_bundle_dict()
    sample = raw["physical_trace"]["samples"][3]
    sample["altitude_amsl_m"] = 0.0
    sample["altitude_agl_m"] = 0.0
    sample["position_enu"]["z_m"] = 0.0
    sample["linear_velocity_enu_mps"]["z_m"] = 0.0
    sample["in_air"] = False
    sample["landed"] = True
    sample["ground_contact"] = True
    events = list(raw["physical_trace"]["contact_events"])
    events.insert(
        4,
        {
            "sequence": 3,
            "sim_time_s": 3.6,
            "counterparty_kind": "ground",
            "counterparty_id": "ground.zone",
            "phase": "began",
        },
    )
    events.insert(
        5,
        {
            "sequence": 4,
            "sim_time_s": 4.8,
            "counterparty_kind": "ground",
            "counterparty_id": "ground.zone",
            "phase": "ended",
        },
    )
    raw["physical_trace"]["contact_events"] = tuple(events)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "pre_observation_ground_track")
    assert component.passed is False
    assert "continuous armed in-air non-contacting chain" in component.reason


def test_one_frame_per_target_fails_target_observations() -> None:
    raw = _build_bundle_dict()
    frames = list(raw["captured_frames"])
    del frames[1]
    _set_captured_frames(raw, frames)
    raw["report"]["qualifying_frame_ids"] = (
        "frame.alpha.1",
        "frame.bravo.1",
        "frame.bravo.2",
    )
    raw["report"] = _issue_report(raw["report"])
    raw["network_outcome"]["report_digest"] = raw["report"]["report_digest"]
    raw["network_outcome"]["report_size_bytes"] = raw["report"]["report_size_bytes"]
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


def test_duplicate_frame_rejected_by_bundle_schema() -> None:
    raw = _build_bundle_dict()
    _set_captured_frames(
        raw,
        list(raw["captured_frames"])
        + [{**copy.deepcopy(raw["captured_frames"][0]), "frame_sequence": 9}],
    )
    with pytest.raises(ValidationError, match="globally unique"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_non_positive_frame_distance_rejected_by_bundle_schema() -> None:
    raw = _build_bundle_dict()
    frames = list(raw["captured_frames"])
    frames[0] = {**copy.deepcopy(frames[0]), "distance_m": 0.0}
    _set_captured_frames(raw, frames)
    with pytest.raises(ValidationError, match="distance_m"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_captured_frame_rejects_corrupt_or_substituted_png_bytes() -> None:
    raw = _build_bundle_dict()
    frames = list(raw["captured_frames"])
    substituted = bytearray(base64.b64decode(frames[0]["image_base64"]))
    substituted[-1] ^= 1
    frames[0] = {
        **copy.deepcopy(frames[0]),
        "image_base64": base64.b64encode(bytes(substituted)).decode("ascii"),
    }
    _set_captured_frames(raw, frames)

    with pytest.raises(ValidationError, match="digest differs"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_captured_frame_rejects_declared_dimensions_that_differ_from_png() -> None:
    raw = _build_bundle_dict()
    frames = list(raw["captured_frames"])
    frames[0] = {**copy.deepcopy(frames[0]), "width": 3}
    _set_captured_frames(raw, frames)

    with pytest.raises(ValidationError, match="dimensions differ"):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_formal_projection_rejects_detection_bound_to_another_frame() -> None:
    raw = _build_bundle_dict()
    frame = CapturedFrameEvidenceV2.model_validate(raw["captured_frames"][0])
    package, evidence, _, _ = _complete_report_projection(frame)
    evidence.detections = (
        SimpleNamespace(
            frame_id="frame.unrelated",
            observation_id=frame.observation_id,
            image_sha256=frame.image_sha256,
        ),
    )

    with pytest.raises(ValueError, match="exact captured RGB frame"):
        _report_and_network(
            package=package,
            evidence=evidence,
            frames=(frame,),
            config=None,
        )


def _complete_report_projection(frame: CapturedFrameEvidenceV2):
    at = SimulationTime(tick=2, sim_time_ns=200)
    arrival = SimulationTime(tick=2, sim_time_ns=150)
    package = SimpleNamespace(
        work_orders=(
            SimpleNamespace(
                work_order_id="work.1",
                target_id=frame.target_id,
                required_observation_id=frame.observation_id,
                upload_deadline_ns=180,
            ),
        )
    )
    state = SimpleNamespace(
        work_order_id="work.1",
        target_id=frame.target_id,
        required_observation_id=frame.observation_id,
        status=WorkOrderStatus.COMPLETED,
        claimed_by="agent.1",
        observation_id=frame.observation_id,
        report_payload_digest="a" * 64,
        last_time=at,
    )
    report = SimpleNamespace(
        source_artifact_id="artifact.report",
        run_id="b" * 64,
        work_order_id="work.1",
        observation_id=frame.observation_id,
    )
    delivery = SimpleNamespace(
        run_id="b" * 64,
        work_order_id="work.1",
        payload_digest="a" * 64,
        source_artifact_id="artifact.delivery",
        message_id="message.report",
        sent_at=SimulationTime(tick=1, sim_time_ns=100),
        delivered_at=arrival,
    )
    payload = {
        "schema_id": "inspection.network-delivery.v1",
        "run_id": "b" * 64,
        "provider_id": "provider.network",
        "command_id": "command.send",
        "agent_id": "agent.1",
        "source_artifact_id": "artifact.delivery",
        "work_order_id": "work.1",
        "message_id": "message.report",
        "payload_digest": "a" * 64,
        "sent_tick": 1,
        "sent_time_ns": 100,
        "delivered_tick": 2,
        "delivered_time_ns": 150,
    }
    completion_id = "internal.complete." + hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()[:32]
    event = SimpleNamespace(
        event_type="network.delivery",
        source_kind="provider",
        source="provider.network",
        provider_id="provider.network",
        workload_id="provider.network",
        command_id="command.send",
        correlation_id="command.send",
        payload_schema_id=payload["schema_id"],
        time=at,
        payload=tuple(NamedValue(name=k, value=v) for k, v in payload.items()),
    )
    history = SimpleNamespace(
        command=SimpleNamespace(command=SimpleNamespace(
            work_order_id="work.1", kind="complete",
            command_id=completion_id, issued_at=at,
        )),
        transition=SimpleNamespace(time=at),
    )
    evidence = SimpleNamespace(
        run_id="b" * 64,
        seal=SimpleNamespace(
            artifacts=(
                SimpleNamespace(
                    artifact_id="artifact.report",
                    sha256="a" * 64,
                    size_bytes=10,
                ),
            )
        ),
        bindings=(
            SimpleNamespace(
                evidence_kind="report",
                artifact_id="artifact.report",
                producer_id="agent.1",
            ),
            SimpleNamespace(
                evidence_kind="delivery", artifact_id="artifact.delivery",
                producer_id="provider.network",
            ),
        ),
        event_ledger=SimpleNamespace(records=(SimpleNamespace(event=event),)),
        business_state=SimpleNamespace(
            state=SimpleNamespace(work_orders=(state,)), history=(history,),
        ),
        report=(report,),
        detections=(),
        deliveries=(delivery,),
    )
    return package, evidence, state, delivery


def test_formal_report_uses_real_completed_delivery_time() -> None:
    raw = _build_bundle_dict()
    frame = CapturedFrameEvidenceV2.model_validate(raw["captured_frames"][0])
    package, evidence, _, _ = _complete_report_projection(frame)

    _, outcome = _report_and_network(
        package=package,
        evidence=evidence,
        frames=(frame,),
        config=None,
    )

    assert outcome.delivered_at_s == 150e-9


@pytest.mark.parametrize("mutation", [
    "undelivered", "missing-report", "submitted", "missing-event",
    "repeated-event", "arrival-after-commit", "wrong-tick", "late-arrival",
    "wrong-provider", "wrong-payload", "wrong-completion", "wrong-barrier",
])
def test_formal_report_requires_actual_delivery_and_completed_business_state(
    mutation: str,
) -> None:
    raw = _build_bundle_dict()
    frame = CapturedFrameEvidenceV2.model_validate(raw["captured_frames"][0])
    package, evidence, state, delivery = _complete_report_projection(frame)
    if mutation == "undelivered":
        delivery.delivered_at = None
    elif mutation == "missing-report":
        evidence.report = ()
    elif mutation == "submitted":
        state.status = WorkOrderStatus.SUBMITTED
    elif mutation == "missing-event":
        evidence.event_ledger.records = ()
    elif mutation == "repeated-event":
        evidence.event_ledger.records *= 2
    elif mutation == "arrival-after-commit":
        evidence.event_ledger.records[0].event.time = SimulationTime(
            tick=2, sim_time_ns=140,
        )
    elif mutation == "wrong-tick":
        evidence.event_ledger.records[0].event.time = SimulationTime(
            tick=3, sim_time_ns=200,
        )
    elif mutation == "late-arrival":
        package.work_orders[0].upload_deadline_ns = 149
    elif mutation == "wrong-provider":
        evidence.event_ledger.records[0].event.source = "provider.unrelated"
    elif mutation == "wrong-payload":
        delivery.payload_digest = "c" * 64
    elif mutation == "wrong-completion":
        evidence.business_state.history[0].command.command.command_id = "unrelated"
    elif mutation == "wrong-barrier":
        state.last_time = delivery.delivered_at

    with pytest.raises(ValueError):
        _report_and_network(
            package=package,
            evidence=evidence,
            frames=(frame,),
            config=None,
        )


def test_insufficient_dwell_fails_target_observations() -> None:
    raw = _build_bundle_dict()
    raw["policy"]["dwell_s"] = 1.5
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


def test_middle_sample_geometry_break_rejects_target_dwell() -> None:
    raw = _build_bundle_dict()
    _insert_trace_sample(
        raw,
        5,
        _sample(
            5,
            5.4,
            x=60.0,
            y=-35.0,
            agl=15.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        ),
    )
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


def test_middle_airborne_state_loss_rejects_target_dwell() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][5]["armed"] = False
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


def test_landed_camera_view_rejects_target_dwell() -> None:
    raw = _build_bundle_dict()
    sample = raw["physical_trace"]["samples"][5]
    sample["in_air"] = False
    sample["landed"] = True
    sample["ground_contact"] = True
    events = list(raw["physical_trace"]["contact_events"])
    events.insert(
        4,
        {
            "sequence": 5,
            "sim_time_s": 6.0,
            "counterparty_kind": "ground",
            "counterparty_id": "ground.zone",
            "phase": "began",
        },
    )
    events.insert(
        5,
        {
            "sequence": 6,
            "sim_time_s": 7.2,
            "counterparty_kind": "ground",
            "counterparty_id": "ground.zone",
            "phase": "ended",
        },
    )
    raw["physical_trace"]["contact_events"] = tuple(events)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


def test_camera_coincident_with_target_center_rejects_observation() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][4]["position_enu"].update(
        {"x_m": 80.0, "y_m": -10.0, "z_m": 15.0}
    )
    raw["physical_trace"]["samples"][4]["altitude_amsl_m"] = 15.0
    raw["physical_trace"]["samples"][4]["terrain_altitude_amsl_m"] = 0.0
    raw["physical_trace"]["samples"][4]["altitude_agl_m"] = 15.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


def test_out_of_fov_metadata_cannot_force_success() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][4]["position_enu"]["y_m"] = -35.0
    raw["physical_trace"]["samples"][5]["position_enu"]["y_m"] = -35.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "target_observations")
    assert component.passed is False
    assert "qualifying dwell interval" in component.reason


@pytest.mark.parametrize(
    ("mutator", "match"),
    (
        (
            lambda raw: raw["physical_trace"]["samples"][4].__setitem__(
                "sim_time_s", 3.5
            ),
            "strictly increasing",
        ),
        (
            lambda raw: raw["physical_trace"].__setitem__("trace_end_sequence", 13),
            "last sample sequence",
        ),
        (
            lambda raw: raw["physical_trace"].__setitem__("maximum_gap_s", 1.1),
            "observed maximum trace gap",
        ),
    ),
)
def test_trace_gap_reordering_and_omission_are_rejected(mutator, match: str) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    with pytest.raises(ValidationError, match=match):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_large_time_consistent_trace_gap_fails_trace_coverage() -> None:
    raw = _build_bundle_dict()
    raw["policy"]["max_trace_gap_s"] = 1.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "trace_coverage")
    assert component.passed is False
    assert "maximum_gap_s exceeds policy.max_trace_gap_s" in component.reason


def test_geofence_swept_crossing_fails_containment() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][8]["position_enu"]["x_m"] = 130.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "geofence_containment")
    assert component.passed is False
    assert "exits the selected geofence" in component.reason


def test_concave_geofence_segment_fails_containment() -> None:
    raw = _build_bundle_dict()
    raw["mission_context"]["geofences"] = (
        {
            "prism_id": "zone.geofence",
            "vertices_enu": (
                {"east_m": -40.0, "north_m": -60.0},
                {"east_m": 120.0, "north_m": -60.0},
                {"east_m": 120.0, "north_m": 40.0},
                {"east_m": 20.0, "north_m": 40.0},
                {"east_m": 20.0, "north_m": -35.0},
                {"east_m": 10.0, "north_m": -35.0},
                {"east_m": 10.0, "north_m": 40.0},
                {"east_m": -40.0, "north_m": 40.0},
            ),
            "vertical_reference": "amsl",
            "min_altitude_m": -5.0,
            "max_altitude_m": 50.0,
        },
    )
    raw["mission_context"] = _issue_context(raw["mission_context"])
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "geofence_containment")
    assert component.passed is False
    assert (
        "swept segment exits" in component.reason
        or "exits the selected geofence" in component.reason
    )


def test_no_fly_crossing_fails_avoidance() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][3]["position_enu"]["x_m"] = 35.0
    raw["physical_trace"]["samples"][3]["position_enu"]["y_m"] = 0.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "no_fly_avoidance")
    assert component.passed is False
    assert "intersects a selected no-fly prism" in component.reason


def test_uncertainty_boundary_fails_no_fly_avoidance() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][3]["position_enu"]["x_m"] = 29.85
    raw["physical_trace"]["samples"][3]["position_enu"]["y_m"] = 0.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "no_fly_avoidance")
    assert component.passed is False
    assert "intersects a selected no-fly prism" in component.reason


def test_forbidden_collision_fails_contact_safety() -> None:
    raw = _build_bundle_dict()
    events = list(raw["physical_trace"]["contact_events"])
    events.insert(
        4,
        {
            "sequence": 6,
            "sim_time_s": 7.2,
            "counterparty_kind": "obstacle",
            "counterparty_id": "tower.one",
            "phase": "began",
        },
    )
    raw["physical_trace"]["contact_events"] = tuple(events)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "contact_safety")
    assert component.passed is False
    assert "forbidden contact" in component.reason


@pytest.mark.parametrize(
    ("mutator", "schema", "match"),
    (
        (
            lambda raw: raw["network_outcome"].__setitem__("report_digest", "1" * 64),
            False,
            "network outcome digest",
        ),
        (
            lambda raw: raw["network_outcome"].__setitem__("delivered_at_s", 12.5),
            False,
            "network deadline",
        ),
        (
            lambda raw: raw["network_outcome"].__setitem__("delivered_at_s", None),
            True,
            "valid number",
        ),
    ),
)
def test_delivery_digest_deadline_and_nullable_rejections(
    mutator, schema: bool, match: str
) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    if schema:
        with pytest.raises(ValidationError, match=match):
            InspectionFormalEvidenceBundleV2.model_validate(raw)
        return
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "report_outcome")
    assert component.passed is False
    assert match in component.reason


@pytest.mark.parametrize(
    ("outcome_factory", "timestamp_key", "match"),
    (
        (
            lambda raw: copy.deepcopy(raw["network_outcome"]),
            "delivered_at_s",
            "report delivery cannot precede the canonical report timestamp",
        ),
        (
            lambda raw: _buffered_outcome(raw["report"]),
            "buffered_at_s",
            "durable buffering cannot precede the canonical report timestamp",
        ),
    ),
)
def test_report_outcome_cannot_precede_canonical_report(
    outcome_factory, timestamp_key: str, match: str
) -> None:
    raw = _build_bundle_dict()
    raw["network_outcome"] = outcome_factory(raw)
    raw["network_outcome"][timestamp_key] = raw["report"]["reported_at_s"] - 0.1
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "report_outcome")
    assert component.passed is False
    assert match in component.reason


@pytest.mark.parametrize(
    ("outcome_factory", "timestamp_key"),
    (
        (lambda raw: copy.deepcopy(raw["network_outcome"]), "delivered_at_s"),
        (lambda raw: _buffered_outcome(raw["report"]), "buffered_at_s"),
    ),
)
def test_report_outcome_accepts_same_tick_as_canonical_report(
    outcome_factory, timestamp_key: str
) -> None:
    raw = _build_bundle_dict()
    raw["network_outcome"] = outcome_factory(raw)
    raw["network_outcome"][timestamp_key] = raw["report"]["reported_at_s"]
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "report_outcome")
    assert component.passed is True
    assert result.passed is True


@pytest.mark.parametrize(
    ("outcome_factory", "schema", "match"),
    (
        (
            lambda report: _buffered_outcome(report, retention_guarantee_s=10.0),
            False,
            "retention is shorter",
        ),
        (
            lambda report: {
                "outcome_kind": "durably_buffered",
                "ownership_kind": "provider",
                "provider_buffer_id": "buffer.main",
                "report_digest": report["transport_artifact_digest"],
                "report_size_bytes": report["transport_artifact_size_bytes"],
                "buffered_at_s": 9.0,
                "storage_uri": "provider://buffer/main",
                "terminal_presence": True,
                "expires_at_s": 20.0,
                "retention_guarantee_s": 25.0,
            },
            True,
            "guaranteed retention",
        ),
        (
            lambda report: {
                **_buffered_outcome(report),
                "ownership_kind": "participant",
            },
            True,
            "Input should be 'provider'",
        ),
    ),
)
def test_invalid_or_expired_buffer_outcomes(
    outcome_factory, schema: bool, match: str
) -> None:
    raw = _build_bundle_dict()
    raw["network_outcome"] = outcome_factory(raw["report"])
    if schema:
        with pytest.raises(ValidationError, match=match):
            InspectionFormalEvidenceBundleV2.model_validate(raw)
        return
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "report_outcome")
    assert component.passed is False
    assert match in component.reason


def test_report_timestamp_after_deadline_fails_directly() -> None:
    raw = _build_bundle_dict()
    deadline = (
        raw["physical_trace"]["samples"][0]["sim_time_s"]
        + raw["policy"]["network_deadline_s"]
    )
    raw["report"]["reported_at_s"] = deadline + 0.1
    _refresh_report_and_outcome(raw)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "report_outcome")
    assert component.passed is False
    assert "canonical report misses the network deadline" in component.reason


def test_startup_sample_cannot_satisfy_return() -> None:
    raw = _build_bundle_dict()
    for sample in raw["physical_trace"]["samples"][8:]:
        sample["position_enu"]["x_m"] = 20.0
        sample["position_enu"]["y_m"] = 20.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "return_to_launch")
    assert component.passed is False
    assert "returns within the selected launch radius" in component.reason


@pytest.mark.parametrize(
    ("mutator", "schema", "match"),
    (
        (
            lambda raw: raw["physical_trace"]["samples"][9]["position_enu"].update(
                {"x_m": 4.0, "y_m": 0.0}
            ),
            False,
            "selected launch pad",
        ),
        (
            lambda raw: raw["physical_trace"]["samples"][8].__setitem__(
                "linear_velocity_enu_mps",
                {"x_m": 0.0, "y_m": 0.0, "z_m": 0.1},
            ),
            False,
            "with descent and permitted contact",
        ),
        (
            lambda raw: raw["physical_trace"]["samples"][9].__setitem__(
                "linear_velocity_enu_mps",
                {"x_m": 0.0, "y_m": 0.0, "z_m": -1.1},
            ),
            False,
            "with descent and permitted contact",
        ),
        (
            lambda raw: raw["physical_trace"].__setitem__(
                "contact_events",
                tuple(
                    event
                    for event in raw["physical_trace"]["contact_events"]
                    if event["sequence"] != 9
                ),
            ),
            True,
            "sample ground_contact must exactly match active ground or launch-pad contacts",
        ),
    ),
)
def test_wrong_pad_and_missing_descent_contact_fail_landing(
    mutator, schema: bool, match: str
) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    if schema:
        with pytest.raises(ValidationError, match=match):
            InspectionFormalEvidenceBundleV2.model_validate(raw)
        return
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "landing")
    assert component.passed is False
    assert match in component.reason


def test_contact_event_time_must_match_trace_sample_time() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["contact_events"][0]["sim_time_s"] = 2.5
    with pytest.raises(
        ValidationError,
        match="contact event sim_time_s must exactly match its trace sample time",
    ):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda raw: raw["physical_trace"].__setitem__(
            "contact_events",
            tuple(
                event
                for event in raw["physical_trace"]["contact_events"]
                if event["sequence"] != 0
            ),
        ),
        lambda raw: raw["physical_trace"].__setitem__(
            "contact_events",
            tuple(
                event
                for event in raw["physical_trace"]["contact_events"]
                if event["sequence"] != 2
            ),
        ),
    ),
)
def test_contact_state_must_be_explicit_and_match_trace(mutator) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    with pytest.raises(
        ValidationError,
        match="contacts_complete traces must declare explicit initial ground or launch-pad contact state at the first sample|sample ground_contact must exactly match active ground or launch-pad contacts|contact began events require the counterparty to be inactive",
    ):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_contact_ended_before_landing_is_schema_invalid() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["contact_events"] = (
        raw["physical_trace"]["contact_events"][0],
        raw["physical_trace"]["contact_events"][1],
        raw["physical_trace"]["contact_events"][2],
        raw["physical_trace"]["contact_events"][3],
        {
            "sequence": 7,
            "sim_time_s": 8.4,
            "counterparty_kind": "launch_pad",
            "counterparty_id": "launch.main",
            "phase": "began",
        },
        {
            "sequence": 8,
            "sim_time_s": 9.6,
            "counterparty_kind": "launch_pad",
            "counterparty_id": "launch.main",
            "phase": "ended",
        },
    )
    with pytest.raises(
        ValidationError,
        match="sample ground_contact must exactly match active ground or launch-pad contacts",
    ):
        InspectionFormalEvidenceBundleV2.model_validate(raw)


def test_landing_pad_uncertainty_boundary_fails_landing() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][9]["position_enu"]["x_m"] = 1.3
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "landing")
    assert component.passed is False
    assert "selected launch pad" in component.reason


def test_instantaneous_stop_fails_stopped_dwell() -> None:
    raw = _build_bundle_dict()
    raw["policy"]["stopped_dwell_s"] = 2.5
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "stopped_dwell")
    assert component.passed is False
    assert "continuous stopped dwell" in component.reason


def test_interrupted_then_later_valid_stopped_dwell_passes() -> None:
    raw = _build_bundle_dict()
    samples = [copy.deepcopy(sample) for sample in raw["physical_trace"]["samples"]]
    samples[11]["linear_velocity_enu_mps"] = {"x_m": 0.2, "y_m": 0.0, "z_m": 0.0}
    samples[12]["armed"] = True
    samples.append(
        _sample(
            13,
            15.6,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.05,
            vy=0.0,
            vz=0.0,
            armed=True,
            in_air=False,
            landed=True,
            ground_contact=True,
            wz=0.05,
        )
    )
    samples.append(
        _sample(
            14,
            16.8,
            x=0.0,
            y=0.0,
            agl=0.0,
            vx=0.0,
            vy=0.0,
            vz=0.0,
            armed=False,
            in_air=False,
            landed=True,
            ground_contact=True,
        )
    )
    _set_trace_samples(raw, samples)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    assert result.passed is True


def test_stopped_pad_uncertainty_boundary_fails_stopped_dwell() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][10]["position_enu"]["x_m"] = 1.3
    raw["physical_trace"]["samples"][11]["position_enu"]["x_m"] = 1.3
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "stopped_dwell")
    assert component.passed is False
    assert "continuous stopped dwell" in component.reason


@pytest.mark.parametrize(
    ("mutator", "match"),
    (
        (
            lambda raw: raw["physical_trace"]["samples"][12].__setitem__("armed", True),
            "terminal disarmed state",
        ),
        (
            lambda raw: _set_trace_samples(
                raw,
                list(raw["physical_trace"]["samples"])
                + [
                    _sample(
                        13,
                        15.6,
                        x=0.0,
                        y=0.0,
                        agl=0.0,
                        vx=0.0,
                        vy=0.0,
                        vz=0.0,
                        armed=True,
                        in_air=False,
                        landed=True,
                        ground_contact=True,
                    )
                ],
            ),
            "rearms after",
        ),
    ),
)
def test_terminal_armed_and_rearm_fail_terminal_disarm(mutator, match: str) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "terminal_disarm")
    assert component.passed is False
    assert match in component.reason or "terminal disarmed state" in component.reason


def test_terminal_pad_uncertainty_boundary_fails_terminal_disarm() -> None:
    raw = _build_bundle_dict()
    raw["physical_trace"]["samples"][12]["position_enu"]["z_m"] = 0.1
    raw["physical_trace"]["samples"][12]["altitude_amsl_m"] = 0.1
    raw["physical_trace"]["samples"][12]["altitude_agl_m"] = 0.1
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "terminal_disarm")
    assert component.passed is False
    assert "physically at rest on the selected launch pad" in component.reason


@pytest.mark.parametrize(
    ("mutator", "schema"),
    (
        (
            lambda raw: raw["physical_trace"]["samples"][12]["position_enu"].update(
                {"x_m": 2.0, "y_m": 0.0}
            ),
            False,
        ),
        (
            lambda raw: raw["physical_trace"]["samples"][12].__setitem__(
                "linear_velocity_enu_mps",
                {"x_m": 0.2, "y_m": 0.0, "z_m": 0.0},
            ),
            False,
        ),
        (
            lambda raw: raw["physical_trace"]["samples"][12].update(
                {"in_air": True, "landed": False, "ground_contact": False}
            ),
            True,
        ),
    ),
)
def test_terminal_disarm_requires_terminal_rest(mutator, schema: bool) -> None:
    raw = _build_bundle_dict()
    mutator(raw)
    if schema:
        with pytest.raises(
            ValidationError,
            match="sample ground_contact must exactly match active ground or launch-pad contacts",
        ):
            InspectionFormalEvidenceBundleV2.model_validate(raw)
        return
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "terminal_disarm")
    assert component.passed is False
    assert "physically at rest on the selected launch pad" in component.reason


def test_semantic_f1_below_threshold_fails() -> None:
    raw = _build_bundle_dict()
    raw["report"]["detections"] = (
        {
            "detection_id": "detect.alpha",
            "defect_id": "defect.alpha",
            "target_id": "target.alpha",
            "frame_id": "frame.alpha.1",
        },
    )
    _refresh_report_and_outcome(raw)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "semantic_f1")
    assert component.passed is False
    assert "below the mandatory" in component.reason


def test_wrong_target_semantic_laundering_fails_f1() -> None:
    raw = _build_bundle_dict()
    raw["report"]["detections"] = (
        {
            "detection_id": "detect.alpha",
            "defect_id": "defect.alpha",
            "target_id": "target.bravo",
            "frame_id": "frame.bravo.1",
        },
        {
            "detection_id": "detect.bravo",
            "defect_id": "defect.bravo",
            "target_id": "target.alpha",
            "frame_id": "frame.alpha.1",
        },
    )
    _refresh_report_and_outcome(raw)
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "semantic_f1")
    assert component.passed is False
    assert result.semantic_f1 == pytest.approx(0.0)
    assert "below the mandatory" in component.reason


def test_return_radius_uncertainty_boundary_fails_return_component() -> None:
    raw = _build_bundle_dict()
    for sample in raw["physical_trace"]["samples"][8:]:
        sample["position_enu"]["x_m"] = 2.85
        sample["position_enu"]["y_m"] = 0.0
    result = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    component = _component(result, "return_to_launch")
    assert component.passed is False
    assert "returns within the selected launch radius" in component.reason
