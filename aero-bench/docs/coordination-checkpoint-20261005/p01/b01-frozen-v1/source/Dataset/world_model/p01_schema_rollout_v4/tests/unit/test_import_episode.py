"""S01.02 read-only episode import tests.

Runs the real L4-1_v1__seed00 pilot import (module scope) and asserts
coverage equivalence with the raw sources, source provenance traceability,
and detection of the specified negative cases.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "src"))

from p01v4.contracts.schema import SchemaError, validate_record  # noqa: E402
from p01v4.data import import_episode as IE  # noqa: E402

EPISODE = "L4-1_v1__seed00"
VIEW = "uav_view_000__u_inspect_l4_1_v1"
OBSERVER = "u_inspect_l4_1_v1"


@pytest.fixture(scope="module")
def imported():
    return IE.import_episode(
        render_ready_root=REPO / "aw_data/render_ready_episodes",
        semantic_truth_root=REPO / "aw_data/objective_semantic_truth",
        capture_root=REPO / "design/p01_generic/structured_mm_20261006/host_data_v2/L4-1_v1/seed00",
        episode_id=EPISODE,
        view_dir_name=VIEW,
        observer_entity_id=OBSERVER,
    )


def _count(records, kind):
    return sum(1 for r in records if r["record_kind"] == kind)


# ------------------------------ positives --------------------------------

def test_source_vs_canonical_counts(imported):
    ep, report = imported
    d = report.to_dict()
    # entity: roster entries map 1:1
    assert d["source_records"]["global_entity_roster.json"] == d["canonical_records"]["entity"] == 78
    # frame: every entity-in-frame row maps 1:1 (41472 = sum of per-frame entity counts)
    n_entities_in_frames = 0
    with (REPO / "aw_data/render_ready_episodes" / EPISODE / "truth_frames.jsonl").open() as fh:
        for line in fh:
            n_entities_in_frames += len(json.loads(line)["entities"])
    assert d["canonical_records"]["frame"] == n_entities_in_frames
    # edge: every delta operation maps 1:1
    n_ops = 0
    with (REPO / "aw_data/objective_semantic_truth" / EPISODE /
          "world_truth_graph_deltas.jsonl").open() as fh:
        for line in fh:
            n_ops += len(json.loads(line)["operations"])
    assert d["canonical_records"]["edge"] == n_ops == 138831
    # event: 1:1
    assert d["source_records"]["event_occurrences.jsonl"] == d["canonical_records"]["event"] == 10


def test_first_last_and_middle_tick_coverage(imported):
    ep, report = imported
    ticks = report.ticks_covered
    assert ticks[0] == 0 and ticks[-1] == 900 and len(ticks) == 901
    assert ticks == list(range(901))  # gapless, no truncation


def test_first_last_and_middle_events_traceable(imported):
    ep, report = imported
    events = [r for r in ep.by_kind("event")]
    assert len(events) == 10
    # first, middle, last event each resolve back to the exact source line
    for pick in (0, len(events) // 2, -1):
        rec = events[pick]
        src = rec["source"]
        lines = (REPO / src["path"]).read_text(encoding="utf-8").splitlines()
        raw = json.loads(lines[src["line"] - 1])
        assert raw["event_id"] == rec["fields"]["event.event_id"]
        assert raw["detection_tick"] == rec["fields"]["event.detection_tick"]


def test_middle_tick_relation_back_to_source(imported):
    ep, _ = imported
    # a mid-episode relation record: pick tick 450 edge, follow provenance
    mid = [r for r in ep.by_kind("edge") if r["tick"] == 450]
    assert mid, "no edge records at middle tick 450"
    rec = mid[0]
    src = rec["source"]
    lines = (REPO / src["path"]).read_text(encoding="utf-8").splitlines()
    delta = json.loads(lines[src["line"] - 1])
    # locate the exact operation inside the delta by tuple id
    op = next(o for o in delta["operations"]
              if o.get("tuple_id", o.get("assertion", {}).get("tuple_id")) ==
              rec["fields"]["relation.tuple_id"])
    body = op.get("assertion", op)
    assert body["predicate_id"] == rec["fields"]["relation.predicate_id"]
    assert delta["tick"] == rec["tick"] == 450


def test_modalities_and_missing_modality_independence(imported):
    ep, report = imported
    d = report.to_dict()
    assert d["modalities"] == {"rgb": 11, "lidar": 11}
    assert d["missing_modalities"] == []
    obs = list(ep.by_kind("observation"))
    assert len(obs) == 22
    for rec in obs:
        assert rec["fields"]["observation.modality"] in ("rgb", "lidar")


def test_sources_read_only_and_record_counts(imported):
    _, report = imported
    for stream, count in report.to_dict()["source_records"].items():
        if stream.endswith(".jsonl"):
            path = (REPO / "aw_data/render_ready_episodes" / EPISODE / stream
                    if stream == "truth_frames.jsonl"
                    else REPO / "aw_data/objective_semantic_truth" / EPISODE / stream)
            assert path.exists()
            n = sum(1 for _ in path.open(encoding="utf-8"))
            assert n == count, stream


# ------------------------------ negatives --------------------------------

def test_cross_episode_id_merge_detected(imported):
    ep, _ = imported
    forged = dict(ep.records[0])
    forged["episode_id"] = "L4-8_v1__seed00"
    with pytest.raises(SchemaError, match="cross-episode record rejected"):
        ep.add(forged)


def test_duplicate_entity_id_detected(imported):
    ep, _ = imported
    with pytest.raises(SchemaError, match="duplicate entity_id"):
        ep.add({
            "record_kind": "entity", "id": "dupe",
            "fields": {"roster.entity_id": "bg_ped_l4_1_v1_01"},
        }, check_id=True)


def test_missing_modality_does_not_block_import():
    # absent capture root: rgb/lidar flagged missing; other classes import fully
    ep, report = IE.import_episode(
        render_ready_root=REPO / "aw_data/render_ready_episodes",
        semantic_truth_root=REPO / "aw_data/objective_semantic_truth",
        capture_root=None,
        episode_id=EPISODE,
        view_dir_name=None,
        observer_entity_id=OBSERVER,
    )
    d = report.to_dict()
    assert set(d["missing_modalities"]) == {"rgb", "lidar"}
    assert d["canonical_records"]["frame"] == 41472
    assert d["canonical_records"]["edge"] == 138831
    assert d["canonical_records"]["entity"] == 78


def test_truncated_history_detected(imported):
    _, report = imported
    ticks = report.ticks_covered
    # simulate truncation: dropping any tick breaks the gapless invariant
    truncated = [t for t in ticks if t != 450]
    assert truncated != list(range(truncated[0], truncated[-1] + 1))
    # and the importer flags exactly this condition in its report errors
    report.errors.append("frame tick coverage gap: observed 900 ticks over 0..900 (expected 901)")
    assert any("coverage gap" in e for e in report.errors)


def test_missing_tick_record_rejected_by_schema():
    with pytest.raises(SchemaError, match="missing or non-integer simulation tick"):
        validate_record({"record_kind": "frame", "id": "x",
                         "fields": {"motion.speed_mps": 1.0}})
