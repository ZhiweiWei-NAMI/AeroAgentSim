#!/usr/bin/env python3
"""Run explicit mock adapters with real Cordis/DSH owners; retain every attempt.

This runner does not enqueue tools, inject proposals, or label mock as native.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import resource
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
    parser.add_argument('--case', choices=['C01', 'C02', 'C03', 'C04', 'C07', 'C08'], default='C01')
    parser.add_argument('--variant', choices=['no_person', 'occluded_then_visible', 'still_uncertain', 'low_energy_after_offer', 'payload_unavailable', 'role_id_permutation', 'missing_fragment_nack_then_retry', 'path_lost_then_second_attempt', 'no_valid_path', 'lost_assignment_before_acceptance', 'joint_did_not_release', 'final_ack_lost', 'confirmation_final_ack_lost', 'second_incident_queued', 'deadline_expired', 'no_eligible_vehicle'])
    parser.add_argument('--until-ns', type=int, default=900_000_000_000)
    parser.add_argument('--wall-timeout', type=float, default=1000)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--node', default='node')
    parser.add_argument('--model-cache', type=Path)
    parser.add_argument('--person-sprite', type=Path, help='Existing renderer asset; retained only in the private mock configuration')
    parser.add_argument('--restart-agent', help='One existing agent to restart once, keeping its data and the running coordinator')
    parser.add_argument('--restart-at-sim-ns', type=int)
    parser.add_argument('--restart-after-release', action='store_true', help='Restart only when this agent has observed release but has not received its radio receipt')
    parser.add_argument('--stop-on-outcome', action='store_true', help='Stop after centre outcome and actual nonprotected agent return completions')
    args = parser.parse_args()
    root = (args.output or APP / 'runs/mock' / args.run_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    scene = yaml.safe_load(args.scenario.read_text())
    scene['seed'] = args.seed
    if scene.get('mock_backend', {}).get('person_sprite_required') and not args.person_sprite:
        raise ValueError('This mock scene requires --person-sprite PATH. Prepare it with tools/prepare_mock_sprite.py from the documented existing image; see docs/mock-visual-asset.md.')
    if args.case == 'C02' and args.variant == 'no_person':
        scene.setdefault('mock_backend', {})['people'] = []
    elif args.case == 'C02' and args.variant in {'occluded_then_visible', 'still_uncertain'}:
        scene['public_map'].update(search_polygon=[[46, 46], [56, 46], [56, 56], [46, 56]],
                                   search_row_spacing_m=5, search_altitude_m=40,
                                   uncertain_reobserve_altitude_m=30)
        for action in scene['incident']['initial_graph']['actions']:
            if action.get('role') == 'inspect':
                action['target'] = [51, 51, 40]
        lower, upper = ([50, 49.9, 17], [50.8, 52.1, 17.2]) if args.variant == 'occluded_then_visible' else ([49, 49, 17], [53, 53, 17.2])
        scene['public_map']['obstacles'].append({'id': 'mock-public-canopy', 'lower': lower, 'upper': upper})
    elif args.case == 'C08' and args.variant in {'second_incident_queued', 'deadline_expired', 'no_eligible_vehicle'}:
        scene['incident'].update(discover_secondary_clue=True, secondary_clue_separation_m=15.0,
                                 secondary_clue_ttl_ns=30_000_000_000 if args.variant == 'deadline_expired' else 120_000_000_000)
        if args.variant == 'no_eligible_vehicle':
            scene['incident'].update(secondary_clue_ttl_ns=600_000_000_000,
                                     secondary_demand_source='Public task notice also requests emergency supplies for a second flood-isolated group if separately observed.')
            scene['vehicles']['u2']['capacity_j'] = 90_000
            scene['vehicles']['u2']['energy_budget'] = {'initial_fraction': 1.0, 'max_gap_ns': 1_000_000_000}
        scene.setdefault('mock_backend', {}).setdefault('people', []).append(
            {'position_enu_m': [74, 74, 15.8], 'height_m': 1.75, 'width_m': 1.236})
    elif args.case == 'C04' and args.variant == 'payload_unavailable':
        scene.pop('payload', None)
        for vehicle in scene['vehicles'].values():
            vehicle['initial_payload_kg'] = 0.0
    elif args.case == 'C04' and args.variant == 'role_id_permutation':
        ids = list(scene['vehicles'])
        mapping = dict(zip(ids, ids[1:] + ids[:1]))
        rotated = {mapping[uid]: profile for uid, profile in scene['vehicles'].items()}
        scene['vehicles'] = {uid: rotated[uid] for uid in ids}
        scene['payload']['carrier'] = mapping[scene['payload']['carrier']]
    elif args.case == 'C04' and args.variant == 'low_energy_after_offer':
        u2, u4 = scene['vehicles']['u2'], scene['vehicles']['u4']
        u2['energy_budget'] = {'initial_fraction': .23, 'max_gap_ns': 1_000_000_000}
        u4['capabilities'] = list(dict.fromkeys([*u4['capabilities'], 'inspect']))
        u2['spawn'][:2], u4['spawn'][:2] = u4['spawn'][:2], u2['spawn'][:2]
    elif args.case == 'C03' and args.variant in {'path_lost_then_second_attempt', 'no_valid_path', 'lost_assignment_before_acceptance'}:
        scene.setdefault('mock_backend', {})['radio_fault'] = {'case_id': 'C03', 'variant': args.variant, 'outage_duration_ns': 20_000_000_000}
    elif args.case == 'C07' and args.variant == 'joint_did_not_release':
        scene.setdefault('mock_backend', {})['joint_release_blocked'] = True
    elif (args.case == 'C03' and args.variant in {'missing_fragment_nack_then_retry', 'final_ack_lost'}) or (args.case == 'C07' and args.variant == 'confirmation_final_ack_lost'):
        scene['native_radio_fault'] = {'case_id': args.case, 'variant': args.variant}
    elif args.variant or args.case != 'C01':
        raise ValueError('Case and variant do not match an implemented existing scenario input')
    agents = args.agents.split(',')
    if len(set(agents)) != len(agents) or any(uid not in {*scene['vehicles'], 'centre'} for uid in agents):
        raise ValueError('agents must be distinct configured vehicle IDs or centre')
    restart_triggers = int(args.restart_at_sim_ns is not None) + int(args.restart_after_release)
    if (bool(args.restart_agent) and restart_triggers != 1) or (not args.restart_agent and restart_triggers) or args.restart_agent and args.restart_agent not in agents:
        raise ValueError('Restart needs an existing --restart-agent and exactly one actual-state or simulation-time trigger')
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
    def record_source(prefix: str = '') -> None:
        for name, command in [('source-head.txt', ['git', 'rev-parse', 'HEAD']),
                              ('source-status.txt', ['git', 'status', '--short']),
                              ('source-diff.patch', ['git', 'diff', '--', 'AeroRescue/src', 'AeroRescue/harness', 'AeroRescue/scenarios', 'AeroRescue/tools/run_mock.py'])]:
            recorded = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
            (root / (prefix + name)).write_text(recorded.stdout + recorded.stderr)
    record_source()
    write(root / 'run-manifest.json', manifest)
    processes: dict[str, subprocess.Popen] = {}
    commands: dict[str, tuple[list[str], Path]] = {}
    generations: dict[str, int] = {}
    lifecycle: list[dict] = []
    handles = []
    stop_requested = False
    def stop(*_):
        nonlocal stop_requested
        stop_requested = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def launch(name: str, command: list[str], cwd: Path) -> None:
        generation = generations.get(name, -1) + 1
        generations[name] = generation
        stem = name if generation == 0 else f'{name}.restart-{generation}'
        if generation > 0:
            record_source(stem + '.')
        out = open(root / f'{stem}.stdout.jsonl', 'w')
        err = open(root / f'{stem}.stderr.log', 'w')
        handles.extend([out, err])
        write(root / f'{name}.command.json', command)
        processes[name] = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)
        commands[name] = (command, cwd)
        lifecycle.append({'event': 'started', 'name': name, 'generation': generation, 'pid': processes[name].pid, 'wall_ns': time.time_ns()})
        write(root / 'process-lifecycle.json', lifecycle)
    started = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
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
        restarted = False
        last_sample = 0.0
        while not stop_requested and time.monotonic() - started < args.wall_timeout:
            if (root / 'stop-request.json').exists():
                reason = read(root / 'stop-request.json').get('reason', 'requested_stop')
                break
            exits = {name: proc.returncode for name, proc in processes.items() if proc.poll() is not None}
            if exits:
                reason = 'coordinator_finished' if exits == {'coordinator': 0} else 'process_exit'
                break
            if time.monotonic() - last_sample >= 30:
                lifecycle.append({'event': 'alive', 'wall_ns': time.time_ns(), 'wall_elapsed_s': time.monotonic() - started,
                                  'processes': {name: {'pid': proc.pid, 'generation': generations[name]} for name, proc in processes.items()}})
                write(root / 'process-lifecycle.json', lifecycle)
                last_sample = time.monotonic()
            if args.restart_agent and not restarted:
                state = read(root / 'agents' / args.restart_agent / 'runtime-state.json')
                payload = read(root / 'agents' / args.restart_agent / 'payload-state.json')
                released_without_receipt = any(row.get('released') and not row.get('receipt_delivered') for row in payload.values())
                restart_due = released_without_receipt if args.restart_after_release else state.get('sim_ns', -1) >= args.restart_at_sim_ns
                if restart_due:
                    old = processes[args.restart_agent]
                    write(root / f'{args.restart_agent}.before-restart-state.json', state)
                    write(root / f'{args.restart_agent}.before-restart-payload.json', payload)
                    old.terminate()
                    old.wait(timeout=20)
                    # This attempt created the process group. Ensure an orphaned
                    # worker cannot overlap its replacement on the same data.
                    try:
                        os.killpg(old.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    else:
                        group_deadline = time.monotonic() + 2
                        while time.monotonic() < group_deadline:
                            try:
                                os.killpg(old.pid, 0)
                            except ProcessLookupError:
                                break
                            time.sleep(.05)
                        else:
                            try:
                                os.killpg(old.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    lifecycle.append({'event': 'restart-stop', 'name': args.restart_agent, 'pid': old.pid,
                                      'exit_code': old.returncode, 'sim_ns': state['sim_ns'], 'wall_ns': time.time_ns(),
                                      'released_without_receipt_at_trigger': released_without_receipt,
                                      'coordinator_pid_unchanged': processes['coordinator'].pid})
                    launch(args.restart_agent, *commands[args.restart_agent])
                    restarted = True
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
        lifecycle.append({'event': 'finished', 'wall_ns': time.time_ns(), 'exit_codes': {name: proc.returncode for name, proc in processes.items()}})
        write(root / 'process-lifecycle.json', lifecycle)
        for stream in handles:
            stream.close()
    summary = {'stop_reason': reason, 'wall_elapsed_s': time.monotonic() - started,
               'process_exit_codes': {name: proc.returncode for name, proc in processes.items()},
               'centre_outcome': read(root / 'agents/centre/outcome.json'), 'agents': {}}
    summary['centre_outcomes'] = {path.stem: read(path) for path in (root / 'agents/centre/outcomes').glob('*.json')}
    summary['primary_event_id'] = scene.get('incident', {}).get('event_id')
    primary = summary['centre_outcomes'].get(summary['primary_event_id'])
    if primary:
        summary['centre_outcome'] = primary
    summary['restart'] = {'requested_agent': args.restart_agent,
                          'actual_events': [event for event in lifecycle if event['event'] == 'restart-stop'],
                          'note': 'Only recorded actual trigger state establishes coverage; no event means the requested window was not observed.'}
    summary['dsh_completion_input_scope'] = 'Consumed user/message events; pending inbox-splice notifications remain in the raw DSH log and are not silently counted as consumed.'
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    summary['resource_usage'] = {
        'source': 'resource.getrusage(RUSAGE_CHILDREN)',
        'user_cpu_s': usage_after.ru_utime - usage_before.ru_utime,
        'system_cpu_s': usage_after.ru_stime - usage_before.ru_stime,
        'linux_child_ru_maxrss_kib': usage_after.ru_maxrss,
        'scope': 'Waited child-process accounting; ru_maxrss is not the aggregate concurrent process-tree or machine peak.',
        'rule_adapter_network_model_tokens': 0,
        'cost_note': 'Local rule mode makes no live provider request; zero network-model tokens does not estimate live-model cost.',
    }
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
