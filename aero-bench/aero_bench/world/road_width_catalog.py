"""Production authoring of a road-width catalog for a compiled urban scene.

The strict ``aero-bench.world/v2`` ``WorldPackage`` authoring stage
(:mod:`aero_bench.world.scene_authoring`) refuses to emit a road network when any
source road lacks a defensible width. The checked-in Shanghai sample carries **no**
OSM ``width`` tag on any of its 289 roads, so this module supplies the missing
road widths from a real, reproducible source rather than inventing them:

* The compiled scene ships a deterministic, provenance-tagged SUMO conversion of
  its own roads -- ``osm/sumo-network.osm`` (each synthetic way carries
  ``aero_bench:source_id`` and ``aero_bench:component_index``) and the
  ``sumo/network.net.xml`` netconvert 1.27.1 produced from it (pinned by
  ``sumo/engineering-inputs.json`` ``network_sha256``). Each source road
  ``object_id`` therefore maps to **exact** SUMO edges and lanes.

* A road width is read from that net when the net itself declares the lane
  widths. When a lane omits ``width`` -- which is the normal case for the
  carriageway lanes here, because netconvert only writes a width when it differs
  from its default -- the lane contributes netconvert's documented **default lane
  width** (``SUMO_DEFAULT_LANE_WIDTH_M``). Such a width is explicitly an
  **estimate** from a lane-count/SUMO-default model; it is never presented as a
  surveyed or OSM-tagged measurement.

* A real OSM ``width`` tag, when present, is preserved verbatim as
  ``source == "osm_width_tag"`` and is never overwritten by an estimate.

Honesty rules:

* Every entry is keyed by the **exact** source road ``object_id`` and carries an
  explicit :class:`~aero_bench.world.scene_authoring.RoadWidthProvenance` naming
  the algorithm, the configuration constants, the exact SUMO edges/lanes, and a
  SHA-256 ``evidence_sha256``.
* No highway-class width table and no hidden fallback exists. A road that cannot
  be matched to a SUMO edge (and has no OSM width tag) is **exposed** and, by
  default, authoring **fails closed** rather than silently omitting it.
* Every consumed scene artifact is digest-checked against the compiled manifest
  and the SUMO engineering inputs; a mismatch fails closed.

The output fragment is directly consumable as the ``road_width_m`` section of an
``aero-bench.urban-world-authoring/v1`` :class:`UrbanWorldCatalog`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.scene_authoring import (
    SCENE_COMPILER_SCHEMA_VERSION,
    CatalogRoadWidth,
    RoadWidthLane,
    RoadWidthProvenance,
)

ROAD_WIDTH_CATALOG_SCHEMA_VERSION = "aero-bench.road-width-catalog/v1"
"""Schema version of the road-width catalog fragment this module emits."""

SUMO_DEFAULT_LANE_WIDTH_M = 3.2
"""SUMO's documented default lane width, used when a net lane omits ``width``.

sumolib reads a missing lane width as ``3.2`` (``sumolib/net/__init__.py``), which
is the ``NBEdge::DEFAULT_LANE_WIDTH`` constant netconvert writes lanes against.
This value is recorded in every estimate's provenance; it is a modelling constant,
not a surveyed measurement.
"""

SUMO_LANE_MODEL_ALGORITHM = "sum_of_modelled_lane_widths"
"""Algorithm name recorded on every ``sumo_lane_model`` estimate."""

_SIDEWALK_ALLOW = "pedestrian"
_NETCONVERT_VERSION_RE = re.compile(r"netconvert\s+(\d+\.\d+\.\d+)")
_EDGE_ID_RE = re.compile(r"^(-+)([0-9]+)(?:#\d+)?$")

_SCENE_MANIFEST_RELATIVE = "manifest.json"
_SCENE_OBJECTS_RELATIVE = "metadata/objects.json"
_SCENE_SYNTHETIC_OSM_RELATIVE = "osm/sumo-network.osm"
_SCENE_SUMO_NETWORK_RELATIVE = "sumo/network.net.xml"
_SCENE_SUMO_ENGINEERING_RELATIVE = "sumo/engineering-inputs.json"

_ROAD_OBJECT_ID_RE = re.compile(r"^road\.way\.(\d+)\.component\.(\d+)$")


class RoadWidthCatalogError(Exception):
    """The road-width catalog could not be authored from the compiled scene."""


@dataclass(frozen=True, slots=True)
class _SyntheticWay:
    source_id: str
    component_index: str
    highway: str | None


@dataclass(frozen=True, slots=True)
class _Lane:
    edge_id: str
    lane_id: str
    index: int
    width_m: float
    width_explicit: bool
    scope: Literal["carriageway", "sidewalk"]


@dataclass(frozen=True, slots=True)
class RoadWidthCatalogResult:
    """Coverage and identity of one authored road-width catalog fragment."""

    entries: tuple[CatalogRoadWidth, ...]
    unmatched_road_ids: tuple[str, ...]
    road_count: int
    osm_tagged_count: int
    estimated_count: int
    default_lane_width_m: float
    algorithm: str
    osm_lanes_cross_checked: int
    osm_lanes_disagreements: int
    sumo_netconvert_version: str | None
    scene_manifest_sha256: str
    synthetic_osm_sha256: str
    sumo_network_sha256: str
    fragment_document: dict[str, object]
    fragment_sha256: str

    @property
    def matched_count(self) -> int:
        return len(self.entries)


def _read_bytes(scene_root: Path, relative: str) -> bytes:
    path = scene_root / relative
    if not path.is_file():
        raise RoadWidthCatalogError(
            f"compiled urban scene is missing required file {relative!r}: {path}"
        )
    try:
        return path.read_bytes()
    except OSError as exc:  # pragma: no cover - the is_file() check precedes this
        raise RoadWidthCatalogError(
            f"cannot read compiled urban scene file {relative!r}: {exc}"
        ) from exc


def _read_json(document: bytes, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError as exc:
        raise RoadWidthCatalogError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RoadWidthCatalogError(f"{label} must be a JSON object")
    return parsed


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_synthetic_ways(document: bytes) -> dict[str, _SyntheticWay]:
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise RoadWidthCatalogError(
            f"osm/sumo-network.osm is not valid OSM XML: {exc}"
        ) from exc
    ways: dict[str, _SyntheticWay] = {}
    for way in root.findall("way"):
        synthetic_id = way.get("id")
        if not synthetic_id:
            continue
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        if tags.get("aero_bench:source_type") != "way":
            continue
        source_id = tags.get("aero_bench:source_id")
        component_index = tags.get("aero_bench:component_index")
        if source_id is None or component_index is None:
            continue
        ways[synthetic_id] = _SyntheticWay(
            source_id=source_id,
            component_index=component_index,
            highway=tags.get("highway"),
        )
    return ways


def _netconvert_version(document: bytes) -> str | None:
    match = _NETCONVERT_VERSION_RE.search(document[:4096].decode("utf-8", "replace"))
    return match.group(1) if match else None


def _parse_network_lanes(
    document: bytes, default_lane_width_m: float
) -> dict[str, dict[str, list[list[_Lane]]]]:
    """Return ``synthetic way id -> {'forward'|'reverse' -> [segment lanes]}``.

    Each directed way may be split into several segment edges (``...#0``,
    ``...#1``); every segment's lane list is retained so one cross-section can be
    chosen deterministically later. Internal junction edges (id starting with
    ``:``) are ignored: they are not part of a road's cross-section.
    """

    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise RoadWidthCatalogError(
            f"sumo/network.net.xml is not valid SUMO XML: {exc}"
        ) from exc
    grouped: dict[str, dict[str, list[list[_Lane]]]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if not edge_id or edge_id.startswith(":"):
            continue
        match = _EDGE_ID_RE.match(edge_id)
        if match is None:
            continue
        direction = "reverse" if len(match.group(1)) == 2 else "forward"
        synthetic_id = "-" + match.group(2)
        lanes: list[_Lane] = []
        for lane in edge.findall("lane"):
            raw_width = lane.get("width")
            allow = (lane.get("allow") or "").strip()
            width_explicit = raw_width is not None
            width_m = float(raw_width) if width_explicit else default_lane_width_m
            if not math.isfinite(width_m) or width_m <= 0.0:
                raise RoadWidthCatalogError(
                    f"SUMO edge {edge_id!r} lane declares a non-positive width "
                    f"{raw_width!r}"
                )
            lanes.append(
                _Lane(
                    edge_id=edge_id,
                    lane_id=lane.get("id") or "",
                    index=int(lane.get("index") or 0),
                    width_m=width_m,
                    width_explicit=width_explicit,
                    scope=("sidewalk" if allow == _SIDEWALK_ALLOW else "carriageway"),
                )
            )
        grouped.setdefault(synthetic_id, {}).setdefault(direction, []).append(lanes)
    return grouped


def _representative(lanes_by_direction: dict[str, list[list[_Lane]]]) -> list[_Lane]:
    """Pick one cross-section per direction from possibly multi-segment edges.

    Every segment of a way should share the same lane count; when they do not, the
    segment with the most lanes is taken deterministically (the physical
    cross-section is at least that wide).
    """

    chosen: list[_Lane] = []
    for direction in ("forward", "reverse"):
        segments = lanes_by_direction.get(direction)
        if not segments:
            continue
        best = max(
            segments,
            key=lambda lanes: (len(lanes), lanes[0].edge_id if lanes else ""),
        )
        chosen.extend(best)
    return chosen


def _road_width_from_lanes(
    lanes: list[_Lane], default_lane_width_m: float
) -> tuple[float, list[_Lane]]:
    carriageway = [lane for lane in lanes if lane.scope == "carriageway"]
    selected = carriageway if carriageway else lanes
    total = round(math.fsum(lane.width_m for lane in selected), 6)
    if total <= 0.0 or not math.isfinite(total):
        raise RoadWidthCatalogError(
            "modelled cross-section yields a non-positive road width"
        )
    return total, selected


def _evidence_digest(payload: dict[str, object]) -> str:
    return _sha256(canonical_json_bytes(payload))


def _estimate_evidence(
    *,
    synthetic_id: str,
    lanes: list[_Lane],
    width_m: float,
    default_lane_width_m: float,
    netconvert_version: str | None,
    network_sha256: str,
    osm_lanes_tag: str | None,
    osm_lanes_consistent: bool | None,
) -> RoadWidthProvenance:
    lane_records = tuple(
        RoadWidthLane(
            edge_id=lane.edge_id,
            lane_index=lane.index,
            lane_width_m=lane.width_m,
            width_explicit=lane.width_explicit,
            lane_scope=lane.scope,
        )
        for lane in lanes
    )
    representing_edges = tuple(sorted({lane.edge_id for lane in lanes}))
    evidence = {
        "algorithm": SUMO_LANE_MODEL_ALGORITHM,
        "default_lane_width_m": default_lane_width_m,
        "sumo_netconvert_version": netconvert_version,
        "sumo_network_sha256": network_sha256,
        "synthetic_way_id": synthetic_id,
        "representing_edges": list(representing_edges),
        "lane_count": len(lane_records),
        "lanes": [lane.model_dump(mode="json") for lane in lane_records],
        "osm_lanes_tag": osm_lanes_tag,
        "osm_lanes_consistent": osm_lanes_consistent,
        "width_m": width_m,
    }
    return RoadWidthProvenance(
        source="sumo_lane_model",
        is_estimate=True,
        measurement_status=(
            "estimate_from_sumo_lane_model; not a surveyed or OSM-tagged measurement"
        ),
        algorithm=SUMO_LANE_MODEL_ALGORITHM,
        evidence_sha256=_evidence_digest(evidence),
        default_lane_width_m=default_lane_width_m,
        sumo_netconvert_version=netconvert_version,
        sumo_network_sha256=network_sha256,
        synthetic_way_id=synthetic_id,
        representing_edges=representing_edges,
        lane_count=len(lane_records),
        lanes=lane_records,
        osm_lanes_tag=osm_lanes_tag,
        osm_lanes_consistent=osm_lanes_consistent,
    )


def _parse_length_m(value: object, label: str) -> float:
    if not isinstance(value, str):
        raise RoadWidthCatalogError(f"{label} must be a string")
    text = value.strip()
    if text.endswith("m"):
        text = text[:-1].strip()
    try:
        parsed = float(text)
    except ValueError as exc:
        raise RoadWidthCatalogError(
            f"{label} must be a numeric metre value, found {value!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise RoadWidthCatalogError(f"{label} must be a positive metre value")
    return parsed


def _verify_manifest_output(
    manifest: dict[str, object], relative: str, actual: bytes
) -> None:
    """Fail closed unless a manifest-declared scene output matches its digest."""

    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise RoadWidthCatalogError("scene manifest outputs must be a list")
    actual_digest = _sha256(actual)
    for record in outputs:
        if isinstance(record, dict) and record.get("path") == relative:
            declared = record.get("sha256")
            if declared == actual_digest:
                return
            raise RoadWidthCatalogError(
                f"scene output {relative!r} does not match its manifest digest "
                f"(declared {declared!r}, found {actual_digest})"
            )
    raise RoadWidthCatalogError(
        f"scene output {relative!r} is not declared in the manifest outputs list"
    )


def build_road_width_catalog(
    scene_root: Path,
    *,
    default_lane_width_m: float = SUMO_DEFAULT_LANE_WIDTH_M,
    require_complete: bool = True,
) -> RoadWidthCatalogResult:
    """Author a road-width catalog fragment from a compiled urban scene.

    ``require_complete`` (the default) fails closed when any source road cannot be
    matched to a SUMO edge and carries no OSM width tag; set it ``False`` only for
    an explicit coverage audit, where the returned ``unmatched_road_ids`` is the
    finding and the fragment covers the rest.
    """

    if not math.isfinite(default_lane_width_m) or default_lane_width_m <= 0.0:
        raise RoadWidthCatalogError(
            "default_lane_width_m must be a positive metre value"
        )

    manifest_bytes = _read_bytes(scene_root, _SCENE_MANIFEST_RELATIVE)
    manifest = _read_json(manifest_bytes, "manifest.json")
    if manifest.get("schema_version") != SCENE_COMPILER_SCHEMA_VERSION:
        raise RoadWidthCatalogError(
            "scene manifest is not a "
            f"{SCENE_COMPILER_SCHEMA_VERSION!r} package: "
            f"found {manifest.get('schema_version')!r}"
        )

    objects_bytes = _read_bytes(scene_root, _SCENE_OBJECTS_RELATIVE)
    objects = _read_json(objects_bytes, "metadata/objects.json")

    synthetic_osm_bytes = _read_bytes(scene_root, _SCENE_SYNTHETIC_OSM_RELATIVE)
    network_bytes = _read_bytes(scene_root, _SCENE_SUMO_NETWORK_RELATIVE)
    engineering_bytes = _read_bytes(scene_root, _SCENE_SUMO_ENGINEERING_RELATIVE)
    engineering = _read_json(engineering_bytes, "sumo/engineering-inputs.json")

    _verify_manifest_output(manifest, _SCENE_OBJECTS_RELATIVE, objects_bytes)
    _verify_manifest_output(
        manifest, _SCENE_SYNTHETIC_OSM_RELATIVE, synthetic_osm_bytes
    )

    synthetic_osm_sha256 = _sha256(synthetic_osm_bytes)
    network_sha256 = _sha256(network_bytes)

    declared_network = engineering.get("network_sha256")
    if not isinstance(declared_network, str):
        raise RoadWidthCatalogError(
            "sumo/engineering-inputs.json must declare a network_sha256"
        )
    if declared_network != network_sha256:
        raise RoadWidthCatalogError(
            "sumo/network.net.xml does not match the engineering-inputs "
            f"network_sha256 (expected {declared_network}, found {network_sha256})"
        )
    declared_osm = engineering.get("source_osm_sha256")
    if isinstance(declared_osm, str) and declared_osm != synthetic_osm_sha256:
        raise RoadWidthCatalogError(
            "osm/sumo-network.osm does not match the engineering-inputs "
            f"source_osm_sha256 (expected {declared_osm}, found {synthetic_osm_sha256})"
        )

    synthetic_ways = _parse_synthetic_ways(synthetic_osm_bytes)
    grouped = _parse_network_lanes(network_bytes, default_lane_width_m)
    netconvert_version = _netconvert_version(network_bytes)

    by_source: dict[tuple[str, str], str] = {}
    for synthetic_id, way in synthetic_ways.items():
        by_source[(way.source_id, way.component_index)] = synthetic_id

    raw_roads = objects.get("roads")
    if not isinstance(raw_roads, list) or not raw_roads:
        raise RoadWidthCatalogError(
            "metadata/objects.json.roads must be a non-empty array"
        )

    entries: list[CatalogRoadWidth] = []
    unmatched: list[str] = []
    osm_tagged = 0
    estimated = 0
    lanes_cross_checked = 0
    lanes_disagreements = 0
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_roads):
        label = f"metadata/objects.json.roads[{index}]"
        if not isinstance(raw, dict):
            raise RoadWidthCatalogError(f"{label} must be an object")
        object_id = raw.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise RoadWidthCatalogError(f"{label}.object_id must be a non-empty string")
        if object_id in seen_ids:
            raise RoadWidthCatalogError(
                f"duplicate source road object_id {object_id!r} in {label}"
            )
        seen_ids.add(object_id)
        match = _ROAD_OBJECT_ID_RE.match(object_id)
        if match is None:
            raise RoadWidthCatalogError(
                f"{label}.object_id {object_id!r} is not a "
                "'road.way.<way_id>.component.<index>' id"
            )
        way_id, component_index = match.group(1), match.group(2)

        width_tag = raw.get("width_osm_tag")
        osm_lanes_tag = raw.get("lanes")
        normalized_lanes_tag = (
            osm_lanes_tag if isinstance(osm_lanes_tag, str) and osm_lanes_tag else None
        )

        if width_tag is not None:
            width_m = _parse_length_m(width_tag, f"{label}.width_osm_tag")
            evidence = {
                "source": "osm_width_tag",
                "osm_width_tag": width_tag,
                "width_m": width_m,
                "sumo_network_sha256": network_sha256,
            }
            provenance = RoadWidthProvenance(
                source="osm_width_tag",
                is_estimate=False,
                measurement_status="preserved OSM width tag",
                algorithm="osm_width_tag_passthrough",
                evidence_sha256=_evidence_digest(evidence),
                osm_width_tag=width_tag,
                sumo_network_sha256=network_sha256,
                osm_lanes_tag=normalized_lanes_tag,
            )
            entries.append(
                CatalogRoadWidth(
                    object_id=object_id,
                    width_m=width_m,
                    source="osm_width_tag",
                    is_estimate=False,
                    provenance=provenance,
                )
            )
            osm_tagged += 1
            continue

        synthetic_id = by_source.get((way_id, component_index))
        direction_lanes = grouped.get(synthetic_id) if synthetic_id else None
        lane_cross_section = _representative(direction_lanes) if direction_lanes else []
        if not lane_cross_section:
            unmatched.append(object_id)
            continue
        width_m, selected = _road_width_from_lanes(
            lane_cross_section, default_lane_width_m
        )
        lane_count = len(selected)
        osm_lanes_consistent: bool | None = None
        if normalized_lanes_tag is not None and normalized_lanes_tag.isdigit():
            osm_lanes_consistent = int(normalized_lanes_tag) == lane_count
            lanes_cross_checked += 1
            if not osm_lanes_consistent:
                lanes_disagreements += 1
        provenance = _estimate_evidence(
            synthetic_id=synthetic_id or "",
            lanes=selected,
            width_m=width_m,
            default_lane_width_m=default_lane_width_m,
            netconvert_version=netconvert_version,
            network_sha256=network_sha256,
            osm_lanes_tag=normalized_lanes_tag,
            osm_lanes_consistent=osm_lanes_consistent,
        )
        entries.append(
            CatalogRoadWidth(
                object_id=object_id,
                width_m=width_m,
                source="sumo_lane_model",
                is_estimate=True,
                provenance=provenance,
            )
        )
        estimated += 1

    if unmatched and require_complete:
        missing = sorted(unmatched)
        raise RoadWidthCatalogError(
            f"{len(missing)} of {len(raw_roads)} compiled-scene roads could not be "
            "matched to a SUMO edge and carry no OSM width tag; no width is "
            "invented for them. Re-run with require_complete=False for a coverage "
            f"audit. Unmatched road object_ids: {missing}"
        )

    entries.sort(key=lambda entry: entry.object_id)
    unmatched_sorted = tuple(sorted(unmatched))
    manifest_sha256 = _sha256(manifest_bytes)

    fragment_document: dict[str, object] = {
        "schema_version": ROAD_WIDTH_CATALOG_SCHEMA_VERSION,
        "source_scene_manifest_sha256": manifest_sha256,
        "source_synthetic_osm_sha256": synthetic_osm_sha256,
        "source_sumo_network_sha256": network_sha256,
        "sumo_netconvert_version": netconvert_version,
        "default_lane_width_m": default_lane_width_m,
        "algorithm": SUMO_LANE_MODEL_ALGORITHM,
        "road_count": len(raw_roads),
        "osm_tagged_road_count": osm_tagged,
        "estimated_road_count": estimated,
        "osm_lanes_cross_checked": lanes_cross_checked,
        "osm_lanes_disagreements": lanes_disagreements,
        "unmatched_road_ids": list(unmatched_sorted),
        "road_width_m": [
            entry.model_dump(mode="json", exclude_none=True) for entry in entries
        ],
    }
    fragment_sha256 = _sha256(canonical_json_bytes(fragment_document) + b"\n")

    return RoadWidthCatalogResult(
        entries=tuple(entries),
        unmatched_road_ids=unmatched_sorted,
        road_count=len(raw_roads),
        osm_tagged_count=osm_tagged,
        estimated_count=estimated,
        default_lane_width_m=default_lane_width_m,
        algorithm=SUMO_LANE_MODEL_ALGORITHM,
        osm_lanes_cross_checked=lanes_cross_checked,
        osm_lanes_disagreements=lanes_disagreements,
        sumo_netconvert_version=netconvert_version,
        scene_manifest_sha256=manifest_sha256,
        synthetic_osm_sha256=synthetic_osm_sha256,
        sumo_network_sha256=network_sha256,
        fragment_document=fragment_document,
        fragment_sha256=fragment_sha256,
    )


def write_catalog_fragment(result: RoadWidthCatalogResult, path: Path) -> str:
    """Write the fragment as canonical JSON and return the SHA-256 of the bytes."""

    raw = canonical_json_bytes(result.fragment_document) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return _sha256(raw)


def merge_fragment_into_catalog(
    catalog_document: dict[str, object], result: RoadWidthCatalogResult
) -> dict[str, object]:
    """Return ``catalog_document`` with this fragment's ``road_width_m`` merged in.

    Any existing ``road_width_m`` entries for the same ``object_id`` are replaced.
    """

    merged = dict(catalog_document)
    existing = merged.get("road_width_m")
    by_id: dict[str, object] = {}
    if isinstance(existing, list):
        for entry in existing:
            if isinstance(entry, dict) and isinstance(entry.get("object_id"), str):
                by_id[entry["object_id"]] = entry
    for entry in result.entries:
        by_id[entry.object_id] = entry.model_dump(mode="json", exclude_none=True)
    merged["road_width_m"] = [by_id[key] for key in sorted(by_id)]
    return merged


__all__ = [
    "ROAD_WIDTH_CATALOG_SCHEMA_VERSION",
    "SUMO_DEFAULT_LANE_WIDTH_M",
    "SUMO_LANE_MODEL_ALGORITHM",
    "RoadWidthCatalogError",
    "RoadWidthCatalogResult",
    "build_road_width_catalog",
    "merge_fragment_into_catalog",
    "write_catalog_fragment",
]
