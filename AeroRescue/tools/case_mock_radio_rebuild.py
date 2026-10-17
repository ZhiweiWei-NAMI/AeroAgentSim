#!/usr/bin/env python3
"""Two H04 RadioTransport component rebuilds; world/kernel remain running.

Uses actual mock delivery/drop events and the normal file-IPC sender/receiver.
This is not a coordinator, container, process, or native-service restart.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
from pathlib import Path
import threading
import time
import traceback

import yaml
from PIL import Image

from aerorescue.companion.coordinator import Coordinator
from aerorescue.companion.flight import FlightClient
from aerorescue.companion.transport import RadioTransport


def run_branch(scene, root, variant, content):
    root.mkdir(parents=True, exist_ok=False); run_id = root.name
    scene = copy.deepcopy(scene)
    scene['max_real_time_factor'] = 5
    scene['mock_backend'].update(expected_clients=['u2'], startup_timeout_s=20)
    scene['native_radio_fault'] = {'case_id': 'C03', 'variant': variant}
    # No flight is needed: u2 and centre are on the same unobstructed side of
    # the original RF volume. Only the existing one-packet lifetime fault acts.
    (root / 'private-scene.yaml').write_text(yaml.safe_dump(scene))
    (root / 'sender-input.png').write_bytes(content)

    class RebuildCoordinator(Coordinator):
        selected_packet = None
        selected_drop_seen = False
        rebuilt = None

        def inject_radio_lifetime(self, params, now_ns):
            result = super().inject_radio_lifetime(params, now_ns)
            if result is not None: self.selected_packet = result['native_packet_id']
            return result

        def collect(self, now_ns):
            # Retain evidence before super consumes and clears these events.
            dropped = any(event['kind'] == 'drop' and event['payload']['packet_id'] == self.selected_packet
                          for event in self.radio.native_events)
            super().collect(now_ns)
            self.selected_drop_seen = self.selected_drop_seen or dropped
            mid = self.radio_fault_message
            if self.rebuilt or not mid or not self.selected_drop_seen: return
            state = self.transport.state; message = state['messages'][mid]
            incoming = state['incoming'].get(mid, {})
            committed = mid in state['committed']
            if variant == 'missing_fragment_nack_then_retry':
                ready = 0 < len(incoming) < len(message['chunk_hashes']) and not committed
            else:
                ready = committed and message['status'] != 'succeeded' and message.get('ack_received_ns') is None
            if not ready: return
            before = copy.deepcopy(state)
            previous, kernel, world = self.transport, self.simulation.kernel, self.mock_world
            self.transport = RadioTransport(previous.root, self.run_id, chunk_bytes=previous.chunk_bytes)
            after = copy.deepcopy(self.transport.state)
            assert self.transport is not previous and before == after
            assert kernel is self.simulation.kernel and world is self.mock_world
            self.rebuilt = {'scope': 'RadioTransport component reconstruction only', 'execution_backend': 'mock',
                'sim_ns': now_ns, 'kernel_frontier_ns': self.simulation.kernel.view().instant.ns,
                'world_sim_ns': self.mock_world.now, 'message_id': mid,
                'selected_native_packet_id': self.selected_packet, 'actual_drop_seen': self.selected_drop_seen,
                'received_chunk_indices': sorted(incoming, key=int), 'chunk_count': len(message['chunk_hashes']),
                'committed_before_rebuild': committed, 'ack_received_ns_before_rebuild': message.get('ack_received_ns'),
                'before_status': copy.deepcopy(previous.status(mid)), 'after_status': copy.deepcopy(self.transport.status(mid)),
                'persistent_state_equal': before == after, 'same_kernel_object': kernel is self.simulation.kernel,
                'same_world_object': world is self.mock_world}
            (root / 'transport-before.json').write_text(json.dumps(before, indent=2))
            (root / 'transport-after-construction.json').write_text(json.dumps(after, indent=2))
            (root / 'component-rebuild.json').write_text(json.dumps(self.rebuilt, indent=2))

    coordinator = RebuildCoordinator(scene, run_id, root / 'coordinator', root / 'ipc', backend='mock', until_ns=90_000_000_000)
    errors = []
    def run():
        try: coordinator.run()
        except Exception as error: errors.append(repr(error)); traceback.print_exc()
    thread = threading.Thread(target=run); thread.start()
    try:
        while not (root / 'ipc/ready.json').exists():
            if errors: raise RuntimeError(errors)
            time.sleep(.01)
        sender = FlightClient('file://' + str(root / 'ipc/u2/agent.sock'), run_id)
        receiver = FlightClient('file://' + str(root / 'ipc/centre/agent.sock'), run_id)
        sender.state()
        def resolve(client, operation, **params):
            reply = client.request(operation, **params)
            if reply['status'] == 'pending':
                request_id = reply['request_id']
                while True:
                    reply = client.request('result', pending_request_id=request_id)
                    if reply['status'] != 'pending': break
                    time.sleep(.005)
            return reply
        while not (messages := sender.request('radio_inbox')['messages']): time.sleep(.01)
        task = messages[0]
        sender.accept_task(task['message_id'], task['task_id'], task['task_revision'])
        sent = resolve(sender, 'radio_send', destination='centre', content_base64=base64.b64encode(content).decode(),
            metadata={'content_type': 'image/png'}, deadline_ns=80_000_000_000,
            **{key: task[key] for key in ('event_id', 'task_id', 'task_revision')})
        assert sent['status'] == 'accepted', sent
        while True:
            status = resolve(sender, 'radio_status', message_id=sent['message_id'])
            if status['status'] in ('succeeded', 'failed'): break
            time.sleep(.005)
        assert coordinator.rebuilt is not None, 'requested actual partial/commit boundary was never reached'
        assert status['status'] == 'succeeded', status
        inbox = receiver.request('radio_inbox')['messages']
        assert len(inbox) == 1 and inbox[0]['message_id'] == sent['message_id'], inbox
        received = base64.b64decode(inbox[0]['content_base64'])
        assert received == content
        (root / 'receiver-persisted.png').write_bytes(received)
        resolve(receiver, 'radio_inbox_ack', message_id=sent['message_id'])
        observation_until = sender.state()['sim_ns'] + 5_000_000_000
        while sender.state()['sim_ns'] < observation_until: time.sleep(.01)
        assert receiver.request('radio_inbox')['messages'] == [], 'duplicate local inbox delivery'
        records = [json.loads(line) for line in (root / 'coordinator/radio/packet-ledger.jsonl').read_text().splitlines()]
        commits = [row for row in records if row['event'] == 'receiver_file_commit' and row['message_id'] == sent['message_id']]
        assert len(commits) == 1, commits
        rebuild = coordinator.rebuilt
        assert rebuild['message_id'] == sent['message_id']
        assert rebuild['before_status'] == rebuild['after_status']
        assert status['deadline_ns'] == rebuild['before_status']['deadline_ns']
        assert status['attempt'] <= 2 and all(count <= 3 for count in status['chunk_sends'].values())
        rpc = [json.loads(line) for line in (root / 'coordinator/mock-rpc.jsonl').read_text().splitlines()]
        resets = {backend: sum(row['backend'] == backend and row['request']['op'] == 'reset' for row in rpc)
                  for backend in ('flight', 'radio')}
        assert resets == {'flight': 1, 'radio': 1}, resets
        before_message = json.loads((root / 'transport-before.json').read_text())['messages'][sent['message_id']]
        after_message = coordinator.transport.state['messages'][sent['message_id']]
        assert before_message['created_ns'] == after_message['created_ns']
        report = {'execution_backend': 'mock', 'native': False, 'variant': variant,
            'scope': 'transport component rebuild, not process/container restart', 'run_id': run_id,
            'rebuild': rebuild, 'original_message_id': sent['message_id'], 'final_status': status,
            'byte_equality': received == content, 'received_bytes': len(received),
            'receiver_commit_count': len(commits), 'receiver_commit': commits[0],
            'no_duplicate_local_inbox_after_ack_and_5s': True, 'reset_counts': resets,
            'message_created_ns_before': before_message['created_ns'], 'message_created_ns_after': after_message['created_ns'],
            'final_budget_within_original_limits': True, 'native_remaining': ['ns-3 service continuity', 'container/process crash recovery']}
        (root / 'report.json').write_text(json.dumps(report, indent=2)); print('DONE', variant, status, flush=True)
    except Exception as error:
        (root / 'failure.json').write_text(json.dumps({'error': repr(error), 'traceback': traceback.format_exc()}, indent=2)); raise
    finally:
        coordinator.stop_requested = True; thread.join(10)
        if errors: raise RuntimeError(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input-png', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).parents[1] / 'scenarios/rooftop-flood-mock.yaml')
    args = parser.parse_args(); root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    content = args.input_png.read_bytes()
    with Image.open(args.input_png) as image: image.verify()
    if len(content) <= 1024: raise ValueError('the missing-fragment branch needs a real multi-chunk PNG')
    scene = yaml.safe_load(args.scenario.read_text())
    for variant in ('missing_fragment_nack_then_retry', 'final_ack_lost'):
        run_branch(scene, root / variant, variant, content)


if __name__ == '__main__': main()
