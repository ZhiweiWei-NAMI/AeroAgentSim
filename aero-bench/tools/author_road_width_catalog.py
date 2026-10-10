#!/usr/bin/env python3
"""Author a road-width catalog from a compiled urban scene.

Reads the scene's provenance-tagged SUMO conversion (``osm/sumo-network.osm`` and
the digest-pinned ``sumo/network.net.xml``), maps every source road
``object_id`` to the exact SUMO edges and lanes it was modelled as, and emits a
``aero-bench.road-width-catalog/v1`` fragment whose ``road_width_m`` section drops
straight into an ``aero-bench.urban-world-authoring/v1`` ``UrbanWorldCatalog``.

Every entry is keyed by the exact source road ``object_id`` and carries explicit
provenance. A width derived from modelled lanes and netconvert's default lane
width is labelled ``is_estimate`` true with its algorithm, configuration and an
evidence digest; a real OSM ``width`` tag, when present, is preserved and labelled
``is_estimate`` false. No highway-class width table and no hidden fallback exist.

By default the tool fails closed if any source road cannot be matched (and has no
OSM width tag) and reports the offending ``object_id``s; pass ``--allow-unmatched``
only to run an explicit coverage audit.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.world.road_width_catalog import (  # noqa: E402
    RoadWidthCatalogError,
    build_road_width_catalog,
    write_catalog_fragment,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Author a provenance-bearing road-width catalog for a compiled "
            "urban scene from its own SUMO netconvert build. Fails closed when "
            "any source road is unmatchable."
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
        help="Path of the emitted aero-bench.road-width-catalog/v1 JSON fragment.",
    )
    result.add_argument(
        "--default-lane-width-m",
        type=float,
        default=None,
        help=(
            "Default lane width substituted for net lanes that omit a width "
            "(default: SUMO's documented 3.2 m constant)."
        ),
    )
    result.add_argument(
        "--allow-unmatched",
        action="store_true",
        help=(
            "Audit mode: emit the fragment for the matchable roads and report "
            "the unmatchable object_ids instead of failing closed."
        ),
    )
    return result


def _summary(result) -> dict[str, object]:
    widths = Counter(entry.width_m for entry in result.entries)
    return {
        "schema_version": result.fragment_document["schema_version"],
        "road_count": result.road_count,
        "matched_road_count": result.matched_count,
        "unmatched_road_count": len(result.unmatched_road_ids),
        "unmatched_road_ids": list(result.unmatched_road_ids),
        "osm_tagged_road_count": result.osm_tagged_count,
        "estimated_road_count": result.estimated_count,
        "osm_lanes_cross_checked": result.osm_lanes_cross_checked,
        "osm_lanes_disagreements": result.osm_lanes_disagreements,
        "algorithm": result.algorithm,
        "default_lane_width_m": result.default_lane_width_m,
        "sumo_netconvert_version": result.sumo_netconvert_version,
        "scene_manifest_sha256": result.scene_manifest_sha256,
        "synthetic_osm_sha256": result.synthetic_osm_sha256,
        "sumo_network_sha256": result.sumo_network_sha256,
        "width_distribution_m": {
            str(width): count for width, count in sorted(widths.items())
        },
        "fragment_sha256": result.fragment_sha256,
    }


def main() -> int:
    args = parser().parse_args()
    kwargs = {"require_complete": not args.allow_unmatched}
    if args.default_lane_width_m is not None:
        kwargs["default_lane_width_m"] = args.default_lane_width_m
    try:
        result = build_road_width_catalog(Path(args.scene_root), **kwargs)
    except RoadWidthCatalogError as exc:
        parser().error(str(exc))
        return 2  # pragma: no cover - parser.error exits
    written_sha256 = write_catalog_fragment(result, Path(args.output))
    summary = _summary(result)
    summary["output"] = args.output
    summary["output_sha256"] = written_sha256
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
