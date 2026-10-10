"""Production logistics scene/bundle builder: the authoring caller of the
px4.gazebo world-input pinning bridge.

The executor world seam (``aero_bench.executor.facility_world``) consumes a
logistics ``px4.gazebo`` provider config whose ``world_input`` (bundle path and
SHA-256) and ``world_name`` declare *exactly* the deterministic facility-spliced
world the executor later re-derives.  Authoring that declaration by hand -- or
leaving it unset -- makes every map/facility change silently produce a Run ID
that omits the world identity, and the omission only surfaces later at executor
materialization.

``build_logistics_bundle`` is the production caller of
``pin_px4_gazebo_world_input``.  Given the authored source logistics bundle
(the scene: world package, task, environment, agents) plus the *explicit*
selected compiled base world, the *explicit* logistics facilities document and
the *explicit* WGS84 scene origin, it

  1. derives the facility-spliced world through the same immutable inputs the
     executor later re-derives, and pins the provider declaration;
  2. copies the source bundle into a *new* bundle root;
  3. stages the pinned provider config, the canonical facilities document, the
     actual facility-spliced world source, and re-pins every affected
     ``FileRef`` (provider config, environment, task, suite);

all *before* the suite is resolved -- so the derived world identity enters the
ResolvedRun Run ID via the pinned provider config digest.

This module never resolves the suite, never claims a runnable logistics runtime
(``LOGISTICS_RUNTIME_IMPLEMENTED`` stays ``False``), never mutates a
``ResolvedRun`` and never fabricates a Provider, flight, observation or world.
Every input is explicit and deterministic; missing, malformed or inconsistent
inputs fail closed instead of being silently substituted.

Scope note (honest limitation): the *upstream* authoring of a strict
``aero-bench.world/v2`` ``WorldPackage`` (the shared city scene) has no
production compiler in this repository -- the only producers are the
``aero_bench.world.contracts.world_package`` factory and test fixtures.  This
builder therefore consumes an already-authored source bundle and is the
deterministic *pinning/staging* stage of the logistics scene->bundle pipeline;
authoring the world package remains an explicit upstream provisioning step.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FileRef,
    SchemaBoundFile,
    TaskSpec,
)
from aero_bench.executor.facility_world import (
    DEFAULT_LOGISTICS_WORLD_DESTINATION,
    LogisticsWorldMaterializationError,
    materialize_logistics_world,
    pin_px4_gazebo_world_input,
    validate_origin_matches_package,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    LogisticsTaskPackage,
    lower_logistics_task_package,
)
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.scene_compiler import SceneOrigin

#: Bundle-root-relative path the derived facility-spliced world source is staged
#: to.  This is the bundle form of the executor's runtime input destination
#: (``bundle/facility/logistics_world.sdf``): ``_bundle_inputs`` maps a bundle
#: file at ``{path}`` to the input-volume path ``bundle/{path}``, so the staged
#: source agrees byte-for-byte with the executor-derived world the provider
#: consumes.  Nothing references this file; it is retained evidence of the
#: declared reproducible derivation.
LOGISTICS_WORLD_SOURCE_RELATIVE = (
    DEFAULT_LOGISTICS_WORLD_DESTINATION.removeprefix("bundle/")
)

#: Scene-origin tolerances the logistics resolver applies when binding a
#: resolved frame authority to a declared package scene (mirrors
#: ``integration._SCENE_ORIGIN_TOLERANCE_*``).
_ORIGIN_TOLERANCE_DEG = 1e-9
_ORIGIN_TOLERANCE_M = 1e-6


class LogisticsBundleAuthoringError(ValueError):
    """An explicit logistics bundle-authoring contract violation."""


@dataclass(frozen=True, slots=True)
class LogisticsBundleRequest:
    """Explicit, deterministic inputs of one logistics bundle build.

    ``source_root`` is an authored logistics suite bundle; ``base_world_sdf`` is
    the selected compiled base world SDF; ``origin`` is the selected WGS84 map
    origin.  ``package_document`` optionally overrides the source facilities
    document (defaults to the source package).  Nothing here is hard-coded to a
    map: any base world, facility list and origin that are mutually consistent
    drive a distinct, reproducible output identity.
    """

    source_root: Path
    output_root: Path
    base_world_sdf: Path
    origin: SceneOrigin
    package_document: Mapping[str, object] | None = None
    flight_provider_id: str = "flight"
    executor_kind: str = "docker_reference"


@dataclass(frozen=True, slots=True)
class LogisticsBundleResult:
    """Identity of one produced logistics bundle (no run is resolved here)."""

    bundle_root: Path
    suite_path: Path
    executor_kind: str
    provider_id: str
    pinned_config_path: Path
    pinned_config_sha256: str
    world_source_path: Path
    world_destination: str
    world_sha256: str
    world_name: str
    package_path: str
    package_sha256: str
    package_digest: str
    base_world_sha256: str
    scene_origin_sha256: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise LogisticsBundleAuthoringError(f"{label} must be a mapping document")
    document = dict(value)
    if any(not isinstance(key, str) for key in document):
        raise LogisticsBundleAuthoringError(f"{label} keys must be strings")
    return document


def _px4_provider(environment: EnvironmentSpec, provider_id: str):
    matches = tuple(
        provider
        for provider in environment.providers
        if provider.provider_id == provider_id
    )
    if len(matches) != 1:
        raise LogisticsBundleAuthoringError(
            "logistics bundle builder requires exactly one px4.gazebo Provider "
            f"with provider_id {provider_id!r}, found {len(matches)}"
        )
    provider = matches[0]
    if provider.adapter != "px4.gazebo":
        raise LogisticsBundleAuthoringError(
            f"logistics flight Provider {provider_id!r} is not a px4.gazebo adapter"
        )
    return provider


def _validate_scene_consistency(
    *,
    package: LogisticsTaskPackage,
    world: WorldPackage,
    origin: SceneOrigin,
) -> None:
    """Fail closed when the package, world package and origin disagree."""
    if package.scene.scene_id != world.world_id:
        raise LogisticsBundleAuthoringError(
            "logistics package scene_id does not match the source world package "
            f"world_id: package={package.scene.scene_id!r}, world={world.world_id!r}"
        )
    if package.scene.scene_source_sha256 != world.asset_digest:
        raise LogisticsBundleAuthoringError(
            "logistics package scene_source_sha256 does not match the source "
            "world package asset digest"
        )
    try:
        validate_origin_matches_package(package=package, origin=origin)
    except LogisticsWorldMaterializationError as exc:
        raise LogisticsBundleAuthoringError(str(exc)) from exc
    frame_origin = world.frame.origin
    if not (
        math.isclose(
            float(origin.longitude_deg),
            float(frame_origin.longitude_deg),
            rel_tol=0.0,
            abs_tol=_ORIGIN_TOLERANCE_DEG,
        )
        and math.isclose(
            float(origin.latitude_deg),
            float(frame_origin.latitude_deg),
            rel_tol=0.0,
            abs_tol=_ORIGIN_TOLERANCE_DEG,
        )
        and math.isclose(
            float(origin.ellipsoid_height_m),
            float(frame_origin.altitude_m),
            rel_tol=0.0,
            abs_tol=_ORIGIN_TOLERANCE_M,
        )
    ):
        raise LogisticsBundleAuthoringError(
            "selected scene origin does not match the source world package frame "
            "origin"
        )


def _staged_world_name(world_bytes: bytes) -> str:
    try:
        root = ET.fromstring(world_bytes)
    except ET.ParseError as exc:
        raise LogisticsBundleAuthoringError(
            "materialized logistics world SDF is not well-formed XML"
        ) from exc
    worlds = [
        element for element in root if element.tag.rsplit("}", 1)[-1] == "world"
    ]
    if len(worlds) != 1:
        raise LogisticsBundleAuthoringError(
            "materialized logistics world must declare exactly one <world>"
        )
    name = worlds[0].attrib.get("name")
    if not name:
        raise LogisticsBundleAuthoringError(
            "materialized logistics world <world> has no name attribute"
        )
    return name


def _reject_overlapping_roots(*, source_root: Path, output_root: Path) -> None:
    """Fail closed when ``output_root`` aliases or nests inside ``source_root``.

    ``shutil.copytree`` walks the source while writing the output, so an output
    that resolves inside the source (directly, as an ancestor, or through a
    symlink alias) would copy the bundle into itself -- mutating the source and
    leaving a partial tree.  Comparing fully resolved paths closes the symlink
    alias evasion.
    """
    source_resolved = source_root.resolve()
    output_resolved = output_root.resolve()
    if (
        output_resolved == source_resolved
        or output_resolved.is_relative_to(source_resolved)
        or source_resolved.is_relative_to(output_resolved)
    ):
        raise LogisticsBundleAuthoringError(
            "output root overlaps the source logistics bundle root: "
            f"source={source_resolved}, output={output_resolved}"
        )


def _staging_directory(output_root: Path) -> Path:
    """A fresh private sibling of ``output_root`` on the same filesystem.

    Staging next to the destination (rather than in a system temp dir) keeps the
    final commit a same-filesystem rename.
    """
    parent = output_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f".{output_root.name}.staging-{uuid.uuid4().hex}"


def _commit_staged_bundle(staged_root: Path, output_root: Path) -> None:
    """Atomically move the fully staged bundle onto ``output_root``.

    ``output_root`` was validated absent or empty before staging; re-validate
    here so a racing writer cannot make the commit clobber unexpected bytes.
    """
    if output_root.is_symlink():
        raise LogisticsBundleAuthoringError(
            f"output root must not be a symbolic link: {output_root}"
        )
    if output_root.exists():
        if not output_root.is_dir():
            raise LogisticsBundleAuthoringError(
                f"output root exists and is not a directory: {output_root}"
            )
        if any(output_root.iterdir()):
            raise LogisticsBundleAuthoringError(
                f"output root must not already contain files: {output_root}"
            )
        output_root.rmdir()
    os.replace(staged_root, output_root)



def build_logistics_bundle(request: LogisticsBundleRequest) -> LogisticsBundleResult:
    """Derive, pin and stage one logistics bundle, before ``resolve_suite``.

    Raises :class:`LogisticsBundleAuthoringError` for every missing, malformed
    or inconsistent input.  The output is staged in a private sibling directory
    and committed with a single atomic rename only after every document has been
    written, so a failure never leaves a partial bundle at ``output_root`` (the
    staging sibling is removed; a missing output parent may remain).  An
    ``output_root`` that resolves to, contains, or nests inside ``source_root``
    -- including through a symlink alias -- is rejected before anything is
    written.
    """
    if not isinstance(request, LogisticsBundleRequest):
        raise LogisticsBundleAuthoringError(
            "build_logistics_bundle requires a LogisticsBundleRequest"
        )
    source_root = Path(request.source_root)
    output_root = Path(request.output_root)
    base_world_path = Path(request.base_world_sdf)
    if not isinstance(request.origin, SceneOrigin):
        raise LogisticsBundleAuthoringError("origin must be a SceneOrigin")

    # ---- fail-closed input validation (before writing anything) ----------- #
    if not source_root.is_dir():
        raise LogisticsBundleAuthoringError(
            f"source logistics bundle root does not exist: {source_root}"
        )
    _reject_overlapping_roots(source_root=source_root, output_root=output_root)
    if not base_world_path.is_file():
        raise LogisticsBundleAuthoringError(
            f"compiled base world SDF does not exist: {base_world_path}"
        )
    try:
        base_world_bytes = base_world_path.read_bytes()
    except OSError as exc:
        raise LogisticsBundleAuthoringError(
            f"cannot read compiled base world SDF {base_world_path}: {exc}"
        ) from exc
    if not base_world_bytes:
        raise LogisticsBundleAuthoringError("compiled base world SDF is empty")
    if output_root.is_symlink():
        raise LogisticsBundleAuthoringError(
            f"output root must not be a symbolic link: {output_root}"
        )
    if output_root.exists():
        if not output_root.is_dir():
            raise LogisticsBundleAuthoringError(
                f"output root exists and is not a directory: {output_root}"
            )
        if any(output_root.iterdir()):
            raise LogisticsBundleAuthoringError(
                f"output root must not already contain files: {output_root}"
            )
    if request.package_document is not None and not isinstance(
        request.package_document, Mapping
    ):
        raise LogisticsBundleAuthoringError(
            "package_document must be a mapping document or None"
        )
    if not request.flight_provider_id:
        raise LogisticsBundleAuthoringError("flight_provider_id must be non-empty")

    # ---- read the source scene bundle (loader validates every FileRef) ----- #
    suite_relative = "suite.yaml"
    loaded = load_suite(str(source_root / suite_relative))
    source_root_resolved = source_root.resolve()
    if loaded.root != source_root_resolved:
        raise LogisticsBundleAuthoringError(
            "source suite root does not match the source bundle root"
        )
    if len(loaded.suite.cases) != 1:
        raise LogisticsBundleAuthoringError(
            "logistics bundle builder supports exactly one suite case"
        )
    case = loaded.suite.cases[0]
    reader = BundleReader(loaded.root)
    task = reader.load_yaml(case.task, TaskSpec)
    environment = reader.load_yaml(case.environment, EnvironmentSpec)
    for agent_ref in case.agents:
        reader.load_yaml(agent_ref, AgentSpec)
    if task.package.package_id != LOGISTICS_PACKAGE_ID:
        raise LogisticsBundleAuthoringError(
            "source task is not a logistics TaskSpec package, found "
            f"{task.package.package_id}"
        )
    world = WorldPackage.model_validate(reader.load_document(case.world_package))

    # ---- explicit facilities document -> lowered package ------------------- #
    if request.package_document is None:
        document = _require_mapping(
            reader.load_document(task.package.config.file),
            "source logistics package document",
        )
    else:
        document = _require_mapping(
            request.package_document, "package_document"
        )
    try:
        package = lower_logistics_task_package(document)
    except ValueError as exc:
        raise LogisticsBundleAuthoringError(
            f"logistics package document is not a valid logistics package: {exc}"
        ) from exc
    if package.task_id != task.task_id:
        raise LogisticsBundleAuthoringError(
            "logistics package task_id does not match the source task"
        )
    if package.verifier_id != task.verifier.verifier_id:
        raise LogisticsBundleAuthoringError(
            "logistics package verifier_id does not match the source task verifier"
        )
    _validate_scene_consistency(package=package, world=world, origin=request.origin)

    # ---- derive + pin the world declaration (fail closed) ------------------ #
    provider = _px4_provider(environment, request.flight_provider_id)
    config_path = provider.config.file.path
    try:
        config_document = json.loads(
            reader.resolve_file(provider.config.file).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise LogisticsBundleAuthoringError(
            f"cannot read the source px4.gazebo provider config {config_path!r}: {exc}"
        ) from exc
    try:
        pinned_config, pinned_bytes = pin_px4_gazebo_world_input(
            provider_config=config_document,
            package=package,
            origin=request.origin,
            base_world=base_world_bytes,
        )
        world_plan, derived = materialize_logistics_world(
            package=package,
            origin=request.origin,
            base_world=base_world_bytes,
            provider_id=provider.provider_id,
        )
    except LogisticsWorldMaterializationError as exc:
        raise LogisticsBundleAuthoringError(str(exc)) from exc

    world_source_bytes = derived.content_utf8.encode("utf-8")
    declared = pinned_config.world_input
    if (
        declared is None
        or declared.path != derived.destination
        or declared.sha256 != world_plan.world_sha256
        or declared.sha256 != _sha256(world_source_bytes)
        or pinned_config.world_name != _staged_world_name(world_source_bytes)
    ):  # pragma: no cover - pin_px4_gazebo_world_input guarantees this
        raise LogisticsBundleAuthoringError(
            "pinned px4.gazebo config did not retain the derived world declaration"
        )

    # ---- stage in a sibling, commit only once every write has succeeded --- #
    staged_root = _staging_directory(output_root)
    try:
        shutil.copytree(source_root_resolved, staged_root)
        bundle_root = staged_root

        package_bytes = canonical_json_bytes(document) + b"\n"
        package_relative = task.package.config.file.path
        (bundle_root / package_relative).write_bytes(package_bytes)
        new_package_ref = FileRef(
            path=package_relative, sha256=_sha256(package_bytes)
        )

        new_task = task.model_copy(
            update={
                "package": task.package.model_copy(
                    update={
                        "config": SchemaBoundFile(
                            file=new_package_ref,
                            schema_file=task.package.config.schema_file,
                        )
                    }
                )
            }
        )
        task_bytes = yaml.safe_dump(
            new_task.model_dump(mode="json"), sort_keys=False
        ).encode("utf-8")
        task_relative = case.task.path
        (bundle_root / task_relative).write_bytes(task_bytes)
        new_task_ref = FileRef(path=task_relative, sha256=_sha256(task_bytes))

        pinned_config_path = bundle_root / config_path
        pinned_config_path.write_bytes(pinned_bytes)
        new_pinned_ref = FileRef(path=config_path, sha256=_sha256(pinned_bytes))
        new_environment = environment.model_copy(
            update={
                "providers": tuple(
                    item.model_copy(
                        update={
                            "config": SchemaBoundFile(
                                file=new_pinned_ref,
                                schema_file=item.config.schema_file,
                            )
                        }
                    )
                    if item.provider_id == provider.provider_id
                    else item
                    for item in environment.providers
                )
            }
        )
        env_bytes = yaml.safe_dump(
            new_environment.model_dump(mode="json"), sort_keys=False
        ).encode("utf-8")
        env_relative = case.environment.path
        (bundle_root / env_relative).write_bytes(env_bytes)
        new_env_ref = FileRef(path=env_relative, sha256=_sha256(env_bytes))

        world_source_path = bundle_root / LOGISTICS_WORLD_SOURCE_RELATIVE
        world_source_path.parent.mkdir(parents=True, exist_ok=True)
        world_source_path.write_bytes(world_source_bytes)

        suite_document = yaml.safe_load(
            (loaded.root / suite_relative).read_text(encoding="utf-8")
        )
        if not isinstance(suite_document, dict) or not isinstance(
            suite_document.get("cases"), list
        ):
            raise LogisticsBundleAuthoringError("source suite document is malformed")
        suite_document["cases"][0]["task"] = new_task_ref.model_dump(mode="json")
        suite_document["cases"][0]["environment"] = new_env_ref.model_dump(
            mode="json"
        )
        suite_path = bundle_root / suite_relative
        suite_path.write_bytes(
            yaml.safe_dump(suite_document, sort_keys=False).encode("utf-8")
        )

        _commit_staged_bundle(staged_root, output_root)
    except BaseException:
        shutil.rmtree(staged_root, ignore_errors=True)
        raise

    bundle_root = output_root.resolve()
    return LogisticsBundleResult(
        bundle_root=bundle_root,
        suite_path=bundle_root / suite_relative,
        executor_kind=request.executor_kind,
        provider_id=provider.provider_id,
        pinned_config_path=bundle_root / config_path,
        pinned_config_sha256=_sha256(pinned_bytes),
        world_source_path=bundle_root / LOGISTICS_WORLD_SOURCE_RELATIVE,
        world_destination=derived.destination,
        world_sha256=world_plan.world_sha256,
        world_name=pinned_config.world_name,
        package_path=package_relative,
        package_sha256=_sha256(package_bytes),
        package_digest=package.canonical_digest(),
        base_world_sha256=_sha256(base_world_bytes),
        scene_origin_sha256=world_plan.scene_origin_sha256,
    )


__all__ = [
    "LOGISTICS_WORLD_SOURCE_RELATIVE",
    "LogisticsBundleAuthoringError",
    "LogisticsBundleRequest",
    "LogisticsBundleResult",
    "build_logistics_bundle",
]
