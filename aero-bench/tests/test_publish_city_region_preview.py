from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools.publish_city_region_preview import (
    PublicationError,
    _json_bytes,
    measure_file,
    measure_tree,
    prepare_publication,
)


REGION_ID = "test-region-v1"
SCENE_URL = "/city-presentation/test-region-preview-v1.json"


def _write(path: Path, content: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    measured = measure_file(path)
    return {"path": path.name, **measured.as_dict()}


def _write_relative(root: Path, relative: str, content: bytes) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"path": relative, **measure_file(path).as_dict()}


def _public_asset(public: Path, url: str, content: bytes) -> dict[str, Any]:
    path = public / url.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"url": url, **measure_file(path).as_dict()}


def _fixture(tmp_path: Path) -> dict[str, Path]:
    staging = tmp_path / "staging"
    public = tmp_path / "frontend-public"
    candidate = tmp_path / "candidate"
    staging.mkdir(parents=True)
    public.mkdir(parents=True)

    road_texture = _public_asset(public, "/osm2world/style/textures/road.png", b"road texture")
    signal = _public_asset(public, "/models/signal.glb", b"signal model")
    lamp = _public_asset(public, "/models/lamp.glb", b"lamp model")

    objects = {
        "schema_version": "aero-bench.urban-scene-objects/v1",
        "coordinate_frame": "ENU",
        "origin": {
            "frame": "WGS84->ECEF->ENU",
            "latitude_deg": 31.2,
            "longitude_deg": 121.4,
            "ellipsoid_height_m": 50.0,
        },
        "buildings": [],
    }
    objects_path = staging / "source/objects.json"
    objects_path.parent.mkdir(parents=True)
    objects_path.write_bytes(_json_bytes(objects))
    objects_measurement = measure_file(objects_path)

    source_osm_json = _json_bytes({"elements": []})
    source_osm_json_measurement = {
        "sha256": hashlib.sha256(source_osm_json).hexdigest(),
        "size_bytes": len(source_osm_json),
    }
    pack_root = staging / "pack"
    (pack_root / "assets").mkdir(parents=True)
    (pack_root / "assets" / source_osm_json_measurement["sha256"]).write_bytes(source_osm_json)
    pack = {
        "schema_version": "aero-bench.osm2world-mesh-pack/v1",
        "source": source_osm_json_measurement,
        "textures": {road_texture["url"]: {
            "sha256": road_texture["sha256"], "size_bytes": road_texture["size_bytes"]}},
    }
    (pack_root / "manifest.json").write_bytes(_json_bytes(pack))
    pack_measurement = measure_file(pack_root / "manifest.json")

    runtime_scene = {
        "coordinate_frame": "ENU",
        "id": REGION_ID,
        "mesh_pack_manifest_sha256": pack_measurement.sha256,
        "mesh_pack_source_sha256": source_osm_json_measurement["sha256"],
        "objects_json_sha256": objects_measurement.sha256,
        "origin_wgs84": {
            "latitude_deg": 31.2,
            "longitude_deg": 121.4,
            "ellipsoid_height_m": 50.0,
        },
        "placement_rule": "test",
    }
    runtime_context = {
        "schema_version": "aero-bench.building-render-source-context/v1",
        "scene": runtime_scene,
        "buildings": [],
        "sources": {},
    }
    runtime_context_bytes = _json_bytes(runtime_context)
    runtime_context_sha = hashlib.sha256(runtime_context_bytes).hexdigest()
    runtime_root = staging / "runtime"
    (runtime_root / "assets").mkdir(parents=True)
    (runtime_root / "assets" / runtime_context_sha).write_bytes(runtime_context_bytes)
    runtime = {
        "schema_version": "aero-bench.building-render-runtime/v2",
        "scene": runtime_scene,
        "buildings": [],
        "blocks": [],
        "textures": [],
    }
    (runtime_root / "manifest.json").write_bytes(_json_bytes(runtime))
    runtime_measurement = measure_file(runtime_root / "manifest.json")

    selection_ref = _write_relative(staging, "selection.json", b"selection\n")
    network_ref = _write_relative(staging, "source/network.net.xml", b"<net/>\n")
    source_osm_ref = _write_relative(staging, "source/roads.osm", b"<osm/>\n")
    engineering_ref = _write_relative(staging, "source/engineering.json", b"{}\n")
    closed_loop_doc = {
        "schema_version": "aero-bench.ground-streets-closed-loop/v1",
        "final_status": "PASS",
        "rounds": [{"status": "PASS", "gate": {"status": "PASS"}}],
    }
    closed_loop_ref = _write_relative(staging, "audit/closed-loop.json", _json_bytes(closed_loop_doc))

    rendered_context = {
        "schema_version": "aero-bench.city-rendered-source-context/v1",
        "scene_id": REGION_ID,
        "objects_json_sha256": objects_measurement.sha256,
        "building_render_manifest_sha256": runtime_measurement.sha256,
        "mesh_pack_manifest_sha256": pack_measurement.sha256,
        "mesh_pack_source_sha256": source_osm_json_measurement["sha256"],
        "source_network_sha256": network_ref["sha256"],
        "source_osm_sha256": source_osm_ref["sha256"],
        "engineering_inputs_sha256": engineering_ref["sha256"],
    }
    displayed_surface = hashlib.sha256(b"displayed surface").hexdigest()
    road = {
        "schema_version": "aero-bench.city-road-preview/v3",
        "source_context": rendered_context,
        "source_asphalt_texture": road_texture["url"],
        "displayed_surface_sha256": displayed_surface,
        "physical_clearance": {"status": "PASS"},
    }
    road_ref = _write_relative(staging, "viewer/road.json", _json_bytes(road))
    road_ref["displayed_surface_sha256"] = displayed_surface
    clearance = {
        "schema_version": "aero-bench.city-road-physical-clearance/v1",
        "source_context": rendered_context,
        "native_lane_clearance": {"status": "PASS"},
    }
    clearance_ref = _write_relative(staging, "audit/clearance.json", _json_bytes(clearance))
    clearance_ref["status"] = "PASS"
    fixtures = {
        "schema_version": "aero-bench.city-effective-fixture-geometry/v1",
        "source_context": rendered_context,
        "displayed_surface_sha256": displayed_surface,
        "models": {"signal": signal, "street_lamp": lamp},
    }
    fixtures_ref = _write_relative(staging, "viewer/fixtures.json", _json_bytes(fixtures))
    traffic = {
        "schema_version": "aero-bench.city-sumo-preview/v2",
        "artifact_class": "offline-engineering-preview",
        "source_kind": "offline-sumo-engineering-preview",
        "source_context": rendered_context,
        "visual_obstacle_basis": {"effective_fixture_geometry_sha256": fixtures_ref["sha256"]},
    }
    traffic_ref = _write_relative(staging, "viewer/traffic.json", _json_bytes(traffic))
    traffic_ref.update({
        "source_kind": "offline-sumo-engineering-preview",
        "formal_provider_evidence": False,
    })
    flight = {
        "schema_version": "aero-bench.city-planned-flight-preview/v2",
        "source_kind": "planned-visual-flight",
        "physical_simulation": False,
        "source_context": rendered_context,
    }
    flight_ref = _write_relative(staging, "viewer/flight.json", _json_bytes(flight))
    flight_ref.update({"source_kind": "planned-visual-flight", "physical_simulation": False})

    workspace_ref = _write_relative(staging, "traffic/workspace.json", b"{}\n")
    native_ref = _write_relative(staging, "traffic/private/native.json", b"{}\n")
    routes_ref = _write_relative(staging, "traffic/private/routes.xml", b"<routes/>\n")
    recording_ref = _write_relative(staging, "traffic/private/recording.json", b"{}\n")
    motion_audit = {
        "schema_version": "aero-bench.city-canonical-motion-audit/v2",
        "status": "PASS",
        "failures": [],
        "source_context": rendered_context,
        "inputs": {
            "effective_fixtures": {key: fixtures_ref[key] for key in ("sha256", "size_bytes")},
            "native": {key: native_ref[key] for key in ("sha256", "size_bytes")},
            "network": {key: network_ref[key] for key in ("sha256", "size_bytes")},
            "pack": pack_measurement.as_dict(),
            "recording_inputs": {key: recording_ref[key] for key in ("sha256", "size_bytes")},
            "render_manifest": runtime_measurement.as_dict(),
            "road": {key: road_ref[key] for key in ("sha256", "size_bytes")},
            "routes": {key: routes_ref[key] for key in ("sha256", "size_bytes")},
            "source_osm": {key: source_osm_ref[key] for key in ("sha256", "size_bytes")},
            "traffic": {key: traffic_ref[key] for key in ("sha256", "size_bytes")},
            "workspace": {key: workspace_ref[key] for key in ("sha256", "size_bytes")},
        },
    }
    audit_ref = _write_relative(staging, "audit/motion.json", _json_bytes(motion_audit))
    audit_ref.update({"status": "PASS", "failures": 0})

    pack_ref = {
        "path": "pack/manifest.json",
        **pack_measurement.as_dict(),
        "tree_sha256": measure_tree(pack_root).sha256,
    }
    runtime_ref = {
        "path": "runtime/manifest.json",
        **runtime_measurement.as_dict(),
        "tree_sha256": measure_tree(runtime_root).sha256,
    }
    manifest = {
        "schema_version": "aero-bench.city-region-staging/v1",
        "region_id": REGION_ID,
        "state": "validated-private-staging-not-published",
        "artifact_class": "offline-engineering-preview-inputs-and-viewer-assets",
        "formal_run_evidence": False,
        "source_selection": selection_ref,
        "source_context": {
            "network": network_ref,
            "source_osm": source_osm_ref,
            "engineering_inputs": engineering_ref,
            "closed_loop_audit": {**closed_loop_ref, "status": "PASS"},
        },
        "render_assets": {
            "mesh_pack": pack_ref,
            "building_runtime": runtime_ref,
            "road": road_ref,
            "road_physical_clearance": clearance_ref,
            "effective_fixtures": fixtures_ref,
            "traffic": traffic_ref,
            "planned_flight": flight_ref,
        },
        "traffic_validation": {
            "workspace": workspace_ref,
            "independent_audit": audit_ref,
            "private_native_evidence": {
                "directory_mode": "0700",
                "native_recording": native_ref,
                "routes": routes_ref,
                "recording_inputs": recording_ref,
            },
        },
        "asset_authorization": {
            "scope": "internal-research-staging",
            "research_staging_blocked": False,
            "external_redistribution": "requires an explicit asset-ledger decision",
        },
    }
    manifest_path = staging / "staging.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    return {
        "candidate": candidate,
        "manifest": manifest_path,
        "objects": objects_path,
        "public": public,
        "staging": staging,
    }


def _run(paths: dict[str, Path], *, publish: bool = False) -> dict[str, Any]:
    return prepare_publication(
        staging_manifest=paths["manifest"],
        candidate_root=paths["candidate"],
        public_root=paths["public"],
        publication_slug="test-region",
        scene_url=SCENE_URL,
        scene_name="Test region engineering preview",
        environment_objects=paths["objects"],
        publish=publish,
    )


def test_stages_source_bound_scene_without_writing_public_tree(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    receipt = _run(paths)
    scene_path = paths["candidate"] / "public" / SCENE_URL.lstrip("/")
    scene = json.loads(scene_path.read_bytes())

    assert receipt["status"] == "candidate-byte-verified"
    assert receipt["scene"] == {"url": SCENE_URL, **measure_file(scene_path).as_dict()}
    assert scene["building_render"]["source_scene_id"] == REGION_ID
    assert scene["building_render"]["source_context"]["sha256"] == (
        receipt["runtime_source_context"]["sha256"]
    )
    assert scene["traffic_signal_model"]["url"] == "/models/signal.glb"
    assert scene["road_assets"]["traffic"]["url"] == (
        "/city-presentation/test-region-traffic-v2.json"
    )
    environment = json.loads((
        paths["candidate"] / "public/city-presentation/test-region-source-ground-cover-v1.json"
    ).read_bytes())
    assert environment["source"]["objectsSha256"] == measure_file(paths["objects"]).sha256
    assert environment["groundCovers"] == []
    assert not (paths["public"] / SCENE_URL.lstrip("/")).exists()
    assert _run(paths) == receipt  # Exact candidate reruns are idempotent.


def test_publish_is_idempotent_and_preserves_exact_trees(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    receipt = _run(paths, publish=True)
    public_scene = paths["public"] / SCENE_URL.lstrip("/")

    assert receipt["status"] == "published-byte-verified"
    assert measure_file(public_scene).sha256 == receipt["scene"]["sha256"]
    assert measure_tree(paths["public"] / f"osm2world/packs/{REGION_ID}") == (
        measure_tree(paths["staging"] / "pack")
    )
    assert measure_tree(paths["public"] / f"building-renders/{REGION_ID}") == (
        measure_tree(paths["staging"] / "runtime")
    )
    assert _run(paths, publish=True) == receipt


def test_public_collision_fails_before_any_target_is_copied(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    collision = paths["public"] / "city-presentation/test-region-road-v3.json"
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_bytes(b"another region")

    with pytest.raises(PublicationError, match="public collision"):
        _run(paths, publish=True)

    assert collision.read_bytes() == b"another region"
    assert not (paths["public"] / f"osm2world/packs/{REGION_ID}").exists()
    assert not (paths["candidate"] / "public").exists()


def test_runtime_revision_preserves_retained_runtime_and_source_identity(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    retained = paths["public"] / f"building-renders/{REGION_ID}/manifest.json"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"retained old runtime")
    receipt = prepare_publication(
        staging_manifest=paths["manifest"], candidate_root=paths["candidate"],
        public_root=paths["public"], publication_slug="test-region",
        scene_url=SCENE_URL, scene_name="Versioned runtime", environment_objects=paths["objects"],
        publish=True, runtime_revision="facade-middle-tiles-v2",
    )
    scene = json.loads((paths["public"] / SCENE_URL.lstrip("/")).read_bytes())
    assert retained.read_bytes() == b"retained old runtime"
    assert scene["building_render"]["source_scene_id"] == REGION_ID
    assert scene["building_render"]["base_url"] == f"/building-renders/{REGION_ID}-facade-middle-tiles-v2/"
    assert receipt["publication_guards"]["building_runtime_url"] == scene["building_render"]["base_url"]


def test_runtime_revision_rejects_path_escape(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    with pytest.raises(PublicationError, match="runtime revision"):
        prepare_publication(
            staging_manifest=paths["manifest"], candidate_root=paths["candidate"],
            public_root=paths["public"], publication_slug="test-region", scene_url=SCENE_URL,
            scene_name="Versioned runtime", environment_objects=paths["objects"],
            publish=True, runtime_revision="../escape",
        )


def test_rejects_changed_staged_bytes_and_non_pass_evidence(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    (paths["staging"] / "viewer/traffic.json").write_bytes(b"{}\n")
    with pytest.raises(PublicationError, match="sha256 mismatch"):
        _run(paths)

    paths = _fixture(tmp_path / "failed-audit")
    manifest = json.loads(paths["manifest"].read_bytes())
    failed = {"schema_version": "test", "final_status": "FAIL", "rounds": []}
    failed_path = paths["staging"] / "audit/closed-loop.json"
    failed_path.write_bytes(_json_bytes(failed))
    failed_ref = manifest["source_context"]["closed_loop_audit"]
    failed_ref.update(measure_file(failed_path).as_dict())
    failed_ref["status"] = "PASS"
    paths["manifest"].write_bytes(_json_bytes(manifest))

    with pytest.raises(PublicationError, match="closed-loop evidence is not PASS"):
        _run(paths)
