from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree

COLLISION_NAMES = frozenset(
    {
        "base_link_collision_0",
        "base_link_collision_1",
        "base_link_collision_2",
        "base_link_collision_3",
        "base_link_collision_4",
        "rotor_0_collision",
        "rotor_1_collision",
        "rotor_2_collision",
        "rotor_3_collision",
    }
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: inject_contact_sensors.py MODEL_SDF")
    path = Path(sys.argv[1])
    source = path.read_bytes()

    root = ElementTree.fromstring(source)
    observed: set[str] = set()
    for link in (
        element for element in root.iter() if _local_name(element.tag) == "link"
    ):
        collisions = tuple(
            child for child in link if _local_name(child.tag) == "collision"
        )
        for collision in collisions:
            collision_name = collision.attrib.get("name")
            if collision_name not in COLLISION_NAMES:
                raise RuntimeError("x500_base contains an undeclared collision")
            if collision_name in observed:
                raise RuntimeError("x500_base collision names are not unique")
            observed.add(collision_name)
            sensor = ElementTree.SubElement(
                link,
                "sensor",
                {
                    "name": f"aero_contact_{collision_name}",
                    "type": "contact",
                },
            )
            contact = ElementTree.SubElement(sensor, "contact")
            ElementTree.SubElement(contact, "collision").text = collision_name
            ElementTree.SubElement(sensor, "always_on").text = "1"
            ElementTree.SubElement(sensor, "update_rate").text = "250"

    if observed != COLLISION_NAMES:
        raise RuntimeError(
            "x500_base collision inventory differs from the production pin"
        )
    ElementTree.indent(root, space="  ")
    payload = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    path.write_bytes(payload + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
