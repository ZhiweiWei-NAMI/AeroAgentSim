"""Independent stream admission/closure oracles and offline replay properties."""

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerokernel import (
    BindingManifest,
    ClockMapping,
    CommandRequest,
    IngressPolicy,
    IngressReceipt,
    IngressStream,
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


class Target(SimpleEngine):
    def __init__(self, name):
        super().__init__(
            Partition(name, name, commands=("do",), timing=Timing("real_time"))
        )
        self.seen = []
        self.advanced = threading.Event()
        self.delivered = threading.Event()

    def advance(self, partition, to, view):
        batch = super().advance(partition, to, view)
        self.advanced.set()
        return batch

    def on_react(self, view, inbox, dirty):
        self.seen.extend((view.instant, d.message) for d in inbox)
        self.delivered.set()
        return ()


def make(*, slow_policy=None, fast_policy=None, provenance="full"):
    fast, slow = Target("fast"), Target("slow")
    streams = (
        IngressStream(
            "fast-stream", fast_policy or IngressPolicy(0), "fast-clock", ("fast",)
        ),
        IngressStream(
            "slow-stream",
            slow_policy or IngressPolicy(0, lateness="delay"),
            "slow-clock",
            ("slow",),
        ),
    )
    k = Kernel(
        provenance=provenance,
        ingress_streams=streams,
        mappings=(
            ClockMapping("fast-clock", "fast-native", offset_ns=10),
            ClockMapping("slow-clock", "slow-native", offset_ns=20),
        ),
    )
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
        ),
        BindingManifest("k4", "epoch"),
        (fast, slow),
    )
    k.start()
    return k, fast, slow


def source(stream, ns):
    return Stamp(
        stream + "-native", ns - (10 if stream == "fast" else 20), 1, stream + "-clock"
    )


def request(target, ns, value=1, key=None):
    return CommandRequest("do", target, Instant(max(0, ns)), value, key)


@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_slow_stream_does_not_block_fast_native_work_or_command_delivery(provenance):
    k, fast, slow = make(provenance=provenance)
    mid = k.submit_live(request("fast", 5), source("fast", 5), stream_id="fast-stream")
    k.advance_watermark(20, stream_id="fast-stream")
    errors = []

    def run():
        try:
            k.run_until(20)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert fast.delivered.wait(1)
        assert fast.logical.ns == 5 and slow.logical.ns == 0
        assert not slow.advanced.is_set()
        assert k._store.sealed_ns == 0
        with k._ingress_condition:
            restored = replay(k.journal.bytes)
            assert restored.incomplete and restored.records == k.records
        assert restored.ingress_receipt(mid) == k.ingress_receipt(mid)
    finally:
        k.advance_watermark(20, stream_id="slow-stream")
        worker.join(2)
    assert not worker.is_alive() and errors == []
    assert fast.logical.ns == slow.logical.ns == k._store.sealed_ns == 20
    assert fast.seen[0][1].id == mid
    assert replay(k.journal.bytes).records == k.records


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(("fast", "slow")),
            st.sampled_from(("input", "watermark", "progress")),
            st.integers(0, 25),
        ),
        max_size=35,
    )
)
def test_any_stream_interleaving_replays_identical_committed_history(operations):
    k, fast, slow = make(
        fast_policy=IngressPolicy(0, allowed_lateness_ns=3),
        slow_policy=IngressPolicy(0, lateness="delay", allowed_lateness_ns=5),
    )
    closures = {"fast": 0, "slow": 0}
    for stream, operation, ns in operations:
        sid = stream + "-stream"
        if operation == "input":
            receipt = k.admit_live(
                request(stream, ns, ns), source(stream, ns), stream_id=sid
            )
            expected = (
                "rejected"
                if stream == "fast" and ns <= closures[stream]
                else "delayed"
                if ns <= closures[stream]
                else "accepted"
            )
            assert receipt.disposition == expected
            assert receipt.code == ("LATE_INGRESS" if expected == "rejected" else None)
        else:
            closed = max(closures[stream], ns)
            if operation == "progress":
                bound = 3 if stream == "fast" else 5
                k.advance_source_progress(source(stream, closed + bound), stream_id=sid)
            else:
                k.advance_watermark(closed, stream_id=sid)
            closures[stream] = closed
        restored = replay(k.journal.bytes)
        assert restored.records == k.records
        assert dict(restored.ingress_watermarks) == dict(k.ingress_watermarks)
        assert restored._store.messages == k._store.messages
    for stream in closures:
        k.advance_watermark(40, stream_id=stream + "-stream")
    k.run_until(40)
    restored = replay(k.journal.bytes)
    assert restored.records == k.records and not restored.incomplete
    assert restored._store.messages == k._store.messages
    assert restored._store.actions.states == k._store.actions.states
    for record in k.records:
        if record["type"] == "seal":
            assert record["physical_ns"] <= min(k.ingress_watermarks.values())
    for engine in (fast, slow):
        assert all(
            message.source == "stream:" + engine.partition.id + "-stream"
            for _, message in engine.seen
        )


@given(st.integers(0, 30), st.integers(0, 20))
def test_explicit_source_progress_keeps_lateness_tail_open(progress, bound):
    k, _, _ = make(fast_policy=IngressPolicy(0, allowed_lateness_ns=bound))
    progress += bound
    k.advance_source_progress(source("fast", progress), stream_id="fast-stream")
    closed = progress - bound
    assert k.ingress_watermarks["fast-stream"] == closed
    rejected = k.admit_live(
        request("fast", closed), source("fast", closed), stream_id="fast-stream"
    )
    assert isinstance(rejected, IngressReceipt) and rejected.code == "LATE_INGRESS"
    if bound:
        admissible = k.admit_live(
            request("fast", progress), source("fast", progress), stream_id="fast-stream"
        )
        assert admissible.disposition == "accepted"
    assert k.ingress_watermarks["fast-stream"] == closed
    assert replay(k.journal.bytes).records == k.records


def test_stream_specific_mapping_binding_and_idempotency():
    k, _, _ = make()
    first = k.admit_live(
        request("fast", 3, key="same"), source("fast", 3), stream_id="fast-stream"
    )
    second = k.admit_live(
        request("slow", 3, key="same"), source("slow", 3), stream_id="slow-stream"
    )
    assert first.command_id != second.command_id
    cut = k.view().cut
    assert (
        k.admit_live(
            request("fast", 3, key="same"), source("fast", 3), stream_id="fast-stream"
        )
        == first
    )
    assert k.view().cut == cut
    with pytest.raises(KernelError, match="IDEMPOTENCY_CONFLICT"):
        k.admit_live(
            request("fast", 3, value=2, key="same"),
            source("fast", 3),
            stream_id="fast-stream",
        )
    for command, stamp, sid in (
        (request("slow", 4), source("fast", 4), "fast-stream"),
        (request("fast", 4), source("slow", 4), "fast-stream"),
        (request("fast", 4), source("fast", 4), "missing"),
    ):
        with pytest.raises(KernelError):
            k.submit_live(command, stamp, stream_id=sid)
        assert k.view().cut == cut
    rejection = k.admit_live(
        request("fast", 0, key="rejected"), source("fast", 0), stream_id="fast-stream"
    )
    cut = k.view().cut
    assert (
        k.admit_live(
            request("fast", 0, key="rejected"),
            source("fast", 0),
            stream_id="fast-stream",
        )
        == rejection
    )
    with pytest.raises(KernelError, match="LATE_INGRESS"):
        k.submit_live(
            request("fast", 0, key="rejected"),
            source("fast", 0),
            stream_id="fast-stream",
        )
    assert k.view().cut == cut
    with pytest.raises(KernelError, match="INGRESS_RECEIPT"):
        k.ingress_receipt("absent")
    assert replay(k.journal.bytes).ingress_receipt(first.command_id) == first


@pytest.mark.parametrize("bound", [-1, True, 1.5, "3"])
def test_invalid_lateness_bounds(bound):
    with pytest.raises(KernelError, match="INGRESS_LATENESS"):
        IngressPolicy(0, allowed_lateness_ns=bound)


@pytest.mark.parametrize(
    "stream",
    [
        lambda: IngressStream("s", None, "c", ("e",)),
        lambda: IngressStream("s", IngressPolicy(0), "c", ()),
        lambda: IngressStream("s", IngressPolicy(0), "c", ("e", "e")),
        lambda: IngressStream("s", IngressPolicy(0), "c", ["e"]),
    ],
)
def test_invalid_stream_declarations(stream):
    with pytest.raises(KernelError):
        stream()


def test_stream_preflight_configuration_and_binding():
    s = IngressStream("s", IngressPolicy(0), "canonical", ("e",))
    for kwargs in (
        {"ingress_streams": [s]},
        {"ingress_streams": (None,)},
        {"ingress_streams": (s, s)},
        {
            "ingress_policy": IngressPolicy(0),
            "ingress_streams": (replace(s, id="default"),),
        },
    ):
        with pytest.raises(KernelError):
            Kernel(provenance="full", **kwargs)
    for stream, engine_id in (
        (replace(s, mapping_id="absent"), "e"),
        (s, "other"),
        (replace(s, engine_ids=("other",)), "e"),
    ):
        k = Kernel(provenance="full", ingress_streams=(stream,))
        with pytest.raises(KernelError):
            k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (Target(engine_id),))
    k = Kernel(provenance="full")
    with pytest.raises(KernelError, match="RUN_STATE"):
        _ = k.ingress_watermarks
    with pytest.raises(KernelError, match="RUN_STATE"):
        k.ingress_receipt("absent")


def test_retrograde_progress_explicit_closure_and_immutable_watermark_view():
    k, _, _ = make(fast_policy=IngressPolicy(0, allowed_lateness_ns=5))
    k.advance_source_progress(source("fast", 10), stream_id="fast-stream")
    cut = k.view().cut
    k.advance_source_progress(source("fast", 10), stream_id="fast-stream")
    assert k.view().cut == cut
    with pytest.raises(TypeError):
        k.ingress_watermarks["fast-stream"] = 0
    for stamp in (source("fast", 9), source("slow", 10), "invalid"):
        with pytest.raises(KernelError):
            k.advance_source_progress(stamp, stream_id="fast-stream")
    k.advance_watermark(20, stream_id="fast-stream")
    assert k.ingress_watermarks["fast-stream"] == 20  # no second subtraction
    with pytest.raises(KernelError):
        k.advance_source_progress(source("fast", 24), stream_id="fast-stream")
    assert replay(k.journal.bytes).records == k.records


def test_replay_actual_unmodified_1_2_live_journal():
    data = (Path(__file__).parent / "fixtures/journal_1_2_live.jsonl").read_bytes()
    restored = replay(data)
    assert restored.header["minor"] == 2 and not restored.incomplete
    assert restored._store.sealed_ns == restored._store.watermark_ns == 5
    assert restored.records[3]["type"] == "live_ingress"
    assert restored.records[3]["disposition"] == "delayed"
    assert "allowed_lateness_ns" not in restored.header["ingress_policy"]["fields"]
    assert [json.loads(line) for line in data.splitlines()[1:]] == list(
        restored.records
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(watermark_ns=11),
        lambda r: r.update(progress_ns=16),
        lambda r: r.update(stream_id="slow-stream"),
        lambda r: r.update(stream_id="absent"),
        lambda r: r.update(
            instant={"$type": "Instant", "fields": {"ns": 1, "microstep": 0}}
        ),
    ],
)
def test_replay_rejects_forged_stream_progress(mutate):
    k, _, _ = make(fast_policy=IngressPolicy(0, allowed_lateness_ns=5))
    k.advance_source_progress(source("fast", 15), stream_id="fast-stream")
    lines = [json.loads(line) for line in k.journal.bytes.splitlines()]
    mutate(lines[-1])
    with pytest.raises((KernelError, ValueError)):
        replay(b"".join((json.dumps(line) + "\n").encode() for line in lines))


def assert_closure_trace(k):
    closed = {s.id: s.policy.initial_watermark_ns for s in k.ingress_streams.values()}
    for record in k.records:
        if record["type"] in {"watermark", "source_progress"}:
            assert record["watermark_ns"] >= closed[record["stream_id"]]
            closed[record["stream_id"]] = record["watermark_ns"]
        if record["type"] == "invocations" and record["phase"] != "reset":
            ns = record["instant"]["fields"]["ns"]
            for pid in record["selected"]:
                assert all(ns <= closed[s] for s in k._store.ingress_dependencies[pid])
        if record["type"] == "seal":
            assert record["physical_ns"] <= min(closed.values())


@given(st.integers(1, 20), st.integers(1, 20))
@settings(max_examples=30, deadline=None)
def test_closure_never_exceeds_min_for_multi_stream_engine(a, b):
    target = Target("target")
    k = Kernel(
        provenance="full",
        ingress_streams=(
            IngressStream("a", IngressPolicy(0), "canonical", ("target",)),
            IngressStream("b", IngressPolicy(0), "canonical", ("target",)),
        ),
    )
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (target,),
    )
    k.start()
    k.advance_watermark(a, stream_id="a")
    k.advance_watermark(b, stream_id="b")
    k.run_until(min(a, b))
    assert_closure_trace(k)
    assert k._store.sealed_ns == min(a, b)
    assert replay(k.journal.bytes).records == k.records


def test_sdk_live_ingress_handles_actual_source_progress_and_decisions():
    from aerokernel.sdk import LiveIngress

    k, _, _ = make()
    adapter = LiveIngress(k, "fast-stream")
    assert adapter.watermark_ns == 0
    receipt = adapter.admit(request("fast", 3), source("fast", 3))
    assert receipt.disposition == "accepted"
    mid = adapter.submit(request("fast", 4), source("fast", 4))
    assert k.ingress_receipt(mid).boundary_ns == 4
    adapter.advance_source_progress(source("fast", 5))
    adapter.advance_watermark(10)
    assert adapter.watermark_ns == 10
    with pytest.raises(KernelError, match="INGRESS_STREAM"):
        LiveIngress(k, "absent")
    assert replay(k.journal.bytes).records == k.records
    assert_closure_trace(k)


def test_dependency_streams_propagate_to_downstream_engine_and_timeout():
    from aerokernel import Dependency, FieldDescriptor, TypeDescriptor

    fast = SimpleEngine(
        Partition(
            "fast",
            "fast",
            consumes=(Dependency("f", lag_ns=1),),
            timing=Timing("real_time"),
        )
    )
    slow = SimpleEngine(
        Partition("slow", "slow", produces=("f",), timing=Timing("real_time"))
    )
    k = Kernel(
        provenance="full",
        ingress_streams=(
            IngressStream(
                "a", IngressPolicy(0, timeout_s=0.02), "canonical", ("fast",)
            ),
            IngressStream(
                "b", IngressPolicy(0, timeout_s=0.02), "canonical", ("slow",)
            ),
        ),
    )
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),), (FieldDescriptor("f", "T", {"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (fast, slow),
    )
    k.start()
    assert k._store.ingress_dependencies["fast"] == ("a", "b")
    k.advance_watermark(20, stream_id="a")
    with pytest.raises(KernelError, match="WATERMARK_TIMEOUT"):
        k.run_until(20)
    assert fast.logical.ns == slow.logical.ns == 0
    assert k._store.sealed_ns == 0
    assert replay(k.journal.bytes).records == k.records
    assert_closure_trace(k)


def test_per_stream_timeout_is_not_shortened_by_already_closed_stream(monkeypatch):
    k, _, _ = make(
        fast_policy=IngressPolicy(20, timeout_s=0.001),
        slow_policy=IngressPolicy(0, timeout_s=1),
    )
    now = [100.0]
    waits = []
    monkeypatch.setattr("time.monotonic", lambda: now[0])

    def wait(seconds):
        waits.append(seconds)
        now[0] += seconds
        k.advance_watermark(20, stream_id="slow-stream")

    monkeypatch.setattr(k._ingress_condition, "wait", wait)
    k.run_until(20)
    assert waits == [1.0]
    assert_closure_trace(k)
    assert replay(k.journal.bytes).records == k.records


def test_partial_pacing_uses_only_streams_affecting_selected_engine(monkeypatch):
    k, _, _ = make(
        fast_policy=IngressPolicy(20, speed_ratio=2),
        slow_policy=IngressPolicy(0, speed_ratio=0.5),
    )
    now = [100.0]
    waits = []
    k._pace_origin = now[0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])

    def wait(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(k._ingress_condition, "wait", wait)
    with k._ingress_condition:
        k._pace_to(100_000_000, ("fast",))
    assert sum(waits) == pytest.approx(0.05)


def test_open_slow_stream_admits_actual_input_after_fast_partial_commit():
    k, fast, slow = make(slow_policy=IngressPolicy(0))
    k.submit_live(request("fast", 20), source("fast", 20), stream_id="fast-stream")
    k.advance_watermark(20, stream_id="fast-stream")
    errors = []

    def run():
        try:
            k.run_until(20)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert fast.delivered.wait(1)
        with k._ingress_condition:
            assert fast.logical.ns == 20 and slow.logical.ns == 0
            receipt = k.admit_live(
                request("slow", 3), source("slow", 3), stream_id="slow-stream"
            )
            assert receipt.disposition == "accepted"
            assert receipt.mapped_ns == 3 and receipt.boundary_ns == 21
            assert receipt.delay_ns == 18
            assert replay(k.journal.bytes).records == k.records
    finally:
        k.advance_watermark(20, stream_id="slow-stream")
        worker.join(2)
    assert not worker.is_alive() and errors == []
    k.advance_watermark(30, stream_id="fast-stream")
    k.advance_watermark(30, stream_id="slow-stream")
    k.run_until(30)
    assert slow.seen[0][1].source_stamp == source("slow", 3)
    assert slow.seen[0][0].ns == 21
    assert_closure_trace(k)
    assert replay(k.journal.bytes).records == k.records


def test_independent_sample_cone_settles_before_unrelated_slow_stream():
    from tests.kernel.test_m2_sampling import setup

    old, evaluator, _ = setup()
    k = Kernel(
        provenance="full",
        ingress_streams=(
            IngressStream("a", IngressPolicy(20), "canonical", ("source",)),
            IngressStream("b", IngressPolicy(0), "canonical", ("slow",)),
        ),
    )
    engines = tuple(old._engines.values()) + (Target("slow"),)
    registry = MemoryRegistry.from_data(old._store.registry.to_data())
    # The slow target uses no commands in this registry.
    engines[-1].partition = Partition("slow", "slow", timing=Timing("real_time"))
    engines[-1].partitions = (engines[-1].partition,)
    k.bind(registry, old._store.manifest, engines)
    k.start()
    sampled = threading.Event()
    original = evaluator.on_react

    def react(view, inbox, dirty):
        result = original(view, inbox, dirty)
        sampled.set()
        return result

    evaluator.on_react = react
    errors = []

    def run():
        try:
            k.run_until(1)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert sampled.wait(1)
        with k._ingress_condition:
            assert k.view().sample_frames("parent")[-1].frame.physical_ns == 1
            assert k._store.frontiers["slow"][0].ns == 0
            assert k._store.sealed_ns == 0
            assert replay(k.journal.bytes).records == k.records
    finally:
        k.advance_watermark(1, stream_id="b")
        worker.join(2)
    assert not worker.is_alive() and errors == []
    assert_closure_trace(k)
    assert replay(k.journal.bytes).records == k.records


def test_atomic_cohort_requires_all_its_streams():
    fast, slow = Target("fast"), Target("slow")
    k = Kernel(
        provenance="full",
        ingress_streams=(
            IngressStream(
                "a", IngressPolicy(20, timeout_s=0.02), "canonical", ("fast",)
            ),
            IngressStream(
                "b", IngressPolicy(0, timeout_s=0.02), "canonical", ("slow",)
            ),
        ),
    )
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
        ),
        BindingManifest("r", "e", cohorts=(("fast", "slow"),)),
        (fast, slow),
    )
    k.start()
    assert (
        k._store.ingress_dependencies["fast"]
        == k._store.ingress_dependencies["slow"]
        == ("a", "b")
    )
    with pytest.raises(KernelError, match="WATERMARK_TIMEOUT"):
        k.run_until(5)
    assert not fast.advanced.is_set() and not slow.advanced.is_set()
    assert replay(k.journal.bytes).records == k.records


def test_default_stream_preserves_shared_offline_idempotency_conflicts():
    from tests.kernel.test_m2_ingress import make as make_default

    for live_first in (True, False):
        k, _ = make_default(IngressPolicy(0))
        command = request("target", 3, key="shared")
        stamp = Stamp("source", -7, 1, "source-v1")
        if live_first:
            mid = k.submit_live(command, stamp)
            assert k.submit_live(command, stamp) == mid
            with pytest.raises(KernelError, match="IDEMPOTENCY_CONFLICT"):
                k.submit(command)
        else:
            k.submit(command)
            with pytest.raises(KernelError, match="IDEMPOTENCY_CONFLICT"):
                k.submit_live(command, stamp)
        assert replay(k.journal.bytes).records == k.records


def test_replay_rejects_unissued_seal_instant():
    k, _, _ = make()
    lines = [json.loads(line) for line in k.journal.bytes.splitlines()]
    assert lines[-1]["type"] == "seal"
    lines[-1]["instant"]["fields"]["microstep"] += 1
    with pytest.raises(KernelError, match="JOURNAL_SEAL"):
        replay(b"".join((json.dumps(line) + "\n").encode() for line in lines))


def test_sampling_declared_upstream_and_command_receipts_propagate_streams():
    from aerokernel import SampleSpec

    source = SimpleEngine(Partition("source", "source", timing=Timing("real_time")))
    evaluator = SimpleEngine(Partition("eval", "eval"))
    spec = SampleSpec("scope", "eval", upstream=("source",))
    k = Kernel(
        provenance="full",
        ingress_streams=(
            IngressStream("s", IngressPolicy(0), "canonical", ("source",)),
        ),
    )
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e", samples=(spec,)),
        (source, evaluator),
    )
    assert k._store.ingress_dependencies["eval"] == ("s",)
    assert (
        replay(k.journal.bytes)._store.ingress_dependencies
        == k._store.ingress_dependencies
    )

    sender = SimpleEngine(
        Partition(
            "sender",
            "sender",
            emits=("do",),
            message_targets=("target",),
            timing=Timing("real_time"),
        )
    )
    target = SimpleEngine(
        Partition("target", "target", commands=("do",), timing=Timing("real_time"))
    )
    k = Kernel(
        provenance="full",
        ingress_streams=(
            IngressStream("a", IngressPolicy(0), "canonical", ("sender",)),
            IngressStream("b", IngressPolicy(0), "canonical", ("target",)),
        ),
    )
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (sender, target),
    )
    assert k._store.ingress_dependencies["sender"] == ("a", "b")
    assert k._store.ingress_dependencies["target"] == ("a", "b")
