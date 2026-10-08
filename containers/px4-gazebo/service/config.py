"""Explicit reset configuration and SDF rewriting, without benchmark bindings."""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path("/opt/px4-gazebo")
MODELS = ROOT / "share/gz/models"
WORLDS = ROOT / "share/gz/worlds"
DATA = ROOT / "etc"
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


def integer(value, label, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric data")
    return float(value)


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("identifier must match [A-Za-z][A-Za-z0-9_-]{0,63}")
    return value


def fields(obj, required, optional=()):
    if (
        not isinstance(obj, dict)
        or set(required) - obj.keys()
        or obj.keys() - set(required) - set(optional)
    ):
        raise ValueError(f"expected fields {required}; optional {optional}")


def catalog():
    result = {}
    for path in sorted((DATA / "init.d-posix/airframes").glob("*_gz_*")):
        prefix, model = path.name.split("_gz_", 1)
        if prefix.isdigit() and (MODELS / model / "model.sdf").is_file():
            if model in result:
                raise ValueError(f"ambiguous airframe model {model}")
            result[model] = int(prefix)
    return result


@dataclass(frozen=True)
class Vehicle:
    id: str
    model: str
    spawn: tuple
    index: int
    autostart: int

    @property
    def model_name(self):
        return f"aas_{self.id}"

    @property
    def grpc_port(self):
        return 50051 + self.index

    @property
    def udp_port(self):
        return 14540 + self.index


@dataclass
class Config:
    seed: int
    world: str
    sdf: Path
    vehicles: list
    warmup: int
    physics_step_ns: int
    origin_wgs84: tuple


def prepare(payload, directory):
    fields(payload, ("seed", "world", "vehicles", "warmup"))
    seed = integer(payload["seed"], "seed", maximum=2**32 - 1)
    warmup = integer(payload["warmup"], "warmup", minimum=1, maximum=120_000_000_000)
    world_ref = payload["world"]
    if not isinstance(world_ref, str):
        raise ValueError("world must be image world name or absolute mounted SDF path")
    source = (
        Path(world_ref)
        if world_ref.startswith("/")
        else WORLDS / (identifier(world_ref) + ".sdf")
    )
    root = ET.parse(source).getroot()
    worlds = [root] if root.tag == "world" else root.findall("world")
    if len(worlds) != 1:
        raise ValueError("SDF must contain exactly one world")
    world = worlds[0]
    name = identifier(world.attrib["name"])
    physics = world.find("physics")
    if physics is None or physics.find("max_step_size") is None:
        raise ValueError("world requires explicit physics/max_step_size")
    step = Decimal(physics.findtext("max_step_size")) * 1_000_000_000
    if not step.is_finite() or step != step.to_integral_value() or step <= 0:
        raise ValueError("physics step must be positive integer nanoseconds")
    step = int(step)
    if warmup % step:
        raise ValueError("warmup must align to world physics step")
    # Paused multi_step controls integration; no wall-clock real-time throttle.
    for key in ("real_time_factor", "real_time_update_rate"):
        element = physics.find(key)
        if element is None:
            element = ET.SubElement(physics, key)
        element.text = "0"
    paused = world.find("paused")
    if paused is None:
        paused = ET.SubElement(world, "paused")
    paused.text = "true"
    spherical = world.find("spherical_coordinates")
    if spherical is None or spherical.findtext("world_frame_orientation") != "ENU":
        raise ValueError(
            "world requires spherical_coordinates with ENU orientation for goto_location"
        )
    origin = tuple(
        number(float(spherical.findtext(key)), key)
        for key in ("latitude_deg", "longitude_deg", "elevation")
    )
    supported = catalog()
    raw_vehicles = payload["vehicles"]
    if not isinstance(raw_vehicles, list) or not 1 <= len(raw_vehicles) <= 32:
        raise ValueError("vehicles must contain 1..32 declarations")
    vehicles, ids = [], set()
    existing = {m.attrib["name"] for m in world.findall("model")}
    existing.update(e.findtext("name") for e in world.findall("include"))
    for index, item in enumerate(raw_vehicles):
        fields(item, ("id", "model", "spawn"))
        vid = identifier(item["id"])
        model = identifier(item["model"])
        if vid in ids or f"aas_{vid}" in existing:
            raise ValueError(f"duplicate vehicle/model id {vid}")
        ids.add(vid)
        if model not in supported:
            raise ValueError(f"model {model} has no installed SDF and PX4 airframe")
        pose = item["spawn"]
        if not isinstance(pose, list) or len(pose) != 6:
            raise ValueError(
                "spawn is ENU [east,north,up,roll,pitch,yaw] in metres/radians"
            )
        pose = tuple(number(v, "spawn") for v in pose)
        vehicle = Vehicle(vid, model, pose, index, supported[model])
        vehicles.append(vehicle)
        include = ET.SubElement(world, "include")
        ET.SubElement(include, "uri").text = f"model://{model}"
        ET.SubElement(include, "name").text = vehicle.model_name
        ET.SubElement(include, "pose").text = " ".join(format(v, ".17g") for v in pose)
    # Preserve the installed system list, changing only pose publication.
    # SceneBroadcaster's dynamic topic has native model poses and timestamps;
    # 1000 Hz bounds wall-time publication delay (pose/info is fixed at 60 Hz).
    server = ET.parse(ROOT / "share/gz/server.config")
    for element in (server.getroot(), world):
        for plugin in element.iter("plugin"):
            if plugin.get("name") == "gz::sim::systems::SceneBroadcaster":
                hertz = plugin.find("dynamic_pose_hertz")
                if hertz is None:
                    hertz = ET.SubElement(plugin, "dynamic_pose_hertz")
                hertz.text = "1000"
    server.write(directory / "server.config", encoding="utf-8", xml_declaration=True)
    destination = directory / "world.sdf"
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    return Config(seed, name, destination, vehicles, warmup, step, origin)
