from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from aero_bench.config.models import (
    ArtifactRequirement,
    ClockSpec,
    FileRef,
    ImplementationIdentity,
    NamedValue,
)
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.px4_gazebo.config import (
    PhysicalCompletionPolicy,
    Pose,
    Px4GazeboConfig,
    SoftwareIdentity,
    VehicleSpec,
)
from aero_bench.providers.px4_gazebo.provider import (
    PROTOCOL_VERSION,
    Px4GazeboProvider,
    Px4ProviderError,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    BatteryState,
    BodyAngularVelocity,
    CommandRequest,
    EnuLinearVelocity,
    HealthState,
    MotionStageRequest,
    NedLinearVelocity,
    ProviderEvent,
    ProviderFinalizationRequest,
    SimulationTime,
    StateAttribute,
    StateSample,
    StepReceipt,
    state_sample_digest_value,
)
from aero_bench.world.resolved import (
    ResolvedCoordinate,
    ResolvedEcefPosition,
    ResolvedEnuPosition,
    ResolvedEntity,
    ResolvedNedPosition,
    ResolvedPose,
    ResolvedProvider,
    ResolvedQuaternion,
    ResolvedScenario,
    ResolvedWgs84Position,
    ResolvedTaskBinding,
)


RUN_ID = "a" * 64
SESSION_TOKEN = "c" * 64
SCENARIO_DIGEST = "4" * 64
STATE_SCHEMA = "px4.state.v1"
COMMAND_SCHEMA = "px4.command.v2"
PROOF_SCHEMA = "px4.command.physical.v3"
MAVSDK_SERVER_SYSTEM_ID = 245
MAVSDK_SERVER_COMPONENT_ID = 190
COMMAND_AUDIT_TRANSPORT_KIND = "command_transport_accept"
COMMAND_AUDIT_ACK_INGRESS_KIND = "command_ack_ingress"
COMMAND_AUDIT_ACK_DISPOSITION_KIND = "command_ack_disposition"
COMMAND_AUDIT_MATCHED_DISPOSITION = "matched_outstanding_command"
COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY = (
    "mavsdk_impl.deliver_message.connection_send_return"
)
COMMAND_TRANSPORT_FRAME_ENCODING = (
    "mavlink_msg_to_send_buffer_from_packed_message"
)
COMMAND_ACK_CAPTURE_BOUNDARY = (
    "mavlink_command_sender.receive_command_ack.decoded_handler"
)
COMMAND_ACK_FRAME_ENCODING = "mavlink_msg_to_send_buffer_from_decoded_message"
COMMAND_AUDIT_APPLIED_STATUS = "decoded_command_ack_accepted"
COMMAND_AUDIT_MISSING_STATUS = "decoded_command_ack_missing"
COMMAND_AUDIT_SCHEMA_VERSION = "aero-bench.px4-decoded-command-audit/v2"
MAVLINK_V2_MAGIC = 0xFD
MAVLINK_COMMAND_INT_MESSAGE_ID = 75
MAVLINK_COMMAND_LONG_MESSAGE_ID = 76
MAVLINK_COMMAND_ACK_MESSAGE_ID = 77
MAVLINK_CRC_EXTRAS = {
    MAVLINK_COMMAND_INT_MESSAGE_ID: 158,
    MAVLINK_COMMAND_LONG_MESSAGE_ID: 152,
    MAVLINK_COMMAND_ACK_MESSAGE_ID: 143,
}
MAV_RESULT_ACCEPTED = 0
MAV_RESULT_IN_PROGRESS = 5
TOOL_COMMAND_AUDIT_SPEC = {
    "flight.arm": {"mav_cmd": 400, "wire_type": "COMMAND_LONG"},
    "flight.disarm": {"mav_cmd": 400, "wire_type": "COMMAND_LONG"},
    "flight.takeoff": {"mav_cmd": 22, "wire_type": "COMMAND_LONG"},
    "flight.land": {"mav_cmd": 21, "wire_type": "COMMAND_LONG"},
    "flight.goto": {"mav_cmd": 192, "wire_type": "COMMAND_INT"},
    "flight.hold": {"mav_cmd": 176, "wire_type": "COMMAND_LONG"},
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _artifact_requirement() -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id="artifact.px4.trajectory",
        artifact_type="trajectory",
        producer_id="flight",
        visibility="private",
        relative_path="trajectory/evidence.jsonl",
        max_size_bytes=1_048_576,
        source_asset_id=None,
    )


def _manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id="flight",
        adapter="px4.gazebo",
        implementation=ImplementationIdentity(
            component_id="px4.gazebo",
            kind="mechanical_fixture",
            source_uri="https://github.com/moby/moby",
            source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
            version="test-fixture-1",
        ),
        runtime_image="registry.test/px4-gazebo@sha256:" + "1" * 64,
        config_digest="f" * 64,
        capabilities=("flight.path",),
        protocol_schema=FileRef(path="px4.json", sha256="e" * 64),
        artifact_requirements=(_artifact_requirement(),),
    )


def _config() -> Px4GazeboConfig:
    return Px4GazeboConfig(
        schema_version="aero-bench.px4-gazebo/v3",
        provider_id="flight",
        px4=SoftwareIdentity(
            version="v1.17.0-alpha1-1551-g381149fb01", commit="1" * 40
        ),
        gazebo=SoftwareIdentity(version="8.11.0", commit="2" * 40),
        mavsdk=SoftwareIdentity(version="3.17.2", commit="3" * 40),
        engine_binding_id="binding.gazebo",
        physics_step_ns=5,
        px4_executable="px4",
        gazebo_executable="gz",
        mavsdk_server_executable="mavsdk-server",
        vehicles=(
            VehicleSpec(
                vehicle_id="uav.1",
                system_id=1,
                mavsdk_udp_port=14540,
                px4_mavlink_udp_port=14580,
                mavsdk_grpc_port=15040,
                sys_autostart=4001,
                model="gz_x500_mono_cam",
                gazebo_model_name="x500_mono_cam_0",
                gazebo_resource="x500_mono_cam",
                initial_pose=Pose(
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.1,
                    roll_rad=0.0,
                    pitch_rad=0.0,
                    yaw_rad=0.0,
                ),
            ),
        ),
        required_commands=("gz", "mavsdk-server", "px4"),
        command_timeout_ms=1_000,
        physical_completion_policy=PhysicalCompletionPolicy(
            schema_version="aero-bench.px4-physical-completion-policy/v2",
            physical_sim_timeout_ns=50,
            min_settle_samples=2,
            settle_duration_ns=20,
            takeoff_altitude_tolerance_m=0.25,
            goto_horizontal_tolerance_m=1.0,
            goto_vertical_tolerance_m=0.5,
            goto_minimum_progress_m=5.0,
            hold_drift_radius_m=0.75,
            max_horizontal_settled_speed_m_s=0.4,
            max_vertical_settled_speed_m_s=0.3,
            landing_max_speed_m_s=0.35,
            landing_max_height_proxy_m=0.2,
            disarm_requires_contact=True,
            arm_allowed_modes=("READY",),
            disarm_allowed_modes=("LAND",),
            takeoff_allowed_modes=("TAKEOFF",),
            goto_allowed_modes=("MISSION",),
            hold_allowed_modes=("HOLD",),
            land_allowed_modes=("LAND",),
        ),
        maximum_agent_decision_wall_time_ms=1_000,
        heartbeat_timeout_fixed_margin_ms=100,
    )


def _scenario() -> ResolvedScenario:
    return ResolvedScenario.model_construct(
        scenario_digest=SCENARIO_DIGEST,
        providers=(ResolvedProvider.model_construct(provider_id="flight"),),
        entities=(
            ResolvedEntity.model_construct(
                entity_id="uav.1",
                kind="uav",
                owner_kind="provider",
                owner_id="flight",
                source_provider_id="flight",
            ),
        ),
        task=ResolvedTaskBinding.model_construct(observations=()),
    )


def _scenario_two_vehicles() -> ResolvedScenario:
    return ResolvedScenario.model_construct(
        scenario_digest=SCENARIO_DIGEST,
        providers=(ResolvedProvider.model_construct(provider_id="flight"),),
        entities=(
            ResolvedEntity.model_construct(
                entity_id="uav.1",
                kind="uav",
                owner_kind="provider",
                owner_id="flight",
                source_provider_id="flight",
            ),
            ResolvedEntity.model_construct(
                entity_id="uav.2",
                kind="uav",
                owner_kind="provider",
                owner_id="flight",
                source_provider_id="flight",
            ),
        ),
        task=ResolvedTaskBinding.model_construct(observations=()),
    )


def _config_two_vehicles() -> Px4GazeboConfig:
    raw = _config().model_dump(mode="python")
    raw["vehicles"] = (
        *raw["vehicles"],
        VehicleSpec(
            vehicle_id="uav.2",
            system_id=2,
            mavsdk_udp_port=14541,
            px4_mavlink_udp_port=14581,
            mavsdk_grpc_port=15041,
            sys_autostart=4001,
            model="gz_x500_mono_cam",
            gazebo_model_name="x500_mono_cam_1",
            gazebo_resource="x500_mono_cam",
            initial_pose=Pose(
                x_m=2.0,
                y_m=0.0,
                z_m=0.1,
                roll_rad=0.0,
                pitch_rad=0.0,
                yaw_rad=0.0,
            ),
        ).model_dump(mode="python"),
    )
    return Px4GazeboConfig.model_validate(raw)


def _clock() -> ClockSpec:
    return ClockSpec(
        authority="provider_barrier",
        step_ns=10,
        max_steps=100,
        provider_timeout_ms=1_000,
    )


    return ClockSpec(
        authority="provider_barrier",
        step_ns=10,
        max_steps=100,
        provider_timeout_ms=1_000,
    )


def _state_event(
    target: SimulationTime, vehicle_id: str = "uav.1"
) -> ProviderEvent:
    return ProviderEvent(
        provider_id="flight",
        event_id=f"state.{vehicle_id}.{target.tick}",
        time=target,
        payload_schema_id=STATE_SCHEMA,
        payload=(
            NamedValue(name="vehicle_id", value=vehicle_id),
            NamedValue(
                name="pose_json",
                value='{"pitch_rad":0.0,"roll_rad":0.0,"x_m":0.0,"y_m":0.0,"yaw_rad":0.0,"z_m":0.1}',
            ),
            NamedValue(
                name="position_wgs84_json",
                value='{"altitude_m":100.0,"latitude_deg":37.0,"longitude_deg":-122.0}',
            ),
            NamedValue(
                name="velocity_json",
                value='{"down_m_s":0.0,"east_m_s":0.0,"north_m_s":0.0}',
            ),
            NamedValue(
                name="angular_velocity_json",
                value='{"x_rad_s":0.0,"y_rad_s":0.0,"z_rad_s":0.0}',
            ),
            NamedValue(
                name="attitude_json",
                value='{"pitch_rad":0.0,"roll_rad":0.0,"yaw_rad":0.0}',
            ),
            NamedValue(name="flight_mode", value="TAKEOFF"),
            NamedValue(name="armed", value=True),
            NamedValue(name="in_air", value=True),
            NamedValue(name="landed", value=False),
            NamedValue(name="landed_state", value="IN_AIR"),
            NamedValue(name="battery_percent", value=95.0),
            NamedValue(name="health", value='{"is_global_position_ok":true}'),
            NamedValue(name="contacts_json", value="[]"),
            NamedValue(name="ground_contact", value=False),
            NamedValue(name="collision_contact", value=False),
            NamedValue(name="evidence_path", value="trajectory/evidence.jsonl"),
            NamedValue(name="evidence_sha256", value="1" * 64),
            NamedValue(name="simulation_time_ns", value=target.sim_time_ns),
        ),
    )


def _command_event(
    command_id: str, tool_id: str, phase: str, target: SimulationTime
) -> ProviderEvent:
    return ProviderEvent(
        provider_id="flight",
        event_id=f"command.{command_id}.{phase}",
        time=target,
        payload_schema_id=COMMAND_SCHEMA,
        payload=(
            NamedValue(name="schema_id", value=COMMAND_SCHEMA),
            NamedValue(name="command_id", value=command_id),
            NamedValue(name="tool_id", value=tool_id),
            NamedValue(name="phase", value=phase),
        ),
    )


def _tool_command_audit_spec(tool_id: str) -> dict[str, object]:
    return dict(TOOL_COMMAND_AUDIT_SPEC[tool_id])


def _mavlink_x25_checksum(payload: bytes, *, crc_extra: int) -> int:
    checksum = 0xFFFF
    for value in (*payload, crc_extra):
        temporary = value ^ (checksum & 0xFF)
        temporary ^= (temporary << 4) & 0xFF
        checksum = (
            (checksum >> 8)
            ^ (temporary << 8)
            ^ (temporary << 3)
            ^ (temporary >> 4)
        ) & 0xFFFF
    return checksum


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
            MAVLINK_V2_MAGIC,
            len(payload),
            0,
            0,
            packet_sequence,
            source_system_id,
            source_component_id,
        )
    ) + message_id.to_bytes(3, "little")
    checksum = _mavlink_x25_checksum(
        header[1:] + payload,
        crc_extra=MAVLINK_CRC_EXTRAS[message_id],
    )
    frame = header + payload + checksum.to_bytes(2, "little")
    return {
        "canonical_frame_hex": frame.hex(),
        "canonical_frame_length": len(frame),
        "checksum": checksum,
        "compat_flags": 0,
        "frame_encoding": frame_encoding,
        "incompat_flags": 0,
        "magic": MAVLINK_V2_MAGIC,
        "message_id": message_id,
        "packet_sequence": packet_sequence,
        "payload_hex": payload.hex(),
        "payload_length": len(payload),
        "signature_hex": "",
        "signed_frame": False,
        "wire_version": 2,
    }


def _command_audit_send(
    tool_id: str,
    *,
    target_system_id: int = 1,
    target_component_id: int = 1,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    spec = _tool_command_audit_spec(tool_id)
    command = int(spec["mav_cmd"])
    wire_type = str(spec["wire_type"])
    payload_length = 33 if wire_type == "COMMAND_LONG" else 35
    message_id = (
        MAVLINK_COMMAND_LONG_MESSAGE_ID
        if wire_type == "COMMAND_LONG"
        else MAVLINK_COMMAND_INT_MESSAGE_ID
    )
    payload = bytearray(payload_length)
    payload[28:30] = command.to_bytes(2, "little")
    payload[30] = target_system_id
    payload[31] = target_component_id
    record: dict[str, object] = {
        "acceptance_boundary": COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY,
        "accepted_connection_count": 1,
        **_command_audit_frame_fields(
            message_id=message_id,
            payload=bytes(payload),
            source_system_id=MAVSDK_SERVER_SYSTEM_ID,
            source_component_id=MAVSDK_SERVER_COMPONENT_ID,
            packet_sequence=7,
            frame_encoding=COMMAND_TRANSPORT_FRAME_ENCODING,
        ),
        "command": command,
        "confirmation": 0,
        "kind": COMMAND_AUDIT_TRANSPORT_KIND,
        "seq": 1,
        "source_component_id": MAVSDK_SERVER_COMPONENT_ID,
        "source_system_id": MAVSDK_SERVER_SYSTEM_ID,
        "target_component_id": target_component_id,
        "target_system_id": target_system_id,
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
    target_system_id: int = MAVSDK_SERVER_SYSTEM_ID,
    target_component_id: int = MAVSDK_SERVER_COMPONENT_ID,
    vehicle_system_id: int = 1,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    send_record = _command_audit_send(
        tool_id,
        target_system_id=vehicle_system_id,
    )
    command = int(send_record["command"])
    source_system_id = int(send_record["target_system_id"])
    source_component_id = int(send_record["target_component_id"])
    payload = bytearray(10)
    payload[0:2] = command.to_bytes(2, "little")
    payload[2] = result
    payload[3] = progress
    payload[8] = target_system_id
    payload[9] = target_component_id
    record: dict[str, object] = {
        **_command_audit_frame_fields(
            message_id=MAVLINK_COMMAND_ACK_MESSAGE_ID,
            payload=bytes(payload),
            source_system_id=source_system_id,
            source_component_id=source_component_id,
            packet_sequence=8,
            frame_encoding=COMMAND_ACK_FRAME_ENCODING,
        ),
        "capture_boundary": COMMAND_ACK_CAPTURE_BOUNDARY,
        "command": command,
        "kind": COMMAND_AUDIT_ACK_INGRESS_KIND,
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
    target_system_id: int = MAVSDK_SERVER_SYSTEM_ID,
    target_component_id: int = MAVSDK_SERVER_COMPONENT_ID,
    vehicle_system_id: int = 1,
    overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    ingress = _command_audit_ack_ingress(
        tool_id,
        result=result,
        seq=seq,
        timestamp_ns=timestamp_ns,
        progress=progress,
        target_system_id=target_system_id,
        target_component_id=target_component_id,
        vehicle_system_id=vehicle_system_id,
        overrides=overrides,
    )
    return ingress, {
        "ack_ingress_seq": int(ingress["seq"]),
        "command": int(ingress["command"]),
        "kind": COMMAND_AUDIT_ACK_DISPOSITION_KIND,
        "seq": int(ingress["seq"]) + 1,
        "status": COMMAND_AUDIT_MATCHED_DISPOSITION,
        "timestamp_ns": int(ingress["timestamp_ns"]) + 1,
    }


def _decoded_command_audit(
    tool_id: str,
    *,
    records: list[dict[str, object]] | None = None,
    vehicle_system_id: int = 1,
) -> dict[str, object]:
    ordered_records = list(
        records
        or [
            _command_audit_send(tool_id, target_system_id=vehicle_system_id),
            *_command_audit_ack_records(
                tool_id,
                result=MAV_RESULT_ACCEPTED,
                seq=2,
                timestamp_ns=101,
                vehicle_system_id=vehicle_system_id,
            ),
        ]
    )
    audit = {
        "schema_version": COMMAND_AUDIT_SCHEMA_VERSION,
        "vehicle_system_id": vehicle_system_id,
        "expected_mav_cmd": int(_tool_command_audit_spec(tool_id)["mav_cmd"]),
        "expected_wire_type": str(_tool_command_audit_spec(tool_id)["wire_type"]),
        "records": ordered_records,
    }
    return _refresh_decoded_command_audit_digests(audit)


def _refresh_decoded_command_audit_digests(
    decoded_audit: dict[str, object],
) -> dict[str, object]:
    records = [dict(item) for item in decoded_audit["records"]]
    decoded_audit["records"] = records
    decoded_audit["record_sha256s"] = [_canonical_digest(item) for item in records]
    canonical_journal = b"".join(
        _canonical_json_bytes(item) + b"\n" for item in records
    )
    decoded_audit["journal_sha256"] = hashlib.sha256(canonical_journal).hexdigest()
    dispositions = [
        item
        for item in records
        if item.get("kind") == COMMAND_AUDIT_ACK_DISPOSITION_KIND
        and item.get("status") == COMMAND_AUDIT_MATCHED_DISPOSITION
    ]
    assert dispositions
    terminal_disposition = dispositions[-1]
    terminal_ingress = next(
        item
        for item in records
        if item.get("kind") == COMMAND_AUDIT_ACK_INGRESS_KIND
        and item.get("seq") == terminal_disposition["ack_ingress_seq"]
    )
    decoded_audit["terminal_ack_ingress_record"] = dict(terminal_ingress)
    decoded_audit["terminal_ack_ingress_record_sha256"] = _canonical_digest(
        terminal_ingress
    )
    decoded_audit["terminal_ack_disposition_record"] = dict(terminal_disposition)
    decoded_audit["terminal_ack_disposition_record_sha256"] = _canonical_digest(
        terminal_disposition
    )
    return decoded_audit


def _accepted_command_response(
    command_id: str = "command.takeoff",
) -> dict[str, object]:
    return {
        "receipts": [
            {
                "run_id": RUN_ID,
                "command_id": command_id,
                "provider_id": "flight",
                "phase": "received",
                "time": {"tick": 0, "sim_time_ns": 0},
            },
            {
                "run_id": RUN_ID,
                "command_id": command_id,
                "provider_id": "flight",
                "phase": "accepted",
                "time": {"tick": 0, "sim_time_ns": 0},
            },
        ],
        "events": [],
    }


def _proof_event(
    command_id: str,
    tool_id: str,
    phase: str,
    target: SimulationTime,
    *,
    proof_overrides: dict[str, object] | None = None,
    proof_json: str | None = None,
    vehicle_id: str = "uav.1",
) -> ProviderEvent:
    proof = _proof_payload(
        command_id,
        tool_id,
        phase,
        target,
        proof_overrides=proof_overrides,
        vehicle_id=vehicle_id,
    )
    return ProviderEvent(
        provider_id="flight",
        event_id=f"command.{command_id}.{phase}.physical",
        time=target,
        payload_schema_id=PROOF_SCHEMA,
        payload=(
            NamedValue(name="schema_id", value=PROOF_SCHEMA),
            NamedValue(name="command_id", value=command_id),
            NamedValue(name="tool_id", value=tool_id),
            NamedValue(name="phase", value=phase),
            NamedValue(
                name="proof_json",
                value=(
                    proof_json
                    if proof_json is not None
                    else json.dumps(proof, sort_keys=True, separators=(",", ":"))
                ),
            ),
        ),
    )


def _proof_payload(
    command_id: str,
    tool_id: str,
    phase: str,
    target: SimulationTime,
    *,
    proof_overrides: dict[str, object] | None = None,
    vehicle_id: str = "uav.1",
) -> dict[str, object]:
    default_applied_tick = 1 if phase == "completed" else None
    default_applied_sim_time_ns = 10 if phase == "completed" else None
    proof = {
        "command_id": command_id,
        "tool_id": tool_id,
        "vehicle_id": vehicle_id,
        "phase": phase,
        "policy_schema_version": "aero-bench.px4-physical-completion-policy/v2",
        "baseline_tick": 0,
        "baseline_sim_time_ns": 0,
        "baseline_state_digest": "1" * 64,
        "current_tick": target.tick,
        "current_sim_time_ns": target.sim_time_ns,
        "current_state_digest": "2" * 64,
        "accepted_tick": 0,
        "accepted_sim_time_ns": 0,
        "applied_tick": default_applied_tick if phase != "applied" else target.tick,
        "applied_sim_time_ns": (
            default_applied_sim_time_ns if phase != "applied" else target.sim_time_ns
        ),
        "ack_audit_status": (
            COMMAND_AUDIT_APPLIED_STATUS
            if phase in {"applied", "completed"}
            else COMMAND_AUDIT_MISSING_STATUS
        ),
        "decoded_command_audit": (
            _decoded_command_audit(tool_id)
            if phase in {"applied", "completed"}
            else None
        ),
        "thresholds": {"min_settle_samples": 2},
        "metrics": {"armed": True},
        "settling": {"sample_count": 0, "sample_duration_ns": 0},
    }
    if proof_overrides is not None:
        proof.update(proof_overrides)
    return proof


def _step_receipt(
    target: SimulationTime,
    *,
    extra_events: Sequence[ProviderEvent] = (),
    vehicle_ids: Sequence[str] = ("uav.1",),
) -> dict[str, object]:
    return StepReceipt(
        run_id=RUN_ID,
        provider_id="flight",
        reached=target,
        state_digest="d" * 64,
        events=tuple(_state_event(target, vehicle_id) for vehicle_id in vehicle_ids)
        + tuple(extra_events),
    ).model_dump(mode="json")


def _state_sample(target: SimulationTime, vehicle_id: str = "uav.1") -> StateSample:
    coordinate = ResolvedCoordinate(
        enu=ResolvedEnuPosition(east_m=0.0, north_m=0.0, up_m=0.1),
        ned=ResolvedNedPosition(north_m=0.0, east_m=0.0, down_m=-0.1),
        ecef=ResolvedEcefPosition(x_m=0.0, y_m=0.0, z_m=0.0),
        wgs84=ResolvedWgs84Position(
            longitude_deg=-122.0,
            latitude_deg=37.0,
            ellipsoid_height_m=100.0,
        ),
        geoid_separation_m=0.0,
        amsl_m=100.0,
        terrain_amsl_m=99.9,
        agl_m=0.1,
    )
    pose = ResolvedPose(
        position=coordinate,
        orientation_enu=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
        orientation_ned=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
    )
    attributes = (
        StateAttribute(name="collision_contact", value_type="bool", value=False),
        StateAttribute(name="contacts_complete", value_type="bool", value=True),
        StateAttribute(name="ground_contact", value_type="bool", value=False),
        StateAttribute(name="in_air", value_type="bool", value=True),
        StateAttribute(name="landed", value_type="bool", value=False),
        StateAttribute(name="landed_state", value_type="str", value="IN_AIR"),
        StateAttribute(
            name="telemetry_source", value_type="str", value="gazebo-mavsdk"
        ),
    )
    candidate = StateSample.model_construct(
        schema_version="aero-bench.state-sample/v1",
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        at=target,
        stage="motion",
        entity_id=vehicle_id,
        provider_id="flight",
        sample_kind="dynamic",
        pose=pose,
        linear_velocity_enu=EnuLinearVelocity(
            east_mps=0.0, north_mps=0.0, up_mps=0.0
        ),
        linear_velocity_ned=NedLinearVelocity(
            north_mps=0.0, east_mps=0.0, down_mps=0.0
        ),
        angular_velocity_body=BodyAngularVelocity(
            x_radps=0.0, y_radps=0.0, z_radps=0.0
        ),
        mode="TAKEOFF",
        armed=True,
        battery=BatteryState(remaining_fraction=0.95),
        health=HealthState(
            healthy=True,
            attributes=(
                StateAttribute(
                    name="is_global_position_ok", value_type="bool", value=True
                ),
            ),
        ),
        contacts=(),
        attributes=attributes,
        sample_digest="0" * 64,
    )
    return StateSample.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "sample_digest": state_sample_digest_value(candidate),
        }
    )


def _motion_stage_request(target: SimulationTime) -> MotionStageRequest:
    return MotionStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        provider_id="flight",
        target=target,
        stage="motion",
    )


async def _step_to(provider: Px4GazeboProvider, target: SimulationTime) -> StepReceipt:
    result = await provider.step_stage(_motion_stage_request(target))
    return result.step_receipt


def _command_request(
    command_id: str = "command.takeoff", vehicle_id: str = "uav.1"
) -> CommandRequest:
    return CommandRequest(
        run_id=RUN_ID,
        command_id=command_id,
        agent_id="agent.1",
        tool_id="flight.takeoff",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(
            NamedValue(name="vehicle_id", value=vehicle_id),
            NamedValue(name="altitude_m", value=3.0),
        ),
    )


def _provider(
    transport,
    *,
    config: Px4GazeboConfig | None = None,
    scenario: ResolvedScenario | None = None,
) -> Px4GazeboProvider:
    provider = Px4GazeboProvider(
        config=config or _config(),
        manifest=_manifest(),
        runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=17434),
        run_id=RUN_ID,
        session_token=SESSION_TOKEN,
        scenario=scenario or _scenario(),
        clock=_clock(),
    )
    provider._transport = transport
    return provider


def _strict_policy_payload(**overrides: object) -> dict[str, object]:
    raw = _config().physical_completion_policy.model_dump(mode="python")
    raw.update(overrides)
    return raw


class _Transport:
    def __init__(
        self,
        *,
        command_response: dict[str, object],
        step_receipts: list[dict[str, object]],
    ) -> None:
        self.command_response = command_response
        self.step_receipts = list(step_receipts)
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def request(self, operation, payload):
        self.requests.append((operation, dict(payload)))
        if operation == "prepare":
            config = _config()
            manifest = _manifest()
            return {
                "status": "ready",
                "provider_id": "flight",
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": manifest.runtime_image,
                "scenario_digest": SCENARIO_DIGEST,
                "px4_version": config.px4.version,
                "px4_commit": config.px4.commit,
                "gazebo_version": config.gazebo.version,
                "gazebo_commit": config.gazebo.commit,
                "mavsdk_version": config.mavsdk.version,
                "mavsdk_commit": config.mavsdk.commit,
            }
        if operation == "reset":
            return {"receipt": _step_receipt(SimulationTime(tick=0, sim_time_ns=0))}
        if operation == "command":
            return self.command_response
        if operation == "step_stage":
            receipt = self.step_receipts.pop(0)
            target = receipt["reached"]
            return {
                "schema_version": "aero-bench.px4-motion-stage-response/v1",
                "run_id": RUN_ID,
                "scenario_digest": SCENARIO_DIGEST,
                "provider_id": "flight",
                "target": target,
                "stage": "motion",
                "receipt": receipt,
                "samples": [
                    _state_sample(
                        SimulationTime.model_validate(target), vehicle_id
                    ).model_dump(mode="json")
                    for vehicle_id in (
                        event["payload"][0]["value"]
                        for event in receipt["events"]
                        if event["payload_schema_id"] == STATE_SCHEMA
                    )
                ],
            }
        if operation == "finalize":
            request = payload["request"]
            return {
                "receipt": {
                    "schema_version": "aero-bench.provider-finalization-receipt/v1",
                    "run_id": RUN_ID,
                    "provider_id": "flight",
                    "event_chain_root": request["event_chain_root"],
                    "artifacts": [
                        {
                            "artifact_id": _artifact_requirement().artifact_id,
                            "sha256": "e" * 64,
                            "size_bytes": 128,
                        }
                    ],
                }
            }
        if operation == "shutdown":
            return {"status": "stopped"}
        raise AssertionError(operation)

    async def close(self) -> None:
        return None


class _TwoVehicleTransport(_Transport):
    async def request(self, operation, payload):
        if operation == "reset":
            return {
                "receipt": _step_receipt(
                    SimulationTime(tick=0, sim_time_ns=0),
                    vehicle_ids=("uav.1", "uav.2"),
                )
            }
        if operation == "command":
            request = payload["request"]
            return _accepted_command_response(request["command_id"])
        return await super().request(operation, payload)



def test_provider_routes_simultaneous_deferred_commands_by_vehicle() -> None:
    async def run() -> None:
        target1 = SimulationTime(tick=1, sim_time_ns=10)
        target2 = SimulationTime(tick=2, sim_time_ns=20)
        first_receipt = _step_receipt(
            target1,
            vehicle_ids=("uav.1", "uav.2"),
            extra_events=(
                _command_event(
                    "command.one", "flight.takeoff", "applied", target1
                ),
                _proof_event(
                    "command.one",
                    "flight.takeoff",
                    "applied",
                    target1,
                    vehicle_id="uav.1",
                    proof_overrides={
                        "decoded_command_audit": _decoded_command_audit(
                            "flight.takeoff", vehicle_system_id=1
                        )
                    },
                ),
                _command_event(
                    "command.two", "flight.takeoff", "applied", target1
                ),
                _proof_event(
                    "command.two",
                    "flight.takeoff",
                    "applied",
                    target1,
                    vehicle_id="uav.2",
                    proof_overrides={
                        "decoded_command_audit": _decoded_command_audit(
                            "flight.takeoff", vehicle_system_id=2
                        )
                    },
                ),
            ),
        )
        second_receipt = _step_receipt(
            target2,
            vehicle_ids=("uav.1", "uav.2"),
            extra_events=(
                _command_event(
                    "command.one", "flight.takeoff", "completed", target2
                ),
                _proof_event(
                    "command.one",
                    "flight.takeoff",
                    "completed",
                    target2,
                    vehicle_id="uav.1",
                    proof_overrides={
                        "decoded_command_audit": _decoded_command_audit(
                            "flight.takeoff", vehicle_system_id=1
                        )
                    },
                ),
                _command_event(
                    "command.two", "flight.takeoff", "completed", target2
                ),
                _proof_event(
                    "command.two",
                    "flight.takeoff",
                    "completed",
                    target2,
                    vehicle_id="uav.2",
                    proof_overrides={
                        "decoded_command_audit": _decoded_command_audit(
                            "flight.takeoff", vehicle_system_id=2
                        )
                    },
                ),
            ),
        )
        transport = _TwoVehicleTransport(
            command_response={},
            step_receipts=[first_receipt, second_receipt],
        )
        provider = _provider(
            transport,
            config=_config_two_vehicles(),
            scenario=_scenario_two_vehicles(),
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(
            _command_request("command.one", vehicle_id="uav.1")
        )
        await provider.handle_command(
            _command_request("command.two", vehicle_id="uav.2")
        )
        assert set(provider._pending_commands_by_vehicle) == {"uav.1", "uav.2"}

        first = await _step_to(provider, target1)
        assert [
            event.event_id for event in first.events if "physical" in event.event_id
        ] == [
            "command.command.one.applied.physical",
            "command.command.two.applied.physical",
        ]
        assert {
            vehicle_id: state.stage
            for vehicle_id, state in provider._pending_commands_by_vehicle.items()
        } == {"uav.1": "applied", "uav.2": "applied"}

        await _step_to(provider, target2)
        assert provider._pending_commands_by_vehicle == {}
        assert provider._failure_latched_by_vehicle == set()
        assert provider._consumed_terminal_ack_seq_by_vehicle == {
            "uav.1": 2,
            "uav.2": 2,
        }

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("physical_sim_timeout_ns", "50"),
        ("min_settle_samples", "2"),
        ("settle_duration_ns", "20"),
        ("takeoff_altitude_tolerance_m", 1),
        ("goto_horizontal_tolerance_m", "1.0"),
        ("goto_vertical_tolerance_m", 1),
        ("goto_minimum_progress_m", "5.0"),
        ("hold_drift_radius_m", 1),
        ("max_horizontal_settled_speed_m_s", "0.4"),
        ("max_vertical_settled_speed_m_s", 1),
        ("landing_max_speed_m_s", "0.35"),
        ("landing_max_height_proxy_m", 1),
        ("disarm_requires_contact", 1),
        ("arm_allowed_modes", [1]),
        ("disarm_allowed_modes", [1]),
        ("takeoff_allowed_modes", [1]),
        ("goto_allowed_modes", [1]),
        ("hold_allowed_modes", [1]),
        ("land_allowed_modes", [1]),
    ),
)
def test_physical_completion_policy_rejects_v2_field_coercions(
    field: str, bad_value: object
) -> None:
    with pytest.raises(ValidationError):
        PhysicalCompletionPolicy.model_validate(
            _strict_policy_payload(**{field: bad_value})
        )


def test_physical_completion_policy_rejects_lowercase_mode_identifiers() -> None:
    with pytest.raises(ValidationError):
        PhysicalCompletionPolicy.model_validate(
            _strict_policy_payload(arm_allowed_modes=("ready",))
        )


def test_px4_provider_rejects_physical_timeout_at_exact_completion_boundary() -> None:
    raw = _config().model_dump(mode="python")
    raw["physical_completion_policy"]["physical_sim_timeout_ns"] = 40
    config = Px4GazeboConfig.model_validate(raw)
    with pytest.raises(
        ValueError, match="earliest possible physical completion|completion barrier"
    ):
        _provider(object(), config=config)


def test_px4_provider_accepts_physical_timeout_past_exact_completion_boundary() -> None:
    raw = _config().model_dump(mode="python")
    raw["physical_completion_policy"]["physical_sim_timeout_ns"] = 41
    config = Px4GazeboConfig.model_validate(raw)
    provider = _provider(object(), config=config)
    assert provider._config.physical_completion_policy.physical_sim_timeout_ns == 41


@pytest.mark.parametrize(
    "phases",
    (
        ("received", "accepted", "applied"),
        ("received", "accepted", "completed"),
    ),
)
def test_provider_rejects_immediate_non_staging_command_phases(
    phases: tuple[str, ...],
) -> None:
    async def run() -> None:
        transport = _Transport(
            command_response={
                "receipts": [
                    {
                        "run_id": RUN_ID,
                        "command_id": "command.takeoff",
                        "provider_id": "flight",
                        "phase": phase,
                        "time": {"tick": 0, "sim_time_ns": 0},
                    }
                    for phase in phases
                ],
                "events": [],
            },
            step_receipts=[],
        )
        provider = _provider(transport)
        await provider.prepare()
        await provider.reset(seed=7)
        with pytest.raises(Px4ProviderError, match="staging-only"):
            await provider.handle_command(_command_request())

    asyncio.run(run())


def test_provider_tracks_one_unresolved_command_and_requires_deferred_order() -> None:
    async def run() -> None:
        target1 = SimulationTime(tick=1, sim_time_ns=10)
        target2 = SimulationTime(tick=2, sim_time_ns=20)
        retry_records = [
            _command_audit_send("flight.takeoff"),
            *_command_audit_ack_records(
                "flight.takeoff",
                result=MAV_RESULT_IN_PROGRESS,
                seq=2,
                timestamp_ns=101,
                progress=7,
            ),
            _command_audit_send(
                "flight.takeoff",
                overrides={"seq": 4, "timestamp_ns": 103},
            ),
            *_command_audit_ack_records(
                "flight.takeoff",
                result=MAV_RESULT_ACCEPTED,
                seq=5,
                timestamp_ns=104,
            ),
        ]
        transport = _Transport(
            command_response=_accepted_command_response(),
            step_receipts=[
                _step_receipt(
                    target1,
                    extra_events=(
                        _command_event(
                            "command.takeoff", "flight.takeoff", "applied", target1
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            target1,
                            proof_overrides={
                                "decoded_command_audit": _decoded_command_audit(
                                    "flight.takeoff",
                                    records=retry_records,
                                )
                            },
                        ),
                    ),
                ),
                _step_receipt(
                    target2,
                    extra_events=(
                        _command_event(
                            "command.takeoff", "flight.takeoff", "completed", target2
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "completed",
                            target2,
                            proof_overrides={
                                "decoded_command_audit": _decoded_command_audit(
                                    "flight.takeoff",
                                    records=retry_records,
                                )
                            },
                        ),
                    ),
                ),
            ],
        )
        provider = _provider(transport)
        await provider.prepare()
        await provider.reset(seed=7)
        result = await provider.handle_command(_command_request())
        assert [receipt.phase for receipt in result.receipts] == [
            "received",
            "accepted",
        ]
        with pytest.raises(Px4ProviderError, match="unresolved deferred command"):
            await provider.handle_command(_command_request(command_id="command.other"))
        with pytest.raises(Px4ProviderError, match="unresolved command"):
            await provider.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=RUN_ID,
                    terminal_event="run.completed",
                    terminal_time=SimulationTime(tick=0, sim_time_ns=0),
                    event_chain_root="d" * 64,
                )
            )
        first = await _step_to(provider, target1)
        assert first.reached == target1
        second = await _step_to(provider, target2)
        assert second.reached == target2
        receipt = await provider.finalize(
            ProviderFinalizationRequest(
                schema_version="aero-bench.provider-finalization-request/v1",
                run_id=RUN_ID,
                terminal_event="run.completed",
                terminal_time=target2,
                event_chain_root="d" * 64,
            )
        )
        assert receipt.event_chain_root == "d" * 64

    asyncio.run(run())


@pytest.mark.parametrize(
    ("step_receipts", "terminal_tick"),
    (
        (
            [
                _step_receipt(
                    SimulationTime(tick=1, sim_time_ns=10),
                    extra_events=(
                        _command_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "failed",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "failed",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                    ),
                )
            ],
            SimulationTime(tick=1, sim_time_ns=10),
        ),
        (
            [
                _step_receipt(
                    SimulationTime(tick=1, sim_time_ns=10),
                    extra_events=(
                        _command_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                    ),
                ),
                _step_receipt(SimulationTime(tick=2, sim_time_ns=20)),
                _step_receipt(
                    SimulationTime(tick=3, sim_time_ns=30),
                    extra_events=(
                        _command_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "failed",
                            SimulationTime(tick=3, sim_time_ns=30),
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "failed",
                            SimulationTime(tick=3, sim_time_ns=30),
                            proof_overrides={
                                "applied_tick": 1,
                                "applied_sim_time_ns": 10,
                                "ack_audit_status": COMMAND_AUDIT_APPLIED_STATUS,
                                "decoded_command_audit": _decoded_command_audit(
                                    "flight.takeoff"
                                ),
                                "current_tick": 3,
                                "current_sim_time_ns": 30,
                            },
                        ),
                    ),
                ),
                _step_receipt(SimulationTime(tick=4, sim_time_ns=40)),
            ],
            SimulationTime(tick=4, sim_time_ns=40),
        ),
    ),
)
def test_provider_latches_deferred_failure_until_successful_reset(
    step_receipts: list[dict[str, object]],
    terminal_tick: SimulationTime,
) -> None:
    async def run() -> None:
        provider = _provider(
            _Transport(
                command_response={
                    "receipts": [
                        {
                            "run_id": RUN_ID,
                            "command_id": "command.takeoff",
                            "provider_id": "flight",
                            "phase": "received",
                            "time": {"tick": 0, "sim_time_ns": 0},
                        },
                        {
                            "run_id": RUN_ID,
                            "command_id": "command.takeoff",
                            "provider_id": "flight",
                            "phase": "accepted",
                            "time": {"tick": 0, "sim_time_ns": 0},
                        },
                    ],
                    "events": [],
                },
                step_receipts=step_receipts,
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        for tick in range(1, terminal_tick.tick + 1):
            await _step_to(
                provider,
                SimulationTime(tick=tick, sim_time_ns=tick * 10),
            )
        with pytest.raises(Px4ProviderError, match="failure-latched"):
            await provider.handle_command(_command_request(command_id="command.after"))
        with pytest.raises(Px4ProviderError, match="failure-latched"):
            await provider.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=RUN_ID,
                    terminal_event="run.completed",
                    terminal_time=terminal_tick,
                    event_chain_root="d" * 64,
                )
            )
        await provider.reset(seed=7)
        result = await provider.handle_command(_command_request())
        assert [receipt.phase for receipt in result.receipts] == [
            "received",
            "accepted",
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("proof_json", "message"),
    (
        (
            json.dumps(
                _proof_payload(
                    "command.takeoff",
                    "flight.takeoff",
                    "failed",
                    SimulationTime(tick=1, sim_time_ns=10),
                    proof_overrides={"extra_field": 1},
                ),
                sort_keys=True,
                separators=(",", ":"),
            ),
            "fields are not exact",
        ),
        (
            json.dumps(
                _proof_payload(
                    "command.takeoff",
                    "flight.takeoff",
                    "failed",
                    SimulationTime(tick=1, sim_time_ns=10),
                ),
                separators=(",", ":"),
            ),
            "canonical JSON",
        ),
        (
            json.dumps(
                _proof_payload(
                    "command.takeoff",
                    "flight.takeoff",
                    "failed",
                    SimulationTime(tick=1, sim_time_ns=10),
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).replace(
                '"command_id":"command.takeoff"',
                '"command_id":"command.takeoff","command_id":"command.takeoff"',
                1,
            ),
            "invalid",
        ),
    ),
)
def test_provider_rejects_strict_proof_json_before_mutating_lifecycle(
    proof_json: str,
    message: str,
) -> None:
    async def run() -> None:
        target = SimulationTime(tick=1, sim_time_ns=10)
        provider = _provider(
            _Transport(
                command_response=_accepted_command_response(),
                step_receipts=[
                    _step_receipt(
                        target,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "failed", target
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "failed",
                                target,
                                proof_json=proof_json,
                            ),
                        ),
                    )
                ],
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        with pytest.raises(Px4ProviderError, match=message):
            await _step_to(provider, target)
        assert provider._unresolved_command_stage == "accepted"
        assert provider._unresolved_command_audit_digest is None
        assert provider._consumed_terminal_ack_seq_by_vehicle == {}

    asyncio.run(run())


@pytest.mark.parametrize(
    "decoded_audit",
    (
        _refresh_decoded_command_audit_digests(
            _decoded_command_audit(
                "flight.takeoff",
                records=[
                    _command_audit_send(
                        "flight.takeoff",
                        overrides={"source_system_id": 244},
                    ),
                    *_command_audit_ack_records(
                        "flight.takeoff",
                        result=MAV_RESULT_ACCEPTED,
                        seq=2,
                        timestamp_ns=101,
                        overrides={"target_system_id": 244},
                    ),
                ],
            )
        ),
        _refresh_decoded_command_audit_digests(
            _decoded_command_audit(
                "flight.takeoff",
                records=[
                    _command_audit_send(
                        "flight.takeoff",
                        overrides={"confirmation": 256},
                    ),
                    *_command_audit_ack_records(
                        "flight.takeoff",
                        result=MAV_RESULT_ACCEPTED,
                        seq=2,
                        timestamp_ns=101,
                    ),
                ],
            )
        ),
        _refresh_decoded_command_audit_digests(
            _decoded_command_audit(
                "flight.takeoff",
                records=[
                    _command_audit_send("flight.takeoff"),
                    *_command_audit_ack_records(
                        "flight.takeoff",
                        result=MAV_RESULT_ACCEPTED,
                        seq=2,
                        timestamp_ns=101,
                        overrides={"progress": 256},
                    ),
                ],
            )
        ),
        _refresh_decoded_command_audit_digests(
            _decoded_command_audit(
                "flight.takeoff",
                records=[
                    _command_audit_send("flight.takeoff"),
                    *_command_audit_ack_records(
                        "flight.takeoff",
                        result=MAV_RESULT_ACCEPTED,
                        seq=2,
                        timestamp_ns=101,
                        overrides={"result_param2": 2**31},
                    ),
                ],
            )
        ),
        _decoded_command_audit(
            "flight.takeoff",
            records=[
                _command_audit_send("flight.takeoff"),
                *_command_audit_ack_records(
                    "flight.takeoff",
                    result=MAV_RESULT_ACCEPTED,
                    seq=2,
                    timestamp_ns=101,
                ),
                _command_audit_send(
                    "flight.takeoff",
                    overrides={"seq": 4, "timestamp_ns": 103},
                ),
            ],
        ),
    ),
)
def test_provider_rejects_invalid_decoded_command_audit_fields_before_mutating_lifecycle(
    decoded_audit: dict[str, object],
) -> None:
    async def run() -> None:
        target = SimulationTime(tick=1, sim_time_ns=10)
        provider = _provider(
            _Transport(
                command_response=_accepted_command_response(),
                step_receipts=[
                    _step_receipt(
                        target,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "applied", target
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "applied",
                                target,
                                proof_overrides={
                                    "decoded_command_audit": decoded_audit
                                },
                            ),
                        ),
                    )
                ],
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        with pytest.raises(Px4ProviderError):
            await _step_to(provider, target)
        assert provider._unresolved_command_stage == "accepted"
        assert provider._unresolved_command_audit_digest is None
        assert provider._consumed_terminal_ack_seq_by_vehicle == {}

    asyncio.run(run())


def test_provider_accepts_mavlink_ack_target_wildcards() -> None:
    async def run() -> None:
        target = SimulationTime(tick=1, sim_time_ns=10)
        wildcard_bundle = _decoded_command_audit(
            "flight.takeoff",
            records=[
                _command_audit_send("flight.takeoff"),
                *_command_audit_ack_records(
                    "flight.takeoff",
                    result=MAV_RESULT_ACCEPTED,
                    seq=2,
                    timestamp_ns=101,
                    target_system_id=0,
                    target_component_id=0,
                ),
            ],
        )
        provider = _provider(
            _Transport(
                command_response=_accepted_command_response(),
                step_receipts=[
                    _step_receipt(
                        target,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "applied", target
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "applied",
                                target,
                                proof_overrides={
                                    "decoded_command_audit": wildcard_bundle
                                },
                            ),
                        ),
                    )
                ],
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())

        receipt = await _step_to(provider, target)

        assert receipt.reached == target
        assert provider._unresolved_command_stage == "applied"
        assert provider._consumed_terminal_ack_seq_by_vehicle == {"uav.1": 2}

    asyncio.run(run())


def test_provider_rejects_decoded_command_audit_bundle_substitution_across_phases() -> None:
    async def run() -> None:
        target1 = SimulationTime(tick=1, sim_time_ns=10)
        target2 = SimulationTime(tick=2, sim_time_ns=20)
        first_bundle = _decoded_command_audit("flight.takeoff")
        substituted_bundle = _decoded_command_audit(
            "flight.takeoff",
            records=[
                _command_audit_send(
                    "flight.takeoff",
                    overrides={"seq": 4, "timestamp_ns": 103},
                ),
                *_command_audit_ack_records(
                    "flight.takeoff",
                    result=MAV_RESULT_ACCEPTED,
                    seq=5,
                    timestamp_ns=104,
                ),
            ],
        )
        provider = _provider(
            _Transport(
                command_response=_accepted_command_response(),
                step_receipts=[
                    _step_receipt(
                        target1,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "applied", target1
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "applied",
                                target1,
                                proof_overrides={"decoded_command_audit": first_bundle},
                            ),
                        ),
                    ),
                    _step_receipt(
                        target2,
                        extra_events=(
                            _command_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "completed",
                                target2,
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "completed",
                                target2,
                                proof_overrides={
                                    "decoded_command_audit": substituted_bundle
                                },
                            ),
                        ),
                    ),
                ],
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        await _step_to(provider, target1)
        assert provider._unresolved_command_stage == "applied"
        assert provider._unresolved_command_audit_digest == _canonical_digest(
            first_bundle
        )
        with pytest.raises(Px4ProviderError, match="changed across deferred phases"):
            await _step_to(provider, target2)
        assert provider._unresolved_command_stage == "applied"
        assert provider._unresolved_command_audit_digest == _canonical_digest(
            first_bundle
        )

    asyncio.run(run())


def test_provider_rejects_replayed_terminal_ack_until_successful_reset() -> None:
    async def run() -> None:
        target1 = SimulationTime(tick=1, sim_time_ns=10)
        target2 = SimulationTime(tick=2, sim_time_ns=20)
        target3 = SimulationTime(tick=3, sim_time_ns=30)
        replay_bundle = _decoded_command_audit("flight.takeoff")
        provider = _provider(
            _Transport(
                command_response=_accepted_command_response(),
                step_receipts=[
                    _step_receipt(
                        target1,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "applied", target1
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "applied",
                                target1,
                                proof_overrides={"decoded_command_audit": replay_bundle},
                            ),
                        ),
                    ),
                    _step_receipt(
                        target2,
                        extra_events=(
                            _command_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "completed",
                                target2,
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "completed",
                                target2,
                                proof_overrides={"decoded_command_audit": replay_bundle},
                            ),
                        ),
                    ),
                    _step_receipt(
                        target3,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "applied", target3
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "applied",
                                target3,
                                proof_overrides={
                                    "baseline_tick": 2,
                                    "baseline_sim_time_ns": 20,
                                    "accepted_tick": 2,
                                    "accepted_sim_time_ns": 20,
                                    "decoded_command_audit": replay_bundle,
                                },
                            ),
                        ),
                    ),
                    _step_receipt(
                        target1,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "applied", target1
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "applied",
                                target1,
                                proof_overrides={"decoded_command_audit": replay_bundle},
                            ),
                        ),
                    ),
                ],
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        await _step_to(provider, target1)
        await _step_to(provider, target2)
        await provider.handle_command(_command_request())
        with pytest.raises(Px4ProviderError, match="already consumed"):
            await _step_to(provider, target3)
        assert provider._unresolved_command_stage == "accepted"
        assert provider._consumed_terminal_ack_seq_by_vehicle == {"uav.1": 2}

        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        receipt = await _step_to(provider, target1)
        assert receipt.reached == target1
        assert provider._unresolved_command_stage == "applied"
        assert provider._consumed_terminal_ack_seq_by_vehicle == {"uav.1": 2}

    asyncio.run(run())


@pytest.mark.parametrize(
    "proof_overrides",
    (
        {"vehicle_id": "uav.2"},
        {"ack_audit_status": "not_yet_decoded_audited"},
        {"baseline_state_digest": "0" * 64},
        {"current_tick": 2},
        {"policy_schema_version": "aero-bench.px4-physical-completion-policy/v1"},
        {
            "ack_audit_status": COMMAND_AUDIT_APPLIED_STATUS,
            "decoded_command_audit": None,
        },
    ),
)
def test_provider_rejects_invalid_deferred_proof_before_mutating_lifecycle(
    proof_overrides: dict[str, object],
) -> None:
    async def run() -> None:
        target = SimulationTime(tick=1, sim_time_ns=10)
        provider = _provider(
            _Transport(
                command_response={
                    "receipts": [
                        {
                            "run_id": RUN_ID,
                            "command_id": "command.takeoff",
                            "provider_id": "flight",
                            "phase": "received",
                            "time": {"tick": 0, "sim_time_ns": 0},
                        },
                        {
                            "run_id": RUN_ID,
                            "command_id": "command.takeoff",
                            "provider_id": "flight",
                            "phase": "accepted",
                            "time": {"tick": 0, "sim_time_ns": 0},
                        },
                    ],
                    "events": [],
                },
                step_receipts=[
                    _step_receipt(
                        target,
                        extra_events=(
                            _command_event(
                                "command.takeoff", "flight.takeoff", "failed", target
                            ),
                            _proof_event(
                                "command.takeoff",
                                "flight.takeoff",
                                "failed",
                                target,
                                proof_overrides=proof_overrides,
                            ),
                        ),
                    )
                ],
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        with pytest.raises(Px4ProviderError):
            await _step_to(provider, target)
        with pytest.raises(Px4ProviderError, match="unresolved deferred command"):
            await provider.handle_command(_command_request(command_id="command.after"))
        with pytest.raises(Px4ProviderError, match="unresolved command"):
            await provider.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=RUN_ID,
                    terminal_event="run.completed",
                    terminal_time=SimulationTime(tick=0, sim_time_ns=0),
                    event_chain_root="d" * 64,
                )
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    ("step_receipts", "message"),
    (
        (
            [
                _step_receipt(
                    SimulationTime(tick=1, sim_time_ns=10),
                    extra_events=(
                        _command_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "completed",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "completed",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                    ),
                )
            ],
            "accepted to applied",
        ),
        (
            [
                _step_receipt(
                    SimulationTime(tick=1, sim_time_ns=10),
                    extra_events=(
                        _command_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                    ),
                ),
                _step_receipt(
                    SimulationTime(tick=2, sim_time_ns=20),
                    extra_events=(
                        _command_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=2, sim_time_ns=20),
                        ),
                        _proof_event(
                            "command.takeoff",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=2, sim_time_ns=20),
                        ),
                    ),
                ),
            ],
            "regress or re-apply",
        ),
        (
            [
                _step_receipt(
                    SimulationTime(tick=1, sim_time_ns=10),
                    extra_events=(
                        _command_event(
                            "command.other",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                        _proof_event(
                            "command.other",
                            "flight.takeoff",
                            "applied",
                            SimulationTime(tick=1, sim_time_ns=10),
                        ),
                    ),
                )
            ],
            "unknown unresolved command",
        ),
    ),
)
def test_provider_rejects_bad_deferred_command_sequences(
    step_receipts: list[dict[str, object]], message: str
) -> None:
    async def run() -> None:
        provider = _provider(
            _Transport(
                command_response={
                    "receipts": [
                        {
                            "run_id": RUN_ID,
                            "command_id": "command.takeoff",
                            "provider_id": "flight",
                            "phase": "received",
                            "time": {"tick": 0, "sim_time_ns": 0},
                        },
                        {
                            "run_id": RUN_ID,
                            "command_id": "command.takeoff",
                            "provider_id": "flight",
                            "phase": "accepted",
                            "time": {"tick": 0, "sim_time_ns": 0},
                        },
                    ],
                    "events": [],
                },
                step_receipts=step_receipts,
            )
        )
        await provider.prepare()
        await provider.reset(seed=7)
        await provider.handle_command(_command_request())
        await _step_to(
            provider, SimulationTime(tick=1, sim_time_ns=10)
        )
        if len(step_receipts) > 1:
            with pytest.raises(Px4ProviderError, match=message):
                await _step_to(
                    provider, SimulationTime(tick=2, sim_time_ns=20)
                )

    if len(step_receipts) == 1:

        async def first_failure() -> None:
            provider = _provider(
                _Transport(
                    command_response={
                        "receipts": [
                            {
                                "run_id": RUN_ID,
                                "command_id": "command.takeoff",
                                "provider_id": "flight",
                                "phase": "received",
                                "time": {"tick": 0, "sim_time_ns": 0},
                            },
                            {
                                "run_id": RUN_ID,
                                "command_id": "command.takeoff",
                                "provider_id": "flight",
                                "phase": "accepted",
                                "time": {"tick": 0, "sim_time_ns": 0},
                            },
                        ],
                        "events": [],
                    },
                    step_receipts=step_receipts,
                )
            )
            await provider.prepare()
            await provider.reset(seed=7)
            await provider.handle_command(_command_request())
            with pytest.raises(Px4ProviderError, match=message):
                await _step_to(
                    provider, SimulationTime(tick=1, sim_time_ns=10)
                )

        asyncio.run(first_failure())
        return
    asyncio.run(run())
