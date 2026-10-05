#!/usr/bin/env python3
"""Issue a fresh Huangpu RunnerConfig for a process-interrupted retry.

The command preserves every execution and capacity field from a validated
Docker Reference Executor config and changes only its output root.  Both the
config path and output root must be fresh, so an interrupted attempt remains
immutable and auditable.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.runner.contracts import RunnerConfig  # noqa: E402


class HuangpuNativeRunnerVersionError(ValueError):
    """A fresh retry config cannot be issued without changing its contract."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-runner", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-runner", type=Path, required=True)
    return parser


def _load_runner(path: Path) -> RunnerConfig:
    if path.is_symlink() or not path.is_file():
        raise HuangpuNativeRunnerVersionError(
            f"base runner must be one regular file: {path}"
        )
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HuangpuNativeRunnerVersionError(
            f"base runner is invalid: {path}: {exc}"
        ) from exc
    runner = RunnerConfig.model_validate(document)
    if runner.executor_kind != "docker_reference":
        raise HuangpuNativeRunnerVersionError(
            "Huangpu retries require the docker_reference executor"
        )
    return runner


def issue_runner(
    *,
    base_runner_path: Path,
    output_root: Path,
) -> RunnerConfig:
    runner = _load_runner(base_runner_path)
    if not output_root.is_absolute():
        raise HuangpuNativeRunnerVersionError("output root must be absolute")
    if output_root.exists() or output_root.is_symlink():
        raise HuangpuNativeRunnerVersionError(
            f"output root must be fresh: {output_root}"
        )
    return runner.model_copy(update={"output_root": str(output_root)})


def _write_new(path: Path, runner: RunnerConfig) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(
        runner.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
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
        raise HuangpuNativeRunnerVersionError(
            f"output runner must be fresh; refusing to overwrite: {path}"
        ) from None
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _parser().parse_args()
    try:
        runner = issue_runner(
            base_runner_path=args.base_runner,
            output_root=args.output_root,
        )
        _write_new(args.output_runner, runner)
    except (HuangpuNativeRunnerVersionError, OSError, ValueError) as exc:
        print(f"city runner versioning failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "artifact_volume_size_bytes": runner.artifact_volume_size_bytes,
                "input_volume_size_bytes": runner.input_volume_size_bytes,
                "output_root": runner.output_root,
                "output_runner": str(args.output_runner),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
