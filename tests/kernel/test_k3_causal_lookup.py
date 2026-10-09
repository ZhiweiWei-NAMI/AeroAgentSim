"""Bounded causal lookup, divergent prefixes and lossless operation references."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from aerokernel import (
    BindingManifest,
    BindingRule,
    Create,
    Cut,
    Dependency,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    Partition,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.codec import encode
from aerokernel.compact import compact_operations, expand_item, expand_record
from aerokernel.sdk import SimpleEngine
from aerokernel.storage import RecordLog
from aerokernel.transactions import Candidate, item_at
from aerokernel.values import canonical_json, parse_json
from examples.two_engine_toy import MS, make_toy


def fact_kernel():
    refs = tuple(EntityRef("r", "e", str(i), 0, "T") for i in range(20))

    class Writer(SimpleEngine):
        def initialize(self, view):
            return tuple(Create(ref) for ref in refs) + tuple(
                FactWrite(
                    (ref, "x"),
                    {"nested": [i]},
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), None),
                )
                for i, ref in enumerate(refs)
            )

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),),
            (
                FieldDescriptor(
                    "x",
                    "T",
                    {
                        "type": "record",
                        "members": {},
                        "required": [],
                        "extra": True,
                    },
                ),
            ),
        ),
        BindingManifest(
            "r",
            "e",
            refs,
            rules=(BindingRule("p", "T", ("x",)),),
            lifecycle=(LifecycleRule("p", "T"),),
        ),
        (Writer(Partition("p", "p", produces=("x",), lifecycle=True)),),
    )
    k.start()
    return k, refs


def test_repeated_causes_parse_once_expand_only_requested_item_and_detach(monkeypatch):
    k, refs = fact_kernel()
    calls = []
    expansions = []
    original_parse = parse_json
    original_expand = expand_item

    def counted_parse(*args, **kwargs):
        calls.append(args[0])
        return original_parse(*args, **kwargs)

    def counted_expand(record, index):
        expansions.append(index)
        return original_expand(record, index)

    monkeypatch.setattr("aerokernel.values.parse_json", counted_parse)
    monkeypatch.setattr("aerokernel.compact.expand_item", counted_expand)
    version = k.view().field((refs[0], "x"), Instant(0)).version
    expected = item_at(k._store, version)
    for _ in range(50):
        actual = item_at(k._store, version)
        assert actual == expected
        actual["proposal"]["fields"]["value"]["nested"].append(99)
    assert len(calls) == 1
    assert expansions == [version.item_index] * 51
    diagnostics = k._store.records[version.record_index - 1]
    diagnostics["items"][version.item_index]["proposal"]["fields"]["value"] = None
    assert item_at(k._store, version) == expected


def test_cache_is_bounded_and_different_suffixes_never_alias():
    log = RecordLog()
    for i in range(12):
        log.append({"items": [{"value": i}]})
        log.freeze_tail(canonical_json({"items": [{"value": i}]}))
    for i in range(12):
        assert log.item(i, 0) == {"value": i}
    assert len(log._decoded) == 8
    before = log.fork()
    abandoned = before.fork()
    abandoned.append({"items": [{"value": "abandoned"}]})
    abandoned.freeze_tail(canonical_json({"items": [{"value": "abandoned"}]}))
    assert abandoned.item(12, 0)["value"] == "abandoned"
    replacement = before.fork()
    replacement.append({"items": [{"value": "replacement"}]})
    replacement.freeze_tail(canonical_json({"items": [{"value": "replacement"}]}))
    assert replacement.item(12, 0)["value"] == "replacement"
    assert abandoned.item(12, 0)["value"] == "abandoned"
    assert len(before) == 12
    with pytest.raises(IndexError):
        before.item(12, 0)
    with pytest.raises(IndexError):
        before.item(0, -1)
    # A refrozen line at the same coordinate is a different cache entry too.
    replacement.freeze_tail(canonical_json({"items": [{"value": "refrozen"}]}))
    assert replacement.item(12, 0)["value"] == "refrozen"
    assert replacement[-1] == replacement[12]
    assert len(replacement[:]) == 13


@pytest.mark.parametrize("ref", (ItemRef(0, 0), ItemRef(999, 0), ItemRef(1, 999)))
def test_cached_lookup_still_rejects_unknown_coordinates(ref):
    k, _ = fact_kernel()
    with pytest.raises(KernelError, match="CAUSE_UNKNOWN"):
        item_at(k._store, ref)


def test_compact_operations_preserve_full_proposals_and_replay_historical_encoding():
    k, _ = fact_kernel()
    lines = [parse_json(line) for line in k.journal.bytes.splitlines(keepends=True)]
    transaction = next(
        record for record in lines if record.get("type") == "transaction"
    )
    expanded = expand_record(transaction)
    again = compact_operations(transaction)
    assert again == transaction and expand_record(again) == expanded
    assert all(
        set(op) == {"$item"}
        for op in transaction["batches"][0]["batch"]["fields"]["operations"]
    )
    # Fully expanded historical records retain their exact closed semantics.
    historical = b"".join(canonical_json(expand_record(record)) for record in lines)
    assert replay(historical).records == k.records
    assert replay(k.journal.bytes).records == k.records
    bad = deepcopy(lines)
    tx = next(record for record in bad if record.get("type") == "transaction")
    operations = tx["batches"][0]["batch"]["fields"]["operations"]
    operations[0], operations[1] = operations[1], operations[0]
    with pytest.raises(KernelError):
        replay(b"".join(canonical_json(record) for record in bad))


def test_original_toy_wal_replays_identical_complete_causality_and_state():
    previous = (Path(__file__).parent / "fixtures" / "k3-before-toy.jsonl").read_bytes()
    assert hashlib.sha256(previous).hexdigest() == (
        "1b75a0cc904cd3833d7357a4eddb44ad1a18c34bcee4bf851fcb5e0b57f7413d"
    )
    current = make_toy()
    current.start()
    current.run_until(13 * MS)
    assert len(current.journal.bytes) < len(previous)
    assert replay(previous).records == current.records
    assert replay(current.journal.bytes).records == current.records


def test_successful_cause_validation_cache_cannot_cross_invocation_read_cuts():
    k, refs = fact_kernel()
    version = k.view().field((refs[0], "x"), Instant(0)).version
    candidate = k._candidate(Instant(0, 10))
    intent = {
        "partition": "p",
        "read_cut": encode(k.view().cut),
        "transaction_base_cut": encode(k.view().cut),
        "authorized": [],
    }
    assert candidate.cause_refs((version, version), [], intent) == (version, version)
    assert candidate.cause_refs((version,), [], intent) == (version,)
    older = {
        **intent,
        "read_cut": encode(Cut(0, Instant(0))),
        "transaction_base_cut": encode(Cut(0, Instant(0))),
    }
    with pytest.raises(KernelError, match="CAUSE_FUTURE"):
        candidate.cause_refs((version,), [], older)


def test_warm_record_cache_does_not_grant_other_partitions_state_scope_or_lag():
    k, refs = fact_kernel()
    version = k.view().field((refs[0], "x"), Instant(0)).version
    state = k._store.clone()
    state.partitions = {
        **state.partitions,
        "lagged": Partition("lagged", "lagged", consumes=(Dependency("x", lag_ns=1),)),
        "unscoped": Partition("unscoped", "unscoped"),
    }
    candidate = Candidate(
        state, Instant(0, 10), state.cut.index + 1, k.mappings, k.budget
    )
    common = {
        "read_cut": encode(state.cut),
        "transaction_base_cut": encode(state.cut),
        "authorized": [],
    }
    assert candidate.cause_refs((version,), [], {**common, "partition": "p"}) == (
        version,
    )
    for partition, code in (
        ("lagged", "CAUSE_STATE_LAG"),
        ("unscoped", "CAUSE_STATE_SCOPE"),
    ):
        intent = {**common, "partition": partition}
        for _ in range(2):
            with pytest.raises(KernelError, match=code):
                candidate.cause_refs((version,), [], intent)
