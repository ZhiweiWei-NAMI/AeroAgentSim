"""Scoped native contact evidence; receipt completion never follows a detach ACK."""

import xml.etree.ElementTree as ET

from .config import identifier, integer, topic


class ContactReceiver:
    def __init__(self, root, package_id, receiver_collision, source_topic, epoch, *,
                 receiver_id, contact_duration_ns, max_gap_ns):
        self.package_id = identifier(package_id)
        self.receiver_id = identifier(receiver_id)
        self.receiver_collision = receiver_collision
        self.source_topic = topic(source_topic)
        self.epoch = identifier(epoch)
        self.contact_duration_ns = integer(contact_duration_ns, "contact_duration_ns", 1)
        self.max_gap_ns = integer(max_gap_ns, "max_gap_ns", 1)
        self.first_ns = self.last_ns = self.source_ns = None
        self.release_ns = None
        self.completed = False
        self.receipts = []
        # No local file restores completion: recorded owner receipts are the
        # replay contract. `root` is retained for the archived constructor ABI.

    def arm(self, release_ns):
        if self.release_ns is not None:
            raise ValueError("contact receiver is already armed")
        self.release_ns = integer(release_ns, "release source time")
        self.first_ns = self.last_ns = None

    def on_contacts(self, message):
        if not message.HasField("header") or not message.header.HasField("stamp"):
            raise ValueError("payload contact sensor omitted source timestamp")
        stamp = message.header.stamp
        sec = integer(stamp.sec, "contact seconds")
        nsec = integer(stamp.nsec, "contact nanoseconds", 0, 999_999_999)
        pairs = [(c.collision1.name, c.collision2.name) for c in message.contact]
        return self.consume(sec * 1_000_000_000 + nsec, pairs)

    def consume(self, source_ns, collision_pairs):
        source_ns = integer(source_ns, "contact source time")
        if self.source_ns is not None and source_ns < self.source_ns:
            self.first_ns = self.last_ns = None
            raise ValueError("contact source clock regressed")
        self.source_ns = source_ns
        matching = False
        for pair in collision_pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(
                not isinstance(name, str) or not name for name in pair
            ):
                raise ValueError("contact requires two nonempty scoped collision names")
            first, second = pair
            other = second if first == self.receiver_collision else first if second == self.receiver_collision else None
            if other and other.startswith(self.package_id + "::"):
                matching = True
        if self.completed or self.release_ns is None or source_ns < self.release_ns:
            return None
        if not matching:
            self.first_ns = self.last_ns = None
            return None
        if self.last_ns is None or source_ns - self.last_ns > self.max_gap_ns:
            self.first_ns = source_ns
        self.last_ns = source_ns
        if source_ns - self.first_ns < self.contact_duration_ns:
            return None
        receipt = {
            "source": "gazebo_contact_sensor", "receiver_id": self.receiver_id,
            "package_id": self.package_id, "source_topic": self.source_topic,
            "clock_epoch": self.epoch, "contact_start_ns": self.first_ns,
            "contact_end_ns": source_ns,
            "contact_duration_ns": source_ns - self.first_ns,
            "receipt_id": self.package_id + "-" + self.epoch + "-contact",
        }
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
    values = {"ixx": mass * (y * y + z * z) / 12,
              "iyy": mass * (x * x + z * z) / 12,
              "izz": mass * (x * x + y * y) / 12, "ixy": 0, "ixz": 0, "iyz": 0}
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
