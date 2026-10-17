#!/usr/bin/env python3
"""One C06 mock history exercise through real Cordis/DSH and local tools.

This is a concrete case driver, not a test suite or a replacement runtime.
All sensor records come from the running mock camera and coordinator.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import yaml

from case_history import assess_query, build_queries, read_observations, spatial_bounds

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--model-cache', type=Path, required=True)
    parser.add_argument('--person-sprite', type=Path, required=True, help='Existing local renderer sprite; never downloaded by this driver')
    parser.add_argument('--wall-timeout', type=float, default=560)
    args = parser.parse_args()
    root = APP / 'runs' / args.run_id
    if not args.run_id.startswith('mock-history-'):
        raise ValueError('Use a fresh mock-history-* run identity')
    root.mkdir(exist_ok=False)
    scene = yaml.safe_load((APP / 'scenarios/rooftop-flood-mock.yaml').read_text())
    scene.update(until_ns=600_000_000_000, max_real_time_factor=1.0,
                 model_path=str(args.model_cache / 'rtdetrv2_r18vd_120e_coco.onnx'),
                 embedding_path=str(args.model_cache / 'bge-small-zh-v1.5'))
    # Public roof/edge subregion. No simulator-only target location is used by
    # the worker, by query construction, or by record relevance assessment.
    polygon = [[60, 60], [72, 60], [72, 72], [60, 72]]
    scene['public_map'].update(search_polygon=polygon, search_altitude_m=40, search_row_spacing_m=6)
    scene['vehicles']['u2']['initial_task'] = {
        'task_id': 'history-survey-u2', 'task_revision': 1, 'intent': 'retain-history',
        'workflow': [
            {'tool': 'flight-command', 'request': {'action': 'arm'}},
            {'tool': 'flight-command', 'request': {'action': 'takeoff', 'params': {'altitude_m': 40}}},
            {'tool': 'search-area', 'request': {'polygon': polygon, 'altitude_m': 40,
                                             'footprint_width_m': 6 / .7}, 'result_key': 'survey'},
        ]}
    scene['vehicles']['u3']['initial_task'] = {
        'task_id': 'history-receive-u3', 'task_revision': 1, 'intent': 'retain-history',
        'workflow': [
            {'tool': 'flight-command', 'request': {'action': 'arm'}},
            {'tool': 'flight-command', 'request': {'action': 'takeoff', 'params': {'altitude_m': 40}}},
        ]}
    scene['mock_backend']['expected_clients'] = ['u2', 'u3']
    asset = args.person_sprite.resolve()
    sprite = root / 'private-assets' / asset.name
    sprite.parent.mkdir()
    shutil.copyfile(asset, sprite)
    scene['mock_backend']['person_sprite_path'] = str(sprite)
    private = root / 'private-scene.yaml'
    private.write_text(yaml.safe_dump(scene, sort_keys=False))
    config = root / 'scenario.yaml'
    config.write_text(yaml.safe_dump({k: v for k, v in scene.items() if k not in {'mock_backend', 'native_radio_fault'}}, sort_keys=False))
    env = {**os.environ, 'ORT_DISABLE_TELEMETRY': '1', 'PYTHONUNBUFFERED': '1',
           'PYTHONPATH': os.pathsep.join(map(str, [APP / 'src', REPO / 'src', REPO / 'aerokernel']))}
    started = time.monotonic()
    deadline = started + args.wall_timeout
    processes: dict[str, subprocess.Popen] = {}
    generations: dict[str, int] = {}
    handles = []
    lifecycle = []
    outcomes = {}
    summary = {'case_id': 'C06', 'backend': 'mock', 'component_status': 'RUNNING',
               'native_status': 'NOT_ASSESSED', 'whole_C06_status': 'NOT_ASSESSED',
               'scope': 'MOCK components only; same-host process/datastore separation does not prove OS file/socket isolation',
               'run_id': args.run_id, 'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    write(root / 'run-manifest.json', {**summary, 'decision_owner': 'official Cordis/DSH agent-loop',
          'mode': 'rule', 'agents': ['u2', 'u3'], 'coordinator_is_only_advance_owner': True,
          'acquisition': 'actual mock-rendered PNG and numerical depth, no synthetic observation inserts'})
    shutil.copyfile(Path(__file__), root / 'driver-source.py')
    for name, command in [('source-head.txt', ['git', 'rev-parse', 'HEAD']),
                          ('source-status.txt', ['git', 'status', '--short']),
                          ('source-diff.patch', ['git', 'diff', '--', 'AeroRescue/src', 'AeroRescue/harness'])]:
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
        (root / name).write_text(result.stdout + result.stderr)

    def launch(name: str, command: list[str]) -> None:
        generation = generations.get(name, -1) + 1
        generations[name] = generation
        stem = name if not generation else f'{name}.restart-{generation}'
        out, err = open(root / f'{stem}.stdout.jsonl', 'w'), open(root / f'{stem}.stderr.log', 'w')
        handles.extend([out, err])
        write(root / f'{stem}.command.json', command)
        process = subprocess.Popen(command, cwd=APP, env=env, stdout=out, stderr=err, start_new_session=True)
        processes[name] = process
        lifecycle.append({'event': 'started', 'name': name, 'generation': generation,
                          'pid': process.pid, 'wall_ns': time.time_ns()})
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
        # The owned process group also contains DSH and the Python worker.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        lifecycle.append({'event': 'stopped', 'name': name, 'generation': generations[name],
                          'pid': process.pid, 'returncode': process.returncode, 'wall_ns': time.time_ns()})
        write(root / 'process-lifecycle.json', lifecycle)

    def wait_until(predicate, label: str, seconds: float = 120) -> None:
        until = min(deadline, time.monotonic() + seconds)
        while time.monotonic() < until:
            if predicate():
                return
            exits = {name: process.returncode for name, process in processes.items() if process.poll() is not None}
            if exits:
                raise RuntimeError(f'{label}: process exit {exits}')
            time.sleep(.1)
        raise TimeoutError(label)

    def command(uid: str) -> list[str]:
        return ['node', str(APP / 'harness/launch.mjs'), '--config', str(config), '--run-id', args.run_id,
                '--uav-id', uid, '--role', 'uav', '--data-dir', str(root / 'agents' / uid),
                '--socket', 'file://' + str(root / 'ipc' / uid / 'agent.sock'), '--mode', 'rule', '--python', args.python]

    def query(stage: str, request: dict, tool: str = 'retrieve-sightings', uid: str = 'u2') -> dict:
        cli = [args.python, '-m', 'aerorescue.companion.tools', '--data-dir', str(root / 'agents' / uid)]
        queued = subprocess.run(cli + [tool, '--request', json.dumps(request)], cwd=APP, env=env,
                                capture_output=True, text=True, timeout=20, check=True)
        (root / f'{stage}.queued.log').write_text(queued.stdout + queued.stderr)
        identity = json.loads(queued.stdout)['request_id']
        path = root / 'agents' / uid / 'tasks/tool-requests' / (identity + '.result.json')
        wait_until(path.exists, stage + ': queued tool did not finish', seconds=80)
        result = read(path)
        write(root / f'{stage}.json', result)
        return result

    def batch(phase: str, queries: list[dict], records: list[dict]) -> dict:
        outcomes[phase] = []
        for item in queries:
            try:
                result = query(f'history-{phase}-{item["query_id"]}', item['request'])
                outcome = assess_query(item, result, records)
            except Exception as exc:
                outcome = {'query_id': item['query_id'], 'success': False, 'actual_result': None,
                           'execution_error': str(exc)}
            outcomes[phase].append(outcome)
            write(root / 'retrieval-results.json', outcomes)
        positive = [row for row in outcomes[phase] if row['query_id'].startswith('positive-')]
        absent = [row for row in outcomes[phase] if row['query_id'].startswith('absence-')]
        return {'positive_queries': len(positive), 'top5_hits': sum(row.get('top5_hit') is True for row in positive),
                'absence_queries': len(absent), 'correct_empty_answers': sum(row['success'] for row in absent),
                'all_success': all(row['success'] for row in outcomes[phase]),
                'failures': [row['query_id'] for row in outcomes[phase] if not row['success']],
                'semantic_backend_unavailable': [row['query_id'] for row in outcomes[phase] if row.get('semantic_backend_unavailable')],
                'execution_errors': [row['query_id'] for row in outcomes[phase] if row.get('execution_error')],
                'tool_failures': [row['query_id'] for row in outcomes[phase] if row.get('actual_result') is not None and row['actual_result'].get('status') != 'succeeded'],
                'fabricated_record_ids': [frame for row in outcomes[phase] for frame in row.get('fabricated_record_ids', [])],
                'provenance_or_filter_violations': [v for row in outcomes[phase] for v in row.get('filter_or_provenance_violations', [])]}

    def lookup(phase: str, frame: dict, records: list[dict]) -> dict:
        expected = [{k: frame[k] for k in ('frame_id', 'rgb_sha256', 'observed_at', 'uav_id')}]
        request = {'observer': 'u2', 'observed_after_ns': frame['observed_at'], 'observed_before_ns': frame['observed_at'], 'limit': 5}
        requests = [{'query_id': 'old-frame-time', 'request': request, 'expected': expected},
                    {'query_id': 'old-frame-area', 'request': {**request, 'region_enu': spatial_bounds(frame)}, 'expected': expected}]
        return batch(phase, requests, records)

    database = root / 'agents/u2/index.sqlite'
    state_path = root / 'agents/u2/runtime-state.json'
    try:
        launch('coordinator', [args.python, '-m', 'aerorescue.companion.coordinator', '--backend', 'mock',
               '--scenario', str(private), '--run-id', args.run_id, '--data', str(root / 'coordinator'),
               '--socket-root', str(root / 'ipc'), '--until-ns', '600000000000', '--seed', '42'])
        wait_until(lambda: (root / 'ipc/ready.json').exists(), 'coordinator readiness', 30)
        launch('u2', command('u2'))
        launch('u3', command('u3'))
        wait_until(lambda: bool(read(state_path).get('results', {}).get('survey')) or bool(read(state_path).get('task_blocker')),
                   'actual arm/takeoff/search acquisition', 210)
        acquisition = read(state_path)
        records = read_observations(database)
        summary['acquisition'] = {'survey_status': acquisition.get('results', {}).get('survey', {}).get('status'),
             'task_blocker': acquisition.get('task_blocker'), 'record_count': len(records),
             'unique_rgb_hashes': len({row['rgb_sha256'] for row in records}),
             'depth_records': sum(bool(row.get('depth_artifact_id')) for row in records),
             'footprint_records': sum(bool(spatial_bounds(row)) for row in records)}
        write(root / 'history-query-summary.json', summary)
        if len(records) < 6:
            raise RuntimeError('Fewer than six actual camera frames; retaining failure without fabricated observations')
        indices = sorted({i * (len(records) - 1) // 5 for i in range(6)})
        descriptions = []
        for index in indices:
            descriptions.append({'frame_id': records[index]['frame_id'],
                'actual_result': query(f'history-describe-{index}', {'frame_id': records[index]['frame_id']}, 'detect-persons')})
            write(root / 'history-description-results.json', descriptions)
        records = read_observations(database)
        candidates = [row for row in records if row.get('last_analysis') is not None and not row['last_analysis'].get('detections')]
        frame = next((row for row in candidates if spatial_bounds(row)), None)
        summary['old_frame_no_detection_precondition_met'] = frame is not None
        if frame is None:
            # Keep independent retrieval/radio/restart work running. This fallback
            # is explicitly not an originally empty-detection-frame success.
            frame = records[0]
            summary['old_frame_precondition_failure'] = 'No actually analyzed original no-detection frame; other case components continue'
        write(root / 'history-selected-frame.json', frame)
        corpus = build_queries(records)
        write(root / 'history-corpus-observations.json', records)
        write(root / 'queries.json', corpus)
        frozen_digest = hashlib.sha256((root / 'queries.json').read_bytes()).hexdigest()
        summary['corpus'] = {k: v for k, v in corpus.items() if k not in {'positive', 'absence'}}
        summary['frozen_queries_sha256'] = frozen_digest
        queries = corpus['positive'] + corpus['absence']
        summary['before_restart'] = batch('before-restart', queries, records)
        summary['old_frame_before_restart'] = lookup('old-before', frame, records)
        summary['compare_observations'] = query('compare-observations', {'first_frame_id': records[0]['frame_id'], 'second_frame_id': records[-1]['frame_id']}, 'compare-observations')
        summary['read_local_rgb'] = query('read-local-evidence', {'artifact_id': frame['rgb_artifact_id'], 'frame_id': frame['frame_id']}, 'read-evidence')
        summary['read_local_depth'] = query('read-local-depth', {'artifact_id': frame['depth_artifact_id'], 'frame_id': frame['frame_id']}, 'read-evidence')
        # Read only the destination's own store. Never open the source path as u3.
        summary['cross_uav_before_radio'] = query('cross-before-radio', {'artifact_id': frame['rgb_artifact_id']}, 'read-evidence', 'u3')
        transfer = query('cross-radio-transfer', {'artifact_id': frame['rgb_artifact_id'], 'frame_id': frame['frame_id'], 'destination': 'u3', 'route': ['u2', 'u3']}, 'send-evidence')
        summary['cross_uav_transfer'] = transfer
        message_id = transfer.get('message_id')
        if message_id:
            inbox_path = root / 'agents/u3/inbox' / message_id / 'message.json'
            wait_until(inbox_path.exists, 'radio-delivered copy commit', 30)
            received = read(inbox_path)
            write(root / 'cross-radio-received-message.json', received)
            artifact_id = received.get('artifact_id')
            if artifact_id:
                copied = query('cross-after-radio', {'artifact_id': artifact_id, 'message_id': message_id}, 'read-evidence', 'u3')
                summary['cross_uav_after_radio'] = copied
                metadata = copied.get('metadata', {})
                summary['cross_uav_copy_checks'] = {
                    'unknown_before_send': summary['cross_uav_before_radio'].get('status') == 'unavailable',
                    'complete_transfer': transfer.get('status') == 'succeeded',
                    'destination_read_succeeded': copied.get('status') == 'succeeded',
                    'source_hash_preserved': artifact_id == frame['rgb_artifact_id'],
                    'source_observer_preserved': metadata.get('observer') == frame['uav_id'],
                    'source_observed_at_preserved': metadata.get('observed_at') == frame['observed_at'],
                    'acquisition_source_preserved': metadata.get('acquisition_source') == frame.get('acquisition_source'),
                    'observation_run_id_preserved': metadata.get('observation_run_id') == frame.get('run_id'),
                    'received_at_is_later': metadata.get('received_at', -1) > frame['observed_at'],
                    'destination_path_is_own_copy': str(copied.get('path', '')).startswith(str(root / 'agents/u3/')),
                    'os_file_socket_isolation': 'NOT_ASSESSED: shared host and user identity'}
                # Returning the bytes exercises forwarding provenance. u2 already
                # owns the content hash, so inspect the new inbound manifest;
                # content deduplication must not overwrite its original metadata.
                forwarded = query('cross-forward-to-origin', {'artifact_id': artifact_id, 'message_id': message_id, 'destination': 'u2', 'route': ['u3', 'u2']}, 'send-evidence', 'u3')
                summary['cross_uav_forward_transfer'] = forwarded
                if forwarded.get('message_id'):
                    forwarded_path = root / 'agents/u2/inbox' / forwarded['message_id'] / 'message.json'
                    wait_until(forwarded_path.exists, 'forwarded copy commit', 30)
                    forwarded_message = read(forwarded_path)
                    write(root / 'cross-forwarded-received-message.json', forwarded_message)
                    forwarded_metadata = forwarded_message.get('metadata', {})
                    summary['cross_uav_forward_checks'] = {
                        'forwarded_hash_preserved': forwarded_message.get('file_hash') == frame['rgb_artifact_id'],
                        'original_observer_preserved': forwarded_metadata.get('observer') == frame['uav_id'],
                        'original_observed_at_preserved': forwarded_metadata.get('observed_at') == frame['observed_at'],
                        'actual_radio_sender_is_u3': forwarded_message.get('source') == 'u3',
                        'received_at_is_later_than_first_copy': forwarded_message.get('received_ns', -1) > received.get('received_ns', 0)}
        before = read(state_path)
        write(root / 'runtime-before-restart.json', before)
        before_ps = subprocess.run(['ps', '-eo', 'pid,ppid,pgid,args'], capture_output=True, text=True)
        (root / 'processes-before-restart.txt').write_text(before_ps.stdout)
        coordinator_pid = processes['coordinator'].pid
        stop('u2')
        restart_frontier = read(root / 'agents/u3/runtime-state.json').get('sim_ns', before['sim_ns'])
        time.sleep(2)
        advancing = read(root / 'agents/u3/runtime-state.json').get('sim_ns', 0)
        u3_before = read(root / 'agents/u3/runtime-state.json')
        write(root / 'u3-runtime-before-restart.json', u3_before)
        stop('u3')
        launch('u2', command('u2'))
        launch('u3', command('u3'))
        wait_until(lambda: read(state_path).get('sim_ns', 0) > advancing, 'same-run companion restart', 45)
        wait_until(lambda: read(root / 'agents/u3/runtime-state.json').get('sim_ns', 0) > advancing, 'same-run receiving companion restart', 45)
        after = read(state_path)
        write(root / 'runtime-after-restart.json', after)
        after_ps = subprocess.run(['ps', '-eo', 'pid,ppid,pgid,args'], capture_output=True, text=True)
        (root / 'processes-after-restart.txt').write_text(after_ps.stdout)
        archive_after = read_observations(database)
        identities = {(row['frame_id'], row['rgb_sha256'], row['observed_at']) for row in archive_after}
        summary['restart'] = {
            'same_coordinator_pid': processes['coordinator'].pid == coordinator_pid,
            'coordinator_advanced_during_u2_stop': advancing > restart_frontier,
            'before_stop_frontier_ns': restart_frontier, 'before_restart_frontier_ns': advancing,
            'same_run_uav_task': all(after[k] == before[k] for k in ('run_id', 'uav_id', 'task')),
            'inbox_restored': set(before['seen_messages']).issubset(after['seen_messages']),
            'workflow_cursor_restored': after['step_index'] >= before['step_index'],
            'all_frame_id_hash_observed_at_preserved': all((row['frame_id'], row['rgb_sha256'], row['observed_at']) in identities for row in records)}
        u3_after = read(root / 'agents/u3/runtime-state.json')
        write(root / 'u3-runtime-after-restart.json', u3_after)
        summary['restart']['receiving_inbox_restored'] = set(u3_before['seen_messages']).issubset(u3_after['seen_messages'])
        if summary.get('cross_uav_after_radio', {}).get('artifact_id'):
            summary['cross_uav_read_after_restart'] = query('cross-read-after-restart', {'artifact_id': summary['cross_uav_after_radio']['artifact_id'], 'message_id': message_id}, 'read-evidence', 'u3')
        summary['after_restart'] = batch('after-restart', queries, archive_after)
        summary['old_frame_after_restart'] = lookup('old-after', frame, archive_after)
        reanalysis = query('historical-reanalysis', {'reanalyze_frame_id': frame['frame_id']})
        updated = next(row for row in read_observations(database) if row['frame_id'] == frame['frame_id'])
        summary['old_frame_reanalysis'] = {
            'original_detection_count': len(frame['last_analysis']['detections']),
            'original_analyzed_at': frame['analyzed_at'], 'new_analyzed_at': reanalysis.get('analyzed_at'),
            'frame_id': frame['frame_id'], 'observed_at': frame['observed_at'],
            'frame_hash_observed_at_preserved': all(updated[k] == frame[k] for k in ('frame_id', 'rgb_sha256', 'observed_at')),
            'analysis_is_new': reanalysis.get('analyzed_at', -1) > frame['analyzed_at'],
            'status': reanalysis.get('status'), 'detection_required': False}
        summary['queries_unchanged'] = hashlib.sha256((root / 'queries.json').read_bytes()).hexdigest() == frozen_digest
        summary['history_component_pass'] = corpus['status'] == 'READY' and all(
            summary[phase]['positive_queries'] == 20 and summary[phase]['top5_hits'] >= 18
            and summary[phase]['correct_empty_answers'] == 5 and not summary[phase]['fabricated_record_ids']
            and not summary[phase]['provenance_or_filter_violations'] and not summary[phase]['semantic_backend_unavailable']
            and not summary[phase]['execution_errors'] and not summary[phase]['tool_failures'] for phase in ('before_restart', 'after_restart'))
        summary['component_status'] = 'MOCK_PASS' if summary['history_component_pass'] and summary['old_frame_no_detection_precondition_met'] and all(
            value for key, value in summary['restart'].items() if not key.endswith('_ns')) and summary['old_frame_reanalysis']['frame_hash_observed_at_preserved'] and summary['old_frame_reanalysis']['analysis_is_new'] and summary['old_frame_reanalysis']['status'] == 'succeeded' and all(summary[key]['all_success'] for key in ('old_frame_before_restart', 'old_frame_after_restart')) else 'MOCK_FAIL'
        summary['history_query_restart_component_status'] = summary['component_status']
        summary['cross_uav_component_status'] = 'MOCK_PASS' if summary.get('cross_uav_copy_checks') and all(value for key, value in summary['cross_uav_copy_checks'].items() if key != 'os_file_socket_isolation') and summary.get('cross_uav_forward_checks') and all(summary['cross_uav_forward_checks'].values()) and summary.get('cross_uav_read_after_restart', {}).get('status') == 'succeeded' else 'MOCK_FAIL'
        summary['whole_C06_status'] = 'NOT_ASSESSED: OS file/socket isolation and native acquisition not exercised'
        print(json.dumps({'run': str(root), 'status': summary['component_status'], 'before': summary['before_restart'], 'after': summary['after_restart']}, ensure_ascii=False), flush=True)
        return 0 if summary['component_status'] == 'MOCK_PASS' else 1
    except Exception as exc:
        summary.update(component_status='MOCK_BLOCKED', execution_error=str(exc))
        print(json.dumps({'run': str(root), 'error': str(exc)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        for name in ['u2', 'u3', 'coordinator']:
            if name in processes:
                stop(name)
        for handle in handles:
            handle.close()
        summary['elapsed_wall_s'] = time.monotonic() - started
        write(root / 'history-query-summary.json', summary)
        write(root / 'retrieval-results.json', outcomes)


if __name__ == '__main__':
    raise SystemExit(main())
