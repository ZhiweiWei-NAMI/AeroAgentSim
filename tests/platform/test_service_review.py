"""Deterministic review counterexamples against actual storage/worker/endpoints."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from aerokernel import Instant
from aerokernel.codec import encode
from fastapi.testclient import TestClient

from aeroagentsim.services import worker
from aeroagentsim.services.app import create_app
from aeroagentsim.services.storage import RunStorage


def artifacts(path: Path) -> RunStorage:
    path.mkdir(parents=True)
    rows = [
        {"index": index, "instant": encode(Instant(index)), "items": []}
        for index in range(4)
    ]
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    (path / "journal.jsonl").write_bytes(raw)
    (path / "index.json").write_text("[]")
    (path / "manifest.json").write_text(
        json.dumps(
            {"id": path.name, "status": "running", "scenario": "test", "until_ns": "3"}
        )
    )
    return RunStorage(path)


def test_h6_restart_recovers_complete_wal_prefix(tmp_path: Path) -> None:
    storage = artifacts(tmp_path / "runs" / "run")
    with TestClient(create_app(tmp_path / "runs")) as client:
        page = client.get("/v1/runs/run/commits?from=1").json()
    assert [item["commitIndex"] for item in page["commits"]] == [1, 2, 3]
    assert storage.metadata()["status"] == "interrupted"
    assert storage.metadata()["final_cursor"] == 4


def test_h4_sse_interleaving_cannot_lose_final_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = artifacts(tmp_path / "runs" / "run")
    storage.index()
    # Construct app while status terminal so startup does not consume the race.
    storage.status("completed")
    original = RunStorage.records
    raced = False

    def records(
        self: RunStorage, start: int = 0, limit: int = 1000
    ) -> list[dict[str, Any]]:
        nonlocal raced
        if not raced:
            raced = True
            self.status("completed")
            return []  # snapshot just before final index became visible
        return original(self, start, limit)

    monkeypatch.setattr(RunStorage, "records", records)
    with TestClient(create_app(tmp_path / "runs")) as client:
        response = client.get("/v1/runs/run/stream?from=1")
    assert "id: 3\n" in response.text
    assert '"finalCursor":4' in response.text.replace(" ", "")


def test_h5_worker_never_publishes_success_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]
) -> None:
    path = tmp_path / "run"
    storage = artifacts(path)
    statuses: list[str] = []
    original = RunStorage.status

    def status(self: RunStorage, outcome: str, *, error: str | None = None) -> None:
        statuses.append(outcome)
        original(self, outcome, error=error)

    class Session:
        now_ns = 0
        started_wall = 0.0

        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def run_until(self, ns: int) -> None:
            self.now_ns = ns

        def close(self) -> None:
            raise RuntimeError("CLOSE_FAILED")

    from aeroagentsim.scenario import load_scenario

    document["run"]["until_ns"] = 0
    scenario = load_scenario(document)
    monkeypatch.setattr(worker, "RunSession", Session)
    monkeypatch.setattr(RunStorage, "status", status)
    try:
        worker.execute(scenario, path, threading.Event(), threading.Event())
    except RuntimeError:
        pass
    assert "completed" not in statuses
    assert storage.metadata()["status"] == "faulted"
    assert "CLOSE_FAILED" in storage.metadata()["error"]


def test_h9_semantic_id_cannot_escape_output_root(
    document: dict[str, Any], tmp_path: Path
) -> None:
    document["id"] = "../escaped"
    document["run"]["until_ns"] = 0
    with TestClient(create_app(tmp_path / "runs")) as client:
        response = client.post("/v1/runs", json={"scenario": document})
        assert response.status_code == 201, response.text
        run = response.json()
        assert "/" not in run["id"] and ".." not in run["id"]
        assert (tmp_path / "runs" / run["id"] / "manifest.json").exists()
        assert run["scenario"] == "../escaped"
    assert not list(tmp_path.glob("escaped-*"))
