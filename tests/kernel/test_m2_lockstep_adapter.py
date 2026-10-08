"""Native simulator observations prove the 3-ms / 20-ms causal contract."""

import pytest

from aerokernel import CommandRequest, Instant, KernelError, replay
from aerokernel.codec import decode_record
from aerokernel.testing import FakeLockstepSimulator
from examples.lockstep_adapter_skeleton import MS, make_adapter_run


def test_off_grid_hold_keeps_native_start_cut_and_latches_after_boundary():
    sim = FakeLockstepSimulator(20 * MS)
    k, adapter = make_adapter_run(sim)
    initial = k.start().cut
    mid = k.submit(
        CommandRequest("control", "external", Instant(3 * MS), 9, ingress_at_ns=3 * MS)
    )
    k.run_until(3 * MS)
    assert sim.native_ns == 0 and sim.calls == [] and sim.inputs == []
    assert adapter.logical.ns == 3 * MS and adapter.native_ns == 0
    k.run_until(20 * MS)
    assert sim.calls == [(0, 20 * MS, 20 * MS)]
    assert sim.inputs == [(20 * MS, 9)]
    physical = [
        item
        for record in k.records
        for item in record["items"]
        if item["kind"] == "return" and item["native_interval"] == [0, 20 * MS]
    ]
    assert len(physical) == 1
    assert decode_record(physical[0]["native_input_cut"]) == initial
    assert decode_record(physical[0]["read_cut"]) == initial
    dispatches = [
        item
        for record in k.records
        for item in record["items"]
        if item["kind"] == "dispatch"
    ]
    assert decode_record(dispatches[0]["delivery"]).instant.ns == 20 * MS
    assert (
        k.action(mid).status == "submitted"
    )  # Applying an input does not invent completion.
    assert replay(k.journal.bytes).records == k.records
    k.close()


def test_unexpected_early_return_faults_without_publishing_a_native_output():
    sim = FakeLockstepSimulator(20 * MS, early_return_ns=3 * MS, outputs=((3 * MS, 7),))
    k, _ = make_adapter_run(sim)
    k.start()
    with pytest.raises(KernelError, match="LOCKSTEP_EARLY_RETURN"):
        k.run_until(20 * MS)
    assert sim.native_ns == 3 * MS  # Real irreversible native state is not rolled back.
    assert k._store.messages == {}
    assert k._store.frontiers["external"][1] == 0
    assert k.records[-1]["type"] == "fault"
    assert replay(k.journal.bytes).records == k.records
    with pytest.raises(KernelError, match="RUN_FAULTED"):
        k.run_until(40 * MS)
    k.close()


def test_buffering_finishes_the_grant_and_records_delayed_occurrence():
    sim = FakeLockstepSimulator(20 * MS, early_return_ns=3 * MS, outputs=((3 * MS, 7),))
    k, _ = make_adapter_run(sim, buffer_early_return=True)
    k.start()
    k.run_until(3 * MS)
    assert sim.native_ns == 0 and k._store.messages == {}
    k.run_until(20 * MS)
    assert sim.calls == [(0, 20 * MS, 3 * MS), (3 * MS, 20 * MS, 20 * MS)]
    message = next(iter(k._store.messages.values()))
    assert message.at.ns == 3 * MS
    assert message.available == Instant(20 * MS)
    assert message.source_stamp.numerator == 3 * MS
    assert k._store.frontiers["external"][1] == 20 * MS
    assert replay(k.journal.bytes).records == k.records
    k.close()


def test_exact_stop_adapter_integrates_to_3_ms_before_applying_the_input():
    from dataclasses import replace

    from aerokernel import Kernel, Timing

    sim = FakeLockstepSimulator(1)
    original, adapter = make_adapter_run(sim)
    adapter.partition = replace(
        adapter.partition, timing=Timing("lockstep", latch=False, exact_stop=True)
    )
    adapter.partitions = (adapter.partition,)
    k = Kernel()
    k.bind(original._store.registry, original._store.manifest, (adapter,))
    k.start()
    k.submit(
        CommandRequest("control", "external", Instant(3 * MS), 9, ingress_at_ns=3 * MS)
    )
    k.run_until(3 * MS)
    assert sim.calls == [(0, 3 * MS, 3 * MS)]
    assert sim.inputs == [(3 * MS, 9)]
    adopted = k._store.native_cuts["external"]
    k.run_until(20 * MS)
    assert sim.calls[-1] == (3 * MS, 20 * MS, 20 * MS)
    returns = [
        item
        for record in k.records
        for item in record["items"]
        if item["kind"] == "return" and item["native_interval"] == [3 * MS, 20 * MS]
    ]
    assert decode_record(returns[0]["native_input_cut"]) == adopted
    assert replay(k.journal.bytes).records == k.records
