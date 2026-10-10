"""Source, identity, HTTP and immutable pack checks at the authoring boundary."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from pathlib import Path
from collections.abc import Iterator
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from aero_bench.authoring.api import make_server
from aero_bench.serialization import canonical_json_bytes
from aero_bench.authoring.publication import (
    PublicationError,
    ScenePackPublisher,
    _no_replace,
    _verified_pack,
    _verified_interchange,
    _validate_pack_shape,
    generator_identity,
)
from aero_bench.authoring.compiler import compile_scene_selection
from aero_bench.authoring.selection import SceneSelection, default_scene_source_registry


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA = "d0f3f30f846e8145aecd497e20ee95bc58e5cb0b3d4b4f46d1339e285217b847"


@pytest.fixture
def publisher(tmp_path: Path) -> Iterator[ScenePackPublisher]:
    instance = ScenePackPublisher(tmp_path / "published", repository_root=ROOT)
    try:
        yield instance
    finally:
        instance.close()


def _selection() -> SceneSelection:
    return SceneSelection.from_document({
        "schema_version": "aero-bench.scene-selection/v1",
        "source_id": "shanghai-central-osm-v1",
        "source_sha256": SOURCE_SHA,
        "origin": {
            "latitude_deg": 31.2304, "longitude_deg": 121.4737,
            "ellipsoid_height_m": 50.0, "geoid_undulation_m": 30.0, "amsl_m": 20.0,
        },
        "bounds_enu_m": {
            "min_east_m": -300.0, "max_east_m": 300.0,
            "min_north_m": -300.0, "max_north_m": 300.0,
        },
    })


def test_registered_catalog_and_raw_bytes(publisher: ScenePackPublisher) -> None:
    catalog = publisher.sources()
    assert catalog["schema_version"] == "aero-bench.scene-source-catalog/v1"
    assert len(catalog["sources"]) == 2
    source = catalog["sources"][0]
    assert source["sha256"] == SOURCE_SHA
    assert source["bounds_wgs84"] == {
        "min_latitude_deg": 31.2228, "max_latitude_deg": 31.2371,
        "min_longitude_deg": 121.4636, "max_longitude_deg": 121.4868,
    }
    raw = publisher.source_bytes(source["source_id"])
    assert raw == (ROOT / "frontend/public/osm2world/shanghai-hongqiao.osm.json").read_bytes()
    assert len(raw) == source["size_bytes"] == 4_868_022
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA
    second = catalog["sources"][1]
    assert second["source_id"] == "shanghai-jingan-osm-v1"
    assert second["display_name"] == "上海静安 OSM"
    assert hashlib.sha256(publisher.source_bytes(second["source_id"])).hexdigest() == second["sha256"]


def test_generator_identity_covers_complete_style_tree(publisher: ScenePackPublisher) -> None:
    source = default_scene_source_registry(ROOT).verify(_selection())
    identity = generator_identity(ROOT, source)
    assert identity["style"]["file_count"] == 1203
    assert identity["style"]["total_bytes"] == 31_301_278
    assert identity["style"]["sha256"] == "1fa86218edc94233ba35cb4774bf7d1be8d56ef60de5997161b4630cbcdd4a21"
    assert identity["files"]["frontend/public/osm2world/osm2world-core-web.mjs"]["sha256"]
    assert identity["files"]["frontend/src/osm2world/projection.ts"]["sha256"]
    assert identity["files"]["frontend/scripts/city_road_topology.py"]["sha256"]
    policy_path = ROOT / "aero_bench/world/ground_roads.py"
    assert identity["files"]["aero_bench/world/ground_roads.py"] == {
        "sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "size_bytes": policy_path.stat().st_size,
    }
    assert "frontend/src/map.ts" not in identity["files"]
    assert "frontend/src/city-presentation.ts" not in identity["files"]
    assert "frontend/scripts/authoring-studio-e2e.mjs" not in identity["files"]


def test_no_replace_does_not_overwrite_competitor(tmp_path: Path) -> None:
    staged, final = tmp_path / "staged", tmp_path / "published"
    staged.mkdir()
    final.mkdir()
    (final / "owner").write_text("existing")
    with pytest.raises(PublicationError, match="已存在"):
        _no_replace(staged, final)
    assert (final / "owner").read_text() == "existing"
    assert staged.exists()


def _copy_current_pack(pack: Path) -> dict:
    source = ROOT / "frontend/public/osm2world/packs/shanghai-huangpu-east-v1"
    # The retained engineering cache also contains historical manifests. A
    # fresh publication must contain the exact current manifest inventory.
    manifest_raw = (source / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assets_dir = pack / "assets"
    assets_dir.mkdir(parents=True)
    for name in ("manifest.json", "manifest.lock.json"):
        shutil.copyfile(source / name, pack / name)
    refs = [manifest["source"], *manifest["textures"].values(),
            *(batch["file"] for batch in manifest["batches"])]
    for digest in {hashlib.sha256(manifest_raw).hexdigest(), *(ref["sha256"] for ref in refs)}:
        shutil.copyfile(source / "assets" / digest, assets_dir / digest)
    return manifest


def test_real_pack_verification_rejects_tampered_asset(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    manifest = _copy_current_pack(pack)
    expected = manifest["source"]
    reference, assets = _verified_pack(
        pack, effective_sha=expected["sha256"], effective_size=expected["size_bytes"],
        origin=manifest["projection"]["origin"], root=ROOT,
    )
    assert reference["sha256"] and expected["sha256"] in assets
    target = pack / "assets" / expected["sha256"]
    target.write_bytes(b"tampered")
    with pytest.raises(PublicationError, match="资产 bytes"):
        _verified_pack(
            pack, effective_sha=expected["sha256"], effective_size=expected["size_bytes"],
            origin=manifest["projection"]["origin"], root=ROOT,
        )


def test_rehashed_malformed_manifest_cannot_publish(tmp_path: Path) -> None:
    source = ROOT / "frontend/public/osm2world/packs/shanghai-huangpu-east-v1"
    pack = tmp_path / "pack"
    shutil.copytree(source, pack)
    original_raw = (pack / "manifest.json").read_bytes()
    manifest = json.loads(original_raw)
    old_digest = hashlib.sha256(original_raw).hexdigest()
    manifest["objects"] = "broken"
    changed = (json.dumps(manifest, separators=(",", ":")) + "\n").encode()
    changed_ref = {"sha256": hashlib.sha256(changed).hexdigest(), "size_bytes": len(changed)}
    (pack / "manifest.json").write_bytes(changed)
    (pack / "manifest.lock.json").write_bytes(canonical_json_bytes(changed_ref))
    (pack / "assets" / old_digest).unlink()
    (pack / "assets" / changed_ref["sha256"]).write_bytes(changed)
    with pytest.raises(PublicationError, match="objects"):
        _verified_pack(
            pack, effective_sha=manifest["source"]["sha256"],
            effective_size=manifest["source"]["size_bytes"],
            origin=manifest["projection"]["origin"], root=ROOT,
        )


@pytest.mark.parametrize("field,value", [
    ("schema_version", "aero-bench.osm2world-source-coordinates/v0"),
    ("recipe", "unbound-coordinates"),
    ("storage", "untranslated"),
    ("earth_circumference_m", 40_000_000),
    ("native_point_quantization_m", True),
    ("source_json_sha256", "a" * 64),
    ("stored_translation_xz_m", [0]),
    ("stored_translation_xz_m", [0, float("inf")]),
    ("converter_origin", {"latitude_deg": 90, "longitude_deg": 0}),
    ("producer_sha256", "not-a-digest"),
])
def test_current_pack_coordinate_contract_is_required(field: str, value: object) -> None:
    source = ROOT / "frontend/public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json"
    manifest = json.loads(source.read_bytes())
    manifest["coordinate_contract"][field] = value
    with pytest.raises(PublicationError):
        _validate_pack_shape(manifest)


def test_pack_without_current_coordinate_contract_is_rejected() -> None:
    source = ROOT / "frontend/public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json"
    manifest = json.loads(source.read_bytes())
    del manifest["coordinate_contract"]
    with pytest.raises(PublicationError, match="manifest"):
        _validate_pack_shape(manifest)


def test_retained_compiler_interchange_is_complete_and_immutable(tmp_path: Path) -> None:
    selection = _selection()
    compiled = compile_scene_selection(selection, output_root=tmp_path / "compiled")
    effective = compiled.effective_osm_path.read_bytes()
    reference = _verified_interchange(
        compiled.output_dir, selection=selection,
        manifest_sha=compiled.manifest_sha256,
        effective_sha=compiled.effective_osm_sha256, effective_size=len(effective),
    )
    assert reference["path"] == "interchange/manifest.json"
    assert reference["sha256"] == compiled.manifest_sha256
    assert (compiled.output_dir / "osm/sumo-network.osm.json").is_file()
    assert (compiled.output_dir / "gazebo/scene.sdf").is_file()
    assert (compiled.output_dir / "audit/raw-closure.osm.json").is_file()
    sdf = compiled.output_dir / "gazebo/scene.sdf"
    sdf.write_bytes(sdf.read_bytes() + b"\n")
    with pytest.raises(PublicationError, match="编译输出文件摘要不符"):
        _verified_interchange(
            compiled.output_dir, selection=selection,
            manifest_sha=compiled.manifest_sha256,
            effective_sha=compiled.effective_osm_sha256, effective_size=len(effective),
        )


def test_status_recovers_immutable_receipt_after_restart(
    publisher: ScenePackPublisher, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _selection()
    identity = generator_identity(ROOT, publisher.registry.verify(selection))
    job_id = hashlib.sha256(canonical_json_bytes({
        "schema_version": "aero-bench.scene-build-identity/v1",
        "selection": selection.to_document(), "generator": identity,
    })).hexdigest()
    manifest_ref = {"sha256": "b" * 64, "size_bytes": 12}
    presentation_ref = {"sha256": "e" * 64, "size_bytes": 24}
    effective_sha = "c" * 64
    final = publisher.output_root / job_id
    final.mkdir()
    (final / "authoring-publication.json").write_bytes(canonical_json_bytes({
        "job_id": job_id, "selection": selection.to_document(), "identity": identity,
        "compiler_manifest_sha256": "d" * 64,
        "effective_osm": {"sha256": effective_sha, "size_bytes": 5},
        "manifest": manifest_ref,
        "presentation_manifest": presentation_ref,
        "interchange_manifest": {"path": "interchange/manifest.json", "sha256": "d" * 64, "size_bytes": 12},
    }))
    monkeypatch.setattr(
        "aero_bench.authoring.publication._verified_pack",
        lambda *args, **kwargs: (manifest_ref, {effective_sha}),
    )
    monkeypatch.setattr(
        "aero_bench.authoring.publication._verified_interchange",
        lambda *args, **kwargs: {"path": "interchange/manifest.json", "sha256": "d" * 64, "size_bytes": 12},
    )
    monkeypatch.setattr(
        "aero_bench.authoring.publication.verify_selected_presentation",
        lambda *args, **kwargs: (presentation_ref, {"f" * 64}),
    )
    publisher.close()
    restarted = ScenePackPublisher(publisher.output_root, repository_root=ROOT)
    try:
        recovered = restarted.status(job_id)
        assert recovered["state"] == "ready"
        assert recovered["pack"] == {
            "base_url": f"/authoring/v1/scenes/{job_id}/pack/",
            "manifest": manifest_ref, "source_sha256": effective_sha,
        }
        assert restarted.published_file(job_id, "manifest") == final / "manifest.json"
        assert recovered["presentation"] == {
            "base_url": f"/authoring/v1/scenes/{job_id}/presentation/",
            "manifest": presentation_ref,
        }
        assert restarted.published_file(job_id, "presentation_asset", "f" * 64) == (
            final / "presentation/assets" / ("f" * 64))
    finally:
        restarted.close()


def test_accepted_unfinished_job_survives_restart_as_explicit_failure(
    publisher: ScenePackPublisher, monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = threading.Event()
    started = threading.Event()

    def held_build(*args: object) -> None:
        started.set()
        waiting.wait(timeout=5)

    monkeypatch.setattr(publisher, "_build", held_build)
    accepted = publisher.submit(_selection())
    try:
        assert started.wait(timeout=5)
        assert (publisher.output_root / ".job-status" / f"{accepted['job_id']}.json").is_file()
        with pytest.raises(PublicationError, match="已有运行中的发布服务"):
            ScenePackPublisher(publisher.output_root, repository_root=ROOT)
        assert publisher.status(accepted["job_id"])["state"] == "queued"
        publisher.close()
        restarted = ScenePackPublisher(publisher.output_root, repository_root=ROOT)
        try:
            recovered = restarted.status(accepted["job_id"])
            assert recovered["state"] == "failed"
            assert recovered["pack"] is None
            assert recovered["error"] == {
                "code": "job_interrupted",
                "message": "作者服务重启，中断的场景构建需重新提交",
            }
            assert restarted.status(accepted["job_id"]) == recovered
        finally:
            restarted.close()
    finally:
        waiting.set()


def test_one_job_per_identity_under_concurrent_submission(
    publisher: ScenePackPublisher, monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = []

    def held_build(*args: object) -> None:
        calls.append(args)
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(publisher, "_build", held_build)
    first = publisher.submit(_selection())
    assert started.wait(timeout=5)
    second = publisher.submit(_selection())
    release.set()
    assert second == first
    assert len(calls) == 1


def test_meshing_failure_stays_failed_without_visible_pack(
    publisher: ScenePackPublisher, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_builder(*args: object) -> None:
        raise PublicationError("meshing_failed", "官方转换失败")

    monkeypatch.setattr(publisher, "_run_builder", fail_builder)
    job = publisher.submit(_selection())
    for _ in range(100):
        job = publisher.status(job["job_id"])
        if job["state"] == "failed":
            break
        time.sleep(0.05)
    assert job["state"] == "failed"
    assert job["compiler_manifest_sha256"] is not None
    assert job["pack"] is None
    assert job["error"] == {"code": "meshing_failed", "message": "官方转换失败"}
    assert not (publisher.output_root / job["job_id"]).exists()
    with pytest.raises(PublicationError, match="尚未发布"):
        publisher.published_file(job["job_id"], "manifest")


def test_http_catalog_job_and_pack_gate(
    publisher: ScenePackPublisher, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep the module HTTP test on the actual registered bytes; the official
    # converter is exercised separately in the end-to-end publication gate.
    monkeypatch.setattr(publisher, "_build", lambda *_: None)
    with make_server("127.0.0.1", 0, publisher) as server:
        local_http = build_opener(ProxyHandler({}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with local_http.open(base + "/authoring/v1/sources") as response:
                catalog = json.load(response)
            assert catalog["sources"][0]["data_url"] == "/authoring/v1/sources/shanghai-central-osm-v1"
            with local_http.open(base + catalog["sources"][0]["data_url"]) as response:
                raw = response.read()
                assert response.headers["ETag"] == f'"{SOURCE_SHA}"'
            assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA
            request = Request(
                base + "/authoring/v1/scenes",
                data=json.dumps(_selection().to_document()).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with local_http.open(request) as response:
                assert response.status == 202
                job = json.load(response)
            assert job["schema_version"] == "aero-bench.scene-build-job/v1"
            assert job["state"] == "queued" and job["pack"] is None and job["error"] is None
            assert job["source_sha256"] == SOURCE_SHA
            with pytest.raises(HTTPError) as blocked:
                local_http.open(base + f"/authoring/v1/scenes/{job['job_id']}/pack/manifest.json")
            assert blocked.value.code == 409
            assert json.load(blocked.value)["error"]["code"] == "pack_not_ready"
            with pytest.raises(HTTPError) as unknown:
                local_http.open(base + "/authoring/v1/sources/../../etc/passwd")
            assert unknown.value.code == 404
        finally:
            server.shutdown()
            thread.join(timeout=5)
