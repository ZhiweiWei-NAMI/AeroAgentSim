#!/usr/bin/env python3
"""Fixed mock RGB-D diagnostic, real detector and production localizer.

This exercises software geometry only. Camera fixtures are evaluator-selected,
not flight trajectories, and mock world coordinates do not validate PX4 origins.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import zlib

import numpy as np
import yaml

from aerorescue.companion.geometry import backproject, locate_target
from aerorescue.companion.mock_backend import MockRpcClient, MockWorld
from aerorescue.companion.vision import RtdetrDetector
from aerorescue.companion.sensors import join_observation


def rotation_y(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rotation_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def fixtures():
    # Locked before any inference. No detection-dependent view selection.
    views = [
        ([51, 51, 25], 0, 90, 'central-near'),
        ([51, 51, 30], 0, 90, 'central-medium'),
        ([51, 51, 40], 0, 90, 'central-far'),
        ([43, 51, 40], 0, 90, 'offset-west'),
        ([59, 51, 40], 0, 90, 'offset-east'),
        ([51, 43, 40], 0, 90, 'offset-south'),
        ([51, 59, 40], 0, 90, 'offset-north'),
        ([37.5, 51, 40], 0, 90, 'edge-west'),
        ([64.5, 51, 40], 0, 90, 'edge-east'),
        ([51, 38, 40], 0, 90, 'edge-south'),
        ([51, 64, 40], 0, 90, 'edge-north'),
        ([36, 51, 40], 0, 60, 'tilted-east'),
        ([51, 36, 40], 90, 60, 'tilted-north'),
        ([41, 41, 40], 45, 70, 'tilted-diagonal'),
        ([51, 51, 30], 0, 90, 'partial-occlusion'),
    ]
    result = []
    for uid in ('u2', 'u4'):
        for position, yaw, pitch, label in views:
            # A second camera orientation is independently authored, not an
            # origin offset inferred from a rendered target or a detection.
            result.append({'sample_id': f'{uid}-{label}', 'uav_id': uid,
                'position_enu_m': position, 'yaw_deg_enu': yaw + (180 if uid == 'u4' and pitch == 90 else 0),
                'camera_pitch_deg': pitch, 'eligible': True, 'variant': label})
    # Earlier attempt's four out-of-FOV fixtures remain explicit negatives.
    # Corrected eligible edge centers project inside the vertical image with
    # a full 1.75 m billboard margin; no detector output selects this geometry.
    for uid in ('u2', 'u4'):
        for x, label in ((34, 'west'), (68, 'east')):
            result.append({'sample_id': f'original-{uid}-edge-{label}', 'uav_id': uid,
                'position_enu_m': [x, 51, 40], 'yaw_deg_enu': 180 if uid == 'u4' else 0,
                'camera_pitch_deg': 90, 'eligible': False, 'variant': 'out-of-view'})
    for label in ('empty-scene', 'fully-occluded', 'out-of-view'):
        result.append({'sample_id': label, 'uav_id': 'u2',
            'position_enu_m': [51, 51, 30] if label != 'out-of-view' else [80, 80, 30],
            'yaw_deg_enu': 0, 'camera_pitch_deg': 90,
            'eligible': False, 'variant': label})
    repeat = copy.deepcopy(result[1])
    repeat.update(sample_id='new-run-repeat', reset_of='u2-central-medium')
    result.append(repeat)
    return result


def annotation(scene, fixture, manifest):
    """Independent forward projection from evaluator fixture/asset coordinates.

    Does not use detector output, localizer functions, or returned sensor pose.
    """
    person = scene['mock_backend']['people'][0]
    camera = scene['simulator']['rgbd_camera']
    body = rotation_z(math.radians(fixture['yaw_deg_enu']))
    mount = rotation_y(math.radians(fixture['camera_pitch_deg']))
    forward, right, down = (body @ mount @ vector for vector in
                           (np.array([1, 0, 0]), np.array([0, -1, 0]), np.array([0, 0, -1])))
    origin = np.array(fixture['position_enu_m']) + body @ np.array(camera['pose_flu'][:3])
    delta = np.array(person['position_enu_m']) - origin
    z = float(delta @ forward)
    focal = camera['width'] / (2 * math.tan(camera['horizontal_fov_rad'] / 2))
    u = focal * float(delta @ right) / z + camera['width'] / 2
    v = focal * float(delta @ down) / z + camera['height'] / 2
    width = max(1, round(focal * person['width_m'] / z))
    height = max(1, round(focal * person['height_m'] / z))
    left, top = round(u - width / 2), round(v - height / 2)
    crop = manifest['crop_xyxy']; box = manifest['offline_asset_annotation_person_bbox_xyxy']
    sw, sh = manifest['sprite_size_wh']
    bbox = [left + (box[0] - crop[0]) * width / sw,
            top + (box[1] - crop[1]) * height / sh,
            left + (box[2] - crop[0]) * width / sw,
            top + (box[3] - crop[1]) * height / sh]
    return {'sample_id': fixture['sample_id'], 'target_position_enu_m': person['position_enu_m'],
            'amodal_person_bbox_xyxy': bbox, 'projected_optical_z_m': z,
            'source': 'evaluator-only fixture and source-photo annotation',
            'eligible': fixture['eligible']}


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).parents[1] / 'scenarios/rooftop-flood-mock.yaml')
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--person-sprite', type=Path, required=True)
    parser.add_argument('--sprite-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tile-size', type=int, default=None, help='Explicit optional uniform detector tiling; baseline stays single view')
    parser.add_argument('--reuse-rendered', type=Path, help='Re-run unchanged saved pixels; never overwrite the earlier attempt')
    args = parser.parse_args()
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    scene = yaml.safe_load(args.scenario.read_text()); manifest = json.loads(args.sprite_manifest.read_text())
    samples = fixtures()
    (root / 'fixed-samples.json').write_text(json.dumps({'execution_backend': 'mock',
        'fixed_before_render_and_inference': True, 'fixture_revision': 2, 'minimum_iou': .5, 'score_threshold': .5,
        'samples': samples}, indent=2))
    inputs = []
    if args.reuse_rendered is None:
        for fixture in samples:
            current = copy.deepcopy(scene)
            person = current['mock_backend']['people'][0]
            person['sprite_path'] = str(args.person_sprite.resolve())
            camera = current['simulator']['rgbd_camera']
            camera['pose_flu'][4] = math.radians(fixture['camera_pitch_deg'])
            camera['orientation'] = 'explicit mock calibration fixture'
            target = annotation(current, fixture, manifest)
            variant = fixture['variant']
            if variant == 'empty-scene': current['mock_backend']['people'] = []
            if variant in ('partial-occlusion', 'fully-occluded'):
                current['public_map']['obstacles'].append({'id': 'private-calibration-occluder',
                    'lower': [50.0, 49.9, 17.0],
                    'upper': [50.8 if variant == 'partial-occlusion' else 52.0, 52.1, 17.2]})
            directory = root / fixture['sample_id']; directory.mkdir()
            world = MockWorld(current, 'C10-' + fixture['sample_id'], directory, 42)
            rpc = MockRpcClient(world, 'flight')
            spawn = fixture['position_enu_m'] + [0, 0, math.radians(fixture['yaw_deg_enu'])]
            rpc.request('reset', {'vehicles': [{'id': fixture['uav_id'], 'spawn': spawn}], 'warmup': 0})
            rpc.request('advance', {'to_sim_ns': 1_000_000_000})
            capture = rpc.request('capture', {'vehicle': fixture['uav_id'], 'action': 'poll', 'after_sim_ns': -1})
            observation = capture['observation']
            for kind, info in capture['blobs'].items():
                import base64
                data = b''
                while len(data) < info['bytes']:
                    reply = rpc.request('capture', {'vehicle': fixture['uav_id'], 'action': 'read',
                        'frame_id': observation['frame_id'], 'kind': kind, 'offset': len(data), 'max_bytes': 262144})
                    data += base64.b64decode(reply['data_base64'])
                (directory / ('rgb.png' if kind == 'rgb' else 'depth.f32.zlib')).write_bytes(data)
            (directory / 'observation.json').write_text(json.dumps(observation, indent=2))
            (directory / 'evaluator-annotation.json').write_text(json.dumps(target, indent=2))
            inputs.append((fixture, observation, target, directory))
    else:
        frozen = json.loads((args.reuse_rendered / 'fixed-samples.json').read_text())
        samples = frozen['samples']
        (root / 'fixed-samples.json').write_text(json.dumps(frozen, indent=2))
        for fixture in samples:
            directory = args.reuse_rendered / fixture['sample_id']
            inputs.append((fixture, json.loads((directory / 'observation.json').read_text()),
                json.loads((directory / 'evaluator-annotation.json').read_text()), directory))
    # All fixtures, RGB-D and evaluator labels exist before detector construction.
    detector = RtdetrDetector(args.model, score_threshold=.5, tile_size=args.tile_size)
    results = []; errors = []; reasons = {}; tp = fp = fn = 0
    for fixture, observation, target, directory in inputs:
        result_directory = root / fixture['sample_id']; result_directory.mkdir(exist_ok=True)
        detection = detector.detect(directory / 'rgb.png', observation['frame_id'], result_directory / 'inference')
        shape = (observation['calibration']['height'], observation['calibration']['width'])
        depth = np.frombuffer(zlib.decompress((directory / 'depth.f32.zlib').read_bytes()), dtype='<f4').reshape(shape)
        locations = [locate_target(observation, d, depth) for d in detection['detections']]
        scores = [iou(d['bbox_xyxy'], target['amodal_person_bbox_xyxy']) for d in detection['detections']]
        match = int(np.argmax(scores)) if scores and max(scores) >= .5 and fixture['eligible'] else None
        row = {'sample_id': fixture['sample_id'], 'eligible': fixture['eligible'], 'detections': detection,
               'locations': locations, 'evaluator_ious': scores, 'matched_detection_index': match}
        if fixture['eligible']:
            tp += int(match is not None); fn += int(match is None)
            if match is None: reason = 'no IoU>=0.5 matched detection'
            elif locations[match]['status'] != 'available': reason = locations[match]['reason']
            else:
                error = float(np.linalg.norm((np.array(locations[match]['position_enu_m']) - target['target_position_enu_m'])[:2]))
                row['horizontal_error_m'] = error; errors.append(error); reason = None
            if reason: reasons[reason] = reasons.get(reason, 0) + 1
            row['unavailable_reason'] = reason
        fp += len(detection['detections']) - int(match is not None)
        results.append(row)
        (result_directory / 'measurement.json').write_text(json.dumps(row, indent=2))
        print(fixture['sample_id'], 'matched=' + str(match is not None), row.get('horizontal_error_m', row.get('unavailable_reason')), flush=True)
    eligible = sum(s['eligible'] for s in samples)
    rate = len(errors) / eligible
    median = float(np.median(errors)) if errors else None
    p95 = float(np.percentile(errors, 95)) if errors else None
    calibration = []
    for point in ([4., 3., 12.], [-6., 4., 9.], [0., 0., 10.]):
        k = {'K': [500, 0, 320, 0, 500, 240, 0, 0, 1], 'distortion': []}
        pixel = [500 * point[0] / point[2] + 320, 500 * point[1] / point[2] + 240]
        for convention, value in [('optical_z', point[2]), ('ray_range', float(np.linalg.norm(point)))]:
            output = backproject(pixel, value, {**k, 'depth_convention': convention})
            calibration.append({'explicit_calibration_input': True, 'convention': convention,
                'camera_point_m': point, 'output_m': output.tolist(), 'error_m': float(np.linalg.norm(output-point))})
    repeat = next(row for row in results if row['sample_id'] == 'new-run-repeat')
    original = next(row for row in results if row['sample_id'] == 'u2-central-medium')
    observations = {fixture['sample_id']: observation for fixture, observation, _, _ in inputs}
    first_pose = observations['u2-central-medium']['vehicle_estimate']
    next_observation = observations['new-run-repeat']
    stale_join = join_observation(next_observation, [first_pose], next_observation['available_sim_ns'])
    report = {'execution_backend': 'mock', 'native': False,
        'captured_inputs': str(args.reuse_rendered.resolve()) if args.reuse_rendered else str(root),
        'scope': 'fixed software renderer fixtures, real RT-DETR, production RGB-D localizer; no DSH or flight trajectory',
        'detector_tile_size': args.tile_size,
        'truncated_tile_detections': sum(row['detections'].get('truncated_tile_detections', 0) for row in results),
        'inference_views_per_frame': [len(row['detections'].get('inference_views', [None])) for row in results],
        'inference_total_wall_s': sum(row['detections']['inference_wall_ns'] for row in results) / 1e9,
        'inference_median_wall_s': float(np.median([row['detections']['inference_wall_ns'] for row in results])) / 1e9,
        'inference_p95_wall_s': float(np.percentile([row['detections']['inference_wall_ns'] for row in results], 95)) / 1e9,
        'fixed_eligible_total': eligible, 'observations_total': len(samples), 'matched_detections': tp,
        'localization_attempted': sum(len(row['locations']) for row in results), 'valid': len(errors),
        'unavailable_by_reason': reasons, 'valid_output_rate': rate,
        'horizontal_error_median_m': median, 'horizontal_error_p95_m': p95,
        'localization_thresholds_met': rate >= .9 and median is not None and median <= 2 and p95 <= 5,
        'detection': {'true_positive': tp, 'false_positive': fp, 'false_negative': fn,
            'precision': tp/(tp+fp) if tp+fp else 0, 'recall': tp/(tp+fn) if tp+fn else 0,
            'scope': 'development photo billboard, not held-out imagery; no independent benchmark generalization claim'},
        'depth_calibration': calibration,
        'new_run_repeat': {'original': original['sample_id'], 'repeat': repeat['sample_id'],
            'separate_run_and_epoch': observations['u2-central-medium']['run_id'] != next_observation['run_id'] and first_pose['clock_epoch'] != next_observation['clock_epoch'],
            'old_epoch_pose_join': stale_join['localization_status'], 'old_epoch_reason': stale_join.get('localization_reason'), 'original_error_m': original.get('horizontal_error_m'),
            'repeat_error_m': repeat.get('horizontal_error_m')},
        'native_remaining': ['PX4 NED/AMSL origins', 'measured clock alignment', 'Gazebo RGB-D calibration',
            'in-place native reset', 'held-out scenario detection', 'real-scene localization uncertainty']}
    (root / 'report.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
