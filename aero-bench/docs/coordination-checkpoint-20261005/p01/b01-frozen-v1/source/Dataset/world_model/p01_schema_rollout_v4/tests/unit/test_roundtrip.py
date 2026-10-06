"""S01.03 lossless structural text tests.

Round-trip equivalence (JSONL and compact table), negative cases, order
permutation semantics, and the byte/token measurement policy on the real
pilot episode records.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "src"))

from p01v4.contracts.schema import SchemaError, validate_record  # noqa: E402
from p01v4.data import serialization as SE  # noqa: E402

PILOT = (REPO / "design/p01_generic/schema_rollout_v4/derived/"
         "pilot_L4-1_v1__seed00.canonical.jsonl")


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return [SE.canonicalize(json.loads(line))
            for line in PILOT.read_text(encoding="utf-8").splitlines()]


# ------------------------------ positives --------------------------------

def test_jsonl_roundtrip_exact(records):
    text = SE.encode_jsonl(records)
    assert SE.decode_jsonl(text) == records


def test_compact_roundtrip_exact(records):
    tables = SE.encode_compact(records)
    assert SE.decode_compact(tables) == records


def test_precision_enum_units_edges_missing_preserved(records):
    # floats: pick a frame record, check exact binary64 passthrough
    frame = next(r for r in records if r["record_kind"] == "frame")
    pos = frame["fields"]["pose.position_enu_m"]
    assert isinstance(pos, dict) and pos["frame"] == "world_enu_m"
    tables = SE.encode_compact(records)
    back = SE.decode_compact(tables)
    bframe = next(r for r in back if r["record_kind"] == "frame"
                  and r["id"] == frame["id"])
    bpos = bframe["fields"]["pose.position_enu_m"]
    for a, b in zip(pos["value"], bpos["value"]):
        assert math.copysign(1, a) == math.copysign(1, b)
        assert a == b  # exact, no rounding
    # missing markers preserved
    ev = next(r for r in back if r["record_kind"] == "event"
              and r["fields"]["event.end_tick"] != {"kind": "missing"})
    assert not isinstance(ev["fields"]["event.end_tick"], dict)  # present int
    # edge values preserved
    edge = next(r for r in back if r["record_kind"] == "edge")
    assert edge["fields"]["relation.value"] in ("true", "false", "unknown", "out_of_scope")


def test_permuted_order_semantic_equivalence(records):
    tables = SE.encode_compact(records[::-1])
    permuted = SE.decode_compact(tables)
    assert SE.semantic_equivalent(permuted, records)


def test_typed_input_view_from_same_records(records):
    view = SE.to_typed_input(records)
    assert len(view["entities"]) == 78
    assert len(view["states"]) == 41472
    assert len(view["edges"]) == 138831
    assert len(view["events"]) == 10
    # typed state rows carry world-frame vectors only
    st = view["states"][0]
    assert st["position_enu_m"]["frame"] == "world_enu_m"


def test_schema_drift_detected(records):
    # a record with an unregistered family fails validation after decode
    forged = dict(records[0])
    forged["fields"] = dict(records[0]["fields"])
    forged["fields"]["roster.new_unregistered"] = 1.0
    with pytest.raises(SchemaError, match="unknown field family"):
        validate_record(forged)


# ------------------------------ negatives --------------------------------

def test_rounding_detected():
    rec = {"record_kind": "frame", "id": "f", "tick": 0, "entity_id": "e",
           "fields": {"motion.speed_mps": 0.1234567890123456789}}
    valid = dict(rec)
    validate_record(valid)  # schema accepts
    text = SE.encode_jsonl([SE.canonicalize(valid)])
    decoded = SE.decode_jsonl(text)
    # lossless: binary64 round-trips exactly
    assert decoded[0]["fields"]["motion.speed_mps"] == valid["fields"]["motion.speed_mps"]
    # but a rounded value is NOT equal: tolerance is not allowed
    rounded = dict(rec)
    rounded["fields"] = {"motion.speed_mps": round(rec["fields"]["motion.speed_mps"], 6)}
    assert decoded[0]["fields"]["motion.speed_mps"] != rounded["fields"]["motion.speed_mps"]


def test_missing_edge_detected(records):
    edges = [r for r in records if r["record_kind"] == "edge"]
    dropped = [r for r in records if r["record_kind"] != "edge"]
    # any dropped edge class breaks coverage equivalence
    text = SE.encode_jsonl(dropped + edges[:-1])
    back = SE.decode_jsonl(text)
    assert sum(1 for r in back if r["record_kind"] == "edge") == len(edges) - 1
    assert not SE.semantic_equivalent(back, records)


def test_list_order_semantics():
    a = {"frame": "world_enu_m", "value": [1.0, 2.0, 3.0]}
    b = {"frame": "world_enu_m", "value": [3.0, 2.0, 1.0]}
    rec_a = {"record_kind": "frame", "id": "f", "tick": 0,
             "fields": {"pose.position_enu_m": a}}
    rec_b = {"record_kind": "frame", "id": "f", "tick": 0,
             "fields": {"pose.position_enu_m": b}}
    assert SE.decode_jsonl(SE.encode_jsonl([rec_a]))[0]["fields"]["pose.position_enu_m"] != \
        SE.decode_jsonl(SE.encode_jsonl([rec_b]))[0]["fields"]["pose.position_enu_m"]


def test_unknown_non_present_kind_in_compact():
    tables = {"entity": {"columns": SE._COMPACT_COLUMNS, "pool": [],
                         "rows": [["x", None, None, None, None, 0, ["!bogus"]]]}}
    with pytest.raises(SchemaError, match="bad field_names pool ref|unknown non-present kind"):
        SE.decode_compact(tables)


def test_ragged_row_rejected():
    tables = {"entity": {"columns": SE._COMPACT_COLUMNS, "pool": ["roster.entity_id"],
                         "rows": [["x", None, None, None, None, 0]]}}
    with pytest.raises(SchemaError, match="ragged row width|bad field_names pool ref"):
        SE.decode_compact(tables)


# --------------------------- byte/token policy ---------------------------

def test_byte_measurement_and_token_policy(records):
    full = SE.encode_jsonl(records)
    compact = json.dumps(SE.encode_compact(records), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    full_bytes = len(full.encode("utf-8"))
    compact_bytes = len(compact.encode("utf-8"))
    assert full_bytes > 0 and compact_bytes > 0
    # the compact columnar form must actually be smaller
    assert compact_bytes < full_bytes
    # token measurement: only with the authorized local tokenizer; otherwise
    # report bytes and never present them as tokens.
    snapshot = (Path("/mnt/data1/weizhiwei/AERO_WORLD_runtime/qwen35/hf/hub/"
                     "models--Qwen--Qwen3.5-2B-Base/snapshots/"
                     "b1485b2fa6dfa1287294f269f5fb618e03d52d7c") / "tokenizer.json")
    if snapshot.exists():
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(snapshot))  # CPU, no weight load
        n_full = len(tok.encode(full[:200000]).ids)  # bounded slice for CPU cost
        n_compact = len(tok.encode(compact[:200000]).ids)
        assert n_compact < n_full
    else:
        # bytes only; no token claims
        assert isinstance(full_bytes, int) and isinstance(compact_bytes, int)
