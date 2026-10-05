#!/usr/bin/env python3
"""Project recorded PX4/Gazebo engineering positions into the Shanghai viewer frame."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "validation/urban-inspection-flight-run-20260908T075509Z"
RUN_ID = "5c0a19b53de286c1721d8bebbaa5c54c1d2176c992a78ddfbe467d972e4992ce"
SOURCE = RUN / "runs/baseline" / RUN_ID / "runtime-seal/flight/trajectory.json"
VERIFICATION = RUN / "runs/baseline" / RUN_ID / "verification/verifier/report.json"
SOURCE_PACK = RUN / "inputs/baseline/world/osm2world/manifest.json"
VIEWER_PACK = ROOT / "frontend/public/osm2world/packs/urban-compiled-v2/manifest.json"
OUTPUT = ROOT / "frontend/public/city-presentation/shanghai-px4-preview-v1.json"


def main() -> None:
    source_pack = json.loads(SOURCE_PACK.read_text())
    viewer_pack = json.loads(VIEWER_PACK.read_text())
    if source_pack["source"]["sha256"] != viewer_pack["source"]["sha256"]:
        raise ValueError("Recorded flight and city presentation use different OSM sources")
    verifier = json.loads(VERIFICATION.read_text())
    if verifier["run_id"] != RUN_ID or verifier["execution_scope"] != "executor_validation":
        raise ValueError("Flight verification scope changed")
    frames = []
    airborne = set()
    collisions = set()
    for expected_tick, line in enumerate(SOURCE.read_text().splitlines()):
        record = json.loads(line)
        if record["schema_version"] != "aero-bench.px4-evidence/v1" or record["run_id"] != RUN_ID:
            raise ValueError("Flight trajectory provenance changed")
        if record["tick"] != expected_tick or record["sim_time_ns"] != expected_tick * 200_000_000:
            raise ValueError("Flight trajectory is not a complete 0.2-second sequence")
        samples = []
        for vehicle in record["snapshot"]["vehicles"]:
            pose = json.loads(vehicle["pose_json"])
            vehicle_id = vehicle["vehicle_id"]
            if vehicle["in_air"]:
                airborne.add(vehicle_id)
            if vehicle["collision_contact"]:
                collisions.add(vehicle_id)
            samples.append([vehicle_id, round(pose["x_m"], 3), round(-pose["y_m"], 3),
                            round(pose["z_m"], 3), round(pose["yaw_rad"], 5),
                            round(pose["pitch_rad"], 5), round(pose["roll_rad"], 5),
                            vehicle["in_air"]])
        if len(samples) != 2:
            raise ValueError("The recorded flight does not contain exactly two UAVs")
        frames.append(samples)
    if len(frames) != 601 or airborne != {"uav.01", "uav.02"} or collisions:
        raise ValueError("Recorded PX4 flight is incomplete or has collision contact")
    payload = {
        "schema_version": "aero-bench.city-px4-preview/v1",
        "source_kind": "recorded-px4-gazebo-engineering-replay",
        "independent_from_sumo_preview": True,
        "source_run_id": RUN_ID,
        "source_trajectory_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "mesh_pack_source_sha256": source_pack["source"]["sha256"],
        "verification_scope": verifier["execution_scope"],
        "verification_status": verifier["status"],
        "flight_image_id": json.loads(SOURCE.open().readline())["runtime_image"],
        "duration_seconds": 120, "step_seconds": 0.2,
        "vehicle_ids": ["uav.01", "uav.02"],
        "frames": frames,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "frames": len(frames), "vehicles": payload["vehicle_ids"],
                      "source_run_id": RUN_ID, "verification_status": verifier["status"]}, indent=2))


if __name__ == "__main__":
    main()
