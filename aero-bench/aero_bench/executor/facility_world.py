"""Executor materialization seam for logistics facility worlds.

This is the *missing seam* in the formal
``ResolvedRun -> executor materialization -> px4.gazebo provider`` path: when a
ResolvedRun declares a logistics TaskSpec package, the executor must stage a
deterministic facility-spliced SDF world into the ``px4.gazebo`` Provider
workload the same way it stages every other pinned workload input.  The bytes
are derived here, never read from a mounted host path, and every relevant
digest (package, base world, resolved scene origin, staged world) is bound to
the immutable ``ExecutionPlan`` formal identity.

The derivation itself is ``aero_bench.tasks.logistics.facility_world``: it
consumes the committed acceptance geometry and splices every configured
facility's ``launch_pad.*`` / ``facility_*`` static models into an existing
``<world>`` document, verified at ``ensure_paused_world_sdf`` (the actual
PX4/Gazebo world staging function in ``containers/px4-gazebo/service.py``).

This module never claims a successful logistics run.  It rejects
unsupported/missing configuration explicitly, and the logistics runtime flag
(``LOGISTICS_RUNTIME_IMPLEMENTED``) remains untouched - materializing a world
is not executing a flight.
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Sequence, TypeAlias

from pydantic import ValidationError

from aero_bench.config.loader import BundleReader
from aero_bench.executor.contracts import (
    ExecutionPlan,
    InlineInputPlan,
    LogisticsWorldPlan,
    WorkloadPlan,
)
from aero_bench.world.scene_compiler import SceneOrigin

#: The workload adapter that physically stages the facility-spliced world into
#: the actual PX4/Gazebo run (the committed inspection-v1 environment adapter).
PX4_GAZEBO_ADAPTER = "px4.gazebo"

#: Normalized bundle destination the staged world SDF is written to.  The
#: convention matches ``_bundle_inputs`` (``bundle/{path}``) so the Provider
#: resolves the file under its ``AERO_BENCH_BUNDLE_DIR`` without any host mount.
DEFAULT_LOGISTICS_WORLD_DESTINATION = "bundle/facility/logistics_world.sdf"


class LogisticsWorldMaterializationError(ValueError):
    """An explicit executor-materialization contract violation."""


def _require_normalized_relative_destination(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or value.strip() != value
        or "\\" in value
    ):
        raise LogisticsWorldMaterializationError(
            "logistics world destination must be a normalized relative path"
        )
    return value


def scene_origin_digest(origin: SceneOrigin) -> str:
    """Deterministic SHA-256 of the resolved SceneOrigin frame authority values."""
    if not isinstance(origin, SceneOrigin):
        raise LogisticsWorldMaterializationError("origin must be a SceneOrigin")
    from aero_bench.serialization import canonical_json_bytes

    payload = {
        "latitude_deg": float(origin.latitude_deg),
        "longitude_deg": float(origin.longitude_deg),
        "ellipsoid_height_m": float(origin.ellipsoid_height_m),
        "geoid_undulation_m": float(origin.geoid_undulation_m),
        "amsl_m": float(origin.amsl_m),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def base_world_bytes(base_world: bytes | Path) -> bytes:
    """Return the base world SDF bytes, rejecting unsupported input shapes."""
    if isinstance(base_world, Path):
        try:
            return base_world.read_bytes()
        except OSError as exc:
            raise LogisticsWorldMaterializationError(
                f"cannot read base world SDF {base_world}: {exc}"
            ) from exc
    if isinstance(base_world, (bytes, bytearray)):
        return bytes(base_world)
    raise LogisticsWorldMaterializationError(
        "base_world must be SDF bytes or a Path to an SDF file"
    )


def base_world_digest(base_world: bytes | Path) -> str:
    return hashlib.sha256(base_world_bytes(base_world)).hexdigest()


def logistics_package_for_run(
    *,
    reader: BundleReader,
    run,
) -> LogisticsTaskPackage:
    """Load the declared logistics package of a ResolvedRun (immutable input)."""
    from aero_bench.tasks.logistics.contracts import LOGISTICS_PACKAGE_ID
    from aero_bench.tasks.logistics.integration import load_logistics_package
    from aero_bench.tasks.logistics.native_parcel_integration import (
        NATIVE_PARCEL_PACKAGE_ID, load_native_parcel_package,
    )

    if run.task.package.package_id == NATIVE_PARCEL_PACKAGE_ID:
        package = load_native_parcel_package(reader=reader, task=run.task)
    elif run.task.package.package_id == LOGISTICS_PACKAGE_ID:
        package = load_logistics_package(reader=reader, task=run.task)
    else:
        raise LogisticsWorldMaterializationError(
            f"logistics world materialization received {run.task.package.package_id}"
        )
    if package.task_id != run.task.task_id:
        raise LogisticsWorldMaterializationError(
            "logistics package task_id does not match the TaskSpec"
        )
    return package


def validate_origin_matches_package(
    *,
    package: LogisticsTaskPackage,
    origin: SceneOrigin,
) -> None:
    """Bind the resolved SceneOrigin to the declared package scene origin.

    Mirrors the accepted logistics scene binding tolerance
    (``integration._SCENE_ORIGIN_TOLERANCE_*``): a resolved origin that is not
    the package's WGS84 origin would silently place the pads kilometres away.
    """
    declared = package.scene
    if not math.isclose(
        float(origin.longitude_deg),
        float(declared.origin_longitude_deg),
        rel_tol=0.0,
        abs_tol=1e-9,
    ) or not math.isclose(
        float(origin.latitude_deg),
        float(declared.origin_latitude_deg),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise LogisticsWorldMaterializationError(
            "resolved SceneOrigin does not match the declared logistics package "
            "scene WGS84 origin"
        )
    if not math.isclose(
        float(origin.ellipsoid_height_m),
        float(declared.origin_altitude_m),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise LogisticsWorldMaterializationError(
            "resolved SceneOrigin altitude does not match the declared logistics "
            "package scene altitude"
        )


def materialize_logistics_world(
    *,
    package: LogisticsTaskPackage,
    origin: SceneOrigin,
    base_world: bytes | Path,
    provider_id: str,
    destination: str = DEFAULT_LOGISTICS_WORLD_DESTINATION,
) -> tuple[LogisticsWorldPlan, InlineInputPlan]:
    """Derive the deterministic facility-spliced world and its formal plan inputs.

    Returns:
        A validated ``LogisticsWorldPlan`` (the formal digest identity) and an
        ``InlineInputPlan`` (the exact staged bytes) that the executor adds to
        the ``px4.gazebo`` Provider workload.  No host path is mounted; the
        world bytes are content-derived and bound by SHA-256.

    Raises:
        :class:`LogisticsWorldMaterializationError` for unsupported/missing
        package, origin, base world, provider, or geometry inputs.
    """
    # Function-local so the executor package stays importable on snapshots
    # where the logistics world-v2 resolved surface is absent.
    from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
    from aero_bench.tasks.logistics.facility_world import (
        FacilityWorldError,
        combined_facility_physics,
        facility_world_bytes,
    )

    if not isinstance(package, LogisticsTaskPackage):
        raise LogisticsWorldMaterializationError(
            "package must be a lowered LogisticsTaskPackage"
        )
    if not isinstance(origin, SceneOrigin):
        raise LogisticsWorldMaterializationError("origin must be a SceneOrigin")
    _require_normalized_relative_destination(destination)
    if not provider_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for c in provider_id) or provider_id.startswith((".", "_", "-")):
        raise LogisticsWorldMaterializationError(
            "logistics world provider_id must be a declared Provider identifier"
        )

    validate_origin_matches_package(package=package, origin=origin)

    try:
        combined = combined_facility_physics(package.facilities)
    except FacilityWorldError as exc:
        raise LogisticsWorldMaterializationError(str(exc)) from exc

    raw = base_world_bytes(base_world)
    try:
        world = facility_world_bytes(
            raw,
            facilities=package.facilities,
            origin=origin,
        )
    except FacilityWorldError as exc:
        raise LogisticsWorldMaterializationError(str(exc)) from exc

    world_sha256 = hashlib.sha256(world).hexdigest()
    from aero_bench.serialization import canonical_json_bytes

    plan = LogisticsWorldPlan(
        schema_version="aero-bench.logistics-world-plan/v1",
        destination=destination,
        world_sha256=world_sha256,
        world_size_bytes=len(world),
        package_sha256=package.canonical_digest(),
        base_world_sha256=base_world_digest(raw),
        scene_origin_sha256=scene_origin_digest(origin),
        facility_ids=tuple(item.facility_id for item in package.facilities.facilities),
        facility_pad_count=sum(len(physics.contacts) for physics in combined),
        provider_id=provider_id,
    )
    derived = InlineInputPlan(
        destination=destination,
        content_utf8=world.decode("utf-8"),
        sha256=world_sha256,
    )
    return plan, derived


def px4_gazebo_provider_id(run) -> str:
    """Identifier of the single declared ``px4.gazebo`` Provider workload."""
    providers = tuple(
        provider
        for provider in run.environment.providers
        if provider.adapter == PX4_GAZEBO_ADAPTER
    )
    if not providers:
        raise LogisticsWorldMaterializationError(
            "logistics world materialization requires a declared px4.gazebo "
            "Provider in the run environment"
        )
    if len(providers) > 1:
        raise LogisticsWorldMaterializationError(
            "logistics world materialization requires exactly one px4.gazebo "
            "Provider, found "
            f"{len(providers)}"
        )
    return providers[0].provider_id


def _staged_world_name(world_bytes: bytes) -> str:
    """Name of the single ``<world>`` element of the derived SDF bytes."""
    try:
        root = ET.fromstring(world_bytes)
    except ET.ParseError as exc:
        raise LogisticsWorldMaterializationError(
            "materialized logistics world SDF is not well-formed XML"
        ) from exc

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    worlds = [element for element in root if local(element.tag) == "world"]
    if len(worlds) != 1:
        raise LogisticsWorldMaterializationError(
            "materialized logistics world must declare exactly one <world>, "
            f"found {len(worlds)}"
        )
    name = worlds[0].attrib.get("name")
    if not name:
        raise LogisticsWorldMaterializationError(
            "materialized logistics world <world> has no name attribute"
        )
    return name


def pin_px4_gazebo_world_input(
    *,
    provider_config: Mapping[str, object],
    package: LogisticsTaskPackage,
    origin: SceneOrigin,
    base_world: bytes | Path,
) -> tuple[Px4GazeboConfig, bytes]:
    """Derive and pin the bundle world declaration of a px4.gazebo provider config.

    This is the *authoring* half of the executor world seam and the exact
    counterpart of :func:`bind_provider_bundle_world_input` (the materialization
    half).  A map/facility pipeline authors the ``px4.gazebo`` provider config
    with no ``world_input``/``world_name`` declaration — or with a previously
    pinned one — and calls this *before* the suite is resolved, so the derived
    facility-spliced world identity is bound into the provider config digest and
    therefore into the ResolvedRun Run ID.  ``ResolvedRun`` does not exist at
    this point and is never touched.

    The spliced world is derived from the same immutable inputs the executor
    later re-derives (``materialize_logistics_world``): the lowered logistics
    package, the resolved ``SceneOrigin``, the compiled base world SDF, and the
    provider's own ``provider_id``.  The returned config declares exactly that
    ``world_input`` (destination path + SHA-256) and the staged SDF ``<world>``
    ``world_name``.

    Fail-closed, with no silent substitution:

    * an authored ``world_input`` present but not equal to the derived
      destination/digest is rejected rather than overwritten (a stale pin is an
      explicit contract violation);
    * an authored ``world_name`` present but not equal to the staged SDF name is
      rejected;
    * any input the derivation itself rejects (non-logistics package, mismatched
      origin, malformed base world, ...) propagates as
      :class:`LogisticsWorldMaterializationError`.

    The input mapping is never mutated.  The pinned declaration is returned as a
    new validated ``Px4GazeboConfig`` plus its canonical bundle bytes (canonical
    JSON with a trailing newline) so the caller can stage the file and re-pin the
    environment's ``config.file`` ``FileRef`` before resolution.
    """
    # Function-local so the executor package stays importable on snapshots
    # where the logistics world-v2 resolved surface is absent.
    from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
    from aero_bench.serialization import canonical_json_bytes

    if not isinstance(provider_config, Mapping):
        raise LogisticsWorldMaterializationError(
            "px4.gazebo provider config must be a mapping document"
        )
    try:
        config = Px4GazeboConfig.model_validate(provider_config)
    except ValidationError as exc:
        raise LogisticsWorldMaterializationError(
            "px4.gazebo provider config is not a valid logistics world-v2 "
            f"Px4GazeboConfig: {exc}"
        ) from exc

    world_plan, derived = materialize_logistics_world(
        package=package,
        origin=origin,
        base_world=base_world,
        provider_id=config.provider_id,
    )
    staged_name = _staged_world_name(derived.content_utf8.encode("utf-8"))

    declared = config.world_input
    if declared is not None and (
        declared.path != derived.destination
        or declared.sha256 != world_plan.world_sha256
    ):
        raise LogisticsWorldMaterializationError(
            "authored px4.gazebo world_input does not match the derived "
            "facility-spliced world; refusing to overwrite a stale declaration: "
            f"declared=({declared.path!r}, {declared.sha256}), "
            f"derived=({derived.destination!r}, {world_plan.world_sha256})"
        )
    if config.world_name is not None and config.world_name != staged_name:
        raise LogisticsWorldMaterializationError(
            "authored px4.gazebo world_name does not match the staged logistics "
            f"world SDF name: authored={config.world_name!r}, staged={staged_name!r}"
        )

    pinned_document = config.model_dump(mode="json")
    pinned_document["world_name"] = staged_name
    pinned_document["world_input"] = {
        "schema_version": "aero-bench.px4-gazebo/bundle-world/v1",
        "source": "bundle",
        "path": derived.destination,
        "sha256": world_plan.world_sha256,
    }
    try:
        pinned = Px4GazeboConfig.model_validate(pinned_document)
    except ValidationError as exc:
        raise LogisticsWorldMaterializationError(
            "pinned px4.gazebo provider config is not a valid logistics "
            f"world-v2 Px4GazeboConfig: {exc}"
        ) from exc
    if (
        pinned.world_input is None
        or pinned.world_name is None
        or pinned.world_input.sha256 != world_plan.world_sha256
        or pinned.world_input.path != derived.destination
        or pinned.world_name != staged_name
    ):  # pragma: no cover - the explicit assignment above guarantees this
        raise LogisticsWorldMaterializationError(
            "pinned px4.gazebo config did not retain the derived world declaration"
        )
    return pinned, canonical_json_bytes(pinned.model_dump(mode="json")) + b"\n"


def bind_provider_bundle_world_input(
    *,
    plan: ExecutionPlan,
    world_bytes: bytes,
    world_sha256: str,
    derived_destination: str,
) -> None:
    """Fail-closed cross-module binding of the staged logistics world.

    The ``px4.gazebo`` Provider workload consumes the facility-spliced world
    ONLY when its pinned provider config declares
    ``Px4GazeboConfig.world_input`` pointing at the exact executor-derived SDF.
    This binding re-reads that provider config from the materialization bundle
    and requires:

    * ``world_input`` is set (a logistics run whose px4 provider config leaves
      it unset would silently select the scenario world - the staged logistics
      pads would never be simulated);
    * ``world_input.path`` equals the derived world's input destination;
    * ``world_input.sha256`` equals the derived world digest;
    * ``Px4GazeboConfig.world_name`` equals the staged SDF's ``<world>`` name
      (the service's ``_bundle_world_sdf`` startup gate requires the same).

    Any violation is an explicit :class:`LogisticsWorldMaterializationError`;
    the immutable ResolvedRun is never mutated and no provider config is
    silently substituted.
    """
    if not isinstance(plan, ExecutionPlan):
        raise LogisticsWorldMaterializationError(
            "provider bundle-world binding requires an ExecutionPlan"
        )
    provider = next(
        (
            item
            for item in plan.run.environment.providers
            if item.adapter == PX4_GAZEBO_ADAPTER
        ),
        None,
    )
    if provider is None:
        raise LogisticsWorldMaterializationError(
            "logistics world materialization requires a declared px4.gazebo "
            "Provider in the run environment"
        )
    bundle_root = Path(plan.bundle_root)
    if not bundle_root.is_absolute():
        raise LogisticsWorldMaterializationError(
            "logistics world binding requires an absolute bundle_root"
        )
    config_path = bundle_root / provider.config.file.path
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LogisticsWorldMaterializationError(
            "logistics world binding cannot read the px4.gazebo provider "
            f"config {provider.config.file.path!r}: {exc}"
        ) from exc
    from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig

    try:
        config = Px4GazeboConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise LogisticsWorldMaterializationError(
            "px4.gazebo provider config is not a valid logistics world-v2 "
            f"Px4GazeboConfig: {exc}"
        ) from exc
    declared = config.world_input
    if declared is None:
        raise LogisticsWorldMaterializationError(
            "px4.gazebo provider config must declare world_input for a "
            "logistics run; without it the service would silently select the "
            "scenario world and the staged logistics pads would never be "
            "simulated"
        )
    if declared.path != derived_destination:
        raise LogisticsWorldMaterializationError(
            "px4.gazebo world_input.path does not match the staged logistics "
            f"world destination: declared={declared.path!r}, "
            f"staged={derived_destination!r}"
        )
    if declared.sha256 != world_sha256:
        raise LogisticsWorldMaterializationError(
            "px4.gazebo world_input.sha256 does not match the executor-derived "
            f"logistics world digest: declared={declared.sha256}, "
            f"derived={world_sha256}"
        )
    staged_name = _staged_world_name(world_bytes)
    if config.world_name != staged_name:
        raise LogisticsWorldMaterializationError(
            "px4.gazebo world_name does not match the staged logistics world "
            f"SDF name: configured={config.world_name!r}, staged={staged_name!r}"
        )


def apply_logistics_world_materialization(
    plan: ExecutionPlan,
    *,
    package: LogisticsTaskPackage,
    origin: SceneOrigin,
    base_world: bytes | Path,
) -> ExecutionPlan:
    """Return a *new* ExecutionPlan with the staged facility world added.

    The input ``plan`` (and its ResolvedRun) is immutable and never modified.
    The px4.gazebo Provider workload gains the derived world input, the plan
    carries the formal ``LogisticsWorldPlan`` identity record, and the declared
    px4.gazebo provider config is fail-closed-bound to the exact staged bytes.
    """
    provider_id = px4_gazebo_provider_id(plan.run)
    world_plan, derived = materialize_logistics_world(
        package=package,
        origin=origin,
        base_world=base_world,
        provider_id=provider_id,
    )
    rebuilt: list[WorkloadPlan] = []
    matched = False
    for workload in plan.runtime_workloads:
        if workload.workload_id == provider_id:
            workload = workload.model_copy(
                update={"derived_inputs": (*workload.derived_inputs, derived)},
            )
            matched = True
        rebuilt.append(workload)
    if not matched:
        raise LogisticsWorldMaterializationError(
            f"px4.gazebo provider workload {provider_id!r} is not in the "
            "execution plan"
        )
    updated = plan.model_copy(
        update={
            "runtime_workloads": tuple(rebuilt),
            "logistics_world": world_plan,
        }
    )
    bind_provider_bundle_world_input(
        plan=plan,
        world_bytes=derived.content_utf8.encode("utf-8"),
        world_sha256=world_plan.world_sha256,
        derived_destination=derived.destination,
    )
    return updated


__all__ = [
    "DEFAULT_LOGISTICS_WORLD_DESTINATION",
    "PX4_GAZEBO_ADAPTER",
    "LogisticsWorldMaterializationError",
    "apply_logistics_world_materialization",
    "base_world_digest",
    "base_world_bytes",
    "logistics_package_for_run",
    "materialize_logistics_world",
    "pin_px4_gazebo_world_input",
    "px4_gazebo_provider_id",
    "scene_origin_digest",
    "validate_origin_matches_package",
]
