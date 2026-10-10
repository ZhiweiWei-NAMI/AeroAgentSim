from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import re
import signal
import stat
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from workload_scenario import (
    ValidatedWorkloadScenario,
    WorkloadScenarioError,
    validate_workload_scenario,
)


NS3_ROOT = Path("/opt/ns-3.48")
NS3_VERSION = "3.48"
NS3_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
PROTOCOL_VERSION = "aero-bench.ns3-rpc/v5"
STATE_SCHEMA = "ns3.state.v4"
NETWORK_MODEL = "wifi-adhoc-scene-mobility/v1"
NETWORK_MODEL_CAPABILITY = "network.wifi-scene-mobility"
MAILBOX_CAPABILITY = "network.agent-mailbox"
DELIVERY_EVENT_SCHEMA = "inspection.network-delivery.v1"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
PROVIDER_ADAPTER = "ns3.rpc"
SUPPORTED_WIFI_STANDARDS = ("802.11ac", "802.11ax", "802.11n")
SUPPORTED_QOS = {
    "traffic_class": "best_effort",
    "priority": 0,
    "reliability": "best_effort",
}
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_NETWORK_PAYLOAD_BYTES = 60 * 1024
MAX_LINKS = 253
MAX_NODES = MAX_LINKS + 1
MAX_RADIO_PROFILES = MAX_NODES
MAX_SIMULATION_TIME_NS = 2**63 - 1
MAX_LINK_DELAY_NS = MAX_SIMULATION_TIME_NS
MAX_POSITION_METRES = 1_000_000_000.0
MAX_COMMAND_TIMEOUT_MS = 600_000
BUILDING_ATTENUATION_DB = 12.0
LOG_DISTANCE_EXPONENT = 3.0
RX_NOISE_FIGURE_DB = 7.0
THERMAL_NOISE_DENSITY_DBM_HZ = -174.0
PROPAGATION_MODEL = "log-distance-with-scene-volumes/v1"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE_IMAGE = re.compile(
    r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$"
)
DECIMAL_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")


class ProviderError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class Ns3ConfigIdentity(NamedTuple):
    config_digest: str
    network_model: str
    supported_wifi_standards: tuple[str, ...]


class Ns3ClockIdentity(NamedTuple):
    step_ns: int
    max_steps: int
    provider_timeout_ms: int


class WifiRadioProfile(NamedTuple):
    radio_profile_id: str
    provider_id: str
    wifi_standard: str
    frequency_ghz: float
    frequency_mhz: int
    channel_number: int
    channel_width_mhz: int
    band: str
    tx_power_dbm: float
    rx_sensitivity_dbm: float
    data_mode: str
    control_mode: str
    max_data_rate_bps: int


class NetworkNodeBinding(NamedTuple):
    node_id: str
    entity_id: str
    endpoint_id: str
    radio_profile_id: str
    position_enu_m: tuple[float, float, float]


class NetworkLinkBinding(NamedTuple):
    link_id: str
    source_node_id: str
    destination_node_id: str
    data_rate_bps: int
    propagation_delay_ns: int


class PropagationVolume(NamedTuple):
    volume_id: str
    kind: str
    polygon_enu_m: tuple[tuple[float, float], ...]
    min_up_m: float
    max_up_m: float
    attenuation_db: float


class Ns3NetworkProjection(NamedTuple):
    provider_id: str
    radio_profiles: tuple[WifiRadioProfile, ...]
    node_bindings: tuple[NetworkNodeBinding, ...]
    links: tuple[NetworkLinkBinding, ...]
    propagation_volumes: tuple[PropagationVolume, ...]
    projection_digest: str


class Ns3WorkloadIdentity(NamedTuple):
    run_id: str
    provider_id: str
    adapter: str
    runtime_image: str
    config: Ns3ConfigIdentity
    clock: Ns3ClockIdentity
    scenario_digest: str
    scenario: ValidatedWorkloadScenario
    network: Ns3NetworkProjection
    artifact_requirement: dict[str, Any]

    @property
    def config_digest(self) -> str:
        return self.config.config_digest


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def parse_json(frame: bytes) -> dict[str, Any]:
    if not frame.endswith(b"\n"):
        raise ProviderError("request.invalid", "request frame must end with a newline")
    try:
        value = json.loads(
            frame,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProviderError("request.invalid", "request is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ProviderError("request.invalid", "request must be a JSON object")
    return value


def encode_json(value: dict[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise ProviderError("response.invalid", "response cannot be encoded") from exc


def require_object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProviderError("request.invalid", f"{label} fields are not exact")
    return value


def require_string(
    value: Any, *, label: str, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderError("request.invalid", f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ProviderError("request.invalid", f"{label} has an invalid format")
    return value


def require_integer(
    value: Any,
    *,
    label: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        maximum_text = "" if maximum is None else f" and <= {maximum}"
        raise ProviderError(
            "request.invalid",
            f"{label} must be an integer >= {minimum}{maximum_text}",
        )
    return value


def require_number(
    value: Any,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError("request.invalid", f"{label} must be a finite number")
    result = float(value)
    if (
        not math.isfinite(result)
        or (minimum is not None and result < minimum)
        or (maximum is not None and result > maximum)
    ):
        raise ProviderError(
            "request.invalid", f"{label} is outside its finite numeric range"
        )
    return result


def require_float(
    value: Any,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ProviderError("request.invalid", f"{label} must be a finite float")
    if (minimum is not None and value < minimum) or (
        maximum is not None and value > maximum
    ):
        raise ProviderError(
            "request.invalid", f"{label} is outside its finite numeric range"
        )
    return value


def parse_backend_float(
    value: str,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise ProviderError("backend.invalid", f"{label} is not numeric") from exc
    if (
        not math.isfinite(result)
        or (minimum is not None and result < minimum)
        or (maximum is not None and result > maximum)
    ):
        raise ProviderError("backend.invalid", f"{label} is outside its finite range")
    return result


def require_sha256(value: Any, *, label: str) -> str:
    return require_string(value, label=label, pattern=SHA256)


def require_session_token(payload: Any, expected: str) -> None:
    presented = payload.get("session_token") if isinstance(payload, dict) else None
    valid = (
        isinstance(presented, str)
        and SHA256.fullmatch(presented) is not None
        and presented != "0" * 64
    )
    candidate = presented if valid else "0" * 64
    if not valid or not hmac.compare_digest(candidate, expected):
        raise ProviderError(
            "principal.denied",
            "ns-3 request cannot present the executor-issued run-scoped session token",
        )


def require_base64(
    value: Any, *, label: str, maximum_bytes: int | None = None
) -> str:
    value = require_string(value, label=label)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ProviderError("request.invalid", f"{label} is not strict base64") from exc
    if not decoded or base64.b64encode(decoded).decode("ascii") != value:
        raise ProviderError("request.invalid", f"{label} is not canonical base64")
    if maximum_bytes is not None and len(decoded) > maximum_bytes:
        raise ProviderError(
            "request.invalid", f"{label} exceeds {maximum_bytes} decoded bytes"
        )
    return value


def _relative_artifact_path(value: Any) -> PurePosixPath:
    value = require_string(value, label="artifact.relative_path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or value.strip() != value
        or str(path) != value
        or "\\" in value
    ):
        raise ProviderError(
            "request.invalid", "artifact.relative_path must be normalized and relative"
        )
    return path


def require_artifact_requirement(value: Any, *, provider_id: str) -> dict[str, Any]:
    requirement = require_object(
        value,
        fields={
            "artifact_id",
            "artifact_type",
            "producer_id",
            "visibility",
            "relative_path",
            "max_size_bytes",
            "source_asset_id",
        },
        label="artifact requirement",
    )
    artifact_id = require_string(
        requirement["artifact_id"], label="artifact.artifact_id", pattern=IDENTIFIER
    )
    artifact_type = require_string(
        requirement["artifact_type"],
        label="artifact.artifact_type",
        pattern=IDENTIFIER,
    )
    producer = require_string(
        requirement["producer_id"], label="artifact.producer_id", pattern=IDENTIFIER
    )
    if producer != provider_id:
        raise ProviderError(
            "identity.mismatch", "artifact.producer_id does not match provider_id"
        )
    visibility = requirement["visibility"]
    if visibility not in {"public", "private"}:
        raise ProviderError("request.invalid", "artifact.visibility is invalid")
    relative_path = _relative_artifact_path(requirement["relative_path"])
    max_size_bytes = require_integer(
        requirement["max_size_bytes"], label="artifact.max_size_bytes", minimum=1
    )
    source_asset_id = requirement["source_asset_id"]
    if source_asset_id is not None:
        source_asset_id = require_string(
            source_asset_id, label="artifact.source_asset_id", pattern=IDENTIFIER
        )
    if artifact_type != "network.delivery":
        raise ProviderError(
            "request.unsupported",
            "ns-3 provider emits only the declared network.delivery artifact",
        )
    if source_asset_id is not None:
        raise ProviderError(
            "request.unsupported",
            "ns-3 network.delivery source_asset_id must be null",
        )
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_id": producer,
        "visibility": visibility,
        "relative_path": str(relative_path),
        "max_size_bytes": max_size_bytes,
        "source_asset_id": source_asset_id,
    }


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class SceneStageInput(NamedTuple):
    scene_state_digest: str
    motion_barrier_digest: str
    node_positions_enu_m: tuple[tuple[str, float, float, float], ...]
    link_attenuations_db: tuple[tuple[str, float], ...]
    link_obstruction_volume_ids: tuple[tuple[str, tuple[str, ...]], ...]


class LinkRuntimeState(NamedTuple):
    link_id: str
    source_node_id: str
    destination_node_id: str
    distance_m: float
    base_path_loss_db: float
    obstruction_loss_db: float
    forward_rssi_dbm: float
    reverse_rssi_dbm: float
    forward_snr_db: float
    reverse_snr_db: float
    source_queue_packets: int
    destination_queue_packets: int
    source_queue_bytes: int
    destination_queue_bytes: int


def require_array(value: Any, *, label: str, minimum: int = 0) -> list[Any]:
    if type(value) is not list or len(value) < minimum:
        raise ProviderError("request.invalid", f"{label} must be an array")
    return value


def require_non_placeholder_sha256(value: Any, *, label: str) -> str:
    result = require_sha256(value, label=label)
    if result == "0" * 64:
        raise ProviderError("request.invalid", f"{label} cannot be a placeholder")
    return result


def require_boolean(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise ProviderError("request.invalid", f"{label} must be a boolean")
    return value


def require_simulation_time(value: Any, *, label: str) -> tuple[int, int]:
    time = require_object(value, fields={"tick", "sim_time_ns"}, label=label)
    return (
        require_integer(time["tick"], label=f"{label}.tick"),
        require_integer(
            time["sim_time_ns"],
            label=f"{label}.sim_time_ns",
            maximum=MAX_SIMULATION_TIME_NS,
        ),
    )


def require_sorted_identifiers(
    value: Any, *, label: str, minimum: int = 0
) -> tuple[str, ...]:
    values = tuple(
        require_string(item, label=f"{label}[{index}]", pattern=IDENTIFIER)
        for index, item in enumerate(require_array(value, label=label, minimum=minimum))
    )
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ProviderError(
            "request.invalid", f"{label} must be sorted and unique"
        )
    return values


def require_state_attributes(value: Any, *, label: str) -> tuple[str, ...]:
    names: list[str] = []
    for index, item in enumerate(require_array(value, label=label)):
        attribute = require_object(
            item,
            fields={"name", "value_type", "value"},
            label=f"{label}[{index}]",
        )
        name = require_string(
            attribute["name"], label=f"{label}[{index}].name", pattern=IDENTIFIER
        )
        value_type = require_string(
            attribute["value_type"], label=f"{label}[{index}].value_type"
        )
        raw_value = attribute["value"]
        if raw_value is None:
            actual_type = "null"
        elif type(raw_value) is bool:
            actual_type = "bool"
        elif type(raw_value) is int:
            actual_type = "int"
        elif type(raw_value) is float:
            require_float(raw_value, label=f"{label}[{index}].value")
            actual_type = "float"
        elif type(raw_value) is str:
            actual_type = "str"
        else:
            raise ProviderError(
                "request.invalid", f"{label}[{index}].value is not a JSON scalar"
            )
        if value_type not in {"bool", "int", "float", "str", "null"}:
            raise ProviderError(
                "request.invalid", f"{label}[{index}].value_type is invalid"
            )
        if value_type != actual_type:
            raise ProviderError(
                "request.invalid", f"{label}[{index}] value type is inconsistent"
            )
        names.append(name)
    result = tuple(names)
    if result != tuple(sorted(result)) or len(result) != len(set(result)):
        raise ProviderError("request.invalid", f"{label} must be sorted and unique")
    return result


def require_optional_number(value: Any, *, label: str) -> None:
    if value is not None:
        require_float(value, label=label)


def require_battery(value: Any, *, label: str) -> None:
    if value is None:
        return
    battery = require_object(
        value,
        fields={
            "voltage_v",
            "current_a",
            "remaining_fraction",
            "consumed_mah",
            "temperature_c",
            "attributes",
        },
        label=label,
    )
    for field in ("voltage_v", "current_a", "consumed_mah", "temperature_c"):
        require_optional_number(battery[field], label=f"{label}.{field}")
    remaining = battery["remaining_fraction"]
    if remaining is not None:
        require_float(
            remaining,
            label=f"{label}.remaining_fraction",
            minimum=0.0,
            maximum=1.0,
        )
    attributes = require_state_attributes(battery["attributes"], label=f"{label}.attributes")
    if (
        battery["voltage_v"] is None
        and battery["current_a"] is None
        and battery["remaining_fraction"] is None
        and battery["consumed_mah"] is None
        and battery["temperature_c"] is None
        and not attributes
    ):
        raise ProviderError("request.invalid", f"{label} must contain a value")


def require_health(value: Any, *, label: str) -> None:
    if value is None:
        return
    health = require_object(value, fields={"healthy", "attributes"}, label=label)
    healthy = health["healthy"]
    if healthy is not None:
        require_boolean(healthy, label=f"{label}.healthy")
    attributes = require_state_attributes(health["attributes"], label=f"{label}.attributes")
    if healthy is None and not attributes:
        raise ProviderError("request.invalid", f"{label} must contain a value")


def require_vector(
    value: Any, *, label: str, fields: set[str]
) -> dict[str, float]:
    vector = require_object(value, fields=fields, label=label)
    return {
        name: require_float(vector[name], label=f"{label}.{name}") for name in fields
    }


def require_quaternion(value: Any, *, label: str) -> None:
    quaternion = require_vector(
        value, label=label, fields={"qw", "qx", "qy", "qz"}
    )
    qw, qx, qy, qz = (
        quaternion["qw"],
        quaternion["qx"],
        quaternion["qy"],
        quaternion["qz"],
    )
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if norm <= 0.0 or abs(norm - 1.0) > 1e-9:
        raise ProviderError("request.invalid", f"{label} must be a unit quaternion")
    is_negative = lambda item: math.copysign(1.0, item) < 0.0
    if qw < 0.0 or (
        qw == 0.0
        and (
            is_negative(qx)
            or (qx == 0.0 and (is_negative(qy) or (qy == 0.0 and is_negative(qz))))
        )
    ):
        raise ProviderError("request.invalid", f"{label} must use canonical sign")


def require_velocity(
    value: Any,
    *,
    label: str,
    frame: str,
    components: set[str],
) -> dict[str, float]:
    velocity = require_object(value, fields={"frame_id", *components}, label=label)
    if velocity["frame_id"] != frame:
        raise ProviderError("request.invalid", f"{label}.frame_id must be {frame}")
    return {
        component: require_float(
            velocity[component], label=f"{label}.{component}"
        )
        for component in components
    }


def require_pose(value: Any, *, label: str) -> tuple[float, float, float]:
    pose = require_object(
        value,
        fields={"position", "orientation_enu", "orientation_ned"},
        label=label,
    )
    position = require_object(
        pose["position"],
        fields={
            "enu",
            "ned",
            "ecef",
            "wgs84",
            "geoid_separation_m",
            "amsl_m",
            "terrain_amsl_m",
            "agl_m",
        },
        label=f"{label}.position",
    )
    enu = require_vector(
        position["enu"],
        label=f"{label}.position.enu",
        fields={"east_m", "north_m", "up_m"},
    )
    require_vector(
        position["ned"],
        label=f"{label}.position.ned",
        fields={"north_m", "east_m", "down_m"},
    )
    require_vector(
        position["ecef"],
        label=f"{label}.position.ecef",
        fields={"x_m", "y_m", "z_m"},
    )
    wgs84 = require_vector(
        position["wgs84"],
        label=f"{label}.position.wgs84",
        fields={"longitude_deg", "latitude_deg", "ellipsoid_height_m"},
    )
    if not -180.0 <= wgs84["longitude_deg"] < 180.0 or not -90.0 < wgs84[
        "latitude_deg"
    ] < 90.0:
        raise ProviderError("request.invalid", f"{label}.position.wgs84 is out of range")
    for field in ("geoid_separation_m", "amsl_m", "terrain_amsl_m", "agl_m"):
        require_float(position[field], label=f"{label}.position.{field}")
    for orientation in ("orientation_enu", "orientation_ned"):
        require_quaternion(pose[orientation], label=f"{label}.{orientation}")
    return (enu["east_m"], enu["north_m"], enu["up_m"])


def require_motion_stage_receipt(
    value: Any,
    *,
    label: str,
    run_id: str,
    scenario_digest: str,
    target: tuple[int, int],
    provider_id: str,
) -> tuple[str, str]:
    receipt = require_object(
        value,
        fields={
            "schema_version",
            "run_id",
            "scenario_digest",
            "at",
            "stage",
            "provider_id",
            "state_digest",
            "step_receipt_digest",
            "contribution_digest",
            "payload_digest",
            "input_scene_state_digest",
            "predecessor_barriers",
            "receipt_digest",
        },
        label=label,
    )
    if receipt["schema_version"] != "aero-bench.stage-receipt/v1":
        raise ProviderError("request.invalid", f"{label}.schema_version is unsupported")
    if require_sha256(receipt["run_id"], label=f"{label}.run_id") != run_id:
        raise ProviderError("identity.mismatch", f"{label} run identity differs")
    if (
        require_sha256(receipt["scenario_digest"], label=f"{label}.scenario_digest")
        != scenario_digest
    ):
        raise ProviderError("identity.mismatch", f"{label} scenario identity differs")
    if require_simulation_time(receipt["at"], label=f"{label}.at") != target:
        raise ProviderError("state.invalid", f"{label} time differs")
    if receipt["stage"] != "motion":
        raise ProviderError("request.invalid", f"{label}.stage must be motion")
    if require_string(
        receipt["provider_id"], label=f"{label}.provider_id", pattern=IDENTIFIER
    ) != provider_id:
        raise ProviderError("identity.mismatch", f"{label} provider differs")
    for field in (
        "state_digest",
        "step_receipt_digest",
        "contribution_digest",
        "payload_digest",
    ):
        require_non_placeholder_sha256(receipt[field], label=f"{label}.{field}")
    if receipt["input_scene_state_digest"] is not None:
        raise ProviderError("request.invalid", f"{label} cannot consume a SceneState")
    if require_array(receipt["predecessor_barriers"], label=f"{label}.predecessor_barriers"):
        raise ProviderError("request.invalid", f"{label} cannot have predecessors")
    receipt_digest = require_non_placeholder_sha256(
        receipt["receipt_digest"], label=f"{label}.receipt_digest"
    )
    try:
        expected = digest(
            {key: item for key, item in receipt.items() if key != "receipt_digest"}
        )
    except (TypeError, ValueError) as exc:
        raise ProviderError("request.invalid", f"{label} is not canonical JSON") from exc
    if receipt_digest != expected:
        raise ProviderError("identity.mismatch", f"{label} digest does not match")
    return receipt_digest, receipt["contribution_digest"]


def require_motion_stage_barrier(
    value: Any,
    *,
    run_id: str,
    scenario_digest: str,
    target: tuple[int, int],
    motion_provider_ids: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    barrier = require_object(
        value,
        fields={
            "schema_version",
            "run_id",
            "scenario_digest",
            "at",
            "stage",
            "input_scene_state_digest",
            "predecessor_barriers",
            "provider_ids",
            "receipts",
            "receipt_digests",
            "barrier_digest",
        },
        label="scene_state.stage_barrier",
    )
    if barrier["schema_version"] != "aero-bench.stage-barrier/v1":
        raise ProviderError("request.invalid", "motion barrier schema_version is unsupported")
    if require_sha256(barrier["run_id"], label="motion barrier.run_id") != run_id:
        raise ProviderError("identity.mismatch", "motion barrier run identity differs")
    if (
        require_sha256(barrier["scenario_digest"], label="motion barrier.scenario_digest")
        != scenario_digest
    ):
        raise ProviderError(
            "identity.mismatch", "motion barrier scenario identity differs"
        )
    if require_simulation_time(barrier["at"], label="motion barrier.at") != target:
        raise ProviderError("state.invalid", "motion barrier time differs")
    if barrier["stage"] != "motion":
        raise ProviderError("request.invalid", "SceneState barrier must be motion")
    if barrier["input_scene_state_digest"] is not None:
        raise ProviderError("request.invalid", "motion barrier cannot consume SceneState")
    if require_array(
        barrier["predecessor_barriers"], label="motion barrier.predecessor_barriers"
    ):
        raise ProviderError("request.invalid", "motion barrier cannot have predecessors")
    provider_ids = require_sorted_identifiers(
        barrier["provider_ids"], label="motion barrier.provider_ids"
    )
    if provider_ids != motion_provider_ids:
        raise ProviderError(
            "identity.mismatch", "motion barrier providers do not close dynamic owners"
        )
    receipts = require_array(barrier["receipts"], label="motion barrier.receipts")
    if len(receipts) != len(provider_ids):
        raise ProviderError("request.invalid", "motion barrier receipt count differs")
    receipt_digests: list[str] = []
    contribution_digests: list[str] = []
    for index, provider_id in enumerate(provider_ids):
        receipt_digest, contribution_digest = require_motion_stage_receipt(
            receipts[index],
            label=f"motion barrier.receipts[{index}]",
            run_id=run_id,
            scenario_digest=scenario_digest,
            target=target,
            provider_id=provider_id,
        )
        receipt_digests.append(receipt_digest)
        contribution_digests.append(contribution_digest)
    declared_receipt_digests = tuple(
        require_non_placeholder_sha256(
            item, label=f"motion barrier.receipt_digests[{index}]"
        )
        for index, item in enumerate(
            require_array(barrier["receipt_digests"], label="motion barrier.receipt_digests")
        )
    )
    if declared_receipt_digests != tuple(receipt_digests):
        raise ProviderError(
            "identity.mismatch", "motion barrier receipt inventory differs"
        )
    if len(contribution_digests) != len(set(contribution_digests)):
        raise ProviderError(
            "request.invalid", "motion barrier contribution digests repeat"
        )
    barrier_digest = require_non_placeholder_sha256(
        barrier["barrier_digest"], label="motion barrier.barrier_digest"
    )
    try:
        expected = digest(
            {key: item for key, item in barrier.items() if key != "barrier_digest"}
        )
    except (TypeError, ValueError) as exc:
        raise ProviderError("request.invalid", "motion barrier is not canonical JSON") from exc
    if barrier_digest != expected:
        raise ProviderError("identity.mismatch", "motion barrier digest does not match")
    return barrier_digest, tuple(sorted(contribution_digests))


def require_state_sample(
    value: Any,
    *,
    label: str,
    run_id: str,
    scenario_digest: str,
    target: tuple[int, int],
    entity: dict[str, Any],
    motion_provider_ids: tuple[str, ...],
) -> dict[str, Any]:
    sample = require_object(
        value,
        fields={
            "schema_version",
            "run_id",
            "scenario_digest",
            "at",
            "stage",
            "entity_id",
            "provider_id",
            "sample_kind",
            "pose",
            "linear_velocity_enu",
            "linear_velocity_ned",
            "angular_velocity_body",
            "mode",
            "armed",
            "battery",
            "health",
            "contacts",
            "attributes",
            "sample_digest",
        },
        label=label,
    )
    if sample["schema_version"] != "aero-bench.state-sample/v1":
        raise ProviderError("request.invalid", f"{label}.schema_version is unsupported")
    if require_sha256(sample["run_id"], label=f"{label}.run_id") != run_id:
        raise ProviderError("identity.mismatch", f"{label} run identity differs")
    if (
        require_sha256(sample["scenario_digest"], label=f"{label}.scenario_digest")
        != scenario_digest
    ):
        raise ProviderError("identity.mismatch", f"{label} scenario identity differs")
    if require_simulation_time(sample["at"], label=f"{label}.at") != target:
        raise ProviderError("state.invalid", f"{label} time differs")
    if sample["stage"] != "motion":
        raise ProviderError("request.invalid", f"{label}.stage must be motion")
    entity_id = require_string(sample["entity_id"], label=f"{label}.entity_id", pattern=IDENTIFIER)
    expected_entity_id = require_string(
        entity["entity_id"], label=f"{label}.expected_entity_id", pattern=IDENTIFIER
    )
    if entity_id != expected_entity_id:
        raise ProviderError("identity.mismatch", f"{label} entity binding differs")
    provider_id = require_string(
        sample["provider_id"], label=f"{label}.provider_id", pattern=IDENTIFIER
    )
    sample_kind = require_string(sample["sample_kind"], label=f"{label}.sample_kind")
    state = require_string(entity["state"], label=f"{label}.expected_state")
    owner_id = require_string(
        entity["owner_id"], label=f"{label}.expected_owner", pattern=IDENTIFIER
    )
    if sample_kind != state or sample_kind not in {"static", "dynamic"}:
        raise ProviderError("identity.mismatch", f"{label} sample kind differs")
    position_enu = require_pose(sample["pose"], label=f"{label}.pose")
    enu = require_velocity(
        sample["linear_velocity_enu"],
        label=f"{label}.linear_velocity_enu",
        frame="ENU",
        components={"east_mps", "north_mps", "up_mps"},
    )
    ned = require_velocity(
        sample["linear_velocity_ned"],
        label=f"{label}.linear_velocity_ned",
        frame="NED",
        components={"north_mps", "east_mps", "down_mps"},
    )
    if (
        ned["north_mps"] != enu["north_mps"]
        or ned["east_mps"] != enu["east_mps"]
        or ned["down_mps"] != -enu["up_mps"]
    ):
        raise ProviderError("request.invalid", f"{label} ENU/NED velocities differ")
    angular = sample["angular_velocity_body"]
    if angular is not None:
        require_velocity(
            angular,
            label=f"{label}.angular_velocity_body",
            frame="body",
            components={"x_radps", "y_radps", "z_radps"},
        )
    if sample["mode"] is not None:
        require_string(sample["mode"], label=f"{label}.mode")
    if sample["armed"] is not None:
        require_boolean(sample["armed"], label=f"{label}.armed")
    require_battery(sample["battery"], label=f"{label}.battery")
    require_health(sample["health"], label=f"{label}.health")
    require_sorted_identifiers(sample["contacts"], label=f"{label}.contacts")
    require_state_attributes(sample["attributes"], label=f"{label}.attributes")
    if sample_kind == "static":
        if provider_id != "scenario.compiler" or owner_id != "scenario.compiler":
            raise ProviderError("identity.mismatch", f"{label} static ownership differs")
        if (
            sample["pose"] != entity["initial_pose"]
            or enu["east_mps"] != 0.0
            or enu["north_mps"] != 0.0
            or enu["up_mps"] != 0.0
            or angular is not None
            or sample["mode"] is not None
            or sample["armed"] is not None
            or sample["battery"] is not None
            or sample["health"] is not None
            or sample["contacts"] != []
            or sample["attributes"] != []
        ):
            raise ProviderError("identity.mismatch", f"{label} static state drifted")
    elif provider_id != owner_id or provider_id not in motion_provider_ids:
        raise ProviderError("identity.mismatch", f"{label} dynamic ownership differs")
    sample_digest = require_non_placeholder_sha256(
        sample["sample_digest"], label=f"{label}.sample_digest"
    )
    try:
        expected_digest = digest(
            {key: item for key, item in sample.items() if key != "sample_digest"}
        )
    except (TypeError, ValueError) as exc:
        raise ProviderError("request.invalid", f"{label} is not canonical JSON") from exc
    if sample_digest != expected_digest:
        raise ProviderError("identity.mismatch", f"{label} digest does not match")
    return {
        "entity_id": entity_id,
        "provider_id": provider_id,
        "sample_kind": sample_kind,
        "position_enu": position_enu,
    }


def validate_scene_stage_input(
    value: Any,
    *,
    run_id: str,
    scenario_digest: str,
    target: tuple[int, int],
    scenario_document: dict[str, Any],
    projection: Ns3NetworkProjection,
) -> SceneStageInput:
    scene_state = require_object(
        value,
        fields={
            "schema_version",
            "run_id",
            "scenario_digest",
            "at",
            "declared_entity_ids",
            "samples",
            "stage_barrier",
            "contribution_digests",
            "previous_scene_state_digest",
            "scene_state_digest",
        },
        label="scene_state",
    )
    if scene_state["schema_version"] != "aero-bench.scene-state/v1":
        raise ProviderError("request.invalid", "scene_state.schema_version is unsupported")
    if require_sha256(scene_state["run_id"], label="scene_state.run_id") != run_id:
        raise ProviderError("identity.mismatch", "scene_state run identity differs")
    if (
        require_sha256(scene_state["scenario_digest"], label="scene_state.scenario_digest")
        != scenario_digest
    ):
        raise ProviderError("identity.mismatch", "scene_state scenario identity differs")
    if require_simulation_time(scene_state["at"], label="scene_state.at") != target:
        raise ProviderError("state.invalid", "scene_state time differs")
    previous_scene_state_digest = require_sha256(
        scene_state["previous_scene_state_digest"],
        label="scene_state.previous_scene_state_digest",
    )
    if target[0] < 1:
        raise ProviderError("state.invalid", "scene_state tick must start at one")
    if target[0] == 1:
        if previous_scene_state_digest != "0" * 64:
            raise ProviderError(
                "state.invalid", "first scene_state must bind the chain root"
            )
    elif previous_scene_state_digest == "0" * 64:
        raise ProviderError(
            "state.invalid", "later scene_state must bind a prior scene state"
        )
    scenario_entities = require_array(
        scenario_document.get("entities"), label="verified scenario.entities", minimum=1
    )
    entities_by_id: dict[str, dict[str, Any]] = {}
    for index, entity in enumerate(scenario_entities):
        if type(entity) is not dict:
            raise ProviderError("state.invalid", "verified scenario entity is malformed")
        entity_id = require_string(
            entity.get("entity_id"),
            label=f"verified scenario.entities[{index}].entity_id",
            pattern=IDENTIFIER,
        )
        if entity_id in entities_by_id:
            raise ProviderError("state.invalid", "verified scenario entity IDs repeat")
        entities_by_id[entity_id] = entity
    expected_entity_ids = tuple(entities_by_id)
    if expected_entity_ids != tuple(sorted(expected_entity_ids)):
        raise ProviderError("state.invalid", "verified scenario entity IDs are unordered")
    declared_entity_ids = require_sorted_identifiers(
        scene_state["declared_entity_ids"],
        label="scene_state.declared_entity_ids",
        minimum=1,
    )
    if declared_entity_ids != expected_entity_ids:
        raise ProviderError(
            "identity.mismatch", "scene_state entity inventory differs from scenario"
        )
    motion_provider_ids = tuple(
        sorted(
            {
                require_string(
                    entity.get("owner_id"),
                    label=f"verified scenario entity {entity_id}.owner_id",
                    pattern=IDENTIFIER,
                )
                for entity_id, entity in entities_by_id.items()
                if entity.get("state") == "dynamic"
            }
        )
    )
    barrier_digest, expected_contribution_digests = require_motion_stage_barrier(
        scene_state["stage_barrier"],
        run_id=run_id,
        scenario_digest=scenario_digest,
        target=target,
        motion_provider_ids=motion_provider_ids,
    )
    samples = require_array(scene_state["samples"], label="scene_state.samples", minimum=1)
    if len(samples) != len(declared_entity_ids):
        raise ProviderError("request.invalid", "scene_state sample inventory differs")
    samples_by_id: dict[str, dict[str, Any]] = {}
    for index, entity_id in enumerate(declared_entity_ids):
        validated = require_state_sample(
            samples[index],
            label=f"scene_state.samples[{index}]",
            run_id=run_id,
            scenario_digest=scenario_digest,
            target=target,
            entity=entities_by_id[entity_id],
            motion_provider_ids=motion_provider_ids,
        )
        samples_by_id[validated["entity_id"]] = validated
    if tuple(samples_by_id) != declared_entity_ids:
        raise ProviderError("identity.mismatch", "scene_state samples do not close entity IDs")
    contribution_digests = tuple(
        require_non_placeholder_sha256(
            item, label=f"scene_state.contribution_digests[{index}]"
        )
        for index, item in enumerate(
            require_array(
                scene_state["contribution_digests"],
                label="scene_state.contribution_digests",
            )
        )
    )
    if (
        contribution_digests != tuple(sorted(contribution_digests))
        or len(contribution_digests) != len(set(contribution_digests))
        or contribution_digests != expected_contribution_digests
    ):
        raise ProviderError(
            "identity.mismatch", "scene_state contribution inventory differs"
        )
    node_positions: list[tuple[str, float, float, float]] = []
    positions_by_node: dict[str, tuple[float, float, float]] = {}
    for node in projection.node_bindings:
        entity = entities_by_id.get(node.entity_id)
        sample = samples_by_id.get(node.entity_id)
        if entity is None or sample is None:
            raise ProviderError(
                "identity.mismatch", "network node is absent from the staged SceneState"
            )
        if entity.get("state") == "dynamic":
            owner_id = require_string(
                entity.get("owner_id"),
                label=f"network node {node.node_id}.owner_id",
                pattern=IDENTIFIER,
            )
            if sample["sample_kind"] != "dynamic" or sample["provider_id"] != owner_id:
                raise ProviderError(
                    "identity.mismatch", "dynamic network node ownership differs"
                )
        elif (
            sample["sample_kind"] != "static"
            or sample["position_enu"] != node.position_enu_m
        ):
            raise ProviderError(
                "identity.mismatch", "static network node position differs"
            )
        position = sample["position_enu"]
        positions_by_node[node.node_id] = position
        node_positions.append((node.node_id, *position))
    scene_digest = require_non_placeholder_sha256(
        scene_state["scene_state_digest"], label="scene_state.scene_state_digest"
    )
    try:
        expected_scene_digest = digest(
            {key: item for key, item in scene_state.items() if key != "scene_state_digest"}
        )
    except (TypeError, ValueError) as exc:
        raise ProviderError("request.invalid", "scene_state is not canonical JSON") from exc
    if scene_digest != expected_scene_digest:
        raise ProviderError("identity.mismatch", "scene_state digest does not match")

    link_attenuations: list[tuple[str, float]] = []
    link_obstructions: list[tuple[str, tuple[str, ...]]] = []
    for link in projection.links:
        source = positions_by_node[link.source_node_id]
        destination = positions_by_node[link.destination_node_id]
        intersected_volumes = tuple(
            volume
            for volume in projection.propagation_volumes
            if _segment_intersects_volume(source, destination, volume)
        )
        attenuation_db = sum(
            volume.attenuation_db for volume in intersected_volumes
        )
        if not math.isfinite(attenuation_db) or not 0.0 <= attenuation_db <= 1_000.0:
            raise ProviderError(
                "state.invalid", "scene obstruction attenuation is outside backend range"
            )
        link_attenuations.append((link.link_id, attenuation_db))
        link_obstructions.append(
            (link.link_id, tuple(volume.volume_id for volume in intersected_volumes))
        )
    return SceneStageInput(
        scene_state_digest=scene_digest,
        motion_barrier_digest=barrier_digest,
        node_positions_enu_m=tuple(node_positions),
        link_attenuations_db=tuple(link_attenuations),
        link_obstruction_volume_ids=tuple(link_obstructions),
    )


_CHANNELS_24_GHZ = {
    20: {2412 + 5 * index: 1 + index for index in range(13)},
    40: {2422 + 5 * index: 3 + index for index in range(9)},
}
_CHANNELS_5_GHZ = {
    20: {
        frequency: channel
        for channel, frequency in (
            (36, 5180),
            (40, 5200),
            (44, 5220),
            (48, 5240),
            (52, 5260),
            (56, 5280),
            (60, 5300),
            (64, 5320),
            (100, 5500),
            (104, 5520),
            (108, 5540),
            (112, 5560),
            (116, 5580),
            (120, 5600),
            (124, 5620),
            (128, 5640),
            (132, 5660),
            (136, 5680),
            (140, 5700),
            (144, 5720),
            (149, 5745),
            (153, 5765),
            (157, 5785),
            (161, 5805),
            (165, 5825),
            (169, 5845),
            (173, 5865),
            (177, 5885),
            (181, 5905),
        )
    },
    40: {
        frequency: channel
        for channel, frequency in (
            (38, 5190),
            (46, 5230),
            (54, 5270),
            (62, 5310),
            (102, 5510),
            (110, 5550),
            (118, 5590),
            (126, 5630),
            (134, 5670),
            (142, 5710),
            (151, 5755),
            (159, 5795),
            (167, 5835),
            (175, 5875),
        )
    },
    80: {
        frequency: channel
        for channel, frequency in (
            (42, 5210),
            (58, 5290),
            (106, 5530),
            (122, 5610),
            (138, 5690),
            (155, 5775),
            (171, 5855),
        )
    },
    160: {5250: 50, 5570: 114, 5815: 163},
}
_WIFI_LIMITS = {
    "802.11n": {
        20: ("HtMcs7", "HtMcs0", 65_000_000),
        40: ("HtMcs7", "HtMcs0", 135_000_000),
    },
    "802.11ac": {
        20: ("VhtMcs8", "VhtMcs0", 78_000_000),
        40: ("VhtMcs9", "VhtMcs0", 200_000_000),
        80: ("VhtMcs9", "VhtMcs0", 433_000_000),
        160: ("VhtMcs9", "VhtMcs0", 866_000_000),
    },
    "802.11ax": {
        20: ("HeMcs11", "HeMcs0", 143_000_000),
        40: ("HeMcs11", "HeMcs0", 286_000_000),
        80: ("HeMcs11", "HeMcs0", 600_000_000),
        160: ("HeMcs11", "HeMcs0", 1_201_000_000),
    },
}


def _required_capabilities(standards: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                "network.delivery",
                MAILBOX_CAPABILITY,
                NETWORK_MODEL_CAPABILITY,
                *(f"wifi.{standard}" for standard in standards),
            }
        )
    )


def _wifi_channel(
    *, wifi_standard: str, frequency_ghz: float, channel_width_mhz: float
) -> tuple[int, int, str, str, str, int]:
    if wifi_standard not in _WIFI_LIMITS:
        raise ProviderError("request.unsupported", "Wi-Fi standard is unsupported")
    width = round(channel_width_mhz)
    if not math.isclose(channel_width_mhz, width, rel_tol=0.0, abs_tol=1e-9):
        raise ProviderError(
            "request.invalid", "Wi-Fi channel width must be an integral MHz value"
        )
    if width not in _WIFI_LIMITS[wifi_standard]:
        raise ProviderError(
            "request.unsupported",
            f"{wifi_standard} does not support the declared channel width",
        )
    frequency_mhz_value = frequency_ghz * 1000.0
    frequency_mhz = round(frequency_mhz_value)
    if not math.isclose(
        frequency_mhz_value, frequency_mhz, rel_tol=0.0, abs_tol=1e-6
    ):
        raise ProviderError(
            "request.invalid", "Wi-Fi frequency must name an exact MHz channel center"
        )

    channel_number: int | None = None
    band: str | None = None
    if wifi_standard in {"802.11n", "802.11ax"}:
        channel_number = _CHANNELS_24_GHZ.get(width, {}).get(frequency_mhz)
        if channel_number is not None:
            band = "BAND_2_4GHZ"
    if channel_number is None and wifi_standard in {
        "802.11n",
        "802.11ac",
        "802.11ax",
    }:
        channel_number = _CHANNELS_5_GHZ.get(width, {}).get(frequency_mhz)
        if channel_number is not None:
            band = "BAND_5GHZ"
    if channel_number is None and wifi_standard == "802.11ax":
        six_parameters = {
            20: (1, 5955, 4, 20, 58),
            40: (3, 5965, 8, 40, 28),
            80: (7, 5985, 16, 80, 13),
            160: (15, 6025, 32, 160, 6),
        }
        first_channel, first_frequency, channel_step, frequency_step, last = (
            six_parameters[width]
        )
        offset = frequency_mhz - first_frequency
        if offset >= 0 and offset % frequency_step == 0 and offset // frequency_step <= last:
            channel_number = first_channel + channel_step * (offset // frequency_step)
            band = "BAND_6GHZ"
    if channel_number is None or band is None:
        raise ProviderError(
            "request.unsupported",
            "Wi-Fi frequency/channel-width pair is not an ns-3.48 operating channel",
        )
    data_mode, control_mode, max_rate = _WIFI_LIMITS[wifi_standard][width]
    return frequency_mhz, width, band, data_mode, control_mode, max_rate


def _parse_ns3_config(
    value: Any,
    *,
    config_digest: str,
    expected_provider_id: str,
) -> Ns3ConfigIdentity:
    config = require_object(
        value,
        fields={
            "schema_version",
            "provider_id",
            "ns3",
            "network_model",
            "supported_wifi_standards",
            "required_commands",
            "command_timeout_ms",
        },
        label="Ns3Config",
    )
    if config["schema_version"] != "aero-bench.ns3/v3":
        raise ProviderError("request.unsupported", "Ns3Config schema is unsupported")
    provider_id = require_string(
        config["provider_id"], label="Ns3Config.provider_id", pattern=IDENTIFIER
    )
    if provider_id != expected_provider_id:
        raise ProviderError(
            "identity.mismatch", "Ns3Config provider_id differs from workload"
        )
    software = require_object(
        config["ns3"], fields={"version", "commit"}, label="Ns3Config.ns3"
    )
    if software != {"version": NS3_VERSION, "commit": NS3_COMMIT}:
        raise ProviderError("identity.mismatch", "Ns3Config ns-3 identity is unsupported")
    if config["network_model"] != NETWORK_MODEL:
        raise ProviderError("request.unsupported", "Ns3Config network model is unsupported")
    standards_value = config["supported_wifi_standards"]
    if not isinstance(standards_value, list) or not standards_value:
        raise ProviderError(
            "request.invalid", "Ns3Config supported_wifi_standards must be non-empty"
        )
    standards = tuple(
        require_string(item, label="Ns3Config supported Wi-Fi standard")
        for item in standards_value
    )
    if (
        standards != tuple(sorted(standards))
        or len(standards) != len(set(standards))
        or any(standard not in SUPPORTED_WIFI_STANDARDS for standard in standards)
    ):
        raise ProviderError(
            "request.invalid",
            "Ns3Config supported_wifi_standards must be sorted, unique, and supported",
        )
    commands = config["required_commands"]
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(command, str) or not command for command in commands)
        or len(commands) != len(set(commands))
    ):
        raise ProviderError("request.invalid", "Ns3Config required_commands are invalid")
    require_integer(
        config["command_timeout_ms"],
        label="Ns3Config.command_timeout_ms",
        minimum=1,
        maximum=MAX_COMMAND_TIMEOUT_MS,
    )
    return Ns3ConfigIdentity(
        config_digest=config_digest,
        network_model=NETWORK_MODEL,
        supported_wifi_standards=standards,
    )


def _parse_clock(value: Any) -> Ns3ClockIdentity:
    clock = require_object(
        value,
        fields={"authority", "step_ns", "max_steps", "provider_timeout_ms"},
        label="ProviderWorkloadContract.clock",
    )
    if clock["authority"] != "provider_barrier":
        raise ProviderError(
            "request.unsupported", "ns-3 requires provider_barrier clock authority"
        )
    step_ns = require_integer(
        clock["step_ns"],
        label="contract.clock.step_ns",
        minimum=1,
        maximum=MAX_SIMULATION_TIME_NS,
    )
    max_steps = require_integer(
        clock["max_steps"],
        label="contract.clock.max_steps",
        minimum=1,
        maximum=MAX_SIMULATION_TIME_NS // step_ns,
    )
    provider_timeout_ms = require_integer(
        clock["provider_timeout_ms"],
        label="contract.clock.provider_timeout_ms",
        minimum=1,
        maximum=MAX_COMMAND_TIMEOUT_MS,
    )
    return Ns3ClockIdentity(
        step_ns=step_ns,
        max_steps=max_steps,
        provider_timeout_ms=provider_timeout_ms,
    )


def _entity_positions(
    scenario: dict[str, Any], *, provider_ids: set[str]
) -> dict[str, tuple[float, float, float]]:
    raw_entities = scenario["entities"]
    if not isinstance(raw_entities, list):
        raise ProviderError("request.invalid", "ResolvedScenario.entities must be an array")
    result: dict[str, tuple[float, float, float]] = {}
    ordered_ids: list[str] = []
    for index, raw_entity in enumerate(raw_entities):
        label = f"ResolvedScenario.entities[{index}]"
        entity = require_object(
            raw_entity,
            fields={
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
            },
            label=label,
        )
        entity_id = require_string(
            entity["entity_id"], label=f"{label}.entity_id", pattern=IDENTIFIER
        )
        owner_kind = require_string(entity["owner_kind"], label=f"{label}.owner_kind")
        owner_id = require_string(
            entity["owner_id"], label=f"{label}.owner_id", pattern=IDENTIFIER
        )
        source_provider_id = entity["source_provider_id"]
        state = require_string(entity["state"], label=f"{label}.state")
        if owner_kind == "provider":
            if (
                owner_id not in provider_ids
                or source_provider_id != owner_id
                or state != "dynamic"
            ):
                raise ProviderError(
                    "identity.mismatch", f"{label} has inconsistent provider ownership"
                )
        elif owner_kind == "scenario":
            if (
                owner_id != "scenario.compiler"
                or source_provider_id is not None
                or state != "static"
            ):
                raise ProviderError(
                    "identity.mismatch", f"{label} has inconsistent scenario ownership"
                )
        else:
            raise ProviderError("request.invalid", f"{label}.owner_kind is unsupported")
        pose = require_object(
            entity["initial_pose"],
            fields={"position", "orientation_enu", "orientation_ned"},
            label=f"{label}.initial_pose",
        )
        position = require_object(
            pose["position"],
            fields={
                "enu",
                "ned",
                "ecef",
                "wgs84",
                "geoid_separation_m",
                "amsl_m",
                "terrain_amsl_m",
                "agl_m",
            },
            label=f"{label}.initial_pose.position",
        )
        enu = require_object(
            position["enu"],
            fields={"east_m", "north_m", "up_m"},
            label=f"{label}.initial_pose.position.enu",
        )
        result[entity_id] = tuple(
            require_number(
                enu[field],
                label=f"{label}.initial_pose.position.enu.{field}",
                minimum=-MAX_POSITION_METRES,
                maximum=MAX_POSITION_METRES,
            )
            for field in ("east_m", "north_m", "up_m")
        )
        ordered_ids.append(entity_id)
    if ordered_ids != sorted(ordered_ids) or len(ordered_ids) != len(set(ordered_ids)):
        raise ProviderError(
            "request.invalid", "ResolvedScenario entities must be sorted and unique"
        )
    return result


def _resolved_polygon(
    value: Any,
    *,
    label: str,
) -> tuple[tuple[float, float, float], ...]:
    vertices = require_array(value, label=label, minimum=3)
    result: list[tuple[float, float, float]] = []
    for index, raw_vertex in enumerate(vertices):
        vertex = require_object(
            raw_vertex,
            fields={
                "enu",
                "ned",
                "ecef",
                "wgs84",
                "geoid_separation_m",
                "amsl_m",
                "terrain_amsl_m",
                "agl_m",
            },
            label=f"{label}[{index}]",
        )
        enu = require_object(
            vertex["enu"],
            fields={"east_m", "north_m", "up_m"},
            label=f"{label}[{index}].enu",
        )
        result.append(
            tuple(
                require_number(
                    enu[field],
                    label=f"{label}[{index}].enu.{field}",
                    minimum=-MAX_POSITION_METRES,
                    maximum=MAX_POSITION_METRES,
                )
                for field in ("east_m", "north_m", "up_m")
            )
        )
    return tuple(result)


def _propagation_volumes(document: dict[str, Any]) -> tuple[PropagationVolume, ...]:
    volumes: list[PropagationVolume] = []
    raw_buildings = require_array(document.get("buildings"), label="ResolvedScenario.buildings")
    for index, raw_building in enumerate(raw_buildings):
        label = f"ResolvedScenario.buildings[{index}]"
        building = require_object(
            raw_building,
            fields={
                "building_id",
                "entity_id",
                "source_asset_id",
                "render_asset_id",
                "collision_asset_id",
                "anchor_east_m",
                "anchor_north_m",
                "base_vertices",
                "top_vertices",
            },
            label=label,
        )
        building_id = require_string(
            building["building_id"], label=f"{label}.building_id", pattern=IDENTIFIER
        )
        base = _resolved_polygon(building["base_vertices"], label=f"{label}.base_vertices")
        top = _resolved_polygon(building["top_vertices"], label=f"{label}.top_vertices")
        if len(base) != len(top):
            raise ProviderError(
                "request.invalid", "ResolvedScenario building volume is not closed"
            )
        heights = tuple(vertex[2] for vertex in (*base, *top))
        volumes.append(
            PropagationVolume(
                volume_id=f"building.{building_id}",
                kind="building",
                polygon_enu_m=tuple((vertex[0], vertex[1]) for vertex in base),
                min_up_m=min(heights),
                max_up_m=max(heights),
                attenuation_db=BUILDING_ATTENUATION_DB,
            )
        )

    raw_regions = require_array(document.get("regions"), label="ResolvedScenario.regions")
    for index, raw_region in enumerate(raw_regions):
        label = f"ResolvedScenario.regions[{index}]"
        region = require_object(
            raw_region,
            fields={
                "region_id",
                "kind",
                "communications_shadow_attenuation_db",
                "anchor_east_m",
                "anchor_north_m",
                "lower_vertices",
                "upper_vertices",
            },
            label=label,
        )
        if region["kind"] != "communications_shadow":
            continue
        region_id = require_string(
            region["region_id"], label=f"{label}.region_id", pattern=IDENTIFIER
        )
        attenuation = require_number(
            region["communications_shadow_attenuation_db"],
            label=f"{label}.communications_shadow_attenuation_db",
            minimum=0.000_001,
            maximum=1_000.0,
        )
        lower = _resolved_polygon(
            region["lower_vertices"], label=f"{label}.lower_vertices"
        )
        upper = _resolved_polygon(
            region["upper_vertices"], label=f"{label}.upper_vertices"
        )
        if len(lower) != len(upper):
            raise ProviderError(
                "request.invalid", "ResolvedScenario shadow volume is not closed"
            )
        heights = tuple(vertex[2] for vertex in (*lower, *upper))
        volumes.append(
            PropagationVolume(
                volume_id=f"shadow.{region_id}",
                kind="communications_shadow",
                polygon_enu_m=tuple((vertex[0], vertex[1]) for vertex in lower),
                min_up_m=min(heights),
                max_up_m=max(heights),
                attenuation_db=attenuation,
            )
        )

    volume_ids = tuple(volume.volume_id for volume in volumes)
    if volume_ids != tuple(sorted(set(volume_ids))):
        raise ProviderError(
            "request.invalid", "ResolvedScenario propagation volumes are not canonical"
        )
    return tuple(volumes)


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def _point_on_segment(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> bool:
    tolerance = 1.0e-9
    return abs(_orientation(first, second, point)) <= tolerance and (
        min(first[0], second[0]) - tolerance
        <= point[0]
        <= max(first[0], second[0]) + tolerance
        and min(first[1], second[1]) - tolerance
        <= point[1]
        <= max(first[1], second[1]) + tolerance
    )


def _segment_polygon_intervals(
    source: tuple[float, float],
    destination: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    tolerance = 1.0e-9
    direction = (destination[0] - source[0], destination[1] - source[1])
    length_squared = direction[0] * direction[0] + direction[1] * direction[1]
    if length_squared <= tolerance * tolerance:
        return ((0.0, 1.0),) if _point_in_polygon(source, polygon) else ()

    def cross(first: tuple[float, float], second: tuple[float, float]) -> float:
        return first[0] * second[1] - first[1] * second[0]

    parameters = [0.0, 1.0]
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        edge_direction = (
            edge_end[0] - edge_start[0],
            edge_end[1] - edge_start[1],
        )
        offset = (edge_start[0] - source[0], edge_start[1] - source[1])
        denominator = cross(direction, edge_direction)
        if abs(denominator) <= tolerance:
            if abs(cross(offset, direction)) <= tolerance:
                for vertex in (edge_start, edge_end):
                    parameter = (
                        (vertex[0] - source[0]) * direction[0]
                        + (vertex[1] - source[1]) * direction[1]
                    ) / length_squared
                    if -tolerance <= parameter <= 1.0 + tolerance:
                        parameters.append(min(1.0, max(0.0, parameter)))
            continue
        parameter = cross(offset, edge_direction) / denominator
        edge_parameter = cross(offset, direction) / denominator
        if (
            -tolerance <= parameter <= 1.0 + tolerance
            and -tolerance <= edge_parameter <= 1.0 + tolerance
        ):
            parameters.append(min(1.0, max(0.0, parameter)))

    ordered: list[float] = []
    for parameter in sorted(parameters):
        if not ordered or parameter - ordered[-1] > tolerance:
            ordered.append(parameter)
    intervals: list[tuple[float, float]] = []
    for start, end in zip(ordered, ordered[1:]):
        if end - start <= tolerance:
            continue
        midpoint = (start + end) / 2.0
        point = (
            source[0] + midpoint * direction[0],
            source[1] + midpoint * direction[1],
        )
        if _point_in_polygon(point, polygon):
            intervals.append((start, end))
    return tuple(intervals)


def _point_in_polygon(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    inside = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if _point_on_segment(point, first, second):
            return True
        crosses = (first[1] > point[1]) != (second[1] > point[1])
        if crosses:
            intersection_x = first[0] + (
                (point[1] - first[1]) * (second[0] - first[0])
                / (second[1] - first[1])
            )
            if intersection_x >= point[0]:
                inside = not inside
    return inside


def _segment_intersects_volume(
    source: tuple[float, float, float],
    destination: tuple[float, float, float],
    volume: PropagationVolume,
) -> bool:
    tolerance = 1.0e-9
    horizontal_intervals = _segment_polygon_intervals(
        (source[0], source[1]),
        (destination[0], destination[1]),
        volume.polygon_enu_m,
    )
    if not horizontal_intervals:
        return False

    vertical_delta = destination[2] - source[2]
    if abs(vertical_delta) <= tolerance:
        if not volume.min_up_m - tolerance <= source[2] <= volume.max_up_m + tolerance:
            return False
        vertical_interval = (0.0, 1.0)
    else:
        first = (volume.min_up_m - source[2]) / vertical_delta
        second = (volume.max_up_m - source[2]) / vertical_delta
        vertical_interval = (max(0.0, min(first, second)), min(1.0, max(first, second)))
        if vertical_interval[1] - vertical_interval[0] <= tolerance:
            return False

    return any(
        min(horizontal_end, vertical_interval[1])
        - max(horizontal_start, vertical_interval[0])
        > tolerance
        for horizontal_start, horizontal_end in horizontal_intervals
    )


def _validate_backend_link_states(
    lines: list[str],
    *,
    projection: Ns3NetworkProjection,
    staged_input: SceneStageInput,
) -> tuple[LinkRuntimeState, ...]:
    if len(lines) != len(projection.links):
        raise ProviderError(
            "backend.invalid", "ns-3 STEP link-state inventory differs"
        )
    positions = {
        node_id: (east_m, north_m, up_m)
        for node_id, east_m, north_m, up_m in staged_input.node_positions_enu_m
    }
    attenuations = dict(staged_input.link_attenuations_db)
    nodes = {node.node_id: node for node in projection.node_bindings}
    profiles = {
        profile.radio_profile_id: profile for profile in projection.radio_profiles
    }
    states: list[LinkRuntimeState] = []
    for index, link in enumerate(projection.links):
        fields = lines[index].split()
        if (
            len(fields) != 15
            or fields[:4]
            != [
                "LINK",
                b64(link.link_id),
                b64(link.source_node_id),
                b64(link.destination_node_id),
            ]
        ):
            raise ProviderError(
                "backend.invalid", "ns-3 STEP link-state identity differs"
            )
        distance_m = parse_backend_float(
            fields[4], label="link distance_m", minimum=0.0, maximum=4.0e9
        )
        base_path_loss_db = parse_backend_float(
            fields[5], label="link base_path_loss_db", minimum=0.0, maximum=2_000.0
        )
        obstruction_loss_db = parse_backend_float(
            fields[6], label="link obstruction_loss_db", minimum=0.0, maximum=1_000.0
        )
        forward_rssi_dbm = parse_backend_float(
            fields[7], label="link forward_rssi_dbm", minimum=-3_000.0, maximum=200.0
        )
        reverse_rssi_dbm = parse_backend_float(
            fields[8], label="link reverse_rssi_dbm", minimum=-3_000.0, maximum=200.0
        )
        forward_snr_db = parse_backend_float(
            fields[9], label="link forward_snr_db", minimum=-3_000.0, maximum=500.0
        )
        reverse_snr_db = parse_backend_float(
            fields[10], label="link reverse_snr_db", minimum=-3_000.0, maximum=500.0
        )
        queue_values = fields[11:]
        if any(DECIMAL_INTEGER.fullmatch(value) is None for value in queue_values):
            raise ProviderError(
                "backend.invalid", "ns-3 STEP link queue facts are malformed"
            )
        source_queue_packets, destination_queue_packets, source_queue_bytes, destination_queue_bytes = map(
            int, queue_values
        )
        if min(
            source_queue_packets,
            destination_queue_packets,
            source_queue_bytes,
            destination_queue_bytes,
        ) < 0:
            raise ProviderError(
                "backend.invalid", "ns-3 STEP link queue facts are negative"
            )

        source_position = positions[link.source_node_id]
        destination_position = positions[link.destination_node_id]
        expected_distance = math.dist(source_position, destination_position)
        source_profile = profiles[nodes[link.source_node_id].radio_profile_id]
        destination_profile = profiles[
            nodes[link.destination_node_id].radio_profile_id
        ]
        reference_loss = 20.0 * math.log10(
            4.0
            * math.pi
            * (source_profile.frequency_mhz * 1_000_000.0)
            / 299_792_458.0
        )
        expected_base_loss = reference_loss + 10.0 * LOG_DISTANCE_EXPONENT * math.log10(
            max(1.0, expected_distance)
        )
        expected_obstruction_loss = attenuations[link.link_id]
        expected_forward_rssi = (
            source_profile.tx_power_dbm
            - expected_base_loss
            - expected_obstruction_loss
        )
        expected_reverse_rssi = (
            destination_profile.tx_power_dbm
            - expected_base_loss
            - expected_obstruction_loss
        )
        destination_noise_floor = (
            THERMAL_NOISE_DENSITY_DBM_HZ
            + 10.0
            * math.log10(destination_profile.channel_width_mhz * 1_000_000.0)
            + RX_NOISE_FIGURE_DB
        )
        source_noise_floor = (
            THERMAL_NOISE_DENSITY_DBM_HZ
            + 10.0
            * math.log10(source_profile.channel_width_mhz * 1_000_000.0)
            + RX_NOISE_FIGURE_DB
        )
        actual_metrics = (
            distance_m,
            base_path_loss_db,
            obstruction_loss_db,
            forward_rssi_dbm,
            reverse_rssi_dbm,
            forward_snr_db,
            reverse_snr_db,
        )
        expected_metrics = (
            expected_distance,
            expected_base_loss,
            expected_obstruction_loss,
            expected_forward_rssi,
            expected_reverse_rssi,
            expected_forward_rssi - destination_noise_floor,
            expected_reverse_rssi - source_noise_floor,
        )
        if any(
            not math.isclose(actual, expected, rel_tol=1.0e-12, abs_tol=1.0e-9)
            for actual, expected in zip(actual_metrics, expected_metrics)
        ):
            raise ProviderError(
                "backend.invalid", "ns-3 STEP link metrics differ from staged mobility"
            )
        states.append(
            LinkRuntimeState(
                link_id=link.link_id,
                source_node_id=link.source_node_id,
                destination_node_id=link.destination_node_id,
                distance_m=distance_m,
                base_path_loss_db=base_path_loss_db,
                obstruction_loss_db=obstruction_loss_db,
                forward_rssi_dbm=forward_rssi_dbm,
                reverse_rssi_dbm=reverse_rssi_dbm,
                forward_snr_db=forward_snr_db,
                reverse_snr_db=reverse_snr_db,
                source_queue_packets=source_queue_packets,
                destination_queue_packets=destination_queue_packets,
                source_queue_bytes=source_queue_bytes,
                destination_queue_bytes=destination_queue_bytes,
            )
        )
    return tuple(states)


def _validate_network_projection(
    scenario: ValidatedWorkloadScenario,
    *,
    provider_id: str,
    supported_wifi_standards: tuple[str, ...],
    capabilities: tuple[str, ...],
) -> Ns3NetworkProjection:
    document = scenario.scenario
    raw_providers = document["providers"]
    if not isinstance(raw_providers, list):
        raise ProviderError("request.invalid", "ResolvedScenario.providers must be an array")
    provider_ids: list[str] = []
    owner_matches: list[dict[str, Any]] = []
    for index, value in enumerate(raw_providers):
        provider = require_object(
            value,
            fields={"provider_id", "runtime_stage", "roles", "capability_ids"},
            label=f"ResolvedScenario.providers[{index}]",
        )
        declared_id = require_string(
            provider["provider_id"], label="scenario provider_id", pattern=IDENTIFIER
        )
        provider_ids.append(declared_id)
        if provider["runtime_stage"] not in ("motion", "network", "business_environment"):
            raise ProviderError("request.invalid", "scenario provider runtime_stage is invalid")
        if declared_id == provider_id:
            owner_matches.append(provider)
    if provider_ids != sorted(provider_ids) or len(provider_ids) != len(set(provider_ids)):
        raise ProviderError("request.invalid", "ResolvedScenario providers are not canonical")
    if (len(owner_matches) != 1
        or owner_matches[0]["runtime_stage"] != "network"
        or owner_matches[0]["roles"] != ["wireless_network"]):
        raise ProviderError(
            "identity.mismatch",
            "ns-3 must explicitly own exactly the wireless_network scenario role",
        )
    if tuple(owner_matches[0]["capability_ids"]) != capabilities:
        raise ProviderError(
            "identity.mismatch", "ns-3 scenario capabilities differ from workload"
        )

    entity_positions = _entity_positions(document, provider_ids=set(provider_ids))
    propagation_volumes = _propagation_volumes(document)
    network = require_object(
        document.get("network"),
        fields={"provider_id", "radio_profiles", "node_bindings", "links"},
        label="ResolvedScenario.network",
    )
    if network["provider_id"] != provider_id:
        raise ProviderError(
            "identity.mismatch", "ResolvedScenario network owner differs from workload"
        )

    raw_profiles = network["radio_profiles"]
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ProviderError(
            "request.invalid", "ResolvedScenario network radio_profiles must be non-empty"
        )
    if len(raw_profiles) > MAX_RADIO_PROFILES:
        raise ProviderError(
            "request.invalid",
            f"ResolvedScenario supports at most {MAX_RADIO_PROFILES} radio profiles",
        )
    profiles: list[WifiRadioProfile] = []
    profile_ids: list[str] = []
    used_standards: set[str] = set()
    for index, raw_profile in enumerate(raw_profiles):
        label = f"ResolvedScenario.network.radio_profiles[{index}]"
        profile = require_object(
            raw_profile,
            fields={
                "radio_profile_id",
                "provider_id",
                "wifi_standard",
                "frequency_ghz",
                "channel_width_mhz",
                "tx_power_dbm",
                "rx_sensitivity_dbm",
            },
            label=label,
        )
        profile_id = require_string(
            profile["radio_profile_id"],
            label=f"{label}.radio_profile_id",
            pattern=IDENTIFIER,
        )
        if profile["provider_id"] != provider_id:
            raise ProviderError(
                "identity.mismatch", f"{label} is not owned by the ns-3 workload"
            )
        standard = require_string(profile["wifi_standard"], label=f"{label}.wifi_standard")
        if standard not in supported_wifi_standards:
            raise ProviderError(
                "request.unsupported", f"{label} uses an undeclared Wi-Fi standard"
            )
        frequency = require_number(
            profile["frequency_ghz"],
            label=f"{label}.frequency_ghz",
            minimum=2.0,
            maximum=7.2,
        )
        channel_width = require_number(
            profile["channel_width_mhz"],
            label=f"{label}.channel_width_mhz",
            minimum=20.0,
            maximum=160.0,
        )
        tx_power = require_number(
            profile["tx_power_dbm"],
            label=f"{label}.tx_power_dbm",
            minimum=-100.0,
            maximum=100.0,
        )
        rx_sensitivity = require_number(
            profile["rx_sensitivity_dbm"],
            label=f"{label}.rx_sensitivity_dbm",
            minimum=-200.0,
            maximum=0.0,
        )
        if tx_power <= rx_sensitivity:
            raise ProviderError(
                "request.invalid", f"{label} transmit power must exceed sensitivity"
            )
        frequency_mhz, width, band, data_mode, control_mode, max_rate = _wifi_channel(
            wifi_standard=standard,
            frequency_ghz=frequency,
            channel_width_mhz=channel_width,
        )
        channel_number = (
            _CHANNELS_24_GHZ.get(width, {}).get(frequency_mhz)
            or _CHANNELS_5_GHZ.get(width, {}).get(frequency_mhz)
        )
        if channel_number is None:
            six_parameters = {
                20: (1, 5955, 4, 20),
                40: (3, 5965, 8, 40),
                80: (7, 5985, 16, 80),
                160: (15, 6025, 32, 160),
            }
            first_channel, first_frequency, channel_step, frequency_step = six_parameters[width]
            channel_number = first_channel + channel_step * (
                (frequency_mhz - first_frequency) // frequency_step
            )
        profiles.append(
            WifiRadioProfile(
                radio_profile_id=profile_id,
                provider_id=provider_id,
                wifi_standard=standard,
                frequency_ghz=frequency,
                frequency_mhz=frequency_mhz,
                channel_number=channel_number,
                channel_width_mhz=width,
                band=band,
                tx_power_dbm=tx_power,
                rx_sensitivity_dbm=rx_sensitivity,
                data_mode=data_mode,
                control_mode=control_mode,
                max_data_rate_bps=max_rate,
            )
        )
        profile_ids.append(profile_id)
        used_standards.add(standard)
    if profile_ids != sorted(profile_ids) or len(profile_ids) != len(set(profile_ids)):
        raise ProviderError(
            "request.invalid", "ResolvedScenario radio profiles must be sorted and unique"
        )
    if tuple(sorted(used_standards)) != supported_wifi_standards:
        raise ProviderError(
            "identity.mismatch",
            "Ns3Config supported Wi-Fi standards do not exactly close radio profiles",
        )
    expected_capabilities = _required_capabilities(supported_wifi_standards)
    if capabilities != expected_capabilities:
        raise ProviderError(
            "identity.mismatch",
            "ns-3 capabilities do not exactly identify the Wi-Fi link model",
        )
    profile_map = {profile.radio_profile_id: profile for profile in profiles}

    raw_nodes = network["node_bindings"]
    if not isinstance(raw_nodes, list) or not 2 <= len(raw_nodes) <= MAX_NODES:
        raise ProviderError(
            "request.invalid",
            f"ResolvedScenario network requires between 2 and {MAX_NODES} nodes",
        )
    nodes: list[NetworkNodeBinding] = []
    node_ids: list[str] = []
    entity_ids: list[str] = []
    endpoint_ids: list[str] = []
    used_profile_ids: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        label = f"ResolvedScenario.network.node_bindings[{index}]"
        node = require_object(
            raw_node,
            fields={"node_id", "entity_id", "endpoint_id", "radio_profile_id"},
            label=label,
        )
        node_id = require_string(
            node["node_id"], label=f"{label}.node_id", pattern=IDENTIFIER
        )
        entity_id = require_string(
            node["entity_id"], label=f"{label}.entity_id", pattern=IDENTIFIER
        )
        endpoint_id = require_string(
            node["endpoint_id"], label=f"{label}.endpoint_id", pattern=IDENTIFIER
        )
        profile_id = require_string(
            node["radio_profile_id"],
            label=f"{label}.radio_profile_id",
            pattern=IDENTIFIER,
        )
        if entity_id not in entity_positions:
            raise ProviderError(
                "identity.mismatch", f"{label} references an undeclared scenario entity"
            )
        if profile_id not in profile_map:
            raise ProviderError(
                "identity.mismatch", f"{label} references an undeclared radio profile"
            )
        nodes.append(
            NetworkNodeBinding(
                node_id=node_id,
                entity_id=entity_id,
                endpoint_id=endpoint_id,
                radio_profile_id=profile_id,
                position_enu_m=entity_positions[entity_id],
            )
        )
        node_ids.append(node_id)
        entity_ids.append(entity_id)
        endpoint_ids.append(endpoint_id)
        used_profile_ids.add(profile_id)
    if (
        node_ids != sorted(node_ids)
        or len(node_ids) != len(set(node_ids))
        or len(entity_ids) != len(set(entity_ids))
        or len(endpoint_ids) != len(set(endpoint_ids))
    ):
        raise ProviderError(
            "request.invalid",
            "ResolvedScenario network nodes must be sorted and bijective",
        )
    if used_profile_ids != set(profile_map):
        raise ProviderError(
            "identity.mismatch", "ResolvedScenario contains an unused radio profile"
        )
    node_map = {node.node_id: node for node in nodes}

    raw_links = network["links"]
    if not isinstance(raw_links, list) or not raw_links:
        raise ProviderError(
            "request.invalid", "ResolvedScenario network links must be non-empty"
        )
    if len(raw_links) > MAX_LINKS:
        raise ProviderError(
            "request.invalid", f"ResolvedScenario supports at most {MAX_LINKS} links"
        )
    links: list[NetworkLinkBinding] = []
    link_ids: list[str] = []
    link_pairs: set[tuple[str, str]] = set()
    linked_nodes: set[str] = set()
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for index, raw_link in enumerate(raw_links):
        label = f"ResolvedScenario.network.links[{index}]"
        link = require_object(
            raw_link,
            fields={
                "link_id",
                "source_node_id",
                "destination_node_id",
                "data_rate_bps",
                "propagation_delay_ns",
            },
            label=label,
        )
        link_id = require_string(
            link["link_id"], label=f"{label}.link_id", pattern=IDENTIFIER
        )
        source = require_string(
            link["source_node_id"], label=f"{label}.source_node_id", pattern=IDENTIFIER
        )
        destination = require_string(
            link["destination_node_id"],
            label=f"{label}.destination_node_id",
            pattern=IDENTIFIER,
        )
        if source == destination or source not in node_map or destination not in node_map:
            raise ProviderError(
                "identity.mismatch", f"{label} does not close declared node bindings"
            )
        pair = tuple(sorted((source, destination)))
        if pair in link_pairs:
            raise ProviderError(
                "request.invalid", "parallel links are unsupported by the Wi-Fi link model"
            )
        source_profile = profile_map[node_map[source].radio_profile_id]
        destination_profile = profile_map[node_map[destination].radio_profile_id]
        if (
            source_profile.wifi_standard != destination_profile.wifi_standard
            or source_profile.frequency_mhz != destination_profile.frequency_mhz
            or source_profile.channel_width_mhz
            != destination_profile.channel_width_mhz
        ):
            raise ProviderError(
                "request.unsupported", f"{label} joins radio-incompatible nodes"
            )
        rate = require_integer(
            link["data_rate_bps"],
            label=f"{label}.data_rate_bps",
            minimum=1,
            maximum=min(
                source_profile.max_data_rate_bps,
                destination_profile.max_data_rate_bps,
            ),
        )
        delay = require_integer(
            link["propagation_delay_ns"],
            label=f"{label}.propagation_delay_ns",
            minimum=0,
            maximum=MAX_LINK_DELAY_NS,
        )
        links.append(
            NetworkLinkBinding(
                link_id=link_id,
                source_node_id=source,
                destination_node_id=destination,
                data_rate_bps=rate,
                propagation_delay_ns=delay,
            )
        )
        link_ids.append(link_id)
        link_pairs.add(pair)
        linked_nodes.update(pair)
        adjacency[source].add(destination)
        adjacency[destination].add(source)
    if link_ids != sorted(link_ids) or len(link_ids) != len(set(link_ids)):
        raise ProviderError(
            "request.invalid", "ResolvedScenario network links must be sorted and unique"
        )
    if linked_nodes != set(node_ids):
        raise ProviderError(
            "identity.mismatch", "ResolvedScenario network contains an unlinked node"
        )
    pending = [node_ids[0]]
    visited = {node_ids[0]}
    while pending:
        current = pending.pop()
        for neighbour in adjacency[current]:
            if neighbour not in visited:
                visited.add(neighbour)
                pending.append(neighbour)
    if visited != set(node_ids):
        raise ProviderError(
            "identity.mismatch", "ResolvedScenario network link graph is disconnected"
        )

    projection_document = {
        "provider": owner_matches[0],
        "bound_entities": [
            next(entity for entity in document["entities"] if entity["entity_id"] == entity_id)
            for entity_id in sorted(entity_ids)
        ],
        "network": network,
        "propagation": {
            "model": PROPAGATION_MODEL,
            "log_distance_exponent": LOG_DISTANCE_EXPONENT,
            "building_attenuation_db": BUILDING_ATTENUATION_DB,
            "rx_noise_figure_db": RX_NOISE_FIGURE_DB,
            "thermal_noise_density_dbm_hz": THERMAL_NOISE_DENSITY_DBM_HZ,
            "volumes": [
                {
                    "volume_id": volume.volume_id,
                    "kind": volume.kind,
                    "polygon_enu_m": [list(vertex) for vertex in volume.polygon_enu_m],
                    "min_up_m": volume.min_up_m,
                    "max_up_m": volume.max_up_m,
                    "attenuation_db": volume.attenuation_db,
                }
                for volume in propagation_volumes
            ],
        },
    }
    return Ns3NetworkProjection(
        provider_id=provider_id,
        radio_profiles=tuple(profiles),
        node_bindings=tuple(nodes),
        links=tuple(links),
        propagation_volumes=propagation_volumes,
        projection_digest=digest(projection_document),
    )


class Ns3Backend:
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        process = await asyncio.create_subprocess_exec(
            "./ns3",
            "run",
            "--no-build",
            "aero-ns3-provider",
            cwd=NS3_ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(0)
        if process.returncode is not None:
            await process.wait()
            raise ProviderError(
                "backend.unavailable", "ns-3 backend exited before service startup"
            )
        self._process = process

    async def request(self, fields: list[str]) -> list[str]:
        async with self._lock:
            if (
                self._process is None
                or self._process.stdin is None
                or self._process.stdout is None
            ):
                raise ProviderError(
                    "backend.unavailable", "ns-3 backend is not running"
                )
            line = (" ".join(fields) + "\n").encode("ascii")
            self._process.stdin.write(line)
            try:
                await self._process.stdin.drain()
                first = await self._process.stdout.readline()
            except (BrokenPipeError, ConnectionError) as exc:
                raise ProviderError(
                    "backend.unavailable", "ns-3 backend I/O failed"
                ) from exc
            if not first:
                raise ProviderError(
                    "backend.unavailable", "ns-3 backend closed its output"
                )
            if len(first) > MAX_FRAME_BYTES:
                raise ProviderError(
                    "backend.invalid", "ns-3 backend response is too large"
                )
            text = first.decode("ascii", errors="strict").rstrip("\n")
            if text.startswith("ERR "):
                raise ProviderError("backend.rejected", text[4:])
            if not text.startswith("OK "):
                raise ProviderError(
                    "backend.invalid", "ns-3 backend response is malformed"
                )
            lines = [text]
            if fields[0] == "STEP":
                while True:
                    frame = await self._process.stdout.readline()
                    if not frame:
                        raise ProviderError(
                            "backend.unavailable", "ns-3 backend ended a step"
                        )
                    if len(frame) > MAX_FRAME_BYTES:
                        raise ProviderError(
                            "backend.invalid", "ns-3 backend response is too large"
                        )
                    line_text = frame.decode("ascii", errors="strict").rstrip("\n")
                    lines.append(line_text)
                    if line_text == "END":
                        break
            return lines

    async def close(self) -> None:
        if self._process is None:
            return
        process = self._process
        self._process = None
        if process.returncode is None and process.stdin is not None:
            try:
                process.stdin.write(b"SHUTDOWN\n")
                await process.stdin.drain()
                await process.stdout.readline() if process.stdout is not None else None
            except (BrokenPipeError, ConnectionError):
                pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


def _load_ns3_workload_identity(
    *,
    contract_path: Path,
    bundle_root: Path,
    expected_run_id: str,
    expected_seed: int,
    expected_provider_id: str,
    expected_provider_port: int,
) -> Ns3WorkloadIdentity:
    try:
        contract_mode = contract_path.lstat().st_mode
        bundle_mode = bundle_root.lstat().st_mode
        contract_file = contract_path.resolve(strict=True)
        bundle = bundle_root.resolve(strict=True)
        raw = contract_file.read_bytes()
    except OSError as exc:
        raise RuntimeError("ns-3 workload contract or bundle is unavailable") from exc
    if (
        not contract_path.is_absolute()
        or not bundle_root.is_absolute()
        or stat.S_ISLNK(contract_mode)
        or not stat.S_ISREG(contract_mode)
        or stat.S_ISLNK(bundle_mode)
        or not stat.S_ISDIR(bundle_mode)
        or not contract_file.is_file()
        or not bundle.is_dir()
    ):
        raise RuntimeError("ns-3 workload contract and bundle must be regular paths")
    try:
        contract = parse_json(raw)
        if encode_json(contract) != raw:
            raise ProviderError(
                "request.invalid", "ns-3 workload contract must be canonical JSON"
            )
    except ProviderError as exc:
        raise RuntimeError(
            "ns-3 workload contract is not strict canonical JSON"
        ) from exc
    contract = require_object(
        contract,
        fields={
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
        },
        label="ProviderWorkloadContract",
    )
    if (
        contract["schema_version"] != "aero-bench.workload-contract/v5"
        or contract["role"] != "provider"
    ):
        raise RuntimeError("ns-3 workload contract has an unsupported identity")
    if require_sha256(contract["run_id"], label="contract.run_id") != expected_run_id:
        raise RuntimeError("ns-3 workload contract run identity mismatch")
    seed = require_integer(
        contract["seed"],
        label="contract.seed",
        minimum=1,
        maximum=0xFFFFFFFF,
    )
    if seed != expected_seed:
        raise RuntimeError("ns-3 workload contract seed identity mismatch")
    workload_id = require_string(
        contract["workload_id"], label="contract.workload_id", pattern=IDENTIFIER
    )
    if workload_id != expected_provider_id:
        raise RuntimeError("ns-3 workload contract provider identity mismatch")
    clock = _parse_clock(contract["clock"])

    provider = require_object(
        contract["provider"],
        fields={
            "provider_id",
            "adapter",
            "port",
            "workload",
            "config",
            "protocol_schema",
            "capabilities",
            "artifact_requirements",
        },
        label="ProviderWorkloadContract.provider",
    )
    provider_id = require_string(
        provider["provider_id"],
        label="contract.provider.provider_id",
        pattern=IDENTIFIER,
    )
    if provider_id != expected_provider_id:
        raise RuntimeError("ns-3 provider identity does not match workload identity")
    adapter = require_string(
        provider["adapter"], label="contract.provider.adapter", pattern=IDENTIFIER
    )
    if adapter != PROVIDER_ADAPTER:
        raise RuntimeError("ns-3 workload contract adapter is unsupported")
    provider_port = require_integer(
        provider["port"],
        label="contract.provider.port",
        minimum=1024,
        maximum=65535,
    )
    if provider_port != expected_provider_port:
        raise RuntimeError("ns-3 workload contract port is not the bind port")
    capabilities_value = provider["capabilities"]
    if not isinstance(capabilities_value, list) or not capabilities_value:
        raise RuntimeError("ns-3 workload capabilities must be a non-empty array")
    capabilities = tuple(
        require_string(
            capability,
            label=f"provider.capabilities[{index}]",
            pattern=IDENTIFIER,
        )
        for index, capability in enumerate(capabilities_value)
    )
    if capabilities != tuple(sorted(capabilities)) or len(capabilities) != len(
        set(capabilities)
    ):
        raise RuntimeError("ns-3 workload capabilities must be sorted and unique")

    workload = require_object(
        provider["workload"],
        fields={"runtime", "resources", "implementation"},
        label="ProviderWorkloadContract.provider.workload",
    )
    runtime = require_object(
        workload["runtime"], fields={"image", "command"}, label="provider.runtime"
    )
    runtime_image = require_string(runtime["image"], label="provider.runtime.image")
    if IMMUTABLE_IMAGE.fullmatch(runtime_image) is None:
        raise RuntimeError("ns-3 workload runtime image must be immutable")
    if runtime_image.rsplit(":", maxsplit=1)[1] == "0" * 64:
        raise RuntimeError("ns-3 workload runtime image cannot be a placeholder")
    if (
        not isinstance(runtime["command"], list)
        or not runtime["command"]
        or any(not isinstance(item, str) or not item for item in runtime["command"])
    ):
        raise RuntimeError("ns-3 workload runtime command is invalid")

    config_binding = require_object(
        provider["config"], fields={"file", "schema_file"}, label="provider.config"
    )
    config_path, config_digest = _verify_ns3_file_ref(
        bundle, config_binding["file"], label="provider.config.file"
    )
    _verify_ns3_file_ref(
        bundle, config_binding["schema_file"], label="provider.config.schema_file"
    )
    _verify_ns3_file_ref(
        bundle, provider["protocol_schema"], label="provider.protocol_schema"
    )
    try:
        config_raw = config_path.read_bytes()
        config_document = parse_json(config_raw)
    except OSError as exc:
        raise RuntimeError("ns-3 provider config is unavailable") from exc
    if encode_json(config_document) != config_raw:
        raise ProviderError(
            "request.invalid", "Ns3Config must use strict canonical JSON"
        )
    config = _parse_ns3_config(
        config_document,
        config_digest=config_digest,
        expected_provider_id=provider_id,
    )
    if capabilities != _required_capabilities(config.supported_wifi_standards):
        raise ProviderError(
            "identity.mismatch",
            "provider capabilities do not exactly match Ns3Config",
        )

    try:
        scenario = validate_workload_scenario(
            contract["scenario"],
            expected_seed=seed,
            expected_digest=contract["scenario_digest"],
            role="provider",
            workload_id=provider_id,
            projected_assets=contract["scenario_assets"],
            provider_capabilities=capabilities,
        )
    except WorkloadScenarioError as exc:
        raise RuntimeError("ns-3 workload scenario is invalid") from exc
    network = _validate_network_projection(
        scenario,
        provider_id=provider_id,
        supported_wifi_standards=config.supported_wifi_standards,
        capabilities=capabilities,
    )

    requirements = provider["artifact_requirements"]
    if not isinstance(requirements, list) or len(requirements) != 1:
        raise RuntimeError("ns-3 workload contract requires one artifact requirement")
    try:
        artifact_requirement = require_artifact_requirement(
            requirements[0], provider_id=provider_id
        )
    except ProviderError as exc:
        raise RuntimeError("ns-3 workload artifact contract is invalid") from exc
    return Ns3WorkloadIdentity(
        run_id=expected_run_id,
        provider_id=provider_id,
        adapter=adapter,
        runtime_image=runtime_image,
        config=config,
        clock=clock,
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        network=network,
        artifact_requirement=artifact_requirement,
    )

def _verify_ns3_file_ref(
    root: Path, value: Any, *, label: str
) -> tuple[Path, str]:
    reference = require_object(value, fields={"path", "sha256"}, label=label)
    relative = require_string(reference["path"], label=f"{label}.path")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or str(path) != relative
        or relative.strip() != relative
        or "\\" in relative
    ):
        raise ProviderError("request.invalid", f"{label}.path is not normalized")
    expected = require_sha256(reference["sha256"], label=f"{label}.sha256")
    candidate = root.joinpath(*path.parts)
    if candidate.is_symlink() or any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise ProviderError("request.invalid", f"{label} cannot contain a symlink")
    try:
        mode = candidate.lstat().st_mode
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProviderError("request.invalid", f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(mode)
        or not resolved.is_relative_to(root)
        or not resolved.is_file()
    ):
        raise ProviderError("request.invalid", f"{label} is not a regular bundle file")
    try:
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProviderError("request.invalid", f"{label} is unavailable") from exc
    if actual != expected:
        raise ProviderError("identity.mismatch", f"{label} digest does not match")
    return resolved, expected


class Ns3Service:
    def __init__(
        self,
        workload_identity: Ns3WorkloadIdentity | None = None,
        *,
        session_token: str,
    ) -> None:
        if SHA256.fullmatch(session_token) is None or session_token == "0" * 64:
            raise RuntimeError(
                "ns-3 session token must be an executor-issued SHA-256 digest"
            )
        self._expected_run_id = self._required_run_id()
        self._expected_seed = self._required_seed()
        self._artifact_dir = self._required_artifact_dir()
        self._workload_identity = workload_identity
        self._session_token = session_token
        if (
            workload_identity is not None
            and workload_identity.run_id != self._expected_run_id
        ):
            raise RuntimeError(
                "ns-3 workload identity run ID does not match the process"
            )
        self._artifact_requirement: dict[str, Any] | None = None
        self._artifact_path: Path | None = None
        self._finalization_receipt: dict[str, Any] | None = None
        self._backend = Ns3Backend()
        self._operation_lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._prepared = False
        self._provider_id: str | None = None
        self._protocol_version: str | None = None
        self._runtime_image: str | None = None
        self._projection: Ns3NetworkProjection | None = None
        self._nodes: tuple[str, ...] = ()
        self._endpoint_to_node: dict[str, str] = {}
        self._node_to_endpoint: dict[str, str] = {}
        self._endpoint_to_entity: dict[str, str] = {}
        self._links: tuple[NetworkLinkBinding, ...] = ()
        self._link_states: tuple[LinkRuntimeState, ...] = ()
        self._config_digest: str | None = None
        self._run_id: str | None = None
        self._seed: int | None = None
        self._current_tick = 0
        self._current_time_ns = 0
        self._accepted_stage_input: SceneStageInput | None = None
        self._submissions: dict[str, dict[str, Any]] = {}
        self._deliveries: dict[str, dict[str, Any]] = {}
        self._dropped: dict[str, str] = {}
        self._published_deliveries: set[str] = set()
        self._published_drops: set[str] = set()
        self._backend_facts: dict[str, int | float] = {
            "sim_time_ns": 0,
            "submitted": 0,
            "delivered": 0,
            "pending": 0,
            "delivered_payload_bytes_this_step": 0,
            "delivered_throughput_bps": 0.0,
        }

    @staticmethod
    def _required_run_id() -> str:
        value = os.environ.get("AERO_BENCH_RUN_ID")
        if value is None or SHA256.fullmatch(value) is None or value == "0" * 64:
            raise RuntimeError(
                "AERO_BENCH_RUN_ID must be an explicit non-placeholder SHA-256 run ID"
            )
        return value

    @staticmethod
    def _required_seed() -> int:
        value = os.environ.get("AERO_BENCH_SEED")
        if value is None or DECIMAL_INTEGER.fullmatch(value) is None:
            raise RuntimeError("AERO_BENCH_SEED must be an explicit decimal integer")
        seed = int(value)
        if not 1 <= seed <= 0xFFFFFFFF:
            raise RuntimeError("AERO_BENCH_SEED must be in [1, 2^32-1]")
        return seed

    @staticmethod
    def _required_artifact_dir() -> Path:
        value = os.environ.get("AERO_BENCH_ARTIFACT_DIR")
        if value is None:
            raise RuntimeError("AERO_BENCH_ARTIFACT_DIR is required")
        path = Path(value)
        if not path.is_absolute() or not path.is_dir():
            raise RuntimeError(
                "AERO_BENCH_ARTIFACT_DIR must name an existing directory"
            )
        try:
            entries = tuple(path.iterdir())
        except OSError as exc:
            raise RuntimeError("AERO_BENCH_ARTIFACT_DIR cannot be inspected") from exc
        if entries:
            raise RuntimeError(
                "AERO_BENCH_ARTIFACT_DIR must be empty at provider start"
            )
        return path.resolve(strict=True)

    def _resolve_artifact_path(self, requirement: dict[str, Any]) -> Path:
        relative_path = PurePosixPath(requirement["relative_path"])
        root = self._artifact_dir
        candidate = root.joinpath(*relative_path.parts)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise ProviderError(
                "artifact.invalid", "artifact path cannot be resolved"
            ) from exc
        if not resolved.is_relative_to(root):
            raise ProviderError(
                "artifact.invalid", "artifact path escapes artifact directory"
            )
        current = root
        for part in relative_path.parts:
            current /= part
            if current.is_symlink():
                raise ProviderError(
                    "artifact.invalid", "artifact path contains a symlink"
                )
            if current.exists() and current != candidate and not current.is_dir():
                raise ProviderError(
                    "artifact.invalid", "artifact parent path is not a directory"
                )
        if candidate.exists() and not candidate.is_file():
            raise ProviderError(
                "artifact.invalid", "artifact path is not a regular file"
            )
        return candidate

    async def start(self) -> None:
        await self._backend.start()

    async def close(self) -> None:
        await self._backend.close()

    def attach_stop_event(self, stop_event: asyncio.Event) -> None:
        self._stop_event = stop_event

    def request_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def handle(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._operation_lock:
            if operation == "probe":
                if payload != {}:
                    raise ProviderError(
                        "request.invalid", "provider probe request must be empty"
                    )
                if self._workload_identity is None:
                    raise ProviderError(
                        "state.invalid",
                        "verified provider workload identity is unavailable",
                    )
                return {
                    "schema_version": PROVIDER_PROBE_SCHEMA,
                    "status": "accepting",
                    "run_id": self._workload_identity.run_id,
                    "provider_id": self._workload_identity.provider_id,
                    "adapter": self._workload_identity.adapter,
                    "runtime_image": self._workload_identity.runtime_image,
                    "config_digest": self._workload_identity.config_digest,
                }
            if operation == "prepare":
                return await self._prepare(payload)
            require_session_token(payload, self._session_token)
            if operation == "reset":
                return await self._reset(payload)
            if operation == "step_stage":
                return await self._step_stage(payload)
            if operation == "submit_message":
                return await self._submit_message(payload)
            if operation == "snapshot":
                return await self._snapshot(payload)
            if operation == "finalize":
                return await self._finalize(payload)
            if operation == "shutdown":
                return await self._shutdown(payload)
            raise ProviderError(
                "request.invalid", f"unsupported operation: {operation}"
            )

    async def _prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        require_session_token(payload, self._session_token)
        request = require_object(
            payload,
            fields={
                "artifact_requirements",
                "provider_id",
                "protocol_version",
                "runtime_image",
                "config_digest",
                "network_model",
                "supported_wifi_standards",
                "session_token",
            },
            label="prepare",
        )
        if self._prepared:
            raise ProviderError("state.invalid", "prepare may only be called once")
        if self._workload_identity is None:
            raise ProviderError(
                "state.invalid", "verified workload scenario is unavailable"
            )
        identity = self._workload_identity
        provider_id = require_string(
            request["provider_id"], label="provider_id", pattern=IDENTIFIER
        )
        protocol_version = require_string(
            request["protocol_version"], label="protocol_version"
        )
        if protocol_version != PROTOCOL_VERSION:
            raise ProviderError(
                "identity.mismatch", "protocol version is not aero-bench.ns3-rpc/v5"
            )
        runtime_image = require_string(request["runtime_image"], label="runtime_image")
        if IMMUTABLE_IMAGE.fullmatch(runtime_image) is None:
            raise ProviderError(
                "identity.mismatch", "runtime_image must be immutable"
            )
        config_digest = require_sha256(
            request["config_digest"], label="config_digest"
        )
        network_model = require_string(
            request["network_model"], label="network_model"
        )
        raw_standards = request["supported_wifi_standards"]
        if not isinstance(raw_standards, list):
            raise ProviderError(
                "request.invalid", "supported_wifi_standards must be an array"
            )
        standards = tuple(
            require_string(item, label="supported Wi-Fi standard")
            for item in raw_standards
        )
        if (
            provider_id != identity.provider_id
            or runtime_image != identity.runtime_image
            or config_digest != identity.config_digest
            or network_model != identity.config.network_model
            or standards != identity.config.supported_wifi_standards
        ):
            raise ProviderError(
                "identity.mismatch",
                "prepare identity does not match the verified workload contract",
            )

        raw_artifacts = request["artifact_requirements"]
        if not isinstance(raw_artifacts, list) or len(raw_artifacts) != 1:
            raise ProviderError(
                "request.invalid",
                "ns-3 prepare requires exactly one artifact requirement",
            )
        artifact_requirement = require_artifact_requirement(
            raw_artifacts[0], provider_id=provider_id
        )
        if artifact_requirement != identity.artifact_requirement:
            raise ProviderError(
                "identity.mismatch",
                "prepare artifact requirement differs from workload authority",
            )
        artifact_path = self._resolve_artifact_path(artifact_requirement)
        projection = identity.network
        profiles = projection.radio_profiles
        nodes = projection.node_bindings
        links = projection.links

        profile_token = ";".join(
            ",".join(
                (
                    b64(profile.radio_profile_id),
                    b64(profile.provider_id),
                    b64(profile.wifi_standard),
                    str(profile.frequency_mhz),
                    str(profile.channel_number),
                    str(profile.channel_width_mhz),
                    b64(profile.band),
                    format(profile.tx_power_dbm, ".17g"),
                    format(profile.rx_sensitivity_dbm, ".17g"),
                    b64(profile.data_mode),
                    b64(profile.control_mode),
                    str(profile.max_data_rate_bps),
                )
            )
            for profile in profiles
        )
        node_token = ";".join(
            ",".join(
                (
                    b64(node.node_id),
                    b64(node.entity_id),
                    b64(node.endpoint_id),
                    b64(node.radio_profile_id),
                    *(format(value, ".17g") for value in node.position_enu_m),
                )
            )
            for node in nodes
        )
        link_token = ";".join(
            ",".join(
                (
                    b64(link.link_id),
                    b64(link.source_node_id),
                    b64(link.destination_node_id),
                    str(link.data_rate_bps),
                    str(link.propagation_delay_ns),
                )
            )
            for link in links
        )
        backend_response = await self._backend.request(
            [
                "PREPARE",
                b64(provider_id),
                b64(protocol_version),
                b64(runtime_image),
                b64(NS3_VERSION),
                b64(NS3_COMMIT),
                b64(NETWORK_MODEL),
                profile_token,
                node_token,
                link_token,
            ]
        )
        expected_backend_response = [
            " ".join(
                (
                    "OK",
                    "PREPARE",
                    b64(NETWORK_MODEL),
                    str(len(profiles)),
                    str(len(nodes)),
                    str(len(links)),
                )
            )
        ]
        if backend_response != expected_backend_response:
            raise ProviderError(
                "backend.invalid", "ns-3 PREPARE response does not bind the projection"
            )

        self._provider_id = provider_id
        self._protocol_version = protocol_version
        self._runtime_image = runtime_image
        self._projection = projection
        self._nodes = tuple(node.node_id for node in nodes)
        self._endpoint_to_node = {node.endpoint_id: node.node_id for node in nodes}
        self._node_to_endpoint = {node.node_id: node.endpoint_id for node in nodes}
        self._endpoint_to_entity = {
            node.endpoint_id: node.entity_id for node in nodes
        }
        self._links = links
        self._artifact_requirement = artifact_requirement
        self._artifact_path = artifact_path
        self._config_digest = config_digest
        self._prepared = True
        return {
            "status": "ready",
            "provider_id": provider_id,
            "protocol_version": protocol_version,
            "runtime_image": runtime_image,
            "config_digest": config_digest,
            "scenario_digest": identity.scenario_digest,
            "network_projection_digest": projection.projection_digest,
            "network_model": NETWORK_MODEL,
            "propagation_model": PROPAGATION_MODEL,
            "log_distance_exponent": LOG_DISTANCE_EXPONENT,
            "building_attenuation_db": BUILDING_ATTENUATION_DB,
            "rx_noise_figure_db": RX_NOISE_FIGURE_DB,
            "thermal_noise_density_dbm_hz": THERMAL_NOISE_DENSITY_DBM_HZ,
            "propagation_volume_count": len(projection.propagation_volumes),
            "clock_step_ns": identity.clock.step_ns,
            "clock_max_steps": identity.clock.max_steps,
            "supported_wifi_standards": list(standards),
            "radio_profile_count": len(profiles),
            "node_count": len(nodes),
            "link_count": len(links),
            "ns3_version": NS3_VERSION,
            "ns3_commit": NS3_COMMIT,
        }

    async def _reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_prepared()
        self._require_not_finalized()
        request = require_object(
            payload, fields={"provider_id", "seed", "session_token"}, label="reset"
        )
        self._require_provider(request["provider_id"])
        seed = require_integer(
            request["seed"], label="seed", minimum=1, maximum=0xFFFFFFFF
        )
        if seed != self._expected_seed:
            raise ProviderError(
                "identity.mismatch",
                "reset seed does not match the explicit provider workload contract",
            )
        # The executor owns run identity.  The provider must echo the explicit
        # workload identity; it must never derive a substitute from local state.
        self._run_id = self._expected_run_id
        self._seed = seed
        backend_response = await self._backend.request(["RESET", str(seed)])
        if backend_response != ["OK RESET 0 0 0 0"]:
            raise ProviderError(
                "backend.invalid", "ns-3 RESET response does not bind zero state"
            )
        self._current_tick = 0
        self._current_time_ns = 0
        self._accepted_stage_input = None
        self._link_states = ()
        self._submissions.clear()
        self._deliveries.clear()
        self._dropped.clear()
        self._published_deliveries.clear()
        self._published_drops.clear()
        self._backend_facts = {
            "sim_time_ns": 0,
            "submitted": 0,
            "delivered": 0,
            "pending": 0,
            "delivered_payload_bytes_this_step": 0,
            "delivered_throughput_bps": 0.0,
        }
        return {"receipt": self._receipt()}

    async def _step_stage(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_prepared()
        self._require_not_finalized()
        if self._run_id is None:
            raise ProviderError("state.invalid", "ns-3 provider has not been reset")
        if self._workload_identity is None or self._projection is None:
            raise ProviderError("state.invalid", "ns-3 stage authority is unavailable")
        request = require_object(
            payload, fields={"request", "session_token"}, label="step_stage"
        )
        stage = require_object(
            request["request"],
            fields={
                "schema_version",
                "run_id",
                "scenario_digest",
                "provider_id",
                "target",
                "stage",
                "scene_state",
                "scene_state_digest",
                "predecessor_barriers",
            },
            label="network stage request",
        )
        if stage["schema_version"] != "aero-bench.provider-stage-request/v1":
            raise ProviderError("request.invalid", "network stage request schema is unsupported")
        run_id = require_sha256(stage["run_id"], label="network stage request.run_id")
        if run_id != self._run_id:
            raise ProviderError("identity.mismatch", "stage request belongs to another run")
        scenario_digest = require_sha256(
            stage["scenario_digest"], label="network stage request.scenario_digest"
        )
        if scenario_digest != self._workload_identity.scenario_digest:
            raise ProviderError(
                "identity.mismatch", "stage request belongs to another scenario"
            )
        provider_id = require_string(
            stage["provider_id"],
            label="network stage request.provider_id",
            pattern=IDENTIFIER,
        )
        self._require_provider(provider_id)
        if stage["stage"] != "network":
            raise ProviderError("request.invalid", "ns-3 only accepts network stages")
        target = require_simulation_time(
            stage["target"], label="network stage request.target"
        )
        tick, sim_time_ns = target
        clock = self._workload_identity.clock
        expected_tick = self._current_tick + 1
        expected_time_ns = self._current_time_ns + clock.step_ns
        if (
            tick != expected_tick
            or sim_time_ns != expected_time_ns
            or tick > clock.max_steps
        ):
            raise ProviderError(
                "state.invalid",
                "stage target must equal the next authorized contract clock barrier",
            )
        claimed_scene_digest = require_non_placeholder_sha256(
            stage["scene_state_digest"],
            label="network stage request.scene_state_digest",
        )
        raw_predecessors = require_array(
            stage["predecessor_barriers"],
            label="network stage request.predecessor_barriers",
            minimum=1,
        )
        if len(raw_predecessors) != 1:
            raise ProviderError(
                "request.invalid", "network stage requires exactly one motion predecessor"
            )
        predecessor = require_object(
            raw_predecessors[0],
            fields={"stage", "barrier_digest"},
            label="network stage motion predecessor",
        )
        if predecessor["stage"] != "motion":
            raise ProviderError(
                "request.invalid", "network stage predecessor must be the motion barrier"
            )
        claimed_motion_barrier = require_non_placeholder_sha256(
            predecessor["barrier_digest"],
            label="network stage motion predecessor.barrier_digest",
        )
        staged_input = validate_scene_stage_input(
            stage["scene_state"],
            run_id=run_id,
            scenario_digest=scenario_digest,
            target=target,
            scenario_document=self._workload_identity.scenario.scenario,
            projection=self._projection,
        )
        if staged_input.scene_state_digest != claimed_scene_digest:
            raise ProviderError(
                "identity.mismatch", "stage request SceneState digest does not match"
            )
        if staged_input.motion_barrier_digest != claimed_motion_barrier:
            raise ProviderError(
                "identity.mismatch", "stage request motion predecessor does not match"
            )

        # Validate the entire SceneState and causal closure before mutating backend
        # mobility or advancing simulator time. Every network step consumes exactly
        # one position/loss set derived from that committed SceneState.
        node_token = ";".join(
            ",".join(
                (
                    b64(node_id),
                    format(east_m, ".17g"),
                    format(north_m, ".17g"),
                    format(up_m, ".17g"),
                )
            )
            for node_id, east_m, north_m, up_m in staged_input.node_positions_enu_m
        )
        link_token = ";".join(
            ",".join((b64(link_id), format(attenuation_db, ".17g")))
            for link_id, attenuation_db in staged_input.link_attenuations_db
        )
        mobility_response = await self._backend.request(
            [
                "MOBILITY",
                staged_input.scene_state_digest,
                node_token,
                link_token,
            ]
        )
        expected_mobility_response = [
            " ".join(
                (
                    "OK",
                    "MOBILITY",
                    staged_input.scene_state_digest,
                    str(len(staged_input.node_positions_enu_m)),
                    str(len(staged_input.link_attenuations_db)),
                )
            )
        ]
        if mobility_response != expected_mobility_response:
            raise ProviderError(
                "backend.invalid", "ns-3 MOBILITY response does not bind SceneState"
            )

        lines = await self._backend.request(["STEP", str(sim_time_ns)])
        if len(lines) < 2 or lines[-1] != "END":
            raise ProviderError(
                "backend.invalid", "ns-3 STEP response is not terminated"
            )
        fields = lines[0].split()
        if (
            len(fields) != 7
            or fields[:2] != ["OK", "STEP"]
            or any(DECIMAL_INTEGER.fullmatch(value) is None for value in fields[2:6])
            or SHA256.fullmatch(fields[6]) is None
        ):
            raise ProviderError(
                "backend.invalid", "ns-3 STEP response has invalid facts"
            )
        backend_time, submitted, delivered, pending = map(int, fields[2:6])
        applied_scene_digest = fields[6]
        if min(backend_time, submitted, delivered, pending) < 0:
            raise ProviderError(
                "backend.invalid", "ns-3 STEP response has negative facts"
            )
        if backend_time != sim_time_ns:
            raise ProviderError(
                "backend.invalid", "ns-3 backend reached a different time"
            )
        if applied_scene_digest != staged_input.scene_state_digest:
            raise ProviderError(
                "backend.invalid", "ns-3 STEP applied a different SceneState"
            )
        if (
            submitted != len(self._submissions)
            or delivered > submitted
            or pending != submitted - delivered
        ):
            raise ProviderError(
                "backend.invalid", "ns-3 STEP facts differ from submitted messages"
            )

        record_lines = lines[1:-1]
        link_record_count = len(self._projection.links)
        if len(record_lines) < link_record_count:
            raise ProviderError(
                "backend.invalid", "ns-3 STEP omitted link-state records"
            )
        link_states = _validate_backend_link_states(
            record_lines[:link_record_count],
            projection=self._projection,
            staged_input=staged_input,
        )

        new_deliveries: dict[str, dict[str, Any]] = {}
        for line in record_lines[link_record_count:]:
            delivery = line.split()
            if len(delivery) != 8 or delivery[0] != "DELIVERY":
                raise ProviderError(
                    "backend.invalid", "ns-3 delivery record is malformed"
                )
            (
                _,
                message_token,
                source_token,
                destination_token,
                send_tick,
                send_ns,
                arrival_ns,
                payload_b64,
            ) = delivery
            try:
                message_id = base64.b64decode(message_token, validate=True).decode(
                    "utf-8"
                )
                source = base64.b64decode(source_token, validate=True).decode("utf-8")
                destination = base64.b64decode(destination_token, validate=True).decode(
                    "utf-8"
                )
            except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
                raise ProviderError(
                    "backend.invalid", "ns-3 delivery identity is invalid"
                ) from exc
            message = self._submissions.get(message_id)
            if message is None or message["message_id"] != message_id:
                raise ProviderError(
                    "backend.invalid", "ns-3 delivered an unknown message"
                )
            if message_id in self._deliveries or message_id in new_deliveries:
                raise ProviderError(
                    "backend.invalid", "ns-3 delivered a message more than once"
                )
            if (
                source != message["source_node_id"]
                or destination != message["destination_node_id"]
            ):
                raise ProviderError("backend.invalid", "ns-3 delivery node binding changed")
            if payload_b64 != message["payload_base64"]:
                raise ProviderError("backend.invalid", "ns-3 delivery payload changed")
            if any(
                DECIMAL_INTEGER.fullmatch(value) is None
                for value in (send_tick, send_ns, arrival_ns)
            ):
                raise ProviderError(
                    "backend.invalid", "ns-3 delivery time is malformed"
                )
            send_tick_value, send_ns_value, arrival = map(
                int, (send_tick, send_ns, arrival_ns)
            )
            if min(send_tick_value, send_ns_value, arrival) < 0:
                raise ProviderError("backend.invalid", "ns-3 delivery time is negative")
            if (
                send_tick_value != message["send_time"]["tick"]
                or send_ns_value != message["send_time"]["sim_time_ns"]
            ):
                raise ProviderError(
                    "backend.invalid", "ns-3 delivery send time changed"
                )
            if arrival < message["send_time"]["sim_time_ns"] or arrival > sim_time_ns:
                raise ProviderError(
                    "backend.invalid", "ns-3 delivery time is outside the step"
                )
            trace = {
                "run_id": self._run_id,
                "message_id": message_id,
                "source": message["source"],
                "destination": message["destination"],
                "payload_sha256": message["payload_sha256"],
                "send_time": message["send_time"],
                "outcome": "delivered",
                "arrival_time": {"tick": tick, "sim_time_ns": arrival},
                # ns-3 currently exposes endpoint delivery callbacks but no
                # per-hop trace.  Do not infer a route from the declared graph.
                "path": [],
            }
            new_deliveries[message_id] = {
                "work_order_id": message["work_order_id"],
                "trace": trace,
                "payload_base64": payload_b64,
            }
        if delivered != len(self._deliveries) + len(new_deliveries):
            raise ProviderError(
                "backend.invalid", "ns-3 STEP delivery facts omit or invent callbacks"
            )

        if tick == clock.max_steps:
            for message_id in sorted(self._submissions):
                if message_id not in self._deliveries and message_id not in new_deliveries:
                    self._dropped[message_id] = "simulation-horizon-undelivered"
        self._accepted_stage_input = staged_input
        self._link_states = link_states
        self._deliveries.update(new_deliveries)
        delivered_payload_bytes = sum(
            len(base64.b64decode(delivery["payload_base64"], validate=True))
            for delivery in new_deliveries.values()
        )
        self._backend_facts = {
            "sim_time_ns": backend_time,
            "submitted": submitted,
            "delivered": delivered,
            "pending": pending,
            "delivered_payload_bytes_this_step": delivered_payload_bytes,
            "delivered_throughput_bps": (
                delivered_payload_bytes * 8.0 * 1_000_000_000.0 / clock.step_ns
            ),
        }
        self._current_tick = tick
        self._current_time_ns = sim_time_ns
        return {
            "receipt": self._receipt(),
            "deliveries": [
                new_deliveries[message_id] for message_id in sorted(new_deliveries)
            ],
            "input_scene_state_digest": staged_input.scene_state_digest,
            "predecessor_barriers": [
                {
                    "stage": "motion",
                    "barrier_digest": staged_input.motion_barrier_digest,
                }
            ],
        }

    async def _submit_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_prepared()
        self._require_not_finalized()
        request = require_object(
            payload,
            fields={"message", "session_token", "work_order_id"},
            label="submit_message",
        )
        if self._run_id is None:
            raise ProviderError("state.invalid", "ns-3 provider has not been reset")
        work_order_id = require_string(
            request["work_order_id"], label="work_order_id", pattern=IDENTIFIER
        )
        message = require_object(
            request["message"],
            fields={
                "message_id",
                "source",
                "destination",
                "payload_base64",
                "qos",
                "send_time",
                "payload_sha256",
            },
            label="network message",
        )
        message_id = require_string(
            message["message_id"], label="message_id", pattern=IDENTIFIER
        )
        source = require_string(message["source"], label="source", pattern=IDENTIFIER)
        destination = require_string(
            message["destination"], label="destination", pattern=IDENTIFIER
        )
        payload_b64 = require_base64(
            message["payload_base64"],
            label="payload_base64",
            maximum_bytes=MAX_NETWORK_PAYLOAD_BYTES,
        )
        payload_digest = require_sha256(
            message["payload_sha256"], label="payload_sha256"
        )
        payload_bytes = base64.b64decode(payload_b64, validate=True)
        if hashlib.sha256(payload_bytes).hexdigest() != payload_digest:
            raise ProviderError(
                "request.invalid", "payload_sha256 does not match payload_base64"
            )
        qos = require_object(
            message["qos"],
            fields={"traffic_class", "priority", "reliability"},
            label="network message qos",
        )
        require_string(
            qos["traffic_class"], label="qos.traffic_class", pattern=IDENTIFIER
        )
        require_integer(qos["priority"], label="qos.priority")
        if qos != SUPPORTED_QOS:
            raise ProviderError(
                "request.unsupported",
                "ns-3 UDP backend only supports best_effort priority 0 semantics",
            )
        send_time = require_object(
            message["send_time"],
            fields={"tick", "sim_time_ns"},
            label="message.send_time",
        )
        send_tick = require_integer(send_time["tick"], label="send_time.tick")
        send_ns = require_integer(
            send_time["sim_time_ns"], label="send_time.sim_time_ns"
        )
        if send_ns != self._current_time_ns or send_tick != self._current_tick:
            raise ProviderError(
                "state.invalid",
                "message send time must equal the current provider barrier",
            )
        if source not in self._endpoint_to_node or destination not in self._endpoint_to_node:
            raise ProviderError(
                "request.invalid", "message endpoint is not an authorized logical endpoint"
            )
        source_node = self._endpoint_to_node[source]
        destination_node = self._endpoint_to_node[destination]
        self._require_path(source_node, destination_node)
        if message_id in self._submissions:
            raise ProviderError("state.invalid", "message_id was already submitted")
        lines = await self._backend.request(
            [
                "MESSAGE",
                b64(message_id),
                b64(source_node),
                b64(destination_node),
                payload_b64,
                str(send_tick),
                str(send_ns),
            ]
        )
        if lines != ["OK MESSAGE"]:
            raise ProviderError("backend.invalid", "ns-3 MESSAGE response is malformed")
        stored = {
            "work_order_id": work_order_id,
            "message_id": message_id,
            "source": source,
            "destination": destination,
            "source_node_id": source_node,
            "destination_node_id": destination_node,
            "payload_base64": payload_b64,
            "payload_sha256": payload_digest,
            "send_time": {"tick": send_tick, "sim_time_ns": send_ns},
        }
        self._submissions[message_id] = stored
        return {
            "submission": {
                "run_id": self._run_id,
                "work_order_id": work_order_id,
                "message_id": message_id,
                "source": source,
                "destination": destination,
                "payload_sha256": payload_digest,
                "send_time": stored["send_time"],
                "status": "queued",
            }
        }

    async def _snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_prepared()
        request = require_object(
            payload, fields={"provider_id", "session_token"}, label="snapshot"
        )
        self._require_provider(request["provider_id"])
        lines = await self._backend.request(["SNAPSHOT"])
        fields = lines[0].split() if len(lines) == 1 else []
        if (
            len(fields) != 5
            or fields[:2] != ["OK", "SNAPSHOT"]
            or any(DECIMAL_INTEGER.fullmatch(value) is None for value in fields[2:])
        ):
            raise ProviderError(
                "backend.invalid", "ns-3 SNAPSHOT response has invalid facts"
            )
        sim_time_ns, submitted, delivered = map(int, fields[2:])
        if min(sim_time_ns, submitted, delivered) < 0:
            raise ProviderError(
                "backend.invalid", "ns-3 SNAPSHOT response has negative facts"
            )
        if (
            sim_time_ns != self._current_time_ns
            or submitted != len(self._submissions)
            or delivered != len(self._deliveries)
            or delivered > submitted
        ):
            raise ProviderError(
                "backend.invalid", "ns-3 SNAPSHOT facts differ from service state"
            )
        self._backend_facts = {
            "sim_time_ns": sim_time_ns,
            "submitted": submitted,
            "delivered": delivered,
            "pending": submitted - delivered,
            "delivered_payload_bytes_this_step": self._backend_facts[
                "delivered_payload_bytes_this_step"
            ],
            "delivered_throughput_bps": self._backend_facts[
                "delivered_throughput_bps"
            ],
        }
        return {"snapshot_digest": self._state_digest()}

    async def _finalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_prepared()
        request = require_object(
            payload,
            fields={"provider_id", "request", "session_token"},
            label="finalize",
        )
        self._require_provider(request["provider_id"])
        finalization = require_object(
            request["request"],
            fields={
                "schema_version",
                "run_id",
                "terminal_event",
                "terminal_time",
                "event_chain_root",
            },
            label="provider finalization request",
        )
        if (
            require_string(
                finalization["schema_version"], label="finalization.schema_version"
            )
            != "aero-bench.provider-finalization-request/v1"
        ):
            raise ProviderError(
                "request.invalid", "provider finalization schema version is unsupported"
            )
        run_id = require_sha256(finalization["run_id"], label="finalization.run_id")
        if run_id != self._run_id:
            raise ProviderError(
                "identity.mismatch", "provider finalization belongs to another run"
            )
        if finalization["terminal_event"] != "run.completed":
            raise ProviderError(
                "request.invalid", "provider finalization terminal event is invalid"
            )
        terminal_time = require_object(
            finalization["terminal_time"],
            fields={"tick", "sim_time_ns"},
            label="provider finalization terminal time",
        )
        terminal_tick = require_integer(
            terminal_time["tick"], label="finalization.terminal_time.tick"
        )
        terminal_time_ns = require_integer(
            terminal_time["sim_time_ns"],
            label="finalization.terminal_time.sim_time_ns",
        )
        if (terminal_tick, terminal_time_ns) != (
            self._current_tick,
            self._current_time_ns,
        ):
            raise ProviderError(
                "identity.mismatch",
                "provider finalization time differs from provider time",
            )
        event_chain_root = require_sha256(
            finalization["event_chain_root"], label="finalization.event_chain_root"
        )
        if event_chain_root == "0" * 64:
            raise ProviderError(
                "request.invalid", "provider finalization root cannot be a placeholder"
            )
        if self._finalization_receipt is not None:
            if self._finalization_receipt["event_chain_root"] != event_chain_root:
                raise ProviderError(
                    "state.invalid", "provider finalization root cannot be changed"
                )
            return {"receipt": dict(self._finalization_receipt)}

        artifact_sha256, size_bytes = self._write_artifact()
        assert self._artifact_requirement is not None
        receipt: dict[str, Any] = {
            "schema_version": "aero-bench.provider-finalization-receipt/v1",
            "run_id": run_id,
            "provider_id": self._provider_id,
            "event_chain_root": event_chain_root,
            "artifacts": [
                {
                    "artifact_id": self._artifact_requirement["artifact_id"],
                    "sha256": artifact_sha256,
                    "size_bytes": size_bytes,
                }
            ],
        }
        self._finalization_receipt = receipt
        return {"receipt": dict(receipt)}

    async def _shutdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_prepared()
        request = require_object(
            payload, fields={"provider_id", "session_token"}, label="shutdown"
        )
        self._require_provider(request["provider_id"])
        backend_response = await self._backend.request(["SHUTDOWN"])
        if backend_response != ["OK SHUTDOWN"]:
            raise ProviderError(
                "backend.invalid", "ns-3 SHUTDOWN response is malformed"
            )
        self._prepared = False
        return {"status": "stopped"}

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise ProviderError(
                "state.invalid", "ns-3 provider has not completed prepare"
            )

    def _require_not_finalized(self) -> None:
        if self._finalization_receipt is not None:
            raise ProviderError("state.invalid", "ns-3 provider is already finalized")

    def _require_provider(self, value: Any) -> None:
        if value != self._provider_id:
            raise ProviderError(
                "identity.mismatch", "provider_id does not match prepare"
            )

    def _state_digest(self) -> str:
        return digest(
            {
                "state_schema": STATE_SCHEMA,
                "run_id": self._run_id,
                "seed": self._seed,
                "config_digest": self._config_digest,
                "scenario_digest": (
                    None
                    if self._workload_identity is None
                    else self._workload_identity.scenario_digest
                ),
                "network_projection_digest": (
                    None if self._projection is None else self._projection.projection_digest
                ),
                "network_model": NETWORK_MODEL,
                "clock": (
                    None
                    if self._workload_identity is None
                    else {
                        "step_ns": self._workload_identity.clock.step_ns,
                        "max_steps": self._workload_identity.clock.max_steps,
                        "provider_timeout_ms": (
                            self._workload_identity.clock.provider_timeout_ms
                        ),
                    }
                ),
                "current_tick": self._current_tick,
                "current_time_ns": self._current_time_ns,
                "accepted_stage_input": (
                    None
                    if self._accepted_stage_input is None
                    else {
                        "scene_state_digest": self._accepted_stage_input.scene_state_digest,
                        "motion_barrier_digest": self._accepted_stage_input.motion_barrier_digest,
                        "staged_positions_applied": True,
                        "staged_position_binding": (
                            "scene-state-constant-position-mobility/v1"
                        ),
                        "node_positions_enu_m": [
                            {
                                "node_id": node_id,
                                "east_m": east_m,
                                "north_m": north_m,
                                "up_m": up_m,
                            }
                            for node_id, east_m, north_m, up_m in (
                                self._accepted_stage_input.node_positions_enu_m
                            )
                        ],
                        "link_attenuations_db": [
                            {"link_id": link_id, "attenuation_db": attenuation_db}
                            for link_id, attenuation_db in (
                                self._accepted_stage_input.link_attenuations_db
                            )
                        ],
                        "link_obstruction_volume_ids": [
                            {
                                "link_id": link_id,
                                "volume_ids": list(volume_ids),
                            }
                            for link_id, volume_ids in (
                                self._accepted_stage_input.link_obstruction_volume_ids
                            )
                        ],
                    }
                ),
                "backend": self._backend_facts,
                "link_states": [
                    {
                        "link_id": state.link_id,
                        "source_node_id": state.source_node_id,
                        "destination_node_id": state.destination_node_id,
                        "distance_m": state.distance_m,
                        "base_path_loss_db": state.base_path_loss_db,
                        "obstruction_loss_db": state.obstruction_loss_db,
                        "forward_rssi_dbm": state.forward_rssi_dbm,
                        "reverse_rssi_dbm": state.reverse_rssi_dbm,
                        "forward_snr_db": state.forward_snr_db,
                        "reverse_snr_db": state.reverse_snr_db,
                        "source_queue_packets": state.source_queue_packets,
                        "destination_queue_packets": state.destination_queue_packets,
                        "source_queue_bytes": state.source_queue_bytes,
                        "destination_queue_bytes": state.destination_queue_bytes,
                    }
                    for state in self._link_states
                ],
                "submissions": [
                    {
                        key: submission[key]
                        for key in (
                            "work_order_id",
                            "message_id",
                            "source",
                            "destination",
                            "source_node_id",
                            "destination_node_id",
                            "payload_sha256",
                            "send_time",
                        )
                    }
                    for _, submission in sorted(self._submissions.items())
                ],
                "deliveries": [
                    {
                        "message_id": message_id,
                        "work_order_id": delivery["work_order_id"],
                        "trace": delivery["trace"],
                    }
                    for message_id, delivery in sorted(self._deliveries.items())
                ],
                "dropped": [
                    {"message_id": message_id, "reason": reason}
                    for message_id, reason in sorted(self._dropped.items())
                ],
            }
        )

    def _write_artifact(self) -> tuple[str, int]:
        if (
            self._run_id is None
            or self._provider_id is None
            or self._artifact_requirement is None
            or self._artifact_path is None
        ):
            raise ProviderError(
                "state.invalid", "network delivery artifact requires an active run"
            )
        self._artifact_path = self._resolve_artifact_path(self._artifact_requirement)
        artifact = [
            {
                "source_artifact_id": self._artifact_requirement["artifact_id"],
                "run_id": self._run_id,
                "work_order_id": submission["work_order_id"],
                "message_id": message_id,
                "payload_digest": submission["payload_sha256"],
                "sent_at": submission["send_time"],
                "delivered_at": (
                    self._deliveries[message_id]["trace"]["arrival_time"]
                    if message_id in self._deliveries
                    else None
                ),
            }
            for message_id, submission in sorted(self._submissions.items())
        ]
        content = canonical_bytes(artifact)
        if len(content) > self._artifact_requirement["max_size_bytes"]:
            raise ProviderError(
                "artifact.too-large",
                "network delivery artifact exceeds declared max_size_bytes",
            )
        artifact_parent = self._artifact_path.parent
        try:
            relative_path = PurePosixPath(self._artifact_requirement["relative_path"])
            declared_directories = {
                self._artifact_dir.joinpath(*relative_path.parts[:index])
                for index in range(1, len(relative_path.parts))
            }
            if artifact_parent.exists() and not artifact_parent.is_dir():
                raise ProviderError(
                    "artifact.invalid",
                    "network artifact parent is not a directory",
                )
            for directory in sorted(
                declared_directories, key=lambda path: len(path.parts)
            ):
                if directory.is_symlink():
                    raise ProviderError(
                        "artifact.invalid", "artifact parent path contains a symlink"
                    )
                if directory.exists() and not directory.is_dir():
                    raise ProviderError(
                        "artifact.invalid", "artifact parent path is not a directory"
                    )
                if not directory.exists():
                    directory.mkdir(mode=0o750)
            actual_entries = tuple(self._artifact_dir.rglob("*"))
        except ProviderError:
            raise
        except OSError as exc:
            raise ProviderError(
                "artifact.unavailable",
                "network delivery artifact directory cannot be inspected",
            ) from exc
        declared_directories = {
            self._artifact_dir.joinpath(*relative_path.parts[:index])
            for index in range(1, len(relative_path.parts))
        }
        unexpected = [
            entry
            for entry in actual_entries
            if entry != self._artifact_path and entry not in declared_directories
        ]
        if unexpected or any(entry.is_symlink() for entry in actual_entries):
            raise ProviderError(
                "artifact.invalid",
                "network delivery artifact directory contains an unexpected file",
            )
        temporary_path: Path | None = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".network.delivery.",
                suffix=".tmp",
                dir=artifact_parent,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._artifact_path)
            temporary_path = None
            directory_descriptor = os.open(artifact_parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            root_descriptor = os.open(self._artifact_dir, os.O_RDONLY)
            try:
                os.fsync(root_descriptor)
            finally:
                os.close(root_descriptor)
        except OSError as exc:
            raise ProviderError(
                "artifact.unavailable",
                "network delivery artifact cannot be atomically written",
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        return hashlib.sha256(content).hexdigest(), len(content)

    def _receipt(self) -> dict[str, Any]:
        if self._run_id is None or self._provider_id is None:
            raise ProviderError("state.invalid", "provider has no active run")
        time = {"tick": self._current_tick, "sim_time_ns": self._current_time_ns}
        events: list[dict[str, Any]] = [
            {
                "provider_id": self._provider_id,
                "event_id": f"state.{self._current_tick}",
                "time": time,
                "payload_schema_id": STATE_SCHEMA,
                "payload": [
                    {
                        "name": "accepted_scene_state_digest",
                        "value": (
                            None
                            if self._accepted_stage_input is None
                            else self._accepted_stage_input.scene_state_digest
                        ),
                    },
                    {
                        "name": "motion_predecessor_barrier_digest",
                        "value": (
                            None
                            if self._accepted_stage_input is None
                            else self._accepted_stage_input.motion_barrier_digest
                        ),
                    },
                    {
                        "name": "staged_positions_applied",
                        "value": self._accepted_stage_input is not None,
                    },
                    {
                        "name": "staged_position_binding",
                        "value": (
                            "not-staged"
                            if self._accepted_stage_input is None
                            else "scene-state-constant-position-mobility/v1"
                        ),
                    },
                    {
                        "name": "staged_node_positions_digest",
                        "value": (
                            None
                            if self._accepted_stage_input is None
                            else digest(
                                [
                                    {
                                        "node_id": node_id,
                                        "east_m": east_m,
                                        "north_m": north_m,
                                        "up_m": up_m,
                                    }
                                    for node_id, east_m, north_m, up_m in (
                                        self._accepted_stage_input.node_positions_enu_m
                                    )
                                ]
                            )
                        ),
                    },
                    {
                        "name": "staged_link_attenuations_digest",
                        "value": (
                            None
                            if self._accepted_stage_input is None
                            else digest(
                                [
                                    {
                                        "link_id": link_id,
                                        "attenuation_db": attenuation_db,
                                    }
                                    for link_id, attenuation_db in (
                                        self._accepted_stage_input.link_attenuations_db
                                    )
                                ]
                            )
                        ),
                    },
                    {
                        "name": "staged_link_obstructions_digest",
                        "value": (
                            None
                            if self._accepted_stage_input is None
                            else digest(
                                [
                                    {
                                        "link_id": link_id,
                                        "volume_ids": list(volume_ids),
                                    }
                                    for link_id, volume_ids in (
                                        self._accepted_stage_input.link_obstruction_volume_ids
                                    )
                                ]
                            )
                        ),
                    },
                    {
                        "name": "link_state_digest",
                        "value": (
                            None
                            if not self._link_states
                            else digest(
                                [
                                    {
                                        "link_id": state.link_id,
                                        "source_node_id": state.source_node_id,
                                        "destination_node_id": state.destination_node_id,
                                        "distance_m": state.distance_m,
                                        "base_path_loss_db": state.base_path_loss_db,
                                        "obstruction_loss_db": state.obstruction_loss_db,
                                        "forward_rssi_dbm": state.forward_rssi_dbm,
                                        "reverse_rssi_dbm": state.reverse_rssi_dbm,
                                        "forward_snr_db": state.forward_snr_db,
                                        "reverse_snr_db": state.reverse_snr_db,
                                        "source_queue_packets": state.source_queue_packets,
                                        "destination_queue_packets": (
                                            state.destination_queue_packets
                                        ),
                                        "source_queue_bytes": state.source_queue_bytes,
                                        "destination_queue_bytes": (
                                            state.destination_queue_bytes
                                        ),
                                    }
                                    for state in self._link_states
                                ]
                            )
                        ),
                    },
                    {"name": "link_state_count", "value": len(self._link_states)},
                    {"name": "network_model", "value": NETWORK_MODEL},
                    {
                        "name": "network_projection_digest",
                        "value": (
                            None
                            if self._projection is None
                            else self._projection.projection_digest
                        ),
                    },
                    {"name": "simulator_time_ns", "value": self._current_time_ns},
                    {
                        "name": "submitted_messages",
                        "value": self._backend_facts["submitted"],
                    },
                    {
                        "name": "delivered_messages",
                        "value": self._backend_facts["delivered"],
                    },
                    {
                        "name": "delivered_payload_bytes_this_step",
                        "value": self._backend_facts[
                            "delivered_payload_bytes_this_step"
                        ],
                    },
                    {
                        "name": "delivered_throughput_bps",
                        "value": self._backend_facts["delivered_throughput_bps"],
                    },
                    {"name": "dropped_messages", "value": len(self._dropped)},
                ],
            }
        ]
        if self._projection is not None and self._link_states:
            nodes = {node.node_id: node for node in self._projection.node_bindings}
            profiles = {
                profile.radio_profile_id: profile
                for profile in self._projection.radio_profiles
            }
            obstruction_volume_ids = (
                {}
                if self._accepted_stage_input is None
                else dict(self._accepted_stage_input.link_obstruction_volume_ids)
            )
            for link_state in self._link_states:
                source_node = nodes[link_state.source_node_id]
                destination_node = nodes[link_state.destination_node_id]
                source_profile = profiles[source_node.radio_profile_id]
                destination_profile = profiles[destination_node.radio_profile_id]
                link_document = {
                    "link_id": link_state.link_id,
                    "source_entity_id": source_node.entity_id,
                    "target_entity_id": destination_node.entity_id,
                    "state": (
                        "above-sensitivity"
                        if link_state.forward_rssi_dbm
                        >= destination_profile.rx_sensitivity_dbm
                        and link_state.reverse_rssi_dbm
                        >= source_profile.rx_sensitivity_dbm
                        else "below-sensitivity"
                    ),
                    "public_properties": [
                        {"name": name, "value": value, "provenance": []}
                        for name, value in sorted(
                            {
                                "base_path_loss_db": link_state.base_path_loss_db,
                                "destination_queue_bytes": (
                                    link_state.destination_queue_bytes
                                ),
                                "destination_queue_packets": (
                                    link_state.destination_queue_packets
                                ),
                                "distance_m": link_state.distance_m,
                                "forward_rssi_dbm": link_state.forward_rssi_dbm,
                                "forward_snr_db": link_state.forward_snr_db,
                                "obstruction_loss_db": (
                                    link_state.obstruction_loss_db
                                ),
                                "obstruction_volume_count": len(
                                    obstruction_volume_ids[link_state.link_id]
                                ),
                                "obstruction_volume_ids": canonical_bytes(
                                    list(obstruction_volume_ids[link_state.link_id])
                                ).decode("utf-8"),
                                "pending_messages": self._backend_facts["pending"],
                                "reverse_rssi_dbm": link_state.reverse_rssi_dbm,
                                "reverse_snr_db": link_state.reverse_snr_db,
                                "source_queue_bytes": link_state.source_queue_bytes,
                                "source_queue_packets": (
                                    link_state.source_queue_packets
                                ),
                            }.items()
                        )
                    ],
                    "provenance": [],
                }
                events.append(
                    {
                        "provider_id": self._provider_id,
                        "event_id": "public.network-link",
                        "time": time,
                        "payload_schema_id": "public.network.link.v3",
                        "payload": [
                            {
                                "name": "network_link",
                                "value": canonical_bytes(link_document).decode("utf-8"),
                            }
                        ],
                    }
                )
        requirement = self._artifact_requirement
        if requirement is not None and requirement["visibility"] == "public":
            for index, (message_id, submission) in enumerate(
                sorted(self._submissions.items())
            ):
                reference = {
                    "artifact_id": requirement["artifact_id"],
                    "selector": str(index),
                }
                delivered = self._deliveries.get(message_id)
                drop_reason = self._dropped.get(message_id)
                properties: list[dict[str, Any]] = [
                    {
                        "name": "payload_bytes",
                        "value": len(
                            base64.b64decode(
                                submission["payload_base64"], validate=True
                            )
                        ),
                        "provenance": [reference],
                    },
                    {
                        "name": "work_order_id",
                        "value": submission["work_order_id"],
                        "provenance": [reference],
                    },
                ]
                if delivered is not None:
                    arrival = delivered["trace"]["arrival_time"]
                    latency_ms = (
                        arrival["sim_time_ns"] - submission["send_time"]["sim_time_ns"]
                    ) / 1_000_000.0
                    properties.append(
                        {
                            "name": "latency_ms",
                            "value": latency_ms,
                            "provenance": [reference],
                        }
                    )
                elif drop_reason is not None:
                    properties.append(
                        {
                            "name": "drop_reason",
                            "value": drop_reason,
                            "provenance": [],
                        }
                    )
                link = {
                    "link_id": f"link.{message_id}",
                    "source_entity_id": self._endpoint_to_entity[
                        submission["source"]
                    ],
                    "target_entity_id": self._endpoint_to_entity[
                        submission["destination"]
                    ],
                    "state": (
                        "delivered"
                        if delivered is not None
                        else "dropped"
                        if drop_reason is not None
                        else "queued"
                    ),
                    "public_properties": properties,
                    "provenance": [reference],
                }
                events.append(
                    {
                        "provider_id": self._provider_id,
                        "event_id": "public.network-link",
                        "time": time,
                        "payload_schema_id": "public.network.link.v3",
                        "payload": [
                            {
                                "name": "network_link",
                                "value": canonical_bytes(link).decode("utf-8"),
                            }
                        ],
                    }
                )
                if (
                    delivered is not None
                    and message_id not in self._published_deliveries
                ):
                    # Stage receipts use the barrier time for every event; the
                    # true callback time remains inside the delivery trace.
                    event_time = time
                    evidence = [reference]
                    events.append(
                        {
                            "provider_id": self._provider_id,
                            "event_id": "public.event",
                            "time": event_time,
                            "payload_schema_id": "public.event.v3",
                            "payload": [
                                {"name": "severity", "value": "info"},
                                {"name": "state", "value": "delivered"},
                                {
                                    "name": "evidence",
                                    "value": canonical_bytes(evidence).decode("utf-8"),
                                },
                            ],
                        }
                    )
                    self._published_deliveries.add(message_id)
                elif (
                    drop_reason is not None
                    and message_id not in self._published_drops
                ):
                    events.append(
                        {
                            "provider_id": self._provider_id,
                            "event_id": "public.event",
                            "time": time,
                            "payload_schema_id": "public.event.v3",
                            "payload": [
                                {"name": "severity", "value": "warning"},
                                {"name": "state", "value": "dropped"},
                                {
                                    "name": "evidence",
                                    "value": canonical_bytes([reference]).decode(
                                        "utf-8"
                                    ),
                                },
                            ],
                        }
                    )
                    self._published_drops.add(message_id)
        return {
            "run_id": self._run_id,
            "provider_id": self._provider_id,
            "reached": time,
            "state_digest": self._state_digest(),
            "events": events,
        }

    def _require_path(self, source: str, destination: str) -> None:
        if source == destination:
            return
        adjacency: dict[str, set[str]] = {node: set() for node in self._nodes}
        for link in self._links:
            adjacency[link.source_node_id].add(link.destination_node_id)
            adjacency[link.destination_node_id].add(link.source_node_id)
        pending = [source]
        visited = {source}
        while pending:
            current = pending.pop()
            for neighbour in adjacency[current]:
                if neighbour == destination:
                    return
                if neighbour not in visited:
                    visited.add(neighbour)
                    pending.append(neighbour)
        raise ProviderError("request.invalid", "message endpoints are disconnected")


async def _send_error(writer: asyncio.StreamWriter, error: ProviderError) -> None:
    writer.write(encode_json({"error": {"code": error.code, "detail": error.detail}}))
    await writer.drain()


async def _serve_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, service: Ns3Service
) -> None:
    try:
        while True:
            try:
                frame = await reader.readline()
            except ValueError:
                await _send_error(
                    writer,
                    ProviderError("frame.too-large", "request exceeds frame limit"),
                )
                break
            if not frame:
                break
            if len(frame) > MAX_FRAME_BYTES:
                await _send_error(
                    writer,
                    ProviderError("frame.too-large", "request exceeds frame limit"),
                )
                break
            if not frame.endswith(b"\n"):
                await _send_error(
                    writer,
                    ProviderError(
                        "request.invalid", "request frame must end with a newline"
                    ),
                )
                break
            try:
                request = parse_json(frame)
                operation = request.pop("operation", None)
                if (
                    not isinstance(operation, str)
                    or not operation
                    or any(char.isspace() for char in operation)
                ):
                    raise ProviderError(
                        "request.invalid", "operation must be a non-whitespace token"
                    )
                response = await service.handle(operation, request)
                encoded = encode_json(response)
                if len(encoded) > MAX_FRAME_BYTES:
                    raise ProviderError(
                        "response.too-large", "response exceeds frame limit"
                    )
                writer.write(encoded)
                await writer.drain()
                if operation == "shutdown":
                    service.request_stop()
                    break
            except ProviderError as error:
                await _send_error(writer, error)
            except (ValueError, OverflowError) as error:
                await _send_error(writer, ProviderError("request.invalid", str(error)))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def serve() -> None:
    host = os.environ.get("AERO_BENCH_PROVIDER_BIND_HOST")
    port_text = os.environ.get("AERO_BENCH_PROVIDER_PORT")
    if host is None or not host.strip():
        raise RuntimeError("AERO_BENCH_PROVIDER_BIND_HOST is required")
    if port_text is None or not port_text.isdigit():
        raise RuntimeError("AERO_BENCH_PROVIDER_PORT is required")
    port = int(port_text)
    if not 1024 <= port <= 65535:
        raise RuntimeError("AERO_BENCH_PROVIDER_PORT must be in [1024, 65535]")
    contract_value = os.environ.get("AERO_BENCH_CONTRACT")
    bundle_value = os.environ.get("AERO_BENCH_BUNDLE_DIR")
    provider_value = os.environ.get("AERO_BENCH_WORKLOAD_ID")
    if not contract_value or not bundle_value or not provider_value:
        raise RuntimeError(
            "AERO_BENCH_CONTRACT, AERO_BENCH_BUNDLE_DIR, and "
            "AERO_BENCH_WORKLOAD_ID are required"
        )
    provider_id = require_string(
        provider_value, label="AERO_BENCH_WORKLOAD_ID", pattern=IDENTIFIER
    )
    try:
        workload_identity = _load_ns3_workload_identity(
            contract_path=Path(contract_value),
            bundle_root=Path(bundle_value),
            expected_run_id=Ns3Service._required_run_id(),
            expected_seed=Ns3Service._required_seed(),
            expected_provider_id=provider_id,
            expected_provider_port=port,
        )
    except ProviderError as exc:
        raise RuntimeError("ns-3 workload identity validation failed") from exc
    session_token = os.environ.get("AERO_BENCH_PROVIDER_TOKEN")
    if (
        not isinstance(session_token, str)
        or SHA256.fullmatch(session_token) is None
        or session_token == "0" * 64
    ):
        raise RuntimeError("AERO_BENCH_PROVIDER_TOKEN is required")
    service = Ns3Service(workload_identity, session_token=session_token)
    await service.start()
    try:
        server = await asyncio.start_server(
            lambda reader, writer: _serve_client(reader, writer, service),
            host=host,
            port=port,
            limit=MAX_FRAME_BYTES + 1,
        )
        stop_event = asyncio.Event()
        service.attach_stop_event(stop_event)
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop_event.set)
        try:
            async with server:
                await stop_event.wait()
        finally:
            server.close()
            await server.wait_closed()
    finally:
        await service.close()


def _provider_selfcheck_pose(east_m: float, north_m: float, up_m: float) -> dict[str, Any]:
    return {
        "position": {
            "enu": {
                "east_m": east_m,
                "north_m": north_m,
                "up_m": up_m,
            },
            "ned": {
                "north_m": north_m,
                "east_m": east_m,
                "down_m": -up_m,
            },
            "ecef": {"x_m": east_m, "y_m": north_m, "z_m": up_m},
            "wgs84": {
                "longitude_deg": 0.0,
                "latitude_deg": 0.0,
                "ellipsoid_height_m": 0.0,
            },
            "geoid_separation_m": 0.0,
            "amsl_m": 0.0,
            "terrain_amsl_m": 0.0,
            "agl_m": 0.0,
        },
        "orientation_enu": {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0},
        "orientation_ned": {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0},
    }


def _provider_selfcheck_identity(
    *, runtime_image: str, run_id: str
) -> Ns3WorkloadIdentity:
    config_document = {
        "schema_version": "aero-bench.ns3/v3",
        "provider_id": "network",
        "ns3": {"version": NS3_VERSION, "commit": NS3_COMMIT},
        "network_model": NETWORK_MODEL,
        "supported_wifi_standards": ["802.11ax"],
        "required_commands": ["ns3"],
        "command_timeout_ms": 30_000,
    }
    config = Ns3ConfigIdentity(
        config_digest=digest(config_document),
        network_model=NETWORK_MODEL,
        supported_wifi_standards=("802.11ax",),
    )
    clock = Ns3ClockIdentity(
        step_ns=10_000_000,
        max_steps=2,
        provider_timeout_ms=30_000,
    )
    profile = WifiRadioProfile(
        radio_profile_id="radio.ax",
        provider_id="network",
        wifi_standard="802.11ax",
        frequency_ghz=5.805,
        frequency_mhz=5805,
        channel_number=161,
        channel_width_mhz=20,
        band="BAND_5GHZ",
        tx_power_dbm=20.0,
        rx_sensitivity_dbm=-92.0,
        data_mode="HeMcs11",
        control_mode="HeMcs0",
        max_data_rate_bps=143_000_000,
    )
    network = Ns3NetworkProjection(
        provider_id="network",
        radio_profiles=(profile,),
        node_bindings=(
            NetworkNodeBinding(
                node_id="node.edge",
                entity_id="entity.edge",
                endpoint_id="edge.1",
                radio_profile_id="radio.ax",
                position_enu_m=(10.0, 0.0, 0.0),
            ),
            NetworkNodeBinding(
                node_id="node.uav",
                entity_id="entity.uav",
                endpoint_id="uav.1",
                radio_profile_id="radio.ax",
                position_enu_m=(0.0, 0.0, 0.0),
            ),
        ),
        links=(
            NetworkLinkBinding(
                link_id="link.1",
                source_node_id="node.uav",
                destination_node_id="node.edge",
                data_rate_bps=1_000_000,
                # The largest selfcheck separation is 10 m: ceil(10 / c * 1e9).
                propagation_delay_ns=34,
            ),
        ),
        propagation_volumes=(
            PropagationVolume(
                volume_id="shadow.selfcheck",
                kind="communications_shadow",
                polygon_enu_m=((3.0, -1.0), (3.5, -1.0), (3.5, 1.0), (3.0, 1.0)),
                min_up_m=-1.0,
                max_up_m=1.0,
                attenuation_db=9.0,
            ),
        ),
        projection_digest=digest(
            {
                "network_model": NETWORK_MODEL,
                "profile": "radio.ax",
                "nodes": ["node.edge", "node.uav"],
                "link": "link.1",
            }
        ),
    )
    scenario = ValidatedWorkloadScenario(
        scenario_digest="e" * 64,
        scenario={
            "entities": [
                {
                    "entity_id": "entity.edge",
                    "state": "static",
                    "owner_id": "scenario.compiler",
                    "initial_pose": _provider_selfcheck_pose(10.0, 0.0, 0.0),
                },
                {
                    "entity_id": "entity.uav",
                    "state": "dynamic",
                    "owner_id": "flight",
                    "initial_pose": _provider_selfcheck_pose(0.0, 0.0, 0.0),
                },
            ]
        },
        assets=(),
    )
    artifact_requirement = {
        "artifact_id": "artifact.network.delivery",
        "artifact_type": "network.delivery",
        "producer_id": "network",
        "visibility": "private",
        "relative_path": "evidence/network-delivery.json",
        "max_size_bytes": 65_536,
        "source_asset_id": None,
    }
    return Ns3WorkloadIdentity(
        run_id=run_id,
        provider_id="network",
        adapter=PROVIDER_ADAPTER,
        runtime_image=runtime_image,
        config=config,
        clock=clock,
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        network=network,
        artifact_requirement=artifact_requirement,
    )


async def provider_selfcheck(
    *, runtime_image: str, run_id: str, seed: int, artifact_dir: str
) -> None:
    os.environ["AERO_BENCH_RUN_ID"] = run_id
    os.environ["AERO_BENCH_SEED"] = str(seed)
    os.environ["AERO_BENCH_ARTIFACT_DIR"] = artifact_dir
    session_token = "c" * 64
    os.environ["AERO_BENCH_PROVIDER_TOKEN"] = session_token
    identity = _provider_selfcheck_identity(runtime_image=runtime_image, run_id=run_id)
    service = Ns3Service(identity, session_token=session_token)
    await service.start()
    try:
        prepare = await service.handle(
            "prepare",
            {
                "provider_id": "network",
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": runtime_image,
                "config_digest": identity.config_digest,
                "network_model": NETWORK_MODEL,
                "supported_wifi_standards": ["802.11ax"],
                "session_token": session_token,
                "artifact_requirements": [identity.artifact_requirement],
            },
        )
        payload = base64.b64encode(b"aero-bench-real-ns3-provider").decode("ascii")
        message = {
            "message_id": "message.1",
            "source": "uav.1",
            "destination": "edge.1",
            "payload_base64": payload,
            "payload_sha256": hashlib.sha256(
                base64.b64decode(payload, validate=True)
            ).hexdigest(),
            "qos": dict(SUPPORTED_QOS),
            "send_time": {"tick": 0, "sim_time_ns": 0},
        }
        reset = await service.handle(
            "reset",
            {
                "provider_id": "network",
                "seed": seed,
                "session_token": session_token,
            },
        )
        run = reset["receipt"]["run_id"]
        accepted = await service.handle(
            "submit_message",
            {
                "message": message,
                "session_token": session_token,
                "work_order_id": "work-order.1",
            },
        )
        scene_state_digests: dict[int, str] = {}

        def staged_request(tick: int) -> dict[str, Any]:
            target = {"tick": tick, "sim_time_ns": tick * 10_000_000}
            entities = identity.scenario.scenario["entities"]
            samples: list[dict[str, Any]] = []
            for entity in entities:
                sample_pose = (
                    entity["initial_pose"]
                    if entity["state"] == "static"
                    else _provider_selfcheck_pose(float(tick * 2), 0.0, 0.0)
                )
                sample = {
                    "schema_version": "aero-bench.state-sample/v1",
                    "run_id": run,
                    "scenario_digest": identity.scenario_digest,
                    "at": target,
                    "stage": "motion",
                    "entity_id": entity["entity_id"],
                    "provider_id": (
                        "scenario.compiler"
                        if entity["state"] == "static"
                        else "flight"
                    ),
                    "sample_kind": entity["state"],
                    "pose": sample_pose,
                    "linear_velocity_enu": {
                        "frame_id": "ENU",
                        "east_mps": 0.0,
                        "north_mps": 0.0,
                        "up_mps": 0.0,
                    },
                    "linear_velocity_ned": {
                        "frame_id": "NED",
                        "north_mps": 0.0,
                        "east_mps": 0.0,
                        "down_mps": -0.0,
                    },
                    "angular_velocity_body": None,
                    "mode": None,
                    "armed": None,
                    "battery": None,
                    "health": None,
                    "contacts": [],
                    "attributes": [],
                }
                sample["sample_digest"] = digest(sample)
                samples.append(sample)
            motion_receipt = {
                "schema_version": "aero-bench.stage-receipt/v1",
                "run_id": run,
                "scenario_digest": identity.scenario_digest,
                "at": target,
                "stage": "motion",
                "provider_id": "flight",
                "state_digest": digest({"selfcheck": "motion-state", "tick": tick}),
                "step_receipt_digest": digest(
                    {"selfcheck": "motion-receipt", "tick": tick}
                ),
                "contribution_digest": digest(
                    {"selfcheck": "motion-contribution", "tick": tick}
                ),
                "payload_digest": digest(
                    {"selfcheck": "motion-payload", "tick": tick}
                ),
                "input_scene_state_digest": None,
                "predecessor_barriers": [],
            }
            motion_receipt["receipt_digest"] = digest(motion_receipt)
            motion_barrier = {
                "schema_version": "aero-bench.stage-barrier/v1",
                "run_id": run,
                "scenario_digest": identity.scenario_digest,
                "at": target,
                "stage": "motion",
                "input_scene_state_digest": None,
                "predecessor_barriers": [],
                "provider_ids": ["flight"],
                "receipts": [motion_receipt],
                "receipt_digests": [motion_receipt["receipt_digest"]],
            }
            motion_barrier["barrier_digest"] = digest(motion_barrier)
            scene_state = {
                "schema_version": "aero-bench.scene-state/v1",
                "run_id": run,
                "scenario_digest": identity.scenario_digest,
                "at": target,
                "declared_entity_ids": ["entity.edge", "entity.uav"],
                "samples": samples,
                "stage_barrier": motion_barrier,
                "contribution_digests": [motion_receipt["contribution_digest"]],
                "previous_scene_state_digest": (
                    "0" * 64 if tick == 1 else scene_state_digests[tick - 1]
                ),
            }
            scene_state["scene_state_digest"] = digest(scene_state)
            scene_state_digests[tick] = scene_state["scene_state_digest"]
            return {
                "schema_version": "aero-bench.provider-stage-request/v1",
                "run_id": run,
                "scenario_digest": identity.scenario_digest,
                "provider_id": "network",
                "target": target,
                "stage": "network",
                "scene_state": scene_state,
                "scene_state_digest": scene_state["scene_state_digest"],
                "predecessor_barriers": [
                    {"stage": "motion", "barrier_digest": motion_barrier["barrier_digest"]}
                ],
            }

        step_one = await service.handle(
            "step_stage",
            {"request": staged_request(1), "session_token": session_token},
        )
        step_two = await service.handle(
            "step_stage",
            {"request": staged_request(2), "session_token": session_token},
        )

        def state_evidence(step: dict[str, Any]) -> dict[str, Any]:
            events = [
                event
                for event in step["receipt"]["events"]
                if event["payload_schema_id"] == STATE_SCHEMA
            ]
            if len(events) != 1:
                raise RuntimeError("provider selfcheck state event inventory differs")
            return {item["name"]: item["value"] for item in events[0]["payload"]}

        first_evidence = state_evidence(step_one)
        second_evidence = state_evidence(step_two)
        if (
            first_evidence.get("accepted_scene_state_digest")
            != scene_state_digests[1]
            or second_evidence.get("accepted_scene_state_digest")
            != scene_state_digests[2]
            or first_evidence.get("staged_positions_applied") is not True
            or second_evidence.get("staged_positions_applied") is not True
            or first_evidence.get("staged_node_positions_digest")
            == second_evidence.get("staged_node_positions_digest")
            or first_evidence.get("staged_link_obstructions_digest")
            == second_evidence.get("staged_link_obstructions_digest")
            or first_evidence.get("link_state_digest")
            == second_evidence.get("link_state_digest")
        ):
            raise RuntimeError(
                "provider selfcheck did not apply changing SceneState mobility"
            )
        deliveries = {
            "deliveries": [*step_one["deliveries"], *step_two["deliveries"]]
        }
        if (
            len(deliveries["deliveries"]) != 1
            or deliveries["deliveries"][0]["trace"]["message_id"]
            != message["message_id"]
        ):
            raise RuntimeError(
                "provider selfcheck did not release delivery in its network stage"
            )
        snapshot = await service.handle(
            "snapshot",
            {"provider_id": "network", "session_token": session_token},
        )
        finalization = await service.handle(
            "finalize",
            {
                "provider_id": "network",
                "request": {
                    "schema_version": "aero-bench.provider-finalization-request/v1",
                    "run_id": run,
                    "terminal_event": "run.completed",
                    "terminal_time": {"tick": 2, "sim_time_ns": 20_000_000},
                    "event_chain_root": "d" * 64,
                },
                "session_token": session_token,
            },
        )
        shutdown = await service.handle(
            "shutdown",
            {"provider_id": "network", "session_token": session_token},
        )
        print(
            json.dumps(
                {
                    "accepted": accepted,
                    "deliveries": deliveries,
                    "finalization": finalization,
                    "prepare": prepare,
                    "reset": reset,
                    "shutdown": shutdown,
                    "snapshot": snapshot,
                    "status": "real-wifi-provider-two-barrier-ok",
                    "step_one": step_one,
                    "step_two": step_two,
                },
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
    finally:
        await service.close()


def _provider_selfcheck_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ns3-server.py provider-selfcheck")
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--artifact-dir", required=True)
    return parser.parse_args(argv)


def main() -> int:
    if sys.argv[1:] == ["provider", "serve"]:
        asyncio.run(serve())
        return 0
    if len(sys.argv) > 1:
        if sys.argv[1] == "selfcheck" and len(sys.argv) == 2:
            os.chdir(NS3_ROOT)
            os.execv("./ns3", ["./ns3", "run", "--no-build", "aero-ns3-selfcheck"])
        if sys.argv[1] == "provider-selfcheck":
            arguments = _provider_selfcheck_arguments(sys.argv[2:])
            asyncio.run(
                provider_selfcheck(
                    runtime_image=arguments.runtime_image,
                    run_id=arguments.run_id,
                    seed=arguments.seed,
                    artifact_dir=arguments.artifact_dir,
                )
            )
            return 0
        raise SystemExit(
            "usage: server.py selfcheck | provider-selfcheck "
            "--runtime-image IMAGE --run-id SHA256 --seed INTEGER --artifact-dir DIR"
        )
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
