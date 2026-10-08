"""HTTP worker ownership, replay pagination, controls and SSE termination."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from aeroagentsim.services.app import create_app


def wait_status(client: TestClient, run_id: str, expected: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        runs = client.get("/v1/runs").json()
        run = next(row for row in runs if row["id"] == run_id)
        if run["status"] in expected:
            return run  # type: ignore[no-any-return]
        if run["status"] == "faulted":
            raise AssertionError(run)
        time.sleep(0.02)
    raise AssertionError(f"run did not reach {expected}: {runs}")


def test_api_run_page_sse_and_no_viewer_writes(
    document: dict[str, Any], tmp_path: Path
) -> None:
    document["run"]["until_ns"] = 2_000_000_000
    with TestClient(create_app(tmp_path / "runs")) as client:
        response = client.post("/v1/runs", json={"scenario": document})
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        wait_status(client, run_id, {"completed"})
        journal = tmp_path / "runs" / run_id / "journal.jsonl"
        digest = hashlib.sha256(journal.read_bytes()).hexdigest()
        assert (
            client.get(f"/v1/runs/{run_id}/header").json()["contract"]
            == "aeroagentsim.viewer-feed/v1"
        )
        first = client.get(f"/v1/runs/{run_id}/commits?from=0&limit=2").json()
        assert [c["commitIndex"] for c in first["commits"]] == [1, 2]
        second = client.get(
            f"/v1/runs/{run_id}/commits", params={"from": first["next"], "limit": 2}
        ).json()
        assert second["commits"][0]["commitIndex"] == 3
        with client.stream("GET", f"/v1/runs/{run_id}/stream?from=1") as stream:
            text = "".join(stream.iter_text())
            assert "event: commit" in text and "event: end" in text
        assert hashlib.sha256(journal.read_bytes()).hexdigest() == digest
        assert client.post(f"/v1/runs/{run_id}/resume").status_code == 409
        assert client.get(f"/v1/runs/{run_id}/commits?from=-1").status_code == 422
        assert client.get("/v1/runs/missing/header").status_code == 404


def test_safe_boundary_pause_resume_stop(
    document: dict[str, Any], tmp_path: Path
) -> None:
    document["run"].update(
        until_ns=10_000_000_000, advance_ns=100_000_000, pacing="realtime"
    )
    with TestClient(create_app(tmp_path / "runs")) as client:
        response = client.post("/v1/runs", json=document)
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        wait_status(client, run_id, {"running"})
        assert client.post(f"/v1/runs/{run_id}/pause").status_code == 200
        wait_status(client, run_id, {"paused"})
        journal = tmp_path / "runs" / run_id / "journal.jsonl"
        before = journal.read_bytes()
        time.sleep(0.2)
        assert journal.read_bytes() == before
        assert client.post(f"/v1/runs/{run_id}/resume").status_code == 200
        wait_status(client, run_id, {"running"})
        assert client.post(f"/v1/runs/{run_id}/stop").status_code == 200
        wait_status(client, run_id, {"stopped"})


def test_path_scope_and_invalid_body(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs", scenario_root=tmp_path)) as client:
        assert (
            client.post(
                "/v1/runs", json={"scenario_path": "../../etc/passwd"}
            ).status_code
            == 422
        )
        assert client.post("/v1/runs", json={"format": "unknown"}).status_code == 422
