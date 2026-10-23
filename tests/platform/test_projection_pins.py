"""Run-time pins distinguish matching definitions from current-code recomputation."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel import BindingManifest, Kernel
from aerokernel.errors import KernelError
from aerokernel.journal import Journal
from aerokernel.registry import MemoryRegistry
from aerokernel.values import canonical_json
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
    RunReadError,
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


@pytest.mark.parametrize("with_projection_pins", [False, True])
def test_read_only_provenance_creates_no_files_or_indexes(
    launched: tuple[TestClient, Path], tmp_path: Path, document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, with_projection_pins: bool,
) -> None:
    # Do not name this parameter `pinned`: document depends on that session
    # fixture, and a bool would replace its scenario before launched can use it.
    _, modern = launched
    path = modern if with_projection_pins else _legacy_run(tmp_path / "legacy", document)
    (path / "index.json").unlink()
    assert not (path / feed_index.FILENAME).exists()
    before = _inventory(path)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Provenance must not project, create SQLite, or repair indexes")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(RunStorage, "index", forbidden)
    monkeypatch.setattr(worker, "_publish_projection_pins", forbidden)
    for method in ("initial", "consume", "snapshot"):
        monkeypatch.setattr(_Reducer, method, forbidden)
    for _ in range(2):
        assert projection_provenance(path).mode is (
            ProjectionMode.ORIGINAL if with_projection_pins else ProjectionMode.LEGACY_UNPINNED
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
    with pytest.raises(ValueError, match="Projection pins.*API version"):
        projection_provenance(path)


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


@pytest.mark.parametrize(("location", "invalid"), [
    pytest.param(("implementation_digest",), None, id="unknown-implementation"),
    pytest.param(("implementation_digest",), "sha256:not-a-digest", id="malformed-digest"),
    pytest.param(("presentation_provider_digest",), None, id="unknown-provider"),
    pytest.param(("presentation_snapshot_digest",), False, id="snapshot-type"),
    pytest.param(("authored_digests",), {}, id="missing-authored-keys"),
    pytest.param(("authored_digests", "scenario.json"), 1, id="authored-digest-type"),
    pytest.param(("metric_reducers",), None, id="unknown-metrics"),
    pytest.param(("metric_reducers", 0), {}, id="missing-plugin-identity"),
    pytest.param(("metric_reducers", 0, "distribution"), [None, None], id="empty-distribution"),
    pytest.param(("metric_reducers", 0, "entry_point"), [], id="entry-point-type"),
    pytest.param(("metric_reducers", 0, "reducers"), None, id="unknown-reducers"),
    pytest.param(("metric_reducers", 0, "reducers", 0), {}, id="missing-reducer-identity"),
    pytest.param(("metric_reducers", 0, "reducers", 0, "implementation"), None, id="unknown-reducer"),
    pytest.param(("metric_reducers", 0, "reducers", 0, "version"), None, id="unknown-revision"),
    pytest.param(("metric_reducers", 0, "reducers", 0, "input_schemas"), [], id="missing-inputs"),
    pytest.param(("metric_reducers", 0, "reducers", 0, "descriptors"), None, id="unknown-descriptors"),
    pytest.param(("metric_reducers", 0, "reducers", 0, "descriptors", 0), {}, id="incomplete-descriptor"),
    pytest.param(("metric_reducers", 0, "reducers", 0, "descriptors", 0, "direction"), "up", id="direction"),
])
def test_nested_malformed_pins_are_rejected_not_reported_as_drift(
    launched: tuple[TestClient, Path], location: tuple[str | int, ...], invalid: Any,
) -> None:
    _, path = launched
    # Exercise invalidation too: malformed replacement pins cannot reuse the
    # previously cached "original" metadata, or become known historical drift.
    assert feed_index.kpi_result(path)["projection_provenance"]["mode"] == "original"
    pins_path = path / projections.PROJECTION_PINS_FILENAME
    pins = json.loads(pins_path.read_text())
    target = pins["definitions"]
    for key in location[:-1]:
        target = target[key]
    target[location[-1]] = invalid
    pins_path.write_text(json.dumps(pins))
    with pytest.raises(ValueError, match="Projection pins"):
        projection_provenance(path)
    with pytest.raises(ValueError, match="Projection pins"):
        feed_index.kpi_result(path)


@pytest.mark.parametrize("source_name", ["journal.jsonl", "manifest.json", "committed-prefix.json"])
def test_reader_unsupported_symlinks_preserve_service_payloads(
    launched: tuple[TestClient, Path], source_name: str,
) -> None:
    client, path = launched
    expected_cuts = [_without_provenance(cut) for cut in feed_index.page(path, 1, 4096)]
    expected_kpis = _without_provenance(feed_index.kpi_result(path))
    source = path / source_name
    retained = path / (source_name + ".retained")
    source.replace(retained)
    source.symlink_to(retained.name)
    with pytest.raises(RunReadError):
        with open_run(path):
            pass
    with pytest.raises(RunReadError):
        projection_provenance(path)
    response = client.get(f"/v1/runs/{path.name}/commits", params={"limit": 4096})
    assert response.status_code == 200, response.text
    assert response.json()["commits"] == expected_cuts
    response = client.get(f"/v1/runs/{path.name}/kpis")
    assert response.status_code == 200, response.text
    assert response.json() == {**expected_kpis, "run_id": path.name}
    assert feed_index.stream_page(path, 1, 4096) == expected_cuts
    # The omission itself survives reconnects, without retrying reader admission.
    with feed_index._LOCK:
        feed_index._PROJECTIONS.pop(path, None)
    assert feed_index.stream_page(path, 1, 4096) == expected_cuts


def test_reader_only_strict_json_admission_does_not_fail_the_feed(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer,
) -> None:
    path = _legacy_run(tmp_path / "service-readable", document, ack=False)
    expected_cuts = feed_index.page(path, 1, 4096)
    expected_kpis = feed_index.kpi_result(path)
    # The existing service parser accepts foreign manifest metadata containing a
    # non-finite value; the raw reader's stricter parser correctly rejects it.
    storage = RunStorage(path)
    manifest = storage.metadata()
    manifest["foreign_metadata"] = float("nan")
    (path / "manifest.json").write_text(json.dumps(manifest))
    last = storage.entries[-1]
    storage.publish_prefix(last["offset"] + last["length"], last["index"])
    with pytest.raises((RunReadError, KernelError)):
        with open_run(path):
            pass
    assert feed_index.page(path, 1, 4096) == expected_cuts
    assert feed_index.kpi_result(path) == expected_kpis
    assert feed_index.stream_page(path, 1, 4096) == expected_cuts


def test_warm_live_pages_reuse_offsets_header_and_definition_content(
    tmp_path: Path, definitions: _Reducer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.runs import reader

    path = tmp_path / "live"
    path.mkdir()
    registry = MemoryRegistry(())
    (path / "scenario.json").write_bytes(canonical_json({"id": "live", "registry": {"messages": []}}))
    (path / "runtime.registry.json").write_bytes(canonical_json(registry.to_data()))
    storage = RunStorage(path)
    storage._atomic("manifest.json", {"id": path.name, "status": "waiting_for_input"})
    kernel = Kernel(journal=Journal(path / "journal.jsonl"))
    kernel.bind(registry, BindingManifest("projection-live", "0"), ())
    try:
        kernel.start()
        storage.index()
        initial_index = (path / "index.json").read_bytes()
        worker._publish_projection_pins(path, projections.projection_definitions(path))
        for ns in range(1, 13):
            kernel.run_until(ns)
        storage.publish_prefix(*kernel.acknowledged_prefix)

        constructions: list[Path] = []
        scans: list[int] = []
        admissions: list[None] = []
        definition_reads: list[Path] = []
        original_init = RunStorage.__init__
        original_index = RunStorage.index
        original_admit = reader._admit_header
        original_definitions = feed_index.projection_definitions

        def construct(self: RunStorage, directory: Path) -> None:
            constructions.append(directory)
            original_init(self, directory)

        def scan(self: RunStorage, **kwargs: Any) -> None:
            scans.append(self.offset)
            original_index(self, **kwargs)

        def admit(header: dict[str, Any]) -> str:
            admissions.append(None)
            return original_admit(header)

        def identify(directory: Path, **kwargs: Any) -> dict[str, Any]:
            definition_reads.append(directory)
            return original_definitions(directory, **kwargs)

        monkeypatch.setattr(RunStorage, "__init__", construct)
        monkeypatch.setattr(RunStorage, "index", scan)
        monkeypatch.setattr(reader, "_admit_header", admit)
        monkeypatch.setattr(feed_index, "projection_definitions", identify)
        cuts = feed_index.page(path, 1, 4096)
        assert len(cuts) >= 9
        assert all(cut["projection_provenance"]["mode"] == "original" for cut in cuts)
        assert len(constructions) == len(scans) == len(admissions) == len(definition_reads) == 1
        for start in (1, 4, 7):
            expected = cuts[start - 1:start + 2]
            assert feed_index.page(path, start, 3) == expected
            assert feed_index.stream_page(path, start, 3) == expected
        assert len(constructions) == len(scans) == len(admissions) == len(definition_reads) == 1
        assert feed_index.kpi_result(path)["projection_provenance"] == cuts[-1]["projection_provenance"]
        assert len(scans) == len(admissions) == len(definition_reads) == 1

        old_bytes, old_index = kernel.acknowledged_prefix
        for ns in range(13, 25):
            kernel.run_until(ns)
        storage.publish_prefix(*kernel.acknowledged_prefix)
        grown = feed_index.page(path, old_index + 1, 4096)
        assert grown and grown[0]["commitIndex"] == old_index + 1
        # Only the new tail is indexed, from the retained byte cursor. Neither
        # ACK growth nor paging that tail reconstructs old offsets or the header.
        assert scans[1:] == [old_bytes]
        assert len(admissions) == len(definition_reads) == 1
        assert (path / "index.json").read_bytes() == initial_index
        for cut in grown:
            provenance = cut["projection_provenance"]
            assert provenance["source_cut"]["cut_index"] == cut["commitIndex"]
            assert old_bytes < provenance["source_cut"]["cut_bytes"] <= kernel.acknowledged_prefix[0]
        # SQLite carries the metadata even when the in-memory working set is
        # lost; the ready SSE path needs no storage or header admission at all.
        with feed_index._LOCK:
            feed_index._PROJECTIONS.pop(path, None)

        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("A cached SSE page reconstructed source metadata")

        with monkeypatch.context() as reconnect:
            reconnect.setattr(RunStorage, "__init__", forbidden)
            reconnect.setattr(reader, "_admit_header", forbidden)
            assert feed_index.stream_page(path, 1, 3) == cuts[:3]
    finally:
        kernel.close()


@pytest.mark.parametrize("change", ["replacement", "ack", "truncation"])
def test_cached_provenance_checks_source_identity_and_ack_bounds(
    launched: tuple[TestClient, Path], change: str,
) -> None:
    _, path = launched
    cuts = feed_index.page(path, 1, 4096)
    journal = path / "journal.jsonl"
    if change == "replacement":
        old_identity = cuts[0]["projection_provenance"]["source_cut"]["journal_identity"]
        replacement = path / "replacement.jsonl"
        replacement.write_bytes(journal.read_bytes())
        replacement.replace(journal)
        after = feed_index.stream_page(path, 1, 4096)
        identity = [journal.stat().st_dev, journal.stat().st_ino]
        assert identity != old_identity
        assert after and all(cut["projection_provenance"]["source_cut"]["journal_identity"] == identity
                             for cut in after)
    elif change == "ack":
        first = cuts[0]["projection_provenance"]["source_cut"]
        RunStorage(path).publish_prefix(first["cut_bytes"], first["cut_index"])
        assert feed_index.stream_page(path, 1, 4096) == cuts[:1]
        assert feed_index.page(path, 1, 4096) == cuts[:1]
    else:
        with journal.open("r+b") as stream:
            stream.truncate(journal.stat().st_size - 1)
        with pytest.raises(ValueError, match="shorter|beyond"):
            feed_index.stream_page(path, 1, 4096)


@pytest.mark.parametrize("durability", ["flush", "fsync"])
def test_pins_are_file_and_directory_synced_before_first_world_ack(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer,
    monkeypatch: pytest.MonkeyPatch, durability: str,
) -> None:
    document["run"]["until_ns"] = 0
    document["outputs"]["durability"] = durability
    scenario = load_scenario(document)
    path = tmp_path / "durable"
    RunStorage(path).prepare(scenario)
    events: list[str] = []
    original_fsync, original_link, original_append = os.fsync, os.link, Journal.append

    def fsync(descriptor: int) -> None:
        original_fsync(descriptor)
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            assert (path / projections.PROJECTION_PINS_FILENAME).is_file()
            events.append("directory-fsync")
        elif info.st_ino != (path / "journal.jsonl").stat().st_ino:
            events.append("file-fsync")

    def install(source: Any, target: Any) -> None:
        assert events == ["file-fsync"]
        assert json.loads(Path(source).read_text())["format"] == projections.PROJECTION_PINS_FORMAT
        assert Path(target) == path / projections.PROJECTION_PINS_FILENAME
        original_link(source, target)
        events.append("install")

    def append(self: Journal, record: dict[str, Any], **kwargs: Any) -> bytes:
        if record["type"] != "header":
            assert events[:3] == ["file-fsync", "install", "directory-fsync"]
            if "world" not in events:
                events.append("world")
        return original_append(self, record, **kwargs)

    monkeypatch.setattr(worker.os, "fsync", fsync)
    monkeypatch.setattr(worker.os, "link", install)
    monkeypatch.setattr(Journal, "append", append)
    worker.execute(scenario, path, threading.Event(), threading.Event())
    assert RunStorage(path).metadata()["status"] == "completed"
    assert events == ["file-fsync", "install", "directory-fsync", "world"]
    assert not list(path.glob(".projection-pins-*"))


@pytest.mark.parametrize("failure", ["file", "install", "directory"])
def test_pin_publication_failure_prevents_world_ack(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    document["run"]["until_ns"] = 0
    document["outputs"]["durability"] = "fsync"
    scenario = load_scenario(document)
    path = tmp_path / "failed-pins"
    RunStorage(path).prepare(scenario)
    started: list[None] = []
    records: list[str] = []
    original_fsync, original_link = os.fsync, os.link
    original_start, original_append = RunSession.start, Journal.append

    def fsync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        point = "directory" if stat.S_ISDIR(info.st_mode) else "file"
        is_journal = not stat.S_ISDIR(info.st_mode) and info.st_ino == (path / "journal.jsonl").stat().st_ino
        if not is_journal and failure == point:
            raise OSError("injected pin durability failure")
        original_fsync(descriptor)

    def install(source: Any, target: Any) -> None:
        if failure == "install":
            raise OSError("injected pin durability failure")
        original_link(source, target)

    def start(self: RunSession) -> Any:
        started.append(None)
        return original_start(self)

    def append(self: Journal, record: dict[str, Any], **kwargs: Any) -> bytes:
        records.append(record["type"])
        return original_append(self, record, **kwargs)

    monkeypatch.setattr(worker.os, "fsync", fsync)
    monkeypatch.setattr(worker.os, "link", install)
    monkeypatch.setattr(RunSession, "start", start)
    monkeypatch.setattr(Journal, "append", append)
    worker.execute(scenario, path, threading.Event(), threading.Event())
    metadata = RunStorage(path).metadata()
    assert metadata["status"] == "faulted"
    assert "injected pin durability failure" in metadata["error"]
    assert not started and records == ["header"]
    assert not list(path.glob(".projection-pins-*"))


def test_durable_pin_publication_never_replaces_existing_evidence(
    launched: tuple[TestClient, Path],
) -> None:
    _, path = launched
    pin = path / projections.PROJECTION_PINS_FILENAME
    before, identity = pin.read_bytes(), pin.stat().st_ino
    with pytest.raises(FileExistsError):
        worker._publish_projection_pins(path, projections.projection_definitions(path))
    assert pin.read_bytes() == before and pin.stat().st_ino == identity
    assert not list(path.glob(".projection-pins-*"))


def test_summary_kpi_bytes_ignore_run_local_provenance_and_pin_presence(
    tmp_path: Path, document: dict[str, Any], definitions: _Reducer,
) -> None:
    document["run"]["until_ns"] = 0
    scenario = load_scenario(document)
    paths = []
    for name, factor in (("max", 0), ("paced", 5)):
        path = tmp_path / name
        RunStorage(path).prepare(scenario)
        worker.execute(scenario, path, threading.Event(), threading.Event(), speed=SimpleNamespace(value=factor))
        assert RunStorage(path).metadata()["status"] == "completed"
        paths.append(path)
    headless = tmp_path / "headless"
    with RunSession(scenario, headless) as session:
        session.run()
    paths.append(headless)
    assert len({(path / "journal.jsonl").read_bytes() for path in paths}) == 1
    summaries = [feed_index.kpi_result(path, include_series=False) for path in paths]
    assert all("projection_provenance" not in result for result in summaries)
    assert len({canonical_json(result) for result in summaries}) == 1
    # Full responses still expose run-local identities and honest pin modes.
    assert all(feed_index.kpi_result(path)["projection_provenance"]["mode"] == "original"
               for path in paths[:2])
    assert "projection_provenance" not in feed_index.kpi_result(headless)
