"""Freeze verified technical window metadata without changing source results."""
import hashlib
import json
import pathlib

ROOT = pathlib.Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/ue_input_overlay_v14')


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


verification = read(ROOT / 'verification_capture_window.json')
assert verification['episodes_checked'] == 36 and verification['errors'] == []
assembly = read(ROOT / 'assembly_manifest.json')
windows = read(ROOT / 'capture_window_report.json')
for ep in assembly['episodes']:
    directory = ROOT / pathlib.Path(ep['ue_entry']).parent
    relative = directory.relative_to(ROOT).as_posix()
    mask_path = directory / 'multimodal_window_mask.jsonl'
    with mask_path.open('w') as output:
        for tick in range(ep['recorded_source_duration_ticks'] + 1):
            output.write(json.dumps({
                'episode_id': ep['episode_id'], 'tick': tick, 'sim_time_s': tick / 10,
                'multimodal_window_valid': tick <= 900,
                'observed_sensor_availability': 'NOT_CAPTURED_BY_THIS_ASSEMBLY' if tick <= 900 else 'UNKNOWN_UNOBSERVED',
                'rgb_lidar_loss_mask_if_outside_window': False if tick > 900 else None,
                'mask_semantics': 'Capture-window eligibility only; inside-window sensor success must come from actual UE capture.',
            }, separators=(',', ':')) + '\n')
    window = read(directory / 'capture_window.json')
    window['multimodal_window_mask'] = relative + '/multimodal_window_mask.jsonl'
    write(directory / 'capture_window.json', window)
    ep['final_ready'] = True
    ep['final_ready_scope'] = 'Technical serialized input package for original 0..90s capture window only.'
    ep['technical_input_status'] = 'VERIFIED_WINDOW_FILES_READY'
    ep['collection_status'] = 'TECHNICAL_INPUT_READY; user performs UE capture after parent delivery.'
    ep['multimodal_window_mask'] = window['multimodal_window_mask']
    write(directory / 'projection_receipt.json', ep)
    manifest = read(directory / 'episode_manifest.json')
    manifest['validation_summary'] = {
        'status': 'TECHNICAL_WINDOW_FILE_CHECKS_PASSED', 'source_files_verified': True,
        'actual_ue_capture_executed': False, 'scientific_story_passed': ep['scientific_story_passed'],
        'ns3_reexecuted': False, 'physical_realization_reverified': False,
    }
    manifest['multimodal_window_mask'] = window['multimodal_window_mask']
    write(directory / 'episode_manifest.json', manifest)
assembly['final_ready'] = True
assembly['final_ready_scope'] = 'Technical files/relative entries/recorded time support in original 0..90s capture window.'
assembly['dataset_scientific_acceptance_claimed'] = False
assembly['technical_input_status'] = 'VERIFIED_WINDOW_FILES_READY'
assembly['ue_execution_tested'] = False
assembly['full210_and_ARM_freeze_claimed'] = False
write(ROOT / 'assembly_manifest.json', assembly)
refs = read(ROOT / 'original210_reference_index.json')
refs['updated_overlay_technical_ready'] = True
refs['final_ready'] = False
refs['final_ready_scope'] = 'Full210/ARM adoption remains separate; this index preserves the original174 references.'
write(ROOT / 'original210_reference_index.json', refs)
summary = {
    'schema_version': 'p09.ue-input-assembly-summary/v2',
    'episodes_assembled': 36, 'selected_l6_2_v5': 6, 'selected_other_v13': 24,
    'retained_same_old_peer_l6_4_group': 6, 'tower_egress_selected': 0,
    'original210_overwritten': False, 'new_simulator_runs': 0, 'ue_capture_started': False,
    'capture_contract': {'tick_hz': 10, 'dt_s': 0.1, 'tick_start': 0, 'tick_end': 900,
                         'duration_s': 90, 'capture_step_ticks': 5, 'capture_rate_hz': 2,
                         'planned_frames_per_episode': 181, 'planned_frames_36_episode_grid': 6516,
                         'planned_not_already_collected': True},
    'numeric_projection': {'all_recorded_truth_frames_before_partition': sum(ep['truth_frames'] for ep in assembly['episodes']),
                           'ue_truth_frames': verification['ue_truth_frames'],
                           'ue_entity_rows': verification['ue_entity_rows'],
                           'recorded_pose_equality_checks': sum(ep['recorded_pose_equalities_checked'] for ep in assembly['episodes'])},
    'unmet_old_peer_stories': [{'episode_id': ep['episode_id'], 'unfired_events': ep['unfired_events']}
                              for ep in assembly['episodes'] if ep['unfired_events']],
    'extended_records_excluded_from_ue_window': [
        {'episode_id': row['episode_id'], 'missing_extended_background': row['capture_window']['missing_extended_background'],
         'byte_preserving_partitions': row['byte_preserving_partitions']}
        for row in windows['episodes'] if row['byte_preserving_partitions']],
    'sensor_binding_scope': 'Existing UE rig/map/assets required. No new sensor calibration or actual RGB/LiDAR capture success asserted.',
    'arm': {'dependency_records': 143, 'not_direct_capture_episode_count': True,
            'source_reference_reconciliation': 'NOT_COMPLETED_BY_THIS_ASSEMBLY'},
    'original174': 'Retained as original references; not certified finalC by this package.',
    'final_ready': True, 'final_ready_scope': assembly['final_ready_scope'],
    'scientific_full_story_accepted': False, 'full210_and_ARM_freeze_claimed': False,
    'collection_status': 'Technical36 overlay ready in original90s window; no automated capture authorized or launched.',
}
write(ROOT / 'assembly_summary.json', summary)

# Source-index hashes were already verified by verify_package. Only small
# metadata and the two partitioned projections changed since the earlier index.
old_index = read(ROOT / 'package_files.json')
old = {entry['path']: entry for entry in old_index['files']}
file_entries = []
for path in sorted(ROOT.rglob('*')):
    if not path.is_file():
        continue
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith('process_failures/') or relative in ('package_files.json', 'pull_entry.json'):
        continue
    assert not path.is_symlink(), relative
    prior = old.get(relative)
    unchanged_numeric = relative.startswith('sources/') or (
        path.suffix == '.jsonl' and '/additional_recorded_suffix/' not in relative
        and not any('/' + eid + '/' in relative for eid in ('L6-4_v1__seed00', 'L6-4_v2__seed00'))
        and path.name != 'multimodal_window_mask.jsonl')
    if unchanged_numeric and prior is not None:
        assert path.stat().st_size == prior['bytes'], relative
        digest = prior['sha256']
    else:
        h = hashlib.sha256()
        with path.open('rb') as input_file:
            for block in iter(lambda: input_file.read(1048576), b''):
                h.update(block)
        digest = h.hexdigest()
    file_entries.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': digest})
write(ROOT / 'package_files.json', {'schema_version': 'p09.ue-overlay-files/v2',
    'files': file_entries, 'bytes': sum(entry['bytes'] for entry in file_entries),
    'original210_modified': False, 'tower_egress_selected': False,
    'final_ready': True, 'final_ready_scope': assembly['final_ready_scope'],
    'self_and_pull_entry_excluded_to_avoid_recursive_hashes': True})
print(json.dumps({'technical_window_ready': True, 'episodes': 36, 'files': len(file_entries),
                  'uncompressed_bytes': sum(entry['bytes'] for entry in file_entries),
                  'extended_suffixes_preserved': 2, 'unmet_story_episodes_preserved': len(summary['unmet_old_peer_stories'])}))
