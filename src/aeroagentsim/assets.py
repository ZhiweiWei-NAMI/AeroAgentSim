"""Optional city download packs, stored outside the source checkout."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = "aeroagentsim.asset-pack/v1"
PACK_ID = "huangpu-city"
VERSION = "1.0.0"
DEFAULT_URL = "https://github.com/ZhiweiWei-NAMI/AeroAgentSim/releases/download/assets-huangpu-city-v1/huangpu-city-v1.zip"


def data_dir() -> Path:
    value = os.environ.get("AEROAGENTSIM_ASSETS_DIR")
    return Path(value).expanduser().resolve() if value else Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    ).expanduser().resolve() / "aeroagentsim/assets"


def _relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("asset pack: invalid file path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"..", "."} for part in value.split("/")):
        raise ValueError("asset pack: file path escapes pack")
    return value


def read_pack(root: Path, *, check_files: bool = True) -> dict[str, Any]:
    pack: Any = json.loads((root / "pack.json").read_text())
    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        raise ValueError("asset pack: expected aeroagentsim.asset-pack/v1")
    if not isinstance(pack.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pack["id"]):
        raise ValueError("asset pack: invalid id")
    if not isinstance(pack.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", pack["version"]):
        raise ValueError("asset pack: expected numeric major.minor.patch version")
    if not isinstance(pack.get("files"), list) or not pack["files"]:
        raise ValueError("asset pack: file list required")
    names: set[str] = set()
    for row in pack["files"]:
        name = _relative(row["path"])
        if name in names:
            raise ValueError("asset pack: duplicate file path")
        names.add(name)
        if type(row["bytes"]) is not int or row["bytes"] < 0:
            raise ValueError("asset pack: nonnegative file byte size required")
        if check_files:
            file = root / name
            if not file.resolve().is_relative_to(root.resolve()) or file.is_symlink():
                raise ValueError("asset pack: file escapes pack")
            if not file.is_file():
                raise FileNotFoundError(f"asset pack: missing {name}")
    if "assets/scene.json" not in names or "NOTICE.txt" not in names:
        raise ValueError("asset pack: scene and NOTICE.txt required")
    return dict(pack)


@lru_cache(maxsize=16)
def _catalog(root: Path, modified_ns: int) -> dict[str, Any]:
    return read_pack(root, check_files=False)


def installed() -> list[dict[str, Any]]:
    root = data_dir()
    if not root.is_dir():
        return []
    return [{**_catalog(p.parent, p.stat().st_mtime_ns), "directory": str(p.parent)}
            for p in sorted(root.glob("*/*/pack.json"))
            if not p.parent.parent.name.startswith(".")]


def selected() -> dict[str, Any] | None:
    packs = [p for p in installed() if p["id"] == PACK_ID]
    return max(packs, key=lambda p: tuple(int(n) for n in p["version"].split("."))) if packs else None


def traffic_root() -> Path | None:
    override = os.environ.get("AEROAGENTSIM_TRAFFIC_ASSET_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if not (root / "scene.json").is_file():
            raise FileNotFoundError(f"Traffic asset override has no scene.json: {root}")
        return root
    pack = selected()
    return Path(pack["directory"]) / "assets" if pack else None


def install(source: Path) -> dict[str, Any]:
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
        staging = Path(temporary) / "pack"
        staging.mkdir()
        if source.is_dir():
            for path in source.rglob("*"):
                if path.is_symlink():
                    raise ValueError("asset pack: symlinks are unsupported")
            shutil.copytree(source, staging, dirs_exist_ok=True)
        else:
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    _relative(info.filename.rstrip("/"))
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("asset pack: symlinks are unsupported")
                archive.extractall(staging)
        pack = read_pack(staging)
        destination = root / pack["id"] / pack["version"]
        if destination.exists():
            raise FileExistsError(f"Pack already installed: {pack['id']} {pack['version']}; remove it first")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    return {**pack, "directory": str(destination)}


def remove(identifier: str = PACK_ID, version: str | None = None) -> int:
    matches = [p for p in installed() if p["id"] == identifier and (version is None or p["version"] == version)]
    if not matches:
        raise FileNotFoundError(f"Asset pack is not installed: {identifier}")
    for pack in matches:
        shutil.rmtree(pack["directory"])
    return len(matches)


def fetch(url: str | None = None) -> dict[str, Any]:
    url = url or os.environ.get("AEROAGENTSIM_ASSET_PACK_URL", DEFAULT_URL)
    cache = data_dir() / ".downloads"
    cache.mkdir(parents=True, exist_ok=True)
    # Resume only bytes from the same URL. This metadata is not a content pin.
    partial, origin = cache / "huangpu-city-v1.zip.part", cache / "url.txt"
    if origin.exists() and origin.read_text() != url:
        partial.unlink(missing_ok=True)
    origin.write_text(url)
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and exc.headers.get("Content-Range") == f"bytes */{offset}":
            # All bytes arrived before the previous process was interrupted.
            archive = cache / "huangpu-city-v1.zip"
            partial.replace(archive)
            return install(archive)
        if exc.code == 404:
            raise RuntimeError("pack not published yet — install from a local file: aeroagentsim assets install <zip-or-dir>") from exc
        raise RuntimeError(f"Asset download HTTP {exc.code}: {url}; rerun fetch to resume") from exc
    with response:
        resumed = response.status == 206
        if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise RuntimeError("Asset download returned an unexpected resume offset")
        received = offset if resumed else 0
        length = response.headers.get("Content-Length")
        total = received + int(length) if length else None
        with partial.open("ab" if resumed else "wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
                received += len(block)
                print(f"\rDownloading city: {received / 1048576:.1f} MiB" + (f" / {total / 1048576:.1f} MiB" if total else ""), end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)
        if total is not None and received != total:
            raise RuntimeError("Asset download interrupted; rerun fetch to resume")
    archive = cache / "huangpu-city-v1.zip"
    partial.replace(archive)
    return install(archive)
