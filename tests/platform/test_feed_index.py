"""Persistent cuts preserve projection semantics and resume only the new WAL tail."""

from __future__ import annotations

import json
import subprocess
import sys
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
    # The feed projects from the run's pinned, compiled declarations.
    pinned = json.loads((directory / "scenario.json").read_text())
    subjects = SubjectProjection(registry, pinned)
    kpis = KPIProjector(registry, pinned.get("presentation", []))
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


def test_live_cache_recovers_after_failed_projection_and_external_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aeroagentsim.services import feed_index

    prepared = TemplateRegistry().apply("parcel-delivery", {"fleet_size": 2})
    prepared.document["run"]["until_ns"] = 10_000_000_000
    directory = tmp_path / "live"
    with RunSession(
        load_scenario(prepared.document, base=prepared.base), directory
    ) as run:
        run.run()
    storage = RunStorage(directory)
    entries = storage.entries
    # Compute the oracle from the actual complete journal, then rebuild its
    # derived index as a growing live prefix.
    expected = page(directory, 1, len(entries))
    storage._atomic("index.json", entries[:9])
    assert page(directory, 1, 8) == expected[:8]
    storage._atomic("index.json", entries[:17])
    original = feed_index.project

    def fail_after_consuming(record: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        cut = original(record, **kwargs)
        if record["index"] == 11:
            raise RuntimeError("projection interrupted after consuming KPI state")
        return cut

    monkeypatch.setattr(feed_index, "project", fail_after_consuming)
    with pytest.raises(RuntimeError, match="projection interrupted"):
        page(directory, 9, 8)
    monkeypatch.setattr(feed_index, "project", original)
    assert page(directory, 9, 8) == expected[8:16]

    # Another process can publish while this reader retains its previous live
    # cursor. The persisted checkpoint must win over that stale cached cursor.
    storage._atomic("index.json", entries)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from aeroagentsim.services.feed_index import update; import sys; update(Path(sys.argv[1]))",
            str(directory),
        ],
        check=True,
    )
    assert page(directory, 1, len(entries)) == expected
    assert kpi_result(directory)["final"] == expected[-1]["kpis"]
    assert RunStorage(directory).entries == entries


def test_cached_offset_rows_remain_private_to_each_storage(tmp_path: Path) -> None:
    storage = RunStorage(tmp_path)
    expected = [{"index": 0, "offset": 0, "length": 10}]
    storage._atomic("index.json", expected)
    first, second = RunStorage(tmp_path), RunStorage(tmp_path)
    first.entries[0]["length"] = 99
    first.entries.append({"index": 1, "offset": 99, "length": 5})
    assert second.entries == expected
    assert RunStorage(tmp_path).entries == expected
