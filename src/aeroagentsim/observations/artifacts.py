"""Run-local content-addressed bytes; storage never implies task acceptance."""

from __future__ import annotations

import builtins
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aerokernel.values import canonical_json

from aeroagentsim.scenario.loader import contract

from .contracts import CaptureRequest, content_digest, digest
from .png import validate_png


class ArtifactStore:
    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory.resolve()
        self.directory = self.run_directory / "artifacts"

    def _key(self, request_id: str) -> str:
        return content_digest(request_id.encode("utf-8"))

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _atomic(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            Path(name).replace(path)
        finally:
            Path(name).unlink(missing_ok=True)

    def register(self, request: CaptureRequest) -> None:
        """Called only by a dispatched capture owner, never by the upload route."""
        with self._write_lock():
            path = (
                self.directory / "requests" / (self._key(request.request_id) + ".json")
            )
            if path.exists():
                if path.read_bytes() != canonical_json(request.to_data()):
                    raise ValueError(
                        "capture request identity conflicts with recorded request"
                    )
                return
            self._atomic(path, canonical_json(request.to_data()))

    def request(self, request_id: str) -> CaptureRequest:
        path = self.directory / "requests" / (self._key(request_id) + ".json")
        result = CaptureRequest.from_data(json.loads(path.read_bytes()))
        if result.request_id != request_id:
            raise ValueError("stored capture request identity mismatch")
        return result

    def put(
        self, request: CaptureRequest, png: bytes, *, renderer_mode: str
    ) -> dict[str, Any]:
        """Persist validated bytes before the storage result can enter the kernel."""
        if renderer_mode not in {"browser", "stub"}:
            raise ValueError("explicit browser or stub renderer mode required")
        validate_png(png, expected=(request.width, request.height))
        content = content_digest(png)
        record: dict[str, Any] = {
            "contract": "aeroagentsim.capture-artifact/v1",
            "request": request.to_data(),
            "digest": content,
            "byte_count": len(png),
            "media_type": "image/png",
            "camera_digest": request.camera_digest,
            "renderer_mode": renderer_mode,
        }
        with self._write_lock():
            closed = (
                self.directory / "closed" / (self._key(request.request_id) + ".json")
            )
            if closed.exists():
                raise ValueError("capture request is closed: " + closed.read_text())
            if self.request(request.request_id).to_data() != request.to_data():
                raise ValueError("upload differs from outstanding capture request")
            target = (
                self.directory / "records" / (self._key(request.request_id) + ".json")
            )
            if target.exists():
                if json.loads(target.read_bytes()) != record:
                    raise ValueError(
                        "idempotency conflict: request already has different content/metadata"
                    )
                self.read(content)
                return record
            blob = self.directory / "blobs" / (content + ".png")
            if blob.exists():
                if blob.read_bytes() != png:
                    raise ValueError("stored artifact blob is corrupt")
            else:
                self._atomic(blob, png)
            self._atomic(target, canonical_json(record))
        return record

    def _record(self, path: Path) -> dict[str, Any]:
        record = contract(
            json.loads(path.read_bytes()),
            "artifact.record",
            {
                "contract",
                "request",
                "digest",
                "byte_count",
                "media_type",
                "camera_digest",
                "renderer_mode",
            },
        )
        if (
            record["contract"] != "aeroagentsim.capture-artifact/v1"
            or record["media_type"] != "image/png"
        ):
            raise ValueError("unsupported artifact record contract/media type")
        return record

    def get(self, request_id: str) -> dict[str, Any]:
        path = self.directory / "records" / (self._key(request_id) + ".json")
        record = self._record(path)
        request = CaptureRequest.from_data(record["request"])
        if (
            request.request_id != request_id
            or record["camera_digest"] != request.camera_digest
        ):
            raise ValueError("artifact record identity/camera integrity mismatch")
        if self.request(request_id).to_data() != request.to_data():
            raise ValueError(
                "artifact metadata differs from registered capture request"
            )
        if record["renderer_mode"] not in {"browser", "stub"}:
            raise ValueError("artifact record has no explicit renderer mode")
        data = self.read(record["digest"], expected=(request.width, request.height))
        if type(record["byte_count"]) is not int or len(data) != record["byte_count"]:
            raise ValueError("artifact byte count mismatch")
        return record

    def list(self) -> builtins.list[dict[str, Any]]:
        result = []
        for path in sorted((self.directory / "records").glob("*.json")):
            value = self._record(path)
            result.append(self.get(value["request"]["request_id"]))
        return result

    def read(self, content: str, *, expected: tuple[int, int] | None = None) -> bytes:
        data = (self.directory / "blobs" / (digest(content) + ".png")).read_bytes()
        if content_digest(data) != content:
            raise ValueError("artifact SHA-256 mismatch")
        validate_png(data, expected=expected)
        return data

    def by_digest(self, content: str) -> builtins.list[dict[str, Any]]:
        digest(content)
        records = []
        for path in sorted((self.directory / "records").glob("*.json")):
            record = self._record(path)
            if record["digest"] == content:
                request = CaptureRequest.from_data(record["request"])
                records.append(self.get(request.request_id))
        if not records:
            raise FileNotFoundError("artifact has no stored record")
        return records

    def delivery(self, request_id: str, receipt: dict[str, Any]) -> None:
        """Retain transport/admission status; this is never an acceptance event."""
        with self._write_lock():
            self._atomic(
                self.directory / "deliveries" / (self._key(request_id) + ".json"),
                canonical_json(receipt),
            )

    def close_request(self, request_id: str, status: str) -> None:
        """Close failed/timed-out acquisition so delayed uploads cannot succeed."""
        with self._write_lock():
            self._atomic(
                self.directory / "closed" / (self._key(request_id) + ".json"),
                canonical_json({"status": status}),
            )
