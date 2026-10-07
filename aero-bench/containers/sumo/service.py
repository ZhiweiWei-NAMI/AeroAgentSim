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
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path, PurePosixPath
from typing import Any

import aero_frame_math as frame_math
from workload_scenario import (
    ValidatedWorkloadScenario,
    WorkloadScenarioError,
    validate_workload_scenario,
)


SUMO_VERSION = "1.27.1"
SUMO_COMMIT = "7717f2379d9e314a0c81c5cec748444de06a2a91"
PROTOCOL_VERSION = "aero-bench.sumo-traci/v3"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
PROVIDER_ADAPTER = "sumo.traci"
STATE_SCHEMA = "sumo.state.v3"
PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE = "public.traffic-light"
PUBLIC_TRAFFIC_LIGHT_SCHEMA = "sumo.traffic_light.v1"
EVIDENCE_SCHEMA = "aero-bench.sumo-evidence/v3"
EVIDENCE_ARTIFACT_TYPE = "sumo.traffic.evidence"
TRAFFIC_RESTRICTION_CAPABILITY = "sumo.traffic.restrictions"
TRAFFIC_RESTRICTION_SCHEMA = "sumo.traffic.restricted.v2"
FINALIZATION_BINDING_SCHEMA = "aero-bench.provider-artifact-finalization/v1"
MAX_FRAME_BYTES = 8 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
PINNED_IMAGE = re.compile(
    r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$"
)
HOST = re.compile(r"^[A-Za-z0-9_.-]+$")
OPERATION = re.compile(r"^[a-z][a-z0-9_.-]*$")
NONNEGATIVE_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")


class SumoServiceError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "request.invalid"):
        self.code = code
        super().__init__(detail)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _parse_json(frame: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            frame,
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SumoServiceError("provider RPC frame is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SumoServiceError("provider RPC frame must be a JSON object")
    return value


def _parse_rpc_frame(frame: bytes) -> dict[str, Any]:
    if not frame.endswith(b"\n"):
        raise SumoServiceError("provider RPC frame must end with a newline")
    return _parse_json(frame)


def _strict_object(
    value: object,
    *,
    required: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SumoServiceError(f"{label} must be a JSON object")
    actual = set(value)
    missing = required - actual
    extra = actual - required
    if missing or extra:
        raise SumoServiceError(
            f"{label} fields are not exact: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _parse_restrictions(value: object, max_steps: int) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise SumoServiceError("SUMO restrictions must be an array")
    result = []
    for item in value:
        raw = _strict_object(item, required=frozenset({
            "event_id", "at_tick", "edge_id", "disallowed_classes",
        }), label="SUMO traffic restriction")
        event_id = _string(raw["event_id"], label="restriction.event_id", pattern=IDENTIFIER)
        tick = _integer(raw["at_tick"], label="restriction.at_tick", minimum=1)
        edge_id = _string(raw["edge_id"], label="restriction.edge_id")
        if tick > max_steps or edge_id.startswith(":") or any(c.isspace() for c in edge_id):
            raise SumoServiceError("SUMO restriction tick or external edge ID is invalid")
        if raw["disallowed_classes"] != ["passenger"]:
            raise SumoServiceError("SUMO restrictions currently implement exactly the passenger class")
        result.append({"event_id": event_id, "at_tick": tick, "edge_id": edge_id,
                       "disallowed_classes": ["passenger"]})
    if result != sorted(result, key=lambda item: (item["at_tick"], item["event_id"])):
        raise SumoServiceError("SUMO restriction schedule must be sorted by tick and event ID")
    for field in ("event_id", "edge_id"):
        if len({item[field] for item in result}) != len(result):
            raise SumoServiceError(f"SUMO restriction {field} values must be unique")
    return tuple(result)


def _string(
    value: object, *, label: str, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise SumoServiceError(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise SumoServiceError(f"{label} has an invalid value")
    return value


def _integer(value: object, *, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SumoServiceError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise SumoServiceError(f"{label} must be >= {minimum}")
    return value


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SumoServiceError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SumoServiceError(f"{label} must be a finite number")
    return result


def _sha256(value: object, *, label: str) -> str:
    result = _string(value, label=label, pattern=SHA256)
    if result == "0" * 64:
        raise SumoServiceError(f"{label} cannot be a placeholder digest")
    return result


def _file_ref(value: object, *, label: str) -> dict[str, str]:
    raw = _strict_object(
        value,
        required=frozenset({"path", "sha256"}),
        label=label,
    )
    path = _string(raw["path"], label=f"{label}.path")
    normalized = PurePosixPath(path)
    if (
        normalized.is_absolute()
        or normalized == PurePosixPath(".")
        or ".." in normalized.parts
        or str(normalized) != path
        or "\\" in path
    ):
        raise SumoServiceError(f"{label}.path must be normalized and relative")
    return {"path": path, "sha256": _sha256(raw["sha256"], label=f"{label}.sha256")}


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
        raise SumoServiceError(f"{label}.visibility must be public or private")
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
        raise SumoServiceError(f"{label}.relative_path must be normalized and relative")
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


def _string_list(value: object, *, label: str, minimum: int = 0) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SumoServiceError(f"{label} must be a JSON array")
    if len(value) < minimum:
        raise SumoServiceError(f"{label} must contain at least {minimum} item(s)")
    return tuple(
        _string(item, label=f"{label}[{index}]") for index, item in enumerate(value)
    )


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Mapping[str, object]) -> str:
    return _digest_bytes(_canonical_json(value))


def _seconds_text(step_length_ns: int) -> str:
    return format(Decimal(step_length_ns) / Decimal(1_000_000_000), "f")


def _require_session_token(payload: object, expected: str) -> None:
    presented = payload.get("session_token") if isinstance(payload, dict) else None
    valid = (
        isinstance(presented, str)
        and SHA256.fullmatch(presented) is not None
        and presented != "0" * 64
    )
    candidate = presented if valid else "0" * 64
    if not valid or not hmac.compare_digest(candidate, expected):
        raise SumoServiceError(
            "SUMO request cannot present the executor-issued run-scoped session token",
            code="principal.denied",
        )


def _safe_runtime_image(value: object) -> str:
    image = _string(value, label="runtime_image", pattern=PINNED_IMAGE)
    if image.rsplit(":", maxsplit=1)[1] == "0" * 64:
        raise SumoServiceError("runtime_image cannot use a placeholder digest")
    return image


@dataclass(frozen=True, slots=True)
class PreparedConfig:
    provider_id: str
    run_id: str
    protocol_version: str
    runtime_image: str
    config_digest: str
    scenario_digest: str
    artifact_requirement: dict[str, str | int | None]
    sumo_binary: str
    traci_port: int
    step_length_ns: int
    clock_max_steps: int
    scenario_config: dict[str, str]
    scenario_files: tuple[dict[str, str], ...]
    object_bindings: tuple[dict[str, str], ...]
    frame_transform: frame_math.RigidTransform
    enu_transform: frame_math.EnuTransform
    geoid_grid: frame_math.ScalarGridSampler
    terrain_grid: frame_math.ScalarGridSampler
    geoid_interpolation: str
    terrain_interpolation: str
    geoid_precision_m: float
    terrain_precision_m: float
    sumo_args: tuple[str, ...]
    required_commands: tuple[str, ...]
    command_timeout_ms: int
    restrictions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    run_id: str
    provider_id: str
    provider_port: int
    runtime_image: str
    config_digest: str
    scenario_digest: str
    scenario: ValidatedWorkloadScenario
    scenario_config: dict[str, str]
    scenario_files: tuple[dict[str, str], ...]
    geoid_grid_file: dict[str, str]
    terrain_grid_file: dict[str, str]
    clock_step_ns: int
    clock_max_steps: int
    object_bindings: tuple[dict[str, str], ...]
    frame_transform: frame_math.RigidTransform
    enu_transform: frame_math.EnuTransform
    geoid_interpolation: str
    terrain_interpolation: str
    geoid_precision_m: float
    terrain_precision_m: float
    artifact_requirement: dict[str, str | int | None]
    capabilities: tuple[str, ...] = ()
    restrictions: tuple[dict[str, Any], ...] = ()

    @property
    def adapter(self) -> str:
        return PROVIDER_ADAPTER


@dataclass(frozen=True, slots=True)
class LiveSumoEntity:
    entity_id: str
    sumo_object_id: str
    kind: str
    position_enu: frame_math.Vector3
    linear_velocity_enu: frame_math.Vector3
    orientation_enu: frame_math.UnitQuaternion
    orientation_ned: frame_math.UnitQuaternion
    speed_mps: float
    yaw_enu_rad: float
    road_id: str
    lane_id: str


class EvidenceWriter:
    def __init__(self, root: Path, requirement: Mapping[str, str | int | None]):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise SumoServiceError("SUMO artifact root must be a directory")
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise SumoServiceError("SUMO artifact root is not writable")
        self.requirement = _artifact_requirement(
            requirement, label="SUMO evidence ArtifactRequirement"
        )
        self.path = self._resolve_output_path()
        self._reject_undeclared_files()

    def _resolve_output_path(self) -> Path:
        relative_path = self.requirement["relative_path"]
        if not isinstance(relative_path, str):
            raise SumoServiceError("SUMO artifact relative_path must be a string")
        relative = PurePosixPath(relative_path)
        candidate = self.root.joinpath(*relative.parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != self.root.parent
        ):
            raise SumoServiceError("SUMO artifact path cannot contain a symbolic link")
        if candidate.exists() and not candidate.is_file():
            raise SumoServiceError("SUMO artifact path must be a regular file")
        return candidate

    def _reject_undeclared_files(self) -> None:
        expected = self.path
        allowed_directories = {
            expected.parent,
            *expected.parent.parents,
        }
        allowed_directories = {
            directory
            for directory in allowed_directories
            if directory == self.root or directory.is_relative_to(self.root)
        }
        for candidate in self.root.rglob("*"):
            if candidate.is_symlink():
                raise SumoServiceError(
                    "SUMO artifact root cannot contain symbolic links"
                )
            if candidate.is_dir() and candidate not in allowed_directories:
                raise SumoServiceError(
                    f"SUMO artifact root contains an undeclared directory: "
                    f"{candidate.relative_to(self.root).as_posix()}"
                )
            if candidate.is_file() and candidate != expected:
                raise SumoServiceError(
                    f"SUMO artifact root contains an undeclared file: "
                    f"{candidate.relative_to(self.root).as_posix()}"
                )

    def _create_declared_parents(self) -> None:
        relative_parent = self.path.parent.relative_to(self.root)
        current = self.root
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise SumoServiceError(
                        "SUMO artifact parent path is not a regular directory"
                    )
            else:
                current.mkdir()

    def reset(self) -> None:
        self._reject_undeclared_files()
        self._create_declared_parents()
        if self.path.exists() and self.path.is_symlink():
            raise SumoServiceError("SUMO evidence path cannot be a symbolic link")
        with self.path.open("wb"):
            pass

    def append(self, record: Mapping[str, object]) -> str:
        encoded = _canonical_json(dict(record)) + b"\n"
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise SumoServiceError("SUMO artifact max_size_bytes must be an integer")
        self._reject_undeclared_files()
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size + len(encoded) > max_size_bytes:
            raise SumoServiceError(
                "SUMO evidence artifact exceeds declared max_size_bytes"
            )
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            if stream.tell() > max_size_bytes:
                raise SumoServiceError(
                    "SUMO evidence artifact exceeds declared max_size_bytes"
                )
            os.fsync(stream.fileno())
        final_size = self.path.stat().st_size
        if final_size > max_size_bytes:
            raise SumoServiceError(
                "SUMO evidence artifact exceeded declared max_size_bytes after append"
            )
        return _digest_bytes(self.path.read_bytes())

    def flush(self) -> None:
        if not self.path.exists() or self.path.is_symlink():
            return
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise SumoServiceError("SUMO artifact max_size_bytes must be an integer")
        if self.path.stat().st_size > max_size_bytes:
            raise SumoServiceError(
                "SUMO evidence artifact exceeds declared max_size_bytes"
            )
        with self.path.open("rb") as stream:
            os.fsync(stream.fileno())

    def finalize(self, finalization_binding: Mapping[str, object]) -> tuple[str, int]:
        self._reject_undeclared_files()
        if not self.path.exists() or self.path.is_symlink() or not self.path.is_file():
            raise SumoServiceError("SUMO evidence artifact is unavailable")
        if not self.path.stat().st_size:
            raise SumoServiceError("SUMO evidence artifact must not be empty")
        self.append(finalization_binding)
        self.flush()
        content = self.path.read_bytes()
        return _digest_bytes(content), len(content)


def _resolve_root_file(root: Path, reference: Mapping[str, str], *, label: str) -> Path:
    relative = PurePosixPath(reference["path"])
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise SumoServiceError(
            f"{label} cannot use a symbolic link: {reference['path']}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SumoServiceError(f"{label} is unavailable: {reference['path']}") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise SumoServiceError(f"{label} is not a regular file: {reference['path']}")
    digest = _digest_bytes(resolved.read_bytes())
    if digest != reference["sha256"]:
        raise SumoServiceError(
            f"{label} SHA-256 mismatch for {reference['path']}: "
            f"expected {reference['sha256']}, got {digest}"
        )
    return resolved


def _load_scalar_grid(
    root: Path,
    reference: Mapping[str, str],
    *,
    label: str,
) -> frame_math.ScalarGridSampler:
    source = _resolve_root_file(root, reference, label=label)
    content = source.read_bytes()
    document = _parse_json(content)
    if _canonical_json(document) + b"\n" != content:
        raise SumoServiceError(f"{label} must be canonical JSON")
    document = _strict_object(
        document,
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
        raise SumoServiceError(f"{label} schema or frame is unsupported")

    def axis(value: object, *, axis_label: str) -> tuple[float, ...]:
        if not isinstance(value, list) or len(value) < 2:
            raise SumoServiceError(f"{axis_label} must contain at least two values")
        return tuple(
            _finite_number(item, label=f"{axis_label}[{index}]")
            for index, item in enumerate(value)
        )

    east_axis = axis(document["east_axis_m"], axis_label=f"{label}.east_axis_m")
    north_axis = axis(
        document["north_axis_m"], axis_label=f"{label}.north_axis_m"
    )
    raw_values = document["values_m"]
    if not isinstance(raw_values, list):
        raise SumoServiceError(f"{label}.values_m must be an array")
    values_rows: list[tuple[float, ...]] = []
    for row_index, row in enumerate(raw_values):
        if not isinstance(row, list):
            raise SumoServiceError(f"{label}.values_m[{row_index}] must be an array")
        values_rows.append(
            tuple(
                _finite_number(
                    item,
                    label=f"{label}.values_m[{row_index}][{column_index}]",
                )
                for column_index, item in enumerate(row)
            )
        )
    try:
        return frame_math.ScalarGridSampler(
            east_axis_m=east_axis,
            north_axis_m=north_axis,
            values_m=tuple(values_rows),
        )
    except frame_math.FrameMathError as exc:
        raise SumoServiceError(f"{label} grid is invalid") from exc


def _load_workload_identity(
    *,
    contract_path: Path,
    bundle_root: Path,
    expected_run_id: str,
    expected_seed: int,
    expected_provider_id: str,
    expected_provider_port: int,
) -> WorkloadIdentity:
    try:
        contract_mode = contract_path.lstat().st_mode
        bundle_mode = bundle_root.lstat().st_mode
        contract_bytes = contract_path.read_bytes()
        raw = _parse_json(contract_bytes)
    except OSError as exc:
        raise SumoServiceError("AERO_BENCH_CONTRACT is unavailable") from exc
    if (
        not contract_path.is_absolute()
        or not bundle_root.is_absolute()
        or stat.S_ISLNK(contract_mode)
        or not stat.S_ISREG(contract_mode)
        or stat.S_ISLNK(bundle_mode)
        or not stat.S_ISDIR(bundle_mode)
    ):
        raise SumoServiceError(
            "AERO_BENCH_CONTRACT and AERO_BENCH_BUNDLE_DIR must be regular paths"
        )
    bundle_root = bundle_root.resolve(strict=True)
    if _canonical_json(raw) + b"\n" != contract_bytes:
        raise SumoServiceError("AERO_BENCH_CONTRACT must be canonical JSON")
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
        raise SumoServiceError("ProviderWorkloadContract schema version is unsupported")
    if contract["role"] != "provider":
        raise SumoServiceError(
            "AERO_BENCH_CONTRACT is not a provider workload contract"
        )
    contract_run_id = _sha256(contract["run_id"], label="contract.run_id")
    if contract_run_id != expected_run_id:
        raise SumoServiceError(
            "ProviderWorkloadContract run_id differs from AERO_BENCH_RUN_ID"
        )
    contract_seed = _integer(contract["seed"], label="contract.seed", minimum=0)
    if contract_seed != expected_seed:
        raise SumoServiceError(
            "ProviderWorkloadContract seed differs from AERO_BENCH_SEED"
        )
    contract_workload_id = _string(
        contract["workload_id"], label="contract.workload_id", pattern=IDENTIFIER
    )
    if contract_workload_id != expected_provider_id:
        raise SumoServiceError(
            "ProviderWorkloadContract workload_id differs from AERO_BENCH_WORKLOAD_ID"
        )
    clock = _strict_object(
        contract["clock"],
        required=frozenset(
            {"authority", "step_ns", "max_steps", "provider_timeout_ms"}
        ),
        label="ProviderWorkloadContract.clock",
    )
    if clock["authority"] != "provider_barrier":
        raise SumoServiceError("ProviderWorkloadContract clock authority is unsupported")
    clock_step_ns = _integer(clock["step_ns"], label="contract.clock.step_ns", minimum=1)
    clock_max_steps = _integer(
        clock["max_steps"], label="contract.clock.max_steps", minimum=1
    )
    _integer(
        clock["provider_timeout_ms"],
        label="contract.clock.provider_timeout_ms",
        minimum=1,
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
        raise SumoServiceError(
            "ProviderWorkloadContract provider_id is not the workload identity"
        )
    if provider["adapter"] != PROVIDER_ADAPTER:
        raise SumoServiceError("SUMO service requires the sumo.traci provider adapter")
    provider_port = _integer(
        provider["port"], label="contract.provider.port", minimum=1024
    )
    if provider_port > 65535 or provider_port != expected_provider_port:
        raise SumoServiceError(
            "ProviderWorkloadContract provider port is not the explicit bind port"
        )
    capabilities = _string_list(
        provider["capabilities"],
        label="contract.provider.capabilities",
        minimum=1,
    )
    if (
        len(capabilities) != len(set(capabilities))
        or any(IDENTIFIER.fullmatch(capability) is None for capability in capabilities)
    ):
        raise SumoServiceError("ProviderWorkloadContract capabilities are invalid")
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
        raise SumoServiceError("ProviderWorkloadContract scenario is invalid") from exc
    sumo = _strict_object(
        scenario.scenario.get("sumo"),
        required=frozenset(
            {
                "provider_id",
                "config_asset_id",
                "network_asset_id",
                "routes_asset_id",
                "additional_asset_id",
                "frame_binding_id",
                "object_bindings",
            }
        ),
        label="ResolvedScenario.sumo",
    )
    if sumo["provider_id"] != provider_id:
        raise SumoServiceError("ResolvedScenario SUMO owner differs from workload")
    scenario_asset_ids = (
        sumo["config_asset_id"],
        sumo["network_asset_id"],
        sumo["routes_asset_id"],
        sumo["additional_asset_id"],
    )
    if any(not isinstance(asset_id, str) for asset_id in scenario_asset_ids):
        raise SumoServiceError("ResolvedScenario SUMO asset IDs are invalid")
    scenario_files = tuple(
        _file_ref(
            scenario.asset(asset_id)["file"],
            label=f"ResolvedScenario SUMO asset {asset_id}",
        )
        for asset_id in scenario_asset_ids
    )
    scenario_config = scenario_files[0]

    raw_object_bindings = sumo["object_bindings"]
    if not isinstance(raw_object_bindings, list):
        raise SumoServiceError("ResolvedScenario SUMO object_bindings must be an array")
    object_bindings: list[dict[str, str]] = []
    for index, value in enumerate(raw_object_bindings):
        label = f"ResolvedScenario.sumo.object_bindings[{index}]"
        binding = _strict_object(
            value,
            required=frozenset({"sumo_object_id", "entity_id", "kind"}),
            label=label,
        )
        kind = _string(binding["kind"], label=f"{label}.kind")
        if kind not in {"vehicle", "person"}:
            raise SumoServiceError(f"{label}.kind is unsupported")
        object_bindings.append(
            {
                "sumo_object_id": _string(
                    binding["sumo_object_id"], label=f"{label}.sumo_object_id"
                ),
                "entity_id": _string(
                    binding["entity_id"],
                    label=f"{label}.entity_id",
                    pattern=IDENTIFIER,
                ),
                "kind": kind,
            }
        )
    object_ids = tuple(item["sumo_object_id"] for item in object_bindings)
    entity_ids = tuple(item["entity_id"] for item in object_bindings)
    if (
        object_ids != tuple(sorted(object_ids))
        or len(object_ids) != len(set(object_ids))
        or len(entity_ids) != len(set(entity_ids))
    ):
        raise SumoServiceError(
            "ResolvedScenario SUMO object bindings must be sorted and bijective"
        )
    raw_entities = scenario.scenario["entities"]
    if not isinstance(raw_entities, list):
        raise SumoServiceError("ResolvedScenario.entities must be an array")
    owned_entities: dict[str, str] = {}
    for index, value in enumerate(raw_entities):
        label = f"ResolvedScenario.entities[{index}]"
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
        if (
            entity["owner_kind"] == "provider"
            and entity["owner_id"] == provider_id
            and entity["authority_kind"] == "sumo_traffic"
            and entity["state"] == "dynamic"
        ):
            entity_id = _string(
                entity["entity_id"], label=f"{label}.entity_id", pattern=IDENTIFIER
            )
            owned_entities[entity_id] = _string(
                entity["kind"], label=f"{label}.kind"
            )
    expected_entity_kinds = {
        binding["entity_id"]: "ugv"
        if binding["kind"] == "vehicle"
        else "pedestrian"
        for binding in object_bindings
    }
    if owned_entities != expected_entity_kinds:
        raise SumoServiceError(
            "ResolvedScenario SUMO object bindings do not close provider-owned entities"
        )

    raw_frame_bindings = scenario.scenario["engine_frame_bindings"]
    if not isinstance(raw_frame_bindings, list):
        raise SumoServiceError(
            "ResolvedScenario.engine_frame_bindings must be an array"
        )
    frame_matches = [
        value
        for value in raw_frame_bindings
        if isinstance(value, dict)
        and value.get("binding_id") == sumo["frame_binding_id"]
    ]
    if len(frame_matches) != 1:
        raise SumoServiceError("ResolvedScenario SUMO frame binding is not unique")
    frame_binding = _strict_object(
        frame_matches[0],
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
        label="ResolvedScenario SUMO frame binding",
    )
    if (
        frame_binding["provider_id"] != provider_id
        or frame_binding["engine"] != "sumo"
        or frame_binding["scene_asset_id"] is not None
        or frame_binding["source_frame_id"] != "sumo_net"
        or frame_binding["target_frame_id"] != "ENU"
    ):
        raise SumoServiceError("ResolvedScenario SUMO frame binding is invalid")
    translation = _strict_object(
        frame_binding["translation_m"],
        required=frozenset({"x_m", "y_m", "z_m"}),
        label="ResolvedScenario SUMO frame translation",
    )
    rotation = _strict_object(
        frame_binding["rotation_matrix"],
        required=frozenset({"rows"}),
        label="ResolvedScenario SUMO frame rotation",
    )
    rows = rotation["rows"]
    if (
        not isinstance(rows, list)
        or len(rows) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in rows)
    ):
        raise SumoServiceError("ResolvedScenario SUMO rotation matrix is invalid")
    try:
        frame_transform = frame_math.RigidTransform(
            rotation=frame_math.RotationMatrix(
                rows=tuple(
                    tuple(
                        _finite_number(
                            component,
                            label=(
                                "ResolvedScenario SUMO rotation matrix"
                                f"[{row_index}][{column_index}]"
                            ),
                        )
                        for column_index, component in enumerate(row)
                    )
                    for row_index, row in enumerate(rows)
                )
            ),
            translation=frame_math.Vector3(
                _finite_number(
                    translation["x_m"], label="ResolvedScenario SUMO translation.x_m"
                ),
                _finite_number(
                    translation["y_m"], label="ResolvedScenario SUMO translation.y_m"
                ),
                _finite_number(
                    translation["z_m"], label="ResolvedScenario SUMO translation.z_m"
                ),
            ),
        )
    except (TypeError, ValueError, frame_math.FrameMathError) as exc:
        raise SumoServiceError("ResolvedScenario SUMO frame transform is invalid") from exc

    frame_authority = _strict_object(
        scenario.scenario["frame_authority"],
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
    if (
        frame_authority["geodetic_frame_id"] != "WGS84"
        or frame_authority["ecef_frame_id"] != "ECEF"
        or frame_authority["enu_frame_id"] != "ENU"
        or frame_authority["ned_frame_id"] != "NED"
    ):
        raise SumoServiceError("ResolvedScenario frame authority names are invalid")
    origin = _strict_object(
        frame_authority["origin"],
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
        required=frozenset(
            {"longitude_deg", "latitude_deg", "ellipsoid_height_m"}
        ),
        label="ResolvedScenario.frame_authority.origin.wgs84",
    )
    geoid_interpolation = _string(
        frame_authority["geoid_interpolation"],
        label="frame_authority.geoid_interpolation",
    )
    terrain_interpolation = _string(
        frame_authority["terrain_interpolation"],
        label="frame_authority.terrain_interpolation",
    )
    if (
        geoid_interpolation not in {"nearest", "bilinear"}
        or terrain_interpolation not in {"nearest", "bilinear"}
    ):
        raise SumoServiceError("ResolvedScenario uses unsupported grid interpolation")
    geoid_precision_m = _finite_number(
        frame_authority["geoid_precision_m"],
        label="frame_authority.geoid_precision_m",
    )
    terrain_precision_m = _finite_number(
        frame_authority["terrain_precision_m"],
        label="frame_authority.terrain_precision_m",
    )
    if geoid_precision_m <= 0.0 or terrain_precision_m <= 0.0:
        raise SumoServiceError("ResolvedScenario datum precision must be positive")
    try:
        enu_transform = frame_math.EnuTransform.from_origin(
            longitude_deg=_finite_number(
                origin_wgs84["longitude_deg"],
                label="ResolvedScenario frame origin longitude",
            ),
            latitude_deg=_finite_number(
                origin_wgs84["latitude_deg"],
                label="ResolvedScenario frame origin latitude",
            ),
            altitude_m=_finite_number(
                origin_wgs84["ellipsoid_height_m"],
                label="ResolvedScenario frame origin ellipsoid height",
            ),
        )
    except frame_math.FrameMathError as exc:
        raise SumoServiceError("ResolvedScenario frame origin is invalid") from exc

    def datum_file(*, field: str, expected_role: str) -> dict[str, str]:
        asset_id = _string(
            frame_authority[field], label=f"frame_authority.{field}", pattern=IDENTIFIER
        )
        try:
            asset = scenario.asset(asset_id)
        except WorkloadScenarioError as exc:
            raise SumoServiceError(f"ResolvedScenario {field} asset is unavailable") from exc
        world = asset.get("world")
        if (
            asset.get("source_kind") != "world"
            or not isinstance(world, dict)
            or world.get("asset_role") != expected_role
            or world.get("media_type") != "application/json"
            or world.get("selector_fragment") is not None
        ):
            raise SumoServiceError(f"ResolvedScenario {field} asset metadata is invalid")
        reference = _file_ref(asset.get("file"), label=f"ResolvedScenario {field}")
        _resolve_root_file(bundle_root, reference, label=f"ResolvedScenario {field}")
        return reference

    geoid_grid_file = datum_file(
        field="geoid_correction_asset_id", expected_role="geoid_model"
    )
    terrain_grid_file = datum_file(
        field="terrain_height_asset_id", expected_role="terrain_model"
    )

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
        raise SumoServiceError("Provider workload runtime command must be non-empty")
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
    config_path = _resolve_root_file(bundle_root, config_file, label="provider config file")
    domain_config = _parse_json(config_path.read_bytes())
    restrictions = _parse_restrictions(domain_config.get("restrictions", []), clock_max_steps)
    if restrictions and TRAFFIC_RESTRICTION_CAPABILITY not in capabilities:
        raise SumoServiceError("SUMO restriction configuration lacks its declared capability")
    _resolve_root_file(bundle_root, schema_file, label="provider config schema file")

    requirements = provider["artifact_requirements"]
    if not isinstance(requirements, list) or len(requirements) != 1:
        raise SumoServiceError(
            "SUMO provider must declare exactly one formal evidence ArtifactRequirement"
        )
    requirement = _artifact_requirement(
        requirements[0], label="SUMO evidence ArtifactRequirement"
    )
    artifact_type = requirement["artifact_type"]
    if artifact_type != EVIDENCE_ARTIFACT_TYPE:
        raise SumoServiceError(
            f"SUMO evidence ArtifactRequirement must use {EVIDENCE_ARTIFACT_TYPE!r}"
        )
    if requirement["producer_id"] != provider_id:
        raise SumoServiceError("SUMO evidence ArtifactRequirement producer_id mismatch")
    if (
        requirement["visibility"] != "private"
        or requirement["source_asset_id"] is not None
    ):
        raise SumoServiceError(
            "SUMO evidence ArtifactRequirement must be a private runtime artifact"
        )

    return WorkloadIdentity(
        run_id=expected_run_id,
        provider_id=provider_id,
        provider_port=provider_port,
        runtime_image=runtime_image,
        config_digest=config_file["sha256"],
        capabilities=tuple(capabilities),
        restrictions=restrictions,
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        scenario_config=scenario_config,
        scenario_files=scenario_files,
        geoid_grid_file=geoid_grid_file,
        terrain_grid_file=terrain_grid_file,
        clock_step_ns=clock_step_ns,
        clock_max_steps=clock_max_steps,
        object_bindings=tuple(object_bindings),
        frame_transform=frame_transform,
        enu_transform=enu_transform,
        geoid_interpolation=geoid_interpolation,
        terrain_interpolation=terrain_interpolation,
        geoid_precision_m=geoid_precision_m,
        terrain_precision_m=terrain_precision_m,
        artifact_requirement=requirement,
    )


class SumoService:
    """Strict JSON-line service backed by a real SUMO TraCI process."""

    def __init__(
        self,
        *,
        scenario_root: Path,
        artifact_root: Path,
        rpc_port: int,
        workload_identity: WorkloadIdentity,
        session_token: str,
    ):
        try:
            self._scenario_root = scenario_root.resolve(strict=True)
        except OSError as exc:
            raise SumoServiceError("SUMO scenario root is unavailable") from exc
        if not self._scenario_root.is_dir():
            raise SumoServiceError("SUMO scenario root must be a directory")
        if not 1024 <= rpc_port <= 65535:
            raise ValueError("SUMO RPC port is outside the user-service range")
        if SHA256.fullmatch(session_token) is None or session_token == "0" * 64:
            raise ValueError(
                "SUMO session token must be an executor-issued SHA-256 digest"
            )
        self._rpc_port = rpc_port
        if workload_identity.provider_port != rpc_port:
            raise ValueError("workload provider port must equal the RPC port")
        self._workload_identity = workload_identity
        self._session_token = session_token
        self._evidence = EvidenceWriter(
            artifact_root, workload_identity.artifact_requirement
        )
        raw_entities = workload_identity.scenario.scenario.get("entities")
        if not isinstance(raw_entities, list):
            raise ValueError("ResolvedScenario entity inventory is unavailable")
        bound_entity_ids = {
            binding["entity_id"] for binding in workload_identity.object_bindings
        }
        self._initial_pose_by_entity = {
            str(entity["entity_id"]): dict(entity["initial_pose"])
            for entity in raw_entities
            if isinstance(entity, dict)
            and entity.get("entity_id") in bound_entity_ids
            and isinstance(entity.get("initial_pose"), dict)
        }
        if set(self._initial_pose_by_entity) != bound_entity_ids:
            raise ValueError("SUMO initial poses do not close object bindings")
        self._entity_lifecycle: dict[str, str] = {}
        self._last_entity_state: dict[str, dict[str, object]] = {}
        self._config: PreparedConfig | None = None
        self._connection: Any | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._process_stdout_path: Path | None = None
        self._process_stderr_path: Path | None = None
        self._process_stdout_offset = 0
        self._process_stderr_offset = 0
        self._current: tuple[int, int] | None = None
        self._finalization_receipt: dict[str, object] | None = None
        self._applied_restrictions: set[str] = set()
        self._restriction_applications: list[dict[str, Any]] = []
        self._shutdown_requested = asyncio.Event()
        self._lock = asyncio.Lock()
        self._closed = False
        self._shutdown_started = False

    @property
    def shutdown_requested(self) -> asyncio.Event:
        return self._shutdown_requested

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def close_runtime(self) -> None:
        self._stop_runtime()
        self._evidence.flush()
        self._closed = True

    def _resolve_scenario_file(self, reference: Mapping[str, str]) -> Path:
        relative = PurePosixPath(reference["path"])
        candidate = self._scenario_root.joinpath(*relative.parts)
        for parent in (self._scenario_root, *candidate.parents):
            if parent == candidate:
                break
            if parent.is_symlink():
                raise SumoServiceError(
                    f"SUMO scenario path contains a symbolic link: {reference['path']}"
                )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SumoServiceError(
                f"SUMO scenario file is unavailable: {reference['path']}"
            ) from exc
        if not resolved.is_file() or not resolved.is_relative_to(self._scenario_root):
            raise SumoServiceError(
                f"SUMO scenario path is not a regular file: {reference['path']}"
            )
        digest = _digest_bytes(resolved.read_bytes())
        if digest != reference["sha256"]:
            raise SumoServiceError(
                f"SUMO scenario SHA-256 mismatch for {reference['path']}: "
                f"expected {reference['sha256']}, got {digest}"
            )
        return resolved

    def _parse_prepare(self, payload: Mapping[str, Any]) -> PreparedConfig:
        _require_session_token(payload, self._session_token)
        raw = _strict_object(
            payload,
            required=frozenset(
                {
                    "provider_id",
                    "run_id",
                    "protocol_version",
                    "runtime_image",
                    "config_digest",
                    "artifact_requirements",
                    "session_token",
                    "sumo",
                    "sumo_binary",
                    "traci_port",
                    "step_length_ns",
                    "sumo_args",
                    "required_commands",
                    "command_timeout_ms",
                    "restrictions",
                }
            ),
            label="SUMO prepare request",
        )
        provider_id = _string(
            raw["provider_id"], label="provider_id", pattern=IDENTIFIER
        )
        if provider_id != self._workload_identity.provider_id:
            raise SumoServiceError(
                "SUMO provider_id differs from the workload contract"
            )
        run_id = _sha256(raw["run_id"], label="run_id")
        if run_id != self._workload_identity.run_id:
            raise SumoServiceError("SUMO run_id differs from the workload contract")
        protocol_version = _string(raw["protocol_version"], label="protocol_version")
        if protocol_version != PROTOCOL_VERSION:
            raise SumoServiceError("SUMO protocol version does not match the service")
        runtime_image = _safe_runtime_image(raw["runtime_image"])
        if runtime_image != self._workload_identity.runtime_image:
            raise SumoServiceError(
                "SUMO runtime image differs from the workload contract"
            )
        config_digest = _sha256(raw["config_digest"], label="config_digest")
        if config_digest != self._workload_identity.config_digest:
            raise SumoServiceError(
                "SUMO config digest differs from the workload contract"
            )
        artifact_requirements = raw["artifact_requirements"]
        if (
            not isinstance(artifact_requirements, list)
            or len(artifact_requirements) != 1
        ):
            raise SumoServiceError("SUMO artifact requirements are not contract-bound")
        requirement = _artifact_requirement(
            artifact_requirements[0], label="SUMO prepare artifact requirement"
        )
        if requirement != self._workload_identity.artifact_requirement:
            raise SumoServiceError(
                "SUMO artifact requirement differs from the workload contract"
            )

        sumo = _strict_object(
            raw["sumo"],
            required=frozenset({"version", "commit"}),
            label="sumo",
        )
        sumo_version = _string(sumo["version"], label="sumo.version")
        sumo_commit = _string(
            sumo["commit"],
            label="sumo.commit",
            pattern=re.compile(r"^[0-9a-f]{40}$"),
        )
        if sumo_version != SUMO_VERSION or sumo_commit != SUMO_COMMIT:
            raise SumoServiceError(
                "SUMO runtime identity does not match the pinned service"
            )

        sumo_binary = _string(
            raw["sumo_binary"], label="sumo_binary", pattern=IDENTIFIER
        )
        traci_port = _integer(raw["traci_port"], label="traci_port", minimum=1024)
        if traci_port > 65535 or traci_port == self._rpc_port:
            raise SumoServiceError("SUMO TraCI port is invalid or collides with RPC")
        step_length_ns = _integer(
            raw["step_length_ns"], label="step_length_ns", minimum=1
        )
        if step_length_ns != self._workload_identity.clock_step_ns:
            raise SumoServiceError(
                "SUMO step_length_ns differs from the ProviderWorkloadContract clock"
            )

        scenario_config = self._workload_identity.scenario_config
        scenario_files = self._workload_identity.scenario_files
        paths = [item["path"] for item in scenario_files]
        if len(paths) != len(set(paths)):
            raise SumoServiceError("ResolvedScenario SUMO file paths must be unique")
        by_path = {item["path"]: item for item in scenario_files}
        if by_path.get(scenario_config["path"]) != scenario_config:
            raise SumoServiceError(
                "ResolvedScenario SUMO config must be included in its asset set"
            )
        for reference in scenario_files:
            self._resolve_scenario_file(reference)
        geoid_grid = _load_scalar_grid(
            self._scenario_root,
            self._workload_identity.geoid_grid_file,
            label="ResolvedScenario geoid scalar grid",
        )
        terrain_grid = _load_scalar_grid(
            self._scenario_root,
            self._workload_identity.terrain_grid_file,
            label="ResolvedScenario terrain scalar grid",
        )

        sumo_args = _string_list(raw["sumo_args"], label="sumo_args")
        forbidden = {
            "--configuration-file",
            "-c",
            "--remote-port",
            "--seed",
            "--step-length",
        }
        overridden = {
            argument.split("=", maxsplit=1)[0]
            for argument in sumo_args
            if argument.startswith("-")
        }
        if forbidden.intersection(overridden):
            raise SumoServiceError(
                "sumo_args cannot override configuration, port, seed, or step length"
            )

        required_commands = _string_list(
            raw["required_commands"], label="required_commands", minimum=1
        )
        if len(required_commands) != len(set(required_commands)):
            raise SumoServiceError("SUMO required_commands must be unique")
        if sumo_binary not in required_commands:
            raise SumoServiceError("required_commands must include sumo_binary")
        for command in required_commands:
            if shutil.which(command) is None:
                raise SumoServiceError(
                    f"SUMO required command is unavailable: {command}"
                )
        command_timeout_ms = _integer(
            raw["command_timeout_ms"], label="command_timeout_ms", minimum=1
        )
        restrictions = _parse_restrictions(raw["restrictions"], self._workload_identity.clock_max_steps)
        if restrictions != self._workload_identity.restrictions:
            raise SumoServiceError("SUMO restriction schedule differs from the pinned Provider configuration")

        try:
            installed_traci = package_version("traci")
        except PackageNotFoundError as exc:
            raise SumoServiceError("declared traci package is not installed") from exc
        if installed_traci != SUMO_VERSION:
            raise SumoServiceError(
                f"traci package identity mismatch: expected {SUMO_VERSION}, got {installed_traci}"
            )
        version_check = subprocess.run(
            (sumo_binary, "--version"),
            check=False,
            capture_output=True,
            text=True,
            timeout=command_timeout_ms / 1000,
        )
        version_output = f"{version_check.stdout}\n{version_check.stderr}"
        if version_check.returncode != 0 or SUMO_VERSION not in version_output:
            raise SumoServiceError(
                f"SUMO executable identity mismatch: {version_output.strip()}"
            )

        return PreparedConfig(
            provider_id=provider_id,
            run_id=run_id,
            protocol_version=protocol_version,
            runtime_image=runtime_image,
            config_digest=config_digest,
            scenario_digest=self._workload_identity.scenario_digest,
            artifact_requirement=self._workload_identity.artifact_requirement,
            sumo_binary=sumo_binary,
            traci_port=traci_port,
            step_length_ns=step_length_ns,
            clock_max_steps=self._workload_identity.clock_max_steps,
            scenario_config=scenario_config,
            scenario_files=scenario_files,
            object_bindings=self._workload_identity.object_bindings,
            frame_transform=self._workload_identity.frame_transform,
            enu_transform=self._workload_identity.enu_transform,
            geoid_grid=geoid_grid,
            terrain_grid=terrain_grid,
            geoid_interpolation=self._workload_identity.geoid_interpolation,
            terrain_interpolation=self._workload_identity.terrain_interpolation,
            geoid_precision_m=self._workload_identity.geoid_precision_m,
            terrain_precision_m=self._workload_identity.terrain_precision_m,
            sumo_args=sumo_args,
            required_commands=required_commands,
            command_timeout_ms=command_timeout_ms,
            restrictions=restrictions,
        )

    def _require_identity(self, payload: Mapping[str, Any]) -> PreparedConfig:
        _require_session_token(payload, self._session_token)
        config = self._config
        if config is None:
            raise SumoServiceError("SUMO provider has not completed prepare")
        if not isinstance(payload, dict):
            raise SumoServiceError("SUMO request must be a JSON object")
        if "provider_id" not in payload or "run_id" not in payload:
            raise SumoServiceError("SUMO request must identify provider and run")
        request = payload
        if request["provider_id"] != config.provider_id:
            raise SumoServiceError("SUMO request provider identity mismatch")
        if request["run_id"] != config.run_id:
            raise SumoServiceError("SUMO request run identity mismatch")
        return config

    def _require_runtime(self) -> tuple[PreparedConfig, Any]:
        config = self._config
        connection = self._connection
        if config is None or connection is None or self._current is None:
            raise SumoServiceError("SUMO provider must be reset before this operation")
        return config, connection

    def _require_not_finalized(self) -> None:
        if self._finalization_receipt is not None:
            raise SumoServiceError("SUMO provider is already finalized")

    def _simulation_time_ns(self, connection: Any) -> int:
        seconds = connection.simulation.getTime()
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise SumoServiceError("SUMO returned a non-numeric simulation time")
        if not math.isfinite(float(seconds)):
            raise SumoServiceError("SUMO returned a non-finite simulation time")
        return int(round(Decimal(str(seconds)) * Decimal(1_000_000_000)))

    @staticmethod
    def _quaternion_payload(
        quaternion: frame_math.UnitQuaternion,
    ) -> dict[str, float]:
        return {
            "qw": quaternion.w,
            "qx": quaternion.x,
            "qy": quaternion.y,
            "qz": quaternion.z,
        }

    def _resolved_pose(
        self,
        config: PreparedConfig,
        *,
        position_enu: frame_math.Vector3,
        orientation_enu: frame_math.UnitQuaternion,
    ) -> dict[str, object]:
        try:
            ecef = config.enu_transform.ecef_from_enu(position_enu)
            longitude_deg, latitude_deg, ellipsoid_height_m = (
                config.enu_transform.enu_to_geodetic(position_enu)
            )
            geoid_separation_m = config.geoid_grid.sample(
                position_enu.x,
                position_enu.y,
                config.geoid_interpolation,
            )
            terrain_amsl_m = config.terrain_grid.sample(
                position_enu.x,
                position_enu.y,
                config.terrain_interpolation,
            )
            orientation_ned = frame_math.UnitQuaternion.from_rotation_matrix(
                frame_math.ENU_TO_NED_ROTATION.compose(
                    orientation_enu.to_rotation_matrix()
                )
            )
        except frame_math.FrameMathError as exc:
            raise SumoServiceError("SUMO state cannot be resolved canonically") from exc
        amsl_m = ellipsoid_height_m - geoid_separation_m
        return {
            "position": {
                "enu": {
                    "east_m": position_enu.x,
                    "north_m": position_enu.y,
                    "up_m": position_enu.z,
                },
                "ned": {
                    "north_m": position_enu.y,
                    "east_m": position_enu.x,
                    "down_m": -position_enu.z,
                },
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
            "orientation_enu": self._quaternion_payload(orientation_enu),
            "orientation_ned": self._quaternion_payload(orientation_ned),
        }

    @staticmethod
    def _zero_velocity() -> tuple[dict[str, object], dict[str, object]]:
        return (
            {
                "frame_id": "ENU",
                "east_mps": 0.0,
                "north_mps": 0.0,
                "up_mps": 0.0,
            },
            {
                "frame_id": "NED",
                "north_mps": 0.0,
                "east_mps": 0.0,
                "down_mps": 0.0,
            },
        )

    def _pending_entity_state(
        self,
        binding: Mapping[str, str],
    ) -> dict[str, object]:
        pose = self._initial_pose_by_entity[binding["entity_id"]]
        orientation = pose.get("orientation_enu")
        if not isinstance(orientation, dict):
            raise SumoServiceError("SUMO entity initial orientation is invalid")
        try:
            quaternion = frame_math.UnitQuaternion(
                w=_finite_number(orientation.get("qw"), label="initial pose.qw"),
                x=_finite_number(orientation.get("qx"), label="initial pose.qx"),
                y=_finite_number(orientation.get("qy"), label="initial pose.qy"),
                z=_finite_number(orientation.get("qz"), label="initial pose.qz"),
            )
            _, _, yaw_enu_rad = frame_math.quaternion_to_rpy(quaternion)
        except frame_math.FrameMathError as exc:
            raise SumoServiceError("SUMO entity initial orientation is invalid") from exc
        velocity_enu, velocity_ned = self._zero_velocity()
        position = pose.get("position")
        if not isinstance(position, dict) or not isinstance(position.get("enu"), dict):
            raise SumoServiceError("SUMO entity initial position is invalid")
        enu = position["enu"]
        return {
            "entity_id": binding["entity_id"],
            "sumo_object_id": binding["sumo_object_id"],
            "kind": binding["kind"],
            "lifecycle": "pending",
            "pose": pose,
            "position_enu_m": {
                "east_m": enu["east_m"],
                "north_m": enu["north_m"],
                "up_m": enu["up_m"],
            },
            "linear_velocity_enu": velocity_enu,
            "linear_velocity_ned": velocity_ned,
            "speed_mps": 0.0,
            "yaw_enu_rad": yaw_enu_rad,
            "road_id": "",
            "lane_id": "",
        }

    def _live_entity_state(
        self,
        config: PreparedConfig,
        connection: Any,
        binding: Mapping[str, str],
    ) -> dict[str, object]:
        object_id = binding["sumo_object_id"]
        domain = (
            connection.vehicle if binding["kind"] == "vehicle" else connection.person
        )
        position = domain.getPosition(object_id)
        speed_mps = _finite_number(
            domain.getSpeed(object_id), label=f"SUMO {object_id} speed"
        )
        angle_deg = _finite_number(
            domain.getAngle(object_id), label=f"SUMO {object_id} angle"
        )
        if speed_mps < 0.0 or not 0.0 <= angle_deg < 360.0:
            raise SumoServiceError("SUMO returned an out-of-domain object state")
        if (
            not isinstance(position, (tuple, list))
            or len(position) != 2
            or not all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
                for value in position
            )
        ):
            raise SumoServiceError("SUMO returned an invalid object position")
        try:
            position_enu = config.frame_transform.apply_position(
                frame_math.Vector3(float(position[0]), float(position[1]), 0.0)
            )
            source_yaw_rad = math.pi / 2.0 - math.radians(angle_deg)
            source_orientation = frame_math.rpy_to_quaternion(
                roll_rad=0.0,
                pitch_rad=0.0,
                yaw_rad=source_yaw_rad,
            )
            orientation_enu = frame_math.UnitQuaternion.from_rotation_matrix(
                config.frame_transform.rotation.compose(
                    source_orientation.to_rotation_matrix()
                )
            )
            _, _, yaw_enu_rad = frame_math.quaternion_to_rpy(orientation_enu)
            heading_enu = config.frame_transform.apply_vector(
                frame_math.Vector3(
                    math.sin(math.radians(angle_deg)),
                    math.cos(math.radians(angle_deg)),
                    0.0,
                )
            )
        except frame_math.FrameMathError as exc:
            raise SumoServiceError("SUMO-to-ENU state transform failed") from exc
        velocity_enu_vector = heading_enu * speed_mps
        velocity_ned_vector = frame_math.enu_to_ned(velocity_enu_vector)
        velocity_enu = {
            "frame_id": "ENU",
            "east_mps": velocity_enu_vector.x,
            "north_mps": velocity_enu_vector.y,
            "up_mps": velocity_enu_vector.z,
        }
        velocity_ned = {
            "frame_id": "NED",
            "north_mps": velocity_ned_vector.x,
            "east_mps": velocity_ned_vector.y,
            "down_mps": velocity_ned_vector.z,
        }
        return {
            "entity_id": binding["entity_id"],
            "sumo_object_id": object_id,
            "kind": binding["kind"],
            "lifecycle": "active",
            "pose": self._resolved_pose(
                config,
                position_enu=position_enu,
                orientation_enu=orientation_enu,
            ),
            "position_enu_m": {
                "east_m": position_enu.x,
                "north_m": position_enu.y,
                "up_m": position_enu.z,
            },
            "linear_velocity_enu": velocity_enu,
            "linear_velocity_ned": velocity_ned,
            "speed_mps": speed_mps,
            "yaw_enu_rad": yaw_enu_rad,
            "road_id": str(domain.getRoadID(object_id)),
            "lane_id": str(domain.getLaneID(object_id)),
        }

    @staticmethod
    def _optional_simulation_ids(simulation: Any, method_name: str) -> set[str]:
        method = getattr(simulation, method_name, None)
        if not callable(method):
            return set()
        values = method()
        if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
            raise SumoServiceError(
                f"SUMO simulation.{method_name} returned an invalid ID list"
            )
        return {str(value) for value in values}

    @staticmethod
    def _traffic_light_states(connection: Any) -> list[dict[str, object]]:
        """Read the authoritative TLS controller state for this TraCI tick."""

        domain = getattr(connection, "trafficlight", None)
        if domain is None or not callable(getattr(domain, "getIDList", None)):
            return []
        raw_ids = domain.getIDList()
        if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, (tuple, list)):
            raise SumoServiceError("SUMO trafficlight.getIDList returned an invalid list")
        states: list[dict[str, object]] = []
        for raw_id in sorted(str(value) for value in raw_ids):
            try:
                state = str(domain.getRedYellowGreenState(raw_id))
                phase_index = int(domain.getPhase(raw_id))
                next_switch_s = float(domain.getNextSwitch(raw_id))
                program_id = str(domain.getProgram(raw_id))
            except (AttributeError, TypeError, ValueError) as exc:
                raise SumoServiceError(
                    f"SUMO traffic-light state is unavailable for {raw_id}"
                ) from exc
            if not state or phase_index < 0 or not math.isfinite(next_switch_s):
                raise SumoServiceError("SUMO returned an invalid traffic-light state")
            states.append(
                {
                    "signal_id": raw_id,
                    "state": state,
                    "phase_index": phase_index,
                    "next_switch_s": next_switch_s,
                    "program_id": program_id,
                    "telemetry_source": "sumo-traci",
                }
            )
        return states

    def _snapshot(
        self, config: PreparedConfig, connection: Any
    ) -> dict[str, object]:
        simulation_time_ns = self._simulation_time_ns(connection)
        expected_by_kind = {
            "vehicle": {
                binding["sumo_object_id"]
                for binding in config.object_bindings
                if binding["kind"] == "vehicle"
            },
            "person": {
                binding["sumo_object_id"]
                for binding in config.object_bindings
                if binding["kind"] == "person"
            },
        }
        observed_by_kind = {
            "vehicle": {str(item) for item in connection.vehicle.getIDList()},
            "person": {str(item) for item in connection.person.getIDList()},
        }
        if any(
            not observed_by_kind[kind] <= expected_by_kind[kind]
            for kind in ("vehicle", "person")
        ):
            raise SumoServiceError(
                "live SUMO objects are not authorized by ResolvedScenario object_bindings"
            )
        arrived_by_kind = {
            "vehicle": self._optional_simulation_ids(
                connection.simulation, "getArrivedIDList"
            ),
            "person": self._optional_simulation_ids(
                connection.simulation, "getArrivedPersonIDList"
            ),
        }
        entities: list[dict[str, object]] = []
        for binding in sorted(
            config.object_bindings,
            key=lambda item: item["entity_id"],
        ):
            entity_id = binding["entity_id"]
            object_id = binding["sumo_object_id"]
            previous_lifecycle = self._entity_lifecycle.get(entity_id)
            if object_id in observed_by_kind[binding["kind"]]:
                if previous_lifecycle in {"arrived", "removed"}:
                    raise SumoServiceError(
                        "SUMO object reappeared after a terminal lifecycle state"
                    )
                state = self._live_entity_state(config, connection, binding)
            elif previous_lifecycle == "active":
                previous = self._last_entity_state.get(entity_id)
                if previous is None:
                    raise SumoServiceError("SUMO active entity has no prior state")
                lifecycle = (
                    "arrived"
                    if object_id in arrived_by_kind[binding["kind"]]
                    else "removed"
                )
                velocity_enu, velocity_ned = self._zero_velocity()
                state = {
                    **previous,
                    "lifecycle": lifecycle,
                    "linear_velocity_enu": velocity_enu,
                    "linear_velocity_ned": velocity_ned,
                    "speed_mps": 0.0,
                }
            elif previous_lifecycle in {"arrived", "removed"}:
                state = dict(self._last_entity_state[entity_id])
            else:
                if object_id in arrived_by_kind[binding["kind"]]:
                    raise SumoServiceError(
                        "SUMO object arrived without an observed authoritative state"
                    )
                state = self._pending_entity_state(binding)
            lifecycle = str(state["lifecycle"])
            self._entity_lifecycle[entity_id] = lifecycle
            self._last_entity_state[entity_id] = state
            entities.append(state)
        return {
            "simulation_time_ns": simulation_time_ns,
            "entities": entities,
            "traffic_lights": self._traffic_light_states(connection),
        }

    def _state_samples(
        self,
        config: PreparedConfig,
        *,
        target: tuple[int, int],
        snapshot: Mapping[str, object],
    ) -> list[dict[str, object]]:
        raw_entities = snapshot.get("entities")
        if not isinstance(raw_entities, list) or len(raw_entities) != len(
            config.object_bindings
        ):
            raise SumoServiceError("SUMO snapshot does not close object bindings")
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        samples: list[dict[str, object]] = []
        for entity in raw_entities:
            if not isinstance(entity, dict):
                raise SumoServiceError("SUMO snapshot entity is invalid")
            attributes = [
                {"name": "kind", "value_type": "str", "value": entity["kind"]},
                {
                    "name": "lane_id",
                    "value_type": "str",
                    "value": entity["lane_id"],
                },
                {
                    "name": "lifecycle",
                    "value_type": "str",
                    "value": entity["lifecycle"],
                },
                {
                    "name": "road_id",
                    "value_type": "str",
                    "value": entity["road_id"],
                },
                {
                    "name": "speed_mps",
                    "value_type": "float",
                    "value": entity["speed_mps"],
                },
                {
                    "name": "sumo_object_id",
                    "value_type": "str",
                    "value": entity["sumo_object_id"],
                },
                {
                    "name": "telemetry_source",
                    "value_type": "str",
                    "value": "sumo-traci",
                },
                {
                    "name": "yaw_enu_rad",
                    "value_type": "float",
                    "value": entity["yaw_enu_rad"],
                },
            ]
            sample: dict[str, object] = {
                "schema_version": "aero-bench.state-sample/v1",
                "run_id": config.run_id,
                "scenario_digest": config.scenario_digest,
                "at": time_value,
                "stage": "motion",
                "entity_id": entity["entity_id"],
                "provider_id": config.provider_id,
                "sample_kind": "dynamic",
                "pose": entity["pose"],
                "linear_velocity_enu": entity["linear_velocity_enu"],
                "linear_velocity_ned": entity["linear_velocity_ned"],
                "angular_velocity_body": None,
                "mode": entity["lifecycle"],
                "armed": None,
                "battery": None,
                "health": None,
                "contacts": [],
                "attributes": attributes,
            }
            sample["sample_digest"] = _digest_json(sample)
            samples.append(sample)
        entity_ids = tuple(str(sample["entity_id"]) for sample in samples)
        if entity_ids != tuple(sorted(entity_ids)) or len(entity_ids) != len(
            set(entity_ids)
        ):
            raise SumoServiceError("SUMO state samples are not sorted and unique")
        return samples

    def _process_log_chunks(
        self,
    ) -> tuple[list[dict[str, object]], tuple[int, int]]:
        paths = (
            ("stderr", self._process_stderr_path, self._process_stderr_offset),
            ("stdout", self._process_stdout_path, self._process_stdout_offset),
        )
        chunks: list[dict[str, object]] = []
        next_offsets: dict[str, int] = {}
        for stream_name, path, offset in paths:
            if path is None:
                raise SumoServiceError("SUMO process log capture is unavailable")
            try:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    content = stream.read()
                    next_offset = stream.tell()
            except OSError as exc:
                raise SumoServiceError(
                    f"cannot read captured SUMO {stream_name}"
                ) from exc
            chunks.append(
                {
                    "stream": stream_name,
                    "offset_bytes": offset,
                    "size_bytes": len(content),
                    "sha256": _digest_bytes(content),
                    "encoding": "base64",
                    "data": base64.b64encode(content).decode("ascii"),
                }
            )
            next_offsets[stream_name] = next_offset
        return chunks, (next_offsets["stdout"], next_offsets["stderr"])

    def _apply_due_restrictions(
        self, config: PreparedConfig, connection: Any, target: tuple[int, int],
    ) -> list[dict[str, Any]]:
        applications = []
        for request in config.restrictions:
            if request["at_tick"] != target[0]:
                continue
            if request["event_id"] in self._applied_restrictions:
                raise SumoServiceError("SUMO restriction was applied more than once")
            native_time_ns = self._simulation_time_ns(connection)
            if native_time_ns != target[1]:
                raise SumoServiceError("SUMO restriction application is outside its native barrier")
            edge_id = request["edge_id"]
            if edge_id not in connection.edge.getIDList():
                raise SumoServiceError(f"SUMO restriction targets an unknown edge: {edge_id}")
            lanes = sorted(lane_id for lane_id in connection.lane.getIDList()
                           if connection.lane.getEdgeID(lane_id) == edge_id)
            if not lanes:
                raise SumoServiceError("SUMO restriction edge has no native lanes")
            before = {lane_id: sorted(connection.lane.getDisallowed(lane_id)) for lane_id in lanes}
            bound_vehicles = {item["sumo_object_id"] for item in config.object_bindings
                              if item["kind"] == "vehicle"}
            active = set(connection.vehicle.getIDList()) & bound_vehicles
            pending = {item["sumo_object_id"] for item in config.object_bindings
                       if item["kind"] == "vehicle" and item["sumo_object_id"] not in active
                       and self._entity_lifecycle[item["entity_id"]] == "pending"}
            candidates = []
            for vehicle_id in sorted(active | pending):
                vehicle_class = connection.vehicle.getVehicleClass(vehicle_id)
                route = list(connection.vehicle.getRoute(vehicle_id))
                route_index = connection.vehicle.getRouteIndex(vehicle_id)
                road_id = connection.vehicle.getRoadID(vehicle_id)
                lifecycle = "active" if vehicle_id in active else "pending"
                remaining = route[route_index + 1:] if lifecycle == "active" else route
                if (vehicle_class in request["disallowed_classes"]
                        and edge_id in remaining
                        and road_id != edge_id and route[-1] != edge_id):
                    candidates.append((vehicle_id, vehicle_class, lifecycle, route, route_index, road_id))
            permissions = []
            for lane_id in lanes:
                requested = sorted(set(before[lane_id]) | set(request["disallowed_classes"]))
                connection.lane.setDisallowed(lane_id, requested)
                observed = sorted(connection.lane.getDisallowed(lane_id))
                if observed != requested:
                    raise SumoServiceError("SUMO lane permission readback differs from the applied request")
                permissions.append({"lane_id": lane_id, "before_disallowed": before[lane_id],
                                    "requested_disallowed": requested, "after_disallowed": observed,
                                    "traci_acknowledged": True})
            effects = []
            for vehicle_id, vehicle_class, lifecycle, route, route_index, road_id in candidates:
                connection.vehicle.rerouteTraveltime(vehicle_id, currentTravelTimes=False)
                effects.append({
                    "vehicle_id": vehicle_id, "vehicle_class": vehicle_class,
                    "lifecycle": lifecycle,
                    "before_route": route, "before_route_index": route_index,
                    "before_road_id": road_id,
                    "after_route": list(connection.vehicle.getRoute(vehicle_id)),
                    "after_route_index": connection.vehicle.getRouteIndex(vehicle_id),
                    "after_road_id": connection.vehicle.getRoadID(vehicle_id),
                    "traci_acknowledged": True,
                })
            applications.append({"schema_version": TRAFFIC_RESTRICTION_SCHEMA,
                                 "request": dict(request), "native_sim_time_ns": native_time_ns,
                                 "permissions": permissions, "route_effects": effects})
            self._applied_restrictions.add(request["event_id"])
        return applications

    def _record(
        self,
        *,
        operation: str,
        target: tuple[int, int],
        snapshot: Mapping[str, object],
    ) -> tuple[str, str]:
        config = self._config
        if config is None:
            raise SumoServiceError("SUMO provider has not completed prepare")
        snapshot_digest = _digest_json(snapshot)
        process_streams, next_log_offsets = self._process_log_chunks()
        evidence_digest = self._evidence.append(
            {
                "schema_version": EVIDENCE_SCHEMA,
                "provider_id": config.provider_id,
                "run_id": config.run_id,
                "operation": operation,
                "tick": target[0],
                "sim_time_ns": target[1],
                "snapshot": dict(snapshot),
                "snapshot_sha256": snapshot_digest,
                "process_streams": process_streams,
                "sumo_version": SUMO_VERSION,
                "sumo_commit": SUMO_COMMIT,
                "runtime_image": config.runtime_image,
                "config_digest": config.config_digest,
                "artifact_id": config.artifact_requirement["artifact_id"],
                "artifact_type": config.artifact_requirement["artifact_type"],
                "scenario_config_sha256": config.scenario_config["sha256"],
                "traffic_restrictions": list(self._restriction_applications),
            }
        )
        self._process_stdout_offset, self._process_stderr_offset = next_log_offsets
        return snapshot_digest, evidence_digest

    def _receipt(
        self, *, target: tuple[int, int], operation: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        config, connection = self._require_runtime()
        snapshot = self._snapshot(config, connection)
        if snapshot["simulation_time_ns"] != target[1]:
            raise SumoServiceError("SUMO TraCI did not reach the requested sim_time_ns")
        snapshot_digest, evidence_digest = self._record(
            operation=operation, target=target, snapshot=snapshot
        )
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        receipt = {
            "run_id": config.run_id,
            "provider_id": config.provider_id,
            "reached": time_value,
            "state_digest": snapshot_digest,
            "events": [
                {
                    "provider_id": config.provider_id,
                    "event_id": f"state.{target[0]}",
                    "time": time_value,
                    "payload_schema_id": STATE_SCHEMA,
                    "payload": [
                        {"name": "snapshot_digest", "value": snapshot_digest},
                        {
                            "name": "evidence_path",
                            "value": config.artifact_requirement["relative_path"],
                        },
                        {"name": "evidence_sha256", "value": evidence_digest},
                        {"name": "simulation_time_ns", "value": target[1]},
                    ],
                },
                {
                    "provider_id": config.provider_id,
                    "event_id": PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE,
                    "time": time_value,
                    "payload_schema_id": PUBLIC_TRAFFIC_LIGHT_SCHEMA,
                    "payload": [
                        {"name": "snapshot_digest", "value": snapshot_digest},
                        {"name": "simulation_time_ns", "value": target[1]},
                        {
                            "name": "traffic_lights_json",
                            "value": _canonical_json(snapshot["traffic_lights"]).decode("utf-8"),
                        },
                    ],
                },
            ],
        }
        for application in self._restriction_applications:
            receipt["events"].append({
                "provider_id": config.provider_id,
                "event_id": f"restriction.{application['request']['event_id']}",
                "time": time_value,
                "payload_schema_id": TRAFFIC_RESTRICTION_SCHEMA,
                "payload": [
                    {"name": "application_json", "value": _canonical_json(application).decode("utf-8")},
                    {"name": "evidence_sha256", "value": evidence_digest},
                ],
            })
        self._restriction_applications = []
        return receipt, snapshot

    def _start_runtime(self, *, seed: int) -> None:
        config = self._config
        if config is None:
            raise SumoServiceError("SUMO provider has not completed prepare")
        self._stop_runtime()
        self._resolve_scenario_file(config.scenario_config)
        command = (
            config.sumo_binary,
            "--configuration-file",
            config.scenario_config["path"],
            "--remote-port",
            str(config.traci_port),
            "--step-length",
            _seconds_text(config.step_length_ns),
            "--seed",
            str(seed),
            *config.sumo_args,
        )
        stdout_log: Any | None = None
        stderr_log: Any | None = None
        try:
            stdout_log = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="aero-bench-sumo-stdout-",
                delete=False,
            )
            stderr_log = tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="aero-bench-sumo-stderr-",
                delete=False,
            )
            process = subprocess.Popen(
                command,
                cwd=self._scenario_root,
                stdin=subprocess.DEVNULL,
                stdout=stdout_log,
                stderr=stderr_log,
            )
        except OSError as exc:
            for stream in (stdout_log, stderr_log):
                if stream is not None:
                    path = Path(stream.name)
                    stream.close()
                    path.unlink(missing_ok=True)
            raise SumoServiceError("cannot start the declared SUMO executable") from exc
        self._process_stdout_path = Path(stdout_log.name)
        self._process_stderr_path = Path(stderr_log.name)
        stdout_log.close()
        stderr_log.close()
        self._process_stdout_offset = 0
        self._process_stderr_offset = 0
        self._process = process
        try:
            import traci

            deadline = time.monotonic() + config.command_timeout_ms / 1000
            connection: Any | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise SumoServiceError(
                        f"SUMO exited before TraCI connection (status={process.returncode})"
                    )
                try:
                    connection = traci.connect(
                        port=config.traci_port,
                        host="127.0.0.1",
                    )
                    break
                except Exception:
                    time.sleep(0.05)
            if connection is None:
                raise SumoServiceError("SUMO TraCI connection timed out")
            version_info = connection.getVersion()
            if (
                not isinstance(version_info, tuple)
                or len(version_info) < 2
                or SUMO_VERSION not in str(version_info[1])
            ):
                raise SumoServiceError(
                    "SUMO TraCI returned an unexpected version identity"
                )
            self._connection = connection
            if self._simulation_time_ns(connection) != 0:
                raise SumoServiceError(
                    "SUMO reset did not start at zero simulation time"
                )
        except Exception:
            self._stop_runtime()
            raise

    def _stop_runtime(self) -> None:
        connection, process = self._connection, self._process
        log_paths = (self._process_stdout_path, self._process_stderr_path)
        self._connection = None
        self._process = None
        self._process_stdout_path = None
        self._process_stderr_path = None
        self._process_stdout_offset = 0
        self._process_stderr_offset = 0
        self._current = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for path in log_paths:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    raise SumoServiceError("cannot remove captured SUMO process log") from exc

    async def handle(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        async with self._lock:
            if self._closed:
                raise SumoServiceError("SUMO provider service is closed")
            if self._shutdown_started and operation != "shutdown":
                raise SumoServiceError("SUMO provider shutdown is already in progress")
            if operation == "probe":
                if payload != {}:
                    raise SumoServiceError("SUMO provider probe request must be empty")
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
                parsed = await asyncio.to_thread(self._parse_prepare, payload)
                if self._config is not None:
                    raise SumoServiceError("SUMO provider prepare called twice")
                self._config = parsed
                return {
                    "status": "ready",
                    "provider_id": self._config.provider_id,
                    "protocol_version": self._config.protocol_version,
                    "runtime_image": self._config.runtime_image,
                    "scenario_digest": self._config.scenario_digest,
                    "sumo_version": SUMO_VERSION,
                    "sumo_commit": SUMO_COMMIT,
                }
            if operation == "reset":
                config = self._require_identity(payload)
                self._require_not_finalized()
                raw = _strict_object(
                    payload,
                    required=frozenset(
                        {"provider_id", "run_id", "seed", "session_token"}
                    ),
                    label="SUMO reset request",
                )
                seed = _integer(raw["seed"], label="seed")
                await asyncio.to_thread(self._start_runtime, seed=seed)
                self._evidence.reset()
                self._entity_lifecycle.clear()
                self._last_entity_state.clear()
                self._applied_restrictions.clear()
                self._restriction_applications = []
                self._current = (0, 0)
                receipt, _ = await asyncio.to_thread(
                    self._receipt, target=(0, 0), operation="reset"
                )
                return {"receipt": receipt}
            if operation == "step_stage":
                config = self._require_identity(payload)
                self._require_not_finalized()
                raw = _strict_object(
                    payload,
                    required=frozenset(
                        {"provider_id", "run_id", "request", "session_token"}
                    ),
                    label="SUMO step_stage request",
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
                    label="SUMO MotionStageRequest",
                )
                if request["schema_version"] != "aero-bench.provider-stage-request/v1":
                    raise SumoServiceError("SUMO motion stage request schema is unsupported")
                if request["run_id"] != config.run_id:
                    raise SumoServiceError("SUMO motion stage run identity mismatch")
                if request["scenario_digest"] != config.scenario_digest:
                    raise SumoServiceError("SUMO motion stage scenario identity mismatch")
                if request["provider_id"] != config.provider_id:
                    raise SumoServiceError("SUMO motion stage provider identity mismatch")
                if request["stage"] != "motion":
                    raise SumoServiceError("SUMO accepts only the motion stage")
                target_raw = _strict_object(
                    request["target"],
                    required=frozenset({"tick", "sim_time_ns"}),
                    label="SUMO motion stage target",
                )
                target = (
                    _integer(target_raw["tick"], label="target.tick", minimum=0),
                    _integer(
                        target_raw["sim_time_ns"], label="target.sim_time_ns", minimum=0
                    ),
                )
                if self._current is None:
                    raise SumoServiceError("SUMO reset must precede step_stage")
                expected = (
                    self._current[0] + 1,
                    self._current[1] + config.step_length_ns,
                )
                if target != expected:
                    raise SumoServiceError(
                        "SUMO motion stage must advance exactly one configured step_length_ns"
                    )
                _, connection = self._require_runtime()
                await asyncio.to_thread(connection.simulationStep)
                self._restriction_applications = await asyncio.to_thread(
                    self._apply_due_restrictions, config, connection, target,
                )
                receipt, snapshot = await asyncio.to_thread(
                    self._receipt, target=target, operation="step_stage"
                )
                samples = self._state_samples(
                    config,
                    target=target,
                    snapshot=snapshot,
                )
                self._current = target
                return {
                    "schema_version": "aero-bench.sumo-motion-stage-response/v1",
                    "run_id": config.run_id,
                    "scenario_digest": config.scenario_digest,
                    "provider_id": config.provider_id,
                    "target": {"tick": target[0], "sim_time_ns": target[1]},
                    "stage": "motion",
                    "receipt": receipt,
                    "samples": samples,
                    "traffic_lights": snapshot["traffic_lights"],
                }
            if operation == "snapshot":
                config = self._require_identity(payload)
                self._require_not_finalized()
                _, connection = self._require_runtime()
                snapshot = await asyncio.to_thread(self._snapshot, config, connection)
                if snapshot["simulation_time_ns"] != self._current[1]:
                    raise SumoServiceError(
                        "SUMO snapshot time moved outside the provider barrier"
                    )
                digest, _ = await asyncio.to_thread(
                    self._record,
                    operation="snapshot",
                    target=self._current,
                    snapshot=snapshot,
                )
                return {"snapshot_digest": digest}
            if operation == "finalize":
                config = self._require_identity(payload)
                raw = _strict_object(
                    payload,
                    required=frozenset(
                        {"provider_id", "run_id", "request", "session_token"}
                    ),
                    label="SUMO finalize request",
                )
                finalization = _strict_object(
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
                    label="SUMO ProviderFinalizationRequest",
                )
                if (
                    _string(
                        finalization["schema_version"],
                        label="finalization.schema_version",
                    )
                    != "aero-bench.provider-finalization-request/v1"
                ):
                    raise SumoServiceError(
                        "SUMO ProviderFinalizationRequest schema is unsupported"
                    )
                if (
                    _sha256(finalization["run_id"], label="finalization.run_id")
                    != config.run_id
                ):
                    raise SumoServiceError("SUMO finalization belongs to another run")
                if finalization["terminal_event"] != "run.completed":
                    raise SumoServiceError(
                        "SUMO finalization terminal event is invalid"
                    )
                terminal_time = _strict_object(
                    finalization["terminal_time"],
                    required=frozenset({"tick", "sim_time_ns"}),
                    label="SUMO finalization terminal time",
                )
                target = (
                    _integer(
                        terminal_time["tick"], label="terminal_time.tick", minimum=0
                    ),
                    _integer(
                        terminal_time["sim_time_ns"],
                        label="terminal_time.sim_time_ns",
                        minimum=0,
                    ),
                )
                if self._current is None or target != self._current:
                    raise SumoServiceError(
                        "SUMO finalization time differs from provider time"
                    )
                event_chain_root = _sha256(
                    finalization["event_chain_root"],
                    label="finalization.event_chain_root",
                )
                if self._finalization_receipt is not None:
                    if (
                        self._finalization_receipt["event_chain_root"]
                        != event_chain_root
                    ):
                        raise SumoServiceError(
                            "SUMO finalization root cannot be changed"
                        )
                    return {"receipt": dict(self._finalization_receipt)}
                finalization_binding = {
                    "schema_version": FINALIZATION_BINDING_SCHEMA,
                    "run_id": config.run_id,
                    "provider_id": config.provider_id,
                    "terminal_event": "run.completed",
                    "terminal_time": {
                        "tick": target[0],
                        "sim_time_ns": target[1],
                    },
                    "event_chain_root": event_chain_root,
                }
                artifact_sha256, size_bytes = await asyncio.to_thread(
                    self._evidence.finalize, finalization_binding
                )
                receipt: dict[str, object] = {
                    "schema_version": "aero-bench.provider-finalization-receipt/v1",
                    "run_id": config.run_id,
                    "provider_id": config.provider_id,
                    "event_chain_root": event_chain_root,
                    "artifacts": [
                        {
                            "artifact_id": config.artifact_requirement["artifact_id"],
                            "sha256": artifact_sha256,
                            "size_bytes": size_bytes,
                        }
                    ],
                }
                self._finalization_receipt = receipt
                return {"receipt": dict(receipt)}
            if operation == "shutdown":
                self._require_identity(payload)
                await asyncio.to_thread(self._stop_runtime)
                self._shutdown_started = True
                return {"status": "stopped"}
            raise SumoServiceError(f"SUMO operation is not supported: {operation}")


async def _write_response(
    writer: asyncio.StreamWriter, response: Mapping[str, Any]
) -> None:
    encoded = _canonical_json(dict(response)) + b"\n"
    if len(encoded) > MAX_FRAME_BYTES:
        raise SumoServiceError("provider response exceeds the frame limit")
    writer.write(encoded)
    await writer.drain()


async def _client_handler(
    service: SumoService, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    shutdown_acknowledged = False
    try:
        while True:
            try:
                frame = await reader.readline()
            except asyncio.LimitOverrunError:
                await _write_response(
                    writer,
                    {
                        "error": {
                            "code": "frame.too-large",
                            "detail": "provider request exceeds the frame limit",
                        }
                    },
                )
                break
            if not frame:
                break
            if len(frame) > MAX_FRAME_BYTES:
                await _write_response(
                    writer,
                    {
                        "error": {
                            "code": "frame.too-large",
                            "detail": "provider request exceeds the frame limit",
                        }
                    },
                )
                break
            if not frame.endswith(b"\n"):
                await _write_response(
                    writer,
                    {
                        "error": {
                            "code": "request.invalid",
                            "detail": "provider RPC frame must end with a newline",
                        }
                    },
                )
                break
            operation: str | None = None
            try:
                request = _parse_rpc_frame(frame)
                operation = request.pop("operation", None)
                if (
                    not isinstance(operation, str)
                    or OPERATION.fullmatch(operation) is None
                ):
                    raise SumoServiceError("provider operation must be a strict token")
                response = await service.handle(operation, request)
            except SumoServiceError as exc:
                response = {"error": {"code": exc.code, "detail": str(exc)}}
            except (OSError, ValueError) as exc:
                response = {"error": {"code": "request.invalid", "detail": str(exc)}}
            await _write_response(writer, response)
            if operation == "shutdown" and response.get("status") == "stopped":
                shutdown_acknowledged = True
                service.request_shutdown()
                break
    finally:
        if shutdown_acknowledged:
            service.request_shutdown()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def serve(service: SumoService, *, host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    server: asyncio.Server | None = None
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, service.request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        server = await asyncio.start_server(
            lambda reader, writer: _client_handler(service, reader, writer),
            host=host,
            port=port,
            limit=MAX_FRAME_BYTES + 1,
        )
        await service.shutdown_requested.wait()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        await asyncio.to_thread(service.close_runtime)
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(signum)
            except (NotImplementedError, RuntimeError):
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AERO-BENCH SUMO TraCI provider")
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
    scenario_root = Path(_required_environment("AERO_BENCH_BUNDLE_DIR"))
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
    seed_text = _required_environment("AERO_BENCH_SEED")
    if NONNEGATIVE_DECIMAL.fullmatch(seed_text) is None:
        raise SystemExit("AERO_BENCH_SEED must be a canonical nonnegative integer")
    workload_identity = _load_workload_identity(
        contract_path=contract_path,
        bundle_root=scenario_root,
        expected_run_id=expected_run_id,
        expected_seed=int(seed_text, 10),
        expected_provider_id=expected_provider_id,
        expected_provider_port=bind_port,
    )
    session_token = _sha256(
        _required_environment("AERO_BENCH_PROVIDER_TOKEN"),
        label="AERO_BENCH_PROVIDER_TOKEN",
    )
    service = SumoService(
        scenario_root=scenario_root,
        artifact_root=artifact_root,
        rpc_port=bind_port,
        workload_identity=workload_identity,
        session_token=session_token,
    )
    asyncio.run(serve(service, host=bind_host, port=bind_port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
