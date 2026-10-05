"""Extract labelled greens and canonical footprints from one scene compiler source.

Presentation design consumes this derivative. It does not update source geometry,
surveyed tree observations, or any formal simulation input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.frame_math import EnuTransform


GROUND_COVER_KINDS = {
    "pitch", "construction", "brownfield", "parking", "pedestrian_area",
    "square", "water", "explicit_surface",
}
ZONING_LANDUSES = {"residential", "commercial", "retail"}
WATER_NATURAL_VALUES = {"water"}
WATER_WATERWAY_VALUES = {"riverbank", "dock", "canal"}
WATER_LANDUSE_VALUES = {"reservoir", "basin"}


def green_tags(tags: dict[str, str]) -> bool:
    return (tags.get("landuse") in {"grass", "meadow", "village_green", "recreation_ground"}
            or tags.get("natural") in {"grassland", "wood"}
            or tags.get("leisure") in {"park", "garden"})


def ground_cover_kind(tags: dict[str, str]) -> str | None:
    """Return the source-supported render class for one OSM area.

    Residential, commercial, and retail are zoning declarations, not observed
    ground surfaces. Building and building-part outlines are also excluded: the
    canonical building footprints already own that area. An explicit ``surface``
    tag is retained only when no more specific supported area class applies.
    """
    if tags.get("landuse") in ZONING_LANDUSES or "building" in tags or "building:part" in tags:
        return None
    if tags.get("natural") in WATER_NATURAL_VALUES \
            or tags.get("waterway") in WATER_WATERWAY_VALUES \
            or tags.get("landuse") in WATER_LANDUSE_VALUES:
        return "water"
    if tags.get("leisure") == "pitch":
        return "pitch"
    if tags.get("landuse") == "construction":
        return "construction"
    if tags.get("landuse") == "brownfield":
        return "brownfield"
    if tags.get("amenity") in {"parking", "parking_space"} or tags.get("landuse") == "parking":
        return "parking"
    if tags.get("highway") == "pedestrian":
        return "pedestrian_area"
    if tags.get("place") == "square":
        return "square"
    surface = tags.get("surface")
    return "explicit_surface" if isinstance(surface, str) and surface.strip() else None


def _rings(parts: list[list[int]]) -> list[list[int]]:
    pending = [list(part) for part in parts]
    result = []
    while pending:
        ring = pending.pop(0)
        while ring[0] != ring[-1]:
            matches = [(i, part) for i, part in enumerate(pending)
                       if part[0] == ring[-1] or part[-1] == ring[-1]]
            if len(matches) != 1:
                raise ValueError("Green relation has an open or ambiguous member ring")
            i, part = matches[0]
            pending.pop(i)
            ring.extend((part if part[0] == ring[-1] else list(reversed(part)))[1:])
        if len(set(ring)) < 3:
            raise ValueError("Green ring has fewer than three vertices")
        result.append(ring)
    return result


def extract(osm_bytes: bytes, objects_bytes: bytes) -> dict:
    osm = json.loads(osm_bytes)
    objects = json.loads(objects_bytes)
    if objects["schema_version"] != "aero-bench.urban-scene-objects/v1" or objects["coordinate_frame"] != "ENU":
        raise ValueError("Canonical footprint metadata is not in ENU")
    origin = objects["origin"]
    if origin["frame"] != "WGS84->ECEF->ENU":
        raise ValueError("Unsupported source projection")
    transform = EnuTransform.from_origin(longitude_deg=origin["longitude_deg"],
                                        latitude_deg=origin["latitude_deg"],
                                        altitude_m=origin["ellipsoid_height_m"])
    osm_sha = hashlib.sha256(osm_bytes).hexdigest()
    elements = osm["elements"]
    nodes = {e["id"]: e for e in elements if e["type"] == "node"}
    ways = {e["id"]: e for e in elements if e["type"] == "way"}

    def project(node_id: int) -> list[float]:
        node = nodes[node_id]
        enu = transform.geodetic_to_enu(longitude_deg=node["lon"], latitude_deg=node["lat"],
                                        altitude_m=origin["ellipsoid_height_m"])
        return [enu.x, -enu.y]

    def polygon(outline: list, holes: list, label: str) -> None:
        geometry = Polygon(outline, holes)
        if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
            raise ValueError(f"Invalid green/source footprint: {label}")

    def provenance(element: dict, tags: dict[str, str]) -> dict:
        source_type = tags.get("aero_bench:source_type", element["type"])
        if source_type not in {"way", "relation"}:
            raise ValueError("Source area element type is invalid")
        return {"kind": "osm", "sourceSha256": osm_sha, "elementType": element["type"],
                "elementId": str(element["id"]), "sourceElementType": source_type,
                "sourceElementId": tags.get("aero_bench:source_id", str(element["id"])), "tags": tags}

    greens = []
    relation_ways = set()
    for element in sorted(elements, key=lambda e: (e["type"], e["id"])):
        tags = element.get("tags", {})
        if not green_tags(tags) or element["type"] == "node":
            continue
        area_provenance = provenance(element, tags)
        if element["type"] == "way":
            ring = element["nodes"]
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError(f"Tagged green way is not closed: {element['id']}")
            outlines, holes = [[project(n) for n in ring]], []
        elif element["type"] == "relation":
            parts = {"outer": [], "inner": []}
            for member in element["members"]:
                role = member["role"] or "outer"
                if member["type"] != "way" or role not in parts:
                    raise ValueError("Green relation has unsupported member")
                parts[role].append(ways[member["ref"]]["nodes"])
                relation_ways.add(member["ref"])
            outlines = [[project(n) for n in ring] for ring in _rings(parts["outer"])]
            holes = [[project(n) for n in ring] for ring in _rings(parts["inner"])]
            if not outlines:
                raise ValueError("Green relation has no outer ring")
        else:
            raise ValueError("Green geometry element type is invalid")
        assigned_holes = set()
        for i, outline in enumerate(outlines):
            selected_holes = []
            for j, hole in enumerate(holes):
                if Polygon(outline).covers(Polygon(hole)):
                    if j in assigned_holes:
                        raise ValueError("Green hole belongs to more than one outer ring")
                    assigned_holes.add(j)
                    selected_holes.append(hole)
            polygon(outline, selected_holes, str(element["id"]))
            greens.append({"id": f"osm:{element['type']}:{element['id']}:{i}",
                           "outline": outline, "holes": selected_holes, "provenance": area_provenance})
        if len(assigned_holes) != len(holes):
            raise ValueError("Green relation has an uncontained inner ring")
    greens = [g for g in greens if g["provenance"]["elementType"] != "way"
              or int(g["provenance"]["elementId"]) not in relation_ways]

    ground_covers = []
    ground_cover_relation_ways = set()
    for element in sorted(elements, key=lambda e: (e["type"], e["id"])):
        tags = element.get("tags", {})
        kind = ground_cover_kind(tags)
        if kind is None or element["type"] == "node":
            continue
        geometry_kind = tags.get("aero_bench:geometry_kind")
        area_provenance = provenance(element, tags)
        if element["type"] == "way":
            if geometry_kind != "polygon":
                continue
            ring = element["nodes"]
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError(f"Tagged ground-cover way is not closed: {element['id']}")
            outlines, holes = [[project(n) for n in ring]], []
        elif element["type"] == "relation":
            if geometry_kind != "multipolygon":
                continue
            parts = {"outer": [], "inner": []}
            for member in element["members"]:
                role = member["role"] or "outer"
                if member["type"] != "way" or role not in parts:
                    raise ValueError("Ground-cover relation has unsupported member")
                parts[role].append(ways[member["ref"]]["nodes"])
                ground_cover_relation_ways.add(member["ref"])
            outlines = [[project(n) for n in ring] for ring in _rings(parts["outer"])]
            holes = [[project(n) for n in ring] for ring in _rings(parts["inner"])]
            if not outlines:
                raise ValueError("Ground-cover relation has no outer ring")
        else:
            raise ValueError("Ground-cover geometry element type is invalid")
        assigned_holes = set()
        for i, outline in enumerate(outlines):
            selected_holes = []
            for j, hole in enumerate(holes):
                if Polygon(outline).covers(Polygon(hole)):
                    if j in assigned_holes:
                        raise ValueError("Ground-cover hole belongs to more than one outer ring")
                    assigned_holes.add(j)
                    selected_holes.append(hole)
            polygon(outline, selected_holes, str(element["id"]))
            ground_covers.append({"id": f"osm:{element['type']}:{element['id']}:{i}", "kind": kind,
                                  "surface": tags.get("surface"), "outline": outline,
                                  "holes": selected_holes, "provenance": area_provenance})
        if len(assigned_holes) != len(holes):
            raise ValueError("Ground-cover relation has an uncontained inner ring")
    ground_covers = [cover for cover in ground_covers
                     if cover["provenance"]["elementType"] != "way"
                     or int(cover["provenance"]["elementId"]) not in ground_cover_relation_ways]
    buildings = []
    for b in objects["buildings"]:
        # This schema defines one filled footprint ring per compiler component.
        outline = [[east, -north] for east, north in b["footprint_enu_m"]]
        polygon(outline, [], b["object_id"])
        buildings.append({"id": b["object_id"], "outline": outline, "holes": [],
                          "geometrySha256": b["effective_geometry_sha256"]})
    trees = [{"id": f"osm:node:{node['id']}", "x": project(node["id"])[0],
              "z": project(node["id"])[1], "sourceSha256": osm_sha, "sourceElementId": str(node["id"])}
             for node in sorted(nodes.values(), key=lambda n: n["id"])
             if node.get("tags", {}).get("natural") == "tree"]
    tagged_areas = [e for e in elements if e["type"] in {"way", "relation"}
                    and any(k in e.get("tags", {}) for k in ("landuse", "leisure", "natural"))]
    excluded = Counter()
    for e in tagged_areas:
        if green_tags(e.get("tags", {})):
            continue
        for k in ("landuse", "leisure", "natural"):
            if k in e["tags"]:
                excluded[f"{k}={e['tags'][k]}"] += 1
    ground_cover_counts = Counter(cover["kind"] for cover in ground_covers)
    ground_cover_areas = Counter()
    for cover in ground_covers:
        ground_cover_areas[cover["kind"]] += Polygon(cover["outline"], cover["holes"]).area
    return {"schemaVersion": "aero-bench.city-environment-source/v1",
            "coordinateFrame": "x-east,y-up,z-south-meters",
            "source": {"osmSha256": osm_sha, "objectsSha256": hashlib.sha256(objects_bytes).hexdigest(),
                       "origin": {k: origin[k] for k in ("latitude_deg", "longitude_deg", "ellipsoid_height_m")},
                       "projection": origin["frame"]},
            "buildings": buildings, "greens": greens, "groundCovers": ground_covers, "sourceTrees": trees,
            "inspection": {"taggedAreaCount": len(tagged_areas), "greenAreaCount": len(greens),
                           "groundCoverCount": len(ground_covers),
                           "groundCoverCountsByKind": dict(sorted(ground_cover_counts.items())),
                           "groundCoverSourceAreaM2ByKind": {
                               key: round(value, 6) for key, value in sorted(ground_cover_areas.items())},
                           "sourceTreeCount": len(trees), "excludedTags": dict(sorted(excluded.items())),
                           "measurementStatus": "OSM tags; derived green tree placements are presentation design, not surveyed observations"}}


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-environment", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    derivative = extract(args.osm.read_bytes(), args.objects.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(derivative))
    result = {"candidate": str(args.output), "bytes": args.output.stat().st_size,
              "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
              "inspection": derivative["inspection"]}
    if args.baseline_environment is not None:
        baseline = json.loads(args.baseline_environment.read_bytes())
        if baseline.get("source") != derivative["source"]:
            raise ValueError("Baseline environment authority differs from the candidate")
        baseline_green_bytes = canonical_bytes(baseline.get("greens"))
        candidate_green_bytes = canonical_bytes(derivative["greens"])
        if baseline_green_bytes != candidate_green_bytes:
            raise ValueError("Candidate changed the existing greens")
        result["greenPreservation"] = {
            "status": "byte-identical-canonical-json", "count": len(derivative["greens"]),
            "sha256": hashlib.sha256(candidate_green_bytes).hexdigest(),
            "baseline": str(args.baseline_environment),
        }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
