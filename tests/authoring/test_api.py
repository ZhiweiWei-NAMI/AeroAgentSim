"""Real authoring REST and kernel-run/replay integration, without Docker."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from aeroagentsim.authoring.inputs import configured_ontology
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.services.app import create_app

try:
    ONTOLOGY = configured_ontology()
except ValueError as error:  # unconfigured: AEROAGENTSIM_AEROGRAPH_ROOT unset/invalid
    pytest.skip(str(error), allow_module_level=True)


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "drafts", ONTOLOGY)


def placed(store: WorkspaceStore) -> dict[str, Any]:
    w = store.create("experiment")
    for i in range(2):
        w = store.place(
            w["id"],
            {
                "id": f"uav-{i + 1}",
                "type": "oo:UAV",
                "kind": "entity",
                "position": [0, i * 12, 10],
                "engine": "kinematic",
            },
        )
    return store.place(
        w["id"],
        {
            "id": "facility-1",
            "type": "aas:StudioFacility",
            "kind": "facility",
            "position": [10, 10, 0],
            "engine": "workflow",
        },
    )


def test_draft_to_yaml_real_validation_and_persistence(store: WorkspaceStore) -> None:
    w = placed(store)
    assert store.validate(w["id"])["valid"]
    exported = yaml.safe_load(store.export(w["id"]))
    assert exported == w["scenario"]
    other = store.create("import")
    assert (
        store.import_yaml(other["id"], yaml.safe_dump(exported))["scenario"] == exported
    )
    restarted = WorkspaceStore(store.root, ONTOLOGY)
    assert restarted.get(w["id"])["scenario"] == exported
    assert {b["writer"] for b in exported["bindings"]["exact"]} == {
        "motion",
        "operations",
    }


@pytest.mark.parametrize(
    "position", [[0, 0], [None, 0, 0], [float("nan"), 0, 0], [False, 0, 0]]
)
def test_invalid_placement_leaves_draft_unchanged(
    store: WorkspaceStore, position: list[Any]
) -> None:
    w = store.create("invalid")
    with pytest.raises(ValueError, match="finite ENU"):
        store.place(
            w["id"],
            {
                "id": "a",
                "type": "oo:UAV",
                "kind": "entity",
                "position": position,
                "engine": "kinematic",
            },
        )
    assert store.get(w["id"]) == w


def test_invalid_import_and_path_scope_preserve_draft(store: WorkspaceStore) -> None:
    w = store.create("import")
    for body in ["{broken", "null", "a: 1\na: 2\n"]:
        with pytest.raises((ValueError, TypeError)):
            store.import_yaml(w["id"], body)
        assert store.get(w["id"]) == w
    bad = copy.deepcopy(w["scenario"])
    bad["registry"]["compile"]["root"] = "/etc"
    with pytest.raises(ValueError, match="configured AeroGraph"):
        store.import_yaml(w["id"], yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="identifier"):
        store.get("../escape")


def test_duplicate_abstract_unknown_types_rejected(store: WorkspaceStore) -> None:
    w = placed(store)
    for identifier, typ, match in [
        ("uav-1", "oo:UAV", "duplicate"),
        ("abstract", "oo:ModelObject", "abstract"),
        ("missing", "oo:NotAType", "Unresolved"),
    ]:
        with pytest.raises(ValueError, match=match):
            store.place(
                w["id"],
                {
                    "id": identifier,
                    "type": typ,
                    "kind": "entity",
                    "position": [0, 0, 0],
                    "engine": "workflow",
                },
            )


def test_writer_conflict_is_real_diagnostic(store: WorkspaceStore) -> None:
    w = placed(store)
    doc = copy.deepcopy(w["scenario"])
    doc["bindings"]["exact"][0]["writer"] = "missing"
    result = store.validate(w["id"], doc)
    assert result["valid"] is False
    assert result["errors"]
    with pytest.raises(ValueError, match="export"):
        store.export(w["id"])


def test_catalog_and_real_run_replay(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "runs", scenario_root=tmp_path, studio_root=tmp_path / "studio"
    )
    with TestClient(app) as client:
        catalog = client.get("/v1/studio/catalog")
        assert catalog.status_code == 200 and catalog.json()["extracts"]
        types = client.get("/v1/studio/types").json()["types"]
        assert len(types) == 970
        detail = client.get("/v1/studio/types/oo:UAV").json()
        assert detail["parents"] and any(
            f["metadata"].get("unit") for f in detail["fields"]
        )
        assert (
            client.post("/v1/studio/workspaces", json={"name": ""}).status_code == 422
        )
        w = client.post("/v1/studio/workspaces", json={"name": "browser"}).json()
        for i in range(2):
            response = client.post(
                f"/v1/studio/workspaces/{w['id']}/place",
                json={
                    "id": f"uav-{i}",
                    "type": "oo:UAV",
                    "kind": "entity",
                    "position": [0, i * 12, 10],
                    "engine": "kinematic",
                },
            )
            assert response.status_code == 200, response.text
            w = response.json()
        w = client.post(
            f"/v1/studio/workspaces/{w['id']}/place",
            json={
                "id": "facility",
                "type": "aas:StudioFacility",
                "kind": "facility",
                "position": [10, 0, 0],
                "engine": "workflow",
            },
        ).json()
        endpoint = f"/v1/studio/workspaces/{w['id']}"
        assert (
            client.post(endpoint + "/validate", json={"scenario": None}).status_code
            == 422
        )
        assert client.post(endpoint + "/validate", json={}).json()["valid"]
        assert client.get(endpoint + "/export").status_code == 200
        run = client.post(
            "/v1/runs", json={"scenario": w["scenario"], "studio_workspace": w["id"]}
        )
        assert run.status_code == 201, run.text
        run_id = run.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = next(
                r for r in client.get("/v1/runs").json() if r["id"] == run_id
            )["status"]
            if status in {"completed", "faulted", "interrupted"}:
                break
            time.sleep(0.05)
        assert status == "completed"
        assert client.get(f"/v1/runs/{run_id}/header").status_code == 200
        commits = client.get(f"/v1/runs/{run_id}/commits").json()["commits"]
        assert commits and any(c["facts"] for c in commits)


def test_workspace_recency_uses_persisted_edits(store: WorkspaceStore) -> None:
    first = store.create("First experiment")
    second = store.create("Second experiment")
    edited = store.save(first["id"], {"name": "Renamed experiment"})
    assert edited["created_at"] == first["created_at"]
    assert edited["updated_at"] > second["updated_at"]
    assert [item["id"] for item in store.list()] == [first["id"], second["id"]]
    path = store.directory(second["id"]) / "draft.json"
    legacy = {key: value for key, value in second.items() if key != "updated_at"}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    from datetime import datetime, timezone

    assert store.get(second["id"])["updated_at"] == datetime.fromtimestamp(
        path.stat().st_mtime, timezone.utc
    ).isoformat()
