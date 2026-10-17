"""Own the existing kernel and expose only per-UAV local services to companions."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import signal
import time
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

from .bridge import VehicleBridge
from .operations import TERMINAL
from .transport import RadioTransport


def adapter_document(scene: dict[str, Any], run_id: str, seed: int,
                     until_ns: int, root: Path | None = None) -> dict[str, Any]:
    """Reuse the authored PX4/native adapter setup, with no legacy rescue engines."""
    root = Path.cwd() if root is None else root
    template = root / 'scenarios/adapters/px4-flight.yaml'
    if not template.exists():
        template = Path(__file__).resolve().parents[3] / 'scenarios/adapters/px4-flight.yaml'
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
                 socket_root: Path, *, seed: int = 42, until_ns: int = 900_000_000_000) -> None:
        self.scene, self.run_id, self.data = scene, run_id, data
        data.mkdir(parents=True, exist_ok=True)
        self.until_ns = until_ns
        self.stop_requested = False
        self.bridge = VehicleBridge(run_id, socket_root, data, tuple(scene['vehicles']) + ('centre',))
        self.transport = RadioTransport(data / 'radio', run_id)
        self.document = adapter_document(scene, run_id, seed, until_ns)
        (data / 'resolved-scenario.yaml').write_text(yaml.safe_dump(self.document, sort_keys=False))
        self.simulation = Simulation(load_scenario(self.document), journal=Journal(data / 'kernel.jsonl'),
                                     catalog=register(EngineCatalog()))
        self.flight = next(e for e in self.simulation.engines if isinstance(e, PX4GazeboEngine))
        self.radio = next(e for e in self.simulation.engines if isinstance(e, NS3Engine))
        self.commands: dict[str, str] = {}
        self.bootstrap_messages: list[str] = []
        self.release_tasks: dict[str, dict[str, Any]] = {}

    def log(self, filename: str, value: dict[str, Any]) -> None:
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
                payload = {'vehicle': uid, 'action': 'read' if op == 'capture_read' else 'poll'}
                for name in ('after_sim_ns', 'frame_id', 'kind', 'offset', 'max_bytes'):
                    if name in request:
                        payload[name] = request[name]
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
                for destination in ('centre', self.scene['payload']['carrier']):
                    self.transport.queue_message('drop-receiver-1', destination, 'receipt', receipt,
                        deadline_ns=self.until_ns, now_ns=now_ns, **task)
        for event in self.flight.native_events:
            self.log('native-flight-events.jsonl', event)
        self.flight.native_events.clear()
        for event in self.radio.native_events:
            self.log('native-radio-events.jsonl', event)
            self.transport.consume_native_event(event['kind'], event['payload'])
        self.radio.native_events.clear()
        for uid in self.bridge.uav_ids:
            for delivered in self.transport.pop_delivered(uid):
                message = dict(delivered)
                if 'content_path' in message:
                    content = Path(message.pop('content_path')).read_bytes()
                    message['content_base64'] = base64.b64encode(content).decode()
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

    def run(self) -> None:
        started = time.monotonic()
        try:
            self.bridge.start()
            self.simulation.start()
            self.collect(0)
            self.bootstrap()
            while not self.stop_requested:
                now_ns = self.simulation.kernel.view().instant.ns
                if now_ns >= self.until_ns:
                    break
                for request in self.bridge.drain_requests():
                    self.handle(request, now_ns)
                self.transport.tick(now_ns)
                for params in self.transport.pop_commands():
                    self.simulation.kernel.submit(CommandRequest('adapters.ns3.send', 'radio',
                        Instant(now_ns + 1), params))
                target = min(now_ns + 200_000_000, self.until_ns)
                self.simulation.run_until(target)
                self.collect(target)
                # Yield CPU for independent companions; no model call blocks the
                # sole native connection. This is not a real-time performance claim.
                time.sleep(0.001)
        finally:
            elapsed = time.monotonic() - started
            sim_ns = self.simulation.kernel.view().instant.ns
            (self.data / 'timing.json').write_text(json.dumps({'sim_elapsed_s': sim_ns / 1e9,
                'wall_elapsed_s': elapsed, 'real_time_factor': sim_ns / 1e9 / max(elapsed, 1e-9)}))
            try:
                self.simulation.close()
            finally:
                self.bridge.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--run-id', default=os.getenv('RUN_ID'), required=os.getenv('RUN_ID') is None)
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--socket-root', type=Path, default=Path('/run/aas'))
    parser.add_argument('--until-ns', type=int, default=900_000_000_000)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    scene = yaml.safe_load(args.scenario.read_text())
    owner = Coordinator(scene, args.run_id, args.data, args.socket_root,
                        seed=args.seed, until_ns=args.until_ns)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(owner, 'stop_requested', True))
    owner.run()


if __name__ == '__main__':
    main()
