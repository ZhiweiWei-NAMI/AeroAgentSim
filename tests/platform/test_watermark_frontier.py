"""Concurrent source producers reconcile real frontiers without invalid closures."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from aerokernel.journal import replay

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.services.app import create_app
from aeroagentsim.services.worker import superseded_watermark
from tests.platform.test_workbench_lifecycle import wait_status


def conditional(expected: int, requested: int) -> dict:
    return {"stream_id": "operator", "expected_watermark_ns": expected, "watermark_ns": requested}


@pytest.mark.parametrize("body", [conditional(-1, 1), conditional(2, 1), conditional(2, 3),
                                  {**conditional(0, 1), "stream_id": "missing"},
                                  {**conditional(0, 1), "source_stamp": {}},
                                  {**conditional(0, 1), "expected_watermark_ns": True}])
def test_invalid_conditional_frontier_is_never_acknowledged(body: dict) -> None:
    with pytest.raises((ValueError, TypeError)):
        superseded_watermark(body, {"operator": 1})


def test_concurrent_and_terminal_source_closures_return_real_frontier_without_records(tmp_path: Path) -> None:
    prepared = TemplateRegistry().apply("food-delivery", {"run_mode": "interactive"})
    prepared.document["run"]["until_ns"] = 3_000_000_000
    with TestClient(create_app(tmp_path / "runs", scenario_root=prepared.base), base_url="http://localhost") as client:
        created = client.post("/v1/runs", json=prepared.document)
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        url = f"/v1/runs/{run_id}"
        wait_status(client, run_id, {"waiting_for_input"})
        first = client.post(url + "/watermark", json=conditional(0, 1_000_000_000))
        assert first.status_code == 200, first.text
        assert first.json()["advanced"] is True
        wait_status(client, run_id, {"waiting_for_input"})
        stale = conditional(0, 500_000_000)
        frontier = client.post(url + "/watermark", json=stale)
        assert frontier.status_code == 200, frontier.text
        assert frontier.json() == {"contract": "aeroagentsim.watermark-receipt/v2", "stream_id": "operator",
                                   "watermark_ns": 1_000_000_000, "advanced": False}
        strict = client.post(url + "/watermark", json={"stream_id": "operator", "watermark_ns": 0})
        assert strict.status_code == 422
        assert strict.json()["detail"]["code"] == "INGRESS_WATERMARK"
        closed = client.post(url + "/watermark", json={"stream_id": "operator", "watermark_ns": 3_000_000_000})
        assert closed.status_code == 200
        wait_status(client, run_id, {"completed"})
        journal = tmp_path / "runs" / run_id / "journal.jsonl"
        before = journal.read_bytes()
        terminal = client.post(url + "/watermark", json=conditional(1_000_000_000, 2_000_000_000))
        assert terminal.status_code == 200, terminal.text
        assert terminal.json()["advanced"] is False
        assert terminal.json()["watermark_ns"] == 3_000_000_000
        assert journal.read_bytes() == before
        assert dict(replay(journal).ingress_watermarks) == {"operator": 3_000_000_000}
        # No successful write is synthesized at a terminal source frontier.
        refused = client.post(url + "/watermark", json=conditional(3_000_000_000, 4_000_000_000))
        assert refused.status_code == 409
        assert journal.read_bytes() == before
