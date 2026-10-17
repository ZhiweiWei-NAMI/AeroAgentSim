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
    model.append(ET.fromstring(detachable_plugin(package_id)))
    ET.ElementTree(sdf).write(wrapper, encoding='utf-8', xml_declaration=True)
    include.find('uri').text = str(wrapper)
    offset = config.get('attachment_offset_enu_m', [0, 0, -0.35])
    package_pose = [carrier.spawn[i] + offset[i] for i in range(3)] + [0, 0, 0]
    world.append(ET.fromstring(payload_sdf(package_id, ' '.join(map(str, package_pose)))).find('model'))
    receiver_pose = ' '.join(map(str, config['receiver_pose']))
    size = ' '.join(map(str, config.get('receiver_size', [10, 10, 0.1])))
    world.append(ET.fromstring(receiver_sdf(package_id, receiver_pose, size)).find('model'))
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
            'drop-receiver-1::surface::receiver_collision', '/rescue/receiver/contacts', epoch,
            max_gap_ns=physics_step_ns)
        self.publisher = None

    async def start(self):
        from gz.msgs10.contacts_pb2 import Contacts
        from gz.msgs10.empty_pb2 import Empty
        from gz.msgs10.stringmsg_pb2 import StringMsg
        self.empty_type = Empty
        package_id = self.config['package_id']
        await self.gazebo.subscribe(StringMsg, f'/rescue/{package_id}/state', self.on_state)
        await self.gazebo.subscribe(Contacts, '/rescue/receiver/contacts', self.on_contacts)
        self.publisher = self.gazebo.node.advertise(f'/rescue/{package_id}/detach', Empty)
        if not self.publisher:
            raise RuntimeError('could not advertise payload release topic')

    def on_state(self, message):
        with self.lock:
            if message.data not in {'attached', 'detached'}:
                return
            # The upstream plugin publishes no source stamp. Retain observation
            # availability and an interval, never pretend this is source time.
            observed = max(0, self.logical_clock())
            self.state.update(status=message.data, released=message.data == 'detached',
                              observed_sim_ns=observed, received_wall_ns=time.time_ns())
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
