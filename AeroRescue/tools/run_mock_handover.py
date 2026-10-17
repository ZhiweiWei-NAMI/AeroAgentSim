#!/usr/bin/env python3
"""Concrete relay handover through two real Cordis/DSH owners and mock radio.

The driver observes the real workflow, packets and state. It never constructs
an acceptance, updates a runtime state, or substitutes a fake tool result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import yaml

from case_history import read_observations
from run_mock_history import read, write

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--variant', choices=['accepted', 'not-authorized'], default='accepted')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--model-cache', type=Path, required=True)
    parser.add_argument('--person-sprite', type=Path, required=True)
    parser.add_argument('--wall-timeout', type=float, default=330)
    args = parser.parse_args()
    if not args.run_id.startswith('mock-handover-'):
        raise ValueError('Use a fresh mock-handover-* run identity')
    root = APP / 'runs' / args.run_id
    root.mkdir(exist_ok=False)
    scene = yaml.safe_load((APP / 'scenarios/rooftop-flood-mock.yaml').read_text())
    scene.update(until_ns=300_000_000_000, max_real_time_factor=1.0,
                 model_path=str(args.model_cache.resolve() / 'rtdetrv2_r18vd_120e_coco.onnx'),
                 embedding_path=str(args.model_cache.resolve() / 'bge-small-zh-v1.5'))
    scene['incident']['deadline_ns'] = 260_000_000_000
    polygon = [[8, 28], [12, 28], [12, 32], [8, 32]]
    candidate = [10, 30, 30]
    if candidate not in scene['public_map']['relay_candidates']:
        raise ValueError('The fixed handover point must be an existing public candidate')
    scene['public_map'].update(search_polygon=polygon, search_altitude_m=40, search_row_spacing_m=6)
    scene['vehicles']['u4']['capabilities'] = list(dict.fromkeys([*scene['vehicles']['u4']['capabilities'], 'relay']))
    scene['vehicles']['u3']['initial_task'] = {
        'task_id': 'relay-original-u3', 'task_revision': 7, 'intent': 'relay-handover',
        'deadline_ns': 260_000_000_000,
        'workflow': [
            {'tool': 'flight-command', 'request': {'action': 'arm'}},
            {'tool': 'flight-command', 'request': {'action': 'takeoff', 'params': {'altitude_m': 40}}},
            {'tool': 'search-area', 'request': {'polygon': polygon, 'altitude_m': 40, 'footprint_width_m': 6 / .7}, 'result_key': 'survey'},
            {'tool': 'send-evidence', 'request': {'frame_id': '$results.survey.frames.0.frame_id', 'destination': 'u4', 'route': ['u3', 'u4']}, 'result_key': 'evidence'},
            {'tool': 'hold-relay', 'request': {'candidates': [candidate], 'peers': ['centre', 'u2']}, 'result_key': 'relay'},
            {'tool': 'handover-role', 'request': {'action': 'propose', 'destination': 'u4', 'evidence_message_ids': ['$results.evidence.message_id']}, 'result_key': 'handover-offer'},
        ]}
    scene['vehicles']['u4']['initial_task'] = {
        'task_id': 'relay-replacement-u4', 'task_revision': 11, 'intent': 'relay-replacement',
        'deadline_ns': 260_000_000_000, 'accept_relay_handover': args.variant == 'accepted', 'workflow': []}
    scene['mock_backend']['expected_clients'] = ['u3', 'u4']
    sprite = root / 'private-assets' / args.person_sprite.name
    sprite.parent.mkdir()
    shutil.copyfile(args.person_sprite.resolve(), sprite)
    scene['mock_backend']['person_sprite_path'] = str(sprite)
    private, config = root / 'private-scene.yaml', root / 'scenario.yaml'
    private.write_text(yaml.safe_dump(scene, sort_keys=False))
    config.write_text(yaml.safe_dump({key: value for key, value in scene.items() if key not in {'mock_backend', 'native_radio_fault'}}, sort_keys=False))
    env = {**os.environ, 'ORT_DISABLE_TELEMETRY': '1', 'PYTHONUNBUFFERED': '1',
           'PYTHONPATH': os.pathsep.join(map(str, [APP / 'src', REPO / 'src', REPO / 'aerokernel']))}
    summary = {'run_id': args.run_id, 'case_id': 'relay-handover-component', 'variant': args.variant,
               'component_status': 'RUNNING', 'execution_backend': 'mock', 'native_status': 'NOT_ASSESSED',
               'decision_owner': 'official Cordis/DSH agent-loop', 'mode': 'rule',
               'agents': ['u3', 'u4'], 'centre_agent_started': False,
               'scope': 'Concrete mock protocol, real rendered evidence and real queued flight/radio operations; no native/OS isolation claim',
               'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    write(root / 'run-manifest.json', summary)
    shutil.copyfile(Path(__file__), root / 'driver-source.py')
    for name, command in [('source-head.txt', ['git', 'rev-parse', 'HEAD']),
                          ('source-status.txt', ['git', 'status', '--short']),
                          ('source-diff.patch', ['git', 'diff', '--', 'AeroRescue/src', 'AeroRescue/harness'])]:
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
        (root / name).write_text(result.stdout + result.stderr)
    processes: dict[str, subprocess.Popen] = {}
    lifecycle, handles, samples = [], [], []
    started, last_sim = time.monotonic(), -1
    saved = set()

    def launch(name: str, command: list[str]) -> None:
        out, err = open(root / f'{name}.stdout.jsonl', 'w'), open(root / f'{name}.stderr.log', 'w')
        handles.extend([out, err])
        write(root / f'{name}.command.json', command)
        process = subprocess.Popen(command, cwd=APP, env=env, stdout=out, stderr=err, start_new_session=True)
        processes[name] = process
        lifecycle.append({'event': 'started', 'name': name, 'pid': process.pid, 'wall_ns': time.time_ns()})
        write(root / 'process-lifecycle.json', lifecycle)

    def stop(name: str) -> None:
        process = processes[name]
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        lifecycle.append({'event': 'stopped', 'name': name, 'pid': process.pid,
                          'returncode': process.returncode, 'wall_ns': time.time_ns()})
        write(root / 'process-lifecycle.json', lifecycle)

    def snapshot(label: str, states: dict) -> None:
        if label not in saved:
            saved.add(label)
            write(root / f'state-{label}.json', states)

    def sample(states: dict) -> None:
        nonlocal last_sim
        now = max((state.get('sim_ns', 0) for state in states.values()), default=0)
        if now == last_sim:
            return
        last_sim = now
        row = {'sample_sim_ns': now, 'wall_ns': time.time_ns(), 'agents': {}}
        for uid, state in states.items():
            local = state.get('self_state', {})
            row['agents'][uid] = {'sim_ns': state.get('sim_ns'), 'relay_held': state.get('relay_held', False),
                'relay_release_authorized': state.get('relay_release_authorized'),
                'handover_outgoing_status': (state.get('handover_outgoing') or {}).get('status'),
                'handover_incoming_message_id': (state.get('handover_incoming') or {}).get('message_id'),
                'handover_released_message_id': (state.get('handover_released') or {}).get('message_id'),
                'position_enu_m': local.get('vehicle_estimate', {}).get('position_enu_m'),
                'landed_state': local.get('landed_state'), 'mode': local.get('mode'),
                'task_id': (state.get('task') or {}).get('task_id'),
                'task_revision': (state.get('task') or {}).get('task_revision'),
                'result_keys': list(state.get('results', {}))}
        samples.append(row)
        with (root / 'responsibility-timeline.jsonl').open('a') as output:
            output.write(json.dumps(row) + '\n')

    def assess(states: dict) -> dict:
        u3, u4 = states['u3'], states['u4']
        records = read_observations(root / 'agents/u3/index.sqlite')
        write(root / 'actual-observations.json', records)
        d3, d4 = lines(root / 'agents/u3/logs/decisions.jsonl'), lines(root / 'agents/u4/logs/decisions.jsonl')
        calls3 = [row for row in d3 if row.get('call', {}).get('tool') == 'handover-role']
        calls4 = [row for row in d4 if row.get('call', {}).get('tool') == 'handover-role']
        evidence = u3.get('results', {}).get('evidence', {})
        evidence_message = read(root / 'agents/u4/inbox' / evidence.get('message_id', 'missing') / 'message.json')
        outgoing = u3.get('handover_outgoing') or {}
        offer = read(root / 'agents/u4/inbox' / outgoing.get('message_id', 'missing') / 'message.json')
        accept = next((row for row in calls4 if row['call']['request'].get('action') == 'accept'), {})
        acknowledge = next((row for row in calls4 if row['call']['request'].get('action') == 'acknowledge' and row['result'].get('status') == 'succeeded'), {})
        release = next((row for row in calls3 if row['call']['request'].get('action') == 'release' and row['result'].get('status') == 'succeeded'), {})
        reply = u3.get('handover_released') or {}
        holds = [m for m in u4.get('missions', {}).values() if m.get('tool_name') == 'hold-relay' and m.get('status') == 'succeeded']
        proof = holds[0].get('probe_results', []) if holds else []
        held_rows = [row['agents']['u3'] for row in samples if row['agents']['u3'].get('relay_held')]
        held_since = min((row['sim_ns'] for row in held_rows), default=None)
        boundary = reply.get('received_ns', outgoing.get('data', {}).get('deadline_ns', 2**63))
        window = [row['agents']['u3'] for row in samples if held_since is not None and held_since <= (row['agents']['u3'].get('sim_ns') or 0) < boundary]
        checks = {
            'actual_rgb_depth_retained': bool(records) and all(row.get('rgb_artifact_id') and row.get('depth_artifact_id') for row in records),
            'evidence_reached_final_ack': evidence.get('status') == 'succeeded',
            'evidence_received_before_offer': bool(evidence_message) and evidence_message.get('received_ns', 2**63) < offer.get('created_ns', -1),
            'file_ack_before_offer': evidence.get('ack_received_ns', 2**63) <= offer.get('created_ns', -1),
            'offer_bound_to_source_task_revision': offer.get('data', {}).get('previous_task') == {'event_id': scene['incident']['event_id'], 'task_id': 'relay-original-u3', 'task_revision': 7},
            'source_held_during_wait': bool(window) and all(row['relay_held'] and not row['handover_released_message_id'] for row in window),
            'source_stayed_at_relay_while_waiting': bool(window) and all(row['position_enu_m'] and math.dist(row['position_enu_m'], candidate) <= 2 for row in window),
        }
        if args.variant == 'accepted':
            checks.update(
                acceptance_succeeded=accept.get('result', {}).get('status') == 'succeeded',
                file_completed_before_acceptance=evidence.get('ack_received_ns', 2**63) <= accept.get('sim_ns', -1),
                replacement_really_held=bool(holds) and u4.get('relay_held') is True,
                replacement_probed_both_endpoints={p['peer'] for p in proof} == {'centre', 'u2'} and all(p['result'].get('status') == 'succeeded' and p['result'].get('source') == 'u4' and p['result'].get('probe_only') for p in proof),
                probe_acks_precede_business_ack=bool(proof) and bool(acknowledge) and max(p['result'].get('ack_received_ns', 2**63) for p in proof) <= acknowledge.get('sim_ns', -1),
                replacement_ack_bound_to_own_task=reply.get('data', {}).get('accepted_task') == {'event_id': scene['incident']['event_id'], 'task_id': 'relay-replacement-u4', 'task_revision': 11},
                source_release_after_received_business_ack=bool(reply) and bool(release) and release.get('sim_ns', -1) >= reply.get('received_ns', 2**63),
                source_return_completed=u3.get('results', {}).get('return_after_handover', {}).get('status') == 'succeeded',
                source_responsibility_released=u3.get('relay_held') is False and bool(u3.get('relay_release_authorized')))
            returns = [row for row in d3 if row.get('call', {}).get('tool') == 'resume-or-return']
            checks['no_return_before_business_ack'] = bool(returns) and all(row['sim_ns'] >= reply.get('received_ns', 2**63) for row in returns)
        else:
            checks.update(unauthorized_acceptance_rejected=accept.get('result', {}).get('status') in {'unavailable', 'rejected'},
                          confirmation_deadline_expired=outgoing.get('status') == 'timed_out',
                          original_responsibility_retained=u3.get('relay_held') is True and not u3.get('handover_released') and not u3.get('relay_release_authorized'),
                          no_unapproved_return='return_after_handover' not in u3.get('results', {}),
                          replacement_did_not_accept=not u4.get('handover_incoming') and not acknowledge)
        if evidence_message.get('content_path') and records:
            content = Path(evidence_message['content_path']).read_bytes()
            checks['received_file_hash_matches'] = hashlib.sha256(content).hexdigest() == evidence.get('file_hash') == records[0]['rgb_sha256']
        write(root / 'handover-evidence.json', {'evidence_result': evidence, 'evidence_message': evidence_message,
              'offer': offer, 'accept_decision': accept, 'ack_decision': acknowledge, 'release_decision': release,
              'business_ack': reply, 'replacement_probe_results': proof})
        return {'checks': checks, 'observation_count': len(records), 'timing': {
            'file_received_ns': evidence_message.get('received_ns'), 'file_ack_ns': evidence.get('ack_received_ns'),
            'offer_created_ns': offer.get('created_ns'), 'accept_completed_ns': accept.get('sim_ns'),
            'business_ack_completed_ns': acknowledge.get('sim_ns'), 'business_ack_received_ns': reply.get('received_ns'),
            'source_released_ns': release.get('sim_ns'), 'offer_deadline_ns': outgoing.get('data', {}).get('deadline_ns')},
            'component_status': 'MOCK_PASS' if checks and all(checks.values()) else 'MOCK_FAIL'}

    states = {'u3': {}, 'u4': {}}
    try:
        launch('coordinator', [args.python, '-m', 'aerorescue.companion.coordinator', '--backend', 'mock',
               '--scenario', str(private), '--run-id', args.run_id, '--data', str(root / 'coordinator'),
               '--socket-root', str(root / 'ipc'), '--until-ns', '300000000000', '--seed', '42'])
        until = time.monotonic() + 30
        while not (root / 'ipc/ready.json').exists():
            if processes['coordinator'].poll() is not None or time.monotonic() > until:
                raise RuntimeError('coordinator not ready')
            time.sleep(.1)
        for uid in ('u3', 'u4'):
            launch(uid, ['node', str(APP / 'harness/launch.mjs'), '--config', str(config), '--run-id', args.run_id,
                   '--uav-id', uid, '--role', 'uav', '--data-dir', str(root / 'agents' / uid),
                   '--socket', 'file://' + str(root / 'ipc' / uid / 'agent.sock'), '--mode', 'rule', '--python', args.python])
        terminal_since = None
        while time.monotonic() - started < args.wall_timeout:
            states = {uid: read(root / 'agents' / uid / 'runtime-state.json') for uid in ('u3', 'u4')}
            sample(states)
            u3, u4 = states['u3'], states['u4']
            for label, condition in [('source-holding', u3.get('relay_held')), ('offer-created', u3.get('handover_outgoing')),
                                     ('replacement-accepted', u4.get('handover_incoming')), ('replacement-holding', u4.get('relay_held')),
                                     ('source-released', u3.get('handover_released'))]:
                if condition:
                    snapshot(label, states)
            terminal = u3.get('results', {}).get('return_after_handover', {}).get('status') == 'succeeded' if args.variant == 'accepted' else u3.get('handover_outgoing', {}).get('status') == 'timed_out'
            if terminal:
                terminal_since = terminal_since or u3['sim_ns']
                if u3['sim_ns'] >= terminal_since + 5_000_000_000:
                    break
            exits = {name: p.returncode for name, p in processes.items() if p.poll() is not None}
            if exits:
                raise RuntimeError('process exited: ' + str(exits))
            if any(s.get('task_blocker') for s in states.values()):
                raise RuntimeError('actual workflow blocker; see final states')
            time.sleep(.1)
        else:
            raise TimeoutError('handover case observation window ended before terminal state')
        snapshot('final', states)
        summary.update(assess(states))
        print(json.dumps({'run': str(root), 'status': summary['component_status'], 'checks': summary['checks'], 'timing': summary['timing']}, ensure_ascii=False), flush=True)
        return 0 if summary['component_status'] == 'MOCK_PASS' else 1
    except Exception as exc:
        snapshot('final', states)
        summary.update(component_status='MOCK_BLOCKED', execution_error=str(exc))
        try:
            partial = assess(states)
            summary.update({key: value for key, value in partial.items() if key != 'component_status'})
        except Exception as assessment_error:
            summary['assessment_error'] = str(assessment_error)
        print(json.dumps({'run': str(root), 'error': str(exc)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        for name in ('u3', 'u4', 'coordinator'):
            if name in processes:
                stop(name)
        for handle in handles:
            handle.close()
        summary['elapsed_wall_s'] = time.monotonic() - started
        write(root / 'handover-summary.json', summary)
        write(root / 'run-manifest.json', summary)


if __name__ == '__main__':
    raise SystemExit(main())
