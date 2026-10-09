"""Deterministic review counterexamples against actual storage/worker/endpoints."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from aerokernel import BindingManifest, Kernel, MemoryRegistry
from fastapi.testclient import TestClient

from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.services import worker
from aeroagentsim.services.app import create_app
from aeroagentsim.services.storage import RunStorage


def test_created_run_has_committed_configuration_despite_delayed_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]
) -> None:
    original = RunSession.start

    def delayed_start(self: RunSession) -> Any:
        time.sleep(0.1)
        return original(self)

    monkeypatch.setattr(RunSession, "start", delayed_start)
    document["run"]["until_ns"] = 0
    with TestClient(
        create_app(tmp_path / "runs", studio_root=tmp_path / "studio")
    ) as client:
        created = client.post("/v1/runs", json=document)
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        configuration = client.get(f"/v1/studio/runs/{run_id}/configuration")
        assert configuration.status_code == 200, configuration.text
        assert configuration.json()["service_run_id"] == run_id
        assert configuration.json()["kernel_run_id"] == created.json()["kernel_run_id"]


def artifacts(path: Path) -> RunStorage:
    path.mkdir(parents=True)
    # An empty journal fixture still retains the pinned projection inputs.
    (path / "runtime.registry.json").write_text(
        json.dumps(MemoryRegistry(()).to_data())
    )
    (path / "scenario.json").write_text(json.dumps({"registry": {"messages": []}}))
    # Use an actual committed WAL prefix, including its codec/resource header.
    # The tests exercise index/feed races, not acceptance of fabricated journals.
    kernel = Kernel()
    kernel.bind(MemoryRegistry(()), BindingManifest("service-review", "0"), ())
    kernel.start()
    for ns in (1, 2, 3):
        kernel.run_until(ns)
    kernel.close()
    raw = b"".join(kernel.journal.bytes.splitlines(keepends=True)[:4])
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

    def status(
        self: RunStorage,
        outcome: str,
        *,
        error: str | None = None,
        waiting: dict[str, Any] | None = None,
    ) -> None:
        statuses.append(outcome)
        original(self, outcome, error=error, waiting=waiting)

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
