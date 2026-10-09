"""Immutable prefix views over complete bitemporal fact history."""

from __future__ import annotations

import heapq
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, overload

from .engine import Batch, Partition
from .errors import KernelError
from .ids import EntityRef, FieldKey, FramePrefix, ItemRef, LocalCause
from .queues import TimerQueue, WorkQueue
from .storage import AppendList, Overlay, RecordLog
from .time import Cut, Instant, Interval, Stamp
from .values import FrozenValue

if TYPE_CHECKING:
    from .binding import BindingManifest
    from .ingress import IngressReceipt
    from .messages import Actions, ActionState, Dirty
    from .registry import MemoryRegistry
    from .relations import Edge, Obligation
    from .sampling import RecordedFrame, SampleSpec


@dataclass(frozen=True, slots=True)
class Absent:
    """Tagged absence, distinct from JSON null and every concrete value."""


ABSENT = Absent()


@dataclass(frozen=True, slots=True)
class Fact:
    """A kernel-stamped immutable field version."""

    key: FieldKey
    value: FrozenValue
    acquired: Stamp
    mapped_ns: int
    available: Instant
    valid: Interval
    producer: str
    version: ItemRef


@dataclass(frozen=True, slots=True)
class Retraction:
    """An explicit absence version that shadows older facts."""

    key: FieldKey
    valid: Interval
    reason: str
    available: Instant
    producer: str
    version: ItemRef


@dataclass(frozen=True, slots=True)
class Life:
    """Creation/death history is resolved at the reader's prefix."""

    ref: EntityRef
    created: Cut
    removed: Cut | None = None


@dataclass(frozen=True, slots=True)
class Work:
    """Queued recipient-specific delivery or dirty notification."""

    recipient: str
    eligible: Instant
    cause: ItemRef
    message_id: str | None = None
    dirty: Dirty | None = None
    timer_key: tuple[str, ...] | None = None


class FactVersions(Sequence[Fact | Retraction]):
    """Per-key immutable prefixes of compact append-only version rows.

    Keys and item coordinates are reconstructed on reads. Each write retains
    its real value, source stamp, mapped/acquired/available/valid times and owner;
    no version is pruned or inferred from another sample.
    """

    __slots__ = ("key", "columns", "indices", "length", "starts", "monotone", "cache")

    def __init__(
        self,
        key: FieldKey,
        columns: tuple[list[Any], ...] | None = None,
        indices: list[int] | None = None,
        length: int = 0,
        starts: list[Instant] | None = None,
        monotone: bool = True,
        cache: dict[int, tuple[list[Instant], list[int]]] | None = None,
    ) -> None:
        self.key = key
        self.columns = tuple([] for _ in range(7)) if columns is None else columns
        self.indices = [] if indices is None else indices
        self.length = length
        self.starts = [] if starts is None else starts
        self.monotone = monotone
        self.cache = {} if cache is None else cache

    def with_version(self, fact: Fact | Retraction, index: int) -> FactVersions:
        """Append to shared columns; old readers retain their exact prefix length."""
        columns, indices, starts, cache = (
            self.columns,
            self.indices,
            self.starts,
            self.cache,
        )
        if len(indices) != self.length:
            columns = tuple(column[: self.length] for column in columns)
            indices = indices[: self.length]
            starts = starts[: self.length]
            cache = {}
        values = (
            (
                fact.value,
                fact.acquired,
                fact.mapped_ns,
                fact.available,
                fact.valid,
                fact.producer,
                fact.version.item_index,
            )
            if isinstance(fact, Fact)
            else (
                fact.reason,
                None,
                None,
                fact.available,
                fact.valid,
                fact.producer,
                fact.version.item_index,
            )
        )
        for column, value in zip(columns, values, strict=True):
            column.append(value)
        indices.append(index)
        monotone = (
            self.monotone
            and fact.valid.end is None
            and (not starts or fact.valid.start >= starts[-1])
        )
        starts.append(fact.valid.start)
        return FactVersions(
            self.key, columns, indices, self.length + 1, starts, monotone, cache
        )

    def known_count(self, record_index: int) -> int:
        """Locate the exact knowledge prefix by binary search."""
        return bisect_right(self.indices, record_index, hi=self.length)

    def select(self, at: Instant, record_index: int, *, left: bool = False) -> int:
        """Indexed greatest-known interval lookup, with an optional open-left limit.

        Monotone open intervals need two binary searches. General overlapping
        intervals compile a sweep index once per requested knowledge prefix;
        subsequent queries are logarithmic. Four indexes are shared as a bounded
        cache; the complete underlying history is never evicted.
        """
        count = self.known_count(record_index)
        search = bisect_left if left else bisect_right
        if self.monotone:
            return search(self.starts, at, hi=count) - 1
        compiled = self.cache.get(count)
        if compiled is None:
            events: dict[Instant, list[tuple[int, bool]]] = {}
            for i in range(count):
                valid = self.columns[4][i]
                events.setdefault(valid.start, []).append((i, True))
                if valid.end is not None:
                    events.setdefault(valid.end, []).append((i, False))
            active: set[int] = set()
            heap: list[int] = []
            coordinates: list[Instant] = []
            winners: list[int] = []
            for point, changes in sorted(events.items()):
                for index, added in changes:
                    if added:
                        active.add(index)
                        heapq.heappush(heap, -index)
                    else:
                        active.remove(index)
                while heap and -heap[0] not in active:
                    heapq.heappop(heap)
                winner = -heap[0] if heap else -1
                if not winners or winners[-1] != winner:
                    coordinates.append(point)
                    winners.append(winner)
            compiled = (coordinates, winners)
            if len(self.cache) == 4:
                self.cache.pop(next(iter(self.cache)))
            self.cache[count] = compiled
        coordinates, winners = compiled
        position = search(coordinates, at) - 1
        return winners[position] if position >= 0 else -1

    def at(self, index: int) -> Fact | Retraction:
        """Materialize one immutable public version from its retained columns."""
        value, acquired, mapped, available, valid, producer, item = (
            c[index] for c in self.columns
        )
        ref = ItemRef(self.indices[index], item)
        if acquired is None:
            return Retraction(self.key, valid, value, available, producer, ref)
        return Fact(self.key, value, acquired, mapped, available, valid, producer, ref)

    @overload
    def __getitem__(self, index: int) -> Fact | Retraction: ...

    @overload
    def __getitem__(self, index: slice) -> list[Fact | Retraction]: ...

    def __getitem__(
        self, index: int | slice
    ) -> Fact | Retraction | list[Fact | Retraction]:
        if isinstance(index, slice):
            return [self.at(i) for i in range(*index.indices(self.length))]
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        return self.at(index)

    def __len__(self) -> int:
        return self.length

    def __iter__(self) -> Iterator[Fact | Retraction]:
        for i in range(self.length):
            yield self.at(i)


class Store:
    """Internal candidate state; only coordinator transactions mutate it."""

    def __init__(
        self,
        registry: MemoryRegistry,
        manifest: BindingManifest,
        partitions: dict[str, Partition],
        actions: Actions,
    ) -> None:
        self.registry = registry
        self.manifest = manifest
        self.partitions = partitions
        self.dependency_fields = frozenset(
            d.field for p in partitions.values() for d in p.consumes
        )
        self.actions = actions
        self.action_snapshots: Overlay[int, Actions] = Overlay({0: actions.clone()})
        self.action_snapshot_indices: AppendList[int] = AppendList([0])
        self.cuts: AppendList[Cut] = AppendList([Cut(0, Instant(0))])
        self.records = RecordLog(budget=actions.budget)
        self.lives: Overlay[EntityRef, Life] = Overlay()
        self.live_ids: Overlay[str, EntityRef] = Overlay()
        self.generations: Overlay[str, int] = Overlay()
        self.writers: Overlay[FieldKey, str] = Overlay()
        self.controllers: Overlay[EntityRef, str] = Overlay()
        self.facts: Overlay[FieldKey, FactVersions] = Overlay()
        self.frames: Overlay[str, Sequence[RecordedFrame]] = Overlay()
        self.frame_indices: Overlay[str, AppendList[int]] = Overlay()
        self.edges: Overlay[str, tuple[Edge, ...]] = Overlay()
        self.obligations: Overlay[str, tuple[Obligation, ...]] = Overlay()
        self.relation_edges: Overlay[str, frozenset[str]] = Overlay()
        self.relation_obligations: Overlay[str, frozenset[str]] = Overlay()
        self.incident_edges: Overlay[EntityRef, frozenset[str]] = Overlay()
        self.incident_obligations: Overlay[EntityRef, frozenset[str]] = Overlay()
        self.sample_specs: Mapping[str, SampleSpec] = MappingProxyType({})
        self.sample_partitions: Mapping[str, str] = MappingProxyType({})
        self.sample_levels: Mapping[str, int] = MappingProxyType({})
        self.ready: Overlay[tuple[EntityRef, str], ItemRef] = Overlay()
        self.messages: Overlay[str, Any] = Overlay()
        self.work = WorkQueue()
        self.pending_ingress: Overlay[str, dict[str, Any]] = Overlay()
        self.idempotency: Overlay[str, tuple[bytes, str]] = Overlay()
        self.sequences: Overlay[tuple[str, str], int] = Overlay()
        self.intents: Overlay[ItemRef, dict[str, Any]] = Overlay()
        self.pending_intents: dict[str, ItemRef] = {}
        self.latest_returned: dict[tuple[str, str], ItemRef] = {}
        self.frontiers = {p: (Instant(0), 0) for p in partitions}
        self.native_cuts = {p: self.cuts[0] for p in partitions}
        self.timers: Overlay[tuple[str, ...], dict[str, Any]] = Overlay()
        self.timer_queue = TimerQueue()
        self.sealed_ns: int | None = None
        self.watermark_ns: int | None = None
        self.watermarks: dict[str, int] = {}
        self.source_progress: dict[str, int] = {}
        self.ingress_dependencies: dict[str, tuple[str, ...]] = {}
        self.ingress_receipts: Overlay[str, IngressReceipt] = Overlay()
        self.ingress_dedup: Overlay[tuple[str, str], tuple[bytes, IngressReceipt]] = (
            Overlay()
        )
        self.run_target: int | None = None
        self.pending_wall_clock_hold: int | None = None
        self.faulted = False
        self.max_microsteps = 1024
        self.allow_frame_prefix = False
        self.provenance = "full"

    @property
    def cut(self) -> Cut:
        """The latest fully applied journal prefix."""
        return self.cuts[-1]

    def clone(self) -> Store:
        """Copy mutable indexes; all history values remain frozen/shared."""
        import copy

        other = copy.copy(self)
        for name in (
            "lives",
            "live_ids",
            "generations",
            "writers",
            "controllers",
            "facts",
            "frames",
            "frame_indices",
            "edges",
            "obligations",
            "relation_edges",
            "relation_obligations",
            "incident_edges",
            "incident_obligations",
            "ready",
            "messages",
            "pending_ingress",
            "idempotency",
            "sequences",
            "frontiers",
            "pending_intents",
            "latest_returned",
            "native_cuts",
            "watermarks",
            "source_progress",
            "ingress_receipts",
            "ingress_dedup",
            "action_snapshots",
        ):
            value = getattr(self, name)
            setattr(
                other,
                name,
                value.fork() if isinstance(value, Overlay) else value.copy(),
            )
        other.actions = self.actions.clone()
        other.intents = self.intents.fork()
        other.timers = self.timers.fork()
        other.timer_queue = self.timer_queue.fork()
        other.work = self.work.fork()
        other.cuts = self.cuts.fork()
        other.records = self.records.fork()
        other.action_snapshot_indices = self.action_snapshot_indices.fork()
        return other

    def check_cut(self, cut: Cut) -> None:
        """Reject forged, future or mismatched prefix coordinates."""
        if cut.index >= len(self.cuts) or self.cuts[cut.index] != cut:
            raise KernelError("CUT", "not an issued prefix cut")

    def known(self, ref: EntityRef, cut: Cut) -> Life:
        """Require identity existence in this namespace and knowledge prefix."""
        self.check_cut(cut)
        if (ref.run_id, ref.epoch) != (self.manifest.run_id, self.manifest.epoch):
            raise KernelError("NAMESPACE", "foreign entity reference")
        life = self.lives.get(ref)
        if life is None or life.created.index > cut.index:
            raise KernelError("ENTITY_UNKNOWN", "generation was not created at cut")
        return life

    def alive(self, ref: EntityRef, cut: Cut, at: Instant) -> bool:
        """Resolve lifecycle at the same knowledge cut as the field."""
        life = self.known(ref, cut)
        return life.created.instant <= at and not (
            life.removed is not None
            and life.removed.index <= cut.index
            and life.removed.instant <= at
        )

    def field(self, key: FieldKey, at: Instant, cut: Cut) -> Fact | Absent:
        """Select greatest known overlapping version, including retractions."""
        if key not in self.writers:
            self.known(key[0], cut)
            raise KernelError("FIELD_INACTIVE", "field is not selected for generation")
        if not self.alive(key[0], cut, at):
            return ABSENT
        versions = self.facts.get(key)
        if versions is None:
            return ABSENT
        index = versions.select(at, cut.index)
        if index < 0:
            return ABSENT
        version = versions[index]
        return ABSENT if isinstance(version, Retraction) else version

    def field_before(self, key: FieldKey, boundary: Instant, cut: Cut) -> Fact | Absent:
        """Effective value on the open left side, without inventing a predecessor."""
        life = self.known(key[0], cut)
        if life.created.instant >= boundary or (
            life.removed is not None
            and life.removed.index <= cut.index
            and life.removed.instant < boundary
        ):
            return ABSENT
        versions = self.facts.get(key)
        if versions is not None:
            index = versions.select(boundary, cut.index, left=True)
            if index >= 0:
                version = versions[index]
                return ABSENT if isinstance(version, Retraction) else version
        return ABSENT

    def history(
        self, key: FieldKey, start: Instant, end: Instant, cut: Cut
    ) -> tuple[Fact | Retraction, ...]:
        """All known versions intersecting the effective lifecycle interval."""
        Interval(start, end)
        life = self.known(key[0], cut)
        if key not in self.writers:
            raise KernelError("FIELD_INACTIVE", "unselected field")
        lo = max(start, life.created.instant)
        hi = end
        if life.removed is not None and life.removed.index <= cut.index:
            hi = min(hi, life.removed.instant)
        if hi <= lo:
            return ()
        return tuple(
            v
            for v in self.facts.get(key, ())
            if v.version.record_index <= cut.index and v.valid.intersects(lo, hi)
        )

    def cut_at(self, ns: int, cap: Cut) -> Cut:
        """Newest prefix no later than physical time and the explicit cap."""
        low, high = 0, cap.index + 1
        while low < high:
            middle = (low + high) // 2
            if self.cuts[middle].instant.ns <= ns:
                low = middle + 1
            else:
                high = middle
        return self.cuts[low - 1]


class StateView:
    """Frozen prefix read API; evolution views additionally enforce declarations."""

    def __init__(
        self,
        store: Store,
        cut: Cut | None = None,
        transaction_base_cut: Cut | None = None,
        partition: str | None = None,
        instant: Instant | None = None,
        native_input_cut: Cut | None = None,
        native_reached_ns: int = 0,
        invocation_ref: ItemRef | None = None,
        phase: str | None = None,
        logical_before: Instant | None = None,
        native_before: int | None = None,
    ) -> None:
        self.invocation_ref = invocation_ref
        self.phase = phase
        self.logical_before = logical_before
        self.native_before = native_before
        self._store = store
        self.cut = store.cut if cut is None else cut
        store.check_cut(self.cut)
        self.transaction_base_cut = (
            self.cut if transaction_base_cut is None else transaction_base_cut
        )
        self.partition = partition
        self.instant = self.cut.instant if instant is None else instant
        self.native_input_cut = (
            self.cut if native_input_cut is None else native_input_cut
        )
        self.native_reached_ns = native_reached_ns

    @property
    def provenance(self) -> str:
        """Pinned invocation audit level, also exposed to SDK/RPC engines."""
        return self._store.provenance

    def _cut(self, known_at: Cut | None) -> Cut:
        cut = self.cut if known_at is None else known_at
        self._store.check_cut(cut)
        if cut.index > self.cut.index:
            raise KernelError("READ_FUTURE", "knowledge exceeds invocation read cut")
        return cut

    def _scope(self, key: FieldKey, at: Instant, cut: Cut) -> Cut | None:
        if self.partition is None:
            return cut
        p = self._store.partitions[self.partition]
        deps = [
            d
            for d in p.consumes
            if d.field == key[1]
            and fnmatchcase(key[0].id, d.id_pattern)
            and (
                d.type_id is None
                or self._store.registry.is_a(key[0].type_id, d.type_id)
            )
        ]
        if not deps and self._store.writers.get(key) != self.partition:
            raise KernelError("READ_UNDECLARED", "undeclared field read")
        lag = min(d.lag_ns for d in deps) if deps else 0
        limit = self.instant.ns - lag
        if limit < 0:
            return None
        if at.ns > limit or (lag == 0 and at > self.instant):
            raise KernelError(
                "READ_VALIDITY_FUTURE", "validity exceeds lag/native scope"
            )
        return self._store.cut_at(limit, cut)

    def field(
        self, key: FieldKey, valid_at: Instant, known_at: Cut | None = None
    ) -> Fact | Absent:
        """Read a field at explicit validity and bounded knowledge coordinates."""
        issued = self._cut(known_at)
        self._store.known(key[0], issued)
        if key not in self._store.writers:
            raise KernelError(
                "FIELD_INACTIVE", "field not selected for this generation"
            )
        cut = self._scope(key, valid_at, issued)
        if cut is None or self._store.lives[key[0]].created.index > cut.index:
            return ABSENT
        return self._store.field(key, valid_at, cut)

    def history(
        self, key: FieldKey, start: Instant, end: Instant, known_at: Cut | None = None
    ) -> tuple[Fact | Retraction, ...]:
        """Read complete version history, without implicit resampling."""
        issued = self._cut(known_at)
        self._store.known(key[0], issued)
        if key not in self._store.writers:
            raise KernelError(
                "FIELD_INACTIVE", "field not selected for this generation"
            )
        cut = self._scope(key, start, issued)
        if self.partition is not None:
            self._scope(key, end, issued)
        if self.partition is not None and end > self.instant:
            raise KernelError("READ_VALIDITY_FUTURE", "history exceeds evolution view")
        return (
            ()
            if cut is None or self._store.lives[key[0]].created.index > cut.index
            else self._store.history(key, start, end, cut)
        )

    def lifecycle(self, ref: EntityRef, known_at: Cut | None = None) -> Life:
        """Inspect declared lifecycle, resolving death at this knowledge cut."""
        cut = self._cut(known_at)
        if self.partition is not None:
            p = self._store.partitions[self.partition]
            if not (p.lifecycle or ref.type_id in p.lifecycle_reads):
                raise KernelError("READ_UNDECLARED", "undeclared lifecycle read")
        life = self._store.known(ref, cut)
        return Life(
            ref,
            life.created,
            life.removed
            if life.removed is not None and life.removed.index <= cut.index
            else None,
        )

    def action(self, command_id: str) -> ActionState:
        """Inspect canonical pending/receipt-derived action state."""
        # Views retain their candidate store, so later live commits cannot alter it.
        indices = self._store.action_snapshot_indices
        snapshot_index = indices[bisect_right(indices, self.cut.index) - 1]
        state = self._store.action_snapshots[snapshot_index].action(command_id)
        if self.partition is not None:
            related = state.target == self.partition or (
                state.source_kind == "partition" and state.source == self.partition
            )
            control = self.partition in self._store.manifest.cancel_sources
            if not related and not control:
                raise KernelError("READ_UNDECLARED", "unrelated action read")
        return state

    def committed_operation(self, invocation: ItemRef, local: LocalCause) -> ItemRef:
        """Resolve an own committed proposal from its original invocation.

        LocalCause alone is invocation-local. Retain it with invocation_ref to
        learn the actual publication reference later, without predicting IDs or
        waiting for a message echo. Resolution grants no additional cause scope.
        Lean retains only each partition's latest returned call per phase;
        resolve in the next same-phase callback and retain the resulting ItemRef.
        Full retains every returned invocation. Issued views keep their snapshots.
        """
        intent = self._store.intents.get(invocation)
        if intent is None:
            raise KernelError("CAUSE_UNKNOWN", "unknown invocation reference")
        if self.partition is not None and intent["partition"] != self.partition:
            raise KernelError("CAUSE_STATE_SCOPE", "another partition's invocation")
        if intent["status"] != "returned":
            raise KernelError("CAUSE_UNCOMMITTED", "invocation has not committed")
        refs = intent["operation_refs"]
        if local.index >= len(refs):
            raise KernelError("CAUSE_LOCAL", "local operation does not exist")
        ref: ItemRef = refs[local.index]
        if ref.record_index > self.cut.index:
            raise KernelError("CAUSE_FUTURE", "operation exceeds read cut")
        return ref

    def sample_frames(
        self, context_id: str, known_at: Cut | None = None
    ) -> tuple[RecordedFrame, ...]:
        """Read recorded context frames only, capped at the issued knowledge cut."""
        cut = self._cut(known_at)
        self._sample_access(context_id)
        return tuple(
            row
            for row in self._store.frames.get(context_id, ())
            if row.version.record_index <= cut.index and row.available <= cut.instant
        )

    def _sample_access(self, context_id: str) -> None:
        """Validate declared context authority without walking its history."""
        if context_id not in self._store.sample_specs:
            raise KernelError("SAMPLE_CONTEXT", "unknown sampled context")
        if self.partition is not None:
            own = self._store.sample_partitions.get(self.partition)
            spec = self._store.sample_specs.get(own) if own is not None else None
            if own != context_id and (
                spec is None or context_id not in spec.frame_inputs
            ):
                raise KernelError("READ_UNDECLARED", "undeclared sampled frame read")

    def sample_frame_prefix(
        self, context_id: str, known_at: Cut | None = None
    ) -> FramePrefix:
        """Reference the exact visible frame sequence without materializing it."""
        cut = self._cut(known_at)
        self._sample_access(context_id)
        rows = self._store.frames.get(context_id, ())
        indices = self._store.frame_indices.get(context_id)
        if indices is None:
            # Remote projections contain the exact committed frame prefix and
            # reconstruct this local index without changing the RPC protocol.
            count = sum(
                row.version.record_index <= cut.index and row.available <= cut.instant
                for row in rows
            )
        else:
            count = bisect_right(indices, cut.index)
        return FramePrefix(
            context_id, count, rows[count - 1].version if count else None
        )

    def relations(
        self, relation_id: str, valid_at: Instant, known_at: Cut | None = None
    ) -> tuple[Edge, ...]:
        """Read effective directed edges at bounded validity/knowledge coordinates."""
        from .relations import latest_edge

        issued = self._cut(known_at)
        self._store.registry.relation(relation_id)
        dependencies = (
            ()
            if self.partition is None
            else tuple(
                d
                for d in self._store.partitions[self.partition].relation_consumes
                if d.relation_id == relation_id
            )
        )
        owned = (
            self.partition is not None
            and relation_id in self._store.partitions[self.partition].relation_produces
        )
        if self.partition is not None and not dependencies and not owned:
            raise KernelError("READ_UNDECLARED", "undeclared relation read")
        # Whole-relation queries must respect every declared slice's validity
        # lag; knowledge is then capped separately for each immutable source.
        validity_lag = max((d.lag_ns for d in dependencies), default=0)
        if self.partition is not None and self.instant.ns < validity_lag:
            return ()
        if self.partition is not None and (
            valid_at.ns > self.instant.ns - validity_lag
            or (validity_lag == 0 and valid_at > self.instant)
        ):
            raise KernelError("READ_VALIDITY_FUTURE", "relation exceeds declared lag")
        result = []
        for edge_id in sorted(self._store.relation_edges.get(relation_id, ())):
            versions = self._store.edges[edge_id]
            source = versions[0].source
            scoped = tuple(
                d
                for d in dependencies
                if fnmatchcase(source.id, d.id_pattern)
                and (
                    d.source_type is None
                    or self._store.registry.is_a(source.type_id, d.source_type)
                )
            )
            own_scope = (
                owned
                and self._store.manifest.edge_writer(
                    relation_id, source, self._store.registry, self._store.partitions
                )
                == self.partition
            )
            if self.partition is not None and not scoped and not own_scope:
                continue
            lag = min((d.lag_ns for d in scoped), default=0)
            if self.partition is not None and self.instant.ns < lag:
                continue
            cut = (
                issued
                if self.partition is None
                else self._store.cut_at(self.instant.ns - lag, issued)
            )
            if versions[0].version.record_index > cut.index:
                continue
            edge = latest_edge(self._store, edge_id, cut.index)
            if (
                edge.valid is not None
                and edge.valid.contains(valid_at)
                and self._store.alive(edge.source, cut, valid_at)
                and self._store.alive(edge.target, cut, valid_at)
            ):
                result.append(edge)
        return tuple(result)

    def relation_history(
        self, edge_id: str, known_at: Cut | None = None
    ) -> tuple[Edge, ...]:
        """Read assertion/closure/cancellation versions without pruning history."""
        from .relations import latest_edge

        cut = self._cut(known_at)
        edge = latest_edge(self._store, edge_id, cut.index)
        if self.partition is not None:
            p = self._store.partitions[self.partition]
            if (
                not any(
                    d.relation_id == edge.relation_id
                    and fnmatchcase(edge.source.id, d.id_pattern)
                    and (
                        d.source_type is None
                        or self._store.registry.is_a(edge.source.type_id, d.source_type)
                    )
                    for d in p.relation_consumes
                )
                and self._store.manifest.edge_writer(
                    edge.relation_id,
                    edge.source,
                    self._store.registry,
                    self._store.partitions,
                )
                != self.partition
            ):
                raise KernelError("READ_UNDECLARED", "undeclared edge history")
            lags = [
                d.lag_ns
                for d in p.relation_consumes
                if d.relation_id == edge.relation_id
                and fnmatchcase(edge.source.id, d.id_pattern)
                and (
                    d.source_type is None
                    or self._store.registry.is_a(edge.source.type_id, d.source_type)
                )
            ]
            lag = min(lags) if lags else 0
            if self.instant.ns - lag < 0:
                return ()
            cut = self._store.cut_at(self.instant.ns - lag, cut)
        return tuple(
            v for v in self._store.edges[edge_id] if v.version.record_index <= cut.index
        )

    def obligation_history(
        self, obligation_id: str, known_at: Cut | None = None
    ) -> tuple[Obligation, ...]:
        """Read explicit minimum activation/termination versions by prefix."""
        from .relations import latest_obligation

        cut = self._cut(known_at)
        obligation = latest_obligation(self._store, obligation_id, cut.index)
        if self.partition is not None:
            p = self._store.partitions[self.partition]
            own = (
                self._store.manifest.obligation_controller(
                    obligation.relation_id,
                    obligation.direction,
                    obligation.endpoint_ref,
                    self._store.registry,
                    self._store.partitions,
                )
                == self.partition
            )
            if not own and not any(
                d.relation_id == obligation.relation_id for d in p.relation_consumes
            ):
                raise KernelError("READ_UNDECLARED", "undeclared obligation history")
            if not own:
                dependencies = tuple(
                    d
                    for d in p.relation_consumes
                    if d.relation_id == obligation.relation_id
                    and (
                        obligation.direction == "sources_per_target"
                        or (
                            fnmatchcase(obligation.endpoint_ref.id, d.id_pattern)
                            and (
                                d.source_type is None
                                or self._store.registry.is_a(
                                    obligation.endpoint_ref.type_id, d.source_type
                                )
                            )
                        )
                    )
                )
                if not dependencies:
                    raise KernelError(
                        "READ_UNDECLARED", "obligation outside source scope"
                    )
                lag = min(d.lag_ns for d in dependencies)
                if self.instant.ns < lag:
                    return ()
                cut = self._store.cut_at(self.instant.ns - lag, cut)
        return tuple(
            v
            for v in self._store.obligations[obligation_id]
            if v.version.record_index <= cut.index
        )

    def batch(
        self, operations: tuple[object, ...] = (), native_reached_ns: int | None = None
    ) -> Batch:
        """Echo invocation metadata for a concise engine return."""
        return Batch(
            self.instant,
            self.transaction_base_cut,
            self.cut,
            self.native_reached_ns if native_reached_ns is None else native_reached_ns,
            self.native_input_cut,
            operations,
        )
