"""Relation/obligation declarations survive bind cloning and engine-free replay."""

import pytest

from aerokernel import (
    ActivateObligation,
    AssertEdge,
    BindingManifest,
    Cardinality,
    CloseEdge,
    Create,
    EndObligation,
    EntityRef,
    Instant,
    Interval,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    ObligationRule,
    Partition,
    RelationDescriptor,
    RelationRule,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.codec import decode_record, encode
from aerokernel.journal import prefixes
from aerokernel.sdk import SimpleEngine


def test_manifest_relation_and_obligation_rules_clone_and_replay_real_versions():
    source = EntityRef("r", "e", "source", 0, "T")
    target = EntityRef("r", "e", "target", 0, "T")
    relation = RelationRule("p", "R", "T", "s*", 7)
    obligation = ObligationRule("p", "R", "targets_per_source", "T", "s*", 9)
    assert decode_record(encode(relation)) == relation
    assert decode_record(encode(obligation)) == obligation
    manifest = BindingManifest(
        "r",
        "e",
        (source, target),
        lifecycle=(LifecycleRule("p", "T"),),
        relation_rules=(relation,),
        obligation_rules=(obligation,),
    )
    assert BindingManifest.from_data(manifest.to_data()) == manifest

    class Owner(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "p",
                    "p",
                    lifecycle=True,
                    relation_produces=("R",),
                    obligation_produces=("R",),
                )
            )
            self.wakeup_ns = 3

        def initialize(self, view):
            valid = Interval(Instant(0), None)
            return (
                Create(source),
                Create(target),
                AssertEdge(
                    "edge",
                    "R",
                    source,
                    target,
                    valid,
                    Stamp("canonical", 0, 1, "canonical"),
                ),
                ActivateObligation("minimum", "R", "targets_per_source", source, valid),
            )

        def integrate(self, view):
            self.wakeup_ns = None
            return CloseEdge("edge"), EndObligation("minimum")

    k = Kernel()
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),),
            relations=(
                RelationDescriptor("R", "T", "T", Cardinality(1, 1), Cardinality(0, 1)),
            ),
        ),
        manifest,
        (Owner(),),
    )
    assert k._store.manifest is not manifest and k._store.manifest == manifest
    initial = k.start().cut
    assert k.view().relations("R", Instant(0))[0].target == target
    assert k.view().obligation_history("minimum")[0].endpoint_ref == source
    k.run_until(3)
    assert k.view().relations("R", Instant(3)) == ()
    assert len(k.view().relation_history("edge")) == 2
    assert len(k.view().obligation_history("minimum")) == 2
    assert k.view(initial).relations("R", Instant(0))[0].source == source
    published = k.view(initial).relation_history("edge")[0].version.record_index
    for prefix in prefixes(k.journal.bytes):
        restored = replay(prefix)
        assert restored._store.manifest == manifest
        cut = restored.view().cut
        assert restored.records == k.records[: cut.index]
        if cut.index < published:
            with pytest.raises(KernelError, match="EDGE_UNKNOWN"):
                restored.view().relation_history("edge")
            with pytest.raises(KernelError, match="OBLIGATION_UNKNOWN"):
                restored.view().obligation_history("minimum")
        else:
            assert restored.view().relation_history("edge") == k.view(
                cut
            ).relation_history("edge")
            assert restored.view().obligation_history("minimum") == k.view(
                cut
            ).obligation_history("minimum")
    assert replay(k.journal.bytes).records == k.records
