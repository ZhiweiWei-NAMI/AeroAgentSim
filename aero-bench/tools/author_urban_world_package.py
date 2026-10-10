#!/usr/bin/env python3
"""Author a strict aero-bench.world/v2 WorldPackage from a compiled urban scene."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.world.scene_authoring import (  # noqa: E402
    UrbanWorldAuthoringError,
    UrbanWorldAuthoringRequest,
    author_urban_world_package,
)
from aero_bench.world.scene_compiler import SceneOrigin  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Map a compiled aero-bench.urban-scene-compiler/v1 package into a "
            "strict aero-bench.world/v2 WorldPackage bundle. Buildings and road "
            "centrelines come from the scene; the explicit WGS84 origin must "
            "match the compiled manifest; the catalog supplies the facts the "
            "scene cannot (geoid/terrain datum assets, license, weather, and a "
            "defensible width for every source road and one renderable asset per "
            "building). Authoring fails closed if any source road lacks a width "
            "or any building lacks an authored render asset. The output directory "
            "must not already contain files."
        )
    )
    result.add_argument(
        "--scene-root",
        required=True,
        help="Compiled urban scene package (containing manifest.json).",
    )
    result.add_argument(
        "--output",
        required=True,
        help="Bundle output directory. A non-empty existing directory is refused.",
    )
    result.add_argument(
        "--world-id",
        required=True,
        help="Explicit scene identity (strict identifier, e.g. world.city-demo).",
    )
    result.add_argument(
        "--catalog",
        required=True,
        help="Explicit authored catalog JSON of facts the compiled scene cannot supply.",
    )
    result.add_argument("--latitude-deg", type=float, required=True)
    result.add_argument("--longitude-deg", type=float, required=True)
    result.add_argument("--ellipsoid-height-m", type=float, required=True)
    result.add_argument("--geoid-undulation-m", type=float, required=True)
    result.add_argument("--amsl-m", type=float, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = author_urban_world_package(
            UrbanWorldAuthoringRequest(
                scene_root=Path(args.scene_root),
                output_root=Path(args.output),
                world_id=args.world_id,
                origin=SceneOrigin(
                    latitude_deg=args.latitude_deg,
                    longitude_deg=args.longitude_deg,
                    ellipsoid_height_m=args.ellipsoid_height_m,
                    geoid_undulation_m=args.geoid_undulation_m,
                    amsl_m=args.amsl_m,
                ),
                catalog_path=Path(args.catalog),
            )
        )
    except UrbanWorldAuthoringError as exc:
        parser().error(str(exc))
    print(
        json.dumps(
            {
                "bundle_root": str(result.bundle_root),
                "package_path": result.package_path,
                "package_sha256": result.package_ref.sha256,
                "world_id": result.world_package.world_id,
                "world_digest": result.world_digest,
                "asset_digest": result.asset_digest,
                "scene_source_sha256": result.scene_source_sha256,
                "scene_manifest_sha256": result.scene_manifest_sha256,
                "catalog_sha256": result.catalog_sha256,
                "building_count": result.building_count,
                "road_count": result.road_count,
                "entity_count": result.entity_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
