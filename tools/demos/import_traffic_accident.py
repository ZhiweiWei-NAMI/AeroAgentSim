#!/usr/bin/env python3
"""Freeze local traffic inputs without importing or running the legacy Runtime.

Renderer X/Y/Z -> local ENU E/N/U = X/-Z/Y. This is a local right-handed
frame, NOT a recovered geodetic anchor. Road geometry is an OSM-derived
database (ODbL); meshes/textures are inventoried and never copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from aeroagentsim.packs.traffic_accident.geometry import Polyline, Pose, blockers


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def enu(point: list[float]) -> list[float]:
    if len(point) != 2 or any(
        type(x) not in (float, int) or not math.isfinite(x) for x in point
    ):
        raise ValueError("scene route requires finite renderer X/Z pairs")
    return [float(point[0]), -float(point[1]), 0.0]


def bypass(
    lane: Polyline, adjacent: Polyline, incident_s: float
) -> tuple[list[list[float]], float]:
    start, shift = incident_s - 18, incident_s - 5
    return_start, resume = incident_s + 12, min(lane.length - 4, incident_s + 34)
    count = max(2, math.ceil(resume - start) + 1)
    points = []
    for i in range(count):
        s = start + (resume - start) * i / (count - 1)
        own = lane.at(s).position
        other = adjacent.at(s / lane.length * adjacent.length).position
        fraction = (
            (s - start) / (shift - start)
            if s <= shift
            else (s - return_start) / (resume - return_start)
        )
        t = min(1.0, max(0.0, fraction))
        blend = t * t * (3 - 2 * t)
        weight = blend if s <= shift else 1.0 if s < return_start else 1 - blend
        points.append([a + (b - a) * weight for a, b in zip(own, other)])
    return points, resume


def import_demo(source: Path, output: Path) -> dict[str, Any]:
    """One content-pinned import; exhaustion is never a successful placement."""
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError("import destination must be outside the read-only source demo")
    paths = (
        "config.json",
        "web/assets/scene.json",
        "runtime.py",
        "web/assets/NOTICE.txt",
    )
    raw = {name: (source / name).read_bytes() for name in paths}
    cfg, scene = json.loads(raw[paths[0]]), json.loads(raw[paths[1]])
    cars, uavs = cfg["background_cars"], cfg["background_uavs"]
    if type(cars) is not int or not 0 <= cars <= len(scene["routes"]):
        raise ValueError(
            "background_cars exceeds explicit exported demand; supply new routes"
        )
    if type(uavs) is not int or uavs < 0:
        raise ValueError("background_uavs must be a nonnegative integer")
    road_routes = []
    scene_digest = digest(raw[paths[1]])
    for route in scene["routes"]:
        road_routes.append(
            {
                "id": "route." + route["id"],
                "points_enu_m": [enu(p) for p in route["points"]],
                "speed_mps": route["speed"],
                "offset_m": route["offset_m"],
                "native_edges": [],
                "native_mapping_status": "not_exported_in_source_scene",
                "source_digest": scene_digest,
                "loop_policy": "declared_discontinuity",
            }
        )
    incident = scene["incident"]
    lane_points = [enu(p) for p in incident["points"]]
    adjacent_points = [enu(p) for p in incident["adjacent_lane"]["points"]]
    lane, adjacent = Polyline(lane_points), Polyline(adjacent_points)
    network_id: str | None = None
    network_start: float | None = None
    for route in road_routes:
        points = route["points_enu_m"]
        for start in range(len(points) - len(lane_points) + 1):
            if all(
                math.dist(points[start + i], p) < 0.05
                for i, p in enumerate(lane_points)
            ):
                network_id, network_start = (
                    route["id"],
                    Polyline(points).distances[start],
                )
                break
        if network_id is not None:
            break
    if network_id is None or network_start is None:
        raise ValueError("incident lane lacks connected exported reporter network")
    bypass_points, resume = bypass(lane, adjacent, incident["distance_m"])
    for identity, points, native in (
        ("lane.incident", lane_points, incident["lane_id"]),
        ("lane.adjacent", adjacent_points, incident["adjacent_lane"]["id"]),
        ("adjacent_lane_bypass", bypass_points, ""),
    ):
        road_routes.append(
            {
                "id": identity,
                "points_enu_m": points,
                "native_edges": [],
                "native_lane_id": native,
                "source_digest": scene_digest,
                "loop_policy": "stop",
            }
        )
    actors: list[dict[str, Any]] = []
    poses: dict[str, Pose] = {}

    def actor(
        identity: str,
        route_id: str,
        progress: float,
        speed: float,
        direction: int,
        stop: float,
        loop: bool,
        role: str,
    ) -> None:
        route = next(r for r in road_routes if r["id"] == route_id)
        line = Polyline(route["points_enu_m"])
        pose = line.at(progress)
        pose = Pose(pose.position, pose.heading + (math.pi if direction == -1 else 0))
        poses[identity] = pose
        actors.append(
            {
                "id": identity,
                "route_id": route_id,
                "progress_m": progress,
                "speed_mps": speed,
                "direction": direction,
                "stop_progress_m": stop,
                "loop": loop,
                "role": role,
                "position_enu_m": list(pose.position),
                "attitude_xyzw": pose.quaternion,
            }
        )

    actor(
        "vehicle.reporter",
        "lane.incident",
        10,
        6,
        1,
        incident["distance_m"] - 18,
        False,
        "reporter",
    )
    for suffix, direction in (("a", 1), ("b", -1)):
        stop = (
            incident["distance_m"] - 2.3
            if direction == 1
            else incident["distance_m"] + 2.3
        )
        start = stop - 48 if direction == 1 else min(lane.length, stop + 48)
        # Legacy initializer says 6; tick b uses lane-limited staged speed. Retain both.
        speed = (
            6.0
            if direction == 1
            else min(6.0, max(1.0, (lane.length - stop) / incident["time_s"]))
        )
        actor(
            f"vehicle.incident.{suffix}",
            "lane.incident",
            start,
            speed,
            direction,
            stop,
            False,
            "incident",
        )
    for route in road_routes[:cars]:
        line = Polyline(route["points_enu_m"])
        progress = float(route["offset_m"])
        for _ in range(200):
            conflicts = blockers(route["id"][6:], line.at(progress), poses, 0.55)
            if not conflicts:
                break
            progress = (progress + 7) % line.length
        else:
            raise ValueError(f"packing exhausted for {route['id']}: {conflicts}")
        actor(
            route["id"][6:],
            route["id"],
            progress,
            route["speed_mps"],
            1,
            line.length,
            True,
            "background",
        )
    corridor = Polyline(bypass_points)
    corridor_count = math.ceil(corridor.length / 0.5)
    for row in actors:
        identity = row["id"]
        row["blocked_by"] = list(blockers(identity, poses[identity], poses, 0.55))
        if row["role"] == "reporter":
            occupied: set[str] = set()
            for i in range(corridor_count + 1):
                occupied.update(
                    blockers(
                        identity,
                        corridor.at(corridor.length * i / corridor_count),
                        poses,
                        0.8,
                    )
                )
            row["safe_gap"] = not occupied
        else:
            row["safe_gap"] = not row["blocked_by"]
    p = incident["position"]
    anchor = [p["x"], -p["z"], 0.0]

    def offset(x: float, z: float, altitude: float = 145) -> list[float]:
        return [anchor[0] + x, anchor[1] - z, altitude]

    air = [
        {
            "id": "uav.alpha",
            "mode": "waypoints",
            "speed_mps": 7,
            "initial_legacy_battery_pct": 91,
            "points_enu_m": [
                offset(65, 30),
                offset(-360, 270),
                offset(360, 275),
                offset(-345, -280),
                offset(350, -270),
            ],
            "task_id": "medical-delivery-01",
            "interruptible": False,
        },
        {
            "id": "uav.bravo",
            "mode": "orbit",
            "radius_m": 210,
            "phase_rad": 0,
            "altitude_m": 145,
            "speed_mps": cfg["uav_speed_mps"],
            "initial_legacy_battery_pct": 87,
            "position_enu_m": offset(210, 0),
            "task_id": "district-patrol-02",
            "interruptible": True,
        },
    ]
    for i in range(uavs):
        phase, radius = i * math.tau / uavs, 120 + 38 * i
        air.append(
            {
                "id": f"uav.bg.{i + 1:02d}",
                "mode": "orbit",
                "radius_m": radius,
                "phase_rad": phase,
                "altitude_m": 145 + 5 * i,
                "speed_mps": cfg["uav_speed_mps"],
                "position_enu_m": offset(
                    radius * math.cos(phase), radius * math.sin(phase), 145 + 5 * i
                ),
                "initial_legacy_battery_pct": 76 + 2.5 * i,
                "task_id": f"background-patrol-{i + 1:02d}",
                "interruptible": True,
            }
        )
    inventory = []
    for path in sorted((source / "web/assets").rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            inventory.append(
                {
                    "path": path.relative_to(source).as_posix(),
                    "sha256": digest(data),
                    "bytes": len(data),
                    "distribution": "reference_only",
                    "licence_status": "undecided",
                }
            )
    result = {
        "format": "traffic-accident-inputs/v1",
        "source_digests": {n: digest(b) for n, b in raw.items()},
        "frame": {
            "id": "traffic.local/v1",
            "transform": "E=X,N=-Z,U=Y",
            "geodetic_anchor": None,
        },
        "config": {
            k: cfg[k]
            for k in (
                "background_cars",
                "background_uavs",
                "tick_hz",
                "uav_speed_mps",
                "capture_dwell_s",
                "region_radius_m",
            )
        },
        "incident": {
            "anchor_enu_m": anchor,
            "distance_m": incident["distance_m"],
            "time_s": incident["time_s"],
        },
        "road_routes": road_routes,
        "road_actors": actors,
        "air_actors": air,
        "reporter_return": {
            "route_id": network_id,
            "progress_m": network_start + resume,
        },
        "asset_pipeline": {
            "command": "python tools/sync_assets.py --help",
            "map_command": "python tools/build_map.py --help",
            "visual_parity": "not_verified",
            "inventory_ref": "city-manifest.json",
        },
        "licences": {
            "road_database": "ODbL-1.0; © OpenStreetMap contributors; original source XML/demand recipe not supplied",
            "code_parameters": "MIT project code; renderer meshes and textures excluded",
            "undecided": [
                "Huangpu mesh and embedded textures",
                "legacy car.glb",
                "legacy uav.glb",
                "native demand recipe",
            ],
        },
    }
    # Guard against reading a moving source checkout as one coherent import.
    for name, data in raw.items():
        if (source / name).read_bytes() != data:
            raise ValueError(f"source changed during import: {name}")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "inputs.json", result)
    # Compact metadata only; do not ship original 1.9 MB scene/GLBs.
    write_json(
        output / "city-manifest.json",
        {
            "scene_sha256": scene_digest,
            "bounds_renderer_xz": scene["bounds"],
            "building_count": len(scene["buildings"]),
            "building_placements": [
                {
                    "id": b["id"],
                    "source_asset_path": "web" + b["url"],
                    "position_enu_m": [b["x"], -b["z"], b["y"]],
                    "height_m": b["height"],
                    "licence_status": "undecided; placement data retained, mesh not copied",
                }
                for b in scene["buildings"]
            ],
            "road_layers": {k: len(v) for k, v in scene["roads"].items()},
            "assets": inventory,
            "distribution": "external assets only; see INTEGRATION.md and ASSETS.md",
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    import_demo(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
