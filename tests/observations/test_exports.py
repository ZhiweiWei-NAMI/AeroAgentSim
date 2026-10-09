"""Finished real-kernel prefixes, portable photo reports and decoded replay MP4."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Instant
from aerokernel.values import canonical_json

from aeroagentsim.observations.contracts import CaptureRequest, content_digest
from aeroagentsim.observations.renderer import RenderedFrame
from aeroagentsim.services.storage import RunStorage
from tests.observations.test_browser import (
    browser_scene as browser_scene,  # noqa: PLC0414 - fixture re-export
)
from tests.observations.test_capture import FixtureRenderer, png, setup_capture
from tools.demos.export_traffic_report import export_report
from tools.demos.record_run_views import cuts, record_views, schedule


def finished_run(path: Path) -> tuple[Path, dict[str, Any]]:
    kernel, capture, request = setup_capture(path, FixtureRenderer(png()))
    command = kernel.submit(
        CommandRequest(
            "capture.request",
            "capture",
            Instant(1_000_000_000),
            request.to_command_data(),
        )
    )
    kernel.run_until(2_000_000_000)
    assert kernel.view().action(command).status == "succeeded"
    path.mkdir(exist_ok=True)
    (path / "journal.jsonl").write_bytes(kernel.journal.bytes)
    (path / "runtime.registry.json").write_bytes(
        canonical_json(capture.build.registry.to_data())
    )
    (path / "scenario.json").write_text(
        json.dumps({"id": "capture-test", "registry": {}})
    )
    (path / "manifest.json").write_text(
        json.dumps(
            {"id": path.name, "kernel_run_id": "capture-test", "status": "completed"}
        )
    )
    storage = RunStorage(path)
    storage.index()
    storage.status("completed")
    view = {
        "id": "overview",
        "actor": request.actor.to_data(),
        "camera": request.camera,
        "asset_digest": request.asset_digest,
        "width": 16,
        "height": 16,
    }
    kernel.close()
    return path, view


class RecorderFixture:
    def __init__(self) -> None:
        self.requests: list[CaptureRequest] = []
        self.labels: list[str | None] = []

    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame:
        self.requests.append(request)
        self.labels.append(label)
        return RenderedFrame(png(), "stub")

    def close(self) -> None:
        pass


def source_hashes(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(path)): content_digest(file.read_bytes())
        for file in path.rglob("*")
        if file.is_file()
    }


def test_report_actual_receipts_photo_numbers_and_no_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = finished_run(tmp_path / "run")
    before = source_hashes(run)
    # Export reads stored data even when provider and renderer methods cannot run.
    monkeypatch.setattr(
        FixtureRenderer, "render", lambda *a, **k: pytest.fail("report rendered")
    )
    report = export_report(run, tmp_path / "report")
    text = report.read_text()
    assert "capture.request" in text and '"succeeded"' in text
    assert "model call totals are unavailable" in text
    record = next((run / "artifacts" / "records").glob("*.json"))
    artifact = json.loads(record.read_text())
    assert artifact["digest"] in text
    assert (
        report.parent / "photos" / (artifact["digest"] + ".png")
    ).read_bytes() == png()
    assert str(artifact["byte_count"]) in text
    assert source_hashes(run) == before
    with pytest.raises(ValueError, match="outside"):
        export_report(run, run / "report")
    blob = run / "artifacts" / "blobs" / (artifact["digest"] + ".png")
    blob.write_bytes(b"bad")
    with pytest.raises(ValueError, match="SHA-256"):
        export_report(run, tmp_path / "corrupt-report")


def test_recorder_cuts_speed_failure_manifest_and_immutable_run(tmp_path: Path) -> None:
    run, view = finished_run(tmp_path / "run")
    source = cuts(run)
    normal = schedule(source, 4, Fraction(1))
    faster = schedule(source, 4, Fraction(2))
    assert len(normal) == 9 and len(faster) == 5
    assert faster[-1] == source[-1]
    # Same physical time selects the final journal microstep/index.
    assert (
        normal[0]
        == [cut for cut in source if cut.instant.ns == source[0].instant.ns][-1]
    )
    before = source_hashes(run)
    renderer = RecorderFixture()
    manifest = record_views(
        run,
        tmp_path / "video",
        [view],
        renderer=renderer,
        fps=4,
        speed=Fraction(2),
        ffmpeg=None,
    )
    assert manifest["views"][0]["status"] == "failed"
    assert "ffmpeg unavailable" in manifest["views"][0]["reason"]
    assert renderer.requests == []
    assert source_hashes(run) == before
    with pytest.raises(ValueError, match="outside"):
        record_views(
            run,
            run / "videos",
            [view],
            renderer=renderer,
            fps=4,
            speed=Fraction(2),
            ffmpeg=None,
        )
    with pytest.raises(ValueError, match="at least one"):
        record_views(
            run,
            tmp_path / "empty",
            [],
            renderer=renderer,
            fps=4,
            speed=Fraction(2),
            ffmpeg=None,
        )


@pytest.mark.media
def test_real_ffmpeg_decodes_actual_frame_count(tmp_path: Path) -> None:
    ffmpeg = os.environ.get("AEROAGENTSIM_FFMPEG") or shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip(
            "ffmpeg unavailable; media gate requires an explicit installed executable"
        )
    run, view = finished_run(tmp_path / "run")
    renderer = RecorderFixture()
    before = source_hashes(run)
    manifest = record_views(
        run,
        tmp_path / "video",
        [view],
        renderer=renderer,
        fps=4,
        speed=Fraction(2),
        ffmpeg=ffmpeg,
        encoder="mpeg4",
    )
    result = manifest["views"][0]
    assert result["status"] == "completed", result
    assert result["frame_count"] == len(renderer.requests) == 5
    assert result["renderer_mode"] == "stub"
    assert all(
        label is not None and "speed=2x" in label and "source=" in label
        for label in renderer.labels
    )
    target = tmp_path / "video" / result["file"]
    assert content_digest(target.read_bytes()) == result["digest"]
    decoded = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(target),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    assert len(decoded.stdout) == result["frame_count"] * 16 * 16 * 3
    assert source_hashes(run) == before


@pytest.mark.browser
@pytest.mark.media
def test_actual_browser_replay_mp4_decodes(
    browser_scene: tuple[str, Path, CaptureRequest], tmp_path: Path
) -> None:
    from aeroagentsim.observations.renderer import BrowserRenderer
    from aeroagentsim.services.projector import project

    ffmpeg = os.environ.get("AEROAGENTSIM_FFMPEG") or shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg unavailable; combined browser/media gate requires it")
    url, modules, scene_request = browser_scene
    run, view = finished_run(tmp_path / "video-run")
    storage = RunStorage(run)
    (tmp_path / "viewer-build" / "feed.json").write_text(
        json.dumps([project(row) for row in storage.records(1, 4096)])
    )
    view.update(
        camera=scene_request.camera,
        asset_digest=scene_request.asset_digest,
        width=128,
        height=96,
    )
    with BrowserRenderer(
        url,
        node_modules=modules,
        timeout_s=20.0,
        browser_executable=Path(os.environ["AEROAGENTSIM_CHROMIUM"])
        if "AEROAGENTSIM_CHROMIUM" in os.environ
        else None,
    ) as renderer:
        manifest = record_views(
            run,
            tmp_path / "browser-video",
            [view],
            renderer=renderer,
            fps=4,
            speed=Fraction(2),
            ffmpeg=ffmpeg,
            encoder="mpeg4",
        )
    result = manifest["views"][0]
    assert result["status"] == "completed", result
    assert result["renderer_mode"] == "browser"
    target = tmp_path / "browser-video" / result["file"]
    decoded = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(target),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    assert len(decoded.stdout) == result["frame_count"] * 128 * 96 * 3


def test_incomplete_index_cannot_be_reported_as_complete(tmp_path: Path) -> None:
    run, view = finished_run(tmp_path / "run")
    entries = json.loads((run / "index.json").read_text())
    (run / "index.json").write_text(json.dumps(entries[:-1]))
    with pytest.raises(ValueError, match="final cursor"):
        export_report(run, tmp_path / "report")
    with pytest.raises(ValueError, match="final cursor"):
        record_views(
            run,
            tmp_path / "videos",
            [view],
            renderer=RecorderFixture(),
            fps=4,
            speed=Fraction(2),
            ffmpeg=None,
        )


def test_internal_index_gap_cannot_silently_drop_evidence(tmp_path: Path) -> None:
    run, _ = finished_run(tmp_path / "run")
    entries = json.loads((run / "index.json").read_text())
    (run / "index.json").write_text(json.dumps(entries[:3] + entries[4:]))
    with pytest.raises(ValueError, match="gap"):
        export_report(run, tmp_path / "report")
    with pytest.raises(ValueError, match="gap"):
        cuts(run)


def test_explicit_decision_schema_exports_actual_recorded_usage(tmp_path: Path) -> None:
    from typing import cast

    from aerokernel import (
        BindingManifest,
        Kernel,
        MemoryRegistry,
        MessageDescriptor,
        Partition,
        TypeDescriptor,
    )
    from aerokernel.engine import Engine
    from aerokernel.sdk import ContextEngine, EngineContext

    from aeroagentsim.observations.schemas import TEXT, record
    from aeroagentsim.services.projector import project

    data = {
        "model": "explicit-recorded-fixture",
        "usage": {"prompt_tokens": 7, "completion_tokens": 11},
    }

    class DecisionLog(ContextEngine):
        def __init__(self) -> None:
            super().__init__(
                Partition(
                    "logger",
                    "logger",
                    emits=("custom.log",),
                    message_targets=("public",),
                )
            )

        def bootstrap(self, ctx: EngineContext) -> None:
            ctx.emit(
                "custom.log",
                {"phase": "response", "data_json": json.dumps(data)},
                topic="public",
            )

    registry = MemoryRegistry(
        (TypeDescriptor("Log"),),
        messages=(
            MessageDescriptor(
                "custom.log", schema=record({"phase": TEXT, "data_json": TEXT})
            ),
        ),
    )
    kernel = Kernel()
    kernel.bind(
        registry, BindingManifest("log-run", "e"), (cast(Engine, DecisionLog()),)
    )
    kernel.start()
    kernel.run_until(1)
    run = tmp_path / "run"
    run.mkdir()
    (run / "journal.jsonl").write_bytes(kernel.journal.bytes)
    (run / "runtime.registry.json").write_bytes(canonical_json(registry.to_data()))
    (run / "scenario.json").write_text(json.dumps({"registry": {}}))
    (run / "manifest.json").write_text(
        json.dumps({"id": "log-run", "status": "completed"})
    )
    storage = RunStorage(run)
    storage.index()
    storage.status("completed")
    messages = [
        message
        for row in storage.records(1, 4096)
        for message in project(row)["messages"]
    ]
    recorded = json.loads(messages[0]["payload"]["data_json"])
    report = export_report(
        run, tmp_path / "report", decision_schemas=("custom.log",)
    ).read_text()
    assert json.dumps(recorded, indent=2, ensure_ascii=False) in report
    assert '"response": 1' in report
    assert "model attempts" in report
    kernel.close()
