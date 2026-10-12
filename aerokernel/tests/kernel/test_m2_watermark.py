"""Declared closure gates physical work, with bounded waits and offline replay."""

import threading
import time

import pytest

from aerokernel import (
    BindingManifest,
    IngressPolicy,
    Kernel,
    KernelError,
    MemoryRegistry,
    Partition,
    Timing,
    replay,
)
from aerokernel.sdk import SimpleEngine


def make(policy):
    calls = []

    class Live(SimpleEngine):
        def advance(self, partition, to, view):
            calls.append(to)
            return super().advance(partition, to, view)

    k = Kernel(provenance="full", ingress_policy=policy)
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (Live(Partition("live", "live", timing=Timing("real_time"))),),
    )
    k.start()
    return k, calls


def test_watermark_timeout_happens_before_any_native_or_logical_advance():
    k, calls = make(IngressPolicy(0, timeout_s=0.02))
    before = time.monotonic()
    with pytest.raises(KernelError, match="WATERMARK_TIMEOUT"):
        k.run_until(20)
    assert time.monotonic() - before < 1
    assert calls == [] and k._store.sealed_ns == 0
    assert k.records[-1]["type"] == "run_limit"
    assert not k._store.faulted
    assert replay(k.journal.bytes).records == k.records
    k.close()
    assert k.records[-1]["type"] == "run_stop"
    assert not replay(k.journal.bytes).incomplete


def test_unbounded_wait_can_yield_and_resume_without_inventing_a_seal():
    from aerokernel import IngressWait

    k, calls = make(IngressPolicy(0))
    observed = []

    def yield_wait(state):
        observed.append(state)
        return False

    returned = k.run_until(20, on_wait=yield_wait)
    assert observed == [IngressWait(20, ("default",))]
    assert returned.cut == k.view().cut
    assert k.sealed_ns == 0 and calls == []
    assert not any(record["type"] == "fault" for record in k.records)
    k.advance_watermark(20)
    k.run_until(20)
    assert k.sealed_ns == 20
    k.close()
    assert not replay(k.journal.bytes).incomplete


def test_wait_is_not_busy_polled_and_recorded_watermark_releases_it():
    k, calls = make(IngressPolicy(0, timeout_s=1))
    started = threading.Event()
    failures = []
    engine = k._by_partition["live"]
    original = engine.horizon
    queries = []

    def horizon(partition, cut):
        queries.append(cut)
        started.set()
        return original(partition, cut)

    engine.horizon = horizon

    def run():
        try:
            k.run_until(20)
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(1)
    k.advance_watermark(20)
    thread.join(1)
    assert not thread.is_alive() and failures == []
    assert [at.ns for at in calls] == [20]
    assert len(queries) == 3  # Before wait, after cut change, at settlement.
    assert k._store.watermark_ns == k._store.sealed_ns == 20
    assert replay(k.journal.bytes).records == k.records
    with pytest.raises(KernelError, match="INGRESS_WATERMARK"):
        k.advance_watermark(19)
    cut = k.view().cut
    k.advance_watermark(20)
    assert k.view().cut == cut
    k.close()


def test_optional_pacing_waits_without_changing_transaction_order():
    plain, plain_calls = make(IngressPolicy(10_000_000))
    paced, paced_calls = make(IngressPolicy(10_000_000, speed_ratio=1))
    plain.run_until(10_000_000)
    paced.run_until(10_000_000)
    assert plain_calls == paced_calls
    assert plain.records == paced.records
    assert replay(paced.journal.bytes).records == paced.records
    plain.close()
    paced.close()


def test_real_time_cannot_bind_without_a_declared_watermark():
    with pytest.raises(KernelError, match="INGRESS_POLICY"):
        Kernel(provenance="full").bind(
            MemoryRegistry(()),
            BindingManifest("r", "e"),
            (SimpleEngine(Partition("p", "e", timing=Timing("real_time"))),),
        )


def test_host_wait_releases_ingress_and_pause_preserves_the_source_budget():
    kernel, calls = make(IngressPolicy(0, timeout_s=0.03))
    suspended, resume, admitted = (threading.Event() for _ in range(3))
    failures = []

    def wait(state):
        if state is not None:
            suspended.set()
            assert resume.wait(2)
        return True

    def run():
        try:
            kernel.run_until(20, on_wait=wait)
        except BaseException as error:
            failures.append(error)

    def admit():
        kernel.advance_watermark(20)
        admitted.set()

    execution = threading.Thread(target=run)
    admission = threading.Thread(target=admit)
    execution.start()
    try:
        assert suspended.wait(1)
        admission.start()
        assert admitted.wait(0.5), "A paused host must not retain the ingress lock"
        time.sleep(0.06)  # Longer than the actual authored source timeout.
    finally:
        resume.set()
        execution.join(2)
        if admission.ident is not None:
            admission.join(2)
    assert not execution.is_alive() and not admission.is_alive()
    assert failures == [] and kernel.sealed_ns == 20
    assert [at.ns for at in calls] == [20]
    assert sum(row["type"] == "run_limit" for row in kernel.records) == 1
    assert replay(kernel.journal.bytes).records == kernel.records
    kernel.close()


def test_source_still_times_out_after_host_suspension_without_input():
    kernel, calls = make(IngressPolicy(0, timeout_s=0.03))
    resumed = None

    def wait(state):
        nonlocal resumed
        if resumed is None:
            time.sleep(0.06)
            resumed = time.monotonic()
        return True

    with pytest.raises(KernelError, match="WATERMARK_TIMEOUT"):
        kernel.run_until(20, on_wait=wait)
    assert time.monotonic() - resumed >= 0.025
    assert calls == [] and kernel.sealed_ns == 0 and not kernel._store.faulted
    assert replay(kernel.journal.bytes).records == kernel.records
    kernel.close()
