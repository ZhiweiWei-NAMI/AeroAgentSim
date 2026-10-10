#!/usr/bin/env python3
"""Build private v2 street geometry for a compiler-selected OSM region.

The output is a static candidate for placement and publication. It contains no
recorded vehicles, people, or invented signal phases.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from city_road_topology import lane_kind


SCRIPTS = Path(__file__).resolve().parent


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


road_builder = _module("selected_static_road_builder", "build-city-road-preview.py")
building_builder = _module("selected_static_building_source", "build-city-building-placements.py")
road_auditor = _module("selected_static_road_auditor", "audit-city-road-preview.py")


@dataclass(frozen=True)
class StaticRoadCandidate:
    road_path: Path
    signal_inventory_path: Path
    road_sha256: str
    signal_inventory_sha256: str
    source_network_sha256: str
    source_osm_sha256: str
    mesh_pack_source_sha256: str
    mesh_pack_manifest_sha256: str


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _asphalt_texture(pack_path: Path, pack: dict) -> str:
    candidates = {batch["material"]["base_color_texture"]
                  for batch in pack["batches"] if batch["layer"] == "roads"
                  and isinstance(batch["material"]["base_color_texture"], str)
                  and "/Asphalt" in batch["material"]["base_color_texture"]}
    if len(candidates) != 1:
        raise ValueError(f"Selected pack must declare exactly one asphalt road material: {sorted(candidates)}")
    texture = candidates.pop()
    if road_builder.pack_texture_asset(pack_path, pack, texture, private_candidate=True) is None:
        raise ValueError("Selected pack asphalt material has no verified texture asset")
    return texture


def _allows_motor(lane: ET.Element) -> bool:
    return lane_kind(lane) in ("motor", "shared")


def _signal_inventory(network: ET.Element, transformer: Transformer, origin: dict,
                      footprints: tuple, network_sha: str, pack_source_sha: str) -> dict:
    """Use each controller's actual incoming motor lane and link index."""
    edges = {edge.attrib["id"]: edge for edge in network.findall("edge")
             if edge.get("function", "normal") == "normal"}
    source_lanes = {lane.get("id"): lane for lane in network.findall(".//lane")}
    unique: dict[tuple[str, str], ET.Element] = {}
    for connection in network.findall("connection"):
        tls, source, link = connection.get("tl"), connection.get("from"), connection.get("linkIndex")
        if not tls or source not in edges or link is None:
            continue
        lanes = edges[source].findall("lane")
        index = int(connection.attrib["fromLane"])
        if index < 0 or index >= len(lanes) or not _allows_motor(lanes[index]):
            continue
        link_index = int(link)
        if link_index < 0:
            raise ValueError(f"SUMO signal has a negative link index: {tls}:{source}")
        key = (tls, source)
        rank = (index, connection.get("dir") != "s", link_index)
        prior = unique.get(key)
        if prior is None or rank < (int(prior.attrib["fromLane"]),
                                    prior.get("dir") != "s", int(prior.attrib["linkIndex"])):
            unique[key] = connection
    # These pole positions are private scaffolding for the first road/walk build.
    # The published positions are selected against that build's complete walkbed
    # and checked against its actual roadway, source roofs and target approach.
    signals = []
    for (tls, source), connection in sorted(unique.items()):
        lane = edges[source].findall("lane")[int(connection.attrib["fromLane"])]
        points = road_builder.shape(lane.get("shape"), transformer, origin)
        if len(points) < 2:
            raise ValueError(f"SUMO signal lane lacks a usable shape: {tls}:{source}")
        line = LineString(points)
        if line.length < .01:
            raise ValueError(f"SUMO signal lane has collapsed geometry: {tls}:{source}")
        x0, z0 = points[-2]
        x1, z1 = points[-1]
        dx, dz = x1 - x0, z1 - z0
        magnitude = math.hypot(dx, dz)
        forward = (dx / magnitude, dz / magnitude)
        right = (-forward[1], forward[0])
        heading = math.degrees(math.atan2(forward[0], -forward[1]))
        if line.length < 2.5:
            # The terminal lane can be a sub-metre SUMO junction remnant. Use
            # its connected straight predecessor's longer tangent when that
            # direction still matches the controlled lane's audited heading.
            predecessors = []
            for incoming in network.findall("connection"):
                if (incoming.get("to") != source
                        or int(incoming.get("toLane", "-1")) != int(connection.attrib["fromLane"])
                        or incoming.get("dir") != "s"):
                    continue
                predecessor = edges.get(incoming.get("from"))
                if predecessor is None:
                    continue
                index = int(incoming.get("fromLane", "-1"))
                predecessor_lanes = predecessor.findall("lane")
                if not 0 <= index < len(predecessor_lanes) or not _allows_motor(predecessor_lanes[index]):
                    continue
                prior_points = road_builder.shape(predecessor_lanes[index].get("shape"),
                                                  transformer, origin)
                if len(prior_points) < 2:
                    continue
                via_id = incoming.get("via")
                via = source_lanes.get(via_id)
                via_points = road_builder.shape(via.get("shape"), transformer, origin) if via is not None else []
                if via_id and (len(via_points) < 2
                               or Point(prior_points[-1]).distance(Point(via_points[0])) > .75
                               or Point(via_points[-1]).distance(Point(points[0])) > .75):
                    continue
                if not via_id and Point(prior_points[-1]).distance(Point(points[0])) > .75:
                    continue
                px0, pz0 = prior_points[-2]
                px1, pz1 = prior_points[-1]
                if math.hypot(px1 - px0, pz1 - pz0) < 2:
                    continue
                prior_heading = math.degrees(math.atan2(px1 - px0, pz0 - pz1))
                angle_error = abs((prior_heading - heading + 180) % 360 - 180)
                if angle_error <= 2.9:
                    predecessors.append((angle_error, incoming.get("from"), index, prior_heading))
            if predecessors:
                heading = min(predecessors)[3]
        initial = (x1 - forward[0] * 2.5 + right[0] * (float(lane.get("width", "3.2")) / 2 + 1),
                   z1 - forward[1] * 2.5 + right[1] * (float(lane.get("width", "3.2")) / 2 + 1))
        signals.append({"id": f"{tls}:{source}", "tls": tls,
                        "link": int(connection.attrib["linkIndex"]),
                        "x": round(initial[0], 2), "z": round(initial[1], 2),
                        "heading": round(heading, 1)})
    return {"schema_version": "aero-bench.city-static-signal-inventory/v1",
            "source_network_sha256": network_sha,
            "mesh_pack_source_sha256": pack_source_sha,
            "signals": signals}


def _fit_signals_to_displayed_walk(inventory: dict, preliminary_road: dict,
                                   network: ET.Element,
                                   footprints: tuple, transformer: Transformer,
                                   origin: dict) -> dict:
    """Place each controlled pole on the displayed walk beside its real approach."""
    walk = unary_union([Polygon(part["outline"], part["holes"])
                        for part in preliminary_road["walkbed"]])
    road = unary_union([Polygon(part["outline"], part["holes"])
                        for part in preliminary_road["roadbed"]])
    buildings = unary_union(footprints)
    lane_records = {lane["id"]: lane for lane in preliminary_road["lanes"]}
    motor_surface = unary_union([
        LineString(lane["shape"]).buffer(lane["width"] / 2, cap_style=2, join_style=2)
        for lane in lane_records.values()
        if lane["kind"] in ("motor", "shared", "cycle") and len(lane["shape"]) >= 2])
    source_edges = {edge.get("id"): edge for edge in network.findall("edge")
                    if edge.get("function", "normal") == "normal"}
    source_lanes = {lane.get("id"): lane for lane in network.findall(".//lane")}
    placed: list[Point] = []
    refined = []
    for signal in inventory["signals"]:
        source_edge = signal["id"].removeprefix(signal["tls"] + ":")
        selected_connections = [connection for connection in network.findall("connection")
                                if connection.get("tl") == signal["tls"]
                                and connection.get("from") == source_edge
                                and int(connection.get("linkIndex", "-1")) == signal["link"]]
        if not selected_connections:
            raise ValueError(f"Selected signal has no controlled lane: {signal['id']}")
        lane_index = min(int(connection.attrib["fromLane"]) for connection in selected_connections)
        lane = lane_records.get(f"{source_edge}_{lane_index}")
        if lane is None:
            raise ValueError(f"Selected signal has no displayed lane: {signal['id']}")
        lane_line = LineString(lane["shape"])
        approach = lane_line.interpolate(max(0, lane_line.length - 2.5))
        heading = math.radians(signal["heading"])
        right = (math.cos(heading), math.sin(heading))
        forward = (math.sin(heading), -math.cos(heading))
        selected = None

        def consider(x: float, z: float) -> bool:
            nonlocal selected
            x, z = round(x, 2), round(z, 2)
            center = Point(x, z)
            pole = center.buffer(.35)
            head = Point(x - 2.82 * right[0], z - 2.82 * right[1])
            if (not walk.covers(pole) or road.intersects(pole)
                    or buildings.intersects(pole)
                    or motor_surface.distance(pole) < .45
                    or head.distance(approach) > 6.2
                    or any(center.distance(other) < .8 for other in placed)):
                return False
            selected = {**signal, "x": x, "z": z}
            placed.append(center)
            return True

        for outward in (0, .25, .5, .75, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8):
            for along in (0, -.5, .5, -1, 1, -2, 2, -3, 3, -5, 5):
                if consider(signal["x"] + right[0] * outward + forward[0] * along,
                            signal["z"] + right[1] * outward + forward[1] * along):
                    break
            if selected is not None:
                break

        # A very short controlled edge can start immediately after an internal
        # connection. Follow that connection and its actual incoming motor lane
        # when the local sidewalk search cannot fit a pole.
        if selected is None:
            incoming = []
            for connection in network.findall("connection"):
                if connection.get("to") != source_edge or int(connection.get("toLane", "-1")) != lane_index:
                    continue
                predecessor = source_edges.get(connection.get("from"))
                if predecessor is None:
                    continue
                predecessor_index = int(connection.get("fromLane", "-1"))
                predecessor_lanes = predecessor.findall("lane")
                if not 0 <= predecessor_index < len(predecessor_lanes) or not _allows_motor(
                        predecessor_lanes[predecessor_index]):
                    continue
                predecessor_record = lane_records.get(predecessor_lanes[predecessor_index].get("id"))
                if predecessor_record is None or len(predecessor_record["shape"]) < 2:
                    continue
                predecessor_points = predecessor_record["shape"]
                via_id = connection.get("via")
                if via_id:
                    via = source_lanes.get(via_id)
                    via_points = (road_builder.shape(via.get("shape"), transformer, origin)
                                  if via is not None else [])
                    if (len(via_points) < 2
                            or Point(predecessor_points[-1]).distance(Point(via_points[0])) > .75
                            or Point(via_points[-1]).distance(Point(lane["shape"][0])) > .75):
                        continue
                else:
                    if Point(predecessor_points[-1]).distance(Point(lane["shape"][0])) > .75:
                        continue
                    via_points = []
                route = LineString(predecessor_points + via_points + lane["shape"])
                incoming.append((connection.get("dir") != "s", connection.get("from"),
                                 predecessor_index, route))
            for _, _, _, route in sorted(incoming, key=lambda item: item[:3]):
                for half_metre in sorted(range(1, min(48, int(route.length * 2)) + 1),
                                         key=lambda value: (abs(value - 5), value)):
                    distance = half_metre / 2
                    station = route.length - distance
                    base = route.interpolate(station)
                    before = route.interpolate(max(0, station - .1))
                    after = route.interpolate(min(route.length, station + .1))
                    dx, dz = after.x - before.x, after.y - before.y
                    magnitude = math.hypot(dx, dz)
                    if magnitude < .01:
                        continue
                    local_right = (-dz / magnitude, dx / magnitude)
                    for outward in (0, .25, .5, .75, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8):
                        offset = float(lane.get("width", 3.2)) / 2 + 1 + outward
                        if consider(base.x + local_right[0] * offset,
                                    base.y + local_right[1] * offset):
                            break
                    if selected is not None:
                        break
                if selected is not None:
                    break
        if selected is None:
            raise ValueError(f"No displayed sidewalk can hold SUMO signal pole {signal['id']}")
        refined.append(selected)
    return {**inventory, "signals": refined}


def build_selected_static_roads(*, network_path: Path, engineering_inputs_path: Path,
                                source_osm_path: Path, pack_manifest_path: Path,
                                output_path: Path, tile_size_m: int = 60) -> StaticRoadCandidate:
    if not isinstance(tile_size_m, int) or isinstance(tile_size_m, bool) or not 10 <= tile_size_m <= 200:
        raise ValueError("Road tile size must be an integer from 10 to 200 metres")
    network_path = Path(network_path)
    engineering_inputs_path = Path(engineering_inputs_path)
    source_osm_path = Path(source_osm_path)
    pack_manifest_path = Path(pack_manifest_path)
    output_path = Path(output_path)
    if output_path in (network_path, engineering_inputs_path, source_osm_path, pack_manifest_path):
        raise ValueError("Static road output must not replace an input")
    network_bytes = network_path.read_bytes()
    source_bytes = source_osm_path.read_bytes()
    pack_bytes = pack_manifest_path.read_bytes()
    engineering = json.loads(engineering_inputs_path.read_text())
    pack = json.loads(pack_bytes)
    network_sha = _sha(network_bytes)
    source_sha = _sha(source_bytes)
    pack_sha = _sha(pack_bytes)
    pack_source_sha = pack["source"]["sha256"]
    if engineering.get("network_sha256") != network_sha or engineering.get("source_osm_sha256") != source_sha:
        raise ValueError("Selected network engineering receipt does not match the supplied network and OSM")
    if pack.get("projection", {}).get("name") != "MetricMapProjection" or \
            pack["projection"].get("axes") != "east-up-south":
        raise ValueError("Selected mesh pack has an unsupported road projection")
    if not re.fullmatch(r"[0-9a-f]{64}", pack_source_sha):
        raise ValueError("Selected mesh pack lacks a valid effective OSM digest")
    root = ET.fromstring(network_bytes)
    location = root.find("location")
    if location is None or location.get("projParameter") != engineering.get("projection"):
        raise ValueError("Selected network projection differs from its engineering receipt")
    if [float(value) for value in location.get("netOffset", "").split(",")] != [0.0, 0.0]:
        raise ValueError("Selected network has an unsupported nonzero SUMO netOffset")
    transformer = Transformer.from_crs(CRS.from_proj4(location.attrib["projParameter"]),
                                       CRS.from_epsg(4326), always_xy=True)
    longitude, latitude = transformer.transform(0.0, 0.0)
    origin = pack["projection"]["origin"]
    if abs(latitude - origin["latitude_deg"]) > 1e-7 or \
            abs(longitude - origin["longitude_deg"]) > 1e-7:
        raise ValueError("Selected network and mesh pack use different geographic origins")
    roofs, _, _, _, _ = building_builder.building_geometry(pack, pack_manifest_path.parent)
    if not roofs:
        raise ValueError("Selected pack has no verified building roof footprints")
    footprints = tuple(roofs.values())
    asphalt = _asphalt_texture(pack_manifest_path, pack)
    inventory = _signal_inventory(root, transformer, origin, footprints,
                                  network_sha, pack_source_sha)
    signal_path = output_path.with_name(output_path.stem + "-signals.json")
    audit_path = output_path.with_name(output_path.stem + "-audit.json")
    if signal_path in (network_path, engineering_inputs_path, source_osm_path, pack_manifest_path):
        raise ValueError("Static signal output must not replace an input")
    if output_path.exists() or signal_path.exists() or audit_path.exists():
        raise FileExistsError("Selected road, signals or audit output already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="selected-road-", dir=output_path.parent) as temporary:
        preliminary_path = Path(temporary) / "preliminary.json"
        preliminary_sha = _sha(_json_bytes(inventory))
        preliminary_inputs = road_builder.BuildInputs(
            network_path, source_osm_path, None, pack_manifest_path, asphalt, None,
            preliminary_path, tile_size_m, static_footprints=footprints,
            static_signals=tuple(inventory["signals"]),
            static_signal_inventory_sha256=preliminary_sha,
            engineering_inputs=engineering_inputs_path)
        road_builder.build(preliminary_inputs)
        inventory = _fit_signals_to_displayed_walk(
            inventory, json.loads(preliminary_path.read_bytes()), root, footprints,
            transformer, origin)
        signal_bytes = _json_bytes(inventory)
        signal_sha = _sha(signal_bytes)
        with signal_path.open("xb") as stream:
            stream.write(signal_bytes)
        if signal_sha == preliminary_sha:
            with output_path.open("xb") as stream:
                stream.write(preliminary_path.read_bytes())
        else:
            final_inputs = road_builder.BuildInputs(
                network_path, source_osm_path, None, pack_manifest_path, asphalt, None,
                output_path, tile_size_m, static_footprints=footprints,
                static_signals=tuple(inventory["signals"]),
                static_signal_inventory_sha256=signal_sha,
                engineering_inputs=engineering_inputs_path)
            road_builder.build(final_inputs)
    road_bytes = output_path.read_bytes()
    road = json.loads(road_bytes)
    if road["schema_version"] != "aero-bench.city-static-road-candidate/v1" or \
            road["signal_inventory_sha256"] != signal_sha:
        raise ValueError("Static road candidate is not bound to its signal inventory")
    audit = road_auditor.audit_static_candidate(
        road_path=output_path, signal_inventory_path=signal_path, network_path=network_path,
        source_osm_path=source_osm_path, pack_manifest_path=pack_manifest_path,
        building_footprints=footprints)
    triangulation = subprocess.run(
        ["node", str(SCRIPTS / "audit-city-road-triangulation.mjs"), str(output_path)],
        cwd=SCRIPTS.parent, text=True, capture_output=True, check=True)
    audit["browser_triangulation"] = json.loads(triangulation.stdout)
    with audit_path.open("xb") as stream:
        stream.write(_json_bytes(audit))
    return StaticRoadCandidate(output_path, signal_path, _sha(road_bytes), signal_sha,
                               network_sha, source_sha, pack_source_sha, pack_sha)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--engineering-inputs", required=True, type=Path)
    parser.add_argument("--source-osm", required=True, type=Path)
    parser.add_argument("--pack-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tile-size-m", type=int, default=60)
    args = parser.parse_args()
    result = build_selected_static_roads(network_path=args.network,
        engineering_inputs_path=args.engineering_inputs, source_osm_path=args.source_osm,
        pack_manifest_path=args.pack_manifest, output_path=args.output,
        tile_size_m=args.tile_size_m)
    print(json.dumps({key: str(value) if isinstance(value, Path) else value
                      for key, value in vars(result).items()}, indent=2))
