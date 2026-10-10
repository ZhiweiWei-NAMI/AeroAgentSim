"""Focused executor materialization seam tests for logistics facility worlds.

Cover the bounded executor seam exactly: deterministic facility-spliced world
derivation, formal digest identity, immutable ExecutionPlan update, and
explicit rejection of every unsupported/missing configuration.  The
px4.gazebo Provider context and planning branch behavior are exercised here;
the provider-facing staging integration (``ensure_paused_world_sdf``) lives in
``test_executor_facility_world_integration.py``.
"""

from __future__ import annotations

from tests.executor.logistics_package_fixture import logistics_fixture_provider_registry

import hashlib
from pathlib import Path

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.executor.planning import build_execution_plan
from aero_bench.executor.contracts import InlineInputPlan
from aero_bench.executor.facility_world import (
    DEFAULT_LOGISTICS_WORLD_DESTINATION,
    PX4_GAZEBO_ADAPTER,
    LogisticsWorldMaterializationError,
    apply_logistics_world_materialization,
    base_world_digest,
    logistics_package_for_run,
    materialize_logistics_world,
    px4_gazebo_provider_id,
    scene_origin_digest,
)
from aero_bench.tasks.logistics.facility_world import combined_facility_physics
from aero_bench.world.scene_compiler import SceneOrigin
from tests.executor.logistics_package_fixture import (
    LOGISTICS_PACKAGE_ID,
    PX4_PROVIDER_ID,
    base_execution_plan,
    base_world_sdf_bytes,
    logistics_package,
    logistics_resolved_run,
    materialize_logistics_suite,
)


def _origin(**overrides: object) -> SceneOrigin:
    values: dict[str, object] = {
        "latitude_deg": 39.916,
        "longitude_deg": 116.397,
        "amsl_m": 43.0,
        "ellipsoid_height_m": 43.0,
        "geoid_undulation_m": 0.0,
    }
    values.update(overrides)
    return SceneOrigin(**values)


def _multi_pad_package():
    # 4 facilities, 9 configured landing contacts (2 vertiport + 2 hub + 3 charger).
    return logistics_package(park_slots=2, hub_slots=2, charge_slots=3)


def test_materialization_is_deterministic_and_binds_every_digest() -> None:
    package = _multi_pad_package()
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)

    plan_a, derived_a = materialize_logistics_world(
        package=package, origin=origin, base_world=base, provider_id="flight"
    )
    plan_b, derived_b = materialize_logistics_world(
        package=package, origin=origin, base_world=base, provider_id="flight"
    )

    # Determinism: the same resolved package, origin, and base world always
    # derive the exact same world bytes and formal identity.
    assert plan_a == plan_b
    assert derived_a == derived_b
    assert derived_a.content_utf8 == derived_b.content_utf8
    assert isinstance(derived_a, InlineInputPlan)

    # The staged world bytes are content-identified end to end.
    assert plan_a.world_sha256 == derived_a.sha256
    assert plan_a.world_size_bytes == len(derived_a.content_utf8.encode("utf-8"))
    assert hashlib.sha256(
        derived_a.content_utf8.encode("utf-8")
    ).hexdigest() == plan_a.world_sha256

    # Every relevant digest and geometry decision is bound to the plan identity.
    assert plan_a.package_sha256 == package.canonical_digest()
    assert plan_a.base_world_sha256 == base_world_digest(base)
    assert plan_a.scene_origin_sha256 == scene_origin_digest(origin)
    assert plan_a.facility_ids == tuple(
        facility.facility_id for facility in package.facilities.facilities
    )
    combined = combined_facility_physics(package.facilities)
    assert plan_a.facility_pad_count == sum(
        len(physics.contacts) for physics in combined
    ) == 9
    assert plan_a.provider_id == "flight"
    assert plan_a.destination == DEFAULT_LOGISTICS_WORLD_DESTINATION
    assert plan_a.destination == "bundle/facility/logistics_world.sdf"


def test_changing_the_base_world_changes_the_materialized_world() -> None:
    package = _multi_pad_package()
    origin = _origin()
    base_a = base_world_sdf_bytes(origin=origin)
    base_b = base_a.replace(
        b'<world name="compiled_urban_scene">', b'<world name="other_scene">'
    )

    plan_a, _ = materialize_logistics_world(
        package=package, origin=origin, base_world=base_a, provider_id="flight"
    )
    plan_b, _ = materialize_logistics_world(
        package=package, origin=origin, base_world=base_b, provider_id="flight"
    )
    assert plan_a.base_world_sha256 != plan_b.base_world_sha256
    assert plan_a.world_sha256 != plan_b.world_sha256


def test_scene_origin_digest_is_canonical_and_sensitive() -> None:
    assert scene_origin_digest(_origin()) == scene_origin_digest(_origin())
    assert isinstance(scene_origin_digest(_origin()), str)
    assert len(scene_origin_digest(_origin())) == 64
    # A different resolved WGS84 origin must produce a different scene identity.
    assert scene_origin_digest(
        _origin(latitude_deg=39.917)
    ) != scene_origin_digest(_origin())
    # Altitude is part of the frame authority, not just lat/lon.
    assert scene_origin_digest(
        _origin(amsl_m=60.0, ellipsoid_height_m=60.0)
    ) != scene_origin_digest(_origin())


def test_apply_materialization_updates_immutable_plan(tmp_path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    bundle_root, run = materialize_logistics_suite(
        tmp_path / "bundle", origin=origin, base_world=base
    )
    package = logistics_package_for_run(
        reader=BundleReader(bundle_root), run=run
    )
    plan = base_execution_plan(run, bundle_root=str(bundle_root))

    applied = apply_logistics_world_materialization(
        plan, package=package, origin=origin, base_world=base
    )

    # The input plan (and its ResolvedRun) is never mutated.
    assert applied is not plan
    assert plan.logistics_world is None
    original_flight = next(
        workload
        for workload in plan.runtime_workloads
        if workload.workload_id == PX4_PROVIDER_ID
    )
    assert original_flight.derived_inputs == ()
    assert applied.run == plan.run

    # The new plan carries the formal logistics world identity.
    assert applied.logistics_world is not None
    assert applied.logistics_world.schema_version == "aero-bench.logistics-world-plan/v1"
    assert applied.logistics_world.provider_id == PX4_PROVIDER_ID
    assert applied.logistics_world.facility_pad_count == 9

    # Only the px4.gazebo Provider workload gains the derived world input; the
    # other workloads are untouched.
    flight = next(
        workload
        for workload in applied.runtime_workloads
        if workload.workload_id == PX4_PROVIDER_ID
    )
    assert len(flight.derived_inputs) == 1
    assert flight.derived_inputs[0].destination == DEFAULT_LOGISTICS_WORLD_DESTINATION
    assert flight.derived_inputs[0].sha256 == applied.logistics_world.world_sha256
    for workload in applied.runtime_workloads:
        if workload.workload_id != PX4_PROVIDER_ID:
            assert workload.derived_inputs == ()


def test_px4_provider_id_selects_the_single_actual_adapter() -> None:
    run = logistics_resolved_run()
    assert px4_gazebo_provider_id(run) == PX4_PROVIDER_ID


def test_rejects_missing_px4_gazebo_provider() -> None:
    run = logistics_resolved_run(flight_adapter="flight.gazebo")
    with pytest.raises(LogisticsWorldMaterializationError, match="px4.gazebo"):
        px4_gazebo_provider_id(run)


def test_rejects_multiple_px4_gazebo_providers() -> None:
    run = logistics_resolved_run(extra_px4_provider_id="flight-2")
    with pytest.raises(LogisticsWorldMaterializationError, match="exactly one"):
        px4_gazebo_provider_id(run)


def test_rejects_non_logistics_package_object() -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    with pytest.raises(LogisticsWorldMaterializationError, match="lowered LogisticsTaskPackage"):
        materialize_logistics_world(
            package=object(),
            origin=origin,
            base_world=base,
            provider_id="flight",
        )


def test_rejects_package_id_mismatch_at_seam_entry(tmp_path) -> None:
    # A run whose declared TaskSpec package is not the logistics package is
    # rejected explicitly by the seam's package loader, never materialized.
    from tests.support import build_bundle, resolve_bundle

    bundle = build_bundle(tmp_path)
    inspection_run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    from aero_bench.executor.facility_world import logistics_package_for_run

    assert inspection_run.task.package.package_id != LOGISTICS_PACKAGE_ID
    with pytest.raises(LogisticsWorldMaterializationError, match="logistics TaskSpec"):
        logistics_package_for_run(reader=None, run=inspection_run)  # type: ignore[arg-type]


def test_rejects_origin_wgs84_mismatch() -> None:
    package = _multi_pad_package()
    base = base_world_sdf_bytes(origin=_origin())
    with pytest.raises(LogisticsWorldMaterializationError, match="WGS84"):
        materialize_logistics_world(
            package=package,
            origin=_origin(latitude_deg=31.2304),
            base_world=base,
            provider_id="flight",
        )


def test_rejects_origin_altitude_mismatch() -> None:
    package = _multi_pad_package()
    base = base_world_sdf_bytes(origin=_origin())
    with pytest.raises(LogisticsWorldMaterializationError, match="altitude"):
        materialize_logistics_world(
            package=package,
            origin=_origin(amsl_m=101.0, ellipsoid_height_m=101.0),
            base_world=base,
            provider_id="flight",
        )


def test_rejects_absolute_or_parent_traversal_destination() -> None:
    package = _multi_pad_package()
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    for bad in ("/etc/passwd.sdf", "bundle/../escape.sdf", "bundle\\\\win.sdf"):
        with pytest.raises(LogisticsWorldMaterializationError, match="normalized relative"):
            materialize_logistics_world(
                package=package,
                origin=origin,
                base_world=base,
                provider_id="flight",
                destination=bad,
            )


def test_rejects_invalid_provider_identifier() -> None:
    package = _multi_pad_package()
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    with pytest.raises(LogisticsWorldMaterializationError, match="Provider identifier"):
        materialize_logistics_world(
            package=package,
            origin=origin,
            base_world=base,
            provider_id="flight!/payload",
        )


def test_rejects_unsupported_base_world_shapes() -> None:
    package = _multi_pad_package()
    origin = _origin()
    with pytest.raises(LogisticsWorldMaterializationError, match="SDF bytes"):
        materialize_logistics_world(
            package=package,
            origin=origin,
            base_world=12345,  # type: ignore[arg-type]
            provider_id="flight",
        )
    with pytest.raises(LogisticsWorldMaterializationError, match="cannot read"):
        materialize_logistics_world(
            package=package,
            origin=origin,
            base_world=Path("/does/not/exist/scene.sdf"),
            provider_id="flight",
        )


def test_rejects_plan_without_provider_workload_for_px4_id() -> None:
    package = _multi_pad_package()
    origin = _origin()
    run = logistics_resolved_run()
    plan = base_execution_plan(run, bundle_root="/tmp")
    provider = next(
        workload
        for workload in plan.runtime_workloads
        if workload.workload_id == PX4_PROVIDER_ID
    )
    # Drop the px4.gazebo Provider workload from the plan surface.
    stripped = plan.model_copy(
        update={
            "runtime_workloads": tuple(
                workload
                for workload in plan.runtime_workloads
                if workload.workload_id != PX4_PROVIDER_ID
            ),
            # keep the Provider in the environment so the seam targets its id
        }
    )
    with pytest.raises(LogisticsWorldMaterializationError, match="not in the execution plan"):
        apply_logistics_world_materialization(
            stripped,
            package=package,
            origin=origin,
            base_world=base_world_sdf_bytes(origin=origin),
        )
    assert provider.derived_inputs == ()


def test_non_logistics_run_keeps_plan_without_logistics_world(tmp_path) -> None:
    from tests.support import build_bundle, resolve_bundle
    from aero_bench.tasks.registry import builtin_task_package_resolvers

    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]

    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=bundle.root,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert plan.logistics_world is None
    assert all(workload.derived_inputs == () for workload in plan.runtime_workloads)


def test_logistics_plan_rejects_through_formal_path_when_no_resolver(tmp_path) -> None:
    # The logistics ResolvedRun cannot yet be produced by the suite resolver
    # (scene threading + runtime barrier); feeding the strict resolved identity
    # to the current formal materializer must fail explicitly, never silently
    # produce a plan that claims a runnable logistics world.
    run = logistics_resolved_run()
    with pytest.raises(Exception):
        build_execution_plan(
            run,
            executor_kind="docker_reference",
            bundle_root=str(tmp_path),
            task_package_resolvers=(),
            provider_registry=logistics_fixture_provider_registry(),
        )
