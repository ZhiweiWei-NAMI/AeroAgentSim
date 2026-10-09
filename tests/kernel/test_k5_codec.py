"""Lossless codec admission, stable bytes and exact sampled-prefix causes."""

import base64
import json
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from aerokernel import (
    FramePrefix,
    ItemRef,
    Journal,
    Kernel,
    KernelError,
    ResourceLimit,
    replay,
)
from aerokernel.codec import encode
from aerokernel.journal import read_records
from aerokernel.journal_codec import CODEC, decode_frame, encode_frame
from aerokernel.values import ResourceBudget, canonical_json, parse_json
from examples.two_engine_toy import MS, make_toy
from tests.kernel.test_m2_sampling import setup


def test_codec_preserves_portable_tag_collisions_types_and_duplicate_causes():
    budget = ResourceBudget()
    record = {
        "type": "test",
        "index": 1,
        "payload": {"$type": ["ItemRef"], "fields": None, "x": [None, False, 0, 0.0]},
        "causes": encode((ItemRef(1, 2), ItemRef(1, 2))),
    }
    first = encode_frame(record, budget)
    assert first == encode_frame(record, budget)
    assert canonical_json(decode_frame(parse_json(first), budget)) == canonical_json(
        record
    )
    wire = parse_json(first)
    wire["index"] = 2
    with pytest.raises(KernelError, match="JOURNAL_CODEC"):
        decode_frame(wire, budget)
    wire["data"] = "invalid!"
    with pytest.raises(KernelError, match="JOURNAL_CODEC"):
        decode_frame(wire, budget)


def test_v2_live_execution_replay_and_legacy_fixture(tmp_path):
    bound = make_toy()
    k = Kernel(journal=Journal(tmp_path / "run.jsonl", codec="positional-deflate"))
    k.bind(bound._store.registry, bound._store.manifest, bound._engines.values())
    k.start()
    k.run_until(13 * MS)
    legacy = make_toy()
    legacy.start()
    legacy.run_until(13 * MS)
    records, _ = read_records(legacy.journal.bytes)
    header = dict(records[0], major=2, minor=0, codec=CODEC, semantic_version=2)
    data = canonical_json(header) + b"".join(
        encode_frame(r, k.budget) for r in records[1:]
    )
    result = replay(data)
    assert read_records(data)[0][1:] == read_records(legacy.journal.bytes)[0][1:]
    assert tuple(result.iter_records()) == legacy.records
    assert k.records == legacy.records == replay(k.journal.bytes).records
    assert k.header["major"] == 2
    assert (
        replay(Path(__file__).parent / "fixtures/journal_1_2_live.jsonl").header[
            "minor"
        ]
        == 2
    )
    header["codec"] = "unknown"
    with pytest.raises(KernelError, match="JOURNAL_HEADER"):
        replay(canonical_json(header))
    with pytest.raises(KernelError, match="JOURNAL_HEADER"):
        read_records(canonical_json(header))


def test_frame_prefix_expands_in_place_preserving_order_and_repetition():
    existing, evaluator, _ = setup()
    kernel = Kernel(journal=Journal(codec="positional-deflate"))
    kernel.bind(
        existing._store.registry, existing._store.manifest, existing._engines.values()
    )
    original = evaluator.on_react

    def with_prefix(view, inbox, dirty):
        (frame,) = original(view, inbox, dirty)
        prefix = view.sample_frame_prefix("parent")
        return (replace(frame, causes=(prefix, prefix)),)

    evaluator.on_react = with_prefix
    kernel.start()
    old = kernel.view()
    kernel.run_until(1)
    frames = kernel.view().sample_frames("parent")
    assert old.sample_frame_prefix("parent").count == 1
    assert kernel.view().sample_frame_prefix("parent").through == frames[-1].version
    operation = kernel._store.records.item(
        frames[-1].version.record_index - 1, frames[-1].version.item_index
    )
    assert operation["causes"] == encode((frames[0].version, frames[0].version))
    assert replay(kernel.journal.bytes).records == kernel.records
    with pytest.raises(KernelError, match="CAUSE_PREFIX"):
        FramePrefix("parent", 1, None)


@pytest.mark.parametrize(
    "packed",
    [[], [False], [1, ["x", 1], ["x", 2]], [2, "unknown"], [2, "ItemRef", 1], [3]],
)
def test_corrupt_positional_containers_are_rejected(packed):
    wire = {
        "type": "test",
        "index": 1,
        "data": base64.b64encode(zlib.compress(json.dumps(packed).encode())).decode(),
    }
    with pytest.raises(KernelError, match="JOURNAL_CODEC"):
        decode_frame(wire, ResourceBudget())


def test_compression_and_expansion_budgets_and_boundaries():
    budget = ResourceBudget(frame_bytes=512)
    with pytest.raises(ResourceLimit):
        encode_frame({"type": "test", "index": 1, "data": "x" * 600}, budget)
    with pytest.raises(ResourceLimit):
        encode_frame({"type": "test", "index": 1}, ResourceBudget(frame_bytes=70))
    with pytest.raises(KernelError, match="JOURNAL_CODEC"):
        Journal(codec="unknown")
    valid = parse_json(encode_frame({"type": "test", "index": 1}, budget))
    for data in (
        base64.b64encode(zlib.compress(b"x" * 600)),
        base64.b64encode(zlib.compress(b"[]") + b"suffix"),
        base64.b64encode(b"invalid"),
    ):
        wire = {**valid, "data": data.decode()}
        with pytest.raises(KernelError):
            decode_frame(wire, budget)
    for wire in ({**valid, "data": 3}, {**valid, "extra": None}):
        with pytest.raises(KernelError, match="JOURNAL_CODEC"):
            decode_frame(wire, budget)


def test_file_prefix_detects_corruption_and_keeps_divergent_cause_indexes(tmp_path):
    from aerokernel.storage import RecordLog

    first = {
        "type": "test",
        "index": 1,
        "items": [{"kind": "receipt", "command_id": "a"}],
    }
    log = RecordLog(budget=ResourceBudget())
    log.append(first)
    line = canonical_json(first)
    log.freeze_tail(line)
    source = tmp_path / "prefix.jsonl"
    source.write_bytes(line)
    log.file_tail(source, 0, line)
    old = log.fork()
    other = old.fork()
    for branch, command in ((log, "b"), (other, "c")):
        record = {"index": 2, "items": [{"kind": "receipt", "command_id": command}]}
        branch.append(record)
        branch.freeze_tail(canonical_json(record))
    assert log.cause_item(1, 0)["command_id"] == "b"
    assert other.cause_item(1, 0)["command_id"] == "c"
    assert len(old) == 1
    with pytest.raises(IndexError):
        old.cause_item(0, 1)
    source.write_bytes(line.replace(b'"a"', b'"z"'))
    with pytest.raises(KernelError, match="JOURNAL_CHANGED"):
        old[0]


@pytest.mark.parametrize("cause", [ItemRef(0, 0), ItemRef(1, 999)])
def test_authority_lookup_rejects_unknown_coordinates(cause):
    from aerokernel.transactions import cause_at

    kernel = make_toy()
    kernel.start()
    with pytest.raises(KernelError, match="CAUSE_UNKNOWN"):
        cause_at(kernel._store, cause)


@pytest.mark.parametrize(
    "prefix",
    [
        FramePrefix("parent", 1, ItemRef(1, 999)),
        FramePrefix("parent", 999, ItemRef(1, 0)),
    ],
)
def test_forged_frame_prefix_boundary_is_rejected(prefix):
    from aerokernel.codec import encode

    existing, _, _ = setup()
    kernel = Kernel(journal=Journal(codec="positional-deflate"))
    kernel.bind(
        existing._store.registry, existing._store.manifest, existing._engines.values()
    )
    kernel.start()
    candidate = kernel._candidate(kernel.view().instant)
    intent = {
        "partition": "eval",
        "read_cut": encode(kernel.view().cut),
        "authorized": [],
    }
    with pytest.raises(KernelError, match="CAUSE_PREFIX"):
        candidate.cause_refs((prefix,), [], intent)
    candidate.before.allow_frame_prefix = False
    with pytest.raises(KernelError, match="CAUSE_PREFIX"):
        candidate.cause_refs((prefix,), [], intent)


def test_fact_only_returns_can_be_causes_of_the_next_owned_write():
    from aerokernel import (
        BindingManifest,
        BindingRule,
        Create,
        EntityRef,
        FactWrite,
        FieldDescriptor,
        Interval,
        LifecycleRule,
        MemoryRegistry,
        Partition,
        Stamp,
        Timing,
        TypeDescriptor,
    )
    from aerokernel.sdk import SimpleEngine

    ref = EntityRef("r", "e", "subject", 0, "T")

    class Writer(SimpleEngine):
        def write(self, view, causes=()):
            return FactWrite(
                (ref, "x"),
                view.instant.ns,
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
                causes,
            )

        def initialize(self, view):
            return (Create(ref), self.write(view))

        def integrate(self, view):
            return (self.write(view, (view.field((ref, "x"), view.instant).version,)),)

    engine = Writer(
        Partition(
            "p", "p", produces=("x",), lifecycle=True, timing=Timing("fixed_step", 1)
        )
    )
    kernel = Kernel()
    kernel.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
        ),
        BindingManifest(
            "r",
            "e",
            (ref,),
            rules=(BindingRule("p", "T", ("x",)),),
            lifecycle=(LifecycleRule("p", "T"),),
        ),
        (engine,),
    )
    kernel.start()
    kernel.run_until(2)
    assert kernel.view().field((ref, "x"), kernel.view().instant).value == 2
    assert replay(kernel.journal.bytes).records == kernel.records
