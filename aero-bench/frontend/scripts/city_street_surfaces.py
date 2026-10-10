"""Derive conservative, walkable roadside surfaces from verified street geometry.

The returned surfaces are a visual completion of the supplied OSM/SUMO geometry.
They do not assert that a sidewalk or median was surveyed in the source data.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shapely import difference as precision_difference
from shapely import intersection as precision_intersection
from shapely import make_valid, set_precision, union_all
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


SIDEWALK_WIDTH_M = 2.2
BUILDING_CLEARANCE_M = 0.15
VEHICLE_CLEARANCE_M = 0.15
MITRE_JOIN = 2
PRECISION_GRID_M = 0.001
PRECISION_CLEARANCE_MARGIN_M = 0.003
ROAD_WALK_SEPARATION_M = 0.003
PEDESTRIAN_SEAM_MAX_EXTENSION_M = 0.01


def _items(value: BaseGeometry | Iterable[BaseGeometry], name: str) -> list[BaseGeometry]:
    if isinstance(value, BaseGeometry):
        return [value]
    if value is None:
        raise ValueError(f"{name} is required")
    result = list(value)
    if any(not isinstance(item, BaseGeometry) for item in result):
        raise TypeError(f"{name} must contain Shapely geometries")
    return result


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [polygon for item in geometry.geoms for polygon in _polygons(item)]
    return []


def _empty_geometry() -> BaseGeometry:
    return set_precision(GeometryCollection(), grid_size=PRECISION_GRID_M,
                         mode="valid_output")


def _normalize_result(geometry: BaseGeometry, name: str) -> BaseGeometry:
    """Repair overlay results and snap them to the shared millimeter grid."""
    if not isinstance(geometry, BaseGeometry):
        raise TypeError(f"{name} must be a Shapely geometry")
    repaired = geometry if geometry.is_valid else make_valid(geometry)
    snapped = set_precision(repaired, grid_size=PRECISION_GRID_M, mode="valid_output")
    if not snapped.is_valid:
        snapped = set_precision(make_valid(snapped), grid_size=PRECISION_GRID_M,
                                mode="valid_output")
    if not snapped.is_valid:
        raise ValueError(f"{name} could not be normalized to valid geometry")
    return snapped


def _normalize_input(geometry: BaseGeometry, name: str) -> BaseGeometry:
    """Reject invalid caller geometry, then apply the shared precision grid."""
    if not isinstance(geometry, BaseGeometry):
        raise TypeError(f"{name} must be a Shapely geometry")
    if not geometry.is_valid:
        raise ValueError(f"{name} must be valid")
    return _normalize_result(geometry, name)


def _union(geometries: Iterable[BaseGeometry], name: str) -> BaseGeometry:
    items = list(geometries)
    if not items:
        return _empty_geometry()
    result = union_all(items, grid_size=PRECISION_GRID_M)
    return _normalize_result(result, name)


def _intersection(first: BaseGeometry, second: BaseGeometry, name: str) -> BaseGeometry:
    return _normalize_result(
        precision_intersection(first, second, grid_size=PRECISION_GRID_M), name)


def _difference(first: BaseGeometry, second: BaseGeometry, name: str) -> BaseGeometry:
    return _normalize_result(
        precision_difference(first, second, grid_size=PRECISION_GRID_M), name)


def _polygonal_result(geometry: BaseGeometry, name: str) -> BaseGeometry:
    normalized = _normalize_result(geometry, name)
    parts = _polygons(normalized)
    return _union(parts, name) if parts else _empty_geometry()


def _polygon_intersection(first: BaseGeometry, second: BaseGeometry,
                          name: str) -> BaseGeometry:
    polygonal_first = _polygonal_result(first, f"{name} first operand")
    polygonal_second = _polygonal_result(second, f"{name} second operand")
    return _polygonal_result(
        precision_intersection(polygonal_first, polygonal_second,
                               grid_size=PRECISION_GRID_M), name)


def _polygon_difference(first: BaseGeometry, second: BaseGeometry,
                        name: str) -> BaseGeometry:
    polygonal_first = _polygonal_result(first, f"{name} first operand")
    polygonal_second = _polygonal_result(second, f"{name} second operand")
    return _polygonal_result(
        precision_difference(polygonal_first, polygonal_second,
                             grid_size=PRECISION_GRID_M), name)


def _buffer(geometry: BaseGeometry, distance: float, name: str) -> BaseGeometry:
    return _normalize_result(
        geometry.buffer(distance, join_style=MITRE_JOIN), name)


def _polygonal(geometry: BaseGeometry, name: str, *, allow_empty: bool = False) -> BaseGeometry:
    normalized = _normalize_input(geometry, name)
    parts = _polygons(normalized)
    if not parts:
        if allow_empty and normalized.is_empty:
            return _empty_geometry()
        raise ValueError(f"{name} must contain polygonal geometry")
    return _union(parts, name)


def _union_polygon_inputs(
    value: BaseGeometry | Iterable[BaseGeometry], name: str, *, allow_empty: bool = False
) -> BaseGeometry:
    items = _items(value, name)
    if not items:
        if allow_empty:
            return _empty_geometry()
        raise ValueError(f"{name} must contain at least one geometry")
    parts: list[Polygon] = []
    for item in items:
        normalized = _normalize_input(item, name)
        item_polygons = _polygons(normalized)
        parts.extend(item_polygons)
        if not item_polygons and not normalized.is_empty:
            raise ValueError(f"{name} must contain polygonal geometries")
    if not parts:
        if allow_empty:
            return _empty_geometry()
        raise ValueError(f"{name} must contain polygonal geometry")
    return _union(parts, name)


def _union_any(value: Iterable[BaseGeometry], name: str) -> BaseGeometry:
    items = list(value)
    if any(not isinstance(item, BaseGeometry) for item in items):
        raise TypeError(f"{name} must contain Shapely geometries")
    normalized = [_normalize_input(item, name) for item in items]
    return _union(normalized, name)


def _narrow_enclosed_strips(roadbed: BaseGeometry) -> BaseGeometry:
    """Select only long, narrow holes surrounded by roadbed as median candidates."""
    candidates: list[Polygon] = []
    for surface in _polygons(roadbed):
        for ring in surface.interiors:
            hole = Polygon(ring)
            if hole.is_empty or not hole.is_valid or hole.area <= 1.0:
                continue
            rectangle = hole.minimum_rotated_rectangle
            if not isinstance(rectangle, Polygon):
                continue
            corners = list(rectangle.exterior.coords)
            dimensions = sorted(
                LineString((corners[index], corners[index + 1])).length
                for index in range(4)
            )
            narrow, long = dimensions[0], dimensions[-1]
            if narrow <= 5.4 and long >= 15.0 and long / max(narrow, 0.01) >= 4.0:
                candidates.append(hole)
    return _union(candidates, "median strip candidates")


def _round_area(geometry: BaseGeometry) -> float:
    return round(max(0.0, geometry.area), 3)


def build_sidewalk_layout(
    roadbed: BaseGeometry,
    walkbed: BaseGeometry,
    motor_core: BaseGeometry,
    building_footprints: BaseGeometry | Iterable[BaseGeometry],
    eligible_corridors: BaseGeometry | Iterable[BaseGeometry],
    crossing_cuts: Iterable[BaseGeometry],
    *,
    sidewalk_width_m: float = SIDEWALK_WIDTH_M,
    building_clearance_m: float = BUILDING_CLEARANCE_M,
    vehicle_clearance_m: float = VEHICLE_CLEARANCE_M,
) -> dict[str, Any]:
    """Complete eligible road edges with bounded sidewalk and median polygons.

    ``eligible_corridors`` is a polygonal mask built from source OSM road classes
    and lane corridors. It must already extend far enough to cover the intended
    sidewalk width. This module never infers eligibility from an unclassified
    gray surface.

    Source ``walkbed`` is retained. New sidewalk geometry is limited to a
    ``sidewalk_width_m`` band next to ``roadbed``, clipped to the eligible mask,
    and cleared from the motor lane envelope and building footprints. Crossing
    cuts open curb lines only; they do not punch holes through walkable paving.
    """
    for name, value in (
        ("sidewalk_width_m", sidewalk_width_m),
        ("building_clearance_m", building_clearance_m),
        ("vehicle_clearance_m", vehicle_clearance_m),
    ):
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{name} must be a non-negative number")
    if sidewalk_width_m <= 0:
        raise ValueError("sidewalk_width_m must be greater than zero")

    roads = _polygonal(roadbed, "roadbed")
    source_walk = _union_polygon_inputs(walkbed, "walkbed", allow_empty=True)
    vehicles = _polygonal(motor_core, "motor_core", allow_empty=True)
    buildings = _union_polygon_inputs(building_footprints, "building_footprints", allow_empty=True)
    eligible = _union_polygon_inputs(eligible_corridors, "eligible_corridors")
    cuts = _union_any(crossing_cuts, "crossing_cuts")
    road_walk_clearance = _buffer(roads, ROAD_WALK_SEPARATION_M,
                                  "road-walk numerical separation")
    source_walk = _polygon_difference(source_walk, road_walk_clearance,
                                      "source walk outside road-walk separation")

    # A positive buffer around the rendered road footprint creates paired bands
    # on both outer and block-facing edges. Subtraction retains road holes; only
    # narrow, isolated enclosed strips are classified separately below.
    roadside_candidate = _polygon_difference(
        _buffer(roads, sidewalk_width_m, "roadside buffer"), road_walk_clearance,
        "roadside candidate")
    eligible_candidate = _polygon_intersection(roadside_candidate, eligible,
                                               "eligible roadside candidate")
    outside_corridor = _polygon_difference(roadside_candidate, eligible,
                                          "outside eligible corridors")

    source_overlap = _polygon_intersection(eligible_candidate, source_walk,
                                          "source walk overlap")
    after_source_walk = _polygon_difference(eligible_candidate, source_walk,
                                            "remaining after source walk")
    effective_vehicle_clearance = vehicle_clearance_m + PRECISION_CLEARANCE_MARGIN_M
    vehicle_clearance = _buffer(vehicles, effective_vehicle_clearance,
                                "vehicle clearance") \
        if not vehicles.is_empty else vehicles
    vehicle_overlap = _polygon_intersection(after_source_walk, vehicle_clearance,
                                            "vehicle clearance overlap")
    after_vehicle = _polygon_difference(after_source_walk, vehicle_clearance,
                                        "remaining after vehicle clearance")
    effective_building_clearance = building_clearance_m + PRECISION_CLEARANCE_MARGIN_M
    building_clearance = _buffer(buildings, effective_building_clearance,
                                 "building clearance") \
        if not buildings.is_empty else buildings
    building_overlap = _polygon_intersection(after_vehicle, building_clearance,
                                              "building clearance overlap")
    available = _polygon_difference(after_vehicle, building_clearance,
                                    "remaining after building clearance")

    median_region = _narrow_enclosed_strips(roads)
    median_beds = _polygon_intersection(available, median_region, "median beds")
    derived_walkbed = _polygon_difference(available, median_beds, "derived walkbed")
    combined_walkbed = _union([source_walk, derived_walkbed], "combined walkbed")
    adjacent_paving = _union([combined_walkbed, median_beds], "adjacent paving")

    # Build curbs from un-tiled unions so artificial tile edges cannot become
    # street curbs. Keep the crossing surface continuous and remove only its curb.
    curb_lines = _intersection(roads.boundary,
                               _buffer(adjacent_paving, 0.03, "curb adjacency"),
                               "curb lines")
    if not cuts.is_empty:
        curb_lines = _difference(curb_lines, cuts, "curbs with crossing cuts")

    accounted = _union([source_overlap, derived_walkbed, median_beds], "accounted roadside")
    unclassified = _polygon_difference(roadside_candidate, accounted, "unclassified roadside")
    return {
        "roadbed": roads,
        "walkbed": combined_walkbed,
        "derived_walkbed": derived_walkbed,
        "median_beds": median_beds,
        "curb_lines": curb_lines,
        "provenance": {
            "source_kind": "derived_osm_eligible_corridor_visual_geometry",
            "sidewalk_kind": "derived_paved_sidewalk_not_surveyed",
            "median_kind": "derived_paved_narrow_enclosed_strip_not_surveyed",
            "sumo_lane_topology_modified": False,
            "surface_precision_normalized": True,
            "crossing_cuts_affect": "curb_lines_only",
        },
        "stats": {
            "sidewalk_width_m": float(sidewalk_width_m),
            "building_clearance_m": float(building_clearance_m),
            "vehicle_clearance_m": float(vehicle_clearance_m),
            "effective_building_clearance_m": float(effective_building_clearance),
            "effective_vehicle_clearance_m": float(effective_vehicle_clearance),
            "precision_grid_m": PRECISION_GRID_M,
            "precision_clearance_margin_m": PRECISION_CLEARANCE_MARGIN_M,
            "road_walk_separation_m": ROAD_WALK_SEPARATION_M,
            "raw_roadbed_area_m2": _round_area(roadbed),
            "roadbed_normalization_symmetric_difference_m2": _round_area(roadbed.symmetric_difference(roads)),
            "normalized_roadbed_area_m2": _round_area(roads),
            "normalized_source_walkbed_area_m2": _round_area(source_walk),
            "roadside_candidate_area_m2": _round_area(roadside_candidate),
            "eligible_candidate_area_m2": _round_area(eligible_candidate),
            "outside_eligible_corridor_area_m2": _round_area(outside_corridor),
            "source_walkbed_overlap_area_m2": _round_area(source_overlap),
            "vehicle_clearance_excluded_area_m2": _round_area(vehicle_overlap),
            "building_clearance_excluded_area_m2": _round_area(building_overlap),
            "derived_walkbed_area_m2": _round_area(derived_walkbed),
            "median_beds_area_m2": _round_area(median_beds),
            "unclassified_remaining_area_m2": _round_area(unclassified),
            "curb_length_m": round(curb_lines.length, 3),
        },
    }


def complete_pedestrian_port_paving(roadbed: BaseGeometry, walkbed: BaseGeometry,
                                     corridors: Iterable[BaseGeometry],
                                     motor_corridors: BaseGeometry,
                                     buildings: BaseGeometry) -> dict[str, Any]:
    """Pave missing portions of declared SUMO pedestrian connection corridors.

    Only space outside motor clearance and building clearance can become raised
    paving. The caller must still audit each complete path against the final
    tiled surfaces; this function does not approve a partly blocked connection.
    """
    roads = _polygonal(roadbed, "roadbed")
    walks = _union_polygon_inputs(walkbed, "walkbed", allow_empty=True)
    paths = _union_polygon_inputs(corridors, "pedestrian connection corridors", allow_empty=True)
    vehicles = _union_polygon_inputs(motor_corridors, "motor corridors", allow_empty=True)
    structures = _union_polygon_inputs(buildings, "buildings", allow_empty=True)
    candidate = _polygon_difference(paths, _union([roads, walks], "existing paving"),
                                    "missing pedestrian paving")
    exclusion = _union([
        _buffer(roads, ROAD_WALK_SEPARATION_M, "road separation"),
        _buffer(vehicles, VEHICLE_CLEARANCE_M + PRECISION_CLEARANCE_MARGIN_M, "vehicle clearance"),
        _buffer(structures, BUILDING_CLEARANCE_M + PRECISION_CLEARANCE_MARGIN_M, "building clearance"),
    ], "pedestrian paving exclusions")
    addition = _polygon_difference(candidate, exclusion, "pedestrian port paving")
    return {"walkbed": _union([walks, addition], "completed pedestrian paving"),
            "added_walkbed": addition,
            "provenance": {"source_kind": "sumo-connection-port-derived-paving-not-surveyed",
                           "surveyed": False, "added_area_m2": _round_area(addition),
                           "candidate_area_m2": _round_area(candidate),
                           "motor_clearance_m": VEHICLE_CLEARANCE_M + PRECISION_CLEARANCE_MARGIN_M,
                           "building_clearance_m": BUILDING_CLEARANCE_M + PRECISION_CLEARANCE_MARGIN_M}}


def close_pedestrian_port_seams(roadbed: BaseGeometry, walkbed: BaseGeometry,
                                corridors: Iterable[BaseGeometry],
                                buildings: BaseGeometry) -> dict[str, Any]:
    """Close separators inside connection corridors without moving existing edges.

    Use one floating-precision overlay throughout. Snapping an intermediate copy
    of the existing road can heal a hole in the exclusion mask while leaving
    that same hole in the original road returned to the caller.
    """
    inputs = [roadbed, walkbed, buildings, *list(corridors)]
    if any(not isinstance(item, BaseGeometry) or not item.is_valid for item in inputs):
        raise ValueError("Pedestrian seam inputs must be valid geometries")
    roads, walks, structures, *paths = [set_precision(item, 0) for item in inputs]
    seam_width = PEDESTRIAN_SEAM_MAX_EXTENSION_M
    band = roads.buffer(seam_width)
    candidate = union_all(paths, grid_size=0).intersection(band)
    excluded = union_all([walks.buffer(PRECISION_GRID_M),
        structures.buffer(BUILDING_CLEARANCE_M + PRECISION_CLEARANCE_MARGIN_M)], grid_size=0)
    patch = candidate.difference(excluded)
    completed = union_all([roads, patch], grid_size=0)
    addition = completed.difference(roads)
    return {"roadbed": completed, "added_roadbed": addition,
            "provenance": {"source_kind": "derived-pedestrian-port-seam-completion",
                           "maximum_road_edge_extension_m": seam_width,
                           "walking_edge_separation_m": PRECISION_GRID_M,
                           "added_area_m2": _round_area(addition), "surveyed": False}}
