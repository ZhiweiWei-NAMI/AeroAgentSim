#!/usr/bin/env python3
"""Export the reviewed single STL as a centered, material-neutral GLB preview."""
import hashlib
import json
import struct
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT.parent / "validation/downloaded-assets/quark-drone-models/003 无人机/reconnaissance-attack-aircraft-1 6 STL X_T.zip"
SOURCE_MEMBER = "reconnaissance-attack-aircraft-1 6 STL X_T/Assem1 - Hellfire_Assembly_Halcyon1-2.STL"
OUTPUT = ROOT / "public/models/uav/mesh/reconnaissance-hellfire-appearance-preview.glb"
EXPECTED_SHA256 = "fadfe4ead48d5c88e200f110dca6e6593a59dbf0f968a020740c5337ed94e0cd"
ZIP_SHA256 = "1cc93023d4c7fe8032c068fd7e37f4d9ce98d2e721f0a7018e0e0e0ff08684a4"

if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ZIP_SHA256:
    raise ValueError(f"Unexpected source ZIP SHA-256: {ARCHIVE}")
with zipfile.ZipFile(ARCHIVE) as bundle:
    source = bundle.read(SOURCE_MEMBER)
source_sha256 = hashlib.sha256(source).hexdigest()
if source_sha256 != EXPECTED_SHA256:
    raise ValueError(f"Unexpected STL SHA-256: {source_sha256}")
triangle_count = struct.unpack_from("<I", source, 80)[0]
if len(source) != 84 + 50 * triangle_count:
    raise ValueError("STL binary length does not match the declared facet count")

facets = np.frombuffer(
    source,
    dtype=[("normal", "<f4", (3,)), ("vertex", "<f4", (3, 3)), ("attribute", "<u2")],
    count=triangle_count,
    offset=84,
)
positions = facets["vertex"].reshape((-1, 3)).astype("<f4", copy=True)
normals = np.repeat(facets["normal"], 3, axis=0).astype("<f4", copy=False)
if not np.isfinite(positions).all() or not np.isfinite(normals).all():
    raise ValueError("STL contains non-finite coordinates or normals")

source_min = positions.min(axis=0)
source_max = positions.max(axis=0)
source_center = (source_min + source_max) * 0.5
source_extent = source_max - source_min
# Recenter the single object as a whole. No island-specific placements or scale are inferred.
translation = (-source_center).astype("<f4")
vertex_count = int(positions.shape[0])
degenerate_count = int(
    (
        np.linalg.norm(
            np.cross(
                positions[0::3].astype(np.float64) - positions[1::3].astype(np.float64),
                positions[0::3].astype(np.float64) - positions[2::3].astype(np.float64),
            ),
            axis=1,
        )
        < 1e-12
    ).sum()
)

position_bytes = positions.tobytes(order="C")
normal_bytes = normals.tobytes(order="C")
binary = position_bytes + normal_bytes
binary_length = len(binary)

document = {
    "asset": {
        "version": "2.0",
        "generator": "AERO_BENCH reviewed STL appearance preview exporter",
        "extras": {
            "sourceZip": "reconnaissance-attack-aircraft-1 6 STL X_T.zip",
            "sourceZipSha256": ZIP_SHA256,
            "sourceStl": "Assem1 - Hellfire_Assembly_Halcyon1-2.STL",
            "sourceStlSha256": source_sha256,
            "sourceTriangleCount": triangle_count,
            "sourceMaterial": "not provided by STL (all binary facet attributes are zero)",
            "sourceTextures": "none",
            "sourceUnits": "unverified; source coordinate values are preserved without scale",
            "wholeModelRecenteringTranslation": [float(x) for x in translation],
            "sourceAabbMin": [float(x) for x in source_min],
            "sourceAabbMax": [float(x) for x in source_max],
            "sourceAabbExtent": [float(x) for x in source_extent],
            "connectedSurfaceIslands": 10,
            "degenerateTrianglesRetained": degenerate_count,
            "previewClassification": "Hellfire missile appearance only; not a UAV airframe",
        },
    },
    "scene": 0,
    "scenes": [{"nodes": [0]}],
    "nodes": [
        {
            "name": "Hellfire Assembly appearance preview",
            "mesh": 0,
            "translation": [float(x) for x in translation],
        }
    ],
    "meshes": [
        {
            "name": "Single STL geometry; 10 source-coordinate surface islands",
            "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "material": 0, "mode": 4}],
        }
    ],
    "materials": [
        {
            "name": "Neutral preview material (source finish unavailable)",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.58, 0.60, 0.66, 1.0],
                "metallicFactor": 0.18,
                "roughnessFactor": 0.62,
            },
            "doubleSided": True,
            "extras": {
                "sourceMaterial": "none; neutral gray is a preview-only choice",
                "sourceTexture": "none",
            },
        }
    ],
    "buffers": [{"byteLength": binary_length}],
    "bufferViews": [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes), "target": 34962},
        {
            "buffer": 0,
            "byteOffset": len(position_bytes),
            "byteLength": len(normal_bytes),
            "target": 34962,
        },
    ],
    "accessors": [
        {
            "bufferView": 0,
            "componentType": 5126,
            "count": vertex_count,
            "type": "VEC3",
            "min": [float(x) for x in source_min],
            "max": [float(x) for x in source_max],
        },
        {"bufferView": 1, "componentType": 5126, "count": vertex_count, "type": "VEC3"},
    ],
}

json_chunk = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode("ascii")
json_chunk += b" " * ((-len(json_chunk)) % 4)
binary_chunk = binary + b"\0" * ((-len(binary)) % 4)
total_length = 12 + 8 + len(json_chunk) + 8 + len(binary_chunk)
with OUTPUT.open("wb") as handle:
    handle.write(struct.pack("<4sII", b"glTF", 2, total_length))
    handle.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
    handle.write(json_chunk)
    handle.write(struct.pack("<I4s", len(binary_chunk), b"BIN\0"))
    handle.write(binary_chunk)

print(
    json.dumps(
        {
            "output": str(OUTPUT),
            "glbBytes": OUTPUT.stat().st_size,
            "triangles": triangle_count,
            "vertexCount": vertex_count,
            "sourceAabbMin": source_min.tolist(),
            "sourceAabbMax": source_max.tolist(),
            "sourceAabbExtent": source_extent.tolist(),
            "recenteringTranslation": translation.tolist(),
            "degenerateTrianglesRetained": degenerate_count,
            "material": "neutral preview-only PBR; source material/texture absent",
            "sourceUnits": "unverified; no scale applied",
        },
        indent=2,
    )
)
