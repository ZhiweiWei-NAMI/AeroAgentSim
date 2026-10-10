"""Offline urban recovery verifier workload entrypoint."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath

from aero_bench.artifacts.contracts import SealManifest, seal_manifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import NamedValue
from aero_bench.executor.contracts import VerifierWorkloadContract
from aero_bench.runtime.events import (
    RUN_EVENT_CHAIN_ROOT,
    AgentInteraction,
    RunEvent,
    RunEventAudience,
    agent_interaction_digest_value,
    run_event_digest_value,
    run_event_payload_digest_value,
)
from aero_bench.runtime.evidence import load_sealed_event_ledger
from aero_bench.runtime.ledger import (
    EventLedger,
    LedgerRecord,
    VerifierEventLedger,
    verifier_event_segment_jsonl_bytes,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.verifier import (
    UrbanRecoveryVerifierConfig,
    verify_urban_recovery_sealed,
)


class UrbanVerifierWorkloadError(RuntimeError):
    pass


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise UrbanVerifierWorkloadError(f"required environment is unavailable: {name}")
    return value


def _root(name: str) -> Path:
    path = Path(_env(name))
    if not path.is_absolute() or os.fspath(path) != os.path.normpath(os.fspath(path)):
        raise UrbanVerifierWorkloadError(f"{name} must be an absolute normalized path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise UrbanVerifierWorkloadError(f"{name} is unavailable") from error
    if resolved != path or not resolved.is_dir():
        raise UrbanVerifierWorkloadError(f"{name} must be a real directory")
    return resolved


def _canonical_object(path: Path, label: str) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UrbanVerifierWorkloadError(f"{label} is invalid") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) + b"\n" != raw:
        raise UrbanVerifierWorkloadError(f"{label} must be canonical JSON")
    return value


def _contract() -> VerifierWorkloadContract:
    path = Path(_env("AERO_BENCH_CONTRACT"))
    try:
        contract = VerifierWorkloadContract.model_validate(
            _canonical_object(path, "VerifierWorkloadContract")
        )
    except (TypeError, ValueError) as error:
        raise UrbanVerifierWorkloadError("VerifierWorkloadContract validation failed") from error
    if (
        _env("AERO_BENCH_ROLE") != "verifier"
        or _env("AERO_BENCH_RUN_ID") != contract.run_id
        or _env("AERO_BENCH_WORKLOAD_ID") != contract.workload_id
        or int(_env("AERO_BENCH_SEED")) != contract.seed
    ):
        raise UrbanVerifierWorkloadError("executor environment differs from verifier contract")
    return contract


def _seal(
    root: Path,
    contract: VerifierWorkloadContract,
    *,
    verify_artifacts: bool = True,
) -> SealManifest:
    try:
        value = SealManifest.model_validate(
            _canonical_object(root / "seal-manifest.json", "runtime seal")
        )
        if verify_artifacts:
            verified = seal_manifest(
                root=root,
                run_id=contract.run_id,
                attempt_id=value.attempt_id,
                execution_scope=contract.run.execution_scope,
                event_chain_root=value.event_chain_root,
                artifacts=value.artifacts,
                manifest_name="seal-manifest.json",
            )
            if verified != value:
                raise UrbanVerifierWorkloadError("runtime seal identity changed")
        elif (
            value.run_id != contract.run_id
            or value.execution_scope != contract.run.execution_scope
        ):
            raise UrbanVerifierWorkloadError("runtime seal identity changed")
    except (OSError, TypeError, ValueError) as error:
        raise UrbanVerifierWorkloadError("runtime seal validation failed") from error
    return value


def _output_requirements(contract: VerifierWorkloadContract):
    outputs = tuple(contract.verifier.output_artifacts)
    by_type = {item.artifact_type: item for item in outputs}
    expected = {"verification.report", "verification.event-segment"}
    if (
        len(outputs) != 2
        or set(by_type) != expected
        or any(item.producer_id != contract.workload_id for item in outputs)
        or len({item.artifact_id for item in outputs}) != 2
        or len({item.relative_path for item in outputs}) != 2
        or any(item.visibility != "public" for item in outputs)
    ):
        raise UrbanVerifierWorkloadError(
            "urban verifier requires exactly one public report and one public event segment"
        )
    return by_type["verification.report"], by_type["verification.event-segment"]


def _write(root: Path, relative: str, payload: bytes, maximum: int) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or str(path) != relative:
        raise UrbanVerifierWorkloadError("verification output path is invalid")
    if len(payload) > maximum:
        raise UrbanVerifierWorkloadError("verification output exceeds its size bound")
    target = root.joinpath(*path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise UrbanVerifierWorkloadError("verification output could not be written") from error


def _goal_payload(goal, report, report_payload: bytes) -> tuple[NamedValue, ...]:
    return tuple(
        NamedValue(name=name, value=value)
        for name, value in (
            ("failure_class", goal.failure_class),
            ("goal_id", goal.goal_id),
            (
                "goal_result_json",
                canonical_json_bytes(goal.model_dump(mode="json")).decode("utf-8"),
            ),
            ("passed", goal.passed),
            ("report_sha256", hashlib.sha256(report_payload).hexdigest()),
            ("report_status", report.status),
        )
    )


def _engineering_terminal_record(
    contract: VerifierWorkloadContract,
    seal: SealManifest,
    seal_root: Path,
    config: UrbanRecoveryVerifierConfig,
) -> LedgerRecord:
    event_artifacts = tuple(
        record
        for record in seal.artifacts
        if record.artifact_type == "event.log"
        and record.producer_id == "harness"
        and record.visibility == "private"
    )
    if len(event_artifacts) != 1:
        raise UrbanVerifierWorkloadError(
            "engineering seal lacks one authoritative event ledger"
        )
    artifact = event_artifacts[0]
    relative = PurePosixPath(artifact.relative_path)
    target = seal_root.joinpath(*relative.parts)
    try:
        resolved = target.resolve(strict=True)
        if resolved != target or not resolved.is_file():
            raise UrbanVerifierWorkloadError("engineering event ledger path is invalid")
        descriptor = os.open(
            target,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            before = os.fstat(descriptor)
            if before.st_size != artifact.size_bytes:
                raise UrbanVerifierWorkloadError(
                    "engineering event ledger size differs from its seal"
                )
            tail_size = min(artifact.size_bytes, 2 * 1024 * 1024)
            os.lseek(descriptor, artifact.size_bytes - tail_size, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = tail_size
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    raise UrbanVerifierWorkloadError(
                        "engineering event ledger changed while reading"
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, RuntimeError) as error:
        if isinstance(error, UrbanVerifierWorkloadError):
            raise
        raise UrbanVerifierWorkloadError(
            "engineering event ledger is unavailable"
        ) from error
    if (
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_dev,
        before.st_ino,
    ) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_dev,
        after.st_ino,
    ):
        raise UrbanVerifierWorkloadError(
            "engineering event ledger changed while reading"
        )
    tail = b"".join(chunks)
    if not tail or not tail.endswith(b"\n"):
        raise UrbanVerifierWorkloadError(
            "engineering event ledger is not newline terminated"
        )
    without_final_newline = tail[:-1]
    separator = without_final_newline.rfind(b"\n")
    if separator < 0 and artifact.size_bytes > len(tail):
        raise UrbanVerifierWorkloadError(
            "engineering terminal event exceeds its bounded record limit"
        )
    raw_line = without_final_newline[separator + 1 :]
    try:
        document = json.loads(raw_line.decode("utf-8"))
        terminal = LedgerRecord.model_validate(document)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise UrbanVerifierWorkloadError(
            "engineering terminal event is invalid"
        ) from error
    if canonical_json_bytes(terminal.model_dump(mode="json")) != raw_line:
        raise UrbanVerifierWorkloadError(
            "engineering terminal event is not canonical"
        )
    if (
        terminal.event_hash != seal.event_chain_root
        or terminal.event.run_id != contract.run_id
        or terminal.event.segment_kind != "runtime"
        or terminal.event.source_kind != "harness"
        or terminal.event.source != "harness"
        or terminal.event.event_type != "run.completed"
        or terminal.event.time.tick != config.final_tick
        or terminal.event.time.sim_time_ns != config.duration_ns
    ):
        raise UrbanVerifierWorkloadError(
            "engineering runtime does not end in the sealed completed horizon"
        )
    return terminal


def _engineering_verifier_record(
    *,
    contract: VerifierWorkloadContract,
    terminal_time,
    sequence: int,
    previous_digest: str,
    minimum_wall_time_ns: int,
    goal,
    report,
    report_payload: bytes,
) -> LedgerRecord:
    event_id = f"event.{sequence:016d}"
    source = contract.workload_id
    correlation_id = f"verification.{goal.goal_id}"
    wall_time_ns = max(time.time_ns(), minimum_wall_time_ns)
    payload = EventLedger._state_attributes(
        _goal_payload(goal, report, report_payload)
    )
    visibility = (RunEventAudience(scope="public", audience_id=None),)
    common = {
        "schema_version": "aero-bench.agent-interaction/v1",
        "segment_kind": "verifier",
        "interaction_type": "verifier.evidence.v1",
        "run_id": contract.run_id,
        "sequence": sequence,
        "event_id": event_id,
        "time": terminal_time,
        "wall_time_ns": wall_time_ns,
        "source_kind": "verifier",
        "source": source,
        "workload_id": source,
        "correlation_id": correlation_id,
        "parent_event_id": None,
        "causal_event_ids": (),
        "visibility": visibility,
        "frame_id": None,
        "agent_id": None,
        "provider_id": None,
        "vehicle_id": None,
        "entity_id": None,
        "command_id": None,
        "observation_id": None,
        "payload_schema_id": "verifier.evidence.v1",
        "payload": payload,
        "payload_digest": run_event_payload_digest_value(payload),
    }
    interaction_candidate = AgentInteraction.model_construct(
        **common,
        interaction_digest=RUN_EVENT_CHAIN_ROOT,
    )
    interaction = AgentInteraction(
        **common,
        interaction_digest=agent_interaction_digest_value(interaction_candidate),
    )
    event_fields = {
        "schema_version": "aero-bench.run-event/v1",
        "segment_kind": "verifier",
        "run_id": contract.run_id,
        "sequence": sequence,
        "event_id": event_id,
        "source_kind": "verifier",
        "source": source,
        "workload_id": source,
        "event_type": f"verifier.evidence.{goal.goal_id}",
        "time": terminal_time,
        "wall_time_ns": wall_time_ns,
        "correlation_id": correlation_id,
        "parent_event_id": None,
        "causal_event_ids": (),
        "visibility": visibility,
        "frame_id": None,
        "agent_id": None,
        "provider_id": None,
        "vehicle_id": None,
        "entity_id": None,
        "command_id": None,
        "observation_id": None,
        "payload_schema_id": "verifier.evidence.v1",
        "payload": payload,
        "payload_digest": common["payload_digest"],
        "interaction": interaction,
        "previous_event_digest": previous_digest,
    }
    event_candidate = RunEvent.model_construct(
        **event_fields,
        event_digest=RUN_EVENT_CHAIN_ROOT,
    )
    event = RunEvent(
        **event_fields,
        event_digest=run_event_digest_value(event_candidate),
    )
    return LedgerRecord(
        sequence=sequence,
        event=event,
        previous_hash=previous_digest,
        event_hash=event.event_digest,
    )


def _engineering_segment(
    contract: VerifierWorkloadContract,
    seal: SealManifest,
    seal_root: Path,
    config: UrbanRecoveryVerifierConfig,
    report,
    report_payload: bytes,
) -> bytes:
    # Executor validation is complete only when explicitly unscored, not when
    # engineering artifact/identity checks failed and also returned "invalid".
    if report.status != "invalid" or {
        goal.failure_class for goal in report.goals
    } != {"urban.scope.executor_validation_not_scorable"}:
        raise UrbanVerifierWorkloadError("engineering runtime evidence validation failed")
    terminal = _engineering_terminal_record(contract, seal, seal_root, config)
    records: list[LedgerRecord] = []
    previous_digest = terminal.event_hash
    minimum_wall_time_ns = terminal.event.wall_time_ns
    for offset, goal in enumerate(report.goals, start=1):
        record = _engineering_verifier_record(
            contract=contract,
            terminal_time=terminal.event.time,
            sequence=terminal.sequence + offset,
            previous_digest=previous_digest,
            minimum_wall_time_ns=minimum_wall_time_ns,
            goal=goal,
            report=report,
            report_payload=report_payload,
        )
        records.append(record)
        previous_digest = record.event_hash
        minimum_wall_time_ns = record.event.wall_time_ns
    if not records:
        raise UrbanVerifierWorkloadError("verification report contains no goals")
    return b"".join(
        canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
        for record in records
    )


def _segment(
    contract: VerifierWorkloadContract,
    seal: SealManifest,
    seal_root: Path,
    report,
    report_payload: bytes,
    *,
    config: UrbanRecoveryVerifierConfig | None = None,
) -> bytes:
    if config is not None and config.execution_profile == "engineering":
        return _engineering_segment(
            contract, seal, seal_root, config, report, report_payload
        )
    runtime = load_sealed_event_ledger(run=contract.run, seal=seal, seal_root=seal_root)
    ledger = VerifierEventLedger(runtime_records=runtime.records)
    for goal in report.goals:
        ledger.append_event(
            source=contract.workload_id,
            workload_id=contract.workload_id,
            event_type=f"verifier.evidence.{goal.goal_id}",
            time=runtime.records[-1].event.time,
            payload=_goal_payload(goal, report, report_payload),
            payload_schema_id="verifier.evidence.v1",
            interaction_type="verifier.evidence.v1",
            correlation_id=f"verification.{goal.goal_id}",
            visibility=(RunEventAudience(scope="public", audience_id=None),),
        )
    return verifier_event_segment_jsonl_bytes(
        runtime_records=runtime.records,
        verifier_records=ledger.records,
    )


def run() -> None:
    contract = _contract()
    bundle_root = _root("AERO_BENCH_BUNDLE_DIR")
    seal_root = _root("AERO_BENCH_SEAL_DIR")
    artifact_root = _root("AERO_BENCH_ARTIFACT_DIR")
    reader = BundleReader(bundle_root)
    config_document = reader.validate_schema_bound_file(contract.run.task.verifier.config)
    try:
        config = UrbanRecoveryVerifierConfig.model_validate(config_document)
    except (TypeError, ValueError) as error:
        raise UrbanVerifierWorkloadError("urban verifier configuration is invalid") from error
    seal = _seal(
        seal_root,
        contract,
        verify_artifacts=config.execution_profile != "engineering",
    )
    report = verify_urban_recovery_sealed(
        bundle_root=bundle_root,
        run=contract.run,
        seal=seal,
        sealed_root=seal_root,
        config=config,
    )
    report_requirement, segment_requirement = _output_requirements(contract)
    if any(artifact_root.iterdir()):
        raise UrbanVerifierWorkloadError("verifier output root is not empty")
    report_payload = canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
    segment_payload = _segment(
        contract,
        seal,
        seal_root,
        report,
        report_payload,
        config=config,
    )
    _write(
        artifact_root,
        report_requirement.relative_path,
        report_payload,
        report_requirement.max_size_bytes,
    )
    _write(
        artifact_root,
        segment_requirement.relative_path,
        segment_payload,
        segment_requirement.max_size_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["verify"]:
        print("usage: urban-recovery-verifier verify", file=sys.stderr)
        return 2
    try:
        run()
    except Exception as error:
        print(f"urban-recovery-verifier: FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
