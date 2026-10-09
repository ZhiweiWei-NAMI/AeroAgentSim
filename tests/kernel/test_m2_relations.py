"""Temporal graph transactions, writer authority, lifetime cleanup and replay."""

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerokernel import (
    Activate,
    ActivateObligation,
    AssertEdge,
    BindingManifest,
    CancelEdge,
    CancelObligation,
    Cardinality,
    CloseEdge,
    Create,
    EndObligation,
    EntityRef,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    ObligationRule,
    Partition,
    RelationDependency,
    RelationDescriptor,
    RelationRule,
    Remove,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.relation_sweep import validate_graph
from aerokernel.relations import Edge, Obligation
from aerokernel.sdk import ContextEngine, SimpleEngine

REFS = tuple(EntityRef("r", "e", name, 0, "T") for name in ("s1", "s2", "t1", "t2"))
S1, S2, T1, T2 = REFS
ZERO = Instant(0)
STAMP = Stamp("canonical", 0, 1, "canonical")


def edge(eid="a", source=S1, target=T1, start=ZERO, end=None):
    return AssertEdge(eid, "R", source, target, Interval(start, end), STAMP)


def obligation(oid="o", ref=S1, direction="targets_per_source", start=ZERO, end=None):
    return ActivateObligation(oid, "R", direction, ref, Interval(start, end))


def setup(
    initial=(),
    later=(),
    *,
    minimum=0,
    maximum=1,
    reader_lag=0,
    extra=(),
    **manifest_options,
):
    class Owner(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "owner",
                    "owner",
                    lifecycle=True,
                    relation_produces=("R",),
                    obligation_produces=("R",),
                )
            )
            self.wakeup_ns = 3

        def initialize(self, view):
            return (*[Create(ref) for ref in REFS], *initial)

        def integrate(self, view):
            self.wakeup_ns = None
            return (
                (Activate("owner"),)
                if any(isinstance(op, Remove) for op in later)
                else later
            )

        def on_react(self, view, inbox, dirty):
            return (
                later
                if any(isinstance(op, Remove) for op in later) and view.instant.ns == 3
                else ()
            )

    class Reader(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "reader",
                    "reader",
                    relation_consumes=(RelationDependency("R", lag_ns=reader_lag),),
                )
            )
            self.seen = []

        def on_react(self, view, inbox, dirty):
            at = Instant(max(0, view.instant.ns - reader_lag))
            self.seen.append((view.instant, view.relations("R", at), dirty))
            return ()

    owner, reader = Owner(), Reader()
    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        relations=(
            RelationDescriptor(
                "R", "T", "T", Cardinality(minimum, maximum), Cardinality(0, maximum)
            ),
        ),
    )
    manifest = BindingManifest(
        "r",
        "e",
        REFS,
        lifecycle=(LifecycleRule("owner", "T"),),
        relation_rules=(RelationRule("owner", "R", "T"),),
        obligation_rules=tuple(
            ObligationRule("owner", "R", d, "T")
            for d in ("targets_per_source", "sources_per_target")
        ),
        **manifest_options,
    )
    k = Kernel(provenance="full")
    k.bind(registry, manifest, (owner, reader, *extra))
    return k, owner, reader


def test_replace_reads_two_knowledge_prefixes_and_sdk_sources():
    k, _, reader = setup(
        (edge(),), (CloseEdge("a"), edge("b", target=T2, start=Instant(3)))
    )
    k.start()
    before = k.view().cut
    k.run_until(3)
    assert [e.edge_id for e in k.view().relations("R", Instant(3, 0))] == ["b"]
    assert [e.edge_id for e in k.view().relations("R", Instant(3, 0), before)] == ["a"]
    versions = k.view().relation_history("a")
    assert len(versions) == 2 and versions[-1].valid.end == Instant(3)
    assert [e.target for e in reader.seen[-1][1]] == [T2]
    assert replay(k.journal.bytes).view().relation_history("a") == versions


def test_future_overlap_and_minimum_gap_reject_whole_wave():
    for initial, minimum in (
        ((edge("a", end=Instant(10)), edge("b", target=T2, start=Instant(5))), 0),
        ((edge(end=Instant(5)), obligation()), 1),
        ((obligation(),), 1),
        ((edge(), edge("b")), 0),
        ((edge(), obligation(), obligation("other")), 0),
    ):
        k, _, _ = setup(initial, minimum=minimum)
        with pytest.raises(KernelError):
            k.start()
        assert k.view().relations("R", Instant(0)) == ()
        assert replay(k.journal.bytes).records == k.records


def test_obligation_end_and_incident_edge_close_allow_removal_same_transaction():
    k, _, _ = setup(
        (edge(), obligation()),
        (CloseEdge("a"), EndObligation("o"), Remove(S1)),
        minimum=1,
    )
    k.start()
    k.run_until(3)
    assert k.view().relations("R", k.view().lifecycle(S1).removed.instant) == ()
    assert (
        k.view().obligation_history("o")[-1].valid.end
        == k.view().lifecycle(S1).removed.instant
    )
    assert k.view().lifecycle(S1).removed.instant.ns == 3
    assert replay(k.journal.bytes).records == k.records
    k, _, _ = setup((edge(),), (Remove(S1),))
    k.start()
    with pytest.raises(KernelError, match="RELATION_LIFETIME"):
        k.run_until(3)
    assert k.view().lifecycle(S1).removed is None


def test_future_cancel_and_interval_boundary_notifications():
    k, _, reader = setup(
        (
            edge(start=Instant(5), end=Instant(9)),
            obligation(start=Instant(5), end=Instant(9)),
        ),
        (CancelEdge("a"), CancelObligation("o")),
    )
    k.start()
    k.run_until(3)
    assert k.view().relation_history("a")[-1].valid is None
    assert k.view().obligation_history("o")[-1].valid is None
    assert all(t["state"] != "pending" for t in k._store.timers.values())
    k.run_until(10)
    assert replay(k.journal.bytes).records == k.records
    k, _, reader = setup((edge(start=Instant(5), end=Instant(9)),))
    k.start()
    k.run_until(10)
    assert any(at.ns == 5 and len(rows) == 1 for at, rows, _ in reader.seen)
    assert any(at.ns == 9 and len(rows) == 0 for at, rows, _ in reader.seen)
    assert replay(k.journal.bytes).records == k.records


@pytest.mark.parametrize(
    "initial,later,code",
    [
        ((edge(),), (CancelEdge("a"),), "EDGE_CANCEL"),
        ((edge(start=Instant(5)),), (CloseEdge("a"),), "EDGE_CLOSE"),
        ((edge(end=Instant(2)),), (CloseEdge("a"),), "EDGE_ENDED"),
        ((edge(),), (edge(),), "EDGE_REUSE"),
        ((), (CloseEdge("missing"),), "EDGE_UNKNOWN"),
        ((edge(),), (CloseEdge("a"), CloseEdge("a")), "EDGE_DUPLICATE"),
        ((obligation(),), (CancelObligation("o"),), "OBLIGATION_CANCEL"),
        ((obligation(start=Instant(5)),), (EndObligation("o"),), "OBLIGATION_END"),
        ((obligation(end=Instant(2)),), (EndObligation("o"),), "OBLIGATION_ENDED"),
        ((obligation(),), (obligation(),), "OBLIGATION_REUSE"),
    ],
)
def test_invalid_relation_versions_do_not_publish(initial, later, code):
    k, _, _ = setup(initial, later)
    k.start()
    before = (
        k.view().relation_history("a")
        if initial and isinstance(initial[0], AssertEdge)
        else ()
    )
    with pytest.raises(KernelError, match=code):
        k.run_until(3)
    if before:
        assert k.view().relation_history("a") == before
    assert replay(k.journal.bytes).records == k.records


def test_foreign_writer_and_undeclared_reads_fail_and_sdk_helpers_work():
    class Intruder(SimpleEngine):
        def integrate(self, view):
            return (edge("bad", start=Instant(3)),)

    intruder = Intruder(Partition("intruder", "intruder", relation_produces=("R",)))
    intruder.wakeup_ns = 3
    k, _, _ = setup(extra=(intruder,))
    k.start()
    with pytest.raises(KernelError, match="EDGE_AUTHORITY"):
        k.run_until(3)

    class SDKOwner(ContextEngine):
        def bootstrap(self, ctx):
            for ref in REFS:
                ctx.create(ref)
            ctx.relate("a", "R", S1, T1, valid=Interval(ctx.now, None), acquired=STAMP)

        def step(self, ctx):
            assert ctx.relations("R")[0].target == T1
            ctx.replace_relation(
                "a", "b", "R", S1, T2, valid=Interval(ctx.now, None), acquired=STAMP
            )
            self.wakeup_ns = None

    k, old, _ = setup()
    sdk = SDKOwner(old.partition)
    sdk.wakeup_ns = 3
    k2 = Kernel(provenance="full")
    k2.bind(
        k._store.registry,
        k._store.manifest,
        (sdk, SimpleEngine(Partition("reader", "reader"))),
    )
    k2.start()
    k2.run_until(3)
    assert k2.view().relations("R", Instant(3))[0].target == T2
    assert replay(k2.journal.bytes).records == k2.records


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 1),
            st.integers(2, 3),
            st.integers(0, 5),
            st.one_of(st.none(), st.integers(1, 6)),
        ),
        max_size=8,
    ),
    st.integers(0, 2),
    st.integers(0, 2),
    st.booleans(),
    st.booleans(),
    st.booleans(),
)
def test_directional_sweep_against_independent_brute_oracle(
    rows, source_max, target_max, activate, pair_identity, reverse
):
    edges = []
    for i, (s, t, start, end) in enumerate(rows):
        valid = (
            None
            if end is not None and end <= start
            else Interval(Instant(start), None if end is None else Instant(end))
        )
        edges.append(
            Edge(
                str(i),
                "R",
                REFS[s],
                REFS[t],
                valid,
                STAMP,
                Instant(0),
                "owner",
                ItemRef(1, i),
            )
        )
    desc = RelationDescriptor(
        "R",
        "T",
        "T",
        Cardinality(min(1, source_max), source_max),
        Cardinality(min(1, target_max), target_max),
        "endpoint_pair" if pair_identity else "edge_id",
    )
    obligations = (
        (
            Obligation(
                "o",
                "R",
                "targets_per_source",
                S1,
                Interval(Instant(0), Instant(6)),
                Instant(0),
                "owner",
                ItemRef(1, 20),
            ),
        )
        if activate
        else ()
    )
    if reverse:
        obligations = (
            *obligations,
            Obligation(
                "reverse",
                "R",
                "sources_per_target",
                T1,
                Interval(ZERO, Instant(6)),
                ZERO,
                "owner",
                ItemRef(1, 21),
            ),
        )
    # Independent oracle: evaluate every discrete boundary, counting actual rows.
    valid = True
    if pair_identity and len({(e.source, e.target) for e in edges}) != len(edges):
        valid = False
    for at in range(7):
        active = [
            e
            for e in edges
            if e.valid is not None
            and e.valid.start.ns <= at
            and (e.valid.end is None or at < e.valid.end.ns)
        ]
        pairs = [(e.source, e.target) for e in active]
        if len(pairs) != len(set(pairs)):
            valid = False
        for ref in REFS:
            outgoing = len({t for s, t in pairs if s == ref})
            incoming = len({s for s, t in pairs if t == ref})
            if outgoing > source_max or incoming > target_max:
                valid = False
            if activate and at < 6 and ref == S1 and outgoing < min(1, source_max):
                valid = False
            if reverse and at < 6 and ref == T1 and incoming < min(1, target_max):
                valid = False
    if valid:
        validate_graph(desc, edges, obligations)
    else:
        with pytest.raises(KernelError):
            validate_graph(desc, edges, obligations)


def test_positive_lag_relation_and_obligation_history_use_the_actual_prefix():
    from aerokernel.state import StateView

    k, _, reader = setup(
        (edge(), obligation()), (CloseEdge("a"), EndObligation("o")), reader_lag=2
    )
    k.start()
    assert reader.seen == []  # Notification itself is delayed, not manufactured.
    k.run_until(3)
    view = StateView(k._store, partition="reader", instant=Instant(3, 3))
    assert len(view.relation_history("a")) == len(view.obligation_history("o")) == 1
    assert view.relations("R", Instant(1))[0].valid.end is None
    with pytest.raises(KernelError, match="READ_VALIDITY_FUTURE"):
        view.relations("R", Instant(3))
    undeclared = StateView(k._store, partition="reader", instant=Instant(3))
    from dataclasses import replace

    unbound = k._store.clone()
    unbound.partitions = {
        **unbound.partitions,
        "reader": replace(unbound.partitions["reader"], relation_consumes=()),
    }
    undeclared = StateView(unbound, partition="reader")
    for read in (
        lambda: undeclared.relations("R", Instant(0)),
        lambda: undeclared.relation_history("a"),
        lambda: undeclared.obligation_history("o"),
    ):
        with pytest.raises(KernelError, match="READ_UNDECLARED"):
            read()
    assert replay(k.journal.bytes).records == k.records


@pytest.mark.parametrize("declared", [False, True])
def test_cross_owner_target_exchange_requires_an_explicit_merged_cohort(declared):
    class Second(SimpleEngine):
        def __init__(self):
            super().__init__(Partition("second", "second", relation_produces=("R",)))
            self.wakeup_ns = 3

        def integrate(self, view):
            self.wakeup_ns = None
            return (edge("b", source=S2, start=Instant(3)),)

    second = Second()
    k, owner, reader = setup(
        (edge(),),
        (CloseEdge("a"),),
        extra=(second,),
        cohorts=(("owner", "second"),) if declared else (),
    )
    manifest = replace(
        k._store.manifest,
        relation_rules=(
            RelationRule("owner", "R", "T"),
            RelationRule("second", "R", "T", "s2", 1),
        ),
    )
    live = Kernel(provenance="full")
    live.bind(k._store.registry, manifest, (owner, reader, second))
    live.start()
    if declared:
        live.run_until(3)
        assert live.view().relations("R", Instant(3))[0].source == S2
    else:
        with pytest.raises(KernelError, match="RELATION_COHORT"):
            live.run_until(3)
        assert live.view().relation_history("a")[-1].valid.end is None
        with pytest.raises(KernelError, match="EDGE_UNKNOWN"):
            live.view().relation_history("b")
    assert replay(live.journal.bytes).records == live.records


def test_sweep_respects_microstep_half_open_touches_and_open_tail_minimum():
    a = Edge(
        "a",
        "R",
        S1,
        T1,
        Interval(Instant(1, 1), Instant(1, 2)),
        STAMP,
        ZERO,
        "p",
        ItemRef(1, 0),
    )
    b = replace(a, edge_id="b", target=T2, valid=Interval(Instant(1, 2), None))
    descriptor = RelationDescriptor(
        "R", "T", "T", Cardinality(1, 1), Cardinality(0, None)
    )
    scope = Obligation(
        "o",
        "R",
        "targets_per_source",
        S1,
        Interval(Instant(1, 1), None),
        ZERO,
        "p",
        ItemRef(1, 2),
    )
    validate_graph(descriptor, (a, b), (scope,))
    with pytest.raises(KernelError, match="RELATION_MINIMUM"):
        validate_graph(descriptor, (a,), (scope,))
    with pytest.raises(KernelError, match="RELATION_DECLARATION"):
        validate_graph(descriptor, (replace(a, relation_id="different"),), ())
    with pytest.raises(KernelError, match="RELATION_DECLARATION"):
        validate_graph(descriptor, (), (replace(scope, direction="unknown"),))
