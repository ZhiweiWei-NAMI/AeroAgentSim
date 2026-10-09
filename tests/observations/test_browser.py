"""Actual WebGL pixels from one persistent Chromium; fixtures use real WAL cuts."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from aerokernel import CommandRequest, Instant

from aeroagentsim.observations.contracts import CaptureRequest, content_digest
from aeroagentsim.observations.png import validate_png
from aeroagentsim.observations.renderer import BrowserRenderer
from aeroagentsim.services.projector import project
from tests.observations.test_capture import FixtureRenderer, png, setup_capture


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def browser_scene(tmp_path: Path) -> Iterator[tuple[str, Path, CaptureRequest]]:
    modules = Path("frontend/node_modules").resolve()
    if not (modules / "playwright/index.mjs").exists():
        pytest.skip(
            "Node Playwright unavailable; browser gate requires explicit installation"
        )
    kernel, _, request = setup_capture(tmp_path / "run", FixtureRenderer(png()))
    kernel.run_until(2**53 + 1)
    html = Path(__file__).with_name("fixtures") / "viewer.html"
    build = tmp_path / "viewer-build"
    build.mkdir()
    (build / "index.html").write_bytes(html.read_bytes())
    three = modules / "three/build/three.module.js"
    (build / "three.js").write_bytes(three.read_bytes())
    asset_digest = content_digest(html.read_bytes() + three.read_bytes())
    request = replace(
        request,
        width=128,
        height=96,
        timeout_s=20.0,
        asset_digest=asset_digest,
        camera={
            "revision": "fixture/v1",
            "fov": 55.0,
            "eye": [8.0, 7.0, 8.0],
            "target": [3.0, 2.0, -1.0],
        },
    )
    (build / "scene.json").write_text(
        json.dumps(
            {
                "run_id": request.run_id,
                "actor": request.actor.to_data(),
                "asset_digest": asset_digest,
            }
        )
    )
    (build / "feed.json").write_text(
        json.dumps([project(r) for r in kernel.records[1:]])
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(build))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", modules, request
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        kernel.close()


@pytest.mark.browser
def test_browser_png_one_instance_exact_scene_and_camera(
    browser_scene: tuple[str, Path, CaptureRequest], tmp_path: Path
) -> None:
    url, modules, request = browser_scene
    with BrowserRenderer(
        url,
        node_modules=modules,
        timeout_s=25.0,
        browser_executable=Path(os.environ["AEROAGENTSIM_CHROMIUM"])
        if "AEROAGENTSIM_CHROMIUM" in os.environ
        else None,
    ) as renderer:
        first = renderer.render(request)
        process = renderer.process
        assert first.mode == "browser"
        assert validate_png(first.png) == (128, 96)
        other = replace(request, camera={**request.camera, "eye": [-7.0, 4.0, -6.0]})
        second = renderer.render(other, label="source=0ns speed=2x")
        assert renderer.process is process
        assert first.png != second.png
        assert len(first.png) > 500  # Real geometric coverage, not a flat stub PNG.
        (tmp_path / "browser-capture.png").write_bytes(first.png)
    assert renderer.process is None


@pytest.mark.browser
def test_browser_wrong_cut_assets_and_timeout(
    browser_scene: tuple[str, Path, CaptureRequest],
) -> None:
    url, modules, request = browser_scene
    renderer = BrowserRenderer(
        url,
        node_modules=modules,
        timeout_s=25.0,
        browser_executable=Path(os.environ["AEROAGENTSIM_CHROMIUM"])
        if "AEROAGENTSIM_CHROMIUM" in os.environ
        else None,
    )
    wrong = replace(request, source_cut=replace(request.source_cut, index=999))
    with pytest.raises(RuntimeError, match="wrong cut"):
        renderer.render(wrong)
    assert renderer.process is None
    with pytest.raises(RuntimeError, match="assets"):
        renderer.render(replace(request, asset_digest="0" * 64))
    renderer.render(request)  # Warm the real browser before measuring render deadline.
    with pytest.raises(TimeoutError, match="deadline"):
        renderer.render(
            replace(
                request, timeout_s=0.2, camera={**request.camera, "test_delay_ms": 2000}
            )
        )
    assert renderer.process is None


@pytest.mark.browser
def test_browser_plugin_actual_png_record(
    browser_scene: tuple[str, Path, CaptureRequest], tmp_path: Path
) -> None:
    url, modules, request = browser_scene
    renderer = BrowserRenderer(
        url,
        node_modules=modules,
        timeout_s=25.0,
        browser_executable=Path(os.environ["AEROAGENTSIM_CHROMIUM"])
        if "AEROAGENTSIM_CHROMIUM" in os.environ
        else None,
    )
    kernel, capture, local_request = setup_capture(tmp_path / "actual", renderer)
    # Both kernels execute the same authored bootstrap; source prefix coordinates match.
    request = replace(request, source_cut=local_request.source_cut)
    command = kernel.submit(
        CommandRequest(
            "capture.request", "capture", Instant(1), request.to_command_data()
        )
    )
    kernel.run_until(1)
    assert kernel.view().action(command).status == "succeeded"
    record = capture.store.get(request.request_id)
    assert record["renderer_mode"] == "browser"
    assert record["camera_digest"] == request.camera_digest
    assert record["request"]["source_cut"] == request.to_data()["source_cut"]
    assert validate_png(capture.store.read(record["digest"])) == (128, 96)
    kernel.close()


@pytest.mark.browser
def test_browser_large_exact_source_time(
    browser_scene: tuple[str, Path, CaptureRequest],
) -> None:
    url, modules, request = browser_scene
    import urllib.request

    from aerokernel import Cut, Stamp

    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        url + "/feed.json"
    ) as response:
        final = json.load(response)[-1]
    request = replace(
        request,
        source_cut=Cut(
            final["commitIndex"],
            Instant(int(final["at"]["ns"]), final["at"]["microstep"]),
        ),
        acquired=Stamp("canonical", int(final["at"]["ns"]), 1, "canonical"),
    )
    assert request.source_cut.instant.ns == 2**53 + 1
    with BrowserRenderer(
        url,
        node_modules=modules,
        timeout_s=25.0,
        browser_executable=Path(os.environ["AEROAGENTSIM_CHROMIUM"])
        if "AEROAGENTSIM_CHROMIUM" in os.environ
        else None,
    ) as renderer:
        assert validate_png(renderer.render(request).png) == (128, 96)
