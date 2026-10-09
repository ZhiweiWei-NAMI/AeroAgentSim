"""Release regressions for scenario paths and the HTTP submission boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.services.app import create_app


def test_scenario_path_keeps_new_run_under_output_root(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    document["run"]["until_ns"] = 0
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(document))
    root = tmp_path / "runs"
    with TestClient(
        create_app(root, scenario_root=tmp_path), base_url="http://localhost"
    ) as client:
        response = client.post("/v1/runs", json={"scenario_path": scenario_file.name})
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        assert (root / run_id / "manifest.json").is_file()
        assert client.get(f"/v1/runs/{run_id}/header").status_code == 200
    assert scenario_file.read_text() == json.dumps(document)


def test_submission_boundary_and_configured_console(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AEROAGENTSIM_CONSOLE_URL", "https://console.example")
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    document["run"]["until_ns"] = 0
    with TestClient(
        create_app(tmp_path / "runs"), base_url="http://localhost"
    ) as client:
        for headers, expected in (
            ({"Origin": "https://evil.invalid"}, 403),
            ({"Host": "evil.invalid"}, 403),
            ({"Host": "localhost.evil.invalid"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ):
            response = client.post(
                "/v1/runs",
                content=json.dumps(document),
                headers={"Content-Type": "application/json", **headers},
            )
            assert response.status_code == expected, response.text
        assert not client.get("/v1/runs").json()
        preflight = client.options(
            "/v1/runs",
            headers={
                "Origin": "https://console.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Authorization",
            },
        )
        assert preflight.status_code == 200
        assert (
            preflight.headers["access-control-allow-origin"]
            == "https://console.example"
        )
        response = client.post(
            "/v1/runs",
            json=document,
            headers={"Origin": "https://console.example", "Host": "console.example"},
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        assert client.get(f"/v1/runs/{run_id}/header").status_code == 200
        # Controls use the same JSON boundary even when they have no parameters.
        assert client.post(f"/v1/runs/{run_id}/stop", json={}).status_code in {200, 409}

    monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", "dummy-api-token")
    with TestClient(
        create_app(tmp_path / "authenticated"), base_url="http://localhost"
    ) as client:
        assert client.get("/v1/runs").status_code == 401
        assert client.post("/v1/runs", json=document).status_code == 401
        client.headers["Authorization"] = "Bearer dummy-api-token"
        assert client.get("/v1/runs").status_code == 200
        assert client.post("/v1/runs", json=document).status_code == 201
