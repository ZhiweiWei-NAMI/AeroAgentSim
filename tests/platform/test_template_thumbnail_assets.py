"""The console thumbnail server serves replay/model assets within their boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urljoin
from urllib.request import urlopen

import pytest

from tools.templates import thumbnails

# Allow the local server thread to be scheduled on heavily loaded test hosts.
SERVER_READ_TIMEOUT = 15


def test_thumbnail_camera_addons_and_path_boundary(
    tmp_path: Path, monkeypatch: Any
) -> None:
    modules = tmp_path / "node_modules"
    three = modules / "three/build/three.module.js"
    three.parent.mkdir(parents=True)
    three.write_text("export {};\n")
    root = modules / "three/examples/jsm"
    addon = root / "loaders/GLTFLoader.js"
    addon.parent.mkdir(parents=True)
    dependency = "../utils/BufferGeometryUtils.js"
    addon.write_text(
        f"import {{ helper }} from '{dependency}';\nexport class GLTFLoader {{}}\n"
    )
    utility = root / "utils/BufferGeometryUtils.js"
    utility.parent.mkdir()
    utility.write_text("export const helper = () => {};\n")
    secret = root.parent / "private.txt"
    secret.write_text("not a browser asset\n")
    sibling = root.with_name("jsm-private") / "private.txt"
    sibling.parent.mkdir()
    sibling.write_text("not an addon either\n")
    (root / "escaped.js").symlink_to(secret)
    (root / "escaped-directory").symlink_to(secret.parent, target_is_directory=True)
    bundle = tmp_path / "thumbnail-viewer.js"
    bundle.write_text("// bundled console Viewport\n")
    feed = {"header": {"runId": "pinned-run"}, "commits": []}
    city_root = tmp_path / "city"
    city_root.mkdir()
    model = city_root / "drone.glb"
    model.write_bytes(b"real model fixture")
    (city_root / "escaped.glb").symlink_to(secret)
    from aeroagentsim.authoring import demo

    monkeypatch.setattr(
        demo, "city_assets", lambda: (city_root, {"drone.glb": {}, "escaped.glb": {}})
    )
    monkeypatch.setattr(thumbnails, "node_modules_root", lambda: modules)
    monkeypatch.setattr(
        thumbnails, "browser_executable_path", lambda browser: tmp_path / "browser"
    )

    class Renderer:
        def __init__(self, url: str, **kwargs: Any) -> None:
            self.url = url

        def __enter__(self) -> Renderer:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def render(self, request: Any) -> Any:
            with urlopen(self.url, timeout=SERVER_READ_TIMEOUT) as response:
                assert response.headers["Content-Type"] == "text/html"
                assert b"/viewer.js" in response.read()
            with urlopen(
                self.url + "feed.json", timeout=SERVER_READ_TIMEOUT
            ) as response:
                assert json.load(response) == feed
            with urlopen(
                self.url + "v1/studio/demo-assets/drone.glb",
                timeout=SERVER_READ_TIMEOUT,
            ) as response:
                assert response.read() == model.read_bytes()
            loader_url = self.url + "addons/loaders/GLTFLoader.js?module=1"
            for url, asset in (
                (self.url + "viewer.js?module=1", bundle),
                (loader_url, addon),
                (urljoin(loader_url, dependency), utility),
            ):
                with urlopen(url, timeout=SERVER_READ_TIMEOUT) as response:
                    data = response.read()
                    assert response.headers["Content-Type"] == "text/javascript"
                    assert int(response.headers["Content-Length"]) == len(data)
                    assert data == asset.read_bytes()
            for path in (
                "addons/%2e%2e/private.txt",
                "addons/%2e%2e%2fprivate.txt",
                "addons/%2e%2e/jsm-private/private.txt",
                "addons/" + quote(str(secret), safe=""),
                "addons/escaped.js",
                "addons/escaped-directory/private.txt",
                "addons/loaders/",
                "addons/loaders/missing.js",
                "private.txt",
                "v1/studio/demo-assets/escaped.glb",
                "v1/studio/demo-assets/%2e%2e/private.txt",
                "v1/studio/demo-assets/missing.glb",
            ):
                with pytest.raises(HTTPError) as denied:
                    urlopen(self.url + path, timeout=SERVER_READ_TIMEOUT)
                with denied.value:
                    assert denied.value.code == 404, path
            return SimpleNamespace(png=b"rendered")

    monkeypatch.setattr(thumbnails, "BrowserRenderer", Renderer)
    assert (
        thumbnails.capture_png(SimpleNamespace(), None, feed=feed, bundle=bundle)
        == b"rendered"
    )
