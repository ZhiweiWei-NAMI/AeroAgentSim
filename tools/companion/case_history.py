"""Concrete retained-evidence rescue case actions."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_observations(database: Path) -> list[dict]:
    if not database.is_file():
        return []
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        try:
            rows = connection.execute("SELECT metadata FROM observations ORDER BY observed_at,frame_id").fetchall()
        except sqlite3.OperationalError:
            return []
    return [json.loads(row[0]) for row in rows]


def spatial_bounds(record: dict) -> list[float] | None:
    points = record.get("footprint_enu", [])
    if not points:
        return None
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def filter_matches(record: dict, request: dict) -> bool:
    """Independent metadata checks; spatial semantics are candidate bbox only."""
    when = record["observed_at"]
    if when < request.get("observed_after_ns", when) or when > request.get("observed_before_ns", when):
        return False
    if any(request.get(key) is not None and record.get(field) != request[key]
           for key, field in (("observer", "uav_id"), ("source", "source"))):
        return False
    if request.get("category") and request["category"] not in record.get("categories", []):
        return False
    if request.get("region_enu") is not None:
        bounds = spatial_bounds(record)
        x1, y1, x2, y2 = request["region_enu"]
        if bounds is None or bounds[2] < x1 or bounds[0] > x2 or bounds[3] < y1 or bounds[1] > y2:
            return False
    return True


def build_queries(records: list[dict]) -> dict:
    """Freeze actual-record relevance before running any retrieval queries.

    Several distinct intents may legitimately refer to one frame. No minimum
    frame count is added to the specification, and no synthetic records are made.
    """
    if not records:
        return {"status": "BLOCKED", "positive": [], "absence": [],
                "missing_material": ["No actual recorded observations"]}
    cutoff = max(row["observed_at"] for row in records)
    times = sorted({row["observed_at"] for row in records})
    groups: dict[str, list[dict]] = {name: [] for name in ("time", "window_source", "space", "chinese_context")}
    seen: set[str] = set()

    def add(group: str, question: str, request: dict, relevant: list[dict], basis: str) -> None:
        request = {"observed_before_ns": cutoff, **request, "limit": 5}
        expected = [row for row in relevant if filter_matches(row, request)]
        key = json.dumps(request, sort_keys=True)
        if not expected or key in seen:
            return
        seen.add(key)
        groups[group].append({"intent": group, "question_zh": question, "request": request,
            "expected": [{key: row[key] for key in ("frame_id", "rgb_sha256", "observed_at", "uav_id")} for row in expected],
            "relevance_basis": basis})

    for record in records:
        when, observer = record["observed_at"], record["uav_id"]
        at = times.index(when)
        earlier = times[max(0, at - 1)]
        add("time", f"{observer}在模拟时刻{when}纳秒采集了哪些历史画面？",
            {"observer": observer, "observed_after_ns": when, "observed_before_ns": when}, records, "actual acquisition timestamp and observer")
        if earlier < when:
            add("window_source", f"从{earlier}到{when}纳秒，{observer}的{record['source']}来源档案有哪些？",
                {"observer": observer, "source": record["source"], "observed_after_ns": earlier, "observed_before_ns": when},
                records, "actual closed time interval and stored source")
        bounds = spatial_bounds(record)
        if bounds:
            add("space", f"截至{when}纳秒，{observer}有哪些观察范围与ENU矩形{bounds}重叠的候选画面？",
                {"observer": observer, "region_enu": bounds, "observed_before_ns": when}, records,
                "actual depth-derived footprint bounding-box overlap; candidate filter, not exact visible coverage")
        if record.get("description") and "last_analysis" in record:
            positive = bool(record["last_analysis"].get("detections"))
            phrase = "人员检测候选" if positive else "未检出人员"
            relevant = [row for row in records if row.get("description") and "last_analysis" in row
                        and bool(row["last_analysis"].get("detections")) == positive]
            add("chinese_context", f"在{earlier}到{when}纳秒的{observer}档案中，查找文字描述为“{phrase}”的画面；这不是对图中真实人数的判定。",
                {"observer": observer, "query": phrase, "observed_after_ns": earlier, "observed_before_ns": when},
                relevant, "actual stored detector description; BGE is not assumed to understand undescribed pixels")
    selected = []
    while len(selected) < 20 and any(groups.values()):
        for group in groups.values():
            if group and len(selected) < 20:
                selected.append(group.pop(0))
    for index, query in enumerate(selected, 1):
        query["query_id"] = f"positive-{index:02d}"
    unknown_observer = "unrecorded-history-observer"
    while any(row["uav_id"] == unknown_observer for row in records):
        unknown_observer += "-absent"
    unknown_category = "boat"
    while any(unknown_category in row.get("categories", []) for row in records):
        unknown_category += "-unrecorded"
    minimum_time = min(times)
    bounds = [value for row in records if (value := spatial_bounds(row)) is not None]
    absent = [
        ("档案中有从未记录的观察者拍摄的画面吗？", {"observer": unknown_observer}, "observer absent from frozen records"),
        ("首次采集以前的这个时间窗里有历史画面吗？", {"observed_after_ns": minimum_time - 2, "observed_before_ns": minimum_time - 1}, "window strictly before every retained source timestamp"),
        (f"历史中有被标为{unknown_category}类别的检测记录吗？", {"category": unknown_category}, "category absent from actual stored analyses"),
        ("有没有关于地铁车票退款的文字记录？", {"query": "地铁车票退款"}, "unrelated to retained detector descriptions; semantic false matches count as failures"),
    ]
    if bounds:
        east, north = max(b[2] for b in bounds) + 10, max(b[3] for b in bounds) + 10
        absent.append(("有观察范围覆盖这个档案范围以外区域的画面吗？",
                       {"region_enu": [east, north, east + 1, north + 1]}, "region disjoint from every retained footprint bounding box"))
    else:
        unknown_source = "unrecorded-source"
        while any(row["source"] == unknown_source for row in records):
            unknown_source += "-absent"
        absent.append(("有来自未使用采集来源的历史记录吗？", {"source": unknown_source}, "source absent from frozen records"))
    absence = [{"query_id": f"absence-{index:02d}", "question_zh": question,
                "request": {"observed_before_ns": cutoff, **request, "limit": 5}, "expected": [], "relevance_basis": basis}
               for index, (question, request, basis) in enumerate(absent, 1)]
    missing = []
    if any("地铁" in row.get("description", "") or "退款" in row.get("description", "") for row in records):
        missing.append("Frozen unrelated-text query may concern this archive; manual relevance review is needed, query was not substituted")
    if len(selected) < 20:
        missing.append(f"Only {len(selected)} distinct evidence-backed query intents could be built; need 20")
    if not any(item["intent"] == "chinese_context" for item in selected):
        missing.append("No actual analysis description for a Chinese text retrieval query")
    if not bounds:
        missing.append("No actual camera observation footprint for spatial retrieval or old-frame review")
    return {"status": "BLOCKED" if missing else "READY", "positive": selected, "absence": absence,
            "frozen_observed_through_ns": cutoff, "record_count": len(records), "missing_material": missing,
            "scope": "record-derived Chinese/time/source/candidate-spatial retrieval; not an independently human-labeled semantic benchmark",
            "whole_C06_status": "NOT_ASSESSED: cross-UAV radio-copy isolation is a separate required check"}


def assess_query(query: dict, result: dict, archive: list[dict]) -> dict:
    actual = {row["frame_id"]: row for row in archive}
    returned = result.get("records", [])
    violations = []
    fabricated = []
    if len(returned) > 5:
        violations.append("More than five records returned")
    for row in returned:
        original = actual.get(row.get("frame_id"))
        if original is None:
            fabricated.append(row.get("frame_id"))
            violations.append("Returned frame is not in this UAV's actual archive")
        elif any(row.get(key) != original.get(key) for key in ("rgb_sha256", "observed_at", "uav_id", "source")):
            violations.append("Returned frame identity/hash/source time differs from archive")
        elif not filter_matches(original, query["request"]):
            violations.append("Returned record violates an explicit filter")
    expected = {(row["frame_id"], row["rgb_sha256"]) for row in query["expected"]}
    hit = any((row.get("frame_id"), row.get("rgb_sha256")) in expected for row in returned[:5])
    semantic_unavailable = bool(query["request"].get("query")) and result.get("semantic_backend") != "local BGE"
    success = result.get("status") == "succeeded" and not violations and not semantic_unavailable and (hit if expected else not returned)
    return {"query_id": query["query_id"], "success": success,
            "top5_hit": hit and result.get("status") == "succeeded" and not violations if expected else None,
            "fabricated_record_ids": fabricated, "semantic_backend_unavailable": semantic_unavailable,
            "retrieval_false_positive": not expected and any(row.get("frame_id") in actual for row in returned),
            "filter_or_provenance_violations": violations, "actual_result": result}


def run_history_case(
    run: Path, command: list[str], env: dict[str, str], timeout: float
) -> None:
    """Exercise the retained archive through its existing native CLI worker."""
    deadline = time.monotonic() + timeout
    database = run / "data/u2/index.sqlite"
    state_path = run / "data/u2/runtime-state.json"
    outcomes: dict[str, list[dict]] = {}
    summary: dict = {
        "status": "BLOCKED", "reason": "Native acquisition has not completed",
        "whole_C06_status": "NOT_ASSESSED: cross-UAV isolation/radio-copy check remains separate",
        "thresholds": {"positive_queries": 20, "top5_hits": 18, "absence_queries": 5},
    }

    def execute(stage: str, suffix: list[str]) -> str:
        try:
            result = subprocess.run(
                command + suffix, cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=max(0.1, deadline - time.monotonic()), check=False,
            )
        except subprocess.TimeoutExpired as error:
            partial = b"".join(
                value if isinstance(value, bytes) else value.encode()
                for value in (error.stdout, error.stderr) if value
            )
            (run / (stage + ".log")).write_bytes(partial)
            raise
        (run / (stage + ".log")).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"C06 {stage} exited {result.returncode}; see its log")
        return result.stdout

    def query(stage: str, request: dict, tool: str = "retrieve-sightings") -> dict:
        if time.monotonic() >= deadline:
            raise TimeoutError("C06 wall-time limit reached before this query")
        cli = ["exec", "-T", "uav-u2", "python", "-m", "aeroagentsim.companion.tools"]
        queued = json.loads(execute(stage + "-queued", cli + [
            tool, "--data-dir", "/data", "--request", json.dumps(request),
        ]))
        result_path = run / "data/u2/tasks/tool-requests" / (queued["request_id"] + ".result.json")
        while time.monotonic() < deadline and not result_path.is_file():
            time.sleep(0.2)
        if not result_path.is_file():
            raise TimeoutError("C06 existing worker did not finish the queued tool")
        # Failed/unavailable real tool results are evidence too. Do not discard.
        return json.loads(execute(stage, cli + ["--data-dir", "/data", "--status", queued["request_id"]]))

    def batch(phase: str, queries: list[dict], archive: list[dict]) -> dict:
        outcomes[phase] = []
        for item in queries:
            try:
                result = query(f"history-{phase}-{item['query_id']}", item["request"])
                outcome = assess_query(item, result, archive)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                outcome = {"query_id": item["query_id"], "success": False,
                           "actual_result": None, "execution_error": str(error)}
            outcomes[phase].append(outcome)
            write_json(run / "retrieval-results.json", outcomes)
        positives = [row for row in outcomes[phase] if row["query_id"].startswith("positive-")]
        absent = [row for row in outcomes[phase] if row["query_id"].startswith("absence-")]
        return {
            "positive_queries": len(positives),
            "positive_queries_executed": sum(row.get("actual_result") is not None for row in positives),
            "absence_queries_executed": sum(row.get("actual_result") is not None for row in absent),
            "top5_hits": sum(row.get("top5_hit") is True for row in positives),
            "absence_queries": len(absent), "correct_empty_answers": sum(row["success"] for row in absent),
            "retrieval_false_positives": sum(row.get("retrieval_false_positive", False) for row in absent),
            "fabricated_record_ids": [frame for row in outcomes[phase] for frame in row.get("fabricated_record_ids", [])],
            "filter_or_provenance_violations": [issue for row in outcomes[phase] for issue in row.get("filter_or_provenance_violations", [])],
            "execution_errors": [row["query_id"] for row in outcomes[phase] if row.get("execution_error")],
            "tool_failures": [row["query_id"] for row in outcomes[phase]
                              if row.get("actual_result") is not None and row["actual_result"].get("status") != "succeeded"],
            "semantic_backend_unavailable": [row["query_id"] for row in outcomes[phase] if row.get("semantic_backend_unavailable")],
        }

    def lookup(phase: str, frame: dict, archive: list[dict]) -> dict:
        expected = [{key: frame[key] for key in ("frame_id", "rgb_sha256", "observed_at", "uav_id")}]
        requests = [{"query_id": "old-frame-time", "request": {
            "observed_after_ns": frame["observed_at"], "observed_before_ns": frame["observed_at"],
            "observer": frame["uav_id"], "limit": 5}, "expected": expected}]
        bounds = spatial_bounds(frame)
        if bounds:
            requests.append({"query_id": "old-frame-observed-area", "request": {
                "region_enu": bounds, "observed_after_ns": frame["observed_at"],
                "observed_before_ns": frame["observed_at"], "observer": frame["uav_id"], "limit": 5},
                "expected": expected})
        batch(phase, requests, archive)
        return {"status": "PASS" if bounds and all(row["success"] for row in outcomes[phase]) else "BLOCKED" if not bounds else "FAIL",
                "footprint_available": bounds is not None,
                "spatial_scope": "actual footprint bounding-box candidate filter plus acquisition time"}

    corpus = None
    try:
        execute("history-start", ["up", "-d", "--no-build", "--pull", "never", "simulator", "radio", "coordinator", "uav-u2"])
        state = {}
        while time.monotonic() < deadline:
            if state_path.is_file():
                state = json.loads(state_path.read_text())
                if state.get("results", {}).get("survey") or state.get("task_blocker"):
                    break
            time.sleep(0.2)
        records = read_observations(database)
        summary["acquisition"] = {"survey": state.get("results", {}).get("survey"),
                                  "task_blocker": state.get("task_blocker"), "record_count": len(records)}
        candidates = [row for row in records if not row.get("last_analysis", {}).get("detections")]
        frame = next((row for row in candidates if spatial_bounds(row)), candidates[0] if candidates else None)
        if frame is not None:
            # Reserve an original no-detection frame until the independent old-frame check.
            write_json(run / "history-selected-frame.json", frame)
        # Native capture has no textual description until the real detector runs.
        # Analyze a few spatially/time-separated retained frames, never invent captions.
        pending = [row for row in records if row != frame and "last_analysis" not in row]
        count = min(5, len(pending))
        indices = sorted({index * (len(pending) - 1) // max(1, count - 1) for index in range(count)})
        descriptions = []
        for index in indices:
            record = pending[index]
            try:
                result = query(f"history-describe-{index}", {"frame_id": record["frame_id"]}, "detect-persons")
                descriptions.append({"frame_id": record["frame_id"], "actual_result": result})
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                descriptions.append({"frame_id": record["frame_id"], "actual_result": None, "execution_error": str(error)})
            write_json(run / "history-description-results.json", descriptions)
        records = read_observations(database)
        corpus = build_queries(records)
        # Frozen before either retrieval batch; failed queries are never substituted.
        write_json(run / "history-corpus-observations.json", records)
        write_json(run / "queries.json", corpus)
        summary["corpus"] = {key: value for key, value in corpus.items() if key not in {"positive", "absence"}}
        queries = corpus["positive"] + corpus["absence"]
        summary["before_restart"] = batch("before-restart", queries, records)
        if frame is not None:
            summary["old_frame_before_restart"] = lookup("old-before", frame, records)

        before = json.loads(state_path.read_text())
        write_json(run / "runtime-before-restart.json", before)
        execute("history-restart", ["restart", "--timeout", "10", "uav-u2"])
        restart_frontier = json.loads(state_path.read_text())["sim_ns"]
        while time.monotonic() < deadline:
            after = json.loads(state_path.read_text())
            if after["sim_ns"] > restart_frontier:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("Restarted companion did not resume its persisted run")
        write_json(run / "runtime-after-restart.json", after)
        archive_after = read_observations(database)
        identities_after = {(row["frame_id"], row["rgb_sha256"], row["observed_at"]) for row in archive_after}
        restored = (
            all(after[key] == before[key] for key in ("run_id", "uav_id", "task"))
            and after["step_index"] >= before["step_index"]
            and set(before["seen_messages"]).issubset(after["seen_messages"])
            and all((row["frame_id"], row["rgb_sha256"], row["observed_at"]) in identities_after for row in records)
        )
        summary["persisted_state_restored"] = restored
        summary["after_restart"] = batch("after-restart", queries, archive_after)
        if frame is None:
            summary["old_frame_reanalysis"] = {"status": "BLOCKED", "reason": "No actual original frame without a detection record"}
        else:
            summary["old_frame_after_restart"] = lookup("old-after", frame, archive_after)
            result = query("history-reanalysis", {"reanalyze_frame_id": frame["frame_id"]})
            write_json(run / "historical-reanalysis.json", result)
            updated = next(row for row in read_observations(database) if row["frame_id"] == frame["frame_id"])
            preserved = (
                result.get("status") == "succeeded"
                and result.get("original_rgb_sha256") == frame["rgb_sha256"]
                and result.get("frame_id") == frame["frame_id"]
                and result.get("observed_at") == frame["observed_at"]
                and all(updated[key] == frame[key] for key in ("frame_id", "rgb_sha256", "observed_at"))
                and result.get("analyzed_at") is not None
                and result["analyzed_at"] > max(frame["observed_at"], frame.get("analyzed_at", frame["observed_at"]))
                and updated.get("analyzed_at") == result["analyzed_at"]
            )
            summary["old_frame_reanalysis"] = {"status": "PASS" if preserved else "FAIL",
                "frame_id": frame["frame_id"], "observed_at": frame["observed_at"], "analyzed_at": result.get("analyzed_at"),
                "detection_count": len(result.get("detections", [])), "detection_required": False}
        phases = (summary["before_restart"], summary["after_restart"])
        passed = all(
            phase["positive_queries"] == 20 and phase["top5_hits"] >= 18
            and phase["absence_queries"] == 5 and phase["correct_empty_answers"] == 5
            and not any(phase[key] for key in ("fabricated_record_ids", "filter_or_provenance_violations", "execution_errors", "semantic_backend_unavailable"))
            for phase in phases
        )
        old_checks = all(summary.get(key, {}).get("status") == "PASS" for key in (
            "old_frame_before_restart", "old_frame_after_restart", "old_frame_reanalysis"))
        summary.update(status="BLOCKED" if corpus["status"] == "BLOCKED" or frame is None else "PASS" if passed and restored and old_checks else "FAIL",
                       reason="This component checks retrieval/restart/old-frame reanalysis only; all actual outcomes retained")
        if summary["status"] != "PASS":
            raise RuntimeError(f"C06 history component {summary['status']}; see history-query-summary.json")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        summary["execution_error"] = str(error)
        raise
    finally:
        if corpus is None:
            records = read_observations(database)
            write_json(run / "history-corpus-observations.json", records)
            write_json(run / "queries.json", build_queries(records))
        write_json(run / "retrieval-results.json", outcomes)
        write_json(run / "history-query-summary.json", summary)
