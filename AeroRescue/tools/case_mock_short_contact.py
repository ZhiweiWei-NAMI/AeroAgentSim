#!/usr/bin/env python3
"""P04: actual execution-service tools with sustained/short mock contact.

Requests are selected by this component driver, not Cordis. The same public
scene, detector, release/verify tools and real mock radio are used in both runs.
The only negative-case change is a declared post-contact mechanical impulse.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import threading
import time
import traceback
import uuid

import yaml

from aerorescue.companion.coordinator import Coordinator
from aerorescue.companion.flight import FlightClient
from aerorescue.companion.runtime import CompanionRuntime
from aerorescue.companion.sensors import download_capture
from aerorescue.companion.transport import NS, save_json


def run_branch(args, base, variant):
    root = args.output.resolve() / variant
    root.mkdir()
    assets = root / 'private-assets'; assets.mkdir()
    sprite = assets / args.person_sprite.name
    shutil.copy2(args.person_sprite, sprite)
    scene = copy.deepcopy(base)
    scene.update(max_real_time_factor=3, execution_backend='mock')
    scene['incident']['deadline_ns'] = 300 * NS
    scene['vehicles']['u4']['initial_task'] = {'task_id': 'contact-diagnostic', 'task_revision': 1, 'intent': 'diagnostic'}
    scene['mock_backend'].update(expected_clients=['u4'], startup_timeout_s=30, person_sprite_path=str(sprite))
    for person in scene['mock_backend']['people']:
        person.pop('sprite_path', None)
    scene['mock_backend'].pop('payload_contact_impulse', None)
    if variant == 'short_contact':
        scene['mock_backend']['payload_contact_impulse'] = {
            'after_contact_ns': 500_000_000,
            'velocity_delta_enu_m_s': [12.0, 0.0, 5.0],
        }
    # Public near-field centre isolates contact causality from the separately
    # exercised obstructed-link cases. It still receives only radio commits.
    next(n for n in scene['radio']['nodes'] if n['id'] == 'centre')['position_enu'] = [90, 0, 0]
    (root / 'private-scene.yaml').write_text(yaml.safe_dump(scene))
    public = copy.deepcopy(scene); public.pop('mock_backend')
    public.update(model_path=str(args.model.resolve()), embedding_path=None)
    (root / 'agent-config.yaml').write_text(yaml.safe_dump(public))
    save_json(root / 'fixed-inputs.json', {
        'execution_backend': 'mock', 'variant': variant,
        'contact_threshold_ns': scene['payload']['contact_duration_ns'],
        'impulse': scene['mock_backend'].get('payload_contact_impulse'),
        'diagnostic_camera_goal_enu_m': [60, 60, 40],
        'input_scope': 'public receiver centre chosen before detector; no person position enters tool requests',
    })
    run_id = args.output.name + '-' + variant
    coordinator = Coordinator(scene, run_id, root / 'coordinator', root / 'ipc', backend='mock', until_ns=300 * NS)
    errors = []
    def run():
        try: coordinator.run()
        except Exception as error:
            errors.append(repr(error)); traceback.print_exc()
    thread = threading.Thread(target=run); thread.start()
    runtime = None
    try:
        until = time.monotonic() + 30
        while not (root / 'ipc/ready.json').exists():
            if errors: raise RuntimeError(errors)
            if time.monotonic() > until: raise TimeoutError('coordinator ready')
            time.sleep(.01)
        runtime = CompanionRuntime(public, run_id=run_id, uav_id='u4', data_dir=root / 'u4',
            socket_path='file://' + str(root / 'ipc/u4/agent.sock'))
        centre = FlightClient('file://' + str(root / 'ipc/centre/agent.sock'), run_id)
        received = {}
        def tick():
            if errors: raise RuntimeError(errors)
            runtime.tick()
            for message in centre.request('radio_inbox').get('messages', []):
                if message['message_id'] not in received:
                    received[message['message_id']] = message
                    centre.request('radio_inbox_ack', message_id=message['message_id'])
            time.sleep(.01)
        def call(name, request, control=False):
            rid = uuid.uuid4().hex
            folder = runtime.root / 'tasks/tool-requests'; folder.mkdir(exist_ok=True)
            item = {'request_id': rid, 'control' if control else 'tool': name, 'request': request,
                    'continue_on_unavailable': True}
            task = runtime.state.get('task')
            if task: item.update(task_id=task['task_id'], task_revision=task['task_revision'])
            save_json(folder / (rid + '.queued.json'), item)
            until = time.monotonic() + 100
            while not (folder / (rid + '.result.json')).exists():
                if time.monotonic() > until: raise TimeoutError(name)
                tick()
            result = json.loads((folder / (rid + '.result.json')).read_text())
            print(variant, name, result.get('status'), flush=True)
            return result
        until = time.monotonic() + 30
        while not runtime.state['seen_messages']:
            if time.monotonic() > until: raise TimeoutError('initial radio notice did not arrive')
            tick()
        result = call('accept-task', {'message_id': runtime.state['seen_messages'][0]}, True)
        assert result['status'] == 'succeeded', result
        for action, params in [('arm', {}), ('takeoff', {'altitude_m': 40}),
                ('goto', {'position_enu_m': [60, 60, 40], 'yaw_deg': 0})]:
            result = call('flight-command', {'action': action, 'params': params})
            assert result['status'] == 'succeeded', result
        after = runtime.flight.state()['sim_ns']
        while True:
            capture = runtime._gateway_request(op='capture_request', after_sim_ns=after)
            if capture['status'] != 'pending': break
            tick()
        assert capture['status'] == 'succeeded', capture
        first = download_capture(runtime.tools.perception_tools().store, runtime._gateway_request, capture)
        detected = call('detect-persons', {'frame_id': first['frame_id']})
        assert detected.get('detections'), detected
        person = max(detected['detections'], key=lambda row: row['score'])
        site = call('select-drop-site', {'frame_id': first['frame_id'], 'detection_id': person['detection_id']})
        assert site['status'] == 'succeeded', site
        package_id = scene['payload']['package_id']
        delivery = call('deliver-supply', {'drop_site_id': site['drop_site_id'], 'package_id': package_id})
        assert delivery['status'] == 'succeeded' and delivery['frames'], delivery
        package = runtime.tools.payload.packages[package_id]
        assert package['released'] is True
        # Both runs observe the full existing 30-second receipt window. There is
        # no hand-written receipt or direct alteration of payload state.
        while runtime.state['sim_ns'] < package['released_ns'] + 32 * NS: tick()
        receipts = [json.loads((runtime.root / 'inbox' / mid / 'message.json').read_text())
                    for mid in runtime.state['seen_messages']]
        receipts = [m for m in receipts if m['kind'] == 'receipt' and m['data'].get('package_id') == package_id]
        if variant == 'sustained_contact':
            assert len(receipts) == 1, receipts
            capture_after = call('verify-delivery', {'package_id': package_id, 'receipt_message_id': receipts[0]['message_id']})
            assert capture_after['status'] == 'succeeded' and capture_after['frames'], capture_after
            result = call('verify-delivery', {'package_id': package_id,
                'post_drop_frame_id': capture_after['frames'][-1]['frame_id']})
            assert result.get('delivery_verified') is True, result
        else:
            assert receipts == [], receipts
            result = call('verify-delivery', {'package_id': package_id,
                'post_drop_frame_id': delivery['frames'][-1]['frame_id']})
            assert result['status'] == 'receipt_unconfirmed' and not result['delivery_verified'], result
        sent = call('send-evidence', {'destination': 'centre', 'data': {
            'kind': 'contact_diagnostic_result', 'package_id': package_id,
            'status': result['status'], 'delivery_verified': bool(result.get('delivery_verified'))}})
        assert sent['status'] == 'succeeded', sent
        for _ in range(3): tick()
        assert sent['message_id'] in received
        returned = call('resume-or-return', {'return_position_enu_m': [9, 0, 3]})
        assert returned['status'] == 'succeeded', returned
        rows = [json.loads(line) for line in (root / 'coordinator/mock-world.jsonl').read_text().splitlines()]
        contacts = [row for row in rows if row['kind'] == 'payload_contact']
        breaks = [row for row in rows if row['kind'] == 'payload_contact_interrupted']
        landings = [row for row in rows if row['kind'] == 'payload_landed']
        centre_receipts = [m for m in received.values() if m['kind'] == 'receipt']
        confirmations = [m for m in received.values() if m.get('data', {}).get('kind') == 'delivery_confirmation']
        if variant == 'short_contact':
            assert len(breaks) == 1 and breaks[0]['contact_duration_ns'] < NS
            assert landings[0]['inside_receiver'] and not landings[-1]['inside_receiver']
            assert not contacts and not centre_receipts and not confirmations
        else:
            assert not breaks and len(contacts) == 1 and contacts[0]['receipt']['contact_duration_ns'] >= NS
            assert len(centre_receipts) == len(confirmations) == 1
        report = {'execution_backend': 'mock', 'native': False, 'run_id': run_id, 'variant': variant,
            'scope': 'driver-selected actual tool/flight/camera/radio path, not a five-Cordis autonomous decision run',
            'detector_result': detected, 'selected_site': site,
            'release_count': sum(row['kind'] == 'payload_release' for row in rows),
            'contact_threshold_ns': scene['payload']['contact_duration_ns'],
            'landings': landings, 'contact_interruptions': breaks, 'contact_receipts': contacts,
            'carrier_receipts': receipts, 'centre_receipts': centre_receipts, 'centre_confirmations': confirmations,
            'verification': result, 'radio_report': received[sent['message_id']],
            'return_status': returned['status'], 'original_task': runtime.state['task'],
            'checks': 'short contact remains unconfirmed; only sustained contact creates actual radio receipt and verifies delivery',
            'evidence': ['coordinator/mock-world.jsonl', 'coordinator/mock-rpc.jsonl', 'coordinator/flight/operations.jsonl',
                'coordinator/radio/packet-ledger.jsonl', 'u4/logs/tool-trace.jsonl', 'u4/tasks/tool-requests', 'u4/inbox']}
        assert report['release_count'] == 1
        save_json(root / 'report.json', report)
        print('DONE', variant, flush=True)
        return report
    except Exception as error:
        save_json(root / 'failure.json', {'error': repr(error), 'traceback': traceback.format_exc()})
        raise
    finally:
        if runtime is not None: runtime.close()
        coordinator.stop_requested = True; thread.join(10)
        if thread.is_alive(): raise RuntimeError('coordinator did not stop')
        if errors: raise RuntimeError(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--person-sprite', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).parents[1] / 'scenarios/rooftop-flood-mock.yaml')
    args = parser.parse_args()
    os.environ.setdefault('ORT_DISABLE_TELEMETRY', '1')
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, args.output / 'driver.py')
    from aerorescue.companion import mock_backend
    shutil.copy2(mock_backend.__file__, args.output / 'mock_backend.py')
    base = yaml.safe_load(args.scenario.read_text())
    reports = [run_branch(args, base, variant) for variant in ('short_contact', 'sustained_contact')]
    save_json(args.output / 'summary.json', {'execution_backend': 'mock', 'native': False,
        'scope': 'two actual tool-chain contact components',
        'results': [{k: r[k] for k in ['variant', 'release_count', 'contact_threshold_ns', 'return_status']} |
                    {'delivery_verified': r['verification']['delivery_verified'],
                     'verification_status': r['verification']['status'],
                     'carrier_receipt_count': len(r['carrier_receipts']),
                     'centre_confirmation_count': len(r['centre_confirmations'])} for r in reports]})


if __name__ == '__main__': main()
