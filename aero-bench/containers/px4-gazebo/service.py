from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import signal
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from xml.etree import ElementTree

import aero_frame_math as frame_math
from rpc import JsonLineRpcServer
from workload_scenario import (
    ValidatedWorkloadScenario,
    WorkloadScenarioError,
    validate_workload_scenario,
)


PX4_VERSION = "v1.17.0-alpha1-1551-g381149fb01"
PX4_COMMIT = "381149fb012762f5e38c4a7fdc1b905b28038970"
PX4_SHA256 = "b6d41b7e7d8be65017a1dc4cfe12502382e8595d2408c4c27d28c64a7cff66f6"
GAZEBO_VERSION = "8.11.0"
GAZEBO_COMMIT = "1be3cc376fec778cc725b4eeea463245affa56d3"
GAZEBO_SHA256 = "6e9cccdef9f9f266f45b76559c0974b1970e4348e82feb7a51964b4854cdd22a"
MAVSDK_VERSION = "3.17.2"
MAVSDK_COMMIT = "9e3ca17faa84aa868caea10a3bbdab7e53810ced"
MAVSDK_SOURCE_SHA256 = (
    "709de37d225f6374b3ebe65af8e34921759b7a8c2bbe611a581fd614126293c0"
)
MAVSDK_PATCH_SHA256 = "972bef4a0d23a72a1fa46c3d9fa808e4f2f73467da32692b1c5b5d681e3bfed5"
MAVLINK_COMMIT = "d6a7eeaf43319ce6da19a1973ca40180a4210643"
MAVLINK_SOURCE_SHA256 = (
    "1e82b295bfd4fb9c3ce4d7f503e445c2050ea6d254dfd8addf56b7f2fa8b984d"
)
MAVLINK_OFFLINE_PATCH_SHA256 = (
    "777a0f83ab88ab9eff71a51daeddc7e88efcf58c23b1c73c5477c788ce82734a"
)
PYMAVLINK_COMMIT = "5977f44504f01715a44bed44a4601e6c12cef92d"
PYMAVLINK_SOURCE_SHA256 = (
    "e0949dd5858ecce28c52f5b30a7957f2236482417798867f5e961d7a2abd1fc8"
)
PYMAVLINK_BUILD_LOCK_SHA256 = (
    "4c51de8177452454bc62e795ee802c521e442b1b2a8429dc01cde45675546795"
)
MAVSDK_SERVER_SHA256 = (
    "6f7978e40127849d925cfc5b3181b4fd3e3bb2b3701d5fb591e92d8710e1deee"
)
MONO_CAM_MODEL_SHA256 = (
    "2d8b948585fc615cb0f34ed9d67885c15a9bac16df2d1fc59ebb03325c0c26cf"
)
PROTOCOL_VERSION = "aero-bench.px4-gazebo-rpc/v4"
BUNDLE_WORLD_SCHEMA = "aero-bench.px4-gazebo/bundle-world/v1"
BUNDLE_WORLD_PATH = re.compile(
    r"^bundle/(?:[a-z0-9][a-z0-9_.-]*/)*[a-z0-9][a-z0-9_.-]*\.sdf$"
)
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
PROVIDER_ADAPTER = "px4.gazebo"
STATE_SCHEMA = "px4.state.v1"
COMMAND_SCHEMA = "px4.command.v2"
PHYSICAL_COMMAND_PROOF_SCHEMA = "px4.command.physical.v3"
EVIDENCE_SCHEMA = "aero-bench.px4-evidence/v1"
EVIDENCE_ARTIFACT_TYPE = "trajectory"
OBSERVATION_ARTIFACT_TYPE = "observation"
SENSOR_FRAME_ARTIFACT_TYPE = "sensor-frame"
CAMERA_FRAME_DATA_ARTIFACT_TYPE = "camera-frame-data"
PUBLIC_SENSOR_FRAME_EVENT_TYPE = "public.sensor-frame"
PUBLIC_SENSOR_FRAME_SCHEMA = "sensor.frame_ref.public.v2"
PUBLIC_SENSOR_FRAME_ARTIFACT_SCHEMA = "aero-bench.public-sensor-frame-artifact/v2"
INSPECTION_OBSERVATION_METADATA_SCHEMA = (
    "aero-bench.inspection-observation-metadata/v1"
)
RGB_OBSERVATION_SCHEMA = "aero-bench.observation.rgb/v1"
URBAN_TELEMETRY_OBSERVATION_SCHEMA = "aero-bench.observation.urban-telemetry.v1"
URBAN_SAFETY_OBSERVATION_SCHEMA = "aero-bench.observation.urban-safety.v1"
FLIGHT_TELEMETRY_OBSERVATION_SCHEMA = (
    "aero-bench.observation.flight-telemetry/v1"
)
FLIGHT_GNSS_OBSERVATION_SCHEMA = "aero-bench.observation.flight-gnss/v1"
CAMERA_TRIGGER_TOPIC = "/aero-bench/camera/trigger"
CONTACT_SENSOR_BINDINGS = (
    ("base_link", "base_link_collision_0"),
    ("base_link", "base_link_collision_1"),
    ("base_link", "base_link_collision_2"),
    ("base_link", "base_link_collision_3"),
    ("base_link", "base_link_collision_4"),
    ("rotor_0", "rotor_0_collision"),
    ("rotor_1", "rotor_1_collision"),
    ("rotor_2", "rotor_2_collision"),
    ("rotor_3", "rotor_3_collision"),
)
# Gazebo skips GZ_SIM_SERVER_CONFIG_PATH once an SDF declares a world plugin.
# Runtime preparation adds AirspaceTransition, so retain the core system set
# from the pinned PX4 image's server.config in the prepared world itself.
GAZEBO_SERVER_SYSTEM_PLUGINS = (
    ("gz-sim-physics-system", "gz::sim::systems::Physics"),
    ("gz-sim-user-commands-system", "gz::sim::systems::UserCommands"),
    ("gz-sim-scene-broadcaster-system", "gz::sim::systems::SceneBroadcaster"),
    ("gz-sim-contact-system", "gz::sim::systems::Contact"),
    ("gz-sim-imu-system", "gz::sim::systems::Imu"),
    ("gz-sim-air-pressure-system", "gz::sim::systems::AirPressure"),
    ("gz-sim-air-speed-system", "gz::sim::systems::AirSpeed"),
    ("gz-sim-apply-link-wrench-system", "gz::sim::systems::ApplyLinkWrench"),
    ("gz-sim-navsat-system", "gz::sim::systems::NavSat"),
    ("gz-sim-magnetometer-system", "gz::sim::systems::Magnetometer"),
    ("gz-sim-sensors-system", "gz::sim::systems::Sensors"),
)
GAZEBO_SENSORS_SYSTEM_NAME = "gz::sim::systems::Sensors"
# This is an event-loop scheduling checkpoint, not simulated time.  A short
# checkpoint lets MAVSDK/contact readers run before a bounded WorldControl
# request without adding a fixed 200 ms wall-clock delay to every barrier.
BARRIER_SCHEDULING_SETTLE_S = 0.01
# Pose_V may publish repeatedly while Gazebo is paused.  A barrier therefore
# waits for a fresh post-barrier version and for the decoded pose value to stay
# stable, rather than waiting for publication silence.  It never selects an
# older sample or another transport.
# One transport publication period is sufficient once the exact stats barrier
# has paused the world.  A 10 ms value filters a same-callback race without
# imposing a 50 ms wall-clock tax on every logical barrier.
POSE_TOPIC_QUIESCENCE_S = 0.01
POSE_TOPIC_POLL_S = 0.005
# Keep the largest configured 125-step logical barrier in one bounded native
# request. Warm-up still advances in finite chunks, and every strict CLI ACK is
# followed by the existing exact WorldStatistics equality barrier.
WORLD_CONTROL_CHUNK_STEPS = 128
# Live urban runs show the WorldControl reply arriving only after Gazebo
# finishes the bounded physics chunk, not before it.  The timeout must cover
# that wall-clock; a 2 s budget aborted 124-step chunks before sim time moved.
# A lost/false reply still reconciles against WorldStatistics and must not
# resend after that stream has advanced.
WORLD_CONTROL_ACK_TIMEOUT_S = 20.0
# Gazebo publishes the exact final sim-time sample before its asynchronous
# pause flag is observable.  Confirm that flag within a short bounded window;
# this is a transport-ordering wait, not extra simulated time.
PAUSED_STATE_CONFIRM_TIMEOUT_S = 5.0
GZ_COMMAND_DIAGNOSTIC_TAIL_BYTES = 4096
MAX_OBSERVATION_BYTES = 1_048_576
WARMUP_NS = 10_000_000_000
PX4_BIN = "/opt/px4-gazebo/bin/px4"
GZ_BIN = "/usr/bin/gz"
MAVSDK_BIN = "/opt/mavsdk/mavsdk_server"
PX4_DATA_DIR = "/opt/px4-gazebo/etc"
GZ_WORLDS_DIR = "/opt/px4-gazebo/share/gz/worlds"
GZ_MODELS_DIR = "/opt/px4-gazebo/share/gz/models"
IDENTITY_PATH = Path("/opt/aero-bench/identity.json")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEX_BYTES = re.compile(r"^(?:[0-9a-f]{2})*$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
FLIGHT_MODE_ENUM_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
PINNED_IMAGE = re.compile(
    r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$"
)
HOST = re.compile(r"^[A-Za-z0-9_.-]+$")
GOTO_TOOL = "flight.goto"
ARM_TOOL = "flight.arm"
DISARM_TOOL = "flight.disarm"
TAKEOFF_TOOL = "flight.takeoff"
LAND_TOOL = "flight.land"
HOLD_TOOL = "flight.hold"
COMMAND_HORIZON_INTERRUPTED_DETAIL = (
    "PX4 command interrupted at the configured run horizon before physical completion"
)
MAVSDK_SERVER_SYSTEM_ID = 245
MAVSDK_SERVER_COMPONENT_ID = 190
COMMAND_AUDIT_TRANSPORT_KIND = "command_transport_accept"
COMMAND_AUDIT_ACK_INGRESS_KIND = "command_ack_ingress"
COMMAND_AUDIT_ACK_DISPOSITION_KIND = "command_ack_disposition"
COMMAND_AUDIT_MATCHED_DISPOSITION = "matched_outstanding_command"
COMMAND_AUDIT_UNMATCHED_DISPOSITION = "unmatched_no_outstanding_command"
COMMAND_AUDIT_TARGET_FILTERED_DISPOSITION = "target_filtered_before_match"
COMMAND_AUDIT_INTERNAL_ERROR_DISPOSITION = "matcher_internal_error"
COMMAND_AUDIT_WIRE_LONG = "COMMAND_LONG"
COMMAND_AUDIT_WIRE_INT = "COMMAND_INT"
COMMAND_AUDIT_APPLIED_STATUS = "decoded_command_ack_accepted"
COMMAND_AUDIT_FAILED_STATUS = "decoded_command_ack_nonaccepted"
COMMAND_AUDIT_MISSING_STATUS = "decoded_command_ack_missing"
COMMAND_AUDIT_CORRUPT_STATUS = "decoded_command_audit_corrupt"
COMMAND_AUDIT_SCHEMA_VERSION = "aero-bench.px4-decoded-command-audit/v2"
COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY = (
    "mavsdk_impl.deliver_message.connection_send_return"
)
COMMAND_TRANSPORT_FRAME_ENCODING = "mavlink_msg_to_send_buffer_from_packed_message"
COMMAND_ACK_CAPTURE_BOUNDARY = (
    "mavlink_command_sender.receive_command_ack.decoded_handler"
)
COMMAND_ACK_FRAME_ENCODING = "mavlink_msg_to_send_buffer_from_decoded_message"
MAVLINK_V1_MAGIC = 0xFE
MAVLINK_V2_MAGIC = 0xFD
MAVLINK_IFLAG_SIGNED = 0x01
MAVLINK_SIGNATURE_LENGTH = 13
MAVLINK_COMMAND_INT_MESSAGE_ID = 75
MAVLINK_COMMAND_LONG_MESSAGE_ID = 76
MAVLINK_COMMAND_ACK_MESSAGE_ID = 77
MAVLINK_CRC_EXTRAS = {
    MAVLINK_COMMAND_INT_MESSAGE_ID: 158,
    MAVLINK_COMMAND_LONG_MESSAGE_ID: 152,
    MAVLINK_COMMAND_ACK_MESSAGE_ID: 143,
}
MAV_RESULT_ACCEPTED = 0
MAV_RESULT_DENIED = 2
MAV_RESULT_FAILED = 4
MAV_RESULT_IN_PROGRESS = 5
UINT8_MAX = 255
UINT16_MAX = 65_535
UINT24_MAX = (1 << 24) - 1
UINT64_MAX = (1 << 64) - 1
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
TOOL_COMMAND_AUDIT_SPEC = {
    ARM_TOOL: {"mav_cmd": 400, "wire_type": COMMAND_AUDIT_WIRE_LONG},
    DISARM_TOOL: {"mav_cmd": 400, "wire_type": COMMAND_AUDIT_WIRE_LONG},
    TAKEOFF_TOOL: {"mav_cmd": 22, "wire_type": COMMAND_AUDIT_WIRE_LONG},
    LAND_TOOL: {"mav_cmd": 21, "wire_type": COMMAND_AUDIT_WIRE_LONG},
    GOTO_TOOL: {"mav_cmd": 192, "wire_type": COMMAND_AUDIT_WIRE_INT},
    HOLD_TOOL: {"mav_cmd": 176, "wire_type": COMMAND_AUDIT_WIRE_LONG},
}
COMMAND_AUDIT_FRAME_FIELDS = frozenset(
    {
        "canonical_frame_hex",
        "canonical_frame_length",
        "checksum",
        "compat_flags",
        "frame_encoding",
        "incompat_flags",
        "magic",
        "message_id",
        "packet_sequence",
        "payload_hex",
        "payload_length",
        "signature_hex",
        "signed_frame",
        "wire_version",
    }
)
COMMAND_AUDIT_TRANSPORT_FIELDS = COMMAND_AUDIT_FRAME_FIELDS | frozenset(
    {
        "acceptance_boundary",
        "accepted_connection_count",
        "command",
        "confirmation",
        "kind",
        "seq",
        "source_component_id",
        "source_system_id",
        "target_component_id",
        "target_system_id",
        "timestamp_ns",
        "wire_type",
    }
)
COMMAND_AUDIT_ACK_INGRESS_FIELDS = COMMAND_AUDIT_FRAME_FIELDS | frozenset(
    {
        "capture_boundary",
        "command",
        "kind",
        "progress",
        "result",
        "result_param2",
        "seq",
        "source_component_id",
        "source_system_id",
        "target_component_id",
        "target_system_id",
        "timestamp_ns",
    }
)
COMMAND_AUDIT_ACK_DISPOSITION_FIELDS = frozenset(
    {"ack_ingress_seq", "command", "kind", "seq", "status", "timestamp_ns"}
)
REQUIRED_GOTO_ARGUMENTS = (
    "vehicle_id",
    "latitude_deg",
    "longitude_deg",
    "altitude_amsl_m",
    "yaw_deg",
)
REQUIRED_VEHICLE_ARGUMENT = ("vehicle_id",)
REQUIRED_TAKEOFF_ARGUMENTS = ("vehicle_id", "altitude_m")
# Source contract note: this code requires transport-accepted COMMAND_* records
# and canonical frame identity captured at decoded COMMAND_ACK ingress in the
# patched MAVSDK source. Until a real image rebuild happens, the old pinned
# mavsdk_server binary digest cannot satisfy this contract or a live validation
# claim.


class Px4ServiceError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "request.invalid"):
        self.code = code
        super().__init__(detail)


class _GazeboWorldControlAckError(Px4ServiceError):
    """A WorldControl acknowledgement was absent, malformed, or negative."""

    def __init__(self, detail: str, *, lost_reply: bool = False):
        super().__init__(detail)
        self.lost_reply = lost_reply


class Px4CommandPhaseError(Px4ServiceError):
    def __init__(self, *, last_success: str, detail: str):
        super().__init__(detail)
        self.last_success = last_success
        self.detail = detail


class ProviderLifecycle(str, Enum):
    RUNNING = "RUNNING"
    BARRIER_PAUSED = "BARRIER_PAUSED"
    ACTION_STAGED = "ACTION_STAGED"
    EXECUTING = "EXECUTING"


_ALLOWED_LIFECYCLE_TRANSITIONS = {
    ProviderLifecycle.BARRIER_PAUSED: {
        ProviderLifecycle.RUNNING,
        ProviderLifecycle.ACTION_STAGED,
    },
    ProviderLifecycle.ACTION_STAGED: {ProviderLifecycle.RUNNING},
    ProviderLifecycle.RUNNING: {
        ProviderLifecycle.BARRIER_PAUSED,
        ProviderLifecycle.EXECUTING,
    },
    ProviderLifecycle.EXECUTING: {ProviderLifecycle.BARRIER_PAUSED},
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _startup_progress(
    stage: str, *, started_at: float, detail: Mapping[str, object] | None = None
) -> None:
    payload: dict[str, object] = {
        "kind": "px4-startup-progress",
        "stage": stage,
        "elapsed_ms": max(0, round((time.monotonic() - started_at) * 1000)),
    }
    if detail is not None:
        payload["detail"] = dict(detail)
    sys.stderr.write(_canonical_json(payload).decode("utf-8") + "\n")
    sys.stderr.flush()


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _parse_json(frame: bytes | str) -> dict[str, Any]:
    try:
        value = json.loads(
            frame,
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Px4ServiceError("provider JSON is not strict") from exc
    if not isinstance(value, dict):
        raise Px4ServiceError("provider JSON must be an object")
    return value


def _strict_object(
    value: object,
    *,
    required: frozenset[str],
    label: str,
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Px4ServiceError(f"{label} must be a JSON object")
    actual = set(value)
    missing = required - actual
    extra = actual - required - optional
    if missing or extra:
        raise Px4ServiceError(
            f"{label} fields are not exact: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _string(
    value: object, *, label: str, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise Px4ServiceError(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise Px4ServiceError(f"{label} has an invalid value")
    return value


def _integer(value: object, *, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise Px4ServiceError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise Px4ServiceError(f"{label} must be >= {minimum}")
    return value


def _bounded_integer(
    value: object,
    *,
    label: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    result = _integer(value, label=label, minimum=minimum)
    if maximum is not None and result > maximum:
        raise Px4ServiceError(f"{label} must be <= {maximum}")
    return result


def _sha256(value: object, *, label: str) -> str:
    result = _string(value, label=label, pattern=SHA256)
    if result == "0" * 64:
        raise Px4ServiceError(f"{label} cannot be a placeholder digest")
    return result


def _absolute_normalized_path(value: object, *, label: str) -> Path:
    path = _string(value, label=label)
    normalized = PurePosixPath(path)
    if (
        not normalized.is_absolute()
        or ".." in normalized.parts
        or "." in normalized.parts
        or str(normalized) != path
        or "\\" in path
    ):
        raise Px4ServiceError(f"{label} must be a normalized absolute path")
    return Path(path)


def _file_ref(value: object, *, label: str) -> dict[str, str]:
    raw = _strict_object(value, required=frozenset({"path", "sha256"}), label=label)
    path = _string(raw["path"], label=f"{label}.path")
    normalized = PurePosixPath(path)
    if (
        normalized.is_absolute()
        or normalized == PurePosixPath(".")
        or ".." in normalized.parts
        or str(normalized) != path
        or "\\" in path
    ):
        raise Px4ServiceError(f"{label}.path must be normalized and relative")
    return {"path": path, "sha256": _sha256(raw["sha256"], label=f"{label}.sha256")}


def _asset_ref(value: object, *, label: str) -> dict[str, Any]:
    raw = _strict_object(
        value,
        required=frozenset({"asset_id", "file", "classification", "audiences"}),
        label=label,
    )
    asset_id = _string(raw["asset_id"], label=f"{label}.asset_id", pattern=IDENTIFIER)
    classification = _string(raw["classification"], label=f"{label}.classification")
    if classification not in {"public", "private"}:
        raise Px4ServiceError(f"{label}.classification must be public or private")
    raw_audiences = raw["audiences"]
    if not isinstance(raw_audiences, list) or not raw_audiences:
        raise Px4ServiceError(f"{label}.audiences must be a non-empty JSON array")
    audiences: list[dict[str, object]] = []
    roles: set[str] = set()
    for index, item in enumerate(raw_audiences):
        audience_label = f"{label}.audiences[{index}]"
        audience = _strict_object(
            item,
            required=frozenset({"role", "workload_ids"}),
            label=audience_label,
        )
        role = _string(audience["role"], label=f"{audience_label}.role")
        if role not in {"agent", "provider", "verifier"} or role in roles:
            raise Px4ServiceError(f"{label}.audiences contains an invalid role")
        workload_ids = _string_list(
            audience["workload_ids"],
            label=f"{audience_label}.workload_ids",
            minimum=1,
        )
        if len(workload_ids) != len(set(workload_ids)) or any(
            IDENTIFIER.fullmatch(workload_id) is None for workload_id in workload_ids
        ):
            raise Px4ServiceError(
                f"{audience_label}.workload_ids contains an invalid identity"
            )
        roles.add(role)
        audiences.append({"role": role, "workload_ids": workload_ids})
    if classification == "private" and "agent" in roles:
        raise Px4ServiceError(f"{label} exposes a private asset to an Agent")
    return {
        "asset_id": asset_id,
        "file": _file_ref(raw["file"], label=f"{label}.file"),
        "classification": classification,
        "audiences": tuple(audiences),
    }


def _artifact_requirement(value: object, *, label: str) -> dict[str, str | int | None]:
    raw = _strict_object(
        value,
        required=frozenset(
            {
                "artifact_id",
                "artifact_type",
                "producer_id",
                "visibility",
                "relative_path",
                "max_size_bytes",
                "source_asset_id",
            }
        ),
        label=label,
    )
    artifact_id = _string(
        raw["artifact_id"], label=f"{label}.artifact_id", pattern=IDENTIFIER
    )
    artifact_type = _string(
        raw["artifact_type"], label=f"{label}.artifact_type", pattern=IDENTIFIER
    )
    producer_id = _string(
        raw["producer_id"], label=f"{label}.producer_id", pattern=IDENTIFIER
    )
    visibility = _string(raw["visibility"], label=f"{label}.visibility")
    if visibility not in {"public", "private"}:
        raise Px4ServiceError(f"{label}.visibility must be public or private")
    relative_path = _string(raw["relative_path"], label=f"{label}.relative_path")
    normalized = PurePosixPath(relative_path)
    if (
        normalized.is_absolute()
        or normalized == PurePosixPath(".")
        or ".." in normalized.parts
        or relative_path.strip() != relative_path
        or str(normalized) != relative_path
        or "\\" in relative_path
        or "\x00" in relative_path
    ):
        raise Px4ServiceError(f"{label}.relative_path must be normalized and relative")
    max_size_bytes = _integer(
        raw["max_size_bytes"], label=f"{label}.max_size_bytes", minimum=1
    )
    source_asset_id = raw["source_asset_id"]
    if source_asset_id is not None:
        source_asset_id = _string(
            source_asset_id,
            label=f"{label}.source_asset_id",
            pattern=IDENTIFIER,
        )
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_id": producer_id,
        "visibility": visibility,
        "relative_path": relative_path,
        "max_size_bytes": max_size_bytes,
        "source_asset_id": source_asset_id,
    }


def _airspace_transition_config(
    value: object, *, world_id: str
) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = _strict_object(
        value,
        required=frozenset(
            {
                "world_id",
                "world_digest",
                "region_id",
                "region_digest",
                "incident_vehicle",
                "transition_topic",
                "min_east_m",
                "max_east_m",
                "min_north_m",
                "max_north_m",
                "min_up_m",
                "max_up_m",
            }
        ),
        label="airspace_transition",
    )
    parsed: dict[str, Any] = {
        "world_id": _string(
            raw["world_id"], label="airspace_transition.world_id", pattern=IDENTIFIER
        ),
        "world_digest": _sha256(
            raw["world_digest"], label="airspace_transition.world_digest"
        ),
        "region_id": _string(
            raw["region_id"], label="airspace_transition.region_id", pattern=IDENTIFIER
        ),
        "region_digest": _sha256(
            raw["region_digest"], label="airspace_transition.region_digest"
        ),
        "incident_vehicle": _string(
            raw["incident_vehicle"],
            label="airspace_transition.incident_vehicle",
            pattern=IDENTIFIER,
        ),
        "transition_topic": _string(
            raw["transition_topic"], label="airspace_transition.transition_topic"
        ),
    }
    if parsed["world_digest"] == "0" * 64 or parsed["region_digest"] == "0" * 64:
        raise Px4ServiceError("airspace identity digests must be real")
    if parsed["world_id"] != world_id:
        raise Px4ServiceError(
            "airspace_transition.world_id must match ResolvedScenario.world_id"
        )
    if not parsed["transition_topic"].startswith("/") or any(
        character.isspace() for character in parsed["transition_topic"]
    ):
        raise Px4ServiceError(
            "airspace_transition.transition_topic must be an absolute topic"
        )
    for axis in ("east", "north", "up"):
        parsed[f"min_{axis}_m"] = _finite_number(
            raw[f"min_{axis}_m"], label=f"airspace_transition.min_{axis}_m"
        )
        parsed[f"max_{axis}_m"] = _finite_number(
            raw[f"max_{axis}_m"], label=f"airspace_transition.max_{axis}_m"
        )
        if parsed[f"min_{axis}_m"] > parsed[f"max_{axis}_m"]:
            raise Px4ServiceError(f"airspace_transition {axis} bounds are inverted")
    return parsed


def _inspection_config(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = _strict_object(
        value,
        required=frozenset(
            {
                "agent_id",
                "work_order_id",
                "target_id",
                "target_position",
                "target_position_wgs84",
                "target_model_asset_id",
                "observation_id",
                "camera_id",
                "camera_vehicle_id",
                "camera_mount",
                "resolution_width_px",
                "resolution_height_px",
                "metadata_schema",
                "media_type",
                "min_distance_m",
                "max_distance_m",
                "min_view_angle_deg",
                "max_view_angle_deg",
                "earliest_time_ns",
                "latest_time_ns",
            }
        ),
        label="inspection",
    )
    point_fields = frozenset({"x_m", "y_m", "z_m"})
    pose_fields = frozenset({"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"})
    target = _strict_object(
        raw["target_position"],
        required=point_fields,
        label="inspection.target_position",
    )
    target_wgs84 = _strict_object(
        raw["target_position_wgs84"],
        required=frozenset({"longitude_deg", "latitude_deg", "altitude_m"}),
        label="inspection.target_position_wgs84",
    )
    mount = _strict_object(
        raw["camera_mount"], required=pose_fields, label="inspection.camera_mount"
    )
    parsed: dict[str, Any] = {
        "agent_id": _string(
            raw["agent_id"], label="inspection.agent_id", pattern=IDENTIFIER
        ),
        "work_order_id": _string(
            raw["work_order_id"], label="inspection.work_order_id", pattern=IDENTIFIER
        ),
        "target_id": _string(
            raw["target_id"], label="inspection.target_id", pattern=IDENTIFIER
        ),
        "target_position": {
            name: _finite_number(value, label=f"inspection.target_position.{name}")
            for name, value in target.items()
        },
        "target_position_wgs84": {
            name: _finite_number(
                value, label=f"inspection.target_position_wgs84.{name}"
            )
            for name, value in target_wgs84.items()
        },
        "target_model_asset_id": _string(
            raw["target_model_asset_id"],
            label="inspection.target_model_asset_id",
            pattern=IDENTIFIER,
        ),
        "observation_id": _string(
            raw["observation_id"], label="inspection.observation_id", pattern=IDENTIFIER
        ),
        "camera_id": _string(
            raw["camera_id"], label="inspection.camera_id", pattern=IDENTIFIER
        ),
        "camera_vehicle_id": _string(
            raw["camera_vehicle_id"],
            label="inspection.camera_vehicle_id",
            pattern=IDENTIFIER,
        ),
        "camera_mount": {
            name: _finite_number(value, label=f"inspection.camera_mount.{name}")
            for name, value in mount.items()
        },
        "resolution_width_px": _bounded_integer(
            raw["resolution_width_px"],
            label="inspection.resolution_width_px",
            minimum=1,
            maximum=4096,
        ),
        "resolution_height_px": _bounded_integer(
            raw["resolution_height_px"],
            label="inspection.resolution_height_px",
            minimum=1,
            maximum=4096,
        ),
        "metadata_schema": _file_ref(
            raw["metadata_schema"], label="inspection.metadata_schema"
        ),
        "media_type": _string(raw["media_type"], label="inspection.media_type"),
        "min_distance_m": _finite_number(
            raw["min_distance_m"], label="inspection.min_distance_m"
        ),
        "max_distance_m": _finite_number(
            raw["max_distance_m"], label="inspection.max_distance_m"
        ),
        "min_view_angle_deg": _finite_number(
            raw["min_view_angle_deg"], label="inspection.min_view_angle_deg"
        ),
        "max_view_angle_deg": _finite_number(
            raw["max_view_angle_deg"], label="inspection.max_view_angle_deg"
        ),
        "earliest_time_ns": _integer(
            raw["earliest_time_ns"], label="inspection.earliest_time_ns", minimum=0
        ),
        "latest_time_ns": _integer(
            raw["latest_time_ns"], label="inspection.latest_time_ns", minimum=1
        ),
    }
    if parsed["media_type"] != "application/json":
        raise Px4ServiceError(
            "inspection observation media_type must be application/json"
        )
    if not 0 < parsed["min_distance_m"] <= parsed["max_distance_m"]:
        raise Px4ServiceError("inspection distance range is invalid")
    if not (0 <= parsed["min_view_angle_deg"] <= parsed["max_view_angle_deg"] <= 180):
        raise Px4ServiceError("inspection view-angle range is invalid")
    if parsed["earliest_time_ns"] >= parsed["latest_time_ns"]:
        raise Px4ServiceError("inspection time window is invalid")
    return parsed


def _string_list(
    value: object,
    *,
    label: str,
    minimum: int = 0,
    pattern: re.Pattern[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise Px4ServiceError(f"{label} must be a JSON array")
    if len(value) < minimum:
        raise Px4ServiceError(f"{label} must contain at least {minimum} item(s)")
    return tuple(
        _string(item, label=f"{label}[{index}]", pattern=pattern)
        for index, item in enumerate(value)
    )


def _strict_positive_int(value: object, *, label: str, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise Px4ServiceError(f"{label} must be an integer")
    if value < minimum:
        raise Px4ServiceError(f"{label} must be >= {minimum}")
    return value


def _strict_positive_float(value: object, *, label: str) -> float:
    if not isinstance(value, float):
        raise Px4ServiceError(f"{label} must be a finite number")
    number = value
    if not math.isfinite(number) or number <= 0.0:
        raise Px4ServiceError(f"{label} must be a positive finite number")
    return number


def _flight_mode_scalar(value: object, *, label: str) -> str:
    return _string(value, label=label, pattern=FLIGHT_MODE_ENUM_NAME)


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _earliest_physical_completion_offset_ns(
    *,
    step_length_ns: int,
    min_settle_samples: int,
    settle_duration_ns: int,
) -> int:
    # One step reaches the apply barrier. Completion then needs a later barrier
    # plus enough additional step intervals to satisfy both dwell predicates.
    settling_intervals = max(
        min_settle_samples - 1,
        _ceil_div(settle_duration_ns, step_length_ns),
    )
    return (2 + settling_intervals) * step_length_ns


def _validate_physical_completion_window(
    *,
    step_length_ns: int,
    policy: Mapping[str, Any],
    label: str,
) -> None:
    earliest_completion_offset_ns = _earliest_physical_completion_offset_ns(
        step_length_ns=step_length_ns,
        min_settle_samples=int(policy["min_settle_samples"]),
        settle_duration_ns=int(policy["settle_duration_ns"]),
    )
    if earliest_completion_offset_ns >= int(policy["physical_sim_timeout_ns"]):
        raise Px4ServiceError(
            f"{label}.physical_sim_timeout_ns must exceed the earliest possible "
            "physical completion offset: "
            f"step_length_ns={step_length_ns}, "
            f"earliest_completion_offset_ns={earliest_completion_offset_ns}"
        )


def _sorted_mode_allowlist(value: object, *, label: str) -> tuple[str, ...]:
    modes = _string_list(
        value,
        label=label,
        minimum=1,
        pattern=FLIGHT_MODE_ENUM_NAME,
    )
    if len(modes) != len(set(modes)):
        raise Px4ServiceError(f"{label} must not repeat modes")
    if tuple(sorted(modes)) != modes:
        raise Px4ServiceError(f"{label} must be sorted")
    return modes


def _physical_completion_policy(value: object) -> dict[str, Any]:
    raw = _strict_object(
        value,
        required=frozenset(
            {
                "schema_version",
                "physical_sim_timeout_ns",
                "min_settle_samples",
                "settle_duration_ns",
                "takeoff_altitude_tolerance_m",
                "goto_horizontal_tolerance_m",
                "goto_vertical_tolerance_m",
                "goto_minimum_progress_m",
                "hold_drift_radius_m",
                "max_horizontal_settled_speed_m_s",
                "max_vertical_settled_speed_m_s",
                "landing_max_speed_m_s",
                "landing_max_height_proxy_m",
                "disarm_requires_contact",
                "arm_allowed_modes",
                "disarm_allowed_modes",
                "takeoff_allowed_modes",
                "goto_allowed_modes",
                "hold_allowed_modes",
                "land_allowed_modes",
            }
        ),
        label="physical_completion_policy",
    )
    if raw["schema_version"] != "aero-bench.px4-physical-completion-policy/v2":
        raise Px4ServiceError("physical_completion_policy schema_version mismatch")
    if not isinstance(raw["disarm_requires_contact"], bool):
        raise Px4ServiceError(
            "physical_completion_policy.disarm_requires_contact must be a boolean"
        )
    parsed = {
        "schema_version": raw["schema_version"],
        "physical_sim_timeout_ns": _strict_positive_int(
            raw["physical_sim_timeout_ns"],
            label="physical_completion_policy.physical_sim_timeout_ns",
        ),
        "min_settle_samples": _strict_positive_int(
            raw["min_settle_samples"],
            label="physical_completion_policy.min_settle_samples",
            minimum=2,
        ),
        "settle_duration_ns": _strict_positive_int(
            raw["settle_duration_ns"],
            label="physical_completion_policy.settle_duration_ns",
        ),
        "takeoff_altitude_tolerance_m": _strict_positive_float(
            raw["takeoff_altitude_tolerance_m"],
            label="physical_completion_policy.takeoff_altitude_tolerance_m",
        ),
        "goto_horizontal_tolerance_m": _strict_positive_float(
            raw["goto_horizontal_tolerance_m"],
            label="physical_completion_policy.goto_horizontal_tolerance_m",
        ),
        "goto_vertical_tolerance_m": _strict_positive_float(
            raw["goto_vertical_tolerance_m"],
            label="physical_completion_policy.goto_vertical_tolerance_m",
        ),
        "goto_minimum_progress_m": _strict_positive_float(
            raw["goto_minimum_progress_m"],
            label="physical_completion_policy.goto_minimum_progress_m",
        ),
        "hold_drift_radius_m": _strict_positive_float(
            raw["hold_drift_radius_m"],
            label="physical_completion_policy.hold_drift_radius_m",
        ),
        "max_horizontal_settled_speed_m_s": _strict_positive_float(
            raw["max_horizontal_settled_speed_m_s"],
            label="physical_completion_policy.max_horizontal_settled_speed_m_s",
        ),
        "max_vertical_settled_speed_m_s": _strict_positive_float(
            raw["max_vertical_settled_speed_m_s"],
            label="physical_completion_policy.max_vertical_settled_speed_m_s",
        ),
        "landing_max_speed_m_s": _strict_positive_float(
            raw["landing_max_speed_m_s"],
            label="physical_completion_policy.landing_max_speed_m_s",
        ),
        "landing_max_height_proxy_m": _strict_positive_float(
            raw["landing_max_height_proxy_m"],
            label="physical_completion_policy.landing_max_height_proxy_m",
        ),
        "disarm_requires_contact": raw["disarm_requires_contact"],
        "arm_allowed_modes": _sorted_mode_allowlist(
            raw["arm_allowed_modes"],
            label="physical_completion_policy.arm_allowed_modes",
        ),
        "disarm_allowed_modes": _sorted_mode_allowlist(
            raw["disarm_allowed_modes"],
            label="physical_completion_policy.disarm_allowed_modes",
        ),
        "takeoff_allowed_modes": _sorted_mode_allowlist(
            raw["takeoff_allowed_modes"],
            label="physical_completion_policy.takeoff_allowed_modes",
        ),
        "goto_allowed_modes": _sorted_mode_allowlist(
            raw["goto_allowed_modes"],
            label="physical_completion_policy.goto_allowed_modes",
        ),
        "hold_allowed_modes": _sorted_mode_allowlist(
            raw["hold_allowed_modes"],
            label="physical_completion_policy.hold_allowed_modes",
        ),
        "land_allowed_modes": _sorted_mode_allowlist(
            raw["land_allowed_modes"],
            label="physical_completion_policy.land_allowed_modes",
        ),
    }
    if parsed["settle_duration_ns"] >= parsed["physical_sim_timeout_ns"]:
        raise Px4ServiceError(
            "physical_completion_policy.settle_duration_ns must be less than physical_sim_timeout_ns"
        )
    return parsed


def _software_identity(value: object, *, label: str) -> dict[str, str]:
    raw = _strict_object(value, required=frozenset({"version", "commit"}), label=label)
    commit = _string(raw["commit"], label=f"{label}.commit")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise Px4ServiceError(f"{label}.commit must be a full lowercase Git SHA")
    return {
        "version": _string(raw["version"], label=f"{label}.version"),
        "commit": commit,
    }


def _vehicle_initial_pose(value: object, *, label: str) -> dict[str, float]:
    fields = ("x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad")
    raw = _strict_object(value, required=frozenset(fields), label=label)
    return {
        field: _finite_number(raw[field], label=f"{label}.{field}")
        for field in fields
    }


def _px4_provider_config(value: object) -> dict[str, Any]:
    raw = _strict_object(
        value,
        required=frozenset(
            {
                "schema_version",
                "provider_id",
                "px4",
                "gazebo",
                "mavsdk",
                "engine_binding_id",
                "physics_step_ns",
                "px4_executable",
                "gazebo_executable",
                "mavsdk_server_executable",
                "vehicles",
                "required_commands",
                "command_timeout_ms",
                "physical_completion_policy",
                "maximum_agent_decision_wall_time_ms",
                "heartbeat_timeout_fixed_margin_ms",
            }
        ),
        label="Px4GazeboConfig",
        optional=frozenset(
            {"airspace_transition", "world_name", "world_input"}
        ),
    )
    if raw["schema_version"] != "aero-bench.px4-gazebo/v3":
        raise Px4ServiceError("Px4GazeboConfig schema version is unsupported")
    raw_vehicles = raw["vehicles"]
    if not isinstance(raw_vehicles, list) or not raw_vehicles:
        raise Px4ServiceError("Px4GazeboConfig.vehicles must be a non-empty array")
    vehicles: list[dict[str, Any]] = []
    vehicle_ids: list[str] = []
    system_ids: list[int] = []
    ports: list[int] = []
    gazebo_names: list[str] = []
    for index, item in enumerate(raw_vehicles):
        label = f"Px4GazeboConfig.vehicles[{index}]"
        vehicle = _strict_object(
            item,
            required=frozenset(
                {
                    "vehicle_id",
                    "system_id",
                    "mavsdk_udp_port",
                    "px4_mavlink_udp_port",
                    "mavsdk_grpc_port",
                    "sys_autostart",
                    "model",
                    "gazebo_model_name",
                    "gazebo_resource",
                }
            ),
            optional=frozenset({"initial_pose"}),
            label=label,
        )
        parsed = {
            "vehicle_id": _string(
                vehicle["vehicle_id"], label=f"{label}.vehicle_id", pattern=IDENTIFIER
            ),
            "system_id": _bounded_integer(
                vehicle["system_id"], label=f"{label}.system_id", minimum=1, maximum=255
            ),
            "mavsdk_udp_port": _bounded_integer(
                vehicle["mavsdk_udp_port"],
                label=f"{label}.mavsdk_udp_port",
                minimum=1024,
                maximum=65535,
            ),
            "px4_mavlink_udp_port": _bounded_integer(
                vehicle["px4_mavlink_udp_port"],
                label=f"{label}.px4_mavlink_udp_port",
                minimum=1024,
                maximum=65535,
            ),
            "mavsdk_grpc_port": _bounded_integer(
                vehicle["mavsdk_grpc_port"],
                label=f"{label}.mavsdk_grpc_port",
                minimum=1024,
                maximum=65535,
            ),
            "sys_autostart": _integer(
                vehicle["sys_autostart"], label=f"{label}.sys_autostart", minimum=1
            ),
            "model": _string(
                vehicle["model"], label=f"{label}.model", pattern=IDENTIFIER
            ),
            "gazebo_model_name": _string(
                vehicle["gazebo_model_name"],
                label=f"{label}.gazebo_model_name",
                pattern=IDENTIFIER,
            ),
            "gazebo_resource": _string(
                vehicle["gazebo_resource"],
                label=f"{label}.gazebo_resource",
                pattern=IDENTIFIER,
            ),
            "initial_pose": (
                _vehicle_initial_pose(
                    vehicle["initial_pose"], label=f"{label}.initial_pose"
                )
                if vehicle.get("initial_pose") is not None
                else None
            ),
        }
        vehicle_ids.append(parsed["vehicle_id"])
        system_ids.append(parsed["system_id"])
        gazebo_names.append(parsed["gazebo_model_name"])
        ports.extend(
            (
                parsed["mavsdk_udp_port"],
                parsed["px4_mavlink_udp_port"],
                parsed["mavsdk_grpc_port"],
            )
        )
        vehicles.append(parsed)
    if (
        len(vehicle_ids) != len(set(vehicle_ids))
        or len(system_ids) != len(set(system_ids))
        or len(gazebo_names) != len(set(gazebo_names))
        or len(ports) != len(set(ports))
    ):
        raise Px4ServiceError("Px4GazeboConfig vehicle identities or ports repeat")
    commands = _string_list(
        raw["required_commands"], label="Px4GazeboConfig.required_commands", minimum=1
    )
    if len(commands) != len(set(commands)):
        raise Px4ServiceError("Px4GazeboConfig.required_commands must be unique")
    executables = (
        _string(raw["px4_executable"], label="Px4GazeboConfig.px4_executable"),
        _string(raw["gazebo_executable"], label="Px4GazeboConfig.gazebo_executable"),
        _string(
            raw["mavsdk_server_executable"],
            label="Px4GazeboConfig.mavsdk_server_executable",
        ),
    )
    if len(set(executables)) != 3 or set(executables) - set(commands):
        raise Px4ServiceError(
            "Px4GazeboConfig executables must be distinct required_commands"
        )
    return {
        "schema_version": raw["schema_version"],
        "provider_id": _string(
            raw["provider_id"], label="Px4GazeboConfig.provider_id", pattern=IDENTIFIER
        ),
        "px4": _software_identity(raw["px4"], label="Px4GazeboConfig.px4"),
        "gazebo": _software_identity(raw["gazebo"], label="Px4GazeboConfig.gazebo"),
        "mavsdk": _software_identity(raw["mavsdk"], label="Px4GazeboConfig.mavsdk"),
        "engine_binding_id": _string(
            raw["engine_binding_id"],
            label="Px4GazeboConfig.engine_binding_id",
            pattern=IDENTIFIER,
        ),
        "physics_step_ns": _integer(
            raw["physics_step_ns"],
            label="Px4GazeboConfig.physics_step_ns",
            minimum=1,
        ),
        "px4_executable": executables[0],
        "gazebo_executable": executables[1],
        "mavsdk_server_executable": executables[2],
        "vehicles": vehicles,
        "required_commands": list(commands),
        "command_timeout_ms": _integer(
            raw["command_timeout_ms"],
            label="Px4GazeboConfig.command_timeout_ms",
            minimum=1,
        ),
        "physical_completion_policy": _physical_completion_policy(
            raw["physical_completion_policy"]
        ),
        "maximum_agent_decision_wall_time_ms": _integer(
            raw["maximum_agent_decision_wall_time_ms"],
            label="Px4GazeboConfig.maximum_agent_decision_wall_time_ms",
            minimum=1,
        ),
        "heartbeat_timeout_fixed_margin_ms": _integer(
            raw["heartbeat_timeout_fixed_margin_ms"],
            label="Px4GazeboConfig.heartbeat_timeout_fixed_margin_ms",
            minimum=1,
        ),
        "airspace_transition": raw.get("airspace_transition"),
        "world_name": (
            _string(raw["world_name"], label="Px4GazeboConfig.world_name", pattern=IDENTIFIER)
            if raw.get("world_name") is not None
            else None
        ),
        "world_input": (
            _parse_world_input_value(
                raw["world_input"], label="Px4GazeboConfig.world_input"
            )
            if raw.get("world_input") is not None
            else None
        ),
    }


def _resolved_pose(value: object, *, label: str) -> dict[str, float]:
    pose = _strict_object(
        value,
        required=frozenset({"position", "orientation_enu", "orientation_ned"}),
        label=label,
    )
    position = _strict_object(
        pose["position"],
        required=frozenset(
            {
                "enu",
                "ned",
                "ecef",
                "wgs84",
                "geoid_separation_m",
                "amsl_m",
                "terrain_amsl_m",
                "agl_m",
            }
        ),
        label=f"{label}.position",
    )
    enu = _strict_object(
        position["enu"],
        required=frozenset({"east_m", "north_m", "up_m"}),
        label=f"{label}.position.enu",
    )
    orientation = _strict_object(
        pose["orientation_enu"],
        required=frozenset({"qw", "qx", "qy", "qz"}),
        label=f"{label}.orientation_enu",
    )
    try:
        quaternion = frame_math.UnitQuaternion(
            w=_finite_number(orientation["qw"], label=f"{label}.orientation_enu.qw"),
            x=_finite_number(orientation["qx"], label=f"{label}.orientation_enu.qx"),
            y=_finite_number(orientation["qy"], label=f"{label}.orientation_enu.qy"),
            z=_finite_number(orientation["qz"], label=f"{label}.orientation_enu.qz"),
        )
        roll, pitch, yaw = frame_math.quaternion_to_rpy(quaternion)
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError(f"{label} has an invalid ENU orientation") from exc
    return {
        "x_m": _finite_number(enu["east_m"], label=f"{label}.position.enu.east_m"),
        "y_m": _finite_number(enu["north_m"], label=f"{label}.position.enu.north_m"),
        "z_m": _finite_number(enu["up_m"], label=f"{label}.position.enu.up_m"),
        "roll_rad": roll,
        "pitch_rad": pitch,
        "yaw_rad": yaw,
    }


def _resolved_pose_wgs84(value: object, *, label: str) -> dict[str, float]:
    pose = _strict_object(
        value,
        required=frozenset({"position", "orientation_enu", "orientation_ned"}),
        label=label,
    )
    position = _strict_object(
        pose["position"],
        required=frozenset(
            {
                "enu",
                "ned",
                "ecef",
                "wgs84",
                "geoid_separation_m",
                "amsl_m",
                "terrain_amsl_m",
                "agl_m",
            }
        ),
        label=f"{label}.position",
    )
    wgs84 = _strict_object(
        position["wgs84"],
        required=frozenset({"longitude_deg", "latitude_deg", "ellipsoid_height_m"}),
        label=f"{label}.position.wgs84",
    )
    return {
        "longitude_deg": _finite_number(
            wgs84["longitude_deg"], label=f"{label}.position.wgs84.longitude_deg"
        ),
        "latitude_deg": _finite_number(
            wgs84["latitude_deg"], label=f"{label}.position.wgs84.latitude_deg"
        ),
        "altitude_m": _finite_number(
            position["amsl_m"], label=f"{label}.position.amsl_m"
        ),
    }


def _scenario_entity(value: object, *, label: str) -> dict[str, Any]:
    entity = _strict_object(
        value,
        required=frozenset(
            {
                "entity_id",
                "kind",
                "owner_kind",
                "owner_id",
                "source_provider_id",
                "authority_kind",
                "state",
                "model_asset_id",
                "initial_pose",
                "selected_launch_override",
            }
        ),
        label=label,
    )
    if not isinstance(entity["selected_launch_override"], bool):
        raise Px4ServiceError(f"{label}.selected_launch_override must be a boolean")
    return {
        **entity,
        "entity_id": _string(
            entity["entity_id"], label=f"{label}.entity_id", pattern=IDENTIFIER
        ),
        "owner_id": _string(
            entity["owner_id"], label=f"{label}.owner_id", pattern=IDENTIFIER
        ),
        "model_asset_id": _string(
            entity["model_asset_id"],
            label=f"{label}.model_asset_id",
            pattern=IDENTIFIER,
        ),
        "initial_pose": _resolved_pose(
            entity["initial_pose"], label=f"{label}.initial_pose"
        ),
    }


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def _rgb_png(*, width: int, height: int, pixels: bytes) -> bytes:
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        raise Px4ServiceError(
            "Gazebo camera dimensions are outside the supported range"
        )
    row_bytes = width * 3
    if len(pixels) != row_bytes * height:
        raise Px4ServiceError("Gazebo RGB frame byte length does not match dimensions")
    scanlines = b"".join(
        b"\x00" + pixels[offset : offset + row_bytes]
        for offset in range(0, len(pixels), row_bytes)
    )
    encoded = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )
    if len(encoded) > MAX_OBSERVATION_BYTES:
        raise Px4ServiceError(
            "lossless Gazebo camera frame exceeds the formal observation byte limit"
        )
    return encoded


@dataclass(frozen=True, slots=True)
class CapturedCameraFrame:
    vehicle_id: str
    camera_id: str
    width: int
    height: int
    pixel_format: str
    engine_sim_time_ns: int
    png_bytes: bytes


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Mapping[str, object]) -> str:
    return _digest_bytes(_canonical_json(value))


def _camera_frame_archive_record(frame_id: str, png_bytes: bytes) -> bytes:
    """Encode one self-delimiting PNG record for the public frame archive."""
    try:
        frame_id_bytes = frame_id.encode("ascii")
    except UnicodeEncodeError as error:
        raise Px4ServiceError("camera frame ID must be ASCII") from error
    if not frame_id_bytes or len(frame_id_bytes) > 4096:
        raise Px4ServiceError("camera frame ID has an invalid archive length")
    if not png_bytes or len(png_bytes) > 2 * 1024 * 1024:
        raise Px4ServiceError("camera PNG has an invalid archive length")
    return (
        struct.pack(">8sIQ", b"ABFRAME1", len(frame_id_bytes), len(png_bytes))
        + frame_id_bytes
        + png_bytes
    )


def _validate_camera_frame_archive(
    path: Path, expected_frames: Sequence[Mapping[str, object]]
) -> None:
    """Verify every framed PNG in the archive before it can be sealed."""
    expected_by_id = {
        str(frame["frame_id"]): frame for frame in expected_frames
    }
    observed: set[str] = set()
    try:
        with path.open("rb") as stream:
            while True:
                header = stream.read(struct.calcsize(">8sIQ"))
                if not header:
                    break
                if len(header) != struct.calcsize(">8sIQ"):
                    raise Px4ServiceError("camera frame archive has a truncated header")
                magic, frame_id_length, image_length = struct.unpack(">8sIQ", header)
                if magic != b"ABFRAME1" or not 1 <= frame_id_length <= 4096:
                    raise Px4ServiceError("camera frame archive record header is invalid")
                if not 1 <= image_length <= 2 * 1024 * 1024:
                    raise Px4ServiceError("camera frame archive image length is invalid")
                frame_id_bytes = stream.read(frame_id_length)
                if len(frame_id_bytes) != frame_id_length:
                    raise Px4ServiceError("camera frame archive has a truncated frame ID")
                try:
                    frame_id = frame_id_bytes.decode("ascii")
                except UnicodeDecodeError as error:
                    raise Px4ServiceError("camera frame archive frame ID is not ASCII") from error
                image = stream.read(image_length)
                if len(image) != image_length:
                    raise Px4ServiceError("camera frame archive has a truncated PNG")
                if frame_id in observed or frame_id not in expected_by_id:
                    raise Px4ServiceError("camera frame archive frame inventory is invalid")
                expected = expected_by_id[frame_id]
                if (
                    expected.get("size_bytes") != image_length
                    or expected.get("image_sha256") != _digest_bytes(image)
                ):
                    raise Px4ServiceError("camera frame archive image digest differs from metadata")
                observed.add(frame_id)
    except OSError as error:
        raise Px4ServiceError("camera frame archive cannot be read") from error
    if observed != set(expected_by_id):
        raise Px4ServiceError("camera frame archive does not contain every captured frame")


def _physical_state_digest(
    *,
    vehicle_id: str,
    tick: int,
    sim_time_ns: int,
    pose: Mapping[str, float],
    position_wgs84: Mapping[str, float],
    velocity_ned: Mapping[str, float],
    angular_velocity_body: Mapping[str, float],
    flight_mode: str,
    armed: bool,
    in_air: bool,
    landed: bool,
    landed_state: str,
    contacts: tuple[str, ...],
    ground_contact: bool,
    collision_contact: bool,
) -> str:
    return _digest_json(
        {
            "vehicle_id": vehicle_id,
            "tick": tick,
            "sim_time_ns": sim_time_ns,
            "pose": dict(pose),
            "position_wgs84": dict(position_wgs84),
            "velocity_ned": dict(velocity_ned),
            "angular_velocity_body": dict(angular_velocity_body),
            "flight_mode": flight_mode,
            "armed": armed,
            "in_air": in_air,
            "landed": landed,
            "landed_state": landed_state,
            "contacts": list(contacts),
            "ground_contact": ground_contact,
            "collision_contact": collision_contact,
        }
    )


def _require_session_token(payload: object, expected: str) -> None:
    presented = payload.get("session_token") if isinstance(payload, dict) else None
    valid = (
        isinstance(presented, str)
        and SHA256.fullmatch(presented) is not None
        and presented != "0" * 64
    )
    candidate = presented if valid else "0" * 64
    if not valid or not hmac.compare_digest(candidate, expected):
        raise Px4ServiceError(
            "PX4 request cannot present the executor-issued run-scoped session token",
            code="principal.denied",
        )


def _safe_runtime_image(value: object) -> str:
    image = _string(value, label="runtime_image", pattern=PINNED_IMAGE)
    if image.rsplit(":", maxsplit=1)[1] == "0" * 64:
        raise Px4ServiceError("runtime_image cannot use a placeholder digest")
    return image


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise Px4ServiceError(f"{label} must be a finite number")
    if isinstance(value, str):
        try:
            value = Decimal(value)
        except Exception as exc:
            raise Px4ServiceError(f"{label} must be a finite number") from exc
    if not isinstance(value, (int, float, Decimal)):
        raise Px4ServiceError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise Px4ServiceError(f"{label} must be a finite number")
    return number


def _proto3_float(value: object, *, label: str, default: float = 0.0) -> float:
    """Decode a proto3 JSON float. Omitted fields are the numeric default."""

    if value is None:
        return default
    return _finite_number(value, label=label)


def _proto3_int(
    value: object, *, label: str, default: int = 0, minimum: int | None = None
) -> int:
    """Decode a proto3 JSON integer. int64 values may be decimal strings."""

    if value is None:
        number = default
    elif isinstance(value, bool) or not isinstance(value, (int, str)):
        raise Px4ServiceError(f"{label} must be an integer")
    elif isinstance(value, str):
        if re.fullmatch(r"-?[0-9]+", value) is None:
            raise Px4ServiceError(f"{label} must be an integer")
        number = int(value)
    else:
        number = value
    if minimum is not None and number < minimum:
        raise Px4ServiceError(f"{label} must be >= {minimum}")
    return number


def sim_time_ns_from_stats(payload: Mapping[str, Any]) -> int:
    sim_time = payload.get("simTime")
    if not isinstance(sim_time, dict):
        raise Px4ServiceError("Gazebo stats omitted simTime")
    sec = _proto3_int(sim_time.get("sec"), label="simTime.sec")
    nsec = _proto3_int(sim_time.get("nsec"), label="simTime.nsec")
    if not 0 <= nsec < 1_000_000_000:
        raise Px4ServiceError("simTime.nsec is outside 0..999999999")
    return sec * 1_000_000_000 + nsec


def physics_step_count(*, current_ns: int, target_ns: int, physics_step_ns: int) -> int:
    if physics_step_ns <= 0:
        raise Px4ServiceError("physics_step_ns must be a positive integer")
    if target_ns < current_ns:
        raise Px4ServiceError("barrier target moved backwards")
    delta = target_ns - current_ns
    if delta % physics_step_ns != 0:
        raise Px4ServiceError(
            f"barrier target {target_ns} is not aligned to physics_step_ns "
            f"{physics_step_ns} from current {current_ns}"
        )
    return delta // physics_step_ns


def gazebo_paused_argv(executable: str, world_sdf: Path) -> tuple[str, ...]:
    return (executable, "sim", "-s", str(world_sdf))


def incoming_heartbeat_timeout_s(
    *, maximum_agent_decision_wall_time_ms: int, fixed_margin_ms: int
) -> Decimal:
    if (
        isinstance(maximum_agent_decision_wall_time_ms, bool)
        or not isinstance(maximum_agent_decision_wall_time_ms, int)
        or maximum_agent_decision_wall_time_ms <= 0
    ):
        raise Px4ServiceError(
            "maximum_agent_decision_wall_time_ms must be a positive integer"
        )
    if (
        isinstance(fixed_margin_ms, bool)
        or not isinstance(fixed_margin_ms, int)
        or fixed_margin_ms <= 0
    ):
        raise Px4ServiceError(
            "heartbeat_timeout_fixed_margin_ms must be a positive integer"
        )
    return Decimal(maximum_agent_decision_wall_time_ms + fixed_margin_ms) / Decimal(
        1000
    )


def _plain_decimal(value: Decimal) -> str:
    return format(value, "f")


def mavsdk_command_audit_journal_path(
    tmp_dir: Path,
    vehicle_id: str,
    *,
    process_generation: int,
) -> Path:
    _string(vehicle_id, label="vehicle_id", pattern=IDENTIFIER)
    _bounded_integer(
        process_generation,
        label="process_generation",
        minimum=1,
        maximum=UINT64_MAX,
    )
    return (
        tmp_dir
        / "mavsdk-command-audit"
        / vehicle_id
        / f"process-{process_generation}"
        / "command-audit.jsonl"
    )


def allocate_mavsdk_command_audit_journal_path(
    tmp_dir: Path,
    vehicle_id: str,
    *,
    process_generation: int,
) -> Path:
    journal_path = mavsdk_command_audit_journal_path(
        tmp_dir,
        vehicle_id,
        process_generation=process_generation,
    )
    directories = (
        tmp_dir,
        tmp_dir / "mavsdk-command-audit",
        tmp_dir / "mavsdk-command-audit" / vehicle_id,
        journal_path.parent,
    )
    for directory in directories:
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise Px4ServiceError(
                    "MAVSDK command audit journal parent path must be a real directory"
                )
            continue
        directory.mkdir(mode=0o700)
    if journal_path.exists() or journal_path.is_symlink():
        raise Px4ServiceError(
            "MAVSDK command audit journal target path must not preexist"
        )
    return journal_path


def mavsdk_server_argv(
    executable: str,
    *,
    grpc_port: int,
    udp_port: int,
    incoming_heartbeat_timeout: Decimal,
    command_audit_journal_path: Path,
) -> tuple[str, ...]:
    if incoming_heartbeat_timeout <= 0 or not incoming_heartbeat_timeout.is_finite():
        raise Px4ServiceError("incoming heartbeat timeout must be positive and finite")
    journal_path = _absolute_normalized_path(
        str(command_audit_journal_path), label="command_audit_journal_path"
    )
    return (
        executable,
        "-p",
        str(grpc_port),
        "--incoming-heartbeat-timeout-s",
        _plain_decimal(incoming_heartbeat_timeout),
        "--command-audit-journal-path",
        str(journal_path),
        f"udpin://0.0.0.0:{udp_port}",
    )


def px4_instance(system_id: int) -> int:
    return system_id - 1


def pose_csv(vehicle: Mapping[str, Any]) -> str:
    pose = vehicle["initial_pose"]
    return (
        f"{pose['x_m']},{pose['y_m']},{pose['z_m']},"
        f"{pose['roll_rad']},{pose['pitch_rad']},{pose['yaw_rad']}"
    )


def px4_vehicle_environment(
    vehicle: Mapping[str, Any],
    *,
    world_name: str,
    work_dir: Path,
    seed: int | None,
) -> dict[str, str]:
    environment = {
        "PX4_GZ_STANDALONE": "1",
        "PX4_GZ_WORLD": world_name,
        "PX4_GZ_MODEL_NAME": str(vehicle["gazebo_model_name"]),
        "PX4_GZ_NO_FOLLOW": "1",
        "PX4_SIM_MODEL": str(vehicle["model"]),
        "PX4_SYS_AUTOSTART": str(vehicle["sys_autostart"]),
        "PX4_SIMULATOR": "gz",
        "HEADLESS": "1",
        "HOME": str(work_dir),
    }
    if seed is not None:
        environment["PX4_SEED"] = str(seed)
    return environment


def px4_mavlink_script(vehicle: Mapping[str, Any]) -> str:
    return (
        "#!/bin/sh\n"
        "mavlink start -x -u "
        f"{vehicle['px4_mavlink_udp_port']} -r 4000000 -f -m onboard "
        f"-o {vehicle['mavsdk_udp_port']}\n"
        f"param set MAV_SYS_ID {vehicle['system_id']}\n"
    )


def quaternion_to_rpy(
    x: float, y: float, z: float, w: float
) -> tuple[float, float, float]:
    try:
        return frame_math.quaternion_to_rpy(
            frame_math.UnitQuaternion(w=w, x=x, y=y, z=z)
        )
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError("quaternion-to-RPY conversion failed") from exc


def _inspection_geometry(
    *,
    vehicle_pose: Mapping[str, Any],
    camera_mount: Mapping[str, Any],
    target_position: Mapping[str, Any],
) -> tuple[float, float, str, str]:
    try:
        vehicle_transform = frame_math.RigidTransform(
            rotation=frame_math.rpy_to_quaternion(
                roll_rad=_finite_number(
                    vehicle_pose["roll_rad"], label="vehicle_pose.roll_rad"
                ),
                pitch_rad=_finite_number(
                    vehicle_pose["pitch_rad"], label="vehicle_pose.pitch_rad"
                ),
                yaw_rad=_finite_number(
                    vehicle_pose["yaw_rad"], label="vehicle_pose.yaw_rad"
                ),
            ).to_rotation_matrix(),
            translation=frame_math.Vector3(
                *(
                    _finite_number(vehicle_pose[name], label=f"vehicle_pose.{name}")
                    for name in ("x_m", "y_m", "z_m")
                )
            ),
        )
        mount_transform = frame_math.RigidTransform(
            rotation=frame_math.rpy_to_quaternion(
                roll_rad=_finite_number(
                    camera_mount["roll_rad"], label="camera_mount.roll_rad"
                ),
                pitch_rad=_finite_number(
                    camera_mount["pitch_rad"], label="camera_mount.pitch_rad"
                ),
                yaw_rad=_finite_number(
                    camera_mount["yaw_rad"], label="camera_mount.yaw_rad"
                ),
            ).to_rotation_matrix(),
            translation=frame_math.Vector3(
                *(
                    _finite_number(camera_mount[name], label=f"camera_mount.{name}")
                    for name in ("x_m", "y_m", "z_m")
                )
            ),
        )
        camera_transform = vehicle_transform.compose(mount_transform)
        target = frame_math.Vector3(
            *(
                _finite_number(target_position[name], label=f"target_position.{name}")
                for name in ("x_m", "y_m", "z_m")
            )
        )
        to_target = target - camera_transform.translation
        distance_m = to_target.norm()
        camera_forward = camera_transform.apply_vector(
            frame_math.Vector3(1.0, 0.0, 0.0)
        )
        if distance_m <= 0.0:
            raise Px4ServiceError(
                "inspection target must differ from the camera position"
            )
        cosine = camera_forward.dot(to_target) / (camera_forward.norm() * distance_m)
        view_angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError("inspection geometry transform failed") from exc
    camera_pose = {
        "x_m": camera_transform.translation.x,
        "y_m": camera_transform.translation.y,
        "z_m": camera_transform.translation.z,
        "rotation_matrix": [list(row) for row in camera_transform.rotation.rows],
    }
    normalized_target = {
        "x_m": target.x,
        "y_m": target.y,
        "z_m": target.z,
    }
    return (
        distance_m,
        view_angle_deg,
        _digest_json(camera_pose),
        _digest_json(normalized_target),
    )


def _point_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    try:
        left_vector = frame_math.Vector3(
            *(
                _finite_number(left[name], label=f"left.{name}")
                for name in ("x_m", "y_m", "z_m")
            )
        )
        right_vector = frame_math.Vector3(
            *(
                _finite_number(right[name], label=f"right.{name}")
                for name in ("x_m", "y_m", "z_m")
            )
        )
        return (left_vector - right_vector).norm()
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError("inspection trajectory distance is invalid") from exc


def _rpy_quaternion(pose: Mapping[str, Any]) -> dict[str, float]:
    try:
        quaternion = frame_math.rpy_to_quaternion(
            roll_rad=_finite_number(pose["roll_rad"], label="pose.roll_rad"),
            pitch_rad=_finite_number(pose["pitch_rad"], label="pose.pitch_rad"),
            yaw_rad=_finite_number(pose["yaw_rad"], label="pose.yaw_rad"),
        )
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError("RPY-to-quaternion conversion failed") from exc
    return {
        "qw": quaternion.w,
        "qx": quaternion.x,
        "qy": quaternion.y,
        "qz": quaternion.z,
    }


def ensure_paused_world_sdf(
    source: Path,
    destination: Path,
    *,
    vehicles: Sequence[Mapping[str, Any]] = (),
    inspections: Sequence[Mapping[str, Any]] = (),
    target_model_sdfs: Sequence[Path] = (),
    airspace_transition: Mapping[str, Any] | None = None,
) -> Path:
    text = source.read_text(encoding="utf-8")
    root = ElementTree.fromstring(text)
    world = _world_element(root)
    paused = _child_by_local_name(world, "paused")
    if paused is None:
        paused = ElementTree.SubElement(world, "paused")
    paused.text = "true"
    if len(inspections) != len(target_model_sdfs):
        raise Px4ServiceError(
            "Inspection world requires one SDF asset per target observation"
        )
    existing_model_names: set[str] = set()
    for child in world:
        kind = _local_name(child.tag)
        if kind == "model":
            name = child.attrib.get("name")
        elif kind == "include":
            name_element = _child_by_local_name(child, "name")
            name = None if name_element is None else (name_element.text or "").strip()
        else:
            continue
        if name:
            existing_model_names.add(name)
    for vehicle in vehicles:
        model_name = _string(
            vehicle.get("gazebo_model_name"),
            label="vehicle Gazebo model name",
            pattern=IDENTIFIER,
        )
        if model_name in existing_model_names:
            raise Px4ServiceError(
                f"Gazebo world already contains vehicle {model_name!r}"
            )
        world.append(_vehicle_model_include(vehicle))
        existing_model_names.add(model_name)
    for inspection, target_model_sdf in zip(
        inspections, target_model_sdfs, strict=True
    ):
        model_name = _string(
            inspection.get("gazebo_target_model_name"),
            label="inspection target Gazebo model name",
            pattern=IDENTIFIER,
        )
        if model_name in existing_model_names:
            raise Px4ServiceError(
                f"Gazebo world already contains Inspection target {model_name!r}"
            )
        world.append(
            _inspection_target_model(
                target_model_sdf,
                target_position=inspection["target_position"],
                model_name=model_name,
            )
        )
        existing_model_names.add(model_name)
    if airspace_transition is not None:
        if any(
            _local_name(child.tag) == "plugin"
            and child.attrib.get("name") == "aero_bench::gazebo::AirspaceTransition"
            for child in world
        ):
            raise Px4ServiceError(
                "Gazebo world already contains the airspace transition plugin"
            )
        _ensure_gazebo_server_system_plugins(world)
        monitored_models = tuple(vehicle["gazebo_model_name"] for vehicle in vehicles)
        if not monitored_models:
            monitored_models = (airspace_transition["incident_vehicle"],)
        for model_name in monitored_models:
            world.append(_airspace_transition_plugin({
                **airspace_transition,
                "incident_vehicle": model_name,
            }))
    destination.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(root).write(
        destination, encoding="utf-8", xml_declaration=True
    )
    return destination


def _ensure_gazebo_server_system_plugins(world: ElementTree.Element) -> None:
    expected_by_name = {
        name: filename for filename, name in GAZEBO_SERVER_SYSTEM_PLUGINS
    }
    expected_by_filename = {
        filename: name for filename, name in GAZEBO_SERVER_SYSTEM_PLUGINS
    }
    existing_by_name: dict[str, ElementTree.Element] = {}
    for child in world:
        if _local_name(child.tag) != "plugin":
            continue
        name = child.attrib.get("name")
        filename = child.attrib.get("filename")
        if name not in expected_by_name and filename not in expected_by_filename:
            continue
        if name is None or filename is None:
            raise Px4ServiceError("Gazebo server system plugin identity is incomplete")
        if expected_by_name.get(name) != filename or expected_by_filename.get(filename) != name:
            raise Px4ServiceError(
                f"Gazebo server system plugin identity conflicts with the production pin: {name}"
            )
        if name in existing_by_name:
            raise Px4ServiceError(f"Gazebo server system plugin repeats: {name}")
        existing_by_name[name] = child

    for filename, name in GAZEBO_SERVER_SYSTEM_PLUGINS:
        plugin = existing_by_name.get(name)
        if plugin is None:
            plugin = ElementTree.Element(
                "plugin", {"filename": filename, "name": name}
            )
            world.append(plugin)
        if name == GAZEBO_SENSORS_SYSTEM_NAME:
            render_engines = tuple(
                child
                for child in plugin
                if _local_name(child.tag) == "render_engine"
            )
            if not render_engines:
                ElementTree.SubElement(plugin, "render_engine").text = "ogre2"
            elif (
                len(render_engines) != 1
                or (render_engines[0].text or "").strip() != "ogre2"
            ):
                raise Px4ServiceError(
                    "Gazebo Sensors system render_engine conflicts with the production pin"
                )


def _airspace_transition_plugin(
    config: Mapping[str, Any],
) -> ElementTree.Element:
    fields = (
        "world_id", "world_digest", "region_id", "region_digest",
        "incident_vehicle", "transition_topic", "min_east_m", "max_east_m",
        "min_north_m", "max_north_m", "min_up_m", "max_up_m",
    )
    plugin = ElementTree.Element(
        "plugin",
        {
            "filename": "libaero_bench_gazebo_airspace_transition.so",
            "name": "aero_bench::gazebo::AirspaceTransition",
        },
    )
    for field in fields:
        ElementTree.SubElement(plugin, field).text = str(config[field])
    return plugin


def _vehicle_model_include(vehicle: Mapping[str, Any]) -> ElementTree.Element:
    model_name = _string(
        vehicle.get("gazebo_model_name"),
        label="vehicle Gazebo model name",
        pattern=IDENTIFIER,
    )
    resource = _string(
        vehicle.get("gazebo_resource"),
        label="vehicle Gazebo resource",
        pattern=IDENTIFIER,
    )
    initial_pose = _strict_object(
        vehicle.get("initial_pose"),
        required=frozenset({"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}),
        label="vehicle initial_pose",
    )
    values = tuple(
        _finite_number(initial_pose[name], label=f"vehicle.initial_pose.{name}")
        for name in ("x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad")
    )
    include = ElementTree.Element("include")
    ElementTree.SubElement(include, "uri").text = f"model://{resource}"
    ElementTree.SubElement(include, "name").text = model_name
    ElementTree.SubElement(include, "pose").text = " ".join(
        format(value, ".17g") for value in values
    )
    return include


def _inspection_target_model(
    source: Path,
    *,
    target_position: Mapping[str, Any],
    model_name: str,
) -> ElementTree.Element:
    try:
        root = ElementTree.fromstring(source.read_text(encoding="utf-8"))
    except (OSError, ElementTree.ParseError, UnicodeError) as exc:
        raise Px4ServiceError("inspection target model SDF is invalid") from exc
    if _local_name(root.tag) == "model":
        model = root
    elif _local_name(root.tag) == "sdf":
        models = tuple(child for child in root if _local_name(child.tag) == "model")
        if len(models) != 1:
            raise Px4ServiceError(
                "inspection target SDF must contain exactly one model"
            )
        model = models[0]
    else:
        raise Px4ServiceError("inspection target SDF root must be sdf or model")
    if model.attrib != {"name": "inspection_target_panel"}:
        raise Px4ServiceError(
            "inspection target SDF must name the pinned inspection_target_panel model"
        )
    model.attrib["name"] = _string(
        model_name,
        label="resolved inspection target Gazebo model name",
        pattern=IDENTIFIER,
    )
    if any(
        _local_name(element.tag) in {"include", "plugin"} for element in model.iter()
    ):
        raise Px4ServiceError(
            "inspection target SDF cannot include external resources or plugins"
        )
    static = _child_by_local_name(model, "static")
    if static is None or (static.text or "").strip() != "true":
        raise Px4ServiceError("inspection target SDF model must be static")
    if _child_by_local_name(model, "pose") is not None:
        raise Px4ServiceError(
            "inspection target SDF pose must come from ResolvedScenario"
        )
    position = tuple(
        _finite_number(target_position[name], label=f"target_position.{name}")
        for name in ("x_m", "y_m", "z_m")
    )
    pose = ElementTree.Element("pose")
    pose.text = " ".join(format(value, ".17g") for value in (*position, 0.0, 0.0, 0.0))
    model.insert(1, pose)
    return model


def _world_element(root: ElementTree.Element) -> ElementTree.Element:
    if _local_name(root.tag) == "world":
        return root
    for child in root:
        if _local_name(child.tag) == "world":
            return child
    raise Px4ServiceError("world SDF does not contain a world element")


def _child_by_local_name(
    parent: ElementTree.Element, name: str
) -> ElementTree.Element | None:
    for child in parent:
        if _local_name(child.tag) == name:
            return child
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _resolve_root_file(root: Path, reference: Mapping[str, str], *, label: str) -> Path:
    relative = PurePosixPath(reference["path"])
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise Px4ServiceError(
            f"{label} cannot use a symbolic link: {reference['path']}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise Px4ServiceError(f"{label} is unavailable: {reference['path']}") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise Px4ServiceError(f"{label} is not a regular file: {reference['path']}")
    digest = _digest_bytes(resolved.read_bytes())
    if digest != reference["sha256"]:
        raise Px4ServiceError(
            f"{label} SHA-256 mismatch for {reference['path']}: "
            f"expected {reference['sha256']}, got {digest}"
        )
    return resolved


def _scenario_world_sdf(
    *,
    scenario: ValidatedWorkloadScenario,
    provider_config: Mapping[str, Any],
    bundle_root: Path,
) -> tuple[str, Path]:
    raw_bindings = scenario.scenario["engine_frame_bindings"]
    if not isinstance(raw_bindings, list):
        raise Px4ServiceError("ResolvedScenario.engine_frame_bindings must be an array")
    bindings = []
    for index, value in enumerate(raw_bindings):
        label = f"ResolvedScenario.engine_frame_bindings[{index}]"
        binding = _strict_object(
            value,
            required=frozenset(
                {
                    "binding_id",
                    "provider_id",
                    "engine",
                    "scene_asset_id",
                    "source_frame_id",
                    "target_frame_id",
                    "translation_m",
                    "rotation_matrix",
                    "rotation_quaternion",
                }
            ),
            label=label,
        )
        if (
            binding["binding_id"] == provider_config["engine_binding_id"]
            and binding["provider_id"] == provider_config["provider_id"]
            and binding["engine"] == "gazebo"
        ):
            bindings.append(binding)
    if len(bindings) != 1:
        raise Px4ServiceError(
            "Px4GazeboConfig engine_binding_id is not one unique Gazebo scenario binding"
        )
    scene_asset_id = _string(
        bindings[0]["scene_asset_id"],
        label="ResolvedScenario Gazebo scene_asset_id",
        pattern=IDENTIFIER,
    )
    asset = scenario.asset(scene_asset_id)
    if asset["source_kind"] != "world" or asset["world"] is None:
        raise Px4ServiceError("Gazebo scene_asset_id must reference a world asset")
    world = asset["world"]
    if world["asset_role"] != "other" or world["selector_fragment"] is not None:
        raise Px4ServiceError("Gazebo scene asset metadata is invalid")
    source = _resolve_root_file(
        bundle_root, asset["file"], label="ResolvedScenario Gazebo scene asset"
    )
    try:
        root = ElementTree.fromstring(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ElementTree.ParseError) as exc:
        raise Px4ServiceError(
            "ResolvedScenario Gazebo scene asset is not valid SDF"
        ) from exc
    world_element = _world_element(root)
    world_name = _string(
        world_element.attrib.get("name"),
        label="Gazebo SDF world name",
        pattern=IDENTIFIER,
    )
    return world_name, source


def _parse_world_input_value(
    raw: object, *, label: str = "world_input"
) -> dict[str, str]:
    """Parse the strict digest-pinned bundle-world input declaration.

    ``world_input`` names the exact derived-input destination the executor
    staged on the non-host input volume (e.g. ``bundle/facility/
    logistics_world.sdf``) plus its SHA-256 identity.  The value is normalized
    to a strict, bounded shape so every consumer (startup identity load,
    prepare binds declaration, live-binary preflight) agrees on one contract.
    """
    world_input = _strict_object(
        raw,
        required=frozenset({"schema_version", "source", "path", "sha256"}),
        label=label,
    )
    schema = _string(
        world_input["schema_version"],
        label=f"{label}.schema_version",
        pattern=re.compile(r"^aero-bench\.px4-gazebo/bundle-world/v1$"),
    )
    source = _string(world_input["source"], label=f"{label}.source")
    path = _string(world_input["path"], label=f"{label}.path")
    digest = _sha256(world_input["sha256"], label=f"{label}.sha256")
    if schema != BUNDLE_WORLD_SCHEMA:
        raise Px4ServiceError(f"{label} schema_version is unsupported")
    if source != "bundle":
        raise Px4ServiceError(f"{label} source must be 'bundle'")
    relative = PurePosixPath(path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) < 2
        or relative.parts[0] != "bundle"
        or BUNDLE_WORLD_PATH.fullmatch(path) is None
    ):
        raise Px4ServiceError(
            f"{label}.path must be a normalized bundle-relative SDF path"
        )
    return {
        "schema_version": schema,
        "source": source,
        "path": str(relative),
        "sha256": digest,
    }


def _bundle_world_sdf(
    *,
    provider_config: Mapping[str, Any],
    bundle_root: Path,
) -> tuple[str, Path]:
    """Resolve and verify the DECLARED bundle world from the input volume.

    A staged bundle world is accepted EXCLUSIVELY from the non-host input
    volume: the normalized ``bundle/...`` path resolves under ``bundle_root``
    (``AERO_BENCH_BUNDLE_DIR``), is symlink-checked, and its SHA-256 must equal
    the digest pinned in the provider config.  A missing or tampered staged
    world raises ``Px4ServiceError``; there is no image/scenario fallback for a
    declared bundle world.  The staged SDF's ``<world>`` name must exist and
    match ``Px4GazeboConfig.world_name``.
    """
    parsed = _parse_world_input_value(
        provider_config.get("world_input"), label="Px4GazeboConfig.world_input"
    )
    configured_world_name = provider_config.get("world_name")
    if configured_world_name is None:
        raise Px4ServiceError(
            "bundle world input requires an explicit Px4GazeboConfig.world_name"
        )
    bundle_relative = PurePosixPath(*parsed["path"].split("/")[1:])
    source = _resolve_root_file(
        bundle_root,
        {"path": str(bundle_relative), "sha256": parsed["sha256"]},
        label="logistics bundle world SDF",
    )
    try:
        root = ElementTree.fromstring(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ElementTree.ParseError) as exc:
        raise Px4ServiceError(
            "logistics bundle world SDF is not valid SDF"
        ) from exc
    world_element = _world_element(root)
    world_name = _string(
        world_element.attrib.get("name"),
        label="Gazebo bundle world name",
        pattern=IDENTIFIER,
    )
    if world_name != configured_world_name:
        raise Px4ServiceError(
            "Px4GazeboConfig.world_name must match the staged bundle world SDF "
            f"name: configured={configured_world_name!r}, staged={world_name!r}"
        )
    return world_name, source


def _scenario_vehicles(
    *, scenario: ValidatedWorkloadScenario, provider_config: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    raw_entities = scenario.scenario["entities"]
    if not isinstance(raw_entities, list):
        raise Px4ServiceError("ResolvedScenario.entities must be an array")
    entities = tuple(
        _scenario_entity(value, label=f"ResolvedScenario.entities[{index}]")
        for index, value in enumerate(raw_entities)
    )
    owned = {
        entity["entity_id"]: entity
        for entity in entities
        if entity["owner_kind"] == "provider"
        and entity["owner_id"] == provider_config["provider_id"]
        and entity["kind"] == "uav"
        and entity["authority_kind"] == "gazebo_physics"
        and entity["state"] == "dynamic"
    }
    configured = {
        str(vehicle["vehicle_id"]): dict(vehicle)
        for vehicle in provider_config["vehicles"]
    }
    if set(configured) != set(owned):
        raise Px4ServiceError(
            "Px4GazeboConfig vehicle logical IDs differ from scenario-owned UAVs"
        )
    resolved: list[dict[str, Any]] = []
    pose_fields = ("x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad")
    for vehicle_id in sorted(configured):
        configured_vehicle = configured[vehicle_id]
        scenario_pose = owned[vehicle_id]["initial_pose"]
        configured_pose = configured_vehicle.get("initial_pose")
        if configured_pose is not None and any(
            not math.isclose(
                float(configured_pose[field]),
                float(scenario_pose[field]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            for field in pose_fields
        ):
            raise Px4ServiceError(
                f"Px4GazeboConfig initial_pose differs from ResolvedScenario: {vehicle_id}"
            )
        resolved.append(
            {
                **configured_vehicle,
                "initial_pose": (
                    scenario_pose if configured_pose is None else configured_pose
                ),
            }
        )
    return tuple(resolved)


def _gazebo_world_model_names(source: Path) -> tuple[str, ...]:
    try:
        root = ElementTree.fromstring(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ElementTree.ParseError) as exc:
        raise Px4ServiceError(
            "ResolvedScenario Gazebo scene asset is not valid SDF"
        ) from exc
    world = _world_element(root)
    names: list[str] = []
    for child in world:
        kind = _local_name(child.tag)
        if kind == "model":
            name = child.attrib.get("name")
            if not isinstance(name, str) or not name:
                raise Px4ServiceError("Gazebo world model omits its name")
            names.append(name)
            continue
        if kind != "include":
            continue
        declared_name = _child_by_local_name(child, "name")
        if declared_name is not None and (declared_name.text or "").strip():
            names.append((declared_name.text or "").strip())
            continue
        uri = _child_by_local_name(child, "uri")
        uri_text = "" if uri is None else (uri.text or "").strip().rstrip("/")
        if not uri_text:
            raise Px4ServiceError("Gazebo world include has no resolvable model name")
        names.append(uri_text.rsplit("/", 1)[-1])
    if len(names) != len(set(names)):
        raise Px4ServiceError("Gazebo world repeats a top-level model name")
    return tuple(sorted(names))


def _scenario_contact_model_tokens(
    *,
    scenario: ValidatedWorkloadScenario,
    vehicles: tuple[dict[str, Any], ...],
    world_source_sdf: Path,
    inspections: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    aliases: dict[str, str] = {}

    def bind(model_name: object, token: str, *, label: str) -> None:
        name = _string(model_name, label=label, pattern=IDENTIFIER)
        existing = aliases.get(name)
        if existing is not None and existing != token:
            raise Px4ServiceError(
                f"Gazebo contact model {name!r} has conflicting scenario identities"
            )
        aliases[name] = token

    for vehicle in vehicles:
        vehicle_id = _string(
            vehicle["vehicle_id"], label="vehicle_id", pattern=IDENTIFIER
        )
        bind(
            vehicle["gazebo_model_name"],
            f"vehicle.{vehicle_id}",
            label=f"Gazebo model name for {vehicle_id}",
        )

    raw_launch_sites = scenario.scenario["launch_sites"]
    if not isinstance(raw_launch_sites, list):
        raise Px4ServiceError("ResolvedScenario.launch_sites must be an array")
    for index, value in enumerate(raw_launch_sites):
        launch = _strict_object(
            value,
            required=frozenset(
                {
                    "launch_site_id",
                    "primary_uav_entity_id",
                    "allowed_uav_entity_ids",
                    "pose",
                    "pad_radius_m",
                    "selected",
                }
            ),
            label=f"ResolvedScenario.launch_sites[{index}]",
        )
        launch_id = _string(
            launch["launch_site_id"], label="launch_site_id", pattern=IDENTIFIER
        )
        bind(launch_id, f"launch_pad.{launch_id}", label="launch_site_id")

    raw_targets = scenario.scenario["semantic_targets"]
    if not isinstance(raw_targets, list):
        raise Px4ServiceError("ResolvedScenario.semantic_targets must be an array")
    for index, value in enumerate(raw_targets):
        if not isinstance(value, dict):
            raise Px4ServiceError(
                f"ResolvedScenario.semantic_targets[{index}] must be an object"
            )
        target_id = _string(
            value.get("target_id"), label="target_id", pattern=IDENTIFIER
        )
        bind(target_id, f"target.{target_id}", label="target_id")

    raw_buildings = scenario.scenario["buildings"]
    if not isinstance(raw_buildings, list):
        raise Px4ServiceError("ResolvedScenario.buildings must be an array")
    building_entity_ids: set[str] = set()
    for index, value in enumerate(raw_buildings):
        if not isinstance(value, dict):
            raise Px4ServiceError(
                f"ResolvedScenario.buildings[{index}] must be an object"
            )
        building_id = _string(
            value.get("building_id"), label="building_id", pattern=IDENTIFIER
        )
        entity_id = _string(
            value.get("entity_id"), label="building.entity_id", pattern=IDENTIFIER
        )
        building_entity_ids.add(entity_id)
        token = f"obstacle.{building_id}"
        bind(building_id, token, label="building_id")
        bind(entity_id, token, label="building.entity_id")

    raw_entities = scenario.scenario["entities"]
    if not isinstance(raw_entities, list):
        raise Px4ServiceError("ResolvedScenario.entities must be an array")
    for index, value in enumerate(raw_entities):
        entity = _scenario_entity(value, label=f"ResolvedScenario.entities[{index}]")
        entity_id = str(entity["entity_id"])
        if entity_id in building_entity_ids or entity_id in aliases:
            continue
        kind = entity["kind"]
        if kind in {"uav", "ugv"}:
            token = f"vehicle.{entity_id}"
        elif kind == "static_asset":
            token = f"obstacle.{entity_id}"
        else:
            token = f"other.{entity_id}"
        bind(entity_id, token, label="entity_id")

    for inspection in inspections:
        target_id = _string(
            inspection["target_id"],
            label="inspection.target_id",
            pattern=IDENTIFIER,
        )
        bind(
            inspection["gazebo_target_model_name"],
            f"target.{target_id}",
            label="inspection target Gazebo model",
        )

    for model_name in _gazebo_world_model_names(world_source_sdf):
        if model_name in {"ground", "ground_plane"}:
            bind(model_name, "ground.world", label="Gazebo ground model")
        elif model_name not in aliases and IDENTIFIER.fullmatch(model_name) is not None:
            bind(model_name, f"other.{model_name}", label="Gazebo world model")

    if not any(token == "ground.world" for token in aliases.values()):
        raise Px4ServiceError(
            "Gazebo scene must declare ground or ground_plane for contact authority"
        )
    return tuple(sorted(aliases.items()))


def _relative_pose(
    *, parent: Mapping[str, float], child: Mapping[str, float], label: str
) -> dict[str, float]:
    try:
        parent_transform = frame_math.RigidTransform(
            rotation=frame_math.rpy_to_quaternion(
                roll_rad=parent["roll_rad"],
                pitch_rad=parent["pitch_rad"],
                yaw_rad=parent["yaw_rad"],
            ).to_rotation_matrix(),
            translation=frame_math.Vector3(parent["x_m"], parent["y_m"], parent["z_m"]),
        )
        child_transform = frame_math.RigidTransform(
            rotation=frame_math.rpy_to_quaternion(
                roll_rad=child["roll_rad"],
                pitch_rad=child["pitch_rad"],
                yaw_rad=child["yaw_rad"],
            ).to_rotation_matrix(),
            translation=frame_math.Vector3(child["x_m"], child["y_m"], child["z_m"]),
        )
        relative = parent_transform.inverse().compose(child_transform)
        roll, pitch, yaw = frame_math.quaternion_to_rpy(
            frame_math.UnitQuaternion.from_rotation_matrix(relative.rotation)
        )
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError(f"{label} relative transform is invalid") from exc
    return {
        "x_m": relative.translation.x,
        "y_m": relative.translation.y,
        "z_m": relative.translation.z,
        "roll_rad": roll,
        "pitch_rad": pitch,
        "yaw_rad": yaw,
    }


def _scenario_cameras(
    *,
    scenario: ValidatedWorkloadScenario,
) -> tuple[dict[str, Any], ...]:
    """Urban camera declarations are intentionally empty.

    The browser renders recorded SceneState trajectories; camera capture remains
    an Inspection-only capability.
    """

    task = scenario.scenario.get("task")
    if not isinstance(task, dict):
        raise Px4ServiceError("ResolvedScenario.task must be an object")
    if task.get("package_id") != "urban.uav-recovery-demo.v1":
        return ()
    # Urban visualization is rendered from recorded SceneState trajectories;
    # RGB sensors remain an Inspection-only concern.
    return ()


def _scenario_urban_camera_observations(
    *,
    scenario: ValidatedWorkloadScenario,
    provider_id: str,
    vehicles: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    task = scenario.scenario.get("task")
    if not isinstance(task, dict) or task.get("package_id") != "urban.uav-recovery-demo.v1":
        return ()
    vehicle_ids = {str(vehicle["vehicle_id"]) for vehicle in vehicles}
    raw_bindings = task.get("observations")
    if not isinstance(raw_bindings, list):
        raise Px4ServiceError("urban recovery task observations must be an array")
    resolved: list[dict[str, Any]] = []
    for index, value in enumerate(raw_bindings):
        if not isinstance(value, dict):
            raise Px4ServiceError(
                f"ResolvedScenario.task.observations[{index}] must be an object"
            )
        binding = value
        if binding.get("endpoint_id") != provider_id or binding.get("urban") is None:
            continue
        projection_value = binding["urban"]
        if not isinstance(projection_value, dict):
            raise Px4ServiceError("urban recovery observation projection must be an object")
        projection = projection_value
        if (
            projection.get("projection_kind") != "urban_recovery"
            or projection.get("observation_kind") not in {"telemetry", "safety"}
            or projection.get("observation_id") != binding.get("observation_id")
        ):
            continue
        observation_kind = str(projection["observation_kind"])
        vehicle_id = _string(
            projection.get("vehicle_id"),
            label="urban recovery observation vehicle_id",
            pattern=IDENTIFIER,
        )
        if vehicle_id not in vehicle_ids or projection.get("camera_id") is not None:
            raise Px4ServiceError(
                "urban telemetry or safety observation has invalid camera or vehicle authority"
            )
        resolved.append(
            {
                "agent_id": _string(
                    binding.get("agent_id"),
                    label="urban recovery observation agent_id",
                    pattern=IDENTIFIER,
                ),
                "observation_id": _string(
                    binding.get("observation_id"),
                    label="urban recovery observation_id",
                    pattern=IDENTIFIER,
                ),
                "observation_kind": observation_kind,
                "vehicle_id": vehicle_id,
                "schema_file": binding["schema_file"],
            }
        )
    resolved.sort(key=lambda item: str(item["observation_id"]))
    if len({item["observation_id"] for item in resolved}) != len(resolved):
        raise Px4ServiceError("urban recovery observation IDs must be unique")
    expected_vehicle_ids = vehicle_ids
    for kind in ("telemetry", "safety"):
        observations = tuple(
            item for item in resolved if item["observation_kind"] == kind
        )
        if len(observations) != len(expected_vehicle_ids) or {
            item["vehicle_id"] for item in observations
        } != expected_vehicle_ids:
            raise Px4ServiceError(
                f"urban recovery must grant one {kind} observation per UAV"
            )
    return tuple(resolved)


def _scenario_flight_observations(
    *,
    scenario: ValidatedWorkloadScenario,
    provider_id: str,
    vehicles: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """Resolve package-independent, explicitly granted flight sensor streams."""

    task = scenario.scenario.get("task")
    if not isinstance(task, dict):
        raise Px4ServiceError("ResolvedScenario.task must be an object")
    raw_bindings = task.get("observations")
    if not isinstance(raw_bindings, list):
        raise Px4ServiceError("ResolvedScenario.task.observations must be an array")
    vehicle_ids = {str(vehicle["vehicle_id"]) for vehicle in vehicles}
    resolved: list[dict[str, Any]] = []
    for index, value in enumerate(raw_bindings):
        if not isinstance(value, dict):
            raise Px4ServiceError(
                f"ResolvedScenario.task.observations[{index}] must be an object"
            )
        if value.get("endpoint_id") != provider_id or value.get("flight") is None:
            continue
        projection = _strict_object(
            value["flight"],
            required=frozenset(
                {
                    "projection_kind",
                    "observation_id",
                    "observation_kind",
                    "vehicle_id",
                }
            ),
            label=f"ResolvedScenario.task.observations[{index}].flight",
        )
        observation_kind = _string(
            projection["observation_kind"],
            label="flight observation_kind",
        )
        if observation_kind not in {"telemetry", "gnss"}:
            raise Px4ServiceError("flight observation_kind is unsupported")
        vehicle_id = _string(
            projection["vehicle_id"],
            label="flight observation vehicle_id",
            pattern=IDENTIFIER,
        )
        observation_id = _string(
            value.get("observation_id"),
            label="flight observation_id",
            pattern=IDENTIFIER,
        )
        if (
            projection["projection_kind"] != "flight"
            or projection["observation_id"] != observation_id
            or observation_id != f"flight.{observation_kind}.{vehicle_id}"
            or vehicle_id not in vehicle_ids
        ):
            raise Px4ServiceError(
                "flight observation identity differs from provider authority"
            )
        resolved.append(
            {
                "agent_id": _string(
                    value.get("agent_id"),
                    label="flight observation agent_id",
                    pattern=IDENTIFIER,
                ),
                "observation_id": observation_id,
                "observation_kind": observation_kind,
                "vehicle_id": vehicle_id,
                "schema_file": _file_ref(
                    value.get("schema_file"),
                    label="flight observation schema_file",
                ),
            }
        )
    resolved.sort(key=lambda item: str(item["observation_id"]))
    if len({item["observation_id"] for item in resolved}) != len(resolved):
        raise Px4ServiceError("flight observation IDs must be unique")
    return tuple(resolved)


def _scenario_inspections(
    *,
    scenario: ValidatedWorkloadScenario,
    provider_id: str,
    vehicles: tuple[dict[str, Any], ...],
    bundle_root: Path,
) -> tuple[tuple[dict[str, Any], ...], tuple[Path, ...]]:
    task = scenario.scenario["task"]
    if isinstance(task, dict) and task.get("package_id") == "urban.uav-recovery-demo.v1":
        return (), ()
    raw_bindings = task["observations"]
    if not isinstance(raw_bindings, list):
        raise Px4ServiceError("ResolvedScenario.task.observations must be an array")
    bindings: list[dict[str, Any]] = []
    for index, value in enumerate(raw_bindings):
        label = f"ResolvedScenario.task.observations[{index}]"
        binding = _strict_object(
            value,
            required=frozenset(
                {
                    "binding_id",
                    "agent_id",
                    "observation_id",
                    "endpoint_id",
                    "schema_file",
                    "timeout_ms",
                    "inspection",
                    "urban",
                    "flight",
                }
            ),
            label=label,
        )
        if (
            binding["endpoint_id"] == provider_id
            and binding.get("inspection") is not None
        ):
            bindings.append(binding)
    resolved = tuple(
        sorted(
            (
                _scenario_inspection_binding(
                    scenario=scenario,
                    provider_id=provider_id,
                    vehicles=vehicles,
                    bundle_root=bundle_root,
                    binding=binding,
                )
                for binding in bindings
            ),
            key=lambda item: str(item[0]["observation_id"]),
        )
    )
    inspections = tuple(item[0] for item in resolved)
    model_paths = tuple(item[1] for item in resolved)
    observation_ids = tuple(str(item["observation_id"]) for item in inspections)
    if observation_ids != tuple(sorted(set(observation_ids))):
        raise Px4ServiceError(
            "PX4 scenario camera observations must be sorted and unique"
        )
    return inspections, model_paths


def _scenario_inspection_binding(
    *,
    scenario: ValidatedWorkloadScenario,
    provider_id: str,
    vehicles: tuple[dict[str, Any], ...],
    bundle_root: Path,
    binding: Mapping[str, Any],
) -> tuple[dict[str, Any], Path]:
    observation_id = _string(
        binding["observation_id"],
        label="ResolvedScenario observation_id",
        pattern=IDENTIFIER,
    )
    metadata_schema = _file_ref(
        binding["schema_file"], label="ResolvedScenario observation schema_file"
    )

    projection = _strict_object(
        binding["inspection"],
        required=frozenset(
            {
                "projection_kind",
                "observation_id",
                "work_order_id",
                "target_id",
                "simulation_asset_id",
                "sensor_id",
                "media_type",
                "min_distance_m",
                "max_distance_m",
                "min_view_angle_deg",
                "max_view_angle_deg",
                "earliest_time_ns",
                "latest_time_ns",
            }
        ),
        label="ResolvedScenario inspection observation projection",
    )
    if (
        projection["projection_kind"] != "inspection"
        or projection["observation_id"] != observation_id
    ):
        raise Px4ServiceError("inspection observation projection identity is invalid")
    observation = {
        "target_id": projection["target_id"],
        "world_asset_id": projection["simulation_asset_id"],
    }
    trigger = {
        "observation_id": observation_id,
        "target_id": projection["target_id"],
        "provider_id": provider_id,
        "camera_id": projection["sensor_id"],
        "min_distance_m": projection["min_distance_m"],
        "max_distance_m": projection["max_distance_m"],
        "min_view_angle_deg": projection["min_view_angle_deg"],
        "max_view_angle_deg": projection["max_view_angle_deg"],
        "earliest_time_ns": projection["earliest_time_ns"],
        "latest_time_ns": projection["latest_time_ns"],
    }

    raw_targets = scenario.scenario["semantic_targets"]
    if not isinstance(raw_targets, list):
        raise Px4ServiceError("ResolvedScenario.semantic_targets must be an array")
    target_matches = [
        value
        for value in raw_targets
        if isinstance(value, dict)
        and value.get("target_id") == observation["target_id"]
    ]
    if len(target_matches) != 1:
        raise Px4ServiceError("inspection target is absent from ResolvedScenario")
    target = _strict_object(
        target_matches[0],
        required=frozenset(
            {
                "target_id",
                "parent_entity_id",
                "required_sensor_id",
                "pose",
                "geometry",
                "surface_normal_target",
                "min_distance_m",
                "max_distance_m",
                "max_view_angle_deg",
                "fov_margin_deg",
                "dwell_time_s",
                "required_evidence",
            }
        ),
        label="ResolvedScenario semantic target",
    )
    if (
        _finite_number(target["min_distance_m"], label="target.min_distance_m")
        != _finite_number(trigger["min_distance_m"], label="trigger.min_distance_m")
        or _finite_number(target["max_distance_m"], label="target.max_distance_m")
        != _finite_number(trigger["max_distance_m"], label="trigger.max_distance_m")
        or _finite_number(
            target["max_view_angle_deg"], label="target.max_view_angle_deg"
        )
        != _finite_number(
            trigger["max_view_angle_deg"], label="trigger.max_view_angle_deg"
        )
    ):
        raise Px4ServiceError(
            "inspection trigger geometry differs from scenario target"
        )

    raw_sensors = scenario.scenario["sensors"]
    if not isinstance(raw_sensors, list):
        raise Px4ServiceError("ResolvedScenario.sensors must be an array")
    sensor_matches = [
        value
        for value in raw_sensors
        if isinstance(value, dict)
        and value.get("sensor_id") == target["required_sensor_id"]
    ]
    if len(sensor_matches) != 1:
        raise Px4ServiceError("inspection sensor is absent from ResolvedScenario")
    sensor = _strict_object(
        sensor_matches[0],
        required=frozenset(
            {
                "sensor_id",
                "provider_id",
                "parent_entity_id",
                "kind",
                "initial_pose",
                "horizontal_fov_deg",
                "vertical_fov_deg",
                "resolution_width_px",
                "resolution_height_px",
            }
        ),
        label="ResolvedScenario inspection sensor",
    )
    if (
        sensor["provider_id"] != provider_id
        or sensor["kind"] != "camera"
        or trigger["camera_id"] != sensor["sensor_id"]
    ):
        raise Px4ServiceError("inspection camera binding differs from ResolvedScenario")
    vehicles_by_id = {str(vehicle["vehicle_id"]): vehicle for vehicle in vehicles}
    camera_vehicle = vehicles_by_id.get(sensor["parent_entity_id"])
    if camera_vehicle is None:
        raise Px4ServiceError("inspection camera parent is not a scenario PX4 vehicle")
    if (
        camera_vehicle["model"] != "gz_x500_mono_cam"
        or camera_vehicle["gazebo_resource"] != "x500_mono_cam"
    ):
        raise Px4ServiceError("inspection camera vehicle implementation is unsupported")
    camera_mount = _relative_pose(
        parent=camera_vehicle["initial_pose"],
        child=_resolved_pose(
            sensor["initial_pose"],
            label="ResolvedScenario inspection sensor.initial_pose",
        ),
        label="inspection camera mount",
    )

    raw_entities = scenario.scenario["entities"]
    if not isinstance(raw_entities, list):
        raise Px4ServiceError("ResolvedScenario.entities must be an array")
    entity_matches = [
        _scenario_entity(value, label=f"ResolvedScenario.entities[{index}]")
        for index, value in enumerate(raw_entities)
        if isinstance(value, dict)
        and value.get("entity_id") == target["parent_entity_id"]
    ]
    if len(entity_matches) != 1:
        raise Px4ServiceError("inspection target parent entity is absent")
    target_entity = entity_matches[0]
    target_model_asset_id = _string(
        observation["world_asset_id"],
        label="inspection world_asset_id",
        pattern=IDENTIFIER,
    )
    if target_entity["owner_kind"] != "scenario" or target_entity["state"] != "static":
        raise Px4ServiceError("inspection target is not a compiler-owned static entity")
    target_asset = scenario.asset(target_model_asset_id)
    target_file = target_asset["file"]
    if target_asset["source_kind"] != "task" or not str(target_file["path"]).endswith(
        ".sdf"
    ):
        raise Px4ServiceError(
            "inspection simulation asset must be a provider-private task SDF"
        )
    target_model_path = _resolve_root_file(
        bundle_root, target_file, label="inspection target scenario asset"
    )

    inspection = _inspection_config(
        {
            "agent_id": _string(
                binding["agent_id"],
                label="ResolvedScenario inspection agent_id",
                pattern=IDENTIFIER,
            ),
            "work_order_id": projection["work_order_id"],
            "target_id": target["target_id"],
            "target_position": {
                key: value
                for key, value in _resolved_pose(
                    target["pose"], label="ResolvedScenario semantic target.pose"
                ).items()
                if key in {"x_m", "y_m", "z_m"}
            },
            "target_position_wgs84": _resolved_pose_wgs84(
                target["pose"], label="ResolvedScenario semantic target.pose"
            ),
            "target_model_asset_id": target_model_asset_id,
            "observation_id": observation_id,
            "camera_id": sensor["sensor_id"],
            "camera_vehicle_id": sensor["parent_entity_id"],
            "camera_mount": camera_mount,
            "resolution_width_px": sensor["resolution_width_px"],
            "resolution_height_px": sensor["resolution_height_px"],
            "metadata_schema": metadata_schema,
            "media_type": projection["media_type"],
            "min_distance_m": trigger["min_distance_m"],
            "max_distance_m": trigger["max_distance_m"],
            "min_view_angle_deg": trigger["min_view_angle_deg"],
            "max_view_angle_deg": trigger["max_view_angle_deg"],
            "earliest_time_ns": trigger["earliest_time_ns"],
            "latest_time_ns": trigger["latest_time_ns"],
        }
    )
    if inspection is None:
        raise Px4ServiceError(
            "scenario inspection projection unexpectedly resolved null"
        )
    inspection["gazebo_target_model_name"] = (
        f"inspection_target.{inspection['target_id']}"
    )
    return inspection, target_model_path


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    run_id: str
    seed: int
    provider_id: str
    provider_port: int
    runtime_image: str
    config_digest: str
    scenario_digest: str
    scenario: ValidatedWorkloadScenario
    bundle_root: Path
    artifact_requirements: tuple[dict[str, str | int | None], ...]
    clock_step_ns: int
    provider_config: dict[str, Any]
    world_name: str
    world_source_sdf: Path
    vehicles: tuple[dict[str, Any], ...]
    inspections: tuple[dict[str, Any], ...]
    inspection_target_model_sdfs: tuple[Path, ...]
    airspace_transition: dict[str, Any] | None = None
    camera_declarations: tuple[dict[str, Any], ...] = ()
    urban_camera_observations: tuple[dict[str, Any], ...] = ()
    flight_observations: tuple[dict[str, Any], ...] = ()
    clock_max_steps: int | None = None

    @property
    def adapter(self) -> str:
        return PROVIDER_ADAPTER


@dataclass(frozen=True, slots=True)
class MotionFrameAuthority:
    enu_transform: frame_math.EnuTransform
    geoid_grid: frame_math.ScalarGridSampler
    terrain_grid: frame_math.ScalarGridSampler
    geoid_interpolation: str
    terrain_interpolation: str

    def resolved_pose(self, live_pose: Mapping[str, object]) -> dict[str, object]:
        enu = frame_math.Vector3(
            _finite_number(live_pose.get("x_m"), label="Gazebo pose.x_m"),
            _finite_number(live_pose.get("y_m"), label="Gazebo pose.y_m"),
            _finite_number(live_pose.get("z_m"), label="Gazebo pose.z_m"),
        )
        ecef = self.enu_transform.ecef_from_enu(enu)
        longitude_deg, latitude_deg, ellipsoid_height_m = (
            self.enu_transform.enu_to_geodetic(enu)
        )
        try:
            geoid_separation_m = self.geoid_grid.sample(
                enu.x, enu.y, self.geoid_interpolation
            )
            terrain_amsl_m = self.terrain_grid.sample(
                enu.x, enu.y, self.terrain_interpolation
            )
            orientation_enu = frame_math.rpy_to_quaternion(
                roll_rad=_finite_number(
                    live_pose.get("roll_rad"), label="Gazebo pose.roll_rad"
                ),
                pitch_rad=_finite_number(
                    live_pose.get("pitch_rad"), label="Gazebo pose.pitch_rad"
                ),
                yaw_rad=_finite_number(
                    live_pose.get("yaw_rad"), label="Gazebo pose.yaw_rad"
                ),
            )
            orientation_ned = frame_math.UnitQuaternion.from_rotation_matrix(
                frame_math.ENU_TO_NED_ROTATION
            ).compose(orientation_enu)
        except frame_math.FrameMathError as exc:
            raise Px4ServiceError(
                "PX4 live pose cannot be resolved canonically"
            ) from exc
        amsl_m = ellipsoid_height_m - geoid_separation_m
        return {
            "position": {
                "enu": {"east_m": enu.x, "north_m": enu.y, "up_m": enu.z},
                "ned": {"north_m": enu.y, "east_m": enu.x, "down_m": -enu.z},
                "ecef": {"x_m": ecef.x, "y_m": ecef.y, "z_m": ecef.z},
                "wgs84": {
                    "longitude_deg": longitude_deg,
                    "latitude_deg": latitude_deg,
                    "ellipsoid_height_m": ellipsoid_height_m,
                },
                "geoid_separation_m": geoid_separation_m,
                "amsl_m": amsl_m,
                "terrain_amsl_m": terrain_amsl_m,
                "agl_m": amsl_m - terrain_amsl_m,
            },
            "orientation_enu": _quaternion_payload(orientation_enu),
            "orientation_ned": _quaternion_payload(orientation_ned),
        }


def _quaternion_payload(value: frame_math.UnitQuaternion) -> dict[str, float]:
    return {"qw": value.w, "qx": value.x, "qy": value.y, "qz": value.z}


def _load_scalar_grid(
    identity: WorkloadIdentity,
    *,
    asset_id: str,
    expected_role: str,
    label: str,
) -> frame_math.ScalarGridSampler:
    try:
        asset = identity.scenario.asset(asset_id)
    except WorkloadScenarioError as exc:
        raise Px4ServiceError(f"{label} asset is unavailable") from exc
    world = asset.get("world")
    if (
        asset.get("source_kind") != "world"
        or not isinstance(world, dict)
        or world.get("asset_role") != expected_role
        or world.get("media_type") != "application/json"
        or world.get("selector_fragment") is not None
    ):
        raise Px4ServiceError(f"{label} asset metadata is invalid")
    source = _resolve_root_file(identity.bundle_root, asset["file"], label=label)
    document = _strict_object(
        _parse_json(source.read_bytes()),
        required=frozenset(
            {
                "schema_version",
                "frame_id",
                "east_axis_m",
                "north_axis_m",
                "values_m",
            }
        ),
        label=label,
    )
    if (
        document["schema_version"] != "aero-bench.scalar-grid/v1"
        or document["frame_id"] != "ENU"
    ):
        raise Px4ServiceError(f"{label} schema or frame is unsupported")

    def axis(value: object, *, axis_label: str) -> tuple[float, ...]:
        if not isinstance(value, list) or len(value) < 2:
            raise Px4ServiceError(f"{axis_label} must contain at least two values")
        return tuple(
            _finite_number(item, label=f"{axis_label}[{index}]")
            for index, item in enumerate(value)
        )

    east_axis = axis(document["east_axis_m"], axis_label=f"{label}.east_axis_m")
    north_axis = axis(document["north_axis_m"], axis_label=f"{label}.north_axis_m")
    raw_values = document["values_m"]
    if not isinstance(raw_values, list):
        raise Px4ServiceError(f"{label}.values_m must be an array")
    values = tuple(
        tuple(
            _finite_number(item, label=f"{label}.values_m[{row_index}][{column_index}]")
            for column_index, item in enumerate(row)
        )
        if isinstance(row, list)
        else ()
        for row_index, row in enumerate(raw_values)
    )
    try:
        return frame_math.ScalarGridSampler(
            east_axis_m=east_axis,
            north_axis_m=north_axis,
            values_m=values,
        )
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError(f"{label} grid is invalid") from exc


def _motion_frame_authority(identity: WorkloadIdentity) -> MotionFrameAuthority:
    frame = _strict_object(
        identity.scenario.scenario["frame_authority"],
        required=frozenset(
            {
                "geodetic_frame_id",
                "ecef_frame_id",
                "enu_frame_id",
                "ned_frame_id",
                "origin",
                "origin_ecef",
                "ecef_to_enu_rotation",
                "enu_to_ecef_rotation",
                "enu_to_ned_rotation",
                "ned_to_enu_rotation",
                "spatial_extent",
                "geoid_correction_asset_id",
                "terrain_height_asset_id",
                "geoid_interpolation",
                "terrain_interpolation",
                "geoid_precision_m",
                "terrain_precision_m",
            }
        ),
        label="ResolvedScenario.frame_authority",
    )
    origin = _strict_object(
        frame["origin"],
        required=frozenset(
            {
                "enu",
                "ned",
                "ecef",
                "wgs84",
                "geoid_separation_m",
                "amsl_m",
                "terrain_amsl_m",
                "agl_m",
            }
        ),
        label="ResolvedScenario.frame_authority.origin",
    )
    origin_wgs84 = _strict_object(
        origin["wgs84"],
        required=frozenset({"longitude_deg", "latitude_deg", "ellipsoid_height_m"}),
        label="ResolvedScenario.frame_authority.origin.wgs84",
    )
    geoid_interpolation = _string(
        frame["geoid_interpolation"], label="frame_authority.geoid_interpolation"
    )
    terrain_interpolation = _string(
        frame["terrain_interpolation"], label="frame_authority.terrain_interpolation"
    )
    if geoid_interpolation not in {
        "nearest",
        "bilinear",
    } or terrain_interpolation not in {
        "nearest",
        "bilinear",
    }:
        raise Px4ServiceError("ResolvedScenario uses unsupported grid interpolation")
    try:
        enu_transform = frame_math.EnuTransform.from_origin(
            longitude_deg=_finite_number(
                origin_wgs84["longitude_deg"], label="frame origin longitude"
            ),
            latitude_deg=_finite_number(
                origin_wgs84["latitude_deg"], label="frame origin latitude"
            ),
            altitude_m=_finite_number(
                origin_wgs84["ellipsoid_height_m"],
                label="frame origin ellipsoid height",
            ),
        )
    except frame_math.FrameMathError as exc:
        raise Px4ServiceError("ResolvedScenario frame origin is invalid") from exc
    return MotionFrameAuthority(
        enu_transform=enu_transform,
        geoid_grid=_load_scalar_grid(
            identity,
            asset_id=_string(
                frame["geoid_correction_asset_id"],
                label="frame_authority.geoid_correction_asset_id",
                pattern=IDENTIFIER,
            ),
            expected_role="geoid_model",
            label="ResolvedScenario geoid scalar grid",
        ),
        terrain_grid=_load_scalar_grid(
            identity,
            asset_id=_string(
                frame["terrain_height_asset_id"],
                label="frame_authority.terrain_height_asset_id",
                pattern=IDENTIFIER,
            ),
            expected_role="terrain_model",
            label="ResolvedScenario terrain scalar grid",
        ),
        geoid_interpolation=geoid_interpolation,
        terrain_interpolation=terrain_interpolation,
    )


@dataclass(frozen=True, slots=True)
class PreparedConfig:
    provider_id: str
    run_id: str
    protocol_version: str
    runtime_image: str
    config_digest: str
    artifact_requirements: tuple[dict[str, str | int | None], ...]
    endpoint_host: str
    endpoint_port: int
    world_name: str
    world_sdf: str
    world_source_sdf: Path
    physics_step_ns: int
    step_length_ns: int
    px4_executable: str
    gazebo_executable: str
    mavsdk_server_executable: str
    vehicles: tuple[dict[str, Any], ...]
    required_commands: tuple[str, ...]
    command_timeout_ms: int
    physical_completion_policy: dict[str, Any]
    maximum_agent_decision_wall_time_ms: int
    heartbeat_timeout_fixed_margin_ms: int
    inspections: tuple[dict[str, Any], ...]
    inspection_target_model_sdfs: tuple[Path, ...]
    airspace_transition: dict[str, Any] | None = None
    world_input: dict[str, str] | None = None
    contact_model_tokens: tuple[tuple[str, str], ...] = ()
    camera_declarations: tuple[dict[str, Any], ...] = ()
    urban_camera_observations: tuple[dict[str, Any], ...] = ()
    flight_observations: tuple[dict[str, Any], ...] = ()

    @property
    def incoming_heartbeat_timeout_s(self) -> Decimal:
        return incoming_heartbeat_timeout_s(
            maximum_agent_decision_wall_time_ms=(
                self.maximum_agent_decision_wall_time_ms
            ),
            fixed_margin_ms=self.heartbeat_timeout_fixed_margin_ms,
        )


@dataclass(frozen=True, slots=True)
class VehicleTelemetry:
    vehicle_id: str
    pose_json: str
    position_wgs84_json: str
    velocity_json: str
    angular_velocity_json: str
    attitude_json: str
    flight_mode: str
    armed: bool
    in_air: bool
    landed: bool
    landed_state: str
    battery_percent: float
    health: str
    contacts: tuple[str, ...]
    ground_contact: bool
    collision_contact: bool
    simulation_time_ns: int

    def as_json(self) -> dict[str, object]:
        return {
            "vehicle_id": self.vehicle_id,
            "pose_json": self.pose_json,
            "position_wgs84_json": self.position_wgs84_json,
            "velocity_json": self.velocity_json,
            "angular_velocity_json": self.angular_velocity_json,
            "attitude_json": self.attitude_json,
            "flight_mode": self.flight_mode,
            "armed": self.armed,
            "in_air": self.in_air,
            "landed": self.landed,
            "landed_state": self.landed_state,
            "battery_percent": self.battery_percent,
            "health": self.health,
            "contacts": list(self.contacts),
            "ground_contact": self.ground_contact,
            "collision_contact": self.collision_contact,
            "simulation_time_ns": self.simulation_time_ns,
        }


@dataclass(frozen=True, slots=True)
class ParsedPhysicalState:
    vehicle_id: str
    tick: int
    sim_time_ns: int
    pose: dict[str, float]
    position_wgs84: dict[str, float]
    velocity_ned: dict[str, float]
    angular_velocity_body: dict[str, float]
    flight_mode: str
    armed: bool
    in_air: bool
    landed: bool
    landed_state: str
    contacts: tuple[str, ...]
    ground_contact: bool
    collision_contact: bool
    state_digest: str
    horizontal_speed_m_s: float
    vertical_speed_m_s: float
    total_speed_m_s: float


@dataclass(slots=True)
class UnresolvedCommandRecord:
    command_id: str
    tool_id: str
    vehicle_id: str
    arguments: dict[str, object]
    expected_mav_cmd: int
    expected_wire_type: str
    stage: str
    accepted_at: tuple[int, int]
    applied_at: tuple[int, int] | None
    baseline_state: ParsedPhysicalState
    hold_anchor: dict[str, float]
    deadline_sim_time_ns: int
    settle_start: tuple[int, int] | None
    settle_end: tuple[int, int] | None
    settle_count: int
    last_state_digest: str | None
    last_metrics: dict[str, object]
    failure_latched: bool
    command_audit_status: str | None
    command_audit: dict[str, object] | None


@dataclass(slots=True)
class CommandAuditJournalCursor:
    path: Path
    offset: int = 0
    last_seq: int = 0
    last_timestamp_ns: int = 0
    device: int | None = None
    inode: int | None = None
    trailing_bytes: bytes = b""


@dataclass(frozen=True, slots=True)
class CommandAuditTransaction:
    records: tuple[dict[str, object], ...]
    terminal_ack_ingress_record: dict[str, object] | None
    terminal_ack_disposition_record: dict[str, object] | None


class EvidenceWriter:
    def __init__(
        self,
        root: Path,
        requirement: Mapping[str, str | int | None],
        *,
        allowed_requirements: Sequence[Mapping[str, str | int | None]] | None = None,
    ):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise Px4ServiceError("PX4 artifact root must be a directory")
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise Px4ServiceError("PX4 artifact root is not writable")
        self.requirement = _artifact_requirement(
            requirement, label="PX4 evidence ArtifactRequirement"
        )
        allowed = allowed_requirements or (requirement,)
        self._allowed_relative_paths = tuple(
            _string(
                item.get("relative_path"),
                label="PX4 allowed ArtifactRequirement.relative_path",
            )
            for item in allowed
        )
        self.path = self._resolve_output_path()
        self._reject_undeclared_files()

    def _resolve_output_path(self) -> Path:
        relative_path = self.requirement["relative_path"]
        if not isinstance(relative_path, str):
            raise Px4ServiceError("PX4 artifact relative_path must be a string")
        relative = PurePosixPath(relative_path)
        candidate = self.root.joinpath(*relative.parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != self.root.parent
        ):
            raise Px4ServiceError("PX4 artifact path cannot contain a symbolic link")
        if candidate.exists() and not candidate.is_file():
            raise Px4ServiceError("PX4 artifact path must be a regular file")
        return candidate

    def _reject_undeclared_files(self) -> None:
        allowed_files = {
            self.root.joinpath(*PurePosixPath(relative).parts)
            for relative in self._allowed_relative_paths
        }
        allowed_directories = {self.root}
        for expected in allowed_files:
            allowed_directories.update(
                directory
                for directory in (expected.parent, *expected.parent.parents)
                if directory == self.root or directory.is_relative_to(self.root)
            )
        for candidate in self.root.rglob("*"):
            if candidate.is_symlink():
                raise Px4ServiceError("PX4 artifact root cannot contain symbolic links")
            if candidate.is_dir() and candidate not in allowed_directories:
                raise Px4ServiceError(
                    "PX4 artifact root contains an undeclared directory: "
                    f"{candidate.relative_to(self.root).as_posix()}"
                )
            if candidate.is_file() and candidate not in allowed_files:
                raise Px4ServiceError(
                    "PX4 artifact root contains an undeclared file: "
                    f"{candidate.relative_to(self.root).as_posix()}"
                )

    def _create_declared_parents(self) -> None:
        relative_parent = self.path.parent.relative_to(self.root)
        current = self.root
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise Px4ServiceError(
                        "PX4 artifact parent path is not a regular directory"
                    )
            else:
                current.mkdir()

    def reset(self) -> None:
        self._reject_undeclared_files()
        self._create_declared_parents()
        self.replace(b"")

    def replace(self, encoded: bytes) -> str:
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise Px4ServiceError("PX4 artifact max_size_bytes must be an integer")
        if len(encoded) > max_size_bytes:
            raise Px4ServiceError(
                "PX4 evidence artifact exceeds declared max_size_bytes"
            )
        self._create_declared_parents()
        temporary = self.path.with_name(self.path.name + ".tmp")
        if temporary.exists() or temporary.is_symlink():
            raise Px4ServiceError("PX4 evidence temporary path already exists")
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self._reject_undeclared_files()
        if self.path.stat().st_size != len(encoded):
            raise Px4ServiceError("PX4 evidence file size changed after atomic replace")
        return _digest_bytes(self.path.read_bytes())

    def append(self, encoded: bytes) -> str:
        """Append one already-framed record to a bounded evidence artifact.

        Appends are used only for the urban camera byte archive.  Each caller
        supplies a complete self-delimiting record; a crash therefore leaves an
        invalid archive that readiness/finalization must reject rather than
        silently dropping or repairing a frame.
        """
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise Px4ServiceError("PX4 artifact max_size_bytes must be an integer")
        self._reject_undeclared_files()
        self._create_declared_parents()
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size + len(encoded) > max_size_bytes:
            raise Px4ServiceError("PX4 evidence artifact exceeds declared max_size_bytes")
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        self._reject_undeclared_files()
        return _digest_bytes(self.path.read_bytes())

    def write_records(self, records: Sequence[Mapping[str, object]]) -> str:
        encoded = b"".join(_canonical_json(dict(record)) + b"\n" for record in records)
        return self.replace(encoded)

    def write_array(self, records: Sequence[Mapping[str, object]]) -> str:
        return self.replace(_canonical_json([dict(record) for record in records]))


def _read_fd_exact(fd: int, *, length: int) -> bytes:
    parts: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        parts.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(parts)
    if len(payload) != length:
        raise Px4ServiceError("MAVSDK command audit journal returned a short read")
    return payload


def _command_audit_hex_bytes(
    value: object,
    *,
    label: str,
    allow_empty: bool,
) -> bytes:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or HEX_BYTES.fullmatch(value) is None
    ):
        raise Px4ServiceError(f"{label} must be canonical lowercase byte hex")
    return bytes.fromhex(value)


def _mavlink_x25_checksum(payload: bytes, *, crc_extra: int) -> int:
    checksum = 0xFFFF
    for value in (*payload, crc_extra):
        temporary = value ^ (checksum & 0xFF)
        temporary ^= (temporary << 4) & 0xFF
        checksum = (
            (checksum >> 8) ^ (temporary << 8) ^ (temporary << 3) ^ (temporary >> 4)
        ) & UINT16_MAX
    return checksum


def _validate_command_audit_frame(
    record: Mapping[str, object],
    *,
    label: str,
    expected_message_id: int,
    expected_frame_encoding: str,
) -> bytes:
    frame = _command_audit_hex_bytes(
        record.get("canonical_frame_hex"),
        label=f"{label}.canonical_frame_hex",
        allow_empty=False,
    )
    frame_length = _bounded_integer(
        record.get("canonical_frame_length"),
        label=f"{label}.canonical_frame_length",
        minimum=1,
        maximum=280,
    )
    if frame_length != len(frame):
        raise Px4ServiceError(f"{label}.canonical_frame_length is inconsistent")
    payload = _command_audit_hex_bytes(
        record.get("payload_hex"),
        label=f"{label}.payload_hex",
        allow_empty=True,
    )
    signature = _command_audit_hex_bytes(
        record.get("signature_hex"),
        label=f"{label}.signature_hex",
        allow_empty=True,
    )
    payload_length = _bounded_integer(
        record.get("payload_length"),
        label=f"{label}.payload_length",
        minimum=0,
        maximum=UINT8_MAX,
    )
    if payload_length != len(payload):
        raise Px4ServiceError(f"{label}.payload_length is inconsistent")
    wire_version = _bounded_integer(
        record.get("wire_version"),
        label=f"{label}.wire_version",
        minimum=1,
        maximum=2,
    )
    magic = _bounded_integer(
        record.get("magic"),
        label=f"{label}.magic",
        minimum=0,
        maximum=UINT8_MAX,
    )
    incompat_flags = _bounded_integer(
        record.get("incompat_flags"),
        label=f"{label}.incompat_flags",
        minimum=0,
        maximum=UINT8_MAX,
    )
    compat_flags = _bounded_integer(
        record.get("compat_flags"),
        label=f"{label}.compat_flags",
        minimum=0,
        maximum=UINT8_MAX,
    )
    packet_sequence = _bounded_integer(
        record.get("packet_sequence"),
        label=f"{label}.packet_sequence",
        minimum=0,
        maximum=UINT8_MAX,
    )
    source_system_id = _bounded_integer(
        record.get("source_system_id"),
        label=f"{label}.source_system_id",
        minimum=0,
        maximum=UINT8_MAX,
    )
    source_component_id = _bounded_integer(
        record.get("source_component_id"),
        label=f"{label}.source_component_id",
        minimum=0,
        maximum=UINT8_MAX,
    )
    message_id = _bounded_integer(
        record.get("message_id"),
        label=f"{label}.message_id",
        minimum=0,
        maximum=UINT24_MAX,
    )
    checksum = _bounded_integer(
        record.get("checksum"),
        label=f"{label}.checksum",
        minimum=0,
        maximum=UINT16_MAX,
    )
    signed_frame = record.get("signed_frame")
    if not isinstance(signed_frame, bool):
        raise Px4ServiceError(f"{label}.signed_frame must be a boolean")
    frame_encoding = _string(
        record.get("frame_encoding"), label=f"{label}.frame_encoding"
    )
    if frame_encoding != expected_frame_encoding:
        raise Px4ServiceError(f"{label}.frame_encoding is invalid")

    if wire_version == 1:
        if magic != MAVLINK_V1_MAGIC or incompat_flags != 0 or compat_flags != 0:
            raise Px4ServiceError(f"{label} MAVLink v1 header identity is invalid")
        if signed_frame or signature:
            raise Px4ServiceError(f"{label} MAVLink v1 signature identity is invalid")
        payload_offset = 6
        expected_frame_length = payload_offset + payload_length + 2
        if len(frame) != expected_frame_length:
            raise Px4ServiceError(f"{label} MAVLink v1 frame length is invalid")
        header_message_id = frame[5]
        if (
            frame[0] != magic
            or frame[1] != payload_length
            or frame[2] != packet_sequence
            or frame[3] != source_system_id
            or frame[4] != source_component_id
        ):
            raise Px4ServiceError(f"{label} MAVLink v1 header bytes are inconsistent")
    else:
        if magic != MAVLINK_V2_MAGIC:
            raise Px4ServiceError(f"{label} MAVLink v2 magic is invalid")
        expected_signed = bool(incompat_flags & MAVLINK_IFLAG_SIGNED)
        if signed_frame is not expected_signed:
            raise Px4ServiceError(f"{label} MAVLink v2 signing flag is inconsistent")
        if len(signature) != (MAVLINK_SIGNATURE_LENGTH if expected_signed else 0):
            raise Px4ServiceError(f"{label} MAVLink v2 signature length is invalid")
        payload_offset = 10
        expected_frame_length = (
            payload_offset
            + payload_length
            + 2
            + (MAVLINK_SIGNATURE_LENGTH if expected_signed else 0)
        )
        if len(frame) != expected_frame_length:
            raise Px4ServiceError(f"{label} MAVLink v2 frame length is invalid")
        header_message_id = int.from_bytes(frame[7:10], "little")
        if (
            frame[0] != magic
            or frame[1] != payload_length
            or frame[2] != incompat_flags
            or frame[3] != compat_flags
            or frame[4] != packet_sequence
            or frame[5] != source_system_id
            or frame[6] != source_component_id
        ):
            raise Px4ServiceError(f"{label} MAVLink v2 header bytes are inconsistent")

    if message_id != expected_message_id or header_message_id != message_id:
        raise Px4ServiceError(f"{label}.message_id is inconsistent")
    payload_end = payload_offset + payload_length
    if frame[payload_offset:payload_end] != payload:
        raise Px4ServiceError(f"{label}.payload_hex is inconsistent")
    frame_checksum = int.from_bytes(frame[payload_end : payload_end + 2], "little")
    if frame_checksum != checksum:
        raise Px4ServiceError(f"{label}.checksum is inconsistent")
    if frame[payload_end + 2 :] != signature:
        raise Px4ServiceError(f"{label}.signature_hex is inconsistent")
    crc_extra = MAVLINK_CRC_EXTRAS[expected_message_id]
    if _mavlink_x25_checksum(frame[1:payload_end], crc_extra=crc_extra) != checksum:
        raise Px4ServiceError(f"{label}.checksum failed MAVLink validation")
    return payload


def _validate_command_transport_record(
    record: Mapping[str, object], *, label: str
) -> None:
    wire_type = _string(record.get("wire_type"), label=f"{label}.wire_type")
    if wire_type == COMMAND_AUDIT_WIRE_LONG:
        expected_message_id = MAVLINK_COMMAND_LONG_MESSAGE_ID
        maximum_payload_length = 33
    elif wire_type == COMMAND_AUDIT_WIRE_INT:
        expected_message_id = MAVLINK_COMMAND_INT_MESSAGE_ID
        maximum_payload_length = 35
    else:
        raise Px4ServiceError(f"{label}.wire_type is invalid")
    if (
        _string(record.get("acceptance_boundary"), label=f"{label}.acceptance_boundary")
        != COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY
    ):
        raise Px4ServiceError(f"{label}.acceptance_boundary is invalid")
    _bounded_integer(
        record.get("accepted_connection_count"),
        label=f"{label}.accepted_connection_count",
        minimum=1,
        maximum=UINT8_MAX,
    )
    confirmation = _bounded_integer(
        record.get("confirmation"),
        label=f"{label}.confirmation",
        minimum=0,
        maximum=UINT8_MAX,
    )
    payload = _validate_command_audit_frame(
        record,
        label=label,
        expected_message_id=expected_message_id,
        expected_frame_encoding=COMMAND_TRANSPORT_FRAME_ENCODING,
    )
    # MAVLink 2 permits truncating trailing extension bytes when they are zero.
    # COMMAND_LONG therefore appears as 32 bytes when confirmation is zero, and
    # COMMAND_INT may be 32..35 bytes depending on its trailing fields.  The
    # command and target IDs occupy bytes 28..31 in both messages and are always
    # required; omitted confirmation decodes to its zero default.
    if len(payload) < 32 or len(payload) > maximum_payload_length:
        raise Px4ServiceError(f"{label} command payload length is invalid")
    command = int.from_bytes(payload[28:30], "little")
    if (
        command != int(record["command"])
        or payload[30] != int(record["target_system_id"])
        or payload[31] != int(record["target_component_id"])
    ):
        raise Px4ServiceError(f"{label} decoded command payload is inconsistent")
    if wire_type == COMMAND_AUDIT_WIRE_LONG:
        decoded_confirmation = payload[32] if len(payload) > 32 else 0
        if decoded_confirmation != confirmation:
            raise Px4ServiceError(f"{label} COMMAND_LONG confirmation is inconsistent")
    elif confirmation != 0:
        raise Px4ServiceError(f"{label} COMMAND_INT confirmation must be zero")


def _validate_command_ack_ingress_record(
    record: Mapping[str, object], *, label: str
) -> None:
    if (
        _string(record.get("capture_boundary"), label=f"{label}.capture_boundary")
        != COMMAND_ACK_CAPTURE_BOUNDARY
    ):
        raise Px4ServiceError(f"{label}.capture_boundary is invalid")
    payload = _validate_command_audit_frame(
        record,
        label=label,
        expected_message_id=MAVLINK_COMMAND_ACK_MESSAGE_ID,
        expected_frame_encoding=COMMAND_ACK_FRAME_ENCODING,
    )
    wire_version = int(record["wire_version"])
    if (
        len(payload) < 3
        or len(payload) > 10
        or (wire_version == 1 and len(payload) != 3)
    ):
        raise Px4ServiceError(f"{label} COMMAND_ACK payload length is invalid")
    padded = payload.ljust(10, b"\x00")
    if (
        int.from_bytes(padded[0:2], "little") != int(record["command"])
        or padded[2] != int(record["result"])
        or padded[3] != int(record["progress"])
        or int.from_bytes(padded[4:8], "little", signed=True)
        != int(record["result_param2"])
        or padded[8] != int(record["target_system_id"])
        or padded[9] != int(record["target_component_id"])
    ):
        raise Px4ServiceError(f"{label} decoded COMMAND_ACK payload is inconsistent")


def _command_audit_transport_matches(
    transport: Mapping[str, object],
    other: Mapping[str, object],
) -> bool:
    return (
        int(other["command"]) == int(transport["command"])
        and other["wire_type"] == transport["wire_type"]
        and int(other["source_system_id"]) == int(transport["source_system_id"])
        and int(other["source_component_id"]) == int(transport["source_component_id"])
        and int(other["target_system_id"]) == int(transport["target_system_id"])
        and int(other["target_component_id"]) == int(transport["target_component_id"])
    )


def _command_audit_ack_matches_transport(
    ack: Mapping[str, object],
    transport: Mapping[str, object],
) -> bool:
    return (
        int(ack["command"]) == int(transport["command"])
        and int(ack["source_system_id"]) == int(transport["target_system_id"])
        and int(ack["source_component_id"]) == int(transport["target_component_id"])
        and int(ack["target_system_id"]) in {0, int(transport["source_system_id"])}
        and int(ack["target_component_id"])
        in {0, int(transport["source_component_id"])}
    )


def _command_audit_transactions(
    records: Sequence[Mapping[str, object]],
) -> tuple[tuple[CommandAuditTransaction, ...], CommandAuditTransaction | None]:
    completed: list[CommandAuditTransaction] = []
    # MAVSDK can have more than one command outstanding at a time.  Telemetry
    # stream-rate requests (511/512) may interleave with the action command, and
    # the command sender can retransmit either command before its ACK arrives.
    # Keep one active group per wire identity so those legitimate interleavings
    # do not get mistaken for a corrupt single-command journal.
    active_groups: list[dict[str, object]] = []
    pending_ingress: Mapping[str, object] | None = None
    previous_seq: int | None = None
    previous_timestamp_ns: int | None = None
    for record in records:
        seq = int(record["seq"])
        timestamp_ns = int(record["timestamp_ns"])
        if previous_seq is not None and seq != previous_seq + 1:
            raise Px4ServiceError("decoded command audit sequence is not contiguous")
        if previous_timestamp_ns is not None and timestamp_ns < previous_timestamp_ns:
            raise Px4ServiceError("decoded command audit timestamp moved backward")
        previous_seq = seq
        previous_timestamp_ns = timestamp_ns
        kind = str(record["kind"])
        if kind == COMMAND_AUDIT_ACK_INGRESS_KIND:
            if pending_ingress is not None:
                raise Px4ServiceError(
                    "decoded command audit ingress is missing its disposition"
                )
            pending_ingress = record
            continue
        if kind == COMMAND_AUDIT_ACK_DISPOSITION_KIND:
            if pending_ingress is None:
                raise Px4ServiceError(
                    "decoded command audit disposition has no ingress"
                )
            if int(record["ack_ingress_seq"]) != int(pending_ingress["seq"]) or int(
                record["command"]
            ) != int(pending_ingress["command"]):
                raise Px4ServiceError(
                    "decoded command audit disposition references the wrong ingress"
                )
            disposition = str(record["status"])
            targets_this_mavsdk = int(pending_ingress["target_system_id"]) in {
                0,
                MAVSDK_SERVER_SYSTEM_ID,
            } and int(pending_ingress["target_component_id"]) in {
                0,
                MAVSDK_SERVER_COMPONENT_ID,
            }
            matching_groups = [
                group
                for group in active_groups
                if _command_audit_ack_matches_transport(
                    pending_ingress, group["transport"]
                )
            ]
            if disposition == COMMAND_AUDIT_INTERNAL_ERROR_DISPOSITION:
                raise Px4ServiceError(
                    "decoded command audit matcher reported an internal error"
                )
            if disposition == COMMAND_AUDIT_TARGET_FILTERED_DISPOSITION:
                if targets_this_mavsdk:
                    raise Px4ServiceError(
                        "decoded COMMAND_ACK target-filter disposition is inconsistent"
                    )
            elif disposition == COMMAND_AUDIT_UNMATCHED_DISPOSITION:
                if not targets_this_mavsdk:
                    raise Px4ServiceError(
                        "decoded COMMAND_ACK unmatched disposition is inconsistent"
                    )
                # A late ACK for a retransmission is legitimately marked
                # unmatched after MAVSDK has closed the logical command group.
                if matching_groups:
                    raise Px4ServiceError(
                        "decoded COMMAND_ACK was marked unmatched despite an active transport"
                    )
            elif disposition == COMMAND_AUDIT_MATCHED_DISPOSITION:
                if not targets_this_mavsdk:
                    raise Px4ServiceError(
                        "decoded COMMAND_ACK matched disposition is inconsistent"
                    )
                if len(matching_groups) != 1:
                    raise Px4ServiceError(
                        "decoded COMMAND_ACK match is ambiguous or lacks transport"
                    )
                group = matching_groups[0]
                group_records = group["records"]
                if not isinstance(group_records, list):
                    raise Px4ServiceError("decoded command audit group is malformed")
                group_records.extend((dict(pending_ingress), dict(record)))
                if int(pending_ingress["result"]) != MAV_RESULT_IN_PROGRESS:
                    completed.append(
                        CommandAuditTransaction(
                            records=tuple(group_records),
                            terminal_ack_ingress_record=dict(pending_ingress),
                            terminal_ack_disposition_record=dict(record),
                        )
                    )
                    active_groups.remove(group)
            else:
                raise Px4ServiceError(
                    "decoded command audit disposition status is invalid"
                )
            pending_ingress = None
            continue
        if pending_ingress is not None:
            raise Px4ServiceError(
                "decoded command audit ingress disposition is not immediate"
            )
        if kind != COMMAND_AUDIT_TRANSPORT_KIND:
            raise Px4ServiceError("decoded command audit record kind is invalid")
        if (
            int(record["source_system_id"]) != MAVSDK_SERVER_SYSTEM_ID
            or int(record["source_component_id"]) != MAVSDK_SERVER_COMPONENT_ID
        ):
            raise Px4ServiceError("decoded command audit transport source is invalid")
        if int(record["target_component_id"]) <= 0:
            raise Px4ServiceError(
                "decoded command audit transport target component is invalid"
            )
        matching_groups = [
            group
            for group in active_groups
            if _command_audit_transport_matches(group["transport"], record)
        ]
        if len(matching_groups) > 1:
            raise Px4ServiceError(
                "decoded command audit has ambiguous repeated transports"
            )
        if matching_groups:
            group_records = matching_groups[0]["records"]
            if not isinstance(group_records, list):
                raise Px4ServiceError("decoded command audit group is malformed")
            group_records.append(dict(record))
        else:
            active_groups.append(
                {"transport": dict(record), "records": [dict(record)]}
            )
    if pending_ingress is not None:
        raise Px4ServiceError(
            "decoded command audit ingress is missing its disposition"
        )
    if not active_groups:
        return tuple(completed), None
    active_records = tuple(
        item
        for group in active_groups
        for item in group["records"]
        if isinstance(item, dict)
    )
    return (
        tuple(completed),
        CommandAuditTransaction(
            records=active_records,
            terminal_ack_ingress_record=None,
            terminal_ack_disposition_record=None,
        ),
    )


def _parse_command_audit_record(
    line: bytes,
    *,
    label: str,
    last_seq: int,
    last_timestamp_ns: int,
) -> dict[str, object]:
    if not line.endswith(b"\n"):
        raise Px4ServiceError(f"{label} must end with a single newline")
    payload = _parse_json(line[:-1])
    if _canonical_json(payload) + b"\n" != line:
        raise Px4ServiceError(f"{label} is not canonical JSON")
    kind = _string(payload.get("kind"), label=f"{label}.kind")
    if kind == COMMAND_AUDIT_TRANSPORT_KIND:
        raw = _strict_object(
            payload, required=COMMAND_AUDIT_TRANSPORT_FIELDS, label=label
        )
    elif kind == COMMAND_AUDIT_ACK_INGRESS_KIND:
        raw = _strict_object(
            payload, required=COMMAND_AUDIT_ACK_INGRESS_FIELDS, label=label
        )
    elif kind == COMMAND_AUDIT_ACK_DISPOSITION_KIND:
        raw = _strict_object(
            payload, required=COMMAND_AUDIT_ACK_DISPOSITION_FIELDS, label=label
        )
    else:
        raise Px4ServiceError(f"{label}.kind is invalid")
    seq = _bounded_integer(
        raw.get("seq"),
        label=f"{label}.seq",
        minimum=1,
        maximum=UINT64_MAX,
    )
    if seq != last_seq + 1:
        raise Px4ServiceError(f"{label}.seq is not contiguous")
    timestamp_ns = _bounded_integer(
        raw.get("timestamp_ns"),
        label=f"{label}.timestamp_ns",
        minimum=0,
        maximum=UINT64_MAX,
    )
    if timestamp_ns < last_timestamp_ns:
        raise Px4ServiceError(f"{label}.timestamp_ns moved backward")
    _bounded_integer(
        raw.get("command"),
        label=f"{label}.command",
        minimum=1,
        maximum=UINT16_MAX,
    )
    if kind == COMMAND_AUDIT_ACK_DISPOSITION_KIND:
        _bounded_integer(
            raw.get("ack_ingress_seq"),
            label=f"{label}.ack_ingress_seq",
            minimum=1,
            maximum=UINT64_MAX,
        )
        status = _string(raw.get("status"), label=f"{label}.status")
        if status not in {
            COMMAND_AUDIT_MATCHED_DISPOSITION,
            COMMAND_AUDIT_UNMATCHED_DISPOSITION,
            COMMAND_AUDIT_TARGET_FILTERED_DISPOSITION,
            COMMAND_AUDIT_INTERNAL_ERROR_DISPOSITION,
        }:
            raise Px4ServiceError(f"{label}.status is invalid")
        return raw
    for field in (
        "source_system_id",
        "source_component_id",
        "target_system_id",
        "target_component_id",
    ):
        _bounded_integer(
            raw.get(field),
            label=f"{label}.{field}",
            minimum=0,
            maximum=UINT8_MAX,
        )
    if kind == COMMAND_AUDIT_TRANSPORT_KIND:
        _validate_command_transport_record(raw, label=label)
    else:
        _bounded_integer(
            raw.get("result"),
            label=f"{label}.result",
            minimum=0,
            maximum=UINT8_MAX,
        )
        _bounded_integer(
            raw.get("progress"),
            label=f"{label}.progress",
            minimum=0,
            maximum=UINT8_MAX,
        )
        _bounded_integer(
            raw.get("result_param2"),
            label=f"{label}.result_param2",
            minimum=INT32_MIN,
            maximum=INT32_MAX,
        )
        _validate_command_ack_ingress_record(raw, label=label)
    return raw


def _read_command_audit_records(
    cursor: CommandAuditJournalCursor,
    *,
    require_complete: bool = False,
) -> tuple[dict[str, object], ...]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(cursor.path, flags)
    except OSError as exc:
        raise Px4ServiceError(
            f"MAVSDK command audit journal is unavailable: {cursor.path}"
        ) from exc
    try:
        info_before = os.fstat(fd)
        if not stat.S_ISREG(info_before.st_mode):
            raise Px4ServiceError("MAVSDK command audit journal must be a regular file")
        if cursor.device is not None and (
            cursor.device != info_before.st_dev or cursor.inode != info_before.st_ino
        ):
            raise Px4ServiceError("MAVSDK command audit journal was replaced")
        if info_before.st_size < cursor.offset:
            raise Px4ServiceError("MAVSDK command audit journal was truncated")
        to_read = info_before.st_size - cursor.offset
        if to_read == 0:
            if require_complete and cursor.trailing_bytes:
                raise Px4ServiceError(
                    "MAVSDK command audit journal ended with a partial record"
                )
            if cursor.device is None:
                cursor.device = info_before.st_dev
                cursor.inode = info_before.st_ino
            return ()
        os.lseek(fd, cursor.offset, os.SEEK_SET)
        appended = _read_fd_exact(fd, length=to_read)
        if len(appended) != to_read:
            raise Px4ServiceError("MAVSDK command audit journal returned a short read")
        info_after = os.fstat(fd)
        if info_after.st_size < info_before.st_size:
            raise Px4ServiceError("MAVSDK command audit journal was truncated")
    finally:
        os.close(fd)
    combined = cursor.trailing_bytes + appended
    last_newline = combined.rfind(b"\n")
    if last_newline < 0:
        if require_complete:
            raise Px4ServiceError(
                "MAVSDK command audit journal ended with a partial record"
            )
        cursor.device = info_before.st_dev
        cursor.inode = info_before.st_ino
        cursor.offset += len(appended)
        cursor.trailing_bytes = combined
        return ()
    complete = combined[: last_newline + 1]
    trailing = combined[last_newline + 1 :]
    records: list[dict[str, object]] = []
    last_seq = cursor.last_seq
    last_timestamp_ns = cursor.last_timestamp_ns
    for index, line in enumerate(complete.splitlines(keepends=True)):
        record = _parse_command_audit_record(
            line,
            label=f"command_audit[{index}]",
            last_seq=last_seq,
            last_timestamp_ns=last_timestamp_ns,
        )
        last_seq = int(record["seq"])
        last_timestamp_ns = int(record["timestamp_ns"])
        records.append(record)
    if require_complete and trailing:
        raise Px4ServiceError(
            "MAVSDK command audit journal ended with a partial record"
        )
    cursor.device = info_before.st_dev
    cursor.inode = info_before.st_ino
    cursor.last_seq = last_seq
    cursor.last_timestamp_ns = last_timestamp_ns
    cursor.offset += len(appended)
    cursor.trailing_bytes = trailing
    return tuple(records)


def _expected_px4_artifact_types(
    *,
    inspections: Sequence[object],
    camera_declarations: Sequence[object],
    urban_camera_observations: Sequence[object],
) -> frozenset[str]:
    expected = {EVIDENCE_ARTIFACT_TYPE}
    if inspections or camera_declarations or urban_camera_observations:
        expected.update(
            {
                OBSERVATION_ARTIFACT_TYPE,
                SENSOR_FRAME_ARTIFACT_TYPE,
                CAMERA_FRAME_DATA_ARTIFACT_TYPE,
            }
        )
    return frozenset(expected)


def _load_workload_identity(
    *,
    contract_path: Path,
    bundle_root: Path,
    expected_run_id: str,
    expected_provider_id: str,
    expected_provider_port: int,
    expected_seed: int,
) -> WorkloadIdentity:
    try:
        raw = _parse_json(contract_path.read_bytes())
    except OSError as exc:
        raise Px4ServiceError("AERO_BENCH_CONTRACT is unavailable") from exc
    contract = _strict_object(
        raw,
        required=frozenset(
            {
                "schema_version",
                "role",
                "run_id",
                "seed",
                "workload_id",
                "clock",
                "provider",
                "scenario_digest",
                "scenario",
                "scenario_assets",
            }
        ),
        label="ProviderWorkloadContract",
    )
    if contract["schema_version"] != "aero-bench.workload-contract/v5":
        raise Px4ServiceError("ProviderWorkloadContract schema version is unsupported")
    if contract["role"] != "provider":
        raise Px4ServiceError("AERO_BENCH_CONTRACT is not a provider workload contract")
    contract_run_id = _sha256(contract["run_id"], label="contract.run_id")
    if contract_run_id != expected_run_id:
        raise Px4ServiceError(
            "ProviderWorkloadContract run_id differs from AERO_BENCH_RUN_ID"
        )
    contract_seed = _integer(contract["seed"], label="contract.seed")
    if contract_seed != expected_seed:
        raise Px4ServiceError(
            "ProviderWorkloadContract seed differs from AERO_BENCH_SEED"
        )
    contract_workload_id = _string(
        contract["workload_id"], label="contract.workload_id", pattern=IDENTIFIER
    )
    if contract_workload_id != expected_provider_id:
        raise Px4ServiceError(
            "ProviderWorkloadContract workload_id differs from AERO_BENCH_WORKLOAD_ID"
        )
    clock = _strict_object(
        contract["clock"],
        required=frozenset(
            {"authority", "step_ns", "max_steps", "provider_timeout_ms"}
        ),
        label="ProviderWorkloadContract.clock",
    )
    clock_step_ns = _integer(
        clock["step_ns"], label="contract.clock.step_ns", minimum=1
    )
    clock_max_steps = _integer(
        clock["max_steps"], label="contract.clock.max_steps", minimum=1
    )

    provider = _strict_object(
        contract["provider"],
        required=frozenset(
            {
                "provider_id",
                "adapter",
                "port",
                "workload",
                "config",
                "protocol_schema",
                "capabilities",
                "artifact_requirements",
            }
        ),
        label="ProviderWorkloadContract.provider",
    )
    provider_id = _string(
        provider["provider_id"],
        label="contract.provider.provider_id",
        pattern=IDENTIFIER,
    )
    if provider_id != expected_provider_id:
        raise Px4ServiceError(
            "ProviderWorkloadContract provider_id is not the workload identity"
        )
    if provider["adapter"] != PROVIDER_ADAPTER:
        raise Px4ServiceError("PX4 service requires the px4.gazebo provider adapter")
    provider_port = _integer(
        provider["port"], label="contract.provider.port", minimum=1024
    )
    if provider_port > 65535 or provider_port != expected_provider_port:
        raise Px4ServiceError(
            "ProviderWorkloadContract provider port is not the explicit bind port"
        )
    capabilities = _string_list(
        provider["capabilities"],
        label="contract.provider.capabilities",
        minimum=1,
        pattern=IDENTIFIER,
    )
    if len(capabilities) != len(set(capabilities)):
        raise Px4ServiceError("ProviderWorkloadContract capabilities repeat an ID")
    try:
        scenario = validate_workload_scenario(
            contract["scenario"],
            expected_seed=contract_seed,
            expected_digest=contract["scenario_digest"],
            role="provider",
            workload_id=provider_id,
            projected_assets=contract["scenario_assets"],
            provider_capabilities=capabilities,
        )
    except WorkloadScenarioError as exc:
        raise Px4ServiceError("ProviderWorkloadContract scenario is invalid") from exc

    workload = _strict_object(
        provider["workload"],
        required=frozenset({"runtime", "resources", "implementation"}),
        label="ProviderWorkloadContract.provider.workload",
    )
    runtime = _strict_object(
        workload["runtime"],
        required=frozenset({"image", "command"}),
        label="ProviderWorkloadContract.provider.workload.runtime",
    )
    runtime_image = _safe_runtime_image(runtime["image"])
    if not isinstance(runtime["command"], list) or not runtime["command"]:
        raise Px4ServiceError("Provider workload runtime command must be non-empty")
    _string_list(
        runtime["command"],
        label="contract.provider.workload.runtime.command",
        minimum=1,
    )

    config_bound = _strict_object(
        provider["config"],
        required=frozenset({"file", "schema_file"}),
        label="ProviderWorkloadContract.provider.config",
    )
    config_file = _file_ref(config_bound["file"], label="contract.provider.config.file")
    schema_file = _file_ref(
        config_bound["schema_file"], label="contract.provider.config.schema_file"
    )
    config_path = _resolve_root_file(
        bundle_root, config_file, label="provider config file"
    )
    _resolve_root_file(bundle_root, schema_file, label="provider config schema file")
    provider_config = _px4_provider_config(_parse_json(config_path.read_bytes()))
    if provider_config["provider_id"] != provider_id:
        raise Px4ServiceError("Px4GazeboConfig provider_id differs from workload")

    scenario_world_id = _string(
        scenario.scenario["world_id"],
        label="ResolvedScenario.world_id",
        pattern=IDENTIFIER,
    )
    raw_airspace_transition = provider_config.get("airspace_transition")
    configured_world_id = provider_config.get("world_name")
    if raw_airspace_transition is not None:
        if configured_world_id is None:
            raise Px4ServiceError(
                "airspace transition requires an explicit logical world binding"
            )
        if configured_world_id != scenario_world_id:
            raise Px4ServiceError(
                "Px4GazeboConfig.world_name must match ResolvedScenario.world_id"
            )

    if provider_config.get("world_input") is not None:
        # Declared bundle world: consume ONLY the staged input-volume SDF.
        # Startup resolves it under AERO_BENCH_BUNDLE_DIR, symlink-checks it,
        # and hash-verifies the exact staged bytes; a missing or tampered
        # staged world rejects here.  There is NO fallback to the scenario
        # image/world path for a declared bundle world.
        world_name, world_source_sdf = _bundle_world_sdf(
            provider_config=provider_config,
            bundle_root=bundle_root,
        )
    else:
        world_name, world_source_sdf = _scenario_world_sdf(
            scenario=scenario,
            provider_config=provider_config,
            bundle_root=bundle_root,
        )
    airspace_transition = _airspace_transition_config(
        raw_airspace_transition, world_id=scenario_world_id
    )
    vehicles = _scenario_vehicles(scenario=scenario, provider_config=provider_config)
    inspections, target_model_paths = _scenario_inspections(
        scenario=scenario,
        provider_id=provider_id,
        vehicles=vehicles,
        bundle_root=bundle_root,
    )
    camera_declarations = _scenario_cameras(
        scenario=scenario,
    )
    urban_camera_observations = _scenario_urban_camera_observations(
        scenario=scenario,
        provider_id=provider_id,
        vehicles=vehicles,
    )
    flight_observations = _scenario_flight_observations(
        scenario=scenario,
        provider_id=provider_id,
        vehicles=vehicles,
    )

    requirements = provider["artifact_requirements"]
    expected_types = _expected_px4_artifact_types(
        inspections=inspections,
        camera_declarations=camera_declarations,
        urban_camera_observations=urban_camera_observations,
    )
    if not isinstance(requirements, list) or len(requirements) != len(expected_types):
        raise Px4ServiceError(
            "PX4 Provider artifact inventory does not match its configured capabilities"
        )
    parsed_requirements = tuple(
        _artifact_requirement(item, label=f"PX4 ArtifactRequirement[{index}]")
        for index, item in enumerate(requirements)
    )
    if {item["artifact_type"] for item in parsed_requirements} != expected_types:
        raise Px4ServiceError("PX4 evidence ArtifactRequirement types are incomplete")
    for requirement in parsed_requirements:
        if requirement["producer_id"] != provider_id:
            raise Px4ServiceError(
                "PX4 evidence ArtifactRequirement producer_id mismatch"
            )
        if (
            requirement["source_asset_id"] is not None
            or (
                requirement["artifact_type"] == OBSERVATION_ARTIFACT_TYPE
                and requirement["visibility"] != "private"
            )
            or (
                requirement["artifact_type"] == SENSOR_FRAME_ARTIFACT_TYPE
                and requirement["visibility"] != "public"
            )
            or (
                requirement["artifact_type"] == CAMERA_FRAME_DATA_ARTIFACT_TYPE
                and requirement["visibility"] != "public"
            )
        ):
            raise Px4ServiceError(
                "PX4 trajectory visibility follows the run declaration, observation "
                "evidence must be private, sensor frames and camera frame data must "
                "be public, and source_asset_id must be null"
            )

    return WorkloadIdentity(
        run_id=expected_run_id,
        seed=expected_seed,
        provider_id=provider_id,
        provider_port=provider_port,
        runtime_image=runtime_image,
        config_digest=config_file["sha256"],
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        bundle_root=bundle_root.resolve(strict=True),
        artifact_requirements=parsed_requirements,
        clock_step_ns=clock_step_ns,
        clock_max_steps=clock_max_steps,
        provider_config=provider_config,
        world_name=world_name,
        world_source_sdf=world_source_sdf,
        vehicles=vehicles,
        inspections=inspections,
        inspection_target_model_sdfs=target_model_paths,
        airspace_transition=airspace_transition,
        camera_declarations=camera_declarations,
        urban_camera_observations=urban_camera_observations,
        flight_observations=flight_observations,
    )


class Px4RuntimeStack(Protocol):
    async def verify_identities(self, config: PreparedConfig) -> None: ...

    async def reset(self, config: PreparedConfig, *, seed: int) -> None: ...

    async def current_sim_time_ns(self) -> int: ...

    async def observed_physics_step_ns(self) -> int: ...

    async def multi_step(
        self,
        iterations: int,
        *,
        execution_started: asyncio.Event | None = None,
        command_dispatched: asyncio.Event | None = None,
    ) -> None: ...

    async def telemetry(self) -> tuple[VehicleTelemetry, ...]: ...

    async def wait_for_telemetry(self) -> None: ...

    def flight_telemetry_observation(
        self, vehicle_id: str, *, simulation_time_ns: int
    ) -> Mapping[str, object]: ...

    def flight_gnss_observation(
        self, vehicle_id: str, *, simulation_time_ns: int
    ) -> Mapping[str, object]: ...

    def camera_frame(self, camera_id: str) -> CapturedCameraFrame: ...

    def read_command_audit_records(
        self, vehicle_id: str, *, require_complete: bool = False
    ) -> tuple[dict[str, object], ...]: ...

    async def accept_command(self, request: Mapping[str, Any]) -> None: ...

    async def apply_command(self, request: Mapping[str, Any]) -> None: ...

    async def snapshot(self) -> Mapping[str, object]: ...

    def airspace_observation(
        self, *, vehicle_id: str, requested_at: tuple[int, int]
    ) -> Mapping[str, object]: ...

    async def wait_for_airspace(self, *, requested_at: tuple[int, int]) -> None: ...

    def airspace_transition_events(
        self, *, requested_at: tuple[int, int]
    ) -> tuple[dict[str, object], ...]: ...

    async def stop(self) -> None: ...


def _command_argument_map(request: Mapping[str, Any]) -> dict[str, object]:
    arguments = request.get("arguments")
    if arguments is None:
        return {}
    if not isinstance(arguments, list):
        raise Px4CommandPhaseError(
            last_success="received",
            detail="command arguments must be a JSON array",
        )
    mapping: dict[str, object] = {}
    for item in arguments:
        if not isinstance(item, dict) or "name" not in item or "value" not in item:
            raise Px4CommandPhaseError(
                last_success="received",
                detail="command argument is not a name/value object",
            )
        name = item["name"]
        if not isinstance(name, str) or name in mapping:
            raise Px4CommandPhaseError(
                last_success="received",
                detail="command argument names must be unique strings",
            )
        mapping[name] = item["value"]
    return mapping


def _require_vehicle_id(
    request: Mapping[str, Any], vehicles: tuple[dict[str, Any], ...]
) -> str:
    arguments = _command_argument_map(request)
    vehicle_id = arguments.get("vehicle_id")
    if not isinstance(vehicle_id, str):
        raise Px4CommandPhaseError(
            last_success="received",
            detail="command is missing vehicle_id",
        )
    if vehicle_id not in {vehicle["vehicle_id"] for vehicle in vehicles}:
        raise Px4CommandPhaseError(
            last_success="received",
            detail=f"command names undeclared vehicle {vehicle_id!r}",
        )
    return vehicle_id


class RealPx4Stack:
    """Launches the declared PX4 SITL, Gazebo Harmonic, and MAVSDK processes."""

    def __init__(
        self,
        *,
        tmp_dir: Path,
        executable_lookup: Callable[[str], str | None] = shutil.which,
        identity_path: Path = IDENTITY_PATH,
        px4_data_dir: Path = Path(PX4_DATA_DIR),
        gz_worlds_dir: Path = Path(GZ_WORLDS_DIR),
        gz_models_dir: Path = Path(GZ_MODELS_DIR),
        px4_bin: Path = Path(PX4_BIN),
        gz_bin: Path = Path(GZ_BIN),
        mavsdk_bin: Path = Path(MAVSDK_BIN),
        bundle_root: Path | None = None,
    ):
        self._tmp_dir = tmp_dir
        self._bundle_root = bundle_root
        self._executable_lookup = executable_lookup
        self._identity_path = identity_path
        self._px4_data_dir = px4_data_dir
        self._gz_worlds_dir = gz_worlds_dir
        self._gz_models_dir = gz_models_dir
        self._px4_bin = px4_bin
        self._gz_bin = gz_bin
        self._mavsdk_bin = mavsdk_bin
        self._config: PreparedConfig | None = None
        self._processes: list[asyncio.subprocess.Process] = []
        self._log_paths: dict[str, Path] = {}
        self._mavsdk: dict[str, Any] = {}
        self._telemetry_tasks: list[asyncio.Task[None]] = []
        self._telemetry_cache: dict[str, dict[str, object]] = {}
        self._telemetry_received_monotonic_ns: dict[str, dict[str, int]] = {}
        self._telemetry_failure: Exception | None = None
        self._stream_timeout_s: float | None = None
        self._time_origin_ns = 0
        self._latest_poses: dict[str, dict[str, float]] = {}
        self._stats_topic_name: str | None = None
        self._stats_topic_subscribed = False
        self._stats_topic_active = False
        self._stats_topic_callback: Callable[[Any], None] | None = None
        self._stats_topic_version = 0
        self._stats_topic_latest: tuple[int, bool] | None = None
        self._stats_topic_failure: Exception | None = None
        self._stats_topic_lock = threading.Lock()
        self._pose_topic_name: str | None = None
        self._pose_topic_subscribed = False
        self._pose_topic_active = False
        self._pose_topic_callback: Callable[[Any], None] | None = None
        self._pose_topic_version = 0
        self._pose_topic_latest: dict[str, dict[str, float]] | None = None
        self._pose_topic_latest_received_at: float | None = None
        self._pose_topic_failure: Exception | None = None
        self._pose_topic_lock = threading.Lock()
        self._airspace_topic_name: str | None = None
        self._airspace_topic_subscribed = False
        self._airspace_topic_active = False
        self._airspace_topic_callback: Callable[[Any], None] | None = None
        self._airspace_samples: dict[str, dict[int, dict[str, object]]] = {}
        self._airspace_events: list[dict[str, object]] = []
        self._airspace_emitted_sequences: set[tuple[str, int]] = set()
        self._airspace_failure: Exception | None = None
        self._airspace_lock = threading.Lock()
        self._latest_contacts: dict[str, tuple[str, ...]] = {}
        self._contact_lock = threading.Lock()
        self._contact_active = False
        self._contact_subscribed_topics: tuple[str, ...] = ()
        self._contact_callbacks: list[Callable[[Any], None]] = []
        self._contact_window: dict[str, set[str]] = {}
        self._contact_failure: Exception | None = None
        self._camera_frames: dict[str, CapturedCameraFrame] = {}
        self._camera_stream_lock = threading.Lock()
        self._camera_capture_lock = asyncio.Lock()
        self._camera_stream_generation = 0
        self._camera_stream_active = False
        self._camera_stream_failed = False
        self._camera_stream_failure: str | None = None
        self._camera_stream_node: Any | None = None
        self._camera_stream_topics: dict[str, str] = {}
        self._camera_subscribed_topics: list[str] = []
        self._camera_callbacks: list[Callable[[Any], None]] = []
        self._camera_stream_versions: dict[str, int] = {}
        self._camera_stream_latest: dict[str, tuple[int, Any]] = {}
        self._camera_trigger_publisher: Any | None = None
        self._camera_boolean_type: Any | None = None
        self._command_audit_journals: dict[str, CommandAuditJournalCursor] = {}
        self._mavsdk_process_generation = 0
        self._gazebo_transport_node: Any | None = None
        self._gazebo_world_control_type: Any | None = None
        self._gazebo_boolean_type: Any | None = None
        self._gazebo_transport_lock = asyncio.Lock()

    async def verify_identities(self, config: PreparedConfig) -> None:
        self._config = config
        self._verify_required_commands(config)
        identity = self._load_identity_file()
        self._verify_live_binaries(identity, config)

    async def reset(self, config: PreparedConfig, *, seed: int) -> None:
        started_at = time.monotonic()
        _startup_progress("stack-reset.begin", started_at=started_at)
        await self.stop()
        self._time_origin_ns = 0
        self._camera_frames = {}
        await self.verify_identities(config)
        _startup_progress("stack-reset.identities-complete", started_at=started_at)
        await self._start(config, seed=seed, progress_started_at=started_at)
        _startup_progress("stack-reset.native-start-complete", started_at=started_at)
        if WARMUP_NS % config.physics_step_ns != 0:
            raise Px4ServiceError(
                "PX4 warm-up interval is not aligned to physics_step_ns"
            )
        warmup_steps = WARMUP_NS // config.physics_step_ns
        _startup_progress(
            "stack-reset.warmup-begin",
            started_at=started_at,
            detail={"iterations": warmup_steps},
        )
        await self.multi_step(warmup_steps)
        _startup_progress("stack-reset.warmup-complete", started_at=started_at)
        await self._wait_for_telemetry_cache(config)
        _startup_progress("stack-reset.telemetry-complete", started_at=started_at)
        self._time_origin_ns = await self._raw_sim_time_ns()
        if config.airspace_transition is not None:
            await self.wait_for_airspace(requested_at=(0, 0))
        _startup_progress("stack-reset.airspace-complete", started_at=started_at)
        with self._airspace_lock:
            self._airspace_samples = {
                vehicle: {at: sample for at, sample in samples.items() if at >= self._time_origin_ns}
                for vehicle, samples in self._airspace_samples.items()
            }
            self._airspace_events = [
                sample for sample in self._airspace_events
                if int(sample["sim_time_ns"]) > self._time_origin_ns
            ]
            self._airspace_emitted_sequences.clear()
        reached = await self.current_sim_time_ns()
        if reached != 0:
            raise Px4ServiceError(
                "PX4/Gazebo reset did not establish a zero benchmark time: "
                f"observed={reached}"
            )
        _startup_progress("stack-reset.complete", started_at=started_at)

    async def _raw_sim_time_ns(self) -> int:
        sim_time_ns, _paused = await self._wait_for_stats_latest(
            timeout_s=self._require_config().command_timeout_ms / 1000
        )
        return sim_time_ns

    async def current_sim_time_ns(self) -> int:
        raw_time_ns = await self._raw_sim_time_ns()
        relative_time_ns = raw_time_ns - self._time_origin_ns
        if relative_time_ns < 0:
            raise Px4ServiceError("Gazebo simulation time moved before the run epoch")
        return relative_time_ns

    async def observed_physics_step_ns(self) -> int:
        world = self._tmp_dir / "worlds" / self._require_config().world_sdf
        try:
            text = world.read_text(encoding="utf-8")
        except OSError as exc:
            raise Px4ServiceError(
                f"Gazebo world SDF is missing after launch: {world}"
            ) from exc
        match = re.search(r"<max_step_size>\s*([0-9eE.+-]+)\s*</max_step_size>", text)
        if match is None:
            raise Px4ServiceError("Gazebo world SDF omitted max_step_size")
        step_s = Decimal(match.group(1))
        step_ns = step_s * Decimal(1_000_000_000)
        if step_ns != step_ns.to_integral_value():
            raise Px4ServiceError("Gazebo max_step_size is not an integer nanosecond")
        value_ns = int(step_ns)
        if value_ns <= 0:
            raise Px4ServiceError("Gazebo max_step_size must be positive")
        return value_ns

    async def _advance_world_control(
        self,
        *,
        start_ns: int,
        iterations: int,
        physics_ns: int,
    ) -> None:
        """Advance a bounded number of paused Gazebo steps exactly.

        Keeping each request bounded prevents a long camera/render burst from
        monopolizing the Gazebo transport thread.  The stats equality check
        remains the authoritative barrier after every request.
        """

        if iterations < 0:
            raise Px4ServiceError("physics multi_step cannot be negative")
        for offset in range(0, iterations, WORLD_CONTROL_CHUNK_STEPS):
            chunk = min(WORLD_CONTROL_CHUNK_STEPS, iterations - offset)
            expected_ns = start_ns + (offset + chunk) * physics_ns
            stats_version = self._stats_topic_version_snapshot()
            service = "/world/" + self._require_config().world_name + "/control"
            try:
                await self._gazebo_world_control(
                    service,
                    multi_step=chunk,
                    timeout_s=WORLD_CONTROL_ACK_TIMEOUT_S,
                )
            except _GazeboWorldControlAckError as error:
                # A confirmed Boolean.data=false rejection is fatal.  A lost
                # transport reply is not: Gazebo may already be executing the
                # bounded request.  Wait on WorldStatistics and never resend
                # after that stream has advanced.
                if not error.lost_reply:
                    raise
                try:
                    reconciled = await self._world_control_target_reached(
                        expected_ns=expected_ns,
                        minimum_version=stats_version,
                        timeout_s=WORLD_CONTROL_ACK_TIMEOUT_S,
                    )
                except Px4ServiceError as reconciliation_error:
                    raise error from reconciliation_error
                if not reconciled:
                    if self._stats_topic_version_snapshot() > stats_version:
                        raise error
                    self._record_gz_diagnostic(
                        "world-control-ack-retry",
                        {
                            "service": service,
                            "multi_step": chunk,
                            "expected_sim_time_ns": expected_ns
                            - self._time_origin_ns,
                            "error": str(error),
                        },
                    )
                    await asyncio.sleep(BARRIER_SCHEDULING_SETTLE_S)
                    await self._gazebo_world_control(
                        service,
                        multi_step=chunk,
                        timeout_s=WORLD_CONTROL_ACK_TIMEOUT_S,
                    )
            await self._wait_until_sim_time(
                expected_ns, minimum_version=stats_version
            )

    async def multi_step(
        self,
        iterations: int,
        *,
        execution_started: asyncio.Event | None = None,
        command_dispatched: asyncio.Event | None = None,
    ) -> None:
        if iterations < 0:
            raise Px4ServiceError("physics multi_step cannot be negative")
        if (execution_started is None) != (command_dispatched is None):
            raise Px4ServiceError(
                "command execution stepping requires both synchronization events"
            )
        if execution_started is not None and iterations < 2:
            raise Px4ServiceError(
                "command execution window requires at least two physics steps"
            )
        if iterations == 0:
            return
        current_ns = await self.current_sim_time_ns()
        physics_ns = await self.observed_physics_step_ns()
        self._latest_contacts = {}
        stats_version = self._stats_topic_version_snapshot()
        if execution_started is None:
            # A normal barrier has no staged command. Advance the complete
            # logical interval in one bounded request; the exact
            # WorldStatistics barrier below remains authoritative.
            await asyncio.sleep(BARRIER_SCHEDULING_SETTLE_S)
            pose_version = self._pose_topic_version_snapshot()
            await self._advance_world_control(
                start_ns=current_ns,
                iterations=iterations,
                physics_ns=physics_ns,
            )
        else:
            # Complete one real resume step before releasing the staged MAVSDK
            # call. Hold the world at that intermediate boundary until the call
            # has entered its async dispatch path, then spend all remaining
            # deterministic steps in one bounded request while PX4 can produce
            # COMMAND_ACK records.
            await asyncio.sleep(BARRIER_SCHEDULING_SETTLE_S)
            await self._advance_world_control(
                start_ns=current_ns,
                iterations=1,
                physics_ns=physics_ns,
            )
            execution_started.set()
            if command_dispatched is None:
                raise Px4ServiceError("command dispatch synchronization is missing")
            await command_dispatched.wait()
            pose_version = self._pose_topic_version_snapshot()
            await self._advance_world_control(
                start_ns=current_ns + physics_ns,
                iterations=iterations - 1,
                physics_ns=physics_ns,
            )
        expected_sim_time_ns = current_ns + iterations * physics_ns
        await self._wait_for_world_paused(
            expected_sim_time_ns=expected_sim_time_ns,
            minimum_version=stats_version,
            timeout_s=PAUSED_STATE_CONFIRM_TIMEOUT_S,
        )
        pose_barrier_time = time.monotonic()
        expected_models = frozenset(
            str(vehicle["gazebo_model_name"])
            for vehicle in self._require_config().vehicles
        )
        self._latest_poses = await self._wait_for_pose_update(
            pose_version,
            expected_models=expected_models,
            timeout_s=self._require_config().command_timeout_ms / 1000,
            minimum_received_at=pose_barrier_time,
        )
        try:
            self._latest_contacts = await self._drain_contact_events()
        except Exception as exc:
            raise Px4ServiceError(
                "Gazebo contact journal did not provide classified events"
            ) from exc
        await self._capture_camera_frames(expected_sim_time_ns)

    @staticmethod
    def _physical_cameras(config: PreparedConfig) -> tuple[dict[str, Any], ...]:
        cameras: dict[str, dict[str, Any]] = {}
        for declaration in config.camera_declarations or config.inspections:
            camera = {
                field: declaration[field]
                for field in (
                    "camera_id", "camera_vehicle_id", "camera_mount",
                    "resolution_width_px", "resolution_height_px",
                )
            }
            camera_id = str(camera["camera_id"])
            if camera_id in cameras and cameras[camera_id] != camera:
                raise Px4ServiceError(
                    f"inspection declarations conflict for camera {camera_id}"
                )
            cameras[camera_id] = camera
        return tuple(cameras.values())

    @staticmethod
    def _camera_image_type() -> Any:
        from gz.msgs10.image_pb2 import Image
        return Image

    @staticmethod
    def _camera_message_payload(message: Any) -> dict[str, Any]:
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(message)

    async def _start_camera_subscribers(self, config: PreparedConfig) -> None:
        cameras = self._physical_cameras(config)
        if not cameras:
            return
        topics = {str(camera["camera_id"]): self._camera_topic(camera) for camera in cameras}
        if len(set(topics.values())) != len(topics):
            raise Px4ServiceError("distinct camera IDs refer to the same physical image topic")
        async with self._gazebo_transport_lock:
            if self._camera_stream_active:
                raise Px4ServiceError("Gazebo camera subscribers were already initialized")
            node, _world_control, boolean_type = self._require_gazebo_transport(config)
            image_type = self._camera_image_type()
            with self._camera_stream_lock:
                self._camera_stream_generation += 1
                generation = self._camera_stream_generation
                self._camera_stream_active = True
                self._camera_stream_failed = False
                self._camera_stream_failure = None
                self._camera_stream_latest = {}
                self._camera_stream_versions = {camera_id: 0 for camera_id in topics}
            self._camera_stream_node = node
            self._camera_stream_topics = topics
            self._camera_boolean_type = boolean_type
            try:
                # These binding calls register locally and do not await discovery.
                # No detached to_thread registration can finish after cancellation.
                for camera_id, topic in topics.items():
                    def callback(message: Any, *, camera_id: str = camera_id) -> None:
                        with self._camera_stream_lock:
                            if (
                                not self._camera_stream_active
                                or generation != self._camera_stream_generation
                            ):
                                return
                        try:
                            copied = image_type()
                            copied.CopyFrom(message)
                        except Exception as exc:
                            with self._camera_stream_lock:
                                if generation == self._camera_stream_generation:
                                    self._camera_stream_failure = str(exc)
                            return
                        with self._camera_stream_lock:
                            if (
                                not self._camera_stream_active
                                or generation != self._camera_stream_generation
                            ):
                                return
                            self._camera_stream_versions[camera_id] += 1
                            self._camera_stream_latest[camera_id] = (
                                self._camera_stream_versions[camera_id], copied
                            )

                    if node.subscribe(image_type, topic, callback) is not True:
                        raise Px4ServiceError(f"Gazebo camera subscription rejected: {topic}")
                    self._camera_subscribed_topics.append(topic)
                    self._camera_callbacks.append(callback)
                publisher = node.advertise(CAMERA_TRIGGER_TOPIC, boolean_type)
                if publisher is None or not publisher.valid():
                    raise Px4ServiceError("Gazebo camera trigger publisher is invalid")
                self._camera_trigger_publisher = publisher
            except BaseException:
                self._release_camera_subscribers()
                raise

    def _release_camera_subscribers(self) -> None:
        # Caller holds the transport lock. Invalidate callbacks before unregistering.
        with self._camera_stream_lock:
            self._camera_stream_active = False
            self._camera_stream_generation += 1
            self._camera_stream_failed = False
            self._camera_stream_failure = None
            self._camera_stream_versions = {}
            self._camera_stream_latest = {}
        node = self._camera_stream_node
        topics = self._camera_subscribed_topics
        self._camera_subscribed_topics = []
        self._camera_stream_topics = {}
        self._camera_trigger_publisher = None
        self._camera_boolean_type = None
        self._camera_stream_node = None
        self._camera_frames = {}
        for topic in topics:
            try:
                if node.unsubscribe(topic) is not True:
                    raise Px4ServiceError("unsubscribe was not accepted")
            except Exception as exc:
                try:
                    self._record_gz_diagnostic(
                        "camera-unsubscribe-failed", {"topic": topic, "error": str(exc)}
                    )
                except OSError:
                    pass
        self._camera_callbacks = []

    async def _stop_camera_subscribers(self) -> None:
        async with self._gazebo_transport_lock:
            self._release_camera_subscribers()

    async def _capture_camera_frames(self, expected_sim_time_ns: int) -> None:
        async with self._camera_capture_lock:
            self._camera_frames = {}
            config = self._require_config()
            cameras = self._physical_cameras(config)
            if self._time_origin_ns == 0 or not cameras:
                return
            expected = self._time_origin_ns + expected_sim_time_ns
            timeout = config.command_timeout_ms / 1000
            deadline = time.monotonic() + timeout
            baselines: dict[str, int] = {}
            observed_times: dict[str, int] = {}
            connected = False
            generation = self._camera_stream_generation

            def timeout_error(phase: str) -> Px4ServiceError:
                with self._camera_stream_lock:
                    detail = {
                        "phase": phase,
                        "expected_engine_time_ns": expected,
                        "timeout_s": timeout,
                        "registered": self._camera_stream_active,
                        "trigger_has_connections": connected,
                        "baseline_sequences": baselines,
                        "last_sequences": dict(self._camera_stream_versions),
                        "last_engine_times_ns": dict(observed_times),
                    }
                try:
                    self._record_gz_diagnostic("camera-capture-timeout", detail)
                except OSError:
                    pass
                return Px4ServiceError(
                    "Gazebo camera timed out waiting for exact barrier time: "
                    + _canonical_json(detail).decode("utf-8")
                )

            def check_active() -> None:
                with self._camera_stream_lock:
                    if (
                        not self._camera_stream_active
                        or generation != self._camera_stream_generation
                        or self._camera_stream_failed
                    ):
                        raise Px4ServiceError("Gazebo camera stream is inactive or requires reset")
                    if self._camera_stream_failure is not None:
                        raise Px4ServiceError(
                            "Gazebo camera callback failed: " + self._camera_stream_failure
                        )

            try:
                if {str(camera["camera_id"]) for camera in cameras} != set(
                    self._camera_stream_topics
                ):
                    raise Px4ServiceError("Gazebo camera subscription inventory changed")
                while True:
                    # Wait outside the transport lock so shutdown remains possible.
                    async with self._gazebo_transport_lock:
                        check_active()
                        if time.monotonic() >= deadline:
                            raise timeout_error("trigger-connection")
                        publisher = self._camera_trigger_publisher
                        connected = bool(publisher.has_connections())
                        if connected:
                            # Snapshot every sensor immediately before ONE shared trigger.
                            with self._camera_stream_lock:
                                baselines = dict(self._camera_stream_versions)
                            if publisher.publish(self._camera_boolean_type(data=True)) is not True:
                                raise Px4ServiceError("Gazebo camera trigger publication failed")
                            break
                    if time.monotonic() >= deadline:
                        raise timeout_error("trigger-connection")
                    await asyncio.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
                examined = dict(baselines)
                validated: dict[str, CapturedCameraFrame] = {}
                while len(validated) != len(cameras):
                    check_active()
                    if time.monotonic() >= deadline:
                        raise timeout_error("image-delivery")
                    with self._camera_stream_lock:
                        latest = dict(self._camera_stream_latest)
                    for camera in cameras:
                        camera_id = str(camera["camera_id"])
                        sample = latest.get(camera_id)
                        if camera_id in validated or sample is None or sample[0] <= examined[camera_id]:
                            continue
                        examined[camera_id] = sample[0]
                        frame = self._parse_camera_frame(
                            self._camera_message_payload(sample[1]),
                            vehicle_id=str(camera["camera_vehicle_id"]),
                            camera_id=camera_id,
                            expected_width=int(camera["resolution_width_px"]),
                            expected_height=int(camera["resolution_height_px"]),
                        )
                        observed_times[camera_id] = frame.engine_sim_time_ns
                        if frame.engine_sim_time_ns < expected:
                            continue
                        if frame.engine_sim_time_ns != expected:
                            raise Px4ServiceError(
                                "Gazebo camera frame timestamp is not the exact barrier time: "
                                f"observed={frame.engine_sim_time_ns}, expected={expected}"
                            )
                        validated[camera_id] = frame
                    if len(validated) == len(cameras):
                        check_active()
                        if time.monotonic() >= deadline:
                            raise timeout_error("image-validation")
                        self._camera_frames = validated
                        return
                    if time.monotonic() >= deadline:
                        raise timeout_error("image-delivery")
                    await asyncio.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
            except BaseException:
                self._camera_frames = {}
                with self._camera_stream_lock:
                    if generation == self._camera_stream_generation:
                        # A late image from a cancelled trigger cannot satisfy a retry.
                        self._camera_stream_failed = True
                raise

    async def wait_for_telemetry(self) -> None:
        await self._wait_for_telemetry_cache(self._require_config())

    def _camera_topic(self, inspection: Mapping[str, Any]) -> str:
        config = self._require_config()
        vehicle_id = str(inspection["camera_vehicle_id"])
        vehicle = next(
            (
                item
                for item in config.vehicles
                if str(item["vehicle_id"]) == vehicle_id
            ),
            None,
        )
        if vehicle is None:
            raise Px4ServiceError(
                f"inspection camera vehicle is not declared: {vehicle_id}"
            )
        return (
            f"/world/{config.world_name}/model/{vehicle['gazebo_model_name']}"
            "/link/camera_link/sensor/camera/image"
        )

    def camera_frame(self, camera_id: str) -> CapturedCameraFrame:
        try:
            return self._camera_frames[camera_id]
        except KeyError as exc:
            raise Px4ServiceError(
                f"Gazebo has not emitted a camera frame for {camera_id}"
            ) from exc

    @staticmethod
    def _parse_camera_frame(
        payload: Mapping[str, Any],
        *,
        vehicle_id: str,
        camera_id: str,
        expected_width: int,
        expected_height: int,
    ) -> CapturedCameraFrame:
        width = _proto3_int(payload.get("width"), label="camera.width", minimum=1)
        height = _proto3_int(payload.get("height"), label="camera.height", minimum=1)
        if width != expected_width or height != expected_height:
            raise Px4ServiceError(
                "Gazebo camera dimensions differ from the declared sensor: "
                f"observed={width}x{height}, expected={expected_width}x{expected_height}"
            )
        pixel_format = payload.get("pixelFormatType")
        if pixel_format != "RGB_INT8":
            raise Px4ServiceError(
                f"Gazebo camera pixel format is not RGB_INT8: {pixel_format!r}"
            )
        step = _proto3_int(payload.get("step"), label="camera.step", minimum=1)
        if step != width * 3:
            raise Px4ServiceError("Gazebo camera row step is not packed RGB")
        encoded = payload.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise Px4ServiceError("Gazebo camera frame omitted base64 data")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise Px4ServiceError(
                "Gazebo camera frame data is not strict base64"
            ) from exc
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise Px4ServiceError("Gazebo camera frame data is not canonical base64")
        header = _strict_object(
            payload.get("header"),
            required=frozenset({"stamp"}),
            optional=frozenset({"data"}),
            label="camera.header",
        )
        if "data" in header:
            if not isinstance(header["data"], list):
                raise Px4ServiceError("camera.header.data must be a repeated Header.Map")
            for entry in header["data"]:
                metadata = _strict_object(
                    entry,
                    required=frozenset(),
                    optional=frozenset({"key", "value"}),
                    label="camera.header.data entry",
                )
                # Native protobuf JSON omits scalar/repeated default values.
                key = metadata.get("key", "")
                values = metadata.get("value", [])
                if (
                    not isinstance(key, str)
                    or not isinstance(values, list)
                    or any(not isinstance(value, str) for value in values)
                ):
                    raise Px4ServiceError("camera.header.data requires string keys and values")
        stamp = _strict_object(
            header["stamp"],
            required=frozenset(),
            optional=frozenset({"sec", "nsec"}),
            label="camera.header.stamp",
        )
        sec = _proto3_int(stamp.get("sec", 0), label="camera.header.stamp.sec", minimum=0)
        nsec = _proto3_int(
            stamp.get("nsec", 0), label="camera.header.stamp.nsec", minimum=0
        )
        if nsec >= 1_000_000_000:
            raise Px4ServiceError("Gazebo camera header nsec is outside the valid range")
        engine_sim_time_ns = sec * 1_000_000_000 + nsec
        return CapturedCameraFrame(
            vehicle_id=vehicle_id,
            camera_id=camera_id,
            width=width,
            height=height,
            pixel_format=pixel_format,
            engine_sim_time_ns=engine_sim_time_ns,
            png_bytes=_rgb_png(width=width, height=height, pixels=raw),
        )


    async def telemetry(self) -> tuple[VehicleTelemetry, ...]:
        config = self._require_config()
        sim_time_ns = await self.current_sim_time_ns()
        poses = dict(self._latest_poses)
        if not poses:
            raise Px4ServiceError("Gazebo has not emitted a pose at the barrier")
        contacts = await self._contact_names()
        records: list[VehicleTelemetry] = []
        for vehicle in config.vehicles:
            session = self._mavsdk.get(vehicle["vehicle_id"])
            if session is None:
                raise Px4ServiceError(
                    f"MAVSDK session missing for {vehicle['vehicle_id']}"
                )
            pose = poses.get(vehicle["gazebo_model_name"])
            if pose is None:
                raise Px4ServiceError(
                    f"Gazebo omitted pose for {vehicle['gazebo_model_name']}"
                )
            cached = self._telemetry_cache.get(str(vehicle["vehicle_id"]), {})
            required = {
                "global_position",
                "velocity",
                "angular_velocity",
                "flight_mode",
                "armed",
                "in_air",
                "landed_state",
                "battery_percent",
                "health",
            }
            missing = required - cached.keys()
            if missing:
                raise Px4ServiceError(
                    f"MAVSDK telemetry cache is missing {sorted(missing)} for "
                    f"{vehicle['vehicle_id']}"
                )
            global_position = cached["global_position"]
            velocity = cached["velocity"]
            angular_velocity = cached["angular_velocity"]
            if not isinstance(global_position, dict):
                raise Px4ServiceError("MAVSDK global position cache is invalid")
            if not isinstance(velocity, dict):
                raise Px4ServiceError("MAVSDK velocity cache is invalid")
            if not isinstance(angular_velocity, dict):
                raise Px4ServiceError("MAVSDK angular velocity cache is invalid")
            position_wgs84 = {
                name: global_position[name]
                for name in ("longitude_deg", "latitude_deg", "altitude_m")
            }
            flight_mode = _flight_mode_scalar(
                cached["flight_mode"],
                label="MAVSDK telemetry cache flight_mode",
            )
            armed = cached["armed"]
            in_air = cached["in_air"]
            landed_state = cached["landed_state"]
            battery_percent = cached["battery_percent"]
            health = cached["health"]
            if (
                not isinstance(armed, bool)
                or not isinstance(in_air, bool)
                or not isinstance(landed_state, str)
                or not isinstance(battery_percent, float)
                or not isinstance(health, str)
            ):
                raise Px4ServiceError("MAVSDK telemetry cache types are invalid")
            if landed_state not in {"ON_GROUND", "IN_AIR", "TAKING_OFF", "LANDING"}:
                raise Px4ServiceError("MAVSDK landed_state is not authoritative")
            if (landed_state == "ON_GROUND" and in_air) or (
                landed_state == "IN_AIR" and not in_air
            ):
                raise Px4ServiceError("MAVSDK in_air and landed_state disagree")
            vehicle_id = str(vehicle["vehicle_id"])
            vehicle_contacts = contacts.get(vehicle_id, ())
            ground_contact = any(
                contact.startswith("ground.") or contact.startswith("launch_pad.")
                for contact in vehicle_contacts
            )
            collision_contact = any(
                not contact.startswith("ground.")
                and not contact.startswith("launch_pad.")
                for contact in vehicle_contacts
            )
            records.append(
                VehicleTelemetry(
                    vehicle_id=vehicle_id,
                    pose_json=_canonical_json(pose).decode("utf-8"),
                    position_wgs84_json=_canonical_json(position_wgs84).decode("utf-8"),
                    velocity_json=_canonical_json(velocity).decode("utf-8"),
                    angular_velocity_json=_canonical_json(angular_velocity).decode(
                        "utf-8"
                    ),
                    attitude_json=_canonical_json(
                        {
                            "roll_rad": pose["roll_rad"],
                            "pitch_rad": pose["pitch_rad"],
                            "yaw_rad": pose["yaw_rad"],
                        }
                    ).decode("utf-8"),
                    flight_mode=flight_mode,
                    armed=armed,
                    in_air=in_air,
                    landed=landed_state == "ON_GROUND",
                    landed_state=landed_state,
                    battery_percent=battery_percent,
                    health=health,
                    contacts=vehicle_contacts,
                    ground_contact=ground_contact,
                    collision_contact=collision_contact,
                    simulation_time_ns=sim_time_ns,
                )
            )
        return tuple(records)

    def read_command_audit_records(
        self, vehicle_id: str, *, require_complete: bool = False
    ) -> tuple[dict[str, object], ...]:
        try:
            cursor = self._command_audit_journals[vehicle_id]
        except KeyError as exc:
            raise Px4ServiceError(
                f"MAVSDK command audit journal is missing for {vehicle_id}"
            ) from exc
        return _read_command_audit_records(cursor, require_complete=require_complete)

    async def accept_command(self, request: Mapping[str, Any]) -> None:
        config = self._require_config()
        vehicle_id = _require_vehicle_id(request, config.vehicles)
        arguments = _command_argument_map(request)
        tool_id = request.get("tool_id")
        if tool_id == GOTO_TOOL:
            missing = [
                name for name in REQUIRED_GOTO_ARGUMENTS if name not in arguments
            ]
            if missing:
                raise Px4CommandPhaseError(
                    last_success="received",
                    detail=f"flight.goto missing arguments: {missing}",
                )
            for name in REQUIRED_GOTO_ARGUMENTS[1:]:
                _finite_number(arguments[name], label=name)
        elif tool_id in {ARM_TOOL, DISARM_TOOL, LAND_TOOL, HOLD_TOOL}:
            missing = [
                name for name in REQUIRED_VEHICLE_ARGUMENT if name not in arguments
            ]
            if missing:
                raise Px4CommandPhaseError(
                    last_success="received",
                    detail=f"{tool_id} missing arguments: {missing}",
                )
        elif tool_id == TAKEOFF_TOOL:
            missing = [
                name for name in REQUIRED_TAKEOFF_ARGUMENTS if name not in arguments
            ]
            if missing:
                raise Px4CommandPhaseError(
                    last_success="received",
                    detail=f"flight.takeoff missing arguments: {missing}",
                )
            _finite_number(arguments["altitude_m"], label="altitude_m")
        else:
            raise Px4CommandPhaseError(
                last_success="received",
                detail=f"unsupported PX4/MAVSDK tool {tool_id!r}",
            )
        if vehicle_id not in self._mavsdk:
            raise Px4CommandPhaseError(
                last_success="received",
                detail=f"MAVSDK session is not connected for {vehicle_id}",
            )

    async def apply_command(self, request: Mapping[str, Any]) -> None:
        config = self._require_config()
        session = self._mavsdk[_require_vehicle_id(request, config.vehicles)]
        timeout = config.command_timeout_ms / 1000
        try:
            await asyncio.wait_for(
                self._issue_mavsdk(session, request), timeout=timeout
            )
        except Px4CommandPhaseError:
            raise
        except Exception as exc:
            raise Px4CommandPhaseError(
                last_success="accepted",
                detail=f"MAVSDK did not accept {request.get('tool_id')}: {exc}",
            ) from exc

    async def snapshot(self) -> Mapping[str, object]:
        telemetry = await self.telemetry()
        return {
            "sim_time_ns": await self.current_sim_time_ns(),
            "vehicles": [item.as_json() for item in telemetry],
        }

    async def _close_mavsdk_clients(self) -> None:
        tasks = self._telemetry_tasks
        self._telemetry_tasks = []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._telemetry_cache = {}
        self._telemetry_received_monotonic_ns = {}
        self._telemetry_failure = None
        for session in self._mavsdk.values():
            close = getattr(session, "close", None)
            if close is not None:
                try:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
        self._mavsdk = {}

    async def stop(self) -> None:
        await self._stop_camera_subscribers()
        await self._close_mavsdk_clients()
        await self._stop_stats_subscriber()
        await self._stop_pose_subscriber()
        await self._stop_airspace_subscriber()
        await self._stop_contact_subscribers()
        self._latest_poses = {}
        self._latest_contacts = {}
        self._command_audit_journals = {}
        self._gazebo_transport_node = None
        self._gazebo_world_control_type = None
        self._gazebo_boolean_type = None
        self._time_origin_ns = 0
        processes = list(reversed(self._processes))
        self._processes = []
        self._log_paths = {}
        for process in processes:
            _signal_process_group(process, signal.SIGTERM)
        for process in processes:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                _signal_process_group(process, signal.SIGKILL)
                if process.returncode is None:
                    try:
                        await process.wait()
                    except ProcessLookupError:
                        pass

    def _require_config(self) -> PreparedConfig:
        if self._config is None:
            raise Px4ServiceError("PX4 stack has no prepared configuration")
        return self._config

    def _verify_required_commands(self, config: PreparedConfig) -> None:
        for command in config.required_commands:
            if self._executable_lookup(command) is None:
                raise Px4ServiceError(f"declared executable is not on PATH: {command}")

    def _load_identity_file(self) -> dict[str, Any]:
        try:
            identity = _parse_json(self._identity_path.read_bytes())
        except (OSError, Px4ServiceError) as exc:
            raise Px4ServiceError(
                f"PX4/Gazebo/MAVSDK identity file is missing or invalid: {self._identity_path}"
            ) from exc
        if identity.get("protocol_version") != PROTOCOL_VERSION:
            raise Px4ServiceError(
                "identity.json PX4 RPC protocol is not the production pin"
            )
        px4 = _strict_object(
            identity.get("px4"),
            required=frozenset({"version", "commit", "sha256"}),
            label="identity.px4",
        )
        gazebo = _strict_object(
            identity.get("gazebo"),
            required=frozenset({"version", "commit", "sha256"}),
            label="identity.gazebo",
        )
        mavsdk = _strict_object(
            identity.get("mavsdk"),
            required=frozenset({"version", "commit", "source_sha256", "patch_sha256"}),
            label="identity.mavsdk",
        )
        mavlink = _strict_object(
            identity.get("mavlink"),
            required=frozenset(
                {
                    "commit",
                    "source_sha256",
                    "offline_build_patch_sha256",
                    "pymavlink_commit",
                    "pymavlink_source_sha256",
                    "pymavlink_build_lock_sha256",
                }
            ),
            label="identity.mavlink",
        )
        if px4["version"] != PX4_VERSION or px4["commit"] != PX4_COMMIT:
            raise Px4ServiceError(
                "identity.json PX4 identity is not the production pin"
            )
        if gazebo["version"] != GAZEBO_VERSION or gazebo["commit"] != GAZEBO_COMMIT:
            raise Px4ServiceError(
                "identity.json Gazebo identity is not the production pin"
            )
        if mavsdk["version"] != MAVSDK_VERSION or mavsdk["commit"] != MAVSDK_COMMIT:
            raise Px4ServiceError(
                "identity.json MAVSDK identity is not the production pin"
            )
        if (
            _sha256(mavsdk["source_sha256"], label="identity.mavsdk.source_sha256")
            != MAVSDK_SOURCE_SHA256
            or _sha256(mavsdk["patch_sha256"], label="identity.mavsdk.patch_sha256")
            != MAVSDK_PATCH_SHA256
        ):
            raise Px4ServiceError(
                "identity.json MAVSDK source or benchmark patch is not pinned"
            )
        if (
            mavlink["commit"] != MAVLINK_COMMIT
            or _sha256(mavlink["source_sha256"], label="identity.mavlink.source_sha256")
            != MAVLINK_SOURCE_SHA256
            or _sha256(
                mavlink["offline_build_patch_sha256"],
                label="identity.mavlink.offline_build_patch_sha256",
            )
            != MAVLINK_OFFLINE_PATCH_SHA256
            or mavlink["pymavlink_commit"] != PYMAVLINK_COMMIT
            or _sha256(
                mavlink["pymavlink_source_sha256"],
                label="identity.mavlink.pymavlink_source_sha256",
            )
            != PYMAVLINK_SOURCE_SHA256
            or _sha256(
                mavlink["pymavlink_build_lock_sha256"],
                label="identity.mavlink.pymavlink_build_lock_sha256",
            )
            != PYMAVLINK_BUILD_LOCK_SHA256
        ):
            raise Px4ServiceError(
                "identity.json MAVLink source is not the production pin"
            )
        if _sha256(px4["sha256"], label="identity.px4.sha256") != PX4_SHA256:
            raise Px4ServiceError(
                "identity.json PX4 binary digest is not the production pin"
            )
        if _sha256(gazebo["sha256"], label="identity.gazebo.sha256") != GAZEBO_SHA256:
            raise Px4ServiceError(
                "identity.json Gazebo binary digest is not the production pin"
            )
        if (
            _sha256(identity.get("mavsdk_server_sha256"), label="mavsdk_server_sha256")
            != MAVSDK_SERVER_SHA256
        ):
            raise Px4ServiceError(
                "identity.json mavsdk_server digest is not the production pin"
            )
        return identity

    def _verify_live_binaries(
        self, identity: Mapping[str, Any], config: PreparedConfig
    ) -> None:
        if (
            config.px4_executable != "px4"
            or config.gazebo_executable != "gz"
            or config.mavsdk_server_executable != "mavsdk-server"
        ):
            raise Px4ServiceError("PX4 service requires the pinned executable names")
        for path, digest, label in (
            (self._px4_bin, PX4_SHA256, "PX4"),
            (self._gz_bin, GAZEBO_SHA256, "Gazebo"),
            (self._mavsdk_bin, MAVSDK_SERVER_SHA256, "mavsdk_server"),
        ):
            if not path.is_file():
                raise Px4ServiceError(f"{label} binary is missing: {path}")
            observed = _digest_bytes(path.read_bytes())
            if observed != digest:
                raise Px4ServiceError(
                    f"live {label} digest does not match the pinned identity"
                )
        versions = self._run_blocking(
            (str(self._gz_bin), "sim", "--versions"),
            label="gz sim --versions",
            timeout_s=config.command_timeout_ms / 1000,
        )
        first_line = next(
            (line.strip() for line in versions.splitlines() if line.strip()),
            "",
        )
        if first_line != GAZEBO_VERSION:
            raise Px4ServiceError(
                "live Gazebo version does not match identity: "
                f"observed={first_line!r}, declared={GAZEBO_VERSION!r}"
            )
        try:
            installed = package_version("mavsdk")
        except PackageNotFoundError as exc:
            raise Px4ServiceError(
                "declared mavsdk Python package is not installed"
            ) from exc
        if installed != MAVSDK_VERSION:
            raise Px4ServiceError(
                f"Python mavsdk identity mismatch: expected {MAVSDK_VERSION}, got {installed}"
            )
        if not self._px4_data_dir.is_dir():
            raise Px4ServiceError(f"PX4 data dir is missing: {self._px4_data_dir}")
        if config.world_input is not None:
            # Declared bundle world: preflight accepts ONLY the staged
            # input-volume SDF with a fresh symlink check + SHA-256 pin.  A
            # missing or tampered staged world rejects here; there is NO silent
            # fallback to the scenario/image world path.
            if self._bundle_root is None:
                raise Px4ServiceError(
                    "bundle world input requires AERO_BENCH_BUNDLE_DIR"
                )
            bundle_relative = PurePosixPath(*config.world_input["path"].split("/")[1:])
            _resolve_root_file(
                self._bundle_root,
                {
                    "path": str(bundle_relative),
                    "sha256": config.world_input["sha256"],
                },
                label="logistics bundle world SDF",
            )
        else:
            if not config.world_source_sdf.is_file():
                raise Px4ServiceError(
                    f"ResolvedScenario Gazebo world SDF is missing: {config.world_source_sdf}"
                )
        camera_model = self._gz_models_dir / "mono_cam" / "model.sdf"
        if not camera_model.is_file():
            raise Px4ServiceError(f"patched mono_cam model is missing: {camera_model}")
        if _digest_bytes(camera_model.read_bytes()) != MONO_CAM_MODEL_SHA256:
            raise Px4ServiceError(
                "mono_cam model digest does not match the patched camera-sensor contract"
            )

    async def _start(
        self, config: PreparedConfig, *, seed: int, progress_started_at: float
    ) -> None:
        self._config = config
        await self._start_gazebo(config, seed=seed)
        _startup_progress("stack-start.gazebo-complete", started_at=progress_started_at)
        await self._start_stats_subscriber(config)
        await self._start_pose_subscriber(config)
        await self._start_airspace_subscriber(config)
        await self._assert_world_paused()
        observed_step = await self.observed_physics_step_ns()
        if observed_step != config.physics_step_ns:
            raise Px4ServiceError(
                "Gazebo max_step_size does not match configured physics_step_ns: "
                f"observed={observed_step}, configured={config.physics_step_ns}"
            )
        await self._start_contact_subscribers(config)
        _startup_progress("stack-start.transport-complete", started_at=progress_started_at)
        for vehicle in config.vehicles:
            await self._start_px4(config, vehicle, seed=seed)
        _startup_progress(
            "stack-start.px4-spawned",
            started_at=progress_started_at,
            detail={"vehicle_count": len(config.vehicles)},
        )
        await self._wait_for_vehicle_poses(config)
        await self._start_camera_subscribers(config)
        _startup_progress("stack-start.vehicle-poses-complete", started_at=progress_started_at)
        for vehicle in config.vehicles:
            await self._wait_for_log(
                f"px4-{vehicle['vehicle_id']}",
                "PX4_GZ_MODEL_NAME set, PX4 will attach to existing model",
                timeout_s=config.command_timeout_ms / 1000,
            )
        for vehicle in config.vehicles:
            await self._wait_for_log(
                f"px4-{vehicle['vehicle_id']}",
                "Startup script returned successfully",
                timeout_s=config.command_timeout_ms / 1000,
            )
        _startup_progress("stack-start.px4-ready", started_at=progress_started_at)
        for vehicle in config.vehicles:
            await self._start_mavsdk_server(config, vehicle)
            await self._wait_for_tcp(
                int(vehicle["mavsdk_grpc_port"]),
                timeout_s=config.command_timeout_ms / 1000,
            )
        _startup_progress("stack-start.mavsdk-servers-ready", started_at=progress_started_at)
        await self._connect_mavsdk(config)
        _startup_progress("stack-start.mavsdk-connected", started_at=progress_started_at)

    async def _start_gazebo(self, config: PreparedConfig, *, seed: int) -> None:
        source = config.world_source_sdf
        world_copy = self._tmp_dir / "worlds" / config.world_sdf
        ensure_paused_world_sdf(
            source,
            world_copy,
            vehicles=config.vehicles,
            inspections=config.inspections,
            target_model_sdfs=config.inspection_target_model_sdfs,
            airspace_transition=config.airspace_transition,
        )
        argv = (
            str(self._gz_bin),
            "sim",
            "--seed",
            str(seed),
            "-s",
            str(world_copy),
        )
        if "-r" in argv or "--run" in argv:
            raise Px4ServiceError("Gazebo must start paused without -r/--run")
        await self._spawn_process(
            tuple(argv),
            cwd=self._tmp_dir,
            env=self._base_env(config),
            log_name="gazebo",
        )
        await self._wait_for_world(config)

    def _contact_topic_bindings(
        self, config: PreparedConfig
    ) -> tuple[tuple[str, str, str], ...]:
        bindings: list[tuple[str, str, str]] = []
        for vehicle in sorted(
            config.vehicles, key=lambda item: str(item["vehicle_id"])
        ):
            vehicle_id = str(vehicle["vehicle_id"])
            model_name = str(vehicle["gazebo_model_name"])
            for link_name, collision_name in CONTACT_SENSOR_BINDINGS:
                topic = (
                    f"/world/{config.world_name}/model/{model_name}/link/{link_name}"
                    f"/sensor/aero_contact_{collision_name}/contact"
                )
                bindings.append((topic, vehicle_id, collision_name))
        return tuple(bindings)

    async def _start_contact_subscribers(self, config: PreparedConfig) -> None:
        with self._contact_lock:
            if self._contact_active or self._contact_subscribed_topics:
                raise Px4ServiceError(
                    "Gazebo contact subscribers were already initialized"
                )
            self._contact_active = True
            self._contact_failure = None
            self._contact_window = {
                str(vehicle["vehicle_id"]): set() for vehicle in config.vehicles
            }
            self._contact_subscribed_topics = ()
            self._contact_callbacks = []
        bindings = self._contact_topic_bindings(config)
        expected_topics = {topic for topic, _, _ in bindings}
        listing = await self._gz_cli(("topic", "--list"), config, timeout_s=5.0)
        observed_topics = {
            line.strip() for line in listing.splitlines() if line.strip()
        }
        instrumented_topics = {
            topic for topic in observed_topics if "/sensor/aero_contact_" in topic
        }
        if instrumented_topics != expected_topics:
            with self._contact_lock:
                self._contact_active = False
                self._contact_window = {}
            raise Px4ServiceError(
                "Gazebo contact sensor topic inventory is not exact: "
                f"missing={sorted(expected_topics - instrumented_topics)}, "
                f"extra={sorted(instrumented_topics - expected_topics)}"
            )
        try:
            node, _world_control_type, _boolean_type = self._require_gazebo_transport(
                config
            )
            if "/usr/lib/python3/dist-packages" not in sys.path:
                sys.path.append("/usr/lib/python3/dist-packages")
            from gz.msgs10.contacts_pb2 import Contacts
        except (ImportError, OSError) as exc:
            with self._contact_lock:
                self._contact_active = False
                self._contact_window = {}
            raise Px4ServiceError(
                "Gazebo Python Contacts message binding is unavailable"
            ) from exc

        subscribed: list[str] = []
        try:
            for topic, _vehicle_id, _collision_name in bindings:
                callback = self._contact_callback(topic)
                self._contact_callbacks.append(callback)
                async with self._gazebo_transport_lock:
                    accepted = await asyncio.to_thread(
                        node.subscribe,
                        Contacts,
                        topic,
                        callback,
                    )
                if accepted is not True:
                    raise Px4ServiceError(
                        "Gazebo contact transport subscription was not accepted: "
                        f"{topic}"
                    )
                subscribed.append(topic)
                with self._contact_lock:
                    self._contact_subscribed_topics = tuple(subscribed)
        except BaseException:
            await self._stop_contact_subscribers()
            raise

    def _contact_callback(self, topic: str) -> Callable[[Any], None]:
        def callback(message: Any) -> None:
            self._on_contact_topic_message(topic, message)

        return callback

    def _on_contact_topic_message(self, topic: str, message: Any) -> None:
        with self._contact_lock:
            if not self._contact_active or self._contact_failure is not None:
                return
        try:
            payload = self._contacts_message_payload(message)
            parsed = self._parse_contact_names(payload)
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, Px4ServiceError)
                else Px4ServiceError(
                    f"Gazebo Contacts message could not be decoded on {topic}: {exc}"
                )
            )
            with self._contact_lock:
                if self._contact_active and self._contact_failure is None:
                    self._contact_failure = failure
            return
        with self._contact_lock:
            if not self._contact_active:
                return
            for vehicle_id, counterparties in parsed.items():
                window = self._contact_window.get(vehicle_id)
                if window is None:
                    self._contact_failure = Px4ServiceError(
                        f"Gazebo contact window omitted vehicle {vehicle_id}"
                    )
                    return
                window.update(counterparties)

    @staticmethod
    def _contacts_message_payload(message: Any) -> dict[str, Any]:
        contacts = getattr(message, "contact", None)
        if contacts is None:
            raise Px4ServiceError("Gazebo Contacts message omitted contact")
        payload: list[dict[str, object]] = []
        for index, item in enumerate(contacts):
            first = getattr(item, "collision1", None)
            second = getattr(item, "collision2", None)
            if first is None or second is None:
                raise Px4ServiceError(
                    f"Gazebo Contacts message omitted contact[{index}] collisions"
                )
            payload.append(
                {
                    "collision1": {
                        "id": getattr(first, "id", None),
                        "name": getattr(first, "name", None),
                    },
                    "collision2": {
                        "id": getattr(second, "id", None),
                        "name": getattr(second, "name", None),
                    },
                }
            )
        return {"contact": payload}

    async def _drain_contact_events(self) -> dict[str, tuple[str, ...]]:
        with self._contact_lock:
            if self._contact_failure is not None:
                raise Px4ServiceError(
                    "Gazebo contact subscriber failed"
                ) from self._contact_failure
            if not self._contact_subscribed_topics:
                raise Px4ServiceError("Gazebo contact subscribers are not active")
            snapshot = {
                vehicle_id: tuple(sorted(tokens))
                for vehicle_id, tokens in sorted(self._contact_window.items())
            }
            for tokens in self._contact_window.values():
                tokens.clear()
        return snapshot

    async def _stop_contact_subscribers(self) -> None:
        with self._contact_lock:
            node = self._gazebo_transport_node
            topics = self._contact_subscribed_topics
            self._contact_active = False
            self._contact_subscribed_topics = ()
            self._contact_callbacks = []
            self._contact_window = {}
            self._contact_failure = None
        if node is None or not topics:
            return
        for topic in topics:
            try:
                async with self._gazebo_transport_lock:
                    result = await asyncio.to_thread(node.unsubscribe, topic)
                if result is not True:
                    self._record_gz_diagnostic(
                        "contact-topic-unsubscribe-failed",
                        {"topic": topic, "result": repr(result)},
                    )
            except Exception as exc:
                self._record_gz_diagnostic(
                    "contact-topic-unsubscribe-error",
                    {
                        "topic": topic,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

    async def _start_px4(
        self, config: PreparedConfig, vehicle: Mapping[str, Any], *, seed: int
    ) -> None:
        work_dir = self._tmp_dir / f"px4-{vehicle['vehicle_id']}"
        data_dir = self._tmp_dir / f"px4-data-{vehicle['vehicle_id']}"
        if data_dir.exists():
            shutil.rmtree(data_dir)
        shutil.copytree(self._px4_data_dir, data_dir)
        mavlink_script = data_dir / "init.d-posix" / "px4-rc.mavlink"
        if not mavlink_script.is_file():
            raise Px4ServiceError(
                f"PX4 data dir is missing px4-rc.mavlink: {mavlink_script}"
            )
        mavlink_script.write_text(px4_mavlink_script(vehicle), encoding="utf-8")
        work_dir.mkdir(parents=True, exist_ok=True)
        environment = self._base_env(config)
        environment.update(
            px4_vehicle_environment(
                vehicle,
                world_name=config.world_name,
                work_dir=work_dir,
                seed=seed,
            )
        )
        argv = (
            str(self._px4_bin),
            "-d",
            "-i",
            str(px4_instance(int(vehicle["system_id"]))),
            "-w",
            str(work_dir),
            str(data_dir),
        )
        await self._spawn_process(
            argv,
            cwd=work_dir,
            env=environment,
            log_name=f"px4-{vehicle['vehicle_id']}",
        )

    async def _start_mavsdk_server(
        self, config: PreparedConfig, vehicle: Mapping[str, Any]
    ) -> None:
        vehicle_id = _string(vehicle["vehicle_id"], label="vehicle.vehicle_id")
        self._mavsdk_process_generation += 1
        journal_path = allocate_mavsdk_command_audit_journal_path(
            self._tmp_dir,
            vehicle_id,
            process_generation=self._mavsdk_process_generation,
        )
        argv = mavsdk_server_argv(
            str(self._mavsdk_bin),
            grpc_port=int(vehicle["mavsdk_grpc_port"]),
            udp_port=int(vehicle["mavsdk_udp_port"]),
            incoming_heartbeat_timeout=config.incoming_heartbeat_timeout_s,
            command_audit_journal_path=journal_path,
        )
        await self._spawn_process(
            argv,
            cwd=self._tmp_dir,
            env=self._base_env(config),
            log_name=f"mavsdk-{vehicle_id}",
        )
        self._command_audit_journals[vehicle_id] = CommandAuditJournalCursor(
            path=journal_path
        )

    async def _connect_mavsdk(self, config: PreparedConfig) -> None:
        try:
            from mavsdk import System
        except ImportError as exc:
            raise Px4ServiceError(
                "PX4 provider requires the declared mavsdk Python package"
            ) from exc
        timeout = config.command_timeout_ms / 1000
        for vehicle in config.vehicles:
            system = System(
                mavsdk_server_address="127.0.0.1",
                port=int(vehicle["mavsdk_grpc_port"]),
            )
            try:
                await asyncio.wait_for(system.connect(), timeout=timeout)
                await asyncio.wait_for(self._wait_connected(system), timeout=timeout)
            except Exception as exc:
                raise Px4ServiceError(
                    f"MAVSDK did not connect for {vehicle['vehicle_id']}"
                ) from exc
            vehicle_id = str(vehicle["vehicle_id"])
            self._mavsdk[vehicle_id] = system
            self._start_telemetry_tasks(vehicle_id, system)
        await asyncio.sleep(0.2)

    def _start_telemetry_tasks(self, vehicle_id: str, system: Any) -> None:
        self._telemetry_cache[vehicle_id] = {}
        self._telemetry_received_monotonic_ns[vehicle_id] = {}
        streams = (
            ("global_position", system.telemetry.position()),
            ("velocity", system.telemetry.position_velocity_ned()),
            (
                "angular_velocity",
                system.telemetry.attitude_angular_velocity_body(),
            ),
            ("flight_mode", system.telemetry.flight_mode()),
            ("armed", system.telemetry.armed()),
            ("in_air", system.telemetry.in_air()),
            ("landed_state", system.telemetry.landed_state()),
            ("battery_percent", system.telemetry.battery()),
            ("health", system.telemetry.health()),
            ("attitude_euler", system.telemetry.attitude_euler()),
            ("gps_info", system.telemetry.gps_info()),
            ("raw_gps", system.telemetry.raw_gps()),
        )
        for field, stream in streams:
            self._telemetry_tasks.append(
                asyncio.create_task(
                    self._consume_telemetry(vehicle_id, field, stream),
                    name=f"px4-telemetry-{vehicle_id}-{field}",
                )
            )

    async def _consume_telemetry(
        self, vehicle_id: str, field: str, stream: Any
    ) -> None:
        try:
            async for sample in stream:
                decoded = self._decode_telemetry(field, sample)
                if field == "raw_gps":
                    prior = self._telemetry_cache[vehicle_id].get(field)
                    if (
                        isinstance(prior, dict)
                        and isinstance(decoded, dict)
                        and int(decoded["timestamp_us"]) < int(prior["timestamp_us"])
                    ):
                        raise Px4ServiceError(
                            "MAVSDK raw GPS source timestamp moved backwards"
                        )
                self._telemetry_cache[vehicle_id][field] = decoded
                self._telemetry_received_monotonic_ns[vehicle_id][field] = (
                    time.monotonic_ns()
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._telemetry_failure is None:
                self._telemetry_failure = Px4ServiceError(
                    f"MAVSDK {field} stream failed for {vehicle_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

    @staticmethod
    def _decode_telemetry(field: str, sample: Any) -> object:
        if field == "global_position":
            latitude = _finite_attr(sample, "latitude_deg")
            longitude = _finite_attr(sample, "longitude_deg")
            altitude = _finite_attr(sample, "absolute_altitude_m")
            if not -90.0 <= latitude <= 90.0:
                raise Px4ServiceError("MAVSDK latitude_deg is outside [-90, 90]")
            if not -180.0 <= longitude <= 180.0:
                raise Px4ServiceError("MAVSDK longitude_deg is outside [-180, 180]")
            return {
                "longitude_deg": longitude,
                "latitude_deg": latitude,
                "altitude_m": altitude,
                "relative_altitude_m": _finite_attr(
                    sample, "relative_altitude_m"
                ),
            }
        if field == "velocity":
            velocity = getattr(sample, "velocity", None)
            if velocity is None:
                raise Px4ServiceError("MAVSDK omitted velocity telemetry")
            return {
                "north_m_s": _finite_attr(velocity, "north_m_s"),
                "east_m_s": _finite_attr(velocity, "east_m_s"),
                "down_m_s": _finite_attr(velocity, "down_m_s"),
            }
        if field == "angular_velocity":
            return {
                "x_rad_s": _finite_attr(sample, "roll_rad_s"),
                "y_rad_s": _finite_attr(sample, "pitch_rad_s"),
                "z_rad_s": _finite_attr(sample, "yaw_rad_s"),
            }
        if field == "flight_mode":
            return _flight_mode_scalar(
                getattr(sample, "name", None),
                label="MAVSDK flight_mode telemetry",
            )
        if field == "armed":
            if not isinstance(sample, bool):
                raise Px4ServiceError("MAVSDK armed telemetry is not a boolean")
            return sample
        if field == "in_air":
            if not isinstance(sample, bool):
                raise Px4ServiceError("MAVSDK in_air telemetry is not a boolean")
            return sample
        if field == "landed_state":
            return _flight_mode_scalar(
                getattr(sample, "name", None),
                label="MAVSDK landed_state telemetry",
            )
        if field == "battery_percent":
            remaining = getattr(sample, "remaining_percent", None)
            if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
                raise Px4ServiceError("MAVSDK omitted battery remaining_percent")
            percent = float(remaining)
            if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
                raise Px4ServiceError(
                    "MAVSDK battery remaining_percent is outside 0..100"
                )
            return percent
        if field == "health":
            flags = {
                name: getattr(sample, name)
                for name in dir(sample)
                if name.startswith("is_") and isinstance(getattr(sample, name), bool)
            }
            if not flags:
                raise Px4ServiceError("MAVSDK omitted health flags")
            return _canonical_json(flags).decode("utf-8")
        if field == "attitude_euler":
            return {
                "roll_deg": _finite_attr(sample, "roll_deg"),
                "pitch_deg": _finite_attr(sample, "pitch_deg"),
                "yaw_deg": _finite_attr(sample, "yaw_deg"),
            }
        if field == "gps_info":
            satellites = getattr(sample, "num_satellites", None)
            if (
                not isinstance(satellites, int)
                or isinstance(satellites, bool)
                or not 0 <= satellites <= 255
            ):
                raise Px4ServiceError("MAVSDK GPS info omitted num_satellites")
            fix_type = _flight_mode_scalar(
                getattr(getattr(sample, "fix_type", None), "name", None),
                label="MAVSDK GPS fix_type",
            )
            if fix_type not in {
                "NO_GPS",
                "NO_FIX",
                "FIX_2D",
                "FIX_3D",
                "FIX_DGPS",
                "RTK_FLOAT",
                "RTK_FIXED",
            }:
                raise Px4ServiceError("MAVSDK GPS fix_type is unsupported")
            return {"num_satellites": satellites, "fix_type": fix_type}
        if field == "raw_gps":
            timestamp_us = getattr(sample, "timestamp_us", None)
            if (
                not isinstance(timestamp_us, int)
                or isinstance(timestamp_us, bool)
                or timestamp_us <= 0
            ):
                raise Px4ServiceError("MAVSDK raw GPS omitted source timestamp_us")
            latitude = _finite_attr(sample, "latitude_deg")
            longitude = _finite_attr(sample, "longitude_deg")
            altitude = _finite_attr(sample, "absolute_altitude_m")
            if not -90.0 <= latitude <= 90.0:
                raise Px4ServiceError("MAVSDK raw GPS latitude is outside [-90, 90]")
            if not -180.0 <= longitude <= 180.0:
                raise Px4ServiceError(
                    "MAVSDK raw GPS longitude is outside [-180, 180]"
                )
            optional_fields = {
                "hdop": ("hdop", 0.0, None),
                "vdop": ("vdop", 0.0, None),
                "velocity_m_s": ("velocity_m_s", 0.0, None),
                "course_over_ground_deg": ("cog_deg", 0.0, 360.0),
                "altitude_ellipsoid_m": ("altitude_ellipsoid_m", None, None),
                "horizontal_uncertainty_m": (
                    "horizontal_uncertainty_m",
                    0.0,
                    None,
                ),
                "vertical_uncertainty_m": (
                    "vertical_uncertainty_m",
                    0.0,
                    None,
                ),
                "velocity_uncertainty_m_s": (
                    "velocity_uncertainty_m_s",
                    0.0,
                    None,
                ),
                "heading_uncertainty_deg": (
                    "heading_uncertainty_deg",
                    0.0,
                    360.0,
                ),
                "yaw_deg": ("yaw_deg", 0.0, 360.0),
            }
            decoded_optional: dict[str, float | None] = {}
            for output_name, (
                attribute_name,
                minimum,
                maximum,
            ) in optional_fields.items():
                value = getattr(sample, attribute_name, None)
                numeric = (
                    float(value)
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    else None
                )
                if numeric is not None and (
                    (minimum is not None and numeric < minimum)
                    or (maximum is not None and numeric > maximum)
                ):
                    numeric = None
                decoded_optional[output_name] = numeric
            return {
                "timestamp_us": timestamp_us,
                "latitude_deg": latitude,
                "longitude_deg": longitude,
                "absolute_altitude_m": altitude,
                **decoded_optional,
            }
        raise Px4ServiceError(f"unsupported MAVSDK telemetry field {field}")

    def _public_cache_sample(
        self, vehicle_id: str, *, required: frozenset[str]
    ) -> tuple[dict[str, object], int, int]:
        config = self._require_config()
        if vehicle_id not in {
            str(vehicle["vehicle_id"]) for vehicle in config.vehicles
        }:
            raise Px4ServiceError(
                f"flight observation names undeclared vehicle {vehicle_id}"
            )
        cached = self._telemetry_cache.get(vehicle_id, {})
        receipts = self._telemetry_received_monotonic_ns.get(vehicle_id, {})
        missing = (required - cached.keys()) | (required - receipts.keys())
        if missing:
            raise Px4ServiceError(
                "flight sensor observation is not ready: missing "
                f"{sorted(missing)}"
            )
        observed_at = time.monotonic_ns()
        sample_received = min(receipts[field] for field in required)
        if sample_received > observed_at:
            raise Px4ServiceError("flight sensor sample receipt time is invalid")
        return cached, sample_received, observed_at

    def flight_telemetry_observation(
        self, vehicle_id: str, *, simulation_time_ns: int
    ) -> Mapping[str, object]:
        required = frozenset(
            {
                "global_position",
                "velocity",
                "attitude_euler",
                "flight_mode",
                "armed",
                "in_air",
                "landed_state",
                "battery_percent",
                "health",
            }
        )
        cached, sample_received, observed_at = self._public_cache_sample(
            vehicle_id, required=required
        )
        position = cached["global_position"]
        velocity = cached["velocity"]
        attitude = cached["attitude_euler"]
        if not all(
            isinstance(value, dict) for value in (position, velocity, attitude)
        ):
            raise Px4ServiceError("MAVSDK public telemetry cache is invalid")
        landed_state = _flight_mode_scalar(
            cached["landed_state"], label="MAVSDK public landed_state"
        )
        return {
            "schema_version": FLIGHT_TELEMETRY_OBSERVATION_SCHEMA,
            "vehicle_id": vehicle_id,
            "observation_status": "available",
            "simulation_time_ns": simulation_time_ns,
            "source_timestamp_status": "missing",
            "source_timestamp_us": None,
            "source_to_sim_time_mapping": "unavailable",
            "freshness_basis": "latest_sample_received_before_barrier",
            "sample_received_monotonic_ns": sample_received,
            "observation_monotonic_ns": observed_at,
            "receipt_age_ms": (observed_at - sample_received) / 1_000_000.0,
            "position_source": "mavsdk.telemetry.position",
            "longitude_deg": position["longitude_deg"],
            "latitude_deg": position["latitude_deg"],
            "absolute_altitude_m": position["altitude_m"],
            "absolute_altitude_reference": "AMSL",
            "relative_altitude_m": position["relative_altitude_m"],
            "velocity_source": "mavsdk.telemetry.position_velocity_ned",
            "north_m_s": velocity["north_m_s"],
            "east_m_s": velocity["east_m_s"],
            "down_m_s": velocity["down_m_s"],
            "attitude_source": "mavsdk.telemetry.attitude_euler",
            "roll_deg": attitude["roll_deg"],
            "pitch_deg": attitude["pitch_deg"],
            "yaw_deg": attitude["yaw_deg"],
            "flight_mode": cached["flight_mode"],
            "armed": cached["armed"],
            "in_air": cached["in_air"],
            "landed": landed_state == "ON_GROUND",
            "landed_state": landed_state,
            "battery_percent": cached["battery_percent"],
            "health_source": "mavsdk.telemetry.health",
            "health_json": cached["health"],
        }

    def flight_gnss_observation(
        self, vehicle_id: str, *, simulation_time_ns: int
    ) -> Mapping[str, object]:
        cached, sample_received, observed_at = self._public_cache_sample(
            vehicle_id, required=frozenset({"gps_info", "raw_gps"})
        )
        gps_info = cached["gps_info"]
        raw_gps = cached["raw_gps"]
        if not isinstance(gps_info, dict) or not isinstance(raw_gps, dict):
            raise Px4ServiceError("MAVSDK public GNSS cache is invalid")
        fix_type = _flight_mode_scalar(
            gps_info.get("fix_type"), label="MAVSDK public GNSS fix_type"
        )
        payload: dict[str, object] = {
            "schema_version": FLIGHT_GNSS_OBSERVATION_SCHEMA,
            "vehicle_id": vehicle_id,
            "observation_status": "available",
            "simulation_time_ns": simulation_time_ns,
            "source": "mavsdk.telemetry.raw_gps+gps_info",
            "source_timestamp_stream": "raw_gps",
            "source_timestamp_us": raw_gps["timestamp_us"],
            "source_time_basis": "unix_epoch_or_autopilot_boot_unspecified",
            "gps_info_timestamp_status": "missing",
            "gps_info_timestamp_us": None,
            "source_to_sim_time_mapping": "unavailable",
            "freshness_basis": "latest_sample_received_before_barrier",
            "sample_received_monotonic_ns": sample_received,
            "observation_monotonic_ns": observed_at,
            "receipt_age_ms": (observed_at - sample_received) / 1_000_000.0,
            "position_status": (
                "invalid_fix" if fix_type in {"NO_GPS", "NO_FIX"} else "available"
            ),
            "latitude_deg": raw_gps["latitude_deg"],
            "longitude_deg": raw_gps["longitude_deg"],
            "absolute_altitude_m": raw_gps["absolute_altitude_m"],
            "absolute_altitude_reference": "AMSL",
            "fix_type": fix_type,
            "num_satellites": gps_info["num_satellites"],
        }
        optional_fields = (
            ("hdop", "hdop_status", "hdop"),
            ("vdop", "vdop_status", "vdop"),
            ("velocity_m_s", "velocity_status", "velocity_m_s"),
            (
                "course_over_ground_deg",
                "course_over_ground_status",
                "course_over_ground_deg",
            ),
            (
                "altitude_ellipsoid_m",
                "ellipsoid_altitude_status",
                "altitude_ellipsoid_m",
            ),
            (
                "horizontal_uncertainty_m",
                "horizontal_uncertainty_status",
                "horizontal_uncertainty_m",
            ),
            (
                "vertical_uncertainty_m",
                "vertical_uncertainty_status",
                "vertical_uncertainty_m",
            ),
            (
                "velocity_uncertainty_m_s",
                "velocity_uncertainty_status",
                "velocity_uncertainty_m_s",
            ),
            (
                "heading_uncertainty_deg",
                "heading_uncertainty_status",
                "heading_uncertainty_deg",
            ),
            ("yaw_deg", "yaw_status", "yaw_deg"),
        )
        for source_name, status_name, value_name in optional_fields:
            value = raw_gps.get(source_name)
            payload[status_name] = "available" if value is not None else "missing"
            payload[value_name] = value
        return payload

    async def _wait_for_telemetry_cache(self, config: PreparedConfig) -> None:
        required = {
            "global_position",
            "velocity",
            "angular_velocity",
            "flight_mode",
            "armed",
            "in_air",
            "landed_state",
            "battery_percent",
            "health",
        }
        deadline = asyncio.get_running_loop().time() + min(
            3.0, config.command_timeout_ms / 1000
        )
        while asyncio.get_running_loop().time() < deadline:
            if self._telemetry_failure is not None:
                failure = self._telemetry_failure
                self._telemetry_failure = None
                raise failure
            if all(
                required.issubset(
                    self._telemetry_cache.get(str(vehicle["vehicle_id"]), {})
                )
                for vehicle in config.vehicles
            ):
                return
            await asyncio.sleep(0.05)
        missing = {
            str(vehicle["vehicle_id"]): sorted(
                required
                - self._telemetry_cache.get(str(vehicle["vehicle_id"]), {}).keys()
            )
            for vehicle in config.vehicles
        }
        raise Px4ServiceError(f"MAVSDK telemetry did not warm up: {missing}")

    async def _wait_connected(self, system: Any) -> None:
        async for state in system.core.connection_state():
            if state.is_connected:
                return
        raise Px4ServiceError("MAVSDK connection stream ended before connect")

    async def _issue_mavsdk(self, session: Any, request: Mapping[str, Any]) -> None:
        arguments = _command_argument_map(request)
        action = session.action
        tool_id = request.get("tool_id")
        if tool_id == ARM_TOOL:
            await action.arm()
        elif tool_id == DISARM_TOOL:
            await action.disarm()
        elif tool_id == LAND_TOOL:
            await action.land()
        elif tool_id == HOLD_TOOL:
            await action.hold()
        elif tool_id == TAKEOFF_TOOL:
            await action.set_takeoff_altitude(
                _finite_number(arguments["altitude_m"], label="altitude_m")
            )
            await action.takeoff()
        elif tool_id == GOTO_TOOL:
            formal_yaw_deg = _finite_number(arguments["yaw_deg"], label="yaw_deg")
            await action.goto_location(
                _finite_number(arguments["latitude_deg"], label="latitude_deg"),
                _finite_number(arguments["longitude_deg"], label="longitude_deg"),
                _finite_number(arguments["altitude_amsl_m"], label="altitude_amsl_m"),
                _mavsdk_heading_from_enu_yaw(formal_yaw_deg),
            )
        else:
            raise Px4CommandPhaseError(
                last_success="accepted",
                detail=f"unsupported PX4/MAVSDK tool {tool_id!r}",
            )

    async def _read_velocity(self, session: Any) -> dict[str, float]:
        sample = await self._first(session.telemetry.position_velocity_ned())
        velocity = getattr(sample, "velocity", None)
        if velocity is None:
            raise Px4ServiceError("MAVSDK omitted velocity telemetry")
        return {
            "north_m_s": _finite_attr(velocity, "north_m_s"),
            "east_m_s": _finite_attr(velocity, "east_m_s"),
            "down_m_s": _finite_attr(velocity, "down_m_s"),
        }

    async def _read_flight_mode(self, session: Any) -> str:
        sample = await self._first(session.telemetry.flight_mode())
        return _flight_mode_scalar(
            getattr(sample, "name", None),
            label="MAVSDK flight_mode telemetry",
        )

    async def _read_armed(self, session: Any) -> bool:
        sample = await self._first(session.telemetry.armed())
        if not isinstance(sample, bool):
            raise Px4ServiceError("MAVSDK armed telemetry is not a boolean")
        return sample

    async def _read_battery_percent(self, session: Any) -> float:
        sample = await self._first(session.telemetry.battery())
        remaining = getattr(sample, "remaining_percent", None)
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            raise Px4ServiceError("MAVSDK omitted battery remaining_percent")
        percent = float(remaining)
        if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
            raise Px4ServiceError("MAVSDK battery remaining_percent is outside 0..100")
        return percent

    async def _read_health(self, session: Any) -> str:
        sample = await self._first(session.telemetry.health())
        flags = {
            name: getattr(sample, name)
            for name in dir(sample)
            if name.startswith("is_") and isinstance(getattr(sample, name), bool)
        }
        if not flags:
            raise Px4ServiceError("MAVSDK omitted health flags")
        return _canonical_json(flags).decode("utf-8")

    async def _first(self, stream: Any) -> Any:
        timeout = self._stream_timeout_s
        if timeout is None:
            timeout = self._require_config().command_timeout_ms / 1000
        try:
            return await asyncio.wait_for(stream.__anext__(), timeout=timeout)
        except Exception as exc:
            raise Px4ServiceError(
                "MAVSDK telemetry stream produced no sample: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    async def _model_poses(self) -> dict[str, dict[str, float]]:
        with self._pose_topic_lock:
            failure = self._pose_topic_failure
            poses = self._pose_topic_latest
            subscribed = self._pose_topic_subscribed
            if poses is not None:
                poses = {name: dict(pose) for name, pose in poses.items()}
        if failure is not None:
            raise Px4ServiceError(
                "Gazebo Pose_V subscriber failed while reading dynamic_pose/info: "
                f"{failure}"
            ) from failure
        if not subscribed:
            raise Px4ServiceError(
                "Gazebo Pose_V subscriber is not active for dynamic_pose/info"
            )
        return poses or {}

    def _pose_topic_version_snapshot(self) -> int:
        with self._pose_topic_lock:
            return self._pose_topic_version

    async def _wait_for_pose_update(
        self,
        minimum_version: int,
        *,
        expected_models: frozenset[str],
        timeout_s: float,
        minimum_received_at: float = 0.0,
    ) -> dict[str, dict[str, float]]:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise Px4ServiceError("Gazebo pose subscriber timeout must be positive")
        if not math.isfinite(minimum_received_at) or minimum_received_at < 0:
            raise Px4ServiceError("Gazebo pose subscriber receipt time is invalid")
        deadline = asyncio.get_running_loop().time() + timeout_s
        fresh_seen = False
        stable_since: float | None = None
        stable_digest: bytes | None = None
        last_version = minimum_version
        processed_version = minimum_version
        observed: dict[str, dict[str, float]] = {}
        while True:
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                raise Px4ServiceError(
                    "Gazebo Pose_V subscriber did not provide a complete fresh "
                    "dynamic_pose/info message after the exact stats barrier: "
                    f"minimum_version={minimum_version}, last_version={last_version}, "
                    f"observed_models={sorted(observed)}"
                )
            with self._pose_topic_lock:
                failure = self._pose_topic_failure
                version = self._pose_topic_version
                poses = self._pose_topic_latest
                received_at = self._pose_topic_latest_received_at
            if failure is not None:
                raise Px4ServiceError(
                    "Gazebo Pose_V subscriber failed while waiting for a fresh "
                    f"dynamic_pose/info message: {failure}"
                ) from failure
            if (
                version > minimum_version
                and received_at is not None
                and received_at >= minimum_received_at
            ):
                if version != processed_version:
                    processed_version = version
                    if poses is not None:
                        observed = {
                            name: dict(pose) for name, pose in poses.items()
                        }
                    if not expected_models.issubset(observed):
                        stable_since = None
                        stable_digest = None
                        last_version = version
                    else:
                        digest = _canonical_json(observed)
                        if not fresh_seen:
                            fresh_seen = True
                            stable_since = now
                            stable_digest = digest
                            last_version = version
                        elif digest != stable_digest:
                            last_version = version
                            stable_since = now
                            stable_digest = digest
                elif (
                    fresh_seen
                    and stable_since is not None
                    and now - stable_since >= POSE_TOPIC_QUIESCENCE_S
                ):
                    return observed
            await asyncio.sleep(min(POSE_TOPIC_POLL_S, deadline - now))

    def _on_pose_topic_message(self, message: Any) -> None:
        with self._pose_topic_lock:
            if not self._pose_topic_active:
                return
        try:
            model_names = frozenset(
                _string(
                    vehicle.get("gazebo_model_name"),
                    label="configured Gazebo model name",
                    pattern=IDENTIFIER,
                )
                for vehicle in self._require_config().vehicles
            )
            poses = self._parse_pose_message(message, model_names=model_names)
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, Px4ServiceError)
                else Px4ServiceError(
                    f"Gazebo Pose_V message could not be decoded: {exc}"
                )
            )
            with self._pose_topic_lock:
                if self._pose_topic_active and self._pose_topic_failure is None:
                    self._pose_topic_failure = failure
            return
        with self._pose_topic_lock:
            if not self._pose_topic_active:
                return
            self._pose_topic_latest = poses
            self._pose_topic_latest_received_at = time.monotonic()
            self._pose_topic_version += 1

    @staticmethod
    def _parse_pose_message(
        message: Any, *, model_names: frozenset[str]
    ) -> dict[str, dict[str, float]]:
        if not isinstance(model_names, frozenset) or not model_names:
            raise Px4ServiceError(
                "Gazebo Pose_V authoritative model_names must be a non-empty frozenset"
            )
        for name in model_names:
            _string(
                name,
                label="Gazebo Pose_V authoritative model name",
                pattern=IDENTIFIER,
            )
        entries = getattr(message, "pose", None)
        if entries is None:
            raise Px4ServiceError("Gazebo Pose_V message omitted pose entries")
        poses: dict[str, dict[str, float]] = {}
        for index, item in enumerate(entries):
            name = getattr(item, "name", None)
            if not isinstance(name, str) or not name:
                raise Px4ServiceError(
                    f"Gazebo Pose_V message omitted pose[{index}].name"
                )
            if name not in model_names:
                continue
            position = getattr(item, "position", None)
            orientation = getattr(item, "orientation", None)
            if position is None or orientation is None:
                raise Px4ServiceError(
                    f"Gazebo Pose_V message omitted pose[{index}] geometry"
                )
            pose = _pose_from_json(
                {
                    "position": {
                        "x": getattr(position, "x", None),
                        "y": getattr(position, "y", None),
                        "z": getattr(position, "z", None),
                    },
                    "orientation": {
                        "x": getattr(orientation, "x", None),
                        "y": getattr(orientation, "y", None),
                        "z": getattr(orientation, "z", None),
                        "w": getattr(orientation, "w", None),
                    },
                }
            )
            if name in poses:
                raise Px4ServiceError(
                    f"Gazebo Pose_V message repeated model name {name!r}"
                )
            poses[name] = pose
        return poses

    async def _contact_names(self) -> dict[str, tuple[str, ...]]:
        return dict(self._latest_contacts)

    def _parse_contact_names(
        self, payload: Mapping[str, Any]
    ) -> dict[str, tuple[str, ...]]:
        config = self._require_config()
        model_tokens = dict(config.contact_model_tokens)
        if len(model_tokens) != len(config.contact_model_tokens):
            raise Px4ServiceError("Gazebo contact model mapping repeats a model name")
        vehicle_tokens = {
            f"vehicle.{vehicle['vehicle_id']}": str(vehicle["vehicle_id"])
            for vehicle in config.vehicles
        }
        if not vehicle_tokens.values() or not set(vehicle_tokens).issubset(
            model_tokens.values()
        ):
            raise Px4ServiceError(
                "Gazebo contact model mapping omits a configured vehicle"
            )

        raw_contacts = payload.get("contact")
        if raw_contacts is None and {"collision1", "collision2"}.issubset(payload):
            raw_contacts = [payload]
        elif raw_contacts is None:
            raw_contacts = []
        elif isinstance(raw_contacts, dict):
            raw_contacts = [raw_contacts]
        if not isinstance(raw_contacts, list):
            raise Px4ServiceError("Gazebo contacts payload is not an array")

        def collision_name(value: object, *, label: str) -> str:
            collision = _strict_object(
                value,
                required=frozenset({"id", "name"}),
                optional=frozenset({"header", "type"}),
                label=label,
            )
            return _string(collision["name"], label=f"{label}.name")

        def classified_token(name: str) -> str | None:
            components = tuple(part for part in name.split("::") if part)
            if len(components) < 2:
                raise Px4ServiceError(
                    f"Gazebo collision name is not fully scoped: {name!r}"
                )
            matches = {
                model_tokens[component]
                for component in components
                if component in model_tokens
            }
            if len(matches) > 1:
                raise Px4ServiceError(
                    f"Gazebo collision scope has ambiguous model identity: {name!r}"
                )
            return next(iter(matches), None)

        contacts: dict[str, set[str]] = {
            vehicle_id: set() for vehicle_id in vehicle_tokens.values()
        }
        for index, raw_contact in enumerate(raw_contacts):
            contact = _strict_object(
                raw_contact,
                required=frozenset({"collision1", "collision2"}),
                optional=frozenset(
                    {
                        "depth",
                        "header",
                        "normal",
                        "position",
                        "wrench",
                        "world",
                    }
                ),
                label=f"Gazebo contacts[{index}]",
            )
            first_name = collision_name(
                contact["collision1"], label=f"Gazebo contacts[{index}].collision1"
            )
            second_name = collision_name(
                contact["collision2"], label=f"Gazebo contacts[{index}].collision2"
            )
            first_token = classified_token(first_name)
            second_token = classified_token(second_name)
            first_vehicle = vehicle_tokens.get(first_token or "")
            second_vehicle = vehicle_tokens.get(second_token or "")
            if first_vehicle is None and second_vehicle is None:
                continue
            if first_vehicle is not None:
                if second_token is None:
                    raise Px4ServiceError(
                        f"Gazebo contact counterpart is unclassified: {second_name!r}"
                    )
                if second_token != first_token:
                    contacts[first_vehicle].add(second_token)
            if second_vehicle is not None:
                if first_token is None:
                    raise Px4ServiceError(
                        f"Gazebo contact counterpart is unclassified: {first_name!r}"
                    )
                if first_token != second_token:
                    contacts[second_vehicle].add(first_token)
        return {
            vehicle_id: tuple(sorted(counterparties))
            for vehicle_id, counterparties in sorted(contacts.items())
        }

    async def _wait_for_tcp(self, port: int, *, timeout_s: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            for process in self._processes:
                if process.returncode is not None:
                    raise Px4ServiceError(
                        "PX4 stack process exited before "
                        f"127.0.0.1:{port} opened (status={process.returncode})"
                    )
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=0.2
                )
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
                continue
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return
        raise Px4ServiceError(f"PX4 stack did not open 127.0.0.1:{port}")

    async def _wait_until_sim_time(
        self, target_ns: int, *, minimum_version: int | None = None
    ) -> None:
        timeout_s = self._require_config().command_timeout_ms / 1000
        deadline = asyncio.get_running_loop().time() + timeout_s
        baseline = (
            self._stats_topic_version_snapshot()
            if minimum_version is None
            else minimum_version
        )
        raw_target_ns = self._time_origin_ns + target_ns
        while True:
            now = asyncio.get_running_loop().time()
            remaining = deadline - now
            if remaining <= 0:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber did not prove the exact "
                    f"sim_time_ns {target_ns} after version {baseline}"
                )
            with self._stats_topic_lock:
                failure = self._stats_topic_failure
                version = self._stats_topic_version
                latest = self._stats_topic_latest
                subscribed = self._stats_topic_subscribed
            if failure is not None:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber failed while waiting for "
                    f"sim_time_ns {target_ns}: {failure}"
                ) from failure
            if not subscribed:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber is not active"
                )
            if version > baseline and latest is not None:
                sim_time_ns, _paused = latest
                if sim_time_ns > raw_target_ns:
                    reached_ns = sim_time_ns - self._time_origin_ns
                    raise Px4ServiceError(
                        "Gazebo WorldStatistics overshot the requested sim_time_ns: "
                        f"observed={reached_ns}, target={target_ns}"
                    )
                if sim_time_ns == raw_target_ns:
                    return
            await asyncio.sleep(min(POSE_TOPIC_POLL_S, remaining))

    async def _world_control_target_reached(
        self, *, expected_ns: int, minimum_version: int, timeout_s: float
    ) -> bool:
        """Reconcile a lost WorldControl reply without issuing a duplicate step."""

        if expected_ns < 0 or minimum_version < 0:
            raise Px4ServiceError("WorldControl reconciliation arguments are invalid")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise Px4ServiceError("WorldControl reconciliation timeout must be positive")
        deadline = asyncio.get_running_loop().time() + timeout_s
        raw_expected_ns = self._time_origin_ns + expected_ns
        while True:
            now = asyncio.get_running_loop().time()
            remaining = deadline - now
            if remaining <= 0:
                return False
            with self._stats_topic_lock:
                failure = self._stats_topic_failure
                version = self._stats_topic_version
                latest = self._stats_topic_latest
                subscribed = self._stats_topic_subscribed
            if failure is not None:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber failed during WorldControl reconciliation"
                ) from failure
            if not subscribed:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber is not active during WorldControl reconciliation"
                )
            if version > minimum_version and latest is not None:
                sim_time_ns, _paused = latest
                if sim_time_ns > raw_expected_ns:
                    raise Px4ServiceError(
                        "Gazebo WorldControl reconciliation observed an overshoot: "
                        f"observed={sim_time_ns - self._time_origin_ns}, "
                        f"expected={expected_ns}"
                    )
                if sim_time_ns == raw_expected_ns:
                    return True
            await asyncio.sleep(min(POSE_TOPIC_POLL_S, remaining))


    async def _wait_for_log(
        self, log_name: str, text: str, *, timeout_s: float
    ) -> None:
        path = self._log_paths.get(log_name)
        if path is None:
            raise Px4ServiceError(f"PX4 log is missing for {log_name}")
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if path.exists() and text in path.read_text(errors="replace"):
                return
            await asyncio.sleep(0.2)
        tail = path.read_text(errors="replace")[-2000:] if path.exists() else ""
        raise Px4ServiceError(f"timed out waiting for {text!r} in {log_name}: {tail}")

    async def _wait_for_vehicle_poses(self, config: PreparedConfig) -> None:
        expected = {str(vehicle["gazebo_model_name"]) for vehicle in config.vehicles}
        current_version = self._pose_topic_version_snapshot()
        current = await self._model_poses()
        if expected.issubset(current):
            self._latest_poses = current
            return
        self._latest_poses = await self._wait_for_pose_update(
            current_version,
            expected_models=frozenset(expected),
            timeout_s=config.command_timeout_ms / 1000,
        )

    async def _wait_for_world(self, config: PreparedConfig) -> None:
        deadline = asyncio.get_running_loop().time() + (
            config.command_timeout_ms / 1000
        )
        service = "/world/" + config.world_name + "/control"
        while asyncio.get_running_loop().time() < deadline:
            for process in self._processes:
                if process.returncode is not None:
                    raise Px4ServiceError(
                        f"Gazebo exited before the world was ready (status={process.returncode})"
                    )
            try:
                listing = await self._gz_cli(
                    ("service", "-l"),
                    config,
                    timeout_s=5.0,
                )
            except Px4ServiceError:
                await asyncio.sleep(0.2)
                continue
            names = {line.strip() for line in listing.splitlines() if line.strip()}
            if service in names:
                return
            await asyncio.sleep(0.2)
        raise Px4ServiceError("Gazebo world control service did not become ready")

    async def _assert_world_paused(self) -> None:
        if not await self._world_is_paused():
            raise Px4ServiceError("Gazebo world did not start paused")

    async def _world_is_paused(self) -> bool:
        _sim_time_ns, paused = await self._wait_for_stats_latest(
            timeout_s=self._require_config().command_timeout_ms / 1000
        )
        return paused

    async def _wait_for_world_paused(
        self,
        *,
        expected_sim_time_ns: int,
        minimum_version: int,
        timeout_s: float,
    ) -> None:
        """Prove the final exact barrier sample is paused.

        Gazebo's WorldControl reply and its WorldStatistics publication are
        independent transport paths.  A final sample can therefore carry the
        requested time while still reporting ``paused=false`` for a short
        interval.  Accept only a fresh sample at the exact target time with
        ``paused=true``; a later time remains an overshoot and is fatal.
        """

        if expected_sim_time_ns < 0 or minimum_version < 0:
            raise Px4ServiceError("Gazebo paused-state barrier arguments are invalid")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise Px4ServiceError("Gazebo paused-state timeout must be positive")
        deadline = asyncio.get_running_loop().time() + timeout_s
        raw_target_ns = self._time_origin_ns + expected_sim_time_ns
        last_observed: tuple[int, bool] | None = None
        while True:
            now = asyncio.get_running_loop().time()
            remaining = deadline - now
            if remaining <= 0:
                detail = (
                    "none"
                    if last_observed is None
                    else f"sim_time_ns={last_observed[0]}, paused={last_observed[1]}"
                )
                raise Px4ServiceError(
                    "Gazebo did not confirm paused state at the exact barrier: "
                    f"target_sim_time_ns={expected_sim_time_ns}, last={detail}"
                )
            with self._stats_topic_lock:
                failure = self._stats_topic_failure
                version = self._stats_topic_version
                latest = self._stats_topic_latest
                subscribed = self._stats_topic_subscribed
            if failure is not None:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber failed while confirming "
                    f"paused state: {failure}"
                ) from failure
            if not subscribed:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber is not active while confirming paused state"
                )
            if version > minimum_version and latest is not None:
                sim_time_ns, paused = latest
                last_observed = latest
                if sim_time_ns > raw_target_ns:
                    reached_ns = sim_time_ns - self._time_origin_ns
                    raise Px4ServiceError(
                        "Gazebo WorldStatistics overshot before paused-state "
                        f"confirmation: observed={reached_ns}, target={expected_sim_time_ns}"
                    )
                if sim_time_ns == raw_target_ns and paused:
                    return
            await asyncio.sleep(min(POSE_TOPIC_POLL_S, remaining))

    def _record_gz_diagnostic(
        self, kind: str, payload: Mapping[str, object]
    ) -> None:
        log_dir = self._tmp_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {"kind": kind, **dict(payload)}
        with (log_dir / "gz-command-diagnostics.jsonl").open("ab") as output:
            output.write(_canonical_json(record) + b"\n")

    def _stats_topic(self, config: PreparedConfig) -> str:
        return "/world/" + config.world_name + "/stats"

    def _stats_topic_version_snapshot(self) -> int:
        with self._stats_topic_lock:
            return self._stats_topic_version

    async def _wait_for_stats_latest(
        self, *, timeout_s: float
    ) -> tuple[int, bool]:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise Px4ServiceError("Gazebo WorldStatistics timeout must be positive")
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber did not provide a usable "
                    "message before timeout"
                )
            with self._stats_topic_lock:
                failure = self._stats_topic_failure
                latest = self._stats_topic_latest
                subscribed = self._stats_topic_subscribed
            if failure is not None:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber failed: " f"{failure}"
                ) from failure
            if not subscribed:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber is not active"
                )
            if latest is not None:
                return latest
            await asyncio.sleep(min(POSE_TOPIC_POLL_S, deadline - now))

    @staticmethod
    def _parse_stats_message(message: Any) -> tuple[int, bool]:
        sim_time = getattr(message, "sim_time", None)
        if sim_time is None:
            raise Px4ServiceError("Gazebo WorldStatistics omitted sim_time")
        sec = _proto3_int(getattr(sim_time, "sec", None), label="simTime.sec")
        nsec = _proto3_int(getattr(sim_time, "nsec", None), label="simTime.nsec")
        if nsec < 0 or nsec >= 1_000_000_000:
            raise Px4ServiceError("Gazebo WorldStatistics nsec is outside 0..999999999")
        paused = getattr(message, "paused", None)
        if not isinstance(paused, bool):
            raise Px4ServiceError("Gazebo WorldStatistics omitted paused state")
        return sec * 1_000_000_000 + nsec, paused

    def _on_stats_topic_message(self, message: Any) -> None:
        with self._stats_topic_lock:
            if not self._stats_topic_active:
                return
        try:
            sim_time_ns, paused = self._parse_stats_message(message)
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, Px4ServiceError)
                else Px4ServiceError(
                    f"Gazebo WorldStatistics message could not be decoded: {exc}"
                )
            )
            with self._stats_topic_lock:
                if self._stats_topic_active and self._stats_topic_failure is None:
                    self._stats_topic_failure = failure
            return
        with self._stats_topic_lock:
            if not self._stats_topic_active:
                return
            previous = self._stats_topic_latest
            if previous is not None and sim_time_ns < previous[0]:
                self._stats_topic_failure = Px4ServiceError(
                    "Gazebo WorldStatistics simulation time regressed: "
                    f"previous={previous[0]}, observed={sim_time_ns}"
                )
                return
            self._stats_topic_version += 1
            self._stats_topic_latest = (sim_time_ns, paused)

    async def _start_stats_subscriber(self, config: PreparedConfig) -> None:
        with self._stats_topic_lock:
            if self._stats_topic_active or self._stats_topic_subscribed:
                raise Px4ServiceError(
                    "Gazebo WorldStatistics subscriber was initialized more than once"
                )
            self._stats_topic_name = self._stats_topic(config)
            self._stats_topic_active = True
            self._stats_topic_subscribed = False
            self._stats_topic_version = 0
            self._stats_topic_latest = None
            self._stats_topic_failure = None
        try:
            node, _world_control_type, _boolean_type = self._require_gazebo_transport(
                config
            )
            if "/usr/lib/python3/dist-packages" not in sys.path:
                sys.path.append("/usr/lib/python3/dist-packages")
            from gz.msgs10.world_stats_pb2 import WorldStatistics
        except (ImportError, OSError) as exc:
            with self._stats_topic_lock:
                self._stats_topic_active = False
                self._stats_topic_name = None
            raise Px4ServiceError(
                "Gazebo Python WorldStatistics message binding is unavailable"
            ) from exc

        callback = self._on_stats_topic_message
        self._stats_topic_callback = callback
        try:
            async with self._gazebo_transport_lock:
                subscribed = await asyncio.to_thread(
                    node.subscribe,
                    WorldStatistics,
                    self._stats_topic(config),
                    callback,
                )
        except asyncio.CancelledError:
            with self._stats_topic_lock:
                self._stats_topic_active = False
                self._stats_topic_name = None
                self._stats_topic_callback = None
            raise
        except Exception as exc:
            with self._stats_topic_lock:
                self._stats_topic_active = False
                self._stats_topic_name = None
                self._stats_topic_callback = None
            raise Px4ServiceError(
                "Gazebo WorldStatistics transport subscription failed"
            ) from exc
        if subscribed is not True:
            with self._stats_topic_lock:
                self._stats_topic_active = False
                self._stats_topic_name = None
                self._stats_topic_callback = None
            raise Px4ServiceError(
                "Gazebo WorldStatistics transport subscription was not accepted"
            )
        with self._stats_topic_lock:
            self._stats_topic_subscribed = True

    async def _stop_stats_subscriber(self) -> None:
        with self._stats_topic_lock:
            node = self._gazebo_transport_node
            topic = self._stats_topic_name
            subscribed = self._stats_topic_subscribed
            self._stats_topic_active = False
            self._stats_topic_subscribed = False
            self._stats_topic_name = None
            self._stats_topic_callback = None
            self._stats_topic_version = 0
            self._stats_topic_latest = None
            self._stats_topic_failure = None
        if node is None or topic is None or not subscribed:
            return
        try:
            async with self._gazebo_transport_lock:
                result = await asyncio.to_thread(node.unsubscribe, topic)
            if result is not True:
                self._record_gz_diagnostic(
                    "stats-topic-unsubscribe-failed",
                    {"topic": topic, "result": repr(result)},
                )
        except Exception as exc:
            self._record_gz_diagnostic(
                "stats-topic-unsubscribe-error",
                {
                    "topic": topic,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    def _pose_topic(self, config: PreparedConfig) -> str:
        return "/world/" + config.world_name + "/dynamic_pose/info"

    async def _start_pose_subscriber(self, config: PreparedConfig) -> None:
        with self._pose_topic_lock:
            if self._pose_topic_active or self._pose_topic_subscribed:
                raise Px4ServiceError(
                    "Gazebo Pose_V subscriber was initialized more than once"
                )
            self._pose_topic_name = self._pose_topic(config)
            self._pose_topic_active = True
            self._pose_topic_subscribed = False
            self._pose_topic_version = 0
            self._pose_topic_latest = None
            self._pose_topic_latest_received_at = None
            self._pose_topic_failure = None
        try:
            node, _world_control_type, _boolean_type = self._require_gazebo_transport(
                config
            )
            if "/usr/lib/python3/dist-packages" not in sys.path:
                sys.path.append("/usr/lib/python3/dist-packages")
            from gz.msgs10.pose_v_pb2 import Pose_V
        except (ImportError, OSError) as exc:
            with self._pose_topic_lock:
                self._pose_topic_active = False
                self._pose_topic_name = None
            raise Px4ServiceError(
                "Gazebo Python Pose_V message binding is unavailable"
            ) from exc

        callback = self._on_pose_topic_message
        self._pose_topic_callback = callback
        try:
            async with self._gazebo_transport_lock:
                subscribed = await asyncio.to_thread(
                    node.subscribe,
                    Pose_V,
                    self._pose_topic(config),
                    callback,
                )
        except asyncio.CancelledError:
            with self._pose_topic_lock:
                self._pose_topic_active = False
                self._pose_topic_name = None
                self._pose_topic_callback = None
            raise
        except Exception as exc:
            with self._pose_topic_lock:
                self._pose_topic_active = False
                self._pose_topic_name = None
                self._pose_topic_callback = None
            raise Px4ServiceError(
                "Gazebo Pose_V transport subscription failed"
            ) from exc
        if subscribed is not True:
            with self._pose_topic_lock:
                self._pose_topic_active = False
                self._pose_topic_name = None
                self._pose_topic_callback = None
            raise Px4ServiceError(
                "Gazebo Pose_V transport subscription was not accepted"
            )
        with self._pose_topic_lock:
            self._pose_topic_subscribed = True

    async def _stop_pose_subscriber(self) -> None:
        with self._pose_topic_lock:
            node = self._gazebo_transport_node
            topic = self._pose_topic_name
            subscribed = self._pose_topic_subscribed
            self._pose_topic_active = False
            self._pose_topic_subscribed = False
            self._pose_topic_name = None
            self._pose_topic_callback = None
            self._pose_topic_version = 0
            self._pose_topic_latest = None
            self._pose_topic_latest_received_at = None
            self._pose_topic_failure = None
        if node is None or topic is None or not subscribed:
            return
        try:
            async with self._gazebo_transport_lock:
                result = await asyncio.to_thread(node.unsubscribe, topic)
            if result is not True:
                self._record_gz_diagnostic(
                    "pose-topic-unsubscribe-failed",
                    {"topic": topic, "result": repr(result)},
                )
        except Exception as exc:
            self._record_gz_diagnostic(
                "pose-topic-unsubscribe-error",
                {
                    "topic": topic,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    async def _start_airspace_subscriber(self, config: PreparedConfig) -> None:
        transition = config.airspace_transition
        if transition is None:
            return
        with self._airspace_lock:
            if self._airspace_topic_active or self._airspace_topic_subscribed:
                raise Px4ServiceError(
                    "Gazebo airspace subscriber was initialized more than once"
                )
            self._airspace_topic_name = str(transition["transition_topic"])
            self._airspace_topic_active = True
            self._airspace_topic_subscribed = False
            self._airspace_samples.clear()
            self._airspace_events.clear()
            self._airspace_emitted_sequences.clear()
            self._airspace_failure = None
        try:
            node, _world_control_type, _boolean_type = self._require_gazebo_transport(
                config
            )
            if "/usr/lib/python3/dist-packages" not in sys.path:
                sys.path.append("/usr/lib/python3/dist-packages")
            from gz.msgs10.stringmsg_pb2 import StringMsg
        except (ImportError, OSError) as exc:
            with self._airspace_lock:
                self._airspace_topic_active = False
                self._airspace_topic_name = None
            raise Px4ServiceError(
                "Gazebo Python StringMsg binding is unavailable for airspace transitions"
            ) from exc
        callback = self._on_airspace_topic_message
        self._airspace_topic_callback = callback
        try:
            async with self._gazebo_transport_lock:
                subscribed = await asyncio.to_thread(
                    node.subscribe,
                    StringMsg,
                    str(transition["transition_topic"]),
                    callback,
                )
        except asyncio.CancelledError:
            with self._airspace_lock:
                self._airspace_topic_active = False
                self._airspace_topic_name = None
                self._airspace_topic_callback = None
            raise
        except Exception as exc:
            with self._airspace_lock:
                self._airspace_topic_active = False
                self._airspace_topic_name = None
                self._airspace_topic_callback = None
            raise Px4ServiceError(
                "Gazebo airspace transition transport subscription failed"
            ) from exc
        if subscribed is not True:
            with self._airspace_lock:
                self._airspace_topic_active = False
                self._airspace_topic_name = None
                self._airspace_topic_callback = None
            raise Px4ServiceError(
                "Gazebo airspace transition subscription was not accepted"
            )
        with self._airspace_lock:
            self._airspace_topic_subscribed = True

    def _on_airspace_topic_message(self, message: Any) -> None:
        with self._airspace_lock:
            if not self._airspace_topic_active:
                return
        try:
            encoded = getattr(message, "data", None)
            if not isinstance(encoded, str) or not encoded:
                raise Px4ServiceError("Gazebo airspace StringMsg has no JSON data")
            raw = json.loads(encoded)
            if not isinstance(raw, dict):
                raise Px4ServiceError("Gazebo airspace message is not a JSON object")
            required = {
                "schema_version", "source", "sequence", "transition", "airspace_state",
                "sim_time_ns", "world_id", "world_digest", "region_id", "region_digest",
                "incident_vehicle", "position_enu_m",
            }
            if set(raw) != required:
                raise Px4ServiceError("Gazebo airspace message fields are not exact")
            if raw["schema_version"] != "aero-bench.gazebo-airspace-transition/v1":
                raise Px4ServiceError("Gazebo airspace message schema is invalid")
            if raw["source"] != "gazebo.system":
                raise Px4ServiceError("Gazebo airspace message source is not the system plugin")
            sequence = _bounded_integer(raw["sequence"], label="airspace.sequence", minimum=0)
            sim_time_ns = _integer(raw["sim_time_ns"], label="airspace.sim_time_ns", minimum=0)
            if raw["transition"] not in {"entered", "exited", "none"}:
                raise Px4ServiceError("Gazebo airspace transition kind is invalid")
            if raw["airspace_state"] not in {"inside", "outside"}:
                raise Px4ServiceError("Gazebo airspace state is invalid")
            world_id = _string(raw["world_id"], label="airspace.world_id", pattern=IDENTIFIER)
            world_digest = _sha256(raw["world_digest"], label="airspace.world_digest")
            region_id = _string(raw["region_id"], label="airspace.region_id", pattern=IDENTIFIER)
            region_digest = _sha256(raw["region_digest"], label="airspace.region_digest")
            incident_vehicle = _string(
                raw["incident_vehicle"], label="airspace.incident_vehicle", pattern=IDENTIFIER
            )
            position = _strict_object(
                raw["position_enu_m"],
                required=frozenset({"east", "north", "up"}),
                label="airspace.position_enu_m",
            )
            position = {
                key: _finite_number(position[key], label=f"airspace.position_enu_m.{key}")
                for key in ("east", "north", "up")
            }
            sample = {
                "source": raw["source"],
                "sequence": sequence,
                "transition": raw["transition"],
                "airspace_state": raw["airspace_state"],
                "sim_time_ns": sim_time_ns,
                "world_id": world_id,
                "world_digest": world_digest,
                "region_id": region_id,
                "region_digest": region_digest,
                "incident_vehicle": incident_vehicle,
                "position_enu_m": position,
            }
            config = self._config
            if config is None or config.airspace_transition is None:
                raise Px4ServiceError("airspace message arrived without configured transition")
            transition_config = config.airspace_transition
            if any(
                sample[key] != transition_config[key]
                for key in ("world_id", "world_digest", "region_id", "region_digest")
            ):
                raise Px4ServiceError("Gazebo airspace message identity differs from the configuration")
            vehicle_id = next(
                (
                    str(vehicle["vehicle_id"])
                    for vehicle in config.vehicles
                    if vehicle["gazebo_model_name"] == incident_vehicle
                ),
                None,
            )
            if vehicle_id is None:
                raise Px4ServiceError("Gazebo airspace message names an undeclared vehicle")
        except Exception as exc:
            failure = exc if isinstance(exc, Px4ServiceError) else Px4ServiceError(
                f"Gazebo airspace message could not be decoded: {exc}"
            )
            with self._airspace_lock:
                if self._airspace_topic_active and self._airspace_failure is None:
                    self._airspace_failure = failure
            return
        with self._airspace_lock:
            if not self._airspace_topic_active:
                return
            previous_events = [
                item for item in self._airspace_events
                if item["incident_vehicle"] == incident_vehicle
            ]
            samples = self._airspace_samples.setdefault(vehicle_id, {})
            if samples and sim_time_ns < max(samples):
                self._airspace_failure = Px4ServiceError("Gazebo airspace sample time regressed")
                return
            if raw["transition"] == "none":
                if sequence != 0:
                    self._airspace_failure = Px4ServiceError("Gazebo airspace non-event must use sequence zero")
                    return
            else:
                if sequence < 1 or (previous_events and sequence != int(previous_events[-1]["sequence"]) + 1):
                    self._airspace_failure = Px4ServiceError(
                        "Gazebo airspace transition sequence regressed, repeated, or skipped"
                    )
                    return
                if raw["airspace_state"] != ("inside" if raw["transition"] == "entered" else "outside"):
                    self._airspace_failure = Px4ServiceError("Gazebo airspace transition contradicts engine state")
                    return
                self._airspace_events.append(sample)
            samples[sim_time_ns] = sample
            for old_time in tuple(samples):
                if old_time < sim_time_ns - config.step_length_ns:
                    del samples[old_time]

    async def _stop_airspace_subscriber(self) -> None:
        with self._airspace_lock:
            node = self._gazebo_transport_node
            topic = self._airspace_topic_name
            subscribed = self._airspace_topic_subscribed
            self._airspace_topic_active = False
            self._airspace_topic_subscribed = False
            self._airspace_topic_name = None
            self._airspace_topic_callback = None
            self._airspace_samples.clear()
            self._airspace_events.clear()
            self._airspace_emitted_sequences.clear()
            self._airspace_failure = None
        if node is None or topic is None or not subscribed:
            return
        try:
            async with self._gazebo_transport_lock:
                result = await asyncio.to_thread(node.unsubscribe, topic)
            if result is not True:
                self._record_gz_diagnostic(
                    "airspace-topic-unsubscribe-failed",
                    {"topic": topic, "result": repr(result)},
                )
        except Exception as exc:
            self._record_gz_diagnostic(
                "airspace-topic-unsubscribe-error",
                {"topic": topic, "error_type": type(exc).__name__, "error": str(exc)},
            )

    async def wait_for_airspace(self, *, requested_at: tuple[int, int]) -> None:
        config = self._require_config()
        if config.airspace_transition is None:
            return
        engine_time = self._time_origin_ns + requested_at[1]
        deadline = asyncio.get_running_loop().time() + config.command_timeout_ms / 1000
        while True:
            with self._airspace_lock:
                if self._airspace_failure is not None:
                    raise Px4ServiceError("Gazebo airspace plugin stream failed") from self._airspace_failure
                if not self._airspace_topic_active:
                    raise Px4ServiceError("Gazebo airspace plugin stream is not active")
                if all(
                    engine_time in self._airspace_samples.get(str(vehicle["vehicle_id"]), {})
                    for vehicle in config.vehicles
                ):
                    return
            if asyncio.get_running_loop().time() >= deadline:
                raise Px4ServiceError("Gazebo airspace plugin did not close every vehicle at the barrier")
            await asyncio.sleep(0.005)

    def airspace_transition_events(
        self, *, requested_at: tuple[int, int]
    ) -> tuple[dict[str, object], ...]:
        raw_target = self._time_origin_ns + requested_at[1]
        with self._airspace_lock:
            if self._airspace_failure is not None:
                raise Px4ServiceError("Gazebo airspace plugin stream failed") from self._airspace_failure
            pending = tuple(sorted(
                (
                    item for item in self._airspace_events
                    if self._time_origin_ns < int(item["sim_time_ns"]) <= raw_target
                    and (str(item["incident_vehicle"]), int(item["sequence"])) not in self._airspace_emitted_sequences
                ),
                key=lambda item: (int(item["sim_time_ns"]), str(item["incident_vehicle"]), int(item["sequence"])),
            ))
            self._airspace_emitted_sequences.update(
                (str(item["incident_vehicle"]), int(item["sequence"])) for item in pending
            )
            return tuple(json.loads(_canonical_json({
                **item, "logical_origin_engine_ns": self._time_origin_ns,
            })) for item in pending)

    def airspace_observation(
        self, *, vehicle_id: str, requested_at: tuple[int, int]
    ) -> Mapping[str, object]:
        if not isinstance(vehicle_id, str) or not vehicle_id:
            raise Px4ServiceError("airspace observation vehicle_id is invalid")
        if (
            not isinstance(requested_at, tuple)
            or len(requested_at) != 2
            or not isinstance(requested_at[0], int)
            or not isinstance(requested_at[1], int)
        ):
            raise Px4ServiceError("airspace observation requested_at is invalid")
        config = self._require_config()
        engine_time = self._time_origin_ns + requested_at[1]
        with self._airspace_lock:
            failure = self._airspace_failure
            sample = self._airspace_samples.get(vehicle_id, {}).get(engine_time)
            transitions = tuple(
                item for item in self._airspace_events
                if sample is not None and item["incident_vehicle"] == sample["incident_vehicle"]
                and engine_time - config.step_length_ns < int(item["sim_time_ns"]) <= engine_time
                and int(item["sim_time_ns"]) > self._time_origin_ns
            )
        if failure is not None:
            raise Px4ServiceError("Gazebo airspace plugin stream failed") from failure
        if sample is None:
            raise Px4ServiceError(
                "Gazebo airspace plugin has no sample at the requested barrier"
            )
        if len(transitions) > 1:
            raise Px4ServiceError("multiple airspace transitions in one barrier cannot be collapsed")
        transition = transitions[0] if transitions else None
        if transition is not None and transition["airspace_state"] != sample["airspace_state"]:
            raise Px4ServiceError("airspace transition differs from the boundary state")
        return {
            "schema_version": "aero-bench.observation.urban-safety.v1",
            "vehicle_id": vehicle_id,
            "region_id": sample["region_id"],
            "airspace_state": sample["airspace_state"],
            "transition": transition["transition"] if transition is not None else "none",
            "transition_sequence": transition["sequence"] if transition is not None else 0,
            "transition_engine_sim_time_ns": transition["sim_time_ns"] if transition is not None else None,
            "engine_sim_time_ns": sample["sim_time_ns"],
            "logical_origin_engine_ns": self._time_origin_ns,
            "simulation_time_ns": requested_at[1],
            "source": sample["source"],
            "world_sha256": sample["world_digest"],
            "region_sha256": sample["region_digest"],
            "position_enu_m": {
                "x": sample["position_enu_m"]["east"],
                "y": sample["position_enu_m"]["north"],
                "z": sample["position_enu_m"]["up"],
            },
        }

    def _require_gazebo_transport(
        self, config: PreparedConfig
    ) -> tuple[Any, Any, Any]:
        """Load the pinned Gazebo transport bindings for WorldControl calls."""

        node = self._gazebo_transport_node
        world_control_type = self._gazebo_world_control_type
        boolean_type = self._gazebo_boolean_type
        if node is not None and world_control_type is not None and boolean_type is not None:
            return node, world_control_type, boolean_type
        try:
            if "/usr/lib/python3/dist-packages" not in sys.path:
                sys.path.append("/usr/lib/python3/dist-packages")
            import gz.transport13 as gz_transport
            from gz.msgs10.boolean_pb2 import Boolean
            from gz.msgs10.world_control_pb2 import WorldControl
        except (ImportError, OSError) as exc:
            raise Px4ServiceError(
                "Gazebo Python transport binding is unavailable"
            ) from exc
        options = gz_transport.NodeOptions()
        options.partition = self._base_env(config)["GZ_PARTITION"]
        try:
            node = gz_transport.Node(options)
        except Exception as exc:
            raise Px4ServiceError(
                "Gazebo Python transport node could not be initialized"
            ) from exc
        self._gazebo_transport_node = node
        self._gazebo_world_control_type = WorldControl
        self._gazebo_boolean_type = Boolean
        return node, WorldControl, Boolean

    @staticmethod
    def _parse_gz_boolean_response(response: str) -> bool:
        match = re.fullmatch(
            r"[ \t\r\n\v\f]*data[ \t\r\n\v\f]*:[ \t\r\n\v\f]*(true|false)[ \t\r\n\v\f]*",
            response,
        )
        if match is None:
            raise Px4ServiceError(
                "Gazebo service response is not a single gz.msgs.Boolean data field"
            )
        return match.group(1) == "true"

    async def _gazebo_world_control(
        self,
        service: str,
        *,
        multi_step: int,
        timeout_s: float,
    ) -> None:
        if multi_step <= 0:
            raise Px4ServiceError("Gazebo WorldControl multi_step must be positive")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise Px4ServiceError("Gazebo WorldControl timeout must be positive")
        config = self._require_config()
        timeout_ms = max(1, math.ceil(timeout_s * 1000))
        request = f"pause: true\nmulti_step: {multi_step}"
        arguments = (
            "service",
            "--service",
            service,
            "--reqtype",
            "gz.msgs.WorldControl",
            "--reptype",
            "gz.msgs.Boolean",
            "--timeout",
            str(timeout_ms),
            "--req",
            request,
        )
        try:
            response = await self._gz_cli(
                arguments,
                config,
                timeout_s=timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except Px4ServiceError as exc:
            self._record_gz_diagnostic(
                "world-control-cli-request-error",
                {
                    "service": service,
                    "multi_step": multi_step,
                    "timeout_s": timeout_s,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise _GazeboWorldControlAckError(
                "Gazebo WorldControl CLI request failed",
                lost_reply=True,
            ) from exc
        try:
            response_data = self._parse_gz_boolean_response(response)
        except Px4ServiceError as exc:
            self._record_gz_diagnostic(
                "world-control-response-invalid",
                {
                    "service": service,
                    "multi_step": multi_step,
                    "timeout_s": timeout_s,
                    "response": response[-GZ_COMMAND_DIAGNOSTIC_TAIL_BYTES:],
                    "transport": "gz-service-cli",
                },
            )
            response_repr = repr(response[:GZ_COMMAND_DIAGNOSTIC_TAIL_BYTES])
            if len(response) > GZ_COMMAND_DIAGNOSTIC_TAIL_BYTES:
                response_repr += "...<truncated>"
            raise _GazeboWorldControlAckError(
                "Gazebo WorldControl CLI acknowledgement is invalid; "
                f"raw stdout={response_repr}",
                lost_reply=False,
            ) from exc
        if response_data is not True:
            self._record_gz_diagnostic(
                "world-control-response-rejected",
                {
                    "service": service,
                    "multi_step": multi_step,
                    "timeout_s": timeout_s,
                    "response_data": False,
                    "lost_reply": False,
                    "transport": "gz-service-cli",
                },
            )
            raise _GazeboWorldControlAckError(
                "Gazebo WorldControl acknowledgement reported failure",
                lost_reply=False,
            )

    async def _gz_cli(
        self,
        arguments: Sequence[str],
        config: PreparedConfig,
        *,
        timeout_s: float | None = None,
    ) -> str:
        timeout = config.command_timeout_ms / 1000 if timeout_s is None else timeout_s
        process = await asyncio.create_subprocess_exec(
            str(self._gz_bin),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._tmp_dir),
            env=self._base_env(config),
            start_new_session=True,
        )
        communicate_task = asyncio.create_task(process.communicate())
        try:
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communicate_task),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                _signal_process_group(process, signal.SIGKILL)
                try:
                    await process.wait()
                except ProcessLookupError:
                    pass
                try:
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.shield(communicate_task),
                        timeout=2.0,
                    )
                except (asyncio.TimeoutError, ProcessLookupError):
                    communicate_task.cancel()
                    drained = await asyncio.gather(
                        communicate_task, return_exceptions=True
                    )
                    result = drained[0] if drained else None
                    if isinstance(result, tuple) and len(result) == 2:
                        stdout, stderr = result
                    else:
                        stdout, stderr = b"", b""
                stderr_text = stderr.decode("utf-8", "replace")
                stdout_text = stdout.decode("utf-8", "replace")
                stderr_tail = stderr_text[-GZ_COMMAND_DIAGNOSTIC_TAIL_BYTES:]
                stdout_tail = stdout_text[-GZ_COMMAND_DIAGNOSTIC_TAIL_BYTES:]
                try:
                    self._record_gz_diagnostic(
                        "command-timeout",
                        {
                            "arguments": list(arguments),
                            "timeout_s": timeout,
                            "returncode": process.returncode,
                            "stderr_tail": stderr_tail,
                            "stdout_tail": stdout_tail,
                        },
                    )
                except OSError:
                    # A diagnostic filesystem failure must not replace the
                    # transport failure that caused the command to time out.
                    pass
                detail = "gz command timed out: " + " ".join(arguments)
                if stderr_tail:
                    detail += f" stderr={stderr_tail}"
                raise Px4ServiceError(detail) from exc
        except asyncio.CancelledError:
            communicate_task.cancel()
            _signal_process_group(process, signal.SIGKILL)
            try:
                await process.wait()
            except ProcessLookupError:
                pass
            await asyncio.gather(communicate_task, return_exceptions=True)
            raise
        if process.returncode != 0:
            raise Px4ServiceError(
                "gz command failed: "
                + " ".join(arguments)
                + f" stderr={stderr.decode('utf-8', 'replace')}"
            )
        return stdout.decode("utf-8")

    async def _spawn_process(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_name: str,
    ) -> None:
        log_dir = self._tmp_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"{log_name}.stdout"
        stdout = stdout_path.open("wb", buffering=0)
        stderr = (log_dir / f"{log_name}.stderr").open("wb", buffering=0)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        self._processes.append(process)
        self._log_paths[log_name] = stdout_path

    def _base_env(self, config: PreparedConfig) -> dict[str, str]:
        cache_dir = self._tmp_dir / "xdg-cache"
        runtime_dir = self._tmp_dir / "runtime"
        cache_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.chmod(0o700)
        environment = dict(os.environ)
        environment["HOME"] = str(self._tmp_dir)
        environment["XDG_CACHE_HOME"] = str(cache_dir)
        environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
        environment["TMPDIR"] = str(self._tmp_dir)
        environment["PX4_GZ_MODELS"] = str(self._gz_models_dir)
        environment["PX4_GZ_WORLDS"] = str(self._gz_worlds_dir)
        environment["GZ_PARTITION"] = f"aero-px4-{config.run_id[:12]}"
        environment["GZ_IP"] = "127.0.0.1"
        environment["HEADLESS"] = "1"
        environment["LP_NUM_THREADS"] = "4"
        return environment

    def _run_blocking(
        self, argv: Sequence[str], *, label: str, timeout_s: float
    ) -> str:
        try:
            completed = subprocess.run(
                list(argv),
                check=False,
                capture_output=True,
                text=True,
                env=self._base_env(self._require_config())
                if self._config is not None
                else dict(os.environ),
                timeout=timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Px4ServiceError(f"{label} failed") from exc
        if completed.returncode != 0:
            raise Px4ServiceError(f"{label} failed: {completed.stderr.strip()}")
        return completed.stdout


def _finite_attr(source: Any, name: str) -> float:
    value = getattr(source, name, None)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise Px4ServiceError(f"MAVSDK omitted {name}")
    number = float(value)
    if not math.isfinite(number):
        raise Px4ServiceError(f"MAVSDK {name} is not finite")
    return number


def _mavsdk_heading_from_enu_yaw(yaw_deg: float) -> float:
    """Convert the formal ENU yaw to MAVSDK's NED heading convention.

    Formal flight commands expose orientation in the benchmark's ENU frame:
    zero points east and positive angles rotate towards north.  MAVSDK's
    ``Action.goto_location`` instead consumes a NED heading where zero points
    north and positive angles rotate clockwise.  Passing the formal value
    through unchanged rotates the x500 camera by ninety degrees and makes a
    geometrically valid inspection impossible even though the vehicle reaches
    the requested position.
    """

    if not math.isfinite(yaw_deg):
        raise Px4ServiceError("MAVSDK heading conversion received a non-finite yaw")
    heading = 90.0 - yaw_deg
    # Keep the wire value in the conventional half-open degree interval while
    # retaining the exact direction represented by the formal command.
    return (heading + 180.0) % 360.0 - 180.0


def _pose_from_json(item: Mapping[str, Any]) -> dict[str, float]:
    nested = item.get("pose")
    source = nested if isinstance(nested, dict) else item
    position = source.get("position")
    orientation = source.get("orientation")
    if position is None:
        position = {}
    if orientation is None:
        orientation = {}
    if not isinstance(position, dict) or not isinstance(orientation, dict):
        raise Px4ServiceError("Gazebo pose omitted position or orientation")
    x = _proto3_float(position.get("x"), label="pose.x")
    y = _proto3_float(position.get("y"), label="pose.y")
    z = _proto3_float(position.get("z"), label="pose.z")
    qx = _proto3_float(orientation.get("x"), label="pose.qx")
    qy = _proto3_float(orientation.get("y"), label="pose.qy")
    qz = _proto3_float(orientation.get("z"), label="pose.qz")
    qw = _proto3_float(orientation.get("w"), label="pose.qw")
    if qx == 0.0 and qy == 0.0 and qz == 0.0 and qw == 0.0:
        qw = 1.0
    roll, pitch, yaw = quaternion_to_rpy(qx, qy, qz, qw)
    return {
        "x_m": x,
        "y_m": y,
        "z_m": z,
        "roll_rad": roll,
        "pitch_rad": pitch,
        "yaw_rad": yaw,
    }


def _signal_process_group(process: asyncio.subprocess.Process, sig: int) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, sig)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.send_signal(sig)
    except ProcessLookupError:
        return


class Px4GazeboService:
    """Strict JSON-line service backed by real PX4 SITL, Gazebo, and MAVSDK."""

    def __init__(
        self,
        *,
        artifact_root: Path,
        rpc_port: int,
        workload_identity: WorkloadIdentity,
        session_token: str,
        tmp_dir: Path | None = None,
    ):
        if not 1024 <= rpc_port <= 65535:
            raise ValueError("PX4 RPC port is outside the user-service range")
        if SHA256.fullmatch(session_token) is None or session_token == "0" * 64:
            raise ValueError(
                "PX4 session token must be an executor-issued SHA-256 digest"
            )
        if workload_identity.provider_port != rpc_port:
            raise ValueError("workload provider port must equal the RPC port")
        self._rpc_port = rpc_port
        self._workload_identity = workload_identity
        self._motion_frame = _motion_frame_authority(workload_identity)
        self._session_token = session_token
        requirements = workload_identity.artifact_requirements
        self._evidence = {
            str(requirement["artifact_type"]): EvidenceWriter(
                artifact_root,
                requirement,
                allowed_requirements=requirements,
            )
            for requirement in requirements
        }
        self._stack = RealPx4Stack(
            tmp_dir=tmp_dir or Path(os.environ.get("TMPDIR", "/tmp")),
            bundle_root=workload_identity.bundle_root,
        )
        self._config: PreparedConfig | None = None
        self._current: tuple[int, int] | None = None
        self._records: list[dict[str, object]] = []
        self._observation_records: list[dict[str, object]] = []
        self._public_sensor_frame_records: list[dict[str, object]] = []
        self._pending_public_sensor_frames: list[dict[str, object]] = []
        self._observation_envelopes: dict[tuple[str, str, int], dict[str, object]] = {}
        self._latest_views: dict[str, dict[str, object]] = {}
        self._shutdown_requested = asyncio.Event()
        self._lock = asyncio.Lock()
        self._closed = False
        self._shutdown_started = False
        self._finalization: dict[str, object] | None = None
        # Command records are authoritative per vehicle.  The singular field is
        # retained only as a compatibility diagnostic for existing callers: it is
        # populated when exactly one record exists and is cleared for concurrent
        # records, never used to gate dispatch or completion.
        self._command_records_by_vehicle: dict[str, UnresolvedCommandRecord] = {}
        self._command_record: UnresolvedCommandRecord | None = None
        self._command_ids: set[str] = set()
        self._lifecycle: ProviderLifecycle | None = None

    def _sync_command_record_view(self) -> None:
        """Expose a singular diagnostic only when the map has one record."""
        if len(self._command_records_by_vehicle) == 1:
            self._command_record = next(iter(self._command_records_by_vehicle.values()))
        else:
            self._command_record = None

    def _is_engineering_horizon(self, target: tuple[int, int]) -> bool:
        # Formal urban runs are resolver-bound to 3000 steps; only shorter
        # engineering recordings may interrupt an applied command at the end.
        task = self._workload_identity.scenario.scenario.get("task")
        max_steps = self._workload_identity.clock_max_steps
        return (
            isinstance(task, dict)
            and task.get("package_id") == "urban.uav-recovery-demo.v1"
            and max_steps is not None
            and 0 < max_steps < 3_000
            and target
            == (max_steps, max_steps * self._workload_identity.clock_step_ns)
        )

    @property
    def shutdown_requested(self) -> asyncio.Event:
        return self._shutdown_requested

    @property
    def lifecycle(self) -> ProviderLifecycle | None:
        return self._lifecycle

    def _require_lifecycle(self, *expected: ProviderLifecycle) -> ProviderLifecycle:
        lifecycle = self._lifecycle
        if lifecycle not in expected:
            names = ", ".join(item.value for item in expected)
            observed = lifecycle.value if lifecycle is not None else "UNINITIALIZED"
            raise Px4ServiceError(
                f"PX4 lifecycle must be one of [{names}], observed {observed}"
            )
        return lifecycle

    def _transition_lifecycle(self, target: ProviderLifecycle) -> None:
        current = self._lifecycle
        if current is None:
            if target != ProviderLifecycle.BARRIER_PAUSED:
                raise Px4ServiceError(
                    "PX4 lifecycle can initialize only at BARRIER_PAUSED"
                )
        elif target not in _ALLOWED_LIFECYCLE_TRANSITIONS[current]:
            raise Px4ServiceError(
                f"invalid PX4 lifecycle transition {current.value} -> {target.value}"
            )
        self._lifecycle = target

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    async def close_runtime(self) -> None:
        await self._stack.stop()
        self._closed = True

    def _parse_prepare(self, payload: Mapping[str, Any]) -> PreparedConfig:
        # world_input is an OPTIONAL bundle-world extension of the prepare
        # request: non-logistics senders that do not declare a bundle world
        # keep their exact established request shape, while the host provider
        # carries the digest-pinned declaration when the provider config binds
        # one.  Pop before the strict exact-key validation of the established
        # prepare fields.
        payload_copy = dict(payload)
        raw_world_input = payload_copy.pop("world_input", None)
        raw = _strict_object(
            payload_copy,
            required=frozenset(
                {
                    "provider_id",
                    "run_id",
                    "protocol_version",
                    "runtime_image",
                    "config_digest",
                    "scenario_digest",
                    "artifact_requirements",
                    "session_token",
                    "endpoint",
                }
            ),
            label="PX4 prepare request",
        )
        provider_id = _string(
            raw["provider_id"], label="provider_id", pattern=IDENTIFIER
        )
        if provider_id != self._workload_identity.provider_id:
            raise Px4ServiceError("prepare provider_id does not match the workload")
        run_id = _sha256(raw["run_id"], label="run_id")
        if run_id != self._workload_identity.run_id:
            raise Px4ServiceError("prepare run_id does not match AERO_BENCH_RUN_ID")
        protocol_version = _string(raw["protocol_version"], label="protocol_version")
        if protocol_version != PROTOCOL_VERSION:
            raise Px4ServiceError("PX4 provider protocol version mismatch")
        runtime_image = _safe_runtime_image(raw["runtime_image"])
        if runtime_image != self._workload_identity.runtime_image:
            raise Px4ServiceError("prepare runtime_image does not match the contract")
        config_digest = _sha256(raw["config_digest"], label="config_digest")
        if config_digest != self._workload_identity.config_digest:
            raise Px4ServiceError("prepare config_digest does not match the contract")
        scenario_digest = _sha256(raw["scenario_digest"], label="scenario_digest")
        if scenario_digest != self._workload_identity.scenario_digest:
            raise Px4ServiceError("prepare scenario_digest does not match the contract")

        requirements = raw["artifact_requirements"]
        if not isinstance(requirements, list) or len(requirements) != len(
            self._workload_identity.artifact_requirements
        ):
            raise Px4ServiceError(
                "prepare ArtifactRequirement inventory has the wrong size"
            )
        parsed_requirements = tuple(
            _artifact_requirement(item, label=f"prepare artifact_requirements[{index}]")
            for index, item in enumerate(requirements)
        )
        if parsed_requirements != self._workload_identity.artifact_requirements:
            raise Px4ServiceError(
                "prepare ArtifactRequirements do not match the workload contract"
            )

        endpoint = _strict_object(
            raw["endpoint"],
            required=frozenset({"host", "port"}),
            label="endpoint",
        )
        endpoint_host = _string(endpoint["host"], label="endpoint.host", pattern=HOST)
        endpoint_port = _integer(endpoint["port"], label="endpoint.port", minimum=1024)
        if endpoint_port > 65535 or endpoint_port != self._rpc_port:
            raise Px4ServiceError("prepare endpoint.port is not the explicit bind port")

        declared = self._workload_identity.provider_config
        world_input = (
            _parse_world_input_value(raw_world_input, label="world_input")
            if raw_world_input is not None
            else None
        )
        if world_input != declared.get("world_input"):
            raise Px4ServiceError(
                "prepare world_input must exactly match the digest-pinned "
                "provider config"
            )
        px4 = declared["px4"]
        gazebo = declared["gazebo"]
        mavsdk = declared["mavsdk"]
        if px4["version"] != PX4_VERSION or px4["commit"] != PX4_COMMIT:
            raise Px4ServiceError(
                "PX4 runtime identity does not match the pinned service"
            )
        if gazebo["version"] != GAZEBO_VERSION or gazebo["commit"] != GAZEBO_COMMIT:
            raise Px4ServiceError(
                "Gazebo runtime identity does not match the pinned service"
            )
        if mavsdk["version"] != MAVSDK_VERSION or mavsdk["commit"] != MAVSDK_COMMIT:
            raise Px4ServiceError(
                "MAVSDK runtime identity does not match the pinned service"
            )

        world_name = self._workload_identity.world_name
        world_sdf = f"{world_name}.sdf"
        physics_step_ns = int(declared["physics_step_ns"])
        step_length_ns = self._workload_identity.clock_step_ns
        if step_length_ns % physics_step_ns != 0:
            raise Px4ServiceError(
                "scenario clock step_ns must be a multiple of physics_step_ns"
            )
        parsed_vehicles = self._workload_identity.vehicles
        required_commands = tuple(str(item) for item in declared["required_commands"])
        command_timeout_ms = int(declared["command_timeout_ms"])
        physical_completion_policy = dict(declared["physical_completion_policy"])
        _validate_physical_completion_window(
            step_length_ns=step_length_ns,
            policy=physical_completion_policy,
            label="physical_completion_policy",
        )
        maximum_agent_decision_wall_time_ms = int(
            declared["maximum_agent_decision_wall_time_ms"]
        )
        heartbeat_timeout_fixed_margin_ms = int(
            declared["heartbeat_timeout_fixed_margin_ms"]
        )
        incoming_heartbeat_timeout_s(
            maximum_agent_decision_wall_time_ms=maximum_agent_decision_wall_time_ms,
            fixed_margin_ms=heartbeat_timeout_fixed_margin_ms,
        )
        inspections = self._workload_identity.inspections
        camera_declarations = self._workload_identity.camera_declarations
        urban_camera_observations = self._workload_identity.urban_camera_observations
        flight_observations = self._workload_identity.flight_observations
        contact_model_tokens = _scenario_contact_model_tokens(
            scenario=self._workload_identity.scenario,
            vehicles=parsed_vehicles,
            world_source_sdf=self._workload_identity.world_source_sdf,
            inspections=inspections,
        )
        return PreparedConfig(
            provider_id=provider_id,
            run_id=run_id,
            protocol_version=protocol_version,
            runtime_image=runtime_image,
            config_digest=config_digest,
            artifact_requirements=parsed_requirements,
            endpoint_host=endpoint_host,
            endpoint_port=endpoint_port,
            world_name=world_name,
            world_sdf=world_sdf,
            world_source_sdf=self._workload_identity.world_source_sdf,
            world_input=world_input,
            physics_step_ns=physics_step_ns,
            step_length_ns=step_length_ns,
            px4_executable=str(declared["px4_executable"]),
            gazebo_executable=str(declared["gazebo_executable"]),
            mavsdk_server_executable=str(declared["mavsdk_server_executable"]),
            vehicles=parsed_vehicles,
            required_commands=required_commands,
            command_timeout_ms=command_timeout_ms,
            physical_completion_policy=physical_completion_policy,
            maximum_agent_decision_wall_time_ms=(maximum_agent_decision_wall_time_ms),
            heartbeat_timeout_fixed_margin_ms=heartbeat_timeout_fixed_margin_ms,
            inspections=inspections,
            inspection_target_model_sdfs=(
                self._workload_identity.inspection_target_model_sdfs
            ),
            airspace_transition=self._workload_identity.airspace_transition,
            contact_model_tokens=contact_model_tokens,
            camera_declarations=camera_declarations,
            urban_camera_observations=urban_camera_observations,
            flight_observations=flight_observations,
        )

    def _require_identity(self, payload: Mapping[str, Any]) -> PreparedConfig:
        config = self._config
        if config is None:
            raise Px4ServiceError("PX4 provider has not completed prepare")
        if "provider_id" not in payload or "run_id" not in payload:
            raise Px4ServiceError("PX4 request must identify provider and run")
        if payload["provider_id"] != config.provider_id:
            raise Px4ServiceError("PX4 request provider identity mismatch")
        if payload["run_id"] != config.run_id:
            raise Px4ServiceError("PX4 request run identity mismatch")
        return config

    @staticmethod
    def _command_audit_spec(tool_id: str) -> Mapping[str, object]:
        spec = TOOL_COMMAND_AUDIT_SPEC.get(tool_id)
        if spec is None:
            raise Px4ServiceError(f"unsupported PX4/MAVSDK tool {tool_id!r}")
        return spec

    @staticmethod
    def _command_audit_bundle(
        *,
        record: UnresolvedCommandRecord,
        vehicle_system_id: int,
        records: Sequence[Mapping[str, object]],
        terminal_ack_ingress_record: Mapping[str, object],
        terminal_ack_disposition_record: Mapping[str, object],
    ) -> dict[str, object]:
        canonical_journal = b"".join(
            _canonical_json(dict(item)) + b"\n" for item in records
        )
        return {
            "schema_version": COMMAND_AUDIT_SCHEMA_VERSION,
            "vehicle_system_id": vehicle_system_id,
            "expected_mav_cmd": record.expected_mav_cmd,
            "expected_wire_type": record.expected_wire_type,
            "records": [dict(item) for item in records],
            "record_sha256s": [_digest_json(item) for item in records],
            "journal_sha256": _digest_bytes(canonical_journal),
            "terminal_ack_ingress_record": dict(terminal_ack_ingress_record),
            "terminal_ack_ingress_record_sha256": _digest_json(
                terminal_ack_ingress_record
            ),
            "terminal_ack_disposition_record": dict(terminal_ack_disposition_record),
            "terminal_ack_disposition_record_sha256": _digest_json(
                terminal_ack_disposition_record
            ),
        }

    @staticmethod
    def _vehicle_system_id(config: PreparedConfig, vehicle_id: str) -> int:
        matches = [item for item in config.vehicles if item["vehicle_id"] == vehicle_id]
        if len(matches) != 1:
            raise Px4ServiceError(f"vehicle declaration is missing for {vehicle_id}")
        return _bounded_integer(
            matches[0]["system_id"],
            label=f"{vehicle_id}.system_id",
            minimum=1,
            maximum=255,
        )

    def _drain_prior_command_audit_records(self, vehicle_id: str) -> str | None:
        try:
            journal_records = self._stack.read_command_audit_records(
                vehicle_id,
                require_complete=True,
            )
        except Px4ServiceError as exc:
            return str(exc)
        try:
            _completed, active = _command_audit_transactions(journal_records)
        except Px4ServiceError as exc:
            return str(exc)
        if active is not None:
            return "decoded command audit has an unresolved pre-window transaction"
        return None

    def _correlate_command_audit(
        self,
        *,
        config: PreparedConfig,
        record: UnresolvedCommandRecord,
    ) -> tuple[str, dict[str, object] | None, str | None]:
        try:
            journal_records = self._stack.read_command_audit_records(
                record.vehicle_id,
                require_complete=True,
            )
        except Px4ServiceError as exc:
            return COMMAND_AUDIT_CORRUPT_STATUS, None, str(exc)
        if not journal_records:
            return (
                COMMAND_AUDIT_MISSING_STATUS,
                None,
                "decoded COMMAND_ACK journal is empty",
            )
        try:
            completed, active = _command_audit_transactions(journal_records)
        except Px4ServiceError as exc:
            return COMMAND_AUDIT_CORRUPT_STATUS, None, str(exc)
        if active is not None:
            return (
                COMMAND_AUDIT_MISSING_STATUS,
                None,
                "decoded COMMAND_ACK terminal result is missing",
            )
        vehicle_system_id = self._vehicle_system_id(config, record.vehicle_id)
        expected_transactions: list[
            tuple[CommandAuditTransaction, dict[str, object]]
        ] = []
        for candidate in completed:
            transport_records = [
                item
                for item in candidate.records
                if item.get("kind") == COMMAND_AUDIT_TRANSPORT_KIND
            ]
            if not transport_records:
                continue
            if any(
                int(item["command"]) == record.expected_mav_cmd
                and item["wire_type"] == record.expected_wire_type
                and int(item["source_system_id"]) == MAVSDK_SERVER_SYSTEM_ID
                and int(item["source_component_id"]) == MAVSDK_SERVER_COMPONENT_ID
                and int(item["target_system_id"]) == vehicle_system_id
                for item in transport_records
            ):
                if any(
                    not _command_audit_transport_matches(
                        transport_records[0], item
                    )
                    for item in transport_records[1:]
                ):
                    return (
                        COMMAND_AUDIT_CORRUPT_STATUS,
                        None,
                        "decoded command audit contains multiple transport identities for the staged command",
                    )
                expected_transactions.append((candidate, transport_records[0]))
        if not expected_transactions:
            return (
                COMMAND_AUDIT_MISSING_STATUS,
                None,
                "decoded COMMAND_ACK for the staged MAVLink command is missing",
            )
        if len(expected_transactions) != 1:
            return (
                COMMAND_AUDIT_CORRUPT_STATUS,
                None,
                "decoded command audit contains multiple completed transactions for the staged command",
            )
        transaction, transport_record = expected_transactions[0]
        if int(transport_record["target_component_id"]) <= 0:
            return (
                COMMAND_AUDIT_CORRUPT_STATUS,
                None,
                "command transport acceptance has an invalid target component",
            )
        if (
            int(transport_record["command"]) != record.expected_mav_cmd
            or transport_record["wire_type"] != record.expected_wire_type
            or int(transport_record["source_system_id"]) != MAVSDK_SERVER_SYSTEM_ID
            or int(transport_record["source_component_id"])
            != MAVSDK_SERVER_COMPONENT_ID
            or int(transport_record["target_system_id"]) != vehicle_system_id
            or int(transport_record["target_component_id"]) <= 0
        ):
            return (
                COMMAND_AUDIT_CORRUPT_STATUS,
                None,
                "command transport acceptance does not match the staged MAVSDK command",
            )
        terminal_ingress = transaction.terminal_ack_ingress_record
        terminal_disposition = transaction.terminal_ack_disposition_record
        if terminal_ingress is None or terminal_disposition is None:
            return (
                COMMAND_AUDIT_CORRUPT_STATUS,
                None,
                "decoded command audit terminal ingress identity is missing",
            )
        bundle = self._command_audit_bundle(
            record=record,
            vehicle_system_id=vehicle_system_id,
            records=journal_records,
            terminal_ack_ingress_record=terminal_ingress,
            terminal_ack_disposition_record=terminal_disposition,
        )
        terminal_result = int(terminal_ingress["result"])
        if terminal_result == MAV_RESULT_ACCEPTED:
            return COMMAND_AUDIT_APPLIED_STATUS, bundle, None
        return (
            COMMAND_AUDIT_FAILED_STATUS,
            bundle,
            f"decoded COMMAND_ACK result was nonaccepted: {terminal_result}",
        )

    @staticmethod
    def _command_mode_allowlist(
        policy: Mapping[str, Any], tool_id: str
    ) -> tuple[str, ...]:
        mapping = {
            ARM_TOOL: "arm_allowed_modes",
            DISARM_TOOL: "disarm_allowed_modes",
            TAKEOFF_TOOL: "takeoff_allowed_modes",
            GOTO_TOOL: "goto_allowed_modes",
            HOLD_TOOL: "hold_allowed_modes",
            LAND_TOOL: "land_allowed_modes",
        }
        key = mapping.get(tool_id)
        if key is None:
            raise Px4ServiceError(f"unsupported PX4/MAVSDK tool {tool_id!r}")
        allowlist = policy.get(key)
        if not isinstance(allowlist, tuple):
            raise Px4ServiceError(
                f"physical completion policy allowlist is invalid for {tool_id}"
            )
        return allowlist

    @staticmethod
    def _command_thresholds(
        policy: Mapping[str, Any], tool_id: str
    ) -> dict[str, object]:
        thresholds: dict[str, object] = {
            "allowlisted_modes": list(
                Px4GazeboService._command_mode_allowlist(policy, tool_id)
            ),
            "min_settle_samples": policy["min_settle_samples"],
            "settle_duration_ns": policy["settle_duration_ns"],
            "max_horizontal_settled_speed_m_s": policy[
                "max_horizontal_settled_speed_m_s"
            ],
            "max_vertical_settled_speed_m_s": policy["max_vertical_settled_speed_m_s"],
        }
        if tool_id == DISARM_TOOL:
            thresholds["disarm_requires_contact"] = policy["disarm_requires_contact"]
        if tool_id == TAKEOFF_TOOL:
            thresholds["takeoff_altitude_tolerance_m"] = policy[
                "takeoff_altitude_tolerance_m"
            ]
        if tool_id == GOTO_TOOL:
            thresholds["goto_horizontal_tolerance_m"] = policy[
                "goto_horizontal_tolerance_m"
            ]
            thresholds["goto_vertical_tolerance_m"] = policy[
                "goto_vertical_tolerance_m"
            ]
            thresholds["goto_minimum_progress_m"] = policy["goto_minimum_progress_m"]
        if tool_id == HOLD_TOOL:
            thresholds["hold_drift_radius_m"] = policy["hold_drift_radius_m"]
        if tool_id == LAND_TOOL:
            thresholds["landing_max_speed_m_s"] = policy["landing_max_speed_m_s"]
            thresholds["landing_max_height_proxy_m"] = policy[
                "landing_max_height_proxy_m"
            ]
        return thresholds

    def _physical_state_from_telemetry(
        self,
        telemetry: tuple[VehicleTelemetry, ...],
        *,
        vehicle_id: str,
        target: tuple[int, int],
    ) -> ParsedPhysicalState:
        match = next(
            (item for item in telemetry if item.vehicle_id == vehicle_id), None
        )
        if match is None:
            raise Px4ServiceError(
                f"PX4 physical evaluation omitted authoritative telemetry for {vehicle_id}"
            )
        if match.vehicle_id != vehicle_id:
            raise Px4ServiceError(
                "PX4 physical evaluation telemetry names the wrong vehicle"
            )
        if match.simulation_time_ns != target[1]:
            raise Px4ServiceError(
                "PX4 physical evaluation telemetry has a stale simulation time"
            )
        pose = _strict_object(
            _parse_json(match.pose_json),
            required=frozenset(
                {"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}
            ),
            label="pose_json",
        )
        position_wgs84 = _strict_object(
            _parse_json(match.position_wgs84_json),
            required=frozenset({"longitude_deg", "latitude_deg", "altitude_m"}),
            label="position_wgs84_json",
        )
        velocity_ned = _strict_object(
            _parse_json(match.velocity_json),
            required=frozenset({"north_m_s", "east_m_s", "down_m_s"}),
            label="velocity_json",
        )
        angular_velocity_body = _strict_object(
            _parse_json(match.angular_velocity_json),
            required=frozenset({"x_rad_s", "y_rad_s", "z_rad_s"}),
            label="angular_velocity_json",
        )
        parsed_pose = {
            name: _finite_number(value, label=f"pose_json.{name}")
            for name, value in pose.items()
        }
        parsed_position_wgs84 = {
            name: _finite_number(value, label=f"position_wgs84_json.{name}")
            for name, value in position_wgs84.items()
        }
        parsed_velocity_ned = {
            name: _finite_number(value, label=f"velocity_json.{name}")
            for name, value in velocity_ned.items()
        }
        parsed_angular_velocity_body = {
            name: _finite_number(value, label=f"angular_velocity_json.{name}")
            for name, value in angular_velocity_body.items()
        }
        contacts = tuple(
            _string(contact, label="classified Gazebo contact", pattern=IDENTIFIER)
            for contact in match.contacts
        )
        if contacts != tuple(sorted(set(contacts))):
            raise Px4ServiceError(
                "PX4 physical evaluation contacts are not sorted and unique"
            )
        if not all(
            type(value) is bool
            for value in (
                match.armed,
                match.in_air,
                match.landed,
                match.ground_contact,
                match.collision_contact,
            )
        ):
            raise Px4ServiceError(
                "PX4 physical evaluation boolean telemetry is invalid"
            )
        landed_state = _flight_mode_scalar(
            match.landed_state,
            label="PX4 physical evaluation landed_state",
        )
        if landed_state not in {"ON_GROUND", "IN_AIR", "TAKING_OFF", "LANDING"}:
            raise Px4ServiceError(
                "PX4 physical evaluation landed_state is not authoritative"
            )
        if match.landed != (landed_state == "ON_GROUND"):
            raise Px4ServiceError(
                "PX4 physical evaluation landed state is inconsistent"
            )
        if (landed_state == "ON_GROUND" and match.in_air) or (
            landed_state == "IN_AIR" and not match.in_air
        ):
            raise Px4ServiceError(
                "PX4 physical evaluation in_air and landed_state disagree"
            )
        observed_ground_contact = any(
            contact.startswith("ground.") or contact.startswith("launch_pad.")
            for contact in contacts
        )
        observed_collision_contact = any(
            not contact.startswith("ground.") and not contact.startswith("launch_pad.")
            for contact in contacts
        )
        if (
            match.ground_contact != observed_ground_contact
            or match.collision_contact != observed_collision_contact
        ):
            raise Px4ServiceError(
                "PX4 physical evaluation contact flags differ from classified contacts"
            )
        horizontal_speed_m_s = math.hypot(
            parsed_velocity_ned["north_m_s"], parsed_velocity_ned["east_m_s"]
        )
        vertical_speed_m_s = abs(parsed_velocity_ned["down_m_s"])
        total_speed_m_s = math.sqrt(
            horizontal_speed_m_s**2 + parsed_velocity_ned["down_m_s"] ** 2
        )
        if not all(
            math.isfinite(value)
            for value in (
                horizontal_speed_m_s,
                vertical_speed_m_s,
                total_speed_m_s,
            )
        ):
            raise Px4ServiceError("PX4 physical evaluation derived a non-finite speed")
        flight_mode = _flight_mode_scalar(
            match.flight_mode,
            label="PX4 physical evaluation flight_mode",
        )
        state_digest = _physical_state_digest(
            vehicle_id=vehicle_id,
            tick=target[0],
            sim_time_ns=target[1],
            pose=parsed_pose,
            position_wgs84=parsed_position_wgs84,
            velocity_ned=parsed_velocity_ned,
            angular_velocity_body=parsed_angular_velocity_body,
            flight_mode=flight_mode,
            armed=match.armed,
            in_air=match.in_air,
            landed=match.landed,
            landed_state=landed_state,
            contacts=contacts,
            ground_contact=match.ground_contact,
            collision_contact=match.collision_contact,
        )
        return ParsedPhysicalState(
            vehicle_id=vehicle_id,
            tick=target[0],
            sim_time_ns=target[1],
            pose=parsed_pose,
            position_wgs84=parsed_position_wgs84,
            velocity_ned=parsed_velocity_ned,
            angular_velocity_body=parsed_angular_velocity_body,
            flight_mode=flight_mode,
            armed=match.armed,
            in_air=match.in_air,
            landed=match.landed,
            landed_state=landed_state,
            contacts=contacts,
            ground_contact=match.ground_contact,
            collision_contact=match.collision_contact,
            state_digest=state_digest,
            horizontal_speed_m_s=horizontal_speed_m_s,
            vertical_speed_m_s=vertical_speed_m_s,
            total_speed_m_s=total_speed_m_s,
        )

    async def _fresh_physical_state(
        self, *, vehicle_id: str, target: tuple[int, int]
    ) -> ParsedPhysicalState:
        await self._stack.wait_for_telemetry()
        return self._physical_state_from_telemetry(
            await self._stack.telemetry(),
            vehicle_id=vehicle_id,
            target=target,
        )

    def _evaluate_physical_completion(
        self,
        *,
        config: PreparedConfig,
        record: UnresolvedCommandRecord,
        current: ParsedPhysicalState,
    ) -> tuple[bool, dict[str, object], dict[str, object]]:
        policy = config.physical_completion_policy
        thresholds = self._command_thresholds(policy, record.tool_id)
        mode_allowlist = set(self._command_mode_allowlist(policy, record.tool_id))
        baseline = record.baseline_state
        speeds_settled = current.horizontal_speed_m_s <= float(
            policy["max_horizontal_settled_speed_m_s"]
        ) and current.vertical_speed_m_s <= float(
            policy["max_vertical_settled_speed_m_s"]
        )
        angular_speed_rad_s = math.sqrt(
            sum(value**2 for value in current.angular_velocity_body.values())
        )
        if not math.isfinite(angular_speed_rad_s):
            raise Px4ServiceError(
                "PX4 physical completion derived non-finite angular speed"
            )
        metrics: dict[str, object] = {
            "armed": current.armed,
            "in_air": current.in_air,
            "landed": current.landed,
            "landed_state": current.landed_state,
            "flight_mode": current.flight_mode,
            "allowlisted_mode": current.flight_mode in mode_allowlist,
            "contacts": list(current.contacts),
            "ground_contact": current.ground_contact,
            "collision_contact": current.collision_contact,
            "horizontal_speed_m_s": current.horizontal_speed_m_s,
            "vertical_speed_m_s": current.vertical_speed_m_s,
            "total_speed_m_s": current.total_speed_m_s,
            "angular_speed_rad_s": angular_speed_rad_s,
            "settled_speed": speeds_settled,
        }
        satisfied = False
        if record.tool_id == ARM_TOOL:
            satisfied = (
                current.armed
                and current.landed
                and not current.in_air
                and current.ground_contact
                and not current.collision_contact
                and bool(metrics["allowlisted_mode"])
                and speeds_settled
            )
        elif record.tool_id == DISARM_TOOL:
            contact_requirement = bool(policy["disarm_requires_contact"])
            metrics["contact_requirement_satisfied"] = (
                current.ground_contact or not contact_requirement
            )
            satisfied = (
                not current.armed
                and current.landed
                and not current.in_air
                and bool(metrics["allowlisted_mode"])
                and speeds_settled
                and not current.collision_contact
                and bool(metrics["contact_requirement_satisfied"])
            )
        elif record.tool_id == TAKEOFF_TOOL:
            requested_altitude_m = _finite_number(
                record.arguments["altitude_m"], label="command.altitude_m"
            )
            gazebo_relative_altitude_m = current.pose["z_m"] - baseline.pose["z_m"]
            px4_absolute_altitude_delta_m = (
                current.position_wgs84["altitude_m"]
                - baseline.position_wgs84["altitude_m"]
            )
            metrics.update(
                {
                    "requested_relative_altitude_m": requested_altitude_m,
                    "gazebo_relative_altitude_m": gazebo_relative_altitude_m,
                    "px4_absolute_altitude_delta_m": px4_absolute_altitude_delta_m,
                    "gazebo_altitude_satisfied": (
                        abs(gazebo_relative_altitude_m - requested_altitude_m)
                        <= float(policy["takeoff_altitude_tolerance_m"])
                    ),
                    "px4_altitude_satisfied": (
                        abs(px4_absolute_altitude_delta_m - requested_altitude_m)
                        <= float(policy["takeoff_altitude_tolerance_m"])
                    ),
                    "no_collision_contact": not current.collision_contact,
                }
            )
            satisfied = (
                current.armed
                and current.in_air
                and not current.landed
                and not current.ground_contact
                and bool(metrics["allowlisted_mode"])
                and bool(metrics["no_collision_contact"])
                and speeds_settled
                and bool(metrics["gazebo_altitude_satisfied"])
                and bool(metrics["px4_altitude_satisfied"])
            )
        elif record.tool_id == GOTO_TOOL:
            target_wgs84 = {
                "latitude_deg": _finite_number(
                    record.arguments["latitude_deg"], label="command.latitude_deg"
                ),
                "longitude_deg": _finite_number(
                    record.arguments["longitude_deg"], label="command.longitude_deg"
                ),
                "altitude_m": _finite_number(
                    record.arguments["altitude_amsl_m"],
                    label="command.altitude_amsl_m",
                ),
            }
            try:
                current_delta = frame_math.wgs84_amsl_delta_enu(
                    origin_longitude_deg=_finite_number(
                        current.position_wgs84["longitude_deg"],
                        label="current.longitude_deg",
                    ),
                    origin_latitude_deg=_finite_number(
                        current.position_wgs84["latitude_deg"],
                        label="current.latitude_deg",
                    ),
                    origin_amsl_m=_finite_number(
                        current.position_wgs84["altitude_m"],
                        label="current.altitude_amsl_m",
                    ),
                    target_longitude_deg=target_wgs84["longitude_deg"],
                    target_latitude_deg=target_wgs84["latitude_deg"],
                    target_amsl_m=target_wgs84["altitude_m"],
                )
                baseline_delta = frame_math.wgs84_amsl_delta_enu(
                    origin_longitude_deg=_finite_number(
                        baseline.position_wgs84["longitude_deg"],
                        label="baseline.longitude_deg",
                    ),
                    origin_latitude_deg=_finite_number(
                        baseline.position_wgs84["latitude_deg"],
                        label="baseline.latitude_deg",
                    ),
                    origin_amsl_m=_finite_number(
                        baseline.position_wgs84["altitude_m"],
                        label="baseline.altitude_amsl_m",
                    ),
                    target_longitude_deg=target_wgs84["longitude_deg"],
                    target_latitude_deg=target_wgs84["latitude_deg"],
                    target_amsl_m=target_wgs84["altitude_m"],
                )
            except frame_math.FrameMathError as exc:
                raise Px4ServiceError("PX4 GOTO coordinate conversion failed") from exc
            current_east_m, current_north_m, current_up_m = current_delta.as_tuple()
            baseline_east_m, baseline_north_m, baseline_up_m = baseline_delta.as_tuple()
            horizontal_error_m = math.hypot(current_east_m, current_north_m)
            vertical_error_m = abs(current_up_m)
            baseline_horizontal_error_m = math.hypot(baseline_east_m, baseline_north_m)
            baseline_vertical_error_m = abs(baseline_up_m)
            target_local_x_m = baseline.pose["x_m"] + baseline_east_m
            target_local_y_m = baseline.pose["y_m"] + baseline_north_m
            target_local_z_m = baseline.pose["z_m"] + baseline_up_m
            baseline_local_error_m = math.sqrt(
                baseline_east_m**2 + baseline_north_m**2 + baseline_up_m**2
            )
            current_local_error_m = math.sqrt(
                (target_local_x_m - current.pose["x_m"]) ** 2
                + (target_local_y_m - current.pose["y_m"]) ** 2
                + (target_local_z_m - current.pose["z_m"]) ** 2
            )
            local_progress_m = baseline_local_error_m - current_local_error_m
            metrics.update(
                {
                    "target_latitude_deg": target_wgs84["latitude_deg"],
                    "target_longitude_deg": target_wgs84["longitude_deg"],
                    "target_altitude_amsl_m": target_wgs84["altitude_m"],
                    "horizontal_error_m": horizontal_error_m,
                    "vertical_error_m": vertical_error_m,
                    "baseline_horizontal_error_m": baseline_horizontal_error_m,
                    "baseline_vertical_error_m": baseline_vertical_error_m,
                    "baseline_local_error_m": baseline_local_error_m,
                    "current_local_error_m": current_local_error_m,
                    "local_progress_m": local_progress_m,
                    "baseline_outside_tolerance": (
                        baseline_horizontal_error_m
                        > float(policy["goto_horizontal_tolerance_m"])
                        or baseline_vertical_error_m
                        > float(policy["goto_vertical_tolerance_m"])
                    ),
                }
            )
            progress_required = bool(metrics["baseline_outside_tolerance"])
            metrics["progress_requirement_satisfied"] = (
                local_progress_m >= float(policy["goto_minimum_progress_m"])
                if progress_required
                else True
            )
            satisfied = (
                current.armed
                and current.in_air
                and not current.landed
                and not current.ground_contact
                and not current.collision_contact
                and bool(metrics["allowlisted_mode"])
                and speeds_settled
                and horizontal_error_m <= float(policy["goto_horizontal_tolerance_m"])
                and vertical_error_m <= float(policy["goto_vertical_tolerance_m"])
                and bool(metrics["progress_requirement_satisfied"])
            )
        elif record.tool_id == HOLD_TOOL:
            horizontal_drift_m = math.hypot(
                current.pose["x_m"] - record.hold_anchor["x_m"],
                current.pose["y_m"] - record.hold_anchor["y_m"],
            )
            metrics.update(
                {
                    "horizontal_drift_m": horizontal_drift_m,
                    "armed_state_unchanged": current.armed == baseline.armed,
                }
            )
            satisfied = (
                current.armed
                and current.in_air
                and not current.landed
                and not current.ground_contact
                and not current.collision_contact
                and bool(metrics["allowlisted_mode"])
                and speeds_settled
                and horizontal_drift_m <= float(policy["hold_drift_radius_m"])
                and bool(metrics["armed_state_unchanged"])
            )
        elif record.tool_id == LAND_TOOL:
            vehicle = next(
                item
                for item in config.vehicles
                if item["vehicle_id"] == record.vehicle_id
            )
            initial_pose = _strict_object(
                vehicle["initial_pose"],
                required=frozenset(
                    {"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}
                ),
                label="vehicle.initial_pose",
            )
            ground_baseline_proxy_z_m = _finite_number(
                initial_pose["z_m"], label="vehicle.initial_pose.z_m"
            )
            gazebo_relative_height_proxy_m = abs(
                current.pose["z_m"] - ground_baseline_proxy_z_m
            )
            metrics.update(
                {
                    "gazebo_relative_height_proxy_m": gazebo_relative_height_proxy_m,
                    "ground_baseline_proxy_z_m": ground_baseline_proxy_z_m,
                }
            )
            satisfied = (
                current.landed
                and not current.in_air
                and current.ground_contact
                and not current.collision_contact
                and not current.armed
                and bool(metrics["allowlisted_mode"])
                and current.total_speed_m_s <= float(policy["landing_max_speed_m_s"])
                and gazebo_relative_height_proxy_m
                <= float(policy["landing_max_height_proxy_m"])
            )
        else:
            raise Px4ServiceError(f"unsupported PX4/MAVSDK tool {record.tool_id!r}")
        return satisfied, thresholds, metrics

    @staticmethod
    def _command_proof_payload(
        *,
        config: PreparedConfig,
        record: UnresolvedCommandRecord,
        current: ParsedPhysicalState,
        phase: str,
        thresholds: Mapping[str, object],
        metrics: Mapping[str, object],
    ) -> dict[str, object]:
        settle_start = record.settle_start
        settle_end = record.settle_end
        settled_duration_ns = (
            0
            if settle_start is None or settle_end is None
            else settle_end[1] - settle_start[1]
        )
        if record.command_audit_status is None:
            raise Px4ServiceError("decoded command audit status is missing")
        return {
            "command_id": record.command_id,
            "tool_id": record.tool_id,
            "vehicle_id": record.vehicle_id,
            "phase": phase,
            "policy_schema_version": config.physical_completion_policy[
                "schema_version"
            ],
            "baseline_tick": record.baseline_state.tick,
            "baseline_sim_time_ns": record.baseline_state.sim_time_ns,
            "baseline_state_digest": record.baseline_state.state_digest,
            "current_tick": current.tick,
            "current_sim_time_ns": current.sim_time_ns,
            "current_state_digest": current.state_digest,
            "accepted_tick": record.accepted_at[0],
            "accepted_sim_time_ns": record.accepted_at[1],
            "applied_tick": None if record.applied_at is None else record.applied_at[0],
            "applied_sim_time_ns": (
                None if record.applied_at is None else record.applied_at[1]
            ),
            "ack_audit_status": record.command_audit_status,
            "decoded_command_audit": record.command_audit,
            "thresholds": dict(thresholds),
            "metrics": dict(metrics),
            "settling": {
                "start_tick": None if settle_start is None else settle_start[0],
                "start_sim_time_ns": None if settle_start is None else settle_start[1],
                "end_tick": None if settle_end is None else settle_end[0],
                "end_sim_time_ns": None if settle_end is None else settle_end[1],
                "sample_count": record.settle_count,
                "sample_duration_ns": settled_duration_ns,
            },
        }

    async def _advance_with_pending_commands(
        self,
        *,
        config: PreparedConfig,
        iterations: int,
        target: tuple[int, int],
    ) -> tuple[dict[str, object], ...]:
        records = tuple(
            sorted(
                self._command_records_by_vehicle.values(),
                key=lambda item: item.vehicle_id,
            )
        )
        staged_records = tuple(record for record in records if record.stage == "staged")
        applied_records = tuple(record for record in records if record.stage == "applied")
        expected = (
            ProviderLifecycle.ACTION_STAGED
            if staged_records
            else ProviderLifecycle.BARRIER_PAUSED
        )
        self._require_lifecycle(expected)
        if staged_records and iterations < 2:
            raise Px4ServiceError(
                "a staged PX4 action requires at least two physics steps for deterministic dispatch"
            )
        self._transition_lifecycle(ProviderLifecycle.RUNNING)
        command_events: list[dict[str, object]] = []
        if staged_records:
            execution_started = asyncio.Event()
            command_dispatched = asyncio.Event()
            barrier_task = asyncio.create_task(
                self._stack.multi_step(
                    iterations,
                    execution_started=execution_started,
                    command_dispatched=command_dispatched,
                )
            )
            started_task = asyncio.create_task(execution_started.wait())
            done, _ = await asyncio.wait(
                (barrier_task, started_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if barrier_task in done:
                started_task.cancel()
                await asyncio.gather(started_task, return_exceptions=True)
                await barrier_task
                raise Px4ServiceError(
                    "Gazebo execution window closed before MAVSDK action dispatch"
                )
            await started_task
            if barrier_task.done():
                await barrier_task
                raise Px4ServiceError(
                    "Gazebo execution window closed before MAVSDK action dispatch"
                )

            predispatch: dict[str, str | None] = {}
            tasks: dict[str, asyncio.Task[None]] = {}
            for record in staged_records:
                detail = self._drain_prior_command_audit_records(record.vehicle_id)
                predispatch[record.vehicle_id] = detail
                if detail is None:
                    if self._lifecycle == ProviderLifecycle.RUNNING:
                        self._transition_lifecycle(ProviderLifecycle.EXECUTING)
                    tasks[record.vehicle_id] = asyncio.create_task(
                        self._stack.apply_command(
                            {
                                "tool_id": record.tool_id,
                                "arguments": [
                                    {"name": name, "value": value}
                                    for name, value in record.arguments.items()
                                ],
                            }
                        )
                    )
            # Give every deferred call one event-loop turn to enter MAVSDK before
            # releasing the remaining deterministic physics window.
            try:
                command_dispatched.set()
                if tasks:
                    await asyncio.sleep(0)
                results = await asyncio.gather(
                    barrier_task,
                    *(tasks[vehicle_id] for vehicle_id in sorted(tasks)),
                    return_exceptions=True,
                )
            except BaseException:
                for task in tasks.values():
                    task.cancel()
                barrier_task.cancel()
                await asyncio.gather(
                    barrier_task, *tasks.values(), return_exceptions=True
                )
                raise
            barrier_error = results[0]
            if isinstance(barrier_error, BaseException):
                for task in tasks.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
                raise barrier_error
            command_results = {
                vehicle_id: result
                for vehicle_id, result in zip(
                    sorted(tasks), results[1:], strict=True
                )
            }
            await self._stack.wait_for_telemetry()
            self._transition_lifecycle(ProviderLifecycle.BARRIER_PAUSED)
            for record in staged_records:
                current = await self._fresh_physical_state(
                    vehicle_id=record.vehicle_id, target=target
                )
                _satisfied, thresholds, metrics = self._evaluate_physical_completion(
                    config=config,
                    record=record,
                    current=current,
                )
                record.last_state_digest = current.state_digest
                record.last_metrics = metrics
                predispatch_detail = predispatch[record.vehicle_id]
                if predispatch_detail is not None:
                    record.command_audit_status = COMMAND_AUDIT_CORRUPT_STATUS
                    record.command_audit = None
                    audit_detail = predispatch_detail
                else:
                    (
                        record.command_audit_status,
                        record.command_audit,
                        audit_detail,
                    ) = self._correlate_command_audit(config=config, record=record)
                command_result = command_results.get(record.vehicle_id)
                if (
                    predispatch_detail is not None
                    or isinstance(command_result, BaseException)
                    or record.command_audit_status != COMMAND_AUDIT_APPLIED_STATUS
                ):
                    record.stage = "failed"
                    record.failure_latched = True
                    detail_parts = []
                    if isinstance(command_result, BaseException):
                        detail_parts.append(str(command_result))
                    if audit_detail is not None:
                        detail_parts.append(audit_detail)
                    detail = (
                        "; ".join(detail_parts)
                        or "decoded command audit did not validate"
                    )
                    command_events.extend(
                        (
                            self._deferred_command_event(
                                config=config,
                                command_id=record.command_id,
                                tool_id=record.tool_id,
                                phase="failed",
                                time_value={
                                    "tick": target[0],
                                    "sim_time_ns": target[1],
                                },
                                detail=detail,
                            ),
                            self._deferred_command_proof_event(
                                config=config,
                                record=record,
                                current=current,
                                phase="failed",
                                time_value={
                                    "tick": target[0],
                                    "sim_time_ns": target[1],
                                },
                                thresholds=thresholds,
                                metrics=metrics,
                            ),
                        )
                    )
                    continue
                record.applied_at = target
                if self._is_engineering_horizon(target):
                    record.stage = "failed"
                    command_events.extend(
                        (
                            self._deferred_command_event(
                                config=config,
                                command_id=record.command_id,
                                tool_id=record.tool_id,
                                phase="failed",
                                time_value={
                                    "tick": target[0],
                                    "sim_time_ns": target[1],
                                },
                                detail=COMMAND_HORIZON_INTERRUPTED_DETAIL,
                            ),
                            self._deferred_command_proof_event(
                                config=config,
                                record=record,
                                current=current,
                                phase="failed",
                                time_value={
                                    "tick": target[0],
                                    "sim_time_ns": target[1],
                                },
                                thresholds=thresholds,
                                metrics=metrics,
                            ),
                        )
                    )
                    self._command_records_by_vehicle.pop(record.vehicle_id, None)
                    continue
                record.stage = "applied"
                command_events.extend(
                    (
                        self._deferred_command_event(
                            config=config,
                            command_id=record.command_id,
                            tool_id=record.tool_id,
                            phase="applied",
                            time_value={"tick": target[0], "sim_time_ns": target[1]},
                        ),
                        self._deferred_command_proof_event(
                            config=config,
                            record=record,
                            current=current,
                            phase="applied",
                            time_value={"tick": target[0], "sim_time_ns": target[1]},
                            thresholds=thresholds,
                            metrics=metrics,
                        ),
                    )
                )
        else:
            await self._stack.multi_step(iterations)
            await self._stack.wait_for_telemetry()
            self._transition_lifecycle(ProviderLifecycle.BARRIER_PAUSED)
        for record in applied_records:
            current = await self._fresh_physical_state(
                vehicle_id=record.vehicle_id, target=target
            )
            satisfied, thresholds, metrics = self._evaluate_physical_completion(
                config=config,
                record=record,
                current=current,
            )
            previous_state_digest = record.last_state_digest
            record.last_state_digest = current.state_digest
            record.last_metrics = metrics
            time_value = {"tick": target[0], "sim_time_ns": target[1]}
            if current.sim_time_ns >= record.deadline_sim_time_ns:
                record.stage = "failed"
                record.failure_latched = True
                command_events.extend(
                    (
                        self._deferred_command_event(
                            config=config,
                            command_id=record.command_id,
                            tool_id=record.tool_id,
                            phase="failed",
                            time_value=time_value,
                            detail="PX4 physical completion timed out",
                        ),
                        self._deferred_command_proof_event(
                            config=config,
                            record=record,
                            current=current,
                            phase="failed",
                            time_value=time_value,
                            thresholds=thresholds,
                            metrics=metrics,
                        ),
                    )
                )
                continue
            if satisfied:
                if current.state_digest != previous_state_digest:
                    if record.settle_start is None:
                        record.settle_start = target
                    record.settle_end = target
                    record.settle_count += 1
            else:
                record.settle_start = None
                record.settle_end = None
                record.settle_count = 0
            settled_duration_ns = (
                0
                if record.settle_start is None or record.settle_end is None
                else record.settle_end[1] - record.settle_start[1]
            )
            if record.settle_count >= int(
                config.physical_completion_policy["min_settle_samples"]
            ) and settled_duration_ns >= int(
                config.physical_completion_policy["settle_duration_ns"]
            ):
                record.stage = "completed"
                command_events.extend(
                    (
                        self._deferred_command_event(
                            config=config,
                            command_id=record.command_id,
                            tool_id=record.tool_id,
                            phase="completed",
                            time_value=time_value,
                        ),
                        self._deferred_command_proof_event(
                            config=config,
                            record=record,
                            current=current,
                            phase="completed",
                            time_value=time_value,
                            thresholds=thresholds,
                            metrics=metrics,
                        ),
                    )
                )
                self._command_records_by_vehicle.pop(record.vehicle_id, None)
                continue
            if self._is_engineering_horizon(target):
                record.stage = "failed"
                command_events.extend(
                    (
                        self._deferred_command_event(
                            config=config,
                            command_id=record.command_id,
                            tool_id=record.tool_id,
                            phase="failed",
                            time_value=time_value,
                            detail=COMMAND_HORIZON_INTERRUPTED_DETAIL,
                        ),
                        self._deferred_command_proof_event(
                            config=config,
                            record=record,
                            current=current,
                            phase="failed",
                            time_value=time_value,
                            thresholds=thresholds,
                            metrics=metrics,
                        ),
                    )
                )
                self._command_records_by_vehicle.pop(record.vehicle_id, None)
        self._sync_command_record_view()
        return tuple(command_events)

    @staticmethod
    def _deferred_command_event(
        *,
        config: PreparedConfig,
        command_id: object,
        tool_id: object,
        phase: str,
        time_value: Mapping[str, int],
        detail: str | None = None,
    ) -> dict[str, object]:
        payload: list[dict[str, object]] = [
            {"name": "schema_id", "value": COMMAND_SCHEMA},
            {"name": "command_id", "value": command_id},
            {"name": "tool_id", "value": tool_id},
            {"name": "phase", "value": phase},
        ]
        if detail is not None:
            payload.append({"name": "detail", "value": detail})
        return {
            "provider_id": config.provider_id,
            "event_id": f"command.{command_id}.{phase}",
            "time": dict(time_value),
            "payload_schema_id": COMMAND_SCHEMA,
            "payload": payload,
        }

    @staticmethod
    def _deferred_command_proof_event(
        *,
        config: PreparedConfig,
        record: UnresolvedCommandRecord,
        current: ParsedPhysicalState,
        phase: str,
        time_value: Mapping[str, int],
        thresholds: Mapping[str, object],
        metrics: Mapping[str, object],
    ) -> dict[str, object]:
        proof = Px4GazeboService._command_proof_payload(
            config=config,
            record=record,
            current=current,
            phase=phase,
            thresholds=thresholds,
            metrics=metrics,
        )
        return {
            "provider_id": config.provider_id,
            "event_id": f"command.{record.command_id}.{phase}.physical",
            "time": dict(time_value),
            "payload_schema_id": PHYSICAL_COMMAND_PROOF_SCHEMA,
            "payload": [
                {"name": "schema_id", "value": PHYSICAL_COMMAND_PROOF_SCHEMA},
                {"name": "command_id", "value": record.command_id},
                {"name": "tool_id", "value": record.tool_id},
                {"name": "phase", "value": phase},
                {"name": "proof_json", "value": _canonical_json(proof).decode("utf-8")},
            ],
        }

    def _pending_sensor_frame_events(
        self,
        *,
        config: PreparedConfig,
        target: tuple[int, int],
    ) -> tuple[dict[str, object], ...]:
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        events: list[dict[str, object]] = []
        for frame in sorted(
            self._pending_public_sensor_frames,
            key=lambda item: str(item["frame_id"]),
        ):
            events.append(
                {
                    "provider_id": config.provider_id,
                    "event_id": PUBLIC_SENSOR_FRAME_EVENT_TYPE,
                    "time": time_value,
                    "payload_schema_id": PUBLIC_SENSOR_FRAME_SCHEMA,
                    "payload": [
                        {"name": name, "value": frame[name]} for name in sorted(frame)
                    ],
                }
            )
        return tuple(events)

    def _artifact_writer(self, artifact_type: str) -> EvidenceWriter:
        try:
            return self._evidence[artifact_type]
        except KeyError as exc:
            raise Px4ServiceError(
                f"PX4 workload omitted the {artifact_type} artifact writer"
            ) from exc

    @staticmethod
    def _require_artifact(
        config: PreparedConfig, artifact_type: str
    ) -> dict[str, str | int | None]:
        matches = [
            requirement
            for requirement in config.artifact_requirements
            if requirement["artifact_type"] == artifact_type
        ]
        if len(matches) != 1:
            raise Px4ServiceError(
                f"PX4 configuration must declare exactly one {artifact_type} artifact"
            )
        return matches[0]

    def _motion_samples(
        self,
        *,
        telemetry: Sequence[VehicleTelemetry],
        target: tuple[int, int],
    ) -> list[dict[str, object]]:
        config = self._config
        if config is None:
            raise Px4ServiceError("PX4 provider has not completed prepare")
        expected_entity_ids = tuple(
            sorted(str(vehicle["vehicle_id"]) for vehicle in config.vehicles)
        )
        by_entity = {item.vehicle_id: item for item in telemetry}
        if (
            len(by_entity) != len(telemetry)
            or tuple(sorted(by_entity)) != expected_entity_ids
        ):
            raise Px4ServiceError(
                "PX4 motion samples do not close over owned dynamic entities"
            )
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        samples: list[dict[str, object]] = []
        for entity_id in expected_entity_ids:
            item = by_entity[entity_id]
            if item.simulation_time_ns != target[1]:
                raise Px4ServiceError("PX4 motion sample telemetry is stale")
            live_pose = _strict_object(
                _parse_json(item.pose_json),
                required=frozenset(
                    {"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}
                ),
                label=f"PX4 Gazebo pose for {entity_id}",
            )
            velocity_ned = _strict_object(
                _parse_json(item.velocity_json),
                required=frozenset({"north_m_s", "east_m_s", "down_m_s"}),
                label=f"PX4 velocity for {entity_id}",
            )
            angular_velocity_body = _strict_object(
                _parse_json(item.angular_velocity_json),
                required=frozenset({"x_rad_s", "y_rad_s", "z_rad_s"}),
                label=f"PX4 angular velocity for {entity_id}",
            )
            north_mps = _finite_number(
                velocity_ned["north_m_s"], label=f"{entity_id}.velocity.north_m_s"
            )
            east_mps = _finite_number(
                velocity_ned["east_m_s"], label=f"{entity_id}.velocity.east_m_s"
            )
            down_mps = _finite_number(
                velocity_ned["down_m_s"], label=f"{entity_id}.velocity.down_m_s"
            )
            angular_x_radps = _finite_number(
                angular_velocity_body["x_rad_s"],
                label=f"{entity_id}.angular_velocity.x_rad_s",
            )
            angular_y_radps = _finite_number(
                angular_velocity_body["y_rad_s"],
                label=f"{entity_id}.angular_velocity.y_rad_s",
            )
            angular_z_radps = _finite_number(
                angular_velocity_body["z_rad_s"],
                label=f"{entity_id}.angular_velocity.z_rad_s",
            )
            contacts = tuple(
                _string(
                    contact,
                    label=f"{entity_id}.classified_contact",
                    pattern=IDENTIFIER,
                )
                for contact in item.contacts
            )
            if contacts != tuple(sorted(set(contacts))):
                raise Px4ServiceError(
                    f"PX4 classified contacts are not canonical for {entity_id}"
                )
            observed_ground_contact = any(
                contact.startswith("ground.") or contact.startswith("launch_pad.")
                for contact in contacts
            )
            observed_collision_contact = any(
                not contact.startswith("ground.")
                and not contact.startswith("launch_pad.")
                for contact in contacts
            )
            if (
                item.ground_contact != observed_ground_contact
                or item.collision_contact != observed_collision_contact
                or item.landed != (item.landed_state == "ON_GROUND")
            ):
                raise Px4ServiceError(
                    f"PX4 contact or landed flags differ from authoritative state for {entity_id}"
                )
            health_flags = _parse_json(item.health)
            health_attributes: list[dict[str, object]] = []
            for name in sorted(health_flags):
                value = health_flags[name]
                if not isinstance(value, bool):
                    raise Px4ServiceError(f"PX4 health flag {name!r} is not a boolean")
                _string(name, label="PX4 health flag", pattern=IDENTIFIER)
                health_attributes.append(
                    {"name": name, "value_type": "bool", "value": value}
                )
            if not health_attributes:
                raise Px4ServiceError("PX4 health state contains no flags")
            sample: dict[str, object] = {
                "schema_version": "aero-bench.state-sample/v1",
                "run_id": config.run_id,
                "scenario_digest": self._workload_identity.scenario_digest,
                "at": time_value,
                "stage": "motion",
                "entity_id": entity_id,
                "provider_id": config.provider_id,
                "sample_kind": "dynamic",
                "pose": self._motion_frame.resolved_pose(live_pose),
                "linear_velocity_enu": {
                    "frame_id": "ENU",
                    "east_mps": east_mps,
                    "north_mps": north_mps,
                    "up_mps": -down_mps,
                },
                "linear_velocity_ned": {
                    "frame_id": "NED",
                    "north_mps": north_mps,
                    "east_mps": east_mps,
                    "down_mps": down_mps,
                },
                "angular_velocity_body": {
                    "frame_id": "body",
                    "x_radps": angular_x_radps,
                    "y_radps": angular_y_radps,
                    "z_radps": angular_z_radps,
                },
                "mode": _string(item.flight_mode, label=f"{entity_id}.flight_mode"),
                "armed": item.armed,
                "battery": {
                    "voltage_v": None,
                    "current_a": None,
                    "remaining_fraction": item.battery_percent / 100.0,
                    "consumed_mah": None,
                    "temperature_c": None,
                    "attributes": [],
                },
                "health": {
                    "healthy": all(
                        bool(attribute["value"]) for attribute in health_attributes
                    ),
                    "attributes": health_attributes,
                },
                "contacts": list(contacts),
                "attributes": [
                    {
                        "name": "collision_contact",
                        "value_type": "bool",
                        "value": item.collision_contact,
                    },
                    {
                        "name": "contacts_complete",
                        "value_type": "bool",
                        "value": True,
                    },
                    {
                        "name": "ground_contact",
                        "value_type": "bool",
                        "value": item.ground_contact,
                    },
                    {
                        "name": "in_air",
                        "value_type": "bool",
                        "value": item.in_air,
                    },
                    {
                        "name": "landed",
                        "value_type": "bool",
                        "value": item.landed,
                    },
                    {
                        "name": "landed_state",
                        "value_type": "str",
                        "value": item.landed_state,
                    },
                    {
                        "name": "telemetry_source",
                        "value_type": "str",
                        "value": "gazebo-mavsdk",
                    },
                ],
            }
            sample["sample_digest"] = _digest_json(sample)
            samples.append(sample)
        return samples

    async def _receipt(
        self,
        *,
        target: tuple[int, int],
        operation: str,
        additional_events: Sequence[Mapping[str, object]] = (),
        telemetry: Sequence[VehicleTelemetry] | None = None,
    ) -> dict[str, object]:
        config = self._config
        if config is None:
            raise Px4ServiceError("PX4 provider has not completed prepare")
        reached_ns = await self._stack.current_sim_time_ns()
        if reached_ns != target[1]:
            raise Px4ServiceError(
                "PX4/Gazebo backend did not reach the requested sim_time_ns"
            )
        if telemetry is None:
            telemetry = await self._stack.telemetry()
        telemetry = tuple(telemetry)
        declared = {vehicle["vehicle_id"] for vehicle in config.vehicles}
        observed = {item.vehicle_id for item in telemetry}
        if observed != declared:
            raise Px4ServiceError(
                f"PX4 state vehicles mismatch: observed={sorted(observed)}"
            )
        events = []
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        for item in telemetry:
            if item.simulation_time_ns != target[1]:
                raise Px4ServiceError(
                    "PX4 state event time differs from the barrier target"
                )
            events.append(
                {
                    "provider_id": config.provider_id,
                    "event_id": f"state.{item.vehicle_id}.{target[0]}",
                    "time": time_value,
                    "payload_schema_id": STATE_SCHEMA,
                    "payload": [
                        {"name": "vehicle_id", "value": item.vehicle_id},
                        {"name": "pose_json", "value": item.pose_json},
                        {
                            "name": "position_wgs84_json",
                            "value": item.position_wgs84_json,
                        },
                        {"name": "velocity_json", "value": item.velocity_json},
                        {
                            "name": "angular_velocity_json",
                            "value": item.angular_velocity_json,
                        },
                        {"name": "attitude_json", "value": item.attitude_json},
                        {"name": "flight_mode", "value": item.flight_mode},
                        {"name": "armed", "value": item.armed},
                        {"name": "in_air", "value": item.in_air},
                        {"name": "landed", "value": item.landed},
                        {"name": "landed_state", "value": item.landed_state},
                        {
                            "name": "contacts_json",
                            "value": _canonical_json(list(item.contacts)).decode(
                                "utf-8"
                            ),
                        },
                        {"name": "ground_contact", "value": item.ground_contact},
                        {"name": "battery_percent", "value": item.battery_percent},
                        {"name": "health", "value": item.health},
                        {"name": "collision_contact", "value": item.collision_contact},
                        {
                            "name": "simulation_time_ns",
                            "value": item.simulation_time_ns,
                        },
                    ],
                }
            )
        snapshot = {
            "sim_time_ns": target[1],
            "vehicles": [item.as_json() for item in telemetry],
        }
        snapshot_digest = _digest_json(snapshot)
        trajectory_requirement = self._require_artifact(config, EVIDENCE_ARTIFACT_TYPE)
        trajectory_writer = self._artifact_writer(EVIDENCE_ARTIFACT_TYPE)
        inspections = config.inspections
        if not inspections:
            record = {
                "schema_version": EVIDENCE_SCHEMA,
                "provider_id": config.provider_id,
                "run_id": config.run_id,
                "operation": operation,
                "tick": target[0],
                "sim_time_ns": target[1],
                "snapshot": snapshot,
                "snapshot_sha256": snapshot_digest,
                "px4_version": PX4_VERSION,
                "px4_commit": PX4_COMMIT,
                "gazebo_version": GAZEBO_VERSION,
                "gazebo_commit": GAZEBO_COMMIT,
                "mavsdk_version": MAVSDK_VERSION,
                "mavsdk_commit": MAVSDK_COMMIT,
                "runtime_image": config.runtime_image,
                "config_digest": config.config_digest,
                "artifact_id": trajectory_requirement["artifact_id"],
                "artifact_type": trajectory_requirement["artifact_type"],
            }
            self._records.append(record)
            evidence_digest = trajectory_writer.write_records(self._records)
        else:
            telemetry_by_vehicle = {item.vehicle_id: item for item in telemetry}
            self._latest_views = {}
            for inspection in inspections:
                camera_vehicle_id = str(inspection["camera_vehicle_id"])
                camera_telemetry = telemetry_by_vehicle.get(camera_vehicle_id)
                if camera_telemetry is None:
                    raise Px4ServiceError(
                        "inspection camera vehicle omitted authoritative telemetry"
                    )
                vehicle_pose = _parse_json(camera_telemetry.pose_json)
                position_wgs84 = _parse_json(camera_telemetry.position_wgs84_json)
                velocity_ned = _parse_json(camera_telemetry.velocity_json)
                distance_m, view_angle_deg, camera_digest, target_digest = (
                    _inspection_geometry(
                        vehicle_pose=vehicle_pose,
                        camera_mount=inspection["camera_mount"],
                        target_position=inspection["target_position"],
                    )
                )
                observation_id = str(inspection["observation_id"])
                view = {
                    "source_provider_id": config.provider_id,
                    "camera_id": inspection["camera_id"],
                    "observation_id": observation_id,
                    "target_id": inspection["target_id"],
                    "time": time_value,
                    "distance_m": distance_m,
                    "view_angle_deg": view_angle_deg,
                    "camera_pose_digest": camera_digest,
                    "target_pose_digest": target_digest,
                    "provider_config_digest": config.config_digest,
                }
                self._latest_views[observation_id] = view
                target_position = inspection["target_position"]
                target_position_wgs84 = inspection["target_position_wgs84"]
                orientation = _rpy_quaternion(vehicle_pose)
                linear_velocity_enu = {
                    "east_mps": _finite_number(
                        velocity_ned["east_m_s"], label="velocity.east_m_s"
                    ),
                    "north_mps": _finite_number(
                        velocity_ned["north_m_s"], label="velocity.north_m_s"
                    ),
                    "up_mps": -_finite_number(
                        velocity_ned["down_m_s"], label="velocity.down_m_s"
                    ),
                }
                distance_to_target_m = _point_distance(vehicle_pose, target_position)
                self._records.append(
                    {
                        "source_artifact_id": trajectory_requirement["artifact_id"],
                        "run_id": config.run_id,
                        "work_order_id": inspection["work_order_id"],
                        "target_id": inspection["target_id"],
                        "vehicle_id": camera_telemetry.vehicle_id,
                        "time": time_value,
                        "position_wgs84": position_wgs84,
                        "target_position_wgs84": target_position_wgs84,
                        "orientation": orientation,
                        "linear_velocity_enu_mps": linear_velocity_enu,
                        "flight_mode": camera_telemetry.flight_mode,
                        "armed": camera_telemetry.armed,
                        "battery_percent": camera_telemetry.battery_percent,
                        "collision_contact": camera_telemetry.collision_contact,
                        "distance_to_target_m": distance_to_target_m,
                    }
                )
            evidence_digest = trajectory_writer.write_array(self._records)
        evidence_path = trajectory_requirement["relative_path"]
        if not isinstance(evidence_path, str):
            raise Px4ServiceError("trajectory artifact path is invalid")
        for event in events:
            if str(event["event_id"]).startswith("public."):
                continue
            event["payload"].extend(
                [
                    {"name": "evidence_path", "value": evidence_path},
                    {"name": "evidence_sha256", "value": evidence_digest},
                ]
            )
        events.extend(dict(event) for event in additional_events)
        return {
            "run_id": config.run_id,
            "provider_id": config.provider_id,
            "reached": time_value,
            "state_digest": snapshot_digest,
            "events": events,
        }

    async def handle(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        # Probe is deliberately side-effect-free and must remain responsive while
        # a long real Gazebo reset or bounded physics transaction holds the command
        # lock.  The readiness contract only asserts that the listener accepts
        # authenticated workload operations; serializing probe behind reset made
        # Docker mark a healthy provider unhealthy under real render load.
        if operation == "probe":
            if payload != {}:
                raise Px4ServiceError("PX4 provider probe request must be empty")
            return {
                "schema_version": PROVIDER_PROBE_SCHEMA,
                "status": "accepting",
                "run_id": self._workload_identity.run_id,
                "provider_id": self._workload_identity.provider_id,
                "adapter": self._workload_identity.adapter,
                "runtime_image": self._workload_identity.runtime_image,
                "config_digest": self._workload_identity.config_digest,
            }
        async with self._lock:
            if self._closed:
                raise Px4ServiceError("PX4 provider service is closed")
            if self._shutdown_started and operation != "shutdown":
                raise Px4ServiceError("PX4 provider shutdown is already in progress")
            try:
                _require_session_token(payload, self._session_token)
                return await self._dispatch(operation, payload)
            except Px4ServiceError as exc:
                if exc.code == "principal.denied":
                    return {"error": {"code": exc.code, "detail": str(exc)}}
                raise ValueError(str(exc)) from exc

    def _airspace_provider_events(
        self,
        *,
        config: PreparedConfig,
        target: tuple[int, int],
    ) -> tuple[dict[str, object], ...]:
        """Convert newly observed Gazebo plugin transitions into public events."""

        if config.airspace_transition is None:
            return ()
        read_transitions = getattr(self._stack, "airspace_transition_events", None)
        if not callable(read_transitions):
            raise Px4ServiceError("urban airspace events require the real plugin stream")
        pending = read_transitions(requested_at=target)
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        events: list[dict[str, object]] = []
        for item in pending:
            transition = str(item["transition"])
            sequence = int(item["sequence"])
            region_id = str(item["region_id"])
            incident_vehicle = str(item["incident_vehicle"])
            vehicle_id = next(
                (
                    str(vehicle["vehicle_id"])
                    for vehicle in config.vehicles
                    if vehicle["gazebo_model_name"] == incident_vehicle
                ),
                None,
            )
            if vehicle_id is None:
                raise Px4ServiceError(
                    "Gazebo airspace transition names an undeclared vehicle"
                )
            transition_record = {
                "schema_version": "aero-bench.gazebo-airspace-transition/v1",
                "vehicle_id": vehicle_id, "region_id": region_id,
                "engine_model": incident_vehicle, "engine_sim_time_ns": item["sim_time_ns"],
                "sequence": sequence, "transition": transition, "source": item["source"],
                "world_sha256": item["world_digest"], "region_sha256": item["region_digest"],
                "position_enu_m": {
                    "x": item["position_enu_m"]["east"],
                    "y": item["position_enu_m"]["north"],
                    "z": item["position_enu_m"]["up"],
                },
            }
            events.append({
                "provider_id": config.provider_id,
                "event_id": f"gazebo.airspace.{transition}", "time": time_value,
                "payload_schema_id": "gazebo.airspace-transition.v1",
                "payload": [
                    {"name": "transition_json", "value": _canonical_json(transition_record).decode("utf-8")},
                    {"name": "logical_origin_engine_ns", "value": item["logical_origin_engine_ns"]},
                ],
            })
            events.append(
                {
                    "provider_id": config.provider_id,
                    "event_id": "public.event",
                    "time": time_value,
                    "payload_schema_id": "public.event.v3",
                    "payload": [
                        {
                            "name": "severity",
                            "value": "critical" if transition == "entered" else "info",
                        },
                        {
                            "name": "message",
                            "value": (
                                "Gazebo airspace transition "
                                f"{transition} sequence {sequence} region {region_id} "
                                f"engine_sim_time_ns {int(item['sim_time_ns'])}"
                            ),
                        },
                        {"name": "entity_id", "value": vehicle_id},
                        {"name": "state", "value": f"airspace.{transition}"},
                        {"name": "evidence", "value": "[]"},
                    ],
                }
            )
        return tuple(events)

    def _observe_flight_sensor(
        self,
        *,
        config: PreparedConfig,
        agent_id: str,
        observation_id: str,
        requested_at: tuple[int, int],
        declaration: Mapping[str, Any],
    ) -> dict[str, object]:
        """Expose only explicitly declared MAVSDK estimates or raw GNSS data."""

        if agent_id != declaration["agent_id"]:
            raise Px4ServiceError("flight observation is owned by another Agent")
        vehicle_id = _string(
            declaration.get("vehicle_id"),
            label="flight observation vehicle_id",
            pattern=IDENTIFIER,
        )
        observation_kind = declaration.get("observation_kind")
        if observation_kind == "telemetry":
            reader = getattr(self._stack, "flight_telemetry_observation", None)
        elif observation_kind == "gnss":
            reader = getattr(self._stack, "flight_gnss_observation", None)
        else:
            raise Px4ServiceError("flight observation kind is unsupported")
        if not callable(reader):
            raise Px4ServiceError(
                f"flight {observation_kind} observation stream is unavailable"
            )
        try:
            payload = reader(vehicle_id, simulation_time_ns=requested_at[1])
        except Exception as exc:
            raise Px4ServiceError(
                f"flight {observation_kind} observation is not ready"
            ) from exc
        if not isinstance(payload, Mapping):
            raise Px4ServiceError("flight observation payload is not an object")
        expected_schema = {
            "telemetry": FLIGHT_TELEMETRY_OBSERVATION_SCHEMA,
            "gnss": FLIGHT_GNSS_OBSERVATION_SCHEMA,
        }[str(observation_kind)]
        payload_document = dict(payload)
        if (
            payload_document.get("schema_version") != expected_schema
            or payload_document.get("vehicle_id") != vehicle_id
            or payload_document.get("simulation_time_ns") != requested_at[1]
        ):
            raise Px4ServiceError(
                "flight observation payload identity or barrier time is invalid"
            )
        envelope = self._urban_observation_envelope(
            config=config,
            agent_id=agent_id,
            observation_id=observation_id,
            requested_at=requested_at,
            schema_file=declaration["schema_file"],
            payload=payload_document,
        )
        self._observation_envelopes[
            (agent_id, observation_id, requested_at[0])
        ] = envelope
        return {"observation": envelope}

    async def _observe_urban_telemetry(
        self,
        *,
        config: PreparedConfig,
        agent_id: str,
        observation_id: str,
        requested_at: tuple[int, int],
        declaration: Mapping[str, Any],
    ) -> dict[str, object]:
        """Expose one barrier-closed MAVSDK/Gazebo telemetry sample to its UAV Agent."""

        vehicle_id = _string(
            declaration.get("vehicle_id"),
            label="urban recovery telemetry vehicle_id",
            pattern=IDENTIFIER,
        )
        telemetry = tuple(await self._stack.telemetry())
        matches = tuple(item for item in telemetry if item.vehicle_id == vehicle_id)
        if len(matches) != 1:
            raise Px4ServiceError(
                "urban recovery telemetry must contain exactly one declared vehicle"
            )
        sample = matches[0]
        if sample.simulation_time_ns != requested_at[1]:
            raise Px4ServiceError(
                "urban recovery telemetry sample time differs from the barrier"
            )
        payload = {
            "schema_version": URBAN_TELEMETRY_OBSERVATION_SCHEMA,
            "vehicle_id": vehicle_id,
            "simulation_time_ns": sample.simulation_time_ns,
            "pose_json": sample.pose_json,
            "position_wgs84_json": sample.position_wgs84_json,
            "velocity_json": sample.velocity_json,
            "angular_velocity_json": sample.angular_velocity_json,
            "attitude_json": sample.attitude_json,
            "flight_mode": sample.flight_mode,
            "armed": sample.armed,
            "in_air": sample.in_air,
            "landed": sample.landed,
            "landed_state": sample.landed_state,
            "battery_percent": sample.battery_percent,
            "health": sample.health,
            "contacts_json": _canonical_json(list(sample.contacts)).decode("utf-8"),
            "ground_contact": sample.ground_contact,
            "collision_contact": sample.collision_contact,
        }
        return {
            "observation": self._urban_observation_envelope(
                config=config,
                agent_id=agent_id,
                observation_id=observation_id,
                requested_at=requested_at,
                schema_file=declaration["schema_file"],
                payload=payload,
            )
        }

    async def _observe_urban_safety(
        self,
        *,
        config: PreparedConfig,
        agent_id: str,
        observation_id: str,
        requested_at: tuple[int, int],
        declaration: Mapping[str, Any],
    ) -> dict[str, object]:
        """Expose only plugin-confirmed airspace state; never infer it locally."""

        provider = getattr(self._stack, "airspace_observation", None)
        if not callable(provider):
            raise Px4ServiceError(
                "urban recovery safety observation requires the Gazebo airspace plugin stream"
            )
        try:
            payload = provider(
                vehicle_id=declaration["vehicle_id"],
                requested_at=requested_at,
            )
        except Exception as exc:
            raise Px4ServiceError(
                "urban recovery safety observation has no authoritative plugin sample"
            ) from exc
        if not isinstance(payload, Mapping):
            raise Px4ServiceError("Gazebo airspace plugin returned a non-object sample")
        payload = dict(payload)
        payload.setdefault("schema_version", URBAN_SAFETY_OBSERVATION_SCHEMA)
        if payload.get("schema_version") != URBAN_SAFETY_OBSERVATION_SCHEMA:
            raise Px4ServiceError("Gazebo airspace plugin safety schema is invalid")
        required_fields = {
            "schema_version",
            "vehicle_id",
            "region_id",
            "airspace_state",
            "transition",
            "transition_sequence",
            "transition_engine_sim_time_ns",
            "engine_sim_time_ns",
            "logical_origin_engine_ns",
            "simulation_time_ns",
            "source",
            "world_sha256",
            "region_sha256",
            "position_enu_m",
        }
        if set(payload) != required_fields or payload.get("vehicle_id") != declaration["vehicle_id"]:
            raise Px4ServiceError(
                "Gazebo airspace plugin safety sample fields are incomplete"
            )
        if payload.get("simulation_time_ns") != requested_at[1]:
            raise Px4ServiceError(
                "Gazebo airspace plugin safety sample time differs from barrier"
            )
        # NamedValue is deliberately scalar on the Gateway wire. Preserve the
        # structured ENU position as canonical JSON rather than letting the
        # envelope constructor coerce or discard provider truth.
        payload["position_enu_m"] = _canonical_json(payload["position_enu_m"]).decode("utf-8")
        return {
            "observation": self._urban_observation_envelope(
                config=config,
                agent_id=agent_id,
                observation_id=observation_id,
                requested_at=requested_at,
                schema_file=declaration["schema_file"],
                payload=payload,
            )
        }

    @staticmethod
    def _urban_observation_envelope(
        *,
        config: PreparedConfig,
        agent_id: str,
        observation_id: str,
        requested_at: tuple[int, int],
        schema_file: Mapping[str, object],
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        expected_time = {"tick": requested_at[0], "sim_time_ns": requested_at[1]}
        payload_document = dict(payload)
        return {
            "run_id": config.run_id,
            "agent_id": agent_id,
            "observation_id": observation_id,
            "time": expected_time,
            "payload_schema": dict(schema_file),
            "payload": [
                {"name": name, "value": value}
                for name, value in payload_document.items()
            ],
            "payload_digest": _digest_json(payload_document),
        }

    def _observe_urban_camera(
        self,
        *,
        config: PreparedConfig,
        agent_id: str,
        observation_id: str,
        requested_at: tuple[int, int],
        declaration: Mapping[str, Any],
    ) -> dict[str, object]:
        """Return one real Gazebo RGB frame for an authorized UAV camera."""

        if agent_id != declaration["agent_id"]:
            raise Px4ServiceError(
                "urban recovery camera observation is owned by another Agent"
            )
        camera_id = str(declaration["camera_id"])
        vehicle_id = str(declaration["camera_vehicle_id"])
        camera_frame = getattr(self._stack, "camera_frame", None)
        if not callable(camera_frame):
            raise Px4ServiceError(
                "urban recovery observation requires the authoritative Gazebo camera stream"
            )
        try:
            frame = camera_frame(camera_id)
        except Exception as exc:
            raise Px4ServiceError(
                "urban recovery observation has no captured Gazebo RGB frame"
            ) from exc
        expected_engine_time_ns = self._stack._time_origin_ns + requested_at[1]
        if (
            frame.vehicle_id != vehicle_id
            or frame.camera_id != camera_id
            or frame.width != int(declaration["resolution_width_px"])
            or frame.height != int(declaration["resolution_height_px"])
            or frame.pixel_format != "RGB_INT8"
            or frame.engine_sim_time_ns != expected_engine_time_ns
        ):
            raise Px4ServiceError(
                "urban recovery Gazebo RGB frame identity differs from its declaration"
            )
        vehicle = next(
            (
                item
                for item in config.vehicles
                if str(item["vehicle_id"]) == vehicle_id
            ),
            None,
        )
        if vehicle is None:
            raise Px4ServiceError(
                "urban recovery camera vehicle is not declared by PX4"
            )
        pose = self._latest_poses.get(str(vehicle["gazebo_model_name"]))
        if pose is None:
            raise Px4ServiceError(
                "urban recovery RGB frame has no matching Gazebo capture pose"
            )
        image_sha256 = _digest_bytes(frame.png_bytes)
        pose_sha256 = _digest_json(
            {"camera_id": camera_id, "vehicle_id": vehicle_id, "pose": pose}
        )
        intrinsics_sha256 = _digest_json(
            {
                "camera_id": camera_id,
                "width": frame.width,
                "height": frame.height,
                "horizontal_fov_deg": declaration.get("horizontal_fov_deg"),
                "vertical_fov_deg": declaration.get("vertical_fov_deg"),
            }
        )
        frame_id = f"frame.{agent_id}.{observation_id}.{requested_at[0]:016d}"
        selector = f"frames/{frame_id}"
        public_payload = {
            "schema_version": RGB_OBSERVATION_SCHEMA,
            "camera_id": camera_id,
            "visual_kind": "gazebo_rgb",
            "visual_status": "captured",
            "frame_id": frame_id,
            "vehicle_id": vehicle_id,
            "engine_sim_time_ns": frame.engine_sim_time_ns,
            "logical_origin_engine_ns": self._stack._time_origin_ns,
            "pose_sha256": pose_sha256,
            "intrinsics_sha256": intrinsics_sha256,
            "image_sha256": image_sha256,
            "size_bytes": len(frame.png_bytes),
            "selector": selector,
            "width": frame.width,
            "height": frame.height,
            "media_type": "image/png",
            "source": "gazebo.camera",
        }
        payload_digest = _digest_json(public_payload)
        observation_requirement = self._require_artifact(
            config, OBSERVATION_ARTIFACT_TYPE
        )
        sensor_frame_requirement = self._require_artifact(
            config, SENSOR_FRAME_ARTIFACT_TYPE
        )
        if observation_requirement["visibility"] != "private":
            raise Px4ServiceError("PX4 verifier observation artifact must be private")
        if sensor_frame_requirement["visibility"] != "public":
            raise Px4ServiceError("PX4 sensor-frame artifact must be public")
        frame_data_requirement = self._require_artifact(
            config, CAMERA_FRAME_DATA_ARTIFACT_TYPE
        )
        if frame_data_requirement["visibility"] != "public":
            raise Px4ServiceError("PX4 camera frame-data artifact must be public")
        self._artifact_writer(CAMERA_FRAME_DATA_ARTIFACT_TYPE).append(
            _camera_frame_archive_record(frame_id, frame.png_bytes)
        )
        expected_time = {"tick": requested_at[0], "sim_time_ns": requested_at[1]}
        metadata = {
            "source_artifact_id": observation_requirement["artifact_id"],
            "run_id": config.run_id,
            "observation_id": observation_id,
            "time": expected_time,
            "camera_id": camera_id,
            "vehicle_id": vehicle_id,
            "engine_sim_time_ns": frame.engine_sim_time_ns,
            "logical_origin_engine_ns": self._stack._time_origin_ns,
            "pose_sha256": pose_sha256,
            "intrinsics_sha256": intrinsics_sha256,
            "image_sha256": image_sha256,
            "size_bytes": len(frame.png_bytes),
            "selector": selector,
            "width": frame.width,
            "height": frame.height,
            "media_type": "image/png",
            "source": "gazebo.camera",
            "payload_digest": payload_digest,
            "visual_kind": "gazebo_rgb",
            "visual_status": "captured",
        }
        public_frame = {
            "schema_version": PUBLIC_SENSOR_FRAME_ARTIFACT_SCHEMA,
            "source_artifact_id": sensor_frame_requirement["artifact_id"],
            "run_id": config.run_id,
            "frame_id": frame_id,
            "selector": selector,
            "observation_id": observation_id,
            "time": expected_time,
            "camera_id": camera_id,
            "vehicle_id": vehicle_id,
            "engine_sim_time_ns": frame.engine_sim_time_ns,
            "logical_origin_engine_ns": self._stack._time_origin_ns,
            "pose_sha256": pose_sha256,
            "intrinsics_sha256": intrinsics_sha256,
            "image_sha256": image_sha256,
            "size_bytes": len(frame.png_bytes),
            "width": frame.width,
            "height": frame.height,
            "media_type": "image/png",
            "source": "gazebo.camera",
            "payload_digest": payload_digest,
        }
        self._observation_records.append(metadata)
        self._artifact_writer(OBSERVATION_ARTIFACT_TYPE).write_array(
            self._observation_records
        )
        self._public_sensor_frame_records.append(public_frame)
        self._artifact_writer(SENSOR_FRAME_ARTIFACT_TYPE).write_array(
            self._public_sensor_frame_records
        )
        pending_frame = {
            "artifact_id": frame_data_requirement["artifact_id"],
            "frame_id": frame_id,
            "observation_id": observation_id,
            "payload_digest": payload_digest,
            "selector": selector,
            "camera_id": camera_id,
            "engine_sim_time_ns": frame.engine_sim_time_ns,
            "logical_origin_engine_ns": self._stack._time_origin_ns,
            "height": frame.height,
            "image_sha256": image_sha256,
            "intrinsics_sha256": intrinsics_sha256,
            "media_type": "image/png",
            "pose_sha256": pose_sha256,
            "size_bytes": len(frame.png_bytes),
            "source": "gazebo.camera",
            "vehicle_id": vehicle_id,
            "width": frame.width,
        }
        self._pending_public_sensor_frames.append(pending_frame)
        envelope: dict[str, object] = {
            "run_id": config.run_id,
            "agent_id": agent_id,
            "observation_id": observation_id,
            "time": expected_time,
            "payload_schema": declaration["schema_file"],
            "payload": [
                {"name": name, "value": value}
                for name, value in public_payload.items()
            ],
            "payload_digest": payload_digest,
        }
        self._observation_envelopes[(agent_id, observation_id, requested_at[0])] = envelope
        return {"observation": envelope}

    async def _dispatch(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._finalization is not None and operation not in {"finalize", "shutdown"}:
            raise Px4ServiceError("PX4 provider is already finalized")
        if operation == "prepare":
            if self._config is not None:
                raise Px4ServiceError("PX4 provider prepare called twice")
            config = self._parse_prepare(payload)
            await self._stack.verify_identities(config)
            self._config = config
            return {
                "status": "ready",
                "provider_id": config.provider_id,
                "protocol_version": config.protocol_version,
                "runtime_image": config.runtime_image,
                "scenario_digest": self._workload_identity.scenario_digest,
                "px4_version": PX4_VERSION,
                "px4_commit": PX4_COMMIT,
                "gazebo_version": GAZEBO_VERSION,
                "gazebo_commit": GAZEBO_COMMIT,
                "mavsdk_version": MAVSDK_VERSION,
                "mavsdk_commit": MAVSDK_COMMIT,
            }
        if operation == "reset":
            config = self._require_identity(payload)
            raw = _strict_object(
                payload,
                required=frozenset({"provider_id", "run_id", "seed", "session_token"}),
                label="PX4 reset request",
            )
            seed = _integer(raw["seed"], label="seed")
            if seed != self._workload_identity.seed:
                raise Px4ServiceError("reset seed does not match AERO_BENCH_SEED")
            started_at = time.monotonic()
            _startup_progress("rpc-reset.begin", started_at=started_at)
            await self._stack.reset(config, seed=seed)
            _startup_progress("rpc-reset.stack-complete", started_at=started_at)
            self._lifecycle = None
            self._transition_lifecycle(ProviderLifecycle.BARRIER_PAUSED)
            self._command_records_by_vehicle = {}
            self._command_record = None
            self._command_ids = set()
            for writer in self._evidence.values():
                writer.reset()
            self._records = []
            self._observation_records = []
            self._public_sensor_frame_records = []
            self._pending_public_sensor_frames = []
            self._observation_envelopes = {}
            self._latest_views = {}
            if config.inspections or config.urban_camera_observations:
                self._artifact_writer(EVIDENCE_ARTIFACT_TYPE).write_array(())
                self._artifact_writer(OBSERVATION_ARTIFACT_TYPE).write_array(())
                self._artifact_writer(SENSOR_FRAME_ARTIFACT_TYPE).write_array(())
                if config.inspections or config.urban_camera_observations:
                    self._artifact_writer(CAMERA_FRAME_DATA_ARTIFACT_TYPE).reset()
            self._current = (0, 0)
            receipt = await self._receipt(target=(0, 0), operation="reset")
            _startup_progress("rpc-reset.complete", started_at=started_at)
            return {"receipt": receipt}
        if operation == "step_stage":
            config = self._require_identity(payload)
            raw = _strict_object(
                payload,
                required=frozenset(
                    {"provider_id", "run_id", "request", "session_token"}
                ),
                label="PX4 step_stage request",
            )
            request = _strict_object(
                raw["request"],
                required=frozenset(
                    {
                        "schema_version",
                        "run_id",
                        "scenario_digest",
                        "provider_id",
                        "target",
                        "stage",
                    }
                ),
                label="PX4 MotionStageRequest",
            )
            if request["schema_version"] != "aero-bench.provider-stage-request/v1":
                raise Px4ServiceError("PX4 motion stage request schema is unsupported")
            if request["run_id"] != config.run_id:
                raise Px4ServiceError("PX4 motion stage run identity mismatch")
            if request["scenario_digest"] != self._workload_identity.scenario_digest:
                raise Px4ServiceError("PX4 motion stage scenario identity mismatch")
            if request["provider_id"] != config.provider_id:
                raise Px4ServiceError("PX4 motion stage provider identity mismatch")
            if request["stage"] != "motion":
                raise Px4ServiceError("PX4 accepts only the motion stage")
            target_raw = _strict_object(
                request["target"],
                required=frozenset({"tick", "sim_time_ns"}),
                label="PX4 motion stage target",
            )
            target = (
                _integer(target_raw["tick"], label="target.tick", minimum=0),
                _integer(
                    target_raw["sim_time_ns"], label="target.sim_time_ns", minimum=0
                ),
            )
            if self._current is None:
                raise Px4ServiceError("PX4 reset must precede step_stage")
            expected = (
                self._current[0] + 1,
                self._current[1] + config.step_length_ns,
            )
            if target != expected:
                raise Px4ServiceError(
                    "PX4 motion stage must advance exactly one configured step_length_ns"
                )
            current_ns = await self._stack.current_sim_time_ns()
            if current_ns != self._current[1]:
                raise Px4ServiceError("PX4 backend time differs before motion staging")
            observed_step = await self._stack.observed_physics_step_ns()
            if observed_step != config.physics_step_ns:
                raise Px4ServiceError("Gazebo physics step changed after prepare")
            iterations = physics_step_count(
                current_ns=current_ns,
                target_ns=target[1],
                physics_step_ns=config.physics_step_ns,
            )
            command_events = await self._advance_with_pending_commands(
                config=config,
                iterations=iterations,
                target=target,
            )
            if config.airspace_transition is not None:
                wait_for_airspace = getattr(self._stack, "wait_for_airspace", None)
                if not callable(wait_for_airspace):
                    raise Px4ServiceError("urban airspace barrier has no plugin stream closure")
                await wait_for_airspace(requested_at=target)
            airspace_events = self._airspace_provider_events(
                config=config,
                target=target,
            )
            sensor_frame_events = self._pending_sensor_frame_events(
                config=config,
                target=target,
            )
            telemetry = tuple(await self._stack.telemetry())
            motion_samples = self._motion_samples(
                telemetry=telemetry,
                target=target,
            )
            receipt = await self._receipt(
                target=target,
                operation="step_stage",
                additional_events=(*command_events, *sensor_frame_events, *airspace_events),
                telemetry=telemetry,
            )
            self._pending_public_sensor_frames = []
            self._current = target
            return {
                "schema_version": "aero-bench.px4-motion-stage-response/v1",
                "run_id": config.run_id,
                "scenario_digest": self._workload_identity.scenario_digest,
                "provider_id": config.provider_id,
                "target": {"tick": target[0], "sim_time_ns": target[1]},
                "stage": "motion",
                "receipt": receipt,
                "samples": motion_samples,
            }
        if operation == "command":
            config = self._require_identity(payload)
            if self._current is None:
                raise Px4ServiceError("PX4 reset must precede command")
            raw = _strict_object(
                payload,
                required=frozenset(
                    {"provider_id", "run_id", "request", "session_token"}
                ),
                label="PX4 command request",
            )
            request = raw["request"]
            if not isinstance(request, dict):
                raise Px4ServiceError("PX4 command request is invalid")
            if request.get("run_id") != config.run_id:
                raise Px4ServiceError("PX4 command request belongs to a different run")
            phases = ["received"]
            detail = None
            try:
                if self._lifecycle not in {
                    ProviderLifecycle.BARRIER_PAUSED,
                    ProviderLifecycle.ACTION_STAGED,
                }:
                    observed = (
                        self._lifecycle.value
                        if self._lifecycle is not None
                        else "UNINITIALIZED"
                    )
                    raise Px4CommandPhaseError(
                        last_success="received",
                        detail=(
                            "PX4 actions may be validated and staged only at "
                            f"BARRIER_PAUSED or ACTION_STAGED; observed {observed}"
                        ),
                    )
                await self._stack.accept_command(request)
                command_id = _string(
                    request.get("command_id"),
                    label="PX4 command_id",
                    pattern=IDENTIFIER,
                )
                if command_id in self._command_ids:
                    raise Px4CommandPhaseError(
                        last_success="received",
                        detail="PX4 command_id was already consumed",
                    )
                vehicle_id = _require_vehicle_id(request, config.vehicles)
                blocking_record = self._command_records_by_vehicle.get(vehicle_id)
                if blocking_record is not None and (
                    blocking_record.stage in {"staged", "applied"}
                    or blocking_record.failure_latched
                ):
                    raise Px4CommandPhaseError(
                        last_success="received",
                        detail=(
                            "PX4 already has a staged, applied, or failure-latched "
                            f"command for {vehicle_id}"
                        ),
                    )
                if vehicle_id in self._command_records_by_vehicle:
                    raise Px4CommandPhaseError(
                        last_success="received",
                        detail=f"PX4 command record for {vehicle_id} is not reusable",
                    )
                target = self._current
                if target is None:
                    raise Px4ServiceError("PX4 current barrier is missing")
                arguments = _command_argument_map(request)
                baseline_state = await self._fresh_physical_state(
                    vehicle_id=vehicle_id,
                    target=target,
                )
                command_spec = self._command_audit_spec(
                    _string(request.get("tool_id"), label="PX4 tool_id")
                )
                self._command_ids.add(command_id)
                record = UnresolvedCommandRecord(
                    command_id=command_id,
                    tool_id=_string(request.get("tool_id"), label="PX4 tool_id"),
                    vehicle_id=vehicle_id,
                    arguments=arguments,
                    expected_mav_cmd=int(command_spec["mav_cmd"]),
                    expected_wire_type=_string(
                        command_spec["wire_type"], label="command_audit.wire_type"
                    ),
                    stage="staged",
                    accepted_at=target,
                    applied_at=None,
                    baseline_state=baseline_state,
                    hold_anchor=dict(baseline_state.pose),
                    deadline_sim_time_ns=(
                        baseline_state.sim_time_ns
                        + int(
                            config.physical_completion_policy["physical_sim_timeout_ns"]
                        )
                    ),
                    settle_start=None,
                    settle_end=None,
                    settle_count=0,
                    last_state_digest=None,
                    last_metrics={},
                    failure_latched=False,
                    command_audit_status=None,
                    command_audit=None,
                )
                self._command_records_by_vehicle[vehicle_id] = record
                self._sync_command_record_view()
                if self._lifecycle == ProviderLifecycle.BARRIER_PAUSED:
                    self._transition_lifecycle(ProviderLifecycle.ACTION_STAGED)
                phases.append("accepted")
            except Px4CommandPhaseError as exc:
                detail = exc.detail
                phases.append("failed")
            except Px4ServiceError as exc:
                detail = str(exc)
                phases.append("failed")
            time_value = {"tick": self._current[0], "sim_time_ns": self._current[1]}
            receipts = [
                {
                    "run_id": config.run_id,
                    "command_id": request.get("command_id"),
                    "provider_id": config.provider_id,
                    "phase": phase,
                    "time": time_value,
                    "detail": detail if phase == "failed" else None,
                }
                for phase in phases
            ]
            return {"receipts": receipts, "events": []}
        if operation == "observe":
            config = self._require_identity(payload)
            self._require_lifecycle(
                ProviderLifecycle.BARRIER_PAUSED,
                ProviderLifecycle.ACTION_STAGED,
            )
            inspections = config.inspections
            urban_observations = config.urban_camera_observations
            flight_observations = config.flight_observations
            if not inspections and not urban_observations and not flight_observations:
                raise Px4ServiceError(
                    "PX4 provider has no configured camera or flight observation"
                )
            raw = _strict_object(
                payload,
                required=frozenset(
                    {
                        "provider_id",
                        "run_id",
                        "agent_id",
                        "observation_id",
                        "requested_at",
                        "session_token",
                    }
                ),
                label="PX4 observation request",
            )
            agent_id = _string(
                raw["agent_id"], label="observation agent_id", pattern=IDENTIFIER
            )
            observation_id = _string(
                raw["observation_id"],
                label="observation_id",
                pattern=IDENTIFIER,
            )
            inspection = next(
                (
                    item
                    for item in inspections
                    if item["observation_id"] == observation_id
                ),
                None,
            )
            urban_observation = next(
                (
                    item
                    for item in urban_observations
                    if item["observation_id"] == observation_id
                ),
                None,
            )
            flight_observation = next(
                (
                    item
                    for item in flight_observations
                    if item["observation_id"] == observation_id
                ),
                None,
            )
            if (
                inspection is None
                and urban_observation is None
                and flight_observation is None
            ):
                raise Px4ServiceError("PX4 observation identity is undeclared")
            requested = _strict_object(
                raw["requested_at"],
                required=frozenset({"tick", "sim_time_ns"}),
                label="PX4 observation requested_at",
            )
            requested_at = (
                _integer(requested["tick"], label="requested_at.tick", minimum=0),
                _integer(
                    requested["sim_time_ns"],
                    label="requested_at.sim_time_ns",
                    minimum=0,
                ),
            )
            if self._current is None or requested_at != self._current:
                raise Px4ServiceError(
                    "PX4 observation time must equal the current provider barrier"
                )
            observation_key = (agent_id, observation_id, requested_at[0])
            cached = self._observation_envelopes.get(observation_key)
            if cached is not None:
                cached_time = cached.get("time")
                if cached_time != {
                    "tick": requested_at[0],
                    "sim_time_ns": requested_at[1],
                }:
                    raise Px4ServiceError(
                        "PX4 observation was already captured at another barrier"
                    )
                return {"observation": cached}
            if urban_observation is not None:
                observation_kind = urban_observation["observation_kind"]
                if observation_kind == "camera":
                    return self._observe_urban_camera(
                        config=config,
                        agent_id=agent_id,
                        observation_id=observation_id,
                        requested_at=requested_at,
                        declaration=urban_observation,
                    )
                if observation_kind == "telemetry":
                    return await self._observe_urban_telemetry(
                        config=config,
                        agent_id=agent_id,
                        observation_id=observation_id,
                        requested_at=requested_at,
                        declaration=urban_observation,
                    )
                if observation_kind == "safety":
                    return await self._observe_urban_safety(
                        config=config,
                        agent_id=agent_id,
                        observation_id=observation_id,
                        requested_at=requested_at,
                        declaration=urban_observation,
                    )
                raise Px4ServiceError(
                    "urban recovery observation kind is unsupported"
                )
            if flight_observation is not None:
                return self._observe_flight_sensor(
                    config=config,
                    agent_id=agent_id,
                    observation_id=observation_id,
                    requested_at=requested_at,
                    declaration=flight_observation,
                )
            view = self._latest_views.get(observation_id)
            expected_time = {
                "tick": requested_at[0],
                "sim_time_ns": requested_at[1],
            }
            if view is None or view.get("time") != expected_time:
                raise Px4ServiceError(
                    "PX4 has no authoritative camera geometry at this barrier"
                )
            failed_conditions: list[str] = []
            distance_m = _finite_number(view.get("distance_m"), label="view.distance_m")
            view_angle_deg = _finite_number(
                view.get("view_angle_deg"), label="view.view_angle_deg"
            )
            if distance_m < inspection["min_distance_m"]:
                failed_conditions.append("distance_below_minimum")
            if distance_m > inspection["max_distance_m"]:
                failed_conditions.append("distance_above_maximum")
            if view_angle_deg < inspection["min_view_angle_deg"]:
                failed_conditions.append("view_angle_below_minimum")
            if view_angle_deg > inspection["max_view_angle_deg"]:
                failed_conditions.append("view_angle_above_maximum")
            if not (
                inspection["earliest_time_ns"]
                <= requested_at[1]
                <= inspection["latest_time_ns"]
            ):
                failed_conditions.append("time_window")
            if failed_conditions:
                raise Px4ServiceError(
                    "PX4 observation trigger is not eligible: "
                    + ",".join(failed_conditions)
                )
            if agent_id != inspection["agent_id"]:
                raise Px4ServiceError(
                    "inspection camera observation is owned by another Agent"
                )
            camera_frame = getattr(self._stack, "camera_frame", None)
            if not callable(camera_frame):
                raise Px4ServiceError(
                    "inspection requires the authoritative Gazebo camera stream"
                )
            try:
                captured_frame = camera_frame(str(inspection["camera_id"]))
            except Exception as exc:
                raise Px4ServiceError(
                    "inspection observation has no captured Gazebo RGB frame"
                ) from exc
            expected_engine_time_ns = self._stack._time_origin_ns + requested_at[1]
            if (
                captured_frame.vehicle_id != inspection["camera_vehicle_id"]
                or captured_frame.camera_id != inspection["camera_id"]
                or captured_frame.width != inspection["resolution_width_px"]
                or captured_frame.height != inspection["resolution_height_px"]
                or captured_frame.pixel_format != "RGB_INT8"
                or captured_frame.engine_sim_time_ns != expected_engine_time_ns
            ):
                raise Px4ServiceError(
                    "inspection Gazebo RGB frame identity or time differs from its declaration"
                )
            frame_id = f"frame.{agent_id}.{observation_id}.{requested_at[0]:016d}"
            selector = f"frames/{frame_id}"
            image_sha256 = _digest_bytes(captured_frame.png_bytes)
            camera_pose_sha256 = _sha256(
                view["camera_pose_digest"], label="view.camera_pose_digest"
            )
            target_pose_sha256 = _sha256(
                view["target_pose_digest"], label="view.target_pose_digest"
            )
            public_payload = {
                "schema_version": RGB_OBSERVATION_SCHEMA,
                "target_id": inspection["target_id"],
                "camera_id": inspection["camera_id"],
                "distance_m": distance_m,
                "view_angle_deg": view_angle_deg,
                "visual_kind": "gazebo_rgb",
                "visual_status": "captured",
                "frame_id": frame_id,
                "vehicle_id": captured_frame.vehicle_id,
                "engine_sim_time_ns": captured_frame.engine_sim_time_ns,
                "logical_origin_engine_ns": self._stack._time_origin_ns,
                "capture_pose_source": "gazebo.pose.private_digest",
                "camera_pose_sha256": camera_pose_sha256,
                "target_pose_sha256": target_pose_sha256,
                "image_sha256": image_sha256,
                "size_bytes": len(captured_frame.png_bytes),
                "selector": selector,
                "width": captured_frame.width,
                "height": captured_frame.height,
                "media_type": "image/png",
                "source": "gazebo.camera",
                "image_base64": base64.b64encode(captured_frame.png_bytes).decode(
                    "ascii"
                ),
            }
            payload_digest = _digest_json(public_payload)
            observation_requirement = self._require_artifact(
                config, OBSERVATION_ARTIFACT_TYPE
            )
            sensor_frame_requirement = self._require_artifact(
                config, SENSOR_FRAME_ARTIFACT_TYPE
            )
            if observation_requirement["visibility"] != "private":
                raise Px4ServiceError(
                    "PX4 verifier observation artifact must be private"
                )
            if sensor_frame_requirement["visibility"] != "public":
                raise Px4ServiceError("PX4 sensor-frame artifact must be public")
            frame_data_requirement = self._require_artifact(
                config, CAMERA_FRAME_DATA_ARTIFACT_TYPE
            )
            if frame_data_requirement["visibility"] != "public":
                raise Px4ServiceError("PX4 camera frame-data artifact must be public")
            self._artifact_writer(CAMERA_FRAME_DATA_ARTIFACT_TYPE).append(
                _camera_frame_archive_record(frame_id, captured_frame.png_bytes)
            )
            metadata = {
                "schema_version": INSPECTION_OBSERVATION_METADATA_SCHEMA,
                "source_artifact_id": observation_requirement["artifact_id"],
                "run_id": config.run_id,
                "work_order_id": inspection["work_order_id"],
                "observation_id": observation_id,
                "target_id": inspection["target_id"],
                "time": expected_time,
                "frame_id": frame_id,
                "camera_id": inspection["camera_id"],
                "payload_digest": payload_digest,
                "view": dict(view),
                "visual_kind": "gazebo_rgb",
                "visual_status": "captured",
                "vehicle_id": captured_frame.vehicle_id,
                "engine_sim_time_ns": captured_frame.engine_sim_time_ns,
                "logical_origin_engine_ns": self._stack._time_origin_ns,
                "capture_pose_source": "gazebo.pose.private_digest",
                "camera_pose_sha256": camera_pose_sha256,
                "target_pose_sha256": target_pose_sha256,
                "image_sha256": image_sha256,
                "size_bytes": len(captured_frame.png_bytes),
                "selector": selector,
                "width": captured_frame.width,
                "height": captured_frame.height,
                "media_type": "image/png",
                "source": "gazebo.camera",
            }
            public_frame = {
                "schema_version": PUBLIC_SENSOR_FRAME_ARTIFACT_SCHEMA,
                "source_artifact_id": sensor_frame_requirement["artifact_id"],
                "run_id": config.run_id,
                "frame_id": frame_id,
                "selector": selector,
                "observation_id": observation_id,
                "time": expected_time,
                "camera_id": inspection["camera_id"],
                "payload_digest": payload_digest,
                "vehicle_id": captured_frame.vehicle_id,
                "engine_sim_time_ns": captured_frame.engine_sim_time_ns,
                "logical_origin_engine_ns": self._stack._time_origin_ns,
                "capture_pose_source": "gazebo.pose.private_digest",
                "camera_pose_sha256": camera_pose_sha256,
                "target_pose_sha256": target_pose_sha256,
                "image_sha256": image_sha256,
                "size_bytes": len(captured_frame.png_bytes),
                "width": captured_frame.width,
                "height": captured_frame.height,
                "media_type": "image/png",
                "source": "gazebo.camera",
            }
            self._observation_records.append(metadata)
            self._artifact_writer(OBSERVATION_ARTIFACT_TYPE).write_array(
                self._observation_records
            )
            self._public_sensor_frame_records.append(public_frame)
            self._artifact_writer(SENSOR_FRAME_ARTIFACT_TYPE).write_array(
                self._public_sensor_frame_records
            )
            pending_frame = {
                "artifact_id": frame_data_requirement["artifact_id"],
                "frame_id": frame_id,
                "observation_id": observation_id,
                "payload_digest": payload_digest,
                "selector": selector,
                "vehicle_id": captured_frame.vehicle_id,
                "camera_id": captured_frame.camera_id,
                "engine_sim_time_ns": captured_frame.engine_sim_time_ns,
                "logical_origin_engine_ns": self._stack._time_origin_ns,
                "capture_pose_source": "gazebo.pose.private_digest",
                "camera_pose_sha256": camera_pose_sha256,
                "target_pose_sha256": target_pose_sha256,
                "image_sha256": image_sha256,
                "size_bytes": len(captured_frame.png_bytes),
                "width": captured_frame.width,
                "height": captured_frame.height,
                "media_type": "image/png",
                "source": "gazebo.camera",
            }
            self._pending_public_sensor_frames.append(pending_frame)
            envelope: dict[str, object] = {
                "run_id": config.run_id,
                "agent_id": agent_id,
                "observation_id": observation_id,
                "time": expected_time,
                "payload_schema": inspection["metadata_schema"],
                "payload": [
                    {"name": name, "value": value}
                    for name, value in public_payload.items()
                ],
                "payload_digest": payload_digest,
            }
            self._observation_envelopes[observation_key] = envelope
            return {"observation": envelope}
        if operation == "finalize":
            config = self._require_identity(payload)
            self._require_lifecycle(ProviderLifecycle.BARRIER_PAUSED)
            raw = _strict_object(
                payload,
                required=frozenset(
                    {"provider_id", "run_id", "request", "session_token"}
                ),
                label="PX4 finalize request",
            )
            request = _strict_object(
                raw["request"],
                required=frozenset(
                    {
                        "schema_version",
                        "run_id",
                        "terminal_event",
                        "terminal_time",
                        "event_chain_root",
                    }
                ),
                label="PX4 ProviderFinalizationRequest",
            )
            terminal_time = _strict_object(
                request["terminal_time"],
                required=frozenset({"tick", "sim_time_ns"}),
                label="PX4 finalization terminal_time",
            )
            expected_time = (
                _integer(terminal_time["tick"], label="terminal_time.tick", minimum=0),
                _integer(
                    terminal_time["sim_time_ns"],
                    label="terminal_time.sim_time_ns",
                    minimum=0,
                ),
            )
            root = _sha256(request["event_chain_root"], label="event_chain_root")
            if (
                request["schema_version"]
                != "aero-bench.provider-finalization-request/v1"
                or request["run_id"] != config.run_id
                or request["terminal_event"] != "run.completed"
                or expected_time != self._current
            ):
                raise Px4ServiceError("PX4 finalization identity or time mismatch")
            if self._pending_public_sensor_frames:
                raise Px4ServiceError(
                    "PX4 cannot finalize before captured public sensor frames are staged"
                )
            if any(
                record.stage in {"staged", "applied"} or record.failure_latched
                for record in self._command_records_by_vehicle.values()
            ):
                raise Px4ServiceError(
                    "PX4 cannot finalize with a staged, applied, or failure-latched command"
                )
            if config.inspections or config.urban_camera_observations:
                _validate_camera_frame_archive(
                    self._artifact_writer(CAMERA_FRAME_DATA_ARTIFACT_TYPE).path,
                    self._public_sensor_frame_records,
                )
            if self._finalization is not None:
                if self._finalization["request"] != request:
                    raise Px4ServiceError("PX4 finalization root cannot be changed")
                receipt = self._finalization["receipt"]
                if not isinstance(receipt, dict):
                    raise Px4ServiceError("PX4 cached finalization receipt is invalid")
                return {"receipt": receipt}
            finalized_artifacts: list[dict[str, object]] = []
            for requirement in config.artifact_requirements:
                artifact_type = str(requirement["artifact_type"])
                content = self._artifact_writer(artifact_type).path.read_bytes()
                finalized_artifacts.append(
                    {
                        "artifact_id": requirement["artifact_id"],
                        "sha256": _digest_bytes(content),
                        "size_bytes": len(content),
                    }
                )
            receipt = {
                "schema_version": "aero-bench.provider-finalization-receipt/v1",
                "run_id": config.run_id,
                "provider_id": config.provider_id,
                "event_chain_root": root,
                "artifacts": finalized_artifacts,
            }
            self._finalization = {"request": dict(request), "receipt": receipt}
            return {"receipt": receipt}
        if operation == "snapshot":
            config = self._require_identity(payload)
            self._require_lifecycle(ProviderLifecycle.BARRIER_PAUSED)
            _strict_object(
                payload,
                required=frozenset({"provider_id", "run_id", "session_token"}),
                label="PX4 snapshot request",
            )
            if self._current is None:
                raise Px4ServiceError("PX4 reset must precede snapshot")
            snapshot = await self._stack.snapshot()
            if snapshot.get("sim_time_ns") != self._current[1]:
                raise Px4ServiceError(
                    "PX4 snapshot time moved outside the provider barrier"
                )
            digest = _digest_json(dict(snapshot))
            if not config.inspections:
                trajectory_requirement = self._require_artifact(
                    config, EVIDENCE_ARTIFACT_TYPE
                )
                record = {
                    "schema_version": EVIDENCE_SCHEMA,
                    "provider_id": config.provider_id,
                    "run_id": config.run_id,
                    "operation": "snapshot",
                    "tick": self._current[0],
                    "sim_time_ns": self._current[1],
                    "snapshot": dict(snapshot),
                    "snapshot_sha256": digest,
                    "px4_version": PX4_VERSION,
                    "px4_commit": PX4_COMMIT,
                    "gazebo_version": GAZEBO_VERSION,
                    "gazebo_commit": GAZEBO_COMMIT,
                    "mavsdk_version": MAVSDK_VERSION,
                    "mavsdk_commit": MAVSDK_COMMIT,
                    "runtime_image": config.runtime_image,
                    "config_digest": config.config_digest,
                    "artifact_id": trajectory_requirement["artifact_id"],
                    "artifact_type": trajectory_requirement["artifact_type"],
                }
                self._records.append(record)
                self._artifact_writer(EVIDENCE_ARTIFACT_TYPE).write_records(
                    self._records
                )
            return {"snapshot_digest": digest}
        if operation == "shutdown":
            self._require_identity(payload)
            _strict_object(
                payload,
                required=frozenset({"provider_id", "run_id", "session_token"}),
                label="PX4 shutdown request",
            )
            await self._stack.stop()
            self._shutdown_started = True
            self._shutdown_requested.set()
            return {"status": "stopped"}
        raise Px4ServiceError(f"PX4 operation is not supported: {operation}")


async def serve(service: Px4GazeboService, *, host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    rpc_server = JsonLineRpcServer(service.handle)
    handle = await rpc_server.start(host=host, port=port)
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, service.request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await service.shutdown_requested.wait()
    finally:
        await handle.graceful_close()
        await service.close_runtime()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(signum)
            except (NotImplementedError, RuntimeError):
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AERO-BENCH PX4/Gazebo/MAVSDK provider"
    )
    parser.add_argument("mode", nargs="+", help="provider serve")
    return parser


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise SystemExit(f"{name} is required")
    return value


def _environment_port(name: str) -> int:
    value = _required_environment(name)
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer port") from exc
    if not 1024 <= port <= 65535:
        raise SystemExit(f"{name} must be between 1024 and 65535")
    return port


def _environment_seed(name: str) -> int:
    value = _required_environment(name)
    try:
        seed = int(value, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    return seed


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.mode != ["provider", "serve"]:
        raise SystemExit("service mode must be 'provider serve'")
    bind_host = _string(
        _required_environment("AERO_BENCH_PROVIDER_BIND_HOST"),
        label="AERO_BENCH_PROVIDER_BIND_HOST",
        pattern=HOST,
    )
    bind_port = _environment_port("AERO_BENCH_PROVIDER_PORT")
    bundle_root = Path(_required_environment("AERO_BENCH_BUNDLE_DIR"))
    artifact_root = Path(_required_environment("AERO_BENCH_ARTIFACT_DIR"))
    contract_path = Path(_required_environment("AERO_BENCH_CONTRACT"))
    expected_run_id = _sha256(
        _required_environment("AERO_BENCH_RUN_ID"),
        label="AERO_BENCH_RUN_ID",
    )
    expected_provider_id = _string(
        _required_environment("AERO_BENCH_WORKLOAD_ID"),
        label="AERO_BENCH_WORKLOAD_ID",
        pattern=IDENTIFIER,
    )
    expected_seed = _environment_seed("AERO_BENCH_SEED")
    session_token = _sha256(
        _required_environment("AERO_BENCH_PROVIDER_TOKEN"),
        label="AERO_BENCH_PROVIDER_TOKEN",
    )
    tmp_dir = Path(_required_environment("TMPDIR"))
    workload_identity = _load_workload_identity(
        contract_path=contract_path,
        bundle_root=bundle_root,
        expected_run_id=expected_run_id,
        expected_provider_id=expected_provider_id,
        expected_provider_port=bind_port,
        expected_seed=expected_seed,
    )
    service = Px4GazeboService(
        artifact_root=artifact_root,
        rpc_port=bind_port,
        workload_identity=workload_identity,
        session_token=session_token,
        tmp_dir=tmp_dir,
    )
    asyncio.run(serve(service, host=bind_host, port=bind_port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
