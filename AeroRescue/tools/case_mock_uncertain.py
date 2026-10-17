#!/usr/bin/env python3
"""C02 component exercises: actual mock flight/capture and installed tools.

This driver selects requests; it is not a Cordis decision-loop acceptance run.
Invalid RGB-D cases are separately labelled controlled sensor inputs, not PX4
or camera failures observed in a native simulator.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
from pathlib import Path
import threading
import time
import traceback
import uuid

import numpy as np
import yaml

from aerorescue.companion.centre import RescueCentre
from aerorescue.companion.coordinator import Coordinator
from aerorescue.companion.flight import FlightClient
from aerorescue.companion.runtime import CompanionRuntime
from aerorescue.companion.sensors import CaptureDownload, download_capture, ingest_frame
from aerorescue.companion.transport import save_json


def invalid_inputs(runtime, frame, detection, root):
    """Keep the original pixels/time; these are declared corrupted input copies."""
    tools = runtime.tools
    store = tools.perception_tools().store
    original = store.observation(frame['frame_id'])
    rgb, _ = store.read(original['rgb_artifact_id'])
    packed, _ = store.read(original['depth_artifact_id'])
    depth = np.load(io.BytesIO(packed), allow_pickle=False)
    analysis = original['last_analysis']
    cases = []
    for name in ('nan_depth', 'beyond_camera_range', 'background_dominant', 'two_depth_clusters',
                 'depth_shape_mismatch', 'unregistered_depth', 'rgb_depth_time_mismatch', 'stale_pose', 'missing_depth'):
        metadata = copy.deepcopy(original)
        for key in ('last_analysis', 'locations', 'rgb_artifact_id', 'depth_artifact_id'):
            metadata.pop(key, None)
        metadata.update(frame_id=original['frame_id'] + ':controlled-' + name,
            acquisition_source='controlled_sensor_fault_input', fixture_origin_frame_id=original['frame_id'],
            fault_name=name, new_camera_observation=False)
        data = depth.copy()
        if name == 'nan_depth': data[:] = np.nan
        elif name == 'beyond_camera_range': data[:] = metadata['calibration']['depth_max_m'] + 1
        elif name in ('background_dominant', 'two_depth_clusters'):
            # Uniform spatial pattern, not evaluator-person labels. Only the
            # actual detector ROI enters the production clustering routine.
            yy, xx = np.indices(data.shape)
            data[:] = np.where(xx % 10 < (1 if name == 'background_dominant' else 5), 10, 20)
        elif name == 'depth_shape_mismatch': data = data[:-1]
        elif name == 'unregistered_depth': metadata['calibration']['registered_rgb_depth'] = False
        elif name == 'rgb_depth_time_mismatch': metadata['depth_observed_at'] -= 40_000_001
        elif name == 'stale_pose': metadata['vehicle_estimate']['source_sim_ns'] -= 40_000_001
        if name == 'missing_depth': saved = store.record(metadata, rgb)
        else:
            saved = ingest_frame(store, metadata, rgb, data, [metadata['vehicle_estimate']], metadata['available_sim_ns'])
        # Identical RGB gets the original actual inference, explicitly reused.
        store.add_analysis(saved['frame_id'], copy.deepcopy(analysis), runtime.state['sim_ns'])
        result = tools.call('locate-target', {'frame_id': saved['frame_id'], 'detection_id': detection['detection_id']}, runtime.context())
        assert result['status'] == 'unavailable' and result.get('reason'), result
        assert 'position_enu_m' not in result, result
        cases.append({'variant': name, 'frame_id': saved['frame_id'], 'result': result,
                      'scope': 'controlled sensor-input copy; original RGB inference reused'})
    missing = tools.call('detect-persons', {'frame_id': 'not-acquired-frame'}, runtime.context())
    assert missing['status'] == 'unavailable' and missing.get('reason'), missing
    cases.append({'variant': 'missing_frame', 'result': missing, 'scope': 'unrecorded local frame lookup'})
    save_json(root / 'invalid-observation-cases.json', cases)
    return cases


def run_branch(args, base, variant):
    root = args.output.resolve() / variant; root.mkdir()
    scene = copy.deepcopy(base); scene['max_real_time_factor'] = 3
    scene['mock_backend'].update(expected_clients=['u2'], startup_timeout_s=30)
    scene['mock_backend']['people'][0]['sprite_path'] = str(args.person_sprite.resolve())
    # Explicit diagnostic centre location; all traffic still goes through the
    # unchanged radio loss/delay model, not a direct business-message shortcut.
    next(n for n in scene['radio']['nodes'] if n['id'] == 'centre')['position_enu'] = [45, 90, 0]
    occluder = {'id': 'C02-canopy', 'lower': [50.0, 49.9, 17.0], 'upper': [50.8, 52.1, 17.2]}
    if variant == 'still_uncertain': occluder.update(lower=[49, 49, 17], upper=[53, 53, 17.2])
    scene['public_map']['obstacles'].append(occluder)
    save_json(root / 'fixed-inputs.json', {'execution_backend': 'mock', 'variant': variant,
        'first_view_enu_m': [51, 51, 30], 'occluder': occluder,
        'still_uncertain_second_view_enu_m': [62, 51, 40], 'detector_score_threshold': .5,
        'detector_tile_size': args.tile_size, 'frozen_before_inference': True})
    (root / 'private-scene.yaml').write_text(yaml.safe_dump(scene))
    public = copy.deepcopy(scene); public.pop('mock_backend')
    public.update(model_path=str(args.model.resolve()), embedding_path=None)
    public.setdefault('perception', {})['detector_tile_size'] = args.tile_size
    (root / 'agent-config.yaml').write_text(yaml.safe_dump(public))
    run_id = args.output.name + '-' + variant
    coordinator = Coordinator(scene, run_id, root / 'coordinator', root / 'ipc', backend='mock', until_ns=600_000_000_000)
    errors = []
    def run():
        try: coordinator.run()
        except Exception as error: errors.append(repr(error)); traceback.print_exc()
    thread = threading.Thread(target=run); thread.start(); runtime = None
    try:
        while not (root / 'ipc/ready.json').exists():
            if errors: raise RuntimeError(errors)
            time.sleep(.01)
        runtime = CompanionRuntime(public, run_id=run_id, uav_id='u2', data_dir=root / 'u2', socket_path='file://' + str(root / 'ipc/u2/agent.sock'))
        flight = runtime.flight
        def tick():
            if errors: raise RuntimeError(errors)
            runtime.tick(); time.sleep(.01)
        def call(name, request, control=False):
            rid = uuid.uuid4().hex; folder = runtime.root / 'tasks/tool-requests'; folder.mkdir(exist_ok=True)
            save_json(folder / (rid + '.queued.json'), {'request_id': rid, 'control' if control else 'tool': name, 'request': request})
            until = time.monotonic() + 100
            while not (folder / (rid + '.result.json')).exists():
                if time.monotonic() > until: raise TimeoutError(name)
                tick()
            result = json.loads((folder / (rid + '.result.json')).read_text())
            print(variant, name, result['status'], flush=True)
            return result
        while not runtime.state['seen_messages']: tick()
        accepted = call('accept-task', {'message_id': runtime.state['seen_messages'][0]}, True)
        assert accepted['status'] == 'succeeded', accepted
        for action, params in [('arm', {}), ('takeoff', {'altitude_m': 40}), ('goto', {'position_enu_m': [51, 51, 30], 'yaw_deg': 90})]:
            result = call('flight-command', {'action': action, 'params': params})
            assert result['status'] == 'succeeded', result
        def capture():
            after = flight.state()['sim_ns']
            while True:
                result = runtime._gateway_request(op='capture_request', after_sim_ns=after)
                if result['status'] != 'pending': break
                tick()
            assert result['status'] == 'succeeded', result
            saved = download_capture(runtime.tools.perception_tools().store, runtime._gateway_request, result)
            return saved, result
        first, first_capture = capture()
        first_detection = call('detect-persons', {'frame_id': first['frame_id']})
        assert first_detection['status'] == 'succeeded', first_detection
        report = {'execution_backend': 'mock', 'native': False, 'scope': 'component requests, not Cordis policy run',
                  'variant': variant, 'first_frame': first, 'first_detection': first_detection}
        if variant == 'occluded_then_visible':
            detections = first_detection['detections']; assert detections, first_detection
            # Highest model score, never ground-truth location or annotation.
            person = max(detections, key=lambda d: d['score'])
            location = call('locate-target', {'frame_id': first['frame_id'], 'detection_id': person['detection_id']})
            assert location['status'] == 'available', location
            same = call('verify-target', {'first_frame_id': first['frame_id'], 'second_frame_id': first['frame_id'],
                'first_detection_id': person['detection_id'], 'second_detection_id': person['detection_id']})
            assert same['status'] == 'uncertain', same
            moved = call('reobserve-target', {'frame_id': first['frame_id'], 'detection_id': person['detection_id']})
            assert moved['status'] == 'succeeded' and moved['frames'], moved
            second = moved['frames'][-1]
            second_detection = call('detect-persons', {'frame_id': second['frame_id']})
            assert second_detection['detections'], second_detection
            second_person = max(second_detection['detections'], key=lambda d: d['score'])
            second_location = call('locate-target', {'frame_id': second['frame_id'], 'detection_id': second_person['detection_id']})
            verdict = call('verify-target', {'first_frame_id': first['frame_id'], 'second_frame_id': second['frame_id'],
                'first_detection_id': person['detection_id'], 'second_detection_id': second_person['detection_id']})
            report.update(initial_uncertainty_source='driver explicitly compared the first frame with itself; partial occlusion did not prevent the actual detector/location from being available',
                first_location=location, same_image_verification=same, reobserve_mission=moved,
                second_frame=second, second_detection=second_detection, second_location=second_location, verification=verdict)
            assert verdict['status'] == 'confirmed', verdict
            report['invalid_inputs'] = invalid_inputs(runtime, first, person, root)
        else:
            assert first_detection['detections'] == [], first_detection
            # Without a location the current tool cannot choose a target view.
            no_location = call('reobserve-target', {'frame_id': first['frame_id'], 'detection_id': 'no-detection'})
            assert no_location['status'] == 'unavailable', no_location
            moved = call('flight-command', {'action': 'goto', 'params': {'position_enu_m': [62, 51, 40], 'yaw_deg': 90}})
            assert moved['status'] == 'succeeded', moved
            second, _ = capture()
            second_detection = call('detect-persons', {'frame_id': second['frame_id']})
            assert second_detection['detections'] == [], second_detection
            comparison = call('compare-observations', {'first_frame_id': first['frame_id'], 'second_frame_id': second['frame_id']})
            verdict = call('verify-target', {'first_frame_id': first['frame_id'], 'second_frame_id': second['frame_id'],
                'first_detection_id': 'no-detection', 'second_detection_id': 'no-detection'})
            assert verdict['status'] == 'uncertain', verdict
            sent = call('send-evidence', {'data': {'observation_verdict': 'uncertain', 'reason': verdict['reason'],
                'frame_ids': [first['frame_id'], second['frame_id']]}, 'destination': 'centre'})
            assert sent['status'] == 'succeeded', sent
            centre_client = FlightClient('file://' + str(root / 'ipc/centre/agent.sock'), run_id)
            received = centre_client.request('radio_inbox')['messages']
            message = next(row for row in received if row['message_id'] == sent['message_id'])
            centre_root = root / 'centre'; centre_root.mkdir()
            centre = RescueCentre(centre_root, execution_backend='mock')
            centre.notice_submitted(message['event_id'], 0, 600_000_000_000, scene['incident']['demand_source'])
            centre.consume(message, flight.state()['sim_ns'])
            try:
                centre.finish(message['event_id'], 'completed_with_delivery', flight.state()['sim_ns'])
                raise AssertionError('uncertain evidence accepted as rescue completion')
            except ValueError as error: completion_rejection = str(error)
            outcome = centre.finish(message['event_id'], 'unresolved_evidence', flight.state()['sim_ns'])
            report.update(second_frame=second, second_detection=second_detection, verification=verdict,
                comparison=comparison, no_location_reobserve=no_location, fixed_second_view_mission=moved,
                actual_radio_report=message, completion_rejection=completion_rejection, outcome=outcome,
                policy_boundary='driver selected the second public view and unresolved finish; current rule does not autonomously reobserve without a usable first location')
        assert first['frame_id'] != second['frame_id'] and first['rgb_sha256'] != second['rgb_sha256']
        assert first['observed_at'] < second['observed_at']
        # Do not return an old cached frame as a new acquisition.
        pending = runtime._gateway_request(op='capture_poll', after_sim_ns=600_000_000_000)
        assert pending['status'] == 'pending' and pending.get('reason'), pending
        partial = CaptureDownload(root / 'incomplete-download', first_capture)
        try:
            partial.finish(runtime.tools.perception_tools().store)
            raise AssertionError('incomplete capture accepted')
        except ValueError as error: missing_chunks = str(error)
        for _ in range(5): capture()
        expired = runtime._gateway_request(op='capture_read', frame_id=first['frame_id'], kind='rgb', offset=0, max_bytes=1024)
        assert expired['status'] == 'unavailable' and expired.get('reason'), expired
        report.update(next_frame_pending=pending, incomplete_capture_rejection=missing_chunks, expired_frame=expired,
            sensor_scope='missing chunk/expired buffer are exercised; no native camera outage injected')
        rows = [json.loads(line) for line in (root / 'coordinator/mock-world.jsonl').read_text().splitlines()]
        report['payload_release_count'] = sum(row['kind'] == 'payload_release' for row in rows)
        assert report['payload_release_count'] == 0
        report['remaining'] = ['native RGB-D/PX4 sources', 'five-Cordis decision autonomy for these variants']
        save_json(root / 'report.json', report)
        print('DONE', variant, flush=True)
    except Exception as error:
        save_json(root / 'failure.json', {'error': repr(error), 'traceback': traceback.format_exc()}); raise
    finally:
        if runtime is not None: runtime.close()
        coordinator.stop_requested = True; thread.join(10)
        if errors: raise RuntimeError(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--person-sprite', type=Path, required=True)
    parser.add_argument('--tile-size', type=int, default=None)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).parents[1] / 'scenarios/rooftop-flood-mock.yaml')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    base = yaml.safe_load(args.scenario.read_text())
    for variant in ('occluded_then_visible', 'still_uncertain'): run_branch(args, base, variant)


if __name__ == '__main__': main()
