"""Settled whole cones, coalesced frames, sample levels and replay."""

from dataclasses import replace

import pytest

from aerokernel import (
    Activate,
    BindingManifest,
    BindingRule,
    Create,
    Dependency,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Interval,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    Partition,
    SampleFrame,
    SampleSpec,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.sdk import SimpleEngine

REF = EntityRef("r", "e", "subject", 0, "T")


def write(view, field, value):
    return FactWrite(
        (REF, field),
        value,
        Stamp("canonical", view.instant.ns, 1, "canonical"),
        Interval(view.instant, None),
    )


class Controller(SimpleEngine):
    def initialize(self, view):
        return (Create(REF),)


class Source(SimpleEngine):
    def __init__(self):
        super().__init__(Partition("source", "source", produces=("x",)))
        self.wakeup_ns = 1
        self.followed = False

    def initialize(self, view):
        return (write(view, "x", 0),)

    def integrate(self, view):
        self.wakeup_ns = None
        return (write(view, "x", 1), Activate("source"))

    def on_react(self, view, inbox, dirty):
        if view.instant.ns == 1 and not self.followed:
            self.followed = True
            return (write(view, "x", 2),)
        return ()


class Middle(SimpleEngine):
    def on_react(self, view, inbox, dirty):
        value = view.field((REF, "x"), view.instant).value
        return (write(view, "y", value * 2),)


class Evaluator(SimpleEngine):
    def __init__(self, partition, spec, *, bad=()):
        super().__init__(partition)
        self.spec = spec
        self.seen = []
        self.bad = bad

    def on_react(self, view, inbox, dirty):
        prior = view.sample_frames(self.spec.context_id)
        value = view.field((REF, "y"), view.instant).value
        self.seen.append((view.instant, value, len(prior), len(dirty)))
        frame = SampleFrame(
            self.spec.context_id,
            view.instant.ns,
            view.cut,
            self.spec.bindings,
            self.spec.clocks,
            {"value": value},
            self.spec.sources,
        )
        return (frame, *self.bad)


def setup(*, child=False, bad=(), provenance="full"):
    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        tuple(FieldDescriptor(f, "T", {"type": "integer"}) for f in ("x", "y")),
    )
    spec = SampleSpec(
        "parent",
        "eval",
        ("controller", "source", "middle"),
        {"subject": REF},
        {"subject": "middle"},
        {"subject": ("canonical", "canonical")},
    )
    evaluator = Evaluator(
        Partition("eval", "eval", consumes=(Dependency("y", value_only=True),)),
        spec,
        bad=bad,
    )
    engines = [
        Controller(Partition("controller", "controller", lifecycle=True)),
        Source(),
        Middle(
            Partition(
                "middle",
                "middle",
                produces=("y",),
                consumes=(Dependency("x", value_only=True),),
            )
        ),
        evaluator,
    ]
    specs = [spec]
    child_engine = None
    if child:
        child_spec = SampleSpec(
            "child",
            "child",
            ("controller", "source", "middle", "eval"),
            frame_inputs=("parent",),
        )

        class Child(SimpleEngine):
            seen = []

            def on_react(self, view, inbox, dirty):
                rows = view.sample_frames("parent")
                assert rows[-1].frame.physical_ns == view.instant.ns
                self.seen.append(view.instant)
                return (
                    SampleFrame(
                        "child",
                        view.instant.ns,
                        view.cut,
                        {},
                        {},
                        {"parent_value": rows[-1].frame.result["value"]},
                        {},
                    ),
                )

        child_engine = Child(Partition("child", "child"))
        engines.append(child_engine)
        specs.append(child_spec)
    manifest = BindingManifest(
        "r",
        "e",
        (REF,),
        rules=(BindingRule("source", "T", ("x",)), BindingRule("middle", "T", ("y",))),
        lifecycle=(LifecycleRule("controller", "T"),),
        samples=tuple(specs),
    )
    k = Kernel(provenance=provenance)
    k.bind(registry, manifest, tuple(engines))
    return k, evaluator, child_engine


def test_cone_settles_multiple_waves_into_one_frame_per_physical_time():
    k, evaluator, _ = setup()
    k.start()
    assert evaluator.seen[0][1:3] == (0, 0)
    k.run_until(1)
    assert len(evaluator.seen) == 2
    assert evaluator.seen[1][1:3] == (4, 1)
    assert evaluator.seen[1][3] == 2  # Coalesced y=2 and y=4 notifications.
    frames = k.view().sample_frames("parent")
    assert [row.frame.physical_ns for row in frames] == [0, 1]
    assert frames[-1].frame.result == {"value": 4}
    cut = k.view().cut
    k.run_until(1)
    assert k.view().cut == cut
    k.run_until(2)
    assert len(k.view().sample_frames("parent")) == 2
    assert replay(k.journal.bytes).records == k.records
    assert len(k.view(frames[0].frame.cut).sample_frames("parent")) == 0


def test_sample_levels_observe_current_parent_then_publish_at_later_microsteps():
    k, evaluator, child = setup(child=True)
    k.start()
    k.run_until(1)
    parent_rows = k.view().sample_frames("parent")
    child_rows = k.view().sample_frames("child")
    assert len(parent_rows) == len(child_rows) == 2
    for parent, downstream in zip(parent_rows, child_rows, strict=True):
        assert parent.available.ns == downstream.available.ns
        assert parent.available.microstep < downstream.available.microstep
        assert downstream.frame.result["parent_value"] == parent.frame.result["value"]
    assert len(child.seen) == len(evaluator.seen) == 2
    restored = replay(k.journal.bytes)
    assert restored.view().sample_frames("parent") == parent_rows
    assert restored.view().sample_frames("child") == child_rows


def test_later_same_time_activation_into_a_sampled_cone_is_an_atomic_fault():
    k, _, _ = setup(bad=(Activate("eval"),))
    with pytest.raises(KernelError, match="SAMPLE_CAUSALITY"):
        k.start()
    assert k.view().sample_frames("parent") == ()
    assert k.records[-1]["type"] == "fault"
    assert replay(k.journal.bytes).records == k.records


def test_transitive_zero_lag_sample_feedback_rejects_and_positive_lag_breaks_it():
    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        tuple(FieldDescriptor(f, "T", {"type": "integer"}) for f in ("x", "y", "z")),
    )
    spec = SampleSpec("context", "sample", ("owner", "relay"))
    manifest = BindingManifest(
        "r",
        "e",
        rules=(
            BindingRule("owner", "T", ("x",)),
            BindingRule("relay", "T", ("y",)),
            BindingRule("sample", "T", ("z",)),
        ),
        samples=(spec,),
    )
    owner = Partition("owner", "owner", produces=("x",), consumes=(Dependency("y"),))
    relay = Partition("relay", "relay", produces=("y",), consumes=(Dependency("z"),))
    sample = Partition("sample", "sample", produces=("z",), consumes=(Dependency("x"),))
    with pytest.raises(KernelError, match="SAMPLE_CYCLE"):
        Kernel(provenance="full").bind(
            registry, manifest, tuple(SimpleEngine(p) for p in (owner, relay, sample))
        )
    Kernel(provenance="full").bind(
        registry,
        manifest,
        tuple(
            SimpleEngine(p)
            for p in (
                replace(owner, consumes=(Dependency("y", lag_ns=1),)),
                relay,
                sample,
            )
        ),
    )
