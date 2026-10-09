"""Jacobi transitions share one immutable base; quiescence includes events."""

from __future__ import annotations

import pytest

from aerokernel import (
    BindingManifest,
    BindingRule,
    Create,
    Dependency,
    Emit,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    MicrostepLimitExceeded,
    Partition,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.sdk import ContextEngine, FactPolicy, SimpleEngine


def test_declared_zero_lag_scc_reads_jacobi_base_and_settles_without_reintegration():
    ref = EntityRef("r", "e", "entity", 0, "T")
    observations = []
    native_calls = []
    policy = FactPolicy(
        lambda at: Stamp("canonical", at.ns, 1, "canonical"),
        lambda at: Interval(at, None),
    )

    class Controller(SimpleEngine):
        def initialize(self, view):
            return (Create(ref),)

    class Member(ContextEngine):
        def __init__(self, own, dependency):
            super().__init__(
                Partition(
                    own,
                    own,
                    produces=(own,),
                    consumes=(Dependency(dependency, value_only=True),),
                ),
                policies={own: policy},
            )
            self.own, self.dependency = own, dependency

        def bootstrap(self, ctx):
            ctx.set(ref, self.own, 0)

        def step(self, ctx):
            native_calls.append((self.own, ctx.now))

        def on_inputs(self, ctx):
            prior = ctx.get(ref, self.dependency)
            observations.append(
                (self.own, ctx.now, ctx.view.transaction_base_cut, prior)
            )
            ctx.set(ref, self.own, min(prior + 1, 3))

    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        tuple(FieldDescriptor(f, "T", {"type": "integer"}) for f in ("x", "y")),
    )
    k = Kernel(provenance="full", max_microsteps=16)
    k.bind(
        registry,
        BindingManifest(
            "r",
            "e",
            (ref,),
            rules=tuple(BindingRule(f, "T", (f,)) for f in ("x", "y")),
            lifecycle=(LifecycleRule("controller", "T"),),
        ),
        (
            Member("y", "x"),
            Controller(Partition("controller", "controller", lifecycle=True)),
            Member("x", "y"),
        ),
    )
    k.start()
    assert ("x", "y") in k._sccs
    assert native_calls == []
    assert len(observations) == 8
    for step in range(4):
        left, right = observations[2 * step : 2 * step + 2]
        assert left[0] == "x" and right[0] == "y"
        assert left[1] == right[1] == Instant(0, step + 2)
        assert left[2] == right[2]
        assert left[3] == right[3] == min(step, 3)
    for f in ("x", "y"):
        assert k.view().field((ref, f), Instant(0, 16)).value == 3
    restored = replay(k.journal.bytes)
    assert restored.records == k.records


def test_equal_state_does_not_quiesce_an_event_cycle_and_fault_is_replayable():
    ref = EntityRef("r", "e", "entity", 0, "T")

    class Ping(SimpleEngine):
        def __init__(self, name, peer):
            super().__init__(
                Partition(name, name, emits=("ping",), message_targets=(peer,))
            )
            self.peer = peer

        def initialize(self, view):
            return (Emit("event", "ping", self.peer, view.instant, 1),)

        def on_react(self, view, inbox, dirty):
            assert len(inbox) == 1
            return (
                Emit(
                    "event",
                    "ping",
                    self.peer,
                    view.instant,
                    1,
                    (inbox[0].dispatch_ref,),
                ),
            )

    class Owner(SimpleEngine):
        def initialize(self, view):
            return (
                Create(ref),
                FactWrite(
                    (ref, "x"),
                    7,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), None),
                ),
            )

    k = Kernel(provenance="full", max_microsteps=5)
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),),
            (FieldDescriptor("x", "T", {"type": "integer"}),),
            (MessageDescriptor("ping", "event", {"type": "integer"}),),
        ),
        BindingManifest(
            "r",
            "e",
            (ref,),
            rules=(BindingRule("owner", "T", ("x",)),),
            lifecycle=(LifecycleRule("owner", "T"),),
        ),
        (
            Ping("a", "b"),
            Ping("b", "a"),
            Owner(Partition("owner", "owner", produces=("x",), lifecycle=True)),
        ),
    )
    with pytest.raises(MicrostepLimitExceeded) as error:
        k.start()
    assert error.value.context["trace"]
    assert k.records[-1]["code"] == "MICROSTEP_LIMIT"
    assert k.view().field((ref, "x"), Instant(0, 5)).value == 7
    assert len(k._store.messages) == 10
    assert replay(k.journal.bytes).records == k.records
