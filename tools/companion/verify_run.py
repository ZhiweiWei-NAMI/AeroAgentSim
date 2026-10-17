#!/usr/bin/env python3
"""Inspect actual rescue artifacts; never treat a program's success as proof.

This intentionally small checker covers bytes, sensor samples and radio causal
links. Full flight, fault-injection and task semantics still need the original
case-specific review; it does not issue whole-case PASS certificates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def records(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_number}: object required")
            rows.append(value)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def result(name: str, status: str, reason: str, **measured: Any) -> dict[str, Any]:
    return {"check": name, "status": status, "reason": reason, "measured": measured}


def radio_check(run: Path) -> dict[str, Any]:
    ledgers = list(run.rglob("packet-ledger.jsonl"))
    if not ledgers:
        return result(
            "radio_file_causality", "BLOCKED", "No packet ledger or received bytes"
        )
    raw_paths = list(run.rglob("native-radio-events.jsonl"))
    raw = [row for path in raw_paths for row in records(path)]
    # Coordinator records actual native deliveries separately from application
    # receiver bookkeeping. Application logs alone cannot prove native transit.
    native_delivered = set()
    for row in raw:
        payload = row.get("payload", row.get("data", row.get("event", row)))
        if isinstance(payload, dict) and row.get("kind", row.get("type")) in {
            "delivery",
            "adapters.ns3.delivery",
            "ns3.delivery",
        }:
            native_delivered.add(payload.get("packet_id"))
    commits = acknowledgements = chunks = 0
    errors: list[str] = []
    for ledger in ledgers:
        queued, submitted, native, last_hop, chunk_commits, file_commits = (
            {},
            {},
            {},
            {},
            {},
            {},
        )
        attempts: dict[tuple, int] = {}
        for row in records(ledger):
            event, mid, pid = (
                row.get("event"),
                row.get("message_id"),
                row.get("packet_id"),
            )
            if event == "queued":
                queued[mid] = row
            elif event == "native_submit":
                submitted[pid] = row
                header = row.get("header", {})
                # Count original sends, not every relay hop, against retry budget.
                if header.get("kind") == "chunk" and str(pid).endswith("-h0"):
                    key = (mid, header.get("attempt"), header.get("chunk_index"))
                    attempts[key] = attempts.get(key, 0) + 1
                    if attempts[key] > 3 or header.get("attempt", 0) not in (1, 2):
                        errors.append(
                            f"{mid}: transmission attempt/retry limit exceeded"
                        )
            elif event == "native_delivery":
                if pid not in submitted:
                    errors.append(f"{mid}: native delivery has no earlier submission")
                native[pid] = row
            elif event == "last_hop_delivery":
                if pid not in native:
                    errors.append(f"{mid}: final hop has no earlier native delivery")
                last_hop[pid] = row
            elif event == "receiver_chunk_commit":
                chunks += 1
                header = last_hop.get(pid, {}).get("header", {})
                if (
                    header.get("kind"),
                    header.get("chunk_index"),
                    header.get("chunk_hash"),
                ) != ("chunk", row.get("chunk_index"), row.get("chunk_hash")):
                    errors.append(
                        f"{mid}: committed chunk has no matching earlier last hop"
                    )
                if raw and pid not in native_delivered:
                    errors.append(
                        f"{mid}: committed packet absent from raw native deliveries"
                    )
                chunk_commits.setdefault(mid, {})[row.get("chunk_index")] = row
            elif event == "receiver_file_commit":
                commits += 1
                source = queued.get(mid, {})
                destination = row.get("destination")
                path = (
                    ledger.parent / "received" / str(destination) / str(mid) / "content"
                )
                if not path.is_file():
                    errors.append(f"{mid}: committed received file is missing")
                    continue
                file_hash = sha256(path)
                if file_hash != source.get("file_hash") or file_hash != row.get(
                    "file_hash"
                ):
                    errors.append(
                        f"{mid}: actual received bytes differ from declared source"
                    )
                if path.stat().st_size != source.get("size"):
                    errors.append(f"{mid}: received byte count differs")
                expected = source.get("chunk_hashes", [])
                parts = chunk_commits.get(mid, {})
                if not expected or set(parts) != set(range(len(expected))):
                    errors.append(f"{mid}: complete file lacks all prior chunk commits")
                for index, chunk_hash in enumerate(expected):
                    part = path.parent / f"{index}.part"
                    original = (
                        ledger.parent / "private-payloads" / str(mid) / str(index)
                    )
                    if not part.is_file() or not original.is_file():
                        errors.append(f"{mid}: original/received chunk {index} missing")
                    elif sha256(part) != chunk_hash or sha256(original) != chunk_hash:
                        errors.append(f"{mid}: chunk {index} bytes differ")
                if mid in file_commits:
                    errors.append(f"{mid}: duplicate persistent file commit")
                file_commits[mid] = row
            elif event == "radio_final_ack":
                acknowledgements += 1
                header = last_hop.get(pid, {}).get("header", {})
                if mid not in file_commits or header.get("kind") != "final_ACK":
                    errors.append(
                        f"{mid}: ACK has no earlier durable file and radio final hop"
                    )
                if row.get("file_hash") != file_commits.get(mid, {}).get("file_hash"):
                    errors.append(f"{mid}: ACK file hash differs")
                if raw and pid not in native_delivered:
                    errors.append(f"{mid}: ACK absent from raw native deliveries")
    if errors:
        return result(
            "radio_file_causality", "FAIL", "; ".join(errors[:30]), errors=len(errors)
        )
    if not raw or not native_delivered:
        return result(
            "radio_file_causality",
            "BLOCKED",
            "Raw native delivery records missing; application ledger alone is insufficient",
            committed_files=commits,
            acknowledged_files=acknowledgements,
        )
    if not commits or not acknowledgements:
        return result(
            "radio_file_causality",
            "BLOCKED",
            "No complete acknowledged file transfer; pending/no-link cases need their own negative-branch review",
        )
    return result(
        "radio_file_causality",
        "PASS",
        "Actual source/chunk/receiver bytes and earlier native events agree; no whole-case claim",
        committed_files=commits,
        acknowledged_files=acknowledgements,
        chunks=chunks,
    )


def sensor_check(run: Path) -> dict[str, Any]:
    databases = list(run.rglob("index.sqlite"))
    if not databases:
        return result("recorded_rgb_depth", "BLOCKED", "No local observation database")
    try:
        import numpy as np
        from PIL import Image
    except ImportError as error:
        return result(
            "recorded_rgb_depth", "BLOCKED", f"Cannot decode recorded inputs: {error}"
        )
    count = 0
    identities = set()
    errors, unavailable = [], []
    for database in databases:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(observations)")
            }
            if "metadata" not in columns:
                unavailable.append(
                    f"{database.relative_to(run)}: observation metadata unavailable"
                )
                continue
            observations = connection.execute(
                "SELECT metadata FROM observations"
            ).fetchall()
        for (metadata,) in observations:
            observation = json.loads(metadata)
            frame = observation.get("frame_id", "unknown")
            identities.add(observation.get("uav_id"))
            rgb_id, depth_id = (
                observation.get("rgb_artifact_id"),
                observation.get("depth_artifact_id"),
            )
            if not rgb_id or not depth_id:
                unavailable.append(f"{frame}: RGB or depth missing")
                continue
            # IDs are local content identifiers, never arbitrary paths.
            if any(
                "/" in str(value) or ".." in str(value) for value in (rgb_id, depth_id)
            ):
                errors.append(f"{frame}: unsafe artifact identifier")
                continue
            rgb = (
                database.parent
                / "artifacts"
                / (str(rgb_id).removesuffix(".png") + ".png")
            )
            depth = (
                database.parent
                / "artifacts"
                / (str(depth_id).removesuffix(".npy") + ".npy")
            )
            if not rgb.is_file() or not depth.is_file():
                errors.append(f"{frame}: referenced RGB/depth absent")
                continue
            try:
                with Image.open(rgb) as picture:
                    picture.load()
                    size = picture.size
                array = np.load(depth, allow_pickle=False)
                if array.dtype != np.dtype("float32") or array.shape != (
                    size[1],
                    size[0],
                ):
                    errors.append(
                        f"{frame}: depth is not registered float32 with RGB dimensions"
                    )
                if not np.isfinite(array).any():
                    unavailable.append(f"{frame}: no finite depth values")
                if observation.get("rgb_sha256") != sha256(rgb):
                    errors.append(f"{frame}: saved RGB differs from observation")
                count += 1
            except (OSError, ValueError) as error:
                errors.append(f"{frame}: cannot decode: {error}")
    if errors:
        return result(
            "recorded_rgb_depth", "FAIL", "; ".join(errors[:20]), errors=len(errors)
        )
    if not count:
        return result(
            "recorded_rgb_depth",
            "BLOCKED",
            "No decodable registered RGB/depth pair",
            unavailable=unavailable,
        )
    return result(
        "recorded_rgb_depth",
        "PASS",
        "Recorded pixels and numeric depth decode; this does not establish native rendering, clock alignment or detection accuracy",
        samples=count,
        uav_ids=sorted(str(value) for value in identities),
        unavailable=unavailable,
    )


def flight_check(run: Path) -> dict[str, Any]:
    telemetry = [
        row for path in run.rglob("estimated-telemetry.jsonl") for row in records(path)
    ]
    ledgers = list(run.rglob("operations.jsonl"))
    if not telemetry or not ledgers:
        return result(
            "estimated_flight_completion",
            "BLOCKED",
            "No operation and independent estimated-telemetry traces",
        )
    latest = {}
    for path in ledgers:
        for row in records(path):
            latest[row["operation_id"]] = row
    checked, missing, errors = [], [], []
    for operation in latest.values():
        action = operation.get("action")
        if (
            action not in {"goto", "hold", "land"}
            or operation.get("status") != "succeeded"
        ):
            continue
        end = operation.get("observed_sim_ns")
        if end is None:
            missing.append(operation["operation_id"] + ": terminal source time missing")
            continue
        samples = [
            row
            for row in telemetry
            if row.get("uav_id") == operation["uav_id"]
            and operation["submitted_sim_ns"] <= row["sim_ns"] <= end
        ]
        if action == "land":
            terminal = [row for row in samples if end - row["sim_ns"] <= 300_000_000]
            if not terminal:
                missing.append(operation["operation_id"] + ": no landing samples")
            elif (
                terminal[-1].get("landed_state") != "ON_GROUND"
                or terminal[-1].get("armed") is not False
            ):
                errors.append(
                    operation["operation_id"]
                    + ": succeeded landing lacks on-ground/disarmed observation"
                )
            else:
                checked.append(operation["operation_id"])
            continue
        estimates = [
            row["vehicle_estimate"]
            for row in samples
            if isinstance(row.get("vehicle_estimate"), dict)
            and row["vehicle_estimate"].get("status") == "available"
            and row["vehicle_estimate"].get("source") == "PX4_MavlinkDirect"
            and row["vehicle_estimate"].get("source_sim_ns") is not None
        ]
        estimates = list({row["source_sim_ns"]: row for row in estimates}.values())
        estimates.sort(key=lambda row: row["source_sim_ns"])
        start = previous = None
        observed = False
        for estimate in estimates:
            now = estimate["source_sim_ns"]
            speed = math.dist(estimate["velocity_enu_m_s"], [0, 0, 0])
            good = speed < (1.5 if action == "goto" else 1.0)
            if action == "goto":
                target = operation["params"]["position_enu_m"]
                good = good and math.dist(estimate["position_enu_m"], target) <= 2.0
            if not good or (previous is not None and now - previous > 300_000_000):
                start = None
                observed = False
            if good and start is None:
                start = now
            if (
                good
                and start is not None
                and now - start >= 1_000_000_000
                and 0 <= end - now <= 300_000_000
            ):
                observed = True
            previous = now
        if not estimates:
            missing.append(
                operation["operation_id"] + ": source-timed PX4 estimates missing"
            )
        elif not observed:
            errors.append(
                operation["operation_id"]
                + ": succeeded action lacks measured 1s position/speed settling"
            )
        else:
            checked.append(operation["operation_id"])
    if errors:
        return result(
            "estimated_flight_completion",
            "FAIL",
            "; ".join(errors),
            unavailable=missing,
        )
    if not checked or missing:
        return result(
            "estimated_flight_completion",
            "BLOCKED",
            "Insufficient source observations for completed goto/hold/land actions",
            checked=checked,
            unavailable=missing,
        )
    return result(
        "estimated_flight_completion",
        "PASS",
        "Recomputed completed goto/hold/land observations; cancellation and owner isolation require separate cases",
        checked=checked,
    )


def verify(run: Path) -> dict[str, Any]:
    run = run.resolve()
    manifest_path = run / "run-manifest.json"
    if not manifest_path.is_file():
        return {
            "run_id": run.name,
            "status": "BLOCKED",
            "checks": [],
            "reason": "No run-manifest.json; no case was executed",
        }
    manifest = read_json(manifest_path)
    checks = []
    for check in (radio_check, sensor_check, flight_check):
        try:
            checks.append(check(run))
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            sqlite3.DatabaseError,
        ) as error:
            checks.append(
                result(
                    check.__name__, "FAIL", f"Malformed or unreadable evidence: {error}"
                )
            )
    failed = any(check["status"] == "FAIL" for check in checks)
    if manifest.get("execution_status") == "FAIL":
        failed = True
    status = "FAIL" if failed else "BLOCKED"
    # Even perfect files cannot establish flight completion, actor isolation,
    # contact-sensor origin, scenario stimuli or a live model decision branch.
    return {
        "run_id": manifest.get("run_id", run.name),
        "case_id": manifest.get("case_id"),
        "seed": manifest.get("seed"),
        "agent_mode": manifest.get("agent_mode"),
        "attempt": manifest.get("attempt"),
        "variant_id": manifest.get("variant_id"),
        "status": status,
        "execution_status": manifest.get("execution_status"),
        "checks": checks,
        "reason": manifest.get("reason")
        or "Partial artifact checks only. Original case flight/task/fault-injection criteria still require independent review.",
        "scope": "No software version/hash-field gates; file hashes only verify delivered content.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path, help="Optional new report file; source run is read-only"
    )
    args = parser.parse_args()
    report = verify(args.run)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        if args.out.resolve().is_relative_to(args.run.resolve()):
            parser.error("--out must be outside the source run")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x") as stream:
            stream.write(text)
    print(text, end="")
    return 1 if report["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
