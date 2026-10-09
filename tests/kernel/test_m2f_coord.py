"""Earlier safe ingress, observable hold outputs, and mutant witnesses."""

import threading
from dataclasses import replace

import pytest

from aerokernel import (
    BindingManifest,
    CancelEdge,
    CancelObligation,
    ClockMapping,
    CloseEdge,
    CommandRequest,
    EndObligation,
    Horizon,
    IngressPolicy,
    Instant,
    Kernel,
    KernelError,
    MemoryRegistry,
    Partition,
    Stamp,
    Timing,
    replay,
)
from aerokernel.sdk import SimpleEngine
from tests.kernel.test_m2_ingress import make
from tests.kernel.test_m2_relations import edge, obligation, setup


def test_new_safe_ingress_dispatches_before_final_watermark():
    k, target = make(IngressPolicy(0, timeout_s=0.5))
    waiting, delivered = threading.Event(), threading.Event()
    original_horizon, original_react = target.horizon, target.on_react

    def horizon(partition, cut):
        waiting.set()
        return original_horizon(partition, cut)

    def react(view, inbox, dirty):
        result = original_react(view, inbox, dirty)
        if inbox:
            delivered.set()
        return result

    target.horizon, target.on_react = horizon, react
    errors = []

    def run():
        try:
            k.run_until(20)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert waiting.wait(1)
        k.submit_live(
            CommandRequest("do", "target", Instant(3), 9),
            Stamp("source", -7, 1, "source-v1"),
        )
        k.advance_watermark(3)
        assert delivered.wait(0.3), (
            "safe command at 3 must dispatch while waiting for 20"
        )
        assert target.seen[0][0] == Instant(3, 1)
        k.advance_watermark(20)
        thread.join(1)
        assert not thread.is_alive() and errors == []
        assert replay(k.journal.bytes).records == k.records
    finally:
        k.close()
        thread.join(1)


@pytest.mark.parametrize("bound", [20, None])
@pytest.mark.parametrize(
    "kind", ["assert", "close", "cancel", "activate", "end", "cancel_obligation"]
)
def test_all_relation_outputs_fault_during_actual_native_hold(bound, kind):
    initials = {
        "assert": (),
        "close": (edge(),),
        "cancel": (edge(start=Instant(20)),),
        "activate": (),
        "end": (obligation(),),
        "cancel_obligation": (obligation(start=Instant(20)),),
    }
    operations = {
        "assert": edge(start=Instant(3)),
        "close": CloseEdge("a"),
        "cancel": CancelEdge("a"),
        "activate": obligation(start=Instant(3)),
        "end": EndObligation("o"),
        "cancel_obligation": CancelObligation("o"),
    }
    old, owner, reader = setup(initials[kind])
    owner.partition = replace(
        owner.partition, timing=Timing("lockstep", 20, certified_hold=True)
    )
    owner.partitions = (owner.partition,)
    k = Kernel(provenance="full")
    k.bind(old._store.registry, old._store.manifest, (owner, reader))
    owner.horizon = lambda partition, cut: Horizon(
        owner.logical, owner.native_ns, 20, bound, None, cut
    )
    held = []

    def hold(partition, to, view):
        owner.logical = to
        held.append((to.ns, owner.native_ns))
        return view.batch((operations[kind],))

    owner.advance = hold
    try:
        k.start()
        before_edges, before_obligations = (
            dict(k._store.edges),
            dict(k._store.obligations),
        )
        with pytest.raises(KernelError, match="OUTPUT_BOUND"):
            k.run_until(3)
        assert held == [(3, 0)]
        assert (
            k._store.edges == before_edges
            and k._store.obligations == before_obligations
        )
        assert k._store.frontiers["owner"][1] == 0
        assert replay(k.journal.bytes).records == k.records
    finally:
        k.close()
        old.close()


def test_pacing_waits_to_wall_clock_deadline_with_controlled_clock(monkeypatch):
    k = Kernel(
        provenance="full", ingress_policy=IngressPolicy(50_000_000, speed_ratio=1)
    )
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (SimpleEngine(Partition("p", "e", timing=Timing("real_time"))),),
    )
    k.start()
    now = [100.0]
    waits = []
    k._pace_origin = now[0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])

    def wait(seconds):
        assert seconds > 0
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(k._ingress_condition, "wait", wait)
    try:
        k.run_until(50_000_000)
        assert sum(waits) == pytest.approx(0.05)
        assert now[0] >= 100.05
    finally:
        k.close()


@pytest.mark.parametrize("mapped_ns", [-1, 0, 1])
def test_relation_bootstrap_acquisition_validates_actual_mapped_source(mapped_ns):
    old, owner, reader = setup(
        (replace(edge(), acquired=Stamp("source", mapped_ns - 10, 1, "source-v1")),)
    )
    k = Kernel(
        provenance="full",
        mappings=(
            ClockMapping("canonical", "canonical"),
            ClockMapping("source-v1", "source", offset_ns=10),
        ),
    )
    k.bind(old._store.registry, old._store.manifest, (owner, reader))
    try:
        if mapped_ns > 0:
            with pytest.raises(KernelError, match="ACQUISITION_FUTURE"):
                k.start()
            assert not k._store.edges
        else:
            k.start()
            assert (
                k.view().relations("R", Instant(0))[0].acquired.numerator
                == mapped_ns - 10
            )
        assert replay(k.journal.bytes).records == k.records
    finally:
        k.close()
        old.close()


def test_notifications_without_physical_progress_do_not_reset_watermark_budget(
    monkeypatch,
):
    k, _ = make(IngressPolicy(0, timeout_s=0.1))
    now = [100.0]
    waits = []
    monkeypatch.setattr("time.monotonic", lambda: now[0])

    def notify(seconds):
        waits.append(seconds)
        now[0] += min(seconds, 0.04)
        k.advance_watermark(len(waits))

    monkeypatch.setattr(k._ingress_condition, "wait", notify)
    try:
        with pytest.raises(KernelError, match="WATERMARK_TIMEOUT"):
            k.run_until(20)
        assert len(waits) == 3
        assert now[0] == pytest.approx(100.1)
        assert k._store.sealed_ns == 0
    finally:
        k.close()


def test_hold_allows_nonoutput_scheduling_and_activation():
    from aerokernel import Activate, Horizon, ScheduleTimer

    old, owner, reader = setup()
    owner.partition = replace(
        owner.partition, timing=Timing("lockstep", 20, certified_hold=True)
    )
    owner.partitions = (owner.partition,)
    owner.horizon = lambda partition, cut: Horizon(
        owner.logical, owner.native_ns, 20, None, None, cut
    )

    def hold(partition, to, view):
        owner.logical = to
        return view.batch((ScheduleTimer("next", Instant(4), {}), Activate("owner")))

    owner.advance = hold
    k = Kernel(provenance="full")
    k.bind(old._store.registry, old._store.manifest, (owner, reader))
    try:
        k.start()
        k.run_until(3)
        assert k._store.frontiers["owner"][1] == 0
        assert k._store.timer_queue.earliest() == Instant(4)
    finally:
        k.close()
        old.close()


def test_incremental_watermark_reselects_before_pacing_old_target(monkeypatch):
    k, target = make(IngressPolicy(0, timeout_s=0.5, speed_ratio=1))
    now, stage = [100.0], [0]
    k._pace_origin = now[0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])

    def wait(seconds):
        if stage[0] == 0:
            now[0] += 0.001
            k.submit_live(
                CommandRequest("do", "target", Instant(3_000_000), 9),
                Stamp("source", 2_999_990, 1, "source-v1"),
            )
            k.advance_watermark(3_000_000)
        elif stage[0] == 1:
            assert seconds == pytest.approx(0.002), "pace only to the newly safe 3 ms"
            now[0] += seconds
        elif stage[0] == 2:
            assert target.seen, "producer closes 20 only after the response at 3"
            k.advance_watermark(20_000_000)
        else:
            now[0] += seconds
        stage[0] += 1

    monkeypatch.setattr(k._ingress_condition, "wait", wait)
    try:
        k.run_until(20_000_000)
        assert target.seen[0][0] == Instant(3_000_000, 1)
        assert stage[0] == 4
        assert now[0] == pytest.approx(100.02)
    finally:
        k.close()
