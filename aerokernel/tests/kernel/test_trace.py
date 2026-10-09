"""Executable DESIGN §12 trace, exact sequence and engine-free prefix reconstruction."""

from __future__ import annotations

import pytest

from aerokernel import Instant, KernelError, replay
from aerokernel.codec import decode_record, encode
from aerokernel.journal import prefixes
from examples.two_engine_toy import ITEM, MS, ORDER, make_toy


def test_worked_trace_exact_commits_receipts_native_inputs_and_bytes():
    a, b = make_toy(), make_toy()
    for k in (a, b):
        k.start()
        k.run_until(13 * MS)
    assert a.journal.bytes == b.journal.bytes
    transactions = [r for r in a.records if r["type"] == "transaction"]
    assert [
        (
            decode_record(r["instant"]).ns,
            decode_record(r["instant"]).microstep,
            r["phase"],
        )
        for r in transactions
    ] == [
        (0, 0, "reset"),
        (0, 1, "react"),
        (0, 2, "react"),
        (0, 3, "react"),
        (0, 4, "react"),
        (2 * MS, 0, "advance"),
        (5 * MS, 0, "advance"),
        (5 * MS, 1, "react"),
        (10 * MS, 0, "advance"),
        (10 * MS, 1, "react"),
        (10 * MS, 2, "react"),
        (13 * MS, 0, "advance"),
    ]
    receipts = []
    for record in transactions:
        for item in record["items"]:
            receipt = item.get(
                "receipt", item if item.get("kind") == "receipt" else None
            )
            if receipt is not None:
                receipts.append(
                    (
                        decode_record(record["instant"]).ns,
                        decode_record(record["instant"]).microstep,
                        receipt["kind"],
                        receipt.get("status"),
                    )
                )
    assert receipts == [
        (0, 0, "receipt", "submitted"),
        (0, 1, "receipt", "accepted"),
        (0, 1, "receipt", "executing"),
        (0, 1, "receipt", "submitted"),
        (0, 3, "receipt", "accepted"),
        (0, 3, "receipt", "executing"),
        (2 * MS, 0, "feedback", "executing"),
        (10 * MS, 1, "receipt", "succeeded"),
        (13 * MS, 0, "receipt", "succeeded"),
    ]
    assert a.view().field((ITEM, "position_truth"), Instant(13 * MS)).value == 10
    assert (
        a.view().field((ORDER, "order_status"), Instant(10 * MS, 2)).value
        == "awaiting_acknowledgment"
    )
    assert (
        a.view().field((ORDER, "order_status"), Instant(13 * MS)).value
        == "accepted_output"
    )
    assert [
        (f.available.ns, f.value)
        for f in a.view().history(
            (ITEM, "position_truth"), Instant(0), Instant(14 * MS)
        )
    ] == [(0, 0), (5 * MS, 5), (10 * MS, 10)]
    before = a.view().cut
    assert a.run_until(13 * MS).cut == before
    assert a.journal.bytes == b.journal.bytes
    for prefix in prefixes(a.journal.bytes):
        r = replay(prefix)
        if any(record["type"] == "transaction" for record in r.records):
            for key in (
                (ITEM, "position_truth"),
                (ITEM, "in_zone"),
                (ORDER, "order_status"),
            ):
                assert encode(r.view().field(key, Instant(13 * MS))) == encode(
                    a.view(r.view().cut).field(key, Instant(13 * MS))
                )
            for mid in r._store.actions.states:
                assert encode(r.action(mid)) == encode(a.view(r.view().cut).action(mid))
    r = replay(a.journal.bytes)
    assert r._store.native_cuts == a._store.native_cuts
    assert r._store.frontiers == a._store.frontiers
    with pytest.raises(KernelError):
        r.run_until(20 * MS)
    a.close()
    a.close()
    b.close()
