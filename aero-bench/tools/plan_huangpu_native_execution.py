#!/usr/bin/env python3
"""Materialize and preflight the registered Huangpu inspection run.

This command resolves the exact Suite registered by the city input lock, builds
the Docker Reference Executor plan, and performs the executor's read-only
preflight.  It does not create volumes, networks, or containers and does not
start the runtime or verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeRegistrationError,
    assess_city_native_registration,
    load_city_native_input_lock,
)
from aero_bench.config.loader import BundleReader, load_suite, sha256_file  # noqa: E402
from aero_bench.config.resolver import resolve_suite  # noqa: E402
from aero_bench.executor.contracts import WorkloadPlan  # noqa: E402
from aero_bench.executor.docker import DockerExecutorError  # noqa: E402
from aero_bench.providers.registry import builtin_provider_registry  # noqa: E402
from aero_bench.runner.execution import (  # noqa: E402
    RunnerError,
    build_docker_executor,
    load_runner_config,
)
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.tasks.registry import builtin_task_package_resolvers  # noqa: E402


REPORT_SCHEMA_VERSION = "aero-bench.huangpu-native-execution-plan/v1"


class HuangpuNativeExecutionPlanError(ValueError):
    """The registered city inspection run cannot be materialized exactly."""


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


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(document) + b"\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _workload_summary(workload: WorkloadPlan) -> dict[str, object]:
    bundle_inputs = workload.bundle_inputs
    derived_inputs = workload.derived_inputs
    contract_bytes = len(workload.contract.content_utf8.encode("utf-8"))
    return {
        "workload_id": workload.workload_id,
        "role": workload.role,
        "phase": workload.phase,
        "image": workload.workload.runtime.image,
        "contract_sha256": workload.contract.sha256,
        "contract_size_bytes": contract_bytes,
        "bundle_input_count": len(bundle_inputs),
        "bundle_input_size_bytes": sum(item.size_bytes for item in bundle_inputs),
        "derived_input_count": len(derived_inputs),
        "derived_input_size_bytes": sum(
            len(item.content_utf8.encode("utf-8")) for item in derived_inputs
        ),
    }


def build_report(
    *,
    root: Path,
    input_lock_path: Path,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    definition = load_city_native_input_lock(input_lock_path)
    if definition.execution is None:
        raise HuangpuNativeExecutionPlanError(
            "city input lock has no execution binding"
        )

    readiness = assess_city_native_registration(root, definition)
    if readiness.run_id is None:
        raise HuangpuNativeExecutionPlanError(
            "city execution binding did not resolve a Run ID"
        )

    reader = BundleReader(root)
    suite_path = reader.resolve_file(definition.execution.suite.file)
    runner_path = reader.resolve_file(definition.execution.runner_config.file)
    loaded_suite = load_suite(suite_path)
    runner = load_runner_config(runner_path)
    registry = builtin_provider_registry()
    runs = resolve_suite(
        str(suite_path),
        executor_kind=runner.executor_kind,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=registry,
    )
    if len(runs) != 1:
        raise HuangpuNativeExecutionPlanError(
            "registered city Suite must resolve exactly one run"
        )
    run = runs[0]
    if run.run_id != readiness.run_id:
        raise HuangpuNativeExecutionPlanError(
            "materialized Run ID differs from the registered city Run ID"
        )

    executor = build_docker_executor(runner, provider_registry=registry)
    plan = executor.materialize(run, bundle_root=loaded_suite.root)
    preflight = executor.preflight(plan)
    workloads = (*plan.runtime_workloads, plan.verifier_workload)
    plan_payload = canonical_json_bytes(plan.model_dump(mode="json"))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": (
            "preflight-ready-not-executed"
            if preflight.ready
            else "preflight-blocked-not-executed"
        ),
        "registration_id": definition.registration_id,
        "registration_sha256": readiness.registration_sha256,
        "registration_status": readiness.status,
        "registration_blocking_codes": list(readiness.blocking_codes),
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "execution_scope": run.execution_scope,
        "executor_kind": plan.executor_kind,
        "execution_plan_sha256": hashlib.sha256(plan_payload).hexdigest(),
        "suite": {
            "path": definition.execution.suite.file.path,
            "sha256": sha256_file(suite_path),
            "size_bytes": suite_path.stat().st_size,
        },
        "runner_config": {
            "path": definition.execution.runner_config.file.path,
            "sha256": sha256_file(runner_path),
            "size_bytes": runner_path.stat().st_size,
        },
        "runtime_workload_count": len(plan.runtime_workloads),
        "workloads": [_workload_summary(item) for item in workloads],
        "preflight": preflight.model_dump(mode="json"),
        "formal_execution_started": False,
        "side_effect_boundary": (
            "Suite resolution, execution-plan materialization, Docker daemon and "
            "image metadata inspection only; no Docker object was created or started."
        ),
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build_report(
            root=args.root,
            input_lock_path=args.input_lock,
        )
        _write_atomic(args.report, report)
    except (
        CityNativeRegistrationError,
        DockerExecutorError,
        HuangpuNativeExecutionPlanError,
        OSError,
        RunnerError,
        ValueError,
    ) as exc:
        print(f"city execution planning failed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "preflight_ready": report["preflight"]["ready"],
                "report": str(args.report),
                "run_id": report["run_id"],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["preflight"]["ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
