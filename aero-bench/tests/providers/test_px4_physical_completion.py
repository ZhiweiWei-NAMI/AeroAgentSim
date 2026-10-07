from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from aero_bench.providers import rpc as _RPC


_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


RUN_ID = "a" * 64
SESSION_TOKEN = "9" * 64
RUNTIME_IMAGE = "registry.test/px4-gazebo@sha256:" + "1" * 64
CONFIG_DIGEST = "b" * 64
STATE_SCHEMA = "px4.state.v1"
COMMAND_SCHEMA = "px4.command.v2"
PROOF_SCHEMA = "px4.command.physical.v3"
STEP_LENGTH_NS = 10
PHYSICS_STEP_NS = 5
SERVICE_PORT = 17434


@pytest.mark.parametrize(
    ("enu_yaw_deg", "expected_mavsdk_heading_deg"),
    ((0.0, 90.0), (90.0, 0.0), (-90.0, -180.0), (180.0, -90.0)),
)
def test_mavsdk_heading_conversion_is_explicitly_enu_to_ned(
    enu_yaw_deg: float, expected_mavsdk_heading_deg: float
) -> None:
    assert _SERVICE._mavsdk_heading_from_enu_yaw(enu_yaw_deg) == pytest.approx(
        expected_mavsdk_heading_deg
    )


def _enu_offset_wgs84(
    position: dict[str, float],
    *,
    east_m: float,
    north_m: float,
    up_m: float,
) -> dict[str, float]:
    transform = _SERVICE.frame_math.EnuTransform.from_origin(
        longitude_deg=position["longitude_deg"],
        latitude_deg=position["latitude_deg"],
        altitude_m=0.0,
    )
    longitude_deg, latitude_deg, _ = transform.enu_to_geodetic(
        _SERVICE.frame_math.Vector3(east_m, north_m, 0.0)
    )
    return {
        "latitude_deg": latitude_deg,
        "longitude_deg": longitude_deg,
        "altitude_m": position["altitude_m"] + up_m,
    }


def _artifact_requirement() -> dict[str, object]:
    return {
        "artifact_id": "artifact.px4.trajectory",
        "artifact_type": "trajectory",
        "producer_id": "flight",
        "visibility": "private",
        "relative_path": "trajectory/evidence.jsonl",
        "max_size_bytes": 1_048_576,
        "source_asset_id": None,
    }


def _policy(*, timeout_ns: int = 50) -> dict[str, object]:
    return {
        "schema_version": "aero-bench.px4-physical-completion-policy/v2",
        "physical_sim_timeout_ns": timeout_ns,
        "min_settle_samples": 2,
        "settle_duration_ns": 20,
        "takeoff_altitude_tolerance_m": 0.25,
        "goto_horizontal_tolerance_m": 1.0,
        "goto_vertical_tolerance_m": 0.5,
        "goto_minimum_progress_m": 5.0,
        "hold_drift_radius_m": 0.75,
        "max_horizontal_settled_speed_m_s": 0.4,
        "max_vertical_settled_speed_m_s": 0.3,
        "landing_max_speed_m_s": 0.35,
        "landing_max_height_proxy_m": 0.2,
        "disarm_requires_contact": True,
        "arm_allowed_modes": ["READY"],
        "disarm_allowed_modes": ["LAND"],
        "takeoff_allowed_modes": ["TAKEOFF"],
        "goto_allowed_modes": ["MISSION"],
        "hold_allowed_modes": ["HOLD"],
        "land_allowed_modes": ["LAND"],
    }


def _parsed_policy(*, timeout_ns: int = 50) -> dict[str, object]:
    return _SERVICE._physical_completion_policy(_policy(timeout_ns=timeout_ns))


def _vehicle() -> dict[str, object]:
    return {
        "vehicle_id": "uav.1",
        "system_id": 1,
        "mavsdk_udp_port": 14540,
        "px4_mavlink_udp_port": 14580,
        "mavsdk_grpc_port": 15040,
        "sys_autostart": 4001,
        "model": "gz_x500_mono_cam",
        "gazebo_model_name": "x500_mono_cam_0",
        "gazebo_resource": "x500_mono_cam",
        "initial_pose": {
            "x_m": 0.0,
            "y_m": 0.0,
            "z_m": 0.1,
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "yaw_rad": 0.0,
        },
    }


def _provider_config(*, timeout_ns: int = 50) -> dict[str, object]:
    return {
        "provider_id": "flight",
        "px4": {"version": _SERVICE.PX4_VERSION, "commit": _SERVICE.PX4_COMMIT},
        "gazebo": {
            "version": _SERVICE.GAZEBO_VERSION,
            "commit": _SERVICE.GAZEBO_COMMIT,
        },
        "mavsdk": {
            "version": _SERVICE.MAVSDK_VERSION,
            "commit": _SERVICE.MAVSDK_COMMIT,
        },
        "world_name": "default",
        "world_sdf": "default.sdf",
        "physics_step_ns": PHYSICS_STEP_NS,
        "step_length_ns": STEP_LENGTH_NS,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [_vehicle()],
        "required_commands": ["gz", "mavsdk-server", "px4"],
        "command_timeout_ms": 1_000,
        "physical_completion_policy": _parsed_policy(timeout_ns=timeout_ns),
        "maximum_agent_decision_wall_time_ms": 1_000,
        "heartbeat_timeout_fixed_margin_ms": 100,
        "inspection": None,
    }


def _prepare_payload(*, timeout_ns: int = 50) -> dict[str, object]:
    del timeout_ns
    return {
        "provider_id": "flight",
        "run_id": RUN_ID,
        "protocol_version": _SERVICE.PROTOCOL_VERSION,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": CONFIG_DIGEST,
        "scenario_digest": "e" * 64,
        "artifact_requirements": [_artifact_requirement()],
        "session_token": SESSION_TOKEN,
        "endpoint": {"host": "127.0.0.1", "port": SERVICE_PORT},
    }


def _rotation_payload(rotation) -> dict[str, list[list[float]]]:
    return {"rows": [list(row) for row in rotation.rows]}


def _frame_authority() -> dict[str, object]:
    longitude_deg = -122.0
    latitude_deg = 37.0
    ellipsoid_height_m = 100.0
    transform = _SERVICE.frame_math.EnuTransform.from_origin(
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        altitude_m=ellipsoid_height_m,
    )
    ecef = {
        "x_m": transform.origin_ecef.x,
        "y_m": transform.origin_ecef.y,
        "z_m": transform.origin_ecef.z,
    }
    return {
        "geodetic_frame_id": "WGS84",
        "ecef_frame_id": "ECEF",
        "enu_frame_id": "ENU",
        "ned_frame_id": "NED",
        "origin": {
            "enu": {"east_m": 0.0, "north_m": 0.0, "up_m": 0.0},
            "ned": {"north_m": 0.0, "east_m": 0.0, "down_m": 0.0},
            "ecef": ecef,
            "wgs84": {
                "longitude_deg": longitude_deg,
                "latitude_deg": latitude_deg,
                "ellipsoid_height_m": ellipsoid_height_m,
            },
            "geoid_separation_m": 0.0,
            "amsl_m": ellipsoid_height_m,
            "terrain_amsl_m": ellipsoid_height_m,
            "agl_m": 0.0,
        },
        "origin_ecef": ecef,
        "ecef_to_enu_rotation": _rotation_payload(transform.ecef_to_enu),
        "enu_to_ecef_rotation": _rotation_payload(transform.enu_to_ecef),
        "enu_to_ned_rotation": _rotation_payload(
            _SERVICE.frame_math.ENU_TO_NED_ROTATION
        ),
        "ned_to_enu_rotation": _rotation_payload(
            _SERVICE.frame_math.NED_TO_ENU_ROTATION
        ),
        "spatial_extent": {
            "min_east_m": -100_000.0,
            "max_east_m": 100_000.0,
            "min_north_m": -100_000.0,
            "max_north_m": 100_000.0,
            "min_up_m": -1_000.0,
            "max_up_m": 10_000.0,
            "vertical_reference": "enu_up",
        },
        "geoid_correction_asset_id": "asset.geoid",
        "terrain_height_asset_id": "asset.terrain-heights",
        "geoid_interpolation": "bilinear",
        "terrain_interpolation": "bilinear",
        "geoid_precision_m": 0.01,
        "terrain_precision_m": 0.01,
    }


def _scalar_grid_asset(
    bundle_root: Path,
    *,
    asset_id: str,
    asset_role: str,
    relative_path: str,
    value_m: float,
) -> dict[str, object]:
    document = {
        "schema_version": "aero-bench.scalar-grid/v1",
        "frame_id": "ENU",
        "east_axis_m": [-100_000.0, 100_000.0],
        "north_axis_m": [-100_000.0, 100_000.0],
        "values_m": [[value_m, value_m], [value_m, value_m]],
    }
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path = bundle_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "source_kind": "world",
        "asset_id": asset_id,
        "file": {
            "path": relative_path,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "byte_size": len(payload),
        "classification": "public",
        "audiences": [],
        "world": {
            "asset_role": asset_role,
            "media_type": "application/json",
            "selector_fragment": None,
        },
    }


def _identity(tmp_path: Path, *, timeout_ns: int = 50) -> _SERVICE.WorkloadIdentity:
    assets = (
        _scalar_grid_asset(
            tmp_path,
            asset_id="asset.geoid",
            asset_role="geoid_model",
            relative_path="world/geoid.json",
            value_m=0.0,
        ),
        _scalar_grid_asset(
            tmp_path,
            asset_id="asset.terrain-heights",
            asset_role="terrain_model",
            relative_path="world/terrain-heights.json",
            value_m=100.0,
        ),
    )
    world_source_sdf = tmp_path / "default.sdf"
    world_source_sdf.write_text(
        "<sdf version='1.9'><world name='default'>"
        "<model name='ground'><static>true</static></model>"
        "</world></sdf>",
        encoding="utf-8",
    )
    return _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        seed=7,
        provider_id="flight",
        provider_port=SERVICE_PORT,
        runtime_image=RUNTIME_IMAGE,
        config_digest=CONFIG_DIGEST,
        scenario_digest="e" * 64,
        scenario=_SERVICE.ValidatedWorkloadScenario(
            scenario_digest="e" * 64,
            scenario={
                "frame_authority": _frame_authority(),
                "launch_sites": [],
                "semantic_targets": [],
                "buildings": [],
                "entities": [],
            },
            assets=assets,
        ),
        bundle_root=tmp_path,
        artifact_requirements=(_artifact_requirement(),),
        clock_step_ns=STEP_LENGTH_NS,
        provider_config=_provider_config(timeout_ns=timeout_ns),
        world_name="default",
        world_source_sdf=world_source_sdf,
        vehicles=(_vehicle(),),
        inspections=(),
        inspection_target_model_sdfs=(),
    )


def _authorized(payload: dict[str, object]) -> dict[str, object]:
    return {"session_token": SESSION_TOKEN, **payload}


def _reset_payload() -> dict[str, object]:
    return {"provider_id": "flight", "run_id": RUN_ID, "seed": 7}


def _step_payload(tick: int) -> dict[str, object]:
    return {
        "provider_id": "flight",
        "run_id": RUN_ID,
        "request": {
            "schema_version": "aero-bench.provider-stage-request/v1",
            "run_id": RUN_ID,
            "scenario_digest": "e" * 64,
            "provider_id": "flight",
            "target": {"tick": tick, "sim_time_ns": tick * STEP_LENGTH_NS},
            "stage": "motion",
        },
    }


def _command_payload(
    tool_id: str,
    command_id: str,
    *,
    tick: int = 0,
    **arguments: object,
) -> dict[str, object]:
    return {
        "provider_id": "flight",
        "run_id": RUN_ID,
        "request": {
            "run_id": RUN_ID,
            "command_id": command_id,
            "agent_id": "agent.1",
            "tool_id": tool_id,
            "issued_at": {"tick": tick, "sim_time_ns": tick * STEP_LENGTH_NS},
            "arguments": [
                {"name": name, "value": value}
                for name, value in sorted(arguments.items())
            ],
        },
    }


def _finalize_payload(tick: int) -> dict[str, object]:
    return {
        "provider_id": "flight",
        "run_id": RUN_ID,
        "request": {
            "schema_version": "aero-bench.provider-finalization-request/v1",
            "run_id": RUN_ID,
            "terminal_event": "run.completed",
            "terminal_time": {"tick": tick, "sim_time_ns": tick * STEP_LENGTH_NS},
            "event_chain_root": "d" * 64,
        },
    }


def _vehicle_telemetry(
    *,
    tick: int,
    x_m: float,
    y_m: float,
    z_m: float,
    altitude_m: float,
    north_m_s: float,
    east_m_s: float,
    down_m_s: float,
    mode: str,
    armed: bool,
    contact: bool,
    latitude_deg: float = 37.0,
    longitude_deg: float = -122.0,
    yaw_rad: float = 0.0,
) -> _SERVICE.VehicleTelemetry:
    return _SERVICE.VehicleTelemetry(
        vehicle_id="uav.1",
        pose_json=json.dumps(
            {
                "x_m": x_m,
                "y_m": y_m,
                "z_m": z_m,
                "roll_rad": 0.0,
                "pitch_rad": 0.0,
                "yaw_rad": yaw_rad,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        position_wgs84_json=json.dumps(
            {
                "latitude_deg": latitude_deg,
                "longitude_deg": longitude_deg,
                "altitude_m": altitude_m,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        velocity_json=json.dumps(
            {
                "north_m_s": north_m_s,
                "east_m_s": east_m_s,
                "down_m_s": down_m_s,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        angular_velocity_json=json.dumps(
            {"x_rad_s": 0.0, "y_rad_s": 0.0, "z_rad_s": 0.0},
            sort_keys=True,
            separators=(",", ":"),
        ),
        attitude_json=json.dumps(
            {"roll_rad": 0.0, "pitch_rad": 0.0, "yaw_rad": yaw_rad},
            sort_keys=True,
            separators=(",", ":"),
        ),
        flight_mode=mode,
        armed=armed,
        in_air=armed and not contact,
        landed=contact,
        landed_state="ON_GROUND" if contact else "IN_AIR",
        battery_percent=95.0,
        health='{"is_global_position_ok":true}',
        contacts=("ground.world",) if contact else (),
        ground_contact=contact,
        collision_contact=False,
        simulation_time_ns=tick * STEP_LENGTH_NS,
    )


def _tool_command_audit_spec(tool_id: str) -> dict[str, object]:
    return dict(_SERVICE.TOOL_COMMAND_AUDIT_SPEC[tool_id])


def _command_audit_frame_fields(
    *,
    message_id: int,
    payload: bytes,
    source_system_id: int,
    source_component_id: int,
    packet_sequence: int,
    frame_encoding: str,
) -> dict[str, object]:
    header = bytes(
        (
            _SERVICE.MAVLINK_V2_MAGIC,
            len(payload),
            0,
            0,
            packet_sequence,
            source_system_id,
            source_component_id,
        )
    ) + message_id.to_bytes(3, "little")
    checksum = _SERVICE._mavlink_x25_checksum(
        header[1:] + payload,
        crc_extra=_SERVICE.MAVLINK_CRC_EXTRAS[message_id],
    )
    frame = header + payload + checksum.to_bytes(2, "little")
    return {
        "canonical_frame_hex": frame.hex(),
        "canonical_frame_length": len(frame),
        "checksum": checksum,
        "compat_flags": 0,
        "frame_encoding": frame_encoding,
        "incompat_flags": 0,
        "magic": _SERVICE.MAVLINK_V2_MAGIC,
        "message_id": message_id,
        "packet_sequence": packet_sequence,
        "payload_hex": payload.hex(),
        "payload_length": len(payload),
        "signature_hex": "",
        "signed_frame": False,
        "wire_version": 2,
    }


def _command_audit_send(
    tool_id: str, *, overrides: dict[str, object] | None = None
) -> dict[str, object]:
    spec = _tool_command_audit_spec(tool_id)
    command = int(spec["mav_cmd"])
    wire_type = str(spec["wire_type"])
    payload_length = 33 if wire_type == _SERVICE.COMMAND_AUDIT_WIRE_LONG else 35
    message_id = (
        _SERVICE.MAVLINK_COMMAND_LONG_MESSAGE_ID
        if wire_type == _SERVICE.COMMAND_AUDIT_WIRE_LONG
        else _SERVICE.MAVLINK_COMMAND_INT_MESSAGE_ID
    )
    payload = bytearray(payload_length)
    payload[28:30] = command.to_bytes(2, "little")
    payload[30] = 1
    payload[31] = 1
    record: dict[str, object] = {
        "acceptance_boundary": _SERVICE.COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY,
        "accepted_connection_count": 1,
        **_command_audit_frame_fields(
            message_id=message_id,
            payload=bytes(payload),
            source_system_id=_SERVICE.MAVSDK_SERVER_SYSTEM_ID,
            source_component_id=_SERVICE.MAVSDK_SERVER_COMPONENT_ID,
            packet_sequence=7,
            frame_encoding=_SERVICE.COMMAND_TRANSPORT_FRAME_ENCODING,
        ),
        "command": command,
        "confirmation": 0,
        "kind": _SERVICE.COMMAND_AUDIT_TRANSPORT_KIND,
        "seq": 1,
        "source_component_id": _SERVICE.MAVSDK_SERVER_COMPONENT_ID,
        "source_system_id": _SERVICE.MAVSDK_SERVER_SYSTEM_ID,
        "target_component_id": 1,
        "target_system_id": 1,
        "timestamp_ns": 100,
        "wire_type": wire_type,
    }
    if overrides is not None:
        record.update(overrides)
    return record


def _command_audit_ack_ingress(
    tool_id: str,
    *,
    result: int,
    seq: int,
    timestamp_ns: int,
    progress: int = 255,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    send_record = _command_audit_send(tool_id)
    command = int(send_record["command"])
    source_system_id = int(send_record["target_system_id"])
    source_component_id = int(send_record["target_component_id"])
    target_system_id = int(send_record["source_system_id"])
    target_component_id = int(send_record["source_component_id"])
    payload = bytearray(10)
    payload[0:2] = command.to_bytes(2, "little")
    payload[2] = result
    payload[3] = progress
    payload[8] = target_system_id
    payload[9] = target_component_id
    record: dict[str, object] = {
        **_command_audit_frame_fields(
            message_id=_SERVICE.MAVLINK_COMMAND_ACK_MESSAGE_ID,
            payload=bytes(payload),
            source_system_id=source_system_id,
            source_component_id=source_component_id,
            packet_sequence=8,
            frame_encoding=_SERVICE.COMMAND_ACK_FRAME_ENCODING,
        ),
        "capture_boundary": _SERVICE.COMMAND_ACK_CAPTURE_BOUNDARY,
        "command": command,
        "kind": _SERVICE.COMMAND_AUDIT_ACK_INGRESS_KIND,
        "progress": progress,
        "result": result,
        "result_param2": 0,
        "seq": seq,
        "source_component_id": source_component_id,
        "source_system_id": source_system_id,
        "target_component_id": target_component_id,
        "target_system_id": target_system_id,
        "timestamp_ns": timestamp_ns,
    }
    if overrides is not None:
        record.update(overrides)
    return record


def _command_audit_ack_records(
    tool_id: str,
    *,
    result: int,
    seq: int,
    timestamp_ns: int,
    progress: int = 255,
    overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    ingress = _command_audit_ack_ingress(
        tool_id,
        result=result,
        seq=seq,
        timestamp_ns=timestamp_ns,
        progress=progress,
        overrides=overrides,
    )
    disposition = {
        "ack_ingress_seq": int(ingress["seq"]),
        "command": int(ingress["command"]),
        "kind": _SERVICE.COMMAND_AUDIT_ACK_DISPOSITION_KIND,
        "seq": int(ingress["seq"]) + 1,
        "status": _SERVICE.COMMAND_AUDIT_MATCHED_DISPOSITION,
        "timestamp_ns": int(ingress["timestamp_ns"]) + 1,
    }
    return ingress, disposition


def _decoded_command_audit(
    tool_id: str,
    *,
    records: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    ordered_records = list(
        records
        or [
            _command_audit_send(tool_id),
            *_command_audit_ack_records(
                tool_id,
                result=_SERVICE.MAV_RESULT_ACCEPTED,
                seq=2,
                timestamp_ns=101,
            ),
        ]
    )
    dispositions = [
        item
        for item in ordered_records
        if item.get("kind") == _SERVICE.COMMAND_AUDIT_ACK_DISPOSITION_KIND
        and item.get("status") == _SERVICE.COMMAND_AUDIT_MATCHED_DISPOSITION
    ]
    assert dispositions
    terminal_disposition = dispositions[-1]
    terminal_ingress = next(
        item
        for item in ordered_records
        if item.get("kind") == _SERVICE.COMMAND_AUDIT_ACK_INGRESS_KIND
        and item.get("seq") == terminal_disposition["ack_ingress_seq"]
    )
    canonical_journal = b"".join(
        _SERVICE._canonical_json(item) + b"\n" for item in ordered_records
    )
    return {
        "schema_version": _SERVICE.COMMAND_AUDIT_SCHEMA_VERSION,
        "vehicle_system_id": 1,
        "expected_mav_cmd": int(_tool_command_audit_spec(tool_id)["mav_cmd"]),
        "expected_wire_type": str(_tool_command_audit_spec(tool_id)["wire_type"]),
        "records": ordered_records,
        "record_sha256s": [_SERVICE._digest_json(item) for item in ordered_records],
        "journal_sha256": _SERVICE._digest_bytes(canonical_journal),
        "terminal_ack_ingress_record": dict(terminal_ingress),
        "terminal_ack_ingress_record_sha256": _SERVICE._digest_json(
            terminal_ingress
        ),
        "terminal_ack_disposition_record": dict(terminal_disposition),
        "terminal_ack_disposition_record_sha256": _SERVICE._digest_json(
            terminal_disposition
        ),
    }


@dataclass
class _FakeStack:
    samples: list[tuple[_SERVICE.VehicleTelemetry, ...]]
    apply_error: str | None = None
    baseline_batches: list[tuple[dict[str, object], ...]] | None = None
    audit_batches: list[tuple[dict[str, object], ...]] | None = None

    def __post_init__(self) -> None:
        self.index = 0
        self.apply_calls = 0
        self.accept_calls = 0
        self.baseline_calls = 0
        self.audit_calls = 0
        self.config = None

    async def verify_identities(self, config) -> None:
        self.config = config

    async def reset(self, config, *, seed: int) -> None:
        self.config = config
        self.index = 0

    async def current_sim_time_ns(self) -> int:
        return self.samples[self.index][0].simulation_time_ns

    async def observed_physics_step_ns(self) -> int:
        return PHYSICS_STEP_NS

    async def multi_step(
        self,
        iterations: int,
        *,
        execution_started: asyncio.Event | None = None,
        command_dispatched: asyncio.Event | None = None,
    ) -> None:
        assert iterations >= 0
        if execution_started is not None:
            assert command_dispatched is not None
            execution_started.set()
            await command_dispatched.wait()
        self.index += 1
        if self.index >= len(self.samples):
            raise AssertionError("missing fake telemetry sample for next barrier")

    async def telemetry(self) -> tuple[_SERVICE.VehicleTelemetry, ...]:
        return self.samples[self.index]

    async def wait_for_telemetry(self) -> None:
        return None

    def camera_frame(self, vehicle_id: str):
        raise AssertionError("inspection is disabled in these tests")

    def read_command_audit_records(
        self, vehicle_id: str, *, require_complete: bool = False
    ) -> tuple[dict[str, object], ...]:
        if self.apply_calls == 0:
            if self.baseline_batches is not None:
                if self.baseline_calls >= len(self.baseline_batches):
                    return ()
                batch = self.baseline_batches[self.baseline_calls]
                self.baseline_calls += 1
                return batch
            self.baseline_calls += 1
            return ()
        if self.audit_batches is not None:
            if self.audit_calls >= len(self.audit_batches):
                return ()
            batch = self.audit_batches[self.audit_calls]
            self.audit_calls += 1
            return batch
        if self.audit_calls > 0:
            return ()
        self.audit_calls += 1
        request = getattr(self, "last_apply_request", None)
        if not isinstance(request, dict):
            raise AssertionError("missing fake apply request for command audit")
        tool_id = request["tool_id"]
        return (
            _command_audit_send(tool_id),
            *_command_audit_ack_records(
                tool_id,
                result=_SERVICE.MAV_RESULT_ACCEPTED,
                seq=2,
                timestamp_ns=101,
            ),
        )

    async def accept_command(self, request) -> None:
        self.accept_calls += 1

    async def apply_command(self, request) -> None:
        self.apply_calls += 1
        self.last_apply_request = dict(request)
        if self.apply_error is not None:
            raise _SERVICE.Px4CommandPhaseError(
                last_success="accepted",
                detail=self.apply_error,
            )

    async def snapshot(self):
        return {"sim_time_ns": await self.current_sim_time_ns()}

    async def stop(self) -> None:
        return None


def _service(tmp_path: Path, stack: _FakeStack, *, timeout_ns: int = 50):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    service = _SERVICE.Px4GazeboService(
        artifact_root=artifact_root,
        rpc_port=SERVICE_PORT,
        workload_identity=_identity(tmp_path, timeout_ns=timeout_ns),
        session_token=SESSION_TOKEN,
        tmp_dir=tmp_path / "tmp",
    )
    service._stack = stack
    return service


async def _prepare_and_reset(
    service: _SERVICE.Px4GazeboService, *, timeout_ns: int = 50
) -> None:
    await service.handle(
        "prepare", _authorized(_prepare_payload(timeout_ns=timeout_ns))
    )
    await service.handle("reset", _authorized(_reset_payload()))


def _events(response: dict[str, object], schema_id: str) -> list[dict[str, object]]:
    receipt = response["receipt"]
    assert isinstance(receipt, dict)
    events = receipt["events"]
    assert isinstance(events, list)
    return [event for event in events if event["payload_schema_id"] == schema_id]


def _payload_map(event: dict[str, object]) -> dict[str, object]:
    payload = event["payload"]
    assert isinstance(payload, list)
    return {item["name"]: item["value"] for item in payload}


def _proof(response: dict[str, object], phase: str) -> dict[str, object]:
    matches = [
        event
        for event in _events(response, PROOF_SCHEMA)
        if _payload_map(event)["phase"] == phase
    ]
    assert len(matches) == 1
    return json.loads(_payload_map(matches[0])["proof_json"])


def _receipts(response: dict[str, object]) -> list[dict[str, object]]:
    receipts = response["receipts"]
    assert isinstance(receipts, list)
    return receipts


def _prepared_config() -> _SERVICE.PreparedConfig:
    return _SERVICE.PreparedConfig(
        provider_id="flight",
        run_id=RUN_ID,
        protocol_version=_SERVICE.PROTOCOL_VERSION,
        runtime_image=RUNTIME_IMAGE,
        config_digest=CONFIG_DIGEST,
        artifact_requirements=(_artifact_requirement(),),
        endpoint_host="127.0.0.1",
        endpoint_port=SERVICE_PORT,
        world_name="default",
        world_sdf="default.sdf",
        world_source_sdf=Path("/tmp/default.sdf"),
        physics_step_ns=PHYSICS_STEP_NS,
        step_length_ns=STEP_LENGTH_NS,
        px4_executable="px4",
        gazebo_executable="gz",
        mavsdk_server_executable="mavsdk-server",
        vehicles=(_vehicle(),),
        required_commands=("gz", "mavsdk-server", "px4"),
        command_timeout_ms=1_000,
        physical_completion_policy=_parsed_policy(),
        landing_sites=({
            "launch_site_id": "pad.home",
            "allowed_uav_entity_ids": ("uav.1",),
            "pose": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.1},
            "pad_radius_m": 2.0,
        },),
        maximum_agent_decision_wall_time_ms=1_000,
        heartbeat_timeout_fixed_margin_ms=100,
        inspections=(),
        inspection_target_model_sdfs=(),
    )


def _parsed_state(
    *,
    tick: int,
    pose: tuple[float, float, float],
    altitude_m: float,
    velocity_ned: tuple[float, float, float],
    mode: str,
    armed: bool,
    contact: bool,
    latitude_deg: float = 37.0,
    longitude_deg: float = -122.0,
) -> _SERVICE.ParsedPhysicalState:
    parsed_pose = {
        "x_m": pose[0],
        "y_m": pose[1],
        "z_m": pose[2],
        "roll_rad": 0.0,
        "pitch_rad": 0.0,
        "yaw_rad": 0.0,
    }
    parsed_position_wgs84 = {
        "latitude_deg": latitude_deg,
        "longitude_deg": longitude_deg,
        "altitude_m": altitude_m,
    }
    parsed_velocity_ned = {
        "north_m_s": velocity_ned[0],
        "east_m_s": velocity_ned[1],
        "down_m_s": velocity_ned[2],
    }
    parsed_angular_velocity_body = {
        "x_rad_s": 0.0,
        "y_rad_s": 0.0,
        "z_rad_s": 0.0,
    }
    contacts = ("ground.world",) if contact else ()
    in_air = armed and not contact
    landed = contact
    landed_state = "ON_GROUND" if contact else "IN_AIR"
    horizontal_speed_m_s = (velocity_ned[0] ** 2 + velocity_ned[1] ** 2) ** 0.5
    vertical_speed_m_s = abs(velocity_ned[2])
    total_speed_m_s = (horizontal_speed_m_s**2 + velocity_ned[2] ** 2) ** 0.5
    return _SERVICE.ParsedPhysicalState(
        vehicle_id="uav.1",
        tick=tick,
        sim_time_ns=tick * STEP_LENGTH_NS,
        pose=parsed_pose,
        position_wgs84=parsed_position_wgs84,
        velocity_ned=parsed_velocity_ned,
        angular_velocity_body=parsed_angular_velocity_body,
        flight_mode=mode,
        armed=armed,
        in_air=in_air,
        landed=landed,
        landed_state=landed_state,
        contacts=contacts,
        ground_contact=contact,
        collision_contact=False,
        state_digest=_SERVICE._physical_state_digest(
            vehicle_id="uav.1",
            tick=tick,
            sim_time_ns=tick * STEP_LENGTH_NS,
            pose=parsed_pose,
            position_wgs84=parsed_position_wgs84,
            velocity_ned=parsed_velocity_ned,
            angular_velocity_body=parsed_angular_velocity_body,
            flight_mode=mode,
            armed=armed,
            in_air=in_air,
            landed=landed,
            landed_state=landed_state,
            contacts=contacts,
            ground_contact=contact,
            collision_contact=False,
        ),
        horizontal_speed_m_s=horizontal_speed_m_s,
        vertical_speed_m_s=vertical_speed_m_s,
        total_speed_m_s=total_speed_m_s,
    )


def _record(
    *,
    tool_id: str,
    baseline: _SERVICE.ParsedPhysicalState,
    arguments: dict[str, object],
) -> _SERVICE.UnresolvedCommandRecord:
    spec = _tool_command_audit_spec(tool_id)
    return _SERVICE.UnresolvedCommandRecord(
        command_id="command.1",
        tool_id=tool_id,
        vehicle_id="uav.1",
        arguments=arguments,
        expected_mav_cmd=int(spec["mav_cmd"]),
        expected_wire_type=str(spec["wire_type"]),
        stage="applied",
        accepted_at=(0, 0),
        applied_at=(1, STEP_LENGTH_NS),
        baseline_state=baseline,
        hold_anchor=dict(baseline.pose),
        deadline_sim_time_ns=50,
        settle_start=None,
        settle_end=None,
        settle_count=0,
        last_state_digest=None,
        last_metrics={},
        failure_latched=False,
        command_audit_status=_SERVICE.COMMAND_AUDIT_APPLIED_STATUS,
        command_audit=_decoded_command_audit(tool_id),
    )


def test_px4_config_rejects_v1_schema_and_missing_policy() -> None:
    raw = _provider_config()
    invalid_policy = _policy()
    invalid_policy["schema_version"] = "aero-bench.px4-physical-completion-policy/v1"
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE._physical_completion_policy(invalid_policy)
    from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig

    with pytest.raises(ValueError):
        Px4GazeboConfig.model_validate(
            {"schema_version": "aero-bench.px4-gazebo/v1", **raw}
        )
    with pytest.raises(ValueError):
        Px4GazeboConfig.model_validate(
            {
                k: v
                for k, v in {
                    "schema_version": "aero-bench.px4-gazebo/v2",
                    **raw,
                }.items()
                if k != "physical_completion_policy"
            }
        )


def test_prepare_rejects_lowercase_mode_allowlist(tmp_path: Path) -> None:
    del tmp_path
    policy = _policy()
    policy["arm_allowed_modes"] = ["ready"]
    with pytest.raises(
        _SERVICE.Px4ServiceError,
        match="arm_allowed_modes\\[0\\] has an invalid value",
    ):
        _SERVICE._physical_completion_policy(policy)


def test_prepare_rejects_timeout_at_exact_completion_boundary(tmp_path: Path) -> None:
    async def run() -> None:
        service = _service(tmp_path, _FakeStack([]), timeout_ns=40)
        with pytest.raises(
            ValueError,
            match="earliest possible physical completion offset",
        ):
            await service.handle("prepare", _authorized(_prepare_payload()))

    asyncio.run(run())


def test_staging_only_command_rpc_and_single_unresolved_command(tmp_path: Path) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=2,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        service = _service(tmp_path, _FakeStack(samples))
        await _prepare_and_reset(service)
        staged = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        assert [receipt["phase"] for receipt in _receipts(staged)] == [
            "received",
            "accepted",
        ]
        assert staged["events"] == []
        rejected_staged = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.ARM_TOOL,
                    "command.arm2",
                    vehicle_id="uav.1",
                )
            ),
        )
        assert [receipt["phase"] for receipt in _receipts(rejected_staged)] == [
            "received",
            "failed",
        ]
        step_one = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(step_one, COMMAND_SCHEMA)
        ] == ["applied"]
        rejected_applied = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.ARM_TOOL,
                    "command.arm3",
                    vehicle_id="uav.1",
                )
            ),
        )
        assert [receipt["phase"] for receipt in _receipts(rejected_applied)] == [
            "received",
            "failed",
        ]

    asyncio.run(run())


def test_takeoff_counts_fresh_identical_paused_samples_and_emits_physical_evidence(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=2,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=3,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=4,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        service = _service(
            tmp_path,
            _FakeStack(
                samples,
                audit_batches=[
                    (
                        _command_audit_send(_SERVICE.TAKEOFF_TOOL),
                        *_command_audit_ack_records(
                            _SERVICE.TAKEOFF_TOOL,
                            result=_SERVICE.MAV_RESULT_IN_PROGRESS,
                            seq=2,
                            timestamp_ns=101,
                            progress=42,
                        ),
                        _command_audit_send(
                            _SERVICE.TAKEOFF_TOOL,
                            overrides={"seq": 4, "timestamp_ns": 103},
                        ),
                        *_command_audit_ack_records(
                            _SERVICE.TAKEOFF_TOOL,
                            result=_SERVICE.MAV_RESULT_ACCEPTED,
                            seq=5,
                            timestamp_ns=104,
                        ),
                    )
                ],
            ),
        )
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["applied"]
        applied_proof = _proof(tick1, "applied")
        assert (
            applied_proof["ack_audit_status"] == _SERVICE.COMMAND_AUDIT_APPLIED_STATUS
        )
        assert applied_proof["decoded_command_audit"]["records"] == [
            _command_audit_send(_SERVICE.TAKEOFF_TOOL),
            *_command_audit_ack_records(
                _SERVICE.TAKEOFF_TOOL,
                result=_SERVICE.MAV_RESULT_IN_PROGRESS,
                seq=2,
                timestamp_ns=101,
                progress=42,
            ),
            _command_audit_send(
                _SERVICE.TAKEOFF_TOOL,
                overrides={"seq": 4, "timestamp_ns": 103},
            ),
            *_command_audit_ack_records(
                _SERVICE.TAKEOFF_TOOL,
                result=_SERVICE.MAV_RESULT_ACCEPTED,
                seq=5,
                timestamp_ns=104,
            ),
        ]
        assert applied_proof["metrics"]["gazebo_relative_altitude_m"] == pytest.approx(
            0.0
        )
        assert applied_proof["settling"]["sample_count"] == 0
        for tick in (2, 3):
            response = await service.handle(
                "step_stage", _authorized(_step_payload(tick))
            )
            assert _events(response, COMMAND_SCHEMA) == []
        tick4 = await service.handle("step_stage", _authorized(_step_payload(4)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick4, COMMAND_SCHEMA)
        ] == ["completed"]
        completed_proof = _proof(tick4, "completed")
        assert (
            completed_proof["ack_audit_status"] == _SERVICE.COMMAND_AUDIT_APPLIED_STATUS
        )
        assert completed_proof["decoded_command_audit"]["records"] == [
            _command_audit_send(_SERVICE.TAKEOFF_TOOL),
            *_command_audit_ack_records(
                _SERVICE.TAKEOFF_TOOL,
                result=_SERVICE.MAV_RESULT_IN_PROGRESS,
                seq=2,
                timestamp_ns=101,
                progress=42,
            ),
            _command_audit_send(
                _SERVICE.TAKEOFF_TOOL,
                overrides={"seq": 4, "timestamp_ns": 103},
            ),
            *_command_audit_ack_records(
                _SERVICE.TAKEOFF_TOOL,
                result=_SERVICE.MAV_RESULT_ACCEPTED,
                seq=5,
                timestamp_ns=104,
            ),
        ]
        assert completed_proof["metrics"]["gazebo_altitude_satisfied"] is True
        assert completed_proof["metrics"]["px4_altitude_satisfied"] is True
        assert completed_proof["metrics"]["settled_speed"] is True
        assert completed_proof["settling"]["sample_count"] == 3
        assert completed_proof["settling"]["sample_duration_ns"] == 20

    asyncio.run(run())


def test_prior_completed_command_audit_records_are_drained_before_dispatch(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        stack = _FakeStack(
            samples,
            baseline_batches=[
                (
                    _command_audit_send(
                        _SERVICE.ARM_TOOL,
                        overrides={"seq": 1, "timestamp_ns": 100},
                    ),
                    *_command_audit_ack_records(
                        _SERVICE.ARM_TOOL,
                        result=_SERVICE.MAV_RESULT_ACCEPTED,
                        seq=2,
                        timestamp_ns=101,
                    ),
                )
            ],
            audit_batches=[
                (
                    _command_audit_send(
                        _SERVICE.TAKEOFF_TOOL,
                        overrides={"seq": 4, "timestamp_ns": 103},
                    ),
                    *_command_audit_ack_records(
                        _SERVICE.TAKEOFF_TOOL,
                        result=_SERVICE.MAV_RESULT_ACCEPTED,
                        seq=5,
                        timestamp_ns=104,
                    ),
                )
            ],
        )
        service = _service(tmp_path, stack)
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["applied"]
        applied_proof = _proof(tick1, "applied")
        assert applied_proof["decoded_command_audit"]["records"] == [
            _command_audit_send(
                _SERVICE.TAKEOFF_TOOL,
                overrides={"seq": 4, "timestamp_ns": 103},
            ),
            *_command_audit_ack_records(
                _SERVICE.TAKEOFF_TOOL,
                result=_SERVICE.MAV_RESULT_ACCEPTED,
                seq=5,
                timestamp_ns=104,
            ),
        ]
        assert stack.apply_calls == 1

    asyncio.run(run())


def test_unresolved_prewindow_command_audit_fails_before_dispatch(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        stack = _FakeStack(
            samples,
            baseline_batches=[
                (
                    _command_audit_send(
                        _SERVICE.ARM_TOOL,
                        overrides={"seq": 1, "timestamp_ns": 100},
                    ),
                    *_command_audit_ack_records(
                        _SERVICE.ARM_TOOL,
                        result=_SERVICE.MAV_RESULT_IN_PROGRESS,
                        seq=2,
                        timestamp_ns=101,
                        progress=7,
                    ),
                )
            ],
        )
        service = _service(tmp_path, stack)
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["failed"]
        failed_proof = _proof(tick1, "failed")
        assert failed_proof["ack_audit_status"] == _SERVICE.COMMAND_AUDIT_CORRUPT_STATUS
        assert failed_proof["decoded_command_audit"] is None
        assert stack.apply_calls == 0

    asyncio.run(run())


def test_current_window_without_matching_ack_fails_after_apply(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        stack = _FakeStack(
            samples,
            audit_batches=[
                (
                    _command_audit_send(_SERVICE.TAKEOFF_TOOL),
                    _command_audit_send(
                        _SERVICE.ARM_TOOL,
                        overrides={"seq": 2, "timestamp_ns": 101},
                    ),
                )
            ],
        )
        service = _service(tmp_path, stack)
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["failed"]
        failed_proof = _proof(tick1, "failed")
        assert failed_proof["ack_audit_status"] == _SERVICE.COMMAND_AUDIT_MISSING_STATUS
        assert failed_proof["decoded_command_audit"] is None
        assert stack.apply_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    ("audit_batch", "expected_status"),
    (
        (
            (_command_audit_send(_SERVICE.TAKEOFF_TOOL),),
            _SERVICE.COMMAND_AUDIT_MISSING_STATUS,
        ),
        (
            (
                _command_audit_send(
                    _SERVICE.TAKEOFF_TOOL,
                    overrides={"command": _SERVICE.MAV_RESULT_FAILED},
                ),
                *_command_audit_ack_records(
                    _SERVICE.TAKEOFF_TOOL,
                    result=_SERVICE.MAV_RESULT_ACCEPTED,
                    seq=2,
                    timestamp_ns=101,
                    overrides={"command": _SERVICE.MAV_RESULT_FAILED},
                ),
            ),
            _SERVICE.COMMAND_AUDIT_MISSING_STATUS,
        ),
        (
            (
                _command_audit_send(_SERVICE.TAKEOFF_TOOL),
                *_command_audit_ack_records(
                    _SERVICE.TAKEOFF_TOOL,
                    result=_SERVICE.MAV_RESULT_ACCEPTED,
                    seq=2,
                    timestamp_ns=101,
                    overrides={"source_system_id": 2},
                ),
            ),
            _SERVICE.COMMAND_AUDIT_CORRUPT_STATUS,
        ),
        (
            (
                _command_audit_send(_SERVICE.TAKEOFF_TOOL),
                *_command_audit_ack_records(
                    _SERVICE.TAKEOFF_TOOL,
                    result=_SERVICE.MAV_RESULT_ACCEPTED,
                    seq=2,
                    timestamp_ns=101,
                    overrides={"source_component_id": 2},
                ),
            ),
            _SERVICE.COMMAND_AUDIT_CORRUPT_STATUS,
        ),
        (
            (
                _command_audit_send(_SERVICE.TAKEOFF_TOOL),
                *_command_audit_ack_records(
                    _SERVICE.TAKEOFF_TOOL,
                    result=_SERVICE.MAV_RESULT_DENIED,
                    seq=2,
                    timestamp_ns=101,
                ),
            ),
            _SERVICE.COMMAND_AUDIT_FAILED_STATUS,
        ),
        (
            (
                _command_audit_send(_SERVICE.TAKEOFF_TOOL, overrides={"seq": 3}),
                *_command_audit_ack_records(
                    _SERVICE.TAKEOFF_TOOL,
                    result=_SERVICE.MAV_RESULT_ACCEPTED,
                    seq=2,
                    timestamp_ns=101,
                ),
            ),
            _SERVICE.COMMAND_AUDIT_CORRUPT_STATUS,
        ),
    ),
)
def test_decoded_command_audit_failures_latch_before_apply(
    tmp_path: Path,
    audit_batch: tuple[dict[str, object], ...],
    expected_status: str,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        service = _service(
            tmp_path,
            _FakeStack(samples, audit_batches=[audit_batch]),
        )
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["failed"]
        failed_proof = _proof(tick1, "failed")
        assert failed_proof["ack_audit_status"] == expected_status
        assert failed_proof["applied_tick"] is None
        assert service._command_record is not None
        assert service._command_record.stage == "failed"
        assert service._command_record.failure_latched is True
        rejected = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.ARM_TOOL,
                    "command.after-failed-audit",
                    vehicle_id="uav.1",
                )
            ),
        )
        assert [receipt["phase"] for receipt in _receipts(rejected)] == [
            "received",
            "failed",
        ]

    asyncio.run(run())


def test_physical_state_digest_distinguishes_fresh_identical_ticks_and_rejects_stale(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        _FakeStack(
            [
                (
                    _vehicle_telemetry(
                        tick=0,
                        x_m=0.0,
                        y_m=0.0,
                        z_m=0.1,
                        altitude_m=100.0,
                        north_m_s=0.0,
                        east_m_s=0.0,
                        down_m_s=0.0,
                        mode="HOLD",
                        armed=True,
                        contact=True,
                    ),
                )
            ]
        ),
    )
    fresh_tick_2 = (
        _vehicle_telemetry(
            tick=2,
            x_m=1.0,
            y_m=2.0,
            z_m=3.0,
            altitude_m=101.0,
            north_m_s=0.0,
            east_m_s=0.0,
            down_m_s=0.0,
            mode="HOLD",
            armed=True,
            contact=False,
        ),
    )
    fresh_tick_3 = (
        _vehicle_telemetry(
            tick=3,
            x_m=1.0,
            y_m=2.0,
            z_m=3.0,
            altitude_m=101.0,
            north_m_s=0.0,
            east_m_s=0.0,
            down_m_s=0.0,
            mode="HOLD",
            armed=True,
            contact=False,
        ),
    )
    state_2 = service._physical_state_from_telemetry(
        fresh_tick_2,
        vehicle_id="uav.1",
        target=(2, 20),
    )
    state_3 = service._physical_state_from_telemetry(
        fresh_tick_3,
        vehicle_id="uav.1",
        target=(3, 30),
    )
    assert state_2.pose == state_3.pose
    assert state_2.position_wgs84 == state_3.position_wgs84
    assert state_2.velocity_ned == state_3.velocity_ned
    assert state_2.state_digest != state_3.state_digest
    with pytest.raises(_SERVICE.Px4ServiceError, match="stale simulation time"):
        service._physical_state_from_telemetry(
            fresh_tick_2,
            vehicle_id="uav.1",
            target=(3, 30),
        )


@pytest.mark.parametrize(
    (
        "tool_id",
        "command_id",
        "baseline_mode",
        "baseline_armed",
        "step_mode",
        "step_armed",
    ),
    (
        (
            _SERVICE.ARM_TOOL,
            "command.arm",
            "READY",
            False,
            "HOLD",
            True,
        ),
        (
            _SERVICE.DISARM_TOOL,
            "command.disarm",
            "LAND",
            True,
            "HOLD",
            False,
        ),
    ),
)
def test_arm_and_disarm_wrong_mode_remain_applied(
    tmp_path: Path,
    tool_id: str,
    command_id: str,
    baseline_mode: str,
    baseline_armed: bool,
    step_mode: str,
    step_armed: bool,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode=baseline_mode,
                    armed=baseline_armed,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode=step_mode,
                    armed=step_armed,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=2,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode=step_mode,
                    armed=step_armed,
                    contact=True,
                ),
            ),
        ]
        service = _service(tmp_path, _FakeStack(samples))
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    tool_id,
                    command_id,
                    vehicle_id="uav.1",
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["applied"]
        assert _proof(tick1, "applied")["metrics"]["allowlisted_mode"] is False
        tick2 = await service.handle("step_stage", _authorized(_step_payload(2)))
        assert _events(tick2, COMMAND_SCHEMA) == []
        assert service._command_record is not None
        assert service._command_record.stage == "applied"
        assert service._command_record.failure_latched is False

    asyncio.run(run())


@pytest.mark.parametrize(
    ("tool_id", "record_factory", "current_state"),
    (
        (
            _SERVICE.TAKEOFF_TOOL,
            lambda baseline: _record(
                tool_id=_SERVICE.TAKEOFF_TOOL,
                baseline=baseline,
                arguments={"vehicle_id": "uav.1", "altitude_m": 3.0},
            ),
            _parsed_state(
                tick=2,
                pose=(0.0, 0.0, 0.1),
                altitude_m=100.0,
                velocity_ned=(0.0, 0.0, 0.0),
                mode="TAKEOFF",
                armed=True,
                contact=False,
            ),
        ),
        (
            _SERVICE.GOTO_TOOL,
            lambda baseline: _record(
                tool_id=_SERVICE.GOTO_TOOL,
                baseline=baseline,
                arguments={
                    "vehicle_id": "uav.1",
                    "latitude_deg": _enu_offset_wgs84(
                        baseline.position_wgs84,
                        east_m=10.0,
                        north_m=0.0,
                        up_m=5.0,
                    )["latitude_deg"],
                    "longitude_deg": _enu_offset_wgs84(
                        baseline.position_wgs84,
                        east_m=10.0,
                        north_m=0.0,
                        up_m=5.0,
                    )["longitude_deg"],
                    "altitude_amsl_m": _enu_offset_wgs84(
                        baseline.position_wgs84,
                        east_m=10.0,
                        north_m=0.0,
                        up_m=5.0,
                    )["altitude_m"],
                    "yaw_deg": 0.0,
                },
            ),
            _parsed_state(
                tick=2,
                pose=(0.0, 0.0, 0.1),
                altitude_m=_enu_offset_wgs84(
                    {
                        "latitude_deg": 37.0,
                        "longitude_deg": -122.0,
                        "altitude_m": 100.0,
                    },
                    east_m=10.0,
                    north_m=0.0,
                    up_m=5.0,
                )["altitude_m"],
                velocity_ned=(0.0, 0.0, 0.0),
                mode="MISSION",
                armed=True,
                contact=False,
                latitude_deg=_enu_offset_wgs84(
                    {
                        "latitude_deg": 37.0,
                        "longitude_deg": -122.0,
                        "altitude_m": 100.0,
                    },
                    east_m=10.0,
                    north_m=0.0,
                    up_m=5.0,
                )["latitude_deg"],
                longitude_deg=_enu_offset_wgs84(
                    {
                        "latitude_deg": 37.0,
                        "longitude_deg": -122.0,
                        "altitude_m": 100.0,
                    },
                    east_m=10.0,
                    north_m=0.0,
                    up_m=5.0,
                )["longitude_deg"],
            ),
        ),
        (
            _SERVICE.HOLD_TOOL,
            lambda baseline: _record(
                tool_id=_SERVICE.HOLD_TOOL,
                baseline=baseline,
                arguments={"vehicle_id": "uav.1"},
            ),
            _parsed_state(
                tick=2,
                pose=(1.5, 0.0, 0.1),
                altitude_m=100.0,
                velocity_ned=(0.0, 0.0, 0.0),
                mode="HOLD",
                armed=True,
                contact=False,
            ),
        ),
        (
            _SERVICE.LAND_TOOL,
            lambda baseline: _record(
                tool_id=_SERVICE.LAND_TOOL,
                baseline=baseline,
                arguments={"vehicle_id": "uav.1"},
            ),
            _parsed_state(
                tick=2,
                pose=(0.0, 0.0, 0.15),
                altitude_m=100.05,
                velocity_ned=(0.0, 0.0, 0.0),
                mode="LAND",
                armed=True,
                contact=False,
            ),
        ),
        (
            _SERVICE.ARM_TOOL,
            lambda baseline: _record(
                tool_id=_SERVICE.ARM_TOOL,
                baseline=baseline,
                arguments={"vehicle_id": "uav.1"},
            ),
            _parsed_state(
                tick=2,
                pose=(0.0, 0.0, 0.1),
                altitude_m=100.0,
                velocity_ned=(0.0, 0.0, 0.0),
                mode="READY",
                armed=False,
                contact=True,
            ),
        ),
        (
            _SERVICE.DISARM_TOOL,
            lambda baseline: _record(
                tool_id=_SERVICE.DISARM_TOOL,
                baseline=baseline,
                arguments={"vehicle_id": "uav.1"},
            ),
            _parsed_state(
                tick=2,
                pose=(0.0, 0.0, 0.1),
                altitude_m=100.0,
                velocity_ned=(0.0, 0.0, 0.0),
                mode="LAND",
                armed=True,
                contact=True,
            ),
        ),
    ),
)
def test_physical_predicates_reject_incomplete_terminal_states(
    tmp_path: Path,
    tool_id: str,
    record_factory,
    current_state: _SERVICE.ParsedPhysicalState,
) -> None:
    service = _service(
        tmp_path,
        _FakeStack(
            [
                (
                    _vehicle_telemetry(
                        tick=0,
                        x_m=0.0,
                        y_m=0.0,
                        z_m=0.1,
                        altitude_m=100.0,
                        north_m_s=0.0,
                        east_m_s=0.0,
                        down_m_s=0.0,
                        mode="HOLD",
                        armed=True,
                        contact=True,
                    ),
                )
            ]
        ),
    )
    baseline = _parsed_state(
        tick=0,
        pose=(0.0, 0.0, 0.1),
        altitude_m=100.0,
        velocity_ned=(0.0, 0.0, 0.0),
        mode="HOLD" if tool_id == _SERVICE.HOLD_TOOL else "READY",
        armed=True if tool_id != _SERVICE.TAKEOFF_TOOL else False,
        contact=True,
    )
    record = record_factory(baseline)
    satisfied, _, _ = service._evaluate_physical_completion(
        config=_prepared_config(),
        record=record,
        current=current_state,
    )
    assert satisfied is False


def test_timeout_failure_latches_and_blocks_finalize_and_new_command(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=2,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=3,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=4,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        service = _service(tmp_path, _FakeStack(samples))
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        await service.handle("step_stage", _authorized(_step_payload(1)))
        assert service._command_record is not None
        service._command_record.deadline_sim_time_ns = 30
        tick2 = await service.handle("step_stage", _authorized(_step_payload(2)))
        assert _events(tick2, COMMAND_SCHEMA) == []
        tick3 = await service.handle("step_stage", _authorized(_step_payload(3)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick3, COMMAND_SCHEMA)
        ] == ["failed"]
        tick4 = await service.handle("step_stage", _authorized(_step_payload(4)))
        assert _events(tick4, COMMAND_SCHEMA) == []
        rejected = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.ARM_TOOL,
                    "command.arm-after-failure",
                    vehicle_id="uav.1",
                )
            ),
        )
        assert [receipt["phase"] for receipt in _receipts(rejected)] == [
            "received",
            "failed",
        ]
        with pytest.raises(ValueError, match="failure-latched"):
            await service.handle("finalize", _authorized(_finalize_payload(4)))

    asyncio.run(run())


def test_exact_deadline_timeout_wins_over_boundary_completion(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        samples = [
            (
                _vehicle_telemetry(
                    tick=0,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    altitude_m=100.0,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="READY",
                    armed=False,
                    contact=True,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=1,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=2,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=3,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=4,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
            (
                _vehicle_telemetry(
                    tick=5,
                    x_m=0.0,
                    y_m=0.0,
                    z_m=3.12,
                    altitude_m=103.08,
                    north_m_s=0.0,
                    east_m_s=0.0,
                    down_m_s=0.0,
                    mode="TAKEOFF",
                    armed=True,
                    contact=False,
                ),
            ),
        ]
        service = _service(tmp_path, _FakeStack(samples))
        await _prepare_and_reset(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    _SERVICE.TAKEOFF_TOOL,
                    "command.takeoff",
                    vehicle_id="uav.1",
                    altitude_m=3.0,
                )
            ),
        )
        tick1 = await service.handle("step_stage", _authorized(_step_payload(1)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick1, COMMAND_SCHEMA)
        ] == ["applied"]
        assert service._command_record is not None
        service._command_record.deadline_sim_time_ns = 40
        tick2 = await service.handle("step_stage", _authorized(_step_payload(2)))
        assert _events(tick2, COMMAND_SCHEMA) == []
        tick3 = await service.handle("step_stage", _authorized(_step_payload(3)))
        assert _events(tick3, COMMAND_SCHEMA) == []
        tick4 = await service.handle("step_stage", _authorized(_step_payload(4)))
        assert [
            _payload_map(event)["phase"] for event in _events(tick4, COMMAND_SCHEMA)
        ] == ["failed"]
        failed_proof = _proof(tick4, "failed")
        assert failed_proof["ack_audit_status"] == _SERVICE.COMMAND_AUDIT_APPLIED_STATUS
        assert failed_proof["decoded_command_audit"] == _decoded_command_audit(
            _SERVICE.TAKEOFF_TOOL
        )
        assert failed_proof["current_sim_time_ns"] == 40
        assert failed_proof["metrics"]["allowlisted_mode"] is True
        assert failed_proof["metrics"]["gazebo_altitude_satisfied"] is True
        assert failed_proof["metrics"]["px4_altitude_satisfied"] is True
        assert failed_proof["settling"]["sample_count"] == 2
        assert failed_proof["settling"]["sample_duration_ns"] == 10
        assert service._command_record is not None
        assert service._command_record.stage == "failed"
        assert service._command_record.failure_latched is True
        tick5 = await service.handle("step_stage", _authorized(_step_payload(5)))
        assert _events(tick5, COMMAND_SCHEMA) == []

    asyncio.run(run())


@pytest.mark.parametrize(
    ("height_error_m", "contact_site", "expected"),
    ((0.0, "pad.dropoff", True), (0.51, "pad.dropoff", False), (0.0, "pad.pickup", False)),
)
def test_land_uses_destination_pad_geometry_and_preserves_contact_and_tolerance(
    tmp_path: Path, height_error_m: float, contact_site: str, expected: bool,
) -> None:
    service = _service(tmp_path, _FakeStack([]))
    vehicle = _vehicle()
    vehicle["initial_pose"] = {**vehicle["initial_pose"], "z_m": 3.3495}
    config = replace(
        _prepared_config(), vehicles=(vehicle,),
        physical_completion_policy={**_parsed_policy(), "landing_max_height_proxy_m": 0.5},
        landing_sites=tuple(
            {"launch_site_id": name, "allowed_uav_entity_ids": ("uav.1",),
             "pose": {"x_m": x, "y_m": -450.0, "z_m": z}, "pad_radius_m": 4.0}
            for name, x, z in (("pad.pickup", -450.0, 3.3495), ("pad.dropoff", -420.0, 0.707))
        ),
    )
    baseline = _parsed_state(tick=40, pose=(-420.0, -450.0, 20.0), altitude_m=40.0,
                             velocity_ned=(0.0, 0.0, 0.0), mode="LAND", armed=True, contact=False)
    current = replace(
        _parsed_state(tick=66, pose=(-420.0, -450.0, 0.707 + height_error_m), altitude_m=20.707,
                      velocity_ned=(0.0, 0.0, 0.0), mode="LAND", armed=False, contact=True),
        contacts=(f"launch_pad.{contact_site}",),
    )
    satisfied, _, metrics = service._evaluate_physical_completion(
        config=config, record=_record(tool_id=_SERVICE.LAND_TOOL, baseline=baseline,
                                     arguments={"vehicle_id": "uav.1"}), current=current,
    )
    assert satisfied is expected
    assert metrics["landing_site_id"] == "pad.dropoff"
    assert metrics["ground_baseline_proxy_z_m"] == 0.707
    assert metrics["gazebo_relative_height_proxy_m"] == pytest.approx(height_error_m)


@pytest.mark.parametrize("on_pad", (True, False))
def test_land_preserves_return_pad_aliases_and_ground_landing(tmp_path: Path, on_pad: bool) -> None:
    service = _service(tmp_path, _FakeStack([]))
    sites = tuple(
        {"launch_site_id": name, "allowed_uav_entity_ids": ("uav.1",),
         "pose": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.1}, "pad_radius_m": 2.0}
        for name in ("pad.original", "pad.pickup")
    ) if on_pad else ()
    config = replace(_prepared_config(), landing_sites=sites)
    baseline = _parsed_state(tick=40, pose=(0.0, 0.0, 20.0), altitude_m=120.0,
                             velocity_ned=(0.0, 0.0, 0.0), mode="LAND", armed=True, contact=False)
    current = replace(
        _parsed_state(tick=66, pose=(0.0, 0.0, 0.1), altitude_m=100.1,
                      velocity_ned=(0.0, 0.0, 0.0), mode="LAND", armed=False, contact=True),
        contacts=("launch_pad.pad.pickup",) if on_pad else ("ground.world",),
    )
    satisfied, _, metrics = service._evaluate_physical_completion(
        config=config, record=_record(tool_id=_SERVICE.LAND_TOOL, baseline=baseline,
                                     arguments={"vehicle_id": "uav.1"}), current=current,
    )
    assert satisfied
    assert metrics["landing_site_id"] == ("pad.pickup" if on_pad else None)
    assert metrics["ground_baseline_proxy_z_m"] == pytest.approx(0.1 if on_pad else 0.0, abs=1e-6)
