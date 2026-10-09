"""Conservative boundaries, declared scopes, lifecycle priority and timer order."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aerokernel import (
    Activate,
    BindingManifest,
    BindingRule,
    CancelTimer,
    CommandRequest,
    Create,
    Dependency,
    Emit,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    RequestCancel,
    ScheduleTimer,
    Stamp,
    Timing,
    TypeDescriptor,
    replay,
)
from aerokernel.sdk import SimpleEngine


class TimerEngine(SimpleEngine):
    def __init__(self):
        super().__init__(Partition("timer", "timer"))
        self.seen = []

    def initialize(self, view):
        return (
            ScheduleTimer("early", Instant(0, 2), 1),
            ScheduleTimer("later", Instant(0, 5), 2),
            ScheduleTimer("canceled", Instant(1), 3),
            CancelTimer("canceled"),
            Activate("timer"),
        )

    def on_react(self, view, inbox, dirty):
        self.seen.append((view.instant.microstep, [d.kind for d in dirty]))
        return ()


def test_timer_and_inbox_use_earliest_microstep_not_latest_deadline():
    engine = TimerEngine()
    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (engine,))
    k.start()
    assert engine.seen == [(1, ["activation"]), (3, ["timer"]), (6, ["timer"])]
    k.run_until(1)
    assert engine.seen == [(1, ["activation"]), (3, ["timer"]), (6, ["timer"])]
    assert replay(k.journal.bytes)._store.timers == k._store.timers


@pytest.mark.parametrize(
    "ops",
    [
        (ScheduleTimer("id", Instant(0), None),),
        (ScheduleTimer(True, Instant(1), None),),
        (ScheduleTimer("id", Instant(1), None), ScheduleTimer("id", Instant(2), None)),
        (CancelTimer("missing"),),
        (ScheduleTimer("id", Instant(1), None), CancelTimer("id"), CancelTimer("id")),
        (Activate("other"),),
    ],
)
def test_invalid_owned_timer_and_activation_proposals_reject(ops):
    class Bad(SimpleEngine):
        def initialize(self, view):
            return ops

    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (Bad(Partition("p", "e")),))
    with pytest.raises(KernelError):
        k.start()


def test_idempotency_uses_original_request_after_seal_advances():
    engine = SimpleEngine(Partition("target", "target", commands=("do",)))
    registry = MemoryRegistry(
        (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
    )
    k = Kernel(provenance="full")
    k.bind(registry, BindingManifest("r", "e"), (engine,))
    k.start()
    request = CommandRequest("do", "target", Instant(3), 1, "key")
    mid = k.submit(request)
    k.run_until(5)
    before = k.view().cut
    assert k.submit(request) == mid and k.view().cut == before
    replay(k.journal.bytes)


def test_lifecycle_cohort_activated_through_member_runs_before_unrelated_writer():
    seen = []

    class Named(SimpleEngine):
        def initialize(self, view):
            return (
                (Activate(self.partition.id),)
                if self.partition.id != "controller"
                else ()
            )

        def on_react(self, view, inbox, dirty):
            seen.append((self.partition.id, view.instant.microstep))
            return ()

    k = Kernel(provenance="full")
    engines = (
        Named(Partition("member", "member")),
        Named(Partition("controller", "controller", lifecycle=True)),
        Named(Partition("ordinary", "ordinary")),
    )
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e", cohorts=(("member", "controller"),)),
        engines,
    )
    k.start()
    assert seen == [("controller", 1), ("member", 1), ("ordinary", 2)]
    replay(k.journal.bytes)


def test_route_lag_is_applied_once_before_recipient_grid_latch_and_empty_fanout():
    class Pub(SimpleEngine):
        def initialize(self, view):
            return (
                Emit("event", "event", "topic", Instant(0), 9),
                Emit("event", "event", "empty", Instant(0), 10),
            )

    class Sub(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "sub",
                    "sub",
                    subscribes=("topic",),
                    timing=Timing("fixed_step", 20),
                    message_lag_ns=3,
                )
            )
            self.seen = []

        def on_react(self, view, inbox, dirty):
            self.seen.extend(inbox)
            return ()

    sub = Sub()
    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("event", "event", {"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (
            Pub(
                Partition(
                    "pub", "pub", emits=("event",), message_targets=("topic", "empty")
                )
            ),
            sub,
        ),
    )
    k.start()
    k.run_until(3)
    assert not sub.seen
    k.run_until(20)
    assert len(sub.seen) == 1 and sub.seen[0].instant == Instant(20, 1)
    assert any(i["kind"] == "fanout_empty" for r in k.records for i in r["items"])
    replay(k.journal.bytes)


@pytest.mark.parametrize(
    "partition",
    [
        Partition("p", "e", timing=Timing("lockstep")),
        Partition("p", "e", timing=Timing("real_time")),
    ],
)
def test_external_profiles_require_declared_contracts(partition):
    with pytest.raises(KernelError):
        Kernel(provenance="full").bind(
            MemoryRegistry(()), BindingManifest("r", "e"), (SimpleEngine(partition),)
        )


@pytest.mark.parametrize(
    "partition",
    [
        Partition("p", "e", timing=Timing("unknown")),
        Partition("p", "e", timing=Timing("fixed_step", True)),
        Partition("p", "e", timing=Timing(origin_ns=1)),
        Partition("p", "e", message_lag_ns=-1),
        Partition("p", "e", emits=("event",)),
    ],
)
def test_invalid_selected_timing_and_route_policies_fail_bind(partition):
    with pytest.raises(KernelError):
        Kernel(provenance="full").bind(
            MemoryRegistry(()), BindingManifest("r", "e"), (SimpleEngine(partition),)
        )


def test_nonreactive_zero_lag_cycle_fails_at_bind():
    registry = MemoryRegistry(
        (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
    )
    manifest = BindingManifest("r", "e", rules=(BindingRule("p", "T", ("x",)),))
    with pytest.raises(KernelError, match="CYCLE_REENTRY"):
        Kernel(provenance="full").bind(
            registry,
            manifest,
            (
                SimpleEngine(
                    Partition(
                        "p",
                        "e",
                        produces=("x",),
                        consumes=(Dependency("x"),),
                        reactive=False,
                    )
                ),
            ),
        )


def test_output_promise_faults_before_fact_publication_and_echo_mismatch():
    ref = EntityRef("r", "e", "x", 0, "T")

    class Wrong(SimpleEngine):
        def initialize(self, view):
            return (Create(ref),)

        def advance(self, partition, to, view):
            self.logical = to
            self.native_ns = to.ns
            return view.batch(
                (
                    FactWrite(
                        (ref, "x"),
                        1,
                        Stamp("canonical", 0, 1, "canonical"),
                        Interval(Instant(0), None),
                    ),
                )
            )

    registry = MemoryRegistry(
        (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
    )
    manifest = BindingManifest(
        "r",
        "e",
        (ref,),
        rules=(BindingRule("p", "T", ("x",)),),
        lifecycle=(LifecycleRule("p", "T"),),
    )
    k = Kernel(provenance="full")
    k.bind(
        registry,
        manifest,
        (Wrong(Partition("p", "e", produces=("x",), lifecycle=True)),),
    )
    k.start()
    with pytest.raises(KernelError, match="OUTPUT_BOUND"):
        k.run_until(1)
    from aerokernel import ABSENT

    assert k.view().field((ref, "x"), Instant(1)) is ABSENT
    replay(k.journal.bytes)

    class Echo(SimpleEngine):
        def initialize(self, view):
            return ()

        def advance(self, partition, to, view):
            return replace(view.batch(), native_reached_ns=999)

    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (Echo(Partition("p", "e")),))
    k.start()
    with pytest.raises(KernelError, match="INVOCATION_FRONTIER"):
        k.run_until(1)


def test_engine_originated_cancel_is_owned_and_result_routed():
    from tests.kernel.test_messages import Target, registry

    class Origin(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "origin", "origin", emits=("do",), message_targets=("target",)
                )
            )
            self.requested = False

        def initialize(self, view):
            return (Emit("command", "do", "target", Instant(0), 1),)

        def on_react(self, view, inbox, dirty):
            if not self.requested:
                command = next(
                    (
                        d.payload["command_id"]
                        for d in dirty
                        if d.kind == "receipt"
                        and isinstance(
                            d.payload, __import__("collections.abc").abc.Mapping
                        )
                        and view.action(d.payload["command_id"]).status == "executing"
                    ),
                    None,
                )
                if command is not None:
                    self.requested = True
                    return (RequestCancel(command),)
            return ()

    k = Kernel(provenance="full")
    k.bind(registry(), BindingManifest("r", "e"), (Origin(), Target()))
    k.start()
    assert next(iter(k._store.actions.states.values())).status == "canceled"
    assert len(k._store.messages) == 2
    assert (
        replay(k.journal.bytes)._store.actions.to_data() == k._store.actions.to_data()
    )


def test_frontier_structural_integers_reject_bool_and_mappings_stay_pinned():
    from aerokernel import Batch, ClockMapping, Cut, Horizon

    cut = Cut(0, Instant(0))
    with pytest.raises(KernelError):
        Batch(Instant(0), cut, cut, False, cut)
    with pytest.raises(KernelError):
        Horizon(Instant(0), False, None, None, None, cut)
    with pytest.raises(KernelError):
        Horizon(Instant(0), 0, False, None, None, cut)
    k = Kernel(provenance="full")
    with pytest.raises(TypeError):
        k.mappings["canonical"] = ClockMapping("canonical", "canonical", offset_ns=1)
