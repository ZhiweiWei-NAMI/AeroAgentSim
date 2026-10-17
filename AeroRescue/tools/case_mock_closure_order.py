#!/usr/bin/env python3
"""Actual mock radio ordering and execution-service closure persistence.

The diagnostic driver chooses message order and queued controls. This is not a
five-agent autonomous-policy run and does not modify runtime state to pass.
"""
import argparse
import copy
import json
from pathlib import Path
import threading
import time
import traceback
import uuid

import yaml
from aerorescue.companion.coordinator import Coordinator
from aerorescue.companion.flight import FlightClient
from aerorescue.companion.runtime import CompanionRuntime
from aerorescue.companion.transport import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).parents[1] / 'scenarios/rooftop-flood-mock.yaml')
    args = parser.parse_args(); root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    scene = yaml.safe_load(args.scenario.read_text()); scene['max_real_time_factor'] = 3
    scene['vehicles']['u2']['initial_task'] = {'task_id': 'original-A', 'task_revision': 1, 'workflow': []}
    scene['mock_backend'].update(expected_clients=['u2'], startup_timeout_s=30)
    scene['execution_backend'] = 'mock'
    (root / 'private-scene.yaml').write_text(yaml.safe_dump(scene))
    public = copy.deepcopy(scene); public.pop('mock_backend'); public.update(model_path=None, embedding_path=None)
    (root / 'agent-config.yaml').write_text(yaml.safe_dump(public))
    coordinator = Coordinator(scene, root.name, root / 'coordinator', root / 'ipc', backend='mock', until_ns=180_000_000_000)
    errors = []
    def world():
        try: coordinator.run()
        except Exception as exc: errors.append(repr(exc))
    thread = threading.Thread(target=world); thread.start(); runtime = None
    try:
        until = time.monotonic() + 30
        while not (root / 'ipc/ready.json').exists():
            if errors or time.monotonic() > until: raise RuntimeError(errors or 'ready timeout')
            time.sleep(.01)
        def create():
            return CompanionRuntime(public, run_id=root.name, uav_id='u2', data_dir=root / 'u2', socket_path='file://' + str(root / 'ipc/u2/agent.sock'))
        runtime = create(); centre = FlightClient('file://' + str(root / 'ipc/centre/agent.sock'), root.name)
        def tick():
            if errors: raise RuntimeError(errors)
            runtime.tick(); time.sleep(.01)
        def wait(predicate, timeout=40):
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() >= deadline: raise TimeoutError('diagnostic observation timeout')
                tick()
        def queue(name, request, control=True):
            rid = uuid.uuid4().hex
            save_json(runtime.root / 'tasks/tool-requests' / (rid + '.queued.json'), {'request_id': rid, 'control' if control else 'tool': name, 'request': request})
            return rid
        def result(rid):
            p = runtime.root / 'tasks/tool-requests' / (rid + '.result.json')
            return json.loads(p.read_text()) if p.exists() else None
        def call(name, request, control=True):
            rid = queue(name, request, control); wait(lambda: result(rid) is not None); return result(rid)
        def send(event, kind, data, revision=1, client=None):
            client = client or centre
            reply = client.request('radio_send', destination='u2', kind=kind, data=data,
                event_id=event, task_id=event + ('-closed' if kind == 'report' else '-task'), task_revision=revision,
                deadline_ns=runtime.state['sim_ns'] + 60_000_000_000)
            request_id = reply.get('request_id')
            while 'message_id' not in reply:
                tick(); reply = client.request('result', pending_request_id=request_id)
            mid = reply['message_id']; wait(lambda: mid in runtime.state['seen_messages'])
            return mid
        wait(lambda: bool(runtime.state['seen_messages']))
        bootstrap = runtime.state['seen_messages'][0]
        assert call('accept-task', {'message_id': bootstrap})['status'] == 'succeeded'
        original = copy.deepcopy(runtime.state['task'])
        closed_b = send('closed-B', 'report', {'kind': 'mission_closed', 'outcome': 'unfulfilled_no_resource'})
        assert 'closed-B' in runtime.state['mission_closures']
        assert closed_b not in runtime.state.get('applied_messages', [])
        assert runtime.state['task'] == original and not runtime.state.get('mission_closed')
        late_b = send('closed-B', 'notice', {'task': {'workflow': []}})
        reject_b = call('accept-task', {'message_id': late_b}); assert reject_b['status'] == 'rejected'
        assert call('apply-radio-control', {'message_id': closed_b})['status'] == 'succeeded'
        runtime.close(); runtime = create(); tick()
        late_b2 = send('closed-B', 'notice', {'task': {'workflow': []}}, revision=2)
        reject_b2 = call('accept-task', {'message_id': late_b2}); assert reject_b2['status'] == 'rejected'
        open_c = send('open-C', 'notice', {'task': {'workflow': []}})
        assert call('accept-task', {'message_id': open_c})['status'] == 'succeeded'
        other = FlightClient('file://' + str(root / 'ipc/u4/agent.sock'), root.name)
        unauthorized = send('open-C', 'report', {'kind': 'mission_closed'}, client=other)
        rejected_source = call('apply-radio-control', {'message_id': unauthorized})
        assert rejected_source['status'] == 'rejected' and 'open-C' not in runtime.state['mission_closures']
        for action, params in [('arm', {}), ('takeoff', {'altitude_m': 20})]:
            assert call('flight-command', {'action': action, 'params': params}, False)['status'] == 'succeeded'
        moving = queue('flight-command', {'action': 'goto', 'params': {'position_enu_m': [100, 0, 20.2], 'yaw_deg': 90}}, False)
        wait(lambda: bool(runtime.active_tool))
        offer_d = send('closed-D', 'notice', {'task': {'workflow': []}})
        pending_d = queue('accept-task', {'message_id': offer_d}); wait(lambda: runtime.state.get('pending_task', {}).get('message_id') == offer_d)
        closed_d = send('closed-D', 'report', {'kind': 'mission_closed', 'outcome': 'expired'})
        assert call('apply-radio-control', {'message_id': closed_d})['status'] == 'succeeded'
        wait(lambda: result(pending_d) is not None)
        assert result(pending_d)['status'] == 'failed' and runtime.state['task']['event_id'] == 'open-C'
        assert not runtime.state.get('mission_closed')
        wait(lambda: result(moving) is not None)
        assert result(moving)['status'] == 'succeeded'
        assert call('flight-command', {'action': 'land'}, False)['status'] == 'succeeded'
        report = {'status': 'MOCK_PASS', 'scope': __doc__, 'run_id': root.name,
            'closed_B_message_id': closed_b, 'late_notice_rejected': reject_b,
            'restart_late_notice_rejected': reject_b2, 'pending_D_rejected': result(pending_d),
            'noncentre_control_rejected': rejected_source,
            'unrelated_C_flight_result': result(moving), 'final_event': runtime.state['task']['event_id'],
            'persisted_closure_events': list(runtime.state['mission_closures']), 'finished_sim_ns': runtime.state['sim_ns']}
        save_json(root / 'report.json', report); print(json.dumps({'status': report['status'], 'output': str(root)}))
    except Exception as exc:
        save_json(root / 'failure.json', {'error': repr(exc), 'traceback': traceback.format_exc()}); raise
    finally:
        if runtime: runtime.close()
        coordinator.stop_requested = True; thread.join(timeout=20)
    if errors: raise RuntimeError(errors)


if __name__ == '__main__': main()
