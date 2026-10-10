#!/usr/bin/env python3
"""Author and pin one logistics bundle from an explicit base world + facilities.

This is the production entry point of the logistics scene/bundle generation
pipeline: it drives :func:`aero_bench.tasks.logistics.bundle_builder
.build_logistics_bundle`, which derives the facility-spliced world and pins the
``px4.gazebo`` provider declaration *before* the suite is resolved, so the world
identity enters the Run ID.

Every input is explicit: the authored source bundle, the selected compiled base
world SDF, the logistics facilities document, and the WGS84 scene origin.  With
``--resolve`` the produced suite is resolved and its Run ID is printed; the
runtime flag ``LOGISTICS_RUNTIME_IMPLEMENTED`` is not touched and no flight is
executed.
"""

from __future__ import annotations


import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.providers.registry import builtin_provider_registry

from aero_bench.tasks.logistics.bundle_builder import (  # noqa: E402
    LogisticsBundleAuthoringError,
    LogisticsBundleRequest,
    build_logistics_bundle,
)
from aero_bench.world.scene_compiler import SceneOrigin  # noqa: E402


def _load_package_document(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Author a logistics bundle: derive and pin the px4.gazebo world "
            "declaration from an explicit compiled base world and facilities "
            "document, then stage the pinned bundle before resolution."
        )
    )
    result.add_argument(
        "--source",
        required=True,
        help="Authored source logistics bundle root (contains suite.yaml).",
    )
    result.add_argument(
        "--output",
        required=True,
        help="New/empty output bundle root for the pinned bundle.",
    )
    result.add_argument(
        "--base-world",
        required=True,
        help="Selected compiled base world SDF; it is read and never modified.",
    )
    result.add_argument(
        "--package",
        help="Explicit logistics facilities document (JSON/YAML). Defaults to "
        "the source bundle package.",
    )
    result.add_argument("--latitude-deg", type=float, required=True)
    result.add_argument("--longitude-deg", type=float, required=True)
    result.add_argument("--ellipsoid-height-m", type=float, required=True)
    result.add_argument("--geoid-undulation-m", type=float, required=True)
    result.add_argument("--amsl-m", type=float, required=True)
    result.add_argument("--flight-provider-id", default="flight")
    result.add_argument("--executor-kind", default="docker_reference")
    result.add_argument(
        "--resolve",
        action="store_true",
        help="Resolve the produced suite and print its Run ID (no flight).",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    origin = SceneOrigin(
        latitude_deg=args.latitude_deg,
        longitude_deg=args.longitude_deg,
        ellipsoid_height_m=args.ellipsoid_height_m,
        geoid_undulation_m=args.geoid_undulation_m,
        amsl_m=args.amsl_m,
    )
    package_document = (
        _load_package_document(Path(args.package)) if args.package else None
    )
    request = LogisticsBundleRequest(
        source_root=Path(args.source),
        output_root=Path(args.output),
        base_world_sdf=Path(args.base_world),
        origin=origin,
        package_document=package_document,
        flight_provider_id=args.flight_provider_id,
        executor_kind=args.executor_kind,
    )
    try:
        result = build_logistics_bundle(request)
    except LogisticsBundleAuthoringError as exc:
        parser().error(str(exc))

    payload: dict[str, object] = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(result).items()
    }
    if args.resolve:
        from aero_bench.config.resolver import resolve_suite
        from aero_bench.tasks.registry import builtin_task_package_resolvers

        runs = resolve_suite(
            str(result.suite_path),
            executor_kind=args.executor_kind,
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=builtin_provider_registry(),
        )
        if len(runs) != 1:
            raise SystemExit(
                f"expected exactly one logistics run, found {len(runs)}"
            )
        payload["run_id"] = runs[0].run_id
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
