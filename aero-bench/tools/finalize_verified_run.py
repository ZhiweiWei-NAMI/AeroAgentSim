#!/usr/bin/env python3
"""Recover only publication of a verified run into a fresh read-only delivery."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aero_bench.authoring.draft_compiler import load_published_compilation
from aero_bench.config.loader import sha256_file
from aero_bench.control.sealed_replay import _check_file, _load_seal, _model
from aero_bench.runner.contracts import RunSummary, RunnerConfig, RunnerSummary
from aero_bench.runner.execution import (
    _project_and_write_public_trace,
    _seal_identity,
    _verification_identity,
    load_runner_config,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier.output import load_and_validate_verification_output


def _write(path: Path, value: object) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")


def _source_revision() -> dict[str, object]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    # Include working source bytes: HEAD alone would omit an uncommitted fix.
    paths = ("aero_bench/trace/projector.py", "aero_bench/runner/execution.py",
             "tools/finalize_verified_run.py")
    return {"git_head": revision, "files": [
        {"path": relative, "sha256": sha256_file(ROOT / relative)}
        for relative in paths
    ]}


def _copy_seal(source: Path, destination: Path) -> None:
    # Independent inodes preserve the original execution even if delivery
    # files are later changed. Never share writable hardlinks or symlinks.
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def finalize_verified_run(
    *, compilation_root: Path, compilation_id: str,
    runner_config_path: Path, source_run_root: Path, output_root: Path,
    progress: Callable[[str], None] = print,
) -> RunnerSummary:
    source = source_run_root.absolute()
    output = output_root.absolute()
    if output.resolve(strict=False) != output:
        raise ValueError("publication output must be normalized without symbolic links")
    if output == source or source in output.parents:
        raise ValueError("publication output must be outside the original execution")
    if output.exists() or output.is_symlink():
        raise ValueError("publication output must be fresh")
    result, bundle, runs = load_published_compilation(compilation_root, compilation_id)
    if len(runs) != 1:
        raise ValueError("publication recovery requires a single-run compilation")
    run = runs[0]
    if output == bundle or bundle in output.parents:
        raise ValueError("publication output must be outside the immutable bundle")
    config = load_runner_config(runner_config_path)
    config_digest = sha256_file(runner_config_path)
    replay_config = RunnerConfig.model_validate({
        **config.model_dump(mode="json"), "output_root": str(output),
    })
    original = _model(source / "run-summary.json", RunSummary)
    if (
        source.name != run.run_id
        or original.run_id != run.run_id
        or original.executor_kind != run.executor_kind
        or config.executor_kind != run.executor_kind
        or original.execution_scope != "formal_benchmark"
        or run.execution_scope != "formal_benchmark"
        or original.status != "error"
        or original.failure_classes != ("public_trace_projection_failed",)
        or original.public_trace is not None
        or not original.preflight.ready or original.preflight.blocker_codes
        or original.seal is None or original.verification is None
        or original.verification.status != "passed"
    ):
        raise ValueError("source is not a passed formal run with only failed publication")
    runtime = _load_seal(source / "runtime-seal", "seal-manifest.json", run.artifact_requirements)
    verification = _load_seal(source / "verification", "verification-manifest.json", run.verification_outputs)
    if _seal_identity(runtime) != original.seal:
        raise ValueError("runtime seal differs from the original failure summary")
    validated = load_and_validate_verification_output(
        run=run, runtime_seal=runtime, output_seal=verification,
        output_root=source / "verification", runtime_root=source / "runtime-seal",
    )
    if _verification_identity(validated) != original.verification or validated.report.status != "passed":
        raise ValueError("sealed verification differs from the original passed verdict")
    original_bytes = _check_file(source / "run-summary.json", limit=1_048_576, read=True)
    if original_bytes != canonical_json_bytes(original.model_dump(mode="json")) + b"\n":
        raise ValueError("original failure summary changed during validation")
    diagnostics = [
        {"path": str(path), "sha256": hashlib.sha256(
            _check_file(path, limit=1_048_576, read=True)
        ).hexdigest()}
        for path in sorted(source.glob("failure-diagnostic*.json"))
    ]
    if not diagnostics:
        raise ValueError("source publication failure has no diagnostic receipt")
    if result.suite is None:
        raise ValueError("compilation has no suite identity")
    source_revision = _source_revision()
    progress("inputs verified")

    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    destination = output / run.run_id
    destination.mkdir(mode=0o700)
    (destination / "original-run-summary.json").write_bytes(original_bytes)
    _copy_seal(source / "runtime-seal", destination / "runtime-seal")
    _copy_seal(source / "verification", destination / "verification")
    progress("sealed copies complete; projection begins")
    started = time.time_ns()
    # No executor, Provider or verifier process is constructed by recovery.
    identity = _project_and_write_public_trace(
        run=run, seal=runtime, validated=validated, bundle_root=bundle,
        run_root=destination, seal_root=destination / "runtime-seal", progress=progress,
    )
    progress("projection complete")
    recovered = RunSummary(
        run_id=run.run_id, executor_kind=run.executor_kind,
        execution_scope=run.execution_scope, preflight=original.preflight,
        status=validated.report.status, seal=original.seal,
        verification=original.verification, public_trace=identity, failure_classes=(),
    )
    config_path = output / "replay-runner.json"
    _write(config_path, replay_config.model_dump(mode="json"))
    runner = RunnerSummary(
        schema_version="aero-bench.runner-summary/v1", executor_kind=config.executor_kind,
        suite_sha256=result.suite.sha256, runner_config_sha256=sha256_file(config_path),
        run_count=1, execution_complete_count=1, passed_count=1, runs=(recovered,),
    )
    _write(destination / "run-summary.json", recovered.model_dump(mode="json"))
    _write(output / "runner-summary.json", runner.model_dump(mode="json"))
    report_artifact = next(item for item in verification.artifacts if item.artifact_type == "verification.report")
    _write(destination / "finalization-recovery.json", {
        "schema_version": "aero-bench.publication-recovery/v1", "status": "completed",
        "run_id": run.run_id, "simulation_rerun": False, "verifier_rerun": False,
        "started_wall_time_ns": started, "completed_wall_time_ns": time.time_ns(),
        "compilation_id": compilation_id, "bundle_root": str(bundle),
        "source_run_root": str(source),
        "original_summary_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "original_failure_classes": list(original.failure_classes),
        "original_diagnostics": diagnostics,
        "source_runner_config": {"path": str(runner_config_path.absolute()), "sha256": config_digest},
        "runtime_seal": original.seal.model_dump(mode="json"),
        "verification_manifest_digest": verification.manifest_digest,
        "verification_report": report_artifact.model_dump(mode="json"),
        "source_revision": source_revision, "public_trace": identity.model_dump(mode="json"),
        "recovered_summary_sha256": sha256_file(destination / "run-summary.json"),
        "serving_runner_config_sha256": sha256_file(config_path),
    })
    progress("summaries and recovery receipt written")
    return runner


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compilation-root", type=Path, required=True)
    parser.add_argument("--compilation-id", required=True)
    parser.add_argument("--runner-config", type=Path, required=True)
    parser.add_argument("--source-run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    runner = finalize_verified_run(
        compilation_root=args.compilation_root, compilation_id=args.compilation_id,
        runner_config_path=args.runner_config, source_run_root=args.source_run_root,
        output_root=args.output_root,
        progress=lambda stage: print(f"PUBLICATION_STAGE={stage}", flush=True),
    )
    print(f"RECOVERED_RUN_ID={runner.runs[0].run_id}", flush=True)
    print(f"SERVING_RUNNER_CONFIG={args.output_root.absolute() / 'replay-runner.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
