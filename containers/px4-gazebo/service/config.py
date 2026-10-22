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
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be finite numeric data")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{label} must be finite numeric data") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite numeric data")
    return result


def bounded(value, label, minimum, maximum, *, positive=False):
    result = number(value, label)
    if not minimum <= result <= maximum or (positive and result <= minimum):
        raise ValueError(f"{label} outside configured bounds")
    return result


def vector(value, label, length, minimum, maximum):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{label} requires {length} numeric values")
    return [bounded(v, label, minimum, maximum) for v in value]


def topic(value):
    # No coercion, empty segments, traversal, whitespace or transport metacharacters.
    if (
        not isinstance(value, str)
        or len(value) > 240
        or not re.fullmatch(r"(?:/[A-Za-z0-9_][A-Za-z0-9_-]*)+", value)
    ):
        raise ValueError("topic must be an absolute Gazebo topic with nonempty segments")
    return value


def camera_config(value):
    fields(value, (
        "sensor_id", "topic_prefix", "pose_flu", "width", "height",
        "update_rate_hz", "horizontal_fov_rad", "near_m", "far_m",
    ))
    result = dict(value)
    result["sensor_id"] = identifier(value["sensor_id"])
    result["topic_prefix"] = topic(value["topic_prefix"])
    if len(result["topic_prefix"]) > 96:
        raise ValueError("camera topic prefix exceeds 96 characters")
    result["pose_flu"] = vector(value["pose_flu"], "pose_flu", 6, -100, 100)
    for angle in result["pose_flu"][3:]:
        bounded(angle, "camera rotation", -2 * math.pi, 2 * math.pi)
    for key in ("width", "height"):
        result[key] = integer(value[key], key, 1, 2048)
    if result["width"] * result["height"] > 1_048_576:
        raise ValueError("camera dimensions exceed the pixel budget")
    result["update_rate_hz"] = bounded(value["update_rate_hz"], "update_rate_hz", 0, 240, positive=True)
    result["horizontal_fov_rad"] = bounded(value["horizontal_fov_rad"], "horizontal_fov_rad", 0.01, math.pi - 0.01)
    result["near_m"] = bounded(value["near_m"], "near_m", 0, 10_000, positive=True)
    result["far_m"] = bounded(value["far_m"], "far_m", 0, 10_000, positive=True)
    if result["near_m"] >= result["far_m"]:
        raise ValueError("camera requires near_m < far_m")
    return result


def payload_config(value, vehicle_ids, physics_step_ns):
    physics_step_ns = integer(physics_step_ns, "physics_step_ns", minimum=1)
    fields(value, (
        "carrier", "package_id", "receiver_id", "detach_topic", "state_topic",
        "contact_topic", "attachment_offset_enu_m", "package_size_m",
        "package_mass_kg", "receiver_pose", "receiver_size", "contact_duration_ns",
    ), ("parent_link", "child_link", "receiver_link", "receiver_collision", "package_orientation_rpy"))
    result = dict(value)
    for key in ("carrier", "package_id", "receiver_id"):
        result[key] = identifier(value[key])
    if result["carrier"] not in vehicle_ids:
        raise ValueError("payload carrier must name a declared vehicle")
    reserved = set(vehicle_ids) | {"aas_" + vid for vid in vehicle_ids}
    if result["package_id"] == result["receiver_id"] or {result["package_id"], result["receiver_id"]} & reserved:
        raise ValueError("payload and receiver IDs must be distinct from vehicle IDs")
    for key, default in (("parent_link", "base_link"), ("child_link", "parcel"),
                         ("receiver_link", "surface"), ("receiver_collision", "receiver_collision")):
        result[key] = identifier(value.get(key, default))
    for key in ("detach_topic", "state_topic", "contact_topic"):
        result[key] = topic(value[key])
        if result[key] == "/stats" or result[key].startswith("/world/"):
            raise ValueError("payload topics must not shadow native world transport")
    if len({result[key] for key in ("detach_topic", "state_topic", "contact_topic")}) != 3:
        raise ValueError("payload transport topics must be distinct")
    result["attachment_offset_enu_m"] = vector(value["attachment_offset_enu_m"], "attachment_offset_enu_m", 3, -100, 100)
    result["package_orientation_rpy"] = vector(value.get("package_orientation_rpy", [0, 0, 0]), "package_orientation_rpy", 3, -2 * math.pi, 2 * math.pi)
    result["receiver_pose"] = vector(value["receiver_pose"], "receiver_pose", 6, -1_000_000, 1_000_000)
    for angle in result["receiver_pose"][3:]:
        bounded(angle, "receiver rotation", -2 * math.pi, 2 * math.pi)
    for key in ("package_size_m", "receiver_size"):
        result[key] = vector(value[key], key, 3, 0.001, 100)
    result["package_mass_kg"] = bounded(value["package_mass_kg"], "package_mass_kg", 0.001, 1000)
    result["contact_duration_ns"] = integer(value["contact_duration_ns"], "contact_duration_ns", physics_step_ns, 60_000_000_000)
    if result["contact_duration_ns"] % physics_step_ns:
        raise ValueError("contact duration must align to the physics step")
    return result


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
    payload: dict | None = None
    altitude_datum: str | None = None
    rgbd_camera: dict | None = None


def prepare(payload, directory):
    fields(payload, ("seed", "world", "vehicles", "warmup"), ("payload", "altitude_datum", "rgbd_camera"))
    camera = camera_config(payload["rgbd_camera"]) if "rgbd_camera" in payload else None
    datum = payload.get("altitude_datum")
    if "altitude_datum" in payload and datum != "amsl":
        raise ValueError("altitude_datum must explicitly be amsl or be omitted")
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
    physical_payload = (
        payload_config(payload["payload"], ids, step) if "payload" in payload else None
    )
    model_names = {v.model_name for v in vehicles}
    if physical_payload is not None:
        if {physical_payload["package_id"], physical_payload["receiver_id"]} & existing:
            raise ValueError("payload/receiver model ID already exists in the world")
        if {physical_payload["child_link"], physical_payload["receiver_link"]} & model_names:
            raise ValueError("payload link names must not alias vehicle model poses")
    if camera is not None:
        from .configure_rgbd import attach_rgbd, sensor_topic

        if "aas_rgbd_" + camera["sensor_id"] + "_link" in model_names:
            raise ValueError("camera link name must not alias a vehicle model pose")

        for vehicle in vehicles:
            base = sensor_topic(camera, vehicle.id)
            topic(base + "/depth_image")
            if physical_payload is not None and any(
                t == base or t.startswith(base + "/")
                for t in (physical_payload["detach_topic"], physical_payload["state_topic"], physical_payload["contact_topic"])
            ):
                raise ValueError("payload and camera topics must not overlap")
        attach_rgbd(world, vehicles, directory, camera)
    if physical_payload is not None:
        from .payload_adapter import attach_payload

        attach_payload(world, vehicles, directory, physical_payload)
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
    return Config(seed, name, destination, vehicles, warmup, step, origin,
                  physical_payload, datum, camera)
