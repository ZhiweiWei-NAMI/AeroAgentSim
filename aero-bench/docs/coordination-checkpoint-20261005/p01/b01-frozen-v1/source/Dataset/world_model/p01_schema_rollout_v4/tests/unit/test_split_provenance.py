"""S01.04 supplement tests: split groups, transported_q exclusion, leak audits.

Parent cross-review supplement requirements:
- source_family/split_group derived from ORIGINAL scenario+seed; every copy,
  overlay and branch of one source shares one split; versions are not
  independent examples;
- transported_q (TRUE future + TRAIN-frozen Gaussian noise, oracle
  diagnostic only) rejected from prediction inputs;
- future-truth and future-visibility/topology selector leakage rejected;
- producer mechanism / available_time / same-run capture binding recorded
  where established, unknown stays explicit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "src"))

from p01v4.data import provenance as P  # noqa: E402
from p01v4.data import windows as W  # noqa: E402

CUTOFF = 250

# The split table is the verified TRAIN/VALID assignment of the prior v2
# engineering cohort (host_data_v2/cohort.json), keyed by ORIGINAL
# scenario/seed identity.
SPLIT_TABLE = {
    "L1-1_v1__seed00": "TRAIN",
    "L4-1_v1__seed00": "TRAIN",
    "L4-5_v1__seed00": "TRAIN",
    "L4-8_v1__seed00": "TRAIN",
    "L1-1_v1__seed01": "VALID",
    "L4-3_v1__seed00": "VALID",
}


# --------------------- original identity / split group --------------------

def test_original_identity_parse():
    ident = P.original_identity("L4-1_v1__seed00")
    assert ident.original_key == "L4-1_v1__seed00"
    assert (ident.scenario, ident.version, ident.seed) == ("L4-1", 1, 0)
    assert ident.grammar_verified and ident.variant_marks == ()


def test_split_group_shared_by_all_versions():
    # every old/new copy, 36overlay and branch of one source maps to the SAME
    # split group: the original key decides, not the variant decoration
    assert P.split_group("L4-1_v1__seed00", SPLIT_TABLE) == "TRAIN"
    assert P.split_group("L4-1_v1__seed00::overlay36", SPLIT_TABLE) == "TRAIN"
    assert P.split_group("L4-1_v1__seed00++congested", SPLIT_TABLE) == "TRAIN"
    assert P.split_group("L4-3_v1__seed00::copy2", SPLIT_TABLE) == "VALID"


def test_split_group_matches_old_cohort_assignment():
    cohort = json.loads((REPO / "design/p01_generic/structured_mm_20261006/"
                         "host_data_v2/cohort.json").read_text(encoding="utf-8"))
    for ep in cohort["episodes"]:
        key = ep["episode_id"]  # cohort keys are already original identities
        assert P.split_group(key, SPLIT_TABLE) == ep["split"]


def test_decorated_variant_resolves_to_original_not_new_group():
    ident = P.original_identity("L4-1_v1__seed00::overlay36")
    assert ident.original_key == "L4-1_v1__seed00"  # decoration preserved separately
    assert ident.variant_marks == ("overlay36",)
    assert ident.grammar_verified is False


def test_original_missing_from_split_table_raises():
    with pytest.raises(P.SplitGroupError, match="not in the split table"):
        P.split_group("L9-9_v1__seed00", SPLIT_TABLE)


# ------------------------- transported_q exclusion ------------------------

def test_transported_q_rejected_from_prediction_inputs():
    rec = {"record_kind": "frame", "id": "tq1", "tick": 10,
           "source_family": "transported_q",
           "fields": {"motion.speed_mps": 1.0}}
    with pytest.raises(P.SplitGroupError, match="oracle diagnostic"):
        P.reject_transported_inputs([rec])


def test_transported_q_detected_via_provenance_mechanism():
    rec = {"record_kind": "edge", "id": "tq2", "tick": 10,
           "provenance": {"producer_mechanism": {"source_family": "transported_q",
                                                 "mechanism": "TRUE future + TRAIN-frozen noise"}},
           "fields": {"relation.value": "true"}}
    with pytest.raises(P.SplitGroupError, match="cannot be a prediction input"):
        P.reject_transported_inputs([rec])


def test_normal_records_pass_transported_gate():
    recs = [{"record_kind": "frame", "id": "ok1", "tick": 10,
             "fields": {"motion.speed_mps": 1.0}},
            {"record_kind": "entity", "id": "ok2", "tick": None,
             "fields": {"roster.entity_id": "e1"}}]
    P.reject_transported_inputs(recs)  # no raise


def test_transported_q_mechanism_documented_not_reimplemented():
    doc = P.TRANSPORTED_Q_MECHANISM
    assert doc["admissible_as_prediction_input"] is False
    assert "TRUE future" in doc["mechanism"] and "TRAIN-frozen" in doc["mechanism"]
    assert doc["producer"].startswith("historical")  # lineage, not re-derived here


# ---------------- future-truth input rejection (new cases) ---------------

def _frame(tick, speed=1.0):
    return {"record_kind": "frame", "id": f"f{tick}", "tick": tick,
            "fields": {"motion.speed_mps": speed}}


def test_future_truth_frame_rejected_as_input():
    leaky = [_frame(10), _frame(260), _frame(270)]
    with pytest.raises(W.FutureLeakError, match="future-truth record"):
        W.reject_future_truth_inputs(leaky, cutoff=CUTOFF)


def test_future_truth_edge_rejected_as_input():
    rec = {"record_kind": "edge", "id": "edge300", "tick": 300,
           "fields": {"relation.predicate_id": "p", "relation.tuple_id": "t",
                      "relation.value": "true"}}
    with pytest.raises(W.FutureLeakError, match="tick 300"):
        W.reject_future_truth_inputs([rec], cutoff=CUTOFF)


def test_available_time_after_cutoff_rejected_even_if_tick_past():
    # content tick predates the cutoff but the payload only became available
    # after it: hindsight truth, not legal input
    rec = {"record_kind": "frame", "id": "late", "tick": 100,
           "provenance": {"available_time": {"rule": "hindsight_truth", "tick": 400}},
           "fields": {"motion.speed_mps": 1.0}}
    with pytest.raises(W.FutureLeakError, match="available at tick 400"):
        W.reject_future_truth_inputs([rec], cutoff=CUTOFF)


def test_prefix_truth_inputs_pass_future_truth_gate():
    W.reject_future_truth_inputs([_frame(0), _frame(250)], cutoff=CUTOFF)  # no raise


# ------------- future-visibility / topology selector audits ---------------

def _full_records():
    recs = [_frame(t) for t in range(0, 251, 5)]
    recs += [_frame(t, speed=9.0) for t in range(255, 301, 5)]  # future truth
    for r in recs:
        if r["tick"] > CUTOFF:
            r["fields"]["annotations.visibility_state"] = "visible"
    return recs


def test_audit_catches_future_visibility_selector():
    recs = _full_records()

    def visibility_selector(all_records):
        # leaky: membership reads the visibility field, which future truth
        # records carry; perturbing future content must therefore flip it
        return [r for r in all_records
                if r["fields"].get("annotations.visibility_state") == "visible"]

    leaky = [r for r in recs
             if r["fields"].get("annotations.visibility_state") == "visible"]
    assert leaky and all(r["tick"] > CUTOFF for r in leaky)  # the leak is real
    with pytest.raises(W.FutureLeakError, match="future-visibility/topology leak"):
        W.audit_input_selector(visibility_selector, recs, cutoff=CUTOFF)


def test_audit_catches_future_topology_selector():
    recs = _full_records()
    recs.append({"record_kind": "edge", "id": "e300", "tick": 300,
                 "fields": {"relation.predicate_id": "near", "relation.tuple_id": "t1",
                            "relation.value": "true"}})

    def topology_selector(all_records):
        # leaky: input set grows when a FUTURE topology relation exists
        future_edges = [r for r in all_records
                        if r["record_kind"] == "edge" and r["tick"] > CUTOFF
                        and r["fields"].get("relation.value") == "true"]
        base = [r for r in all_records if isinstance(r.get("tick"), int) and r["tick"] <= CUTOFF]
        return base + ([recs[0]] if future_edges else [])

    with pytest.raises(W.FutureLeakError, match="membership depends on future"):
        W.audit_input_selector(topology_selector, recs, cutoff=CUTOFF)


def test_audit_accepts_tick_prefix_selector():
    recs = _full_records()

    def prefix_selector(all_records):
        return [r for r in all_records
                if isinstance(r.get("tick"), int) and r["tick"] <= CUTOFF]

    chosen = W.audit_input_selector(prefix_selector, recs, cutoff=CUTOFF)
    assert all(r["tick"] <= CUTOFF for r in chosen)
    assert len(chosen) == 51


# ------------------- provenance and capture binding ----------------------

def test_capture_binding_established_for_pilot_modalities():
    binding = P.capture_binding_record(
        "L4-1_v1__seed00", frame_tick=250,
        observations=[{"tick": 250, "fields": {"observation.modality": "rgb"}},
                      {"tick": 250, "fields": {"observation.modality": "lidar"}}])
    assert binding["established"] and binding["modalities"] == ["lidar", "rgb"]


def test_capture_binding_mismatch_rejected():
    with pytest.raises(P.SplitGroupError, match="same-run binding"):
        P.capture_binding_record(
            "L4-1_v1__seed00", frame_tick=250,
            observations=[{"tick": 255, "fields": {"observation.modality": "rgb"}}])


def test_available_time_unknown_stays_unknown():
    doc = P.available_time_document(rule="unknown", note="receiver register log: availability rule not established")
    assert doc == {"rule": "unknown", "tick": None,
                   "note": "receiver register log: availability rule not established"}
    with pytest.raises(P.SplitGroupError, match="must not carry a tick"):
        P.available_time_document(rule="unknown", tick=123)


def test_available_time_established_rules_recorded():
    cap = P.available_time_document(rule="capture_bound", tick=250)
    hind = P.available_time_document(rule="hindsight_truth", tick=250)
    assert cap["tick"] == 250 and hind["rule"] == "hindsight_truth"


# ------------- point 1: package vs per-dimension revisions ----------------

def _pkg_doc():
    return P.package_revision_document(
        package_source_revision="v14_package36x8",
        dimensions={
            "truth_frames": {"producer_revision": "v14_package36x8",
                             "source_files": ["aw_data/render_ready_episodes/L4-1_v1__seed00/truth_frames.jsonl"]},
            "event_occurrences": {"producer_revision": "v14_package36x8",
                                  "source_files": ["aw_data/objective_semantic_truth/L4-1_v1__seed00/event_occurrences.jsonl"]},
            "rgb": {"producer_revision": "r20261006-host",
                    "source_files": ["design/p01_generic/structured_mm_20261006/host_data_v2/L4-1_v1/seed00/rgb"]},
        })


def test_package_label_never_proves_all_producers_changed():
    doc = _pkg_doc()
    s = P.revision_summary(doc)
    assert s["package_source_revision"] == "v14_package36x8"
    assert s["changed_vs_package"] == ["rgb"]          # the only differing producer
    assert s["same_as_package"] == ["event_occurrences", "truth_frames"]
    # the other five dimensions have NO per-dimension revision: unknown
    assert s["unknown_producer_revision"] == ["entity_roster", "lidar",
                                              "world_truth_graph_deltas"]
    assert "never proves all producers changed" in s["note"]


def test_missing_source_file_map_rejected():
    with pytest.raises(P.ProvenanceError, match="must map its actual source files"):
        P.package_revision_document(
            package_source_revision="v14",
            dimensions={"lidar": {"producer_revision": "x", "source_files": []}})


def test_unprovided_dimension_recorded_unknown_not_guessed():
    doc = P.package_revision_document(package_source_revision="v14", dimensions={})
    assert doc["dimensions"]["rgb"]["producer_revision"] is None
    assert "producer_revision unknown" in doc["dimensions"]["rgb"]["note"]


# ------------- point 2: archive view vs model-visible view ----------------

def _archive_records():
    return [
        {"record_kind": "frame", "id": "f10", "tick": 10, "fields": {}},
        {"record_kind": "frame", "id": "f300", "tick": 300, "fields": {}},
        {"record_kind": "entity", "id": "script", "tick": None,
         "fields": {"identity.scenario_id": "full_event_script_text"},
         "provenance": {"available_time": {"rule": "archive_only"}}},
        {"record_kind": "edge", "id": "future_fault", "tick": 300,
         "fields": {"relation.predicate_id": "fault"},
         "provenance": {"available_time": {"rule": "archive_only"}}},
        {"record_kind": "frame", "id": "known_plan", "tick": 10,
         "fields": {},
         "provenance": {"available_time": {"rule": "archive_only",
                                           "known_at_cutoff": True,
                                           "experiment_label": "known_plan_v1"}}},
    ]


def test_archive_only_content_excluded_from_model_view():
    vis = P.model_visible_view(_archive_records(), cutoff=CUTOFF)
    ids = {r["id"] for r in vis}
    assert "script" not in ids and "future_fault" not in ids
    assert "f10" in ids and "f300" not in ids
    # three archive_only-marked records are excluded (the known-plan one too,
    # from the default view - it belongs to its separately labeled experiment)
    assert P.model_visible_view.last_exclusion_counts["archive_only"] == 3


def test_known_plan_experiment_requires_separate_label():
    # a known-plan experiment needs its own label; the default view excludes it
    vis = P.model_visible_view(_archive_records(), cutoff=CUTOFF)
    assert "known_plan" not in {r["id"] for r in vis}
    rec = next(r for r in _archive_records() if r["id"] == "known_plan")
    label = rec["provenance"]["available_time"]["experiment_label"]
    assert label and label != "default"  # separate experiment namespace


def test_transportexcluded_from_model_view():
    recs = [{"record_kind": "frame", "id": "tq", "tick": 10,
             "source_family": "transported_q", "fields": {}}]
    assert P.model_visible_view(recs, cutoff=CUTOFF) == []


# ------------- point 5: TTL L definition and censoring --------------------

def test_ttl_L_excludes_untimely_rx_never_zero():
    cases = [
        {"available_time_tick": 10, "rx_tick": 15, "latency_ticks": 5},
        {"available_time_tick": 240, "rx_tick": 260, "latency_ticks": 20},   # timely: available at 240 <= cutoff
        {"available_time_tick": 10, "rx_tick": None},                        # no RX -> L undefined
        {"available_time_tick": 260, "rx_tick": 270, "latency_ticks": 10},   # late availability -> undefined
    ]
    out = P.timely_L(cases, cutoff=250)
    assert out["definition_id"] == P.L_DEFINITION_TTL
    # the gate is actual available_time <= cutoff; RX may physically arrive later
    assert out["n_timely"] == 2
    assert out["l_undefined_cases"] == 2
    assert out["L"] == 12.5          # mean of timely cases; no zero imputation


def test_pending_compute_tasks_stay_censored():
    cases = [{"available_time_tick": 10, "rx_tick": 12, "latency_ticks": 2,
              "compute_status": "pending"},
             {"available_time_tick": 10, "rx_tick": 12, "latency_ticks": 4,
              "compute_status": "censored"}]
    out = P.timely_L(cases, cutoff=250)
    assert out["pending_or_censored"] == 2
    assert out["n_timely"] == 2 and out["L"] == 3.0
    assert "outcome" not in cases[0]  # no hindsight outcome attached


def test_legacy_q_and_ttl_have_distinct_definition_ids():
    assert P.L_DEFINITION_LEGACY_Q != P.L_DEFINITION_TTL
    assert "q-bounded" in P.L_DEFINITION_LEGACY_Q
    assert "ttl" in P.L_DEFINITION_TTL


# ------------- point 6: same-run capture binding, actual counts ------------

def test_actual_capture_counts_not_planned_count():
    # 181 planned capture times never prove 181 successful frames; the pilot
    # manifest carries the ACTUAL counted frames (11 rgb + 11 lidar at the
    # capture supplement's exported steps), never the planned number
    manifest = json.loads((REPO / "design/p01_generic/schema_rollout_v4/"
                           "manifests/pilot-import.json").read_text(encoding="utf-8"))
    actual = manifest["modalities"]
    assert actual == {"rgb": 11, "lidar": 11}
    assert sum(actual.values()) != 181


def test_capture_binding_same_run_enforced():
    binding = P.capture_binding_record(
        "L4-1_v1__seed00", frame_tick=250,
        observations=[{"tick": 250, "fields": {"observation.modality": "rgb"}}])
    assert binding["modalities"] == ["rgb"] and binding["established"]
    with pytest.raises(P.SplitGroupError, match="same-run binding"):
        P.capture_binding_record(
            "L4-1_v1__seed00", frame_tick=250,
            observations=[{"tick": 300, "fields": {"observation.modality": "lidar"}}])


def test_not_recomputed_is_not_zero_events():
    # events never recomputed in a source are recorded as unknown, never 0
    doc = P.available_time_document(rule="unknown",
                                    note="events not recomputed in this source revision")
    assert doc["tick"] is None and doc["rule"] == "unknown"
