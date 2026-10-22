"""Gazebo receiver input: only scoped contact messages, never world truth.

Install the contact system on the world and subscribe this object to the
receiver collision sensor. The world owner drains source-stamped contact records.
"""
import json
from pathlib import Path
import xml.etree.ElementTree as ET


class ContactReceiver:
    def __init__(self, root, package_id, receiver_collision, source_topic, epoch, *,
                 receiver_id, contact_duration_ns, max_gap_ns):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.package_id = package_id
        self.receiver_id = receiver_id
        self.contact_duration_ns = contact_duration_ns
        self.receiver_collision = receiver_collision
        self.source_topic, self.epoch = source_topic, epoch
        self.max_gap_ns = max_gap_ns
        self.first_ns = self.last_ns = None
        self.receipts = []
        self.record_path = self.root / (package_id + "-receipt.json")
        self.completed = self.record_path.exists()
        if self.completed:
            record = json.loads(self.record_path.read_text())
            if record["clock_epoch"] != epoch:
                raise ValueError("receiver directory belongs to another clock epoch")
            self.receipts.append(record)

    def on_contacts(self, message):
        stamp = message.header.stamp
        source_ns = stamp.sec * 1_000_000_000 + stamp.nsec
        pairs = [(contact.collision1.name, contact.collision2.name) for contact in message.contact]
        return self.consume(source_ns, pairs)

    def consume(self, source_ns, collision_pairs):
        matching = []
        for first, second in collision_pairs:
            other = second if first == self.receiver_collision else first if second == self.receiver_collision else None
            if other and (other == self.package_id or other.startswith(self.package_id + "::")):
                matching.append([first, second])
        row = {"source": "gazebo_contact_sensor", "source_topic": self.source_topic, "clock_epoch": self.epoch, "source_time_ns": source_ns, "collision_pairs": collision_pairs, "package_id": self.package_id}
        with (self.root / "contact-events.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        if self.completed:
            return None
        if not matching:
            self.first_ns = self.last_ns = None
            return None
        if self.last_ns is not None and source_ns < self.last_ns:
            raise ValueError("contact source clock regressed")
        if self.last_ns is None or source_ns - self.last_ns > self.max_gap_ns:
            self.first_ns = source_ns
        self.last_ns = source_ns
        if source_ns - self.first_ns < self.contact_duration_ns:
            return None
        receipt = {"source": "gazebo_contact_sensor", "receiver_id": self.receiver_id, "package_id": self.package_id, "source_topic": self.source_topic, "clock_epoch": self.epoch, "contact_start_ns": self.first_ns, "contact_end_ns": source_ns, "contact_duration_ns": source_ns - self.first_ns, "contact_log": "contact-events.jsonl", "receipt_id": self.package_id + "-" + self.epoch + "-contact"}
        self.record_path.write_text(json.dumps(receipt, indent=2))
        self.completed = True
        self.receipts.append(receipt)
        return receipt

    def pop_receipts(self):
        result, self.receipts = self.receipts, []
        return result



def _box(parent, size):
    geometry = ET.SubElement(parent, "geometry")
    box = ET.SubElement(geometry, "box")
    ET.SubElement(box, "size").text = " ".join(map(str, size))


def payload_sdf(package_id, pose, *, size, mass, child_link="parcel"):
    root = ET.Element("sdf", version="1.9")
    model = ET.SubElement(root, "model", name=package_id)
    ET.SubElement(model, "pose").text = pose
    link = ET.SubElement(model, "link", name=child_link)
    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(inertial, "mass").text = str(mass)
    inertia = ET.SubElement(inertial, "inertia")
    x, y, z = size
    values = {"ixx": mass*(y*y+z*z)/12, "iyy": mass*(x*x+z*z)/12,
              "izz": mass*(x*x+y*y)/12, "ixy": 0, "ixz": 0, "iyz": 0}
    for name, value in values.items():
        ET.SubElement(inertia, name).text = str(value)
    _box(ET.SubElement(link, "collision", name=child_link + "_collision"), size)
    _box(ET.SubElement(link, "visual", name=child_link + "_visual"), size)
    return ET.tostring(root, encoding="unicode")


def detachable_plugin(package_id, *, detach_topic, state_topic,
                      parent_link="base_link", child_link="parcel"):
    plugin = ET.Element("plugin", filename="gz-sim-detachable-joint-system",
                        name="gz::sim::systems::DetachableJoint")
    for name, value in {"parent_link": parent_link, "child_model": package_id,
                        "child_link": child_link, "detach_topic": detach_topic,
                        "output_topic": state_topic}.items():
        ET.SubElement(plugin, name).text = value
    return ET.tostring(plugin, encoding="unicode")


def receiver_sdf(receiver_id, pose, size, *, contact_topic,
                 receiver_link="surface", receiver_collision="receiver_collision"):
    root = ET.Element("sdf", version="1.9")
    model = ET.SubElement(root, "model", name=receiver_id)
    ET.SubElement(model, "static").text = "true"
    ET.SubElement(model, "pose").text = pose
    link = ET.SubElement(model, "link", name=receiver_link)
    _box(ET.SubElement(link, "collision", name=receiver_collision), size)
    _box(ET.SubElement(link, "visual", name=receiver_collision + "_visual"), size)
    sensor = ET.SubElement(link, "sensor", name=receiver_collision + "_contact", type="contact")
    ET.SubElement(sensor, "always_on").text = "true"
    ET.SubElement(sensor, "update_rate").text = "0"
    ET.SubElement(sensor, "topic").text = contact_topic
    contact = ET.SubElement(sensor, "contact")
    ET.SubElement(contact, "collision").text = receiver_collision
    ET.SubElement(contact, "topic").text = contact_topic
    return ET.tostring(root, encoding="unicode")
