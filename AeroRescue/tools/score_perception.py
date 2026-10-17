#!/usr/bin/env python3
"""Score frozen native C10 observations without running inference or simulation.

Evaluator-only inputs (never mount these in a companion):
  samples: {"target_reference":"visible_surface_horizontal", "frames":[
    {"frame_id":"...", "eligible_annotation_ids":["person-1"]}]}
  annotations: {"position_reference":"visible_surface_horizontal", "annotations":[
    {"frame_id":"...", "annotation_id":"person-1", "bbox_xyxy":[x1,y1,x2,y2],
     "visible_surface_horizontal_enu_m":[east,north]}]}
Negative frames remain in frames with eligible_annotation_ids: []. Every positive
GT box contributes to detection recall. Localization eligibility is fixed by the
input manifest, never inferred from detection success or depth availability.

Observed records are read from each private index.sqlite under --inference-run.
PASS means these metric thresholds only, never all C10 clock/reset requirements.
Exit codes: 0 metric PASS, 1 metric FAIL, 2 BLOCKED by missing/native evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from prepare_models import match_detections


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def observations(run: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(run.rglob("index.sqlite")):
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            owner = db.execute("SELECT uav_id FROM identity").fetchone()
            for row in db.execute("SELECT metadata FROM observations"):
                record = json.loads(row[0])
                if owner and record.get("uav_id") == owner[0] and record.get("source") == "local":
                    result[record["frame_id"]] = {**record, "evidence_database": str(path)}
    return result


def score(samples_path: Path, annotations_path: Path, run: Path) -> dict[str, Any]:
    blocked = []
    for name, path in (("frozen samples", samples_path), ("evaluator annotations", annotations_path), ("native inference run", run)):
        if not path.exists():
            blocked.append(f"Missing {name}: {path}")
    report: dict[str, Any] = {
        "status": "BLOCKED", "scope": "native scenario detection and visible-surface horizontal localization metrics only",
        "input_samples": str(samples_path), "input_annotations": str(annotations_path), "inference_run": str(run),
        "target_reference": "visible_surface_horizontal", "matching_iou": .5, "confidence": .5,
        "whole_C10_status": "NOT_ASSESSED", "blockers": blocked,
        "counts": {key: None for key in ("eligible", "matched", "attempted", "valid", "unavailable", "tp", "fp", "fn")},
        "precision": None, "recall": None, "valid_rate": None,
        "horizontal_median_m": None, "horizontal_p95_m": None,
        "not_assessed_here": ["freeze ordering provenance", "reset clock/origin rebuilding", "source clock residual calibration", "independent optical_z/ray_range calibration"],
    }
    if blocked:
        return report
    manifest = json.loads(samples_path.read_text())
    labels = json.loads(annotations_path.read_text())
    if manifest.get("target_reference") != "visible_surface_horizontal" or labels.get("position_reference") != "visible_surface_horizontal":
        blocked.append("Reference must explicitly be visible_surface_horizontal; feet/ground origins cannot substitute")
    frames = manifest["frames"]
    if len({frame["frame_id"] for frame in frames}) != len(frames):
        blocked.append("Frozen frame list contains duplicates")
    by_frame: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for annotation in labels["annotations"]:
        by_frame[annotation["frame_id"]].append(annotation)
    archived = observations(run)
    errors, frame_rows, sample_rows, native_frames, observers, origins = [], [], [], 0, set(), set()
    totals = Counter(eligible=0, matched=0, attempted=0, valid=0, unavailable=0, tp=0, fp=0, fn=0)
    reasons: Counter[str] = Counter()
    for frame in frames:
        frame_id = frame["frame_id"]
        targets = by_frame[frame_id]
        target_ids = [target["annotation_id"] for target in targets]
        eligible = frame["eligible_annotation_ids"]
        if len(set(eligible)) != len(eligible) or len(set(target_ids)) != len(target_ids):
            blocked.append(f"Duplicate target identity in {frame_id}")
        record = archived.get(frame_id, {})
        native = record.get("source_clock") == "gazebo_sim" and record.get("source_time") is not None
        if not native:
            blocked.append(f"No native source-stamped local frame: {frame_id}")
        else:
            native_frames += 1
            observers.add(record["uav_id"])
            origin = record.get("vehicle_estimate", {}).get("local_ned_origin_world_enu_m")
            if origin is not None:
                origins.add(tuple(round(value, 6) for value in origin))
        analysis = record.get("last_analysis")
        if analysis is None:
            blocked.append(f"No actual recorded inference: {frame_id}")
        detections = [item for item in (analysis or {}).get("detections", []) if item.get("class") == "person" and item["score"] >= .5]
        match = match_detections(detections, [target["bbox_xyxy"] for target in targets], iou_threshold=.5)
        by_target = {targets[item["target_index"]]["annotation_id"]: item for item in match["matched"]}
        tp, fp, fn = len(match["matched"]), len(match["false_positive"]), len(match["unmatched_indices"])
        totals.update(tp=tp, fp=fp, fn=fn)
        frame_rows.append({"frame_id": frame_id, "native_source": native, "tp": tp, "fp": fp, "fn": fn,
                           "matches": match["matched"], "false_positive_detection_ids": match["false_positive"],
                           "missed_annotation_ids": [targets[index]["annotation_id"] for index in match["unmatched_indices"]],
                           "evidence_database": record.get("evidence_database"), "rgb_artifact_id": record.get("rgb_artifact_id")})
        for target_id in eligible:
            totals["eligible"] += 1
            row: dict[str, Any] = {"frame_id": frame_id, "annotation_id": target_id, "status": "unavailable"}
            matched = by_target.get(target_id)
            if target_id not in target_ids:
                reason = "eligible annotation missing from evaluator labels"
                blocked.append(f"{frame_id}: {target_id}: {reason}")
            elif matched is None:
                reason = "not detected/matched at score .5 and IoU .5"
            else:
                totals["matched"] += 1
                row["detection_id"] = matched["detection_id"]
                location = record.get("locations", {}).get(matched["detection_id"])
                if location is None:
                    reason = "localization not attempted"
                else:
                    totals["attempted"] += 1
                    target = targets[target_ids.index(target_id)]
                    reference = target.get("visible_surface_horizontal_enu_m")
                    position = location.get("position_enu_m")
                    if reference is None or len(reference) < 2 or not all(math.isfinite(v) for v in reference[:2]):
                        reason = "visible surface reference unavailable; no feet-origin fallback"
                        blocked.append(f"{frame_id}: {target_id}: {reason}")
                    elif location.get("status") != "available":
                        reason = location.get("reason", "localization unavailable")
                    elif position is None or len(position) < 2 or not all(math.isfinite(v) for v in position[:2]):
                        reason = "no finite estimated horizontal position"
                    elif location.get("target_reference") != "visible_person_surface_horizontal_position":
                        reason = "location uses a different target reference"
                        blocked.append(f"{frame_id}: {target_id}: {reason}")
                    else:
                        error = math.dist(position[:2], reference[:2])
                        errors.append(error)
                        totals["valid"] += 1
                        row.update(status="valid", horizontal_error_m=error, estimated_xy_m=position[:2], reference_xy_m=reference[:2])
                        reason = None
            if row["status"] != "valid":
                totals["unavailable"] += 1
                reasons[str(reason)] += 1
                row["reason"] = reason
            sample_rows.append(row)
    precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else None
    recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else None
    valid_rate = totals["valid"] / totals["eligible"] if totals["eligible"] else None
    median, p95 = percentile(errors, .5), percentile(errors, .95)
    if native_frames < 30:
        blocked.append(f"Only {native_frames} native held-out frames; at least 30 required")
    if totals["eligible"] < 30:
        blocked.append(f"Only {totals['eligible']} frozen eligible target samples; at least 30 required")
    if len(observers) < 2 or len(origins) < 2:
        blocked.append("Held-out samples must include two UAVs with distinct source-reported local origins")
    checks = {"precision_ge_0_90": precision is not None and precision >= .90,
              "recall_ge_0_85": recall is not None and recall >= .85,
              "valid_over_all_eligible_ge_0_90": valid_rate is not None and valid_rate >= .90,
              "horizontal_median_le_2m": median is not None and median <= 2.,
              "horizontal_p95_le_5m": p95 is not None and p95 <= 5.}
    report.update(status="BLOCKED" if blocked else "PASS" if all(checks.values()) else "FAIL",
                  counts=dict(totals), frame_count=len(frames), native_frame_count=native_frames,
                  observer_ids=sorted(observers), distinct_origin_count=len(origins),
                  precision=precision, recall=recall, valid_rate=valid_rate, horizontal_median_m=median,
                  horizontal_p95_m=p95, thresholds=checks, unavailable_reasons=dict(reasons),
                  per_frame=frame_rows, per_eligible_sample=sample_rows)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--inference-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path; previous evidence is retained")
    try:
        report = score(args.samples, args.annotations, args.inference_run)
    except (OSError, ValueError, KeyError, sqlite3.DatabaseError) as error:
        report = {"status": "BLOCKED", "scope": "native perception metrics only", "blockers": [str(error)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in {"per_frame", "per_eligible_sample"}}, ensure_ascii=False))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
