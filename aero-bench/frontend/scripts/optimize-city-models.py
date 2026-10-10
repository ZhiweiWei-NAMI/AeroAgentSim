#!/usr/bin/env python3
"""Build browser copies of supplied GLBs with shared, lossless WebP textures."""

from __future__ import annotations

import hashlib
import io
import json
import struct
from pathlib import Path

from PIL import Image


PUBLIC = Path(__file__).resolve().parents[1] / "public"
OUTPUT = PUBLIC / "models" / "city-runtime"
SOURCES = (
    "models/incoming/urban-traffic/glb/Car_6-preview.glb",
    "models/incoming/urban-traffic/glb/Taxi_1-day-preview.glb",
    "models/incoming/urban-traffic/glb/Police_1-day-preview.glb",
    "models/incoming/urban-traffic/glb/Bicycle_Man_34-bike-only.glb",
    "models/incoming/citizens/glb/casual27_m_highpoly_walk.glb",
    "models/uav/holybro-x500-textured-preview.glb",
    "models/uav/quadcopter-40-preview.glb",
)


def read_glb(path: Path) -> tuple[dict, bytes]:
    source = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", source)
    if magic != 0x46546C67 or version != 2 or length != len(source):
        raise ValueError(f"Invalid glTF 2.0 binary: {path}")
    offset = 12
    chunks = []
    while offset < length:
        chunk_length, chunk_type = struct.unpack_from("<II", source, offset)
        offset += 8
        chunks.append((chunk_type, source[offset:offset + chunk_length]))
        offset += chunk_length
    if len(chunks) != 2 or chunks[0][0] != 0x4E4F534A or chunks[1][0] != 0x004E4942:
        raise ValueError(f"Expected one JSON and one BIN chunk: {path}")
    return json.loads(chunks[0][1]), chunks[1][1]


def buffer_view_references(value: object) -> set[int]:
    if isinstance(value, list):
        return set().union(*(buffer_view_references(item) for item in value))
    if isinstance(value, dict):
        refs = {item for key, item in value.items() if key == "bufferView" and isinstance(item, int)}
        return refs | set().union(*(buffer_view_references(item) for item in value.values()))
    return set()


def remap_buffer_views(value: object, mapping: dict[int, int]) -> None:
    if isinstance(value, list):
        for item in value:
            remap_buffer_views(item, mapping)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "bufferView" and isinstance(item, int):
                value[key] = mapping[item]
            else:
                remap_buffer_views(item, mapping)


def write_glb(path: Path, document: dict, binary: bytes) -> None:
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_chunk = encoded + b" " * (-len(encoded) % 4)
    bin_chunk = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    path.write_bytes(
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
        + struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    textures = OUTPUT / "textures"
    textures.mkdir(exist_ok=True)
    report = []
    emitted = set()
    for relative in SOURCES:
        source_path = PUBLIC / relative
        document, binary = read_glb(source_path)
        if len(document.get("buffers", [])) != 1 or "uri" in document["buffers"][0]:
            raise ValueError(f"Expected a single embedded buffer: {source_path}")
        views = document["bufferViews"]
        converted = set()
        for index, image in enumerate(document.get("images", [])):
            if "bufferView" not in image:
                raise ValueError(f"Image {index} is not embedded: {source_path}")
            view = views[image["bufferView"]]
            start = view.get("byteOffset", 0)
            original = binary[start:start + view["byteLength"]]
            with Image.open(io.BytesIO(original)) as bitmap:
                bitmap.load()
                output = io.BytesIO()
                bitmap.save(output, format="WEBP", lossless=True, exact=True, method=4)
            webp = output.getvalue()
            use_webp = len(webp) < len(original)
            asset = webp if use_webp else original
            original_extension = {"image/png": "png", "image/jpeg": "jpg"}.get(image.get("mimeType"))
            if original_extension is None:
                raise ValueError(f"Unsupported embedded image {index}: {source_path}")
            extension = "webp" if use_webp else original_extension
            name = f"{hashlib.sha256(asset).hexdigest()}.{extension}"
            texture_path = textures / name
            if not texture_path.exists():
                texture_path.write_bytes(asset)
            emitted.add(texture_path)
            image.pop("bufferView")
            image.pop("mimeType", None)
            image["uri"] = f"textures/{name}"
            if use_webp:
                converted.add(index)
        if converted:
            document.setdefault("extensionsUsed", []).append("EXT_texture_webp")
            document.setdefault("extensionsRequired", []).append("EXT_texture_webp")
            for texture in document.get("textures", []):
                index = texture.get("source")
                if index in converted:
                    texture.pop("source")
                    texture.setdefault("extensions", {})["EXT_texture_webp"] = {"source": index}

        referenced = buffer_view_references({key: value for key, value in document.items()
                                              if key != "bufferViews"})
        mapping = {}
        new_views = []
        new_binary = bytearray()
        for old_index in sorted(referenced):
            view = views[old_index]
            if view.get("buffer", 0) != 0:
                raise ValueError(f"Unexpected second buffer: {source_path}")
            start = view.get("byteOffset", 0)
            new_binary.extend(b"\x00" * (-len(new_binary) % 4))
            mapping[old_index] = len(new_views)
            new_views.append({**view, "byteOffset": len(new_binary)})
            new_binary.extend(binary[start:start + view["byteLength"]])
        remap_buffer_views({key: value for key, value in document.items() if key != "bufferViews"}, mapping)
        document["bufferViews"] = new_views
        document["buffers"][0]["byteLength"] = len(new_binary)
        output_path = OUTPUT / source_path.name
        write_glb(output_path, document, bytes(new_binary))
        report.append({"model": source_path.name,
                       "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                       "runtime_glb_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                       "source_bytes": source_path.stat().st_size,
                       "runtime_glb_bytes": output_path.stat().st_size,
                       "textures": len(document.get("images", []))})

    (OUTPUT / "conversion-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"models": len(report), "source_mb": round(sum(x["source_bytes"] for x in report) / 2**20, 2),
                      "glb_mb": round(sum(x["runtime_glb_bytes"] for x in report) / 2**20, 2),
                      "shared_texture_mb": round(sum(path.stat().st_size for path in emitted) / 2**20, 2)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
