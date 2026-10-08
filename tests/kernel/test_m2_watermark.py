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

    k = Kernel(ingress_policy=policy)
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
    assert k.records[-1]["code"] == "WATERMARK_TIMEOUT"
    assert replay(k.journal.bytes).records == k.records
    k.close()


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
        Kernel().bind(
            MemoryRegistry(()),
            BindingManifest("r", "e"),
            (SimpleEngine(Partition("p", "e", timing=Timing("real_time"))),),
        )
