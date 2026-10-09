"""Adversarial authority, temporal and routing traces."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerokernel import (
    ABSENT,
    BindingManifest,
    BindingRule,
    CommandRequest,
    Create,
    Dependency,
    Emit,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    KernelError,
    LifecycleReady,
    LifecycleRule,
    LocalCause,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.codec import encode
from aerokernel.journal import prefixes
from aerokernel.sdk import SimpleEngine

ROOT = EntityRef("r", "e", "object", 0, "Thing")


def fact(view, value=1):
    return FactWrite(
        (ROOT, "x"),
        value,
        Stamp("canonical", view.instant.ns, 1, "canonical"),
        Interval(view.instant, None),
    )


class Owner(SimpleEngine):
    def __init__(self):
        super().__init__(
            Partition(
                "owner", "owner", produces=("x",), commands=("do",), lifecycle=True
            )
        )
        self.seen = []

    def initialize(self, view):
        return (Create(ROOT), fact(view, 0))

    def on_react(self, view, inbox, dirty):
        self.seen.extend(inbox)
        return ()


def bind(owner=None, extra=(), *, manifest=None, registry=None, **kwargs):
    registry = registry or MemoryRegistry(
        (TypeDescriptor("Thing"),),
        (FieldDescriptor("x", "Thing", {"type": "integer"}),),
        (
            MessageDescriptor(
                "do", "command", {"type": "integer"}, cancel_support=True
            ),
            MessageDescriptor("event", "event", {"type": "integer"}),
        ),
    )
    manifest = manifest or BindingManifest(
        "r",
        "e",
        (ROOT,),
        rules=(BindingRule("owner", "Thing", ("x",)),),
        lifecycle=(LifecycleRule("owner", "Thing"),),
    )
    kernel = Kernel(provenance="full", **kwargs)
    kernel.bind(registry, manifest, (owner or Owner(),) + extra)
    return kernel


def test_ingress_idempotency_reserves_once_and_replay_pending():
    owner = Owner()
    k = bind(owner)
    k.start()
    request = CommandRequest("do", "owner", Instant(3), 9, "key", 2)
    mid = k.submit(request)
    cut = k.view().cut
    assert k.submit(request) == mid
    assert k.view().cut == cut
    with pytest.raises(KernelError):
        k.submit(replace(request, payload=10))
    assert not owner.seen
    assert replay(k.journal.bytes).action(mid).status == "pending"
    k.run_until(3)
    assert len(owner.seen) == 1
    assert owner.seen[0].instant > owner.seen[0].message.available
    assert k.action(mid).status == "submitted"
    assert replay(k.journal.bytes).action(mid).status == "submitted"


def test_cross_batch_local_causes_future_causes_and_foreign_writes_reject():
    for proposal in (
        lambda view: Emit("event", "event", "none", view.instant, 1, (LocalCause(0),)),
        lambda view: Emit(
            "event", "event", "none", view.instant, 1, (ItemRef(999, 0),)
        ),
        lambda view: FactWrite(
            (replace(ROOT, run_id="foreign"), "x"),
            1,
            Stamp("canonical", 0, 1, "canonical"),
            Interval(Instant(0), None),
        ),
    ):

        class Bad(Owner):
            def __init__(self, proposal=proposal):
                super().__init__()
                self.proposal = proposal
                self.partitions = (
                    replace(
                        self.partition, emits=("event",), message_targets=("none",)
                    ),
                )
                self.partition = self.partitions[0]

            def on_react(self, view, inbox, dirty):
                return (self.proposal(view),)

        k = bind(Bad())
        with pytest.raises(KernelError):
            k.start()
        assert k.view().field((ROOT, "x"), Instant(0, 100)).value == 0


def test_nonowner_bootstrap_and_duplicate_generation_are_rejected_atomically():
    class Intruder(SimpleEngine):
        def initialize(self, view):
            return (fact(view, 20),)

    k = bind(extra=(Intruder(Partition("other", "other", produces=("x",))),))
    with pytest.raises(KernelError):
        k.start()
    assert not k._store.lives
    assert replay(k.journal.bytes)._store.lives == {}

    class Duplicate(Owner):
        def initialize(self, view):
            return (Create(ROOT), Create(ROOT))

    k = bind(Duplicate())
    with pytest.raises(KernelError):
        k.start()
    assert not k._store.lives


def test_restricted_read_future_cut_and_undeclared_reads():
    class Spy(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            view.field((ROOT, "x"), view.instant)
            return ()

    k = bind(extra=(Spy(Partition("spy", "spy", lifecycle_reads=("Thing",))),))
    with pytest.raises(KernelError, match="undeclared"):
        k.start()

    class Future(Owner):
        def on_react(self, view, inbox, dirty):
            view.field((ROOT, "x"), Instant(view.instant.ns + 1))
            return ()

    k = bind(Future())
    with pytest.raises(KernelError):
        k.start()


def test_native_cleanup_freezes_writes_and_requires_actual_ack():
    class Cleaner(Owner):
        cleaned = False

        def on_react(self, view, inbox, dirty):
            if not self.cleaned:
                self.cleaned = True
                return (LifecycleReady(ROOT), fact(view, 5))
            return ()

    manifest = BindingManifest(
        "r",
        "e",
        (ROOT,),
        rules=(BindingRule("owner", "Thing", ("x",)),),
        lifecycle=(LifecycleRule("owner", "Thing"),),
        lifecycle_participants={"Thing": ("owner",)},
    )
    k = bind(Cleaner(), manifest=manifest)
    with pytest.raises(KernelError):
        k.start()
    assert k.view().field((ROOT, "x"), Instant(0, 100)).value == 0


def test_validity_expiry_and_value_only_suppression():
    seen = []

    class Observer(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            seen.append(
                (
                    view.instant,
                    [(d.kind, d.value_changed) for d in dirty],
                    view.field((ROOT, "x"), view.instant),
                )
            )
            return ()

    class Expiring(Owner):
        def initialize(self, view):
            return (
                Create(ROOT),
                FactWrite(
                    (ROOT, "x"),
                    1,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), Instant(4)),
                ),
            )

    observer = Observer(
        Partition("observer", "observer", consumes=(Dependency("x", value_only=True),))
    )
    k = bind(Expiring(), (observer,))
    k.start()
    k.run_until(4)
    assert seen[-1][2] is ABSENT
    assert seen[-1][1] == [("validity", True)]
    replay(k.journal.bytes)


@settings(max_examples=15, deadline=None)
@given(st.lists(st.integers(-100, 100), min_size=1, max_size=6), st.booleans())
def test_registration_order_byte_determinism_and_prefixes(values, reverse):
    class Publisher(SimpleEngine):
        def initialize(self, view):
            return tuple(
                Emit("event", "event", "topic", Instant(0), value) for value in values
            )

    class Subscriber(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition("subscriber", "subscriber", subscribes=("topic",))
            )
            self.received = []

        def on_react(self, view, inbox, dirty):
            self.received.extend(d.message.payload for d in inbox)
            return ()

    def run(reverse):
        pub = Publisher(
            Partition(
                "publisher", "publisher", emits=("event",), message_targets=("topic",)
            )
        )
        sub = Subscriber()
        engines = (Owner(), pub, sub)
        registry = MemoryRegistry(
            (TypeDescriptor("Thing"),),
            (FieldDescriptor("x", "Thing", {"type": "integer"}),),
            (
                MessageDescriptor("do", "command", {"type": "integer"}),
                MessageDescriptor("event", "event", {"type": "integer"}),
            ),
        )
        manifest = BindingManifest(
            "r",
            "e",
            (ROOT,),
            rules=(BindingRule("owner", "Thing", ("x",)),),
            lifecycle=(LifecycleRule("owner", "Thing"),),
        )
        k = Kernel(provenance="full", root_seed=123)
        k.queue_snapshots = {0: []}
        publish = k._publish

        def capture(state, record):
            publish(state, record)
            k.queue_snapshots[state.cut.index] = encode(state.work)

        k._publish = capture
        k.bind(registry, manifest, tuple(reversed(engines)) if reverse else engines)
        k.start()
        k.run_until(2)
        assert sub.received == values
        return k

    a, b = run(reverse), run(not reverse)
    assert a.journal.bytes == b.journal.bytes
    for prefix in prefixes(a.journal.bytes):
        r = replay(prefix)
        assert encode(r._store.work) == a.queue_snapshots[r.view().cut.index]
