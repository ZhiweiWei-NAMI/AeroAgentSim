"""Gazebo receiver input: only scoped contact messages, never world truth.

Install the contact system on the world and subscribe this object to the
receiver collision sensor. The simulator owner drains receipts into radio.
"""
import json
from pathlib import Path


class ContactReceiver:
    def __init__(self, root, package_id, receiver_collision, source_topic, epoch, max_gap_ns=200_000_000):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.package_id = package_id
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
        if source_ns - self.first_ns < 1_000_000_000:
            return None
        receipt = {"source": "gazebo_contact_sensor", "receiver_id": "drop-receiver-1", "package_id": self.package_id, "source_topic": self.source_topic, "clock_epoch": self.epoch, "contact_start_ns": self.first_ns, "contact_end_ns": source_ns, "contact_duration_ns": source_ns - self.first_ns, "contact_log": "contact-events.jsonl", "receipt_id": self.package_id + "-" + self.epoch + "-contact"}
        self.record_path.write_text(json.dumps(receipt, indent=2))
        self.completed = True
        self.receipts.append(receipt)
        return receipt

    def pop_receipts(self):
        result, self.receipts = self.receipts, []
        return result


def payload_sdf(package_id="rescue-package-1", pose="0 0 1 0 0 0"):
    return f'''<sdf version="1.9"><model name="{package_id}"><pose>{pose}</pose>
<link name="parcel"><inertial><mass>1.0</mass><inertia><ixx>0.0104167</ixx><iyy>0.0104167</iyy><izz>0.015</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia></inertial>
<collision name="parcel_collision"><geometry><box><size>0.3 0.3 0.2</size></box></geometry></collision>
<visual name="parcel_visual"><geometry><box><size>0.3 0.3 0.2</size></box></geometry><material><diffuse>1 0.4 0 1</diffuse></material></visual></link></model></sdf>'''


def detachable_plugin(package_id="rescue-package-1", parent_link="base_link"):
    return f'''<plugin filename="gz-sim-detachable-joint-system" name="gz::sim::systems::DetachableJoint">
<parent_link>{parent_link}</parent_link><child_model>{package_id}</child_model><child_link>parcel</child_link>
<detach_topic>/rescue/{package_id}/detach</detach_topic><output_topic>/rescue/{package_id}/state</output_topic></plugin>'''


def receiver_sdf(package_id="rescue-package-1", pose="0 0 0 0 0 0", size="10 10 0.1"):
    return f'''<sdf version="1.9"><model name="drop-receiver-1"><static>true</static><pose>{pose}</pose>
<link name="surface"><collision name="receiver_collision"><geometry><box><size>{size}</size></box></geometry></collision>
<visual name="receiver_visual"><geometry><box><size>{size}</size></box></geometry><material><diffuse>1 0.8 0 1</diffuse></material></visual>
<sensor name="receiver_contact" type="contact"><always_on>true</always_on><update_rate>50</update_rate><topic>/rescue/receiver/contacts</topic><contact><collision>receiver_collision</collision></contact></sensor></link>
<plugin filename="gz-sim-touchplugin-system" name="gz::sim::systems::TouchPlugin"><target>{package_id}</target><namespace>rescue_receiver</namespace><time>1</time><enabled>true</enabled></plugin>
</model></sdf>'''
