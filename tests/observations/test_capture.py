"""Real kernel receipts, capture failures and zero-render artifact replay."""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from aerokernel import (
    BindingManifest,
    BindingRule,
    CommandRequest,
    EntityRef,
    FieldDescriptor,
    Instant,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.engine import Engine
from aerokernel.sdk import ContextEngine, EngineContext

from aeroagentsim.engines.common import policies
from aeroagentsim.observations.capture import Capture, replay_capture
from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.observations.png import validate_png
from aeroagentsim.observations.renderer import RenderedFrame
from aeroagentsim.observations.schemas import (
    CAPTURE_RECEIPT,
    METADATA_SCHEMAS,
    REQUEST,
    STORAGE_RESULT,
    UPLOAD_RECEIPT,
    UPLOAD_VERIFIED,
)
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.services.projector import project

ACTOR = EntityRef("capture-test", "e1", "subject", 0, "Subject")


def png(width: int = 16, height: int = 16, value: int = 60) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(
            b"IDAT", zlib.compress((b"\x00" + bytes([value, 20, 100]) * width) * height)
        )
        + chunk(b"IEND", b"")
    )


class FixtureRenderer:
    """Selected stub; errors are authored test responses, never fallback paths."""

    def __init__(self, data: bytes | Exception) -> None:
        self.data = data
        self.calls = 0

    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame:
        self.calls += 1
        if isinstance(self.data, Exception):
            raise self.data
        return RenderedFrame(self.data, "stub")

    def close(self) -> None:
        pass


class Subject(ContextEngine):
    def __init__(self) -> None:
        super().__init__(
            Partition("subject", "subject", lifecycle=True, produces=("pose",)),
            policies=policies(("pose",)),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        ctx.create(ACTOR)
        ctx.set(ACTOR, "pose", [3.0, 1.0, 2.0])


def setup_capture(
    tmp_path: Path, renderer: Any
) -> tuple[Kernel, Capture, CaptureRequest]:
    fields = {key: "capture." + key for key in METADATA_SCHEMAS}
    registry = MemoryRegistry(
        (TypeDescriptor("Subject"), TypeDescriptor("CaptureRecord")),
        tuple(
            FieldDescriptor(fields[name], "CaptureRecord", schema)
            for name, schema in METADATA_SCHEMAS.items()
        )
        + (
            FieldDescriptor(
                "pose",
                "Subject",
                {"type": "vector", "length": 3, "items": {"type": "number"}},
            ),
        ),
        (
            MessageDescriptor(
                "capture.request", "command", REQUEST, result_schema=CAPTURE_RECEIPT
            ),
            MessageDescriptor(
                "capture.upload",
                "command",
                STORAGE_RESULT,
                result_schema=UPLOAD_RECEIPT,
            ),
            MessageDescriptor("capture.upload_verified", "event", UPLOAD_VERIFIED),
        ),
    )
    manifest = BindingManifest(
        "capture-test",
        "e1",
        (ACTOR,),
        rules=(
            BindingRule("capture", "CaptureRecord", tuple(fields.values())),
            BindingRule("subject", "Subject", ("pose",)),
        ),
        lifecycle=(
            LifecycleRule("subject", "Subject"),
            LifecycleRule("capture", "CaptureRecord"),
        ),
    )
    subject = Subject()
    config = {
        "request_schema": "capture.request",
        "storage_result_schema": "capture.upload",
        "accepted_schema": "capture.upload_verified",
        "accepted_topic": "uploads",
        "record_type": "CaptureRecord",
        "record_id_prefix": "image/",
        "fields": fields,
        "run_directory": str(tmp_path),
        "renderer": {"mode": "stub", "fixtures": {}},
    }
    build = EngineBuild(
        "capture",
        config,
        registry,
        manifest,
        (ACTOR,),
        {ACTOR.id: {}},
        {"subject": subject.partition},
    )
    capture = Capture(build, renderer=renderer)
    kernel = Kernel()
    engines = (cast(Engine, subject), cast(Engine, capture))
    kernel.bind(registry, manifest, engines)
    kernel.start()
    cut = kernel.view().cut
    request = CaptureRequest(
        "photo-1",
        "capture-test",
        ACTOR,
        cut,
        {"preset": "overview", "revision": "1"},
        "a" * 64,
        Stamp("canonical", cut.instant.ns, 1, "canonical"),
        16,
        16,
        1.0,
    )
    return kernel, capture, request


def receipts(kernel: Kernel) -> list[dict[str, Any]]:
    return [r for record in kernel.records[1:] for r in project(record)["receipts"]]


def test_capture_fields_receipts_separate_upload_and_replay(tmp_path: Path) -> None:
    renderer = FixtureRenderer(png())
    kernel, capture, request = setup_capture(tmp_path, renderer)
    command = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(1), request.to_command_data()
        )
    )
    kernel.run_until(1)
    result = kernel.view().action(command)
    assert result.status == "succeeded"
    record = capture.store.get(request.request_id)
    assert record["byte_count"] == len(png())
    assert renderer.calls == 1
    assert not any(
        project(r)["messages"]
        for r in kernel.records[1:]
        if any(
            m["schemaId"] == "capture.upload_verified" for m in project(r)["messages"]
        )
    )
    upload = kernel.submit(
        CommandRequest(
            "capture.upload",
            "capture",
            Instant(2),
            {"request_id": request.request_id, "digest": record["digest"]},
        )
    )
    kernel.run_until(2)
    assert kernel.view().action(upload).status == "succeeded"
    events = [
        m
        for r in kernel.records[1:]
        for m in project(r)["messages"]
        if m["schemaId"] == "capture.upload_verified"
    ]
    assert len(events) == 1
    journal = kernel.journal.bytes
    assert replay(journal).records == kernel.records
    assert replay_capture(capture.store, request.request_id) == record
    assert renderer.calls == 1
    duplicate = kernel.submit(
        CommandRequest(
            "capture.upload",
            "capture",
            Instant(3),
            {"request_id": request.request_id, "digest": record["digest"]},
        )
    )
    kernel.run_until(3)
    assert kernel.view().action(duplicate).status == "failed"
    kernel.close()


@pytest.mark.parametrize(
    "response,status",
    [
        (b"not a PNG", "failed"),
        (b"", "failed"),
        (TimeoutError("renderer timed out"), "timeout"),
        (RuntimeError("no camera"), "failed"),
    ],
)
def test_failed_render_cannot_create_success(
    tmp_path: Path, response: bytes | Exception, status: str
) -> None:
    kernel, capture, request = setup_capture(tmp_path, FixtureRenderer(response))
    command = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(1), request.to_command_data()
        )
    )
    kernel.run_until(1)
    assert kernel.view().action(command).status == "failed"
    terminal = [
        receipt for receipt in receipts(kernel) if receipt["status"] == "failed"
    ][-1]
    assert terminal["result"]["status"] == status
    assert capture.store.list() == []
    with pytest.raises(ValueError, match="closed"):
        capture.store.register(request)
        capture.store.put(request, png(), renderer_mode="browser")
    kernel.close()


def test_forged_cut_fails_before_render(tmp_path: Path) -> None:
    renderer = FixtureRenderer(png())
    kernel, _, request = setup_capture(tmp_path, renderer)
    request = replace(
        request, source_cut=replace(request.source_cut, instant=Instant(77))
    )
    command = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(1), request.to_command_data()
        )
    )
    kernel.run_until(1)
    assert kernel.view().action(command).status == "failed"
    assert renderer.calls == 0
    kernel.close()


def test_stale_generation_rejected_without_render(tmp_path: Path) -> None:
    renderer = FixtureRenderer(png())
    kernel, _, request = setup_capture(tmp_path, renderer)
    stale = replace(request, actor=replace(ACTOR, generation=1))
    command = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(1), stale.to_command_data()
        )
    )
    kernel.run_until(1)
    assert kernel.view().action(command).status == "failed"
    assert renderer.calls == 0
    kernel.close()


def test_artifact_conflicts_corruption_and_wrong_actor(tmp_path: Path) -> None:
    kernel, capture, request = setup_capture(tmp_path, FixtureRenderer(png()))
    store = capture.store
    store.register(request)
    record = store.put(request, png(), renderer_mode="stub")
    assert store.put(request, png(), renderer_mode="stub") == record
    with pytest.raises(ValueError, match="conflict"):
        store.put(request, png(value=30), renderer_mode="stub")
    with pytest.raises(ValueError, match="outstanding"):
        store.put(
            replace(request, actor=replace(ACTOR, id="other")),
            png(),
            renderer_mode="stub",
        )
    blob = tmp_path / "artifacts" / "blobs" / (record["digest"] + ".png")
    blob.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="PNG"):
        replay_capture(store, request.request_id)
    kernel.close()


def test_png_crc_truncation_dimensions_and_deflate() -> None:
    data = png()
    assert validate_png(data) == (16, 16)
    for invalid in (data[:-1], data + b"trailing", data[:40] + b"x" + data[41:]):
        with pytest.raises(ValueError):
            validate_png(invalid)
    with pytest.raises(ValueError, match="dimensions"):
        validate_png(data, expected=(10, 10))


def test_replay_rejects_tampered_capture_metadata(tmp_path: Path) -> None:
    kernel, capture, request = setup_capture(tmp_path, FixtureRenderer(png()))
    capture.store.register(request)
    capture.store.put(request, png(), renderer_mode="stub")
    path = next((tmp_path / "artifacts" / "records").glob("*.json"))
    record = json.loads(path.read_bytes())
    record["request"]["asset_digest"] = "f" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="registered capture request"):
        replay_capture(capture.store, request.request_id)
    kernel.close()


def test_duplicate_capture_request_is_typed_failure(tmp_path: Path) -> None:
    renderer = FixtureRenderer(png())
    kernel, capture, request = setup_capture(tmp_path, renderer)
    first = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(1), request.to_command_data()
        )
    )
    kernel.run_until(1)
    second = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(2), request.to_command_data()
        )
    )
    kernel.run_until(2)
    assert kernel.view().action(first).status == "succeeded"
    assert kernel.view().action(second).status == "failed"
    assert len(capture.store.list()) == 1
    assert renderer.calls == 1
    kernel.close()


def test_semantically_invalid_typed_request_returns_failure_without_fault(
    tmp_path: Path,
) -> None:
    renderer = FixtureRenderer(png())
    kernel, _, request = setup_capture(tmp_path, renderer)
    data = request.to_command_data()
    data["height"] = 0
    command = kernel.submit(
        CommandRequest("capture.request", "capture", Instant(1), data)
    )
    kernel.run_until(1)
    assert kernel.view().action(command).status == "failed"
    assert renderer.calls == 0
    # The prefix remains executable after the invalid request is answered.
    valid = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(2), request.to_command_data()
        )
    )
    kernel.run_until(2)
    assert kernel.view().action(valid).status == "succeeded"
    kernel.close()


def test_legacy_artifact_filenames_are_read_without_content_pins(
    tmp_path: Path,
) -> None:
    """An old capture record still resolves by its recorded request identity."""
    from aeroagentsim.observations.contracts import artifact_id

    kernel, capture, request = setup_capture(tmp_path, FixtureRenderer(png()))
    capture.store.register(request)
    record = capture.store.put(request, png(), renderer_mode="stub")
    key = artifact_id(request.request_id)
    assert record["digest"] == key
    root = tmp_path / "artifacts"
    (root / "requests" / (key + ".json")).rename(
        root / "requests" / ("a" * 64 + ".json")
    )
    (root / "blobs" / (key + ".png")).rename(root / "blobs" / ("e" * 64 + ".png"))
    record["digest"] = "e" * 64
    record["camera_digest"] = "b" * 64
    current = root / "records" / (key + ".json")
    current.unlink()
    (root / "records" / ("c" * 64 + ".json")).write_text(json.dumps(record))
    assert capture.store.get(request.request_id) == record
    kernel.close()
