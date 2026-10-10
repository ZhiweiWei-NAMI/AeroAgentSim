"""Fixture-only single-authority ledger. No commands, network I/O or physical rollback.

This is a finite in-memory admission model, not a controller, a distributed ledger,
or a claim about actual safety. All time fields are exact nanosecond ticks in the
refs' run/epoch. Authoritative occupancy coverage is supplied fixture evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
import math
from threading import RLock

try:
    from .contracts import Ref, Interval
except ImportError:
    from contracts import Ref, Interval


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty')


def _integer(value, label, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f'{label} must be an integer' + (f' >= {minimum}' if minimum is not None else ''))


def _scope(*refs):
    if any(not isinstance(r, Ref) for r in refs):
        raise ValueError('structured Ref required')
    if any(r.ref_type != 'object' for r in refs):
        raise ValueError('ledger endpoints, resources and tasks require object Ref')
    if len({(r.run_id, r.epoch) for r in refs}) > 1:
        raise ValueError('cross-run/epoch references are not comparable')


def _evidence(values):
    values = tuple(values)
    if not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError('nonempty evidence pointers required')
    if len(set(values)) != len(values):
        raise ValueError('duplicate evidence pointers')
    return tuple(sorted(values))


@dataclass(frozen=True)
class AuthorityToken:
    authority_id: str
    epoch: int

    def __post_init__(self):
        _text(self.authority_id, 'authority_id')
        _integer(self.epoch, 'authority epoch', 0)


@dataclass(frozen=True)
class Quantity:
    dimension: str
    value: int | float
    unit: str

    def __post_init__(self):
        _text(self.dimension, 'dimension')
        _text(self.unit, 'unit')
        if (type(self.value) not in (int, float) or self.value < 0 or
                (type(self.value) is float and not math.isfinite(self.value))):
            raise ValueError('quantity must be finite and nonnegative')


def _vector(values):
    values = tuple(values)
    if not values or any(not isinstance(v, Quantity) for v in values):
        raise ValueError('nonempty Quantity vector required')
    if len({v.dimension for v in values}) != len(values):
        raise ValueError('duplicate vector dimensions')
    return tuple(sorted(values, key=lambda q: q.dimension))


def _sum(values):
    # Exact integer/binary-float arithmetic avoids rounded-down capacity grants.
    return sum((Fraction(value) for value in values), Fraction(0))


def _schema(vector):
    return tuple((q.dimension, q.unit) for q in vector)


@dataclass(frozen=True)
class Custody:
    parcel: Ref
    holder: Ref | None
    revision: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        _scope(self.parcel, *(() if self.holder is None else (self.holder,)))
        if self.holder == self.parcel:
            raise ValueError('parcel cannot be its own custodian')
        _integer(self.revision, 'custody revision', 0)
        object.__setattr__(self, 'evidence_refs', _evidence(self.evidence_refs))


@dataclass(frozen=True)
class TransferIntent:
    tx_id: str
    parcel: Ref
    source: Ref
    target: Ref
    task: Ref
    expected_revision: int

    def __post_init__(self):
        _text(self.tx_id, 'tx_id')
        _scope(self.parcel, self.source, self.target, self.task)
        _integer(self.expected_revision, 'expected_revision', 0)
        if self.source == self.target:
            raise ValueError('source and target must differ')
        if self.parcel in (self.source, self.target):
            raise ValueError('parcel cannot be a transfer endpoint')


@dataclass(frozen=True)
class TransferEvidence:
    evidence_id: str
    tx_id: str
    parcel: Ref
    source: Ref
    target: Ref
    task: Ref
    kind: str

    def __post_init__(self):
        _text(self.evidence_id, 'evidence_id')
        _text(self.tx_id, 'tx_id')
        _scope(self.parcel, self.source, self.target, self.task)
        if self.kind not in ('release', 'acquire'):
            raise ValueError('evidence kind must be release or acquire')

    def matches(self, intent):
        return (self.tx_id, self.parcel, self.source, self.target, self.task) == (
            intent.tx_id, intent.parcel, intent.source, intent.target, intent.task)


@dataclass(frozen=True)
class Transfer:
    intent: TransferIntent
    status: str = 'prepared'
    committed_revision: int | None = None
    evidence: tuple[TransferEvidence, ...] = ()


@dataclass(frozen=True)
class Resource:
    ref: Ref
    capacity: tuple[Quantity, ...]
    policy_revision: str
    revision: int = 0

    def __post_init__(self):
        _scope(self.ref)
        _text(self.policy_revision, 'policy_revision')
        _integer(self.revision, 'resource revision', 0)
        object.__setattr__(self, 'capacity', _vector(self.capacity))


@dataclass(frozen=True)
class Occupancy:
    occupancy_id: str
    resource: Ref
    consumer: Ref
    amounts: tuple[Quantity, ...]
    valid: Interval
    evidence_refs: tuple[str, ...]
    claim_id: str | None = None

    def __post_init__(self):
        _text(self.occupancy_id, 'occupancy_id')
        _scope(self.resource, self.consumer)
        object.__setattr__(self, 'amounts', _vector(self.amounts))
        object.__setattr__(self, 'evidence_refs', _evidence(self.evidence_refs))
        if not isinstance(self.valid, Interval):
            raise ValueError('valid must be an Interval')
        if self.claim_id is not None:
            _text(self.claim_id, 'claim_id')


@dataclass(frozen=True)
class OccupancySnapshot:
    resource: Ref
    occupancies: tuple[Occupancy, ...]
    valid: Interval
    complete: bool
    source_revision: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        _scope(self.resource)
        _integer(self.source_revision, 'source_revision', 0)
        if type(self.complete) is not bool or not isinstance(self.valid, Interval):
            raise ValueError('explicit completeness and valid Interval required')
        object.__setattr__(self, 'occupancies', tuple(self.occupancies))
        object.__setattr__(self, 'evidence_refs', _evidence(self.evidence_refs))
        if any(o.resource != self.resource for o in self.occupancies):
            raise ValueError('occupancy resource must match exact snapshot reference')
        if len({o.occupancy_id for o in self.occupancies}) != len(self.occupancies):
            raise ValueError('duplicate occupancy identity')


@dataclass(frozen=True)
class ResourceRequest:
    claim_id: str
    resource: Ref
    amounts: tuple[Quantity, ...]
    valid: Interval
    lease_end_ns: int
    expected_revision: int
    policy_revision: str

    def __post_init__(self):
        _text(self.claim_id, 'claim_id')
        _scope(self.resource)
        _integer(self.lease_end_ns, 'lease_end_ns')
        _integer(self.expected_revision, 'expected_revision', 0)
        _text(self.policy_revision, 'policy_revision')
        object.__setattr__(self, 'amounts', _vector(self.amounts))
        if not isinstance(self.valid, Interval) or self.valid.end_ns is None:
            raise ValueError('reservations require a bounded half-open interval')
        if self.lease_end_ns <= self.valid.start_ns:
            raise ValueError('lease must extend beyond reservation start')


@dataclass(frozen=True)
class BundleIntent:
    bundle_id: str
    task: Ref
    consumer: Ref
    requests: tuple[ResourceRequest, ...]
    priority: int = 0

    def __post_init__(self):
        _text(self.bundle_id, 'bundle_id')
        object.__setattr__(self, 'requests', tuple(self.requests))
        if not self.requests:
            raise ValueError('bundle must have at least one request')
        _scope(self.task, self.consumer, *(r.resource for r in self.requests))
        _integer(self.priority, 'priority')
        if len({r.resource for r in self.requests}) != len(self.requests):
            raise ValueError('one request per resource per bundle required')
        if len({r.claim_id for r in self.requests}) != len(self.requests):
            raise ValueError('duplicate claim identity')


@dataclass(frozen=True)
class Claim:
    bundle_id: str
    task: Ref
    consumer: Ref
    request: ResourceRequest
    fencing_token: AuthorityToken


@dataclass(frozen=True)
class CapacityViolation:
    resource: Ref
    dimension: str
    unit: str
    interval: Interval
    total: Fraction
    capacity: int | float
    contributor_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdmissionResult:
    admitted: bool
    reason: str
    bundle_id: str
    resource_revisions: tuple[tuple[Ref, int], ...]
    claim_ids: tuple[str, ...] = ()
    witness: tuple[CapacityViolation, ...] = ()


@dataclass(frozen=True)
class AuditEntry:
    operation: str
    identity: str
    claimed_token: AuthorityToken
    outcome: str


class LedgerError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FixtureLedger:
    """One lock protects custody replacement and all-or-none resource bundles.

    Tokens model fencing, not authentication. Supplied evidence is not verified
    against hardware. No physical command API exists. A lease affects permission
    only; observed occupancy survives it, priority changes and authority rotation.
    """
    def __init__(self, authority_id: str, authority_epoch: int):
        self._authority = AuthorityToken(authority_id, authority_epoch)
        self._lock = RLock()
        self._custody = {}
        self._transfers = {}
        self._evidence = {}
        self._contacts = {}
        self._resources = {}
        self._snapshots = {}
        self._occupancy = {}
        self._snapshot_history = []
        self._claims = {}
        self._bundles = {}
        self._audit = []

    @property
    def authority(self):
        with self._lock:
            return self._authority

    @property
    def audit(self):
        with self._lock:
            return tuple(self._audit)

    @property
    def claims(self):
        with self._lock:
            return tuple(self._claims.values())

    @property
    def snapshot_history(self):
        with self._lock:
            return tuple(self._snapshot_history)

    def _guard(self, token, operation, identity):
        if token != self._authority:
            self._audit.append(AuditEntry(operation, identity, token, 'stale_fencing_epoch'))
            raise LedgerError('stale_fencing_epoch')

    def rotate_authority(self, new_epoch, *, token):
        with self._lock:
            self._guard(token, 'rotate_authority', str(new_epoch))
            _integer(new_epoch, 'new_epoch', 0)
            if new_epoch <= self._authority.epoch:
                raise LedgerError('epoch_must_increase')
            self._authority = AuthorityToken(self._authority.authority_id, new_epoch)
            return self._authority

    def initialize_custody(self, record: Custody, *, token):
        with self._lock:
            self._guard(token, 'initialize_custody', record.parcel.id)
            if record.parcel in self._custody:
                raise LedgerError('custody_already_initialized')
            self._custody[record.parcel] = record
            return record

    def custody(self, parcel):
        """None means no authoritative record, not a known unassigned holder."""
        with self._lock:
            return self._custody.get(parcel)

    def observe_contacts(self, parcel, contacts, *, token):
        """Independent fixture observation; two contacts need not conflict."""
        contacts = frozenset(contacts)
        _scope(parcel, *contacts)
        with self._lock:
            self._guard(token, 'observe_contacts', parcel.id)
            self._contacts[parcel] = contacts

    def contacts(self, parcel):
        with self._lock:
            return self._contacts.get(parcel)  # Missing is not known empty.

    def _transaction(self, intent):
        previous = self._transfers.get(intent.tx_id)
        if previous is not None and previous.intent != intent:
            raise LedgerError('transaction_payload_collision')
        return previous

    def _custody_precondition(self, intent):
        current = self._custody.get(intent.parcel)
        if current is None:
            raise LedgerError('custody_unknown')
        if current.revision != intent.expected_revision or current.holder != intent.source:
            raise LedgerError('stale_precondition')
        return current

    def _no_other_indeterminate(self, intent):
        if any(t.intent.parcel == intent.parcel and t.intent.tx_id != intent.tx_id
               and t.status == 'execution_indeterminate' for t in self._transfers.values()):
            raise LedgerError('reconciliation_required')

    def prepare_transfer(self, intent: TransferIntent, *, token):
        with self._lock:
            self._guard(token, 'prepare_transfer', intent.tx_id)
            previous = self._transaction(intent)
            if previous is not None:
                return previous
            self._no_other_indeterminate(intent)
            self._custody_precondition(intent)
            result = Transfer(intent)
            self._transfers[intent.tx_id] = result
            return result

    def transfer_status(self, tx_id):
        with self._lock:
            return self._transfers.get(tx_id)

    def _mark_transfer(self, intent, status, token):
        with self._lock:
            self._guard(token, status, intent.tx_id)
            previous = self._transaction(intent)
            if previous is None:
                raise LedgerError('unknown_transaction')
            if previous.status == 'committed':
                return previous
            if previous.status == 'execution_indeterminate' and status == 'executing':
                raise LedgerError('reconciliation_required')
            result = replace(previous, status=status)
            self._transfers[intent.tx_id] = result
            return result

    def mark_executing(self, intent, *, token):
        return self._mark_transfer(intent, 'executing', token)

    def mark_execution_indeterminate(self, intent, *, token):
        return self._mark_transfer(intent, 'execution_indeterminate', token)

    def observe_transfer_evidence(self, intent, evidence: TransferEvidence, *, token):
        """Store release/acquire reports without promoting them to custody."""
        with self._lock:
            self._guard(token, 'observe_transfer_evidence', intent.tx_id)
            previous = self._transaction(intent)
            if previous is None:
                raise LedgerError('unknown_transaction')
            if not evidence.matches(intent):
                raise LedgerError('evidence_binding_mismatch')
            old = self._evidence.get(evidence.evidence_id)
            if old is not None and old != evidence:
                raise LedgerError('evidence_payload_collision')
            if evidence in previous.evidence:
                return previous
            if previous.status == 'committed':
                raise LedgerError('committed_transaction_immutable')
            result = replace(previous, evidence=tuple(sorted(previous.evidence + (evidence,),
                                                            key=lambda e: e.evidence_id)))
            self._evidence[evidence.evidence_id] = evidence
            self._transfers[intent.tx_id] = result
            return result

    def _finish_transfer(self, intent, evidence, token, reconcile):
        with self._lock:
            operation = 'reconcile_transfer' if reconcile else 'commit_transfer'
            self._guard(token, operation, intent.tx_id)
            previous = self._transaction(intent)
            if previous is None:
                raise LedgerError('unknown_transaction')
            evidence = tuple(sorted(evidence, key=lambda e: e.evidence_id))
            if len({e.evidence_id for e in evidence}) != len(evidence):
                raise LedgerError('duplicate_evidence_identity')
            if any(not e.matches(intent) for e in evidence):
                raise LedgerError('evidence_binding_mismatch')
            if {e.kind for e in evidence} != {'release', 'acquire'}:
                raise LedgerError('required_evidence_missing')
            for e in evidence:
                if e.evidence_id in self._evidence and self._evidence[e.evidence_id] != e:
                    raise LedgerError('evidence_payload_collision')
            if previous.status == 'committed':
                if previous.evidence != evidence:
                    raise LedgerError('transaction_evidence_collision')
                return previous
            if previous.status == 'execution_indeterminate' and not reconcile:
                raise LedgerError('reconciliation_required')
            self._no_other_indeterminate(intent)
            current = self._custody_precondition(intent)
            result = Transfer(intent, 'committed', current.revision + 1, evidence)
            replacement = Custody(intent.parcel, intent.target, current.revision + 1,
                                  tuple(e.evidence_id for e in evidence))
            self._custody[intent.parcel] = replacement
            self._transfers[intent.tx_id] = result
            self._evidence.update((e.evidence_id, e) for e in evidence)
            self._audit.append(AuditEntry(operation, intent.tx_id, token, 'committed'))
            return result

    def commit_transfer(self, intent, evidence, *, token):
        return self._finish_transfer(intent, evidence, token, False)

    def reconcile_transfer(self, intent, evidence, *, token):
        """Same transaction, authoritative evidence; never a physical retry."""
        return self._finish_transfer(intent, evidence, token, True)

    def register_resource(self, resource: Resource, *, token):
        with self._lock:
            self._guard(token, 'register_resource', resource.ref.id)
            if resource.ref in self._resources:
                raise LedgerError('resource_already_registered')
            self._resources[resource.ref] = resource
            return resource

    def resource(self, resource_ref):
        with self._lock:
            return self._resources.get(resource_ref)

    def publish_occupancy_snapshot(self, snapshot: OccupancySnapshot, *, expected_revision, token):
        with self._lock:
            self._guard(token, 'publish_occupancy_snapshot', snapshot.resource.id)
            resource = self._resources.get(snapshot.resource)
            if resource is None:
                raise LedgerError('unknown_resource')
            previous = self._snapshots.get(snapshot.resource)
            if previous is not None and snapshot.source_revision <= previous.source_revision:
                if snapshot == previous:
                    return resource
                raise LedgerError('occupancy_revision_conflict')
            if resource.revision != expected_revision:
                raise LedgerError('stale_precondition')
            if any(_schema(o.amounts) != _schema(resource.capacity) for o in snapshot.occupancies):
                raise LedgerError('vector_unit_mismatch')
            # Incomplete enumeration cannot erase existing positive observations.
            occupancies = {} if snapshot.complete else dict(self._occupancy.get(snapshot.resource, {}))
            occupancies.update((o.occupancy_id, o) for o in snapshot.occupancies)
            self._occupancy[snapshot.resource] = occupancies
            self._snapshots[snapshot.resource] = snapshot
            self._snapshot_history.append(snapshot)
            result = replace(resource, revision=resource.revision + 1)
            self._resources[resource.ref] = result
            return result

    def update_capacity(self, resource_ref, capacity, *, expected_revision,
                        expected_policy_revision, new_policy_revision, token):
        capacity = _vector(capacity)
        _text(new_policy_revision, 'new_policy_revision')
        with self._lock:
            self._guard(token, 'update_capacity', resource_ref.id)
            resource = self._resources.get(resource_ref)
            if resource is None:
                raise LedgerError('unknown_resource')
            if (resource.revision != expected_revision or
                    resource.policy_revision != expected_policy_revision):
                raise LedgerError('stale_precondition')
            if _schema(resource.capacity) != _schema(capacity):
                raise LedgerError('vector_unit_mismatch')
            if new_policy_revision == expected_policy_revision:
                raise LedgerError('new_policy_revision_required')
            result = replace(resource, capacity=capacity, policy_revision=new_policy_revision,
                             revision=resource.revision + 1)
            self._resources[resource_ref] = result
            return result  # Existing claims/observations are deliberately retained.

    @staticmethod
    def _covers(outer, inner):
        return outer.start_ns <= inner.start_ns and (
            outer.end_ns is None or (inner.end_ns is not None and outer.end_ns >= inner.end_ns))

    def _violations(self, resource, interval, now_ns, proposed=()):
        """Sweep segment boundaries, with max(claim, linked occupancy) per group."""
        claims = [c for c in self._claims.values() if c.request.resource == resource.ref
                  and c.request.lease_end_ns > now_ns]
        occupancies = list(self._occupancy.get(resource.ref, {}).values())
        rows = [(c.request.valid, c) for c in claims] + [(o.valid, o) for o in occupancies]
        rows += [(r.valid, r) for r in proposed]
        boundaries = {interval.start_ns, interval.end_ns}
        for valid, _ in rows:
            if interval.start_ns < valid.start_ns < interval.end_ns:
                boundaries.add(valid.start_ns)
            if valid.end_ns is not None and interval.start_ns < valid.end_ns < interval.end_ns:
                boundaries.add(valid.end_ns)
        points = sorted(boundaries)
        violations = []
        for start, end in zip(points, points[1:]):
            active_claims = {c.request.claim_id: c for c in claims if c.request.valid.contains(start)}
            active_occ = [o for o in occupancies if o.valid.contains(start)]
            linked, unmatched = {}, []
            for o in active_occ:
                claim = self._claims.get(o.claim_id)
                if claim and claim.request.resource == o.resource and claim.consumer == o.consumer:
                    linked.setdefault(o.claim_id, []).append(o)
                else:
                    unmatched.append(o)
            active_proposed = [r for r in proposed if r.valid.contains(start)]
            for index, cap in enumerate(resource.capacity):
                values = [o.amounts[index].value for o in unmatched]
                contributors = ['occupancy:' + o.occupancy_id for o in unmatched]
                for claim_id in set(active_claims) | set(linked):
                    c = active_claims.get(claim_id)
                    observed = linked.get(claim_id, [])
                    values.append(max(c.request.amounts[index].value if c else 0,
                                      _sum(o.amounts[index].value for o in observed)))
                    if c:
                        contributors.append('claim:' + claim_id)
                    contributors.extend('occupancy:' + o.occupancy_id for o in observed)
                values.extend(r.amounts[index].value for r in active_proposed)
                contributors.extend('proposed:' + r.claim_id for r in active_proposed)
                total = _sum(values)
                if total > cap.value:
                    violations.append(CapacityViolation(resource.ref, cap.dimension, cap.unit,
                        Interval(start, end), total, cap.value, tuple(sorted(contributors))))
        return tuple(violations)

    def capacity_conflicts(self, resource_ref, interval, *, now_ns):
        """Known modeled overcommit witnesses, not proof of complete observation."""
        _integer(now_ns, 'now_ns')
        if not isinstance(interval, Interval) or interval.end_ns is None:
            raise ValueError('bounded interval required')
        with self._lock:
            resource = self._resources.get(resource_ref)
            if resource is None:
                raise LedgerError('unknown_resource')
            return self._violations(resource, interval, now_ns)

    def reserve_bundle(self, intent: BundleIntent, *, now_ns, token):
        _integer(now_ns, 'now_ns')
        with self._lock:
            self._guard(token, 'reserve_bundle', intent.bundle_id)
            previous = self._bundles.get(intent.bundle_id)
            if previous is not None:
                if previous[0] != intent:
                    raise LedgerError('bundle_payload_collision')
                return previous[1]  # Historical admission, not a lease renewal.
            revisions = tuple((r.resource, self._resources[r.resource].revision)
                              for r in intent.requests if r.resource in self._resources)

            def reject(reason, witness=()):
                self._audit.append(AuditEntry('reserve_bundle', intent.bundle_id, token, reason))
                return AdmissionResult(False, reason, intent.bundle_id, revisions, witness=witness)

            for request in intent.requests:
                resource = self._resources.get(request.resource)
                if resource is None:
                    return reject('unknown_resource')
                if request.claim_id in self._claims:
                    return reject('claim_identity_collision')
                if resource.revision != request.expected_revision:
                    return reject('stale_precondition')
                if resource.policy_revision != request.policy_revision:
                    return reject('policy_revision_mismatch')
                if _schema(resource.capacity) != _schema(request.amounts):
                    return reject('vector_unit_mismatch')
                if request.valid.start_ns < now_ns or request.lease_end_ns <= now_ns:
                    return reject('past_or_expired_request')
                coverage = self._snapshots.get(request.resource)
                if coverage is None or not coverage.complete or not self._covers(coverage.valid, request.valid):
                    return reject('scope_incomplete')
                witness = self._violations(resource, request.valid, now_ns, (request,))
                if witness:
                    return reject('capacity_exceeded', witness)
            # No writes until every dependency has passed under this same lock.
            for request in intent.requests:
                self._claims[request.claim_id] = Claim(intent.bundle_id, intent.task, intent.consumer, request, token)
                resource = self._resources[request.resource]
                self._resources[request.resource] = replace(resource, revision=resource.revision + 1)
            result = AdmissionResult(True, 'admitted', intent.bundle_id, revisions,
                                     tuple(r.claim_id for r in intent.requests))
            self._bundles[intent.bundle_id] = (intent, result)
            self._audit.append(AuditEntry('reserve_bundle', intent.bundle_id, token, 'admitted'))
            return result
