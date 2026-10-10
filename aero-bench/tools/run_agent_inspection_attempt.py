#!/usr/bin/env python3
"""Run one sealed formal Agent inspection attempt without overwriting history."""

from __future__ import annotations


import argparse
import hashlib
import os
import re
import secrets
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aero_bench.providers.registry import builtin_provider_registry

# ruff: noqa: E402
from aero_bench.config import BundleReader, load_suite, resolve_suite
from aero_bench.config.models import FileRef
from aero_bench.runner import RunExecutionSession, RunnerConfig, load_runner_config
from aero_bench.runner.execution import build_docker_executor
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers


ATTEMPT_SCHEMA = "aero-bench.agent-inspection-attempt/v1"
SOURCE_SNAPSHOT_SCHEMA = "aero-bench.source-spec-snapshot/v1"
WRAPPER_FAILURE_SCHEMA = "aero-bench.attempt-wrapper-failure/v1"
_ATTEMPT_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")


class AttemptRunnerError(RuntimeError):
    """Stable failure at the formal attempt wrapper boundary."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_payload(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_json_exclusive(path: Path, value: object) -> str:
    payload = _canonical_payload(value)
    _write_exclusive(path, payload)
    return _sha256(payload)


def _replace_json(path: Path, value: object) -> None:
    """Best-effort atomic replacement for wrapper-owned status sidecars."""

    payload = _canonical_payload(value)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _model_payload(value: object) -> dict[str, object]:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        raise AttemptRunnerError("attempt input is not a serializable strict model")
    payload = model_dump(mode="json")
    if not isinstance(payload, dict):
        raise AttemptRunnerError("attempt model payload is not an object")
    return payload


def _new_attempt_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S%fZ").lower()
    return f"attempt.{timestamp}.{secrets.token_hex(8)}"


def _validated_attempt_id(value: str) -> str:
    if len(value) > 128 or _ATTEMPT_ID.fullmatch(value) is None:
        raise AttemptRunnerError("attempt_id must be a declared identifier")
    return value


def _resolve_existing_file(value: str | Path, *, label: str) -> Path:
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        raise AttemptRunnerError(f"{label} is unavailable") from None
    if not path.is_file():
        raise AttemptRunnerError(f"{label} must name a regular file")
    return path


def _resolve_output_root(value: str | Path) -> Path:
    try:
        root = Path(value).resolve(strict=False)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    except (OSError, RuntimeError):
        raise AttemptRunnerError("output root could not be prepared") from None
    if not root.is_dir():
        raise AttemptRunnerError("output root must be a directory")
    return root


def _case_for_run(loaded_suite: object, run: object) -> object:
    suite = getattr(loaded_suite, "suite", None)
    cases = getattr(suite, "cases", ())
    matches = tuple(case for case in cases if case.case_id == run.case_id)
    if len(matches) != 1:
        raise AttemptRunnerError("resolved run does not name one source suite case")
    return matches[0]


def _source_spec_references(
    *,
    loaded_suite: object,
    run: object,
    runner_config_path: Path,
) -> tuple[tuple[str, FileRef], ...]:
    case = _case_for_run(loaded_suite, run)
    suite_path = loaded_suite.suite_path
    suite_ref = FileRef(
        path=suite_path.relative_to(loaded_suite.root).as_posix(),
        sha256=loaded_suite.suite_digest,
    )
    config_ref = FileRef(
        path=runner_config_path.relative_to(loaded_suite.root).as_posix(),
        sha256=_sha256(runner_config_path.read_bytes()),
    )
    references: list[tuple[str, FileRef]] = [
        ("suite", suite_ref),
        ("runner_config", config_ref),
        ("task", case.task),
        ("environment", case.environment),
        ("world_package", case.world_package),
    ]
    references.extend(
        (f"agent.{index}", reference)
        for index, reference in enumerate(case.agents)
    )
    for axis in case.axes:
        references.extend(
            (f"matrix.{axis.axis_id}.{index}", value)
            for index, value in enumerate(axis.values)
            if isinstance(value, FileRef)
        )
    instruction = run.task.instruction
    references.append(("task_instruction", instruction))
    return tuple(references)


def _write_source_snapshot(
    *,
    attempt_root: Path,
    loaded_suite: object,
    run: object,
    runner_config_path: Path,
) -> str:
    reader = BundleReader(loaded_suite.root)
    by_path: dict[str, dict[str, object]] = {}
    for role, reference in _source_spec_references(
        loaded_suite=loaded_suite,
        run=run,
        runner_config_path=runner_config_path,
    ):
        source = reader.resolve_file(reference)
        existing = by_path.get(reference.path)
        if existing is not None:
            if existing["sha256"] != reference.sha256:
                raise AttemptRunnerError(
                    "source spec path is referenced with conflicting digests"
                )
            cast_roles = existing["roles"]
            if not isinstance(cast_roles, list):
                raise AssertionError("source snapshot roles are not a list")
            cast_roles.append(role)
            continue
        payload = source.read_bytes()
        if _sha256(payload) != reference.sha256:
            raise AttemptRunnerError("source spec changed after strict resolution")
        destination = attempt_root / "source-specs" / "files" / reference.path
        _write_exclusive(destination, payload)
        by_path[reference.path] = {
            "relative_path": reference.path,
            "sha256": reference.sha256,
            "size_bytes": len(payload),
            "snapshot_relative_path": destination.relative_to(attempt_root).as_posix(),
            "roles": [role],
        }
    files = []
    for path in sorted(by_path):
        record = by_path[path]
        record["roles"] = sorted(set(record["roles"]))
        files.append(record)
    manifest = {
        "schema_version": SOURCE_SNAPSHOT_SCHEMA,
        "run_id": run.run_id,
        "case_id": run.case_id,
        "files": files,
    }
    return _write_json_exclusive(
        attempt_root / "source-specs" / "manifest.json", manifest
    )


def _effective_runner_config(
    *,
    base_config: object,
    staging_root: Path,
    attempt_id: str,
    model_auth_file: Path,
    model_https_proxy: str,
    timeout_seconds: int,
) -> object:
    payload = _model_payload(base_config)
    payload.update(
        {
            "output_root": str(staging_root),
            "runtime_timeout_seconds": timeout_seconds,
            "verifier_timeout_seconds": timeout_seconds,
            "attempt_id": attempt_id,
            "model_auth_file": str(model_auth_file),
            "model_https_proxy": model_https_proxy,
        }
    )
    try:
        return RunnerConfig.model_validate(payload)
    except Exception as error:
        raise AttemptRunnerError(
            "effective RunnerConfig is invalid for a formal Agent attempt"
        ) from error


def _promote_session_outputs(*, session_run_root: Path, attempt_root: Path) -> None:
    if not session_run_root.is_dir():
        raise AttemptRunnerError("RunExecutionSession produced no run directory")
    for source in sorted(session_run_root.iterdir(), key=lambda item: item.name):
        destination = attempt_root / source.name
        if destination.exists() or destination.is_symlink():
            raise AttemptRunnerError(
                f"session output conflicts with attempt input: {source.name}"
            )
        source.rename(destination)
    session_run_root.rmdir()
    staging_root = session_run_root.parent
    if not any(staging_root.iterdir()):
        staging_root.rmdir()


def _bounded_failure(error: BaseException) -> tuple[str, str]:
    kind = type(error).__name__[:128] or "BaseException"
    detail = str(error)
    if len(detail) > 4096:
        detail = detail[-4096:]
    return kind, detail


def _attempt_summary(
    *,
    run: object,
    attempt_id: str,
    started_at: str,
    completed_at: str,
    status: str,
    source_snapshot_sha256: str | None,
    task_definition_sha256: str | None,
    resolved_run_sha256: str | None,
    runner_config_sha256: str | None,
    run_summary_sha256: str | None,
    failure_type: str | None = None,
    failure: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": ATTEMPT_SCHEMA,
        "run_id": run.run_id,
        "attempt_id": attempt_id,
        "execution_scope": run.execution_scope,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "source_snapshot_sha256": source_snapshot_sha256,
        "task_definition_sha256": task_definition_sha256,
        "resolved_run_sha256": resolved_run_sha256,
        "runner_config_sha256": runner_config_sha256,
        "run_summary_sha256": run_summary_sha256,
        "failure_type": failure_type,
        "failure": failure,
    }


def _run_one_attempt(
    *,
    loaded_suite: object,
    run: object,
    base_config: object,
    runner_config_path: Path,
    output_root: Path,
    attempt_id: str,
    model_auth_file: Path,
    model_https_proxy: str,
    timeout_seconds: int,
) -> tuple[object, Path]:
    if run.execution_scope != "formal_benchmark":
        raise AttemptRunnerError(
            "Agent inspection attempts require formal_benchmark execution scope"
        )
    attempt_root = output_root / run.run_id / attempt_id
    try:
        attempt_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        raise AttemptRunnerError(
            f"attempt already exists for run {run.run_id}: {attempt_id}"
        ) from None
    except OSError:
        raise AttemptRunnerError("attempt directory could not be created") from None

    started_at = datetime.now(timezone.utc).isoformat()
    source_digest: str | None = None
    task_definition_digest: str | None = None
    resolved_digest: str | None = None
    runner_digest: str | None = None
    run_summary_digest: str | None = None
    staging_root = attempt_root / ".staging"
    try:
        staging_root.mkdir(mode=0o700)
        task_definition = {
            "schema_version": "aero-bench.attempt-task-definition/v1",
            "run_id": run.run_id,
            "task": _model_payload(run.task),
        }
        task_definition_digest = _write_json_exclusive(
            attempt_root / "task-definition.json", task_definition
        )
        resolved_digest = _write_json_exclusive(
            attempt_root / "resolved-run.json", _model_payload(run)
        )
        source_digest = _write_source_snapshot(
            attempt_root=attempt_root,
            loaded_suite=loaded_suite,
            run=run,
            runner_config_path=runner_config_path,
        )
        config = _effective_runner_config(
            base_config=base_config,
            staging_root=staging_root,
            attempt_id=attempt_id,
            model_auth_file=model_auth_file,
            model_https_proxy=model_https_proxy,
            timeout_seconds=timeout_seconds,
        )
        runner_digest = _write_json_exclusive(
            attempt_root / "runner-config.json", _model_payload(config)
        )
        executor = build_docker_executor(config, provider_registry=builtin_provider_registry())
        summary = RunExecutionSession(
            executor=executor,
            run=run,
            bundle_root=loaded_suite.root,
            output_root=staging_root,
            runtime_timeout_seconds=config.runtime_timeout_seconds,
            verifier_timeout_seconds=config.verifier_timeout_seconds,
        ).execute()
        _promote_session_outputs(
            session_run_root=staging_root / run.run_id,
            attempt_root=attempt_root,
        )
        run_summary_digest = _write_json_exclusive(
            attempt_root / "run-summary.json", _model_payload(summary)
        )
        _write_json_exclusive(
            attempt_root / "attempt-summary.json",
            _attempt_summary(
                run=run,
                attempt_id=attempt_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                status=summary.status,
                source_snapshot_sha256=source_digest,
                task_definition_sha256=task_definition_digest,
                resolved_run_sha256=resolved_digest,
                runner_config_sha256=runner_digest,
                run_summary_sha256=run_summary_digest,
            ),
        )
        return summary, attempt_root
    except BaseException as error:
        failure_type, failure = _bounded_failure(error)
        failure_payload = {
            "schema_version": WRAPPER_FAILURE_SCHEMA,
            "run_id": run.run_id,
            "attempt_id": attempt_id,
            "failure_type": failure_type,
            "failure": failure,
        }
        try:
            _write_json_exclusive(
                attempt_root / "wrapper-failure.json", failure_payload
            )
        except Exception:
            pass
        try:
            _replace_json(
                attempt_root / "attempt-summary.json",
                _attempt_summary(
                    run=run,
                    attempt_id=attempt_id,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    status=(
                        "interrupted"
                        if isinstance(error, (KeyboardInterrupt, SystemExit))
                        else "error"
                    ),
                    source_snapshot_sha256=source_digest,
                    task_definition_sha256=task_definition_digest,
                    resolved_run_sha256=resolved_digest,
                    runner_config_sha256=runner_digest,
                    run_summary_sha256=run_summary_digest,
                    failure_type=failure_type,
                    failure=failure,
                ),
            )
        except Exception:
            pass
        raise


def execute_attempts(
    *,
    suite_path: str | Path,
    output_root: str | Path,
    model_auth_file: str | Path,
    model_https_proxy: str,
    timeout_seconds: int = 22_800,
    attempt_id: str | None = None,
) -> tuple[tuple[object, Path], ...]:
    if timeout_seconds <= 0:
        raise AttemptRunnerError("timeout_seconds must be positive")
    suite_file = _resolve_existing_file(suite_path, label="suite")
    auth_file = _resolve_existing_file(model_auth_file, label="model auth file")
    destination_root = _resolve_output_root(output_root)
    runner_config_path = _resolve_existing_file(
        suite_file.parent / "runner.local.yaml",
        label="adjacent runner.local.yaml",
    )
    try:
        loaded_suite = load_suite(suite_file)
        runs = resolve_suite(
            str(suite_file),
            executor_kind="docker_reference",
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=builtin_provider_registry(),
        )
        base_config = load_runner_config(runner_config_path)
    except Exception as error:
        raise AttemptRunnerError(
            "suite, RunnerConfig, or ResolvedRun preparation failed"
        ) from error
    if not runs:
        raise AttemptRunnerError("suite resolved to no runs")
    selected_attempt_id = _validated_attempt_id(attempt_id or _new_attempt_id())
    return tuple(
        _run_one_attempt(
            loaded_suite=loaded_suite,
            run=run,
            base_config=base_config,
            runner_config_path=runner_config_path,
            output_root=destination_root,
            attempt_id=selected_attempt_id,
            model_auth_file=auth_file,
            model_https_proxy=model_https_proxy,
            timeout_seconds=timeout_seconds,
        )
        for run in runs
    )


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute a formal Agent inspection through the canonical Docker "
            "runner and retain one immutable attempt directory."
        )
    )
    parser.add_argument("--suite", required=True, help="Formal suite.yaml path")
    parser.add_argument(
        "--output-root",
        required=True,
        help="Persistent attempt root, normally validation/agent-inspection",
    )
    parser.add_argument(
        "--model-auth-file",
        required=True,
        help="Host auth file copied privately to the Driver input; excluded from model input and evidence",
    )
    parser.add_argument(
        "--model-https-proxy",
        required=True,
        help="Credential-free HTTPS proxy URL provided to the Agent driver",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_integer,
        default=22_800,
        help="Runtime and independent verifier timeout (default: 22800)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        results = execute_attempts(
            suite_path=args.suite,
            output_root=args.output_root,
            model_auth_file=args.model_auth_file,
            model_https_proxy=args.model_https_proxy,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as error:
        print(f"agent inspection attempt failed: {error}", file=sys.stderr)
        return 1
    for summary, attempt_root in results:
        print(
            f"{summary.run_id} {summary.status} {attempt_root}",
            flush=True,
        )
    return 0 if all(summary.status == "passed" for summary, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
