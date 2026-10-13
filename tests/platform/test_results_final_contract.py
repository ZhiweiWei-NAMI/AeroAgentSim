"""Terminal status and final KPI/Story data use separate committed boundaries."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from aerokernel import BindingManifest, Kernel, MemoryRegistry
from fastapi.testclient import TestClient

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services import feed_index, worker
from aeroagentsim.services.app import create_app
from aeroagentsim.services.storage import RunStorage


@pytest.fixture
def finished(tmp_path: Path) -> Path:
    path = tmp_path / "runs" / "run-final"
    path.mkdir(parents=True)
    registry = MemoryRegistry(())
    (path / "scenario.json").write_text(json.dumps({"id": "empty", "registry": {"messages": []}}))
    (path / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    kernel = Kernel()
    kernel.bind(registry, BindingManifest("final-contract", "0"), ())
    kernel.start()
    for ns in range(1, 150):
        kernel.run_until(ns)
    kernel.close()
    (path / "journal.jsonl").write_bytes(kernel.journal.bytes)
    (path / "index.json").write_text("[]")
    (path / "manifest.json").write_text(json.dumps({"id": path.name, "status": "running"}))
    storage = RunStorage(path)
    storage.status("completed")
    storage.publish_prefix(*kernel.acknowledged_prefix)
    return path


def test_terminal_records_wait_for_committed_kpi_and_story(finished: Path) -> None:
    storage = RunStorage(finished)
    assert feed_index.results_state(finished)["results_final"] is False
    feed_index.update(finished, through_index=2)
    assert feed_index.results_state(finished)["results_cursor"] == 2
    with TestClient(create_app(finished.parent), base_url="http://localhost") as client:
        url = f"/v1/runs/{finished.name}"
        record = client.get(url).json()
        assert record["status"] == "completed" and record["results_final"] is False
        assert client.get("/v1/runs", params={"include_kpis": False}).json()[0]["results_final"] is False
        result = client.get(url + "/kpis").json()
        assert result["results_final"] is True
        cursor = storage.metadata()["final_cursor"]
        assert result["results_cursor"] == cursor
        assert result["final_commit_index"] == cursor - 1
        last = client.get(url + "/commits", params={"from": cursor - 1}).json()
        assert last["results_final"] is True
        assert last["commits"][-1]["kpis"] == result["final"]
        assert "events" in last["commits"][-1]
        assert client.get(url).json()["results_final"] is True
        assert client.get("/v1/runs", params={"include_kpis": False}).json()[0]["results_final"] is True


def test_projection_failure_never_claims_final_results(
    finished: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed_index.update(finished, through_index=2)
    cursor = feed_index.results_state(finished)["results_cursor"]
    original = feed_index.NarrativeProjector.consume

    def failed(self: Any, record: dict[str, Any]) -> Any:
        original(self, record)
        raise RuntimeError("Story projection failed")

    monkeypatch.setattr(feed_index.NarrativeProjector, "consume", failed)
    with pytest.raises(RuntimeError, match="Story projection failed"):
        feed_index.update(finished)
    state = feed_index.results_state(finished)
    assert state["results_cursor"] == cursor and state["results_final"] is False
    monkeypatch.setattr(feed_index.NarrativeProjector, "consume", original)
    feed_index.update(finished)
    assert feed_index.results_state(finished)["results_final"] is True


def test_acknowledgement_and_source_signature_must_match(finished: Path) -> None:
    storage = RunStorage(finished)
    final = storage.entries[-1]
    previous = storage.entries[-2]
    storage.publish_prefix(previous["offset"] + previous["length"], previous["index"])
    feed_index.update(finished)
    assert feed_index.results_state(finished)["results_final"] is False
    storage.publish_prefix(final["offset"] + final["length"], final["index"])
    feed_index.update(finished)
    assert feed_index.results_state(finished)["results_final"] is True
    scenario = finished / "scenario.json"
    document = json.loads(scenario.read_text())
    document["id"] = "changed"
    scenario.write_text(json.dumps(document))
    assert feed_index.results_state(finished)["results_final"] is False


def test_results_state_is_read_only_and_requires_a_terminal_boundary(finished: Path) -> None:
    before = {path.name: path.read_bytes() for path in finished.iterdir()}
    assert feed_index.results_state(finished)["results_final"] is False
    assert {path.name: path.read_bytes() for path in finished.iterdir()} == before
    feed_index.update(finished)
    metadata = RunStorage(finished).metadata()
    assert feed_index.results_state(finished, {**metadata, "status": "running"})["results_final"] is False
    metadata.pop("final_cursor")
    assert feed_index.results_state(finished, metadata)["results_final"] is False


def test_interrupted_run_finalizes_only_its_acknowledged_prefix(finished: Path) -> None:
    storage = RunStorage(finished)
    acknowledged = storage.entries[-3]
    storage.publish_prefix(acknowledged["offset"] + acknowledged["length"], acknowledged["index"])
    storage.status("interrupted", error="authored worker interruption")
    assert storage.metadata()["final_cursor"] == acknowledged["index"] + 1
    feed_index.update(finished)
    state = feed_index.results_state(finished)
    assert state["results_final"] is True
    assert state["final_commit_index"] == acknowledged["index"]


def test_status_poll_does_not_inflate_full_projector_state(finished: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feed_index.update(finished)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Status poll inflated the full checkpoint")

    monkeypatch.setattr(feed_index, "_decode", forbidden)
    assert feed_index.results_state(finished)["results_final"] is True


def test_cleanup_failure_still_publishes_its_real_acknowledged_tail(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    document["run"]["until_ns"] = 0
    scenario = load_scenario(document)
    path = tmp_path / "faulted"
    storage = RunStorage(path)
    storage.prepare(scenario)
    original = RunSession.close

    def failed_close(self: RunSession) -> None:
        original(self)
        raise RuntimeError("authored cleanup failure")

    monkeypatch.setattr(RunSession, "close", failed_close)
    monkeypatch.setattr(worker, "close_inherited_network_sockets", lambda: None)
    worker.execute(scenario, path, threading.Event(), threading.Event())
    storage = RunStorage(path)
    assert storage.metadata()["status"] == "faulted"
    assert "authored cleanup failure" in storage.metadata()["error"]
    assert storage.metadata()["final_cursor"] == storage.entries[-1]["index"] + 1
    assert feed_index.results_state(path)["results_final"] is False
    feed_index.update(path)
    assert feed_index.results_state(path)["results_final"] is True
