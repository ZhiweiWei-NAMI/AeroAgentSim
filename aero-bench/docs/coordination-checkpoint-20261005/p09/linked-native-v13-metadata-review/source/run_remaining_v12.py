"""Generate remaining scoped P09 seeds without changing published episodes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

PROJECT = Path('/mnt/data2/weizhiwei/AERO_WORLD')
FROZEN = Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/linked_native_v12_review')
OUTPUT = Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/linked_native_v12_remaining')
sys.path.insert(0, str(PROJECT))
from Dataset.semantic_simulation.ns3_episode.linked_contract import GROUPS
from Dataset.semantic_simulation.ns3_episode.linked_replay import run_one
from Dataset.semantic_simulation.ns3_episode.provider_adapter import ProviderAdapter


def verify_source(rows):
    for row in rows:
        actual = hashlib.sha256((PROJECT / row['path']).read_bytes()).hexdigest()
        if actual != row['sha256']:
            raise ValueError('Reviewed source changed: ' + row['path'])


def save(name, value):
    with (OUTPUT / name).open('w') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    rows = json.loads((FROZEN / 'source/manifest.json').read_text())
    verify_source(rows)
    OUTPUT.mkdir(exist_ok=False)
    shutil.copytree(FROZEN / 'source', OUTPUT / 'source')
    order = ['X5_comm_failure_to_pad_contention', 'L2-1_v2', 'L2-1_v1']
    order += [name for name in GROUPS if name not in order]
    reused = FROZEN / 'X5_comm_failure_to_pad_contention__seed00/adopted/summary.json'
    record = {'scope': '30 scoped communication episodes only; generation is not independent adoption acceptance',
              'source_commit': 'fd407a065d4eb6f4ff5863c3bfb175eb64d17515',
              'pid': os.getpid(), 'started_unix_s': time.time(),
              'reused': [str(reused)], 'completed': [], 'failed': [],
              'published210_modified': False, 'ue_capture_started': False}
    save('progress.json', record)
    print(json.dumps({'pid': os.getpid(), 'output': str(OUTPUT), 'reused': str(reused)}), flush=True)
    with ProviderAdapter() as provider:
        for scenario in order:
            for seed in (0, 1, 2):
                if scenario == 'X5_comm_failure_to_pad_contention' and seed == 0:
                    continue
                verify_source(rows)
                started = time.perf_counter()
                try:
                    result = run_one(scenario, seed, OUTPUT,
                                     None if GROUPS[scenario] == 'local_weather' else provider)
                    # Process return values are not the persisted semantic verdict.
                    from Dataset.semantic_simulation.ns3_episode.batch_status import persisted_episode_status
                    status = persisted_episode_status(Path(result['output_dir']) / 'summary.json', scenario, seed)
                    record['completed'].append({'scenario': scenario, 'seed': seed,
                        **status,
                        'wall_time_s': time.perf_counter() - started})
                except Exception as error:
                    failure = {'scenario': scenario, 'seed': seed,
                               'error_type': type(error).__name__, 'error': str(error),
                               'wall_time_s': time.perf_counter() - started}
                    record['failed'].append(failure)
                    print(json.dumps({'failure': failure}), flush=True)
                    traceback.print_exc()
                save('progress.json', record)
    verify_source(rows)
    record['finished_unix_s'] = time.time()
    record['status'] = 'generated-with-failures' if record['failed'] else 'generated-pending-independent-review'
    save('progress.json', record)


if __name__ == '__main__':
    main()
