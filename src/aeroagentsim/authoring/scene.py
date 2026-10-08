"""Offline source geometry; ENU projection matches the existing viewer.

Intersecting ways are retained whole, including their referenced nodes. This
preserves OSM topology at crop boundaries rather than inventing clipped ways.
"""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from typing import Any

Bounds = list[float]
Point = tuple[float, float]


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label}: finite number required")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: finite number required") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label}: finite number required")
    return result


def _bounds(value: Any) -> Bounds:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("bounds: [west, south, east, north] required")
    w, s, e, n = (_number(v, "bounds") for v in value)
    if not -180 <= w < e <= 180 or not -90 < s < n < 90:
        raise ValueError(
            "bounds: ordered WGS84 coordinates required (no antimeridian wrap)"
        )
    return [w, s, e, n]


def _read(path: Path) -> tuple[ET.Element, dict[str, Point]]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"OSM XML: {exc}") from exc
    if root.tag != "osm":
        raise ValueError("OSM XML: expected osm root")
    nodes: dict[str, Point] = {}
    for node in root.findall("node"):
        identifier = node.attrib["id"]
        lon, lat = (
            _number(node.attrib["lon"], "node.lon"),
            _number(node.attrib["lat"], "node.lat"),
        )
        if not -180 <= lon <= 180 or not -90 <= lat <= 90 or identifier in nodes:
            raise ValueError(
                f"OSM node {identifier}: invalid coordinate or duplicate ID"
            )
        nodes[identifier] = (lon, lat)
    if not nodes:
        raise ValueError("OSM: no coordinate nodes")
    return root, nodes


def extract_bounds(path: Path) -> Bounds:
    _, nodes = _read(path)
    return _bounds(
        [
            min(p[0] for p in nodes.values()),
            min(p[1] for p in nodes.values()),
            max(p[0] for p in nodes.values()),
            max(p[1] for p in nodes.values()),
        ]
    )


def _inside(point: Point, bounds: Bounds) -> bool:
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def _segment(a: Point, b: Point, box: Bounds) -> bool:
    low, high = 0.0, 1.0
    for start, delta, minimum, maximum in (
        (a[0], b[0] - a[0], box[0], box[2]),
        (a[1], b[1] - a[1], box[1], box[3]),
    ):
        if delta == 0:
            if not minimum <= start <= maximum:
                return False
        else:
            t0, t1 = (minimum - start) / delta, (maximum - start) / delta
            low, high = max(low, min(t0, t1)), min(high, max(t0, t1))
            if high < low:
                return False
    return True


def _contains(points: list[Point], point: Point) -> bool:
    inside = False
    for a, b in pairwise(points):
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (b[0] - a[0]) * (
            point[1] - a[1]
        ) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def _selected(
    root: ET.Element, nodes: dict[str, Point], box: Bounds
) -> list[tuple[ET.Element, list[Point], dict[str, str]]]:
    selected = []
    for way in root.findall("way"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in way.findall("tag")}
        refs = [n.attrib["ref"] for n in way.findall("nd")]
        known = [nodes[r] for r in refs if r in nodes]
        intersects = any(_inside(p, box) for p in known) or any(
            _segment(a, b, box) for a, b in pairwise(known)
        )
        if refs and refs[0] == refs[-1] and len(known) == len(refs):
            intersects |= _contains(
                known, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            )
        if not intersects:
            continue
        missing = [r for r in refs if r not in nodes]
        if missing:
            raise ValueError(
                f"OSM way {way.attrib['id']}: missing node references {missing[:5]}"
            )
        selected.append((way, known, tags))
    return selected


def _source_bounds(nodes: dict[str, Point], box: Bounds) -> None:
    extent = [
        min(p[0] for p in nodes.values()),
        min(p[1] for p in nodes.values()),
        max(p[0] for p in nodes.values()),
        max(p[1] for p in nodes.values()),
    ]
    if (
        box[0] < extent[0]
        or box[1] < extent[1]
        or box[2] > extent[2]
        or box[3] > extent[3]
    ):
        raise ValueError("region bounds: outside local extract's coordinate coverage")


def _rings(parts: list[list[str]], relation: str) -> list[list[str]]:
    pending = [list(part) for part in parts]
    result: list[list[str]] = []
    while pending:
        chain = pending.pop()
        while chain and chain[0] != chain[-1]:
            for i, part in enumerate(pending):
                if chain[-1] == part[0]:
                    chain.extend(part[1:])
                elif chain[-1] == part[-1]:
                    chain.extend(list(reversed(part))[1:])
                else:
                    continue
                pending.pop(i)
                break
            else:
                raise ValueError(f"{relation}: incomplete multipolygon ring")
        if len(chain) < 4:
            raise ValueError(f"{relation}: degenerate multipolygon ring")
        result.append(chain)
    return result


def _buildings_relations(
    root: ET.Element, nodes: dict[str, Point], box: Bounds
) -> list[tuple[ET.Element, list[Point], dict[str, str], list[list[Point]]]]:
    ways = {way.attrib["id"]: way for way in root.findall("way")}
    result = []
    for relation in root.findall("relation"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in relation.findall("tag")}
        if tags.get("type") != "multipolygon" or tags.get("building", "no") == "no":
            continue
        identifier = "relation/" + relation.attrib["id"]
        members = relation.findall("member")
        known = [
            nodes[n.attrib["ref"]]
            for member in members
            if member.get("type") == "way" and member.get("ref") in ways
            for n in ways[member.attrib["ref"]].findall("nd")
            if n.attrib["ref"] in nodes
        ]
        if (
            not any(_inside(point, box) for point in known)
            and not any(_segment(a, b, box) for a, b in pairwise(known))
            and not _contains(
                known + known[:1], ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            )
        ):
            continue
        parts: dict[str, list[list[str]]] = {"outer": [], "inner": []}
        for member in members:
            if member.get("type") != "way":
                continue
            if member.get("ref") not in ways:
                raise ValueError(
                    f"{identifier}: missing member way {member.get('ref')}"
                )
            role = member.get("role") or "outer"
            if role not in parts:
                raise ValueError(f"{identifier}: unsupported member role {role}")
            refs = [n.attrib["ref"] for n in ways[member.attrib["ref"]].findall("nd")]
            if any(ref not in nodes for ref in refs):
                raise ValueError(f"{identifier}: missing member node references")
            parts[role].append(refs)
        outers = [
            [nodes[ref] for ref in ring] for ring in _rings(parts["outer"], identifier)
        ]
        inners = [
            [nodes[ref] for ref in ring] for ring in _rings(parts["inner"], identifier)
        ]
        for index, outer in enumerate(outers):
            pseudo = ET.Element(
                "way",
                {"id": relation.attrib["id"], "studio_id": f"{identifier}/{index}"},
            )
            holes = [ring for ring in inners if _contains(outer, ring[0])]
            result.append((pseudo, outer, tags, holes))
    return result


def compile_scene(
    path: Path, bounds: Bounds, *, alt: float, level_height_m: float
) -> dict[str, Any]:
    box = _bounds(bounds)
    altitude = _number(alt, "alt")
    level = _number(level_height_m, "level_height_m")
    if level <= 0:
        raise ValueError("level_height_m: positive explicit assumption required")
    root, nodes = _read(path)
    _source_bounds(nodes, box)
    lon0, lat0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rad, a, e2 = math.pi / 180, 6378137.0, 6.69437999014e-3
    denominator = 1 - e2 * math.sin(lat0 * rad) ** 2
    east = a / math.sqrt(denominator) * math.cos(lat0 * rad) * rad
    north = a * (1 - e2) / denominator**1.5 * rad
    features: list[dict[str, Any]] = []
    roads: list[dict[str, Any]] = []
    diagnostics = [
        "Intersecting OSM ways are retained whole beyond crop boundaries; ground is the authored planar ENU surface."
    ]
    geometry: list[
        tuple[ET.Element, list[Point], dict[str, str], list[list[Point]]]
    ] = [(way, points, tags, []) for way, points, tags in _selected(root, nodes, box)]
    geometry.extend(_buildings_relations(root, nodes, box))
    for way, points, tags, holes in geometry:
        identifier = way.get("studio_id") or "way/" + way.attrib["id"]
        if "highway" in tags and len(points) >= 2:
            roads.append(
                {
                    "id": identifier,
                    "points": [
                        [(lon - lon0) * east, (lat - lat0) * north, 0.0]
                        for lon, lat in points
                    ],
                    "tags": tags,
                }
            )
            features.append(
                {
                    "type": "Feature",
                    "id": identifier,
                    "properties": tags,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(p) for p in points],
                    },
                }
            )
        if tags.get("building", tags.get("building:part", "no")) == "no":
            continue
        if len(points) < 4 or points[0] != points[-1]:
            raise ValueError(
                f"{identifier}: building footprint must be a closed polygon"
            )
        props: dict[str, Any] = dict(tags)
        raw_height, raw_levels = tags.get("height"), tags.get("building:levels")
        try:
            if raw_height is not None:
                match = re.fullmatch(
                    r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:m)?\s*", raw_height
                )
                if not match:
                    raise ValueError("height must use metres")
                height = _number(match[1], "height")
                if height <= 0:
                    raise ValueError("height must be positive")
                props.update(height=height, height_source="osm.height")
            elif raw_levels is not None:
                levels = _number(raw_levels, "building:levels")
                if levels <= 0:
                    raise ValueError("levels must be positive")
                props.update(
                    height=levels * level,
                    height_source="osm.building:levels * authored level_height_m",
                    level_height_m=level,
                )
            else:
                diagnostics.append(
                    f"{identifier}: missing height and building:levels; footprint retained without a volume"
                )
        except ValueError as exc:
            # Retain source tags separately; never feed invalid heights to the viewer.
            props.pop("height", None)
            props.pop("building:levels", None)
            props["source_tags"] = tags
            diagnostics.append(
                f"{identifier}: {exc}; footprint retained without a volume"
            )
        features.append(
            {
                "type": "Feature",
                "id": identifier,
                "properties": props,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [list(p) for p in ring] for ring in [points, *holes]
                    ],
                },
            }
        )
    return {
        "origin": {"lat": lat0, "lon": lon0, "alt": altitude},
        "bounds": box,
        "geojson": {"type": "FeatureCollection", "features": features},
        "roads": roads,
        "ground": {
            "width_m": (box[2] - box[0]) * east,
            "depth_m": (box[3] - box[1]) * north,
        },
        "diagnostics": diagnostics,
        "attribution": "© OpenStreetMap contributors · ODbL",
    }


def crop_osm(path: Path, bounds: Bounds, output: Path) -> None:
    root, nodes = _read(path)
    box = _bounds(bounds)
    _source_bounds(nodes, box)
    ways = _selected(root, nodes, box)
    relation_ids = {
        entry[0].attrib["studio_id"].split("/")[1]
        for entry in _buildings_relations(root, nodes, box)
    }
    selected_ids = {way.attrib["id"] for way, _, _ in ways}
    by_id = {way.attrib["id"]: way for way in root.findall("way")}
    for relation in root.findall("relation"):
        if relation.attrib["id"] not in relation_ids:
            continue
        for member in relation.findall("member"):
            if member.get("type") == "way" and member.attrib["ref"] not in selected_ids:
                way = by_id[member.attrib["ref"]]
                ways.append((way, [], {}))
                selected_ids.add(way.attrib["id"])
    refs = {n.attrib["ref"] for way, _, _ in ways for n in way.findall("nd")}
    crop = ET.Element("osm", root.attrib)
    ET.SubElement(
        crop,
        "bounds",
        {
            "minlon": str(box[0]),
            "minlat": str(box[1]),
            "maxlon": str(box[2]),
            "maxlat": str(box[3]),
        },
    )
    for node in root.findall("node"):
        if node.attrib["id"] in refs:
            crop.append(node)
    for way, _, _ in ways:
        crop.append(way)
    for relation in root.findall("relation"):
        if relation.attrib["id"] in relation_ids:
            crop.append(relation)
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(".pending.xml")
    ET.ElementTree(crop).write(pending, encoding="utf-8", xml_declaration=True)
    pending.replace(output)


def generate_sumo(
    osm: Path, output: Path, netconvert: str = "netconvert"
) -> dict[str, Any]:
    if not osm.is_file():
        raise ValueError("SUMO: compile the region OSM first")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix="netconvert-"
    ) as directory:
        pending = Path(directory) / "network.net.xml"
        command = [
            netconvert,
            "--osm-files",
            str(osm.resolve()),
            "--output-file",
            str(pending),
        ]
        try:
            process = subprocess.run(
                command, capture_output=True, text=True, timeout=120, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"SUMO netconvert: {exc}") from exc
        if process.returncode != 0:
            raise ValueError(
                f"SUMO netconvert exited {process.returncode}: {process.stderr[-4000:]}"
            )
        try:
            network = ET.parse(pending).getroot()
        except (OSError, ET.ParseError) as exc:
            raise ValueError(f"SUMO: invalid generated network: {exc}") from exc
        edges = [
            edge
            for edge in network.findall("edge")
            if edge.get("function") != "internal"
        ]
        lanes = [lane for edge in edges for lane in edge.findall("lane")]
        if network.tag != "net" or not edges or not lanes:
            raise ValueError("SUMO: generated network has no real edges/lanes")
        for lane in lanes:
            shape = lane.get("shape")
            if not shape:
                raise ValueError("SUMO: generated lane missing shape")
            for point in shape.split():
                pair = point.split(",")
                if len(pair) < 2:
                    raise ValueError("SUMO: invalid lane shape")
                for value in pair:
                    _number(value, "SUMO lane coordinate")
        digest = hashlib.sha256(pending.read_bytes()).hexdigest()
        pending.replace(output)
        location = network.find("location")
        return {
            "status": "generated",
            "edge_count": len(edges),
            "lane_count": len(lanes),
            "sha256": digest,
            "command": command,
            "location": None if location is None else dict(location.attrib),
            "stderr": process.stderr[-4000:],
        }
