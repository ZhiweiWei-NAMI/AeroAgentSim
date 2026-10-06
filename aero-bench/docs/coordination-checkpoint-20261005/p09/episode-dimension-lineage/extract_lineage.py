#!/usr/bin/env python3
"""P09 episode_dimension_lineage narrow extractor.

Scope: named manifests/summaries + file-path existence checks only.
No payload archives, RGB/LiDAR, bank scans, hashing, or simulation.
Writes only inside design/p09/episode_dimension_lineage.
"""
import csv
import json
import os
from collections import Counter

ROOT = "/mnt/data2/weizhiwei/AERO_WORLD"
OUT = os.path.join(ROOT, "design/p09/episode_dimension_lineage")
RT = "/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09"
BOUNDARY = "aw_data/render_ready_episodes_capture_filtered"
OST = "aw_data/objective_semantic_truth"
CCS = "aw_data/compute_comm_supplement"
V14 = RT + "/ue_input_overlay_v14"

DIMS = [
    "communication", "computation", "energy/thermal", "motion/geometry",
    "localization/navigation", "weather/environment", "perception/capture",
    "facility/task state",
]
COLS = [
    "episode_id", "dimension", "log_ref", "producer/source_ref",
    "source_version_or_hash", "run_or_profile_revision", "evidence_ref",
    "evidence_scope", "current_consumer", "used_fields",
    "observation_or_truth", "ue_input_ref", "disposition", "pending_check",
    "source_family", "split_group", "available_time", "same_run_capture_binding",
    "time_support_recorded", "time_support_planned",
]

def jload(path):
    with open(os.path.join(ROOT, path) if not path.startswith("/") else path) as f:
        return json.load(f)

def rows(path):
    with open(os.path.join(ROOT, path) if not path.startswith("/") else path) as f:
        return list(csv.DictReader(f))

def rel(p):
    return p if p.startswith(("aw_data/", "Dataset/", "/mnt/")) else p

# --- identity sources -------------------------------------------------------
idx = rows("design/p09/source_group_plan/published_capture_episode_index.csv")
change = {r["episode_id"]: r for r in rows("design/p09/source_group_plan/current_episode_change_table.csv")}
archive = jload("aw_data/capture_archives/parent_complete_210/manifest.json")
# formal manifest shape (mechanically verified): integer episodes=210, archives=list of 210 row objects
assert archive.get("episodes") == 210 and isinstance(archive.get("episodes"), int), \
    f"archive manifest episodes must be integer 210, got {archive.get('episodes')!r}"
assert isinstance(archive.get("archives"), list) and len(archive["archives"]) == 210, \
    "archive manifest archives must be a list of 210 row objects"
arch_eps = [a["episode"] for a in archive["archives"] if isinstance(a, dict) and "episode" in a]
if len(arch_eps) != 210:
    raise SystemExit("archive manifest carries no per-episode archive entries")
ids = [r["episode_id"] for r in idx]
assert len(ids) == 210 and len(set(ids)) == 210, "identity index not 210 unique"
assert sorted(ids) == sorted(arch_eps), "identity vs archive manifest set mismatch"
scen = {}
for r in idx:
    scen.setdefault(r["scenario_id"], []).append(r["seed"])
assert len(scen) == 70 and all(sorted(v) == ["0", "1", "2"] for v in scen.values()), "not 70x3"
adopted_rows = [e for e, r in change.items() if r.get("release_adoption", "NO_CANDIDATE_ADOPTED") != "NO_CANDIDATE_ADOPTED"]
plan_adopted = {e: r for e, r in change.items() if r.get("release_adoption") == "PLAN_ADOPTED_PENDING_COMBINED_RELEASE_FREEZE"}

# --- v14 overlay set --------------------------------------------------------
pull = jload(V14 + "/pull_entry.json")
v14_rows = rows(V14 + "/episode_status.csv")
v14_eps = [r["episode_id"] for r in v14_rows]
assert len(v14_eps) == pull.get("episodes", len(v14_eps))

# P01/P09 supplement (1): separate v14 package source_revision from per-dimension
# producer_revision. The package label never proves all eight producers changed;
# the projection receipts name the actual revised files.
PROJ = V14 + "/capture_filtered_updates"
V14_LABEL = "p09.ue-input-overlay/v14 (package label)"
V14_SRC = "p09.linked-native-causal/v12"
V13_META = "p09.linked-source-adoption/v13-metadata"
PRODUCER_BY_FILE = {
    "event_script.json": "authored event script producer",
    "scene_setup.json": "authored scene producer",
    "adoption_profile.json": "adoption metadata (not an episode producer)",
    "summary.json": "authored plan/closure producer (v12 adopted summary)",
    "actions.json": "authored plan/closure producer (v12 adopted actions)",
    "event_admission.json": "authored plan/closure producer (v12 adopted admission)",
    "event_trace.jsonl": "event realization producer (v12)",
    "weather.jsonl": "authored weather schedule producer (v12)",
    "command_receipts.json": "command receipt producer (v12; RX evidence only, not completed physical actions)",
    "receiver_observed_states.jsonl": "communication observation producer (v12)",
    "predicate_flips.jsonl": "predicate evaluation producer (v12)",
    "trajectories.jsonl": "motion producer (v12; authored waypoint engine, not PX4 feedback)",
    # p09.receipt_replay/v5/run1 files (6 L6-2 episodes)
    "receipt.json": "receipt replay evidence (v5/run1; RX/accept timing, not completed physical actions)",
    "receipt_timeline.json": "receipt replay evidence (v5/run1)",
    "run_config.json": "replay run configuration (v5/run1)",
    "cached_clearance_provenance.json": "clearance provenance cache (v5/run1)",
    "causal_event_bindings.jsonl.gz": "causal event bindings (v5/run1; event-layer evidence, not a weather producer)",
    "command_receipts.jsonl.gz": "command receipts (v5/run1; RX evidence only)",
    "local_predicate_evaluations.jsonl.gz": "predicate evaluations (v5/run1)",
    "mission_trajectory.jsonl.gz": "mission trajectory (v5/run1)",
    "metadata_migration.json": "metadata migration record (not an episode producer)",
    "predicate_binding_definitions.json": "predicate binding definitions (not an episode producer)",
}
v14_proj = {}
v14_files = {}
for ep in v14_eps:
    p = os.path.join(PROJ, ep, "projection_receipt.json")
    if os.path.exists(p):
        rec = json.load(open(p))
        v14_proj[ep] = rec
        v14_files[ep] = [f.get("path", "").rsplit("/", 1)[-1] for f in rec.get("files", [])]
V14_ACTUAL = sorted({n for fl in v14_files.values() for n in fl})
v14_src_revs = sorted({r.get("source_revision", "") for r in v14_proj.values()})
v14_meta_revs = sorted({r.get("metadata_revision", "") for r in v14_proj.values()})
v14_rev_groups = {}
for ep, rec in v14_proj.items():
    v14_rev_groups.setdefault((rec.get("source_revision", ""), rec.get("metadata_revision", "ABSENT")), []).append(ep)

def v14_inherit_note(ep, candidates, label):
    src = v14_proj.get(ep, {}).get("source_revision", "")
    files = v14_files.get(ep, [])
    for c in candidates:
        if c in files:
            return f"; v14 package (source_revision={src}) inherits producer file {c}"
    return f"; v14 package (source_revision={src}) lists no {label} producer file (no producer change evidenced)"

# P01/P09 supplement (2): model-visible view vs lossless archive-only view.
# Names/labels present in named summaries; contents deliberately not read.
ARCHIVE_ONLY = ("address/archive-only unless known at cutoff: full event_script/scene_setup, future fault and "
                "weather schedules, terminal/hidden control, semantic episode names")
AO = " | archive-only view: " + ARCHIVE_ONLY

# P01/P09 supplement (5): DISTINCT definition ids for the two L/P families.
DEF_Q = ("definition_id=p09.LP.q-derived/v1 (old q-based scalar L/P; source distribution/statistics not "
         "demonstrated per root causal summary)")
DEF_TTL = ("definition_id=p09.LP.mature-ttl-cohort/v1 (native ns3 receipt cohort; strict RX<firstTX+TTL; "
           "count actual available_time<=cutoff; no timely RX means L undefined, never zero; incomplete compute "
           "tasks remain pending/right-censored without hindsight outcomes)")

# P01/P09 supplement (4): split_group base family = scenario_id minus trailing _vN.
import re

def split_base(scenario_id):
    m = re.match(r"^(.+)_v\d+$", scenario_id)
    return m.group(1) if m else scenario_id

# --- per-episode canonical artifact existence (path existence only) ---------
exist = {}
for ep in ids:
    e = {}
    for key, p in [
        ("episode_manifest", f"{BOUNDARY}/{ep}/episode_manifest.json"),
        ("trajectories", f"{BOUNDARY}/{ep}/trajectories.jsonl"),
        ("truth_frames", f"{BOUNDARY}/{ep}/truth_frames.jsonl"),
        ("weather_meta", f"{BOUNDARY}/{ep}/weather_meta.jsonl"),
        ("render_host_config", f"{BOUNDARY}/{ep}/render_host_config.json"),
        ("scene_occupancy", f"{BOUNDARY}/{ep}/scene_occupancy_manifest.json"),
        ("event_occurrences", f"{OST}/{ep}/event_occurrences.jsonl"),
        ("communication_state", f"{OST}/{ep}/communication_state.jsonl"),
        ("compute_state", f"{OST}/{ep}/compute_state.jsonl"),
        ("domain_state", f"{OST}/{ep}/domain_state_observations.jsonl"),
        ("l0_state", f"{OST}/{ep}/l0_predicate_state.jsonl"),
        ("ccs_summary", f"{CCS}/{ep}/summary.json"),
        ("ccs_manifest", f"{CCS}/{ep}/simulation_manifest.json"),
        ("v14_render_host_config", f"{V14}/capture_filtered_updates/{ep}/render_host_config.json"),
        ("v2_compute_ref", f"{RT}/compute_source_all210_v2_reference/{ep}"),
    ]:
        e[key] = os.path.exists(os.path.join(ROOT, p) if not p.startswith("/") else p)
    exist[ep] = e

# --- row builders -----------------------------------------------------------
BUSINESS_EPS = ["L2-3_v1__seed00", "L2-3_v1__seed01", "L2-3_v1__seed02",
                "L6-2_v1__seed00", "L6-2_v1__seed01", "L6-2_v1__seed02"]
V9_EP = "L6-2_v1__seed00"
# available_time is ACTOR CUTOFF-AVAILABILITY: UNKNOWN unless an actual ingest/
# consumption receipt exists (none does today). Time support labels are recorded
# separately in time_support_recorded / time_support_planned.
AVAIL_UNKNOWN = "UNKNOWN_no_actual_ingest_receipt"
TS_RECORDED = "recorded_time_support_full_episode_0_90s(901_truth_ticks;181_capture_frames)"
TS_CAPTURE = "planned_capture_grid_0_90s_every_0.5s(181_planned_times;6516_grid_36eps)"
TS_UNK = "unknown"

matrix = []

def add(ep, dim, **kw):
    if "producer_source_ref" in kw:
        kw["producer/source_ref"] = kw.pop("producer_source_ref")
    row = {c: "" for c in COLS}
    row.update(episode_id=ep, dimension=dim, split_group=split_base(
        next(r["scenario_id"] for r in idx if r["episode_id"] == ep)))
    row.update(kw)
    matrix.append(row)

for r in idx:
    ep = r["episode_id"]
    e = exist[ep]
    sc = r["scenario_id"]
    in14 = ep in v14_eps
    sg = split_base(sc)
    ue14 = f"{V14}/capture_filtered_updates/{ep}/render_host_config.json" if e["v14_render_host_config"] else ""
    bind_orig = "original_capture_run(capture_filtered_boundary)"
    bind_motion = (bind_orig + (v14_inherit_note(ep, ["trajectories.jsonl", "mission_trajectory.jsonl.gz"], "motion") + " (UE capture not yet run; authored waypoint engine, not PX4 feedback)" if in14 else ""))
    bind_weather = (bind_orig + (v14_inherit_note(ep, ["weather.jsonl"], "weather") + " (authored schedule, not new weather dynamics)" if in14 else ""))
    bind_capture = (bind_orig + ("; v14_technical_overlay_same_window(0..900_ticks)" if in14 else ""))
    comm14 = ("; v14 package " + (f"(source_revision={v14_proj[ep].get('source_revision','')}) inherits {V13_META} metadata + receiver_observed_states.jsonl (no new comm producer executed)" if "receiver_observed_states.jsonl" in v14_files.get(ep, []) else f"(source_revision={v14_proj[ep].get('source_revision','')}) — no comm observation file listed (no comm producer change evidenced)") if in14 else "")
    motion14 = v14_inherit_note(ep, ["trajectories.jsonl", "mission_trajectory.jsonl.gz"], "motion") if in14 else ""
    weather14 = v14_inherit_note(ep, ["weather.jsonl"], "weather") if in14 else ""
    # 1 communication
    add(ep, "communication",
        log_ref=f"{OST}/{ep}/communication_state.jsonl" if e["communication_state"] else "",
        producer_source_ref=f"{CCS}/{ep}/simulation_manifest.json (model aeroworld_discrete_compute_comm_sim)" if e["ccs_manifest"] else "",
        source_version_or_hash="compute_comm:1.6.0" + comm14,
        run_or_profile_revision="aeroworld_compute_comm_supplement_v1",
        evidence_ref=f"{CCS}/{ep}/summary.json" if e["ccs_summary"] else "",
        evidence_scope="episode_output" if e["communication_state"] and e["ccs_summary"] else "source_only",
        current_consumer="documented corpus-level: Qwen fit consumes latency_mean_ms+packet_loss_ratio only (design/p09/source_group_plan/current_state_facts.md); episode-level consumption unconfirmed; " + DEF_Q,
        used_fields="bandwidth.allocated/dropped_mbps; link_quality; handover.active; requirement.status (row schema verified L1-1_v1__seed00)",
        observation_or_truth="truth=1.6.0 supplement model output; agent comm observations in communication_state.jsonl; command RX not evidence of actions",
        ue_input_ref=ue14,
        pending_check="confirm whether the adopted training pipeline consumes this episode's 1.6.0 comm outputs directly or only derived fit fields; group-specific ns3 replay candidates (12 groups/36 eps) not adopted; " + DEF_TTL,
        source_family="original_210_compute_comm_1.6.0",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["communication_state"] else TS_UNK,
        same_run_capture_binding="unknown")
    # 2 computation
    add(ep, "computation",
        log_ref=f"{OST}/{ep}/compute_state.jsonl" if e["compute_state"] else "",
        producer_source_ref=f"{CCS}/{ep}/simulation_manifest.json (model aeroworld_discrete_compute_comm_sim)" if e["ccs_manifest"] else "",
        source_version_or_hash="compute_comm:1.6.0",
        run_or_profile_revision="aeroworld_compute_comm_supplement_v1",
        evidence_ref=f"{CCS}/{ep}/summary.json" if e["ccs_summary"] else "",
        evidence_scope="episode_output" if e["compute_state"] and e["ccs_summary"] else "source_only",
        current_consumer="documented corpus-level: 210 low-load compute sidecars (runtime batch_receipt.json); episode-level consumption unconfirmed",
        used_fields="allocation.cpu_cores; queue_depth; capacity.cpu_cores; failure.requirement_status; node_availability.available (row schema verified L1-1_v1__seed00)",
        observation_or_truth="truth=1.6.0 supplement model output; agent compute observations in compute_state.jsonl",
        ue_input_ref=ue14,
        pending_check="final computation cohort undecided: compute_source_all210_v2_reference is LOW_LOAD_REFERENCE_ONLY not adopted; business v3 candidates only for " + ",".join(BUSINESS_EPS) + "; " + DEF_TTL,
        source_family="original_210_compute_comm_1.6.0",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["compute_state"] else TS_UNK,
        same_run_capture_binding="unknown")
    # 3 energy/thermal
    add(ep, "energy/thermal",
        log_ref=f"{OST}/{ep}/domain_state_observations.jsonl" if e["domain_state"] else "",
        producer_source_ref="Dataset/semantic_simulation/domain_state.py (model aeroworld_domain_state_observation_sim)",
        source_version_or_hash="domain_state:2.2.0",
        run_or_profile_revision="domain_state_supplement 2.2.0",
        evidence_ref=f"{OST}/{ep}/event_occurrences.jsonl families charger_unavailable/charging_queue_over_capacity/high_temp_battery_derating where present" if e["event_occurrences"] else "",
        evidence_scope="episode_output" if e["domain_state"] else "source_only",
        current_consumer="none confirmed at episode level; P01 facility_energy_service contract lists domain.payload_energy.state_of_charge_ratio operands (current_state_facts.md)",
        used_fields="domain.payload_energy.state_of_charge_ratio (contract operand); per-episode energy observation rows not read",
        observation_or_truth="domain observations (model output); thermal/battery physical discharge model not demonstrated (root causal summary)",
        ue_input_ref=ue14,
        pending_check="verify per-episode energy observation_family rows and any actual consumer; unconsumed physical dimensions do not block P01 B01 CPU engineering",
        source_family="original_210_domain_state_2.2.0",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["domain_state"] else TS_UNK,
        same_run_capture_binding="unknown")
    # 4 motion/geometry
    motion_pending = ("byte/content equivalence of captured truth vs boundary unresolved (missing capture-time per-artifact receipts); v9 revised motion for " + V9_EP + " NOT adopted")
    if ep in plan_adopted:
        motion_pending = ("change-table PLAN_ADOPTED_PENDING_COMBINED_RELEASE_FREEZE: candidate measured "
                          + plan_adopted[ep].get("trajectory_change", "") + " ("
                          + plan_adopted[ep].get("changed_position_ticks", "?") + " position / "
                          + plan_adopted[ep].get("changed_activity_ticks", "?") + " activity ticks, first changed pose "
                          + plan_adopted[ep].get("first_changed_pose_time_s", "?") + "s); release not modified, recapture required; current formal source remains " + BOUNDARY)
    add(ep, "motion/geometry",
        log_ref=f"{BOUNDARY}/{ep}/trajectories.jsonl; {BOUNDARY}/{ep}/truth_frames.jsonl" if e["trajectories"] and e["truth_frames"] else "",
        producer_source_ref=f"{BOUNDARY}/{ep}/episode_manifest.json (server semantic pipeline; UE-rendered truth)",
        source_version_or_hash="capture_filtered_boundary(unversioned; newest boundary file 2026-09-24)" + motion14,
        run_or_profile_revision="tick contract 0..900 step5 181 frames (verified representative episodes)",
        evidence_ref=f"{BOUNDARY}/{ep}/episode_manifest.json" if e["episode_manifest"] else "",
        evidence_scope="episode_output" if e["trajectories"] and e["truth_frames"] else "source_only",
        current_consumer="documented contract-level: P01 airspace_operations(12)/agent_interaction_safety(6) spatial predicates consume rendered motion/geometry (current_state_facts.md)",
        used_fields="pos_enu; velocity_enu_mps; activity_type (verified L6-2_v1__seed00/L1-1_v1__seed00 prior P09 session)",
        observation_or_truth="UE-rendered physical truth (trajectories); truth_frames per-tick entity truth; plans/command RX excluded",
        ue_input_ref=ue14,
        pending_check=motion_pending,
        source_family="original_210_render_ready_boundary",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["trajectories"] else TS_UNK,
        same_run_capture_binding=bind_motion)
    # 5 localization/navigation
    add(ep, "localization/navigation",
        log_ref=f"{OST}/{ep}/domain_state_observations.jsonl; {OST}/{ep}/l0_predicate_state.jsonl" if e["domain_state"] and e["l0_state"] else "",
        producer_source_ref="Dataset/semantic_simulation/domain_state.py (gnss_navigation family); l0 state supplement",
        source_version_or_hash="domain_state:2.2.0",
        run_or_profile_revision="domain_state_supplement 2.2.0",
        evidence_ref=f"{OST}/{ep}/l0_predicate_source_availability.json",
        evidence_scope="episode_output" if e["l0_state"] else "source_only",
        current_consumer="documented contract-level: positioning_navigation(4) predicates (current_state_facts.md); episode-level consumption unconfirmed",
        used_fields="domain.gnss_navigation.quality_level/gnss_spoofed/position_error_m; navigation.path_deviation (contract operands)",
        observation_or_truth="domain observations + l0 predicate states (model outputs); GNSS receiver/signal simulation not demonstrated (root causal summary)",
        ue_input_ref=ue14,
        pending_check="confirm per-episode gnss_navigation rows and whether spoofing scenarios (L6-3/X2) carry typed states only or any physical model evidence",
        source_family="original_210_domain_state_2.2.0",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["l0_state"] else TS_UNK,
        same_run_capture_binding="unknown")
    # 6 weather/environment
    add(ep, "weather/environment",
        log_ref=f"{BOUNDARY}/{ep}/weather_meta.jsonl" if e["weather_meta"] else "",
        producer_source_ref="server weather source recorded at capture (row schema verified L1-1_v1__seed00)",
        source_version_or_hash="capture_filtered_boundary(unversioned)" + weather14,
        run_or_profile_revision="901 ticks/episode (0..900)",
        evidence_ref=f"{BOUNDARY}/{ep}/weather_meta.jsonl" if e["weather_meta"] else "",
        evidence_scope="episode_output" if e["weather_meta"] else "source_only",
        current_consumer="documented contract-level: environment_hazard(6) predicates; rain_fog_environment event family 24 occurrences corpus-wide",
        used_fields="condition; rain; fog_density; visibility_m; wind_speed; temperature_c; illumination_lux; dust; hazard_* (verified L1-1_v1__seed00)",
        observation_or_truth="recorded weather truth values + threshold comparisons; no local meteorological dynamics (root causal summary)",
        ue_input_ref=ue14,
        pending_check="confirm whether UE rendering actually consumed these weather values for all 210 (v14 weather projection verified only for its 36-episode scope)",
        source_family="original_210_weather_meta",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["weather_meta"] else TS_UNK,
        same_run_capture_binding=bind_weather)
    # 7 perception/capture
    disp = ""
    pend = ("camera/LiDAR sidecar clock/extrinsic fields are presence-verified only; validity unaudited; no per-frame occlusion ground truth (prior P09 finding); per-episode capture_tick list verified 1/210")
    if in14:
        disp = "UE_AFFECTED"
        pend = ("v14 technical input ready (final_ready_scope 36, original 0..90s window) but adoption/capture not decided; not an all210 log replacement; "
                "181 planned capture times do not prove 181 successful frames until an actual same-run capture binds pose/weather/clock")
    view14 = ("model-visible: package supplies planned capture_window.json (181 planned times) + multimodal_window_mask.jsonl" + AO if in14 else
              "model-visible: boundary config/occupancy metadata" + AO)
    add(ep, "perception/capture",
        log_ref=f"{BOUNDARY}/{ep}/render_host_config.json; {BOUNDARY}/{ep}/scene_occupancy_manifest.json" if e["render_host_config"] and e["scene_occupancy"] else "",
        producer_source_ref="UE capture owned by UE side; server owns capture_filtered transfer boundary",
        source_version_or_hash="capture contract 181 frames/0.5s step (representative-verified)",
        run_or_profile_revision="uav_camera_rule + 60m padding (unchanged)",
        evidence_ref=f"{BOUNDARY}/{ep}/render_host_config.json" if e["render_host_config"] else "",
        evidence_scope="episode_output" if e["render_host_config"] else "source_only",
        current_consumer="persisted UE config pointer exists per episode (intake bounded example); runtime capture consumption not proven by pointer alone",
        used_fields="render_host_config scope pointers; scene_occupancy_manifest; per-frame sidecars live inside tars (not read here)",
        observation_or_truth="capture config + occupancy metadata; RGB/depth/seg/LiDAR payload not read per assignment | " + view14,
        ue_input_ref=ue14,
        disposition=disp,
        pending_check=pend,
        source_family="original_210_capture_boundary" + ("+v14_overlay_36" if in14 else ""),
        available_time=AVAIL_UNKNOWN,
        time_support_planned=TS_CAPTURE if e["render_host_config"] else TS_UNK,
        same_run_capture_binding=bind_capture)
    # 8 facility/task state
    cand = ""
    if ep in BUSINESS_EPS:
        cand = "business v3 candidates exist (normal/congested branches; runtime compute_business_requests_v3_pilot + compute_business_group_seeds_v3)"
    if ep == V9_EP:
        cand += "; v9 reactive replay versioned output exists (NOT adopted)"
    add(ep, "facility/task state",
        log_ref=f"{OST}/{ep}/domain_state_observations.jsonl; {OST}/{ep}/event_occurrences.jsonl" if e["domain_state"] and e["event_occurrences"] else "",
        producer_source_ref="Dataset/semantic_simulation/domain_state.py (pad_facility/charging families); event pipeline",
        source_version_or_hash="domain_state:2.2.0",
        run_or_profile_revision="domain_state_supplement 2.2.0",
        evidence_ref=f"{OST}/{ep}/event_occurrences.jsonl" if e["event_occurrences"] else "",
        evidence_scope="episode_output" if e["event_occurrences"] else "source_only",
        current_consumer="none confirmed at episode level; facility/task event families present only where authored scenarios fire them (L2-3/L2-4 charging/pad; L4-9 has zero event rows unresolved)",
        used_fields="domain.pad_facility.availability/requester_count/capacity; facility.* event families (contract operands)",
        observation_or_truth="domain observations + authored facility state schedules; task outcomes are model outputs, not physical actions",
        ue_input_ref=ue14,
        pending_check=("task-profile candidate exists for this episode; not adopted" if cand else "confirm which facility observation families exist for this episode and any consumer; L4-9 zero-event applicability unresolved corpus-wide"),
        source_family="original_210_domain_state_2.2.0",
        available_time=AVAIL_UNKNOWN,
        time_support_recorded=TS_RECORDED if e["domain_state"] else TS_UNK,
        same_run_capture_binding="unknown")

assert len(matrix) == 1680, f"matrix rows {len(matrix)} != 1680"
for row in matrix:
    assert row["split_group"] == split_base(
        next(r["scenario_id"] for r in idx if r["episode_id"] == row["episode_id"]))

# P09 parent directive: make the consumer tier explicit per row without changing
# the exact 14-column base schema. Tier token prefixes current_consumer.
def tier_of(text):
    if text.startswith("none confirmed"):
        return "none_confirmed"
    if "pointer" in text:
        return "configured_pointer"
    return "documented_corpus_or_contract_level"

for row in matrix:
    row["current_consumer"] = "[tier=" + tier_of(row["current_consumer"]) + "] " + row["current_consumer"]

# --- write deliverables -----------------------------------------------------
def wbytes(name, data):
    p = os.path.join(OUT, name)
    with open(p, "w") as f:
        f.write(data)
    return os.path.getsize(p)

sizes = {}
import io
sio = io.StringIO()
wr = csv.DictWriter(sio, fieldnames=COLS, lineterminator="\n", restval="", extrasaction="raise")
wr.writeheader()
_extra = set(COLS)
for row in matrix:
    _bad = set(row) - _extra
    if _bad:
        raise ValueError(f"stray matrix keys not in required fieldnames: {sorted(_bad)}")
    wr.writerow(row)
sizes["dimension_lineage_1680.csv"] = wbytes("dimension_lineage_1680.csv", sio.getvalue())

import io as _io
sio2 = _io.StringIO()
wr2 = csv.writer(sio2, lineterminator="\n")
wr2.writerow(["episode_id", "scenario_id", "original_seed", "original_archive_ref", "original_source_manifest_ref",
              "current_adoption_ref", "current_change_table_release_adoption", "current_candidate_status",
              "scenario_seed_unique_check", "v14_overlay_present", "split_group"])
seen = set()
for r in idx:
    ep = r["episode_id"]
    key = (r["scenario_id"], r["seed"])
    assert key not in seen, f"duplicate scenario/seed {key}"
    seen.add(key)
    c = change.get(ep, {})
    wr2.writerow([ep, r["scenario_id"], r["seed"], r.get("archive", ""), r.get("source_manifest", ""),
                  f"{BOUNDARY}/{ep}", c.get("release_adoption", ""), c.get("candidate_status", ""),
                  "OK_70x3", "yes" if ep in v14_eps else "no", ep])
sizes["episode_identity_210.csv"] = wbytes("episode_identity_210.csv", sio2.getvalue())

catalog = {
    "schema_version": "p09.lineage.source-version-catalog/v2",
    "status": "EXTRACTION_PROVISIONAL_NOT_ACCEPTANCE",
    "hash_policy": "no new digests computed; known version labels only",
    "adoption_basis_rule": "current-adopted status is taken ONLY from documentary adoption statements (change-table release_adoption / original_model_version; current_state_facts.md released legacy supplements; adoption_configuration.json); path or source existence supports coverage only and never adoption",
    "v14_package_vs_producer_revisions": {
        "principle": "the 36x8 inherited package label (v14) never proves all eight producers changed; producer_revision is mapped per dimension from projection_receipt.json files lists",
        "package_label": V14_LABEL,
        "per_episode_source_revision_values": v14_src_revs,
        "per_episode_metadata_revision_values": v14_meta_revs,
        "source_revision_groups": {f"source_revision={k[0]} | metadata_revision={k[1]}": {"episodes": len(v), "members": sorted(v)} for k, v in sorted(v14_rev_groups.items())},
        "files_actually_listed_in_projection_receipts": V14_ACTUAL,
        "producer_by_file": PRODUCER_BY_FILE,
        "per_dimension_producer_revision": {
            "communication": "30 episodes (v12+v13-metadata): 1.6.0 unchanged, receiver_observed_states.jsonl inherited; 6 L6-2 episodes (p09.receipt_replay/v5/run1): receipt.json/receipt_timeline.json replay evidence, not a new physical comm producer",
            "computation": "1.6.0 unchanged (no compute file listed in projection receipts)",
            "energy/thermal": "2.2.0 unchanged",
            "motion/geometry": "30 episodes: v12 authored waypoint engine trajectories.jsonl listed (physical_motion_scope = authored waypoint engine, not PX4 motion feedback); 6 L6-2 episodes: receipt_replay/v5/run1 output",
            "localization/navigation": "2.2.0 unchanged",
            "weather/environment": "v12 weather.jsonl authored schedule inherited (30 episodes); 6 L6-2 episodes list no weather file (no weather producer change evidenced); no new weather dynamics anywhere",
            "perception/capture": "capture config projected (render_host_config + capture_window.json planned times); no new capture producer; UE capture not yet run",
            "facility/task state": "2.2.0 unchanged (no facility file listed beyond v12/v5 event sources)",
        },
        "not_recomputed_rule": "empty new event_realization is marked NOT_RECOMPUTED, which is not zero events",
    },
    "model_visible_vs_archive_only": {
        "model_visible": "values/fields a consumer can read from named manifests/summaries at the transfer boundary or in v14 package files",
        "archive_only": ARCHIVE_ONLY,
        "known_plan_experiments": "explicit known-plan experiments (authored schedules, planned fault injection, ARM windows) carry their own labels and are never counted as observed model-visible history",
    },
    "lp_definitions": {"q_derived": DEF_Q, "mature_ttl_cohort": DEF_TTL},
    "split_group_semantics": {
        "rule": "ORIGINAL scenario+seed and all versions/overlays/load branches/windows share one split_group (base family = scenario_id minus _vN); never derived from revision; version copies are not independent samples",
        "base_families": len({split_base(r['scenario_id']) for r in idx}),
        "note": "39 base families behind 70 scenario_ids: 25 paired v1/v2, 3 triple (L4-3/L4-5/L5-1 v1/v2/v3), 5 single-version L-series, 6 X-named episodes; new-scenario generalization claims require the ENTIRE base family across ALL seeds held out",
        "transported_q": "ORACLE_ONLY: transported_q TRUE future + TRAIN-frozen Gaussian noise is an oracle diagnostic, never a historical available prediction input",
    },
    "versions": [
        {"label": "compute_comm:1.6.0", "producer": "aeroworld_discrete_compute_comm_sim", "scope": "all 210 (current adopted legacy supplement)", "adoption": "current_adoption", "paths": [f"{CCS}/<episode>/{{summary.json,simulation_manifest.json}}", f"{OST}/<episode>/{{communication_state,compute_state}}.jsonl"], "coverage_claim": "manifest-asserted + path-existence checked; field-observed only for representative episodes"},
        {"label": "domain_state:2.2.0", "producer": "aeroworld_domain_state_observation_sim", "scope": "all 210 (current adopted legacy supplement)", "adoption": "current_adoption", "paths": [f"{OST}/<episode>/domain_state_observations.jsonl"], "coverage_claim": "path-existence checked; per-family row presence not read"},
        {"label": "capture_filtered_boundary(unversioned)", "producer": "server semantic pipeline + UE capture", "scope": "all 210", "adoption": "current_adoption", "paths": [f"{BOUNDARY}/<episode>/..."], "coverage_claim": "path-existence checked; newest boundary file 2026-09-24"},
        {"label": "p09.ue-input-overlay/v14 (assembly v2, pull_entry v2)", "commit": "a9a6f8d87d86c410cd22f72f399abc3b09e942ab (parent-verified; not reverified here)", "scope": "36 episodes technical input, original 0..90s window", "adoption": "output_coverage_candidate", "paths": [f"{V14}/capture_filtered_updates/<episode>/render_host_config.json"], "coverage_claim": "episode_status.csv lists 36; path-existence checked; empty new event_realization = NOT_RECOMPUTED (not zero)"},
        {"label": "compute_source_all210_v2_reference", "scope": "210 episodes", "adoption": "output_coverage_NOT_adopted", "paths": [f"{RT}/compute_source_all210_v2_reference/<episode>"], "coverage_claim": "adoption_configuration.json: LOW_LOAD_REFERENCE_ONLY, adopted_as_final_workload=false"},
        {"label": "p09.compute.local-resource-fifo/v3 (business requests)", "scope": "6 episodes x normal/congested branches", "episodes": BUSINESS_EPS, "adoption": "candidate", "paths": [f"{RT}/compute_business_requests_v3_pilot", f"{RT}/compute_business_group_seeds_v3"], "coverage_claim": "receipts read; independent review verdict PASS_REPAIRED_CODE_AND_ACTUAL_TWELVE_RUNS"},
        {"label": "receipt replay v4/v5 candidates", "scope": "L6-2 seed batch", "adoption": "plan_adopted_pending_combined_release_freeze (recapture required)", "episodes": sorted(plan_adopted.keys()), "paths": ["design/p09/source_group_plan/current_radio_checkpoint.md", f"{RT}/receipt_l6_2_seed_batch_v5_candidates", f"{RT}/receipt_l6_2_v2_seed00_v5_candidate"], "coverage_claim": "accepted_receipt_candidate=L6-2seed00receiptv4 (adoption_configuration.json); change-table recommendation A: planned new version, release_modified_by_this_work=False"},
        {"label": "ns3 versioned stack (R1 radio ns3.48 params; v7 training_r1; v8 linked; v9 reactive)", "scope": "versioned experiment dirs, not formal", "adoption": "candidate", "paths": ["aw_data/ns3_episode_v7_training_r1", "aw_data/ns3_episode_v8_linked_scenario", "aw_data/ns3_episode_v9_reactive_replay/L6-2_v1__seed00/reactive_replay/run1"], "coverage_claim": "v9 exists for 1 episode only; NOT promoted into formal 210"},
        {"label": "transported_q oracle diagnostic", "scope": "diagnostic only", "adoption": "oracle_diagnostic_NEVER_historical_input", "coverage_claim": "transported_q TRUE future + TRAIN-frozen Gaussian noise is oracle diagnostic, never a historical available prediction input"},
    ],
}
sizes["source_version_catalog.json"] = wbytes("source_version_catalog.json", json.dumps(catalog, ensure_ascii=False, indent=1))

evidence = {
    "schema_version": "p09.lineage.existing-evidence-index/v2",
    "status": "EXTRACTION_PROVISIONAL_NOT_ACCEPTANCE",
    "per_episode_canonical": {ep: {k: (f"{BOUNDARY}/{ep}/..." if k in ("episode_manifest", "trajectories", "truth_frames", "weather_meta", "render_host_config", "scene_occupancy") else f"{OST}/{ep}/..." if k in ("event_occurrences", "communication_state", "compute_state", "domain_state", "l0_state") else f"{CCS}/{ep}/...") for k, v in exist[ep].items() if v and not k.startswith(("v14", "v2"))} for ep in ids},
    "per_episode_existence_booleans": exist,
    "episode_specific_overlays": {
        "v14_capture_filtered_updates": v14_eps,
        "v14_per_episode_projection_receipts": {ep: v14_files.get(ep, []) for ep in v14_eps},
        "v14_package_label": V14_LABEL,
        "v14_package_source_revision_values": v14_src_revs,
        "v14_package_metadata_revision_values": v14_meta_revs,
        "business_v3_compute_candidates": BUSINESS_EPS,
        "v9_reactive_replay": [V9_EP],
        "receipt_v5_candidates": ["L6-2_v1__seed00 (batch)", "L6-2_v2__seed00"],
        "charging_event_episodes_L2_3": ["L2-3_v1__seed00", "L2-3_v1__seed01", "L2-3_v1__seed02", "L2-3_v2__seed00", "L2-3_v2__seed01", "L2-3_v2__seed02"],
        "zero_event_L4_9": ["L4-9_v1__seed00", "L4-9_v1__seed01", "L4-9_v1__seed02", "L4-9_v2__seed00", "L4-9_v2__seed01", "L4-9_v2__seed02"],
    },
    "named_documents_read": [
        "design/p09/source_group_plan/current_state_facts.md",
        "design/p09/source_group_plan/published_capture_episode_index.csv",
        "design/p09/source_group_plan/current_episode_change_table.csv",
        "design/p09/source_group_plan/source_groups.json",
        "design/p09/source_group_plan/current_radio_checkpoint.md",
        "design/p09/compute_resource/adoption_configuration.json",
        "design/p09/compute_resource/business_group_batch_review.json",
        "design/p09/dispatch/p09_inventory_result.json (public result head only)",
        f"{V14}/pull_entry.json",
        f"{V14}/assembly_summary.json",
        f"{V14}/episode_status.csv",
        f"{V14}/capture_filtered_updates/<episode>/projection_receipt.json (parsed for all 36: file lists, source_revision, metadata_revision; L2-1_v1__seed00 read in full)",
        f"{RT}/compute_business_requests_v3_pilot/receipt.json",
        "design/p09/episode_dimension_lineage/intake_observations.json (desktop-owned; reused counts)",
    ],
    "existence_note": "os.path.exists checks only; no file content reads for existence; no payload archives or RGB/LiDAR",
}
sizes["existing_evidence_index.json"] = wbytes("existing_evidence_index.json", json.dumps(evidence, ensure_ascii=False, indent=1))

path_exist_dims = {}
for row in matrix:
    key = row["dimension"]
    has_ref = bool(row["log_ref"]) and all(os.path.exists(os.path.join(ROOT, p.split(";")[0].strip())) or p.split(";")[0].strip().startswith("/mnt") and os.path.exists(p.split(";")[0].strip()) for p in [row["log_ref"]] if p)
    path_exist_dims[key] = path_exist_dims.get(key, 0) + (1 if has_ref else 0)

# count group 3: three explicit consumer tiers (P09 parent directive).
# actual_consumer_confirmed requires POSITIVE per-episode consumption evidence
# (an actual run/capture receipt naming the episode and the artifact). A
# configured pointer, path existence, or a corpus/contract claim never enters it;
# those are counted separately.
def consumer_tier(text):
    if "none confirmed" in text:
        return "none_confirmed"
    if "pointer" in text:
        return "configured_pointer"
    return "documented_corpus_or_contract_level"

# positive per-episode consumption receipts currently known: NONE
ACTUAL_RUN_RECEIPTS = {}     # episode_id -> receipt evidence (empty: none exist)
ACTUAL_CAPTURE_RECEIPTS = {} # episode_id -> capture binding evidence (empty: none exist)
consumer_episode_level = {}
tier_counts = {"actual_consumer_confirmed": {}, "configured_pointer": {},
               "actual_capture_binding": {}, "documented_corpus_or_contract_level": {},
               "none_confirmed": {}}
tier_members = {k: {} for k in tier_counts}
for row in matrix:
    key = row["dimension"]
    ep = row["episode_id"]
    t = row["current_consumer"].split("]")[0].replace("[tier=", "", 1)
    if ep in ACTUAL_RUN_RECEIPTS:
        t = "actual_consumer_confirmed"
    elif ep in ACTUAL_CAPTURE_RECEIPTS and key == "perception/capture":
        t = "actual_capture_binding"
    tier_counts[t][key] = tier_counts[t].get(key, 0) + 1
    tier_members[t].setdefault(key, []).append(ep)
    consumer_episode_level[key] = 0  # no episode-level field-observed consumption evidenced

# count group 5: explicit auditable memberships and numeric counts per bucket.
# Buckets are defined over explicit dimension lists; memberships are the matrix
# rows/episodes themselves — no keyword/regex guessing.
G5_BUCKETS = {
    "missing_mapping": {
        "dimensions": ["energy/thermal", "localization/navigation", "facility/task state"],
        "reason": "no episode-level consumer mapping exists in any read source; pending_check states the open question per row",
    },
    "missing_coverage_statistics": {
        "dimensions": ["perception/capture"],
        "reason": "camera/LiDAR sidecar clock/extrinsic fields presence-verified only (validity unaudited); no per-frame occlusion ground truth; per-episode capture_tick lists verified 1/210",
    },
    "real_mechanism_gap": {
        "dimensions": ["communication", "computation", "localization/navigation", "weather/environment", "facility/task state"],
        "reason": "scalar model or typed state without a physical mechanism (root causal summary): comm/compute = 1.6.0 scalar model; GNSS/weather/facility = typed states; ns3/R1/v9/business candidates exist only as versioned outputs outside the formal 210",
    },
}
g5 = {}
for _name, _spec in G5_BUCKETS.items():
    _members = {d: sorted(r["episode_id"] for r in matrix if r["dimension"] == d) for d in _spec["dimensions"]}
    g5[_name] = {
        "dimensions": _spec["dimensions"],
        "row_count": sum(len(v) for v in _members.values()),
        "episode_memberships": _members,
        "reason": _spec["reason"],
    }
g5["_total_rows"] = len(matrix)
g5["_note"] = "buckets are dimension-based and may overlap across buckets; every counted row carries exactly one specific pending_check"

coverage = {
    "schema_version": "p09.lineage.coverage-summary/v2",
    "status": "EXTRACTION_PROVISIONAL_NOT_ACCEPTANCE",
    "view_labels": {
        "model_visible": "fields/labels readable from named manifests/summaries at the boundary or in v14 package files",
        "archive_only": ARCHIVE_ONLY,
        "known_plan_experiments": "explicit known-plan experiments carry separate labels and are never counted as observed model-visible history",
    },
    "lp_definition_ids": {"q_derived": DEF_Q, "mature_ttl_cohort": DEF_TTL,
        "rule": "distinct definition_id per L/P family; count only actual available_time<=cutoff; no timely RX -> L undefined (never zero); incomplete compute tasks remain pending/right-censored without hindsight outcomes"},
    "count_group_1_identity_alignment": {
        "definition": "episode set agreement across published index (210), archive manifest (210), change table (210), v14 original210 references (210); 70 scenarios x seeds 0/1/2; duplicates none",
        "count": 210,
        "unique": len(set(ids)),
        "scenario_x_seed": "70 x {0,1,2} verified",
        "v14_alignment": "intake: entries 210/unique 210/missing []/added [] (reused, not re-audited)",
        "levels": {"manifest_assertion": 210, "file_path_existence": 210, "field_observed": "representative episodes only (prior P09 sessions)"},
    },
    "count_group_2_per_dimension_specific_output_ref": {
        "definition": "rows citing an episode-specific artifact whose path exists (existence level, not content)",
        "counts": path_exist_dims,
    },
    "count_group_3_actual_consumer_confirmed": {
        "definition": "actual_consumer_confirmed counts ONLY rows with positive per-episode consumption evidence (an actual run/capture receipt naming the episode and artifact); configured pointers, path existence and corpus/contract claims are counted separately and NEVER enter this group",
        "tier_counts": tier_counts,
        "tier_memberships": {t: {d: {"count": len(eps), "episodes": eps} for d, eps in dims.items()} for t, dims in tier_members.items() if dims},
        "actual_consumer_confirmed": "0 rows (ACTUAL_RUN_RECEIPTS empty: no per-episode consumption receipt exists)",
        "actual_capture_binding": "0 rows (ACTUAL_CAPTURE_RECEIPTS empty: no same-run pose/weather/clock capture receipt exists)",
        "configured_pointer_note": "persisted UE example config points to aw_data/rebuild_l1_1_radius10_20260918/...; pointer presence only",
        "documented_corpus_or_contract_level_note": "corpus/contract statements backed by read named documents; neither actual-run nor capture evidence",
        "episode_level_field_observed": consumer_episode_level,
    },
    "count_group_4_adopted_version_confirmed": {
        "definition": "rows citing a version confirmed adopted by actual consumer entrances (release materialized, not planned)",
        "count": 0,
        "evidence": "current_episode_change_table.csv: release_adoption=NO_CANDIDATE_ADOPTED for 204/210; the 6 L6-2_v1/v2 episodes carry PLAN_ADOPTED_PENDING_COMBINED_RELEASE_FREEZE with candidate_status=COMPLETED, planned_adoption=PLAN_ADOPTED_RECAPTURE_REQUIRED, release_modified_by_this_work=False — planned, not a materialized adopted version",
        "plan_adopted_pending_freeze_episodes": sorted(plan_adopted.keys()),
        "supporting": "adoption_configuration.json adopted_as_final_workload=false and adopted_render_versions=false; v14 is technical-input candidate only",
    },
    "count_group_4b_split_group_alignment": {
        "definition": "episodes grouped by base scenario family (scenario_id minus _vN); ORIGINAL scenario+seed and all versions/overlays/load branches/windows share one split_group; new-scenario generalization claims require the ENTIRE base family across ALL seeds held out",
        "base_families": len({split_base(r['scenario_id']) for r in idx}),
        "episodes_covered": 210,
        "composition": {"paired_v1_v2": 25, "triple_v1_v2_v3": 3, "single_version_L": 5, "X_named": 6},
    },
    "count_group_4c_v14_package_vs_producer_revisions": {
        "definition": "v14 package label (36 episodes x 8 dimensions inherited) is NOT a producer revision; actual revised files are mapped from projection_receipt.json file lists",
        "package_label": V14_LABEL,
        "package_source_revision_values": v14_src_revs,
        "package_metadata_revision_values": v14_meta_revs,
        "source_revision_groups": {f"source_revision={k[0]} | metadata_revision={k[1]}": {"episodes": len(v), "members": sorted(v)} for k, v in sorted(v14_rev_groups.items())},
        "dimensions_with_changed_producer_files": {"motion/geometry": "30 eps v12 trajectories.jsonl + 6 eps p09.receipt_replay/v5/run1", "weather/environment": "v12 weather.jsonl authored schedule (30 eps; 6 L6-2 eps list no weather file)"},
        "dimensions_unchanged": ["communication (1.6.0)", "computation (1.6.0)", "energy/thermal (2.2.0)", "localization/navigation (2.2.0)", "facility/task state (2.2.0)"],
        "projection_configured_not_produced": ["perception/capture (render_host_config + planned capture_window.json; UE capture not run)"],
        "not_recomputed_is_not_zero_events": True,
    },
    "count_group_5_pending_grouped": g5,
    "dispositions": {"UE_AFFECTED": sum(1 for r in matrix if r["disposition"] == "UE_AFFECTED"), "other": "empty with exactly one specific pending_check (per assignment rule)"},
}
sizes["coverage_summary.json"] = wbytes("coverage_summary.json", json.dumps(coverage, ensure_ascii=False, indent=1))

next_checks = """# P09 episode_dimension_lineage - next bounded checks

Status: provisional extraction, not scientific acceptance. No hashing, no payload reads, no re-audit.

1. Consumer entrances (bounded): for each of the 8 dimensions confirm which adopted pipeline stage opens the cited log paths for a given episode. Missing: consumer-side access logs/manifests. Estimate: reading consumer configs only (KB-MB), no bank scan.
2. Adopted-version confirmation: zero adopted replacements confirmed (change table NO_CANDIDATE_ADOPTED 204/210; 6 L6-2 PLAN_ADOPTED_PENDING_COMBINED_RELEASE_FREEZE, release not materialized). Missing: a coordinator decision record mapping candidate -> adopted per episode. Estimate: decision metadata only.
3. Energy/thermal + localization/navigation + facility/task state: per-episode observation_family row presence (domain_state_observations.jsonl row scan, few MB/episode). Estimate: 210 x ~1-4 MB text reads if required; summary absent for these families, hence listed rather than executed.
4. v14 overlay: confirm per-episode capture_filtered_updates/render_host_config.json actually enters the next UE run (36 technical-ready; adoption undecided). Missing: UE-side acceptance record. v14 event_realization empty = NOT_RECOMPUTED (not zero).
5. Actual UE capture receipt: bind same run/pose/weather/clock per frame; 181 planned capture times (capture_window.json) do not prove 181 successful frames; planned_frames_36_episode_grid 6516 is planned-only until per-frame receipts exist.
6. L4-9 zero-event applicability: contract expects vehicle_intersection_conflict chain; event layer empty for 6 episodes. Check acceptance records only.
7. Byte/content equivalence of captured truth vs boundary: remains unresolved; closure requires capture-time per-artifact receipts, not scans.
8. L/P definition split: q-derived (p09.LP.q-derived/v1) vs mature TTL cohort (p09.LP.mature-ttl-cohort/v1) must never be merged; count actual available_time<=cutoff; no timely RX -> L undefined (never zero); incomplete compute tasks stay pending/right-censored without hindsight outcomes. Unknown: which historical rows satisfy each definition.
9. transported_q TRUE future + TRAIN-frozen Gaussian noise: ORACLE_ONLY; verify no consumer treats it as historical prediction input.
10. Split discipline: generalization claims require the ENTIRE base scenario family (39 base families behind 70 scenario_ids) held out across ALL seeds; per-episode or per-version splits are leakage.

Source manifest sizes (from intake, not re-measured): parent_complete_210/manifest.json 103,432 B; v14 assembly_manifest.json 231,123 B; v14 source_manifest.json 182,279 B; v14 existing_sensor_dependency_index.json 407,276 B; batch_receipt.json 157,202 B; v14 projection_receipt.json ~5.8 KB x 36 (parsed for file lists only). If any summary is absent, scanning that manifest is text-only and bounded (<0.5 MB each); no payload archives.
"""
sizes["next_checks.md"] = wbytes("next_checks.md", next_checks)

readme = """# design/p09/episode_dimension_lineage

Ownership: P09 lineage extraction leaf (author session continuation). Parent owns native research and cross-review.

Contents: episode_identity_210.csv, dimension_lineage_1680.csv (210 x 8 dimensions with source_family/split_group/available_time/same_run_capture_binding supplement columns), source_version_catalog.json, existing_evidence_index.json, coverage_summary.json, next_checks.md, extract_lineage.py (this generator), author_checkpoint.json, author_result.json.

Rules honored: read-only originals (original 210, v14, ARM, evidence); named manifests only; path-existence checks only; no payload archives or RGB/LiDAR; no hashing (known version labels only); no simulation/training/field statistics; no delegation.

Consumer tiers (P09 parent directive): configured_pointer / actual_run_consumption / actual_capture_binding are DISTINCT; every current_consumer cell carries a [tier=...] token; configured pointers, path existence and global plans are never promoted into actual-consumer-confirmed. Persisted UE example config points to aw_data/rebuild_l1_1_radius10_20260918/... — pointer only, not proven actual run/capture consumption.

Supplement labels carried: (1) v14 package label vs per-dimension producer_revision (mapped from projection receipts; 36x8 inherited package never proves all eight producers changed); (2) model-visible vs archive-only view (full event_script/scene_setup, future fault/weather schedules, terminal/hidden control, semantic episode names stay archive-only unless known at cutoff; known-plan experiments carry separate labels); (3) transported_q TRUE future + TRAIN-frozen Gaussian noise = ORACLE_ONLY, never historical input; (4) split_group = base scenario family (scenario_id minus _vN) shared by ORIGINAL scenario+seed and all versions/overlays/load branches/windows — generalization claims need the entire family across all seeds held out; (5) distinct L/P definition ids (p09.LP.q-derived/v1 vs p09.LP.mature-ttl-cohort/v1); count actual available_time<=cutoff; no timely RX -> L undefined, never zero; incomplete compute tasks stay pending/right-censored; (6) actual capture must bind same run/pose/weather/clock; 181 planned capture times do not prove 181 successful frames; NOT_RECOMPUTED is not zero events.

Desktop-owned files in this directory (intake_observations.json, route_receipt.json, coordinator_public.txt, assignment.txt) are not written by this leaf.
"""
sizes["README.md"] = wbytes("README.md", readme)

sizes["extract_lineage.py"] = os.path.getsize(os.path.abspath(__file__))
print(json.dumps({"sizes": sizes, "rows": len(matrix), "adopted_rows": len(adopted_rows),
                  "v14": len(v14_eps), "scenarios": len(scen)}, indent=1))
