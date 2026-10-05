#!/usr/bin/env python3
"""Describe a recorded engineering inspection flight; never assign formal scores."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def inspect(trace: dict, configs: dict[str, dict]) -> dict:
    if trace["execution_scope"] != "executor_validation":
        raise ValueError("inspection visualization summary requires engineering evidence")
    states = trace["scene_states"]
    if not states or [s["at"]["tick"] for s in states] != list(range(1, trace["time"]["tick"] + 1)):
        raise ValueError("recorded SceneState coverage is incomplete")
    summaries = {}
    for event in trace["events"]:
        if event["interaction_type"] == "agent.decision_summary.v1":
            for item in event["public_payload"]:
                if item["name"] == "decision_summary":
                    summaries[event["agent_id"]] = item["value"]
    vehicles = []
    for agent_id, config in configs.items():
        if config["mission_mode"] != "inspection":
            raise ValueError("agent configuration is not an inspection mission")
        if config["final_tick"] != trace["time"]["tick"]:
            raise ValueError("recorded horizon differs from the bound participant configuration")
        entity_id = config["vehicle_id"]
        samples = [next(s for s in state["samples"] if s["entity_id"] == entity_id) for state in states]
        final = samples[-1]
        flags = {a["name"]: a["value"] for a in final["attributes"]}
        patrol, launch = config["patrol_enu_m"], config["launch_enu_m"]

        def distance(sample: dict, target: dict) -> float:
            enu = sample["pose"]["position"]["enu"]
            return math.hypot(enu["east_m"] - target["x"], enu["north_m"] - target["y"])

        # Contiguous recorded occupancy at altitude: no interpolated dwell samples.
        start = None
        longest_dwell_ns = 0
        arrival = None
        for sample in samples:
            at = sample["at"]["sim_time_ns"]
            inside = distance(sample, patrol) <= 1.5 and abs(sample["pose"]["position"]["agl_m"] - config["cruise_agl_m"]) <= 1.0
            if inside:
                if start is None:
                    start = at
                if arrival is None:
                    arrival = sample["at"]
                longest_dwell_ns = max(longest_dwell_ns, at - start)
            else:
                start = None
        summary = summaries.get(agent_id, "")
        marker = "actual_state="
        actual = json.loads(summary.split(marker, 1)[1]) if marker in summary else None
        policy_done = actual is not None and actual["mission_phase"] == "done" and actual["active_flight_command_id"] is None and actual["failed_command_count"] == 0 and actual["mission_failure_count"] == 0 and not actual["flight_progression_stopped"] and actual["airspace_state"] == "outside"
        physical_done = final["armed"] is False and flags.get("landed") is True and flags.get("in_air") is False and flags.get("ground_contact") is True and flags.get("collision_contact") is False and distance(final, launch) <= 1.5 and final["pose"]["position"]["agl_m"] <= 0.5
        completed = policy_done and physical_done and longest_dwell_ns >= 5_000_000_000
        modes = []
        for sample in samples:
            if not modes or modes[-1]["mode"] != sample["mode"]:
                modes.append({"at": sample["at"], "mode": sample["mode"]})
        vehicles.append({
            "entity_id": entity_id, "agent_id": agent_id, "completed": completed,
            "inspection_arrival": arrival, "recorded_inspection_dwell_seconds": longest_dwell_ns / 1e9,
            "return_distance_m": distance(final, launch), "final_armed": final["armed"],
            "final_flags": flags, "final_sample_digest": final["sample_digest"],
            "max_agl_m": max(s["pose"]["position"]["agl_m"] for s in samples),
            "recorded_modes": modes, "final_policy_state": actual,
            "final_battery_fraction": final["battery"]["remaining_fraction"] if final["battery"] else None,
        })
    return {"run_id": trace["run_id"], "execution_scope": trace["execution_scope"], "formal_benchmark_pass": False,
            "inspection_flight_complete": len(vehicles) == 2 and all(v["completed"] for v in vehicles) and trace["terminal"]["kind"] == "completed",
            "recorded_time": trace["time"], "vehicles": vehicles,
            "scope": "recorded flight inspection: target visit, >=5s dwell, return, land, disarm; no defect detection or formal scoring"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.trace.read_bytes()
    trace = json.loads(raw)
    resolved = json.loads((args.bundle / "resolved-run.json").read_bytes())
    manifest = json.loads((args.trace.parent / "replay/replay-manifest.json").read_bytes())
    trace_digest = hashlib.sha256(raw).hexdigest()
    if not (trace["run_id"] == resolved["run_id"] == manifest["run_id"] and trace["scenario_digest"] == resolved["scenario"]["scenario_digest"] == manifest["scenario_digest"] and trace_digest == manifest["trace_sha256"]):
        raise ValueError("trace, publication and resolved inputs do not share an exact identity")
    configs = {agent: json.loads((args.bundle / "agents" / f"{agent}.json").read_text()) for agent in ("uav.policy.01", "uav.policy.02")}
    result = inspect(trace, configs)
    result["public_trace_sha256"] = trace_digest
    result["trace_path"] = str(args.trace)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"inspection_flight_complete": result["inspection_flight_complete"], "output": str(args.output)}))
    if not result["inspection_flight_complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
