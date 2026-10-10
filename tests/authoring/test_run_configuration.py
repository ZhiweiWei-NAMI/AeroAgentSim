"""Experiment replays read real WAL identities through the console API."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.workspace import WorkspaceStore


def test_descriptive_experiment_run_identity_and_containment(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run_id = "parcel-delivery-exp-real-c0-s0"
    run = root / run_id
    run.mkdir(parents=True)
    (run / "scenario.json").write_text(json.dumps({"id": "parcel-delivery"}))
    (run / "journal.jsonl").write_text(
        json.dumps(
            {"type": "header", "index": 0, "run_id": "kernel-parcel", "epoch": "0", "resolved_bindings": []}
        )
        + "\n"
    )
    app = FastAPI()
    app.include_router(create_router(WorkspaceStore(tmp_path / "workspaces", None), extracts={}, run_root=root))
    with TestClient(app) as client:
        response = client.get(f"/v1/studio/runs/{run_id}/configuration")
        assert response.status_code == 200
        assert response.json()["service_run_id"] == run_id
        assert response.json()["kernel_run_id"] == "kernel-parcel"
        assert response.json()["epoch"] == "0"
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "escape").symlink_to(outside, target_is_directory=True)
        assert client.get("/v1/studio/runs/escape/configuration").status_code == 422
