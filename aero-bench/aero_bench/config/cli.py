from __future__ import annotations

from aero_bench.providers.registry import builtin_provider_registry

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from aero_bench.config.resolver import resolve_suite
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve strict AERO-BENCH suite cases into immutable runs."
    )
    parser.add_argument("suite", help="Path to suite.yaml")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="New directory for immutable resolved-run JSON files; it must not exist.",
    )
    parser.add_argument(
        "--executor",
        choices=("docker_reference", "kubernetes_cluster"),
        required=True,
        help="Explicit execution profile recorded in each ResolvedRun.",
    )
    arguments = parser.parse_args(argv)
    runs = resolve_suite(
        arguments.suite,
        executor_kind=arguments.executor,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    output_dir = Path(arguments.output_dir)
    output_dir.mkdir(parents=True)
    for run in runs:
        destination = output_dir / f"{run.run_id}.resolved-run.json"
        destination.write_bytes(
            canonical_json_bytes(run.model_dump(mode="json")) + b"\n"
        )
    index = {
        "schema_version": "aero-bench.resolved-index/v1",
        "executor_kind": arguments.executor,
        "run_count": len(runs),
        "run_ids": [run.run_id for run in runs],
    }
    (output_dir / "index.json").write_bytes(canonical_json_bytes(index) + b"\n")
    print(json.dumps(index, sort_keys=True))
    return 0
