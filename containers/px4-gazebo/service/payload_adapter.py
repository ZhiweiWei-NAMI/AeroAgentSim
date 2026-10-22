"""Native detachable-joint commands and receiver contact subscriptions."""
import json
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .payload_receiver import ContactReceiver, detachable_plugin, payload_sdf, receiver_sdf


def attach_payload(world, vehicles, directory, config):
    """Place physical payload and receiver and attach the existing carrier model."""
    carrier = next(v for v in vehicles if v.id == config['carrier'])
    package_id = config['package_id']
    include = next(e for e in world.findall('include') if e.findtext('name') == carrier.model_name)
    uri = include.findtext('uri')
    if uri.startswith('model://'):
        sdf = ET.Element('sdf', version='1.9')
        model = ET.SubElement(sdf, 'model', name='payload_' + carrier.id)
        merged = ET.SubElement(model, 'include', merge='true')
        ET.SubElement(merged, 'uri').text = uri
        wrapper = directory / ('payload_' + carrier.id + '.sdf')
    else:
        wrapper = Path(uri.removeprefix('file://'))
        sdf = ET.parse(wrapper).getroot()
        model = sdf.find('model')
    model.append(ET.fromstring(detachable_plugin(package_id,
        detach_topic=config['detach_topic'], state_topic=config['state_topic'],
        parent_link=config.get('parent_link', 'base_link'), child_link=config.get('child_link', 'parcel'))))
    ET.ElementTree(sdf).write(wrapper, encoding='utf-8', xml_declaration=True)
    include.find('uri').text = str(wrapper)
    offset = config['attachment_offset_enu_m']
    package_pose = [carrier.spawn[i] + offset[i] for i in range(3)] + config.get('package_orientation_rpy', [0, 0, 0])
    world.append(ET.fromstring(payload_sdf(package_id, ' '.join(map(str, package_pose)),
        size=config['package_size_m'], mass=config['package_mass_kg'],
        child_link=config.get('child_link', 'parcel'))).find('model'))
    receiver_pose = ' '.join(map(str, config['receiver_pose']))
    size = config['receiver_size']
    world.append(ET.fromstring(receiver_sdf(config['receiver_id'], receiver_pose, size,
        contact_topic=config['contact_topic'], receiver_link=config.get('receiver_link', 'surface'),
        receiver_collision=config.get('receiver_collision', 'receiver_collision'))).find('model'))
    if not any(p.get('name') == 'gz::sim::systems::Contact' for p in world.findall('plugin')):
        ET.SubElement(world, 'plugin', filename='gz-sim-contact-system', name='gz::sim::systems::Contact')


class PayloadAdapter:
    def __init__(self, config, gazebo, directory, epoch, logical_clock, *, physics_step_ns):
        self.config, self.gazebo, self.directory = config, gazebo, directory
        self.logical_clock = logical_clock
        self.lock = threading.RLock()
        self.state = {'package_id': config['package_id'], 'carrier': config['carrier'],
            'status': 'unavailable', 'released': False, 'source': 'gazebo_detachable_joint',
            'source_time_ns': None, 'reason': 'joint state not yet observed'}
        self.release_request = None
        self.events = []
        self.received = []
        self.receiver = ContactReceiver(directory, config['package_id'],
            f"{config['receiver_id']}::{config.get('receiver_link', 'surface')}::{config.get('receiver_collision', 'receiver_collision')}",
            config['contact_topic'], epoch, receiver_id=config['receiver_id'],
            contact_duration_ns=config['contact_duration_ns'], max_gap_ns=physics_step_ns)
        self.publisher = None

    async def start(self):
        from gz.msgs10.contacts_pb2 import Contacts
        from gz.msgs10.empty_pb2 import Empty
        from gz.msgs10.stringmsg_pb2 import StringMsg
        self.empty_type = Empty
        package_id = self.config['package_id']
        await self.gazebo.subscribe(StringMsg, self.config['state_topic'], self.on_state)
        await self.gazebo.subscribe(Contacts, self.config['contact_topic'], self.on_contacts)
        self.publisher = self.gazebo.node.advertise(self.config['detach_topic'], Empty)
        if not self.publisher:
            raise RuntimeError('could not advertise payload release topic')

    async def initialize_attachment(self, carrier_model):
        # The initial attached transition can precede subscriber discovery even
        # while Gazebo is paused. Query the real joint; never infer from config.
        with self.lock:
            if self.state['status'] != 'unavailable':
                return
        result = await self.gazebo.payload_attachment(carrier_model, self.config['package_id'],
            parent_link=self.config.get('parent_link', 'base_link'),
            child_link=self.config.get('child_link', 'parcel'))
        with self.lock:
            if self.state['status'] != 'unavailable':
                return
            if result['status'] != 'attached':
                self.state['reason'] = result.get('reason', 'native joint unavailable')
                return
            observed = max(0, self.logical_clock())
            self.state.update(result, observed_sim_ns=observed,
                              source_time_ns=observed, source_clock='episode_sim',
                              native_source_clock='gazebo_sim', received_wall_ns=time.time_ns())
            self.state.pop('reason', None)
            self.events.append(dict(self.state))

    def on_state(self, message):
        with self.lock:
            if message.data not in {'attached', 'detached'}:
                return
            # The upstream plugin publishes no source stamp. Retain observation
            # availability and an interval, never pretend this is source time.
            observed = max(0, self.logical_clock())
            self.state.update(status=message.data, released=message.data == 'detached',
                              source='gazebo_detachable_joint', source_time_ns=None, source_clock=None,
                              observed_sim_ns=observed, received_wall_ns=time.time_ns())
            self.state.pop('native_source_time_ns', None)
            self.state.pop('native_source_clock', None)
            self.state.pop('reason', None)
            if self.release_request is not None and message.data == 'detached':
                self.state['release_interval_ns'] = [self.release_request['accepted_sim_ns'], observed]
            self.events.append(dict(self.state))

    def on_contacts(self, message):
        with self.lock:
            self.receiver.on_contacts(message)
            self.received.extend(self.receiver.pop_receipts())

    def release(self, vehicle, package_id, sim_ns):
        with self.lock:
            if vehicle != self.config['carrier'] or package_id != self.config['package_id']:
                return {'status': 'rejected', 'reason': 'payload is not attached to this vehicle'}
            if self.release_request is not None:
                return {**self.release_request, 'repeat_release': False}
            if self.state['status'] != 'attached':
                return {'status': 'unavailable', 'reason': 'physical attachment has not been observed'}
            self.release_request = {'status': 'accepted', 'package_id': package_id,
                                   'accepted_sim_ns': sim_ns, 'released': False}
            if not self.publisher.publish(self.empty_type()):
                self.release_request['status'] = 'failed'
                self.release_request['reason'] = 'Gazebo transport rejected detach publication'
            with (self.directory / 'release-operations.jsonl').open('a') as output:
                output.write(json.dumps(self.release_request) + '\n')
            return dict(self.release_request)

    def status(self, vehicle, package_id):
        with self.lock:
            if vehicle != self.config['carrier'] or package_id != self.config['package_id']:
                return {'status': 'unavailable', 'reason': 'payload is not owned by this UAV'}
            return {**self.state, 'release_request': self.release_request}

    def drain(self, sim_ns):
        with self.lock:
            # Arrival can occur during a native step: use the confirmed boundary
            # as the observed upper bound, not the previous cached frontier.
            for event in self.events:
                event['observed_sim_ns'] = sim_ns
                if event['released'] and self.release_request:
                    event['release_interval_ns'] = [self.release_request['accepted_sim_ns'], sim_ns]
                    self.state['release_interval_ns'] = event['release_interval_ns']
                    self.state['observed_sim_ns'] = sim_ns
            with (self.directory / 'joint-state.jsonl').open('a') as output:
                for event in self.events:
                    output.write(json.dumps(event) + '\n')
            events, receipts = self.events, self.received
            self.events, self.received = [], []
            return events, receipts

    async def stop(self):
        self.publisher = None  # Gazebo owns subscriptions and closes them once.
