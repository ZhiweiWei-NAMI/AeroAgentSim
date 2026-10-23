"""Data-only exports at public reader cuts; no live model, camera or browser.

The genuine telemetry fixtures reuse the run-reader builders. The diagnostic
message fixture deliberately tests decoding/formatting, not semantic replay or
scientific measurements. Unsupported viewer metadata/finality are supplied as
small DTOs; no HTTP server or projection-cache writer is started.
"""

from __future__ import annotations

import copy
import json
import socket
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel import Cut, Emit, EntityRef, Instant, Message, Stamp, replay
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
    expected = compute(replay(recorded_run / "journal.jsonl"))
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
        lambda: metrics(recorded_run),
    ]
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
    monkeypatch.setattr(video, "results_state", lambda *args: next(states))
    monkeypatch.setattr(video, "Event", lambda: SimpleNamespace(wait=waits.append))
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    selected, metadata, evidence = video.recording_source(recorded_run)
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

    monkeypatch.setattr(video, "results_state", lambda *args: state)
    monkeypatch.setattr(video, "Event", lambda: SimpleNamespace(wait=publish_from_owner))
    forbid_live(monkeypatch)
    selected, _, evidence = video.recording_source(recorded_run)
    assert len(waits) == 1
    assert selected[-1].index == evidence["cut_index"] == prefix["index"]
    assert evidence["cut_bytes"] == prefix["bytes"]


def test_absent_projection_finality_times_out_without_render_or_cache_creation(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    # Use the real read-only results-state path with no SQLite cache. A terminal
    # manifest/ACK alone must not trigger a capture or a cache rebuild.
    with pytest.raises(TimeoutError, match="results_final"):
        video.record_views(
            recorded_run, tmp_path / "video",
            [{"id": "fixture", "actor": {}, "camera": {}, "asset_digest": "fixture",
              "width": 16, "height": 16}],
            renderer=SimpleNamespace(render=lambda *args: pytest.fail("rendered")),
            fps=4, speed=Fraction(1), ffmpeg=None, results_timeout_s=0,
        )
    assert not (tmp_path / "video").exists()
    assert inventory(recorded_run) == before


def test_finality_reads_an_existing_wal_cache_without_touching_source_sidecars(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = final_state(recorded_run)
    forbid_live(monkeypatch)
    # An explicitly authored progress fixture, not a simulated KPI measurement.
    # Keep WAL open so this exercises WAL reading, not just a checkpointed DB.
    with closing(sqlite3.connect(recorded_run / video.FILENAME)) as database:
        assert database.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        database.execute(
            "CREATE TABLE projection_progress "
            "(id INTEGER PRIMARY KEY, next INTEGER NOT NULL, signature TEXT NOT NULL)"
        )
        database.execute(
            "INSERT INTO projection_progress VALUES (1, ?, ?)",
            (state["results_cursor"], json.dumps(video._signature(recorded_run))),
        )
        database.commit()
        assert (recorded_run / (video.FILENAME + "-wal")).stat().st_size > 0
        before = inventory(recorded_run)
        _, _, evidence = video.recording_source(recorded_run, timeout_s=0)
        assert evidence["projection"] == state
        assert inventory(recorded_run) == before


def test_recording_manifest_keeps_existing_keys_and_adds_reader_provenance(
    recorded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = final_state(recorded_run)
    metadata = json.loads((recorded_run / "manifest.json").read_text())
    monkeypatch.setattr(video, "results_state", lambda *args: state)
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    manifest = video.record_views(
        recorded_run, tmp_path / "video",
        [{"id": "fixture", "actor": {}, "camera": {}, "asset_digest": "fixture",
          "width": 16, "height": 16}],
        renderer=SimpleNamespace(render=lambda *args: pytest.fail("rendered")),
        fps=4, speed=Fraction(2), ffmpeg=None, results_timeout_s=0,
    )
    assert manifest["contract"] == "aeroagentsim.replay-recordings/v1"
    assert manifest["source_run"] == metadata
    assert manifest["fps"] == 4 and manifest["speed"] == "2"
    assert manifest["terminal_frame_hold_s"] == "1/4"
    assert manifest["source_evidence"]["projection"] == state
    assert manifest["views"][0]["status"] == "failed"
    assert "ffmpeg unavailable" in manifest["views"][0]["reason"]
    assert manifest["views"][0]["source_cuts"][-1]["index"] == state["final_commit_index"]
    assert json.loads((tmp_path / "video/manifest.json").read_text())["source_run"] == metadata
    assert inventory(recorded_run) == before


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


def test_standalone_metrics_remain_explicitly_unacknowledged(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = compute(replay(recorded_run / "journal.jsonl"))
    forbid_live(monkeypatch)
    before = inventory(recorded_run)
    result = metrics(recorded_run / "journal.jsonl")
    assert {key: value for key, value in result.items() if key != "source_evidence"} == expected
    assert result["source_evidence"]["reader_api_version"] is None
    assert result["source_evidence"]["acknowledged"] is False
    assert inventory(recorded_run) == before
