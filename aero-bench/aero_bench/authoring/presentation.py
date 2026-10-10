"""Finalize a selected city's verified static road and building presentation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union

from aero_bench.authoring.selection import SceneSelection
from aero_bench.authoring.publication_evidence import verify_ground_network_closure
from aero_bench.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SCRIPT_DIR = ROOT / "frontend/scripts"
_PLACEMENT_BUILDER: ModuleType | None = None


@dataclass(frozen=True, slots=True)
class StaticPresentation:
    manifest_path: Path
    manifest_sha256: str
    manifest_size_bytes: int
    road_path: Path
    building_placement_path: Path
    signal_inventory_path: Path
    visual_assets_path: Path
    asset_sha256s: frozenset[str]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"{label} contains non-JSON number: {value}")), object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _read_ref(path: Path, sha256: str, label: str) -> bytes:
    if not isinstance(sha256, str) or _HEX.fullmatch(sha256) is None:
        raise ValueError(f"{label} has invalid SHA-256")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file")
    raw = path.read_bytes()
    if _sha(raw) != sha256:
        raise ValueError(f"{label} SHA-256 differs from its receipt")
    return raw


def _file_ref(raw: bytes) -> dict[str, int | str]:
    if not raw:
        raise ValueError("presentation asset must not be empty")
    return {"sha256": _sha(raw), "size_bytes": len(raw)}


def _tree_identity(directory: Path, root: Path) -> str:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"presentation asset tree is unavailable: {directory}")
    entries: list[dict[str, object]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"presentation asset tree contains a symlink: {path}")
        if path.is_file():
            entries.append({"path": path.relative_to(root).as_posix(), **_file_ref(path.read_bytes())})
    if not entries:
        raise ValueError(f"presentation asset tree is empty: {directory}")
    return _sha(canonical_json_bytes(entries))


_EXTRA_VISUAL_ASSETS = (
    "/models/incoming/urban-traffic/images/64d1d14365d8479458d03808ea6b95d5.webp",
    "/models/incoming/urban-traffic/images/f7a11eed4c9d2e047af8297ed9751e31.webp",
    "/models/incoming/furniture/glb/street_light_8.glb",
    "/models/incoming/furniture/glb/traffic_light_4.glb",
    "/models/incoming/furniture/glb/bus_stop_4.glb",
)


def _media_type(path: Path, raw: bytes) -> str:
    suffix = path.suffix.lower()
    if suffix == ".fbx" and raw.startswith(b"Kaydara FBX Binary  \x00\x1a\x00"):
        return "application/octet-stream"
    if suffix == ".glb" and raw[:4] == b"glTF" and len(raw) >= 12 \
            and int.from_bytes(raw[4:8], "little") == 2 \
            and int.from_bytes(raw[8:12], "little") == len(raw):
        if len(raw) < 20 or raw[16:20] != b"JSON":
            raise ValueError(f"GLB has no JSON chunk: {path}")
        json_size = int.from_bytes(raw[12:16], "little")
        if json_size <= 0 or 20 + json_size > len(raw):
            raise ValueError(f"GLB JSON chunk is truncated: {path}")
        document = _json(raw[20:20 + json_size].rstrip(b" \t\r\n"), str(path))
        if document.get("asset", {}).get("version") != "2.0":
            raise ValueError(f"GLB asset version is invalid: {path}")

        def check_uris(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "uri" and (not isinstance(child, str) or not child.startswith("data:")):
                        raise ValueError(f"GLB contains an external URI: {path}")
                    check_uris(child)
            elif isinstance(value, list):
                for child in value:
                    check_uris(child)

        check_uris(document)
        return "model/gltf-binary"
    if suffix == ".webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if suffix == ".png" and raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix == ".json":
        _json(raw, str(path))
        return "application/json"
    raise ValueError(f"visual asset content or extension is unsupported: {path}")


def _visual_asset_sources(public_root: Path) -> list[tuple[dict[str, object], bytes]]:
    bigcity = public_root / "models/bigcity"
    if not bigcity.is_dir() or bigcity.is_symlink():
        raise ValueError("BigCity asset library is unavailable")
    paths = list(sorted(bigcity.rglob("*"))) + [public_root / path.lstrip("/")
                                                 for path in _EXTRA_VISUAL_ASSETS]
    entries = []
    seen: set[str] = set()
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"visual asset library contains a symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"visual asset is missing: {path}")
        url = "/" + path.relative_to(public_root).as_posix()
        if url in seen:
            raise ValueError(f"duplicate visual asset path: {url}")
        seen.add(url)
        raw = path.read_bytes()
        ref = _file_ref(raw)
        entries.append(({"path": url, **ref, "media_type": _media_type(path, raw)}, raw))
    if not entries or not any(row["path"] == "/models/bigcity/manifest.json" for row, _ in entries):
        raise ValueError("BigCity asset manifest is missing")
    return sorted(entries, key=lambda item: item[0]["path"])


def _bigcity_tree_sha256(entries: list[tuple[dict[str, object], bytes]]) -> str:
    references = [{"path": row["path"], "sha256": row["sha256"],
                   "size_bytes": row["size_bytes"]}
                  for row, _ in entries if str(row["path"]).startswith("/models/bigcity/")]
    if not references:
        raise ValueError("BigCity asset tree is empty")
    return _sha(canonical_json_bytes(references))


def presentation_asset_identity(root: Path = ROOT) -> dict[str, str]:
    """Hash complete browser-visible style and BigCity libraries for job identity."""
    visual_sources = _visual_asset_sources(root / "frontend/public")
    return {
        "osm2world_style_tree_sha256": _tree_identity(root / "frontend/public/osm2world/style", root),
        "bigcity_library_tree_sha256": _bigcity_tree_sha256(visual_sources),
        "visual_assets_tree_sha256": _sha(canonical_json_bytes(
            [row for row, _ in visual_sources])),
    }


def _placement_builder() -> ModuleType:
    global _PLACEMENT_BUILDER
    if _PLACEMENT_BUILDER is None:
        path = _SCRIPT_DIR / "build-city-building-placements.py"
        spec = importlib.util.spec_from_file_location("aero_selected_building_placements", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("building placement builder is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(_SCRIPT_DIR))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        _PLACEMENT_BUILDER = module
    return _PLACEMENT_BUILDER


def _strict_signal_inventory(signal: dict[str, Any], network_sha: str, effective_sha: str) -> None:
    if set(signal) != {"schema_version", "source_network_sha256", "mesh_pack_source_sha256", "signals"}:
        raise ValueError("signal inventory fields differ from the static contract")
    if (signal["schema_version"] != "aero-bench.city-static-signal-inventory/v1"
            or signal["source_network_sha256"] != network_sha
            or signal["mesh_pack_source_sha256"] != effective_sha):
        raise ValueError("signal inventory identity differs from the selected city")
    if not isinstance(signal["signals"], list):
        raise ValueError("signal inventory signals must be an array")
    seen: set[str] = set()
    for item in signal["signals"]:
        if not isinstance(item, dict) or set(item) != {"id", "tls", "link", "x", "z", "heading"}:
            raise ValueError("signal inventory contains an invalid signal")
        if (not isinstance(item["id"], str) or not item["id"] or item["id"] in seen
                or not isinstance(item["tls"], str) or not item["tls"]
                or type(item["link"]) is not int or item["link"] < 0
                or any(type(item[field]) not in (int, float) or not math.isfinite(item[field])
                       for field in ("x", "z", "heading"))):
            raise ValueError("signal inventory contains invalid identity or geometry")
        seen.add(item["id"])


def _street_surface(road: dict[str, Any], effective_sha: str, pack_sha: str, builder: ModuleType):
    if road.get("schema_version") != "aero-bench.city-static-road-candidate/v1":
        raise ValueError("road is not a private static road candidate")
    if road.get("mesh_pack_source_sha256") != effective_sha or road.get("mesh_pack_manifest_sha256") != pack_sha:
        raise ValueError("road candidate pack identity differs from this job")
    if any("traffic" in key or "trajectory" in key for key in road):
        raise ValueError("static road candidate contains dynamic traffic fields")
    required_arrays = ("concrete_roadbed", "lanes", "crossings", "curb_edges",
                       "street_lamps", "junctions", "direction_guides")
    if any(not isinstance(road.get(field), list) for field in required_arrays):
        raise ValueError("static road candidate is missing rendered topology")
    layout = road.get("street_layout")
    if not isinstance(layout, dict) or not isinstance(layout.get("markings"), list) \
            or not isinstance(layout.get("arrows"), list):
        raise ValueError("static road candidate is missing markings or arrows")
    polygons = []
    for field in ("roadbed", "walkbed"):
        entries = road.get(field)
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"road candidate has no {field} geometry")
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError(f"road candidate {field} polygon is invalid")
            polygon = Polygon(item.get("outline", ()), item.get("holes", ()))
            if not polygon.is_valid or polygon.area <= 0:
                raise ValueError(f"road candidate {field} polygon is invalid")
            polygons.append(polygon)
    displayed_sha = builder.displayed_surface_sha256(road["roadbed"], road["walkbed"])
    if road.get("displayed_surface_sha256") != displayed_sha:
        raise ValueError("road candidate displayed surface identity differs from geometry")
    return unary_union(polygons), displayed_sha


def _write_asset(directory: Path, raw: bytes) -> Path:
    sha = _sha(raw)
    path = directory / sha
    path.write_bytes(raw)
    return path


def _verified_compiler_inputs(selection: SceneSelection, compiler_dir: Path) -> tuple[bytes, dict, dict]:
    """Bind the selected source to every immutable compiler output byte."""
    compiler_raw = (compiler_dir / "manifest.json").read_bytes()
    compiler = _json(compiler_raw, "compiler manifest")
    selected_document = selection.to_document()
    selected_receipt = _json((compiler_dir / "authoring-selection.json").read_bytes(),
                             "authoring selection receipt")
    if selected_receipt != selected_document:
        raise ValueError("compiler authoring selection differs from the requested selection")
    if (compiler.get("schema_version") != "aero-bench.urban-scene-compiler/v1"
            or compiler.get("source", {}).get("sha256") != selection.source_sha256
            or any(compiler.get("origin", {}).get(key) != value
                   for key, value in selected_document["origin"].items())
            or compiler.get("crop", {}).get("enu_bounds_m") != selection.bounds_enu_m.manifest_document()):
        raise ValueError("compiler manifest differs from the selected source or bounds")
    outputs = compiler.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("compiler manifest has no output files")
    compiler_outputs = {}
    for item in outputs:
        if (not isinstance(item, dict) or set(item) != {"path", "sha256", "byte_size"}
                or not isinstance(item["path"], str)
                or item["path"].startswith("/")
                or any(part in ("", ".", "..") for part in item["path"].split("/"))
                or item["path"] in compiler_outputs
                or type(item["byte_size"]) is not int or item["byte_size"] <= 0):
            raise ValueError("compiler manifest has invalid or duplicate output references")
        raw = _read_ref(compiler_dir / item["path"], item["sha256"], "compiler output")
        if len(raw) != item["byte_size"]:
            raise ValueError("compiler output size differs from manifest")
        compiler_outputs[item["path"]] = item
    effective_ref = compiler_outputs["osm/effective.osm.json"]
    sumo_ref = compiler_outputs["osm/sumo-network.osm"]

    return compiler_raw, effective_ref, sumo_ref


def _bind_concrete_texture(materials: dict, road: dict, pack: dict, job_id: str) -> dict:
    """Bind a concrete texture only when the audited road actually renders it."""
    concrete = materials["concrete"]
    texture = concrete.get("texture")
    reference = concrete.get("texture_asset")
    if not isinstance(texture, str):
        raise ValueError("static road candidate concrete texture is invalid")
    result = json.loads(json.dumps(materials))
    if road.get("concrete_roadbed"):
        if not isinstance(reference, dict):
            raise ValueError("static road candidate concrete texture is unverified")
        if pack.get("textures", {}).get(texture) != reference:
            raise ValueError("static road concrete texture differs from this mesh pack")
        result["concrete"]["texture_asset"] = {
            **reference,
            "url": f"/authoring/v1/scenes/{job_id}/pack/assets/{reference['sha256']}",
        }
    elif reference is not None or road.get("concrete_roadbed_area_m2") != 0:
        raise ValueError("static road declares concrete texture or area without concrete geometry")
    return result


def finalize_selected_static_presentation(
    *, job_id: str, selection: SceneSelection, compiler_dir: Path, pack_dir: Path,
    network_artifact: Any, road_candidate: Any, public_assets_root: Path,
    output_dir: Path,
) -> StaticPresentation:
    """Validate matching real inputs, place buildings, and seal static JSON assets.

    The caller owns the private staging directory and publishes it only after
    independent verification. This function never edits compiler, pack, network,
    or road-candidate files.
    """
    if not isinstance(selection, SceneSelection) or _HEX.fullmatch(job_id) is None:
        raise ValueError("a validated selection and 64-hex job_id are required")
    if output_dir.exists():
        raise ValueError("static presentation output directory must not already exist")
    compiler_raw, effective_ref, sumo_ref = _verified_compiler_inputs(selection, compiler_dir)

    pack_raw = (pack_dir / "manifest.json").read_bytes()
    pack = _json(pack_raw, "mesh pack manifest")
    from aero_bench.authoring.publication import _validate_pack_shape
    _validate_pack_shape(pack)
    pack_ref = _file_ref(pack_raw)
    if (pack.get("schema_version") != "aero-bench.osm2world-mesh-pack/v1"
            or pack.get("source") != {"sha256": effective_ref["sha256"],
                                      "size_bytes": effective_ref["byte_size"]}
            or pack.get("projection", {}).get("origin") != {
                "latitude_deg": selection.origin.latitude_deg,
                "longitude_deg": selection.origin.longitude_deg,
            }):
        raise ValueError("mesh pack differs from selected compiler output")
    lock = _json((pack_dir / "manifest.lock.json").read_bytes(), "mesh pack lock")
    if lock != pack_ref:
        raise ValueError("mesh pack lock differs from manifest bytes")
    for batch in pack.get("batches", []):
        reference = batch["file"]
        raw = _read_ref(pack_dir / "assets" / reference["sha256"], reference["sha256"], "mesh batch")
        if len(raw) != reference["size_bytes"]:
            raise ValueError("mesh batch size differs from manifest")
    for reference in pack.get("textures", {}).values():
        raw = _read_ref(pack_dir / "assets" / reference["sha256"], reference["sha256"], "mesh texture")
        if len(raw) != reference["size_bytes"]:
            raise ValueError("mesh texture size differs from manifest")

    network_receipt = _json(network_artifact.receipt_path.read_bytes(), "SUMO network receipt")
    if (network_receipt.get("schema_version") != "aero-bench.selected-urban-network/v2"
            or network_receipt.get("compiler_manifest", {}).get("sha256") != _sha(compiler_raw)
            or network_receipt.get("network", {}).get("sha256") != network_artifact.network_sha256
            or network_receipt.get("source_osm", {}).get("sha256") != network_artifact.source_osm_sha256
            or network_receipt.get("projection") != network_artifact.projection
            or network_receipt.get("sumo_image_id") != network_artifact.sumo_image_id):
        raise ValueError("SUMO network receipt differs from the selected build")
    verify_ground_network_closure(
        network_artifact.receipt_path.parent,
        network_closure=network_receipt.get("network_closure"),
        ground_filter_report=network_receipt.get("ground_filter_report"),
        network_sha256=network_artifact.network_sha256,
    )
    for name, path in (("engineering_inputs", network_artifact.engineering_inputs_path),
                       ("ground_filter_report", network_artifact.ground_filter_report_path)):
        ref = network_receipt.get(name)
        if not isinstance(ref, dict):
            raise ValueError(f"SUMO receipt is missing {name}")
        raw = _read_ref(path, ref.get("sha256"), name)
        if len(raw) != ref.get("size_bytes"):
            raise ValueError(f"SUMO {name} size differs from receipt")
    network_raw = _read_ref(network_artifact.network_path, network_artifact.network_sha256, "SUMO network")
    source_raw = _read_ref(network_artifact.source_osm_path, network_artifact.source_osm_sha256,
                           "SUMO source OSM")
    if (network_artifact.source_osm_sha256 != sumo_ref["sha256"]
            or len(source_raw) != sumo_ref["byte_size"]
            or not isinstance(network_artifact.projection, str) or not network_artifact.projection
            or not isinstance(network_artifact.sumo_image_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", network_artifact.sumo_image_id) is None):
        raise ValueError("SUMO network is not bound to this selected compiler output")
    candidate_raw = _read_ref(road_candidate.road_path, road_candidate.road_sha256, "static road candidate")
    candidate = _json(candidate_raw, "static road candidate")
    signal_raw = _read_ref(road_candidate.signal_inventory_path,
                           road_candidate.signal_inventory_sha256, "signal inventory")
    signal = _json(signal_raw, "signal inventory")
    if (road_candidate.source_network_sha256 != network_artifact.network_sha256
            or road_candidate.source_osm_sha256 != sumo_ref["sha256"]
            or road_candidate.mesh_pack_source_sha256 != effective_ref["sha256"]
            or road_candidate.mesh_pack_manifest_sha256 != pack_ref["sha256"]
            or candidate.get("source_network_sha256") != network_artifact.network_sha256
            or candidate.get("source_osm_sha256") != sumo_ref["sha256"]
            or candidate.get("signal_inventory_sha256") != road_candidate.signal_inventory_sha256):
        raise ValueError("static road candidate identity differs from network, source, or signals")
    _strict_signal_inventory(signal, network_artifact.network_sha256, effective_ref["sha256"])

    builder = _placement_builder()
    street_surface, surface_sha = _street_surface(candidate, effective_ref["sha256"],
                                                   pack_ref["sha256"], builder)
    placement = builder.build_placement_payload(pack, pack_raw, pack_dir, candidate, street_surface,
                                                 enforce_displayed_clearance=True)
    if (placement["mesh_pack_source_sha256"] != effective_ref["sha256"]
            or placement["mesh_pack_manifest_sha256"] != pack_ref["sha256"]
            or placement["displayed_surface_sha256"] != surface_sha
            or placement["audit"]["visible_buildings"] <= 0):
        raise ValueError("building placement failed selected source or street clearance validation")
    placement_raw = canonical_json_bytes(placement)

    road = dict(candidate)
    road["schema_version"] = "aero-bench.city-static-road/v1"
    road["building_placement_sha256"] = _sha(placement_raw)
    road["displayed_surface_sha256"] = surface_sha
    road["signal_inventory_sha256"] = _sha(signal_raw)
    materials = road.get("road_surface_materials")
    if (not isinstance(materials, dict) or not isinstance(materials.get("concrete"), dict)
            or not isinstance(materials.get("asphalt"), dict)):
        raise ValueError("static road candidate is missing source road materials")
    asphalt = materials["asphalt"].get("texture")
    if (not isinstance(asphalt, str) or road.get("source_asphalt_texture") != asphalt
            or asphalt not in pack.get("textures", {})):
        raise ValueError("static road asphalt texture differs from this mesh pack")
    materials = _bind_concrete_texture(materials, road, pack, job_id)
    road["road_surface_materials"] = materials
    if (road.get("mesh_pack_manifest_sha256") != pack_ref["sha256"]
            or road.get("mesh_pack_source_sha256") != effective_ref["sha256"]
            or any("traffic" in key or "trajectory" in key for key in road)):
        raise ValueError("final static road contains stale or dynamic provenance")
    road_raw = canonical_json_bytes(road)

    if public_assets_root.is_symlink() or not public_assets_root.is_dir():
        raise ValueError("trusted public asset root is unavailable")
    visual_sources = _visual_asset_sources(public_assets_root)
    visual_raw = canonical_json_bytes({
        "schema_version": "aero-bench.city-visual-assets/v1",
        "assets": [row for row, _ in visual_sources],
    })
    tree_identity = {
        "osm2world_style_tree_sha256": _tree_identity(
            public_assets_root / "osm2world/style", public_assets_root.parent.parent),
        "bigcity_library_tree_sha256": _bigcity_tree_sha256(visual_sources),
    }
    manifest = {
        "schema_version": "aero-bench.city-static-presentation/v1",
        "job_id": job_id,
        "selection_sha256": selection.sha256,
        "source_id": selection.source_id,
        "raw_source_sha256": selection.source_sha256,
        "effective_osm_sha256": effective_ref["sha256"],
        "sumo_source_osm_sha256": sumo_ref["sha256"],
        "compiler_manifest_sha256": _sha(compiler_raw),
        "origin": selection.to_document()["origin"],
        "pack_manifest": pack_ref,
        "network": {**_file_ref(network_raw), "projection": network_artifact.projection,
                    "sumo_image_id": network_artifact.sumo_image_id},
        "road": _file_ref(road_raw),
        "building_placement": _file_ref(placement_raw),
        "signal_inventory": _file_ref(signal_raw),
        "visual_assets": _file_ref(visual_raw),
        **tree_identity,
    }
    manifest_raw = canonical_json_bytes(manifest)
    output_dir.mkdir(parents=True)
    assets = output_dir / "assets"
    assets.mkdir()
    road_path = _write_asset(assets, road_raw)
    placement_path = _write_asset(assets, placement_raw)
    signal_path = _write_asset(assets, signal_raw)
    visual_path = _write_asset(assets, visual_raw)
    for row, raw in visual_sources:
        if _sha(raw) != row["sha256"] or len(raw) != row["size_bytes"]:
            raise ValueError(f"visual asset changed during publication: {row['path']}")
        _write_asset(assets, raw)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_bytes(manifest_raw)
    return StaticPresentation(manifest_path, _sha(manifest_raw), len(manifest_raw), road_path,
                              placement_path, signal_path, visual_path,
                              frozenset((_sha(road_raw), _sha(placement_raw), _sha(signal_raw),
                                         _sha(visual_raw), *(str(row["sha256"]) for row, _ in visual_sources))))
