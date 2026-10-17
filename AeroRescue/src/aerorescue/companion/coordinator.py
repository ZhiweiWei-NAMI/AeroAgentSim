"""Own the existing kernel and expose only per-UAV local services to companions."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import math
import os
import signal
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from aerokernel import CommandRequest, Instant, Journal
from aerokernel.values import thaw

from aeroagentsim.adapters import register
from aeroagentsim.adapters.ns3 import NS3Engine
from aeroagentsim.adapters.px4_gazebo import PX4GazeboEngine
from aeroagentsim.adapters.schemas import command_descriptors, event_descriptors
from aeroagentsim.platform.plugins import EngineCatalog
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario

from aerorescue.resources import resource_path

from .bridge import VehicleBridge
from .operations import TERMINAL
from .transport import RadioTransport


def adapter_document(scene: dict[str, Any], run_id: str, seed: int,
                     until_ns: int, root: Path | None = None) -> dict[str, Any]:
    """Reuse the authored PX4/native adapter setup, with no legacy rescue engines."""
    template = (root / 'scenarios/adapters/px4-flight.yaml') if root is not None else resource_path('adapters', 'px4-flight.yaml')
    document = yaml.safe_load(template.read_text())
    document['id'] = run_id
    document['registry']['snapshot'] = str(template.with_name('authored-registry.json'))
    vehicles = scene['vehicles']
    document['entities'] = [{'id': uid, 'type': 'e1:Aircraft', 'facts': {}} for uid in vehicles]
    config = document['engines']['flight']['config']
    simulator = scene.get('simulator', {})
    config.update(host=simulator.get('host', 'simulator'), port=simulator.get('port', 9000),
                  step_ns=200_000_000, control_step_ns=20_000_000,
                  vehicles={uid: uid for uid in vehicles})
    warmup = simulator.get('warmup_ns', 10_000_000_000)
    config['reset'] = {'world': simulator.get('world', 'default'), 'warmup': warmup,
        'vehicles': [{'id': uid, 'model': row.get('model', 'x500'), 'spawn': row['spawn']}
                     for uid, row in vehicles.items()]}
    if simulator.get('rgbd_camera') is not None:
        config['reset']['rgbd_camera'] = simulator['rgbd_camera']
    if scene.get('payload'):
        config['reset']['payload'] = scene['payload']
    if simulator.get('altitude_datum') is not None:
        config['reset']['altitude_datum'] = simulator['altitude_datum']
    config['fields']['estimate'] = 'e1.px4.estimate'
    document['registry']['fields'].append({'id': 'e1.px4.estimate', 'type': 'e1:Aircraft',
        'schema': {'type': 'record', 'members': {}, 'required': [], 'extra': True},
        'metadata': {'role': 'observation', 'time_semantics': 'PX4 source clocks contained in record'}})
    document['bindings']['rules'][0]['fields'].append('e1.px4.estimate')
    document['registry']['messages'] = []
    for backend in ('px4_gazebo', 'ns3'):
        for descriptor in command_descriptors(backend) + event_descriptors(backend):
            row = {'id': descriptor.id, 'kind': descriptor.kind, 'schema': thaw(descriptor.schema)}
            if descriptor.result_schema is not None:
                row['result_schema'] = thaw(descriptor.result_schema)
            if descriptor.cancel_support:
                row['cancel_support'] = True
            document['registry']['messages'].append(row)
    radio = scene.get('radio', {})
    nodes_by_id = {node['id']: copy.deepcopy(node) for node in radio.get('nodes', [])}
    for uid, row in vehicles.items():
        nodes_by_id[uid] = {'id': uid, 'position_enu': row['spawn'][:3]}
    nodes_by_id.setdefault('centre', {'id': 'centre', 'position_enu': [0, 0, 0]})
    reset = copy.deepcopy(radio.get('reset', {}))
    reset['nodes'] = list(nodes_by_id.values())
    for key in ('channel', 'propagation', 'scene_volumes'):
        if key in radio:
            reset[key] = radio[key]
    document['engines']['radio'] = {'plugin': 'ns3', 'config': {
        'host': radio.get('host', 'radio'), 'port': radio.get('port', 9000),
        'step_ns': 200_000_000, 'clock_id': 'ns3.simulation', 'mapping_id': 'ns3.elapsed/v1',
        'event_topic': 'native-events', 'consumer_lag_ns': 200_000_000,
        'mobility': [{'entity': uid, 'field': 'e1.px4.position', 'node': uid} for uid in vehicles],
        'reset': reset}}
    for mapping in document['clock_mappings']:
        if mapping['clock_id'] == 'gazebo.simulation':
            mapping['offset_ns'] = -warmup
    document['clock_mappings'].append({'clock_id': 'ns3.simulation', 'mapping_id': 'ns3.elapsed/v1'})
    document['run'].update(seed=seed, until_ns=until_ns, advance_ns=200_000_000)
    return document


class Coordinator:
    def __init__(self, scene: dict[str, Any], run_id: str, data: Path,
                 socket_root: Path, *, seed: int = 42, until_ns: int = 900_000_000_000, backend: str = 'native') -> None:
        if backend not in ('native', 'mock'):
            raise ValueError('backend must be native or mock')
        if scene.get('execution_backend', backend) != backend:
            raise ValueError('scenario execution_backend differs from explicit backend')
        self.backend = backend
        self.scene, self.run_id, self.data = scene, run_id, data
        data.mkdir(parents=True, exist_ok=True)
        self.until_ns = until_ns
        self.max_real_time_factor = float(scene.get('max_real_time_factor', 1.0))
        if not math.isfinite(self.max_real_time_factor) or self.max_real_time_factor <= 0:
            raise ValueError('max_real_time_factor must be positive')
        self.stop_requested = False
        self.bridge = VehicleBridge(run_id, socket_root, data, tuple(scene['vehicles']) + ('centre',),
            transport='file' if backend == 'mock' else 'unix')
        self.transport = RadioTransport(data / 'radio', run_id)
        self.document = adapter_document(scene, run_id, seed, until_ns)
        (data / 'resolved-scenario.yaml').write_text(yaml.safe_dump(self.document, sort_keys=False))
        self.simulation = Simulation(load_scenario(self.document), journal=Journal(data / 'kernel.jsonl'),
                                     catalog=register(EngineCatalog()))
        self.flight = next(e for e in self.simulation.engines if isinstance(e, PX4GazeboEngine))
        self.radio = next(e for e in self.simulation.engines if isinstance(e, NS3Engine))
        if backend == 'mock':
            from .mock_backend import MockWorld, MockRpcClient
            self.mock_world = MockWorld(scene, run_id, data, seed)
            self.flight.client = MockRpcClient(self.mock_world, 'flight')
            self.radio.client = MockRpcClient(self.mock_world, 'radio')
        self.commands: dict[str, str] = {}
        self.bootstrap_messages: list[str] = []
        self.release_tasks: dict[str, dict[str, Any]] = {}
        self.receiver_transfers: dict[str, dict[str, Any]] = {}
        self.receiver_id = scene.get('payload', {}).get('receiver_id', 'drop-receiver-1')
        self.receiver_routes: dict[str, dict[str, Any]] = {}
        self.radio_fault_message: str | None = None
        self.radio_fault_fired = False
        self.mock_radio_outage: dict[str, Any] | None = None

    def inject_mock_radio_lifetime(self, params: dict[str, Any], now_ns: int) -> dict[str, Any] | None:
        """The three explicit C03 mock faults change packets, never receipts."""
        fault = self.scene.get('mock_backend', {}).get('radio_fault')
        if self.backend != 'mock' or not fault:
            return None
        variant = fault['variant']
        if fault['case_id'] != 'C03' or variant not in {
                'path_lost_then_second_attempt', 'no_valid_path', 'lost_assignment_before_acceptance'}:
            raise ValueError('unsupported explicit mock radio fault')
        packet_id = params['packet_id'].rsplit('-h', 1)[0]
        packet = self.transport.state['packets'][packet_id]
        message = self.transport.state['messages'][packet['message_id']]
        if variant == 'lost_assignment_before_acceptance':
            eligible = message['kind'] == 'plan' and message['source'] == 'centre'
        else:
            eligible = message['kind'] == 'file'
            if variant == 'path_lost_then_second_attempt':
                eligible = eligible and len(message['chunk_hashes']) > 1
        if not eligible:
            return None
        if self.radio_fault_message is None:
            self.radio_fault_message = packet['message_id']
        if packet['message_id'] != self.radio_fault_message:
            return None
        route, header = packet['route'], packet['header']
        if packet['hop'] != len(route) - 2 or packet['native_id'] != params['packet_id']:
            return None
        if variant == 'path_lost_then_second_attempt':
            if self.mock_radio_outage is None:
                if packet['kind'] != 'chunk' or header['attempt'] != 1 or header['chunk_index'] != 1:
                    return None
                duration = int(fault.get('outage_duration_ns', 20_000_000_000))
                if duration <= 0:
                    raise ValueError('mock outage duration must be positive')
                self.mock_radio_outage = {'started_ns': now_ns, 'ends_ns': now_ns + duration,
                                          'route': list(route)}
            outage = self.mock_radio_outage
            selected = (now_ns < outage['ends_ns'] and
                        route in (outage['route'], list(reversed(outage['route']))))
        elif variant == 'no_valid_path':
            selected = True  # Every proposed path of this file fails its actual probe.
        else:
            selected = packet['kind'] == 'chunk'  # Ready path is not a received plan.
        if not selected:
            return None
        original = params['lifetime_ns']
        params['lifetime_ns'] = 1
        self.radio_fault_fired = True
        return {'run_id': self.run_id, 'stage': 'kernel_queued', 'case_id': 'C03',
            'variant': variant, 'sim_ns': now_ns, 'native_packet_id': params['packet_id'],
            'packet_id': packet_id, 'message_id': packet['message_id'],
            **{key: message[key] for key in ('event_id', 'task_id', 'task_revision')},
            'message_kind': message['kind'], 'packet_kind': packet['kind'],
            'message_source': message['source'], 'message_destination': message['destination'],
            'attempt': header['attempt'], 'chunk_index': header.get('chunk_index'),
            'chunk_send_count': message['chunk_sends'].get(str(header.get('chunk_index')), 0),
            'route': list(route), 'hop': packet['hop'], 'src': params['src'], 'dst': params['dst'],
            'original_lifetime_ns': original, 'lifetime_ns': 1,
            'outage': copy.deepcopy(self.mock_radio_outage)}

    def inject_radio_lifetime(self, params: dict[str, Any], now_ns: int) -> dict[str, Any] | None:
        """Select the four authored radio diagnostics; ns-3 decides the outcome."""
        if self.backend == 'mock' and self.scene.get('mock_backend', {}).get('radio_fault'):
            return self.inject_mock_radio_lifetime(params, now_ns)
        fault = self.scene.get('native_radio_fault')
        if not fault:
            return None
        case, variant = fault['case_id'], fault['variant']
        packet_id = params['packet_id'].rsplit('-h', 1)[0]
        packet = self.transport.state['packets'][packet_id]
        message = self.transport.state['messages'][packet['message_id']]
        if case == 'C03':
            if message['kind'] != 'file':
                return None
            if variant == 'missing_fragment_nack_then_retry' and len(message['chunk_hashes']) < 2:
                return None  # There must be an actual required chunk 1 to lose.
        elif case == 'C07':
            if (message['kind'] != 'receipt' or message['source'] != 'drop-receiver-1'
                    or message['destination'] != self.scene['payload']['carrier']):
                return None
        else:
            raise ValueError('native_radio_fault supports only authored C03/C07 diagnostics')
        # Keep one actual business message as the fault target across all its
        # hops, retries and final acknowledgements. Startup notices never match.
        if self.radio_fault_message is None:
            self.radio_fault_message = packet['message_id']
        if packet['message_id'] != self.radio_fault_message:
            return None
        route, hop, header = packet['route'], packet['hop'], packet['header']
        if hop != len(route) - 2 or packet['native_id'] != params['packet_id']:
            return None
        kind = packet['kind']
        chunk_index = header.get('chunk_index')
        chunk_sends = message['chunk_sends'].get(str(chunk_index), 0)
        if (case, variant) == ('C03', 'missing_fragment_nack_then_retry'):
            selected = (not self.radio_fault_fired and kind == 'chunk' and chunk_index == 1
                        and header['attempt'] == 1 and chunk_sends == 1)
        elif (case, variant) in {('C03', 'final_ack_lost'), ('C07', 'confirmation_final_ack_lost')}:
            selected = not self.radio_fault_fired and kind == 'final_ACK'
        elif (case, variant) == ('C07', 'receiver_receipt_lost'):
            # Carrier-only loss: the centre's independent receipt is untouched.
            selected = kind == 'chunk'
        else:
            raise ValueError('unsupported native radio diagnostic variant')
        if not selected:
            return None
        original = params['lifetime_ns']
        params['lifetime_ns'] = 1
        self.radio_fault_fired = True
        return {'run_id': self.run_id, 'stage': 'kernel_queued', 'case_id': case,
            'variant': variant, 'sim_ns': now_ns, 'native_packet_id': params['packet_id'],
            'packet_id': packet_id, 'message_id': packet['message_id'],
            **{key: message[key] for key in ('event_id', 'task_id', 'task_revision')},
            'message_kind': message['kind'], 'packet_kind': kind,
            'message_source': message['source'], 'message_destination': message['destination'],
            'attempt': header['attempt'], 'chunk_index': chunk_index, 'chunk_send_count': chunk_sends,
            'route': list(route), 'hop': hop, 'src': params['src'], 'dst': params['dst'],
            'original_lifetime_ns': original, 'lifetime_ns': 1}

    def log(self, filename: str, value: dict[str, Any]) -> None:
        if self.backend == 'mock':
            filename = filename.replace('native-', 'mock-')
            value = {**value, 'execution_backend': 'mock'}
        with (self.data / filename).open('a') as output:
            output.write(json.dumps(value, allow_nan=False) + '\n')

    def bootstrap(self) -> None:
        """Initial public tasks use the same lossy radio as later plans."""
        for uid, row in self.scene['vehicles'].items():
            task = copy.deepcopy(row.get('initial_task', {}))
            task_id = task.get('task_id', f'initial-{uid}')
            revision = task.get('task_revision', 1)
            data = {'task': task, 'capabilities': row.get('capabilities', []),
                    'public_map': self.scene.get('public_map', {}),
                    'incident': self.scene.get('incident', {})}
            mid = self.transport.queue_message('centre', uid, 'notice', data,
                event_id=self.scene.get('incident', {}).get('event_id', 'initial'),
                task_id=task_id, task_revision=revision, deadline_ns=self.until_ns, now_ns=0)
            self.bootstrap_messages.append(mid)
        incident = self.scene.get('incident', {})
        self.bridge.state_cache['centre'] = {'status': 'available', 'uav_id': 'centre', 'sim_ns': 0,
            'notice_submissions': [{'event_id': incident.get('event_id', 'initial'),
                'submitted_ns': 0, 'deadline_ns': self.until_ns,
                'demand_source': incident.get('demand_source')}],
            'public_map': self.scene.get('public_map', {})}

    def handle(self, request: dict[str, Any], now_ns: int) -> None:
        op, uid = request['op'], request['uav_id']
        try:
            if op == 'submit':
                record = self.bridge.operations.get(request['operation_id'], uid)
                if record['status'] == 'cancel_requested':
                    self.bridge.operations.update(request['operation_id'], status='canceled',
                        completed_sim_ns=now_ns, reason='canceled before kernel submission')
                    return
                if record['status'] in TERMINAL:
                    return
                params = dict(request['params'])
                if request['action'] == 'goto':
                    params['position_enu'] = params.pop('position_enu_m')
                command_id = self.simulation.kernel.submit(CommandRequest(
                    'adapters.px4_gazebo.' + request['action'], 'flight', Instant(now_ns + 1),
                    {**params, 'entity': uid}))
                self.commands[request['operation_id']] = command_id
                self.bridge.operations.update(request['operation_id'], kernel_command_id=command_id)
            elif op == 'cancel':
                operation_id = request['operation_id']
                record = self.bridge.operations.get(operation_id, uid)
                if record['status'] in TERMINAL:
                    return  # Completion may have won while this request was queued.
                command_id = self.commands.get(operation_id)
                if command_id is None:
                    self.bridge.operations.update(operation_id, status='reconcile_required',
                        reason='cancellation has no known live kernel command')
                    return
                result = self.simulation.kernel.cancel(command_id)
                if not result.queued:
                    action = self.simulation.kernel.view().action(command_id)
                    status = {'executing': 'running', 'canceling': 'cancel_requested'}.get(action.status, action.status)
                    self.bridge.operations.update(operation_id, status=status,
                        cancel_rejected=True, cancel_reason='kernel rejected cancellation; original action state retained')
            elif op == 'radio_send':
                common = {'event_id': request['event_id'], 'task_id': request['task_id'],
                    'task_revision': request['task_revision'], 'deadline_ns': min(request['deadline_ns'], self.until_ns),
                    'now_ns': now_ns, 'route': request.get('route')}
                if request.get('content_base64') is not None:
                    mid = self.transport.queue_file(uid, request['destination'],
                        base64.b64decode(request['content_base64'], validate=True),
                        metadata=request.get('metadata'), **common)
                else:
                    mid = self.transport.queue_message(uid, request['destination'],
                        request['kind'], request['data'], **common)
                self.bridge.reply(request, {'status': 'accepted', 'message_id': mid, 'submitted_ns': now_ns})
            elif op == 'radio_route':
                if self.transport.state['messages'][request['message_id']]['source'] != uid:
                    raise PermissionError('message belongs to another source')
                self.transport.update_route(request['message_id'], request['route'], now_ns)
                self.bridge.reply(request, {'status': 'succeeded', 'message_id': request['message_id']})
            elif op == 'radio_status':
                status = self.transport.status(request['message_id'])
                if self.transport.state['messages'][request['message_id']]['source'] != uid:
                    raise PermissionError('message belongs to another source')
                self.bridge.reply(request, status)
            elif op == 'radio_inbox_ack':
                if request['message_id'] not in self.bridge.received_messages[uid]:
                    raise PermissionError('message has not been delivered to this endpoint')
                self.transport.acknowledge_local_delivery(uid, request['message_id'])
                self.bridge.acknowledge_inbox(uid, request['message_id'])
                self.bridge.reply(request, {'status': 'accepted'})
            elif op == 'probe_route':
                route = request.get('route') or [uid, request['destination']]
                if route[0] != uid:
                    raise PermissionError('probe source is fixed by endpoint')
                mid = self.transport.start_probe(uid, request['destination'], route=route,
                    now_ns=now_ns, deadline_ns=min(now_ns + 10_000_000_000, self.until_ns))
                self.bridge.reply(request, {'status': 'accepted', 'message_id': mid, 'submitted_ns': now_ns})
            elif op in ('payload_release', 'payload_status'):
                package_id = request['package_id']
                if op == 'payload_release':
                    if (request['task_id'], request['task_revision']) != self.bridge.tasks.get(uid):
                        raise ValueError('release task is stale or not accepted')
                    if self.bridge.operations.active(uid):
                        raise ValueError('finish the flight operation before release')
                    completed = [r for r in self.bridge.operations.records.values()
                        if r['uav_id'] == uid and r['task_id'] == request['task_id']
                        and r['task_revision'] == request['task_revision']]
                    if not completed or completed[-1]['action'] != 'hold' or completed[-1]['status'] != 'succeeded':
                        raise ValueError('a measured hold for the current task is required before release')
                result = self.flight.client.request('payload', {'vehicle': uid,
                    'package_id': package_id, 'action': 'release' if op == 'payload_release' else 'status'})
                if op == 'payload_release' and result['status'] == 'accepted':
                    self.release_tasks[package_id] = {key: request[key]
                        for key in ('event_id', 'task_id', 'task_revision')}
                self.bridge.reply(request, result)
            elif op in ('capture_request', 'capture_poll', 'capture_read'):
                if uid not in self.scene['vehicles']:
                    raise PermissionError('this endpoint has no onboard sensor')
                # Invalid agent input must not taint the sole native connection.
                payload = {'vehicle': uid, 'action': 'read' if op == 'capture_read' else 'poll'}
                if op == 'capture_read':
                    frame_id, kind = request.get('frame_id'), request.get('kind')
                    offset, size = request.get('offset', 0), request.get('max_bytes', 262144)
                    if not isinstance(frame_id, str) or not frame_id or kind not in ('rgb', 'depth'):
                        raise ValueError('capture_read requires frame_id and rgb/depth kind')
                    if type(offset) is not int or offset < 0 or type(size) is not int or not 1 <= size <= 262144:
                        raise ValueError('capture_read requires offset >= 0 and 1..262144 bytes')
                    payload.update(frame_id=frame_id, kind=kind, offset=offset, max_bytes=size)
                else:
                    after = request.get('after_sim_ns', -1)
                    if type(after) is not int or after < -1:
                        raise ValueError('after_sim_ns must be an integer >= -1')
                    payload['after_sim_ns'] = after
                self.bridge.reply(request, self.flight.client.request('capture', payload))
            else:
                self.bridge.reply(request, {'status': 'unavailable',
                    'reason': f'{op} is not connected to a native actuator yet'})
        except (ValueError, KeyError, PermissionError, RuntimeError) as error:
            if op in ('submit', 'cancel'):
                record = self.bridge.operations.get(request['operation_id'], uid)
                if record['status'] not in TERMINAL:
                    self.bridge.operations.update(request['operation_id'],
                        status='reconcile_required' if op == 'cancel' else 'failed', reason=str(error))
            else:
                self.bridge.reply(request, {'status': 'error', 'reason': str(error)})

    def collect(self, now_ns: int) -> None:
        for sample in self.flight.latest_native.get('telemetry', []):
            self.bridge.publish_state(sample['vehicle'], sample, now_ns)
            self.log('estimated-telemetry.jsonl', {'run_id': self.run_id, 'sim_ns': now_ns,
                'uav_id': sample['vehicle'], 'vehicle_estimate': sample.get('vehicle_estimate'),
                **{k: sample[k] for k in ('armed', 'flight_mode', 'landed_state') if k in sample}})
        if 'centre' in self.bridge.state_cache:
            self.bridge.state_cache['centre']['sim_ns'] = now_ns
        for event in self.flight.latest_native.get('payload_events', []):
            self.log('native-payload-events.jsonl', event)
        for receipt in self.flight.latest_native.get('receiver_receipts', []):
            self.log('receiver-contact-receipts.jsonl', receipt)
            task = self.release_tasks.get(receipt['package_id'])
            if task is not None:
                transfers = []
                for destination in ('centre', self.scene['payload']['carrier']):
                    # Re-draining one physical receipt must retain its existing
                    # transfer, including terminal failure, not create success
                    # or gain a fresh radio retry budget under a new identity.
                    message_id = 'receipt-' + uuid.uuid5(uuid.NAMESPACE_URL,
                        json.dumps([self.run_id, receipt['receipt_id'], destination])).hex
                    self.transport.queue_message(self.receiver_id, destination, 'receipt', receipt,
                        deadline_ns=self.until_ns, now_ns=now_ns, message_id=message_id,
                        route=self.receiver_route(task['event_id'], destination, now_ns), **task)
                    transfers.append({'destination': destination, 'message_id': message_id})
                self.receiver_transfers.setdefault(receipt['receipt_id'], {
                    **task, 'receipt_id': receipt['receipt_id'], 'package_id': receipt['package_id'],
                    'transfers': transfers})
        for event in self.flight.native_events:
            self.log('native-flight-events.jsonl', event)
        self.flight.native_events.clear()
        for event in self.radio.native_events:
            self.log('native-radio-events.jsonl', event)
            self.transport.consume_native_event(event['kind'], event['payload'])
        self.radio.native_events.clear()
        self.collect_receiver_routes(now_ns)
        self.confirm_receiver_transfers(now_ns)
        for uid in self.bridge.uav_ids:
            for delivered in self.transport.pop_delivered(uid):
                message = dict(delivered)
                if 'content_path' in message:
                    content = Path(message.pop('content_path')).read_bytes()
                    message['content_base64'] = base64.b64encode(content).decode()
                if self.backend == 'mock':
                    message['execution_backend'] = 'mock'
                self.bridge.deliver(uid, message)
        for operation_id, command_id in list(self.commands.items()):
            action = self.simulation.kernel.view().action(command_id)
            record = self.bridge.operations.records[operation_id]
            status = {'executing': 'running', 'canceling': 'cancel_requested'}.get(action.status, action.status)
            # Kernel submitted/accepted do not erase a pending cancel request.
            if record['status'] == 'cancel_requested' and status in ('submitted', 'accepted', 'running'):
                continue
            if record['status'] != status:
                self.bridge.operations.update(operation_id, status=status, observed_sim_ns=now_ns,
                    kernel_history=[thaw(item) for item in action.history])
            if status in TERMINAL:
                del self.commands[operation_id]

    def receiver_route(self, event_id: str, destination: str, now_ns: int) -> list[str]:
        """Select a candidate from the receiver's own radio-delivered knowledge."""
        ready = self.receiver_routes.get(event_id)
        if destination == 'centre' and ready and now_ns <= ready['valid_until_ns']:
            return [self.receiver_id, ready['relay'], destination]
        return [self.receiver_id, destination]

    def collect_receiver_routes(self, now_ns: int) -> None:
        """The receiver learns routes like an agent, never from global readiness."""
        for message in self.transport.pop_delivered(self.receiver_id):
            # Persist the local delivered envelope before acknowledging its inbox.
            self.log('receiver-radio-inbox.jsonl', message)
            data, relay = message.get('data', {}), message['source']
            proofs = data.get('probe_results', [])
            valid_until = data.get('valid_until_ns', -1)
            received_ns = message['received_ns']
            valid = (message['kind'] == 'report' and data.get('kind') == 'relay_ready'
                and data.get('service_peer') == self.receiver_id
                and 'relay' in self.scene['vehicles'].get(relay, {}).get('capabilities', [])
                and received_ns <= now_ns <= valid_until
                and {p.get('destination') for p in proofs} == {'centre', self.receiver_id}
                and all(p.get('source') == relay and p.get('status') == 'succeeded'
                    and p.get('probe_only') is True and p.get('ack_received_ns') is not None
                    and 0 <= now_ns - p['ack_received_ns'] <= 5_000_000_000
                    and p['ack_received_ns'] <= received_ns
                    and valid_until <= p['ack_received_ns'] + 5_000_000_000 for p in proofs))
            if valid:
                event_id = message['event_id']
                previous = self.receiver_routes.get(event_id)
                if previous is None or valid_until >= previous['valid_until_ns']:
                    self.receiver_routes[event_id] = {'relay': relay, 'valid_until_ns': valid_until,
                        'message_id': message['message_id']}
                    # Only this receiver's own outbox may be rerouted. Keep the
                    # original IDs, deadlines and retry counters; update_route
                    # starts an actual round-trip probe over the candidate path.
                    for receipt in self.receiver_transfers.values():
                        if receipt['event_id'] != event_id:
                            continue
                        mids = [t['message_id'] for t in receipt['transfers']]
                        if receipt.get('confirmation_message_id'):
                            mids.append(receipt['confirmation_message_id'])
                        for mid in mids:
                            status = self.transport.status(mid)
                            route = self.receiver_route(event_id, status['destination'], now_ns)
                            if (status['destination'] == 'centre' and status['source'] == self.receiver_id
                                    and status['status'] not in ('succeeded', 'failed') and status['route'] != route):
                                self.transport.update_route(mid, route, now_ns)
                                self.log('receiver-radio-routes.jsonl', {'message_id': mid, 'route': route,
                                    'sim_ns': now_ns, 'learned_from_message_id': message['message_id']})
            self.transport.acknowledge_local_delivery(self.receiver_id, message['message_id'])

    def confirm_receiver_transfers(self, now_ns: int) -> None:
        """The physical receiver reports only its own completed radio transfers."""
        for receipt in self.receiver_transfers.values():
            if receipt.get('confirmation_message_id') or now_ns >= self.until_ns:
                continue
            transfers = []
            for transfer in receipt['transfers']:
                status = self.transport.status(transfer['message_id'])
                if status['status'] != 'succeeded' or status.get('ack_received_ns') is None:
                    break
                transfers.append({**transfer, 'ack_received_ns': status['ack_received_ns']})
            if len(transfers) != len(receipt['transfers']):
                continue
            data = {'kind': 'delivery_confirmation', 'receipt_id': receipt['receipt_id'],
                'package_id': receipt['package_id'], 'receipt_transfers': transfers}
            task = {key: receipt[key] for key in ('event_id', 'task_id', 'task_revision')}
            message_id = 'confirmation-' + uuid.uuid5(uuid.NAMESPACE_URL,
                json.dumps([self.run_id, receipt['receipt_id'], 'delivery-confirmation'])).hex
            self.transport.queue_message(self.receiver_id, 'centre', 'report', data,
                deadline_ns=self.until_ns, now_ns=now_ns, message_id=message_id,
                route=self.receiver_route(task['event_id'], 'centre', now_ns), **task)
            receipt['confirmation_message_id'] = message_id
            # This is a queued notification, not a claim that centre received it.
            # Its own final ACK does not generate another confirmation.
            self.log('receiver-radio-confirmations.jsonl', {'run_id': self.run_id,
                'source': self.receiver_id, 'destination': 'centre', 'stage': 'queued',
                'submitted_ns': now_ns, 'message_id': message_id, **task, **data})

    def wait_for_mock_clients(self) -> None:
        options = self.scene.get('mock_backend', {})
        expected = set(options.get('expected_clients', self.bridge.uav_ids))
        if not expected <= set(self.bridge.uav_ids):
            raise ValueError('mock expected_clients includes unknown endpoint')
        ready = {'run_id': self.run_id, 'execution_backend': 'mock', 'sim_ns': 0,
            'endpoints': {uid: 'file://' + str((self.bridge.socket_root / uid / 'agent.sock').resolve())
                          for uid in self.bridge.uav_ids}}
        (self.bridge.socket_root / 'ready.json').write_text(json.dumps(ready))
        deadline = time.monotonic() + float(options.get('startup_timeout_s', 120))
        while not expected <= self.bridge.connected_clients:
            if self.stop_requested:
                raise RuntimeError('mock startup stopped')
            if time.monotonic() >= deadline:
                raise TimeoutError('mock clients did not request initial state: ' + str(sorted(expected-self.bridge.connected_clients)))
            self.bridge.poll_local_requests()
            time.sleep(.005)

    def run(self) -> None:
        started = time.monotonic()
        episode_started = None
        try:
            self.bridge.start()
            self.simulation.start()
            self.collect(0)
            if self.backend == 'mock':
                self.wait_for_mock_clients()
            self.bootstrap()
            episode_started = time.monotonic()
            while not self.stop_requested:
                now_ns = self.simulation.kernel.view().instant.ns
                if now_ns >= self.until_ns:
                    break
                self.bridge.poll_local_requests()
                for request in self.bridge.drain_requests():
                    self.handle(request, now_ns)
                self.transport.tick(now_ns)
                for params in self.transport.pop_commands():
                    injection = self.inject_radio_lifetime(params, now_ns)
                    command_id = self.simulation.kernel.submit(CommandRequest('adapters.ns3.send', 'radio',
                        Instant(now_ns + 1), params))
                    if injection is not None:
                        self.log('native-fault-injections.jsonl', {**injection, 'kernel_command_id': command_id})
                target = min(now_ns + 200_000_000, self.until_ns)
                self.simulation.run_until(target)
                self.collect(target)
                # Cap simulation speed so independent agent work has its stated
                # deadline budget. A slow backend incurs no additional delay.
                remaining = target / 1e9 / self.max_real_time_factor - (time.monotonic() - episode_started)
                if remaining > 0:
                    if self.backend == 'native':
                        time.sleep(remaining)
                    else:
                        deadline = time.monotonic() + remaining
                        while not self.stop_requested and time.monotonic() < deadline:
                            self.bridge.poll_local_requests()
                            time.sleep(min(.005, max(0., deadline-time.monotonic())))
        finally:
            elapsed = time.monotonic() - started
            sim_ns = self.simulation.kernel.view().instant.ns
            episode_elapsed = time.monotonic() - episode_started if episode_started is not None else 0.0
            (self.data / 'timing.json').write_text(json.dumps({'sim_elapsed_s': sim_ns / 1e9,
                'wall_elapsed_s': elapsed, 'episode_wall_elapsed_s': episode_elapsed,
                'max_real_time_factor': self.max_real_time_factor,
                'real_time_factor': sim_ns / 1e9 / max(episode_elapsed, 1e-9), 'execution_backend': self.backend}))
            try:
                self.simulation.close()
            finally:
                self.bridge.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('native', 'mock'), default='native')
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--run-id', default=os.getenv('RUN_ID'), required=os.getenv('RUN_ID') is None)
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--socket-root', type=Path, default=Path('/run/aas'))
    parser.add_argument('--until-ns', type=int, default=900_000_000_000)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    scene = yaml.safe_load(args.scenario.read_text())
    owner = Coordinator(scene, args.run_id, args.data, args.socket_root,
                        seed=args.seed, until_ns=args.until_ns, backend=args.backend)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(owner, 'stop_requested', True))
    owner.run()


if __name__ == '__main__':
    main()
