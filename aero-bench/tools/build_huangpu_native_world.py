#!/usr/bin/env python3
"""Build the source-derived Huangpu native WorldPackage.

The builder starts from the canonical urban-scene authoring stage, preserving
all 414 validated building GLBs and all 289 road-width records. It then adds
only explicit runtime bindings backed by current production contracts:

* the accepted road-v3, fixture, ground-cover, SUMO network, and route bytes;
* a Gazebo world derived from the compiled city SDF, with deterministic physics
  systems, a ground plane, one authored launch pad, and one operations station;
* exact SUMO object bindings for every object in the accepted route file;
* one UAV, one camera, two source-facade inspection targets, and a bounded
  ns-3 network declaration.

The generated clear-weather sample and flat scalar grids are authored scenario
inputs derived from the compiled scene datum. They are not observations. The
authorization file records the user's mentor-authorized internal research use
without asserting upstream ownership, licensing, or redistribution rights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeInputLock,
    PinnedCityFile,
    assess_city_native_registration,
    load_city_native_input_lock,
)
from aero_bench.config.loader import sha256_file  # noqa: E402
from aero_bench.providers.ns3.config import ns3_capabilities  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.world.building_render_merge import (  # noqa: E402
    merge_building_render_fragment,
    verify_staged_render_bytes,
)
from aero_bench.world.contracts import (  # noqa: E402
    ArtifactSelector,
    AssetAudience,
    AssetProvenance,
    AssetRecord,
    EngineFrameBinding,
    EntitySpec,
    ExpectedPublicAsset,
    LaunchSiteSpec,
    MissionRequirement,
    NetworkConfiguration,
    NetworkLinkBinding,
    NetworkNodeBinding,
    ParentRelativePose,
    PrecisionMetadata,
    ProviderRequirement,
    PublicLayer,
    RegionGeometry,
    RegionSpec,
    RigidOffset,
    SemanticTargetSpec,
    SensorSpec,
    SpatialBounds,
    SumoConfiguration,
    SumoEntityBinding,
    TargetGeometry,
    TargetSurfaceNormal,
    UnitDeclaration,
    WeatherSample,
    WirelessRadioProfile,
    WindVector,
    WorldPackage,
    WorldPose,
    world_package,
)
from aero_bench.world.scene_authoring import (  # noqa: E402
    UrbanWorldAuthoringRequest,
    UrbanWorldCatalog,
    author_urban_world_package,
)
from aero_bench.world.scene_compiler import SceneOrigin  # noqa: E402


WORLD_ID = "world.shanghai-huangpu-east-v1"
AUTHORIZATION_ID = "LicenseRef-InternalResearchAuthorization"
FLIGHT_ID = "flight"
TRAFFIC_ID = "traffic"
NETWORK_ID = "network"
BUSINESS_ID = "business"
UAV_ID = "uav.inspector"
SENSOR_ID = "camera.inspection.front"
LAUNCH_ID = "launch.huangpu-research"
PAD_ENTITY_ID = "entity.launch.huangpu-research"
STATION_ENTITY_ID = "station.operations"
TARGET_IDS = (
    "target.city-facade.lower",
    "target.city-facade.upper",
)
GEOFENCE_ID = "region.city-operational-geofence"
NO_FLY_ID = "region.city-no-fly-east"
WEATHER_ID = "weather.authored-clear"
LAUNCH_EAST_M = -450.0
LAUNCH_NORTH_M = -450.0
STATION_EAST_M = -450.0
STATION_NORTH_M = -420.0
UAV_LAUNCH_REFERENCE_UP_M = 0.137
WORLD_NAME = "huangpu_native"
TARGET_STANDOFF_M = 10.0
TARGET_PANEL_HALF_HEIGHT_M = 0.4
TARGET_ALTITUDE_OFFSETS_M = (4.0, 9.0)
OPERATIONAL_GEOFENCE_MIN_M = -525.0
OPERATIONAL_GEOFENCE_MAX_M = 480.0
OPERATIONAL_ROUTE_MARGIN_M = 10.0
GROUND_SIZE_M = 1100.0
# Declared clearance added around the measured SUMO network surface (all lane
# and junction shapes). SUMO reports object positions on lane centrelines or
# inside walking-area/junction polygons; the margin covers lateral offsets and
# body extents so every Provider-resolved traffic pose samples inside the
# authored geoid/terrain grids and WorldFrame.spatial_extent.
SUMO_NETWORK_EXTENT_MARGIN_M = 10.0
# Maximum accepted difference between the SUMO aeqd projection centre and the
# compiled scene origin. The SUMO frame binding is identity, so the projection
# must be centred on the ENU origin.
SUMO_PROJECTION_CENTRE_TOLERANCE_DEG = 1e-9

BUILD_REPORT_SCHEMA = "aero-bench.huangpu-native-world-build/v1"


class HuangpuNativeWorldBuildError(ValueError):
    """The exact city sources cannot produce the declared native package."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-lock", required=True)
    parser.add_argument("--authoring-output", required=True)
    parser.add_argument("--world-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--world-id", default=WORLD_ID)
    return parser


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HuangpuNativeWorldBuildError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise HuangpuNativeWorldBuildError(f"{label} must be a JSON object: {path}")
    return document


def _write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _write_json(path: Path, document: object, *, pretty: bool = False) -> Path:
    if pretty:
        payload = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
    else:
        payload = canonical_json_bytes(document) + b"\n"
    return _write_bytes(path, payload)


def _require_new_path(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        raise HuangpuNativeWorldBuildError(
            f"{label} must not already exist; refusing to overwrite: {path}"
        )


def _repository_relative(path: Path) -> str:
    resolved = path.resolve(strict=True)
    root = REPOSITORY_ROOT.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise HuangpuNativeWorldBuildError(
            f"output must remain inside the repository: {resolved}"
        )
    return resolved.relative_to(root).as_posix()


def _copy_pinned(
    *, bundle_root: Path, pinned: PinnedCityFile, destination: str
) -> Path:
    source = REPOSITORY_ROOT / pinned.file.path
    if (
        source.stat().st_size != pinned.size_bytes
        or sha256_file(source) != pinned.file.sha256
    ):
        raise HuangpuNativeWorldBuildError(
            f"pinned source drifted before staging: {pinned.file.path}"
        )
    target = bundle_root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if (
        target.stat().st_size != pinned.size_bytes
        or sha256_file(target) != pinned.file.sha256
    ):
        raise HuangpuNativeWorldBuildError(
            f"staged bytes differ from the pinned source: {destination}"
        )
    return target


def _authorization_text() -> str:
    return """Internal research-use authorization record

The repository artifacts do not establish the original upstream source,
copyright ownership, or license terms for every city asset. The user states
that these materials are shared within their research group and that their
mentor authorized their use in this project for internal research.

This record does not claim copyright ownership, grant a third-party license,
or authorize public redistribution. It records only the current, user-declared
research-use authorization boundary.
"""


def _scalar_grid(value_m: float, coverage: SpatialBounds) -> dict[str, object]:
    if not (
        coverage.min_east_m < 0.0 < coverage.max_east_m
        and coverage.min_north_m < 0.0 < coverage.max_north_m
    ):
        raise HuangpuNativeWorldBuildError(
            "scalar grid coverage must strictly contain the ENU origin"
        )
    return {
        "schema_version": "aero-bench.scalar-grid/v1",
        "frame_id": "ENU",
        "east_axis_m": [coverage.min_east_m, 0.0, coverage.max_east_m],
        "north_axis_m": [coverage.min_north_m, 0.0, coverage.max_north_m],
        "values_m": [[value_m] * 3 for _ in range(3)],
    }


def _shape_points(shape: str, label: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in shape.split():
        parts = token.split(",")
        if len(parts) not in {2, 3}:
            raise HuangpuNativeWorldBuildError(f"{label} has an invalid point: {token}")
        try:
            east_m, north_m = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise HuangpuNativeWorldBuildError(
                f"{label} has a non-numeric point: {token}"
            ) from exc
        if not (math.isfinite(east_m) and math.isfinite(north_m)):
            raise HuangpuNativeWorldBuildError(f"{label} has a non-finite point")
        points.append((east_m, north_m))
    if not points:
        raise HuangpuNativeWorldBuildError(f"{label} has an empty shape")
    return points


def _planar_bounds(points: list[tuple[float, float]]) -> dict[str, float]:
    return {
        "min_east_m": min(item[0] for item in points),
        "max_east_m": max(item[0] for item in points),
        "min_north_m": min(item[1] for item in points),
        "max_north_m": max(item[1] for item in points),
    }


def _measure_sumo_network(network_path: Path, origin: SceneOrigin) -> dict[str, object]:
    """Measure the pinned SUMO network surface in the scene ENU frame.

    The world binds SUMO network coordinates to ENU with an identity
    transform. That is valid only when the network has a zero netOffset and an
    azimuthal-equidistant projection centred on the scene origin; anything else
    is rejected rather than reinterpreted.
    """

    try:
        root = ET.parse(network_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HuangpuNativeWorldBuildError(
            f"invalid accepted SUMO network: {exc}"
        ) from exc
    locations = root.findall("location")
    if len(locations) != 1:
        raise HuangpuNativeWorldBuildError(
            "accepted SUMO network must declare exactly one location"
        )
    location = locations[0]
    try:
        offset = tuple(float(item) for item in location.get("netOffset", "").split(","))
    except ValueError as exc:
        raise HuangpuNativeWorldBuildError(
            "accepted SUMO network netOffset is not numeric"
        ) from exc
    if offset != (0.0, 0.0):
        raise HuangpuNativeWorldBuildError(
            f"accepted SUMO network netOffset must be 0,0; found {offset}"
        )
    projection = location.get("projParameter", "")
    parameters: dict[str, str] = {}
    for token in projection.split():
        key, separator, value = token.partition("=")
        parameters[key] = value if separator else ""
    required = {"+proj": "aeqd", "+datum": "WGS84", "+units": "m"}
    if any(parameters.get(key) != value for key, value in required.items()):
        raise HuangpuNativeWorldBuildError(
            f"accepted SUMO network projection must be WGS84 aeqd in metres: {projection}"
        )
    try:
        latitude_deg = float(parameters["+lat_0"])
        longitude_deg = float(parameters["+lon_0"])
    except (KeyError, ValueError) as exc:
        raise HuangpuNativeWorldBuildError(
            f"accepted SUMO network projection has no numeric centre: {projection}"
        ) from exc
    if (
        abs(latitude_deg - origin.latitude_deg) > SUMO_PROJECTION_CENTRE_TOLERANCE_DEG
        or abs(longitude_deg - origin.longitude_deg)
        > SUMO_PROJECTION_CENTRE_TOLERANCE_DEG
    ):
        raise HuangpuNativeWorldBuildError(
            "accepted SUMO network projection centre "
            f"({latitude_deg}, {longitude_deg}) differs from the scene origin "
            f"({origin.latitude_deg}, {origin.longitude_deg})"
        )

    lane_points: list[tuple[float, float]] = []
    lane_count = 0
    for lane in root.iter("lane"):
        shape = lane.get("shape")
        if not shape:
            raise HuangpuNativeWorldBuildError(
                f"SUMO lane has no shape: {lane.get('id')}"
            )
        lane_points.extend(_shape_points(shape, f"SUMO lane {lane.get('id')}"))
        lane_count += 1
    junction_points: list[tuple[float, float]] = []
    junction_count = 0
    for junction in root.findall("junction"):
        shape = junction.get("shape")
        if shape:
            junction_points.extend(
                _shape_points(shape, f"SUMO junction {junction.get('id')}")
            )
        junction_points.extend(
            _shape_points(
                f"{junction.get('x')},{junction.get('y')}",
                f"SUMO junction {junction.get('id')} position",
            )
        )
        junction_count += 1
    if not lane_points:
        raise HuangpuNativeWorldBuildError("accepted SUMO network has no lanes")
    surface = _planar_bounds(lane_points + junction_points)
    margin = SUMO_NETWORK_EXTENT_MARGIN_M
    return {
        "net_offset": list(offset),
        "projection": projection,
        "projection_centre_deg": {
            "latitude_deg": latitude_deg,
            "longitude_deg": longitude_deg,
        },
        "lane_count": lane_count,
        "junction_count": junction_count,
        "lane_bounds_m": _planar_bounds(lane_points),
        "junction_bounds_m": _planar_bounds(junction_points)
        if junction_points
        else None,
        "surface_bounds_m": surface,
        "margin_m": margin,
        "required_bounds_m": {
            "min_east_m": surface["min_east_m"] - margin,
            "max_east_m": surface["max_east_m"] + margin,
            "min_north_m": surface["min_north_m"] - margin,
            "max_north_m": surface["max_north_m"] + margin,
        },
    }


def _provider_coverage(network: dict[str, object]) -> SpatialBounds:
    """Return the planar box every Provider-resolved pose must lie inside.

    It is the union of the operational geofence (flight) and the measured SUMO
    surface plus margin (traffic), rounded outward to whole metres.
    """

    required = network["required_bounds_m"]
    if not isinstance(required, dict):
        raise HuangpuNativeWorldBuildError("SUMO required bounds are malformed")
    return SpatialBounds(
        min_east_m=float(
            math.floor(min(OPERATIONAL_GEOFENCE_MIN_M, required["min_east_m"]))
        ),
        max_east_m=float(
            math.ceil(max(OPERATIONAL_GEOFENCE_MAX_M, required["max_east_m"]))
        ),
        min_north_m=float(
            math.floor(min(OPERATIONAL_GEOFENCE_MIN_M, required["min_north_m"]))
        ),
        max_north_m=float(
            math.ceil(max(OPERATIONAL_GEOFENCE_MAX_M, required["max_north_m"]))
        ),
        min_up_m=-5.0,
        max_up_m=200.0,
        vertical_reference="enu_up",
    )


def _catalog(
    *,
    definition: CityNativeInputLock,
    authoring_root: Path,
    manifest: dict[str, Any],
    coverage: SpatialBounds,
) -> UrbanWorldCatalog:
    origin = manifest.get("origin")
    if not isinstance(origin, dict):
        raise HuangpuNativeWorldBuildError("compiled scene manifest has no origin")
    geoid_m = origin.get("geoid_undulation_m")
    terrain_m = origin.get("amsl_m")
    if not isinstance(geoid_m, (int, float)) or isinstance(geoid_m, bool):
        raise HuangpuNativeWorldBuildError("compiled geoid_undulation_m is not numeric")
    if not isinstance(terrain_m, (int, float)) or isinstance(terrain_m, bool):
        raise HuangpuNativeWorldBuildError("compiled amsl_m is not numeric")

    _write_bytes(authoring_root / "authorization.txt", _authorization_text().encode())
    _write_json(
        authoring_root / "geoid-grid.json", _scalar_grid(float(geoid_m), coverage)
    )
    _write_json(
        authoring_root / "terrain-grid.json", _scalar_grid(float(terrain_m), coverage)
    )

    road_width_document = _read_json(
        REPOSITORY_ROOT / definition.road_width_catalog.file.path,
        "accepted road-width catalog",
    )
    widths = road_width_document.get("road_width_m")
    if not isinstance(widths, list) or len(widths) != 289:
        raise HuangpuNativeWorldBuildError(
            "accepted road-width catalog must contain exactly 289 entries"
        )
    scene_manifest_digest = definition.scene_manifest.file.sha256
    provenance = {
        "source_kind": "bundled_offline",
        "recorded_by": "city-native-authoring",
        "source_dataset": "compiled-scene-flat-datum",
        "source_version": scene_manifest_digest,
        "runtime_download": False,
    }
    raw = {
        "schema_version": "aero-bench.urban-world-authoring/v1",
        "license_id": AUTHORIZATION_ID,
        "license_path": "licenses/internal-research-authorization.txt",
        "license_file": "authorization.txt",
        "assets": [
            {
                "asset_id": "asset.geoid",
                "path": "world/geoid/grid.json",
                "file": "geoid-grid.json",
                "asset_role": "geoid_model",
                "media_type": "application/json",
                "source_frame": "raster_pixel",
                "units": [{"quantity": "height", "unit": "m"}],
                "precision": [
                    {
                        "quantity": "height",
                        "kind": "absolute_tolerance",
                        "unit": "m",
                        "exact_bytes": None,
                        "absolute_tolerance": 0.001,
                    }
                ],
                "provenance": provenance,
            },
            {
                "asset_id": "asset.terrain-heights",
                "path": "world/terrain/heights.json",
                "file": "terrain-grid.json",
                "asset_role": "terrain_model",
                "media_type": "application/json",
                "source_frame": "raster_pixel",
                "units": [{"quantity": "height", "unit": "m"}],
                "precision": [
                    {
                        "quantity": "height",
                        "kind": "absolute_tolerance",
                        "unit": "m",
                        "exact_bytes": None,
                        "absolute_tolerance": 0.001,
                    }
                ],
                "provenance": provenance,
            },
        ],
        "weather": [
            {
                "sample_id": WEATHER_ID,
                "mode": "deterministic_constant",
                "wind": {"east_mps": 0.0, "north_mps": 0.0, "up_mps": 0.0},
                "visibility_m": 10_000.0,
                "precipitation": "none",
                "precipitation_rate_mm_per_h": 0.0,
                "temperature_c": 20.0,
                "pressure_pa": 101_325.0,
            }
        ],
        "road_width_m": widths,
        "building_render": [],
    }
    return UrbanWorldCatalog.model_validate(raw)


def _origin(manifest: dict[str, Any]) -> SceneOrigin:
    raw = manifest.get("origin")
    if not isinstance(raw, dict):
        raise HuangpuNativeWorldBuildError("compiled scene manifest has no origin")
    try:
        return SceneOrigin(
            latitude_deg=float(raw["latitude_deg"]),
            longitude_deg=float(raw["longitude_deg"]),
            ellipsoid_height_m=float(raw["ellipsoid_height_m"]),
            geoid_undulation_m=float(raw["geoid_undulation_m"]),
            amsl_m=float(raw["amsl_m"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HuangpuNativeWorldBuildError(
            f"compiled scene origin is incomplete: {exc}"
        ) from exc


def _runtime_sdf(source: bytes) -> bytes:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HuangpuNativeWorldBuildError("compiled Gazebo SDF is not UTF-8") from exc
    if text.count('<world name="urban_scene">') != 1:
        raise HuangpuNativeWorldBuildError(
            "compiled Gazebo SDF must contain one urban_scene world"
        )
    if text.count("</spherical_coordinates>") != 1:
        raise HuangpuNativeWorldBuildError(
            "compiled Gazebo SDF must contain one spherical_coordinates block"
        )
    text = text.replace(
        '<world name="urban_scene">',
        f"""<world name="{WORLD_NAME}">
    <paused>true</paused>
    <gravity>0 0 -9.80665</gravity>
    <magnetic_field>2.2e-5 0 4.2e-5</magnetic_field>
    <atmosphere type="adiabatic"/>
    <wind><linear_velocity>0 0 0</linear_velocity></wind>""",
    )
    runtime = f"""
    <physics name="deterministic_4ms" type="ignored"><max_step_size>0.004</max_step_size><real_time_factor>0</real_time_factor></physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"/>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
    <plugin filename="gz-sim-air-pressure-system" name="gz::sim::systems::AirPressure"/>
    <plugin filename="gz-sim-air-speed-system" name="gz::sim::systems::AirSpeed"/>
    <plugin filename="gz-sim-magnetometer-system" name="gz::sim::systems::Magnetometer"/>
    <plugin filename="gz-sim-navsat-system" name="gz::sim::systems::NavSat"/>
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
    <scene><ambient>0.55 0.55 0.55 1</ambient><background>0.70 0.82 0.94 1</background><shadows>false</shadows></scene>
    <light type="directional" name="sun"><cast_shadows>false</cast_shadows><pose>0 0 500 0 0 0</pose><diffuse>0.9 0.9 0.9 1</diffuse><specular>0.2 0.2 0.2 1</specular><direction>-0.4 0.2 -0.9</direction></light>
    <model name="ground"><static>true</static><link name="ground_link"><collision name="ground_collision"><geometry><plane><normal>0 0 1</normal><size>{GROUND_SIZE_M} {GROUND_SIZE_M}</size></plane></geometry></collision><visual name="ground_visual"><geometry><plane><normal>0 0 1</normal><size>{GROUND_SIZE_M} {GROUND_SIZE_M}</size></plane></geometry><material><ambient>0.34 0.39 0.32 1</ambient><diffuse>0.34 0.39 0.32 1</diffuse></material></visual></link></model>
    <model name="{LAUNCH_ID}"><static>true</static><pose>{LAUNCH_EAST_M} {LAUNCH_NORTH_M} 0.075 0 0 0</pose><link name="pad"><collision name="pad_collision"><geometry><box><size>8 8 0.15</size></box></geometry></collision><visual name="pad_visual"><geometry><box><size>8 8 0.15</size></box></geometry><material><ambient>0.12 0.35 0.72 1</ambient><diffuse>0.12 0.35 0.72 1</diffuse></material></visual></link></model>
    <model name="{STATION_ENTITY_ID}"><static>true</static><pose>{STATION_EAST_M} {STATION_NORTH_M} 2 0 0 0</pose><link name="station"><collision name="station_collision"><geometry><box><size>8 8 4</size></box></geometry></collision><visual name="station_visual"><geometry><box><size>8 8 4</size></box></geometry><material><ambient>0.22 0.62 0.34 1</ambient><diffuse>0.22 0.62 0.34 1</diffuse></material></visual></link></model>"""
    text = text.replace(
        "    </spherical_coordinates>",
        "    </spherical_coordinates>" + runtime,
    )
    payload = text.encode("utf-8")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise HuangpuNativeWorldBuildError(
            f"derived Gazebo SDF is invalid XML: {exc}"
        ) from exc
    worlds = root.findall("world")
    if len(worlds) != 1 or worlds[0].get("name") != WORLD_NAME:
        raise HuangpuNativeWorldBuildError("derived Gazebo world identity is invalid")
    return payload


def _sumo_config() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <input><net-file value="network.net.xml"/><route-files value="routes.rou.xml"/><additional-files value="additional.add.xml"/></input>
  <time><begin value="0"/><end value="120"/><step-length value="0.25"/></time>
  <processing><time-to-teleport value="-1"/></processing>
  <report><no-step-log value="true"/><duration-log.disable value="true"/><no-warnings value="true"/></report>
</configuration>
"""


def _sumo_objects(routes_path: Path) -> tuple[tuple[str, str], ...]:
    try:
        root = ET.parse(routes_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HuangpuNativeWorldBuildError(
            f"invalid accepted SUMO routes: {exc}"
        ) from exc
    objects: list[tuple[str, str]] = []
    for element in root:
        if element.tag not in {"vehicle", "person"}:
            continue
        object_id = element.get("id")
        if not object_id:
            raise HuangpuNativeWorldBuildError("SUMO route object has no id")
        objects.append((object_id, "person" if element.tag == "person" else "vehicle"))
    if len(objects) != 108 or len({item[0] for item in objects}) != len(objects):
        raise HuangpuNativeWorldBuildError(
            "accepted SUMO routes must declare 108 unique vehicle/person objects"
        )
    return tuple(sorted(objects))


def _route_initial_positions(
    *,
    network_path: Path,
    routes_path: Path,
    objects: tuple[tuple[str, str], ...],
    coverage: SpatialBounds,
) -> dict[str, tuple[float, float]]:
    """Resolve pending entity poses from the formal network/route bytes.

    These poses are only the WorldPackage's pre-departure declarations; SUMO
    becomes authoritative at runtime. Deriving them from the first route edge
    avoids treating the engineering-preview recording as formal state.
    """

    try:
        network_root = ET.parse(network_path).getroot()
        routes_root = ET.parse(routes_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HuangpuNativeWorldBuildError(
            f"invalid accepted SUMO network/routes: {exc}"
        ) from exc
    edge_first_points: dict[str, tuple[float, float]] = {}
    for edge in network_root.findall("edge"):
        edge_id = edge.get("id")
        lanes = edge.findall("lane")
        if not edge_id or not lanes:
            continue
        shape = lanes[0].get("shape")
        if not shape:
            continue
        first = shape.split()[0].split(",")
        if len(first) != 2:
            raise HuangpuNativeWorldBuildError(
                f"SUMO lane shape has no x,y first point: {edge_id}"
            )
        edge_first_points[edge_id] = (float(first[0]), float(first[1]))

    route_elements = {
        element.get("id"): element
        for element in routes_root
        if element.tag in {"vehicle", "person"} and element.get("id")
    }
    positions: dict[str, tuple[float, float]] = {}
    for object_id, kind in objects:
        element = route_elements.get(object_id)
        child = (
            element.find("walk" if kind == "person" else "route")
            if element is not None
            else None
        )
        edges = child.get("edges", "").split() if child is not None else []
        if not edges or edges[0] not in edge_first_points:
            raise HuangpuNativeWorldBuildError(
                f"SUMO object {object_id} has no resolvable first route edge"
            )
        positions[object_id] = edge_first_points[edges[0]]
    if any(
        not math.isfinite(east_m)
        or not math.isfinite(north_m)
        or not coverage.min_east_m <= east_m <= coverage.max_east_m
        or not coverage.min_north_m <= north_m <= coverage.max_north_m
        for east_m, north_m in positions.values()
    ):
        raise HuangpuNativeWorldBuildError(
            "native SUMO initial positions fall outside the city extent"
        )
    return positions


def _pose(east_m: float, north_m: float, up_m: float = 0.0) -> WorldPose:
    return WorldPose(
        frame_id="ENU",
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        qw=1.0,
        qx=0.0,
        qy=0.0,
        qz=0.0,
    )


def _asset_record(
    *,
    bundle_root: Path,
    asset_id: str,
    selector: str,
    role: str,
    media_type: str,
    visibility: str,
    source_frame: str,
    license_ref: ArtifactSelector,
    provider_ids: tuple[str, ...] = (),
    source_dataset: str,
) -> AssetRecord:
    path = bundle_root / selector
    size = path.stat().st_size
    if visibility == "public":
        audiences = (AssetAudience(audience_kind="public", audience_id="all"),)
    else:
        if not provider_ids:
            raise HuangpuNativeWorldBuildError(
                f"private asset {asset_id} has no Provider audience"
            )
        audiences = tuple(
            AssetAudience(audience_kind="provider", audience_id=item)
            for item in provider_ids
        )
    return AssetRecord(
        artifact=ArtifactSelector(
            artifact_id=asset_id,
            selector=selector,
            sha256=sha256_file(path),
        ),
        asset_role=role,
        byte_size=size,
        media_type=media_type,
        visibility=visibility,
        audiences=audiences,
        units=(UnitDeclaration(quantity="file_size", unit="B"),),
        source_frame=source_frame,
        precision=(
            PrecisionMetadata(
                quantity="file_size",
                kind="exact_bytes",
                unit="B",
                exact_bytes=size,
                absolute_tolerance=None,
            ),
        ),
        provenance=AssetProvenance(
            source_kind="bundled_offline",
            recorded_by="city-native-authoring",
            source_dataset=source_dataset,
            source_version="2026.10.01",
            runtime_download=False,
        ),
        license_id=AUTHORIZATION_ID,
        license_file=license_ref,
    )


def _symbol(name: str, dimensions: tuple[float, float, float]) -> bytes:
    return (
        canonical_json_bytes(
            {
                "schema_version": "aero-bench.entity-symbol/v1",
                "symbol": name,
                "dimensions_m": list(dimensions),
                "frame_id": "asset_local",
            }
        )
        + b"\n"
    )


def _targets(
    base: WorldPackage,
) -> tuple[tuple[SemanticTargetSpec, ...], str, dict[str, float]]:
    """Select one source facade that supports the production reference route.

    The installed participant requires two west-facing targets whose camera
    standoff points share one east coordinate.  Both targets therefore occupy
    distinct vertical positions on the same verified source-building facade.
    Candidate selection also accounts for the declared camera mount and the
    10 m standoff so the complete observation pose remains inside the authored
    operational geofence.
    """

    entity_by_id = {item.entity_id: item for item in base.entities}
    candidates: list[tuple[float, str, object, float, float]] = []
    minimum_target_up = max(TARGET_ALTITUDE_OFFSETS_M) + TARGET_PANEL_HALF_HEIGHT_M
    for building in base.buildings:
        if (
            building.geometry.top_altitude_m - building.geometry.base_altitude_m
            < minimum_target_up
        ):
            continue
        points = building.geometry.footprint_enu_m
        min_east = min(item.east_m for item in points)
        for index, first in enumerate(points):
            second = points[(index + 1) % len(points)]
            if (
                abs(first.east_m - second.east_m) <= 0.25
                and abs((first.east_m + second.east_m) * 0.5 - min_east) <= 0.25
                and abs(first.north_m - second.north_m) >= 1.5
            ):
                target_north = (first.north_m + second.north_m) * 0.5
                # The reference participant subtracts both standoff and the
                # camera's +0.12 m east mount offset from the target pose.
                navigation_east = min_east - 0.05 - TARGET_STANDOFF_M - 0.12
                route_min = OPERATIONAL_GEOFENCE_MIN_M + OPERATIONAL_ROUTE_MARGIN_M
                route_max = OPERATIONAL_GEOFENCE_MAX_M - OPERATIONAL_ROUTE_MARGIN_M
                if not (
                    route_min <= navigation_east <= route_max
                    and route_min <= target_north <= route_max
                ):
                    continue
                route_distance = math.hypot(
                    navigation_east - LAUNCH_EAST_M,
                    target_north - LAUNCH_NORTH_M,
                )
                candidates.append(
                    (
                        route_distance,
                        building.building_id,
                        building,
                        min_east,
                        target_north,
                    )
                )
    if not candidates:
        raise HuangpuNativeWorldBuildError(
            "no source building exposes an in-geofence west-facing target edge"
        )

    _distance, _building_id, building, min_east, target_north = min(candidates)
    parent = entity_by_id[building.entity_id]
    targets = tuple(
        SemanticTargetSpec(
            target_id=target_id,
            parent_entity_id=building.entity_id,
            pose=ParentRelativePose(
                frame_id="parent",
                x_m=min_east - parent.pose.east_m - 0.05,
                y_m=target_north - parent.pose.north_m,
                z_m=(
                    building.geometry.base_altitude_m
                    + altitude_offset
                    - parent.pose.up_m
                ),
                qw=1.0,
                qx=0.0,
                qy=0.0,
                qz=0.0,
            ),
            geometry=TargetGeometry(
                shape="box",
                size_x_m=0.08,
                size_y_m=1.0,
                size_z_m=2.0 * TARGET_PANEL_HALF_HEIGHT_M,
            ),
            surface_normal_target=TargetSurfaceNormal(x=-1.0, y=0.0, z=0.0),
            required_sensor_id=SENSOR_ID,
            min_distance_m=8.0,
            max_distance_m=12.0,
            max_view_angle_deg=12.0,
            fov_margin_deg=5.0,
            dwell_time_s=2.0,
            required_evidence=(
                "sensor_frame",
                "image",
                "report",
                "network_upload_or_buffer",
            ),
        )
        for target_id, altitude_offset in zip(
            TARGET_IDS, TARGET_ALTITUDE_OFFSETS_M, strict=True
        )
    )
    return (
        targets,
        building.building_id,
        {
            "target_east_m": min_east - 0.05,
            "target_north_m": target_north,
            "navigation_east_m": min_east - 0.05 - TARGET_STANDOFF_M - 0.12,
            "one_way_horizontal_lower_bound_m": _distance,
        },
    )


def _augment_world(
    *,
    bundle_root: Path,
    base: WorldPackage,
    definition: CityNativeInputLock,
    coverage: SpatialBounds,
    network_measurement: dict[str, object],
) -> tuple[WorldPackage, dict[str, object]]:
    license_ref = base.assets[0].license_file
    _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.raw_osm,
        destination="world/source/raw-city.osm.json",
    )
    _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.road_source_osm,
        destination="world/source/accepted-road-source.osm",
    )
    _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.road_geometry,
        destination="world/layers/accepted-road-v3.json",
    )
    _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.effective_fixtures,
        destination="world/layers/effective-fixtures-v1.json",
    )
    _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.environment_source,
        destination="world/layers/source-ground-cover-v1.json",
    )
    network_path = _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.sumo_network,
        destination="world/sumo/network.net.xml",
    )
    routes_path = _copy_pinned(
        bundle_root=bundle_root,
        pinned=definition.sumo_routes,
        destination="world/sumo/routes.rou.xml",
    )
    _write_bytes(bundle_root / "world/sumo/simulation.sumocfg", _sumo_config())
    _write_bytes(
        bundle_root / "world/sumo/additional.add.xml",
        b'<?xml version="1.0" encoding="UTF-8"?>\n<additional/>\n',
    )

    compiled_sdf = (
        REPOSITORY_ROOT
        / Path(definition.scene_manifest.file.path).parent
        / "gazebo/scene.sdf"
    )
    runtime_sdf = _runtime_sdf(compiled_sdf.read_bytes())
    _write_bytes(bundle_root / "world/gazebo/huangpu-native.sdf", runtime_sdf)

    model_specs = {
        "uav": (1.0, 1.0, 0.35),
        "vehicle": (4.5, 1.8, 1.5),
        "pedestrian": (0.5, 0.5, 1.75),
        "station": (8.0, 8.0, 4.0),
        "pad": (8.0, 8.0, 0.15),
    }
    for name, dimensions in model_specs.items():
        _write_bytes(
            bundle_root / f"world/models/{name}.json", _symbol(name, dimensions)
        )

    extra_assets = [
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.raw-city-osm",
            selector="world/source/raw-city.osm.json",
            role="osm_source",
            media_type="application/json",
            visibility="public",
            source_frame="WGS84",
            license_ref=license_ref,
            source_dataset="accepted-city-source",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.accepted-road-source",
            selector="world/source/accepted-road-source.osm",
            role="osm_source",
            media_type="application/xml",
            visibility="public",
            source_frame="WGS84",
            license_ref=license_ref,
            source_dataset="accepted-road-v3-source",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.layer-road-v3",
            selector="world/layers/accepted-road-v3.json",
            role="layer_tiles",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            license_ref=license_ref,
            source_dataset="accepted-road-v3",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.layer-effective-fixtures",
            selector="world/layers/effective-fixtures-v1.json",
            role="layer_tiles",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            license_ref=license_ref,
            source_dataset="accepted-effective-fixtures-v1",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.layer-source-ground-cover",
            selector="world/layers/source-ground-cover-v1.json",
            role="layer_tiles",
            media_type="application/json",
            visibility="public",
            source_frame="asset_local",
            license_ref=license_ref,
            source_dataset="accepted-source-ground-cover-v1",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.gazebo-city",
            selector="world/gazebo/huangpu-native.sdf",
            role="other",
            media_type="application/vnd.gazebo.sdf+xml",
            visibility="private",
            source_frame="ENU",
            license_ref=license_ref,
            provider_ids=(FLIGHT_ID,),
            source_dataset="compiled-city-sdf+authored-runtime-infrastructure",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.sumo-config",
            selector="world/sumo/simulation.sumocfg",
            role="sumo_config",
            media_type="application/xml",
            visibility="private",
            source_frame="non_spatial",
            license_ref=license_ref,
            provider_ids=(TRAFFIC_ID,),
            source_dataset="city-native-sumo-binding",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.sumo-network",
            selector="world/sumo/network.net.xml",
            role="sumo_network",
            media_type="application/xml",
            visibility="private",
            source_frame="sumo_net",
            license_ref=license_ref,
            provider_ids=(TRAFFIC_ID,),
            source_dataset="accepted-city-native-sumo-network",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.sumo-routes",
            selector="world/sumo/routes.rou.xml",
            role="sumo_routes",
            media_type="application/xml",
            visibility="private",
            source_frame="non_spatial",
            license_ref=license_ref,
            provider_ids=(TRAFFIC_ID,),
            source_dataset="accepted-city-native-sumo-routes",
        ),
        _asset_record(
            bundle_root=bundle_root,
            asset_id="asset.sumo-additional",
            selector="world/sumo/additional.add.xml",
            role="sumo_additional",
            media_type="application/xml",
            visibility="private",
            source_frame="non_spatial",
            license_ref=license_ref,
            provider_ids=(TRAFFIC_ID,),
            source_dataset="city-native-sumo-binding",
        ),
    ]
    for name in model_specs:
        extra_assets.append(
            _asset_record(
                bundle_root=bundle_root,
                asset_id=f"asset.model-{name}",
                selector=f"world/models/{name}.json",
                role="entity_model",
                media_type="application/json",
                visibility="public",
                source_frame="asset_local",
                license_ref=license_ref,
                source_dataset="city-native-runtime-symbols",
            )
        )

    sumo_objects = _sumo_objects(routes_path)
    positions = _route_initial_positions(
        network_path=network_path,
        routes_path=routes_path,
        objects=sumo_objects,
        coverage=coverage,
    )
    entities = list(base.entities)
    entities.extend(
        (
            EntitySpec(
                entity_id=PAD_ENTITY_ID,
                kind="static_asset",
                provider_id=None,
                authority_kind="scenario_static",
                state="static",
                model_asset_id="asset.model-pad",
                pose=_pose(LAUNCH_EAST_M, LAUNCH_NORTH_M),
            ),
            EntitySpec(
                entity_id=STATION_ENTITY_ID,
                kind="static_asset",
                provider_id=None,
                authority_kind="scenario_static",
                state="static",
                model_asset_id="asset.model-station",
                pose=_pose(STATION_EAST_M, STATION_NORTH_M),
            ),
            EntitySpec(
                entity_id=UAV_ID,
                kind="uav",
                provider_id=FLIGHT_ID,
                authority_kind="gazebo_physics",
                state="dynamic",
                model_asset_id="asset.model-uav",
                pose=_pose(
                    LAUNCH_EAST_M,
                    LAUNCH_NORTH_M,
                    UAV_LAUNCH_REFERENCE_UP_M,
                ),
            ),
        )
    )
    sumo_bindings: list[SumoEntityBinding] = []
    for object_id, kind in sumo_objects:
        east_m, north_m = positions[object_id]
        entities.append(
            EntitySpec(
                entity_id=object_id,
                kind="pedestrian" if kind == "person" else "ugv",
                provider_id=TRAFFIC_ID,
                authority_kind="sumo_traffic",
                state="dynamic",
                model_asset_id=(
                    "asset.model-pedestrian"
                    if kind == "person"
                    else "asset.model-vehicle"
                ),
                pose=_pose(east_m, north_m),
            )
        )
        sumo_bindings.append(
            SumoEntityBinding(
                sumo_object_id=object_id,
                entity_id=object_id,
                kind=kind,
            )
        )

    targets, target_building_id, target_route = _targets(base)
    bounds = base.frame.spatial_extent
    if (
        bounds.min_east_m < coverage.min_east_m
        or bounds.max_east_m > coverage.max_east_m
        or bounds.min_north_m < coverage.min_north_m
        or bounds.max_north_m > coverage.max_north_m
    ):
        raise HuangpuNativeWorldBuildError(
            "authored scene extent exceeds the geoid/terrain grid coverage: "
            f"{bounds.model_dump(mode='json')} vs {coverage.model_dump(mode='json')}"
        )
    # The planar extent equals the grid coverage, so every Provider-resolved
    # traffic and flight pose samples inside both scalar grids.
    spatial_extent = SpatialBounds(
        min_east_m=coverage.min_east_m,
        max_east_m=coverage.max_east_m,
        min_north_m=coverage.min_north_m,
        max_north_m=coverage.max_north_m,
        min_up_m=min(bounds.min_up_m, coverage.min_up_m),
        max_up_m=max(bounds.max_up_m, coverage.max_up_m),
        vertical_reference="enu_up",
    )
    ground_half_m = GROUND_SIZE_M / 2.0
    if (
        -ground_half_m > spatial_extent.min_east_m
        or ground_half_m < spatial_extent.max_east_m
        or -ground_half_m > spatial_extent.min_north_m
        or ground_half_m < spatial_extent.max_north_m
    ):
        raise HuangpuNativeWorldBuildError(
            f"{GROUND_SIZE_M} m ground plane does not cover the spatial extent"
        )
    frame = base.frame.model_copy(update={"spatial_extent": spatial_extent})
    package = world_package(
        world_id=base.world_id,
        frame=frame,
        assets=tuple(
            sorted(
                (*base.assets, *extra_assets),
                key=lambda item: item.artifact.artifact_id,
            )
        ),
        provider_requirements=(
            ProviderRequirement(
                provider_id=BUSINESS_ID,
                roles=("mission",),
                required_capability_ids=("business.work-order",),
            ),
            ProviderRequirement(
                provider_id=FLIGHT_ID,
                roles=("motion", "sensor"),
                required_capability_ids=(
                    "camera.rgb",
                    "flight.arm",
                    "gazebo.frames",
                    "gazebo.physics",
                    "observation.capture",
                ),
            ),
            ProviderRequirement(
                provider_id=NETWORK_ID,
                roles=("wireless_network",),
                required_capability_ids=ns3_capabilities(("802.11ax",)),
            ),
            ProviderRequirement(
                provider_id=TRAFFIC_ID,
                roles=("traffic",),
                required_capability_ids=("sumo.frames", "sumo.traffic"),
            ),
        ),
        base_layers=base.base_layers,
        layers=(
            *base.layers,
            PublicLayer(
                layer_id="layer.accepted-road-v3",
                kind="roads",
                asset_id="asset.layer-road-v3",
                visibility="public",
                default_visible=True,
            ),
            PublicLayer(
                layer_id="layer.effective-fixtures",
                kind="entities",
                asset_id="asset.layer-effective-fixtures",
                visibility="public",
                default_visible=True,
            ),
            PublicLayer(
                layer_id="layer.source-ground-cover",
                kind="regions",
                asset_id="asset.layer-source-ground-cover",
                visibility="public",
                default_visible=True,
            ),
        ),
        buildings=base.buildings,
        roads=base.roads,
        regions=(
            RegionSpec(
                region_id=GEOFENCE_ID,
                kind="geofence",
                geometry=RegionGeometry(
                    footprint_enu_m=(
                        {
                            "east_m": OPERATIONAL_GEOFENCE_MIN_M,
                            "north_m": OPERATIONAL_GEOFENCE_MIN_M,
                        },
                        {
                            "east_m": OPERATIONAL_GEOFENCE_MAX_M,
                            "north_m": OPERATIONAL_GEOFENCE_MIN_M,
                        },
                        {
                            "east_m": OPERATIONAL_GEOFENCE_MAX_M,
                            "north_m": OPERATIONAL_GEOFENCE_MAX_M,
                        },
                        {
                            "east_m": OPERATIONAL_GEOFENCE_MIN_M,
                            "north_m": OPERATIONAL_GEOFENCE_MAX_M,
                        },
                    ),
                    min_altitude_m=0.0,
                    max_altitude_m=180.0,
                    height_reference="enu_up",
                ),
                communications_shadow_attenuation_db=None,
            ),
            RegionSpec(
                region_id=NO_FLY_ID,
                kind="no_fly",
                geometry=RegionGeometry(
                    footprint_enu_m=(
                        {"east_m": 300.0, "north_m": 250.0},
                        {"east_m": 350.0, "north_m": 250.0},
                        {"east_m": 350.0, "north_m": 300.0},
                        {"east_m": 300.0, "north_m": 300.0},
                    ),
                    min_altitude_m=0.0,
                    max_altitude_m=180.0,
                    height_reference="enu_up",
                ),
                communications_shadow_attenuation_db=None,
            ),
        ),
        launch_sites=(
            LaunchSiteSpec(
                launch_site_id=LAUNCH_ID,
                primary_uav_entity_id=UAV_ID,
                allowed_uav_entity_ids=(UAV_ID,),
                pose=_pose(
                    LAUNCH_EAST_M,
                    LAUNCH_NORTH_M,
                    UAV_LAUNCH_REFERENCE_UP_M,
                ),
                pad_radius_m=4.0,
            ),
        ),
        entities=tuple(entities),
        sensors=(
            SensorSpec(
                sensor_id=SENSOR_ID,
                provider_id=FLIGHT_ID,
                parent_entity_id=UAV_ID,
                kind="camera",
                pose=ParentRelativePose(
                    frame_id="parent",
                    x_m=0.12,
                    y_m=0.03,
                    z_m=0.242,
                    qw=1.0,
                    qx=0.0,
                    qy=0.0,
                    qz=0.0,
                ),
                horizontal_fov_deg=80.0,
                vertical_fov_deg=60.0,
                resolution_width_px=640,
                resolution_height_px=480,
            ),
        ),
        semantic_targets=targets,
        weather=(
            WeatherSample(
                sample_id=WEATHER_ID,
                mode="deterministic_constant",
                wind=WindVector(east_mps=0.0, north_mps=0.0, up_mps=0.0),
                visibility_m=10_000.0,
                precipitation="none",
                precipitation_rate_mm_per_h=0.0,
                temperature_c=20.0,
                pressure_pa=101_325.0,
            ),
        ),
        engine_frame_bindings=(
            EngineFrameBinding(
                binding_id="gazebo.enu",
                provider_id=FLIGHT_ID,
                engine="gazebo",
                scene_asset_id="asset.gazebo-city",
                source_frame_id="ENU",
                target_frame_id=f"{UAV_ID}/body",
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
                binding_id="sumo.enu",
                provider_id=TRAFFIC_ID,
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
        sumo=SumoConfiguration(
            provider_id=TRAFFIC_ID,
            config_asset_id="asset.sumo-config",
            network_asset_id="asset.sumo-network",
            routes_asset_id="asset.sumo-routes",
            additional_asset_id="asset.sumo-additional",
            frame_binding_id="sumo.enu",
            object_bindings=tuple(sumo_bindings),
        ),
        network=NetworkConfiguration(
            provider_id=NETWORK_ID,
            radio_profiles=(
                WirelessRadioProfile(
                    radio_profile_id="radio.city-research",
                    provider_id=NETWORK_ID,
                    wifi_standard="802.11ax",
                    frequency_ghz=5.775,
                    channel_width_mhz=80.0,
                    tx_power_dbm=23.0,
                    rx_sensitivity_dbm=-92.0,
                ),
            ),
            node_bindings=(
                NetworkNodeBinding(
                    node_id="node.operations",
                    # The operations radio is mounted at the launch pad so
                    # the deterministic reference participant can transmit
                    # only after the native landing witness.  The separate
                    # station entity remains physical scene infrastructure.
                    entity_id=PAD_ENTITY_ID,
                    endpoint_id="endpoint.operations",
                    radio_profile_id="radio.city-research",
                ),
                NetworkNodeBinding(
                    node_id="node.uav",
                    entity_id=UAV_ID,
                    endpoint_id="endpoint.uav",
                    radio_profile_id="radio.city-research",
                ),
            ),
            links=(
                NetworkLinkBinding(
                    link_id="link.uav-operations",
                    source_node_id="node.uav",
                    destination_node_id="node.operations",
                    data_rate_bps=6_000_000,
                    propagation_delay_ns=20_000_000,
                ),
            ),
        ),
        mission_requirements=(
            MissionRequirement(
                requirement_id="mission.takeoff",
                kind="takeoff",
                dependencies=(),
                launch_site_id=LAUNCH_ID,
                target_id=None,
                expected_public_asset_id=None,
            ),
            *(
                MissionRequirement(
                    requirement_id=f"mission.observe-city-facade-{index}",
                    kind="observation",
                    dependencies=(
                        ("mission.takeoff",)
                        if index == 1
                        else (f"mission.observe-city-facade-{index - 1}",)
                    ),
                    launch_site_id=None,
                    target_id=target_id,
                    expected_public_asset_id=None,
                )
                for index, target_id in enumerate(TARGET_IDS, start=1)
            ),
            MissionRequirement(
                requirement_id="mission.upload",
                kind="upload_or_buffer",
                dependencies=("mission.observe-city-facade-2",),
                launch_site_id=None,
                target_id=None,
                expected_public_asset_id="mission.inspection-report",
            ),
            MissionRequirement(
                requirement_id="mission.return",
                kind="return_to_launch",
                dependencies=("mission.upload",),
                launch_site_id=LAUNCH_ID,
                target_id=None,
                expected_public_asset_id=None,
            ),
            MissionRequirement(
                requirement_id="mission.land",
                kind="land",
                dependencies=("mission.return",),
                launch_site_id=LAUNCH_ID,
                target_id=None,
                expected_public_asset_id=None,
            ),
        ),
        expected_public_assets=(
            ExpectedPublicAsset(
                asset_id="mission.inspection-report",
                kind="mission_output",
                producer_requirement_id="mission.upload",
                media_type="application/json",
                units=(UnitDeclaration(quantity="file_size", unit="B"),),
                precision=(
                    PrecisionMetadata(
                        quantity="file_size",
                        kind="absolute_tolerance",
                        unit="B",
                        exact_bytes=None,
                        absolute_tolerance=1.0,
                    ),
                ),
            ),
        ),
    )
    package_path = bundle_root / "world/package.json"
    _write_json(package_path, package.model_dump(mode="json"))
    provenance = {
        "schema_version": BUILD_REPORT_SCHEMA,
        "world_id": package.world_id,
        "world_digest": package.world_digest,
        "asset_digest": package.asset_digest,
        "compiled_scene_sdf_sha256": sha256_file(compiled_sdf),
        "runtime_city_sdf_sha256": _sha256_bytes(runtime_sdf),
        "accepted_sumo_network_sha256": definition.sumo_network.file.sha256,
        "accepted_sumo_routes_sha256": definition.sumo_routes.file.sha256,
        "sumo_object_count": len(sumo_objects),
        "target_building_id": target_building_id,
        "target_ids": list(TARGET_IDS),
        "target_route": target_route,
        "geofence_id": GEOFENCE_ID,
        "no_fly_id": NO_FLY_ID,
        "ground_size_m": GROUND_SIZE_M,
        "sumo_network_measurement": network_measurement,
        "spatial_extent": spatial_extent.model_dump(mode="json"),
        "launch_model_name": LAUNCH_ID,
        "station_model_name": STATION_ENTITY_ID,
        "launch_site": {
            "east_m": LAUNCH_EAST_M,
            "north_m": LAUNCH_NORTH_M,
            "source_building_clearance_m": 30.590377706012156,
        },
    }
    _write_json(bundle_root / "world/native-authoring-provenance.json", provenance)
    return package, provenance


def build(
    *,
    input_lock: Path,
    authoring_output: Path,
    world_output: Path,
    report_path: Path,
    world_id: str,
) -> dict[str, object]:
    definition = load_city_native_input_lock(input_lock)
    source_only_definition = definition.model_copy(
        update={"world": None, "execution": None}
    )
    source_report = assess_city_native_registration(
        REPOSITORY_ROOT, source_only_definition
    )
    source_blockers = [
        item.code
        for item in source_report.prerequisites
        if item.code.startswith("source.") and item.status != "ready"
    ]
    if source_blockers:
        raise HuangpuNativeWorldBuildError(
            f"accepted city sources failed verification: {source_blockers}"
        )
    _require_new_path(authoring_output, "authoring output")
    _require_new_path(world_output, "world output")
    _require_new_path(report_path, "build report")
    authoring_output.mkdir(parents=True, exist_ok=False)

    scene_root = REPOSITORY_ROOT / Path(definition.scene_manifest.file.path).parent
    manifest = _read_json(scene_root / "manifest.json", "compiled scene manifest")
    network_source = REPOSITORY_ROOT / definition.sumo_network.file.path
    if sha256_file(network_source) != definition.sumo_network.file.sha256:
        raise HuangpuNativeWorldBuildError(
            f"pinned SUMO network drifted: {definition.sumo_network.file.path}"
        )
    network_measurement = _measure_sumo_network(network_source, _origin(manifest))
    coverage = _provider_coverage(network_measurement)
    catalog = _catalog(
        definition=definition,
        authoring_root=authoring_output,
        manifest=manifest,
        coverage=coverage,
    )
    base_catalog_path = _write_json(
        authoring_output / "base-catalog.json",
        catalog.model_dump(mode="json"),
        pretty=True,
    )
    merge = merge_building_render_fragment(
        fragment_path=REPOSITORY_ROOT / definition.building_render_catalog.file.path,
        scene_root=scene_root,
        catalog_path=base_catalog_path,
        output_catalog_path=authoring_output / "merged-catalog.json",
    )

    world_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{world_output.name}.staging-", dir=world_output.parent
    ) as temporary:
        bundle_root = Path(temporary) / "bundle"
        authored = author_urban_world_package(
            UrbanWorldAuthoringRequest(
                scene_root=scene_root,
                output_root=bundle_root,
                world_id=world_id,
                origin=_origin(manifest),
                catalog_path=merge.output_catalog_path,
            )
        )
        verified_render_count = verify_staged_render_bytes(
            bundle_root, merge.validated.staged
        )
        package, provenance = _augment_world(
            bundle_root=bundle_root,
            base=authored.world_package,
            definition=definition,
            coverage=coverage,
            network_measurement=network_measurement,
        )
        os.replace(bundle_root, world_output)

    package_path = world_output / "world/package.json"
    report: dict[str, object] = {
        "schema_version": BUILD_REPORT_SCHEMA,
        "status": "world-authored-execution-unbound",
        "world": {
            "bundle_root": _repository_relative(world_output),
            "package_path": _repository_relative(package_path),
            "package_sha256": sha256_file(package_path),
            "world_id": package.world_id,
            "world_digest": package.world_digest,
            "asset_digest": package.asset_digest,
            "asset_count": len(package.assets),
            "building_count": len(package.buildings),
            "road_count": len(package.roads),
            "entity_count": len(package.entities),
            "sumo_object_count": len(package.sumo.object_bindings)
            if package.sumo is not None
            else 0,
            "provider_ids": [
                item.provider_id for item in package.provider_requirements
            ],
        },
        "authoring": {
            "catalog_path": _repository_relative(merge.output_catalog_path),
            "catalog_sha256": merge.output_catalog_sha256,
            "building_render_count": verified_render_count,
            "building_render_bytes": sum(
                item.byte_size for item in merge.validated.staged
            ),
            "road_width_count": len(catalog.road_width_m),
            "flat_datum_source": "compiled scene origin and flat-scene model",
            "weather_source": "authored deterministic clear baseline; not observed weather",
        },
        "authorization": {
            "original_source_and_license": "unknown in repository artifacts",
            "current_use": "user-declared mentor authorization for internal research",
            "ownership_claimed": False,
            "public_redistribution_authorized": False,
        },
        "runtime_bindings": provenance,
        "remaining_external_or_implementation_prerequisites": [
            "No registered weather Provider applies changing wind or precipitation to native physics.",
            "No registered Airspace Provider owns permission, revocation, and enforcement state.",
            "Physical logistics completion and its independent verifier are not implemented.",
            "A one-case Suite, city Provider configs, digest-pinned workload image lock, and independent verifier must be bound before docker_reference execution.",
            "The existing I3 reference chain must be released before a formal city run starts.",
        ],
    }
    _write_json(report_path, report, pretty=True)
    return report


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build(
            input_lock=Path(args.input_lock),
            authoring_output=Path(args.authoring_output),
            world_output=Path(args.world_output),
            report_path=Path(args.report),
            world_id=args.world_id,
        )
    except (HuangpuNativeWorldBuildError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["world"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
