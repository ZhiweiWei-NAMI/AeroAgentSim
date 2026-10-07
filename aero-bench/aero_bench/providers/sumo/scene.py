"""Deterministic SUMO scene and route materialisation.

The benchmark keeps the SUMO network as an explicit input asset.  This module
is the single authoring path for that asset and for the route inventory used by
the trajectory exporter.  It deliberately fails when the declared
``netgenerate`` executable is unavailable; callers must never silently replace
the microscopic SUMO network with a synthetic or analytical trajectory.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


GRID_NUMBER = 5
GRID_LENGTH_M = 225.0
GRID_ATTACH_LENGTH_M = 50.0
SCENE_MIN_M = -500.0
SCENE_MAX_M = 500.0
LANE_COUNT = 2
LANE_WIDTH_M = 3.5
ROAD_SPEED_MPS = 13.9
TLS_CYCLE_S = 60
TLS_YELLOW_S = 3
TLS_RED_S = 2
VEHICLE_COUNT = 20
PERSON_COUNT = 5

ROUTE_IDS = (
    "ring.clockwise",
    "ring.counterclockwise",
    "arterial.east-west",
    "arterial.west-east",
    "arterial.south-north",
    "arterial.north-south",
)


class SumoSceneError(RuntimeError):
    """Raised when the declared SUMO scene cannot be materialised safely."""


@dataclass(frozen=True, slots=True)
class TrafficLight:
    signal_id: str
    junction_id: str
    east_m: float
    north_m: float
    cycle_s: int = TLS_CYCLE_S
    phase_offset_s: int = 0

    @property
    def phases(self) -> tuple[dict[str, object], ...]:
        # The exact signal string is supplied by SUMO in trajectory frames.
        # These phases describe the declared fixed-time controller and are
        # useful to a viewer before the first TraCI frame arrives.
        return (
            {"state": "GrGr", "duration_s": 27},
            {"state": "yryr", "duration_s": TLS_YELLOW_S},
            {"state": "rGrG", "duration_s": 27},
            {"state": "ryry", "duration_s": TLS_YELLOW_S},
        )


@dataclass(frozen=True, slots=True)
class SumoNetwork:
    xml: bytes
    edge_ids: tuple[str, ...]
    traffic_lights: tuple[TrafficLight, ...]


def _run_netgenerate(binary: str) -> bytes:
    executable = shutil.which(binary)
    if executable is None:
        raise SumoSceneError(f"declared SUMO network generator is unavailable: {binary}")
    with tempfile.TemporaryDirectory(prefix="aero-bench-sumo-net-") as directory:
        output = Path(directory) / "network.net.xml"
        command = (
            executable,
            "--grid",
            "--grid.number",
            str(GRID_NUMBER),
            "--grid.length",
            f"{GRID_LENGTH_M:g}",
            "--grid.attach-length",
            f"{GRID_ATTACH_LENGTH_M:g}",
            "--offset.x",
            f"{SCENE_MIN_M:g}",
            "--offset.y",
            f"{SCENE_MIN_M:g}",
            "--alphanumerical-ids",
            "--default.lanenumber",
            str(LANE_COUNT),
            "--default.lanewidth",
            f"{LANE_WIDTH_M:g}",
            "--default.speed",
            f"{ROAD_SPEED_MPS:g}",
            "--default.junctions.keep-clear",
            "--default.junctions.radius",
            "5",
            "--default.sidewalk-width",
            "2.5",
            "--default.crossing-width",
            "3",
            "--default-junction-type",
            "traffic_light",
            "--tls.guess",
            "--tls.cycle.time",
            str(TLS_CYCLE_S),
            "--tls.yellow.time",
            str(TLS_YELLOW_S),
            "--tls.red.time",
            str(TLS_RED_S),
            "--output-file",
            str(output),
        )
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SumoSceneError("SUMO network generation did not complete") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise SumoSceneError(
                f"SUMO network generation failed{': ' + detail if detail else ''}"
            )
        try:
            root = ET.parse(output).getroot()
        except (OSError, ET.ParseError) as exc:
            raise SumoSceneError("SUMO network generator returned invalid XML") from exc
    if root.tag != "net":
        raise SumoSceneError("SUMO network root must be <net>")
    # ElementTree omits comments when parsing with its default parser.  The
    # resulting bytes therefore contain no timestamp or host-specific path.
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return payload + b"\n"


def _float_attribute(element: ET.Element, name: str) -> float:
    value = element.attrib.get(name)
    if value is None:
        raise SumoSceneError(f"SUMO network element is missing {name}")
    try:
        result = float(value)
    except ValueError as exc:
        raise SumoSceneError(f"SUMO network {name} is not numeric") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise SumoSceneError(f"SUMO network {name} is not finite")
    return result


def _parse_network(payload: bytes) -> SumoNetwork:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise SumoSceneError("SUMO network XML cannot be parsed") from exc
    location = root.find("location")
    if location is None:
        raise SumoSceneError("SUMO network must declare a location")
    boundary = location.attrib.get("convBoundary", "").split(",")
    if len(boundary) != 4:
        raise SumoSceneError("SUMO network convBoundary is malformed")
    try:
        bounds = tuple(float(item) for item in boundary)
    except ValueError as exc:
        raise SumoSceneError("SUMO network convBoundary is not numeric") from exc
    if bounds != (SCENE_MIN_M, SCENE_MIN_M, SCENE_MAX_M, SCENE_MAX_M):
        raise SumoSceneError(
            "SUMO network must cover exactly the declared 1 km ENU square"
        )

    edge_ids: list[str] = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if edge_id.startswith(":"):
            continue
        if not edge_id or edge.attrib.get("from") is None or edge.attrib.get("to") is None:
            raise SumoSceneError("SUMO network contains an incomplete road edge")
        edge_ids.append(edge_id)
    if len(edge_ids) < 40 or len(edge_ids) != len(set(edge_ids)):
        raise SumoSceneError("SUMO network road-edge inventory is incomplete")

    signals: list[TrafficLight] = []
    for junction in root.findall("junction"):
        if junction.attrib.get("type") != "traffic_light":
            continue
        junction_id = junction.attrib.get("id")
        if not junction_id:
            raise SumoSceneError("SUMO traffic-light junction has no id")
        signals.append(
            TrafficLight(
                signal_id=f"signal.{junction_id}",
                junction_id=junction_id,
                east_m=_float_attribute(junction, "x"),
                north_m=_float_attribute(junction, "y"),
            )
        )
    if len(signals) < 9:
        raise SumoSceneError("SUMO network must contain at least nine traffic lights")
    signals.sort(key=lambda item: item.junction_id)
    return SumoNetwork(
        xml=payload,
        edge_ids=tuple(sorted(edge_ids)),
        traffic_lights=tuple(signals),
    )


@functools.lru_cache(maxsize=2)
def materialize_network(binary: str = "netgenerate") -> SumoNetwork:
    """Generate and validate the deterministic 1 km network once per process."""

    return _parse_network(_run_netgenerate(binary))


def _edge_id(source: str, target: str) -> str:
    return f"{source}{target}"


def _path(*nodes: str) -> tuple[str, ...]:
    if len(nodes) < 2 or nodes[0] != nodes[-1]:
        raise SumoSceneError("SUMO route authoring paths must be closed")
    return tuple(_edge_id(source, target) for source, target in zip(nodes, nodes[1:]))


def _ring(clockwise: bool = True) -> tuple[str, ...]:
    nodes = (
        "A0",
        "A1",
        "A2",
        "A3",
        "A4",
        "B4",
        "C4",
        "D4",
        "E4",
        "E3",
        "E2",
        "E1",
        "E0",
        "D0",
        "C0",
        "B0",
    )
    if not clockwise:
        nodes = tuple(reversed(nodes))
    return _path(*nodes, nodes[0])


def _routes() -> dict[str, tuple[str, ...]]:
    ring = _ring(True)
    reverse_ring = _ring(False)
    # The two arterial routes traverse the complete central row/column and
    # return along the adjacent lane.  Repeating each route keeps every actor
    # active for the declared 600 s simulation while exposing intersections.
    east_west_nodes = ("B2", "C2", "D2", "E2", "E3", "D3", "C3", "B3", "B2")
    west_east_nodes = tuple(reversed(east_west_nodes))
    south_north_nodes = (
        "C0",
        "C1",
        "C2",
        "C3",
        "C4",
        "D4",
        "D3",
        "D2",
        "D1",
        "D0",
        "C0",
    )
    north_south_nodes = tuple(reversed(south_north_nodes))
    east_west = _path(*east_west_nodes)
    west_east = _path(*west_east_nodes)
    south_north = _path(*south_north_nodes)
    north_south = _path(*north_south_nodes)
    return {
        "ring.clockwise": ring + ring,
        "ring.counterclockwise": reverse_ring + reverse_ring,
        "arterial.east-west": east_west * 8,
        "arterial.west-east": west_east * 8,
        "arterial.south-north": south_north * 6,
        "arterial.north-south": north_south * 6,
    }


def _validate_route_edges(
    route: Iterable[str],
    *,
    edge_ids: frozenset[str],
    edge_endpoints: dict[str, tuple[str, str]],
    label: str,
) -> tuple[str, ...]:
    result = tuple(route)
    if not result:
        raise SumoSceneError(f"SUMO route {label} is empty")
    missing = set(result) - edge_ids
    if missing:
        raise SumoSceneError(f"SUMO route {label} names unknown edges: {sorted(missing)}")
    for previous, current in zip(result, result[1:]):
        if edge_endpoints[previous][1] != edge_endpoints[current][0]:
            raise SumoSceneError(
                f"SUMO route {label} is disconnected between {previous} and {current}"
            )
    return result


def materialize_routes(network: SumoNetwork | None = None) -> str:
    """Return canonical routes for exactly 20 vehicles and 5 pedestrians."""

    network = network or materialize_network()
    routes = _routes()
    edge_ids = frozenset(network.edge_ids)
    try:
        network_root = ET.fromstring(network.xml)
    except ET.ParseError as exc:
        raise SumoSceneError("SUMO network XML cannot be parsed for route validation") from exc
    edge_endpoints = {
        edge.attrib["id"]: (edge.attrib["from"], edge.attrib["to"])
        for edge in network_root.findall("edge")
        if not edge.attrib.get("id", "").startswith(":")
    }
    checked = {
        route_id: _validate_route_edges(
            path,
            edge_ids=edge_ids,
            edge_endpoints=edge_endpoints,
            label=route_id,
        )
        for route_id, path in routes.items()
    }
    root = ET.Element("routes")
    vehicle_types = (
        {
            "id": "city.passenger",
            "accel": "2.6",
            "decel": "4.5",
            "sigma": "0.35",
            "length": "4.6",
            "width": "1.8",
            "maxSpeed": "13.9",
            "color": "0.18,0.48,0.86",
        },
        {
            "id": "city.delivery",
            "accel": "2.2",
            "decel": "4.5",
            "sigma": "0.25",
            "length": "5.5",
            "width": "2.0",
            "maxSpeed": "11.1",
            "color": "0.95,0.55,0.16",
        },
        {
            "id": "city.transit",
            "accel": "1.4",
            "decel": "4.0",
            "sigma": "0.20",
            "length": "12.0",
            "width": "2.5",
            "maxSpeed": "11.1",
            "color": "0.20,0.72,0.42",
        },
    )
    for attributes in vehicle_types:
        ET.SubElement(root, "vType", attributes)
    for route_id in ROUTE_IDS:
        ET.SubElement(root, "route", id=route_id, edges=" ".join(checked[route_id]))

    vehicle_route_ids = (
        "ring.clockwise",
        "ring.counterclockwise",
        "arterial.east-west",
        "arterial.west-east",
        "arterial.south-north",
        "arterial.north-south",
    )
    vehicle_type_ids = ("city.passenger", "city.delivery", "city.transit")
    actors: list[tuple[int, int, ET.Element]] = []
    for index in range(1, VEHICLE_COUNT + 1):
        route_id = vehicle_route_ids[(index - 1) % len(vehicle_route_ids)]
        type_id = vehicle_type_ids[(index - 1) % len(vehicle_type_ids)]
        vehicle = ET.Element(
            "vehicle",
            id=f"veh.{index:02d}",
            type=type_id,
            route=route_id,
            depart=f"{(index - 1) * 3:d}",
            departLane="best",
            departPos="0",
            departSpeed="max",
        )
        # Explicit business metadata is carried by the route input and is
        # available to downstream exporters without changing SUMO semantics.
        ET.SubElement(vehicle, "param", key="service", value="urban-delivery")
        actors.append(((index - 1) * 3, 0, vehicle))

    person_route = checked["ring.clockwise"]
    walking_speeds = ("1.20", "1.28", "1.35", "1.42", "1.30")
    for index in range(1, PERSON_COUNT + 1):
        person = ET.Element(
            "person",
            id=f"ped.{index:02d}",
            depart=f"{(index - 1) * 4:d}",
        )
        ET.SubElement(
            person,
            "walk",
            edges=" ".join(person_route),
            speed=walking_speeds[index - 1],
        )
        actors.append(((index - 1) * 4, 1, person))
    for _depart, _kind, actor in sorted(actors, key=lambda item: (item[0], item[1])):
        root.append(actor)
    return ET.tostring(root, encoding="unicode") + "\n"


def materialize_additional(network: SumoNetwork | None = None) -> str:
    """Return SUMO POIs that make every declared signal visible in viewers."""

    network = network or materialize_network()
    root = ET.Element("additional")
    for signal in network.traffic_lights:
        ET.SubElement(
            root,
            "poi",
            id=signal.signal_id,
            type="traffic_signal",
            color="1,0.78,0.08",
            layer="100",
            x=f"{signal.east_m:.2f}",
            y=f"{signal.north_m:.2f}",
        )
    return ET.tostring(root, encoding="unicode") + "\n"


def traffic_signals_geojson(network: SumoNetwork | None = None) -> dict[str, object]:
    """Build the public signal layer from the same network used by SUMO."""

    network = network or materialize_network()
    features = []
    for signal in network.traffic_lights:
        features.append(
            {
                "type": "Feature",
                "id": signal.signal_id,
                "properties": {
                    "signal_id": signal.signal_id,
                    "junction_id": signal.junction_id,
                    "cycle_s": signal.cycle_s,
                    "phase_offset_s": signal.phase_offset_s,
                    "phases": list(signal.phases),
                    "telemetry_source": "sumo-traci",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [signal.east_m, signal.north_m, 0.0],
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "schema_version": "aero-bench.sumo-traffic-signals/v1",
        "coordinate_frame": "ENU",
        "features": features,
    }


def scene_counts() -> dict[str, int]:
    """Expose the closed actor inventory for builders and validation tools."""

    return {"vehicles": VEHICLE_COUNT, "pedestrians": PERSON_COUNT}
