"""Focused builders for the strict `aero-bench.world/v2` contract tests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    ArtifactRequirement,
    AssetAudience as TaskAssetAudience,
    AssetRef,
    FileRef,
    GoalSpec,
    ImplementationIdentity,
    ObservationGrant,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
    TaskPackageRef,
    TaskSpec,
    ToolGrant,
    VerifierSpec,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.contracts import (
    ArtifactSelector,
    AssetAudience,
    AssetProvenance,
    AssetRecord,
    BuildingGeometry,
    BuildingSpec,
    EngineFrameBinding,
    EnuPoint,
    EntitySpec,
    ExpectedPublicAsset,
    LaunchSiteSpec,
    MissionRequirement,
    NetworkConfiguration,
    NetworkNodeBinding,
    ParentRelativePose,
    PrecisionMetadata,
    ProviderRequirement,
    PublicLayer,
    RegionGeometry,
    RegionSpec,
    RigidOffset,
    RoadSpec,
    SemanticTargetSpec,
    SensorSpec,
    SpatialBounds,
    SumoConfiguration,
    SumoEntityBinding,
    TargetSurfaceNormal,
    TargetGeometry,
    UnitDeclaration,
    VerticalDatumBinding,
    ViewerLayerSource,
    WeatherSample,
    WirelessRadioProfile,
    WindVector,
    WorldFrame,
    WorldPackage,
    WorldPose,
    world_package,
)
from aero_bench.world.frames import WGS84_FRAME_ID, LocalFrameOrigin
from aero_bench.world.resolved import (
    ResolvedInspectionObservationProjection,
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
)

DIGEST_LICENSE = "0f" * 32
DIGEST_GEOID = "01" * 32
DIGEST_TERRAIN_MODEL = "02" * 32
DIGEST_IMAGERY = "03" * 32
DIGEST_TERRAIN_LAYER = "04" * 32
DIGEST_BUILDINGS_LAYER = "05" * 32
DIGEST_BUILDING_SOURCE = "06" * 32
DIGEST_BUILDING_RENDER = "07" * 32
DIGEST_BUILDING_COLLISION = "08" * 32
DIGEST_UAV_MODEL = "09" * 32
DIGEST_SUMO_CFG = "0a" * 32
DIGEST_SUMO_NET = "0b" * 32
DIGEST_SUMO_ROUTES = "0c" * 32
DIGEST_SUMO_ADDITIONAL = "0d" * 32
DIGEST_UGV_MODEL = "10" * 32
DIGEST_PEDESTRIAN_MODEL = "11" * 32
DIGEST_PAD_MODEL = "12" * 32
DIGEST_GAZEBO_SCENE = "13" * 32


def provider_capabilities() -> dict[str, tuple[str, ...]]:
    return {
        "flight": ("gazebo.extra", "gazebo.frames", "gazebo.physics"),
        "perception": ("camera.depth", "camera.rgb"),
        "radio": ("wifi.802.11ax", "wifi.monitor"),
        "traffic": ("sumo.frames", "sumo.traffic", "sumo.traffic.alt"),
    }


def provider_stages(provider_ids) -> dict[str, str]:
    """Pure, explicit stage projection for the world compiler fixtures."""
    stages = {
        "flight": "motion",
        "traffic": "motion",
        "radio": "network",
        "perception": "business_environment",
        "observation.provider": "business_environment",
    }
    return {key: value for key, value in stages.items() if key in provider_ids}


_FIXTURE_DIGEST = "ab" * 32
_FIXTURE_SOURCE_REVISION = "12" * 20


def _fixture_file(path: str) -> FileRef:
    return FileRef(path=path, sha256=_FIXTURE_DIGEST)


def _fixture_runtime(component_id: str) -> RuntimeSpec:
    return RuntimeSpec(
        runtime=RuntimeImage(
            image=f"aero-bench/{component_id}@sha256:{_FIXTURE_DIGEST}",
            command=("python", "-m", component_id),
        ),
        resources=ResourceBudget(
            cpu_millicores=100,
            memory_mib=64,
            gpu_count=0,
        ),
        implementation=ImplementationIdentity(
            component_id=component_id,
            kind="mechanical_fixture",
            source_uri="https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
            source_revision=_FIXTURE_SOURCE_REVISION,
            version="fixture.1",
        ),
    )


def scenario_task_inputs() -> tuple[
    TaskSpec,
    tuple[AgentSpec, ...],
    ResolvedTaskScenarioProjection,
]:
    verifier_id = "fixture.verifier"
    task = TaskSpec(
        schema_version="aero-bench.task/v1",
        task_id="fixture.task",
        package=TaskPackageRef(
            package_id="fixture.package",
            config=SchemaBoundFile(
                file=_fixture_file("fixture/package.json"),
                schema_file=_fixture_file("fixture/package.schema.json"),
            ),
        ),
        instruction=_fixture_file("fixture/instruction.md"),
        required_capabilities=(),
        required_tools=(),
        assets=(),
        goals=(
            GoalSpec(
                goal_id="fixture.goal",
                verifier_id=verifier_id,
                metric_id="fixture.metric",
                operator="ge",
                threshold=1.0,
                evidence="authoritative_state",
                parameters=(),
            ),
        ),
        verifier=VerifierSpec(
            verifier_id=verifier_id,
            workload=_fixture_runtime(verifier_id),
            config=SchemaBoundFile(
                file=_fixture_file("fixture/verifier.json"),
                schema_file=_fixture_file("fixture/verifier.schema.json"),
            ),
            artifact_requirements=(),
            output_artifacts=(
                ArtifactRequirement(
                    artifact_id="fixture.verification",
                    artifact_type="verification.report",
                    producer_id=verifier_id,
                    visibility="public",
                    relative_path="verification/report.json",
                    max_size_bytes=4096,
                    source_asset_id=None,
                ),
            ),
        ),
    )
    agents = (
        AgentSpec(
            schema_version="aero-bench.agent/v2",
            agent_id="fixture.agent",
            workload=_fixture_runtime("fixture.agent"),
            tools=(),
            queries=(),
            observations=(),
            artifact_requirements=(),
        ),
    )
    return (
        task,
        agents,
        ResolvedTaskScenarioProjection(
            logical_endpoint_ids=(),
            logical_capability_ids=(),
            observations=(),
        ),
    )


def scalar_grid_document(
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


def default_scalar_grids() -> tuple[dict[str, object], dict[str, object]]:
    return (
        scalar_grid_document(
            values_m=(
                (30.0, 30.5, 31.5, 33.0),
                (30.2, 31.0, 32.0, 33.4),
                (30.4, 31.6, 33.0, 34.2),
                (31.0, 32.4, 34.0, 35.0),
            )
        ),
        scalar_grid_document(
            values_m=(
                (4.0, 4.6, 5.8, 7.0),
                (4.2, 5.0, 6.4, 7.4),
                (4.4, 5.8, 7.2, 8.2),
                (5.0, 6.6, 8.0, 9.0),
            )
        ),
    )


def frame_origin() -> LocalFrameOrigin:
    return LocalFrameOrigin(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=116.397,
        latitude_deg=39.916,
        altitude_m=43.0,
    )


def world_frame() -> WorldFrame:
    return WorldFrame(
        geodetic_frame_id="WGS84",
        ecef_frame_id="ECEF",
        enu_frame_id="ENU",
        ned_frame_id="NED",
        transform_chain="WGS84->ECEF->ENU->NED",
        datum="WGS84",
        semi_major_axis_m=6_378_137.0,
        inverse_flattening=298.257_223_563,
        ellipsoid_height_reference="wgs84_ellipsoid",
        amsl_height_reference="orthometric_msl",
        agl_height_reference="terrain_relative",
        origin=frame_origin(),
        origin_height_reference="wgs84_ellipsoid",
        spatial_extent=SpatialBounds(
            min_east_m=-200.0,
            max_east_m=500.0,
            min_north_m=-200.0,
            max_north_m=500.0,
            min_up_m=-50.0,
            max_up_m=300.0,
            vertical_reference="enu_up",
        ),
        vertical_datum=VerticalDatumBinding(
            geoid_correction_asset_id="geoid.cn",
            terrain_height_asset_id="terrain.heights",
            geoid_interpolation="bilinear",
            terrain_interpolation="bilinear",
            geoid_precision_m=0.05,
            terrain_precision_m=0.2,
        ),
    )


def license_file() -> ArtifactSelector:
    return ArtifactSelector(
        artifact_id="license.cc-by-4.0",
        selector="licenses/cc-by-4.0.txt",
        sha256=DIGEST_LICENSE,
    )


def file_size_unit() -> UnitDeclaration:
    return UnitDeclaration(quantity="file_size", unit="B")


def file_size_precision() -> PrecisionMetadata:
    return PrecisionMetadata(
        quantity="file_size",
        kind="exact_bytes",
        unit="B",
        exact_bytes=1,
        absolute_tolerance=None,
    )


def asset_record(
    *,
    asset_id: str,
    selector: str,
    sha256: str,
    asset_role: str,
    media_type: str,
    visibility: str,
    source_frame: str,
    byte_size: int,
    units: tuple[UnitDeclaration, ...] = (),
    precision: tuple[PrecisionMetadata, ...] = (),
    provider_audience_ids: tuple[str, ...] = ("flight",),
) -> AssetRecord:
    audiences = (
        (AssetAudience(audience_kind="public", audience_id="all"),)
        if visibility == "public"
        else tuple(
            AssetAudience(audience_kind="provider", audience_id=provider_id)
            for provider_id in provider_audience_ids
        )
    )
    return AssetRecord(
        artifact=ArtifactSelector(
            artifact_id=asset_id,
            selector=selector,
            sha256=sha256,
        ),
        asset_role=asset_role,
        byte_size=byte_size,
        media_type=media_type,
        visibility=visibility,
        audiences=audiences,
        units=(file_size_unit(), *units),
        source_frame=source_frame,
        precision=(file_size_precision(), *precision),
        provenance=AssetProvenance(
            source_kind="bundled_offline",
            recorded_by="world",
            source_dataset="fixture.bundle",
            source_version="2026.09.01",
            runtime_download=False,
        ),
        license_id="CC-BY-4.0",
        license_file=license_file(),
    )


def assets() -> tuple[AssetRecord, ...]:
    return (
        asset_record(
            asset_id="geoid.cn",
            selector="world/geoid/grid.bin",
            sha256=DIGEST_GEOID,
            asset_role="geoid_model",
            media_type="application/octet-stream",
            visibility="public",
            source_frame="raster_pixel",
            byte_size=4096,
            units=(UnitDeclaration(quantity="height", unit="m"),),
            precision=(
                PrecisionMetadata(
                    quantity="height",
                    kind="absolute_tolerance",
                    unit="m",
                    exact_bytes=None,
                    absolute_tolerance=0.05,
                ),
            ),
        ),
        asset_record(
            asset_id="terrain.heights",
            selector="world/terrain/heights.tif",
            sha256=DIGEST_TERRAIN_MODEL,
            asset_role="terrain_model",
            media_type="image/tiff",
            visibility="public",
            source_frame="raster_pixel",
            byte_size=8192,
            units=(UnitDeclaration(quantity="height", unit="m"),),
            precision=(
                PrecisionMetadata(
                    quantity="height",
                    kind="absolute_tolerance",
                    unit="m",
                    exact_bytes=None,
                    absolute_tolerance=0.2,
                ),
            ),
        ),
        asset_record(
            asset_id="imagery.base",
            selector="world/imagery/base.mbtiles",
            sha256=DIGEST_IMAGERY,
            asset_role="imagery_tiles",
            media_type="application/x-sqlite3",
            visibility="public",
            source_frame="raster_pixel",
            byte_size=16_384,
        ),
        asset_record(
            asset_id="terrain.base",
            selector="world/terrain/base.mbtiles",
            sha256=DIGEST_TERRAIN_LAYER,
            asset_role="terrain_tiles",
            media_type="application/x-sqlite3",
            visibility="public",
            source_frame="raster_pixel",
            byte_size=16_384,
        ),
        asset_record(
            asset_id="layer.buildings",
            selector="world/layers/buildings.geojson",
            sha256=DIGEST_BUILDINGS_LAYER,
            asset_role="layer_tiles",
            media_type="application/geo+json",
            visibility="public",
            source_frame="ENU",
            byte_size=2048,
        ),
        asset_record(
            asset_id="building.source",
            selector="world/buildings/block-a.ifc",
            sha256=DIGEST_BUILDING_SOURCE,
            asset_role="building_source",
            media_type="application/octet-stream",
            visibility="private",
            source_frame="asset_local",
            byte_size=10_240,
        ),
        asset_record(
            asset_id="building.render",
            selector="world/buildings/block-a.osm.json",
            sha256=DIGEST_BUILDING_RENDER,
            asset_role="building_render",
            media_type="application/json",
            visibility="public",
            source_frame="WGS84",
            byte_size=6144,
        ),
        asset_record(
            asset_id="building.collision",
            selector="world/buildings/block-a-collision.obj",
            sha256=DIGEST_BUILDING_COLLISION,
            asset_role="building_collision",
            media_type="model/obj",
            visibility="private",
            source_frame="asset_local",
            byte_size=3072,
        ),
        asset_record(
            asset_id="model.uav",
            selector="world/models/uav.json",
            sha256=DIGEST_UAV_MODEL,
            asset_role="entity_model",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            byte_size=5120,
        ),
        asset_record(
            asset_id="model.ugv",
            selector="world/models/ugv.json",
            sha256=DIGEST_UGV_MODEL,
            asset_role="entity_model",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            byte_size=4096,
        ),
        asset_record(
            asset_id="model.pedestrian",
            selector="world/models/pedestrian.json",
            sha256=DIGEST_PEDESTRIAN_MODEL,
            asset_role="entity_model",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            byte_size=3072,
        ),
        asset_record(
            asset_id="model.pad",
            selector="world/models/pad.json",
            sha256=DIGEST_PAD_MODEL,
            asset_role="entity_model",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            byte_size=2048,
        ),
        asset_record(
            asset_id="gazebo.scene",
            selector="world/gazebo/city.sdf",
            sha256=DIGEST_GAZEBO_SCENE,
            asset_role="other",
            media_type="application/vnd.gazebo.sdf+xml",
            visibility="private",
            source_frame="ENU",
            byte_size=4096,
            provider_audience_ids=("flight",),
        ),
        asset_record(
            asset_id="sumo.cfg",
            selector="world/sumo/sim.sumocfg",
            sha256=DIGEST_SUMO_CFG,
            asset_role="sumo_config",
            media_type="application/xml",
            visibility="private",
            source_frame="non_spatial",
            byte_size=1024,
            provider_audience_ids=("traffic",),
        ),
        asset_record(
            asset_id="sumo.net",
            selector="world/sumo/network.net.xml",
            sha256=DIGEST_SUMO_NET,
            asset_role="sumo_network",
            media_type="application/xml",
            visibility="private",
            source_frame="sumo_net",
            byte_size=4096,
            provider_audience_ids=("traffic",),
        ),
        asset_record(
            asset_id="sumo.routes",
            selector="world/sumo/routes.rou.xml",
            sha256=DIGEST_SUMO_ROUTES,
            asset_role="sumo_routes",
            media_type="application/xml",
            visibility="private",
            source_frame="non_spatial",
            byte_size=1024,
            provider_audience_ids=("traffic",),
        ),
        asset_record(
            asset_id="sumo.additional",
            selector="world/sumo/additional.add.xml",
            sha256=DIGEST_SUMO_ADDITIONAL,
            asset_role="sumo_additional",
            media_type="application/xml",
            visibility="private",
            source_frame="non_spatial",
            byte_size=1024,
            provider_audience_ids=("traffic",),
        ),
    )


def building_geometry() -> BuildingGeometry:
    return BuildingGeometry(
        footprint_enu_m=(
            EnuPoint(east_m=0.0, north_m=0.0),
            EnuPoint(east_m=20.0, north_m=0.0),
            EnuPoint(east_m=20.0, north_m=12.0),
            EnuPoint(east_m=0.0, north_m=12.0),
        ),
        base_altitude_m=0.0,
        top_altitude_m=18.0,
        height_reference="amsl",
    )


def building() -> BuildingSpec:
    return BuildingSpec(
        building_id="building.a",
        entity_id="scene.building.a",
        source_asset_id="building.source",
        render_asset_id="building.render",
        collision_asset_id="building.collision",
        geometry=building_geometry(),
    )


def uav_entity(*, entity_id: str = "uav.alpha", east_m: float = 5.0) -> EntitySpec:
    return EntitySpec(
        entity_id=entity_id,
        kind="uav",
        provider_id="flight",
        authority_kind="gazebo_physics",
        state="dynamic",
        model_asset_id="model.uav",
        pose=WorldPose(
            frame_id="ENU",
            east_m=east_m,
            north_m=10.0,
            up_m=12.0,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
    )


def ugv_entity() -> EntitySpec:
    return EntitySpec(
        entity_id="ugv.beta",
        kind="ugv",
        provider_id="traffic",
        authority_kind="sumo_traffic",
        state="dynamic",
        model_asset_id="model.ugv",
        pose=WorldPose(
            frame_id="ENU",
            east_m=60.0,
            north_m=5.0,
            up_m=0.0,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
    )


def pedestrian_entity() -> EntitySpec:
    return EntitySpec(
        entity_id="pedestrian.charlie",
        kind="pedestrian",
        provider_id="traffic",
        authority_kind="sumo_traffic",
        state="dynamic",
        model_asset_id="model.pedestrian",
        pose=WorldPose(
            frame_id="ENU",
            east_m=55.0,
            north_m=8.0,
            up_m=0.0,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
    )


def static_target_entity() -> EntitySpec:
    return EntitySpec(
        entity_id="static.pad",
        kind="static_asset",
        provider_id=None,
        authority_kind="scenario_static",
        state="static",
        model_asset_id="model.pad",
        pose=WorldPose(
            frame_id="ENU",
            east_m=25.0,
            north_m=20.0,
            up_m=0.0,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
    )


def building_entity() -> EntitySpec:
    return EntitySpec(
        entity_id="scene.building.a",
        kind="static_asset",
        provider_id=None,
        authority_kind="scenario_static",
        state="static",
        model_asset_id="building.render",
        pose=WorldPose(
            frame_id="ENU",
            east_m=10.0,
            north_m=6.0,
            up_m=0.0,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
    )


def sensor() -> SensorSpec:
    return SensorSpec(
        sensor_id="camera.front",
        provider_id="perception",
        parent_entity_id="uav.alpha",
        kind="camera",
        pose=ParentRelativePose(
            frame_id="parent",
            x_m=0.2,
            y_m=0.0,
            z_m=-0.1,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
        horizontal_fov_deg=80.0,
        vertical_fov_deg=60.0,
        resolution_width_px=1920,
        resolution_height_px=1080,
    )


def semantic_target() -> SemanticTargetSpec:
    return SemanticTargetSpec(
        target_id="target.pad-marker",
        parent_entity_id="static.pad",
        pose=ParentRelativePose(
            frame_id="parent",
            x_m=0.0,
            y_m=0.0,
            z_m=1.5,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        ),
        geometry=TargetGeometry(
            shape="box",
            size_x_m=0.6,
            size_y_m=0.6,
            size_z_m=0.2,
        ),
        surface_normal_target=TargetSurfaceNormal(x=-1.0, y=0.0, z=0.0),
        required_sensor_id="camera.front",
        min_distance_m=3.0,
        max_distance_m=25.0,
        max_view_angle_deg=12.0,
        fov_margin_deg=5.0,
        dwell_time_s=1.5,
        required_evidence=(
            "sensor_frame",
            "image",
            "bbox_2d",
            "report",
            "network_upload_or_buffer",
        ),
    )


def weather() -> WeatherSample:
    return WeatherSample(
        sample_id="weather.day",
        mode="deterministic_constant",
        wind=WindVector(east_mps=1.0, north_mps=-0.5, up_mps=0.0),
        visibility_m=8000.0,
        precipitation="none",
        precipitation_rate_mm_per_h=0.0,
        temperature_c=21.0,
        pressure_pa=101_325.0,
    )


def expected_public_asset() -> ExpectedPublicAsset:
    return ExpectedPublicAsset(
        asset_id="public.report",
        kind="mission_output",
        producer_requirement_id="mission.upload",
        media_type="application/json",
        units=(file_size_unit(),),
        precision=(file_size_precision(),),
    )


def content_kwargs() -> dict[str, object]:
    return {
        "world_id": "world.city-demo",
        "frame": world_frame(),
        "assets": assets(),
        "provider_requirements": (
            ProviderRequirement(
                provider_id="flight",
                roles=("motion",),
                required_capability_ids=("gazebo.frames", "gazebo.physics"),
            ),
            ProviderRequirement(
                provider_id="perception",
                roles=("sensor",),
                required_capability_ids=("camera.rgb",),
            ),
            ProviderRequirement(
                provider_id="radio",
                roles=("wireless_network",),
                required_capability_ids=("wifi.802.11ax",),
            ),
            ProviderRequirement(
                provider_id="traffic",
                roles=("traffic",),
                required_capability_ids=("sumo.frames", "sumo.traffic"),
            ),
        ),
        "base_layers": (
            ViewerLayerSource(
                layer_id="base.imagery",
                kind="imagery",
                asset_id="imagery.base",
                visibility="public",
                default_visible=True,
            ),
            ViewerLayerSource(
                layer_id="base.terrain",
                kind="terrain",
                asset_id="terrain.base",
                visibility="public",
                default_visible=True,
            ),
        ),
        "layers": (
            PublicLayer(
                layer_id="layer.buildings",
                kind="buildings",
                asset_id="layer.buildings",
                visibility="public",
                default_visible=True,
            ),
        ),
        "buildings": (building(),),
        "roads": (
            RoadSpec(
                road_id="road.main",
                kind="vehicle_lane",
                centerline_enu_m=(
                    EnuPoint(east_m=-20.0, north_m=5.0),
                    EnuPoint(east_m=50.0, north_m=5.0),
                ),
                width_m=3.5,
            ),
        ),
        "regions": (
            RegionSpec(
                region_id="region.keepout",
                kind="no_fly",
                geometry=RegionGeometry(
                    footprint_enu_m=(
                        EnuPoint(east_m=40.0, north_m=40.0),
                        EnuPoint(east_m=80.0, north_m=40.0),
                        EnuPoint(east_m=80.0, north_m=80.0),
                        EnuPoint(east_m=40.0, north_m=80.0),
                    ),
                    min_altitude_m=0.0,
                    max_altitude_m=120.0,
                    height_reference="amsl",
                ),
                communications_shadow_attenuation_db=None,
            ),
        ),
        "launch_sites": (
            LaunchSiteSpec(
                launch_site_id="launch.alpha",
                primary_uav_entity_id="uav.alpha",
                allowed_uav_entity_ids=("uav.alpha",),
                pose=WorldPose(
                    frame_id="ENU",
                    east_m=2.0,
                    north_m=8.0,
                    up_m=0.0,
                    qw=1.0,
                    qx=0.0,
                    qy=0.0,
                    qz=0.0,
                ),
                pad_radius_m=2.5,
            ),
        ),
        "entities": (
            uav_entity(),
            ugv_entity(),
            pedestrian_entity(),
            static_target_entity(),
            building_entity(),
        ),
        "sensors": (sensor(),),
        "semantic_targets": (semantic_target(),),
        "weather": (weather(),),
        "engine_frame_bindings": (
            EngineFrameBinding(
                binding_id="gazebo.enu-body",
                provider_id="flight",
                engine="gazebo",
                scene_asset_id="gazebo.scene",
                source_frame_id="ENU",
                target_frame_id="uav.alpha/body",
                transform=RigidOffset(
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.0,
                    qw=1.0,
                    qx=0.0,
                    qy=0.0,
                    qz=0.0,
                ),
            ),
            EngineFrameBinding(
                binding_id="sumo.net-enu",
                provider_id="traffic",
                engine="sumo",
                scene_asset_id=None,
                source_frame_id="sumo_net",
                target_frame_id="ENU",
                transform=RigidOffset(
                    x_m=0.0,
                    y_m=0.0,
                    z_m=0.0,
                    qw=1.0,
                    qx=0.0,
                    qy=0.0,
                    qz=0.0,
                ),
            ),
        ),
        "sumo": SumoConfiguration(
            provider_id="traffic",
            config_asset_id="sumo.cfg",
            network_asset_id="sumo.net",
            routes_asset_id="sumo.routes",
            additional_asset_id="sumo.additional",
            frame_binding_id="sumo.net-enu",
            object_bindings=(
                SumoEntityBinding(
                    sumo_object_id="veh.beta",
                    entity_id="ugv.beta",
                    kind="vehicle",
                ),
                SumoEntityBinding(
                    sumo_object_id="ped.charlie",
                    entity_id="pedestrian.charlie",
                    kind="person",
                ),
            ),
        ),
        "network": NetworkConfiguration(
            provider_id="radio",
            radio_profiles=(
                WirelessRadioProfile(
                    radio_profile_id="radio.wifi-main",
                    provider_id="radio",
                    wifi_standard="802.11ax",
                    frequency_ghz=5.8,
                    channel_width_mhz=80.0,
                    tx_power_dbm=20.0,
                    rx_sensitivity_dbm=-92.0,
                ),
            ),
            node_bindings=(
                NetworkNodeBinding(
                    node_id="node.uav.alpha",
                    entity_id="uav.alpha",
                    endpoint_id="endpoint.uav.alpha",
                    radio_profile_id="radio.wifi-main",
                ),
            ),
            links=(),
        ),
        "mission_requirements": (
            MissionRequirement(
                requirement_id="mission.takeoff",
                kind="takeoff",
                dependencies=(),
                launch_site_id="launch.alpha",
                target_id=None,
                expected_public_asset_id=None,
            ),
            MissionRequirement(
                requirement_id="mission.observe",
                kind="observation",
                dependencies=("mission.takeoff",),
                launch_site_id=None,
                target_id="target.pad-marker",
                expected_public_asset_id=None,
            ),
            MissionRequirement(
                requirement_id="mission.upload",
                kind="upload_or_buffer",
                dependencies=("mission.observe",),
                launch_site_id=None,
                target_id=None,
                expected_public_asset_id="public.report",
            ),
            MissionRequirement(
                requirement_id="mission.rtl",
                kind="return_to_launch",
                dependencies=("mission.upload",),
                launch_site_id="launch.alpha",
                target_id=None,
                expected_public_asset_id=None,
            ),
            MissionRequirement(
                requirement_id="mission.land",
                kind="land",
                dependencies=("mission.rtl",),
                launch_site_id="launch.alpha",
                target_id=None,
                expected_public_asset_id=None,
            ),
        ),
        "expected_public_assets": (expected_public_asset(),),
    }


def materialize_world_package(
    root: Path,
    *,
    mutate_kwargs: Callable[[dict[str, Any], Path], None] | None = None,
) -> tuple[BundleReader, FileRef, WorldPackage]:
    """Write deterministic real WorldPackage bytes and return their strict binding."""

    root.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = dict(content_kwargs())
    geoid_grid, terrain_grid = default_scalar_grids()

    license_bytes = b"CC-BY-4.0 fixture license\n"
    license_path = "licenses/cc-by-4.0.txt"
    license_destination = root / license_path
    license_destination.parent.mkdir(parents=True, exist_ok=True)
    license_destination.write_bytes(license_bytes)
    license_ref = ArtifactSelector(
        artifact_id="license.cc-by-4.0",
        selector=license_path,
        sha256=hashlib.sha256(license_bytes).hexdigest(),
    )

    records: list[AssetRecord] = []
    for template in kwargs["assets"]:
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
        destination = root / selector
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        records.append(
            template.model_copy(
                update={
                    "artifact": ArtifactSelector(
                        artifact_id=asset_id,
                        selector=selector,
                        sha256=hashlib.sha256(data).hexdigest(),
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
    package_bytes = canonical_json_bytes(package.model_dump(mode="json")) + b"\n"
    relative_path = "world/package.json"
    package_path = root / relative_path
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(package_bytes)
    reference = FileRef(
        path=relative_path,
        sha256=hashlib.sha256(package_bytes).hexdigest(),
    )
    return BundleReader(root), reference, package


def compile_scenario_fixture(
    root: Path,
    *,
    launch_site_id: str = "launch.alpha",
    seed: int = 7,
    capabilities: dict[str, tuple[str, ...]] | None = None,
    mutate_kwargs: Callable[[dict[str, Any], Path], None] | None = None,
) -> ResolvedScenario:
    reader, world_reference, _ = materialize_world_package(
        root,
        mutate_kwargs=mutate_kwargs,
    )
    task, agents, task_projection = scenario_task_inputs()
    return compile_resolved_scenario(
        reader,
        world_reference,
        launch_site_id,
        seed,
        provider_capabilities() if capabilities is None else capabilities,
        provider_stages((provider_capabilities() if capabilities is None else capabilities).keys()),
        task,
        agents,
        task_projection,
    )


def _inspection_verifier_world(kwargs: dict[str, Any], _root: Path) -> None:
    """Project the base world for an Inspection task without runtime Business authority."""

    kwargs["provider_requirements"] = tuple(
        ProviderRequirement(
            provider_id="observation.provider",
            roles=("sensor",),
            required_capability_ids=("camera.rgb", "observation.capture"),
        )
        if requirement.provider_id == "perception"
        else requirement
        for requirement in kwargs["provider_requirements"]
    )
    kwargs["sensors"] = tuple(
        source.model_copy(
            update={
                "sensor_id": "camera.1",
                "provider_id": "observation.provider",
                "horizontal_fov_deg": 120.0,
                "vertical_fov_deg": 120.0,
            }
        )
        for source in kwargs["sensors"]
    )
    kwargs["semantic_targets"] = tuple(
        target.model_copy(
            update={
                "target_id": "asset.1",
                "required_sensor_id": "camera.1",
                "min_distance_m": 2.0,
                "max_distance_m": 20.0,
                "max_view_angle_deg": 45.0,
            }
        )
        for target in kwargs["semantic_targets"]
    )
    kwargs["mission_requirements"] = tuple(
        requirement.model_copy(update={"target_id": "asset.1"})
        if requirement.kind == "observation"
        else requirement
        for requirement in kwargs["mission_requirements"]
    )


def _materialized_fixture_file(root: Path, path: str, payload: bytes) -> FileRef:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return FileRef(path=path, sha256=hashlib.sha256(payload).hexdigest())


def inspection_scenario_task_inputs(
    root: Path,
) -> tuple[TaskSpec, tuple[AgentSpec, ...], ResolvedTaskScenarioProjection]:
    """Build byte-real task assets and the task-local Business projection."""

    simulation_asset = _materialized_fixture_file(
        root,
        "private/inspection-scene.sdf",
        (
            b'<?xml version="1.0"?>\n'
            b'<sdf version="1.9"><model name="inspection_asset">'
            b"<static>true</static></model></sdf>\n"
        ),
    )
    truth_asset = _materialized_fixture_file(
        root,
        "private/inspection-labels.json",
        canonical_json_bytes(
            [
                {
                    "defect_id": "defect.1",
                    "geometrically_visible": True,
                    "target_id": "asset.1",
                }
            ]
        )
        + b"\n",
    )
    verifier_id = "inspection.verifier"
    task = TaskSpec(
        schema_version="aero-bench.task/v1",
        task_id="inspection.task",
        package=TaskPackageRef(
            package_id="inspection.v1",
            config=SchemaBoundFile(
                file=_fixture_file("configs/inspection.json"),
                schema_file=_fixture_file("schemas/inspection-package.json"),
            ),
        ),
        instruction=_fixture_file("instructions/inspection.txt"),
        required_capabilities=("business.work-order", "observation.capture"),
        required_tools=("business.work-order",),
        assets=(
            AssetRef(
                asset_id="world.images",
                file=simulation_asset,
                classification="private",
                audiences=(
                    TaskAssetAudience(
                        role="provider",
                        workload_ids=("observation.provider",),
                    ),
                ),
            ),
            AssetRef(
                asset_id="verifier.labels",
                file=truth_asset,
                classification="private",
                audiences=(
                    TaskAssetAudience(
                        role="verifier",
                        workload_ids=(verifier_id,),
                    ),
                ),
            ),
        ),
        goals=(
            GoalSpec(
                goal_id="goal.complete",
                verifier_id=verifier_id,
                metric_id="work_order.completion_rate",
                operator="eq",
                threshold=1.0,
                evidence="authoritative_state",
                parameters=(),
            ),
        ),
        verifier=VerifierSpec(
            verifier_id=verifier_id,
            workload=_fixture_runtime(verifier_id),
            config=SchemaBoundFile(
                file=_fixture_file("configs/inspection-verifier.json"),
                schema_file=_fixture_file("schemas/inspection-verifier.json"),
            ),
            artifact_requirements=(),
            output_artifacts=(
                ArtifactRequirement(
                    artifact_id="artifact.verification",
                    artifact_type="verification.report",
                    producer_id=verifier_id,
                    visibility="public",
                    relative_path="verification/report.json",
                    max_size_bytes=4096,
                    source_asset_id=None,
                ),
            ),
        ),
    )
    agents = (
        AgentSpec(
            schema_version="aero-bench.agent/v2",
            agent_id="agent.1",
            workload=_fixture_runtime("agent.1"),
            tools=(
                ToolGrant(
                    tool_id="business.work-order",
                    provider_id="business",
                    request_schema=_fixture_file("schemas/business-request.json"),
                    response_schema=_fixture_file("schemas/business-response.json"),
                    timeout_ms=1000,
                    idempotent=False,
                ),
            ),
            queries=(),
            observations=(
                ObservationGrant(
                    observation_id="obs.1",
                    provider_id="observation.provider",
                    schema_file=_fixture_file("schemas/observation.json"),
                    timeout_ms=1000,
                ),
            ),
            artifact_requirements=(),
        ),
    )
    projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=("business",),
        logical_capability_ids=("business.work-order",),
        observations=(
            ResolvedInspectionObservationProjection(
                projection_kind="inspection",
                observation_id="obs.1",
                work_order_id="wo.1",
                target_id="asset.1",
                simulation_asset_id="world.images",
                sensor_id="camera.1",
                media_type="image/png",
                min_distance_m=2.0,
                max_distance_m=20.0,
                min_view_angle_deg=0.0,
                max_view_angle_deg=45.0,
                earliest_time_ns=0,
                latest_time_ns=20_000_000_000,
            ),
        ),
    )
    return task, agents, projection


def materialized_inspection_scenario(
    root: Path,
    *,
    seed: int = 7,
) -> ResolvedScenario:
    """Compile the Inspection scenario while preserving its source bundle bytes."""

    capabilities = {
        "flight": ("gazebo.frames", "gazebo.physics"),
        "observation.provider": ("camera.rgb", "observation.capture"),
    }
    reader, world_reference, _ = materialize_world_package(
        root,
        mutate_kwargs=_inspection_verifier_world,
    )
    task, agents, task_projection = inspection_scenario_task_inputs(root)
    return compile_resolved_scenario(
        reader,
        world_reference,
        "launch.alpha",
        seed,
        capabilities,
        provider_stages((capabilities).keys()),
        task,
        agents,
        task_projection,
    )


@lru_cache(maxsize=None)
def deterministic_inspection_scenario(seed: int = 7) -> ResolvedScenario:
    """Compile one reusable Inspection scenario with task-local Business authority."""

    with TemporaryDirectory(prefix="aero-scenario-fixture-") as temporary:
        return materialized_inspection_scenario(Path(temporary), seed=seed)


def world_package_valid() -> WorldPackage:
    return world_package(**content_kwargs())


def world_package_raw() -> dict[str, object]:
    return world_package_valid().model_dump(mode="json")


__all__ = [
    "DIGEST_BUILDING_COLLISION",
    "DIGEST_BUILDING_RENDER",
    "DIGEST_BUILDING_SOURCE",
    "DIGEST_GAZEBO_SCENE",
    "DIGEST_GEOID",
    "DIGEST_IMAGERY",
    "DIGEST_LICENSE",
    "DIGEST_PAD_MODEL",
    "DIGEST_PEDESTRIAN_MODEL",
    "DIGEST_SUMO_ADDITIONAL",
    "DIGEST_SUMO_CFG",
    "DIGEST_SUMO_NET",
    "DIGEST_SUMO_ROUTES",
    "DIGEST_TERRAIN_LAYER",
    "DIGEST_TERRAIN_MODEL",
    "DIGEST_UGV_MODEL",
    "DIGEST_UAV_MODEL",
    "asset_record",
    "assets",
    "building",
    "building_entity",
    "building_geometry",
    "compile_scenario_fixture",
    "content_kwargs",
    "default_scalar_grids",
    "deterministic_inspection_scenario",
    "expected_public_asset",
    "file_size_precision",
    "file_size_unit",
    "frame_origin",
    "inspection_scenario_task_inputs",
    "license_file",
    "pedestrian_entity",
    "provider_capabilities",
    "materialize_world_package",
    "scalar_grid_document",
    "scenario_task_inputs",
    "semantic_target",
    "sensor",
    "static_target_entity",
    "uav_entity",
    "ugv_entity",
    "weather",
    "world_frame",
    "world_package_raw",
    "world_package_valid",
]
