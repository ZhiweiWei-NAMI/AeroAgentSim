"""Explicit input selection and authored, self-contained grid demand."""

import random
import xml.etree.ElementTree as ET
from pathlib import Path

from .wire import fields, integer, number, string

GRID = Path("/opt/aeroagentsim/grid.net.xml")


def seconds(ns):
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


def existing(value, name):
    path = Path(string(value, name)).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{name} must be a file")
    return path


def demand(directory, scenario, seed):
    fields(scenario, ("kind",), ("vehicles", "persons", "depart_interval_s"))
    if scenario["kind"] != "grid":
        raise ValueError("inline scenario kind must be grid")
    count = integer(scenario.get("vehicles", 200), "vehicles", maximum=10000)
    persons = integer(scenario.get("persons", 3), "persons", maximum=10000)
    interval = number(scenario.get("depart_interval_s", 0.1), "depart_interval_s")
    if interval < 0:
        raise ValueError("depart_interval_s must be nonnegative")
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        id="car",
        vClass="passenger",
        accel="2.6",
        decel="4.5",
        sigma="0.5",
        length="5",
        maxSpeed="13.89",
    )
    ET.SubElement(root, "vType", id="pedestrian", vClass="pedestrian")
    nodes = ["A0", "B0", "C0", "C1", "C2", "B2", "A2", "A1"]
    routes = []
    for direction in (nodes, list(reversed(nodes))):
        for offset in range(8):
            cycle = direction[offset:] + direction[:offset]
            edges = [cycle[i] + cycle[(i + 1) % 8] for i in range(8)] * 2
            route_id = f"r{len(routes)}"
            routes.append(route_id)
            ET.SubElement(root, "route", id=route_id, edges=" ".join(edges))
    rng = random.Random(seed)
    # Sort all departures globally, including persons: SUMO requires time order.
    departures = []
    for i in range(count):
        route = "r0" if i == 0 else rng.choice(routes)
        departures.append((i * interval, "vehicle", i, route))
    for i in range(persons):
        departures.append((i * interval, "person", i, None))
    for at, kind, i, route in sorted(departures):
        if kind == "vehicle":
            ET.SubElement(
                root,
                "vehicle",
                id=f"v{i}",
                type="car",
                route=route,
                depart=str(at),
                departLane="best",
                departSpeed="0",
            )
        else:
            person = ET.SubElement(
                root, "person", id=f"p{i}", type="pedestrian", depart=str(at)
            )
            ET.SubElement(person, "walk", edges="A0B0 B0C0", arrivalPos="190")
    path = Path(directory) / "grid.rou.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def prepare(payload, directory):
    fields(
        payload,
        ("seed", "step_length_ns"),
        ("scenario", "network", "routes", "config_file", "options"),
    )
    seed = integer(payload["seed"], "seed", maximum=2**31 - 1)
    step = integer(
        payload["step_length_ns"],
        "step_length_ns",
        minimum=1_000_000,
        maximum=1_000_000_000,
    )
    if step % 1_000_000:
        raise ValueError("SUMO clock uses integral milliseconds")
    modes = sum(key in payload for key in ("scenario", "network", "config_file"))
    if modes != 1:
        raise ValueError("select exactly one of scenario, network or config_file")
    network = None
    if "scenario" in payload:
        if "routes" in payload:
            raise ValueError("routes conflicts with inline scenario")
        network = existing(str(GRID), "grid")
        route = demand(directory, payload["scenario"], seed)
        args = ["--net-file", str(network), "--route-files", str(route)]
    elif "network" in payload:
        network = existing(payload["network"], "network")
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes:
            raise ValueError("network requires nonempty routes list")
        args = [
            "--net-file",
            str(network),
            "--route-files",
            ",".join(str(existing(r, "route file")) for r in routes),
        ]
    else:
        if "routes" in payload:
            raise ValueError("routes conflicts with config_file")
        config = existing(payload["config_file"], "config_file")
        tree = ET.parse(config)
        entry = tree.find("./input/net-file")
        if entry is None:
            raise ValueError("configuration must declare a network")
        network = existing(str(config.parent / entry.attrib["value"]), "network")
        args = ["--configuration-file", str(config)]
    options = payload.get("options", {})
    fields(options, (), ("time_to_teleport_s", "collision_action"))
    if "time_to_teleport_s" in options:
        value = number(options["time_to_teleport_s"], "time_to_teleport_s")
        if value != -1 and value < 0:
            raise ValueError("time_to_teleport_s must be -1 or nonnegative")
        args += ["--time-to-teleport", str(value)]
    if "collision_action" in options:
        value = string(options["collision_action"], "collision_action")
        if value not in ("none", "warn", "teleport", "remove"):
            raise ValueError("invalid collision_action")
        args += ["--collision.action", value]
    location = ET.parse(network).find("location")
    if location is None:
        raise ValueError("network has no location metadata")
    projection = location.attrib["projParameter"]
    geo = projection not in ("!", "-", ".")
    args += [
        "--ignore-route-errors",
        "false",
        "--seed",
        str(seed),
        "--step-length",
        seconds(step),
        "--begin",
        "0",
        "--end",
        "-1",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
    ]
    return seed, step, args, geo
