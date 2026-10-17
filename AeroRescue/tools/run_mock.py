#!/usr/bin/env python3
"""Run explicit mock adapters with real Cordis/DSH owners; retain every attempt.

This runner does not enqueue tools, inject proposals, or label mock as native.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import yaml

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--agents', default='u1,u2,u3,u4,centre')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--case', choices=['C01', 'C03', 'C04', 'C07'], default='C01')
    parser.add_argument('--variant', choices=['low_energy_after_offer', 'missing_fragment_nack_then_retry', 'final_ack_lost', 'confirmation_final_ack_lost'])
    parser.add_argument('--until-ns', type=int, default=900_000_000_000)
    parser.add_argument('--wall-timeout', type=float, default=1000)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--node', default='node')
    parser.add_argument('--model-cache', type=Path)
    parser.add_argument('--person-sprite', type=Path, help='Existing renderer asset; retained only in the private mock configuration')
    parser.add_argument('--stop-on-outcome', action='store_true', help='Stop after centre outcome and actual nonprotected agent return completions')
    args = parser.parse_args()
    root = (args.output or APP / 'runs/mock' / args.run_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    scene = yaml.safe_load(args.scenario.read_text())
    scene['seed'] = args.seed
    if scene.get('mock_backend', {}).get('person_sprite_required') and not args.person_sprite:
        raise ValueError('This mock scene requires --person-sprite PATH. Prepare it with tools/prepare_mock_sprite.py from the documented existing image; see docs/mock-visual-asset.md.')
    if args.case == 'C04' and args.variant == 'low_energy_after_offer':
        u2, u4 = scene['vehicles']['u2'], scene['vehicles']['u4']
        u2['energy_budget'] = {'initial_fraction': .23, 'max_gap_ns': 1_000_000_000}
        u4['capabilities'] = list(dict.fromkeys([*u4['capabilities'], 'inspect']))
        u2['spawn'][:2], u4['spawn'][:2] = u4['spawn'][:2], u2['spawn'][:2]
    elif (args.case == 'C03' and args.variant in {'missing_fragment_nack_then_retry', 'final_ack_lost'}) or (args.case == 'C07' and args.variant == 'confirmation_final_ack_lost'):
        scene['native_radio_fault'] = {'case_id': args.case, 'variant': args.variant}
    elif args.variant or args.case != 'C01':
        raise ValueError('Use C01, C04/low_energy_after_offer, or an existing C03/C07 packet fault variant')
    agents = args.agents.split(',')
    if len(set(agents)) != len(agents) or any(uid not in {*scene['vehicles'], 'centre'} for uid in agents):
        raise ValueError('agents must be distinct configured vehicle IDs or centre')
    if args.model_cache:
        cache = args.model_cache.resolve()
        scene['embedding_path'] = str(cache / 'bge-small-zh-v1.5')
        scene['model_path'] = str(cache / 'rtdetrv2_r18vd_120e_coco.onnx')
    scene['execution_backend'] = 'mock'
    scene.setdefault('mock_backend', {})['expected_clients'] = agents
    if args.person_sprite:
        private_assets = root / 'private-assets'
        private_assets.mkdir()
        sprite = private_assets / args.person_sprite.name
        shutil.copyfile(args.person_sprite.resolve(), sprite)
        scene['mock_backend']['person_sprite_path'] = str(sprite)
        for person in scene['mock_backend'].get('people', []):
            person.pop('sprite_path', None)
    private_config = root / 'private-scene.yaml'
    private_config.write_text(yaml.safe_dump(scene, sort_keys=False))
    config = root / 'scenario.yaml'
    # Simulator-only people/sprite/fault truth never enters the agent config.
    config.write_text(yaml.safe_dump({key: value for key, value in scene.items() if key not in {'mock_backend', 'native_radio_fault'}}, sort_keys=False))
    env = {**os.environ, 'ORT_DISABLE_TELEMETRY': '1', 'PYTHONUNBUFFERED': '1',
           'PYTHONPATH': os.pathsep.join([str(APP / 'src'), str(REPO / 'src'), str(REPO / 'aerokernel'), os.environ.get('PYTHONPATH', '')])}
    manifest = {'run_id': args.run_id, 'backend': 'mock', 'native_started': False,
                'decision_owner': 'official Cordis/DSH agent-loop', 'agent_mode': 'rule',
                'agents': agents, 'seed': args.seed, 'case_id': args.case, 'variant': args.variant,
                'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'source_scenario': str(args.scenario.resolve()), 'execution_status': 'RUNNING',
                'note': 'Mock adapter results are simulated evidence, not native flight, radio, camera, or physical delivery acceptance.'}
    # Reproduction notes only: no hash/version admission check is introduced.
    for name, command in [('source-status.txt', ['git', 'status', '--short']),
                          ('source-diff.patch', ['git', 'diff', '--', 'AeroRescue/src', 'AeroRescue/harness', 'AeroRescue/scenarios', 'AeroRescue/tools/run_mock.py'])]:
        recorded = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        (root / name).write_text(recorded.stdout + recorded.stderr)
    write(root / 'run-manifest.json', manifest)
    processes: dict[str, subprocess.Popen] = {}
    handles = []
    stop_requested = False
    def stop(*_):
        nonlocal stop_requested
        stop_requested = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def launch(name: str, command: list[str], cwd: Path) -> None:
        out = open(root / f'{name}.stdout.jsonl', 'w')
        err = open(root / f'{name}.stderr.log', 'w')
        handles.extend([out, err])
        write(root / f'{name}.command.json', command)
        processes[name] = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)
    started = time.monotonic()
    reason = 'wall_timeout'
    try:
        launch('coordinator', [args.python, '-m', 'aerorescue.companion.coordinator', '--backend', 'mock',
               '--scenario', str(private_config), '--run-id', args.run_id, '--data', str(root / 'coordinator'),
               '--socket-root', str(root / 'ipc'), '--until-ns', str(args.until_ns), '--seed', str(args.seed)], APP)
        ready_deadline = time.monotonic() + 30
        while not (root / 'ipc/ready.json').exists():
            if processes['coordinator'].poll() is not None:
                raise RuntimeError('Mock coordinator exited before readiness; see coordinator.stderr.log')
            if time.monotonic() >= ready_deadline:
                raise RuntimeError('Mock coordinator readiness deadline')
            time.sleep(.1)
        for uid in agents:
            launch(uid, [args.node, str(APP / 'harness/launch.mjs'), '--config', str(config), '--run-id', args.run_id,
                   '--uav-id', uid, '--role', 'centre' if uid == 'centre' else 'uav', '--data-dir', str(root / 'agents' / uid),
                   '--socket', 'file://' + str(root / 'ipc' / uid / 'agent.sock'), '--mode', 'rule', '--python', args.python], APP / 'harness')
        while not stop_requested and time.monotonic() - started < args.wall_timeout:
            if (root / 'stop-request.json').exists():
                reason = read(root / 'stop-request.json').get('reason', 'requested_stop')
                break
            exits = {name: proc.returncode for name, proc in processes.items() if proc.poll() is not None}
            if exits:
                reason = 'coordinator_finished' if exits == {'coordinator': 0} else 'process_exit'
                break
            if args.stop_on_outcome and (root / 'agents/centre/outcome.json').exists():
                closed = True
                for uid in agents:
                    if uid == 'centre':
                        continue
                    state = read(root / 'agents' / uid / 'runtime-state.json')
                    if ((state.get('task') or {}).get('commitment') or {}).get('protected'):
                        continue
                    returned = state.get('results', {}).get('return_after_closure', {})
                    if not state.get('mission_closed') or returned.get('status') != 'succeeded':
                        closed = False
                if closed:
                    reason = 'centre_outcome_and_agent_return_completions'
                    break
            time.sleep(.2)
        if stop_requested:
            reason = 'interrupted'
    except Exception as exc:
        reason = 'launcher_error'
        manifest['error'] = repr(exc)
    finally:
        for name in [*agents, 'coordinator']:
            proc = processes.get(name)
            if proc and proc.poll() is None:
                proc.terminate()
        for proc in processes.values():
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            # A failed host may already have exited while its own child remains.
            # These groups were created by this attempt, never external services.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for stream in handles:
            stream.close()
    summary = {'stop_reason': reason, 'wall_elapsed_s': time.monotonic() - started,
               'process_exit_codes': {name: proc.returncode for name, proc in processes.items()},
               'centre_outcome': read(root / 'agents/centre/outcome.json'), 'agents': {}}
    for uid in agents:
        data = root / 'agents' / uid
        state = read(data / 'runtime-state.json')
        calls = []
        trace = data / 'logs/tool-trace.jsonl'
        if trace.exists():
            for line in trace.read_text().splitlines():
                row = json.loads(line)
                calls.append({key: row['result'].get(key) for key in ('tool_call_id', 'tool_name', 'status', 'task_id', 'task_revision')})
        # These are actual runtime calls, kept separate from DSH request/tool events.
        dsh_calls, dsh_pending, completions = [], [], []
        event_path = data / 'logs/harness-events.jsonl'
        if event_path.exists():
            for line in event_path.read_text().splitlines():
                event = json.loads(line).get('params', {}).get('event', {})
                payload = event.get('data', {})
                if event.get('type') == 'tool/call':
                    dsh_calls.append({'seq': event['seq'], **payload})
                if event.get('type') in {'tool/result', 'user/message'}:
                    message = payload.get('message', payload)
                    for block in message.get('content', []):
                        if block.get('type') != 'text':
                            continue
                        try:
                            fact = json.loads(block['text'])
                        except (ValueError, KeyError):
                            continue
                        if isinstance(fact, dict) and fact.get('status') == 'pending':
                            dsh_pending.append({'seq': event['seq'], 'call_id': message.get('toolCallId'), **fact})
                        if isinstance(fact, dict) and fact.get('event') in {'tool.completed', 'tool.failed'}:
                            completions.append({'seq': event['seq'], **fact})
        summary['agents'][uid] = {'task': state.get('task'), 'step_index': state.get('step_index'),
                                 'task_blocker': state.get('task_blocker'), 'runtime_tool_calls': calls,
                                 'mission_closed': bool(state.get('mission_closed')),
                                 'return_after_closure': state.get('results', {}).get('return_after_closure'),
                                 'dsh_tool_calls': dsh_calls, 'dsh_pending_results': dsh_pending,
                                 'dsh_completion_inputs': completions, 'dsh_events': str(event_path)}
    summary['task_outcome'] = summary['centre_outcome'].get('outcome')
    summary['closure_complete'] = bool(summary['centre_outcome']) and all(
        uid == 'centre' or ((row['task'] or {}).get('commitment') or {}).get('protected')
        or (row['mission_closed'] and (row['return_after_closure'] or {}).get('status') == 'succeeded')
        for uid, row in summary['agents'].items())
    summary['completion'] = ('completed' if summary['task_outcome'] == 'completed_with_delivery' and summary['closure_complete']
                             else 'terminal_failure' if summary['task_outcome'] and summary['closure_complete']
                             else 'incomplete') if 'centre' in agents else 'scoped_run_finished' if reason == 'coordinator_finished' else 'incomplete'
    write(root / 'mock-summary.json', summary)
    manifest.update(execution_status='STOPPED', stop_reason=reason, completion=summary['completion'],
                    task_outcome=summary['task_outcome'], closure_complete=summary['closure_complete'], wall_elapsed_s=summary['wall_elapsed_s'])
    write(root / 'run-manifest.json', manifest)
    print(json.dumps({'run_id': args.run_id, 'path': str(root), 'stop_reason': reason,
                      'centre_outcome': summary['centre_outcome'], 'completion': summary['completion'],
                      'dsh_tool_call_counts': {uid: len(row['dsh_tool_calls']) for uid, row in summary['agents'].items()}}, ensure_ascii=False))
    return 0 if summary['completion'] in {'completed', 'scoped_run_finished'} and reason in {'coordinator_finished', 'centre_outcome_and_agent_return_completions'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
