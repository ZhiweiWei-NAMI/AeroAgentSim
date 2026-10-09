"""Persistent headless viewer bridge with an explicitly selected fixture mode."""

from __future__ import annotations

import base64
import json
import math
import os
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contracts import CaptureRequest, browser_decode, browser_encode
from .png import validate_png


@dataclass(frozen=True)
class RenderedFrame:
    png: bytes
    mode: str


class Renderer(Protocol):
    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame: ...
    def close(self) -> None: ...


class StubRenderer:
    """Explicit fixture bytes; missing fixtures fail rather than switch providers."""

    def __init__(self, fixtures: dict[str, Path]) -> None:
        self.fixtures = fixtures

    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame:
        data = self.fixtures[request.request_id].read_bytes()
        validate_png(data, expected=(request.width, request.height))
        return RenderedFrame(data, "stub")

    def close(self) -> None:
        pass


def browser_executable_path(configured: Path | None = None) -> Path | None:
    """Prefer an authored executable, then the operator override, else Playwright."""
    if configured is None:
        value = os.environ.get("AEROAGENTSIM_CHROMIUM")
        if value is None:
            return None
        if not value:
            raise ValueError("AEROAGENTSIM_CHROMIUM: executable path must be nonempty")
        configured = Path(value)
    if not configured.is_file():
        raise FileNotFoundError(f"Chromium executable does not exist: {configured}")
    return configured


class BrowserRenderer:
    """One Node/Playwright browser process per bridge, reused across requests.

    The built viewer implements window.aeroCapture.render as documented in
    docs/guides/visualization.md. Failure kills the bridge; it never selects stub.
    """

    def __init__(
        self,
        viewer_url: str,
        *,
        node_modules: Path,
        timeout_s: float = 10,
        executable: str = "node",
        browser_executable: Path | None = None,
        software_gl: bool = True,
        service_run_id: str | None = None,
    ) -> None:
        if not isinstance(viewer_url, str) or not viewer_url:
            raise ValueError("renderer requires an explicit viewer URL")
        if type(software_gl) is not bool:
            raise TypeError("renderer.software_gl must be an explicit boolean")
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (float, int))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 300
        ):
            raise ValueError("renderer timeout must be finite and in (0,300]")
        self.viewer_url, self.node_modules = viewer_url, node_modules.resolve()
        self.timeout_s = timeout_s
        self.executable = executable
        self.browser_executable = browser_executable_path(browser_executable)
        self.software_gl = software_gl
        self.service_run_id = service_run_id
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = bytearray()

    def __enter__(self) -> BrowserRenderer:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _start(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            self.process = subprocess.Popen(
                [
                    self.executable,
                    str(Path(__file__).with_name("viewer_bridge.mjs")),
                    str(self.node_modules),
                    self.viewer_url,
                    str(self.browser_executable)
                    if self.browser_executable is not None
                    else "",
                    "software" if self.software_gl else "hardware",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return self.process

    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame:
        process = self._start()
        assert process.stdin is not None and process.stdout is not None
        deadline = time.monotonic() + min(self.timeout_s, request.timeout_s)
        try:
            payload = (
                json.dumps(
                    {
                        "request": browser_encode(request.to_data()),
                        "label": label,
                        "service_run_id": self.service_run_id,
                    }
                ).encode()
                + b"\n"
            )
            os.set_blocking(process.stdin.fileno(), False)
            offset = 0
            while offset < len(payload):
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([], [process.stdin], [], left)[1]:
                    raise TimeoutError("viewer request delivery deadline exceeded")
                offset += os.write(process.stdin.fileno(), payload[offset:])
            while b"\n" not in self.buffer:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
                    raise TimeoutError("viewer render deadline exceeded")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError(
                        "renderer bridge exited before supplying a frame"
                    )
                self.buffer.extend(chunk)
                if len(self.buffer) > 96 * 1024 * 1024:
                    raise ValueError("renderer bridge response exceeds limit")
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            reply: dict[str, Any] = json.loads(line)
            if "error" in reply:
                raise RuntimeError("viewer render failed: " + str(reply["error"]))
            if browser_decode(reply["request"]) != request.to_data():
                raise ValueError(
                    "viewer returned wrong actor, source cut, camera or assets"
                )
            png = base64.b64decode(reply["png_base64"], validate=True)
            validate_png(png, expected=(request.width, request.height))
            if time.monotonic() > deadline:
                raise TimeoutError("viewer render deadline exceeded")
            return RenderedFrame(png, "browser")
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError, KeyError):
            self.close()
            raise

    def close(self) -> None:
        process, self.process = self.process, None
        self.buffer.clear()
        if process is None:
            return
        if process.poll() is None:
            # Playwright children share this group, so timeout also closes Chromium.
            import signal

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # The browser group has already exited; no cleanup remains.
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
