"""Merge a strict building-render catalog fragment into an authored urban catalog.

The validated ``validation/building-render-scaleout-mimo-20260929`` run produced
one real per-building GLB render per scene ``object_id`` plus a strict fragment
document (``building-render-catalog-fragment.json``) whose ``building_render``
entries already satisfy :class:`~aero_bench.world.scene_authoring.CatalogBuildingRender`
and whose ``glb_digests`` pin the real bytes. This module is the smallest
reusable stage that takes such a fragment -- for any compiled scene, not only
Shanghai -- merges it with an authored ``UrbanWorldCatalog``, and produces the
exact-byte staging plan that :func:`aero_bench.world.scene_authoring.
author_urban_world_package` writes into the bundle.

Honesty rules (fail closed; no mock, no fallback, no legacy parser):

* The fragment is read strictly: schema version, ``building_render`` section,
  strict JSON (duplicate object keys are rejected), and every entry validated by
  the live strict contract. A glTF render must declare ``source_frame:
  asset_local`` and ``media_type: model/gltf-binary``; anything else -- including
  a ``WGS84`` label -- is rejected here, never passed through.
* Entry ``object_id``s must be **1:1** with the compiled scene's building
  ``object_id``s: duplicates, missing and extra ids all fail closed and are
  enumerated.
* Every staged byte comes from the fragment's real ``file``; its SHA-256 and
  size must equal the fragment's ``glb_digests`` record, its container must be a
  glTF 2.0 GLB, and its embedded provenance must identify the **same** source
  scene (``asset.extras.scene.objects_json_sha256`` equals the live
  ``metadata/objects.json`` digest). A fragment rendered from an older scene is
  rejected as stale, not merged.
* The render's geometry envelope must fit the scene's collision/geometry
  envelope: POSITION bounds (read from the real binary chunk) must reproduce the
  scene footprint extents about the area-weighted centroid anchor and the
  scene's base/top heights within 1e-3 m, and the embedded anchor must equal the
  scene centroid. A render built from a different footprint/height fails closed.
* Selector paths must not alias: no duplicate render selectors, no collisions
  with scene assets, catalog assets, the license, the package or provenance
  paths, and no selector that shadows another as a directory prefix. Two
  ``object_id``s may not share one source file.
* The authored base catalog must already be a complete strict
  ``UrbanWorldCatalog`` (license, weather, geoid and terrain assets); this
  module never invents terrain, license or weather values, and it refuses to
  overwrite an already-populated ``building_render`` section.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aero_bench.world.scene_authoring import (
    AUTHORING_SCHEMA_VERSION,
    OBJECTS_SCHEMA_VERSION,
    PACKAGE_RELATIVE_PATH,
    PROVENANCE_RELATIVE_PATH,
    SCENE_ASSET_SELECTOR_PREFIX,
    SCENE_COMPILER_SCHEMA_VERSION,
    CatalogBuildingRender,
    UrbanWorldCatalog,
)

MERGE_EVIDENCE_SCHEMA_VERSION = "aero-bench.building-render-merge/v1"
PREREQUISITES_SCHEMA_VERSION = "aero-bench.authoring-prerequisites/v1"

FRAGMENT_SECTION = "building_render"
FRAGMENT_MEDIA_TYPE = "model/gltf-binary"
FRAGMENT_SOURCE_FRAME = "asset_local"

#: Dimension tolerance of the render-vs-scene envelope cross-check, matching the
#: accepted scale-out validation (float32 packing sits far below it).
ENVELOPE_TOLERANCE_M = 1e-3
#: Tolerance for the embedded anchor vs the recomputed scene centroid. The GLB
#: rounds its anchor to 9 decimals, so 1e-6 m is already generous.
ANCHOR_TOLERANCE_M = 1e-6

_FRAGMENT_DOCUMENT_KEYS = frozenset(
    {"schema_version", "section", "note", "glb_digests", "building_render"}
)
_DIGEST_RECORD_KEYS = frozenset({"sha256", "bytes", "style_id"})

_SCENE_RELATIVES = (
    "osm/effective.osm.json",
    "metadata/objects.json",
    "gazebo/scene.sdf",
)

_GLB_MAGIC = 0x46546C67  # 'glTF'
_CHUNK_JSON = 0x4E4F534A  # 'JSON'
_CHUNK_BIN = 0x004E4942  # 'BIN\0'
_COMPONENT_FLOAT = 5126

#: Checks this stage executes, recorded verbatim in the evidence document.
CHECKS = (
    "fragment_strict_json",
    "fragment_schema_version",
    "fragment_section",
    "fragment_known_keys",
    "entry_strict_contract",
    "entry_asset_local_gltf",
    "unique_object_ids",
    "unique_selectors",
    "digest_record_contract",
    "digest_id_coverage",
    "scene_manifest_verified",
    "scene_objects_verified",
    "building_id_set_equality",
    "source_file_exists",
    "sha256_and_size_match",
    "glb_container_v2",
    "glb_accessor_bounds_integrity",
    "glb_source_scene_identity",
    "geometry_envelope",
    "path_aliasing",
    "unique_source_files",
)


class BuildingRenderMergeError(ValueError):
    """A strict fragment/scene/catalog contract violation during the merge."""


# --------------------------------------------------------------------------
# strict JSON loading (duplicate keys never collapse silently)
# --------------------------------------------------------------------------


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise BuildingRenderMergeError(
                f"duplicate JSON object key {key!r} is not allowed"
            )
        document[key] = value
    return document


def _load_json_strict(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BuildingRenderMergeError(
            f"{label} cannot be read: {path}: {exc}"
        ) from exc
    try:
        document = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise BuildingRenderMergeError(
            f"{label} is not valid UTF-8 JSON: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BuildingRenderMergeError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise BuildingRenderMergeError(f"{label} must be a JSON object document")
    return document, raw


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# compiled scene (identity + collision/geometry envelope source)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SceneBuilding:
    object_id: str
    ring: tuple[tuple[float, float], ...]  # open ENU ring, positive area
    base_m: float
    top_m: float


@dataclass(frozen=True, slots=True)
class CompiledScene:
    root: Path
    manifest_sha256: str
    objects_sha256: str
    buildings: dict[str, SceneBuilding]
    road_ids: tuple[str, ...]
    road_ids_without_width_tag: tuple[str, ...]


def _open_ring(raw: object, label: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, list) or len(raw) < 4:
        raise BuildingRenderMergeError(f"{label} must be a closed footprint array")
    points: list[tuple[float, float]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise BuildingRenderMergeError(
                f"{label}[{index}] must be an [east, north] pair"
            )
        east, north = item[0], item[1]
        if isinstance(east, bool) or not isinstance(east, (int, float)):
            raise BuildingRenderMergeError(f"{label}[{index}][0] must be a number")
        if isinstance(north, bool) or not isinstance(north, (int, float)):
            raise BuildingRenderMergeError(f"{label}[{index}][1] must be a number")
        if not math.isfinite(float(east)) or not math.isfinite(float(north)):
            raise BuildingRenderMergeError(f"{label}[{index}] must be finite")
        points.append((float(east), float(north)))
    if points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or len(set(points)) != len(points):
        raise BuildingRenderMergeError(
            f"{label} must contain at least three distinct vertices"
        )
    area2 = _ring_area2(points)
    if abs(area2) * 0.5 <= 0.0:
        raise BuildingRenderMergeError(f"{label} must enclose a positive area")
    return tuple(points)


def _ring_area2(
    points: list[tuple[float, float]] | tuple[tuple[float, float], ...],
) -> float:
    area2 = 0.0
    for index, (east, north) in enumerate(points):
        next_east, next_north = points[(index + 1) % len(points)]
        area2 += east * next_north - next_east * north
    return area2


def _ring_centroid(
    points: tuple[tuple[float, float], ...],
) -> tuple[float, float]:
    """Area-weighted centroid of the open ENU ring (shoelace convention)."""

    area2 = _ring_area2(points)
    if abs(area2) < 1e-12:  # pragma: no cover - degenerate rings are rejected
        raise BuildingRenderMergeError("footprint centroid is undefined")
    east = 0.0
    north = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        cross = x0 * y1 - x1 * y0
        east += (x0 + x1) * cross
        north += (y0 + y1) * cross
    return east / (3.0 * area2), north / (3.0 * area2)


def load_compiled_scene(scene_root: Path) -> CompiledScene:
    """Verify the compiled scene against its own manifest and load identity.

    Every declared output the merge depends on is re-digested against the real
    bytes; a tampered or stale scene fails closed before any fragment check.
    """

    root = Path(scene_root)
    if not root.is_dir():
        raise BuildingRenderMergeError(
            f"compiled urban scene root does not exist: {root}"
        )
    manifest, manifest_bytes = _load_json_strict(
        root / "manifest.json", "compiled scene manifest"
    )
    if manifest.get("schema_version") != SCENE_COMPILER_SCHEMA_VERSION:
        raise BuildingRenderMergeError(
            "compiled scene manifest schema_version must be "
            f"{SCENE_COMPILER_SCHEMA_VERSION!r}, found {manifest.get('schema_version')!r}"
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise BuildingRenderMergeError("compiled scene manifest outputs must be a list")
    declared: dict[str, tuple[str, int]] = {}
    for index, record in enumerate(outputs):
        if not isinstance(record, dict):
            raise BuildingRenderMergeError(
                f"compiled scene manifest outputs[{index}] must be an object"
            )
        relative = record.get("path")
        digest = record.get("sha256")
        size = record.get("byte_size")
        if not isinstance(relative, str) or not relative:
            raise BuildingRenderMergeError(
                f"compiled scene manifest outputs[{index}].path must be a string"
            )
        if not isinstance(digest, str) or len(digest) != 64:
            raise BuildingRenderMergeError(
                f"compiled scene manifest outputs[{index}].sha256 must be a digest"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BuildingRenderMergeError(
                f"compiled scene manifest outputs[{index}].byte_size must be an int"
            )
        if relative in declared:
            raise BuildingRenderMergeError(
                f"compiled scene manifest declares {relative!r} twice"
            )
        declared[relative] = (digest, size)

    objects_relative = _SCENE_RELATIVES[1]
    if objects_relative not in declared:
        raise BuildingRenderMergeError(
            f"compiled scene manifest does not declare {objects_relative!r}"
        )
    for relative in _SCENE_RELATIVES:
        if relative not in declared:
            raise BuildingRenderMergeError(
                f"compiled scene manifest does not declare {relative!r}"
            )
        path = root / relative
        if not path.is_file():
            raise BuildingRenderMergeError(
                f"compiled scene is missing declared output {relative!r}: {path}"
            )
        data = path.read_bytes()
        expected_digest, expected_size = declared[relative]
        actual_digest = _sha256(data)
        if actual_digest != expected_digest:
            raise BuildingRenderMergeError(
                f"compiled scene output {relative!r} sha256 mismatch: "
                f"manifest {expected_digest}, real bytes {actual_digest}"
            )
        if len(data) != expected_size:
            raise BuildingRenderMergeError(
                f"compiled scene output {relative!r} byte size mismatch: "
                f"manifest {expected_size}, real bytes {len(data)}"
            )

    objects_bytes = (root / objects_relative).read_bytes()
    objects_sha256 = _sha256(objects_bytes)
    objects, _ = _load_json_strict(root / objects_relative, "metadata/objects.json")
    if objects.get("schema_version") != OBJECTS_SCHEMA_VERSION:
        raise BuildingRenderMergeError(
            "metadata/objects.json schema_version must be "
            f"{OBJECTS_SCHEMA_VERSION!r}, found {objects.get('schema_version')!r}"
        )
    if objects.get("coordinate_frame") != "ENU":
        raise BuildingRenderMergeError(
            "metadata/objects.json coordinate_frame must be 'ENU'"
        )

    raw_buildings = objects.get("buildings")
    if not isinstance(raw_buildings, list) or not raw_buildings:
        raise BuildingRenderMergeError(
            "metadata/objects.json.buildings must be a non-empty array"
        )
    buildings: dict[str, SceneBuilding] = {}
    for index, raw in enumerate(raw_buildings):
        label = f"metadata/objects.json.buildings[{index}]"
        if not isinstance(raw, dict):
            raise BuildingRenderMergeError(f"{label} must be an object")
        if raw.get("kind") != "building":
            raise BuildingRenderMergeError(f"{label}.kind must be 'building'")
        object_id = raw.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise BuildingRenderMergeError(
                f"{label}.object_id must be a non-empty string"
            )
        if object_id in buildings:
            raise BuildingRenderMergeError(
                f"metadata/objects.json declares building {object_id!r} twice"
            )
        ring = _open_ring(raw.get("footprint_enu_m"), f"{label}.footprint_enu_m")
        base = raw.get("base_enu_up_m")
        top = raw.get("top_enu_up_m")
        if isinstance(base, bool) or not isinstance(base, (int, float)):
            raise BuildingRenderMergeError(f"{label}.base_enu_up_m must be a number")
        if isinstance(top, bool) or not isinstance(top, (int, float)):
            raise BuildingRenderMergeError(f"{label}.top_enu_up_m must be a number")
        if not math.isfinite(float(base)) or not math.isfinite(float(top)):
            raise BuildingRenderMergeError(f"{label} heights must be finite")
        if float(top) <= float(base):
            raise BuildingRenderMergeError(
                f"{label}.top_enu_up_m must exceed base_enu_up_m"
            )
        buildings[object_id] = SceneBuilding(
            object_id=object_id,
            ring=ring,
            base_m=float(base),
            top_m=float(top),
        )

    raw_roads = objects.get("roads")
    if not isinstance(raw_roads, list) or not raw_roads:
        raise BuildingRenderMergeError(
            "metadata/objects.json.roads must be a non-empty array"
        )
    road_ids: list[str] = []
    road_ids_without_width: list[str] = []
    for index, raw in enumerate(raw_roads):
        label = f"metadata/objects.json.roads[{index}]"
        if not isinstance(raw, dict):
            raise BuildingRenderMergeError(f"{label} must be an object")
        if raw.get("kind") != "road":
            raise BuildingRenderMergeError(f"{label}.kind must be 'road'")
        object_id = raw.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise BuildingRenderMergeError(
                f"{label}.object_id must be a non-empty string"
            )
        if object_id in road_ids:
            raise BuildingRenderMergeError(
                f"metadata/objects.json declares road {object_id!r} twice"
            )
        road_ids.append(object_id)
        width_tag = raw.get("width_osm_tag")
        if width_tag is None:
            road_ids_without_width.append(object_id)

    return CompiledScene(
        root=root,
        manifest_sha256=_sha256(manifest_bytes),
        objects_sha256=objects_sha256,
        buildings=buildings,
        road_ids=tuple(road_ids),
        road_ids_without_width_tag=tuple(road_ids_without_width),
    )


# --------------------------------------------------------------------------
# GLB container: real header, real JSON chunk, real POSITION floats
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlbBounds:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]


def _load_glb(data: bytes, label: str) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20:
        raise BuildingRenderMergeError(
            f"{label}: file is too small to be a GLB container"
        )
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC:
        raise BuildingRenderMergeError(f"{label}: not a GLB container (bad magic)")
    if version != 2:
        raise BuildingRenderMergeError(
            f"{label}: GLB version must be 2, found {version}"
        )
    if length != len(data):
        raise BuildingRenderMergeError(
            f"{label}: GLB declared length {length} != file size {len(data)}"
        )
    offset = 12
    document: dict[str, Any] | None = None
    binary = b""
    while offset < len(data):
        if offset + 8 > len(data):
            raise BuildingRenderMergeError(f"{label}: truncated GLB chunk header")
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        start = offset + 8
        end = start + chunk_length
        if end > len(data):
            raise BuildingRenderMergeError(f"{label}: GLB chunk exceeds file size")
        chunk = data[start:end]
        if chunk_type == _CHUNK_JSON and document is None:
            try:
                document = json.loads(chunk, object_pairs_hook=_no_duplicate_keys)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BuildingRenderMergeError(
                    f"{label}: GLB JSON chunk is not valid JSON: {exc}"
                ) from exc
        elif chunk_type == _CHUNK_BIN and not binary:
            binary = chunk
        offset = end
    if document is None:
        raise BuildingRenderMergeError(f"{label}: GLB has no JSON chunk")
    if not isinstance(document, dict):
        raise BuildingRenderMergeError(f"{label}: GLB JSON chunk must be an object")
    if binary:
        return document, binary
    # A GLB may legally have no BIN chunk; the mesh buffer may still be embedded
    # only when there is a BIN chunk, so an external-uri buffer is rejected below.
    return document, b""


def _accessor_float_bounds(
    document: dict[str, Any],
    binary: bytes,
    label: str,
) -> GlbBounds:
    """Read real POSITION floats (never a declared-only shortcut)."""

    buffers = document.get("buffers")
    if not isinstance(buffers, list) or len(buffers) != 1:
        raise BuildingRenderMergeError(f"{label}: GLB must declare exactly one buffer")
    buffer_record = buffers[0]
    if not isinstance(buffer_record, dict):
        raise BuildingRenderMergeError(f"{label}: GLB buffer record must be an object")
    if buffer_record.get("uri") is not None:
        raise BuildingRenderMergeError(
            f"{label}: GLB buffer must be the self-contained BIN chunk, not an external uri"
        )
    if not binary:
        raise BuildingRenderMergeError(f"{label}: GLB has no BIN chunk for its buffer")
    declared_buffer_length = buffer_record.get("byteLength")
    if isinstance(declared_buffer_length, bool) or not isinstance(
        declared_buffer_length, int
    ):
        raise BuildingRenderMergeError(f"{label}: GLB buffer byteLength must be an int")
    if declared_buffer_length > len(binary):
        raise BuildingRenderMergeError(
            f"{label}: GLB buffer byteLength {declared_buffer_length} exceeds "
            f"BIN chunk {len(binary)}"
        )

    buffer_views = document.get("bufferViews")
    if not isinstance(buffer_views, list) or not buffer_views:
        raise BuildingRenderMergeError(f"{label}: GLB has no bufferViews")
    meshes = document.get("meshes")
    if not isinstance(meshes, list) or not meshes:
        raise BuildingRenderMergeError(f"{label}: GLB has no meshes")
    accessors = document.get("accessors")
    if not isinstance(accessors, list) or not accessors:
        raise BuildingRenderMergeError(f"{label}: GLB has no accessors")

    union_min = [math.inf, math.inf, math.inf]
    union_max = [-math.inf, -math.inf, -math.inf]
    position_count = 0
    for mesh_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            raise BuildingRenderMergeError(
                f"{label}: meshes[{mesh_index}] must be an object"
            )
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list) or not primitives:
            raise BuildingRenderMergeError(
                f"{label}: meshes[{mesh_index}] has no primitives"
            )
        for primitive_index, primitive in enumerate(primitives):
            if not isinstance(primitive, dict):
                raise BuildingRenderMergeError(
                    f"{label}: primitives[{primitive_index}] must be an object"
                )
            attributes = primitive.get("attributes")
            if not isinstance(attributes, dict) or "POSITION" not in attributes:
                raise BuildingRenderMergeError(
                    f"{label}: primitive without POSITION attributes is unsupported"
                )
            accessor_index = attributes["POSITION"]
            if isinstance(accessor_index, bool) or not isinstance(accessor_index, int):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION accessor index must be an int"
                )
            if accessor_index >= len(accessors):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION accessor index out of range"
                )
            accessor = accessors[accessor_index]
            if not isinstance(accessor, dict):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION accessor must be an object"
                )
            if accessor.get("sparse") is not None:
                raise BuildingRenderMergeError(
                    f"{label}: sparse POSITION accessors are unsupported"
                )
            if accessor.get("componentType") != _COMPONENT_FLOAT:
                raise BuildingRenderMergeError(
                    f"{label}: POSITION componentType must be FLOAT (5126)"
                )
            if accessor.get("type") != "VEC3":
                raise BuildingRenderMergeError(f"{label}: POSITION type must be VEC3")
            count = accessor.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 3:
                raise BuildingRenderMergeError(
                    f"{label}: POSITION count must be an int >= 3"
                )
            view_index = accessor.get("bufferView")
            if isinstance(view_index, bool) or not isinstance(view_index, int):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION accessor needs a bufferView"
                )
            if view_index >= len(buffer_views):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION bufferView out of range"
                )
            view = buffer_views[view_index]
            if not isinstance(view, dict):
                raise BuildingRenderMergeError(f"{label}: bufferView must be an object")
            if view.get("buffer", 0) != 0:
                raise BuildingRenderMergeError(f"{label}: only buffer 0 is supported")
            view_offset = view.get("byteOffset", 0)
            accessor_offset = accessor.get("byteOffset", 0)
            if isinstance(view_offset, bool) or not isinstance(view_offset, int):
                raise BuildingRenderMergeError(
                    f"{label}: bufferView byteOffset must be an int"
                )
            if isinstance(accessor_offset, bool) or not isinstance(
                accessor_offset, int
            ):
                raise BuildingRenderMergeError(
                    f"{label}: accessor byteOffset must be an int"
                )
            stride = view.get("byteStride")
            if stride is None:
                stride = 12
            if isinstance(stride, bool) or not isinstance(stride, int) or stride < 12:
                raise BuildingRenderMergeError(
                    f"{label}: POSITION byteStride must be >= 12"
                )
            start = view_offset + accessor_offset
            last = start + (count - 1) * stride + 12
            if start < 0 or last > len(binary):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION accessor reads outside the BIN chunk"
                )
            mins = accessor.get("min")
            maxs = accessor.get("max")
            if (
                not isinstance(mins, list)
                or len(mins) != 3
                or not isinstance(maxs, list)
                or len(maxs) != 3
            ):
                raise BuildingRenderMergeError(
                    f"{label}: POSITION accessor must declare min/max (glTF 2.0)"
                )
            computed_min = [math.inf, math.inf, math.inf]
            computed_max = [-math.inf, -math.inf, -math.inf]
            for vertex in range(count):
                base = start + vertex * stride
                x, y, z = struct.unpack_from("<3f", binary, base)
                values = (x, y, z)
                for axis in range(3):
                    value = values[axis]
                    if value < computed_min[axis]:
                        computed_min[axis] = value
                    if value > computed_max[axis]:
                        computed_max[axis] = value
            for axis in range(3):
                declared_low = mins[axis]
                declared_high = maxs[axis]
                if not isinstance(declared_low, (int, float)) or isinstance(
                    declared_low, bool
                ):
                    raise BuildingRenderMergeError(
                        f"{label}: POSITION min must be numbers"
                    )
                if not isinstance(declared_high, (int, float)) or isinstance(
                    declared_high, bool
                ):
                    raise BuildingRenderMergeError(
                        f"{label}: POSITION max must be numbers"
                    )
                # The declared min/max may be authored from float64 mesh
                # coordinates before float32 packing, so allow the packing
                # quantum (far below the 1e-3 m envelope tolerance) while still
                # rejecting a metadata value that disagrees with the real bytes.
                if abs(float(declared_low) - computed_min[axis]) > 1e-4:
                    raise BuildingRenderMergeError(
                        f"{label}: POSITION accessor min[{axis}] "
                        f"{declared_low} != real binary {computed_min[axis]}"
                    )
                if abs(float(declared_high) - computed_max[axis]) > 1e-4:
                    raise BuildingRenderMergeError(
                        f"{label}: POSITION accessor max[{axis}] "
                        f"{declared_high} != real binary {computed_max[axis]}"
                    )
                if computed_min[axis] < union_min[axis]:
                    union_min[axis] = computed_min[axis]
                if computed_max[axis] > union_max[axis]:
                    union_max[axis] = computed_max[axis]
            position_count += 1
    if position_count == 0:
        raise BuildingRenderMergeError(f"{label}: GLB declares no POSITION geometry")
    return GlbBounds(
        minimum=(union_min[0], union_min[1], union_min[2]),
        maximum=(union_max[0], union_max[1], union_max[2]),
    )


# --------------------------------------------------------------------------
# fragment validation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StagedRender:
    object_id: str
    selector: str
    source: Path
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class ValidatedBuildingRenderFragment:
    fragment_path: Path
    fragment_sha256: str
    scene: CompiledScene
    entries: tuple[CatalogBuildingRender, ...]  # scene building order
    staged: tuple[StagedRender, ...]
    evidence: dict[str, Any]


def _resolve_source(
    fragment_dir: Path, entry: CatalogBuildingRender, label: str
) -> Path:
    source = Path(entry.file)
    if not source.is_absolute():
        source = fragment_dir / source
    if not source.is_file():
        raise BuildingRenderMergeError(
            f"{label}: source file for {entry.object_id!r} does not exist: {source}"
        )
    return source


def _check_path_aliasing(selectors: set[str]) -> None:
    ordered = sorted(selectors)
    for index, selector in enumerate(ordered):
        for other in ordered[index + 1 :]:
            if other.startswith(selector + "/"):
                raise BuildingRenderMergeError(
                    f"path aliasing: selector {selector!r} shadows {other!r} as a "
                    "directory prefix; staged bytes would collide"
                )
            if selector.startswith(other + "/"):
                raise BuildingRenderMergeError(
                    f"path aliasing: selector {other!r} shadows {selector!r} as a "
                    "directory prefix; staged bytes would collide"
                )


def _glb_extras_checks(
    *,
    document: dict[str, Any],
    object_id: str,
    scene: CompiledScene,
    label: str,
) -> tuple[float, float]:
    """Validate embedded provenance; return the embedded anchor (east, north)."""

    asset = document.get("asset")
    if not isinstance(asset, dict):
        raise BuildingRenderMergeError(f"{label}: GLB asset record missing")
    extras = asset.get("extras")
    if not isinstance(extras, dict):
        raise BuildingRenderMergeError(
            f"{label}: GLB asset.extras provenance block is required"
        )
    if extras.get("object_id") != object_id:
        raise BuildingRenderMergeError(
            f"{label}: embedded object_id {extras.get('object_id')!r} != {object_id!r}"
        )
    if extras.get("source_frame") != FRAGMENT_SOURCE_FRAME:
        raise BuildingRenderMergeError(
            f"{label}: embedded source_frame {extras.get('source_frame')!r} must be "
            f"{FRAGMENT_SOURCE_FRAME!r}"
        )
    frame = extras.get("frame")
    if not isinstance(frame, dict):
        raise BuildingRenderMergeError(f"{label}: embedded frame block is required")
    scene_block = extras.get("scene")
    if not isinstance(scene_block, dict):
        raise BuildingRenderMergeError(
            f"{label}: embedded scene provenance is required"
        )
    embedded_objects_sha = scene_block.get("objects_json_sha256")
    if embedded_objects_sha != scene.objects_sha256:
        raise BuildingRenderMergeError(
            f"{label}: stale source scene identity: GLB was rendered from "
            f"objects.json {embedded_objects_sha!r} but the compiled scene is "
            f"{scene.objects_sha256!r}"
        )
    anchor_east = frame.get("anchor_east_m")
    anchor_north = frame.get("anchor_north_m")
    if isinstance(anchor_east, bool) or not isinstance(anchor_east, (int, float)):
        raise BuildingRenderMergeError(
            f"{label}: embedded anchor_east_m must be a number"
        )
    if isinstance(anchor_north, bool) or not isinstance(anchor_north, (int, float)):
        raise BuildingRenderMergeError(
            f"{label}: embedded anchor_north_m must be a number"
        )
    embedded_base = frame.get("base_enu_up_m")
    if isinstance(embedded_base, bool) or not isinstance(embedded_base, (int, float)):
        raise BuildingRenderMergeError(
            f"{label}: embedded base_enu_up_m must be a number"
        )
    building = scene.buildings[object_id]
    if abs(float(embedded_base) - building.base_m) > 1e-9:
        raise BuildingRenderMergeError(
            f"{label}: embedded base_enu_up_m {embedded_base} != scene base "
            f"{building.base_m}"
        )
    return float(anchor_east), float(anchor_north)


def _envelope_checks(
    *,
    bounds: GlbBounds,
    anchor: tuple[float, float],
    scene_building: SceneBuilding,
    label: str,
) -> None:
    """Render envelope must reproduce the scene collision/geometry envelope."""

    ring = scene_building.ring
    centroid = _ring_centroid(ring)
    if (
        abs(anchor[0] - centroid[0]) > ANCHOR_TOLERANCE_M
        or abs(anchor[1] - centroid[1]) > ANCHOR_TOLERANCE_M
    ):
        raise BuildingRenderMergeError(
            f"{label}: embedded anchor ({anchor[0]}, {anchor[1]}) != scene footprint "
            f"centroid ({centroid[0]}, {centroid[1]}) within {ANCHOR_TOLERANCE_M} m"
        )
    easts = [point[0] for point in ring]
    norths = [point[1] for point in ring]
    height = scene_building.top_m - scene_building.base_m
    checks = (
        ("X/min", bounds.minimum[0], min(easts) - anchor[0]),
        ("X/max", bounds.maximum[0], max(easts) - anchor[0]),
        ("Z/min", bounds.minimum[2], -(max(norths) - anchor[1])),
        ("Z/max", bounds.maximum[2], -(min(norths) - anchor[1])),
        ("Y/min", bounds.minimum[1], 0.0),
        ("Y/max", bounds.maximum[1], height),
    )
    for name, actual, expected in checks:
        if abs(actual - expected) > ENVELOPE_TOLERANCE_M:
            raise BuildingRenderMergeError(
                f"{label}: geometry envelope violation: GLB {name} {actual} != "
                f"scene {expected} (tolerance {ENVELOPE_TOLERANCE_M} m)"
            )


def validate_building_render_fragment(
    *,
    fragment_path: Path,
    scene_root: Path,
    reserved_selectors: frozenset[str] | set[str] = (),
) -> ValidatedBuildingRenderFragment:
    """Validate a strict building-render fragment 1:1 against a compiled scene.

    Returns the entries in scene building order together with the exact-byte
    staging plan (selector -> source file -> SHA-256/size) and a
    machine-readable evidence document. Every rejection raises
    :class:`BuildingRenderMergeError`; nothing is ever skipped.
    """

    fragment = Path(fragment_path)
    if not fragment.is_file():
        raise BuildingRenderMergeError(
            f"building-render fragment does not exist: {fragment}"
        )
    document, fragment_bytes = _load_json_strict(fragment, "building-render fragment")
    fragment_sha256 = _sha256(fragment_bytes)

    unknown_keys = sorted(set(document) - _FRAGMENT_DOCUMENT_KEYS)
    if unknown_keys:
        raise BuildingRenderMergeError(
            f"building-render fragment has unknown top-level keys: {unknown_keys}"
        )
    if document.get("schema_version") != AUTHORING_SCHEMA_VERSION:
        raise BuildingRenderMergeError(
            "building-render fragment schema_version must be "
            f"{AUTHORING_SCHEMA_VERSION!r}, found {document.get('schema_version')!r}"
        )
    if document.get("section") != FRAGMENT_SECTION:
        raise BuildingRenderMergeError(
            f"building-render fragment section must be {FRAGMENT_SECTION!r}, "
            f"found {document.get('section')!r}"
        )
    raw_entries = document.get("building_render")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BuildingRenderMergeError(
            "building-render fragment building_render must be non-empty"
        )
    raw_digests = document.get("glb_digests")
    if not isinstance(raw_digests, dict) or not raw_digests:
        raise BuildingRenderMergeError(
            "building-render fragment glb_digests must be non-empty"
        )

    # ---- entries: live strict contract + this stage's stricter GLB rules ---- #
    entries: list[CatalogBuildingRender] = []
    seen_ids: set[str] = set()
    seen_selectors: set[str] = set()
    for index, raw in enumerate(raw_entries):
        label = f"building_render[{index}]"
        if not isinstance(raw, dict):
            raise BuildingRenderMergeError(f"{label} must be an object")
        try:
            entry = CatalogBuildingRender.model_validate(raw)
        except ValidationError as exc:
            raise BuildingRenderMergeError(
                f"{label} violates the strict contract: {exc}"
            ) from exc
        if entry.source_frame != FRAGMENT_SOURCE_FRAME:
            raise BuildingRenderMergeError(
                f"{label} ({entry.object_id!r}): wrong source frame "
                f"{entry.source_frame!r}; a building render must declare "
                f"{FRAGMENT_SOURCE_FRAME!r}"
            )
        if entry.media_type != FRAGMENT_MEDIA_TYPE:
            raise BuildingRenderMergeError(
                f"{label} ({entry.object_id!r}): wrong media_type "
                f"{entry.media_type!r}; expected {FRAGMENT_MEDIA_TYPE!r}"
            )
        if not entry.path.endswith(".glb"):
            raise BuildingRenderMergeError(
                f"{label} ({entry.object_id!r}): selector {entry.path!r} must end "
                "with .glb for a GLB render"
            )
        if entry.object_id in seen_ids:
            raise BuildingRenderMergeError(
                f"duplicate building_render object_id {entry.object_id!r} in the fragment"
            )
        if entry.path in seen_selectors:
            raise BuildingRenderMergeError(
                f"path aliasing: selector {entry.path!r} is claimed by more than one "
                "building_render entry"
            )
        seen_ids.add(entry.object_id)
        seen_selectors.add(entry.path)
        entries.append(entry)

    # ---- digest records --------------------------------------------------- #
    digests: dict[str, tuple[str, int]] = {}
    for object_id, record in raw_digests.items():
        label = f"glb_digests[{object_id!r}]"
        if not isinstance(record, dict):
            raise BuildingRenderMergeError(f"{label} must be an object")
        unknown = sorted(set(record) - _DIGEST_RECORD_KEYS)
        if unknown:
            raise BuildingRenderMergeError(f"{label} has unknown keys: {unknown}")
        digest = record.get("sha256")
        size = record.get("bytes")
        if not isinstance(digest, str) or len(digest) != 64 or digest != digest.lower():
            raise BuildingRenderMergeError(
                f"{label}.sha256 must be a lowercase SHA-256 hex"
            )
        try:
            int(digest, 16)
        except ValueError as exc:
            raise BuildingRenderMergeError(
                f"{label}.sha256 must be hexadecimal"
            ) from exc
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise BuildingRenderMergeError(f"{label}.bytes must be a positive int")
        digests[object_id] = (digest, size)
    if set(digests) != seen_ids:
        only_digests = sorted(set(digests) - seen_ids)
        only_entries = sorted(seen_ids - set(digests))
        raise BuildingRenderMergeError(
            "glb_digests and building_render must cover the same object_ids; "
            f"only in glb_digests: {only_digests}, only in building_render: {only_entries}"
        )

    # ---- compiled scene identity ------------------------------------------ #
    scene = load_compiled_scene(scene_root)

    # ---- 1:1 building id set ---------------------------------------------- #
    scene_ids = set(scene.buildings)
    missing = sorted(scene_ids - seen_ids)
    extra = sorted(seen_ids - scene_ids)
    if missing:
        raise BuildingRenderMergeError(
            f"{len(missing)} of {len(scene_ids)} compiled-scene buildings have no "
            f"building_render fragment entry; missing object_ids: {missing}"
        )
    if extra:
        raise BuildingRenderMergeError(
            f"building-render fragment declares {len(extra)} object_ids that are "
            f"not buildings of the compiled scene; extra object_ids: {extra}"
        )

    # ---- path aliasing against every other staged selector ----------------- #
    bundle_selectors = set(reserved_selectors)
    bundle_selectors.update(
        {f"{SCENE_ASSET_SELECTOR_PREFIX}{relative}" for relative in _SCENE_RELATIVES}
    )
    bundle_selectors.add(PACKAGE_RELATIVE_PATH)
    bundle_selectors.add(PROVENANCE_RELATIVE_PATH)
    collisions = sorted(
        {entry.path for entry in entries if entry.path in bundle_selectors}
    )
    if collisions:
        raise BuildingRenderMergeError(
            f"path aliasing: building_render selectors collide with reserved bundle "
            f"selectors: {collisions}"
        )
    bundle_selectors.update(entry.path for entry in entries)
    _check_path_aliasing(bundle_selectors)

    # ---- per-building: exact bytes, identity, envelope --------------------- #
    fragment_dir = fragment.parent
    by_id = {entry.object_id: entry for entry in entries}
    staged: list[StagedRender] = []
    source_by_id: dict[str, Path] = {}
    total_bytes = 0
    combined = hashlib.sha256()
    for object_id in sorted(scene.buildings):
        entry = by_id[object_id]
        label = f"building_render[{object_id!r}]"
        source = _resolve_source(fragment_dir, entry, label)
        data = source.read_bytes()
        actual_digest = _sha256(data)
        expected_digest, expected_size = digests[object_id]
        if actual_digest != expected_digest:
            raise BuildingRenderMergeError(
                f"{label}: sha256 mismatch: glb_digests {expected_digest}, "
                f"real bytes {actual_digest} ({source})"
            )
        if len(data) != expected_size:
            raise BuildingRenderMergeError(
                f"{label}: size mismatch: glb_digests {expected_size} B, "
                f"real bytes {len(data)} B ({source})"
            )
        document_glb, binary = _load_glb(data, label)
        anchor = _glb_extras_checks(
            document=document_glb, object_id=object_id, scene=scene, label=label
        )
        bounds = _accessor_float_bounds(document_glb, binary, label)
        _envelope_checks(
            bounds=bounds,
            anchor=anchor,
            scene_building=scene.buildings[object_id],
            label=label,
        )
        staged.append(
            StagedRender(
                object_id=object_id,
                selector=entry.path,
                source=source,
                sha256=actual_digest,
                byte_size=len(data),
            )
        )
        source_by_id[object_id] = source
        total_bytes += len(data)
        # Same convention as the scale-out validation: concat(sorted filename ||
        # bytes), so the digest cross-checks against its published value.
        combined.update(Path(entry.path).name.encode("utf-8"))
        combined.update(data)

    # ---- unique source files (one file never serves two object_ids) -------- #
    by_source: dict[Path, list[str]] = {}
    for object_id, source in source_by_id.items():
        by_source.setdefault(source, []).append(object_id)
    shared = sorted(
        (sorted(ids), str(source)) for source, ids in by_source.items() if len(ids) > 1
    )
    if shared:
        raise BuildingRenderMergeError(
            f"path aliasing: source files shared by multiple object_ids: {shared}"
        )

    ordered_entries = tuple(by_id[object_id] for object_id in scene.buildings)
    ordered_staged = tuple(staged)
    evidence: dict[str, Any] = {
        "schema_version": MERGE_EVIDENCE_SCHEMA_VERSION,
        "checks": list(CHECKS),
        "fragment": {
            "path": str(fragment),
            "sha256": fragment_sha256,
            "entry_count": len(entries),
            "digest_count": len(digests),
        },
        "scene": {
            "root": str(scene.root),
            "manifest_sha256": scene.manifest_sha256,
            "objects_json_sha256": scene.objects_sha256,
            "building_count": len(scene.buildings),
            "road_count": len(scene.road_ids),
            "road_count_without_width_tag": len(scene.road_ids_without_width_tag),
        },
        "staged": {
            "count": len(ordered_staged),
            "total_bytes": total_bytes,
            "combined_sha256": combined.hexdigest(),
            "entries": [
                {
                    "object_id": item.object_id,
                    "selector": item.selector,
                    "source": str(item.source),
                    "sha256": item.sha256,
                    "bytes": item.byte_size,
                }
                for item in ordered_staged
            ],
        },
        "envelope_tolerance_m": ENVELOPE_TOLERANCE_M,
        "anchor_tolerance_m": ANCHOR_TOLERANCE_M,
    }
    return ValidatedBuildingRenderFragment(
        fragment_path=fragment,
        fragment_sha256=fragment_sha256,
        scene=scene,
        entries=ordered_entries,
        staged=ordered_staged,
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# merge with an authored urban world catalog
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildingRenderMergeResult:
    output_catalog_path: Path
    output_catalog_sha256: str
    validated: ValidatedBuildingRenderFragment
    evidence: dict[str, Any]


def _load_catalog_document(
    catalog_path: Path,
) -> tuple[dict[str, Any], UrbanWorldCatalog, bytes]:
    if not catalog_path.is_file():
        raise BuildingRenderMergeError(
            f"authored world catalog does not exist: {catalog_path}"
        )
    document, raw = _load_json_strict(catalog_path, "authored world catalog")
    try:
        catalog = UrbanWorldCatalog.model_validate(document)
    except ValidationError as exc:
        raise BuildingRenderMergeError(
            f"authored world catalog is not a complete strict UrbanWorldCatalog: {exc}"
        ) from exc
    return document, catalog, raw


def _load_authored_catalog(
    catalog_path: Path,
) -> tuple[dict[str, Any], UrbanWorldCatalog, bytes]:
    document, catalog, raw = _load_catalog_document(catalog_path)
    if catalog.building_render:
        raise BuildingRenderMergeError(
            "authored world catalog already declares building_render entries "
            f"({len(catalog.building_render)}); this stage merges the fragment "
            "into an empty building_render section and never overwrites one"
        )
    return document, catalog, raw


def _catalog_reserved_selectors(catalog: UrbanWorldCatalog) -> set[str]:
    reserved = {catalog.license_path}
    reserved.update(asset.path for asset in catalog.assets)
    return reserved


def _relative_source(source: Path, output_dir: Path) -> str:
    try:
        return os.path.relpath(source, output_dir)
    except ValueError as exc:  # pragma: no cover - different drives (Windows)
        raise BuildingRenderMergeError(
            f"cannot express source {source} relative to {output_dir}"
        ) from exc


def merge_building_render_fragment(
    *,
    fragment_path: Path,
    scene_root: Path,
    catalog_path: Path,
    output_catalog_path: Path,
) -> BuildingRenderMergeResult:
    """Merge a strict fragment into an authored catalog and pin exact bytes.

    The written output catalog is a complete strict ``UrbanWorldCatalog``. All
    filesystem path fields it carries (``license_file``, catalog asset
    ``file`` and ``building_render[].file``) are re-expressed relative to the
    output catalog's own directory, pointing at the original sources -- so the
    merged document resolves the fragment's exact bytes wherever it is written,
    and :func:`author_urban_world_package` stages and digests those bytes.
    """

    base_document, base_catalog, base_bytes = _load_authored_catalog(Path(catalog_path))
    reserved = _catalog_reserved_selectors(base_catalog)
    validated = validate_building_render_fragment(
        fragment_path=Path(fragment_path),
        scene_root=Path(scene_root),
        reserved_selectors=reserved,
    )

    input_dir = Path(catalog_path).parent
    output_path = Path(output_catalog_path)
    output_dir = output_path.parent

    def _rebase_file(value: str) -> str:
        source = Path(value)
        if not source.is_absolute():
            source = input_dir / source
        return _relative_source(source, output_dir)

    merged_entries: list[dict[str, Any]] = []
    for entry in validated.entries:
        dumped = entry.model_dump(mode="json")
        source = next(
            item.source
            for item in validated.staged
            if item.object_id == entry.object_id
        )
        dumped["file"] = _relative_source(source, output_dir)
        merged_entries.append(dumped)
    merged_document = dict(base_document)
    merged_document["license_file"] = _rebase_file(base_document["license_file"])
    merged_document["assets"] = [
        {**asset, "file": _rebase_file(asset["file"])}
        for asset in base_document["assets"]
    ]
    merged_document["building_render"] = merged_entries
    try:
        merged_catalog = UrbanWorldCatalog.model_validate(merged_document)
    except ValidationError as exc:
        raise BuildingRenderMergeError(
            f"merged catalog violates the strict UrbanWorldCatalog contract: {exc}"
        ) from exc
    if len(merged_catalog.building_render) != len(
        validated.entries
    ):  # pragma: no cover
        raise BuildingRenderMergeError("merged catalog dropped building_render entries")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_bytes = (
        json.dumps(merged_document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    if output_path.exists() and output_path.read_bytes() != output_bytes:
        raise BuildingRenderMergeError(
            f"output catalog already exists with different bytes: {output_path}"
        )
    output_path.write_bytes(output_bytes)

    evidence = dict(validated.evidence)
    evidence["catalog"] = {
        "input_path": str(catalog_path),
        "input_sha256": _sha256(base_bytes),
        "output_path": str(output_path),
        "output_sha256": _sha256(output_bytes),
        "license_path": base_catalog.license_path,
        "asset_count": len(base_catalog.assets),
        "weather_sample_count": len(base_catalog.weather),
        "road_width_count": len(base_catalog.road_width_m),
        "merged_building_render_count": len(merged_catalog.building_render),
    }
    return BuildingRenderMergeResult(
        output_catalog_path=output_path,
        output_catalog_sha256=_sha256(output_bytes),
        validated=validated,
        evidence=evidence,
    )


def verify_staged_render_bytes(
    bundle_root: Path, staged: tuple[StagedRender, ...]
) -> int:
    """Re-hash every staged render selector against the pinned fragment bytes.

    Returns the number of verified renders. Any missing, extra-length or
    digest-mismatched staged file fails closed.
    """

    root = Path(bundle_root)
    total = 0
    for item in staged:
        path = root / item.selector
        if not path.is_file():
            raise BuildingRenderMergeError(
                f"staged bundle is missing render selector {item.selector!r}: {path}"
            )
        data = path.read_bytes()
        if len(data) != item.byte_size:
            raise BuildingRenderMergeError(
                f"staged render {item.selector!r} size {len(data)} != pinned "
                f"{item.byte_size} B"
            )
        digest = _sha256(data)
        if digest != item.sha256:
            raise BuildingRenderMergeError(
                f"staged render {item.selector!r} sha256 {digest} != pinned {item.sha256}"
            )
        total += 1
    return total


# --------------------------------------------------------------------------
# precise prerequisite report (never invents terrain/license/weather)
# --------------------------------------------------------------------------


def assess_authoring_prerequisites(
    *,
    scene_root: Path,
    catalog_path: Path | None = None,
    validated_fragment: ValidatedBuildingRenderFragment | None = None,
    road_width_catalog_path: Path | None = None,
) -> dict[str, Any]:
    """Report exactly which ``author_urban_world_package`` inputs exist.

    Missing inputs are listed as blocking requirements; this function never
    synthesises terrain, license or weather values to make a package buildable.
    """

    blocking: list[str] = []
    scene: CompiledScene | None = None
    scene_section: dict[str, Any]
    try:
        scene = load_compiled_scene(Path(scene_root))
        scene_section = {
            "status": "present",
            "root": str(scene.root),
            "manifest_sha256": scene.manifest_sha256,
            "objects_json_sha256": scene.objects_sha256,
            "building_count": len(scene.buildings),
            "road_count": len(scene.road_ids),
            "road_count_without_width_tag": len(scene.road_ids_without_width_tag),
        }
    except BuildingRenderMergeError as exc:
        scene_section = {"status": "invalid", "error": str(exc)}
        blocking.append(f"compiled urban scene is invalid: {exc}")

    render_section: dict[str, Any]
    if validated_fragment is not None:
        render_section = {
            "status": "validated",
            "fragment_path": str(validated_fragment.fragment_path),
            "fragment_sha256": validated_fragment.fragment_sha256,
            "entry_count": len(validated_fragment.entries),
            "staged_bytes": sum(item.byte_size for item in validated_fragment.staged),
        }
    else:
        render_section = {"status": "not_validated"}

    catalog_section: dict[str, Any]
    requirements: list[dict[str, str]] = []
    catalog_ready = False
    loaded_catalog: UrbanWorldCatalog | None = None
    if catalog_path is None:
        catalog_section = {"status": "missing", "path": None}
        requirements = [
            {
                "requirement": "authored UrbanWorldCatalog JSON document",
                "status": "missing",
                "detail": (
                    "no authored catalog path supplied; authoring needs an explicit "
                    "aero-bench.urban-world-authoring/v1 document"
                ),
            },
            {
                "requirement": "license_id + license file bytes",
                "status": "missing",
                "detail": "supplied by the authored catalog; never generated here",
            },
            {
                "requirement": ">= 1 authored WeatherSample",
                "status": "missing",
                "detail": "supplied by the authored catalog; never generated here",
            },
            {
                "requirement": "exactly one geoid_model asset",
                "status": "missing",
                "detail": "supplied by the authored catalog; never generated here",
            },
            {
                "requirement": "exactly one terrain_model asset",
                "status": "missing",
                "detail": "supplied by the authored catalog; never generated here",
            },
            {
                "requirement": "road_width_m covering every road without an OSM width tag",
                "status": "missing",
                "detail": (
                    "authored into the catalog; a validated width fragment may be "
                    "supplied via --road-width-catalog for coverage checking"
                ),
            },
        ]
        blocking.append(
            "authored world catalog not found: supply an authored "
            "UrbanWorldCatalog (license, weather, geoid_model, terrain_model)"
        )
    else:
        try:
            _, loaded_catalog, _ = _load_catalog_document(Path(catalog_path))
        except BuildingRenderMergeError as exc:
            catalog_section = {
                "status": "invalid",
                "path": str(catalog_path),
                "error": str(exc),
            }
            blocking.append(f"authored world catalog is unusable: {exc}")
            loaded_catalog = None
        if loaded_catalog is not None:
            catalog = loaded_catalog
            catalog_section = {
                "status": "present",
                "path": str(catalog_path),
                "asset_count": len(catalog.assets),
                "weather_sample_count": len(catalog.weather),
                "road_width_count": len(catalog.road_width_m),
                "building_render_count": len(catalog.building_render),
            }
            catalog_ready = True
            catalog_dir = Path(catalog_path).parent
            license_source = Path(catalog.license_file)
            if not license_source.is_absolute():
                license_source = catalog_dir / license_source
            if not license_source.is_file():
                catalog_ready = False
                blocking.append(
                    f"catalog license file does not exist: {license_source}"
                )
                catalog_section["license_file_status"] = "missing"
            else:
                catalog_section["license_file_status"] = "present"
            missing_asset_files = []
            for asset in catalog.assets:
                source = Path(asset.file)
                if not source.is_absolute():
                    source = catalog_dir / source
                if not source.is_file():
                    missing_asset_files.append(asset.asset_id)
            if missing_asset_files:
                catalog_ready = False
                blocking.append(
                    f"catalog asset files missing for asset_ids: {sorted(missing_asset_files)}"
                )
                catalog_section["asset_files_status"] = "missing"
            else:
                catalog_section["asset_files_status"] = "present"
            geoid_count = sum(
                1 for asset in catalog.assets if asset.asset_role == "geoid_model"
            )
            terrain_count = sum(
                1 for asset in catalog.assets if asset.asset_role == "terrain_model"
            )
            if geoid_count != 1 or terrain_count != 1:
                catalog_ready = False
                blocking.append(
                    "catalog must declare exactly one geoid_model and one terrain_model, "
                    f"found {geoid_count} and {terrain_count}"
                )
            catalog_section["ready"] = catalog_ready

    road_width_section: dict[str, Any]
    if scene is not None:
        missing_width = set(scene.road_ids_without_width_tag)
        catalog_width_ids: set[str] = set()
        if loaded_catalog is not None:
            catalog_width_ids = {item.object_id for item in loaded_catalog.road_width_m}
            unknown_width_ids = sorted(catalog_width_ids - set(scene.road_ids))
            uncovered = sorted(missing_width - catalog_width_ids)
            road_width_section = {
                "status": "present"
                if not uncovered and not unknown_width_ids
                else "incomplete",
                "source": "authored catalog road_width_m",
                "catalog_entry_count": len(catalog_width_ids),
                "roads_without_osm_width_tag": len(missing_width),
                "uncovered_roads": uncovered,
                "unknown_roads": unknown_width_ids,
            }
            if uncovered:
                blocking.append(
                    f"authored catalog lacks road_width_m for {len(uncovered)} roads "
                    f"that have no OSM width tag: {uncovered[:10]}"
                    f"{'...' if len(uncovered) > 10 else ''}"
                )
            if unknown_width_ids:
                blocking.append(
                    "authored catalog road_width_m references unknown road "
                    f"object_ids: {unknown_width_ids[:10]}"
                )
        elif missing_width:
            road_width_section = {
                "status": "missing",
                "source": None,
                "roads_without_osm_width_tag": len(missing_width),
                "detail": (
                    "every road without a real OSM width tag needs an authored "
                    "road_width_m entry inside the catalog"
                ),
            }
        else:  # pragma: no cover - this sample carries no tagged widths
            road_width_section = {
                "status": "scene_tags",
                "roads_without_osm_width_tag": 0,
            }

        if road_width_catalog_path is not None:
            widths, widths_raw = _load_json_strict(
                Path(road_width_catalog_path), "road-width catalog"
            )
            declared = widths.get("road_width_m")
            if not isinstance(declared, list):
                raise BuildingRenderMergeError(
                    "road-width catalog road_width_m must be a list"
                )
            covered = {
                item.get("object_id")
                for item in declared
                if isinstance(item, dict) and isinstance(item.get("object_id"), str)
            }
            fragment_uncovered = sorted(missing_width - covered)
            fragment_unknown = sorted(covered - set(scene.road_ids))
            road_width_section["fragment_input"] = {
                "path": str(road_width_catalog_path),
                "sha256": _sha256(widths_raw),
                "entry_count": len(declared),
                "uncovered_roads": fragment_uncovered,
                "unknown_roads": fragment_unknown,
                "status": (
                    "complete"
                    if not fragment_uncovered and not fragment_unknown
                    else "incomplete"
                ),
                "detail": (
                    "available width source; authoring reads road_width_m only from "
                    "the authored catalog, so these widths must be present there"
                ),
            }
            if fragment_uncovered:
                blocking.append(
                    f"road-width catalog fragment leaves {len(fragment_uncovered)} "
                    f"source roads without a width: {fragment_uncovered[:10]}"
                    f"{'...' if len(fragment_uncovered) > 10 else ''}"
                )
            if fragment_unknown:
                blocking.append(
                    "road-width catalog fragment references unknown road object_ids: "
                    f"{fragment_unknown[:10]}"
                )
            if (
                loaded_catalog is not None
                and road_width_section.get("uncovered_roads")
                and not fragment_uncovered
            ):
                blocking.append(
                    "the supplied road-width catalog fragment is complete but is not "
                    "part of the authored catalog; merge it into road_width_m before "
                    "authoring"
                )
    else:
        road_width_section = {
            "status": "unknown",
            "detail": "compiled scene unavailable",
        }

    if validated_fragment is None and render_section.get("status") != "validated":
        blocking.append(
            "building-render fragment has not been validated against the scene"
        )

    return {
        "schema_version": PREREQUISITES_SCHEMA_VERSION,
        "ready": not blocking,
        "scene": scene_section,
        "building_render": render_section,
        "authored_catalog": catalog_section,
        "road_widths": road_width_section,
        "blocking": blocking,
        "requirements": requirements,
        "note": (
            "This report only inspects inputs. No terrain, license or weather value "
            "is ever generated, defaulted or patched by this tool."
        ),
    }


__all__ = [
    "ANCHOR_TOLERANCE_M",
    "BuildingRenderMergeError",
    "BuildingRenderMergeResult",
    "CompiledScene",
    "ENVELOPE_TOLERANCE_M",
    "FRAGMENT_MEDIA_TYPE",
    "FRAGMENT_SECTION",
    "FRAGMENT_SOURCE_FRAME",
    "MERGE_EVIDENCE_SCHEMA_VERSION",
    "PREREQUISITES_SCHEMA_VERSION",
    "StagedRender",
    "ValidatedBuildingRenderFragment",
    "assess_authoring_prerequisites",
    "load_compiled_scene",
    "merge_building_render_fragment",
    "validate_building_render_fragment",
    "verify_staged_render_bytes",
]
