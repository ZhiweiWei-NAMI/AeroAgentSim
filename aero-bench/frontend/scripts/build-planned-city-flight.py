#!/usr/bin/env python3
"""Build an explicitly planned visual UAV loop above a pinned city mesh pack.

This is a camera demonstration path, not PX4 telemetry or a physical flight result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from city_planned_flight import planned_flight_loop
from city_preview_scene import read_scene_paths


def build(scene_path: Path) -> dict:
    paths = read_scene_paths(scene_path)
    placement_bytes = paths.placement.read_bytes()
    placement = json.loads(placement_bytes)
    pack = json.loads(paths.pack.read_text())
    if placement["mesh_pack_source_sha256"] != pack["source"]["sha256"]:
        raise ValueError("Flight plan and building placement use different OSM sources")
    parts = placement["placements"]
    frames, audit = planned_flight_loop([(part["x"], part["z"], part["base_y"] + part["height"]) for part in parts],
                                        pack["extent"])
    return {
        "schema_version": "aero-bench.city-planned-flight-preview/v1",
        "source_kind": "planned-visual-flight",
        "physical_simulation": False,
        "independent_from_sumo_preview": True,
        "mesh_pack_source_sha256": pack["source"]["sha256"],
        "building_placement_sha256": hashlib.sha256(placement_bytes).hexdigest(),
        "geometry_audit": audit,
        "duration_seconds": 120,
        "step_seconds": 0.2,
        "vehicle_ids": ["uav.01", "uav.02"],
        "frames": frames,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    args = parser.parse_args()
    paths = read_scene_paths(args.scene)
    payload = build(args.scene)
    paths.flight.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(paths.flight), "geometry_audit": payload["geometry_audit"]}, indent=2))


if __name__ == "__main__":
    main()
