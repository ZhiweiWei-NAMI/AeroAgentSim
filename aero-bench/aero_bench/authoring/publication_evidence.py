"""Independent byte and provenance checks for a published selected city."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.ground_roads import GROUND_ROAD_POLICY

from .selection import SceneSelection


HEX = re.compile(r"[0-9a-f]{64}\Z")
VISUAL_EXTRAS = {
    "/models/incoming/urban-traffic/images/64d1d14365d8479458d03808ea6b95d5.webp",
    "/models/incoming/urban-traffic/images/f7a11eed4c9d2e047af8297ed9751e31.webp",
    "/models/incoming/furniture/glb/street_light_8.glb",
    "/models/incoming/furniture/glb/traffic_light_4.glb",
    "/models/incoming/furniture/glb/bus_stop_4.glb",
}
MEDIA = {".fbx": "application/octet-stream", ".glb": "model/gltf-binary",
         ".webp": "image/webp", ".png": "image/png", ".json": "application/json"}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"),
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid published JSON: {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"published JSON is not an object: {label}")
    return value


def _json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"invalid published JSON: {path}") from exc
    return _json_bytes(raw, str(path))


def _ref(value: object, label: str, *, allow_empty: bool = False) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"sha256", "size_bytes"}:
        raise ValueError(f"{label} has an invalid file reference")
    sha, size = value["sha256"], value["size_bytes"]
    if not isinstance(sha, str) or HEX.fullmatch(sha) is None or type(size) is not int \
            or size < (0 if allow_empty else 1):
        raise ValueError(f"{label} has an invalid digest or byte count")
    return sha, size


def _file(path: Path, reference: object, label: str, *, allow_empty: bool = False) -> bytes:
    sha, size = _ref(reference, label, allow_empty=allow_empty)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or is a symlink")
    raw = path.read_bytes()
    if len(raw) != size or _digest(raw) != sha:
        raise ValueError(f"{label} bytes differ from their declaration")
    return raw


def _relative_file(directory: Path, item: object, label: str, *, allow_empty: bool = False) -> bytes:
    if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
        raise ValueError(f"{label} has an invalid relative reference")
    name = item["path"]
    if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError(f"{label} has an unsafe relative path")
    path = directory
    if path.is_symlink():
        raise ValueError(f"{label} is missing or is a symlink")
    for part in name.split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError(f"{label} is missing or is a symlink")
    return _file(directory / name, {"sha256": item["sha256"], "size_bytes": item["size_bytes"]},
                 label, allow_empty=allow_empty)


def verify_ground_network_closure(
    directory: Path, *, network_closure: object, ground_filter_report: object,
    network_sha256: str,
) -> None:
    """Bind the indexed ground proof to its network, filtered OSM and report."""
    proof_raw = _relative_file(directory, network_closure, "network closure")
    report_raw = _relative_file(directory, ground_filter_report, "ground filter report")
    if (network_closure["path"] != "urban/ground-network-proof.json"
            or ground_filter_report["path"] != "urban/ground-filter-report.json"):
        raise ValueError("network closure or filter report path differs from selected network")
    proof = _json_bytes(proof_raw, "network closure")
    report = _json_bytes(report_raw, "ground filter report")
    # The report's path is relative to urban/, while the receipt uses the
    # selected-network directory. Keep both references on the same file.
    if report.get("network_closure") != {
            "path": "ground-network-proof.json", "sha256": network_closure["sha256"]}:
        raise ValueError("ground filter report network closure reference differs from receipt")
    if (proof.get("schema_version") != "aero-bench.ground-network-closure/v1"
            or proof.get("policy") != GROUND_ROAD_POLICY):
        raise ValueError("network closure schema or ground policy is unsupported")
    if (not isinstance(network_sha256, str) or HEX.fullmatch(network_sha256) is None
            or proof.get("network_sha256") != network_sha256
            or report.get("network_sha256") != network_sha256):
        raise ValueError("network closure network digest differs from selected network")
    filtered_sha = report.get("filtered_osm_sha256")
    if (not isinstance(filtered_sha, str) or HEX.fullmatch(filtered_sha) is None
            or proof.get("filtered_osm_sha256") != filtered_sha):
        raise ValueError("network closure filtered OSM digest differs from ground filter report")
    filtered_path = directory / "urban/ground.osm"
    if filtered_path.is_symlink() or not filtered_path.is_file():
        raise ValueError("filtered ground OSM is missing or is a symlink")
    filtered_raw = filtered_path.read_bytes()
    if not filtered_raw or _digest(filtered_raw) != filtered_sha:
        raise ValueError("filtered ground OSM bytes differ from network closure")


def evidence_tree_sha256(directory: Path) -> str:
    """Seal every private evidence file, including logs not named by a receipt."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("private evidence directory is missing")
    entries = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("private evidence contains a symlink")
        if path.is_file():
            raw = path.read_bytes()
            entries.append({"path": path.relative_to(directory).as_posix(),
                            "sha256": _digest(raw), "size_bytes": len(raw)})
    if not entries:
        raise ValueError("private evidence directory is empty")
    return _digest(canonical_json_bytes(entries))


def verify_selected_presentation(
    final: Path, *, job_id: str, selection: SceneSelection, pack_ref: dict[str, object],
    compiler_manifest_sha256: str, effective_osm_sha256: str, identity: dict[str, object],
    receipt: dict[str, object],
) -> tuple[dict[str, object], set[str]]:
    """Return the public manifest reference and its only allowed asset hashes."""
    if HEX.fullmatch(job_id) is None or final.is_symlink() or not final.is_dir():
        raise ValueError("published city path or job ID is invalid")
    directory = final / "presentation"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("published static presentation is missing")
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("published static presentation manifest is missing")
    manifest_raw = manifest_path.read_bytes()
    manifest_ref = {"sha256": _digest(manifest_raw), "size_bytes": len(manifest_raw)}
    if receipt.get("presentation_manifest") != manifest_ref:
        raise ValueError("presentation manifest differs from publication receipt")
    manifest = _json(manifest_path)
    required = {"schema_version", "job_id", "selection_sha256", "source_id",
                "raw_source_sha256", "effective_osm_sha256", "sumo_source_osm_sha256",
                "compiler_manifest_sha256", "origin", "pack_manifest", "network", "road",
                "building_placement", "signal_inventory", "visual_assets",
                "osm2world_style_tree_sha256", "bigcity_library_tree_sha256"}
    compiler = _json(final / "interchange" / "manifest.json")
    outputs = {row["path"]: row for row in compiler["outputs"]}
    sumo_sha = outputs["osm/sumo-network.osm"]["sha256"]
    visual_identity = identity.get("visual_assets")
    if (set(manifest) != required or manifest["schema_version"] != "aero-bench.city-static-presentation/v1"
            or manifest["job_id"] != job_id or manifest["selection_sha256"] != selection.sha256
            or manifest["source_id"] != selection.source_id
            or manifest["raw_source_sha256"] != selection.source_sha256
            or manifest["effective_osm_sha256"] != effective_osm_sha256
            or manifest["sumo_source_osm_sha256"] != sumo_sha
            or manifest["compiler_manifest_sha256"] != compiler_manifest_sha256
            or manifest["origin"] != selection.to_document()["origin"]
            or manifest["pack_manifest"] != pack_ref
            or not isinstance(visual_identity, dict)
            or manifest["osm2world_style_tree_sha256"] != visual_identity.get("osm2world_style_tree_sha256")
            or manifest["bigcity_library_tree_sha256"] != visual_identity.get("bigcity_library_tree_sha256")):
        raise ValueError("presentation identity differs from its selected city")

    network_dir = final / "evidence" / "urban-network"
    road_dir = final / "evidence" / "static-road"
    if (receipt.get("urban_network_evidence_sha256") != evidence_tree_sha256(network_dir)
            or receipt.get("static_road_evidence_sha256") != evidence_tree_sha256(road_dir)):
        raise ValueError("private network or road evidence changed")
    network_receipt = _json(network_dir / "selected-network-receipt.json")
    network_keys = {"schema_version", "compiler_manifest", "source_osm", "initial_network",
                    "network", "network_closure", "engineering_inputs", "ground_filter_report", "initial_netconvert",
                    "initial_version_check", "initial_version_stdout", "initial_version_stderr",
                    "initial_stdout", "initial_stderr", "urban_netconvert", "urban_stdout",
                    "urban_stderr", "projection", "sumo_image_id", "netconvert_version"}
    if (set(network_receipt) != network_keys
            or network_receipt.get("schema_version") != "aero-bench.selected-urban-network/v2"
            or network_receipt.get("compiler_manifest") != {"sha256": compiler_manifest_sha256}):
        raise ValueError("selected network receipt differs from compiler")
    for key, item in network_receipt.items():
        if isinstance(item, dict) and "path" in item:
            _relative_file(network_dir, item, f"network {key}",
                           allow_empty=key.endswith("stdout") or key.endswith("stderr"))
    network = manifest["network"]
    if (not isinstance(network, dict)
            or set(network) != {"sha256", "size_bytes", "projection", "sumo_image_id"}
            or network_receipt.get("network") != {
                "path": "urban/network.net.xml", "sha256": network["sha256"],
                "size_bytes": network["size_bytes"]}
            or network_receipt.get("source_osm", {}).get("sha256") != sumo_sha
            or network_receipt.get("projection") != network["projection"]
            or network_receipt.get("sumo_image_id") != network["sumo_image_id"]):
        raise ValueError("published network differs from its receipt")
    engineering = _json(network_dir / "urban/engineering-inputs.json")
    filter_report = _json(network_dir / "urban/ground-filter-report.json")
    if (engineering.get("network_sha256") != network["sha256"]
            or engineering.get("source_osm_sha256") != sumo_sha
            or engineering.get("projection") != network["projection"]
            or engineering.get("sumo_image_id") != network["sumo_image_id"]
            or filter_report.get("network_sha256") != network["sha256"]
            or filter_report.get("source_osm_sha256") != sumo_sha):
        raise ValueError("network engineering and filter audit differ from published bytes")
    verify_ground_network_closure(
        network_dir, network_closure=network_receipt["network_closure"],
        ground_filter_report=network_receipt["ground_filter_report"],
        network_sha256=network["sha256"],
    )

    visual_dir = directory / "assets"
    if visual_dir.is_symlink() or not visual_dir.is_dir():
        raise ValueError("presentation assets directory is missing")
    declared: set[str] = set()
    documents: dict[str, dict[str, Any]] = {}
    for key in ("road", "building_placement", "signal_inventory", "visual_assets"):
        sha, _ = _ref(manifest[key], key)
        documents[key] = _json(visual_dir / sha)
        _file(visual_dir / sha, manifest[key], key)
        declared.add(sha)
    road = documents["road"]
    placement = documents["building_placement"]
    signals = documents["signal_inventory"]
    if (road.get("schema_version") != "aero-bench.city-static-road/v1"
            or road.get("source_network_sha256") != network["sha256"]
            or road.get("source_osm_sha256") != sumo_sha
            or road.get("mesh_pack_source_sha256") != effective_osm_sha256
            or road.get("mesh_pack_manifest_sha256") != pack_ref["sha256"]
            or road.get("building_placement_sha256") != manifest["building_placement"]["sha256"]
            or road.get("signal_inventory_sha256") != manifest["signal_inventory"]["sha256"]
            or placement.get("schema_version") != "aero-bench.city-building-placement/v1"
            or placement.get("mesh_pack_source_sha256") != effective_osm_sha256
            or placement.get("mesh_pack_manifest_sha256") != pack_ref["sha256"]
            or placement.get("displayed_surface_sha256") != road.get("displayed_surface_sha256")
            or signals.get("schema_version") != "aero-bench.city-static-signal-inventory/v1"
            or signals.get("source_network_sha256") != network["sha256"]
            or signals.get("mesh_pack_source_sha256") != effective_osm_sha256):
        raise ValueError("static roads, buildings, and signals have different source identities")
    candidate_item = receipt.get("static_road_candidate")
    audit_item = receipt.get("static_road_audit")
    candidate = _json(road_dir / "candidate.json")
    audit = _json(road_dir / "candidate-audit.json")
    _relative_file(road_dir, candidate_item, "static road candidate")
    _relative_file(road_dir, audit_item, "static road audit")
    _file(road_dir / "candidate-signals.json", manifest["signal_inventory"], "private signal inventory")
    if (candidate.get("schema_version") != "aero-bench.city-static-road-candidate/v1"
            or candidate.get("source_network_sha256") != network["sha256"]
            or candidate.get("signal_inventory_sha256") != manifest["signal_inventory"]["sha256"]
            or audit.get("road_sha256") != candidate_item["sha256"]
            or audit.get("signal_inventory_sha256") != manifest["signal_inventory"]["sha256"]):
        raise ValueError("static road audit differs from its candidate")
    expected_road = json.loads(json.dumps(candidate))
    expected_road["schema_version"] = "aero-bench.city-static-road/v1"
    expected_road["building_placement_sha256"] = manifest["building_placement"]["sha256"]
    concrete = expected_road.get("road_surface_materials", {}).get("concrete", {})
    texture_ref = concrete.get("texture_asset")
    if expected_road.get("concrete_roadbed"):
        if not isinstance(texture_ref, dict):
            raise ValueError("static road concrete material lacks a source pack reference")
        texture_sha, _ = _ref(texture_ref, "static road concrete texture")
        concrete["texture_asset"] = {
            **texture_ref, "url": f"/authoring/v1/scenes/{job_id}/pack/assets/{texture_sha}",
        }
    elif texture_ref is not None or expected_road.get("concrete_roadbed_area_m2") != 0:
        raise ValueError("static road has concrete material without concrete geometry")
    if road != expected_road:
        raise ValueError("published static road differs from audited candidate geometry")

    visual = documents["visual_assets"]
    if (set(visual) != {"schema_version", "assets"}
            or visual["schema_version"] != "aero-bench.city-visual-assets/v1"
            or not isinstance(visual["assets"], list) or not visual["assets"]):
        raise ValueError("visual asset inventory is invalid")
    if _digest(canonical_json_bytes(visual["assets"])) != visual_identity.get("visual_assets_tree_sha256"):
        raise ValueError("visual asset inventory differs from the job identity")
    bigcity = []
    prior = ""
    for item in visual["assets"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes", "media_type"}:
            raise ValueError("visual asset reference is invalid")
        name = item["path"]
        if (not isinstance(name, str) or name <= prior or ".." in name or "//" in name
                or (not name.startswith("/models/bigcity/") and name not in VISUAL_EXTRAS)
                or MEDIA.get(Path(name).suffix.lower()) != item["media_type"]):
            raise ValueError("visual asset path or media type is invalid")
        prior = name
        raw_ref = {"sha256": item["sha256"], "size_bytes": item["size_bytes"]}
        sha, _ = _ref(raw_ref, name)
        _file(visual_dir / sha, raw_ref, name)
        declared.add(sha)
        if name.startswith("/models/bigcity/"):
            bigcity.append({"path": name, **raw_ref})
    if not VISUAL_EXTRAS.issubset({item["path"] for item in visual["assets"]}) \
            or "/models/bigcity/manifest.json" not in {item["path"] for item in visual["assets"]}:
        raise ValueError("visual inventory omits a required street asset")
    if _digest(canonical_json_bytes(bigcity)) != manifest["bigcity_library_tree_sha256"]:
        raise ValueError("BigCity asset tree differs from manifest")
    actual = {path.name for path in visual_dir.iterdir() if path.is_file() and not path.is_symlink()}
    if actual != declared or any(path.is_symlink() or not path.is_file() for path in visual_dir.iterdir()):
        raise ValueError("presentation asset directory contains missing or undeclared files")
    return manifest_ref, declared
