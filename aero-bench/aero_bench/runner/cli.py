from __future__ import annotations

import argparse
from collections.abc import Sequence

from aero_bench.serialization import canonical_json_bytes

from aero_bench.runner.execution import RunnerError, run_suite


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a strict AERO-BENCH suite through the Docker Reference Executor."
    )
    parser.add_argument("suite", help="Path to the strict Suite file")
    parser.add_argument("runner_config", help="Path to the strict RunnerConfig file")
    arguments = parser.parse_args(argv)
    try:
        summary = run_suite(arguments.suite, arguments.runner_config)
    except RunnerError as error:
        parser.error(str(error))
    print(canonical_json_bytes(summary.model_dump(mode="json")).decode("utf-8"))
    return 0 if summary.execution_complete_count == summary.run_count else 1


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
