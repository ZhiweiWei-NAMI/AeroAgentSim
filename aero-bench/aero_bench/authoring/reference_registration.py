"""Publish an explicit reference registration from existing pinned native inputs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from aero_bench.authoring.native_registry import (
    NativeSceneDefinition, NativeSceneRegistryManifest, verify_native_scene,
)
from aero_bench.authoring.workspace import CityOrderGeneration, WORKSPACE_SCHEMA
from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import FileRef
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.projector import project_public_scenario
from aero_bench.world.contracts import WorldPackage


def _file_ref(root: Path, path: Path) -> FileRef:
    return FileRef(path=path.relative_to(root).as_posix(),
                   sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def _references(value: object) -> tuple[FileRef, ...]:
    """Collect strict file references, not runtime outputs or unpinned extras."""
    if isinstance(value, dict):
        if set(value) == {"path", "sha256"}:
            return (FileRef.model_validate(value),)
        return tuple(ref for item in value.values() for ref in _references(item))
    if isinstance(value, list):
        return tuple(ref for item in value for ref in _references(item))
    return ()


def publish_reference_registration(
    *, suite: Path, alignment: Path, output: Path, registration_id: str,
    scene_path: str, name: str, battery_wh: float, reserve_ratio: float,
) -> Path:
    """Copy verified inputs; publish existing PublicScenario, never a city substitute.

    Battery and reserve are explicit authoring declarations. The fixed native
    participant does not consume them as physical energy telemetry or policy.
    No execution, task acceptance, or city-preview compatibility is asserted.
    """
    loaded = load_suite(suite)
    reader = BundleReader(loaded.root)
    runs = resolve_suite(str(loaded.suite_path), executor_kind="docker_reference",
                         provider_registry=builtin_provider_registry(),
                         task_package_resolvers=builtin_task_package_resolvers())
    if len(runs) != 1 or len(loaded.suite.cases) != 1:
        raise ValueError("reference registration requires exactly one native run")
    run = runs[0]
    if run.task.task_id != "inspection.reference.v1":
        raise ValueError("this publisher accepts only the explicit inspection reference")
    alignment = alignment.resolve(strict=True)
    if not alignment.is_relative_to(loaded.root):
        raise ValueError("alignment must belong to the pinned native bundle")
    world_ref = loaded.suite.cases[0].world_package
    world = WorldPackage.model_validate(reader.load_document(world_ref))
    references = (*_references(loaded.suite.model_dump(mode="json")),
                  *_references(run.model_dump(mode="json")),
                  _file_ref(loaded.root, alignment))
    raw_files = {}
    for reference in references:
        target = loaded.root / reference.path
        if any(part.is_symlink() for part in (target, *target.parents)
               if part.is_relative_to(loaded.root)):
            raise ValueError("reference publication cannot copy symlinked input")
        raw = reader.resolve_file(reference).read_bytes()
        if reference.path in raw_files and raw_files[reference.path] != raw:
            raise ValueError("reference input has conflicting file identities")
        raw_files[reference.path] = raw
    if re.fullmatch(r"/city-presentation/[A-Za-z0-9_-]+\.json", scene_path) is None:
        raise ValueError("presentation must select an explicit city-presentation JSON path")
    public = project_public_scenario(run)
    scene_relative = scene_path.removeprefix("/")
    if scene_relative in raw_files:
        raise ValueError("reference presentation collides with a pinned input")
    raw_files[scene_relative] = canonical_json_bytes(public.model_dump(mode="json"))
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for relative, raw in sorted(raw_files.items()):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    files = [{"file": _file_ref(output, output / path).model_dump(mode="json"),
              "size_bytes": len(raw)} for path, raw in sorted(raw_files.items())]
    render_ids = {building.render_asset_id for building in world.buildings}
    presentation = [{
        "file": {"path": asset.artifact.selector.split("#", 1)[0],
                 "sha256": asset.artifact.sha256},
        "size_bytes": asset.byte_size, "world_asset_ids": [asset.artifact.artifact_id],
    } for asset in world.assets if asset.artifact.artifact_id in render_ids]
    bindings = world.sumo.object_bindings if world.sumo is not None else ()
    definition = NativeSceneDefinition.model_validate({
        "schema_version": "aero-bench.native-scene-registration/v1",
        "registration_id": registration_id, "profile_id": "inspection.reference.v1",
        "scene_path": scene_path,
        "scene_document": {"file": _file_ref(output, output / scene_relative).model_dump(mode="json"),
                           "size_bytes": len(raw_files[scene_relative])},
        "suite": run.suite.model_dump(mode="json"), "world": world_ref.model_dump(mode="json"),
        "alignment": _file_ref(output, output / alignment.relative_to(loaded.root)).model_dump(mode="json"),
        "world_id": world.world_id, "world_digest": world.world_digest,
        "files": files, "presentation": presentation,
        "reference_draft": {
            "purpose": "scenario-authoring", "schema_version": WORKSPACE_SCHEMA,
            "name": name, "seed": run.seed, "scenePath": scene_path,
            "environment": {"cloudCover": 0, "precipitation": "none", "precipitationRateMmPerH": 0,
                            "visibilityM": 10000, "windMps": 0, "windDirectionDeg": 0,
                            "timeOfDay": "day", "reflectionsEnabled": True},
            "fleet": [{"id": item.entity_id, "assetId": "model:holybro-x500", "count": 1,
                       "homeFacilityId": None, "batteryWh": battery_wh, "reserveRatio": reserve_ratio}
                      for item in world.entities if item.kind == "uav"],
            "traffic": {"vehicles": sum(item.kind == "vehicle" for item in bindings),
                        "pedestrians": sum(item.kind == "person" for item in bindings), "bicycles": 0},
            "facilities": [], "airspace": [],
            "algorithms": {"mode": "centralized", "assignment": "external", "routing": "external",
                           "energy": "external", "parameters": {"profile": "inspection.reference.v1"}},
            "deployment": {"executor": "docker_reference", "imageRef": run.agents[0].workload.runtime.image},
            "events": [], "actionRules": [], "stateKeyframes": [], "labelRules": [],
            "orders": [],
            "orderGeneration": CityOrderGeneration.disabled(run.seed).model_dump(mode="json"),
            "performanceProfiles": [], "authoredLandscape": [],
        },
    })
    verified = verify_native_scene(output, definition)
    definition_path = output / "native-scene.json"
    definition_path.write_bytes(canonical_json_bytes(definition.model_dump(mode="json")))
    manifest = NativeSceneRegistryManifest(
        schema_version="aero-bench.native-scene-registry/v1",
        registrations=(_file_ref(output, definition_path),),
    )
    manifest_path = output / "native-scenes.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    (output / "reference-workspace.json").write_bytes(canonical_json_bytes(definition.reference_draft.snapshot()))
    (output / "registration-public.json").write_bytes(canonical_json_bytes(verified.public().model_dump(mode="json")))
    return manifest_path
