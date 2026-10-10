"""Authoring-side derivation/pinning of the logistics px4.gazebo world_input.

The executor world seam (``aero_bench.executor.facility_world``) requires a
logistics ``px4.gazebo`` provider config to declare ``world_input`` (path +
SHA-256) and ``world_name`` matching the deterministic facility-spliced SDF.
Authoring that declaration by hand makes map/facility changes cumbersome and
blocks an automated map-change pipeline.

``pin_px4_gazebo_world_input`` is the bounded, fail-closed bridge that derives
the spliced world from the current contracts and pins the correct declaration
*before* the suite is resolved, so the map/facility/world identity enters the
ResolvedRun Run ID.  These tests drive the real authoring path
(author unpinned -> pin -> re-pin env ref -> resolve) and assert:

  * an unpinned run resolves but its executor materialization fails closed;
  * a pinned run's Run ID carries the world identity and materializes;
  * identical inputs reproduce the same config bytes and the same Run ID;
  * a facility change changes the pinned world digest and the Run ID;
  * stale/mismatching declarations and non-mapping input are rejected;
  * re-pinning an already-correct config is byte-identical (idempotent).
"""

from __future__ import annotations

from tests.executor.logistics_package_fixture import logistics_fixture_provider_registry

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import (
    EnvironmentSpec,
    FileRef,
    SchemaBoundFile,
    TaskSpec,
)
from aero_bench.config.resolver import resolve_suite
from aero_bench.executor.facility_world import (
    LogisticsWorldMaterializationError,
    materialize_logistics_world,
    pin_px4_gazebo_world_input,
)
from aero_bench.executor.planning import build_execution_plan
from aero_bench.tasks.logistics.integration import (
    LogisticsTaskPackageResolver,
    load_logistics_package,
)
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.scene_compiler import SceneOrigin
from tests.executor.logistics_package_fixture import (
    author_logistics_bundle,
    base_world_sdf_bytes,
)

_WORLD_INPUT_SCHEMA_VERSION = "aero-bench.px4-gazebo/bundle-world/v1"


def _origin() -> SceneOrigin:
    return SceneOrigin(
        latitude_deg=39.916,
        longitude_deg=116.397,
        amsl_m=43.0,
        ellipsoid_height_m=43.0,
        geoid_undulation_m=0.0,
    )


def _resolve(suite_path: Path):
    runs = resolve_suite(
        str(suite_path),
        executor_kind="docker_reference",
        task_package_resolvers=(LogisticsTaskPackageResolver(),),
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert len(runs) == 1, f"expected one run, found {len(runs)}"
    return runs[0]


def _px4_provider(environment: EnvironmentSpec):
    return next(
        provider
        for provider in environment.providers
        if provider.adapter == "px4.gazebo"
    )


def _staged_world_name(world_bytes: bytes) -> str:
    root = ET.fromstring(world_bytes)
    worlds = [
        element for element in root if element.tag.rsplit("}", 1)[-1] == "world"
    ]
    assert len(worlds) == 1
    return worlds[0].attrib["name"]


def _authored_inputs(root: Path, suite_path: Path):
    loaded = load_suite(suite_path)
    case = loaded.suite.cases[0]
    reader = BundleReader(loaded.root)
    environment = reader.load_yaml(case.environment, EnvironmentSpec)
    task = reader.load_yaml(case.task, TaskSpec)
    package = load_logistics_package(reader=reader, task=task)
    provider = _px4_provider(environment)
    document = json.loads(
        (loaded.root / provider.config.file.path).read_text(encoding="utf-8")
    )
    return loaded, case, environment, package, provider, document


def _pin_bundle(root: Path, suite_path: Path, *, origin, base_world) -> bytes:
    """Pin the authored (unpinned) px4.gazebo config and rewrite env+suite refs.

    This mirrors what an automated map-change pipeline does before the suite is
    resolved: derive the correct world declaration, stage the pinned config
    bytes, and re-pin the environment ``FileRef`` so the identity enters the Run
    ID.  The suite document is rewritten only because its case references the
    environment file whose digest changed.
    """
    loaded, case, environment, package, provider, document = _authored_inputs(
        root, suite_path
    )
    bundle_root = loaded.root

    pinned, pinned_bytes = pin_px4_gazebo_world_input(
        provider_config=document,
        package=package,
        origin=origin,
        base_world=base_world,
    )
    assert pinned.model_dump(mode="json") == json.loads(pinned_bytes.decode("utf-8"))

    config_path = bundle_root / provider.config.file.path
    config_path.write_bytes(pinned_bytes)
    new_config_ref = FileRef(
        path=provider.config.file.path,
        sha256=hashlib.sha256(pinned_bytes).hexdigest(),
    )
    new_environment = environment.model_copy(
        update={
            "providers": tuple(
                item.model_copy(
                    update={
                        "config": SchemaBoundFile(
                            file=new_config_ref,
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
    environment_path = bundle_root / case.environment.path
    environment_bytes = yaml.safe_dump(
        new_environment.model_dump(mode="json"), sort_keys=False
    ).encode("utf-8")
    environment_path.write_bytes(environment_bytes)
    new_environment_ref = FileRef(
        path=case.environment.path,
        sha256=hashlib.sha256(environment_bytes).hexdigest(),
    )

    suite_document = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    suite_document["cases"][0]["environment"] = new_environment_ref.model_dump(
        mode="json"
    )
    suite_path.write_text(
        yaml.safe_dump(suite_document, sort_keys=False), encoding="utf-8"
    )
    return pinned_bytes


def _px4_provider_config_ref(root: Path, suite_path: Path) -> FileRef:
    _, _, _, _, provider, _ = _authored_inputs(root, suite_path)
    return provider.config.file


def test_unpinned_run_materialization_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    suite_path = author_logistics_bundle(
        tmp_path / "unpinned", origin=origin, base_world=base, pin_world_input=False
    )
    run = _resolve(suite_path)
    provider = _px4_provider(run.environment)
    document = json.loads(
        (suite_path.parent / provider.config.file.path).read_text(encoding="utf-8")
    )
    assert document["world_input"] is None
    assert document["world_name"] is None

    with pytest.raises(
        LogisticsWorldMaterializationError, match="must declare world_input"
    ):
        build_execution_plan(
            run,
            executor_kind="docker_reference",
            bundle_root=suite_path.parent,
            task_package_resolvers=builtin_task_package_resolvers(),
            resolved_scene_origin=origin,
            base_world_sdf=base,
            provider_registry=logistics_fixture_provider_registry(),
        )


def test_pinning_binds_world_identity_into_run_id(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    root = tmp_path / "bundle"
    suite_path = author_logistics_bundle(
        root, origin=origin, base_world=base, pin_world_input=False
    )
    unpinned_run = _resolve(suite_path)
    assert _px4_provider(unpinned_run.environment).config.file.sha256 == (
        _px4_provider_config_ref(root, suite_path).sha256
    )

    pinned_bytes = _pin_bundle(root, suite_path, origin=origin, base_world=base)
    pinned_run = _resolve(suite_path)

    provider = _px4_provider(pinned_run.environment)
    bundle_root = suite_path.parent
    derived_provider = _px4_provider(unpinned_run.environment).provider_id
    package = load_logistics_package(
        reader=BundleReader(bundle_root), task=pinned_run.task
    )
    world_plan, derived = materialize_logistics_world(
        package=package,
        origin=origin,
        base_world=base,
        provider_id=derived_provider,
    )
    document = json.loads(
        (bundle_root / provider.config.file.path).read_text(encoding="utf-8")
    )
    assert document["world_input"] == {
        "schema_version": _WORLD_INPUT_SCHEMA_VERSION,
        "source": "bundle",
        "path": derived.destination,
        "sha256": world_plan.world_sha256,
    }
    assert document["world_name"] == _staged_world_name(
        derived.content_utf8.encode("utf-8")
    )

    # The world identity now enters the Run ID: the pinned run differs from the
    # unpinned one, and its provider config FileRef carries the pinned digest.
    assert pinned_run.run_id != unpinned_run.run_id
    assert provider.config.file.sha256 == hashlib.sha256(pinned_bytes).hexdigest()

    # And the executor materialization seam now accepts the pinned run.
    plan = build_execution_plan(
        pinned_run,
        executor_kind="docker_reference",
        bundle_root=bundle_root,
        task_package_resolvers=builtin_task_package_resolvers(),
        resolved_scene_origin=origin,
        base_world_sdf=base,
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert plan.logistics_world is not None
    assert plan.logistics_world.world_sha256 == world_plan.world_sha256


def test_identical_inputs_reproduce_config_and_run_id(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    config_digests: list[str] = []
    run_ids: list[str] = []
    for name in ("first", "second"):
        root = tmp_path / name
        suite_path = author_logistics_bundle(
            root, origin=origin, base_world=base, pin_world_input=False
        )
        pinned_bytes = _pin_bundle(root, suite_path, origin=origin, base_world=base)
        config_digests.append(hashlib.sha256(pinned_bytes).hexdigest())
        run_ids.append(_resolve(suite_path).run_id)
    assert config_digests[0] == config_digests[1]
    assert run_ids[0] == run_ids[1]


def test_facility_change_changes_world_digest_and_run_id(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    config_digests: list[str] = []
    run_ids: list[str] = []
    for name, charge_slots in (("two", 2), ("three", 3)):
        root = tmp_path / name
        suite_path = author_logistics_bundle(
            root,
            origin=origin,
            base_world=base,
            pin_world_input=False,
            charge_slots=charge_slots,
        )
        pinned_bytes = _pin_bundle(root, suite_path, origin=origin, base_world=base)
        config_digests.append(hashlib.sha256(pinned_bytes).hexdigest())
        run_ids.append(_resolve(suite_path).run_id)
    assert config_digests[0] != config_digests[1]
    assert run_ids[0] != run_ids[1]


def test_pin_is_idempotent(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    root = tmp_path / "bundle"
    suite_path = author_logistics_bundle(
        root, origin=origin, base_world=base, pin_world_input=False
    )
    _, _, _, package, provider, document = _authored_inputs(root, suite_path)
    _, first = pin_px4_gazebo_world_input(
        provider_config=document,
        package=package,
        origin=origin,
        base_world=base,
    )
    _, second = pin_px4_gazebo_world_input(
        provider_config=json.loads(first.decode("utf-8")),
        package=package,
        origin=origin,
        base_world=base,
    )
    assert first == second


def test_pin_rejects_non_mapping(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    root = tmp_path / "bundle"
    suite_path = author_logistics_bundle(
        root, origin=origin, base_world=base, pin_world_input=False
    )
    _, _, _, package, _, _ = _authored_inputs(root, suite_path)
    with pytest.raises(LogisticsWorldMaterializationError, match="must be a mapping"):
        pin_px4_gazebo_world_input(
            provider_config=["not", "a", "mapping"],
            package=package,
            origin=origin,
            base_world=base,
        )


def test_pin_rejects_stale_world_name(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    root = tmp_path / "bundle"
    suite_path = author_logistics_bundle(
        root, origin=origin, base_world=base, pin_world_input=False
    )
    _, _, _, package, _, document = _authored_inputs(root, suite_path)
    document["world_name"] = "some_other_world"
    with pytest.raises(LogisticsWorldMaterializationError, match="world_name does not match"):
        pin_px4_gazebo_world_input(
            provider_config=document,
            package=package,
            origin=origin,
            base_world=base,
        )


def test_pin_rejects_stale_world_input(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    root = tmp_path / "bundle"
    suite_path = author_logistics_bundle(
        root, origin=origin, base_world=base, pin_world_input=False
    )
    _, _, _, package, provider, document = _authored_inputs(root, suite_path)
    world_plan, derived = materialize_logistics_world(
        package=package,
        origin=origin,
        base_world=base,
        provider_id=provider.provider_id,
    )
    document["world_name"] = _staged_world_name(
        derived.content_utf8.encode("utf-8")
    )
    document["world_input"] = {
        "schema_version": _WORLD_INPUT_SCHEMA_VERSION,
        "source": "bundle",
        "path": derived.destination,
        "sha256": "f" * 64,
    }
    with pytest.raises(
        LogisticsWorldMaterializationError,
        match="refusing to overwrite a stale declaration",
    ):
        pin_px4_gazebo_world_input(
            provider_config=document,
            package=package,
            origin=origin,
            base_world=base,
        )
