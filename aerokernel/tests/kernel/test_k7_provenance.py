"""Lean invocation provenance preserves functional authority and exact replay."""

from dataclasses import replace

import pytest

from aerokernel import ItemRef, Journal, Kernel, KernelError, Receipt, replay
from aerokernel.codec import decode_record
from aerokernel.rpc import _projection, _view
from aerokernel.sdk import EngineContext
from aerokernel.transactions import item_at
from examples.two_engine_toy import MS, make_toy
from tests.kernel.test_m2_sampling import setup


def test_default_and_invalid_levels():
    assert Kernel().provenance == "lean"
    for level in ("unknown", None, []):
        with pytest.raises(KernelError, match="PROVENANCE"):
            Kernel(provenance=level)


@pytest.mark.parametrize("mode", ["lean", "full"])
@pytest.mark.parametrize("codec", ["json", "positional-deflate"])
def test_state_receipts_time_and_reexecution(mode, codec):
    def run():
        kernel = make_toy(provenance=mode)
        # Rebind rather than replacing the already admitted header's codec.
        other = Kernel(
            provenance=mode,
            journal=Journal(codec=codec),
            root_seed=42,
            configuration=dict(kernel.configuration),
        )
        other.bind(
            kernel._store.registry,
            kernel._store.manifest,
            tuple(kernel._engines.values()),
        )
        other.start()
        other.run_until(13 * MS)
        return other

    kernel = run()
    assert run().journal.bytes == kernel.journal.bytes
    recovered = replay(kernel.journal.bytes)
    assert recovered.records == kernel.records
    assert recovered.provenance == mode
    assert recovered.view().cut == kernel.view().cut
    assert {k: tuple(v) for k, v in recovered._store.facts.items()} == {
        k: tuple(v) for k, v in kernel._store.facts.items()
    }
    assert recovered._store.messages == kernel._store.messages
    assert recovered._store.actions.to_data() == kernel._store.actions.to_data()
    if mode == "lean":
        assert not kernel._store.pending_intents
        assert len(kernel._store.intents) == len(kernel._store.latest_returned)
        assert len(kernel._store.intents) <= 4 * len(kernel._store.partitions)
        assert all(
            set(intent) == {"partition", "status", "operation_refs"}
            for intent in kernel._store.intents.values()
        )
        for record in kernel.records:
            for item in record.get("items", ()):
                if item.get("kind") != "operation":
                    continue
                causes = decode_record(item["causes"])
                assert len(causes) == 1
                invocation = item_at(kernel._store, causes[0])
                assert invocation["kind"] == "intent"
                assert invocation["partition"] == item["partition"]
                proposal = decode_record(item["proposal"])
                if hasattr(proposal, "causes"):
                    assert proposal.causes == ()


def test_lean_discards_invalid_vectors_without_resolving_them(monkeypatch):
    kernel = make_toy(provenance="lean")
    engine = kernel._engines["mover"]
    initialize = engine.initialize

    def polluted(view):
        return tuple(
            replace(op, causes=(ItemRef(999999, 0),)) if hasattr(op, "causes") else op
            for op in initialize(view)
        )

    engine.initialize = polluted

    def forbidden(*args, **kwargs):
        raise AssertionError("lean expanded a read vector")

    monkeypatch.setattr("aerokernel.transactions.cause_at", forbidden)
    kernel.start()
    assert replay(kernel.journal.bytes).view().cut == kernel.view().cut
    full = make_toy(provenance="full")
    full._engines["mover"].initialize = polluted
    with pytest.raises(KernelError, match="CAUSE_FUTURE"):
        full.start()


def test_lean_operation_reference_retention_is_bounded_and_views_stay_immutable():
    from aerokernel import LocalCause

    kernel = make_toy(provenance="lean")
    kernel.start()
    kernel.run_until(5 * MS)
    snapshot = kernel.view()
    invocation = kernel._store.latest_returned[("mover", "advance")]
    publication = snapshot.committed_operation(invocation, LocalCause(0))
    kernel.run_until(13 * MS)
    assert snapshot.committed_operation(invocation, LocalCause(0)) == publication
    with pytest.raises(KernelError, match="CAUSE_UNKNOWN"):
        kernel.view().committed_operation(invocation, LocalCause(0))
    assert len(kernel._store.intents) <= 4 * len(kernel._store.partitions)
    restored = replay(kernel.journal.bytes)
    assert restored._store.latest_returned == kernel._store.latest_returned


def test_lean_receipts_require_real_dispatch_and_correct_target():
    kernel = make_toy(provenance="lean")
    kernel.start()
    actions = kernel._store.actions.clone()
    command_id = next(iter(actions.states))
    with pytest.raises(KernelError, match="ACTION_TARGET"):
        actions.receipt(
            "wrong-target", Receipt(command_id, "accepted"), ItemRef(99, 0), (), ()
        )
    # Target authorization cannot manufacture an undispatched command.
    actions.dispatches.clear()
    with pytest.raises(KernelError, match="ACTION_DISPATCH"):
        actions.receipt(
            actions.states[command_id].target,
            Receipt(command_id, "accepted"),
            ItemRef(99, 0),
            (),
            (),
        )


@pytest.mark.parametrize("mode", ["lean", "full"])
def test_sample_sdk_and_rpc_projection(mode):
    kernel, evaluator, _ = setup(provenance=mode)
    kernel.start()
    kernel.run_until(1)
    frame = kernel.view().sample_frames("parent")[-1]
    if mode == "lean":
        assert item_at(kernel._store, frame.frame.causes[0])["kind"] == "intent"
    view = kernel.view()
    projected = _view(_projection(view, (evaluator.partition,)), kernel.budget)
    assert projected.provenance == mode
    context = EngineContext(projected)
    context.inputs.append(ItemRef(999999, 0))
    assert context._causes() == (() if mode == "lean" else (ItemRef(999999, 0),))
    assert replay(kernel.journal.bytes).view().sample_frames("parent") == (
        kernel.view().sample_frames("parent")
    )


@pytest.mark.parametrize("codec", ["json", "positional-deflate"])
def test_lean_header_admission(codec):
    from aerokernel.journal import read_records
    from aerokernel.values import canonical_json

    toy = make_toy(provenance="lean")
    kernel = Kernel(journal=Journal(codec=codec))
    kernel.bind(toy._store.registry, toy._store.manifest, tuple(toy._engines.values()))
    header = read_records(kernel.journal.bytes)[0][0]
    for field, value in (
        ("provenance", "full"),
        ("policy_version", True),
        ("minor", 7),
    ):
        with pytest.raises(KernelError, match="JOURNAL_HEADER"):
            replay(canonical_json({**header, field: value}))


def test_lean_named_ingress_closure_and_replay():
    from tests.kernel.test_k4_streams import make, request, source

    kernel, fast, slow = make(provenance="lean")
    receipt = kernel.submit_live(
        request("fast", 5), source("fast", 5), stream_id="fast-stream"
    )
    kernel.advance_watermark(20, stream_id="fast-stream")
    kernel.advance_watermark(20, stream_id="slow-stream")
    kernel.run_until(20)
    assert fast.seen[0][1].id == receipt
    assert not slow.seen
    assert kernel._store.sealed_ns == 20
    recovered = replay(kernel.journal.bytes)
    assert recovered._store.watermarks == kernel._store.watermarks
    assert recovered._store.sealed_ns == 20
    assert recovered._store.ingress_receipts == kernel._store.ingress_receipts


@pytest.mark.parametrize("codec", ["json", "positional-deflate"])
def test_lean_payload_depth_budget_replays_with_record_wrappers(codec):
    from aerokernel import FactWrite, ResourceBudget
    from aerokernel.values import thaw
    from tests.kernel.test_k3_causal_lookup import fact_kernel

    bound, refs = fact_kernel()
    engine = bound._engines["p"]
    initialize = engine.initialize
    value = 0
    for _ in range(30):
        value = [value]

    def deep(view):
        return tuple(
            replace(op, value={"nested": value}) if isinstance(op, FactWrite) else op
            for op in initialize(view)
        )

    engine.initialize = deep
    budget = ResourceBudget(nesting_depth=32)
    kernel = Kernel(budget=budget, journal=Journal(codec=codec, budget=budget))
    kernel.bind(bound._store.registry, bound._store.manifest, (engine,))
    kernel.start()
    recovered = replay(kernel.journal.bytes)
    for source in (kernel, recovered):
        assert thaw(
            source.view().field((refs[0], "x"), source.view().cut.instant).value
        ) == {"nested": value}


@pytest.mark.parametrize("input_kind", ["mapped_stamp", "run_limit"])
def test_lean_checks_new_metadata_integer_bounds(input_kind):
    from aerokernel import ClockMapping, FactWrite, ResourceBudget, ResourceLimit, Stamp
    from tests.kernel.test_k3_causal_lookup import fact_kernel

    bound, _ = fact_kernel()
    engine = bound._engines["p"]
    if input_kind == "mapped_stamp":
        initialize = engine.initialize

        def distant(view):
            return tuple(
                replace(op, acquired=Stamp("canonical", -1000000, 1, "canonical"))
                if isinstance(op, FactWrite)
                else op
                for op in initialize(view)
            )

        engine.initialize = distant
    kernel = Kernel(
        budget=ResourceBudget(integer_digits=7),
        mappings=(ClockMapping("canonical", "canonical", p=1000000),),
    )
    kernel.bind(bound._store.registry, bound._store.manifest, (engine,))
    if input_kind == "mapped_stamp":
        with pytest.raises(ResourceLimit):
            kernel.start()
        assert not kernel._store.facts
    else:
        kernel.start()
        before = kernel.view().cut
        with pytest.raises(ResourceLimit):
            kernel.run_until(10000000)
        assert replay(kernel.journal.bytes).view().cut == before
