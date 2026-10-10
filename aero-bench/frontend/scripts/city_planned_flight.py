"""Planned visual UAV loop shared by the placement and canonical city flight builders.

The loop is a camera demonstration path, not PX4 telemetry or a physical flight result.
Buildings are (x, z, top_up_m) in viewer east/south metres; the extent is the pack's
declared west/east/south/north bounds in metres.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

DURATION_S = 120
STEP_S = 0.2
MAX_SPEED_MPS = 15
AIRFRAME_HALF_HEIGHT_M = 0.35
MIN_LOOP_RADIUS_M = 75
EXTENT_MARGIN_M = 20


def planned_flight_loop(buildings: Sequence[tuple[float, float, float]], extent: Mapping[str, float]) -> tuple[list, dict]:
    if not buildings:
        raise ValueError("City flight plan has no building geometry to audit")
    roof_ceiling = max(top for _, _, top in buildings)
    lower_altitude = math.ceil(roof_ceiling + 25)
    upper_altitude = lower_altitude + 22
    density = [(sum(math.hypot(x - ox, z - oz) < 180 for ox, oz, _ in buildings), x, z) for x, z, _ in buildings]
    _, district_x, district_z = max(density)
    center_x = district_x + 30
    center_z = district_z - 50
    minimum_center_x = extent["west"] + EXTENT_MARGIN_M + MIN_LOOP_RADIUS_M
    maximum_center_x = extent["east"] - EXTENT_MARGIN_M - MIN_LOOP_RADIUS_M
    minimum_center_z = MIN_LOOP_RADIUS_M + EXTENT_MARGIN_M - extent["north"]
    maximum_center_z = -extent["south"] - EXTENT_MARGIN_M - MIN_LOOP_RADIUS_M
    if minimum_center_x > maximum_center_x or minimum_center_z > maximum_center_z:
        raise ValueError("Dense district lacks room for a collision-clear planned flight loop")
    center_x = min(max(center_x, minimum_center_x), maximum_center_x)
    center_z = min(max(center_z, minimum_center_z), maximum_center_z)
    radius_x = min(140, center_x - extent["west"] - EXTENT_MARGIN_M,
                   extent["east"] - center_x - EXTENT_MARGIN_M)
    radius_z = min(110, center_z + extent["north"] - EXTENT_MARGIN_M,
                   -extent["south"] - center_z - EXTENT_MARGIN_M)
    if radius_x < MIN_LOOP_RADIUS_M or radius_z < MIN_LOOP_RADIUS_M:
        raise ValueError("Dense district lacks room for a collision-clear planned flight loop")
    frames = []
    speeds: dict[str, list[float]] = {"uav.01": [], "uav.02": []}
    previous: dict[str, tuple[float, float]] = {}
    for tick in range(round(DURATION_S / STEP_S) + 1):
        second = tick * STEP_S
        angle = 2 * math.pi * second / DURATION_S
        samples = []
        for aircraft_id, route_x, route_z, loop_x, loop_z, phase, altitude in (
            ("uav.01", center_x, center_z, radius_x, radius_z, 0.8, lower_altitude),
            ("uav.02", center_x + 15, center_z - 10, radius_x * 0.72, radius_z * 0.72, 3.0, upper_altitude),
        ):
            theta = angle + phase
            x = route_x + loop_x * math.cos(theta)
            z = route_z + loop_z * math.sin(theta)
            velocity_x = -loop_x * math.sin(theta) * 2 * math.pi / DURATION_S
            velocity_z = loop_z * math.cos(theta) * 2 * math.pi / DURATION_S
            yaw = math.atan2(velocity_x, -velocity_z)
            if not (extent["west"] + 10 < x < extent["east"] - 10
                    and extent["south"] + 10 < -z < extent["north"] - 10):
                raise ValueError(f"Planned aircraft leaves the mapped city: {aircraft_id} at {second}s")
            if aircraft_id in previous:
                last_x, last_z = previous[aircraft_id]
                speeds[aircraft_id].append(math.hypot(x - last_x, z - last_z) / STEP_S)
            previous[aircraft_id] = (x, z)
            samples.append([aircraft_id, round(x, 3), round(z, 3), altitude,
                            round(yaw, 5), 0.0, 0.0, True])
        frames.append(samples)
    if max(max(values) for values in speeds.values()) > MAX_SPEED_MPS:
        raise ValueError("Planned flight exceeds its 15 m/s visual speed limit")
    audit = {
        "building_count": len(buildings),
        "highest_building_m": roof_ceiling,
        "minimum_aircraft_altitude_m": lower_altitude,
        "minimum_building_clearance_m": lower_altitude - roof_ceiling - AIRFRAME_HALF_HEIGHT_M,
        "minimum_aircraft_vertical_separation_m": upper_altitude - lower_altitude - AIRFRAME_HALF_HEIGHT_M,
        "max_speed_mps": {key: round(max(values), 3) for key, values in speeds.items()},
        "visual_district_center_xz": [district_x, district_z],
    }
    return frames, audit
