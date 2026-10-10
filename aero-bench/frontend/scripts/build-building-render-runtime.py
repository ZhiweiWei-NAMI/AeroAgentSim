#!/usr/bin/env python3
"""Compile validated building-render GLBs into a region-specific runtime layout.

Fail-closed: every input is pinned by SHA-256, every GLB is re-verified against
objects.json (anchor, height, POSITION envelope), and the published combined
digest is reproduced before anything is derived.

Runtime dedup (v2 manifest): canonical GLBs may repeat their embedded style
textures. After verification each building is
re-derived as a geometry-only GLB (images rewritten to content-addressed
`assets/<sha256>` URIs, every distinct texture stored exactly once) with
deterministic *art detail* added for near-view hierarchy: a parapet cornice with
recessed roof deck and up to two roof structures. Facade bay relief is an
explicit opt-in layer; the default keeps the canonical facade geometry and
uses authored metre-scale facade UVs. Shared lossless middle-region source
tiles and authored pane semantics are a separate explicit texture option.
The art
detail never leaves the canonical footprint, never rises above the canonical
top height, keeps source material parameters and original textures intact,
and is labelled as non-survey render art in `asset.extras.art_detail` and in
the manifest. Every derivation is verified against its canonical source
(envelope, footprint corners, image digests, art-detail contract) before
anything is staged; only verified derived assets are written.

The region, verified inputs and output locations are explicit command-line
arguments. The scene manifest supplies the region identity, mesh-pack path and
source-context pins. Outputs remain byte-deterministic for a fixed scene,
canonical render directory and art-detail option set.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]

BLOCK_SIZE_M = 120.0
ENVELOPE_TOL_M = 1e-3
ANCHOR_TOL_M = 1e-6

class BuildError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise BuildError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, pinned: str | None = None, label: str = "") -> object:
    if not path.is_file():
        fail(f"{label or path}: file does not exist")
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if pinned is not None and actual != pinned:
        fail(f"{label or path}: sha256 {actual} does not match pinned {pinned}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{label or path}: invalid JSON: {error}")


def file_reference(value: object, label: str) -> tuple[str, int]:
    reference = strict_object(value, label)
    if set(reference) != {"sha256", "size_bytes"}:
        fail(f"{label} must contain only sha256 and size_bytes")
    digest = reference.get("sha256")
    size = reference.get("size_bytes")
    if not isinstance(digest, str) or len(digest) != 64 \
            or any(character not in "0123456789abcdef" for character in digest):
        fail(f"{label}.sha256 must be a lowercase SHA-256 digest")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        fail(f"{label}.size_bytes must be a positive integer")
    return digest, size


def verified_json_reference(path: Path, reference: object, label: str) -> tuple[dict, bytes]:
    digest, size = file_reference(reference, label)
    if not path.is_file():
        fail(f"{label}: file does not exist at {path}")
    raw = path.read_bytes()
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        fail(f"{label}: bytes differ from the scene reference")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{label}: invalid JSON: {error}")
    return strict_object(value, label), raw


def strict_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def strict_array(value: object, label: str) -> list:
    if not isinstance(value, list):
        fail(f"{label} must be an array")
    return value


def footprint_centroid(ring: list) -> tuple[float, float]:
    points = [(float(p[0]), float(p[1])) for p in ring]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        fail("footprint ring has fewer than three vertices")
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area2) < 1e-9:
        fail("footprint ring encloses no area")
    return (cx / (3.0 * area2), cy / (3.0 * area2))


def read_glb(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    if len(raw) < 20:
        fail(f"{path.name}: not a GLB container")
    magic, version, length = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF" or version != 2 or length != len(raw):
        fail(f"{path.name}: bad GLB header")
    chunk_length, chunk_type = struct.unpack_from("<II", raw, 12)
    if chunk_type != 0x4E4F534A or 20 + chunk_length > len(raw):
        fail(f"{path.name}: missing JSON chunk")
    try:
        document = json.loads(raw[20:20 + chunk_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{path.name}: invalid glTF JSON: {error}")
    bin_start = 20 + chunk_length
    binary = b""
    if bin_start + 8 <= len(raw):
        bin_length, bin_type = struct.unpack_from("<II", raw, bin_start)
        if bin_type != 0x004E4942 or bin_start + 8 + bin_length > len(raw):
            fail(f"{path.name}: bad BIN chunk")
        binary = raw[bin_start + 8:bin_start + 8 + bin_length]
    return document, binary


def position_bounds(document: dict, binary: bytes) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    buffers = strict_array(document.get("buffers"), "glb buffers")
    if len(buffers) != 1 or buffers[0].get("uri") is not None:
        fail("glb must contain exactly one embedded buffer")
    if buffers[0].get("byteLength") != len(binary):
        fail("glb buffer byteLength mismatch")
    buffer_views = strict_array(document.get("bufferViews"), "glb bufferViews")
    accessors = strict_array(document.get("accessors"), "glb accessors")
    meshes = strict_array(document.get("meshes"), "glb meshes")
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = 0
    for mesh in meshes:
        for primitive in strict_array(mesh.get("primitives"), "glb primitives"):
            attributes = strict_object(primitive.get("attributes"), "primitive attributes")
            position_index = attributes.get("POSITION")
            if position_index is None:
                fail("glb primitive without POSITION")
            accessor = strict_object(accessors[position_index], "POSITION accessor")
            if accessor.get("componentType") != 5126 or accessor.get("type") != "VEC3":
                fail("POSITION accessor is not float32 VEC3")
            view = strict_object(buffer_views[accessor["bufferView"]], "bufferView")
            if view.get("buffer", 0) != 0:
                fail("POSITION accessor references a non-embedded buffer")
            stride = view.get("byteStride", 12)
            if stride != 12:
                fail("POSITION accessor stride is not tightly packed")
            start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            count = accessor.get("count")
            if not isinstance(count, int) or count < 3:
                fail("POSITION accessor count is invalid")
            need = start + (count - 1) * 12 + 12
            if need > len(binary):
                fail("POSITION accessor overruns the BIN chunk")
            seen += count
            for index in range(count):
                x, y, z = struct.unpack_from("<3f", binary, start + index * 12)
                lo[0] = min(lo[0], x); hi[0] = max(hi[0], x)
                lo[1] = min(lo[1], y); hi[1] = max(hi[1], y)
                lo[2] = min(lo[2], z); hi[2] = max(hi[2], z)
    if seen == 0:
        fail("glb has no POSITION data")
    return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


COMPONENT_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
ACCESSOR_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
                       "MAT2": 4, "MAT3": 9, "MAT4": 16}


def align4(value: int) -> int:
    return (value + 3) & ~3


def detect_image_media_type(data: bytes, declared: object, label: str) -> str:
    if data[:3] == b"\xff\xd8\xff":
        detected = "image/jpeg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        detected = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        detected = "image/webp"
    else:
        fail(f"{label}: embedded image format is not jpeg/png/webp")
    if declared is not None and declared != detected:
        fail(f"{label}: declared mimeType {declared} disagrees with magic bytes {detected}")
    return detected


def validate_accessors(document: dict, label: str) -> None:
    buffer_views = strict_array(document.get("bufferViews"), f"{label} bufferViews")
    for position, accessor in enumerate(strict_array(document.get("accessors"), f"{label} accessors")):
        acc = strict_object(accessor, f"{label} accessors[{position}]")
        if "sparse" in acc:
            fail(f"{label}: sparse accessors are not supported in derived GLBs")
        if "bufferView" not in acc:
            continue
        view = strict_object(buffer_views[acc["bufferView"]], f"{label} accessor view")
        component_size = COMPONENT_SIZE.get(acc.get("componentType"))
        if component_size is None:
            fail(f"{label} accessors[{position}]: unsupported componentType")
        components = ACCESSOR_COMPONENTS.get(acc.get("type"))
        if components is None:
            fail(f"{label} accessors[{position}]: unsupported type")
        count = acc.get("count")
        if not isinstance(count, int) or count < 1:
            fail(f"{label} accessors[{position}]: invalid count")
        acc_offset = acc.get("byteOffset", 0)
        total_offset = view.get("byteOffset", 0) + acc_offset
        if total_offset % component_size != 0:
            fail(f"{label} accessors[{position}]: offset {total_offset} breaks component alignment")
        element_size = component_size * components
        stride = view.get("byteStride", element_size)
        need = acc_offset + (count - 1) * stride + element_size
        if need > view.get("byteLength", 0):
            fail(f"{label} accessors[{position}]: data overruns its bufferView")


# --------------------------------------------------------------------------
# Deterministic art detail for the derived runtime GLB.
#
# Everything below is *art only*: it is generated from the verified footprint /
# height, never exceeds the source footprint or the source top height, and is
# labelled as art detail in `asset.extras.art_detail` and in the manifest. It is
# not survey data and must never be read as measurement.
# --------------------------------------------------------------------------

ART_DETAIL_SCHEMA = "aero-bench.building-render-art-detail/v1"
ART_DETAIL_CLASS = "derived_render_art_only_not_survey"
ART_DETAIL_NOTE_ZH = "派生渲染艺术细节（米制立面纹理密度、檐口女儿墙、立面分格凹凸、屋顶构筑物），非测绘事实"
ART_DETAIL_NOTE_EN = ("derived render art detail (metre-scale facade texture density, "
                      "parapet cornice, facade bay relief, "
                      "roof structures); not survey or measurement data")

# Facade bay relief: recessed panels between flush pilasters.
BAY_TARGET_M = 6.0
BAY_MAX = 8
BAY_MIN_SPAN_M = 2.0
EDGE_RELIEF_MIN_M = 2.5
PILASTER_W_M = 0.36
CHAMFER_M = 0.12
RECESS_M = 0.10
PANEL_MIN_M = 0.20

# Roof: deck recessed under a parapet, so nothing ever rises above top_up.
PARAPET_RATIO = 0.018
PARAPET_MIN_M = 0.5
PARAPET_MAX_M = 1.8
PARAPET_THICKNESS_M = 0.28
MITER_MAX_FACTOR = 3.0
MITER_MIN_DOT = 0.35

# Roof structures: up to two bulkheads on the recessed deck, flush with the
# parapet top so the source maximum height is never exceeded.
BULKHEAD_COUNT = 2
BULKHEAD_FRACTION = 0.22
BULKHEAD_MAX_W_M = 7.0
BULKHEAD_MAX_D_M = 5.5
BULKHEAD_MIN_EXTENT_M = 1.6
BULKHEAD_CLEARANCE_M = 0.3
BULKHEAD_PLACEMENT_TRIES = 4

MAX_DERIVED_VERTICES = 60_000
MAX_RUNTIME_PAYLOAD_BYTES = 14_000_000

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class FacadeTileProfile:
    """Pixel features of one source crop plus explicitly authored metre spacing.

    The canonical Unity materials specify scale (1,1), not a physical facade
    tile size. These profiles describe render art. Pane pitch and the reference
    window box were read from the unmodified albedo crop; storey spacing is an
    art choice and must never be interpreted as surveyed building levels.
    One metres-per-pixel value preserves brick courses and source pixel aspect.
    """

    atlas_set: str
    region_px: tuple[int, int, int, int]
    pane_pitch_px: Vec2
    reference_window_px: Vec2
    authored_storey_pitch_m: float
    pane_rows_per_storey: int = 1

    @property
    def metres_per_pixel(self) -> float:
        return self.authored_storey_pitch_m / self.pane_rows_per_storey / self.pane_pitch_px[1]

    @property
    def repeat_m(self) -> Vec2:
        left, top, right, bottom = self.region_px
        return ((right - left) * self.metres_per_pixel,
                (bottom - top) * self.metres_per_pixel)


# Profiles are tied to source textures, never to a city, building ID or height
# class. New source crops require an authored profile rather than a generic
# height-normalised UV fallback. Reference boxes measure a representative pane
# (single glazing pane for paired windows), not average surveyed windows.
FACADE_TILE_PROFILES: dict[str, FacadeTileProfile] = {
    "classical_dark_brick": FacadeTileProfile("classical", (170, 160, 410, 400), (49, 102), (29, 56), 3.2),
    "classical_ornate": FacadeTileProfile("classical", (815, 585, 1055, 825), (96, 86), (26, 44), 3.4),
    "classical_red_brick": FacadeTileProfile("classical", (110, 560, 370, 820), (70, 140), (27, 85), 3.6),
    "classical_stone_arch": FacadeTileProfile("classical", (440, 10, 700, 270), (64, 96), (38, 57), 3.6),
    "classical_tan_stone": FacadeTileProfile("classical", (460, 565, 720, 825), (50, 53), (22, 32), 3.2),
    "modern_beige_grid": FacadeTileProfile("modern", (373, 345, 613, 585), (19, 40), (16, 17), 3.2),
    "modern_beige_squares": FacadeTileProfile("modern", (710, 747, 950, 987), (46, 51), (30, 26), 3.2),
    "modern_blue_banded": FacadeTileProfile("modern", (373, 657, 633, 917), (21, 40), (18, 23), 3.2),
    "modern_bw_bands": FacadeTileProfile("modern", (20, 395, 260, 635), (16, 30), (13, 14), 3.2),
    "modern_concrete_band": FacadeTileProfile("modern", (383, 980, 623, 1220), (40, 40), (26, 13), 3.2),
    "modern_dark_curtain": FacadeTileProfile("modern", (995, 85, 1255, 345), (55, 135), (46, 106), 3.6),
    "modern_dark_grid": FacadeTileProfile("modern", (30, 677, 270, 917), (41, 36), (36, 31), 3.2, 2),
    "modern_grey_glass": FacadeTileProfile("modern", (1025, 1007, 1265, 1247), (26, 51), (22, 42), 3.2),
    "modern_grey_grid": FacadeTileProfile("modern", (10, 1000, 250, 1240), (32, 32), (28, 28), 3.2, 2),
    "modern_xbrace_glass": FacadeTileProfile("modern", (30, 40, 270, 280), (21, 21), (18, 18), 3.2, 2),
}
FACADE_TILING_SCHEMA = "aero-bench.authored-facade-tiling/v1"


@dataclass(frozen=True)
class FacadeMiddleTileProfile:
    """Authored repeating part and glazing semantics in the source RGB crop.

    Boxes use top-left image pixels and half-open bounds. They describe display
    materials, not observations of this scene's buildings. Source pane rows
    and columns are inspected in albedo, normal and Illum together; lit and
    unlit panes share the same semantics. No colour or normal pixels are made
    up, rotated, mirrored or resampled.
    """

    crop_px: tuple[int, int, int, int]
    pane_rows: int
    glass_columns_px: tuple[tuple[int, int], ...]
    glass_rows_px: tuple[tuple[int, int], ...]
    reference_window_px: Vec2
    opaque_polygons_px: tuple[tuple[Vec2, ...], ...] = ()


# Crops remove source roof cornices, mixed lower facades, and the large X motif.
# This registry is per source texture style. Scene IDs and building heights
# never select a crop, physical density or glass/opaque segmentation.
FACADE_MIDDLE_TILE_PROFILES: dict[str, FacadeMiddleTileProfile] = {
    "classical_dark_brick": FacadeMiddleTileProfile(
        (24, 119, 220, 223), 1,
        ((18, 29), (33, 44), (65, 77), (81, 92), (149, 161), (165, 176), (214, 225), (229, 240)),
        ((160, 175), (179, 191), (195, 212)), (26, 52)),
    "classical_ornate": FacadeMiddleTileProfile(
        (24, 88, 218, 150), 1,
        ((30, 51), (97, 118), (147, 168), (225, 240)),
        ((103, 121), (127, 141)), (21, 38)),
    "classical_red_brick": FacadeMiddleTileProfile(
        (18, 24, 230, 116), 1,
        ((29, 53), (79, 101), (145, 171), (202, 223)),
        ((34, 51), (56, 79), (85, 108)), (24, 74)),
    "classical_stone_arch": FacadeMiddleTileProfile(
        (2, 90, 260, 172), 1,
        ((16, 47), (83, 111), (150, 177), (215, 242)),
        ((99, 125), (131, 154)), (31, 55),
        (((16, 99), (31.5, 99), (25.5, 100.2), (20.5, 104), (17.2, 109), (16, 115)),
         ((31.5, 99), (47, 99), (47, 115), (45.8, 109), (42.5, 104), (37.5, 100.2)),
         ((83, 99), (97, 99), (91, 100.2), (87, 104), (84.2, 109), (83, 115)),
         ((97, 99), (111, 99), (111, 115), (109.8, 109), (107, 104), (103, 100.2)),
         ((150, 99), (163.5, 99), (157.5, 100.2), (154, 104), (151.2, 109), (150, 115)),
         ((163.5, 99), (177, 99), (177, 115), (175.8, 109), (173, 104), (169.5, 100.2)),
         ((215, 99), (228.5, 99), (222.5, 100.2), (219, 104), (216.2, 109), (215, 115)),
         ((228.5, 99), (242, 99), (242, 115), (240.8, 109), (238, 104), (234.5, 100.2)))),
    "classical_tan_stone": FacadeMiddleTileProfile(
        (60, 39, 258, 251), 4,
        ((7, 22), (34, 52), (105, 121), (133, 149), (204, 219), (231, 247)),
        ((59, 71), (75, 84), (113, 125), (129, 138), (167, 178), (182, 192),
         (221, 233), (237, 247)), (18, 26)),
    "modern_beige_grid": FacadeMiddleTileProfile(
        (12, 16, 238, 218), 5,
        ((12, 27), (30, 45), (49, 63), (67, 81), (85, 99), (103, 118), (121, 136),
         (139, 154), (157, 172), (175, 190), (193, 208), (211, 226), (229, 240)),
        ((33, 48), (72, 87), (111, 126), (150, 164), (189, 203)), (15, 15)),
    "modern_beige_squares": FacadeMiddleTileProfile(
        (6, 30, 234, 236), 4,
        ((0, 14), (31, 60), (78, 108), (124, 153), (170, 198), (215, 240)),
        ((52, 77), (103, 128), (154, 178), (204, 229)), (29, 25)),
    "modern_blue_banded": FacadeMiddleTileProfile(
        (10, 10, 260, 252), 6,
        tuple((x + 2, min(x + 19, 260)) for x in range(0, 260, 21)),
        ((0, 18), (36, 58), (77, 99), (116, 139), (157, 179), (197, 219), (237, 260)), (17, 22)),
    "modern_bw_bands": FacadeMiddleTileProfile(
        (0, 2, 240, 240), 8,
        tuple((x + 2, min(x + 14, 240)) for x in range(0, 240, 16)),
        ((9, 18), (38, 48), (67, 77), (96, 106), (125, 135), (155, 164),
         (184, 193), (214, 223)), (12, 10)),
    "modern_concrete_band": FacadeMiddleTileProfile(
        (56, 26, 236, 228), 5,
        ((0, 25), (35, 70), (81, 114), (126, 158), (172, 202), (217, 240)),
        ((0, 10), (39, 48), (79, 89), (120, 131), (160, 170), (200, 209)), (32, 11)),
    "modern_dark_curtain": FacadeMiddleTileProfile(
        (16, 14, 238, 94), 1,
        ((20, 69), (76, 121), (133, 178), (187, 232)),
        ((17, 91),), (45, 74),
        (((122, 83), (133, 83), (133, 101), (122, 101)),)),
    "modern_dark_grid": FacadeMiddleTileProfile(
        (32, 22, 235, 200), 5,
        ((0, 10), (15, 51), (56, 92), (97, 133), (138, 174), (179, 215), (220, 240)),
        ((0, 29), (34, 65), (70, 100), (105, 135), (141, 171), (176, 207), (211, 240)), (36, 31)),
    "modern_grey_glass": FacadeMiddleTileProfile(
        (16, 6, 222, 106), 2,
        ((0, 6), (13, 31), (38, 56), (63, 81), (88, 106), (113, 131),
         (138, 151), (159, 176), (184, 201), (209, 225), (233, 240)),
        # Dark transoms have continuous glass mullions in the source normal
        # map. They are authored as glazing even where Illum is unlit.
        ((0, 17), (21, 48), (53, 68), (73, 100), (105, 122)), (18, 27)),
    "modern_grey_grid": FacadeMiddleTileProfile(
        (6, 12, 232, 234), 7,
        ((0, 20), (26, 52), (59, 86), (92, 119), (125, 151), (158, 184),
         (190, 217), (223, 240)),
        ((0, 20), (27, 51), (59, 84), (91, 116), (123, 147), (155, 179),
         (187, 211), (219, 240)), (27, 25)),
    "modern_xbrace_glass": FacadeMiddleTileProfile(
        (100, 4, 142, 27), 1,
        ((96, 112), (118, 132), (138, 152)),
        ((0, 19), (24, 39)), (16, 17)),
}
FACADE_PANE_SCHEMA = "aero-bench.authored-facade-pane-profile/v1"


def middle_tile_profile(style: dict, source: FacadeTileProfile, label: str) -> tuple[FacadeTileProfile, dict]:
    profile = FACADE_MIDDLE_TILE_PROFILES.get(style.get("style_id"))
    if profile is None:
        fail(f"{label}: BLOCKED facade tiling: source style has no authored middle tile")
    left, top, right, bottom = profile.crop_px
    width, height = right - left, bottom - top
    source_width = source.region_px[2] - source.region_px[0]
    source_height = source.region_px[3] - source.region_px[1]
    if not 0 <= left < right <= source_width or not 0 <= top < bottom <= source_height:
        fail(f"{label}: BLOCKED facade tiling: middle crop leaves the source image")
    if profile.pane_rows <= 0:
        fail(f"{label}: BLOCKED facade tiling: middle crop needs positive authored pane rows")
    boxes = []
    for x0, x1 in profile.glass_columns_px:
        for y0, y1 in profile.glass_rows_px:
            a, b, c, d = max(x0, left), max(y0, top), min(x1, right), min(y1, bottom)
            if a < c and b < d:
                boxes.append([a - left, b - top, c - left, d - top])
    if not boxes:
        fail(f"{label}: BLOCKED facade tiling: middle crop has no authored glass panes")
    # Current opaque polygons all lie within or cross one crop edge. Clip them
    # to the rectangle, retaining their source shape instead of rotating it.
    polygons = []
    for polygon in profile.opaque_polygons_px:
        clipped = list(polygon)
        for axis, bound, keep_greater in ((0, left, True), (0, right, False),
                                         (1, top, True), (1, bottom, False)):
            out = []
            for i, a in enumerate(clipped):
                b = clipped[(i + 1) % len(clipped)]
                a_inside = a[axis] >= bound if keep_greater else a[axis] <= bound
                b_inside = b[axis] >= bound if keep_greater else b[axis] <= bound
                if a_inside:
                    out.append(a)
                if a_inside != b_inside:
                    fraction = (bound - a[axis]) / (b[axis] - a[axis])
                    out.append((a[0] + fraction * (b[0] - a[0]),
                                a[1] + fraction * (b[1] - a[1])))
            clipped = out
            if not clipped:
                break
        if len(clipped) >= 3:
            polygons.append([[round(x - left, 6), round(y - top, 6)] for x, y in clipped])
    source_left, source_top, _, _ = source.region_px
    active = FacadeTileProfile(
        source.atlas_set, (source_left + left, source_top + top,
                           source_left + right, source_top + bottom),
        (source.pane_pitch_px[0], height / profile.pane_rows), profile.reference_window_px,
        source.authored_storey_pitch_m, source.pane_rows_per_storey)
    semantics = {
        "schema_version": FACADE_PANE_SCHEMA,
        "provenance": "source-texture-art",
        "material_kind": "glass-and-masonry" if source.atlas_set == "modern" else "masonry-and-windows",
        "style_id": style["style_id"],
        "tile_size_px": [width, height],
        "source_crop_px": list(profile.crop_px),
        "coordinate_system": "image-top-left-pixel",
        "glass_rectangles_px": sorted(boxes),
        "opaque_polygons_px": polygons,
    }
    return active, semantics


@lru_cache(maxsize=45)
def crop_source_png(data: bytes, crop: tuple[int, int, int, int]) -> bytes:
    """Lossless source pixels encoded as deterministic RGB8 PNG, filter 0."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, ValueError) as error:
        fail(f"BLOCKED facade tiling: cannot decode source PNG: {error}")
    if image.format != "PNG" or image.mode != "RGB":
        fail("BLOCKED facade tiling: middle tiles require unchanged RGB PNG source pixels")
    left, top, right, bottom = crop
    if not 0 <= left < right <= image.width or not 0 <= top < bottom <= image.height:
        fail("BLOCKED facade tiling: requested crop leaves the decoded source PNG")
    tile = image.crop(crop)
    pixels = tile.tobytes()
    stride = tile.width * 3
    scanlines = b"".join(b"\x00" + pixels[start:start + stride]
                         for start in range(0, len(pixels), stride))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", tile.width, tile.height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines, level=9)) + chunk(b"IEND", b""))


def tile_channel_metadata(document: dict, binary: bytes, style: dict, label: str) -> dict:
    """Bind each active channel to its canonical source bytes and same crop."""
    profile = FACADE_MIDDLE_TILE_PROFILES.get(style.get("style_id"))
    if profile is None:
        fail(f"{label}: BLOCKED facade tiling: missing middle tile profile")
    material = document["materials"][0]
    channels = (("albedo", material["pbrMetallicRoughness"]["baseColorTexture"]),
                ("normal", material["normalTexture"]), ("illum", material["emissiveTexture"]))
    result = {}
    for channel, info in channels:
        image_index = document["textures"][info["index"]]["source"]
        view = document["bufferViews"][document["images"][image_index]["bufferView"]]
        start = view.get("byteOffset", 0)
        raw = bytes(binary[start:start + view["byteLength"]])
        tile = crop_source_png(raw, profile.crop_px)
        result[channel] = {
            "image_index": image_index,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
            "tile_sha256": hashlib.sha256(tile).hexdigest(), "tile_bytes": len(tile),
            "crop_px": list(profile.crop_px),
        }
    if len({value["image_index"] for value in result.values()}) != 3 or len(document["images"]) != 3:
        fail(f"{label}: BLOCKED facade tiling: exactly three distinct source channels are required")
    return result


def facade_tile_profile(document: dict, binary: bytes, style: dict,
                        height: float, label: str) -> FacadeTileProfile:
    """Require a known source crop and aligned repeat sampling on all channels."""
    prefix = f"{label}: BLOCKED facade tiling"
    profile = FACADE_TILE_PROFILES.get(style.get("style_id"))
    if profile is None:
        fail(f"{prefix}: source style has no authored tile profile")
    if style.get("atlas_set") != profile.atlas_set or style.get("region_px") != list(profile.region_px):
        fail(f"{prefix}: source texture crop disagrees with its authored tile profile")
    frame = strict_object(document.get("asset", {}).get("extras", {}).get("frame"), f"{label} frame")
    if frame.get("convention") != (
            "glTF 2.0: right-handed, Y up, metres, asset-local origin (no geodetic coordinates in-file)"):
        fail(f"{prefix}: source geometry must explicitly declare Y-up metres")
    if not math.isfinite(height) or height <= 0:
        fail(f"{prefix}: height must be positive finite metres")
    if not math.isfinite(profile.metres_per_pixel) or profile.metres_per_pixel <= 0:
        fail(f"{prefix}: authored tile spacing must be positive finite metres")

    materials = strict_array(document.get("materials"), f"{label} materials")
    if not materials:
        fail(f"{prefix}: source facade material is missing")
    material = strict_object(materials[0], f"{label} facade material")
    pbr = strict_object(material.get("pbrMetallicRoughness"), f"{label} facade PBR")
    textures = strict_array(document.get("textures"), f"{label} textures")
    samplers = strict_array(document.get("samplers"), f"{label} samplers")
    images = strict_array(document.get("images"), f"{label} images")
    views = strict_array(document.get("bufferViews"), f"{label} bufferViews")
    expected_size = (profile.region_px[2] - profile.region_px[0],
                     profile.region_px[3] - profile.region_px[1])
    for channel, info in (("albedo", pbr.get("baseColorTexture")),
                          ("normal", material.get("normalTexture")),
                          ("illum", material.get("emissiveTexture"))):
        if not isinstance(info, dict):
            fail(f"{prefix}: {channel} texture is missing")
        if info.get("texCoord", 0) != 0 or info.get("extensions"):
            fail(f"{prefix}: {channel} must share untransformed TEXCOORD_0")
        texture_index = info.get("index")
        if not isinstance(texture_index, int) or not 0 <= texture_index < len(textures):
            fail(f"{prefix}: {channel} texture reference is invalid")
        texture = textures[texture_index]
        sampler_index = texture.get("sampler")
        if not isinstance(sampler_index, int) or not 0 <= sampler_index < len(samplers):
            fail(f"{prefix}: {channel} repeat sampler is missing")
        sampler = samplers[sampler_index]
        if sampler.get("wrapS") != 10497 or sampler.get("wrapT") != 10497:
            fail(f"{prefix}: {channel} requires REPEAT on both source texture axes")
        image_index = texture.get("source")
        if not isinstance(image_index, int) or not 0 <= image_index < len(images):
            fail(f"{prefix}: {channel} image reference is invalid")
        image = images[image_index]
        view_index = image.get("bufferView")
        if not isinstance(view_index, int) or not 0 <= view_index < len(views):
            fail(f"{prefix}: {channel} embedded source image is missing")
        view = views[view_index]
        start = view.get("byteOffset", 0)
        data = binary[start:start + view["byteLength"]]
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            fail(f"{prefix}: {channel} source crop is not a measurable PNG")
        if struct.unpack_from(">II", data, 16) != expected_size:
            fail(f"{prefix}: {channel} source crop size disagrees with its tile profile")
    return profile


def metric_facade_uvs(positions: list[tuple], profile: FacadeTileProfile,
                      label: str) -> list[Vec2]:
    """Replace only UVs; edge distance is in metres, V remains upright Y-up.

    Every wall starts at U=0 and every base starts at V=1. Upper vertices may
    have negative V, which glTF REPEAT samples correctly. This avoids fitting
    a whole crop to the building height and preserves source window aspect.
    """
    tile_width, tile_height = profile.repeat_m
    result: list[Vec2] = []
    for index in range(0, len(positions), 4):
        a, b, c, d = positions[index:index + 4]
        span = math.hypot(b[0] - a[0], b[2] - a[2])
        if span <= 0 or not math.isfinite(span):
            fail(f"{label}: BLOCKED facade tiling: wall length must be positive finite metres")
        result.extend([(0.0, 1.0 - a[1] / tile_height),
                       (span / tile_width, 1.0 - b[1] / tile_height),
                       (span / tile_width, 1.0 - c[1] / tile_height),
                       (0.0, 1.0 - d[1] / tile_height)])
    return result


@dataclass(frozen=True)
class ArtDetailOptions:
    """Render layers selected at generation time, independently of scene IDs."""

    roof_detail: bool = True
    facade_relief: bool = False
    facade_middle_tiles: bool = False


@dataclass(frozen=True)
class BuildCommandOptions:
    scene: Path
    source_context: Path
    mesh_pack_manifest: Path
    scene_root: Path
    canonical_render_dir: Path
    output_dir: Path
    scene_output: Path
    art_detail: ArtDetailOptions


@dataclass(frozen=True)
class RegionBuildInputs:
    scene_id: str
    scene_config: dict
    scene_origin: dict
    pins: dict[str, str]
    expected_buildings: int
    expected_object_ids: frozenset[str]
    objects_path: Path
    effective_osm_path: Path
    scaleout_manifest_path: Path
    fragment_path: Path
    canonical_assets: Path
    pack_manifest_path: Path
    output_dir: Path
    scene_output: Path
    source_context_bytes: bytes


def geometry_layers(options: ArtDetailOptions) -> dict[str, bool]:
    # v1 art-detail layers are the two geometry layers. Texture provenance is
    # recorded under facade_tiling and material extras, not in this strict map.
    return {"roof_detail": options.roof_detail, "facade_relief": options.facade_relief}


def parse_options(argv: list[str] | None = None) -> BuildCommandOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True,
                        help="Input city-scene-preview/v1 manifest")
    parser.add_argument("--source-context", type=Path, required=True,
                        help="Source context whose bytes match the scene reference")
    parser.add_argument("--mesh-pack-manifest", type=Path, required=True,
                        help="Verified mesh-pack manifest whose bytes match the scene reference")
    parser.add_argument("--scene-root", type=Path, required=True,
                        help="Verified urban scene compiler output for this region")
    parser.add_argument("--canonical-render-dir", type=Path, required=True,
                        help="Canonical render manifest, catalog fragment and assets")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Region-named directory for the runtime manifest and assets")
    parser.add_argument("--scene-output", type=Path, required=True,
                        help="Output copy of the scene with the rebuilt runtime reference")
    parser.add_argument("--roof-detail", action=argparse.BooleanOptionalAction,
                        default=True, help="Generate in-envelope roof art (default: on)")
    parser.add_argument("--facade-relief", action=argparse.BooleanOptionalAction,
                        default=False, help="Opt in to the more expensive facade geometry")
    parser.add_argument("--facade-middle-tiles", action=argparse.BooleanOptionalAction,
                        default=False, help="Use shared lossless crops of authored repeatable source facade regions")
    args = parser.parse_args(argv)
    return BuildCommandOptions(
        scene=args.scene,
        source_context=args.source_context,
        mesh_pack_manifest=args.mesh_pack_manifest,
        scene_root=args.scene_root,
        canonical_render_dir=args.canonical_render_dir,
        output_dir=args.output_dir,
        scene_output=args.scene_output,
        art_detail=ArtDetailOptions(
            roof_detail=args.roof_detail,
            facade_relief=args.facade_relief,
            facade_middle_tiles=args.facade_middle_tiles,
        ),
    )


def resolve_region_build(command: BuildCommandOptions) -> RegionBuildInputs:
    scene_config = strict_object(load_json(command.scene), "city scene")
    if scene_config.get("schema_version") != "aero-bench.city-scene-preview/v1":
        fail("city scene schema mismatch")
    scene_building = strict_object(scene_config.get("building_render"), "scene building_render")
    scene_id = scene_building.get("source_scene_id")
    if not isinstance(scene_id, str) or not scene_id \
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in scene_id):
        fail("scene building_render.source_scene_id is invalid")
    building_base = scene_building.get("base_url")
    expected_building_base = f"/building-renders/{scene_id}/"
    if building_base != expected_building_base:
        fail(f"scene building_render.base_url must be {expected_building_base}")

    source_context, source_context_bytes = verified_json_reference(
        command.source_context,
        scene_building["source_context"],
        "building render source context",
    )
    if source_context.get("schema_version") != "aero-bench.building-render-source-context/v1":
        fail("building render source context schema mismatch")
    context_scene = strict_object(source_context.get("scene"), "source context scene")
    context_sources = strict_object(source_context.get("sources"), "source context sources")
    if context_scene.get("id") != scene_id or context_scene.get("coordinate_frame") != "ENU":
        fail("scene and building render source context identities differ")
    scene_origin = strict_object(context_scene.get("origin_wgs84"), "source context origin_wgs84")
    required_origin = {
        "latitude_deg", "longitude_deg", "ellipsoid_height_m", "amsl_m", "geoid_undulation_m",
    }
    if set(scene_origin) != required_origin \
            or any(not isinstance(scene_origin[key], (int, float)) or isinstance(scene_origin[key], bool)
                   or not math.isfinite(float(scene_origin[key])) for key in required_origin):
        fail("source context origin_wgs84 is invalid")

    mesh_pack = strict_object(scene_config.get("mesh_pack"), "scene mesh_pack")
    pack_base = mesh_pack.get("base_url")
    if not isinstance(pack_base, str) or not pack_base.endswith("/"):
        fail("scene mesh_pack.base_url must end in /")
    pack_manifest, _ = verified_json_reference(
        command.mesh_pack_manifest, mesh_pack.get("manifest"), "scene mesh_pack manifest")
    pack_manifest_digest, _ = file_reference(mesh_pack["manifest"], "scene mesh_pack.manifest")
    if context_scene.get("mesh_pack_manifest_sha256") != pack_manifest_digest:
        fail("source context mesh pack manifest differs from the scene")
    pack_source = strict_object(pack_manifest.get("source"), "mesh pack source")
    pack_source_digest = pack_source.get("sha256")
    if context_scene.get("mesh_pack_source_sha256") != pack_source_digest:
        fail("source context mesh pack source differs from the scene pack")

    pins = {
        "objects_json": context_scene.get("objects_json_sha256"),
        "scaleout_manifest": context_sources.get("scaleout_manifest_sha256"),
        "fragment": context_sources.get("catalog_fragment_sha256"),
        "combined_glbs": context_sources.get("scaleout_glb_combined_sha256"),
        "pack_manifest": context_scene.get("mesh_pack_manifest_sha256"),
        "pack_source": context_scene.get("mesh_pack_source_sha256"),
        "placement_rule": context_scene.get("placement_rule"),
    }
    for key in ("objects_json", "scaleout_manifest", "fragment", "combined_glbs",
                "pack_manifest", "pack_source"):
        value = pins[key]
        if not isinstance(value, str) or len(value) != 64 \
                or any(character not in "0123456789abcdef" for character in value):
            fail(f"source context {key} is not a lowercase SHA-256 digest")
    if not isinstance(pins["placement_rule"], str) or not pins["placement_rule"]:
        fail("source context placement_rule is missing")

    context_buildings = strict_array(source_context.get("buildings"), "source context buildings")
    if not context_buildings:
        fail("source context has no buildings")
    context_ids = [entry.get("object_id") if isinstance(entry, dict) else None
                   for entry in context_buildings]
    if any(not isinstance(object_id, str) or not object_id for object_id in context_ids) \
            or len(set(context_ids)) != len(context_ids):
        fail("source context building identities are invalid")

    output_dir = command.output_dir.resolve()
    if output_dir.name != scene_id:
        fail(f"output directory must end with the explicit scene id {scene_id}")
    return RegionBuildInputs(
        scene_id=scene_id,
        scene_config=scene_config,
        scene_origin=dict(scene_origin),
        pins=pins,
        expected_buildings=len(context_ids),
        expected_object_ids=frozenset(context_ids),
        objects_path=command.scene_root / "metadata/objects.json",
        effective_osm_path=command.scene_root / "osm/effective.osm.json",
        scaleout_manifest_path=command.canonical_render_dir / "manifest.json",
        fragment_path=command.canonical_render_dir / "building-render-catalog-fragment.json",
        canonical_assets=command.canonical_render_dir / "assets",
        pack_manifest_path=command.mesh_pack_manifest,
        output_dir=command.output_dir,
        scene_output=command.scene_output,
        source_context_bytes=source_context_bytes,
    )


class MeshBuilder:
    """Collects one glTF primitive (positions, normals, optional UVs, indices)."""

    def __init__(self, with_uvs: bool) -> None:
        self.with_uvs = with_uvs
        self.positions: list[Vec3] = []
        self.normals: list[Vec3] = []
        self.uvs: list[tuple[float, float]] = []
        self.indices: list[int] = []

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3

    def add_quad(self, corners: list[Vec3], normal: Vec3,
                 uvs: list[tuple[float, float]] | None) -> None:
        """Append a planar quad, winding chosen so the face normal is `normal`."""
        if len(corners) != 4:
            fail("quad needs exactly four corners")
        base = len(self.positions)
        p0, p1, p2 = corners[0], corners[1], corners[2]
        a = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        b = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        computed = (a[1] * b[2] - a[2] * b[1],
                    a[2] * b[0] - a[0] * b[2],
                    a[0] * b[1] - a[1] * b[0])
        length = math.sqrt(computed[0] ** 2 + computed[1] ** 2 + computed[2] ** 2)
        if length < 1e-12:
            fail("degenerate quad in derived art-detail geometry")
        if computed[0] * normal[0] + computed[1] * normal[1] + computed[2] * normal[2] < 0:
            corners = [corners[0], corners[3], corners[2], corners[1]]
            if uvs is not None:
                uvs = [uvs[0], uvs[3], uvs[2], uvs[1]]
        for corner in corners:
            self.positions.append(corner)
            self.normals.append(normal)
        if self.with_uvs:
            if uvs is None:
                fail("facade primitive requires UVs")
            self.uvs.extend(uvs)
        self.indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])


def _sub2(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _add2(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def _scale2(a: Vec2, k: float) -> Vec2:
    return (a[0] * k, a[1] * k)


def _dot2(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _unit2(a: Vec2) -> Vec2:
    length = math.hypot(a[0], a[1])
    if length < 1e-12:
        fail("zero-length 2D direction in art-detail geometry")
    return (a[0] / length, a[1] / length)


def signed_area(ring: list[Vec2]) -> float:
    total = 0.0
    for index in range(len(ring)):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total * 0.5


def ring_from_footprint(footprint: list) -> list[Vec2]:
    """CCW ring without a repeated closing point, matching the source generator."""
    pts: list[Vec2] = [(float(p[0]), float(p[1])) for p in footprint]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        fail("footprint ring has fewer than three vertices")
    if signed_area(pts) < 0:
        pts = list(reversed(pts))
    if signed_area(pts) <= 1e-9:
        fail("footprint ring encloses no area")
    return pts


def edge_profile(length: float) -> list[tuple[float, float]] | None:
    """Arc position / inward-offset pairs along one wall edge.

    Returns ``None`` when the edge is too short to take relief (the caller keeps
    the flat wall for that edge).
    """
    if length < EDGE_RELIEF_MIN_M:
        return None
    bays = int(round(length / BAY_TARGET_M))
    bays = max(1, min(BAY_MAX, bays))
    bays = min(bays, max(1, int(length // BAY_MIN_SPAN_M)))
    bay = length / bays
    pilaster = min(PILASTER_W_M, 0.18 * bay)
    if bay - pilaster - PANEL_MIN_M <= 0:
        return None
    chamfer = min(CHAMFER_M, (bay - pilaster - PANEL_MIN_M) / 2.0)
    if chamfer < 0.02:
        return None
    recess = min(RECESS_M, 0.35 * bay)
    points: list[tuple[float, float]] = []

    def push(arc: float, offset: float) -> None:
        if points and abs(points[-1][0] - arc) < 1e-9 and abs(points[-1][1] - offset) < 1e-9:
            return
        points.append((arc, offset))

    for bay_index in range(bays + 1):
        centre = bay_index * bay
        lo = max(0.0, centre - pilaster / 2.0)
        hi = min(length, centre + pilaster / 2.0)
        push(lo, 0.0)
        push(hi, 0.0)
        if bay_index < bays:
            push(hi + chamfer, -recess)
            next_lo = max(0.0, (bay_index + 1) * bay - pilaster / 2.0)
            push(next_lo - chamfer, -recess)
    if not points or abs(points[0][0]) > 1e-9 or abs(points[-1][0] - length) > 1e-9:
        return None
    for left, right in zip(points, points[1:]):
        if right[0] <= left[0] + 1e-9:
            return None
    return points


def _segments_intersect(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> bool:
    def orient(p: Vec2, q: Vec2, r: Vec2) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    if abs(o1) < 1e-12 and abs(o2) < 1e-12 and abs(o3) < 1e-12 and abs(o4) < 1e-12:
        return False
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def ring_is_simple(ring: list[Vec2]) -> bool:
    count = len(ring)
    for i in range(count):
        for j in range(i + 1, count):
            if j == i or (i == 0 and j == count - 1) or j == i + 1:
                continue
            if _segments_intersect(ring[i], ring[(i + 1) % count],
                                   ring[j], ring[(j + 1) % count]):
                return False
    return True


def inward_offset_ring(ring: list[Vec2], thickness: float) -> list[Vec2] | None:
    """Mitered inward offset; ``None`` when the offset would not stay simple."""
    count = len(ring)
    result: list[Vec2] = []
    for index in range(count):
        previous = ring[index - 1]
        current = ring[index]
        following = ring[(index + 1) % count]
        in_dir = _unit2(_sub2(current, previous))
        out_dir = _unit2(_sub2(following, current))
        # CCW ring: inward normal of a direction (dx, dy) is (-dy, dx).
        n_in = (-in_dir[1], in_dir[0])
        n_out = (-out_dir[1], out_dir[0])
        miter = (n_in[0] + n_out[0], n_in[1] + n_out[1])
        if math.hypot(miter[0], miter[1]) < 1e-9:
            miter = n_in
        miter = _unit2(miter)
        dot = max(_dot2(miter, n_in), MITER_MIN_DOT)
        step = min(thickness / dot, thickness * MITER_MAX_FACTOR)
        result.append(_add2(current, _scale2(miter, step)))
    if signed_area(result) <= 1e-9 or not ring_is_simple(result):
        return None
    # A mitered offset can leave a concave footprint; art detail must never
    # cross the source outline, so reject it and let the caller retry smaller.
    if not all(_inside_or_on(point, ring) for point in result):
        return None
    return result


def _inside_or_on(point: Vec2, ring: list[Vec2], tol: float = 1e-4) -> bool:
    """True when a point is inside the ring or on its boundary within `tol`."""
    if point_in_ring(point, ring):
        return True
    return ring_distance(point, ring) <= tol


def point_in_ring(point: Vec2, ring: list[Vec2]) -> bool:
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > point[1]) != (y2 > point[1]):
            x_cross = x1 + (point[1] - y1) * (x2 - x1) / (y2 - y1)
            if point[0] < x_cross:
                inside = not inside
    return inside


def ring_distance(point: Vec2, ring: list[Vec2]) -> float:
    best = float("inf")
    count = len(ring)
    for index in range(count):
        a = ring[index]
        b = ring[(index + 1) % count]
        ab = _sub2(b, a)
        denom = _dot2(ab, ab)
        if denom < 1e-18:
            candidate = math.hypot(point[0] - a[0], point[1] - a[1])
        else:
            t = max(0.0, min(1.0, _dot2(_sub2(point, a), ab) / denom))
            closest = (a[0] + ab[0] * t, a[1] + ab[1] * t)
            candidate = math.hypot(point[0] - closest[0], point[1] - closest[1])
        best = min(best, candidate)
    return best


def bulkhead_rects(inner: list[Vec2], parapet_h: float, seed: int) -> list[dict]:
    """Deterministic candidate roof structures that stay inside the parapet."""
    xs = [p[0] for p in inner]
    ys = [p[1] for p in inner]
    width = max(xs) - min(xs)
    depth = max(ys) - min(ys)
    if min(width, depth) < BULKHEAD_MIN_EXTENT_M:
        return []
    half_w = min(BULKHEAD_FRACTION * width, BULKHEAD_MAX_W_M) / 2.0
    half_d = min(BULKHEAD_FRACTION * depth, BULKHEAD_MAX_D_M) / 2.0
    if half_w < 0.6 or half_d < 0.6:
        return []
    centroid = _ring_centroid(inner)
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    angle = ((seed >> 0) & 0xFFFF) / 65535.0 * math.pi
    drift = ((seed >> 16) & 0xFFFF) / 65535.0
    base = (min(width, depth) * 0.12) * (0.4 + 0.6 * drift)
    seeds = [
        centroid,
        (mean_x, mean_y),
        ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0),
        (centroid[0] + math.cos(angle) * base, centroid[1] + math.sin(angle) * base),
        (centroid[0] - math.cos(angle) * base, centroid[1] - math.sin(angle) * base),
        (centroid[0] + math.cos(angle + math.pi / 2) * base,
         centroid[1] + math.sin(angle + math.pi / 2) * base),
        (centroid[0] - math.cos(angle + math.pi / 2) * base,
         centroid[1] - math.sin(angle + math.pi / 2) * base),
    ]
    placed: list[dict] = []
    for centre in seeds:
        if len(placed) >= BULKHEAD_COUNT:
            break
        scale = 1.0
        chosen = None
        for _ in range(BULKHEAD_PLACEMENT_TRIES):
            w = half_w * 2.0 * scale
            d = half_d * 2.0 * scale
            corners = [
                (centre[0] - w / 2.0, centre[1] - d / 2.0),
                (centre[0] + w / 2.0, centre[1] - d / 2.0),
                (centre[0] + w / 2.0, centre[1] + d / 2.0),
                (centre[0] - w / 2.0, centre[1] + d / 2.0),
            ]
            if all(point_in_ring(corner, inner) for corner in corners) \
                    and all(ring_distance(corner, inner) >= BULKHEAD_CLEARANCE_M
                            for corner in corners) \
                    and all(not _overlaps(rect["corners"], corners) for rect in placed):
                chosen = {"centre": centre, "width": w, "depth": d, "corners": corners}
                break
            scale *= 0.7
        if chosen is None:
            continue
        placed.append(chosen)
    # Deterministic order: larger footprints first, then by centre coordinate.
    placed.sort(key=lambda rect: (-rect["width"] * rect["depth"],
                                  round(rect["centre"][0], 6),
                                  round(rect["centre"][1], 6)))
    if len(placed) > BULKHEAD_COUNT:
        placed = placed[:BULKHEAD_COUNT]
    for rect in placed:
        rect["height"] = parapet_h
    return placed


def _overlaps(corners_a: list[Vec2], corners_b: list[Vec2]) -> bool:
    a_x = [p[0] for p in corners_a]
    a_y = [p[1] for p in corners_a]
    b_x = [p[0] for p in corners_b]
    b_y = [p[1] for p in corners_b]
    gap = BULKHEAD_CLEARANCE_M
    return not (max(a_x) + gap <= min(b_x) or max(b_x) + gap <= min(a_x)
                or max(a_y) + gap <= min(b_y) or max(b_y) + gap <= min(a_y))


def _ring_centroid(ring: list[Vec2]) -> Vec2:
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for index in range(len(ring)):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % len(ring)]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area2) < 1e-12:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return (cx / (3.0 * area2), cy / (3.0 * area2))


def _enu_to_gltf(point: Vec2, up: float, anchor: tuple[float, float]) -> Vec3:
    return (point[0] - anchor[0], up, anchor[1] - point[1])


def _enu_normal_to_gltf(normal: Vec2, up: float) -> Vec3:
    return (normal[0], up, -normal[1])


def parapet_height(height: float) -> float:
    return min(PARAPET_MAX_M, max(PARAPET_MIN_M, PARAPET_RATIO * height))


def read_accessor(document: dict, binary: bytes, index: int, label: str) -> list[tuple]:
    accessors = strict_array(document.get("accessors"), f"{label} accessors")
    buffer_views = strict_array(document.get("bufferViews"), f"{label} bufferViews")
    accessor = strict_object(accessors[index], f"{label} accessors[{index}]")
    view = strict_object(buffer_views[accessor["bufferView"]], f"{label} bufferView")
    components = ACCESSOR_COMPONENTS[accessor["type"]]
    component_size = COMPONENT_SIZE[accessor["componentType"]]
    stride = view.get("byteStride", components * component_size)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"]
    if start + (count - 1) * stride + components * component_size > len(binary):
        fail(f"{label}: accessor {index} overruns the BIN chunk")
    fmt = {5126: "f", 5123: "H", 5125: "I", 5121: "B", 5120: "b"}.get(accessor["componentType"])
    if fmt is None:
        fail(f"{label}: unsupported accessor componentType")
    return [struct.unpack_from(f"<{components}{fmt}", binary, start + i * stride)
            for i in range(count)]


def build_facade_geometry(ring: list[Vec2], height: float, wall_top: float,
                          anchor: tuple[float, float], tile_profile: FacadeTileProfile,
                          relief: bool) -> tuple[MeshBuilder, dict]:
    """Facade primitive in local Y (0 = base, `height` = canonical source top).

    Bay relief spans base..wall_top; when a parapet recess is present a plain
    texture-continuous band closes wall_top..height on the footprint boundary.
    Recess tops sit against the roof deck, which is emitted exactly at
    `wall_top`, so the grooves are sealed from above without shelf faces.
    """
    builder = MeshBuilder(with_uvs=True)
    relief_edges = 0
    bays_total = 0
    tile_width, tile_height = tile_profile.repeat_m
    count = len(ring)
    for index in range(count):
        start = ring[index]
        end = ring[(index + 1) % count]
        delta = _sub2(end, start)
        length = math.hypot(delta[0], delta[1])
        if length < 1e-9:
            fail("footprint has a zero-length edge")
        along = (delta[0] / length, delta[1] / length)
        outward = (along[1], -along[0])
        profile = edge_profile(length) if relief else None
        if profile is not None:
            # Art detail must never cross the source outline: drop relief on
            # this edge if any profile point falls outside a concave footprint.
            for arc, offset in profile:
                point = _add2(_add2(start, _scale2(along, arc)), _scale2(outward, offset))
                if not _inside_or_on(point, ring):
                    profile = None
                    break
        if profile is None:
            pairs = [(0.0, 0.0, length, 0.0)]
        else:
            relief_edges += 1
            bays_total += sum(1 for point in profile if point[1] < -1e-9) // 2
            pairs = [(profile[i][0], profile[i][1],
                      profile[i + 1][0], profile[i + 1][1])
                     for i in range(len(profile) - 1)]
        for arc_a, offset_a, arc_b, offset_b in pairs:
            point_a = _add2(_add2(start, _scale2(along, arc_a)), _scale2(outward, offset_a))
            point_b = _add2(_add2(start, _scale2(along, arc_b)), _scale2(outward, offset_b))
            direction = _sub2(point_b, point_a)
            wall_normal = _unit2((direction[1], -direction[0]))
            face_normal = _enu_normal_to_gltf(wall_normal, 0.0)
            u_a = arc_a / tile_width
            u_b = arc_b / tile_width
            v_top = 1.0 - wall_top / tile_height
            builder.add_quad(
                [_enu_to_gltf(point_a, 0.0, anchor),
                 _enu_to_gltf(point_b, 0.0, anchor),
                 _enu_to_gltf(point_b, wall_top, anchor),
                 _enu_to_gltf(point_a, wall_top, anchor)],
                face_normal,
                [(u_a, 1.0), (u_b, 1.0), (u_b, v_top), (u_a, v_top)],
            )
        if wall_top < height:
            # Plain parapet band above the relief; U is continuous with the
            # wall below (arc measured from the same edge start).
            nrm = _enu_normal_to_gltf(outward, 0.0)
            u_edge = length / tile_width
            v_bottom = 1.0 - wall_top / tile_height
            v_top = 1.0 - height / tile_height
            builder.add_quad(
                [_enu_to_gltf(start, wall_top, anchor),
                 _enu_to_gltf(end, wall_top, anchor),
                 _enu_to_gltf(end, height, anchor),
                 _enu_to_gltf(start, height, anchor)],
                nrm,
                [(0.0, v_bottom), (u_edge, v_bottom), (u_edge, v_top), (0.0, v_top)],
            )
    stats = {"relief": bool(relief and relief_edges > 0),
             "relief_edges": relief_edges, "bays": bays_total}
    return builder, stats


def build_roof_geometry(source_roof_positions: list[tuple[float, float, float]],
                        source_roof_normals: list[tuple[float, float, float]],
                        source_roof_indices: list[int],
                        ring: list[Vec2], inner: list[Vec2] | None,
                        height: float, anchor: tuple[float, float],
                        bulkheads: list[dict], deck_drop: float) -> MeshBuilder:
    """Roof primitive in local Y: deck (recessed by `deck_drop`), parapet, structures."""
    builder = MeshBuilder(with_uvs=False)
    deck_y = height - deck_drop
    if len(source_roof_positions) != len(ring):
        fail("source roof vertex count does not match the footprint ring")
    for position, normal in zip(source_roof_positions, source_roof_normals):
        if abs(position[1] - height) > 1e-6:
            fail("source roof vertex is not at the source top height")
        if abs(normal[0]) > 1e-6 or abs(normal[1] - 1.0) > 1e-6 or abs(normal[2]) > 1e-6:
            fail("source roof normal is not +Y")
    deck_positions = [(p[0], p[1] - deck_drop, p[2]) for p in source_roof_positions]
    for index in source_roof_indices:
        if not 0 <= index < len(deck_positions):
            fail("source roof index out of range")
        builder.indices.append(index)
    builder.positions.extend(deck_positions)
    builder.normals.extend(source_roof_normals)
    if inner is None:
        return builder
    count = len(ring)
    up_normal = (0.0, 1.0, 0.0)
    for index in range(count):
        next_index = (index + 1) % count
        outer_a, outer_b = ring[index], ring[next_index]
        inner_a, inner_b = inner[index], inner[next_index]
        builder.add_quad(
            [_enu_to_gltf(outer_a, height, anchor),
             _enu_to_gltf(outer_b, height, anchor),
             _enu_to_gltf(inner_b, height, anchor),
             _enu_to_gltf(inner_a, height, anchor)],
            up_normal, None,
        )
        edge = _sub2(inner_b, inner_a)
        facing = _unit2((-edge[1], edge[0]))
        face_normal = _enu_normal_to_gltf(facing, 0.0)
        builder.add_quad(
            [_enu_to_gltf(inner_a, deck_y, anchor),
             _enu_to_gltf(inner_b, deck_y, anchor),
             _enu_to_gltf(inner_b, height, anchor),
             _enu_to_gltf(inner_a, height, anchor)],
            face_normal, None,
        )
    for rect in bulkheads:
        corners = rect["corners"]
        bottom = deck_y
        top = deck_y + rect["height"]
        sides = [
            (corners[0], corners[1], (0.0, -1.0)),
            (corners[1], corners[2], (1.0, 0.0)),
            (corners[2], corners[3], (0.0, 1.0)),
            (corners[3], corners[0], (-1.0, 0.0)),
        ]
        for point_a, point_b, normal_enu in sides:
            builder.add_quad(
                [_enu_to_gltf(point_a, bottom, anchor),
                 _enu_to_gltf(point_b, bottom, anchor),
                 _enu_to_gltf(point_b, top, anchor),
                 _enu_to_gltf(point_a, top, anchor)],
                _enu_normal_to_gltf(normal_enu, 0.0), None,
            )
        builder.add_quad(
            [_enu_to_gltf(corners[0], top, anchor),
             _enu_to_gltf(corners[1], top, anchor),
             _enu_to_gltf(corners[2], top, anchor),
             _enu_to_gltf(corners[3], top, anchor)],
            up_normal, None,
        )
    return builder


def verify_source_ring(facade_pos: list[tuple], facade_nrm: list[tuple],
                       roof_pos: list[tuple], roof_nrm: list[tuple],
                       ring: list[Vec2], anchor: tuple[float, float],
                       height: float, label: str) -> None:
    """Cross-check the objects.json footprint against the canonical GLB walls.

    The art-detail builders reconstruct walls from the footprint ring; this
    fails closed unless that reconstruction reproduces the canonical facade
    vertex-for-vertex (both position and outward normal), so relief/parapet
    placement is provably anchored to the real geometry.
    """
    count = len(ring)
    if len(facade_pos) != 4 * count or len(facade_nrm) != 4 * count:
        fail(f"{label}: canonical facade does not carry four vertices per footprint edge")
    if len(roof_pos) != count or len(roof_nrm) != count:
        fail(f"{label}: canonical roof does not carry one vertex per footprint corner")
    tol = 1e-4
    for index in range(count):
        point_a = ring[index]
        point_b = ring[(index + 1) % count]
        delta = _sub2(point_b, point_a)
        length = math.hypot(delta[0], delta[1])
        outward = (delta[1] / length, -delta[0] / length)
        expected_normal = _enu_normal_to_gltf(outward, 0.0)
        expected_corners = [
            _enu_to_gltf(point_a, 0.0, anchor),
            _enu_to_gltf(point_b, 0.0, anchor),
            _enu_to_gltf(point_b, height, anchor),
            _enu_to_gltf(point_a, height, anchor),
        ]
        for corner_index, expected in enumerate(expected_corners):
            actual = facade_pos[4 * index + corner_index]
            if any(abs(actual[axis] - expected[axis]) > tol for axis in range(3)):
                fail(f"{label}: canonical facade disagrees with the objects.json footprint "
                     f"(edge {index}, corner {corner_index}); art detail would be misplaced")
            normal = facade_nrm[4 * index + corner_index]
            if any(abs(normal[axis] - expected_normal[axis]) > tol for axis in range(3)):
                fail(f"{label}: canonical facade normal disagrees with footprint winding")
        expected_roof = _enu_to_gltf(point_a, height, anchor)
        actual_roof = roof_pos[index]
        if any(abs(actual_roof[axis] - expected_roof[axis]) > tol for axis in range(3)):
            fail(f"{label}: canonical roof ring disagrees with the objects.json footprint")
        normal = roof_nrm[index]
        if abs(normal[0]) > tol or abs(normal[1] - 1.0) > tol or abs(normal[2]) > tol:
            fail(f"{label}: canonical roof normal is not +Y")


def derive_art_detail_glb(document: dict, binary: bytes, label: str, *,
                          ring: list[Vec2], base_up: float, height: float,
                          top_up: float, anchor: tuple[float, float],
                          style: dict,
                          options: ArtDetailOptions = ArtDetailOptions()) -> tuple[bytes, dict[str, bytes], dict]:
    """Rebuild one canonical GLB as a geometry-only runtime GLB with art detail.

    Spatial truth is preserved: the result stays inside the canonical footprint,
    never rises above the canonical top height, keeps source material parameters
    and texture assignments, and externalises shared textures. Optional middle
    tiles retain the original PNGs and encode cropped source pixels losslessly.
    The
    added geometry is deterministic render art detail (parapet cornice, facade
    bay relief, roof structures) and is labelled as such in
    ``asset.extras.art_detail`` — it is not survey data.
    """
    if document.get("extensionsUsed") is not None:
        fail(f"{label}: extensions are not supported by the derivation")
    source_tile_profile = facade_tile_profile(document, binary, style, height, label)
    tile_profile = source_tile_profile
    pane_profile = None
    tile_channels = None
    if options.facade_middle_tiles:
        tile_profile, pane_profile = middle_tile_profile(style, source_tile_profile, label)
        tile_channels = tile_channel_metadata(document, binary, style, label)

    meshes = strict_array(document.get("meshes"), f"{label} meshes")
    if len(meshes) != 1:
        fail(f"{label}: expected exactly one mesh")
    primitives = strict_array(meshes[0].get("primitives"), f"{label} primitives")
    expected_primitives = [
        {"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
         "indices": 3, "material": 0, "mode": 4},
        {"attributes": {"POSITION": 4, "NORMAL": 5},
         "indices": 6, "material": 1, "mode": 4},
    ]
    if primitives != expected_primitives:
        fail(f"{label}: canonical primitive layout changed")
    if len(strict_array(document.get("accessors"), f"{label} accessors")) != 7:
        fail(f"{label}: expected the canonical seven geometry accessors")

    # --- externalise shared source channels, optionally cropping their pixels ---
    images = strict_array(document.get("images"), f"{label} images")
    buffer_views = strict_array(document.get("bufferViews"), f"{label} bufferViews")
    derived_images: list[dict] = []
    extracted: dict[str, bytes] = {}
    archived_sources: dict[str, bytes] = {}
    image_view_indices: set[int] = set()
    for index, raw_image in enumerate(images):
        image = strict_object(raw_image, f"{label} images[{index}]")
        if "uri" in image:
            fail(f"{label} images[{index}]: source image is not embedded")
        view_index = image.get("bufferView")
        if not isinstance(view_index, int) or not (0 <= view_index < len(buffer_views)):
            fail(f"{label} images[{index}]: invalid bufferView")
        if view_index in image_view_indices:
            fail(f"{label} images[{index}]: bufferView shared with another image")
        image_view_indices.add(view_index)
        view = strict_object(buffer_views[view_index], f"{label} bufferViews[{view_index}]")
        if view.get("byteStride") is not None:
            fail(f"{label} images[{index}]: strided image bufferView")
        offset = view.get("byteOffset", 0)
        length = view.get("byteLength")
        if not isinstance(length, int) or offset + length > len(binary):
            fail(f"{label} images[{index}]: bufferView overruns the BIN chunk")
        data = bytes(binary[offset:offset + length])
        media_type = detect_image_media_type(data, image.get("mimeType"),
                                             f"{label} images[{index}]")
        source_digest = hashlib.sha256(data).hexdigest()
        if options.facade_middle_tiles:
            archived_sources[source_digest] = data
            data = crop_source_png(data, FACADE_MIDDLE_TILE_PROFILES[style["style_id"]].crop_px)
        digest = hashlib.sha256(data).hexdigest()
        if digest in extracted and extracted[digest] != data:
            fail(f"{label}: sha256 collision on extracted image {digest}")
        extracted[digest] = data
        derived_image: dict = {"uri": f"assets/{digest}"}
        if "name" in image:
            derived_image["name"] = image["name"]
        if "mimeType" in image:
            derived_image["mimeType"] = media_type
        derived_images.append(derived_image)

    # --- canonical geometry (accessor layout is pinned by expected_primitives) ---
    facade_pos = read_accessor(document, binary, 0, label)
    facade_nrm = read_accessor(document, binary, 1, label)
    facade_uvs = read_accessor(document, binary, 2, label)
    if len(facade_uvs) != len(facade_pos):
        fail(f"{label}: canonical UV count does not match the facade vertices")
    facade_idx = [row[0] for row in read_accessor(document, binary, 3, label)]
    roof_pos = read_accessor(document, binary, 4, label)
    roof_nrm = read_accessor(document, binary, 5, label)
    roof_idx = [row[0] for row in read_accessor(document, binary, 6, label)]
    verify_source_ring(facade_pos, facade_nrm, roof_pos, roof_nrm, ring,
                       anchor, height, label)

    # --- deterministic art detail, always inside the footprint and <= source top ---
    parapet_target = parapet_height(height)
    parapet_thickness = 0.0
    inner: list[Vec2] | None = None
    deck_drop = 0.0
    if options.roof_detail and height - parapet_target >= 0.4:
        thickness = PARAPET_THICKNESS_M
        for _ in range(4):
            inner = inward_offset_ring(ring, thickness)
            if inner is not None:
                parapet_thickness = thickness
                deck_drop = parapet_target
                break
            thickness *= 0.5
    wall_top = height - deck_drop
    seed = int(hashlib.sha256(label.encode("utf-8")).hexdigest()[:8], 16)
    bulkheads = bulkhead_rects(inner, deck_drop, seed) \
        if inner is not None and deck_drop > 0 else []

    if options.facade_relief:
        facade_builder, facade_stats = build_facade_geometry(
            ring, height, wall_top, anchor, tile_profile, relief=True)
    else:
        # Preserve every source wall position, normal and index; physical UV
        # density is independent of roof details and building height.
        facade_builder = MeshBuilder(with_uvs=True)
        facade_builder.positions.extend(facade_pos)
        facade_builder.normals.extend(facade_nrm)
        facade_builder.uvs.extend(metric_facade_uvs(facade_pos, tile_profile, label))
        facade_builder.indices.extend(facade_idx)
        facade_stats = {"relief": False, "relief_edges": 0, "bays": 0}
    roof_builder = build_roof_geometry(
        roof_pos, roof_nrm, roof_idx, ring, inner, height, anchor,
        bulkheads, deck_drop)
    for primitive_name, builder in (("facade", facade_builder), ("roof", roof_builder)):
        if builder.vertex_count > MAX_DERIVED_VERTICES:
            fail(f"{label}: derived {primitive_name} exceeds {MAX_DERIVED_VERTICES} vertices")

    # --- pack the derived GLB (geometry-only, images externalised) ---
    views: list[dict] = []
    output = bytearray()

    def add_view(data: bytes, target: int | None = None) -> int:
        aligned = align4(len(output))
        output.extend(b"\x00" * (aligned - len(output)))
        entry: dict = {"buffer": 0, "byteOffset": aligned, "byteLength": len(data)}
        if target is not None:
            entry["target"] = target
        views.append(entry)
        output.extend(data)
        return len(views) - 1

    def pack_floats(values: list[tuple]) -> bytes:
        flat = [component for value in values for component in value]
        return struct.pack(f"<{len(flat)}f", *flat)

    def pack_indices(values: list[int], component_type: int) -> bytes:
        fmt = "I" if component_type == 5125 else "H"
        return struct.pack(f"<{len(values)}{fmt}", *values)

    def bounds_of(values: list[tuple]) -> tuple[list[float], list[float]]:
        lo = [min(value[axis] for value in values) for axis in range(3)]
        hi = [max(value[axis] for value in values) for axis in range(3)]
        return lo, hi

    f_idx_type = 5125 if max(facade_builder.indices) > 65535 else 5123
    r_idx_type = 5125 if max(roof_builder.indices) > 65535 else 5123
    f_pos = facade_builder.positions
    f_nrm = facade_builder.normals
    f_uv = facade_builder.uvs
    f_idx = facade_builder.indices
    r_pos = roof_builder.positions
    r_nrm = roof_builder.normals
    r_idx = roof_builder.indices
    v_f_pos = add_view(pack_floats(f_pos), 34962)
    v_f_nrm = add_view(pack_floats(f_nrm), 34962)
    v_f_uv = add_view(pack_floats(f_uv), 34962)
    v_f_idx = add_view(pack_indices(f_idx, f_idx_type), 34963)
    v_r_pos = add_view(pack_floats(r_pos), 34962)
    v_r_nrm = add_view(pack_floats(r_nrm), 34962)
    v_r_idx = add_view(pack_indices(r_idx, r_idx_type), 34963)
    f_lo, f_hi = bounds_of(f_pos)
    r_lo, r_hi = bounds_of(r_pos)
    accessors = [
        {"bufferView": v_f_pos, "componentType": 5126, "count": len(f_pos),
         "type": "VEC3", "min": f_lo, "max": f_hi},
        {"bufferView": v_f_nrm, "componentType": 5126, "count": len(f_nrm), "type": "VEC3"},
        {"bufferView": v_f_uv, "componentType": 5126, "count": len(f_uv), "type": "VEC2"},
        {"bufferView": v_f_idx, "componentType": f_idx_type, "count": len(f_idx),
         "type": "SCALAR"},
        {"bufferView": v_r_pos, "componentType": 5126, "count": len(r_pos),
         "type": "VEC3", "min": r_lo, "max": r_hi},
        {"bufferView": v_r_nrm, "componentType": 5126, "count": len(r_nrm), "type": "VEC3"},
        {"bufferView": v_r_idx, "componentType": r_idx_type, "count": len(r_idx),
         "type": "SCALAR"},
    ]

    art_detail = {
        "schema": ART_DETAIL_SCHEMA,
        "class": ART_DETAIL_CLASS,
        "layers": geometry_layers(options),
        "disclaimer_zh": ART_DETAIL_NOTE_ZH,
        "disclaimer_en": ART_DETAIL_NOTE_EN,
        "detail": {
            "facade_relief": facade_stats["relief"],
            "relief_edges": facade_stats["relief_edges"],
            "bays": facade_stats["bays"],
            "parapet": deck_drop > 0,
            "parapet_height_m": round(deck_drop, 6),
            "parapet_thickness_m": round(parapet_thickness, 6),
            "roof_structures": len(bulkheads),
            "facade_tiling": {
                "schema": FACADE_TILING_SCHEMA,
                "class": ART_DETAIL_CLASS,
                "profile_id": style["style_id"],
                "physical_unit": "metres",
                "source_region_px": list(source_tile_profile.region_px),
                "atlas_repeat_m": [round(value, 8) for value in tile_profile.repeat_m],
                "pane_pitch_m": [round(value * tile_profile.metres_per_pixel, 8)
                                 for value in tile_profile.pane_pitch_px],
                "reference_window_m": [round(value * tile_profile.metres_per_pixel, 8)
                                       for value in tile_profile.reference_window_px],
                "authored_storey_pitch_m": tile_profile.authored_storey_pitch_m,
                "pane_rows_per_storey": tile_profile.pane_rows_per_storey,
                "uv_channel": 0,
                "mapping": "U=wall_arc_m/tile_width_m; V=1-local_up_m/tile_height_m; upright; per-wall U origin",
                "uv_min": [round(min(uv[axis] for uv in f_uv), 8) for axis in range(2)],
                "uv_max": [round(max(uv[axis] for uv in f_uv), 8) for axis in range(2)],
            },
        },
        "max_up_m": round(top_up, 6),
        "vertices": facade_builder.vertex_count + roof_builder.vertex_count,
        "triangles": facade_builder.triangle_count + roof_builder.triangle_count,
    }
    if options.facade_middle_tiles:
        art_detail["detail"]["facade_tiling"].update({
            "mode": "source-middle-crop",
            "source_crop_px": list(FACADE_MIDDLE_TILE_PROFILES[style["style_id"]].crop_px),
            "active_tile_region_in_atlas_px": list(tile_profile.region_px),
            "tile_size_px": pane_profile["tile_size_px"],
            "authored_pane_rows_per_tile": FACADE_MIDDLE_TILE_PROFILES[style["style_id"]].pane_rows,
            "metres_per_source_pixel": round(tile_profile.metres_per_pixel, 10),
            "channels": tile_channels,
            "encoding": {"format": "RGB8-PNG", "filter": 0, "zlib_level": 9,
                         "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION},
        })

    derived = json.loads(json.dumps(document))
    derived["bufferViews"] = views
    derived["accessors"] = accessors
    derived["images"] = derived_images
    if options.facade_middle_tiles:
        extras = derived["materials"][0].setdefault("extras", {})
        if "authoredFacadePaneProfile" in extras:
            fail(f"{label}: source material already carries an authored pane profile")
        extras["authoredFacadePaneProfile"] = pane_profile
        roof_extras = derived["materials"][1].setdefault("extras", {})
        if "authoredBuildingMaterialRole" in roof_extras:
            fail(f"{label}: source roof already carries an authored material role")
        roof_extras["authoredBuildingMaterialRole"] = "opaque-roof"
    # BIN chunk length includes trailing alignment, matching write_glb.
    bin_padding = b"\x00" * (-len(output) % 4)
    derived["buffers"] = [{"byteLength": len(output) + len(bin_padding)}]
    strict_object(derived.get("asset"), "derived asset")["extras"]["art_detail"] = art_detail
    validate_accessors(derived, label)

    json_bytes = json.dumps(derived, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8")
    json_padding = b" " * (-len(json_bytes) % 4)
    total = 12 + 8 + len(json_bytes) + len(json_padding) + 8 + len(output) + len(bin_padding)
    glb = bytearray(struct.pack("<4sII", b"glTF", 2, total))
    glb.extend(struct.pack("<II", len(json_bytes) + len(json_padding), 0x4E4F534A))
    glb.extend(json_bytes)
    glb.extend(json_padding)
    glb.extend(struct.pack("<II", len(output) + len(bin_padding), 0x004E4942))
    glb.extend(output)
    glb.extend(bin_padding)

    stats = {
        "art": art_detail,
        "entry": {
            "relief": facade_stats["relief"],
            "relief_edges": facade_stats["relief_edges"],
            "bays": facade_stats["bays"],
            "parapet": deck_drop > 0,
            "parapet_height_m": round(deck_drop, 6),
            "parapet_thickness_m": round(parapet_thickness, 6),
            "roof_structures": len(bulkheads),
            "vertices": art_detail["vertices"],
            "triangles": art_detail["triangles"],
        },
        "source_vertices": len(facade_pos) + len(roof_pos),
        "source_triangles": len(facade_idx) // 3 + len(roof_idx) // 3,
        "archived_source_images": archived_sources,
    }
    return bytes(glb), extracted, stats


def verify_derivation(source_path: Path, derived_bytes: bytes, label: str, *,
                      ring: list[Vec2], height: float, top_up: float,
                      anchor: tuple[float, float],
                      expected_lo: tuple[float, float, float],
                      expected_hi: tuple[float, float, float],
                      expected_art: dict) -> None:
    """Fail-closed art-detail contract check before a derived GLB is staged.

    Contract: spatial truth preserved (envelope, footprint corners, nothing
    above the source top), material parameters/texture assignments/nodes untouched,
    images externalised or losslessly cropped to the shared texture store,
    and art detail explicitly
    labelled as non-survey render art.
    """
    source_doc, source_bin = read_glb(source_path)
    derived_doc, derived_bin = read_glb_bytes(derived_bytes, label)

    for key in ("nodes", "samplers", "textures", "scene", "scenes", "meshes"):
        if derived_doc.get(key) != source_doc.get(key):
            fail(f"{label}: derived GLB changed {key}")

    source_asset = strict_object(source_doc.get("asset"), "source asset")
    derived_asset = strict_object(derived_doc.get("asset"), "derived asset")
    derived_extras = strict_object(derived_asset.get("extras"), "derived asset extras")
    art = derived_extras.get("art_detail")
    if art != expected_art:
        fail(f"{label}: asset.extras.art_detail does not match the generated detail")
    if art.get("schema") != ART_DETAIL_SCHEMA or art.get("class") != ART_DETAIL_CLASS:
        fail(f"{label}: art detail is not labelled as non-survey render art")
    if float(art.get("max_up_m", float("inf"))) > top_up + ANCHOR_TOL_M:
        fail(f"{label}: art detail claims a top above the source top height")
    derived_asset_wo = json.loads(json.dumps(derived_asset))
    derived_asset_wo["extras"].pop("art_detail", None)
    if derived_asset_wo != source_asset:
        fail(f"{label}: derived asset metadata changed outside art_detail")

    style = source_asset["extras"]["style"]
    profile = facade_tile_profile(source_doc, source_bin, style, height, label)
    tiling = art["detail"]["facade_tiling"]
    cropped = tiling.get("mode") == "source-middle-crop"
    derived_materials = json.loads(json.dumps(derived_doc.get("materials")))
    source_materials = source_doc.get("materials")
    channels = None
    if cropped:
        profile, panes = middle_tile_profile(style, profile, label)
        channels = tile_channel_metadata(source_doc, source_bin, style, label)
        if tiling.get("channels") != channels:
            fail(f"{label}: middle tile channels disagree with canonical source crop provenance")
        if tiling.get("source_crop_px") != panes["source_crop_px"] or tiling.get("tile_size_px") != panes["tile_size_px"]:
            fail(f"{label}: middle tile dimensions disagree with the authored source crop")
        material_extras = derived_materials[0].get("extras", {})
        if material_extras.pop("authoredFacadePaneProfile", None) != panes:
            fail(f"{label}: authored glass panes disagree with source crop coordinates")
        roof_extras = derived_materials[1].get("extras", {})
        if roof_extras.pop("authoredBuildingMaterialRole", None) != "opaque-roof":
            fail(f"{label}: derived roof material role is missing or changed")
        for index in (0, 1):
            if "extras" not in source_materials[index] and not derived_materials[index].get("extras"):
                derived_materials[index].pop("extras", None)
    if derived_materials != source_materials:
        fail(f"{label}: derived GLB changed source material parameters or assignments")

    source_images = strict_array(source_doc.get("images"), "source images")
    source_views = strict_array(source_doc.get("bufferViews"), "source bufferViews")
    derived_images = strict_array(derived_doc.get("images"), "derived images")
    if len(derived_images) != len(source_images):
        fail(f"{label}: image count changed")
    for index, image in enumerate(derived_images):
        uri = image.get("uri")
        if not isinstance(uri, str) or not uri.startswith("assets/"):
            fail(f"{label}: derived image {index} is not content-addressed")
        if image.get("bufferView") is not None:
            fail(f"{label}: derived image {index} still embeds bytes")
        digest = uri[len("assets/"):]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            fail(f"{label}: derived image {index} digest is malformed")
        source_image = strict_object(source_images[index], "source image")
        view = strict_object(source_views[source_image["bufferView"]], "source image view")
        offset = view.get("byteOffset", 0)
        source_bytes = bytes(source_bin[offset:offset + view["byteLength"]])
        if cropped:
            matching = [channel for channel in channels.values() if channel["image_index"] == index]
            if len(matching) != 1 or matching[0]["tile_sha256"] != digest:
                fail(f"{label}: derived image {index} digest does not match the lossless source crop")
        elif hashlib.sha256(source_bytes).hexdigest() != digest:
            fail(f"{label}: derived image {index} digest does not match the canonical bytes")
        if image.get("mimeType") != source_image.get("mimeType"):
            fail(f"{label}: derived image {index} mimeType changed")

    validate_accessors(derived_doc, label)
    lo, hi = position_bounds(derived_doc, derived_bin)
    for index, axis in enumerate(("east", "up", "south")):
        if lo[index] < expected_lo[index] - ENVELOPE_TOL_M:
            fail(f"{label}: derived POSITION min {axis} {lo[index]} leaves the source envelope {expected_lo[index]}")
        if hi[index] > expected_hi[index] + ENVELOPE_TOL_M:
            fail(f"{label}: derived POSITION max {axis} {hi[index]} exceeds the source envelope {expected_hi[index]}")
        if abs(lo[index] - expected_lo[index]) > ENVELOPE_TOL_M \
                or abs(hi[index] - expected_hi[index]) > ENVELOPE_TOL_M:
            fail(f"{label}: derived geometry no longer touches the source envelope on {axis}")

    # Default UV-only correction must preserve the canonical wall payload.
    # Recompute physical UVs from source geometry independently of the packed
    # accessor, rather than accepting self-reported extras as verification.
    if not art["layers"]["facade_relief"]:
        for index in (0, 1, 3):
            if read_accessor(source_doc, source_bin, index, label) != read_accessor(derived_doc, derived_bin, index, label):
                fail(f"{label}: UV derivation changed a canonical facade position, normal or index")
        expected_uv = metric_facade_uvs(read_accessor(source_doc, source_bin, 0, label), profile, label)
        actual_uv = read_accessor(derived_doc, derived_bin, 2, label)
        if len(actual_uv) != len(expected_uv) or any(
                abs(actual[axis] - expected[axis]) > 2e-5
                for actual, expected in zip(actual_uv, expected_uv) for axis in range(2)):
            fail(f"{label}: derived facade UVs disagree with the authored metre tile profile")

    # Every footprint corner must remain present at base and at the source top,
    # and no derived vertex may leave the footprint outline.
    facade_positions = read_accessor(derived_doc, derived_bin, 0, label)
    roof_positions = read_accessor(derived_doc, derived_bin, 4, label)
    tol = 1e-4
    base_points = [p for p in facade_positions if abs(p[1]) <= tol]
    top_points = [(p[0], p[2]) for p in facade_positions if abs(p[1] - height) <= tol] \
        + [(p[0], p[2]) for p in roof_positions if abs(p[1] - height) <= tol]
    for east, north in ring:
        want_x = east - anchor[0]
        want_z = anchor[1] - north
        if not any(abs(p[0] - want_x) <= tol and abs(p[2] - want_z) <= tol
                   for p in base_points):
            fail(f"{label}: footprint corner missing from the derived base ring")
        if not any(abs(x - want_x) <= tol and abs(z - want_z) <= tol
                   for x, z in top_points):
            fail(f"{label}: footprint corner missing from the derived roof ring")
    for position in facade_positions:
        point = (position[0] + anchor[0], anchor[1] - position[2])
        if not _inside_or_on(point, ring):
            fail(f"{label}: derived facade vertex leaves the source footprint")
    for position in roof_positions:
        point = (position[0] + anchor[0], anchor[1] - position[2])
        if not _inside_or_on(point, ring):
            fail(f"{label}: derived roof vertex leaves the source footprint")


def read_glb_bytes(raw: bytes, label: str) -> tuple[dict, bytes]:
    if len(raw) < 20:
        fail(f"{label}: not a GLB container")
    magic, version, length = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF" or version != 2 or length != len(raw):
        fail(f"{label}: bad GLB header")
    chunk_length, chunk_type = struct.unpack_from("<II", raw, 12)
    if chunk_type != 0x4E4F534A or 20 + chunk_length > len(raw):
        fail(f"{label}: missing JSON chunk")
    try:
        document = json.loads(raw[20:20 + chunk_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{label}: invalid glTF JSON: {error}")
    bin_start = 20 + chunk_length
    binary = b""
    if bin_start + 8 <= len(raw):
        bin_length, bin_type = struct.unpack_from("<II", raw, bin_start)
        if bin_type == 0x004E4942:
            binary = raw[bin_start + 8:bin_start + 8 + bin_length]
    if bin_start + 8 + len(binary) != len(raw):
        fail(f"{label}: trailing bytes after the BIN chunk")
    return document, binary


def pack_linkage(pack_manifest: dict, effective_osm: dict, objects: dict) -> dict[str, list[str]]:
    targets: set[str] = set()
    for batch in pack_manifest.get("batches", []):
        for item in batch.get("ranges", []):
            target = item.get("target")
            if isinstance(target, dict) and target.get("kind") == "building":
                targets.add(target["id"])
    index: dict[tuple, list[str]] = {}
    for element in effective_osm.get("elements", []):
        tags = element.get("tags") or {}
        source_id = tags.get("aero_bench:source_id")
        if source_id is None:
            continue
        key = (tags.get("aero_bench:source_type"), str(source_id),
               tags.get("aero_bench:component_index", "0"))
        # Reproduce the pack's JS Number rounding of the negative synthetic id.
        packed_id = f"{element['type'][0]}{int(float(element['id']))}"
        index.setdefault(key, []).append(packed_id)
    linked: dict[str, list[str]] = {}
    for building in objects["buildings"]:
        object_id = building["object_id"]
        component = object_id.rsplit(".component.", 1)[-1]
        osm = building["osm"]
        key = (osm["type"], str(osm["id"]), component)
        candidates = [packed for packed in index.get(key, []) if packed in targets]
        if len(candidates) > 2:
            fail(f"{object_id}: {len(candidates)} pack targets resolve to one object")
        linked[object_id] = sorted(set(candidates))
    used = {target for value in linked.values() for target in value}
    if targets - used:
        fail(f"pack has building targets not claimed by any object: {sorted(targets - used)[:4]}")
    return linked


def main(argv: list[str] | None = None) -> int:
    command = parse_options(argv)
    options = command.art_detail
    build = resolve_region_build(command)
    started = time.time()
    objects_path = build.objects_path
    scaleout_manifest_path = build.scaleout_manifest_path
    fragment_path = build.fragment_path
    pack_manifest_path = build.pack_manifest_path
    effective_osm_path = build.effective_osm_path
    # Regeneration owns only the building manifest reference; authored road,
    # traffic and environment configuration is kept from the current file.
    scene_config = build.scene_config
    scene_building = strict_object(scene_config.get("building_render"), "scene building_render")

    objects = strict_object(load_json(objects_path, build.pins["objects_json"], "objects.json"), "objects")
    scaleout = strict_object(load_json(scaleout_manifest_path, build.pins["scaleout_manifest"], "scaleout manifest"), "scaleout manifest")
    fragment = strict_object(load_json(fragment_path, build.pins["fragment"], "catalog fragment"), "catalog fragment")
    pack_manifest = strict_object(load_json(pack_manifest_path, build.pins["pack_manifest"], "pack manifest"), "pack manifest")
    effective_osm = strict_object(load_json(effective_osm_path, build.pins["pack_source"], "effective osm"), "effective osm")

    if objects.get("schema_version") != "aero-bench.urban-scene-objects/v1":
        fail("objects.json schema mismatch")
    if objects.get("coordinate_frame") != "ENU":
        fail("objects.json coordinate frame is not ENU")
    origin = strict_object(objects.get("origin"), "objects origin")
    for key, expected in build.scene_origin.items():
        if abs(float(origin[key]) - expected) > 1e-9:
            fail(f"objects.json origin {key} is {origin[key]}, expected {expected}")
    if pack_manifest.get("source", {}).get("sha256") != build.pins["pack_source"]:
        fail("pack source digest does not match the pinned scene source")

    buildings = strict_array(objects.get("buildings"), "objects.buildings")
    scaleout_buildings = strict_array(scaleout.get("buildings"), "scaleout.buildings")
    fragment_entries = strict_array(fragment.get("building_render"), "fragment.building_render")
    glb_digests = strict_object(fragment.get("glb_digests"), "fragment.glb_digests")

    expected_buildings = build.expected_buildings
    if len(buildings) != expected_buildings or len(scaleout_buildings) != expected_buildings \
            or len(fragment_entries) != expected_buildings:
        fail(f"expected {expected_buildings} entries everywhere, got objects={len(buildings)} "
             f"scaleout={len(scaleout_buildings)} fragment={len(fragment_entries)}")

    object_ids = [entry["object_id"] for entry in buildings]
    if len(set(object_ids)) != expected_buildings:
        fail("objects.json has duplicate object_ids")
    if set(object_ids) != build.expected_object_ids:
        fail("objects.json building identities differ from the source context")
    scaleout_by_id = {}
    for entry in scaleout_buildings:
        object_id = entry.get("object_id")
        if object_id in scaleout_by_id:
            fail(f"scaleout manifest duplicates {object_id}")
        scaleout_by_id[object_id] = entry
    fragment_by_id = {}
    for entry in fragment_entries:
        object_id = entry.get("object_id")
        if object_id in fragment_by_id:
            fail(f"fragment duplicates {object_id}")
        fragment_by_id[object_id] = entry
    if set(scaleout_by_id) != set(object_ids) or set(fragment_by_id) != set(object_ids):
        fail("object_id sets differ across objects.json / scaleout manifest / fragment")

    linkage = pack_linkage(pack_manifest, effective_osm, objects)
    linked_objects = sum(1 for value in linkage.values() if value)
    missing_objects = sorted(key for key, value in linkage.items() if not value)

    combined = hashlib.sha256()
    total_bytes = 0
    derived_total_bytes = 0
    styles: dict[str, int] = {}
    art_relief_buildings = 0
    art_roof_buildings = 0
    art_structure_buildings = 0
    derived_vertices_total = 0
    derived_triangles_total = 0
    source_vertices_total = 0
    source_triangles_total = 0
    entries = []
    texture_store: dict[str, bytes] = {}
    source_texture_store: dict[str, bytes] = {}
    derived_bytes_by_digest: dict[str, bytes] = {}
    for building in sorted(buildings, key=lambda item: item["object_id"]):
        object_id = building["object_id"]
        footprint = strict_array(building.get("footprint_enu_m"), f"{object_id} footprint")
        min_e = min(float(p[0]) for p in footprint)
        max_e = max(float(p[0]) for p in footprint)
        min_n = min(float(p[1]) for p in footprint)
        max_n = max(float(p[1]) for p in footprint)
        base_up = float(building["base_enu_up_m"])
        top_up = float(building["top_enu_up_m"])
        height = float(building["extrusion_height_m"])
        if abs((top_up - base_up) - height) > 1e-9:
            fail(f"{object_id}: extrusion height does not span base..top")

        scaleout_entry = strict_object(scaleout_by_id[object_id], "scaleout entry")
        fragment_entry = strict_object(fragment_by_id[object_id], "fragment entry")
        digest_record = strict_object(glb_digests.get(object_id), f"glb_digests[{object_id}]")
        source_path = build.canonical_assets / f"{object_id}.glb"
        if not source_path.is_file():
            fail(f"{object_id}: GLB missing at {source_path}")
        raw = source_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != scaleout_entry.get("glb_sha256") or digest != digest_record.get("sha256"):
            fail(f"{object_id}: GLB digest mismatch against manifest/fragment")
        if len(raw) != digest_record.get("bytes") or len(raw) != scaleout_entry.get("glb_bytes"):
            fail(f"{object_id}: GLB byte size mismatch")
        if fragment_entry.get("file") != f"assets/{object_id}.glb" \
                or fragment_entry.get("media_type") != "model/gltf-binary" \
                or fragment_entry.get("source_frame") != "asset_local":
            fail(f"{object_id}: fragment entry contract mismatch")
        total_bytes += len(raw)
        combined.update(source_path.name.encode("utf-8"))
        combined.update(raw)

        document, binary = read_glb(source_path)
        asset = strict_object(document.get("asset"), "glb asset")
        if asset.get("version") != "2.0" or asset.get("generator") is None:
            fail(f"{object_id}: glTF asset header is incomplete")
        extras = strict_object(asset.get("extras"), "glb asset extras")
        if extras.get("object_id") != object_id:
            fail(f"{object_id}: glTF extras object_id mismatch")
        if extras.get("source_frame") != "asset_local":
            fail(f"{object_id}: glTF source_frame is not asset_local")
        scene_block = strict_object(extras.get("scene"), "glb scene extras")
        if scene_block.get("objects_json_sha256") != build.pins["objects_json"]:
            fail(f"{object_id}: glTF is bound to a different objects.json")
        frame = strict_object(extras.get("frame"), "glb frame extras")
        if frame.get("placement") != build.pins["placement_rule"]:
            fail(f"{object_id}: placement rule drift")
        anchor_east = float(frame["anchor_east_m"])
        anchor_north = float(frame["anchor_north_m"])
        anchor_base = float(frame["base_enu_up_m"])
        if abs(anchor_base - base_up) > ANCHOR_TOL_M:
            fail(f"{object_id}: GLB base {anchor_base} != scene base {base_up}")
        if abs(float(scaleout_entry["anchor_east_m"]) - anchor_east) > ANCHOR_TOL_M \
                or abs(float(scaleout_entry["anchor_north_m"]) - anchor_north) > ANCHOR_TOL_M:
            fail(f"{object_id}: GLB anchor differs from the scaleout manifest anchor")
        centroid = footprint_centroid(footprint)
        if abs(anchor_east - centroid[0]) > ANCHOR_TOL_M or abs(anchor_north - centroid[1]) > ANCHOR_TOL_M:
            fail(f"{object_id}: GLB anchor ({anchor_east}, {anchor_north}) != footprint centroid {centroid}")
        building_extras = strict_object(extras.get("building"), "glb building extras")
        if abs(float(building_extras["height_m"]) - height) > 1e-9:
            fail(f"{object_id}: GLB height {building_extras['height_m']} != scene height {height}")
        style = strict_object(extras.get("style"), "glb style extras")
        style_id = style.get("style_id")
        if not isinstance(style_id, str) or not style_id:
            fail(f"{object_id}: missing style_id")
        if digest_record.get("style_id") != style_id:
            fail(f"{object_id}: style_id differs between GLB and fragment")
        styles[style_id] = styles.get(style_id, 0) + 1
        nodes = strict_array(document.get("nodes"), "glb nodes")
        if len(nodes) != 1 or nodes[0].get("name") != object_id or "mesh" not in nodes[0]:
            fail(f"{object_id}: unexpected node layout")
        if any(key in nodes[0] for key in ("translation", "rotation", "scale", "matrix")):
            fail(f"{object_id}: node carries a transform; placement must be pure translation")

        lo, hi = position_bounds(document, binary)
        expected_lo = (min_e - anchor_east, 0.0, anchor_north - max_n)
        expected_hi = (max_e - anchor_east, height, anchor_north - min_n)
        for index, axis in enumerate(("east", "up", "south")):
            if abs(lo[index] - expected_lo[index]) > ENVELOPE_TOL_M:
                fail(f"{object_id}: POSITION min {axis} {lo[index]} != envelope {expected_lo[index]}")
            if abs(hi[index] - expected_hi[index]) > ENVELOPE_TOL_M:
                fail(f"{object_id}: POSITION max {axis} {hi[index]} != envelope {expected_hi[index]}")

        # Derive the runtime (deduplicated) GLB from the verified canonical
        # source, adding deterministic art detail that never leaves the
        # footprint or the source top height.
        ring = ring_from_footprint(footprint)
        derived_bytes, extracted, art_stats = derive_art_detail_glb(
            document, binary, object_id, ring=ring, base_up=base_up,
            height=height, top_up=top_up,
            anchor=(anchor_east, anchor_north), style=style, options=options)
        verify_derivation(source_path, derived_bytes, object_id, ring=ring,
                          height=height, top_up=top_up,
                          anchor=(anchor_east, anchor_north),
                          expected_lo=expected_lo, expected_hi=expected_hi,
                          expected_art=art_stats["art"])
        detail = art_stats["entry"]
        if detail["relief"]:
            art_relief_buildings += 1
        if detail["parapet"]:
            art_roof_buildings += 1
        if detail["roof_structures"]:
            art_structure_buildings += 1
        derived_vertices_total += detail["vertices"]
        derived_triangles_total += detail["triangles"]
        source_vertices_total += art_stats["source_vertices"]
        source_triangles_total += art_stats["source_triangles"]
        derived_digest = hashlib.sha256(derived_bytes).hexdigest()
        if derived_digest in derived_bytes_by_digest \
                and derived_bytes_by_digest[derived_digest] != derived_bytes:
            fail(f"{object_id}: sha256 collision on derived GLB {derived_digest}")
        derived_bytes_by_digest[derived_digest] = derived_bytes
        for image_digest, image_bytes in extracted.items():
            if image_digest in texture_store and texture_store[image_digest] != image_bytes:
                fail(f"{object_id}: sha256 collision on texture {image_digest}")
            texture_store[image_digest] = image_bytes
        for image_digest, image_bytes in art_stats["archived_source_images"].items():
            if image_digest in source_texture_store and source_texture_store[image_digest] != image_bytes:
                fail(f"{object_id}: sha256 collision on archived source texture {image_digest}")
            source_texture_store[image_digest] = image_bytes
        derived_total_bytes += len(derived_bytes)

        entries.append({
            "object_id": object_id,
            "glb": {"path": f"assets/{digest}", "sha256": digest, "bytes": len(raw)},
            "derived_glb": {"path": f"assets/{derived_digest}", "sha256": derived_digest,
                            "bytes": len(derived_bytes)},
            "anchor_east_m": anchor_east,
            "anchor_north_m": anchor_north,
            "base_enu_up_m": base_up,
            "height_m": height,
            "envelope": {"min_e": min_e, "min_n": min_n, "max_e": max_e, "max_n": max_n,
                         "base_up": base_up, "top_up": top_up},
            "style_id": style_id,
            "art_detail": detail,
            "pack_target_ids": linkage[object_id],
            "osm": {"type": building["osm"]["type"], "id": int(building["osm"]["id"])},
        })

    combined_digest = combined.hexdigest()
    if combined_digest != build.pins["combined_glbs"]:
        fail(f"combined GLB digest {combined_digest} != pinned {build.pins['combined_glbs']}")

    # Spatial blocks for lazy loading: 120 m cells over anchor positions.
    cells: dict[tuple[int, int], list[str]] = {}
    anchors = {entry["object_id"]: (entry["anchor_east_m"], entry["anchor_north_m"]) for entry in entries}
    for object_id, (east, north) in anchors.items():
        cell = (int(east // BLOCK_SIZE_M), int(north // BLOCK_SIZE_M))
        cells.setdefault(cell, []).append(object_id)
    ordered_cells = sorted(cells)
    cell_index = {cell: index for index, cell in enumerate(ordered_cells)}
    blocks = []
    for cell in ordered_cells:
        index = cell_index[cell]
        block_east = (cell[0] * BLOCK_SIZE_M, (cell[0] + 1) * BLOCK_SIZE_M)
        block_north = (cell[1] * BLOCK_SIZE_M, (cell[1] + 1) * BLOCK_SIZE_M)
        blocks.append({
            "index": index,
            "min_e": block_east[0], "max_e": block_east[1],
            "min_n": block_north[0], "max_n": block_north[1],
            "object_ids": sorted(cells[cell]),
        })
    for entry in entries:
        cell = (int(entry["anchor_east_m"] // BLOCK_SIZE_M), int(entry["anchor_north_m"] // BLOCK_SIZE_M))
        entry["block"] = cell_index[cell]

    if len({entry["glb"]["sha256"] for entry in entries}) != expected_buildings:
        fail("GLB digests are not unique across buildings")
    if len({entry["derived_glb"]["sha256"] for entry in entries}) != expected_buildings:
        fail("derived GLB digests are not unique across buildings")
    texture_bytes = 0
    textures = []
    for image_digest in sorted(texture_store):
        image_bytes = texture_store[image_digest]
        media_type = detect_image_media_type(image_bytes, None, f"texture {image_digest}")
        texture_bytes += len(image_bytes)
        textures.append({"sha256": image_digest, "bytes": len(image_bytes),
                         "media_type": media_type})
    referenced = set()
    for entry in entries:
        derived_doc, _ = read_glb_bytes(derived_bytes_by_digest[entry["derived_glb"]["sha256"]],
                                        entry["object_id"])
        for image in derived_doc.get("images", []):
            referenced.add(image.get("uri", "")[len("assets/"):])
    if referenced != set(texture_store):
        fail("texture store does not match the URIs referenced by the derived GLBs")
    runtime_payload_bytes = derived_total_bytes + texture_bytes
    if runtime_payload_bytes > MAX_RUNTIME_PAYLOAD_BYTES:
        fail(f"runtime payload {runtime_payload_bytes} bytes exceeds the "
             f"{MAX_RUNTIME_PAYLOAD_BYTES} byte budget")
    manifest = {
        "schema_version": "aero-bench.building-render-runtime/v2",
        "scene": {
            "id": build.scene_id,
            "coordinate_frame": "ENU",
            "origin_wgs84": dict(build.scene_origin),
            "objects_json_sha256": build.pins["objects_json"],
            "placement_rule": build.pins["placement_rule"],
            "mesh_pack_manifest_sha256": build.pins["pack_manifest"],
            "mesh_pack_source_sha256": build.pins["pack_source"],
        },
        "sources": {
            "scaleout_glb_combined_sha256": build.pins["combined_glbs"],
            "scaleout_manifest_sha256": build.pins["scaleout_manifest"],
            "catalog_fragment_sha256": build.pins["fragment"],
        },
        "counts": {
            "buildings": len(entries),
            "total_bytes": total_bytes,
            "derived_total_bytes": derived_total_bytes,
            "texture_bytes": texture_bytes,
            "textures": len(textures),
            "styles": len(styles),
            "pack_linked_objects": linked_objects,
            "pack_missing_objects": len(missing_objects),
            "pack_targets": sum(len(value) for value in linkage.values()),
            "blocks": len(blocks),
            "art_detail_buildings": sum(
                1 for entry in entries
                if entry["art_detail"]["relief"] or entry["art_detail"]["parapet"]
                or entry["art_detail"]["roof_structures"]),
            "art_detail_relief_buildings": art_relief_buildings,
            "art_detail_roofs": art_roof_buildings,
            "art_detail_structures": art_structure_buildings,
            "derived_vertices": derived_vertices_total,
            "derived_triangles": derived_triangles_total,
            "source_vertices": source_vertices_total,
            "source_triangles": source_triangles_total,
        },
        "art_detail": {
            "schema": ART_DETAIL_SCHEMA,
            "class": ART_DETAIL_CLASS,
            "layers": geometry_layers(options),
            "disclaimer_zh": ART_DETAIL_NOTE_ZH,
            "disclaimer_en": ART_DETAIL_NOTE_EN,
            "note": ("entries[].art_detail and asset.extras.art_detail describe "
                     "derived render art detail only (authored metre-scale facade UVs, "
                     "facade bay relief, parapet "
                     "cornice, roof structures); it is not survey or measurement "
                     "data, never leaves the canonical footprint, and never rises "
                     "above the canonical top height") + (
                     "; shared middle source crops and pane semantics are authored display art, "
                     "with all original source PNG bytes retained" if options.facade_middle_tiles else ""),
        },
        "pack_missing_object_ids": missing_objects,
        "block_size_m": BLOCK_SIZE_M,
        "textures": textures,
        "blocks": blocks,
        "buildings": entries,
    }

    build.output_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = build.output_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    staged: set[str] = set()

    def stage_bytes(name: str, data: bytes) -> None:
        if hashlib.sha256(data).hexdigest() != name:
            fail(f"staged payload {name}: digest does not match its bytes")
        target = asset_dir / name
        if target.exists():
            if sha256_file(target) != name:
                target.write_bytes(data)
                if sha256_file(target) != name:
                    fail(f"staged asset {name} is corrupt")
        else:
            target.write_bytes(data)
        staged.add(name)

    # Derived GLBs and the shared texture store replace the generated canonical
    # copies (their sources remain in validation/ as canonical evidence).
    for entry in entries:
        stage_bytes(entry["derived_glb"]["sha256"],
                    derived_bytes_by_digest[entry["derived_glb"]["sha256"]])
    for image_digest, image_bytes in texture_store.items():
        stage_bytes(image_digest, image_bytes)
    # Retain the 45 exact original PNGs as provenance assets. Only active tile
    # URIs belong to manifest.textures and are fetched by GLTFLoader.
    for image_digest, image_bytes in source_texture_store.items():
        stage_bytes(image_digest, image_bytes)
    source_context_digest = hashlib.sha256(build.source_context_bytes).hexdigest()
    stage_bytes(source_context_digest, build.source_context_bytes)
    pruned_canonical = 0
    for leftover in asset_dir.iterdir():
        if leftover.is_file() and leftover.name not in staged:
            if leftover.suffix == "" and len(leftover.name) == 64 \
                    and leftover.name in {e["glb"]["sha256"] for e in entries}:
                pruned_canonical += 1
            leftover.unlink()

    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path = build.output_dir / "manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_alias = asset_dir / manifest_sha
    try:
        os.link(manifest_path, manifest_alias)
    except OSError:
        shutil.copy2(manifest_path, manifest_alias)

    scene_building["manifest"] = {"sha256": manifest_sha, "size_bytes": len(manifest_bytes)}
    build.scene_output.parent.mkdir(parents=True, exist_ok=True)
    build.scene_output.write_bytes(
        (json.dumps(scene_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    summary = {
        "status": "ok",
        "buildings": len(entries),
        "total_bytes": total_bytes,
        "derived_total_bytes": derived_total_bytes,
        "texture_bytes": texture_bytes,
        "textures": len(textures),
        "runtime_payload_bytes": runtime_payload_bytes,
        "art_detail_layers": geometry_layers(options),
        "pruned_canonical_glbs": pruned_canonical,
        "styles": len(styles),
        "blocks": len(blocks),
        "pack_linked_objects": linked_objects,
        "pack_missing_object_ids": missing_objects,
        "art_detail_relief_buildings": art_relief_buildings,
        "art_detail_roofs": art_roof_buildings,
        "art_detail_structures": art_structure_buildings,
        "derived_vertices": derived_vertices_total,
        "derived_triangles": derived_triangles_total,
        "source_vertices": source_vertices_total,
        "source_triangles": source_triangles_total,
        "combined_glb_sha256": combined_digest,
        "manifest_sha256": manifest_sha,
        "manifest_bytes": len(manifest_bytes),
        "scene_config": str(build.scene_output),
        "elapsed_seconds": round(time.time() - started, 2),
    }
    if options.facade_middle_tiles:
        summary.update({
            "facade_middle_tiles": True,
            "archived_source_textures": len(source_texture_store),
            "archived_source_texture_bytes": sum(len(raw) for raw in source_texture_store.values()),
            "stored_unique_texture_bytes": sum(len(raw) for raw in {**source_texture_store, **texture_store}.values()),
            "stored_payload_bytes": derived_total_bytes + sum(len(raw) for raw in {**source_texture_store, **texture_store}.values()),
        })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as error:
        print(f"BUILD FAILED: {error}", file=sys.stderr)
        sys.exit(1)
