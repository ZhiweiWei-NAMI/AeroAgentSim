"""Optional city download packs, stored outside the source checkout."""
from __future__ import annotations

import hashlib
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
VERSION = "1.1.0"
DEFAULT_URL = "https://github.com/ZhiweiWei-NAMI/AeroAgentSim/releases/download/assets-huangpu-city-v1.1/huangpu-city-v1.1.zip"
# No published archive digest is pinned in this checkout's release metadata.
DEFAULT_SHA256: str | None = "044ef338f893b29839c8aba326826cf295a293d685d49be61de939caea0850ff"  # GitHub Release asset digest, huangpu-city-v1.1.zip (129,165,236 bytes)


def localized(en: str, zh: str) -> str:
    return f"{en} / {zh}"


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", value):
        raise ValueError(localized("asset pack: expected a SHA-256 digest (64 hex digits)", "资产包：SHA-256 摘要须为 64 位十六进制字符"))
    return value.removeprefix("sha256:").lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


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


MODEL_KINDS = frozenset({"road_vehicle", "bicycle", "pedestrian", "uav", "furniture"})
_AXIS = re.compile(r"[+-][xyz]")


def _model_catalog(value: Any, names: set[str]) -> dict[str, dict[str, Any]]:
    """Validate the optional v1.1 ``models`` catalog; absent catalogs are legal (v1.0 packs)."""
    if not isinstance(value, dict) or not value:
        raise ValueError("asset pack: models catalog must be a nonempty object")
    for ident, row in value.items():
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", ident) or not isinstance(row, dict):
            raise ValueError("asset pack: invalid model entry")
        if row.get("kind") not in MODEL_KINDS:
            raise ValueError(f"asset pack: model {ident} has an unknown kind")
        if "path" not in row:
            raise ValueError(f"asset pack: model {ident} has no path")
        for key in ("path", "lod"):
            if key in row and _relative(row[key]) not in names:
                raise ValueError(f"asset pack: model {ident} {key} is not a pack file")
        size = row.get("size_m")
        if not (isinstance(size, list) and len(size) == 3
                and all(type(n) in (int, float) and 0 < n < 1000 for n in size)):
            raise ValueError(f"asset pack: model {ident} needs size_m [length, width, height] in metres")
        for key in ("forward", "up"):
            if not (isinstance(row.get(key), str) and _AXIS.fullmatch(row[key])):
                raise ValueError(f"asset pack: model {ident} needs {key} as one of +x -x +y -y +z -z")
        if row["forward"][1] == row["up"][1]:
            raise ValueError(f"asset pack: model {ident} forward and up must use different axes")
    return dict(value)


def read_pack(root: Path, *, check_files: bool = True) -> dict[str, Any]:
    if root.is_symlink() or (root / "pack.json").is_symlink():
        raise ValueError(localized("asset pack: symlinks are unsupported", "资产包：不支持符号链接"))
    pack: Any = json.loads((root / "pack.json").read_text())
    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        raise ValueError("asset pack: expected aeroagentsim.asset-pack/v1")
    if not isinstance(pack.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pack["id"]):
        raise ValueError("asset pack: invalid id")
    if not isinstance(pack.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", pack["version"]):
        raise ValueError("asset pack: expected numeric major.minor.patch version")
    major, minor, _ = (int(part) for part in pack["version"].split("."))
    if pack["id"] == PACK_ID and (major != 1 or minor > 1):
        raise ValueError(localized(f"asset pack: unsupported {PACK_ID} version {pack['version']}; supported >=1.0.0,<1.2.0", "资产包：黄浦素材版本不兼容，支持范围为 >=1.0.0,<1.2.0"))
    if not isinstance(pack.get("files"), list) or not pack["files"]:
        raise ValueError("asset pack: file list required")
    names: set[str] = set()
    for row in pack["files"]:
        if not isinstance(row, dict) or "path" not in row or "bytes" not in row:
            raise ValueError(localized("asset pack: each file needs path and bytes", "资产包：每个文件必须声明路径和字节数"))
        name = _relative(row["path"])
        if name in names:
            raise ValueError("asset pack: duplicate file path")
        names.add(name)
        if type(row["bytes"]) is not int or row["bytes"] < 0:
            raise ValueError("asset pack: nonnegative file byte size required")
        expected_digest = _digest(row["sha256"]) if "sha256" in row else None
        if check_files:
            file = root / name
            if not file.resolve().is_relative_to(root.resolve()) or file.is_symlink():
                raise ValueError("asset pack: file escapes pack")
            if not file.is_file():
                raise FileNotFoundError(f"asset pack: missing {name}")
            actual_size = file.stat().st_size
            if actual_size != row["bytes"]:
                raise ValueError(localized(f"asset pack: {name} byte size mismatch; expected {row['bytes']}, actual {actual_size}", f"资产包：{name} 字节数不符；预期 {row['bytes']}，实际 {actual_size}"))
            if expected_digest is not None:
                actual_digest = _sha256(file)
                if actual_digest != expected_digest:
                    raise ValueError(localized(f"asset pack: {name} SHA-256 mismatch; expected {expected_digest}, actual {actual_digest}", f"资产包：{name} SHA-256 不符；预期 {expected_digest}，实际 {actual_digest}"))
    if "assets/scene.json" not in names or "NOTICE.txt" not in names:
        raise ValueError("asset pack: scene and NOTICE.txt required")
    if "models" in pack:
        _model_catalog(pack["models"], names)
    return dict(pack)


@lru_cache(maxsize=16)
def _manifest(root: Path, identity: tuple[int, int, int, int, int]) -> dict[str, Any]:
    return read_pack(root, check_files=False)


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.lstat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=16)
def _catalog(root: Path, identity: tuple[Any, ...]) -> dict[str, Any]:
    return read_pack(root)


def installed() -> list[dict[str, Any]]:
    root = data_dir()
    if not root.is_dir():
        return []
    packs = []
    for path in sorted(root.glob("*/*/pack.json")):
        if path.parent.parent.name.startswith("."):
            continue
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
            raise ValueError(localized("asset pack: installed path is a symlink", "资产包：安装路径是符号链接"))
        manifest_identity = _identity(path)
        manifest = _manifest(path.parent, manifest_identity)
        # Rehash only after real files change; a warm catalog still checks stats.
        identity = (manifest_identity, *(_identity(path.parent / row["path"]) for row in manifest["files"]))
        pack = _catalog(path.parent, identity)
        if (path.parent.name, path.parent.parent.name) != (pack["version"], pack["id"]):
            raise ValueError(localized("asset pack: installed directory differs from manifest identity", "资产包：安装目录与清单标识不一致"))
        receipt = path.parent / "installation.json"
        source = json.loads(receipt.read_text())["source"] if receipt.exists() else pack.get("source")
        packs.append({**pack, "directory": str(path.parent), "source": source})
    return packs


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


def model_catalog() -> dict[str, dict[str, Any]]:
    """Viewer-facing display-model catalog of the selected pack ({} for v1.0 packs or no pack).

    URLs are service-relative (``/v1/studio/demo-assets/...``); the same route serves the scene.
    """
    pack = None if os.environ.get("AEROAGENTSIM_TRAFFIC_ASSET_ROOT") else selected()
    if not pack or "models" not in pack:
        return {}
    url = "/v1/studio/demo-assets/"
    return {
        ident: {
            "url": url + row["path"].removeprefix("assets/"),
            "kind": row["kind"], "label": row.get("label", ident),
            "sizeM": row["size_m"], "forward": row["forward"], "up": row["up"],
            **({"lodUrl": url + row["lod"].removeprefix("assets/")} if "lod" in row else {}),
            **({"skinned": True} if row.get("skinned") else {}),
        }
        for ident, row in pack["models"].items()
    }


def install(source: Path, *, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
        staging = Path(temporary) / "pack"
        staging.mkdir()
        if source.is_dir():
            if source.is_symlink():
                raise ValueError(localized("asset pack: symlinks are unsupported", "资产包：不支持符号链接"))
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
        provenance = provenance if provenance is not None else {
            "kind": "directory" if source.is_dir() else "archive", "path": str(source.resolve()),
            "archive_sha256": None if source.is_dir() else _sha256(source),
            "expected_archive_sha256": None, "digest_verified": False,
        }
        (staging / "installation.json").write_text(json.dumps({"source": provenance}, ensure_ascii=False))
        destination = root / pack["id"] / pack["version"]
        if destination.exists():
            raise FileExistsError(f"Pack already installed: {pack['id']} {pack['version']}; remove it first")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    return {**pack, "directory": str(destination), "source": provenance}


def remove(identifier: str = PACK_ID, version: str | None = None) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identifier) or (
        version is not None and not re.fullmatch(r"\d+\.\d+\.\d+", version)
    ):
        raise ValueError(localized("asset pack: invalid removal id/version", "资产包：移除标识或版本无效"))
    root = data_dir()
    # Removal must remain available even when the installed files are corrupt.
    matches = [path for path in (root / identifier).glob("*")
               if re.fullmatch(r"\d+\.\d+\.\d+", path.name) and path.is_dir()
               and (version is None or path.name == version)]
    if not matches:
        raise FileNotFoundError(f"Asset pack is not installed: {identifier}")
    for path in matches:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(localized("asset pack: removal path escapes installed packs", "资产包：移除路径超出安装目录"))
        shutil.rmtree(path)
    return len(matches)


def fetch(url: str | None = None, *, insecure_skip_digest: bool = False) -> dict[str, Any]:
    url = url or os.environ.get("AEROAGENTSIM_ASSET_PACK_URL", DEFAULT_URL)
    pin = os.environ.get("AEROAGENTSIM_ASSET_PACK_SHA256")
    if pin is None and url == DEFAULT_URL:
        pin = DEFAULT_SHA256
    expected_digest = _digest(pin) if pin is not None else None
    skip = insecure_skip_digest or os.environ.get("AEROAGENTSIM_ASSET_PACK_INSECURE_SKIP_DIGEST", "").lower() in {"1", "true", "yes"}
    if expected_digest is None and not skip:
        raise RuntimeError(localized("Asset archive SHA-256 is not pinned; set AEROAGENTSIM_ASSET_PACK_SHA256 to the published digest, install a local file with aeroagentsim assets install <zip-or-dir>, or explicitly use --insecure-skip-digest", "资产归档尚未固定 SHA-256；请设置发布摘要环境变量、从本地文件安装，或明确使用 --insecure-skip-digest"))
    cache = data_dir() / ".downloads"
    cache.mkdir(parents=True, exist_ok=True)
    # Resume only bytes from the same URL. This metadata is not a content pin.
    partial, origin = cache / "huangpu-city-v1.zip.part", cache / "url.txt"
    def verified_install() -> dict[str, Any]:
        actual_digest = _sha256(partial)
        if not skip and actual_digest != expected_digest:
            raise RuntimeError(localized(f"Asset archive SHA-256 mismatch; expected {expected_digest}, actual {actual_digest}; retained {partial}. Check the published digest; remove this partial to download again, or install a trusted local file: aeroagentsim assets install <zip-or-dir>", f"资产归档 SHA-256 不符；预期 {expected_digest}，实际 {actual_digest}；已保留下载文件。请核实发布摘要，删除分片重新下载，或安装可信本地文件。"))
        if skip:
            print(localized("Archive digest verification explicitly skipped", "已明确跳过归档摘要校验"), file=sys.stderr)
        # Keep the partial until both archive and manifest validation succeed.
        result = install(partial, provenance={"kind": "url", "url": url,
                         "archive_sha256": actual_digest, "expected_archive_sha256": expected_digest,
                         "digest_verified": not skip})
        return result
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
            return verified_install()
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
    return verified_install()
