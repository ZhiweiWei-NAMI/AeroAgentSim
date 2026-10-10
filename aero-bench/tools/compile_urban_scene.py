#!/usr/bin/env python3
"""Compile a deterministic, offline shared 1 km urban OSM scene."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.world.scene_compiler import (  # noqa: E402
    DEFAULT_BUILDING_HEIGHT_M,
    DEFAULT_BUILDING_LEVEL_HEIGHT_M,
    EnuBounds,
    SceneCompilationError,
    SceneOrigin,
    compile_urban_scene,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Strictly crop an offline OSM JSON source into one shared 1 km ENU "
            "scene package. The output directory must not already exist."
        )
    )
    result.add_argument(
        "--source",
        required=True,
        help="Offline OSM API 0.6 JSON input; it is never downloaded or modified.",
    )
    result.add_argument(
        "--output",
        required=True,
        help="New output directory. Existing directories are refused.",
    )
    result.add_argument(
        "--source-provenance",
        help=(
            "Optional source provenance JSON. If omitted, the adjacent "
            "<source-base>.provenance.json is copied when available."
        ),
    )
    result.add_argument("--latitude-deg", type=float, default=31.2304)
    result.add_argument("--longitude-deg", type=float, default=121.4737)
    result.add_argument("--ellipsoid-height-m", type=float, default=50.0)
    result.add_argument("--geoid-undulation-m", type=float, default=30.0)
    result.add_argument("--amsl-m", type=float, default=20.0)
    result.add_argument("--min-east-m", type=float, default=-500.0)
    result.add_argument("--max-east-m", type=float, default=500.0)
    result.add_argument("--min-north-m", type=float, default=-500.0)
    result.add_argument("--max-north-m", type=float, default=500.0)
    result.add_argument(
        "--default-building-height-m",
        type=float,
        default=DEFAULT_BUILDING_HEIGHT_M,
        help="Explicit simulation assumption for buildings without height metadata.",
    )
    result.add_argument(
        "--building-level-height-m",
        type=float,
        default=DEFAULT_BUILDING_LEVEL_HEIGHT_M,
        help="Explicit simulation assumption used with OSM building:levels.",
    )
    result.add_argument(
        "--netconvert",
        action="store_true",
        help=(
            "Validate and run exactly netconvert 1.27.1 from the declared "
            "digest-pinned SUMO OCI workload against geometry-clipped SUMO OSM. "
            "Omit this flag to emit the required command record only; host "
            "versions are intentionally rejected."
        ),
    )
    result.add_argument(
        "--netconvert-executable",
        default="netconvert",
        help="OCI-provided netconvert 1.27.1 executable used only with --netconvert.",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = compile_urban_scene(
            source_path=args.source,
            output_dir=args.output,
            source_provenance_path=args.source_provenance,
            origin=SceneOrigin(
                latitude_deg=args.latitude_deg,
                longitude_deg=args.longitude_deg,
                ellipsoid_height_m=args.ellipsoid_height_m,
                geoid_undulation_m=args.geoid_undulation_m,
                amsl_m=args.amsl_m,
            ),
            bounds=EnuBounds(
                min_east_m=args.min_east_m,
                max_east_m=args.max_east_m,
                min_north_m=args.min_north_m,
                max_north_m=args.max_north_m,
            ),
            default_building_height_m=args.default_building_height_m,
            building_level_height_m=args.building_level_height_m,
            run_netconvert=args.netconvert,
            netconvert_executable=args.netconvert_executable,
        )
    except SceneCompilationError as exc:
        parser().error(str(exc))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "manifest": str(result.manifest_path),
                "source_sha256": result.source_sha256,
                "cropped_source_sha256": result.cropped_source_sha256,
                "building_count": result.building_count,
                "road_count": result.road_count,
                "netconvert_generated": result.netconvert_generated,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
