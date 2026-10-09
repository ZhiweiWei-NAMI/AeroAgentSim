"""Predicate-agnostic settled frames and declared sample scheduling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from .errors import KernelError
from .ids import EntityRef, ItemRef, validate_text
from .operations import SampleFrame
from .storage import AppendList
from .time import ClockMapping, Instant
from .values import FrozenValue, freeze

if TYPE_CHECKING:
    from .state import Store
    from .transactions import Candidate


@dataclass(frozen=True)
class SampleSpec:
    """Pinned context, whole zero-lag cone and input/timer trigger kinds."""

    context_id: str
    partition: str
    upstream: tuple[str, ...] = ()
    bindings: Mapping[str, EntityRef] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    clocks: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    triggers: tuple[str, ...] = (
        "field",
        "timer",
        "relation",
        "lifecycle",
        "activation",
        "sample",
    )
    frame_inputs: tuple[str, ...] = ()
    parameters: object = None

    def __post_init__(self) -> None:
        validate_text(self.context_id)
        validate_text(self.partition)
        for name in ("upstream", "triggers", "frame_inputs"):
            values = tuple(getattr(self, name))
            if len(values) != len(set(values)):
                raise KernelError("SAMPLE_DECLARATION", "duplicate sample declaration")
            for value in values:
                validate_text(value)
            object.__setattr__(self, name, tuple(sorted(values)))
        if set(self.triggers) - {
            "field",
            "timer",
            "relation",
            "lifecycle",
            "activation",
            "sample",
            "message",
        }:
            raise KernelError("SAMPLE_TRIGGER", "unsupported trigger kind")
        if set(self.bindings) != set(self.sources) or set(self.bindings) != set(
            self.clocks
        ):
            raise KernelError(
                "SAMPLE_BINDINGS", "roles require identity, source and native clock"
            )
        for role, ref in self.bindings.items():
            validate_text(role)
            if not isinstance(ref, EntityRef):
                raise KernelError("SAMPLE_BINDINGS", "typed role identity required")
            validate_text(self.sources[role])
            clock = self.clocks[role]
            if not isinstance(clock, (tuple, list)) or len(clock) != 2:
                raise KernelError("SAMPLE_CLOCK", "native clock and mapping required")
            for name in clock:
                validate_text(name)
        object.__setattr__(
            self,
            "clocks",
            MappingProxyType({k: tuple(v) for k, v in self.clocks.items()}),
        )
        for name in ("bindings", "sources"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        object.__setattr__(self, "parameters", freeze(self.parameters))


@dataclass(frozen=True)
class RecordedFrame:
    """Kernel-stamped frame whose result is immutable portable data."""

    frame: SampleFrame
    available: Instant
    producer: str
    version: ItemRef


def compile_samples(
    store: Store,
    graph: Mapping[str, tuple[str, ...]],
    mappings: Mapping[str, ClockMapping],
) -> None:
    """Check complete cones, context uniqueness and zero-lag feedback paths."""
    specs = store.manifest.samples
    if len({s.context_id for s in specs}) != len(specs) or len(
        {s.partition for s in specs}
    ) != len(specs):
        raise KernelError(
            "SAMPLE_DECLARATION", "unique context and evaluator partition required"
        )
    by_context = {s.context_id: s for s in specs}
    reverse: dict[str, set[str]] = {p: set() for p in store.partitions}
    for source, targets in graph.items():
        for target in targets:
            reverse[target].add(source)
    levels: dict[str, int] = {}
    for spec in specs:
        if (
            spec.partition not in store.partitions
            or not store.partitions[spec.partition].reactive
        ):
            raise KernelError(
                "SAMPLE_DECLARATION", "sample evaluator requires reactive support"
            )
        if store.partitions[spec.partition].lifecycle or any(
            spec.partition in c for c in store.manifest.cohorts
        ):
            raise KernelError(
                "SAMPLE_DECLARATION",
                "sample evaluator cannot be a lifecycle controller/cohort",
            )
        ancestors: set[str] = set()
        pending = list(reverse[spec.partition])
        while pending:
            node = pending.pop()
            if node == spec.partition:
                raise KernelError(
                    "SAMPLE_CYCLE",
                    "zero-lag sampled feedback path",
                    context=spec.context_id,
                )
            if node not in ancestors:
                ancestors.add(node)
                pending.extend(reverse[node])
        if not ancestors.issubset(spec.upstream) or set(spec.upstream) - set(
            store.partitions
        ):
            raise KernelError(
                "SAMPLE_CONE",
                "declare the complete transitive upstream cone",
                context=spec.context_id,
                required=sorted(ancestors),
            )
        if set(spec.frame_inputs) - set(by_context):
            raise KernelError("SAMPLE_INPUT", "unknown frame context")
        for role, ref in spec.bindings.items():
            clock_id, mapping_id = spec.clocks[role]
            if mapping_id not in mappings or mappings[mapping_id].clock_id != clock_id:
                raise KernelError("SAMPLE_CLOCK", "unknown pinned native clock/mapping")
            store.manifest._namespace(ref)
            store.registry.is_a(ref.type_id, ref.type_id)
            if spec.sources[role] not in store.partitions:
                raise KernelError("SAMPLE_SOURCE", "unknown declared instance source")
        # Ranking by the number of sampled ancestors gives a stable topological
        # order even for skipped/untriggered contexts; disconnected peers tie.
        levels[spec.context_id] = len({s.partition for s in specs} & ancestors)
    store.sample_specs = MappingProxyType(by_context)
    store.sample_partitions = MappingProxyType(
        {s.partition: s.context_id for s in specs}
    )
    store.sample_levels = MappingProxyType(levels)


def ordinary_earliest(store: Store) -> Instant | None:
    """Earliest ordinary recipient heap, excluding deferred sample inputs."""
    from .ingress import safe_partitions

    return min(
        (
            heap[0][0]
            for p, heap in store.work.heaps.items()
            if heap
            and p not in store.sample_partitions
            and (
                not store.ingress_dependencies
                or p in safe_partitions(store, heap[0][0].ns)
            )
        ),
        default=None,
    )


def sample_ready(store: Store, ns: int) -> tuple[str, ...]:
    """Coalesce due contexts at the next topological level, once per time."""
    for work in store.work:
        if work.eligible.ns > ns or work.recipient not in store.sample_partitions:
            continue
        spec = store.sample_specs[store.sample_partitions[work.recipient]]
        kind = "message" if work.dirty is None else work.dirty.kind
        if kind == "validity":
            kind = "field"
        if kind not in spec.triggers:
            raise KernelError(
                "SAMPLE_TRIGGER",
                "input is not a declared sample trigger",
                context=spec.context_id,
                trigger=kind,
            )
    from .ingress import safe_partitions

    safe = (
        set(safe_partitions(store, ns))
        if store.ingress_dependencies
        else set(store.partitions)
    )
    due = {
        w.recipient
        for w in store.work
        if w.eligible.ns <= ns
        and w.recipient in store.sample_partitions
        and w.recipient in safe
        and (not store.ingress_dependencies or store.frontiers[w.recipient][0].ns == ns)
    }
    if not due:
        return ()
    for p in due:
        context = store.sample_partitions[p]
        rows = store.frames.get(context, ())
        if rows and rows[-1].frame.physical_ns == ns:
            raise KernelError(
                "SAMPLE_CAUSALITY",
                "input arrived after sampling at this physical time",
                context=context,
            )
    level = min(store.sample_levels[store.sample_partitions[p]] for p in due)
    return tuple(
        sorted(
            (
                p
                for p in due
                if store.sample_levels[store.sample_partitions[p]] == level
            ),
            key=lambda p: (store.partitions[p].engine_id, p),
        )
    )


def publish_frame(
    candidate: Candidate, partition: str, op: SampleFrame, out: ItemRef, phase: str
) -> None:
    """Validate the invocation contract and record one atomic evaluator result."""
    from .codec import decode_record, encode
    from .messages import Dirty
    from .state import Work
    from .transactions import eligibility

    state = candidate.state
    spec = state.sample_specs.get(op.context_id)
    intent = state.intents[state.pending_intents[partition]]
    if spec is None or spec.partition != partition or phase != "sample":
        raise KernelError(
            "SAMPLE_AUTHORITY", "frame requires its declared sampled invocation"
        )
    if (
        type(op.physical_ns) is not int
        or op.physical_ns != candidate.instant.ns
        or op.cut != decode_record(intent["read_cut"])
    ):
        raise KernelError(
            "SAMPLE_CUT", "frame must echo its physical time and permitted cut"
        )
    if (
        dict(op.bindings) != dict(spec.bindings)
        or dict(op.sources) != dict(spec.sources)
        or dict(op.clocks) != dict(spec.clocks)
    ):
        raise KernelError(
            "SAMPLE_PIN", "frame changed pinned identities/sources/clocks"
        )
    prior = state.frames.get(op.context_id, ())
    if prior and prior[-1].frame.physical_ns == op.physical_ns:
        raise KernelError("SAMPLE_DUPLICATE", "one frame per context and physical time")
    for ref in op.bindings.values():
        state.known(ref, op.cut)
    frozen: FrozenValue = freeze(op.result, candidate.budget)
    frame = replace(op, result=frozen)
    row = RecordedFrame(frame, candidate.instant, partition, out)
    history = prior.fork() if isinstance(prior, AppendList) else AppendList(list(prior))
    history.append(row)
    state.frames[op.context_id] = history
    old_indices = state.frame_indices.get(op.context_id)
    indices = old_indices.fork() if old_indices is not None else AppendList[int]()
    indices.append(out.record_index)
    state.frame_indices[op.context_id] = indices
    candidate.items[out.item_index]["sample_frame"] = encode(row)
    for consumer in state.sample_specs.values():
        if op.context_id not in consumer.frame_inputs:
            continue
        at = eligibility(
            state, consumer.partition, candidate.instant, candidate.instant
        )
        dirty = Dirty(
            None,
            "sample",
            at,
            out,
            True,
            {"context_id": op.context_id},
            candidate.budget,
        )
        state.work.append(Work(consumer.partition, at, out, dirty=dirty))
        candidate.add(
            {
                "kind": "dirty",
                "recipient": consumer.partition,
                "notification": encode(dirty),
            }
        )


def validate_sample_work(store: Store, ns: int) -> None:
    """Later same-time input into an already sampled cone is a fault."""
    if not store.sample_specs:
        return
    for spec in store.sample_specs.values():
        rows = store.frames.get(spec.context_id, ())
        if not rows or rows[-1].frame.physical_ns != ns:
            continue
        cone = {*spec.upstream, spec.partition}
        if any(w.eligible.ns == ns and w.recipient in cone for w in store.work):
            raise KernelError(
                "SAMPLE_CAUSALITY",
                "same-time input followed settled sample",
                context=spec.context_id,
            )
