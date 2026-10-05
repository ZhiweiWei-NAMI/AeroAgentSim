#!/usr/bin/env python3
"""Prepare engineering traffic on the existing real Shanghai OSM road closure.

This executes netconvert, then authors deterministic traffic demand. It is not
an execution attestation, a verifier, or a recorded simulation result.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROJECTION = "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"


def _allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed = lane.get("allow", "").split()
    denied = lane.get("disallow", "").split()
    return (not allowed or "all" in allowed or vehicle_class in allowed) and vehicle_class not in denied and "all" not in denied


def _drivable_edges(network: ET.Element) -> dict[str, ET.Element]:
    location = network.find("location")
    raw_boundary = location.get("convBoundary") if location is not None else None
    try:
        boundary = tuple(float(value) for value in (raw_boundary or "").split(","))
    except ValueError as exc:
        raise ValueError("real network has an invalid conversion boundary") from exc
    if (
        len(boundary) != 4
        or not all(math.isfinite(value) for value in boundary)
        or boundary[0] >= boundary[2]
        or boundary[1] >= boundary[3]
    ):
        raise ValueError("real network has an invalid conversion boundary")

    def lane_is_covered(lane: ET.Element) -> bool:
        points = lane.get("shape", "").split()
        if len(points) < 2:
            raise ValueError("real network has a drivable lane without a usable shape")
        try:
            coordinates = [
                tuple(float(value) for value in point.split(",")[:2])
                for point in points
            ]
        except ValueError as exc:
            raise ValueError("real network has an invalid drivable lane shape") from exc
        return all(
            len(coordinate) == 2
            and all(math.isfinite(value) for value in coordinate)
            and boundary[0] <= coordinate[0] <= boundary[2]
            and boundary[1] <= coordinate[1] <= boundary[3]
            for coordinate in coordinates
        )

    edges = {
        edge.attrib["id"]: edge
        for edge in network.findall("edge")
        if edge.get("function") != "internal"
    }
    result: dict[str, ET.Element] = {}
    for edge_id, edge in edges.items():
        passenger_lanes = [
            lane for lane in edge.findall("lane") if _allows(lane, "passenger")
        ]
        if passenger_lanes and all(lane_is_covered(lane) for lane in passenger_lanes):
            result[edge_id] = edge
    return result


def _cycle(graph: dict[str, list[str]], start: str) -> tuple[str, ...] | None:
    pending = deque([(start,)])
    seen = {start}
    while pending:
        path = pending.popleft()
        for successor in graph[path[-1]]:
            if successor == start:
                return path
            if successor not in seen:
                seen.add(successor)
                pending.append((*path, successor))
    return None


def _xml(path: Path, document: ET.Element) -> None:
    with path.open("xb") as stream:
        stream.write(ET.tostring(document, encoding="utf-8", xml_declaration=True) + b"\n")


def traffic_demand(output: Path, duration_seconds: int) -> None:
    network = ET.parse(output / "network.net.xml").getroot()
    edges = {
        edge.attrib["id"]: edge
        for edge in network.findall("edge")
        if edge.get("function") != "internal"
    }
    drivable = _drivable_edges(network)
    graph = {key: [] for key in drivable}
    for connection in network.findall("connection"):
        source, target = connection.get("from"), connection.get("to")
        if source in graph and target in graph:
            source_lane = drivable[source].findall("lane")[int(connection.attrib["fromLane"])]
            target_lane = drivable[target].findall("lane")[int(connection.attrib["toLane"])]
            if _allows(source_lane, "passenger") and _allows(target_lane, "passenger"):
                graph[source].append(target)
    graph = {key: sorted(set(values)) for key, values in graph.items()}
    cycles = []
    for edge_id in sorted(graph):
        cycle = _cycle(graph, edge_id)
        if cycle:
            cycles.append(cycle)
            if len(cycles) == 20:
                break
    if len(cycles) != 20:
        raise ValueError("real network needs 20 distinct drivable cycle starts for the declared traffic")
    walking = sorted((
        (max(float(lane.attrib["length"]) for lane in edge.findall("lane") if _allows(lane, "pedestrian")), key)
        for key, edge in edges.items() if any(_allows(lane, "pedestrian") for lane in edge.findall("lane"))
    ), reverse=True)
    if len(walking) < 5 or walking[4][0] < 20:
        raise ValueError("real network needs five usable walking edges")
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", {"id": "engineering.car", "vClass": "passenger", "maxSpeed": "5.0", "length": "4.0", "minGap": "2.0"})
    for index, cycle in enumerate(cycles, 1):
        length = sum(max(float(lane.attrib["length"]) for lane in drivable[key].findall("lane")) for key in cycle)
        repetitions = max(2, math.ceil((duration_seconds * 5.0 + 500.0) / length))
        vehicle = ET.SubElement(routes, "vehicle", {"id": f"veh.{index:02d}", "type": "engineering.car", "depart": "0", "departPos": "base", "departSpeed": "0"})
        ET.SubElement(vehicle, "route", {"edges": " ".join(cycle * repetitions)})
    for index, (length, edge_id) in enumerate(walking[:5], 1):
        # Authored pedestrian speed keeps each person present for the horizon;
        # positions themselves are produced only by SUMO, never by this tool.
        speed = min(1.2, (length - 5.0) / (duration_seconds + 30))
        person = ET.SubElement(routes, "person", {"id": f"ped.{index:02d}", "depart": "0", "departPos": "0"})
        ET.SubElement(person, "walk", {"edges": edge_id, "speed": str(speed), "arrivalPos": str(length - 1.0)})
    _xml(output / "routes.rou.xml", routes)
    _xml(output / "additional.add.xml", ET.Element("additional"))
    config = ET.Element("configuration")
    inputs = ET.SubElement(config, "input")
    for name, value in (("net-file", "network.net.xml"), ("route-files", "routes.rou.xml"), ("additional-files", "additional.add.xml")):
        ET.SubElement(inputs, name, {"value": value})
    _xml(output / "simulation.sumocfg", config)


def prepare(args: argparse.Namespace) -> Path:
    if not 1 <= args.duration_seconds <= 600:
        raise ValueError("engineering traffic duration must be between 1 and 600 seconds")
    source = Path(args.osm).resolve(strict=True)
    output = Path(args.output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,size=64m",
        "--mount", f"type=bind,source={source},target=/input/map.osm,readonly",
        "--mount", f"type=bind,source={output},target=/output",
        "--entrypoint", "netconvert", image,
        "--osm-files", "/input/map.osm", "--output-file", "/output/network.net.xml",
        "--proj", PROJECTION, "--offset.disable-normalization",
        "--sidewalks.guess", "--crossings.guess",
    ]
    with (output / "netconvert.log").open("xb") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    traffic_demand(output, args.duration_seconds)
    (output / "engineering-inputs.json").write_text(json.dumps({
        "schema_version": "aero-bench.urban-engineering-traffic-inputs/v1",
        "source_osm": str(source), "source_osm_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "image_config_id": image, "projection": PROJECTION,
        "demand": {"vehicles": 20, "pedestrians": 5, "vehicle_max_speed_m_s": 5.0, "duration_seconds": args.duration_seconds},
        "sidewalk_policy": "netconvert --sidewalks.guess --crossings.guess on real OSM roads",
        "simulation_executed": False, "formal_toolchain_attestation": False,
    }, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm", default=str(ROOT / "validation/scene-compiler-shanghai-v3/osm/sumo-network.osm"))
    parser.add_argument("--image", required=True, help="existing local real SUMO image; resolved to immutable config ID")
    parser.add_argument("--duration-seconds", type=int, default=120)
    parser.add_argument("--output", required=True)
    prepare(parser.parse_args())
