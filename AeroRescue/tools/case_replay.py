"""Concrete retained-evidence rescue case actions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def replay_retained(source: Path, output: Path) -> dict:
    """Read committed logs only. No runtime, provider, socket or simulator import."""
    from verify_run import records, verify

    source = source.resolve()
    if not (source / "run-manifest.json").is_file():
        raise ValueError("replay source needs its retained run-manifest.json")
    if output.resolve().is_relative_to(source):
        raise ValueError("replay output must be outside the retained source run")
    output.mkdir(parents=True, exist_ok=False)
    selected = (
        [source / "run-manifest.json"]
        + sorted(source.rglob("*.jsonl"))
        + sorted(source.rglob("runtime-state.json"))
        + sorted(source.rglob("timing.json"))
    )
    before = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in selected
    }
    from aerokernel import replay

    from aeroagentsim.services.projector import project

    journals = list(source.rglob("kernel.jsonl"))
    reconstruction = None
    status, reason = (
        "BLOCKED",
        "No unique nonempty native kernel journal to reconstruct",
    )
    if len(journals) == 1 and journals[0].stat().st_size:
        journal = journals[0]
        try:
            kernel = replay(journal)
            view = kernel.view()
            projected_counts = dict(commits=0, facts=0, messages=0, receipts=0)
            with (output / "kernel-replay.jsonl").open("w") as target:
                for record in kernel.iter_records():
                    projected = project(record)
                    target.write(json.dumps(projected, ensure_ascii=False) + "\n")
                    projected_counts["commits"] += 1
                    for name in ("facts", "messages", "receipts"):
                        projected_counts[name] += len(projected[name])
            reconstruction = {
                "journal": str(journal.relative_to(source)),
                "cut": view.cut.index,
                "sim_ns": view.instant.ns,
                "sealed_ns": kernel.sealed_ns,
                "incomplete": kernel.incomplete,
                "projected_records": projected_counts,
                "actions": [],
            }
            missing, contradictions = [], []
            if kernel.incomplete:
                missing.append("Kernel journal reconstruction is incomplete")
            if view.instant.ns <= 0 or not projected_counts["facts"]:
                missing.append("No nonzero native simulation progress/facts to replay")
            timing_path = journal.parent / "timing.json"
            if not timing_path.is_file():
                missing.append("Native terminal timing is missing")
            else:
                timing = json.loads(timing_path.read_text())
                expected_ns = round(timing["sim_elapsed_s"] * 1_000_000_000)
                reconstruction["recorded_terminal_sim_ns"] = expected_ns
                if view.instant.ns != expected_ns or kernel.sealed_ns != expected_ns:
                    contradictions.append(
                        "Reconstructed kernel time differs from retained terminal time"
                    )
            native_counts = {}
            for name in ("native-flight-events.jsonl", "native-radio-events.jsonl"):
                path = journal.parent / name
                native_counts[name] = len(records(path)) if path.is_file() else 0
            reconstruction["native_source_records"] = native_counts
            if not all(native_counts.values()):
                missing.append("Retained native flight/radio source events are missing")
            operations_path = journal.parent / "flight/operations.jsonl"
            latest = (
                {row["operation_id"]: row for row in records(operations_path)}
                if operations_path.is_file()
                else {}
            )
            for operation in latest.values():
                command_id = operation.get("kernel_command_id")
                if not command_id:
                    continue
                action = view.action(command_id)
                mapped = {"executing": "running", "canceling": "cancel_requested"}.get(
                    action.status, action.status
                )
                reconstruction["actions"].append(
                    {
                        "operation_id": operation["operation_id"],
                        "kernel_command_id": command_id,
                        "recorded_status": operation["status"],
                        "reconstructed_status": action.status,
                        "receipt_count": len(action.history),
                    }
                )
                if operation["status"] == "cancel_requested" and mapped in {
                    "submitted",
                    "accepted",
                    "running",
                }:
                    missing.append(
                        f"{command_id}: pending cancellation has not reached the kernel terminal state"
                    )
                elif operation["status"] != mapped:
                    contradictions.append(
                        f"{command_id}: reconstructed action differs from retained operation status"
                    )
            if not reconstruction["actions"]:
                missing.append(
                    "No native flight operations can be compared with reconstructed actions"
                )
            status = "FAIL" if contradictions else "BLOCKED" if missing else "FINISHED"
            reason = "; ".join(contradictions + missing) or None
        except (ValueError, KeyError, StopIteration) as error:
            status, reason = (
                "FAIL",
                "Kernel journal could not be reconstructed: " + str(error),
            )
    counts = {}
    with (output / "timeline.jsonl").open("w") as target:
        for path in selected:
            if path.suffix != ".jsonl" or path.name == "kernel.jsonl":
                continue
            count = 0
            with path.open() as stream:
                for number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    target.write(
                        json.dumps(
                            {
                                "source": str(path.relative_to(source)),
                                "line": number,
                                "record": row,
                            }
                        )
                        + "\n"
                    )
                    count += 1
            counts[str(path.relative_to(source))] = count
    after = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in selected
    }
    if after != before:
        raise RuntimeError(
            "source logs changed during replay; stop their owning services first"
        )
    source_manifest = json.loads((source / "run-manifest.json").read_text())
    states = [
        json.loads(p.read_text()) for p in selected if p.name == "runtime-state.json"
    ]
    report = {
        "source_run": str(source),
        "source_run_id": source_manifest["run_id"],
        "status": status,
        "reason": reason,
        "external_observations": "timeline.jsonl retains companion/native logs; agent decisions and camera samples are not regenerated",
        "kernel_reconstruction": reconstruction,
        "records_read": counts,
        "source_records_unchanged": True,
        "replay_new_model_calls": 0,
        "replay_new_physics_steps": 0,
        "replay_new_camera_samples": 0,
        "recorded_runtime_counters": [
            {
                "uav_id": r["uav_id"],
                "model_calls": r.get("model_calls"),
                "model_client_constructions": r.get("model_client_constructions"),
            }
            for r in states
        ],
        "source_evidence_review": verify(source),
    }
    write_json(output / "replay.json", report)
    return report
