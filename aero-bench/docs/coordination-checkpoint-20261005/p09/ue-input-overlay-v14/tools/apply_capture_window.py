"""Expose the existing 0..900 capture contract; preserve extra recorded bytes.

This is a serialization operation on the new overlay only. It never runs a
simulator or changes source trajectories/events. The ordinary UE truth/weather
files end at tick900 even when an importer ignores supplemental metadata.
"""
import copy
import csv
import hashlib
import json
import os
import pathlib

import orjson

ROOT = pathlib.Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/ue_input_overlay_v14')
END = 900


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def partition(path, suffix_path):
    """Split existing JSONL on ticks, retaining every original byte in order."""
    if suffix_path.exists():
        raise ValueError('Capture window already partitioned: ' + str(suffix_path))
    original = hashlib.sha256()
    counts = [0, 0]
    sizes = [0, 0]
    tick_ranges = [None, None]
    temporary = path.with_name(path.name + '.window-tmp')
    with path.open('rb') as source, temporary.open('xb') as prefix, suffix_path.open('xb') as suffix:
        last_tick = -1
        for line in source:
            row = orjson.loads(line)
            tick = row['tick']
            if tick < last_tick:
                raise ValueError('Unordered source ticks: ' + str(path))
            last_tick = tick
            target = 0 if tick <= END else 1
            (prefix if target == 0 else suffix).write(line)
            original.update(line)
            counts[target] += 1
            sizes[target] += len(line)
            if tick_ranges[target] is None:
                tick_ranges[target] = [tick, tick]
            tick_ranges[target][1] = tick
    combined = hashlib.sha256()
    for part in (temporary, suffix_path):
        with part.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                combined.update(block)
    if combined.digest() != original.digest():
        raise ValueError('Partition changed original JSONL bytes: ' + str(path))
    os.replace(temporary, path)
    return {'original_bytes': sum(sizes), 'original_sha256': original.hexdigest(),
            'prefix_records': counts[0], 'suffix_records': counts[1],
            'prefix_bytes': sizes[0], 'suffix_bytes': sizes[1],
            'prefix_ticks': tick_ranges[0], 'suffix_ticks': tick_ranges[1],
            'concatenation_matches_original_bytes': True}


assembly = read(ROOT / 'assembly_manifest.json')
if (ROOT / 'capture_window_report.json').exists():
    raise ValueError('Window operation already completed; do not repeat it.')
reports = []
for episode in assembly['episodes']:
    directory = ROOT / pathlib.Path(episode['ue_entry']).parent
    relative = directory.relative_to(ROOT).as_posix()
    recorded_end = episode['duration_ticks']
    partitions = {}
    if recorded_end > END:
        extra = directory / 'additional_recorded_suffix'
        extra.mkdir(exist_ok=False)
        for name in ('truth_frames.jsonl', 'trajectories.jsonl', 'weather_meta.jsonl'):
            partitions[name] = partition(directory / name, extra / name)
    window = {
        'schema_version': 'p09.capture-support-window/v1',
        'episode_id': episode['episode_id'],
        'authority': {'readme': 'AERO_WORLD/README.md:83',
                      'executable_contract': 'Dataset/tools/roi_contract.py',
                      'baseline_episode': episode['base_capture_dir']},
        'tick_start': 0, 'tick_end_inclusive': END, 'tick_hz': 10,
        'dt_s': 0.1, 'sim_time_start_s': 0.0, 'sim_time_end_s': 90.0,
        'capture_step_ticks': 5, 'capture_rate_hz': 2.0,
        'planned_capture_ticks': list(range(0, END + 1, 5)),
        'planned_capture_frames': 181,
        'actual_sensor_frames_collected_by_this_operation': 0,
        'recorded_source_tick_end': recorded_end,
        'ue_input_files_are_physically_limited_to_window': True,
        'outside_window': {
            'multimodal_support_mask': False, 'multimodal_status': 'UNKNOWN_UNOBSERVED',
            'not_an_event_negative': True,
            'retained_source_events_and_trajectories': 'sources/' + episode['episode_id'],
            'retained_projected_suffix': relative + '/additional_recorded_suffix' if partitions else None,
            'background_extrapolation_or_forward_fill': False,
        },
        'story_status': episode['story_status'], 'unfired_events': episode['unfired_events'],
        'story_acceptance_is_separate_from_capture_window_completeness': True,
        'sensor_rig': 'Existing UE rig/assets required; no new extrinsic or calibration certification.',
    }
    if partitions:
        window['missing_extended_background'] = {
            'tick_start': END + 1, 'tick_end': recorded_end,
            'sim_time_start_s': 90.1, 'sim_time_end_s': recorded_end / 10,
            'sources': ['SUMO vehicle/light/incident observations', 'global-UAV background observations'],
            'excluded_from_ue_capture': True,
        }
    write(directory / 'capture_window.json', window)
    manifest = read(directory / 'episode_manifest.json')
    manifest['p09_recorded_source_horizon'] = {'tick_end': recorded_end, 'sim_time_end_s': recorded_end / 10}
    manifest['duration_ticks'] = END
    manifest['time_range']['tick_end'] = END
    manifest['time_range']['sim_time_end'] = 90.0
    if partitions:
        manifest['p09_before_capture_window_record_counts'] = copy.deepcopy(manifest['record_counts'])
        for name, stats in partitions.items():
            key = name.removesuffix('.jsonl')
            manifest['record_counts'][key] = stats['prefix_records']
            manifest['canonical_record_counts'][key] = stats['prefix_records']
        stats = manifest['capture_truth_sync']['stats']
        stats['output_truth_entities'] = partitions['trajectories.jsonl']['prefix_records']
        stats['trajectory_rows'] = partitions['trajectories.jsonl']['prefix_records']
        stats['truth_frames'] = 901
        # Patched-actor counts describe the earlier full recorded projection.
        stats['patched_actor_rows_scope'] = 'Full projection including retained additional suffix.'
    manifest['capture_support_window'] = relative + '/capture_window.json'
    manifest['unrestricted_event_logs_retained'] = True
    manifest['generation']['status'] = 'EXISTING_RECORDED_INPUTS_CAPTURE_WINDOW_SERIALIZED'
    write(directory / 'episode_manifest.json', manifest)
    plan = read(directory / 'scenario_plan.json')
    plan['p09_recorded_source_horizon'] = {'tick_end': recorded_end, 'sim_time_end_s': recorded_end / 10}
    plan['runtime_contract']['tick_end'] = END
    plan['capture_truth_sync']['stats'] = manifest['capture_truth_sync']['stats']
    plan['capture_support_window'] = relative + '/capture_window.json'
    write(directory / 'scenario_plan.json', plan)
    package = read(directory / 'scenario_package.json')
    # Do not invent a UE capture_plan schema. The existing importer entry still
    # consumes its original truth/weather filenames; they now end at tick900.
    package['p09_capture_support_window'] = relative + '/capture_window.json'
    package['p09_additional_records_are_not_ue_frames'] = window['outside_window']['retained_projected_suffix']
    write(directory / 'scenario_package.json', package)
    config = read(directory / 'render_host_config.json')
    config['output_dir'] = 'G:/aw_cap/_direct_render_host_capture_filtered_v14/' + episode['episode_id']
    write(directory / 'render_host_config.json', config)
    episode['recorded_source_duration_ticks'] = recorded_end
    episode['capture_duration_ticks'] = END
    episode['capture_truth_frames'] = 901
    episode['planned_capture_frames'] = 181
    episode['capture_window'] = relative + '/capture_window.json'
    episode['capture_support_complete_in_window'] = True
    if episode['group'] == 'old_peer_load_actual_results':
        episode['adoption_status'] = 'RETAINED_SAME_OLD_PEER_GROUP_ACTUAL_RESULT'
    episode['technical_input_status'] = 'WINDOW_FILES_PREPARED_AWAITING_FILE_VERIFICATION'
    episode['scientific_story_passed'] = not episode['unfired_events']
    write(directory / 'projection_receipt.json', episode)
    reports.append({'episode_id': episode['episode_id'], 'capture_window': window,
                    'byte_preserving_partitions': partitions})
assembly['old_peer_pending_episodes'] = 0
assembly['retained_old_peer_episodes'] = 6
assembly['technical_input_status'] = 'WINDOW_FILES_PREPARED_AWAITING_FILE_VERIFICATION'
write(ROOT / 'assembly_manifest.json', assembly)
source_manifest = read(ROOT / 'source_manifest.json')
source_manifest['old_peer_pending_episodes'] = 0
source_manifest['retained_old_peer_episodes'] = 6
for episode in source_manifest['episodes']:
    if episode['group'] == 'old_peer_load_actual_results':
        episode['adoption_status'] = 'RETAINED_SAME_OLD_PEER_GROUP_ACTUAL_RESULT'
write(ROOT / 'source_manifest.json', source_manifest)
by_id = {episode['episode_id']: episode for episode in assembly['episodes']}
references = read(ROOT / 'original210_reference_index.json')
for row in references['episodes']:
    if row['episode_id'] in by_id:
        ep = by_id[row['episode_id']]
        row['update_status'] = ep['adoption_status']
        row['capture_window'] = ep['capture_window']
        row['story_status'] = ep['story_status']
        row['unfired_events'] = ep['unfired_events']
write(ROOT / 'original210_reference_index.json', references)
with (ROOT / 'episode_status.csv').open('w', newline='') as output:
    names = ['episode_id', 'scenario_id', 'seed', 'group', 'adoption_status', 'story_status',
             'recorded_source_duration_ticks', 'capture_duration_ticks', 'planned_capture_frames', 'unfired_events']
    writer = csv.DictWriter(output, fieldnames=names)
    writer.writeheader()
    for ep in assembly['episodes']:
        writer.writerow({key: json.dumps(ep[key]) if key == 'unfired_events' else ep[key] for key in names})
write(ROOT / 'capture_window_report.json', {
    'schema_version': 'p09.capture-window-adjustment/v1',
    'episodes': reports, 'simulations_started': 0, 'source_files_changed': False,
    'event_logs_truncated': False, 'background_fabricated': False,
    'ue_source_code_not_present_in_server_checkout': True,
    'enforcement': 'Ordinary UE truth/weather/trajectory files contain only tick0..900.',
})
print(json.dumps({'episodes': len(reports), 'partitioned_extended_episodes': sum(bool(row['byte_preserving_partitions']) for row in reports),
                  'capture_window': '0..900 inclusive, 10Hz truth / 2Hz planned capture / 181 planned frames',
                  'source_files_changed': False, 'event_logs_truncated': False}))
