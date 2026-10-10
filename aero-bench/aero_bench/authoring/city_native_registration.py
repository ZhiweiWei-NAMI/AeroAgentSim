"""Fail-closed registration gate for a source-derived native city run.

The Huangpu browser scene is an engineering presentation assembled from several
independently pinned products.  It is not, by itself, a ``WorldPackage`` or a
formal run.  This module verifies that the accepted source products still refer
to one scene and then assesses the additional contracts required for native
execution.  Preview trajectories are never treated as Provider evidence.

The registration stays separate from ``NativeSceneRegistry`` while that
registry is intentionally limited to ``inspection.reference.v1``.  A city can
be reported ready only when an exact native ``WorldPackage``, one formal Suite,
all declared Provider bindings, a Docker runner configuration, and the image
lock resolve together without substitution.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, TypeAlias
from xml.etree import ElementTree

from pydantic import Field, field_validator, model_validator

from aero_bench.config.loader import BundleReader, load_suite, sha256_file
from aero_bench.config.models import FileRef, Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.runner.contracts import RunnerConfig
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.integration import LOGISTICS_RUNTIME_IMPLEMENTED
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.building_render_merge import (
    ValidatedBuildingRenderFragment,
    validate_building_render_fragment,
)
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.scene_authoring import CatalogRoadWidth


CITY_INPUT_LOCK_SCHEMA_VERSION = "aero-bench.city-native-input-lock/v1"
CITY_READINESS_SCHEMA_VERSION = "aero-bench.city-native-readiness/v1"

_DIGEST_IMAGE = re.compile(r"^(?:[^@\s]+@)?sha256:[0-9a-f]{64}$")
_REQUIRED_PROVIDER_ADAPTERS = frozenset({"px4.gazebo", "sumo.traci", "ns3.rpc"})
_BUSINESS_PROVIDER_ADAPTERS = frozenset({"inspection.business", "logistics.business"})

CityNativeExecutionDomain: TypeAlias = Literal[
    "airspace-enforcement",
    "business",
    "flight",
    "network",
    "observation",
    "physical-logistics",
    "traffic",
    "weather-physics",
]
_BASE_EXECUTION_DOMAINS = frozenset(
    {"business", "flight", "network", "observation", "traffic"}
)


class CityNativeRegistrationError(ValueError):
    """The declared city source or execution registration is inconsistent."""


class CityNativeRegistrationBlocked(CityNativeRegistrationError):
    """The source lock is valid, but formal native prerequisites are absent."""


class PinnedCityFile(StrictModel):
    """One repository-relative file with exact bytes and byte count."""

    file: FileRef
    size_bytes: Annotated[int, Field(gt=0)]


class CityNativeWorldBinding(StrictModel):
    """Exact authored catalog and resulting native world package."""

    world_id: Identifier
    bundle_root: Annotated[str, Field(min_length=1)]
    authored_catalog: PinnedCityFile
    world_package: PinnedCityFile

    @field_validator("bundle_root")
    @classmethod
    def normalized_bundle_root(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path == PurePosixPath(".")
            or ".." in path.parts
            or value.strip() != value
            or str(path) != value
            or "\\" in value
            or "://" in value
        ):
            raise ValueError(
                "bundle_root must be normalized and relative to the repository root"
            )
        return value


class CityNativeExecutionBinding(StrictModel):
    """One exact formal run input set for the Docker Reference Executor."""

    task_id: Identifier
    suite: PinnedCityFile
    runner_config: PinnedCityFile
    image_lock: PinnedCityFile
    required_provider_adapters: tuple[Identifier, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def complete_provider_set(self) -> "CityNativeExecutionBinding":
        adapters = tuple(self.required_provider_adapters)
        if adapters != tuple(sorted(adapters)) or len(adapters) != len(set(adapters)):
            raise ValueError("required_provider_adapters must be unique and sorted")
        declared = set(adapters)
        missing = _REQUIRED_PROVIDER_ADAPTERS - declared
        if missing:
            raise ValueError(
                "city execution must declare physical flight, traffic, and network "
                f"adapters; missing {sorted(missing)}"
            )
        if not declared.intersection(_BUSINESS_PROVIDER_ADAPTERS):
            raise ValueError(
                "city execution must declare one implemented business adapter"
            )
        return self


class CityNativeInputLock(StrictModel):
    """Immutable identity of accepted Huangpu sources and optional native inputs."""

    schema_version: Literal["aero-bench.city-native-input-lock/v1"]
    registration_id: Identifier
    scene_id: Identifier
    required_execution_domains: tuple[CityNativeExecutionDomain, ...] = Field(
        min_length=5
    )

    raw_osm: PinnedCityFile
    scene_manifest: PinnedCityFile
    scene_objects: PinnedCityFile
    scene_effective_osm: PinnedCityFile
    mesh_pack_manifest: PinnedCityFile
    building_render_manifest: PinnedCityFile
    building_render_source_context: PinnedCityFile
    building_render_scaleout_manifest: PinnedCityFile
    building_render_catalog: PinnedCityFile
    road_width_catalog: PinnedCityFile
    road_width_network: PinnedCityFile

    road_source_osm: PinnedCityFile
    road_engineering_inputs: PinnedCityFile
    sumo_network: PinnedCityFile
    sumo_routes: PinnedCityFile
    source_context: PinnedCityFile
    road_geometry: PinnedCityFile
    effective_fixtures: PinnedCityFile
    environment_source: PinnedCityFile
    traffic_signal_model: PinnedCityFile
    street_lamp_model: PinnedCityFile
    traffic_preview: PinnedCityFile
    native_traffic_recording: PinnedCityFile
    native_recording_inputs: PinnedCityFile
    native_road_gate: PinnedCityFile
    flight_preview: PinnedCityFile
    presentation: PinnedCityFile

    world: CityNativeWorldBinding | None
    execution: CityNativeExecutionBinding | None

    def source_files(self) -> tuple[PinnedCityFile, ...]:
        """Return only the accepted city source products, in identity order."""

        return (
            self.raw_osm,
            self.scene_manifest,
            self.scene_objects,
            self.scene_effective_osm,
            self.mesh_pack_manifest,
            self.building_render_manifest,
            self.building_render_source_context,
            self.building_render_scaleout_manifest,
            self.building_render_catalog,
            self.road_width_catalog,
            self.road_width_network,
            self.road_source_osm,
            self.road_engineering_inputs,
            self.sumo_network,
            self.sumo_routes,
            self.source_context,
            self.road_geometry,
            self.effective_fixtures,
            self.environment_source,
            self.traffic_signal_model,
            self.street_lamp_model,
            self.traffic_preview,
            self.native_traffic_recording,
            self.native_recording_inputs,
            self.native_road_gate,
            self.flight_preview,
            self.presentation,
        )

    def pinned_files(self) -> tuple[PinnedCityFile, ...]:
        """Return every exact-byte input, including optional execution inputs."""

        files = self.source_files()
        if self.world is not None:
            files += (self.world.authored_catalog, self.world.world_package)
        if self.execution is not None:
            files += (
                self.execution.suite,
                self.execution.runner_config,
                self.execution.image_lock,
            )
        return files

    @model_validator(mode="after")
    def unique_file_paths(self) -> "CityNativeInputLock":
        domains = tuple(self.required_execution_domains)
        if domains != tuple(sorted(domains)) or len(domains) != len(set(domains)):
            raise ValueError("required_execution_domains must be unique and sorted")
        missing_domains = _BASE_EXECUTION_DOMAINS - set(domains)
        if missing_domains:
            raise ValueError(
                "native city execution must require the baseline execution domains; "
                f"missing {sorted(missing_domains)}"
            )
        paths = [item.file.path for item in self.pinned_files()]
        if len(paths) != len(set(paths)):
            raise ValueError("city input-lock file paths must be unique")
        return self


class CitySourceOrigin(StrictModel):
    latitude_deg: float
    longitude_deg: float
    ellipsoid_height_m: float
    amsl_m: float
    geoid_undulation_m: float


class CityNativeSourceEvidence(StrictModel):
    """Measured identities and counts from the verified source products."""

    scene_id: Identifier
    origin: CitySourceOrigin
    verified_file_count: Annotated[int, Field(gt=0)]
    verified_file_bytes: Annotated[int, Field(gt=0)]
    building_count: Annotated[int, Field(gt=0)]
    building_render_bytes: Annotated[int, Field(gt=0)]
    building_render_combined_sha256: Sha256
    building_runtime_asset_count: Annotated[int, Field(gt=0)]
    building_runtime_asset_bytes: Annotated[int, Field(gt=0)]
    mesh_runtime_asset_count: Annotated[int, Field(gt=0)]
    mesh_runtime_asset_bytes: Annotated[int, Field(gt=0)]
    compiled_road_count: Annotated[int, Field(gt=0)]
    road_width_count: Annotated[int, Field(gt=0)]
    effective_fixture_count: Annotated[int, Field(gt=0)]
    native_road_ribbons_checked: Annotated[int, Field(gt=0)]
    native_traffic_frame_count: Annotated[int, Field(gt=0)]
    flight_preview_frame_count: Annotated[int, Field(gt=0)]
    source_network_sha256: Sha256
    route_sha256: Sha256
    sumo_image_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    sumo_version: Annotated[str, Field(min_length=1)]
    traffic_artifact_class: Annotated[str, Field(min_length=1)]
    flight_preview_physical_simulation: bool
    provider_registry_adapters: tuple[Identifier, ...]
    weather_provider_registered: bool
    logistics_runtime_implemented: bool


class CityNativePrerequisite(StrictModel):
    code: Identifier
    status: Literal["ready", "missing", "unsupported", "mismatch"]
    detail: Annotated[str, Field(min_length=1)]


class CityNativeReadinessReport(StrictModel):
    schema_version: Literal["aero-bench.city-native-readiness/v1"]
    registration_id: Identifier
    registration_sha256: Sha256
    status: Literal["ready", "blocked"]
    required_execution_domains: tuple[CityNativeExecutionDomain, ...]
    source_evidence: CityNativeSourceEvidence
    prerequisites: tuple[CityNativePrerequisite, ...] = Field(min_length=1)
    blocking_codes: tuple[Identifier, ...]
    world_id: Identifier | None
    world_digest: Sha256 | None
    run_id: Sha256 | None

    @model_validator(mode="after")
    def status_matches_prerequisites(self) -> "CityNativeReadinessReport":
        derived = tuple(
            item.code for item in self.prerequisites if item.status != "ready"
        )
        if self.blocking_codes != derived:
            raise ValueError(
                "blocking_codes must preserve non-ready prerequisite order"
            )
        if (self.status == "ready") != (not self.blocking_codes):
            raise ValueError("readiness status disagrees with blocking_codes")
        if self.status == "ready" and (
            self.world_id is None or self.world_digest is None or self.run_id is None
        ):
            raise ValueError("ready registration requires world and run identities")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedCityNativeRegistration:
    root: Path
    definition: CityNativeInputLock
    report: CityNativeReadinessReport
    world: WorldPackage
    run: ResolvedRunSpec


@dataclass(frozen=True, slots=True)
class _VerifiedSources:
    evidence: CityNativeSourceEvidence
    documents: dict[str, dict[str, object]]
    renders: ValidatedBuildingRenderFragment
    required_world_assets: tuple[tuple[Sha256, int], ...]


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CityNativeRegistrationError(f"{label} must be a JSON object")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise CityNativeRegistrationError(
                f"duplicate JSON object key {key!r} is not allowed"
            )
        document[key] = value
    return document


def load_city_native_input_lock(path: Path) -> CityNativeInputLock:
    """Load one strict input lock without allowing duplicate JSON keys."""

    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise CityNativeRegistrationError(
            f"city input lock cannot be read: {source}: {exc}"
        ) from exc
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise CityNativeRegistrationError(
            f"city input lock is not UTF-8: {source}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CityNativeRegistrationError(
            f"city input lock is not valid JSON: {source}: {exc}"
        ) from exc
    try:
        return CityNativeInputLock.model_validate(document)
    except ValueError as exc:
        if isinstance(exc, CityNativeRegistrationError):
            raise
        raise CityNativeRegistrationError(
            f"city input lock violates its strict contract: {exc}"
        ) from exc


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CityNativeRegistrationError(f"{label} must be an array")
    return value


def _expect(condition: bool, detail: str) -> None:
    if not condition:
        raise CityNativeRegistrationError(detail)


def _nested(document: dict[str, object], *path: str) -> object:
    value: object = document
    traversed: list[str] = []
    for key in path:
        traversed.append(key)
        if not isinstance(value, dict) or key not in value:
            raise CityNativeRegistrationError(
                f"missing required field {'.'.join(traversed)}"
            )
        value = value[key]
    return value


def _read_pinned_files(
    root: Path, definition: CityNativeInputLock
) -> tuple[BundleReader, dict[str, Path]]:
    reader = BundleReader(root)
    paths: dict[str, Path] = {}
    for item in definition.pinned_files():
        candidate = root / item.file.path
        if any(
            part.is_symlink()
            for part in (candidate, *candidate.parents)
            if part.is_relative_to(root)
        ):
            raise CityNativeRegistrationError(
                f"city input lock contains a symlink: {item.file.path}"
            )
        try:
            path = reader.resolve_file(item.file)
        except ValueError as exc:
            raise CityNativeRegistrationError(str(exc)) from exc
        size = path.stat().st_size
        if size != item.size_bytes:
            raise CityNativeRegistrationError(
                f"size mismatch for {item.file.path}: expected {item.size_bytes}, got {size}"
            )
        paths[item.file.path] = path
    return reader, paths


def _document(
    reader: BundleReader, pinned: PinnedCityFile, label: str
) -> dict[str, object]:
    try:
        return _require_mapping(reader.load_document(pinned.file), label)
    except ValueError as exc:
        if isinstance(exc, CityNativeRegistrationError):
            raise
        raise CityNativeRegistrationError(f"invalid {label}: {exc}") from exc


def _scene_output(
    scene_manifest: dict[str, object], relative: str
) -> dict[str, object]:
    outputs = _require_list(scene_manifest.get("outputs"), "scene manifest outputs")
    matches = [
        _require_mapping(item, "scene manifest output")
        for item in outputs
        if isinstance(item, dict) and item.get("path") == relative
    ]
    if len(matches) != 1:
        raise CityNativeRegistrationError(
            f"scene manifest must contain one output for {relative!r}"
        )
    return matches[0]


def _verify_runtime_asset(
    *,
    root: Path,
    reader: BundleReader,
    base: Path,
    record: dict[str, object],
    label: str,
    inferred_path: str | None = None,
    size_field: str = "bytes",
) -> int:
    digest = record.get("sha256")
    size = record.get(size_field)
    relative = inferred_path if inferred_path is not None else record.get("path")
    if (
        not isinstance(digest, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not isinstance(relative, str)
        or relative != f"assets/{digest}"
    ):
        raise CityNativeRegistrationError(
            f"{label} must be one exact content-addressed asset record"
        )
    repository_relative = (base / relative).as_posix()
    candidate = root / repository_relative
    if any(
        part.is_symlink()
        for part in (candidate, *candidate.parents)
        if part.is_relative_to(root)
    ):
        raise CityNativeRegistrationError(
            f"{label} resolves through a symlink: {repository_relative}"
        )
    try:
        path = reader.resolve_file(
            FileRef.model_validate({"path": repository_relative, "sha256": digest})
        )
    except ValueError as exc:
        raise CityNativeRegistrationError(f"invalid {label}: {exc}") from exc
    if path.stat().st_size != size:
        raise CityNativeRegistrationError(
            f"size mismatch for {label}: expected {size}, got {path.stat().st_size}"
        )
    return size


def _verify_scene_sources(
    *,
    root: Path,
    definition: CityNativeInputLock,
    reader: BundleReader,
) -> _VerifiedSources:
    documents = {
        "scene_manifest": _document(
            reader, definition.scene_manifest, "scene manifest"
        ),
        "scene_objects": _document(reader, definition.scene_objects, "scene objects"),
        "mesh_pack": _document(
            reader, definition.mesh_pack_manifest, "mesh-pack manifest"
        ),
        "building_manifest": _document(
            reader, definition.building_render_manifest, "building-render manifest"
        ),
        "building_source_context": _document(
            reader,
            definition.building_render_source_context,
            "building-render source context",
        ),
        "building_scaleout": _document(
            reader,
            definition.building_render_scaleout_manifest,
            "building-render scale-out manifest",
        ),
        "road_width": _document(
            reader, definition.road_width_catalog, "road-width catalog"
        ),
        "road_engineering": _document(
            reader, definition.road_engineering_inputs, "road engineering inputs"
        ),
        "source_context": _document(
            reader, definition.source_context, "source context"
        ),
        "road": _document(reader, definition.road_geometry, "road geometry"),
        "fixtures": _document(
            reader, definition.effective_fixtures, "effective fixtures"
        ),
        "environment": _document(
            reader, definition.environment_source, "environment source"
        ),
        "traffic": _document(reader, definition.traffic_preview, "traffic preview"),
        "recording": _document(
            reader, definition.native_traffic_recording, "native SUMO recording"
        ),
        "recording_inputs": _document(
            reader, definition.native_recording_inputs, "native recording inputs"
        ),
        "road_gate": _document(reader, definition.native_road_gate, "native road gate"),
        "flight": _document(reader, definition.flight_preview, "flight preview"),
        "presentation": _document(reader, definition.presentation, "presentation"),
    }

    manifest = documents["scene_manifest"]
    _expect(
        manifest.get("schema_version") == "aero-bench.urban-scene-compiler/v1",
        "compiled scene uses an unsupported schema",
    )
    source = _require_mapping(manifest.get("source"), "scene manifest source")
    _expect(
        (source.get("sha256"), source.get("byte_size"))
        == (definition.raw_osm.file.sha256, definition.raw_osm.size_bytes),
        "compiled scene source does not match the pinned raw OSM",
    )
    for relative, pinned in (
        ("metadata/objects.json", definition.scene_objects),
        ("osm/effective.osm.json", definition.scene_effective_osm),
    ):
        output = _scene_output(manifest, relative)
        _expect(
            (output.get("sha256"), output.get("byte_size"))
            == (pinned.file.sha256, pinned.size_bytes),
            f"compiled scene output identity differs for {relative}",
        )
    origin = _require_mapping(manifest.get("origin"), "scene manifest origin")
    origin_model = CitySourceOrigin.model_validate(
        {field: origin.get(field) for field in CitySourceOrigin.model_fields}
    )

    objects = documents["scene_objects"]
    _expect(
        objects.get("schema_version") == "aero-bench.urban-scene-objects/v1",
        "compiled scene objects use an unsupported schema",
    )
    buildings = _require_list(objects.get("buildings"), "scene buildings")
    roads = _require_list(objects.get("roads"), "scene roads")
    building_ids = {
        _require_mapping(item, "scene building").get("object_id") for item in buildings
    }
    road_ids = {_require_mapping(item, "scene road").get("object_id") for item in roads}
    _expect(
        all(isinstance(item, str) and item for item in building_ids)
        and len(building_ids) == len(buildings),
        "scene building object_ids must be present and unique",
    )
    _expect(
        all(isinstance(item, str) and item for item in road_ids)
        and len(road_ids) == len(roads),
        "scene road object_ids must be present and unique",
    )

    mesh = documents["mesh_pack"]
    _expect(
        mesh.get("schema_version") == "aero-bench.osm2world-mesh-pack/v1",
        "mesh pack uses an unsupported schema",
    )
    mesh_source = _require_mapping(mesh.get("source"), "mesh-pack source")
    _expect(
        (mesh_source.get("sha256"), mesh_source.get("size_bytes"))
        == (
            definition.scene_effective_osm.file.sha256,
            definition.scene_effective_osm.size_bytes,
        ),
        "mesh pack is not derived from the pinned effective OSM",
    )
    mesh_origin = _require_mapping(
        _require_mapping(mesh.get("projection"), "mesh projection").get("origin"),
        "mesh projection origin",
    )
    _expect(
        mesh_origin.get("latitude_deg") == origin_model.latitude_deg
        and mesh_origin.get("longitude_deg") == origin_model.longitude_deg,
        "mesh-pack origin differs from the compiled scene",
    )
    mesh_base = Path(definition.mesh_pack_manifest.file.path).parent
    mesh_records: list[tuple[dict[str, object], str]] = []
    for index, item in enumerate(_require_list(mesh.get("batches"), "mesh batches")):
        batch = _require_mapping(item, f"mesh batch {index}")
        mesh_records.append(
            (
                _require_mapping(batch.get("file"), f"mesh batch {index} file"),
                f"mesh batch {index}",
            )
        )
    mesh_textures = _require_mapping(mesh.get("textures"), "mesh textures")
    for source_name, item in sorted(mesh_textures.items()):
        mesh_records.append(
            (
                _require_mapping(item, f"mesh texture {source_name}"),
                f"mesh texture {source_name}",
            )
        )
    mesh_digests = [item.get("sha256") for item, _label in mesh_records]
    _expect(
        mesh_records
        and all(isinstance(item, str) for item in mesh_digests)
        and len(mesh_digests) == len(set(mesh_digests)),
        "mesh runtime assets must have unique content digests",
    )
    mesh_runtime_bytes = sum(
        _verify_runtime_asset(
            root=root,
            reader=reader,
            base=mesh_base,
            record=item,
            label=label,
            inferred_path=f"assets/{item.get('sha256')}",
            size_field="size_bytes",
        )
        for item, label in mesh_records
    )
    mesh_runtime_count = len(mesh_records)

    scene_root = (root / definition.scene_manifest.file.path).parent
    try:
        renders = validate_building_render_fragment(
            fragment_path=root / definition.building_render_catalog.file.path,
            scene_root=scene_root,
        )
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"building-render source validation failed: {exc}"
        ) from exc
    _expect(
        renders.fragment_sha256 == definition.building_render_catalog.file.sha256,
        "building-render fragment digest drifted during validation",
    )
    _expect(
        len(renders.entries) == len(buildings),
        "building-render coverage differs from the compiled scene",
    )

    building_manifest = documents["building_manifest"]
    _expect(
        building_manifest.get("schema_version")
        == "aero-bench.building-render-runtime/v2",
        "building runtime manifest uses an unsupported schema",
    )
    building_scene = _require_mapping(
        building_manifest.get("scene"), "building runtime scene"
    )
    building_sources = _require_mapping(
        building_manifest.get("sources"), "building runtime sources"
    )
    building_counts = _require_mapping(
        building_manifest.get("counts"), "building runtime counts"
    )
    _expect(
        building_scene.get("id") == definition.scene_id,
        "building runtime scene_id differs from the input lock",
    )
    _expect(
        building_scene.get("objects_json_sha256")
        == definition.scene_objects.file.sha256
        and building_scene.get("mesh_pack_manifest_sha256")
        == definition.mesh_pack_manifest.file.sha256
        and building_scene.get("mesh_pack_source_sha256")
        == definition.scene_effective_osm.file.sha256,
        "building runtime source identity differs from the compiled scene",
    )
    _expect(
        building_sources.get("catalog_fragment_sha256")
        == definition.building_render_catalog.file.sha256,
        "building runtime does not bind the pinned 414-building fragment",
    )
    render_total_bytes = sum(item.byte_size for item in renders.staged)
    _expect(
        building_counts.get("buildings") == len(buildings)
        and building_counts.get("total_bytes") == render_total_bytes,
        "building runtime counts differ from the validated render bytes",
    )

    scaleout = documents["building_scaleout"]
    scaleout_scene = _require_mapping(scaleout.get("scene"), "building scale-out scene")
    scaleout_totals = _require_mapping(
        scaleout.get("totals"), "building scale-out totals"
    )
    scaleout_buildings = _require_list(
        scaleout.get("buildings"), "building scale-out buildings"
    )
    scaleout_ids = {
        _require_mapping(item, "building scale-out entry").get("object_id")
        for item in scaleout_buildings
    }
    _expect(
        scaleout.get("schema") == "aero-bench.building-render-scaleout/v0"
        and scaleout_scene.get("objects_json_sha256")
        == definition.scene_objects.file.sha256
        and scaleout_scene.get("building_count") == len(buildings),
        "building scale-out manifest differs from the compiled scene",
    )
    _expect(
        scaleout_ids == building_ids
        and scaleout_totals.get("glb_count") == len(renders.staged)
        and scaleout_totals.get("glb_bytes") == render_total_bytes
        and scaleout_totals.get("exceptions") == 0,
        "building scale-out manifest differs from the validated GLB set",
    )

    building_context = documents["building_source_context"]
    context_scene = _require_mapping(
        building_context.get("scene"), "building source-context scene"
    )
    context_sources = _require_mapping(
        building_context.get("sources"), "building source-context sources"
    )
    context_buildings = _require_list(
        building_context.get("buildings"), "building source-context buildings"
    )
    context_by_id = {
        _require_mapping(item, "building source-context entry").get(
            "object_id"
        ): _require_mapping(item, "building source-context entry")
        for item in context_buildings
    }
    _expect(
        building_context.get("schema_version")
        == "aero-bench.building-render-source-context/v1"
        and context_scene == building_scene
        and context_sources == building_sources,
        "building source context differs from the runtime manifest",
    )
    _expect(
        len(context_by_id) == len(context_buildings)
        and set(context_by_id) == building_ids
        and all(
            isinstance(context_by_id[item.object_id].get("glb"), dict)
            and context_by_id[item.object_id]["glb"].get("sha256") == item.sha256
            and context_by_id[item.object_id]["glb"].get("bytes") == item.byte_size
            for item in renders.staged
        ),
        "building source context differs from the validated per-building GLBs",
    )
    _expect(
        building_sources.get("scaleout_manifest_sha256")
        == definition.building_render_scaleout_manifest.file.sha256,
        "building runtime does not bind the pinned scale-out manifest",
    )
    runtime_buildings = _require_list(
        building_manifest.get("buildings"), "building runtime entries"
    )
    runtime_by_id = {
        _require_mapping(item, "building runtime entry").get(
            "object_id"
        ): _require_mapping(item, "building runtime entry")
        for item in runtime_buildings
    }
    _expect(
        len(runtime_by_id) == len(runtime_buildings)
        and set(runtime_by_id) == building_ids,
        "building runtime entries do not cover the compiled building set",
    )
    staged_by_id = {item.object_id: item for item in renders.staged}
    runtime_asset_bytes = 0
    runtime_base = Path(definition.building_render_manifest.file.path).parent
    for object_id in sorted(building_ids):
        runtime_entry = runtime_by_id[object_id]
        source_glb = _require_mapping(
            runtime_entry.get("glb"), f"building runtime {object_id} source GLB"
        )
        context_glb = _require_mapping(
            context_by_id[object_id].get("glb"),
            f"building source-context {object_id} GLB",
        )
        staged = staged_by_id[object_id]
        _expect(
            source_glb == context_glb
            and source_glb.get("sha256") == staged.sha256
            and source_glb.get("bytes") == staged.byte_size,
            f"building runtime {object_id} differs from its validated source GLB",
        )
        runtime_asset_bytes += _verify_runtime_asset(
            root=root,
            reader=reader,
            base=runtime_base,
            record=_require_mapping(
                runtime_entry.get("derived_glb"),
                f"building runtime {object_id} derived GLB",
            ),
            label=f"building runtime {object_id} derived GLB",
        )
    textures = _require_list(
        building_manifest.get("textures"), "building runtime textures"
    )
    for index, item in enumerate(textures):
        texture = _require_mapping(item, f"building runtime texture {index}")
        digest = texture.get("sha256")
        runtime_asset_bytes += _verify_runtime_asset(
            root=root,
            reader=reader,
            base=runtime_base,
            record=texture,
            label=f"building runtime texture {index}",
            inferred_path=f"assets/{digest}",
        )
    derived_total = building_counts.get("derived_total_bytes")
    texture_total = building_counts.get("texture_bytes")
    _expect(
        isinstance(derived_total, int)
        and not isinstance(derived_total, bool)
        and isinstance(texture_total, int)
        and not isinstance(texture_total, bool)
        and derived_total + texture_total == runtime_asset_bytes,
        "building runtime asset byte counts differ from the verified files",
    )
    runtime_asset_count = len(runtime_buildings) + len(textures)

    road_width = documents["road_width"]
    _expect(
        road_width.get("schema_version") == "aero-bench.road-width-catalog/v1",
        "road-width catalog uses an unsupported schema",
    )
    _expect(
        road_width.get("source_scene_manifest_sha256")
        == definition.scene_manifest.file.sha256,
        "road-width catalog is not bound to the compiled scene manifest",
    )
    _expect(
        road_width.get("source_sumo_network_sha256")
        == definition.road_width_network.file.sha256,
        "road-width catalog is not bound to its pinned SUMO network",
    )
    synthetic_osm = _scene_output(manifest, "osm/sumo-network.osm")
    _expect(
        road_width.get("source_synthetic_osm_sha256") == synthetic_osm.get("sha256"),
        "road-width catalog synthetic OSM identity differs from the compiled scene",
    )
    width_entries_raw = _require_list(
        road_width.get("road_width_m"), "road-width entries"
    )
    try:
        width_entries = tuple(
            CatalogRoadWidth.model_validate(item) for item in width_entries_raw
        )
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"road-width catalog violates the authoring contract: {exc}"
        ) from exc
    _expect(
        {item.object_id for item in width_entries} == road_ids
        and road_width.get("road_count") == len(roads)
        and road_width.get("unmatched_road_ids") == [],
        "road-width catalog does not cover the compiled road set exactly",
    )

    engineering = documents["road_engineering"]
    _expect(
        engineering.get("schema_version")
        == "aero-bench.urban-engineering-traffic-inputs/v1",
        "road engineering inputs use an unsupported schema",
    )
    _expect(
        engineering.get("source_osm_sha256") == definition.road_source_osm.file.sha256
        and engineering.get("network_sha256") == definition.sumo_network.file.sha256,
        "canonical road engineering inputs do not bind the pinned OSM/network",
    )

    context = documents["source_context"]
    _expect(
        context.get("schema_version") == "aero-bench.city-rendered-source-context/v1"
        and context.get("scene_id") == definition.scene_id,
        "rendered source context has the wrong schema or scene_id",
    )
    expected_context_fields = {
        "objects_json_sha256": definition.scene_objects.file.sha256,
        "building_render_manifest_sha256": definition.building_render_manifest.file.sha256,
        "mesh_pack_manifest_sha256": definition.mesh_pack_manifest.file.sha256,
        "mesh_pack_source_sha256": definition.scene_effective_osm.file.sha256,
        "source_network_sha256": definition.sumo_network.file.sha256,
        "source_osm_sha256": definition.road_source_osm.file.sha256,
        "engineering_inputs_sha256": definition.road_engineering_inputs.file.sha256,
    }
    _expect(
        all(
            context.get(key) == value for key, value in expected_context_fields.items()
        ),
        "rendered source context does not bind the complete pinned source set",
    )
    context_origin = _require_mapping(context.get("origin_wgs84"), "source origin")
    _expect(
        all(
            context_origin.get(key) == getattr(origin_model, key)
            for key in CitySourceOrigin.model_fields
        ),
        "rendered source context origin differs from the compiled scene",
    )
    for name in (
        "road",
        "fixtures",
        "traffic",
        "recording",
        "recording_inputs",
        "road_gate",
        "flight",
    ):
        _expect(
            documents[name].get("source_context") == context,
            f"{name} source context differs from the accepted shared context",
        )

    road = documents["road"]
    _expect(
        road.get("schema_version") == "aero-bench.city-road-preview/v3"
        and road.get("source_network_sha256") == definition.sumo_network.file.sha256
        and road.get("source_osm_sha256") == definition.road_source_osm.file.sha256,
        "accepted road geometry does not bind the pinned native road inputs",
    )
    fixtures = documents["fixtures"]
    fixture_counts = _require_mapping(fixtures.get("counts"), "fixture counts")
    fixture_models = _require_mapping(fixtures.get("models"), "fixture models")
    _expect(
        _nested(fixture_models, "signal", "sha256")
        == definition.traffic_signal_model.file.sha256
        and _nested(fixture_models, "signal", "size_bytes")
        == definition.traffic_signal_model.size_bytes
        and _nested(fixture_models, "street_lamp", "sha256")
        == definition.street_lamp_model.file.sha256
        and _nested(fixture_models, "street_lamp", "size_bytes")
        == definition.street_lamp_model.size_bytes,
        "effective fixtures do not bind the pinned model bytes",
    )

    environment = documents["environment"]
    _expect(
        environment.get("schemaVersion") == "aero-bench.city-environment-source/v1",
        "environment source uses an unsupported schema",
    )
    environment_source = _require_mapping(
        environment.get("source"), "environment source identity"
    )
    _expect(
        environment_source.get("objectsSha256") == definition.scene_objects.file.sha256
        and environment_source.get("osmSha256")
        == definition.scene_effective_osm.file.sha256,
        "environment source does not bind the compiled scene",
    )

    traffic = documents["traffic"]
    recording = documents["recording"]
    recording_inputs = documents["recording_inputs"]
    _expect(
        traffic.get("schema_version") == "aero-bench.city-sumo-preview/v2"
        and recording.get("schema_version")
        == "aero-bench.city-sumo-native-recording/v1"
        and recording_inputs.get("schema_version")
        == "aero-bench.city-sumo-recording-inputs/v2",
        "SUMO source products use unsupported schemas",
    )
    _expect(
        recording.get("artifact_class") == "offline-engineering-preview"
        and recording.get("source_kind") == "offline-sumo-engineering-preview",
        "native SUMO recording must remain explicitly classified as engineering preview evidence",
    )
    _expect(
        traffic.get("source_network_sha256") == definition.sumo_network.file.sha256
        and traffic.get("source_osm_sha256") == definition.road_source_osm.file.sha256
        and traffic.get("route_sha256") == definition.sumo_routes.file.sha256,
        "traffic preview does not bind the pinned native SUMO inputs",
    )
    native_source = _require_mapping(
        traffic.get("native_motion_source"), "traffic native-motion source"
    )
    _expect(
        (native_source.get("sha256"), native_source.get("size_bytes"))
        == (
            definition.native_traffic_recording.file.sha256,
            definition.native_traffic_recording.size_bytes,
        ),
        "traffic preview does not bind the exact native recording bytes",
    )
    for field, expected in (
        ("source_network_sha256", definition.sumo_network.file.sha256),
        ("source_osm_sha256", definition.road_source_osm.file.sha256),
        ("route_sha256", definition.sumo_routes.file.sha256),
    ):
        _expect(
            recording.get(field) == expected and native_source.get(field) == expected,
            f"native SUMO recording identity differs at {field}",
        )
    _expect(
        recording.get("recording_inputs_sha256")
        == definition.native_recording_inputs.file.sha256
        and recording_inputs.get("expected_route_sha256")
        == definition.sumo_routes.file.sha256,
        "native SUMO recording-input identity differs",
    )
    _expect(
        recording.get("sumo_image_id")
        == native_source.get("sumo_image_id")
        == engineering.get("sumo_image_id")
        and recording.get("sumo_version") == native_source.get("sumo_version"),
        "SUMO runtime identity differs across accepted source products",
    )
    obstacle_basis = _require_mapping(
        recording.get("visual_obstacle_basis"), "native obstacle basis"
    )
    _expect(
        obstacle_basis.get("effective_fixture_geometry_sha256")
        == definition.effective_fixtures.file.sha256
        and obstacle_basis.get("effective_fixture_geometry_size_bytes")
        == definition.effective_fixtures.size_bytes
        and obstacle_basis.get("source_context_sha256")
        == definition.source_context.file.sha256,
        "native SUMO obstacle basis differs from the pinned visible geometry",
    )

    road_gate = _require_mapping(
        documents["road_gate"].get("native_road_geometry"), "native road gate result"
    )
    _expect(
        road_gate.get("status") == "PASS"
        and road_gate.get("model_contacts") == []
        and road_gate.get("missing_surface_ribbons") == []
        and road_gate.get("unrenderable_native_ribbons") == [],
        "native road geometry gate is not a clean PASS",
    )

    flight = documents["flight"]
    _expect(
        flight.get("schema_version") == "aero-bench.city-planned-flight-preview/v2",
        "flight preview uses an unsupported schema",
    )
    physical_preview = flight.get("physical_simulation")
    _expect(
        physical_preview is False
        and flight.get("source_kind") == "planned-visual-flight",
        "flight source must remain an explicitly nonphysical planned preview",
    )

    presentation = documents["presentation"]
    _expect(
        presentation.get("schema_version") == "aero-bench.city-scene-preview/v1",
        "presentation uses an unsupported schema",
    )
    _expect(
        _nested(presentation, "mesh_pack", "manifest", "sha256")
        == definition.mesh_pack_manifest.file.sha256
        and _nested(presentation, "mesh_pack", "manifest", "size_bytes")
        == definition.mesh_pack_manifest.size_bytes
        and _nested(presentation, "building_render", "manifest", "sha256")
        == definition.building_render_manifest.file.sha256
        and _nested(presentation, "building_render", "source_context", "sha256")
        == definition.building_render_source_context.file.sha256
        and _nested(presentation, "building_render", "source_context", "size_bytes")
        == definition.building_render_source_context.size_bytes
        and _nested(presentation, "road_assets", "road", "sha256")
        == definition.road_geometry.file.sha256
        and _nested(presentation, "road_assets", "effective_fixtures", "sha256")
        == definition.effective_fixtures.file.sha256
        and _nested(presentation, "road_assets", "traffic", "sha256")
        == definition.traffic_preview.file.sha256
        and _nested(presentation, "road_assets", "flight", "sha256")
        == definition.flight_preview.file.sha256
        and _nested(presentation, "environment_source", "sha256")
        == definition.environment_source.file.sha256
        and _nested(presentation, "traffic_signal_model", "sha256")
        == definition.traffic_signal_model.file.sha256,
        "presentation pins differ from the accepted city source lock",
    )

    adapters = builtin_provider_registry().adapters
    evidence = CityNativeSourceEvidence(
        scene_id=definition.scene_id,
        origin=origin_model,
        verified_file_count=len(definition.source_files()),
        verified_file_bytes=sum(item.size_bytes for item in definition.source_files()),
        building_count=len(buildings),
        building_render_bytes=render_total_bytes,
        building_render_combined_sha256=renders.evidence["staged"]["combined_sha256"],
        building_runtime_asset_count=runtime_asset_count,
        building_runtime_asset_bytes=runtime_asset_bytes,
        mesh_runtime_asset_count=mesh_runtime_count,
        mesh_runtime_asset_bytes=mesh_runtime_bytes,
        compiled_road_count=len(roads),
        road_width_count=len(width_entries),
        effective_fixture_count=len(
            _require_list(fixtures.get("effective_fixtures"), "effective fixtures")
        ),
        native_road_ribbons_checked=road_gate.get("native_ribbons_checked"),
        native_traffic_frame_count=len(
            _require_list(recording.get("frames"), "native traffic frames")
        ),
        flight_preview_frame_count=len(
            _require_list(flight.get("frames"), "flight preview frames")
        ),
        source_network_sha256=definition.sumo_network.file.sha256,
        route_sha256=definition.sumo_routes.file.sha256,
        sumo_image_id=recording.get("sumo_image_id"),
        sumo_version=recording.get("sumo_version"),
        traffic_artifact_class=recording.get("artifact_class"),
        flight_preview_physical_simulation=physical_preview,
        provider_registry_adapters=adapters,
        weather_provider_registered=any("weather" in adapter for adapter in adapters),
        logistics_runtime_implemented=LOGISTICS_RUNTIME_IMPLEMENTED,
    )
    _expect(
        fixture_counts.get("effective_signals", 0)
        + fixture_counts.get("effective_street_lamps", 0)
        == evidence.effective_fixture_count,
        "effective fixture counts do not add up",
    )
    # Only runtime-relevant source bytes belong in the WorldPackage. Authoring
    # manifests, preview recordings, gates, and provenance reports remain
    # pinned by the registration and verified above, but staging them as unused
    # world assets would make strict scenario resolution fail. The package must
    # contain the actual source geometry, accepted presentation layers, SUMO
    # inputs, and all 414 per-building render meshes.
    formal_source_files = (
        definition.raw_osm,
        definition.scene_objects,
        definition.scene_effective_osm,
        definition.road_source_osm,
        definition.sumo_network,
        definition.sumo_routes,
        definition.road_geometry,
        definition.effective_fixtures,
        definition.environment_source,
    )
    required_world_assets: dict[str, int] = {
        item.file.sha256: item.size_bytes for item in formal_source_files
    }
    gazebo_scene = _scene_output(manifest, "gazebo/scene.sdf")
    gazebo_digest = gazebo_scene.get("sha256")
    gazebo_size = gazebo_scene.get("byte_size")
    _expect(
        isinstance(gazebo_digest, str)
        and isinstance(gazebo_size, int)
        and not isinstance(gazebo_size, bool)
        and gazebo_size > 0,
        "compiled Gazebo scene output has no exact byte identity",
    )
    required_world_assets[gazebo_digest] = gazebo_size
    for item in renders.staged:
        required_world_assets[item.sha256] = item.byte_size
    return _VerifiedSources(
        evidence=evidence,
        documents=documents,
        renders=renders,
        required_world_assets=tuple(sorted(required_world_assets.items())),
    )


def _asset_by_id(world: WorldPackage, asset_id: str):
    return next(
        (asset for asset in world.assets if asset.artifact.artifact_id == asset_id),
        None,
    )


def _sdf_pose_xyz(
    model: ElementTree.Element, *, label: str
) -> tuple[float, float, float]:
    pose = model.find("pose")
    raw = "0 0 0" if pose is None or pose.text is None else pose.text
    fields = raw.split()
    _expect(len(fields) >= 3, f"{label} SDF pose must contain x, y, and z")
    try:
        values = tuple(float(item) for item in fields[:3])
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"{label} SDF pose contains a non-numeric value"
        ) from exc
    _expect(
        all(math.isfinite(item) for item in values), f"{label} SDF pose is not finite"
    )
    return values  # type: ignore[return-value]


def _sdf_size_xyz(
    element: ElementTree.Element, *, label: str
) -> tuple[float, float, float]:
    raw = element.text or ""
    fields = raw.split()
    _expect(len(fields) == 3, f"{label} SDF size must contain three values")
    try:
        values = tuple(float(item) for item in fields)
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"{label} SDF size contains a non-numeric value"
        ) from exc
    _expect(
        all(math.isfinite(item) and item > 0.0 for item in values),
        f"{label} SDF size must be finite and positive",
    )
    return values  # type: ignore[return-value]


def _verify_gazebo_scene_contract(
    *,
    world: WorldPackage,
    resolved_selectors: dict[str, Path],
) -> None:
    bindings = tuple(
        item
        for item in world.engine_frame_bindings
        if item.engine == "gazebo" and item.scene_asset_id is not None
    )
    _expect(
        len(bindings) == 1,
        "native WorldPackage must declare one exact Gazebo scene binding",
    )
    scene = _asset_by_id(world, bindings[0].scene_asset_id)  # type: ignore[arg-type]
    _expect(scene is not None, "Gazebo scene binding names an unknown world asset")
    selector = scene.artifact.selector.partition("#")[0]  # type: ignore[union-attr]
    source = resolved_selectors.get(selector)
    _expect(source is not None, "Gazebo scene asset was not byte-verified")
    try:
        root = ElementTree.fromstring(source.read_bytes())  # type: ignore[union-attr]
    except (OSError, ElementTree.ParseError) as exc:
        raise CityNativeRegistrationError(
            f"native Gazebo scene is not valid SDF XML: {exc}"
        ) from exc
    world_elements = (
        [root]
        if root.tag.rsplit("}", 1)[-1] == "world"
        else [item for item in root if item.tag.rsplit("}", 1)[-1] == "world"]
    )
    _expect(len(world_elements) == 1, "native Gazebo SDF must contain one world")
    models: dict[str, ElementTree.Element] = {}
    for model in world_elements[0]:
        if model.tag.rsplit("}", 1)[-1] != "model":
            continue
        name = model.get("name")
        _expect(bool(name), "native Gazebo SDF contains an unnamed model")
        _expect(name not in models, f"native Gazebo SDF repeats model {name!r}")
        models[name] = model  # type: ignore[index]

    for launch in world.launch_sites:
        model = models.get(launch.launch_site_id)
        _expect(
            model is not None,
            "native Gazebo SDF has no model matching launch site "
            f"{launch.launch_site_id}",
        )
        east_m, north_m, _up_m = _sdf_pose_xyz(
            model,
            label=f"launch site {launch.launch_site_id}",  # type: ignore[arg-type]
        )
        _expect(
            math.isclose(east_m, launch.pose.east_m, abs_tol=1e-9, rel_tol=0.0)
            and math.isclose(north_m, launch.pose.north_m, abs_tol=1e-9, rel_tol=0.0),
            f"Gazebo launch model {launch.launch_site_id} has another horizontal pose",
        )
        box_sizes = model.findall("./link/collision/geometry/box/size")  # type: ignore[union-attr]
        _expect(
            len(box_sizes) == 1,
            f"Gazebo launch model {launch.launch_site_id} must have one collision box",
        )
        size_x, size_y, _size_z = _sdf_size_xyz(
            box_sizes[0], label=f"launch site {launch.launch_site_id} collision"
        )
        diameter = launch.pad_radius_m * 2.0
        _expect(
            math.isclose(size_x, diameter, abs_tol=1e-9, rel_tol=0.0)
            and math.isclose(size_y, diameter, abs_tol=1e-9, rel_tol=0.0),
            f"Gazebo launch model {launch.launch_site_id} differs from pad_radius_m",
        )

    ground = models.get("ground") or models.get("ground_plane")
    _expect(ground is not None, "native Gazebo SDF has no ground contact model")
    plane_sizes = ground.findall("./link/collision/geometry/plane/size")  # type: ignore[union-attr]
    _expect(len(plane_sizes) == 1, "native Gazebo ground must have one collision plane")
    raw_size = (plane_sizes[0].text or "").split()
    _expect(
        len(raw_size) == 2, "native Gazebo ground plane size must contain two values"
    )
    try:
        size_east_m, size_north_m = (float(item) for item in raw_size)
    except ValueError as exc:
        raise CityNativeRegistrationError(
            "native Gazebo ground plane size contains a non-numeric value"
        ) from exc
    center_east_m, center_north_m, _center_up_m = _sdf_pose_xyz(ground, label="ground")
    extent = world.frame.spatial_extent
    _expect(
        math.isfinite(size_east_m)
        and math.isfinite(size_north_m)
        and center_east_m - size_east_m / 2.0 <= extent.min_east_m
        and center_east_m + size_east_m / 2.0 >= extent.max_east_m
        and center_north_m - size_north_m / 2.0 <= extent.min_north_m
        and center_north_m + size_north_m / 2.0 >= extent.max_north_m,
        "native Gazebo ground plane does not cover the declared spatial extent",
    )


def _verify_world_binding(
    *,
    reader: BundleReader,
    definition: CityNativeInputLock,
    sources: _VerifiedSources,
) -> WorldPackage:
    binding = definition.world
    if binding is None:  # guarded by the caller
        raise CityNativeRegistrationError("city world binding is absent")
    catalog = _document(reader, binding.authored_catalog, "authored world catalog")
    _expect(
        catalog.get("schema_version") == "aero-bench.urban-world-authoring/v1",
        "authored world catalog uses an unsupported schema",
    )
    try:
        world = WorldPackage.model_validate(
            reader.load_document(binding.world_package.file)
        )
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"invalid native WorldPackage: {exc}"
        ) from exc
    _expect(
        world.world_id == binding.world_id,
        "native WorldPackage world_id differs from the registration",
    )
    expected_package_path = (
        PurePosixPath(binding.bundle_root) / "world" / "package.json"
    ).as_posix()
    _expect(
        binding.world_package.file.path == expected_package_path,
        "native WorldPackage must be the canonical world/package.json under bundle_root",
    )
    bundle_root = reader.root / binding.bundle_root
    _expect(
        bundle_root.is_dir() and not bundle_root.is_symlink(),
        "native WorldPackage bundle_root is missing or a symbolic link",
    )
    resolved_selectors: dict[str, Path] = {}
    for asset in world.assets:
        selector = asset.artifact.selector.partition("#")[0]
        candidate = bundle_root / selector
        _expect(
            not any(
                part.is_symlink()
                for part in (candidate, *candidate.parents)
                if part.is_relative_to(bundle_root)
            ),
            f"native WorldPackage asset resolves through a symbolic link: {selector}",
        )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise CityNativeRegistrationError(
                f"native WorldPackage asset is missing: {selector}: {exc}"
            ) from exc
        _expect(
            resolved.is_relative_to(bundle_root) and resolved.is_file(),
            f"native WorldPackage asset is not one regular in-bundle file: {selector}",
        )
        _expect(
            resolved.stat().st_size == asset.byte_size
            and sha256_file(resolved) == asset.artifact.sha256,
            f"native WorldPackage asset bytes differ from the declaration: {selector}",
        )
        resolved_selectors[selector] = resolved
        license_selector = asset.license_file.selector.partition("#")[0]
        if license_selector not in resolved_selectors:
            license_candidate = bundle_root / license_selector
            _expect(
                not any(
                    part.is_symlink()
                    for part in (license_candidate, *license_candidate.parents)
                    if part.is_relative_to(bundle_root)
                ),
                "native WorldPackage license resolves through a symbolic link: "
                f"{license_selector}",
            )
            try:
                license_path = license_candidate.resolve(strict=True)
            except OSError as exc:
                raise CityNativeRegistrationError(
                    "native WorldPackage license file is missing: "
                    f"{license_selector}: {exc}"
                ) from exc
            _expect(
                license_path.is_relative_to(bundle_root)
                and license_path.is_file()
                and sha256_file(license_path) == asset.license_file.sha256,
                "native WorldPackage license bytes differ from the declaration: "
                f"{license_selector}",
            )
            resolved_selectors[license_selector] = license_path
    _verify_gazebo_scene_contract(
        world=world,
        resolved_selectors=resolved_selectors,
    )
    origin = sources.evidence.origin
    _expect(
        math.isclose(
            world.frame.origin.latitude_deg,
            origin.latitude_deg,
            abs_tol=1e-9,
            rel_tol=0.0,
        )
        and math.isclose(
            world.frame.origin.longitude_deg,
            origin.longitude_deg,
            abs_tol=1e-9,
            rel_tol=0.0,
        )
        and math.isclose(
            world.frame.origin.altitude_m,
            origin.ellipsoid_height_m,
            abs_tol=1e-6,
            rel_tol=0.0,
        ),
        "native WorldPackage origin differs from the compiled city",
    )
    object_document = sources.documents["scene_objects"]
    building_ids = {
        _require_mapping(item, "scene building").get("object_id")
        for item in _require_list(object_document.get("buildings"), "scene buildings")
    }
    road_ids = {
        _require_mapping(item, "scene road").get("object_id")
        for item in _require_list(object_document.get("roads"), "scene roads")
    }
    _expect(
        {item.building_id for item in world.buildings} == building_ids,
        "native WorldPackage does not contain the exact compiled building set",
    )
    _expect(
        {item.road_id for item in world.roads} == road_ids,
        "native WorldPackage does not contain the exact compiled road set",
    )

    world_assets_by_digest = {
        item.artifact.sha256: item.byte_size for item in world.assets
    }
    _expect(
        all(
            world_assets_by_digest.get(digest) == size
            for digest, size in sources.required_world_assets
        ),
        "native WorldPackage omits or mis-sizes one or more accepted city source assets",
    )
    _expect(world.sumo is not None, "native WorldPackage has no SUMO configuration")
    if world.sumo is not None:
        network_asset = _asset_by_id(world, world.sumo.network_asset_id)
        routes_asset = _asset_by_id(world, world.sumo.routes_asset_id)
        _expect(
            network_asset is not None
            and network_asset.artifact.sha256 == definition.sumo_network.file.sha256
            and routes_asset is not None
            and routes_asset.artifact.sha256 == definition.sumo_routes.file.sha256,
            "native WorldPackage SUMO binding does not use the accepted network/routes",
        )
    _expect(
        world.network is not None,
        "native WorldPackage has no wireless network configuration",
    )
    _expect(
        bool(world.launch_sites) and any(item.kind == "uav" for item in world.entities),
        "native WorldPackage has no physical UAV launch binding",
    )
    _expect(
        bool(world.sensors),
        "native WorldPackage has no physical observation sensor binding",
    )
    return world


def _image_lock_values(document: dict[str, object]) -> frozenset[str]:
    images = document.get("images")
    if not isinstance(images, dict) or not images:
        raise CityNativeRegistrationError(
            "runtime image lock requires an images object"
        )
    values = tuple(images.values())
    if any(
        not isinstance(item, str) or _DIGEST_IMAGE.fullmatch(item) is None
        for item in values
    ):
        raise CityNativeRegistrationError(
            "runtime image lock contains a non-digest image identity"
        )
    return frozenset(values)


def _run_images(run: ResolvedRunSpec) -> frozenset[str]:
    workloads = (
        run.environment.harness,
        *(provider.workload for provider in run.environment.providers),
        *(agent.workload for agent in run.agents),
        *(agent.driver.workload for agent in run.agents if agent.driver is not None),
        run.task.verifier.workload,
    )
    return frozenset(workload.runtime.image for workload in workloads)


def _verify_execution_binding(
    *,
    root: Path,
    reader: BundleReader,
    definition: CityNativeInputLock,
    world: WorldPackage,
) -> ResolvedRunSpec:
    binding = definition.execution
    if binding is None:  # guarded by the caller
        raise CityNativeRegistrationError("city execution binding is absent")
    suite_path = reader.resolve_file(binding.suite.file)
    loaded = load_suite(suite_path)
    _expect(
        len(loaded.suite.cases) == 1
        and not loaded.suite.cases[0].axes
        and len(loaded.suite.cases[0].seeds) == 1
        and len(loaded.suite.cases[0].launch_site_ids) == 1,
        "city native Suite must select one exact case, seed, and launch site",
    )
    suite_world_path = (loaded.root / loaded.suite.cases[0].world_package.path).resolve(
        strict=True
    )
    declared_world_path = reader.resolve_file(
        definition.world.world_package.file  # type: ignore[union-attr]
    )
    _expect(
        suite_world_path == declared_world_path,
        "city Suite does not select the registered native WorldPackage",
    )
    try:
        runs = resolve_suite(
            str(suite_path),
            executor_kind="docker_reference",
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=builtin_provider_registry(),
        )
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"city Suite resolution failed: {exc}"
        ) from exc
    _expect(len(runs) == 1, "city native Suite must resolve exactly one run")
    run = runs[0]
    _expect(
        run.execution_scope == "formal_benchmark",
        "city execution must use formal_benchmark scope",
    )
    _expect(
        run.task.task_id == binding.task_id,
        "resolved city task_id differs from the execution binding",
    )
    _expect(
        run.scenario.world_id == world.world_id,
        "resolved city run uses another WorldPackage",
    )
    _expect(
        run.feasibility.feasible,
        f"city task preflight is infeasible: {run.feasibility.failed_conditions}",
    )

    run_adapters = {item.adapter for item in run.environment.providers}
    _expect(
        set(binding.required_provider_adapters).issubset(run_adapters),
        "resolved city run omits a required Provider adapter",
    )
    registry_adapters = set(builtin_provider_registry().adapters)
    _expect(
        run_adapters.issubset(registry_adapters),
        "resolved city run names an unregistered Provider adapter",
    )

    try:
        runner = reader.load_yaml(binding.runner_config.file, RunnerConfig)
    except ValueError as exc:
        raise CityNativeRegistrationError(
            f"invalid Docker runner config: {exc}"
        ) from exc
    _expect(
        runner.executor_kind == "docker_reference",
        "city runner must select docker_reference explicitly",
    )
    image_lock = _document(reader, binding.image_lock, "runtime image lock")
    locked_images = _image_lock_values(image_lock)
    run_images = _run_images(run)
    _expect(
        run_images.issubset(locked_images),
        "runtime image lock does not cover every resolved workload image",
    )
    _expect(
        runner.volume_keeper_image in locked_images,
        "runner volume-keeper image is absent from the image lock",
    )

    verifier_image = run.task.verifier.workload.runtime.image
    other_images = {
        run.environment.harness.runtime.image,
        *(item.workload.runtime.image for item in run.environment.providers),
        *(item.workload.runtime.image for item in run.agents),
    }
    _expect(
        verifier_image not in other_images,
        "independent verifier must not reuse a participant/provider/harness image",
    )
    return run


def _prerequisite(
    code: str,
    status: Literal["ready", "missing", "unsupported", "mismatch"],
    detail: str,
) -> CityNativePrerequisite:
    return CityNativePrerequisite(code=code, status=status, detail=detail)


def assess_city_native_registration(
    root: Path, definition: CityNativeInputLock
) -> CityNativeReadinessReport:
    """Verify accepted sources and report exact native-execution prerequisites."""

    root = root.resolve(strict=True)
    reader, _paths = _read_pinned_files(root, definition)
    sources = _verify_scene_sources(root=root, definition=definition, reader=reader)
    prerequisites: list[CityNativePrerequisite] = [
        _prerequisite(
            "source.compiled-scene",
            "ready",
            "The raw OSM, compiled outputs, origin, 414 buildings, and 289 roads are exact-byte verified.",
        ),
        _prerequisite(
            "source.building-renders",
            "ready",
            "All per-building GLBs match the scene, fragment digests, embedded provenance, and geometry envelopes.",
        ),
        _prerequisite(
            "source.road-geometry",
            "ready",
            "The accepted road v3, model-backed effective fixtures, source ground cover, and native road gate share one source context.",
        ),
        _prerequisite(
            "source.native-sumo-inputs",
            "ready",
            "The accepted network and route bytes are pinned. The recorded motion remains offline engineering evidence, not a formal Provider run.",
        ),
        _prerequisite(
            "source.presentation",
            "ready",
            "The default engineering presentation pins the verified city source products without being treated as execution evidence.",
        ),
        _prerequisite(
            "providers.registered-adapters",
            "ready",
            "Implemented adapters are inspection.business, logistics.business, ns3.rpc, px4.gazebo, and sumo.traci.",
        ),
    ]

    world: WorldPackage | None = None
    if definition.world is None:
        prerequisites.extend(
            (
                _prerequisite(
                    "world.authored-catalog",
                    "missing",
                    "No authored catalog supplies licensed terrain/geoid assets and measured weather for the Huangpu source.",
                ),
                _prerequisite(
                    "world.native-package",
                    "missing",
                    "No aero-bench.world/v2 package binds the accepted buildings, roads, SUMO inputs, launch sites, sensors, and network.",
                ),
            )
        )
    else:
        try:
            world = _verify_world_binding(
                reader=reader, definition=definition, sources=sources
            )
        except CityNativeRegistrationError as exc:
            prerequisites.append(
                _prerequisite("world.native-package", "mismatch", str(exc))
            )
        else:
            prerequisites.extend(
                (
                    _prerequisite(
                        "world.authored-catalog",
                        "ready",
                        "The authored catalog is schema-valid and exact-byte pinned.",
                    ),
                    _prerequisite(
                        "world.native-package",
                        "ready",
                        "The native WorldPackage contains the complete accepted city source and runtime bindings.",
                    ),
                )
            )

    run: ResolvedRunSpec | None = None
    if definition.execution is None:
        prerequisites.extend(
            (
                _prerequisite(
                    "task.city-profile",
                    "missing",
                    "No one-case formal city Suite selects a task, launch site, participant, and the registered WorldPackage.",
                ),
                _prerequisite(
                    "provider.flight",
                    "missing",
                    "The published flight file explicitly has physical_simulation=false; a city-aligned px4.gazebo config and physical run are absent.",
                ),
                _prerequisite(
                    "provider.traffic",
                    "missing",
                    "The accepted SUMO network/routes exist, but no city Suite binds config/additional/toolchain inputs to sumo.traci.",
                ),
                _prerequisite(
                    "provider.network",
                    "missing",
                    "No city-specific ns3.rpc configuration binds the actual UAV, station, and endpoint identities.",
                ),
                _prerequisite(
                    "provider.business",
                    "missing",
                    "No city task selects inspection.business or logistics.business and binds its authority to the city entities.",
                ),
                _prerequisite(
                    "provider.observation",
                    "missing",
                    "No city task grants a physical PX4/Gazebo observation stream and verifier-visible observation artifacts.",
                ),
                _prerequisite(
                    "executor.docker-reference",
                    "missing",
                    "No digest-pinned city image lock and Docker runner configuration are registered.",
                ),
                _prerequisite(
                    "verifier.independent",
                    "missing",
                    "No city task/verifier contract can evaluate sealed city evidence independently.",
                ),
            )
        )
    elif world is None:
        prerequisites.append(
            _prerequisite(
                "executor.docker-reference",
                "mismatch",
                "Execution inputs cannot be resolved until the native WorldPackage is valid.",
            )
        )
    else:
        try:
            run = _verify_execution_binding(
                root=root,
                reader=reader,
                definition=definition,
                world=world,
            )
        except CityNativeRegistrationError as exc:
            prerequisites.append(
                _prerequisite("executor.docker-reference", "mismatch", str(exc))
            )
        else:
            prerequisites.extend(
                (
                    _prerequisite(
                        "task.city-profile",
                        "ready",
                        "The Suite resolves one feasible formal city run.",
                    ),
                    _prerequisite(
                        "provider.flight",
                        "ready",
                        "The resolved run binds px4.gazebo to the native city UAV.",
                    ),
                    _prerequisite(
                        "provider.traffic",
                        "ready",
                        "The resolved run binds sumo.traci to the accepted native network/routes.",
                    ),
                    _prerequisite(
                        "provider.network",
                        "ready",
                        "The resolved run binds ns3.rpc to declared city nodes.",
                    ),
                    _prerequisite(
                        "provider.business",
                        "ready",
                        "The resolved task binds one implemented business adapter.",
                    ),
                    _prerequisite(
                        "provider.observation",
                        "ready",
                        "The native WorldPackage and task declare physical sensors and observation contracts.",
                    ),
                    _prerequisite(
                        "executor.docker-reference",
                        "ready",
                        "Every workload image is digest-pinned and the runner selects docker_reference.",
                    ),
                    _prerequisite(
                        "verifier.independent",
                        "ready",
                        "The verifier uses a workload image distinct from the harness, Providers, and participant.",
                    ),
                )
            )

    required_domains = set(definition.required_execution_domains)
    if "weather-physics" in required_domains:
        prerequisites.append(
            _prerequisite(
                "provider.weather",
                "unsupported",
                "ResolvedScenario weather is declarative only: the production registry has no weather adapter that applies wind or precipitation to native physics.",
            )
        )
    if "airspace-enforcement" in required_domains:
        prerequisites.append(
            _prerequisite(
                "provider.airspace",
                "unsupported",
                "Region contracts and detection kernels exist, but no registered Airspace Provider owns permissions, revocations, or runtime enforcement.",
            )
        )
    if "physical-logistics" in required_domains and not LOGISTICS_RUNTIME_IMPLEMENTED:
        prerequisites.append(
            _prerequisite(
                "task.physical-logistics",
                "unsupported",
                "Physical logistics completion remains disabled (LOGISTICS_RUNTIME_IMPLEMENTED=false); use an implemented task or complete that runtime and verifier.",
            )
        )

    blocking_codes = tuple(
        item.code for item in prerequisites if item.status != "ready"
    )
    registration_sha256 = hashlib.sha256(
        canonical_json_bytes(definition.model_dump(mode="json"))
    ).hexdigest()
    return CityNativeReadinessReport(
        schema_version=CITY_READINESS_SCHEMA_VERSION,
        registration_id=definition.registration_id,
        registration_sha256=registration_sha256,
        status="blocked" if blocking_codes else "ready",
        required_execution_domains=definition.required_execution_domains,
        source_evidence=sources.evidence,
        prerequisites=tuple(prerequisites),
        blocking_codes=blocking_codes,
        world_id=world.world_id if world is not None else None,
        world_digest=world.world_digest if world is not None else None,
        run_id=run.run_id if run is not None else None,
    )


def verify_city_native_registration(
    root: Path, definition: CityNativeInputLock
) -> VerifiedCityNativeRegistration:
    """Return executable identities only when every native prerequisite is ready."""

    report = assess_city_native_registration(root, definition)
    if report.status != "ready":
        raise CityNativeRegistrationBlocked(
            "city native registration is blocked: " + ", ".join(report.blocking_codes)
        )
    if definition.world is None or definition.execution is None:
        raise AssertionError("ready report omitted required bindings")
    reader = BundleReader(root.resolve(strict=True))
    world = WorldPackage.model_validate(
        reader.load_document(definition.world.world_package.file)
    )
    runs = resolve_suite(
        str(reader.resolve_file(definition.execution.suite.file)),
        executor_kind="docker_reference",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    if len(runs) != 1 or runs[0].run_id != report.run_id:
        raise AssertionError("ready registration changed during final resolution")
    return VerifiedCityNativeRegistration(
        root=root.resolve(strict=True),
        definition=definition,
        report=report,
        world=world,
        run=runs[0],
    )


__all__ = [
    "CITY_INPUT_LOCK_SCHEMA_VERSION",
    "CITY_READINESS_SCHEMA_VERSION",
    "CityNativeExecutionDomain",
    "CityNativeExecutionBinding",
    "CityNativeInputLock",
    "CityNativePrerequisite",
    "CityNativeReadinessReport",
    "CityNativeRegistrationBlocked",
    "CityNativeRegistrationError",
    "CityNativeSourceEvidence",
    "CityNativeWorldBinding",
    "PinnedCityFile",
    "VerifiedCityNativeRegistration",
    "assess_city_native_registration",
    "load_city_native_input_lock",
    "verify_city_native_registration",
]
