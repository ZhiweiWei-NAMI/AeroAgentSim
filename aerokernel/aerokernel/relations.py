"""Directed, versioned temporal relations and activated cardinality scopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .errors import KernelError
from .ids import EntityRef, ItemRef, validate_text
from .time import Instant, Interval, Stamp
from .values import freeze, thaw

if TYPE_CHECKING:
    from .state import Store
    from .transactions import Candidate

DIRECTIONS = frozenset({"targets_per_source", "sources_per_target"})


@dataclass(frozen=True)
class Cardinality:
    """Explicit directional minimum and maximum; None is declared infinity."""

    minimum: int
    maximum: int | None

    def __post_init__(self) -> None:
        if (
            type(self.minimum) is not int
            or self.minimum < 0
            or (
                self.maximum is not None
                and (type(self.maximum) is not int or self.maximum < self.minimum)
            )
        ):
            raise KernelError(
                "RELATION_CARDINALITY", "explicit ordered nonnegative bounds required"
            )


@dataclass(frozen=True)
class RelationDescriptor:
    """Independent endpoint classes, directional bounds and identity policy."""

    id: str
    source_type: str
    target_type: str
    targets_per_source: Cardinality
    sources_per_target: Cardinality
    identity_policy: str = "edge_id"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (self.id, self.source_type, self.target_type):
            validate_text(name)
        if not isinstance(self.targets_per_source, Cardinality) or not isinstance(
            self.sources_per_target, Cardinality
        ):
            raise KernelError(
                "RELATION_CARDINALITY", "declare both cardinality directions"
            )
        if self.identity_policy not in {"edge_id", "endpoint_pair"}:
            raise KernelError(
                "RELATION_IDENTITY", "unsupported relation identity policy"
            )
        object.__setattr__(self, "metadata", freeze(dict(self.metadata)))

    def to_data(self) -> dict[str, Any]:
        """Encode explicit directional cardinalities without source inference."""
        return {
            "id": self.id,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "targets_per_source": {
                "minimum": self.targets_per_source.minimum,
                "maximum": self.targets_per_source.maximum,
            },
            "sources_per_target": {
                "minimum": self.sources_per_target.minimum,
                "maximum": self.sources_per_target.maximum,
            },
            "identity_policy": self.identity_policy,
            "metadata": thaw(self.metadata),
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> RelationDescriptor:
        """Decode the exact portable descriptor shape."""
        if set(data) != {
            "id",
            "source_type",
            "target_type",
            "targets_per_source",
            "sources_per_target",
            "identity_policy",
            "metadata",
        }:
            raise KernelError("RELATION_DESCRIPTOR", "unexpected descriptor fields")
        return cls(
            data["id"],
            data["source_type"],
            data["target_type"],
            Cardinality(**data["targets_per_source"]),
            Cardinality(**data["sources_per_target"]),
            data["identity_policy"],
            data["metadata"],
        )


@dataclass(frozen=True)
class RelationRule:
    """Single edge writer over a relation and inherited source-ID scope."""

    partition: str
    relation_id: str
    source_type: str
    id_pattern: str = "*"
    priority: int = 0


@dataclass(frozen=True)
class ObligationRule:
    """Authorized minimum controller over one directional endpoint scope."""

    partition: str
    relation_id: str
    direction: str
    endpoint_type: str
    id_pattern: str = "*"
    priority: int = 0


@dataclass(frozen=True)
class RelationDependency:
    """Declared edge/obligation source scope, with physical knowledge lag."""

    relation_id: str
    source_type: str | None = None
    id_pattern: str = "*"
    lag_ns: int = 0
    value_only: bool = False


@dataclass(frozen=True)
class Edge:
    """One immutable assertion/closure/cancellation version; None is canceled."""

    edge_id: str
    relation_id: str
    source: EntityRef
    target: EntityRef
    valid: Interval | None
    acquired: Stamp
    available: Instant
    producer: str
    version: ItemRef


@dataclass(frozen=True)
class Obligation:
    """A version of an explicitly activated directional minimum interval."""

    obligation_id: str
    relation_id: str
    direction: str
    endpoint_ref: EntityRef
    valid: Interval | None
    available: Instant
    producer: str
    version: ItemRef


def latest_edge(store: Store, edge_id: str, cut_index: int) -> Edge:
    """Resolve one edge at a knowledge prefix, including canceled versions."""
    for version in reversed(store.edges.get(edge_id, ())):
        if version.version.record_index <= cut_index:
            return version
    raise KernelError("EDGE_UNKNOWN", "edge identity is not visible at this prefix")


def latest_obligation(store: Store, obligation_id: str, cut_index: int) -> Obligation:
    """Resolve a directional obligation without rewriting older prefixes."""
    for version in reversed(store.obligations.get(obligation_id, ())):
        if version.version.record_index <= cut_index:
            return version
    raise KernelError("OBLIGATION_UNKNOWN", "obligation is not visible at this prefix")


def _remember_scope(candidate: Candidate, relation_id: str, out: ItemRef) -> None:
    candidate.relation_affected.add(relation_id)
    candidate.relation_causes[relation_id] = out


def _deadline_key(
    kind: str, identity: str, version: ItemRef, boundary: Instant
) -> tuple[str, ...]:
    return (
        "relation",
        kind,
        f"{identity}:{version.record_index}:{version.item_index}:{boundary.ns}:{boundary.microstep}",
    )


def _deadlines(
    candidate: Candidate,
    kind: str,
    identity: str,
    row: Edge | Obligation,
    *,
    cancel: bool = False,
) -> None:
    from .codec import encode

    if row.valid is None:
        return
    for boundary in (row.valid.start, row.valid.end):
        if boundary is None or boundary <= candidate.instant:
            continue
        key = _deadline_key(kind, identity, row.version, boundary)
        if cancel:
            if key in candidate.state.timer_queue.active:
                candidate.state.timer_queue.cancel(key)
                candidate.state.timers[key] = {
                    **candidate.state.timers[key],
                    "state": "canceled",
                }
                candidate.add({"kind": "relation_timer_cancel", "id": list(key)})
        else:
            timer = {
                "kind": "relation",
                "due": encode(boundary),
                "state": "pending",
                "relation_id": row.relation_id,
                "cause": encode(row.version),
            }
            candidate.state.timers[key] = timer
            candidate.state.timer_queue.add(key, boundary)
            candidate.add({"kind": "relation_timer", "id": list(key), "data": timer})


def apply_relation(
    candidate: Candidate,
    partition: str,
    op: object,
    intent: dict[str, Any],
    out: ItemRef,
    bootstrap: bool,
) -> None:
    """Apply authorized versions privately; integrity is checked after all batches."""
    from dataclasses import replace

    from .codec import decode_record, encode
    from .operations import (
        ActivateObligation,
        AssertEdge,
        CancelEdge,
        CancelObligation,
        CloseEdge,
        EndObligation,
    )

    state = candidate.state
    read = decode_record(intent["read_cut"])
    if not hasattr(read, "index"):
        raise KernelError("RELATION_CUT", "typed invocation read cut required")
    if isinstance(op, (AssertEdge, CloseEdge, CancelEdge)):
        validate_text(op.edge_id)
        identity = ("edge", op.edge_id)
        if identity in candidate.relation_mutated:
            raise KernelError(
                "EDGE_DUPLICATE", "duplicate edge mutation in merged wave"
            )
        candidate.relation_mutated.add(identity)
        if isinstance(op, AssertEdge):
            if op.edge_id in state.edges:
                raise KernelError("EDGE_REUSE", "edge IDs are unique for the epoch")
            descriptor = state.registry.relation(op.relation_id)
            if not state.registry.is_a(
                op.source.type_id, descriptor.source_type
            ) or not state.registry.is_a(op.target.type_id, descriptor.target_type):
                raise KernelError("EDGE_ENDPOINT_TYPE", "endpoint membership mismatch")
            for endpoint in (op.source, op.target):
                if not bootstrap:
                    state.known(endpoint, read)
                elif endpoint not in state.lives:
                    raise KernelError(
                        "EDGE_ENDPOINT", "bootstrap endpoint is not created"
                    )
            if not isinstance(op.valid, Interval):
                raise KernelError(
                    "EDGE_INTERVAL", "explicit half-open validity required"
                )
            candidate.stamp(op.acquired)
            row = Edge(
                op.edge_id,
                op.relation_id,
                op.source,
                op.target,
                op.valid,
                op.acquired,
                candidate.instant,
                partition,
                out,
            )
            if (
                state.manifest.edge_writer(
                    op.relation_id, op.source, state.registry, state.partitions
                )
                != partition
            ):
                raise KernelError(
                    "EDGE_AUTHORITY", "partition is not the bound source writer"
                )
            state.edges[op.edge_id] = (row,)
            state.relation_edges[row.relation_id] = state.relation_edges.get(
                row.relation_id, frozenset()
            ) | {row.edge_id}
            for endpoint in (row.source, row.target):
                state.incident_edges[endpoint] = state.incident_edges.get(
                    endpoint, frozenset()
                ) | {row.edge_id}
        else:
            visible = latest_edge(candidate.before, op.edge_id, read.index)
            old = latest_edge(candidate.before, op.edge_id, candidate.before.cut.index)
            if old.version != visible.version:
                raise KernelError(
                    "EDGE_STALE", "closure cannot use newer hidden relation state"
                )
            if (
                state.manifest.edge_writer(
                    old.relation_id, old.source, state.registry, state.partitions
                )
                != partition
            ):
                raise KernelError(
                    "EDGE_AUTHORITY", "closure needs the bound source writer"
                )
            if old.valid is None or (
                old.valid.end is not None and old.valid.end <= candidate.instant
            ):
                raise KernelError("EDGE_ENDED", "ended/canceled edge cannot be mutated")
            if isinstance(op, CloseEdge):
                if old.valid.start >= candidate.instant:
                    raise KernelError(
                        "EDGE_CLOSE", "closing requires a strictly earlier start"
                    )
                valid = Interval(old.valid.start, candidate.instant)
            else:
                if old.valid.start < candidate.instant:
                    raise KernelError("EDGE_CANCEL", "started edge must be closed")
                valid = None
            _deadlines(candidate, "edge", op.edge_id, old, cancel=True)
            row = replace(
                old,
                valid=valid,
                available=candidate.instant,
                producer=partition,
                version=out,
            )
            state.edges[op.edge_id] = (*state.edges[op.edge_id], row)
            candidate.relation_changes.append((old, row))
        candidate.items[out.item_index]["edge"] = encode(row)
        _remember_scope(candidate, row.relation_id, out)
        _deadlines(candidate, "edge", row.edge_id, row)
    elif isinstance(op, (ActivateObligation, EndObligation, CancelObligation)):
        validate_text(op.obligation_id)
        identity = ("obligation", op.obligation_id)
        if identity in candidate.relation_mutated:
            raise KernelError("OBLIGATION_DUPLICATE", "duplicate obligation mutation")
        candidate.relation_mutated.add(identity)
        if isinstance(op, ActivateObligation):
            if op.obligation_id in state.obligations:
                raise KernelError("OBLIGATION_REUSE", "obligation IDs cannot be reused")
            if op.direction not in DIRECTIONS:
                raise KernelError("OBLIGATION_DIRECTION", "explicit direction required")
            descriptor = state.registry.relation(op.relation_id)
            endpoint_type = (
                descriptor.source_type
                if op.direction == "targets_per_source"
                else descriptor.target_type
            )
            if not state.registry.is_a(op.endpoint_ref.type_id, endpoint_type):
                raise KernelError(
                    "OBLIGATION_ENDPOINT", "directional endpoint membership mismatch"
                )
            if not bootstrap:
                state.known(op.endpoint_ref, read)
            elif op.endpoint_ref not in state.lives:
                raise KernelError(
                    "OBLIGATION_ENDPOINT", "bootstrap endpoint not created"
                )
            if not isinstance(op.valid, Interval) or op.valid.start < candidate.instant:
                raise KernelError(
                    "OBLIGATION_BACKDATE", "activation cannot be backdated"
                )
            if (
                state.manifest.obligation_controller(
                    op.relation_id,
                    op.direction,
                    op.endpoint_ref,
                    state.registry,
                    state.partitions,
                )
                != partition
            ):
                raise KernelError(
                    "OBLIGATION_AUTHORITY", "not the bound minimum controller"
                )
            obligation = Obligation(
                op.obligation_id,
                op.relation_id,
                op.direction,
                op.endpoint_ref,
                op.valid,
                candidate.instant,
                partition,
                out,
            )
            state.obligations[op.obligation_id] = (obligation,)
            state.relation_obligations[op.relation_id] = state.relation_obligations.get(
                op.relation_id, frozenset()
            ) | {op.obligation_id}
            state.incident_obligations[op.endpoint_ref] = (
                state.incident_obligations.get(op.endpoint_ref, frozenset())
                | {op.obligation_id}
            )
        else:
            visible_o = latest_obligation(
                candidate.before, op.obligation_id, read.index
            )
            old_o = latest_obligation(
                candidate.before, op.obligation_id, candidate.before.cut.index
            )
            if old_o.version != visible_o.version:
                raise KernelError(
                    "OBLIGATION_STALE", "termination uses hidden newer state"
                )
            if (
                state.manifest.obligation_controller(
                    old_o.relation_id,
                    old_o.direction,
                    old_o.endpoint_ref,
                    state.registry,
                    state.partitions,
                )
                != partition
            ):
                raise KernelError(
                    "OBLIGATION_AUTHORITY", "termination needs the bound controller"
                )
            if old_o.valid is None or (
                old_o.valid.end is not None and old_o.valid.end <= candidate.instant
            ):
                raise KernelError("OBLIGATION_ENDED", "obligation has ended/canceled")
            if isinstance(op, EndObligation):
                if old_o.valid.start >= candidate.instant:
                    raise KernelError(
                        "OBLIGATION_END", "ending requires an earlier start"
                    )
                valid_o = Interval(old_o.valid.start, candidate.instant)
            else:
                if old_o.valid.start < candidate.instant:
                    raise KernelError(
                        "OBLIGATION_CANCEL", "started obligation must end"
                    )
                valid_o = None
            _deadlines(candidate, "obligation", op.obligation_id, old_o, cancel=True)
            obligation = replace(
                old_o,
                valid=valid_o,
                available=candidate.instant,
                producer=partition,
                version=out,
            )
            state.obligations[op.obligation_id] = (
                *state.obligations[op.obligation_id],
                obligation,
            )
        candidate.items[out.item_index]["obligation"] = encode(obligation)
        _remember_scope(candidate, obligation.relation_id, out)
        _deadlines(candidate, "obligation", obligation.obligation_id, obligation)
    else:
        raise KernelError("RELATION_OPERATION", "unsupported relation mutation")


def _effective_pairs(
    store: Store, relation_id: str, at: Instant, *, before_boundary: bool = False
) -> frozenset[tuple[EntityRef, EntityRef]]:
    pairs = set()
    for identity in store.relation_edges.get(relation_id, ()):
        edge = latest_edge(store, identity, store.cut.index)
        interval = edge.valid
        active = interval is not None and (
            interval.start < at and (interval.end is None or interval.end >= at)
            if before_boundary
            else interval.contains(at)
        )
        if active:
            assert interval is not None
            pairs.add((edge.source, edge.target))
    return frozenset(pairs)


def notify_relation(
    candidate: Candidate, relation_id: str, cause: ItemRef, *, boundary: bool = False
) -> None:
    """Record effective final-graph change/expiry under declared reader scopes."""
    from fnmatch import fnmatchcase

    from .codec import encode
    from .messages import Dirty
    from .state import Work
    from .time import Cut
    from .transactions import eligibility

    state = candidate.state
    cut = Cut(candidate.index, candidate.instant)
    if state.cut != cut:
        state.cuts.append(cut)
    old = _effective_pairs(
        candidate.before, relation_id, candidate.instant, before_boundary=boundary
    )
    new = _effective_pairs(state, relation_id, candidate.instant)
    for recipient, partition in sorted(state.partitions.items()):
        for dependency in partition.relation_consumes:
            if dependency.relation_id != relation_id:
                continue

            def scoped(
                pairs: frozenset[tuple[EntityRef, EntityRef]],
                dependency: RelationDependency = dependency,
            ) -> frozenset[tuple[EntityRef, EntityRef]]:
                return frozenset(
                    (source, target)
                    for source, target in pairs
                    if fnmatchcase(source.id, dependency.id_pattern)
                    and (
                        dependency.source_type is None
                        or state.registry.is_a(source.type_id, dependency.source_type)
                    )
                )

            changed = scoped(old) != scoped(new)
            if dependency.value_only and not changed:
                continue
            at = eligibility(
                state,
                recipient,
                candidate.instant,
                candidate.instant,
                dependency.lag_ns,
            )
            dirty = Dirty(
                None,
                "relation",
                at,
                cause,
                changed,
                {"relation_id": relation_id, "boundary": boundary},
                candidate.budget,
            )
            state.work.append(Work(recipient, at, cause, dirty=dirty))
            candidate.add(
                {"kind": "dirty", "recipient": recipient, "notification": encode(dirty)}
            )


def validate_relations(candidate: Candidate) -> None:
    """Validate lifetimes and directional integrity once on the merged graph."""
    state = candidate.state
    affected = set(candidate.relation_affected)
    for removed in candidate.removing:
        affected.update(
            state.edges[e][-1].relation_id
            for e in state.incident_edges.get(removed, ())
        )
        affected.update(
            state.obligations[o][-1].relation_id
            for o in state.incident_obligations.get(removed, ())
        )
    if not affected:
        return
    from .relation_sweep import validate_graph

    for relation_id in sorted(affected):
        edges = [
            state.edges[e][-1]
            for e in sorted(state.relation_edges.get(relation_id, ()))
        ]
        obligations = [
            state.obligations[o][-1]
            for o in sorted(state.relation_obligations.get(relation_id, ()))
        ]
        rows: list[Edge | Obligation] = [*edges, *obligations]
        for row in rows:
            endpoints = (
                (row.source, row.target)
                if isinstance(row, Edge)
                else (row.endpoint_ref,)
            )
            old_rows = (
                candidate.before.edges.get(row.edge_id, ())
                if isinstance(row, Edge)
                else candidate.before.obligations.get(row.obligation_id, ())
            )
            old_interval = old_rows[-1].valid if old_rows else None
            for endpoint in endpoints:
                if (
                    endpoint in candidate.removing
                    and old_interval is not None
                    and (
                        old_interval.end is None or old_interval.end > candidate.instant
                    )
                    and row.version.record_index == candidate.index
                    and row.producer != state.controllers[endpoint]
                    and not any(
                        {row.producer, state.controllers[endpoint]} <= set(cohort)
                        for cohort in state.manifest.cohorts
                    )
                ):
                    raise KernelError(
                        "RELATION_COHORT",
                        "same-wave incident cleanup requires controller cohort",
                    )
                if row.valid is None:
                    continue
                life = state.lives[endpoint]
                if row.valid.start < life.created.instant or (
                    life.removed is not None
                    and (row.valid.end is None or row.valid.end > life.removed.instant)
                ):
                    raise KernelError(
                        "RELATION_LIFETIME",
                        "edge/obligation needs authorized lifetime closure",
                        endpoint=endpoint,
                    )
        descriptor = state.registry.relation(relation_id)
        validate_graph(descriptor, edges, obligations)
        if descriptor.sources_per_target.maximum == 1:
            for edge in edges:
                if (
                    edge.version.record_index != candidate.index
                    or edge.edge_id in candidate.before.edges
                    or edge.valid is None
                ):
                    continue
                for old_versions in candidate.before.edges.values():
                    old = old_versions[-1]
                    if (
                        old.relation_id == relation_id
                        and old.target == edge.target
                        and old.valid is not None
                        and (edge.valid.end is None or old.valid.start < edge.valid.end)
                        and (old.valid.end is None or edge.valid.start < old.valid.end)
                        and old.producer != edge.producer
                        and not any(
                            {old.producer, edge.producer} <= set(cohort)
                            for cohort in state.manifest.cohorts
                        )
                    ):
                        raise KernelError(
                            "RELATION_COHORT",
                            "cross-owner swap requires a declared cohort",
                        )
    for relation_id in sorted(candidate.relation_affected):
        notify_relation(candidate, relation_id, candidate.relation_causes[relation_id])
