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


def run_history_case(
    run: Path, command: list[str], env: dict[str, str], timeout: float
) -> None:
    """Acquire real frames, query/reanalyze them, restart the same companion."""
    deadline = time.monotonic() + timeout

    def execute(stage: str, suffix: list[str], script: str | None = None) -> str:
        try:
            result = subprocess.run(
                command + suffix,
                cwd=ROOT,
                env=env,
                input=script,
                capture_output=True,
                text=True,
                timeout=max(0.1, deadline - time.monotonic()),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            partial = b"".join(
                value if isinstance(value, bytes) else value.encode()
                for value in (error.stdout, error.stderr)
                if value
            )
            (run / (stage + ".log")).write_bytes(
                partial if isinstance(partial, bytes) else partial.encode()
            )
            raise
        (run / (stage + ".log")).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"C06 {stage} exited {result.returncode}; see its log")
        return result.stdout

    execute(
        "history-start",
        [
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "simulator",
            "radio",
            "coordinator",
            "uav-u2",
        ],
    )
    database = run / "data/u2/index.sqlite"
    frame = None
    while time.monotonic() < deadline:
        if database.is_file():
            with sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True
            ) as connection:
                try:
                    rows = connection.execute(
                        "SELECT metadata FROM observations ORDER BY observed_at"
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            frame = next(
                (
                    json.loads(row[0])
                    for row in rows
                    if not json.loads(row[0]).get("last_analysis", {}).get("detections")
                ),
                None,
            )
            if frame:
                break
        time.sleep(0.2)
    if frame is None:
        raise RuntimeError(
            "C06 acquired no real historical frame without a detection record"
        )
    write_json(run / "history-selected-frame.json", frame)

    def query(stage: str, request: dict) -> dict:
        cli = ["exec", "-T", "uav-u2", "python", "-m", "aeroagentsim.companion.tools"]
        queued = json.loads(
            execute(
                stage + "-queued",
                cli
                + [
                    "retrieve-sightings",
                    "--data-dir",
                    "/data",
                    "--request",
                    json.dumps(request),
                ],
            )
        )
        result_path = (
            run
            / "data/u2/tasks/tool-requests"
            / (queued["request_id"] + ".result.json")
        )
        while time.monotonic() < deadline and not result_path.is_file():
            time.sleep(0.2)
        if not result_path.is_file():
            raise RuntimeError(
                "C06 existing worker did not finish the queued history tool"
            )
        result = json.loads(
            execute(
                stage, cli + ["--data-dir", "/data", "--status", queued["request_id"]]
            )
        )
        if result.get("status") in {"failed", "unavailable", "error"}:
            raise RuntimeError("C06 history tool failed: " + json.dumps(result))
        return result

    def lookup(stage: str) -> None:
        result = query(
            stage + "-exact-time",
            {
                "observed_after_ns": frame["observed_at"],
                "observed_before_ns": frame["observed_at"],
                "observer": "u2",
                "limit": 5,
            },
        )
        if not any(
            row["frame_id"] == frame["frame_id"]
            and row["rgb_sha256"] == frame["rgb_sha256"]
            for row in result.get("records", [])
        ):
            raise RuntimeError(
                "Original frame was not recovered by exact observed time"
            )
        absent = query(stage + "-absence", {"observer": "never-recorded-uav"})
        if absent.get("status") != "succeeded" or absent.get("records"):
            raise RuntimeError("Unrecorded observer query returned invented evidence")

    lookup("history-before")
    before = json.loads((run / "data/u2/runtime-state.json").read_text())
    write_json(run / "runtime-before-restart.json", before)
    execute("history-restart", ["restart", "--timeout", "10", "uav-u2"])
    while time.monotonic() < deadline:
        after = json.loads((run / "data/u2/runtime-state.json").read_text())
        if after["sim_ns"] > before["sim_ns"]:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("Restarted companion did not resume its persisted run")
    if (
        after["run_id"] != before["run_id"]
        or after["uav_id"] != before["uav_id"]
        or not set(before["seen_messages"]).issubset(after["seen_messages"])
    ):
        raise RuntimeError(
            "Restart lost identity or persisted received-message records"
        )
    write_json(run / "runtime-after-restart.json", after)
    lookup("history-after")
    reanalysis = query("history-reanalysis", {"reanalyze_frame_id": frame["frame_id"]})
    if (
        reanalysis.get("original_rgb_sha256") != frame["rgb_sha256"]
        or reanalysis.get("observed_at") != frame["observed_at"]
    ):
        raise RuntimeError(
            "Historical reanalysis did not retain the original frame/hash/time"
        )
