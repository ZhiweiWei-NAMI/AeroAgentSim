#!/usr/bin/env python3
"""Issue a new Huangpu native input lock from exact generated artifacts.

The command preserves the accepted source pins from an existing lock and
replaces only its generated WorldPackage and, when supplied, formal execution
bindings. It writes a fresh output path and never mutates the prior lock or a
failed-run bundle.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeExecutionBinding,
    CityNativeInputLock,
    CityNativeWorldBinding,
    PinnedCityFile,
    load_city_native_input_lock,
)
from aero_bench.config.loader import sha256_file  # noqa: E402
from aero_bench.config.models import FileRef  # noqa: E402
from aero_bench.world.contracts import WorldPackage  # noqa: E402


class HuangpuNativeRebindError(ValueError):
    """Generated Huangpu artifacts cannot be bound without ambiguity."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--base-lock", type=Path, required=True)
    parser.add_argument("--world-bundle", type=Path, required=True)
    parser.add_argument("--authored-catalog", type=Path, required=True)
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--runner-config", type=Path)
    parser.add_argument("--output-lock", type=Path, required=True)
    return parser


def _repository_path(root: Path, path: Path, label: str) -> tuple[Path, str]:
    if path.is_symlink():
        raise HuangpuNativeRebindError(f"{label} cannot be a symbolic link: {path}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HuangpuNativeRebindError(f"{label} is unavailable: {path}") from exc
    if not resolved.is_file():
        raise HuangpuNativeRebindError(f"{label} must be a regular file: {path}")
    if not resolved.is_relative_to(root):
        raise HuangpuNativeRebindError(f"{label} must remain inside the repository")
    return resolved, resolved.relative_to(root).as_posix()


def _pinned(root: Path, path: Path, label: str) -> PinnedCityFile:
    resolved, relative = _repository_path(root, path, label)
    return PinnedCityFile(
        file=FileRef(path=relative, sha256=sha256_file(resolved)),
        size_bytes=resolved.stat().st_size,
    )


def issue_lock(
    *,
    root: Path,
    base_lock_path: Path,
    world_bundle: Path,
    authored_catalog_path: Path,
    suite_path: Path | None,
    runner_config_path: Path | None,
) -> CityNativeInputLock:
    root = root.resolve(strict=True)
    definition = load_city_native_input_lock(base_lock_path)
    if world_bundle.is_symlink():
        raise HuangpuNativeRebindError("world bundle cannot be a symbolic link")
    bundle = world_bundle.resolve(strict=True)
    if not bundle.is_dir() or not bundle.is_relative_to(root):
        raise HuangpuNativeRebindError("world bundle must be a repository directory")
    package_path = bundle / "world/package.json"
    package_file = _pinned(root, package_path, "WorldPackage")
    world = WorldPackage.model_validate_json(package_path.read_bytes())
    world_binding = CityNativeWorldBinding(
        world_id=world.world_id,
        bundle_root=bundle.relative_to(root).as_posix(),
        authored_catalog=_pinned(root, authored_catalog_path, "authored catalog"),
        world_package=package_file,
    )

    if (suite_path is None) != (runner_config_path is None):
        raise HuangpuNativeRebindError(
            "--suite and --runner-config must be supplied together"
        )
    execution = None
    if suite_path is not None and runner_config_path is not None:
        if definition.execution is None:
            raise HuangpuNativeRebindError(
                "base lock has no image lock or Provider adapter set to preserve"
            )
        execution = CityNativeExecutionBinding(
            task_id=definition.execution.task_id,
            suite=_pinned(root, suite_path, "Suite"),
            runner_config=_pinned(root, runner_config_path, "runner config"),
            image_lock=definition.execution.image_lock,
            required_provider_adapters=(
                definition.execution.required_provider_adapters
            ),
        )
    return definition.model_copy(
        update={"world": world_binding, "execution": execution}
    )


def _write_new(path: Path, definition: CityNativeInputLock) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            definition.model_dump(mode="json"),
            indent=2,
            sort_keys=False,
            ensure_ascii=False,
        )
        + "\n"
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
        raise HuangpuNativeRebindError(
            f"output lock must be fresh; refusing to overwrite: {path}"
        ) from None
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise HuangpuNativeRebindError(f"cannot write output lock: {path}") from exc
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _parser().parse_args()
    try:
        definition = issue_lock(
            root=args.root,
            base_lock_path=args.base_lock,
            world_bundle=args.world_bundle,
            authored_catalog_path=args.authored_catalog,
            suite_path=args.suite,
            runner_config_path=args.runner_config,
        )
        _write_new(args.output_lock, definition)
    except (HuangpuNativeRebindError, OSError, ValueError) as exc:
        print(f"city native rebind failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "execution_bound": definition.execution is not None,
                "output_lock": str(args.output_lock),
                "world_id": definition.world.world_id if definition.world else None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
