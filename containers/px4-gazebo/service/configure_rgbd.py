"""Attach explicitly configured Gazebo geometric RGB-D cameras to PX4 models.

This is a rendered, registered optical-Z depth image, not a physical range
sensor model (no ranging noise, multipath, material response or dropout model).
Configuration is validated by config.camera_config before reaching this module.
"""

import math
import xml.etree.ElementTree as ET

from .world_plugins import ensure_baseline_systems


def sensor_topic(config, vehicle):
    return f"{config['topic_prefix'].rstrip('/')}/{vehicle}/{config['sensor_id']}"


def sensor_frame(config, vehicle):
    """One declared native frame identity for RGB, depth and CameraInfo."""
    return f"{vehicle.model_name}::aas_rgbd_{config['sensor_id']}_link::{config['sensor_id']}"


def attach_rgbd(world, vehicles, directory, config):
    """Generate local wrappers with the mount pose relative to base_link FLU."""
    ensure_baseline_systems(world)
    plugin = next(
        (p for p in world.findall("plugin")
         if p.get("name") == "gz::sim::systems::Sensors"),
        None,
    )
    if plugin is None:
        plugin = ET.SubElement(
            world, "plugin", filename="gz-sim-sensors-system",
            name="gz::sim::systems::Sensors",
        )
    engine = plugin.find("render_engine")
    if engine is None:
        engine = ET.SubElement(plugin, "render_engine")
        engine.text = "ogre2"
    elif (engine.text or "").strip() != "ogre2":
        raise ValueError("RGBD requires the Gazebo ogre2 render engine")

    for vehicle in vehicles:
        matches = [item for item in world.findall("include")
                   if item.findtext("name") == vehicle.model_name]
        if len(matches) != 1 or matches[0].find("uri") is None:
            raise ValueError("RGBD owner requires exactly one named model include")
        model_dir = directory / ("rgbd_" + vehicle.id)
        model_dir.mkdir(parents=True, exist_ok=True)
        sdf = ET.Element("sdf", version="1.9")
        model = ET.SubElement(sdf, "model", name="rgbd_" + vehicle.id)
        include = ET.SubElement(model, "include", merge="true")
        ET.SubElement(include, "uri").text = "model://" + vehicle.model
        link_name = "aas_rgbd_" + config["sensor_id"] + "_link"
        link = ET.SubElement(model, "link", name=link_name)
        ET.SubElement(link, "pose", relative_to="base_link").text = " ".join(
            format(value, ".17g") for value in config["pose_flu"]
        )
        # Declare the mounting mass; omit collisions that could mask the image.
        inertial = ET.SubElement(link, "inertial")
        ET.SubElement(inertial, "mass").text = "0.01"
        tensor = ET.SubElement(inertial, "inertia")
        for key in ("ixx", "iyy", "izz"):
            ET.SubElement(tensor, key).text = "0.00001"
        for key in ("ixy", "ixz", "iyz"):
            ET.SubElement(tensor, key).text = "0"
        sensor = ET.SubElement(
            link, "sensor", name=config["sensor_id"], type="rgbd_camera"
        )
        ET.SubElement(sensor, "topic").text = sensor_topic(config, vehicle.id)
        ET.SubElement(sensor, "gz_frame_id").text = sensor_frame(config, vehicle)
        ET.SubElement(sensor, "always_on").text = "true"
        ET.SubElement(sensor, "update_rate").text = str(config["update_rate_hz"])
        ET.SubElement(sensor, "visualize").text = "false"
        camera = ET.SubElement(sensor, "camera")
        ET.SubElement(camera, "horizontal_fov").text = str(config["horizontal_fov_rad"])
        image = ET.SubElement(camera, "image")
        for key, value in (("width", config["width"]),
                           ("height", config["height"]), ("format", "R8G8B8")):
            ET.SubElement(image, key).text = str(value)
        clip = ET.SubElement(camera, "clip")
        ET.SubElement(clip, "near").text = str(config["near_m"])
        ET.SubElement(clip, "far").text = str(config["far_m"])
        # RGBD uses the SAME image dimensions and optics for both streams.
        depth = ET.SubElement(camera, "depth_camera")
        depth_clip = ET.SubElement(depth, "clip")
        ET.SubElement(depth_clip, "near").text = str(config["near_m"])
        ET.SubElement(depth_clip, "far").text = str(config["far_m"])
        joint = ET.SubElement(
            model, "joint", name="aas_rgbd_" + config["sensor_id"] + "_joint", type="fixed"
        )
        ET.SubElement(joint, "parent").text = "base_link"
        ET.SubElement(joint, "child").text = link_name
        model_path = model_dir / "model.sdf"
        ET.ElementTree(sdf).write(model_path, encoding="utf-8", xml_declaration=True)
        matches[0].find("uri").text = str(model_path)


def optical_to_body_frd(pose_flu):
    """SDF sensor FLU -> body FLU -> body FRD; optical axes right/down/forward."""
    if not isinstance(pose_flu, (list, tuple)) or len(pose_flu) != 6:
        raise ValueError("camera pose must contain six finite metres/radians")
    if any(type(value) not in (int, float) for value in pose_flu):
        raise ValueError("camera pose must contain six finite metres/radians")
    try:
        values = [float(value) for value in pose_flu]
    except (OverflowError, ValueError) as error:
        raise ValueError("camera pose must contain six finite metres/radians") from error
    if not all(math.isfinite(value) for value in values):
        raise ValueError("camera pose must contain six finite metres/radians")
    x, y, z, roll, pitch, yaw = values
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rotation = [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]
    optical = [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]
    result = [
        [sign * sum(rotation[i][k] * optical[k][j] for k in range(3))
         for j in range(3)] + [sign * (x, y, z)[i]]
        for i, sign in enumerate((1, -1, -1))
    ]
    return result + [[0, 0, 0, 1]]
