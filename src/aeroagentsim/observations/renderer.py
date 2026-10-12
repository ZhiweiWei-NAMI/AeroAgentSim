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
from typing import Any, BinaryIO, Protocol

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
        self.prepared: bytes | None = None

    def __enter__(self) -> BrowserRenderer:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _start(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            read_fd, write_fd = os.pipe()
            with os.fdopen(read_fd, "rb") as ready_channel:
                try:
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
                        pass_fds=(write_fd,),
                        env={**os.environ, "AEROAGENTSIM_RENDERER_READY_FD": str(write_fd)},
                        start_new_session=True,
                    )
                finally:
                    os.close(write_fd)
                try:
                    ready = self._read_reply(time.monotonic() + 30, "viewer startup deadline exceeded", stream=ready_channel)
                    if ready != {"contract": "aeroagentsim.viewer-ready/v1"}:
                        raise RuntimeError("viewer startup failed: " + str(ready))
                except (ValueError, TypeError, RuntimeError, TimeoutError, OSError):
                    self.close()
                    raise
        return self.process

    def _read_reply(self, deadline: float, timeout: str, *, stream: BinaryIO | None = None) -> dict[str, Any]:
        process = self.process
        assert process is not None
        channel = process.stdout if stream is None else stream
        assert channel is not None
        buffer = self.buffer if stream is None else bytearray()
        while b"\n" not in buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([channel], [], [], left)[0]:
                raise TimeoutError(timeout)
            chunk = os.read(channel.fileno(), 65536)
            if not chunk:
                raise RuntimeError("renderer bridge exited before supplying a reply")
            buffer.extend(chunk)
            if len(buffer) > 96 * 1024 * 1024:
                raise ValueError("renderer bridge response exceeds limit")
        line, _, remainder = buffer.partition(b"\n")
        if stream is None:
            self.buffer = bytearray(remainder)
        reply = json.loads(line)
        if not isinstance(reply, dict):
            raise TypeError("renderer bridge reply must be an object")
        return reply

    def render(
        self, request: CaptureRequest, *, label: str | None = None,
        scene: dict[str, Any] | None = None,
    ) -> RenderedFrame:
        process = self._start()
        assert process.stdin is not None and process.stdout is not None
        try:
            if scene is not None:
                preparation = {
                    "service_run_id": self.service_run_id,
                    "asset_digest": request.asset_digest,
                    "header": scene["header"],
                    "asset_manifest": scene["asset_manifest"],
                    "oblique": request.camera.get("revision") == "uav-gimbal-oblique/v1",
                }
                encoded = json.dumps(browser_encode({"operation": "prepare", "preparation": preparation})).encode() + b"\n"
                if encoded != self.prepared:
                    deadline = time.monotonic() + 30
                    self._send(process, encoded, deadline)
                    ready = self._read_reply(deadline, "viewer asset initialization deadline exceeded")
                    expected = {"contract": "aeroagentsim.viewer-assets-ready/v1", "service_run_id": self.service_run_id, "asset_digest": request.asset_digest}
                    if ready != expected:
                        raise RuntimeError("viewer asset initialization failed: " + str(ready))
                    self.prepared = encoded
            deadline = time.monotonic() + min(self.timeout_s, request.timeout_s)
            payload = (
                json.dumps(
                    {
                        "request": browser_encode(request.to_data()),
                        "label": label,
                        "service_run_id": self.service_run_id,
                        **({"scene": scene} if scene is not None else {}),
                    }
                ).encode()
                + b"\n"
            )
            self._send(process, payload, deadline)
            reply = self._read_reply(deadline, "viewer render deadline exceeded")
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

    def _send(self, process: subprocess.Popen[bytes], payload: bytes, deadline: float) -> None:
        assert process.stdin is not None
        os.set_blocking(process.stdin.fileno(), False)
        offset = 0
        while offset < len(payload):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [process.stdin], [], left)[1]:
                raise TimeoutError("viewer request delivery deadline exceeded")
            try:
                offset += os.write(process.stdin.fileno(), payload[offset:])
            except BrokenPipeError:
                raise RuntimeError("renderer bridge exited: " + self._bridge_error(deadline)) from None

    def _bridge_error(self, deadline: float) -> str:
        """The bridge reports why it stopped on stdout before exiting."""
        process = self.process
        if process is None or process.stdout is None:
            return "no bridge process"
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            self.buffer.extend(chunk)
        line = bytes(self.buffer).partition(b"\n")[0]
        try:
            return str(json.loads(line)["error"])
        except (ValueError, KeyError, TypeError):
            return "no error reported"

    def close(self) -> None:
        process, self.process = self.process, None
        self.buffer.clear()
        self.prepared = None
        if process is None:
            return
        # EOF lets the bridge leave readline and await browser.close(). Sending
        # SIGTERM to Playwright first keeps Node's input loop alive until the
        # forced-kill deadline, delaying every completed or stopped photo run.
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            import signal

            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # A hung render cannot finish the normal shutdown. Its entire
                # owned process group must exit before cleanup can succeed.
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
