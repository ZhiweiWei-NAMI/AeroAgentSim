"""Headless Three.js camera for an explicitly recorded traffic pose snapshot."""

from __future__ import annotations

import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aeroagentsim.observations.capture import Capture
from aeroagentsim.observations.contracts import content_digest
from aeroagentsim.observations.renderer import BrowserRenderer, Renderer
from aeroagentsim.platform.plugins import EngineBuild


class TrafficCameraCapture(Capture):
    """Reuse the capture owner's actual storage, validation and receipt contract."""

    server: ThreadingHTTPServer
    thread: threading.Thread

    def _renderer(self, config: Any) -> Renderer:
        if config["mode"] != "traffic_browser":
            return super()._renderer(config)
        if set(config) != {
            "mode",
            "node_modules",
            "timeout_s",
            "asset_digest",
            "browser_executable",
        }:
            raise ValueError(
                "traffic camera: explicit browser assets/deadline required"
            )
        executable = Path(config["browser_executable"])
        if not executable.is_file():
            raise FileNotFoundError(
                f"traffic camera: configured browser does not exist: {executable}"
            )
        modules = Path(config["node_modules"])
        html = Path(__file__).with_name("camera.html").read_bytes()
        three = (modules / "three/build/three.module.js").read_bytes()
        if content_digest(html + three) != config["asset_digest"]:
            raise ValueError("traffic camera: pinned render asset digest mismatch")
        assets = {"/": (html, "text/html"), "/three.js": (three, "text/javascript")}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in assets:
                    self.send_error(404)
                    return
                data, mime = assets[self.path]
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return BrowserRenderer(
            f"http://127.0.0.1:{self.server.server_port}/",
            node_modules=modules,
            timeout_s=config["timeout_s"],
            browser_executable=Path(config["browser_executable"]),
        )

    def close(self) -> None:
        try:
            super().close()
        finally:
            if hasattr(self, "server"):
                self.server.shutdown()
                self.server.server_close()
                self.thread.join()


def build(context: EngineBuild) -> TrafficCameraCapture:
    if context.config["run_directory"] == "@run":
        if context.run_directory is None:
            raise ValueError("traffic capture requires a RunSession artifact directory")
        context = replace(
            context,
            config={**context.config, "run_directory": str(context.run_directory)},
        )
    return TrafficCameraCapture(context)
