"""Asynchronous, immutable publication of an official OSM2World authoring pack.

This is an authoring artifact, never a formal Run or a public execution trace.
Only a registered OSM source and the strict SceneSelection reach the builder.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.scene_compiler import SceneCompilationError

from .compiler import compile_scene_selection
from .publication_evidence import evidence_tree_sha256, verify_selected_presentation
from .selection import (
    SceneSelection,
    SceneSelectionError,
    SceneSourceRegistry,
    VerifiedSceneSource,
    default_scene_source_registry,
)


JOB_SCHEMA = "aero-bench.scene-build-job/v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_OBJECT_ID = re.compile(r"[nwr]-?[0-9]+\Z")
_TARGET_ID = re.compile(r"[a-zA-Z0-9_.:-]{1,256}\Z")
_TEXTURE = re.compile(r"/osm2world/style/textures/[a-zA-Z0-9_./-]+\Z")
_STYLE_PREFIX = "/osm2world/style/textures/"
_MAX_CHUNK = 64 * 1024 * 1024
_IDENTITY_FILES = (
    "aero_bench/authoring/selection.py",
    "aero_bench/authoring/compiler.py",
    "aero_bench/authoring/publication.py",
    "aero_bench/authoring/publication_evidence.py",
    "aero_bench/authoring/api.py",
    "aero_bench/world/scene_compiler.py",
    "aero_bench/world/frame_math.py",
    "aero_bench/world/ground_roads.py",
    "aero_bench/serialization.py",
    "frontend/public/osm2world/osm2world-core-web.mjs",
    "frontend/public/osm2world/runtime-provenance.json",
    "tools/osm2world/web-integration.patch",
    "frontend/scripts/build-osm2world-pack.mjs",
    "frontend/scripts/authoring-pack-vite.config.mjs",
    "frontend/src/osm2world/runtime.ts",
    "frontend/src/osm2world/source.ts",
    "frontend/src/osm2world/projection.ts",
    "frontend/src/osm2world/mesh.ts",
    "frontend/src/osm2world/pack.ts",
    "frontend/package-lock.json",
    "aero_bench/authoring/urban_network.py",
    "aero_bench/authoring/presentation.py",
    "frontend/scripts/build-urban-ground-network.py",
    "frontend/scripts/build-selected-static-roads.py",
    "frontend/scripts/build-city-road-preview.py",
    "frontend/scripts/audit-city-road-preview.py",
    "frontend/scripts/audit-city-road-triangulation.mjs",
    "frontend/scripts/build-city-building-placements.py",
)


class PublicationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError) as exc:
        raise PublicationError("invalid_pack", f"{label} 不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise PublicationError("invalid_pack", f"{label} 必须是 JSON 对象")
    return value


def _file_ref(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {"sha256": _digest(raw), "size_bytes": len(raw)}


def _matches_ref(path: Path, ref: object) -> bool:
    return (
        isinstance(ref, dict)
        and set(ref) == {"sha256", "size_bytes"}
        and isinstance(ref["sha256"], str)
        and _HEX.fullmatch(ref["sha256"]) is not None
        and type(ref["size_bytes"]) is int
        and ref["size_bytes"] >= 0
        and not path.is_symlink()
        and path.is_file()
        and _file_ref(path) == ref
    )


def _shape(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise PublicationError("invalid_pack", f"{label} 字段不符")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PublicationError("invalid_pack", f"{label} 必须是有限数")
    return float(value)


def _integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum or abs(value) > 2**53 - 1:
        raise PublicationError("invalid_pack", f"{label} 超出整数范围")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value) or value == "0" * 64:
        raise PublicationError("invalid_pack", f"{label} digest 无效")
    return value


def _reference(value: object, label: str) -> dict:
    ref = _shape(value, {"sha256", "size_bytes"}, label)
    _hash(ref["sha256"], label)
    _integer(ref["size_bytes"], 1, _MAX_CHUNK, f"{label} size")
    return ref


def _texture_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TEXTURE.fullmatch(value):
        raise PublicationError("invalid_pack", "pack 纹理路径无效")
    if any(part in {"", ".", ".."} for part in value[1:].split("/")):
        raise PublicationError("invalid_pack", "pack 纹理路径越界")
    return value


def _validate_pack_shape(manifest: dict) -> None:
    """Match the viewer's parseMeshPack v1 constraints before declaring ready."""
    _shape(manifest, {
        "schema_version", "source", "generator", "projection", "extent",
        "coordinate_contract", "original_mesh_count", "batches", "objects", "textures",
    }, "pack manifest")
    if manifest["schema_version"] != "aero-bench.osm2world-mesh-pack/v1":
        raise PublicationError("invalid_pack", "pack schema_version 不符")
    _reference(manifest["source"], "pack source")
    generator = _shape(manifest["generator"], {
        "runtime_sha256", "patch_sha256", "config_sha256", "revision",
    }, "pack generator")
    for key in ("runtime_sha256", "patch_sha256", "config_sha256"):
        _hash(generator[key], f"generator.{key}")
    if not isinstance(generator["revision"], str) or not _REVISION.fullmatch(generator["revision"]):
        raise PublicationError("invalid_pack", "pack revision 无效")
    projection = _shape(manifest["projection"], {"name", "axes", "origin"}, "pack projection")
    if projection["name"] != "MetricMapProjection" or projection["axes"] != "east-up-south":
        raise PublicationError("invalid_pack", "pack 投影无效")
    origin = _shape(projection["origin"], {"latitude_deg", "longitude_deg"}, "pack origin")
    if abs(_finite(origin["latitude_deg"], "origin.latitude")) >= 90 or abs(_finite(origin["longitude_deg"], "origin.longitude")) > 180:
        raise PublicationError("invalid_pack", "pack 原点越界")
    coordinates = _shape(manifest["coordinate_contract"], {
        "schema_version", "recipe", "converter_origin", "stored_translation_xz_m",
        "earth_circumference_m", "native_point_quantization_m", "storage",
        "source_json_sha256", "producer_sha256", "projection_helper_sha256",
    }, "pack source coordinates")
    if (
        coordinates["schema_version"] != "aero-bench.osm2world-source-coordinates/v1"
        or coordinates["recipe"] != "source-node-bounds-local-Mercator-then-declared-origin-translation"
        or _finite(coordinates["earth_circumference_m"], "earth circumference") != 40075016.686
        or _finite(coordinates["native_point_quantization_m"], "native point quantization") != 0.001
        or coordinates["storage"] != "source-mesh-float32-converter-coordinates-then-declared-origin-translation"
    ):
        raise PublicationError("invalid_pack", "pack source coordinate contract 不符")
    converter = _shape(coordinates["converter_origin"], {"latitude_deg", "longitude_deg"}, "pack converter origin")
    if abs(_finite(converter["latitude_deg"], "converter latitude")) >= 90 or abs(_finite(converter["longitude_deg"], "converter longitude")) > 180:
        raise PublicationError("invalid_pack", "pack converter origin 越界")
    translation = coordinates["stored_translation_xz_m"]
    if not isinstance(translation, list) or len(translation) != 2:
        raise PublicationError("invalid_pack", "pack source translation 必须包含 x/z")
    for value in translation:
        _finite(value, "pack source translation")
    for key in ("source_json_sha256", "producer_sha256", "projection_helper_sha256"):
        _hash(coordinates[key], f"coordinate_contract.{key}")
    if coordinates["source_json_sha256"] != manifest["source"]["sha256"]:
        raise PublicationError("invalid_pack", "pack source coordinate bytes 与 source ref 不符")
    extent = _shape(manifest["extent"], {"west", "east", "south", "north"}, "pack extent")
    if not (_finite(extent["west"], "west") < _finite(extent["east"], "east") and _finite(extent["south"], "south") < _finite(extent["north"], "north")):
        raise PublicationError("invalid_pack", "pack extent 倒置")
    _integer(manifest["original_mesh_count"], 1, 1_000_000, "original mesh count")
    batches = manifest["batches"]
    if not isinstance(batches, list) or not 0 < len(batches) <= 4096:
        raise PublicationError("invalid_pack", "pack batches 数量无效")
    textures = manifest["textures"]
    if not isinstance(textures, dict) or len(textures) > 4096:
        raise PublicationError("invalid_pack", "pack textures 数量无效")
    for name, ref in textures.items():
        _texture_name(name)
        _reference(ref, "pack texture")
    for raw in batches:
        batch = _shape(raw, {"file", "vertices", "indices", "layer", "material", "ranges"}, "pack batch")
        ref = _reference(batch["file"], "pack batch file")
        vertices = _integer(batch["vertices"], 3, _MAX_CHUNK // 32, "vertices")
        indices = _integer(batch["indices"], 3, _MAX_CHUNK // 4, "indices")
        if indices % 3 or ref["size_bytes"] != vertices * 32 + indices * 4:
            raise PublicationError("invalid_pack", "pack batch 字节布局不符")
        if batch["layer"] not in {"buildings", "roads", "terrain"}:
            raise PublicationError("invalid_pack", "pack batch 图层无效")
        material = _shape(batch["material"], {
            "color", "base_color_texture", "normal_texture", "orm_texture",
            "opacity_texture", "transparent", "clamp",
        }, "pack material")
        color = material["color"]
        if not isinstance(color, list) or len(color) != 3 or any(not 0 <= _finite(channel, "color") <= 1 for channel in color):
            raise PublicationError("invalid_pack", "pack 颜色无效")
        if type(material["transparent"]) is not bool or type(material["clamp"]) is not bool:
            raise PublicationError("invalid_pack", "pack 材质标志无效")
        for key in ("base_color_texture", "normal_texture", "orm_texture", "opacity_texture"):
            name = _texture_name(material[key])
            if name is not None and name not in textures:
                raise PublicationError("invalid_pack", "pack batch 纹理未声明")
        ranges = batch["ranges"]
        if not isinstance(ranges, list) or not 0 < len(ranges) <= indices // 3:
            raise PublicationError("invalid_pack", "pack ranges 数量无效")
        previous = 0
        for raw_range in ranges:
            item = _shape(raw_range, {"end", "target"}, "pack range")
            previous = _integer(item["end"], previous + 1, indices // 3, "range end")
            target = item["target"]
            if target is not None:
                value = _shape(target, {"kind", "id"}, "pack target")
                if value["kind"] not in {"building", "road"} or not isinstance(value["id"], str) or not _TARGET_ID.fullmatch(value["id"]):
                    raise PublicationError("invalid_pack", "pack target 无效")
        if previous != indices // 3:
            raise PublicationError("invalid_pack", "pack ranges 未覆盖全部三角形")
    objects = manifest["objects"]
    if not isinstance(objects, list) or len(objects) > 100_000:
        raise PublicationError("invalid_pack", "pack objects 数量无效")
    ids: set[str] = set()
    for raw in objects:
        item = _shape(raw, {"id", "tags"}, "pack object")
        identifier = item["id"]
        if not isinstance(identifier, str) or not _OBJECT_ID.fullmatch(identifier) or identifier in ids:
            raise PublicationError("invalid_pack", "pack OSM 对象 ID 无效或重复")
        ids.add(identifier)
        if not isinstance(item["tags"], dict) or any(not isinstance(tag, str) for tag in item["tags"].values()):
            raise PublicationError("invalid_pack", "pack OSM 对象 tags 无效")


def _verified_interchange(
    directory: Path, *, selection: SceneSelection, manifest_sha: str,
    effective_sha: str, effective_size: int,
) -> dict[str, object]:
    """Retain and recheck the complete compiler evidence beside the mesh pack."""
    if not directory.is_dir() or directory.is_symlink():
        raise PublicationError("invalid_interchange", "编译证据目录缺失")
    raw = (directory / "manifest.json").read_bytes()
    reference = {"sha256": _digest(raw), "size_bytes": len(raw)}
    if reference["sha256"] != manifest_sha:
        raise PublicationError("invalid_interchange", "保留的编译 manifest 摘要不符")
    manifest = _read_json(raw, "compiler manifest")
    selected = selection.to_document()
    origin = manifest.get("origin")
    if (
        not isinstance(manifest.get("source"), dict)
        or manifest["source"].get("sha256") != selection.source_sha256
        or not isinstance(origin, dict)
        or any(origin.get(key) != value for key, value in selected["origin"].items())
        or not isinstance(manifest.get("crop"), dict)
        or manifest["crop"].get("enu_bounds_m") != selected["bounds_enu_m"]
    ):
        raise PublicationError("invalid_interchange", "保留的编译来源、原点或选区不符")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise PublicationError("invalid_interchange", "编译输出清单缺失")
    seen: set[str] = set()
    for item in outputs:
        if not isinstance(item, dict):
            raise PublicationError("invalid_interchange", "编译输出引用无效")
        name, digest, size = item.get("path"), item.get("sha256"), item.get("byte_size")
        if (
            not isinstance(name, str) or not name or name in seen
            or name.startswith("/") or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or not isinstance(digest, str) or not _HEX.fullmatch(digest)
            or type(size) is not int or size < 1
        ):
            raise PublicationError("invalid_interchange", "编译输出路径或 digest 无效")
        seen.add(name)
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise PublicationError("invalid_interchange", "编译输出文件缺失")
        content = path.read_bytes()
        if len(content) != size or _digest(content) != digest:
            raise PublicationError("invalid_interchange", "编译输出文件摘要不符")
    required = {
        "osm/effective.osm.json", "osm/sumo-network.osm.json",
        "gazebo/scene.sdf", "metadata/common-scene.json",
        "audit/raw-closure.osm.json",
    }
    if not required.issubset(seen):
        raise PublicationError("invalid_interchange", "编译证据缺少关键 OSM/SUMO/Gazebo/审计文件")
    effective = (directory / "osm/effective.osm.json").read_bytes()
    if _digest(effective) != effective_sha or len(effective) != effective_size:
        raise PublicationError("invalid_interchange", "编译 effective OSM 与 pack 来源不符")
    if (directory / "authoring-selection.json").read_bytes() != canonical_json_bytes(selected):
        raise PublicationError("invalid_interchange", "编译选区记录不符")
    authoring_receipt = _read_json((directory / "authoring-receipt.json").read_bytes(), "authoring receipt")
    if (
        authoring_receipt.get("selection_sha256") != selection.sha256
        or authoring_receipt.get("source_sha256") != selection.source_sha256
        or authoring_receipt.get("compiler_manifest") != {"path": "manifest.json", **reference}
        or authoring_receipt.get("effective_osm") != {
            "path": "osm/effective.osm.json", "sha256": effective_sha,
            "size_bytes": effective_size,
        }
    ):
        raise PublicationError("invalid_interchange", "编译作者收据与保留产物不符")
    return {"path": "interchange/manifest.json", **reference}


def _style_identity(root: Path) -> dict[str, object]:
    directory = root / "frontend/public/osm2world/style"
    if not directory.is_dir() or directory.is_symlink():
        raise PublicationError("generator_unavailable", "OSM2World 样式目录不可用")
    entries = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise PublicationError("generator_unavailable", "OSM2World 样式包含链接文件")
        if path.is_file():
            entries.append({"path": path.relative_to(root).as_posix(), **_file_ref(path)})
    if not entries:
        raise PublicationError("generator_unavailable", "OSM2World 样式为空")
    return {
        "file_count": len(entries),
        "total_bytes": sum(item["size_bytes"] for item in entries),
        "sha256": _digest(canonical_json_bytes(entries)),
    }


def generator_identity(root: Path, source: VerifiedSceneSource) -> dict[str, object]:
    """Hash build inputs, excluding browser-only presentation and test code."""
    files = {}
    names = set(_IDENTITY_FILES)
    # Road/placement Python builders import local helpers dynamically. Include
    # that script family; the two executed MJS scripts and OSM2World TS modules
    # are enumerated above. A camera or E2E edit must not rebuild a city pack.
    for path in (root / "frontend/scripts").rglob("*.py"):
        if path.is_file() and not path.name.startswith("test_"):
            names.add(path.relative_to(root).as_posix())
    for name in sorted(names):
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise PublicationError("generator_unavailable", f"缺少固定生成器输入：{name}")
        files[name] = _file_ref(path)
    raw_provenance = (root / "frontend/public/osm2world/runtime-provenance.json").read_bytes()
    provenance = _read_json(raw_provenance, "runtime provenance")
    runtime = files["frontend/public/osm2world/osm2world-core-web.mjs"]["sha256"]
    patch = files["tools/osm2world/web-integration.patch"]["sha256"]
    if provenance.get("runtime_sha256") != runtime or provenance.get("patch_sha256") != patch:
        raise PublicationError("generator_unavailable", "OSM2World runtime 与 patch 来源不一致")
    if not isinstance(provenance.get("revision"), str) or not provenance["revision"]:
        raise PublicationError("generator_unavailable", "OSM2World revision 缺失")
    from .presentation import presentation_asset_identity
    visual_identity = presentation_asset_identity(root)
    return {
        "source_sha256": source.registration.sha256,
        "source_size_bytes": source.byte_size,
        "files": files,
        "style": _style_identity(root),
        "visual_assets": visual_identity,
        "runtime_revision": provenance["revision"],
    }


def _no_replace(staged: Path, final: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(staged), -100, os.fsencode(final), 1) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in (errno.EEXIST, errno.ENOTEMPTY):
        raise PublicationError("publication_conflict", "场景发布目录已存在")
    raise OSError(error_number, os.strerror(error_number), str(final))


def _verified_pack(
    pack_dir: Path, *, effective_sha: str, effective_size: int,
    origin: dict[str, float], root: Path,
) -> tuple[dict[str, object], set[str]]:
    if pack_dir.is_symlink():
        raise PublicationError("invalid_pack", "pack 目录不是常规目录")
    manifest_path = pack_dir / "manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest_ref = {"sha256": _digest(manifest_raw), "size_bytes": len(manifest_raw)}
    if _read_json((pack_dir / "manifest.lock.json").read_bytes(), "pack lock") != manifest_ref:
        raise PublicationError("invalid_pack", "pack manifest.lock 与 manifest bytes 不符")
    manifest = _read_json(manifest_raw, "pack manifest")
    _validate_pack_shape(manifest)
    if manifest.get("schema_version") != "aero-bench.osm2world-mesh-pack/v1":
        raise PublicationError("invalid_pack", "pack schema_version 不符")
    if manifest.get("source") != {"sha256": effective_sha, "size_bytes": effective_size}:
        raise PublicationError("invalid_pack", "pack source 不是本次 effective OSM")
    projection = manifest.get("projection")
    if not isinstance(projection, dict) or projection.get("origin") != {
        "latitude_deg": origin["latitude_deg"], "longitude_deg": origin["longitude_deg"],
    } or projection.get("name") != "MetricMapProjection" or projection.get("axes") != "east-up-south":
        raise PublicationError("invalid_pack", "pack 投影原点不符")
    provenance = _read_json(
        (root / "frontend/public/osm2world/runtime-provenance.json").read_bytes(), "runtime provenance",
    )
    properties = (root / "frontend/public/osm2world/style/standard.properties").read_bytes()
    runtime_ts = (root / "frontend/src/osm2world/runtime.ts").read_bytes()
    expected_generator = {
        "runtime_sha256": provenance["runtime_sha256"],
        "patch_sha256": provenance["patch_sha256"],
        "revision": provenance["revision"],
        "config_sha256": _digest(properties + runtime_ts),
    }
    if manifest.get("generator") != expected_generator:
        raise PublicationError("invalid_pack", "pack 生成器来源不符")
    batches = manifest.get("batches")
    textures = manifest.get("textures")
    if (
        not isinstance(batches, list) or not 0 < len(batches) <= 4096
        or type(manifest.get("original_mesh_count")) is not int
        or manifest["original_mesh_count"] < 1
        or not isinstance(textures, dict)
    ):
        raise PublicationError("invalid_pack", "pack 网格或纹理清单无效")
    declared: dict[str, dict] = {}

    def add(ref: object) -> None:
        if not isinstance(ref, dict) or set(ref) != {"sha256", "size_bytes"}:
            raise PublicationError("invalid_pack", "pack 资产引用无效")
        digest = ref["sha256"]
        if not isinstance(digest, str) or not _HEX.fullmatch(digest) or type(ref["size_bytes"]) is not int or ref["size_bytes"] < 0:
            raise PublicationError("invalid_pack", "pack 资产 digest 或大小无效")
        previous = declared.setdefault(digest, ref)
        if previous != ref:
            raise PublicationError("invalid_pack", "pack 同一 digest 有不同大小")

    add(manifest["source"])
    for batch in batches:
        if not isinstance(batch, dict) or batch.get("layer") not in {"buildings", "roads", "terrain"}:
            raise PublicationError("invalid_pack", "pack batch 无效")
        vertices, indices = batch.get("vertices"), batch.get("indices")
        if type(vertices) is not int or vertices < 3 or type(indices) is not int or indices < 3 or indices % 3:
            raise PublicationError("invalid_pack", "pack batch 几何计数无效")
        ref = batch.get("file")
        add(ref)
        if ref["size_bytes"] != vertices * 32 + indices * 4:
            raise PublicationError("invalid_pack", "pack batch 字节布局不符")
        ranges = batch.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            raise PublicationError("invalid_pack", "pack batch 范围无效")
        previous_end = 0
        for item in ranges:
            if not isinstance(item, dict) or type(item.get("end")) is not int or not previous_end < item["end"] <= indices // 3:
                raise PublicationError("invalid_pack", "pack batch 范围无效")
            previous_end = item["end"]
        if previous_end != indices // 3:
            raise PublicationError("invalid_pack", "pack batch 范围无效")
        material = batch.get("material")
        if not isinstance(material, dict):
            raise PublicationError("invalid_pack", "pack batch 材质无效")
        for key in ("base_color_texture", "normal_texture", "orm_texture", "opacity_texture"):
            texture = material.get(key)
            if texture is not None and texture not in textures:
                raise PublicationError("invalid_pack", "pack batch 纹理未声明")
    style_dir = root / "frontend/public/osm2world/style/textures"
    for name, ref in textures.items():
        if not isinstance(name, str) or not name.startswith(_STYLE_PREFIX):
            raise PublicationError("invalid_pack", "pack 纹理路径越界")
        relative = Path(name[len(_STYLE_PREFIX):])
        if not relative.parts or any(part in {".", ".."} for part in relative.parts):
            raise PublicationError("invalid_pack", "pack 纹理路径越界")
        original = style_dir / relative
        if not _matches_ref(original, ref):
            raise PublicationError("invalid_pack", "pack 纹理与固定样式不符")
        add(ref)
    assets = pack_dir / "assets"
    if assets.is_symlink():
        raise PublicationError("invalid_pack", "pack 资产目录不是常规目录")
    expected = set(declared) | {manifest_ref["sha256"]}
    actual = {item.name for item in assets.iterdir()} if assets.is_dir() else set()
    if actual != expected:
        raise PublicationError("invalid_pack", "pack 资产集合与 manifest 不符")
    for digest, ref in declared.items():
        if not _matches_ref(assets / digest, ref):
            raise PublicationError("invalid_pack", "pack 资产 bytes 与 manifest 不符")
    for batch in batches:
        raw = (assets / batch["file"]["sha256"]).read_bytes()
        floats_end = batch["vertices"] * 32
        if any(not math.isfinite(item[0]) for item in struct.iter_unpack("<f", raw[:floats_end])):
            raise PublicationError("invalid_pack", "pack 几何包含非有限浮点数")
        if any(item[0] >= batch["vertices"] for item in struct.iter_unpack("<I", raw[floats_end:])):
            raise PublicationError("invalid_pack", "pack 三角形引用不存在的顶点")
    if not _matches_ref(assets / manifest_ref["sha256"], manifest_ref):
        raise PublicationError("invalid_pack", "pack manifest 资产与锁不符")
    return manifest_ref, set(declared)


@dataclass(slots=True)
class _Job:
    job_id: str
    selection_sha256: str
    source_sha256: str
    selection: dict[str, object] | None = None
    identity: dict[str, object] | None = None
    state: str = "queued"
    compiler_manifest_sha256: str | None = None
    pack: dict[str, object] | None = None
    presentation: dict[str, object] | None = None
    error: dict[str, str] | None = None
    assets: set[str] | None = None
    presentation_assets: set[str] | None = None

    def document(self) -> dict[str, object]:
        return {
            "schema_version": JOB_SCHEMA,
            "job_id": self.job_id,
            "selection_sha256": self.selection_sha256,
            "source_sha256": self.source_sha256,
            "state": self.state,
            "compiler_manifest_sha256": self.compiler_manifest_sha256,
            "pack": self.pack,
            "presentation": self.presentation,
            "error": self.error,
        }


class ScenePackPublisher:
    def __init__(
        self, output_root: Path, *, repository_root: Path | None = None,
        registry: SceneSourceRegistry | None = None, build_timeout_s: float = 900,
    ) -> None:
        self.repository_root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._state_root = self.output_root / ".job-status"
        self._state_root.mkdir(exist_ok=True)
        lease_path = self.output_root / ".publisher.lock"
        lease_fd = os.open(lease_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lease_fd)
            raise PublicationError("publisher_in_use", "作者场景输出目录已有运行中的发布服务") from exc
        self._lease_fd = lease_fd
        self.registry = registry or default_scene_source_registry(self.repository_root)
        self.build_timeout_s = build_timeout_s
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._threads: set[threading.Thread] = set()
        self._closing = False

    def close(self) -> None:
        """Stop accepting jobs, finish active builds, then release the lease."""
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                self._closing = True
                threads = tuple(self._threads)
            for thread in threads:
                if thread is not threading.current_thread():
                    thread.join()
        lease_fd = getattr(self, "_lease_fd", -1)
        if lease_fd < 0:
            return
        self._lease_fd = -1
        fcntl.flock(lease_fd, fcntl.LOCK_UN)
        os.close(lease_fd)

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError):
            pass

    def sources(self) -> dict[str, object]:
        sources = []
        for source_id in sorted(self.registry._sources):
            verified = self.registry.read(source_id)
            bounds = verified.bounds_wgs84
            sources.append({
                "source_id": source_id,
                "sha256": verified.registration.sha256,
                "size_bytes": verified.byte_size,
                "display_name": verified.registration.display_name or source_id,
                "origin": {
                    "latitude_deg": verified.registration.origin.latitude_deg,
                    "longitude_deg": verified.registration.origin.longitude_deg,
                    "ellipsoid_height_m": verified.registration.origin.ellipsoid_height_m,
                    "geoid_undulation_m": verified.registration.origin.geoid_undulation_m,
                    "amsl_m": verified.registration.origin.amsl_m,
                },
                "bounds_wgs84": {
                    "min_latitude_deg": bounds.min_latitude_deg,
                    "max_latitude_deg": bounds.max_latitude_deg,
                    "min_longitude_deg": bounds.min_longitude_deg,
                    "max_longitude_deg": bounds.max_longitude_deg,
                },
                "data_url": f"/authoring/v1/sources/{source_id}",
            })
        return {"schema_version": "aero-bench.scene-source-catalog/v1", "sources": sources}

    def source_bytes(self, source_id: str) -> bytes:
        return self.registry.read(source_id).raw_bytes

    def submit(self, selection: SceneSelection) -> dict[str, object]:
        verified = self.registry.verify(selection)
        identity = generator_identity(self.repository_root, verified)
        job_id = _digest(canonical_json_bytes({
            "schema_version": "aero-bench.scene-build-identity/v1",
            "selection": selection.to_document(), "generator": identity,
        }))
        with self._lock:
            if self._closing:
                raise PublicationError("publisher_stopping", "作者发布服务正在停止")
            existing = self._jobs.get(job_id)
            if existing is not None:
                self._retry_interrupted(existing, selection, identity)
                return existing.document()
            job = _Job(job_id, selection.sha256, selection.source_sha256, selection.to_document(), identity)
            final = self.output_root / job_id
            if final.exists():
                self._restore(job, final, selection, identity)
                self._jobs[job_id] = job
                return job.document()
            if self._state_path(job_id).exists():
                recovered = self._load_job(job_id)
                self._retry_interrupted(recovered, selection, identity)
                return recovered.document()
            self._jobs[job_id] = job
            self._persist(job)
            self._start_job(job, selection, identity)
            return job.document()

    def _start_job(self, job: _Job, selection: SceneSelection, identity: dict) -> None:
        def run() -> None:
            try:
                self._build(job, selection, identity)
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, name=f"scene-pack-{job.job_id[:12]}", daemon=True)
        self._threads.add(thread)
        thread.start()

    def _retry_interrupted(self, job: _Job, selection: SceneSelection, identity: dict) -> None:
        if job.state != "failed" or not job.error or job.error.get("code") != "job_interrupted":
            return
        job.selection = selection.to_document()
        job.identity = identity
        job.state = "queued"
        job.compiler_manifest_sha256 = None
        job.pack = None
        job.presentation = None
        job.presentation_assets = None
        job.error = None
        self._persist(job)
        self._start_job(job, selection, identity)

    def status(self, job_id: str) -> dict[str, object]:
        if not _HEX.fullmatch(job_id):
            raise PublicationError("unknown_job", "未知场景任务")
        with self._lock:
            job = self._load_job(job_id)
            return job.document()

    def published_file(self, job_id: str, kind: str, asset_sha: str | None = None) -> Path:
        if not _HEX.fullmatch(job_id):
            raise PublicationError("unknown_job", "未知场景任务")
        with self._lock:
            job = self._load_job(job_id)
            if job.state != "ready":
                raise PublicationError("pack_not_ready", "场景网格尚未发布")
            if kind == "manifest":
                return self.output_root / job_id / "manifest.json"
            if kind == "asset" and asset_sha is not None and _HEX.fullmatch(asset_sha) and asset_sha in (job.assets or set()):
                return self.output_root / job_id / "assets" / asset_sha
            if kind == "presentation_manifest":
                return self.output_root / job_id / "presentation" / "manifest.json"
            if kind == "presentation_asset" and asset_sha is not None and _HEX.fullmatch(asset_sha) and asset_sha in (job.presentation_assets or set()):
                return self.output_root / job_id / "presentation" / "assets" / asset_sha
        raise PublicationError("unknown_asset", "未知场景资产")

    def _load_job(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is not None:
            return job
        final = self.output_root / job_id
        if final.is_dir() and not final.is_symlink():
            try:
                receipt = _read_json((final / "authoring-publication.json").read_bytes(), "publication receipt")
                selection = SceneSelection.from_document(receipt["selection"])
                identity = generator_identity(self.repository_root, self.registry.verify(selection))
                expected_id = _digest(canonical_json_bytes({
                    "schema_version": "aero-bench.scene-build-identity/v1",
                    "selection": selection.to_document(), "generator": identity,
                }))
                if expected_id != job_id:
                    raise PublicationError("publication_conflict", "已发布场景身份与当前输入不一致")
                job = _Job(job_id, selection.sha256, selection.source_sha256, selection.to_document(), identity)
                self._restore(job, final, selection, identity)
            except (OSError, KeyError, ValueError) as exc:
                if isinstance(exc, PublicationError):
                    raise
                raise PublicationError("invalid_pack", "已发布场景记录损坏") from exc
        else:
            record_path = self._state_path(job_id)
            if not record_path.is_file() or record_path.is_symlink():
                raise PublicationError("unknown_job", "未知场景任务")
            try:
                record = _read_json(record_path.read_bytes(), "job status")
                _shape(record, {
                    "schema_version", "job_id", "selection", "identity", "state",
                    "compiler_manifest_sha256", "error",
                }, "job status")
                if record["schema_version"] != JOB_SCHEMA or record["job_id"] != job_id:
                    raise PublicationError("invalid_job", "已保存任务身份不符")
                selection = SceneSelection.from_document(record["selection"])
                expected_id = _digest(canonical_json_bytes({
                    "schema_version": "aero-bench.scene-build-identity/v1",
                    "selection": selection.to_document(), "generator": record["identity"],
                }))
                if expected_id != job_id:
                    raise PublicationError("invalid_job", "已保存任务 digest 不符")
                job = _Job(
                    job_id, selection.sha256, selection.source_sha256,
                    selection.to_document(), record["identity"],
                    state=record["state"], compiler_manifest_sha256=record["compiler_manifest_sha256"],
                    error=record["error"],
                )
                if job.state in {"queued", "compiling", "meshing", "networking", "surfaces", "placing", "auditing"}:
                    job.state = "failed"
                    job.error = {"code": "job_interrupted", "message": "作者服务重启，中断的场景构建需重新提交"}
                    self._persist(job)
                elif job.state != "failed" or not isinstance(job.error, dict):
                    raise PublicationError("invalid_job", "已保存任务状态无效")
            except (OSError, KeyError, ValueError) as exc:
                if isinstance(exc, PublicationError):
                    raise
                raise PublicationError("invalid_job", "已保存任务记录损坏") from exc
        self._jobs[job_id] = job
        return job

    def _state_path(self, job_id: str) -> Path:
        return self._state_root / f"{job_id}.json"

    def _persist(self, job: _Job) -> None:
        if job.selection is None or job.identity is None:
            raise PublicationError("invalid_job", "缺少任务身份记录")
        raw = canonical_json_bytes({
            "schema_version": JOB_SCHEMA, "job_id": job.job_id,
            "selection": job.selection, "identity": job.identity,
            "state": job.state,
            "compiler_manifest_sha256": job.compiler_manifest_sha256,
            "error": job.error,
        })
        fd, temporary = tempfile.mkstemp(prefix=f".{job.job_id[:12]}-", dir=self._state_root)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._state_path(job.job_id))
            directory = os.open(self._state_root, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _update(self, job: _Job, state: str, *, manifest: str | None = None) -> None:
        with self._lock:
            job.state = state
            if manifest is not None:
                job.compiler_manifest_sha256 = manifest
            self._persist(job)

    def _restore(self, job: _Job, final: Path, selection: SceneSelection, identity: dict) -> None:
        receipt = _read_json((final / "authoring-publication.json").read_bytes(), "publication receipt")
        if receipt.get("job_id") != job.job_id or receipt.get("identity") != identity or receipt.get("selection") != selection.to_document():
            raise PublicationError("publication_conflict", "已发布场景与当前输入不一致")
        effective = receipt.get("effective_osm")
        if not isinstance(effective, dict) or not isinstance(effective.get("sha256"), str) or type(effective.get("size_bytes")) is not int:
            raise PublicationError("invalid_pack", "发布记录缺少 effective OSM")
        manifest, assets = _verified_pack(
            final, effective_sha=effective["sha256"], effective_size=effective["size_bytes"],
            origin=selection.to_document()["origin"], root=self.repository_root,
        )
        if receipt.get("manifest") != manifest:
            raise PublicationError("invalid_pack", "发布记录与 pack manifest 不符")
        interchange = _verified_interchange(
            final / "interchange", selection=selection,
            manifest_sha=receipt["compiler_manifest_sha256"],
            effective_sha=effective["sha256"], effective_size=effective["size_bytes"],
        )
        if receipt.get("interchange_manifest") != interchange:
            raise PublicationError("invalid_interchange", "发布记录与编译证据不符")
        try:
            presentation_ref, presentation_assets = verify_selected_presentation(
                final, job_id=job.job_id, selection=selection, pack_ref=manifest,
                compiler_manifest_sha256=receipt["compiler_manifest_sha256"],
                effective_osm_sha256=effective["sha256"], identity=identity, receipt=receipt,
            )
        except (KeyError, OSError, ValueError, TypeError) as exc:
            raise PublicationError("invalid_presentation", "已发布静态城市呈现或私有证据损坏") from exc
        job.compiler_manifest_sha256 = receipt["compiler_manifest_sha256"]
        job.pack = self._pack_document(job.job_id, manifest, effective["sha256"])
        job.assets = assets
        job.presentation = self._presentation_document(job.job_id, presentation_ref)
        job.presentation_assets = presentation_assets
        job.state = "ready"

    @staticmethod
    def _pack_document(job_id: str, manifest: dict, effective_sha: str) -> dict[str, object]:
        return {
            "base_url": f"/authoring/v1/scenes/{job_id}/pack/",
            "manifest": manifest,
            "source_sha256": effective_sha,
        }

    @staticmethod
    def _presentation_document(job_id: str, manifest: dict) -> dict[str, object]:
        return {"base_url": f"/authoring/v1/scenes/{job_id}/presentation/", "manifest": manifest}

    def _run_static_road_builder(self, network: object, pack_dir: Path, output: Path) -> SimpleNamespace:
        script = self.repository_root / "frontend/scripts/build-selected-static-roads.py"
        candidate = output / "candidate.json"
        command = [sys.executable, str(script),
                   "--network", str(network.network_path),
                   "--engineering-inputs", str(network.engineering_inputs_path),
                   "--source-osm", str(network.source_osm_path),
                   "--pack-manifest", str(pack_dir / "manifest.json"),
                   "--output", str(candidate)]
        output.mkdir(parents=True)
        with (output / "builder.stdout.log").open("wb") as stdout, \
                (output / "builder.stderr.log").open("wb") as stderr:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=self.build_timeout_s)
        (output / "builder-command.json").write_bytes(canonical_json_bytes({
            "command": command, "exit_code": result.returncode,
            "path_scope": "build_time_only",
        }))
        if result.returncode:
            raise PublicationError("static_road_failed", "真实路网道路生成或静态审计失败；请查看私有构建日志")
        signals = output / "candidate-signals.json"
        audit = output / "candidate-audit.json"
        if not all(path.is_file() and not path.is_symlink() for path in (candidate, signals, audit)):
            raise PublicationError("static_road_failed", "静态道路缺少候选、信号或审计产物")
        road_data = _read_json(candidate.read_bytes(), "static road candidate")
        audit_data = _read_json(audit.read_bytes(), "static road audit")
        road_sha, signal_sha = _digest(candidate.read_bytes()), _digest(signals.read_bytes())
        if (road_data.get("schema_version") != "aero-bench.city-static-road-candidate/v1"
                or road_data.get("source_network_sha256") != network.network_sha256
                or road_data.get("source_osm_sha256") != network.source_osm_sha256
                or road_data.get("signal_inventory_sha256") != signal_sha
                or audit_data.get("road_sha256") != road_sha
                or audit_data.get("signal_inventory_sha256") != signal_sha):
            raise PublicationError("static_road_failed", "静态道路候选与真实网络或审计不一致")
        return SimpleNamespace(
            road_path=candidate, signal_inventory_path=signals,
            road_sha256=road_sha, signal_inventory_sha256=signal_sha,
            source_network_sha256=network.network_sha256,
            source_osm_sha256=network.source_osm_sha256,
            mesh_pack_source_sha256=road_data["mesh_pack_source_sha256"],
            mesh_pack_manifest_sha256=road_data["mesh_pack_manifest_sha256"],
        )

    def _retain_failure_evidence(self, job: _Job, work: Path | None, code: str, message: str) -> None:
        """Move the failed private staging tree beside its job record for inspection."""
        if work is None or not work.is_dir():
            return
        failures = self._state_root / "failures"
        failures.mkdir(exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix=f"{job.job_id}-", dir=failures))
        (destination / "failure.json").write_bytes(canonical_json_bytes({
            "job_id": job.job_id, "state": job.state, "code": code, "message": message,
        }))
        shutil.move(str(work), str(destination / "staging"))

    def _build(self, job: _Job, selection: SceneSelection, identity: dict) -> None:
        work: Path | None = None
        try:
            work = Path(tempfile.mkdtemp(prefix=f".{job.job_id[:12]}-", dir=self.output_root))
            self._update(job, "compiling")
            compiled = compile_scene_selection(selection, output_root=work / "compiled", registry=self.registry)
            manifest_raw = compiled.manifest_path.read_bytes()
            manifest = _read_json(manifest_raw, "compiler manifest")
            effective = compiled.effective_osm_path.read_bytes()
            if _digest(manifest_raw) != compiled.manifest_sha256 or _digest(effective) != compiled.effective_osm_sha256:
                raise PublicationError("scene_compilation_failed", "编译产物摘要不符")
            outputs = {item.get("path"): item for item in manifest.get("outputs", []) if isinstance(item, dict)}
            compiled_origin = manifest.get("origin")
            if (
                manifest.get("source", {}).get("sha256") != selection.source_sha256
                or not isinstance(compiled_origin, dict)
                or any(compiled_origin.get(key) != value for key, value in selection.to_document()["origin"].items())
                or manifest.get("crop", {}).get("enu_bounds_m") != selection.to_document()["bounds_enu_m"]
            ):
                raise PublicationError("scene_compilation_failed", "编译来源、原点或选区不符")
            if outputs.get("osm/effective.osm.json", {}).get("sha256") != _digest(effective) or outputs.get("osm/effective.osm.json", {}).get("byte_size") != len(effective):
                raise PublicationError("scene_compilation_failed", "effective OSM 与编译 manifest 不符")
            self._update(job, "meshing", manifest=compiled.manifest_sha256)
            staged_pack = work / "pack"
            self._run_builder(compiled.effective_osm_path, staged_pack, selection)
            if generator_identity(self.repository_root, self.registry.verify(selection)) != identity:
                raise PublicationError("generator_changed", "构建期间源或生成器输入发生变化")
            manifest_ref, assets = _verified_pack(
                staged_pack, effective_sha=_digest(effective), effective_size=len(effective),
                origin=selection.to_document()["origin"], root=self.repository_root,
            )
            from .urban_network import build_selected_urban_network
            from .presentation import finalize_selected_static_presentation
            self._update(job, "networking")
            network_dir = work / "urban-network"
            network = build_selected_urban_network(compiled.output_dir, network_dir)
            self._update(job, "surfaces")
            road_dir = work / "static-road"
            road_candidate = self._run_static_road_builder(network, staged_pack, road_dir)
            self._update(job, "placing")
            presentation = finalize_selected_static_presentation(
                job_id=job.job_id, selection=selection, compiler_dir=compiled.output_dir,
                pack_dir=staged_pack, network_artifact=network,
                road_candidate=road_candidate,
                public_assets_root=self.repository_root / "frontend/public",
                output_dir=staged_pack / "presentation",
            )
            self._update(job, "auditing")
            retained = staged_pack / "interchange"
            shutil.move(str(compiled.output_dir), str(retained))
            interchange_ref = _verified_interchange(
                retained, selection=selection, manifest_sha=compiled.manifest_sha256,
                effective_sha=_digest(effective), effective_size=len(effective),
            )
            evidence_dir = staged_pack / "evidence"
            evidence_dir.mkdir()
            shutil.move(str(network_dir), str(evidence_dir / "urban-network"))
            shutil.move(str(road_dir), str(evidence_dir / "static-road"))
            receipt = {
                "schema_version": "aero-bench.authoring-scene-publication/v1",
                "job_id": job.job_id, "selection": selection.to_document(), "identity": identity,
                "compiler_manifest_sha256": compiled.manifest_sha256,
                "effective_osm": {"sha256": _digest(effective), "size_bytes": len(effective)},
                "manifest": manifest_ref,
                "interchange_manifest": interchange_ref,
                "presentation_manifest": _file_ref(presentation.manifest_path),
                "urban_network_evidence_sha256": evidence_tree_sha256(evidence_dir / "urban-network"),
                "static_road_evidence_sha256": evidence_tree_sha256(evidence_dir / "static-road"),
                "static_road_candidate": {"path": "candidate.json", **_file_ref(evidence_dir / "static-road/candidate.json")},
                "static_road_audit": {"path": "candidate-audit.json", **_file_ref(evidence_dir / "static-road/candidate-audit.json")},
            }
            (staged_pack / "authoring-publication.json").write_bytes(canonical_json_bytes(receipt))
            try:
                presentation_ref, presentation_assets = verify_selected_presentation(
                    staged_pack, job_id=job.job_id, selection=selection, pack_ref=manifest_ref,
                    compiler_manifest_sha256=compiled.manifest_sha256,
                    effective_osm_sha256=_digest(effective), identity=identity, receipt=receipt,
                )
            except (KeyError, OSError, ValueError, TypeError) as exc:
                raise PublicationError("invalid_presentation", "静态城市呈现或构建证据验收失败") from exc
            if (presentation_ref["sha256"] != presentation.manifest_sha256
                    or presentation_assets != set(presentation.asset_sha256s)):
                raise PublicationError("invalid_presentation", "静态城市封存资产与构建输出不一致")
            if generator_identity(self.repository_root, self.registry.verify(selection)) != identity:
                raise PublicationError("generator_changed", "发布前源或生成器输入发生变化")
            _no_replace(staged_pack, self.output_root / job.job_id)
            with self._lock:
                job.state = "ready"
                job.assets = assets
                job.pack = self._pack_document(job.job_id, manifest_ref, _digest(effective))
                job.presentation = self._presentation_document(job.job_id, presentation_ref)
                job.presentation_assets = presentation_assets
                self._persist(job)
        except Exception as exc:
            traceback.print_exc()
            if isinstance(exc, PublicationError):
                code, message = exc.code, exc.message
            elif isinstance(exc, (SceneSelectionError, SceneCompilationError)):
                code, message = "scene_compilation_failed", str(exc)
            else:
                code, message = "scene_build_failed", "场景构建失败；请查看服务端日志"
            try:
                self._retain_failure_evidence(job, work, code, message)
            except OSError:
                traceback.print_exc()
            with self._lock:
                job.state = "failed"
                job.pack = None
                job.presentation = None
                job.presentation_assets = None
                job.error = {"code": code, "message": message}
                self._persist(job)
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)

    def _run_builder(self, effective: Path, staged: Path, selection: SceneSelection) -> None:
        frontend = self.repository_root / "frontend"
        vite = frontend / "node_modules/vite/bin/vite.js"
        builder = frontend / "scripts/build-osm2world-pack.mjs"
        node = shutil.which("node")
        if node is None or not vite.is_file():
            raise PublicationError("generator_unavailable", "本地 Node/Vite 生成器不可用")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        local_http = build_opener(ProxyHandler({}))
        vite_stdout = (staged.parent / "vite.stdout.log").open("wb")
        vite_stderr = (staged.parent / "vite.stderr.log").open("wb")
        server = subprocess.Popen(
            [node, str(vite), "--config", "scripts/authoring-pack-vite.config.mjs", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
            cwd=frontend, stdout=vite_stdout, stderr=vite_stderr,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise PublicationError("generator_unavailable", "本地 OSM2World 服务启动失败")
                try:
                    with local_http.open(f"http://127.0.0.1:{port}/src/osm2world/runtime.ts", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(0.2)
            else:
                raise PublicationError("generator_unavailable", "本地 OSM2World 服务启动超时")
            origin = selection.origin
            try:
                result = subprocess.run(
                    [node, str(builder), str(effective), str(staged), f"http://127.0.0.1:{port}", str(origin.latitude_deg), str(origin.longitude_deg)],
                    cwd=frontend, capture_output=True, text=True, timeout=self.build_timeout_s, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise PublicationError("meshing_timeout", "OSM2World 转换超时") from exc
            (staged.parent / "meshing.stdout.log").write_text(result.stdout, encoding="utf-8")
            (staged.parent / "meshing.stderr.log").write_text(result.stderr, encoding="utf-8")
            if result.returncode != 0:
                print(result.stderr[-4000:], file=sys.stderr)
                raise PublicationError("meshing_failed", "OSM2World 官方转换失败；请查看服务端日志")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            vite_stdout.close()
            vite_stderr.close()
