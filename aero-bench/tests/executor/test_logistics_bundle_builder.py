"""Production logistics bundle builder driven by the px4.gazebo world pinning.

``aero_bench.tasks.logistics.bundle_builder.build_logistics_bundle`` is the
production caller of ``pin_px4_gazebo_world_input``.  These focused module-level
tests drive the real authoring path (authored source bundle + explicit compiled
base world + explicit facilities + explicit origin -> pinned bundle) and assert:

  * the produced bundle resolves and materializes through the real executor seam
    with the pinned world identity bound end to end;
  * identical inputs reproduce a byte-identical bundle and Run ID;
  * a base-world change changes the pinned identity and the Run ID;
  * a facilities change changes the pinned identity and the Run ID;
  * stale declarations and malformed/inconsistent inputs fail closed;
  * the source bundle is never mutated.
"""

from __future__ import annotations

from tests.executor.logistics_package_fixture import logistics_fixture_provider_registry

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import EnvironmentSpec, FileRef, SchemaBoundFile
from aero_bench.config.resolver import resolve_suite
from aero_bench.executor.facility_world import DEFAULT_LOGISTICS_WORLD_DESTINATION
from aero_bench.executor.planning import build_execution_plan
from aero_bench.tasks.logistics.bundle_builder import (
    LOGISTICS_WORLD_SOURCE_RELATIVE,
    LogisticsBundleAuthoringError,
    LogisticsBundleRequest,
    build_logistics_bundle,
)
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.scene_compiler import SceneOrigin
from tests.executor.logistics_package_fixture import (
    PX4_PROVIDER_ID,
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


def _base_world(path: Path, *, origin: SceneOrigin) -> bytes:
    payload = base_world_sdf_bytes(origin=origin)
    path.write_bytes(payload)
    return payload


def _base_world_variant(path: Path, *, origin: SceneOrigin) -> bytes:
    """A distinct, still-valid compiled base world (extra static map model)."""
    base = base_world_sdf_bytes(origin=origin).decode("utf-8")
    marker = (
        '    <model name="map_marker_alpha"><static>true</static>'
        '<link name="marker_link"/></model>\n'
    )
    payload = base.replace("  </world>", marker + "  </world>").encode("utf-8")
    assert payload != base.encode("utf-8")
    path.write_bytes(payload)
    return payload


def _source_bundle(root: Path, *, origin: SceneOrigin, base: bytes, **knobs) -> Path:
    return author_logistics_bundle(
        root, origin=origin, base_world=base, pin_world_input=False, **knobs
    )


def _build(source: Path, output: Path, *, origin: SceneOrigin, base_world: Path, **extra):
    return build_logistics_bundle(
        LogisticsBundleRequest(
            source_root=source,
            output_root=output,
            base_world_sdf=base_world,
            origin=origin,
            **extra,
        )
    )


def _resolve(result):
    runs = resolve_suite(
        str(result.suite_path),
        executor_kind="docker_reference",
        task_package_resolvers=(builtin_task_package_resolvers()),
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert len(runs) == 1, f"expected one run, found {len(runs)}"
    return runs[0]


def _source_package_document(source: Path) -> dict[str, object]:
    return json.loads(
        (source / "tasks/logistics-package.json").read_text(encoding="utf-8")
    )


def _tree_sha256(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _rewrite_px4_config(source: Path, mutate) -> None:
    """Mutate the source px4.gazebo config and re-pin its env/suite FileRefs.

    Keeps the source bundle internally consistent so the builder reaches the
    pinning bridge (a bare config edit without the ref update would instead be
    rejected by the loader's digest check).
    """
    suite_path = source / "suite.yaml"
    loaded = load_suite(str(suite_path))
    root = loaded.root
    case = loaded.suite.cases[0]
    environment = BundleReader(root).load_yaml(case.environment, EnvironmentSpec)
    provider = next(
        item for item in environment.providers if item.provider_id == PX4_PROVIDER_ID
    )
    config_path = root / provider.config.file.path
    document = json.loads(config_path.read_text(encoding="utf-8"))
    mutate(document)
    config_bytes = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    config_path.write_bytes(config_bytes)
    new_config_ref = FileRef(
        path=provider.config.file.path, sha256=hashlib.sha256(config_bytes).hexdigest()
    )
    reworked = environment.model_copy(
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
                if item.provider_id == PX4_PROVIDER_ID
                else item
                for item in environment.providers
            )
        }
    )
    environment_bytes = yaml.safe_dump(
        reworked.model_dump(mode="json"), sort_keys=False
    ).encode("utf-8")
    (root / case.environment.path).write_bytes(environment_bytes)
    new_env_ref = FileRef(
        path=case.environment.path,
        sha256=hashlib.sha256(environment_bytes).hexdigest(),
    )
    suite_document = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    suite_document["cases"][0]["environment"] = new_env_ref.model_dump(mode="json")
    suite_path.write_text(
        yaml.safe_dump(suite_document, sort_keys=False), encoding="utf-8"
    )


def test_produced_bundle_resolves_and_materializes(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)

    result = _build(source, tmp_path / "bundle", origin=origin, base_world=base_world_path)

    # The staged facility-spliced world source is byte-real and matches the pin.
    world_source_bytes = result.world_source_path.read_bytes()
    assert result.world_source_path == result.bundle_root / LOGISTICS_WORLD_SOURCE_RELATIVE
    assert hashlib.sha256(world_source_bytes).hexdigest() == result.world_sha256
    assert b"launch_pad." in world_source_bytes

    document = json.loads(result.pinned_config_path.read_text(encoding="utf-8"))
    assert document["world_input"] == {
        "schema_version": _WORLD_INPUT_SCHEMA_VERSION,
        "source": "bundle",
        "path": DEFAULT_LOGISTICS_WORLD_DESTINATION,
        "sha256": result.world_sha256,
    }
    assert document["world_name"] == result.world_name

    run = _resolve(result)
    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=result.bundle_root,
        task_package_resolvers=builtin_task_package_resolvers(),
        resolved_scene_origin=origin,
        base_world_sdf=base,
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert plan.logistics_world is not None
    assert plan.logistics_world.world_sha256 == result.world_sha256
    assert plan.logistics_world.provider_id == PX4_PROVIDER_ID
    flight_workload = next(
        workload
        for workload in plan.runtime_workloads
        if workload.workload_id == PX4_PROVIDER_ID
    )
    assert flight_workload.derived_inputs[0].sha256 == result.world_sha256


def test_identical_inputs_reproduce_bundle_and_run_id(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)

    pinned: list[bytes] = []
    worlds: list[bytes] = []
    suites: list[bytes] = []
    run_ids: list[str] = []
    for name in ("first", "second"):
        source = tmp_path / f"source-{name}"
        _source_bundle(source, origin=origin, base=base)
        result = _build(
            source, tmp_path / f"bundle-{name}", origin=origin, base_world=base_world_path
        )
        pinned.append(result.pinned_config_path.read_bytes())
        worlds.append(result.world_source_path.read_bytes())
        suites.append(result.suite_path.read_bytes())
        run_ids.append(_resolve(result).run_id)

    assert pinned[0] == pinned[1]
    assert worlds[0] == worlds[1]
    assert suites[0] == suites[1]
    assert run_ids[0] == run_ids[1]


def test_base_world_change_changes_identity_and_run_id(tmp_path: Path) -> None:
    origin = _origin()
    base_a = tmp_path / "base-a.sdf"
    base_b = tmp_path / "base-b.sdf"
    payload_a = _base_world(base_a, origin=origin)
    payload_b = _base_world_variant(base_b, origin=origin)
    assert payload_a != payload_b

    world_digests: list[str] = []
    config_digests: list[str] = []
    run_ids: list[str] = []
    for name, base_path in (("a", base_a), ("b", base_b)):
        source = tmp_path / f"source-{name}"
        _source_bundle(source, origin=origin, base=payload_a)
        result = _build(
            source, tmp_path / f"bundle-{name}", origin=origin, base_world=base_path
        )
        world_digests.append(result.world_sha256)
        config_digests.append(result.pinned_config_sha256)
        run_ids.append(_resolve(result).run_id)

    assert world_digests[0] != world_digests[1]
    assert config_digests[0] != config_digests[1]
    assert run_ids[0] != run_ids[1]


def test_facility_change_changes_identity_and_run_id(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base, charge_slots=2)

    baseline = _build(source, tmp_path / "bundle-baseline", origin=origin, base_world=base_world_path)

    changed = _source_package_document(source)
    vertiport = next(
        facility
        for facility in changed["facilities"]
        if facility["id"] == "facility-2"
    )
    vertiport["position"]["x"] = 170

    modified = _build(
        source,
        tmp_path / "bundle-modified",
        origin=origin,
        base_world=base_world_path,
        package_document=changed,
    )

    assert modified.package_sha256 != baseline.package_sha256
    assert modified.world_sha256 != baseline.world_sha256
    assert modified.pinned_config_sha256 != baseline.pinned_config_sha256
    assert _resolve(modified).run_id != _resolve(baseline).run_id


def test_stale_world_input_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)

    def mutate(document: dict[str, object]) -> None:
        # A digest-pinned world_input must also declare a world_name to pass the
        # strict config model; the pinned world_input digest is stale, which the
        # bridge must reject rather than overwrite.
        document["world_name"] = "compiled_urban_scene"
        document["world_input"] = {
            "schema_version": _WORLD_INPUT_SCHEMA_VERSION,
            "source": "bundle",
            "path": DEFAULT_LOGISTICS_WORLD_DESTINATION,
            "sha256": "f" * 64,
        }

    _rewrite_px4_config(source, mutate)
    with pytest.raises(
        LogisticsBundleAuthoringError, match="refusing to overwrite a stale"
    ):
        _build(source, tmp_path / "bundle", origin=origin, base_world=base_world_path)


def test_stale_world_name_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)

    _rewrite_px4_config(source, lambda document: document.__setitem__("world_name", "other_world"))
    with pytest.raises(LogisticsBundleAuthoringError, match="world_name does not match"):
        _build(source, tmp_path / "bundle", origin=origin, base_world=base_world_path)


def test_missing_base_world_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)
    with pytest.raises(
        LogisticsBundleAuthoringError, match="compiled base world SDF does not exist"
    ):
        _build(
            source,
            tmp_path / "bundle",
            origin=origin,
            base_world=tmp_path / "absent.sdf",
        )


def test_origin_mismatch_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)
    wrong = SceneOrigin(
        latitude_deg=40.5,
        longitude_deg=117.1,
        amsl_m=43.0,
        ellipsoid_height_m=43.0,
        geoid_undulation_m=0.0,
    )
    with pytest.raises(LogisticsBundleAuthoringError, match="does not match"):
        _build(source, tmp_path / "bundle", origin=wrong, base_world=base_world_path)


def test_nonempty_output_root_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "existing.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(
        LogisticsBundleAuthoringError, match="must not already contain files"
    ):
        _build(source, output, origin=origin, base_world=base_world_path)


def test_source_bundle_is_not_mutated(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)
    before = {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }
    _build(source, tmp_path / "bundle", origin=origin, base_world=base_world_path)
    after = {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_scene_origin_ellipsoid_with_geoid_undulation_accepted(tmp_path: Path) -> None:
    """A legitimate scene whose AMSL height differs from the world frame by a
    nonzero geoid separation must be accepted.

    The world package frame origin altitude is a WGS84 *ellipsoid* height, so
    the origin's ``ellipsoid_height_m`` is the field that must match it -- not
    ``amsl_m``.  Pre-fix the AMSL/ellipsoid mix-up (``amsl_m=13.0`` against a
    43.0 m frame) falsely rejected this valid origin.  The compiled base world
    separately declares the AMSL elevation (13.0 m), which the derivation
    validates against ``origin.amsl_m``.
    """
    undulated = SceneOrigin(
        latitude_deg=39.916,
        longitude_deg=116.397,
        amsl_m=13.0,
        geoid_undulation_m=30.0,
        ellipsoid_height_m=43.0,
    )
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=undulated)
    source = tmp_path / "source"
    _source_bundle(source, origin=undulated, base=base)

    result = _build(
        source, tmp_path / "bundle", origin=undulated, base_world=base_world_path
    )

    assert hashlib.sha256(result.world_source_path.read_bytes()).hexdigest() == (
        result.world_sha256
    )
    assert _resolve(result).run_id


def test_scene_origin_frame_ellipsoid_mismatch_rejected(tmp_path: Path) -> None:
    """A world-frame ellipsoid height that disagrees with the origin's ellipsoid
    height must fail closed.

    The package scene altitude is bound to the origin's ellipsoid height (73.0),
    while the AMSL height equals the frame altitude (43.0).  The pre-fix
    comparison used AMSL and would have silently accepted this 30 m vertical
    disagreement between the origin and the world frame.
    """
    frame_origin = _origin()  # frame altitude 43.0 (WGS84 ellipsoid)
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=frame_origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=frame_origin, base=base)

    document = _source_package_document(source)
    scene = document["scene"]
    assert isinstance(scene, dict)
    scene["origin_altitude_m"] = 73.0

    mismatch = SceneOrigin(
        latitude_deg=39.916,
        longitude_deg=116.397,
        amsl_m=43.0,
        geoid_undulation_m=30.0,
        ellipsoid_height_m=73.0,
    )
    with pytest.raises(LogisticsBundleAuthoringError, match="frame origin"):
        _build(
            source,
            tmp_path / "bundle",
            origin=mismatch,
            base_world=base_world_path,
            package_document=document,
        )


def test_output_inside_source_fails_closed_and_preserves_source(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)
    before = _tree_sha256(source)

    nested = source / "bundle"
    with pytest.raises(LogisticsBundleAuthoringError, match="overlaps"):
        _build(source, nested, origin=origin, base_world=base_world_path)

    assert _tree_sha256(source) == before
    assert not nested.exists()
    assert not any(tmp_path.glob(".bundle.staging-*"))


def test_output_symlink_alias_into_source_fails_closed(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)

    inside = source / "inside"
    inside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(inside, target_is_directory=True)
    before = _tree_sha256(source)

    with pytest.raises(LogisticsBundleAuthoringError, match="overlaps"):
        _build(source, alias, origin=origin, base_world=base_world_path)

    assert _tree_sha256(source) == before


def test_empty_output_root_committed_atomically(tmp_path: Path) -> None:
    origin = _origin()
    base_world_path = tmp_path / "base.sdf"
    base = _base_world(base_world_path, origin=origin)
    source = tmp_path / "source"
    _source_bundle(source, origin=origin, base=base)

    output = tmp_path / "bundle"
    output.mkdir()  # a pre-existing empty output root is accepted
    result = _build(source, output, origin=origin, base_world=base_world_path)

    assert result.bundle_root == output.resolve()
    assert (output / "suite.yaml").is_file()
    assert not any(tmp_path.glob(".bundle.staging-*"))
