#!/usr/bin/env python3
"""Record a clearly labelled engineering preview from the Shanghai OSM SUMO network.

This is an offline visual preview, never a formal AERO-BENCH trace or evidence.
Vehicle, pedestrian, and signal samples are read from a real SUMO process through
TraCI. Positions and headings use the same canonical ENU conversion as the roads.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import sumolib
import traci

from city_native_signals import canonical_signal_inventory, signal_inventory
from city_ground_fleet import GROUND_FLEET, fleet_width_contract
from city_native_route_capabilities import active_ground_fleet_types, native_route_capabilities

ROOT = Path(__file__).resolve().parents[2]
SEED = 24427
STEP_SECONDS = 0.25
SUMO_MAX_SEED = 2**31 - 1
# Demand is rerun in SUMO after this offset; the model-size proxy audit found a bus/taxi overlap without it.
DEPARTURE_OFFSETS = {"vehicle.026": 15.0}
SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"


DEMO_TYPE_ORDER = ("sedan", "taxi", "sedan", "truck", "sedan", "police", "bus")
DEMO_ROUTE_REQUIREMENTS = {"require_multiple_motor_lane_edge":True, "require_bidirectional_motor_roads":True,
                           "minimum_authored_person_routes":36}
DEMO_DISPLAY_REQUIREMENTS = {"minimum_displayed_vehicle_count":50, "minimum_displayed_person_count":1}
MOTOR_ROUTE_COUNT = 20
MOTOR_BATCH_INTERVAL_SECONDS = 30.0
MOTOR_DEPARTURE_INTERVAL_SECONDS = 1.0
BICYCLE_DEPARTURE_START_SECONDS = 2.0
BICYCLE_DEPARTURE_INTERVAL_SECONDS = 7.0
PERSON_DEPARTURE_INTERVAL_SECONDS = 2.3
# The accepted A3 profile requires 50 displayed actors from 72 authored motor/bicycle routes.
DISPLAY_RETENTION_NUMERATOR = 50
DISPLAY_RETENTION_DENOMINATOR = 72


def motor_departure_seconds(ordinal: int) -> float:
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("Motor vehicle ordinals start at one")
    index = ordinal - 1
    vehicle_id = f"vehicle.{ordinal:03d}"
    return (index // MOTOR_ROUTE_COUNT) * MOTOR_BATCH_INTERVAL_SECONDS \
        + (index % MOTOR_ROUTE_COUNT) * MOTOR_DEPARTURE_INTERVAL_SECONDS \
        + DEPARTURE_OFFSETS.get(vehicle_id, 0.0)


def preview_schedule_limits(duration: int) -> dict[str, int]:
    if type(duration) is not int or not 30 <= duration <= 180:
        raise ValueError("Preview duration must be an integer from 30 to 180 seconds")
    latest_departure = duration - STEP_SECONDS
    motor = 0
    while max((motor_departure_seconds(index) for index in range(1, motor + 2)), default=0) <= latest_departure:
        motor += 1
    bicycles = max(0, math.floor((latest_departure - BICYCLE_DEPARTURE_START_SECONDS)
                                  / BICYCLE_DEPARTURE_INTERVAL_SECONDS) + 1)
    pedestrians = max(0, math.floor(latest_departure / PERSON_DEPARTURE_INTERVAL_SECONDS) + 1)
    return {"vehicles": motor, "bicycles": bicycles, "pedestrians": pedestrians}


def validate_preview_demand(*, seed: int, motor_vehicles: int, pedestrians: int,
                            bicycles: int, duration: int) -> None:
    values = {"vehicles": motor_vehicles, "pedestrians": pedestrians, "bicycles": bicycles}
    if type(seed) is not int or not 0 <= seed <= SUMO_MAX_SEED:
        raise ValueError(f"SUMO preview seed must be an integer from 0 to {SUMO_MAX_SEED}")
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("SUMO preview demand counts must be explicit nonnegative integers")
    limits = preview_schedule_limits(duration)
    exceeded = [f"{name}={values[name]} exceeds {limits[name]}" for name in values
                if values[name] > limits[name]]
    if exceeded:
        raise ValueError(
            f"Requested demand cannot depart within the {duration}s recording: " + ", ".join(exceeded)
        )


def authored_vehicle_counts(motor_vehicles: int, bicycles: int) -> dict[str, int]:
    counts = defaultdict(int)
    for index in range(motor_vehicles):
        wave, source_index = divmod(index, MOTOR_ROUTE_COUNT)
        counts[DEMO_TYPE_ORDER[(source_index + wave * 3) % len(DEMO_TYPE_ORDER)]] += 1
    counts["bicycle"] += bicycles
    return dict(counts)


def demo_authored_vehicle_counts() -> dict[str, int]:
    return authored_vehicle_counts(60, 12)


def configured_recording_parameters(*, duration: int, excluded_walk_edges: set[str],
                                    bicycle_cycle_indices: tuple[int, ...], seed: int,
                                    motor_vehicles: int, pedestrians: int, bicycles: int,
                                    authoring_source: dict | None = None) -> dict:
    validate_preview_demand(seed=seed, motor_vehicles=motor_vehicles, pedestrians=pedestrians,
                            bicycles=bicycles, duration=duration)
    expected_cycle_count = min(6, bicycles)
    if len(bicycle_cycle_indices) != expected_cycle_count \
            or len(set(bicycle_cycle_indices)) != expected_cycle_count:
        raise ValueError(
            f"Preview demand requires {expected_cycle_count} distinct paved bicycle cycles"
        )
    counts = authored_vehicle_counts(motor_vehicles, bicycles)
    active_type_count = sum(count > 0 for count in counts.values())
    total_vehicles = motor_vehicles + bicycles
    display_floor = max(
        active_type_count,
        math.ceil(total_vehicles * DISPLAY_RETENTION_NUMERATOR / DISPLAY_RETENTION_DENOMINATOR),
    ) if total_vehicles else 0
    parameters = {
        "duration_seconds":duration, "step_seconds":STEP_SECONDS, "seed":seed,
        "excluded_walk_edges":sorted(excluded_walk_edges),
        "bicycle_cycle_indices":list(bicycle_cycle_indices),
        "departure_offsets_seconds":{
            vehicle_id: offset for vehicle_id, offset in DEPARTURE_OFFSETS.items()
            if int(vehicle_id.rsplit(".", 1)[1]) <= motor_vehicles
        },
        "fleet_width_contract":fleet_width_contract(),
        "authored_vehicle_counts":counts,
        "route_requirements":{
            "require_multiple_motor_lane_edge":motor_vehicles > 0,
            "require_bidirectional_motor_roads":motor_vehicles > 0,
            "minimum_authored_person_routes":pedestrians,
        },
        "display_requirements":{
            "minimum_displayed_vehicle_count":display_floor,
            "minimum_displayed_person_count":1 if pedestrians else 0,
        },
        "paved_bicycle_cycle_count":expected_cycle_count,
    }
    if authoring_source is not None:
        parameters["authoring_source"] = authoring_source
    return parameters


def demo_recording_parameters(duration: int, excluded_walk_edges: set[str], bicycle_cycle_indices: tuple) -> dict:
    parameters = configured_recording_parameters(
        duration=duration, excluded_walk_edges=excluded_walk_edges,
        bicycle_cycle_indices=bicycle_cycle_indices, seed=SEED,
        motor_vehicles=60, pedestrians=36, bicycles=12,
    )
    if parameters["route_requirements"] != DEMO_ROUTE_REQUIREMENTS \
            or parameters["display_requirements"] != DEMO_DISPLAY_REQUIREMENTS:
        raise AssertionError("Accepted A3 demand constants drifted from the parameterized profile")
    return parameters


def validate_recording_parameters(parameters: object, *, duration: int,
                                  excluded_walk_edges: set[str],
                                  bicycle_cycle_indices: tuple[int, ...]) -> dict:
    if not isinstance(parameters, dict):
        raise ValueError("Materialized recording parameters must be an object")
    allowed = {
        "duration_seconds", "step_seconds", "seed", "excluded_walk_edges",
        "bicycle_cycle_indices", "departure_offsets_seconds", "fleet_width_contract",
        "authored_vehicle_counts", "route_requirements", "display_requirements",
        "paved_bicycle_cycle_count", "authoring_source",
    }
    required = allowed - {"authoring_source"}
    if set(parameters) - allowed or not required <= set(parameters):
        raise ValueError("Materialized recording parameters have missing or unknown fields")
    counts = parameters["authored_vehicle_counts"]
    active_ground_fleet_types(counts)
    if any(kind != "bicycle" and kind not in GROUND_FLEET for kind in counts):
        raise ValueError("Materialized recording parameters contain an unsupported motor type")
    bicycles = counts.get("bicycle", 0)
    motor_vehicles = sum(count for kind, count in counts.items() if kind != "bicycle")
    requirements = parameters["route_requirements"]
    if not isinstance(requirements, dict):
        raise ValueError("Materialized route requirements must be an object")
    pedestrians = requirements.get("minimum_authored_person_routes")
    source = parameters.get("authoring_source")
    if source is not None:
        source_fields = {
            "schema_version", "workspace_schema_version", "workspace_sha256",
            "workspace_size_bytes", "seed", "traffic",
        }
        workspace_sha256 = source.get("workspace_sha256") if isinstance(source, dict) else None
        if not isinstance(source, dict) or set(source) != source_fields \
                or source.get("schema_version") != "aero-bench.city-traffic-preview-demand/v1" \
                or not isinstance(source.get("workspace_schema_version"), str) \
                or not source["workspace_schema_version"].startswith("aero-bench.city-workspace/v") \
                or source.get("seed") != parameters.get("seed") \
                or source.get("traffic") != {"vehicles":motor_vehicles,
                                               "pedestrians":pedestrians,
                                               "bicycles":bicycles} \
                or not isinstance(workspace_sha256, str) or len(workspace_sha256) != 64 \
                or any(character not in "0123456789abcdef" for character in workspace_sha256) \
                or type(source.get("workspace_size_bytes")) is not int \
                or source["workspace_size_bytes"] <= 0:
            raise ValueError("Materialized authoring source does not match its workspace demand")
    expected = configured_recording_parameters(
        duration=duration, excluded_walk_edges=excluded_walk_edges,
        bicycle_cycle_indices=bicycle_cycle_indices, seed=parameters.get("seed"),
        motor_vehicles=motor_vehicles, pedestrians=pedestrians, bicycles=bicycles,
        authoring_source=source,
    )
    if parameters != expected:
        raise ValueError("Materialized recording parameters differ from their configured demand")
    return parameters


def allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed = lane.get("allow", "").split()
    denied = lane.get("disallow", "").split()
    return (not allowed or "all" in allowed or vehicle_class in allowed) and vehicle_class not in denied and "all" not in denied


def dedicated_walk_edges(edges: dict[str, ET.Element], excluded: set[str]) -> list[tuple[str, float]]:
    walkable = []
    for edge_id, edge in edges.items():
        # SUMO also lets people walk on unrestricted motor lanes. Use explicit
        # sidewalk lanes so the recorded people stay on pedestrian paving.
        lengths = [float(lane.get("length", "0")) for lane in edge.findall("lane")
                   if lane.get("allow") == "pedestrian"]
        if edge_id not in excluded and lengths and 45 <= max(lengths) <= 230:
            walkable.append((edge_id, max(lengths)))
    return walkable


def motor_lane_surfaces(network: ET.Element, aligned) -> list:
    """Use the renderer's motor corridors when selecting dedicated sidewalk routes."""
    from shapely.geometry import LineString
    from city_road_topology import lane_kind
    surfaces = []
    for edge in network.findall("edge"):
        if edge.get("function", "normal") not in ("normal", "internal"):
            continue
        for lane in edge.findall("lane"):
            if lane_kind(lane) not in ("motor", "shared", "cycle"):
                continue
            points = [aligned(*map(float, point.split(",")[:2]))
                      for point in lane.get("shape", "").split()]
            if len(points) >= 2:
                surfaces.append(LineString(points).buffer(
                    float(lane.get("width", "3.2")) / 2, cap_style=2, join_style=2).buffer(.15))
    return surfaces


def spread_cycles(network: ET.Element, vehicle_class: str, count: int,
                  *, require_count: bool = True) -> list[tuple[str, ...]]:
    if type(count) is not int or count < 1:
        raise ValueError("Distributed cycle inventory limit must be a positive integer")
    if type(require_count) is not bool:
        raise ValueError("Distributed cycle count requirement must be explicit")
    edges = {edge.get("id"): edge for edge in network.findall("edge") if edge.get("function") != "internal"}
    lanes_by_id = {lane.attrib["id"]: lane for edge in network.findall("edge") for lane in edge.findall("lane")}
    graph: dict[str, set[str]] = defaultdict(set)
    for connection in network.findall("connection"):
        source, target = connection.get("from"), connection.get("to")
        if source not in edges or target not in edges:
            continue
        source_lanes = edges[source].findall("lane")
        target_lanes = edges[target].findall("lane")
        from_lane = source_lanes[int(connection.attrib["fromLane"])]
        to_lane = target_lanes[int(connection.attrib["toLane"])]
        if allows(from_lane, vehicle_class) and allows(to_lane, vehicle_class) \
                and (not connection.get("via") or allows(lanes_by_id[connection.get("via")], vehicle_class)):
            graph[source].add(target)
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, destinations in graph.items():
        for target in destinations:
            reverse[target].add(source)

    def shortest(links: dict[str, set[str]], start: str) -> dict[str, str | None]:
        parent: dict[str, str | None] = {start: None}
        pending = deque([start])
        while pending:
            source = pending.popleft()
            for target in sorted(links[source]):
                if target not in parent:
                    parent[target] = source
                    pending.append(target)
        return parent

    def chain(parent: dict[str, str | None], target: str) -> list[str]:
        result = [target]
        while parent[result[-1]] is not None:
            result.append(parent[result[-1]])  # type: ignore[arg-type]
        result.reverse()
        return result

    centers = {}
    for edge_id in graph:
        lane = edges[edge_id].find("lane")
        if lane is None:
            continue
        points = [tuple(map(float, point.split(",")[:2])) for point in lane.get("shape", "").split()]
        if points:
            centers[edge_id] = ((points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2)
    by_cell: dict[tuple[int, int], list[str]] = defaultdict(list)
    for edge_id, (x, y) in centers.items():
        by_cell[(min(4, max(0, int((x + 500) / 200))), min(4, max(0, int((y + 500) / 200))))].append(edge_id)
    starts = []
    for cell in sorted(by_cell):
        starts.extend(sorted(by_cell[cell], key=lambda key: hashlib.sha256(key.encode()).digest())[:4])
    cycles = []
    used_cells = set()
    for start in starts:
        forward = shortest(graph, start)
        backward = shortest(reverse, start)
        candidates = [target for target in forward.keys() & backward.keys() if target != start and target in centers]
        if not candidates:
            continue
        candidates.sort(key=lambda target: (abs(math.hypot(centers[target][0] - centers[start][0], centers[target][1] - centers[start][1]) - 240), target))
        target = candidates[0]
        if math.hypot(centers[target][0] - centers[start][0], centers[target][1] - centers[start][1]) < 75:
            continue
        cycle = tuple(chain(forward, target) + chain(backward, target)[-2:0:-1])
        if len(cycle) < 4 or cycle[-1] not in graph or start not in graph[cycle[-1]]:
            continue
        cell = (int((centers[start][0] + 500) / 200), int((centers[start][1] + 500) / 200))
        if cell in used_cells and len(used_cells) < min(count, len(by_cell)):
            continue
        used_cells.add(cell)
        cycles.append(cycle)
        if len(cycles) >= count:
            break
    if require_count and len(cycles) < count:
        raise ValueError(
            f"Native SUMO network yielded {len(cycles)} distributed {vehicle_class} cycles; "
            f"{count} are required"
        )
    return cycles


def route_allows(network: ET.Element, path_edges: tuple[str, ...], vehicle_class: str) -> bool:
    """Respect both edge-lane and connecting internal-lane native permissions."""
    edges = {edge.attrib["id"]: edge for edge in network.findall("edge")}
    lanes = {lane.attrib["id"]: lane for edge in edges.values() for lane in edge.findall("lane")}
    if not path_edges or any(edge not in edges or not any(allows(lane, vehicle_class)
            for lane in edges[edge].findall("lane")) for edge in path_edges):
        return False
    connections = defaultdict(list)
    for connection in network.findall("connection"):
        connections[connection.get("from"), connection.get("to")].append(connection)
    for first, second in zip(path_edges, path_edges[1:]):
        if not any(allows(edges[first].findall("lane")[int(c.attrib["fromLane"])], vehicle_class)
                and allows(edges[second].findall("lane")[int(c.attrib["toLane"])], vehicle_class)
                and (not c.get("via") or allows(lanes[c.get("via")], vehicle_class))
                for c in connections[first, second]):
            return False
    return True


def write_routes(path: Path, network: ET.Element, excluded_walk_edges: set[str],
                 bicycle_cycle_indices: tuple[int, ...], parameters: dict) -> dict[str, int]:
    parameters = validate_recording_parameters(
        parameters,
        duration=parameters.get("duration_seconds"),
        excluded_walk_edges=excluded_walk_edges,
        bicycle_cycle_indices=bicycle_cycle_indices,
    )
    seed = parameters["seed"]
    authored_counts = parameters["authored_vehicle_counts"]
    motor_vehicles = sum(count for kind, count in authored_counts.items() if kind != "bicycle")
    bicycle_count = authored_counts.get("bicycle", 0)
    pedestrian_count = parameters["route_requirements"]["minimum_authored_person_routes"]
    departure_offsets = parameters["departure_offsets_seconds"]
    rng = random.Random(seed)
    routes = ET.Element("routes")
    types = {name:(item.vehicle_class,str(item.length_m),str(item.native_width_m),str(item.max_speed_mps))
             for name,item in GROUND_FLEET.items()}
    for name, (vclass, length, width, speed) in types.items():
        ET.SubElement(routes, "vType", {"id": name, "vClass": vclass, "length": length, "width": width,
                                        "maxSpeed": speed, "accel": "2.0", "decel": "4.0", "sigma": "0.35"})

    edges = {edge.get("id"): edge for edge in network.findall("edge") if edge.get("function") != "internal"}
    original = spread_cycles(
        network, "passenger", MOTOR_ROUTE_COUNT, require_count=False,
    ) if motor_vehicles else []
    if motor_vehicles and not original:
        raise ValueError("Native SUMO network has no lawful distributed passenger cycle")
    class_cycles = {}
    emitted = defaultdict(int)
    type_order = DEMO_TYPE_ORDER
    route_rotation = (seed - SEED) % len(original) if original else 0
    for count in range(1, motor_vehicles + 1):
        wave, source_index = divmod(count - 1, MOTOR_ROUTE_COUNT)
        path_edges = original[(source_index + route_rotation) % len(original)]
        name = type_order[(source_index + wave * 3) % len(type_order)]
        vehicle_class = types[name][0]
        repeated_path = path_edges * 3
        if not route_allows(network, repeated_path, vehicle_class):
            if vehicle_class not in class_cycles:
                class_cycles[vehicle_class] = spread_cycles(network, vehicle_class, 1)[0]
            repeated_path = class_cycles[vehicle_class] * 3
            if not route_allows(network, repeated_path, vehicle_class):
                raise ValueError(f"Native network has no lawful authored {name}/{vehicle_class} route")
        vehicle_id = f"vehicle.{count:03d}"
        vehicle = ET.SubElement(routes, "vehicle", {"id": vehicle_id, "type": name,
                                                   "depart": f"{motor_departure_seconds(count):.1f}",
                                                   "departLane": "best", "departPos": "base"})
        if departure_offsets.get(vehicle_id, 0.0) != DEPARTURE_OFFSETS.get(vehicle_id, 0.0):
            raise ValueError(f"Materialized departure offset drifted for {vehicle_id}")
        ET.SubElement(vehicle, "route", {"edges": " ".join(repeated_path)})
        emitted[name] += 1

    cycle_inventory = spread_cycles(
        network, "bicycle", 12, require_count=False,
    ) if bicycle_count else []
    unavailable_bicycle_indices = sorted(
        index for index in bicycle_cycle_indices
        if type(index) is not int or index < 0 or index >= len(cycle_inventory)
    )
    if unavailable_bicycle_indices:
        raise ValueError(
            "Requested paved bicycle cycle indices are unavailable in the native SUMO network: "
            + ", ".join(map(str, unavailable_bicycle_indices))
        )
    bicycle_cycles = [cycle_inventory[index] for index in bicycle_cycle_indices]
    bicycle_rotation = (seed - SEED) % len(bicycle_cycles) if bicycle_cycles else 0
    for index in range(1, bicycle_count + 1):
        cycle = bicycle_cycles[(index - 1 + bicycle_rotation) % len(bicycle_cycles)]
        vehicle = ET.SubElement(routes, "vehicle", {"id": f"bicycle.{index:03d}", "type": "bicycle",
                                                   "depart": f"{(index - 1) * BICYCLE_DEPARTURE_INTERVAL_SECONDS + BICYCLE_DEPARTURE_START_SECONDS:.1f}",
                                                   "departLane": "best"})
        ET.SubElement(vehicle, "route", {"edges": " ".join(cycle * 3)})
        emitted["bicycle"] += 1
    if not bicycle_count:
        emitted["bicycle"] += 0

    walkable = dedicated_walk_edges(
        {edge_id: edge for edge_id, edge in edges.items() if edge.get("function", "normal") == "normal"},
        excluded_walk_edges)
    rng.shuffle(walkable)
    if len(walkable) < pedestrian_count:
        raise ValueError(
            f"Native network has {len(walkable)} usable dedicated walk edges for {pedestrian_count} pedestrians"
        )
    for index, (edge_id, length) in enumerate(walkable[:pedestrian_count], 1):
        person = ET.SubElement(routes, "person", {"id": f"person.{index:03d}",
                                                 "depart": f"{(index - 1) * PERSON_DEPARTURE_INTERVAL_SECONDS:.1f}",
                                                 "departPos": "0"})
        ET.SubElement(person, "walk", {"edges": edge_id, "arrivalPos": f"{length - 1:.2f}",
                                       "speed": f"{rng.uniform(1.0, 1.5):.2f}"})
    routes[:] = list(routes[:len(types)]) + sorted(routes[len(types):], key=lambda item: (float(item.attrib["depart"]), item.attrib["id"]))
    path.write_bytes(ET.tostring(routes, encoding="utf-8", xml_declaration=True))
    demand = dict(emitted)
    if demand != authored_counts:
        raise ValueError("Actual authored route counts differ from the materialized workspace demand")
    return demand


def align_native_payload(payload: dict, projection) -> None:
    """Convert native SUMO positions and directions once, preserving observations."""
    if payload.get("schema_version") != "aero-bench.city-sumo-native-recording/v1":
        raise ValueError("ENU conversion requires the native SUMO recording contract")
    for signal in payload["signals"]:
        native_x, native_y = signal["x"], signal["z"]
        signal["heading"] = projection.heading(native_x, native_y, signal["heading"])
        signal["x"], signal["z"] = projection.point(native_x, native_y)
    for frame in payload["frames"]:
        vehicles = []
        for sample in frame["vehicles"]:
            if len(sample) != 8:
                raise ValueError("Native vehicle observations require front position, angle, z, length and width")
            angle = math.radians(sample[3])
            native_x = sample[1] - math.sin(angle) * sample[6] / 2
            native_y = sample[2] - math.cos(angle) * sample[6] / 2
            east, south = projection.point(native_x, native_y)
            vehicles.append([sample[0], east, south,
                             projection.heading(native_x, native_y, sample[3]), sample[4], sample[5]])
        frame["vehicles"] = vehicles
        for sample in frame["persons"]:
            if len(sample) != 5:
                raise ValueError("Native pedestrian observations require position, angle and native z")
            native_x, native_y = sample[1], sample[2]
            sample[3] = projection.heading(native_x, native_y, sample[3])
            sample[1], sample[2] = projection.point(native_x, native_y)
    payload["schema_version"] = "aero-bench.city-sumo-preview/v2"
    payload["vehicle_position_reference"] = "center-derived-from-native-TraCI-front-bumper-and-length"
    payload["vehicle_fields"] = ["id", "center_east_m", "center_south_m", "clockwise_from_north_deg", "type", "render_up_m"]
    payload["person_fields"] = ["id", "east_m", "south_m", "clockwise_from_north_deg", "render_up_m"]


def build(output: Path, duration: int, excluded_walk_edges: set[str],
          bicycle_cycle_indices: tuple[int, ...], network_path: Path, pack_path: Path,
          recording_inputs_path: Path) -> None:
    if not 30 <= duration <= 180:
        raise ValueError("Preview duration must be 30–180 seconds")
    network_xml = ET.parse(network_path).getroot()
    pack_manifest = json.loads(pack_path.read_text())
    inputs = json.loads(recording_inputs_path.read_text())
    if inputs.get("schema_version") != "aero-bench.city-sumo-recording-inputs/v2":
        raise ValueError("Native recording requires explicit v2 materialized inputs")
    context = inputs["source_context"]
    expected_parameters = validate_recording_parameters(
        inputs.get("recording_parameters"), duration=duration,
        excluded_walk_edges=excluded_walk_edges,
        bicycle_cycle_indices=bicycle_cycle_indices,
    )
    if context["source_network_sha256"] != hashlib.sha256(network_path.read_bytes()).hexdigest() \
            or context["mesh_pack_manifest_sha256"] != hashlib.sha256(pack_path.read_bytes()).hexdigest() \
            or context["mesh_pack_source_sha256"] != pack_manifest["source"]["sha256"]:
        raise ValueError("Native SUMO/mesh inputs differ from the recording source context")
    capabilities = native_route_capabilities(network_xml, excluded_walk_edges,
        expected_parameters["authored_vehicle_counts"], expected_parameters["route_requirements"])
    if capabilities["status"] != "PASS" or inputs.get("native_route_capabilities") != capabilities:
        raise ValueError("Native fleet/multiple-lane/bidirectional/walking route capability gate did not pass")
    signals = signal_inventory(network_xml)
    with tempfile.TemporaryDirectory(prefix="aero-city-sumo-") as temporary:
        routes_path = Path(temporary) / "preview.rou.xml"
        demand = write_routes(
            routes_path, network_xml, excluded_walk_edges, bicycle_cycle_indices,
            expected_parameters,
        )
        if demand != expected_parameters["authored_vehicle_counts"]:
            raise ValueError("Actual authored native types differ from the explicitly configured nonzero demand")
        if hashlib.sha256(routes_path.read_bytes()).hexdigest() != inputs.get("expected_route_sha256"):
            raise ValueError("Actual native authored route bytes differ from their materialized preflight")
        person_route_edges = {person.attrib["id"]: person.find("walk").attrib["edges"]
                              for person in ET.parse(routes_path).getroot().findall("person")}
        command = [sumolib.checkBinary("sumo"), "-n", str(network_path), "-r", str(routes_path),
                   "--begin", "0", "--end", str(duration), "--step-length", str(STEP_SECONDS),
                   "--time-to-teleport", "-1", "--seed", str(expected_parameters["seed"]),
                   "--no-step-log", "true",
                   "--duration-log.disable", "true", "--no-warnings", "true"]
        frames = []
        observed = defaultdict(set)
        bicycle_lane_use: dict[str, set[str]] = defaultdict(set)
        vehicle_lane_use: dict[str, set[str]] = defaultdict(set)
        collisions = set()
        try:
            traci.start(command, label="city-preview")
            connection = traci.getConnection("city-preview")
            for step in range(round(duration / STEP_SECONDS) + 1):
                if step:
                    connection.simulationStep()
                second = round(connection.simulation.getTime(), 2)
                vehicles = []
                for vehicle_id in sorted(connection.vehicle.getIDList()):
                    sx, sy, native_z = connection.vehicle.getPosition3D(vehicle_id)
                    vehicle_type = connection.vehicle.getTypeID(vehicle_id)
                    vehicle_lane_use[vehicle_id].add(connection.vehicle.getLaneID(vehicle_id))
                    if vehicle_type == "bicycle":
                        bicycle_lane_use[vehicle_id].add(connection.vehicle.getLaneID(vehicle_id))
                    angle = connection.vehicle.getAngle(vehicle_id)
                    if native_z != 0:
                        raise ValueError(f"Ground-only SUMO vehicle has nonzero native elevation: {vehicle_id}")
                    vehicles.append([vehicle_id, sx, sy, angle, vehicle_type, native_z,
                                     connection.vehicle.getLength(vehicle_id), connection.vehicle.getWidth(vehicle_id)])
                    observed["vehicles"].add(vehicle_id)
                    observed[vehicle_type].add(vehicle_id)
                persons = []
                for person_id in sorted(connection.person.getIDList()):
                    sx, sy, native_z = connection.person.getPosition3D(person_id)
                    if native_z != 0:
                        raise ValueError(f"Ground-only SUMO pedestrian has nonzero native elevation: {person_id}")
                    persons.append([person_id, sx, sy, connection.person.getAngle(person_id), native_z])
                    observed["persons"].add(person_id)
                tls_states = {tls_id: connection.trafficlight.getRedYellowGreenState(tls_id)
                              for tls_id in sorted(connection.trafficlight.getIDList())}
                collisions.update(connection.simulation.getCollidingVehiclesIDList())
                frames.append({"second": second, "vehicles": vehicles, "persons": persons, "tls": tls_states})
                if step and second % 20 == 0:
                    print(f"SUMO preview: {second}/{duration}s, {len(vehicles)} vehicles, {len(persons)} people", flush=True)
        finally:
            if traci.isLoaded():
                traci.close()
        # Keep the zero-demand census explicit for the independent auditor.
        observed["vehicles"]
        observed["persons"]
        expected_vehicles = sum(expected_parameters["authored_vehicle_counts"].values())
        expected_persons = expected_parameters["route_requirements"]["minimum_authored_person_routes"]
        if (expected_vehicles and not observed["vehicles"]) \
                or (expected_persons and not observed["persons"]) \
                or not any(frame["tls"] for frame in frames):
            raise ValueError("SUMO produced incomplete requested traffic, pedestrian, or signal observations")
        payload = {
            "schema_version": "aero-bench.city-sumo-native-recording/v1",
            "artifact_class": "offline-engineering-preview",
            "source_kind": "offline-sumo-engineering-preview",
            "source_network_sha256": hashlib.sha256(network_path.read_bytes()).hexdigest(),
            "road_scope": "ground-only-native-network",
            "source_osm_sha256": context["source_osm_sha256"],
            "route_sha256": hashlib.sha256(routes_path.read_bytes()).hexdigest(),
            "mesh_pack_source_sha256": pack_manifest["source"]["sha256"],
            "sumo_image_id": SUMO_IMAGE,
            "sumo_version": subprocess.check_output([command[0], "--version"], text=True).splitlines()[0],
            "seed": expected_parameters["seed"], "duration_seconds": duration,
            "step_seconds": STEP_SECONDS,
            "demand_departure_offsets_seconds": expected_parameters["departure_offsets_seconds"],
            "display_requirements":expected_parameters["display_requirements"],
            "paved_bicycle_cycle_count":expected_parameters["paved_bicycle_cycle_count"],
            "source_context": context,
            "visual_obstacle_basis": inputs["visual_obstacle_basis"],
            "recording_inputs_sha256": hashlib.sha256(recording_inputs_path.read_bytes()).hexdigest(),
            "vehicle_position_reference": "native-TraCI-front-bumper",
            "vehicle_fields": ["id", "front_x_m", "front_y_m", "clockwise_from_north_deg", "type",
                               "native_z_m", "length_m", "width_m"],
            "person_fields": ["id", "x_m", "y_m", "clockwise_from_north_deg", "native_z_m"],
            "excluded_static_or_motor_crossing_walk_edges": sorted(excluded_walk_edges),
            "paved_bicycle_cycle_indices": bicycle_cycle_indices,
            "bicycle_route_policy": "paved-building-clear-at-most-three-lanes",
            "bicycle_lane_use": {vehicle_id: sorted(lanes)
                                 for vehicle_id, lanes in sorted(bicycle_lane_use.items())},
            "vehicle_lane_use": {vehicle_id: sorted(lanes)
                                 for vehicle_id, lanes in sorted(vehicle_lane_use.items())},
            "pedestrian_route_policy": "explicit-sidewalk-lanes-only",
            "motor_route_policy": "class-legal-native-cycles-with-preserved-authored-types",
            "person_route_edges": person_route_edges,
            "projection": {"name": "native-SUMO-network-projection", "axes": "x-east,y-north,z-up-meters",
                           "location": dict(network_xml.find("location").attrib)},
            "demand": {"authored": demand, "persons": expected_persons,
                       "observed": {key: len(value) for key, value in sorted(observed.items())}},
            "sumo_colliding_vehicle_ids": sorted(collisions),
            "signals": signals, "frames": frames,
        }
        if "authoring_source" in expected_parameters:
            payload["demand_authoring"] = expected_parameters["authoring_source"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".routes.xml").write_bytes(routes_path.read_bytes())
        output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        print(json.dumps({"output": str(output), "size_bytes": output.stat().st_size,
                          "frames": len(frames), "signal_props": len(signals),
                          "observed": payload["demand"]["observed"]}, indent=2))


def attach_visual_ground_heights(payload: dict, road: dict) -> dict[str, int]:
    """Use the actual current road/walkbed planes; missing coverage is an error."""
    from shapely.geometry import Point, Polygon
    from shapely.strtree import STRtree

    surfaces = {kind: [Polygon(part["outline"], part["holes"]) for part in road[kind]]
                for kind in ("roadbed", "walkbed")}
    if any(not parts or any(not part.is_valid or part.area <= 0 for part in parts)
           for parts in surfaces.values()):
        raise ValueError("Visual ground snap requires actual valid road and walkbed surfaces")
    indices = {kind: STRtree(parts) for kind, parts in surfaces.items()}
    heights = {"roadbed": road["street_layout"]["road_height_m"],
               "walkbed": road["street_layout"]["sidewalk_height_m"]}
    counts = {kind: 0 for kind in surfaces}
    for frame in payload["frames"]:
        for sample in (*frame["vehicles"], *frame["persons"]):
            point = Point(sample[1], sample[2])
            for kind in ("walkbed", "roadbed"):
                if any(surfaces[kind][index].distance(point) <= .01
                       for index in indices[kind].query(point.buffer(.011))):
                    sample[-1] = heights[kind]
                    counts[kind] += 1
                    break
            else:
                raise ValueError(f"Recorded SUMO actor has no displayed ground surface: {sample[0]}")
    return counts





def filter_visual_vehicle_collisions(payload: dict, buildings) -> dict:
    """Omit whole native actor routes that contact current rendered static geometry."""
    from city_motion_continuity import continuity_contacts
    proof = continuity_contacts(payload, buildings)
    if proof["unresolved"]:
        raise ValueError(f"Displayed body interpolation clearance remains unresolved: {proof['unresolved'][:3]}")
    building_conflicts = {record["actor_id"] for record in proof["vehicle_static_contacts"]}
    pair_conflicts = {tuple(sorted(record["actor_ids"])) for record in proof["vehicle_pair_contacts"]}
    frames = payload["frames"]
    excluded = set(building_conflicts)
    observed_types = {sample[0]: sample[4] for frame in frames for sample in frame["vehicles"]}
    remaining_pairs = sorted(pair for pair in pair_conflicts if not (set(pair) & excluded))
    pair_excluded = set()
    while remaining_pairs:
        scores = defaultdict(int)
        for first, second in remaining_pairs:
            scores[first] += 1
            scores[second] += 1
        # Keep bicycle routes when they can be preserved by omitting one motor route.
        victim = max(scores, key=lambda identifier: (
            scores[identifier], observed_types[identifier] != "bicycle", identifier))
        pair_excluded.add(victim)
        remaining_pairs = [pair for pair in remaining_pairs if victim not in pair]
    excluded.update(pair_excluded)
    for frame in frames:
        frame["vehicles"] = [sample for sample in frame["vehicles"] if sample[0] not in excluded]
    minimum = payload["display_requirements"]["minimum_displayed_vehicle_count"]
    if type(minimum) is not int or minimum < 0:
        raise ValueError("Displayed vehicle floor must be an explicit nonnegative configured count")
    if len(observed_types) - len(excluded) < minimum:
        raise ValueError("Too few collision-free recorded SUMO vehicles remain for the visual preview")
    return {"policy": "native-sumo-whole-actor-routes-with-rendered-static-or-body-pair-contacts-omitted",
            "sumo_observed_vehicle_count": len(observed_types),
            "displayed_vehicle_count": len(observed_types) - len(excluded),
            "excluded_static_overlap_ids": sorted(building_conflicts),
            "excluded_pair_overlap_ids": sorted(pair_excluded)}


def building_clear_bicycle_cycles(network: ET.Element, aligned, footprints, building_index,
                                  required_count: int = 6) -> tuple[int, ...]:
    """Select narrow SUMO cycling routes on their actual permitted lanes."""
    from shapely.geometry import LineString
    from city_road_topology import lane_kind

    if type(required_count) is not int or not 0 <= required_count <= 6:
        raise ValueError("Preview requires between zero and six distinct paved bicycle cycles")
    if required_count == 0:
        return ()
    edges = {edge.get("id"): edge for edge in network.findall("edge") if edge.get("function") != "internal"}
    selected = []
    for cycle_index, cycle in enumerate(spread_cycles(
            network, "bicycle", 12, require_count=False)):
        if max(sum(lane_kind(lane) != "walk" for lane in edges[edge_id].findall("lane"))
               for edge_id in cycle) > 3:
            continue
        points = []
        intersects_building = False
        for edge_id in cycle:
            lane = next(lane for lane in edges[edge_id].findall("lane") if allows(lane, "bicycle"))
            segment = [aligned(*map(float, text.split(",")[:2]))
                       for text in lane.get("shape", "").split()]
            points.extend(segment)
            if len(segment) > 1:
                corridor = LineString(segment).buffer(0.35)
                intersects_building |= any(corridor.intersection(footprints[i]).area > 1e-11
                                           for i in building_index.query(corridor))
        # The displayed road is built from these same SUMO lane corridors.
        if not intersects_building and points:
            selected.append(cycle_index)
        if len(selected) == required_count:
            break
    if len(selected) != required_count:
        raise ValueError(
            f"SUMO provides fewer than {required_count} narrow, building-clear bicycle cycles"
        )
    return tuple(selected)




def private_recording_directory(path: Path, public_output: Path) -> Path:
    directory = path.resolve()
    public_root = (ROOT / "frontend/public").resolve()
    if directory == public_output.resolve().parent or directory.is_relative_to(public_root):
        raise ValueError("Native Provider records require a separate private evidence directory outside public assets")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ValueError("Private native evidence directory permits access by other users")
    return directory


def run_container(output: Path, duration: int, network_path: Path, pack_path: Path,
                  road_path: Path, rendered_objects: Path, rendered_objects_manifest: Path,
                  source_osm_path: Path, effective_fixtures_path: Path,
                  private_evidence_dir: Path, workspace_path: Path | None = None) -> None:
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    from city_preview_coordinates import CityEnuProjection, load_canonical_city_geometry, source_context_bytes
    from city_motion_obstacles import load_effective_fixtures, native_road_geometry_gate, validate_recording_road
    from city_road_physical_clearance import LANE_CENTRE_LINE_DECIMALS
    from city_motion_continuity import continuity_contacts

    if workspace_path is None:
        seed, motor_vehicles, pedestrians, bicycles = SEED, 60, 36, 12
        authoring_source = None
    else:
        from aero_bench.authoring.traffic_preview import parse_workspace_preview_demand
        workspace_bytes = workspace_path.resolve().read_bytes()
        workspace_demand = parse_workspace_preview_demand(workspace_bytes)
        seed = workspace_demand.seed
        motor_vehicles = workspace_demand.motor_vehicles
        pedestrians = workspace_demand.pedestrians
        bicycles = workspace_demand.bicycles
        authoring_source = workspace_demand.source_identity()
    validate_preview_demand(seed=seed, motor_vehicles=motor_vehicles,
                            pedestrians=pedestrians, bicycles=bicycles, duration=duration)

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    private_evidence_dir = private_recording_directory(private_evidence_dir, output)
    network_path, pack_path, road_path, rendered_objects, rendered_objects_manifest, source_osm_path, effective_fixtures_path = [
        path.resolve() for path in (network_path, pack_path, road_path, rendered_objects,
                                   rendered_objects_manifest, source_osm_path, effective_fixtures_path)]
    network = ET.parse(network_path).getroot()
    canonical = load_canonical_city_geometry(rendered_objects, rendered_objects_manifest, pack_path)
    context = canonical.source_context(network_path, source_osm_path)
    projection = CityEnuProjection(network, canonical.origin)
    road = json.loads(road_path.read_text())
    surface_sha = validate_recording_road(road, context)
    signals = canonical_signal_inventory(network, projection)
    fixture_polygons, basis = load_effective_fixtures(effective_fixtures_path, context, signals,
                                                     road["street_lamps"], surface_sha)
    buildings = [item.rendered_polygon for item in canonical.footprints]
    native_road_proof = native_road_geometry_gate(network, projection,
        CityEnuProjection(network, canonical.origin, decimals=LANE_CENTRE_LINE_DECIMALS), buildings, road)
    (private_evidence_dir / (output.stem + ".native-road-geometry-gate-v2.json")).write_bytes(source_context_bytes(
        {"source_context":context, "native_road_geometry":native_road_proof}))
    if native_road_proof["status"] != "PASS":
        raise ValueError("Actual native road ribbons/model geometry failed the independent pre-recording gate")
    obstacles = [*buildings, *fixture_polygons]
    basis["obstacle_polygon_count"] = len(obstacles)
    obstacle_index = STRtree(obstacles)
    sidewalk_obstacles = [*obstacles, *motor_lane_surfaces(network, projection.point)]
    sidewalk_index = STRtree(sidewalk_obstacles)
    bicycle_cycle_indices = building_clear_bicycle_cycles(
        network, projection.point, obstacles, obstacle_index, min(6, bicycles),
    )
    excluded_walk_edges = set()
    for edge in network.findall("edge"):
        edge_id = edge.get("id")
        if edge.get("function") == "internal" or edge_id is None:
            continue
        for lane in edge.findall("lane"):
            if not allows(lane, "pedestrian"):
                continue
            points = projection.shape(lane.get("shape"))
            if len(points) < 2:
                raise ValueError(f"Pedestrian source lane has no usable ground shape: {lane.get('id')}")
            corridor = LineString(points).buffer(.34)
            if any(corridor.intersection(sidewalk_obstacles[index]).area > 1e-11
                   for index in sidewalk_index.query(corridor)):
                excluded_walk_edges.add(edge_id)
                break
    input_path = private_evidence_dir / (output.stem + ".recording-inputs-v2.json")
    output.with_name(output.stem + ".source-context-v1.json").write_bytes(source_context_bytes(context))
    raw_path = private_evidence_dir / (output.stem + ".native-sumo-v1.json")
    command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
               "--tmpfs", "/tmp:rw,nosuid,size=256m",
               "--mount", f"type=bind,source={ROOT},target=/work,readonly",
               "--mount", f"type=bind,source={private_evidence_dir},target=/private-evidence",
               "--entrypoint", "python3", SUMO_IMAGE,
               "/work/frontend/scripts/build-sumo-city-preview.py", "--in-container",
               "--network", f"/work/{network_path.relative_to(ROOT)}",
               "--pack", f"/work/{pack_path.relative_to(ROOT)}",
               "--output", f"/private-evidence/{raw_path.name}", "--duration", str(duration),
               "--recording-inputs", f"/private-evidence/{input_path.name}"]
    for attempt in range(12):
        parameters = configured_recording_parameters(
            duration=duration, excluded_walk_edges=excluded_walk_edges,
            bicycle_cycle_indices=bicycle_cycle_indices, seed=seed,
            motor_vehicles=motor_vehicles, pedestrians=pedestrians, bicycles=bicycles,
            authoring_source=authoring_source,
        )
        capabilities = native_route_capabilities(network, excluded_walk_edges,
            parameters["authored_vehicle_counts"], parameters["route_requirements"])
        (private_evidence_dir / (output.stem + ".native-route-capabilities-v1.json")).write_bytes(source_context_bytes(capabilities))
        if capabilities["status"] != "PASS":
            raise ValueError(f"Native route capability gate is BLOCKED: {capabilities['failures']}")
        preflight_routes = private_evidence_dir / (output.stem + ".authored-preflight.routes.xml")
        write_routes(
            preflight_routes, network, excluded_walk_edges, bicycle_cycle_indices, parameters,
        )
        input_path.write_bytes(source_context_bytes({"schema_version":"aero-bench.city-sumo-recording-inputs/v2",
            "source_context":context, "visual_obstacle_basis":basis,
            "native_route_capabilities":capabilities,
            "expected_route_sha256":hashlib.sha256(preflight_routes.read_bytes()).hexdigest(),
            "recording_parameters":parameters}))
        subprocess.run([*command, f"--excluded-walk-edges={','.join(sorted(excluded_walk_edges))}",
                        f"--bicycle-cycle-indices={','.join(map(str, bicycle_cycle_indices))}"], check=True)
        native_bytes = raw_path.read_bytes()
        attempt_prefix = raw_path.with_name(raw_path.stem + f".attempt-{attempt + 1:02d}")
        attempt_prefix.with_suffix(attempt_prefix.suffix + ".json").write_bytes(native_bytes)
        attempt_prefix.with_suffix(attempt_prefix.suffix + ".routes.xml").write_bytes(raw_path.with_suffix(".routes.xml").read_bytes())
        attempt_prefix.with_suffix(attempt_prefix.suffix + ".recording-inputs-v2.json").write_bytes(input_path.read_bytes())
        payload = json.loads(native_bytes)
        align_native_payload(payload, projection)
        if payload["signals"] != signals or payload["source_context"] != context \
                or payload["visual_obstacle_basis"] != basis:
            raise ValueError("Real native SUMO recording differs from the materialized source/fixture inputs")
        pedestrian_proof = continuity_contacts(payload, sidewalk_obstacles, include_vehicles=False)
        crossing_people = {record["actor_id"] for record in pedestrian_proof["person_static_contacts"]}
        if not crossing_people:
            break
        new_exclusions = {payload["person_route_edges"][person_id] for person_id in crossing_people} - excluded_walk_edges
        if not new_exclusions:
            raise ValueError(f"Native sidewalk routes still contact current models or motor lanes: {sorted(crossing_people)}")
        excluded_walk_edges.update(new_exclusions)
        print(f"Pedestrian placement audit retry {attempt + 1}: excluded {len(new_exclusions)} crossing routes", flush=True)
    else:
        raise ValueError("Real SUMO pedestrian route filter did not converge after 12 runs")
    observed_types = {row[4] for frame in payload["frames"] for row in frame["vehicles"]}
    active_types = active_ground_fleet_types(parameters["authored_vehicle_counts"])
    if active_types - observed_types:
        raise ValueError(f"SUMO did not actually observe every declared fleet type: {sorted(active_types-observed_types)}")
    if payload["sumo_colliding_vehicle_ids"]:
        raise ValueError(f"SUMO reported vehicle collisions: {payload['sumo_colliding_vehicle_ids']}")
    payload["projection"] = projection.metadata()
    payload["native_motion_source"] = {"schema_version":"aero-bench.city-sumo-native-recording/v1",
        "sha256":hashlib.sha256(native_bytes).hexdigest(), "size_bytes":len(native_bytes),
        "provider":"SUMO-TraCI", "sumo_image_id":payload["sumo_image_id"],
        "sumo_version":payload["sumo_version"], "source_network_sha256":context["source_network_sha256"],
        "source_osm_sha256":context["source_osm_sha256"], "route_sha256":payload["route_sha256"],
        "recording_inputs_sha256":hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "seed":payload["seed"], "duration_seconds":duration, "step_seconds":STEP_SECONDS}
    payload["signal_pole_placement"] = {"policy":"native-curb-derived-source-placement-with-explicit-effective-omissions",
        "source_signal_count":len(signals), "extra_relocated_count":0}
    payload["visual_collision_audit"] = {"pedestrian_static_or_motor_overlap_samples":0,
        "pedestrian_proxy_radius_m":.3, "route_filter_runs":attempt + 1}
    payload["visual_vehicle_filter"] = filter_visual_vehicle_collisions(payload, obstacles)
    displayed_types = {row[4] for frame in payload["frames"] for row in frame["vehicles"]}
    if active_types - displayed_types:
        raise ValueError(f"Rendered traffic lost a declared observed fleet type: {sorted(active_types-displayed_types)}")
    motion_proof = continuity_contacts(payload, obstacles)
    (private_evidence_dir / (output.stem + ".continuous-motion-v2.json")).write_bytes(source_context_bytes(motion_proof))
    if motion_proof["unresolved"] or any(motion_proof[key] for key in
            ("vehicle_static_contacts", "vehicle_pair_contacts", "person_static_contacts")):
        raise ValueError("Current displayed motion failed its continuous rendered geometry gate")
    payload["ground_snap"] = {"source":"actual-v3-road-and-walkbed-render-planes",
                              "samples":attach_visual_ground_heights(payload, road)}
    # Only the declared public trace and source identities reach the viewer.
    for key in ("vehicle_lane_use", "bicycle_lane_use", "person_route_edges",
                "excluded_static_or_motor_crossing_walk_edges", "paved_bicycle_cycle_indices",
                "sumo_colliding_vehicle_ids", "recording_inputs_sha256"):
        del payload[key]
    output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"aligned_output":str(output), "size_bytes":output.stat().st_size,
                      "native_recording":str(raw_path), "native_sha256":payload["native_motion_source"]["sha256"],
                      "frames":len(payload["frames"]), "source_signals":len(signals),
                      "observed":payload["demand"]["observed"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--road", type=Path)
    parser.add_argument("--rendered-objects", type=Path)
    parser.add_argument("--render-objects-manifest", type=Path)
    parser.add_argument("--source-osm", type=Path)
    parser.add_argument("--effective-fixtures", type=Path)
    parser.add_argument("--private-evidence-dir", type=Path)
    parser.add_argument(
        "--workspace", type=Path,
        help="Validated city workspace whose seed and traffic counts replace the accepted A3 demo demand",
    )
    parser.add_argument("--in-container", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recording-inputs", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--excluded-walk-edges", default="", help=argparse.SUPPRESS)
    parser.add_argument("--bicycle-cycle-indices", default="", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.in_container:
        if arguments.workspace is not None:
            parser.error("The container consumes materialized demand inputs, not a mutable workspace")
        if arguments.recording_inputs is None:
            parser.error("Native SUMO recording requires --recording-inputs")
        build(arguments.output, arguments.duration,
              set(filter(None, arguments.excluded_walk_edges.split(","))),
              tuple(map(int, filter(None, arguments.bicycle_cycle_indices.split(",")))),
              arguments.network, arguments.pack, arguments.recording_inputs)
    else:
        if any(value is None for value in [arguments.road, arguments.rendered_objects,
                arguments.render_objects_manifest, arguments.source_osm, arguments.effective_fixtures,
                arguments.private_evidence_dir]):
            parser.error("v2 recording requires --road, --rendered-objects, --render-objects-manifest, --source-osm --effective-fixtures and --private-evidence-dir")
        run_container(arguments.output, arguments.duration, arguments.network, arguments.pack,
                      arguments.road, arguments.rendered_objects, arguments.render_objects_manifest,
                      arguments.source_osm, arguments.effective_fixtures,
                      arguments.private_evidence_dir, arguments.workspace)
