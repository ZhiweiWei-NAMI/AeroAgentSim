#!/usr/bin/env python3
"""Build independent Shanghai urban UAV recovery release inputs.

The default engineering profile records a short, unscored real-provider run;
formal evidence requirements remain available with --profile formal. Neither
profile creates synthetic map geometry or runtime-success artifacts.
"""
from __future__ import annotations


import argparse
import hashlib
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from shapely.geometry import GeometryCollection, LineString, MultiLineString, box
from shapely import __version__ as SHAPELY_VERSION

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.providers.registry import builtin_provider_registry

from aero_bench.config.models import AgentSpec, EnvironmentSpec, RuntimeImage, TaskSpec  # noqa: E402
from aero_bench.config.resolver import resolve_suite  # noqa: E402
from aero_bench.providers.ns3.config import Ns3Config, ns3_capabilities  # noqa: E402
from aero_bench.providers.ns3.provider import NetworkMailboxObservationPayload  # noqa: E402
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig  # noqa: E402
from aero_bench.providers.sumo.config import SumoConfig  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.tasks.urban_recovery_demo.contracts import (  # noqa: E402
    DURATION_NS,
    PACKAGE_ID,
    PHYSICS_STEP_NS,
    STEP_NS,
    DemoTaskPackage,
)
from aero_bench.tasks.urban_recovery_demo.integration import (  # noqa: E402
    UrbanRecoveryTaskPackageResolver,
)
from aero_bench.tasks.urban_recovery_demo.goals import urban_goals  # noqa: E402
from aero_bench.tasks.urban_recovery_demo.participant import UrbanParticipantConfig  # noqa: E402
from aero_bench.tasks.urban_recovery_demo.provenance import URBAN_PROVENANCE_ASSETS  # noqa: E402
from aero_bench.tasks.urban_recovery_demo.verifier import UrbanRecoveryVerifierConfig  # noqa: E402
from aero_bench.world.contracts import (  # noqa: E402
    ArtifactSelector,
    AssetAudience,
    AssetProvenance,
    AssetRecord,
    BuildingGeometry,
    BuildingSpec,
    EngineFrameBinding,
    EnuPoint,
    EntitySpec,
    LaunchSiteSpec,
    MissionRequirement,
    NetworkConfiguration,
    NetworkLinkBinding,
    NetworkNodeBinding,
    ParentRelativePose,
    PrecisionMetadata,
    PublicLayer,
    ProviderRequirement,
    RegionGeometry,
    RegionSpec,
    RigidOffset,
    RoadSpec,
    SumoConfiguration,
    SumoEntityBinding,
    UnitDeclaration,
    VerticalDatumBinding,
    WeatherSample,
    WindVector,
    WirelessRadioProfile,
    WorldFrame,
    WorldPackage,
    WorldPose,
    world_package,
)
from aero_bench.world.frames import LocalFrameOrigin  # noqa: E402

SOURCE_URI = "https://github.com/ZhiweiWei-NAMI/AERO_BENCH"
WORLD_ID = "world.shanghai.uav-recovery-demo-v1"
TASK_ID = "urban.uav.recovery.demo.v1"
VERIFIER_ID = "urban.recovery.verifier"
FLIGHT_ID = "flight"
NETWORK_ID = "network"
TRAFFIC_ID = "traffic"
SHANGHAI_LAT = 31.2304
SHANGHAI_LON = 121.4737
ORIGIN_ELLIPSOID_M = 50.0
ORIGIN_AMSL_M = 20.0
ENGINEERING_GROUND_MODEL_NAME = "ground_plane"
ENGINEERING_GROUND_LINK_NAME = "ground_link"
ENGINEERING_GROUND_COLLISION_NAME = "ground_collision"
ENGINEERING_PHYSICS_NAME = "deterministic_4ms"

REQUIRED_CAPABILITIES = (
    "flight.arm", "flight.command", "flight.disarm",
    "flight.goto", "flight.hold", "flight.land", "flight.takeoff",
    "gazebo.frames", "gazebo.physics", "network.agent-mailbox",
    "network.delivery", "network.wifi-scene-mobility",
    "sumo.frames", "sumo.signals", "sumo.traffic",
)
REQUIRED_TOOLS = (
    "flight.arm", "flight.disarm", "flight.goto", "flight.hold",
    "flight.land", "flight.takeoff", "network.send",
)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def write_bytes(root: Path, relative: str, value: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def write_json(root: Path, relative: str, value: object) -> Path:
    return write_bytes(root, relative, canonical_json_bytes(value) + b"\n")


def write_yaml(root: Path, relative: str, value: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def copy_asset(root: Path, source: Path, relative: str) -> Path:
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError(f"required authoritative asset is missing or empty: {source}")
    return write_bytes(root, relative, source.read_bytes())


def ref(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": digest_file(path)}


def artifact_selector(root: Path, asset_id: str, path: Path) -> ArtifactSelector:
    return ArtifactSelector(
        artifact_id=asset_id,
        selector=path.relative_to(root).as_posix(),
        sha256=digest_file(path),
    )


def _precision(size: int, *, height_tolerance_m: float | None = None) -> tuple[UnitDeclaration, ...]:
    units: list[UnitDeclaration] = [UnitDeclaration(quantity="file_size", unit="B")]
    if height_tolerance_m is not None:
        units.append(UnitDeclaration(quantity="height", unit="m"))
    return tuple(units)


def _asset(
    root: Path,
    *,
    asset_id: str,
    relative: str,
    role: str,
    media_type: str,
    visibility: str,
    frame: str,
    audiences: tuple[AssetAudience, ...],
    license_ref: ArtifactSelector,
    height_tolerance_m: float | None = None,
) -> AssetRecord:
    path = root / relative
    size = path.stat().st_size
    return AssetRecord(
        artifact=artifact_selector(root, asset_id, path),
        asset_role=role,
        byte_size=size,
        media_type=media_type,
        visibility=visibility,
        audiences=audiences,
        units=_precision(size, height_tolerance_m=height_tolerance_m),
        source_frame=frame,
        precision=(
            PrecisionMetadata(quantity="file_size", kind="exact_bytes", unit="B", exact_bytes=size, absolute_tolerance=None),
            *(() if height_tolerance_m is None else (PrecisionMetadata(quantity="height", kind="absolute_tolerance", unit="m", exact_bytes=None, absolute_tolerance=height_tolerance_m),)),
        ),
        provenance=AssetProvenance(
            source_kind="bundled_offline",
            recorded_by="aero-bench",
            source_dataset="shanghai-hongqiao-osm2world-recovery-v1",
            source_version="2026.09.07",
            runtime_download=False,
        ),
        license_id="LicenseRef-AERO-BENCH-Bundled",
        license_file=license_ref,
    )


def _copy_mesh_pack(root: Path, pack: Path, effective: Path) -> tuple[Path, list[Path]]:
    manifest = pack / "manifest.json"
    if not manifest.is_file():
        raise ValueError("OSM2World pack must contain manifest.json")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    required = {"schema_version", "source", "generator", "projection", "extent", "original_mesh_count", "batches", "objects", "textures"}
    if set(document) != required or document["schema_version"] != "aero-bench.osm2world-mesh-pack/v1":
        raise ValueError("mesh pack is not the official OSM2World v1 pack")
    source = document["source"]
    if source.get("sha256") != digest_file(effective) or source.get("size_bytes") != effective.stat().st_size:
        raise ValueError("OSM2World pack is not generated from the clipped effective OSM")
    projection = document["projection"]
    if projection.get("name") != "MetricMapProjection" or projection.get("axes") != "east-up-south" or projection.get("origin") != {"latitude_deg": SHANGHAI_LAT, "longitude_deg": SHANGHAI_LON}:
        raise ValueError("OSM2World pack does not use the declared Shanghai projection")
    assets_dir = pack / "assets"
    if not assets_dir.is_dir():
        raise ValueError("OSM2World pack has no content-addressed geometry assets")
    descriptors: dict[str, dict[str, object]] = {}
    for batch in document["batches"]:
        file_descriptor = batch.get("file") if isinstance(batch, dict) else None
        if not isinstance(file_descriptor, dict) or set(file_descriptor) != {"sha256", "size_bytes"}:
            raise ValueError("OSM2World batch file descriptor is malformed")
        descriptors[str(file_descriptor["sha256"])] = file_descriptor
    for descriptor in document["textures"].values():
        if not isinstance(descriptor, dict) or set(descriptor) != {"sha256", "size_bytes"}:
            raise ValueError("OSM2World texture descriptor is malformed")
        descriptors[str(descriptor["sha256"])] = descriptor
    copied: list[Path] = [write_bytes(root, "world/osm2world/manifest.json", manifest.read_bytes())]
    for digest, descriptor in sorted(descriptors.items()):
        source = assets_dir / digest
        if not source.is_file() or digest_file(source) != digest or source.stat().st_size != descriptor["size_bytes"]:
            raise ValueError(f"OSM2World content-addressed asset failed verification: {digest}")
        copied.append(copy_asset(root, source, f"world/osm2world/assets/{digest}"))
    lock = pack / "manifest.lock.json"
    if not lock.is_file():
        raise ValueError("OSM2World pack must include manifest.lock.json")
    lock_document = json.loads(lock.read_text(encoding="utf-8"))
    if lock_document.get("sha256") != digest_file(manifest) or lock_document.get("size_bytes") != manifest.stat().st_size:
        raise ValueError("OSM2World manifest lock does not bind manifest.json")
    copied.append(write_bytes(root, "world/osm2world/manifest.lock.json", lock.read_bytes()))
    return copied[0], copied


def _load_scene(scene_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((scene_dir / "manifest.json").read_text(encoding="utf-8"))
    objects = json.loads((scene_dir / "metadata/objects.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "aero-bench.urban-scene-compiler/v1":
        raise ValueError("scene compiler output has an unsupported schema")
    if manifest.get("crop", {}).get("enu_bounds_m") != {"min_east_m": -500.0, "max_east_m": 500.0, "min_north_m": -500.0, "max_north_m": 500.0}:
        raise ValueError("scene compiler output is not the exact 1 km crop")
    origin = manifest.get("origin")
    if not isinstance(origin, dict) or origin.get("latitude_deg") != SHANGHAI_LAT or origin.get("longitude_deg") != SHANGHAI_LON or origin.get("ellipsoid_height_m") != ORIGIN_ELLIPSOID_M or origin.get("amsl_m") != ORIGIN_AMSL_M:
        raise ValueError("scene compiler output does not use the declared Shanghai vertical origin")
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("sha256") != digest_file(ROOT / "frontend/public/osm2world/shanghai-hongqiao.osm.json"):
        raise ValueError("scene compiler output is not bound to the authoritative raw Shanghai OSM source")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("scene compiler manifest has no output inventory")
    output_map = {item.get("path"): item for item in outputs if isinstance(item, dict)}
    for relative in ("gazebo/scene.sdf", "metadata/objects.json", "osm/effective.osm.json"):
        path = scene_dir / relative
        record = output_map.get(relative)
        if not path.is_file() or not isinstance(record, dict) or record.get("sha256") != digest_file(path) or record.get("byte_size") != path.stat().st_size:
            raise ValueError(f"scene compiler output failed manifest binding: {relative}")
    if objects.get("schema_version") != "aero-bench.urban-scene-objects/v1" or objects.get("coordinate_frame") != "ENU":
        raise ValueError("scene compiler object inventory is not an ENU OSM closure")
    return manifest, objects


def _flat_scalar_grid(
    path: Path,
    *,
    label: str,
    extent: dict[str, float],
) -> float:
    document = json.loads(path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version",
        "frame_id",
        "east_axis_m",
        "north_axis_m",
        "values_m",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_fields
        or document.get("schema_version") != "aero-bench.scalar-grid/v1"
        or document.get("frame_id") != "ENU"
    ):
        raise ValueError(f"{label} must be an exact ENU scalar-grid/v1 model")

    def axis(value: object, *, axis_label: str) -> tuple[float, ...]:
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError(f"{axis_label} must contain at least two coordinates")
        coordinates: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{axis_label} coordinates must be finite numbers")
            coordinate = float(item)
            if not math.isfinite(coordinate):
                raise ValueError(f"{axis_label} coordinates must be finite numbers")
            coordinates.append(coordinate)
        if any(left >= right for left, right in zip(coordinates, coordinates[1:])):
            raise ValueError(f"{axis_label} coordinates must be strictly increasing")
        return tuple(coordinates)

    east_axis = axis(document["east_axis_m"], axis_label=f"{label}.east_axis_m")
    north_axis = axis(document["north_axis_m"], axis_label=f"{label}.north_axis_m")
    if east_axis[0] != extent["min_east_m"] or east_axis[-1] != extent["max_east_m"]:
        raise ValueError(f"{label} east axis does not cover the declared scene extent")
    if (
        north_axis[0] != extent["min_north_m"]
        or north_axis[-1] != extent["max_north_m"]
    ):
        raise ValueError(f"{label} north axis does not cover the declared scene extent")

    values = document["values_m"]
    if not isinstance(values, list) or len(values) != len(north_axis):
        raise ValueError(f"{label}.values_m row count must match north_axis_m")
    parsed_values: list[float] = []
    for row in values:
        if not isinstance(row, list) or len(row) != len(east_axis):
            raise ValueError(f"{label}.values_m columns must match east_axis_m")
        for item in row:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{label}.values_m must contain finite numbers")
            number = float(item)
            if not math.isfinite(number):
                raise ValueError(f"{label}.values_m must contain finite numbers")
            parsed_values.append(number)
    if not parsed_values or any(value != parsed_values[0] for value in parsed_values[1:]):
        raise ValueError(
            f"{label} is not flat and cannot be compiled as an engineering ground plane"
        )
    return parsed_values[0]


def _derive_engineering_ground_scene(
    *,
    source: Path,
    destination: Path,
    terrain: Path,
    geoid: Path,
    scene_manifest: dict[str, Any],
    physics_step_ns: int,
) -> dict[str, Any]:
    if (
        not isinstance(physics_step_ns, int)
        or isinstance(physics_step_ns, bool)
        or physics_step_ns <= 0
    ):
        raise ValueError("engineering physics_step_ns must be a positive integer")
    physics_step_s = Decimal(physics_step_ns) / Decimal(1_000_000_000)
    physics_step_text = format(physics_step_s, "f")
    raw_bounds = scene_manifest.get("crop", {}).get("enu_bounds_m")
    expected_bound_fields = {
        "min_east_m",
        "max_east_m",
        "min_north_m",
        "max_north_m",
    }
    if not isinstance(raw_bounds, dict) or set(raw_bounds) != expected_bound_fields:
        raise ValueError("engineering ground requires exact scene ENU bounds")
    extent: dict[str, float] = {}
    for field in sorted(expected_bound_fields):
        value = raw_bounds[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("engineering ground scene bounds must be finite numbers")
        extent[field] = float(value)
        if not math.isfinite(extent[field]):
            raise ValueError("engineering ground scene bounds must be finite numbers")
    if (
        extent["min_east_m"] >= extent["max_east_m"]
        or extent["min_north_m"] >= extent["max_north_m"]
    ):
        raise ValueError("engineering ground scene bounds are inverted")

    terrain_amsl_m = _flat_scalar_grid(
        terrain,
        label="terrain model",
        extent=extent,
    )
    geoid_separation_m = _flat_scalar_grid(
        geoid,
        label="geoid model",
        extent=extent,
    )
    origin = scene_manifest.get("origin")
    if not isinstance(origin, dict):
        raise ValueError("engineering ground requires the scene frame origin")
    required_origin = {
        "latitude_deg": SHANGHAI_LAT,
        "longitude_deg": SHANGHAI_LON,
        "ellipsoid_height_m": ORIGIN_ELLIPSOID_M,
        "amsl_m": ORIGIN_AMSL_M,
    }
    if any(origin.get(field) != value for field, value in required_origin.items()):
        raise ValueError("engineering ground frame origin differs from the Shanghai declaration")
    if not math.isclose(
        float(origin["ellipsoid_height_m"]) - float(origin["amsl_m"]),
        geoid_separation_m,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("flat geoid model differs from the declared vertical origin")
    elevation_enu_up_m = (
        terrain_amsl_m
        + geoid_separation_m
        - float(origin["ellipsoid_height_m"])
    )
    if not math.isclose(
        elevation_enu_up_m,
        terrain_amsl_m - float(origin["amsl_m"]),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("terrain/geoid models disagree on engineering ground elevation")
    if abs(elevation_enu_up_m) < 1e-12:
        elevation_enu_up_m = 0.0

    source_bytes = source.read_bytes()
    try:
        source_text = source_bytes.decode("utf-8", errors="strict")
        source_root = ET.fromstring(source_text)
    except (UnicodeDecodeError, ET.ParseError) as error:
        raise ValueError("engineering ground source is not valid UTF-8 SDF") from error
    if source_root.tag.rsplit("}", 1)[-1] != "sdf":
        raise ValueError("engineering ground source must have an SDF root")
    worlds = [element for element in source_root if element.tag.rsplit("}", 1)[-1] == "world"]
    if len(worlds) != 1:
        raise ValueError("engineering ground source must declare exactly one world")
    source_world = worlds[0]
    source_models = [
        element
        for element in source_world
        if element.tag.rsplit("}", 1)[-1] == "model"
    ]
    expected_buildings = scene_manifest.get("selection", {}).get("building_count")
    if (
        not isinstance(expected_buildings, int)
        or isinstance(expected_buildings, bool)
        or len(source_models) != expected_buildings
        or any(
            not str(model.attrib.get("name", "")).startswith("building_way_")
            for model in source_models
        )
    ):
        raise ValueError(
            "engineering ground source model inventory differs from compiled buildings"
        )
    source_model_names = tuple(str(model.attrib.get("name", "")) for model in source_models)
    if len(source_model_names) != len(set(source_model_names)) or any(
        name in {"ground", ENGINEERING_GROUND_MODEL_NAME} for name in source_model_names
    ):
        raise ValueError("engineering ground source model identities are invalid")

    spherical = [
        element
        for element in source_world
        if element.tag.rsplit("}", 1)[-1] == "spherical_coordinates"
    ]
    if len(spherical) != 1:
        raise ValueError("engineering ground source omits its spherical frame declaration")
    spherical_values = {
        element.tag.rsplit("}", 1)[-1]: (element.text or "").strip()
        for element in spherical[0]
    }
    try:
        frame_matches = (
            spherical_values.get("surface_model") == "EARTH_WGS84"
            and spherical_values.get("world_frame_orientation") == "ENU"
            and float(spherical_values["latitude_deg"]) == float(origin["latitude_deg"])
            and float(spherical_values["longitude_deg"])
            == float(origin["longitude_deg"])
            and float(spherical_values["elevation"]) == float(origin["amsl_m"])
        )
    except (KeyError, ValueError) as error:
        raise ValueError("engineering ground source frame declaration is malformed") from error
    if not frame_matches:
        raise ValueError("engineering ground source frame differs from the supplied models")

    source_physics = [
        element
        for element in source_world
        if element.tag.rsplit("}", 1)[-1] == "physics"
    ]
    physics_declaration = "generated"
    generated_physics = ""
    if source_physics:
        if len(source_physics) != 1:
            raise ValueError("engineering scene has ambiguous source physics declarations")
        source_max_steps = [
            element
            for element in source_physics[0]
            if element.tag.rsplit("}", 1)[-1] == "max_step_size"
        ]
        if len(source_max_steps) != 1:
            raise ValueError("engineering source physics must declare one max_step_size")
        try:
            source_step_s = Decimal((source_max_steps[0].text or "").strip())
        except Exception as error:
            raise ValueError("engineering source max_step_size is malformed") from error
        if not source_step_s.is_finite() or source_step_s != physics_step_s:
            raise ValueError(
                "engineering source max_step_size conflicts with PHYSICS_STEP_NS"
            )
        physics_declaration = "source"
    else:
        generated_physics = (
            f'    <physics name="{ENGINEERING_PHYSICS_NAME}" type="ignored">'
            f"<max_step_size>{physics_step_text}</max_step_size>"
            "<real_time_factor>0</real_time_factor></physics>\n"
        )

    east_size_m = extent["max_east_m"] - extent["min_east_m"]
    north_size_m = extent["max_north_m"] - extent["min_north_m"]
    east_center_m = (extent["min_east_m"] + extent["max_east_m"]) / 2.0
    north_center_m = (extent["min_north_m"] + extent["max_north_m"]) / 2.0

    def scalar(value: float) -> str:
        return format(value, ".17g")

    generated_model = (
        f'    <model name="{ENGINEERING_GROUND_MODEL_NAME}">\n'
        "      <static>true</static>\n"
        f"      <pose>{scalar(east_center_m)} {scalar(north_center_m)} "
        f"{scalar(elevation_enu_up_m)} 0 0 0</pose>\n"
        f'      <link name="{ENGINEERING_GROUND_LINK_NAME}">\n'
        f'        <collision name="{ENGINEERING_GROUND_COLLISION_NAME}">\n'
        "          <geometry><plane>\n"
        "            <normal>0 0 1</normal>\n"
        f"            <size>{scalar(east_size_m)} {scalar(north_size_m)}</size>\n"
        "          </plane></geometry>\n"
        "        </collision>\n"
        "      </link>\n"
        "    </model>\n"
    )
    closing = "  </world>\n</sdf>\n"
    if not source_text.endswith(closing) or source_text.count(closing) != 1:
        raise ValueError("engineering ground source has an unexpected SDF closing structure")
    generated_text = (
        source_text[: -len(closing)]
        + generated_physics
        + generated_model
        + closing
    )
    try:
        generated_root = ET.fromstring(generated_text)
    except ET.ParseError as error:
        raise ValueError("generated engineering ground SDF is malformed") from error
    generated_world = next(
        element
        for element in generated_root
        if element.tag.rsplit("}", 1)[-1] == "world"
    )
    generated_models = [
        element
        for element in generated_world
        if element.tag.rsplit("}", 1)[-1] == "model"
    ]

    def model_content(element: ET.Element) -> str:
        tail = element.tail
        element.tail = None
        try:
            return ET.tostring(element, encoding="unicode")
        finally:
            element.tail = tail

    if (
        len(generated_models) != len(source_models) + 1
        or tuple(model_content(model) for model in generated_models[:-1])
        != tuple(model_content(model) for model in source_models)
        or generated_models[-1].attrib != {"name": ENGINEERING_GROUND_MODEL_NAME}
    ):
        raise ValueError("engineering ground derivation changed source building geometry")
    generated_physics_elements = [
        element
        for element in generated_world
        if element.tag.rsplit("}", 1)[-1] == "physics"
    ]
    if len(generated_physics_elements) != 1:
        raise ValueError("engineering scene does not contain one physics declaration")
    generated_max_steps = [
        element
        for element in generated_physics_elements[0]
        if element.tag.rsplit("}", 1)[-1] == "max_step_size"
    ]
    if (
        len(generated_max_steps) != 1
        or Decimal((generated_max_steps[0].text or "").strip()) != physics_step_s
    ):
        raise ValueError("generated engineering physics step is inconsistent")
    generated_bytes = generated_text.encode("utf-8")
    write_bytes(destination.parent, destination.name, generated_bytes)

    return {
        "schema_version": "aero-bench.engineering-ground/v1",
        "execution_scope": "executor_validation",
        "source_scene": {
            "sha256": digest_bytes(source_bytes),
            "byte_size": len(source_bytes),
        },
        "generated_scene": {
            "path": "world/gazebo/scene.sdf",
            "sha256": digest_bytes(generated_bytes),
            "byte_size": len(generated_bytes),
        },
        "terrain_model": {
            "sha256": digest_file(terrain),
            "constant_amsl_m": terrain_amsl_m,
        },
        "geoid_model": {
            "sha256": digest_file(geoid),
            "constant_separation_m": geoid_separation_m,
        },
        "frame_origin": {
            "latitude_deg": float(origin["latitude_deg"]),
            "longitude_deg": float(origin["longitude_deg"]),
            "ellipsoid_height_m": float(origin["ellipsoid_height_m"]),
            "amsl_m": float(origin["amsl_m"]),
        },
        "physics": {
            "declaration": physics_declaration,
            "name": (
                generated_physics_elements[0].attrib.get("name")
                if physics_declaration == "source"
                else ENGINEERING_PHYSICS_NAME
            ),
            "type": (
                generated_physics_elements[0].attrib.get("type")
                if physics_declaration == "source"
                else "ignored"
            ),
            "physics_step_ns": physics_step_ns,
            "max_step_size_s": physics_step_text,
            "real_time_factor": (
                0.0 if physics_declaration == "generated" else None
            ),
        },
        "ground_plane": {
            "model_name": ENGINEERING_GROUND_MODEL_NAME,
            "link_name": ENGINEERING_GROUND_LINK_NAME,
            "collision_name": ENGINEERING_GROUND_COLLISION_NAME,
            "geometry": "plane",
            "normal_enu": [0.0, 0.0, 1.0],
            "extent_enu_m": dict(sorted(extent.items())),
            "dimensions_m": {
                "east": east_size_m,
                "north": north_size_m,
            },
            "center_enu_m": {
                "east": east_center_m,
                "north": north_center_m,
                "up": elevation_enu_up_m,
            },
            "elevation_enu_up_m": elevation_enu_up_m,
            "elevation_amsl_m": terrain_amsl_m,
        },
        "authored_flat_simulation_assumption": {
            "declared": True,
            "measured_terrain": False,
            "statement": (
                "Engineering-only physical ground compiled from the supplied constant "
                "terrain and geoid models; it is not measured Shanghai terrain."
            ),
        },
    }


def _pose(east: float, north: float, up: float = 0.0) -> WorldPose:
    return WorldPose(frame_id="ENU", east_m=float(east), north_m=float(north), up_m=float(up), qw=1.0, qx=0.0, qy=0.0, qz=0.0)


def _parent_pose(x: float, y: float, z: float) -> ParentRelativePose:
    return ParentRelativePose(frame_id="parent", x_m=x, y_m=y, z_m=z, qw=1.0, qx=0.0, qy=0.0, qz=0.0)


def _region(region_id: str, kind: str, vertices: list[list[float]], low: float, high: float, attenuation: float | None = None) -> RegionSpec:
    return RegionSpec(region_id=region_id, kind=kind, geometry=RegionGeometry(footprint_enu_m=tuple(EnuPoint(east_m=x, north_m=y) for x, y in vertices), min_altitude_m=low, max_altitude_m=high, height_reference="enu_up"), communications_shadow_attenuation_db=attenuation)


def _clip_centerline(points: list[list[float]]) -> tuple[tuple[tuple[float, float], ...], ...]:
    if SHAPELY_VERSION != "2.0.7":
        raise ValueError(f"strict scene closure requires Shapely 2.0.7, found {SHAPELY_VERSION}")
    line = LineString((float(point[0]), float(point[1])) for point in points)
    clipped = line.intersection(box(-500.0, -500.0, 500.0, 500.0))
    lines = clipped.geoms if isinstance(clipped, (MultiLineString, GeometryCollection)) else (clipped,)
    segments = [
        tuple((float(x), float(y)) for x, y in segment.coords)
        for segment in lines
        if isinstance(segment, LineString) and len(segment.coords) >= 2
    ]
    return tuple(sorted(segments, key=lambda segment: (segment[0][0], segment[0][1], segment[-1][0], segment[-1][1])))


def _road_kind(highway: object) -> str:
    if str(highway) in {"footway", "path", "pedestrian", "cycleway", "steps", "bridleway"}:
        return "pedestrian_path"
    if str(highway) == "service":
        return "service_road"
    return "vehicle_lane"


def _world_assets(
    root: Path,
    *,
    scene_dir: Path,
    mesh_pack: Path,
    geoid: Path,
    terrain: Path,
    sumo_dir: Path,
    execution_profile: str = "formal",
) -> tuple[tuple[AssetRecord, ...], dict[str, Path], dict[str, Any]]:
    scene_manifest, objects = _load_scene(scene_dir)
    raw_source = ROOT / "frontend/public/osm2world/shanghai-hongqiao.osm.json"
    effective_source = scene_dir / "osm/effective.osm.json"
    gazebo_source = scene_dir / "gazebo/scene.sdf"
    for path in (raw_source, effective_source, gazebo_source):
        if not path.is_file():
            raise ValueError(f"scene compiler output is incomplete: {path}")
    mesh_manifest, mesh_files = _copy_mesh_pack(root, mesh_pack, effective_source)
    paths: dict[str, Path] = {}
    paths["osm-source"] = copy_asset(root, raw_source, "world/osm/source.osm.json")
    paths["osm-scene"] = copy_asset(root, effective_source, "world/osm/effective.osm.json")
    paths["geoid"] = copy_asset(root, geoid, "world/geoid/model.json")
    paths["terrain"] = copy_asset(root, terrain, "world/terrain/model.json")
    engineering_ground: dict[str, Any] | None = None
    if execution_profile == "engineering":
        paths["gazebo"] = root / "world/gazebo/scene.sdf"
        engineering_ground = _derive_engineering_ground_scene(
            source=gazebo_source,
            destination=paths["gazebo"],
            terrain=paths["terrain"],
            geoid=paths["geoid"],
            scene_manifest=scene_manifest,
            physics_step_ns=PHYSICS_STEP_NS,
        )
        paths["engineering-ground-provenance"] = write_json(
            root,
            "world/provenance/engineering-ground.json",
            engineering_ground,
        )
    else:
        paths["gazebo"] = copy_asset(root, gazebo_source, "world/gazebo/scene.sdf")
    paths["scene-compiler-provenance"] = copy_asset(root, scene_dir / "manifest.json", "world/provenance/scene-compiler.json")
    paths["scene-object-provenance"] = copy_asset(root, scene_dir / "metadata/objects.json", "world/provenance/scene-objects.json")
    if execution_profile == "formal":
        paths["sumo-toolchain"] = copy_asset(root, sumo_dir / "toolchain.json", "world/provenance/sumo-toolchain.json")
    expected_sumo = {"network.net.xml", "routes.rou.xml", "additional.add.xml", "simulation.sumocfg"}
    if not expected_sumo <= {path.name for path in sumo_dir.iterdir() if path.is_file()}:
        raise ValueError("SUMO input directory must contain network, routes, additional, and simulation files")
    for name in sorted(expected_sumo):
        paths[f"sumo-{name}"] = copy_asset(root, sumo_dir / name, f"world/sumo/{name}")
    license_path = write_bytes(root, "licenses/aero-bench-bundled.txt", b"AERO-BENCH bundled offline asset license; see source provenance.\n")
    license_ref = artifact_selector(root, "asset.license", license_path)
    public = (AssetAudience(audience_kind="public", audience_id="all"),)
    provider_flight = (AssetAudience(audience_kind="provider", audience_id=FLIGHT_ID),)
    provider_traffic = (AssetAudience(audience_kind="provider", audience_id=TRAFFIC_ID),)
    assets: list[AssetRecord] = [
        _asset(root, asset_id="asset.osm-source", relative="world/osm/source.osm.json", role="osm_source", media_type="application/json", visibility="private", frame="WGS84", audiences=(AssetAudience(audience_kind="verifier", audience_id="urban.recovery.verifier"),), license_ref=license_ref),
        _asset(root, asset_id="asset.osm-scene", relative="world/osm/effective.osm.json", role="layer_tiles", media_type="application/json", visibility="public", frame="WGS84", audiences=public, license_ref=license_ref),
        _asset(root, asset_id="asset.osm-mesh", relative="world/osm2world/manifest.json", role="layer_tiles", media_type="application/json", visibility="public", frame="asset_local", audiences=public, license_ref=license_ref),
        _asset(root, asset_id="asset.gazebo-scene", relative="world/gazebo/scene.sdf", role="other", media_type="application/vnd.gazebo.sdf+xml", visibility="private", frame="ENU", audiences=provider_flight, license_ref=license_ref),
        _asset(root, asset_id="asset.geoid", relative="world/geoid/model.json", role="geoid_model", media_type="application/json", visibility="public", frame="raster_pixel", audiences=public, license_ref=license_ref, height_tolerance_m=0.01),
        _asset(root, asset_id="asset.terrain", relative="world/terrain/model.json", role="terrain_model", media_type="application/json", visibility="public", frame="raster_pixel", audiences=public, license_ref=license_ref, height_tolerance_m=0.05),
    ]
    mesh_document = json.loads((mesh_pack / "manifest.json").read_text(encoding="utf-8"))
    mesh_texture_digests = {
        str(descriptor["sha256"]): path
        for path, descriptor in mesh_document["textures"].items()
    }
    for path in mesh_files:
        if path.name in {"manifest.json", "manifest.lock.json"}:
            continue
        digest = path.name
        assets.append(_asset(root, asset_id=f"asset.osm2world.{digest}", relative=f"world/osm2world/assets/{digest}", role="layer_tiles", media_type=("image/jpeg" if str(mesh_texture_digests.get(digest, "")).lower().endswith((".jpg", ".jpeg")) else "image/png" if str(mesh_texture_digests.get(digest, "")).lower().endswith(".png") else "application/octet-stream"), visibility="public", frame="asset_local", audiences=public, license_ref=license_ref))
    for key, role, media, frame in (("sumo-simulation.sumocfg", "sumo_config", "application/xml", "non_spatial"), ("sumo-network.net.xml", "sumo_network", "application/xml", "sumo_net"), ("sumo-routes.rou.xml", "sumo_routes", "application/xml", "non_spatial"), ("sumo-additional.add.xml", "sumo_additional", "application/xml", "non_spatial")):
        assets.append(_asset(root, asset_id=f"asset.{key}", relative=f"world/sumo/{paths[key].name}", role=role, media_type=media, visibility="private", frame=frame, audiences=provider_traffic, license_ref=license_ref))
    model_documents = {
        "uav": {"schema_version": "aero-bench.entity-model/v1", "model": "gz_x500", "resource": "x500_base", "source": "PX4 Gazebo model registry"},
        "vehicle": {"schema_version": "aero-bench.entity-model/v1", "model": "sumo.vehicle", "source": "SUMO TraCI model"},
        "pedestrian": {"schema_version": "aero-bench.entity-model/v1", "model": "sumo.person", "source": "SUMO TraCI model"},
        "station": {"schema_version": "aero-bench.entity-model/v1", "model": "groundstation", "source": "scenario static"},
    }
    for kind, document in model_documents.items():
        relative = f"world/models/{kind}.json"
        write_json(root, relative, document)
        paths[f"model-{kind}"] = root / relative
        assets.append(_asset(root, asset_id=f"asset.model-{kind}", relative=relative, role="entity_model", media_type="application/json", visibility="public", frame="asset_local", audiences=public, license_ref=license_ref))
    building_records = sorted(objects.get("buildings", []), key=lambda item: str(item.get("object_id", "")))
    for index, record in enumerate(building_records, start=1):
        building_id = f"building.{index:03d}"
        source_rel = f"world/buildings/{building_id}.source.json"
        render_rel = f"world/buildings/{building_id}.render.json"
        collision_rel = f"world/buildings/{building_id}.collision.json"
        write_json(root, source_rel, record)
        write_json(root, render_rel, {"schema_version": "aero-bench.osm2world-object/v1", "source_object": record})
        write_json(root, collision_rel, {"schema_version": "aero-bench.gazebo-collision/v1", "source_object_id": record.get("object_id"), "footprint_enu_m": record.get("footprint_enu_m"), "base_enu_up_m": record.get("base_enu_up_m"), "top_enu_up_m": record.get("top_enu_up_m")})
        assets.extend((_asset(root, asset_id=f"asset.{building_id}.source", relative=source_rel, role="building_source", media_type="application/json", visibility="private", frame="asset_local", audiences=provider_flight, license_ref=license_ref), _asset(root, asset_id=f"asset.{building_id}.render", relative=render_rel, role="building_render", media_type="application/json", visibility="public", frame="WGS84", audiences=public, license_ref=license_ref), _asset(root, asset_id=f"asset.{building_id}.collision", relative=collision_rel, role="building_collision", media_type="application/json", visibility="private", frame="asset_local", audiences=provider_flight, license_ref=license_ref)))
    assets.sort(key=lambda item: item.artifact.artifact_id)
    return tuple(assets), paths, {
        "manifest": scene_manifest,
        "objects": objects,
        "engineering_ground": engineering_ground,
    }


def _runtime(image: str, command: list[str], component_id: str, revision: str, version: str, cpu: int, memory: int) -> dict[str, object]:
    return {"runtime": {"image": image, "command": command}, "resources": {"cpu_millicores": cpu, "memory_mib": memory, "gpu_count": 0}, "implementation": {"component_id": component_id, "kind": "production", "source_uri": SOURCE_URI, "source_revision": revision, "version": version}}


def _artifact(artifact_id: str, artifact_type: str, producer_id: str, visibility: str, relative_path: str, max_size_bytes: int, source_asset_id: str | None = None) -> dict[str, object]:
    return {"artifact_id": artifact_id, "artifact_type": artifact_type, "producer_id": producer_id, "visibility": visibility, "relative_path": relative_path, "max_size_bytes": max_size_bytes, "source_asset_id": source_asset_id}


def _physical_policy() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.px4-physical-completion-policy/v2",
        "physical_sim_timeout_ns": 60_000_000_000,
        "min_settle_samples": 3,
        "settle_duration_ns": 1_000_000_000,
        "takeoff_altitude_tolerance_m": 1.0,
        "goto_horizontal_tolerance_m": 0.75,
        "goto_vertical_tolerance_m": 0.5,
        "goto_minimum_progress_m": 1.0,
        "hold_drift_radius_m": 1.5,
        "max_horizontal_settled_speed_m_s": 0.8,
        "max_vertical_settled_speed_m_s": 0.5,
        "landing_max_speed_m_s": 0.35,
        "landing_max_height_proxy_m": 0.5,
        "disarm_requires_contact": True,
        "arm_allowed_modes": ["ALTCTL", "HOLD", "MANUAL", "POSCTL", "READY", "STABILIZED"],
        "disarm_allowed_modes": ["HOLD", "LAND", "MANUAL", "POSCTL", "READY", "STABILIZED"],
        "takeoff_allowed_modes": ["HOLD", "MISSION", "POSCTL", "TAKEOFF"],
        "goto_allowed_modes": ["HOLD", "MISSION", "OFFBOARD", "POSCTL"],
        "hold_allowed_modes": ["HOLD", "LOITER", "POSCTL"],
        "land_allowed_modes": ["HOLD", "LAND", "POSCTL", "RETURN_TO_LAUNCH"],
    }


def _provider_configs(root: Path, world: WorldPackage, revision: str) -> dict[str, Path]:
    region = next(item for item in world.regions if item.region_id == "region.no-fly.recovery")
    region_digest = digest_bytes(canonical_json_bytes(region.model_dump(mode="json")))

    def initial_pose(vehicle_id: str) -> dict[str, float]:
        pose = next(entity.pose for entity in world.entities if entity.entity_id == vehicle_id)
        return {"x_m": pose.east_m, "y_m": pose.north_m, "z_m": pose.up_m,
                "roll_rad": 0.0, "pitch_rad": 0.0, "yaw_rad": 0.0}

    px4 = Px4GazeboConfig.model_validate({
        "schema_version": "aero-bench.px4-gazebo/v3",
        "provider_id": FLIGHT_ID,
        "px4": {"version": "v1.17.0-alpha1-1551-g381149fb01", "commit": "381149fb012762f5e38c4a7fdc1b905b28038970"},
        "gazebo": {"version": "8.11.0", "commit": "1be3cc376fec778cc725b4eeea463245affa56d3"},
        "mavsdk": {"version": "3.17.2", "commit": "9e3ca17faa84aa868caea10a3bbdab7e53810ced"},
        "engine_binding_id": "gazebo.enu",
        "physics_step_ns": PHYSICS_STEP_NS,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [
            {"vehicle_id": "uav.01", "system_id": 1, "mavsdk_udp_port": 14540, "px4_mavlink_udp_port": 14580, "mavsdk_grpc_port": 15040, "sys_autostart": 4001, "model": "gz_x500", "gazebo_model_name": "uav.01", "gazebo_resource": "x500", "initial_pose": initial_pose("uav.01")},
            {"vehicle_id": "uav.02", "system_id": 2, "mavsdk_udp_port": 14541, "px4_mavlink_udp_port": 14581, "mavsdk_grpc_port": 15041, "sys_autostart": 4001, "model": "gz_x500", "gazebo_model_name": "uav.02", "gazebo_resource": "x500", "initial_pose": initial_pose("uav.02")},
        ],
        "required_commands": ["px4", "gz", "mavsdk-server"],
        "command_timeout_ms": 180_000,
        "physical_completion_policy": _physical_policy(),
        "maximum_agent_decision_wall_time_ms": 180_000,
        "heartbeat_timeout_fixed_margin_ms": 30_000,
        "world_name": WORLD_ID,
        "airspace_transition": {"world_id": WORLD_ID, "world_digest": world.world_digest, "region_id": region.region_id, "region_digest": region_digest, "incident_vehicle": "uav.01", "transition_topic": "/aero_bench/urban/airspace", "min_east_m": min(point.east_m for point in region.geometry.footprint_enu_m), "max_east_m": max(point.east_m for point in region.geometry.footprint_enu_m), "min_north_m": min(point.north_m for point in region.geometry.footprint_enu_m), "max_north_m": max(point.north_m for point in region.geometry.footprint_enu_m), "min_up_m": region.geometry.min_altitude_m, "max_up_m": region.geometry.max_altitude_m},
    })
    ns3 = Ns3Config.model_validate({"schema_version": "aero-bench.ns3/v3", "provider_id": NETWORK_ID, "ns3": {"version": "3.48", "commit": "d2add90b452d600cfb4859baed8e9ea633519447"}, "network_model": "wifi-adhoc-scene-mobility/v1", "supported_wifi_standards": ["802.11ax"], "required_commands": ["ns3"], "command_timeout_ms": 180_000})
    sumo = SumoConfig.model_validate({"schema_version": "aero-bench.sumo/v2", "provider_id": TRAFFIC_ID, "sumo": {"version": "1.27.1", "commit": "7717f2379d9e314a0c81c5cec748444de06a2a91"}, "sumo_binary": "sumo", "traci_port": 18813, "step_length_ns": STEP_NS, "sumo_args": ["--duration-log.disable", "true", "--no-step-log", "true", "--no-warnings", "true"], "required_commands": ["sumo"], "command_timeout_ms": 180_000})
    return {"px4": write_json(root, "configs/px4.json", px4.model_dump(mode="json")), "ns3": write_json(root, "configs/ns3.json", ns3.model_dump(mode="json")), "sumo": write_json(root, "configs/sumo.json", sumo.model_dump(mode="json"))}


def _schemas(root: Path) -> dict[str, Path]:
    identifier = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    object_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False}
    schemas: dict[str, object] = {
        "world": WorldPackage.model_json_schema(),
        "task-package": DemoTaskPackage.model_json_schema(),
        "px4-config": Px4GazeboConfig.model_json_schema(),
        "ns3-config": Ns3Config.model_json_schema(),
        "sumo-config": SumoConfig.model_json_schema(),
        "gateway-protocol": object_schema,
        "provider-protocol": object_schema,
        "tool-response": object_schema,
        "flight-vehicle": {**object_schema, "properties": {"vehicle_id": identifier}, "required": ["vehicle_id"]},
        "flight-goto": {**object_schema, "properties": {"vehicle_id": identifier, "latitude_deg": {"type": "number", "minimum": -90, "maximum": 90}, "longitude_deg": {"type": "number", "minimum": -180, "maximum": 180}, "altitude_amsl_m": {"type": "number"}, "yaw_deg": {"type": "number", "minimum": -360, "maximum": 360}}, "required": ["vehicle_id", "latitude_deg", "longitude_deg", "altitude_amsl_m", "yaw_deg"]},
        "flight-takeoff": {**object_schema, "properties": {"vehicle_id": identifier, "altitude_m": {"type": "number", "exclusiveMinimum": 10, "maximum": 80}}, "required": ["vehicle_id", "altitude_m"]},
        "network-send": {**object_schema, "properties": {"work_order_id": identifier, "message_id": identifier, "source": identifier, "destination": identifier, "payload_base64": {"type": "string", "minLength": 4, "maxLength": 81920}, "payload_sha256": digest, "traffic_class": {"const": "best_effort"}, "priority": {"const": 0}, "reliability": {"const": "best_effort"}}, "required": ["work_order_id", "message_id", "source", "destination", "payload_base64", "payload_sha256", "traffic_class", "priority", "reliability"]},
        "urban-camera": {**object_schema, "properties": {"schema_version": {"const": "aero-bench.observation.urban-rgb.v1"}, "camera_id": identifier, "visual_kind": {"const": "gazebo_rgb"}, "visual_status": {"const": "captured"}, "frame_id": identifier, "vehicle_id": identifier, "engine_sim_time_ns": {"type": "integer", "minimum": 0}, "logical_origin_engine_ns": {"type": "integer", "minimum": 0}, "pose_sha256": digest, "intrinsics_sha256": digest, "image_sha256": digest, "size_bytes": {"type": "integer", "minimum": 1}, "selector": {"type": "string", "minLength": 1}, "width": {"const": 640}, "height": {"const": 480}, "media_type": {"const": "image/png"}, "source": {"const": "gazebo.camera"}}, "required": ["schema_version", "camera_id", "visual_kind", "visual_status", "frame_id", "vehicle_id", "engine_sim_time_ns", "logical_origin_engine_ns", "pose_sha256", "intrinsics_sha256", "image_sha256", "size_bytes", "selector", "width", "height", "media_type", "source"]},
        "urban-telemetry": {**object_schema, "properties": {"schema_version": {"const": "aero-bench.observation.urban-telemetry.v1"}, "vehicle_id": identifier, "simulation_time_ns": {"type": "integer", "minimum": 0}, "pose_json": {"type": "string", "minLength": 2}, "position_wgs84_json": {"type": "string", "minLength": 2}, "velocity_json": {"type": "string", "minLength": 2}, "angular_velocity_json": {"type": "string", "minLength": 2}, "attitude_json": {"type": "string", "minLength": 2}, "flight_mode": {"type": "string", "minLength": 1}, "armed": {"type": "boolean"}, "in_air": {"type": "boolean"}, "landed": {"type": "boolean"}, "landed_state": {"type": "string", "minLength": 1}, "battery_percent": {"type": "number", "minimum": 0, "maximum": 100}, "health": {"type": "string", "minLength": 1}, "contacts_json": {"type": "string"}, "ground_contact": {"type": "boolean"}, "collision_contact": {"type": "boolean"}}, "required": ["schema_version", "vehicle_id", "simulation_time_ns", "pose_json", "position_wgs84_json", "velocity_json", "angular_velocity_json", "attitude_json", "flight_mode", "armed", "in_air", "landed", "landed_state", "battery_percent", "health", "contacts_json", "ground_contact", "collision_contact"]},
        "urban-safety": {**object_schema, "properties": {"schema_version": {"const": "aero-bench.observation.urban-safety.v1"}, "vehicle_id": identifier, "region_id": identifier, "airspace_state": {"enum": ["inside", "outside"]}, "transition": {"enum": ["entered", "exited", "none"]}, "transition_sequence": {"type": "integer", "minimum": 0}, "transition_engine_sim_time_ns": {"type": ["integer", "null"], "minimum": 0}, "logical_origin_engine_ns": {"type": "integer", "minimum": 0}, "engine_sim_time_ns": {"type": "integer", "minimum": 0}, "simulation_time_ns": {"type": "integer", "minimum": 0}, "source": {"const": "gazebo.system"}, "world_sha256": digest, "region_sha256": digest, "position_enu_m": {"type": "string", "minLength": 2}}, "required": ["schema_version", "vehicle_id", "region_id", "airspace_state", "transition", "transition_sequence", "transition_engine_sim_time_ns", "logical_origin_engine_ns", "engine_sim_time_ns", "simulation_time_ns", "source", "world_sha256", "region_sha256", "position_enu_m"]},
        "network-mailbox": NetworkMailboxObservationPayload.model_json_schema(),
        "verifier": UrbanRecoveryVerifierConfig.model_json_schema(),
    }
    return {name: write_json(root, f"schemas/{name}.json", value) for name, value in schemas.items()}


def _recovery_polygon(execution_profile: str) -> list[list[float]]:
    if execution_profile == "engineering":
        return [[-310.0, -340.0], [-290.0, -340.0], [-290.0, -320.0], [-310.0, -320.0]]
    return [[-70.0, 80.0], [70.0, 80.0], [70.0, 220.0], [-70.0, 220.0]]


def _world_content(
    root: Path,
    *,
    assets: tuple[AssetRecord, ...],
    metadata: dict[str, Any],
    execution_profile: str = "formal",
) -> WorldPackage:
    objects = metadata["objects"]
    building_records = sorted(objects.get("buildings", []), key=lambda item: str(item.get("object_id", "")))
    buildings: list[BuildingSpec] = []
    entities: list[EntitySpec] = []
    for index, record in enumerate(building_records, start=1):
        building_id = f"building.{index:03d}"
        footprint = list(record["footprint_enu_m"])
        if footprint[0] == footprint[-1]:
            footprint = footprint[:-1]
        if sum(footprint[i][0] * footprint[i + 1][1] - footprint[i + 1][0] * footprint[i][1] for i in range(len(footprint) - 1)) < 0:
            footprint.reverse()
        base = float(record.get("base_enu_up_m", 0.0))
        top = float(record["top_enu_up_m"])
        geometry = BuildingGeometry(
            footprint_enu_m=tuple(EnuPoint(east_m=float(x), north_m=float(y)) for x, y in footprint),
            base_altitude_m=base,
            top_altitude_m=top,
            height_reference="enu_up",
        )
        buildings.append(BuildingSpec(building_id=building_id, entity_id=f"entity.{building_id}", source_asset_id=f"asset.{building_id}.source", render_asset_id=f"asset.{building_id}.render", collision_asset_id=f"asset.{building_id}.collision", geometry=geometry))
        center_e = sum(point.east_m for point in geometry.footprint_enu_m) / len(geometry.footprint_enu_m)
        center_n = sum(point.north_m for point in geometry.footprint_enu_m) / len(geometry.footprint_enu_m)
        entities.append(EntitySpec(entity_id=f"entity.{building_id}", kind="static_asset", provider_id=None, authority_kind="scenario_static", state="static", model_asset_id=f"asset.{building_id}.render", pose=_pose(center_e, center_n, base)))

    entities.extend((
        EntitySpec(entity_id="station.ground", kind="static_asset", provider_id=None, authority_kind="scenario_static", state="static", model_asset_id="asset.model-station", pose=_pose(0.0, -350.0, 0.0)),
        EntitySpec(entity_id="uav.01", kind="uav", provider_id=FLIGHT_ID, authority_kind="gazebo_physics", state="dynamic", model_asset_id="asset.model-uav", pose=_pose(-320.0, -350.0, 0.15)),
        EntitySpec(entity_id="uav.02", kind="uav", provider_id=FLIGHT_ID, authority_kind="gazebo_physics", state="dynamic", model_asset_id="asset.model-uav", pose=_pose(300.0 if execution_profile == "engineering" else 320.0, -350.0, 0.15)),
    ))
    roads: list[RoadSpec] = []
    road_records = sorted(objects.get("roads", []), key=lambda item: str(item.get("object_id", "")))
    road_index = 1
    for record in road_records:
        segments = _clip_centerline(record["centerline_enu_m"])
        for segment in segments:
            points = tuple(EnuPoint(east_m=point[0], north_m=point[1]) for point in segment)
            if len(points) < 2 or len({(point.east_m, point.north_m) for point in points}) < 2:
                continue
            width = record.get("width_osm_tag")
            try:
                width_m = float(width) if width is not None else (3.0 if _road_kind(record.get("highway")) == "pedestrian_path" else 7.0)
            except (TypeError, ValueError):
                width_m = 7.0
            roads.append(RoadSpec(road_id=f"road.{road_index:04d}", kind=_road_kind(record.get("highway")), centerline_enu_m=points, width_m=width_m))
            road_index += 1
    roads.sort(key=lambda item: item.road_id)
    # SUMO owns every moving traffic entity. Initial positions are taken from
    # compiled OSM road centerlines only to close the pre-first-step schema.
    traffic_points = [point for road in roads if road.kind == "vehicle_lane" for point in road.centerline_enu_m]
    if len(traffic_points) < 25:
        raise ValueError("clipped OSM scene has insufficient real road centerline points for traffic entities")
    sumo_bindings: list[SumoEntityBinding] = []
    for index in range(20):
        point = traffic_points[index % len(traffic_points)]
        entity_id = f"vehicle.{index + 1:02d}"
        entities.append(EntitySpec(entity_id=entity_id, kind="ugv", provider_id=TRAFFIC_ID, authority_kind="sumo_traffic", state="dynamic", model_asset_id="asset.model-vehicle", pose=_pose(point.east_m, point.north_m, 0.0)))
        sumo_bindings.append(SumoEntityBinding(sumo_object_id=f"veh.{index + 1:02d}", entity_id=entity_id, kind="vehicle"))
    for index in range(5):
        point = traffic_points[(20 + index) % len(traffic_points)]
        entity_id = f"pedestrian.{index + 1:02d}"
        entities.append(EntitySpec(entity_id=entity_id, kind="pedestrian", provider_id=TRAFFIC_ID, authority_kind="sumo_traffic", state="dynamic", model_asset_id="asset.model-pedestrian", pose=_pose(point.east_m, point.north_m, 0.0)))
        sumo_bindings.append(SumoEntityBinding(sumo_object_id=f"ped.{index + 1:02d}", entity_id=entity_id, kind="person"))
    entities.sort(key=lambda item: item.entity_id)
    regions = (
        _region("region.geofence", "geofence", [[-500.0, -500.0], [500.0, -500.0], [500.0, 500.0], [-500.0, 500.0]], -10.0, 200.0),
        _region("region.no-fly.recovery", "no_fly", _recovery_polygon(execution_profile), 10.0 if execution_profile == "engineering" else 20.0, 160.0),
        _region("region.network-shadow.east", "communications_shadow", [[180.0, -200.0], [420.0, -200.0], [420.0, 80.0], [180.0, 80.0]], 0.0, 180.0, 18.0),
    )
    launches = (
        LaunchSiteSpec(launch_site_id="launch.uav.01", primary_uav_entity_id="uav.01", allowed_uav_entity_ids=("uav.01",), pose=_pose(-320.0, -350.0, 0.15), pad_radius_m=4.0),
        LaunchSiteSpec(launch_site_id="launch.uav.02", primary_uav_entity_id="uav.02", allowed_uav_entity_ids=("uav.02",), pose=_pose(300.0 if execution_profile == "engineering" else 320.0, -350.0, 0.15), pad_radius_m=4.0),
    )
    sensors = ()
    max_building_top_m = max((float(record["top_enu_up_m"]) for record in building_records), default=0.0)
    frame = WorldFrame(geodetic_frame_id="WGS84", ecef_frame_id="ECEF", enu_frame_id="ENU", ned_frame_id="NED", transform_chain="WGS84->ECEF->ENU->NED", datum="WGS84", semi_major_axis_m=6378137.0, inverse_flattening=298.257223563, ellipsoid_height_reference="wgs84_ellipsoid", amsl_height_reference="orthometric_msl", agl_height_reference="terrain_relative", origin=LocalFrameOrigin(frame_id="WGS84", longitude_deg=SHANGHAI_LON, latitude_deg=SHANGHAI_LAT, altitude_m=ORIGIN_ELLIPSOID_M), origin_height_reference="wgs84_ellipsoid", spatial_extent={"min_east_m": -500.0, "max_east_m": 500.0, "min_north_m": -500.0, "max_north_m": 500.0, "min_up_m": -10.0, "max_up_m": max(250.0, max_building_top_m + 1.0), "vertical_reference": "enu_up"}, vertical_datum=VerticalDatumBinding(geoid_correction_asset_id="asset.geoid", terrain_height_asset_id="asset.terrain", geoid_interpolation="bilinear", terrain_interpolation="bilinear", geoid_precision_m=0.01, terrain_precision_m=0.05))
    provider_requirements = (
        ProviderRequirement(provider_id=FLIGHT_ID, roles=("motion",), required_capability_ids=tuple(sorted({"flight.arm", "flight.command", "flight.disarm", "flight.goto", "flight.hold", "flight.land", "flight.takeoff", "gazebo.frames", "gazebo.physics"}))),
        ProviderRequirement(provider_id=NETWORK_ID, roles=("wireless_network",), required_capability_ids=ns3_capabilities(("802.11ax",))),
        ProviderRequirement(provider_id=TRAFFIC_ID, roles=("traffic",), required_capability_ids=("sumo.frames", "sumo.signals", "sumo.traffic")),
    )
    layers = (PublicLayer(layer_id="layer.osm-scene", kind="osm_scene", asset_id="asset.osm-scene", visibility="public", default_visible=True), PublicLayer(layer_id="layer.osm-mesh", kind="osm_mesh", asset_id="asset.osm-mesh", visibility="public", default_visible=True))
    engine_bindings = (
        EngineFrameBinding(binding_id="gazebo.enu", provider_id=FLIGHT_ID, engine="gazebo", scene_asset_id="asset.gazebo-scene", source_frame_id="ENU", target_frame_id="gazebo_world", transform=RigidOffset(x_m=0.0, y_m=0.0, z_m=0.0, qw=1.0, qx=0.0, qy=0.0, qz=0.0)),
        EngineFrameBinding(binding_id="sumo.enu", provider_id=TRAFFIC_ID, engine="sumo", scene_asset_id=None, source_frame_id="sumo_net", target_frame_id="ENU", transform=RigidOffset(x_m=0.0, y_m=0.0, z_m=0.0, qw=1.0, qx=0.0, qy=0.0, qz=0.0)),
    )
    sumo = SumoConfiguration(provider_id=TRAFFIC_ID, config_asset_id="asset.sumo-simulation.sumocfg", network_asset_id="asset.sumo-network.net.xml", routes_asset_id="asset.sumo-routes.rou.xml", additional_asset_id="asset.sumo-additional.add.xml", frame_binding_id="sumo.enu", object_bindings=tuple(sorted(sumo_bindings, key=lambda item: item.sumo_object_id)))
    network = NetworkConfiguration(provider_id=NETWORK_ID, radio_profiles=(WirelessRadioProfile(radio_profile_id="radio.urban.ax", provider_id=NETWORK_ID, wifi_standard="802.11ax", frequency_ghz=5.775, channel_width_mhz=80.0, tx_power_dbm=23.0, rx_sensitivity_dbm=-92.0),), node_bindings=tuple(sorted((NetworkNodeBinding(node_id="node.ground", entity_id="station.ground", endpoint_id="endpoint.groundstation", radio_profile_id="radio.urban.ax"), NetworkNodeBinding(node_id="node.uav.01", entity_id="uav.01", endpoint_id="endpoint.uav.01", radio_profile_id="radio.urban.ax"), NetworkNodeBinding(node_id="node.uav.02", entity_id="uav.02", endpoint_id="endpoint.uav.02", radio_profile_id="radio.urban.ax")), key=lambda item: item.node_id)), links=(NetworkLinkBinding(link_id="link.uav.01-ground", source_node_id="node.uav.01", destination_node_id="node.ground", data_rate_bps=6000000, propagation_delay_ns=20000000), NetworkLinkBinding(link_id="link.uav.02-ground", source_node_id="node.uav.02", destination_node_id="node.ground", data_rate_bps=6000000, propagation_delay_ns=20000000)))
    missions = (MissionRequirement(requirement_id="mission.takeoff", kind="takeoff", dependencies=(), launch_site_id="launch.uav.01", target_id=None, expected_public_asset_id=None), MissionRequirement(requirement_id="mission.return", kind="return_to_launch", dependencies=("mission.takeoff",), launch_site_id="launch.uav.01", target_id=None, expected_public_asset_id=None), MissionRequirement(requirement_id="mission.land", kind="land", dependencies=("mission.return",), launch_site_id="launch.uav.01", target_id=None, expected_public_asset_id=None))
    return world_package(world_id=WORLD_ID, frame=frame, assets=tuple(sorted(assets, key=lambda item: item.artifact.artifact_id)), provider_requirements=provider_requirements, base_layers=(), layers=layers, buildings=tuple(buildings), roads=tuple(roads), regions=regions, launch_sites=launches, entities=tuple(entities), sensors=sensors, semantic_targets=(), weather=(WeatherSample(sample_id="weather.constant", mode="deterministic_constant", wind=WindVector(east_mps=1.5, north_mps=-0.4, up_mps=0.0), visibility_m=10000.0, precipitation="none", precipitation_rate_mm_per_h=0.0, temperature_c=22.0, pressure_pa=101325.0),), engine_frame_bindings=engine_bindings, sumo=sumo, network=network, mission_requirements=missions, expected_public_assets=())


def _runtime_artifacts(execution_profile: str = "formal") -> dict[str, dict[str, object]]:
    artifacts = {
        "event": _artifact("artifact.event-log", "event.log", "harness", "private", "harness/event-log.jsonl", 2 * 1024 * 1024 * 1024),
        "scene": _artifact("artifact.scene-state-history", "scene.state-history", "harness", "public", "harness/scene-state-history.jsonl", 2 * 1024 * 1024 * 1024),
        "trajectory": _artifact("artifact.trajectory", "trajectory", FLIGHT_ID, "public", "flight/trajectory.json", 512 * 1024 * 1024),
        "observation": _artifact("artifact.observation", "observation", FLIGHT_ID, "private", "flight/observations.json", 32 * 1024 * 1024),
        "sensor-frame": _artifact("artifact.sensor-frames", "sensor-frame", FLIGHT_ID, "public", "flight/sensor-frames.json", 32 * 1024 * 1024),
        "camera-frame-data": _artifact("artifact.camera-frame-data", "camera-frame-data", FLIGHT_ID, "public", "flight/camera-frame-data.bin", 256 * 1024 * 1024),
        "physics-journal": _artifact("artifact.physics-journal", "gazebo.physics-journal", FLIGHT_ID, "private", "flight/physics-journal.jsonl", 2 * 1024 * 1024 * 1024),
        "physics-plugin": _artifact("artifact.physics-plugin", "gazebo.physics-plugin", FLIGHT_ID, "private", "flight/physics-plugin.so", 64 * 1024 * 1024),
        "network": _artifact("artifact.network-delivery", "network.delivery", NETWORK_ID, "public", "network/delivery.json", 512 * 1024 * 1024),
        "traffic": _artifact("artifact.sumo-evidence", "sumo.traffic.evidence", TRAFFIC_ID, "private", "traffic/evidence.json", 512 * 1024 * 1024),
    }
    if execution_profile == "engineering":
        artifacts.pop("physics-journal")
        artifacts.pop("physics-plugin")
    return artifacts


def _demo_package(
    root: Path, raw_source: Path, *, execution_profile: str = "formal",
    recovery_variant: str = "baseline", duration_ns: int | None = None,
) -> DemoTaskPackage:
    engineering = execution_profile == "engineering"
    if duration_ns is None:
        duration_ns = 120_000_000_000 if engineering else DURATION_NS
    package = DemoTaskPackage.model_validate({
        "schema_version": "aero-bench.urban-recovery-demo/v1", "package_id": PACKAGE_ID, "task_id": TASK_ID, "verifier_id": VERIFIER_ID,
        "replay_mode": "indexed", "execution_profile": execution_profile, "recovery_variant": recovery_variant,
        "scene_source_sha256": digest_file(raw_source), "duration_ns": duration_ns, "step_ns": STEP_NS, "physics_step_ns": PHYSICS_STEP_NS, "final_tick": duration_ns // STEP_NS,
        "roles": [
            {"agent_id": "groundstation.rule", "role": "groundstation", "endpoint_id": "endpoint.groundstation", "vehicle_id": None, "telemetry_observation_id": None, "safety_observation_id": None, "mailbox_observation_id": "network.mailbox.endpoint.groundstation"},
            {"agent_id": "uav.policy.01", "role": "uav", "endpoint_id": "endpoint.uav.01", "vehicle_id": "uav.01", "telemetry_observation_id": "observation.uav.01.telemetry", "safety_observation_id": "observation.uav.01.safety", "mailbox_observation_id": "network.mailbox.endpoint.uav.01"},
            {"agent_id": "uav.policy.02", "role": "uav", "endpoint_id": "endpoint.uav.02", "vehicle_id": "uav.02", "telemetry_observation_id": "observation.uav.02.telemetry", "safety_observation_id": "observation.uav.02.safety", "mailbox_observation_id": "network.mailbox.endpoint.uav.02"},
        ],
        "recovery": {"incident_vehicle_id": "uav.01", "incident_region_id": "region.no-fly.recovery", "injection_start_ns": min(5_000_000_000, duration_ns - STEP_NS) if engineering else 60_000_000_000, "heartbeat_interval_ns": 1_000_000_000, "response_timeout_ns": 10_000_000_000, "maximum_retries": 3, "vehicle_radius_m": 1.0, "obstacle_margin_m": 5.0, "cruise_agl_m": 20.0 if engineering else 45.0, "landing_deadline_ns": min(540_000_000_000, max(STEP_NS, duration_ns - 10_000_000_000)) if engineering else 540_000_000_000},
        "flight_provider_id": FLIGHT_ID, "network_provider_id": NETWORK_ID, "traffic_provider_id": TRAFFIC_ID,
        "required_channels": sorted({"agent", "gazebo.airspace", "gazebo.contact", "gazebo.force", "gazebo.wind", "mavlink", "ns3", "px4", "sumo", "sumo.signals"}),
    })
    write_json(root, "task/urban-recovery-package.json", package.model_dump(mode="json"))
    return package


def _agent_spec(root: Path, *, agent_id: str, image: str, revision: str, schemas: dict[str, Path], package: DemoTaskPackage, mission_mode: str = "recovery") -> Path:
    role = next(item for item in package.roles if item.agent_id == agent_id)
    ground = role.role == "groundstation"
    tool_ids = ["network.send"] if ground else ["flight.arm", "flight.disarm", "flight.goto", "flight.hold", "flight.land", "flight.takeoff", "network.send"]
    tools = []
    ground_endpoint = next(item.endpoint_id for item in package.roles if item.role == "groundstation")
    for tool_id in tool_ids:
        schema_key = (
            "network-send" if tool_id == "network.send" else "flight-goto" if tool_id == "flight.goto"
            else "flight-takeoff" if tool_id == "flight.takeoff" else "flight-vehicle"
        )
        request_schema = json.loads(schemas[schema_key].read_bytes())
        if tool_id == "network.send":
            request_schema["properties"]["source"]["const"] = role.endpoint_id
            request_schema["properties"]["destination"]["enum"] = sorted(
                item.endpoint_id for item in package.roles if item.role == "uav"
            ) if ground else [ground_endpoint]
        else:
            request_schema["properties"]["vehicle_id"]["const"] = role.vehicle_id
        request_path = write_json(root, f"schemas/{agent_id}.{tool_id}.json", request_schema)
        tools.append({
            "tool_id": tool_id, "provider_id": NETWORK_ID if tool_id == "network.send" else FLIGHT_ID,
            "request_schema": ref(root, request_path), "response_schema": ref(root, schemas["tool-response"]),
            "timeout_ms": 180000, "idempotent": False,
        })
    if ground:
        observations = [{"observation_id": role.mailbox_observation_id, "provider_id": NETWORK_ID, "schema_file": ref(root, schemas["network-mailbox"]), "timeout_ms": 180000}]
    else:
        observations = [{"observation_id": role.mailbox_observation_id, "provider_id": NETWORK_ID, "schema_file": ref(root, schemas["network-mailbox"]), "timeout_ms": 180000}, {"observation_id": role.safety_observation_id, "provider_id": FLIGHT_ID, "schema_file": ref(root, schemas["urban-safety"]), "timeout_ms": 180000}, {"observation_id": role.telemetry_observation_id, "provider_id": FLIGHT_ID, "schema_file": ref(root, schemas["urban-telemetry"]), "timeout_ms": 180000}]
    participant = {
        "role": role.role, "endpoint_id": role.endpoint_id,
        "groundstation_endpoint_id": "endpoint.groundstation", "mailbox_observation_id": role.mailbox_observation_id,
        "execution_profile": package.execution_profile, "recovery_variant": package.recovery_variant, "mission_mode": mission_mode,
        "final_tick": package.final_tick,
        **{name: getattr(package.recovery, name) for name in (
            "heartbeat_interval_ns", "injection_start_ns", "response_timeout_ns", "maximum_retries",
            "vehicle_radius_m", "obstacle_margin_m", "cruise_agl_m",
        )},
        "origin_latitude_deg": SHANGHAI_LAT, "origin_longitude_deg": SHANGHAI_LON,
        "origin_ellipsoid_height_m": ORIGIN_ELLIPSOID_M, "origin_amsl_m": ORIGIN_AMSL_M,
    }
    engineering = package.execution_profile == "engineering"
    if ground:
        participant.update({
            "recovery_goal_enu_m": {"x": -270.0, "y": -350.0, "z": 20.0} if engineering else {"x": -220.0, "y": 250.0, "z": 45.0},
            "forbidden_polygons": [_recovery_polygon(package.execution_profile)],
            "vehicle_endpoint_ids": {item.vehicle_id: item.endpoint_id for item in package.roles if item.role == "uav"},
            "vehicle_agent_ids": {item.vehicle_id: item.agent_id for item in package.roles if item.role == "uav"},
        })
    else:
        participant.update({"vehicle_id": role.vehicle_id, "telemetry_observation_id": role.telemetry_observation_id, "safety_observation_id": role.safety_observation_id})
        if engineering:
            first = role.vehicle_id == package.recovery.incident_vehicle_id
            participant.update({
                "patrol_enu_m": {"x": -330.0 if first else 330.0, "y": -350.0 if first else -330.0, "z": 20.0},
                "incident_enu_m": {"x": -300.0, "y": -330.0, "z": 20.0},
                "launch_enu_m": {"x": -320.0 if first else 300.0, "y": -350.0, "z": 20.0},
            })
    participant_config = UrbanParticipantConfig.model_validate(participant)
    write_json(root, f"agents/{agent_id}.json", participant_config.model_dump(mode="json"))
    model = AgentSpec.model_validate({"schema_version": "aero-bench.agent/v2", "queries": [], "agent_id": agent_id, "workload": _runtime(image, ["run", "--config-asset", f"asset.participant.{agent_id}"], agent_id, revision, "1.0.0-urban-recovery", 1500, 2048), "tools": tools, "observations": observations, "artifact_requirements": []})
    return write_yaml(root, f"agents/{agent_id}.yaml", model.model_dump(mode="json"))



def _task_environment(root: Path, *, package: DemoTaskPackage, schemas: dict[str, Path], configs: dict[str, Path], images: dict[str, str], revision: str, mission_mode: str = "recovery") -> tuple[Path, Path, tuple[Path, ...]]:
    artifacts = _runtime_artifacts(package.execution_profile)
    # The 96 s measurement (4.135 GiB) projects ~5.2 GiB at 120 s; its
    # 30 s predecessor produced a 416 MiB ledger. Reserve 8 GiB for resident
    # state, both 2 GiB artifact caps, and 4 GiB parser/encoding work, then
    # apply 2x headroom. This is executor capacity, not a physics parameter.
    inspection_memory_mib = 32768 if mission_mode == "inspection" else None
    instruction = write_bytes(root, "instruction.md", (
        "# Urban UAV recovery demonstration\n\n"
        f"Profile: {package.execution_profile}. Recovery variant: {package.recovery_variant}.\n"
        f"Observe through {package.final_tick} provider barriers ({package.duration_ns / 1e9:g} seconds). "
        "Use only authorized public observations and the Gateway. All motion and network delivery must come from real Providers. "
        "Baseline uses delivered constraints and A*; direct-recovery ablates A*; no-recovery disables incident recovery. "
        "Record actual terminal state, including incomplete or failed missions. Engineering runs are unscored; "
        "only the explicit formal profile requires full 600-second mission acceptance.\n"
    ).encode())
    verifier_config = UrbanRecoveryVerifierConfig(
        schema_version="aero-bench.urban-recovery-verifier/v1", package_id=PACKAGE_ID,
        execution_profile=package.execution_profile,
        duration_ns=package.duration_ns, step_ns=STEP_NS, physics_step_ns=PHYSICS_STEP_NS,
        final_tick=package.final_tick, required_channels=package.required_channels,
    )
    write_json(root, "configs/verifier.json", verifier_config.model_dump(mode="json"))
    package_ref = {"package_id": PACKAGE_ID, "config": {"file": ref(root, root / "task/urban-recovery-package.json"), "schema_file": ref(root, schemas["task-package"])}}
    agent_paths = tuple(_agent_spec(root, agent_id=agent_id, image=images[agent_id], revision=revision, schemas=schemas, package=package, mission_mode=mission_mode) for agent_id in ("groundstation.rule", "uav.policy.01", "uav.policy.02"))
    participant_assets = [
        {
            "asset_id": f"asset.participant.{role.agent_id}",
            "file": ref(root, root / f"agents/{role.agent_id}.json"),
            "classification": "public",
            "audiences": [
                {"role": "agent", "workload_ids": [role.agent_id]},
                {"role": "verifier", "workload_ids": [VERIFIER_ID]},
            ],
        }
        for role in package.roles
    ]
    provenance_assets = [
        {
            "asset_id": asset_id, "file": ref(root, root / relative),
            "classification": "private",
            "audiences": [{"role": "verifier", "workload_ids": [VERIFIER_ID]}],
        }
        for asset_id, relative in URBAN_PROVENANCE_ASSETS
        if package.execution_profile == "formal"
    ]
    goals = [goal.model_dump(mode="json") for goal in urban_goals(VERIFIER_ID)]
    task = TaskSpec.model_validate({"schema_version": "aero-bench.task/v1", "task_id": TASK_ID, "package": package_ref, "instruction": ref(root, instruction), "required_capabilities": list(REQUIRED_CAPABILITIES), "required_tools": list(REQUIRED_TOOLS), "assets": participant_assets + provenance_assets, "goals": goals, "verifier": {"verifier_id": VERIFIER_ID, "workload": _runtime(images["verifier"], ["verify"], VERIFIER_ID, revision, "0.1.0-urban-recovery-verifier.1", 1500, inspection_memory_mib or 4096), "config": {"file": ref(root, root / "configs/verifier.json"), "schema_file": ref(root, schemas["verifier"])}, "artifact_requirements": list(artifacts.values()), "output_artifacts": [_artifact("artifact.verification-report", "verification.report", VERIFIER_ID, "public", "verifier/report.json", 16 * 1024 * 1024), _artifact("artifact.verification-event-segment", "verification.event-segment", VERIFIER_ID, "public", "verifier/events.jsonl", 16 * 1024 * 1024)]}})
    task_path = write_yaml(root, "task/task.yaml", task.model_dump(mode="json"))
    provider_defs = (
        (FLIGHT_ID, "px4.gazebo", 17601, "flight", "px4", tuple(sorted({"flight.arm", "flight.command", "flight.disarm", "flight.goto", "flight.hold", "flight.land", "flight.takeoff", "gazebo.frames", "gazebo.physics"})), tuple(item for item in artifacts.values() if item["producer_id"] == FLIGHT_ID), 2500, 8192),
        (NETWORK_ID, "ns3.rpc", 17602, "network", "ns3", ns3_capabilities(("802.11ax",)), (artifacts["network"],), 2000, 4096),
        (TRAFFIC_ID, "sumo.traci", 17603, "traffic", "sumo", ("sumo.frames", "sumo.signals", "sumo.traffic"), (artifacts["traffic"],), 1500, 4096),
    )
    provider_versions = {"px4.gazebo": "0.4.0-px4-gazebo.1", "ns3.rpc": "0.4.0-ns3.1", "sumo.traci": "0.4.0-sumo.2"}
    providers = [{"provider_id": provider_id, "adapter": adapter, "port": port, "workload": _runtime(images[image], ["provider", "serve"], adapter, revision, provider_versions[adapter], cpu, memory), "config": {"file": ref(root, configs[config_key]), "schema_file": ref(root, schemas[f"{config_key}-config"])}, "protocol_schema": ref(root, schemas["provider-protocol"]), "capabilities": list(capabilities), "artifact_requirements": list(provider_artifacts)} for provider_id, adapter, port, image, config_key, capabilities, provider_artifacts, cpu, memory in provider_defs]
    environment = EnvironmentSpec.model_validate({"schema_version": "aero-bench.environment/v1", "environment_id": "urban.uav.recovery.demo.v1", "harness": _runtime(images["harness"], ["serve"], "aero-bench.harness", revision, "0.4.0-harness.1", 2000, inspection_memory_mib or 8192), "clock": {"authority": "provider_barrier", "step_ns": STEP_NS, "max_steps": package.final_tick, "provider_timeout_ms": 240000}, "gateway": {"protocol_schema": ref(root, schemas["gateway-protocol"]), "port": 17432}, "providers": providers, "harness_artifact_requirements": [artifacts["event"], artifacts["scene"]]})
    environment_path = write_yaml(root, "environment/environment.yaml", environment.model_dump(mode="json"))
    return task_path, environment_path, agent_paths


def _validate_sumo_inputs(sumo_dir: Path, *, execution_profile: str = "formal") -> None:
    if execution_profile == "formal":
        toolchain = sumo_dir / "toolchain.json"
        if not toolchain.is_file():
            raise ValueError("formal SUMO input set must include toolchain.json")
        document = json.loads(toolchain.read_text(encoding="utf-8"))
        if document.get("schema_version") != "aero-bench.sumo-toolchain/v1" or document.get("netconvert_version") != "1.27.1" or document.get("sumo_version") != "1.27.1":
            raise ValueError("formal SUMO toolchain declarations must specify netconvert/sumo 1.27.1")
    roots = {}
    for name, root_tag in (
        ("network.net.xml", "net"), ("routes.rou.xml", "routes"),
        ("additional.add.xml", "additional"), ("simulation.sumocfg", "configuration"),
    ):
        path = sumo_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"SUMO input is missing or empty: {path}")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("SUMO input XML must use UTF-8") from error
        if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("SUMO input cannot contain a DTD or entity declaration")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as error:
            raise ValueError(f"SUMO input XML is malformed: {name}") from error
        if root.tag != root_tag or any(node.tag.rsplit("}", 1)[-1] == "include" for node in root.iter()):
            raise ValueError(f"SUMO input has an unexpected root or undeclared include: {name}")
        roots[name] = root
    network = roots["network.net.xml"]
    if not network.findall("edge"):
        raise ValueError("SUMO network contains no netconvert edges")
    inputs = roots["simulation.sumocfg"].findall("input")
    expected = {"net-file": "network.net.xml", "route-files": "routes.rou.xml", "additional-files": "additional.add.xml"}
    if len(inputs) != 1 or len(inputs[0]) != len(expected):
        raise ValueError("SUMO configuration must bind exactly its three declared input files")
    actual = {child.tag: child.attrib for child in inputs[0]}
    if actual != {name: {"value": value} for name, value in expected.items()} or any(list(child) for child in inputs[0]):
        raise ValueError("SUMO configuration input references differ from the declared bundle files")


def _images_lock(path: Path | None, *, execution_profile: str = "formal") -> dict[str, str]:
    if path is None:
        raise ValueError("an authoritative digest-pinned image lock is required")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("images"), dict):
        raise ValueError("image lock must contain an images object")
    images = raw["images"]
    expected = {"groundstation.rule", "uav.policy.01", "uav.policy.02", "flight", "harness", "network", "traffic", "verifier"}
    if set(images) != expected or any(not isinstance(value, str) for value in images.values()):
        raise ValueError("image lock must bind exactly eight workload image identities, including each Agent")
    for image in images.values():
        RuntimeImage(image=image, command=("identity-check",))
        if execution_profile != "engineering" and image.startswith("sha256:"):
            raise ValueError("formal images must use registry manifest digests, not local config IDs")
    if len({image.rsplit("sha256:", 1)[1] for image in images.values()}) != len(expected):
        raise ValueError("distinct workload component labels require distinct image digests")
    return {str(key): str(value) for key, value in images.items()}


def _runtime_source_revision(value: str | None) -> str:
    if value is None:
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=normal"], cwd=ROOT, text=True,
        )
        if dirty.strip():
            raise ValueError("dirty source tree requires an explicit revision covering the actual image build inputs")
        value = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if len(value) not in {40, 64} or set(value) == {"0"} or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("runtime-source-revision must be a non-placeholder git/content revision")
    return value


def build(args: argparse.Namespace) -> Path:
    output = Path(args.output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"release output already exists and cannot be replaced: {output}")
    output = output.resolve()
    execution_profile = getattr(args, "profile", "engineering")
    recovery_variant = getattr(args, "variant", "baseline")
    mission_mode = args.mission_mode
    if mission_mode == "inspection" and execution_profile != "engineering":
        raise ValueError("inspection mission mode is engineering-only")
    duration_seconds = getattr(args, "duration_seconds", None)
    duration_ns = None if duration_seconds is None else duration_seconds * 1_000_000_000
    images = _images_lock(Path(args.images_lock).resolve() if args.images_lock else None, execution_profile=execution_profile)
    scene_dir = Path(args.scene_dir).resolve()
    mesh_pack = Path(args.mesh_pack).resolve()
    geoid = Path(args.geoid).resolve()
    terrain = Path(args.terrain).resolve()
    sumo_dir = Path(args.sumo_dir).resolve()
    _validate_sumo_inputs(sumo_dir, execution_profile=execution_profile)
    revision_input = args.runtime_source_revision
    if revision_input is None and execution_profile == "engineering":
        revision_input = json.loads(Path(args.images_lock).read_text(encoding="utf-8")).get("runtime_source_revision")
    revision = _runtime_source_revision(revision_input)
    output.mkdir(parents=True)
    assets, _paths, metadata = _world_assets(output, scene_dir=scene_dir, mesh_pack=mesh_pack, geoid=geoid, terrain=terrain, sumo_dir=sumo_dir, execution_profile=execution_profile)
    world = _world_content(output, assets=assets, metadata=metadata, execution_profile=execution_profile)
    world_path = write_json(output, "world/package.json", world.model_dump(mode="json"))
    schemas = _schemas(output)
    configs = _provider_configs(output, world, revision)
    package = _demo_package(output, output / "world/osm/source.osm.json", execution_profile=execution_profile, recovery_variant=recovery_variant, duration_ns=duration_ns)
    task_path, environment_path, agent_paths = _task_environment(root=output, package=package, schemas=schemas, configs=configs, images=images, revision=revision, mission_mode=mission_mode)
    suite = {"schema_version": "aero-bench.suite/v2", "suite_id": "urban.uav.recovery.demo.v1", "aggregator_id": "urban.recovery.verifier", "execution_scope": "executor_validation" if execution_profile == "engineering" else "formal_benchmark", "cases": [{"case_id": f"urban.uav.recovery.demo.{execution_profile}.{recovery_variant}", "task": ref(output, task_path), "environment": ref(output, environment_path), "agents": [ref(output, path) for path in agent_paths], "world_package": ref(output, world_path), "launch_site_ids": ["launch.uav.01"], "seeds": [20260907], "axes": []}]}
    suite_path = write_yaml(output, "suite.yaml", suite)
    runs = resolve_suite(str(suite_path), executor_kind="docker_reference", task_package_resolvers=(UrbanRecoveryTaskPackageResolver(),), provider_registry=builtin_provider_registry())
    if len(runs) != 1:
        raise ValueError("urban recovery source must resolve to exactly one run")
    run = runs[0]
    write_json(output, "resolved-run.json", run.model_dump(mode="json"))
    engineering_ground_lock: dict[str, Any] | None = None
    engineering_ground = metadata.get("engineering_ground")
    if engineering_ground is not None:
        if not isinstance(engineering_ground, dict):
            raise ValueError("engineering ground provenance is malformed")
        ground_plane = engineering_ground.get("ground_plane")
        generated_scene = engineering_ground.get("generated_scene")
        physics = engineering_ground.get("physics")
        assumption = engineering_ground.get("authored_flat_simulation_assumption")
        if not all(
            isinstance(value, dict)
            for value in (ground_plane, generated_scene, physics, assumption)
        ):
            raise ValueError("engineering ground provenance fields are malformed")
        engineering_ground_lock = {
            "provenance_file": ref(
                output,
                output / "world/provenance/engineering-ground.json",
            ),
            "generated_scene_sha256": generated_scene["sha256"],
            "physics": dict(physics),
            "model_name": ground_plane["model_name"],
            "link_name": ground_plane["link_name"],
            "collision_name": ground_plane["collision_name"],
            "geometry": ground_plane["geometry"],
            "extent_enu_m": ground_plane["extent_enu_m"],
            "dimensions_m": ground_plane["dimensions_m"],
            "elevation_enu_up_m": ground_plane["elevation_enu_up_m"],
            "elevation_amsl_m": ground_plane["elevation_amsl_m"],
            "authored_flat_simulation": assumption["declared"],
            "measured_terrain": assumption["measured_terrain"],
        }
    write_json(output, "release-lock.json", {"schema_version": "aero-bench.urban-recovery-release-lock/v1", "package_id": PACKAGE_ID, "world_id": world.world_id, "world_digest": world.world_digest, "world_asset_digest": world.asset_digest, "scenario_digest": run.scenario.scenario_digest, "run_id": run.run_id, "runtime_source_revision": revision, "images": dict(sorted(images.items())), "scene_source_sha256": package.scene_source_sha256, "osm2world_manifest_sha256": digest_file(output / "world/osm2world/manifest.json"), "sumo_toolchain": json.loads((sumo_dir / "toolchain.json").read_text(encoding="utf-8")) if execution_profile == "formal" else None, "engineering_ground": engineering_ground_lock, "execution_profile": execution_profile, "recovery_variant": recovery_variant, "mission_mode": mission_mode, "duration_ns": package.duration_ns, "benchmark_scored": False, "deferred_checks": ["native_physics_audit", "toolchain_audit", "formal_benchmark_scoring", "visual_scoring"] if execution_profile == "engineering" else []})
    write_json(output, "provenance/source-lock.json", {"schema_version": "aero-bench.urban-recovery-source-lock/v1", "source_scene_compiler_manifest": ref(output, scene_dir / "manifest.json") if scene_dir.is_relative_to(output) else {"source": str(scene_dir), "sha256": digest_file(scene_dir / "manifest.json")}, "raw_osm_sha256": package.scene_source_sha256, "effective_osm_sha256": digest_file(output / "world/osm/effective.osm.json"), "mesh_manifest_sha256": digest_file(output / "world/osm2world/manifest.json"), "runtime_source_revision": revision})
    write_bytes(output, ".runtime-source-revision", (revision + "\n").encode())
    write_bytes(output, "README.md", ("# urban.uav-recovery-demo.v1\n\nIndependent Shanghai OSM2World/SUMO/ns-3/PX4/Gazebo release inputs. The run is not a passing demonstration until a real sealed run and an independent verifier report are present.\n").encode())
    print(json.dumps({"output": str(output), "package_id": PACKAGE_ID, "run_id": run.run_id, "scenario_digest": run.scenario.scenario_digest}, sort_keys=True))
    return output


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Shanghai recovery inputs; short unscored engineering execution by default")
    parser.add_argument("--profile", choices=("engineering", "formal"), default="engineering")
    parser.add_argument("--variant", choices=("baseline", "direct-recovery", "no-recovery"), default="baseline")
    parser.add_argument("--mission-mode", choices=("recovery", "inspection"), default="recovery")
    parser.add_argument("--duration-seconds", type=int, help="logical seconds; default 120 engineering / 600 formal")
    parser.add_argument("--scene-dir", default=str(ROOT / "validation/scene-compiler-shanghai-v3"))
    parser.add_argument("--mesh-pack", default=str(ROOT / "frontend/public/osm2world/packs/shanghai-effective-v5"))
    parser.add_argument("--geoid", required=True, help="real bundled geoid correction asset")
    parser.add_argument("--terrain", required=True, help="real bundled terrain height asset")
    parser.add_argument("--sumo-dir", required=True, help="real SUMO network/routes/additional/config directory; formal also requires toolchain.json")
    parser.add_argument("--runtime-source-revision")
    parser.add_argument("--images-lock", required=True, help="immutable image lock: registry digests, or local sha256 config IDs for engineering")
    parser.add_argument("--output", default=str(ROOT / "releases/urban-uav-recovery-demo-v1"))
    return parser


if __name__ == "__main__":
    try:
        build(parser().parse_args())
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser().error(str(exc))
