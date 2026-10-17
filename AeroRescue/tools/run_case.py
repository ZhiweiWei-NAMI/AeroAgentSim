#!/usr/bin/env python3
"""Run one rescue case in a fresh Compose project; keep every failed attempt."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml
from case_flight import run_flight_case
from case_history import run_history_case
from case_replay import replay_retained

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs/execution-package/04_scenario_cases.yaml"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def cases() -> list[dict]:
    return yaml.safe_load(SPEC.read_text())["cases"]


def prepare_scene(
    run: Path,
    command: list[str],
    env: dict[str, str],
    case_id: str,
    variant: str | None,
) -> list[str]:
    """Create one public config plus simulator-only geometry for this attempt."""
    from prepare_world import build_world

    asset_dir = Path(env.get("ASSET_ROOT", ROOT / "deploy/assets")).resolve()
    if not (asset_dir / "makehuman_person/model.sdf").is_file():
        raise RuntimeError(
            "ASSET_ROOT must contain the prepared makehuman_person/model.sdf"
        )
    scene = yaml.safe_load(
        (ROOT / "scenarios/rooftop-flood-companion.yaml").read_text()
    )
    scene["seed"] = int(env["SEED"])
    if case_id == "C06":
        scene["vehicles"]["u2"]["initial_task"] = {
            "task_id": "history-survey-u2",
            "task_revision": 1,
            "workflow": [
                {"tool": "flight-command", "request": {"action": "arm"}},
                {
                    "tool": "flight-command",
                    "request": {"action": "takeoff", "params": {"altitude_m": 10}},
                },
                {
                    "tool": "search-area",
                    "request": {
                        "polygon": scene["public_map"]["search_polygon"],
                        "altitude_m": scene["public_map"]["search_altitude_m"],
                        "footprint_width_m": 15,
                    },
                    "result_key": "survey",
                },
            ],
        }
    if case_id in {"C03", "C07"}:
        scene["native_radio_fault"] = {"case_id": case_id, "variant": variant}
    if case_id == "C03":
        # A real near-base camera image exercises transport independently of
        # person-detection quality. The public rectangle avoids the roof barrier.
        scene["vehicles"]["u2"]["initial_task"] = {
            "task_id": "radio-image-u2",
            "task_revision": 1,
            "workflow": [
                {"tool": "flight-command", "request": {"action": "arm"}},
                {
                    "tool": "flight-command",
                    "request": {"action": "takeoff", "params": {"altitude_m": 10}},
                },
                {
                    "tool": "search-area",
                    "request": {
                        "polygon": [[-8, -8], [-1, -8], [-1, -1], [-8, -1]],
                        "altitude_m": 10,
                        "footprint_width_m": 15,
                    },
                    "result_key": "survey",
                },
                {
                    "tool": "package-evidence",
                    "request": {
                        "artifact_id": "$results.survey.frames.0.rgb_artifact_id",
                        "budget_bytes": 65536,
                    },
                    "result_key": "package",
                },
                {
                    "tool": "send-evidence",
                    "request": {
                        "artifact_id": "$results.package.artifact_id",
                        "destination": "centre",
                        "route": ["u2", "centre"],
                    },
                    "result_key": "transfer",
                },
            ],
        }
    if case_id == "C04":
        if variant == "payload_unavailable":
            scene.pop("payload", None)
            for vehicle in scene["vehicles"].values():
                vehicle["initial_payload_kg"] = 0.0
        elif variant == "low_energy_after_offer":
            u2, u4 = scene["vehicles"]["u2"], scene["vehicles"]["u4"]
            u2["energy_budget"] = {
                "initial_fraction": 0.23,
                "max_gap_ns": 1_000_000_000,
            }
            u4["capabilities"] = list(dict.fromkeys([*u4["capabilities"], "inspect"]))
            # Exchange public horizontal locations, preserving the loaded
            # carrier's ground clearance and each vehicle's declared attitude.
            u2["spawn"][:2], u4["spawn"][:2] = u4["spawn"][:2], u2["spawn"][:2]
        elif variant == "role_id_permutation":
            ids = list(scene["vehicles"])
            mapping = dict(zip(ids, ids[1:] + ids[:1]))
            rotated = {
                mapping[uid]: profile for uid, profile in scene["vehicles"].items()
            }
            scene["vehicles"] = {uid: rotated[uid] for uid in ids}
            scene["payload"]["carrier"] = mapping[scene["payload"]["carrier"]]
    if case_id == "C08":
        scene["incident"].update(
            discover_secondary_clue=True,
            secondary_clue_separation_m=15.0,
            secondary_clue_ttl_ns=5_000_000_000
            if variant == "deadline_expired"
            else 120_000_000_000,
        )
    geometry_variant = variant if case_id == "C02" else "default"
    build_world(scene, asset_dir.parent, int(env["SEED"]), 2, geometry_variant, run)
    env["WORLD_ROOT"] = str(run / "worlds")
    env["ASSET_ROOT"] = str(asset_dir)
    scene_path = run / "scene.yaml"
    scene_path.write_text(yaml.safe_dump(scene, sort_keys=False))
    services = {}
    for name in ("coordinator", "centre-agent", "uav-u1", "uav-u2", "uav-u3", "uav-u4"):
        services[name] = {
            "volumes": [str(scene_path) + ":/case/scene.yaml:ro"],
            "environment": {"RESCUE_CONFIG": "/case/scene.yaml"},
        }
    env["SCENARIO"] = "/case/scene.yaml"
    override = {"services": services}
    if case_id == "C09":
        override["networks"] = {"agents": {"internal": True}}
        for name in services:
            services[name]["environment"].update(
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                HF_HUB_DISABLE_TELEMETRY="1",
            )
    path = run / "case-compose.yaml"
    path.write_text(yaml.safe_dump(override, sort_keys=False))
    return command + ["-f", str(path)]


def low_energy_observations(run: Path) -> dict:
    """Read this branch's real plans, local budget and native/radio outcomes."""
    from verify_run import records

    def rows(path: str) -> list[dict]:
        target = run / "data" / path
        return records(target) if target.is_file() else []

    plans = [
        r["centre_plan"]
        for r in rows("centre/logs/decisions.jsonl")
        if r.get("centre_plan", {}).get("status") == "feasible"
    ]
    report = {
        "status": "BLOCKED",
        "reason": "Initial inspect assignment to u2 was not observed",
        "scope": "Observed low-energy replanning branch only; whole-case acceptance remains separate",
    }
    if not plans or not any(
        a["vehicle"] == "u2" and a["role"] == "inspect"
        for a in plans[0].get("assignments", [])
    ):
        return report
    first = plans[0]
    report["initial_plan"] = first
    ledger = rows("coordinator/radio/packet-ledger.jsonl")
    if not ledger:
        ledger = rows("coordinator/packet-ledger.jsonl")
    raw_deliveries = {
        r["payload"]["packet_id"]
        for r in rows("coordinator/native-radio-events.jsonl")
        if r.get("kind") == "delivery"
    }
    commits = {
        r["message_id"]: r for r in ledger if r.get("event") == "receiver_file_commit"
    }

    def arrived(mid: str) -> bool:
        ids = commits.get(mid, {}).get("native_packet_ids", [])
        return bool(ids) and set(ids).issubset(raw_deliveries)

    missing = []
    energy = [
        r
        for r in rows("u2/logs/energy-ledger.jsonl")
        if r.get("status") == "available"
        and r.get("source") == "declared_onboard_energy_model"
    ]
    before = [
        r
        for r in energy
        if r["effective_fraction"] >= 0.2
        and r["source_sim_ns"] <= first["planned_at_ns"]
    ]
    after = [
        r
        for r in energy
        if r["effective_fraction"] < 0.2
        and r["source_sim_ns"] > first["planned_at_ns"]
        and (r.get("interval") or {}).get("debited_energy_j", 0) > 0
    ]
    if not before or not after:
        missing.append(
            "No source-timed local consumption crossing from eligible to below 0.2 after selection"
        )
    report["energy_crossing"] = {
        "before": before[-1] if before else None,
        "after": after[0] if after else None,
    }
    centre_path = run / "data/centre/centre-state.json"
    incident = (
        json.loads(centre_path.read_text())
        .get("incidents", {})
        .get(first["event_id"], {})
        if centre_path.is_file()
        else {}
    )
    bid_id = first.get("selected_bids", {}).get("u2", {}).get("message_id")
    withdrawals = [
        r
        for r in incident.get("resource_withdrawals", [])
        if r.get("source") == "u2"
        and bid_id is not None
        and r.get("data", {}).get("bid_message_id") == bid_id
        and r.get("data", {}).get("local_energy", {}).get("status") == "available"
        and r["data"]["local_energy"]["effective_fraction"] < 0.2
        and after
        and r["received_ns"] >= after[0]["source_sim_ns"]
        and arrived(r["message_id"])
    ]
    if not withdrawals:
        missing.append("Selected u2 bid has no radio-delivered withdrawal at centre")
    report["withdrawals"] = withdrawals
    native = rows("coordinator/native-flight-events.jsonl")
    responses = {
        r["kernel_command_id"]: r
        for r in native
        if r.get("kind") == "cancel_response"
        and r.get("response", {}).get("status") == "accepted"
    }
    latest = {r["operation_id"]: r for r in rows("coordinator/flight/operations.jsonl")}
    canceled = []
    for operation in latest.values():
        if (
            operation.get("uav_id") != "u2"
            or operation.get("task_revision") != first["task_revision"]
            or operation.get("task_id") != first["event_id"] + "-u2"
            or operation.get("status") != "canceled"
        ):
            continue
        response = responses.get(operation.get("kernel_command_id"), {})
        canceled.extend(
            r
            for r in native
            if response
            and r.get("command_id") == response["command_id"]
            and r.get("status") == "canceled"
            and withdrawals
            and r["sim_ns"] >= withdrawals[0]["received_ns"]
        )
    if not canceled:
        missing.append("No actual native cancellation of u2's selected task")
    reports = [
        r
        for r in incident.get("reports", [])
        if r.get("source") == "u2"
        and r.get("data", {}).get("kind") == "assignment_canceled"
        and r.get("task_revision") == first["task_revision"]
        and r.get("task_id") == first["event_id"] + "-u2"
        and canceled
        and r["received_ns"] >= min(e["sim_ns"] for e in canceled)
        and arrived(r["message_id"])
    ]
    if not reports:
        missing.append("No radio-delivered assignment cancellation confirmation")
    later = [
        p
        for p in plans[1:]
        if p.get("event_id") == first["event_id"]
        and p["task_revision"] > first["task_revision"]
        and any(
            a["role"] == "inspect" and a["vehicle"] != "u2"
            for a in p.get("assignments", [])
        )
    ]
    replacement_ids = (
        {
            a["vehicle"]
            for a in later[0]["assignments"]
            if a["role"] == "inspect" and a["vehicle"] != "u2"
        }
        if later
        else set()
    )
    notices = [
        r
        for r in ledger
        if r.get("event") == "queued"
        and r.get("kind") == "notice"
        and r.get("event_id") == first["event_id"]
        and later
        and r.get("task_revision") == later[0]["task_revision"]
        and r.get("task_id") == first["event_id"] + "-offer"
        and r.get("destination") in replacement_ids
        and reports
        and r["sim_ns"] >= reports[0]["received_ns"]
        and arrived(r["message_id"])
    ]
    if not later or not replacement_ids.issubset({r["destination"] for r in notices}):
        missing.append(
            "No newly delivered notice and later different-vehicle inspect plan"
        )
    elif later[0]["planned_at_ns"] < max(
        reports[0]["received_ns"],
        max(commits[r["message_id"]]["received_ns"] for r in notices),
    ):
        report.update(
            status="FAIL",
            reason="Replanning preceded delivered cancellation/new notice",
        )
        return report
    received_plans = [
        r
        for r in ledger
        if r.get("event") == "queued"
        and r.get("kind") == "plan"
        and r.get("event_id") == first["event_id"]
        and later
        and r.get("task_revision") == later[0]["task_revision"]
        and r.get("destination")
        in {
            a["vehicle"]
            for a in later[0]["assignments"]
            if a["role"] == "inspect" and a["vehicle"] != "u2"
        }
        and arrived(r["message_id"])
    ]
    if not received_plans:
        missing.append(
            "Changed inspect assignment was not actually received over native radio"
        )
    report.update(
        received_reassignment_ids=[r["message_id"] for r in received_plans],
        canceled_native_events=canceled,
        cancellation_reports=reports,
        later_plans=later,
        new_notice_received_ns={
            r["message_id"]: commits[r["message_id"]]["received_ns"] for r in notices
        },
    )
    if missing:
        report["reason"] = "; ".join(missing)
    else:
        report.update(status="FINISHED", reason=None)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[row["id"] for row in cases()])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--agent", choices=("rule", "model"), default="rule")
    parser.add_argument("--attempt", default="a1")
    parser.add_argument("--variant")
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Mount GPU only into the simulator using the supplied Compose override",
    )
    parser.add_argument(
        "--replay-run",
        type=Path,
        help="C09: read an already stopped retained run, without Docker or new native work",
    )
    parser.add_argument(
        "--perception-samples", type=Path, help="C10 frozen eligible sample manifest"
    )
    parser.add_argument(
        "--perception-annotations",
        type=Path,
        help="C10 evaluator-only visible-surface annotations",
    )
    parser.add_argument(
        "--inference-run", type=Path, help="C10 retained real native inference run"
    )
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs/acceptance")
    parser.add_argument("--wall-timeout", type=float, default=7200)
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the supplied case specification; do not run",
    )
    args = parser.parse_args()
    if args.list:
        print(json.dumps(cases(), ensure_ascii=False, indent=2))
        return 0
    if not args.case:
        parser.error("--case is required unless --list is used")
    if args.replay_run and args.case != "C09":
        parser.error("--replay-run is only valid for C09")
    scoring_inputs = (
        args.perception_samples,
        args.perception_annotations,
        args.inference_run,
    )
    if any(scoring_inputs) and (args.case != "C10" or not all(scoring_inputs)):
        parser.error(
            "C10 scoring requires --perception-samples, --perception-annotations and --inference-run together"
        )
    case = next(row for row in cases() if row["id"] == args.case)
    if args.seed not in case["seeds"] or args.agent not in case["agent_modes"]:
        parser.error("seed or agent mode is not declared for this case")
    variants = case.get("variants", [])
    if (variants and args.variant not in variants) or (not variants and args.variant):
        parser.error(
            f"--variant must be one of {variants}"
            if variants
            else "case has no variants"
        )
    if not re.fullmatch(r"a?[1-9][0-9]*", args.attempt):
        parser.error("--attempt must be a positive integer, optionally prefixed with a")
    if not math.isfinite(args.wall_timeout) or args.wall_timeout <= 0:
        parser.error("--wall-timeout must be positive")
    attempt = int(args.attempt.removeprefix("a"))
    prefix = f"{args.case}-{args.seed}-{args.agent}"
    base_id = f"{prefix}-a{attempt}"
    run_id = base_id + (f"-{args.variant}" if args.variant else "")
    root = args.run_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Reserve the attempt across variants, not merely the final directory name.
    reservations = root / ".attempts"
    reservations.mkdir(exist_ok=True)
    previous = [
        int(p.name.rsplit("-a", 1)[1]) for p in reservations.glob(prefix + "-a*")
    ]
    if previous and attempt <= max(previous):
        parser.error(
            f"attempt must exceed the retained attempt a{max(previous)} for {prefix}"
        )
    run = root / run_id
    if run.exists():
        parser.error(f"refusing to overwrite {run}")
    try:
        (reservations / base_id).mkdir()
        run.mkdir()
    except FileExistsError:
        parser.error("this run or attempt already exists; choose a new attempt")
    for identity in ("u1", "u2", "u3", "u4", "centre", "coordinator", "simulator"):
        (run / "data" / identity).mkdir(parents=True, mode=0o700)
    for identity in ("u1", "u2", "u3", "u4", "centre"):
        (run / "data/sockets" / identity).mkdir(parents=True, mode=0o700)
    compose = ROOT / "deploy/compose.yaml"
    env = os.environ.copy()
    env.update(
        RUN_ID=run_id,
        AGENT_MODE=args.agent,
        CASE_ID=args.case,
        CASE_VARIANT=args.variant or "",
        SEED=str(args.seed),
        RUN_ROOT=str(run / "data"),
        AAS_UID=str(os.getuid()),
        AAS_GID=str(os.getgid()),
    )
    command = ["docker", "compose", "-f", str(compose)]
    if args.gpu:
        command += ["-f", str(ROOT / "deploy/compose.gpu.yaml")]
    command += ["-p", "aas-" + run_id.lower()]
    manifest = {
        "run_id": run_id,
        "case_id": args.case,
        "seed": args.seed,
        "agent_mode": args.agent,
        "attempt": attempt,
        "variant_id": args.variant,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "execution_status": "PREPARED",
        "execution_scope": "replay_only"
        if args.replay_run
        else "scoring_only"
        if all(scoring_inputs)
        else "native_driver",
        "native_started": False,
        "native_launch_attempted": False,
        "case_specification": case,
        "command": command,
        "note": "Execution status is not a functional acceptance result.",
        "driver_scope": {
            "C00": "Baseline backend smokes and one-UAV flight sequence",
            "C02": "Physical no-person or canopy geometry, actual common decision loop",
            "C03": "Near-base image fragment/final-ACK diagnostic with original attenuation and coordinator-owned receiver; centre decision worker is stopped; full blocked-direct to relay recovery remains",
            "C07": "Common physical delivery with carrier-only receipt or first confirmation ACK native delivery-timeout injection",
            "C04": "Protected commitment, absent payload, capability/identity permutation, or PX4-motion-integrated declared energy decline and radio replanning",
            "C05": "Queued/running cancellation and stale-revision action injection; late model/low-level callback injection remains",
            "C06": "Actual frozen 20-positive/5-absence query corpus before/after same-volume restart and old-frame reanalysis; cross-UAV radio copies remain",
            "C08": "Actual later-frame detection/location produces a separated second clue and radio event; queue/short deadline variants",
            "C09": "Fresh offline rule deployment and retained-log replay, or replay-only when requested",
        }.get(args.case, "Shared-world rescue task loop"),
    }
    write_json(run / "run-manifest.json", manifest)
    shutil.copyfile(SPEC, run / "case-specification.yaml")
    started = time.monotonic()
    if args.case == "C10":
        if not all(scoring_inputs):
            manifest.update(
                execution_status="BLOCKED",
                reason="C10 requires a frozen sample list, evaluator annotations and retained real native inference run",
            )
        else:
            try:
                evaluator = run / "evaluator"
                evaluator.mkdir()
                shutil.copyfile(
                    args.perception_samples, evaluator / "frozen-samples.json"
                )
                shutil.copyfile(
                    args.perception_annotations, evaluator / "annotations.json"
                )
                scorer = [
                    sys.executable,
                    str(ROOT / "tools/score_perception.py"),
                    "--samples",
                    str(evaluator / "frozen-samples.json"),
                    "--annotations",
                    str(evaluator / "annotations.json"),
                    "--inference-run",
                    str(args.inference_run.resolve()),
                    "--output",
                    str(run / "perception-score.json"),
                ]
                with (run / "perception-score.log").open("w") as log:
                    scored = subprocess.run(
                        scorer,
                        cwd=ROOT,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=args.wall_timeout,
                        check=False,
                    )
                score = json.loads((run / "perception-score.json").read_text())
                manifest.update(
                    execution_status="FINISHED"
                    if scored.returncode == 0
                    else "BLOCKED"
                    if scored.returncode == 2
                    else "FAIL",
                    scoring_status=score.get("status"),
                    inference_source=str(args.inference_run.resolve()),
                    command=scorer,
                    reason=score.get("reason")
                    or "; ".join(score.get("blockers", []))
                    or (
                        "Perception metrics did not meet thresholds; see perception-score.json"
                        if score.get("status") == "FAIL"
                        else None
                    ),
                )
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                manifest.update(execution_status="FAIL", reason=str(error))
        manifest["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(run / "run-manifest.json", manifest)
        print(json.dumps(manifest, indent=2))
        return 0 if manifest["execution_status"] == "FINISHED" else 2
    if args.replay_run:
        try:
            replay = replay_retained(args.replay_run, run / "replay")
            manifest.update(
                execution_status=replay["status"],
                reason=replay["reason"],
                replay_source=str(args.replay_run.resolve()),
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            manifest.update(execution_status="FAIL", reason=str(error))
        write_json(run / "run-manifest.json", manifest)
        print(json.dumps(manifest, indent=2))
        return 0 if manifest["execution_status"] == "FINISHED" else 2
    try:
        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker executable is unavailable; native cases were not run"
            )
        if not compose.is_file():
            raise RuntimeError("Rescue Compose deployment is unavailable")
        with (run / "preflight.log").open("w") as log:
            for check in (["docker", "info"], ["docker", "compose", "version"]):
                result = subprocess.run(
                    check,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=30,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(f"{check!r} failed; see preflight.log")
        # Cases require their own real stimuli. Running C01 repeatedly does not
        # exercise cancellation, restart, packet loss or the image benchmark.
        if (
            args.case
            not in {
                "C00",
                "C01",
                "C02",
                "C03",
                "C04",
                "C05",
                "C06",
                "C07",
                "C08",
                "C09",
            }
            or (
                args.case == "C03"
                and args.variant
                not in {"missing_fragment_nack_then_retry", "final_ack_lost"}
            )
            or (
                args.case == "C07"
                and args.variant
                not in {"receiver_receipt_lost", "confirmation_final_ack_lost"}
            )
            or (args.case == "C08" and args.variant == "no_eligible_vehicle")
        ):
            raise RuntimeError(
                f"{args.case} has no native case driver yet; its specification is retained"
            )
        command = prepare_scene(run, command, env, args.case, args.variant)
        manifest["command"] = command
        manifest["native_launch_attempted"] = True
        manifest["execution_status"] = "RUNNING"
        write_json(run / "run-manifest.json", manifest)
        if args.case in {"C00", "C05"}:
            run_flight_case(
                run, command, env, args.wall_timeout, args.case, args.variant
            )
            manifest.update(execution_status="FINISHED", exit_code=0)
        elif args.case == "C06":
            run_history_case(run, command, env, args.wall_timeout)
            manifest.update(execution_status="FINISHED", exit_code=0)
        else:
            with (run / "compose.log").open("w") as log:
                completed = subprocess.run(
                    command
                    + [
                        "up",
                        "--no-build",
                        "--pull",
                        "never",
                        "--abort-on-container-exit",
                        "--exit-code-from",
                        "coordinator",
                    ]
                    + (
                        ["simulator", "radio", "coordinator", "uav-u2"]
                        if args.case == "C03"
                        else []
                    ),
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=args.wall_timeout,
                    check=False,
                )
            manifest.update(
                execution_status="FINISHED" if completed.returncode == 0 else "FAIL",
                exit_code=completed.returncode,
            )
    except (
        RuntimeError,
        OSError,
        subprocess.TimeoutExpired,
        KeyboardInterrupt,
    ) as error:
        manifest.update(
            execution_status="FAIL"
            if manifest["native_launch_attempted"]
            else "BLOCKED",
            reason=str(error) or "Interrupted by operator",
        )
    finally:
        if manifest["native_launch_attempted"]:
            # Stop only this unique project; preserve containers, bind data and logs.
            with (run / "stop.log").open("w") as log:
                try:
                    stopped = subprocess.run(
                        command + ["stop", "--timeout", "10"],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=60,
                        check=False,
                    )
                    manifest["stop_exit_code"] = stopped.returncode
                    if stopped.returncode:
                        manifest.update(
                            execution_status="FAIL",
                            stop_error="Compose stop did not succeed; services may still be running",
                        )
                except (OSError, subprocess.TimeoutExpired) as error:
                    manifest.update(execution_status="FAIL", stop_error=str(error))
            with (run / "compose-services.log").open("w") as log:
                try:
                    subprocess.run(
                        command + ["logs", "--no-color", "--timestamps"],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=30,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    log.write(f"\nLog capture error: {error}\n")
        manifest["native_started"] = any(
            path.stat().st_size
            for filename in ("native-flight-events.jsonl", "estimated-telemetry.jsonl")
            for path in run.rglob(filename)
        )
        baseline = run / "baseline-px4.json"
        if baseline.is_file():
            try:
                manifest["native_started"] = manifest["native_started"] or any(
                    row.get("trajectory")
                    for row in json.loads(baseline.read_text()).get("runs", [])
                )
            except (OSError, ValueError, TypeError, AttributeError) as error:
                manifest["baseline_evidence_error"] = str(error)
                manifest["execution_status"] = "FAIL"
        manifest.update(
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            wall_elapsed_s=time.monotonic() - started,
        )
        write_json(run / "run-manifest.json", manifest)
    if (
        args.case == "C04"
        and args.variant == "low_energy_after_offer"
        and manifest["execution_status"] == "FINISHED"
    ):
        try:
            observed = low_energy_observations(run)
            write_json(run / "energy-branch-observations.json", observed)
            manifest.update(
                execution_status=observed["status"], reason=observed["reason"]
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            manifest.update(execution_status="FAIL", reason=str(error))
        write_json(run / "run-manifest.json", manifest)
    if args.case in {"C03", "C07"} and manifest["execution_status"] == "FINISHED":
        from verify_run import radio_fault_check

        try:
            fault = radio_fault_check(run)
            write_json(run / "radio-fault-observations.json", fault)
            if fault["status"] != "PASS":
                manifest.update(
                    execution_status=fault["status"], reason=fault["reason"]
                )
        except (OSError, ValueError, KeyError, TypeError) as error:
            manifest.update(execution_status="FAIL", reason=str(error))
        write_json(run / "run-manifest.json", manifest)
    if args.case == "C09" and manifest["execution_status"] == "FINISHED":
        try:
            replay_path = root.parent / "replays" / run_id
            replay = replay_retained(run, replay_path)
            manifest.update(
                replay_output=str(replay_path),
                execution_status=replay["status"],
                reason=replay["reason"],
            )
            counters = replay["recorded_runtime_counters"]
            expected = {"u1", "u2", "u3", "u4", "centre"}
            if {row["uav_id"] for row in counters} != expected or any(
                row.get(key) is None
                for row in counters
                for key in ("model_calls", "model_client_constructions")
            ):
                manifest.update(
                    execution_status="BLOCKED",
                    reason="Offline rule run lacks all five runtime model counters",
                )
            elif any(
                row[key] != 0
                for row in counters
                for key in ("model_calls", "model_client_constructions")
            ) or any(path.stat().st_size for path in run.rglob("model-calls.jsonl")):
                manifest.update(
                    execution_status="FAIL",
                    reason="Offline rule run constructed or called a model client",
                )
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            manifest.update(execution_status="FAIL", reason=str(error))
        write_json(run / "run-manifest.json", manifest)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "run": str(run),
                **{
                    key: manifest[key] for key in ("execution_status", "native_started")
                },
                "reason": manifest.get("reason"),
            },
            indent=2,
        )
    )
    return 0 if manifest["execution_status"] == "FINISHED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
