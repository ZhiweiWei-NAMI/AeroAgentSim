"""Immutable prefix views over complete bitemporal fact history."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

from .engine import Batch, Partition
from .errors import KernelError
from .ids import EntityRef, FieldKey, ItemRef
from .time import Cut, Instant, Interval, Stamp
from .values import FrozenValue

if TYPE_CHECKING:
    from .binding import BindingManifest
    from .messages import Actions, ActionState, Dirty
    from .registry import MemoryRegistry


@dataclass(frozen=True)
class Absent:
    """Tagged absence, distinct from JSON null and every concrete value."""


ABSENT = Absent()


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class Retraction:
    """An explicit absence version that shadows older facts."""

    key: FieldKey
    valid: Interval
    reason: str
    available: Instant
    producer: str
    version: ItemRef


@dataclass(frozen=True)
class Life:
    """Creation/death history is resolved at the reader's prefix."""

    ref: EntityRef
    created: Cut
    removed: Cut | None = None


@dataclass(frozen=True)
class Work:
    """Queued recipient-specific delivery or dirty notification."""

    recipient: str
    eligible: Instant
    cause: ItemRef
    message_id: str | None = None
    dirty: Dirty | None = None
    timer_key: tuple[str, str] | None = None


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
        self.actions = actions
        self.action_snapshots = {0: actions.clone()}
        self.cuts: list[Cut] = [Cut(0, Instant(0))]
        self.records: list[dict[str, Any]] = []
        self.lives: dict[EntityRef, Life] = {}
        self.generations: dict[str, int] = {}
        self.writers: dict[FieldKey, str] = {}
        self.controllers: dict[EntityRef, str] = {}
        self.facts: dict[FieldKey, tuple[Fact | Retraction, ...]] = {}
        self.ready: dict[tuple[EntityRef, str], ItemRef] = {}
        self.messages: dict[str, Any] = {}
        self.work: list[Work] = []
        self.pending_ingress: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, tuple[bytes, str]] = {}
        self.sequences: dict[tuple[str, str], int] = {}
        self.intents: dict[ItemRef, dict[str, Any]] = {}
        self.frontiers = {p: (Instant(0), 0) for p in partitions}
        self.native_cuts = {p: self.cuts[0] for p in partitions}
        self.timers: dict[tuple[str, str], dict[str, Any]] = {}
        self.sealed_ns: int | None = None
        self.run_target: int | None = None
        self.faulted = False

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
            "generations",
            "writers",
            "controllers",
            "facts",
            "ready",
            "messages",
            "pending_ingress",
            "idempotency",
            "sequences",
            "frontiers",
            "native_cuts",
            "action_snapshots",
        ):
            setattr(other, name, getattr(self, name).copy())
        other.actions = self.actions.clone()
        other.intents = {k: v.copy() for k, v in self.intents.items()}
        other.timers = {k: v.copy() for k, v in self.timers.items()}
        other.work = self.work.copy()
        other.cuts = self.cuts.copy()
        other.records = self.records.copy()
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
        for version in reversed(self.facts.get(key, ())):
            if version.version.record_index <= cut.index and version.valid.contains(at):
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
        return next(
            c for c in reversed(self.cuts[: cap.index + 1]) if c.instant.ns <= ns
        )


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
    ) -> None:
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
        if cut is None:
            return ABSENT
        return self._store.field(key, valid_at, cut)

    def history(
        self, key: FieldKey, start: Instant, end: Instant, known_at: Cut | None = None
    ) -> tuple[Fact | Retraction, ...]:
        """Read complete version history, without implicit resampling."""
        cut = self._scope(key, start, self._cut(known_at))
        if self.partition is not None:
            self._scope(key, end, self._cut(known_at))
        if self.partition is not None and end > self.instant:
            raise KernelError("READ_VALIDITY_FUTURE", "history exceeds evolution view")
        return () if cut is None else self._store.history(key, start, end, cut)

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
        snapshot_index = max(
            i for i in self._store.action_snapshots if i <= self.cut.index
        )
        state = self._store.action_snapshots[snapshot_index].action(command_id)
        if self.partition is not None:
            related = state.target == self.partition or (
                state.source_kind == "partition" and state.source == self.partition
            )
            control = self.partition in self._store.manifest.cancel_sources
            if not related and not control:
                raise KernelError("READ_UNDECLARED", "unrelated action read")
        return state

    def relations(
        self, relation_id: str, valid_at: Instant, known_at: Cut
    ) -> tuple[()]:
        """Temporal relation store extension (milestone 2)."""
        raise NotImplementedError("M2_RELATIONS: temporal relation queries")

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
