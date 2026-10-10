"""Persistent cuts preserve projection semantics and resume only the new WAL tail."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel.registry import MemoryRegistry
from fastapi.testclient import TestClient

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.app import create_app
from aeroagentsim.services.feed_index import kpi_result, page
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.narratives import NarrativeProjector
from aeroagentsim.services.projector import project
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.subjects import SubjectProjection


def test_persistent_page_resume_and_source_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    prepared = TemplateRegistry().apply("parcel-delivery", {"fleet_size": 2})
    prepared.document["run"]["until_ns"] = 20_000_000_000
    directory = tmp_path / "parcel"
    with RunSession(
        load_scenario(prepared.document, base=prepared.base), directory
    ) as run:
        run.run()
    storage = RunStorage(directory)
    records = storage.records(1, 4096)
    registry = MemoryRegistry.from_data(
        json.loads((directory / "runtime.registry.json").read_text())
    )
    subjects = SubjectProjection(registry, prepared.document)
    kpis = KPIProjector(registry, prepared.document.get("presentation"))
    narratives = NarrativeProjector.from_directory(directory)
    expected = [
        {
            **project(record, subjects=subjects, kpis=kpis),
            "events": narratives.consume(record),
        }
        for record in records
    ]
    all_events = [event for cut in expected for event in cut["events"]]
    assert all_events
    assert all("text_zh" in event for event in all_events)
    assert all(
        any("\u4e00" <= char <= "\u9fff" for char in event["text_zh"])
        for event in all_events
    )
    entries = storage.entries
    midpoint = len(entries) // 2
    storage._atomic("index.json", entries[:midpoint])
    first = page(directory, 1, 4096)
    assert first == expected[: midpoint - 1]

    original_records = RunStorage.records
    reads: list[int] = []

    def tracked(
        self: RunStorage, start: int = 0, limit: int = 1000
    ) -> list[dict[str, Any]]:
        reads.append(start)
        return original_records(self, start, limit)

    monkeypatch.setattr(RunStorage, "records", tracked)
    storage._atomic("index.json", entries)
    tail = page(directory, midpoint, 4096)
    assert tail == expected[midpoint - 1 :]
    assert reads and min(reads) == midpoint
    assert kpi_result(directory)["final"] == expected[-1]["kpis"]
    reads.clear()
    assert page(directory, midpoint, 3) == expected[midpoint - 1 : midpoint + 2]
    assert not reads  # SQLite survives reconnects; no lifecycle-prefix replay.

    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        response = client.get("/v1/runs/parcel/commits", params={"limit": 4096})
        assert response.status_code == 200, response.text
        assert response.json()["commits"] == expected
        stream = client.get("/v1/runs/parcel/stream")
        assert stream.status_code == 200, stream.text
        streamed = [
            json.loads(line.removeprefix("data: "))
            for block in stream.text.split("\n\n")
            if "event: commit\n" in block
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        assert streamed == expected
        assert [event for cut in streamed for event in cut["events"]] == all_events

    scenario_file = directory / "scenario.json"
    scenario = json.loads(scenario_file.read_text())
    scenario["event_narratives"] = {
        "lowalt.task.created": {
            "template": "Parcel {task} arrived",
            "category": "logistics",
            "severity": "notice",
            "entities": ["task"],
        }
    }
    scenario_file.write_text(json.dumps(scenario))
    rebuilt = page(directory, 1, 4096)
    assert reads[0] == 1
    assert any(
        event["text"].startswith("Parcel ")
        for cut in rebuilt
        for event in cut["events"]
    )
    assert rebuilt[-1]["kpis"] == expected[-1]["kpis"]
    for cut in rebuilt:
        for event in cut["events"]:
            if event["text"].startswith("Parcel "):
                assert "text_zh" not in event  # Never reuse the overwritten pack wording.
