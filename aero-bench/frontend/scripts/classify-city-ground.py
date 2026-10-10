"""Classify the default city scene's ground extent into render classes (C6a).

The viewer draws a large untextured gray plane under the whole scene. Anything the
viewer does not cover with a drawn road surface, a building mesh, or a recognised
green patch shows through as unexplained gray. This script answers, for every
square metre of the scene's declared extent, which class explains it, and where
the gray actually comes from (an excluded OSM land use, a plaza, water, some other
tagged feature, or genuinely no source data at all).

All inputs are resolved from the published scene manifest
(``frontend/public/city-presentation/default-scene-v1.json``) and verified by
SHA-256 against that manifest's own pins before use. A mismatch is a hard error:
this script never silently substitutes or repairs a pinned input.

Priority order (each class claims area only where no higher-priority class already
claimed it, so the classes exactly partition the extent):

  1. roadbed            -- road v3 ``roadbed`` parts (open-ring format)
  2. walkbed            -- road v3 ``walkbed`` parts (open-ring format)
  3. building            -- canonical building footprints (environment source)
  4. green               -- recognised OSM greens (environment source; same tag
                            predicate as ``city_environment_source.green_tags``
                            and ``city-environment.ts``'s ``recognizedGreen``)
  5. excluded_landuse    -- landuse/leisure values the parser explicitly excludes:
                            landuse in {residential, construction, commercial,
                            retail, brownfield}, leisure=pitch
  6. plaza               -- highway=pedestrian areas, place=square
  7. water               -- natural=water, waterway in {riverbank, dock, canal},
                            landuse in {reservoir, basin} (see
                            ``WATER_TAGS_ARE_ASSUMED`` below: no example exists in
                            the current source, so this is a documented
                            assumption, not a measured class)
  8. other_tagged        -- any other tagged OSM area (tag reported)
  9. unclassified        -- no source data at all

Buildings and greens are read directly from the pinned environment-source
derivative (not re-derived from raw OSM tags): that file is itself produced,
tested and pinned by ``city_environment_source.py`` from the same raw OSM input,
and it is the exact set the viewer's parser (``city-environment.ts``) accepts.
Re-deriving them independently here would risk silently drifting from what the
parser actually sees.

Rendering omission checks: this script cannot observe live vegetation meshes, so
the green check remains ``not_measured``. Ground-cover omission can be measured
when the caller supplies the instantiated viewer report emitted by
``city-vegetation-layer.ts``. The report must match both the environment source's
accepted IDs and the road document's verified displayed-surface hash; otherwise
the comparison fails instead of reporting zero omissions for unrelated geometry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))

from city_environment_source import green_tags, ground_cover_kind  # noqa: E402
from city_environment_source import _rings as assemble_relation_rings  # noqa: E402
from city_motion_obstacles import fixture_polygon, road_surface_polygon  # noqa: E402
from city_preview_scene import PUBLIC, ROOT, public_asset  # noqa: E402
from city_surface_identity import displayed_surface_sha256  # noqa: E402

sys.path.insert(0, str(ROOT))
from aero_bench.world.frame_math import EnuTransform  # noqa: E402

DEFAULT_SCENE = PUBLIC / "city-presentation/default-scene-v1.json"
DEFAULT_OUTPUT_DIR = ROOT / "validation/platform-plan-20261001/G"

# Exact match to the parser's current exclusion set (city_environment_source.py's
# ``inspection.excludedTags`` on the default scene): landuse values that are
# known non-green land use, plus leisure=pitch. Any landuse/leisure/natural tag
# outside this fixed set and outside ``green_tags`` is NOT silently folded in
# here; it is reported under ``other_tagged`` instead.
EXCLUDED_LANDUSE_TAGS = {
    ("landuse", "residential"), ("landuse", "construction"), ("landuse", "commercial"),
    ("landuse", "retail"), ("landuse", "brownfield"), ("leisure", "pitch"),
}

# No water parcel exists in the current default-scene source (measured: zero
# natural=water / waterway / reservoir elements). These tag sets are a documented
# assumption about what a plaza/water area would look like if the source gained
# one; they are not verified against a real example in this scene.
PLAZA_HIGHWAY_VALUES = {"pedestrian"}
PLAZA_PLACE_VALUES = {"square"}
WATER_NATURAL_VALUES = {"water"}
WATER_WATERWAY_VALUES = {"riverbank", "dock", "canal"}
WATER_LANDUSE_VALUES = {"reservoir", "basin"}

CLASS_ORDER = ["roadbed", "walkbed", "building", "green", "excluded_landuse", "plaza", "water", "other_tagged"]

CLASS_COLORS = {
    "roadbed": "#4a4a4a", "walkbed": "#9c9c9c", "building": "#c0392b", "green": "#2e8b57",
    "excluded_landuse": "#d4a017", "plaza": "#8e7cc3", "water": "#1f6fb2",
    "other_tagged": "#e07b39", "unclassified": "#bfbfbf",
}

AREA_RELATIVE_TOLERANCE = 1e-6
# Below this, a clipped contribution is floating-point boolean-op noise, not real ground.
NOISE_AREA_M2 = 1e-6


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected: str, label: str) -> None:
    actual = sha256_hex(data)
    if actual != expected:
        raise ValueError(f"{label} sha256 mismatch: expected {expected}, got {actual}")


def read_verified(path: Path, expected_sha256: str, label: str) -> bytes:
    if not path.exists():
        raise FileNotFoundError(f"{label} is missing: {path}")
    data = path.read_bytes()
    verify_sha256(data, expected_sha256, label)
    return data


class ResolvedInputs:
    """Every pinned file this classification needs, verified against the scene."""

    def __init__(self, scene_path: Path):
        self.scene_path = scene_path
        self.scene = json.loads(scene_path.read_text())
        if self.scene.get("schema_version") != "aero-bench.city-scene-preview/v1":
            raise ValueError("Unsupported city scene manifest schema_version")
        road_assets = self.scene["road_assets"]
        environment_source = self.scene["environment_source"]
        mesh_pack = self.scene["mesh_pack"]

        road_path = public_asset(road_assets["road"]["url"])
        road_bytes = read_verified(road_path, road_assets["road"]["sha256"], "road v3")
        self.road = json.loads(road_bytes)

        env_path = public_asset(environment_source["url"])
        env_bytes = read_verified(env_path, environment_source["sha256"], "environment source")
        self.environment = json.loads(env_bytes)

        pack_manifest_path = public_asset(mesh_pack["base_url"] + "manifest.json")
        pack_manifest_bytes = read_verified(pack_manifest_path, mesh_pack["manifest"]["sha256"], "mesh pack manifest")
        if sha256_hex(pack_manifest_bytes) != road_assets["mesh_pack_manifest_sha256"]:
            raise ValueError("mesh pack manifest sha256 disagrees between scene.mesh_pack and scene.road_assets")
        self.pack_manifest = json.loads(pack_manifest_bytes)

        pack_source_sha256 = self.pack_manifest["source"]["sha256"]
        if pack_source_sha256 != road_assets["mesh_pack_source_sha256"]:
            raise ValueError("mesh pack source sha256 disagrees between pack manifest and scene.road_assets")
        if pack_source_sha256 != self.environment["source"]["osmSha256"]:
            raise ValueError("environment source osmSha256 disagrees with the mesh pack's source sha256")
        if pack_source_sha256 != self.road.get("mesh_pack_source_sha256"):
            raise ValueError("road v3 mesh_pack_source_sha256 disagrees with the mesh pack's source sha256")

        source_osm_path = public_asset(mesh_pack["base_url"] + "assets/" + pack_source_sha256)
        self.source_osm_path = source_osm_path
        self.source_osm_bytes = read_verified(source_osm_path, pack_source_sha256, "raw source OSM (mesh pack source)")
        self.osm = json.loads(self.source_osm_bytes)

        objects_sha256 = self.environment["source"]["objectsSha256"]
        road_objects_sha256 = self.road.get("source_context", {}).get("objects_json_sha256")
        if road_objects_sha256 is not None and road_objects_sha256 != objects_sha256:
            raise ValueError("road v3 source_context.objects_json_sha256 disagrees with environment source objectsSha256")

        surface_sha256 = displayed_surface_sha256_of(self.road)
        if surface_sha256 != self.road.get("displayed_surface_sha256"):
            raise ValueError("road v3 displayed_surface_sha256 does not match its own roadbed/walkbed content")
        if surface_sha256 != road_assets.get("displayed_surface_sha256"):
            raise ValueError("road v3 displayed_surface_sha256 disagrees with scene.road_assets.displayed_surface_sha256")

        self.inputs_manifest = [
            {"label": "scene", "path": relpath(scene_path), "sha256": sha256_hex(scene_path.read_bytes())},
            {"label": "road_v3", "path": relpath(road_path), "sha256": road_assets["road"]["sha256"]},
            {"label": "environment_source", "path": relpath(env_path), "sha256": environment_source["sha256"]},
            {"label": "mesh_pack_manifest", "path": relpath(pack_manifest_path), "sha256": mesh_pack["manifest"]["sha256"]},
            {"label": "source_osm", "path": relpath(source_osm_path), "sha256": pack_source_sha256},
        ]


def displayed_surface_sha256_of(road: dict) -> str:
    return displayed_surface_sha256(road["roadbed"], road["walkbed"])


def relpath(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def scene_extent(pack_manifest: dict) -> Polygon:
    extent = pack_manifest["extent"]
    west, east, south, north = extent["west"], extent["east"], extent["south"], extent["north"]
    if not (west < east and south < north):
        raise ValueError("Mesh pack extent is degenerate")
    return box(west, south, east, north)


def road_class_features(road: dict, kind: str) -> list[dict]:
    features = []
    for index, part in enumerate(road[kind]):
        polygon = road_surface_polygon(part)
        features.append({"id": f"road.{kind}[{index}]", "polygon": polygon, "tags": {}})
    return features


def building_class_features(environment: dict) -> list[dict]:
    features = []
    for building in environment["buildings"]:
        polygon = fixture_polygon({"outline": building["outline"], "holes": building["holes"]})
        features.append({"id": building["id"], "polygon": polygon, "tags": {}})
    return features


def green_class_features(environment: dict) -> list[dict]:
    features = []
    for green in environment["greens"]:
        polygon = fixture_polygon({"outline": green["outline"], "holes": green["holes"]})
        features.append({"id": green["id"], "polygon": polygon, "tags": green["provenance"].get("tags", {})})
    return features


def project_factory(origin: dict):
    transform = EnuTransform.from_origin(longitude_deg=origin["longitude_deg"], latitude_deg=origin["latitude_deg"],
                                        altitude_m=origin["ellipsoid_height_m"])

    def project(lon: float, lat: float) -> list[float]:
        enu = transform.geodetic_to_enu(longitude_deg=lon, latitude_deg=lat, altitude_m=origin["ellipsoid_height_m"])
        return [enu.x, -enu.y]

    return project


def extract_tagged_areas(osm: dict, origin: dict) -> list[dict]:
    """Non-green tagged polygon/multipolygon areas from the raw source OSM.

    Mirrors ``city_environment_source.extract``'s ring assembly, generalised to
    any tagged area rather than only greens, and excludes anything already
    carried by ``green_class_features`` (the environment source's own greens)
    so the two never double-count the same OSM element.
    """
    elements = osm["elements"]
    nodes = {e["id"]: e for e in elements if e["type"] == "node"}
    ways = {e["id"]: e for e in elements if e["type"] == "way"}
    project = project_factory(origin)

    def project_node(node_id: int) -> list[float]:
        node = nodes[node_id]
        return project(node["lon"], node["lat"])

    relation_member_ways: set[int] = set()
    features: list[dict] = []
    for element in sorted(elements, key=lambda e: (e["type"], e["id"])):
        if element["type"] not in ("way", "relation"):
            continue
        tags = element.get("tags", {})
        geometry_kind = tags.get("aero_bench:geometry_kind")
        if element["type"] == "way":
            if geometry_kind != "polygon" or green_tags(tags):
                continue
            ring = element["nodes"]
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError(f"Tagged area way {element['id']} is not a closed ring")
            polygon = Polygon([project_node(n) for n in ring])
            if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
                raise ValueError(f"Tagged area way {element['id']} has invalid geometry")
            features.append({"elementType": "way", "elementId": element["id"], "tags": tags, "polygon": polygon})
        else:
            for member in element["members"]:
                role = member["role"] or "outer"
                if member["type"] != "way" or role not in ("outer", "inner"):
                    raise ValueError(f"Relation {element['id']} has an unsupported member")
                relation_member_ways.add(member["ref"])
            if geometry_kind != "multipolygon" or green_tags(tags):
                continue
            parts: dict[str, list[list[int]]] = {"outer": [], "inner": []}
            for member in element["members"]:
                role = member["role"] or "outer"
                parts[role].append(ways[member["ref"]]["nodes"])
            outer_rings = assemble_relation_rings(parts["outer"])
            inner_rings = assemble_relation_rings(parts["inner"]) if parts["inner"] else []
            if not outer_rings:
                raise ValueError(f"Relation {element['id']} has no outer ring")
            outlines = [[project_node(n) for n in ring] for ring in outer_rings]
            holes = [[project_node(n) for n in ring] for ring in inner_rings]
            assigned: set[int] = set()
            for component_index, outline in enumerate(outlines):
                outline_polygon = Polygon(outline)
                selected_holes = []
                for hole_index, hole in enumerate(holes):
                    if outline_polygon.covers(Polygon(hole)):
                        if hole_index in assigned:
                            raise ValueError(f"Relation {element['id']} hole belongs to more than one outer ring")
                        assigned.add(hole_index)
                        selected_holes.append(hole)
                polygon = Polygon(outline, selected_holes)
                if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
                    raise ValueError(f"Relation {element['id']} component {component_index} has invalid geometry")
                features.append({"elementType": "relation", "elementId": element["id"], "componentIndex": component_index,
                                 "tags": tags, "polygon": polygon})
            if len(assigned) != len(holes):
                raise ValueError(f"Relation {element['id']} has an uncontained inner ring")
    # A way that is itself a member of some multipolygon relation is a boundary
    # segment of that relation's area, never a standalone area in its own right.
    return [f for f in features if not (f["elementType"] == "way" and f["elementId"] in relation_member_ways)]


def classify_area_tags(tags: dict) -> tuple[str, str]:
    """Return (class_name, reported_tag) for one non-green tagged OSM area."""
    landuse, leisure = tags.get("landuse"), tags.get("leisure")
    for key, value in (("landuse", landuse), ("leisure", leisure)):
        if value is not None and (key, value) in EXCLUDED_LANDUSE_TAGS:
            return "excluded_landuse", f"{key}={value}"
    highway, place = tags.get("highway"), tags.get("place")
    if highway in PLAZA_HIGHWAY_VALUES:
        return "plaza", f"highway={highway}"
    if place in PLAZA_PLACE_VALUES:
        return "plaza", f"place={place}"
    natural, waterway = tags.get("natural"), tags.get("waterway")
    if natural in WATER_NATURAL_VALUES:
        return "water", f"natural={natural}"
    if waterway in WATER_WATERWAY_VALUES:
        return "water", f"waterway={waterway}"
    if landuse in WATER_LANDUSE_VALUES:
        return "water", f"landuse={landuse}"
    for key in ("building", "building:part", "amenity", "tourism", "shop", "man_made", "aeroway",
                "landuse", "leisure", "natural", "highway", "place"):
        if key in tags:
            return "other_tagged", f"{key}={tags[key]}"
    return "other_tagged", "untagged"


def tagged_class_features(tagged_areas: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {"excluded_landuse": [], "plaza": [], "water": [], "other_tagged": []}
    for area in tagged_areas:
        class_name, detail = classify_area_tags(area["tags"])
        source_type = area["tags"].get("aero_bench:source_type", area["elementType"])
        source_id = area["tags"].get("aero_bench:source_id", str(area["elementId"]))
        feature_id = f"osm:{area['elementType']}:{area['elementId']}"
        if "componentIndex" in area:
            feature_id += f":{area['componentIndex']}"
        buckets[class_name].append({
            "id": feature_id, "polygon": area["polygon"], "tags": area["tags"], "detail": detail,
            "sourceElementType": source_type, "sourceElementId": source_id,
            "groundCoverKind": ground_cover_kind(area["tags"]),
            "groundCoverId": f"osm:{area['elementType']}:{area['elementId']}:{area.get('componentIndex', 0)}",
        })
    return buckets


def clean_tags(tags: dict) -> dict:
    return {k: v for k, v in tags.items() if not k.startswith("aero_bench:")}


def partition_ground(extent: Polygon, class_features: dict[str, list[dict]]) -> dict:
    """Sequentially claim area for each class in priority order.

    Each class's contribution is clipped to whatever remains unclaimed before
    it, so the classes form an exact partition of the extent (no double
    counting), and ``unclassified`` is simply whatever is left at the end.
    """
    remaining: BaseGeometry = extent
    classes: dict[str, dict] = {}
    for class_name in CLASS_ORDER:
        features = class_features.get(class_name, [])
        kept_features = []
        claimed_parts = []
        for feature in features:
            clipped = feature["polygon"].intersection(remaining)
            # A lower-priority raw tag that is almost entirely covered by a higher-priority
            # class (for example a raw OSM "building=yes" way under the authoritative
            # canonical building footprint) can leave a sub-millimetre floating-point sliver
            # at its edge. That is boolean-op noise, not a real ground class, so it is
            # dropped here; it never reaches a size that would change the partition check.
            if clipped.is_empty or clipped.area <= NOISE_AREA_M2:
                continue
            kept_features.append({**feature, "clipped_geometry": clipped, "clipped_area_m2": clipped.area})
            claimed_parts.append(clipped)
        claimed = unary_union(claimed_parts) if claimed_parts else Polygon()
        classes[class_name] = {"geometry": claimed, "features": kept_features}
        remaining = remaining.difference(claimed)
    classes["unclassified"] = {"geometry": remaining, "features": []}
    return classes


def connected_regions(geometry: BaseGeometry) -> list[Polygon]:
    """Flatten a (possibly mixed-type) geometry into its constituent polygons.

    Boolean ops on real-world polygons occasionally leave degenerate zero-area
    slivers (LineString/Point) at shared boundaries; those carry no area and
    are not a ground class, so they are dropped here rather than passed on.
    """
    if geometry.is_empty:
        return []
    if not hasattr(geometry, "geoms"):
        return [geometry] if geometry.geom_type == "Polygon" and geometry.area > 0 else []
    regions = []
    for part in geometry.geoms:
        regions.extend(connected_regions(part))
    return regions


def ground_cover_omission_check(environment: dict, drawn_set: dict | None,
                                displayed_surface_sha256: str) -> dict:
    accepted_ids = sorted(cover["id"] for cover in environment.get("groundCovers", []))
    if drawn_set is None:
        return {
            "status": "not_measured",
            "reason": ("No viewer ground-cover drawn-set report was supplied. A zero omission count cannot be "
                       "inferred from source or parser data."),
            "parser_accepted_ground_cover_count": len(accepted_ids),
            "parser_accepted_ground_cover_ids": accepted_ids,
        }
    if drawn_set.get("schema_version") != "aero-bench.city-ground-cover-drawn-set/v1":
        raise ValueError("Unsupported viewer ground-cover drawn-set schema")
    required_lists = ("parser_accepted_ids", "drawable_ids", "fully_clipped_ids", "drawn_ids",
                      "omitted_ids", "unexpected_ids")
    if any(not isinstance(drawn_set.get(field), list)
           or any(not isinstance(value, str) for value in drawn_set[field]) for field in required_lists):
        raise ValueError("Viewer ground-cover drawn-set lists are invalid")
    if not accepted_ids:
        raise ValueError("Scene environment source has no groundCovers to compare with the viewer drawn set")
    if drawn_set.get("geometry_id") != displayed_surface_sha256:
        raise ValueError("Viewer ground-cover drawn set belongs to a different displayed surface")
    if sorted(drawn_set["parser_accepted_ids"]) != accepted_ids:
        raise ValueError("Viewer ground-cover parser set differs from the scene environment source")
    if any(len(set(drawn_set[field])) != len(drawn_set[field]) for field in required_lists):
        raise ValueError("Viewer ground-cover drawn-set lists contain duplicate IDs")
    accepted = set(accepted_ids)
    drawable = set(drawn_set["drawable_ids"])
    fully_clipped = set(drawn_set["fully_clipped_ids"])
    if drawable & fully_clipped or drawable | fully_clipped != accepted:
        raise ValueError("Viewer drawable and fully-clipped ground-cover sets do not partition the parser set")
    drawn = set(drawn_set["drawn_ids"])
    omitted = sorted(drawable - drawn)
    unexpected = sorted(drawn - drawable)
    if omitted != sorted(drawn_set["omitted_ids"]) or unexpected != sorted(drawn_set["unexpected_ids"]):
        raise ValueError("Viewer ground-cover omission calculation is inconsistent")
    if drawn_set.get("omission_count") != len(omitted):
        raise ValueError("Viewer ground-cover omission count is inconsistent")
    expected_status = "pass" if not omitted and not unexpected else "fail"
    if drawn_set.get("status") != expected_status:
        raise ValueError("Viewer ground-cover drawn-set status is inconsistent")
    return {**drawn_set, "status": "measured-pass" if expected_status == "pass" else "measured-fail"}


def build_report(scene_relpath: str, inputs: ResolvedInputs, extent: Polygon, classes: dict[str, dict],
                 greens_accepted: list[dict], drawn_set: dict | None = None) -> dict:
    extent_area = extent.area
    class_table = []
    for class_name in [*CLASS_ORDER, "unclassified"]:
        geometry = classes[class_name]["geometry"]
        area = geometry.area if not geometry.is_empty else 0.0
        features = classes[class_name]["features"]
        entry = {
            "class": class_name, "area_m2": round(area, 6),
            "share_of_extent": round(area / extent_area, 9) if extent_area else 0.0,
            "feature_count": len(features),
        }
        if class_name in ("excluded_landuse", "plaza", "water", "other_tagged"):
            by_detail: dict[str, dict] = {}
            for feature in features:
                detail = feature["detail"]
                bucket = by_detail.setdefault(detail, {"detail": detail, "count": 0, "ids": [], "_geoms": []})
                bucket["count"] += 1
                bucket["ids"].append(feature["id"])
                bucket["_geoms"].append(feature["clipped_geometry"])
            by_tag = []
            for bucket in by_detail.values():
                # Union (not sum) each tag's clipped parts: avoids overstating area if two
                # same-tag source elements happen to overlap after clipping.
                area = unary_union(bucket["_geoms"]).area if bucket["_geoms"] else 0.0
                by_tag.append({"detail": bucket["detail"], "count": bucket["count"],
                               "area_m2": round(area, 6), "ids": bucket["ids"]})
            entry["by_tag"] = sorted(by_tag, key=lambda b: -b["area_m2"])
            if class_name == "other_tagged":
                entry["features"] = [{"id": f["id"], "area_m2": round(f["clipped_area_m2"], 6),
                                      "tags": clean_tags(f["tags"])} for f in features]
        elif class_name in ("roadbed", "walkbed"):
            entry["ids"] = [f["id"] for f in features]
        elif class_name in ("building", "green"):
            entry["ids"] = [f["id"] for f in features]
        class_table.append(entry)

    unclassified_regions = sorted(connected_regions(classes["unclassified"]["geometry"]), key=lambda p: -p.area)
    top_unclassified = [{
        "rank": index + 1, "area_m2": round(region.area, 6),
        "centroid": [round(region.centroid.x, 3), round(region.centroid.y, 3)],
    } for index, region in enumerate(unclassified_regions[:20])]

    accepted_ids = {g["id"] for g in greens_accepted}
    rendering_omission_check = {
        "status": "not_measured",
        "reason": ("This script loads only published scene files; it does not run the three.js viewer or "
                  "city-vegetation-layer.ts, so it cannot observe which grass meshes the live scene actually "
                  "draws. It can only report the parser-accepted side of the comparison."),
        "parser_accepted_green_count": len(greens_accepted),
        "parser_accepted_green_ids": sorted(accepted_ids),
    }

    total_check = sum(entry["area_m2"] for entry in class_table)
    relative_error = abs(total_check - extent_area) / extent_area if extent_area else 0.0
    source_ground_covers = [feature for class_name in ("excluded_landuse", "plaza", "water", "other_tagged")
                            for feature in classes[class_name]["features"]
                            if feature.get("groundCoverKind") is not None]
    source_ground_cover_counts: dict[str, int] = {}
    for feature in source_ground_covers:
        kind = feature["groundCoverKind"]
        source_ground_cover_counts[kind] = source_ground_cover_counts.get(kind, 0) + 1

    return {
        "schema_version": "aero-bench.city-ground-classification/v1",
        "scene": scene_relpath,
        "inputs": inputs.inputs_manifest,
        "extent": {"west": extent.bounds[0], "south": extent.bounds[1], "east": extent.bounds[2],
                  "north": extent.bounds[3], "area_m2": round(extent_area, 6),
                  "source": "mesh_pack.manifest.extent (east-up-south meters)"},
        "classes": class_table,
        "partition_check": {"sum_of_class_areas_m2": round(total_check, 6), "extent_area_m2": round(extent_area, 6),
                            "relative_error": relative_error, "within_1e-6": relative_error <= AREA_RELATIVE_TOLERANCE},
        "top_unclassified_regions": top_unclassified,
        "rendering_omission_check": rendering_omission_check,
        "ground_cover_omission_check": ground_cover_omission_check(
            inputs.environment, drawn_set, inputs.road["displayed_surface_sha256"]),
        "source_supported_ground_cover_candidates": {
            "count": len(source_ground_covers),
            "counts_by_kind": dict(sorted(source_ground_cover_counts.items())),
            "ids": sorted(feature["groundCoverId"] for feature in source_ground_covers),
            "note": "Candidate source polygons only; viewer clipping and drawn-set measurement are separate.",
        },
        "classifier_assumptions": {
            "water_tags": {"natural": sorted(WATER_NATURAL_VALUES), "waterway": sorted(WATER_WATERWAY_VALUES),
                          "landuse": sorted(WATER_LANDUSE_VALUES)},
            "water_measured": False,
            "water_note": "No natural=water/waterway/reservoir element exists in the current default-scene source; "
                          "these tag sets are an explicit, documented assumption, not a measured class.",
            "plaza_tags": {"highway": sorted(PLAZA_HIGHWAY_VALUES), "place": sorted(PLAZA_PLACE_VALUES)},
            "plaza_measured": False,
            "plaza_note": "The source has highway=pedestrian ways, but all are tagged polyline geometry (named "
                         "streets), not polygon areas; no plaza polygon exists in the current source.",
            "excluded_landuse_tags": sorted(f"{k}={v}" for k, v in EXCLUDED_LANDUSE_TAGS),
        },
    }


def render_class_map(output_path: Path, extent: Polygon, classes: dict[str, dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from shapely.plotting import plot_polygon

    fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
    minx, miny, maxx, maxy = extent.bounds
    ax.set_facecolor(CLASS_COLORS["unclassified"])
    for class_name in [*CLASS_ORDER, "unclassified"]:
        geometry = classes[class_name]["geometry"]
        if geometry.is_empty:
            continue
        color = CLASS_COLORS[class_name]
        for part in connected_regions(geometry):
            plot_polygon(part, ax=ax, add_points=False, facecolor=color, edgecolor=color, linewidth=0.1)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("south (m)")
    ax.set_title("City ground classification (overhead)")
    legend = [Patch(facecolor=CLASS_COLORS[name], label=name) for name in [*CLASS_ORDER, "unclassified"]]
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)


def classify_scene(scene_path: Path, drawn_set: dict | None = None) -> tuple[dict, dict, Polygon]:
    inputs = ResolvedInputs(scene_path)
    extent = scene_extent(inputs.pack_manifest)

    class_features: dict[str, list[dict]] = {
        "roadbed": road_class_features(inputs.road, "roadbed"),
        "walkbed": road_class_features(inputs.road, "walkbed"),
        "building": building_class_features(inputs.environment),
    }
    greens = green_class_features(inputs.environment)
    class_features["green"] = greens
    tagged_areas = extract_tagged_areas(inputs.osm, inputs.environment["source"]["origin"])
    class_features.update(tagged_class_features(tagged_areas))

    classes = partition_ground(extent, class_features)
    report = build_report(relpath(scene_path), inputs, extent, classes, greens, drawn_set)
    return report, classes, extent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-name", default="ground-classification-report.json")
    parser.add_argument("--map-name", default="ground-classification-map.png")
    parser.add_argument("--drawn-set", type=Path,
                        help="Measured viewer output from measureCityGroundCoverDrawnSet")
    args = parser.parse_args()

    drawn_set = json.loads(args.drawn_set.read_text()) if args.drawn_set is not None else None
    report, classes, extent = classify_scene(args.scene, drawn_set)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / args.report_name
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    render_class_map(args.output_dir / args.map_name, extent, classes)
    print(json.dumps({"report": str(report_path), "map": str(args.output_dir / args.map_name),
                      "partition_check": report["partition_check"]}, indent=2))


if __name__ == "__main__":
    main()
