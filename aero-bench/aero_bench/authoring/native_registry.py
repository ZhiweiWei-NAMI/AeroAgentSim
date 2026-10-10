"""Explicit native scene registrations; display names never select a world."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from aero_bench.agent.inspection_reference import REFERENCE_AGENT_VERSION
from aero_bench.authoring.city_native_registration import (
    CityNativeInputLock,
    CityNativeReadinessReport,
    CityNativeRegistrationBlocked,
    VerifiedCityNativeRegistration,
    assess_city_native_registration,
    load_city_native_input_lock,
    verify_city_native_registration,
)
from aero_bench.authoring.compilation_contracts import PublicSceneRegistration, SceneRegistrationCatalog
from aero_bench.authoring.workspace import CityOrderGeneration, CityWorkspaceDraft
from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import FileRef, Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.providers.rpc import parse_json_object
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.contracts import PublicScenario
from aero_bench.trace.projector import project_public_scenario
from aero_bench.world.alignment import AlignmentManifest, verify_alignment
from aero_bench.world.contracts import WorldPackage


CITY_INSPECTION_PROFILE_ID = "inspection.huangpu-native.v1"
CITY_INSPECTION_REGISTRATION_SCHEMA = (
    "aero-bench.native-city-inspection-registration/v1"
)
CITY_INSPECTION_PROVENANCE_SCHEMA = (
    "aero-bench.native-city-inspection-publication/v1"
)
CITY_INSPECTION_DOMAINS = (
    "business",
    "flight",
    "network",
    "observation",
    "traffic",
)
CITY_FULL_UNSUPPORTED_DOMAINS = (
    "airspace-enforcement",
    "physical-logistics",
    "weather-physics",
)
CITY_FULL_BLOCKING_CODES = (
    "provider.weather",
    "provider.airspace",
    "task.physical-logistics",
)


class RegisteredFile(StrictModel):
    file: FileRef
    size_bytes: Annotated[int, Field(gt=0)]


class PresentationBinding(StrictModel):
    """A pinned presentation file associated with exact native rendering assets."""

    file: FileRef
    size_bytes: Annotated[int, Field(gt=0)]
    world_asset_ids: tuple[Identifier, ...] = Field(min_length=1)


class NativeSceneDefinition(StrictModel):
    schema_version: Literal["aero-bench.native-scene-registration/v1"]
    registration_id: Identifier
    profile_id: Literal["inspection.reference.v1"]
    scene_path: Annotated[str, Field(pattern=r"^/city-presentation/[A-Za-z0-9_-]+\.json$")]
    scene_document: RegisteredFile
    suite: FileRef
    world: FileRef
    alignment: FileRef
    world_id: Identifier
    world_digest: Sha256
    files: tuple[RegisteredFile, ...] = Field(min_length=1)
    presentation: tuple[PresentationBinding, ...] = Field(min_length=1)
    reference_draft: CityWorkspaceDraft

    @model_validator(mode="after")
    def consistent_definition(self) -> NativeSceneDefinition:
        paths = [item.file.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("registration file paths must be unique")
        if self.reference_draft.scenePath != self.scene_path:
            raise ValueError("reference draft must select the registered presentation")
        if self.scene_document.file.path != self.scene_path.removeprefix("/"):
            raise ValueError("scene document must pin the selected presentation path")
        if self.reference_draft.deployment.executor != "docker_reference":
            raise ValueError("the registered inspection template uses Docker explicitly")
        algorithms = self.reference_draft.algorithms
        if (algorithms.assignment, algorithms.routing, algorithms.energy) != (
            "external", "external", "external"
        ) or algorithms.parameters != {"profile": self.profile_id}:
            raise ValueError("inspection reference is an external fixed policy, not a city planner")
        return self


class CityInspectionPublicationProvenance(StrictModel):
    """Exact relationship between the broader city gate and this inspection profile."""

    schema_version: Literal["aero-bench.native-city-inspection-publication/v1"]
    source_registration_id: Identifier
    source_registration_sha256: Sha256
    source_input_lock_sha256: Sha256
    source_readiness_sha256: Sha256
    source_status: Literal["blocked"]
    source_blocking_codes: tuple[Identifier, ...]
    inspection_registration_id: Identifier
    inspection_registration_sha256: Sha256
    inspection_readiness_sha256: Sha256
    inspection_status: Literal["ready"]
    profile_id: Literal["inspection.huangpu-native.v1"]
    implemented_execution_domains: tuple[str, ...]
    unsupported_full_city_domains: tuple[str, ...]
    base_run_id: Sha256
    scenario_digest: Sha256
    world_id: Identifier
    world_digest: Sha256
    publication_effect: Literal["copied-pinned-inputs-no-execution"]

    @model_validator(mode="after")
    def exact_scope(self) -> CityInspectionPublicationProvenance:
        if self.source_blocking_codes != CITY_FULL_BLOCKING_CODES:
            raise ValueError("source readiness must retain the three full-city blockers")
        if self.implemented_execution_domains != CITY_INSPECTION_DOMAINS:
            raise ValueError("inspection publication must declare only implemented domains")
        if self.unsupported_full_city_domains != CITY_FULL_UNSUPPORTED_DOMAINS:
            raise ValueError("inspection publication must preserve unsupported full-city domains")
        return self


class CityInspectionSceneDefinition(StrictModel):
    """A catalog entry backed by the real city-native readiness gate, not R5 alignment."""

    schema_version: Literal["aero-bench.native-city-inspection-registration/v1"]
    registration_id: Identifier
    profile_id: Literal["inspection.huangpu-native.v1"]
    scene_path: Annotated[str, Field(pattern=r"^/city-presentation/[A-Za-z0-9_-]+\.json$")]
    scene_document: RegisteredFile
    source_input_lock: RegisteredFile
    source_readiness: RegisteredFile
    inspection_input_lock: RegisteredFile
    inspection_readiness: RegisteredFile
    provenance: RegisteredFile
    suite: FileRef
    world: FileRef
    world_id: Identifier
    world_digest: Sha256
    files: tuple[RegisteredFile, ...] = Field(min_length=1)
    reference_draft: CityWorkspaceDraft

    @model_validator(mode="after")
    def consistent_definition(self) -> CityInspectionSceneDefinition:
        paths = [item.file.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("registration file paths must be unique")
        if self.reference_draft.scenePath != self.scene_path:
            raise ValueError("reference draft must select the registered presentation")
        if self.scene_document.file.path != self.scene_path.removeprefix("/"):
            raise ValueError("scene document must pin the selected presentation path")
        if self.reference_draft.deployment.executor != "docker_reference":
            raise ValueError("the registered inspection template uses Docker explicitly")
        algorithms = self.reference_draft.algorithms
        if (algorithms.assignment, algorithms.routing, algorithms.energy) != (
            "external", "external", "external"
        ) or algorithms.parameters != {"profile": self.profile_id}:
            raise ValueError("city inspection is an external fixed policy, not a city planner")
        return self


class LogisticsArrivalsSceneDefinition(StrictModel):
    """Business-only registration; never an inspection or physical-delivery entry."""

    schema_version: Literal["aero-bench.native-logistics-arrivals-registration/v1"]
    registration_id: Identifier
    profile_id: Literal["logistics.arrivals.v1"]
    scene_path: Annotated[str, Field(pattern=r"^/city-presentation/[A-Za-z0-9_-]+\.json$")]
    scene_document: RegisteredFile
    suite: FileRef
    world: FileRef
    world_id: Identifier
    world_digest: Sha256
    files: tuple[RegisteredFile, ...] = Field(min_length=1)
    reference_draft: CityWorkspaceDraft

    @model_validator(mode="after")
    def consistent_definition(self):
        if len({item.file.path for item in self.files}) != len(self.files):
            raise ValueError("registration file paths must be unique")
        if (
            self.reference_draft.scenePath != self.scene_path
            or self.scene_document.file.path != self.scene_path.removeprefix("/")
            or self.reference_draft.deployment.executor != "docker_reference"
        ):
            raise ValueError("arrivals registration must pin its exact Docker/public selection")
        algorithms = self.reference_draft.algorithms
        if (algorithms.assignment, algorithms.routing, algorithms.energy) != ("external",) * 3 or algorithms.parameters != {"profile": self.profile_id}:
            raise ValueError("arrivals registration cannot declare a physical planning policy")
        return self


SceneDefinition: TypeAlias = NativeSceneDefinition | CityInspectionSceneDefinition | LogisticsArrivalsSceneDefinition


def _definition_sha256(definition: SceneDefinition) -> str:
    return hashlib.sha256(
        canonical_json_bytes(definition.model_dump(mode="json"))
    ).hexdigest()


def _registered_inventory(
    definition: SceneDefinition,
) -> dict[str, RegisteredFile]:
    return {item.file.path: item for item in definition.files}


def _assert_unchanged_definition(
    definition: SceneDefinition, registration_sha256: str,
) -> None:
    if _definition_sha256(definition) != registration_sha256:
        raise ValueError("registered scene definition identity changed")


def _read_registered_file(
    *, root: Path, definition: SceneDefinition, registration_sha256: str,
    registered: RegisteredFile,
) -> bytes:
    """Read one exact registered file without re-reading the whole compilation closure."""

    _assert_unchanged_definition(definition, registration_sha256)
    inventory = _registered_inventory(definition)
    if inventory.get(registered.file.path) != registered:
        raise ValueError("requested file is absent from the exact registration inventory")
    target = root / registered.file.path
    if any(
        part.is_symlink()
        for part in (target, *target.parents)
        if part.is_relative_to(root)
    ):
        raise ValueError(f"registration contains a symlink: {registered.file.path}")
    raw = BundleReader(root).resolve_file(registered.file).read_bytes()
    if (
        len(raw) != registered.size_bytes
        or hashlib.sha256(raw).hexdigest() != registered.file.sha256
    ):
        raise ValueError(f"registration file identity changed: {registered.file.path}")
    return raw


def _read_registered_files(
    *, root: Path, definition: SceneDefinition, registration_sha256: str,
) -> dict[str, bytes]:
    """Recheck the complete registered closure before every compilation."""

    _assert_unchanged_definition(definition, registration_sha256)
    return {
        item.file.path: _read_registered_file(
            root=root,
            definition=definition,
            registration_sha256=registration_sha256,
            registered=item,
        )
        for item in definition.files
    }


def _public_asset_inventory(
    run: ResolvedRunSpec,
) -> dict[str, tuple[RegisteredFile, str]]:
    files: dict[str, tuple[RegisteredFile, str]] = {}
    for asset in project_public_scenario(run).assets:
        entries = [(
            asset.sha256,
            RegisteredFile(
                file=FileRef(path=asset.selector, sha256=asset.sha256),
                size_bytes=asset.size_bytes,
            ),
            asset.media_type or "application/octet-stream",
        )]
        if asset.license_sha256 is not None:
            if asset.license_selector is None or asset.license_size_bytes is None:
                raise AssertionError("validated public asset omitted its licence pin")
            entries.append((
                asset.license_sha256,
                RegisteredFile(
                    file=FileRef(
                        path=asset.license_selector,
                        sha256=asset.license_sha256,
                    ),
                    size_bytes=asset.license_size_bytes,
                ),
                "text/plain; charset=utf-8",
            ))
        for digest, registered, media in entries:
            prior = files.get(digest)
            if prior is not None and prior != (registered, media):
                raise ValueError("public asset digest has conflicting registered files")
            files[digest] = (registered, media)
    return files


def _verify_public_asset_inventory(
    definition: SceneDefinition, run: ResolvedRunSpec,
) -> None:
    inventory = _registered_inventory(definition)
    for registered, _media in _public_asset_inventory(run).values():
        if inventory.get(registered.file.path) != registered:
            raise ValueError(
                "public scenario asset is absent from the exact registration inventory"
            )


def _public_asset_bytes(
    *, root: Path, definition: SceneDefinition, registration_sha256: str,
    run: ResolvedRunSpec, sha256: str,
) -> tuple[bytes, str]:
    public = _public_asset_inventory(run)
    if sha256 not in public:
        raise KeyError("unknown public native scene asset")
    registered, media = public[sha256]
    raw = _read_registered_file(
        root=root,
        definition=definition,
        registration_sha256=registration_sha256,
        registered=registered,
    )
    return raw, media


@dataclass(frozen=True, slots=True)
class VerifiedNativeScene:
    root: Path
    definition: NativeSceneDefinition | LogisticsArrivalsSceneDefinition
    registration_sha256: str
    world: WorldPackage
    run: ResolvedRunSpec

    def public(self) -> PublicSceneRegistration:
        return PublicSceneRegistration(
            registration_id=self.definition.registration_id,
            registration_sha256=self.registration_sha256,
            scene_path=self.definition.scene_path,
            scene_url=f"/authoring/v1/native-scenes/{self.definition.registration_id}/scene",
            scene_schema_version="aero-bench.public-scenario/v1",
            scene_sha256=self.definition.scene_document.file.sha256,
            scene_size_bytes=self.definition.scene_document.size_bytes,
            world_id=self.world.world_id,
            world_digest=self.world.world_digest,
            profile_id=self.definition.profile_id,
            editable_execution_fields=("/seed", "/events") if isinstance(self.definition, LogisticsArrivalsSceneDefinition) else ("/seed",),
            retained_authoring_fields=("/name", "/environment", "/stateKeyframes"),
            reference_draft=self.definition.reference_draft,
        )

    def read_files(self) -> dict[str, bytes]:
        """Recheck pins on every compilation, not only at server startup."""
        return _read_registered_files(
            root=self.root,
            definition=self.definition,
            registration_sha256=self.registration_sha256,
        )

    def public_scene_bytes(self) -> bytes:
        return _read_registered_file(
            root=self.root,
            definition=self.definition,
            registration_sha256=self.registration_sha256,
            registered=self.definition.scene_document,
        )

    def public_asset_bytes(self, sha256: str) -> tuple[bytes, str]:
        """Expose only the public scenario's exact assets and their licences."""
        return _public_asset_bytes(
            root=self.root,
            definition=self.definition,
            registration_sha256=self.registration_sha256,
            run=self.run,
            sha256=sha256,
        )


@dataclass(frozen=True, slots=True)
class VerifiedCityInspectionScene:
    root: Path
    definition: CityInspectionSceneDefinition
    registration_sha256: str
    city_registration: VerifiedCityNativeRegistration
    world: WorldPackage
    run: ResolvedRunSpec

    def public(self) -> PublicSceneRegistration:
        return PublicSceneRegistration(
            registration_id=self.definition.registration_id,
            registration_sha256=self.registration_sha256,
            scene_path=self.definition.scene_path,
            scene_url=f"/authoring/v1/native-scenes/{self.definition.registration_id}/scene",
            scene_schema_version="aero-bench.public-scenario/v1",
            scene_sha256=self.definition.scene_document.file.sha256,
            scene_size_bytes=self.definition.scene_document.size_bytes,
            world_id=self.world.world_id,
            world_digest=self.world.world_digest,
            profile_id=self.definition.profile_id,
            editable_execution_fields=("/seed",),
            retained_authoring_fields=("/name", "/environment", "/stateKeyframes"),
            reference_draft=self.definition.reference_draft,
        )

    def read_files(self) -> dict[str, bytes]:
        return _read_registered_files(
            root=self.root,
            definition=self.definition,
            registration_sha256=self.registration_sha256,
        )

    def public_scene_bytes(self) -> bytes:
        return _read_registered_file(
            root=self.root,
            definition=self.definition,
            registration_sha256=self.registration_sha256,
            registered=self.definition.scene_document,
        )

    def public_asset_bytes(self, sha256: str) -> tuple[bytes, str]:
        return _public_asset_bytes(
            root=self.root,
            definition=self.definition,
            registration_sha256=self.registration_sha256,
            run=self.run,
            sha256=sha256,
        )


VerifiedSceneRegistration: TypeAlias = VerifiedNativeScene | VerifiedCityInspectionScene


def _verify_inspection_reference_draft(
    *, definition: SceneDefinition, world: WorldPackage, run: ResolvedRunSpec,
) -> None:
    if len(run.agents) != 1 or run.agents[0].driver is not None or (
        run.agents[0].workload.implementation.version != REFERENCE_AGENT_VERSION
    ):
        raise ValueError(
            "native profile requires the implemented deterministic inspection participant"
        )
    if definition.reference_draft.deployment.imageRef != run.agents[0].workload.runtime.image:
        raise ValueError("reference draft must pin the actual native Agent workload")
    native_uavs = {item.entity_id for item in world.entities if item.kind == "uav"}
    if {item.id for item in definition.reference_draft.fleet} != native_uavs or any(
        item.count != 1
        or item.assetId != "model:holybro-x500"
        or item.homeFacilityId is not None
        for item in definition.reference_draft.fleet
    ):
        raise ValueError("inspection reference fleet must bind its exact native X500 instance")
    traffic = definition.reference_draft.traffic
    bindings = world.sumo.object_bindings if world.sumo is not None else ()
    if (traffic.vehicles, traffic.pedestrians, traffic.bicycles) != (
        sum(item.kind == "vehicle" for item in bindings),
        sum(item.kind == "person" for item in bindings),
        0,
    ):
        raise ValueError("reference traffic demand must match the declared native SUMO objects")
    draft = definition.reference_draft
    if any((
        draft.facilities,
        draft.airspace,
        draft.events,
        draft.actionRules,
        draft.labelRules,
        draft.orders,
        draft.performanceProfiles,
        draft.authoredLandscape,
    )):
        raise ValueError(
            "inspection reference does not implement authored logistics, landscape, "
            "or scheduled rules"
        )
    if draft.orderGeneration != CityOrderGeneration.disabled(draft.seed):
        raise ValueError("inspection reference must keep order generation disabled")


def verify_native_scene(root: Path, definition: NativeSceneDefinition) -> VerifiedNativeScene:
    root = root.resolve(strict=True)
    reader = BundleReader(root)
    inventory = {entry.file.path: entry for entry in definition.files}
    if inventory.get(definition.scene_document.file.path) != definition.scene_document:
        raise ValueError("selected scene document must be in the exact file inventory")
    for reference in (definition.suite, definition.world, definition.alignment):
        if reference.path not in inventory or inventory[reference.path].file != reference:
            raise ValueError("registered suite/world/alignment must be in the exact file inventory")
    world = WorldPackage.model_validate(reader.load_document(definition.world))
    if (world.world_id, world.world_digest) != (definition.world_id, definition.world_digest):
        raise ValueError("registered native world identity does not match its pinned package")
    alignment = AlignmentManifest.model_validate(reader.load_document(definition.alignment))
    if not alignment.alignments:
        raise ValueError("native registration requires building alignment evidence")
    generator = alignment.alignments[0].generator
    verify_alignment(alignment, reader=reader, world=world, generator=generator)
    assets = {item.artifact.artifact_id: item for item in world.assets}
    required_render_ids = {building.render_asset_id for building in world.buildings}
    covered = set()
    for binding in definition.presentation:
        if binding.file.path not in inventory or inventory[binding.file.path] != RegisteredFile(
            file=binding.file, size_bytes=binding.size_bytes
        ):
            raise ValueError("presentation must be pinned in the exact file inventory")
        for asset_id in binding.world_asset_ids:
            asset = assets.get(asset_id)
            if asset is None or asset.visibility != "public":
                raise ValueError("presentation binding requires an actual public native asset")
            selector_path = asset.artifact.selector.split("#", 1)[0]
            if (binding.file.path, binding.file.sha256, binding.size_bytes) != (
                selector_path, asset.artifact.sha256, asset.byte_size
            ):
                raise ValueError("presentation bytes differ from the declared native rendering asset")
            if asset_id in covered:
                raise ValueError("native presentation binding contains duplicate asset coverage")
            covered.add(asset_id)
    if not required_render_ids.issubset(covered):
        raise ValueError("presentation does not cover every native building rendering asset")
    loaded = load_suite(reader.resolve_file(definition.suite))
    if len(loaded.suite.cases) != 1 or loaded.suite.cases[0].world_package != definition.world:
        raise ValueError("native scene registration requires one exact world-bound case")
    if loaded.suite.cases[0].axes or len(loaded.suite.cases[0].seeds) != 1:
        raise ValueError("native template must not hide a matrix or multiple seeds")
    runs = resolve_suite(
        str(loaded.suite_path), executor_kind="docker_reference",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    if len(runs) != 1:
        raise ValueError("native registration must resolve exactly one run")
    run = runs[0]
    presentation_scene = PublicScenario.model_validate(reader.load_document(definition.scene_document.file))
    if presentation_scene != project_public_scenario(run):
        raise ValueError("selected presentation differs from the exact public native scenario")
    if run.execution_scope != "formal_benchmark" or run.task.task_id != definition.profile_id:
        raise ValueError("native registration must use the declared formal inspection profile")
    _verify_inspection_reference_draft(definition=definition, world=world, run=run)
    verified = VerifiedNativeScene(
        root=root, definition=definition,
        registration_sha256=_definition_sha256(definition),
        world=world, run=run,
    )
    verified.read_files()
    _verify_public_asset_inventory(definition, run)
    return verified


def verify_logistics_arrivals_scene(root: Path, definition: LogisticsArrivalsSceneDefinition) -> VerifiedNativeScene:
    from aero_bench.authoring.reference_registration import _references
    from aero_bench.authoring.order_events import lower_order_events
    from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
    from aero_bench.tasks.logistics_arrivals.integration import LogisticsArrivalsTaskPackageResolver

    root = root.resolve(strict=True)
    reader = BundleReader(root)
    inventory = _registered_inventory(definition)
    for reference in (definition.suite, definition.world, definition.scene_document.file):
        if reference.path not in inventory or inventory[reference.path].file != reference:
            raise ValueError("arrivals registration omits an exact suite/world/public pin")
    loaded = load_suite(reader.resolve_file(definition.suite))
    if (
        len(loaded.suite.cases) != 1 or loaded.suite.cases[0].world_package != definition.world
        or loaded.suite.cases[0].axes or len(loaded.suite.cases[0].seeds) != 1
        or loaded.suite.cases[0].launch_site_ids != (None,)
    ):
        raise ValueError("arrivals registration requires one explicit no-launch case")
    runs = resolve_suite(str(loaded.suite_path), executor_kind="docker_reference",
                         task_package_resolvers=(LogisticsArrivalsTaskPackageResolver(),),
                         provider_registry=builtin_provider_registry())
    if len(runs) != 1 or runs[0].execution_scope != "formal_benchmark" or not runs[0].feasibility.feasible:
        raise ValueError("arrivals registration requires one feasible formal run")
    run = runs[0]
    references = (*_references(loaded.suite.model_dump(mode="json")), *_references(run.model_dump(mode="json")))
    if set(inventory) != {pin.path for pin in references} | {definition.scene_document.file.path} or any(inventory[pin.path].file != pin for pin in references):
        raise ValueError("arrivals registration must close the exact native input inventory")
    world = WorldPackage.model_validate(reader.load_document(definition.world))
    if (world.world_id, world.world_digest) != (definition.world_id, definition.world_digest):
        raise ValueError("arrivals registration changed its world identity")
    if run.task.task_id != definition.profile_id or run.agents[0].workload.implementation.version != "0.4.0-logistics-clock.1":
        raise ValueError("arrivals registration requires its distinct task and clock participant")
    draft = definition.reference_draft
    if (
        draft.seed != run.seed or draft.deployment.imageRef != run.agents[0].workload.runtime.image
        or draft.fleet or draft.facilities or draft.airspace or draft.orders
        or draft.actionRules or draft.labelRules or draft.performanceProfiles or draft.authoredLandscape
        or (draft.traffic.vehicles, draft.traffic.pedestrians, draft.traffic.bicycles) != (0, 0, 0)
        or draft.orderGeneration != CityOrderGeneration.disabled(run.seed)
    ):
        raise ValueError("arrivals reference draft cannot claim physical demand or actuation")
    provider = run.environment.providers[0]
    suite_reader = BundleReader(loaded.root)
    config = LogisticsBusinessConfig.model_validate(suite_reader.validate_schema_bound_file(provider.config))
    scheduled, blockers = lower_order_events(draft.events, run=run, root=loaded.root)
    if blockers or any(event.type != "order.created" for event in draft.events) or scheduled != config.scheduled_orders:
        raise ValueError("arrivals reference events must equal the pinned native schedule")
    raw = _registered_metadata_bytes(root, definition, definition.scene_document)
    if PublicScenario.model_validate(parse_json_object(raw)) != project_public_scenario(run):
        raise ValueError("arrivals presentation differs from its exact public projection")
    verified = VerifiedNativeScene(root=root, definition=definition, registration_sha256=_definition_sha256(definition), world=world, run=run)
    verified.read_files()
    _verify_public_asset_inventory(definition, run)
    return verified


def _registered_metadata_bytes(
    root: Path,
    definition: CityInspectionSceneDefinition | LogisticsArrivalsSceneDefinition,
    registered: RegisteredFile,
) -> bytes:
    return _read_registered_file(
        root=root,
        definition=definition,
        registration_sha256=_definition_sha256(definition),
        registered=registered,
    )


def _expected_inspection_lock(
    source: CityNativeInputLock, registration_id: str,
) -> CityNativeInputLock:
    raw = source.model_dump(mode="json")
    raw["registration_id"] = registration_id
    raw["required_execution_domains"] = list(CITY_INSPECTION_DOMAINS)
    return CityNativeInputLock.model_validate(raw)


def verify_city_inspection_scene(
    root: Path, definition: CityInspectionSceneDefinition,
) -> VerifiedCityInspectionScene:
    """Verify a fixed city inspection catalog entry through the native-city gate."""

    root = root.resolve(strict=True)
    inventory = _registered_inventory(definition)
    metadata = (
        definition.scene_document,
        definition.source_input_lock,
        definition.source_readiness,
        definition.inspection_input_lock,
        definition.inspection_readiness,
        definition.provenance,
    )
    if any(inventory.get(item.file.path) != item for item in metadata):
        raise ValueError("city inspection metadata must be in the exact file inventory")
    for reference in (definition.suite, definition.world):
        item = inventory.get(reference.path)
        if item is None or item.file != reference:
            raise ValueError("registered city suite/world must be in the exact file inventory")

    source_lock_path = root / definition.source_input_lock.file.path
    _registered_metadata_bytes(root, definition, definition.source_input_lock)
    source_lock = load_city_native_input_lock(source_lock_path)
    full_domains = tuple(sorted((*CITY_INSPECTION_DOMAINS, *CITY_FULL_UNSUPPORTED_DOMAINS)))
    if source_lock.required_execution_domains != full_domains:
        raise ValueError("source city lock does not retain the exact broader execution scope")
    source_report = assess_city_native_registration(root, source_lock)
    recorded_source_report = CityNativeReadinessReport.model_validate(
        parse_json_object(_registered_metadata_bytes(
            root, definition, definition.source_readiness,
        ))
    )
    if recorded_source_report != source_report:
        raise ValueError("source city readiness differs from its independently assessed inputs")
    if (
        source_report.status != "blocked"
        or source_report.blocking_codes != CITY_FULL_BLOCKING_CODES
    ):
        raise ValueError("source city readiness must retain only the declared full-city blockers")

    inspection_lock_path = root / definition.inspection_input_lock.file.path
    _registered_metadata_bytes(root, definition, definition.inspection_input_lock)
    inspection_lock = load_city_native_input_lock(inspection_lock_path)
    if inspection_lock != _expected_inspection_lock(source_lock, definition.registration_id):
        raise ValueError(
            "inspection lock may change only registration_id and required execution domains"
        )
    if inspection_lock.execution is None or inspection_lock.world is None:
        raise ValueError("inspection lock omits its exact Suite or native WorldPackage")
    if inspection_lock.execution.task_id != definition.profile_id:
        raise ValueError("city inspection profile differs from the exact Suite task")
    if (
        definition.suite != inspection_lock.execution.suite.file
        or definition.world != inspection_lock.world.world_package.file
    ):
        raise ValueError("city catalog entry differs from its gated Suite or world")
    for pinned in inspection_lock.pinned_files():
        registered = RegisteredFile(file=pinned.file, size_bytes=pinned.size_bytes)
        if inventory.get(pinned.file.path) != registered:
            raise ValueError("city gate input is absent from the exact file inventory")

    try:
        city_registration = verify_city_native_registration(root, inspection_lock)
    except CityNativeRegistrationBlocked as exc:
        raise ValueError("inspection-only city readiness is not ready") from exc
    recorded_inspection_report = CityNativeReadinessReport.model_validate(
        parse_json_object(_registered_metadata_bytes(
            root, definition, definition.inspection_readiness,
        ))
    )
    if recorded_inspection_report != city_registration.report:
        raise ValueError("inspection readiness differs from the verified native-city gate")
    world = city_registration.world
    run = city_registration.run
    if (definition.world_id, definition.world_digest) != (
        world.world_id,
        world.world_digest,
    ):
        raise ValueError("registered city world identity differs from the gated package")
    if source_report.run_id != run.run_id or source_report.world_digest != world.world_digest:
        raise ValueError("inspection lock changed the base city run or world identity")
    if run.execution_scope != "formal_benchmark" or run.task.task_id != definition.profile_id:
        raise ValueError("city registration must use the exact formal inspection task")

    scene_raw = _registered_metadata_bytes(root, definition, definition.scene_document)
    presentation = PublicScenario.model_validate(parse_json_object(scene_raw))
    if presentation != project_public_scenario(run):
        raise ValueError("selected city presentation differs from the exact public scenario")

    provenance = CityInspectionPublicationProvenance.model_validate(
        parse_json_object(_registered_metadata_bytes(root, definition, definition.provenance))
    )
    expected_provenance = CityInspectionPublicationProvenance(
        schema_version=CITY_INSPECTION_PROVENANCE_SCHEMA,
        source_registration_id=source_lock.registration_id,
        source_registration_sha256=source_report.registration_sha256,
        source_input_lock_sha256=definition.source_input_lock.file.sha256,
        source_readiness_sha256=definition.source_readiness.file.sha256,
        source_status="blocked",
        source_blocking_codes=source_report.blocking_codes,
        inspection_registration_id=definition.registration_id,
        inspection_registration_sha256=city_registration.report.registration_sha256,
        inspection_readiness_sha256=definition.inspection_readiness.file.sha256,
        inspection_status="ready",
        profile_id=definition.profile_id,
        implemented_execution_domains=CITY_INSPECTION_DOMAINS,
        unsupported_full_city_domains=CITY_FULL_UNSUPPORTED_DOMAINS,
        base_run_id=run.run_id,
        scenario_digest=run.scenario.scenario_digest,
        world_id=world.world_id,
        world_digest=world.world_digest,
        publication_effect="copied-pinned-inputs-no-execution",
    )
    if provenance != expected_provenance:
        raise ValueError("city inspection publication provenance differs from verified inputs")

    _verify_inspection_reference_draft(definition=definition, world=world, run=run)
    verified = VerifiedCityInspectionScene(
        root=root,
        definition=definition,
        registration_sha256=_definition_sha256(definition),
        city_registration=city_registration,
        world=world,
        run=run,
    )
    verified.read_files()
    _verify_public_asset_inventory(definition, run)
    return verified


class NativeSceneRegistry:
    def __init__(self, scenes: tuple[VerifiedSceneRegistration, ...] = ()):
        ids = [scene.definition.registration_id for scene in scenes]
        if len(set(ids)) != len(ids):
            raise ValueError("native registration IDs must be unique")
        self._scenes = {scene.definition.registration_id: scene for scene in scenes}

    def get(self, registration_id: str) -> VerifiedSceneRegistration | None:
        return self._scenes.get(registration_id)

    def catalog(self) -> SceneRegistrationCatalog:
        return SceneRegistrationCatalog(
            schema_version="aero-bench.city-scene-registration-catalog/v1",
            registrations=tuple(scene.public() for _, scene in sorted(self._scenes.items())),
        )

    @classmethod
    def from_manifest(cls, manifest: Path) -> NativeSceneRegistry:
        document = NativeSceneRegistryManifest.model_validate(parse_json_object(manifest.read_bytes()))
        reader = BundleReader(manifest.resolve(strict=True).parent)
        scenes = []
        for reference in document.registrations:
            path = reader.resolve_file(reference)
            raw = parse_json_object(path.read_bytes())
            schema = raw.get("schema_version")
            if schema == "aero-bench.native-scene-registration/v1":
                definition = NativeSceneDefinition.model_validate(raw)
                scenes.append(verify_native_scene(path.parent, definition))
            elif schema == CITY_INSPECTION_REGISTRATION_SCHEMA:
                city_definition = CityInspectionSceneDefinition.model_validate(raw)
                scenes.append(verify_city_inspection_scene(path.parent, city_definition))
            elif schema == "aero-bench.native-logistics-arrivals-registration/v1":
                scenes.append(verify_logistics_arrivals_scene(path.parent, LogisticsArrivalsSceneDefinition.model_validate(raw)))
            else:
                raise ValueError(f"unsupported native scene registration schema: {schema!r}")
        return cls(tuple(scenes))


class NativeSceneRegistryManifest(StrictModel):
    schema_version: Literal["aero-bench.native-scene-registry/v1"]
    registrations: tuple[FileRef, ...]
