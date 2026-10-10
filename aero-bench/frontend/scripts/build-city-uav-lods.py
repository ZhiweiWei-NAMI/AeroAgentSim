#!/usr/bin/env python3
"""Build middle-distance UAV GLBs from the supplied textured preview GLBs.

The meshoptimizer package already installed for the frontend performs the
indexed simplification. This script keeps material, texture and node data from
each source GLB, and compacts only its POSITION/NORMAL/TEXCOORD_0 vertex data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


FRONTEND = Path(__file__).resolve().parents[1]
MODELS = FRONTEND / "public/models/city-runtime"
SOURCES = {
    "holybro-x500-textured-preview.glb": "holybro-x500-lod1.glb",
    "quadcopter-40-preview.glb": "quadcopter-40-lod1.glb",
}
TARGET_TRIANGLES = 16000
MAX_GEOMETRIC_ERROR = 0.08
ATTRIBUTE_WEIGHTS = [0.08, 0.08, 0.08, 0.1, 0.1]  # normals, then UV
# The X500 has many disconnected parts. Give its shell more triangles, while
# pruning small fitting components and spending less on smooth rotor surfaces.
X500_TARGETS = (680, 9500, 2400, 6400)
X500_PRUNE = (False, False, True, True)

# stdin: vertex count, index count, target index count, prune flag, max error,
# weights, then arrays.
# stdout: simplified index count, meshoptimizer error, then uint32 indices.
NODE_SIMPLIFY = r"""
import fs from 'node:fs';
import { MeshoptSimplifier } from './node_modules/meshoptimizer/meshopt_simplifier.js';
await MeshoptSimplifier.ready;
const input = fs.readFileSync(0);
let offset = 0;
const vertexCount = input.readUInt32LE(offset); offset += 4;
const indexCount = input.readUInt32LE(offset); offset += 4;
const targetCount = input.readUInt32LE(offset); offset += 4;
const prune = input.readUInt32LE(offset); offset += 4;
const maxError = input.readDoubleLE(offset); offset += 8;
function take(Type, count) {
  const size = count * Type.BYTES_PER_ELEMENT;
  const data = new Type(input.buffer.slice(input.byteOffset + offset, input.byteOffset + offset + size));
  offset += size;
  return data;
}
const weights = Array.from(take(Float32Array, 5));
const positions = take(Float32Array, vertexCount * 3);
const attributes = take(Float32Array, vertexCount * 5);
const locks = take(Uint8Array, vertexCount);
const indices = take(Uint32Array, indexCount);
if (offset !== input.length) throw new Error('Unexpected simplifier input length');
const [result, error] = MeshoptSimplifier.simplifyWithAttributes(
  indices, positions, 3, attributes, 5,
  weights, locks,
  targetCount, maxError, prune ? ['Prune'] : []
);
const header = Buffer.alloc(12);
header.writeUInt32LE(result.length, 0);
header.writeDoubleLE(error, 4);
process.stdout.write(header);
process.stdout.write(Buffer.from(result.buffer, result.byteOffset, result.byteLength));
"""


def read_glb(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    if len(raw) < 28 or struct.unpack_from("<4sII", raw) != (b"glTF", 2, len(raw)):
        raise ValueError(f"Invalid GLB header: {path}")
    json_len, json_type = struct.unpack_from("<I4s", raw, 12)
    binary_offset = 20 + json_len
    binary_len, binary_type = struct.unpack_from("<I4s", raw, binary_offset)
    if json_type != b"JSON" or binary_type != b"BIN\0" or binary_offset + 8 + binary_len != len(raw):
        raise ValueError(f"Expected JSON and BIN chunks: {path}")
    return json.loads(raw[20:binary_offset]), raw[binary_offset + 8:]


def accessor_array(document: dict, binary: bytes, accessor_index: int, components: int,
                   component_type: int) -> np.ndarray:
    accessor = document["accessors"][accessor_index]
    view = document["bufferViews"][accessor["bufferView"]]
    if accessor.get("sparse") or accessor.get("normalized") or view.get("byteStride"):
        raise ValueError(f"Unsupported accessor layout: {accessor_index}")
    if accessor["componentType"] != component_type or accessor["type"] != ("SCALAR" if components == 1 else f"VEC{components}"):
        raise ValueError(f"Unexpected accessor type: {accessor_index}")
    dtype = {5125: np.dtype("<u4"), 5126: np.dtype("<f4")}[component_type]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    length = accessor["count"] * components * dtype.itemsize
    if start + length > view.get("byteOffset", 0) + view["byteLength"] or start + length > len(binary):
        raise ValueError(f"Accessor out of bounds: {accessor_index}")
    return np.frombuffer(binary, dtype=dtype, count=accessor["count"] * components, offset=start).reshape(-1, components)


def simplify(positions: np.ndarray, normals: np.ndarray, uv: np.ndarray,
             indices: np.ndarray, target_triangles: int, prune: bool) -> tuple[np.ndarray, float]:
    attributes = np.concatenate((normals, uv), axis=1).astype("<f4", copy=False)
    locks = np.zeros(len(positions), dtype=np.uint8)
    # Preserve each material part's six extrema; this also preserves the full
    # model extent when its parts are recombined after simplification.
    used = np.unique(indices)
    for axis in range(3):
        locks[used[np.argmin(positions[used, axis])]] = 1
        locks[used[np.argmax(positions[used, axis])]] = 1
    payload = b"".join((
        struct.pack("<IIIId", len(positions), len(indices), target_triangles * 3, int(prune), MAX_GEOMETRIC_ERROR),
        np.asarray(ATTRIBUTE_WEIGHTS, dtype="<f4").tobytes(),
        positions.astype("<f4", copy=False).tobytes(),
        attributes.tobytes(), locks.tobytes(), indices.astype("<u4", copy=False).tobytes(),
    ))
    process = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SIMPLIFY], input=payload,
        cwd=FRONTEND, check=True, capture_output=True,
    )
    if len(process.stdout) < 12:
        raise ValueError("meshoptimizer returned no index data")
    count, error = struct.unpack_from("<Id", process.stdout)
    if count < 3 or count % 3 or len(process.stdout) != 12 + count * 4:
        raise ValueError("meshoptimizer returned invalid index data")
    result = np.frombuffer(process.stdout, dtype="<u4", count=count, offset=12).copy()
    if result.max() >= len(positions):
        raise ValueError("meshoptimizer returned an out-of-range vertex")
    return result, error


def encode_glb(document: dict, binary: bytes) -> bytes:
    json_bytes = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return b"".join((
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(json_bytes), b"JSON"), json_bytes,
        struct.pack("<I4s", len(binary), b"BIN\0"), binary,
    ))


def add_view(data: bytearray, views: list[dict], values: np.ndarray, target: int) -> int:
    data.extend(b"\0" * (-len(data) % 4))
    offset = len(data)
    raw = values.tobytes()
    data.extend(raw)
    views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(raw), "target": target})
    return len(views) - 1


def bounds(positions: np.ndarray, indices: np.ndarray) -> dict:
    used = positions[np.unique(indices)]
    lo, hi = used.min(axis=0), used.max(axis=0)
    return {"min": lo.tolist(), "max": hi.tolist(), "extent": (hi - lo).tolist()}


def silhouette_iou(source_positions: np.ndarray, source_indices: np.ndarray,
                   lod_positions: np.ndarray, lod_indices: np.ndarray) -> dict[str, float]:
    """Compare the source and LOD silhouettes in three 512 px orthographic views."""
    source_bounds = bounds(source_positions, source_indices)
    result = {}
    for label, axes in (("xy", (0, 1)), ("xz", (0, 2)), ("yz", (1, 2))):
        masks = []
        for positions, indices in ((source_positions, source_indices), (lod_positions, lod_indices)):
            projected = (
                (positions[:, axes] - np.asarray(source_bounds["min"])[list(axes)])
                / np.asarray(source_bounds["extent"])[list(axes)] * 480 + 16
            )
            bitmap = Image.new("1", (512, 512))
            painter = ImageDraw.Draw(bitmap)
            for triangle in indices.reshape(-1, 3):
                painter.polygon([tuple(point) for point in projected[triangle]], fill=1)
            masks.append(np.asarray(bitmap, dtype=bool))
        source_mask, lod_mask = masks
        result[label] = float(np.count_nonzero(source_mask & lod_mask) / np.count_nonzero(source_mask | lod_mask))
    return result


def build(source_name: str, output_name: str) -> dict:
    source_path = MODELS / source_name
    output_path = MODELS / output_name
    document, binary = read_glb(source_path)
    if len(document.get("meshes", [])) != 1 or len(document.get("buffers", [])) != 1:
        raise ValueError(f"Expected one mesh and one buffer: {source_name}")
    if document.get("skins") or document.get("animations"):
        raise ValueError(f"Animated or skinned source is unsupported: {source_name}")
    mesh = document["meshes"][0]
    primitives = mesh["primitives"]
    if any(set(part["attributes"]) != {"POSITION", "NORMAL", "TEXCOORD_0"} or part.get("mode", 4) != 4 or part.get("targets") for part in primitives):
        raise ValueError(f"Unexpected primitive layout: {source_name}")
    attributes = primitives[0]["attributes"]
    if any(part["attributes"] != attributes for part in primitives):
        raise ValueError(f"Expected shared vertex attributes: {source_name}")
    if any("bufferView" in image for image in document.get("images", [])):
        raise ValueError(f"Expected external textures: {source_name}")
    for image in document.get("images", []):
        uri = image.get("uri")
        if not isinstance(uri, str) or not (MODELS / uri).is_file():
            raise ValueError(f"Missing source texture {uri!r}: {source_name}")
    positions = accessor_array(document, binary, attributes["POSITION"], 3, 5126)
    normals = accessor_array(document, binary, attributes["NORMAL"], 3, 5126)
    uv = accessor_array(document, binary, attributes["TEXCOORD_0"], 2, 5126)
    if len(positions) != len(normals) or len(positions) != len(uv) or not all(np.isfinite(v).all() for v in (positions, normals, uv)):
        raise ValueError(f"Invalid shared vertex data: {source_name}")
    source_indices = [accessor_array(document, binary, part["indices"], 1, 5125).reshape(-1) for part in primitives]
    if any(len(indices) % 3 or indices.max() >= len(positions) for indices in source_indices):
        raise ValueError(f"Invalid source indices: {source_name}")
    source_triangles = sum(len(indices) // 3 for indices in source_indices)
    simplified = []
    primitive_stats = []
    for part_index, (part, indices) in enumerate(zip(primitives, source_indices)):
        if source_name == "holybro-x500-textured-preview.glb":
            target = X500_TARGETS[part_index]
            prune = X500_PRUNE[part_index]
        else:
            target = max(100, round(TARGET_TRIANGLES * len(indices) / (3 * source_triangles)))
            prune = True
        result, error = simplify(positions, normals, uv, indices, target, prune)
        simplified.append(result)
        primitive_stats.append({
            "material": part.get("material"), "source_triangles": len(indices) // 3,
            "target_triangles": target, "prune_components": prune,
            "lod_triangles": len(result) // 3, "meshoptimizer_error": error,
        })
    all_source_indices = np.concatenate(source_indices)
    all_lod_indices = np.concatenate(simplified)
    used, inverse = np.unique(all_lod_indices, return_inverse=True)
    model = copy.deepcopy(document)
    data = bytearray()
    views = []
    for name, values in (("POSITION", positions), ("NORMAL", normals), ("TEXCOORD_0", uv)):
        accessor = model["accessors"][attributes[name]]
        compacted = values[used].astype("<f4", copy=False)
        accessor["bufferView"] = add_view(data, views, compacted, 34962)
        accessor["count"] = len(used)
        accessor.pop("byteOffset", None)
        if name == "POSITION":
            accessor["min"] = compacted.min(axis=0).tolist()
            accessor["max"] = compacted.max(axis=0).tolist()
    cursor = 0
    for part, indices in zip(model["meshes"][0]["primitives"], simplified):
        remapped = inverse[cursor:cursor + len(indices)].astype("<u4")
        cursor += len(indices)
        accessor = model["accessors"][part["indices"]]
        accessor["bufferView"] = add_view(data, views, remapped, 34963)
        accessor["count"] = len(indices)
        accessor.pop("byteOffset", None)
        accessor.pop("min", None)
        accessor.pop("max", None)
    model["bufferViews"] = views
    model["buffers"][0]["byteLength"] = len(data)
    output_bytes = encode_glb(model, bytes(data))
    output_path.write_bytes(output_bytes)
    reread, output_binary = read_glb(output_path)
    if (reread["nodes"] != document["nodes"] or reread.get("scenes") != document.get("scenes")
            or reread["materials"] != document["materials"] or reread["images"] != document["images"]):
        raise ValueError(f"Source transforms or textures changed: {output_name}")
    output_positions = accessor_array(reread, output_binary, attributes["POSITION"], 3, 5126)
    output_normals = accessor_array(reread, output_binary, attributes["NORMAL"], 3, 5126)
    output_uv = accessor_array(reread, output_binary, attributes["TEXCOORD_0"], 2, 5126)
    output_indices = np.concatenate([
        accessor_array(reread, output_binary, part["indices"], 1, 5125).reshape(-1)
        for part in reread["meshes"][0]["primitives"]
    ])
    if (len(output_positions) != len(used) or len(output_normals) != len(used)
            or len(output_uv) != len(used) or output_indices.max() >= len(used)
            or not np.array_equal(output_positions, positions[used])
            or not np.array_equal(output_normals, normals[used])
            or not np.array_equal(output_uv, uv[used])):
        raise ValueError(f"LOD vertex data did not round-trip: {output_name}")
    source_bounds = bounds(positions, all_source_indices)
    lod_bounds = bounds(output_positions, output_indices)
    extent_error = max(abs(a - b) / a for a, b in zip(source_bounds["extent"], lod_bounds["extent"]))
    corner_error = max(
        abs(a - b) / extent
        for key in ("min", "max")
        for a, b, extent in zip(source_bounds[key], lod_bounds[key], source_bounds["extent"])
    )
    if max(extent_error, corner_error) >= 0.01:
        raise ValueError(f"LOD bbox differs by {max(extent_error, corner_error):.3%}: {output_name}")
    return {
        "source": source_name, "source_url": f"/models/city-runtime/{source_name}",
        "lod": output_name, "lod_url": f"/models/city-runtime/{output_name}",
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "lod_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "source_bytes": source_path.stat().st_size, "lod_bytes": len(output_bytes),
        "source_triangles": source_triangles, "lod_triangles": len(all_lod_indices) // 3,
        "source_vertices": len(np.unique(all_source_indices)), "lod_vertices": len(used),
        "source_draw_calls": len(primitives), "lod_draw_calls": len(primitives),
        "source_bbox": source_bounds, "lod_bbox": lod_bounds,
        "max_bbox_extent_relative_error": extent_error,
        "max_bbox_corner_relative_error": corner_error,
        "orthographic_silhouette_iou_512": silhouette_iou(positions, all_source_indices, output_positions, output_indices),
        "source_scene_and_node_transforms_preserved": True,
        "source_materials_and_texture_uris_preserved": True,
        "material_count": len(document.get("materials", [])),
        "texture_count": len(document.get("textures", [])),
        "primitives": primitive_stats,
    }


def main() -> None:
    reports = [build(source, output) for source, output in SOURCES.items()]
    report = {
        "generator": "frontend/scripts/build-city-uav-lods.py",
        "command": "python frontend/scripts/build-city-uav-lods.py",
        "simplifier": "frontend/node_modules/meshoptimizer MeshoptSimplifier.simplifyWithAttributes",
        "meshoptimizer_version": json.loads((FRONTEND / "node_modules/meshoptimizer/package.json").read_text())["version"],
        "simplifier_flags": "Per primitive; see prune_components",
        "target_triangles": TARGET_TRIANGLES,
        "max_geometric_error": MAX_GEOMETRIC_ERROR,
        "attribute_weights_normal_xyz_uv": ATTRIBUTE_WEIGHTS,
        "models": reports,
    }
    (MODELS / "uav-lod-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in reports:
        print(f"{row['lod']}: {row['lod_triangles']} triangles, {row['lod_draw_calls']} draws, {row['lod_bytes']} bytes, bbox extent error {row['max_bbox_extent_relative_error']:.3%}")


if __name__ == "__main__":
    main()
