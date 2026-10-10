#!/usr/bin/env python3
"""Check the displayed SUMO road surfaces against recorded vehicle positions."""
from __future__ import annotations

import hashlib
import json
import argparse
import math
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from city_street_markings import rendered_marking_footprint
from city_pedestrian_connections import audit_crossing_endpoints
from city_preview_scene import ROOT, DEFAULT_NETWORK, DEFAULT_SCENE, read_scene_paths
from city_surface_identity import displayed_surface_sha256
from city_road_topology import (crossing_junction_id, lane_kind, motor_neighbors,
                                visible_crossing_junctions, zebra_crossing_nodes)

DEFAULT_REPORT = ROOT / "frontend/validation/city-road-audit.json"


def audit_v2_surfaces(road: dict, network: ET.Element, osm: ET.Element, pack: dict, pack_base_url: str) -> None:
    """Independently check the new visual surfaces and their declared lane provenance."""
    static = road.get("schema_version") == "aero-bench.city-static-road-candidate/v1"
    if not static and road.get("schema_version") != "aero-bench.city-road-preview/v2":
        raise ValueError("Road audit requires the current v2 surface and marking contract")
    for key in ("concrete_roadbed", "direction_guides"):
        if not isinstance(road.get(key), list):
            raise ValueError(f"Road v2 inventory missing: {key}")
    widths = road.get("marking_widths_m", {})
    for key in ("lane_edge", "lane_divider", "derived_direction_guide"):
        value = widths.get(key)
        if not isinstance(value, (float, int)) or not math.isfinite(value) or not .12 <= value <= .15:
            raise ValueError(f"Road marking width invalid: {key}")
    surfaces = unary_union([Polygon(p["outline"], p["holes"]) for p in road["roadbed"]])
    concrete = unary_union([Polygon(p["outline"], p["holes"]) for p in road["concrete_roadbed"]])
    area = road.get("concrete_roadbed_area_m2")
    if not isinstance(area, (int, float)) or not math.isfinite(area) or area < 0 \
            or abs(concrete.area - area) > max(.05, area * .001):
        raise ValueError("Concrete road footprint differs from its declared area")
    if not concrete.is_valid or concrete.difference(surfaces.buffer(.025)).area > .01:
        raise ValueError("Concrete road material extends beyond the verified road surface")
    materials = road.get("road_surface_materials", {})
    declared = materials.get("concrete", {})
    textures = {b["material"]["base_color_texture"] for b in pack["batches"] if b["layer"] == "roads"}
    if not isinstance(declared.get("texture"), str) or (not concrete.is_empty and declared["texture"] not in textures) \
            or declared.get("rendered_area_m2") != area:
        raise ValueError("Concrete road material differs from the source pack")
    if not concrete.is_empty:
        expected = pack["textures"].get(declared["texture"])
        verified_asset = ({"sha256": expected["sha256"], "size_bytes": expected["size_bytes"]}
                          if expected is not None else None)
        if not static and verified_asset is not None:
            verified_asset = {"url": pack_base_url + "assets/" + expected["sha256"],
                              **verified_asset}
        if expected is None or declared.get("texture_asset") != verified_asset:
            raise ValueError("Concrete texture asset differs from the verified mesh-pack material")
    layout = road.get("street_layout", {})
    if layout.get("source_kind") != "sumo-topology-with-explicit-derived-urban-design" \
            or layout.get("road_height_m") != .075 or layout.get("sidewalk_height_m") != .225:
        raise ValueError("Street layout lacks explicit design provenance and metric heights")
    provenance = layout.get("sidewalk_provenance", {})
    expected_provenance = {
        "source_kind": "derived_osm_eligible_corridor_visual_geometry",
        "sumo_lane_topology_modified": False, "surface_precision_normalized": True,
        "sidewalk_kind": "derived_paved_sidewalk_not_surveyed",
        "median_kind": "derived_paved_narrow_enclosed_strip_not_surveyed",
        "crossing_cuts_affect": "curb_lines_only",
    }
    if any(provenance.get(key) != value for key, value in expected_provenance.items()):
        raise ValueError("Street surface provenance differs from the design contract")
    stats = layout.get("sidewalk_stats", {})
    stat_fields = ("derived_walkbed_area_m2", "median_beds_area_m2", "sidewalk_width_m",
        "building_clearance_m", "vehicle_clearance_m", "precision_grid_m",
        "effective_building_clearance_m", "effective_vehicle_clearance_m", "raw_roadbed_area_m2",
        "roadbed_normalization_symmetric_difference_m2", "precision_clearance_margin_m",
        "road_walk_separation_m", "normalized_roadbed_area_m2", "normalized_source_walkbed_area_m2")
    if any(not isinstance(stats.get(key), (int, float)) or isinstance(stats[key], bool)
           or not math.isfinite(stats[key]) or stats[key] < 0 for key in stat_fields):
        raise ValueError("Street surface precision/area inventory is invalid")
    if stats["precision_grid_m"] != .001 or stats["precision_clearance_margin_m"] != .003 \
            or stats["road_walk_separation_m"] != .003 or any(
                abs(stats[f"effective_{kind}_clearance_m"] - stats[f"{kind}_clearance_m"] - .003) > 1e-9
                for kind in ("building", "vehicle")):
        raise ValueError("Street surface precision policy is inconsistent")
    walks = unary_union([Polygon(p["outline"], p["holes"]) for p in road["walkbed"]])
    for field in ("derived_walkbed", "median_beds"):
        if not isinstance(layout.get(field), list):
            raise ValueError(f"Street layout omits {field}")
        surface = unary_union([Polygon(p["outline"], p["holes"]) for p in layout[field]])
        if surface.intersection(surfaces).area > .1:
            raise ValueError(f"{field} covers motor pavement")
        if field == "derived_walkbed" and surface.difference(walks.buffer(.025)).area > .1:
            raise ValueError("Derived sidewalk is missing from displayed pedestrian surface")
    if not isinstance(layout.get("pavement_edges"), list) or (not walks.is_empty and not layout["pavement_edges"]):
        raise ValueError("Street layout omits raised pavement edges")
    median = unary_union([Polygon(p["outline"], p["holes"]) for p in layout["median_beds"]])
    pavement_boundary = unary_union([walks, median]).boundary.buffer(.001)
    for coordinates in layout["pavement_edges"]:
        edge = LineString(coordinates)
        if not edge.is_valid or edge.length < .1999 or edge.difference(pavement_boundary).length > .001:
            raise ValueError("Pavement wall leaves the published top-surface boundary")
    if not isinstance(layout.get("markings"), list) or not isinstance(layout.get("arrows"), list):
        raise ValueError("Street layout omits its road marking inventory")
    marking_surface = surfaces.buffer(.08)
    arrow_surface = surfaces.buffer(.025)
    for marking in layout["markings"]:
        if marking.get("color") not in ("white", "yellow") \
                or marking.get("pattern") not in ("solid", "dashed") \
                or not 0 < marking.get("width_m", 0) <= .5:
            raise ValueError("Road marking has an invalid metric style")
        painted = LineString(marking["shape"]).buffer(marking["width_m"] / 2, cap_style=2)
        if painted.difference(marking_surface).area > .1:
            raise ValueError(f"Road marking leaves displayed roadway: {marking.get('id')}")
        rendered = rendered_marking_footprint(marking["shape"], marking["width_m"])
        if rendered.difference(surfaces).area > 1e-6:
            raise ValueError(f"Rendered marking triangles leave roadway: {marking.get('source_lane_ids')}")
    for arrow in layout["arrows"]:
        if Polygon(arrow["outline"]).difference(arrow_surface).area > .01:
            raise ValueError(f"Turn arrow leaves displayed roadway: {arrow.get('id')}")
    ways = {"w" + w.attrib["id"]: w for w in osm.findall("way")}
    edges = {e.attrib["id"]: e for e in network.findall("edge") if e.get("function", "normal") == "normal"}
    ids = set()
    for separator in road["direction_guides"]:
        identity = separator["id"]
        if identity in ids:
            raise ValueError(f"Duplicate opposing-flow separator: {identity}")
        ids.add(identity)
        way = separator["source_way_id"]
        pair = separator["edge_ids"]
        if way not in ways or len(pair) != 2 or pair[0] == pair[1] or any(e not in edges for e in pair):
            raise ValueError(f"Center separator lacks source way/edges: {identity}")
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in ways[way].findall("tag")}
        if tags.get("highway") not in {"primary", "primary_link", "secondary", "secondary_link",
                                       "tertiary", "tertiary_link", "residential", "unclassified"}:
            raise ValueError(f"Direction guide drawn on an ineligible road: {identity}")
        left, right = [edges[e] for e in pair]
        for edge in (left, right):
            motor = [lane for lane in edge.findall("lane") if lane_kind(lane) == "motor"]
            if not motor or float(motor[-1].get("width", "3.2")) < 2.5:
                raise ValueError(f"Direction guide uses a narrow or non-motor lane: {identity}")
        if left.get("from") != right.get("to") or left.get("to") != right.get("from"):
            raise ValueError(f"Center separator joins non-opposing edges: {identity}")
        for edge_id in pair:
            base = edge_id.partition("#")[0]
            if way[1:] not in (base, base[1:] if base.startswith("-") else base):
                raise ValueError(f"Center separator references a different source way: {identity}")
        width = separator.get("width_m")
        if separator.get("marking") != "derived_direction_guide" \
                or separator.get("source_kind") != "sumo-lane-topology-not-surveyed-marking" \
                or width != widths["derived_direction_guide"]:
            raise ValueError(f"Center separator marking differs from policy: {identity}")
        line = LineString(separator["shape"])
        if line.is_empty or not line.is_valid or line.length <= 0 \
                or line.buffer(width / 2).difference(surfaces.buffer(.025)).area > .01:
            raise ValueError(f"Center separator extends outside road surface: {identity}")


def audit_static_candidate(*, road_path: Path, signal_inventory_path: Path,
                           network_path: Path, source_osm_path: Path,
                           pack_manifest_path: Path, building_footprints: tuple) -> dict:
    """Check source identity and render geometry without requiring a traffic trace."""
    road_bytes = road_path.read_bytes()
    inventory_bytes = signal_inventory_path.read_bytes()
    network_bytes = network_path.read_bytes()
    source_bytes = source_osm_path.read_bytes()
    pack_bytes = pack_manifest_path.read_bytes()
    road = json.loads(road_bytes)
    inventory = json.loads(inventory_bytes)
    pack = json.loads(pack_bytes)
    network = ET.fromstring(network_bytes)
    source = ET.fromstring(source_bytes)
    if road.get("schema_version") != "aero-bench.city-static-road-candidate/v1" or \
            inventory.get("schema_version") != "aero-bench.city-static-signal-inventory/v1":
        raise ValueError("Selected static road or signal schema differs from the fixed contract")
    network_sha = hashlib.sha256(network_bytes).hexdigest()
    pack_source_sha = pack["source"]["sha256"]
    if (road.get("source_network_sha256") != network_sha
            or road.get("source_osm_sha256") != hashlib.sha256(source_bytes).hexdigest()
            or road.get("mesh_pack_source_sha256") != pack_source_sha
            or road.get("mesh_pack_manifest_sha256") != hashlib.sha256(pack_bytes).hexdigest()
            or road.get("signal_inventory_sha256") != hashlib.sha256(inventory_bytes).hexdigest()
            or inventory.get("source_network_sha256") != network_sha
            or inventory.get("mesh_pack_source_sha256") != pack_source_sha):
        raise ValueError("Static road, signals, SUMO network and mesh pack differ")
    forbidden = ("building_placement_sha256", "traffic_recorded_building_placement_sha256",
                 "frames", "traffic")
    if any(key in road for key in forbidden):
        raise ValueError("Unfinalized static road candidate contains placement or traffic fields")
    if road.get("displayed_surface_sha256") != displayed_surface_sha256(
            road.get("roadbed"), road.get("walkbed")):
        raise ValueError("Static road has a stale displayed surface digest")
    if road.get("crossing_policy") != "osm-zebra-or-sumo-controlled-multiway":
        raise ValueError("Static road crossing policy differs from its source topology")
    audit_v2_surfaces(road, network, source, pack, "")
    crossings = road["crossings"]
    visible = visible_crossing_junctions(network, source)
    expected_crossings, expected_omissions = [], []
    for edge in network.findall("edge"):
        if edge.get("function") != "crossing":
            continue
        destination = (expected_crossings if crossing_junction_id(edge.attrib["id"]) in visible
                       else expected_omissions)
        destination.extend(lane.attrib["id"] for lane in edge.findall("lane") if lane.get("shape"))
    if expected_crossings != [item["id"] for item in crossings] or \
            expected_omissions != road["omitted_unmarked_crossings"]:
        raise ValueError("Static zebra inventory differs from source crossing topology")
    asphalt = road.get("source_asphalt_texture")
    road_textures = {batch["material"]["base_color_texture"] for batch in pack["batches"]
                     if batch["layer"] == "roads"}
    if asphalt not in road_textures or "/Asphalt" not in asphalt or asphalt not in pack["textures"]:
        raise ValueError("Static asphalt material differs from the selected mesh pack")
    roadbed = unary_union([Polygon(part["outline"], part["holes"]) for part in road["roadbed"]])
    walkbed = unary_union([Polygon(part["outline"], part["holes"]) for part in road["walkbed"]])
    if roadbed.is_empty or walkbed.is_empty or roadbed.intersection(walkbed).area > .01:
        raise ValueError("Static road and walkway are empty or overlapping")
    if abs(roadbed.area - road["roadbed_area_m2"]) > max(.05, roadbed.area * .001) or \
            abs(walkbed.area - road["walkbed_area_m2"]) > max(.05, walkbed.area * .001):
        raise ValueError("Static road or walkway tiles differ from their area inventory")
    walk_boundary = walkbed.boundary.buffer(.02)
    for index, coordinates in enumerate(road["curb_edges"]):
        if len(coordinates) < 2:
            raise ValueError(f"Static curb {index} has fewer than two vertices")
        line = LineString(coordinates)
        if not line.is_valid or line.length < .59 or \
                line.difference(walk_boundary).length > .02 or line.distance(roadbed) > .18:
            raise ValueError(f"Static curb {index} leaves the displayed road/walk boundary")
    footprint_union = unary_union(building_footprints)
    if footprint_union.is_empty or roadbed.intersection(footprint_union).area > .05 \
            or walkbed.intersection(footprint_union).area > .05:
        raise ValueError("Selected road or sidewalk clips a source building footprint")
    lane_records = {lane["id"]: lane for lane in road["lanes"]}
    network_lanes = {lane.attrib["id"] for edge in network.findall("edge")
                     for lane in edge.findall("lane")}
    if not lane_records or not set(lane_records).issubset(network_lanes):
        raise ValueError("Static road lanes differ from the selected SUMO network")
    signals = inventory.get("signals")
    if not isinstance(signals, list):
        raise ValueError("Selected signal inventory is malformed")
    controlled = {}
    edge_lanes = {edge.attrib["id"]: edge.findall("lane") for edge in network.findall("edge")
                  if edge.get("function", "normal") == "normal"}
    motor_approaches = set()
    for connection in network.findall("connection"):
        tls, source_edge = connection.get("tl"), connection.get("from")
        if tls and source_edge and connection.get("linkIndex") is not None:
            controlled.setdefault(f"{tls}:{source_edge}", set()).add(int(connection.attrib["linkIndex"]))
            lanes = edge_lanes.get(source_edge)
            index = int(connection.attrib["fromLane"])
            if lanes is not None and 0 <= index < len(lanes) and lane_kind(lanes[index]) in ("motor", "shared"):
                motor_approaches.add(f"{tls}:{source_edge}")
    observed = set()
    for signal in signals:
        signal_id = signal.get("id")
        if signal_id in observed or signal_id not in controlled \
                or signal.get("link") not in controlled[signal_id] \
                or not all(isinstance(signal.get(key), (int, float)) and math.isfinite(signal[key])
                           for key in ("x", "z", "heading")):
            raise ValueError(f"Static signal is not bound to a controlled lane: {signal_id}")
        observed.add(signal_id)
        lane_id = signal_id.removeprefix(signal["tls"] + ":") + "_" + str(
            min(int(conn.attrib["fromLane"]) for conn in network.findall("connection")
                if conn.get("tl") == signal["tls"] and conn.get("from") ==
                signal_id.removeprefix(signal["tls"] + ":") and
                int(conn.get("linkIndex", "-1")) == signal["link"]))
        lane = lane_records.get(lane_id)
        if lane is None or len(lane["shape"]) < 2:
            raise ValueError(f"Static signal has no displayed incoming lane: {signal_id}")
        (x0, z0), (x1, z1) = lane["shape"][-2:]
        expected_heading = math.degrees(math.atan2(x1 - x0, z0 - z1))
        if abs((signal["heading"] - expected_heading + 180) % 360 - 180) > 3:
            raise ValueError(f"Static signal faces away from its incoming lane: {signal_id}")
        approach_line = LineString(lane["shape"])
        approach = approach_line.interpolate(max(0, approach_line.length - 2.5))
        angle = math.radians(signal["heading"])
        head = Point(signal["x"] - 2.82 * math.cos(angle),
                     signal["z"] - 2.82 * math.sin(angle))
        if head.distance(approach) > 6.2:
            raise ValueError(f"Static signal head misses its controlled lane: {signal_id}")
        if footprint_union.intersects(Point(signal["x"], signal["z"]).buffer(.35)):
            raise ValueError(f"Static signal pole clips a source building: {signal_id}")
        pole = Point(signal["x"], signal["z"]).buffer(.35)
        if not walkbed.covers(pole) or roadbed.intersects(pole):
            raise ValueError(f"Static signal pole does not fit the displayed sidewalk: {signal_id}")
    if observed != motor_approaches:
        raise ValueError("Static signal inventory omits or adds a controlled motor approach")
    coverage = road.get("signal_road_coverage")
    if not isinstance(coverage, dict) or set(coverage) != observed or any(
            not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
            for value in coverage.values()):
        raise ValueError("Static road signal coverage differs from the selected inventory")
    lamp_points = [Point(item["x"], item["z"]) for item in road["street_lamps"]]
    if len(road["street_lamps"]) != road["street_lamp_layout"]["stats"]["placed_count"]:
        raise ValueError("Static lamp inventory differs from its placement audit")
    pole_allowed_surface = walkbed.buffer(.003)
    authored_curb_boundary = walkbed.boundary
    curb_offset = road["street_lamp_layout"]["stats"]["curb_offset_m"]
    for index, point in enumerate(lamp_points):
        lamp = road["street_lamps"][index]
        boundary = lamp["source_boundary"]
        curb_point = Point(boundary["curb_point"])
        if not all(math.isfinite(lamp[key]) for key in ("x", "z", "rotation_deg")) or \
                not pole_allowed_surface.covers(point.buffer(.33)) or \
                not boundary["id"] or boundary["station_m"] < 0 or \
                curb_point.distance(authored_curb_boundary) > .02 or \
                curb_point.distance(roadbed) > .16 or \
                abs(curb_point.distance(point) - curb_offset) > .01 or \
                footprint_union.distance(point) < .6 or \
                any(point.distance(Point(signal["x"], signal["z"])) < 1.4 for signal in signals):
            raise ValueError(f"Static lamp {index} clips the road, a building, or a signal")
        if any(point.distance(other) < 1.99 for other in lamp_points[:index]):
            raise ValueError(f"Static lamp {index} duplicates a nearby pole")
    return {"road_sha256": hashlib.sha256(road_bytes).hexdigest(),
            "signal_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
            "roadbed_area_m2": round(roadbed.area, 2),
            "walkbed_area_m2": round(walkbed.area, 2),
            "lanes": len(lane_records), "crossings": len(crossings),
            "street_markings": len(road["street_layout"]["markings"]),
            "street_lamps": len(lamp_points), "signals": len(signals)}



def audit_pedestrian_connections(road: dict, network: ET.Element, pack: dict, placement: dict) -> dict:
    """Bind displayed walking connectors to source geometry and direct topology."""
    layout = road["street_layout"]
    records = layout["walking_areas"]
    projected = Transformer.from_crs(CRS.from_proj4(network.find("location").get("projParameter")),
                                     CRS.from_epsg(4326), always_xy=True)
    origin = pack["projection"]["origin"]
    scale = 40075016.686 * math.cos(math.radians(origin["latitude_deg"]))
    def mercator(latitude):
        sine = math.sin(math.radians(latitude))
        return math.log((1 + sine) / (1 - sine)) / (4 * math.pi) + .5
    source, source_lanes = {}, {}
    for edge in network.findall("edge"):
        for lane in edge.findall("lane"):
            points = []
            for token in lane.get("shape", "").split():
                longitude, latitude = projected.transform(*map(float, token.split(",")[:2]))
                point = [round(scale * (longitude - origin["longitude_deg"]) / 360, 2),
                         round(-scale * (mercator(latitude) - mercator(origin["latitude_deg"])), 2)]
                if not points or point != points[-1]:
                    points.append(point)
            source_lanes[lane.get("id")] = points
            if edge.get("function") == "walkingarea":
                source[edge.get("id")] = points
    if any(source_lanes.get(record["id"]) != record["shape"]
           for record in [*road["lanes"], *road["crossings"]]):
        raise ValueError("Pedestrian connection ports differ from projected source lanes")
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("Duplicate walking-area source records")
    if any(source.get(record["id"]) != record["shape"] for record in records):
        raise ValueError("Walking-area coordinates differ from the source network")
    walk = unary_union([Polygon(p["outline"], p["holes"]) for p in road["walkbed"]])
    asphalt = unary_union([Polygon(p["outline"], p["holes"]) for p in road["roadbed"]])
    connections = layout["pedestrian_connections"]
    allowed_asphalt = asphalt.buffer(.001)
    for record in connections["shared_areas"]:
        for part in record["polygons"]:
            polygon = Polygon(part["outline"], part["holes"])
            if not polygon.is_valid or polygon.difference(allowed_asphalt).area > .001:
                raise ValueError(f"Shared pedestrian connection leaves displayed asphalt: {record['id']}")
    buildings = unary_union([affinity.translate(affinity.rotate(
        box(-item["width"] / 2, -item["depth"] / 2, item["width"] / 2, item["depth"] / 2),
        -item["rotation_deg"], origin=(0, 0)), xoff=item["x"], yoff=item["z"])
        for item in [*placement["placements"], *placement["complete_footprint_envelopes"]]])
    failures = audit_crossing_endpoints(network, records, road["crossings"], road["lanes"],
        asphalt, walk, buildings, connections["shared_areas"], layout["connection_paths"])
    if layout["connection_path_omissions"]:
        raise ValueError(f"Pedestrian connection paths were omitted: {layout['connection_path_omissions']}")
    if failures:
        raise ValueError(f"Disconnected crossing endpoints: {failures}")
    return {"checked_endpoints": 2 * len(road["crossings"]), "failures": [],
            "shared_area_count": len(connections["shared_areas"]),
            "connection_path_count": len(layout["connection_paths"]),
            "guide_segment_count": sum(m["kind"] == "pedestrian_connection_guide" for m in layout["markings"])}


def main(scene_path: Path, network_path: Path, report_path: Path, road_path: Path | None = None,
         source_osm_path: Path | None = None, traffic_path: Path | None = None) -> dict:
    paths = read_scene_paths(scene_path)
    traffic = json.loads((traffic_path or paths.traffic).read_text())
    road = json.loads((road_path or paths.road).read_text())
    if road.get("schema_version") != "aero-bench.city-road-preview/v2":
        raise ValueError("Road audit requires the current v2 surface and marking contract")
    pack = json.loads(paths.pack.read_text())
    placement_sha = hashlib.sha256(paths.placement.read_bytes()).hexdigest()
    network_sha = hashlib.sha256(network_path.read_bytes()).hexdigest()
    if traffic["source_network_sha256"] != network_sha or road["source_network_sha256"] != network_sha:
        raise ValueError("Road and traffic do not match the source SUMO network")
    engineering = json.loads((network_path.parent / "engineering-inputs.json").read_text())
    source_osm = source_osm_path or Path(engineering["source_osm"])
    if not source_osm.is_absolute():
        source_osm = ROOT / source_osm
    source_bytes = source_osm.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if (source_sha != engineering["source_osm_sha256"] or source_sha != traffic["source_osm_sha256"]
            or source_sha != road["source_osm_sha256"]
            or road["crossing_policy"] != "osm-zebra-or-sumo-controlled-multiway"):
        raise ValueError("Crossing geometry does not match the source OSM and visual policy")
    network_root = ET.fromstring(network_path.read_bytes())
    if engineering.get("road_scope") == "ground-only":
        if road.get("road_scope") != "ground-only" or traffic.get("road_scope") != "ground-only":
            raise ValueError("Ground-only road and recording must share the reduced network scope")
        report = json.loads((network_path.parent / engineering["ground_filter_report"]).read_text())
        removed = set(report["removed_edge_ids"])
        edge_ids = {edge.attrib["id"] for edge in network_root.findall("edge")}
        if removed & edge_ids:
            raise ValueError("Ground-only SUMO network still contains excluded edges")
        lane_ids = {lane.attrib["id"] for edge in network_root.findall("edge") for lane in edge.findall("lane")}
        observed = {sample[0] for frame in traffic["frames"] for sample in frame["vehicles"]}
        usage = traffic.get("vehicle_lane_use", {})
        if not observed.issubset(usage):
            raise ValueError("Ground-only traffic lacks vehicle lane provenance")
        for vehicle, used in usage.items():
            if not used or not set(used).issubset(lane_ids):
                raise ValueError(f"Vehicle references a lane outside the ground network: {vehicle}")
        if any(lane["id"] not in lane_ids for lane in road["lanes"]):
            raise ValueError("Displayed road lane is absent from the reduced network")
    bike_lanes = {lane.attrib["id"]: (edge, lane)
                  for edge in network_root.findall("edge") for lane in edge.findall("lane")}
    bike_use = traffic.get("bicycle_lane_use", {})
    if (traffic.get("bicycle_route_policy") != "paved-building-clear-at-most-three-lanes"
            or len(bike_use) != traffic["demand"]["authored"]["bicycle"]):
        raise ValueError("Recorded bicycle routes lack complete lane provenance")
    bike_normal_lanes: set[str] = set()
    bike_lane_kinds: dict[str, str] = {}
    for bicycle_id, used_lanes in bike_use.items():
        if not bicycle_id.startswith("bicycle.") or not used_lanes:
            raise ValueError(f"Recorded bicycle has no lane observations: {bicycle_id}")
        for lane_id in used_lanes:
            if lane_id not in bike_lanes:
                raise ValueError(f"Recorded bicycle lane is absent from the SUMO network: {lane_id}")
            edge, lane = bike_lanes[lane_id]
            allowed = set(lane.get("allow", "").split())
            denied = set(lane.get("disallow", "").split())
            if ((allowed and "all" not in allowed and "bicycle" not in allowed)
                    or "bicycle" in denied or "all" in denied):
                raise ValueError(f"Bicycle used a lane that excludes bicycles: {lane_id}")
            if edge.get("function", "normal") == "internal":
                continue
            kind = lane_kind(lane)
            if sum(lane_kind(item) != "walk" for item in edge.findall("lane")) > 3 \
                    or kind not in ("motor", "shared", "cycle"):
                raise ValueError(f"Bicycle used a wide or pedestrian-only edge: {lane_id}")
            bike_normal_lanes.add(lane_id)
            bike_lane_kinds[lane_id] = kind
    if not bike_normal_lanes:
        raise ValueError("Bicycle observations contain no ordinary roadway or cycleway lanes")
    neighbors = motor_neighbors(network_root)
    source_root = ET.fromstring(source_bytes)
    audit_v2_surfaces(road, network_root, source_root, pack,
                      "/" + paths.pack.parent.relative_to(ROOT / "frontend/public").as_posix() + "/")
    pedestrian_connection_audit = audit_pedestrian_connections(road, network_root, pack,
        json.loads(paths.placement.read_text()))
    zebra_nodes = zebra_crossing_nodes(source_root)
    visible_crossing_nodes = visible_crossing_junctions(network_root, source_root)
    junction_types = {junction.attrib["id"]: junction.get("type")
                      for junction in network_root.findall("junction")}
    source_crossing_nodes = {node.attrib["id"] for node in source_root.findall("node")
                             if any(tag.get("k") == "highway" and tag.get("v") == "crossing"
                                    or tag.get("k") in ("crossing", "crossing_ref", "crossing:markings")
                                    for tag in node.findall("tag"))}
    visible_crossings: list[str] = []
    omitted_unmarked_crossings: list[str] = []
    crossing_origin_counts: Counter[str] = Counter()
    for edge in network_root.findall("edge"):
        if edge.get("function") != "crossing":
            continue
        junction_id = crossing_junction_id(edge.attrib["id"])
        destination = visible_crossings if junction_id in visible_crossing_nodes else omitted_unmarked_crossings
        origin = ("osm_explicit_zebra" if junction_id in zebra_nodes else
                  "sumo_controlled_multiway" if junction_id in visible_crossing_nodes
                  and junction_types.get(junction_id) == "traffic_light" else
                  "osm_crossing_without_zebra" if junction_id in source_crossing_nodes else
                  "sumo_inferred_multiway" if len(neighbors.get(junction_id, set())) >= 3 else
                  "sumo_inferred_other")
        lane_ids = [lane.attrib["id"] for lane in edge.findall("lane") if lane.get("shape")]
        destination.extend(lane_ids)
        crossing_origin_counts[origin] += len(lane_ids)
    if (visible_crossings != [crossing["id"] for crossing in road["crossings"]]
            or omitted_unmarked_crossings != road["omitted_unmarked_crossings"]):
        raise ValueError("Displayed zebra crossings differ from OSM tags or SUMO controlled junctions")
    sidewalk_routes = traffic.get("pedestrian_route_policy") == "explicit-sidewalk-lanes-only"
    if sidewalk_routes:
        edges = {edge.get("id"): edge for edge in network_root.findall("edge")
                 if edge.get("function", "normal") == "normal"}
        routes = traffic["person_route_edges"]
        if len(routes) != traffic["demand"]["persons"] or any(
                edge_id not in edges or not any(
                    lane.get("allow") == "pedestrian" for lane in edges[edge_id].findall("lane"))
                for edge_id in routes.values()):
            raise ValueError("Pedestrian preview route does not use an explicit sidewalk lane")
    if (road["building_placement_sha256"] != placement_sha
            or traffic["mesh_pack_source_sha256"] != pack["source"]["sha256"]
            or road["mesh_pack_source_sha256"] != pack["source"]["sha256"]):
        raise ValueError("Road, traffic and building placement do not match the city mesh source")
    if road["source_asphalt_texture"] != paths.road_surface_texture:
        raise ValueError("Road asphalt material differs from the scene manifest")
    signal_coverage = road["signal_road_coverage"]
    if set(signal_coverage) != {signal["id"] for signal in traffic["signals"]} or any(
            not 0 <= value <= 1 for value in signal_coverage.values()):
        raise ValueError("Road coverage scores do not match recorded traffic signals")
    osm_signal_nodes = {node.attrib["id"] for node in source_root.findall("node")
                        if any(tag.get("k") == "highway" and tag.get("v") == "traffic_signals"
                               for tag in node.findall("tag"))}
    controlled_nodes = {logic.attrib["id"] for logic in network_root.findall("tlLogic")}
    if not osm_signal_nodes or controlled_nodes != osm_signal_nodes:
        raise ValueError("SUMO signal controllers differ from tagged OSM signal nodes")
    normal_edges = {edge.attrib["id"]: edge for edge in network_root.findall("edge")
                    if edge.get("function", "normal") == "normal"}
    motor_connections: dict[tuple[str, str], list[ET.Element]] = {}
    for connection in network_root.findall("connection"):
        tls, source = connection.get("tl"), connection.get("from")
        if not tls or source not in normal_edges or connection.get("linkIndex") is None:
            continue
        lanes = normal_edges[source].findall("lane")
        index = int(connection.attrib["fromLane"])
        if index >= len(lanes):
            raise ValueError(f"Signal connection references a missing lane: {source}_{index}")
        lane = lanes[index]
        allowed = set(lane.get("allow", "").split())
        denied = set(lane.get("disallow", "").split())
        if not any((not allowed or "all" in allowed or kind in allowed)
                   and kind not in denied and "all" not in denied
                   for kind in ("passenger", "bus", "truck")):
            continue
        motor_connections.setdefault((tls, source), []).append(connection)
    observed_signals = {signal["id"]: signal for signal in traffic["signals"]}
    expected_signals = {f"{tls}:{source}" for tls, source in motor_connections}
    if set(observed_signals) != expected_signals:
        raise ValueError("Displayed vehicle lights include walking areas or omit a motor approach")
    road_lanes = {lane["id"]: lane for lane in road["lanes"]}
    head_offsets = []
    for (tls, source), connections in motor_connections.items():
        signal = observed_signals[f"{tls}:{source}"]
        rightmost = min(int(item.attrib["fromLane"]) for item in connections)
        matching = [item for item in connections if int(item.attrib["fromLane"]) == rightmost]
        straight = [item for item in matching if item.get("dir") == "s"]
        selected = straight or matching
        if signal["link"] != min(int(item.attrib["linkIndex"]) for item in selected):
            raise ValueError(f"Vehicle light reads a phase from another lane: {signal['id']}")
        lane_id = f"{source}_{rightmost}"
        if lane_id not in road_lanes:
            raise ValueError(f"Signal approach has no rendered motor lane: {lane_id}")
        line = LineString(road_lanes[lane_id]["shape"])
        before, after = line.coords[-2], line.coords[-1]
        expected_heading = math.degrees(math.atan2(after[0] - before[0], before[1] - after[1]))
        heading_error = (signal["heading"] - expected_heading + 180) % 360 - 180
        if abs(heading_error) > 3:
            raise ValueError(f"Vehicle signal faces away from its incoming lane: {signal['id']}")
        approach = line.interpolate(max(0, line.length - 2.5))
        angle = math.radians(signal["heading"])
        # The measured red lens of traffic_light_4 sits 2.82 m left of its pole.
        head = Point(signal["x"] - 2.82 * math.cos(angle),
                     signal["z"] - 2.82 * math.sin(angle))
        distance = head.distance(approach)
        if distance > 6.2:
            raise ValueError(f"Vehicle signal head misses its controlled lane: {signal['id']} ({distance:.2f} m)")
        head_offsets.append(distance)
    vehicle_surfaces = [Polygon(part["outline"], part["holes"]) for part in road["roadbed"]]
    walk_surfaces = [Polygon(part["outline"], part["holes"]) for part in road["walkbed"]]
    if any(not surface.is_valid for surface in vehicle_surfaces):
        raise ValueError("Generated roadbed contains invalid polygons")
    if any(not surface.is_valid for surface in walk_surfaces):
        raise ValueError("Generated walking surface contains invalid polygons")
    roadbed_area = sum(surface.area for surface in vehicle_surfaces)
    if abs(roadbed_area - road["roadbed_area_m2"]) / road["roadbed_area_m2"] > 0.001:
        raise ValueError("Generated roadbed tiles do not cover the declared road area")
    walkbed_area = sum(surface.area for surface in walk_surfaces)
    if abs(walkbed_area - road["walkbed_area_m2"]) / road["walkbed_area_m2"] > 0.001:
        raise ValueError("Generated walking tiles do not cover the declared walking area")
    curb_edges = road["curb_edges"]
    if len(curb_edges) < 100:
        raise ValueError("Road/walk boundary has too few rendered curb edges")
    walk_boundary = unary_union(walk_surfaces).boundary.buffer(0.02)
    road_union = unary_union(vehicle_surfaces)
    for index, coordinates in enumerate(curb_edges):
        if len(coordinates) < 2:
            raise ValueError(f"Rendered curb {index} has too few vertices")
        line = LineString(coordinates)
        if (not line.is_valid or line.length < 0.59
                or line.difference(walk_boundary).length > 0.02
                or line.distance(road_union) > 0.18):
            raise ValueError(f"Rendered curb {index} does not follow the road/walk boundary")
    vehicle_index = STRtree(vehicle_surfaces)
    visible_walk_overlap = sum(surface.intersection(vehicle_surfaces[index]).area
                               for surface in walk_surfaces for index in vehicle_index.query(surface))
    if visible_walk_overlap > 0.1:
        raise ValueError(f"Walking texture covers {visible_walk_overlap:.2f} m² of motor road")
    person_surfaces = [*vehicle_surfaces, *walk_surfaces]
    person_surfaces.extend(LineString(crossing["shape"]).buffer(
        crossing["width"] / 2, cap_style=2, join_style=2) for crossing in road["crossings"])
    person_index = STRtree(person_surfaces)
    walk_index = STRtree(walk_surfaces)
    paved_index = STRtree(person_surfaces[:len(vehicle_surfaces) + len(walk_surfaces)])
    building_placements = json.loads(paths.placement.read_text())["placements"]
    building_footprints = [affinity.translate(affinity.rotate(
        box(-item["width"] / 2, -item["depth"] / 2, item["width"] / 2, item["depth"] / 2),
        -item["rotation_deg"], origin=(0, 0)), xoff=item["x"], yoff=item["z"])
        for item in building_placements]
    building_index = STRtree(building_footprints)
    signal_points = [Point(item["x"], item["z"]) for item in traffic["signals"]]
    signal_index = STRtree(signal_points)
    pedestrian_points = [Point(item[1], item[2]) for frame in traffic["frames"]
                         for item in frame["persons"]]
    pedestrian_index = STRtree(pedestrian_points)
    vehicle_points = [Point(item[1], item[2]) for frame in traffic["frames"]
                      for item in frame["vehicles"]]
    vehicle_index_points = STRtree(vehicle_points)
    street_lamps = road.get("street_lamps", [])
    lamp_layout = road["street_lamp_layout"]
    if lamp_layout["stats"]["placed_count"] != len(street_lamps):
        raise ValueError("Street lamp placement inventory differs from its layout")
    walk_union = unary_union(walk_surfaces)
    pole_allowed_surface = walk_union.buffer(.003)
    authored_curb_boundary = walk_union.boundary
    for index, lamp in enumerate(street_lamps):
        point = Point(lamp["x"], lamp["z"])
        if not all(math.isfinite(lamp[key]) for key in ("x", "z", "rotation_deg")):
            raise ValueError(f"Street lamp {index} has invalid coordinates")
        if not pole_allowed_surface.covers(point.buffer(.33)):
            raise ValueError(f"Street lamp {index} pole is outside the sidewalk")
        boundary = lamp["source_boundary"]
        curb_point = Point(boundary["curb_point"])
        if (not boundary["id"] or boundary["station_m"] < 0
                or curb_point.distance(authored_curb_boundary) > .02
                or curb_point.distance(road_union) > .16
                or abs(curb_point.distance(point) - lamp_layout["stats"]["curb_offset_m"]) > .01):
            raise ValueError(f"Street lamp {index} no longer follows its authored curb")
        if any(building_footprints[part].distance(point) < 0.6 for part in building_index.query(point.buffer(0.6))):
            raise ValueError(f"Street lamp {index} clips a building proxy")
        if len(signal_index.query(point.buffer(1.4))):
            raise ValueError(f"Street lamp {index} clips a traffic signal pole")
        if len(pedestrian_index.query(point.buffer(0.65))):
            raise ValueError(f"Street lamp {index} clips a recorded pedestrian path")
        if len(vehicle_index_points.query(point.buffer(0.9))):
            raise ValueError(f"Street lamp {index} clips a recorded vehicle path")
        if any(math.hypot(lamp["x"] - other["x"], lamp["z"] - other["z"]) < 1.99
               for other in street_lamps[:index]):
            raise ValueError(f"Street lamp {index} duplicates a nearby pole")
    samples = 0
    missed = []
    bicycle_samples = 0
    missed_bicycles = []
    person_samples = 0
    missed_people = []
    on_walkway_people = 0
    for frame in traffic["frames"][::4]:  # 0.25 s source frames; audit at 1 s intervals.
        for vehicle in frame["vehicles"]:
            samples += 1
            if vehicle[4] == "bicycle":
                bicycle_samples += 1
            point = Point(vehicle[1], vehicle[2])
            footprint = point.buffer(0.6)
            nearby = vehicle_index.query(footprint)
            if not any(vehicle_surfaces[i].intersects(footprint) for i in nearby):
                failure = {"second": frame["second"], "id": vehicle[0],
                           "x": vehicle[1], "z": vehicle[2]}
                missed.append(failure)
                if vehicle[4] == "bicycle":
                    missed_bicycles.append(failure)
        for person in frame["persons"]:
            person_samples += 1
            point = Point(person[1], person[2])
            if any(walk_surfaces[i].covers(point) for i in walk_index.query(point)):
                on_walkway_people += 1
            footprint = point.buffer(0.6)
            nearby = person_index.query(footprint)
            if not any(person_surfaces[i].intersects(footprint) for i in nearby):
                missed_people.append({"second": frame["second"], "id": person[0],
                                      "x": person[1], "z": person[2]})
    if not person_samples:
        raise ValueError("Recorded traffic has no pedestrian samples")
    result = {
        "source_network_sha256": network_sha,
        "pedestrian_connections": pedestrian_connection_audit,
        "street_marking_count": len(road["street_layout"]["markings"]),
        "turn_arrow_count": len(road["street_layout"]["arrows"]),
        "walking_area_count": road["street_layout"]["walking_area_count"],
        "derived_sidewalk_area_m2": road["street_layout"]["sidewalk_stats"]["derived_walkbed_area_m2"],
        "road_lane_count": len(road["lanes"]),
        "internal_lane_count": road["internal_lane_count"],
        "roadbed_polygon_count": len(vehicle_surfaces),
        "roadbed_area_m2": round(roadbed_area, 2),
        "walkbed_polygon_count": len(walk_surfaces),
        "walkbed_area_m2": round(walkbed_area, 2),
        "curb_edge_count": len(curb_edges),
        "walking_texture_overlap_m2": round(visible_walk_overlap, 3),
        "road_junction_count": len(road["junctions"]),
        "road_crossing_count": len(road["crossings"]),
        "osm_signal_controller_count": len(osm_signal_nodes),
        "motor_signal_approach_count": len(observed_signals),
        "signal_head_median_lane_offset_m": round(sorted(head_offsets)[len(head_offsets) // 2], 3),
        "signal_head_max_lane_offset_m": round(max(head_offsets), 3),
        "street_lamp_count": len(street_lamps),
        "omitted_unmarked_crossing_count": len(omitted_unmarked_crossings),
        "crossing_origin_counts": dict(sorted(crossing_origin_counts.items())),
        "observed_bicycles": len(bike_use),
        "bicycle_non_internal_lane_count": len(bike_normal_lanes),
        "bicycle_lane_kind_counts": dict(sorted(Counter(bike_lane_kinds.values()).items())),
        "sample_interval_seconds": 1,
        "surface_tolerance_m": 0.6,
        "vehicle_samples": samples,
        "on_road_samples": samples - len(missed),
        "coverage_fraction": round((samples - len(missed)) / samples, 6),
        "off_road_samples": missed,
        "bicycle_samples": bicycle_samples,
        "on_road_bicycle_samples": bicycle_samples - len(missed_bicycles),
        "off_road_bicycle_samples": missed_bicycles,
        "pedestrian_samples": person_samples,
        "on_walkway_pedestrian_samples": on_walkway_people,
        "walkway_coverage_fraction": round(on_walkway_people / person_samples, 6),
        "on_surface_pedestrian_samples": person_samples - len(missed_people),
        "off_surface_pedestrian_samples": missed_people,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if result["coverage_fraction"] < 0.999:
        raise ValueError(f"Vehicle/road alignment below 99.9%: {result['coverage_fraction']}")
    if not bicycle_samples or missed_bicycles:
        raise ValueError(f"Bicycle/road alignment misses {len(missed_bicycles)} sampled positions")
    if missed_people:
        raise ValueError(f"Pedestrian/road alignment misses {len(missed_people)} sampled positions")
    if sidewalk_routes and result["walkway_coverage_fraction"] < 0.99:
        raise ValueError("Dedicated sidewalk routes fall below 99% visible walkway coverage")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--network", type=Path, default=DEFAULT_NETWORK)
    parser.add_argument("--source-osm", type=Path)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--road", type=Path, help="Audit a staged road file before publication")
    parser.add_argument("--traffic", type=Path, help="Audit a staged SUMO recording before publication")
    args = parser.parse_args()
    print(json.dumps(main(args.scene, args.network, args.report, args.road, args.source_osm, args.traffic),
                     ensure_ascii=False, indent=2))
