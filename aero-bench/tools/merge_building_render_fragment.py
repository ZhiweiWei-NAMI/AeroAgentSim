#!/usr/bin/env python3
"""Merge a strict building-render fragment into an authored urban world catalog.

Validates the fragment 1:1 against a compiled urban scene (duplicate/missing/
extra ids, wrong source frame, path aliasing, SHA-256/size mismatches, stale
source scene identity, geometry-envelope drift), then -- when an authored
``UrbanWorldCatalog`` is supplied -- merges it into a written output catalog
whose ``file`` entries point at the fragment's exact bytes. With
``--package-output`` the real ``author_urban_world_package`` stage is run when
every other required input exists; otherwise a precise prerequisite report is
written and nothing (no terrain, license or weather value) is invented.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.world.building_render_merge import (  # noqa: E402
    BuildingRenderMergeError,
    assess_authoring_prerequisites,
    merge_building_render_fragment,
    validate_building_render_fragment,
    verify_staged_render_bytes,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Validate a strict building-render fragment against a compiled urban "
            "scene and merge it into an authored UrbanWorldCatalog, staging the "
            "fragment's exact bytes. Fails closed on duplicate/missing/extra "
            "building ids, wrong source frame, path aliasing, sha256/size "
            "mismatch, stale source scene identity, or a geometry envelope that "
            "does not match the scene. Without the other authored inputs "
            "(license, weather, geoid, terrain, road widths) a precise "
            "prerequisite report is written instead of inventing them."
        )
    )
    result.add_argument(
        "--fragment", required=True, help="Strict building-render fragment JSON."
    )
    result.add_argument(
        "--scene-root", required=True, help="Compiled urban scene package root."
    )
    result.add_argument(
        "--evidence", required=True, help="Machine-readable evidence JSON to write."
    )
    result.add_argument(
        "--prerequisites-report",
        required=True,
        help="Authoring prerequisite report JSON to write.",
    )
    result.add_argument(
        "--catalog",
        help="Authored UrbanWorldCatalog JSON (building_render must be empty).",
    )
    result.add_argument(
        "--output-catalog",
        help="Where to write the merged catalog (required with --catalog).",
    )
    result.add_argument(
        "--road-width-catalog",
        help="Optional aero-bench.road-width-catalog/v1 fragment for the report.",
    )
    result.add_argument(
        "--package-output",
        help=(
            "Author a real WorldPackage bundle here when every prerequisite "
            "exists; blocked prerequisites exit 3 with the report instead."
        ),
    )
    result.add_argument(
        "--world-id", help="Explicit scene identity (with --package-output)."
    )
    result.add_argument("--latitude-deg", type=float)
    result.add_argument("--longitude-deg", type=float)
    result.add_argument("--ellipsoid-height-m", type=float)
    result.add_argument("--geoid-undulation-m", type=float)
    result.add_argument("--amsl-m", type=float)
    return result


def _write_json(path: Path, document: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parser().parse_args()
    try:
        validated = validate_building_render_fragment(
            fragment_path=Path(args.fragment),
            scene_root=Path(args.scene_root),
        )
        evidence = dict(validated.evidence)

        catalog_path: Path | None = Path(args.catalog) if args.catalog else None
        merged_catalog: Path | None = None
        if catalog_path is not None:
            if not args.output_catalog:
                parser().error("--output-catalog is required with --catalog")
            merge = merge_building_render_fragment(
                fragment_path=Path(args.fragment),
                scene_root=Path(args.scene_root),
                catalog_path=catalog_path,
                output_catalog_path=Path(args.output_catalog),
            )
            merged_catalog = merge.output_catalog_path
            evidence = dict(merge.evidence)
            validated = merge.validated
        elif args.output_catalog:
            parser().error("--output-catalog requires --catalog")

        report = assess_authoring_prerequisites(
            scene_root=Path(args.scene_root),
            catalog_path=merged_catalog or catalog_path,
            validated_fragment=validated,
            road_width_catalog_path=(
                Path(args.road_width_catalog) if args.road_width_catalog else None
            ),
        )
        _write_json(Path(args.evidence), evidence)
        _write_json(Path(args.prerequisites_report), report)

        summary: dict[str, object] = {
            "fragment_sha256": validated.fragment_sha256,
            "render_count": len(validated.staged),
            "staged_bytes": sum(item.byte_size for item in validated.staged),
            "objects_json_sha256": validated.scene.objects_sha256,
            "prerequisites_ready": report["ready"],
        }

        if args.package_output:
            if not report["ready"]:
                summary["status"] = "prerequisites_blocked"
                summary["blocking"] = report["blocking"]
                print(json.dumps(summary, sort_keys=True))
                return 3
            missing_flags = [
                name
                for name in (
                    "world_id",
                    "latitude_deg",
                    "longitude_deg",
                    "ellipsoid_height_m",
                    "geoid_undulation_m",
                    "amsl_m",
                )
                if getattr(args, name) is None
            ]
            if missing_flags:
                parser().error(
                    f"--package-output requires {', '.join('--' + n.replace('_', '-') for n in missing_flags)}"
                )
            from aero_bench.world.scene_authoring import (  # noqa: PLC0415
                UrbanWorldAuthoringError,
                UrbanWorldAuthoringRequest,
                author_urban_world_package,
            )
            from aero_bench.world.scene_compiler import SceneOrigin  # noqa: PLC0415

            try:
                result = author_urban_world_package(
                    UrbanWorldAuthoringRequest(
                        scene_root=Path(args.scene_root),
                        output_root=Path(args.package_output),
                        world_id=args.world_id,
                        origin=SceneOrigin(
                            latitude_deg=args.latitude_deg,
                            longitude_deg=args.longitude_deg,
                            ellipsoid_height_m=args.ellipsoid_height_m,
                            geoid_undulation_m=args.geoid_undulation_m,
                            amsl_m=args.amsl_m,
                        ),
                        catalog_path=merged_catalog or catalog_path,
                    )
                )
            except UrbanWorldAuthoringError as exc:
                summary["status"] = "authoring_failed"
                summary["error"] = str(exc)
                print(json.dumps(summary, sort_keys=True))
                return 2
            verified = verify_staged_render_bytes(result.bundle_root, validated.staged)
            evidence["package"] = {
                "bundle_root": str(result.bundle_root),
                "package_sha256": result.package_ref.sha256,
                "world_digest": result.world_digest,
                "asset_digest": result.asset_digest,
                "building_count": result.building_count,
                "building_render_staged_and_verified": verified,
                "scene_manifest_sha256": result.scene_manifest_sha256,
                "catalog_sha256": result.catalog_sha256,
            }
            _write_json(Path(args.evidence), evidence)
            summary["status"] = "package_authored"
            summary["package_sha256"] = result.package_ref.sha256
            summary["world_digest"] = result.world_digest
            summary["asset_digest"] = result.asset_digest
            summary["staged_verified"] = verified
        else:
            summary["status"] = "merged" if merged_catalog else "validated"
            if merged_catalog is not None:
                summary["output_catalog_sha256"] = evidence["catalog"]["output_sha256"]

        print(json.dumps(summary, sort_keys=True))
        return 0
    except BuildingRenderMergeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
