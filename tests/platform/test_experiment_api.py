"""Research plans use the existing HTTP boundary and ordinary run storage."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.services.app import create_app


def test_experiment_launch_progress_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", "research-test-token")
    root = tmp_path / "runs"
    with TestClient(create_app(root), base_url="http://localhost") as client:
        endpoint = "/v1/templates/parcel-delivery/experiments"
        assert client.post(endpoint, json={}).status_code == 401
        client.headers["Authorization"] = "Bearer research-test-token"
        thumbnail = client.get("/v1/templates/parcel-delivery/thumbnail")
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"] == "image/png"
        assert len(thumbnail.content) <= 120 * 1024
        assert (
            client.get("/v1/templates/parcel-delivery").json()["thumbnail_url"]
            == "/v1/templates/parcel-delivery/thumbnail"
        )
        assert client.post(endpoint, content="{}").status_code == 415
        assert (
            client.post(
                endpoint, json={}, headers={"Origin": "https://invalid.example"}
            ).status_code
            == 403
        )
        assert (
            client.post(endpoint, json={"vary": {"allocator": ["missing"]}}).status_code
            == 422
        )
        assert client.post(endpoint, json={"vary": {"unknown": [1]}}).status_code == 422
        assert client.post(endpoint, json={"seeds": True}).status_code == 422
        assert client.post(endpoint, json={"horizon_s": 0}).status_code == 422
        assert (
            client.post("/v1/templates/missing/experiments", json={}).status_code == 404
        )
        assert client.get("/v1/experiments/missing").status_code == 404
        launched = client.post(
            endpoint,
            json={
                "vary": {"allocator": ["nearest", "auction"]},
                "knobs": {"fleet_size": 2},
                "seeds": 1,
                "jobs": 2,
                "horizon_s": 5,
            },
        )
        assert launched.status_code == 201, launched.text
        identifier = launched.json()["id"]
        assert launched.json()["template_title_zh"] == "城市包裹配送"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            response = client.get(f"/v1/experiments/{identifier}")
            assert response.status_code == 200, response.text
            state = response.json()
            if state["status"] in {"completed", "faulted", "interrupted"}:
                break
            time.sleep(0.05)
        assert state["status"] == "completed", state
        assert state["total"] == 2 and state["completed"] == 2
        assert state["failed"] == 0 and len(state["table"]) == 2
        rows = client.get("/v1/runs").json()
        assert len(rows) == 2
        assert {row["knobs"]["allocator"] for row in rows} == {"nearest", "auction"}
        assert all(row["experiment"]["id"] == identifier for row in rows)
        assert all(row["template"]["title_zh"] == "城市包裹配送" for row in rows)
        assert all(row["title_zh"] == row["template_title_zh"] == "城市包裹配送" for row in rows)
        compared = client.get(
            "/v1/runs/compare", params={"ids": ",".join(row["id"] for row in rows)}
        )
        assert compared.status_code == 200, compared.text
        assert len(compared.json()["differences"]["algorithms"]) == 1
        assert (root / ".experiments" / identifier / "experiment.csv").is_file()
        assert (root / ".experiments" / identifier / "experiment.md").is_file()

    with TestClient(
        create_app(root),
        base_url="http://localhost",
        headers={"Authorization": "Bearer research-test-token"},
    ) as client:
        restored = client.get(f"/v1/experiments/{identifier}").json()
        assert restored["status"] == "completed"
        assert restored["table"] == state["table"]
        assert restored["template_title_zh"] == "城市包裹配送"
