"""Public-clone Studio browses and validates committed snapshot declarations."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.services.app import create_app


def test_snapshot_only_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    with TestClient(
        create_app(tmp_path / "runs", studio_root=tmp_path / "studio"),
        base_url="http://localhost",
    ) as client:
        workspace = client.post(
            "/v1/studio/workspaces", json={"name": "Public demo"}
        ).json()["id"]
        response = client.post(
            f"/v1/studio/workspaces/{workspace}/templates/traffic-accident",
            json={"console": True},
        )
        assert response.status_code == 200, response.text
        catalog = client.get("/v1/studio/types", params={"workspace": workspace}).json()
        assert catalog["catalog_scope"] == "scenario-snapshot"
        assert "limited" in catalog["catalog_notice"]
        assert any(row["id"] == "aas:TrafficRoadVehicle" for row in catalog["types"])
        assert any(
            row["id"] == "traffic.road.position_enu_m" for row in catalog["fields"]
        )
        assert any(row["id"] == "traffic.task-assignee" for row in catalog["relations"])
        detail = client.get(
            "/v1/studio/types/aas:TrafficUAV", params={"workspace": workspace}
        ).json()
        assert detail["parents"] == ["oo:UAV"]
        assert any(
            row["id"] == "he.aircraft.position_enu_m" for row in detail["fields"]
        )
        validation = client.post(f"/v1/studio/workspaces/{workspace}/validate", json={})
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"] is True
