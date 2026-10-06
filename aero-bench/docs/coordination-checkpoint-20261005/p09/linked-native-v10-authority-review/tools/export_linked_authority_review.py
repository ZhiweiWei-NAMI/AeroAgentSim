"""Export the bounded, explicitly authorized P09 authority review packet.

Inputs are completed representative results, exact source copies and the
previously exported X5 v4 operand supplement. This does not adopt episodes.
"""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import argparse
import hashlib
import json

ROOT = Path('/mnt/data2/weizhiwei/AERO_WORLD')
EXPORT = Path('/mnt/data2/weizhiwei/AERO_BENCH/validation/aeroagentsim_bench_export/work/target')
BASE = EXPORT / 'aero-bench/docs/coordination-checkpoint-20261005/p09'
CASES = ('L6-4_v1__seed00', 'L6-4_v2__seed01',
         'X5_comm_failure_to_pad_contention__seed00', 'L5-1_v1__seed00', 'L5-1_v2__seed00')
POSE_FIELDS = ('entity_id', 'tick', 'pos_enu', 'vel_mps', 'yaw_deg', 'label_class', 'state', 'activity_type')


def read(path):
    return json.loads(path.read_text())


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    version = args.revision
    runtime = Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09') / ('linked_native_' + version)
    target = BASE / ('linked-native-' + version + '-authority-review')
    target.mkdir(exist_ok=False)
    manifest, index = [], []

    def copy(source, relative, source_ref):
        data = source.read_bytes()
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest.append({'source_path': source_ref, 'export_path': str(relative),
                         'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})

    snapshot = read(runtime / 'source/manifest.json')
    for row in snapshot['files']:
        path = Path('Dataset/semantic_simulation/ns3_episode') / row['file']
        frozen = runtime / 'source' / row['file']
        if frozen.read_bytes() != (ROOT / path).read_bytes():
            raise ValueError('current source differs from executed frozen source: ' + str(path))
        copy(frozen, Path('source') / path, str(path))
    old = read(BASE / 'linked-native-v4-evidence/source-manifest.json')
    copied = {r['source_path'] for r in manifest}
    for row in old['files']:
        source_path = Path(row['source_path'])
        if source_path.suffix not in ('.py', '.cc') or str(source_path) in copied:
            continue
        current = ROOT / source_path
        previous = BASE / 'linked-native-v4-evidence' / row['export_path']
        if current.read_bytes() != previous.read_bytes():
            raise ValueError('unchanged dependency has changed: ' + str(source_path))
        copy(current, Path('source') / source_path, str(source_path))
    copy(ROOT / 'design/p09/source_group_plan/communication_bindings_review.json',
         Path('source/design/p09/source_group_plan/communication_bindings_review.json'),
         'design/p09/source_group_plan/communication_bindings_review.json')
    copy(Path(__file__), Path('tools/export_linked_authority_review.py'),
         'design/p09/export_linked_authority_review.py')
    copy(runtime / 'source/manifest.json', Path('frozen-source.json'), str(runtime / 'source/manifest.json'))
    copy(runtime / 'focused-tests.txt', Path('focused-tests.txt'), str(runtime / 'focused-tests.txt'))
    supplement = BASE / 'linked-native-v8-authority-review/x5-v4-missing-operands-556-557.json'
    copy(supplement, Path(supplement.name), str(supplement))

    for case in CASES:
        folder = runtime / case / 'adopted'
        summary, profile = read(folder / 'summary.json'), read(folder / 'adoption_profile.json')
        if summary['schema_version'] != 'p09.linked-native-causal/' + version:
            raise ValueError('mixed executed revision')
        out = target / 'cases' / case
        out.mkdir(parents=True)
        for name in ('summary.json', 'adoption_profile.json', 'scene_setup.json', 'event_script.json',
                     'actions.json', 'event_admission.json', 'command_receipts.json',
                     'coupled_iterations.json', 'execution_time.json'):
            source = folder / name
            if source.is_file():
                copy(source, Path('cases') / case / name, str(source))
        for key in ('source_script', 'source_scene'):
            source_path = Path(profile[key])
            copy(ROOT / source_path, Path('cases') / case / 'original' / source_path.name, str(source_path))
        duration = summary['duration_ticks']
        event_ticks = {n // 100_000_000 for n in summary['event_times_ns'].values()}
        boundaries = {t + d for t in event_ticks for d in (-2, -1, 0, 1, 2) if 0 <= t + d <= duration}
        owners = set(profile['actor_ids'])
        script = read(folder / 'event_script.json')
        owners.update(e['p09_owner_id'] for e in script['events'] if 'p09_owner_id' in e)
        owners.update(profile[k] for k in ('primary_station', 'backup_station', 'pad_owner')
                      if profile.get(k) is not None)
        poses, terminal, actor_tail, suffix = [], [], [], []
        with (folder / 'trajectories.jsonl').open() as handle:
            for line in handle:
                row = json.loads(line)
                projected = {k: row[k] for k in POSE_FIELDS if k in row}
                if row['entity_id'] in owners and row['tick'] in boundaries:
                    poses.append(projected)
                if row['tick'] == duration:
                    terminal.append(projected)
                if row['entity_id'] in profile['actor_ids'] and row['tick'] >= duration - 4:
                    actor_tail.append(projected)
                if row['tick'] > 900:
                    suffix.append(projected)
        weather = []
        with (folder / 'weather.jsonl').open() as handle:
            for line in handle:
                row = json.loads(line)
                if row['tick'] in boundaries or row['tick'] >= duration - 4:
                    weather.append(row)
        dump(out / 'boundary-poses.json', {'source_path': str(folder / 'trajectories.jsonl'),
            'scope': 'unchanged field projection of named actors, stations and pad at event ticks +/-2', 'rows': poses})
        dump(out / 'all-entity-terminal.json', {'duration_ticks': duration, 'rows': terminal,
            'controlled_actors_last_five_ticks': actor_tail})
        dump(out / 'boundary-weather.json', {'source_path': str(folder / 'weather.jsonl'), 'rows': weather})
        if suffix:
            with (out / 'all-entity-extended-suffix.jsonl').open('x') as handle:
                for row in suffix:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
        if summary['iteration_count']:
            n = summary['iteration_count'] - 1
            copy(folder / f'iteration{n:02d}/run_config.json', Path('cases') / case / 'final-network-config.json',
                 str(folder / f'iteration{n:02d}/run_config.json'))
            backend = read(folder / 'network_backend.json')
            dump(out / 'backend-projection.json', {k: backend[k] for k in (
                'name', 'ns3_version', 'source_ref', 'radio_config', 'loaded_radio', 'propagation_attributes',
                'network_profile', 'geometry', 'mobility') if k in backend})
            copy(folder / 'radio_actions.jsonl', Path('cases') / case / 'radio_actions.jsonl',
                 str(folder / 'radio_actions.jsonl'))
            receipts = read(folder / 'command_receipts.json')
            ids = {r['accepted']['packet_id'] for r in receipts.values() if r['accepted'] is not None}
            packets = []
            with (folder / 'network_packets.jsonl').open() as handle:
                for line in handle:
                    row = json.loads(line)
                    if row['packet_id'] in ids:
                        packets.append(row)
            dump(out / 'accepted-message-native-packets.json', {'source_path': str(folder / 'network_packets.jsonl'),
                'scope': 'exact native packet rows selected by accepted message packet IDs', 'rows': packets})
        index.append({'episode_id': case, 'seed': summary['seed'], 'mechanism': profile['mechanism'],
            'iterations': summary['iteration_count'], 'duration_ticks': duration,
            'unfired_events': summary['unfired_events'], 'candidate_acquisition_class': summary['adoption_class'],
            'published210_modified': False, 'formal_adoption': 'pending independent native review',
            'controlled_actors': profile['actor_ids'], 'all_terminal_entity_count': len(terminal),
            'owner_local_participants': sorted(owners),
            'controlled_actor_tail_rows': len(actor_tail), 'evidence_directory': 'cases/' + case})

    now = datetime.now(timezone.utc)
    dump(target / 'source-manifest.json', {'revision': version, 'captured_utc': now.isoformat(),
        'captured_working_time': now.astimezone(ZoneInfo('America/Los_Angeles')).isoformat(), 'files': manifest})
    dump(target / 'episode-index.json', {'scope': 'five representative numerical candidates only', 'cases': index})
    print(json.dumps({'target': str(target), 'copied_exact_files': len(manifest), 'cases': index}, indent=2))


if __name__ == '__main__':
    main()
