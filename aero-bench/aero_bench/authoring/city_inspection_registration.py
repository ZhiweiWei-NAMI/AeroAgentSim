"""Publish a self-contained catalog entry for the verified Huangpu inspection run."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from aero_bench.authoring.city_native_registration import (
    CityNativeInputLock,
    CityNativeReadinessReport,
    PinnedCityFile,
    assess_city_native_registration,
    load_city_native_input_lock,
    verify_city_native_registration,
)
from aero_bench.authoring.native_registry import (
    CITY_FULL_BLOCKING_CODES,
    CITY_FULL_UNSUPPORTED_DOMAINS,
    CITY_INSPECTION_DOMAINS,
    CITY_INSPECTION_PROFILE_ID,
    CITY_INSPECTION_PROVENANCE_SCHEMA,
    CITY_INSPECTION_REGISTRATION_SCHEMA,
    CityInspectionPublicationProvenance,
    CityInspectionSceneDefinition,
    NativeSceneRegistryManifest,
    RegisteredFile,
    verify_city_inspection_scene,
)
from aero_bench.authoring.workspace import CityOrderGeneration, CityWorkspaceDraft, WORKSPACE_SCHEMA
from aero_bench.config.loader import sha256_file
from aero_bench.config.models import FileRef
from aero_bench.providers.rpc import parse_json_object
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.contracts import PublicScenario
from aero_bench.trace.projector import project_public_scenario


@dataclass(frozen=True, slots=True)
class _PublicationFile:
    source: Path | None
    raw: bytes | None
    sha256: str
    size_bytes: int


def _file_ref(relative: str, raw: bytes) -> FileRef:
    return FileRef(path=relative, sha256=hashlib.sha256(raw).hexdigest())


def _registered(relative: str, raw: bytes) -> RegisteredFile:
    return RegisteredFile(file=_file_ref(relative, raw), size_bytes=len(raw))


def _checked_relative(root: Path, path: Path, label: str) -> tuple[str, Path]:
    root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    if any(
        part.is_symlink()
        for part in (candidate, *candidate.parents)
        if part.is_relative_to(root)
    ):
        raise ValueError(f"{label} resolves through a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label} is not one regular repository file")
    return resolved.relative_to(root).as_posix(), resolved


def _add_file(
    files: dict[str, _PublicationFile],
    *,
    root: Path,
    path: Path,
    destination: str | None = None,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    label: str,
) -> None:
    relative, source = _checked_relative(root, path, label)
    destination = relative if destination is None else FileRef(
        path=destination, sha256=expected_sha256 or sha256_file(source)
    ).path
    size = source.stat().st_size
    digest = sha256_file(source)
    if expected_size is not None and size != expected_size:
        raise ValueError(f"{label} size differs from its pin")
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"{label} digest differs from its pin")
    record = _PublicationFile(source=source, raw=None, sha256=digest, size_bytes=size)
    prior = files.get(destination)
    if prior is not None and (prior.sha256, prior.size_bytes) != (digest, size):
        raise ValueError(f"publication path has conflicting bytes: {destination}")
    files[destination] = record


def _add_raw(files: dict[str, _PublicationFile], relative: str, raw: bytes) -> None:
    reference = _file_ref(relative, raw)
    record = _PublicationFile(
        source=None, raw=raw, sha256=reference.sha256, size_bytes=len(raw)
    )
    prior = files.get(relative)
    if prior is not None and (prior.sha256, prior.size_bytes) != (
        record.sha256,
        record.size_bytes,
    ):
        raise ValueError(f"publication path has conflicting bytes: {relative}")
    files[relative] = record


def _add_pinned(
    files: dict[str, _PublicationFile], root: Path, pinned: PinnedCityFile, label: str,
) -> None:
    _add_file(
        files,
        root=root,
        path=Path(pinned.file.path),
        expected_sha256=pinned.file.sha256,
        expected_size=pinned.size_bytes,
        label=label,
    )


def _json(path: Path, label: str) -> dict[str, object]:
    try:
        return parse_json_object(path.read_bytes())
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def _source_closure(
    root: Path,
    lock: CityNativeInputLock,
    files: dict[str, _PublicationFile],
) -> None:
    """Collect every file the native-city source verifier reads transitively."""

    for index, pinned in enumerate(lock.pinned_files()):
        _add_pinned(files, root, pinned, f"city input {index}")

    scene_path = root / lock.scene_manifest.file.path
    scene = _json(scene_path, "compiled scene manifest")
    outputs = scene.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("compiled scene manifest outputs must be an array")
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            raise ValueError("compiled scene output must be an object")
        relative = output.get("path")
        digest = output.get("sha256")
        size = output.get("byte_size")
        if not isinstance(relative, str) or not isinstance(digest, str) or not isinstance(size, int):
            raise ValueError("compiled scene output pin is incomplete")
        _add_file(
            files,
            root=root,
            path=scene_path.parent / relative,
            expected_sha256=digest,
            expected_size=size,
            label=f"compiled scene output {index}",
        )

    fragment_path = root / lock.building_render_catalog.file.path
    fragment = _json(fragment_path, "building-render fragment")
    entries = fragment.get("building_render")
    digests = fragment.get("glb_digests")
    if not isinstance(entries, list) or not isinstance(digests, dict):
        raise ValueError("building-render fragment closure is incomplete")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("building-render entry must be an object")
        object_id = entry.get("object_id")
        source = entry.get("file")
        record = digests.get(object_id) if isinstance(object_id, str) else None
        if not isinstance(source, str) or not isinstance(record, dict):
            raise ValueError("building-render source pin is incomplete")
        digest = record.get("sha256")
        size = record.get("bytes")
        if not isinstance(digest, str) or not isinstance(size, int):
            raise ValueError("building-render digest pin is incomplete")
        _add_file(
            files,
            root=root,
            path=fragment_path.parent / source,
            expected_sha256=digest,
            expected_size=size,
            label=f"building-render source {index}",
        )

    mesh_path = root / lock.mesh_pack_manifest.file.path
    mesh = _json(mesh_path, "mesh-pack manifest")
    mesh_records: list[dict[str, object]] = []
    batches = mesh.get("batches")
    textures = mesh.get("textures")
    if not isinstance(batches, list) or not isinstance(textures, dict):
        raise ValueError("mesh-pack runtime closure is incomplete")
    for batch in batches:
        if not isinstance(batch, dict) or not isinstance(batch.get("file"), dict):
            raise ValueError("mesh-pack batch file pin is incomplete")
        mesh_records.append(batch["file"])
    for texture in textures.values():
        if not isinstance(texture, dict):
            raise ValueError("mesh-pack texture pin is incomplete")
        mesh_records.append(texture)
    for index, record in enumerate(mesh_records):
        digest = record.get("sha256")
        size = record.get("size_bytes")
        if not isinstance(digest, str) or not isinstance(size, int):
            raise ValueError("mesh-pack runtime asset pin is incomplete")
        _add_file(
            files,
            root=root,
            path=mesh_path.parent / "assets" / digest,
            expected_sha256=digest,
            expected_size=size,
            label=f"mesh-pack runtime asset {index}",
        )

    building_path = root / lock.building_render_manifest.file.path
    building = _json(building_path, "building runtime manifest")
    buildings = building.get("buildings")
    textures = building.get("textures")
    if not isinstance(buildings, list) or not isinstance(textures, list):
        raise ValueError("building runtime closure is incomplete")
    building_records = []
    for item in buildings:
        if not isinstance(item, dict) or not isinstance(item.get("derived_glb"), dict):
            raise ValueError("building runtime GLB pin is incomplete")
        building_records.append(item["derived_glb"])
    building_records.extend(textures)
    for index, record in enumerate(building_records):
        if not isinstance(record, dict):
            raise ValueError("building runtime asset pin must be an object")
        digest = record.get("sha256")
        size = record.get("bytes")
        relative = record.get("path", f"assets/{digest}")
        if not isinstance(digest, str) or not isinstance(size, int) or not isinstance(relative, str):
            raise ValueError("building runtime asset pin is incomplete")
        _add_file(
            files,
            root=root,
            path=building_path.parent / relative,
            expected_sha256=digest,
            expected_size=size,
            label=f"building runtime asset {index}",
        )


def _bundle_and_public_assets(
    root: Path,
    lock: CityNativeInputLock,
    scenario: PublicScenario,
    files: dict[str, _PublicationFile],
) -> None:
    if lock.world is None:
        raise ValueError("city inspection publication requires a native world binding")
    bundle = (root / lock.world.bundle_root).resolve(strict=True)
    if bundle.is_symlink() or not bundle.is_dir() or not bundle.is_relative_to(root):
        raise ValueError("native city bundle root is invalid")
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise ValueError("native city bundle contains a symlink")
        if path.is_file():
            _add_file(files, root=root, path=path, label="native city bundle file")

    # PublicScenario selectors are relative to the Suite bundle. Publish exact
    # root aliases for the asset endpoint while retaining the original nested
    # paths required by the unchanged input lock and Suite.
    for asset in scenario.assets:
        _add_file(
            files,
            root=root,
            path=bundle / asset.selector,
            destination=asset.selector,
            expected_sha256=asset.sha256,
            expected_size=asset.size_bytes,
            label=f"public scenario asset {asset.asset_id}",
        )
        if asset.license_sha256 is not None:
            if asset.license_selector is None or asset.license_size_bytes is None:
                raise ValueError("public scenario licence pin is incomplete")
            _add_file(
                files,
                root=root,
                path=bundle / asset.license_selector,
                destination=asset.license_selector,
                expected_sha256=asset.license_sha256,
                expected_size=asset.license_size_bytes,
                label=f"public scenario licence {asset.asset_id}",
            )


def _reference_draft(
    *, scene_path: str, name: str, battery_wh: float,
    reserve_ratio: float, verified,
) -> CityWorkspaceDraft:
    run = verified.run
    world = verified.world
    bindings = world.sumo.object_bindings if world.sumo is not None else ()
    return CityWorkspaceDraft.model_validate({
        "purpose": "scenario-authoring",
        "schema_version": WORKSPACE_SCHEMA,
        "name": name,
        "scenePath": scene_path,
        "seed": run.seed,
        "environment": {
            "cloudCover": 0,
            "precipitation": "none",
            "precipitationRateMmPerH": 0,
            "visibilityM": 10000,
            "windMps": 0,
            "windDirectionDeg": 0,
            "timeOfDay": "day",
            "reflectionsEnabled": True,
        },
        "fleet": [{
            "id": item.entity_id,
            "assetId": "model:holybro-x500",
            "count": 1,
            "homeFacilityId": None,
            "batteryWh": battery_wh,
            "reserveRatio": reserve_ratio,
        } for item in world.entities if item.kind == "uav"],
        "traffic": {
            "vehicles": sum(item.kind == "vehicle" for item in bindings),
            "pedestrians": sum(item.kind == "person" for item in bindings),
            "bicycles": 0,
        },
        "facilities": [],
        "airspace": [],
        "algorithms": {
            "mode": "centralized",
            "assignment": "external",
            "routing": "external",
            "energy": "external",
            "parameters": {"profile": CITY_INSPECTION_PROFILE_ID},
        },
        "deployment": {
            "executor": "docker_reference",
            "imageRef": run.agents[0].workload.runtime.image,
        },
        "events": [],
        "actionRules": [],
        "stateKeyframes": [],
        "labelRules": [],
        "orders": [],
        "orderGeneration": CityOrderGeneration.disabled(run.seed).model_dump(mode="json"),
        "performanceProfiles": [],
        "authoredLandscape": [],
    })


def _materialize(staging: Path, files: dict[str, _PublicationFile]) -> None:
    by_identity: dict[tuple[str, int], Path] = {}
    for relative, record in sorted(files.items()):
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        identity = (record.sha256, record.size_bytes)
        prior = by_identity.get(identity)
        if prior is not None:
            os.link(prior, destination)
        elif record.raw is not None:
            destination.write_bytes(record.raw)
            by_identity[identity] = destination
        elif record.source is not None:
            shutil.copyfile(record.source, destination)
            by_identity[identity] = destination
        else:
            raise AssertionError("publication file has no byte source")
        if destination.stat().st_size != record.size_bytes or sha256_file(destination) != record.sha256:
            raise ValueError(f"published file identity changed while copying: {relative}")


def publish_city_inspection_registration(
    *,
    repository_root: Path,
    source_input_lock: Path,
    source_readiness: Path,
    public_scenario: Path,
    output: Path,
    registration_id: str,
    scene_path: str,
    name: str,
    battery_wh: float,
    reserve_ratio: float,
) -> Path:
    """Publish exact current city inputs without starting or claiming a new run."""

    root = repository_root.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"publication output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    _source_lock_relative, source_lock_path = _checked_relative(
        root, source_input_lock, "source city input lock"
    )
    _source_readiness_relative, source_readiness_path = _checked_relative(
        root, source_readiness, "source city readiness"
    )
    _public_scenario_relative, public_scenario_path = _checked_relative(
        root, public_scenario, "public scenario"
    )

    source_lock = load_city_native_input_lock(source_lock_path)
    source_report = assess_city_native_registration(root, source_lock)
    recorded_source_report = CityNativeReadinessReport.model_validate(
        parse_json_object(source_readiness_path.read_bytes())
    )
    if recorded_source_report != source_report:
        raise ValueError("source readiness does not match the independently assessed lock")
    expected_full_domains = tuple(sorted((*CITY_INSPECTION_DOMAINS, *CITY_FULL_UNSUPPORTED_DOMAINS)))
    if (
        source_lock.required_execution_domains != expected_full_domains
        or source_report.status != "blocked"
        or source_report.blocking_codes != CITY_FULL_BLOCKING_CODES
    ):
        raise ValueError("source lock is not the broader city registration with exact blockers")

    inspection_raw = source_lock.model_dump(mode="json")
    inspection_raw["registration_id"] = registration_id
    inspection_raw["required_execution_domains"] = list(CITY_INSPECTION_DOMAINS)
    inspection_lock = CityNativeInputLock.model_validate(inspection_raw)
    verified = verify_city_native_registration(root, inspection_lock)
    if verified.run.task.task_id != CITY_INSPECTION_PROFILE_ID:
        raise ValueError("city Suite does not use the fixed Huangpu inspection task")

    scenario_raw = public_scenario_path.read_bytes()
    scenario = PublicScenario.model_validate(parse_json_object(scenario_raw))
    if scenario != project_public_scenario(verified.run):
        raise ValueError("public scenario differs from the exact current city run")

    source_lock_raw = source_lock_path.read_bytes()
    source_readiness_raw = source_readiness_path.read_bytes()
    inspection_lock_raw = canonical_json_bytes(inspection_lock.model_dump(mode="json"))
    inspection_readiness_raw = canonical_json_bytes(
        verified.report.model_dump(mode="json")
    )
    source_lock_record = _registered(
        "registration/source-city-native-input-lock.json", source_lock_raw
    )
    source_readiness_record = _registered(
        "registration/source-city-native-readiness.json", source_readiness_raw
    )
    inspection_lock_record = _registered(
        "registration/city-inspection-input-lock.json", inspection_lock_raw
    )
    inspection_readiness_record = _registered(
        "registration/city-inspection-readiness.json", inspection_readiness_raw
    )
    provenance = CityInspectionPublicationProvenance(
        schema_version=CITY_INSPECTION_PROVENANCE_SCHEMA,
        source_registration_id=source_lock.registration_id,
        source_registration_sha256=source_report.registration_sha256,
        source_input_lock_sha256=source_lock_record.file.sha256,
        source_readiness_sha256=source_readiness_record.file.sha256,
        source_status="blocked",
        source_blocking_codes=source_report.blocking_codes,
        inspection_registration_id=registration_id,
        inspection_registration_sha256=verified.report.registration_sha256,
        inspection_readiness_sha256=inspection_readiness_record.file.sha256,
        inspection_status="ready",
        profile_id=CITY_INSPECTION_PROFILE_ID,
        implemented_execution_domains=CITY_INSPECTION_DOMAINS,
        unsupported_full_city_domains=CITY_FULL_UNSUPPORTED_DOMAINS,
        base_run_id=verified.run.run_id,
        scenario_digest=verified.run.scenario.scenario_digest,
        world_id=verified.world.world_id,
        world_digest=verified.world.world_digest,
        publication_effect="copied-pinned-inputs-no-execution",
    )
    provenance_raw = canonical_json_bytes(provenance.model_dump(mode="json"))
    provenance_record = _registered(
        "registration/city-inspection-provenance.json", provenance_raw
    )

    files: dict[str, _PublicationFile] = {}
    _source_closure(root, inspection_lock, files)
    _bundle_and_public_assets(root, inspection_lock, scenario, files)
    scene_relative = PurePosixPath(scene_path.removeprefix("/")).as_posix()
    if scene_relative != scene_path.removeprefix("/"):
        raise ValueError("scene path must be normalized")
    _add_raw(files, scene_relative, scenario_raw)
    for registered, raw in (
        (source_lock_record, source_lock_raw),
        (source_readiness_record, source_readiness_raw),
        (inspection_lock_record, inspection_lock_raw),
        (inspection_readiness_record, inspection_readiness_raw),
        (provenance_record, provenance_raw),
    ):
        _add_raw(files, registered.file.path, raw)

    draft = _reference_draft(
        scene_path=scene_path,
        name=name,
        battery_wh=battery_wh,
        reserve_ratio=reserve_ratio,
        verified=verified,
    )
    inventory = tuple(
        RegisteredFile(
            file=FileRef(path=relative, sha256=record.sha256),
            size_bytes=record.size_bytes,
        )
        for relative, record in sorted(files.items())
    )
    definition = CityInspectionSceneDefinition(
        schema_version=CITY_INSPECTION_REGISTRATION_SCHEMA,
        registration_id=registration_id,
        profile_id=CITY_INSPECTION_PROFILE_ID,
        scene_path=scene_path,
        scene_document=_registered(scene_relative, scenario_raw),
        source_input_lock=source_lock_record,
        source_readiness=source_readiness_record,
        inspection_input_lock=inspection_lock_record,
        inspection_readiness=inspection_readiness_record,
        provenance=provenance_record,
        suite=inspection_lock.execution.suite.file,  # type: ignore[union-attr]
        world=inspection_lock.world.world_package.file,  # type: ignore[union-attr]
        world_id=verified.world.world_id,
        world_digest=verified.world.world_digest,
        files=inventory,
        reference_draft=draft,
    )

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        _materialize(staging, files)
        checked = verify_city_inspection_scene(staging, definition)
        if checked.run.run_id != verified.run.run_id:
            raise AssertionError("published city registration changed its base Run ID")
        definition_path = staging / "native-scene.json"
        definition_path.write_bytes(canonical_json_bytes(definition.model_dump(mode="json")))
        manifest = NativeSceneRegistryManifest(
            schema_version="aero-bench.native-scene-registry/v1",
            registrations=(FileRef(
                path="native-scene.json",
                sha256=sha256_file(definition_path),
            ),),
        )
        (staging / "native-scenes.json").write_bytes(
            canonical_json_bytes(manifest.model_dump(mode="json"))
        )
        (staging / "reference-workspace.json").write_bytes(
            canonical_json_bytes(draft.snapshot())
        )
        (staging / "registration-public.json").write_bytes(
            canonical_json_bytes(checked.public().model_dump(mode="json"))
        )
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output / "native-scenes.json"


__all__ = ["publish_city_inspection_registration"]
