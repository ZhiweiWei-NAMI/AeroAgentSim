"""Place urban street lamps along verified road-to-walkway curbs.

This module creates visual placements only. It does not change SUMO routes,
crossings, junction topology, or the road and walking surfaces.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from math import floor

from shapely import prepare
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, nearest_points, unary_union

from city_street_surfaces import PRECISION_GRID_M


DEFAULT_SPACING_M = 24.0
DEFAULT_CURB_OFFSET_M = 0.6
DEFAULT_SHARED_BOUNDARY_TOLERANCE_M = 0.15
DEFAULT_ENDPOINT_SETBACK_M = 8.0
DEFAULT_POLE_RADIUS_M = 0.18
DEFAULT_MINIMUM_CLEAR_WALK_WIDTH_M = 1.2
PLACEMENT_SAFETY_M = 0.01
TANGENT_SAMPLE_M = 0.3
CURB_SIDE_PROBE_M = 0.05
POINT_GRID_M = 20.0


def _geometry(value: BaseGeometry, label: str, *, allow_empty: bool = False) -> BaseGeometry:
    if not isinstance(value, BaseGeometry):
        raise TypeError(f"{label} must be a Shapely geometry")
    if not value.is_valid:
        raise ValueError(f"{label} must be valid")
    if value.is_empty and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    if not value.is_empty and not all(math.isfinite(number) for number in value.bounds):
        raise ValueError(f"{label} bounds must be finite")
    return value


def _geometry_parts(values: BaseGeometry | Iterable[BaseGeometry], label: str) -> list[BaseGeometry]:
    if isinstance(values, BaseGeometry):
        parts = [values]
    else:
        parts = list(values)
    for index, part in enumerate(parts):
        _geometry(part, f"{label}[{index}]", allow_empty=True)
    return [part for part in parts if not part.is_empty]


def _union(values: BaseGeometry | Iterable[BaseGeometry], label: str) -> BaseGeometry:
    parts = _geometry_parts(values, label)
    return unary_union(parts) if parts else Point().buffer(0)


def _point(value: Point | Sequence[float], label: str) -> Point:
    if isinstance(value, Point):
        result = value
    else:
        if len(value) != 2:
            raise ValueError(f"{label} must contain x and z")
        x, z = float(value[0]), float(value[1])
        if not math.isfinite(x) or not math.isfinite(z):
            raise ValueError(f"{label} coordinates must be finite")
        result = Point(x, z)
    if result.is_empty or not result.is_valid or not math.isfinite(result.x) or not math.isfinite(result.y):
        raise ValueError(f"{label} must be a finite point")
    return result


def _line_parts(geometry: BaseGeometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [line for part in geometry.geoms for line in _line_parts(part)]
    return []


def _canonical_line(line: LineString) -> LineString:
    coordinates = [(round(float(x), 3), round(float(z), 3)) for x, z, *_ in line.coords]
    closed = len(coordinates) > 2 and coordinates[0] == coordinates[-1]
    if closed:
        ring = coordinates[:-1]
        first = min(point for point in ring)
        starts = [index for index, point in enumerate(ring) if point == first]
        candidates = []
        for start in starts:
            forward = ring[start:] + ring[:start]
            reverse = [ring[start], *reversed(ring[:start]), *reversed(ring[start + 1:])]
            candidates.extend((forward, reverse))
        coordinates = [*min(candidates), min(candidates)[0]]
    elif tuple(reversed(coordinates)) < tuple(coordinates):
        coordinates.reverse()
    return LineString(coordinates)


def _boundary_id(line: LineString) -> str:
    payload = json.dumps([(round(float(x), 3), round(float(z), 3)) for x, z in line.coords],
                         separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"curb-{digest}"


def _street_boundaries(roadbed: BaseGeometry, walkbed: BaseGeometry,
                       tolerance_m: float) -> list[tuple[str, LineString]]:
    near_road = walkbed.boundary.intersection(roadbed.buffer(tolerance_m))
    lines = _line_parts(near_road)
    if not lines:
        return []
    noded = unary_union(lines)
    merged = noded if noded.geom_type == "LineString" else linemerge(noded)
    canonical = [_canonical_line(line) for line in _line_parts(merged) if line.length > 0.1]
    unique = {_boundary_id(line): line for line in canonical}
    return sorted(unique.items(), key=lambda item: item[0])


def _source_boundary(boundary_id: str, station_m: float, curb_point: Point) -> dict:
    return {
        "id": boundary_id,
        "station_m": round(station_m, 3),
        "curb_point": [round(curb_point.x, 3), round(curb_point.y, 3)],
    }


def _inward_normal(line: LineString, station_m: float, walkbed: BaseGeometry) -> tuple[float, float] | None:
    before = line.interpolate(max(0.0, station_m - TANGENT_SAMPLE_M))
    after = line.interpolate(min(line.length, station_m + TANGENT_SAMPLE_M))
    dx, dz = after.x - before.x, after.y - before.y
    length = math.hypot(dx, dz)
    if length < 1e-6:
        return None
    left = (-dz / length, dx / length)
    right = (-left[0], -left[1])
    curb = line.interpolate(station_m)
    left_inside = walkbed.covers(Point(curb.x + left[0] * CURB_SIDE_PROBE_M,
                                      curb.y + left[1] * CURB_SIDE_PROBE_M))
    right_inside = walkbed.covers(Point(curb.x + right[0] * CURB_SIDE_PROBE_M,
                                        curb.y + right[1] * CURB_SIDE_PROBE_M))
    if left_inside == right_inside:
        return None
    return left if left_inside else right


def _clearance_zone(geometries: BaseGeometry | Iterable[BaseGeometry], label: str,
                    distance_m: float) -> BaseGeometry:
    union = _union(geometries, label)
    return union.buffer(distance_m) if not union.is_empty and distance_m > 0 else union


def _point_grid(points: Sequence[Point]) -> dict[tuple[int, int], list[Point]]:
    grid: dict[tuple[int, int], list[Point]] = {}
    for point in points:
        key = (floor(point.x / POINT_GRID_M), floor(point.y / POINT_GRID_M))
        grid.setdefault(key, []).append(point)
    return grid


def _near_point(point: Point, grid: dict[tuple[int, int], list[Point]], distance_m: float) -> bool:
    if not grid or distance_m <= 0:
        return False
    cell_x, cell_z = floor(point.x / POINT_GRID_M), floor(point.y / POINT_GRID_M)
    cell_radius = math.ceil(distance_m / POINT_GRID_M)
    for x in range(cell_x - cell_radius, cell_x + cell_radius + 1):
        for z in range(cell_z - cell_radius, cell_z + cell_radius + 1):
            for other in grid.get((x, z), ()):
                if math.hypot(point.x - other.x, point.y - other.y) < distance_m:
                    return True
    return False


def build_street_lamp_locations(
    roadbed: BaseGeometry,
    walkbed: BaseGeometry,
    motor_core: BaseGeometry,
    crossing_cuts: Iterable[BaseGeometry],
    junctions: Iterable[BaseGeometry],
    building_footprints: Iterable[BaseGeometry],
    signal_points: Iterable[Point | Sequence[float]],
    pedestrian_points: Iterable[Point | Sequence[float]],
    *,
    spacing_m: float = DEFAULT_SPACING_M,
    curb_offset_m: float = DEFAULT_CURB_OFFSET_M,
    shared_boundary_tolerance_m: float = DEFAULT_SHARED_BOUNDARY_TOLERANCE_M,
    endpoint_setback_m: float = DEFAULT_ENDPOINT_SETBACK_M,
    pole_radius_m: float = DEFAULT_POLE_RADIUS_M,
    motor_clearance_m: float = 0.15,
    minimum_clear_walk_width_m: float = DEFAULT_MINIMUM_CLEAR_WALK_WIDTH_M,
    crossing_wait_zone_m: float = 2.0,
    junction_clearance_m: float = 2.0,
    building_clearance_m: float = 0.7,
    signal_clearance_m: float = 2.0,
    pedestrian_clearance_m: float = 1.5,
    duplicate_clearance_m: float = 2.0,
) -> dict:
    """Place lamps inside walkbed along continuous road-facing curbs.

    Stations are measured from each canonical curb line's first point, with a
    fixed spacing. A blocked station is recorded as omitted; later stations do
    not shift to hide the gap.
    """
    road = _geometry(roadbed, "roadbed")
    walk = _geometry(walkbed, "walkbed")
    prepare(walk)
    motor = _geometry(motor_core, "motor_core", allow_empty=True)
    numeric = {
        "spacing_m": spacing_m,
        "curb_offset_m": curb_offset_m,
        "shared_boundary_tolerance_m": shared_boundary_tolerance_m,
        "endpoint_setback_m": endpoint_setback_m,
        "pole_radius_m": pole_radius_m,
        "motor_clearance_m": motor_clearance_m,
        "minimum_clear_walk_width_m": minimum_clear_walk_width_m,
        "crossing_wait_zone_m": crossing_wait_zone_m,
        "junction_clearance_m": junction_clearance_m,
        "building_clearance_m": building_clearance_m,
        "signal_clearance_m": signal_clearance_m,
        "pedestrian_clearance_m": pedestrian_clearance_m,
        "duplicate_clearance_m": duplicate_clearance_m,
    }
    for name, value in numeric.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if spacing_m <= 0 or pole_radius_m <= 0:
        raise ValueError("spacing_m and pole_radius_m must be positive")
    # The surfaces are snap-rounded to PRECISION_GRID_M; a floating overlay of two
    # boundary-sharing inputs can report sub-grid noding slivers that do not exist there.
    if road.intersection(walk, grid_size=PRECISION_GRID_M).area > 1e-8:
        raise ValueError("roadbed and walkbed must not overlap")

    crossings = _clearance_zone(crossing_cuts, "crossing_cuts", crossing_wait_zone_m)
    junction_zone = _clearance_zone(junctions, "junctions", junction_clearance_m)
    buildings = _clearance_zone(building_footprints, "building_footprints", building_clearance_m)
    signals = [_point(point, f"signal_points[{index}]")
               for index, point in enumerate(signal_points)]
    pedestrians = [_point(point, f"pedestrian_points[{index}]")
                   for index, point in enumerate(pedestrian_points)]
    signal_index = _point_grid(signals)
    pedestrian_index = _point_grid(pedestrians)
    motor_zone = motor.buffer(motor_clearance_m) if not motor.is_empty and motor_clearance_m > 0 else motor
    for zone in (crossings, junction_zone, buildings, motor_zone):
        prepare(zone)
    boundaries = _street_boundaries(road, walk, shared_boundary_tolerance_m)
    lamps: list[dict] = []
    omitted: list[dict] = []
    omitted_counts: Counter[str] = Counter()
    candidate_count = 0

    for boundary_id, line in boundaries:
        first_station = endpoint_setback_m
        last_station = line.length - endpoint_setback_m
        if last_station < first_station:
            continue
        station = first_station
        while station <= last_station + 1e-9:
            candidate_count += 1
            curb_point = line.interpolate(station)
            source = _source_boundary(boundary_id, station, curb_point)
            inward = _inward_normal(line, station, walk)
            reason: str | None = None
            if inward is None:
                reason = "curb_side_ambiguous"
                lamp_point = curb_point
            else:
                lamp_point = Point(round(curb_point.x + inward[0] * curb_offset_m, 3),
                                   round(curb_point.y + inward[1] * curb_offset_m, 3))
            footprint = lamp_point.buffer(pole_radius_m + PLACEMENT_SAFETY_M, quad_segs=16)
            if reason is None and not walk.covers(footprint):
                reason = "pole_circle_outside_walkbed"
            if reason is None and not motor_zone.is_empty and motor_zone.intersects(footprint):
                reason = "motor_clearance"
            if reason is None and not crossings.is_empty and crossings.intersects(footprint):
                reason = "crossing_or_wait_zone"
            if reason is None and not junction_zone.is_empty and junction_zone.intersects(footprint):
                reason = "junction_clearance"
            if reason is None and not buildings.is_empty and buildings.intersects(footprint):
                reason = "building_clearance"
            if reason is None and _near_point(lamp_point, signal_index,
                                              signal_clearance_m + pole_radius_m + PLACEMENT_SAFETY_M):
                reason = "signal_clearance"
            if reason is None and _near_point(lamp_point, pedestrian_index,
                                              pedestrian_clearance_m + pole_radius_m + PLACEMENT_SAFETY_M):
                reason = "pedestrian_clearance"
            if reason is None and inward is not None and minimum_clear_walk_width_m > 0:
                clear_start = pole_radius_m + PLACEMENT_SAFETY_M
                clear_path = LineString([
                    (lamp_point.x + inward[0] * clear_start,
                     lamp_point.y + inward[1] * clear_start),
                    (lamp_point.x + inward[0] * (clear_start + minimum_clear_walk_width_m),
                     lamp_point.y + inward[1] * (clear_start + minimum_clear_walk_width_m)),
                ])
                if not walk.covers(clear_path):
                    reason = "minimum_clear_walk_width"
            if reason is None and any(math.hypot(lamp_point.x - lamp["x"],
                                                  lamp_point.y - lamp["z"]) < duplicate_clearance_m
                                      for lamp in lamps):
                reason = "duplicate_placement"

            if reason is not None:
                omitted.append({"source_boundary": source,
                                "candidate": [round(lamp_point.x, 3), round(lamp_point.y, 3)],
                                "reason": reason})
                omitted_counts[reason] += 1
            else:
                _, nearest_road = nearest_points(lamp_point, road)
                road_dx, road_dz = nearest_road.x - lamp_point.x, nearest_road.y - lamp_point.y
                if math.hypot(road_dx, road_dz) < 1e-6:
                    omitted.append({"source_boundary": source,
                                    "candidate": [round(lamp_point.x, 3), round(lamp_point.y, 3)],
                                    "reason": "road_direction_undefined"})
                    omitted_counts["road_direction_undefined"] += 1
                else:
                    rotation = math.degrees(math.atan2(road_dz, -road_dx))
                    lamps.append({"x": round(lamp_point.x, 3), "z": round(lamp_point.y, 3),
                                  "rotation_deg": round(rotation, 2),
                                  "source_boundary": source})
            station += spacing_m

    return {
        "street_lamps": lamps,
        "omitted": omitted,
        "stats": {
            "curb_boundary_count": len(boundaries),
            "curb_boundary_length_m": round(sum(line.length for _, line in boundaries), 3),
            "candidate_count": candidate_count,
            "placed_count": len(lamps),
            "omitted_count": len(omitted),
            "omitted_by_reason": dict(sorted(omitted_counts.items())),
            "spacing_m": float(spacing_m),
            "curb_offset_m": float(curb_offset_m),
            "pole_radius_m": float(pole_radius_m),
        },
    }
