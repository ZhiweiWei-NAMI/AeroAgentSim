"""I12: replay validates complete semantic records and never executes model code."""

from __future__ import annotations

import copy
import random
import time

import pytest

from aerokernel import (
    BindingManifest,
    CommandRequest,
    Instant,
    Kernel,
    KernelError,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    ResourceBudget,
    replay,
)
from aerokernel.compact import expand_record
from aerokernel.sdk import SimpleEngine
from aerokernel.values import canonical_json, parse_json
from examples.two_engine_toy import MS, make_toy


def records(data):
    return [expand_record(parse_json(line)) for line in data.splitlines(keepends=True)]


def wire(records):
    return b"".join(canonical_json(r) for r in records)


def test_replay_does_not_call_engines_evaluators_rng_or_live_clock(monkeypatch):
    k = make_toy()
    k.start()
    k.run_until(13 * MS)

    def forbidden(*args, **kwargs):
        raise AssertionError("plugin/RNG executed during replay")

    for method in ("reset", "advance", "react", "horizon"):
        monkeypatch.setattr(SimpleEngine, method, forbidden)
    monkeypatch.setattr(random, "Random", forbidden)
    for name in ("time", "monotonic", "perf_counter", "time_ns", "monotonic_ns"):
        monkeypatch.setattr(time, name, forbidden)
    result = replay(k.journal.bytes)
    assert result._store.actions.to_data() == k._store.actions.to_data()
    assert result._store.messages == k._store.messages
    assert not result.incomplete


@pytest.mark.parametrize(
    "field,value",
    [
        ("major", True),
        ("minor", 9),
        ("type", "wrong"),
        ("registry_digest", "corrupt"),
        ("resolved_bindings", []),
        ("engine_versions", {}),
        (
            "rng",
            {"implementation": "random.Random/MT19937", "python": "3.11", "seeds": {}},
        ),
        ("serializer", {"encoder": "custom"}),
        ("epoch", "other"),
    ],
)
def test_corrupt_complete_header_never_skipped(field, value):
    k = make_toy()
    k.start()
    data = records(k.journal.bytes)
    data[0][field] = value
    with pytest.raises(KernelError):
        replay(wire(data))


def test_wrong_effect_wrong_phase_overlap_omitted_return_and_seal_rejected():
    k = make_toy()
    k.start()
    k.run_until(13 * MS)
    original = records(k.journal.bytes)
    transaction = next(i for i, r in enumerate(original) if r["type"] == "transaction")
    data = copy.deepcopy(original)
    data[transaction]["items"].pop()
    with pytest.raises(KernelError):
        replay(wire(data))
    invocation = next(i for i, r in enumerate(original) if r["type"] == "invocations")
    data = copy.deepcopy(original)
    data[invocation]["phase"] = "unknown"
    with pytest.raises(KernelError):
        replay(wire(data))
    data = copy.deepcopy(original[: invocation + 1])
    duplicate = copy.deepcopy(data[-1])
    duplicate["index"] += 1
    data.append(duplicate)
    with pytest.raises(KernelError):
        replay(wire(data))
    data = copy.deepcopy(original[: invocation + 1])
    data.append(
        {
            "type": "seal",
            "index": data[-1]["index"] + 1,
            "instant": data[-1]["instant"],
            "physical_ns": 0,
            "items": [],
        }
    )
    with pytest.raises(KernelError):
        replay(wire(data))
    data = copy.deepcopy(original)
    data[transaction]["index"] = True
    with pytest.raises(KernelError):
        replay(wire(data))


def test_pending_run_control_and_dispatch_prefix_are_marked_incomplete():
    k = make_toy()
    k.start()
    k.run_until(13 * MS)
    lines = k.journal.bytes.splitlines(keepends=True)
    for i, r in enumerate(records(k.journal.bytes)):
        if r["type"] in {"run_limit", "invocations"}:
            assert replay(b"".join(lines[: i + 1])).incomplete
    with pytest.raises(KernelError):
        replay(k.journal.bytes + b"bad\n", recover_truncated=True)
    assert replay(k.journal.bytes + b'{"partial":', recover_truncated=True).incomplete


def test_custom_integer_budget_remains_lossless_in_live_message_and_replay():
    budget = ResourceBudget(integer_digits=4200)
    value = 10**4100
    engine = SimpleEngine(
        Partition("p", "e", commands=("do",), rng_streams=("stream",))
    )
    k = Kernel(provenance="full", budget=budget)
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (engine,),
    )
    k.start()
    mid = k.submit(CommandRequest("do", "p", Instant(1), value))
    k.run_until(1)
    assert k._store.messages[mid].payload == value
    assert replay(k.journal.bytes)._store.messages[mid].payload == value


def test_malformed_reset_return_is_recorded_as_returned_unpublished():
    class Malformed(SimpleEngine):
        def reset(self, context, view):
            return ()

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(()), BindingManifest("r", "e"), (Malformed(Partition("p", "e")),)
    )
    with pytest.raises(KernelError, match="RESET_RETURNS"):
        k.start()
    assert k.records[-1]["items"][0]["status"] == "returned_unpublished"
    assert replay(k.journal.bytes).incomplete
