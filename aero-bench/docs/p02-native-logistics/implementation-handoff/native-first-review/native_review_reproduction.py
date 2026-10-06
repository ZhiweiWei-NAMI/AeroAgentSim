"""Actual-package synthetic reproductions of the first native review findings."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('parcel_review_helpers', ROOT / 'tests/tasks/test_native_parcel_runtime.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
package = h.lower_logistics_task_package(h._package_document())
results = []


def fresh():
    m = h._machine(package)
    for t in (1, 2, 3):
        m.observe(h._observation(package, at=h._dwell_tick(t)))
    return m


def probe(name, fn):
    try:
        value = fn()
        results.append({'probe': name, 'actual': value})
    except Exception as e:
        results.append({'probe': name, 'exception_type': type(e).__name__, 'exception': str(e)})


def default_now():
    m = fresh(); o = m.admit_action(h._action())
    return {'status': o.status, 'transfer_tick': m.parcel_state.transfers[-1].at.tick}


def stale():
    return {'status': fresh().admit_action(h._action(), now=h._dwell_tick(100)).status}


def future_rx():
    return {'status': fresh().admit_action(h._action(received_at=h._dwell_tick(500)), now=h._dwell_tick(3)).status}


def reset_window():
    m = h._machine(package)
    m.observe(h._observation(package, at=h._dwell_tick(1), speed_east=0.3))
    for t in (2, 3, 4):
        m.observe(h._observation(package, at=h._dwell_tick(t)))
    o = m.admit_action(h._action(received_at=h._dwell_tick(4), rx_stage_barrier_digest=f'{4:04d}' * 16), now=h._dwell_tick(4))
    return {'status': o.status, 'reason': o.reason}


def two_facilities():
    m = h._machine(package)
    a = m.observe(h._observation(package, at=h._dwell_tick(1)))
    b = m.observe(h._observation(package, at=h._dwell_tick(1), facility_id=h._DROPOFF))
    return {'first_observed': a, 'second_same_tick_observed': b}


def conflict():
    m = fresh(); a = h._action(); first = m.admit_action(a, now=h._dwell_tick(3))
    for t in (4, 5, 6):
        m.observe(h._observation(package, at=h._dwell_tick(t), facility_id=h._DROPOFF))
    second = m.admit_action(h._action(kind='dropoff', action_id=a.action_id, received_at=h._dwell_tick(6), rx_stage_barrier_digest=f'{6:04d}' * 16), now=h._dwell_tick(6))
    return {'first_status': first.status, 'second_status': second.status, 'transfers': len(m.parcel_state.transfers)}


def foreign_actor():
    return {'status': fresh().admit_action(h._action(actor_id='uav.foreign'), now=h._dwell_tick(3)).status}


def pre_pickup_restore():
    original = fresh(); restored = h._machine(package); restored.restore(original.snapshot())
    return {'original': original.admit_action(h._action(), now=h._dwell_tick(3)).status,
            'restored': restored.admit_action(h._action(), now=h._dwell_tick(3)).status}


def admitted_restore():
    original = fresh(); a = h._action(); o = original.admit_action(a, now=h._dwell_tick(3))
    restored = h._machine(package); restored.restore(original.snapshot())
    replay = restored.admit_action(a, now=h._dwell_tick(3))
    return {'original': o.status, 'restored_retry': replay.status, 'same_outcome': replay == o}


for name, fn in [('default_now', default_now), ('stale_dwell_now100', stale),
                 ('future_received_at500', future_rx), ('bad_then_valid_suffix', reset_window),
                 ('two_facilities_same_tick', two_facilities), ('same_action_id_different_kind', conflict),
                 ('foreign_actor_id', foreign_actor), ('pre_pickup_restore', pre_pickup_restore),
                 ('admitted_restore', admitted_restore)]:
    probe(name, fn)

source_hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                 ['aero_bench/tasks/logistics/native_parcel_contract.py',
                  'aero_bench/tasks/logistics/native_parcel_runtime.py',
                  'tests/tasks/test_native_parcel_runtime.py']}
report = {'scope': 'actual package, synthetic helper observations, real presence kernel; no native flight',
          'reviewed_snapshot': '22b7dfd', 'source_sha256': source_hashes, 'probes': results}
Path(__file__).with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
