"""Author source-bound flat streets within actual building free corridors.

All widths and lane shapes produced here are explicit engineering design. Original
OSM declarations remain observations; unsupported segments are recorded separately.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from shapely.geometry import LineString, Point
from shapely.ops import unary_union, substring

from city_preview_coordinates import CityEnuProjection
from aero_bench.world.frame_math import Vector3
from city_native_signals import allows
from city_pedestrian_sidewalks import _source_way

PROFILE = "authored-ground-streets/v2"
# Local streets whose OSM default access admits pedestrians; used only when no separate
# sidewalk fits the free corridor. Arterial classes never become shared surfaces.
SHARED_SURFACE_HIGHWAYS = frozenset({"residential", "service", "living_street", "unclassified", "road", "track"})
SHARED_SURFACE_BASIS = "shared-carriageway: OSM default foot access on a local street; no separate sidewalk fits the free corridor"
CLEARANCE_M = .2
MOTOR_WIDTH_M = 3.2
MOTOR_MIN_WIDTH_M = 3.0
SERVICE_MIN_WIDTH_M = 2.5
PEDESTRIAN_WIDTH_M = 2.0
PEDESTRIAN_MIN_WIDTH_M = 1.5
AREA_EPSILON_M2 = 1e-8


@dataclass(frozen=True)
class FleetWidth:
    vehicle_class: str
    native_width_m: float
    displayed_width_m: float

    @property
    def clearance_width_m(self):
        return max(self.native_width_m, self.displayed_width_m) + 2 * CLEARANCE_M


def read_fleet_widths(document: dict) -> tuple[FleetWidth, ...]:
    if document.get("schema_version") != "aero-bench.city-ground-fleet-widths/v1" \
            or not isinstance(document.get("source_contract"), str) or not document["source_contract"]:
        raise ValueError("Authored streets require an explicit native/displayed fleet width contract")
    rows = []
    for item in document["vehicles"]:
        row = FleetWidth(item["vehicle_class"], float(item["native_width_m"]), float(item["displayed_width_m"]))
        if not row.vehicle_class or any(not math.isfinite(width) or width <= 0
            for width in (row.native_width_m, row.displayed_width_m)):
            raise ValueError("Invalid fleet width declaration")
        rows.append(row)
    if not rows:
        raise ValueError("Authored streets require actual declared vehicle dimensions")
    return tuple(rows)


def _native_points(edge: ET.Element, nodes: dict) -> list:
    if edge.get("shape"):
        return [tuple(map(float, token.split(",")[:2])) for token in edge.get("shape").split()]
    return [nodes[edge.attrib["from"]], nodes[edge.attrib["to"]]]


def _offset(line: LineString, offset: float) -> LineString:
    if abs(offset) < 1e-12:
        return line
    value = line.parallel_offset(abs(offset), "left" if offset > 0 else "right", join_style=2)
    if value.geom_type != "LineString" or value.is_empty:
        raise ValueError("Authored lane offset produces a disconnected source spine")
    # GEOS versions differ in their right-offset direction convention.
    if LineString(value.coords).coords[0] != line.coords[0] and \
            value.interpolate(0).distance(line.interpolate(line.length)) < value.interpolate(0).distance(line.interpolate(0)):
        value = LineString(list(value.coords)[::-1])
    return value


def _native_shape(line: LineString, projection: CityEnuProjection) -> str:
    points = []
    for east, south in line.coords:
        lon, lat, _ = projection.enu.enu_to_geodetic(Vector3(east, -south, 0))
        x, y = projection.to_geographic.transform(lon, lat, direction="INVERSE", errcheck=True)
        points.append(f"{x+projection.offset[0]:.8f},{y+projection.offset[1]:.8f}")
    return " ".join(points)


def _interior_length(line: LineString, polygon) -> float:
    if polygon.is_empty:
        return 0.
    return line.intersection(polygon).difference(polygon.boundary).length


def _lane_role(lane: ET.Element, fleet: tuple[FleetWidth, ...]) -> str:
    if set(lane.get("allow", "").split()) == {"pedestrian"}:
        return "pedestrian"
    motor = [row for row in fleet if row.vehicle_class != "bicycle" and allows(lane, row.vehicle_class)]
    if motor:
        return "motor"
    if any(row.vehicle_class == "bicycle" and allows(lane, row.vehicle_class) for row in fleet):
        return "cycle"
    raise ValueError(f"Native lane has no supported fleet class or dedicated pedestrian role: {lane.get('id')}")


def _declared_width(tags: dict) -> float | None:
    if "width" not in tags:
        return None
    token = tags["width"].strip()
    if token.endswith(" m"):
        token = token[:-2].strip()
    try:
        width = float(token)
    except (ValueError, TypeError) as error:
        raise ValueError("Explicit source width cannot be interpreted as metres") from error
    if not math.isfinite(width) or width <= 0:
        raise ValueError("Explicit source width must be a positive finite metre declaration")
    return width


def plan_authored_streets(network: ET.Element, source: ET.Element, geometry, projection,
                          fleet: tuple[FleetWidth, ...], sidewalk_overrides: dict | None = None) -> tuple[ET.Element, dict]:
    """Derive complete lane cross sections; never erase contacts by clipping surfaces."""
    normal = {edge.attrib["id"]: edge for edge in network.findall("edge")
              if edge.get("function", "normal") == "normal"}
    nodes = {node.attrib["id"]: (float(node.attrib["x"]), float(node.attrib["y"]))
             for node in network.findall("junction")}
    ways = {way.attrib["id"]: {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
            for way in source.findall("way")}
    source_nodes = {}
    for node in source.findall("node"):
        point = projection.enu.geodetic_to_enu(longitude_deg=float(node.attrib['lon']),
            latitude_deg=float(node.attrib['lat']), altitude_m=projection.origin['ellipsoid_height_m'])
        source_nodes[node.attrib['id']] = (point.x, -point.y)
    source_spines = {}
    for way in source.findall('way'):
        if len(way.findall('nd')) < 2:
            raise ValueError(f"Authored street source way has no declared node spine: {way.attrib['id']}")
        source_spines[way.attrib['id']] = LineString([source_nodes[node.attrib['ref']] for node in way.findall('nd')])
    actual_buildings = unary_union([item.rendered_polygon for item in geometry.footprints])
    source_buildings = unary_union([item.polygon for item in geometry.footprints])
    forbidden = actual_buildings.buffer(CLEARANCE_M)
    paired, groups = set(), []
    for edge_id, edge in sorted(normal.items()):
        if edge_id in paired:
            continue
        way_id, tags = _source_way(edge_id, ways)
        forward = edge_id.split("#", 1)[0] == way_id
        opposite = "-" + edge_id if forward else edge_id[1:]
        keys = [edge_id]
        if opposite in normal and normal[opposite].get("from") == edge.get("to") \
                and normal[opposite].get("to") == edge.get("from"):
            keys.append(opposite)
        paired.update(keys)
        groups.append((way_id, tags, keys))
    patch = ET.Element("edges")
    designed, excluded = [], []
    for way_id, tags, keys in groups:
        edge_records = []
        for key in keys:
            edge = normal[key]
            points = [projection._point(*point) for point in _native_points(edge, nodes)]
            line = LineString(points)
            source_line = source_spines[way_id]
            start = source_nodes.get(edge.attrib['from'], points[0])
            end = source_nodes.get(edge.attrib['to'], points[-1])
            source_fragment = substring(source_line, source_line.project(Point(start)), source_line.project(Point(end)))
            if source_fragment.geom_type != 'LineString' or source_fragment.length < .01:
                edge_records.append({'edge_id':key,'edge':edge,'line':line,'roles':[],
                    'unsupported':'source-way fragment cannot bind the native edge endpoints'})
                continue
            line = source_fragment
            source_endpoint_basis = 'canonical-OSM-way-nodes-bound-to-native-edge-endpoints'
            try:
                roles = [_lane_role(lane, fleet) for lane in edge.findall("lane")]
            except ValueError as error:
                roles = []
                edge_records.append({"edge_id": key, "edge": edge, "line": line, "roles": roles, "unsupported": str(error)})
                continue
            edge_records.append({"edge_id": key, "edge": edge, "line": line, "roles": roles,
                "spine_basis": source_endpoint_basis})
        if len(edge_records) == 2 and all(edge_records[index]["roles"] for index in (0, 1)):
            for index in (0, 1):
                if all(role == "pedestrian" for role in edge_records[index]["roles"]) \
                        and any(role != "pedestrian" for role in edge_records[1-index]["roles"]):
                    edge_records[index]["line"] = LineString(list(edge_records[1-index]["line"].coords)[::-1])
                    edge_records[index]["spine_basis"] = "opposite pedestrian edge rederived from the paired source motor spine"
        source_conflict = any(_interior_length(record["line"], source_buildings) > 1e-8 for record in edge_records)
        unsupported = next((record["unsupported"] for record in edge_records if "unsupported" in record), None)
        # Paired native edge spines must refer to the same road before sharing a carriageway.
        if len(edge_records) == 2 and edge_records[0]["line"].hausdorff_distance(edge_records[1]["line"]) > .02:
            unsupported = "opposite native edges do not share one source spine"
        try:
            declared = _declared_width(tags)
        except ValueError as error:
            unsupported = str(error)
            declared = None
        if source_conflict or unsupported:
            reason = "source_geometry_conflict" if source_conflict else "unsupported_source_or_vehicle_contract"
            excluded.extend({"edge_id": key, "source_way_id": way_id,
                "original_osm_way_id": tags.get("aero_bench:source_id", way_id), "source_tags": tags,
                "reason": reason, "detail": "source spine enters canonical building interior; no modelled ground passage" if source_conflict else unsupported}
                for key in keys)
            continue
        motor_count = sum(record["roles"].count("motor") for record in edge_records)
        lower = SERVICE_MIN_WIDTH_M if tags.get("highway") in {"service", "living_street"} else MOTOR_MIN_WIDTH_M
        motor_widths = [declared / motor_count] if declared is not None and motor_count else \
            ([3.2, 3.0, 2.9, 2.8, 2.7, 2.6, 2.5] if lower == SERVICE_MIN_WIDTH_M else [3.2, 3.0])
        has_walk = any("pedestrian" in record["roles"] for record in edge_records)
        has_road = any(role != "pedestrian" for record in edge_records for role in record["roles"])
        local_walkable = tags.get("highway") in SHARED_SURFACE_HIGHWAYS and tags.get("foot") != "no"
        shared_ok = has_walk and has_road and local_walkable and tags.get("sidewalk") != "separate"
        # Native walking-area feedback: level 1 caps a separate sidewalk at the minimum width,
        # level 2 forbids it, level 3 also removes pedestrian access from a shared carriageway.
        feedback = sorted(((sidewalk_overrides or {})[key] for key in keys if key in (sidewalk_overrides or {})),
                          key=lambda item: item["level"])
        level = feedback[-1]["level"] if feedback else 0
        modes = [("separate-sidewalk", width) for width in (PEDESTRIAN_WIDTH_M, PEDESTRIAN_MIN_WIDTH_M)
                 if level == 0 or (level == 1 and width <= PEDESTRIAN_MIN_WIDTH_M)]
        if shared_ok and level < 3:
            modes.append(("shared-carriageway", None))
        if has_walk and has_road:
            modes.append(("sidewalk-omitted", PEDESTRIAN_MIN_WIDTH_M))
        solution = None
        rejected = []
        bidirectional_motor = False
        for motor_width in motor_widths:
            if motor_count and motor_width < lower:
                rejected.append({"motor_width_m": motor_width, "reason": "explicit width below the design minimum"})
                continue
            for mode, ped_width in modes:
                trial, dropped = [], []
                failed = False
                failure = None
                motor_totals = [record["roles"].count("motor") * motor_width + sum(
                    float(lane.get("width", "3.2")) for lane, role in zip(record["edge"].findall("lane"), record["roles"])
                    if role == "cycle") for record in edge_records]
                bidirectional_motor = len(motor_totals) == 2 and all(total > 0 for total in motor_totals)
                for direction, record in enumerate(edge_records):
                    total = motor_totals[direction]
                    other = motor_totals[1-direction] if len(motor_totals) == 2 else 0
                    shift = (other-total)/2 if bidirectional_motor else 0
                    consumed = 0
                    kept_index = 0
                    for lane, role in zip(record["edge"].findall("lane"), record["roles"]):
                        if mode == "shared-carriageway" and role == "pedestrian":
                            continue
                        if mode == "sidewalk-omitted" and role == "pedestrian" and level >= 2:
                            dropped.append({"edge_id": record["edge_id"], "source_lane_id": lane.attrib["id"],
                                            "reason": feedback[-1]["reason"]})
                            continue
                        width = motor_width if role == "motor" else ped_width if role == "pedestrian" else float(lane.get("width", "3.2"))
                        if role == "pedestrian":
                            offset = shift + total + width/2 if bidirectional_motor else (total or other)/2 + width/2
                            permitted = ["pedestrian"]
                        else:
                            offset = shift + total-consumed-width/2 if bidirectional_motor else total/2-consumed-width/2
                            consumed += width
                            permitted = sorted({row.vehicle_class for row in fleet if allows(lane, row.vehicle_class)
                                and row.clearance_width_m <= width and (role != "cycle" or row.vehicle_class == "bicycle")})
                            if mode == "shared-carriageway" and role == "motor" and permitted:
                                permitted = sorted(set(permitted) | {"pedestrian"})
                        if not permitted:
                            failed = True
                            failure = {"edge_id": record["edge_id"], "role": role, "cause": "no supported class fits the lane width"}
                            break
                        try:
                            centre = _offset(record["line"], offset)
                        except ValueError as error:
                            failed = True
                            failure = {"edge_id": record["edge_id"], "role": role, "offset_m": offset, "cause": str(error)}
                            break
                        occupied = centre.buffer(width/2, cap_style=2, join_style=2)
                        overlap = occupied.intersection(forbidden).area
                        if overlap > AREA_EPSILON_M2:
                            if mode == "sidewalk-omitted" and role == "pedestrian":
                                dropped.append({"edge_id": record["edge_id"], "source_lane_id": lane.attrib["id"],
                                                "overlap_m2": overlap, "reason": "sidewalk cannot fit beside actual buildings"})
                                continue
                            failed = True
                            failure = {"edge_id": record["edge_id"], "role": role, "offset_m": offset,
                                       "width_m": width, "overlap_m2": overlap, "cause": "building clearance"}
                            break
                        trial.append({"edge_id": record["edge_id"], "lane_index": str(kept_index),
                            "source_lane_index": lane.attrib["index"], "cross_section": mode,
                            "source_lane_id": lane.attrib["id"], "role": role, "width_m": width,
                            "source_width_m": declared, "width_basis": "source-declared-width-divided-by-native-motor-lane-count" if declared is not None and role == "motor" else "authored-design-not-surveyed",
                            "source_allowed": lane.get("allow"), "source_disallowed": lane.get("disallow"),
                            "allowed": permitted, "native_source_width_m": float(lane.get("width", "3.2")),
                            "native_source_speed_mps": float(lane.attrib["speed"]), "signed_viewer_left_offset_m": offset,
                            "shape": _native_shape(centre, projection), "native_role": role})
                        kept_index += 1
                    if failed:
                        break
                if not failed and mode == "sidewalk-omitted" and not dropped:
                    failed = True  # identical to a separate-sidewalk design already rejected
                if not failed:
                    solution = (trial, motor_width, ped_width, mode, dropped, edge_records)
                    break
                rejected.append({"motor_width_m": motor_width, "pedestrian_width_m": ped_width,
                    "cross_section": mode, "failure": failure,
                    "reason": "source-spine cross section cannot fit actual building free corridor or supported fleet"})
            if solution:
                break
        if solution is None and local_walkable:
            # No vehicle lane fits: keep one bidirectional walkway on the first native edge when it fits.
            record = edge_records[0]
            for ped_width in (PEDESTRIAN_WIDTH_M, PEDESTRIAN_MIN_WIDTH_M) if level == 0 else (PEDESTRIAN_MIN_WIDTH_M,):
                if declared is not None and ped_width > declared:
                    rejected.append({"pedestrian_width_m": ped_width, "cross_section": "pedestrian-passage",
                        "reason": "walkway would exceed the source-declared width"})
                    continue
                occupied = record["line"].buffer(ped_width/2, cap_style=2, join_style=2)
                overlap = occupied.intersection(forbidden).area
                if overlap > AREA_EPSILON_M2:
                    rejected.append({"pedestrian_width_m": ped_width, "cross_section": "pedestrian-passage",
                        "failure": {"edge_id": record["edge_id"], "role": "pedestrian", "width_m": ped_width,
                                    "overlap_m2": overlap, "cause": "building clearance"},
                        "reason": "walkway cannot fit actual building free corridor"})
                    continue
                source_lane = record["edge"].findall("lane")[0]
                trial = [{"edge_id": record["edge_id"], "lane_index": "0", "source_lane_index": source_lane.attrib["index"],
                    "cross_section": "pedestrian-passage", "source_lane_id": source_lane.attrib["id"], "role": "pedestrian",
                    "width_m": ped_width, "source_width_m": declared, "width_basis": "authored-design-not-surveyed",
                    "source_allowed": source_lane.get("allow"), "source_disallowed": source_lane.get("disallow"),
                    "allowed": ["pedestrian"], "native_source_width_m": float(source_lane.get("width", "3.2")),
                    "native_source_speed_mps": float(source_lane.attrib["speed"]), "signed_viewer_left_offset_m": 0.0,
                    "shape": _native_shape(record["line"], projection), "native_role": "pedestrian"}]
                solution = (trial, None, ped_width, "pedestrian-passage", [], [record])
                bidirectional_motor = False
                for folded in edge_records[1:]:
                    excluded.append({"edge_id": folded["edge_id"], "source_way_id": way_id,
                        "original_osm_way_id": tags.get("aero_bench:source_id", way_id), "source_tags": tags,
                        "reason": "folded_into_pedestrian_passage",
                        "detail": f"no vehicle lane fits; pedestrians use {record['edge_id']} in both directions"})
                break
        if solution is None:
            excluded.extend({"edge_id": key, "source_way_id": way_id,
                "original_osm_way_id": tags.get("aero_bench:source_id", way_id), "source_tags": tags,
                "reason": "unsupported_free_corridor", "design_trials": rejected} for key in keys)
            continue
        trial, motor_width, ped_width, mode, dropped, kept_records = solution
        for record in kept_records:
            kept = [item for item in trial if item["edge_id"] == record["edge_id"]]
            edge_patch = ET.SubElement(patch, "edge", id=record["edge_id"], spreadType="center")
            if len(kept) != len(record["edge"].findall("lane")):
                edge_patch.set("numLanes", str(len(kept)))
            for lane in kept:
                ET.SubElement(edge_patch, "lane", index=lane["lane_index"], width=str(lane["width_m"]),
                    speed=str(lane["native_source_speed_mps"]), allow=" ".join(lane["allowed"]), shape=lane["shape"])
            designed.append({"edge_id": record["edge_id"], "source_way_id": way_id,
                "sidewalk_override": (sidewalk_overrides or {}).get(record["edge_id"]),
                "original_osm_way_id": tags.get("aero_bench:source_id", way_id), "source_tags": tags,
                "source_edge_spread": record["edge"].get("spreadType", "right"),
                "derived_edge_spread": "center-with-explicit-lane-shapes", "bidirectional_motor": bidirectional_motor,
                "spine_basis": record["spine_basis"],
                "motor_width_design_m": motor_width, "pedestrian_width_design_m": ped_width,
                "cross_section": mode,
                "pedestrian_access_basis": SHARED_SURFACE_BASIS if mode == "shared-carriageway" else None,
                "omitted_sidewalk_lanes": [item for item in dropped if item["edge_id"] == record["edge_id"]],
                "lanes": kept})
    return patch, {"schema_version": "aero-bench.city-authored-ground-streets/v1", "profile": PROFILE,
        "surveyed_geometry": False, "clearance_each_side_m": CLEARANCE_M,
        "motor_default_width_m": MOTOR_WIDTH_M, "motor_min_width_m": MOTOR_MIN_WIDTH_M,
        "service_min_width_m": SERVICE_MIN_WIDTH_M, "pedestrian_default_width_m": PEDESTRIAN_WIDTH_M,
        "pedestrian_min_width_m": PEDESTRIAN_MIN_WIDTH_M,
        "shared_surface_highways": sorted(SHARED_SURFACE_HIGHWAYS), "shared_surface_basis": SHARED_SURFACE_BASIS,
        "cross_section_preference": ["separate-sidewalk", "shared-carriageway", "sidewalk-omitted", "pedestrian-passage"],
        "sidewalk_overrides": sidewalk_overrides or {},
        "design_widths_are_not_source_measurements": True,
        "original_normal_edge_count": len(normal), "designed": designed, "excluded": excluded,
        "source_tags_are_not_overwritten": True,
        "native_junction_turning_and_real_motion_audit": "PENDING_NATIVE_MATERIALIZATION_AND_RECORDING"}
