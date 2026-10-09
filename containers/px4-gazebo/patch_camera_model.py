from __future__ import annotations

import math
import sys
from pathlib import Path
from xml.etree import ElementTree

EXPECTED_WIDTH = 1280
EXPECTED_HEIGHT = 960
EXPECTED_HORIZONTAL_FOV = 1.74
TARGET_WIDTH = 640
TARGET_HEIGHT = 480
TARGET_HORIZONTAL_FOV = 80.0 * math.pi / 180.0
TARGET_VERTICAL_FOV = 60.0 * math.pi / 180.0
EXPECTED_ALWAYS_ON = "1"
EXPECTED_UPDATE_RATE_HZ = 30.0
TARGET_ALWAYS_ON = "0"
TARGET_TRIGGERED = "true"
TARGET_TRIGGER_TOPIC = "/aeroagentsim/camera/trigger"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(parent: ElementTree.Element, name: str) -> ElementTree.Element:
    for item in parent:
        if local_name(item.tag) == name:
            return item
    raise SystemExit(f"camera model is missing <{name}>")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_camera_model.py MODEL_SDF")
    path = Path(sys.argv[1])
    try:
        root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ElementTree.ParseError, UnicodeError) as exc:
        raise SystemExit(f"cannot parse camera model: {path}") from exc
    sensors = [
        element
        for element in root.iter()
        if local_name(element.tag) == "sensor"
        and element.attrib.get("type") == "camera"
    ]
    if len(sensors) != 1:
        raise SystemExit(f"expected exactly one camera sensor, got {len(sensors)}")
    camera = child(sensors[0], "camera")
    image = child(camera, "image")
    width = child(image, "width")
    height = child(image, "height")
    fov = child(camera, "horizontal_fov")
    always_on = child(sensors[0], "always_on")
    update_rate = child(sensors[0], "update_rate")
    triggered = (
        child(camera, "triggered")
        if any(local_name(item.tag) == "triggered" for item in camera)
        else None
    )
    if int((width.text or "").strip()) != EXPECTED_WIDTH:
        raise SystemExit("unexpected source camera width")
    if int((height.text or "").strip()) != EXPECTED_HEIGHT:
        raise SystemExit("unexpected source camera height")
    if not math.isclose(float((fov.text or "").strip()), EXPECTED_HORIZONTAL_FOV):
        raise SystemExit("unexpected source camera horizontal FOV")
    if (always_on.text or "").strip() != EXPECTED_ALWAYS_ON:
        raise SystemExit("unexpected source camera always_on setting")
    if not math.isclose(
        float((update_rate.text or "").strip()), EXPECTED_UPDATE_RATE_HZ
    ):
        raise SystemExit("unexpected source camera update rate")
    if triggered is not None:
        raise SystemExit("source camera unexpectedly declares triggered mode")
    width.text = str(TARGET_WIDTH)
    height.text = str(TARGET_HEIGHT)
    fov.text = format(TARGET_HORIZONTAL_FOV, ".17g")
    # The camera is an evidence sensor, not part of the PX4 control loop.  Keep
    # it inactive until an observation subscriber explicitly requests a frame;
    # the provider verifies that the returned frame is fresh at the barrier.
    always_on.text = TARGET_ALWAYS_ON
    # Gazebo only advances rendering sensors on a simulation update.  Triggered
    # mode provides a transport-level capture transaction that can run while
    # the authoritative world is paused, so the frame timestamp remains exactly
    # on the 500 ms barrier without a hidden physics step.
    triggered = ElementTree.Element("triggered")
    triggered.text = TARGET_TRIGGERED
    trigger_topic = ElementTree.Element("trigger_topic")
    trigger_topic.text = TARGET_TRIGGER_TOPIC
    camera.insert(0, triggered)
    camera.insert(1, trigger_topic)
    # Keep the upstream rate while active.  The always_on=0 gate removes all
    # background rendering between demand-driven captures.
    update_rate.text = format(EXPECTED_UPDATE_RATE_HZ, ".17g")
    # The benchmark captures the sensor stream in headless mode.  The GUI-only
    # visualizer adds a second render path and is not part of sensor evidence.
    visualize = child(sensors[0], "visualize")
    if (visualize.text or "").strip().lower() != "true":
        raise SystemExit("unexpected source camera visualize setting")
    visualize.text = "false"
    ElementTree.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    print(path)


if __name__ == "__main__":
    main()
