"""Immutable input contracts for the fixture binding adapter; no evaluator or controller."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import math


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')


def freeze_parameter(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (tuple, list)):
        return tuple(freeze_parameter(x) for x in value)
    raise ValueError('parameters support finite scalar values and immutable sequences only')


def integer(value, label, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f'{label} must be an integer' + (f' >= {minimum}' if minimum is not None else ''))


@dataclass(frozen=True)
class Ref:
    run_id: str
    epoch: str
    id: str
    generation: str | int
    ref_type: str = 'object'

    def __post_init__(self):
        for name in ('run_id', 'epoch', 'id'):
            nonempty(getattr(self, name), name)
        if not ((type(self.generation) is int and self.generation >= 1) or
                (isinstance(self.generation, str) and self.generation.strip())):
            raise ValueError('generation must be a positive integer or nonempty string')
        if self.ref_type not in ('object', 'relation'):
            raise ValueError('ref_type must be object or relation')


@dataclass(frozen=True)
class Interval:
    start_ns: int
    end_ns: int | None = None

    def __post_init__(self):
        integer(self.start_ns, 'start_ns')
        if self.end_ns is not None:
            integer(self.end_ns, 'end_ns')
            if self.end_ns <= self.start_ns:
                raise ValueError('interval must be nonempty and half-open')

    def contains(self, t):
        return self.start_ns <= t and (self.end_ns is None or t < self.end_ns)


@dataclass(frozen=True)
class Capability:
    name: str
    issuer: str
    scope: Ref
    valid: Interval
    revision: str

    def __post_init__(self):
        for name in ('name', 'issuer', 'revision'):
            nonempty(getattr(self, name), name)


@dataclass(frozen=True)
class ObjectRecord:
    ref: Ref
    kind: str
    lifetime: Interval
    capabilities: tuple[Capability, ...] = ()

    def __post_init__(self):
        if self.ref.ref_type != 'object':
            raise ValueError('ObjectRecord requires object reference')
        nonempty(self.kind, 'kind')
        object.__setattr__(self, 'capabilities', tuple(self.capabilities))
        if any(c.scope != self.ref for c in self.capabilities):
            raise ValueError('capability scope must match exact object generation')


@dataclass(frozen=True)
class QueryPoint:
    event_ns: int
    evidence_domain: str
    available_ns: int
    commit: int
    scenario_revision: str = 'fixture.v1'
    observation_policy: str = 'after_barrier'
    policy_revision: str = 'v1'

    def __post_init__(self):
        integer(self.event_ns, 'event_ns')
        integer(self.available_ns, 'available_ns')
        integer(self.commit, 'commit', 0)
        for name in ('evidence_domain', 'scenario_revision', 'policy_revision'):
            nonempty(getattr(self, name), name)
        if self.observation_policy not in ('strict_left', 'after_barrier'):
            raise ValueError('unsupported observation policy')


@dataclass(frozen=True)
class StateCell:
    cell_id: str
    holder: Ref
    quantity: str
    value: Any
    dtype: str
    unit: str
    sample_ns: int
    valid: Interval
    evidence_domain: str
    observation_commit: int
    available_after_commit: int
    available_ns: int
    authority: str
    source_ref: str
    provenance: str
    revision: str = '1'
    frame_ref: str | None = None
    supersedes: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    def __post_init__(self):
        for name in ('cell_id', 'quantity', 'unit', 'evidence_domain', 'authority', 'source_ref', 'revision'):
            nonempty(getattr(self, name), name)
        integer(self.sample_ns, 'sample_ns')
        integer(self.available_ns, 'available_ns')
        integer(self.observation_commit, 'observation_commit', 0)
        integer(self.available_after_commit, 'available_after_commit', self.observation_commit)
        if self.dtype not in ('number', 'integer', 'boolean', 'string', 'ref'):
            raise ValueError('unsupported dtype')
        if self.provenance not in ('physical_truth', 'reported', 'measured', 'estimated', 'hypothetical'):
            raise ValueError('unsupported provenance')
        if not self.valid.contains(self.sample_ns):
            raise ValueError('sample time must be in the valid interval')
        object.__setattr__(self, 'supersedes', tuple(self.supersedes))


@dataclass(frozen=True)
class RelationRecord:
    ref: Ref
    schema_id: str
    endpoints: tuple[tuple[str, Ref], ...]
    valid: Interval
    authority: str
    revision: str
    evidence_domain: str
    available_after_commit: int
    available_ns: int
    provenance: str = 'hypothetical'

    def __post_init__(self):
        if self.ref.ref_type != 'relation':
            raise ValueError('RelationRecord requires relation reference')
        for name in ('schema_id', 'authority', 'revision', 'evidence_domain'):
            nonempty(getattr(self, name), name)
        integer(self.available_after_commit, 'available_after_commit', 0)
        integer(self.available_ns, 'available_ns')
        object.__setattr__(self, 'endpoints', tuple(sorted((role, ref) for role, ref in self.endpoints)))
        if not self.endpoints or len(dict(self.endpoints)) != len(self.endpoints):
            raise ValueError('unique, nonempty endpoint roles required')
        for role, ref in self.endpoints:
            nonempty(role, 'endpoint role')
            if ref.ref_type != 'object' or (ref.run_id, ref.epoch) != (self.ref.run_id, self.ref.epoch):
                raise ValueError('endpoints must be object refs in the same run and epoch')


@dataclass(frozen=True)
class ScopeCoverage:
    schema_id: str
    exact_scope: tuple[tuple[str, Ref], ...]
    valid: Interval
    evidence_domain: str
    through_commit: int
    available_after_commit: int
    available_ns: int
    authority: str
    complete: bool

    def __post_init__(self):
        for name in ('schema_id', 'evidence_domain', 'authority'):
            nonempty(getattr(self, name), name)
        for name in ('through_commit', 'available_after_commit'):
            integer(getattr(self, name), name, 0)
        integer(self.available_ns, 'available_ns')
        if type(self.complete) is not bool:
            raise ValueError('complete must be boolean')
        object.__setattr__(self, 'exact_scope', tuple(sorted((role, ref) for role, ref in self.exact_scope)))
        if not self.exact_scope or len(dict(self.exact_scope)) != len(self.exact_scope):
            raise ValueError('unique explicit coverage scope required')


@dataclass(frozen=True)
class RoleRequirement:
    role: str
    kinds: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    capability_issuers: tuple[str, ...] = ('fixture-provider',)

    def __post_init__(self):
        nonempty(self.role, 'role')
        for name in ('kinds', 'capabilities', 'capability_issuers'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
            for item in getattr(self, name):
                nonempty(item, name)
        if not self.kinds or (self.capabilities and not self.capability_issuers):
            raise ValueError('explicit kinds and capability issuers required')


@dataclass(frozen=True)
class Selector:
    field_id: str
    holder: Ref
    quantity: str
    dtype: str
    unit: str
    allowed_authorities: tuple[str, ...]
    allowed_provenances: tuple[str, ...]
    frame_ref: str | None = None
    relation_schema: str | None = None
    endpoints: tuple[tuple[str, Ref], ...] = ()

    def __post_init__(self):
        for name in ('field_id', 'quantity', 'unit'):
            nonempty(getattr(self, name), name)
        for name in ('allowed_authorities', 'allowed_provenances'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
            for item in getattr(self, name):
                nonempty(item, name)
        if self.dtype not in ('number', 'integer', 'boolean', 'string', 'ref'):
            raise ValueError('unsupported selector dtype')
        if not self.allowed_authorities or not self.allowed_provenances:
            raise ValueError('explicit authority and provenance policy required')
        object.__setattr__(self, 'endpoints', tuple(sorted((role, ref) for role, ref in self.endpoints)))
        if self.holder.ref_type == 'relation' and (not self.relation_schema or not self.endpoints):
            raise ValueError('relation selector needs exact schema and endpoints')


@dataclass(frozen=True)
class Binding:
    binding_id: str
    target_id: str
    target_revision: str
    roles: tuple[tuple[str, Ref], ...]
    requirements: tuple[RoleRequirement, ...]
    selectors: tuple[Selector, ...]
    scenario_revision: str = 'fixture.v1'
    observation_policy: str = 'after_barrier'
    policy_revision: str = 'v1'
    binding_epoch: str = '1'
    evaluation_revision: str = '1'
    parameters: tuple[tuple[str, Any], ...] = ()
    distinct_roles: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for name in ('binding_id', 'target_id', 'target_revision', 'scenario_revision', 'policy_revision', 'binding_epoch', 'evaluation_revision'):
            nonempty(getattr(self, name), name)
        for name in ('roles', 'requirements', 'selectors', 'parameters', 'distinct_roles'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, 'roles', tuple((role, ref) for role, ref in self.roles))
        object.__setattr__(self, 'distinct_roles', tuple((a, b) for a, b in self.distinct_roles))
        object.__setattr__(self, 'parameters', tuple((k, freeze_parameter(v)) for k, v in self.parameters))
        for key, _ in self.parameters:
            nonempty(key, 'parameter name')
        if not self.roles or len(dict(self.roles)) != len(self.roles):
            raise ValueError('unique nonempty binding roles required')
        if len({s.field_id for s in self.selectors}) != len(self.selectors):
            raise ValueError('duplicate target field binding')
        if len(dict(self.parameters)) != len(self.parameters):
            raise ValueError('duplicate parameter')
        if self.observation_policy not in ('strict_left', 'after_barrier'):
            raise ValueError('unsupported observation policy')
