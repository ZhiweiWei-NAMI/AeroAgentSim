"""Data-only exports at public reader cuts; no live model, camera or browser.

The genuine telemetry fixtures reuse the run-reader builders. Pack goldens use
kernel-recorded authored events with hand-derived origin/main metric values,
not a claim about live pack physics. The diagnostic message fixtures test only
decoding/formatting, not semantic replay or scientific measurements. Unsupported
viewer metadata/finality use small DTOs; no HTTP server or cache writer starts.
"""

from __future__ import annotations

import copy
import json
import socket
import sqlite3
import subprocess
from contextlib import nullcontext
from dataclasses import asdict, replace
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel import (
    BindingManifest, CommandRequest, Cut, Emit, EntityRef, Instant, Journal,
    Kernel, MemoryRegistry, Message, MessageDescriptor, Partition, Stamp,
    StateView, replay,
)
from aerokernel.sdk import SimpleEngine
from aerokernel.codec import decode_record, encode
from aerokernel.values import canonical_json, freeze

from aeroagentsim.agents.provider import OpenAIProvider
from aeroagentsim.observations.artifacts import ArtifactStore
from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.observations.renderer import BrowserRenderer
from aeroagentsim.packs.metrics import compute, metrics
from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.runs import RunReadError, open_run
from aeroagentsim.services.projector import project
from tests.observations.test_capture import png
from tests.runs.test_reader import (
    COMMAND,
    FORMATS,
    inventory,
    recorded_records,
    telemetry_document,
    write_prefix,
    write_run,
)
from tools.demos import export_traffic_report as report
from tools.demos import record_run_views as video
from tools.templates import thumbnails


@pytest.fixture(params=[
    pytest.param(("lean", "json"), id="lean-json"),
    pytest.param(("lean", "positional-deflate"), id="lean-deflate"),
    pytest.param(("full", "json"), id="full-json"),
    pytest.param(("full", "positional-deflate"), id="full-deflate"),
])
def recorded_run(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    provenance, codec = request.param
    document = telemetry_document()
    earlier = copy.deepcopy(document["bindings"]["commands"][0])
    earlier["at_ns"] = 50_000_000
    document["bindings"]["commands"].insert(0, earlier)
    return write_run(
        tmp_path / "recorded", provenance=provenance, codec=codec, document=document
    )


def forbid_live(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("recorded data export attempted live execution")

    monkeypatch.setattr(Simulation, "__init__", forbidden)
    monkeypatch.setattr(RunSession, "__init__", forbidden)
    monkeypatch.setattr(OpenAIProvider, "complete", forbidden)
    monkeypatch.setattr(BrowserRenderer, "__init__", forbidden)
    monkeypatch.setattr(BrowserRenderer, "render", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(video, "urlopen", forbidden)
    monkeypatch.setattr(thumbnails, "capture_png", forbidden)
    monkeypatch.setattr(thumbnails, "build_viewer", forbidden)
    # Reader v1 does not supply scene/model metadata. The data-path tests do not
    # load a city pack or ask its renderer to interpret this explicitly fake DTO.
    monkeypatch.setattr(thumbnails, "header", lambda path: {
        "cityDetail": "detailed", "modelCatalog": {"fixture": {}},
        "end": {"ns": "untrusted-index-tail", "microstep": 0},
    })


def final_state(directory: Path) -> dict[str, Any]:
    cursor = json.loads((directory / "manifest.json").read_text())["final_cursor"]
    return {
        "results_final": True,
        "results_cursor": cursor,
        "final_commit_index": cursor - 1,
    }


def test_data_exports_match_one_reader_cut_and_leave_no_cache(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {"run_id": "realtime-ingress", "duration_s": 0.1}
    with open_run(recorded_run) as source:
        snapshot = source.snapshot()
        raw = list(snapshot.iter_records(start=1))
        originals = list(snapshot.iter_messages())
        evidence = video.source_evidence(snapshot)
    assert len(originals) == 2
    assert originals[0].payload == originals[1].payload
    assert originals[0].message_id != originals[1].message_id
    assert any("delivery" in item for row in raw for item in row["items"])
    # Derived indexes are not the evidence boundary; even invalid JSON is ignored.
    (recorded_run / "index.json").write_text("not an index")
    forbid_live(monkeypatch)
    before = inventory(recorded_run)

    text = report.export_report(recorded_run, tmp_path / "report").read_text()
    assert text.startswith("# Traffic accident run report\n")
    assert text.count(f"| command `{COMMAND}`:") == 2
    assert f"| source_evidence | `{report.cell(evidence)}` |" in text
    assert "No simulation, model or camera is called during export." in text
    assert "model call totals are unavailable" in text
    selected = video.cuts(recorded_run)
    assert selected == [
        Cut(row["index"], decode_record(row["instant"])) for row in raw
    ]
    assert video.schedule(selected, 4, Fraction(1))[-1] == selected[-1]
    assert thumbnails.source_cut(recorded_run, 1.0) == selected[-1]
    feed = thumbnails.viewer_feed(recorded_run, selected[-1])
    assert [row["commitIndex"] for row in feed["commits"]] == [row["index"] for row in raw]
    assert [m["id"] for row in feed["commits"] for m in row["messages"]] == [
        message.message_id for message in originals
    ]
    # The service's public HTTP feed uses these same non-message projection rows.
    for row, commit in zip(raw, feed["commits"], strict=True):
        projected = project(row)
        for key in ("at", "created", "removed", "facts", "retracted", "receipts"):
            assert commit[key] == projected[key]
    assert feed["header"]["end"] == feed["commits"][-1]["at"]
    assert feed["source_evidence"] == {
        **evidence, "exported_through_index": selected[-1].index,
    }
    result = metrics(recorded_run)
    assert {key: value for key, value in result.items() if key != "source_evidence"} == expected
    assert result["source_evidence"] == {
        **evidence, "verification": "kernel-semantic-replay",
    }
    assert evidence["header"]["created_at"] is None
    assert inventory(recorded_run) == before


def test_visible_complete_and_partial_tails_are_not_exported(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with open_run(recorded_run) as source:
        snapshot = source.snapshot()
        evidence = video.source_evidence(snapshot)
    expected = metrics(recorded_run)
    with (recorded_run / "journal.jsonl").open("ab") as stream:
        stream.write(canonical_json({
            "index": evidence["cut_index"] + 1,
            "type": "unacknowledged-fixture",
            "items": [],
        }))
        stream.write(b'{"index":')
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    assert metrics(recorded_run) == expected
    selected = video.cuts(recorded_run)
    assert selected[-1].index == evidence["cut_index"]
    feed = thumbnails.viewer_feed(recorded_run, selected[-1])
    assert feed["source_evidence"]["cut_bytes"] == evidence["cut_bytes"]
    text = report.export_report(recorded_run, tmp_path / "report").read_text()
    assert f"| source_evidence | `{report.cell(evidence)}` |" in text
    assert "unacknowledged-fixture" not in text
    assert inventory(recorded_run) == before


@pytest.mark.parametrize("damage", ["missing-ack", "truncated", "gap"])
def test_missing_evidence_raises_before_any_partial_export(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    selected = video.cuts(recorded_run)[-1]
    journal = recorded_run / "journal.jsonl"
    if damage == "missing-ack":
        (recorded_run / "committed-prefix.json").unlink()
    elif damage == "truncated":
        journal.write_bytes(journal.read_bytes()[:-1])
    else:
        lines = journal.read_bytes().splitlines(keepends=True)
        data = b"".join(lines[:2] + lines[3:])
        journal.write_bytes(data)
        write_prefix(recorded_run, {"bytes": len(data), "index": len(lines) - 1})
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    consumers = [
        lambda: report.export_report(recorded_run, tmp_path / "report"),
        lambda: video.cuts(recorded_run),
        lambda: thumbnails.source_cut(recorded_run, 0.0),
        lambda: thumbnails.viewer_feed(recorded_run, selected),
    ]
    if damage != "missing-ack":
        # Pack metrics also admit bare offline journals; other exports need ACKs.
        consumers.append(lambda: metrics(recorded_run))
    for consume in consumers:
        with pytest.raises(RunReadError):
            consume()
    assert not (tmp_path / "report").exists()
    assert inventory(recorded_run) == before


def test_final_manifest_cursor_cannot_truncate_report_or_cuts(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = recorded_run / "manifest.json"
    metadata = json.loads(manifest.read_text())
    metadata["final_cursor"] += 1
    manifest.write_text(json.dumps(metadata))
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    with pytest.raises(ValueError, match="final cursor"):
        report.export_report(recorded_run, tmp_path / "report")
    with pytest.raises(ValueError, match="final cursor"):
        video.cuts(recorded_run)
    assert not (tmp_path / "report").exists()
    assert inventory(recorded_run) == before


def test_final_capture_requires_finality_and_all_matching_cursors(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = final_state(recorded_run)
    states = iter([
        {**final, "results_final": False},
        {**final, "results_cursor": final["results_cursor"] - 1},
        {**final, "final_commit_index": final["final_commit_index"] - 1},
        final,
    ])
    waits = []
    monkeypatch.setattr(video, "Event", lambda: SimpleNamespace(wait=waits.append))
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    selected, metadata, evidence = video.recording_source(
        recorded_run, results_provider=lambda: next(states),
    )
    assert len(waits) == 3
    assert selected[-1].index + 1 == metadata["final_cursor"] == final["results_cursor"]
    assert evidence["cut_index"] == final["final_commit_index"]
    assert evidence["projection"] == final
    assert inventory(recorded_run) == before


def test_final_capture_waits_for_lagging_ack_not_just_a_final_projection(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = json.loads((recorded_run / "committed-prefix.json").read_text())
    lines = (recorded_run / "journal.jsonl").read_bytes().splitlines(keepends=True)
    lagging = {"bytes": sum(map(len, lines[:-1])), "index": prefix["index"] - 1}
    write_prefix(recorded_run, lagging)
    state = final_state(recorded_run)
    waits = []

    def publish_from_owner(delay: float) -> None:
        waits.append(delay)
        write_prefix(recorded_run, prefix)

    monkeypatch.setattr(video, "Event", lambda: SimpleNamespace(wait=publish_from_owner))
    forbid_live(monkeypatch)
    selected, _, evidence = video.recording_source(
        recorded_run, results_provider=lambda: state,
    )
    assert len(waits) == 1
    assert selected[-1].index == evidence["cut_index"] == prefix["index"]
    assert evidence["cut_bytes"] == prefix["bytes"]


def test_absent_projection_finality_times_out_without_render_or_cache_creation(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    # Public finality may lag a terminal manifest/ACK. Never fall back to a
    # private cache read or assume the service has finished after a delay.
    with pytest.raises(TimeoutError, match="results_final"):
        video.record_views(
            recorded_run, tmp_path / "video",
            [{"id": "fixture", "actor": {}, "camera": {}, "asset_digest": "fixture",
              "width": 16, "height": 16}],
            renderer=SimpleNamespace(render=lambda *args: pytest.fail("rendered")),
            fps=4, speed=Fraction(1), ffmpeg=None, results_timeout_s=0,
            results_provider=lambda: {
                "results_final": False, "results_cursor": None,
                "final_commit_index": final_state(recorded_run)["final_commit_index"],
            },
        )
    assert not (tmp_path / "video").exists()
    assert inventory(recorded_run) == before


def test_finality_uses_public_dto_even_with_unreadable_local_cache(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = final_state(recorded_run)
    # Deliberately opaque bytes: neither the consumer nor its reader may inspect
    # SQLite, regardless of whether a viewer happened to leave a local cache.
    (recorded_run / "viewer-index.sqlite3").write_bytes(b"not a SQLite database")
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    _, _, evidence = video.recording_source(
        recorded_run, timeout_s=0, results_provider=lambda: state,
    )
    assert evidence["projection"] == state
    assert inventory(recorded_run) == before


def test_offline_finality_gap_is_explicit_without_private_cache_fallback(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    with pytest.raises(ValueError, match="reader v1 lacks offline projection finality"):
        video.recording_source(recorded_run, timeout_s=0)
    assert inventory(recorded_run) == before


def test_public_finality_adapter_reads_only_service_dto(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = final_state(recorded_run)
    forbid_live(monkeypatch)
    requests = []

    def response(url: str, *, timeout: float) -> BytesIO:
        requests.append((url, timeout))
        return BytesIO(json.dumps({
            "id": "fixture/run", **state, "unrelated": "not provenance",
        }).encode())

    monkeypatch.setattr(video, "urlopen", response)
    before = inventory(recorded_run)
    assert video.public_results("http://service.invalid/", "fixture/run") == state
    assert requests == [(
        "http://service.invalid/v1/runs/fixture%2Frun?include_kpis=false", 20.0,
    )]
    assert inventory(recorded_run) == before
    monkeypatch.setattr(video, "urlopen", lambda *args, **kwargs: BytesIO(b'{"id":"other"}'))
    with pytest.raises(ValueError, match="another service run"):
        video.public_results("http://service.invalid", "fixture/run")


def test_recording_manifest_keeps_existing_keys_and_adds_reader_provenance(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = final_state(recorded_run)
    metadata = json.loads((recorded_run / "manifest.json").read_text())
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    manifest = video.record_views(
        recorded_run, tmp_path / "video",
        [{"id": "fixture", "actor": {}, "camera": {}, "asset_digest": "fixture",
          "width": 16, "height": 16}],
        renderer=SimpleNamespace(render=lambda *args: pytest.fail("rendered")),
        fps=4, speed=Fraction(2), ffmpeg=None, results_timeout_s=0,
        results_provider=lambda: state,
    )
    assert manifest["contract"] == "aeroagentsim.replay-recordings/v1"
    assert manifest["source_run"] == metadata
    assert manifest["fps"] == 4 and manifest["speed"] == "2"
    assert manifest["terminal_frame_hold_s"] == "1/4"
    assert manifest["source_evidence"]["projection"] == state
    assert manifest["views"][0]["status"] == "failed"
    assert "ffmpeg unavailable" in manifest["views"][0]["reason"]
    assert manifest["views"][0]["source_cuts"][-1]["index"] == state["final_commit_index"]
    saved = json.loads((tmp_path / "video/manifest.json").read_text())
    assert saved["source_run"] == metadata
    assert saved["source_evidence"] == json.loads(json.dumps(manifest["source_evidence"]))
    assert saved["source_evidence"]["header"]["journal_identity"] == list(
        manifest["source_evidence"]["header"]["journal_identity"]
    )
    assert saved["source_evidence"]["header"]["engine_versions"]
    assert inventory(recorded_run) == before


@pytest.mark.parametrize("service_url", [None, "https://api.invalid:9443/base"])
def test_recording_cli_defaults_service_to_viewer_origin_and_allows_override(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    service_url: str | None,
) -> None:
    state = final_state(recorded_run)
    metadata = json.loads((recorded_run / "manifest.json").read_text())
    cameras = tmp_path / "cameras.json"
    cameras.write_text("[]")
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    requests = []
    viewer_url = "https://viewer.invalid:8443/viewer/index.html?mode=replay#camera"

    def renderer(url: str, **kwargs: Any) -> Any:
        assert url == viewer_url
        assert kwargs["service_run_id"] == metadata["id"]
        return nullcontext(SimpleNamespace())

    def results(url: str, run_id: str) -> dict[str, Any]:
        requests.append((url, run_id))
        return state

    def record(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["results_provider"]() == state
        return {"views": []}

    monkeypatch.setattr(video, "BrowserRenderer", renderer)
    monkeypatch.setattr(video, "public_results", results)
    monkeypatch.setattr(video, "record_views", record)
    argv = [
        "--run", str(recorded_run), "--output", str(tmp_path / "video"),
        "--cameras", str(cameras), "--viewer-url", viewer_url,
        "--node-modules", str(tmp_path / "node_modules"), "--ffmpeg", "fixture-ffmpeg",
    ]
    if service_url is not None:
        argv += ["--service-url", service_url]
    assert video.main(argv) == 0
    assert requests == [(service_url or "https://viewer.invalid:8443", metadata["id"])]
    assert inventory(recorded_run) == before


class RecordedEvents(SimpleEngine):
    """Fixture writer only: publish authored envelopes, not pack/model outputs."""

    def __init__(self, source: str, rows: list[tuple[int, str, dict[str, Any]]]) -> None:
        schemas = tuple(sorted({schema for _, schema, _ in rows}))
        super().__init__(Partition(
            source, source, emits=schemas, message_targets=("fixture.events",),
        ))
        self.rows = copy.deepcopy(rows)
        self.wakeup_ns = rows[0][0]

    def integrate(self, view: StateView) -> tuple[object, ...]:
        operations = []
        while self.rows and self.rows[0][0] == view.instant.ns:
            _, schema, payload = self.rows.pop(0)
            operations.append(Emit("event", schema, "fixture.events", view.instant, payload))
        self.wakeup_ns = self.rows[0][0] if self.rows else None
        return tuple(operations)


def write_event_run(
    directory: Path, configuration: dict[str, Any],
    schedules: dict[str, list[tuple[int, str, dict[str, Any]]]],
    *, provenance: str, codec: str,
) -> Path:
    """Genuine, replay-valid WAL/ACK with two actual same-ID fan-out deliveries."""
    directory.mkdir()
    schemas = sorted({schema for rows in schedules.values() for _, schema, _ in rows})
    registry = MemoryRegistry((), messages=tuple(
        MessageDescriptor(schema, schema={
            "type": "record", "members": {}, "required": [], "extra": True,
        })
        for schema in schemas
    ))
    engines = tuple(RecordedEvents(source, rows) for source, rows in schedules.items()) + tuple(
        SimpleEngine(Partition(name, name, subscribes=("fixture.events",)))
        for name in ("observer-a", "observer-b")
    )
    kernel = Kernel(
        configuration=configuration, provenance=provenance,
        journal=Journal(directory / "journal.jsonl", codec=codec),
    )
    try:
        kernel.bind(registry, BindingManifest("pack-golden", "fixture-epoch"), engines)
        kernel.start()
        kernel.run_until(20_000_000_000)
    finally:
        kernel.close()
    byte_count, index = kernel.acknowledged_prefix
    write_prefix(directory, {"bytes": byte_count, "index": index})
    (directory / "manifest.json").write_text(json.dumps({
        "id": directory.name, "status": "completed", "final_cursor": index + 1,
    }))
    return directory


# Read from origin/main (2e3f485c), packs/metrics.py::compute:
# ID-keyed viewer events keep first-ID insertion order (fan-out replaces the
# value, not its position); released/accepted/rejected are order sets. Delivery
# times are delivery minus release; only delivered parcels contribute energy.
# Busy intervals here are A:2..6, B:3..10, C:4..8 (failed), D:5..9 (canceled),
# E:12..20 (still active): 27s / (20s * 3 carriers) = .45. One on-time delivery
# out of six releases, durations 6s and 9s, energies 30J and 70J.
# Inspection counts six distinct message IDs, including two equal payloads and
# one invalid sample. Union area is 6 of 8; area and hit waypoint acquire at
# 1,4,10 seconds (gaps 3,6); the missed waypoint has no visits. Invalid/outside
# footprints add no coverage. These literals are NOT generated by compute().
LEGACY_PACK_GOLDEN = {
    "run_id": "legacy-pack-golden",
    "duration_s": 20.0,
    "logistics": {
        "pack": "logistics", "released": 6, "delivered": 2,
        "accepted": 1, "rejected": 1, "on_time_rate": 0.16666666666666666,
        "mean_delivery_time_s": 7.5, "energy_per_parcel_j": 50.0,
        "utilization": 0.45,
    },
    "inspection": {
        "pack": "inspection", "observations": 6, "invalid_observations": 1,
        "coverage_pct": 58.333333333333336,
        "targets": {
            "area": {
                "coverage_pct": 75.0, "acquisitions": 3,
                "revisit_times_s": [3.0, 6.0], "mean_revisit_s": 4.5,
                "max_revisit_s": 6.0,
            },
            "hit": {
                "coverage_pct": 100.0, "acquisitions": 3,
                "revisit_times_s": [3.0, 6.0], "mean_revisit_s": 4.5,
                "max_revisit_s": 6.0,
            },
            "miss": {
                "coverage_pct": 0.0, "acquisitions": 0,
                "revisit_times_s": [], "mean_revisit_s": None,
                "max_revisit_s": None,
            },
        },
    },
}


def legacy_pack_run(directory: Path, provenance: str, codec: str) -> Path:
    second = 1_000_000_000
    logistics = [
        (second, "fixture.logistics", {"order": order, "state": "queued"})
        for order in ("A", "B", "C", "D", "E", "F")
    ]
    logistics += [
        (2 * second, "fixture.logistics", {"order": "A", "state": "pickup"}),
        (3 * second, "fixture.logistics", {"order": "B", "state": "pickup"}),
        (4 * second, "fixture.logistics", {"order": "C", "state": "pickup"}),
        (5 * second, "fixture.logistics", {"order": "D", "state": "pickup"}),
        (6 * second, "fixture.logistics", {
            "order": "A", "state": "delivered", "release_ns": 0,
            "deadline_ns": 7 * second, "energy_used_j": 30.0,
        }),
        (7 * second, "fixture.logistics", {"order": "A", "state": "accepted"}),
        (8 * second, "fixture.logistics", {"order": "C", "state": "failed"}),
        (9 * second, "fixture.logistics", {"order": "D", "state": "canceled"}),
        (10 * second, "fixture.logistics", {
            "order": "B", "state": "delivered", "release_ns": second,
            "deadline_ns": 9 * second, "energy_used_j": 70.0,
        }),
        (11 * second, "fixture.logistics", {"order": "B", "state": "rejected"}),
        (12 * second, "fixture.logistics", {"order": "E", "state": "pickup"}),
    ]
    left = [[0, 0], [2, 0], [2, 2], [0, 2]]
    inspection = [
        (at * second, "fixture.inspection", {
            "acquired_ns": at * second,
            "sample": {"valid": valid, "footprint": footprint},
        })
        for at, valid, footprint in [
            (1, True, left),
            (1, True, left),  # Equal payload, distinct emitted identity.
            (4, True, [[1, 0], [3, 0], [3, 2], [1, 2]]),
            (6, False, [[0, 0], [4, 0], [4, 2], [0, 2]]),
            (10, True, left),
            (12, True, [[5, 0], [6, 0], [6, 1], [5, 1]]),
        ]
    ]
    return write_event_run(directory, {"scenario": {
        "id": "legacy-pack-golden", "engines": {
            "logistics": {"plugin": "logistics", "config": {
                "event_schema": "fixture.logistics", "fleet": ["one", "two", "three"],
            }},
            "inspection": {"plugin": "inspection", "config": {
                "event_schema": "fixture.inspection", "targets": [
                    {"id": "area", "kind": "rectangle", "bounds": [0, 0, 4, 2]},
                    {"id": "hit", "kind": "waypoint", "point": [1.5, 1]},
                    {"id": "miss", "kind": "waypoint", "point": [3.5, 1]},
                ],
            }},
        },
    }}, {"logistics": logistics, "inspection": inspection}, provenance=provenance, codec=codec)


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_pack_metrics_match_independent_legacy_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: str, codec: str,
) -> None:
    directory = legacy_pack_run(tmp_path / "packs", provenance, codec)
    raw = recorded_records(directory)
    emissions = [
        item["message"] for record in raw for item in record.get("items", [])
        if item.get("kind") == "operation" and "message" in item
    ]
    # Kernel fan-out is recorded as dispatch items wrapping Delivery envelopes,
    # not the synthetic kind="delivery" slots used by diagnostic fixtures below.
    deliveries = [
        decode_record(item["delivery"])
        for record in raw for item in record.get("items", [])
        if item.get("kind") == "dispatch"
    ]
    assert len(emissions) == 23
    assert len(deliveries) == 46
    for message in emissions:
        copies = [
            delivery for delivery in deliveries
            if delivery.message.id == message["fields"]["id"]
        ]
        assert len(copies) == 2
        assert {delivery.recipient for delivery in copies} == {"observer-a", "observer-b"}
    with open_run(directory) as run:
        observations = list(run.snapshot().iter_messages(schema_prefix="fixture.inspection"))
    assert len(observations) == 6
    assert observations[0].payload == observations[1].payload
    assert observations[0].message_id != observations[1].message_id
    forbid_live(monkeypatch)
    before = inventory(directory)
    assert compute(replay(directory / "journal.jsonl")) == LEGACY_PACK_GOLDEN
    result = metrics(directory)
    assert {key: value for key, value in result.items() if key != "source_evidence"} == LEGACY_PACK_GOLDEN
    assert result["source_evidence"]["verification"] == "kernel-semantic-replay"
    assert inventory(directory) == before
    # Ordinary immutable pack envelopes have the SAME first-ID population as
    # origin/main. Delivery-only diagnostics/console aliases are intentionally
    # excluded by the new reader, and tested separately, not called equivalence.


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_default_graph_decisions_present_original_model_call_without_alias_emissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: str, codec: str,
) -> None:
    data = {
        "identity": {"node": "planner", "call_index": 0},
        "request": {"messages": [{"role": "user", "content": "recorded prompt"}]},
        "response": {"content": "recorded decision"},
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    }
    directory = write_event_run(
        tmp_path / "graph", {"scenario": {"id": "graph-fixture", "engines": {}}},
        {"planner": [(1_000_000_000, "aas.langgraph.record", {
            "phase": "model_call", "data_json": json.dumps(data),
        })]}, provenance=provenance, codec=codec,
    )
    forbid_live(monkeypatch)
    before = inventory(directory)
    for use_cli in (False, True):
        output = tmp_path / ("cli-report" if use_cli else "report")
        if use_cli:
            assert report.main(["--run", str(directory), "--output", str(output)]) == 0
            text = (output / "report.md").read_text()
        else:
            text = report.export_report(directory, output).read_text()
        timeline, decisions = text.split("## Decisions and model records", 1)
        assert timeline.count("| event `aas.langgraph.record`:") == 1
        assert "aas.agent.record" not in text and "/console/" not in text
        assert '"model_call": 1' in decisions
        assert "one per original message" in decisions
        assert "### Cut " in decisions and ": aas.langgraph.record" in decisions
        assert "Recorded phase data:" in decisions
        assert json.dumps(data, indent=2, ensure_ascii=False) in decisions
        assert "model call totals are unavailable" not in decisions
    assert inventory(directory) == before


def diagnostic_run(directory: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Admitted diagnostic fixture; deliberately NOT a semantically replayed run."""
    write_run(directory, provenance="full", codec="json")
    header = recorded_records(directory)[0]
    header["configuration"] = {"scenario": {
        "id": "diagnostic-inspection", "engines": {
            "inspection": {"plugin": "inspection", "config": {
                "event_schema": "fixture.inspection",
                "targets": [{"id": "point", "kind": "waypoint", "point": [0, 0]}],
            }},
            "logistics": {"plugin": "logistics", "config": {
                "event_schema": "fixture.logistics", "fleet": [],
            }},
        },
    }}
    first = Message(
        "original-first", "event", "fixture.inspection", "partition", "inspection",
        0, Instant(10), Instant(10), freeze({"sample": {"valid": False}}), (),
    )
    second = replace(first, id="original-second", sequence=1)
    proposal = encode(Emit("event", first.schema_id, "fixture", first.at, first.payload))
    records = [header, {
        "index": 1, "type": "transaction", "instant": encode(Instant(10)),
        "items": [
            {"kind": "operation", "proposal": proposal, "message": first.to_data()},
            {"kind": "operation", "proposal": proposal, "message": second.to_data()},
            {"kind": "delivery", "message": first.to_data()},
            {"kind": "delivery", "message": replace(first, id="delivery-only").to_data()},
        ],
    }, {
        "index": 2, "type": "transaction", "instant": encode(Instant(20)),
        "items": [{"kind": "ingress_publish", "message": first.to_data()}],
    }]
    data = b"".join(canonical_json(record) for record in records)
    (directory / "journal.jsonl").write_bytes(data)
    write_prefix(directory, {"bytes": len(data), "index": 2})
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest.pop("configuration_digest", None)
    manifest["final_cursor"] = 3
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory, records


def test_equal_payload_emissions_survive_and_delivery_or_publication_copies_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, records = diagnostic_run(tmp_path / "diagnostic")
    forbid_live(monkeypatch)
    before = inventory(directory)
    with open_run(directory) as source:
        originals = list(source.snapshot().iter_messages())
    assert [m.message_id for m in originals] == ["original-first", "original-second"]
    assert originals[0].payload == originals[1].payload
    text = report.export_report(directory, tmp_path / "report").read_text()
    assert text.count("| event `fixture.inspection`:") == 2
    feed = thumbnails.viewer_feed(directory, Cut(2, Instant(20)))
    messages = [m for row in feed["commits"] for m in row["messages"]]
    assert [m["id"] for m in messages] == [m.message_id for m in originals]
    assert [m["source_cursor"] for m in messages] == [list(m.cursor) for m in originals]
    assert feed["commits"][-1]["messages"] == []
    result = compute(SimpleNamespace(
        header=records[0], records=records[1:],
        configuration=freeze(records[0]["configuration"]),
    ))
    assert result["inspection"]["observations"] == 2
    assert result["inspection"]["invalid_observations"] == 2
    assert result["inspection"]["targets"]["point"]["mean_revisit_s"] is None
    assert result["logistics"]["mean_delivery_time_s"] is None
    assert result["logistics"]["utilization"] is None
    assert inventory(directory) == before


def test_thumbnail_admission_keeps_its_own_explicit_typed_subjects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, previous = diagnostic_run(tmp_path / "admission")
    # A diagnostic reader/formatter fixture, not a semantic replay claim. Its
    # admission has all refs already; publication must not enrich its timing.
    subject = EntityRef("realtime-ingress", "fixture", "sensor-1", 7, "Fixture")
    large = replace(subject, id="large-generation", generation=9_007_199_254_740_993)
    payload = {
        "nested": [{"actor": {"$ref": subject.to_data()}}, {"$ref": large.to_data()}],
        "again": {"$ref": subject.to_data()}, "scalar_name": "not-a-subject",
    }
    message = Message(
        "admitted-command", "command", "fixture.command", "ingress", "stream:sensor",
        0, Instant(20), Instant(20), freeze(payload), (),
    )
    records = [previous[0], {
        "index": 1, "type": "transaction", "instant": encode(Instant(10)),
        "items": [{
            "kind": "reservation", "message_id": message.id,
            "request": encode(CommandRequest("fixture.command", "sensor", Instant(20), payload)),
            "source": "stream:sensor", "sequence": 0,
        }],
    }, {
        "index": 2, "type": "transaction", "instant": encode(Instant(20)),
        "items": [{"kind": "ingress_publish", "message": message.to_data()}],
    }]
    data = b"".join(canonical_json(record) for record in records)
    (directory / "journal.jsonl").write_bytes(data)
    write_prefix(directory, {"bytes": len(data), "index": 2})
    assert project(records[1])["messages"] == []
    forbid_live(monkeypatch)
    before = inventory(directory)
    expected_subjects = [
        {"id": "sensor-1", "generation": 7},
        {"id": "large-generation", "generation": "9007199254740993"},
    ]
    for cut in (Cut(1, Instant(10)), Cut(2, Instant(20))):
        feed = thumbnails.viewer_feed(directory, cut)
        messages = [message for commit in feed["commits"] for message in commit["messages"]]
        assert len(messages) == 1
        assert messages[0]["id"] == "admitted-command"
        assert messages[0]["source_cursor"] == [1, 0]
        assert messages[0]["subjects"] == expected_subjects
        assert messages[0]["at"] == {"ns": "20", "microstep": 0}
    assert feed["commits"][-1]["messages"] == []
    assert inventory(directory) == before


def test_stored_artifacts_are_copied_without_capture_and_missing_blobs_raise(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with open_run(recorded_run) as source:
        header = source.header
    cut = video.cuts(recorded_run)[-1]
    assert header.kernel_run_id is not None and header.epoch is not None
    actor = EntityRef(header.kernel_run_id, header.epoch, "sensor-1", 0, "aas:TelemetryStream")
    request = CaptureRequest(
        "recorded-fixture", actor.run_id, actor, cut, {"revision": "fixture/v1"},
        "fixture-assets", Stamp("canonical", cut.instant.ns, 1, "canonical"),
        16, 16, 1.0,
    )
    store = ArtifactStore(recorded_run)
    store.register(request)
    artifact = store.put(request, png(), renderer_mode="stub")
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    target = report.export_report(recorded_run, tmp_path / "report")
    assert (target.parent / "photos" / (artifact["digest"] + ".png")).read_bytes() == png()
    assert artifact["digest"] in target.read_text()
    assert inventory(recorded_run) == before
    (recorded_run / "artifacts/blobs" / (artifact["digest"] + ".png")).unlink()
    before = inventory(recorded_run)
    with pytest.raises(FileNotFoundError):
        report.export_report(recorded_run, tmp_path / "missing-photo")
    assert inventory(recorded_run) == before


def test_thumbnail_rejects_an_unrecorded_cut_or_wrong_instant(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cut = video.cuts(recorded_run)[-1]
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    with pytest.raises(ValueError, match="ends before"):
        thumbnails.viewer_feed(recorded_run, Cut(cut.index + 1, cut.instant))
    with pytest.raises(ValueError, match="recorded instant"):
        thumbnails.viewer_feed(recorded_run, Cut(cut.index, Instant(cut.instant.ns + 1)))
    assert inventory(recorded_run) == before


def test_thumbnail_request_uses_reader_identity_without_manifest_identity_fields(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with open_run(recorded_run) as source:
        identity = asdict(source.header)
    cut = video.cuts(recorded_run)[-1]
    manifest = recorded_run / "manifest.json"
    metadata = json.loads(manifest.read_text())
    metadata.pop("kernel_run_id", None)
    metadata.pop("epoch", None)
    manifest.write_text(json.dumps(metadata))
    # Authored pose DTO for request construction only, not a measured camera
    # sample or a claim about the scalar telemetry fixture's physical position.
    feed = {
        "header": {
            "types": [{"typeId": "Fixture", "ancestors": []}],
            "presentation": [{
                "typeId": "Fixture", "frame": "enu", "positionField": "pose",
            }],
        },
        "commits": [{
            "created": [{"id": "subject", "generation": 0, "typeId": "Fixture"}],
            "facts": [{
                "entity": {"id": "subject", "generation": 0}, "fieldId": "pose",
                "value": [1.0, 2.0, 3.0],
                "acquired": {"clockId": "canonical", "mappingId": "canonical",
                             "numerator": "0", "denominator": "1"},
                "version": {"journalIndex": cut.index, "itemOrdinal": 0},
            }],
            "retracted": [], "removed": [],
        }],
    }
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    request = thumbnails.capture_request(recorded_run, cut, {
        "subject": "subject", "eye": [1, 2, 3], "target": [0, 0, 0],
    }, feed)
    assert request.actor.run_id == identity["kernel_run_id"]
    assert request.actor.epoch == identity["epoch"]
    assert request.source_cut == cut
    assert request.camera["overview"]["eye"] == [2.0, 5.0, 1.0]
    assert inventory(recorded_run) == before


def test_bare_directory_metrics_keep_legacy_shape_and_need_no_ack(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (recorded_run / "committed-prefix.json").unlink()
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    # Same public kernel replay admission as origin/main, with no evidence field
    # added to this historical shape (existing pack callers compare it exactly).
    expected = {"run_id": "realtime-ingress", "duration_s": 0.1}
    assert metrics(recorded_run) == expected
    assert inventory(recorded_run) == before


@pytest.mark.parametrize("prefix", [{}, {"bytes": 0, "index": 0}])
def test_invalid_published_ack_does_not_fall_back_to_visible_journal(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch, prefix: dict[str, Any],
) -> None:
    write_prefix(recorded_run, prefix)
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    with pytest.raises(RunReadError):
        metrics(recorded_run)
    assert inventory(recorded_run) == before


def test_bare_directory_metrics_keep_incomplete_journal_error(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (recorded_run / "committed-prefix.json").unlink()
    journal = recorded_run / "journal.jsonl"
    journal.write_bytes(journal.read_bytes()[:-5])
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    with pytest.raises(ValueError, match="JOURNAL_TRUNCATED|complete journal"):
        metrics(recorded_run)
    assert inventory(recorded_run) == before


def test_standalone_metrics_remain_explicitly_unacknowledged(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {"run_id": "realtime-ingress", "duration_s": 0.1}
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    result = metrics(recorded_run / "journal.jsonl")
    assert {key: value for key, value in result.items() if key != "source_evidence"} == expected
    assert result["source_evidence"]["reader_api_version"] is None
    assert result["source_evidence"]["acknowledged"] is False
    assert inventory(recorded_run) == before
