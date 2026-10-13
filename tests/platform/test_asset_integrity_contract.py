"""Archive pins, actual manifest files and installation provenance are verified."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim import assets
from aeroagentsim.services.app import create_app


@pytest.fixture(autouse=True)
def isolated_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "installed"))
    for variable in ("AEROAGENTSIM_ASSET_PACK_SHA256", "AEROAGENTSIM_ASSET_PACK_URL",
                     "AEROAGENTSIM_ASSET_PACK_INSECURE_SKIP_DIGEST", "AEROAGENTSIM_TRAFFIC_ASSET_ROOT"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(assets, "DEFAULT_SHA256", None)


def pack_source(tmp_path: Path, *, digests: bool = True) -> Path:
    source = tmp_path / "source"
    (source / "assets").mkdir(parents=True)
    files = {"assets/scene.json": b'{"buildings":[],"roads":{}}', "NOTICE.txt": b"Authored test asset notice\n"}
    rows = []
    for name, content in files.items():
        (source / name).write_bytes(content)
        rows.append({"path": name, "bytes": len(content), **({"sha256": hashlib.sha256(content).hexdigest()} if digests else {})})
    (source / "pack.json").write_text(json.dumps({"format": assets.FORMAT, "id": assets.PACK_ID,
                                                "version": "1.1.0", "files": rows}))
    return source


def zip_source(source: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.writestr(path.relative_to(source).as_posix(), path.read_bytes())
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, content: bytes, status: int = 200, offset: int = 0) -> None:
        super().__init__(content[offset:] if status == 206 else content)
        self.status = status
        self.headers = {"Content-Length": str(len(content) - offset if status == 206 else len(content))}
        if status == 206:
            self.headers["Content-Range"] = f"bytes {offset}-{len(content)-1}/{len(content)}"


@pytest.mark.parametrize("as_zip", [False, True])
@pytest.mark.parametrize("corruption", ["size", "digest", "missing", "invalid_digest"])
def test_invalid_files_never_register_a_pack(tmp_path: Path, as_zip: bool, corruption: str) -> None:
    source = pack_source(tmp_path)
    path = source / "pack.json"
    manifest = json.loads(path.read_text())
    if corruption == "size":
        manifest["files"][0]["bytes"] += 1
    elif corruption == "digest":
        manifest["files"][0]["sha256"] = "0" * 64
    elif corruption == "invalid_digest":
        manifest["files"][0]["sha256"] = "not-a-digest"
    else:
        (source / "assets/scene.json").unlink()
    path.write_text(json.dumps(manifest))
    if as_zip:
        archive = tmp_path / "pack.zip"
        archive.write_bytes(zip_source(source))
        source = archive
    with pytest.raises((ValueError, FileNotFoundError)):
        assets.install(source)
    assert assets.installed() == []
    assert assets.selected() is None
    assert not list(assets.data_dir().glob(".install-*"))


@pytest.mark.parametrize("with_digest", [False, True])
def test_changed_installed_files_invalidate_warm_catalog(tmp_path: Path, with_digest: bool) -> None:
    pack = assets.install(pack_source(tmp_path, digests=with_digest))
    assert assets.selected() == pack
    file = Path(pack["directory"]) / "assets/scene.json"
    original = file.read_bytes()
    file.write_bytes(b"x" * len(original) if with_digest else original + b" ")
    with pytest.raises(ValueError, match="mismatch"):
        assets.selected()
    assert assets.remove() == 1
    assert assets.installed() == []


@pytest.mark.parametrize("version", ["2.0.0", "1.2.0"])
def test_unsupported_city_version_cannot_be_selected(tmp_path: Path, version: str) -> None:
    source = pack_source(tmp_path)
    manifest = json.loads((source / "pack.json").read_text())
    manifest["version"] = version
    (source / "pack.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsupported"):
        assets.install(source)
    assert assets.installed() == []


def test_fetch_requires_digest_before_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unpinned fetch attempted network access")
    monkeypatch.setattr(assets.urllib.request, "urlopen", forbidden)
    with pytest.raises(RuntimeError, match="not pinned"):
        assets.fetch()
    assert assets.installed() == []
    monkeypatch.setenv("AEROAGENTSIM_ASSET_PACK_SHA256", "invalid")
    with pytest.raises(ValueError, match="64 hex"):
        assets.fetch()


@pytest.mark.parametrize("resume", ["none", "206", "200", "416"])
def test_fetch_verifies_every_download_and_resume_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: str
) -> None:
    content = zip_source(pack_source(tmp_path))
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setenv("AEROAGENTSIM_ASSET_PACK_SHA256", "sha256:" + digest)
    url = "https://assets.test/pack.zip"
    cache = assets.data_dir() / ".downloads"
    offset = len(content) if resume == "416" else 80
    if resume != "none":
        cache.mkdir(parents=True)
        (cache / "url.txt").write_text(url)
        (cache / "huangpu-city-v1.zip.part").write_bytes(content[:offset])
    requests = []

    def download(request: Any, **kwargs: Any) -> Response:
        requests.append(request)
        if resume == "416":
            raise urllib.error.HTTPError(url, 416, "complete", {"Content-Range": f"bytes */{offset}"}, None)
        return Response(content, 206 if resume == "206" else 200, offset if resume == "206" else 0)

    monkeypatch.setattr(assets.urllib.request, "urlopen", download)
    result = assets.fetch(url)
    assert result["source"]["digest_verified"] is True
    assert result["source"]["archive_sha256"] == digest
    assert result["source"]["url"] == url
    assert requests[0].get_header("Range") == (f"bytes={offset}-" if resume != "none" else None)
    assert assets.installed()[0]["source"] == result["source"]


@pytest.mark.parametrize("resume", [False, True])
def test_archive_digest_failure_retains_partial_without_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    content = zip_source(pack_source(tmp_path))
    monkeypatch.setenv("AEROAGENTSIM_ASSET_PACK_SHA256", "0" * 64)
    url = "https://assets.test/pack.zip"
    cache = assets.data_dir() / ".downloads"
    if resume:
        cache.mkdir(parents=True)
        (cache / "url.txt").write_text(url)
        (cache / "huangpu-city-v1.zip.part").write_bytes(content)

    def download(request: Any, **kwargs: Any) -> Response:
        if resume:
            raise urllib.error.HTTPError(url, 416, "complete", {"Content-Range": f"bytes */{len(content)}"}, None)
        return Response(content)

    monkeypatch.setattr(assets.urllib.request, "urlopen", download)
    with pytest.raises(RuntimeError, match="expected .*actual .*retained"):
        assets.fetch(url)
    assert (cache / "huangpu-city-v1.zip.part").read_bytes() == content
    assert assets.installed() == []


@pytest.mark.parametrize("environment", [False, True])
def test_explicit_insecure_choice_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: bool
) -> None:
    content = zip_source(pack_source(tmp_path))
    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *args, **kwargs: Response(content))
    if environment:
        monkeypatch.setenv("AEROAGENTSIM_ASSET_PACK_INSECURE_SKIP_DIGEST", "true")
    pack = assets.fetch("https://assets.test/pack.zip", insecure_skip_digest=not environment)
    assert pack["source"]["digest_verified"] is False
    assert pack["source"]["expected_archive_sha256"] is None


def test_default_release_pin_and_environment_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = zip_source(pack_source(tmp_path))
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(assets, "DEFAULT_SHA256", "0" * 64)
    monkeypatch.setenv("AEROAGENTSIM_ASSET_PACK_SHA256", digest)
    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *args, **kwargs: Response(content))
    pack = assets.fetch()
    assert pack["source"]["expected_archive_sha256"] == digest
    assert pack["source"]["digest_verified"] is True


def test_verified_archive_with_bad_manifest_retains_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = pack_source(tmp_path)
    (source / "assets/scene.json").write_bytes(b"wrong size")
    content = zip_source(source)
    monkeypatch.setenv("AEROAGENTSIM_ASSET_PACK_SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *args, **kwargs: Response(content))
    with pytest.raises(ValueError, match="byte size mismatch"):
        assets.fetch("https://assets.test/pack.zip")
    assert (assets.data_dir() / ".downloads/huangpu-city-v1.zip.part").read_bytes() == content
    assert assets.installed() == []


def test_pack_inventory_exposes_real_origin_and_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = pack_source(tmp_path)
    pack = assets.install(source)
    with TestClient(create_app(tmp_path / "runs"), base_url="http://localhost") as client:
        response = client.get("/v1/assets/packs")
        assert response.status_code == 200
        row = response.json()[0]
        assert row["id"] == assets.PACK_ID and row["version"] == "1.1.0"
        assert row["source"]["path"] == str(source.resolve())
        assert row["source"]["digest_verified"] is False
        (Path(pack["directory"]) / "assets/scene.json").write_bytes(b"broken")
        response = client.get("/v1/assets/packs")
        assert response.status_code == 503 and "mismatch" in response.json()["detail"]
