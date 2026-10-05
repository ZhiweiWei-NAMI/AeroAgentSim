#!/usr/bin/env python3
"""Build the planned visual UAV loop above the canonical rendered city.

Clearance is measured against the rendered building footprints and tops of the same
source context the canonical v3 road binds. This is a camera demonstration path, not
PX4 telemetry or a physical flight result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from city_planned_flight import DURATION_S, STEP_S, planned_flight_loop
from city_preview_coordinates import load_canonical_city_geometry

SCHEMA = "aero-bench.city-planned-flight-preview/v2"


def build(road_path: Path, network: Path, source_osm: Path, objects: Path, render: Path, pack_path: Path) -> dict:
    geometry = load_canonical_city_geometry(objects, render, pack_path)
    context = geometry.source_context(network, source_osm)
    road = json.loads(road_path.read_text())
    if road.get("schema_version") != "aero-bench.city-road-preview/v3" or road.get("source_context") != context:
        raise ValueError("Canonical flight requires the v3 road of the same rendered source context")
    pack = json.loads(pack_path.read_text())
    buildings = []
    for footprint in geometry.footprints:
        centre = footprint.rendered_polygon.centroid
        buildings.append((centre.x, centre.y, footprint.top_up_m))
    frames, audit = planned_flight_loop(buildings, pack["extent"])
    audit["building_basis"] = "rendered-footprint-centroids-and-rendered-top-up-m"
    return {
        "schema_version": SCHEMA,
        "source_kind": "planned-visual-flight",
        "physical_simulation": False,
        "independent_from_sumo_preview": True,
        "mesh_pack_source_sha256": context["mesh_pack_source_sha256"],
        "source_context": context,
        "geometry_audit": audit,
        "duration_seconds": DURATION_S,
        "step_seconds": STEP_S,
        "vehicle_ids": ["uav.01", "uav.02"],
        "frames": frames,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("road", "network", "source-osm", "objects", "render", "pack", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    payload = build(args.road, args.network, args.source_osm, args.objects, args.render, args.pack)
    args.output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "geometry_audit": payload["geometry_audit"]}, indent=2))


if __name__ == "__main__":
    main()
