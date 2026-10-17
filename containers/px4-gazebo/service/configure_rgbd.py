"""Attach explicitly configured, registered RGB-D cameras to PX4 airframes."""

import math
import xml.etree.ElementTree as ET


def attach_rgbd(world, vehicles, directory, config):
    """Generate local wrappers with a declared mount pose relative to base_link."""
    plugin = next(
        (
            p
            for p in world.findall("plugin")
            if p.get("name") == "gz::sim::systems::Sensors"
        ),
        None,
    )
    if plugin is None:
        plugin = ET.SubElement(
            world,
            "plugin",
            filename="gz-sim-sensors-system",
            name="gz::sim::systems::Sensors",
        )
        ET.SubElement(plugin, "render_engine").text = "ogre2"
    for vehicle in vehicles:
        model_dir = directory / ("rgbd_" + vehicle.id)
        model_dir.mkdir(parents=True, exist_ok=True)
        sdf = ET.Element("sdf", version="1.9")
        model = ET.SubElement(sdf, "model", name="rgbd_" + vehicle.id)
        include = ET.SubElement(model, "include", merge="true")
        ET.SubElement(include, "uri").text = "model://" + vehicle.model
        link = ET.SubElement(model, "link", name=config["sensor_id"] + "_link")
        ET.SubElement(
            link, "pose", relative_to="base_link"
        ).text = " ".join(map(str, config["pose_flu"]))
        # A tiny declared sensor mass; no collision body that would mask imagery.
        inertia = ET.SubElement(link, "inertial")
        ET.SubElement(inertia, "mass").text = "0.01"
        tensor = ET.SubElement(inertia, "inertia")
        for key in ("ixx", "iyy", "izz"):
            ET.SubElement(tensor, key).text = "0.00001"
        for key in ("ixy", "ixz", "iyz"):
            ET.SubElement(tensor, key).text = "0"
        sensor = ET.SubElement(link, "sensor", name=config["sensor_id"], type="rgbd_camera")
        ET.SubElement(sensor, "topic").text = sensor_topic(config, vehicle.id)
        ET.SubElement(sensor, "always_on").text = "true"
        ET.SubElement(sensor, "update_rate").text = str(config["update_rate_hz"])
        ET.SubElement(sensor, "visualize").text = "false"
        camera = ET.SubElement(sensor, "camera")
        ET.SubElement(camera, "horizontal_fov").text = str(config["horizontal_fov_rad"])
        image = ET.SubElement(camera, "image")
        for key, value in (("width", str(config["width"])), ("height", str(config["height"])), ("format", "R8G8B8")):
            ET.SubElement(image, key).text = value
        clip = ET.SubElement(camera, "clip")
        ET.SubElement(clip, "near").text = str(config["near_m"])
        ET.SubElement(clip, "far").text = str(config["far_m"])
        depth = ET.SubElement(camera, "depth_camera")
        depth_clip = ET.SubElement(depth, "clip")
        ET.SubElement(depth_clip, "near").text = str(config["near_m"])
        ET.SubElement(depth_clip, "far").text = str(config["far_m"])
        joint = ET.SubElement(model, "joint", name=config["sensor_id"] + "_joint", type="fixed")
        ET.SubElement(joint, "parent").text = "base_link"
        ET.SubElement(joint, "child").text = config["sensor_id"] + "_link"
        model_path = model_dir / "model.sdf"
        ET.ElementTree(sdf).write(model_path, encoding="utf-8", xml_declaration=True)
        existing = next(
            item
            for item in world.findall("include")
            if item.findtext("name") == vehicle.model_name
        )
        existing.find("uri").text = str(model_path)


def sensor_topic(config, vehicle):
    return f"{config['topic_prefix'].rstrip('/')}/{vehicle}/{config['sensor_id']}"


def optical_to_body_frd(pose_flu):
    """SDF sensor FLU -> body FLU -> body FRD, with optical right/down/forward."""
    x, y, z, roll, pitch, yaw = pose_flu
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rotation = [
        [cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
        [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
        [-sp, cp*sr, cp*cr],
    ]
    optical = [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]
    result = [[sign * sum(rotation[i][k] * optical[k][j] for k in range(3))
               for j in range(3)] + [sign * (x, y, z)[i]]
              for i, sign in enumerate((1, -1, -1))]
    return result + [[0, 0, 0, 1]]
