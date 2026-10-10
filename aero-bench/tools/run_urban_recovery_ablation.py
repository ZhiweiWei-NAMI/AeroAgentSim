#!/usr/bin/env python3
"""Build/run real engineering variants and summarize only their recorded output."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.runner.execution import load_runner_config, run_suite  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from tools.build_urban_recovery_demo import build, parser as build_parser  # noqa: E402

VARIANTS = ("baseline", "direct-recovery", "no-recovery")
DEFERRED = ("native_physics_audit", "toolchain_audit", "formal_benchmark_scoring", "visual_scoring")


def _write_new(path: Path, document: object) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(document) + b"\n")


def recorded_result(trace: dict) -> dict:
    """Descriptive measurements, not a mission verdict or reconstructed truth."""
    final_states = trace["scene_states"][-1]["samples"] if trace["scene_states"] else []
    distances = {}
    for trajectory in trace["trajectories"]:
        if trajectory["entity_id"] not in {"uav.01", "uav.02"}:
            continue
        points = [sample["pose"]["position"]["enu"] for sample in trajectory["samples"]]
        distances[trajectory["entity_id"]] = sum(
            math.dist(tuple(left[axis] for axis in ("east_m", "north_m", "up_m")),
                      tuple(right[axis] for axis in ("east_m", "north_m", "up_m")))
            for left, right in zip(points, points[1:])
        )
    decisions = {}
    for event in trace["events"]:
        if event["interaction_type"] == "agent.decision_summary.v1":
            for item in event["public_payload"]:
                if item["name"] == "decision_summary":
                    decisions[event["agent_id"]] = {"at": event["at"], "recorded_summary": item["value"]}
    return {
        "recorded_time": trace["time"],
        "recorded_terminal": trace["terminal"],
        "provider_status": trace["provider_status"],
        "sampled_uav_distance_m": distances,
        "distance_semantics": "sum between recorded 200ms samples; not interpolated flight distance",
        "final_uav_states": [sample for sample in final_states if sample["entity_id"] in {"uav.01", "uav.02"}],
        "last_agent_decisions": decisions,
        "network_event_counts": dict(sorted(Counter(event["state"] for event in trace["network_events"]).items())),
        "mission_verdict": "not_scored",
    }


def markdown_result_report(report: dict) -> str:
    rows = [
        "# Urban engineering ablation", "",
        "Unscored real-provider execution; not a formal benchmark pass.", "",
        f"Browser acceptance: {report['browser_acceptance']}; formal acceptance: {report['formal_acceptance']}.",
        "Deferred: " + ", ".join(DEFERRED) + ".", "",
        "| Variant | Status | Declared / recorded time (s) | UAV 01 sampled path (m) | UAV 02 sampled path (m) | Replay |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in report["results"]:
        recorded = item["recorded_result"]
        declared = str(item["duration_ns"] / 1e9) if "duration_ns" in item else "not built"
        seconds = str(recorded["recorded_time"]["sim_time_ns"] / 1e9) if recorded else "not recorded"
        distances = recorded["sampled_uav_distance_m"] if recorded else {}
        paths = [f"{distances[vehicle]:.2f}" if vehicle in distances else "not recorded" for vehicle in ("uav.01", "uav.02")]
        replay = f'[{item["variant"]}]({item["replay_manifest"]})' if item["replay_manifest"] else "not published"
        rows.append(f'| {item["variant"]} | {item["status"]} | {declared} / {seconds} | {paths[0]} | {paths[1]} | {replay} |')
    rows.extend(["", "Distances sum consecutive recorded 200 ms samples; they are not interpolated flight distance or a mission score."])
    for item in report["results"]:
        recorded = item["recorded_result"]
        providers = ", ".join(f"{status['provider_id']}={status['state']}" for status in recorded["provider_status"]) if recorded else ""
        agents = ", ".join(sorted(recorded["last_agent_decisions"])) if recorded else ""
        network = json.dumps(recorded["network_event_counts"], sort_keys=True) if recorded else "not recorded"
        rows.extend([
            "", f"## {item['variant']}", "",
            "- Configured components (not proof of execution): " + ", ".join(item["configured_components"]),
            "- Recorded Provider states: " + (providers or "not recorded"),
            "- Agents with recorded decisions: " + (agents or "not recorded"),
            "- Recorded network event counts: " + network,
            "- Replay status: " + item["replay_status"],
            f"- [Recorded details and final Agent summaries]({item['variant']}.result.json)",
        ])
        if item.get("engineering_ground") is not None:
            rows.append("- Physical ground: derived from the declared flat simulation terrain; not measured Shanghai terrain. Its input provenance is included in the result JSON.")
        if item.get("failure_classes"):
            rows.append("- Execution failures: " + ", ".join(item["failure_classes"]))
        if item.get("failure_diagnostic"):
            rows.append(f"- [Private execution diagnostic]({item['failure_diagnostic']}); not a viewer asset.")
    return "\n".join(rows) + "\n"


def run(args: argparse.Namespace) -> dict:
    if args.profile != "engineering":
        raise ValueError("ablation orchestration is engineering-only; use the standalone builder for formal")
    variants = (args.variant,) if args.variant else tuple(args.variants)
    if len(set(variants)) != len(variants):
        raise ValueError("variants must be unique")
    if not args.build_only and not args.runner_config:
        raise ValueError("--runner-config is required unless --build-only is explicit")
    template = load_runner_config(args.runner_config) if not args.build_only else None
    output = Path(args.output).absolute()
    # Never reuse an experiment directory, even if a previous attempt failed.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    (output / "runs").mkdir(mode=0o700)
    results = []
    expected_world_digest = None
    for variant in variants:
        result = {
            "variant": variant, "execution_profile": "engineering",
            "execution_scope": "executor_validation", "benchmark_scored": False,
            "configured_components": ["flight", "network", "traffic", "uav.policy.01", "uav.policy.02", "groundstation.rule"],
            "deferred_checks": list(DEFERRED), "execution_complete": False,
            "status": "not_started", "recorded_result": None, "replay_manifest": None,
            "replay_status": "not_published",
        }
        try:
            build_args = argparse.Namespace(**vars(args))
            build_args.output = str(output / "inputs" / variant)
            build_args.variant = variant
            bundle = build(build_args)
            lock = json.loads((bundle / "release-lock.json").read_bytes())
            if expected_world_digest is None:
                expected_world_digest = lock["world_digest"]
            elif lock["world_digest"] != expected_world_digest:
                raise ValueError("ablation variants do not share the same world")
            result.update({"run_id": lock["run_id"], "world_digest": lock["world_digest"],
                           "input_bundle": str(bundle.relative_to(output)),
                           "engineering_ground": lock.get("engineering_ground"),
                           "duration_ns": lock["duration_ns"], "status": "inputs_ready_not_executed"})
            if template is not None:
                run_output = output / "runs" / variant
                config = template.model_copy(update={"output_root": str(run_output)})
                config_path = output / f"{variant}.runner.json"
                _write_new(config_path, config.model_dump(mode="json"))
                summary = run_suite(bundle / "suite.yaml", config_path)
                executed = summary.runs[0]
                result.update({"status": executed.status, "execution_complete": executed.execution_complete,
                               "preflight": executed.preflight.model_dump(mode="json"),
                               "failure_classes": list(executed.failure_classes),
                               "runner_summary": str((run_output / "runner-summary.json").relative_to(output))})
                run_root = run_output / executed.run_id
                diagnostic = run_root / "failure-diagnostic.json"
                if diagnostic.is_file():
                    result["failure_diagnostic"] = str(diagnostic.relative_to(output))
                if executed.public_trace is not None:
                    raw = (run_root / executed.public_trace.relative_path).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != executed.public_trace.sha256:
                        raise ValueError("published trace changed since Runner publication")
                    result["recorded_result"] = recorded_result(json.loads(raw))
                    result["replay_manifest"] = str((run_root / executed.public_trace.replay.relative_path).relative_to(output))
                    result["replay_status"] = "published_not_browser_checked"
                if executed.execution_complete:
                    result["status"] = "engineering_complete_unscored"
        except KeyboardInterrupt:
            result.update({"status": "cancelled", "execution_complete": False})
            results.append(result)
            _write_new(output / f"{variant}.result.json", result)
            break
        except Exception as error:
            # Keep failed variants visible; never substitute fixture output.
            result.update({"status": "error", "execution_complete": False,
                           "error": f"{type(error).__name__}: {error}"})
        results.append(result)
        _write_new(output / f"{variant}.result.json", result)
    complete = len(results) == len(variants) and all(item["execution_complete"] for item in results)
    report = {
        "schema_version": "aero-bench.urban-engineering-ablation/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_profile": "engineering", "benchmark_scored": False,
        "engineering_execution_complete": complete,
        "status": "engineering_complete_unscored" if complete else "inputs_only" if args.build_only and all(item["status"] == "inputs_ready_not_executed" for item in results) else "incomplete",
        "requested_variants": list(variants), "results": results,
        "browser_acceptance": "not_run", "formal_acceptance": "not_run",
    }
    _write_new(output / "ablation-summary.json", report)
    with (output / "RESULTS.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown_result_report(report))
    print(canonical_json_bytes({"output": str(output), "status": report["status"]}).decode())
    return report


def parser() -> argparse.ArgumentParser:
    result = build_parser()
    result.description = "Build/run baseline and policy ablations with real Providers; never score engineering output"
    result.set_defaults(variant=None, output=str(ROOT / "validation" / ("urban-engineering-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))))
    result.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    result.add_argument("--runner-config", help="existing Docker RunnerConfig; output_root is replaced with a fresh per-variant path")
    result.add_argument("--build-only", action="store_true", help="explicitly skip execution and label results inputs-only")
    return result


if __name__ == "__main__":
    cli = parser()
    try:
        arguments = cli.parse_args()
        report = run(arguments)
        raise SystemExit(0 if report["engineering_execution_complete"] or report["status"] == "inputs_only" else 1)
    except (OSError, ValueError) as error:
        cli.error(str(error))
