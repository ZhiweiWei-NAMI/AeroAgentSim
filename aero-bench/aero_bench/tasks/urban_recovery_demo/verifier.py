"""Independent, fail-closed offline verifier for the urban recovery demo."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, Mapping, TypeVar

from pydantic import Field, model_validator

from aero_bench.artifacts.contracts import ArtifactRecord, EvidenceReference, SealManifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import ArtifactRequirement, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.gateway.contracts import DecisionSummaryRequest, ObservationEnvelope
from aero_bench.providers.ns3.provider import NetworkMailboxObservationPayload
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.providers.sumo.config import SumoConfig
from aero_bench.runtime.contracts import AgentTurnCompletion, AgentTurnDecision, SceneState, SimulationTime
from aero_bench.runtime.evidence import validate_authoritative_runtime_ledger
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.runtime.scene_history import scene_state_history_from_jsonl_bytes, scene_state_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import (
    DURATION_NS,
    FINAL_TICK,
    PHYSICS_STEP_NS,
    STEP_NS,
    AirspaceTransition,
    AppliedWrench,
    DemoTaskPackage,
    EngineWindow,
    PhysicsJournalRecord,
)
from aero_bench.tasks.urban_recovery_demo.integration import (
    UrbanRecoveryTaskPackageResolver,
    load_urban_recovery_package,
)
from aero_bench.tasks.urban_recovery_demo.goals import HORIZON_METRIC, MISSION_METRIC, validate_urban_goals
from aero_bench.tasks.urban_recovery_demo.participant import (
    RecoveryMessage,
    UrbanParticipantConfig,
)
from aero_bench.tasks.urban_recovery_demo.planner import RecoveryPlanner, RecoveryPlanningError
from aero_bench.verifier.contracts import (
    GoalResult,
    MetricResult,
    VerificationReport,
    validate_report_against_run,
)
from aero_bench.world.frame_math import EnuTransform, Vector3, wgs84_amsl_delta_enu

URBAN_VERIFIER_SCHEMA = "aero-bench.urban-recovery-verifier/v1"
URBAN_METRIC_ID = HORIZON_METRIC
_REQUIRED_CHANNELS = (
    "agent",
    "gazebo.airspace",
    "gazebo.contact",
    "gazebo.force",
    "gazebo.wind",
    "mavlink",
    "ns3",
    "px4",
    "sumo",
    "sumo.signals",
)
_ARTIFACT_AUTHORITIES = {
    "event.log": ("harness", "private"),
    "scene.state-history": ("harness", "public"),
    "trajectory": ("flight", "public"),
    "gazebo.physics-journal": ("flight", "private"),
    "gazebo.physics-plugin": ("flight", "private"),
    "network.delivery": ("network", "public"),
    "sumo.traffic.evidence": ("traffic", "private"),
}
_SEAL_MANIFEST_NAME = "seal-manifest.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENGINE_WINDOW_SCHEMA = "gazebo.engine-window.v1"
_WRENCH_SCHEMA = "gazebo.applied-wrench.v1"
_AIRSPACE_SCHEMA = "aero-bench.gazebo-airspace-transition/v1"
_AIRSPACE_EVENT_SCHEMA = "gazebo.airspace-transition.v1"
_NETWORK_DELIVERY_SCHEMA = "inspection.network-delivery.v1"
_PX4_STATE_SCHEMA = "px4.state.v1"
_SUMO_STATE_SCHEMA = "sumo.state.v3"
_PHYSICAL_PROOF_SCHEMA = "px4.command.physical.v3"


class UrbanRecoveryVerifierConfig(StrictModel):
    schema_version: Literal["aero-bench.urban-recovery-verifier/v1"]
    package_id: Literal["urban.uav-recovery-demo.v1"]
    execution_profile: Literal["formal", "engineering"] = "formal"
    duration_ns: int = Field(default=DURATION_NS, gt=0, le=DURATION_NS)
    step_ns: int = STEP_NS
    physics_step_ns: int = PHYSICS_STEP_NS
    final_tick: int = Field(default=FINAL_TICK, gt=0, le=FINAL_TICK)
    required_channels: tuple[str, ...] = Field(min_length=len(_REQUIRED_CHANNELS))

    @model_validator(mode="after")
    def exact_contract(self) -> "UrbanRecoveryVerifierConfig":
        if (
            self.step_ns != STEP_NS
            or self.physics_step_ns != PHYSICS_STEP_NS
            or (self.execution_profile == "formal" and (self.duration_ns != DURATION_NS or self.final_tick != FINAL_TICK))
            or self.required_channels != _REQUIRED_CHANNELS
            or self.step_ns * self.final_tick != self.duration_ns
            or self.step_ns % self.physics_step_ns
        ):
            raise ValueError("urban verifier configuration is not the exact fixed contract")
        return self


class UrbanRecoveryVerificationError(ValueError):
    """Evidence authority is unavailable, malformed, or unsupported."""

    def __init__(self, failure_class: str, detail: str):
        self.failure_class = failure_class
        super().__init__(detail)


class _MissionFailure(ValueError):
    """Complete authority proves that a mission requirement was not met."""

    def __init__(self, failure_class: str, detail: str):
        self.failure_class = failure_class
        super().__init__(detail)


T = TypeVar("T")


def _phase(failure_class: str, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (_MissionFailure, UrbanRecoveryVerificationError):
        raise
    except Exception as error:
        raise UrbanRecoveryVerificationError(failure_class, str(error) or type(error).__name__) from error


def _invalid_report(run: ResolvedRunSpec, failure_class: str) -> VerificationReport:
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope=run.execution_scope,
        status="invalid",
        goals=tuple(
            GoalResult(goal_id=goal.goal_id, passed=False, metrics=(), failure_class=failure_class)
            for goal in run.task.goals
        ),
        coverage_complete=False,
    )
    validate_report_against_run(report, run)
    return report


def _measured_report(
    run: ResolvedRunSpec,
    *,
    horizon: SimulationTime,
    mission_passed: bool,
    failure_class: str | None,
    evidence: tuple[EvidenceReference, ...],
    coverage_complete: bool,
) -> VerificationReport:
    validate_urban_goals(run.task)
    if not evidence or (mission_passed and (failure_class is not None or not coverage_complete)):
        raise ValueError("mission pass requires complete authority and no failure")
    if not mission_passed and failure_class is None:
        raise ValueError("measured mission failure requires a failure class")
    values = {
        HORIZON_METRIC: float(horizon == SimulationTime(tick=FINAL_TICK, sim_time_ns=DURATION_NS)),
        MISSION_METRIC: float(mission_passed),
    }
    goals = tuple(
        GoalResult(
            goal_id=goal.goal_id,
            passed=values[goal.metric_id] == goal.threshold,
            failure_class=(
                None if values[goal.metric_id] == goal.threshold else
                "urban.mission.exact_horizon_not_reached" if goal.metric_id == HORIZON_METRIC else failure_class
            ),
            metrics=(MetricResult(
                metric_id=goal.metric_id, value=values[goal.metric_id],
                unit="boolean", evidence=evidence,
            ),),
        )
        for goal in run.task.goals
    )
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope=run.execution_scope,
        status="passed" if all(goal.passed for goal in goals) else "failed",
        goals=goals,
        coverage_complete=coverage_complete,
    )
    validate_report_against_run(report, run)
    return report


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate JSON key: {name}")
        result[name] = value
    return result


def _nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _decode_json(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_duplicate_keys,
        parse_constant=_nonfinite_constant,
        parse_float=_finite_float,
    )


def _canonical_document(raw: bytes, *, trailing_newline: bool) -> object:
    if trailing_newline:
        if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
            raise ValueError("canonical JSON must have exactly one trailing newline")
        encoded = raw[:-1]
    else:
        if raw.endswith(b"\n"):
            raise ValueError("canonical JSON must not have a trailing newline")
        encoded = raw
    value = _decode_json(encoded)
    expected = canonical_json_bytes(value) + (b"\n" if trailing_newline else b"")
    if expected != raw:
        raise ValueError("JSON bytes are not canonical")
    return value


def _canonical_jsonl(raw: bytes) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    if not raw or not raw.endswith(b"\n"):
        raise ValueError("evidence must be non-empty newline-terminated canonical JSONL")
    lines = raw[:-1].split(b"\n")
    if not lines or any(not line for line in lines):
        raise ValueError("evidence JSONL contains an empty record")
    records: list[dict[str, Any]] = []
    prefixes: list[str] = []
    digest = hashlib.sha256()
    for line in lines:
        value = _decode_json(line)
        if not isinstance(value, dict) or canonical_json_bytes(value) != line:
            raise ValueError("evidence JSONL record is not a canonical object")
        digest.update(line + b"\n")
        prefixes.append(digest.hexdigest())
        records.append(value)
    return tuple(records), tuple(prefixes)


def _canonical_json_text(value: object, *, label: str) -> object:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not JSON text")
    raw = value.encode("utf-8")
    document = _decode_json(raw)
    if canonical_json_bytes(document) != raw:
        raise ValueError(f"{label} is not canonical JSON")
    return document


def _object(value: object, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields are not exact")
    return value


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None or value == "0" * 64:
        raise ValueError(f"{label} is not a real SHA-256 digest")
    return value


def _payload(record: LedgerRecord) -> dict[str, object]:
    values = {item.name: item.value for item in record.event.payload}
    if len(values) != len(record.event.payload):
        raise ValueError("ledger event payload names are duplicated")
    return values


def _records(
    ledger: EventLedger,
    *,
    event_type: str | None = None,
    schema: str | None = None,
    source: str | None = None,
) -> tuple[LedgerRecord, ...]:
    return tuple(
        record
        for record in ledger.records
        if (event_type is None or record.event.event_type == event_type)
        and (schema is None or record.event.payload_schema_id == schema)
        and (source is None or record.event.source == source)
    )


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or value.strip() != value
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or str(path) != value
        or _SEAL_MANIFEST_NAME in path.parts
    ):
        raise ValueError("sealed artifact path is not normalized and relative")
    return path


_OPEN_COMMON = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_OPEN_FILE = _OPEN_COMMON | getattr(os, "O_NONBLOCK", 0)
_OPEN_DIRECTORY = _OPEN_COMMON | os.O_DIRECTORY


def _open_root(path: Path) -> int:
    if not isinstance(path, Path) or not path.is_absolute() or os.fspath(path) != os.path.normpath(os.fspath(path)):
        raise ValueError("sealed root must be an absolute normalized Path")
    root = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open(root.anchor, _OPEN_DIRECTORY)
    try:
        for component in root.parts[1:]:
            next_descriptor = os.open(component, _OPEN_DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("sealed root is not a directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _inventory(root_descriptor: int) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()

    def visit(descriptor: int, parent: PurePosixPath) -> None:
        for name in os.listdir(descriptor):
            child = parent / name
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError(f"sealed root contains symlink {child.as_posix()}")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(child.as_posix())
                child_descriptor = os.open(name, _OPEN_DIRECTORY, dir_fd=descriptor)
                try:
                    visit(child_descriptor, child)
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(child.as_posix())
            else:
                raise ValueError(f"sealed root contains special file {child.as_posix()}")

    visit(root_descriptor, PurePosixPath("."))
    return files, directories


def _expected_directories(paths: set[str]) -> set[str]:
    result: set[str] = set()
    for value in paths:
        parent = PurePosixPath(value).parent
        while parent != PurePosixPath("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _read_at(root_descriptor: int, path: PurePosixPath, *, expected_size: int) -> bytes:
    descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(path.parts):
            flags = _OPEN_FILE if index == len(path.parts) - 1 else _OPEN_DIRECTORY
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise ValueError("sealed file type or size changed")
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError("sealed file changed while reading")
    finally:
        os.close(descriptor)
    content = b"".join(chunks)
    if len(content) != expected_size:
        raise ValueError("sealed file length changed while reading")
    return content


_ENGINEERING_TERMINAL_READ_LIMIT = 2 * 1024 * 1024


def _digest_at(
    root_descriptor: int,
    path: PurePosixPath,
    *,
    expected_size: int,
    capture_tail: bool = False,
) -> tuple[str, bytes | None]:
    """Hash one sealed file without materializing it, optionally retaining its tail."""

    descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(path.parts):
            flags = _OPEN_FILE if index == len(path.parts) - 1 else _OPEN_DIRECTORY
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise ValueError("sealed file type or size changed")
        digest = hashlib.sha256()
        total = 0
        while total <= expected_size:
            chunk = os.read(descriptor, min(1024 * 1024, expected_size + 1 - total))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        if total != expected_size:
            raise ValueError("sealed file length changed while reading")
        tail: bytes | None = None
        if capture_tail:
            tail_size = min(expected_size, _ENGINEERING_TERMINAL_READ_LIMIT)
            os.lseek(descriptor, expected_size - tail_size, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = tail_size
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    raise ValueError("sealed file changed while reading its terminal record")
                chunks.append(chunk)
                remaining -= len(chunk)
            tail = b"".join(chunks)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError("sealed file changed while reading")
        return digest.hexdigest(), tail
    finally:
        os.close(descriptor)


def _validated_engineering_runtime_boundary(
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root: Path,
    config: UrbanRecoveryVerifierConfig,
) -> LedgerRecord:
    """Validate the sealed engineering boundary without formal evidence parsing."""

    canonical_seal = SealManifest.model_validate(seal.model_dump(mode="json"))
    if (
        canonical_seal != seal
        or seal.run_id != run.run_id
        or seal.execution_scope != run.execution_scope
        or seal.execution_scope != "executor_validation"
    ):
        raise ValueError("engineering seal identity differs from ResolvedRun")
    requirements = tuple(run.artifact_requirements)
    records_by_id = {record.artifact_id: record for record in seal.artifacts}
    requirements_by_id = {item.artifact_id: item for item in requirements}
    if (
        len(records_by_id) != len(seal.artifacts)
        or len(requirements_by_id) != len(requirements)
        or set(records_by_id) != set(requirements_by_id)
    ):
        raise ValueError("engineering seal artifact IDs do not exactly match ResolvedRun")
    verifier_requirements = {
        item.artifact_id: item for item in run.task.verifier.artifact_requirements
    }
    if verifier_requirements != requirements_by_id:
        raise ValueError("Verifier artifact requirements differ from the engineering run inventory")
    for artifact_id, requirement in requirements_by_id.items():
        record = records_by_id[artifact_id]
        if any(
            getattr(record, name) != getattr(requirement, name)
            for name in (
                "artifact_id",
                "artifact_type",
                "producer_id",
                "visibility",
                "relative_path",
            )
        ) or record.size_bytes > requirement.max_size_bytes:
            raise ValueError("sealed engineering artifact identity differs from its declaration")
        _relative_path(record.relative_path)
    expected_order = tuple(
        sorted(
            seal.artifacts,
            key=lambda item: (item.producer_id, item.artifact_type, item.artifact_id),
        )
    )
    if seal.artifacts != expected_order:
        raise ValueError("sealed engineering artifacts are not canonically ordered")
    event_records = tuple(
        record
        for record in seal.artifacts
        if record.artifact_type == "event.log"
        and record.producer_id == "harness"
        and record.visibility == "private"
    )
    if len(event_records) != 1:
        raise ValueError("engineering seal lacks one authoritative event ledger")
    event_record = event_records[0]

    expected_paths = {record.relative_path for record in seal.artifacts} | {
        _SEAL_MANIFEST_NAME
    }
    root_descriptor = _open_root(sealed_root)
    terminal_tail: bytes | None = None
    try:
        before = _inventory(root_descriptor)
        if before != (expected_paths, _expected_directories(expected_paths)):
            raise ValueError("sealed root inventory differs from exact declared artifacts")
        expected_manifest = canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
        manifest = _read_at(
            root_descriptor,
            PurePosixPath(_SEAL_MANIFEST_NAME),
            expected_size=len(expected_manifest),
        )
        if manifest != expected_manifest:
            raise ValueError("stored seal manifest differs from SealManifest")
        for record in seal.artifacts:
            digest, tail = _digest_at(
                root_descriptor,
                _relative_path(record.relative_path),
                expected_size=record.size_bytes,
                capture_tail=record.artifact_id == event_record.artifact_id,
            )
            if digest != record.sha256:
                raise ValueError(
                    f"sealed {record.artifact_type} digest differs from SealManifest"
                )
            if tail is not None:
                terminal_tail = tail
        if _inventory(root_descriptor) != before:
            raise ValueError("sealed root changed while being read")
    finally:
        os.close(root_descriptor)

    if not terminal_tail or not terminal_tail.endswith(b"\n"):
        raise ValueError("engineering event ledger is not newline terminated")
    without_final_newline = terminal_tail[:-1]
    separator = without_final_newline.rfind(b"\n")
    if separator < 0 and event_record.size_bytes > len(terminal_tail):
        raise ValueError("engineering terminal event exceeds its bounded record limit")
    raw_line = without_final_newline[separator + 1 :]
    document = _decode_json(raw_line)
    terminal = LedgerRecord.model_validate(document)
    if canonical_json_bytes(terminal.model_dump(mode="json")) != raw_line:
        raise ValueError("engineering terminal ledger record is not canonical")
    expected_time = SimulationTime(
        tick=config.final_tick,
        sim_time_ns=config.duration_ns,
    )
    if (
        terminal.event_hash != seal.event_chain_root
        or terminal.event.run_id != run.run_id
        or terminal.event.segment_kind != "runtime"
        or terminal.event.source_kind != "harness"
        or terminal.event.source != "harness"
        or terminal.event.event_type != "run.completed"
        or terminal.event.time != expected_time
    ):
        raise ValueError(
            "engineering runtime does not end in the sealed completed horizon"
        )
    return terminal


def _validated_sealed_bytes(
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root: Path,
) -> tuple[dict[str, ArtifactRecord], dict[str, bytes]]:
    canonical_seal = SealManifest.model_validate(seal.model_dump(mode="json"))
    if canonical_seal != seal or seal.run_id != run.run_id or seal.execution_scope != run.execution_scope:
        raise ValueError("seal identity differs from ResolvedRun")
    requirements = tuple(run.artifact_requirements)
    if len(requirements) != len(_ARTIFACT_AUTHORITIES):
        raise ValueError("ResolvedRun artifact inventory is not the exact urban inventory")
    by_type: dict[str, ArtifactRequirement] = {}
    for requirement in requirements:
        if requirement.artifact_type in by_type:
            raise ValueError("ResolvedRun repeats an urban artifact type")
        by_type[requirement.artifact_type] = requirement
    if set(by_type) != set(_ARTIFACT_AUTHORITIES):
        raise ValueError("ResolvedRun urban artifact types are not exact")
    verifier_requirements = {item.artifact_id: item for item in run.task.verifier.artifact_requirements}
    if verifier_requirements != {item.artifact_id: item for item in requirements}:
        raise ValueError("Verifier artifact requirements differ from the sealed run inventory")
    records_by_id = {record.artifact_id: record for record in seal.artifacts}
    if len(records_by_id) != len(seal.artifacts) or set(records_by_id) != {item.artifact_id for item in requirements}:
        raise ValueError("seal artifact IDs do not exactly match ResolvedRun")
    records_by_type: dict[str, ArtifactRecord] = {}
    for artifact_type, requirement in by_type.items():
        expected_producer, expected_visibility = _ARTIFACT_AUTHORITIES[artifact_type]
        record = records_by_id[requirement.artifact_id]
        if any(
            getattr(record, name) != getattr(requirement, name)
            for name in ("artifact_id", "artifact_type", "producer_id", "visibility", "relative_path")
        ):
            raise ValueError(f"sealed {artifact_type} identity differs from its declaration")
        if (
            requirement.producer_id != expected_producer
            or requirement.visibility != expected_visibility
            or requirement.source_asset_id is not None
            or record.size_bytes > requirement.max_size_bytes
        ):
            raise ValueError(f"sealed {artifact_type} authority is invalid")
        _relative_path(record.relative_path)
        records_by_type[artifact_type] = record
    expected_order = tuple(sorted(seal.artifacts, key=lambda item: (item.producer_id, item.artifact_type, item.artifact_id)))
    if seal.artifacts != expected_order:
        raise ValueError("sealed artifact records are not canonically ordered")

    expected_paths = {record.relative_path for record in seal.artifacts} | {_SEAL_MANIFEST_NAME}
    root_descriptor = _open_root(sealed_root)
    try:
        before = _inventory(root_descriptor)
        if before != (expected_paths, _expected_directories(expected_paths)):
            raise ValueError("sealed root inventory differs from exact declared artifacts")
        expected_manifest = canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
        manifest = _read_at(root_descriptor, PurePosixPath(_SEAL_MANIFEST_NAME), expected_size=len(expected_manifest))
        if manifest != expected_manifest:
            raise ValueError("stored seal manifest differs from SealManifest")
        contents: dict[str, bytes] = {}
        for artifact_type, record in records_by_type.items():
            content = _read_at(root_descriptor, _relative_path(record.relative_path), expected_size=record.size_bytes)
            if hashlib.sha256(content).hexdigest() != record.sha256:
                raise ValueError(f"sealed {artifact_type} digest differs from SealManifest")
            contents[artifact_type] = content
        after = _inventory(root_descriptor)
        if after != before:
            raise ValueError("sealed root changed while being read")
        for artifact_type, record in records_by_type.items():
            content = _read_at(root_descriptor, _relative_path(record.relative_path), expected_size=record.size_bytes)
            if hashlib.sha256(content).hexdigest() != record.sha256 or content != contents[artifact_type]:
                raise ValueError(f"sealed {artifact_type} changed after validation")
    finally:
        os.close(root_descriptor)
    return records_by_type, contents



def _load_ledger(raw: bytes, run: ResolvedRunSpec, seal: SealManifest) -> EventLedger:
    objects, _ = _canonical_jsonl(raw)
    records: list[LedgerRecord] = []
    lines = raw[:-1].split(b"\n")
    for document, line in zip(objects, lines, strict=True):
        record = LedgerRecord.model_validate(document)
        if canonical_json_bytes(record.model_dump(mode="json")) != line:
            raise ValueError("event ledger record is not canonical for its contract")
        records.append(record)
    ledger = EventLedger.from_records(tuple(records))
    validate_authoritative_runtime_ledger(run, ledger)
    if ledger.chain_root != seal.event_chain_root:
        raise ValueError("event ledger root differs from SealManifest")
    if not ledger.records:
        raise ValueError("event ledger is empty")
    terminal = ledger.records[-1].event
    if (
        terminal.event_type != "run.completed"
        or terminal.source != "harness"
        or terminal.time != SimulationTime(tick=FINAL_TICK, sim_time_ns=DURATION_NS)
    ):
        raise ValueError("runtime did not complete exactly tick 3000 at 600 seconds")
    commits = _records(ledger, event_type="barrier.committed")
    scene_commits = _records(ledger, event_type="scene.state.committed")
    if len(commits) != FINAL_TICK or len(scene_commits) != FINAL_TICK:
        raise ValueError("ledger does not contain exactly 3000 barrier and SceneState commits")
    expected_times = tuple(SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS) for tick in range(1, FINAL_TICK + 1))
    if tuple(record.event.time for record in commits) != expected_times or tuple(record.event.time for record in scene_commits) != expected_times:
        raise ValueError("ledger commit times do not close the fixed logical grid")
    reset_sources = tuple(sorted(record.event.source for record in _records(ledger, event_type="provider.reset")))
    if reset_sources != tuple(sorted(provider.provider_id for provider in run.environment.providers)):
        raise ValueError("provider reset inventory is not authoritative and exact")
    return ledger


def _load_scene_history(raw: bytes, run: ResolvedRunSpec, ledger: EventLedger) -> tuple[SceneState, ...]:
    states = scene_state_history_from_jsonl_bytes(raw)
    if scene_state_jsonl_bytes(states) != raw:
        raise ValueError("SceneState history bytes are not canonical")
    if len(states) != FINAL_TICK:
        raise ValueError("SceneState history does not contain exactly 3000 states")
    declared = tuple(entity.entity_id for entity in run.scenario.entities)
    commits = {record.event.time.tick: record for record in _records(ledger, event_type="scene.state.committed")}
    entity_events = _records(ledger, event_type="scene.entity-state")
    expected_sample_count = 0
    for tick, state in enumerate(states, start=1):
        expected_time = SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
        if (
            state.run_id != run.run_id
            or state.scenario_digest != run.scenario.scenario_digest
            or state.at != expected_time
            or state.declared_entity_ids != declared
        ):
            raise ValueError("SceneState identity, time, or entity coverage is incomplete")
        commit = commits.get(tick)
        if commit is None or _payload(commit).get("scene_state_digest") != state.scene_state_digest:
            raise ValueError("SceneState is not bound to its authoritative ledger commit")
        tick_events = [record for record in entity_events if record.event.time == expected_time]
        if len(tick_events) != len(state.samples):
            raise ValueError("ledger does not contain one entity-state event per SceneState sample")
        by_entity = {record.event.entity_id: record for record in tick_events}
        if len(by_entity) != len(tick_events) or set(by_entity) != set(state.declared_entity_ids):
            raise ValueError("SceneState entity event inventory is duplicated or incomplete")
        for sample in state.samples:
            event = by_entity[sample.entity_id].event
            payload = _payload(by_entity[sample.entity_id])
            expected_fields = {
                "entity_id", "provider_id", "run_id", "sample_digest", "sample_kind",
                "scenario_digest", "scene_state_digest", "state_sample_json",
                "target_sim_time_ns", "target_tick",
            }
            if (
                event.source != "harness"
                or set(payload) != expected_fields
                or payload["entity_id"] != sample.entity_id
                or payload["provider_id"] != sample.provider_id
                or payload["run_id"] != run.run_id
                or payload["scenario_digest"] != run.scenario.scenario_digest
                or payload["scene_state_digest"] != state.scene_state_digest
                or payload["sample_digest"] != sample.sample_digest
                or payload["sample_kind"] != sample.sample_kind
                or payload["target_tick"] != tick
                or payload["target_sim_time_ns"] != expected_time.sim_time_ns
                or payload["state_sample_json"] != canonical_json_bytes(sample.model_dump(mode="json")).decode("utf-8")
            ):
                raise ValueError("SceneState entity event binding is invalid")
        expected_sample_count += len(state.samples)
    if len(entity_events) != expected_sample_count:
        raise ValueError("ledger contains SceneState entity events outside the history")
    return states


def _parse_named_arguments(value: object) -> dict[str, object]:
    document = _canonical_json_text(value, label="command arguments_json")
    if not isinstance(document, list):
        raise ValueError("command arguments_json must encode an array")
    result: dict[str, object] = {}
    names: list[str] = []
    for item in document:
        if not isinstance(item, dict) or set(item) != {"name", "value"} or not isinstance(item["name"], str):
            raise ValueError("command argument is not an exact name/value object")
        name = item["name"]
        if name in result:
            raise ValueError("command argument name is duplicated")
        names.append(name)
        result[name] = item["value"]
    if names != sorted(names):
        raise ValueError("command arguments are not canonically ordered")
    return result


def _validated_observations(
    run: ResolvedRunSpec,
    package: DemoTaskPackage,
    ledger: EventLedger,
) -> dict[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]]:
    grants = {
        (agent.agent_id, grant.observation_id): grant
        for agent in run.agents
        for grant in agent.observations
    }
    expected_ids = {
        (role.agent_id, observation_id)
        for role in package.roles
        for observation_id in (
            role.mailbox_observation_id,
            role.telemetry_observation_id,
            role.safety_observation_id,
        )
        if observation_id is not None
    }
    if set(grants) != expected_ids:
        raise ValueError("Agent observation grants are not the exact urban least-privilege map")
    result: dict[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]] = {}
    expected_event_fields = {
        "run_id", "agent_id", "authentication_id", "observation_id", "provider_id",
        "schema_digest", "schema_path", "request_digest", "payload_digest",
        "payload_json", "envelope_json",
    }
    for record in _records(ledger, event_type="observation.validated"):
        event = record.event
        key_without_tick = (event.agent_id, event.observation_id)
        if key_without_tick not in grants:
            raise ValueError("ledger contains an undeclared urban Agent observation")
        grant = grants[key_without_tick]
        payload = _payload(record)
        if set(payload) != expected_event_fields:
            raise ValueError("validated observation event fields are not exact")
        document = _canonical_json_text(payload["payload_json"], label="observation payload_json")
        envelope_document = _canonical_json_text(payload["envelope_json"], label="observation envelope_json")
        if not isinstance(document, dict) or not isinstance(envelope_document, dict):
            raise ValueError("validated observation JSON is not an object")
        envelope = ObservationEnvelope.model_validate(envelope_document)
        envelope_payload = {item.name: item.value for item in envelope.payload}
        if len(envelope_payload) != len(envelope.payload):
            raise ValueError("validated observation envelope repeats payload fields")
        if (
            envelope_payload != document
            or hashlib.sha256(canonical_json_bytes(document)).hexdigest() != envelope.payload_digest
            or event.source != grant.provider_id
            or event.provider_id != grant.provider_id
            or envelope.run_id != run.run_id
            or envelope.agent_id != event.agent_id
            or envelope.observation_id != event.observation_id
            or envelope.time != event.time
            or payload["run_id"] != run.run_id
            or payload["agent_id"] != event.agent_id
            or payload["observation_id"] != event.observation_id
            or payload["provider_id"] != grant.provider_id
            or payload["schema_digest"] != grant.schema_file.sha256
            or payload["schema_path"] != grant.schema_file.path
            or payload["payload_digest"] != envelope.payload_digest
        ):
            raise ValueError("validated observation does not bind its grant, envelope, or payload")
        key = (str(event.agent_id), str(event.observation_id), event.time.tick)
        if key in result:
            raise ValueError("urban Agent observation is duplicated in one tick")
        result[key] = (record, document)
    expected_keys = {
        (agent_id, observation_id, tick)
        for agent_id, observation_id in expected_ids
        for tick in range(FINAL_TICK + 1)
    }
    if set(result) != expected_keys:
        raise ValueError("urban Agent observations do not cover every role at ticks 0 through 3000")
    return result


def _command_issues(run: ResolvedRunSpec, ledger: EventLedger) -> dict[str, tuple[LedgerRecord, dict[str, object]]]:
    grant_by_key = {
        (agent.agent_id, grant.tool_id): grant
        for agent in run.agents
        for grant in agent.tools
    }
    issues: dict[str, tuple[LedgerRecord, dict[str, object]]] = {}
    expected_fields = {
        "run_id", "agent_id", "authentication_id", "command_id", "tool_id",
        "provider_id", "request_digest", "request_schema_digest",
        "response_schema_digest", "arguments_json",
    }
    for record in _records(ledger, event_type="command.issued"):
        event = record.event
        payload = _payload(record)
        if set(payload) != expected_fields:
            raise ValueError("command.issued payload fields are not exact")
        command_id = payload["command_id"]
        agent_id = payload["agent_id"]
        tool_id = payload["tool_id"]
        if not all(isinstance(value, str) for value in (command_id, agent_id, tool_id)):
            raise ValueError("command.issued identity fields are invalid")
        grant = grant_by_key.get((agent_id, tool_id))
        if grant is None:
            raise ValueError("command.issued does not match an Agent tool grant")
        arguments = _parse_named_arguments(payload["arguments_json"])
        request_document = {
            "run_id": run.run_id,
            "agent_id": agent_id,
            "command_id": command_id,
            "tool_id": tool_id,
            "issued_at": event.time.model_dump(mode="json"),
            "arguments": [{"name": name, "value": value} for name, value in sorted(arguments.items())],
        }
        request_digest = hashlib.sha256(canonical_json_bytes(request_document)).hexdigest()
        if (
            event.source != agent_id
            or event.agent_id != agent_id
            or event.command_id != command_id
            or event.provider_id != grant.provider_id
            or payload["run_id"] != run.run_id
            or payload["provider_id"] != grant.provider_id
            or payload["request_digest"] != request_digest
            or payload["request_schema_digest"] != grant.request_schema.sha256
            or payload["response_schema_digest"] != grant.response_schema.sha256
        ):
            raise ValueError("command.issued does not bind its request and grant")
        if command_id in issues:
            raise ValueError("command_id has more than one command.issued event")
        issues[command_id] = (record, arguments)

    summaries_by_command: dict[str, LedgerRecord] = {}
    summary_ids: set[tuple[str, str]] = set()
    for record in _records(ledger, event_type="agent.decision-summary"):
        event = record.event
        payload = _payload(record)
        fields = {
            "run_id", "agent_id", "authentication_id", "summary_id", "command_id",
            "observation_ids_json", "decision_summary", "request_digest",
        }
        if set(payload) != fields:
            raise ValueError("Agent decision-summary fields are not exact")
        observation_ids = _canonical_json_text(payload["observation_ids_json"], label="decision observation_ids_json")
        request = DecisionSummaryRequest(
            schema_version="aero-bench.decision-summary-request/v1",
            run_id=run.run_id,
            agent_id=str(payload["agent_id"]),
            summary_id=str(payload["summary_id"]),
            at=event.time,
            decision_summary=str(payload["decision_summary"]),
            command_id=payload["command_id"],
            observation_ids=tuple(observation_ids) if isinstance(observation_ids, list) else (),
        )
        summary_key = (request.agent_id, request.summary_id)
        if summary_key in summary_ids:
            raise ValueError("Agent summary_id is duplicated")
        summary_ids.add(summary_key)
        if payload["request_digest"] != hashlib.sha256(canonical_json_bytes(request.model_dump(mode="json"))).hexdigest():
            raise ValueError("Agent decision-summary request digest is invalid")
        if request.command_id is not None:
            if request.command_id in summaries_by_command:
                raise ValueError("command_id has more than one Agent decision summary")
            summaries_by_command[request.command_id] = record
    if set(summaries_by_command) != set(issues):
        raise ValueError("every command must map uniquely to a prior Agent decision summary")
    for command_id, (issue, _) in issues.items():
        summary = summaries_by_command[command_id]
        if (
            summary.sequence >= issue.sequence
            or summary.event.time != issue.event.time
            or summary.event.agent_id != issue.event.agent_id
            or summary.event.correlation_id != command_id
        ):
            raise ValueError("Agent decision summary does not precede its correlated command")
    return issues



def _validate_agent_timeline(
    run: ResolvedRunSpec,
    package: DemoTaskPackage,
    ledger: EventLedger,
    observations: Mapping[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]],
    issues: Mapping[str, tuple[LedgerRecord, dict[str, object]]],
) -> None:
    agent_ids = tuple(role.agent_id for role in package.roles)
    completions: dict[tuple[str, int], tuple[LedgerRecord, AgentTurnCompletion]] = {}
    expected_fields = {"run_id", "agent_id", "completion_id", "disposition", "command_ids", "observation_ids"}
    for record in _records(ledger, event_type="agent.turn-completion"):
        event = record.event
        payload = _payload(record)
        if set(payload) != expected_fields:
            raise ValueError("Agent turn-completion fields are not exact")
        command_ids = _canonical_json_text(payload["command_ids"], label="turn command_ids")
        observation_ids = _canonical_json_text(payload["observation_ids"], label="turn observation_ids")
        if not isinstance(command_ids, list) or not isinstance(observation_ids, list):
            raise ValueError("Agent turn references are not arrays")
        completion = AgentTurnCompletion(
            schema_version="aero-bench.agent-turn-completion/v1",
            run_id=run.run_id,
            agent_id=str(payload["agent_id"]),
            completion_id=str(payload["completion_id"]),
            at=event.time,
            disposition=payload["disposition"],
            command_ids=tuple(command_ids),
            observation_ids=tuple(observation_ids),
        )
        key = (completion.agent_id, completion.at.tick)
        if key in completions:
            raise ValueError("Agent submitted duplicate turn completion")
        if (
            completion.agent_id not in agent_ids
            or event.source != completion.agent_id
            or event.agent_id != completion.agent_id
            or completion.at.sim_time_ns != completion.at.tick * STEP_NS
            or completion.disposition != ("finished" if completion.at.tick == FINAL_TICK else "advance")
        ):
            raise ValueError("Agent turn completion identity, time, or terminal disposition is invalid")
        expected_observations = tuple(
            sorted(
                observation_id
                for role in package.roles
                if role.agent_id == completion.agent_id
                for observation_id in (role.mailbox_observation_id, role.telemetry_observation_id, role.safety_observation_id)
                if observation_id is not None
            )
        )
        if completion.observation_ids != expected_observations:
            raise ValueError("Agent turn completion does not report its exact observation inventory")
        tick_commands = tuple(
            command_id
            for command_id, (issue, _) in sorted(
                (
                    (command_id, value)
                    for command_id, value in issues.items()
                    if value[0].event.agent_id == completion.agent_id
                    and value[0].event.time == completion.at
                ),
                key=lambda item: item[1][0].sequence,
            )
        )
        if completion.command_ids != tick_commands:
            raise ValueError("Agent turn completion does not map every issued command exactly once")
        causal_ids = {
            observations[(completion.agent_id, observation_id, completion.at.tick)][0].event.event_id
            for observation_id in completion.observation_ids
        } | {
            next(
                item.event.event_id
                for item in _records(ledger, event_type="command.validated")
                if item.event.command_id == command_id and item.event.agent_id == completion.agent_id
            )
            for command_id in completion.command_ids
        }
        if set(event.causal_event_ids) != causal_ids:
            raise ValueError("Agent turn completion causal event mapping is not exact")
        completions[key] = (record, completion)
    expected_completion_keys = {(agent_id, tick) for agent_id in agent_ids for tick in range(FINAL_TICK + 1)}
    if set(completions) != expected_completion_keys:
        raise ValueError("Agent turn completion coverage is not exactly ticks 0 through 3000")

    decisions: list[tuple[LedgerRecord, AgentTurnDecision]] = []
    fields = {"run_id", "status", "active_agent_ids", "missing_agent_ids"}
    for record in _records(ledger, event_type="agent.turn-decision"):
        payload = _payload(record)
        if set(payload) != fields:
            raise ValueError("Agent turn-decision fields are not exact")
        active = _canonical_json_text(payload["active_agent_ids"], label="active_agent_ids")
        missing = _canonical_json_text(payload["missing_agent_ids"], label="missing_agent_ids")
        if not isinstance(active, list) or not isinstance(missing, list):
            raise ValueError("Agent turn-decision inventories are not arrays")
        decision = AgentTurnDecision(
            schema_version="aero-bench.agent-turn-decision/v1",
            run_id=run.run_id,
            status=payload["status"],
            at=record.event.time,
            active_agent_ids=tuple(active),
            missing_agent_ids=tuple(missing),
        )
        decisions.append((record, decision))
    if len(decisions) != FINAL_TICK + 1:
        raise ValueError("Agent turn decisions do not close the exact horizon")
    for tick, (record, decision) in enumerate(decisions[:FINAL_TICK], start=1):
        prior_completion_ids = {completions[(agent_id, tick - 1)][0].event.event_id for agent_id in agent_ids}
        if (
            decision.status != "advanced"
            or decision.at != SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
            or decision.active_agent_ids != agent_ids
            or decision.missing_agent_ids
            or set(record.event.causal_event_ids) != prior_completion_ids
        ):
            raise ValueError("Agent advanced decision sequence is invalid")
    terminal_record, terminal = decisions[-1]
    if (
        terminal.status != "terminated"
        or terminal.at != SimulationTime(tick=FINAL_TICK, sim_time_ns=DURATION_NS)
        or terminal.active_agent_ids
        or terminal.missing_agent_ids
        or set(terminal_record.event.causal_event_ids)
        != {completions[(agent_id, FINAL_TICK)][0].event.event_id for agent_id in agent_ids}
    ):
        raise ValueError("Agent terminal decision is not uniquely mapped to final reports")


def _nested_px4(vehicle: Mapping[str, object]) -> dict[str, object]:
    fields = {
        "vehicle_id", "pose_json", "position_wgs84_json", "velocity_json",
        "angular_velocity_json", "attitude_json", "flight_mode", "armed", "in_air",
        "landed", "landed_state", "battery_percent", "health", "contacts",
        "ground_contact", "collision_contact", "simulation_time_ns",
    }
    item = _object(vehicle, fields, label="PX4 vehicle snapshot")
    pose = _object(_canonical_json_text(item["pose_json"], label="PX4 pose_json"), {"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}, label="PX4 pose")
    wgs = _object(_canonical_json_text(item["position_wgs84_json"], label="PX4 position_wgs84_json"), {"longitude_deg", "latitude_deg", "altitude_m"}, label="PX4 WGS84")
    velocity = _object(_canonical_json_text(item["velocity_json"], label="PX4 velocity_json"), {"north_m_s", "east_m_s", "down_m_s"}, label="PX4 velocity")
    angular = _object(_canonical_json_text(item["angular_velocity_json"], label="PX4 angular_velocity_json"), {"x_rad_s", "y_rad_s", "z_rad_s"}, label="PX4 angular velocity")
    attitude = _object(_canonical_json_text(item["attitude_json"], label="PX4 attitude_json"), {"roll_rad", "pitch_rad", "yaw_rad"}, label="PX4 attitude")
    health = _canonical_json_text(item["health"], label="PX4 health")
    numeric_values = [*pose.values(), *wgs.values(), *velocity.values(), *angular.values(), *attitude.values(), item["battery_percent"]]
    if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in numeric_values):
        raise ValueError("PX4 snapshot contains a non-finite numeric value")
    if not isinstance(health, dict) or not health or any(not isinstance(name, str) or type(value) is not bool for name, value in health.items()):
        raise ValueError("PX4 health evidence is not a non-empty boolean map")
    contacts = item["contacts"]
    if not isinstance(contacts, list) or contacts != sorted(set(contacts)) or any(not isinstance(value, str) for value in contacts):
        raise ValueError("PX4 contact inventory is not sorted and unique")
    ground = any(value.startswith("ground.") or value.startswith("launch_pad.") for value in contacts)
    collision = any(not value.startswith("ground.") and not value.startswith("launch_pad.") for value in contacts)
    if (
        type(item["armed"]) is not bool
        or type(item["in_air"]) is not bool
        or type(item["landed"]) is not bool
        or type(item["ground_contact"]) is not bool
        or type(item["collision_contact"]) is not bool
        or item["ground_contact"] != ground
        or item["collision_contact"] != collision
        or item["landed"] != (item["landed_state"] == "ON_GROUND")
    ):
        raise ValueError("PX4 arm, landed, or classified contact evidence is inconsistent")
    return {"raw": item, "pose": pose, "wgs": wgs, "velocity": velocity, "angular": angular, "health": health}


def _validate_flight_sample(state: SceneState, evidence: Mapping[str, object]) -> None:
    raw = evidence["raw"]
    assert isinstance(raw, dict)
    sample = next((item for item in state.samples if item.entity_id == raw["vehicle_id"] and item.provider_id == "flight"), None)
    if sample is None:
        raise ValueError("SceneState omits a PX4 vehicle sample")
    pose = evidence["pose"]
    velocity = evidence["velocity"]
    angular = evidence["angular"]
    health = evidence["health"]
    assert isinstance(pose, dict) and isinstance(velocity, dict) and isinstance(angular, dict) and isinstance(health, dict)
    attributes = {item.name: item.value for item in sample.attributes}
    health_attributes = {item.name: item.value for item in sample.health.attributes} if sample.health is not None else {}
    if (
        sample.pose.position.enu.east_m != pose["x_m"]
        or sample.pose.position.enu.north_m != pose["y_m"]
        or sample.pose.position.enu.up_m != pose["z_m"]
        or sample.linear_velocity_ned.north_mps != velocity["north_m_s"]
        or sample.linear_velocity_ned.east_mps != velocity["east_m_s"]
        or sample.linear_velocity_ned.down_mps != velocity["down_m_s"]
        or sample.angular_velocity_body is None
        or sample.angular_velocity_body.x_radps != angular["x_rad_s"]
        or sample.angular_velocity_body.y_radps != angular["y_rad_s"]
        or sample.angular_velocity_body.z_radps != angular["z_rad_s"]
        or sample.mode != raw["flight_mode"]
        or sample.armed != raw["armed"]
        or tuple(sample.contacts) != tuple(raw["contacts"])
        or sample.battery is None
        or sample.battery.remaining_fraction != float(raw["battery_percent"]) / 100.0
        or health_attributes != health
        or attributes != {
            "collision_contact": raw["collision_contact"],
            "contacts_complete": True,
            "ground_contact": raw["ground_contact"],
            "in_air": raw["in_air"],
            "landed": raw["landed"],
            "landed_state": raw["landed_state"],
            "telemetry_source": "gazebo-mavsdk",
        }
    ):
        raise ValueError("PX4 artifact snapshot differs from its authoritative SceneState sample")



def _validate_trajectory(
    raw: bytes,
    record: ArtifactRecord,
    run: ResolvedRunSpec,
    ledger: EventLedger,
    states: tuple[SceneState, ...],
    config: Px4GazeboConfig,
) -> tuple[dict[int, dict[str, dict[str, object]]], dict[int, str]]:
    documents, prefixes = _canonical_jsonl(raw)
    if len(documents) != FINAL_TICK + 1:
        raise ValueError("trajectory must contain reset plus exactly 3000 step records")
    fields = {
        "schema_version", "provider_id", "run_id", "operation", "tick", "sim_time_ns",
        "snapshot", "snapshot_sha256", "px4_version", "px4_commit", "gazebo_version",
        "gazebo_commit", "mavsdk_version", "mavsdk_commit", "runtime_image",
        "config_digest", "artifact_id", "artifact_type",
    }
    provider = next(item for item in run.environment.providers if item.provider_id == "flight")
    expected_vehicle_ids = tuple(sorted(vehicle.vehicle_id for vehicle in config.vehicles))
    by_tick: dict[int, dict[str, dict[str, object]]] = {}
    snapshot_digests: dict[int, str] = {}
    state_events = _records(ledger, schema=_PX4_STATE_SCHEMA, source="flight")
    receipt_by_tick = {
        item.event.time.tick: item
        for item in _records(ledger, event_type="provider.step-receipt", source="flight")
    }
    for index, (document, prefix) in enumerate(zip(documents, prefixes, strict=True)):
        item = _object(document, fields, label="trajectory record")
        expected_operation = "reset" if index == 0 else "step_stage"
        expected_time_ns = index * STEP_NS
        if (
            item["schema_version"] != "aero-bench.px4-evidence/v1"
            or item["provider_id"] != "flight"
            or item["run_id"] != run.run_id
            or item["operation"] != expected_operation
            or item["tick"] != index
            or item["sim_time_ns"] != expected_time_ns
            or item["px4_version"] != config.px4.version
            or item["px4_commit"] != config.px4.commit
            or item["gazebo_version"] != config.gazebo.version
            or item["gazebo_commit"] != config.gazebo.commit
            or item["mavsdk_version"] != config.mavsdk.version
            or item["mavsdk_commit"] != config.mavsdk.commit
            or item["runtime_image"] != provider.workload.runtime.image
            or item["config_digest"] != provider.config.file.sha256
            or item["artifact_id"] != record.artifact_id
            or item["artifact_type"] != "trajectory"
        ):
            raise ValueError("trajectory record identity or pinned runtime differs")
        snapshot = _object(item["snapshot"], {"sim_time_ns", "vehicles"}, label="PX4 snapshot")
        if snapshot["sim_time_ns"] != expected_time_ns or not isinstance(snapshot["vehicles"], list):
            raise ValueError("PX4 snapshot time or vehicle container is invalid")
        if item["snapshot_sha256"] != hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest():
            raise ValueError("PX4 snapshot digest is invalid")
        parsed = [_nested_px4(vehicle) for vehicle in snapshot["vehicles"]]
        ids = tuple(str(value["raw"]["vehicle_id"]) for value in parsed)  # type: ignore[index]
        if ids != expected_vehicle_ids:
            raise ValueError("PX4 snapshot does not contain the exact two sorted UAVs")
        tick_map = {str(value["raw"]["vehicle_id"]): value for value in parsed}  # type: ignore[index]
        if any(value["raw"]["simulation_time_ns"] != expected_time_ns for value in parsed):  # type: ignore[index]
            raise ValueError("PX4 vehicle telemetry is stale")
        by_tick[index] = tick_map
        snapshot_digests[index] = str(item["snapshot_sha256"])
        if index == 0:
            continue
        tick_events = [event for event in state_events if event.event.time.tick == index]
        if len(tick_events) != len(expected_vehicle_ids):
            raise ValueError("PX4 ledger state events do not cover both UAVs at every tick")
        event_by_vehicle = {_payload(event).get("vehicle_id"): event for event in tick_events}
        if set(event_by_vehicle) != set(expected_vehicle_ids):
            raise ValueError("PX4 ledger state event vehicle inventory is invalid")
        for vehicle_id in expected_vehicle_ids:
            evidence = tick_map[vehicle_id]
            vehicle = evidence["raw"]
            assert isinstance(vehicle, dict)
            event_record = event_by_vehicle[vehicle_id]
            payload = _payload(event_record)
            expected_payload = {
                "vehicle_id": vehicle_id,
                "pose_json": vehicle["pose_json"],
                "position_wgs84_json": vehicle["position_wgs84_json"],
                "velocity_json": vehicle["velocity_json"],
                "angular_velocity_json": vehicle["angular_velocity_json"],
                "attitude_json": vehicle["attitude_json"],
                "flight_mode": vehicle["flight_mode"],
                "armed": vehicle["armed"],
                "in_air": vehicle["in_air"],
                "landed": vehicle["landed"],
                "landed_state": vehicle["landed_state"],
                "contacts_json": canonical_json_bytes(vehicle["contacts"]).decode("utf-8"),
                "ground_contact": vehicle["ground_contact"],
                "battery_percent": vehicle["battery_percent"],
                "health": vehicle["health"],
                "collision_contact": vehicle["collision_contact"],
                "simulation_time_ns": expected_time_ns,
                "evidence_path": record.relative_path,
                "evidence_sha256": prefix,
            }
            if payload != expected_payload:
                raise ValueError("PX4 rolling artifact digest or state event payload is invalid")
            _validate_flight_sample(states[index - 1], evidence)
        receipt = receipt_by_tick.get(index)
        if receipt is None or _payload(receipt).get("state_digest") != item["snapshot_sha256"]:
            raise ValueError("PX4 snapshot is not bound to its staged receipt")
    if len(state_events) != FINAL_TICK * len(expected_vehicle_ids):
        raise ValueError("PX4 ledger contains state events outside the complete trajectory")
    return by_tick, snapshot_digests


def _sumo_entity(value: object) -> dict[str, Any]:
    fields = {
        "entity_id", "sumo_object_id", "kind", "lifecycle", "pose", "position_enu_m",
        "linear_velocity_enu", "linear_velocity_ned", "speed_mps", "yaw_enu_rad",
        "road_id", "lane_id",
    }
    entity = _object(value, fields, label="SUMO entity snapshot")
    if entity["kind"] not in {"vehicle", "person"} or entity["lifecycle"] not in {"pending", "active", "arrived", "removed"}:
        raise ValueError("SUMO entity kind or lifecycle is invalid")
    position = _object(entity["position_enu_m"], {"east_m", "north_m", "up_m"}, label="SUMO position")
    enu = _object(entity["linear_velocity_enu"], {"frame_id", "east_mps", "north_mps", "up_mps"}, label="SUMO ENU velocity")
    ned = _object(entity["linear_velocity_ned"], {"frame_id", "north_mps", "east_mps", "down_mps"}, label="SUMO NED velocity")
    pose = _object(entity["pose"], {"position", "orientation_enu", "orientation_ned"}, label="SUMO resolved pose")
    pose_position = _object(pose["position"], {"enu", "ned", "ecef", "wgs84", "geoid_separation_m", "amsl_m", "terrain_amsl_m", "agl_m"}, label="SUMO resolved position")
    if pose_position["enu"] != position or enu["frame_id"] != "ENU" or ned["frame_id"] != "NED":
        raise ValueError("SUMO pose and velocity frames are inconsistent")
    numeric: list[object] = [entity["speed_mps"], entity["yaw_enu_rad"]]
    for mapping in (position, enu, ned, pose_position):
        numeric.extend(value for value in mapping.values() if type(value) in (int, float))
    if any(not math.isfinite(float(value)) for value in numeric):
        raise ValueError("SUMO entity snapshot contains non-finite numbers")
    return entity


def _sumo_light(value: object) -> dict[str, Any]:
    light = _object(value, {"signal_id", "state", "phase_index", "next_switch_s", "program_id", "telemetry_source"}, label="SUMO traffic light")
    if (
        not isinstance(light["signal_id"], str)
        or not isinstance(light["state"], str)
        or not light["state"]
        or type(light["phase_index"]) is not int
        or light["phase_index"] < 0
        or type(light["next_switch_s"]) not in (int, float)
        or not math.isfinite(float(light["next_switch_s"]))
        or light["telemetry_source"] != "sumo-traci"
    ):
        raise ValueError("SUMO traffic-light snapshot is malformed")
    return light


def _validate_sumo_sample(state: SceneState, entity: Mapping[str, object]) -> None:
    sample = next((item for item in state.samples if item.entity_id == entity["entity_id"] and item.provider_id == "traffic"), None)
    if sample is None:
        raise ValueError("SceneState omits a SUMO object sample")
    attributes = {item.name: item.value for item in sample.attributes}
    expected_attributes = {
        "kind": entity["kind"], "lane_id": entity["lane_id"], "lifecycle": entity["lifecycle"],
        "road_id": entity["road_id"], "speed_mps": entity["speed_mps"],
        "sumo_object_id": entity["sumo_object_id"], "telemetry_source": "sumo-traci",
        "yaw_enu_rad": entity["yaw_enu_rad"],
    }
    if (
        sample.pose.model_dump(mode="json") != entity["pose"]
        or sample.linear_velocity_enu.model_dump(mode="json") != entity["linear_velocity_enu"]
        or sample.linear_velocity_ned.model_dump(mode="json") != entity["linear_velocity_ned"]
        or sample.mode != entity["lifecycle"]
        or sample.angular_velocity_body is not None
        or sample.armed is not None
        or sample.battery is not None
        or sample.health is not None
        or sample.contacts
        or attributes != expected_attributes
    ):
        raise ValueError("SUMO artifact snapshot differs from its authoritative SceneState sample")



def _validate_sumo(
    raw: bytes,
    record: ArtifactRecord,
    run: ResolvedRunSpec,
    ledger: EventLedger,
    states: tuple[SceneState, ...],
    config: SumoConfig,
) -> None:
    documents, prefixes = _canonical_jsonl(raw)
    if len(documents) != FINAL_TICK + 1:
        raise ValueError("SUMO evidence must contain reset plus exactly 3000 step records")
    if run.scenario.sumo is None:
        raise ValueError("ResolvedRun has no SUMO authority")
    fields = {
        "schema_version", "provider_id", "run_id", "operation", "tick", "sim_time_ns",
        "snapshot", "snapshot_sha256", "process_streams", "sumo_version", "sumo_commit",
        "runtime_image", "config_digest", "artifact_id", "artifact_type",
        "scenario_config_sha256",
    }
    provider = next(item for item in run.environment.providers if item.provider_id == "traffic")
    scenario_asset = next(item for item in run.scenario.assets if item.asset_id == run.scenario.sumo.config_asset_id)
    expected_bindings = {
        binding.entity_id: (binding.sumo_object_id, binding.kind)
        for binding in run.scenario.sumo.object_bindings
    }
    if {kind for _, kind in expected_bindings.values()} != {"vehicle", "person"}:
        raise ValueError("urban SUMO authority must declare both vehicle and person bindings")
    state_events = _records(ledger, schema=_SUMO_STATE_SCHEMA, source="traffic")
    signal_events = _records(ledger, schema="sumo.traffic_light.v1", source="traffic")
    receipt_by_tick = {
        item.event.time.tick: item
        for item in _records(ledger, event_type="provider.step-receipt", source="traffic")
    }
    signal_ids: tuple[str, ...] | None = None
    offsets = {"stderr": 0, "stdout": 0}
    for index, (document, prefix) in enumerate(zip(documents, prefixes, strict=True)):
        item = _object(document, fields, label="SUMO evidence record")
        expected_operation = "reset" if index == 0 else "step_stage"
        expected_time_ns = index * STEP_NS
        if (
            item["schema_version"] != "aero-bench.sumo-evidence/v3"
            or item["provider_id"] != "traffic"
            or item["run_id"] != run.run_id
            or item["operation"] != expected_operation
            or item["tick"] != index
            or item["sim_time_ns"] != expected_time_ns
            or item["sumo_version"] != config.sumo.version
            or item["sumo_commit"] != config.sumo.commit
            or item["runtime_image"] != provider.workload.runtime.image
            or item["config_digest"] != provider.config.file.sha256
            or item["artifact_id"] != record.artifact_id
            or item["artifact_type"] != "sumo.traffic.evidence"
            or item["scenario_config_sha256"] != scenario_asset.file.sha256
        ):
            raise ValueError("SUMO evidence record identity or pinned runtime differs")
        chunks = item["process_streams"]
        if not isinstance(chunks, list) or len(chunks) != 2:
            raise ValueError("SUMO process stream evidence is incomplete")
        names: list[str] = []
        for chunk_value in chunks:
            chunk = _object(chunk_value, {"stream", "offset_bytes", "size_bytes", "sha256", "encoding", "data"}, label="SUMO process stream")
            name = chunk["stream"]
            if name not in offsets or chunk["encoding"] != "base64" or chunk["offset_bytes"] != offsets[name]:
                raise ValueError("SUMO process stream offsets are not contiguous")
            try:
                content = base64.b64decode(str(chunk["data"]), validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("SUMO process stream data is not strict base64") from error
            if (
                base64.b64encode(content).decode("ascii") != chunk["data"]
                or len(content) != chunk["size_bytes"]
                or hashlib.sha256(content).hexdigest() != chunk["sha256"]
            ):
                raise ValueError("SUMO process stream digest or size is invalid")
            offsets[name] += len(content)
            names.append(name)
        if tuple(names) != ("stderr", "stdout"):
            raise ValueError("SUMO process streams are not canonically ordered")
        snapshot = _object(item["snapshot"], {"simulation_time_ns", "entities", "traffic_lights"}, label="SUMO snapshot")
        if snapshot["simulation_time_ns"] != expected_time_ns:
            raise ValueError("SUMO snapshot time differs from the logical barrier")
        if item["snapshot_sha256"] != hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest():
            raise ValueError("SUMO snapshot digest is invalid")
        if not isinstance(snapshot["entities"], list) or not isinstance(snapshot["traffic_lights"], list):
            raise ValueError("SUMO snapshot inventories are not arrays")
        entities = [_sumo_entity(value) for value in snapshot["entities"]]
        entity_ids = tuple(str(entity["entity_id"]) for entity in entities)
        if entity_ids != tuple(sorted(expected_bindings)):
            raise ValueError("SUMO snapshot does not close every resolved object binding")
        for entity in entities:
            if expected_bindings[entity["entity_id"]] != (entity["sumo_object_id"], entity["kind"]):
                raise ValueError("SUMO snapshot object identity differs from ResolvedScenario")
            if index:
                _validate_sumo_sample(states[index - 1], entity)
        lights = [_sumo_light(value) for value in snapshot["traffic_lights"]]
        current_signal_ids = tuple(str(light["signal_id"]) for light in lights)
        if not current_signal_ids or current_signal_ids != tuple(sorted(set(current_signal_ids))):
            raise ValueError("SUMO traffic-light inventory is empty, duplicated, or unsorted")
        if signal_ids is None:
            signal_ids = current_signal_ids
        elif current_signal_ids != signal_ids:
            raise ValueError("SUMO traffic-light inventory is not complete and stable for every tick")
        if index == 0:
            continue
        tick_state_events = [event for event in state_events if event.event.time.tick == index]
        if len(tick_state_events) != 1:
            raise ValueError("SUMO ledger does not contain one state event per tick")
        expected_state_payload = {
            "snapshot_digest": item["snapshot_sha256"],
            "evidence_path": record.relative_path,
            "evidence_sha256": prefix,
            "simulation_time_ns": expected_time_ns,
        }
        if _payload(tick_state_events[0]) != expected_state_payload:
            raise ValueError("SUMO rolling artifact digest is not bound to its state event")
        tick_signal_events = [event for event in signal_events if event.event.time.tick == index]
        if len(tick_signal_events) != 1 or _payload(tick_signal_events[0]) != {
            "snapshot_digest": item["snapshot_sha256"],
            "simulation_time_ns": expected_time_ns,
            "traffic_lights_json": canonical_json_bytes(snapshot["traffic_lights"]).decode("utf-8"),
        }:
            raise ValueError("SUMO signal event does not bind the complete traffic-light snapshot")
        receipt = receipt_by_tick.get(index)
        if receipt is None or _payload(receipt).get("state_digest") != item["snapshot_sha256"]:
            raise ValueError("SUMO snapshot is not bound to its staged receipt")
    if len(state_events) != FINAL_TICK or len(signal_events) != FINAL_TICK:
        raise ValueError("SUMO state or traffic-light events exist outside exact tick coverage")


def _network_record(value: object) -> dict[str, Any]:
    fields = {"source_artifact_id", "run_id", "work_order_id", "message_id", "payload_digest", "sent_at", "delivered_at"}
    record = _object(value, fields, label="network delivery artifact record")
    _sha(record["payload_digest"], label="network payload digest")
    sent = SimulationTime.model_validate(record["sent_at"])
    delivered = None if record["delivered_at"] is None else SimulationTime.model_validate(record["delivered_at"])
    if sent.tick > FINAL_TICK or sent.sim_time_ns != sent.tick * STEP_NS:
        raise ValueError("network submission time is outside the urban clock")
    if delivered is not None and (
        delivered.tick > FINAL_TICK
        or delivered.sim_time_ns != delivered.tick * STEP_NS
        or delivered.tick < sent.tick
    ):
        raise ValueError("network delivery time is outside its causal tick range")
    return record


def _recovery_message(arguments: Mapping[str, object], *, agent_id: str) -> RecoveryMessage:
    required = {"work_order_id", "message_id", "source", "destination", "payload_base64", "payload_sha256", "traffic_class", "priority", "reliability"}
    if set(arguments) != required:
        raise ValueError("network.send arguments are not exact")
    encoded = arguments["payload_base64"]
    if not isinstance(encoded, str):
        raise ValueError("network payload is not base64 text")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("network payload is not strict base64") from error
    if not raw or base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("network payload base64 is empty or non-canonical")
    document = _decode_json(raw)
    if not isinstance(document, dict) or canonical_json_bytes(document) != raw:
        raise ValueError("network recovery payload is not canonical JSON")
    message = RecoveryMessage.model_validate(document)
    if (
        hashlib.sha256(raw).hexdigest() != arguments["payload_sha256"]
        or message.message_id != arguments["message_id"]
        or message.sender_agent_id != agent_id
        or arguments["traffic_class"] != "best_effort"
        or arguments["priority"] != 0
        or arguments["reliability"] != "best_effort"
    ):
        raise ValueError("network payload identity differs from command or authenticated Agent")
    return message



def _groundstation_network_policy(
    reader: BundleReader,
    run: ResolvedRunSpec,
    package: DemoTaskPackage,
) -> UrbanParticipantConfig:
    ground_roles = tuple(role for role in package.roles if role.role == "groundstation")
    if len(ground_roles) != 1:
        raise ValueError("network verification requires one declared groundstation role")
    ground = ground_roles[0]
    asset_id = f"asset.participant.{ground.agent_id}"
    assets = tuple(asset for asset in run.task.assets if asset.asset_id == asset_id)
    if len(assets) != 1:
        raise ValueError("groundstation policy asset is absent or duplicated")
    asset = assets[0]
    audiences = {
        audience.role: tuple(audience.workload_ids) for audience in asset.audiences
    }
    if asset.classification != "public" or audiences != {
        "agent": (ground.agent_id,),
        "verifier": (package.verifier_id,),
    }:
        raise ValueError("groundstation policy asset lacks its exact Agent/verifier audience")
    policy = UrbanParticipantConfig.model_validate(reader.load_document(asset.file))
    expected_vehicle_endpoints = {
        str(role.vehicle_id): role.endpoint_id
        for role in package.roles
        if role.role == "uav"
    }
    expected_vehicle_agents = {
        str(role.vehicle_id): role.agent_id
        for role in package.roles
        if role.role == "uav"
    }
    if (
        policy.role != "groundstation"
        or policy.endpoint_id != ground.endpoint_id
        or policy.groundstation_endpoint_id != ground.endpoint_id
        or policy.mailbox_observation_id != ground.mailbox_observation_id
        or policy.vehicle_endpoint_ids != expected_vehicle_endpoints
        or policy.vehicle_agent_ids != expected_vehicle_agents
        or policy.response_timeout_ns != package.recovery.response_timeout_ns
        or policy.maximum_retries != package.recovery.maximum_retries
    ):
        raise ValueError("groundstation policy asset differs from the resolved network policy")
    return policy


def _validate_alert_retry_timeline(
    attempts: tuple[tuple[RecoveryMessage, LedgerRecord], ...],
    *,
    first_reply_at: SimulationTime | None,
    policy: UrbanParticipantConfig,
) -> None:
    if not attempts:
        return
    if len(attempts) > policy.maximum_retries + 1:
        raise _MissionFailure(
            "urban.mission.alert_retry_limit_exceeded",
            "alert submissions exceeded the declared retry limit",
        )
    first_message = attempts[0][0]
    incident = (
        first_message.sender_agent_id,
        first_message.vehicle_id,
        first_message.incident_region_id,
        first_message.incident_sequence,
    )
    if any(
        (
            message.sender_agent_id,
            message.vehicle_id,
            message.incident_region_id,
            message.incident_sequence,
        )
        != incident
        for message, _ in attempts
    ):
        raise ValueError("alert retries do not preserve the submitted incident identity")

    first_tick = attempts[0][1].event.time.tick
    retry_interval_ticks = (
        policy.response_timeout_ns + STEP_NS - 1
    ) // STEP_NS
    stop_tick = FINAL_TICK if first_reply_at is None else first_reply_at.tick
    expected_ticks = [first_tick]
    for retry_number in range(1, policy.maximum_retries + 1):
        deadline_tick = first_tick + retry_number * retry_interval_ticks
        # A mailbox reply at a deadline is processed before _retry_alert, while
        # FINAL_TICK performs terminal validation before considering a retry.
        if deadline_tick >= stop_tick:
            break
        expected_ticks.append(deadline_tick)
    actual_ticks = [record.event.time.tick for _, record in attempts]
    if actual_ticks != expected_ticks:
        raise ValueError("alert retries do not occur on logical submission-time deadlines")

    exhaustion_tick = first_tick + (
        policy.maximum_retries + 1
    ) * retry_interval_ticks
    if (
        first_reply_at is not None
        and exhaustion_tick < first_reply_at.tick
        and exhaustion_tick < FINAL_TICK
    ):
        raise ValueError("recovery reply arrived after the participant exhausted its retry budget")


def _validate_network(
    raw: bytes,
    artifact: ArtifactRecord,
    run: ResolvedRunSpec,
    package: DemoTaskPackage,
    reader: BundleReader,
    ledger: EventLedger,
    observations: Mapping[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]],
    issues: Mapping[str, tuple[LedgerRecord, dict[str, object]]],
) -> tuple[tuple[RecoveryMessage, SimulationTime], tuple[RecoveryMessage, SimulationTime]]:
    policy = _groundstation_network_policy(reader, run, package)
    document = _canonical_document(raw, trailing_newline=False)
    if not isinstance(document, list):
        raise ValueError("network.delivery must be a canonical JSON array")
    records = tuple(_network_record(value) for value in document)
    message_ids = tuple(str(record["message_id"]) for record in records)
    if message_ids != tuple(sorted(set(message_ids))):
        raise ValueError("network delivery artifact message IDs are duplicated or unsorted")
    network_issues = {
        command_id: (issue, arguments)
        for command_id, (issue, arguments) in issues.items()
        if issue.event.provider_id == "network"
    }
    if any(_payload(issue).get("tool_id") != "network.send" for issue, _ in network_issues.values()):
        raise ValueError("network provider received an unsupported Agent tool")
    if len(network_issues) != len(records):
        raise ValueError("network delivery artifact does not cover every network.send submission")
    records_by_message = {str(record["message_id"]): record for record in records}
    roles = {role.agent_id: role for role in package.roles}
    roles_by_endpoint = {role.endpoint_id: role for role in package.roles}
    expected_work_order_id = f"urban.recovery.{run.run_id[:16]}"
    messages_by_id: dict[
        str, tuple[str, str, RecoveryMessage, str, LedgerRecord]
    ] = {}
    for command_id, (issue, arguments) in network_issues.items():
        agent_id = str(issue.event.agent_id)
        role = roles.get(agent_id)
        if role is None:
            raise ValueError("network command sender is not a declared urban role")
        message = _recovery_message(arguments, agent_id=agent_id)
        source = arguments["source"]
        destination = arguments["destination"]
        if (
            arguments["work_order_id"] != expected_work_order_id
            or source != role.endpoint_id
            or destination not in roles_by_endpoint
        ):
            raise ValueError("network command work order or endpoint routing is unauthenticated")
        destination_role = roles_by_endpoint[str(destination)]
        if message.kind == "alert":
            if (
                role.role != "uav"
                or message.vehicle_id != role.vehicle_id
                or message.vehicle_id != package.recovery.incident_vehicle_id
                or message.incident_region_id != package.recovery.incident_region_id
                or destination_role.role != "groundstation"
            ):
                raise ValueError("alert sender, vehicle, region, or destination is unauthenticated")
        elif message.kind == "recovery_reply":
            if (
                role.role != "groundstation"
                or destination_role.vehicle_id != message.vehicle_id
                or message.vehicle_id != package.recovery.incident_vehicle_id
                or message.incident_region_id != package.recovery.incident_region_id
                or message.goal_enu_m != policy.recovery_goal_enu_m
                or message.forbidden_polygons != policy.forbidden_polygons
            ):
                raise ValueError("recovery reply identity or planning constraints are unauthenticated")
        elif role.role == "groundstation" and destination_role.role != "uav":
            raise ValueError("groundstation heartbeat is not routed to a declared UAV")
        elif role.role == "uav" and destination_role.role != "groundstation":
            raise ValueError("UAV heartbeat is not routed to the groundstation")
        record = records_by_message.get(message.message_id)
        if record is None or (
            record["source_artifact_id"] != artifact.artifact_id
            or record["run_id"] != run.run_id
            or record["work_order_id"] != expected_work_order_id
            or record["payload_digest"] != arguments["payload_sha256"]
            or SimulationTime.model_validate(record["sent_at"]) != issue.event.time
        ):
            raise ValueError("network submission is not byte-bound to its artifact record")
        if message.message_id in messages_by_id:
            raise ValueError("network message ID is submitted more than once")
        messages_by_id[message.message_id] = (
            command_id,
            agent_id,
            message,
            str(destination),
            issue,
        )

    alert_attempts = tuple(
        sorted(
            (
                (message, issue)
                for _, _, message, _, issue in messages_by_id.values()
                if message.kind == "alert"
            ),
            key=lambda item: item[1].sequence,
        )
    )
    if len(alert_attempts) > policy.maximum_retries + 1:
        raise _MissionFailure(
            "urban.mission.alert_retry_limit_exceeded",
            "alert submissions exceeded the declared retry limit",
        )

    delivery_events = _records(ledger, schema=_NETWORK_DELIVERY_SCHEMA, source="network")
    delivered_records = {
        str(record["message_id"]): record
        for record in records
        if record["delivered_at"] is not None
    }
    events_by_message: dict[str, LedgerRecord] = {}
    delivery_fields = {
        "schema_id", "run_id", "provider_id", "command_id", "agent_id", "source_artifact_id",
        "work_order_id", "message_id", "payload_digest", "sent_tick", "sent_time_ns",
        "delivered_tick", "delivered_time_ns",
    }
    for event_record in delivery_events:
        payload = _payload(event_record)
        if set(payload) != delivery_fields or payload["schema_id"] != _NETWORK_DELIVERY_SCHEMA:
            raise ValueError("ns-3 delivery event fields are not exact")
        message_id = payload["message_id"]
        if not isinstance(message_id, str) or message_id in events_by_message or message_id not in delivered_records:
            raise ValueError("ns-3 delivery event identity is duplicated or undeclared")
        artifact_record = delivered_records[message_id]
        command_id, agent_id, _, _, issue = messages_by_id[message_id]
        sent_at = issue.event.time
        delivered_at = SimulationTime.model_validate(artifact_record["delivered_at"])
        if (
            event_record.event.time != delivered_at
            or payload["run_id"] != run.run_id
            or payload["provider_id"] != "network"
            or payload["command_id"] != command_id
            or payload["agent_id"] != agent_id
            or payload["source_artifact_id"] != artifact.artifact_id
            or payload["work_order_id"] != expected_work_order_id
            or payload["payload_digest"] != artifact_record["payload_digest"]
            or payload["sent_tick"] != sent_at.tick
            or payload["sent_time_ns"] != sent_at.sim_time_ns
            or payload["delivered_tick"] != delivered_at.tick
            or payload["delivered_time_ns"] != delivered_at.sim_time_ns
        ):
            raise ValueError("ns-3 delivery event does not match the sealed submission record")
        events_by_message[message_id] = event_record
    if set(events_by_message) != set(delivered_records):
        raise ValueError("actual ns-3 delivery events do not exactly cover delivered artifact records")

    mailbox_messages: dict[str, tuple[str, dict[str, Any], LedgerRecord]] = {}
    for role in package.roles:
        for tick in range(FINAL_TICK + 1):
            event_record, payload = observations[(role.agent_id, role.mailbox_observation_id, tick)]
            mailbox = NetworkMailboxObservationPayload.model_validate(payload)
            if (
                mailbox.run_id != run.run_id
                or mailbox.provider_id != "network"
                or mailbox.agent_id != role.agent_id
                or mailbox.observation_id != role.mailbox_observation_id
                or mailbox.mailbox_endpoint_id != role.endpoint_id
                or mailbox.time_tick != tick
                or mailbox.sim_time_ns != tick * STEP_NS
                or event_record.event.time != SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
            ):
                raise ValueError("ns-3 mailbox observation identity or barrier time is invalid")
            messages_document = _canonical_json_text(mailbox.messages_json, label="mailbox messages_json")
            if not isinstance(messages_document, list):
                raise ValueError("mailbox messages_json is not an array")
            for raw_message in messages_document:
                if not isinstance(raw_message, dict):
                    raise ValueError("mailbox contains a non-object delivery")
                message_id = raw_message.get("message_id")
                if not isinstance(message_id, str) or message_id in mailbox_messages:
                    raise ValueError("mailbox exposes a delivery zero or multiple times")
                mailbox_messages[message_id] = (role.agent_id, raw_message, event_record)
    if set(mailbox_messages) != set(delivered_records):
        raise ValueError("mailbox delivery inventory differs from callback-confirmed ns-3 deliveries")
    for message_id, (recipient_agent, mailbox_raw, _) in mailbox_messages.items():
        command_id, sender_agent, _, destination, issue = messages_by_id[message_id]
        sent_at = issue.event.time
        record = delivered_records[message_id]
        delivered_at = SimulationTime.model_validate(record["delivered_at"])
        if (
            mailbox_raw.get("work_order_id") != expected_work_order_id
            or mailbox_raw.get("command_id") != command_id
            or mailbox_raw.get("sender_agent_id") != sender_agent
            or mailbox_raw.get("source") != roles[sender_agent].endpoint_id
            or mailbox_raw.get("destination") != destination
            or mailbox_raw.get("payload_sha256") != record["payload_digest"]
            or mailbox_raw.get("send_time") != sent_at.model_dump(mode="json")
            or mailbox_raw.get("arrival_time") != delivered_at.model_dump(mode="json")
            or roles[recipient_agent].endpoint_id != destination
        ):
            raise ValueError("mailbox message does not authenticate its outer ns-3 delivery")

    delivered_alerts = {
        message.message_id: (
            message,
            SimulationTime.model_validate(delivered_records[message.message_id]["delivered_at"]),
            issue,
        )
        for _, _, message, _, issue in messages_by_id.values()
        if message.kind == "alert" and message.message_id in delivered_records
    }
    replies_by_cause: dict[str, tuple[RecoveryMessage, LedgerRecord]] = {}
    for _, _, reply, _, reply_issue in messages_by_id.values():
        if reply.kind != "recovery_reply":
            continue
        cause_id = str(reply.cause_message_id)
        cause = delivered_alerts.get(cause_id)
        if cause is None or cause_id in replies_by_cause:
            raise ValueError("recovery reply cause is undelivered, undeclared, or duplicated")
        alert, alert_delivered_at, _ = cause
        recipient_agent, _, alert_observation = mailbox_messages[cause_id]
        delivery_event = events_by_message[cause_id]
        if (
            recipient_agent != next(
                role.agent_id for role in package.roles if role.role == "groundstation"
            )
            or reply.vehicle_id != alert.vehicle_id
            or reply.incident_region_id != alert.incident_region_id
            or reply.incident_sequence != alert.incident_sequence
            or reply_issue.event.time != alert_delivered_at
            or not delivery_event.sequence < alert_observation.sequence < reply_issue.sequence
        ):
            raise ValueError("recovery reply is not caused by a prior groundstation mailbox alert")
        replies_by_cause[cause_id] = (reply, reply_issue)
    if set(replies_by_cause) != set(delivered_alerts):
        raise ValueError("groundstation did not submit exactly one reply for every delivered alert")

    delivered_replies = sorted(
        (
            (
                reply,
                SimulationTime.model_validate(delivered_records[reply.message_id]["delivered_at"]),
            )
            for reply, _ in replies_by_cause.values()
            if reply.message_id in delivered_records
        ),
        key=lambda item: (item[1].tick, item[0].message_id),
    )
    first_reply = delivered_replies[0] if delivered_replies else None
    _validate_alert_retry_timeline(
        alert_attempts,
        first_reply_at=None if first_reply is None else first_reply[1],
        policy=policy,
    )
    if not delivered_alerts:
        raise _MissionFailure(
            "urban.mission.alert_not_delivered",
            "no incident alert was delivered by ns-3",
        )
    if first_reply is None:
        raise _MissionFailure(
            "urban.mission.recovery_reply_not_delivered",
            "no causally authenticated recovery reply was delivered by ns-3",
        )
    reply, reply_delivered_at = first_reply
    alert, alert_delivered_at, _ = delivered_alerts[str(reply.cause_message_id)]
    return (alert, alert_delivered_at), (reply, reply_delivered_at)



def _validate_airspace(
    package: DemoTaskPackage,
    ledger: EventLedger,
    observations: Mapping[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]],
    run: ResolvedRunSpec,
    config: Px4GazeboConfig,
    physics_origin: int,
) -> tuple[
    tuple[AirspaceTransition, LedgerRecord],
    tuple[AirspaceTransition, LedgerRecord],
]:
    authority = config.airspace_transition
    models = {vehicle.vehicle_id: vehicle.gazebo_model_name for vehicle in config.vehicles}
    if (
        authority is None or authority.world_digest != run.scenario.world_digest
        or authority.region_id != package.recovery.incident_region_id
        or type(physics_origin) is not int or physics_origin < 0
    ):
        raise ValueError("airspace config lacks declared world/region or native engine epoch authority")
    transitions: list[tuple[LedgerRecord, AirspaceTransition]] = []
    for record in _records(ledger, schema=_AIRSPACE_EVENT_SCHEMA):
        payload = _object(
            _payload(record), {"logical_origin_engine_ns", "transition_json"},
            label="Gazebo airspace-transition event",
        )
        transition = AirspaceTransition.model_validate(
            _canonical_json_text(payload["transition_json"], label="airspace transition_json")
        )
        logical_time = transition.engine_sim_time_ns - physics_origin
        if (
            record.event.source != "flight" or record.event.provider_id != "flight"
            or record.event.event_type != f"gazebo.airspace.{transition.transition}"
            or record.event.time.sim_time_ns != record.event.time.tick * STEP_NS
            or type(payload["logical_origin_engine_ns"]) is not int
            or payload["logical_origin_engine_ns"] != physics_origin
            or transition.engine_model != models.get(transition.vehicle_id)
            or transition.vehicle_id != package.recovery.incident_vehicle_id
            or transition.region_id != authority.region_id
            or transition.world_sha256 != authority.world_digest
            or transition.region_sha256 != authority.region_digest
            or transition.sequence != len(transitions) + 1
            or transition.transition != ("entered" if not transitions else "exited")
            or transition.engine_sim_time_ns % PHYSICS_STEP_NS
            or not record.event.time.sim_time_ns - STEP_NS < logical_time <= record.event.time.sim_time_ns
            or logical_time < package.recovery.injection_start_ns
        ):
            raise ValueError("airspace transition identity, model, epoch, sequence, or injection time is invalid")
        transitions.append((record, transition))
    if len(transitions) != 2:
        raise UrbanRecoveryVerificationError(
            "urban.evidence.airspace_authority_missing",
            "sealed ledger lacks exactly one full Gazebo system-plugin entry and exit",
        )
    enter_tick, exit_tick = (item[0].event.time.tick for item in transitions)
    if not 0 < enter_tick < exit_tick <= FINAL_TICK:
        raise ValueError("airspace entry and exit must occupy ordered, distinct barriers")
    by_tick = {record.event.time.tick: transition for record, transition in transitions}
    safety_fields = {
        "schema_version", "vehicle_id", "region_id", "airspace_state", "transition",
        "transition_sequence", "engine_sim_time_ns", "logical_origin_engine_ns",
        "transition_engine_sim_time_ns", "simulation_time_ns", "source",
        "world_sha256", "region_sha256", "position_enu_m",
    }
    for role in package.roles:
        if role.role != "uav":
            continue
        incident = role.vehicle_id == package.recovery.incident_vehicle_id
        for tick in range(FINAL_TICK + 1):
            observation_record, payload = observations[(role.agent_id, str(role.safety_observation_id), tick)]
            at = SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
            event = by_tick.get(tick) if incident else None
            expected_state = "inside" if incident and enter_tick <= tick < exit_tick else "outside"
            if (
                set(payload) != safety_fields
                or payload["schema_version"] != "aero-bench.observation.urban-safety.v1"
                or observation_record.event.time != at
                or observation_record.event.source != "flight"
                or observation_record.event.provider_id != "flight"
                or payload["vehicle_id"] != role.vehicle_id
                or payload["region_id"] != authority.region_id
                or type(payload["simulation_time_ns"]) is not int
                or payload["simulation_time_ns"] != at.sim_time_ns
                or type(payload["engine_sim_time_ns"]) is not int
                or payload["engine_sim_time_ns"] != physics_origin + at.sim_time_ns
                or type(payload["logical_origin_engine_ns"]) is not int
                or payload["logical_origin_engine_ns"] != physics_origin
                or payload["source"] != "gazebo.system"
                or payload["world_sha256"] != authority.world_digest
                or payload["region_sha256"] != authority.region_digest
                or payload["airspace_state"] != expected_state
                or payload["transition"] != (event.transition if event else "none")
                or type(payload["transition_sequence"]) is not int
                or payload["transition_sequence"] != (event.sequence if event else 0)
                or payload["transition_engine_sim_time_ns"] != (event.engine_sim_time_ns if event else None)
                or (event is not None and type(payload["transition_engine_sim_time_ns"]) is not int)
            ):
                raise ValueError("Gazebo safety observation differs from declared authority or native transition interval")
            position = _object(
                _canonical_json_text(payload["position_enu_m"], label="safety position_enu_m"),
                {"x", "y", "z"}, label="safety ENU position",
            )
            if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in position.values()):
                raise ValueError("Gazebo safety position is non-finite")
    return (transitions[0][1], transitions[0][0]), (transitions[1][1], transitions[1][0])

def _validate_telemetry_observations(
    package: DemoTaskPackage,
    observations: Mapping[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]],
    trajectory: Mapping[int, Mapping[str, Mapping[str, object]]],
) -> None:
    fields = {
        "schema_version", "vehicle_id", "simulation_time_ns", "pose_json",
        "position_wgs84_json", "velocity_json", "angular_velocity_json", "attitude_json",
        "flight_mode", "armed", "in_air", "landed", "landed_state", "battery_percent",
        "health", "contacts_json", "ground_contact", "collision_contact",
    }
    for role in package.roles:
        if role.role != "uav":
            continue
        for tick in range(FINAL_TICK + 1):
            _, payload = observations[(role.agent_id, str(role.telemetry_observation_id), tick)]
            if set(payload) != fields or payload["schema_version"] != "aero-bench.observation.urban-telemetry.v1":
                raise ValueError("PX4 telemetry observation fields are not exact")
            expected = trajectory[tick][str(role.vehicle_id)]["raw"]
            assert isinstance(expected, dict)
            comparison = dict(expected)
            comparison.pop("contacts")
            comparison["contacts_json"] = canonical_json_bytes(expected["contacts"]).decode("utf-8")
            if payload != {"schema_version": "aero-bench.observation.urban-telemetry.v1", **comparison}:
                raise ValueError("Agent telemetry observation differs from sealed trajectory evidence")


def _validate_physics(
    run: ResolvedRunSpec,
    package: DemoTaskPackage,
    ledger: EventLedger,
    trajectory: Mapping[int, Mapping[str, Mapping[str, object]]],
    physics_journal: bytes | None = None,
    physics_plugin: bytes | None = None,
) -> int:
    if not physics_journal:
        raise UrbanRecoveryVerificationError(
            "urban.evidence.physics_journal_missing",
            "independently sealed native physics journal bytes are required; ledger hashes are not raw authority",
        )
    if not physics_plugin:
        raise UrbanRecoveryVerificationError(
            "urban.evidence.physics_plugin_missing",
            "independently sealed physics plugin bytes are required",
        )
    plugin_digest = hashlib.sha256(physics_plugin).hexdigest()
    window_records = _records(ledger, schema=_ENGINE_WINDOW_SCHEMA, source="flight")
    wrench_records = _records(ledger, schema=_WRENCH_SCHEMA, source="flight")
    if not window_records or not wrench_records:
        raise UrbanRecoveryVerificationError(
            "urban.evidence.physics_authority_missing",
            "sealed ledger has no engine-window and applied/solver wrench authority",
        )
    windows: dict[int, EngineWindow] = {}
    prior: EngineWindow | None = None
    for record in window_records:
        payload = _object(_payload(record), {"window_json"}, label="Gazebo engine-window event")
        window = EngineWindow.model_validate(
            _canonical_json_text(payload["window_json"], label="engine window_json")
        )
        if (
            window.run_id != run.run_id
            or window.scenario_digest != run.scenario.scenario_digest
            or window.provider_id != "flight"
            or record.event.provider_id != "flight"
            or record.event.time != window.at
            or window.at.tick != len(windows) + 1
        ):
            raise ValueError("engine window identity, scenario, time, or tick is invalid")
        if (
            window.engine_start_ns % PHYSICS_STEP_NS
            or window.engine_end_ns % PHYSICS_STEP_NS
            or window.record_count != STEP_NS // PHYSICS_STEP_NS
            or window.journal_sha256 == "0" * 64
        ):
            raise ValueError("engine window does not prove fifty exact 4 ms physics steps")
        if window.plugin_sha256 != plugin_digest:
            raise ValueError("engine window plugin digest differs from sealed plugin bytes")
        if prior is not None and (
            window.engine_start_ns != prior.engine_end_ns
            or window.logical_origin_engine_ns != prior.logical_origin_engine_ns
            or window.first_sequence != prior.last_sequence
        ):
            raise ValueError("engine windows are not contiguous or sequence-closed")
        windows[window.at.tick] = window
        prior = window
    if set(windows) != set(range(1, FINAL_TICK + 1)):
        raise UrbanRecoveryVerificationError(
            "urban.evidence.physics_window_incomplete",
            "engine windows do not cover exactly all 3000 logical ticks",
        )

    # Index once; decoding and comparison below retain only one barrier's data.
    by_tick: dict[int, list[LedgerRecord]] = {}
    for record in wrench_records:
        window = windows.get(record.event.time.tick)
        if window is None or record.event.time != window.at:
            raise ValueError("applied wrench event is outside a declared barrier")
        by_tick.setdefault(window.at.tick, []).append(record)
    uav_ids = {str(role.vehicle_id) for role in package.roles if role.role == "uav"}
    with io.BytesIO(physics_journal) as journal:
        for tick, window in windows.items():
            _validate_physics_window(
                journal, window, by_tick.get(tick, ()), uav_ids, trajectory[tick],
            )
        if journal.read(1):
            raise ValueError("physics journal contains records beyond the exact horizon")
    return windows[1].logical_origin_engine_ns


def _validate_physics_window(
    journal: io.BytesIO,
    window: EngineWindow,
    wrench_records: list[LedgerRecord] | tuple[LedgerRecord, ...],
    uav_ids: set[str],
    trajectory: Mapping[str, Mapping[str, object]],
) -> None:
    wrenches: dict[tuple[str, int, str, str], AppliedWrench] = {}
    for record in wrench_records:
        payload = _object(_payload(record), {"vehicle_id", "wrench_json"}, label="Gazebo applied-wrench event")
        wrench = AppliedWrench.model_validate(
            _canonical_json_text(payload["wrench_json"], label="applied wrench_json")
        )
        key = (wrench.vehicle_id, wrench.engine_sim_time_ns, wrench.component, wrench.engine_link)
        if (
            wrench.vehicle_id not in uav_ids
            or record.event.provider_id != "flight"
            or record.event.vehicle_id != wrench.vehicle_id
            or record.event.time != window.at
            or payload["vehicle_id"] != wrench.vehicle_id
            or key in wrenches
        ):
            raise ValueError("applied wrench is duplicated or outside a declared engine/UAV identity")
        if not (
            window.engine_start_ns < wrench.engine_sim_time_ns <= window.engine_end_ns
            and wrench.engine_sim_time_ns % PHYSICS_STEP_NS == 0
        ):
            raise ValueError("applied wrench is outside the exact 4 ms engine grid")
        wrenches[key] = wrench

    ordinary_components = {"wind", "aerodynamic", "rotor", "gravity"}
    contact_proof: set[str] = set()
    digest = hashlib.sha256()
    for offset in range(1, window.record_count + 1):
        # A bounded read also rejects a missing newline or oversized native record.
        line = journal.readline(2 * 1024 * 1024 + 1)
        if len(line) > 2 * 1024 * 1024 or not line.endswith(b"\n"):
            raise ValueError("physics journal is truncated or its record exceeds the byte limit")
        document = _canonical_document(line, trailing_newline=True)
        step = PhysicsJournalRecord.model_validate(document)
        if canonical_json_bytes(step.model_dump(mode="json")) + b"\n" != line:
            raise ValueError("physics journal record is not canonical for its contract")
        if (
            step.run_id != window.run_id
            or step.scenario_digest != window.scenario_digest
            or step.plugin_sha256 != window.plugin_sha256
            or step.sequence != window.first_sequence + offset
            or step.engine_sim_time_ns != window.engine_start_ns + offset * PHYSICS_STEP_NS
        ):
            raise ValueError("physics journal identity, sequence, or engine time differs from its window")
        digest.update(line)
        components: dict[str, set[str]] = {}
        for wrench in step.wrenches:
            key = (wrench.vehicle_id, wrench.engine_sim_time_ns, wrench.component, wrench.engine_link)
            if wrenches.pop(key, None) != wrench:
                raise ValueError("sealed physics journal wrench differs from its ledger projection")
            components.setdefault(wrench.vehicle_id, set()).add(wrench.component)
            if wrench.component == "contact":
                contact_proof.add(wrench.vehicle_id)
        if set(components) != uav_ids or any(
            not ordinary_components <= components[vehicle_id] for vehicle_id in uav_ids
        ):
            raise UrbanRecoveryVerificationError(
                "urban.evidence.force_wind_torque_incomplete",
                "each native 4 ms step must expose both UAVs' applied wind, aerodynamic, rotor, gravity, force, and torque",
            )
    if wrenches:
        raise ValueError("ledger contains applied wrenches absent from the sealed physics journal")
    if digest.hexdigest() != window.journal_sha256:
        raise ValueError("engine window journal digest differs from sealed native bytes")
    for vehicle_id in uav_ids:
        raw = trajectory[vehicle_id]["raw"]
        if not isinstance(raw, dict):
            raise ValueError("physics contact comparison lacks validated trajectory")
        if raw["contacts"] and vehicle_id not in contact_proof:
            raise UrbanRecoveryVerificationError(
                "urban.evidence.solver_contact_missing",
                "a classified PX4 contact lacks solver-measured contact wrench evidence",
            )

def _physical_state_digest(vehicle_id: str, tick: int, evidence: Mapping[str, object]) -> str:
    raw = evidence["raw"]
    assert isinstance(raw, dict)
    return hashlib.sha256(canonical_json_bytes({
        "vehicle_id": vehicle_id,
        "tick": tick,
        "sim_time_ns": tick * STEP_NS,
        "pose": evidence["pose"],
        "position_wgs84": evidence["wgs"],
        "velocity_ned": evidence["velocity"],
        "angular_velocity_body": evidence["angular"],
        "flight_mode": raw["flight_mode"],
        "armed": raw["armed"],
        "in_air": raw["in_air"],
        "landed": raw["landed"],
        "landed_state": raw["landed_state"],
        "contacts": raw["contacts"],
        "ground_contact": raw["ground_contact"],
        "collision_contact": raw["collision_contact"],
    })).hexdigest()



def _finite_tree(value: object, *, label: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if type(value) in (int, float):
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _finite_tree(item, label=label)
        return
    if isinstance(value, dict):
        if any(not isinstance(name, str) for name in value):
            raise ValueError(f"{label} has a non-string key")
        for item in value.values():
            _finite_tree(item, label=label)
        return
    raise ValueError(f"{label} contains a non-JSON value")


def _validate_decoded_command_audit(
    audit: object,
    *,
    tool_id: str,
    vehicle_id: str,
    config: Px4GazeboConfig,
) -> str:
    fields = {
        "schema_version", "vehicle_system_id", "expected_mav_cmd", "expected_wire_type",
        "records", "record_sha256s", "journal_sha256", "terminal_ack_ingress_record",
        "terminal_ack_ingress_record_sha256", "terminal_ack_disposition_record",
        "terminal_ack_disposition_record_sha256",
    }
    value = _object(audit, fields, label="decoded MAVLink command audit")
    command_spec = {
        "flight.arm": (400, "COMMAND_LONG"),
        "flight.disarm": (400, "COMMAND_LONG"),
        "flight.takeoff": (22, "COMMAND_LONG"),
        "flight.land": (21, "COMMAND_LONG"),
        "flight.goto": (192, "COMMAND_INT"),
        "flight.hold": (176, "COMMAND_LONG"),
    }
    vehicle = next((item for item in config.vehicles if item.vehicle_id == vehicle_id), None)
    if vehicle is None:
        raise ValueError("decoded command audit names an undeclared vehicle")
    expected_command, expected_wire = command_spec[tool_id]
    records = value["records"]
    digests = value["record_sha256s"]
    if (
        value["schema_version"] != "aero-bench.px4-decoded-command-audit/v2"
        or value["vehicle_system_id"] != vehicle.system_id
        or value["expected_mav_cmd"] != expected_command
        or value["expected_wire_type"] != expected_wire
        or not isinstance(records, list)
        or not records
        or not isinstance(digests, list)
        or len(digests) != len(records)
    ):
        raise ValueError("decoded MAVLink command audit identity or inventory is invalid")
    lines: list[bytes] = []
    for record, digest in zip(records, digests, strict=True):
        if not isinstance(record, dict) or _sha(digest, label="decoded command audit record digest") != hashlib.sha256(canonical_json_bytes(record)).hexdigest():
            raise ValueError("decoded MAVLink command audit record digest is invalid")
        _finite_tree(record, label="decoded MAVLink command audit record")
        lines.append(canonical_json_bytes(record) + b"\n")
    ingress = value["terminal_ack_ingress_record"]
    disposition = value["terminal_ack_disposition_record"]
    if (
        not isinstance(ingress, dict)
        or not isinstance(disposition, dict)
        or value["terminal_ack_ingress_record_sha256"] != hashlib.sha256(canonical_json_bytes(ingress)).hexdigest()
        or value["terminal_ack_disposition_record_sha256"] != hashlib.sha256(canonical_json_bytes(disposition)).hexdigest()
        or ingress not in records
        or disposition not in records
        or ingress.get("kind") != "command_ack_ingress"
        or ingress.get("command") != expected_command
        or ingress.get("result") != 0
        or disposition.get("kind") != "command_ack_disposition"
        or disposition.get("command") != expected_command
        or disposition.get("status") != "matched_outstanding_command"
        or value["journal_sha256"] != hashlib.sha256(b"".join(lines)).hexdigest()
    ):
        raise ValueError("decoded MAVLink terminal ACK or journal binding is invalid")
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


_PHYSICAL_ARGUMENT_FIELDS = {
    "flight.arm": {"vehicle_id"},
    "flight.disarm": {"vehicle_id"},
    "flight.takeoff": {"vehicle_id", "altitude_m"},
    "flight.goto": {
        "vehicle_id",
        "latitude_deg",
        "longitude_deg",
        "altitude_amsl_m",
        "yaw_deg",
    },
    "flight.hold": {"vehicle_id"},
    "flight.land": {"vehicle_id"},
}
_PHYSICAL_MODE_FIELDS = {
    "flight.arm": "arm_allowed_modes",
    "flight.disarm": "disarm_allowed_modes",
    "flight.takeoff": "takeoff_allowed_modes",
    "flight.goto": "goto_allowed_modes",
    "flight.hold": "hold_allowed_modes",
    "flight.land": "land_allowed_modes",
}
_PHYSICAL_SETTLING_FIELDS = {
    "start_tick",
    "start_sim_time_ns",
    "end_tick",
    "end_sim_time_ns",
    "sample_count",
    "sample_duration_ns",
}


def _physical_json_exact(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(  # type: ignore[arg-type]
            _physical_json_exact(actual[name], value)  # type: ignore[index]
            for name, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(  # type: ignore[arg-type]
            _physical_json_exact(left, right)
            for left, right in zip(actual, expected, strict=True)  # type: ignore[arg-type]
        )
    return actual == expected


def _physical_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _physical_command_arguments(
    tool_id: str,
    arguments: Mapping[str, object],
    *,
    vehicle_id: str,
) -> None:
    expected_fields = _PHYSICAL_ARGUMENT_FIELDS.get(tool_id)
    if expected_fields is None or set(arguments) != expected_fields:
        raise ValueError("PX4 physical command arguments are not exact")
    if arguments["vehicle_id"] != vehicle_id:
        raise ValueError("PX4 physical command arguments name the wrong vehicle")
    for name in expected_fields - {"vehicle_id"}:
        _physical_number(arguments[name], label=f"{tool_id} argument {name}")


def _physical_thresholds(tool_id: str, config: Px4GazeboConfig) -> dict[str, object]:
    policy = config.physical_completion_policy
    mode_field = _PHYSICAL_MODE_FIELDS.get(tool_id)
    if mode_field is None:
        raise ValueError("PX4 physical command tool is unsupported")
    thresholds: dict[str, object] = {
        "allowlisted_modes": list(getattr(policy, mode_field)),
        "min_settle_samples": policy.min_settle_samples,
        "settle_duration_ns": policy.settle_duration_ns,
        "max_horizontal_settled_speed_m_s": policy.max_horizontal_settled_speed_m_s,
        "max_vertical_settled_speed_m_s": policy.max_vertical_settled_speed_m_s,
    }
    if tool_id == "flight.disarm":
        thresholds["disarm_requires_contact"] = policy.disarm_requires_contact
    elif tool_id == "flight.takeoff":
        thresholds["takeoff_altitude_tolerance_m"] = policy.takeoff_altitude_tolerance_m
    elif tool_id == "flight.goto":
        thresholds.update(
            {
                "goto_horizontal_tolerance_m": policy.goto_horizontal_tolerance_m,
                "goto_vertical_tolerance_m": policy.goto_vertical_tolerance_m,
                "goto_minimum_progress_m": policy.goto_minimum_progress_m,
            }
        )
    elif tool_id == "flight.hold":
        thresholds["hold_drift_radius_m"] = policy.hold_drift_radius_m
    elif tool_id == "flight.land":
        thresholds.update(
            {
                "landing_max_speed_m_s": policy.landing_max_speed_m_s,
                "landing_max_height_proxy_m": policy.landing_max_height_proxy_m,
            }
        )
    return thresholds


def _physical_state_parts(
    evidence: Mapping[str, object],
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    names_and_fields = (
        ("pose", {"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}),
        ("wgs", {"longitude_deg", "latitude_deg", "altitude_m"}),
        ("velocity", {"north_m_s", "east_m_s", "down_m_s"}),
        ("angular", {"x_rad_s", "y_rad_s", "z_rad_s"}),
    )
    raw = evidence.get("raw")
    if not isinstance(raw, dict):
        raise ValueError("PX4 physical state lacks authoritative raw telemetry")
    required_raw = {
        "flight_mode",
        "armed",
        "in_air",
        "landed",
        "landed_state",
        "contacts",
        "ground_contact",
        "collision_contact",
    }
    if not required_raw <= set(raw):
        raise ValueError("PX4 physical state telemetry is incomplete")
    values: list[Mapping[str, object]] = []
    for name, fields in names_and_fields:
        value = evidence.get(name)
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError(f"PX4 physical state {name} fields are not exact")
        for field, number in value.items():
            _physical_number(number, label=f"PX4 physical state {name}.{field}")
        values.append(value)
    if (
        not isinstance(raw["flight_mode"], str)
        or not isinstance(raw["landed_state"], str)
        or not isinstance(raw["contacts"], list)
        or any(not isinstance(contact, str) for contact in raw["contacts"])
        or any(
            type(raw[name]) is not bool
            for name in (
                "armed",
                "in_air",
                "landed",
                "ground_contact",
                "collision_contact",
            )
        )
    ):
        raise ValueError("PX4 physical state scalar telemetry is malformed")
    pose, wgs, velocity, angular = values
    return raw, pose, wgs, velocity, angular


def _physical_completion_state(
    tool_id: str,
    arguments: Mapping[str, object],
    *,
    vehicle_id: str,
    baseline: Mapping[str, object],
    current: Mapping[str, object],
    config: Px4GazeboConfig,
) -> tuple[bool, dict[str, object]]:
    _physical_command_arguments(tool_id, arguments, vehicle_id=vehicle_id)
    baseline_raw, baseline_pose, baseline_wgs, _, _ = _physical_state_parts(baseline)
    raw, pose, wgs, velocity, angular = _physical_state_parts(current)
    policy = config.physical_completion_policy
    allowed_modes = set(getattr(policy, _PHYSICAL_MODE_FIELDS[tool_id]))
    horizontal_speed_m_s = math.hypot(
        _physical_number(velocity["north_m_s"], label="north speed"),
        _physical_number(velocity["east_m_s"], label="east speed"),
    )
    down_m_s = _physical_number(velocity["down_m_s"], label="down speed")
    vertical_speed_m_s = abs(down_m_s)
    total_speed_m_s = math.sqrt(horizontal_speed_m_s**2 + down_m_s**2)
    angular_speed_rad_s = math.sqrt(
        sum(
            _physical_number(angular[name], label=f"angular speed {name}") ** 2
            for name in ("x_rad_s", "y_rad_s", "z_rad_s")
        )
    )
    speeds_settled = (
        horizontal_speed_m_s <= policy.max_horizontal_settled_speed_m_s
        and vertical_speed_m_s <= policy.max_vertical_settled_speed_m_s
    )
    metrics: dict[str, object] = {
        "armed": raw["armed"],
        "in_air": raw["in_air"],
        "landed": raw["landed"],
        "landed_state": raw["landed_state"],
        "flight_mode": raw["flight_mode"],
        "allowlisted_mode": raw["flight_mode"] in allowed_modes,
        "contacts": list(raw["contacts"]),  # type: ignore[arg-type]
        "ground_contact": raw["ground_contact"],
        "collision_contact": raw["collision_contact"],
        "horizontal_speed_m_s": horizontal_speed_m_s,
        "vertical_speed_m_s": vertical_speed_m_s,
        "total_speed_m_s": total_speed_m_s,
        "angular_speed_rad_s": angular_speed_rad_s,
        "settled_speed": speeds_settled,
    }
    satisfied = False
    if tool_id == "flight.arm":
        satisfied = (
            bool(raw["armed"])
            and bool(raw["landed"])
            and not bool(raw["in_air"])
            and bool(raw["ground_contact"])
            and not bool(raw["collision_contact"])
            and bool(metrics["allowlisted_mode"])
            and speeds_settled
        )
    elif tool_id == "flight.disarm":
        contact_requirement_satisfied = bool(raw["ground_contact"]) or not policy.disarm_requires_contact
        metrics["contact_requirement_satisfied"] = contact_requirement_satisfied
        satisfied = (
            not bool(raw["armed"])
            and bool(raw["landed"])
            and not bool(raw["in_air"])
            and bool(metrics["allowlisted_mode"])
            and speeds_settled
            and not bool(raw["collision_contact"])
            and contact_requirement_satisfied
        )
    elif tool_id == "flight.takeoff":
        requested_altitude_m = _physical_number(
            arguments["altitude_m"], label="flight.takeoff altitude_m"
        )
        gazebo_relative_altitude_m = _physical_number(
            pose["z_m"], label="current Gazebo altitude"
        ) - _physical_number(baseline_pose["z_m"], label="baseline Gazebo altitude")
        px4_absolute_altitude_delta_m = _physical_number(
            wgs["altitude_m"], label="current PX4 altitude"
        ) - _physical_number(baseline_wgs["altitude_m"], label="baseline PX4 altitude")
        gazebo_altitude_satisfied = (
            abs(gazebo_relative_altitude_m - requested_altitude_m)
            <= policy.takeoff_altitude_tolerance_m
        )
        px4_altitude_satisfied = (
            abs(px4_absolute_altitude_delta_m - requested_altitude_m)
            <= policy.takeoff_altitude_tolerance_m
        )
        metrics.update(
            {
                "requested_relative_altitude_m": requested_altitude_m,
                "gazebo_relative_altitude_m": gazebo_relative_altitude_m,
                "px4_absolute_altitude_delta_m": px4_absolute_altitude_delta_m,
                "gazebo_altitude_satisfied": gazebo_altitude_satisfied,
                "px4_altitude_satisfied": px4_altitude_satisfied,
                "no_collision_contact": not bool(raw["collision_contact"]),
            }
        )
        satisfied = (
            bool(raw["armed"])
            and bool(raw["in_air"])
            and not bool(raw["landed"])
            and not bool(raw["ground_contact"])
            and bool(metrics["allowlisted_mode"])
            and bool(metrics["no_collision_contact"])
            and speeds_settled
            and gazebo_altitude_satisfied
            and px4_altitude_satisfied
        )
    elif tool_id == "flight.goto":
        target_latitude_deg = _physical_number(
            arguments["latitude_deg"], label="flight.goto latitude_deg"
        )
        target_longitude_deg = _physical_number(
            arguments["longitude_deg"], label="flight.goto longitude_deg"
        )
        target_altitude_amsl_m = _physical_number(
            arguments["altitude_amsl_m"], label="flight.goto altitude_amsl_m"
        )
        current_delta = wgs84_amsl_delta_enu(
            origin_longitude_deg=_physical_number(wgs["longitude_deg"], label="current longitude"),
            origin_latitude_deg=_physical_number(wgs["latitude_deg"], label="current latitude"),
            origin_amsl_m=_physical_number(wgs["altitude_m"], label="current AMSL altitude"),
            target_longitude_deg=target_longitude_deg,
            target_latitude_deg=target_latitude_deg,
            target_amsl_m=target_altitude_amsl_m,
        )
        baseline_delta = wgs84_amsl_delta_enu(
            origin_longitude_deg=_physical_number(baseline_wgs["longitude_deg"], label="baseline longitude"),
            origin_latitude_deg=_physical_number(baseline_wgs["latitude_deg"], label="baseline latitude"),
            origin_amsl_m=_physical_number(baseline_wgs["altitude_m"], label="baseline AMSL altitude"),
            target_longitude_deg=target_longitude_deg,
            target_latitude_deg=target_latitude_deg,
            target_amsl_m=target_altitude_amsl_m,
        )
        horizontal_error_m = math.hypot(current_delta.x, current_delta.y)
        vertical_error_m = abs(current_delta.z)
        baseline_horizontal_error_m = math.hypot(baseline_delta.x, baseline_delta.y)
        baseline_vertical_error_m = abs(baseline_delta.z)
        target_local_x_m = _physical_number(baseline_pose["x_m"], label="baseline local x") + baseline_delta.x
        target_local_y_m = _physical_number(baseline_pose["y_m"], label="baseline local y") + baseline_delta.y
        target_local_z_m = _physical_number(baseline_pose["z_m"], label="baseline local z") + baseline_delta.z
        baseline_local_error_m = math.sqrt(
            baseline_delta.x**2 + baseline_delta.y**2 + baseline_delta.z**2
        )
        current_local_error_m = math.sqrt(
            (target_local_x_m - _physical_number(pose["x_m"], label="current local x")) ** 2
            + (target_local_y_m - _physical_number(pose["y_m"], label="current local y")) ** 2
            + (target_local_z_m - _physical_number(pose["z_m"], label="current local z")) ** 2
        )
        local_progress_m = baseline_local_error_m - current_local_error_m
        baseline_outside_tolerance = (
            baseline_horizontal_error_m > policy.goto_horizontal_tolerance_m
            or baseline_vertical_error_m > policy.goto_vertical_tolerance_m
        )
        progress_requirement_satisfied = (
            local_progress_m >= policy.goto_minimum_progress_m
            if baseline_outside_tolerance
            else True
        )
        metrics.update(
            {
                "target_latitude_deg": target_latitude_deg,
                "target_longitude_deg": target_longitude_deg,
                "target_altitude_amsl_m": target_altitude_amsl_m,
                "horizontal_error_m": horizontal_error_m,
                "vertical_error_m": vertical_error_m,
                "baseline_horizontal_error_m": baseline_horizontal_error_m,
                "baseline_vertical_error_m": baseline_vertical_error_m,
                "baseline_local_error_m": baseline_local_error_m,
                "current_local_error_m": current_local_error_m,
                "local_progress_m": local_progress_m,
                "baseline_outside_tolerance": baseline_outside_tolerance,
                "progress_requirement_satisfied": progress_requirement_satisfied,
            }
        )
        satisfied = (
            bool(raw["armed"])
            and bool(raw["in_air"])
            and not bool(raw["landed"])
            and not bool(raw["ground_contact"])
            and not bool(raw["collision_contact"])
            and bool(metrics["allowlisted_mode"])
            and speeds_settled
            and horizontal_error_m <= policy.goto_horizontal_tolerance_m
            and vertical_error_m <= policy.goto_vertical_tolerance_m
            and progress_requirement_satisfied
        )
    elif tool_id == "flight.hold":
        horizontal_drift_m = math.hypot(
            _physical_number(pose["x_m"], label="current hold x")
            - _physical_number(baseline_pose["x_m"], label="hold anchor x"),
            _physical_number(pose["y_m"], label="current hold y")
            - _physical_number(baseline_pose["y_m"], label="hold anchor y"),
        )
        armed_state_unchanged = raw["armed"] == baseline_raw["armed"]
        metrics.update(
            {
                "horizontal_drift_m": horizontal_drift_m,
                "armed_state_unchanged": armed_state_unchanged,
            }
        )
        satisfied = (
            bool(raw["armed"])
            and bool(raw["in_air"])
            and not bool(raw["landed"])
            and not bool(raw["ground_contact"])
            and not bool(raw["collision_contact"])
            and bool(metrics["allowlisted_mode"])
            and speeds_settled
            and horizontal_drift_m <= policy.hold_drift_radius_m
            and armed_state_unchanged
        )
    elif tool_id == "flight.land":
        vehicle = next(
            (item for item in config.vehicles if item.vehicle_id == vehicle_id), None
        )
        if vehicle is None:
            raise ValueError("PX4 landing command names an undeclared vehicle")
        ground_baseline_proxy_z_m = _physical_number(
            vehicle.initial_pose.z_m, label="vehicle initial pose z_m"
        )
        gazebo_relative_height_proxy_m = abs(
            _physical_number(pose["z_m"], label="current landing z_m")
            - ground_baseline_proxy_z_m
        )
        metrics.update(
            {
                "gazebo_relative_height_proxy_m": gazebo_relative_height_proxy_m,
                "ground_baseline_proxy_z_m": ground_baseline_proxy_z_m,
            }
        )
        satisfied = (
            bool(raw["landed"])
            and not bool(raw["in_air"])
            and bool(raw["ground_contact"])
            and not bool(raw["collision_contact"])
            and not bool(raw["armed"])
            and bool(metrics["allowlisted_mode"])
            and total_speed_m_s <= policy.landing_max_speed_m_s
            and gazebo_relative_height_proxy_m <= policy.landing_max_height_proxy_m
        )
    _finite_tree(metrics, label="recomputed PX4 physical metrics")
    return satisfied, metrics


def _empty_physical_settling() -> dict[str, object]:
    return {
        "start_tick": None,
        "start_sim_time_ns": None,
        "end_tick": None,
        "end_sim_time_ns": None,
        "sample_count": 0,
        "sample_duration_ns": 0,
    }


def _physical_evidence_at(
    trajectory: Mapping[int, Mapping[str, Mapping[str, object]]],
    *,
    tick: int,
    vehicle_id: str,
) -> Mapping[str, object]:
    tick_states = trajectory.get(tick)
    if tick_states is None or vehicle_id not in tick_states:
        raise ValueError("sealed trajectory lacks a physical command state sample")
    return tick_states[vehicle_id]


def _recomputed_physical_settling(
    tool_id: str,
    arguments: Mapping[str, object],
    *,
    vehicle_id: str,
    baseline_tick: int,
    applied_tick: int,
    completed_tick: int,
    trajectory: Mapping[int, Mapping[str, Mapping[str, object]]],
    config: Px4GazeboConfig,
) -> dict[str, object]:
    policy = config.physical_completion_policy
    if completed_tick * STEP_NS >= baseline_tick * STEP_NS + policy.physical_sim_timeout_ns:
        raise ValueError("PX4 physical completion occurs at or after its policy timeout")
    baseline = _physical_evidence_at(
        trajectory, tick=baseline_tick, vehicle_id=vehicle_id
    )
    applied = _physical_evidence_at(
        trajectory, tick=applied_tick, vehicle_id=vehicle_id
    )
    previous_digest = _physical_state_digest(vehicle_id, applied_tick, applied)
    start_tick: int | None = None
    end_tick: int | None = None
    sample_count = 0
    for tick in range(applied_tick + 1, completed_tick + 1):
        current = _physical_evidence_at(trajectory, tick=tick, vehicle_id=vehicle_id)
        satisfied, _ = _physical_completion_state(
            tool_id,
            arguments,
            vehicle_id=vehicle_id,
            baseline=baseline,
            current=current,
            config=config,
        )
        current_digest = _physical_state_digest(vehicle_id, tick, current)
        if satisfied:
            if current_digest != previous_digest:
                if start_tick is None:
                    start_tick = tick
                end_tick = tick
                sample_count += 1
        else:
            start_tick = None
            end_tick = None
            sample_count = 0
        duration_ns = (
            0 if start_tick is None or end_tick is None else (end_tick - start_tick) * STEP_NS
        )
        if (
            sample_count >= policy.min_settle_samples
            and duration_ns >= policy.settle_duration_ns
        ):
            if tick != completed_tick:
                raise ValueError("PX4 physical proof delays completion past the first qualifying sample")
            return {
                "start_tick": start_tick,
                "start_sim_time_ns": start_tick * STEP_NS,
                "end_tick": end_tick,
                "end_sim_time_ns": end_tick * STEP_NS,
                "sample_count": sample_count,
                "sample_duration_ns": duration_ns,
            }
        previous_digest = current_digest
    raise ValueError("PX4 completed proof lacks the required physical settling coverage")


def _validate_physical_proof_measurements(
    proof: Mapping[str, object],
    *,
    tool_id: str,
    arguments: Mapping[str, object],
    vehicle_id: str,
    phase: str,
    baseline: Mapping[str, object],
    current: Mapping[str, object],
    config: Px4GazeboConfig,
) -> None:
    expected_thresholds = _physical_thresholds(tool_id, config)
    satisfied, expected_metrics = _physical_completion_state(
        tool_id,
        arguments,
        vehicle_id=vehicle_id,
        baseline=baseline,
        current=current,
        config=config,
    )
    settling = _object(
        proof["settling"],
        _PHYSICAL_SETTLING_FIELDS,
        label="PX4 physical settling",
    )
    if not _physical_json_exact(proof["thresholds"], expected_thresholds):
        raise ValueError("PX4 physical proof thresholds differ from declared policy")
    if not _physical_json_exact(proof["metrics"], expected_metrics):
        raise ValueError("PX4 physical proof metrics differ from sealed trajectory states")
    if phase == "applied" and not _physical_json_exact(
        settling, _empty_physical_settling()
    ):
        raise ValueError("PX4 applied proof has impossible settling history")
    if phase == "completed" and not satisfied:
        raise ValueError("PX4 completed proof does not satisfy its physical predicate")


def _validate_physical_commands(
    issues: Mapping[str, tuple[LedgerRecord, dict[str, object]]],
    ledger: EventLedger,
    trajectory: Mapping[int, Mapping[str, Mapping[str, object]]],
    config: Px4GazeboConfig,
) -> dict[str, tuple[SimulationTime, SimulationTime]]:
    flight_issues = {
        command_id: (record, arguments)
        for command_id, (record, arguments) in issues.items()
        if record.event.provider_id == "flight"
    }
    allowed_tools = {"flight.arm", "flight.disarm", "flight.takeoff", "flight.land", "flight.goto", "flight.hold"}
    proofs: dict[str, dict[str, tuple[LedgerRecord, dict[str, object], str]]] = {}
    event_fields = {"schema_id", "command_id", "tool_id", "phase", "proof_json"}
    proof_fields = {
        "command_id", "tool_id", "vehicle_id", "phase", "policy_schema_version",
        "baseline_tick", "baseline_sim_time_ns", "baseline_state_digest", "current_tick",
        "current_sim_time_ns", "current_state_digest", "accepted_tick", "accepted_sim_time_ns",
        "applied_tick", "applied_sim_time_ns", "ack_audit_status", "decoded_command_audit",
        "thresholds", "metrics", "settling",
    }
    for record in _records(ledger, schema=_PHYSICAL_PROOF_SCHEMA, source="flight"):
        payload = _object(_payload(record), event_fields, label="PX4 physical proof event")
        command_id = payload["command_id"]
        phase = payload["phase"]
        if (
            payload["schema_id"] != _PHYSICAL_PROOF_SCHEMA
            or not isinstance(command_id, str)
            or command_id not in flight_issues
            or payload["tool_id"] != _payload(flight_issues[command_id][0]).get("tool_id")
            or phase not in {"applied", "completed", "failed"}
            or record.event.provider_id != "flight"
            or record.event.command_id != command_id
        ):
            raise ValueError("PX4 physical proof event identity is invalid")
        proof = _object(_canonical_json_text(payload["proof_json"], label="PX4 proof_json"), proof_fields, label="PX4 physical proof")
        issue, arguments = flight_issues[command_id]
        tool_id = str(_payload(issue)["tool_id"])
        vehicle_id = arguments.get("vehicle_id")
        if tool_id not in allowed_tools or not isinstance(vehicle_id, str) or vehicle_id not in trajectory[0]:
            raise ValueError("PX4 physical proof command tool or vehicle is undeclared")
        baseline_tick = proof["baseline_tick"]
        current_tick = proof["current_tick"]
        if (
            proof["command_id"] != command_id
            or proof["tool_id"] != tool_id
            or proof["vehicle_id"] != vehicle_id
            or proof["phase"] != phase
            or proof["policy_schema_version"] != config.physical_completion_policy.schema_version
            or type(baseline_tick) is not int
            or type(current_tick) is not int
            or baseline_tick != issue.event.time.tick
            or not 0 <= baseline_tick <= current_tick <= FINAL_TICK
            or proof["baseline_sim_time_ns"] != baseline_tick * STEP_NS
            or proof["current_sim_time_ns"] != current_tick * STEP_NS
            or proof["accepted_tick"] != baseline_tick
            or proof["accepted_sim_time_ns"] != baseline_tick * STEP_NS
            or proof["baseline_state_digest"] != _physical_state_digest(vehicle_id, baseline_tick, trajectory[baseline_tick][vehicle_id])
            or proof["current_state_digest"] != _physical_state_digest(vehicle_id, current_tick, trajectory[current_tick][vehicle_id])
            or record.event.time != SimulationTime(tick=current_tick, sim_time_ns=current_tick * STEP_NS)
            or proof["ack_audit_status"] != "decoded_command_ack_accepted"
            or not isinstance(proof["thresholds"], dict)
            or not isinstance(proof["metrics"], dict)
            or not isinstance(proof["settling"], dict)
        ):
            raise ValueError("PX4 physical proof does not bind command acceptance and sealed physical states")
        _finite_tree(proof["thresholds"], label="PX4 physical thresholds")
        _finite_tree(proof["metrics"], label="PX4 physical metrics")
        _finite_tree(proof["settling"], label="PX4 physical settling")
        _validate_physical_proof_measurements(
            proof,
            tool_id=tool_id,
            arguments=arguments,
            vehicle_id=vehicle_id,
            phase=str(phase),
            baseline=trajectory[baseline_tick][vehicle_id],
            current=trajectory[current_tick][vehicle_id],
            config=config,
        )
        audit_digest = _validate_decoded_command_audit(
            proof["decoded_command_audit"], tool_id=tool_id, vehicle_id=vehicle_id, config=config
        )
        command_phases = proofs.setdefault(command_id, {})
        if str(phase) in command_phases:
            raise ValueError("PX4 physical proof repeats a command phase")
        command_phases[str(phase)] = (record, proof, audit_digest)
    if set(proofs) != set(flight_issues):
        raise UrbanRecoveryVerificationError(
            "urban.evidence.physical_command_proof_incomplete",
            "not every issued flight command has physical proof events",
        )
    completions: dict[str, tuple[SimulationTime, SimulationTime]] = {}
    for command_id, phases in proofs.items():
        if set(phases) != {"applied", "completed"}:
            if "failed" in phases:
                raise _MissionFailure("urban.mission.flight_command_failed", "PX4 physically failed an issued mission command")
            raise UrbanRecoveryVerificationError(
                "urban.evidence.physical_command_lifecycle_incomplete",
                "flight command does not have exactly applied and completed physical phases",
            )
        applied_record, applied, applied_audit = phases["applied"]
        completed_record, completed, completed_audit = phases["completed"]
        applied_at = applied_record.event.time
        completed_at = completed_record.event.time
        if (
            applied["applied_tick"] != applied_at.tick
            or applied["applied_sim_time_ns"] != applied_at.sim_time_ns
            or applied_at.tick != applied["baseline_tick"] + 1
            or completed["applied_tick"] != applied_at.tick
            or completed["applied_sim_time_ns"] != applied_at.sim_time_ns
            or completed_at.tick <= applied_at.tick
            or applied_audit != completed_audit
        ):
            raise ValueError("PX4 applied/completed physical command phases do not close")
        issue, arguments = flight_issues[command_id]
        tool_id = str(_payload(issue)["tool_id"])
        vehicle_id = arguments.get("vehicle_id")
        if not isinstance(vehicle_id, str):
            raise ValueError("PX4 physical command lacks a vehicle identity")
        expected_settling = _recomputed_physical_settling(
            tool_id,
            arguments,
            vehicle_id=vehicle_id,
            baseline_tick=issue.event.time.tick,
            applied_tick=applied_at.tick,
            completed_tick=completed_at.tick,
            trajectory=trajectory,
            config=config,
        )
        if not _physical_json_exact(completed["settling"], expected_settling):
            raise ValueError("PX4 completed proof settling differs from sealed trajectory coverage")
        completions[command_id] = (applied_at, completed_at)
    return completions


def _goto_target_enu(
    arguments: Mapping[str, object],
    *,
    transform: EnuTransform,
    geoid_separation_m: float,
) -> Vector3:
    fields = {"vehicle_id", "latitude_deg", "longitude_deg", "altitude_amsl_m", "yaw_deg"}
    if set(arguments) != fields or arguments["yaw_deg"] != 0.0:
        raise ValueError("flight.goto arguments are not the exact urban command shape")
    numeric = tuple(arguments[name] for name in ("latitude_deg", "longitude_deg", "altitude_amsl_m"))
    if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in numeric):
        raise ValueError("flight.goto target contains non-finite coordinates")
    return transform.geodetic_to_enu(
        longitude_deg=float(arguments["longitude_deg"]),
        latitude_deg=float(arguments["latitude_deg"]),
        altitude_m=float(arguments["altitude_amsl_m"]) + geoid_separation_m,
    )


def _near_vector(actual: Vector3, expected: Vector3, *, tolerance_m: float = 1e-5) -> bool:
    return (actual - expected).norm() <= tolerance_m


def _reply_observation_sequence(
    package: DemoTaskPackage,
    observations: Mapping[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]],
    reply: RecoveryMessage,
    delivered_at: SimulationTime,
) -> int:
    role = next(item for item in package.roles if item.vehicle_id == reply.vehicle_id)
    record, payload = observations[(role.agent_id, role.mailbox_observation_id, delivered_at.tick)]
    mailbox = NetworkMailboxObservationPayload.model_validate(payload)
    values = _canonical_json_text(mailbox.messages_json, label="recovery mailbox messages_json")
    if not isinstance(values, list) or sum(isinstance(value, dict) and value.get("message_id") == reply.message_id for value in values) != 1:
        raise ValueError("delivered recovery reply is not uniquely observed at its destination tick")
    return record.sequence


def _validate_mission(
    run: ResolvedRunSpec,
    package: DemoTaskPackage,
    ledger: EventLedger,
    issues: Mapping[str, tuple[LedgerRecord, dict[str, object]]],
    completions: Mapping[str, tuple[SimulationTime, SimulationTime]],
    observations: Mapping[tuple[str, str, int], tuple[LedgerRecord, dict[str, object]]],
    trajectory: Mapping[int, Mapping[str, Mapping[str, object]]],
    config: Px4GazeboConfig,
    entered: tuple[AirspaceTransition, LedgerRecord],
    alert: tuple[RecoveryMessage, SimulationTime],
    reply: tuple[RecoveryMessage, SimulationTime],
) -> None:
    flight_commands: dict[str, list[tuple[str, LedgerRecord, dict[str, object]]]] = {}
    for command_id, (record, arguments) in issues.items():
        if record.event.provider_id != "flight":
            continue
        agent_id = str(record.event.agent_id)
        role = next((item for item in package.roles if item.agent_id == agent_id), None)
        if role is None or role.role != "uav" or arguments.get("vehicle_id") != role.vehicle_id:
            raise ValueError("flight command is not bound to its owning UAV Agent")
        flight_commands.setdefault(str(role.vehicle_id), []).append((command_id, record, arguments))
    for commands in flight_commands.values():
        commands.sort(key=lambda item: item[1].sequence)
    uav_ids = tuple(sorted(str(role.vehicle_id) for role in package.roles if role.role == "uav"))
    if set(flight_commands) != set(uav_ids):
        raise _MissionFailure("urban.mission.uav_command_inventory_incomplete", "both UAVs did not execute physical mission commands")

    policy = config.physical_completion_policy
    final = trajectory[FINAL_TICK]
    initial = trajectory[0]
    for vehicle_id in uav_ids:
        commands = flight_commands[vehicle_id]
        tool_sequence = [str(_payload(record)["tool_id"]) for _, record, _ in commands]
        required_once = ("flight.arm", "flight.takeoff", "flight.land", "flight.disarm")
        if any(tool_sequence.count(tool) != 1 for tool in required_once):
            raise _MissionFailure("urban.mission.flight_sequence_incomplete", "each UAV requires exactly one arm, takeoff, land, and disarm")
        indices = [tool_sequence.index(tool) for tool in required_once]
        if indices != sorted(indices) or tool_sequence[0] != "flight.arm" or tool_sequence[-1] != "flight.disarm":
            raise _MissionFailure("urban.mission.flight_sequence_invalid", "UAV physical command order is invalid")
        land_command_id = commands[tool_sequence.index("flight.land")][0]
        if completions[land_command_id][1].sim_time_ns > package.recovery.landing_deadline_ns:
            raise _MissionFailure("urban.mission.landing_deadline_missed", "a UAV landed after the declared recovery deadline")
        raw = final[vehicle_id]["raw"]
        initial_pose = initial[vehicle_id]["pose"]
        final_pose = final[vehicle_id]["pose"]
        assert isinstance(raw, dict) and isinstance(initial_pose, dict) and isinstance(final_pose, dict)
        horizontal_return_error = math.hypot(
            float(final_pose["x_m"]) - float(initial_pose["x_m"]),
            float(final_pose["y_m"]) - float(initial_pose["y_m"]),
        )
        vertical_return_error = abs(float(final_pose["z_m"]) - float(initial_pose["z_m"]))
        if (
            raw["armed"]
            or raw["in_air"]
            or not raw["landed"]
            or raw["landed_state"] != "ON_GROUND"
            or not raw["ground_contact"]
            or raw["collision_contact"]
            or horizontal_return_error > policy.goto_horizontal_tolerance_m
            or vertical_return_error > policy.landing_max_height_proxy_m
        ):
            raise _MissionFailure("urban.mission.final_uav_state_invalid", "both UAVs are not returned, landed, disarmed, collision-free, and observed at tick 3000")
    if any(
        bool(trajectory[tick][vehicle_id]["raw"]["collision_contact"])  # type: ignore[index]
        for tick in range(FINAL_TICK + 1)
        for vehicle_id in uav_ids
    ):
        raise _MissionFailure("urban.mission.collision_detected", "PX4/Gazebo evidence proves a non-ground collision")

    entry, entry_record = entered
    alert_message, alert_delivered_at = alert
    incident_role = next(
        role for role in package.roles if role.vehicle_id == package.recovery.incident_vehicle_id
    )
    entry_observation, _ = observations[
        (
            incident_role.agent_id,
            str(incident_role.safety_observation_id),
            entry_record.event.time.tick,
        )
    ]
    alert_issues = [
        record
        for record, arguments in issues.values()
        if arguments.get("message_id") == alert_message.message_id
    ]
    if (
        len(alert_issues) != 1
        or alert_message.incident_sequence != entry.sequence
        or alert_message.vehicle_id != entry.vehicle_id
        or alert_message.incident_region_id != entry.region_id
        or not entry_record.sequence < entry_observation.sequence < alert_issues[0].sequence
        or alert_delivered_at.sim_time_ns < alert_issues[0].event.time.sim_time_ns
    ):
        raise _MissionFailure(
            "urban.mission.alert_incident_mismatch",
            "authenticated alert is not causally bound to the exact plugin entry observation",
        )

    message, delivered_at = reply
    incident_id = str(message.vehicle_id)
    observation_sequence = _reply_observation_sequence(package, observations, message, delivered_at)
    start_pose = trajectory[delivered_at.tick][incident_id]["pose"]
    assert isinstance(start_pose, dict)
    goal = message.goal_enu_m
    if goal is None:
        raise ValueError("recovery reply omits its planning goal")
    try:
        path = RecoveryPlanner(
            vehicle_radius_m=package.recovery.vehicle_radius_m,
            obstacle_margin_m=package.recovery.obstacle_margin_m,
        ).plan(
            start_enu_m=(float(start_pose["x_m"]), float(start_pose["y_m"])),
            goal_enu_m=(goal.x, goal.y),
            forbidden_polygons=message.forbidden_polygons,
            altitude_m=goal.z,
            allow_blocked_start=True,
        )
    except RecoveryPlanningError as error:
        raise _MissionFailure("urban.mission.recovery_plan_impossible", str(error)) from error
    if len(path) < 2:
        raise _MissionFailure("urban.mission.recovery_plan_empty", "deterministic recovery plan contains no commanded waypoint")
    origin = run.scenario.frame_authority.origin
    transform = EnuTransform.from_origin(
        longitude_deg=origin.wgs84.longitude_deg,
        latitude_deg=origin.wgs84.latitude_deg,
        altitude_m=origin.wgs84.altitude_m,
    )
    incident_after_reply = [
        item for item in flight_commands[incident_id]
        if item[1].sequence > observation_sequence and _payload(item[1])["tool_id"] == "flight.goto"
    ]
    expected_recovery = tuple(Vector3(point.x, point.y, point.z) for point in path[1:])
    if len(incident_after_reply) != len(expected_recovery) + 1:
        raise _MissionFailure("urban.mission.recovery_command_inventory_invalid", "recovery waypoints and one return command are not exact")
    actual_recovery = tuple(
        _goto_target_enu(arguments, transform=transform, geoid_separation_m=origin.geoid_separation_m)
        for _, _, arguments in incident_after_reply[:len(expected_recovery)]
    )
    if any(not _near_vector(actual, expected) for actual, expected in zip(actual_recovery, expected_recovery, strict=True)):
        raise _MissionFailure("urban.mission.deterministic_replan_mismatch", "issued recovery waypoints differ from independent deterministic planner reconstruction")
    for vehicle_id in uav_ids:
        commands = flight_commands[vehicle_id]
        tool_sequence = [str(_payload(record)["tool_id"]) for _, record, _ in commands]
        land_index = tool_sequence.index("flight.land")
        return_candidates = [item for item in commands[:land_index] if _payload(item[1])["tool_id"] == "flight.goto"]
        if not return_candidates:
            raise _MissionFailure("urban.mission.return_command_missing", "a UAV has no physical return command before landing")
        return_target = _goto_target_enu(
            return_candidates[-1][2], transform=transform, geoid_separation_m=origin.geoid_separation_m
        )
        initial_pose = initial[vehicle_id]["pose"]
        assert isinstance(initial_pose, dict)
        expected_return = Vector3(float(initial_pose["x_m"]), float(initial_pose["y_m"]), package.recovery.cruise_agl_m)
        if not _near_vector(return_target, expected_return):
            raise _MissionFailure("urban.mission.return_target_invalid", "a UAV return command does not target its authoritative launch position")
    incident_tools = [str(_payload(record)["tool_id"]) for _, record, _ in flight_commands[incident_id]]
    if incident_tools.count("flight.hold") != 1:
        raise _MissionFailure("urban.mission.incident_hold_missing", "incident UAV did not physically hold exactly once after no-fly entry")


def _evidence_references(records: Mapping[str, ArtifactRecord]) -> tuple[EvidenceReference, ...]:
    selectors = {
        "event.log": "runtime-chain-root",
        "scene.state-history": "ticks/1-3000",
        "trajectory": "ticks/0-3000",
        "gazebo.physics-journal": "native-steps/1-150000",
        "gazebo.physics-plugin": "sha256",
        "network.delivery": "deliveries",
        "sumo.traffic.evidence": "ticks/0-3000",
    }
    return tuple(
        EvidenceReference(artifact_id=records[artifact_type].artifact_id, selector=selectors[artifact_type])
        for artifact_type in sorted(records)
    )



def verify_urban_recovery_sealed(
    *,
    bundle_root: Path,
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root: Path,
    config: UrbanRecoveryVerifierConfig,
) -> VerificationReport:
    """Verify only independently sealed runtime authority; never inspect live providers."""

    if config.execution_profile == "engineering":
        if run.execution_scope != "executor_validation":
            return _invalid_report(run, "urban.scope.engineering_not_scorable")
        try:
            validated_config = _phase(
                "urban.authority.verifier_config_invalid",
                lambda: UrbanRecoveryVerifierConfig.model_validate(
                    config.model_dump(mode="json")
                ),
            )
            if validated_config != config:
                raise UrbanRecoveryVerificationError(
                    "urban.authority.verifier_config_invalid",
                    "verifier config is not canonical",
                )
            _phase(
                "urban.authority.engineering_runtime_invalid",
                lambda: _validated_engineering_runtime_boundary(
                    run, seal, sealed_root, validated_config
                ),
            )
        except UrbanRecoveryVerificationError as error:
            return _invalid_report(run, error.failure_class)
        except Exception:
            return _invalid_report(run, "urban.verifier.internal_error")
        return _invalid_report(run, "urban.scope.executor_validation_not_scorable")
    if run.execution_scope != "formal_benchmark":
        return _invalid_report(run, "urban.scope.executor_validation_not_scorable")
    try:
        validated_config = _phase(
            "urban.authority.verifier_config_invalid",
            lambda: UrbanRecoveryVerifierConfig.model_validate(config.model_dump(mode="json")),
        )
        if validated_config != config:
            raise UrbanRecoveryVerificationError(
                "urban.authority.verifier_config_invalid",
                "verifier config is not canonical",
            )
        reader = _phase("urban.authority.bundle_invalid", lambda: BundleReader(bundle_root))
        package = _phase(
            "urban.authority.package_invalid",
            lambda: load_urban_recovery_package(reader=reader, task=run.task),
        )
        if (
            package.execution_profile != "formal"
            or config.execution_profile != "formal"
            or package.package_id != config.package_id
            or package.replay_mode != "indexed"
            or package.duration_ns != config.duration_ns
            or package.step_ns != config.step_ns
            or package.physics_step_ns != config.physics_step_ns
            or package.final_tick != config.final_tick
            or package.required_channels != config.required_channels
        ):
            raise UrbanRecoveryVerificationError(
                "urban.authority.package_config_mismatch",
                "task package, indexed replay declaration, and verifier fixed contract differ",
            )
        _phase("urban.authority.goals_invalid", lambda: validate_urban_goals(run.task))
        assessment = _phase(
            "urban.authority.resolution_invalid",
            lambda: UrbanRecoveryTaskPackageResolver().resolve(
                reader=reader,
                task=run.task,
                environment=run.environment,
                agents=run.agents,
                scenario=run.scenario,
            ),
        )
        if assessment != run.feasibility or not assessment.feasible:
            raise UrbanRecoveryVerificationError(
                "urban.authority.resolution_mismatch",
                "independent urban package resolution differs from ResolvedRun feasibility",
            )
        provider_by_id = {provider.provider_id: provider for provider in run.environment.providers}
        flight_config = _phase(
            "urban.authority.flight_config_invalid",
            lambda: Px4GazeboConfig.model_validate(
                reader.load_document(provider_by_id[package.flight_provider_id].config.file)
            ),
        )
        sumo_config = _phase(
            "urban.authority.sumo_config_invalid",
            lambda: SumoConfig.model_validate(
                reader.load_document(provider_by_id[package.traffic_provider_id].config.file)
            ),
        )
        records, contents = _phase(
            "urban.authority.sealed_inventory_invalid",
            lambda: _validated_sealed_bytes(run, seal, sealed_root),
        )
        ledger = _phase(
            "urban.authority.event_ledger_invalid",
            lambda: _load_ledger(contents["event.log"], run, seal),
        )
        states = _phase(
            "urban.authority.scene_history_invalid",
            lambda: _load_scene_history(contents["scene.state-history"], run, ledger),
        )
        observations = _phase(
            "urban.authority.agent_observations_invalid",
            lambda: _validated_observations(run, package, ledger),
        )
        issues = _phase(
            "urban.authority.agent_commands_invalid",
            lambda: _command_issues(run, ledger),
        )
        _phase(
            "urban.authority.agent_timeline_invalid",
            lambda: _validate_agent_timeline(run, package, ledger, observations, issues),
        )
        trajectory, _ = _phase(
            "urban.authority.trajectory_invalid",
            lambda: _validate_trajectory(
                contents["trajectory"], records["trajectory"], run, ledger, states, flight_config
            ),
        )
        _phase(
            "urban.authority.telemetry_observations_invalid",
            lambda: _validate_telemetry_observations(package, observations, trajectory),
        )
        _phase(
            "urban.authority.sumo_evidence_invalid",
            lambda: _validate_sumo(
                contents["sumo.traffic.evidence"], records["sumo.traffic.evidence"],
                run, ledger, states, sumo_config,
            ),
        )
        # Physics and command authority are checked before any mission outcome so
        # incomplete engine truth can never be mislabeled as a measured failure.
        physics_origin = _phase(
            "urban.authority.physics_evidence_invalid",
            lambda: _validate_physics(
                run, package, ledger, trajectory,
                contents["gazebo.physics-journal"], contents["gazebo.physics-plugin"],
            ),
        )
        completions = _phase(
            "urban.authority.physical_command_evidence_invalid",
            lambda: _validate_physical_commands(issues, ledger, trajectory, flight_config),
        )
        entered, _ = _phase(
            "urban.authority.airspace_evidence_invalid",
            lambda: _validate_airspace(package, ledger, observations, run, flight_config, physics_origin),
        )
        alert, reply = _phase(
            "urban.authority.network_evidence_invalid",
            lambda: _validate_network(
                contents["network.delivery"], records["network.delivery"], run,
                package, reader, ledger, observations, issues,
            ),
        )
        _phase(
            "urban.authority.mission_evidence_invalid",
            lambda: _validate_mission(
                run, package, ledger, issues, completions, observations,
                trajectory, flight_config, entered, alert, reply,
            ),
        )
        return _measured_report(
            run,
            horizon=ledger.records[-1].event.time,
            mission_passed=True,
            failure_class=None,
            evidence=_evidence_references(records),
            coverage_complete=True,
        )
    except _MissionFailure as error:
        # Inventory alone is not complete authority. An early mission failure
        # skips later validators, so it cannot claim complete coverage.
        if "records" not in locals() or "ledger" not in locals():
            return _invalid_report(run, "urban.authority.mission_without_complete_seal")
        return _measured_report(
            run,
            horizon=ledger.records[-1].event.time,
            mission_passed=False,
            failure_class=error.failure_class,
            evidence=_evidence_references(records),
            coverage_complete=False,
        )
    except UrbanRecoveryVerificationError as error:
        return _invalid_report(run, error.failure_class)
    except Exception:
        return _invalid_report(run, "urban.verifier.internal_error")


__all__ = [
    "URBAN_METRIC_ID",
    "URBAN_VERIFIER_SCHEMA",
    "UrbanRecoveryVerificationError",
    "UrbanRecoveryVerifierConfig",
    "verify_urban_recovery_sealed",
]
