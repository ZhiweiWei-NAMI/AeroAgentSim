from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from aero_bench.config.models import ArtifactRequirement, ClockSpec, Sha256, StrictModel
from aero_bench.gateway.contracts import ObservationEnvelope
from aero_bench.providers.contracts import (
    ProviderCommandResult,
    ProviderManifest,
    ProviderSession,
)
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.providers.px4_gazebo.observations import (
    FLIGHT_GNSS_OBSERVATION_SCHEMA,
    FLIGHT_TELEMETRY_OBSERVATION_SCHEMA,
    RGB_OBSERVATION_SCHEMA,
    FlightGnssObservation,
    FlightTelemetryObservation,
    InspectionRgbObservation,
)
from aero_bench.providers.px4_gazebo.protocol import JsonLineTransport, Px4Transport
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    FinalizedArtifact,
    MotionStageRequest,
    MotionStageResult,
    ProviderEvent,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    ProviderStageRequest,
    SceneContribution,
    SimulationTime,
    StateSample,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    step_receipt_digest_value,
)
from aero_bench.world import frame_math
from aero_bench.world.resolved import ResolvedScenario


class Px4ProviderError(RuntimeError):
    pass


class Px4ProviderNotReady(Px4ProviderError):
    pass


@dataclass
class _DeferredCommandState:
    """Per-vehicle lifecycle state; vehicles may advance independently."""

    command_id: str
    tool_id: str
    vehicle_id: str
    stage: Literal["accepted", "applied"]
    accepted_at: SimulationTime
    applied_at: SimulationTime | None
    audit_digest: str | None


class Px4Readiness(StrictModel):
    status: Literal["ready"]
    provider_id: str
    protocol_version: str
    runtime_image: str
    scenario_digest: Sha256
    px4_version: str
    px4_commit: str
    gazebo_version: str
    gazebo_commit: str
    mavsdk_version: str
    mavsdk_commit: str


class Px4SnapshotResponse(StrictModel):
    snapshot_digest: Sha256


class Px4MotionStageResponse(StrictModel):
    schema_version: Literal["aero-bench.px4-motion-stage-response/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: str
    target: SimulationTime
    stage: Literal["motion"]
    receipt: StepReceipt
    samples: tuple[StateSample, ...]


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX_BYTES = re.compile(r"^(?:[0-9a-f]{2})*$")
PROTOCOL_VERSION = "aero-bench.px4-gazebo-rpc/v4"
TRAJECTORY_ARTIFACT_TYPE = "trajectory"
OBSERVATION_ARTIFACT_TYPE = "observation"
SENSOR_FRAME_ARTIFACT_TYPE = "sensor-frame"
CAMERA_FRAME_DATA_ARTIFACT_TYPE = "camera-frame-data"
PUBLIC_SENSOR_FRAME_EVENT_TYPE = "public.sensor-frame"
PUBLIC_SENSOR_FRAME_SCHEMA = "sensor.frame_ref.public.v2"
EVIDENCE_ARTIFACT_TYPE = TRAJECTORY_ARTIFACT_TYPE
COMMAND_AUDIT_APPLIED_STATUS = "decoded_command_ack_accepted"
COMMAND_AUDIT_FAILED_STATUS = "decoded_command_ack_nonaccepted"
COMMAND_AUDIT_MISSING_STATUS = "decoded_command_ack_missing"
COMMAND_AUDIT_CORRUPT_STATUS = "decoded_command_audit_corrupt"
COMMAND_HORIZON_INTERRUPTED_DETAIL = (
    "PX4 command interrupted at the configured run horizon before physical completion"
)
COMMAND_AUDIT_SCHEMA_VERSION = "aero-bench.px4-decoded-command-audit/v2"
COMMAND_AUDIT_TRANSPORT_KIND = "command_transport_accept"
COMMAND_AUDIT_ACK_INGRESS_KIND = "command_ack_ingress"
COMMAND_AUDIT_ACK_DISPOSITION_KIND = "command_ack_disposition"
COMMAND_AUDIT_MATCHED_DISPOSITION = "matched_outstanding_command"
COMMAND_AUDIT_UNMATCHED_DISPOSITION = "unmatched_no_outstanding_command"
COMMAND_AUDIT_TARGET_FILTERED_DISPOSITION = "target_filtered_before_match"
COMMAND_AUDIT_INTERNAL_ERROR_DISPOSITION = "matcher_internal_error"
COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY = (
    "mavsdk_impl.deliver_message.connection_send_return"
)
COMMAND_TRANSPORT_FRAME_ENCODING = "mavlink_msg_to_send_buffer_from_packed_message"
COMMAND_ACK_CAPTURE_BOUNDARY = (
    "mavlink_command_sender.receive_command_ack.decoded_handler"
)
COMMAND_ACK_FRAME_ENCODING = "mavlink_msg_to_send_buffer_from_decoded_message"
MAVSDK_SERVER_SYSTEM_ID = 245
MAVSDK_SERVER_COMPONENT_ID = 190
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
UINT8_MAX = 255
UINT16_MAX = 65_535
UINT24_MAX = (1 << 24) - 1
UINT64_MAX = (1 << 64) - 1
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
PHYSICAL_PROOF_FIELDS = frozenset(
    {
        "command_id",
        "tool_id",
        "vehicle_id",
        "phase",
        "policy_schema_version",
        "baseline_tick",
        "baseline_sim_time_ns",
        "baseline_state_digest",
        "current_tick",
        "current_sim_time_ns",
        "current_state_digest",
        "accepted_tick",
        "accepted_sim_time_ns",
        "applied_tick",
        "applied_sim_time_ns",
        "ack_audit_status",
        "decoded_command_audit",
        "thresholds",
        "metrics",
        "settling",
    }
)
DECODED_COMMAND_AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "vehicle_system_id",
        "expected_mav_cmd",
        "expected_wire_type",
        "records",
        "record_sha256s",
        "journal_sha256",
        "terminal_ack_ingress_record",
        "terminal_ack_ingress_record_sha256",
        "terminal_ack_disposition_record",
        "terminal_ack_disposition_record_sha256",
    }
)
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


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


@dataclass(frozen=True, slots=True)
class ValidatedDecodedCommandAudit:
    bundle_digest: str | None
    terminal_seq: int | None


class Px4GazeboProvider(ProviderSession):
    """PX4 SITL + modern Gazebo + MAVSDK provider RPC client.

    All state and command receipts are returned by the external backend. The
    client deliberately has no kinematic state, telemetry generator, or local
    command-success shortcut.

    Every frame, including the first ``prepare``, presents the executor-issued
    run-scoped session token. The workload pins the same token from
    ``AERO_BENCH_PROVIDER_TOKEN`` and rejects any peer that cannot present it.
    """

    _STATE_SCHEMA = "px4.state.v1"
    _COMMAND_SCHEMA = "px4.command.v2"
    _COMMAND_PHYSICAL_SCHEMA = "px4.command.physical.v3"
    _REQUIRED_STATE_FIELDS = frozenset(
        {
            "vehicle_id",
            "pose_json",
            "position_wgs84_json",
            "velocity_json",
            "angular_velocity_json",
            "attitude_json",
            "flight_mode",
            "armed",
            "in_air",
            "landed",
            "landed_state",
            "contacts_json",
            "ground_contact",
            "battery_percent",
            "health",
            "collision_contact",
            "simulation_time_ns",
            "evidence_path",
            "evidence_sha256",
        }
    )

    def __init__(
        self,
        *,
        config: Px4GazeboConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        session_token: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ):
        if manifest.provider_id != config.provider_id:
            raise ValueError("PX4 manifest/provider configuration IDs differ")
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise TypeError("PX4 runtime_endpoint must be a RuntimeEndpoint")
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("PX4 scenario must be a ResolvedScenario")
        if not isinstance(clock, ClockSpec):
            raise TypeError("PX4 clock must be a ClockSpec")
        scenario_provider = next(
            (
                item
                for item in scenario.providers
                if item.provider_id == config.provider_id
            ),
            None,
        )
        if scenario_provider is None:
            raise ValueError("PX4 provider is absent from ResolvedScenario")
        configured_vehicle_ids = tuple(
            sorted(vehicle.vehicle_id for vehicle in config.vehicles)
        )
        scenario_vehicle_ids = tuple(
            sorted(
                entity.entity_id
                for entity in scenario.entities
                if entity.owner_kind == "provider"
                and entity.owner_id == config.provider_id
                and entity.kind == "uav"
            )
        )
        if configured_vehicle_ids != scenario_vehicle_ids:
            raise ValueError(
                "PX4 vehicle logical IDs differ from ResolvedScenario UAV ownership"
            )
        if clock.step_ns % config.physics_step_ns != 0:
            raise ValueError("run clock step_ns must be a multiple of physics_step_ns")
        earliest_completion_offset_ns = (
            2
            + max(
                config.physical_completion_policy.min_settle_samples - 1,
                (
                    config.physical_completion_policy.settle_duration_ns
                    + clock.step_ns
                    - 1
                )
                // clock.step_ns,
            )
        ) * clock.step_ns
        if (
            earliest_completion_offset_ns
            >= config.physical_completion_policy.physical_sim_timeout_ns
        ):
            raise ValueError(
                "PX4 physical completion timeout cannot contain the earliest "
                "scenario-clock completion barrier"
            )
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError("PX4 run_id must be a SHA-256 digest") from error
        if validated_run_id == "0" * 64:
            raise ValueError("PX4 run_id cannot be a placeholder digest")
        try:
            validated_token = TypeAdapter(Sha256).validate_python(session_token)
        except (TypeError, ValueError) as error:
            raise ValueError("PX4 session_token must be a SHA-256 digest") from error
        if validated_token == "0" * 64:
            raise ValueError("PX4 session_token cannot be a placeholder digest")
        self._config = config
        self._scenario = scenario
        self._clock = clock
        self._manifest = manifest
        self._runtime_endpoint = runtime_endpoint
        self._run_id = validated_run_id
        self._session_token = validated_token
        self._transport: Px4Transport | None = None
        self._prepared = False
        self._last_time: SimulationTime | None = None
        self._unresolved_command_id: str | None = None
        self._unresolved_command_tool_id: str | None = None
        self._unresolved_command_vehicle_id: str | None = None
        self._unresolved_command_stage: Literal["accepted", "applied"] | None = None
        self._unresolved_command_accepted_at: SimulationTime | None = None
        self._unresolved_command_applied_at: SimulationTime | None = None
        self._unresolved_command_audit_digest: str | None = None
        self._pending_commands_by_vehicle: dict[str, _DeferredCommandState] = {}
        self._failure_latched_by_vehicle: set[str] = set()
        self._deferred_failure_latched = False
        self._consumed_terminal_ack_seq_by_vehicle: dict[str, int] = {}
        self._pending_public_sensor_frames: dict[str, dict[str, object]] = {}
        self._observed_public_sensor_frames: dict[
            tuple[str, str, int], dict[str, object]
        ] = {}

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    @staticmethod
    async def connect_runtime(endpoint: RuntimeEndpoint) -> Px4Transport:
        """Connect to the materializer-owned endpoint for a production session."""

        return await JsonLineTransport.connect(endpoint)

    async def _request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._transport is None:
            self._transport = await self.connect_runtime(self._runtime_endpoint)
        return await self._transport.request(
            operation, {**dict(payload), "session_token": self._session_token}
        )

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise Px4ProviderNotReady("PX4 provider has not completed readiness")

    def _clear_command_tracking(self, *, clear_failure_latch: bool) -> None:
        self._unresolved_command_id = None
        self._unresolved_command_tool_id = None
        self._unresolved_command_vehicle_id = None
        self._unresolved_command_stage = None
        self._unresolved_command_accepted_at = None
        self._unresolved_command_applied_at = None
        self._unresolved_command_audit_digest = None
        if clear_failure_latch:
            self._deferred_failure_latched = False

    def _capture_active_command(self, *, vehicle_id: str) -> _DeferredCommandState:
        if (
            self._unresolved_command_id is None
            or self._unresolved_command_tool_id is None
            or self._unresolved_command_vehicle_id != vehicle_id
            or self._unresolved_command_stage is None
            or self._unresolved_command_accepted_at is None
        ):
            raise Px4ProviderError("PX4 active deferred command state is incomplete")
        return _DeferredCommandState(
            command_id=self._unresolved_command_id,
            tool_id=self._unresolved_command_tool_id,
            vehicle_id=vehicle_id,
            stage=self._unresolved_command_stage,
            accepted_at=self._unresolved_command_accepted_at,
            applied_at=self._unresolved_command_applied_at,
            audit_digest=self._unresolved_command_audit_digest,
        )

    def _restore_command(self, state: _DeferredCommandState) -> None:
        self._unresolved_command_id = state.command_id
        self._unresolved_command_tool_id = state.tool_id
        self._unresolved_command_vehicle_id = state.vehicle_id
        self._unresolved_command_stage = state.stage
        self._unresolved_command_accepted_at = state.accepted_at
        self._unresolved_command_applied_at = state.applied_at
        self._unresolved_command_audit_digest = state.audit_digest
        self._deferred_failure_latched = state.vehicle_id in self._failure_latched_by_vehicle

    def _sync_command_compatibility_view(self) -> None:
        """Keep legacy private diagnostics representative without global authority."""
        if len(self._pending_commands_by_vehicle) == 1:
            state = next(iter(self._pending_commands_by_vehicle.values()))
            self._restore_command(state)
            return
        self._clear_command_tracking(clear_failure_latch=False)
        self._deferred_failure_latched = len(self._failure_latched_by_vehicle) == 1

    def _artifact_requirements(self) -> list[dict[str, object]]:
        requirements = self._manifest.artifact_requirements
        expected_types = {TRAJECTORY_ARTIFACT_TYPE}
        task = getattr(self._scenario, "task", None)
        urban_recovery = getattr(task, "package_id", None) == (
            "urban.uav-recovery-demo.v1"
        )
        has_observation = any(
            binding.endpoint_id == self._config.provider_id
            and (binding.inspection is not None or binding.urban is not None)
            for binding in self._scenario.task.observations
        )
        has_inspection_observation = any(
            binding.endpoint_id == self._config.provider_id
            and binding.inspection is not None
            for binding in self._scenario.task.observations
        )
        if has_observation:
            expected_types.update(
                {OBSERVATION_ARTIFACT_TYPE, SENSOR_FRAME_ARTIFACT_TYPE}
            )
        if has_inspection_observation or (urban_recovery and has_observation):
            expected_types.add(CAMERA_FRAME_DATA_ARTIFACT_TYPE)
        if len(requirements) != len(expected_types):
            raise Px4ProviderError(
                "PX4 provider artifact inventory does not match its configured capabilities"
            )
        observed_types: set[str] = set()
        for requirement in requirements:
            if not isinstance(requirement, ArtifactRequirement):
                raise Px4ProviderError("PX4 artifact requirement is not strict")
            visibility_is_valid = (
                requirement.artifact_type == TRAJECTORY_ARTIFACT_TYPE
                or (
                    requirement.artifact_type == OBSERVATION_ARTIFACT_TYPE
                    and requirement.visibility == "private"
                )
                or (
                    requirement.artifact_type == SENSOR_FRAME_ARTIFACT_TYPE
                    and requirement.visibility == "public"
                )
                or (
                    requirement.artifact_type == CAMERA_FRAME_DATA_ARTIFACT_TYPE
                    and requirement.visibility == "public"
                )
            )
            if (
                requirement.artifact_type not in expected_types
                or requirement.producer_id != self._config.provider_id
                or requirement.source_asset_id is not None
                or not visibility_is_valid
            ):
                raise Px4ProviderError(
                    "PX4 trajectory may be public or private, verifier observation "
                    "evidence must be private, public sensor frames and camera frame "
                    "data must be public, and source_asset_id must be null"
                )
            observed_types.add(requirement.artifact_type)
        if observed_types != expected_types:
            raise Px4ProviderError("PX4 evidence artifact types are incomplete")
        return [item.model_dump(mode="json") for item in requirements]

    def _config_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self._config.provider_id,
            "run_id": self._run_id,
            "protocol_version": PROTOCOL_VERSION,
            "runtime_image": self._manifest.runtime_image,
            "config_digest": self._manifest.config_digest,
            "scenario_digest": self._scenario.scenario_digest,
            "artifact_requirements": self._artifact_requirements(),
            "endpoint": self._runtime_endpoint.model_dump(mode="json"),
            "world_input": (
                self._config.world_input.model_dump(mode="json")
                if self._config.world_input is not None
                else None
            ),
        }

    async def prepare(self) -> None:
        if self._prepared:
            raise Px4ProviderError("PX4 provider prepare called twice")
        response = await self._request("prepare", self._config_payload())
        try:
            readiness = Px4Readiness.model_validate(response)
        except ValidationError as exc:
            raise Px4ProviderError(
                "PX4 provider readiness response is invalid"
            ) from exc
        if readiness.status != "ready":
            raise Px4ProviderError(
                f"PX4 provider did not become ready: {readiness.status!r}"
            )
        if readiness.provider_id != self._config.provider_id:
            raise Px4ProviderError(
                "PX4 readiness provider_id differs from the declaration"
            )
        if readiness.protocol_version != PROTOCOL_VERSION:
            raise Px4ProviderError("PX4 provider protocol version mismatch")
        if readiness.runtime_image != self._manifest.runtime_image:
            raise Px4ProviderError("PX4 provider image identity mismatch")
        if readiness.scenario_digest != self._scenario.scenario_digest:
            raise Px4ProviderError("PX4 provider scenario identity mismatch")
        expected = (
            (readiness.px4_version, readiness.px4_commit, self._config.px4),
            (readiness.gazebo_version, readiness.gazebo_commit, self._config.gazebo),
            (readiness.mavsdk_version, readiness.mavsdk_commit, self._config.mavsdk),
        )
        for version, commit, declared in expected:
            if version != declared.version or commit != declared.commit:
                raise Px4ProviderError(
                    "PX4/Gazebo/MAVSDK backend identity does not match configuration"
                )
            if not _COMMIT.fullmatch(commit):
                raise Px4ProviderError(
                    "backend commit identity must be a full 40-character SHA"
                )
        self._prepared = True

    @staticmethod
    def _payload_map(event: ProviderEvent, *, label: str) -> dict[str, Any]:
        payload = {item.name: item.value for item in event.payload}
        if len(payload) != len(event.payload):
            raise Px4ProviderError(f"{label} contains duplicate payload fields")
        return payload

    @staticmethod
    def _parse_canonical_json_object(
        value: object,
        *,
        label: str,
    ) -> Mapping[str, Any]:
        if not isinstance(value, str):
            raise Px4ProviderError(f"{label} must be a JSON string")
        try:
            decoded = json.loads(
                value,
                object_pairs_hook=_without_duplicate_keys,
                parse_constant=_reject_non_json_constant,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise Px4ProviderError(f"{label} is invalid") from exc
        if not isinstance(decoded, dict):
            raise Px4ProviderError(f"{label} must decode to an object")
        if value != _canonical_json_bytes(decoded).decode("utf-8"):
            raise Px4ProviderError(f"{label} is not canonical JSON")
        return decoded

    @staticmethod
    def _parse_canonical_json_array(
        value: object,
        *,
        label: str,
    ) -> tuple[Any, ...]:
        if not isinstance(value, str):
            raise Px4ProviderError(f"{label} must be a JSON string")
        try:
            decoded = json.loads(
                value,
                object_pairs_hook=_without_duplicate_keys,
                parse_constant=_reject_non_json_constant,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise Px4ProviderError(f"{label} is invalid") from exc
        if not isinstance(decoded, list):
            raise Px4ProviderError(f"{label} must decode to an array")
        if value != _canonical_json_bytes(decoded).decode("utf-8"):
            raise Px4ProviderError(f"{label} is not canonical JSON")
        return tuple(decoded)

    @staticmethod
    def _parse_proof_json(value: object) -> Mapping[str, Any]:
        if not isinstance(value, str):
            raise Px4ProviderError("PX4 physical proof payload must be a JSON string")
        try:
            decoded = json.loads(
                value,
                object_pairs_hook=_without_duplicate_keys,
                parse_constant=_reject_non_json_constant,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise Px4ProviderError("PX4 physical proof payload is invalid") from exc
        if not isinstance(decoded, dict):
            raise Px4ProviderError(
                "PX4 physical proof payload must decode to an object"
            )
        if value != _canonical_json_bytes(decoded).decode("utf-8"):
            raise Px4ProviderError("PX4 physical proof payload is not canonical JSON")
        return decoded

    @staticmethod
    def _require_finite_number(value: object, *, label: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise Px4ProviderError(f"{label} must be a number")
        result = float(value)
        if not math.isfinite(result):
            raise Px4ProviderError(f"{label} must be finite")
        return result

    @staticmethod
    def _require_int(value: object, *, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise Px4ProviderError(f"{label} must be an integer")
        return value

    @staticmethod
    def _require_bounded_int(
        value: object,
        *,
        label: str,
        minimum: int,
        maximum: int,
    ) -> int:
        result = Px4GazeboProvider._require_int(value, label=label)
        if result < minimum or result > maximum:
            raise Px4ProviderError(f"{label} must be within [{minimum}, {maximum}]")
        return result

    @staticmethod
    def _require_optional_int(value: object, *, label: str) -> int | None:
        if value is None:
            return None
        return Px4GazeboProvider._require_int(value, label=label)

    @staticmethod
    def _require_real_sha256(value: object, *, label: str) -> str:
        try:
            digest = TypeAdapter(Sha256).validate_python(value)
        except ValidationError as exc:
            raise Px4ProviderError(f"{label} must be a SHA-256 digest") from exc
        if digest == "0" * 64:
            raise Px4ProviderError(f"{label} cannot be a placeholder digest")
        return digest

    @staticmethod
    def _require_string(
        value: object, *, label: str, allowed: frozenset[str] | None = None
    ) -> str:
        if not isinstance(value, str) or not value:
            raise Px4ProviderError(f"{label} must be a non-empty string")
        if allowed is not None and value not in allowed:
            raise Px4ProviderError(f"{label} has an invalid value")
        return value

    @staticmethod
    def _require_object(
        value: object, *, label: str, required: frozenset[str]
    ) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise Px4ProviderError(f"{label} must be a JSON object")
        actual = set(value)
        missing = required - actual
        extra = actual - required
        if missing or extra:
            raise Px4ProviderError(
                f"{label} fields are not exact: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        return value

    @staticmethod
    def _require_list(value: object, *, label: str) -> list[Any]:
        if not isinstance(value, list):
            raise Px4ProviderError(f"{label} must be a JSON array")
        return value

    @staticmethod
    def _command_audit_spec(tool_id: str) -> Mapping[str, object]:
        try:
            return TOOL_COMMAND_AUDIT_SPEC[tool_id]
        except KeyError as exc:
            raise Px4ProviderError(f"unsupported PX4 tool_id {tool_id!r}") from exc

    def _vehicle_system_id(self, vehicle_id: str) -> int:
        matches = [
            vehicle
            for vehicle in self._config.vehicles
            if vehicle.vehicle_id == vehicle_id
        ]
        if len(matches) != 1:
            raise Px4ProviderError(
                "PX4 deferred command vehicle declaration is missing"
            )
        return matches[0].system_id

    @staticmethod
    def _audit_hex_bytes(
        value: object,
        *,
        label: str,
        allow_empty: bool,
    ) -> bytes:
        if (
            not isinstance(value, str)
            or (not allow_empty and not value)
            or _HEX_BYTES.fullmatch(value) is None
        ):
            raise Px4ProviderError(f"{label} must be canonical lowercase byte hex")
        return bytes.fromhex(value)

    @staticmethod
    def _mavlink_x25_checksum(payload: bytes, *, crc_extra: int) -> int:
        checksum = 0xFFFF
        for value in (*payload, crc_extra):
            temporary = value ^ (checksum & 0xFF)
            temporary ^= (temporary << 4) & 0xFF
            checksum = (
                (checksum >> 8) ^ (temporary << 8) ^ (temporary << 3) ^ (temporary >> 4)
            ) & UINT16_MAX
        return checksum

    def _validate_audit_frame(
        self,
        record: Mapping[str, Any],
        *,
        label: str,
        expected_message_id: int,
        expected_frame_encoding: str,
    ) -> bytes:
        frame = self._audit_hex_bytes(
            record.get("canonical_frame_hex"),
            label=f"{label}.canonical_frame_hex",
            allow_empty=False,
        )
        frame_length = self._require_bounded_int(
            record.get("canonical_frame_length"),
            label=f"{label}.canonical_frame_length",
            minimum=1,
            maximum=280,
        )
        if frame_length != len(frame):
            raise Px4ProviderError(f"{label}.canonical_frame_length is inconsistent")
        payload = self._audit_hex_bytes(
            record.get("payload_hex"),
            label=f"{label}.payload_hex",
            allow_empty=True,
        )
        signature = self._audit_hex_bytes(
            record.get("signature_hex"),
            label=f"{label}.signature_hex",
            allow_empty=True,
        )
        payload_length = self._require_bounded_int(
            record.get("payload_length"),
            label=f"{label}.payload_length",
            minimum=0,
            maximum=UINT8_MAX,
        )
        if payload_length != len(payload):
            raise Px4ProviderError(f"{label}.payload_length is inconsistent")
        wire_version = self._require_bounded_int(
            record.get("wire_version"),
            label=f"{label}.wire_version",
            minimum=1,
            maximum=2,
        )
        magic = self._require_bounded_int(
            record.get("magic"),
            label=f"{label}.magic",
            minimum=0,
            maximum=UINT8_MAX,
        )
        incompat_flags = self._require_bounded_int(
            record.get("incompat_flags"),
            label=f"{label}.incompat_flags",
            minimum=0,
            maximum=UINT8_MAX,
        )
        compat_flags = self._require_bounded_int(
            record.get("compat_flags"),
            label=f"{label}.compat_flags",
            minimum=0,
            maximum=UINT8_MAX,
        )
        packet_sequence = self._require_bounded_int(
            record.get("packet_sequence"),
            label=f"{label}.packet_sequence",
            minimum=0,
            maximum=UINT8_MAX,
        )
        source_system_id = self._require_bounded_int(
            record.get("source_system_id"),
            label=f"{label}.source_system_id",
            minimum=0,
            maximum=UINT8_MAX,
        )
        source_component_id = self._require_bounded_int(
            record.get("source_component_id"),
            label=f"{label}.source_component_id",
            minimum=0,
            maximum=UINT8_MAX,
        )
        message_id = self._require_bounded_int(
            record.get("message_id"),
            label=f"{label}.message_id",
            minimum=0,
            maximum=UINT24_MAX,
        )
        checksum = self._require_bounded_int(
            record.get("checksum"),
            label=f"{label}.checksum",
            minimum=0,
            maximum=UINT16_MAX,
        )
        signed_frame = record.get("signed_frame")
        if not isinstance(signed_frame, bool):
            raise Px4ProviderError(f"{label}.signed_frame must be a boolean")
        if (
            self._require_string(
                record.get("frame_encoding"), label=f"{label}.frame_encoding"
            )
            != expected_frame_encoding
        ):
            raise Px4ProviderError(f"{label}.frame_encoding is invalid")

        if wire_version == 1:
            if magic != MAVLINK_V1_MAGIC or incompat_flags != 0 or compat_flags != 0:
                raise Px4ProviderError(f"{label} MAVLink v1 header identity is invalid")
            if signed_frame or signature:
                raise Px4ProviderError(
                    f"{label} MAVLink v1 signature identity is invalid"
                )
            payload_offset = 6
            if len(frame) != payload_offset + payload_length + 2:
                raise Px4ProviderError(f"{label} MAVLink v1 frame length is invalid")
            header_message_id = frame[5]
            if (
                frame[0] != magic
                or frame[1] != payload_length
                or frame[2] != packet_sequence
                or frame[3] != source_system_id
                or frame[4] != source_component_id
            ):
                raise Px4ProviderError(
                    f"{label} MAVLink v1 header bytes are inconsistent"
                )
        else:
            if magic != MAVLINK_V2_MAGIC:
                raise Px4ProviderError(f"{label} MAVLink v2 magic is invalid")
            expected_signed = bool(incompat_flags & MAVLINK_IFLAG_SIGNED)
            if signed_frame is not expected_signed:
                raise Px4ProviderError(
                    f"{label} MAVLink v2 signing flag is inconsistent"
                )
            if len(signature) != (MAVLINK_SIGNATURE_LENGTH if expected_signed else 0):
                raise Px4ProviderError(
                    f"{label} MAVLink v2 signature length is invalid"
                )
            payload_offset = 10
            expected_frame_length = (
                payload_offset
                + payload_length
                + 2
                + (MAVLINK_SIGNATURE_LENGTH if expected_signed else 0)
            )
            if len(frame) != expected_frame_length:
                raise Px4ProviderError(f"{label} MAVLink v2 frame length is invalid")
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
                raise Px4ProviderError(
                    f"{label} MAVLink v2 header bytes are inconsistent"
                )

        if message_id != expected_message_id or header_message_id != message_id:
            raise Px4ProviderError(f"{label}.message_id is inconsistent")
        payload_end = payload_offset + payload_length
        if frame[payload_offset:payload_end] != payload:
            raise Px4ProviderError(f"{label}.payload_hex is inconsistent")
        frame_checksum = int.from_bytes(frame[payload_end : payload_end + 2], "little")
        if frame_checksum != checksum:
            raise Px4ProviderError(f"{label}.checksum is inconsistent")
        if frame[payload_end + 2 :] != signature:
            raise Px4ProviderError(f"{label}.signature_hex is inconsistent")
        if (
            self._mavlink_x25_checksum(
                frame[1:payload_end],
                crc_extra=MAVLINK_CRC_EXTRAS[expected_message_id],
            )
            != checksum
        ):
            raise Px4ProviderError(f"{label}.checksum failed MAVLink validation")
        return payload

    def _validate_transport_record(
        self, record: Mapping[str, Any], *, label: str
    ) -> None:
        wire_type = self._require_string(
            record.get("wire_type"),
            label=f"{label}.wire_type",
            allowed=frozenset({"COMMAND_LONG", "COMMAND_INT"}),
        )
        if wire_type == "COMMAND_LONG":
            expected_message_id = MAVLINK_COMMAND_LONG_MESSAGE_ID
            maximum_payload_length = 33
        else:
            expected_message_id = MAVLINK_COMMAND_INT_MESSAGE_ID
            maximum_payload_length = 35
        if (
            self._require_string(
                record.get("acceptance_boundary"),
                label=f"{label}.acceptance_boundary",
            )
            != COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY
        ):
            raise Px4ProviderError(f"{label}.acceptance_boundary is invalid")
        self._require_bounded_int(
            record.get("accepted_connection_count"),
            label=f"{label}.accepted_connection_count",
            minimum=1,
            maximum=UINT8_MAX,
        )
        confirmation = self._require_bounded_int(
            record.get("confirmation"),
            label=f"{label}.confirmation",
            minimum=0,
            maximum=UINT8_MAX,
        )
        payload = self._validate_audit_frame(
            record,
            label=label,
            expected_message_id=expected_message_id,
            expected_frame_encoding=COMMAND_TRANSPORT_FRAME_ENCODING,
        )
        # MAVLink 2 may omit trailing zero fields from a packed command.  The
        # command and target IDs (bytes 28..31) are mandatory; COMMAND_LONG's
        # zero confirmation and COMMAND_INT's trailing fields may be absent.
        if len(payload) < 32 or len(payload) > maximum_payload_length:
            raise Px4ProviderError(f"{label} command payload length is invalid")
        if (
            int.from_bytes(payload[28:30], "little") != int(record["command"])
            or payload[30] != int(record["target_system_id"])
            or payload[31] != int(record["target_component_id"])
        ):
            raise Px4ProviderError(f"{label} decoded command payload is inconsistent")
        if wire_type == "COMMAND_LONG":
            decoded_confirmation = payload[32] if len(payload) > 32 else 0
            if decoded_confirmation != confirmation:
                raise Px4ProviderError(
                    f"{label} COMMAND_LONG confirmation is inconsistent"
                )
        elif confirmation != 0:
            raise Px4ProviderError(f"{label} COMMAND_INT confirmation must be zero")

    def _validate_ack_ingress_record(
        self, record: Mapping[str, Any], *, label: str
    ) -> None:
        if (
            self._require_string(
                record.get("capture_boundary"),
                label=f"{label}.capture_boundary",
            )
            != COMMAND_ACK_CAPTURE_BOUNDARY
        ):
            raise Px4ProviderError(f"{label}.capture_boundary is invalid")
        payload = self._validate_audit_frame(
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
            raise Px4ProviderError(f"{label} COMMAND_ACK payload length is invalid")
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
            raise Px4ProviderError(
                f"{label} decoded COMMAND_ACK payload is inconsistent"
            )

    @staticmethod
    def _transport_records_match(
        transport: Mapping[str, Any],
        other: Mapping[str, Any],
    ) -> bool:
        return (
            int(other["command"]) == int(transport["command"])
            and other["wire_type"] == transport["wire_type"]
            and int(other["source_system_id"]) == int(transport["source_system_id"])
            and int(other["source_component_id"])
            == int(transport["source_component_id"])
            and int(other["target_system_id"]) == int(transport["target_system_id"])
            and int(other["target_component_id"])
            == int(transport["target_component_id"])
        )

    @staticmethod
    def _ack_matches_transport(
        ack: Mapping[str, Any], transport: Mapping[str, Any]
    ) -> bool:
        return (
            int(ack["command"]) == int(transport["command"])
            and int(ack["source_system_id"]) == int(transport["target_system_id"])
            and int(ack["source_component_id"]) == int(transport["target_component_id"])
            and int(ack["target_system_id"]) in {0, int(transport["source_system_id"])}
            and int(ack["target_component_id"])
            in {0, int(transport["source_component_id"])}
        )

    def _validate_decoded_command_audit(
        self,
        *,
        proof: Mapping[str, Any],
        ack_audit_status: str,
    ) -> ValidatedDecodedCommandAudit:
        require_accepted = ack_audit_status == COMMAND_AUDIT_APPLIED_STATUS
        if ack_audit_status in {
            COMMAND_AUDIT_MISSING_STATUS,
            COMMAND_AUDIT_CORRUPT_STATUS,
        }:
            if proof.get("decoded_command_audit") is not None:
                raise Px4ProviderError(
                    "PX4 missing/corrupt command audit proof must not include a decoded_command_audit payload"
                )
            return ValidatedDecodedCommandAudit(bundle_digest=None, terminal_seq=None)
        if ack_audit_status not in {
            COMMAND_AUDIT_APPLIED_STATUS,
            COMMAND_AUDIT_FAILED_STATUS,
        }:
            raise Px4ProviderError("PX4 physical proof ack_audit_status is invalid")
        if (
            self._unresolved_command_tool_id is None
            or self._unresolved_command_vehicle_id is None
        ):
            raise Px4ProviderError(
                "PX4 deferred command audit validation is unexpected"
            )
        decoded_audit = self._require_object(
            proof.get("decoded_command_audit"),
            label="decoded_command_audit",
            required=DECODED_COMMAND_AUDIT_FIELDS,
        )
        spec = self._command_audit_spec(self._unresolved_command_tool_id)
        vehicle_system_id = self._vehicle_system_id(self._unresolved_command_vehicle_id)
        if (
            decoded_audit["schema_version"] != COMMAND_AUDIT_SCHEMA_VERSION
            or self._require_bounded_int(
                decoded_audit["vehicle_system_id"],
                label="decoded_command_audit.vehicle_system_id",
                minimum=1,
                maximum=UINT8_MAX,
            )
            != vehicle_system_id
            or self._require_bounded_int(
                decoded_audit["expected_mav_cmd"],
                label="decoded_command_audit.expected_mav_cmd",
                minimum=1,
                maximum=UINT16_MAX,
            )
            != int(spec["mav_cmd"])
            or self._require_string(
                decoded_audit["expected_wire_type"],
                label="decoded_command_audit.expected_wire_type",
                allowed=frozenset({"COMMAND_LONG", "COMMAND_INT"}),
            )
            != spec["wire_type"]
        ):
            raise Px4ProviderError(
                "PX4 decoded command audit does not match the staged tool"
            )
        records = self._require_list(
            decoded_audit["records"], label="decoded_command_audit.records"
        )
        record_digests = self._require_list(
            decoded_audit["record_sha256s"],
            label="decoded_command_audit.record_sha256s",
        )
        if not records or len(records) != len(record_digests):
            raise Px4ProviderError(
                "PX4 decoded command audit record inventory is invalid"
            )

        validated_records: list[Mapping[str, Any]] = []
        previous_seq: int | None = None
        previous_timestamp_ns: int | None = None
        active_groups: list[dict[str, Any]] = []
        pending_ingress: Mapping[str, Any] | None = None
        completed: list[
            tuple[
                tuple[Mapping[str, Any], ...],
                Mapping[str, Any],
                Mapping[str, Any],
            ]
        ] = []
        for index, (record_value, digest_value) in enumerate(
            zip(records, record_digests)
        ):
            label = f"decoded_command_audit.records[{index}]"
            kind = record_value.get("kind") if isinstance(record_value, dict) else None
            if kind == COMMAND_AUDIT_TRANSPORT_KIND:
                required = COMMAND_AUDIT_TRANSPORT_FIELDS
            elif kind == COMMAND_AUDIT_ACK_INGRESS_KIND:
                required = COMMAND_AUDIT_ACK_INGRESS_FIELDS
            elif kind == COMMAND_AUDIT_ACK_DISPOSITION_KIND:
                required = COMMAND_AUDIT_ACK_DISPOSITION_FIELDS
            else:
                raise Px4ProviderError(
                    "PX4 decoded command audit record kind is invalid"
                )
            record = self._require_object(record_value, label=label, required=required)
            record_digest = self._require_real_sha256(
                digest_value,
                label=f"decoded_command_audit.record_sha256s[{index}]",
            )
            if record_digest != _canonical_digest(dict(record)):
                raise Px4ProviderError(
                    "PX4 decoded command audit record digest mismatch"
                )
            seq = self._require_bounded_int(
                record["seq"],
                label=f"{label}.seq",
                minimum=1,
                maximum=UINT64_MAX,
            )
            timestamp_ns = self._require_bounded_int(
                record["timestamp_ns"],
                label=f"{label}.timestamp_ns",
                minimum=0,
                maximum=UINT64_MAX,
            )
            if previous_seq is not None and seq != previous_seq + 1:
                raise Px4ProviderError(
                    "PX4 decoded command audit sequence is not contiguous"
                )
            if (
                previous_timestamp_ns is not None
                and timestamp_ns < previous_timestamp_ns
            ):
                raise Px4ProviderError(
                    "PX4 decoded command audit timestamp moved backward"
                )
            previous_seq = seq
            previous_timestamp_ns = timestamp_ns
            self._require_bounded_int(
                record["command"],
                label=f"{label}.command",
                minimum=1,
                maximum=UINT16_MAX,
            )

            if kind == COMMAND_AUDIT_ACK_DISPOSITION_KIND:
                self._require_bounded_int(
                    record["ack_ingress_seq"],
                    label=f"{label}.ack_ingress_seq",
                    minimum=1,
                    maximum=UINT64_MAX,
                )
                disposition = self._require_string(
                    record["status"],
                    label=f"{label}.status",
                    allowed=frozenset(
                        {
                            COMMAND_AUDIT_MATCHED_DISPOSITION,
                            COMMAND_AUDIT_UNMATCHED_DISPOSITION,
                            COMMAND_AUDIT_TARGET_FILTERED_DISPOSITION,
                            COMMAND_AUDIT_INTERNAL_ERROR_DISPOSITION,
                        }
                    ),
                )
            else:
                for field in (
                    "source_system_id",
                    "source_component_id",
                    "target_system_id",
                    "target_component_id",
                ):
                    self._require_bounded_int(
                        record[field],
                        label=f"{label}.{field}",
                        minimum=0,
                        maximum=UINT8_MAX,
                    )
                if kind == COMMAND_AUDIT_TRANSPORT_KIND:
                    self._validate_transport_record(record, label=label)
                else:
                    self._require_bounded_int(
                        record["result"],
                        label=f"{label}.result",
                        minimum=0,
                        maximum=UINT8_MAX,
                    )
                    self._require_bounded_int(
                        record["progress"],
                        label=f"{label}.progress",
                        minimum=0,
                        maximum=UINT8_MAX,
                    )
                    self._require_bounded_int(
                        record["result_param2"],
                        label=f"{label}.result_param2",
                        minimum=INT32_MIN,
                        maximum=INT32_MAX,
                    )
                    self._validate_ack_ingress_record(record, label=label)
            validated_records.append(record)

            if kind == COMMAND_AUDIT_ACK_INGRESS_KIND:
                if pending_ingress is not None:
                    raise Px4ProviderError(
                        "PX4 decoded COMMAND_ACK ingress is missing its disposition"
                    )
                pending_ingress = record
                continue
            if kind == COMMAND_AUDIT_ACK_DISPOSITION_KIND:
                if pending_ingress is None:
                    raise Px4ProviderError(
                        "PX4 decoded COMMAND_ACK disposition has no ingress"
                    )
                if int(record["ack_ingress_seq"]) != int(pending_ingress["seq"]) or int(
                    record["command"]
                ) != int(pending_ingress["command"]):
                    raise Px4ProviderError(
                        "PX4 decoded COMMAND_ACK disposition references the wrong ingress"
                    )
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
                    if self._ack_matches_transport(
                        pending_ingress, group["transport"]
                    )
                ]
                if disposition == COMMAND_AUDIT_INTERNAL_ERROR_DISPOSITION:
                    raise Px4ProviderError(
                        "PX4 decoded COMMAND_ACK matcher reported an internal error"
                    )
                if disposition == COMMAND_AUDIT_TARGET_FILTERED_DISPOSITION:
                    if targets_this_mavsdk:
                        raise Px4ProviderError(
                            "decoded COMMAND_ACK target-filter disposition is inconsistent"
                        )
                elif disposition == COMMAND_AUDIT_UNMATCHED_DISPOSITION:
                    if not targets_this_mavsdk:
                        raise Px4ProviderError(
                            "PX4 decoded COMMAND_ACK unmatched disposition is inconsistent"
                        )
                    if matching_groups:
                        raise Px4ProviderError(
                            "PX4 decoded COMMAND_ACK was marked unmatched despite an active transport"
                        )
                elif disposition == COMMAND_AUDIT_MATCHED_DISPOSITION:
                    if not targets_this_mavsdk:
                        raise Px4ProviderError(
                            "PX4 decoded COMMAND_ACK matched disposition is inconsistent"
                        )
                    if len(matching_groups) != 1:
                        raise Px4ProviderError(
                            "PX4 decoded COMMAND_ACK match is ambiguous or lacks transport"
                        )
                    group = matching_groups[0]
                    group_records = group["records"]
                    if not isinstance(group_records, list):
                        raise Px4ProviderError(
                            "PX4 decoded command audit group is malformed"
                        )
                    group_records.extend((pending_ingress, record))
                    if int(pending_ingress["result"]) != MAV_RESULT_IN_PROGRESS:
                        completed.append(
                            (
                                tuple(group_records),
                                pending_ingress,
                                record,
                            )
                        )
                        active_groups.remove(group)
                pending_ingress = None
                continue
            if pending_ingress is not None:
                raise Px4ProviderError(
                    "PX4 decoded COMMAND_ACK disposition is not immediate"
                )
            if (
                int(record["source_system_id"]) != MAVSDK_SERVER_SYSTEM_ID
                or int(record["source_component_id"]) != MAVSDK_SERVER_COMPONENT_ID
                or int(record["target_component_id"]) <= 0
            ):
                raise Px4ProviderError(
                    "PX4 command transport acceptance identity is invalid"
                )
            matching_groups = [
                group
                for group in active_groups
                if self._transport_records_match(group["transport"], record)
            ]
            if len(matching_groups) > 1:
                raise Px4ProviderError(
                    "PX4 decoded command audit has ambiguous repeated transports"
                )
            if matching_groups:
                group_records = matching_groups[0]["records"]
                if not isinstance(group_records, list):
                    raise Px4ProviderError(
                        "PX4 decoded command audit group is malformed"
                    )
                group_records.append(record)
            else:
                active_groups.append({"transport": record, "records": [record]})

        if pending_ingress is not None:
            raise Px4ProviderError(
                "PX4 decoded COMMAND_ACK ingress is missing its disposition"
            )
        if active_groups:
            raise Px4ProviderError("PX4 decoded command audit terminal ACK is missing")
        expected_completed = []
        for candidate in completed:
            candidate_records, candidate_ingress, candidate_disposition = candidate
            candidate_transports = [
                item
                for item in candidate_records
                if item["kind"] == COMMAND_AUDIT_TRANSPORT_KIND
            ]
            if not candidate_transports:
                continue
            first_transport = candidate_transports[0]
            if any(
                not self._transport_records_match(first_transport, item)
                for item in candidate_transports[1:]
            ):
                raise Px4ProviderError(
                    "PX4 decoded command audit contains multiple transport identities"
                )
            if (
                int(first_transport["command"]) == int(spec["mav_cmd"])
                and first_transport["wire_type"] == spec["wire_type"]
                and int(first_transport["source_system_id"])
                == MAVSDK_SERVER_SYSTEM_ID
                and int(first_transport["source_component_id"])
                == MAVSDK_SERVER_COMPONENT_ID
                and int(first_transport["target_system_id"]) == vehicle_system_id
            ):
                expected_completed.append(candidate)
        if len(expected_completed) != 1:
            raise Px4ProviderError(
                "PX4 decoded command audit transaction inventory for the staged tool is invalid"
            )
        transaction_records, terminal_ingress, terminal_disposition = (
            expected_completed[0]
        )
        transport_records = [
            item
            for item in transaction_records
            if item["kind"] == COMMAND_AUDIT_TRANSPORT_KIND
        ]
        if not transport_records:
            raise Px4ProviderError(
                "PX4 decoded command audit transport acceptance is missing"
            )
        first_transport = transport_records[0]
        if (
            int(first_transport["command"]) != int(spec["mav_cmd"])
            or first_transport["wire_type"] != spec["wire_type"]
            or int(first_transport["source_system_id"]) != MAVSDK_SERVER_SYSTEM_ID
            or int(first_transport["source_component_id"]) != MAVSDK_SERVER_COMPONENT_ID
            or int(first_transport["target_system_id"]) != vehicle_system_id
            or int(first_transport["target_component_id"]) < 1
        ):
            raise Px4ProviderError(
                "PX4 command transport acceptance does not match the staged tool"
            )

        canonical_journal = b"".join(
            _canonical_json_bytes(dict(item)) + b"\n" for item in validated_records
        )
        journal_digest = self._require_real_sha256(
            decoded_audit["journal_sha256"],
            label="decoded_command_audit.journal_sha256",
        )
        if journal_digest != hashlib.sha256(canonical_journal).hexdigest():
            raise Px4ProviderError("PX4 decoded command audit journal digest mismatch")
        terminal_ingress_proof = self._require_object(
            decoded_audit["terminal_ack_ingress_record"],
            label="decoded_command_audit.terminal_ack_ingress_record",
            required=COMMAND_AUDIT_ACK_INGRESS_FIELDS,
        )
        terminal_disposition_proof = self._require_object(
            decoded_audit["terminal_ack_disposition_record"],
            label="decoded_command_audit.terminal_ack_disposition_record",
            required=COMMAND_AUDIT_ACK_DISPOSITION_FIELDS,
        )
        terminal_ingress_digest = self._require_real_sha256(
            decoded_audit["terminal_ack_ingress_record_sha256"],
            label="decoded_command_audit.terminal_ack_ingress_record_sha256",
        )
        terminal_disposition_digest = self._require_real_sha256(
            decoded_audit["terminal_ack_disposition_record_sha256"],
            label="decoded_command_audit.terminal_ack_disposition_record_sha256",
        )
        if (
            terminal_ingress_proof != terminal_ingress
            or terminal_disposition_proof != terminal_disposition
            or terminal_ingress_digest != _canonical_digest(dict(terminal_ingress))
            or terminal_disposition_digest
            != _canonical_digest(dict(terminal_disposition))
            or terminal_disposition["status"] != COMMAND_AUDIT_MATCHED_DISPOSITION
        ):
            raise Px4ProviderError(
                "PX4 decoded command audit terminal identity is inconsistent"
            )
        terminal_result = self._require_bounded_int(
            terminal_ingress["result"],
            label="decoded_command_audit.terminal_ack_ingress_record.result",
            minimum=0,
            maximum=UINT8_MAX,
        )
        if terminal_result == MAV_RESULT_IN_PROGRESS:
            raise Px4ProviderError(
                "PX4 terminal decoded COMMAND_ACK cannot be IN_PROGRESS"
            )
        if require_accepted and terminal_result != MAV_RESULT_ACCEPTED:
            raise Px4ProviderError("PX4 applied proof requires MAV_RESULT_ACCEPTED")
        if not require_accepted and terminal_result == MAV_RESULT_ACCEPTED:
            raise Px4ProviderError(
                "PX4 failed pre-apply proof cannot carry an accepted terminal ACK"
            )
        bundle_digest = _canonical_digest(dict(decoded_audit))
        terminal_seq = self._require_bounded_int(
            terminal_ingress["seq"],
            label="decoded_command_audit.terminal_ack_ingress_record.seq",
            minimum=1,
            maximum=UINT64_MAX,
        )
        current_digest = self._unresolved_command_audit_digest
        if current_digest is not None:
            if bundle_digest != current_digest:
                raise Px4ProviderError(
                    "PX4 decoded command audit bundle changed across deferred phases"
                )
        else:
            consumed = self._consumed_terminal_ack_seq_by_vehicle.get(
                self._unresolved_command_vehicle_id
            )
            if consumed is not None and terminal_seq <= consumed:
                raise Px4ProviderError(
                    "PX4 terminal decoded COMMAND_ACK sequence was already consumed"
                )
        return ValidatedDecodedCommandAudit(
            bundle_digest=bundle_digest,
            terminal_seq=terminal_seq,
        )

    def _is_engineering_horizon(self, time_value: SimulationTime) -> bool:
        # The urban resolver binds formal to exactly 3000 steps. Restrict this
        # interruption policy to shorter engineering recordings, never formal.
        task = getattr(self._scenario, "task", None)
        return (
            getattr(task, "package_id", None) == "urban.uav-recovery-demo.v1"
            and 0 < self._clock.max_steps < 3_000
            and time_value
            == SimulationTime(
                tick=self._clock.max_steps,
                sim_time_ns=self._clock.max_steps * self._clock.step_ns,
            )
        )

    @staticmethod
    def _command_argument_map(request: CommandRequest) -> dict[str, object]:
        arguments = {item.name: item.value for item in request.arguments}
        if len(arguments) != len(request.arguments):
            raise Px4ProviderError("PX4 command request duplicated one argument")
        return arguments

    def _validate_physical_proof(
        self,
        proof: Mapping[str, Any],
        *,
        phase: str,
        receipt: StepReceipt,
        horizon_interrupted: bool = False,
    ) -> ValidatedDecodedCommandAudit:
        proof = self._require_object(
            proof,
            label="PX4 deferred command physical proof",
            required=PHYSICAL_PROOF_FIELDS,
        )
        if (
            self._unresolved_command_id is None
            or self._unresolved_command_tool_id is None
            or self._unresolved_command_vehicle_id is None
            or self._unresolved_command_stage is None
            or self._unresolved_command_accepted_at is None
        ):
            raise Px4ProviderError("PX4 deferred command physical proof is unexpected")
        if (
            self._require_string(proof["command_id"], label="command_id")
            != self._unresolved_command_id
            or self._require_string(proof["tool_id"], label="tool_id")
            != self._unresolved_command_tool_id
            or self._require_string(
                proof["phase"],
                label="phase",
                allowed=frozenset({"applied", "completed", "failed"}),
            )
            != phase
        ):
            raise Px4ProviderError("PX4 deferred command physical proof is invalid")
        if (
            self._require_string(proof["vehicle_id"], label="vehicle_id")
            != self._unresolved_command_vehicle_id
        ):
            raise Px4ProviderError(
                "PX4 deferred command physical proof has the wrong vehicle_id"
            )
        if (
            self._require_string(
                proof["policy_schema_version"], label="policy_schema_version"
            )
            != "aero-bench.px4-physical-completion-policy/v2"
        ):
            raise Px4ProviderError(
                "PX4 deferred command physical proof policy schema mismatch"
            )
        ack_audit_status = self._require_string(
            proof["ack_audit_status"],
            label="ack_audit_status",
            allowed=frozenset(
                {
                    COMMAND_AUDIT_APPLIED_STATUS,
                    COMMAND_AUDIT_FAILED_STATUS,
                    COMMAND_AUDIT_MISSING_STATUS,
                    COMMAND_AUDIT_CORRUPT_STATUS,
                }
            ),
        )
        baseline_tick = self._require_int(proof["baseline_tick"], label="baseline_tick")
        baseline_sim_time_ns = self._require_int(
            proof["baseline_sim_time_ns"], label="baseline_sim_time_ns"
        )
        current_tick = self._require_int(proof["current_tick"], label="current_tick")
        current_sim_time_ns = self._require_int(
            proof["current_sim_time_ns"], label="current_sim_time_ns"
        )
        accepted_tick = self._require_int(proof["accepted_tick"], label="accepted_tick")
        accepted_sim_time_ns = self._require_int(
            proof["accepted_sim_time_ns"], label="accepted_sim_time_ns"
        )
        applied_tick = self._require_optional_int(
            proof["applied_tick"], label="applied_tick"
        )
        applied_sim_time_ns = self._require_optional_int(
            proof["applied_sim_time_ns"], label="applied_sim_time_ns"
        )
        if (applied_tick is None) != (applied_sim_time_ns is None):
            raise Px4ProviderError(
                "PX4 deferred command physical proof applied time is malformed"
            )
        self._require_real_sha256(
            proof["baseline_state_digest"], label="baseline_state_digest"
        )
        self._require_real_sha256(
            proof["current_state_digest"], label="current_state_digest"
        )
        if not isinstance(proof["thresholds"], dict):
            raise Px4ProviderError(
                "PX4 deferred command physical proof is missing thresholds"
            )
        if not isinstance(proof["metrics"], dict):
            raise Px4ProviderError(
                "PX4 deferred command physical proof is missing metrics"
            )
        if not isinstance(proof["settling"], dict):
            raise Px4ProviderError(
                "PX4 deferred command physical proof is missing settling"
            )
        accepted_at = self._unresolved_command_accepted_at
        if (
            baseline_tick != accepted_at.tick
            or baseline_sim_time_ns != accepted_at.sim_time_ns
            or accepted_tick != accepted_at.tick
            or accepted_sim_time_ns != accepted_at.sim_time_ns
        ):
            raise Px4ProviderError(
                "PX4 deferred command physical proof acceptance baseline mismatch"
            )
        if (
            current_tick != receipt.reached.tick
            or current_sim_time_ns != receipt.reached.sim_time_ns
        ):
            raise Px4ProviderError(
                "PX4 deferred command physical proof current time mismatch"
            )
        if self._unresolved_command_stage == "accepted":
            if phase == "applied":
                if ack_audit_status != COMMAND_AUDIT_APPLIED_STATUS:
                    raise Px4ProviderError(
                        "PX4 applied proof requires a validated decoded COMMAND_ACK acceptance"
                    )
                audit = self._validate_decoded_command_audit(
                    proof=proof,
                    ack_audit_status=ack_audit_status,
                )
                if (
                    applied_tick != receipt.reached.tick
                    or applied_sim_time_ns != receipt.reached.sim_time_ns
                ):
                    raise Px4ProviderError(
                        "PX4 deferred command applied proof must bind to the current step"
                    )
                return audit
            if phase != "failed":
                raise Px4ProviderError(
                    "PX4 deferred command failed-before-apply proof is invalid"
                )
            if horizon_interrupted:
                if (
                    ack_audit_status != COMMAND_AUDIT_APPLIED_STATUS
                    or applied_tick != receipt.reached.tick
                    or applied_sim_time_ns != receipt.reached.sim_time_ns
                ):
                    raise Px4ProviderError(
                        "PX4 horizon interruption must prove command application at the terminal barrier"
                    )
                return self._validate_decoded_command_audit(
                    proof=proof,
                    ack_audit_status=ack_audit_status,
                )
            if applied_tick is not None:
                raise Px4ProviderError(
                    "PX4 deferred command failed-before-apply proof is invalid"
                )
            return self._validate_decoded_command_audit(
                proof=proof,
                ack_audit_status=ack_audit_status,
            )
        applied_at = self._unresolved_command_applied_at
        if applied_at is None:
            raise Px4ProviderError("PX4 applied command tracking lost its applied time")
        if self._unresolved_command_audit_digest is None:
            raise Px4ProviderError(
                "PX4 applied command tracking lost its decoded command audit digest"
            )
        if ack_audit_status != COMMAND_AUDIT_APPLIED_STATUS:
            raise Px4ProviderError(
                "PX4 post-apply deferred proofs must retain the validated decoded COMMAND_ACK acceptance"
            )
        audit = self._validate_decoded_command_audit(
            proof=proof,
            ack_audit_status=ack_audit_status,
        )
        if (
            applied_tick != applied_at.tick
            or applied_sim_time_ns != applied_at.sim_time_ns
        ):
            raise Px4ProviderError(
                "PX4 deferred command physical proof applied time mismatch"
            )
        if (
            receipt.reached.tick <= applied_at.tick
            or receipt.reached.sim_time_ns <= applied_at.sim_time_ns
        ):
            raise Px4ProviderError(
                "PX4 deferred command terminal proof must occur after application"
            )
        return audit

    def _validate_deferred_command_events(self, receipt: StepReceipt) -> None:
        """Validate each vehicle's deferred lifecycle independently."""
        command_events = tuple(
            event
            for event in receipt.events
            if event.payload_schema_id == self._COMMAND_SCHEMA
        )
        proof_events = tuple(
            event
            for event in receipt.events
            if event.payload_schema_id == self._COMMAND_PHYSICAL_SCHEMA
        )
        if not self._pending_commands_by_vehicle:
            if command_events or proof_events:
                raise Px4ProviderError(
                    "PX4 emitted deferred command events without an unresolved command"
                )
            return

        command_by_id: dict[str, list[ProviderEvent]] = {}
        for event in command_events:
            payload = self._payload_map(event, label="PX4 command lifecycle event")
            command_id = payload.get("command_id")
            if not isinstance(command_id, str):
                raise Px4ProviderError("PX4 deferred command event has no command_id")
            command_by_id.setdefault(command_id, []).append(event)
        proof_by_id: dict[str, list[ProviderEvent]] = {}
        proof_vehicle_by_id: dict[str, str] = {}
        for event in proof_events:
            payload = self._payload_map(event, label="PX4 command physical proof event")
            command_id = payload.get("command_id")
            if not isinstance(command_id, str):
                raise Px4ProviderError("PX4 deferred command proof has no command_id")
            proof = self._parse_proof_json(payload.get("proof_json"))
            vehicle_id = proof.get("vehicle_id")
            if not isinstance(vehicle_id, str):
                raise Px4ProviderError(
                    "PX4 deferred command proof has no vehicle_id for routing"
                )
            prior_vehicle = proof_vehicle_by_id.get(command_id)
            if prior_vehicle is not None and prior_vehicle != vehicle_id:
                raise Px4ProviderError(
                    "PX4 deferred command proof routes one command to multiple vehicles"
                )
            proof_vehicle_by_id[command_id] = vehicle_id
            proof_by_id.setdefault(command_id, []).append(event)

        known_command_ids = {
            state.command_id for state in self._pending_commands_by_vehicle.values()
        }
        event_command_ids = set(command_by_id) | set(proof_by_id)
        if event_command_ids - known_command_ids:
            raise Px4ProviderError(
                "PX4 deferred command event names an unknown unresolved command"
            )
        for vehicle_id, state in tuple(self._pending_commands_by_vehicle.items()):
            command_events_for_state = tuple(command_by_id.get(state.command_id, ()))
            proof_events_for_state = tuple(proof_by_id.get(state.command_id, ()))
            if proof_events_for_state and any(
                proof_vehicle_by_id.get(state.command_id) != vehicle_id
                for _event in proof_events_for_state
            ):
                raise Px4ProviderError(
                    "PX4 deferred command physical proof has the wrong vehicle_id"
                )
            self._restore_command(state)
            self._validate_deferred_command_events_for_active(
                receipt,
                (*command_events_for_state, *proof_events_for_state),
            )
            if self._unresolved_command_id is None:
                self._pending_commands_by_vehicle.pop(vehicle_id, None)
                if self._deferred_failure_latched:
                    self._failure_latched_by_vehicle.add(vehicle_id)
            else:
                self._pending_commands_by_vehicle[vehicle_id] = (
                    self._capture_active_command(vehicle_id=vehicle_id)
                )
        self._sync_command_compatibility_view()

    def _validate_deferred_command_events_for_active(
        self, receipt: StepReceipt, events: tuple[ProviderEvent, ...] | None = None
    ) -> None:
        event_inventory = receipt.events if events is None else events
        command_events = [
            event
            for event in event_inventory
            if event.payload_schema_id == self._COMMAND_SCHEMA
        ]
        proof_events = [
            event
            for event in event_inventory
            if event.payload_schema_id == self._COMMAND_PHYSICAL_SCHEMA
        ]
        if self._unresolved_command_id is None:
            if command_events or proof_events:
                raise Px4ProviderError(
                    "PX4 emitted deferred command events without an unresolved command"
                )
            return
        if len(command_events) != len(proof_events):
            raise Px4ProviderError(
                "PX4 deferred command lifecycle and physical proof events must pair exactly"
            )
        if (
            self._unresolved_command_tool_id is None
            or self._unresolved_command_stage is None
        ):
            raise Px4ProviderError("PX4 deferred command tracking is incomplete")
        command_payloads: dict[str, tuple[ProviderEvent, dict[str, Any]]] = {}
        for event in command_events:
            payload = self._payload_map(event, label="PX4 command lifecycle event")
            phase = payload.get("phase")
            if payload.get("command_id") != self._unresolved_command_id:
                raise Px4ProviderError(
                    "PX4 deferred command lifecycle event has the wrong command_id"
                )
            if payload.get("tool_id") != self._unresolved_command_tool_id:
                raise Px4ProviderError(
                    "PX4 deferred command lifecycle event has the wrong tool_id"
                )
            if (
                event.provider_id != self._config.provider_id
                or event.time != receipt.reached
                or payload.get("schema_id") != self._COMMAND_SCHEMA
                or not isinstance(phase, str)
            ):
                raise Px4ProviderError(
                    "PX4 deferred command lifecycle event is invalid"
                )
            if phase in command_payloads:
                raise Px4ProviderError(
                    "PX4 deferred command lifecycle event duplicated one phase"
                )
            command_payloads[phase] = (event, payload)
        proof_payloads: dict[str, Mapping[str, Any]] = {}
        for event in proof_events:
            payload = self._payload_map(event, label="PX4 command physical proof event")
            phase = payload.get("phase")
            proof = self._parse_proof_json(payload.get("proof_json"))
            if payload.get("command_id") != self._unresolved_command_id:
                raise Px4ProviderError(
                    "PX4 deferred command physical proof has the wrong command_id"
                )
            if payload.get("tool_id") != self._unresolved_command_tool_id:
                raise Px4ProviderError(
                    "PX4 deferred command physical proof has the wrong tool_id"
                )
            if (
                event.provider_id != self._config.provider_id
                or event.time != receipt.reached
                or payload.get("schema_id") != self._COMMAND_PHYSICAL_SCHEMA
                or not isinstance(phase, str)
                or proof.get("command_id") != self._unresolved_command_id
                or proof.get("tool_id") != self._unresolved_command_tool_id
                or proof.get("phase") != phase
            ):
                raise Px4ProviderError("PX4 deferred command physical proof is invalid")
            if phase in proof_payloads:
                raise Px4ProviderError(
                    "PX4 deferred command physical proof duplicated one phase"
                )
            proof_payloads[phase] = proof
        if set(command_payloads) != set(proof_payloads):
            raise Px4ProviderError(
                "PX4 deferred command lifecycle and proof phases do not match"
            )
        failed_command = command_payloads.get("failed")
        has_horizon_interruption_detail = (
            failed_command is not None
            and failed_command[1].get("detail")
            == COMMAND_HORIZON_INTERRUPTED_DETAIL
        )
        horizon_interrupted = (
            has_horizon_interruption_detail
            and self._is_engineering_horizon(receipt.reached)
        )
        if has_horizon_interruption_detail and not horizon_interrupted:
            raise Px4ProviderError(
                "PX4 horizon interruption was emitted outside the urban engineering horizon"
            )
        next_unresolved_stage = self._unresolved_command_stage
        next_unresolved_applied_at = self._unresolved_command_applied_at
        next_unresolved_audit_digest = self._unresolved_command_audit_digest
        next_consumed_terminal_ack_seq_by_vehicle = dict(
            self._consumed_terminal_ack_seq_by_vehicle
        )
        clear_tracking = False
        latch_failure = False
        if self._unresolved_command_stage == "accepted":
            if not command_payloads:
                raise Px4ProviderError(
                    "PX4 deferred command omitted the first applied-or-failed step event"
                )
            if set(command_payloads) - {"applied", "failed"}:
                raise Px4ProviderError(
                    "PX4 deferred command must advance from accepted to applied or failed"
                )
            if len(command_payloads) != 1:
                raise Px4ProviderError(
                    "PX4 deferred command may emit at most one lifecycle phase per step"
                )
            phase = next(iter(command_payloads))
            audit = self._validate_physical_proof(
                proof_payloads[phase],
                phase=phase,
                receipt=receipt,
                horizon_interrupted=horizon_interrupted,
            )
            if (
                audit.terminal_seq is not None
                and self._unresolved_command_vehicle_id is not None
            ):
                next_consumed_terminal_ack_seq_by_vehicle[
                    self._unresolved_command_vehicle_id
                ] = max(
                    audit.terminal_seq,
                    next_consumed_terminal_ack_seq_by_vehicle.get(
                        self._unresolved_command_vehicle_id,
                        0,
                    ),
                )
            if phase == "applied":
                next_unresolved_stage = "applied"
                next_unresolved_applied_at = receipt.reached
                next_unresolved_audit_digest = audit.bundle_digest
            else:
                clear_tracking = True
                latch_failure = not horizon_interrupted
        else:
            if not command_payloads:
                return
            if set(command_payloads) - {"completed", "failed"}:
                raise Px4ProviderError(
                    "PX4 deferred command may not regress or re-apply after application"
                )
            if len(command_payloads) != 1:
                raise Px4ProviderError(
                    "PX4 deferred command may emit at most one terminal phase per step"
                )
            phase = next(iter(command_payloads))
            audit = self._validate_physical_proof(
                proof_payloads[phase],
                phase=phase,
                receipt=receipt,
                horizon_interrupted=horizon_interrupted,
            )
            if (
                audit.terminal_seq is not None
                and self._unresolved_command_vehicle_id is not None
            ):
                next_consumed_terminal_ack_seq_by_vehicle[
                    self._unresolved_command_vehicle_id
                ] = max(
                    audit.terminal_seq,
                    next_consumed_terminal_ack_seq_by_vehicle.get(
                        self._unresolved_command_vehicle_id,
                        0,
                    ),
                )
            if phase == "completed":
                applied_at = self._unresolved_command_applied_at
                if applied_at is None or receipt.reached == applied_at:
                    raise Px4ProviderError(
                        "PX4 command completion must occur strictly after application"
                    )
                clear_tracking = True
            else:
                clear_tracking = True
                latch_failure = not horizon_interrupted
        if clear_tracking:
            self._clear_command_tracking(clear_failure_latch=False)
        else:
            self._unresolved_command_stage = next_unresolved_stage
            self._unresolved_command_applied_at = next_unresolved_applied_at
            self._unresolved_command_audit_digest = next_unresolved_audit_digest
        self._consumed_terminal_ack_seq_by_vehicle = (
            next_consumed_terminal_ack_seq_by_vehicle
        )
        if latch_failure:
            self._deferred_failure_latched = True

    @staticmethod
    def _validate_state_events(
        receipt: StepReceipt,
        *,
        target: SimulationTime,
        vehicles: tuple[str, ...],
    ) -> None:
        state_events = [
            event
            for event in receipt.events
            if event.payload_schema_id == Px4GazeboProvider._STATE_SCHEMA
        ]
        if not state_events:
            raise Px4ProviderError(
                "PX4 step omitted the authoritative px4.state.v1 event"
            )
        seen_vehicles: set[str] = set()
        for event in state_events:
            payload = {item.name: item.value for item in event.payload}
            if len(payload) != len(event.payload):
                raise Px4ProviderError("PX4 state event contains duplicate fields")
            missing = Px4GazeboProvider._REQUIRED_STATE_FIELDS - payload.keys()
            extra = payload.keys() - Px4GazeboProvider._REQUIRED_STATE_FIELDS
            if missing or extra:
                raise Px4ProviderError(
                    "PX4 state event fields differ from the strict inventory: "
                    f"missing={sorted(missing)}, extra={sorted(extra)}"
                )
            vehicle_id = payload["vehicle_id"]
            if not isinstance(vehicle_id, str) or vehicle_id not in vehicles:
                raise Px4ProviderError("PX4 state event names an undeclared vehicle")
            if vehicle_id in seen_vehicles:
                raise Px4ProviderError("PX4 returned duplicate state for one vehicle")
            if event.event_id != f"state.{vehicle_id}.{target.tick}":
                raise Px4ProviderError("PX4 state event identifier is invalid")
            seen_vehicles.add(vehicle_id)
            simulation_time_ns = payload["simulation_time_ns"]
            if (
                not isinstance(simulation_time_ns, int)
                or isinstance(simulation_time_ns, bool)
                or simulation_time_ns != target.sim_time_ns
            ):
                raise Px4ProviderError(
                    "PX4 state event time differs from the barrier target"
                )
            for name in (
                "pose_json",
                "position_wgs84_json",
                "velocity_json",
                "angular_velocity_json",
                "attitude_json",
                "health",
            ):
                Px4GazeboProvider._parse_canonical_json_object(
                    payload[name], label=f"PX4 state event {name}"
                )
            contacts = Px4GazeboProvider._parse_canonical_json_array(
                payload["contacts_json"], label="PX4 state event contacts_json"
            )
            if any(
                not isinstance(value, str) or not value for value in contacts
            ) or contacts != tuple(sorted(set(contacts))):
                raise Px4ProviderError("PX4 state event contacts_json is not canonical")
            for name in (
                "armed",
                "in_air",
                "landed",
                "ground_contact",
                "collision_contact",
            ):
                if not isinstance(payload[name], bool):
                    raise Px4ProviderError(f"PX4 state event {name} must be a boolean")
            if (
                not isinstance(payload["landed_state"], str)
                or not payload["landed_state"]
            ):
                raise Px4ProviderError("PX4 state event landed_state is invalid")
            if (
                not isinstance(payload["evidence_path"], str)
                or not payload["evidence_path"]
            ):
                raise Px4ProviderError("PX4 state event evidence_path is invalid")
            if (
                not isinstance(payload["evidence_sha256"], str)
                or _SHA256.fullmatch(payload["evidence_sha256"]) is None
            ):
                raise Px4ProviderError("PX4 state event evidence digest is invalid")
        missing_vehicles = set(vehicles) - seen_vehicles
        if missing_vehicles:
            raise Px4ProviderError(
                f"PX4 state omitted vehicles: {sorted(missing_vehicles)}"
            )

    def _validate_public_sensor_frame_events(
        self,
        receipt: StepReceipt,
        *,
        target: SimulationTime,
    ) -> None:
        frame_events = tuple(
            event
            for event in receipt.events
            if event.event_id == PUBLIC_SENSOR_FRAME_EVENT_TYPE
            or event.payload_schema_id == PUBLIC_SENSOR_FRAME_SCHEMA
        )
        expected = self._pending_public_sensor_frames
        if len(frame_events) != len(expected):
            raise Px4ProviderError(
                "PX4 public sensor-frame event inventory is incomplete"
            )
        observed_frame_ids: set[str] = set()
        for event in frame_events:
            if (
                event.provider_id != self._config.provider_id
                or event.event_id != PUBLIC_SENSOR_FRAME_EVENT_TYPE
                or event.payload_schema_id != PUBLIC_SENSOR_FRAME_SCHEMA
                or event.time != target
            ):
                raise Px4ProviderError(
                    "PX4 public sensor-frame event authority is invalid"
                )
            payload = self._payload_map(
                event,
                label="PX4 public sensor-frame event",
            )
            frame_id = payload.get("frame_id")
            if not isinstance(frame_id, str) or frame_id in observed_frame_ids:
                raise Px4ProviderError("PX4 public sensor-frame identity is invalid")
            expected_payload = expected.get(frame_id)
            if (
                expected_payload is None
                or set(payload) != set(expected_payload)
                or payload != expected_payload
            ):
                raise Px4ProviderError(
                    "PX4 public sensor-frame event is not bound to a captured frame"
                )
            observed_frame_ids.add(frame_id)
        if observed_frame_ids != set(expected):
            raise Px4ProviderError(
                "PX4 public sensor-frame event inventory does not close"
            )

    @classmethod
    def _validate_motion_samples_against_receipt(
        cls,
        samples: tuple[StateSample, ...],
        receipt: StepReceipt,
    ) -> None:
        state_payloads: dict[str, dict[str, Any]] = {}
        for event in receipt.events:
            if event.payload_schema_id != cls._STATE_SCHEMA:
                continue
            payload = cls._payload_map(event, label="PX4 state event")
            vehicle_id = payload.get("vehicle_id")
            if not isinstance(vehicle_id, str) or vehicle_id in state_payloads:
                raise Px4ProviderError("PX4 state event vehicle inventory is invalid")
            state_payloads[vehicle_id] = payload
        if tuple(sorted(state_payloads)) != tuple(
            sample.entity_id for sample in samples
        ):
            raise Px4ProviderError(
                "PX4 motion samples differ from receipt state-event inventory"
            )

        for sample in samples:
            payload = state_payloads[sample.entity_id]
            pose = cls._parse_canonical_json_object(
                payload["pose_json"],
                label=f"PX4 {sample.entity_id} pose_json",
            )
            attitude = cls._parse_canonical_json_object(
                payload["attitude_json"],
                label=f"PX4 {sample.entity_id} attitude_json",
            )
            velocity = cls._parse_canonical_json_object(
                payload["velocity_json"],
                label=f"PX4 {sample.entity_id} velocity_json",
            )
            angular_velocity = cls._parse_canonical_json_object(
                payload["angular_velocity_json"],
                label=f"PX4 {sample.entity_id} angular_velocity_json",
            )
            contacts = cls._parse_canonical_json_array(
                payload["contacts_json"],
                label=f"PX4 {sample.entity_id} contacts_json",
            )
            position_wgs84 = cls._parse_canonical_json_object(
                payload["position_wgs84_json"],
                label=f"PX4 {sample.entity_id} position_wgs84_json",
            )
            health = cls._parse_canonical_json_object(
                payload["health"],
                label=f"PX4 {sample.entity_id} health",
            )
            expected_pose_fields = {
                "x_m",
                "y_m",
                "z_m",
                "roll_rad",
                "pitch_rad",
                "yaw_rad",
            }
            if set(pose) != expected_pose_fields:
                raise Px4ProviderError("PX4 pose_json fields are invalid")
            if set(attitude) != {"roll_rad", "pitch_rad", "yaw_rad"}:
                raise Px4ProviderError("PX4 attitude_json fields are invalid")
            if set(velocity) != {"north_m_s", "east_m_s", "down_m_s"}:
                raise Px4ProviderError("PX4 velocity_json fields are invalid")
            if set(angular_velocity) != {"x_rad_s", "y_rad_s", "z_rad_s"}:
                raise Px4ProviderError("PX4 angular_velocity_json fields are invalid")
            if any(
                not isinstance(value, str) or not value for value in contacts
            ) or contacts != tuple(sorted(set(contacts))):
                raise Px4ProviderError("PX4 contacts_json values are invalid")
            if set(position_wgs84) != {
                "longitude_deg",
                "latitude_deg",
                "altitude_m",
            }:
                raise Px4ProviderError("PX4 position_wgs84_json fields are invalid")
            longitude_deg = cls._require_finite_number(
                position_wgs84["longitude_deg"],
                label=f"PX4 {sample.entity_id} longitude_deg",
            )
            latitude_deg = cls._require_finite_number(
                position_wgs84["latitude_deg"],
                label=f"PX4 {sample.entity_id} latitude_deg",
            )
            cls._require_finite_number(
                position_wgs84["altitude_m"],
                label=f"PX4 {sample.entity_id} altitude_m",
            )
            if (
                not -180.0 <= longitude_deg <= 180.0
                or not -90.0 <= latitude_deg <= 90.0
            ):
                raise Px4ProviderError("PX4 WGS84 receipt position is outside range")

            numeric_pose = {
                name: cls._require_finite_number(
                    value,
                    label=f"PX4 {sample.entity_id} pose_json.{name}",
                )
                for name, value in pose.items()
            }
            numeric_attitude = {
                name: cls._require_finite_number(
                    value,
                    label=f"PX4 {sample.entity_id} attitude_json.{name}",
                )
                for name, value in attitude.items()
            }
            if any(
                not math.isclose(
                    numeric_pose[name],
                    numeric_attitude[name],
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
                for name in ("roll_rad", "pitch_rad", "yaw_rad")
            ):
                raise Px4ProviderError(
                    "PX4 pose and attitude receipt evidence disagree"
                )
            position_enu = sample.pose.position.enu
            if any(
                not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)
                for actual, expected in (
                    (position_enu.east_m, numeric_pose["x_m"]),
                    (position_enu.north_m, numeric_pose["y_m"]),
                    (position_enu.up_m, numeric_pose["z_m"]),
                )
            ):
                raise Px4ProviderError(
                    "PX4 motion sample position differs from Gazebo receipt evidence"
                )
            try:
                expected_orientation = frame_math.rpy_to_quaternion(
                    roll_rad=numeric_pose["roll_rad"],
                    pitch_rad=numeric_pose["pitch_rad"],
                    yaw_rad=numeric_pose["yaw_rad"],
                )
            except frame_math.FrameMathError as exc:
                raise Px4ProviderError(
                    "PX4 Gazebo orientation evidence is invalid"
                ) from exc
            orientation_enu = sample.pose.orientation_enu
            if any(
                not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
                for actual, expected in (
                    (orientation_enu.qw, expected_orientation.w),
                    (orientation_enu.qx, expected_orientation.x),
                    (orientation_enu.qy, expected_orientation.y),
                    (orientation_enu.qz, expected_orientation.z),
                )
            ):
                raise Px4ProviderError(
                    "PX4 motion sample orientation differs from Gazebo receipt evidence"
                )

            numeric_velocity = {
                name: cls._require_finite_number(
                    value,
                    label=f"PX4 {sample.entity_id} velocity_json.{name}",
                )
                for name, value in velocity.items()
            }
            if any(
                not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)
                for actual, expected in (
                    (
                        sample.linear_velocity_ned.north_mps,
                        numeric_velocity["north_m_s"],
                    ),
                    (
                        sample.linear_velocity_ned.east_mps,
                        numeric_velocity["east_m_s"],
                    ),
                    (
                        sample.linear_velocity_ned.down_mps,
                        numeric_velocity["down_m_s"],
                    ),
                )
            ):
                raise Px4ProviderError(
                    "PX4 motion sample velocity differs from MAVSDK receipt evidence"
                )
            numeric_angular_velocity = {
                name: cls._require_finite_number(
                    value,
                    label=f"PX4 {sample.entity_id} angular_velocity_json.{name}",
                )
                for name, value in angular_velocity.items()
            }
            if sample.angular_velocity_body is None or any(
                not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)
                for actual, expected in (
                    (
                        sample.angular_velocity_body.x_radps,
                        numeric_angular_velocity["x_rad_s"],
                    ),
                    (
                        sample.angular_velocity_body.y_radps,
                        numeric_angular_velocity["y_rad_s"],
                    ),
                    (
                        sample.angular_velocity_body.z_radps,
                        numeric_angular_velocity["z_rad_s"],
                    ),
                )
            ):
                raise Px4ProviderError(
                    "PX4 motion sample angular velocity differs from receipt evidence"
                )

            flight_mode = payload["flight_mode"]
            armed = payload["armed"]
            in_air = payload["in_air"]
            landed = payload["landed"]
            landed_state = payload["landed_state"]
            ground_contact = payload["ground_contact"]
            collision_contact = payload["collision_contact"]
            if not isinstance(flight_mode, str) or not flight_mode:
                raise Px4ProviderError("PX4 receipt flight_mode is invalid")
            if any(
                not isinstance(value, bool)
                for value in (
                    armed,
                    in_air,
                    landed,
                    ground_contact,
                    collision_contact,
                )
            ):
                raise Px4ProviderError("PX4 receipt boolean state is invalid")
            if not isinstance(landed_state, str) or not landed_state:
                raise Px4ProviderError("PX4 receipt landed_state is invalid")
            observed_ground_contact = any(
                value.startswith("ground.") or value.startswith("launch_pad.")
                for value in contacts
            )
            observed_collision_contact = any(
                not value.startswith("ground.") and not value.startswith("launch_pad.")
                for value in contacts
            )
            if (
                ground_contact != observed_ground_contact
                or collision_contact != observed_collision_contact
                or landed != (landed_state == "ON_GROUND")
            ):
                raise Px4ProviderError(
                    "PX4 receipt contact or landed flags are inconsistent"
                )
            if (
                not isinstance(payload["evidence_path"], str)
                or not payload["evidence_path"]
                or not isinstance(payload["evidence_sha256"], str)
                or _SHA256.fullmatch(payload["evidence_sha256"]) is None
            ):
                raise Px4ProviderError("PX4 receipt evidence reference is invalid")
            battery_percent = cls._require_finite_number(
                payload["battery_percent"],
                label=f"PX4 {sample.entity_id} battery_percent",
            )
            if not 0.0 <= battery_percent <= 100.0:
                raise Px4ProviderError("PX4 receipt battery_percent is outside 0..100")
            if sample.mode != flight_mode or sample.armed != armed:
                raise Px4ProviderError(
                    "PX4 motion sample mode or arming differs from receipt evidence"
                )
            if (
                sample.battery is None
                or sample.battery.voltage_v is not None
                or sample.battery.current_a is not None
                or sample.battery.consumed_mah is not None
                or sample.battery.temperature_c is not None
                or sample.battery.attributes
                or sample.battery.remaining_fraction is None
                or not math.isclose(
                    sample.battery.remaining_fraction,
                    battery_percent / 100.0,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                raise Px4ProviderError(
                    "PX4 motion sample battery differs from receipt evidence"
                )
            if not health or any(
                not isinstance(value, bool) for value in health.values()
            ):
                raise Px4ProviderError("PX4 receipt health state is invalid")
            sample_health = sample.health
            health_attributes = (
                {}
                if sample_health is None
                else {
                    attribute.name: attribute.value
                    for attribute in sample_health.attributes
                }
            )
            if (
                sample_health is None
                or health_attributes != dict(health)
                or sample_health.healthy != all(health.values())
            ):
                raise Px4ProviderError(
                    "PX4 motion sample health differs from receipt evidence"
                )
            attributes = {
                attribute.name: attribute.value for attribute in sample.attributes
            }
            if sample.contacts != contacts or attributes != {
                "collision_contact": collision_contact,
                "contacts_complete": True,
                "ground_contact": ground_contact,
                "in_air": in_air,
                "landed": landed,
                "landed_state": landed_state,
                "telemetry_source": "gazebo-mavsdk",
            }:
                raise Px4ProviderError(
                    "PX4 motion sample contact metadata differs from receipt evidence"
                )

    def _parse_receipt(
        self,
        response: Mapping[str, Any],
        *,
        target: SimulationTime,
        operation: str,
        require_state: bool,
    ) -> StepReceipt:
        try:
            receipt = StepReceipt.model_validate(response.get("receipt"))
        except ValidationError as exc:
            raise Px4ProviderError(
                f"PX4 {operation} response does not contain a valid StepReceipt"
            ) from exc
        if receipt.run_id != self._run_id:
            raise Px4ProviderError("PX4 StepReceipt run identity mismatch")
        if receipt.provider_id != self._config.provider_id:
            raise Px4ProviderError("PX4 StepReceipt provider identity mismatch")
        if receipt.reached != target:
            raise Px4ProviderError(
                "PX4 backend did not reach the requested sim_time_ns"
            )
        if require_state:
            self._validate_state_events(
                receipt,
                target=target,
                vehicles=tuple(vehicle.vehicle_id for vehicle in self._config.vehicles),
            )
        return receipt

    async def reset(self, *, seed: int) -> StepReceipt:
        self._require_prepared()
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("PX4 reset seed must be an integer")
        response = await self._request(
            "reset",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "seed": seed,
            },
        )
        receipt = self._parse_receipt(
            response,
            target=SimulationTime(tick=0, sim_time_ns=0),
            operation="reset",
            require_state=True,
        )
        self._pending_public_sensor_frames = {}
        self._observed_public_sensor_frames = {}
        self._validate_public_sensor_frame_events(
            receipt,
            target=SimulationTime(tick=0, sim_time_ns=0),
        )
        self._last_time = receipt.reached
        self._clear_command_tracking(clear_failure_latch=True)
        self._pending_commands_by_vehicle.clear()
        self._failure_latched_by_vehicle.clear()
        self._consumed_terminal_ack_seq_by_vehicle = {}
        return receipt

    @staticmethod
    def _motion_contribution(
        request: MotionStageRequest,
        samples: tuple[StateSample, ...],
    ) -> SceneContribution:
        fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": request.run_id,
            "scenario_digest": request.scenario_digest,
            "at": request.target,
            "stage": "motion",
            "provider_id": request.provider_id,
            "samples": samples,
            "attribute_updates": (),
        }
        payload_candidate = SceneContribution.model_construct(
            **fields,
            payload_digest="0" * 64,
            contribution_digest="0" * 64,
        )
        payload_digest = scene_contribution_payload_digest_value(payload_candidate)
        contribution_candidate = SceneContribution.model_construct(
            **fields,
            payload_digest=payload_digest,
            contribution_digest="0" * 64,
        )
        return SceneContribution(
            **fields,
            payload_digest=payload_digest,
            contribution_digest=scene_contribution_digest_value(contribution_candidate),
        )

    async def step_stage(self, request: ProviderStageRequest) -> MotionStageResult:
        self._require_prepared()
        if not isinstance(request, MotionStageRequest):
            raise Px4ProviderError("PX4 accepts only MotionStageRequest")
        try:
            canonical_request = MotionStageRequest.model_validate(
                request.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise Px4ProviderError("PX4 motion stage request is invalid") from exc
        if canonical_request != request:
            raise Px4ProviderError("PX4 motion stage request is not canonical")
        if request.run_id != self._run_id:
            raise Px4ProviderError("PX4 motion stage belongs to a different run")
        if request.scenario_digest != self._scenario.scenario_digest:
            raise Px4ProviderError("PX4 motion stage belongs to a different scenario")
        if request.provider_id != self._config.provider_id:
            raise Px4ProviderError("PX4 motion stage names another provider")
        if self._last_time is None:
            raise Px4ProviderError("PX4 reset must precede step_stage")
        expected = SimulationTime(
            tick=self._last_time.tick + 1,
            sim_time_ns=self._last_time.sim_time_ns + self._clock.step_ns,
        )
        if request.target != expected:
            raise Px4ProviderError(
                "PX4 motion stage target must equal the next configured barrier"
            )
        response = await self._request(
            "step_stage",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": canonical_request.model_dump(mode="json"),
            },
        )
        try:
            staged = Px4MotionStageResponse.model_validate(response)
            canonical_staged = Px4MotionStageResponse.model_validate(
                staged.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise Px4ProviderError("PX4 motion stage response is invalid") from exc
        if staged != canonical_staged or _canonical_json_bytes(
            staged.model_dump(mode="json")
        ) != _canonical_json_bytes(dict(response)):
            raise Px4ProviderError("PX4 motion stage response is not canonical")
        if (
            staged.run_id != request.run_id
            or staged.scenario_digest != request.scenario_digest
            or staged.provider_id != request.provider_id
            or staged.target != request.target
        ):
            raise Px4ProviderError("PX4 motion stage response binding is inconsistent")
        receipt = self._parse_receipt(
            {"receipt": staged.receipt.model_dump(mode="json")},
            target=request.target,
            operation="step_stage",
            require_state=True,
        )
        expected_entity_ids = tuple(
            sorted(vehicle.vehicle_id for vehicle in self._config.vehicles)
        )
        sample_entity_ids = tuple(sample.entity_id for sample in staged.samples)
        if sample_entity_ids != expected_entity_ids:
            raise Px4ProviderError(
                "PX4 motion samples do not close over owned dynamic entities"
            )
        for sample in staged.samples:
            if (
                sample.run_id != request.run_id
                or sample.scenario_digest != request.scenario_digest
                or sample.at != request.target
                or sample.stage != "motion"
                or sample.provider_id != request.provider_id
                or sample.sample_kind != "dynamic"
            ):
                raise Px4ProviderError("PX4 motion sample binding is inconsistent")
        self._validate_motion_samples_against_receipt(staged.samples, receipt)
        self._validate_public_sensor_frame_events(
            receipt,
            target=request.target,
        )
        contribution = self._motion_contribution(request, staged.samples)
        result = MotionStageResult(
            schema_version="aero-bench.provider-stage-result/v1",
            run_id=request.run_id,
            scenario_digest=request.scenario_digest,
            provider_id=request.provider_id,
            target=request.target,
            stage="motion",
            step_receipt=receipt,
            step_receipt_digest=step_receipt_digest_value(receipt),
            contribution=contribution,
            predecessor_barriers=(),
        )
        self._validate_deferred_command_events(receipt)
        self._pending_public_sensor_frames = {}
        self._last_time = receipt.reached
        return result

    @staticmethod
    def _validate_command_receipts(
        receipts: tuple[CommandReceipt, ...],
        request: CommandRequest,
        provider_id: str,
    ) -> None:
        if not receipts:
            raise Px4ProviderError("PX4 command returned no command receipts")
        for receipt in receipts:
            if receipt.run_id != request.run_id:
                raise Px4ProviderError("PX4 command receipt belongs to another run")
            if receipt.command_id != request.command_id:
                raise Px4ProviderError("PX4 command receipt has the wrong command_id")
            if receipt.provider_id != provider_id:
                raise Px4ProviderError("PX4 command receipt has the wrong provider_id")
        phases = [receipt.phase for receipt in receipts]
        allowed = {
            ("received", "failed"),
            ("received", "accepted", "failed"),
            ("received", "accepted"),
        }
        if tuple(phases) not in allowed:
            raise Px4ProviderError(
                "PX4 immediate command phases must be staging-only or an ordered failure"
            )

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        self._require_prepared()
        if request.run_id != self._run_id:
            raise Px4ProviderError("PX4 command request belongs to a different run")
        if self._last_time is None:
            raise Px4ProviderError("PX4 reset must precede command")
        arguments = self._command_argument_map(request)
        vehicle_id = arguments.get("vehicle_id")
        if not isinstance(vehicle_id, str):
            raise Px4ProviderError(
                "PX4 command must bind a string vehicle_id argument"
            )
        configured_vehicle_ids = {vehicle.vehicle_id for vehicle in self._config.vehicles}
        if vehicle_id not in configured_vehicle_ids:
            raise Px4ProviderError(
                f"PX4 command vehicle_id {vehicle_id!r} is not declared"
            )
        if vehicle_id in self._failure_latched_by_vehicle:
            raise Px4ProviderError(
                f"PX4 command lifecycle for {vehicle_id} is failure-latched until reset"
            )
        if vehicle_id in self._pending_commands_by_vehicle:
            raise Px4ProviderError(
                f"PX4 already has an unresolved deferred command for {vehicle_id}"
            )
        response = await self._request(
            "command",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": request.model_dump(mode="json"),
            },
        )
        raw_receipts = response.get("receipts")
        if not isinstance(raw_receipts, list):
            raise Px4ProviderError("PX4 command response receipts must be a JSON array")
        try:
            receipts = tuple(
                CommandReceipt.model_validate(item) for item in raw_receipts
            )
        except ValidationError as exc:
            raise Px4ProviderError(
                "PX4 command response contains an invalid receipt"
            ) from exc
        self._validate_command_receipts(receipts, request, self._config.provider_id)
        raw_events = response.get("events")
        if not isinstance(raw_events, list):
            raise Px4ProviderError("PX4 command response events must be a JSON array")
        try:
            events = tuple(ProviderEvent.model_validate(event) for event in raw_events)
        except ValidationError as exc:
            raise Px4ProviderError(
                "PX4 command response contains an invalid event"
            ) from exc
        if events:
            raise Px4ProviderError(
                "PX4 staged command RPC cannot emit deferred events before the next motion stage"
            )
        phases = tuple(receipt.phase for receipt in receipts)
        if phases == ("received", "accepted"):
            self._unresolved_command_id = request.command_id
            self._unresolved_command_tool_id = request.tool_id
            self._unresolved_command_vehicle_id = vehicle_id
            self._unresolved_command_stage = "accepted"
            self._unresolved_command_accepted_at = self._last_time
            self._unresolved_command_applied_at = None
            self._unresolved_command_audit_digest = None
            self._pending_commands_by_vehicle[vehicle_id] = self._capture_active_command(
                vehicle_id=vehicle_id
            )
            self._sync_command_compatibility_view()
        return ProviderCommandResult(receipts=receipts, events=events)

    async def observe(
        self,
        *,
        run_id: str,
        agent_id: str,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        self._require_prepared()
        bindings = tuple(
            binding
            for binding in self._scenario.task.observations
            if binding.endpoint_id == self._config.provider_id
            and binding.observation_id == observation_id
        )
        if len(bindings) != 1:
            raise Px4ProviderError(
                "PX4 observation is not uniquely bound by ResolvedScenario"
            )
        binding = bindings[0]
        if run_id != self._run_id or requested_at != self._last_time:
            raise Px4ProviderError("PX4 observation identity or time mismatch")
        response = await self._request(
            "observe",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "agent_id": agent_id,
                "observation_id": observation_id,
                "requested_at": requested_at.model_dump(mode="json"),
            },
        )
        try:
            envelope = ObservationEnvelope.model_validate(response.get("observation"))
        except ValidationError as exc:
            raise Px4ProviderError("PX4 observation response is invalid") from exc
        if (
            envelope.run_id != self._run_id
            or envelope.agent_id != agent_id
            or envelope.observation_id != observation_id
            or envelope.time != requested_at
            or envelope.payload_schema != binding.schema_file
        ):
            raise Px4ProviderError("PX4 observation envelope identity is invalid")
        observation_payload = {item.name: item.value for item in envelope.payload}
        if (
            len(observation_payload) != len(envelope.payload)
            or _canonical_digest(observation_payload) != envelope.payload_digest
        ):
            raise Px4ProviderError(
                "PX4 observation payload does not bind its canonical digest"
            )

        flight_projection = binding.flight
        if flight_projection is not None:
            expected_schema, model = {
                "telemetry": (
                    FLIGHT_TELEMETRY_OBSERVATION_SCHEMA,
                    FlightTelemetryObservation,
                ),
                "gnss": (
                    FLIGHT_GNSS_OBSERVATION_SCHEMA,
                    FlightGnssObservation,
                ),
            }[flight_projection.observation_kind]
            try:
                parsed_flight = model.model_validate(observation_payload)
            except ValidationError as exc:
                raise Px4ProviderError(
                    "PX4 flight observation payload is invalid"
                ) from exc
            if (
                parsed_flight.schema_version != expected_schema
                or parsed_flight.vehicle_id != flight_projection.vehicle_id
                or parsed_flight.simulation_time_ns != requested_at.sim_time_ns
                or flight_projection.observation_id != observation_id
            ):
                raise Px4ProviderError(
                    "PX4 flight observation identity or time is invalid"
                )
            return envelope

        urban_projection = binding.urban
        urban_kind = (
            urban_projection.observation_kind if urban_projection is not None else None
        )
        if urban_projection is not None and urban_kind == "camera":
            raise Px4ProviderError(
                "urban recovery does not expose backend RGB observations"
            )
        telemetry_payload_fields = {
            "schema_version",
            "vehicle_id",
            "simulation_time_ns",
            "pose_json",
            "position_wgs84_json",
            "velocity_json",
            "angular_velocity_json",
            "attitude_json",
            "flight_mode",
            "armed",
            "in_air",
            "landed",
            "landed_state",
            "battery_percent",
            "health",
            "contacts_json",
            "ground_contact",
            "collision_contact",
        }
        safety_payload_fields = {
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
        if urban_projection is not None:
            expected_payload_fields = {
                "telemetry": telemetry_payload_fields,
                "safety": safety_payload_fields,
            }.get(str(urban_kind), frozenset())
            expected_schema = {
                "telemetry": "aero-bench.observation.urban-telemetry.v1",
                "safety": "aero-bench.observation.urban-safety.v1",
            }.get(str(urban_kind))
            if (
                set(observation_payload) != expected_payload_fields
                or observation_payload.get("schema_version") != expected_schema
                or urban_projection.observation_id != observation_id
            ):
                raise Px4ProviderError(
                    "PX4 urban recovery observation payload is invalid"
                )
            expected_vehicle_id = urban_projection.vehicle_id
            if (
                observation_payload.get("vehicle_id") != expected_vehicle_id
                or observation_payload.get("simulation_time_ns")
                != requested_at.sim_time_ns
            ):
                raise Px4ProviderError(
                    "PX4 urban recovery UAV observation identity or time is invalid"
                )
            if urban_kind == "safety":
                engine_time = observation_payload.get("engine_sim_time_ns")
                origin = observation_payload.get("logical_origin_engine_ns")
                transition_time = observation_payload.get("transition_engine_sim_time_ns")
                if (
                    observation_payload.get("source") != "gazebo.system"
                    or type(engine_time) is not int or type(origin) is not int
                    or origin < 0 or engine_time - origin != requested_at.sim_time_ns
                ):
                    raise Px4ProviderError(
                        "PX4 urban safety observation is not engine-bound"
                    )
                if observation_payload.get("transition") == "none":
                    if transition_time is not None or observation_payload.get("transition_sequence") != 0:
                        raise Px4ProviderError("PX4 urban safety non-event contains transition identity")
                elif (
                    type(transition_time) is not int
                    or not max(origin, engine_time - self._clock.step_ns) < transition_time <= engine_time
                ):
                    raise Px4ProviderError("PX4 urban safety transition is outside its engine window")
            return envelope

        inspection_projection = binding.inspection
        if inspection_projection is None:
            raise Px4ProviderError(
                "PX4 observation has no supported scenario projection"
            )
        try:
            rgb = InspectionRgbObservation.model_validate(observation_payload)
        except ValidationError as exc:
            raise Px4ProviderError(
                "PX4 inspection RGB observation payload is invalid"
            ) from exc
        expected_engine_time_ns = (
            rgb.logical_origin_engine_ns + requested_at.sim_time_ns
        )
        if (
            rgb.schema_version != RGB_OBSERVATION_SCHEMA
            or inspection_projection.observation_id != observation_id
            or rgb.target_id != inspection_projection.target_id
            or rgb.camera_id != inspection_projection.sensor_id
            or rgb.engine_sim_time_ns != expected_engine_time_ns
        ):
            raise Px4ProviderError(
                "PX4 inspection RGB observation identity or time is invalid"
            )

        sensor_frame_requirements = tuple(
            requirement
            for requirement in self._manifest.artifact_requirements
            if requirement.artifact_type == SENSOR_FRAME_ARTIFACT_TYPE
        )
        if (
            len(sensor_frame_requirements) != 1
            or sensor_frame_requirements[0].visibility != "public"
            or sensor_frame_requirements[0].producer_id != self._config.provider_id
        ):
            raise Px4ProviderError(
                "PX4 observation lacks one declared public sensor-frame artifact"
            )
        frame_data_requirements = tuple(
            requirement
            for requirement in self._manifest.artifact_requirements
            if requirement.artifact_type == CAMERA_FRAME_DATA_ARTIFACT_TYPE
        )
        if (
            len(frame_data_requirements) != 1
            or frame_data_requirements[0].visibility != "public"
            or frame_data_requirements[0].producer_id != self._config.provider_id
        ):
            raise Px4ProviderError(
                "PX4 observation lacks one declared public camera frame-data artifact"
            )
        frame_id = f"frame.{agent_id}.{observation_id}.{requested_at.tick:016d}"
        expected_frame = {
            "artifact_id": frame_data_requirements[0].artifact_id,
            "frame_id": frame_id,
            "observation_id": observation_id,
            "payload_digest": envelope.payload_digest,
            "selector": f"frames/{frame_id}",
            "vehicle_id": rgb.vehicle_id,
            "camera_id": rgb.camera_id,
            "engine_sim_time_ns": rgb.engine_sim_time_ns,
            "logical_origin_engine_ns": rgb.logical_origin_engine_ns,
            "capture_pose_source": rgb.capture_pose_source,
            "camera_pose_sha256": rgb.camera_pose_sha256,
            "target_pose_sha256": rgb.target_pose_sha256,
            "image_sha256": rgb.image_sha256,
            "size_bytes": rgb.size_bytes,
            "width": rgb.width,
            "height": rgb.height,
            "media_type": rgb.media_type,
            "source": rgb.source,
        }
        observation_key = (agent_id, observation_id, requested_at.tick)
        prior_frame = self._observed_public_sensor_frames.get(observation_key)
        if prior_frame is not None:
            if prior_frame != expected_frame:
                raise Px4ProviderError(
                    "PX4 cached public sensor-frame identity changed"
                )
        else:
            if frame_id in self._pending_public_sensor_frames:
                raise Px4ProviderError("PX4 public sensor-frame identity is not unique")
            self._observed_public_sensor_frames[observation_key] = expected_frame
            self._pending_public_sensor_frames[frame_id] = expected_frame
        return envelope

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt:
        self._require_prepared()
        if request.run_id != self._run_id or request.terminal_time != self._last_time:
            raise Px4ProviderError("PX4 finalization identity or time mismatch")
        if self._failure_latched_by_vehicle:
            raise Px4ProviderError(
                "PX4 cannot finalize with a failure-latched deferred command"
            )
        if self._pending_commands_by_vehicle:
            raise Px4ProviderError("PX4 cannot finalize with an unresolved command")
        if self._pending_public_sensor_frames:
            raise Px4ProviderError(
                "PX4 cannot finalize before captured public sensor frames are staged"
            )
        response = await self._request(
            "finalize",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": request.model_dump(mode="json"),
            },
        )
        try:
            receipt = ProviderFinalizationReceipt.model_validate(
                response.get("receipt")
            )
        except ValidationError as exc:
            raise Px4ProviderError("PX4 finalization response is invalid") from exc
        expected = {
            requirement.artifact_id: requirement
            for requirement in self._manifest.artifact_requirements
        }
        actual: dict[str, FinalizedArtifact] = {
            artifact.artifact_id: artifact for artifact in receipt.artifacts
        }
        if (
            receipt.run_id != self._run_id
            or receipt.provider_id != self._config.provider_id
            or receipt.event_chain_root != request.event_chain_root
            or set(actual) != set(expected)
            or any(
                artifact.size_bytes > expected[artifact_id].max_size_bytes
                for artifact_id, artifact in actual.items()
            )
        ):
            raise Px4ProviderError("PX4 finalization receipt identity is invalid")
        return receipt

    async def snapshot_digest(self) -> str:
        self._require_prepared()
        response = await self._request(
            "snapshot",
            {"provider_id": self._config.provider_id, "run_id": self._run_id},
        )
        try:
            snapshot = Px4SnapshotResponse.model_validate(response)
        except ValidationError as exc:
            raise Px4ProviderError("PX4 snapshot response has no valid digest") from exc
        return TypeAdapter(Sha256).validate_python(snapshot.snapshot_digest)

    async def shutdown(self) -> None:
        if self._transport is None:
            return
        try:
            if self._prepared:
                response = await self._request(
                    "shutdown",
                    {
                        "provider_id": self._config.provider_id,
                        "run_id": self._run_id,
                    },
                )
                if response.get("status") != "stopped":
                    raise Px4ProviderError("PX4 provider did not acknowledge shutdown")
        finally:
            await self._transport.close()
            self._prepared = False
            self._transport = None
            self._last_time = None
            self._clear_command_tracking(clear_failure_latch=True)
