#!/usr/bin/env python3
"""Audit an existing sealed Huangpu native-city execution.

The audit resolves the registered Suite and RunnerConfig, checks the exact
runtime and verification seals, validates the existing independent-verifier
report against the ResolvedRun, and closes the public replay inventory. It is
read-only and does not execute a Provider or Verifier or create a task verdict.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.artifacts.contracts import SealManifest  # noqa: E402
from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeRegistrationError,
    assess_city_native_registration,
    load_city_native_input_lock,
)
from aero_bench.config.loader import BundleReader, load_suite, sha256_file  # noqa: E402
from aero_bench.config.resolver import resolve_suite  # noqa: E402
from aero_bench.providers.registry import builtin_provider_registry  # noqa: E402
from aero_bench.providers.rpc import parse_json_object  # noqa: E402
from aero_bench.runner.contracts import RunnerSummary  # noqa: E402
from aero_bench.runner.execution import load_runner_config  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.tasks.registry import builtin_task_package_resolvers  # noqa: E402
from aero_bench.verifier.contracts import (  # noqa: E402
    VerificationReport,
    validate_report_against_run,
)
from tools.audit_public_replay import audit_public_replay  # noqa: E402


REPORT_SCHEMA_VERSION = "aero-bench.huangpu-native-execution-audit/v1"


class HuangpuNativeExecutionAuditError(ValueError):
    """An existing city execution does not close over its declared evidence."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="Repository root used to resolve the registered files.",
    )
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def _closed_file(root: Path, relative_path: str, *, label: str) -> Path:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or relative == PurePosixPath(".")
        or ".." in relative.parts
        or str(relative) != relative_path
        or "\\" in relative_path
    ):
        raise HuangpuNativeExecutionAuditError(
            f"{label} path must be normalized and relative: {relative_path}"
        )
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise HuangpuNativeExecutionAuditError(
            f"{label} cannot use a symbolic link: {relative_path}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise HuangpuNativeExecutionAuditError(
            f"{label} is unavailable: {relative_path}"
        ) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise HuangpuNativeExecutionAuditError(
            f"{label} is not a regular file inside its root: {relative_path}"
        )
    return resolved


def _canonical_model(path: Path, model: Any, *, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise HuangpuNativeExecutionAuditError(f"{label} is not a regular file")
    try:
        payload = path.read_bytes()
        document = parse_json_object(payload)
        validated = model.model_validate(document)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HuangpuNativeExecutionAuditError(f"{label} is invalid") from exc
    expected = canonical_json_bytes(validated.model_dump(mode="json")) + b"\n"
    if payload != expected:
        raise HuangpuNativeExecutionAuditError(f"{label} is not canonical JSON")
    return validated


def _audit_seal_root(
    root: Path,
    *,
    manifest_name: str,
    label: str,
) -> tuple[SealManifest, dict[str, int]]:
    if root.is_symlink():
        raise HuangpuNativeExecutionAuditError(f"{label} root cannot be a symlink")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise HuangpuNativeExecutionAuditError(f"{label} root is unavailable") from exc
    if not resolved_root.is_dir():
        raise HuangpuNativeExecutionAuditError(f"{label} root is not a directory")
    manifest_path = _closed_file(
        resolved_root,
        manifest_name,
        label=f"{label} manifest",
    )
    manifest = _canonical_model(manifest_path, SealManifest, label=f"{label} manifest")

    declared = {manifest_name}
    total_size_bytes = 0
    for artifact in manifest.artifacts:
        if artifact.relative_path in declared:
            raise HuangpuNativeExecutionAuditError(
                f"{label} repeats a sealed path: {artifact.relative_path}"
            )
        path = _closed_file(
            resolved_root,
            artifact.relative_path,
            label=f"{label} artifact",
        )
        if (
            path.stat().st_size != artifact.size_bytes
            or sha256_file(path) != artifact.sha256
        ):
            raise HuangpuNativeExecutionAuditError(
                f"{label} artifact bytes differ: {artifact.relative_path}"
            )
        declared.add(artifact.relative_path)
        total_size_bytes += artifact.size_bytes

    actual: set[str] = set()
    for path in resolved_root.rglob("*"):
        relative = path.relative_to(resolved_root).as_posix()
        if path.is_symlink():
            raise HuangpuNativeExecutionAuditError(
                f"{label} contains a symbolic link: {relative}"
            )
        if path.is_file():
            actual.add(relative)
        elif not path.is_dir():
            raise HuangpuNativeExecutionAuditError(
                f"{label} contains a special file: {relative}"
            )
    if actual != declared:
        raise HuangpuNativeExecutionAuditError(
            f"{label} inventory is not closed: "
            f"missing={sorted(declared - actual)}, "
            f"undeclared={sorted(actual - declared)}"
        )
    return manifest, {
        "artifact_count": len(manifest.artifacts),
        "artifact_size_bytes": total_size_bytes,
    }


def _write_new(path: Path, document: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(document) + b"\n"
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise HuangpuNativeExecutionAuditError(
            f"audit report must be fresh; refusing to overwrite: {path}"
        ) from None
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise


def build_report(*, root: Path, input_lock_path: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    definition = load_city_native_input_lock(input_lock_path)
    if definition.execution is None:
        raise HuangpuNativeExecutionAuditError(
            "city input lock has no execution binding"
        )
    readiness = assess_city_native_registration(root, definition)
    if readiness.run_id is None:
        raise HuangpuNativeExecutionAuditError(
            "city execution binding did not resolve a Run ID"
        )

    reader = BundleReader(root)
    suite_path = reader.resolve_file(definition.execution.suite.file)
    runner_path = reader.resolve_file(definition.execution.runner_config.file)
    loaded_suite = load_suite(suite_path)
    runs = resolve_suite(
        str(suite_path),
        executor_kind="docker_reference",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    if len(runs) != 1 or runs[0].run_id != readiness.run_id:
        raise HuangpuNativeExecutionAuditError(
            "registered Suite does not resolve the assessed city Run ID"
        )
    run = runs[0]
    runner = load_runner_config(runner_path)
    output_root = Path(runner.output_root)
    if output_root.is_symlink():
        raise HuangpuNativeExecutionAuditError("runner output root cannot be a symlink")
    try:
        output_root = output_root.resolve(strict=True)
    except OSError as exc:
        raise HuangpuNativeExecutionAuditError(
            "runner output root is unavailable"
        ) from exc
    if not output_root.is_dir():
        raise HuangpuNativeExecutionAuditError("runner output root is not a directory")

    runner_summary = _canonical_model(
        output_root / "runner-summary.json",
        RunnerSummary,
        label="Runner summary",
    )
    if (
        runner_summary.executor_kind != "docker_reference"
        or runner_summary.suite_sha256 != sha256_file(suite_path)
        or runner_summary.runner_config_sha256 != sha256_file(runner_path)
        or runner_summary.run_count != 1
        or runner_summary.execution_complete_count != 1
        or runner_summary.passed_count != 1
    ):
        raise HuangpuNativeExecutionAuditError(
            "Runner summary does not identify one passed registered execution"
        )
    summary = runner_summary.runs[0]
    if (
        summary.run_id != run.run_id
        or summary.execution_scope != "formal_benchmark"
        or not summary.preflight.ready
        or summary.preflight.blocker_codes
        or summary.status != "passed"
        or summary.failure_classes
        or summary.seal is None
        or summary.verification is None
        or summary.verification.status != "passed"
        or summary.public_trace is None
    ):
        raise HuangpuNativeExecutionAuditError(
            "registered city execution is not a clean formal pass"
        )

    run_root = output_root / run.run_id
    runtime_seal, runtime_inventory = _audit_seal_root(
        run_root / "runtime-seal",
        manifest_name="seal-manifest.json",
        label="runtime seal",
    )
    runtime_identity = summary.seal
    runtime_artifacts = tuple(
        (item.artifact_id, item.sha256, item.size_bytes)
        for item in runtime_seal.artifacts
    )
    summary_artifacts = tuple(
        (item.artifact_id, item.sha256, item.size_bytes)
        for item in runtime_identity.artifacts
    )
    if (
        runtime_seal.run_id != run.run_id
        or runtime_seal.execution_scope != run.execution_scope
        or runtime_seal.attempt_id != runtime_identity.attempt_id
        or runtime_seal.manifest_digest != runtime_identity.manifest_digest
        or runtime_seal.event_chain_root != runtime_identity.event_chain_root
        or runtime_artifacts != summary_artifacts
    ):
        raise HuangpuNativeExecutionAuditError(
            "runtime seal differs from the passed run identity"
        )

    verification_seal, verification_inventory = _audit_seal_root(
        run_root / "verification",
        manifest_name="verification-manifest.json",
        label="verification seal",
    )
    verification_identity = summary.verification
    if (
        verification_seal.run_id != run.run_id
        or verification_seal.execution_scope != run.execution_scope
        or verification_seal.attempt_id != runtime_seal.attempt_id
        or verification_seal.event_chain_root != runtime_seal.event_chain_root
        or verification_seal.manifest_digest
        != verification_identity.output_manifest_digest
    ):
        raise HuangpuNativeExecutionAuditError(
            "verification seal differs from the passed run identity"
        )
    report_records = [
        item
        for item in verification_seal.artifacts
        if item.artifact_type == "verification.report"
    ]
    if len(report_records) != 1:
        raise HuangpuNativeExecutionAuditError(
            "verification seal must contain one verification report"
        )
    verification_report = _canonical_model(
        _closed_file(
            run_root / "verification",
            report_records[0].relative_path,
            label="verification report",
        ),
        VerificationReport,
        label="verification report",
    )
    validate_report_against_run(verification_report, run)
    if (
        verification_report.status != "passed"
        or not verification_report.coverage_complete
        or tuple(item.goal_id for item in verification_report.goals)
        != verification_identity.goal_ids
    ):
        raise HuangpuNativeExecutionAuditError(
            "independent-verifier report is not a complete passed result"
        )

    public_identity = summary.public_trace
    public_trace = _closed_file(
        run_root,
        public_identity.relative_path,
        label="public trace",
    )
    if sha256_file(public_trace) != public_identity.sha256:
        raise HuangpuNativeExecutionAuditError(
            "public trace differs from the passed run identity"
        )
    replay_manifest = _closed_file(
        run_root,
        public_identity.replay.relative_path,
        label="public replay manifest",
    )
    if sha256_file(replay_manifest) != public_identity.replay.sha256:
        raise HuangpuNativeExecutionAuditError(
            "public replay manifest differs from the passed run identity"
        )
    replay_audit = audit_public_replay(replay_manifest.parent)
    if (
        replay_audit["run_id"] != run.run_id
        or replay_audit["scenario_digest"] != run.scenario.scenario_digest
        or replay_audit["event_chain_root"] != runtime_seal.event_chain_root
        or replay_audit["trace_sha256"] != public_identity.sha256
        or replay_audit["manifest_sha256"] != public_identity.replay.sha256
        or replay_audit["file_count"] != public_identity.replay.file_count
        or replay_audit["total_size_bytes"] != public_identity.replay.total_size_bytes
        or replay_audit["trace_phase"] != "verified"
        or replay_audit["public_verdict"] != "passed"
    ):
        raise HuangpuNativeExecutionAuditError(
            "public replay does not close over the passed city execution"
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "checked-existing-passed-execution",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "world_id": readiness.world_id,
        "world_digest": readiness.world_digest,
        "registration_sha256": readiness.registration_sha256,
        "suite_sha256": runner_summary.suite_sha256,
        "runner_config_sha256": runner_summary.runner_config_sha256,
        "runtime_seal": {
            "manifest_digest": runtime_seal.manifest_digest,
            "event_chain_root": runtime_seal.event_chain_root,
            **runtime_inventory,
        },
        "independent_verifier": {
            "status": verification_report.status,
            "coverage_complete": verification_report.coverage_complete,
            "goal_count": len(verification_report.goals),
            "output_manifest_digest": verification_seal.manifest_digest,
            **verification_inventory,
        },
        "public_replay": replay_audit,
        "benchmark_verifier_executed_by_audit": False,
        "task_verdict_produced_by_audit": False,
        "audit_scope": (
            "Read-only contract, digest, closed-inventory, verifier-report, and "
            "public-replay validation of an existing execution."
        ),
        "suite_root": loaded_suite.root.relative_to(root).as_posix(),
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build_report(root=args.root, input_lock_path=args.input_lock)
        output_root = Path(
            load_runner_config(
                BundleReader(args.root.resolve(strict=True)).resolve_file(
                    load_city_native_input_lock(
                        args.input_lock
                    ).execution.runner_config.file
                )
            ).output_root
        ).resolve()
        if args.report.resolve().is_relative_to(output_root):
            raise HuangpuNativeExecutionAuditError(
                "audit report must stay outside the formal execution root"
            )
        _write_new(args.report, report)
    except (
        CityNativeRegistrationError,
        HuangpuNativeExecutionAuditError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"city execution audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
