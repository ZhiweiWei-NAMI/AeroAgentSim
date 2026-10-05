#!/usr/bin/env python3
"""Derive a viewer source summary from declared source files, never from render output.

All arguments are explicit. This summary binds the read-only viewer to source
objects and canonical GLBs; formal asset identity and Run ID remain upstream.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

SOURCE_SCHEMA = "aero-bench.building-render-source-context/v1"
ORIGIN_KEYS = ("latitude_deg", "longitude_deg", "ellipsoid_height_m", "amsl_m", "geoid_undulation_m")
PLACEMENT = ("ENU_east = anchor_east_m + X; ENU_north = anchor_north_m - Z; "
             "ENU_up = base_enu_up_m + Y")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> tuple[dict, str, bytes]:
    raw = path.read_bytes()
    data = json.loads(raw)
    require(isinstance(data, dict), f"{path}: root must be an object")
    return data, hashlib.sha256(raw).hexdigest(), raw


def indexed(entries: list[dict], label: str) -> dict[str, dict]:
    require(isinstance(entries, list) and bool(entries), f"{label}: entries are missing")
    result = {entry["object_id"]: entry for entry in entries}
    require(len(result) == len(entries), f"{label}: duplicate object ids")
    return result


def origin(value: dict) -> dict:
    result = {key: value[key] for key in ORIGIN_KEYS}
    require(all(isinstance(number, (int, float)) and not isinstance(number, bool)
                and math.isfinite(number) for number in result.values()), "origin must use finite numbers")
    require(abs(result["latitude_deg"]) < 90 and abs(result["longitude_deg"]) <= 180,
            "origin is outside geographic bounds")
    require(abs(result["ellipsoid_height_m"] - result["amsl_m"] - result["geoid_undulation_m"]) <= 1e-3,
            "origin vertical relation is inconsistent")
    return result


def build_context(*, scene_id: str, objects_path: Path, scaleout_path: Path, fragment_path: Path,
                  pack_path: Path, mesh_source_path: Path, canonical_assets: Path) -> dict:
    require(bool(re.fullmatch(r"[a-zA-Z0-9_-]+", scene_id)), "scene id is invalid")
    objects, objects_sha, _ = read_json(objects_path)
    scaleout, scaleout_sha, _ = read_json(scaleout_path)
    fragment, fragment_sha, _ = read_json(fragment_path)
    pack, pack_sha, _ = read_json(pack_path)
    _, mesh_source_sha, mesh_source_raw = read_json(mesh_source_path)
    require(objects["schema_version"] == "aero-bench.urban-scene-objects/v1"
            and objects["coordinate_frame"] == "ENU", "objects schema or coordinate frame mismatch")
    require(scaleout["schema"] == "aero-bench.building-render-scaleout/v0", "unsupported canonical source schema")
    require(fragment["schema_version"] == "aero-bench.urban-world-authoring/v1", "unsupported catalog schema")
    require(pack["schema_version"] == "aero-bench.osm2world-mesh-pack/v1", "unsupported mesh pack schema")
    require(pack["source"] == {"sha256": mesh_source_sha, "size_bytes": len(mesh_source_raw)},
            "mesh pack source is not the supplied verified source bytes")
    source_origin = origin(objects["origin"])
    require(origin(scaleout["scene"]["origin_wgs84"]) == source_origin, "scaleout origin mismatch")
    require(all(pack["projection"]["origin"][key] == source_origin[key]
                for key in ("latitude_deg", "longitude_deg")), "mesh pack origin mismatch")
    require(scaleout["scene"]["objects_json_sha256"] == objects_sha, "scaleout objects digest mismatch")
    source_objects = indexed(objects["buildings"], "objects")
    canonical_entries = indexed(scaleout["buildings"], "scaleout")
    fragment_entries = indexed(fragment["building_render"], "catalog")
    require(set(source_objects) == set(canonical_entries) == set(fragment_entries)
            == set(fragment["glb_digests"]), "source object inventories differ")
    require(scaleout["scene"]["building_count"] == len(source_objects), "scaleout building count mismatch")
    # Reuse the current canonical container/accessor verifier, without its dataset-specific main().
    verifier_path = Path(__file__).with_name("build-building-render-runtime.py")
    spec = importlib.util.spec_from_file_location("building_source_glb_verifier", verifier_path)
    require(spec is not None and spec.loader is not None, "canonical GLB verifier is unavailable")
    verifier = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = verifier
    spec.loader.exec_module(verifier)
    targets = {item["target"]["id"] for batch in pack["batches"] for item in batch["ranges"]
               if item["target"] is not None and item["target"]["kind"] == "building"}
    # The pack serializes object metadata with JS Number spelling while its
    # triangle targets retain the exact represented integer. Use that declared
    # Number representation, as the canonical renderer's pack linkage does.
    normalized_objects = [(item["id"][0] + str(int(float(item["id"][1:]))), item["tags"])
                          for item in pack["objects"] if item["id"][0] in ("w", "r")]
    require(len({key for key, _ in normalized_objects}) == len(normalized_objects),
            "pack object ids collide in the target representation")
    pack_objects = dict(normalized_objects)
    pack_objects = {key: tags for key, tags in pack_objects.items() if key in targets}
    require(set(pack_objects) == targets, "pack target object metadata is incomplete")
    combined = hashlib.sha256()
    buildings = []
    for object_id in sorted(source_objects):
        source = source_objects[object_id]
        canonical = canonical_entries[object_id]
        require(canonical["glb"] == f"assets/{object_id}.glb", f"{object_id}: canonical file declaration mismatch")
        path = canonical_assets / Path(canonical["glb"]).name
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        require(sha == canonical["glb_sha256"] and len(raw) == canonical["glb_bytes"], f"{object_id}: canonical digest/bytes mismatch")
        require(fragment["glb_digests"][object_id] == {"sha256": sha, "bytes": len(raw), "style_id": canonical["style_id"]},
                f"{object_id}: catalog canonical digest/bytes/style mismatch")
        require(fragment_entries[object_id]["file"] == canonical["glb"]
                and fragment_entries[object_id]["source_frame"] == "asset_local", f"{object_id}: catalog source declaration mismatch")
        document, _ = verifier.read_glb(path)
        verifier.validate_accessors(document, object_id)
        primitives = [primitive for mesh in document["meshes"] for primitive in mesh["primitives"]]
        position_accessors = {primitive["attributes"]["POSITION"] for primitive in primitives}
        geometry = {"vertices": sum(document["accessors"][index]["count"] for index in position_accessors),
                    "triangles": sum(document["accessors"][primitive["indices"]]["count"] // 3 for primitive in primitives)}
        extras = document["asset"]["extras"]
        require(extras["object_id"] == object_id and extras["source_frame"] == "asset_local", f"{object_id}: GLB source identity mismatch")
        require(extras["scene"]["objects_json_sha256"] == objects_sha
                and origin(extras["scene"]["origin_wgs84"]) == source_origin, f"{object_id}: GLB source scene mismatch")
        frame = extras["frame"]
        require(frame["placement"] == PLACEMENT and canonical["source_frame"] == "asset_local", f"{object_id}: placement convention mismatch")
        require(frame["anchor_east_m"] == canonical["anchor_east_m"]
                and frame["anchor_north_m"] == canonical["anchor_north_m"]
                and frame["base_enu_up_m"] == source["base_enu_up_m"], f"{object_id}: GLB placement mismatch")
        height = source["extrusion_height_m"]
        require(height == canonical["height_m"] == extras["building"]["height_m"]
                and extras["style"]["style_id"] == canonical["style_id"], f"{object_id}: GLB height/style mismatch")
        footprint = source["footprint_enu_m"]
        osm = source["osm"]
        component = object_id.rsplit(".component.", 1)[1]
        linked = sorted(key for key, tags in pack_objects.items()
                        if tags.get("aero_bench:source_type") == osm["type"]
                        and tags.get("aero_bench:source_id") == str(osm["id"])
                        and tags.get("aero_bench:component_index", "0") == component)
        buildings.append({
            "object_id": object_id, "osm": osm,
            "glb": {"path": f"assets/{sha}", "sha256": sha, "bytes": len(raw)},
            "style_id": canonical["style_id"], "anchor_east_m": frame["anchor_east_m"],
            "anchor_north_m": frame["anchor_north_m"], "base_enu_up_m": source["base_enu_up_m"], "height_m": height,
            "envelope": {"min_e": min(point[0] for point in footprint), "max_e": max(point[0] for point in footprint),
                         "min_n": min(point[1] for point in footprint), "max_n": max(point[1] for point in footprint),
                         "base_up": source["base_enu_up_m"], "top_up": source["top_enu_up_m"]},
            "pack_target_ids": linked,
            "geometry": geometry,
        })
        combined.update(path.name.encode("utf-8"))
        combined.update(raw)
    claimed = [target for building in buildings for target in building["pack_target_ids"]]
    require(len(set(claimed)) == len(claimed) and set(claimed) == targets, "canonical source does not cover pack targets exactly")
    require(scaleout["totals"]["glb_count"] == len(buildings)
            and scaleout["totals"]["glb_bytes"] == sum(entry["glb"]["bytes"] for entry in buildings)
            and scaleout["totals"]["styles_used"] == len({entry["style_id"] for entry in buildings})
            and scaleout["totals"]["exceptions"] == 0, "scaleout totals disagree with the verified canonical assets")
    return {
        "schema_version": SOURCE_SCHEMA,
        "scene": {"id": scene_id, "coordinate_frame": "ENU", "origin_wgs84": source_origin,
                  "objects_json_sha256": objects_sha, "placement_rule": PLACEMENT,
                  "mesh_pack_manifest_sha256": pack_sha, "mesh_pack_source_sha256": mesh_source_sha},
        "sources": {"scaleout_glb_combined_sha256": combined.hexdigest(), "scaleout_manifest_sha256": scaleout_sha,
                    "catalog_fragment_sha256": fragment_sha},
        "buildings": buildings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-id", required=True)
    for key in ("objects", "scaleout-manifest", "catalog-fragment", "mesh-pack", "mesh-source", "canonical-assets", "output"):
        parser.add_argument(f"--{key}", type=Path, required=True)
    args = parser.parse_args()
    result = build_context(scene_id=args.scene_id, objects_path=args.objects, scaleout_path=args.scaleout_manifest,
                           fragment_path=args.catalog_fragment, pack_path=args.mesh_pack, mesh_source_path=args.mesh_source,
                           canonical_assets=args.canonical_assets)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    print(json.dumps({"path": str(args.output), "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                      "buildings": len(result["buildings"])}))


if __name__ == "__main__":
    main()
