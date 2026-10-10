"""Explicit, one-time JSON fixture ingestion. No provider discovery or implicit aliases."""
from dataclasses import asdict
import json
from pathlib import Path
try:
    from .contracts import *
    from .adapter import Snapshot, CompiledBinding
except ImportError:
    from contracts import *
    from adapter import Snapshot, CompiledBinding


def ref(row):
    return Ref(**row)


def interval(row):
    return Interval(**row)


def endpoints(rows):
    return tuple((role, ref(value)) for role, value in rows.items())


def object_record(row):
    row = dict(row)
    row['ref'] = ref(row['ref'])
    row['lifetime'] = interval(row['lifetime'])
    row['capabilities'] = tuple(Capability(**{**c, 'scope': ref(c['scope']), 'valid': interval(c['valid'])}) for c in row.get('capabilities', []))
    return ObjectRecord(**row)


def state_cell(row):
    row = {**row, 'holder': ref(row['holder']), 'valid': interval(row['valid'])}
    if row['dtype'] == 'ref' and row['value'] is not None:
        row['value'] = ref(row['value'])
    return StateCell(**row)


def relation(row):
    return RelationRecord(**{**row, 'ref': ref(row['ref']), 'endpoints': endpoints(row['endpoints']), 'valid': interval(row['valid'])})


def coverage(row):
    return ScopeCoverage(**{**row, 'exact_scope': endpoints(row['exact_scope']), 'valid': interval(row['valid'])})


def binding(row):
    row = dict(row)
    row['roles'] = endpoints(row['roles'])
    row['requirements'] = tuple(RoleRequirement(**r) for r in row['requirements'])
    row['selectors'] = tuple(Selector(**{**s, 'holder': ref(s['holder']), 'endpoints': endpoints(s.get('endpoints', {}))}) for s in row['selectors'])
    row['parameters'] = tuple(row.get('parameters', {}).items())
    return CompiledBinding(Binding(**row))


def load_case(document, case):
    snapshot = Snapshot([object_record(x) for x in document['objects']],
                        [state_cell(x) for x in case.get('cells', [])],
                        [relation(x) for x in case.get('relations', [])],
                        [coverage(x) for x in case.get('coverage', [])])
    return snapshot, binding(case['binding']), QueryPoint(**case['query'])


def read_document(path):
    document = json.loads(Path(path).read_text())
    if document.get('schema') != 'fixture.binding-adapter/v1':
        raise ValueError('unsupported fixture schema')
    return document
