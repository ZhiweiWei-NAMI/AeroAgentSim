"""Run checked-in JSON fixtures through the adapter/native Atlas and fixture authority."""
from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path
import argparse
import hashlib
import json
try:
    from .contracts import Ref, Interval
    from .adapter import load_atlas
    from .json_io import read_document, load_case
    from .ledger import (FixtureLedger, Custody, TransferIntent, TransferEvidence, LedgerError,
                         Resource, Quantity, OccupancySnapshot, ResourceRequest, BundleIntent)
except ImportError:
    from contracts import Ref, Interval
    from adapter import load_atlas
    from json_io import read_document, load_case
    from ledger import (FixtureLedger, Custody, TransferIntent, TransferEvidence, LedgerError,
                        Resource, Quantity, OccupancySnapshot, ResourceRequest, BundleIntent)

HERE = Path(__file__).resolve().parent
NATIVE_FILES = ('runtime/engine.py', 'runtime/operators.py', 'human_urban/predicates.json',
                'machine_network/predicates.json', 'digital_twin/predicates.json', 'runtime/reference_predicates.json')


def run_ledger_fixture(document):
    if document['schema'] != 'fixture.authority-ledger/v1':
        raise ValueError('unsupported ledger fixture schema')
    ledger = FixtureLedger(document['authority_id'], document['authority_epoch'])
    token = ledger.authority
    data = document['custody']
    parcel, source, task = (Ref(**data[k]) for k in ('parcel', 'source', 'task'))
    targets = tuple(Ref(**r) for r in data['targets'])
    ledger.initialize_custody(Custody(parcel, source, data['revision'], ('fixture.initial.custody',)), token=token)
    ledger.observe_contacts(parcel, (source, *targets), token=token)
    intents = tuple(TransferIntent(f'fixture.transfer.{i}', parcel, source, target, task, data['revision']) for i, target in enumerate(targets))
    for intent in intents:
        ledger.prepare_transfer(intent, token=token)
    statuses = []
    for intent in intents:
        evidence = tuple(TransferEvidence(f'{intent.tx_id}.{kind}', intent.tx_id, parcel, source, intent.target, task, kind) for kind in ('release', 'acquire'))
        try:
            statuses.append(ledger.commit_transfer(intent, evidence, token=token).status)
        except LedgerError as error:
            statuses.append(error.code)
    assert statuses == data['expected_commit_statuses']
    data = document['resource_bundle']
    resources = []
    for row in data['resources']:
        resource = Resource(Ref(**row['ref']), (Quantity(row['dimension'], row['capacity'], row['unit']),), 'fixture.capacity.v1')
        ledger.register_resource(resource, token=token)
        ledger.publish_occupancy_snapshot(OccupancySnapshot(resource.ref, (), Interval(0, 30_000_000_000), True, 1, ('fixture.empty.occupancy',)), expected_revision=0, token=token)
        resources.append(resource.ref)
    def bundle(name, current=False):
        requests = tuple(ResourceRequest(name + '.' + r.id, r, (Quantity(row['dimension'], row['demand'], row['unit']),),
                                         Interval(**data['window']), data['window']['end_ns'], ledger.resource(r).revision if current else 1,
                                         'fixture.capacity.v1') for r, row in zip(resources, data['resources']))
        return BundleIntent(name, Ref(**data['task']), Ref(**data['consumer']), requests)
    first = ledger.reserve_bundle(bundle('bundle.first'), now_ns=0, token=token)
    stale = ledger.reserve_bundle(bundle('bundle.second'), now_ns=0, token=token)
    reread = ledger.reserve_bundle(bundle('bundle.second.reread', current=True), now_ns=0, token=token)
    outcomes = [x.reason for x in (first, stale, reread)]
    assert outcomes == data['expected_outcomes']
    return {'custody_statuses': statuses, 'final_custody': asdict(ledger.custody(parcel)),
            'simultaneous_contact_count': len(ledger.contacts(parcel)),
            'resource_outcomes': outcomes, 'final_claim_count': len(ledger.claims),
            'capacity_witnesses': [asdict(w) for w in reread.witness],
            'physical_actions_executed': 0}


def run(atlas_root):
    engine = load_atlas(atlas_root)  # explicit catalog bootstrap once, no per-frame loading
    document = read_document(HERE / 'fixtures.json')
    results = []
    for case in document['cases']:
        snapshot, binding, point = load_case(document, case)
        prepared = binding.prepare(snapshot, point)
        result = binding.evaluate(engine, prepared)
        assert result['result'] == case['expected']
        results.append({'case_id': case['id'], 'expected': case['expected'], 'actual': result,
                        'prepared_values': dict(prepared.values), 'field_map': dict(prepared.field_map)})
    # Boundary manifest only. This fingerprints the listed files, not every transitive native dependency.
    fingerprints = {name: hashlib.sha256((Path(atlas_root) / name).read_bytes()).hexdigest() for name in NATIVE_FILES}
    return {'schema': 'fixture.binding-adapter-results/v1', 'scope': 'hypothetical native prototype; no live provider, controller or server',
            'native_target_count_in_explicit_catalogs': len(engine.records), 'native_compile_errors': engine.compile_errors,
            'listed_native_file_sha256': fingerprints, 'state_binding_cases': results,
            'ledger': run_ledger_fixture(json.loads((HERE / 'ledger_fixtures.json').read_text()))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--atlas-root', required=True)
    parser.add_argument('--out', type=Path, default=HERE / 'demo_results.json')
    args = parser.parse_args()
    result = run(args.atlas_root)
    args.out.write_text(json.dumps(result, indent=2, default=lambda x: str(x) if isinstance(x, Fraction) else str(x)) + '\n')
    print(json.dumps({'state_binding_cases': len(result['state_binding_cases']), 'ledger': result['ledger']['resource_outcomes'], 'output': str(args.out)}))
