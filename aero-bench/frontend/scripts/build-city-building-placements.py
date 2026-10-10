#!/usr/bin/env python3
"""Fit oriented visual collision boxes inside the verified OSM building roofs."""

from __future__ import annotations

import hashlib
import json
import math
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely import affinity
from shapely.geometry import MultiPoint, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

from city_surface_identity import displayed_surface_sha256
from city_preview_scene import read_scene_paths

def packed_triangles(manifest: dict, pack_dir: Path):
    for batch in manifest["batches"]:
        if batch["layer"] not in ("buildings", "roads"):
            continue
        raw = (pack_dir / "assets" / batch["file"]["sha256"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != batch["file"]["sha256"]:
            raise ValueError("Mesh pack chunk digest changed")
        count = batch["vertices"]
        positions = np.frombuffer(raw, dtype="<f4", count=count * 3).reshape(count, 3)
        indices = np.frombuffer(raw, dtype="<u4", count=batch["indices"], offset=count * 32).reshape(-1, 3)
        first = 0
        for item in batch["ranges"]:
            yield batch, item["target"], positions[indices[first:item["end"]]]
            first = item["end"]


def building_geometry(manifest: dict, pack_dir: Path):
    roofs: dict[str, list[Polygon]] = defaultdict(list)
    heights: dict[str, list[float]] = defaultdict(list)
    vertical_surface_area: dict[str, float] = defaultdict(float)
    source_plan_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    roads: list[Polygon] = []
    for batch, target, triangles in packed_triangles(manifest, pack_dir):
        if batch["layer"] == "buildings" and target and target["kind"] == "building":
            building_id = target["id"]
            heights[building_id].extend((float(triangles[:, :, 1].min()), float(triangles[:, :, 1].max())))
            source_plan_points[building_id].extend(
                (float(x), float(z)) for x, z in triangles[:, :, (0, 2)].reshape(-1, 2)
            )
            edge_a = triangles[:, 1] - triangles[:, 0]
            edge_b = triangles[:, 2] - triangles[:, 0]
            cross = np.cross(edge_a, edge_b)
            twice_area = np.linalg.norm(cross, axis=1)
            vertical = np.abs(cross[:, 1]) <= twice_area * 0.2
            vertical_surface_area[building_id] += float(twice_area[vertical].sum() / 2)
            roof_material = "/Roofing" in (batch["material"]["base_color_texture"] or "")
            for vertices in triangles:
                if float(np.ptp(vertices[:, 1])) > 1e-4 and not roof_material:
                    continue
                footprint = Polygon([(float(x), float(z)) for x, _, z in vertices])
                if footprint.area > 1e-5:
                    roofs[building_id].append(footprint)
        elif batch["layer"] == "roads" and any(
            name in (batch["material"]["base_color_texture"] or "")
            for name in ("/Asphalt", "/Concrete", "/Paving")
        ):
            for vertices in triangles:
                footprint = Polygon([(float(x), float(z)) for x, _, z in vertices])
                if footprint.area > 1e-5:
                    roads.append(footprint)
    if set(roofs) != set(heights):
        raise ValueError(f"Buildings have no horizontal roof footprint: {sorted(set(heights) - set(roofs))}")
    return ({key: unary_union(value) for key, value in roofs.items()}, heights,
            unary_union(roads), vertical_surface_area, source_plan_points)


def source_collision_rectangle(points: list[tuple[float, float]], building_id: str,
                               base_y: float, height: float) -> dict:
    """A minimum-area oriented envelope around every indexed source vertex."""
    if len(points) < 3:
        raise ValueError(f"{building_id}: verified source mesh has fewer than three plan points")
    rectangle = MultiPoint(points).convex_hull.minimum_rotated_rectangle
    if rectangle.geom_type != "Polygon" or rectangle.area <= 0:
        raise ValueError(f"{building_id}: verified source mesh has a degenerate collision envelope")
    corners = list(rectangle.exterior.coords)
    edges = [(corners[index + 1][0] - corners[index][0],
              corners[index + 1][1] - corners[index][1]) for index in range(4)]
    dx, dz = max(edges, key=lambda edge: edge[0] ** 2 + edge[1] ** 2)
    width = math.hypot(dx, dz)
    depth = rectangle.area / width if width > 0 else 0
    if width <= 0 or depth <= 0:
        raise ValueError(f"{building_id}: verified source mesh has a degenerate collision envelope")
    center_x = sum(point[0] for point in corners[:4]) / 4
    center_z = sum(point[1] for point in corners[:4]) / 4
    return {"building_id": building_id, "part": 0, "base_y": round(base_y, 3),
            "height": round(height, 3), "x": round(center_x, 3), "z": round(center_z, 3),
            "width": round(width + 0.02, 3), "depth": round(depth + 0.02, 3),
            "rotation_deg": round(-math.degrees(math.atan2(dz, dx)), 3)}


def displayed_street_surface(paths, pack: dict, manifest_bytes: bytes):
    """Read the exact roadbed and walkbed polygons rendered by this scene."""
    road_data = json.loads(paths.road.read_text(encoding="utf-8"))
    traffic = json.loads(paths.traffic.read_text(encoding="utf-8"))
    placement_sha = hashlib.sha256(paths.placement.read_bytes()).hexdigest()
    if road_data.get("schema_version") != "aero-bench.city-road-preview/v2" \
            or traffic.get("schema_version") != "aero-bench.city-sumo-preview/v1" \
            or road_data.get("mesh_pack_source_sha256") != pack["source"]["sha256"] \
            or road_data.get("source_network_sha256") != traffic.get("source_network_sha256") \
            or road_data.get("source_osm_sha256") != traffic.get("source_osm_sha256") \
            or road_data.get("building_placement_sha256") != placement_sha \
            or road_data.get("displayed_surface_sha256") != displayed_surface_sha256(
                road_data.get("roadbed"), road_data.get("walkbed")) \
            or road_data.get("traffic_recorded_building_placement_sha256") \
            != traffic.get("building_placement_sha256"):
        raise ValueError("Displayed road/walk surfaces differ from the verified scene inputs")
    polygons = []
    for field in ("roadbed", "walkbed"):
        entries = road_data.get(field)
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"Displayed scene is missing rendered {field} polygons")
        for index, item in enumerate(entries):
            polygon = Polygon(item["outline"], item["holes"])
            if not polygon.is_valid or polygon.area <= 0:
                raise ValueError(f"Displayed {field} polygon {index} is invalid")
            polygons.append(polygon)
    # The mesh source identity is also bound to the manifest bytes used to build
    # this placement file, so stale network surfaces fail before geometry work.
    if json.loads(manifest_bytes)["source"]["sha256"] != road_data["mesh_pack_source_sha256"]:
        raise ValueError("Displayed road/walk surfaces reference a different OSM mesh pack")
    return unary_union(polygons), traffic, road_data


def dominant_angle(polygon) -> float:
    corners = list(polygon.minimum_rotated_rectangle.exterior.coords)
    edges = [(corners[index + 1][0] - corners[index][0],
              corners[index + 1][1] - corners[index][1]) for index in range(4)]
    dx, dz = max(edges, key=lambda edge: edge[0] ** 2 + edge[1] ** 2)
    angle = math.degrees(math.atan2(dz, dx))
    return (angle + 90) % 180 - 90


def largest_grid_rectangle(inside: list[list[bool]]) -> tuple[int, int, int, int] | None:
    if not inside:
        return None
    columns = len(inside[0])
    heights = [0] * columns
    best = None
    best_area = 0
    for row, occupied in enumerate(inside):
        for column, valid in enumerate(occupied):
            heights[column] = heights[column] + 1 if valid else 0
        stack: list[int] = []
        for column in range(columns + 1):
            height = heights[column] if column < columns else 0
            while stack and heights[stack[-1]] > height:
                previous = stack.pop()
                left = stack[-1] + 1 if stack else 0
                area = heights[previous] * (column - left)
                if area > best_area:
                    best_area = area
                    best = (left, row + 1 - heights[previous], column, row + 1)
            stack.append(column)
    return best


def placed_rectangles(footprint, occupied, road_clearance):
    safe = footprint.buffer(-0.18, join_style=2)
    if safe.is_empty:
        safe = footprint
    if road_clearance.intersects(safe):
        safe = safe.difference(road_clearance)
    blockers = [prior.buffer(0.08) for prior in occupied if prior.intersects(safe)]
    if blockers:
        safe = safe.difference(unary_union(blockers))
    if safe.is_empty:
        return []
    angle = dominant_angle(footprint)
    rotated = affinity.rotate(safe, -angle, origin=(0, 0))
    minx, minz, maxx, maxz = rotated.bounds
    width, depth = maxx - minx, maxz - minz
    cell = min(0.8, max(0.2, min(width, depth) / 8))
    cell = max(cell, math.sqrt(width * depth / 80_000))
    columns = math.ceil(width / cell)
    rows = math.ceil(depth / cell)
    prepared = prep(rotated)
    inside = [
        [prepared.covers(box(minx + x * cell, minz + z * cell,
                             minx + (x + 1) * cell, minz + (z + 1) * cell))
         for x in range(columns)]
        for z in range(rows)
    ]
    rectangles = []
    first_area = 0
    while len(rectangles) < 4:
        found = largest_grid_rectangle(inside)
        if found is None:
            break
        left, bottom, right, top = found
        area = (right - left) * (top - bottom) * cell * cell
        if rectangles and (area < max(12, first_area * 0.16)
                           or sum(part["area"] for part in rectangles) >= safe.area * 0.78):
            break
        first_area = first_area or area
        local_x = minx + (left + right) * cell / 2
        local_z = minz + (bottom + top) * cell / 2
        radians = math.radians(angle)
        center_x = local_x * math.cos(radians) - local_z * math.sin(radians)
        center_z = local_x * math.sin(radians) + local_z * math.cos(radians)
        rectangle = box(minx + left * cell, minz + bottom * cell,
                        minx + right * cell, minz + top * cell)
        if not rotated.covers(rectangle):
            raise ValueError("Computed building collision box exceeds its OSM footprint")
        rectangles.append({"x": round(center_x, 3), "z": round(center_z, 3),
                           "width": round((right - left) * cell - 0.04, 3),
                           "depth": round((top - bottom) * cell - 0.04, 3),
                           "rotation_deg": round(-angle, 3), "area": area})
        for row in range(bottom, top):
            inside[row][left:right] = [False] * (right - left)
    return rectangles


def placed_polygon(item):
    rectangle = box(-item["width"] / 2, -item["depth"] / 2,
                    item["width"] / 2, item["depth"] / 2)
    return affinity.translate(affinity.rotate(rectangle, -item["rotation_deg"], origin=(0, 0)),
                              xoff=item["x"], yoff=item["z"])


def clipped_building_placements(footprints, heights, road_surface, occlusion_audit=None):
    """Keep the original fallback-placement algorithm and geometry unchanged."""
    road_clearance = road_surface.buffer(0.12)
    placements = []
    areas = {}
    occupied = []
    occluded = []
    inferred_ground_base = []
    ordered_buildings = sorted(footprints, key=lambda key: (
        -(max(heights[key]) - min(heights[key])), -footprints[key].area, key))
    for building_id in ordered_buildings:
        footprint = footprints[building_id]
        try:
            rectangles = placed_rectangles(footprint, occupied, road_clearance)
        except ValueError as error:
            raise ValueError(f"{building_id}: {error}; area={footprint.area:.2f} m²") from error
        if not rectangles:
            if occlusion_audit is not None:
                usable = footprint.buffer(-0.18, join_style=2)
                if usable.is_empty:
                    usable = footprint
                after_road = usable.difference(road_clearance)
                blockers = [prior.buffer(0.08) for prior in occupied if prior.intersects(after_road)]
                after_buildings = after_road.difference(unary_union(blockers)) if blockers else after_road
                reason = ("road_clearance_covers_usable_roof" if after_road.area <= 1e-6
                          else "previous_buildings_cover_usable_roof" if after_buildings.area <= 1e-6
                          else "remaining_polygon_has_no_inscribed_rectangle")
                occlusion_audit.append({
                    "building_id": building_id,
                    "source_roof_area_m2": round(footprint.area, 3),
                    "usable_roof_area_m2": round(usable.area, 3),
                    "road_clearance_overlap_m2": round(usable.intersection(road_clearance).area, 3),
                    "remaining_after_road_m2": round(after_road.area, 3),
                    "occupied_visual_part_count": len(blockers),
                    "remaining_after_buildings_m2": round(after_buildings.area, 3),
                    "reason": reason,
                })
            occluded.append(building_id)
            continue
        areas[building_id] = (footprint.area, sum(item["area"] for item in rectangles))
        base_y, top_y = min(heights[building_id]), max(heights[building_id])
        if base_y > 1 and top_y - base_y < 1:
            # This source mesh contains only a roof slab; the presentation ground is y=0.
            inferred_ground_base.append(building_id)
            base_y = 0.0
        for part, rectangle in enumerate(rectangles):
            rectangle.pop("area")
            occupied.append(placed_polygon(rectangle))
            placements.append({"building_id": building_id, "part": part, "base_y": round(base_y, 3),
                               "height": round(top_y - base_y, 3), **rectangle})
    for placement in placements:
        visual = placed_polygon(placement)
        if not footprints[placement["building_id"]].buffer(0.02).covers(visual):
            raise ValueError(f"Rounded visual box exceeds the OSM roof: {placement['building_id']}")
        if visual.intersection(road_surface).area > 0.01:
            raise ValueError(f"Visual box overlaps the verified road surface: {placement['building_id']}")
    return placements, areas, occluded, inferred_ground_base


def write_placement_payload(paths, destination: Path, payload: dict) -> bytes:
    placement_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(placement_bytes)
    if destination.resolve() == paths.placement.resolve():
        # The road surface binds the current placement and source triangles only.
        # SUMO and flight provenance refer to their original, separately recorded input.
        road_data = json.loads(paths.road.read_text(encoding="utf-8"))
        road_data["building_placement_sha256"] = hashlib.sha256(placement_bytes).hexdigest()
        road_data["displayed_surface_sha256"] = displayed_surface_sha256(
            road_data.get("roadbed"), road_data.get("walkbed"))
        paths.road.write_text(json.dumps(road_data, ensure_ascii=False, separators=(",", ":")),
                              encoding="utf-8")
    return placement_bytes


def build_placement_payload(manifest: dict, manifest_bytes: bytes, pack_dir: Path, road_data: dict,
                            rendered_street_surface, *,
                            enforce_displayed_clearance: bool = False) -> dict:
    """Derive source-footprint placements without writing a scene or changing a road."""
    rendered_street_clearance = rendered_street_surface.buffer(0.12)
    footprints, heights, road_surface, vertical_surface_area, source_plan_points = building_geometry(manifest, pack_dir)
    pack_paving_clearance = road_surface.buffer(0.12)
    occlusion_audit = [] if enforce_displayed_clearance else None
    placements, clipped_areas, occluded, inferred_ground_base = clipped_building_placements(
        footprints, heights, rendered_street_surface if enforce_displayed_clearance else road_surface,
        occlusion_audit)
    if occlusion_audit is not None:
        for detail in occlusion_audit:
            footprint = footprints[detail["building_id"]]
            detail["source_pack_road_overlap_m2"] = round(footprint.intersection(road_surface).area, 3)
            detail["displayed_street_overlap_m2"] = round(
                footprint.intersection(rendered_street_surface).area, 3)
    placed_ids = {placement["building_id"] for placement in placements}
    candidate_envelopes = {}
    deferred_source_surface_overlap = 0
    deferred_pack_paving_overlap = 0
    deferred_envelope_displayed_surface_overlap = 0
    deferred_envelope_pack_paving_overlap = 0
    deferred_competing_footprint = 0
    deferred_envelope_competing_footprint = 0
    deferred_competing_envelopes = 0
    deferred_incomplete_source = 0
    for building_id, footprint in footprints.items():
        if building_id not in placed_ids:
            continue
        base_y, top_y = min(heights[building_id]), max(heights[building_id])
        height = top_y - base_y
        expected_wall_area = footprint.length * height
        has_source_walls = height >= 2.5 and expected_wall_area > 0 \
            and vertical_surface_area[building_id] >= max(10.0, expected_wall_area * 0.3)
        if not has_source_walls:
            deferred_incomplete_source += 1
            continue
        surface_intersection = footprint.intersection(rendered_street_clearance).area
        if surface_intersection > 0.001:
            deferred_source_surface_overlap += 1
            continue
        if footprint.intersection(pack_paving_clearance).area > 0.001:
            deferred_pack_paving_overlap += 1
            if not enforce_displayed_clearance:
                continue
        competing_intersection = sum(
            footprint.intersection(other).area
            for other_id, other in footprints.items() if other_id != building_id
            and footprint.intersects(other)
        )
        if competing_intersection > 0.001:
            deferred_competing_footprint += 1
            continue
        envelope = source_collision_rectangle(source_plan_points[building_id], building_id, base_y, height)
        envelope_polygon = placed_polygon(envelope)
        if envelope_polygon.intersection(rendered_street_clearance).area > 0.001:
            deferred_envelope_displayed_surface_overlap += 1
            continue
        if envelope_polygon.intersection(pack_paving_clearance).area > 0.001:
            deferred_envelope_pack_paving_overlap += 1
            if not enforce_displayed_clearance:
                continue
        envelope_competing_area = sum(
            envelope_polygon.intersection(other).area
            for other_id, other in footprints.items() if other_id != building_id
            and envelope_polygon.intersects(other)
        )
        if envelope_competing_area > 0.001:
            deferred_envelope_competing_footprint += 1
            continue
        candidate_envelopes[building_id] = (envelope, envelope_polygon)
    # Projected OBBs can overlap in courtyards even when neither intersects the
    # other's footprint. Keep the first stable source id and defer later boxes.
    complete_envelopes = {}
    accepted_envelopes = []
    for building_id in sorted(candidate_envelopes):
        envelope, envelope_polygon = candidate_envelopes[building_id]
        if any(envelope_polygon.intersection(prior).area > 0.001 for prior in accepted_envelopes):
            deferred_competing_envelopes += 1
            continue
        complete_envelopes[building_id] = envelope
        accepted_envelopes.append(envelope_polygon)
    complete_footprints = sorted(complete_envelopes)
    complete_footprint_ids = set(complete_footprints)
    source_roof_union = unary_union(list(footprints.values()))
    covered_roofs = [footprints[key] for key in complete_footprint_ids]
    rendered_fallbacks = [item for item in placements if item["building_id"] not in complete_footprint_ids]
    covered_roofs.extend(placed_polygon(item) for item in rendered_fallbacks)
    covered_roof_union = unary_union(covered_roofs)
    covered_area = covered_roof_union.area
    source_roof_area = source_roof_union.area
    runtime_coverage_ratios = [
        1.0 if building_id in complete_footprint_ids
        else clipped_areas[building_id][1] / clipped_areas[building_id][0]
        for building_id in placed_ids
    ]
    payload = {
        "schema_version": "aero-bench.city-building-placement/v1",
        "mesh_pack_source_sha256": manifest["source"]["sha256"],
        "mesh_pack_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_kind": "verified-source-surfaces-and-inscribed-rectangles",
        "displayed_surface_sha256": displayed_surface_sha256(road_data["roadbed"], road_data["walkbed"]),
        "complete_footprint_buildings": sorted(complete_footprint_ids),
        "complete_footprint_envelopes": [complete_envelopes[key] for key in sorted(complete_envelopes)],
        "occluded_buildings": occluded,
        "inferred_ground_base_buildings": inferred_ground_base,
        "audit": {"building_count": len(footprints), "visible_buildings": len(footprints) - len(occluded),
                  "visual_parts": len(rendered_fallbacks) + len(complete_footprints),
                  "osm_footprint_area_m2": round(source_roof_area, 2),
                  "osm_roof_area_sum_m2": round(sum(footprint.area for footprint in footprints.values()), 2),
                  "covered_area_m2": round(covered_area, 2),
                  "coverage_ratio": round(covered_area / source_roof_area, 3) if source_roof_area > 0 else 0,
                  "uncovered_roof_area_m2": round(source_roof_union.difference(covered_roof_union).area, 2),
                  "min_coverage_ratio": round(min(runtime_coverage_ratios), 3),
                  "source_street_surface_overlap_buildings": sum(footprint.intersection(road_surface).area > 0.1
                                                                for footprint in footprints.values()),
                  "displayed_street_surface_overlap_buildings": sum(
                      footprint.intersection(rendered_street_surface).area > 0.1
                      for footprint in footprints.values()),
                  "complete_footprint_buildings": len(complete_footprint_ids),
                  "complete_footprint_area_m2": round(sum(footprints[key].area for key in complete_footprint_ids), 2),
                  "complete_envelope_area_m2": round(sum(complete_envelopes[key]["width"]
                                                           * complete_envelopes[key]["depth"]
                                                           for key in complete_footprint_ids), 2),
                  "complete_envelope_phantom_area_m2": round(sum(
                      complete_envelopes[key]["width"] * complete_envelopes[key]["depth"]
                      - footprints[key].area for key in complete_footprint_ids), 2),
                  "deferred_source_surface_overlap_buildings": deferred_source_surface_overlap,
                  "deferred_pack_paving_overlap_buildings": deferred_pack_paving_overlap,
                  "deferred_envelope_displayed_surface_overlap_buildings": deferred_envelope_displayed_surface_overlap,
                  "deferred_envelope_pack_paving_overlap_buildings": deferred_envelope_pack_paving_overlap,
                  "deferred_competing_footprint_buildings": deferred_competing_footprint,
                  "deferred_envelope_competing_footprint_buildings": deferred_envelope_competing_footprint,
                  "deferred_competing_envelopes": deferred_competing_envelopes,
                  "deferred_incomplete_source_buildings": deferred_incomplete_source},
        "placements": placements,
    }
    if occlusion_audit is not None:
        payload["audit"]["occluded_building_details"] = occlusion_audit
        payload["audit"]["hidden_source_road_conflicts"] = [
            {"building_id": key,
             "source_roof_area_m2": round(footprints[key].area, 3),
             "source_pack_road_overlap_m2": round(footprints[key].intersection(road_surface).area, 3),
             "displayed_street_overlap_m2": round(
                 footprints[key].intersection(rendered_street_surface).area, 3)}
            for key in sorted(footprints)
            if footprints[key].intersection(road_surface).area > 0.1
        ]
    if not payload["complete_footprint_buildings"] and not payload["placements"]:
        raise ValueError("Selected pack contains no visible buildings")
    return payload


def main(scene_path: Path, output: Path | None = None) -> None:
    paths = read_scene_paths(scene_path)
    destination = output or paths.placement
    manifest_bytes = paths.pack.read_bytes()
    manifest = json.loads(manifest_bytes)
    rendered_street_surface, _traffic, road_data = displayed_street_surface(paths, manifest, manifest_bytes)
    payload = build_placement_payload(manifest, manifest_bytes, paths.pack.parent, road_data,
                                      rendered_street_surface)
    write_placement_payload(paths, destination, payload)
    print(json.dumps(payload["audit"], indent=2))
    print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Override the scene's building placement output")
    args = parser.parse_args()
    main(args.scene, args.output)
