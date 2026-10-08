"""I2/I5: cross-type generation reuse, native cleanup and historical authority."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from aerokernel import (
    ABSENT,
    Activate,
    BindingManifest,
    BindingRule,
    Create,
    Dependency,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    LifecycleReady,
    LifecycleRule,
    MemoryRegistry,
    Partition,
    Remove,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.testing import SimpleEngine


def test_remove_then_cross_type_reuse_resolves_new_field_owner():
    old = EntityRef("r", "e", "same", 0, "Old")
    new = EntityRef("r", "e", "same", 1, "New")

    class Controller(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition("controller", "controller", produces=("old",), lifecycle=True)
            )
            self.wakeup_ns = 1
            self.stage = 0

        def initialize(self, view):
            return (
                Create(old),
                FactWrite(
                    (old, "old"),
                    1,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), None),
                ),
            )

        def integrate(self, view):
            self.wakeup_ns = 2 if view.instant.ns == 1 else None
            return (Activate("controller"),)

        def on_react(self, view, inbox, dirty):
            if view.instant.ns == 1 and self.stage == 0:
                self.stage = 1
                return (Remove(old),)
            if view.instant.ns == 2 and self.stage == 1:
                self.stage = 2
                return (Create(new),)
            return ()

    class Writer(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "new_writer",
                    "new_writer",
                    produces=("new",),
                    lifecycle_reads=("New",),
                )
            )

        def on_react(self, view, inbox, dirty):
            return (
                FactWrite(
                    (new, "new"),
                    2,
                    Stamp("canonical", 2, 1, "canonical"),
                    Interval(view.instant, None),
                ),
            )

    registry = MemoryRegistry(
        (TypeDescriptor("Old"), TypeDescriptor("New")),
        (
            FieldDescriptor("old", "Old", {"type": "integer"}),
            FieldDescriptor("new", "New", {"type": "integer"}),
        ),
    )
    manifest = BindingManifest(
        "r",
        "e",
        (old,),
        rules=(
            BindingRule("controller", "Old", ("old",)),
            BindingRule("new_writer", "New", ("new",)),
        ),
        lifecycle=(
            LifecycleRule("controller", "Old"),
            LifecycleRule("controller", "New"),
        ),
    )
    k = Kernel()
    k.bind(registry, manifest, (Controller(), Writer()))
    original = k.start().cut
    k.run_until(2)
    assert k._store.generations["same"] == 1
    assert k.view().field((new, "new"), Instant(2, 100)).value == 2
    assert k.view().field((old, "old"), Instant(2, 100)) is ABSENT
    assert k.view(original).field((old, "old"), Instant(2, 100)).value == 1
    assert k._store.writers[(new, "new")] == "new_writer"
    assert replay(k.journal.bytes)._store.lives == k._store.lives


def test_cleanup_ack_own_earlier_local_cause_then_removal():
    from aerokernel import LocalCause

    ref = EntityRef("r", "e", "same", 0, "T")

    class Controller(SimpleEngine):
        done = False

        def initialize(self, view):
            return (Create(ref),)

        def on_react(self, view, inbox, dirty):
            if not self.done:
                self.done = True
                return (LifecycleReady(ref), Remove(ref, (LocalCause(0),)))
            return ()

    registry = MemoryRegistry(
        (TypeDescriptor("Root", abstract=True), TypeDescriptor("T", ("Root",)))
    )
    manifest = BindingManifest(
        "r",
        "e",
        (ref,),
        lifecycle=(LifecycleRule("controller", "T"),),
        lifecycle_participants={"Root": ("controller",)},
    )
    k = Kernel()
    k.bind(
        registry,
        manifest,
        (Controller(Partition("controller", "controller", lifecycle=True)),),
    )
    k.start()
    assert k.view().lifecycle(ref).removed is not None
    replay(k.journal.bytes)


def test_required_native_input_coverage_fails_before_external_integration():
    import pytest

    from aerokernel import KernelError, Timing

    ref = EntityRef("r", "e", "x", 0, "T")

    class Source(SimpleEngine):
        def initialize(self, view):
            return (
                Create(ref),
                FactWrite(
                    (ref, "x"),
                    1,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), Instant(4)),
                ),
            )

    class Sink(SimpleEngine):
        called = False

        def integrate(self, view):
            self.called = True
            return ()

    source = Source(Partition("source", "source", produces=("x",), lifecycle=True))
    sink = Sink(
        Partition(
            "sink",
            "sink",
            consumes=(Dependency("x", require_coverage=True),),
            timing=Timing("fixed_step", 5),
        )
    )
    registry = MemoryRegistry(
        (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
    )
    manifest = BindingManifest(
        "r",
        "e",
        (ref,),
        rules=(BindingRule("source", "T", ("x",)),),
        lifecycle=(LifecycleRule("source", "T"),),
    )
    k = Kernel()
    k.bind(registry, manifest, (source, sink))
    k.start()
    with pytest.raises(KernelError, match="INPUT_COVERAGE"):
        k.run_until(5)
    assert not sink.called
    replay(k.journal.bytes)


@settings(max_examples=12, deadline=None)
@given(st.lists(st.sampled_from(["A", "B"]), min_size=1, max_size=4))
def test_generation_sequence_and_old_knowledge_are_independent_of_reused_type(types):
    refs = [
        EntityRef("r", "e", "id", generation, type_id)
        for generation, type_id in enumerate(types)
    ]

    class E(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition("owner", "engine", produces=("value",), lifecycle=True)
            )
            self.current = 0
            self.physical = -1
            self.pending = False
            self.wakeup_ns = 1 if len(refs) > 1 else None

        def write(self, view):
            return FactWrite(
                (refs[self.current], "value"),
                self.current,
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
            )

        def initialize(self, view):
            return (Create(refs[0]), self.write(view))

        def integrate(self, view):
            self.wakeup_ns = (
                view.instant.ns + 1 if view.instant.ns < 2 * (len(refs) - 1) else None
            )
            return (Activate("owner"),)

        def on_react(self, view, inbox, dirty):
            ns = view.instant.ns
            if ns == 0:
                return ()
            if self.pending:
                self.pending = False
                return (self.write(view),)
            if ns == self.physical:
                return ()
            self.physical = ns
            if ns % 2:
                return (Remove(refs[self.current]),)
            self.current += 1
            self.pending = True
            return (Create(refs[self.current]),)

    registry = MemoryRegistry(
        (
            TypeDescriptor("Root", abstract=True),
            TypeDescriptor("A", ("Root",)),
            TypeDescriptor("B", ("Root",)),
        ),
        (FieldDescriptor("value", "Root", {"type": "integer"}),),
    )
    manifest = BindingManifest(
        "r",
        "e",
        (refs[0],),
        rules=(BindingRule("owner", "Root", ("value",)),),
        lifecycle=(LifecycleRule("owner", "Root"),),
    )
    k = Kernel()
    k.bind(registry, manifest, (E(),))
    views = [k.start()]
    for boundary in range(1, 2 * (len(refs) - 1) + 1):
        view = k.run_until(boundary)
        if boundary % 2 == 0:
            views.append(view)
    assert k._store.generations["id"] == len(refs) - 1
    for generation, view in enumerate(views):
        assert view.field((refs[generation], "value"), Instant(100)).value == generation
    assert replay(k.journal.bytes)._store.lives == k._store.lives


def test_unauthorized_remove_and_stale_write_fault_without_mutating_prefix():
    import pytest

    from aerokernel import KernelError

    ref = EntityRef("r", "e", "id", 0, "T")
    registry = MemoryRegistry(
        (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
    )
    manifest = BindingManifest(
        "r",
        "e",
        (ref,),
        rules=(BindingRule("owner", "T", ("x",)),),
        lifecycle=(LifecycleRule("owner", "T"),),
    )

    class Owner(SimpleEngine):
        def initialize(self, view):
            return (
                Create(ref),
                FactWrite(
                    (ref, "x"),
                    1,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), None),
                ),
            )

    class Intruder(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            return (Remove(ref),)

    k = Kernel()
    k.bind(
        registry,
        manifest,
        (
            Owner(Partition("owner", "owner", produces=("x",), lifecycle=True)),
            Intruder(Partition("intruder", "intruder", lifecycle_reads=("T",))),
        ),
    )
    with pytest.raises(KernelError, match="LIFECYCLE_OWNER"):
        k.start()
    assert k.view().lifecycle(ref).removed is None

    class Stale(Owner):
        removed = False

        def on_react(self, view, inbox, dirty):
            if not self.removed:
                self.removed = True
                return (Remove(ref),)
            return (
                FactWrite(
                    (ref, "x"),
                    2,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(view.instant, None),
                ),
            )

    k = Kernel()
    k.bind(
        registry,
        manifest,
        (Stale(Partition("owner", "owner", produces=("x",), lifecycle=True)),),
    )
    with pytest.raises(KernelError, match="ENTITY_STALE"):
        k.start()
    assert len(k._store.facts[(ref, "x")]) == 1
    assert replay(k.journal.bytes).incomplete


def test_identity_reference_accepts_known_tombstone_not_future_generation():
    source = EntityRef("r", "e", "source", 0, "Source")
    target = EntityRef("r", "e", "target", 0, "Target")

    class E(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition("owner", "owner", produces=("ref",), lifecycle=True)
            )
            self.wakeup_ns = 1
            self.stage = 0

        def write(self, view):
            return FactWrite(
                (source, "ref"),
                {"$ref": target.to_data()},
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
            )

        def initialize(self, view):
            return (Create(source), Create(target), self.write(view))

        def integrate(self, view):
            self.wakeup_ns = None
            return (Activate("owner"),)

        def on_react(self, view, inbox, dirty):
            if view.instant.ns == 1:
                if self.stage == 0:
                    self.stage = 1
                    return (Remove(target),)
                if self.stage == 1:
                    self.stage = 2
                    return (self.write(view),)
            return ()

    registry = MemoryRegistry(
        (
            TypeDescriptor("Root", abstract=True),
            TypeDescriptor("Source", ("Root",)),
            TypeDescriptor("Target", ("Root",)),
        ),
        (FieldDescriptor("ref", "Source", {"type": "ref", "target_type": "Target"}),),
    )
    manifest = BindingManifest(
        "r",
        "e",
        (source, target),
        rules=(BindingRule("owner", "Source", ("ref",)),),
        lifecycle=(LifecycleRule("owner", "Root"),),
    )
    k = Kernel()
    k.bind(registry, manifest, (E(),))
    k.start()
    k.run_until(1)
    assert (
        k.view().field((source, "ref"), Instant(1, 100)).value["$ref"]["generation"]
        == 0
    )
    replay(k.journal.bytes)

    class Future(E):
        def write(self, view):
            from dataclasses import replace

            return FactWrite(
                (source, "ref"),
                {"$ref": replace(target, generation=1).to_data()},
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
            )

    k = Kernel()
    k.bind(registry, manifest, (Future(),))
    import pytest

    from aerokernel import KernelError

    with pytest.raises(KernelError, match="ENTITY_UNKNOWN"):
        k.start()
