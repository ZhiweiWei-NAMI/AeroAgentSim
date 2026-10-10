#!/usr/bin/env python3
"""Build presentation road geometry from the SUMO network used by the city demo.

This is a visual asset. It does not modify the SUMO network or any formal trace.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import argparse
from dataclasses import dataclass, replace
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from pyproj import CRS, Transformer
from shapely import affinity, make_valid, set_precision
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from city_preview_scene import ROOT, read_scene_paths
from city_road_topology import crossing_junction_id, lane_kind, visible_crossing_junctions
from city_effective_fixtures import MOTOR_TOP_UP_M, POLE_CLEARANCE_M
from city_fixture_geometry import displayed_fixture_triangles, fixture_in_height_band, glb_triangles
from city_street_lamps import PLACEMENT_SAFETY_M, build_street_lamp_locations
from city_pedestrian_connections import (build_pedestrian_connections, audit_crossing_endpoints,
    plan_pedestrian_connection_paths, build_pedestrian_connection_paths,
    audit_pedestrian_walkbed_landings)
from city_surface_identity import displayed_surface_sha256
from city_preview_coordinates import CityEnuProjection, load_canonical_city_geometry
from city_native_signals import canonical_signal_inventory
from city_road_physical_clearance import (LANE_CENTRE_LINE_DECIMALS, internal_lane_neighbours, lane_building_conflicts,
                                          lane_ribbon, surface_lane_coverage)
from city_street_surfaces import (ROAD_WALK_SEPARATION_M, build_sidewalk_layout,
    complete_pedestrian_port_paving, close_pedestrian_port_seams)
from city_street_markings import build_street_markings, clip_street_markings_to_roadbed


CONCRETE_ROAD_TEXTURE = "/osm2world/style/textures/cc0textures/Concrete034/Concrete034_Color.jpg"
CONCRETE_TEXTURE_REPEAT_M = (1.2, 0.6)
MOTOR_ROAD_HIGHWAYS = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road", "bus_guideway",
})
MARKING_WIDTHS_M = {"lane_edge": 0.14, "lane_divider": 0.13, "derived_direction_guide": 0.15}
MOTOR_DIRECTION_GUIDE_HIGHWAYS = frozenset({
    "primary", "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential",
})
MAX_SHARED_BOUNDARY_GAP_M = 0.15
MOTOR_PAVEMENT_SHOULDER_M = 0.153
BUILDING_EDGE_CLEARANCE_M = 0.01


@dataclass(frozen=True)
class BuildInputs:
    network: Path
    source_osm: Path
    traffic: Path | None
    pack: Path
    road_surface_texture: str
    placement: Path | None
    output: Path
    tile_size_m: int
    static_footprints: tuple | None = None
    static_signals: tuple[dict, ...] | None = None
    static_signal_inventory_sha256: str | None = None
    engineering_inputs: Path | None = None
    building_objects: Path | None = None
    building_render_manifest: Path | None = None


def read_inputs(scene_path: Path, network_path: Path, tile_size_m: int,
                source_osm_path: Path | None = None, pack_manifest: Path | None = None) -> BuildInputs:
    paths = read_scene_paths(scene_path, pack_manifest=pack_manifest)
    if not isinstance(tile_size_m, int) or tile_size_m < 10 or tile_size_m > 200:
        raise ValueError("Roadbed tile size must be 10–200 metres")
    if source_osm_path is None:
        engineering = json.loads((network_path.parent / "engineering-inputs.json").read_text())
        source_osm_path = Path(engineering["source_osm"])
        if not source_osm_path.is_absolute():
            source_osm_path = ROOT / source_osm_path
    return BuildInputs(network_path, source_osm_path, paths.traffic, paths.pack, paths.road_surface_texture, paths.placement,
                       paths.road, tile_size_m)


def shape(value: str | None, transformer: Transformer, origin: dict[str, float]) -> list[list[float]]:
    if not value:
        return []
    radians = math.pi / 180
    circumference = 40075016.686
    scale = circumference * math.cos(origin["latitude_deg"] * radians)

    def mercator_y(latitude: float) -> float:
        sine = math.sin(latitude * radians)
        return math.log((1 + sine) / (1 - sine)) / (4 * math.pi) + 0.5

    points = []
    for token in value.split():
        east, north = [float(part) for part in token.split(",")[:2]]
        longitude, latitude = transformer.transform(east, north)
        x = scale * (longitude - origin["longitude_deg"]) / 360
        z = -scale * (mercator_y(latitude) - mercator_y(origin["latitude_deg"]))
        points.append([round(x, 2), round(z, 2)])
    if not points:
        return []
    result = [points[0]]
    for point in points[1:]:
        if point != result[-1]:
            result.append(point)
    return result


def ring(points) -> list[list[float]]:
    result = [[float(x), float(z)] for x, z in list(points)[:-1]]
    if len(result) < 3:
        raise ValueError("Roadbed contains a collapsed polygon ring")
    return result


def clip_source_buildings(surface, footprints):
    """Keep rounded surface tiles clear of the source roof boundary."""
    return surface.difference(footprints.buffer(BUILDING_EDGE_CLEARANCE_M))


def polygons_of(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        for part in geometry.geoms:
            yield from polygons_of(part)


def tiled_polygons(surface, tile_size: int) -> list[dict]:
    if surface.is_empty:
        return []
    surface = set_precision(surface, 0)
    min_x, min_z, max_x, max_z = surface.bounds
    parts = []
    for east in range(math.floor(min_x / tile_size) * tile_size,
                      math.ceil(max_x / tile_size) * tile_size, tile_size):
        for south in range(math.floor(min_z / tile_size) * tile_size,
                           math.ceil(max_z / tile_size) * tile_size, tile_size):
            tile = set_precision(surface.intersection(
                box(east, south, east + tile_size, south + tile_size), grid_size=0), 0)
            for polygon in polygons_of(tile):
                if polygon.area <= 1e-8:
                    continue
                parts.append({"outline": ring(polygon.exterior.coords),
                              "holes": [ring(interior.coords) for interior in polygon.interiors]})
    return parts


def lines_of(geometry):
    if geometry.geom_type == "LineString":
        yield geometry
    elif geometry.geom_type in ("MultiLineString", "GeometryCollection"):
        for part in geometry.geoms:
            yield from lines_of(part)


def osm_pack_identity(osm_id: str) -> tuple[str, float]:
    """Normalize the large integer IDs that OSM2World stringifies through JS Number."""
    match = re.fullmatch(r"([nwr])(-?\d+)", osm_id)
    if match is None:
        raise ValueError(f"OSM2World pack target has an invalid object id: {osm_id}")
    return match.group(1), float(int(match.group(2)))


def pack_object_tags(pack: dict) -> dict[tuple[str, float], dict[str, str]]:
    result = {}
    for item in pack["objects"]:
        identity = osm_pack_identity(item["id"])
        if identity in result:
            raise ValueError(f"OSM2World object IDs collide after numeric serialization: {item['id']}")
        result[identity] = item["tags"]
    return result


def selected_road_triangles(batch: dict, allowed_targets: set[tuple[str, float]] | None):
    """Select triangle ranges by source identity, never by overlapping XY bounds."""
    count = batch["indices"] // 3
    if allowed_targets is None:
        return np.arange(count)
    selected = []
    start = 0
    for item in batch["ranges"]:
        end, target = item["end"], item["target"]
        if not isinstance(end, int) or not start < end <= count:
            raise ValueError("Road mesh ranges do not partition triangles")
        if target is not None and target["kind"] == "road" and osm_pack_identity(target["id"]) in allowed_targets:
            selected.extend(range(start, end))
        start = end
    if start != count:
        raise ValueError("Road mesh ranges omit triangles")
    return np.asarray(selected, dtype=np.int64)


def horizontal_mesh_triangles(pack_path: Path, batch: dict,
                              allowed_targets: set[tuple[str, float]] | None = None,
                              point_transform=None) -> list[Polygon]:
    chunk = batch["file"]
    raw = (pack_path.parent / "assets" / chunk["sha256"]).read_bytes()
    if len(raw) != chunk["size_bytes"] or hashlib.sha256(raw).hexdigest() != chunk["sha256"]:
        raise ValueError("OSM2World road surface mesh differs from the verified city pack")
    vertex_count = batch["vertices"]
    index_count = batch["indices"]
    positions = np.frombuffer(raw, dtype="<f4", count=vertex_count * 3).reshape(vertex_count, 3)
    indices = np.frombuffer(raw, dtype="<u4", count=index_count,
                            offset=vertex_count * 32).reshape(-1, 3)
    surfaces = []
    for triangle in positions[indices[selected_road_triangles(batch, allowed_targets)]]:
        if np.ptp(triangle[:, 1]) > 0.01 or np.max(triangle[:, 1]) > 0.2:
            continue
        polygon = Polygon([point_transform(float(x), float(z)) if point_transform is not None
                           else (float(x), float(z)) for x, _, z in triangle])
        if polygon.area > 1e-6:
            surfaces.append(polygon)
    return surfaces


def concrete_motor_road_triangles(pack_path: Path, batch: dict,
                                  object_tags: dict[tuple[str, float], dict[str, str]],
                                  road_junction_nodes: set[tuple[str, float]],
                                  allowed_targets: set[tuple[str, float]] | None = None,
                                  point_transform=None) \
        -> tuple[list[Polygon], set[str], set[str], int]:
    """Read only source Concrete034 faces assigned to OSM motor-road ways.

    The pack's ranges are triangle ranges. `highway=steps`, footways, and paving
    stones are deliberately excluded; lane buffers do not supply this footprint.
    """
    chunk = batch["file"]
    raw = (pack_path.parent / "assets" / chunk["sha256"]).read_bytes()
    if len(raw) != chunk["size_bytes"] or hashlib.sha256(raw).hexdigest() != chunk["sha256"]:
        raise ValueError("OSM2World concrete road mesh differs from the verified city pack")
    vertex_count, index_count = batch["vertices"], batch["indices"]
    positions = np.frombuffer(raw, dtype="<f4", count=vertex_count * 3).reshape(vertex_count, 3)
    indices = np.frombuffer(raw, dtype="<u4", count=index_count,
                            offset=vertex_count * 32).reshape(-1, 3)
    if index_count % 3:
        raise ValueError("OSM2World concrete road mesh has a non-triangle index inventory")

    surfaces: list[Polygon] = []
    source_way_ids: set[str] = set()
    source_junction_node_ids: set[str] = set()
    source_junction_triangles = 0
    start_triangle = 0
    for item in batch["ranges"]:
        end_triangle = item["end"]
        if not isinstance(end_triangle, int) or end_triangle <= start_triangle \
                or end_triangle > index_count // 3:
            raise ValueError("OSM2World concrete ranges do not partition their triangle inventory")
        target = item["target"]
        if allowed_targets is not None and (target is None or osm_pack_identity(target["id"]) not in allowed_targets):
            start_triangle = end_triangle
            continue
        if target is not None and target["kind"] == "road" and target["id"].startswith("w"):
            identity = osm_pack_identity(target["id"])
            tags = object_tags.get(identity)
            if tags is None:
                raise ValueError(f"Concrete road target has no source OSM tags: {target['id']}")
            if tags.get("highway") in MOTOR_ROAD_HIGHWAYS:
                source_way_ids.add(target["id"])
                for triangle in positions[indices[start_triangle:end_triangle]]:
                    if np.ptp(triangle[:, 1]) > 0.01 or np.max(triangle[:, 1]) > 0.2:
                        continue
                    polygon = Polygon([point_transform(float(x), float(z)) if point_transform is not None
                                       else (float(x), float(z)) for x, _, z in triangle])
                    if polygon.area > 1e-6:
                        surfaces.append(polygon)
        elif target is not None and target["kind"] == "road" and target["id"].startswith("n"):
            identity = osm_pack_identity(target["id"])
            if identity in road_junction_nodes:
                source_junction_node_ids.add(target["id"])
                for triangle in positions[indices[start_triangle:end_triangle]]:
                    if np.ptp(triangle[:, 1]) > 0.01 or np.max(triangle[:, 1]) > 0.2:
                        continue
                    polygon = Polygon([point_transform(float(x), float(z)) if point_transform is not None
                                       else (float(x), float(z)) for x, _, z in triangle])
                    if polygon.area > 1e-6:
                        surfaces.append(polygon)
                        source_junction_triangles += 1
        start_triangle = end_triangle
    if start_triangle != index_count // 3:
        raise ValueError("OSM2World concrete ranges omit road surface triangles")
    return surfaces, source_way_ids, source_junction_node_ids, source_junction_triangles


def road_junction_node_ids(source_osm_root: ET.Element) -> set[tuple[str, float]]:
    """Identify source OSM nodes shared by at least two drivable road ways."""
    incident_ways: dict[str, set[str]] = {}
    for way in source_osm_root.findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        if tags.get("highway") not in MOTOR_ROAD_HIGHWAYS:
            continue
        for reference in way.findall("nd"):
            node_id = reference.attrib["ref"]
            incident_ways.setdefault(node_id, set()).add(way.attrib["id"])
    return {osm_pack_identity(f"n{node_id}") for node_id, ways in incident_ways.items() if len(ways) >= 2}


def city_road_surfaces(pack_path: Path, pack: dict, asphalt_texture: str,
                       source_osm_root: ET.Element,
                       allowed_targets: set[tuple[str, float]] | None = None,
                       point_transform=None
                       ) -> tuple[list[Polygon], list[Polygon], set[str], set[str], int]:
    """Extract verified asphalt and tagged concrete traffic-road footprints."""
    asphalt: list[Polygon] = []
    concrete: list[Polygon] = []
    concrete_way_ids: set[str] = set()
    concrete_junction_node_ids: set[str] = set()
    concrete_junction_triangles = 0
    object_tags = pack_object_tags(pack)
    junction_nodes = road_junction_node_ids(source_osm_root)
    for batch in pack["batches"]:
        if batch["layer"] != "roads":
            continue
        texture = batch["material"]["base_color_texture"]
        if texture == asphalt_texture:
            asphalt.extend(horizontal_mesh_triangles(pack_path, batch, allowed_targets, point_transform))
        elif texture == CONCRETE_ROAD_TEXTURE:
            faces, source_way_ids, junction_ids, junction_triangles = concrete_motor_road_triangles(
                pack_path, batch, object_tags, junction_nodes, allowed_targets, point_transform)
            concrete.extend(faces)
            concrete_way_ids.update(source_way_ids)
            concrete_junction_node_ids.update(junction_ids)
            concrete_junction_triangles += junction_triangles
    if not asphalt and not concrete:
        raise ValueError("Verified city mesh pack has no horizontal asphalt or tagged concrete motor-road footprint")
    return asphalt, concrete, concrete_way_ids, concrete_junction_node_ids, concrete_junction_triangles


def pack_texture_asset(pack_path: Path, pack: dict, texture_path: str,
                       *, private_candidate: bool = False) -> dict | None:
    """Return the verified public pack URL for a texture declared by the pack manifest."""
    reference = pack["textures"].get(texture_path)
    if reference is None:
        return None
    asset_path = pack_path.parent / "assets" / reference["sha256"]
    raw = asset_path.read_bytes()
    if len(raw) != reference["size_bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError(f"OSM2World source texture differs from the verified city pack: {texture_path}")
    if private_candidate:
        return {"sha256": reference["sha256"], "size_bytes": reference["size_bytes"]}
    public_root = ROOT / "frontend" / "public"
    try:
        public_path = asset_path.relative_to(public_root).as_posix()
    except ValueError as error:
        raise ValueError("Verified pack texture is outside the public asset root") from error
    return {"url": f"/{public_path}", "sha256": reference["sha256"],
            "size_bytes": reference["size_bytes"]}


def edge_source_way_id(edge_id: str, source_way_ids: set[str]) -> tuple[str, str]:
    """Resolve a SUMO directional edge id to its exact OSM input way id."""
    base, separator, segment = edge_id.partition("#")
    # SUMO prepends one '-' to mark reverse direction. The input may itself
    # use positive or deterministic negative way IDs, so resolve by membership.
    candidates = [f"w{base}"]
    if base.startswith("-"):
        candidates.append(f"w{base[1:]}")
    matches = [candidate for candidate in candidates if candidate in source_way_ids]
    if len(matches) != 1:
        raise ValueError(f"SUMO edge cannot be mapped uniquely to a source OSM way: {edge_id}")
    source_id = matches[0]
    return source_id, segment if separator else ""


def ground_mesh_targets(network: ET.Element, osm: ET.Element) -> set[tuple[str, float]]:
    """Keep source ways used by the reduced network and unmixed junction meshes.

    Source junction meshes touching removed ways are replaced by the rebuilt
    SUMO junction polygon, otherwise flattened bridge stubs would remain visible.
    """
    ways = {f"w{way.attrib['id']}": way for way in osm.findall("way")}
    retained = {edge_source_way_id(edge.attrib["id"], set(ways))[0]
                for edge in network.findall("edge") if edge.get("function", "normal") == "normal"}
    for identity in retained:
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in ways[identity].findall("tag")}
        if any(tags.get(key, "no").lower() not in ("", "no", "false", "0", "off")
               for key in ("bridge", "tunnel")) or float(tags.get("layer", "0")) != 0:
            raise ValueError(f"Ground-only network still contains a bridge/tunnel/layered way: {identity}")
    nodes: dict[str, set[str]] = {}
    for identity, way in ways.items():
        if not any(tag.get("k") == "highway" for tag in way.findall("tag")):
            continue
        for reference in way.findall("nd"):
            nodes.setdefault(reference.attrib["ref"], set()).add(identity)
    targets = {osm_pack_identity(identity) for identity in retained}
    targets.update(osm_pack_identity(f"n{node}") for node, incident in nodes.items()
                   if incident and incident.issubset(retained))
    return targets


def midpoint_line(first: list[list[float]], reverse: list[list[float]],
                  first_width: float, reverse_width: float) -> tuple[list[list[float]], float]:
    """Interpolate opposing boundaries and measure their maximum separation.

    The sample fractions include both polylines' vertices. Between adjacent
    fractions both centerlines are linear, so their relative vector is affine;
    adding its minimum-distance point captures the only interior extremum needed
    to bound the absolute separation of the two width-offset boundaries.
    """
    a, b = LineString(first), LineString(reverse)
    total_width = first_width + reverse_width
    if first_width <= 0 or reverse_width <= 0 or not math.isfinite(total_width):
        raise ValueError("Direction guide lane widths must be positive and finite")
    if a.length <= 0 or b.length <= 0:
        return [], math.inf
    sample_count = max(2, math.ceil(max(a.length, b.length) / 5), len(first) - 1, len(reverse) - 1)
    fractions = {index / sample_count for index in range(sample_count + 1)}

    def vertex_fractions(line: LineString) -> list[float]:
        distances = [0.0]
        for start, end in zip(line.coords, line.coords[1:]):
            distances.append(distances[-1] + math.dist(start[:2], end[:2]))
        return [distance / line.length for distance in distances]

    fractions.update(vertex_fractions(a))
    fractions.update(1 - fraction for fraction in vertex_fractions(b))

    # On each interval delimited by a vertex from either curve, both curves are
    # linear under normalized arc-length interpolation. Add the interior point
    # that minimizes center distance; boundary separation can attain its maximum
    # there when the lane envelopes overlap.
    breakpoints = sorted(fractions)
    for start_fraction, end_fraction in zip(breakpoints, breakpoints[1:]):
        left_start = a.interpolate(start_fraction, normalized=True)
        right_start = b.interpolate(1 - start_fraction, normalized=True)
        left_end = a.interpolate(end_fraction, normalized=True)
        right_end = b.interpolate(1 - end_fraction, normalized=True)
        dx_start = right_start.x - left_start.x
        dz_start = right_start.y - left_start.y
        dx_delta = ((right_end.x - left_end.x) - dx_start)
        dz_delta = ((right_end.y - left_end.y) - dz_start)
        delta_length_squared = dx_delta * dx_delta + dz_delta * dz_delta
        if delta_length_squared <= 1e-12:
            continue
        local_fraction = -(dx_start * dx_delta + dz_start * dz_delta) / delta_length_squared
        if 0 < local_fraction < 1:
            fractions.add(start_fraction + local_fraction * (end_fraction - start_fraction))

    result = []
    maximum_gap = 0.0
    for fraction in sorted(fractions):
        left = a.interpolate(fraction, normalized=True)
        right = b.interpolate(1 - fraction, normalized=True)
        dx, dz = right.x - left.x, right.y - left.y
        center_distance = math.hypot(dx, dz)
        if center_distance < 1e-6:
            return [], math.inf
        ux, uz = dx / center_distance, dz / center_distance
        first_boundary = (left.x + ux * first_width / 2, left.y + uz * first_width / 2)
        reverse_boundary = (right.x - ux * reverse_width / 2, right.y - uz * reverse_width / 2)
        boundary_gap = math.dist(first_boundary, reverse_boundary)
        maximum_gap = max(maximum_gap, boundary_gap)
        point = [round((first_boundary[0] + reverse_boundary[0]) / 2, 2),
                 round((first_boundary[1] + reverse_boundary[1]) / 2, 2)]
        if not result or point != result[-1]:
            result.append(point)
    return result, maximum_gap


def bidirectional_direction_guides(edges: list[dict], source_way_highways: dict[str, str]) -> list[dict]:
    """Derive non-surveyed dashed visual guides from same-way SUMO lane topology.

    Different OSM ways are never paired, which prevents separated carriageways
    from receiving a false guide across a median or green strip.
    """
    by_way_and_nodes: dict[tuple[str, str, str], list[dict]] = {}
    source_way_ids = set(source_way_highways)
    for edge in edges:
        if edge["function"] != "normal":
            continue
        way_id, segment = edge_source_way_id(edge["id"], source_way_ids)
        if source_way_highways[way_id] not in MOTOR_DIRECTION_GUIDE_HIGHWAYS:
            continue
        by_way_and_nodes.setdefault((way_id, segment, edge["from"]), []).append(edge)
    result = []
    seen: set[tuple[str, str, str, str]] = set()
    for edge in edges:
        if edge["function"] != "normal":
            continue
        way_id, segment = edge_source_way_id(edge["id"], source_way_ids)
        if source_way_highways[way_id] not in MOTOR_DIRECTION_GUIDE_HIGHWAYS:
            continue
        source, destination = edge["from"], edge["to"]
        pair_key = (way_id, segment, min(source, destination), max(source, destination))
        if pair_key in seen:
            continue
        seen.add(pair_key)
        opposing = [candidate for candidate in by_way_and_nodes.get((way_id, segment, destination), [])
                    if candidate["to"] == source and candidate["id"] != edge["id"]]
        if len(opposing) != 1:
            continue
        other = opposing[0]
        first_lanes = sorted((lane for lane in edge["lanes"]
                              if lane["kind"] == "motor"), key=lambda lane: lane["index"])
        reverse_lanes = sorted((lane for lane in other["lanes"]
                                if lane["kind"] == "motor"), key=lambda lane: lane["index"])
        if not first_lanes or not reverse_lanes:
            continue
        first_lane, reverse_lane = first_lanes[-1], reverse_lanes[-1]
        if first_lane["width"] < 2.5 or reverse_lane["width"] < 2.5:
            continue
        first_line, reverse_line = LineString(first_lane["shape"]), LineString(reverse_lane["shape"])
        if min(first_line.length, reverse_line.length) < 6:
            continue
        if not 0.75 <= first_line.length / reverse_line.length <= 1.33:
            continue
        endpoint_error = max(Point(first_line.coords[0]).distance(Point(reverse_line.coords[-1])),
                             Point(first_line.coords[-1]).distance(Point(reverse_line.coords[0])))
        if endpoint_error > 8 or first_line.hausdorff_distance(reverse_line) > 8:
            continue
        shape, boundary_gap = midpoint_line(first_lane["shape"], reverse_lane["shape"],
                                            first_lane["width"], reverse_lane["width"])
        if len(shape) < 2 or LineString(shape).length < 6:
            continue
        if boundary_gap > MAX_SHARED_BOUNDARY_GAP_M:
            continue
        centerline = LineString(shape)
        shared_corridor = first_line.buffer(first_lane["width"] / 2 + 0.2).intersection(
            reverse_line.buffer(reverse_lane["width"] / 2 + 0.2))
        if centerline.difference(shared_corridor).length > max(0.5, centerline.length * 0.02):
            continue
        result.append({"id": f"direction-guide:{way_id}:{segment or '0'}",
                       "source_way_id": way_id,
                       "edge_ids": sorted([edge["id"], other["id"]]),
                       "marking": "derived_direction_guide",
                       "source_kind": "sumo-lane-topology-not-surveyed-marking",
                       "width_m": MARKING_WIDTHS_M["derived_direction_guide"],
                       "shape": shape})
    return sorted(result, key=lambda item: item["id"])


STREET_LAMP_MODEL = ROOT / "frontend/public/models/incoming/furniture/glb/street_light_8.glb"


def street_lamp_curb_offset() -> dict:
    """Curb offset at which the displayed lamp's motor-height-band geometry keeps the
    effective-fixture pole clearance from the roadbed.

    The displayed lamp arm points along -x toward the road, so the roadward reach is the
    negated minimum x of the model clipped to the declared motor height band.
    """
    displayed = displayed_fixture_triangles(glb_triangles(STREET_LAMP_MODEL), "street_lamp")
    reach = -fixture_in_height_band(displayed, 0, MOTOR_TOP_UP_M).bounds[0]
    return {"curb_offset_m": math.ceil((reach + POLE_CLEARANCE_M + PLACEMENT_SAFETY_M) * 1000) / 1000,
            "measured_motor_band_roadward_reach_m": round(reach, 4),
            "motor_height_band_up_m": [0, MOTOR_TOP_UP_M], "pole_clearance_m": POLE_CLEARANCE_M,
            "placement_safety_m": PLACEMENT_SAFETY_M, "model": "street_light_8.glb"}


def build(inputs: BuildInputs) -> dict:
    static = inputs.static_footprints is not None
    if static != (inputs.static_signals is not None):
        raise ValueError("Static roads require both source building footprints and network signals")
    if static and (inputs.static_signal_inventory_sha256 is None or
                   re.fullmatch(r"[0-9a-f]{64}", inputs.static_signal_inventory_sha256) is None):
        raise ValueError("Static roads require a verified signal inventory digest")
    network_bytes = inputs.network.read_bytes()
    network_sha = hashlib.sha256(network_bytes).hexdigest()
    source_osm_bytes = inputs.source_osm.read_bytes()
    source_osm_sha = hashlib.sha256(source_osm_bytes).hexdigest()
    engineering = json.loads((inputs.engineering_inputs or
                              inputs.network.parent / "engineering-inputs.json").read_text())
    pack_bytes = inputs.pack.read_bytes()
    pack = json.loads(pack_bytes)
    if engineering.get("network_sha256") != network_sha:
        raise ValueError("SUMO road source differs from its engineering input receipt")
    if source_osm_sha != engineering["source_osm_sha256"]:
        raise ValueError("SUMO crossing provenance differs from its source OSM")
    geometry = None
    source_context = None
    if static:
        footprints = list(inputs.static_footprints)
        signals = list(inputs.static_signals)
    else:
        if inputs.building_objects is None or inputs.building_render_manifest is None:
            raise ValueError("Canonical roads require actual building objects and render manifest; placement proxies are obsolete")
        geometry = load_canonical_city_geometry(inputs.building_objects, inputs.building_render_manifest, inputs.pack)
        source_context = geometry.source_context(inputs.network, inputs.source_osm, inputs.engineering_inputs)
        footprints = [item.rendered_polygon for item in geometry.footprints]
    if not footprints or any(part.is_empty or not part.is_valid for part in footprints):
        raise ValueError("Verified city pack contains no valid building footprints")
    root = ET.fromstring(network_bytes)
    source_root = ET.fromstring(source_osm_bytes)
    road_scope = engineering.get("road_scope", "all-source-roads")
    if road_scope not in ("ground-only", "all-source-roads"):
        raise ValueError(f"Unknown engineering road scope: {road_scope}")
    allowed_mesh_targets = ground_mesh_targets(root, source_root) if road_scope == "ground-only" else None
    visible_crossing_nodes = visible_crossing_junctions(root, source_root)
    projection = root.find("location")
    if projection is None or not projection.get("projParameter"):
        raise ValueError("SUMO network has no projection for OSM2World alignment")
    transformer = Transformer.from_crs(CRS.from_proj4(projection.attrib["projParameter"]),
                                       CRS.from_epsg(4326), always_xy=True)
    origin = pack["projection"]["origin"]
    # Surfaces are snap-rounded to PRECISION_GRID_M (1 mm); displayed walking-area, crossing and
    # junction polygons use the same resolution, lane centre lines LANE_CENTRE_LINE_DECIMALS.
    # Signals are the canonical inventory shared with the effective fixtures and the recording.
    canonical_projection = CityEnuProjection(root, geometry.origin, decimals=3) if geometry is not None else None
    if canonical_projection is not None:
        signals = canonical_signal_inventory(root, CityEnuProjection(root, geometry.origin))
    project_shape = canonical_projection.shape if canonical_projection is not None else lambda value: shape(value, transformer, origin)
    project_centre_line = CityEnuProjection(root, geometry.origin, decimals=LANE_CENTRE_LINE_DECIMALS).shape \
        if geometry is not None else project_shape
    lanes: list[dict] = []
    native_ribbons: list[dict] = []
    road_edges: list[dict] = []
    crossings: list[dict] = []
    crossing_cuts = []
    omitted_unmarked_crossings: list[str] = []
    lane_kinds: dict[str, str] = {}
    road_surfaces = []
    walk_surfaces = []
    internal_lane_count = 0
    walking_area_records = []
    walking_area_count = 0
    walking_area_source_count = sum(edge.get("function") == "walkingarea" for edge in root.findall("edge"))
    omitted_walking_areas = []
    lane_shapes = {lane.get("id"): project_centre_line(lane.get("shape")) for lane in root.iter("lane")}
    neighbours = internal_lane_neighbours(root)
    for edge in root.findall("edge"):
        function = edge.get("function", "normal")
        edge_lanes: list[dict] = []
        for lane in edge.findall("lane"):
            points = (project_centre_line if function in ("normal", "internal") else project_shape)(lane.get("shape"))
            width = round(float(lane.get("width", "3.2")), 2)
            if width <= 0:
                raise ValueError(f"SUMO lane has invalid width: {lane.get('id')}")
            entry_shape, exit_shape = (lane_shapes[item] for item in neighbours[lane.get("id")]) \
                if function == "internal" else (None, None)
            native_ribbons.append({"id": lane.attrib["id"], "width": width,
                "function": function, "kind": "walk" if function in ("crossing", "walkingarea") else lane_kind(lane),
                "shape": points, "entry_shape": entry_shape, "exit_shape": exit_shape})
            if len(points) < 2:
                if function == "walkingarea":
                    omitted_walking_areas.append({"id": lane.get("id"), "reason": "fewer_than_three_vertices"})
                continue
            if function == "crossing":
                crossing_cuts.append(LineString(points).buffer(width / 2 + 0.45,
                                                                cap_style=2, join_style=2))
                crossing_id = edge.attrib["id"]
                junction_id = crossing_junction_id(crossing_id)
                if junction_id in visible_crossing_nodes:
                    crossings.append({"id": lane.attrib["id"], "width": width, "shape": points})
                else:
                    omitted_unmarked_crossings.append(lane.attrib["id"])
                continue
            if function == "walkingarea":
                walking_area = make_valid(Polygon(points)) if len(points) >= 3 else Polygon()
                if not walking_area.is_empty and walking_area.area > .01:
                    walking_area_records.append({"id": edge.attrib["id"], "shape": points})
                    walk_surfaces.append(walking_area)
                    walking_area_count += 1
                else:
                    omitted_walking_areas.append({"id": lane.get("id"),
                        "reason": "fewer_than_three_vertices" if len(points) < 3 else "degenerate_area"})
            kind = "walk" if function == "walkingarea" else lane_kind(lane)
            if function == "internal":
                if kind in ("motor", "shared", "cycle"):
                    road_surfaces.append(lane_ribbon(points, width, entry_shape, exit_shape))
                    internal_lane_count += 1
                elif kind == "walk":
                    # A native pedestrian movement through a junction is walkable paving.
                    walk_surfaces.append(lane_ribbon(points, width, entry_shape, exit_shape))
                continue
            if kind in ("shared", "walk"):
                lane_area = LineString(points).buffer(width / 2, cap_style=2, join_style=2)
                if kind == "walk" and function != "walkingarea":
                    walk_surfaces.append(lane_area)
            record = {"id": lane.attrib["id"], "width": width, "kind": kind, "shape": points,
                      "index": int(lane.get("index", "0"))}
            lanes.append(record)
            edge_lanes.append(record)
            lane_kinds[lane.attrib["id"]] = kind
            if kind in ("motor", "shared", "cycle"):
                road_surfaces.append(LineString(points).buffer(width / 2, cap_style=2, join_style=2))
        motor_lanes = sorted((lane for lane in edge_lanes if lane["kind"] == "motor"),
                             key=lambda lane: lane["index"])
        if motor_lanes:
            motor_lanes[0]["outer"] = True
        for lane in motor_lanes[:-1]:
            lane["divider"] = True
        if function == "normal":
            if "from" not in edge.attrib or "to" not in edge.attrib:
                raise ValueError(f"SUMO road edge lacks source topology: {edge.get('id')}")
            road_edges.append({"id": edge.attrib["id"], "from": edge.attrib["from"],
                               "to": edge.attrib["to"], "function": function,
                               "lanes": edge_lanes})
    source_way_highways = {
        f"w{way.attrib['id']}": next((tag.get("v") for tag in way.findall("tag") if tag.get("k") == "highway"), "")
        for way in source_root.findall("way")
    }
    direction_guides = bidirectional_direction_guides(road_edges, source_way_highways)
    junctions = []
    for junction in root.findall("junction"):
        if junction.get("type") in ("internal", "dead_end"):
            continue
        polygon = project_shape(junction.get("shape"))
        if len(polygon) < 3 or Polygon(polygon).area <= 0.05:
            continue
        incoming = [lane_kinds[lane] for lane in junction.get("incLanes", "").split()
                    if lane in lane_kinds]
        if not incoming:
            continue
        kind = "motor" if "motor" in incoming or "shared" in incoming else "walk"
        junctions.append({"id": junction.attrib["id"], "kind": kind, "type": junction.get("type"), "shape": polygon})
    for edge in road_edges:
        edge["highway"] = source_way_highways[edge_source_way_id(edge["id"], set(source_way_highways))[0]]
    native_physical_clearance = None
    if geometry is not None:
        native_physical_clearance = lane_building_conflicts(root, source_root, native_ribbons, geometry.footprints, canonical_projection)
        diagnostic = {"schema_version": "aero-bench.city-road-physical-clearance/v1", "source_context": source_context,
                      "native_lane_clearance": native_physical_clearance}
        inputs.output.with_suffix(".physical-clearance.json").write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n")
        if native_physical_clearance["status"] != "PASS":
            raise ValueError("Native road/walking geometry fails actual building clearance or renderability; clipping cannot repair the source network")
    marking_layout = build_street_markings(road_edges, junctions,
        [dict(connection.attrib) for connection in root.findall("connection")], direction_guides)
    for lane in lanes:
        lane.pop("index")
    # Source materials may partition the road, but their independent widths
    # must not create additional carriageways outside the traffic network.
    (source_asphalt, source_concrete, source_concrete_way_ids,
     source_concrete_junction_node_ids, source_concrete_junction_triangles) = city_road_surfaces(
        inputs.pack, pack, inputs.road_surface_texture, source_root, allowed_mesh_targets,
        geometry.mesh_pack_projection.point if geometry is not None else None)
    motor_core = unary_union(road_surfaces)
    footprint_union = unary_union(footprints)
    # Split the visible surfaces by the explicit lane types. The curb lies on the
    # SUMO boundary between a sidewalk and a motor or cycle lane. The road-walk seam
    # is centred on it: the roadbed stops half a seam short of the walk paving here,
    # and the sidewalk layout later clears one full seam from the roadbed, so each
    # ribbon stays paved to within half a seam.
    walkbed = clip_source_buildings(unary_union(walk_surfaces).difference(motor_core), footprint_union)
    junction_surfaces = [make_valid(Polygon(junction["shape"])) for junction in junctions
                         if junction["kind"] == "motor"]
    motor_pavement = motor_core.buffer(MOTOR_PAVEMENT_SHOULDER_M, join_style=2)
    smoothed_junctions = unary_union([motor_pavement, *junction_surfaces]) \
        .buffer(0.5, join_style=2).buffer(-0.5, join_style=2)
    # Closing may trim acute tips; it must never remove a real lane corridor.
    roadbed = unary_union([smoothed_junctions, motor_pavement])
    roadbed = clip_source_buildings(roadbed, footprint_union).difference(
        walkbed.buffer(ROAD_WALK_SEPARATION_M / 2, join_style=2))
    # Source mesh carriageways may extend beyond SUMO lane envelopes. This
    # mask selects eligible streets; the surface module independently caps
    # new paving to 2.2 m from the actual road edge and excludes obstacles.
    eligible_corridors = unary_union([
        LineString(lane["shape"]).buffer(lane["width"] / 2 + 6.0, cap_style=2, join_style=2)
        for edge in road_edges if edge["highway"] in MOTOR_DIRECTION_GUIDE_HIGHWAYS
        for lane in edge["lanes"] if lane["kind"] == "motor"
    ])
    pedestrian_plan = plan_pedestrian_connection_paths(root, walking_area_records, crossings, lanes)
    pedestrian_paving = complete_pedestrian_port_paving(roadbed, walkbed,
        [Polygon(path["corridor"]["outline"], path["corridor"]["holes"])
         for path in pedestrian_plan["candidates"]], motor_core, footprint_union)
    walkbed = pedestrian_paving["walkbed"]
    sidewalk_layout = build_sidewalk_layout(roadbed, walkbed, motor_core,
        footprint_union, eligible_corridors, crossing_cuts)
    roadbed = set_precision(sidewalk_layout["roadbed"], 0)
    walkbed = set_precision(sidewalk_layout["walkbed"], 0)
    port_seams = close_pedestrian_port_seams(roadbed, walkbed,
        [Polygon(path["corridor"]["outline"], path["corridor"]["holes"])
         for path in pedestrian_plan["candidates"]], footprint_union)
    roadbed = port_seams["roadbed"]
    if not roadbed.is_valid or roadbed.is_empty:
        raise ValueError("SUMO roadbed union is invalid")
    if not walkbed.is_valid or walkbed.is_empty:
        raise ValueError("SUMO walking surface is invalid")
    # A facility strip is tied to the actual road/walk boundary, independently
    # of lane centre lines. Omitted stations remain visible in the audit data.
    print("Building curb-based lamp layout", flush=True)
    lamp_offset = street_lamp_curb_offset()
    lamp_layout = build_street_lamp_locations(
        roadbed, walkbed, motor_core, crossing_cuts, junction_surfaces, footprints,
        [Point(signal["x"], signal["z"]) for signal in signals],
        [], curb_offset_m=lamp_offset["curb_offset_m"])
    lamp_layout["curb_offset_basis"] = lamp_offset
    street_lamps = lamp_layout["street_lamps"]
    # Partition the union before earcut. A single city-scale polygon has many
    # blocks as holes and cannot be triangulated reliably as one contour.
    tile_size = inputs.tile_size_m
    roadbed_parts = tiled_polygons(roadbed, tile_size)
    walkbed_parts = tiled_polygons(walkbed, tile_size)
    if not roadbed_parts:
        raise ValueError("SUMO roadbed union produced no renderable polygons")
    if not walkbed_parts:
        raise ValueError("SUMO walking surface produced no renderable polygons")
    # Derive curbs from the tiled surfaces that the browser actually renders.
    # Keep valid corner fragments: small area alone does not mean a piece is
    # dispensable. Every SUMO crossing opens a curb cut.
    rendered_roadbed = unary_union([Polygon(part["outline"], part["holes"])
                                    for part in roadbed_parts])
    rendered_walkbed = unary_union([Polygon(part["outline"], part["holes"])
                                    for part in walkbed_parts])
    print("Checking final tiled pedestrian connection corridors", flush=True)
    pedestrian_connections = build_pedestrian_connections(
        root, walking_area_records, crossings, rendered_roadbed, rendered_walkbed, footprint_union)
    pedestrian_paths = build_pedestrian_connection_paths(root, walking_area_records, crossings, lanes,
        rendered_roadbed, rendered_walkbed, footprint_union)
    endpoint_failures = audit_crossing_endpoints(
        root, walking_area_records, crossings, lanes, rendered_roadbed, rendered_walkbed,
        footprint_union, pedestrian_connections["shared_areas"], pedestrian_paths["connection_paths"])
    landing_failures = audit_pedestrian_walkbed_landings(
        root, lanes, rendered_walkbed, pedestrian_paths["connection_paths"])
    coverage = surface_lane_coverage(native_ribbons, rendered_roadbed, rendered_walkbed) if geometry is not None else None
    if endpoint_failures or landing_failures or coverage is not None and coverage["status"] != "PASS":
        inputs.output.with_suffix(".endpoints.json").write_text(json.dumps({
            "failures": endpoint_failures, "omissions": pedestrian_paths["omissions"],
            "normal_port_landing_failures": landing_failures,
            "native_lane_surface_coverage": coverage,
            "source_context": source_context,
            "paving": pedestrian_paving["provenance"],
            "roadbed": roadbed_parts, "walkbed": walkbed_parts,
            "walking_areas": walking_area_records, "crossings": crossings, "lanes": lanes,
            "building_footprints": [{"outline": list(part.exterior.coords), "holes": [list(ring.coords) for ring in part.interiors]}
                                    for footprint in footprints for part in polygons_of(footprint)]}, indent=2))
        raise ValueError("Crossing endpoints lack connected displayed walking surfaces: "
                         f"{endpoint_failures}; dedicated walkbed landing failures: {landing_failures}; native lane paving coverage: {coverage}")
    marking_layout["markings"].extend(pedestrian_paths["markings"])
    marking_layout = clip_street_markings_to_roadbed(marking_layout, rendered_roadbed)
    median_bed_parts = tiled_polygons(sidewalk_layout["median_beds"], tile_size)
    rendered_median_beds = unary_union([Polygon(part["outline"], part["holes"])
                                      for part in median_bed_parts])
    concrete_roadbed = (unary_union(source_concrete).intersection(rendered_roadbed)
                        .difference(rendered_walkbed).difference(footprint_union)) \
        if source_concrete else Polygon()
    concrete_roadbed_parts = tiled_polygons(concrete_roadbed, tile_size) if not concrete_roadbed.is_empty else []
    curb_line = rendered_walkbed.boundary.intersection(rendered_roadbed.buffer(0.17))
    if crossing_cuts:
        curb_line = curb_line.difference(unary_union(crossing_cuts))
    curb_edges = [
        [[round(x, 4), round(z, 4)] for x, z in line.coords]
        for line in lines_of(curb_line) if line.length >= 0.6
    ]
    if not curb_edges and rendered_roadbed.boundary.distance(rendered_walkbed) <= MAX_SHARED_BOUNDARY_GAP_M:
        raise ValueError("Adjacent road and walk surfaces have no recoverable curb")
    signal_road_coverage = {}
    for signal in signals:
        vicinity = Point(signal["x"], signal["z"]).buffer(20)
        signal_road_coverage[signal["id"]] = round(roadbed.intersection(vicinity).area / vicinity.area, 4)
    payload = {
        "schema_version": "aero-bench.city-static-road-candidate/v1" if static else "aero-bench.city-road-preview/v3",
        "source_kind": "sumo-network-visual-geometry",
        "source_network_sha256": network_sha,
        "road_scope": road_scope,
        "source_mesh_target_count": len(allowed_mesh_targets) if allowed_mesh_targets is not None else None,
        "source_osm_sha256": source_osm_sha,
        "mesh_pack_source_sha256": pack["source"]["sha256"],
        **({"mesh_pack_manifest_sha256": hashlib.sha256(pack_bytes).hexdigest(),
            "signal_inventory_sha256": inputs.static_signal_inventory_sha256} if static else {
               "source_context": source_context,
               "physical_clearance": {"status": "PASS", "native_lane_clearance": native_physical_clearance,
                                      "native_lane_surface_coverage": coverage}}),
        "coordinates": "SUMO projection to canonical building ENU east-up-south" if not static else "source-declared static workspace frame",
        "roadbed_source": "sumo-lane-and-junction-union-with-walkingarea-and-explicit-derived-sidewalk-layout",
        "source_asphalt_texture": inputs.road_surface_texture,
        "source_asphalt_triangle_count": len(source_asphalt),
        "road_surface_materials": {
            "asphalt": {"texture": inputs.road_surface_texture,
                        "source": "roads-layer triangles with the scene-pinned asphalt texture"},
            "concrete": {"texture": CONCRETE_ROAD_TEXTURE,
                         "texture_asset": pack_texture_asset(inputs.pack, pack, CONCRETE_ROAD_TEXTURE,
                             private_candidate=static),
                         "uv_repeat_m": list(CONCRETE_TEXTURE_REPEAT_M),
                         "source": "roads-layer Concrete034 triangles assigned to source drivable OSM ways and multi-way road junction nodes",
                         "source_way_count": len(source_concrete_way_ids),
                         "source_way_triangles": len(source_concrete) - source_concrete_junction_triangles,
                         "source_junction_count": len(source_concrete_junction_node_ids),
                         "source_junction_triangles": source_concrete_junction_triangles,
                         "source_triangle_count": len(source_concrete),
                         "rendered_area_m2": round(concrete_roadbed.area, 2)},
        },
        "roadbed_tile_size_m": tile_size,
        "roadbed_area_m2": round(roadbed.area, 2),
        "walkbed_area_m2": round(walkbed.area, 2),
        "internal_lane_count": internal_lane_count,
        "crossing_policy": "osm-zebra-or-sumo-controlled-multiway",
        "omitted_unmarked_crossings": omitted_unmarked_crossings,
        "roadbed": roadbed_parts,
        "concrete_roadbed": concrete_roadbed_parts,
        "concrete_roadbed_area_m2": round(concrete_roadbed.area, 2),
        "walkbed": walkbed_parts,
        "street_layout": {
            "source_kind": "sumo-topology-with-explicit-derived-urban-design",
            "walking_area_count": walking_area_count,
            "walking_areas": walking_area_records,
            "connection_paths": pedestrian_paths["connection_paths"],
            "connection_path_omissions": pedestrian_paths["omissions"],
            "connection_path_profile": pedestrian_paths["profile"],
            "connection_paving": pedestrian_paving["provenance"],
            "connection_seam_paving": port_seams["provenance"],
            "pedestrian_connections": {key: value for key, value in pedestrian_connections.items() if key != "markings"},
            "walking_area_source_count": walking_area_source_count,
            "omitted_degenerate_walking_areas": omitted_walking_areas,
            "junction_surface_count": len(junction_surfaces),
            "motor_pavement_shoulder_m": MOTOR_PAVEMENT_SHOULDER_M,
            "road_height_m": .075, "sidewalk_height_m": .225,
            "pavement_edges": [[[round(x,4),round(z,4)] for x,z in line.coords]
                for line in lines_of(unary_union([rendered_walkbed, rendered_median_beds]).boundary)
                if line.length >= .2],
            "derived_walkbed": tiled_polygons(sidewalk_layout["derived_walkbed"], tile_size)
                if not sidewalk_layout["derived_walkbed"].is_empty else [],
            "median_beds": median_bed_parts,
            "sidewalk_provenance": sidewalk_layout["provenance"],
            "sidewalk_stats": sidewalk_layout["stats"],
            "marking_profile": marking_layout["profile"],
            "markings": marking_layout["markings"], "arrows": marking_layout["arrows"],
        },
        "displayed_surface_sha256": displayed_surface_sha256(roadbed_parts, walkbed_parts),
        "curb_edges": curb_edges,
        "signal_road_coverage": signal_road_coverage,
        "lanes": lanes,
        "marking_widths_m": MARKING_WIDTHS_M,
        "direction_guides": direction_guides,
        "junctions": junctions,
        "crossings": crossings,
        "street_lamps": street_lamps,
        "street_lamp_layout": {key: value for key, value in lamp_layout.items() if key != "street_lamps"},
    }
    inputs.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    if static:
        with inputs.output.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
    else:
        inputs.output.write_text(serialized)
    return {"output": str(inputs.output), "bytes": inputs.output.stat().st_size,
            "lanes": len(lanes), "internal_lanes": internal_lane_count,
            "concrete_source_way_count": len(source_concrete_way_ids),
            "concrete_source_way_triangles": len(source_concrete) - source_concrete_junction_triangles,
            "concrete_source_junction_count": len(source_concrete_junction_node_ids),
            "concrete_source_junction_triangles": source_concrete_junction_triangles,
            "concrete_source_triangles": len(source_concrete),
            "concrete_roadbed_area_m2": round(concrete_roadbed.area, 2),
            "direction_guides": len(direction_guides),
            "roadbed_polygons": len(roadbed_parts), "walkbed_polygons": len(walkbed_parts),
            "curb_edges": len(curb_edges),
            "junctions": len(junctions), "crossings": len(crossings),
            "street_lamps": len(street_lamps),
            "omitted_unmarked_crossings": len(omitted_unmarked_crossings)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True,
                        help="Public scene manifest with verified mesh pack and road output URL")
    parser.add_argument("--network", type=Path, required=True,
                        help="Source SUMO .net.xml for this scene")
    parser.add_argument("--source-osm", type=Path,
                        help="OSM input used to create the SUMO network; defaults to its engineering-inputs.json path")
    parser.add_argument("--building-objects", type=Path, required=True)
    parser.add_argument("--building-render", type=Path, required=True)
    parser.add_argument("--tile-size-m", type=int, default=60)
    parser.add_argument("--output", type=Path, help="Override the scene's road output path")
    parser.add_argument("--pack-manifest", type=Path,
                        help="Unpublished local copy of the scene's mesh-pack manifest; must match the scene pin")
    args = parser.parse_args()
    inputs = read_inputs(args.scene, args.network, args.tile_size_m, args.source_osm, args.pack_manifest)
    inputs = replace(inputs, output=args.output or inputs.output, building_objects=args.building_objects,
                     building_render_manifest=args.building_render)
    print(json.dumps(build(inputs), ensure_ascii=False, indent=2))
