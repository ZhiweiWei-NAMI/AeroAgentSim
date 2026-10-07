from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import stat
import struct
from collections.abc import Iterable, Iterator
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from pydantic import ValidationError

from aero_bench.agent.codex_driver import (
    CodexDriverError,
    interaction_log_from_jsonl_bytes,
)
from aero_bench.agent.session_contracts import (
    AgentSessionManifest,
    InteractionRecord,
    SessionPolicy,
    ToolExecutionResult,
)
from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.ns3.protocol import MAILBOX_OBSERVATION_PREFIX
from aero_bench.runtime.contracts import SceneState
from aero_bench.runtime.evidence import validate_authoritative_runtime_ledger
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.runtime.scene_history import validate_scene_state_history
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.business import replay_business_history
from aero_bench.tasks.inspection.integration import (
    load_inspection_package,
    resolve_inspection_business_endpoint,
    resolve_inspection_run_context,
)
from aero_bench.tasks.inspection.image_evidence import decode_strict_rgb_png
from aero_bench.tasks.inspection.model_gateway_evidence import (
    validate_manifest_tools_digest,
    validate_model_gateway_evidence,
)
from aero_bench.tasks.inspection.contracts import (
    BusinessHistoryRecord,
    BusinessLedgerBinding,
    BusinessStateEvidence,
    CameraFrameDataRecord,
    DefectDetection,
    DefectTruth,
    DeliveryEvidence,
    EvidenceBinding,
    EventLedgerEvidence,
    InspectionEvidenceBundle,
    InspectionArtifactRequirement,
    InspectionReportPayload,
    InspectionTaskPackage,
    ModelInteractionsEvidence,
    ModelSessionManifestEvidence,
    ObservationMetadata,
    PublicSensorFrameRecord,
    TheoreticalBoundsEvidence,
    TrajectoryEvidence,
)


_CORE_EVIDENCE_KINDS = (
    "business_state",
    "trajectory",
    "observation",
    "sensor_frame",
    "camera_frame_data",
    "truth",
    "detection",
    "report",
    "theoretical_bounds",
    "event_log",
    "scene_state_history",
)
_OPTIONAL_DELIVERY_KIND = "delivery"
_CAMERA_FRAME_DATA_KIND = "camera_frame_data"
_OPTIONAL_MODEL_EVIDENCE_KINDS = frozenset(
    {"model_session_manifest", "model_interactions"}
)
_CAMERA_FRAME_ARCHIVE_HEADER = struct.Struct(">8sIQ")
_CAMERA_FRAME_ARCHIVE_MAGIC = b"ABFRAME1"
_MAX_CAMERA_FRAME_ID_BYTES = 4096
_MAX_CAMERA_PNG_BYTES = 2 * 1024 * 1024
_Model = TypeVar("_Model")
_LOADED_EVIDENCE_CAPABILITY = object()


class _LoadedInspectionEvidence:
    """Opaque result issued only after strict sealed-evidence loading."""

    __slots__ = ("__evidence",)

    def __init__(
        self,
        evidence: InspectionEvidenceBundle,
        *,
        capability: object,
    ) -> None:
        if capability is not _LOADED_EVIDENCE_CAPABILITY:
            raise TypeError("loaded Inspection evidence capability is internal")
        self.__evidence = evidence

    @property
    def evidence(self) -> InspectionEvidenceBundle:
        return self.__evidence


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON numbers must be finite")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _strict_json_value(raw: bytes, *, evidence_kind: str) -> Any:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{evidence_kind} evidence is not valid UTF-8") from error
    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=_finite_json_float,
        parse_constant=_reject_json_constant,
    )
    try:
        value, end = decoder.raw_decode(decoded)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(
            f"{evidence_kind} evidence is not valid strict JSON"
        ) from error
    if end != len(decoded):
        raise ValueError(f"{evidence_kind} evidence has trailing JSON data")
    return value


def _canonical_evidence_json_bytes(value: object, *, evidence_kind: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError(
            f"{evidence_kind} evidence cannot be canonical JSON"
        ) from error


def _load_model(
    raw: bytes,
    model_type: type[_Model],
    *,
    evidence_kind: str,
) -> _Model:
    value = _strict_json_value(raw, evidence_kind=evidence_kind)
    if not isinstance(value, dict):
        raise ValueError(f"{evidence_kind} evidence must be a direct JSON object")
    try:
        model = model_type.model_validate(value)  # type: ignore[attr-defined]
    except ValidationError as error:
        raise ValueError(
            f"{evidence_kind} evidence does not match its contract"
        ) from error
    if raw != _canonical_evidence_json_bytes(  # type: ignore[attr-defined]
        model.model_dump(mode="json"), evidence_kind=evidence_kind
    ):
        raise ValueError(f"{evidence_kind} evidence is not canonical JSON")
    return model


def _load_models(
    raw: bytes,
    model_type: type[_Model],
    *,
    evidence_kind: str,
) -> tuple[_Model, ...]:
    value = _strict_json_value(raw, evidence_kind=evidence_kind)
    if not isinstance(value, list):
        raise ValueError(f"{evidence_kind} evidence must be a direct JSON array")
    models: list[_Model] = []
    try:
        for item in value:
            models.append(model_type.model_validate(item))  # type: ignore[attr-defined]
    except ValidationError as error:
        raise ValueError(
            f"{evidence_kind} evidence does not match its contract"
        ) from error
    if raw != _canonical_evidence_json_bytes(
        [model.model_dump(mode="json") for model in models],  # type: ignore[attr-defined]
        evidence_kind=evidence_kind,
    ):
        raise ValueError(f"{evidence_kind} evidence is not canonical JSON")
    return tuple(models)


def _normalize_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        "\x00" in value
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or "\\" in value
        or str(path) != value
    ):
        raise ValueError("sealed evidence path must be normalized and relative")
    return path


_SEAL_MANIFEST_NAME = "seal-manifest.json"
_OPEN_COMMON_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
# A regular file can be swapped for a FIFO after inventory. O_NONBLOCK makes
# that race fail closed rather than allowing a verifier worker to block forever.
_OPEN_FILE_FLAGS = _OPEN_COMMON_FLAGS | getattr(os, "O_NONBLOCK", 0)
_OPEN_DIRECTORY_FLAGS = _OPEN_COMMON_FLAGS | os.O_DIRECTORY


def _open_root(sealed_root: str | Path) -> int:
    """Open every root path component without following a symbolic link."""

    try:
        root = Path(os.path.abspath(os.fspath(sealed_root)))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("sealed evidence root is not available") from error
    if not root.is_absolute() or not root.anchor:
        raise ValueError("sealed evidence root is not available")
    try:
        descriptor = os.open(root.anchor, _OPEN_DIRECTORY_FLAGS)
    except OSError as error:
        raise ValueError("sealed evidence root is not available") from error
    try:
        for component in root.parts[1:]:
            next_descriptor = _open_directory_at(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("sealed evidence root must be a non-link directory")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_directory_at(parent_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as error:
        raise ValueError("sealed evidence directory cannot be opened safely") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("sealed evidence path is not a directory")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _root_inventory(root_descriptor: int) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()

    def visit(directory_descriptor: int, relative: PurePosixPath) -> None:
        try:
            names = os.listdir(directory_descriptor)
        except OSError as error:
            raise ValueError("sealed evidence root cannot be read") from error
        for name in names:
            entry_relative = relative / name
            try:
                entry_stat = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise ValueError("sealed evidence path cannot be inspected") from error
            if stat.S_ISLNK(entry_stat.st_mode):
                raise ValueError(
                    f"sealed evidence contains a symbolic link: {entry_relative.as_posix()}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                directories.add(entry_relative.as_posix())
                child_descriptor = _open_directory_at(directory_descriptor, name)
                try:
                    visit(child_descriptor, entry_relative)
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(entry_stat.st_mode):
                files.add(entry_relative.as_posix())
            else:
                raise ValueError(
                    f"sealed evidence contains a non-regular path: {entry_relative.as_posix()}"
                )

    visit(root_descriptor, PurePosixPath("."))
    return files, directories


def _expected_directories(relative_paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for value in relative_paths:
        path = _normalize_relative_path(value)
        parent = path.parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _iter_regular_file(
    root_descriptor: int,
    relative_path: PurePosixPath,
    *,
    expected_size: int,
    max_size_bytes: int,
    description: str,
    sha256: str | None = None,
    linewise: bool = False,
) -> Iterator[bytes]:
    current_descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(relative_path.parts):
            flags = (
                _OPEN_FILE_FLAGS
                if index == len(relative_path.parts) - 1
                else _OPEN_DIRECTORY_FLAGS
            )
            try:
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=current_descriptor,
                )
            except OSError as error:
                raise ValueError(f"{description} is unavailable") from error
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        metadata = os.fstat(current_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{description} is not a regular file")
        if metadata.st_size != expected_size:
            raise ValueError(f"{description} size mismatch")
        if metadata.st_size > max_size_bytes:
            raise ValueError(f"{description} exceeds its declared size limit")
        consumed = 0
        digest = hashlib.sha256()
        with os.fdopen(os.dup(current_descriptor), "rb") as stream:
            while True:
                remaining = expected_size - consumed + 1
                try:
                    chunk = stream.readline(remaining) if linewise else stream.read(min(65_536, remaining))
                except OSError as error:
                    raise ValueError(f"{description} cannot be read safely") from error
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > expected_size:
                    raise ValueError(f"{description} changed while being read")
                digest.update(chunk)
                yield chunk
        after = os.fstat(current_descriptor)
        if consumed != expected_size or (
            metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(f"{description} changed while being read")
        if sha256 is not None and digest.hexdigest() != sha256:
            raise ValueError(f"{description} digest mismatch")
    finally:
        os.close(current_descriptor)


def _read_regular_file(
    root_descriptor: int,
    relative_path: PurePosixPath,
    *,
    expected_size: int,
    max_size_bytes: int,
    description: str,
) -> bytes:
    return b"".join(_iter_regular_file(
        root_descriptor, relative_path, expected_size=expected_size,
        max_size_bytes=max_size_bytes, description=description,
    ))


def _iter_artifact_content(
    root_descriptor: int,
    artifact: ArtifactRecord,
    *,
    max_size_bytes: int,
    linewise: bool = False,
) -> Iterator[bytes]:
    return _iter_regular_file(
        root_descriptor,
        _normalize_relative_path(artifact.relative_path),
        expected_size=artifact.size_bytes,
        max_size_bytes=max_size_bytes,
        description=f"sealed evidence artifact {artifact.artifact_id}",
        sha256=artifact.sha256,
        linewise=linewise,
    )


def _read_artifact_bytes(
    root_descriptor: int, artifact: ArtifactRecord, *, max_size_bytes: int
) -> bytes:
    return b"".join(_iter_artifact_content(root_descriptor, artifact, max_size_bytes=max_size_bytes))


def _load_camera_frame_archive(
    raw: bytes,
    *,
    source_artifact_id: str,
) -> tuple[CameraFrameDataRecord, ...]:
    if not raw:
        raise ValueError("camera frame-data archive is empty")
    cursor = 0
    records: list[CameraFrameDataRecord] = []
    seen_frame_ids: set[str] = set()
    while cursor < len(raw):
        if len(raw) - cursor < _CAMERA_FRAME_ARCHIVE_HEADER.size:
            raise ValueError("camera frame-data archive has a truncated header")
        magic, frame_id_size, image_size = _CAMERA_FRAME_ARCHIVE_HEADER.unpack_from(
            raw, cursor
        )
        cursor += _CAMERA_FRAME_ARCHIVE_HEADER.size
        if (
            magic != _CAMERA_FRAME_ARCHIVE_MAGIC
            or not 1 <= frame_id_size <= _MAX_CAMERA_FRAME_ID_BYTES
            or not 1 <= image_size <= _MAX_CAMERA_PNG_BYTES
        ):
            raise ValueError("camera frame-data archive header is invalid")
        record_end = cursor + frame_id_size + image_size
        if record_end > len(raw):
            raise ValueError("camera frame-data archive record is truncated")
        frame_id_raw = raw[cursor : cursor + frame_id_size]
        cursor += frame_id_size
        try:
            frame_id = frame_id_raw.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("camera frame-data frame ID is not ASCII") from error
        if frame_id in seen_frame_ids:
            raise ValueError("camera frame-data archive repeats a frame ID")
        png_bytes = raw[cursor:record_end]
        cursor = record_end
        width, height = decode_strict_rgb_png(png_bytes)
        records.append(
            CameraFrameDataRecord(
                source_artifact_id=source_artifact_id,
                frame_id=frame_id,
                image_sha256=hashlib.sha256(png_bytes).hexdigest(),
                size_bytes=len(png_bytes),
                width=width,
                height=height,
                media_type="image/png",
                png_bytes=png_bytes,
            )
        )
        seen_frame_ids.add(frame_id)
    return tuple(records)


_MODEL_REQUEST_KEYS = frozenset(
    {
        "request_index",
        "request_digest",
        "request_size_bytes",
        "model",
        "reasoning_effort",
        "instructions_sha256",
        "initial_input_sha256",
        "tools_digest",
        "history_item_types",
        "image_inputs",
    }
)
_MODEL_RESPONSE_KEYS = frozenset(
    {
        "request_index",
        "response_id",
        "response_digest",
        "response_size_bytes",
        "model",
        "function_calls",
        "assistant_messages",
        "usage",
    }
)
_MODEL_IMAGE_INPUT_KEYS = frozenset(
    {
        "source_call_id",
        "content_index",
        "operation_id",
        "observation_id",
        "frame_id",
        "image_sha256",
        "observation_payload_digest",
        "gateway_response_digest",
        "media_type",
        "size_bytes",
    }
)


def _strict_nonnegative_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _strict_positive_int(value: object, *, label: str) -> int:
    result = _strict_nonnegative_int(value, label=label)
    if result == 0:
        raise ValueError(f"{label} must be positive")
    return result


def _strict_sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_model_log_manifest(
    *,
    manifest: AgentSessionManifest,
    records: tuple[InteractionRecord, ...],
    interactions_raw: bytes,
) -> None:
    if (
        hashlib.sha256(interactions_raw).hexdigest()
        != manifest.interactions_sha256
        or len(interactions_raw) != manifest.interactions_size_bytes
        or len(records) != manifest.log_record_count
        or records[-1].record_hash != manifest.log_chain_root
        or any(record.session_id != manifest.session_id for record in records)
        or records[0].wall_time_ns < manifest.started_wall_time_ns
        or records[-1].wall_time_ns > manifest.completed_wall_time_ns
    ):
        raise ValueError("model session manifest does not close over interaction bytes")

    request_count = 0
    response_count = 0
    tool_call_count = 0
    image_observation_count = 0
    usage_totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
    }
    request_indexes: list[int] = []
    response_indexes: list[int] = []
    turn_ids: set[str] = set()
    for record in records:
        if record.turn_id is not None:
            turn_ids.add(record.turn_id)
        payload = record.payload
        if record.record_type == "model.request":
            request_count += 1
            if set(payload) != _MODEL_REQUEST_KEYS:
                raise ValueError("model request log payload has an invalid field set")
            request_index = _strict_positive_int(
                payload["request_index"], label="model request_index"
            )
            request_indexes.append(request_index)
            if (
                payload["model"] != manifest.model
                or payload["reasoning_effort"] != manifest.reasoning_effort
                or payload["instructions_sha256"] != manifest.instruction_sha256
                or payload["initial_input_sha256"] != manifest.initial_input_sha256
                or payload["tools_digest"] != manifest.tools_digest
            ):
                raise ValueError("model request log differs from session manifest")
            _strict_sha256(
                payload["request_digest"], label="model request digest"
            )
            _strict_positive_int(
                payload["request_size_bytes"], label="model request size"
            )
            history_types = payload["history_item_types"]
            image_inputs = payload["image_inputs"]
            if not isinstance(history_types, list) or any(
                not isinstance(item, str) for item in history_types
            ):
                raise ValueError("model request history inventory is invalid")
            if not isinstance(image_inputs, list):
                raise ValueError("model request image inventory is invalid")
            source_call_ids: set[str] = set()
            for image_input in image_inputs:
                if (
                    not isinstance(image_input, dict)
                    or set(image_input) != _MODEL_IMAGE_INPUT_KEYS
                    or image_input.get("media_type") != "image/png"
                ):
                    raise ValueError("model request image provenance is invalid")
                for name in (
                    "source_call_id",
                    "operation_id",
                    "observation_id",
                    "frame_id",
                ):
                    if not isinstance(image_input[name], str) or not image_input[name]:
                        raise ValueError("model request image identity is invalid")
                for name in (
                    "image_sha256",
                    "observation_payload_digest",
                    "gateway_response_digest",
                ):
                    _strict_sha256(
                        image_input[name], label=f"model request image {name}"
                    )
                _strict_nonnegative_int(
                    image_input["content_index"], label="model image content_index"
                )
                image_size = _strict_positive_int(
                    image_input["size_bytes"], label="model image size"
                )
                if image_size > _MAX_CAMERA_PNG_BYTES:
                    raise ValueError("model request image exceeds camera frame size")
                source_call_id = image_input["source_call_id"]
                if source_call_id in source_call_ids:
                    raise ValueError("model request repeats an image source call")
                source_call_ids.add(source_call_id)
        elif record.record_type == "model.response":
            response_count += 1
            if set(payload) != _MODEL_RESPONSE_KEYS:
                raise ValueError("model response log payload has an invalid field set")
            response_indexes.append(
                _strict_positive_int(
                    payload["request_index"], label="model response request_index"
                )
            )
            if payload["model"] != manifest.model:
                raise ValueError("model response log differs from session manifest")
            _strict_sha256(
                payload["response_digest"], label="model response digest"
            )
            _strict_positive_int(
                payload["response_size_bytes"], label="model response size"
            )
            usage = payload["usage"]
            if not isinstance(usage, dict) or set(usage) != set(usage_totals):
                raise ValueError("model response usage is invalid")
            for name in usage_totals:
                usage_totals[name] += _strict_nonnegative_int(
                    usage[name], label=f"model response {name}"
                )
        elif record.record_type == "tool.call":
            tool_call_count += 1
        elif record.record_type == "tool.result":
            result = ToolExecutionResult.model_validate(payload.get("result"))
            if result.success and result.image_provenance() is not None:
                image_observation_count += 1

    if request_indexes != list(range(1, request_count + 1)) or response_indexes != (
        request_indexes[:response_count]
    ):
        raise ValueError("model request/response indexes are incomplete")
    if (
        manifest.status == "completed"
        and turn_ids != {manifest.terminal_turn_id}
    ) or (
        manifest.status == "failed"
        and manifest.terminal_turn_id is not None
        and turn_ids
        and turn_ids != {manifest.terminal_turn_id}
    ):
        raise ValueError("model session turn identity is inconsistent")
    if (
        request_count != manifest.usage.model_requests
        or tool_call_count != manifest.usage.tool_calls
        or image_observation_count != manifest.usage.image_observations
        or usage_totals["input_tokens"] != manifest.usage.input_tokens
        or usage_totals["cached_input_tokens"]
        != manifest.usage.cached_input_tokens
        or usage_totals["output_tokens"] != manifest.usage.output_tokens
    ):
        raise ValueError("model session usage does not match interaction records")
    terminal = records[-1]
    expected_terminal_kind = (
        "session.complete" if manifest.status == "completed" else "session.failed"
    )
    if terminal.record_type != expected_terminal_kind:
        raise ValueError("model session terminal record differs from manifest status")
    if manifest.status == "completed":
        thread_id = terminal.payload.get("app_server_thread_id")
        if (
            request_count != response_count
            or not isinstance(thread_id, str)
            or not thread_id
            or terminal.payload
            != {
                "app_server_thread_id": thread_id,
                "terminal_turn_id": manifest.terminal_turn_id,
                "model_requests": request_count,
                "tool_calls": tool_call_count,
                "image_observations": image_observation_count,
            }
        ):
            raise ValueError("model session completion summary is invalid")
    elif terminal.payload != {
        "failure_code": manifest.failure_code,
        "failure_detail": manifest.failure_detail,
    }:
        raise ValueError("model session failure summary is invalid")


def _load_model_session_evidence(
    *,
    manifest_raw: bytes,
    interactions_raw: bytes,
    manifest_source_artifact_id: str,
    interactions_source_artifact_id: str,
    run: ResolvedRunSpec,
    seal: SealManifest,
    reader: BundleReader,
    maximum_interactions_bytes: int,
) -> tuple[ModelSessionManifestEvidence, ModelInteractionsEvidence]:
    if (
        not manifest_raw.endswith(b"\n")
        or not manifest_raw[:-1]
        or b"\n" in manifest_raw[:-1]
    ):
        raise ValueError("model session manifest must be one canonical JSON record")
    manifest = _load_model(
        manifest_raw[:-1],
        AgentSessionManifest,
        evidence_kind="model_session_manifest",
    )
    try:
        records = interaction_log_from_jsonl_bytes(
            interactions_raw,
            expected_run_id=run.run_id,
            expected_attempt_id=seal.attempt_id,
            maximum_bytes=maximum_interactions_bytes,
        )
    except CodexDriverError as error:
        raise ValueError("model interaction evidence is invalid") from error
    if (
        manifest.run_id != run.run_id
        or manifest.attempt_id != seal.attempt_id
        or manifest.task_id != run.task.task_id
    ):
        raise ValueError("model session manifest identity differs from sealed run")
    agent = next(
        (item for item in run.agents if item.agent_id == manifest.agent_id),
        None,
    )
    driver = None if agent is None else agent.driver
    if driver is None or driver.driver_id != manifest.driver_id:
        raise ValueError("model manifest does not name the resolved Agent Driver")
    driver_config = reader.validate_schema_bound_file(driver.config)
    if not isinstance(driver_config, dict) or set(driver_config) != {
        "schema_version",
        "policy",
        "initial_input",
        "codex_cli_version",
        "codex_binary_sha256",
    }:
        raise ValueError("resolved Agent Driver config has an invalid field set")
    try:
        policy = SessionPolicy.model_validate(driver_config["policy"])
    except (TypeError, ValidationError, ValueError) as error:
        raise ValueError("resolved Agent Driver policy is invalid") from error
    initial_input = driver_config["initial_input"]
    if not isinstance(initial_input, str):
        raise ValueError("resolved Agent Driver initial input is invalid")
    instruction_bytes = reader.resolve_file(run.task.instruction).read_bytes()
    if (
        driver_config["schema_version"] != "aero-bench.agent-driver-config/v1"
        or manifest.policy != policy
        or manifest.model != policy.model
        or manifest.reasoning_effort != policy.reasoning_effort
        or manifest.initial_input_sha256
        != hashlib.sha256(initial_input.encode("utf-8")).hexdigest()
        or manifest.instruction_sha256
        != hashlib.sha256(instruction_bytes).hexdigest()
        or manifest.codex_cli_version != driver_config["codex_cli_version"]
        or manifest.codex_binary_sha256
        != driver_config["codex_binary_sha256"]
    ):
        raise ValueError("model manifest differs from resolved Driver inputs")
    validate_manifest_tools_digest(
        manifest=manifest,
        run=run,
        read_file=lambda reference: reader.resolve_file(reference).read_bytes(),
    )
    _validate_model_log_manifest(
        manifest=manifest,
        records=records,
        interactions_raw=interactions_raw,
    )
    return (
        ModelSessionManifestEvidence(
            source_artifact_id=manifest_source_artifact_id,
            manifest=manifest,
        ),
        ModelInteractionsEvidence(
            source_artifact_id=interactions_source_artifact_id,
            records=records,
        ),
    )


class InspectionSealedEvidenceLoader:
    """Read the exact canonical Inspection evidence inventory from a sealed root."""

    def __init__(
        self,
        package: InspectionTaskPackage,
        *,
        bundle_root: str | Path,
    ) -> None:
        if not isinstance(package, InspectionTaskPackage):
            raise TypeError(
                "Inspection sealed evidence requires an InspectionTaskPackage"
            )
        try:
            self._bundle_root = Path(bundle_root)
        except (TypeError, ValueError) as error:
            raise ValueError("Inspection bundle root is invalid") from error
        try:
            self._package = InspectionTaskPackage.model_validate(
                package.model_dump(mode="json")
            )
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise ValueError("Inspection package contract is invalid") from error

    @staticmethod
    def _revalidate_run(run: ResolvedRunSpec) -> ResolvedRunSpec:
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("Inspection sealed evidence requires a ResolvedRunSpec")
        try:
            return ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise ValueError("resolved Inspection run is invalid") from error

    def _load_pinned_package(self, run: ResolvedRunSpec) -> BundleReader:
        """Require verifier inputs to use the run's pinned package bytes."""

        try:
            reader = BundleReader(self._bundle_root)
            pinned_package = load_inspection_package(reader=reader, task=run.task)
        except (OSError, TypeError, ValidationError, ValueError) as error:
            raise ValueError(
                "resolved Inspection package cannot be loaded from its pinned bundle"
            ) from error
        if pinned_package != self._package:
            raise ValueError(
                "pinned Inspection package disagrees with the verifier configuration"
            )
        return reader

    @staticmethod
    def _validate_pinned_run_context(
        reader: BundleReader,
        run: ResolvedRunSpec,
    ) -> None:
        """Reresolve the complete run context from pinned bundle bytes."""

        try:
            _, _, feasibility = resolve_inspection_run_context(
                reader=reader,
                task=run.task,
                environment=run.environment,
                agents=run.agents,
                scenario=run.scenario,
            )
        except (OSError, TypeError, ValidationError, ValueError) as error:
            raise ValueError(
                "resolved Inspection context cannot be loaded from its pinned bundle: "
                f"{error}"
            ) from error
        if feasibility != run.feasibility:
            raise ValueError(
                "resolved Inspection feasibility disagrees with its pinned bundle"
            )

    def _validate_run_package_binding(
        self, run: ResolvedRunSpec
    ) -> tuple[str, bool]:
        task = run.task
        if (
            task.task_id != self._package.task_id
            or task.verifier.verifier_id != self._package.verifier_id
        ):
            raise ValueError("Inspection package belongs to another resolved task")

        task_requirements = {
            requirement.artifact_id: requirement
            for requirement in task.verifier.artifact_requirements
        }
        if len(task_requirements) != len(self._package.artifact_requirements):
            raise ValueError(
                "resolved task does not declare the Inspection artifact set"
            )
        for requirement in self._package.artifact_requirements:
            task_requirement = task_requirements.get(requirement.artifact_id)
            if task_requirement is None or task_requirement.model_dump(mode="json") != (
                requirement.model_dump(mode="json", exclude={"evidence_kind"})
            ):
                raise ValueError(
                    "Inspection package artifact disagrees with the resolved task"
                )

        truth_requirement = next(
            requirement
            for requirement in self._package.artifact_requirements
            if requirement.evidence_kind == "truth"
        )
        source_asset_id = truth_requirement.source_asset_id
        task_assets = {asset.asset_id: asset for asset in task.assets}
        package_assets = {asset.asset_id: asset for asset in self._package.assets}
        for asset in self._package.assets:
            if asset.asset_id == source_asset_id:
                continue
            task_asset = task_assets.get(asset.asset_id)
            if task_asset is None or task_asset.file != asset.file:
                raise ValueError(
                    "Inspection package asset disagrees with the resolved task"
                )

        package_truth_asset = package_assets.get(source_asset_id or "")
        task_truth_asset = task_assets.get(source_asset_id or "")
        if (
            package_truth_asset is None
            or task_truth_asset is None
            or task_truth_asset.file != package_truth_asset.file
        ):
            raise ValueError(
                "immutable truth source asset digest disagrees with the resolved task"
            )
        if (
            task_truth_asset.classification != "private"
            or len(task_truth_asset.audiences) != 1
            or task_truth_asset.audiences[0].role != "verifier"
            or task_truth_asset.audiences[0].workload_ids
            != (task.verifier.verifier_id,)
        ):
            raise ValueError(
                "immutable truth source asset is not verifier-private in the resolved task"
            )

        package_agent_ids = {
            actor.actor_id for actor in self._package.actors if actor.role == "agent"
        }
        if package_agent_ids != {agent.agent_id for agent in run.agents}:
            raise ValueError("Inspection package agents disagree with the resolved run")
        provider_ids = {provider.provider_id for provider in run.environment.providers}
        observation_provider_ids = {
            actor.actor_id
            for actor in self._package.actors
            if actor.role == "observation_provider"
        }
        if not observation_provider_ids <= provider_ids:
            raise ValueError(
                "Inspection observation providers disagree with the resolved run"
            )
        business_endpoint_id, task_local_business = (
            resolve_inspection_business_endpoint(
                package=self._package,
                task=run.task,
                environment=run.environment,
                agents=run.agents,
            )
        )
        expected_logical_endpoint_ids = (
            (business_endpoint_id,) if task_local_business else ()
        )
        if run.scenario.task.logical_endpoint_ids != expected_logical_endpoint_ids:
            raise ValueError(
                "Inspection Business ownership disagrees with the resolved run"
            )

        package_observations = {
            (
                observation.observation_id,
                observation.trigger.provider_id,
                observation.metadata_schema,
            )
            for observation in self._package.observations
        }
        network_provider_id = (
            None if run.scenario.network is None else run.scenario.network.provider_id
        )
        network_endpoint_ids = (
            set()
            if run.scenario.network is None
            else {
                binding.endpoint_id
                for binding in run.scenario.network.node_bindings
            }
        )
        resolved_observations: list[tuple[object, ...]] = []
        mailbox_endpoint_ids: set[str] = set()
        for agent in run.agents:
            for grant in agent.observations:
                is_mailbox = (
                    network_provider_id is not None
                    and grant.provider_id == network_provider_id
                    and grant.observation_id.startswith(MAILBOX_OBSERVATION_PREFIX)
                )
                if is_mailbox:
                    endpoint_id = grant.observation_id[
                        len(MAILBOX_OBSERVATION_PREFIX) :
                    ]
                    if (
                        endpoint_id not in network_endpoint_ids
                        or endpoint_id in mailbox_endpoint_ids
                    ):
                        raise ValueError(
                            "Inspection mailbox observations disagree with network authority"
                        )
                    mailbox_endpoint_ids.add(endpoint_id)
                    continue
                if grant.observation_id.startswith(MAILBOX_OBSERVATION_PREFIX):
                    raise ValueError(
                        "Inspection mailbox observation names another Provider"
                    )
                resolved_observations.append(
                    (grant.observation_id, grant.provider_id, grant.schema_file)
                )
        if len(package_observations) != len(
            resolved_observations
        ) or package_observations != set(resolved_observations):
            raise ValueError("Inspection observations disagree with the resolved run")

        package_goals = {
            (
                goal.goal_id,
                goal.metric_id,
                goal.operator,
                goal.threshold,
                goal.evidence,
                goal.parameters,
            )
            for goal in self._package.goals
        }
        task_goals = {
            (
                goal.goal_id,
                goal.metric_id,
                goal.operator,
                goal.threshold,
                goal.evidence,
                goal.parameters,
            )
            for goal in task.goals
        }
        if package_goals != task_goals:
            raise ValueError("Inspection package goals disagree with the resolved task")
        return business_endpoint_id, task_local_business

    def load(
        self,
        *,
        run: ResolvedRunSpec,
        seal: SealManifest,
        sealed_root: str | Path,
    ) -> InspectionEvidenceBundle:
        run = self._revalidate_run(run)
        if not isinstance(seal, SealManifest):
            raise TypeError("Inspection sealed evidence requires a SealManifest")
        try:
            seal = SealManifest.model_validate(seal.model_dump(mode="json"))
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise ValueError("sealed evidence manifest is invalid") from error
        reader = self._load_pinned_package(run)
        business_endpoint_id, task_local_business = self._validate_run_package_binding(
            run
        )
        self._validate_pinned_run_context(reader, run)
        requirements = self._validated_requirements(run, seal)
        run_requirements = {
            requirement.artifact_id: requirement
            for requirement in run.artifact_requirements
        }
        expected_paths = {
            requirement.relative_path for requirement in run_requirements.values()
        }
        if len(expected_paths) != len(run_requirements):
            raise ValueError("ResolvedRun evidence paths must be unique")
        if any(
            _SEAL_MANIFEST_NAME in _normalize_relative_path(path).parts
            for path in expected_paths
        ):
            raise ValueError(
                "Inspection evidence paths cannot use the seal manifest name"
            )
        expected_files = {*expected_paths, _SEAL_MANIFEST_NAME}
        root_descriptor = _open_root(sealed_root)
        try:
            actual_files, actual_directories = _root_inventory(root_descriptor)
            if (
                actual_files != expected_files
                or actual_directories != _expected_directories(expected_files)
            ):
                raise ValueError(
                    "sealed evidence root must contain exactly the declared evidence "
                    "records and seal manifest control file"
                )
            expected_manifest = (
                canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
            )
            control_manifest = _read_regular_file(
                root_descriptor,
                PurePosixPath(_SEAL_MANIFEST_NAME),
                expected_size=len(expected_manifest),
                max_size_bytes=len(expected_manifest),
                description="sealed evidence control manifest",
            )
            if control_manifest != expected_manifest:
                raise ValueError("sealed evidence control manifest does not match seal")

            sealed_by_id = {
                artifact.artifact_id: artifact for artifact in seal.artifacts
            }
            raw_by_kind: dict[str, bytes] = {}
            evidence_kind_by_artifact_id = {
                requirement.artifact_id: evidence_kind
                for evidence_kind, requirement in requirements.items()
            }
            for artifact_id, requirement in run_requirements.items():
                evidence_kind = evidence_kind_by_artifact_id.get(artifact_id)
                artifact = sealed_by_id[artifact_id]
                if evidence_kind in {"event_log", "scene_state_history"}:
                    continue
                if evidence_kind is not None:
                    raw_by_kind[evidence_kind] = _read_artifact_bytes(
                        root_descriptor, artifact, max_size_bytes=requirement.max_size_bytes,
                    )
                else:
                    # Non-Inspection artifacts still require the complete sealed
                    # size/digest read, but need no retained input buffer.
                    for _ in _iter_artifact_content(root_descriptor, artifact, max_size_bytes=requirement.max_size_bytes):
                        pass
            event_requirement = requirements["event_log"]
            with closing(_iter_artifact_content(
                root_descriptor, sealed_by_id[event_requirement.artifact_id],
                max_size_bytes=event_requirement.max_size_bytes, linewise=True,
            )) as lines:
                event_ledger = self._load_event_ledger(
                    lines, source_artifact_id=event_requirement.artifact_id, run=run,
                )
            scene_requirement = requirements["scene_state_history"]
            with closing(_iter_artifact_content(
                root_descriptor, sealed_by_id[scene_requirement.artifact_id],
                max_size_bytes=scene_requirement.max_size_bytes, linewise=True,
            )) as lines:
                scene_states = self._validate_scene_state_history(lines, run=run, event_ledger=event_ledger)
            final_files, final_directories = _root_inventory(root_descriptor)
            if final_files != actual_files or final_directories != actual_directories:
                raise ValueError("sealed evidence root changed while being read")
        finally:
            os.close(root_descriptor)

        business_state = _load_model(
            raw_by_kind["business_state"],
            BusinessStateEvidence,
            evidence_kind="business_state",
        )
        theoretical_bounds = _load_model(
            raw_by_kind["theoretical_bounds"],
            TheoreticalBoundsEvidence,
            evidence_kind="theoretical_bounds",
        )
        trajectory = _load_models(
            raw_by_kind["trajectory"],
            TrajectoryEvidence,
            evidence_kind="trajectory",
        )
        deliveries = (
            _load_models(
                raw_by_kind[_OPTIONAL_DELIVERY_KIND],
                DeliveryEvidence,
                evidence_kind=_OPTIONAL_DELIVERY_KIND,
            )
            if _OPTIONAL_DELIVERY_KIND in raw_by_kind
            else ()
        )
        observations = _load_models(
            raw_by_kind["observation"],
            ObservationMetadata,
            evidence_kind="observation",
        )
        sensor_frames = _load_models(
            raw_by_kind["sensor_frame"],
            PublicSensorFrameRecord,
            evidence_kind="sensor_frame",
        )
        camera_frames = _load_camera_frame_archive(
            raw_by_kind[_CAMERA_FRAME_DATA_KIND],
            source_artifact_id=requirements[_CAMERA_FRAME_DATA_KIND].artifact_id,
        )
        if _OPTIONAL_MODEL_EVIDENCE_KINDS <= set(raw_by_kind):
            model_session, model_interactions = _load_model_session_evidence(
                manifest_raw=raw_by_kind["model_session_manifest"],
                interactions_raw=raw_by_kind["model_interactions"],
                manifest_source_artifact_id=requirements[
                    "model_session_manifest"
                ].artifact_id,
                interactions_source_artifact_id=requirements[
                    "model_interactions"
                ].artifact_id,
                run=run,
                seal=seal,
                reader=reader,
                maximum_interactions_bytes=requirements[
                    "model_interactions"
                ].max_size_bytes,
            )
            validate_model_gateway_evidence(
                manifest=model_session.manifest,
                interactions=model_interactions.records,
                ledger_records=event_ledger.records,
                run=run,
                required_work_order_ids=tuple(
                    item.work_order_id for item in self._package.work_orders
                ),
                required_vehicle_ids=tuple(
                    launch.primary_uav_entity_id
                    for launch in run.scenario.launch_sites
                    if launch.selected
                    and launch.launch_site_id
                    == run.scenario.selected_launch_site_id
                ),
            )
        else:
            model_session = None
            model_interactions = None
        truth = _load_models(
            raw_by_kind["truth"],
            DefectTruth,
            evidence_kind="truth",
        )
        detections = _load_models(
            raw_by_kind["detection"],
            DefectDetection,
            evidence_kind="detection",
        )
        report = _load_models(
            raw_by_kind["report"],
            InspectionReportPayload,
            evidence_kind="report",
        )

        self._validate_unique_evidence(
            trajectory=trajectory,
            deliveries=deliveries,
            observations=observations,
            sensor_frames=sensor_frames,
            camera_frames=camera_frames,
            model_session=model_session,
            model_interactions=model_interactions,
            truth=truth,
            detections=detections,
            report=report,
        )
        self._validate_source_ids(
            business_state=business_state,
            theoretical_bounds=theoretical_bounds,
            trajectory=trajectory,
            deliveries=deliveries,
            observations=observations,
            sensor_frames=sensor_frames,
            camera_frames=camera_frames,
            model_session=model_session,
            model_interactions=model_interactions,
            truth=truth,
            detections=detections,
            report=report,
            requirements=requirements,
        )
        self._validate_rgb_frames(
            observations=observations,
            sensor_frames=sensor_frames,
            camera_frames=camera_frames,
        )
        if event_ledger.records:
            self._validate_sensor_frames(
                run=run,
                event_ledger=event_ledger,
                observations=observations,
                sensor_frames=sensor_frames,
                sensor_frame_requirement=requirements["sensor_frame"],
                camera_frame_requirement=requirements[
                    _CAMERA_FRAME_DATA_KIND
                ],
            )
            self._validate_business_state(
                business_state=business_state,
                event_ledger=event_ledger,
                run_id=run.run_id,
                event_chain_root=seal.event_chain_root,
                allow_task_local_business_principal=task_local_business,
            )
            business_ledger_bindings = self._derive_business_ledger_bindings(
                history=business_state.history,
                ledger_records=event_ledger.records,
                business_source_id=business_endpoint_id,
            )
        else:
            if not business_state.history:
                raise ValueError("business history is empty")
            replayed_state = replay_business_history(
                self._package,
                run_id=run.run_id,
                history=business_state.history,
                allow_task_local_business_principal=task_local_business,
            )
            if replayed_state != business_state.state:
                raise ValueError("business state does not match replayed history")
            business_ledger_bindings = ()
        bindings = tuple(
            EvidenceBinding(
                artifact_id=requirement.artifact_id,
                artifact_type=requirement.artifact_type,
                producer_id=requirement.producer_id,
                visibility=requirement.visibility,
                evidence_kind=evidence_kind,
            )
            for evidence_kind, requirement in requirements.items()
        )
        return InspectionEvidenceBundle(
            run_id=run.run_id,
            seal=seal,
            bindings=bindings,
            event_ledger=event_ledger,
            scene_states=scene_states,
            business_state=business_state,
            business_ledger_bindings=business_ledger_bindings,
            theoretical_bounds=theoretical_bounds,
            trajectory=trajectory,
            deliveries=deliveries,
            observations=observations,
            sensor_frames=sensor_frames,
            camera_frames=camera_frames,
            model_session=model_session,
            model_interactions=model_interactions,
            truth=truth,
            detections=detections,
            report=report,
        )

    def _validated_requirements(
        self,
        run: ResolvedRunSpec,
        seal: SealManifest,
    ) -> dict[str, InspectionArtifactRequirement]:
        package_requirements = {
            requirement.evidence_kind: requirement
            for requirement in self._package.artifact_requirements
        }
        expected_kinds = set(_CORE_EVIDENCE_KINDS)
        if self._package.network_delivery_required:
            expected_kinds.add(_OPTIONAL_DELIVERY_KIND)
        expected_kinds.update(
            set(package_requirements) & _OPTIONAL_MODEL_EVIDENCE_KINDS
        )
        if set(package_requirements) != expected_kinds:
            raise ValueError(
                "Inspection package evidence kinds disagree with its network mission"
            )
        run_requirements = {
            requirement.artifact_id: requirement
            for requirement in run.artifact_requirements
        }
        if len(run.artifact_requirements) != len(run_requirements):
            raise ValueError("ResolvedRun repeats an artifact requirement")
        for requirement in package_requirements.values():
            run_requirement = run_requirements.get(requirement.artifact_id)
            if run_requirement is None or run_requirement.model_dump(mode="json") != (
                requirement.model_dump(mode="json", exclude={"evidence_kind"})
            ):
                raise ValueError(
                    "ResolvedRun artifact contract disagrees with Inspection package"
                )

        if seal.run_id != run.run_id:
            raise ValueError("sealed evidence belongs to another ResolvedRun")
        if seal.execution_scope != run.execution_scope:
            raise ValueError(
                "sealed evidence execution scope disagrees with ResolvedRun"
            )
        sealed_by_id = {artifact.artifact_id: artifact for artifact in seal.artifacts}
        if len(sealed_by_id) != len(seal.artifacts):
            raise ValueError("seal repeats an artifact record")
        if set(sealed_by_id) != set(run_requirements):
            raise ValueError("seal must declare exactly the ResolvedRun artifacts")
        for requirement in run_requirements.values():
            artifact = sealed_by_id.get(requirement.artifact_id)
            if artifact is None:
                raise ValueError(
                    f"seal is missing declared ResolvedRun artifact: {requirement.artifact_id}"
                )
            if (
                artifact.artifact_type != requirement.artifact_type
                or artifact.producer_id != requirement.producer_id
                or artifact.visibility != requirement.visibility
                or artifact.relative_path != requirement.relative_path
                or artifact.size_bytes > requirement.max_size_bytes
            ):
                raise ValueError(
                    f"seal artifact disagrees with ResolvedRun contract: {artifact.artifact_id}"
                )
        truth_requirement = package_requirements["truth"]
        truth_asset = next(
            (
                asset
                for asset in run.task.assets
                if asset.asset_id == truth_requirement.source_asset_id
            ),
            None,
        )
        truth_artifact = sealed_by_id[truth_requirement.artifact_id]
        if truth_asset is None or truth_artifact.sha256 != truth_asset.file.sha256:
            raise ValueError(
                "immutable truth artifact digest disagrees with its source asset"
            )
        return package_requirements

    @staticmethod
    def _load_event_ledger(
        lines: Iterable[bytes],
        *,
        source_artifact_id: str,
        run: ResolvedRunSpec,
    ) -> EventLedgerEvidence:
        parsed: list[LedgerRecord] = []
        for line in lines:
            if not line.endswith(b"\n"):
                raise ValueError("event_log evidence must be non-empty canonical JSONL")
            if line == b"\n":
                raise ValueError("event_log evidence contains an empty record")
            parsed.append(_load_model(line[:-1], LedgerRecord, evidence_kind="event_log"))
        if not parsed:
            raise ValueError("event_log evidence must be non-empty canonical JSONL")
        try:
            ledger = EventLedger.from_records(tuple(parsed))
            validate_authoritative_runtime_ledger(run, ledger)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError(
                "event_log evidence is not an authoritative runtime ledger"
            ) from error
        return EventLedgerEvidence(
            source_artifact_id=source_artifact_id,
            records=ledger.records,
        )

    @staticmethod
    def _validate_scene_state_history(
        lines: Iterable[bytes],
        *,
        run: ResolvedRunSpec,
        event_ledger: EventLedgerEvidence,
    ) -> tuple[SceneState, ...]:
        records = event_ledger.records
        if not records or records[-1].event.event_type not in {
            "run.completed",
            "run.aborted",
        }:
            raise ValueError(
                "scene_state_history requires a completed or controlled-aborted run"
            )
        committed_records = tuple(
            record
            for record in records
            if record.event.event_type == "scene.state.committed"
        )
        aborted_before_first_motion = (
            records[-1].event.event_type == "run.aborted" and not committed_records
        )
        try:
            parsed: list[SceneState] = []
            for line in lines:
                if not line.endswith(b"\n") or line == b"\n":
                    raise ValueError("scene state history must use non-empty newline-terminated records")
                document = json.loads(line.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
                                      parse_constant=_reject_json_constant)
                if not isinstance(document, dict):
                    raise ValueError("scene state history record must be a JSON object")
                parsed.append(SceneState.model_validate(document))
            states = validate_scene_state_history(
                tuple(parsed),
                aborted_before_first_motion=aborted_before_first_motion,
            )
        except (TypeError, UnicodeError, ValueError) as error:
            raise ValueError(
                "scene_state_history evidence is not a canonical SceneState history"
            ) from error

        if any(
            state.run_id != run.run_id
            or state.scenario_digest != run.scenario.scenario_digest
            for state in states
        ):
            raise ValueError(
                "scene_state_history evidence belongs to another run or scenario"
            )
        if len(committed_records) != len(states):
            raise ValueError(
                "scene_state_history does not cover the committed SceneState inventory"
            )
        for state, record in zip(states, committed_records, strict=True):
            expected_payload = {
                "barrier_digest": state.stage_barrier.barrier_digest,
                "contribution_digests": canonical_json_bytes(
                    list(state.contribution_digests)
                ).decode("utf-8"),
                "provider_ids": canonical_json_bytes(
                    list(state.stage_barrier.provider_ids)
                ).decode("utf-8"),
                "receipt_digests": canonical_json_bytes(
                    list(state.stage_barrier.receipt_digests)
                ).decode("utf-8"),
                "run_id": state.run_id,
                "scenario_digest": state.scenario_digest,
                "scene_state_digest": state.scene_state_digest,
                "stage": "motion",
                "target_sim_time_ns": state.at.sim_time_ns,
                "target_tick": state.at.tick,
            }
            actual_payload = {
                item.name: item.value for item in record.event.payload
            }
            if (
                record.event.source != "harness"
                or record.event.time != state.at
                or actual_payload != expected_payload
            ):
                raise ValueError(
                    "scene_state_history disagrees with scene.state.committed"
                )

        sample_records = tuple(
            record
            for record in records
            if record.event.event_type == "scene.entity-state"
        )
        expected_samples = tuple(
            (state, commit, sample)
            for state, commit in zip(states, committed_records, strict=True)
            for sample in state.samples
        )
        if len(sample_records) != len(expected_samples):
            raise ValueError(
                "event ledger does not cover the complete StateSample history"
            )
        sample_event_by_digest: dict[str, LedgerRecord] = {}
        for record, (state, commit, sample) in zip(
            sample_records, expected_samples, strict=True
        ):
            expected_payload = {
                "entity_id": sample.entity_id,
                "provider_id": sample.provider_id,
                "run_id": sample.run_id,
                "sample_digest": sample.sample_digest,
                "sample_kind": sample.sample_kind,
                "scenario_digest": sample.scenario_digest,
                "scene_state_digest": state.scene_state_digest,
                "state_sample_json": canonical_json_bytes(
                    sample.model_dump(mode="json")
                ).decode("utf-8"),
                "target_sim_time_ns": sample.at.sim_time_ns,
                "target_tick": sample.at.tick,
            }
            event = record.event
            interaction = event.interaction
            if (
                event.source != "harness"
                or event.time != state.at
                or event.correlation_id != f"tick.{state.at.tick}"
                or event.parent_event_id != commit.event.event_id
                or event.frame_id != "ENU"
                or event.provider_id != sample.provider_id
                or event.entity_id != sample.entity_id
                or interaction is None
                or interaction.interaction_type != "scene.entity_state.v1"
                or {item.name: item.value for item in event.payload}
                != expected_payload
            ):
                raise ValueError(
                    "StateSample history disagrees with scene.entity-state RunEvents"
                )
            sample_event_by_digest[sample.sample_digest] = record
        if len(sample_event_by_digest) != len(expected_samples):
            raise ValueError("StateSample RunEvent history repeats a sample digest")

        px4_provider_ids = {
            provider.provider_id
            for provider in run.environment.providers
            if provider.adapter == "px4.gazebo"
        }
        expected_px4_samples = tuple(
            (state, sample)
            for state in states
            for sample in state.samples
            if sample.provider_id in px4_provider_ids
        )
        telemetry_records = tuple(
            record
            for record in records
            if record.event.event_type == "px4.telemetry"
        )
        if len(telemetry_records) != len(expected_px4_samples):
            raise ValueError("PX4 telemetry RunEvents do not cover PX4 StateSamples")
        for record, (state, sample) in zip(
            telemetry_records, expected_px4_samples, strict=True
        ):
            source_record = sample_event_by_digest[sample.sample_digest]
            event = record.event
            if (
                event.source != sample.provider_id
                or event.source_kind != "provider"
                or event.time != sample.at
                or event.parent_event_id != source_record.event.event_id
                or event.provider_id != sample.provider_id
                or event.entity_id != sample.entity_id
                or event.interaction is None
                or event.interaction.interaction_type != "px4.telemetry.v1"
                or {item.name: item.value for item in event.payload}
                != {item.name: item.value for item in source_record.event.payload}
                or event.payload_schema_id != "px4.telemetry.v1"
                or event.correlation_id != f"tick.{state.at.tick}"
            ):
                raise ValueError(
                    "PX4 telemetry interaction disagrees with its StateSample"
                )
        return states

    @staticmethod
    def _validate_unique_evidence(
        *,
        trajectory: tuple[TrajectoryEvidence, ...],
        deliveries: tuple[DeliveryEvidence, ...],
        observations: tuple[ObservationMetadata, ...],
        sensor_frames: tuple[PublicSensorFrameRecord, ...],
        camera_frames: tuple[CameraFrameDataRecord, ...],
        model_session: ModelSessionManifestEvidence | None,
        model_interactions: ModelInteractionsEvidence | None,
        truth: tuple[DefectTruth, ...],
        detections: tuple[DefectDetection, ...],
        report: tuple[InspectionReportPayload, ...],
    ) -> None:
        trajectory_keys = [
            (record.work_order_id, record.time.tick, record.time.sim_time_ns)
            for record in trajectory
        ]
        if len(trajectory_keys) != len(set(trajectory_keys)):
            raise ValueError("trajectory evidence repeats a work-order time")
        delivery_ids = [record.message_id for record in deliveries]
        if len(delivery_ids) != len(set(delivery_ids)):
            raise ValueError("delivery evidence repeats a message id")
        observation_keys = [
            (
                record.work_order_id,
                record.observation_id,
                record.time.tick,
                record.time.sim_time_ns,
            )
            for record in observations
        ]
        if len(observation_keys) != len(set(observation_keys)):
            raise ValueError("observation evidence repeats a work-order barrier capture")
        frame_ids = [record.frame_id for record in sensor_frames]
        frame_selectors = [record.selector for record in sensor_frames]
        if (
            len(frame_ids) != len(set(frame_ids))
            or len(frame_selectors) != len(set(frame_selectors))
        ):
            raise ValueError("public sensor-frame evidence repeats identity")
        camera_frame_ids = [record.frame_id for record in camera_frames]
        if len(camera_frame_ids) != len(set(camera_frame_ids)):
            raise ValueError("camera frame-data evidence repeats identity")
        truth_ids = [record.defect_id for record in truth]
        if len(truth_ids) != len(set(truth_ids)):
            raise ValueError("truth evidence repeats a defect id")
        detection_keys = [
            (record.work_order_id, record.defect_id) for record in detections
        ]
        if len(detection_keys) != len(set(detection_keys)):
            raise ValueError("detection evidence repeats a defect")
        report_work_orders = [record.work_order_id for record in report]
        if len(report_work_orders) != len(set(report_work_orders)):
            raise ValueError("report evidence repeats a work order")

    @staticmethod
    def _validate_source_ids(
        *,
        business_state: BusinessStateEvidence,
        theoretical_bounds: TheoreticalBoundsEvidence,
        trajectory: tuple[TrajectoryEvidence, ...],
        deliveries: tuple[DeliveryEvidence, ...],
        observations: tuple[ObservationMetadata, ...],
        sensor_frames: tuple[PublicSensorFrameRecord, ...],
        camera_frames: tuple[CameraFrameDataRecord, ...],
        model_session: ModelSessionManifestEvidence | None,
        model_interactions: ModelInteractionsEvidence | None,
        truth: tuple[DefectTruth, ...],
        detections: tuple[DefectDetection, ...],
        report: tuple[InspectionReportPayload, ...],
        requirements: dict[str, InspectionArtifactRequirement],
    ) -> None:
        records_by_kind = {
            "business_state": (business_state,),
            "theoretical_bounds": (theoretical_bounds,),
            "trajectory": trajectory,
            "delivery": deliveries,
            "observation": observations,
            "sensor_frame": sensor_frames,
            "camera_frame_data": camera_frames,
            "model_session_manifest": (
                () if model_session is None else (model_session,)
            ),
            "model_interactions": (
                () if model_interactions is None else (model_interactions,)
            ),
            "truth": truth,
            "detection": detections,
            "report": report,
        }
        for evidence_kind, records in records_by_kind.items():
            requirement = requirements.get(evidence_kind)
            if requirement is None:
                if records:
                    raise ValueError(
                        f"{evidence_kind} evidence exists without a declared artifact"
                    )
                continue
            expected_source_id = requirement.artifact_id
            if any(
                getattr(record, "source_artifact_id") != expected_source_id
                for record in records
            ):
                raise ValueError(
                    f"{evidence_kind} evidence names another source artifact"
                )

    @staticmethod
    def _validate_rgb_frames(
        *,
        observations: tuple[ObservationMetadata, ...],
        sensor_frames: tuple[PublicSensorFrameRecord, ...],
        camera_frames: tuple[CameraFrameDataRecord, ...],
    ) -> None:
        observations_by_frame = {
            observation.frame_id: observation for observation in observations
        }
        sensor_by_frame = {frame.frame_id: frame for frame in sensor_frames}
        archive_by_frame = {frame.frame_id: frame for frame in camera_frames}
        inventories = (
            set(observations_by_frame),
            set(sensor_by_frame),
            set(archive_by_frame),
        )
        if not inventories[0] or not (
            inventories[0] == inventories[1] == inventories[2]
        ):
            raise ValueError(
                "RGB observation, sensor-frame, and camera archive inventories differ"
            )
        comparable_fields = (
            "frame_id",
            "selector",
            "observation_id",
            "time",
            "camera_id",
            "vehicle_id",
            "engine_sim_time_ns",
            "logical_origin_engine_ns",
            "image_sha256",
            "size_bytes",
            "width",
            "height",
            "media_type",
            "source",
            "capture_pose_source",
            "camera_pose_sha256",
            "target_pose_sha256",
            "payload_digest",
        )
        for frame_id in sorted(observations_by_frame):
            observation = observations_by_frame[frame_id]
            sensor_frame = sensor_by_frame[frame_id]
            archive_frame = archive_by_frame[frame_id]
            if any(
                getattr(observation, field) != getattr(sensor_frame, field)
                for field in comparable_fields
            ):
                raise ValueError(
                    "public sensor frame differs from private RGB capture metadata"
                )
            if (
                archive_frame.image_sha256 != sensor_frame.image_sha256
                or archive_frame.size_bytes != sensor_frame.size_bytes
                or archive_frame.width != sensor_frame.width
                or archive_frame.height != sensor_frame.height
                or archive_frame.media_type != sensor_frame.media_type
            ):
                raise ValueError(
                    "camera frame archive bytes differ from captured frame metadata"
                )
            image_base64 = base64.b64encode(archive_frame.png_bytes).decode("ascii")
            expected_payload_digest = hashlib.sha256(
                canonical_json_bytes(
                    observation.public_payload(image_base64=image_base64)
                )
            ).hexdigest()
            if observation.payload_digest != expected_payload_digest:
                raise ValueError(
                    "RGB observation payload digest does not bind the archived image bytes"
                )

    @staticmethod
    def _validate_sensor_frames(
        *,
        run: ResolvedRunSpec,
        event_ledger: EventLedgerEvidence,
        observations: tuple[ObservationMetadata, ...],
        sensor_frames: tuple[PublicSensorFrameRecord, ...],
        sensor_frame_requirement: InspectionArtifactRequirement,
        camera_frame_requirement: InspectionArtifactRequirement,
    ) -> None:
        observations_by_capture = {
            (
                observation.observation_id,
                observation.time.tick,
                observation.time.sim_time_ns,
            ): observation
            for observation in observations
        }
        if len(observations_by_capture) != len(observations):
            raise ValueError(
                "public sensor frames require unique observation barrier captures"
            )
        records_by_event_id = {
            record.event.event_id: record for record in event_ledger.records
        }
        sensor_records = tuple(
            record
            for record in event_ledger.records
            if record.event.event_type == "public.sensor-frame"
        )
        if len(sensor_records) != len(sensor_frames):
            raise ValueError(
                "public sensor-frame artifact and RunEvent inventories differ"
            )
        frame_capture_keys = {
            (frame.observation_id, frame.time.tick, frame.time.sim_time_ns)
            for frame in sensor_frames
        }
        if frame_capture_keys != set(observations_by_capture):
            raise ValueError(
                "public sensor frames do not close over captured observation barriers"
            )
        sensor_events_by_frame: dict[str, LedgerRecord] = {}
        for record in sensor_records:
            event = record.event
            payload = {item.name: item.value for item in event.payload}
            frame_id = payload.get("frame_id")
            public_visibility = tuple(
                audience
                for audience in event.visibility
                if audience.scope == "public" and audience.audience_id is None
            )
            if (
                not isinstance(frame_id, str)
                or frame_id in sensor_events_by_frame
                or event.source_kind != "provider"
                or event.source != sensor_frame_requirement.producer_id
                or event.provider_id != sensor_frame_requirement.producer_id
                or event.payload_schema_id != "sensor.frame_ref.public.v2"
                or event.interaction is None
                or event.interaction.interaction_type != "sensor.frame_ref.v2"
                or len(event.visibility) != 1
                or len(public_visibility) != 1
                or set(payload) != {
                    "artifact_id",
                    "camera_id",
                    "camera_pose_sha256",
                    "capture_pose_source",
                    "engine_sim_time_ns",
                    "frame_id",
                    "height",
                    "image_sha256",
                    "logical_origin_engine_ns",
                    "media_type",
                    "observation_id",
                    "payload_digest",
                    "selector",
                    "size_bytes",
                    "source",
                    "target_pose_sha256",
                    "vehicle_id",
                    "width",
                }
            ):
                raise ValueError("public sensor-frame RunEvent is invalid")
            sensor_events_by_frame[frame_id] = record

        for frame in sensor_frames:
            observation = observations_by_capture.get(
                (frame.observation_id, frame.time.tick, frame.time.sim_time_ns)
            )
            sensor_record = sensor_events_by_frame.get(frame.frame_id)
            if observation is None or sensor_record is None:
                raise ValueError(
                    "public sensor frame lacks observation or RunEvent authority"
                )
            if (
                frame.source_artifact_id != sensor_frame_requirement.artifact_id
                or frame.run_id != run.run_id
                or frame.time != observation.time
                or frame.camera_id != observation.camera_id
                or frame.payload_digest != observation.payload_digest
            ):
                raise ValueError(
                    "public sensor frame differs from private captured evidence"
                )
            event = sensor_record.event
            payload = {item.name: item.value for item in event.payload}
            expected_payload = {
                "artifact_id": camera_frame_requirement.artifact_id,
                "camera_id": frame.camera_id,
                "camera_pose_sha256": frame.camera_pose_sha256,
                "capture_pose_source": frame.capture_pose_source,
                "engine_sim_time_ns": frame.engine_sim_time_ns,
                "frame_id": frame.frame_id,
                "height": frame.height,
                "image_sha256": frame.image_sha256,
                "logical_origin_engine_ns": frame.logical_origin_engine_ns,
                "media_type": frame.media_type,
                "observation_id": frame.observation_id,
                "payload_digest": frame.payload_digest,
                "selector": frame.selector,
                "size_bytes": frame.size_bytes,
                "source": frame.source,
                "target_pose_sha256": frame.target_pose_sha256,
                "vehicle_id": frame.vehicle_id,
                "width": frame.width,
            }
            if (
                payload != expected_payload
                or event.frame_id != frame.frame_id
                or event.observation_id != frame.observation_id
                or event.time.tick != frame.time.tick + 1
                or event.time.sim_time_ns
                != frame.time.sim_time_ns + run.environment.clock.step_ns
            ):
                raise ValueError(
                    "public sensor-frame RunEvent differs from its captured frame"
                )
            observation_events = tuple(
                record.event
                for record in event_ledger.records[: sensor_record.sequence]
                if record.event.observation_id == frame.observation_id
                and record.event.provider_id == sensor_frame_requirement.producer_id
                and record.event.interaction is not None
                and record.event.interaction.interaction_type
                == "agent.observation.v1"
                and record.event.time == frame.time
                and {
                    item.name: item.value for item in record.event.payload
                }.get("payload_digest")
                == frame.payload_digest
            )
            correlated_observation_ids = {
                item.event_id
                for item in observation_events
                if item.correlation_id == event.correlation_id
            }
            ancestor_ids: set[str] = set(event.causal_event_ids)
            visited_parent_ids: set[str] = set()
            parent_event_id = event.parent_event_id
            while (
                parent_event_id is not None
                and parent_event_id not in visited_parent_ids
            ):
                visited_parent_ids.add(parent_event_id)
                ancestor_ids.add(parent_event_id)
                parent_record = records_by_event_id.get(parent_event_id)
                if parent_record is None:
                    break
                parent_event_id = parent_record.event.parent_event_id
            if not correlated_observation_ids or not (
                correlated_observation_ids & ancestor_ids
            ):
                raise ValueError(
                    "public sensor-frame RunEvent lacks its observation causal chain"
                )

    def _validate_business_state(
        self,
        *,
        business_state: BusinessStateEvidence,
        event_ledger: EventLedgerEvidence,
        run_id: str,
        event_chain_root: str,
        allow_task_local_business_principal: bool = False,
    ) -> None:
        if not business_state.history:
            raise ValueError("business history is empty")
        if business_state.event_chain_root != event_chain_root:
            raise ValueError("business state event root disagrees with seal")
        if (
            business_state.business_history_root
            != business_state.history[-1].record_hash
        ):
            raise ValueError("business history root does not terminate its local chain")
        if (
            EventLedger.from_records(event_ledger.records).chain_root
            != event_chain_root
        ):
            raise ValueError("event_log chain root disagrees with seal")
        replayed_state = replay_business_history(
            self._package,
            run_id=run_id,
            history=business_state.history,
            allow_task_local_business_principal=allow_task_local_business_principal,
        )
        if replayed_state != business_state.state:
            raise ValueError("business state does not match replayed history")

    def _derive_business_ledger_bindings(
        self,
        *,
        history: tuple[BusinessHistoryRecord, ...],
        ledger_records: tuple[LedgerRecord, ...],
        business_source_id: str,
    ) -> tuple[BusinessLedgerBinding, ...]:
        business_sources = {business_source_id}
        bindings: list[BusinessLedgerBinding] = []
        mapped_ledger_sequences: set[int] = set()
        for history_record in history:
            command = history_record.command.command
            transition = history_record.transition
            expected_payload = {
                "schema_id": "aero-bench.business-history/v1",
                "business_history_sequence": history_record.sequence,
                "business_history_record_hash": history_record.record_hash,
                "command_id": command.command_id,
            }
            candidates = [
                record
                for record in ledger_records
                if record.event.source in business_sources
                and record.event.event_type == "business.transition"
                and record.event.time == transition.time
                and {item.name: item.value for item in record.event.payload}
                == expected_payload
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "business history is not uniquely recorded by the global ledger"
                )
            ledger_record = candidates[0]
            bindings.append(
                BusinessLedgerBinding(
                    business_history_sequence=history_record.sequence,
                    business_history_record_hash=history_record.record_hash,
                    command_id=command.command_id,
                    ledger_sequence=ledger_record.sequence,
                    ledger_event_hash=ledger_record.event_hash,
                )
            )
            mapped_ledger_sequences.add(ledger_record.sequence)
        authoritative_business_sequences = {
            record.sequence
            for record in ledger_records
            if record.event.source in business_sources
            and record.event.event_type == "business.transition"
        }
        if mapped_ledger_sequences != authoritative_business_sequences:
            raise ValueError(
                "global ledger contains an unmapped business transition event"
            )
        return tuple(bindings)


def load_sealed_inspection_evidence(
    *,
    package: InspectionTaskPackage,
    bundle_root: str | Path,
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root: str | Path,
) -> InspectionEvidenceBundle:
    """Load only the exact sealed Inspection evidence inventory for a run."""

    return InspectionSealedEvidenceLoader(
        package,
        bundle_root=bundle_root,
    ).load(
        run=run,
        seal=seal,
        sealed_root=sealed_root,
    )


def _load_verified_sealed_inspection_evidence(
    *,
    package: InspectionTaskPackage,
    bundle_root: str | Path,
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root: str | Path,
) -> _LoadedInspectionEvidence:
    """Issue verifier-only evidence after the strict sealed read succeeds."""

    return _LoadedInspectionEvidence(
        load_sealed_inspection_evidence(
            package=package,
            bundle_root=bundle_root,
            run=run,
            seal=seal,
            sealed_root=sealed_root,
        ),
        capability=_LOADED_EVIDENCE_CAPABILITY,
    )


__all__ = [
    "InspectionSealedEvidenceLoader",
    "load_sealed_inspection_evidence",
]
