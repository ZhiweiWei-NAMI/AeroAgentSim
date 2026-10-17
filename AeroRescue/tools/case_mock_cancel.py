#!/usr/bin/env python3
"""C05 component diagnostic using the real coordinator, bridge and file IPC."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import queue
import threading
import time
import traceback

import yaml

from aerorescue.companion.coordinator import Coordinator
from aerorescue.companion.flight import FlightClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).parents[1] / 'scenarios/rooftop-flood-mock.yaml')
    args = parser.parse_args(); root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    scene = yaml.safe_load(args.scenario.read_text()); run_id = root.name
    scene['max_real_time_factor'] = 5
    scene['vehicles']['u2']['speed_m_s'] = 3.0
    scene['mock_backend'].update(expected_clients=['u2'], wind_enu_m_s=[0, 0, 0])
    (root / 'private-scene.yaml').write_text(yaml.safe_dump(scene))
    replay_requests = queue.Queue(); replay_replies = []

    class DiagnosticCoordinator(Coordinator):
        def collect(self, now_ns):
            super().collect(now_ns)
            # Controlled delayed RPC replay, dispatched only by the existing
            # world owner at its paused frontier. Not exposed to any agent.
            try: payload = replay_requests.get_nowait()
            except queue.Empty: return
            response = self.flight.client.request('command', payload)
            replay_replies.append({'sim_ns': now_ns, 'request': payload, 'response': response})
            self.log('mock-delayed-command-replay.jsonl', replay_replies[-1])

    coordinator = DiagnosticCoordinator(scene, run_id, root / 'coordinator', root / 'ipc',
                                        backend='mock', until_ns=150_000_000_000)
    errors = []
    def run():
        try: coordinator.run()
        except Exception as error: errors.append(repr(error)); traceback.print_exc()
    thread = threading.Thread(target=run); thread.start()
    try:
        while not (root / 'ipc/ready.json').exists():
            if errors: raise RuntimeError(errors)
            time.sleep(.01)
        flight = FlightClient('file://' + str(root / 'ipc/u2/agent.sock'), run_id)
        centre = FlightClient('file://' + str(root / 'ipc/centre/agent.sock'), run_id)
        flight.state()
        def resolve(client, operation, **params):
            response = client.request(operation, **params)
            if response['status'] == 'pending':
                request_id = response['request_id']
                while True:
                    response = client.request('result', pending_request_id=request_id)
                    if response['status'] != 'pending': break
                    time.sleep(.005)
            return response
        while not (messages := flight.request('radio_inbox')['messages']): time.sleep(.01)
        task = messages[0]
        flight.accept_task(task['message_id'], task['task_id'], task['task_revision'])
        resolve(flight, 'radio_inbox_ack', message_id=task['message_id'])
        # Hold the bridge scheduling boundary so the queued variant cannot
        # accidentally become a running variant between two request payloads.
        with coordinator.bridge.lock:
            queued = coordinator.bridge.handle('u2', {'run_id': run_id, 'op': 'submit',
                'action': 'takeoff', 'params': {'altitude_m': 20},
                'task_id': task['task_id'], 'task_revision': task['task_revision']})
            queued_cancel = coordinator.bridge.handle('u2', {'run_id': run_id, 'op': 'cancel',
                'operation_id': queued['operation_id']})
        assert queued_cancel['status'] == 'canceled' and not queued_cancel['dispatched']
        def finish(record):
            while record['status'] not in ('succeeded', 'failed', 'canceled', 'rejected'):
                record = flight.poll(record['operation_id']); time.sleep(.005)
            return record
        for action, params in [('arm', {}), ('takeoff', {'altitude_m': 20})]:
            result = finish(flight.submit(action, params, task['task_id'], task['task_revision']))
            assert result['status'] == 'succeeded', result
        running = flight.submit('goto', {'position_enu_m': [60, 0, 20.2], 'yaw_deg': 90}, task['task_id'], task['task_revision'])
        while True:
            initial = flight.state(); velocity = initial['vehicle_estimate']['velocity_enu_m_s']
            speed = math.hypot(*velocity[:2])
            if 2.75 <= speed <= 3.25 and abs(velocity[2]) < .01: break
            time.sleep(.005)
        requested = flight.cancel(running['operation_id'])
        try:
            flight.submit('goto', {'position_enu_m': [-5, 0, 20.2], 'yaw_deg': 90}, task['task_id'], task['task_revision'])
            raise AssertionError('new action accepted before old cancellation completed')
        except RuntimeError as error: busy_rejection = str(error)
        canceled = finish(requested); assert canceled['status'] == 'canceled', canceled
        new_task = {'event_id': task['event_id'], 'task_id': 'cancel-replacement', 'task_revision': 2}
        sent = resolve(centre, 'radio_send', destination='u2', kind='plan', data={'intent': 'calibration_return'},
            deadline_ns=120_000_000_000, **new_task)
        while True:
            messages = flight.request('radio_inbox')['messages']
            if messages and messages[0]['message_id'] == sent['message_id']: break
            time.sleep(.005)
        accepted = flight.accept_task(sent['message_id'], new_task['task_id'], 2)
        try:
            flight.submit('goto', {'position_enu_m': [60, 0, 20.2], 'yaw_deg': 90}, task['task_id'], task['task_revision'])
            raise AssertionError('late old-task model decision was accepted')
        except RuntimeError as error: stale_rejection = str(error)
        replacement = finish(flight.submit('goto', {'position_enu_m': [-5, 0, 20.2], 'yaw_deg': 90}, new_task['task_id'], 2))
        assert replacement['status'] == 'succeeded', replacement
        world_rows = [json.loads(line) for line in (root / 'coordinator/mock-world.jsonl').read_text().splitlines()]
        accept = next(row for row in world_rows if row['kind'] == 'cancel_accepted')
        old_command = next(row['request']['payload'] for row in
            (json.loads(line) for line in (root / 'coordinator/mock-rpc.jsonl').read_text().splitlines())
            if row['backend'] == 'flight' and row['request']['op'] == 'command'
            and row['request']['payload']['command_id'] == accept['command_id'])
        replay_requests.put(copy.deepcopy(old_command))
        while not replay_replies: time.sleep(.005)
        assert replay_replies[0]['response']['status'] == 'rejected', replay_replies
        observed = []; until = replay_replies[0]['sim_ns'] + 5_000_000_000
        while True:
            state = flight.state(); observed.append(state)
            position = state['vehicle_estimate']['position_enu_m']
            assert math.dist(position, [-5, 0, 20.2]) < .15, position
            if state['sim_ns'] >= until: break
            time.sleep(.01)
        queued_final = flight.poll(queued['operation_id'])
        assert queued_final['status'] == 'canceled' and not queued_final['dispatched'] and 'kernel_command_id' not in queued_final
        terminal = next(row for row in world_rows if row['kind'] == 'flight_update'
            and row['command_id'] == accept['command_id'] and row['status'] == 'canceled')
        elapsed = terminal['sim_ns'] - accept['sim_ns']; assert 0 <= elapsed <= 5_000_000_000
        report = {'execution_backend': 'mock', 'native': False, 'initial_horizontal_speed_m_s': speed,
            'initial_estimated_state': initial, 'declared_acceleration_m_s2': scene['mock_backend'].get('acceleration_m_s2', 3),
            'queued_cancel_scheduling': 'same request/reply handlers under one bridge boundary lock; no kernel dispatch',
            'queued_cancel': queued_final, 'running_cancel': canceled, 'cancel_accept': accept,
            'cancel_terminal': terminal, 'cancel_to_hold_ns': elapsed, 'busy_rejection': busy_rejection,
            'replacement_task_radio_send': sent, 'replacement_task_accept': accepted,
            'old_task_revision_rejection': stale_rejection, 'replacement_operation': replacement,
            'delayed_low_level_replay': replay_replies[0], 'after_replay_observations': observed,
            'native_remaining': ['PX4 dynamics', 'MAVSDK asynchronous callbacks and retries', 'native cancel quarantine failure path']}
        (root / 'report.json').write_text(json.dumps(report, indent=2)); print('DONE', root, elapsed, flush=True)
    except Exception as error:
        (root / 'failure.json').write_text(json.dumps({'error': repr(error), 'traceback': traceback.format_exc()}, indent=2)); raise
    finally:
        coordinator.stop_requested = True; thread.join(10)
        if errors: raise RuntimeError(errors)


if __name__ == '__main__': main()
