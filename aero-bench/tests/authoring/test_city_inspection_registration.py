from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path
import tempfile

import pytest

from aero_bench.authoring.city_inspection_registration import (
    publish_city_inspection_registration,
)
from aero_bench.authoring.compilation_contracts import CityCompileRequest
from aero_bench.authoring.draft_compiler import (
    CityDraftCompiler,
    load_published_compilation,
)
from aero_bench.authoring.native_registry import (
    CITY_FULL_BLOCKING_CODES,
    CITY_FULL_UNSUPPORTED_DOMAINS,
    CITY_INSPECTION_DOMAINS,
    CityInspectionPublicationProvenance,
    NativeSceneRegistry,
    VerifiedCityInspectionScene,
)
from aero_bench.providers.rpc import parse_json_object
from aero_bench.trace.contracts import PublicScenario
from aero_bench.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
SOURCE_LOCK = ROOT / "validation/codex-takeover-20261001/B/huangpu-city-native-input-lock-v6.json"
SOURCE_READINESS = ROOT / "validation/codex-takeover-20261001/B/huangpu-city-native-readiness-v6.json"
PUBLIC_SCENARIO = ROOT / "validation/codex-takeover-20261001/B/huangpu-native-public-scenario-v6.json"
REGISTRATION_ID = "inspection.huangpu.native.v6"
BASE_RUN_ID = "44f2ebb7ba410a37041d6b8b74374a81899a13a80a8ff8c3199688ca635806da"
BASE_SCENARIO_DIGEST = "82aa5a3906dfde75d23060ab36fac23663762425296504f69786bb243a70998f"
WORLD_DIGEST = "18e3ac14954a515f85e0753ba050452a58fe933cbcfd1923fcd65b51b4560eed"
PUBLIC_SCENARIO_SHA256 = "61a152713a202f38a551c95136df439f9122ac473fece59d648f884a0b4c84b8"
SOURCE_LOCK_SHA256 = "2375aacc8dba5ed350cf476227e19fb1189b28794e175cb36069763d241291d2"
SOURCE_READINESS_SHA256 = "901e4f38d46eeea2770c521390e273aaf85a698ca4fdab60d538001fc56273c0"
REGISTRATION_SHA256 = "47601a5f5ae8718841bbd7fd36c3a2a80f6244284c33a7a01e4d8d104bb4e794"


@pytest.fixture(scope="module")
def city_publication(tmp_path_factory):
    output = tmp_path_factory.mktemp("city-inspection-publication") / "registered"
    # W1's capacity-based projector now selects indexed replay for this run.
    # Version the unit input; never rewrite the historical v6 artifact.
    prior = PublicScenario.model_validate(parse_json_object(PUBLIC_SCENARIO.read_bytes()))
    assert prior.replay_mode == "embedded"
    current = prior.model_copy(update={"replay_mode": "indexed"})
    current_raw = canonical_json_bytes(current.model_dump(mode="json"))
    assert hashlib.sha256(current_raw).hexdigest() == PUBLIC_SCENARIO_SHA256
    with tempfile.TemporaryDirectory(prefix=".city-catalog-current-", dir=Path(__file__).parent) as temporary:
        public_path = Path(temporary) / "public-scenario.json"
        public_path.write_bytes(current_raw)
        manifest = publish_city_inspection_registration(
            repository_root=ROOT,
            source_input_lock=SOURCE_LOCK,
            source_readiness=SOURCE_READINESS,
            public_scenario=public_path,
            output=output,
            registration_id=REGISTRATION_ID,
            scene_path="/city-presentation/huangpu-native-inspection-v6.json",
            name="Huangpu native inspection v6",
            battery_wh=500,
            reserve_ratio=0.2,
        )
    registry = NativeSceneRegistry.from_manifest(manifest)
    scene = registry.get(REGISTRATION_ID)
    assert isinstance(scene, VerifiedCityInspectionScene)
    return manifest, registry, scene


def _clone_with_hardlinks(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination, copy_function=os.link)
    return destination


def _request(scene: VerifiedCityInspectionScene) -> CityCompileRequest:
    return CityCompileRequest.model_validate({
        "schema_version": "aero-bench.city-compile-request/v1",
        "registration_id": scene.definition.registration_id,
        "registration_sha256": scene.registration_sha256,
        "draft": scene.definition.reference_draft.snapshot(),
    })


def test_publication_binds_exact_city_gate_and_native_presentation(city_publication):
    manifest, registry, scene = city_publication
    assert manifest.name == "native-scenes.json"
    assert scene.registration_sha256 == REGISTRATION_SHA256
    assert scene.run.run_id == BASE_RUN_ID
    assert scene.run.scenario.scenario_digest == BASE_SCENARIO_DIGEST
    assert scene.world.world_digest == WORLD_DIGEST
    assert scene.definition.source_input_lock.file.sha256 == SOURCE_LOCK_SHA256
    assert scene.definition.source_readiness.file.sha256 == SOURCE_READINESS_SHA256
    assert scene.city_registration.report.status == "ready"
    assert scene.city_registration.definition.required_execution_domains == CITY_INSPECTION_DOMAINS

    source_report = parse_json_object(
        (manifest.parent / scene.definition.source_readiness.file.path).read_bytes()
    )
    assert tuple(source_report["blocking_codes"]) == CITY_FULL_BLOCKING_CODES
    provenance = CityInspectionPublicationProvenance.model_validate(parse_json_object(
        (manifest.parent / scene.definition.provenance.file.path).read_bytes()
    ))
    assert provenance.source_status == "blocked"
    assert provenance.inspection_status == "ready"
    assert provenance.unsupported_full_city_domains == CITY_FULL_UNSUPPORTED_DOMAINS
    assert provenance.publication_effect == "copied-pinned-inputs-no-execution"

    raw = scene.public_scene_bytes()
    assert hashlib.sha256(raw).hexdigest() == PUBLIC_SCENARIO_SHA256
    scenario = PublicScenario.model_validate(parse_json_object(raw))
    assert len(scenario.layers) == 4
    assert {layer.kind for layer in scenario.layers} == {
        "entities", "osm_scene", "regions", "roads",
    }
    assert sum(asset.media_type == "model/gltf-binary" for asset in scenario.assets) == 414
    assert scenario.replay_mode == "indexed"
    assert registry.catalog().registrations[0] == scene.public()


def test_public_asset_reads_only_requested_projected_file(
    city_publication, monkeypatch,
):
    _manifest, _registry, scene = city_publication
    asset = next(
        asset for asset in PublicScenario.model_validate(
            parse_json_object(scene.public_scene_bytes())
        ).assets
        if asset.media_type == "model/gltf-binary"
    )

    def unexpected_full_inventory_read(_self):
        raise AssertionError("public asset request re-read the full registration")

    monkeypatch.setattr(
        VerifiedCityInspectionScene, "read_files", unexpected_full_inventory_read,
    )
    raw, media_type = scene.public_asset_bytes(asset.sha256)
    assert hashlib.sha256(raw).hexdigest() == asset.sha256
    assert len(raw) == asset.size_bytes
    assert media_type == "model/gltf-binary"

    private = next(
        asset for asset in scene.run.scenario.assets
        if asset.classification == "private"
    )
    with pytest.raises(KeyError, match="unknown public"):
        scene.public_asset_bytes(private.file.sha256)


@pytest.mark.parametrize("mutation", ["bytes", "symlink"])
def test_public_asset_rejects_tampered_or_linked_path(
    city_publication, tmp_path, mutation,
):
    manifest, _registry, scene = city_publication
    copied = _clone_with_hardlinks(manifest.parent, tmp_path / "registered")
    copied_scene = replace(scene, root=copied)
    asset = next(
        asset for asset in PublicScenario.model_validate(
            parse_json_object(scene.public_scene_bytes())
        ).assets
        if asset.media_type == "model/gltf-binary"
    )
    target = copied / asset.selector
    raw = target.read_bytes()
    target.unlink()
    if mutation == "bytes":
        target.write_bytes(raw + b"tampered")
        match = "sha256 mismatch"
    else:
        outside = tmp_path / "outside.glb"
        outside.write_bytes(raw)
        target.symlink_to(outside)
        match = "symlink"
    with pytest.raises(ValueError, match=match):
        copied_scene.public_asset_bytes(asset.sha256)


@pytest.mark.parametrize("relative,rejection", [
    ("registration/source-city-native-input-lock.json", "sha256 mismatch"),
    ((
        "validation/codex-takeover-20261001/B/huangpu-native-world-v4/"
        "configs/px4.json"
    ), "source city readiness differs from its independently assessed inputs"),
    ("validation/backend-track-b-20260930/reference-images-r5/images-lock.json",
     "sha256 mismatch"),
])
def test_registry_rejects_source_provider_and_image_drift(
    city_publication, tmp_path, relative, rejection,
):
    manifest, _registry, _scene = city_publication
    copied = _clone_with_hardlinks(manifest.parent, tmp_path / "registered")
    target = copied / relative
    raw = target.read_bytes()
    target.unlink()
    target.write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match=rejection):
        NativeSceneRegistry.from_manifest(copied / "native-scenes.json")


def test_city_compile_derives_snapshot_run_without_changing_native_bindings(
    city_publication, tmp_path,
):
    _manifest, registry, scene = city_publication
    compiler = CityDraftCompiler(tmp_path / "compilations", registry)
    result = compiler.compile(_request(scene))
    assert result.status == "compiled"
    assert result.suite is not None
    assert result.suite.path.endswith("/huangpu-native-world-v4/suite.yaml")
    assert result.runs[0].run_id != BASE_RUN_ID
    assert result.runs[0].scenario_digest != BASE_SCENARIO_DIGEST
    assert result.runs[0].world_digest == WORLD_DIGEST

    publication_root = compiler.output_root / result.compilation_id
    publication_digests = {
        path.relative_to(publication_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in publication_root.rglob("*") if path.is_file()
    }
    loaded, bundle, runs = load_published_compilation(
        compiler.output_root, result.compilation_id,
    )
    assert loaded == result and len(runs) == 1
    from aero_bench.executor.planning import build_execution_plan
    from aero_bench.providers.registry import builtin_provider_registry
    from aero_bench.tasks.registry import builtin_task_package_resolvers

    plan = build_execution_plan(
        runs[0], executor_kind="docker_reference", bundle_root=bundle,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    assert plan.run.run_id == result.runs[0].run_id
    assert Path(plan.bundle_root) == bundle
    assert (bundle / runs[0].task.instruction.path).is_file()
    assert {
        path.relative_to(publication_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in publication_root.rglob("*") if path.is_file()
    } == publication_digests
    derived = runs[0]
    base = scene.run
    assert derived.seed == base.seed
    assert derived.scenario.world_id == base.scenario.world_id
    assert derived.scenario.source_world_package == base.scenario.source_world_package
    assert derived.environment == base.environment
    assert derived.agents == base.agents
    base_assets = {asset.asset_id: asset for asset in base.scenario.assets}
    derived_assets = {asset.asset_id: asset for asset in derived.scenario.assets}
    snapshot = derived_assets.pop("asset.city-authoring-snapshot")
    assert derived_assets == base_assets
    assert snapshot.classification == "private"
    suite_root = bundle
    assert suite_root == (
        compiler.output_root / result.compilation_id / "bundle" / result.suite.path
    ).parent
    snapshot_path = suite_root / "authoring/workspace-snapshot.json"
    assert snapshot.file.path == "authoring/workspace-snapshot.json"
    assert parse_json_object(snapshot_path.read_bytes())["draft"] == (
        scene.definition.reference_draft.snapshot()
    )


def test_city_compile_retains_pointer_blockers(city_publication, tmp_path):
    _manifest, registry, scene = city_publication
    raw = _request(scene).model_dump(mode="json")
    raw["draft"]["traffic"]["vehicles"] -= 1
    raw["draft"]["fleet"] = []
    raw["draft"]["events"] = [{
        "id": "weather-change",
        "type": "weather.changed",
        "atS": 1,
        "targetId": "",
        "payload": {},
    }]
    compiler = CityDraftCompiler(tmp_path / "compilations", registry)
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "blocked"
    assert result.suite is None and result.runs == ()
    fields = {blocker.field for blocker in result.blockers}
    assert {
        "/draft/traffic/vehicles", "/draft/fleet", "/draft/events/0/type",
    }.issubset(fields)
