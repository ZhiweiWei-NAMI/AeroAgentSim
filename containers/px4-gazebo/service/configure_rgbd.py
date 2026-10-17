"""Attach a per-UAV registered downward RGB-D sensor to the existing airframe."""

import math
import xml.etree.ElementTree as ET


def attach_rgbd(world, vehicles, directory):
    """Generate local wrappers; original PX4 model and airframe remain unchanged.

    Sensor X-forward/Y-left/Z-up is pitched +90 degrees in the body's FLU
    frame, placing its optical axis down. RGB and depth are produced by the
    same rgbd_camera, so they share K, FOV, resolution and optical origin.
    """
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
        link = ET.SubElement(model, "link", name="rescue_rgbd_link")
        ET.SubElement(
            link, "pose", relative_to="base_link"
        ).text = f"0 0 -0.1 0 {math.pi / 2} 0"
        # A tiny declared sensor mass; no collision body that would mask imagery.
        inertia = ET.SubElement(link, "inertial")
        ET.SubElement(inertia, "mass").text = "0.01"
        tensor = ET.SubElement(inertia, "inertia")
        for key in ("ixx", "iyy", "izz"):
            ET.SubElement(tensor, key).text = "0.00001"
        for key in ("ixy", "ixz", "iyz"):
            ET.SubElement(tensor, key).text = "0"
        sensor = ET.SubElement(link, "sensor", name="rescue_rgbd", type="rgbd_camera")
        ET.SubElement(sensor, "topic").text = f"/aas/{vehicle.id}/rgbd"
        ET.SubElement(sensor, "always_on").text = "true"
        ET.SubElement(sensor, "update_rate").text = "5"
        ET.SubElement(sensor, "visualize").text = "false"
        camera = ET.SubElement(sensor, "camera")
        ET.SubElement(camera, "horizontal_fov").text = str(math.radians(80))
        image = ET.SubElement(camera, "image")
        for key, value in (("width", "1280"), ("height", "960"), ("format", "R8G8B8")):
            ET.SubElement(image, key).text = value
        clip = ET.SubElement(camera, "clip")
        ET.SubElement(clip, "near").text = "0.2"
        ET.SubElement(clip, "far").text = "60"
        depth = ET.SubElement(camera, "depth_camera")
        depth_clip = ET.SubElement(depth, "clip")
        ET.SubElement(depth_clip, "near").text = "0.2"
        ET.SubElement(depth_clip, "far").text = "60"
        joint = ET.SubElement(model, "joint", name="rescue_rgbd_joint", type="fixed")
        ET.SubElement(joint, "parent").text = "base_link"
        ET.SubElement(joint, "child").text = "rescue_rgbd_link"
        model_path = model_dir / "model.sdf"
        ET.ElementTree(sdf).write(model_path, encoding="utf-8", xml_declaration=True)
        existing = next(
            item
            for item in world.findall("include")
            if item.findtext("name") == vehicle.model_name
        )
        existing.find("uri").text = str(model_path)
