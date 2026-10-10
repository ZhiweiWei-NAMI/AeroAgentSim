"""Exact existing X1 JSONL bytes and bounded v12/v13 trajectory comparison."""
from pathlib import Path
from itertools import zip_longest
import hashlib
import json

ROOT = Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/linked_native_v13_metadata_review')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


comparisons = []
for record in json.loads((ROOT / '30_metadata_resolution.json').read_text()):
    source = Path(record['source_output']) / 'trajectories.jsonl'
    current = Path(record['current_trajectory'])
    episode = ROOT / record['episode_id']
    if record['episode_id'].startswith('X1_'):
        raw = episode / 'first_permission_source_poses_450_452.raw.jsonl'
        index = []
        byte_offset = 0
        with source.open('rb') as original, raw.open('wb') as target:
            for line_no, line in enumerate(original, 1):
                row = json.loads(line)
                if row['tick'] in (450, 451, 452) and row['entity_id'] in (
                        'uav_x1_forced_landing', 'ped_x1_crowd_x1_08'):
                    index.append({'source_line_1based': line_no, 'source_byte_offset': byte_offset,
                        'bytes': len(line), 'raw_line_sha256': hashlib.sha256(line).hexdigest(),
                        'tick': row['tick'], 'entity_id': row['entity_id'],
                        'simulation_time_ns_from_declared_tick': row['tick'] * 100_000_000})
                    target.write(line)
                byte_offset += len(line)
                if row['tick'] > 452:
                    break
        assert len(index) == 6 and len({(x['tick'], x['entity_id']) for x in index}) == 6
        write(episode / 'first_permission_source_poses_450_452.raw-index.json', {
            'source': str(source), 'source_sha256': sha(source), 'raw_file_sha256': sha(raw),
            'byte_preserving_export': True, 'tick_duration_ns': 100_000_000,
            'raw_rows': index, 'time_note': 'Derived integer time is index metadata, not a new observed field.',
            'native_execution': 'existing v12, no rerun'})
    if source.resolve() == current.resolve():
        comparisons.append({'episode_id': record['episode_id'], 'source': str(source), 'current': str(current),
            'status': 'SAME_IMMUTABLE_SOURCE_FILE', 'source_sha256': sha(source),
            'numeric_change': False, 'method': 'Current product directly references the exact source file; no transformed trajectory exists.'})
        continue
    counts = {'rows': 0, 'unchanged_rows': 0, 'route_metadata_rows': 0, 'corridor_placement_rows': 0}
    changed_fields = set()
    corridors = set(record['render_changes'])
    physical = 0
    with source.open('rb') as old, current.open('rb') as new:
        for line_no, pair in enumerate(zip_longest(old, new), 1):
            a, b = pair
            assert a is not None and b is not None, ('row count mismatch', line_no)
            x, y = json.loads(a), json.loads(b)
            assert (x['tick'], x['entity_id']) == (y['tick'], y['entity_id'])
            diff = {key for key in set(x) | set(y) if x.get(key) != y.get(key)}
            changed_fields.update(diff)
            counts['rows'] += 1
            if not diff:
                counts['unchanged_rows'] += 1
            elif x['entity_id'] in corridors:
                assert diff <= {'pos_enu', 'yaw_deg'}, ('unexpected corridor delta', line_no, diff)
                counts['corridor_placement_rows'] += 1
            else:
                assert x['entity_id'] == record['route_field_owner']
                assert diff <= {'route_waypoints_enu_m', 'planned_route_waypoints_enu_m'}, (line_no, diff)
                counts['route_metadata_rows'] += 1
            if x['entity_id'] not in corridors:
                assert all(x.get(k) == y.get(k) for k in ('pos_enu', 'vel_mps', 'yaw_deg', 'state', 'activity_type'))
                physical += 1
    comparisons.append({'episode_id': record['episode_id'], 'source': str(source), 'current': str(current),
        'source_sha256': sha(source), 'current_sha256': sha(current), 'status': 'ALL_ROWS_STREAM_COMPARED',
        **counts, 'all_changed_fields': sorted(changed_fields), 'physical_actor_rows_unchanged': physical,
        'physical_motion_numeric_change': False, 'logical_corridor_placement_changed': sorted(corridors),
        'meaning': 'Logical corridor placement numbers intentionally change; physical actor position, velocity, yaw and state do not.'})
write(ROOT / 'full_trajectory_delta_comparison.json', {'schema_version': 'p09.full-existing-trajectory-delta/v1',
    'scope': 'Exact 30 existing native v12 outputs and v13 metadata products only; no simulation or capture.',
    'episodes': comparisons, 'X1_byte_exact_export_rows': 18,
    'pose_scope': 'Every existing field is compared for three transformed L2v2 files. The other 27 current trajectories are the original source files; no broader pose range is inferred from boundary samples.'})
print(json.dumps({'episodes': len(comparisons), 'stream_compared': sum(x['status'] == 'ALL_ROWS_STREAM_COMPARED' for x in comparisons),
    'same_source_file': sum(x['status'] == 'SAME_IMMUTABLE_SOURCE_FILE' for x in comparisons), 'raw_X1_rows': 18,
    'rows': sum(x.get('rows', 0) for x in comparisons)}))
