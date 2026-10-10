"""Optional pack lifecycle and shared city feed without external asset dependencies."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim import assets
from aeroagentsim.authoring.demo import city_file, detailed_city
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.app import create_app
from tools.assets.build_pack import build_pack


def pack(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "scene.json").write_text(json.dumps({"buildings": [], "roads": {}}))
    archive = tmp_path / "city.zip"
    build_pack(source, archive)
    return archive


def test_build_install_discover_override_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "installed"))
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    assert not detailed_city()
    assert "lite-city" in str(city_file("scene.json"))
    installed = assets.install(pack(tmp_path))
    assert installed["format"] == assets.FORMAT
    assert all("sha256" not in row for row in installed["files"])
    assert detailed_city() and assets.selected() == installed
    assert city_file("scene.json") == Path(installed["directory"]) / "assets/scene.json"
    monkeypatch.setenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", str(tmp_path / "source"))
    assert city_file("scene.json") == tmp_path / "source/scene.json"
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT")
    assert assets.remove() == 1
    assert not detailed_city()


def test_fetch_resumes_and_not_published_is_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    content = pack(tmp_path).read_bytes()
    ranges: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/missing":
                self.send_error(404)
                return
            header = self.headers.get("Range")
            ranges.append(header)
            start = int(header.removeprefix("bytes=").removesuffix("-")) if header else 0
            self.send_response(206 if header else 200)
            if header:
                self.send_header("Content-Range", f"bytes {start}-{len(content)-1}/{len(content)}")
            self.send_header("Content-Length", str(len(content)-start))
            self.end_headers()
            self.wfile.write(content[start:])

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        cache = assets.data_dir() / ".downloads"
        cache.mkdir(parents=True)
        (cache / "url.txt").write_text(url)
        (cache / "huangpu-city-v1.zip.part").write_bytes(content[:80])
        assert assets.fetch(url)["id"] == "huangpu-city"
        assert ranges == ["bytes=80-"]
        with pytest.raises(RuntimeError, match="pack not published yet.*local file"):
            assets.fetch(url + "/missing")
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_city_feed_advertises_quality_and_installed_packs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "installed"))
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    prepared = TemplateRegistry().apply("parcel-delivery")
    assert type(prepared.knobs["wind_speed"]) is float
    document = prepared.document
    assert document["city"] == "huangpu"
    # This test checks the feed contract, not parcel physics.
    document.update(entities=[], engines={}, bindings={"exact": [], "lifecycle": []})
    document.pop("generators")
    base = Path("scenarios/templates/parcel-delivery")
    root = tmp_path / "runs"
    run_id = "run-" + "a" * 32
    with RunSession(load_scenario(document, base=base), root / run_id) as session:
        session.run_until(1)
    with TestClient(create_app(root, studio_root=tmp_path / "workspaces"), base_url="http://localhost") as client:
        lite = client.get(f"/v1/runs/{run_id}/header").json()
        assert lite["cityDetail"] == "lite" and lite["assetPacks"] == []
        assert "assets fetch" in lite["scene"]["hint"]
        assets.install(pack(tmp_path))
        detailed = client.get(f"/v1/runs/{run_id}/header").json()
        assert detailed["cityDetail"] == "detailed"
        assert detailed["assetPacks"][0]["id"] == "huangpu-city"
        assert "hint" not in detailed["scene"]
        assert client.get("/v1/studio/demo-assets/scene.json").status_code == 200


def _glb(path: Path, extras: dict[str, Any] | None = None) -> None:
    import struct

    document = {"asset": {"version": "2.0", "generator": "test", **({"extras": extras} if extras else {})}}
    body = json.dumps(document).encode()
    body += b" " * (-len(body) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 20 + len(body)) + struct.pack("<I", len(body)) + b"JSON" + body)


def _catalog_pack(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "scene.json").write_text(json.dumps({"buildings": [], "roads": {}}))
    models = tmp_path / "models"
    _glb(models / "a/car.glb")
    _glb(models / "a/drone.glb", {"source_archive": "drone.zip"})
    catalog = tmp_path / "catalog.json"
    row = {"forward": "+z", "up": "+y", "size_m": [4.0, 1.8, 1.5]}
    catalog.write_text(json.dumps({
        "sedan": {"kind": "road_vehicle", "source": "a/car.glb", "path": "models/vehicles/car.glb", **row},
        "x500": {"kind": "uav", "source": "a/drone.glb", "path": "models/uav/drone.glb",
                 "lod_source": "a/drone.glb", "lod": "models/uav/drone-lod.glb", **row},
    }))
    archive = tmp_path / "city11.zip"
    build_pack(source, archive, catalog=catalog, model_source=models)
    return archive


def test_v11_pack_carries_model_catalog_and_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import zipfile

    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "installed"))
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    assert assets.model_catalog() == {}
    archive = _catalog_pack(tmp_path)
    with zipfile.ZipFile(archive) as z:
        notice = z.read("NOTICE.txt").decode()
    assert "drone.zip" in notice and "to be stated by the project owner" in notice
    assets.install(archive)
    catalog = assets.model_catalog()
    assert catalog["sedan"]["url"] == "/v1/studio/demo-assets/models/vehicles/car.glb"
    assert catalog["x500"]["lodUrl"].endswith("models/uav/drone-lod.glb")
    assert catalog["x500"]["sizeM"] == [4.0, 1.8, 1.5]
    assert "source" not in assets.selected()["models"]["sedan"]


def test_v10_pack_without_catalog_still_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "installed"))
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    assets.install(pack(tmp_path))
    assert assets.model_catalog() == {}


def test_bad_catalog_is_rejected(tmp_path: Path) -> None:
    names = {"assets/m.glb"}
    good = {"kind": "uav", "path": "assets/m.glb", "size_m": [1, 1, 1], "forward": "+z", "up": "+y"}
    assets._model_catalog({"ok": good}, names)
    for bad in (
        {**good, "kind": "boat"}, {**good, "path": "assets/missing.glb"}, {**good, "size_m": [1, 0, 1]},
        {**good, "up": "+z"}, {**good, "forward": "z"}, {**good, "lod": "assets/nope.glb"},
    ):
        with pytest.raises(ValueError):
            assets._model_catalog({"x": bad}, names)
