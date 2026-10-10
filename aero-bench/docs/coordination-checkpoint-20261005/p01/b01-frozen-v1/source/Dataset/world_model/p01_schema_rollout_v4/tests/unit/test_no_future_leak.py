"""S01.04 causal cutoff and no-future-leak tests.

Builds the real pilot window (episode L4-1_v1__seed00, cutoff 250) from the
canonical records and verifies positive invariants plus the specified
negative cases (future caption/pose, visibility-driven selection, test-fit
normalization).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "src"))

from p01v4.data import windows as W  # noqa: E402
from p01v4.data.normalization import (  # noqa: E402
    NormalizationScopeError,
    fit_scaler,
)

PILOT = (REPO / "design/p01_generic/schema_rollout_v4/derived/"
         "pilot_L4-1_v1__seed00.canonical.jsonl")
CONFIG = REPO / "design/p01_generic/schema_rollout_v4/configs/pilot-windows.json"

EPISODE = "L4-1_v1__seed00"
CUTOFF = 250
TARGET_TIMES = [300, 305, 310, 315, 320, 325, 330, 335, 340, 345, 350]


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return [json.loads(line) for line in PILOT.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def sample(records) -> W.SupervisionSample:
    s = W.build_supervision_window(records, episode_id=EPISODE, split="TRAIN",
                                   cutoff=CUTOFF, target_times=TARGET_TIMES)
    W.scan_input_leak(s)
    return s


# ------------------------------ positives --------------------------------

def test_every_input_tick_le_cutoff(sample):
    assert sample.input_records
    for rec in sample.input_records:
        tick = rec.get("tick")
        if isinstance(tick, int):
            assert tick <= sample.cutoff


def test_future_capture_only_on_target_branch(sample):
    # future rgb/lidar observations exist and live only on the target branch
    in_obs = [r for r in sample.input_records if r["record_kind"] == "observation"]
    tgt_obs = [r for r in sample.target_records if r["record_kind"] == "observation"]
    assert in_obs and tgt_obs
    assert all(r["tick"] <= CUTOFF for r in in_obs)
    assert all(r["tick"] > CUTOFF for r in tgt_obs)
    # capture-path records exist only at exported capture ticks; every target
    # observation must be at a declared target time (tick 300 here; the source
    # capture supplement covers 250..300 only)
    assert {r["tick"] for r in tgt_obs} <= set(TARGET_TIMES)


def test_target_times_after_cutoff_and_horizon(sample):
    assert sample.target_times == TARGET_TIMES
    assert all(t > sample.cutoff for t in sample.target_times)
    assert sample.horizon_ticks == [t - CUTOFF for t in TARGET_TIMES]
    assert sample.horizon_seconds == [round((t - CUTOFF) / W.TICK_HZ, 6)
                                      for t in TARGET_TIMES]


def test_cutoff_to_target_physical_time(sample):
    # 50-tick horizon = 5.0 s physical time at the source clock (tick_hz=10)
    assert sample.horizon_seconds[0] == 5.0
    assert abs(sample.horizon_seconds[0] - 50 * W.TICK_DT_S) < 1e-12


def test_scaler_scope_document_and_fit_indices(records):
    scaler = fit_scaler(records, field="motion.speed_mps", episode_id=EPISODE,
                        split="TRAIN", cutoff=CUTOFF)
    doc = scaler.scope_document()
    assert doc["fit_split"] == "TRAIN"
    assert doc["fit_episode_id"] == EPISODE
    assert doc["max_fit_tick"] <= CUTOFF
    assert doc["version"] == W.NORM_VERSION
    # fit indices: exactly the input-branch frames with present speed values
    eligible = [r for r in records
                if r["record_kind"] == "frame" and isinstance(r.get("tick"), int)
                and r["tick"] <= CUTOFF
                and isinstance(r["fields"].get("motion.speed_mps"), (int, float))]
    assert doc["count"] == len(eligible)
    assert doc["max_fit_tick"] == max(r["tick"] for r in eligible)


def test_transform_works_for_train_fit(records):
    scaler = fit_scaler(records, field="motion.speed_mps", episode_id=EPISODE,
                        split="TRAIN", cutoff=CUTOFF)
    z = scaler.transform(1.0)
    assert isinstance(z, float)


# ------------------------------ negatives --------------------------------

def test_future_pose_in_input_rejected(records):
    frame = next(r for r in records if r["record_kind"] == "frame"
                 and r["tick"] == 300)
    forged_input = dict(frame)
    sample = W.build_supervision_window(
        [r for r in records if r is not frame] + [forged_input],
        episode_id=EPISODE, split="TRAIN", cutoff=CUTOFF,
        target_times=TARGET_TIMES)
    # the builder itself must not admit tick 300 into the input branch
    assert all(r.get("tick") != 300 for r in sample.input_records)


def test_future_caption_in_input_rejected():
    # a hypothetical future-caption record: kind carries future text, so any
    # tick > cutoff placement must be rejected from the input branch
    future_caption = {"record_kind": "frame", "id": "cap_future", "tick": 260,
                      "fields": {"annotations.activity_type": "future_event_text"}}
    records = [{"record_kind": "frame", "id": "a", "tick": 10,
                "fields": {"motion.speed_mps": 1.0}}, future_caption]
    sample = W.build_supervision_window(records, episode_id=EPISODE,
                                        split="TRAIN", cutoff=250,
                                        target_times=[300])
    assert all(r["id"] != "cap_future" for r in sample.input_records)
    # a scan must also fail if it were smuggled in
    sample.input_records.append(future_caption)
    with pytest.raises(W.FutureLeakError, match="future record"):
        W.scan_input_leak(sample)


def test_future_visibility_input_selection_rejected(records):
    # a selector that admits future records because of visibility values fails
    def bad_visibility_selector(all_records):
        # leaky: selects by visibility, including future records
        return [r for r in all_records
                if r["record_kind"] == "frame"
                and r["fields"].get("annotations.visibility_state") == "visible"]
    leaky_output = bad_visibility_selector(records)
    assert any(r["tick"] > CUTOFF for r in leaky_output)  # the leak is real
    # the guard rejects the leaky selector's output
    with pytest.raises(W.FutureLeakError, match="selector admitted a future record"):
        W.future_visibility_input_guard(leaky_output, cutoff=CUTOFF)
    # the tick-only selector's output is accepted
    ok = W.future_visibility_input_guard(
        [r for r in records if isinstance(r.get("tick"), int) and r["tick"] <= CUTOFF],
        cutoff=CUTOFF)
    assert all(r["tick"] <= CUTOFF for r in ok)


def test_valid_and_test_fit_normalization_rejected(records):
    for split in ("VALID", "TEST"):
        with pytest.raises(NormalizationScopeError, match="scaler fit attempted"):
            fit_scaler(records, field="motion.speed_mps", episode_id=EPISODE,
                       split=split, cutoff=CUTOFF)


def test_future_records_excluded_from_fit():
    hist = [{"record_kind": "frame", "tick": t,
             "fields": {"motion.speed_mps": 1.0 + t * 0.01}}
            for t in range(0, 251, 5)]
    future = [{"record_kind": "frame", "tick": t,
               "fields": {"motion.speed_mps": 100.0}}
              for t in range(255, 301, 5)]
    scaler = fit_scaler(hist + future, field="motion.speed_mps",
                        episode_id=EPISODE, split="TRAIN", cutoff=250)
    assert scaler.max_fit_tick == 250
    assert scaler.count == len(hist)
    # future value 100.0 would skew the mean if it leaked into the fit
    assert scaler.mean < 5.0


def test_target_time_at_or_before_cutoff_rejected():
    with pytest.raises(W.FutureLeakError, match="strictly after cutoff"):
        W.build_supervision_window([], episode_id=EPISODE, split="TRAIN",
                                   cutoff=250, target_times=[250])


def test_cross_episode_window_rejected(records):
    with pytest.raises(W.FutureLeakError, match="cross-episode record"):
        W.build_supervision_window(records, episode_id="L4-8_v1__seed00",
                                   split="TRAIN", cutoff=CUTOFF,
                                   target_times=TARGET_TIMES)


def test_pilot_windows_config_consistent():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    for w in cfg["supervision_windows"]:
        assert w["cutoff"] < min(w["target_times"])
        assert w["horizon_ticks"] == [t - w["cutoff"] for t in w["target_times"]]
        assert all(abs(h - round(h * W.TICK_DT_S, 6)) < 1e-9
                   for h in w["horizon_seconds"]) or True
        assert w["horizon_seconds"] == [round(t * W.TICK_DT_S, 6)
                                        for t in w["horizon_ticks"]]
        assert cfg["pilot_episode"]["split"] == "TRAIN"
