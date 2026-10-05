"""Deterministic offline OpenStreetMap scene compilation.

This compiler deliberately produces a small, auditable interchange package rather
than a viewer-specific scene.  It crops a fully closed OSM JSON document around a
fixed WGS84/ENU origin, preserves the selected OSM source verbatim in JSON and
faithfully in OSM XML, and derives a single ENU description for Gazebo, SUMO,
ns-3, and public-scene consumers.

The module does not fetch map data, synthesize buildings, or replace source
geometry with a grid.  Height values which cannot be read from OSM are explicit
simulation assumptions in the emitted package.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.sax.saxutils import quoteattr

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.frame_math import EnuTransform, Vector3


SCENE_COMPILER_SCHEMA_VERSION: Final = "aero-bench.urban-scene-compiler/v1"
"""Version of the deterministic compiler package format."""

DEFAULT_ORIGIN_LATITUDE_DEG: Final = 31.2304
DEFAULT_ORIGIN_LONGITUDE_DEG: Final = 121.4737
DEFAULT_ORIGIN_ELLIPSOID_HEIGHT_M: Final = 50.0
DEFAULT_ORIGIN_GEOID_UNDULATION_M: Final = 30.0
DEFAULT_ORIGIN_AMSL_M: Final = 20.0
DEFAULT_CROP_MIN_EAST_M: Final = -500.0
DEFAULT_CROP_MAX_EAST_M: Final = 500.0
DEFAULT_CROP_MIN_NORTH_M: Final = -500.0
DEFAULT_CROP_MAX_NORTH_M: Final = 500.0
DEFAULT_BUILDING_HEIGHT_M: Final = 12.0
DEFAULT_BUILDING_LEVEL_HEIGHT_M: Final = 3.0
REQUIRED_SHAPELY_VERSION: Final = "2.0.7"
"""Pinned geometry engine required for deterministic polygon clipping/triangulation."""

REQUIRED_NETCONVERT_VERSION: Final = "1.27.1"
"""SUMO netconvert identity required for a formal generated network."""

_ELEMENT_TYPES: Final = frozenset({"node", "way", "relation"})
_OSM_METADATA_ATTRIBUTES: Final = (
    "visible",
    "version",
    "changeset",
    "timestamp",
    "user",
    "uid",
)
_LENGTH_RE: Final = re.compile(
    r"^\s*(?P<value>[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*"
    r"(?P<unit>m|metre|metres|meter|meters)?\s*$",
    re.IGNORECASE,
)
_LEVEL_RE: Final = re.compile(r"^\s*(?P<value>[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*$")


class SceneCompilationError(ValueError):
    """Raised when offline source data cannot produce an authoritative scene."""


@dataclass(frozen=True, slots=True)
class SceneOrigin:
    """Explicit shared vertical and horizontal reference for the urban scene."""

    latitude_deg: float = DEFAULT_ORIGIN_LATITUDE_DEG
    longitude_deg: float = DEFAULT_ORIGIN_LONGITUDE_DEG
    ellipsoid_height_m: float = DEFAULT_ORIGIN_ELLIPSOID_HEIGHT_M
    geoid_undulation_m: float = DEFAULT_ORIGIN_GEOID_UNDULATION_M
    amsl_m: float = DEFAULT_ORIGIN_AMSL_M

    def __post_init__(self) -> None:
        for name, value in (
            ("latitude_deg", self.latitude_deg),
            ("longitude_deg", self.longitude_deg),
            ("ellipsoid_height_m", self.ellipsoid_height_m),
            ("geoid_undulation_m", self.geoid_undulation_m),
            ("amsl_m", self.amsl_m),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SceneCompilationError(f"origin {name} must be a finite number")
            if not math.isfinite(float(value)):
                raise SceneCompilationError(f"origin {name} must be a finite number")
        if not -90.0 < float(self.latitude_deg) < 90.0:
            raise SceneCompilationError("origin latitude_deg must be inside (-90, 90)")
        if not -180.0 <= float(self.longitude_deg) <= 180.0:
            raise SceneCompilationError("origin longitude_deg must be in [-180, 180]")
        expected = float(self.amsl_m) + float(self.geoid_undulation_m)
        if not math.isclose(
            float(self.ellipsoid_height_m), expected, rel_tol=0.0, abs_tol=1e-9
        ):
            raise SceneCompilationError(
                "origin ellipsoid_height_m must equal amsl_m plus geoid_undulation_m"
            )

    def enu_transform(self) -> EnuTransform:
        return EnuTransform.from_origin(
            longitude_deg=float(self.longitude_deg),
            latitude_deg=float(self.latitude_deg),
            altitude_m=float(self.ellipsoid_height_m),
        )

    def manifest_document(self) -> dict[str, object]:
        return {
            "latitude_deg": float(self.latitude_deg),
            "longitude_deg": float(self.longitude_deg),
            "ellipsoid_height_m": float(self.ellipsoid_height_m),
            "geoid_undulation_m": float(self.geoid_undulation_m),
            "amsl_m": float(self.amsl_m),
            "vertical_relation": "ellipsoid_height_m = amsl_m + geoid_undulation_m",
            "frame": "WGS84->ECEF->ENU",
            "enu_axes": ["east", "north", "up"],
        }


@dataclass(frozen=True, slots=True)
class EnuBounds:
    """Closed horizontal crop rectangle expressed in metres in the shared ENU."""

    min_east_m: float = DEFAULT_CROP_MIN_EAST_M
    max_east_m: float = DEFAULT_CROP_MAX_EAST_M
    min_north_m: float = DEFAULT_CROP_MIN_NORTH_M
    max_north_m: float = DEFAULT_CROP_MAX_NORTH_M

    def __post_init__(self) -> None:
        for name, value in (
            ("min_east_m", self.min_east_m),
            ("max_east_m", self.max_east_m),
            ("min_north_m", self.min_north_m),
            ("max_north_m", self.max_north_m),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SceneCompilationError(f"crop {name} must be a finite number")
            if not math.isfinite(float(value)):
                raise SceneCompilationError(f"crop {name} must be a finite number")
        if float(self.min_east_m) >= float(self.max_east_m):
            raise SceneCompilationError("crop min_east_m must be smaller than max_east_m")
        if float(self.min_north_m) >= float(self.max_north_m):
            raise SceneCompilationError("crop min_north_m must be smaller than max_north_m")

    def manifest_document(self) -> dict[str, float]:
        return {
            "min_east_m": float(self.min_east_m),
            "max_east_m": float(self.max_east_m),
            "min_north_m": float(self.min_north_m),
            "max_north_m": float(self.max_north_m),
        }

    def corners(self) -> tuple[tuple[float, float], ...]:
        return (
            (float(self.min_east_m), float(self.min_north_m)),
            (float(self.max_east_m), float(self.min_north_m)),
            (float(self.max_east_m), float(self.max_north_m)),
            (float(self.min_east_m), float(self.max_north_m)),
        )


@dataclass(frozen=True, slots=True)
class OsmDataset:
    """Strictly parsed, internally closed OSM JSON source document."""

    root_metadata: dict[str, object]
    nodes: dict[int, dict[str, object]]
    ways: dict[int, dict[str, object]]
    relations: dict[int, dict[str, object]]
    source_sha256: str
    source_bytes: bytes

    @property
    def elements(self) -> dict[str, dict[int, dict[str, object]]]:
        return {
            "node": self.nodes,
            "way": self.ways,
            "relation": self.relations,
        }


@dataclass(frozen=True, slots=True)
class CroppedOsmDataset:
    """Selected OSM elements plus their complete forward and parent relation closure."""

    dataset: OsmDataset
    node_ids: frozenset[int]
    way_ids: frozenset[int]
    relation_ids: frozenset[int]

    def selected_elements(self) -> list[dict[str, object]]:
        return [
            *[
                copy.deepcopy(self.dataset.nodes[element_id])
                for element_id in sorted(self.node_ids)
            ],
            *[
                copy.deepcopy(self.dataset.ways[element_id])
                for element_id in sorted(self.way_ids)
            ],
            *[
                copy.deepcopy(self.dataset.relations[element_id])
                for element_id in sorted(self.relation_ids)
            ],
        ]


@dataclass(frozen=True, slots=True)
class SceneCompilationResult:
    """Immutable references returned only after a complete package is published."""

    output_dir: Path
    manifest_path: Path
    source_sha256: str
    cropped_source_sha256: str
    building_count: int
    road_count: int
    netconvert_generated: bool


def _require_json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SceneCompilationError(f"{label} must be a JSON object")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SceneCompilationError(f"{label} must be a string")
    return value


def _require_osm_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        raise SceneCompilationError(f"{label} must be a non-zero integer")
    return value


def _require_finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneCompilationError(f"{label} must be a finite number")
    as_float = float(value)
    if not math.isfinite(as_float):
        raise SceneCompilationError(f"{label} must be a finite number")
    return as_float


def _strict_tags(element: Mapping[str, object], label: str) -> None:
    tags = element.get("tags")
    if tags is None:
        return
    tags_object = _require_json_object(tags, f"{label}.tags")
    for key, value in tags_object.items():
        if not isinstance(key, str) or not key:
            raise SceneCompilationError(f"{label}.tags keys must be non-empty strings")
        if not isinstance(value, str):
            raise SceneCompilationError(f"{label}.tags[{key!r}] must be a string")


def _strict_node(element: dict[str, object], label: str) -> None:
    latitude = _require_finite_number(element.get("lat"), f"{label}.lat")
    longitude = _require_finite_number(element.get("lon"), f"{label}.lon")
    if not -90.0 <= latitude <= 90.0:
        raise SceneCompilationError(f"{label}.lat must be in [-90, 90]")
    if not -180.0 <= longitude <= 180.0:
        raise SceneCompilationError(f"{label}.lon must be in [-180, 180]")
    _strict_tags(element, label)


def _strict_way(element: dict[str, object], label: str) -> None:
    node_refs = element.get("nodes")
    if not isinstance(node_refs, list) or not node_refs:
        raise SceneCompilationError(f"{label}.nodes must be a non-empty array")
    for index, ref in enumerate(node_refs):
        _require_osm_id(ref, f"{label}.nodes[{index}]")
    _strict_tags(element, label)


def _strict_relation(element: dict[str, object], label: str) -> None:
    members = element.get("members")
    if not isinstance(members, list):
        raise SceneCompilationError(f"{label}.members must be an array")
    for index, raw_member in enumerate(members):
        member = _require_json_object(raw_member, f"{label}.members[{index}]")
        member_type = _require_string(
            member.get("type"), f"{label}.members[{index}].type"
        )
        if member_type not in _ELEMENT_TYPES:
            raise SceneCompilationError(
                f"{label}.members[{index}].type must be node, way, or relation"
            )
        _require_osm_id(member.get("ref"), f"{label}.members[{index}].ref")
        _require_string(member.get("role"), f"{label}.members[{index}].role")
    _strict_tags(element, label)


def _validate_reference_closure(dataset: OsmDataset) -> None:
    for way_id, way in dataset.ways.items():
        for index, node_id in enumerate(way["nodes"]):
            if node_id not in dataset.nodes:
                raise SceneCompilationError(
                    f"way/{way_id} nodes[{index}] references missing node/{node_id}"
                )
    elements = dataset.elements
    for relation_id, relation in dataset.relations.items():
        for index, member in enumerate(relation["members"]):
            member_type = member["type"]
            member_id = member["ref"]
            if member_id not in elements[member_type]:
                raise SceneCompilationError(
                    f"relation/{relation_id} members[{index}] references missing "
                    f"{member_type}/{member_id}"
                )


def load_osm_json(source_path: str | Path) -> OsmDataset:
    """Strictly load an offline OSM JSON document and verify all references.

    OSM element values remain unmodified in the returned dataset.  The compiler
    validates only the OSM JSON structures required to establish type, identity,
    geometry references, tags, and relation closure; unknown standard metadata is
    retained verbatim for provenance.
    """

    path = Path(source_path)
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise SceneCompilationError(f"cannot read offline OSM source {path}: {exc}") from exc
    try:
        raw = json.loads(source_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneCompilationError(f"offline OSM source is not valid JSON: {exc}") from exc
    root = _require_json_object(raw, "OSM root")
    elements_raw = root.get("elements")
    if not isinstance(elements_raw, list):
        raise SceneCompilationError("OSM root.elements must be an array")
    if "version" not in root:
        raise SceneCompilationError("OSM root.version is required")
    _require_finite_number(root["version"], "OSM root.version")

    typed: dict[str, dict[int, dict[str, object]]] = {
        "node": {},
        "way": {},
        "relation": {},
    }
    for index, raw_element in enumerate(elements_raw):
        element = _require_json_object(raw_element, f"OSM root.elements[{index}]")
        element_type = _require_string(
            element.get("type"), f"OSM root.elements[{index}].type"
        )
        if element_type not in _ELEMENT_TYPES:
            raise SceneCompilationError(
                f"OSM root.elements[{index}].type must be node, way, or relation"
            )
        element_id = _require_osm_id(
            element.get("id"), f"OSM root.elements[{index}].id"
        )
        if element_id in typed[element_type]:
            raise SceneCompilationError(f"duplicate OSM {element_type}/{element_id}")
        label = f"{element_type}/{element_id}"
        if element_type == "node":
            _strict_node(element, label)
        elif element_type == "way":
            _strict_way(element, label)
        else:
            _strict_relation(element, label)
        typed[element_type][element_id] = copy.deepcopy(element)

    root_metadata = {
        key: copy.deepcopy(value) for key, value in root.items() if key != "elements"
    }
    dataset = OsmDataset(
        root_metadata=root_metadata,
        nodes=typed["node"],
        ways=typed["way"],
        relations=typed["relation"],
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        source_bytes=source_bytes,
    )
    _validate_reference_closure(dataset)
    return dataset


def _tags(element: Mapping[str, object]) -> Mapping[str, str]:
    raw = element.get("tags")
    return raw if isinstance(raw, dict) else {}


def _is_affirmative_tag(tags: Mapping[str, str], key: str) -> bool:
    value = tags.get(key)
    return value is not None and value.strip().lower() not in {"", "0", "false", "no"}


def _is_building_element(element: Mapping[str, object]) -> bool:
    tags = _tags(element)
    return _is_affirmative_tag(tags, "building") or _is_affirmative_tag(
        tags, "building:part"
    )


def _is_area_way(way: Mapping[str, object]) -> bool:
    node_refs = way["nodes"]
    if len(node_refs) < 4 or node_refs[0] != node_refs[-1]:
        return False
    tags = _tags(way)
    if _is_building_element(way) or tags.get("area", "").lower() == "yes":
        return True
    return any(
        key in tags
        for key in ("landuse", "natural", "leisure", "amenity", "water", "waterway")
    )


def _point_in_bounds(point: tuple[float, float], bounds: EnuBounds) -> bool:
    east, north = point
    return (
        float(bounds.min_east_m) <= east <= float(bounds.max_east_m)
        and float(bounds.min_north_m) <= north <= float(bounds.max_north_m)
    )


def _segment_intersects_bounds(
    first: tuple[float, float], second: tuple[float, float], bounds: EnuBounds
) -> bool:
    """Closed rectangle/segment test using deterministic Liang-Barsky clipping."""

    if _point_in_bounds(first, bounds) or _point_in_bounds(second, bounds):
        return True
    x0, y0 = first
    x1, y1 = second
    dx = x1 - x0
    dy = y1 - y0
    lower = 0.0
    upper = 1.0
    for p, q in (
        (-dx, x0 - float(bounds.min_east_m)),
        (dx, float(bounds.max_east_m) - x0),
        (-dy, y0 - float(bounds.min_north_m)),
        (dy, float(bounds.max_north_m) - y0),
    ):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > upper:
                return False
            lower = max(lower, ratio)
        else:
            if ratio < lower:
                return False
            upper = min(upper, ratio)
    return lower <= upper


def _point_on_segment(
    point: tuple[float, float], first: tuple[float, float], second: tuple[float, float]
) -> bool:
    px, py = point
    ax, ay = first
    bx, by = second
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > 1e-8:
        return False
    return (
        min(ax, bx) - 1e-10 <= px <= max(ax, bx) + 1e-10
        and min(ay, by) - 1e-10 <= py <= max(ay, by) + 1e-10
    )


def _point_in_polygon(point: tuple[float, float], ring: Sequence[tuple[float, float]]) -> bool:
    """Return true for an interior or boundary point of a closed/simple ring."""

    if len(ring) < 4 or ring[0] != ring[-1]:
        raise SceneCompilationError("polygon ring must be closed and have at least three sides")
    inside = False
    point_x, point_y = point
    for first, second in zip(ring, ring[1:]):
        if _point_on_segment(point, first, second):
            return True
        x0, y0 = first
        x1, y1 = second
        crosses = (y0 > point_y) != (y1 > point_y)
        if crosses:
            x_cross = (x1 - x0) * (point_y - y0) / (y1 - y0) + x0
            if point_x < x_cross:
                inside = not inside
    return inside


def _clip_segment_to_bounds(
    first: tuple[float, float], second: tuple[float, float], bounds: EnuBounds
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return the closed interval of a segment lying in the crop rectangle."""

    x0, y0 = first
    x1, y1 = second
    dx = x1 - x0
    dy = y1 - y0
    lower = 0.0
    upper = 1.0
    for p, q in (
        (-dx, x0 - float(bounds.min_east_m)),
        (dx, float(bounds.max_east_m) - x0),
        (-dy, y0 - float(bounds.min_north_m)),
        (dy, float(bounds.max_north_m) - y0),
    ):
        if p == 0.0:
            if q < 0.0:
                return None
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > upper:
                return None
            lower = max(lower, ratio)
        else:
            if ratio < lower:
                return None
            upper = min(upper, ratio)
    if lower > upper:
        return None
    return (
        (x0 + dx * lower, y0 + dy * lower),
        (x0 + dx * upper, y0 + dy * upper),
    )


def _clip_polyline_to_bounds(
    coordinates: Sequence[tuple[float, float]], bounds: EnuBounds
) -> tuple[tuple[float, float], ...]:
    """Compatibility helper returning the first exact clipped line component."""

    components = _clip_polyline_components_to_bounds(coordinates, bounds)
    return components[0] if components else ()


def _clip_polyline_components_to_bounds(
    coordinates: Sequence[tuple[float, float]], bounds: EnuBounds
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Clip all line components exactly; never connect disconnected fragments."""

    _polygon, LineString, box, _version = _require_shapely()
    try:
        source_line = LineString(coordinates)
    except (TypeError, ValueError) as exc:
        raise SceneCompilationError(f"source polyline cannot be clipped: {exc}") from exc
    if not source_line.is_valid:
        raise SceneCompilationError("source polyline is invalid for GEOS clipping")
    clipped = source_line.intersection(
        box(
            float(bounds.min_east_m),
            float(bounds.min_north_m),
            float(bounds.max_east_m),
            float(bounds.max_north_m),
        )
    )
    result: list[tuple[tuple[float, float], ...]] = []
    for component in _geometry_components(clipped):
        if component.geom_type != "LineString":
            continue
        component_coordinates = tuple((float(x), float(y)) for x, y in component.coords)
        if len(component_coordinates) >= 2 and len(set(component_coordinates)) >= 2:
            result.append(component_coordinates)
    return tuple(result)


def _line_or_area_intersects_bounds(
    coordinates: Sequence[tuple[float, float]],
    *,
    is_area: bool,
    bounds: EnuBounds,
) -> bool:
    if any(_point_in_bounds(point, bounds) for point in coordinates):
        return True
    if any(
        _segment_intersects_bounds(first, second, bounds)
        for first, second in zip(coordinates, coordinates[1:])
    ):
        return True
    if not is_area:
        return False
    # A closed area can entirely enclose the 1 km crop without a source vertex
    # inside it and without crossing a crop edge.  Test crop corners and centre.
    probe_points = (
        *bounds.corners(),
        (
            (float(bounds.min_east_m) + float(bounds.max_east_m)) / 2.0,
            (float(bounds.min_north_m) + float(bounds.max_north_m)) / 2.0,
        ),
    )
    return any(_point_in_polygon(point, coordinates) for point in probe_points)


def _way_coordinates(
    dataset: OsmDataset,
    way_id: int,
    node_enu: Mapping[int, tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    return tuple(node_enu[node_id] for node_id in dataset.ways[way_id]["nodes"])


def _relation_tags_relation_type(relation: Mapping[str, object]) -> str | None:
    relation_type = _tags(relation).get("type")
    return relation_type.strip().lower() if relation_type is not None else None


def _assemble_rings(
    dataset: OsmDataset,
    relation_id: int,
    role: str,
) -> tuple[tuple[int, ...], ...]:
    """Assemble unordered OSM multipolygon member ways into closed node-ID rings."""

    relation = dataset.relations[relation_id]
    parts: list[tuple[int, tuple[int, ...]]] = []
    for member in relation["members"]:
        if member["type"] != "way" or member["role"] != role:
            continue
        way_id = member["ref"]
        node_ids = tuple(dataset.ways[way_id]["nodes"])
        if len(node_ids) < 2:
            raise SceneCompilationError(
                f"multipolygon relation/{relation_id} {role} way/{way_id} has no segment"
            )
        parts.append((way_id, node_ids))
    if not parts:
        return ()

    remaining = sorted(parts, key=lambda item: item[0])
    rings: list[tuple[int, ...]] = []
    while remaining:
        _way_id, start_part = remaining.pop(0)
        chain = list(start_part)
        while chain[-1] != chain[0]:
            candidates: list[tuple[int, int, bool, tuple[int, ...]]] = []
            for index, (candidate_id, candidate) in enumerate(remaining):
                if candidate[0] == chain[-1]:
                    candidates.append((candidate_id, index, False, candidate))
                elif candidate[-1] == chain[-1]:
                    candidates.append(
                        (candidate_id, index, True, tuple(reversed(candidate)))
                    )
            if not candidates:
                raise SceneCompilationError(
                    f"multipolygon relation/{relation_id} has an unclosed {role} ring"
                )
            _candidate_id, index, _reversed, oriented = min(candidates)
            del remaining[index]
            chain.extend(oriented[1:])
        if len(set(chain[:-1])) < 3:
            raise SceneCompilationError(
                f"multipolygon relation/{relation_id} has a degenerate {role} ring"
            )
        rings.append(tuple(chain))
    return tuple(rings)


def _relation_intersects_bounds(
    dataset: OsmDataset,
    relation_id: int,
    node_enu: Mapping[int, tuple[float, float]],
    bounds: EnuBounds,
    visiting: set[int] | None = None,
) -> bool:
    relation = dataset.relations[relation_id]
    relation_type = _relation_tags_relation_type(relation)
    if relation_type in {"multipolygon", "boundary", "building"}:
        outer_rings = _assemble_rings(dataset, relation_id, "outer")
        inner_rings = _assemble_rings(dataset, relation_id, "inner")
        if outer_rings:
            outer_coordinates = [
                tuple(node_enu[node_id] for node_id in ring) for ring in outer_rings
            ]
            inner_coordinates = [
                tuple(node_enu[node_id] for node_id in ring) for ring in inner_rings
            ]
            if any(
                _line_or_area_intersects_bounds(
                    coordinates, is_area=True, bounds=bounds
                )
                for coordinates in outer_coordinates
            ):
                # When all crop probes lie solely within a declared inner hole,
                # the boundaries still may not cross.  Exclude only that pure
                # hole case; a boundary touch remains selected.
                centre = (
                    (float(bounds.min_east_m) + float(bounds.max_east_m)) / 2.0,
                    (float(bounds.min_north_m) + float(bounds.max_north_m)) / 2.0,
                )
                if not any(
                    _line_or_area_intersects_bounds(
                        coordinates, is_area=False, bounds=bounds
                    )
                    for coordinates in inner_coordinates
                ) and any(
                    _point_in_polygon(centre, inner) for inner in inner_coordinates
                ):
                    return False
                return True

    active = set() if visiting is None else visiting
    if relation_id in active:
        return False
    active.add(relation_id)
    try:
        for member in relation["members"]:
            member_type = member["type"]
            member_id = member["ref"]
            if member_type == "node" and _point_in_bounds(node_enu[member_id], bounds):
                return True
            if member_type == "way":
                way = dataset.ways[member_id]
                if _line_or_area_intersects_bounds(
                    _way_coordinates(dataset, member_id, node_enu),
                    is_area=_is_area_way(way),
                    bounds=bounds,
                ):
                    return True
            if member_type == "relation" and _relation_intersects_bounds(
                dataset, member_id, node_enu, bounds, active
            ):
                return True
        return False
    finally:
        active.remove(relation_id)


def crop_osm_dataset(
    dataset: OsmDataset,
    *,
    origin: SceneOrigin = SceneOrigin(),
    bounds: EnuBounds = EnuBounds(),
    node_enu: Mapping[int, tuple[float, float]] | None = None,
) -> CroppedOsmDataset:
    """Select an exact ENU crop while retaining complete OSM reference closure.

    Ways are retained if a segment crosses the crop even when neither endpoint is
    inside.  Declared areas are also retained when they enclose the crop.  Parent
    relations and all members they require are retained recursively, so emitted
    OSM JSON/XML never contains dangling references.  Consequently the emitted
    source closure can extend outside the 1 km task area; downstream task-object
    geometry is separately restricted to ways/areas that actually intersect it.
    """

    if node_enu is None:
        node_enu = _enu_coordinates_for_nodes(dataset, origin)
    if set(node_enu) != set(dataset.nodes):
        raise SceneCompilationError(
            "node_enu must define exactly every source node before cropping"
        )

    selected_nodes = {
        node_id
        for node_id, point in node_enu.items()
        if _point_in_bounds(point, bounds)
    }
    selected_ways = {
        way_id
        for way_id, way in dataset.ways.items()
        if _line_or_area_intersects_bounds(
            _way_coordinates(dataset, way_id, node_enu),
            is_area=_is_area_way(way),
            bounds=bounds,
        )
    }
    selected_relations = {
        relation_id
        for relation_id in dataset.relations
        if _relation_intersects_bounds(dataset, relation_id, node_enu, bounds)
    }

    parents: dict[tuple[str, int], set[int]] = defaultdict(set)
    for relation_id, relation in dataset.relations.items():
        for member in relation["members"]:
            parents[(member["type"], member["ref"])].add(relation_id)

    changed = True
    while changed:
        changed = False
        # Include every member needed by selected relations.  Relations may nest;
        # cycles are harmless because the sets are monotonic.
        for relation_id in tuple(selected_relations):
            for member in dataset.relations[relation_id]["members"]:
                member_type = member["type"]
                member_id = member["ref"]
                target = (
                    selected_nodes
                    if member_type == "node"
                    else selected_ways
                    if member_type == "way"
                    else selected_relations
                )
                if member_id not in target:
                    target.add(member_id)
                    changed = True
        # Every selected way requires its complete node sequence.
        for way_id in tuple(selected_ways):
            for node_id in dataset.ways[way_id]["nodes"]:
                if node_id not in selected_nodes:
                    selected_nodes.add(node_id)
                    changed = True
        # A selected member carries its parent relation as source provenance.
        for element_type, element_ids in (
            ("node", selected_nodes),
            ("way", selected_ways),
            ("relation", selected_relations),
        ):
            for element_id in tuple(element_ids):
                for relation_id in parents[(element_type, element_id)]:
                    if relation_id not in selected_relations:
                        selected_relations.add(relation_id)
                        changed = True

    return CroppedOsmDataset(
        dataset=dataset,
        node_ids=frozenset(selected_nodes),
        way_ids=frozenset(selected_ways),
        relation_ids=frozenset(selected_relations),
    )


def _require_shapely() -> tuple[object, object, object, str]:
    """Load the exact GEOS-backed clipping dependency without a fallback.

    Cropping concave polygons safely can yield disjoint components and cannot be
    implemented as a lossy single-ring rectangle clip.  This compiler therefore
    fails explicitly if the pinned Shapely release is unavailable rather than
    joining disconnected pieces or silently retaining out-of-bounds geometry.
    """

    try:
        import shapely
        from shapely.geometry import LineString, Polygon, box
    except ImportError as exc:
        raise SceneCompilationError(
            f"Shapely {REQUIRED_SHAPELY_VERSION} is required for exact scene geometry clipping"
        ) from exc
    if shapely.__version__ != REQUIRED_SHAPELY_VERSION:
        raise SceneCompilationError(
            "scene geometry clipping requires Shapely "
            f"{REQUIRED_SHAPELY_VERSION}, found {shapely.__version__}"
        )
    return Polygon, LineString, box, shapely.__version__


def _shapely_dependency_document() -> dict[str, object]:
    _polygon, _line_string, _box, version = _require_shapely()
    return {
        "package": "Shapely",
        "required_version": REQUIRED_SHAPELY_VERSION,
        "resolved_version": version,
        "backend": "GEOS",
        "purpose": "exact concave polygon, hole, and disconnected-component clipping",
        "fallback": "none; compilation fails if the exact dependency is unavailable",
    }


def _format_number(value: float) -> str:
    if not math.isfinite(value):
        raise SceneCompilationError("cannot serialize a non-finite number")
    return format(value, ".15g")


def _projected_crop_document(origin: SceneOrigin, bounds: EnuBounds) -> dict[str, object]:
    transform = origin.enu_transform()
    corners: list[dict[str, float]] = []
    for east, north in bounds.corners():
        longitude, latitude, ellipsoid_height = transform.enu_to_geodetic(
            Vector3(east, north, 0.0)
        )
        corners.append(
            {
                "east_m": east,
                "north_m": north,
                "longitude_deg": longitude,
                "latitude_deg": latitude,
                "ellipsoid_height_m": ellipsoid_height,
            }
        )
    return {
        "enu_bounds_m": bounds.manifest_document(),
        "geodetic_corners_wgs84": corners,
        "selection_rule": "closed intersection against exact WGS84->ECEF->ENU coordinates",
    }


def _selected_source_document(cropped: CroppedOsmDataset) -> dict[str, object]:
    """Return full selected source closure for audit only, never rendering."""

    document = copy.deepcopy(cropped.dataset.root_metadata)
    document["elements"] = cropped.selected_elements()
    return document


def _set_geodetic_bounds(document: dict[str, object], *, label: str) -> None:
    """Bind OSM's optional extent metadata to the actual emitted node set."""

    raw_elements = document.get("elements")
    if not isinstance(raw_elements, list):
        raise SceneCompilationError(f"{label} must contain an elements array")
    nodes = [
        element
        for element in raw_elements
        if isinstance(element, dict) and element.get("type") == "node"
    ]
    if not nodes:
        raise SceneCompilationError(f"{label} has no nodes for its geodetic bounds")
    latitudes = [_require_finite_number(node.get("lat"), f"{label}.node.lat") for node in nodes]
    longitudes = [_require_finite_number(node.get("lon"), f"{label}.node.lon") for node in nodes]
    document["bounds"] = {
        "minlat": min(latitudes),
        "minlon": min(longitudes),
        "maxlat": max(latitudes),
        "maxlon": max(longitudes),
    }


def _derived_negative_id(*, kind: str, key: object) -> int:
    """Return a deterministic negative ID for a derived crop element."""

    digest = hashlib.sha256(
        canonical_json_bytes(
            {"schema": SCENE_COMPILER_SCHEMA_VERSION, "kind": kind, "key": key}
        )
    ).digest()
    # Keep the magnitude in signed 63-bit OSM-safe space and avoid zero.
    return -(int.from_bytes(digest[:8], "big") & ((1 << 62) - 1) or 1)


def _quantized_coordinate_key(coordinate: tuple[float, float]) -> tuple[str, str]:
    return (_format_number(coordinate[0]), _format_number(coordinate[1]))


def _coordinates_close(
    first: tuple[float, float], second: tuple[float, float]
) -> bool:
    return math.isclose(first[0], second[0], rel_tol=0.0, abs_tol=1e-8) and math.isclose(
        first[1], second[1], rel_tol=0.0, abs_tol=1e-8
    )


def _derived_tags(
    source_tags: Mapping[str, str],
    *,
    source_type: str,
    source_id: int,
    component_index: int,
    geometry_kind: str,
) -> dict[str, str]:
    tags = dict(source_tags)
    tags.update(
        {
            "aero_bench:derived": "geometry_crop",
            "aero_bench:source_type": source_type,
            "aero_bench:source_id": str(source_id),
            "aero_bench:component_index": str(component_index),
            "aero_bench:geometry_kind": geometry_kind,
        }
    )
    return tags


def _enu_to_derived_node(
    *,
    coordinate: tuple[float, float],
    origin: SceneOrigin,
    source_type: str,
    source_id: int,
    component_index: int,
    vertex_index: int,
) -> dict[str, object]:
    transform = origin.enu_transform()
    # OSM nodes carry no ellipsoid height. Every node is later projected at the
    # origin height, so the inverse must land on that same height surface.
    # Inverting with ENU up=0 instead lands on the tangent plane; dropping its
    # returned height moves boundary nodes outside a tight ENU crop.
    up = 0.0
    for _ in range(8):
        longitude, latitude, ellipsoid_height = transform.enu_to_geodetic(
            Vector3(coordinate[0], coordinate[1], up)
        )
        if abs(ellipsoid_height - origin.ellipsoid_height_m) <= 1e-8:
            projected = transform.geodetic_to_enu(
                longitude_deg=longitude,
                latitude_deg=latitude,
                altitude_m=origin.ellipsoid_height_m,
            )
            if (
                abs(projected.x - coordinate[0]) > 1e-7
                or abs(projected.y - coordinate[1]) > 1e-7
            ):
                raise SceneCompilationError("derived OSM node cannot retain its clipped ENU coordinate")
            break
        up += origin.ellipsoid_height_m - ellipsoid_height
    else:
        raise SceneCompilationError("derived OSM node fixed-height inverse did not converge")
    node_id = _derived_negative_id(
        kind="node",
        key={
            "source_type": source_type,
            "source_id": source_id,
            "component_index": component_index,
            "vertex_index": vertex_index,
            "coordinate_enu_m": _quantized_coordinate_key(coordinate),
        },
    )
    return {
        "type": "node",
        "id": node_id,
        "lat": latitude,
        "lon": longitude,
        "tags": {
            "aero_bench:derived": "geometry_crop_boundary",
            "aero_bench:source_type": source_type,
            "aero_bench:source_id": str(source_id),
        },
    }


def _geometry_components(geometry: object) -> list[object]:
    """Return only non-empty Polygon/LineString components in stable order."""

    geometry_type = getattr(geometry, "geom_type", None)
    if getattr(geometry, "is_empty", True):
        return []
    if geometry_type in {"Polygon", "LineString"}:
        components = [geometry]
    elif geometry_type in {"MultiPolygon", "MultiLineString", "GeometryCollection"}:
        components = [
            item
            for item in geometry.geoms
            if not item.is_empty and item.geom_type in {"Polygon", "LineString"}
        ]
    else:
        return []

    def sort_key(item: object) -> tuple[float, float, float, float, str]:
        bounds = item.bounds
        return (
            round(float(bounds[0]), 9),
            round(float(bounds[1]), 9),
            round(float(bounds[2]), 9),
            round(float(bounds[3]), 9),
            item.wkb_hex,
        )

    return sorted(components, key=sort_key)


def _ring_coordinates(geometry: object, label: str) -> tuple[tuple[float, float], ...]:
    coordinates = tuple((float(x), float(y)) for x, y in geometry.exterior.coords)
    _validate_simple_footprint(coordinates, label)
    return coordinates


def _clip_area_components(
    *,
    outer: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]],
    bounds: EnuBounds,
    label: str,
) -> list[tuple[tuple[tuple[float, float], ...], tuple[tuple[tuple[float, float], ...], ...]]]:
    """Clip a potentially concave OSM area into every valid rectangle component."""

    Polygon, _line_string, box, _version = _require_shapely()
    try:
        polygon = Polygon(outer, holes=holes)
    except (TypeError, ValueError) as exc:
        raise SceneCompilationError(f"{label} cannot be made into a polygon: {exc}") from exc
    if not polygon.is_valid:
        raise SceneCompilationError(
            f"{label} is invalid for GEOS clipping; repair/substitution is forbidden"
        )
    clipped = polygon.intersection(
        box(
            float(bounds.min_east_m),
            float(bounds.min_north_m),
            float(bounds.max_east_m),
            float(bounds.max_north_m),
        )
    )
    result: list[
        tuple[tuple[tuple[float, float], ...], tuple[tuple[tuple[float, float], ...], ...]]
    ] = []
    for index, component in enumerate(_geometry_components(clipped)):
        if component.geom_type != "Polygon" or component.area <= 0.0:
            continue
        outer_ring = _ring_coordinates(component, f"{label} clipped outer[{index}]")
        holes_result = tuple(
            _ring_coordinates(
                type("RingPolygon", (), {"exterior": ring})(),
                f"{label} clipped inner[{index}]",
            )
            for ring in component.interiors
        )
        result.append((outer_ring, holes_result))
    return result


def _matching_source_node_id(
    *,
    coordinates: tuple[float, float],
    source_node_ids: Sequence[int],
    node_enu: Mapping[int, tuple[float, float]],
) -> int | None:
    matches = sorted(
        node_id
        for node_id in source_node_ids
        if _coordinates_close(coordinates, node_enu[node_id])
    )
    return matches[0] if matches else None


def _derive_clipped_way(
    *,
    source_type: str,
    source_id: int,
    component_index: int,
    coordinates: Sequence[tuple[float, float]],
    source_node_ids: Sequence[int],
    node_enu: Mapping[int, tuple[float, float]],
    origin: SceneOrigin,
    source_tags: Mapping[str, str],
    geometry_kind: str,
    closed: bool,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Create derived OSM nodes/way while retaining source IDs where possible."""

    node_elements: list[dict[str, object]] = []
    node_ids: list[int] = []
    coordinate_sequence = list(coordinates)
    if closed:
        if not _coordinates_close(coordinate_sequence[0], coordinate_sequence[-1]):
            raise SceneCompilationError("derived polygon component must be closed")
        coordinate_sequence = coordinate_sequence[:-1]
    for vertex_index, coordinate in enumerate(coordinate_sequence):
        source_node_id = _matching_source_node_id(
            coordinates=coordinate,
            source_node_ids=source_node_ids,
            node_enu=node_enu,
        )
        if source_node_id is not None:
            node_ids.append(source_node_id)
            continue
        node = _enu_to_derived_node(
            coordinate=coordinate,
            origin=origin,
            source_type=source_type,
            source_id=source_id,
            component_index=component_index,
            vertex_index=vertex_index,
        )
        node_elements.append(node)
        node_ids.append(int(node["id"]))
    if closed:
        node_ids.append(node_ids[0])
    if len(set(node_ids)) < (3 if closed else 2):
        raise SceneCompilationError("derived geometry component is degenerate after clipping")
    derived_way_id = _derived_negative_id(
        kind="way",
        key={
            "source_type": source_type,
            "source_id": source_id,
            "component_index": component_index,
            "geometry_kind": geometry_kind,
            "coordinates_enu_m": [_quantized_coordinate_key(point) for point in coordinates],
        },
    )
    return node_elements, {
        "type": "way",
        "id": derived_way_id,
        "nodes": node_ids,
        "tags": _derived_tags(
            source_tags,
            source_type=source_type,
            source_id=source_id,
            component_index=component_index,
            geometry_kind=geometry_kind,
        ),
    }


def _is_renderer_relevant_area(tags: Mapping[str, str]) -> bool:
    return _is_building_element({"tags": dict(tags)}) or _is_area_way(
        {"nodes": [1, 2, 3, 1], "tags": dict(tags)}
    )


def _derived_task_osm_document(
    *,
    cropped: CroppedOsmDataset,
    node_enu: Mapping[int, tuple[float, float]],
    origin: SceneOrigin,
    bounds: EnuBounds,
    effective_tags: Mapping[tuple[str, int], Mapping[str, str]],
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    """Build strict render/task OSM geometry entirely inside the declared crop.

    Raw source closure is deliberately not copied here.  Every line and area is
    clipped from original ENU coordinates.  Render-relevant areas retain every
    GEOS-derived polygon component and hole relation.  Generic source relations
    are audit-only; derived relation IDs retain source mapping and only reference
    derived closed members, preventing out-of-crop source members from leaking
    into OSM2World.
    """

    dataset = cropped.dataset
    direct_nodes: dict[int, dict[str, object]] = {}
    derived_nodes: dict[int, dict[str, object]] = {}
    ways: list[dict[str, object]] = []
    relations: list[dict[str, object]] = []
    building_components: list[dict[str, object]] = []
    area_components: list[dict[str, object]] = []

    def add_nodes_and_way(
        *,
        source_type: str,
        source_id: int,
        component_index: int,
        coordinates: Sequence[tuple[float, float]],
        source_node_ids: Sequence[int],
        source_tags: Mapping[str, str],
        geometry_kind: str,
        closed: bool,
    ) -> dict[str, object]:
        node_elements, way = _derive_clipped_way(
            source_type=source_type,
            source_id=source_id,
            component_index=component_index,
            coordinates=coordinates,
            source_node_ids=source_node_ids,
            node_enu=node_enu,
            origin=origin,
            source_tags=source_tags,
            geometry_kind=geometry_kind,
            closed=closed,
        )
        for node in node_elements:
            derived_nodes[int(node["id"])] = node
        for node_id in way["nodes"]:
            if node_id in derived_nodes or node_id in direct_nodes:
                continue
            source_node = dataset.nodes[node_id]
            if not _point_in_bounds(node_enu[node_id], bounds):
                raise SceneCompilationError("out-of-crop source node entered derived task OSM")
            direct_nodes[node_id] = copy.deepcopy(source_node)
        ways.append(way)
        return way

    relation_owned_way_ids: set[int] = set()
    for relation_id in sorted(cropped.relation_ids):
        relation = dataset.relations[relation_id]
        relation_type = _relation_tags_relation_type(relation)
        if relation_type not in {"multipolygon", "building"}:
            continue
        tags = _tags(relation)
        if not _is_renderer_relevant_area(tags):
            continue
        if _is_building_element(relation):
            # Native SDF polyline collision cannot preserve an inner void; do
            # not issue an apparent building with filled holes.
            if _assemble_rings(dataset, relation_id, "inner"):
                raise SceneCompilationError(
                    f"building relation/{relation_id} has inner rings; no hole-filling collision substitute is allowed"
                )
        outer_rings = _assemble_rings(dataset, relation_id, "outer")
        inner_rings = _assemble_rings(dataset, relation_id, "inner")
        if not outer_rings:
            raise SceneCompilationError(
                f"renderer-relevant relation/{relation_id} needs at least one outer ring"
            )
        outer_coordinates = [tuple(node_enu[node_id] for node_id in ring) for ring in outer_rings]
        inner_coordinates = [tuple(node_enu[node_id] for node_id in ring) for ring in inner_rings]
        # GEOS polygon with several outers must be handled independently. An
        # inner hole is assigned only to its containing outer; ambiguous holes
        # are rejected instead of attached to an arbitrary component.
        source_component_index = 0
        relation_members: list[dict[str, object]] = []
        Polygon, _line_string, _box, _version = _require_shapely()
        for outer_index, outer in enumerate(outer_coordinates):
            source_outer = Polygon(outer)
            if not source_outer.is_valid:
                raise SceneCompilationError(
                    f"relation/{relation_id} outer[{outer_index}] is invalid; repair is forbidden"
                )
            contained_holes: list[Sequence[tuple[float, float]]] = []
            contained_hole_ids: list[tuple[int, ...]] = []
            for inner_ids, inner in zip(inner_rings, inner_coordinates):
                inner_polygon = Polygon(inner)
                if source_outer.contains(inner_polygon):
                    contained_holes.append(inner)
                    contained_hole_ids.append(inner_ids)
                elif source_outer.intersects(inner_polygon):
                    raise SceneCompilationError(
                        f"relation/{relation_id} has an inner ring crossing its outer boundary"
                    )
            components = _clip_area_components(
                outer=outer,
                holes=contained_holes,
                bounds=bounds,
                label=f"relation/{relation_id} outer[{outer_index}]",
            )
            for outer_component, hole_components in components:
                outer_way = add_nodes_and_way(
                    source_type="relation",
                    source_id=relation_id,
                    component_index=source_component_index,
                    coordinates=outer_component,
                    source_node_ids=outer_rings[outer_index],
                    source_tags=tags | dict(effective_tags.get(("relation", relation_id), {})),
                    geometry_kind="polygon_outer",
                    closed=True,
                )
                relation_members.append({"type": "way", "ref": outer_way["id"], "role": "outer"})
                component_inner_way_ids: list[int] = []
                for hole_index, hole in enumerate(hole_components):
                    hole_way = add_nodes_and_way(
                        source_type="relation",
                        source_id=relation_id,
                        component_index=source_component_index * 10_000 + hole_index + 1,
                        coordinates=hole,
                        source_node_ids=tuple(
                            node_id for ring in contained_hole_ids for node_id in ring
                        ),
                        source_tags=tags | dict(effective_tags.get(("relation", relation_id), {})),
                        geometry_kind="polygon_inner",
                        closed=True,
                    )
                    relation_members.append({"type": "way", "ref": hole_way["id"], "role": "inner"})
                    component_inner_way_ids.append(int(hole_way["id"]))
                descriptor = {
                    "source_type": "relation",
                    "source_id": relation_id,
                    "component_index": source_component_index,
                    "outer_way_id": outer_way["id"],
                    "inner_way_ids": component_inner_way_ids,
                }
                (building_components if _is_building_element(relation) else area_components).append(descriptor)
                source_component_index += 1
        if relation_members:
            derived_relation_id = _derived_negative_id(
                kind="relation",
                key={"source_type": "relation", "source_id": relation_id, "members": relation_members},
            )
            relations.append(
                {
                    "type": "relation",
                    "id": derived_relation_id,
                    "members": relation_members,
                    "tags": _derived_tags(
                        tags,
                        source_type="relation",
                        source_id=relation_id,
                        component_index=0,
                        geometry_kind="multipolygon",
                    ) | {"type": "multipolygon"},
                }
            )
        relation_owned_way_ids.update(
            member["ref"] for member in relation["members"] if member["type"] == "way"
        )

    for way_id in sorted(cropped.way_ids):
        source_way = dataset.ways[way_id]
        source_tags = dict(_tags(source_way))
        if way_id in relation_owned_way_ids:
            continue
        coordinates = _way_coordinates(dataset, way_id, node_enu)
        if _is_area_way(source_way) and _is_renderer_relevant_area(source_tags):
            components = _clip_area_components(
                outer=coordinates,
                holes=(),
                bounds=bounds,
                label=f"way/{way_id}",
            )
            for component_index, (outer, holes) in enumerate(components):
                if holes:
                    raise SceneCompilationError(
                        f"way/{way_id} unexpectedly produced a clipped hole without source relation semantics"
                    )
                derived_way = add_nodes_and_way(
                    source_type="way",
                    source_id=way_id,
                    component_index=component_index,
                    coordinates=outer,
                    source_node_ids=source_way["nodes"],
                    source_tags=source_tags | dict(effective_tags.get(("way", way_id), {})),
                    geometry_kind="polygon",
                    closed=True,
                )
                descriptor = {
                    "source_type": "way",
                    "source_id": way_id,
                    "component_index": component_index,
                    "outer_way_id": derived_way["id"],
                    "inner_way_ids": [],
                }
                (building_components if _is_building_element(source_way) else area_components).append(descriptor)
            continue
        clipped_components = _clip_polyline_components_to_bounds(coordinates, bounds)
        for component_index, clipped_line in enumerate(clipped_components):
            if len(clipped_line) < 2:
                continue
            add_nodes_and_way(
                source_type="way",
                source_id=way_id,
                component_index=component_index,
                coordinates=clipped_line,
                source_node_ids=source_way["nodes"],
                source_tags=source_tags,
                geometry_kind="polyline",
                closed=False,
            )

    element_ids = [
        *(int(node["id"]) for node in direct_nodes.values()),
        *(int(node["id"]) for node in derived_nodes.values()),
        *(int(way["id"]) for way in ways),
        *(int(relation["id"]) for relation in relations),
    ]
    if len(element_ids) != len(set(element_ids)):
        raise SceneCompilationError("deterministic derived OSM identifiers collided")
    document = {
        **copy.deepcopy(dataset.root_metadata),
        "generator": "AERO-BENCH deterministic ENU geometry crop",
        "elements": [
            *sorted(direct_nodes.values(), key=lambda item: int(item["id"])),
            *sorted(derived_nodes.values(), key=lambda item: int(item["id"])),
            *sorted(ways, key=lambda item: int(item["id"])),
            *sorted(relations, key=lambda item: int(item["id"])),
        ],
    }
    return document, building_components, area_components


def _xml_attribute_value(value: object, label: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return _format_number(value)
    raise SceneCompilationError(f"{label} cannot be represented as an OSM XML attribute")


def _xml_element_attributes(element: Mapping[str, object], kind: str) -> str:
    required = ("id", "lat", "lon") if kind == "node" else ("id",)
    attributes: list[tuple[str, object]] = [
        (name, element[name]) for name in required
    ]
    for name in _OSM_METADATA_ATTRIBUTES:
        if name in element:
            attributes.append((name, element[name]))
    return "".join(
        f" {name}={quoteattr(_xml_attribute_value(value, f'{kind}.{name}'))}"
        for name, value in attributes
    )


def osm_xml_bytes(document: Mapping[str, object]) -> bytes:
    """Serialize strict OSM JSON as deterministic OSM 0.6 XML for netconvert."""

    metadata = {key: value for key, value in document.items() if key != "elements"}
    version = metadata.get("version", 0.6)
    root_attrs = f" version={quoteattr(_xml_attribute_value(version, 'OSM.version'))}"
    generator = metadata.get("generator")
    if generator is not None:
        root_attrs += f" generator={quoteattr(_xml_attribute_value(generator, 'OSM.generator'))}"
    lines = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", f"<osm{root_attrs}>"]

    raw_bounds = metadata.get("bounds")
    if isinstance(raw_bounds, dict):
        recognized_bounds = ["minlat", "minlon", "maxlat", "maxlon"]
        if all(key in raw_bounds for key in recognized_bounds):
            attributes = "".join(
                f" {key}={quoteattr(_xml_attribute_value(raw_bounds[key], f'OSM.bounds.{key}'))}"
                for key in recognized_bounds
            )
            lines.append(f"  <bounds{attributes}/>")

    raw_elements = document.get("elements")
    if not isinstance(raw_elements, list):
        raise SceneCompilationError("OSM XML input document must have an elements array")
    grouped: dict[str, list[dict[str, object]]] = {
        "node": [],
        "way": [],
        "relation": [],
    }
    for raw_element in raw_elements:
        element = _require_json_object(raw_element, "OSM XML element")
        element_type = _require_string(element.get("type"), "OSM XML element.type")
        if element_type not in grouped:
            raise SceneCompilationError("OSM XML element.type is unsupported")
        grouped[element_type].append(element)

    for kind in ("node", "way", "relation"):
        for element in sorted(grouped[kind], key=lambda item: int(item["id"])):
            attributes = _xml_element_attributes(element, kind)
            children: list[str] = []
            if kind == "way":
                children.extend(
                    f"    <nd ref={quoteattr(str(node_id))}/>"
                    for node_id in element["nodes"]
                )
            elif kind == "relation":
                for member in element["members"]:
                    child_attributes = (
                        f" type={quoteattr(str(member['type']))}"
                        f" ref={quoteattr(str(member['ref']))}"
                        f" role={quoteattr(str(member['role']))}"
                    )
                    children.append(f"    <member{child_attributes}/>")
            tags = _tags(element)
            children.extend(
                f"    <tag k={quoteattr(key)} v={quoteattr(value)}/>"
                for key, value in sorted(tags.items())
            )
            if not children:
                lines.append(f"  <{kind}{attributes}/>")
            else:
                lines.append(f"  <{kind}{attributes}>")
                lines.extend(children)
                lines.append(f"  </{kind}>")
    lines.append("</osm>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _read_length_m(value: str, label: str) -> float:
    match = _LENGTH_RE.fullmatch(value)
    if match is None:
        raise SceneCompilationError(
            f"{label} must be a positive metre value; unsupported units are not assumed"
        )
    parsed = float(match.group("value"))
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise SceneCompilationError(f"{label} must be positive")
    return parsed


def _read_optional_length_m(tags: Mapping[str, str], key: str, label: str) -> float | None:
    value = tags.get(key)
    return None if value is None else _read_length_m(value, f"{label} tag {key!r}")


def _read_level_count(value: str, label: str) -> float:
    match = _LEVEL_RE.fullmatch(value)
    if match is None:
        raise SceneCompilationError(f"{label} must be a positive numeric level count")
    parsed = float(match.group("value"))
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise SceneCompilationError(f"{label} must be a positive numeric level count")
    return parsed


def _effective_vertical_geometry(
    *,
    tags: Mapping[str, str],
    label: str,
    default_height_m: float,
    level_height_m: float,
) -> tuple[float, float, str, list[str]]:
    """Return base, top, source kind, and named assumptions for a building."""

    base = _read_optional_length_m(tags, "min_height", label) or 0.0
    if "min_level" in tags:
        min_level = _read_level_count(tags["min_level"], f"{label} tag 'min_level'")
        if "min_height" in tags:
            raise SceneCompilationError(
                f"{label} declares both min_height and min_level; their vertical datum is ambiguous"
            )
        base = min_level * level_height_m
    explicit_height = _read_optional_length_m(tags, "height", label)
    assumptions: list[str] = []
    if explicit_height is not None:
        top = explicit_height
        source_kind = "osm_height_tag"
    elif "building:levels" in tags:
        levels = _read_level_count(tags["building:levels"], f"{label} tag 'building:levels'")
        top = base + levels * level_height_m
        source_kind = "derived_from_osm_building_levels"
        assumptions.append("building_level_height_m")
    else:
        top = base + default_height_m
        source_kind = "assumed_missing_osm_height"
        assumptions.append("missing_building_height_m")
    if top <= base:
        raise SceneCompilationError(
            f"{label} effective top height must exceed its effective base height"
        )
    return base, top, source_kind, assumptions


def _closed_footprint_node_ids(way: Mapping[str, object], label: str) -> tuple[int, ...]:
    node_ids = tuple(way["nodes"])
    if len(node_ids) < 4 or node_ids[0] != node_ids[-1]:
        raise SceneCompilationError(f"{label} must be a closed OSM way to form a building footprint")
    if len(set(node_ids[:-1])) < 3:
        raise SceneCompilationError(f"{label} footprint must contain at least three distinct vertices")
    return node_ids


def _orientation(first: tuple[float, float], second: tuple[float, float], third: tuple[float, float]) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def _segments_intersect(
    first_a: tuple[float, float],
    first_b: tuple[float, float],
    second_a: tuple[float, float],
    second_b: tuple[float, float],
) -> bool:
    def sign(value: float) -> int:
        return 1 if value > 1e-9 else -1 if value < -1e-9 else 0

    one = sign(_orientation(first_a, first_b, second_a))
    two = sign(_orientation(first_a, first_b, second_b))
    three = sign(_orientation(second_a, second_b, first_a))
    four = sign(_orientation(second_a, second_b, first_b))
    if one * two < 0 and three * four < 0:
        return True
    return (
        (one == 0 and _point_on_segment(second_a, first_a, first_b))
        or (two == 0 and _point_on_segment(second_b, first_a, first_b))
        or (three == 0 and _point_on_segment(first_a, second_a, second_b))
        or (four == 0 and _point_on_segment(first_b, second_a, second_b))
    )


def _validate_simple_footprint(
    coordinates: Sequence[tuple[float, float]], label: str
) -> None:
    if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
        raise SceneCompilationError(f"{label} must be a closed footprint")
    if len(set(coordinates[:-1])) < 3:
        raise SceneCompilationError(f"{label} must have three distinct vertices")
    edge_count = len(coordinates) - 1
    for first_index in range(edge_count):
        first_a = coordinates[first_index]
        first_b = coordinates[first_index + 1]
        for second_index in range(first_index + 1, edge_count):
            # Adjacent sides meet at their expected shared endpoint.
            if (
                second_index == first_index + 1
                or (first_index == 0 and second_index == edge_count - 1)
            ):
                continue
            if _segments_intersect(
                first_a,
                first_b,
                coordinates[second_index],
                coordinates[second_index + 1],
            ):
                raise SceneCompilationError(
                    f"{label} has self-intersecting sides; no collision substitute is emitted"
                )


def _relation_building_footprints(
    dataset: OsmDataset,
    relation_id: int,
) -> tuple[tuple[int, ...], ...]:
    outer_rings = _assemble_rings(dataset, relation_id, "outer")
    inner_rings = _assemble_rings(dataset, relation_id, "inner")
    if not outer_rings:
        raise SceneCompilationError(
            f"building relation/{relation_id} requires at least one outer ring"
        )
    if inner_rings:
        raise SceneCompilationError(
            f"building relation/{relation_id} has inner rings; Gazebo native polyline "
            "collision has no hole semantics, so compilation refuses a filled substitute"
        )
    return tuple(outer_rings)


def _enu_coordinates_for_nodes(
    dataset: OsmDataset, origin: SceneOrigin
) -> dict[int, tuple[float, float]]:
    transform = origin.enu_transform()
    result: dict[int, tuple[float, float]] = {}
    for node_id, node in dataset.nodes.items():
        enu = transform.geodetic_to_enu(
            longitude_deg=float(node["lon"]),
            latitude_deg=float(node["lat"]),
            altitude_m=float(origin.ellipsoid_height_m),
        )
        result[node_id] = (enu.x, enu.y)
    return result


def _geometry_digest(
    *,
    source_type: str,
    source_id: int,
    coordinates: Sequence[tuple[float, float]],
    base_m: float,
    top_m: float,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "source_type": source_type,
                "source_id": source_id,
                "footprint_enu_m": [[east, north] for east, north in coordinates],
                "base_enu_up_m": base_m,
                "top_enu_up_m": top_m,
            }
        )
    ).hexdigest()


def _building_objects(
    cropped: CroppedOsmDataset,
    *,
    node_enu: Mapping[int, tuple[float, float]],
    bounds: EnuBounds,
    default_height_m: float,
    level_height_m: float,
) -> tuple[list[dict[str, object]], dict[tuple[str, int], dict[str, str]]]:
    dataset = cropped.dataset
    candidates: list[tuple[str, int, tuple[int, ...], Mapping[str, str]]] = []
    building_relation_member_way_ids: set[int] = set()
    for relation_id in sorted(cropped.relation_ids):
        relation = dataset.relations[relation_id]
        if not _is_building_element(relation):
            continue
        for footprint_ids in _relation_building_footprints(dataset, relation_id):
            candidates.append(("relation", relation_id, footprint_ids, _tags(relation)))
        # A tagged building relation owns its member ways.  A generic
        # multipolygon (park, water, pedestrian plaza) must not suppress a
        # separately tagged building way merely because it is part of another
        # land-use relation.
        building_relation_member_way_ids.update(
            member["ref"]
            for member in relation["members"]
            if member["type"] == "way"
        )
    for way_id in sorted(cropped.way_ids):
        way = dataset.ways[way_id]
        if not _is_building_element(way):
            continue
        if way_id in building_relation_member_way_ids:
            continue
        candidates.append(("way", way_id, _closed_footprint_node_ids(way, f"building way/{way_id}"), _tags(way)))

    objects: list[dict[str, object]] = []
    effective_tags: dict[tuple[str, int], dict[str, str]] = {}
    component_counts: dict[tuple[str, int], int] = {}
    for source_type, source_id, footprint_ids, tags in candidates:
        label = f"building {source_type}/{source_id}"
        coordinates = tuple(node_enu[node_id] for node_id in footprint_ids)
        _validate_simple_footprint(coordinates, label)
        base, top, source_kind, assumptions = _effective_vertical_geometry(
            tags=tags,
            label=label,
            default_height_m=default_height_m,
            level_height_m=level_height_m,
        )
        components = _clip_area_components(
            outer=coordinates,
            holes=(),
            bounds=bounds,
            label=label,
        )
        for component, holes in components:
            if holes:
                raise SceneCompilationError(
                    f"{label} clipping produced holes but Gazebo polyline collision cannot represent them"
                )
            source_key = (source_type, source_id)
            component_index = component_counts.get(source_key, 0)
            component_counts[source_key] = component_index + 1
            digest = _geometry_digest(
                source_type=source_type,
                source_id=source_id,
                coordinates=component,
                base_m=base,
                top_m=top,
            )
            object_id = f"building.{source_type}.{source_id}.component.{component_index}"
            objects.append(
                {
                    "object_id": object_id,
                    "kind": "building",
                    "osm": {"type": source_type, "id": source_id},
                    "component_index": component_index,
                    "geometry_crop": "exact_enu_rectangle",
                    "member_way_ids": (
                        [member["ref"] for member in dataset.relations[source_id]["members"] if member["type"] == "way"]
                        if source_type == "relation"
                        else [source_id]
                    ),
                    "footprint_enu_m": [[east, north] for east, north in component],
                    "source_footprint_vertex_node_ids": list(footprint_ids),
                    "base_enu_up_m": base,
                    "top_enu_up_m": top,
                    "extrusion_height_m": top - base,
                    "height_source": source_kind,
                    "simulation_assumption_ids": assumptions,
                    "measurement_status": "OSM tag provenance only; not independently measured",
                    "effective_geometry_sha256": digest,
                }
            )
            effective_tags[(source_type, source_id)] = {
                "height": _format_number(top),
                "aero_bench:effective_height_m": _format_number(top),
                "aero_bench:effective_base_height_m": _format_number(base),
                "aero_bench:height_source": source_kind,
            }
            if base > 0.0:
                effective_tags[(source_type, source_id)]["min_height"] = _format_number(base)
    return objects, effective_tags


def _road_objects(
    cropped: CroppedOsmDataset,
    *,
    node_enu: Mapping[int, tuple[float, float]],
    bounds: EnuBounds,
) -> list[dict[str, object]]:
    dataset = cropped.dataset
    roads: list[dict[str, object]] = []
    for way_id in sorted(cropped.way_ids):
        way = dataset.ways[way_id]
        tags = _tags(way)
        highway = tags.get("highway")
        if highway is None or highway.strip().lower() in {"", "no"}:
            continue
        coordinates = _way_coordinates(dataset, way_id, node_enu)
        if len(coordinates) < 2 or len(set(coordinates)) < 2:
            raise SceneCompilationError(
                f"road way/{way_id} must have at least two distinct geometry points"
            )
        # A relation closure may pull in long, remote supporting roads. Retain
        # every GEOS-created component; do not connect disjoint fragments.
        clipped_components = _clip_polyline_components_to_bounds(coordinates, bounds)
        for component_index, clipped_coordinates in enumerate(clipped_components):
            roads.append(
                {
                    "object_id": f"road.way.{way_id}.component.{component_index}",
                    "kind": "road",
                    "osm": {"type": "way", "id": way_id},
                    "component_index": component_index,
                    "geometry_crop": "exact_enu_rectangle",
                    "highway": highway,
                    "name": tags.get("name"),
                    "oneway": tags.get("oneway"),
                    "lanes": tags.get("lanes"),
                    "width_osm_tag": tags.get("width"),
                    "centerline_enu_m": [
                        [east, north] for east, north in clipped_coordinates
                    ],
                    "source_centerline_enu_m": [
                        [east, north] for east, north in coordinates
                    ],
                    "node_ids": list(way["nodes"]),
                }
            )
    return roads


def _sdf_name(object_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", object_id)


def gazebo_sdf_bytes(
    *, origin: SceneOrigin, building_objects: Sequence[Mapping[str, object]]
) -> bytes:
    """Create static/collision Gazebo SDF using each exact source footprint.

    SDF's native ``polyline`` primitive extrudes the source ring directly.  This
    avoids a lossy, custom viewer mesh and avoids an unpinned triangulation
    dependency.  Hole-bearing building relations are rejected before this point
    because a native polyline has no hole semantics.
    """

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sdf version="1.10">',
        '  <world name="urban_scene">',
        '    <spherical_coordinates>',
        '      <surface_model>EARTH_WGS84</surface_model>',
        '      <world_frame_orientation>ENU</world_frame_orientation>',
        f"      <latitude_deg>{_format_number(float(origin.latitude_deg))}</latitude_deg>",
        f"      <longitude_deg>{_format_number(float(origin.longitude_deg))}</longitude_deg>",
        f"      <elevation>{_format_number(float(origin.amsl_m))}</elevation>",
        '      <heading_deg>0</heading_deg>',
        '    </spherical_coordinates>',
    ]
    for item in building_objects:
        name = _sdf_name(str(item["object_id"]))
        base = float(item["base_enu_up_m"])
        height = float(item["extrusion_height_m"])
        points = item["footprint_enu_m"]
        lines.extend(
            (
                f'    <model name="{name}">',
                '      <static>true</static>',
                f"      <pose>0 0 {_format_number(base)} 0 0 0</pose>",
                '      <link name="source_footprint">',
                '        <collision name="source_footprint_collision">',
                '          <geometry><polyline>',
                f"            <height>{_format_number(height)}</height>",
            )
        )
        lines.extend(
            f"            <point>{_format_number(float(east))} {_format_number(float(north))}</point>"
            for east, north in points[:-1]
        )
        lines.extend(
            (
                '          </polyline></geometry>',
                '        </collision>',
                '        <visual name="source_footprint_visual">',
                '          <geometry><polyline>',
                f"            <height>{_format_number(height)}</height>",
            )
        )
        lines.extend(
            f"            <point>{_format_number(float(east))} {_format_number(float(north))}</point>"
            for east, north in points[:-1]
        )
        lines.extend(
            (
                '          </polyline></geometry>',
                '        </visual>',
                '      </link>',
                '    </model>',
            )
        )
    lines.extend(('  </world>', '</sdf>'))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _sumo_projection_document(origin: SceneOrigin) -> dict[str, object]:
    projection = (
        f"+proj=aeqd +lat_0={_format_number(float(origin.latitude_deg))} "
        f"+lon_0={_format_number(float(origin.longitude_deg))} +datum=WGS84 "
        "+units=m +no_defs"
    )
    return {
        "projection": projection,
        "normalization": "disabled",
        "sumo_to_enu": {
            "kind": "projection_chain",
            "steps": [
                "SUMO x/y -> inverse declared azimuthal-equidistant projection -> WGS84 longitude/latitude",
                "WGS84 longitude/latitude at declared ellipsoid height -> exact EnuTransform",
            ],
            "identity_claim": False,
            "reason": "SUMO's PROJ plane is explicitly converted; it is not silently treated as ECEF-derived ENU.",
        },
    }


def _portable_netconvert_command(origin: SceneOrigin) -> list[str]:
    projection = _sumo_projection_document(origin)["projection"]
    return [
        "netconvert",
        "--osm-files",
        "osm/sumo-network.osm",
        "--output-file",
        "sumo/network.net.xml",
        "--proj",
        str(projection),
        "--offset.disable-normalization",
    ]


def _validate_netconvert(executable: str) -> tuple[str, str]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise SceneCompilationError(
            f"requested netconvert executable {executable!r} is not available on PATH"
        )
    try:
        completed = subprocess.run(
            [resolved, "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SceneCompilationError(f"cannot validate netconvert executable {resolved}: {exc}") from exc
    version_output = (completed.stdout + completed.stderr).strip()
    match = re.search(r"Eclipse SUMO netconvert Version ([0-9]+(?:\.[0-9]+){1,2})", version_output)
    if match is None:
        raise SceneCompilationError(
            f"{resolved} did not identify itself as Eclipse SUMO netconvert: {version_output!r}"
        )
    version = match.group(1)
    if version != REQUIRED_NETCONVERT_VERSION:
        raise SceneCompilationError(
            "formal SUMO network generation requires netconvert "
            f"{REQUIRED_NETCONVERT_VERSION}, found {version} at {resolved}; "
            "use the declared digest-pinned SUMO OCI workload"
        )
    return resolved, version


def _netconvert_environment() -> tuple[dict[str, str], str | None]:
    """Return the isolated local SUMO data environment used for conversion."""

    environment = dict(os.environ)
    configured_home = environment.get("SUMO_HOME")
    if configured_home:
        return environment, configured_home
    packaged_home = Path("/usr/share/sumo")
    if packaged_home.is_dir():
        environment["SUMO_HOME"] = str(packaged_home)
        return environment, str(packaged_home)
    return environment, None


def _run_netconvert(
    *, staging_dir: Path, origin: SceneOrigin, executable: str
) -> dict[str, object]:
    resolved, version = _validate_netconvert(executable)
    source = staging_dir / "osm" / "sumo-network.osm"
    output = staging_dir / "sumo" / "network.net.xml"
    output.parent.mkdir(parents=True, exist_ok=True)
    projection = str(_sumo_projection_document(origin)["projection"])
    command = [
        resolved,
        "--osm-files",
        str(source),
        "--output-file",
        str(output),
        "--proj",
        projection,
        "--offset.disable-normalization",
    ]
    # Debian/Ubuntu's packaged netconvert needs this local data root to resolve
    # the bundled OSM type map.  The process-only environment makes no host-wide
    # change and never chooses a remote data location.
    environment, sumo_home = _netconvert_environment()
    try:
        completed = subprocess.run(
            command,
            cwd=staging_dir,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SceneCompilationError(f"netconvert execution failed: {exc}") from exc
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SceneCompilationError(
            f"netconvert {version} failed with status {completed.returncode}: {detail}"
        )
    return {
        "status": "generated",
        "validated_executable": resolved,
        "validated_version": version,
        "output": "sumo/network.net.xml",
        "output_sha256": _sha256_file(output),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "process_local_sumo_home": sumo_home,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bytes(root: Path, relative_path: str, contents: bytes) -> Path:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contents)
    return destination


def _write_json(root: Path, relative_path: str, document: object) -> Path:
    return _write_bytes(root, relative_path, canonical_json_bytes(document) + b"\n")


def _auto_provenance_path(source_path: Path) -> Path | None:
    suffix = ".osm.json"
    if source_path.name.endswith(suffix):
        candidate = source_path.with_name(
            source_path.name[: -len(suffix)] + ".provenance.json"
        )
        return candidate if candidate.is_file() else None
    return None


def _copy_and_verify_provenance(
    *,
    staging_dir: Path,
    source_path: Path,
    requested_path: str | Path | None,
    source_sha256: str,
) -> dict[str, object] | None:
    path = (
        Path(requested_path)
        if requested_path is not None
        else _auto_provenance_path(source_path)
    )
    if path is None:
        return None
    try:
        provenance_bytes = path.read_bytes()
        provenance = json.loads(provenance_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneCompilationError(f"cannot read source provenance {path}: {exc}") from exc
    provenance_object = _require_json_object(provenance, "source provenance")
    declared_digest = provenance_object.get("sha256")
    if declared_digest is not None:
        if not isinstance(declared_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", declared_digest
        ):
            raise SceneCompilationError("source provenance sha256 must be a lowercase SHA-256")
        if declared_digest != source_sha256:
            raise SceneCompilationError(
                "source provenance sha256 does not pin the supplied OSM source bytes"
            )
    output_path = _write_bytes(
        staging_dir, "provenance/source-provenance.json", provenance_bytes
    )
    return {
        "source_provenance_file": output_path.relative_to(staging_dir).as_posix(),
        "source_provenance_sha256": _sha256_file(output_path),
        "declared_source_sha256": declared_digest,
    }


def _output_file_digests(root: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json":
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256_file(path),
                    "byte_size": path.stat().st_size,
                }
            )
    return records


def compile_urban_scene(
    *,
    source_path: str | Path,
    output_dir: str | Path,
    source_provenance_path: str | Path | None = None,
    origin: SceneOrigin = SceneOrigin(),
    bounds: EnuBounds = EnuBounds(),
    default_building_height_m: float = DEFAULT_BUILDING_HEIGHT_M,
    building_level_height_m: float = DEFAULT_BUILDING_LEVEL_HEIGHT_M,
    run_netconvert: bool = False,
    netconvert_executable: str = "netconvert",
) -> SceneCompilationResult:
    """Compile one deterministic shared 1 km OSM scene into a new directory.

    ``output_dir`` must not exist. The function stages all output beside that
    path and atomically publishes it only after JSON, XML, common geometry, SDF,
    and the pinned manifest have all been written. Raw full-reference closure is
    published only under ``audit/``; effective renderer/Gazebo geometry and the
    SUMO OSM input are independently clipped to the exact ENU task rectangle.
    Enabling ``run_netconvert`` accepts only version 1.27.1 from the declared
    digest-pinned SUMO OCI workload. No Docker, host configuration, download, or
    global environment mutation is performed by this compiler.
    """

    source = Path(source_path).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise SceneCompilationError(
            f"scene output directory already exists and is never overwritten: {output}"
        )
    if isinstance(default_building_height_m, bool) or not isinstance(
        default_building_height_m, (int, float)
    ) or not math.isfinite(float(default_building_height_m)) or float(default_building_height_m) <= 0.0:
        raise SceneCompilationError("default_building_height_m must be a positive finite number")
    if isinstance(building_level_height_m, bool) or not isinstance(
        building_level_height_m, (int, float)
    ) or not math.isfinite(float(building_level_height_m)) or float(building_level_height_m) <= 0.0:
        raise SceneCompilationError("building_level_height_m must be a positive finite number")
    if not isinstance(run_netconvert, bool):
        raise SceneCompilationError("run_netconvert must be a boolean")
    if not isinstance(netconvert_executable, str) or not netconvert_executable:
        raise SceneCompilationError("netconvert_executable must be a non-empty string")

    dataset = load_osm_json(source)
    node_enu = _enu_coordinates_for_nodes(dataset, origin)
    cropped = crop_osm_dataset(
        dataset, origin=origin, bounds=bounds, node_enu=node_enu
    )
    building_objects, effective_tags = _building_objects(
        cropped,
        node_enu=node_enu,
        bounds=bounds,
        default_height_m=float(default_building_height_m),
        level_height_m=float(building_level_height_m),
    )
    road_objects = _road_objects(cropped, node_enu=node_enu, bounds=bounds)
    raw_document = _selected_source_document(cropped)
    effective_document, derived_building_components, derived_area_components = (
        _derived_task_osm_document(
            cropped=cropped,
            node_enu=node_enu,
            origin=origin,
            bounds=bounds,
            effective_tags=effective_tags,
        )
    )
    _set_geodetic_bounds(effective_document, label="effective OSM document")
    cropped_json = canonical_json_bytes(raw_document) + b"\n"

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    published = False
    try:
        provenance = _copy_and_verify_provenance(
            staging_dir=staging_dir,
            source_path=source,
            requested_path=source_provenance_path,
            source_sha256=dataset.source_sha256,
        )
        # Raw full closure is audit evidence only. It is intentionally not an
        # input to any renderer, Gazebo geometry, or SUMO conversion.
        _write_bytes(staging_dir, "audit/raw-closure.osm.json", cropped_json)
        _write_bytes(staging_dir, "audit/raw-closure.osm", osm_xml_bytes(raw_document))
        _write_json(staging_dir, "osm/effective.osm.json", effective_document)
        _write_bytes(
            staging_dir, "osm/effective.osm", osm_xml_bytes(effective_document)
        )
        sumo_network_document = {
            **copy.deepcopy(effective_document),
            "generator": "AERO-BENCH deterministic ENU geometry crop for SUMO",
            "elements": [
                element
                for element in effective_document["elements"]
                if element["type"] != "relation"
                and (
                    element["type"] == "node"
                    or element["tags"].get("highway") is not None
                )
            ],
        }
        # OSM XML requires only nodes referenced by its selected road ways.
        sumo_node_ids = {
            node_id
            for element in sumo_network_document["elements"]
            if element["type"] == "way"
            for node_id in element["nodes"]
        }
        sumo_network_document["elements"] = [
            element
            for element in sumo_network_document["elements"]
            if element["type"] == "way"
            or (element["type"] == "node" and element["id"] in sumo_node_ids)
        ]
        _write_json(staging_dir, "osm/sumo-network.osm.json", sumo_network_document)
        _write_bytes(
            staging_dir, "osm/sumo-network.osm", osm_xml_bytes(sumo_network_document)
        )
        _write_bytes(
            staging_dir,
            "gazebo/scene.sdf",
            gazebo_sdf_bytes(origin=origin, building_objects=building_objects),
        )

        simulation_assumptions = {
            "schema_version": "aero-bench.urban-scene-simulation-assumptions/v1",
            "scope": "static OSM building vertical geometry only",
            "not_measured_claim": "Neither OSM height tags nor compiler-derived heights are independently measured terrain/building surveys.",
            "assumptions": [
                {
                    "assumption_id": "missing_building_height_m",
                    "value_m": float(default_building_height_m),
                    "applies_when": "selected building has neither usable height nor building:levels",
                    "effective_osm_application": "height and aero_bench:effective_height_m tags",
                    "gazebo_application": "the identical top/base ENU extrusion in gazebo/scene.sdf",
                },
                {
                    "assumption_id": "building_level_height_m",
                    "value_m": float(building_level_height_m),
                    "applies_when": "selected building has building:levels but no usable height",
                    "effective_osm_application": "derived height and aero_bench:effective_height_m tags",
                    "gazebo_application": "the identical top/base ENU extrusion in gazebo/scene.sdf",
                },
            ],
            "effective_buildings": [
                {
                    "object_id": item["object_id"],
                    "osm": item["osm"],
                    "height_source": item["height_source"],
                    "simulation_assumption_ids": item["simulation_assumption_ids"],
                    "base_enu_up_m": item["base_enu_up_m"],
                    "top_enu_up_m": item["top_enu_up_m"],
                    "effective_geometry_sha256": item["effective_geometry_sha256"],
                }
                for item in building_objects
            ],
        }
        _write_json(
            staging_dir,
            "metadata/simulation-assumptions.json",
            simulation_assumptions,
        )

        object_document = {
            "schema_version": "aero-bench.urban-scene-objects/v1",
            "coordinate_frame": "ENU",
            "origin": origin.manifest_document(),
            "buildings": building_objects,
            "roads": road_objects,
            "osm_to_object_id": {
                **{
                    f"{item['osm']['type']}/{item['osm']['id']}": item["object_id"]
                    for item in building_objects
                },
                **{
                    f"way/{item['osm']['id']}": item["object_id"]
                    for item in road_objects
                },
            },
        }
        _write_json(staging_dir, "metadata/objects.json", object_document)
        common_scene = {
            "schema_version": "aero-bench.urban-scene-common/v1",
            "coordinate_frame": "ENU",
            "origin": origin.manifest_document(),
            "crop": _projected_crop_document(origin, bounds),
            "geometry_clipping_dependency": _shapely_dependency_document(),
            "source": {
                "sha256": dataset.source_sha256,
                "raw_closure_audit_osm_json": "audit/raw-closure.osm.json",
                "raw_closure_audit_osm_xml": "audit/raw-closure.osm",
                "effective_render_task_osm_json": "osm/effective.osm.json",
                "effective_render_task_osm_xml": "osm/effective.osm",
                "sumo_network_osm_json": "osm/sumo-network.osm.json",
                "sumo_network_osm_xml": "osm/sumo-network.osm",
                "raw_closure_runtime_use_forbidden": True,
            },
            "gazebo": {
                "scene_sdf": "gazebo/scene.sdf",
                "geometry_kind": "native_sdf_polyline_extrusions",
                "building_objects": building_objects,
            },
            "sumo": {
                **_sumo_projection_document(origin),
                "network_generation_input": "osm/sumo-network.osm",
                "geometry_crop_required": True,
                "raw_closure_input_forbidden": True,
                "reason": "Netconvert receives only the derived, geometry-clipped road OSM; audit raw closure is never a SUMO input.",
            },
            "ns3": {
                "obstruction_buildings": building_objects,
                "source_frame": "ENU",
                "statement": "Use the same footprints/base/top geometry; ns-3 propagation modeling is downstream.",
            },
            "public_builder": {
                "objects": object_document,
                "renderer_input": "osm/effective.osm.json",
                "renderer_geometry_crop_enforced": True,
                "raw_closure_renderer_input_forbidden": True,
                "statement": "Use the official OSM2World renderer only from geometry-clipped osm/effective.osm.json; this compiler emits no custom viewer mesh or raster.",
            },
            "roads": road_objects,
            "derived_geometry": {
                "building_components": derived_building_components,
                "area_components": derived_area_components,
            },
        }
        _write_json(staging_dir, "metadata/common-scene.json", common_scene)

        netconvert_command = {
            "schema_version": "aero-bench.urban-scene-netconvert/v1",
            "status": "not_invoked",
            "cwd": "scene output root",
            "command": _portable_netconvert_command(origin),
            "input": "osm/sumo-network.osm",
            "output": "sumo/network.net.xml",
            "projection": _sumo_projection_document(origin),
            "required_netconvert_version": REQUIRED_NETCONVERT_VERSION,
            "execution_policy": "The compiler only generates a formal SUMO network when --netconvert validates and invokes exactly netconvert 1.27.1 from the declared digest-pinned SUMO OCI workload. Host versions are never accepted as substitutes; packaged SUMO_HOME, when needed, is process-local and never changes host configuration.",
        }
        if run_netconvert:
            result = _run_netconvert(
                staging_dir=staging_dir,
                origin=origin,
                executable=netconvert_executable,
            )
            netconvert_command.update(result)
        _write_json(staging_dir, "sumo/netconvert-command.json", netconvert_command)

        selected_geometry_nodes = {
            node_id
            for way_id in cropped.way_ids
            for node_id in dataset.ways[way_id]["nodes"]
        }
        closure_coordinates = [
            node_enu[node_id] for node_id in sorted(selected_geometry_nodes)
        ]
        selected_bounds = (
            {
                "min_east_m": min(point[0] for point in closure_coordinates),
                "max_east_m": max(point[0] for point in closure_coordinates),
                "min_north_m": min(point[1] for point in closure_coordinates),
                "max_north_m": max(point[1] for point in closure_coordinates),
            }
            if closure_coordinates
            else None
        )
        def projected_document_bounds(document: Mapping[str, object]) -> dict[str, float] | None:
            transform = origin.enu_transform()
            coordinates = []
            for element in document["elements"]:
                if element["type"] != "node":
                    continue
                point = transform.geodetic_to_enu(
                    longitude_deg=float(element["lon"]),
                    latitude_deg=float(element["lat"]),
                    altitude_m=float(origin.ellipsoid_height_m),
                )
                coordinates.append((point.x, point.y))
            if not coordinates:
                return None
            return {
                "min_east_m": min(point[0] for point in coordinates),
                "max_east_m": max(point[0] for point in coordinates),
                "min_north_m": min(point[1] for point in coordinates),
                "max_north_m": max(point[1] for point in coordinates),
            }

        effective_bounds = projected_document_bounds(effective_document)
        sumo_input_bounds = projected_document_bounds(sumo_network_document)
        for label, extent in (
            ("effective render/task OSM", effective_bounds),
            ("SUMO input OSM", sumo_input_bounds),
        ):
            if extent is not None and (
                extent["min_east_m"] < float(bounds.min_east_m) - 1e-6
                or extent["max_east_m"] > float(bounds.max_east_m) + 1e-6
                or extent["min_north_m"] < float(bounds.min_north_m) - 1e-6
                or extent["max_north_m"] > float(bounds.max_north_m) + 1e-6
            ):
                raise SceneCompilationError(
                    f"{label} escaped the declared ENU crop after clipping"
                )
        manifest = {
            "schema_version": SCENE_COMPILER_SCHEMA_VERSION,
            "source": {
                "source_filename": source.name,
                "sha256": dataset.source_sha256,
                "byte_size": len(dataset.source_bytes),
                "root_metadata": dataset.root_metadata,
                "provenance": provenance,
            },
            "origin": origin.manifest_document(),
            "crop": _projected_crop_document(origin, bounds),
            "geometry_clipping_dependency": _shapely_dependency_document(),
            "effective_render_task_enu_bounds_m": effective_bounds,
            "sumo_input_enu_bounds_m": sumo_input_bounds,
            "closure_geometry_enu_bounds_m": selected_bounds,
            "selection": {
                "node_count": len(cropped.node_ids),
                "way_count": len(cropped.way_ids),
                "relation_count": len(cropped.relation_ids),
                "building_count": len(building_objects),
                "road_count": len(road_objects),
                "closure_policy": "selected nodes/ways/relations, full way node closure, full relation member closure, and parent relation closure",
            },
            "outputs": _output_file_digests(staging_dir),
            "netconvert": {
                "generated": run_netconvert,
                "required_version": REQUIRED_NETCONVERT_VERSION,
                "command_record": "sumo/netconvert-command.json",
            },
            "renderer_policy": {
                "official_osm2world_required_downstream": True,
                "custom_viewer_mesh_emitted": False,
                "hand_painted_raster_emitted": False,
            },
        }
        _write_json(staging_dir, "manifest.json", manifest)
        os.replace(staging_dir, output)
        published = True
    finally:
        if not published and staging_dir.exists():
            shutil.rmtree(staging_dir)

    return SceneCompilationResult(
        output_dir=output,
        manifest_path=output / "manifest.json",
        source_sha256=dataset.source_sha256,
        cropped_source_sha256=hashlib.sha256(cropped_json).hexdigest(),
        building_count=len(building_objects),
        road_count=len(road_objects),
        netconvert_generated=run_netconvert,
    )


# A concise alias is useful to callers which do not need to spell out the urban
# scene package name, while keeping the versioned public implementation explicit.
compile_scene = compile_urban_scene


__all__ = [
    "DEFAULT_BUILDING_HEIGHT_M",
    "DEFAULT_BUILDING_LEVEL_HEIGHT_M",
    "DEFAULT_CROP_MAX_EAST_M",
    "DEFAULT_CROP_MAX_NORTH_M",
    "DEFAULT_CROP_MIN_EAST_M",
    "DEFAULT_CROP_MIN_NORTH_M",
    "DEFAULT_ORIGIN_AMSL_M",
    "DEFAULT_ORIGIN_ELLIPSOID_HEIGHT_M",
    "DEFAULT_ORIGIN_GEOID_UNDULATION_M",
    "DEFAULT_ORIGIN_LATITUDE_DEG",
    "DEFAULT_ORIGIN_LONGITUDE_DEG",
    "EnuBounds",
    "OsmDataset",
    "CroppedOsmDataset",
    "SCENE_COMPILER_SCHEMA_VERSION",
    "SceneCompilationError",
    "SceneCompilationResult",
    "SceneOrigin",
    "compile_scene",
    "compile_urban_scene",
    "crop_osm_dataset",
    "gazebo_sdf_bytes",
    "load_osm_json",
    "osm_xml_bytes",
]
