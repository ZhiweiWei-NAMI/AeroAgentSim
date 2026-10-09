"""Run-scoped artifact REST and separate worker admission, never task success."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aeroagentsim.observations.artifacts import ArtifactStore
from aeroagentsim.services.app import create_app
from aeroagentsim.services.artifacts import mount_artifacts
from tests.observations.test_capture import FixtureRenderer, png, setup_capture


def test_rest_list_get_download_hash_and_readonly(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    run = root / "run-1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps({"id": "run-1", "status": "completed"})
    )
    kernel, capture, request = setup_capture(run, FixtureRenderer(png()))
    capture.store.register(request)
    record = capture.store.put(request, png(), renderer_mode="stub")
    with TestClient(create_app(root)) as client:
        base = "/v1/runs/run-1/artifacts"
        assert client.get(base).json() == [record]
        detail = client.get(base + "/" + record["digest"]).json()
        response = client.get(detail["download"])
        assert response.content == png()
        assert response.headers["x-content-sha256"] == record["digest"]
        assert int(response.headers["content-length"]) == len(png())
        assert client.get(base + "/" + "b" * 64).status_code == 404
        assert client.get(base + "/not-a-hash").status_code == 409
        assert client.get("/v1/runs/unknown/artifacts").status_code == 404
        assert (
            client.post("/v1/runs/run-1/capture-artifacts", json={}).status_code == 409
        )
        blob = run / "artifacts" / "blobs" / (record["digest"] + ".png")
        blob.write_bytes(b"corrupt")
        assert (
            client.get(base + "/" + record["digest"] + "/download").status_code == 409
        )
    kernel.close()


def test_upload_integrity_actor_cut_conflict_and_worker_event(tmp_path: Path) -> None:
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"status": "running"}))
    (run / "scenario.json").write_text(
        json.dumps(
            {
                "engines": {
                    "camera": {
                        "plugin": "capture",
                        "config": {
                            "storage_result_schema": "image.upload",
                            "ingress_stream_id": "capture",
                        },
                    }
                }
            }
        )
    )
    kernel, capture, request = setup_capture(run, FixtureRenderer(png()))
    capture.store.register(request)
    app = FastAPI()
    calls: list[dict[str, Any]] = []

    def directory(run_id: str) -> Path:
        if run_id != "run-1":
            raise HTTPException(404, "unknown run")
        return run

    def worker(run_id: str, operation: str, body: Any) -> dict[str, Any]:
        assert operation == "ingress"
        assert body["stream_id"] == "capture"
        assert body["schema"] == "image.upload"
        calls.append(body)
        return {"status": "submitted", "command_id": "test-admission-only"}

    mount_artifacts(app, directory, worker)
    from aeroagentsim.observations.contracts import content_digest

    body = {
        "request": request.to_data(),
        "png_base64": base64.b64encode(png()).decode(),
        "digest": content_digest(png()),
        "byte_count": len(png()),
        "at_ns": 1,
        "source_stamp": {
            "clock_id": "canonical",
            "mapping_id": "canonical",
            "numerator": 1,
            "denominator": 1,
        },
    }
    with TestClient(app) as client:
        url = "/v1/runs/run-1/capture-artifacts"
        assert client.post(url, json={**body, "digest": "0" * 64}).status_code == 422
        wrong = replace(request, actor=replace(request.actor, id="wrong"))
        assert (
            client.post(url, json={**body, "request": wrong.to_data()}).status_code
            == 422
        )
        wrong_cut = replace(request, source_cut=replace(request.source_cut, index=999))
        assert (
            client.post(url, json={**body, "request": wrong_cut.to_data()}).status_code
            == 422
        )
        assert calls == []
        result = client.post(url, json=body)
        assert result.status_code == 202, result.text
        assert result.json()["admission"]["status"] == "submitted"
        assert "accepted" not in result.json()
        assert len(calls) == 1
        assert ArtifactStore(run).list()[0]["digest"] == body["digest"]
        assert client.post(url, json=body).status_code == 202
        assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
        changed = png(value=80)
        conflict = {
            **body,
            "png_base64": base64.b64encode(changed).decode(),
            "digest": content_digest(changed),
            "byte_count": len(changed),
        }
        assert client.post(url, json=conflict).status_code == 422
        capture.store.close_request(request.request_id, "timeout")
        assert client.post(url, json=body).status_code == 422
    kernel.close()


def test_worker_reject_retains_storage_without_acceptance(tmp_path: Path) -> None:
    # Failure remains visible alongside the actual stored blob; it is not completion.
    store = ArtifactStore(tmp_path)
    store.delivery(
        "request", {"status": "admission_failed", "detail": "source stream timeout"}
    )
    records = list((tmp_path / "artifacts" / "deliveries").glob("*.json"))
    assert json.loads(records[0].read_text())["status"] == "admission_failed"
    assert store.list() == []


def test_service_output_scope_pins_scenario_without_mutating_input(
    tmp_path: Path,
) -> None:
    from aeroagentsim.scenario import load_scenario
    from aeroagentsim.services.artifacts import scope_capture_storage

    original = load_scenario(Path("scenarios/p1-slice.yaml"))
    original.document["engines"]["capture"] = {
        "plugin": "capture",
        "config": {"run_directory": "authored-location"},
    }
    scoped = scope_capture_storage(original, tmp_path / "service-run")
    assert scoped.engines["capture"]["config"]["run_directory"] == str(
        (tmp_path / "service-run").resolve()
    )
    assert original.engines["capture"]["config"]["run_directory"] == "authored-location"
    assert scoped.digest != original.digest
    assert scoped.registry is original.registry
    assert scoped.manifest is original.manifest
    assert not (tmp_path / "service-run").exists()


def test_exact_capture_prefix_is_readable_before_worker_index_refresh(
    tmp_path: Path,
) -> None:
    from aeroagentsim.observations.contracts import CaptureRequest
    from tests.observations.test_exports import finished_run

    root = tmp_path / "runs"
    run, _ = finished_run(root / "run-1")
    store = ArtifactStore(run)
    request = CaptureRequest.from_data(store.get("photo-1")["request"])
    # Simulate the actual worker's interval between committed waves and _index().
    (run / "index.json").write_text("[]")
    index_before = (run / "index.json").read_bytes()
    journal_before = (run / "journal.jsonl").read_bytes()
    with TestClient(create_app(root)) as client:
        response = client.get("/v1/runs/run-1/capture-requests/photo-1/scene")
        assert response.status_code == 200, response.text
        scene = response.json()
        assert scene["source_cut"] == request.to_data()["source_cut"]
        assert scene["commits"][-1]["commitIndex"] == request.source_cut.index
        assert scene["commits"][-1]["at"]["ns"] == str(request.source_cut.instant.ns)
        assert (
            client.get("/v1/runs/run-1/capture-requests/missing/scene").status_code
            == 404
        )
    assert (run / "index.json").read_bytes() == index_before
    assert (run / "journal.jsonl").read_bytes() == journal_before
