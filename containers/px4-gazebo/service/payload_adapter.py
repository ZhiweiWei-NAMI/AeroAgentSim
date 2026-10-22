"""Native detachable joint ownership and source-stamped contact receipts."""

import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import identifier, integer
from .payload_receiver import ContactReceiver, detachable_plugin, payload_sdf, receiver_sdf
from .world_plugins import ensure_baseline_systems


def attach_payload(world, vehicles, directory, config):
    ensure_baseline_systems(world)
    carrier = next(v for v in vehicles if v.id == config["carrier"])
    include = next(e for e in world.findall("include") if e.findtext("name") == carrier.model_name)
    uri = include.findtext("uri")
    if uri.startswith("model://"):
        sdf = ET.Element("sdf", version="1.9")
        model = ET.SubElement(sdf, "model", name="payload_" + carrier.id)
        merged = ET.SubElement(model, "include", merge="true")
        ET.SubElement(merged, "uri").text = uri
        wrapper = directory / ("payload_" + carrier.id + ".sdf")
    else:
        wrapper = Path(uri.removeprefix("file://")).resolve()
        # Only extend the generated local camera wrapper, never a mounted model.
        if wrapper.parent.parent != directory.resolve() or wrapper.name != "model.sdf":
            raise ValueError("payload wrapper must be generated in the reset directory")
        sdf = ET.parse(wrapper).getroot()
        model = sdf.find("model")
    model.append(ET.fromstring(detachable_plugin(config["package_id"],
        detach_topic=config["detach_topic"], state_topic=config["state_topic"],
        parent_link=config["parent_link"], child_link=config["child_link"])))
    ET.ElementTree(sdf).write(wrapper, encoding="utf-8", xml_declaration=True)
    include.find("uri").text = str(wrapper)
    offset = config["attachment_offset_enu_m"]
    pose = [carrier.spawn[i] + offset[i] for i in range(3)] + config["package_orientation_rpy"]
    world.append(ET.fromstring(payload_sdf(config["package_id"], " ".join(map(str, pose)),
        size=config["package_size_m"], mass=config["package_mass_kg"], child_link=config["child_link"])).find("model"))
    world.append(ET.fromstring(receiver_sdf(config["receiver_id"], " ".join(map(str, config["receiver_pose"])),
        config["receiver_size"], contact_topic=config["contact_topic"],
        receiver_link=config["receiver_link"], receiver_collision=config["receiver_collision"])).find("model"))
    if not any(p.get("name") == "gz::sim::systems::Contact" for p in world.findall("plugin")):
        ET.SubElement(world, "plugin", filename="gz-sim-contact-system", name="gz::sim::systems::Contact")


class PayloadAdapter:
    def __init__(self, config, gazebo, directory, epoch, logical_clock, *, physics_step_ns):
        self.config, self.gazebo = config, gazebo
        self.logical_clock = logical_clock
        self.epoch = identifier(epoch)
        self.origin_ns = None
        self.lock = threading.RLock()
        self.state = {
            "vehicle": config["carrier"], "package_id": config["package_id"],
            "clock_epoch": self.epoch, "status": "unavailable", "released": False,
            "source": "gazebo_detachable_joint", "source_clock": None,
            "source_time_ns": None, "observed_sim_ns": 0, "command_id": None,
            "reason": "joint state not yet observed",
        }
        self.release_request = None
        self.events, self.received = [], []
        self.receiver = ContactReceiver(directory, config["package_id"],
            f"{config['receiver_id']}::{config['receiver_link']}::{config['receiver_collision']}",
            config["contact_topic"], self.epoch, receiver_id=config["receiver_id"],
            contact_duration_ns=config["contact_duration_ns"], max_gap_ns=physics_step_ns)
        self.publisher = None
        self.active = True

    def set_episode_origin(self, native_origin_ns):
        with self.lock:
            self.origin_ns = integer(native_origin_ns, "episode origin")
            self.events.clear()  # Warmup transitions are not episode evidence.
            self.state.update(status="unavailable", released=False, source_clock=None,
                              source_time_ns=None, observed_sim_ns=0,
                              reason="joint state not yet observed in this episode")

    async def start(self):
        from gz.msgs10.contacts_pb2 import Contacts
        from gz.msgs10.empty_pb2 import Empty
        from gz.msgs10.stringmsg_pb2 import StringMsg

        self.empty_type = Empty
        await self.gazebo.subscribe(StringMsg, self.config["state_topic"], self.on_state)
        await self.gazebo.subscribe(Contacts, self.config["contact_topic"], self.on_contacts)
        self.publisher = self.gazebo.node.advertise(self.config["detach_topic"], Empty)
        if not self.publisher:
            raise RuntimeError("could not advertise payload release topic")

    def queue_state(self):
        if len(self.events) >= 8192:
            raise RuntimeError("payload event budget exceeded")
        self.events.append(dict(self.state))

    async def initialize_attachment(self, carrier_model):
        # Configuration/transport ACKs do not prove a physical joint exists.
        result = await self.gazebo.payload_attachment(carrier_model, self.config["package_id"],
            parent_link=self.config["parent_link"], child_link=self.config["child_link"])
        with self.lock:
            if result["status"] == "attached":
                native_ns = integer(result["native_source_time_ns"], "joint snapshot time")
                if native_ns != self.origin_ns:
                    raise RuntimeError("joint bootstrap is not at the episode origin")
                self.state.update(status="attached", released=False, source_clock="episode_sim",
                                  source_time_ns=0, observed_sim_ns=0)
                self.state.pop("reason", None)
            else:
                self.state["reason"] = result["reason"]
            self.queue_state()

    def on_state(self, message):
        if not self.active:
            return
        try:
            if message.data not in {"attached", "detached"}:
                raise ValueError("unknown detachable-joint state")
            with self.lock:
                if self.origin_ns is None:
                    return
                observed = integer(max(0, self.logical_clock()), "joint observation time")
                if message.data == self.state["status"]:
                    return
                if self.state["released"] and message.data == "attached":
                    raise ValueError("released joint unexpectedly reattached")
                self.state.update(status=message.data, released=message.data == "detached",
                                  source_clock=None, source_time_ns=None,
                                  observed_sim_ns=observed)
                self.state.pop("reason", None)
                if self.release_request is not None and message.data == "detached":
                    accepted = self.release_request["sim_ns"]
                    self.state["release_interval_ns"] = [accepted, observed]
                self.queue_state()
        except Exception as error:
            self.gazebo.fail(error)

    def on_contacts(self, message):
        if not self.active:
            return
        try:
            with self.lock:
                self.receiver.on_contacts(message)
                for receipt in self.receiver.pop_receipts():
                    if self.release_request is None or self.origin_ns is None:
                        raise ValueError("contact completion without an admitted release")
                    self.received.append({**receipt,
                        "command_id": self.release_request["command_id"],
                        "vehicle": self.config["carrier"], "source_clock": "gazebo_sim",
                        "episode_origin_ns": self.origin_ns,
                        "contact_start_sim_ns": receipt["contact_start_ns"] - self.origin_ns,
                        "contact_end_sim_ns": receipt["contact_end_ns"] - self.origin_ns})
        except Exception as error:
            self.gazebo.fail(error)

    def release(self, vehicle, package_id, sim_ns, command_id):
        vehicle, package_id, command_id = identifier(vehicle), identifier(package_id), identifier(command_id)
        sim_ns = integer(sim_ns, "release time")
        with self.lock:
            identity = {"command_id": command_id, "vehicle": vehicle,
                        "package_id": package_id, "sim_ns": sim_ns}
            if vehicle != self.config["carrier"] or package_id != self.config["package_id"]:
                return {**identity, "status": "rejected", "reason": "payload does not belong to this vehicle"}
            if self.release_request is not None:
                return {**identity, "status": "rejected", "reason": "release already requested; no retry"}
            if self.state["status"] != "attached" or self.origin_ns is None or self.publisher is None:
                return {**identity, "status": "unavailable", "reason": "physical attachment has not been observed"}
            # Latch before publishing: failure/exception is not safe to retry.
            self.release_request = {**identity, "status": "accepted"}
            self.state["command_id"] = command_id
            if not self.publisher.publish(self.empty_type()):
                self.release_request.update(status="failed", reason="Gazebo transport rejected detach publication")
            return dict(self.release_request)

    def status(self, vehicle, package_id):
        if identifier(vehicle) != self.config["carrier"] or identifier(package_id) != self.config["package_id"]:
            raise ValueError("payload does not belong to this vehicle")
        with self.lock:
            return dict(self.state)

    def drain(self, sim_ns):
        sim_ns = integer(sim_ns, "payload frontier")
        with self.lock:
            # The stamp-less joint update's source time remains explicitly null;
            # a confirmed boundary supplies observation availability, not truth.
            for event in self.events:
                event["observed_sim_ns"] = sim_ns
                if event["released"] and self.release_request:
                    event["release_interval_ns"] = [self.release_request["sim_ns"], sim_ns]
                    if self.receiver.release_ns is None and self.release_request["status"] == "accepted":
                        # Joint transitions are unstamped. Credit contact only
                        # after the confirmed upper bound of observed detachment,
                        # never attached/pre-detach contact delivered late.
                        self.receiver.arm(self.origin_ns + sim_ns)
            if self.events:
                self.state = dict(self.events[-1])
            for receipt in self.received:
                if receipt["contact_end_sim_ns"] > sim_ns:
                    raise RuntimeError("payload contact is ahead of the confirmed frontier")
            events, receipts = self.events, self.received
            self.events, self.received = [], []
            return events, receipts

    async def stop(self):
        self.active = False
        self.publisher = None
