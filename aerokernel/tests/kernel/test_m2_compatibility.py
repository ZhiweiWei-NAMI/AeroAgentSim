"""Replay the historical journal bytes retained for compatibility."""

from pathlib import Path

import pytest

from aerokernel import Instant, replay
from aerokernel.codec import decode_record, encode
from aerokernel.engine import Partition, Timing
from examples.two_engine_toy import ITEM, ORDER


def test_actual_m1_1_1_journal_replays_with_original_final_state():
    data = Path(__file__).with_name("fixtures").joinpath("m1-1.1.jsonl").read_bytes()
    kernel = replay(data)
    assert kernel.header["minor"] == 1
    assert kernel._store.sealed_ns == 13_000_000
    assert (
        kernel.view().field((ITEM, "position_truth"), Instant(13_000_000)).value == 10
    )
    assert (
        kernel.view().field((ORDER, "order_status"), Instant(13_000_000)).value
        == "accepted_output"
    )
    assert sorted(a.status for a in kernel._store.actions.states.values()) == [
        "succeeded",
        "succeeded",
    ]


def test_only_complete_historical_declaration_shapes_get_additive_defaults():
    old_timing = {
        "$type": "Timing",
        "fields": {"mode": "fixed_step", "step_ns": 20, "origin_ns": 0, "latch": True},
    }
    assert decode_record(old_timing) == Timing("fixed_step", 20)
    for missing in old_timing["fields"]:
        malformed = {
            "$type": "Timing",
            "fields": {k: v for k, v in old_timing["fields"].items() if k != missing},
        }
        with pytest.raises(ValueError, match="RECORD_FIELDS"):
            decode_record(malformed)
    encoded = encode(Partition("p", "e"))
    encoded["fields"]["not_a_declaration"] = None
    with pytest.raises(ValueError, match="RECORD_FIELDS"):
        decode_record(encoded)
