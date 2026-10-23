"""Run-time pins distinguish matching definitions from current-code recomputation."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from typing import Any

import pytest
from aerokernel.registry import MemoryRegistry
from fastapi.testclient import TestClient

from aeroagentsim import metrics_api, presentation_api
from aeroagentsim.metrics_api import MetricDescriptor, MetricPlugin
from aeroagentsim.platform import RunSession
from aeroagentsim.runs import (
    RUN_READER_API_VERSION,
    NoCommittedPrefix,
    ProjectionMode,
    ProjectionProvenance,
    RecordedMessage,
    open_run,
    projection_provenance,
    projections,
)
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services import feed_index, worker
from aeroagentsim.services.app import create_app
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.narratives import NarrativeProjector
from aeroagentsim.services.projector import project
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.subjects import SubjectProjection


class _Reducer:
    version = "1"
    input_schemas = ("aeroagentsim.metrics.",)
    descriptors = (MetricDescriptor(
        id="projection.samples", label="Observed physical samples", label_zh="已观测物理样本",
        unit="count", direction=None,
        aggregation="Count recorded physical evidence items in journal/item order.",
        missing_data_policy="None until a matching evidence item was observed.",
    ),)

    def initial(self) -> list[int]:
        return []

    def consume(self, state: list[int], message: RecordedMessage) -> None:
        state.append(1)

    def snapshot(self, state: list[int]) -> dict[str, int | None]:
        return {self.descriptors[0].id: len(state) if state else None}


@pytest.fixture
def definitions(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Reducer]:
    reducer = _Reducer()
    metric = MetricPlugin("projection-test", (reducer,))
    display = presentation_api.PresentationPlugin("projection-test", {
        "format": presentation_api.PRESENTATION_FORMAT,
        "labels": {"Pinned label": {"en": "Pinned label", "zh": "固定标签"}},
    })
    monkeypatch.setattr(metrics_api, "BUILTINS", {"projection-test": lambda: metric})
    monkeypatch.setattr(metrics_api, "entry_points", lambda **kwargs: ())
    monkeypatch.setattr(presentation_api, "BUILTINS", {"projection-test": lambda: display})
    monkeypatch.setattr(presentation_api, "entry_points", lambda **kwargs: ())
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    presentation_api.clear_presentation_plugin_cache()
    try:
        yield reducer
    finally:
        presentation_api.clear_presentation_plugin_cache()


@pytest.fixture
def launched(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Path]]:
    document["run"]["until_ns"] = 0
    application = create_app(tmp_path / "runs")

    def start(scenario: Any, path: Path, **kwargs: Any) -> Path:
        # Exercise actual HTTP snapshot pinning and the actual worker writer,
        # without a child process, live pacing or background cache warmup.
        assert (path / "presentation.snapshot.json").exists()
        assert not (path / projections.PROJECTION_PINS_FILENAME).exists()
        worker.execute(scenario, path, threading.Event(), threading.Event())
        assert RunStorage(path).metadata()["status"] == "completed"
        return path

    async def wait_started(path: Path) -> dict[str, Any]:
        return RunStorage(path).metadata()

    monkeypatch.setattr(application.state.worker_registry, "start", start)
    monkeypatch.setattr(application.state.worker_registry, "wait_started", wait_started)
    with TestClient(application, base_url="http://localhost") as client:
        response = client.post("/v1/runs", json={"scenario": document})
        assert response.status_code == 201, response.text
        yield client, tmp_path / "runs" / response.json()["id"]


def _legacy_run(
    path: Path, document: dict[str, Any], *, presentation_only: bool = False, ack: bool = True,
) -> Path:
    document["run"]["until_ns"] = 0
    scenario = load_scenario(document)
    storage = RunStorage(path)
    storage.prepare(scenario)
    if presentation_only:
        # Model the earlier HTTP host: it recorded presentation DATA, but never
        # viewer/KPI implementation or metric reducer pins.
        display = presentation_api.presentation_snapshot(
            presentation_api.discover_presentation_plugins(), controls=(),
            registry=scenario.registry, document=scenario.document,
        )
        storage._atomic("presentation.snapshot.json", display)
        storage._atomic("manifest.json", {
            **storage.metadata(), "presentation_snapshot_digest": display["digest"],
        })
    with RunSession(scenario, path, prepared=True) as session:
        session.run()
    if ack:
        storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
    assert not (path / projections.PROJECTION_PINS_FILENAME).exists()
    return path


def _inventory(directory: Path) -> dict[str, tuple[Any, ...]]:
    # Reads may update atimes. Entries, identities, bytes, sizes and mtimes must
    # remain unchanged, including the directory itself and any new SQLite files.
    result = {}
    for path in [directory, *sorted(directory.rglob("*"))]:
        info = path.stat()
        result[str(path.relative_to(directory))] = (
            info.st_mode, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return result


def _without_provenance(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != "projection_provenance"}


def test_http_launch_records_original_definition_pins(launched: tuple[TestClient, Path]) -> None:
    client, path = launched
    provenance = projection_provenance(path)
    assert isinstance(provenance, ProjectionProvenance)
    assert RUN_READER_API_VERSION == 1
    assert provenance.format == "aeroagentsim.projection-provenance/v1"
    assert provenance.mode is ProjectionMode.ORIGINAL
    assert provenance.pinned_definitions == provenance.current_definitions
    pins = json.loads((path / projections.PROJECTION_PINS_FILENAME).read_text())
    assert pins == {
        "format": "aeroagentsim.projection-pins/v1",
        "definitions": provenance.pinned_definitions,
    }
    current = provenance.current_definitions
    assert current["viewer_feed"] == "aeroagentsim.viewer-feed/v1"
    assert current["kpi"] == "aeroagentsim.kpi/v1"
    assert current["metric_reducers"][0]["reducers"][0]["version"] == "1"
    assert current["metric_reducers"][0]["reducers"][0]["descriptors"] == [asdict(_Reducer.descriptors[0])]
    assert current["presentation_snapshot_digest"] == RunStorage(path).metadata()["presentation_snapshot_digest"]
    with open_run(path) as run:
        snapshot = run.snapshot()
        assert provenance.source_cut == {
            "service_run_id": snapshot.header.service_run_id,
            "kernel_run_id": snapshot.header.kernel_run_id,
            "epoch": snapshot.header.epoch,
            "journal_identity": list(snapshot.header.journal_identity),
            "configuration_digest": snapshot.header.configuration_digest,
            "registry_digest": snapshot.header.registry_digest,
            "cut_index": snapshot.cut_index, "cut_bytes": snapshot.cut_bytes,
        }
    result = client.get(f"/v1/runs/{path.name}/kpis")
    assert result.status_code == 200, result.text
    assert result.json()["projection_provenance"] == json.loads(json.dumps(asdict(provenance)))
    with pytest.raises(FrozenInstanceError):
        provenance.mode = ProjectionMode.LEGACY_UNPINNED  # type: ignore[misc]


def test_metric_revision_rebuild_exposes_recorded_and_current_identities(
    launched: tuple[TestClient, Path], definitions: _Reducer,
) -> None:
    client, path = launched
    url = f"/v1/runs/{path.name}/kpis"
    before = client.get(url).json()
    pin_bytes = (path / projections.PROJECTION_PINS_FILENAME).read_bytes()
    definitions.version = "2"
    provenance = projection_provenance(path)
    assert provenance.mode is ProjectionMode.RECOMPUTED_CURRENT_CODE
    assert provenance.pinned_definitions is not None
    assert provenance.pinned_definitions["metric_reducers"][0]["reducers"][0]["version"] == "1"
    assert provenance.current_definitions["metric_reducers"][0]["reducers"][0]["version"] == "2"
    after = client.get(url)
    assert after.status_code == 200, after.text
    returned = after.json()["projection_provenance"]
    assert returned["mode"] == "recomputed-current-code"
    assert returned["pinned_definitions"]["metric_reducers"][0]["reducers"][0]["version"] == "1"
    assert returned["current_definitions"]["metric_reducers"][0]["reducers"][0]["version"] == "2"
    # A revision-only change need not change a number. It still cannot be labelled
    # original; neither nulls nor any other v1 fields are rewritten by provenance.
    assert _without_provenance(after.json()) == _without_provenance(before)
    cuts = feed_index.page(path, 1, 4096)
    assert cuts and all(cut["projection_provenance"]["mode"] == "recomputed-current-code" for cut in cuts)
    assert (path / projections.PROJECTION_PINS_FILENAME).read_bytes() == pin_bytes


@pytest.mark.parametrize("change", ["revision", "data"])
def test_presentation_drift_is_diagnosed_without_bypassing_snapshot_rejection(
    launched: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    client, path = launched
    client.get(f"/v1/runs/{path.name}/kpis").raise_for_status()
    original = presentation_api.discover_presentation_plugins()[0]
    changed = replace(original, revision="2") if change == "revision" else replace(original, data={
        "format": presentation_api.PRESENTATION_FORMAT,
        "labels": {"Pinned label": {"en": "New meaning", "zh": "新含义"}},
    })
    monkeypatch.setattr(presentation_api, "BUILTINS", {"projection-test": lambda: changed})
    presentation_api.clear_presentation_plugin_cache()
    provenance = projection_provenance(path)
    assert provenance.mode is ProjectionMode.RECOMPUTED_CURRENT_CODE
    assert provenance.pinned_definitions is not None
    assert provenance.pinned_definitions["presentation_provider_digest"] != provenance.current_definitions["presentation_provider_digest"]
    # Stored DATA remains stored DATA: diagnosing drift does not replace it with
    # a newly fabricated snapshot or pretend that an HTTP recomputation succeeded.
    assert provenance.pinned_definitions["presentation_snapshot_digest"] == provenance.current_definitions["presentation_snapshot_digest"]
    with pytest.raises(ValueError, match="changed|unavailable"):
        client.get(f"/v1/runs/{path.name}/kpis")


@pytest.mark.parametrize("presentation_only", [False, True])
def test_old_runs_without_projection_pins_are_legacy(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer, presentation_only: bool,
) -> None:
    path = _legacy_run(tmp_path / "legacy", document, presentation_only=presentation_only)
    provenance = projection_provenance(path)
    assert provenance.mode is ProjectionMode.LEGACY_UNPINNED
    assert provenance.pinned_definitions is None
    assert provenance.source_cut is not None
    assert (provenance.current_definitions["presentation_snapshot_digest"] is not None) == presentation_only
    assert feed_index.kpi_result(path)["projection_provenance"]["mode"] == "legacy-unpinned"
    assert all(cut["projection_provenance"]["mode"] == "legacy-unpinned" for cut in feed_index.page(path))
    assert not (path / projections.PROJECTION_PINS_FILENAME).exists()


def test_index_only_legacy_has_no_invented_reader_cut(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer,
) -> None:
    path = _legacy_run(tmp_path / "index-only", document, ack=False)
    before = _inventory(path)
    provenance = projection_provenance(path)
    assert provenance.mode is ProjectionMode.LEGACY_UNPINNED
    assert provenance.source_cut is None
    assert _inventory(path) == before
    assert "projection_provenance" not in feed_index.kpi_result(path)


@pytest.mark.parametrize("pinned", [False, True])
def test_read_only_provenance_creates_no_files_or_indexes(
    launched: tuple[TestClient, Path], tmp_path: Path, document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, pinned: bool,
) -> None:
    _, modern = launched
    path = modern if pinned else _legacy_run(tmp_path / "legacy", document)
    (path / "index.json").unlink()
    assert not (path / feed_index.FILENAME).exists()
    before = _inventory(path)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Provenance must not project, create SQLite, or repair indexes")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(RunStorage, "index", forbidden)
    for method in ("initial", "consume", "snapshot"):
        monkeypatch.setattr(_Reducer, method, forbidden)
    assert projection_provenance(path).mode is (
        ProjectionMode.ORIGINAL if pinned else ProjectionMode.LEGACY_UNPINNED
    )
    assert _inventory(path) == before


@pytest.mark.parametrize("change", ["implementation", "authored"])
def test_host_or_authored_definition_change_is_not_original(
    launched: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    _, path = launched
    if change == "implementation":
        monkeypatch.setattr(projections, "_implementation_digest", lambda: "sha256:" + "f" * 64)
    else:
        scenario_path = path / "scenario.json"
        scenario = json.loads(scenario_path.read_text())
        scenario["event_narratives"] = {}
        scenario_path.write_text(json.dumps(scenario))
    provenance = projection_provenance(path)
    assert provenance.mode is ProjectionMode.RECOMPUTED_CURRENT_CODE
    assert provenance.pinned_definitions != provenance.current_definitions


@pytest.mark.parametrize("damage", ["format", "incomplete", "ack"])
def test_malformed_evidence_never_implies_original(
    launched: tuple[TestClient, Path], damage: str,
) -> None:
    _, path = launched
    if damage == "ack":
        (path / "committed-prefix.json").write_text('{"bytes":0,"index":0}')
        with pytest.raises(NoCommittedPrefix):
            projection_provenance(path)
    else:
        pins_path = path / projections.PROJECTION_PINS_FILENAME
        pins = json.loads(pins_path.read_text())
        if damage == "format":
            pins["format"] = "aeroagentsim.projection-pins/v999"
        else:
            pins["definitions"].pop("metric_reducers")
        pins_path.write_text(json.dumps(pins))
        with pytest.raises(ValueError, match="Projection pins"):
            projection_provenance(path)


def test_pin_identity_does_not_conflate_boolean_with_integer_api_version(
    launched: tuple[TestClient, Path],
) -> None:
    _, path = launched
    pins_path = path / projections.PROJECTION_PINS_FILENAME
    pins = json.loads(pins_path.read_text())
    pins["definitions"]["metric_reducers"][0]["api_version"] = True
    pins_path.write_text(json.dumps(pins))
    provenance = projection_provenance(path)
    assert provenance.mode is ProjectionMode.RECOMPUTED_CURRENT_CODE
    assert provenance.pinned_definitions is not None
    assert provenance.pinned_definitions["metric_reducers"][0]["api_version"] is True
    assert type(provenance.current_definitions["metric_reducers"][0]["api_version"]) is int


def test_existing_viewer_and_kpi_payloads_change_only_by_optional_provenance(
    launched: tuple[TestClient, Path],
) -> None:
    client, path = launched
    storage = RunStorage(path)
    records = storage.records(0, 4096)
    registry = MemoryRegistry.from_data(json.loads((path / "runtime.registry.json").read_text()))
    document = json.loads((path / "scenario.json").read_text())
    subjects = SubjectProjection(registry, document)
    kpis = KPIProjector(registry, document.get("presentation", []))
    kpis.consume(records[0])
    narratives = NarrativeProjector.from_directory(path)
    expected = [
        {**project(record, subjects=subjects, kpis=kpis), "events": narratives.consume(record)}
        for record in records[1:]
    ]
    response = client.get(f"/v1/runs/{path.name}/commits", params={"limit": 4096})
    assert response.status_code == 200, response.text
    cuts = response.json()["commits"]
    assert cuts and [_without_provenance(cut) for cut in cuts] == expected
    for cut in cuts:
        provenance = cut["projection_provenance"]
        entry = storage.entries[cut["commitIndex"]]
        assert provenance["mode"] == "original"
        assert provenance["source_cut"]["cut_index"] == cut["commitIndex"]
        assert provenance["source_cut"]["cut_bytes"] == entry["offset"] + entry["length"]
    assert feed_index.stream_page(path, 1, 4096) == cuts
    # Paging must not relabel a historical cut with the latest reader ACK.
    assert feed_index.page(path, 1, 1) == cuts[:1]
    result = client.get(f"/v1/runs/{path.name}/kpis")
    assert result.status_code == 200, result.text
    cursor = storage.metadata()["final_cursor"]
    assert _without_provenance(result.json()) == {
        "contract": "aeroagentsim.kpi/v1", "run_id": path.name,
        "final": kpis.snapshot(),
        "series": [{"commitIndex": cut["commitIndex"], "at_ns": cut["at"]["ns"], "kpis": cut["kpis"]}
                   for cut in expected],
        "results_final": True, "results_cursor": cursor, "final_commit_index": cursor - 1,
    }
    assert result.json()["projection_provenance"] == cuts[-1]["projection_provenance"]
