"""Byte-level checks at the selected city publication boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aero_bench.authoring.publication_evidence import (
    VISUAL_EXTRAS, _relative_file, evidence_tree_sha256, verify_ground_network_closure,
    verify_selected_presentation,
)
from aero_bench.authoring.selection import SceneSelection
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.ground_roads import audit_ground_network


def test_empty_private_stderr_is_verified_but_empty_artifacts_are_rejected(tmp_path: Path) -> None:
    log = tmp_path / "netconvert.stderr.log"
    log.write_bytes(b"")
    reference = {"path": log.name, "sha256": hashlib.sha256(b"").hexdigest(), "size_bytes": 0}
    assert _relative_file(tmp_path, reference, "netconvert stderr", allow_empty=True) == b""
    with pytest.raises(ValueError, match="byte count"):
        _relative_file(tmp_path, reference, "SUMO network")
    log.write_bytes(b"unexpected warning")
    with pytest.raises(ValueError, match="bytes differ"):
        _relative_file(tmp_path, reference, "netconvert stderr", allow_empty=True)


def test_private_network_and_road_evidence_tree_detects_changed_bytes(tmp_path: Path) -> None:
    network = tmp_path / "urban-network"
    road = tmp_path / "static-road"
    network.mkdir()
    road.mkdir()
    (network / "network.net.xml").write_bytes(b"<net/>")
    (network / "netconvert.stderr.log").write_bytes(b"")
    (road / "candidate.json").write_bytes(b'{"lanes":1}')
    (road / "candidate-audit.json").write_bytes(b'{"checked":true}')
    network_receipt = evidence_tree_sha256(network)
    road_receipt = evidence_tree_sha256(road)
    assert evidence_tree_sha256(network) == network_receipt
    assert evidence_tree_sha256(road) == road_receipt
    (network / "network.net.xml").write_bytes(b"<net tampered='1'/>")
    (road / "candidate-audit.json").write_bytes(b'{"checked":false}')
    assert evidence_tree_sha256(network) != network_receipt
    assert evidence_tree_sha256(road) != road_receipt


def _write(directory: Path, name: str, raw: bytes) -> dict:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def _write_json(directory: Path, name: str, document: dict) -> dict:
    return _write(directory, name, canonical_json_bytes(document))


@pytest.fixture
def selected_publication(tmp_path: Path) -> tuple[Path, dict]:
    """A module fixture with a computed road proof and real visual asset bytes."""
    final = tmp_path / "published"
    network_dir = final / "evidence/urban-network"
    road_dir = final / "evidence/static-road"
    source = b'''<osm version="0.6"><node id="1" lat="31.2304" lon="121.4737"/>
      <node id="2" lat="31.2304" lon="121.4738"/><way id="10"><nd ref="1"/>
      <nd ref="2"/><tag k="highway" v="residential"/></way></osm>'''
    network = b'''<net><location netOffset="0,0"
      projParameter="+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"/>
      <edge id="10" from="1" to="2"><lane id="10_0" index="0"
      allow="passenger bicycle pedestrian" shape="0,0 10,0"/></edge>
      <junction id="1" incLanes="" intLanes="" shape="0,0 0,1"/>
      <junction id="2" incLanes="10_0" intLanes="" shape="10,0 10,1"/></net>'''
    projection = "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"
    proof = audit_ground_network(network, source, expected_projection=projection)
    network_sha = proof["network_sha256"]
    source_sha = proof["filtered_osm_sha256"]
    image = "sha256:" + "a" * 64
    selection = SceneSelection.from_document({
        "schema_version": "aero-bench.scene-selection/v1", "source_id": "shanghai-central-osm-v1",
        "source_sha256": source_sha,
        "origin": {"latitude_deg": 31.2304, "longitude_deg": 121.4737,
                   "ellipsoid_height_m": 50.0, "geoid_undulation_m": 30.0, "amsl_m": 20.0},
        "bounds_enu_m": {"min_east_m": -10.0, "max_east_m": 10.0,
                         "min_north_m": -10.0, "max_north_m": 10.0},
    })
    compiler_ref = _write_json(final, "interchange/manifest.json", {"outputs": [
        {"path": "osm/sumo-network.osm", "sha256": source_sha, "byte_size": len(source)},
    ]})
    receipt = {
        "schema_version": "aero-bench.selected-urban-network/v2",
        "compiler_manifest": {"sha256": compiler_ref["sha256"]},
        "source_osm": _write(network_dir, "source/sumo-network.osm", source),
        "initial_network": _write(network_dir, "initial/network.net.xml", network),
        "network": _write(network_dir, "urban/network.net.xml", network),
        "network_closure": _write_json(network_dir, "urban/ground-network-proof.json", proof),
        "engineering_inputs": _write_json(network_dir, "urban/engineering-inputs.json", {
            "network_sha256": network_sha, "source_osm_sha256": source_sha,
            "projection": projection, "sumo_image_id": image,
        }),
        "projection": projection, "sumo_image_id": image, "netconvert_version": "1.27.1",
    }
    _write(network_dir, "urban/ground.osm", source)
    receipt["ground_filter_report"] = _write_json(network_dir, "urban/ground-filter-report.json", {
        "network_sha256": network_sha, "source_osm_sha256": source_sha,
        "filtered_osm_sha256": source_sha,
        "network_closure": {"path": "ground-network-proof.json",
                            "sha256": receipt["network_closure"]["sha256"]},
    })
    # These declarations exercise the static publication contract only. The
    # real Docker builder is tested separately; this fixture claims no run.
    for key, name in (
        ("initial_netconvert", "initial/netconvert-command.json"),
        ("initial_version_check", "initial/netconvert-version.json"),
        ("initial_version_stdout", "initial/netconvert-version.stdout.log"),
        ("initial_version_stderr", "initial/netconvert-version.stderr.log"),
        ("initial_stdout", "initial/netconvert.stdout.log"),
        ("initial_stderr", "initial/netconvert.stderr.log"),
        ("urban_netconvert", "urban/netconvert-command.json"),
        ("urban_stdout", "urban/netconvert.stdout.log"),
        ("urban_stderr", "urban/netconvert.stderr.log"),
    ):
        receipt[key] = _write(network_dir, name, b"{}" if name.endswith(".json") else b"")
    _write_json(network_dir, "selected-network-receipt.json", receipt)

    effective_sha = hashlib.sha256(b"unfiltered building source fixture").hexdigest()
    pack_ref = {"sha256": hashlib.sha256(b"pack fixture").hexdigest(), "size_bytes": 12}
    visual_dir = final / "presentation/assets"

    def asset(document: dict) -> dict:
        raw = canonical_json_bytes(document)
        item = _write(visual_dir, hashlib.sha256(raw).hexdigest(), raw)
        return {key: item[key] for key in ("sha256", "size_bytes")}

    signals = asset({"schema_version": "aero-bench.city-static-signal-inventory/v1",
                     "source_network_sha256": network_sha, "mesh_pack_source_sha256": effective_sha,
                     "signals": []})
    placement = asset({"schema_version": "aero-bench.city-building-placement/v1",
                       "mesh_pack_source_sha256": effective_sha, "mesh_pack_manifest_sha256": pack_ref["sha256"],
                       "displayed_surface_sha256": "b" * 64})
    candidate = {
        "schema_version": "aero-bench.city-static-road-candidate/v1", "source_network_sha256": network_sha,
        "source_osm_sha256": source_sha, "mesh_pack_source_sha256": effective_sha,
        "mesh_pack_manifest_sha256": pack_ref["sha256"], "signal_inventory_sha256": signals["sha256"],
        "displayed_surface_sha256": "b" * 64, "concrete_roadbed": [], "concrete_roadbed_area_m2": 0,
        "road_surface_materials": {"concrete": {}},
    }
    candidate_ref = _write_json(road_dir, "candidate.json", candidate)
    audit_ref = _write_json(road_dir, "candidate-audit.json", {
        "road_sha256": candidate_ref["sha256"], "signal_inventory_sha256": signals["sha256"],
    })
    (road_dir / "candidate-signals.json").write_bytes((visual_dir / signals["sha256"]).read_bytes())
    road = asset({**candidate, "schema_version": "aero-bench.city-static-road/v1",
                  "building_placement_sha256": placement["sha256"]})
    visual_rows = []
    repository = Path(__file__).resolve().parents[2]
    for name in sorted({"/models/bigcity/manifest.json", *VISUAL_EXTRAS}):
        raw = (repository / "frontend/public" / name.lstrip("/")).read_bytes()
        item = _write(visual_dir, hashlib.sha256(raw).hexdigest(), raw)
        visual_rows.append({**item, "path": name, "media_type": {
            ".json": "application/json", ".glb": "model/gltf-binary", ".webp": "image/webp",
        }[Path(name).suffix]})
    bigcity_rows = [{key: row[key] for key in ("path", "sha256", "size_bytes")}
                   for row in visual_rows if row["path"].startswith("/models/bigcity/")]
    bigcity_sha = hashlib.sha256(canonical_json_bytes(bigcity_rows)).hexdigest()
    visual = asset({"schema_version": "aero-bench.city-visual-assets/v1", "assets": visual_rows})
    identity = {"visual_assets": {
        "osm2world_style_tree_sha256": "c" * 64, "bigcity_library_tree_sha256": bigcity_sha,
        "visual_assets_tree_sha256": hashlib.sha256(canonical_json_bytes(visual_rows)).hexdigest(),
    }}
    job_id = "d" * 64
    manifest_ref = _write_json(final, "presentation/manifest.json", {
        "schema_version": "aero-bench.city-static-presentation/v1", "job_id": job_id,
        "selection_sha256": selection.sha256, "source_id": selection.source_id,
        "raw_source_sha256": selection.source_sha256, "effective_osm_sha256": effective_sha,
        "sumo_source_osm_sha256": source_sha, "compiler_manifest_sha256": compiler_ref["sha256"],
        "origin": selection.to_document()["origin"], "pack_manifest": pack_ref,
        "network": {"sha256": network_sha, "size_bytes": len(network),
                    "projection": projection, "sumo_image_id": image},
        "road": road, "building_placement": placement, "signal_inventory": signals, "visual_assets": visual,
        "osm2world_style_tree_sha256": "c" * 64, "bigcity_library_tree_sha256": bigcity_sha,
    })
    publication_receipt = {
        "presentation_manifest": {key: manifest_ref[key] for key in ("sha256", "size_bytes")},
        "urban_network_evidence_sha256": evidence_tree_sha256(network_dir),
        "static_road_evidence_sha256": evidence_tree_sha256(road_dir),
        "static_road_candidate": candidate_ref, "static_road_audit": audit_ref,
    }
    _write_json(final, "authoring-publication.json", publication_receipt)
    return final, {"job_id": job_id, "selection": selection, "pack_ref": pack_ref,
                   "compiler_manifest_sha256": compiler_ref["sha256"], "effective_osm_sha256": effective_sha,
                   "identity": identity, "receipt": publication_receipt}


def _reseal_network(final: Path, kwargs: dict, *, sync_report_ref: bool = True) -> None:
    """Rehash all file references and the evidence tree after a semantic attack."""
    directory = final / "evidence/urban-network"
    receipt = json.loads((directory / "selected-network-receipt.json").read_bytes())
    proof_path = receipt["network_closure"]["path"]
    receipt["network_closure"] = _write(directory, proof_path, (directory / proof_path).read_bytes())
    report_path = receipt["ground_filter_report"]["path"]
    report = json.loads((directory / report_path).read_bytes())
    if sync_report_ref:
        report["network_closure"] = {"path": "ground-network-proof.json",
                                    "sha256": receipt["network_closure"]["sha256"]}
    receipt["ground_filter_report"] = _write_json(directory, report_path, report)
    _write_json(directory, "selected-network-receipt.json", receipt)
    kwargs["receipt"]["urban_network_evidence_sha256"] = evidence_tree_sha256(directory)
    _write_json(final, "authoring-publication.json", kwargs["receipt"])
    for key, item in receipt.items():
        if isinstance(item, dict) and "path" in item:
            _relative_file(directory, item, key, allow_empty=key.endswith(("stdout", "stderr")))
    assert kwargs["receipt"]["urban_network_evidence_sha256"] == evidence_tree_sha256(directory)


def test_publication_accepts_v2_indexed_closure_with_distinct_building_source(selected_publication) -> None:
    final, kwargs = selected_publication
    reference, assets = verify_selected_presentation(final, **kwargs)
    manifest = json.loads((final / "presentation/manifest.json").read_bytes())
    assert reference == kwargs["receipt"]["presentation_manifest"]
    assert manifest["effective_osm_sha256"] != manifest["sumo_source_osm_sha256"]
    assert assets == {path.name for path in (final / "presentation/assets").iterdir()}


@pytest.mark.parametrize("field, value, message", [
    ("schema_version", "aero-bench.ground-network-closure/v0", "schema or ground policy"),
    ("policy", "unverified-policy/v1", "schema or ground policy"),
    ("network_sha256", "e" * 64, "network digest differs"),
    ("filtered_osm_sha256", "f" * 64, "filtered OSM digest differs"),
])
def test_publication_rejects_semantically_changed_proof_after_rehashing_entire_evidence(
    selected_publication, field: str, value: str, message: str,
) -> None:
    final, kwargs = selected_publication
    directory = final / "evidence/urban-network"
    proof = json.loads((directory / "urban/ground-network-proof.json").read_bytes())
    proof[field] = value
    _write_json(directory, "urban/ground-network-proof.json", proof)
    _reseal_network(final, kwargs)
    with pytest.raises(ValueError, match=message):
        verify_selected_presentation(final, **kwargs)


@pytest.mark.parametrize("path, digest", [
    ("other-proof.json", None), ("../ground-network-proof.json", None),
    ("ground-network-proof.json", "f" * 64),
])
def test_publication_rejects_report_proof_reference_drift_after_rehash(
    selected_publication, path: str, digest: str | None,
) -> None:
    final, kwargs = selected_publication
    directory = final / "evidence/urban-network"
    report = json.loads((directory / "urban/ground-filter-report.json").read_bytes())
    report["network_closure"]["path"] = path
    if digest is not None:
        report["network_closure"]["sha256"] = digest
    _write_json(directory, "urban/ground-filter-report.json", report)
    _reseal_network(final, kwargs, sync_report_ref=False)
    with pytest.raises(ValueError, match="closure reference differs from receipt"):
        verify_selected_presentation(final, **kwargs)


@pytest.mark.parametrize("attack, message", [
    ("missing_reference", "receipt differs from compiler"),
    ("old_receipt_version", "receipt differs from compiler"),
    ("missing_proof", "network network_closure is missing"),
    ("changed_proof_bytes", "network network_closure bytes differ"),
    ("wrong_size", "network network_closure bytes differ"),
])
def test_publication_rejects_absent_or_unverified_closure_after_resealing_tree(
    selected_publication, attack: str, message: str,
) -> None:
    final, kwargs = selected_publication
    directory = final / "evidence/urban-network"
    receipt = json.loads((directory / "selected-network-receipt.json").read_bytes())
    proof_path = directory / "urban/ground-network-proof.json"
    if attack == "missing_reference":
        del receipt["network_closure"]
    elif attack == "old_receipt_version":
        receipt["schema_version"] = "aero-bench.selected-urban-network/v1"
    elif attack == "missing_proof":
        proof_path.unlink()
    elif attack == "changed_proof_bytes":
        proof_path.write_bytes(proof_path.read_bytes() + b"\n")
    elif attack == "wrong_size":
        receipt["network_closure"]["size_bytes"] += 1
    _write_json(directory, "selected-network-receipt.json", receipt)
    kwargs["receipt"]["urban_network_evidence_sha256"] = evidence_tree_sha256(directory)
    _write_json(final, "authoring-publication.json", kwargs["receipt"])
    with pytest.raises(ValueError, match=message):
        verify_selected_presentation(final, **kwargs)


def test_publication_rejects_resealed_filtered_source_bytes_that_do_not_match_proof(selected_publication) -> None:
    final, kwargs = selected_publication
    directory = final / "evidence/urban-network"
    ground = directory / "urban/ground.osm"
    ground.write_bytes(ground.read_bytes() + b"\n")
    _reseal_network(final, kwargs)
    with pytest.raises(ValueError, match="filtered ground OSM bytes differ"):
        verify_selected_presentation(final, **kwargs)


@pytest.mark.parametrize("reference_path", ["../proof.json", "/proof.json", "urban/../proof.json", "urban\\proof.json"])
def test_closure_reference_rejects_unsafe_paths(selected_publication, reference_path: str) -> None:
    final, kwargs = selected_publication
    directory = final / "evidence/urban-network"
    receipt = json.loads((directory / "selected-network-receipt.json").read_bytes())
    receipt["network_closure"]["path"] = reference_path
    with pytest.raises(ValueError, match="unsafe relative path"):
        verify_ground_network_closure(
            directory, network_closure=receipt["network_closure"],
            ground_filter_report=receipt["ground_filter_report"],
            network_sha256=receipt["network"]["sha256"],
        )


@pytest.mark.parametrize("linked_parent", [False, True])
def test_closure_reference_rejects_linked_file_or_parent(selected_publication, linked_parent: bool) -> None:
    final, kwargs = selected_publication
    directory = final / "evidence/urban-network"
    receipt = json.loads((directory / "selected-network-receipt.json").read_bytes())
    proof_path = directory / "urban/ground-network-proof.json"
    if linked_parent:
        urban = directory / "urban"
        moved = directory / "original-urban"
        urban.rename(moved)
        urban.symlink_to(moved, target_is_directory=True)
    else:
        moved = directory / "proof.json"
        proof_path.rename(moved)
        proof_path.symlink_to(moved)
    with pytest.raises(ValueError, match="symlink"):
        verify_ground_network_closure(
            directory, network_closure=receipt["network_closure"],
            ground_filter_report=receipt["ground_filter_report"],
            network_sha256=receipt["network"]["sha256"],
        )
