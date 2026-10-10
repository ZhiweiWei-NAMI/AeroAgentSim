"""Compile exact input bindings once, then prepare immutable values for existing Atlas.

No catalog scan, owner-prefix parsing, action dispatch or new predicate semantics.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json
import math
from types import MappingProxyType
try:
    from .contracts import *
except ImportError:
    from contracts import *


class Truth(str, Enum):
    TRUE = 'true'
    FALSE = 'false'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Resolution:
    value: object = None
    reason: str | None = None
    cell_ids: tuple[str, ...] = ()
    conversions: tuple[str, ...] = ()

    @property
    def available(self):
        return self.reason is None


@dataclass(frozen=True)
class RelationAnswer:
    exists: Truth
    rows: tuple[RelationRecord, ...] = ()
    reason: str | None = None

    @property
    def absent(self):
        return {Truth.TRUE: Truth.FALSE, Truth.FALSE: Truth.TRUE}.get(self.exists, Truth.UNKNOWN)


@dataclass(frozen=True)
class Prepared:
    binding_key: str
    point: QueryPoint
    status: str
    reasons: tuple[str, ...]
    values: object
    field_map: object
    resolutions: object


# Deliberately small, versioned boundary conversion registry; no implicit coercion.
CONVERSIONS = MappingProxyType({('km/h', 'm/s'): 1 / 3.6, ('MB/s', 'bit/s'): 8_000_000,
                               ('Mbit/s', 'bit/s'): 1_000_000})


def typed(value, dtype):
    if dtype == 'number':
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    if dtype == 'integer':
        return type(value) is int
    if dtype == 'boolean':
        return type(value) is bool
    if dtype == 'string':
        return isinstance(value, str)
    if dtype == 'ref':
        return isinstance(value, Ref)
    return False


def active(interval, point):
    if point.observation_policy == 'strict_left':
        return interval.start_ns < point.event_ns and (interval.end_ns is None or point.event_ns <= interval.end_ns)
    return interval.contains(point.event_ns)


def visible(record, point):
    return (record.evidence_domain == point.evidence_domain
            and record.available_after_commit <= point.commit
            and record.available_ns <= point.available_ns)


class _ImmutableBoundary:
    __slots__ = ()

    def __setattr__(self, name, value):
        if hasattr(self, name):
            raise AttributeError(f'{name} is immutable; create a new boundary object')
        object.__setattr__(self, name, value)


class Snapshot(_ImmutableBoundary):
    """One provider-boundary ingestion; indices use exact immutable references."""
    __slots__ = ('objects', 'cells', 'relations', 'coverage', '_cells', '_relations')
    def __init__(self, objects=(), cells=(), relations=(), coverage=()):
        objects = tuple(objects)
        self.objects = MappingProxyType({obj.ref: obj for obj in objects})
        if len(self.objects) != len(objects):
            raise ValueError('duplicate exact object reference')
        self.cells, self.relations, self.coverage = tuple(cells), tuple(relations), tuple(coverage)
        ids = [c.cell_id for c in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate cell_id; revisions need distinct cell IDs')
        by_cell, by_relation = defaultdict(list), defaultdict(list)
        for c in self.cells:
            if c.value is not None and not typed(c.value, c.dtype):
                raise ValueError(f'cell {c.cell_id}: value does not match declared type')
            by_cell[c.holder, c.quantity].append(c)
        for r in self.relations:
            by_relation[r.ref].append(r)
        self._cells = MappingProxyType({k: tuple(v) for k, v in by_cell.items()})
        self._relations = MappingProxyType({k: tuple(v) for k, v in by_relation.items()})

    def relation(self, ref, point):
        rows = tuple(r for r in self._relations.get(ref, ()) if active(r.valid, point) and visible(r, point))
        if not rows:
            return None, 'relation_unavailable'
        # No last-writer-wins. Relation revisions must have nonoverlapping validity.
        if any((r.schema_id, r.endpoints, r.authority, r.revision, r.provenance) !=
               (rows[0].schema_id, rows[0].endpoints, rows[0].authority, rows[0].revision, rows[0].provenance)
               for r in rows[1:]):
            return None, 'relation_integrity_conflict'
        for _, endpoint in rows[0].endpoints:
            obj = self.objects.get(endpoint)
            if obj is None or not active(obj.lifetime, point):
                return None, 'relation_endpoint_inactive_or_missing'
        return rows[0], None

    def scoped_relations(self, schema_id, exact_scope, point, *, authorities, provenances=('hypothetical',)):
        """Existence is positive evidence; absence needs exact complete scope coverage."""
        scope = tuple(sorted(exact_scope))
        if not scope or len(dict(scope)) != len(scope):
            raise ValueError('query requires unique explicit scope roles')
        for _, ref in scope:
            obj = self.objects.get(ref)
            if obj is None or not active(obj.lifetime, point):
                return RelationAnswer(Truth.UNKNOWN, reason='scope_inactive_or_missing')
        rows, gaps = [], []
        candidate_refs = {r.ref for r in self.relations if r.schema_id == schema_id
                          and all(dict(r.endpoints).get(role) == ref for role, ref in scope)
                          and r.authority in authorities and r.provenance in provenances
                          and active(r.valid, point) and visible(r, point)}
        for ref in candidate_refs:
            row, reason = self.relation(ref, point)
            if reason:
                gaps.append(reason)
            elif row.authority in authorities and row.provenance in provenances:
                rows.append(row)
        if rows:
            return RelationAnswer(Truth.TRUE, tuple(sorted(rows, key=lambda r: r.ref.id)), sorted(gaps)[0] if gaps else None)
        if gaps:
            return RelationAnswer(Truth.UNKNOWN, reason=sorted(gaps)[0])
        complete = any(c.schema_id == schema_id and c.exact_scope == scope and c.complete
                       and c.authority in authorities and active(c.valid, point)
                       and c.through_commit >= point.commit and visible(c, point)
                       for c in self.coverage)
        return RelationAnswer(Truth.FALSE if complete else Truth.UNKNOWN,
                              reason=None if complete else 'scope_incomplete')

    def resolve(self, selector, point):
        if selector.holder.ref_type == 'object':
            obj = self.objects.get(selector.holder)
            if obj is None or not active(obj.lifetime, point):
                return Resolution(reason='object_inactive_or_missing')
        if selector.holder.ref_type == 'relation':
            relation, why = self.relation(selector.holder, point)
            if why:
                return Resolution(reason=why)
            if relation.schema_id != selector.relation_schema or relation.endpoints != selector.endpoints:
                return Resolution(reason='relation_endpoint_or_schema_mismatch')
            if relation.authority not in selector.allowed_authorities or relation.provenance not in selector.allowed_provenances:
                return Resolution(reason='relation_authority_or_provenance_rejected')
        candidates, reasons = [], []
        for c in self._cells.get((selector.holder, selector.quantity), ()):
            if c.authority not in selector.allowed_authorities:
                reasons.append('unauthorized_source'); continue
            if c.provenance not in selector.allowed_provenances:
                reasons.append('provenance_rejected'); continue
            if c.evidence_domain != point.evidence_domain:
                reasons.append('evidence_domain_mismatch'); continue
            if not visible(c, point):
                reasons.append('not_available_as_of'); continue
            if not active(c.valid, point):
                reasons.append('outside_valid_interval'); continue
            if c.sample_ns > point.event_ns or (point.observation_policy == 'strict_left' and c.sample_ns == point.event_ns):
                reasons.append('observation_boundary'); continue
            candidates.append(c)
        if not candidates:
            return Resolution(reason=sorted(set(reasons))[0] if reasons else 'exact_binding_missing')
        # Same immutable source revision contradicting itself is an integrity fault,
        # even if a candidate tries to supersede the contradictory twin.
        revision_values = {}
        for c in candidates:
            key = (c.authority, c.source_ref, c.revision)
            value = (c.value, c.dtype, c.unit, c.frame_ref, c.unavailable_reason)
            if key in revision_values and value != revision_values[key]:
                return Resolution(reason='integrity_conflict', cell_ids=tuple(x.cell_id for x in candidates))
            revision_values[key] = value
        by_id = {c.cell_id: c for c in candidates}
        removed = set()
        for c in candidates:
            for old_id in c.supersedes:
                old = by_id.get(old_id)
                if old is not None:
                    if (old.authority, old.source_ref) != (c.authority, c.source_ref) or old.observation_commit >= c.observation_commit:
                        return Resolution(reason='invalid_supersession', cell_ids=(old_id, c.cell_id))
                    removed.add(old_id)
        candidates = [c for c in candidates if c.cell_id not in removed]
        values, conversions = [], []
        for c in candidates:
            if c.unavailable_reason or c.value is None:
                return Resolution(reason=c.unavailable_reason or 'source_unavailable', cell_ids=tuple(x.cell_id for x in candidates))
            if c.dtype != selector.dtype or not typed(c.value, selector.dtype):
                return Resolution(reason='type_mismatch', cell_ids=(c.cell_id,))
            if c.frame_ref != selector.frame_ref:
                return Resolution(reason='frame_mismatch', cell_ids=(c.cell_id,))
            value = c.value
            if c.unit != selector.unit:
                factor = CONVERSIONS.get((c.unit, selector.unit))
                if factor is None or c.dtype != 'number':
                    return Resolution(reason='unit_mismatch', cell_ids=(c.cell_id,))
                value *= factor
                if not typed(value, selector.dtype):
                    return Resolution(reason='unit_conversion_out_of_range', cell_ids=(c.cell_id,))
                conversions.append(f'{c.cell_id}:{c.unit}->{selector.unit}@units.v1')
            values.append(value)
        if any(v != values[0] for v in values[1:]):
            return Resolution(reason='conflicting_source_values', cell_ids=tuple(c.cell_id for c in candidates))
        return Resolution(values[0], cell_ids=tuple(c.cell_id for c in candidates), conversions=tuple(conversions))


class CompiledBinding(_ImmutableBoundary):
    """Validate explicit signature and assign independent slot names once."""
    __slots__ = ('binding', 'key', 'field_map', 'roles')
    def __init__(self, binding):
        self.binding = binding
        # This fingerprint is once per binding, not a per-row audit gate.
        self.key = sha256(json.dumps(asdict(binding), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.field_map = MappingProxyType({s.field_id: f'slot_{i}' for i, s in enumerate(binding.selectors)})
        self.roles = MappingProxyType(dict(binding.roles))
        if len({(r.run_id, r.epoch) for r in self.roles.values()}) != 1:
            raise ValueError('binding cannot mix runs/epochs')
        if any(r.ref_type != 'object' for r in self.roles.values()):
            raise ValueError('roles bind object refs; relations are explicit selectors')
        if len({r.role for r in binding.requirements}) != len(binding.requirements) or set(self.roles) != {r.role for r in binding.requirements}:
            raise ValueError('each role needs one explicit signature requirement')
        for a, b in binding.distinct_roles:
            if a not in self.roles or b not in self.roles or self.roles[a] == self.roles[b]:
                raise ValueError('distinct role requirement violated')
        for s in binding.selectors:
            if s.holder.ref_type == 'object' and s.holder not in self.roles.values():
                raise ValueError('selector owner not in bound roles')
            if s.holder.ref_type == 'relation' and any(ref not in self.roles.values() for _, ref in s.endpoints):
                raise ValueError('relation endpoint not in bound roles')

    def prepare(self, snapshot, point):
        b = self.binding
        status, reasons = 'bound', []
        if (b.scenario_revision, b.observation_policy, b.policy_revision) != (point.scenario_revision, point.observation_policy, point.policy_revision):
            status, reasons = 'invalid', ['observation_or_scenario_revision_mismatch']
        for req in b.requirements:
            obj = snapshot.objects.get(self.roles[req.role])
            if obj is None:
                status = 'not_bound'; reasons.append('object_missing'); continue
            if not active(obj.lifetime, point):
                status = 'not_bound'; reasons.append('object_inactive'); continue
            if obj.kind not in req.kinds:
                status = 'invalid'; reasons.append('kind_mismatch'); continue
            claims = {c.name for c in obj.capabilities if c.issuer in req.capability_issuers and active(c.valid, point)}
            if not set(req.capabilities).issubset(claims):
                status = 'unsupported'; reasons.append('capability_missing_or_expired')
        resolutions = {s.field_id: snapshot.resolve(s, point) for s in b.selectors} if not reasons else {}
        structural = {'relation_endpoint_or_schema_mismatch', 'relation_integrity_conflict', 'relation_endpoint_inactive_or_missing'}
        relation_errors = sorted({v.reason for v in resolutions.values() if v.reason in structural})
        if relation_errors:
            status = 'invalid'
            reasons.extend(relation_errors)
        values = {self.field_map[s.field_id]: resolutions[s.field_id].value if not reasons else None for s in b.selectors}
        return Prepared(self.key, point, status, tuple(sorted(set(reasons))), MappingProxyType(values), self.field_map,
                        MappingProxyType(resolutions))

    def evaluate(self, engine, prepared, history=()):
        """Read-only native call. History must be explicit immutable, same-binding samples.

        Historical rows retain their original availability cutoffs. To revise a past
        projection, use a new evaluation_revision and a separately prepared history.
        """
        if prepared.binding_key != self.key:
            raise ValueError('prepared values belong to another binding')
        if prepared.status != 'bound':
            return {'target_id': self.binding.target_id, 'result': 'unknown', 'binding_status': prepared.status,
                    'reasons': list(prepared.reasons)}
        required = set(engine.requirements.get(self.binding.target_id, ()))
        if not required.issubset(prepared.field_map):
            raise ValueError('target required fields are not explicitly bound')
        def native_time(ns):
            seconds = ns / 1_000_000_000
            if abs(ns) > 2 ** 52 or int(round(seconds * 1_000_000_000)) != ns:
                raise ValueError('native_time_precision_loss: use a declared relative simulation clock')
            return seconds
        now_seconds = native_time(prepared.point.event_ns)
        seen, native_seen, rows = set(), {now_seconds}, []
        for sample in history:
            if sample.binding_key != self.key:
                raise ValueError('history binding/generation/policy mismatch')
            if sample.point.event_ns >= prepared.point.event_ns or sample.point.event_ns in seen:
                raise ValueError('history times must be unique and strictly before current time')
            if sample.point.evidence_domain != prepared.point.evidence_domain or sample.point.commit > prepared.point.commit or sample.point.available_ns > prepared.point.available_ns:
                raise ValueError('history contains evidence beyond current cutoff/domain')
            seen.add(sample.point.event_ns)
            sample_seconds = native_time(sample.point.event_ns)
            if sample_seconds in native_seen:
                raise ValueError('native_time_precision_loss: timestamps collapse in Atlas')
            native_seen.add(sample_seconds)
            rows.append({'t': sample_seconds, 'states': dict(sample.values)})
        result = engine.evaluate(self.binding.target_id, dict(prepared.values), t=now_seconds,
                                 history=rows, parameters=dict(self.binding.parameters), field_map=dict(prepared.field_map), trace=True)
        return {**result, 'binding_status': prepared.status, 'binding_key': self.key,
                'input_reasons': {k: v.reason for k, v in prepared.resolutions.items() if v.reason}}


def exact_path(value, tokens):
    """Ingest nested provider JSON with literal tokens, never dot-separated IDs."""
    for token in tokens:
        if not isinstance(value, dict) or token not in value:
            raise KeyError(tuple(tokens))
        value = value[token]
    return value


def load_atlas(atlas_root, predicate_files=('human_urban/predicates.json', 'machine_network/predicates.json', 'digital_twin/predicates.json', 'runtime/reference_predicates.json'), event_files=()):
    """Optional integration boundary: caller supplies one explicit Atlas runtime root."""
    import importlib.util
    from pathlib import Path
    import sys
    root = Path(atlas_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))  # native operators use explicit sibling imports
    package = '_fixture_atlas_runtime'
    # A fresh private package avoids importing an unrelated generic `runtime` module.
    if package in sys.modules:
        if Path(sys.modules[package].__file__).resolve() != root / 'runtime' / '__init__.py':
            raise ValueError('only one Atlas root per process; start a fresh process to change it')
    previous_bytecode_policy = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # imported Atlas files remain read-only
    try:
        if package not in sys.modules:
            spec = importlib.util.spec_from_file_location(package, root / 'runtime' / '__init__.py', submodule_search_locations=[str(root / 'runtime')])
            module = importlib.util.module_from_spec(spec)
            sys.modules[package] = module
            spec.loader.exec_module(module)
        module = __import__(package + '.engine', fromlist=['Engine'])
    finally:
        sys.dont_write_bytecode = previous_bytecode_policy
    def read_exact(paths):
        rows = []
        for relative in paths:
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError('catalog path must remain under the explicit Atlas root')
            rows.extend(module.read_rows(path))
        return rows
    return module.Engine(read_exact(predicate_files), read_exact(event_files), root=root)
