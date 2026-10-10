"""Focused compiler tests for strict resolved world scenarios."""

from __future__ import annotations

from tests.world.support import provider_stages

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world import frame_math
from aero_bench.world.contracts import (
    ArtifactSelector,
    AssetRecord,
    LaunchSiteSpec,
    ParentRelativePose,
    PrecisionMetadata,
    RegionGeometry,
    RegionSpec,
    WorldPackage,
    WorldPose,
    world_package,
)
from aero_bench.world.resolved import (
    ResolvedFlightObservationProjection,
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
)
from tests.world.support import content_kwargs, scenario_task_inputs


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes(root: Path, relative_path: str, data: bytes) -> ArtifactSelector:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ArtifactSelector(
        artifact_id="unused.placeholder",
        selector=relative_path,
        sha256=_sha256_bytes(data),
    )


def _scenario_digest_for_document(document: dict[str, object]) -> str:
    body = dict(document)
    body.pop("scenario_digest", None)
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _grid_document(
    *,
    east_axis_m: tuple[float, ...] = (-200.0, 0.0, 20.0, 500.0),
    north_axis_m: tuple[float, ...] = (-200.0, 0.0, 12.0, 500.0),
    values_m: tuple[tuple[float, ...], ...],
) -> dict[str, object]:
    return {
        "schema_version": "aero-bench.scalar-grid/v1",
        "frame_id": "ENU",
        "east_axis_m": east_axis_m,
        "north_axis_m": north_axis_m,
        "values_m": values_m,
    }


def _default_grids() -> tuple[dict[str, object], dict[str, object]]:
    geoid = _grid_document(
        values_m=(
            (30.0, 30.5, 31.5, 33.0),
            (30.2, 31.0, 32.0, 33.4),
            (30.4, 31.6, 33.0, 34.2),
            (31.0, 32.4, 34.0, 35.0),
        )
    )
    terrain = _grid_document(
        values_m=(
            (4.0, 4.6, 5.8, 7.0),
            (4.2, 5.0, 6.4, 7.4),
            (4.4, 5.8, 7.2, 8.2),
            (5.0, 6.6, 8.0, 9.0),
        )
    )
    return geoid, terrain


def _provider_capabilities() -> dict[str, tuple[str, ...]]:
    return {
        "flight": ("gazebo.extra", "gazebo.frames", "gazebo.physics"),
        "perception": ("camera.depth", "camera.rgb"),
        "radio": ("wifi.802.11ax", "wifi.monitor"),
        "traffic": ("sumo.frames", "sumo.traffic", "sumo.traffic.alt"),
    }


def _replace_asset(
    records: tuple[AssetRecord, ...],
    asset_id: str,
    *,
    update: Callable[[AssetRecord], AssetRecord],
) -> tuple[AssetRecord, ...]:
    return tuple(
        update(record) if record.artifact.artifact_id == asset_id else record
        for record in records
    )


def _world_file_ref(root: Path, package: WorldPackage) -> FileRef:
    payload = canonical_json_bytes(package.model_dump(mode="json")) + b"\n"
    relative_path = "world/package.json"
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return FileRef(path=relative_path, sha256=_sha256_bytes(payload))


def _rewrite_asset_file(
    kwargs: dict[str, Any],
    root: Path,
    asset_id: str,
    data: bytes,
) -> None:
    for record in kwargs["assets"]:
        if record.artifact.artifact_id != asset_id:
            continue
        path = root / record.artifact.selector
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            asset_id,
            update=lambda current: current.model_copy(
                update={
                    "artifact": current.artifact.model_copy(
                        update={"sha256": _sha256_bytes(data)}
                    ),
                    "byte_size": len(data),
                }
            ),
        )
        return
    raise AssertionError(f"missing asset {asset_id}")


def _materialize_bundle(
    tmp_path: Path,
    *,
    mutate_kwargs: Callable[[dict[str, Any], Path], None] | None = None,
    mutate_files_after_world_write: Callable[[Path], None] | None = None,
) -> tuple[BundleReader, FileRef]:
    root = tmp_path / "bundle"
    root.mkdir(parents=True)

    kwargs = dict(content_kwargs())
    geoid_grid, terrain_grid = _default_grids()
    license_bytes = b"CC-BY-4.0 fixture license\n"
    license_path = "licenses/cc-by-4.0.txt"
    license_path_obj = root / license_path
    license_path_obj.parent.mkdir(parents=True, exist_ok=True)
    license_path_obj.write_bytes(license_bytes)
    license_ref = ArtifactSelector(
        artifact_id="license.cc-by-4.0",
        selector=license_path,
        sha256=_sha256_bytes(license_bytes),
    )

    records: list[AssetRecord] = []
    for template in kwargs["assets"]:
        assert isinstance(template, AssetRecord)
        asset_id = template.artifact.artifact_id
        selector = template.artifact.selector
        media_type = template.media_type
        if asset_id == "geoid.cn":
            selector = "world/geoid/grid.json"
            media_type = "application/json"
            data = canonical_json_bytes(geoid_grid) + b"\n"
        elif asset_id == "terrain.heights":
            selector = "world/terrain/heights.json"
            media_type = "application/json"
            data = canonical_json_bytes(terrain_grid) + b"\n"
        else:
            data = f"{asset_id}:{selector}\n".encode("utf-8")
        asset_ref = _write_bytes(root, selector, data)
        records.append(
            template.model_copy(
                update={
                    "artifact": ArtifactSelector(
                        artifact_id=asset_id,
                        selector=selector,
                        sha256=asset_ref.sha256,
                    ),
                    "byte_size": len(data),
                    "media_type": media_type,
                    "license_file": license_ref,
                }
            )
        )
    kwargs["assets"] = tuple(records)

    if mutate_kwargs is not None:
        mutate_kwargs(kwargs, root)

    package = world_package(**kwargs)
    world_ref = _world_file_ref(root, package)
    if mutate_files_after_world_write is not None:
        mutate_files_after_world_write(root)
    return BundleReader(root), world_ref


def _compile(
    tmp_path: Path,
    *,
    launch_site_id: str = "launch.alpha",
    seed: int = 20260901,
    provider_capabilities: dict[str, tuple[str, ...]] | None = None,
    mutate_kwargs: Callable[[dict[str, Any], Path], None] | None = None,
    mutate_files_after_world_write: Callable[[Path], None] | None = None,
) -> ResolvedScenario:
    reader, world_ref = _materialize_bundle(
        tmp_path,
        mutate_kwargs=mutate_kwargs,
        mutate_files_after_world_write=mutate_files_after_world_write,
    )
    task, agents, task_projection = scenario_task_inputs()
    return compile_resolved_scenario(
        reader=reader,
        world_package=world_ref,
        launch_site_id=launch_site_id,
        seed=seed,
        provider_capabilities=provider_capabilities or _provider_capabilities(),
        task=task,
        agents=agents,
        task_projection=task_projection,
               provider_stages=provider_stages((provider_capabilities or _provider_capabilities()).keys()),
    )


def _pose_transform(pose: WorldPose | ParentRelativePose) -> frame_math.RigidTransform:
    if isinstance(pose, WorldPose):
        translation = frame_math.Vector3(pose.east_m, pose.north_m, pose.up_m)
    else:
        translation = frame_math.Vector3(pose.x_m, pose.y_m, pose.z_m)
    quaternion = frame_math.UnitQuaternion(
        w=pose.qw,
        x=pose.qx,
        y=pose.qy,
        z=pose.qz,
    )
    return frame_math.RigidTransform(
        rotation=quaternion.to_rotation_matrix(),
        translation=translation,
    )


def _assert_coordinate_close(
    actual: Any,
    expected_enu: frame_math.Vector3,
    origin: frame_math.EnuTransform,
) -> None:
    expected_ned = frame_math.enu_to_ned(expected_enu)
    expected_ecef = origin.ecef_from_enu(expected_enu)
    expected_wgs84 = origin.enu_to_geodetic(expected_enu)
    assert actual.enu.east_m == pytest.approx(expected_enu.x, abs=1e-9)
    assert actual.enu.north_m == pytest.approx(expected_enu.y, abs=1e-9)
    assert actual.enu.up_m == pytest.approx(expected_enu.z, abs=1e-9)
    assert actual.ned.north_m == pytest.approx(expected_ned.x, abs=1e-9)
    assert actual.ned.east_m == pytest.approx(expected_ned.y, abs=1e-9)
    assert actual.ned.down_m == pytest.approx(expected_ned.z, abs=1e-9)
    assert actual.ecef.x_m == pytest.approx(expected_ecef.x, abs=1e-6)
    assert actual.ecef.y_m == pytest.approx(expected_ecef.y, abs=1e-6)
    assert actual.ecef.z_m == pytest.approx(expected_ecef.z, abs=1e-6)
    assert actual.wgs84.longitude_deg == pytest.approx(expected_wgs84[0], abs=1e-9)
    assert actual.wgs84.latitude_deg == pytest.approx(expected_wgs84[1], abs=1e-9)
    assert actual.wgs84.ellipsoid_height_m == pytest.approx(expected_wgs84[2], abs=1e-6)


def test_compile_is_deterministic_canonical_and_digest_bound(tmp_path: Path) -> None:
    def mutate_kwargs(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["assets"] = tuple(reversed(kwargs["assets"]))
        kwargs["entities"] = tuple(reversed(kwargs["entities"]))
        kwargs["weather"] = tuple(reversed(kwargs["weather"]))

    scenario = _compile(tmp_path, mutate_kwargs=mutate_kwargs)
    reparsed = ResolvedScenario.model_validate(scenario.model_dump(mode="json"))
    assert scenario == reparsed
    assert _compile(tmp_path / "repeat", mutate_kwargs=mutate_kwargs) == scenario
    assert tuple(provider.provider_id for provider in scenario.providers) == (
        "flight",
        "perception",
        "radio",
        "traffic",
    )
    entities = {entity.entity_id: entity for entity in scenario.entities}
    assert entities["static.pad"].owner_kind == "scenario"
    assert entities["static.pad"].owner_id == "scenario.compiler"
    assert entities["scene.building.a"].owner_kind == "scenario"
    assert entities["scene.building.a"].owner_id == "scenario.compiler"
    assert tuple(asset.asset_id for asset in scenario.assets) == tuple(
        sorted(asset.asset_id for asset in scenario.assets)
    )
    with pytest.raises(ValidationError):
        scenario.world_id = "mutated.world"  # type: ignore[misc]

    tampered = scenario.model_dump(mode="json")
    tampered["scenario_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="scenario_digest"):
        ResolvedScenario.model_validate(tampered)


def test_resolved_scenario_rejects_noncanonical_quaternion_payload_even_with_matching_digest(
    tmp_path: Path,
) -> None:
    scenario = _compile(tmp_path / "noncanonical-quaternion")
    tampered = scenario.model_dump(mode="json")
    orientation = tampered["launch_sites"][0]["pose"]["orientation_enu"]
    assert isinstance(orientation, dict)
    for key in ("qw", "qx", "qy", "qz"):
        orientation[key] = -orientation[key]
    tampered["scenario_digest"] = _scenario_digest_for_document(tampered)

    with pytest.raises(ValidationError, match="canonical sign representation"):
        ResolvedScenario.model_validate(tampered)


def test_asset_and_license_verification_fail_closed_and_unused_assets_are_rejected(
    tmp_path: Path,
) -> None:
    def mutate_geoid(root: Path) -> None:
        (root / "world/geoid/grid.json").write_bytes(b'{"tampered":true}\n')

    with pytest.raises(ValueError, match="sha256 mismatch for world/geoid/grid.json"):
        _compile(tmp_path / "asset-hash", mutate_files_after_world_write=mutate_geoid)

    def mutate_license(root: Path) -> None:
        (root / "licenses/cc-by-4.0.txt").write_bytes(b"tampered license\n")

    with pytest.raises(ValueError, match="sha256 mismatch for licenses/cc-by-4.0.txt"):
        _compile(
            tmp_path / "license-hash", mutate_files_after_world_write=mutate_license
        )

    def mutate_size(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            "sumo.cfg",
            update=lambda record: record.model_copy(
                update={"byte_size": record.byte_size + 7}
            ),
        )

    with pytest.raises(ValueError, match="asset sumo.cfg byte_size mismatch"):
        _compile(tmp_path / "asset-size", mutate_kwargs=mutate_size)

    def mutate_unused(kwargs: dict[str, Any], root: Path) -> None:
        data = b"unused\n"
        ref = _write_bytes(root, "world/unused/extra.bin", data)
        extra = kwargs["assets"][0].model_copy(
            update={
                "artifact": ArtifactSelector(
                    artifact_id="unused.asset",
                    selector=ref.selector,
                    sha256=ref.sha256,
                ),
                "asset_role": "other",
                "byte_size": len(data),
                "media_type": "application/octet-stream",
                "source_frame": "non_spatial",
            }
        )
        kwargs["assets"] = (*kwargs["assets"], extra)

    with pytest.raises(ValueError, match="unused source asset records"):
        _compile(tmp_path / "unused-asset", mutate_kwargs=mutate_unused)


def test_provider_capability_contracts_allow_supersets_but_require_all_required_ids(
    tmp_path: Path,
) -> None:
    scenario = _compile(tmp_path / "superset")
    flight = next(
        provider for provider in scenario.providers if provider.provider_id == "flight"
    )
    assert flight.capability_ids == ("gazebo.extra", "gazebo.frames", "gazebo.physics")

    missing_required = _provider_capabilities()
    missing_required["flight"] = ("gazebo.frames",)
    with pytest.raises(ValueError, match="missing \\['gazebo.physics'\\]"):
        _compile(
            tmp_path / "missing-capability",
            provider_capabilities=missing_required,
        )

    wrong_ids = dict(_provider_capabilities())
    wrong_ids["unknown"] = ("business.work-order",)
    with pytest.raises(ValueError, match="enabled provider unknown is not declared"):
        _compile(tmp_path / "wrong-provider-id", provider_capabilities=wrong_ids)

    malformed_str = dict(_provider_capabilities())
    malformed_str["perception"] = "camera.rgb"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="sequence of strings"):
        _compile(tmp_path / "malformed-str", provider_capabilities=malformed_str)

    malformed_bytes = dict(_provider_capabilities())
    malformed_bytes["perception"] = b"camera.rgb"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="sequence of strings"):
        _compile(tmp_path / "malformed-bytes", provider_capabilities=malformed_bytes)

    with pytest.raises(ValueError, match="seed must be an integer"):
        _compile(tmp_path / "bool-seed", seed=True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("seed_value", "match"),
    [
        ("1", "seed must be an integer"),
        (True, "seed must be an integer"),
        (-1, "seed must be in \\[0, 9223372036854775807\\]"),
        (9_223_372_036_854_775_808, "seed must be in \\[0, 9223372036854775807\\]"),
    ],
)
def test_resolved_scenario_model_parse_rejects_non_strict_or_out_of_range_seed(
    tmp_path: Path,
    seed_value: object,
    match: str,
) -> None:
    scenario = _compile(tmp_path / "seed-parse")
    tampered = scenario.model_dump(mode="json")
    tampered["seed"] = seed_value
    tampered["scenario_digest"] = _scenario_digest_for_document(tampered)

    with pytest.raises(ValidationError, match=match):
        ResolvedScenario.model_validate(tampered)


def test_scalar_grid_assets_require_json_precision_shape_order_and_coverage(
    tmp_path: Path,
) -> None:
    def mutate_precision(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            "geoid.cn",
            update=lambda record: record.model_copy(
                update={
                    "precision": (
                        PrecisionMetadata(
                            quantity="file_size",
                            kind="exact_bytes",
                            unit="B",
                            exact_bytes=1,
                            absolute_tolerance=None,
                        ),
                        PrecisionMetadata(
                            quantity="height",
                            kind="absolute_tolerance",
                            unit="m",
                            exact_bytes=None,
                            absolute_tolerance=0.06,
                        ),
                    )
                }
            ),
        )

    with pytest.raises(ValueError, match="precision must be <="):
        _compile(tmp_path / "precision", mutate_kwargs=mutate_precision)

    def mutate_media_type(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            "geoid.cn",
            update=lambda record: record.model_copy(
                update={"media_type": "image/tiff"}
            ),
        )

    with pytest.raises(ValueError, match="media_type application/json"):
        _compile(tmp_path / "media-type", mutate_kwargs=mutate_media_type)

    def mutate_fragment(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            "terrain.heights",
            update=lambda record: record.model_copy(
                update={
                    "artifact": record.artifact.model_copy(
                        update={
                            "selector": f"{record.artifact.selector}#fragment",
                        }
                    )
                }
            ),
        )

    with pytest.raises(ValueError, match="selector must not include a fragment"):
        _compile(tmp_path / "fragment", mutate_kwargs=mutate_fragment)

    def mutate_shape(kwargs: dict[str, Any], root: Path) -> None:
        bad_grid = _grid_document(
            values_m=(
                (1.0, 2.0),
                (3.0, 4.0),
                (5.0, 6.0),
                (7.0, 8.0),
            )
        )
        _rewrite_asset_file(
            kwargs,
            root,
            "geoid.cn",
            canonical_json_bytes(bad_grid) + b"\n",
        )

    with pytest.raises(ValueError, match="column count must equal east_axis_m"):
        _compile(tmp_path / "shape", mutate_kwargs=mutate_shape)

    def mutate_order(kwargs: dict[str, Any], root: Path) -> None:
        bad_grid = _grid_document(
            east_axis_m=(-200.0, 20.0, 0.0, 500.0),
            values_m=(
                (1.0, 2.0, 3.0, 4.0),
                (5.0, 6.0, 7.0, 8.0),
                (9.0, 10.0, 11.0, 12.0),
                (13.0, 14.0, 15.0, 16.0),
            ),
        )
        _rewrite_asset_file(
            kwargs,
            root,
            "geoid.cn",
            canonical_json_bytes(bad_grid) + b"\n",
        )

    with pytest.raises(ValueError, match="east_axis_m must be sorted"):
        _compile(tmp_path / "order", mutate_kwargs=mutate_order)

    def mutate_coverage(kwargs: dict[str, Any], root: Path) -> None:
        bad_grid = _grid_document(
            east_axis_m=(-50.0, 0.0, 20.0, 50.0),
            north_axis_m=(-50.0, 0.0, 12.0, 50.0),
            values_m=(
                (1.0, 2.0, 3.0, 4.0),
                (5.0, 6.0, 7.0, 8.0),
                (9.0, 10.0, 11.0, 12.0),
                (13.0, 14.0, 15.0, 16.0),
            ),
        )
        _rewrite_asset_file(
            kwargs,
            root,
            "geoid.cn",
            canonical_json_bytes(bad_grid) + b"\n",
        )

    with pytest.raises(ValueError, match="east-axis coverage must include"):
        _compile(tmp_path / "coverage", mutate_kwargs=mutate_coverage)


def test_resolved_coordinates_match_frame_math_and_round_trip_within_one_centimeter(
    tmp_path: Path,
) -> None:
    scenario = _compile(tmp_path / "frames")
    origin = frame_math.EnuTransform.from_origin(
        longitude_deg=scenario.frame_authority.origin.wgs84.longitude_deg,
        latitude_deg=scenario.frame_authority.origin.wgs84.latitude_deg,
        altitude_m=scenario.frame_authority.origin.wgs84.ellipsoid_height_m,
    )
    coordinates = [
        scenario.frame_authority.origin,
        scenario.entities[0].initial_pose.position,
        scenario.sensors[0].initial_pose.position,
        scenario.semantic_targets[0].pose.position,
    ]
    for coordinate in coordinates:
        expected_enu = frame_math.Vector3(
            coordinate.enu.east_m,
            coordinate.enu.north_m,
            coordinate.enu.up_m,
        )
        _assert_coordinate_close(coordinate, expected_enu, origin)
        lon, lat, alt = frame_math.ecef_to_geodetic(
            coordinate.ecef.x_m,
            coordinate.ecef.y_m,
            coordinate.ecef.z_m,
        )
        restored_enu = origin.geodetic_to_enu(
            longitude_deg=lon,
            latitude_deg=lat,
            altitude_m=alt,
        )
        assert math.dist(expected_enu.as_tuple(), restored_enu.as_tuple()) < 0.01


def test_resolved_vertical_relationships_and_per_vertex_height_solves_are_independent(
    tmp_path: Path,
) -> None:
    def mutate_kwargs(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["regions"] = (
            RegionSpec(
                region_id="region.keepout",
                kind="no_fly",
                geometry=RegionGeometry(
                    footprint_enu_m=kwargs["regions"][0].geometry.footprint_enu_m,
                    min_altitude_m=3.0,
                    max_altitude_m=8.0,
                    height_reference="agl",
                ),
                communications_shadow_attenuation_db=None,
            ),
        )

    scenario = _compile(tmp_path / "verticals", mutate_kwargs=mutate_kwargs)
    for coordinate in (
        scenario.frame_authority.origin,
        scenario.entities[0].initial_pose.position,
        scenario.sensors[0].initial_pose.position,
        scenario.semantic_targets[0].pose.position,
    ):
        assert coordinate.wgs84.ellipsoid_height_m == pytest.approx(
            coordinate.amsl_m + coordinate.geoid_separation_m,
            abs=1e-6,
        )
        assert coordinate.agl_m == pytest.approx(
            coordinate.amsl_m - coordinate.terrain_amsl_m,
            abs=1e-6,
        )

    building = scenario.buildings[0]
    assert {round(vertex.enu.up_m, 6) for vertex in building.base_vertices} != {
        round(building.base_vertices[0].enu.up_m, 6)
    }
    for vertex in building.base_vertices:
        assert vertex.amsl_m == pytest.approx(0.0, abs=1e-6)
    for vertex in building.top_vertices:
        assert vertex.amsl_m == pytest.approx(18.0, abs=1e-6)

    region = scenario.regions[0]
    assert {round(vertex.enu.up_m, 6) for vertex in region.lower_vertices} != {
        round(region.lower_vertices[0].enu.up_m, 6)
    }
    for vertex in region.lower_vertices:
        assert vertex.agl_m == pytest.approx(3.0, abs=1e-6)
    for vertex in region.upper_vertices:
        assert vertex.agl_m == pytest.approx(8.0, abs=1e-6)


def test_selected_launch_rebinds_launch_missions_and_only_overrides_the_selected_uav(
    tmp_path: Path,
) -> None:
    def mutate_kwargs(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["entities"] = (
            *kwargs["entities"],
            kwargs["entities"][0].model_copy(
                update={
                    "entity_id": "uav.bravo",
                    "pose": WorldPose(
                        frame_id="ENU",
                        east_m=120.0,
                        north_m=140.0,
                        up_m=20.0,
                        qw=1.0,
                        qx=0.0,
                        qy=0.0,
                        qz=0.0,
                    ),
                }
            ),
        )
        kwargs["launch_sites"] = (
            kwargs["launch_sites"][0],
            LaunchSiteSpec(
                launch_site_id="launch.bravo",
                primary_uav_entity_id="uav.bravo",
                allowed_uav_entity_ids=("uav.bravo",),
                pose=WorldPose(
                    frame_id="ENU",
                    east_m=80.0,
                    north_m=60.0,
                    up_m=5.0,
                    qw=1.0,
                    qx=0.0,
                    qy=0.0,
                    qz=0.0,
                ),
                pad_radius_m=3.0,
            ),
        )

    alpha = _compile(tmp_path / "launch-alpha", mutate_kwargs=mutate_kwargs)
    bravo = _compile(
        tmp_path / "launch-bravo",
        launch_site_id="launch.bravo",
        mutate_kwargs=mutate_kwargs,
    )
    alpha_entities = {entity.entity_id: entity for entity in alpha.entities}
    bravo_entities = {entity.entity_id: entity for entity in bravo.entities}
    assert alpha_entities["uav.alpha"].selected_launch_override is True
    assert alpha_entities["uav.bravo"].selected_launch_override is False
    assert bravo_entities["uav.alpha"].selected_launch_override is False
    assert bravo_entities["uav.bravo"].selected_launch_override is True
    assert alpha_entities[
        "uav.alpha"
    ].initial_pose.position.enu.east_m == pytest.approx(2.0)
    assert bravo_entities[
        "uav.bravo"
    ].initial_pose.position.enu.east_m == pytest.approx(80.0)
    assert alpha_entities["static.pad"].model_dump(mode="json") == bravo_entities[
        "static.pad"
    ].model_dump(mode="json")
    for scenario, selected_launch in ((alpha, "launch.alpha"), (bravo, "launch.bravo")):
        for requirement in scenario.mission_requirements:
            if requirement.kind in {"takeoff", "return_to_launch", "land"}:
                assert requirement.launch_site_id == selected_launch


def test_parent_relative_sensor_and_static_target_poses_compose_correctly(
    tmp_path: Path,
) -> None:
    yaw_90 = math.sqrt(0.5)

    def mutate_kwargs(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["entities"] = tuple(
            entity.model_copy(
                update={
                    "pose": WorldPose(
                        frame_id="ENU",
                        east_m=entity.pose.east_m,
                        north_m=entity.pose.north_m,
                        up_m=entity.pose.up_m,
                        qw=yaw_90,
                        qx=0.0,
                        qy=0.0,
                        qz=yaw_90,
                    )
                }
            )
            if entity.entity_id == "static.pad"
            else entity
            for entity in kwargs["entities"]
        )
        kwargs["launch_sites"] = (
            kwargs["launch_sites"][0].model_copy(
                update={
                    "pose": WorldPose(
                        frame_id="ENU",
                        east_m=kwargs["launch_sites"][0].pose.east_m,
                        north_m=kwargs["launch_sites"][0].pose.north_m,
                        up_m=kwargs["launch_sites"][0].pose.up_m,
                        qw=yaw_90,
                        qx=0.0,
                        qy=0.0,
                        qz=yaw_90,
                    )
                }
            ),
        )
        kwargs["sensors"] = (
            kwargs["sensors"][0].model_copy(
                update={
                    "pose": ParentRelativePose(
                        frame_id="parent",
                        x_m=1.0,
                        y_m=2.0,
                        z_m=3.0,
                        qw=yaw_90,
                        qx=0.0,
                        qy=0.0,
                        qz=yaw_90,
                    )
                }
            ),
        )
        kwargs["semantic_targets"] = (
            kwargs["semantic_targets"][0].model_copy(
                update={
                    "pose": ParentRelativePose(
                        frame_id="parent",
                        x_m=1.0,
                        y_m=0.0,
                        z_m=1.5,
                        qw=yaw_90,
                        qx=0.0,
                        qy=0.0,
                        qz=yaw_90,
                    )
                }
            ),
        )

    scenario = _compile(tmp_path / "composition", mutate_kwargs=mutate_kwargs)
    kwargs = content_kwargs()
    kwargs["entities"] = tuple(
        entity.model_copy(
            update={
                "pose": WorldPose(
                    frame_id="ENU",
                    east_m=entity.pose.east_m,
                    north_m=entity.pose.north_m,
                    up_m=entity.pose.up_m,
                    qw=yaw_90,
                    qx=0.0,
                    qy=0.0,
                    qz=yaw_90,
                )
            }
        )
        if entity.entity_id == "static.pad"
        else entity
        for entity in kwargs["entities"]
    )
    kwargs["launch_sites"] = (
        kwargs["launch_sites"][0].model_copy(
            update={
                "pose": WorldPose(
                    frame_id="ENU",
                    east_m=kwargs["launch_sites"][0].pose.east_m,
                    north_m=kwargs["launch_sites"][0].pose.north_m,
                    up_m=kwargs["launch_sites"][0].pose.up_m,
                    qw=yaw_90,
                    qx=0.0,
                    qy=0.0,
                    qz=yaw_90,
                )
            }
        ),
    )
    sensor_pose = ParentRelativePose(
        frame_id="parent",
        x_m=1.0,
        y_m=2.0,
        z_m=3.0,
        qw=yaw_90,
        qx=0.0,
        qy=0.0,
        qz=yaw_90,
    )
    target_pose = ParentRelativePose(
        frame_id="parent",
        x_m=1.0,
        y_m=0.0,
        z_m=1.5,
        qw=yaw_90,
        qx=0.0,
        qy=0.0,
        qz=yaw_90,
    )
    uav_launch = kwargs["launch_sites"][0]
    static_pad = next(
        entity for entity in kwargs["entities"] if entity.entity_id == "static.pad"
    )
    expected_sensor = _pose_transform(uav_launch.pose).compose(
        _pose_transform(sensor_pose)
    )
    expected_target = _pose_transform(static_pad.pose).compose(
        _pose_transform(target_pose)
    )
    origin = frame_math.EnuTransform.from_origin(
        longitude_deg=kwargs["frame"].origin.longitude_deg,
        latitude_deg=kwargs["frame"].origin.latitude_deg,
        altitude_m=kwargs["frame"].origin.altitude_m,
    )
    _assert_coordinate_close(
        scenario.sensors[0].initial_pose.position,
        expected_sensor.translation,
        origin,
    )
    _assert_coordinate_close(
        scenario.semantic_targets[0].pose.position,
        expected_target.translation,
        origin,
    )
    sensor_q = frame_math.UnitQuaternion.from_rotation_matrix(expected_sensor.rotation)
    target_q = frame_math.UnitQuaternion.from_rotation_matrix(expected_target.rotation)
    assert scenario.sensors[0].initial_pose.orientation_enu.qw == pytest.approx(
        sensor_q.w
    )
    assert scenario.sensors[0].initial_pose.orientation_enu.qz == pytest.approx(
        sensor_q.z
    )
    assert scenario.semantic_targets[0].pose.orientation_enu.qw == pytest.approx(
        target_q.w
    )
    assert scenario.semantic_targets[0].pose.orientation_enu.qz == pytest.approx(
        target_q.z
    )


def test_every_resolved_coordinate_stays_inside_spatial_extent_including_derived_poses(
    tmp_path: Path,
) -> None:
    scenario = _compile(tmp_path / "extent-ok")
    bounds = scenario.frame_authority.spatial_extent
    coordinates = [scenario.frame_authority.origin]
    coordinates.extend(entity.initial_pose.position for entity in scenario.entities)
    coordinates.extend(sensor.initial_pose.position for sensor in scenario.sensors)
    coordinates.extend(target.pose.position for target in scenario.semantic_targets)
    coordinates.extend(
        point for road in scenario.roads for point in road.terrain_points
    )
    coordinates.extend(
        point for building in scenario.buildings for point in building.base_vertices
    )
    coordinates.extend(
        point for building in scenario.buildings for point in building.top_vertices
    )
    coordinates.extend(
        point for region in scenario.regions for point in region.lower_vertices
    )
    coordinates.extend(
        point for region in scenario.regions for point in region.upper_vertices
    )
    for coordinate in coordinates:
        assert bounds.min_east_m <= coordinate.enu.east_m <= bounds.max_east_m
        assert bounds.min_north_m <= coordinate.enu.north_m <= bounds.max_north_m
        assert bounds.min_up_m <= coordinate.enu.up_m <= bounds.max_up_m

    def mutate_kwargs(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["sensors"] = (
            kwargs["sensors"][0].model_copy(
                update={
                    "pose": ParentRelativePose(
                        frame_id="parent",
                        x_m=0.0,
                        y_m=0.0,
                        z_m=301.0,
                        qw=1.0,
                        qx=0.0,
                        qy=0.0,
                        qz=0.0,
                    )
                }
            ),
        )

    with pytest.raises(
        ValueError, match="sensor camera.front initial_pose up_m lies outside"
    ):
        _compile(tmp_path / "extent-bad", mutate_kwargs=mutate_kwargs)


def test_flight_observation_projection_requires_stable_vehicle_scoped_id() -> None:
    projection = ResolvedFlightObservationProjection(
        projection_kind="flight",
        observation_id="flight.gnss.uav.inspector",
        observation_kind="gnss",
        vehicle_id="uav.inspector",
    )

    assert projection.observation_kind == "gnss"

    with pytest.raises(ValidationError, match="flight.<kind>.<vehicle_id>"):
        ResolvedFlightObservationProjection(
            projection_kind="flight",
            observation_id="flight.gnss.other",
            observation_kind="gnss",
            vehicle_id="uav.inspector",
        )


def test_flight_observation_ids_share_global_projection_namespace() -> None:
    flight = ResolvedFlightObservationProjection(
        projection_kind="flight",
        observation_id="flight.telemetry.uav.inspector",
        observation_kind="telemetry",
        vehicle_id="uav.inspector",
    )

    projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=(),
        logical_capability_ids=(),
        observations=(),
        flight_observations=(flight,),
    )

    assert projection.flight_observations == (flight,)
