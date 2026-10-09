"""Entered profile: no unknown-as-false, no first-true emission, pinned comparisons."""

from dataclasses import replace

import pytest

from aerokernel import (
    Cut,
    Instant,
    KernelError,
    Partition,
    SampleFrame,
    SampleSpec,
    replay,
)
from aerokernel.codec import decode_record, encode
from aerokernel.profiles.sampled import (
    EnteredEvaluator,
    Evaluation,
    SampledEvaluator,
    entered,
)
from tests.kernel.test_m2_sampling import setup


def frame(ns, value=False, **kwargs):
    return SampleFrame(
        "context",
        ns,
        Cut(ns, Instant(ns)),
        {},
        {},
        Evaluation("known", value).to_data(),
        {},
        **kwargs,
    )


def test_first_true_no_event_and_false_to_true_has_physical_crossing_interval():
    assert entered(frame(0, True), ()).value is None
    result = entered(frame(3, True), (frame(0), frame(2)))
    assert result.value is True and result.crossing == (2, 3)
    assert entered(frame(4, True), (frame(3, True),)).value is False
    assert entered(frame(4, False), (frame(3, True),)).value is False
    assert result.to_data()["crossing"] == [2, 3]


def test_unresolved_latest_is_not_skipped_to_older_false_or_coerced():
    unknown = replace(
        frame(2),
        result=Evaluation("unresolved", None, ("missing observation",), None).to_data(),
    )
    assert entered(frame(3, True), (frame(0), unknown)).status == "unresolved"
    assert entered(unknown, (frame(0),)).value is None
    assert entered(replace(frame(3), result={}), (frame(0),)).value is None
    assert (
        entered(
            replace(
                frame(3),
                result={
                    "status": "known",
                    "value": 1,
                    "diagnostics": [],
                    "applicability": True,
                },
            ),
            (frame(0),),
        ).status
        == "invalid"
    )
    assert (
        entered(
            replace(
                frame(3),
                result=Evaluation("known", True, applicability=False).to_data(),
            ),
            (frame(0),),
        ).value
        is None
    )


@pytest.mark.parametrize(
    "prior",
    [
        frame(3),
        frame(4),
        replace(frame(0), context_id="other"),
    ],
)
def test_strictly_earlier_same_context(prior):
    assert entered(frame(3, True), (prior,)).status == "invalid"
    assert entered(frame(3, True), (frame(0), frame(0))).status == "invalid"


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("bindings", {"role": "changed"}),
        ("sources", {"role": "changed"}),
        ("clocks", {"role": ("new", "mapping")}),
    ],
)
def test_source_identity_and_mapping_mismatches_are_invalid(attribute, value):
    assert (
        entered(replace(frame(3, True), **{attribute: value}), (frame(0),)).status
        == "invalid"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("unknown", None),
        ("known", None),
        ("known", 0),
        ("unresolved", False),
        ("known", True, (), 0),
    ],
)
def test_strict_evaluation_types(arguments):
    with pytest.raises(KernelError):
        Evaluation(*arguments)


def test_sdk_evaluator_records_known_and_unresolved_without_interpreting_in_core():
    k, old, _ = setup()

    class Evaluator(SampledEvaluator):
        def evaluate(self, ctx):
            value = ctx.get(next(iter(self.spec.bindings.values())), "y")
            return (
                Evaluation("known", value > 0)
                if ctx.now.ns == 0
                else Evaluation("unresolved", None, ("authored missing value",))
            )

    evaluator = Evaluator(old.partition, old.spec)
    engines = tuple(
        evaluator if engine is old else engine for engine in k._engines.values()
    )
    from aerokernel import Kernel

    live = Kernel(provenance="full")
    live.bind(k._store.registry, k._store.manifest, engines)
    live.start()
    live.run_until(1)
    rows = live.view().sample_frames("parent")
    assert rows[0].frame.result["value"] is False
    assert (
        rows[1].frame.result["status"] == "unresolved"
        and rows[1].frame.result["value"] is None
    )
    assert replay(live.journal.bytes).view().sample_frames("parent") == rows
    with pytest.raises(KernelError, match="SAMPLE_DECLARATION"):
        Evaluator(Partition("different", "different"), old.spec)
    with pytest.raises(NotImplementedError):
        SampledEvaluator(old.partition, old.spec).evaluate(None)


def test_entered_sdk_frame_results_and_opaque_role_keys():
    k, old, _ = setup()

    class Evaluator(EnteredEvaluator):
        def evaluate(self, ctx):
            return Evaluation(
                "known", ctx.get(next(iter(self.spec.bindings.values())), "y") > 0
            )

    evaluator = Evaluator(old.partition, old.spec)
    from aerokernel import Kernel

    live = Kernel(provenance="full")
    live.bind(
        k._store.registry,
        k._store.manifest,
        tuple(evaluator if e is old else e for e in k._engines.values()),
    )
    live.start()
    live.run_until(1)
    rows = live.view().sample_frames("parent")
    assert rows[0].frame.result["entered"]["value"] is None
    assert rows[1].frame.result["entered"]["crossing"] == (0, 1)
    assert replay(live.journal.bytes).records == live.records
    ref = next(iter(old.spec.bindings.values()))
    spec = SampleSpec(
        "c",
        "p",
        bindings={"$type": ref},
        sources={"$type": "p"},
        clocks={"$type": ("canonical", "canonical")},
    )
    assert decode_record(encode(spec)) == spec
    with pytest.raises(KernelError, match="SAMPLE_EVENT"):
        Evaluator(old.partition, old.spec, event_schema="event")


def test_entered_sdk_emits_one_event_citing_current_and_prior_frames():
    from aerokernel import Kernel, MemoryRegistry, MessageDescriptor

    k, old, _ = setup()

    class Evaluator(EnteredEvaluator):
        def evaluate(self, ctx):
            return Evaluation(
                "known", ctx.get(next(iter(self.spec.bindings.values())), "y") > 0
            )

    partition = replace(old.partition, emits=("entered",), message_targets=("entered",))
    evaluator = Evaluator(partition, old.spec, event_schema="entered", topic="entered")
    schema = {
        "type": "record",
        "members": {
            "context_id": {"type": "string"},
            "start_ns": {"type": "integer"},
            "end_ns": {"type": "integer"},
        },
        "required": ["context_id", "start_ns", "end_ns"],
        "extra": False,
    }
    registry = MemoryRegistry(
        k._store.registry.types,
        k._store.registry.fields,
        (MessageDescriptor("entered", schema=schema),),
    )
    live = Kernel(provenance="full")
    live.bind(
        registry,
        k._store.manifest,
        tuple(evaluator if e is old else e for e in k._engines.values()),
    )
    live.start()
    assert live._store.messages == {}
    live.run_until(1)
    event = next(iter(live._store.messages.values()))
    assert event.payload == {"context_id": "parent", "start_ns": 0, "end_ns": 1}
    frames = live.view().sample_frames("parent")
    assert frames[-1].version in event.causes
    assert frames[0].version in event.causes
    assert replay(live.journal.bytes).records == live.records
