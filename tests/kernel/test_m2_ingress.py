"""Mapped live ingress retains actual source time, lateness and idempotency."""

import threading
from dataclasses import replace

import pytest

from aerokernel import (
    BindingManifest,
    ClockMapping,
    CommandRequest,
    IngressPolicy,
    Instant,
    Kernel,
    KernelError,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    Timing,
    replay,
)
from aerokernel.sdk import SimpleEngine

REAL_TIME = Timing("real_time")


def make(policy, timing=REAL_TIME):
    class Target(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition("target", "target", commands=("do",), timing=timing)
            )
            self.seen = []

        def on_react(self, view, inbox, dirty):
            self.seen.extend((view.instant, delivery.message) for delivery in inbox)
            return ()

    target = Target()
    k = Kernel(
        provenance="full",
        ingress_policy=policy,
        mappings=(
            ClockMapping("source-v1", "source", offset_ns=10),
            ClockMapping("canonical", "canonical"),
        ),
    )
    registry = MemoryRegistry(
        (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
    )
    k.bind(registry, BindingManifest("r", "e"), (target,))
    k.start()
    return k, target


def test_reject_late_is_recorded_without_reservation_sequence_or_action():
    k, _ = make(IngressPolicy(0))
    cut = k.view().cut
    with pytest.raises(KernelError, match="LATE_INGRESS"):
        k.submit_live(
            CommandRequest("do", "target", Instant(0), 7),
            Stamp("source", -10, 1, "source-v1"),
        )
    assert k.view().cut.index == cut.index + 1
    assert not k._store.sequences and not k._store.actions.states
    record = k.records[-1]
    assert record["mapped_ns"] == 0 and record["disposition"] == "rejected"
    assert replay(k.journal.bytes).records == k.records
    assert not k._store.faulted


def test_delayed_latched_live_message_keeps_original_stamp_and_recorded_delay():
    k, target = make(
        IngressPolicy(0, lateness="delay"), Timing("lockstep", 20, certified_hold=True)
    )
    original = CommandRequest("do", "target", Instant(0), 7, "key")
    stamp = Stamp("source", -13, 1, "source-v1")
    mid = k.submit_live(original, stamp)
    record = k.records[-1]
    assert record["mapped_ns"] == -3 and record["delay_ns"] == 4
    assert record["disposition"] == "delayed"
    k.advance_watermark(20)
    k.run_until(20)
    assert len(target.seen) == 1
    at, message = target.seen[0]
    assert at == Instant(20, 1) and message.source_stamp == stamp
    assert message.at == Instant(1) and message.available.ns == 1 and message.id == mid
    cut = k.view().cut
    assert k.submit_live(original, stamp) == mid and k.view().cut == cut
    with pytest.raises(KernelError, match="IDEMPOTENCY_CONFLICT"):
        k.submit_live(replace(original, payload=8), stamp)
    assert replay(k.journal.bytes).records == k.records


def test_live_submission_wakes_waiter_and_forces_recomputed_boundary():
    k, target = make(IngressPolicy(0, timeout_s=1))
    ready = threading.Event()
    original = target.horizon

    def horizon(partition, cut):
        ready.set()
        return original(partition, cut)

    target.horizon = horizon
    faults = []

    def run():
        try:
            k.run_until(20)
        except Exception as error:
            faults.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(1)
    k.submit_live(
        CommandRequest("do", "target", Instant(3), 9),
        Stamp("source", -7, 1, "source-v1"),
    )
    k.advance_watermark(20)
    thread.join(1)
    assert not thread.is_alive() and faults == []
    assert target.seen[0][0] == Instant(3, 1)
    assert replay(k.journal.bytes).records == k.records


@pytest.mark.parametrize(
    "policy",
    [
        lambda: IngressPolicy(True),
        lambda: IngressPolicy(-1),
        lambda: IngressPolicy(0, lateness="ignore"),
        lambda: IngressPolicy(0, timeout_s=None),
        lambda: IngressPolicy(0, timeout_s=True),
        lambda: IngressPolicy(0, speed_ratio=0),
        lambda: IngressPolicy(0, speed_ratio=float("inf")),
    ],
)
def test_ingress_policy_requires_actual_declared_finite_values(policy):
    with pytest.raises(KernelError):
        policy()


def test_invalid_live_command_clock_or_payload_is_preflight_without_changes():
    k, _ = make(IngressPolicy(0, lateness="delay"))
    request = CommandRequest("do", "target", Instant(3), 1)
    stamp = Stamp("source", -7, 1, "source-v1")
    for command, source in (
        (replace(request, target="missing"), stamp),
        (replace(request, payload=None), stamp),
        (request, replace(stamp, mapping_id="missing")),
        (request, replace(stamp, clock_id="different")),
    ):
        cut = k.view().cut
        with pytest.raises(KernelError):
            k.submit_live(command, source)
        assert k.view().cut == cut
    with pytest.raises(KernelError, match="INGRESS_STATE"):
        from aerokernel.ingress import reserve_live

        state = k._store.clone()
        state.sealed_ns = None
        reserve_live(state, request, stamp, k.ingress_policy, k.mappings, k.budget)
