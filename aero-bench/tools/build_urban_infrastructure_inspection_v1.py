from __future__ import annotations


# ruff: noqa: E402

import argparse
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aero_bench.providers.registry import builtin_provider_registry

from aero_bench.artifacts import seal_manifest_size_upper_bound
from aero_bench.config.models import AgentSpec, EnvironmentSpec, TaskSpec
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.inspection_business.config import InspectionBusinessConfig
from aero_bench.providers.ns3.config import Ns3Config, ns3_capabilities
from aero_bench.providers.ns3.provider import NetworkMailboxObservationPayload
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.providers.px4_gazebo.observations import (
    FlightGnssObservation,
    FlightTelemetryObservation,
    InspectionRgbObservation,
)
from aero_bench.providers.sumo.config import SumoConfig
from aero_bench.providers.sumo.scene import (
    PERSON_COUNT,
    VEHICLE_COUNT,
    materialize_additional,
    materialize_network,
    materialize_routes,
    traffic_signals_geojson,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import InspectionTaskPackage
from aero_bench.tasks.inspection.formal_v2_contracts import (
    FORMAL_V2_COMPONENT_IDS,
    InspectionFormalVerifierConfigV3,
)
from aero_bench.tasks.inspection.integration import InspectionTaskPackageResolver
from aero_bench.world.alignment import GeneratorIdentity, alignment_manifest
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
    NetworkLinkBinding,
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
    TargetGeometry,
    TargetSurfaceNormal,
    UnitDeclaration,
    VerticalDatumBinding,
    WeatherSample,
    WirelessRadioProfile,
    WindVector,
    WorldFrame,
    WorldPackage,
    WorldPose,
    world_package,
)
from aero_bench.world.frames import LocalFrameOrigin
from aero_bench.world.frame_math import EnuTransform, Vector3

SOURCE_URI = "https://github.com/ZhiweiWei-NAMI/AERO_BENCH"
WORLD_ID = "world.urban-infrastructure-inspection-v1"
TASK_ID = "urban.infrastructure.inspection.v1"
PACKAGE_ID = "inspection.v1"
AGENT_ID = "participant.agent"
VERIFIER_ID = "inspection.verifier"
FLIGHT_ID = "flight"
NETWORK_ID = "network"
TRAFFIC_ID = "traffic"
BUSINESS_ID = "business"
UAV_ID = "uav.inspector"
SENSOR_ID = "camera.inspection.front"
SHANGHAI_LATITUDE_DEG = 31.2304
SHANGHAI_LONGITUDE_DEG = 121.4737
REQUIRED_TARGET_IDS = ("target.01", "target.06", "target.08")
ALL_TARGET_IDS = tuple(f"target.{index:02d}" for index in range(1, 9))
LAUNCH_IDS = ("launch.alpha", "launch.bravo")
# Keep the operations endpoint just north of the final target.  At the
# report-send waypoint this gives a short, unobstructed three-dimensional path
# that does not cross the target building or the central no-fly prism.
OPERATIONS_EAST_M = 200.0
OPERATIONS_NORTH_M = 360.0
LAUNCH_ALPHA_EAST_M = -260.0
LAUNCH_ALPHA_NORTH_M = -310.0
LAUNCH_BRAVO_EAST_M = 440.0
LAUNCH_BRAVO_NORTH_M = -430.0
# The PX4 x500 model uses its body reference frame 0.137 m above the
# underside of the lowest collision member. The launch-pad collision top is
# 0.15 m above the ENU terrain plane, so this reference rests on the pad
# without interpenetrating it.
UAV_LAUNCH_REFERENCE_UP_M = 0.137
LICENSE_ID = "license.cc0-1.0"
LICENSE_PATH = "licenses/cc0-1.0.txt"
FORMAL_SCHEMA_VERSION = "aero-bench.inspection-verifier/v3"
# The authoritative event ledger carries one canonical StateSample event for
# every resolved entity at every barrier, plus the corresponding Provider and
# Gateway events.  A measured full-tick upper envelope is 350,000 bytes; the
# fixed command, identity, and bounded process-stream envelope is 32 MiB.
# Keep this arithmetic next to the generated inputs so a clock extension cannot
# silently make the declared event artifact impossible to seal.
FORMAL_MAX_STEPS = 2_000
EVENT_BYTES_PER_TICK_BOUND = 350_000
EVENT_FIXED_OVERHEAD_BYTES = 32 * 1024 * 1024
EVENT_IMAGE_OBSERVATION_COUNT_BOUND = 12
# A 640 x 480 RGB image is at most 921,600 uncompressed bytes.  Two MiB covers
# canonical base64, observation metadata, and its bounded ledger envelope.
EVENT_IMAGE_BYTES_PER_OBSERVATION_BOUND = 2 * 1024 * 1024
EVENT_LOG_MAX_SIZE_BYTES = 768 * 1024 * 1024
RUNNER_INPUT_VOLUME_SIZE_BYTES = 2 * 1024 * 1024 * 1024
# Runtime artifacts include a 768 MiB event ledger, 256 MiB scene history,
# 256 MiB exact camera-frame archive, and all Provider/Agent evidence.  The
# declared maxima exceed 1 GiB even though a normal run is much smaller.
RUNNER_ARTIFACT_VOLUME_SIZE_BYTES = 2 * 1024 * 1024 * 1024
# EventLedger validation keeps the typed records in memory because the
# independent verifier must cross-reference arbitrary causal/event sequences.
# A real 418,788,137-byte sealed ledger measured a 2.8 GiB peak during strict
# parsing; 8 GiB leaves bounded headroom for the typed evidence and projection
# without changing any acceptance rule.
VERIFIER_MEMORY_MIB = 8192
# The real participant emits a 1,513-byte aggregate report.  The link bound
# models the protocol's minimum frame payload, so keep a 1 KiB lower bound with
# explicit room for the canonical report envelope rather than declaring an
# impossible 2 KiB minimum.
MINIMUM_REPORT_PAYLOAD_BYTES = 1024

# The Agent acceptance release is a separate revision of this Shanghai task.
# Keeping the profile explicit lets the historical urban-infrastructure source
# continue to rebuild byte-for-byte while the acceptance builder below can
# require real pixels, a negative target, and API discovery.
AGENT_ACCEPTANCE_PROFILE = "agent_inspection_v1"
AGENT_ACCEPTANCE_OUTPUT = REPO_ROOT / "releases/agent-inspection-v1"
AGENT_TASK_ID = "agent.infrastructure.inspection.v1"
# InspectionTaskPackageResolver is the single strict resolver for this package
# contract.  The separate task/suite/profile identities distinguish acceptance
# without inventing an unsupported package parser identity.
AGENT_PACKAGE_ID = PACKAGE_ID
AGENT_ENVIRONMENT_ID = "agent.infrastructure.inspection.v1"
AGENT_SUITE_ID = "agent.infrastructure.inspection.v1"
AGENT_CASE_ID = "agent.infrastructure.inspection.formal"
AGENT_DRIVER_ID = "astra.driver"
AGENT_DRIVER_BRIDGE_PORT = 17_731
AGENT_MODEL = "gpt-6-astra"
AGENT_REASONING_EFFORT = "low"
# One function call per model response, so the request budget is tool calls
# plus one final response. 144/145 covers the measured candidate09 prefix,
# remaining return/land/disarm and delivery-status confirmation, and a
# 16-call margin. Session wall, runner, and PX4 heartbeat stay measured.
AGENT_MAX_MODEL_REQUESTS = 145
AGENT_MAX_TOOL_CALLS = 144
AGENT_MAX_IMAGE_OBSERVATIONS = 12
AGENT_MODEL_CALL_TIMEOUT_S = 180
AGENT_SESSION_WALL_TIMEOUT_S = 21_600
AGENT_RUNNER_RUNTIME_TIMEOUT_S = 22_800
AGENT_VERIFIER_TIMEOUT_S = 600
AGENT_MAX_OUTPUT_TOKENS = 8_192
AGENT_INITIAL_INPUT = (
    "Execute the authorized infrastructure inspection task to completion now. "
    "Use only the declared functions, discover every work order through the "
    "authorized query API, inspect each target from real camera pixels, deliver "
    "the exact report artifact, and finish landed, stopped, and disarmed."
)
CODEX_CLI_VERSION = "0.153.4"
CODEX_BINARY_SHA256 = (
    "56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da"
)

# Target-model identities are deliberately independent of both target and work
# order suffixes.  The two positive classes describe what is visible in the
# pixels; the clean panel is the required negative example.
AGENT_TARGET_ASSET_BY_ID = {
    "target.01": "asset.inspection-panel-upper",
    "target.06": "asset.inspection-panel-clean",
    "target.08": "asset.inspection-panel-lower",
}
AGENT_DEFECT_BY_TARGET_ID = {
    "target.01": "defect.surface-damage.upper",
    "target.08": "defect.surface-damage.lower",
}

CAMERA_MOUNT_FORWARD_M = 0.12
CAMERA_MOUNT_LEFT_M = 0.03
CAMERA_MOUNT_UP_M = 0.242
NOMINAL_STANDOFF_M = 10.0
MAX_STANDOFF_M = 12.0
CAMERA_HORIZONTAL_FOV_DEG = 80.0
CAMERA_VERTICAL_FOV_DEG = 60.0
CAMERA_WIDTH_PX = 640
CAMERA_HEIGHT_PX = 480
DEFECT_MINIMUM_DIMENSION_M = 0.22
PIXEL_PITCH_MODEL_M = 3.0e-6
# Equivalent focal length for the declared 640 px / 80 degree horizontal
# pinhole camera.  This replaces the physically inconsistent historical 20 mm
# value while retaining the InspectionBoundSpec focal-length representation.
CAMERA_EQUIVALENT_FOCAL_LENGTH_M = (
    CAMERA_WIDTH_PX
    / (2.0 * math.tan(math.radians(CAMERA_HORIZONTAL_FOV_DEG) / 2.0))
    * PIXEL_PITCH_MODEL_M
)


def _required_target_surface_positions_enu_m() -> dict[str, tuple[float, float, float]]:
    """Return the resolved panel centers from the declared building geometry."""

    building_by_id = {
        building_id: (index, east, north)
        for index, (building_id, east, north) in enumerate(BUILDING_CENTERS, start=1)
    }
    result: dict[str, tuple[float, float, float]] = {}
    for target_id in REQUIRED_TARGET_IDS:
        building_id = TARGET_BUILDINGS[target_id]
        index, east, north = building_by_id[building_id]
        size_x, _size_y, _size_z = _building_dimensions(index)
        result[target_id] = (east - size_x / 2.0 - 0.08, north, 12.0)
    return result


def _mission_distance_lower_bound_m() -> float:
    """Prove a route-distance lower bound without assuming a flight policy.

    Each qualifying camera position lies no farther than 12 m from its target.
    Applying the triangle inequality to the declared ordered mission therefore
    yields a lower bound for every feasible launch/visit/return trajectory.  It
    intentionally ignores obstacles, acceleration and dwell, which can only
    increase the actual route or elapsed time.
    """

    launch = (
        LAUNCH_ALPHA_EAST_M,
        LAUNCH_ALPHA_NORTH_M,
        UAV_LAUNCH_REFERENCE_UP_M,
    )
    surfaces = _required_target_surface_positions_enu_m()
    points = (launch, *(surfaces[target_id] for target_id in REQUIRED_TARGET_IDS), launch)
    radii = (0.0, *(MAX_STANDOFF_M for _ in REQUIRED_TARGET_IDS), 0.0)
    return sum(
        max(0.0, math.dist(start, end) - radii[index] - radii[index + 1])
        for index, (start, end) in enumerate(zip(points, points[1:]))
    )

if (
    EVENT_BYTES_PER_TICK_BOUND * FORMAL_MAX_STEPS
    + EVENT_FIXED_OVERHEAD_BYTES
    + EVENT_IMAGE_OBSERVATION_COUNT_BOUND
    * EVENT_IMAGE_BYTES_PER_OBSERVATION_BOUND
    > EVENT_LOG_MAX_SIZE_BYTES
):
    raise RuntimeError(
        "formal event-log capacity is below its explicit barrier upper bound"
    )
if EVENT_IMAGE_OBSERVATION_COUNT_BOUND != AGENT_MAX_IMAGE_OBSERVATIONS:
    raise RuntimeError(
        "event-log image bound differs from the Agent session policy"
    )
if AGENT_MAX_MODEL_REQUESTS != AGENT_MAX_TOOL_CALLS + 1:
    raise RuntimeError(
        "model request budget must be one greater than the tool-call budget"
    )
if AGENT_RUNNER_RUNTIME_TIMEOUT_S <= AGENT_SESSION_WALL_TIMEOUT_S:
    raise RuntimeError(
        "runner runtime timeout must exceed the Agent session wall"
    )

BUILDING_CENTERS = tuple(
    (f"building.{index:02d}", east, north)
    for index, (east, north) in enumerate(
        (
            (east, north)
            # The formal world remains 1 km x 1 km.  The declared inspection
            # work is deliberately concentrated in a smaller central grid so
            # route length reflects the benchmark task rather than empty
            # background geography.
            for north in (-260.0, -160.0, -60.0, 40.0, 140.0, 240.0)
            for east in (-200.0, 0.0, 200.0)
        ),
        start=1,
    )
)
TARGET_BUILDINGS = {
    "target.01": "building.01",
    "target.02": "building.03",
    "target.03": "building.06",
    "target.04": "building.08",
    "target.05": "building.10",
    "target.06": "building.12",
    "target.07": "building.15",
    "target.08": "building.18",
}

BUILDING_TYPES = (
    "residential",
    "commercial",
    "office",
    "industrial",
    "public",
    "logistics",
)


def _building_type(index: int) -> str:
    """Return the deterministic Shanghai urban land-use class for a building."""

    return BUILDING_TYPES[(index - 1) % len(BUILDING_TYPES)]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _managed_source_closure() -> tuple[str, list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    for base in ("aero_bench", "containers"):
        for path in sorted((REPO_ROOT / base).rglob("*")):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or path.suffix in {".pyc", ".pyo"}
            ):
                continue
            payload = path.read_bytes()
            records.append(
                {
                    "path": path.relative_to(REPO_ROOT).as_posix(),
                    "sha256": _sha256_bytes(payload),
                    "size_bytes": len(payload),
                }
            )
    return _sha256_bytes(canonical_json_bytes(records)), records


def _ref(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}


def _write_bytes(root: Path, relative: str, value: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def _write_text(root: Path, relative: str, value: str) -> Path:
    return _write_bytes(root, relative, value.encode("utf-8"))


def _write_json(root: Path, relative: str, value: object) -> Path:
    return _write_bytes(root, relative, canonical_json_bytes(value) + b"\n")


def _write_yaml(root: Path, relative: str, value: object) -> Path:
    return _write_text(root, relative, yaml.safe_dump(value, sort_keys=False))


def _file_ref(root: Path, path: Path) -> dict[str, str]:
    return _ref(root, path)


def _object_schema(
    properties: dict[str, object], required: list[str]
) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _agent_artifact_schemas(root: Path) -> Path:
    """Publish output shapes without embedding any task-specific answer."""

    identifier = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"}
    sha256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    detection_properties = {
        "schema_version": {"const": "aero-bench.inspection-detection/v1"},
        "source_artifact_id": {"const": "artifact.detections"},
        "run_id": sha256,
        "work_order_id": identifier,
        "observation_id": identifier,
        "frame_id": identifier,
        "image_sha256": sha256,
        "defect_id": identifier,
        "target_id": identifier,
    }
    report_detection_properties = {
        "defect_id": identifier,
        "target_id": identifier,
        "frame_id": identifier,
        "image_sha256": sha256,
    }
    report_properties = {
        "schema_version": {"const": "aero-bench.inspection-report/v2"},
        "source_artifact_id": {"const": "artifact.report"},
        "run_id": sha256,
        "work_order_id": identifier,
        "observation_id": identifier,
        "observation_payload_digest": sha256,
        "detection_payload_digest": sha256,
        "detections": {
            "type": "array",
            "items": _object_schema(
                report_detection_properties, list(report_detection_properties)
            ),
        },
    }
    document = {
        "schema_version": "aero-bench.agent-artifact-schemas/v1",
        "artifact_schemas": {
            "artifact.detections": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": _object_schema(
                    detection_properties, list(detection_properties)
                ),
            },
            "artifact.report": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": _object_schema(report_properties, list(report_properties)),
            },
        },
    }
    return _write_json(root, "assets/agent-artifact-schemas.json", document)


def _agent_driver_config(root: Path) -> Path:
    return _write_json(
        root,
        "configs/agent-driver.json",
        {
            "schema_version": "aero-bench.agent-driver-config/v1",
            "policy": {
                "schema_version": "aero-bench.agent-session-policy/v1",
                "model": AGENT_MODEL,
                "reasoning_effort": AGENT_REASONING_EFFORT,
                "max_model_requests": AGENT_MAX_MODEL_REQUESTS,
                "max_tool_calls": AGENT_MAX_TOOL_CALLS,
                "max_image_observations": AGENT_MAX_IMAGE_OBSERVATIONS,
                "model_call_timeout_s": AGENT_MODEL_CALL_TIMEOUT_S,
                "session_wall_timeout_s": AGENT_SESSION_WALL_TIMEOUT_S,
                "max_output_tokens": AGENT_MAX_OUTPUT_TOKENS,
            },
            "initial_input": AGENT_INITIAL_INPUT,
            "codex_cli_version": CODEX_CLI_VERSION,
            "codex_binary_sha256": CODEX_BINARY_SHA256,
        },
    )


def _osm_building(east: float, north: float, size_x: float, size_y: float, height: float) -> dict[str, object]:
    transform = EnuTransform.from_origin(
        longitude_deg=SHANGHAI_LONGITUDE_DEG,
        latitude_deg=SHANGHAI_LATITUDE_DEG,
        altitude_m=50.0,
    )
    nodes = []
    for index, (dx, dy) in enumerate(((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)), start=1):
        longitude, latitude, _altitude = transform.enu_to_geodetic(Vector3(east + dx * size_x, north + dy * size_y, 0.0))
        nodes.append({"type": "node", "id": -index, "lat": latitude, "lon": longitude})
    return {
        "version": 0.6,
        "generator": "AERO-BENCH declared simulation geometry",
        "elements": [*nodes, {"type": "way", "id": -1, "nodes": [-1, -2, -3, -4, -1], "tags": {"building": "yes", "height": str(height)}}],
    }


def _osm_scene() -> dict[str, object]:
    transform = EnuTransform.from_origin(longitude_deg=SHANGHAI_LONGITUDE_DEG, latitude_deg=SHANGHAI_LATITUDE_DEG, altitude_m=50.0)
    elements: list[dict[str, object]] = []
    node_ids: dict[tuple[float, float], int] = {}

    def node(east: float, north: float) -> int:
        key = (east, north)
        if key not in node_ids:
            identifier = -(len(node_ids) + 1)
            node_ids[key] = identifier
            longitude, latitude, _ = transform.enu_to_geodetic(Vector3(east, north, 0.0))
            elements.append({"type": "node", "id": identifier, "lat": latitude, "lon": longitude})
        return node_ids[key]

    way_id = -1
    for index, (building_id, east, north) in enumerate(BUILDING_CENTERS, start=1):
        width, depth, height = _building_dimensions(index)
        nodes = [node(east + dx * width, north + dy * depth) for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))]
        elements.append({"type": "way", "id": way_id, "nodes": [*nodes, nodes[0]], "tags": {"building": "yes", "height": str(height), "aero:building_id": building_id}})
        way_id -= 1
    highway = {"vehicle_lane": "residential", "pedestrian_path": "footway", "service_road": "service"}
    for road in _roads():
        elements.append({"type": "way", "id": way_id, "nodes": [node(point.east_m, point.north_m) for point in road.centerline_enu_m], "tags": {"highway": highway[road.kind], "width": str(road.width_m), "aero:road_id": road.road_id}})
        way_id -= 1
    corners = [transform.enu_to_geodetic(Vector3(east, north, 0)) for east, north in ((-500, -500), (500, 500))]
    return {"version": 0.6, "generator": "AERO-BENCH declared simulation geometry", "bounds": {"minlat": corners[0][1], "minlon": corners[0][0], "maxlat": corners[1][1], "maxlon": corners[1][0]}, "elements": elements}


def _obj_box(size_x: float, size_y: float, size_z: float) -> str:
    x = size_x / 2.0
    y = size_y / 2.0
    vertices = (
        (-x, -y, 0.0),
        (x, -y, 0.0),
        (x, y, 0.0),
        (-x, y, 0.0),
        (-x, -y, size_z),
        (x, -y, size_z),
        (x, y, size_z),
        (-x, y, size_z),
    )
    faces = (
        (1, 3, 2),
        (1, 4, 3),
        (5, 6, 7),
        (5, 7, 8),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 4, 8),
        (3, 8, 7),
        (4, 1, 5),
        (4, 5, 8),
    )
    return "# deterministic AERO-BENCH collision box\n" + "".join(
        f"v {x_value:.6f} {y_value:.6f} {z_value:.6f}\n"
        for x_value, y_value, z_value in vertices
    ) + "".join(f"f {a} {b} {c}\n" for a, b, c in faces)


def _building_dimensions(index: int) -> tuple[float, float, float]:
    return (60.0 + float((index % 3) * 4), 48.0 + float((index % 2) * 6), 24.0 + float((index % 5) * 5))


def _gazebo_box_model(
    name: str,
    east: float,
    north: float,
    size_x: float,
    size_y: float,
    size_z: float,
    color: str,
) -> str:
    return f"""
    <model name=\"{name}\">
      <static>true</static>
      <pose>{east:.6f} {north:.6f} {size_z / 2.0:.6f} 0 0 0</pose>
      <link name=\"body\">
        <collision name=\"collision\"><geometry><box><size>{size_x:.6f} {size_y:.6f} {size_z:.6f}</size></box></geometry></collision>
        <visual name=\"visual\"><geometry><box><size>{size_x:.6f} {size_y:.6f} {size_z:.6f}</size></box></geometry><material><ambient>{color}</ambient><diffuse>{color}</diffuse></material></visual>
      </link>
    </model>"""


def _gazebo_world() -> str:
    models = []
    for index, (building_id, east, north) in enumerate(BUILDING_CENTERS, start=1):
        size_x, size_y, size_z = _building_dimensions(index)
        tone = 0.38 + (index % 4) * 0.06
        models.append(
            _gazebo_box_model(
                building_id,
                east,
                north,
                size_x,
                size_y,
                size_z,
                f"{tone:.3f} {tone + 0.04:.3f} {tone + 0.08:.3f} 1",
            )
        )
    models.extend(
        (
            _gazebo_box_model("launch.alpha", LAUNCH_ALPHA_EAST_M, LAUNCH_ALPHA_NORTH_M, 8.0, 8.0, 0.15, "0.12 0.35 0.72 1"),
            _gazebo_box_model("launch.bravo", LAUNCH_BRAVO_EAST_M, LAUNCH_BRAVO_NORTH_M, 8.0, 8.0, 0.15, "0.12 0.35 0.72 1"),
            _gazebo_box_model("station.operations", OPERATIONS_EAST_M, OPERATIONS_NORTH_M, 8.0, 8.0, 12.0, "0.22 0.62 0.34 1"),
        )
    )
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<sdf version=\"1.10\">
  <world name=\"urban_inspection\">
    <paused>true</paused>
    <gravity>0 0 -9.80665</gravity>
    <magnetic_field>2.2e-5 0 4.2e-5</magnetic_field>
    <atmosphere type=\"adiabatic\"/>
    <wind><linear_velocity>1.5 -0.4 0</linear_velocity></wind>
    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>{SHANGHAI_LATITUDE_DEG:.4f}</latitude_deg>
      <longitude_deg>{SHANGHAI_LONGITUDE_DEG:.4f}</longitude_deg>
      <elevation>20.0</elevation>
      <heading_deg>0</heading_deg>
    </spherical_coordinates>
    <physics name=\"deterministic_4ms\" type=\"ignored\"><max_step_size>0.004</max_step_size><real_time_factor>0</real_time_factor></physics>
    <plugin filename=\"gz-sim-physics-system\" name=\"gz::sim::systems::Physics\"/>
    <plugin filename=\"gz-sim-contact-system\" name=\"gz::sim::systems::Contact\"/>
    <plugin filename=\"gz-sim-user-commands-system\" name=\"gz::sim::systems::UserCommands\"/>
    <plugin filename=\"gz-sim-scene-broadcaster-system\" name=\"gz::sim::systems::SceneBroadcaster\"/>
    <plugin filename=\"gz-sim-imu-system\" name=\"gz::sim::systems::Imu\"/>
    <plugin filename=\"gz-sim-air-pressure-system\" name=\"gz::sim::systems::AirPressure\"/>
    <plugin filename=\"gz-sim-air-speed-system\" name=\"gz::sim::systems::AirSpeed\"/>
    <plugin filename=\"gz-sim-magnetometer-system\" name=\"gz::sim::systems::Magnetometer\"/>
    <plugin filename=\"gz-sim-navsat-system\" name=\"gz::sim::systems::NavSat\"/>
    <plugin filename=\"gz-sim-sensors-system\" name=\"gz::sim::systems::Sensors\"><render_engine>ogre2</render_engine></plugin>
    <!-- Shadow-map rendering starves the paused lockstep worker in the full
         urban scene. Keep all physical and visual geometry, but use the
         deterministic ambient scene required by the provider barrier. -->
    <scene><ambient>0.55 0.55 0.55 1</ambient><background>0.70 0.82 0.94 1</background><shadows>false</shadows></scene>
    <light type=\"directional\" name=\"sun\"><cast_shadows>true</cast_shadows><pose>0 0 500 0 0 0</pose><diffuse>0.9 0.9 0.9 1</diffuse><specular>0.2 0.2 0.2 1</specular><direction>-0.4 0.2 -0.9</direction></light>
    <model name=\"ground\"><static>true</static><link name=\"ground_link\"><collision name=\"ground_collision\"><geometry><plane><normal>0 0 1</normal><size>1000 1000</size></plane></geometry></collision><visual name=\"ground_visual\"><geometry><plane><normal>0 0 1</normal><size>1000 1000</size></plane></geometry><material><ambient>0.34 0.39 0.32 1</ambient><diffuse>0.34 0.39 0.32 1</diffuse></material></visual></link></model>
""" + "\n".join(models) + """
  </world>
</sdf>
"""


def _inspection_target_sdf(*, patch_vertical: str = "historical") -> str:
    if patch_vertical not in {"historical", "upper", "lower", "clean"}:
        raise ValueError(f"unsupported inspection target surface: {patch_vertical}")
    patch_pose = {
        "historical": "-0.046 0.18 0.12 0 0 0",
        "upper": "-0.046 0 0.18 0 0 0",
        "lower": "-0.046 0 -0.18 0 0 0",
    }.get(patch_vertical)
    patch = (
        ""
        if patch_pose is None
        else (
            f'      <visual name="surface_damage_patch"><pose>{patch_pose}</pose>'
            '<geometry><box><size>0.012 0.24 0.22</size></box></geometry>'
            '<material><ambient>0.01 0.01 0.01 1</ambient>'
            '<diffuse>0.01 0.01 0.01 1</diffuse>'
            '<specular>0 0 0 1</specular></material></visual>\n'
        )
    )
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<sdf version=\"1.10\">
  <model name=\"inspection_target_panel\">
    <static>true</static>
    <link name=\"panel\">
      <collision name=\"panel_collision\"><geometry><box><size>0.08 1.00 0.80</size></box></geometry></collision>
      <visual name=\"panel_surface\"><geometry><box><size>0.08 1.00 0.80</size></box></geometry><material><ambient>0.85 0.20 0.12 1</ambient><diffuse>0.85 0.20 0.12 1</diffuse><specular>0.05 0.05 0.05 1</specular></material></visual>
""" + patch + """\
    </link>
  </model>
</sdf>
"""


def _sumo_network() -> str:
    return materialize_network().xml.decode("utf-8")

def _sumo_routes() -> str:
    return materialize_routes()


def _sumo_additional() -> str:
    return materialize_additional()


def _sumo_config() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<configuration>
  <input><net-file value=\"network.net.xml\"/><route-files value=\"routes.rou.xml\"/><additional-files value=\"additional.add.xml\"/></input>
  <time><begin value=\"0\"/><end value=\"600\"/><step-length value=\"0.5\"/></time>
  <processing><time-to-teleport value=\"-1\"/></processing>
  <report><no-step-log value=\"true\"/><duration-log.disable value=\"true\"/><no-warnings value=\"true\"/></report>
</configuration>
"""


def _asset_record(
    *,
    root: Path,
    asset_id: str,
    relative_path: str,
    role: str,
    media_type: str,
    visibility: str,
    source_frame: str,
    license_ref: ArtifactSelector,
    provider_ids: tuple[str, ...] = (),
    height_precision_m: float | None = None,
) -> AssetRecord:
    path = root / relative_path
    size = path.stat().st_size
    audiences = (
        (AssetAudience(audience_kind="public", audience_id="all"),)
        if visibility == "public"
        else tuple(
            AssetAudience(audience_kind="provider", audience_id=provider_id)
            for provider_id in provider_ids
        )
    )
    units = [UnitDeclaration(quantity="file_size", unit="B")]
    precision = [
        PrecisionMetadata(
            quantity="file_size",
            kind="exact_bytes",
            unit="B",
            exact_bytes=size,
            absolute_tolerance=None,
        )
    ]
    if height_precision_m is not None:
        units.append(UnitDeclaration(quantity="height", unit="m"))
        precision.append(
            PrecisionMetadata(
                quantity="height",
                kind="absolute_tolerance",
                unit="m",
                exact_bytes=None,
                absolute_tolerance=height_precision_m,
            )
        )
    return AssetRecord(
        artifact=ArtifactSelector(
            artifact_id=asset_id,
            selector=relative_path,
            sha256=_sha256(path),
        ),
        asset_role=role,
        byte_size=size,
        media_type=media_type,
        visibility=visibility,
        audiences=tuple(audiences),
        units=tuple(units),
        source_frame=source_frame,
        precision=tuple(precision),
        provenance=AssetProvenance(
            source_kind="bundled_offline",
            recorded_by="aero-bench",
            source_dataset="synthetic-urban-infrastructure-v1",
            source_version="2026.09.02",
            runtime_download=False,
        ),
        license_id="CC0-1.0",
        license_file=license_ref,
    )


def _materialize_world_assets(root: Path) -> tuple[AssetRecord, ...]:
    license_path = _write_text(
        root,
        LICENSE_PATH,
        "CC0 1.0 Universal\n\nThe synthetic assets in this release are dedicated to the public domain.\n",
    )
    license_ref = ArtifactSelector(
        artifact_id=LICENSE_ID,
        selector=LICENSE_PATH,
        sha256=_sha256(license_path),
    )
    geoid = {
        "schema_version": "aero-bench.scalar-grid/v1",
        "frame_id": "ENU",
        "east_axis_m": [-500.0, 0.0, 500.0],
        "north_axis_m": [-500.0, 0.0, 500.0],
        "values_m": [[30.0, 30.0, 30.0], [30.0, 30.0, 30.0], [30.0, 30.0, 30.0]],
    }
    terrain = {
        "schema_version": "aero-bench.scalar-grid/v1",
        "frame_id": "ENU",
        "east_axis_m": [-500.0, 0.0, 500.0],
        "north_axis_m": [-500.0, 0.0, 500.0],
        "values_m": [[20.0, 20.0, 20.0], [20.0, 20.0, 20.0], [20.0, 20.0, 20.0]],
    }
    _write_json(root, "world/geoid/grid.json", geoid)
    _write_json(root, "world/terrain/heights.json", terrain)
    _write_text(root, "world/gazebo/urban-inspection.sdf", _gazebo_world())
    sumo_network = materialize_network()
    _write_bytes(root, "world/sumo/network.net.xml", sumo_network.xml)
    _write_text(root, "world/sumo/routes.rou.xml", materialize_routes(sumo_network))
    _write_text(root, "world/sumo/additional.add.xml", materialize_additional(sumo_network))
    _write_json(
        root,
        "world/layers/traffic-signals.geojson",
        traffic_signals_geojson(sumo_network),
    )
    _write_text(root, "world/sumo/simulation.sumocfg", _sumo_config())

    building_features = []
    for index, (building_id, east, north) in enumerate(BUILDING_CENTERS, start=1):
        size_x, size_y, size_z = _building_dimensions(index)
        geometry = {
            "building_id": building_id,
            "building_type": _building_type(index),
            "center_enu_m": [east, north, 0.0],
            "size_m": [size_x, size_y, size_z],
            "frame_id": "ENU",
        }
        _write_json(root, f"world/buildings/{building_id}.source.json", geometry)
        _write_json(
            root,
            f"world/buildings/{building_id}.osm.json",
            _osm_building(east, north, size_x, size_y, size_z),
        )
        _write_text(
            root,
            f"world/buildings/{building_id}.collision.obj",
            _obj_box(size_x, size_y, size_z),
        )
        half_x = size_x / 2.0
        half_y = size_y / 2.0
        building_features.append(
            {
                "type": "Feature",
                "id": building_id,
                "properties": {
                    "building_id": building_id,
                    "building_type": _building_type(index),
                    "height_m": size_z,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [east - half_x, north - half_y],
                        [east + half_x, north - half_y],
                        [east + half_x, north + half_y],
                        [east - half_x, north + half_y],
                        [east - half_x, north - half_y],
                    ]],
                },
            }
        )

    roads = _roads()
    regions = _regions()
    layers = {
        "buildings": {"type": "FeatureCollection", "features": building_features},
        "roads": {
            "schema_version": "aero-bench.road-layer/v2",
            "traffic_lights": traffic_signals_geojson(sumo_network)["features"],
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": road.road_id,
                    "properties": {"road_id": road.road_id, "kind": road.kind, "width_m": road.width_m},
                    "geometry": {"type": "LineString", "coordinates": [[point.east_m, point.north_m] for point in road.centerline_enu_m]},
                }
                for road in roads
            ],
        },
        "regions": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": region.region_id,
                    "properties": {"region_id": region.region_id, "kind": region.kind},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            *[[point.east_m, point.north_m] for point in region.geometry.footprint_enu_m],
                            [region.geometry.footprint_enu_m[0].east_m, region.geometry.footprint_enu_m[0].north_m],
                        ]],
                    },
                }
                for region in regions
            ],
        },
        "entities": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": UAV_ID, "properties": {"entity_id": UAV_ID, "kind": "uav"}, "geometry": {"type": "Point", "coordinates": [LAUNCH_ALPHA_EAST_M, LAUNCH_ALPHA_NORTH_M, UAV_LAUNCH_REFERENCE_UP_M]}},
                {"type": "Feature", "id": "station.operations", "properties": {"entity_id": "station.operations", "kind": "static_asset"}, "geometry": {"type": "Point", "coordinates": [OPERATIONS_EAST_M, OPERATIONS_NORTH_M, 0.0]}},
                *[
                    {
                        "type": "Feature",
                        "id": signal["id"],
                        "properties": {
                            **signal["properties"],
                            "kind": "traffic_light",
                        },
                        "geometry": signal["geometry"],
                    }
                    for signal in traffic_signals_geojson(sumo_network)["features"]
                ],
            ],
        },
        "missions": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": target_id, "properties": {"target_id": target_id, "required": target_id in REQUIRED_TARGET_IDS}, "geometry": None}
                for target_id in ALL_TARGET_IDS
            ],
        },
    }
    for name, document in layers.items():
        _write_json(root, f"world/layers/{name}.geojson", document)

    _write_json(root, "world/scene.osm.json", _osm_scene())

    model_specs = {
        "uav": (1.0, 1.0, 0.35),
        "vehicle": (4.5, 1.8, 1.5),
        "pedestrian": (0.5, 0.5, 1.75),
        "station": (8.0, 8.0, 12.0),
        "pad": (8.0, 8.0, 0.15),
    }
    for name, dimensions in model_specs.items():
        _write_json(root, f"world/models/{name}.json", {
            "schema_version": "aero-bench.entity-symbol/v1",
            "symbol": name,
            "dimensions_m": list(dimensions),
            "frame_id": "asset_local",
        })

    records = [
        _asset_record(root=root, asset_id="asset.osm-scene", relative_path="world/scene.osm.json", role="layer_tiles", media_type="application/json", visibility="public", source_frame="WGS84", license_ref=license_ref),
        _asset_record(root=root, asset_id="asset.geoid", relative_path="world/geoid/grid.json", role="geoid_model", media_type="application/json", visibility="public", source_frame="raster_pixel", license_ref=license_ref, height_precision_m=0.01),
        _asset_record(root=root, asset_id="asset.terrain-heights", relative_path="world/terrain/heights.json", role="terrain_model", media_type="application/json", visibility="public", source_frame="raster_pixel", license_ref=license_ref, height_precision_m=0.05),
        _asset_record(root=root, asset_id="asset.gazebo-scene", relative_path="world/gazebo/urban-inspection.sdf", role="other", media_type="application/vnd.gazebo.sdf+xml", visibility="private", source_frame="ENU", license_ref=license_ref, provider_ids=(FLIGHT_ID,)),
        _asset_record(root=root, asset_id="asset.sumo-config", relative_path="world/sumo/simulation.sumocfg", role="sumo_config", media_type="application/xml", visibility="private", source_frame="non_spatial", license_ref=license_ref, provider_ids=(TRAFFIC_ID,)),
        _asset_record(root=root, asset_id="asset.sumo-network", relative_path="world/sumo/network.net.xml", role="sumo_network", media_type="application/xml", visibility="private", source_frame="sumo_net", license_ref=license_ref, provider_ids=(TRAFFIC_ID,)),
        _asset_record(root=root, asset_id="asset.sumo-routes", relative_path="world/sumo/routes.rou.xml", role="sumo_routes", media_type="application/xml", visibility="private", source_frame="non_spatial", license_ref=license_ref, provider_ids=(TRAFFIC_ID,)),
        _asset_record(root=root, asset_id="asset.sumo-additional", relative_path="world/sumo/additional.add.xml", role="sumo_additional", media_type="application/xml", visibility="private", source_frame="non_spatial", license_ref=license_ref, provider_ids=(TRAFFIC_ID,)),
    ]
    for name in layers:
        records.append(
            _asset_record(root=root, asset_id=f"asset.layer-{name}", relative_path=f"world/layers/{name}.geojson", role="layer_tiles", media_type="application/geo+json", visibility="public", source_frame="ENU", license_ref=license_ref)
        )
    for name in model_specs:
        records.append(
            _asset_record(root=root, asset_id=f"asset.model-{name}", relative_path=f"world/models/{name}.json", role="entity_model", media_type="application/json", visibility="public", source_frame="asset_local", license_ref=license_ref)
        )
    for building_id, _east, _north in BUILDING_CENTERS:
        records.extend(
            (
                _asset_record(root=root, asset_id=f"asset.{building_id}-source", relative_path=f"world/buildings/{building_id}.source.json", role="building_source", media_type="application/json", visibility="private", source_frame="asset_local", license_ref=license_ref, provider_ids=(FLIGHT_ID,)),
                _asset_record(root=root, asset_id=f"asset.{building_id}-render", relative_path=f"world/buildings/{building_id}.osm.json", role="building_render", media_type="application/json", visibility="public", source_frame="WGS84", license_ref=license_ref),
                _asset_record(root=root, asset_id=f"asset.{building_id}-collision", relative_path=f"world/buildings/{building_id}.collision.obj", role="building_collision", media_type="model/obj", visibility="private", source_frame="asset_local", license_ref=license_ref, provider_ids=(FLIGHT_ID,)),
            )
        )
    return tuple(records)


def _roads() -> tuple[RoadSpec, ...]:
    roads: list[RoadSpec] = []
    for index, north in enumerate((-420.0, -280.0, -140.0, 0.0, 140.0, 280.0, 420.0), start=1):
        roads.append(RoadSpec(road_id=f"road.east-west.{index:02d}", kind="vehicle_lane", centerline_enu_m=(EnuPoint(east_m=-480.0, north_m=north), EnuPoint(east_m=480.0, north_m=north)), width_m=8.0))
    for index, east in enumerate((-420.0, -150.0, 150.0, 420.0), start=1):
        roads.append(RoadSpec(road_id=f"road.north-south.{index:02d}", kind="vehicle_lane", centerline_enu_m=(EnuPoint(east_m=east, north_m=-480.0), EnuPoint(east_m=east, north_m=480.0)), width_m=8.0))
    roads.append(RoadSpec(road_id="road.pedestrian.central", kind="pedestrian_path", centerline_enu_m=(EnuPoint(east_m=-450.0, north_m=20.0), EnuPoint(east_m=450.0, north_m=20.0)), width_m=3.0))
    return tuple(roads)


def _regions() -> tuple[RegionSpec, ...]:
    return (
        RegionSpec(
            region_id="region.formal-geofence",
            kind="geofence",
            geometry=RegionGeometry(
                footprint_enu_m=(EnuPoint(east_m=-480.0, north_m=-480.0), EnuPoint(east_m=480.0, north_m=-480.0), EnuPoint(east_m=480.0, north_m=480.0), EnuPoint(east_m=-480.0, north_m=480.0)),
                min_altitude_m=20.0,
                max_altitude_m=180.0,
                height_reference="amsl",
            ),
            communications_shadow_attenuation_db=None,
        ),
        RegionSpec(
            region_id="region.no-fly.central",
            kind="no_fly",
            geometry=RegionGeometry(
                footprint_enu_m=(EnuPoint(east_m=-90.0, north_m=120.0), EnuPoint(east_m=90.0, north_m=120.0), EnuPoint(east_m=90.0, north_m=260.0), EnuPoint(east_m=-90.0, north_m=260.0)),
                min_altitude_m=20.0,
                max_altitude_m=160.0,
                height_reference="amsl",
            ),
            communications_shadow_attenuation_db=None,
        ),
        RegionSpec(
            region_id="region.communication-shadow.east",
            kind="communications_shadow",
            geometry=RegionGeometry(
                footprint_enu_m=(EnuPoint(east_m=180.0, north_m=-120.0), EnuPoint(east_m=380.0, north_m=-120.0), EnuPoint(east_m=380.0, north_m=100.0), EnuPoint(east_m=180.0, north_m=100.0)),
                min_altitude_m=20.0,
                max_altitude_m=120.0,
                height_reference="amsl",
            ),
            communications_shadow_attenuation_db=18.0,
        ),
    )


def _pose(east: float, north: float, up: float = 0.0) -> WorldPose:
    return WorldPose(frame_id="ENU", east_m=east, north_m=north, up_m=up, qw=1.0, qx=0.0, qy=0.0, qz=0.0)


def _world(root: Path, assets: tuple[AssetRecord, ...]) -> WorldPackage:
    buildings: list[BuildingSpec] = []
    entities: list[EntitySpec] = [
        EntitySpec(entity_id=UAV_ID, kind="uav", provider_id=FLIGHT_ID, authority_kind="gazebo_physics", state="dynamic", model_asset_id="asset.model-uav", pose=_pose(LAUNCH_ALPHA_EAST_M, LAUNCH_ALPHA_NORTH_M, UAV_LAUNCH_REFERENCE_UP_M)),
        EntitySpec(entity_id="station.operations", kind="static_asset", provider_id=None, authority_kind="scenario_static", state="static", model_asset_id="asset.model-station", pose=_pose(OPERATIONS_EAST_M, OPERATIONS_NORTH_M)),
        EntitySpec(entity_id="entity.launch.alpha", kind="static_asset", provider_id=None, authority_kind="scenario_static", state="static", model_asset_id="asset.model-pad", pose=_pose(LAUNCH_ALPHA_EAST_M, LAUNCH_ALPHA_NORTH_M)),
        EntitySpec(entity_id="entity.launch.bravo", kind="static_asset", provider_id=None, authority_kind="scenario_static", state="static", model_asset_id="asset.model-pad", pose=_pose(LAUNCH_BRAVO_EAST_M, LAUNCH_BRAVO_NORTH_M)),
    ]
    for index, (building_id, east, north) in enumerate(BUILDING_CENTERS, start=1):
        size_x, size_y, size_z = _building_dimensions(index)
        entity_id = f"entity.{building_id}"
        buildings.append(
            BuildingSpec(
                building_id=building_id,
                entity_id=entity_id,
                source_asset_id=f"asset.{building_id}-source",
                render_asset_id=f"asset.{building_id}-render",
                collision_asset_id=f"asset.{building_id}-collision",
                geometry=BuildingGeometry(
                    footprint_enu_m=(
                        EnuPoint(east_m=east - size_x / 2.0, north_m=north - size_y / 2.0),
                        EnuPoint(east_m=east + size_x / 2.0, north_m=north - size_y / 2.0),
                        EnuPoint(east_m=east + size_x / 2.0, north_m=north + size_y / 2.0),
                        EnuPoint(east_m=east - size_x / 2.0, north_m=north + size_y / 2.0),
                    ),
                    base_altitude_m=0.0,
                    top_altitude_m=size_z,
                    height_reference="enu_up",
                ),
            )
        )
        entities.append(EntitySpec(entity_id=entity_id, kind="static_asset", provider_id=None, authority_kind="scenario_static", state="static", model_asset_id=f"asset.{building_id}-render", pose=_pose(east, north)))

    sumo_bindings: list[SumoEntityBinding] = []
    # Keep initial poses distributed over the southern approach lanes.  SUMO
    # remains authoritative after departure; these poses only make the
    # pending lifecycle explicit in the first barrier snapshot.
    vehicle_positions = tuple(
        (
            -450.0 + float((index - 1) % 10) * 45.0,
            -450.0 + float((index - 1) // 10) * 30.0,
        )
        for index in range(1, VEHICLE_COUNT + 1)
    )
    for index, (east, north) in enumerate(vehicle_positions, start=1):
        entity_id = f"vehicle.{index:02d}"
        entities.append(EntitySpec(entity_id=entity_id, kind="ugv", provider_id=TRAFFIC_ID, authority_kind="sumo_traffic", state="dynamic", model_asset_id="asset.model-vehicle", pose=_pose(east, north)))
        sumo_bindings.append(SumoEntityBinding(sumo_object_id=f"veh.{index:02d}", entity_id=entity_id, kind="vehicle"))
    person_positions = tuple(
        (-450.0 + float(index - 1) * 45.0, -450.0)
        for index in range(1, PERSON_COUNT + 1)
    )
    for index, (east, north) in enumerate(person_positions, start=1):
        entity_id = f"pedestrian.{index:02d}"
        entities.append(EntitySpec(entity_id=entity_id, kind="pedestrian", provider_id=TRAFFIC_ID, authority_kind="sumo_traffic", state="dynamic", model_asset_id="asset.model-pedestrian", pose=_pose(east, north)))
        sumo_bindings.append(SumoEntityBinding(sumo_object_id=f"ped.{index:02d}", entity_id=entity_id, kind="person"))
    sumo_bindings.sort(key=lambda binding: binding.sumo_object_id)

    building_by_id = {building_id: (index, east, north) for index, (building_id, east, north) in enumerate(BUILDING_CENTERS, start=1)}
    targets: list[SemanticTargetSpec] = []
    for target_id in ALL_TARGET_IDS:
        building_id = TARGET_BUILDINGS[target_id]
        index, _east, _north = building_by_id[building_id]
        size_x, _size_y, _size_z = _building_dimensions(index)
        targets.append(
            SemanticTargetSpec(
                target_id=target_id,
                parent_entity_id=f"entity.{building_id}",
                pose=ParentRelativePose(frame_id="parent", x_m=-(size_x / 2.0 + 0.08), y_m=0.0, z_m=12.0, qw=1.0, qx=0.0, qy=0.0, qz=0.0),
                geometry=TargetGeometry(shape="box", size_x_m=0.08, size_y_m=1.0, size_z_m=0.8),
                surface_normal_target=TargetSurfaceNormal(x=-1.0, y=0.0, z=0.0),
                required_sensor_id=SENSOR_ID,
                min_distance_m=8.0,
                max_distance_m=12.0,
                max_view_angle_deg=12.0,
                fov_margin_deg=5.0,
                dwell_time_s=2.0,
                required_evidence=("sensor_frame", "image", "report", "network_upload_or_buffer"),
            )
        )

    mission_requirements: list[MissionRequirement] = [
        MissionRequirement(requirement_id="mission.takeoff", kind="takeoff", dependencies=(), launch_site_id="launch.alpha", target_id=None, expected_public_asset_id=None)
    ]
    previous = "mission.takeoff"
    for target_id in REQUIRED_TARGET_IDS:
        requirement_id = f"mission.observe.{target_id.rsplit('.', 1)[1]}"
        mission_requirements.append(MissionRequirement(requirement_id=requirement_id, kind="observation", dependencies=(previous,), launch_site_id=None, target_id=target_id, expected_public_asset_id=None))
        previous = requirement_id
    mission_requirements.extend(
        (
            MissionRequirement(requirement_id="mission.upload", kind="upload_or_buffer", dependencies=(previous,), launch_site_id=None, target_id=None, expected_public_asset_id="mission.inspection-report"),
            MissionRequirement(requirement_id="mission.return", kind="return_to_launch", dependencies=("mission.upload",), launch_site_id="launch.alpha", target_id=None, expected_public_asset_id=None),
            MissionRequirement(requirement_id="mission.land", kind="land", dependencies=("mission.return",), launch_site_id="launch.alpha", target_id=None, expected_public_asset_id=None),
        )
    )

    package = world_package(
        world_id=WORLD_ID,
        frame=WorldFrame(
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
            origin=LocalFrameOrigin(frame_id="WGS84", longitude_deg=SHANGHAI_LONGITUDE_DEG, latitude_deg=SHANGHAI_LATITUDE_DEG, altitude_m=50.0),
            origin_height_reference="wgs84_ellipsoid",
            spatial_extent=SpatialBounds(min_east_m=-500.0, max_east_m=500.0, min_north_m=-500.0, max_north_m=500.0, min_up_m=-20.0, max_up_m=200.0, vertical_reference="enu_up"),
            vertical_datum=VerticalDatumBinding(geoid_correction_asset_id="asset.geoid", terrain_height_asset_id="asset.terrain-heights", geoid_interpolation="bilinear", terrain_interpolation="bilinear", geoid_precision_m=0.01, terrain_precision_m=0.05),
        ),
        assets=assets,
        provider_requirements=(
            ProviderRequirement(provider_id=BUSINESS_ID, roles=("mission",), required_capability_ids=("business.work-order",)),
            ProviderRequirement(provider_id=FLIGHT_ID, roles=("motion", "sensor"), required_capability_ids=("camera.rgb", "flight.arm", "gazebo.frames", "gazebo.physics", "observation.capture")),
            ProviderRequirement(provider_id=NETWORK_ID, roles=("wireless_network",), required_capability_ids=ns3_capabilities(("802.11ax",))),
            ProviderRequirement(provider_id=TRAFFIC_ID, roles=("traffic",), required_capability_ids=("sumo.frames", "sumo.traffic")),
        ),
        base_layers=(),
        layers=(*tuple(PublicLayer(layer_id=f"layer.{name}", kind=name, asset_id=f"asset.layer-{name}", visibility="public", default_visible=True) for name in ("buildings", "entities", "missions", "regions", "roads")), PublicLayer(layer_id="layer.osm-scene", kind="osm_scene", asset_id="asset.osm-scene", visibility="public", default_visible=True)),
        buildings=tuple(buildings),
        roads=_roads(),
        regions=_regions(),
        launch_sites=(
            LaunchSiteSpec(launch_site_id="launch.alpha", primary_uav_entity_id=UAV_ID, allowed_uav_entity_ids=(UAV_ID,), pose=_pose(LAUNCH_ALPHA_EAST_M, LAUNCH_ALPHA_NORTH_M, UAV_LAUNCH_REFERENCE_UP_M), pad_radius_m=4.0),
            LaunchSiteSpec(launch_site_id="launch.bravo", primary_uav_entity_id=UAV_ID, allowed_uav_entity_ids=(UAV_ID,), pose=_pose(LAUNCH_BRAVO_EAST_M, LAUNCH_BRAVO_NORTH_M), pad_radius_m=4.0),
        ),
        entities=tuple(entities),
        sensors=(
            SensorSpec(sensor_id=SENSOR_ID, provider_id=FLIGHT_ID, parent_entity_id=UAV_ID, kind="camera", pose=ParentRelativePose(frame_id="parent", x_m=0.12, y_m=0.03, z_m=0.242, qw=1.0, qx=0.0, qy=0.0, qz=0.0), horizontal_fov_deg=80.0, vertical_fov_deg=60.0, resolution_width_px=640, resolution_height_px=480),
        ),
        semantic_targets=tuple(targets),
        weather=(WeatherSample(sample_id="weather.formal", mode="deterministic_constant", wind=WindVector(east_mps=1.5, north_mps=-0.4, up_mps=0.0), visibility_m=10_000.0, precipitation="none", precipitation_rate_mm_per_h=0.0, temperature_c=22.0, pressure_pa=101_325.0),),
        engine_frame_bindings=(
            EngineFrameBinding(binding_id="gazebo.enu", provider_id=FLIGHT_ID, engine="gazebo", scene_asset_id="asset.gazebo-scene", source_frame_id="ENU", target_frame_id=f"{UAV_ID}/body", transform=RigidOffset(x_m=0.0, y_m=0.0, z_m=0.0, qw=1.0, qx=0.0, qy=0.0, qz=0.0)),
            EngineFrameBinding(binding_id="sumo.enu", provider_id=TRAFFIC_ID, engine="sumo", scene_asset_id=None, source_frame_id="sumo_net", target_frame_id="ENU", transform=RigidOffset(x_m=0.0, y_m=0.0, z_m=0.0, qw=1.0, qx=0.0, qy=0.0, qz=0.0)),
        ),
        sumo=SumoConfiguration(provider_id=TRAFFIC_ID, config_asset_id="asset.sumo-config", network_asset_id="asset.sumo-network", routes_asset_id="asset.sumo-routes", additional_asset_id="asset.sumo-additional", frame_binding_id="sumo.enu", object_bindings=tuple(sumo_bindings)),
        network=NetworkConfiguration(
            provider_id=NETWORK_ID,
            radio_profiles=(WirelessRadioProfile(radio_profile_id="radio.formal", provider_id=NETWORK_ID, wifi_standard="802.11ax", frequency_ghz=5.775, channel_width_mhz=80.0, tx_power_dbm=23.0, rx_sensitivity_dbm=-92.0),),
            node_bindings=(
                NetworkNodeBinding(node_id="node.operations", entity_id="station.operations", endpoint_id="endpoint.operations", radio_profile_id="radio.formal"),
                NetworkNodeBinding(node_id="node.uav", entity_id=UAV_ID, endpoint_id="endpoint.uav", radio_profile_id="radio.formal"),
            ),
            links=(NetworkLinkBinding(link_id="link.uav-operations", source_node_id="node.uav", destination_node_id="node.operations", data_rate_bps=6_000_000, propagation_delay_ns=20_000_000),),
        ),
        mission_requirements=tuple(mission_requirements),
        expected_public_assets=(ExpectedPublicAsset(asset_id="mission.inspection-report", kind="mission_output", producer_requirement_id="mission.upload", media_type="application/json", units=(UnitDeclaration(quantity="file_size", unit="B"),), precision=(PrecisionMetadata(quantity="file_size", kind="absolute_tolerance", unit="B", exact_bytes=None, absolute_tolerance=1.0),)),),
    )
    _write_json(root, "world/package.json", package.model_dump(mode="json"))
    generator = GeneratorIdentity(generator_id="aero-bench", version="urban-bundle-v1", source_revision=_runtime_revision(root))
    alignment = alignment_manifest(reader=_bundle_reader(root), world=package, generator=generator)
    _write_json(root, "world/alignment.json", alignment.model_dump(mode="json"))
    return package


def _bundle_reader(root: Path):
    from aero_bench.config.loader import BundleReader

    return BundleReader(root)


def _runtime_revision(root: Path) -> str:
    marker = root / ".runtime-source-revision"
    return marker.read_text(encoding="utf-8").strip()


def _artifact(
    artifact_id: str,
    artifact_type: str,
    producer_id: str,
    visibility: str,
    relative_path: str,
    *,
    max_size_bytes: int,
    source_asset_id: str | None = None,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_id": producer_id,
        "visibility": visibility,
        "relative_path": relative_path,
        "max_size_bytes": max_size_bytes,
        "source_asset_id": source_asset_id,
    }


def _goals() -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    goals = []
    bindings = []
    for component_id in FORMAL_V2_COMPONENT_IDS:
        goal_id = f"goal.formal.{component_id}"
        goals.append({"goal_id": goal_id, "metric_id": f"inspection.formal.{component_id}", "operator": "ge", "threshold": 1.0, "evidence": "artifact", "parameters": []})
        bindings.append({"component_id": component_id, "goal_id": goal_id})
    return goals, bindings


def _inspection_task_assets(
    root: Path, *, agent_acceptance: bool
) -> tuple[list[dict[str, object]], dict[str, str], list[dict[str, object]]]:
    if not agent_acceptance:
        target_asset_path = _write_text(
            root, "assets/inspection-target.sdf", _inspection_target_sdf()
        )
        asset_id = "asset.inspection-target-model"
        return (
            [
                {
                    "asset_id": asset_id,
                    "file": _ref(root, target_asset_path),
                    "visibility": "world",
                    "media_type": "application/vnd.gazebo.sdf+xml",
                }
            ],
            {target_id: asset_id for target_id in REQUIRED_TARGET_IDS},
            [
                {
                    "source_artifact_id": "artifact.truth",
                    "defect_id": (
                        f"defect.surface-damage.{target_id.rsplit('.', 1)[1]}"
                    ),
                    "target_id": target_id,
                    "geometrically_visible": True,
                }
                for target_id in REQUIRED_TARGET_IDS
            ],
        )

    surface_kinds = {
        "asset.inspection-panel-upper": "upper",
        "asset.inspection-panel-clean": "clean",
        "asset.inspection-panel-lower": "lower",
    }
    assets: list[dict[str, object]] = []
    for asset_id, surface_kind in surface_kinds.items():
        path = _write_text(
            root,
            f"assets/{asset_id.removeprefix('asset.')}.sdf",
            _inspection_target_sdf(patch_vertical=surface_kind),
        )
        assets.append(
            {
                "asset_id": asset_id,
                "file": _ref(root, path),
                "visibility": "world",
                "media_type": "application/vnd.gazebo.sdf+xml",
            }
        )
    truth = [
        {
            "source_artifact_id": "artifact.truth",
            "defect_id": defect_id,
            "target_id": target_id,
            "geometrically_visible": True,
        }
        for target_id, defect_id in AGENT_DEFECT_BY_TARGET_ID.items()
    ]
    return assets, dict(AGENT_TARGET_ASSET_BY_ID), truth


def _task_package(
    root: Path,
    observation_schema_ref: dict[str, str],
    *,
    agent_acceptance: bool = False,
) -> tuple[InspectionTaskPackage, dict[str, dict[str, object]]]:
    target_assets, target_asset_by_id, truth = _inspection_task_assets(
        root, agent_acceptance=agent_acceptance
    )
    truth_path = _write_bytes(
        root,
        "private/truth.json",
        canonical_json_bytes(truth),
    )
    artifacts = {
        "business_state": _artifact("artifact.business", "business.state", BUSINESS_ID, "private", "business/state.json", max_size_bytes=8 * 1024 * 1024),
        "trajectory": _artifact("artifact.trajectory", "trajectory", FLIGHT_ID, "public", "flight/trajectory.json", max_size_bytes=64 * 1024 * 1024),
        "delivery": _artifact("artifact.delivery", "network.delivery", NETWORK_ID, "public", "network/delivery.json", max_size_bytes=32 * 1024 * 1024),
        "observation": _artifact("artifact.observation", "observation", FLIGHT_ID, "private", "flight/observations.json", max_size_bytes=32 * 1024 * 1024),
        "sensor_frame": _artifact("artifact.sensor-frames", "sensor-frame", FLIGHT_ID, "public", "flight/sensor-frames.json", max_size_bytes=32 * 1024 * 1024),
        "camera_frame_data": _artifact("artifact.camera-frame-data", "camera-frame-data", FLIGHT_ID, "public", "flight/camera-frames.bin", max_size_bytes=256 * 1024 * 1024),
        "truth": _artifact("artifact.truth", "truth.dataset", "bundle", "private", "bundle/truth.json", max_size_bytes=1024 * 1024, source_asset_id="asset.inspection-truth"),
        "detection": _artifact("artifact.detections", "inspection.detection", AGENT_ID, "public", "agent/detections.json", max_size_bytes=8 * 1024 * 1024),
        "report": _artifact("artifact.report", "inspection.report", AGENT_ID, "public", "agent/report.json", max_size_bytes=60 * 1024),
        "theoretical_bounds": _artifact("artifact.bounds", "theoretical.bounds", "harness", "public", "harness/theoretical-bounds.json", max_size_bytes=1024 * 1024),
        "event_log": _artifact("artifact.event-log", "event.log", "harness", "private", "harness/event-log.jsonl", max_size_bytes=EVENT_LOG_MAX_SIZE_BYTES),
        "scene_state_history": _artifact("artifact.scene-states", "scene.state-history", "harness", "public", "harness/scene-states.jsonl", max_size_bytes=256 * 1024 * 1024),
    }
    if agent_acceptance:
        artifacts.update(
            {
                "model_session_manifest": _artifact(
                    "artifact.model-session-manifest",
                    "model.session-manifest",
                    AGENT_DRIVER_ID,
                    "private",
                    "model.session-manifest.json",
                    max_size_bytes=1024 * 1024,
                ),
                "model_interactions": _artifact(
                    "artifact.model-interactions",
                    "model.interactions",
                    AGENT_DRIVER_ID,
                    "private",
                    "model.interactions.jsonl",
                    max_size_bytes=64 * 1024 * 1024,
                ),
            }
        )
        bounds = {
            "mission": {
                "shortest_path_m": _mission_distance_lower_bound_m(),
                "max_speed_mps": 10.0,
                "fixed_time_s": 40.0,
                "inspection_dwell_s": 6.0,
                "deadline_s": 600.0,
            },
            "link": {
                "minimum_payload_bytes": MINIMUM_REPORT_PAYLOAD_BYTES,
                "max_bandwidth_bps": 6_000_000.0,
                "minimum_latency_s": 0.02,
                "upload_deadline_s": 540.0,
            },
            "imaging": {
                "defect_size_m": DEFECT_MINIMUM_DIMENSION_M,
                "standoff_distance_m": MAX_STANDOFF_M,
                "focal_length_m": CAMERA_EQUIVALENT_FOCAL_LENGTH_M,
                "pixel_pitch_m": PIXEL_PITCH_MODEL_M,
                "minimum_resolvable_pixels": 4.0,
            },
            "total_defects": len(truth),
            "geometrically_visible_defects": len(truth),
        }
    else:
        bounds = {
            "mission": {"shortest_path_m": 1500.0, "max_speed_mps": 10.0, "fixed_time_s": 40.0, "inspection_dwell_s": 6.0, "deadline_s": 600.0},
            "link": {"minimum_payload_bytes": MINIMUM_REPORT_PAYLOAD_BYTES, "max_bandwidth_bps": 6_000_000.0, "minimum_latency_s": 0.02, "upload_deadline_s": 30.0},
            "imaging": {"defect_size_m": 0.22, "standoff_distance_m": 10.0, "focal_length_m": 0.02, "pixel_pitch_m": 0.000003, "minimum_resolvable_pixels": 4.0},
            "total_defects": 3,
            "geometrically_visible_defects": 3,
        }
    bound_fields = (
        "mission.shortest_path_m", "mission.max_speed_mps", "mission.fixed_time_s", "mission.inspection_dwell_s", "mission.deadline_s",
        "link.minimum_payload_bytes", "link.max_bandwidth_bps", "link.minimum_latency_s", "link.upload_deadline_s",
        "imaging.defect_size_m", "imaging.standoff_distance_m", "imaging.focal_length_m", "imaging.pixel_pitch_m", "imaging.minimum_resolvable_pixels",
        "total_defects", "geometrically_visible_defects",
    )
    goals, _bindings = _goals()
    raw = {
        "schema_version": "aero-bench.inspection-task/v4",
        "task_id": AGENT_TASK_ID if agent_acceptance else TASK_ID,
        "verifier_id": VERIFIER_ID,
        "network_delivery_required": True,
        "actors": [
            {"actor_id": AGENT_ID, "role": "agent"},
            {"actor_id": FLIGHT_ID, "role": "observation_provider"},
            {"actor_id": BUSINESS_ID, "role": "business"},
            {"actor_id": VERIFIER_ID, "role": "verifier"},
        ],
        "assets": [
            *target_assets,
            {"asset_id": "asset.inspection-truth", "file": _ref(root, truth_path), "visibility": "verifier", "media_type": "application/json"},
        ],
        "work_orders": [
            {"work_order_id": f"work-order.{target_id.rsplit('.', 1)[1]}", "target_id": target_id, "required_observation_id": f"observation.{target_id.rsplit('.', 1)[1]}", "arrival_tolerance_m": 14.0, "report_artifact_type": "inspection.report", "upload_deadline_ns": 540_000_000_000}
            for target_id in REQUIRED_TARGET_IDS
        ],
        "observations": [
            {
                "observation_id": f"observation.{target_id.rsplit('.', 1)[1]}",
                "target_id": target_id,
                "simulation_asset_id": target_asset_by_id[target_id],
                "metadata_schema": observation_schema_ref,
                "media_type": "application/json",
                "trigger": {"observation_id": f"observation.{target_id.rsplit('.', 1)[1]}", "target_id": target_id, "provider_id": FLIGHT_ID, "camera_id": SENSOR_ID, "min_distance_m": 8.0, "max_distance_m": 12.0, "min_view_angle_deg": 0.0, "max_view_angle_deg": 12.0, "earliest_time_ns": 0, "latest_time_ns": 500_000_000_000},
            }
            for target_id in REQUIRED_TARGET_IDS
        ],
        "bounds": bounds,
        "bound_sources": [
            {"bound_field": field, "provider_id": BUSINESS_ID, "config_pointer": "/task_package/bounds/" + "/".join(field.split("."))}
            for field in bound_fields
        ],
        "artifact_requirements": [{**artifact, "evidence_kind": kind} for kind, artifact in artifacts.items()],
        "goals": goals,
    }
    package = InspectionTaskPackage.model_validate(raw)
    _write_json(root, "task/inspection-package.json", package.model_dump(mode="json"))
    return package, artifacts


def _provider_configs(
    root: Path,
    package: InspectionTaskPackage,
    goal_bindings: list[dict[str, str]],
    *,
    agent_acceptance: bool = False,
) -> dict[str, Path]:
    px4 = Px4GazeboConfig.model_validate(
        {
            "schema_version": "aero-bench.px4-gazebo/v3",
            "provider_id": FLIGHT_ID,
            "px4": {"version": "v1.17.0-alpha1-1551-g381149fb01", "commit": "381149fb012762f5e38c4a7fdc1b905b28038970"},
            "gazebo": {"version": "8.11.0", "commit": "1be3cc376fec778cc725b4eeea463245affa56d3"},
            "mavsdk": {"version": "3.17.2", "commit": "9e3ca17faa84aa868caea10a3bbdab7e53810ced"},
            "engine_binding_id": "gazebo.enu",
            "physics_step_ns": 4_000_000,
            "px4_executable": "px4",
            "gazebo_executable": "gz",
            "mavsdk_server_executable": "mavsdk-server",
            "vehicles": [
                {
                    "vehicle_id": UAV_ID,
                    "system_id": 1,
                    "mavsdk_udp_port": 14540,
                    "px4_mavlink_udp_port": 14580,
                    "mavsdk_grpc_port": 15040,
                    "sys_autostart": 4001,
                    "model": "gz_x500_mono_cam",
                    "gazebo_model_name": "x500_mono_cam_0",
                    "gazebo_resource": "x500_mono_cam",
                    "initial_pose": {
                        "x_m": LAUNCH_ALPHA_EAST_M,
                        "y_m": LAUNCH_ALPHA_NORTH_M,
                        "z_m": UAV_LAUNCH_REFERENCE_UP_M,
                        "roll_rad": 0.0,
                        "pitch_rad": 0.0,
                        "yaw_rad": 0.0,
                    },
                }
            ],
            "required_commands": ["px4", "gz", "mavsdk-server"],
            "command_timeout_ms": 180_000,
            "physical_completion_policy": {
                "schema_version": "aero-bench.px4-physical-completion-policy/v2",
                "physical_sim_timeout_ns": 120_000_000_000,
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
            },
            # Multiple model requests may occur at one paused Provider barrier.
            # Their combined legal pause is bounded by the complete session.
            "maximum_agent_decision_wall_time_ms": (
                AGENT_SESSION_WALL_TIMEOUT_S * 1000 if agent_acceptance else 180_000
            ),
            "heartbeat_timeout_fixed_margin_ms": 30_000,
        }
    )
    ns3 = Ns3Config.model_validate({"schema_version": "aero-bench.ns3/v3", "provider_id": NETWORK_ID, "ns3": {"version": "3.48", "commit": "d2add90b452d600cfb4859baed8e9ea633519447"}, "network_model": "wifi-adhoc-scene-mobility/v1", "supported_wifi_standards": ["802.11ax"], "required_commands": ["ns3"], "command_timeout_ms": 180_000})
    sumo = SumoConfig.model_validate({"schema_version": "aero-bench.sumo/v2", "provider_id": TRAFFIC_ID, "sumo": {"version": "1.27.1", "commit": "7717f2379d9e314a0c81c5cec748444de06a2a91"}, "sumo_binary": "sumo", "traci_port": 18813, "step_length_ns": 500_000_000, "sumo_args": ["--duration-log.disable", "true", "--no-step-log", "true", "--no-warnings", "true"], "required_commands": ["sumo"], "command_timeout_ms": 180_000})
    business = InspectionBusinessConfig(schema_version="aero-bench.inspection-business/v1", provider_id=BUSINESS_ID, task_package=package)
    formal = InspectionFormalVerifierConfigV3.model_validate(
        {
            "schema_version": FORMAL_SCHEMA_VERSION,
            "package_id": AGENT_PACKAGE_ID if agent_acceptance else PACKAGE_ID,
            "vehicle_id": UAV_ID,
            "sensor_id": SENSOR_ID,
            "target_ids": REQUIRED_TARGET_IDS,
            "geofence_ids": ("region.formal-geofence",),
            "no_fly_ids": ("region.no-fly.central",),
            "required_tool_ids": ("flight.arm", "flight.disarm", "flight.goto", "flight.hold", "flight.land", "flight.takeoff"),
            "goal_bindings": tuple(goal_bindings),
            "minimum_takeoff_agl_m": 12.5,
            "minimum_horizontal_ground_track_m": 50.1,
            "startup_clearance_margin_m": 0.5,
            "minimum_distinct_frames_per_target": 2,
            "dwell_s": 2.0,
            "max_trace_gap_s": 0.75,
            "return_radius_m": 3.0,
            "launch_pad_height_m": 0.15,
            "landing_max_agl_m": 0.5,
            "landing_max_vertical_speed_mps": 0.35,
            "stopped_linear_speed_mps": 0.25,
            "stopped_angular_speed_rps": 0.15,
            "stopped_dwell_s": 2.0,
            "terminal_disarm_required": True,
            "network_delivery_required": True,
            "network_deadline_s": 540.0,
            "accepted_outcome_kinds": ("delivered",),
            "minimum_buffer_retention_s": 60.0,
            "minimum_detection_f1": 1.0,
            "uncertainty": {"horizontal_position_uncertainty_m": 0.05, "vertical_position_uncertainty_m": 0.02, "terrain_uncertainty_m": 0.05},
        }
    )
    models = {"px4": px4, "ns3": ns3, "sumo": sumo, "business": business, "verifier": formal}
    return {name: _write_json(root, f"configs/{name}.json", model.model_dump(mode="json")) for name, model in models.items()}


def _schemas(root: Path, *, agent_acceptance: bool = False) -> dict[str, Path]:
    identifier = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"}
    sha256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    number = {"type": "number"}
    schemas: dict[str, object] = {
        "observation": InspectionRgbObservation.model_json_schema(),
        "gateway-protocol": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        "provider-protocol": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        "tool-response": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        "business-claim": _object_schema({"actor_id": identifier, "work_order_id": identifier}, ["actor_id", "work_order_id"]),
        "business-start": _object_schema({"actor_id": identifier, "work_order_id": identifier}, ["actor_id", "work_order_id"]),
        "business-submit": _object_schema({"actor_id": identifier, "observation_id": identifier, "report_payload_digest": sha256, "work_order_id": identifier}, ["actor_id", "observation_id", "report_payload_digest", "work_order_id"]),
        "flight-vehicle": _object_schema({"vehicle_id": identifier}, ["vehicle_id"]),
        "flight-takeoff": _object_schema({"vehicle_id": identifier, "altitude_m": {"type": "number", "exclusiveMinimum": 10.0, "maximum": 80.0}}, ["vehicle_id", "altitude_m"]),
        "flight-goto": _object_schema({"vehicle_id": identifier, "latitude_deg": {"type": "number", "minimum": -90.0, "maximum": 90.0}, "longitude_deg": {"type": "number", "minimum": -180.0, "maximum": 180.0}, "altitude_amsl_m": number, "yaw_deg": {"type": "number", "minimum": -360.0, "maximum": 360.0, "description": "ENU yaw in degrees: 0 points east and positive angles rotate toward north."}}, ["vehicle_id", "latitude_deg", "longitude_deg", "altitude_amsl_m", "yaw_deg"]),
        "network-send": _object_schema({"work_order_id": identifier, "message_id": identifier, "source": identifier, "destination": identifier, "payload_base64": {"type": "string", "minLength": 4, "maxLength": 81920}, "payload_sha256": sha256, "traffic_class": {"type": "string", "const": "best_effort"}, "priority": {"type": "integer", "const": 0}, "reliability": {"type": "string", "const": "best_effort"}}, ["work_order_id", "message_id", "source", "destination", "payload_base64", "payload_sha256", "traffic_class", "priority", "reliability"]),
        "network-mailbox": NetworkMailboxObservationPayload.model_json_schema(),
        "inspection-package": InspectionTaskPackage.model_json_schema(),
        "px4-config": Px4GazeboConfig.model_json_schema(),
        "ns3-config": Ns3Config.model_json_schema(),
        "sumo-config": SumoConfig.model_json_schema(),
        "business-config": InspectionBusinessConfig.model_json_schema(),
        "verifier-config": InspectionFormalVerifierConfigV3.model_json_schema(),
    }
    if agent_acceptance:
        nullable_identifier = {"anyOf": [identifier, {"type": "null"}]}
        nullable_sha256 = {"anyOf": [sha256, {"type": "null"}]}
        detail_properties = {
            "work_order_id": identifier,
            "target_id": identifier,
            "status": {
                "enum": [
                    "created",
                    "claimed",
                    "in_progress",
                    "observation_ready",
                    "submitted",
                    "completed",
                    "failed",
                    "cancelled",
                ]
            },
            "version": {"type": "integer", "minimum": 0},
            "last_tick": {"type": "integer", "minimum": 0},
            "last_time_ns": {"type": "integer", "minimum": 0},
            "claimed_by": nullable_identifier,
            "required_observation_id": identifier,
            "arrival_tolerance_m": {"type": "number", "exclusiveMinimum": 0},
            "report_artifact_type": identifier,
            "upload_deadline_ns": {"type": "integer", "exclusiveMinimum": 0},
            "observation_earliest_time_ns": {"type": "integer", "minimum": 0},
            "observation_latest_time_ns": {"type": "integer", "minimum": 0},
            "min_observation_distance_m": {"type": "number", "minimum": 0},
            "max_observation_distance_m": {"type": "number", "exclusiveMinimum": 0},
            "min_view_angle_deg": {"type": "number", "minimum": 0},
            "max_view_angle_deg": {"type": "number", "minimum": 0},
            "navigation_coordinate_frame": {"const": "WGS84+ENU"},
            "height_datum": {"const": "wgs84-ellipsoid+amsl+agl"},
            "longitude_deg": {"type": "number", "minimum": -180, "maximum": 180},
            "latitude_deg": {"type": "number", "minimum": -90, "maximum": 90},
            "ellipsoid_height_m": number,
            "amsl_m": number,
            "agl_m": number,
            "east_m": number,
            "north_m": number,
            "up_m": number,
            "orientation_qw": number,
            "orientation_qx": number,
            "orientation_qy": number,
            "orientation_qz": number,
            "observation_id": nullable_identifier,
            "report_payload_digest": nullable_sha256,
            "network_delivery_required": {"type": "boolean"},
            "completion_authority": {"const": "business"},
        }
        policy_properties = {
            "schema_version": {
                "const": "aero-bench.agent-session-policy/v1"
            },
            "model": {"const": AGENT_MODEL},
            "reasoning_effort": {"const": AGENT_REASONING_EFFORT},
            "max_model_requests": {
                "type": "integer",
                "const": AGENT_MAX_MODEL_REQUESTS,
            },
            "max_tool_calls": {
                "type": "integer",
                "const": AGENT_MAX_TOOL_CALLS,
            },
            "max_image_observations": {
                "type": "integer",
                "const": AGENT_MAX_IMAGE_OBSERVATIONS,
            },
            "model_call_timeout_s": {
                "type": "integer",
                "const": AGENT_MODEL_CALL_TIMEOUT_S,
            },
            "session_wall_timeout_s": {
                "type": "integer",
                "const": AGENT_SESSION_WALL_TIMEOUT_S,
            },
            "max_output_tokens": {
                "type": "integer",
                "const": AGENT_MAX_OUTPUT_TOKENS,
            },
        }
        schemas.update(
            {
                "flight-telemetry-observation": (
                    FlightTelemetryObservation.model_json_schema()
                ),
                "flight-gnss-observation": FlightGnssObservation.model_json_schema(),
                "business-work-orders-query": _object_schema({}, []),
                "business-work-orders-result": _object_schema(
                    {
                        "work_order_count": {"type": "integer", "minimum": 0},
                        "work_orders_json": {"type": "string", "minLength": 2},
                    },
                    ["work_order_count", "work_orders_json"],
                ),
                "business-work-order-query": _object_schema(
                    {"work_order_id": identifier}, ["work_order_id"]
                ),
                "business-work-order-result": _object_schema(
                    detail_properties, list(detail_properties)
                ),
                "agent-driver-config": _object_schema(
                    {
                        "schema_version": {
                            "const": "aero-bench.agent-driver-config/v1"
                        },
                        "policy": _object_schema(
                            policy_properties, list(policy_properties)
                        ),
                        "initial_input": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 131_072,
                        },
                        "codex_cli_version": {"const": CODEX_CLI_VERSION},
                        "codex_binary_sha256": {"const": CODEX_BINARY_SHA256},
                    },
                    [
                        "schema_version",
                        "policy",
                        "initial_input",
                        "codex_cli_version",
                        "codex_binary_sha256",
                    ],
                ),
            }
        )
    return {name: _write_json(root, f"schemas/{name}.json", schema) for name, schema in schemas.items()}


def _runtime(image: str, command: list[str], component_id: str, revision: str, version: str, *, cpu: int, memory: int, source_uri: str = SOURCE_URI) -> dict[str, object]:
    return {
        "runtime": {"image": image, "command": command},
        "resources": {"cpu_millicores": cpu, "memory_mib": memory, "gpu_count": 0},
        "implementation": {"component_id": component_id, "kind": "production", "source_uri": source_uri, "source_revision": revision, "version": version},
    }


def _pending_images(*, agent_acceptance: bool = False) -> dict[str, str]:
    components = (
        "harness",
        "flight",
        "network",
        "traffic",
        "business",
        "verifier",
        "agent",
        *(("agent_driver",) if agent_acceptance else ()),
    )
    return {component: f"registry.invalid/aero-bench/{component}@sha256:{_sha256_bytes(('pending-fresh-image:' + component).encode())}" for component in components}


def _load_images(
    path: Path | None, *, agent_acceptance: bool = False
) -> tuple[dict[str, str], dict[str, str]]:
    if path is None:
        return _pending_images(agent_acceptance=agent_acceptance), {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("images"), dict):
        raise ValueError("images lock must contain an images object")
    images = raw["images"]
    required = {
        "harness",
        "flight",
        "network",
        "traffic",
        "business",
        "verifier",
        "agent",
        *(("agent_driver",) if agent_acceptance else ()),
    }
    if set(images) != required or any(not isinstance(value, str) for value in images.values()):
        raise ValueError("images lock must bind exactly all managed and Agent images")
    if any(value.startswith("registry.invalid/") for value in images.values()):
        raise ValueError("formal images lock cannot use registry.invalid identities")
    metadata = raw.get("agent", {})
    if not isinstance(metadata, dict):
        raise ValueError("images lock agent metadata must be an object")
    return dict(images), {str(key): str(value) for key, value in metadata.items()}


def _runtime_specs(
    root: Path,
    *,
    revision: str,
    images: dict[str, str],
    agent_metadata: dict[str, str],
    schemas: dict[str, Path],
    configs: dict[str, Path],
    package: InspectionTaskPackage,
    artifacts: dict[str, dict[str, object]],
    formal: bool,
    agent_acceptance: bool = False,
    agent_artifact_schema_path: Path | None = None,
    agent_driver_config_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    if agent_acceptance != (
        agent_artifact_schema_path is not None
        and agent_driver_config_path is not None
    ):
        raise ValueError(
            "Agent acceptance must bind its public artifact schema and private driver config"
        )
    if not agent_acceptance and (
        agent_artifact_schema_path is not None or agent_driver_config_path is not None
    ):
        raise ValueError("non-Agent profile cannot bind Agent acceptance assets")
    prefix = "" if formal else "templates/"
    generic_response = _ref(root, schemas["tool-response"])
    tool_specs = (
        ("business.claim", BUSINESS_ID, "business-claim"),
        ("business.start", BUSINESS_ID, "business-start"),
        ("business.submit", BUSINESS_ID, "business-submit"),
        ("flight.arm", FLIGHT_ID, "flight-vehicle"),
        ("flight.disarm", FLIGHT_ID, "flight-vehicle"),
        ("flight.goto", FLIGHT_ID, "flight-goto"),
        ("flight.hold", FLIGHT_ID, "flight-vehicle"),
        ("flight.land", FLIGHT_ID, "flight-vehicle"),
        ("flight.takeoff", FLIGHT_ID, "flight-takeoff"),
        ("network.send", NETWORK_ID, "network-send"),
    )
    query_specs = (
        (
            "business.work-order",
            BUSINESS_ID,
            "business-work-order-query",
            "business-work-order-result",
        ),
        (
            "business.work-orders",
            BUSINESS_ID,
            "business-work-orders-query",
            "business-work-orders-result",
        ),
    ) if agent_acceptance else ()
    goals, _bindings = _goals()
    verifier_requirements = [dict(value) for value in artifacts.values()]
    task = TaskSpec.model_validate(
        {
            "schema_version": "aero-bench.task/v1",
            "task_id": AGENT_TASK_ID if agent_acceptance else TASK_ID,
            "package": {"package_id": AGENT_PACKAGE_ID if agent_acceptance else PACKAGE_ID, "config": {"file": _ref(root, root / "task/inspection-package.json"), "schema_file": _ref(root, schemas["inspection-package"])}},
            "instruction": _ref(root, root / "instruction.md"),
            "required_capabilities": [
                "business.work-order",
                "flight.command",
                *(
                    ["flight.gnss", "flight.telemetry"]
                    if agent_acceptance
                    else []
                ),
                "network.delivery",
                "observation.capture",
            ],
            "required_tools": [item[0] for item in tool_specs],
            "assets": [
                *[
                    {
                        "asset_id": asset.asset_id,
                        "file": asset.file.model_dump(mode="json"),
                        "classification": "private",
                        "audiences": [
                            {
                                "role": (
                                    "provider"
                                    if asset.visibility == "world"
                                    else "verifier"
                                ),
                                "workload_ids": [
                                    FLIGHT_ID
                                    if asset.visibility == "world"
                                    else VERIFIER_ID
                                ],
                            }
                        ],
                    }
                    for asset in package.assets
                ],
                *(
                    [
                        {
                            "asset_id": "asset.agent-artifact-schemas",
                            "file": _ref(root, agent_artifact_schema_path),
                            "classification": "public",
                            "audiences": [
                                {"role": "agent", "workload_ids": [AGENT_ID]}
                            ],
                        },
                    ]
                    if agent_artifact_schema_path is not None
                    and agent_driver_config_path is not None
                    else []
                ),
            ],
            "goals": [{**goal, "verifier_id": VERIFIER_ID} for goal in goals],
            "verifier": {
                "verifier_id": VERIFIER_ID,
                "workload": _runtime(images["verifier"], ["verify"], VERIFIER_ID, revision, "0.4.0-inspection-verifier.4", cpu=1000, memory=VERIFIER_MEMORY_MIB),
                "config": {"file": _ref(root, configs["verifier"]), "schema_file": _ref(root, schemas["verifier-config"])},
                "artifact_requirements": verifier_requirements,
                "output_artifacts": [_artifact("artifact.verification-report", "verification.report", VERIFIER_ID, "public", "verifier/report.json", max_size_bytes=4 * 1024 * 1024)],
            },
        }
    )
    task_path = _write_yaml(root, f"{prefix}task/task.yaml", task.model_dump(mode="json"))

    traffic_artifact = _artifact("artifact.sumo-evidence", "sumo.traffic.evidence", TRAFFIC_ID, "private", "traffic/evidence.json", max_size_bytes=64 * 1024 * 1024)
    provider_versions = {
        "px4.gazebo": "0.4.0-px4-gazebo.1",
        "ns3.rpc": "0.4.0-ns3.1",
        "sumo.traci": "0.4.0-sumo.2",
        "inspection.business": "0.4.0-inspection-business.1",
    }
    provider_defs = (
        (FLIGHT_ID, "px4.gazebo", 17601, "flight", "px4", ("camera.rgb", "flight.arm", "flight.command", "flight.disarm", "flight.goto", "flight.hold", "flight.land", "flight.takeoff", "gazebo.frames", "gazebo.physics", "observation.capture", *(('flight.gnss', 'flight.telemetry') if agent_acceptance else ())), (artifacts["trajectory"], artifacts["observation"], artifacts["sensor_frame"], artifacts["camera_frame_data"]), 4000, 8192),
        (NETWORK_ID, "ns3.rpc", 17602, "network", "ns3", ns3_capabilities(("802.11ax",)), (artifacts["delivery"],), 2000, 2048),
        (TRAFFIC_ID, "sumo.traci", 17603, "traffic", "sumo", ("sumo.frames", "sumo.traffic"), (traffic_artifact,), 1500, 2048),
        (BUSINESS_ID, "inspection.business", 17604, "business", "business", ("business.work-order",), (artifacts["business_state"],), 1000, 1024),
    )
    environment = EnvironmentSpec.model_validate(
        {
            "schema_version": "aero-bench.environment/v1",
            "environment_id": (
                AGENT_ENVIRONMENT_ID
                if agent_acceptance
                else "urban.infrastructure.inspection.v1"
            ),
            "harness": _runtime(images["harness"], ["serve"], "aero-bench.harness", revision, "0.4.0-harness.1", cpu=2000, memory=4096),
            # The selected launch-to-target route is roughly 2.5 km including
            # the required return.  At the PX4 cruise limit of 5 m/s that is
            # 500 s of horizontal flight before the landing/settling margin;
            # 2,000 half-second barriers provide a 1,000 s physical envelope.
            "clock": {"authority": "provider_barrier", "step_ns": 500_000_000, "max_steps": FORMAL_MAX_STEPS, "provider_timeout_ms": 240_000},
            "gateway": {"protocol_schema": _ref(root, schemas["gateway-protocol"]), "port": 17432},
            "providers": [
                {
                    "provider_id": provider_id,
                    "adapter": adapter,
                    "port": port,
                    "workload": _runtime(images[image_key], ["provider", "serve"], adapter, revision, provider_versions[adapter], cpu=cpu, memory=memory),
                    "config": {"file": _ref(root, configs[config_key]), "schema_file": _ref(root, schemas[f"{config_key}-config"])},
                    "protocol_schema": _ref(root, schemas["provider-protocol"]),
                    "capabilities": list(capabilities),
                    "artifact_requirements": list(provider_artifacts),
                }
                for provider_id, adapter, port, image_key, config_key, capabilities, provider_artifacts, cpu, memory in provider_defs
            ],
            "harness_artifact_requirements": [artifacts["event_log"], artifacts["scene_state_history"], artifacts["theoretical_bounds"]],
        }
    )
    environment_path = _write_yaml(root, f"{prefix}environment/environment.yaml", environment.model_dump(mode="json"))

    agent_revision = (
        revision if agent_acceptance else agent_metadata.get("source_revision", revision)
    )
    agent_source_uri = (
        SOURCE_URI
        if agent_acceptance
        else agent_metadata.get("source_uri", SOURCE_URI)
    )
    agent_version = (
        "0.4.0-agent-bridge.1"
        if agent_acceptance
        else agent_metadata.get("version", "pending-external-participant.1")
    )
    agent = AgentSpec.model_validate(
        {
            "schema_version": "aero-bench.agent/v2",
            "agent_id": AGENT_ID,
            "workload": _runtime(
                images["agent"],
                ["bridge", "run"] if agent_acceptance else ["run"],
                AGENT_ID,
                agent_revision,
                agent_version,
                cpu=1500,
                memory=2048,
                source_uri=agent_source_uri,
            ),
            "tools": [{"tool_id": tool_id, "provider_id": provider_id, "request_schema": _ref(root, schemas[schema_name]), "response_schema": generic_response, "timeout_ms": 180_000, "idempotent": False} for tool_id, provider_id, schema_name in tool_specs],
            "queries": [
                {
                    "query_type": query_type,
                    "provider_id": provider_id,
                    "request_schema": _ref(root, schemas[request_schema]),
                    "response_schema": _ref(root, schemas[response_schema]),
                    "timeout_ms": 180_000,
                }
                for query_type, provider_id, request_schema, response_schema in query_specs
            ],
            "observations": [
                *[{"observation_id": f"observation.{target_id.rsplit('.', 1)[1]}", "provider_id": FLIGHT_ID, "schema_file": _ref(root, schemas["observation"]), "timeout_ms": 180_000} for target_id in REQUIRED_TARGET_IDS],
                *(
                    [
                        {
                            "observation_id": f"flight.gnss.{UAV_ID}",
                            "provider_id": FLIGHT_ID,
                            "schema_file": _ref(
                                root, schemas["flight-gnss-observation"]
                            ),
                            "timeout_ms": 180_000,
                        },
                        {
                            "observation_id": f"flight.telemetry.{UAV_ID}",
                            "provider_id": FLIGHT_ID,
                            "schema_file": _ref(
                                root, schemas["flight-telemetry-observation"]
                            ),
                            "timeout_ms": 180_000,
                        },
                    ]
                    if agent_acceptance
                    else []
                ),
                {"observation_id": "network.mailbox.endpoint.uav", "provider_id": NETWORK_ID, "schema_file": _ref(root, schemas["network-mailbox"]), "timeout_ms": 180_000},
            ],
            "artifact_requirements": [artifacts["detection"], artifacts["report"]],
            "driver": (
                {
                    "driver_id": AGENT_DRIVER_ID,
                    "workload": _runtime(
                        images["agent_driver"],
                        ["driver", "run"],
                        AGENT_DRIVER_ID,
                        revision,
                        "0.4.0-astra-driver.1",
                        cpu=1500,
                        memory=2048,
                    ),
                    "config": {
                        "file": _ref(root, agent_driver_config_path),
                        "schema_file": _ref(
                            root, schemas["agent-driver-config"]
                        ),
                    },
                    "bridge_port": AGENT_DRIVER_BRIDGE_PORT,
                    "artifact_requirements": [
                        artifacts["model_session_manifest"],
                        artifacts["model_interactions"],
                    ],
                }
                if agent_acceptance
                else None
            ),
        }
    )
    agent_document = agent.model_dump(mode="json")
    agent_path = _write_yaml(root, f"{prefix}agent/participant.yaml", agent_document)
    return task_path, environment_path, agent_path


def _source_validation(
    root: Path,
    *,
    task_path: Path,
    environment_path: Path,
    agent_path: Path,
    formal: bool,
    agent_acceptance: bool = False,
) -> tuple[str, str]:
    suite_raw = {
        "schema_version": "aero-bench.suite/v2",
        "suite_id": AGENT_SUITE_ID if agent_acceptance else "urban.infrastructure.inspection.v1",
        "aggregator_id": "macro",
        "execution_scope": "formal_benchmark",
        "cases": [
            {
                "case_id": (
                    AGENT_CASE_ID
                    if agent_acceptance
                    else "urban.infrastructure.inspection.formal"
                ),
                "task": _ref(root, task_path),
                "environment": _ref(root, environment_path),
                "agents": [_ref(root, agent_path)],
                "world_package": _ref(root, root / "world/package.json"),
                "launch_site_ids": ["launch.alpha"],
                "seeds": [20260902],
                "axes": [],
            }
        ],
    }
    suite_path = root / ("suite.yaml" if formal else ".source-validation-suite.yaml")
    _write_yaml(root, suite_path.relative_to(root).as_posix(), suite_raw)
    try:
        runs = resolve_suite(str(suite_path), executor_kind="docker_reference", task_package_resolvers=(InspectionTaskPackageResolver(),), provider_registry=builtin_provider_registry())
        if len(runs) != 1:
            raise RuntimeError("urban formal source must resolve to exactly one run")
        run = runs[0]
        declared_artifact_bytes = sum(
            requirement.max_size_bytes for requirement in run.artifact_requirements
        )
        manifest_bytes = seal_manifest_size_upper_bound(
            run_id=run.run_id,
            execution_scope=run.execution_scope,
            requirements=run.artifact_requirements,
        )
        if (
            declared_artifact_bytes + manifest_bytes
            > RUNNER_INPUT_VOLUME_SIZE_BYTES
        ):
            raise RuntimeError(
                "formal verifier input capacity is below the complete sealed "
                "snapshot upper bound"
            )
        if (
            declared_artifact_bytes + manifest_bytes
            > RUNNER_ARTIFACT_VOLUME_SIZE_BYTES
        ):
            raise RuntimeError(
                "runtime artifact capacity is below the complete sealed "
                "artifact upper bound"
            )
        if formal:
            _write_json(root, "resolved-run.json", run.model_dump(mode="json"))
        return run.scenario.scenario_digest, run.run_id
    finally:
        if not formal:
            suite_path.unlink(missing_ok=True)


def _instruction_text(*, agent_acceptance: bool) -> str:
    if not agent_acceptance:
        return (
            "# Urban infrastructure inspection v1\n\n"
            "Execute the declared work orders through the Gateway only. Use the selected launch site, "
            "take off above 12.5 m AGL, inspect every required target with the declared camera geometry "
            "and continuous dwell, retain at least two distinct frames per target, avoid all no-fly "
            "volumes and forbidden contacts, and remain inside the geofence. Classify a visible dark "
            "surface-damage patch as `defect.surface-damage.<target two-digit suffix>`; absence produces "
            "no detection. Produce canonical detection "
            "and report artifacts, send the exact sealed report artifact bytes for every work order from "
            "`endpoint.uav` to `endpoint.operations` through `network.send`, then return, land, stop, and "
            "disarm. `flight.goto.yaw_deg` is an ENU yaw (zero east, positive toward north); the Provider "
            "performs the declared ENU-to-NED conversion for MAVSDK. Command acceptance and MAVLink ACKs are not physical completion; wait for authoritative "
            "tool results and subsequent observations. Emit concise `decision_summary` records only; do not "
            "emit hidden reasoning.\n"
        )

    return (
        "# Agent infrastructure inspection acceptance v1\n\n"
        "The initial function catalog is the complete mission interface. Discover the available work "
        "orders with `query_business_work_orders`, then call `query_business_work_order` only with "
        "identifiers returned by discovery. Do not assume order or target identifiers. Claim and start "
        "each discovered order with `action_business_claim` and `action_business_start`.\n\n"
        "Use only the catalog functions. Read flight health and GNSS with the corresponding `read_*` "
        "functions and confirm a valid fix before "
        "arming. The declared ENU geofence is east/north -480 m through 480 m and AMSL 20 m through "
        "180 m. The central no-fly prism is east -90 m through 90 m, north 120 m through 260 m, and "
        "AMSL 20 m through 160 m. All building roofs are at or below 44 m AGL; use a collision-clear "
        "transit altitude and approach each panel from its outward side. The panel inspection surface "
        "normal is local (-1, 0, 0), and the forward camera optical axis is vehicle +X. A qualifying "
        "capture requires 8 m through 12 m standoff, no more than 12 degrees view angle, two distinct "
        "frames, and at least 2 seconds of continuous dwell. At a qualifying inspection position, "
        "issue `action_flight_hold` and wait for its physical completion before the dwell captures. "
        "Take off above 12.5 m AGL and remain inside "
        "all flight limits.\n\n"
        "Classify a visible dark patch in the upper half as `defect.surface-damage.upper`, a visible dark "
        "patch in the lower half as `defect.surface-damage.lower`, and a panel with no dark patch as no "
        "detection. Base every detection on the returned PNG pixels and cite the corresponding frame "
        "identifier and image digest. Read `asset.agent-artifact-schemas` with `read_public_asset` before "
        "creating outputs. Write the complete detection array first with `write_json_artifact` for "
        "`artifact.detections`; use its returned exact digest in every report, then write the complete report "
        "array for `artifact.report`. The public schema defines the required frame and image bindings and "
        "does not contain detections. Submit each order with `action_business_submit` and the exact report "
        "artifact digest, and call `action_network_send` with `artifact.report` to send those exact bytes "
        "from `endpoint.uav` to "
        "`endpoint.operations` before the returned deadline. A successful send means ns-3 queued the "
        "artifact; that receipt is not delivery and not work-order completion. Completion authority is "
        "business: `query_business_work_order` reports `completed` only after ns-3 delivers the matching "
        "report digest. Advance simulation with `wait_duration`, then re-query status. The UAV mailbox is "
        "inbound and does not show outbound delivery. After a queued success, wait within the deadline "
        "instead of immediately duplicating the send; send again only if waiting shows delivery did not "
        "occur. Then "
        "return to the selected launch site, land, remain stopped for 2 seconds, and disarm.\n\n"
        "`flight.goto.yaw_deg` is ENU yaw: zero points east and positive angles turn north. Command "
        "acceptance and MAVLink ACKs are not physical completion. Use `wait_for_command`, "
        "`get_command_status`, and subsequent flight observations to establish completion. Record only "
        "concise summaries in each action's required `decision_summary` argument; it is recorded with "
        "that command. Use `record_decision_summary` for separate mission notes. Do not emit hidden reasoning. Call `finish` only "
        "after both output artifacts exist and the vehicle is landed, stopped, and disarmed.\n"
    )


def build(args: argparse.Namespace) -> Path:
    root = Path(args.output_root).resolve()
    agent_acceptance = bool(getattr(args, "agent_acceptance", False))
    historical_root = (REPO_ROOT / "releases/urban-infrastructure-inspection-v1").resolve()
    if agent_acceptance and root == historical_root:
        raise ValueError(
            "Agent acceptance output cannot replace the historical urban release"
        )
    if root.exists():
        if not args.force:
            raise ValueError("release output exists; pass --force to replace it")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    source_revision, source_records = _managed_source_closure()
    if source_revision != args.runtime_source_revision:
        raise ValueError(
            "--runtime-source-revision must equal the exact managed source closure"
        )
    _write_json(
        root,
        "provenance/runtime-source-lock.json",
        {
            "schema_version": "aero-bench.runtime-source-lock/v1",
            "source_uri": SOURCE_URI,
            "source_revision_kind": "content_closure_sha256",
            "source_revision": source_revision,
            "selectors": ["aero_bench/**", "containers/**"],
            "files": source_records,
        },
    )
    _write_text(root, ".runtime-source-revision", args.runtime_source_revision + "\n")
    _write_text(
        root,
        "instruction.md",
        _instruction_text(agent_acceptance=agent_acceptance),
    )
    schemas = _schemas(root, agent_acceptance=agent_acceptance)
    package, artifacts = _task_package(
        root,
        _ref(root, schemas["observation"]),
        agent_acceptance=agent_acceptance,
    )
    goals, goal_bindings = _goals()
    del goals
    configs = _provider_configs(
        root, package, goal_bindings, agent_acceptance=agent_acceptance
    )
    assets = _materialize_world_assets(root)
    world = _world(root, assets)
    agent_artifact_schema_path = (
        _agent_artifact_schemas(root) if agent_acceptance else None
    )
    agent_driver_config_path = (
        _agent_driver_config(root) if agent_acceptance else None
    )
    images_path = Path(args.images_lock).resolve() if args.images_lock else None
    images, agent_metadata = _load_images(
        images_path, agent_acceptance=agent_acceptance
    )
    runtime_agent_metadata = (
        {
            "source_uri": SOURCE_URI,
            "source_revision": args.runtime_source_revision,
            "version": "0.4.0-agent-bridge.1",
        }
        if agent_acceptance
        else dict(agent_metadata)
    )
    formal = images_path is not None
    task_path, environment_path, agent_path = _runtime_specs(
        root,
        revision=args.runtime_source_revision,
        images=images,
        agent_metadata=agent_metadata,
        schemas=schemas,
        configs=configs,
        package=package,
        artifacts=artifacts,
        formal=formal,
        agent_acceptance=agent_acceptance,
        agent_artifact_schema_path=agent_artifact_schema_path,
        agent_driver_config_path=agent_driver_config_path,
    )
    scenario_digest, run_id = _source_validation(
        root,
        task_path=task_path,
        environment_path=environment_path,
        agent_path=agent_path,
        formal=formal,
        agent_acceptance=agent_acceptance,
    )
    marker = root / ".runtime-source-revision"
    marker.unlink()
    source_lock = {
        "schema_version": (
            "aero-bench.agent-inspection-source-lock/v1"
            if agent_acceptance
            else "aero-bench.urban-inspection-source-lock/v1"
        ),
        "status": "formal_digest_pinned" if formal else "awaiting_fresh_digest_pinned_images",
        "runtime_source_revision": args.runtime_source_revision if formal else None,
        "source_stage_base_revision": None if formal else args.runtime_source_revision,
        "world_id": world.world_id,
        "task_id": AGENT_TASK_ID if agent_acceptance else TASK_ID,
        "package_id": AGENT_PACKAGE_ID if agent_acceptance else PACKAGE_ID,
        "world_digest": world.world_digest,
        "world_asset_digest": world.asset_digest,
        "scenario_digest_with_declared_runtime_inputs": scenario_digest,
        "resolved_run_id": run_id if formal else None,
        "runtime_images": dict(sorted(images.items())) if formal else None,
        "participant_source": (
            dict(sorted(runtime_agent_metadata.items())) if formal else None
        ),
        "building_count": len(world.buildings),
        "launch_site_ids": list(LAUNCH_IDS),
        "semantic_target_ids": list(ALL_TARGET_IDS),
        "required_target_ids": list(REQUIRED_TARGET_IDS),
        "build_profile": (
            AGENT_ACCEPTANCE_PROFILE if agent_acceptance else "urban_infrastructure_v1"
        ),
        "sumo_vehicle_count": VEHICLE_COUNT,
        "sumo_person_count": PERSON_COUNT,
        "sumo_traffic_light_count": len(materialize_network().traffic_lights),
        "formal_component_ids": list(FORMAL_V2_COMPONENT_IDS),
        "alignment_sha256": _sha256(root / "world/alignment.json"),
        "task_package_sha256": _sha256(root / "task/inspection-package.json"),
        "agent_artifact_schemas_sha256": (
            _sha256(agent_artifact_schema_path)
            if agent_artifact_schema_path is not None
            else None
        ),
        "agent_driver_config_sha256": (
            _sha256(agent_driver_config_path)
            if agent_driver_config_path is not None
            else None
        ),
        "provider_config_sha256": {name: _sha256(path) for name, path in sorted(configs.items())},
    }
    _write_json(root, "source-lock.json", source_lock)
    if formal:
        _write_json(
            root,
            "image-lock.json",
            {
                "schema_version": "aero-bench.runtime-image-lock/v1",
                "runtime_source_revision": args.runtime_source_revision,
                "images": dict(sorted(images.items())),
                "agent": dict(sorted(runtime_agent_metadata.items())),
            },
        )
        _write_yaml(
            root,
            "runner.local.yaml",
            {
                "schema_version": "aero-bench.runner-config/v1",
                "executor_kind": "docker_reference",
                "output_root": (
                    "/tmp/aero-agent-inspection-v1-run"
                    if agent_acceptance
                    else "/tmp/aero-urban-inspection-v1-run"
                ),
                # The real PX4/Gazebo stack advances a 500 ms logical barrier
                # substantially slower than wall time under the declared
                # contact/render workload.  The formal bound is derived from
                # the 2,000-step environment budget plus startup/teardown
                # margin; two hours was below the measured envelope.
                "runtime_timeout_seconds": (
                    AGENT_RUNNER_RUNTIME_TIMEOUT_S if agent_acceptance else 14400
                ),
                "verifier_timeout_seconds": (
                    AGENT_VERIFIER_TIMEOUT_S if agent_acceptance else 600
                ),
                # Provider reset includes a real PX4/Gazebo warm-up.  The
                # image healthchecks have a 420 s startup grace period; keep
                # the executor bound above it so a transient probe failure
                # cannot abort a still-running, recoverable startup.
                "readiness_timeout_seconds": 480,
                "docker_binary": "docker",
                "input_mount_path": "/run/aero-input",
                "artifact_mount_path": "/run/aero-artifacts",
                "seal_mount_path": "/run/aero-seal",
                "volume_keeper_mount_path": "/run/aero-held",
                "input_volume_size_bytes": RUNNER_INPUT_VOLUME_SIZE_BYTES,
                "artifact_volume_size_bytes": RUNNER_ARTIFACT_VOLUME_SIZE_BYTES,
                "scratch_size_bytes": 8589934592,
                "pids_limit": 2048,
                "workload_uid": 65532,
                "workload_gid": 65532,
                "provider_bind_host": "0.0.0.0",
                "gateway_bind_host": "0.0.0.0",
                "volume_keeper_image": images["verifier"],
                "volume_keeper_command": ["/bin/sleep", "infinity"],
                "volume_keeper_cpu_millicores": 50,
                "volume_keeper_memory_mib": 64,
            },
        )
    _write_text(
        root,
        "README.md",
        (
            "# Agent Infrastructure Inspection Acceptance v1\n\n"
            if agent_acceptance
            else "# Urban Infrastructure Inspection v1\n\n"
        )
        + "Deterministic offline Shanghai 1 km × 1 km formal mission source. It contains 18 aligned buildings, "
        "two launch sites, eight semantic targets (three required separated visits), real Gazebo/SUMO "
        "inputs, twenty SUMO vehicles, five pedestrians, and traffic-light phases, wireless scenario bindings, geofence/no-fly/"
        "communications-shadow regions, deterministic weather, private observation/truth assets, and "
        "all 15 Formal Inspection v2 goal bindings.\n\n"
        + (
            "The acceptance profile requires API-discovered work orders, PX4 flight telemetry, raw GNSS, "
            "real Gazebo RGB frames, two distinct defect surfaces, one clean surface, and frame-bound "
            "detection/report artifacts.\n\n"
            if agent_acceptance
            else ""
        )
        + (
            "`suite.yaml` and `resolved-run.json` are digest-pinned formal inputs generated from the supplied fresh image lock.\n"
            if formal
            else "This source-stage bundle intentionally has no `suite.yaml`: runtime templates under `templates/` use non-runnable `registry.invalid` image identities solely for strict source resolution. Build fresh images, create an image lock, and rerun this tool with `--images-lock` before any formal run.\n"
        )
        + "\nNo online map, tile, scene asset, or simulator download is required.\n"
        + ("The declared Astra driver requires authorized access to its model service.\n" if agent_acceptance else ""),
    )
    print(json.dumps({"formal": formal, "output_root": str(root), "run_id": run_id if formal else None, "scenario_digest": scenario_digest, "status": source_lock["status"]}, sort_keys=True))
    return root


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--output-root", default=str(REPO_ROOT / "releases/urban-infrastructure-inspection-v1"))
    result.add_argument("--runtime-source-revision", required=True)
    result.add_argument("--images-lock")
    result.add_argument("--agent-acceptance", action="store_true")
    result.add_argument("--force", action="store_true")
    return result


def main() -> None:
    build(parser().parse_args())


if __name__ == "__main__":
    main()
