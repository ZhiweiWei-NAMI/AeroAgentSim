"""Dependency-free validation for serialized ``ResolvedScenario/v2`` values.

Provider images import this module without importing the host-side Pydantic graph.
The validator therefore mirrors the resolved wire contract with pure stdlib checks,
including canonical ordering, reference closure, and workload asset authorization.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$")
_UNIT = re.compile(r"^[A-Za-z][A-Za-z0-9_./^-]*$")
_LICENSE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9.+-]*|LicenseRef-[A-Za-z0-9][A-Za-z0-9.+-]*)$"
)
_SELECTOR_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_SELECTOR = re.compile(
    rf"{_SELECTOR_SEGMENT}(?:/{_SELECTOR_SEGMENT})*"
    rf"(?:#{_SELECTOR_SEGMENT}(?:/{_SELECTOR_SEGMENT})*)?$"
)
_FRAME_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$"
)
_SUMO_OBJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_MAX_SEED = 9_223_372_036_854_775_807
_MATRIX_TOLERANCE = 1e-12
_QUATERNION_TOLERANCE = 1e-9
_DERIVED_TOLERANCE_M = 1e-5
_WGS84_SEMI_MAJOR_AXIS_M = 6_378_137.0
_WGS84_FLATTENING = 1.0 / 298.257_223_563
_WGS84_ECCENTRICITY_SQUARED = _WGS84_FLATTENING * (
    2.0 - _WGS84_FLATTENING
)
_ENU_TO_NED = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0))

_SCENARIO_FIELDS = frozenset(
    {
        "schema_version",
        "source_world_package",
        "world_schema_version",
        "world_id",
        "world_digest",
        "source_asset_digest",
        "scenario_asset_digest",
        "selected_launch_site_id",
        "seed",
        "frame_authority",
        "providers",
        "assets",
        "task",
        "base_layers",
        "layers",
        "buildings",
        "roads",
        "regions",
        "launch_sites",
        "entities",
        "sensors",
        "semantic_targets",
        "weather",
        "engine_frame_bindings",
        "sumo",
        "network",
        "mission_requirements",
        "expected_public_assets",
        "scenario_digest",
    }
)
_FILE_REF_FIELDS = frozenset({"path", "sha256"})
_FRAME_AUTHORITY_FIELDS = frozenset(
    {
        "geodetic_frame_id",
        "ecef_frame_id",
        "enu_frame_id",
        "ned_frame_id",
        "origin",
        "origin_ecef",
        "ecef_to_enu_rotation",
        "enu_to_ecef_rotation",
        "enu_to_ned_rotation",
        "ned_to_enu_rotation",
        "spatial_extent",
        "geoid_correction_asset_id",
        "terrain_height_asset_id",
        "geoid_interpolation",
        "terrain_interpolation",
        "geoid_precision_m",
        "terrain_precision_m",
    }
)
_SPATIAL_BOUNDS_FIELDS = frozenset(
    {
        "min_east_m",
        "max_east_m",
        "min_north_m",
        "max_north_m",
        "min_up_m",
        "max_up_m",
        "vertical_reference",
    }
)
_COORDINATE_FIELDS = frozenset(
    {
        "enu",
        "ned",
        "ecef",
        "wgs84",
        "geoid_separation_m",
        "amsl_m",
        "terrain_amsl_m",
        "agl_m",
    }
)
_ENU_FIELDS = frozenset({"east_m", "north_m", "up_m"})
_NED_FIELDS = frozenset({"north_m", "east_m", "down_m"})
_ECEF_FIELDS = frozenset({"x_m", "y_m", "z_m"})
_WGS84_FIELDS = frozenset(
    {"longitude_deg", "latitude_deg", "ellipsoid_height_m"}
)
_QUATERNION_FIELDS = frozenset({"qw", "qx", "qy", "qz"})
_ROTATION_FIELDS = frozenset({"rows"})
_POSE_FIELDS = frozenset({"position", "orientation_enu", "orientation_ned"})
_PROVIDER_FIELDS = frozenset({"provider_id", "runtime_stage", "roles", "capability_ids"})
_RUNTIME_STAGES = frozenset({"motion", "network", "business_environment"})
_ASSET_FIELDS = frozenset(
    {
        "source_kind",
        "asset_id",
        "file",
        "byte_size",
        "classification",
        "audiences",
        "world",
    }
)
_ASSET_AUDIENCE_FIELDS = frozenset({"role", "workload_id"})
_WORLD_ASSET_FIELDS = frozenset(
    {
        "asset_role",
        "media_type",
        "units",
        "precision",
        "source_frame",
        "provenance",
        "selector",
        "selector_fragment",
        "license",
    }
)
_LICENSE_FIELDS = frozenset(
    {"license_id", "selector", "selector_fragment", "file", "byte_size"}
)
_UNIT_FIELDS = frozenset({"quantity", "unit"})
_PRECISION_FIELDS = frozenset(
    {"quantity", "kind", "unit", "exact_bytes", "absolute_tolerance"}
)
_PROVENANCE_FIELDS = frozenset(
    {
        "source_kind",
        "recorded_by",
        "source_dataset",
        "source_version",
        "runtime_download",
    }
)
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "package_id",
        "package_config",
        "package_schema",
        "instruction",
        "verifier_id",
        "required_capability_ids",
        "required_tool_ids",
        "logical_endpoint_ids",
        "asset_ids",
        "tools",
        "queries",
        "observations",
        "goals",
        "task_contract_digest",
    }
)
_TASK_TOOL_FIELDS = frozenset(
    {
        "binding_id",
        "agent_id",
        "tool_id",
        "endpoint_id",
        "request_schema",
        "response_schema",
        "timeout_ms",
        "idempotent",
    }
)
_TASK_QUERY_FIELDS = frozenset(
    {
        "binding_id",
        "agent_id",
        "query_type",
        "endpoint_id",
        "request_schema",
        "response_schema",
        "timeout_ms",
    }
)
_TASK_OBSERVATION_FIELDS = frozenset(
    {
        "binding_id",
        "agent_id",
        "observation_id",
        "endpoint_id",
        "schema_file",
        "timeout_ms",
        "inspection",
        "urban",
        "flight",
    }
)
_INSPECTION_OBSERVATION_FIELDS = frozenset(
    {
        "projection_kind",
        "observation_id",
        "work_order_id",
        "target_id",
        "simulation_asset_id",
        "sensor_id",
        "media_type",
        "min_distance_m",
        "max_distance_m",
        "min_view_angle_deg",
        "max_view_angle_deg",
        "earliest_time_ns",
        "latest_time_ns",
    }
)
_URBAN_OBSERVATION_FIELDS = frozenset(
    {
        "projection_kind",
        "observation_id",
        "observation_kind",
        "vehicle_id",
        "camera_id",
    }
)
_FLIGHT_OBSERVATION_FIELDS = frozenset(
    {"projection_kind", "observation_id", "observation_kind", "vehicle_id"}
)
_GOAL_FIELDS = frozenset(
    {
        "goal_id",
        "verifier_id",
        "metric_id",
        "operator",
        "threshold",
        "evidence",
        "parameters",
    }
)
_NAMED_VALUE_FIELDS = frozenset({"name", "value"})
_LAYER_FIELDS = frozenset(
    {"layer_id", "kind", "asset_id", "visibility", "default_visible"}
)
_BUILDING_FIELDS = frozenset(
    {
        "building_id",
        "entity_id",
        "source_asset_id",
        "render_asset_id",
        "collision_asset_id",
        "anchor_east_m",
        "anchor_north_m",
        "base_vertices",
        "top_vertices",
    }
)
_ROAD_FIELDS = frozenset({"road_id", "kind", "width_m", "terrain_points"})
_REGION_FIELDS = frozenset(
    {
        "region_id",
        "kind",
        "communications_shadow_attenuation_db",
        "anchor_east_m",
        "anchor_north_m",
        "lower_vertices",
        "upper_vertices",
    }
)
_LAUNCH_SITE_FIELDS = frozenset(
    {
        "launch_site_id",
        "primary_uav_entity_id",
        "allowed_uav_entity_ids",
        "pose",
        "pad_radius_m",
        "selected",
    }
)
_ENTITY_FIELDS = frozenset(
    {
        "entity_id",
        "kind",
        "owner_kind",
        "owner_id",
        "source_provider_id",
        "authority_kind",
        "state",
        "model_asset_id",
        "initial_pose",
        "selected_launch_override",
    }
)
_SENSOR_FIELDS = frozenset(
    {
        "sensor_id",
        "provider_id",
        "parent_entity_id",
        "kind",
        "initial_pose",
        "horizontal_fov_deg",
        "vertical_fov_deg",
        "resolution_width_px",
        "resolution_height_px",
    }
)
_TARGET_FIELDS = frozenset(
    {
        "target_id",
        "parent_entity_id",
        "required_sensor_id",
        "pose",
        "geometry",
        "surface_normal_target",
        "min_distance_m",
        "max_distance_m",
        "max_view_angle_deg",
        "fov_margin_deg",
        "dwell_time_s",
        "required_evidence",
    }
)
_TARGET_GEOMETRY_FIELDS = frozenset({"shape", "size_x_m", "size_y_m", "size_z_m"})
_TARGET_SURFACE_NORMAL_FIELDS = frozenset({"x", "y", "z"})
_WEATHER_FIELDS = frozenset(
    {
        "sample_id",
        "mode",
        "wind",
        "visibility_m",
        "precipitation",
        "precipitation_rate_mm_per_h",
        "temperature_c",
        "pressure_pa",
    }
)
_WIND_FIELDS = frozenset({"east_mps", "north_mps", "up_mps"})
_ENGINE_BINDING_FIELDS = frozenset(
    {
        "binding_id",
        "provider_id",
        "engine",
        "scene_asset_id",
        "source_frame_id",
        "target_frame_id",
        "translation_m",
        "rotation_matrix",
        "rotation_quaternion",
    }
)
_SUMO_FIELDS = frozenset(
    {
        "provider_id",
        "config_asset_id",
        "network_asset_id",
        "routes_asset_id",
        "additional_asset_id",
        "frame_binding_id",
        "object_bindings",
    }
)
_SUMO_OBJECT_FIELDS = frozenset({"sumo_object_id", "entity_id", "kind"})
_NETWORK_FIELDS = frozenset({"provider_id", "radio_profiles", "node_bindings", "links"})
_RADIO_PROFILE_FIELDS = frozenset(
    {
        "radio_profile_id",
        "provider_id",
        "wifi_standard",
        "frequency_ghz",
        "channel_width_mhz",
        "tx_power_dbm",
        "rx_sensitivity_dbm",
    }
)
_NETWORK_NODE_FIELDS = frozenset(
    {"node_id", "entity_id", "endpoint_id", "radio_profile_id"}
)
_NETWORK_LINK_FIELDS = frozenset(
    {
        "link_id",
        "source_node_id",
        "destination_node_id",
        "data_rate_bps",
        "propagation_delay_ns",
    }
)
_MISSION_FIELDS = frozenset(
    {
        "requirement_id",
        "kind",
        "dependencies",
        "launch_site_id",
        "target_id",
        "expected_public_asset_id",
    }
)
_EXPECTED_PUBLIC_ASSET_FIELDS = frozenset(
    {
        "asset_id",
        "kind",
        "producer_requirement_id",
        "media_type",
        "units",
        "precision",
    }
)

_PROVIDER_ROLES = frozenset(
    {"mission", "motion", "sensor", "static_scene", "traffic", "wireless_network"}
)
_ASSET_ROLES = frozenset(
    {
        "geoid_model",
        "terrain_model",
        "imagery_tiles",
        "terrain_tiles",
        "layer_tiles",
        "building_source",
        "building_render",
        "building_collision",
        "entity_model",
        "sumo_config",
        "sumo_network",
        "sumo_routes",
        "sumo_additional",
        "osm_source",
        "license",
        "other",
    }
)
_SOURCE_FRAMES = frozenset(
    {
        "WGS84",
        "ECEF",
        "ENU",
        "NED",
        "body",
        "parent",
        "sumo_net",
        "non_spatial",
        "asset_local",
        "raster_pixel",
    }
)
_ASSET_ROLE_SOURCE_FRAMES = {
    "geoid_model": frozenset({"raster_pixel"}),
    "terrain_model": frozenset({"raster_pixel"}),
    "imagery_tiles": frozenset({"raster_pixel", "WGS84"}),
    "terrain_tiles": frozenset({"raster_pixel", "ENU"}),
    "layer_tiles": frozenset({"ENU", "WGS84", "raster_pixel", "asset_local"}),
    "building_source": frozenset({"asset_local"}),
    "building_render": frozenset({"WGS84", "asset_local"}),
    "building_collision": frozenset({"asset_local"}),
    "entity_model": frozenset({"asset_local"}),
    "sumo_config": frozenset({"non_spatial"}),
    "sumo_network": frozenset({"sumo_net"}),
    "sumo_routes": frozenset({"non_spatial"}),
    "sumo_additional": frozenset({"non_spatial"}),
    "osm_source": frozenset({"WGS84"}),
    "license": frozenset({"non_spatial"}),
    "other": frozenset({"non_spatial", "asset_local", "ENU", "WGS84"}),
}
_ROAD_KINDS = frozenset(
    {"vehicle_lane", "pedestrian_path", "runway", "taxiway", "service_road"}
)
_REGION_KINDS = frozenset({"geofence", "no_fly", "communications_shadow"})
_EVIDENCE_KINDS = frozenset(
    {
        "sensor_frame",
        "image",
        "report",
        "network_upload_or_buffer",
        "bbox_2d",
        "segmentation_mask",
        "pose_6d",
        "track",
    }
)
_PRECIPITATION_KINDS = frozenset({"none", "drizzle", "rain", "snow", "hail"})
_MISSION_KINDS = frozenset(
    {"takeoff", "observation", "upload_or_buffer", "return_to_launch", "land"}
)
_EXPECTED_PUBLIC_ASSET_KINDS = frozenset(
    {"viewer_layer", "mission_output", "evidence_bundle"}
)


class WorkloadScenarioError(ValueError):
    """Raised when a container workload receives a forged scenario projection."""


@dataclass(frozen=True)
class ValidatedWorkloadScenario:
    scenario_digest: str
    scenario: dict[str, Any]
    assets: tuple[dict[str, Any], ...]

    def asset(self, asset_id: str) -> dict[str, Any]:
        matches = tuple(asset for asset in self.assets if asset["asset_id"] == asset_id)
        if len(matches) != 1:
            raise WorkloadScenarioError(
                f"ResolvedScenario does not project exactly one asset {asset_id!r}"
            )
        return matches[0]


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise WorkloadScenarioError("ResolvedScenario is not canonical JSON data") from error


def _exact_object(value: object, fields: frozenset[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise WorkloadScenarioError(f"{label} fields are not exact")
    return value


def _array(
    value: object,
    *,
    label: str,
    minimum: int = 0,
) -> list[Any]:
    if type(value) is not list or len(value) < minimum:
        qualifier = f" with at least {minimum} item(s)" if minimum else ""
        raise WorkloadScenarioError(f"{label} must be an array{qualifier}")
    return value


def _string(
    value: object,
    *,
    label: str,
    identifier: bool = False,
    minimum: int = 1,
    maximum: int | None = None,
) -> str:
    if type(value) is not str or len(value) < minimum:
        raise WorkloadScenarioError(f"{label} must be a non-empty string")
    if maximum is not None and len(value) > maximum:
        raise WorkloadScenarioError(f"{label} is too long")
    if identifier and _IDENTIFIER.fullmatch(value) is None:
        raise WorkloadScenarioError(f"{label} must be an Identifier")
    return value


def _enum(value: object, allowed: frozenset[str], *, label: str) -> str:
    result = _string(value, label=label)
    if result not in allowed:
        raise WorkloadScenarioError(f"{label} is unsupported")
    return result


def _digest(value: object, *, label: str) -> str:
    result = _string(value, label=label)
    if _SHA256.fullmatch(result) is None or result == "0" * 64:
        raise WorkloadScenarioError(f"{label} must be a real SHA-256 digest")
    return result


def _integer(
    value: object,
    *,
    label: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise WorkloadScenarioError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise WorkloadScenarioError(f"{label} is outside its allowed range")
    return value


def _number(
    value: object,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
    exclusive_maximum: bool = False,
) -> float:
    if type(value) is not float:
        raise WorkloadScenarioError(f"{label} must be a finite number")
    result = value
    if not math.isfinite(result):
        raise WorkloadScenarioError(f"{label} must be a finite number")
    if minimum is not None and (
        result < minimum or (exclusive_minimum and result == minimum)
    ):
        raise WorkloadScenarioError(f"{label} is outside its allowed range")
    if maximum is not None and (
        result > maximum or (exclusive_maximum and result == maximum)
    ):
        raise WorkloadScenarioError(f"{label} is outside its allowed range")
    return result


def _boolean(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise WorkloadScenarioError(f"{label} must be a boolean")
    return value


def _nullable_identifier(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label=label, identifier=True)


def _relative_path(value: object, *, label: str) -> str:
    text = _string(value, label=label)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or str(path) != text
        or text.strip() != text
        or "\\" in text
        or "://" in text
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in text)
    ):
        raise WorkloadScenarioError(f"{label} must be a normalized bundle path")
    return text


def _file_ref(value: object, *, label: str) -> dict[str, Any]:
    reference = _exact_object(value, _FILE_REF_FIELDS, label=label)
    _relative_path(reference["path"], label=f"{label}.path")
    _digest(reference["sha256"], label=f"{label}.sha256")
    return reference


def _selector(value: object, *, label: str) -> tuple[str, str | None]:
    text = _string(value, label=label, maximum=512)
    if (
        text.strip() != text
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in text)
        or "\\" in text
        or "//" in text
        or _SELECTOR.fullmatch(text) is None
    ):
        raise WorkloadScenarioError(f"{label} is not a normalized artifact selector")
    path, separator, fragment = text.partition("#")
    return path, fragment if separator else None


def _media_type(value: object, *, label: str) -> str:
    text = _string(value, label=label, maximum=128)
    if len(text) < 3 or _MEDIA_TYPE.fullmatch(text) is None:
        raise WorkloadScenarioError(f"{label} must be a normalized media type")
    return text


def _frame_id(value: object, *, label: str) -> str:
    text = _string(value, label=label, maximum=64)
    if text.strip() != text or _FRAME_ID.fullmatch(text) is None:
        raise WorkloadScenarioError(f"{label} must be a normalized frame ID")
    return text


def _string_list(
    value: object,
    *,
    label: str,
    allow_empty: bool = False,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    values = _array(value, label=label, minimum=0 if allow_empty else 1)
    result = tuple(
        _string(item, label=f"{label}[{index}]", identifier=allowed is None)
        for index, item in enumerate(values)
    )
    if allowed is not None and any(item not in allowed for item in result):
        raise WorkloadScenarioError(f"{label} contains an unsupported value")
    if len(result) != len(set(result)) or result != tuple(sorted(result)):
        raise WorkloadScenarioError(f"{label} must be sorted and unique")
    return result


def _sorted_record_keys(keys: list[str], *, label: str) -> None:
    if len(keys) != len(set(keys)) or keys != sorted(keys):
        raise WorkloadScenarioError(f"{label} must be sorted and unique")


def _close(
    left: float,
    right: float,
    *,
    absolute: float = 1e-9,
    relative: float = 1e-12,
) -> bool:
    return math.isclose(left, right, abs_tol=absolute, rel_tol=relative)


def _matrix_multiply(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _matrix_transpose(
    matrix: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def _matrix_apply(
    matrix: tuple[tuple[float, float, float], ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(sum(row[index] * vector[index] for index in range(3)) for row in matrix)


def _matrices_close(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
    *,
    tolerance: float = 1e-10,
) -> bool:
    return all(
        abs(left[row][column] - right[row][column]) <= tolerance
        for row in range(3)
        for column in range(3)
    )


def _validate_rotation(value: object, *, label: str) -> tuple[tuple[float, float, float], ...]:
    rotation = _exact_object(value, _ROTATION_FIELDS, label=label)
    raw_rows = _array(rotation["rows"], label=f"{label}.rows")
    if len(raw_rows) != 3:
        raise WorkloadScenarioError(f"{label}.rows must contain exactly three rows")
    rows: list[tuple[float, float, float]] = []
    for row_index, raw_row in enumerate(raw_rows):
        values = _array(raw_row, label=f"{label}.rows[{row_index}]")
        if len(values) != 3:
            raise WorkloadScenarioError(
                f"{label}.rows[{row_index}] must contain exactly three values"
            )
        rows.append(
            tuple(
                _number(
                    item,
                    label=f"{label}.rows[{row_index}][{column_index}]",
                )
                for column_index, item in enumerate(values)
            )
        )
    matrix = tuple(rows)
    for row in matrix:
        if abs(math.sqrt(sum(component * component for component in row)) - 1.0) > _MATRIX_TOLERANCE:
            raise WorkloadScenarioError(f"{label} rows must be unit length")
    for first in range(3):
        for second in range(first + 1, 3):
            dot = sum(matrix[first][index] * matrix[second][index] for index in range(3))
            if abs(dot) > _MATRIX_TOLERANCE:
                raise WorkloadScenarioError(f"{label} rows must be orthogonal")
    determinant = (
        matrix[0][0] * matrix[1][1] * matrix[2][2]
        + matrix[0][1] * matrix[1][2] * matrix[2][0]
        + matrix[0][2] * matrix[1][0] * matrix[2][1]
        - matrix[0][2] * matrix[1][1] * matrix[2][0]
        - matrix[0][1] * matrix[1][0] * matrix[2][2]
        - matrix[0][0] * matrix[1][2] * matrix[2][1]
    )
    if abs(determinant - 1.0) > _MATRIX_TOLERANCE:
        raise WorkloadScenarioError(f"{label} must be a right-handed rotation")
    return matrix


def _canonical_quaternion(
    quaternion: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    w, x, y, z = quaternion

    def negative(value: float) -> bool:
        return math.copysign(1.0, value) < 0.0

    if w < 0.0 or (
        w == 0.0
        and (negative(x) or (x == 0.0 and (negative(y) or (y == 0.0 and negative(z)))))
    ):
        return -w, -x, -y, -z
    return quaternion


def _validate_quaternion(
    value: object, *, label: str
) -> tuple[float, float, float, float]:
    quaternion = _exact_object(value, _QUATERNION_FIELDS, label=label)
    result = tuple(
        _number(quaternion[field], label=f"{label}.{field}")
        for field in ("qw", "qx", "qy", "qz")
    )
    if abs(math.sqrt(sum(component * component for component in result)) - 1.0) > _QUATERNION_TOLERANCE:
        raise WorkloadScenarioError(f"{label} must be a unit quaternion")
    if result != _canonical_quaternion(result):
        raise WorkloadScenarioError(f"{label} must use the canonical quaternion sign")
    return result


def _rotation_from_quaternion(
    quaternion: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    w, x, y, z = quaternion
    return (
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
        ),
        (
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
        ),
        (
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ),
    )


def _wgs84_to_ecef(
    longitude_deg: float,
    latitude_deg: float,
    height_m: float,
) -> tuple[float, float, float]:
    longitude = math.radians(longitude_deg)
    latitude = math.radians(latitude_deg)
    sin_latitude = math.sin(latitude)
    cos_latitude = math.cos(latitude)
    radius = _WGS84_SEMI_MAJOR_AXIS_M / math.sqrt(
        1.0 - _WGS84_ECCENTRICITY_SQUARED * sin_latitude * sin_latitude
    )
    return (
        (radius + height_m) * cos_latitude * math.cos(longitude),
        (radius + height_m) * cos_latitude * math.sin(longitude),
        (radius * (1.0 - _WGS84_ECCENTRICITY_SQUARED) + height_m)
        * sin_latitude,
    )


def _validate_coordinate(value: object, *, label: str) -> dict[str, Any]:
    coordinate = _exact_object(value, _COORDINATE_FIELDS, label=label)
    enu = _exact_object(coordinate["enu"], _ENU_FIELDS, label=f"{label}.enu")
    ned = _exact_object(coordinate["ned"], _NED_FIELDS, label=f"{label}.ned")
    ecef = _exact_object(coordinate["ecef"], _ECEF_FIELDS, label=f"{label}.ecef")
    wgs84 = _exact_object(coordinate["wgs84"], _WGS84_FIELDS, label=f"{label}.wgs84")
    for field in ("east_m", "north_m", "up_m"):
        _number(enu[field], label=f"{label}.enu.{field}")
    for field in ("north_m", "east_m", "down_m"):
        _number(ned[field], label=f"{label}.ned.{field}")
    for field in ("x_m", "y_m", "z_m"):
        _number(ecef[field], label=f"{label}.ecef.{field}")
    longitude = _number(
        wgs84["longitude_deg"],
        label=f"{label}.wgs84.longitude_deg",
        minimum=-180.0,
        maximum=180.0,
        exclusive_maximum=True,
    )
    latitude = _number(
        wgs84["latitude_deg"],
        label=f"{label}.wgs84.latitude_deg",
        minimum=-90.0,
        maximum=90.0,
        exclusive_minimum=True,
        exclusive_maximum=True,
    )
    height = _number(
        wgs84["ellipsoid_height_m"],
        label=f"{label}.wgs84.ellipsoid_height_m",
    )
    for field in ("geoid_separation_m", "amsl_m", "terrain_amsl_m", "agl_m"):
        _number(coordinate[field], label=f"{label}.{field}")

    if not (
        _close(float(ned["north_m"]), float(enu["north_m"]))
        and _close(float(ned["east_m"]), float(enu["east_m"]))
        and _close(float(ned["down_m"]), -float(enu["up_m"]))
    ):
        raise WorkloadScenarioError(f"{label} ENU and NED coordinates disagree")
    if not _close(
        float(coordinate["amsl_m"]),
        height - float(coordinate["geoid_separation_m"]),
        absolute=1e-6,
    ):
        raise WorkloadScenarioError(f"{label} AMSL relationship is inconsistent")
    if not _close(
        float(coordinate["agl_m"]),
        float(coordinate["amsl_m"]) - float(coordinate["terrain_amsl_m"]),
        absolute=1e-6,
    ):
        raise WorkloadScenarioError(f"{label} AGL relationship is inconsistent")
    expected_ecef = _wgs84_to_ecef(longitude, latitude, height)
    actual_ecef = (float(ecef["x_m"]), float(ecef["y_m"]), float(ecef["z_m"]))
    if any(
        not _close(actual, expected, absolute=1e-3)
        for actual, expected in zip(actual_ecef, expected_ecef)
    ):
        raise WorkloadScenarioError(f"{label} WGS84 and ECEF coordinates disagree")
    return coordinate


def _validate_pose(value: object, *, label: str) -> dict[str, Any]:
    pose = _exact_object(value, _POSE_FIELDS, label=label)
    _validate_coordinate(pose["position"], label=f"{label}.position")
    enu_quaternion = _validate_quaternion(
        pose["orientation_enu"], label=f"{label}.orientation_enu"
    )
    ned_quaternion = _validate_quaternion(
        pose["orientation_ned"], label=f"{label}.orientation_ned"
    )
    expected_ned = _matrix_multiply(
        _ENU_TO_NED, _rotation_from_quaternion(enu_quaternion)
    )
    if not _matrices_close(
        expected_ned,
        _rotation_from_quaternion(ned_quaternion),
        tolerance=2e-9,
    ):
        raise WorkloadScenarioError(f"{label} ENU and NED orientations disagree")
    return pose


def _validate_bounds(value: object, *, label: str) -> dict[str, Any]:
    bounds = _exact_object(value, _SPATIAL_BOUNDS_FIELDS, label=label)
    numbers = {
        field: _number(bounds[field], label=f"{label}.{field}")
        for field in (
            "min_east_m",
            "max_east_m",
            "min_north_m",
            "max_north_m",
            "min_up_m",
            "max_up_m",
        )
    }
    if bounds["vertical_reference"] != "enu_up":
        raise WorkloadScenarioError(f"{label}.vertical_reference is unsupported")
    for low, high in (
        ("min_east_m", "max_east_m"),
        ("min_north_m", "max_north_m"),
        ("min_up_m", "max_up_m"),
    ):
        if numbers[low] >= numbers[high]:
            raise WorkloadScenarioError(f"{label} has unordered bounds")
    return bounds


def _validate_coordinate_in_frame(
    coordinate: dict[str, Any],
    *,
    frame: dict[str, Any],
    label: str,
) -> None:
    bounds = frame["spatial_extent"]
    enu = coordinate["enu"]
    if not (
        float(bounds["min_east_m"])
        <= float(enu["east_m"])
        <= float(bounds["max_east_m"])
        and float(bounds["min_north_m"])
        <= float(enu["north_m"])
        <= float(bounds["max_north_m"])
        and float(bounds["min_up_m"])
        <= float(enu["up_m"])
        <= float(bounds["max_up_m"])
    ):
        raise WorkloadScenarioError(f"{label} lies outside spatial_extent")
    translated = _matrix_apply(
        frame["_enu_to_ecef"],
        (float(enu["east_m"]), float(enu["north_m"]), float(enu["up_m"])),
    )
    origin_ecef = frame["origin_ecef"]
    expected = (
        float(origin_ecef["x_m"]) + translated[0],
        float(origin_ecef["y_m"]) + translated[1],
        float(origin_ecef["z_m"]) + translated[2],
    )
    actual = coordinate["ecef"]
    if any(
        not _close(float(actual[field]), expected[index], absolute=_DERIVED_TOLERANCE_M)
        for index, field in enumerate(("x_m", "y_m", "z_m"))
    ):
        raise WorkloadScenarioError(f"{label} does not use frame authority")


def _validate_pose_in_frame(
    pose: dict[str, Any], *, frame: dict[str, Any], label: str
) -> None:
    _validate_coordinate_in_frame(pose["position"], frame=frame, label=label)


def _validate_measurement_metadata(
    units_value: object,
    precision_value: object,
    *,
    label: str,
) -> None:
    units = _array(units_value, label=f"{label}.units", minimum=1)
    unit_by_quantity: dict[str, str] = {}
    for index, value in enumerate(units):
        item_label = f"{label}.units[{index}]"
        unit = _exact_object(value, _UNIT_FIELDS, label=item_label)
        quantity = _string(unit["quantity"], label=f"{item_label}.quantity", identifier=True)
        unit_value = _string(unit["unit"], label=f"{item_label}.unit", maximum=32)
        if _UNIT.fullmatch(unit_value) is None:
            raise WorkloadScenarioError(f"{item_label}.unit is invalid")
        if quantity in unit_by_quantity:
            raise WorkloadScenarioError(f"{label} unit quantities must be unique")
        unit_by_quantity[quantity] = unit_value

    precision = _array(precision_value, label=f"{label}.precision", minimum=1)
    precision_quantities: set[str] = set()
    for index, value in enumerate(precision):
        item_label = f"{label}.precision[{index}]"
        item = _exact_object(value, _PRECISION_FIELDS, label=item_label)
        quantity = _string(item["quantity"], label=f"{item_label}.quantity", identifier=True)
        kind = _enum(
            item["kind"],
            frozenset({"exact_bytes", "absolute_tolerance"}),
            label=f"{item_label}.kind",
        )
        unit_value = _string(item["unit"], label=f"{item_label}.unit", maximum=32)
        if _UNIT.fullmatch(unit_value) is None:
            raise WorkloadScenarioError(f"{item_label}.unit is invalid")
        if quantity in precision_quantities:
            raise WorkloadScenarioError(f"{label} precision quantities must be unique")
        precision_quantities.add(quantity)
        if kind == "exact_bytes":
            _integer(item["exact_bytes"], label=f"{item_label}.exact_bytes", minimum=1)
            if item["absolute_tolerance"] is not None:
                raise WorkloadScenarioError(
                    f"{item_label} exact_bytes precision forbids absolute_tolerance"
                )
        else:
            if item["exact_bytes"] is not None:
                raise WorkloadScenarioError(
                    f"{item_label} absolute_tolerance precision forbids exact_bytes"
                )
            _number(
                item["absolute_tolerance"],
                label=f"{item_label}.absolute_tolerance",
                minimum=0.0,
                exclusive_minimum=True,
            )
        if unit_by_quantity.get(quantity) != unit_value:
            raise WorkloadScenarioError(
                f"{item_label} does not match its declared measurement unit"
            )
    if set(unit_by_quantity) != precision_quantities:
        raise WorkloadScenarioError(f"{label} units and precision are not closed")


def _validate_world_asset_metadata(
    value: object,
    *,
    file_ref: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    world = _exact_object(value, _WORLD_ASSET_FIELDS, label=label)
    asset_role = _enum(world["asset_role"], _ASSET_ROLES, label=f"{label}.asset_role")
    media_type = _media_type(world["media_type"], label=f"{label}.media_type")
    if asset_role == "license" and not media_type.startswith("text/"):
        raise WorkloadScenarioError(f"{label}.media_type is invalid for a license asset")
    _validate_measurement_metadata(world["units"], world["precision"], label=label)
    source_frame = _enum(world["source_frame"], _SOURCE_FRAMES, label=f"{label}.source_frame")
    if source_frame not in _ASSET_ROLE_SOURCE_FRAMES[asset_role]:
        raise WorkloadScenarioError(f"{label}.source_frame is invalid for asset_role")
    if (
        asset_role == "building_render"
        and media_type in ("model/gltf-binary", "model/gltf+json")
        and source_frame == "WGS84"
    ):
        raise WorkloadScenarioError(
            f"{label}.source_frame must be asset_local for a glTF building_render"
        )

    provenance = _exact_object(
        world["provenance"], _PROVENANCE_FIELDS, label=f"{label}.provenance"
    )
    if provenance["source_kind"] != "bundled_offline":
        raise WorkloadScenarioError(f"{label}.provenance.source_kind is unsupported")
    _string(
        provenance["recorded_by"],
        label=f"{label}.provenance.recorded_by",
        identifier=True,
    )
    _string(
        provenance["source_dataset"],
        label=f"{label}.provenance.source_dataset",
        maximum=128,
    )
    _string(
        provenance["source_version"],
        label=f"{label}.provenance.source_version",
        maximum=64,
    )
    if provenance["runtime_download"] is not False:
        raise WorkloadScenarioError(f"{label}.provenance.runtime_download must be false")

    selector_path, selector_fragment = _selector(
        world["selector"], label=f"{label}.selector"
    )
    if selector_path != file_ref["path"] or world["selector_fragment"] != selector_fragment:
        raise WorkloadScenarioError(f"{label}.selector does not bind the asset file")

    license_value = _exact_object(
        world["license"], _LICENSE_FIELDS, label=f"{label}.license"
    )
    license_id = _string(
        license_value["license_id"], label=f"{label}.license.license_id", maximum=128
    )
    if _LICENSE.fullmatch(license_id) is None:
        raise WorkloadScenarioError(f"{label}.license.license_id is invalid")
    license_file = _file_ref(
        license_value["file"], label=f"{label}.license.file"
    )
    _integer(
        license_value["byte_size"],
        label=f"{label}.license.byte_size",
        minimum=1,
    )
    license_path, license_fragment = _selector(
        license_value["selector"], label=f"{label}.license.selector"
    )
    if (
        license_path != license_file["path"]
        or license_value["selector_fragment"] != license_fragment
    ):
        raise WorkloadScenarioError(f"{label}.license.selector does not bind its file")
    return world


def _validated_assets(value: object) -> tuple[dict[str, Any], ...]:
    raw_assets = _array(value, label="ResolvedScenario.assets", minimum=1)
    assets: list[dict[str, Any]] = []
    asset_ids: list[str] = []
    payload_paths: list[str] = []
    license_paths: set[str] = set()
    for index, item in enumerate(raw_assets):
        label = f"ResolvedScenario.assets[{index}]"
        asset = _exact_object(item, _ASSET_FIELDS, label=label)
        source_kind = _enum(
            asset["source_kind"], frozenset({"world", "task"}), label=f"{label}.source_kind"
        )
        asset_id = _string(asset["asset_id"], label=f"{label}.asset_id", identifier=True)
        file_ref = _file_ref(asset["file"], label=f"{label}.file")
        payload_paths.append(file_ref["path"])
        _integer(asset["byte_size"], label=f"{label}.byte_size", minimum=1)
        classification = _enum(
            asset["classification"],
            frozenset({"public", "private"}),
            label=f"{label}.classification",
        )
        audiences = _array(asset["audiences"], label=f"{label}.audiences")
        audience_keys: list[str] = []
        audience_roles: set[str] = set()
        for audience_index, audience_value in enumerate(audiences):
            audience_label = f"{label}.audiences[{audience_index}]"
            audience = _exact_object(
                audience_value, _ASSET_AUDIENCE_FIELDS, label=audience_label
            )
            role = _enum(
                audience["role"],
                frozenset({"harness", "provider", "agent", "verifier"}),
                label=f"{audience_label}.role",
            )
            workload_id = _string(
                audience["workload_id"],
                label=f"{audience_label}.workload_id",
                identifier=True,
            )
            audience_keys.append(f"{role}:{workload_id}")
            audience_roles.add(role)
        _sorted_record_keys(audience_keys, label=f"{label}.audiences")

        if source_kind == "task":
            if not audiences:
                raise WorkloadScenarioError(f"{label} task asset requires an audience")
            if asset["world"] is not None:
                raise WorkloadScenarioError(f"{label} task asset carries world metadata")
            if "harness" in audience_roles:
                raise WorkloadScenarioError(f"{label} task asset authorizes the harness")
            if classification == "private" and "agent" in audience_roles:
                raise WorkloadScenarioError(f"{label} private task asset authorizes an agent")
        else:
            world = _validate_world_asset_metadata(
                asset["world"], file_ref=file_ref, label=f"{label}.world"
            )
            license_paths.add(world["license"]["file"]["path"])
            if classification == "private" and audience_roles & {"harness", "agent"}:
                raise WorkloadScenarioError(
                    f"{label} private world asset has a public runtime audience"
                )
        asset_ids.append(asset_id)
        assets.append(asset)
    _sorted_record_keys(asset_ids, label="ResolvedScenario.assets")
    if len(payload_paths) != len(set(payload_paths)):
        raise WorkloadScenarioError("resolved world/task asset payload files must be unique")
    if set(payload_paths) & license_paths:
        raise WorkloadScenarioError(
            "resolved asset payload files must be disjoint from license files"
        )
    return tuple(assets)


def _canonical_projected_assets(
    projected: list[object],
) -> tuple[dict[str, Any], ...]:
    """Validate projected assets with the dependency-free strict asset validator."""

    if not projected:
        return ()
    return _validated_assets(projected)


def _asset_has_audience(asset: dict[str, Any], role: str, workload_id: str) -> bool:
    return any(
        audience["role"] == role and audience["workload_id"] == workload_id
        for audience in asset["audiences"]
    )


def _require_world_asset(
    asset_map: dict[str, dict[str, Any]],
    asset_id: str,
    *,
    label: str,
    roles: frozenset[str],
) -> dict[str, Any]:
    asset = asset_map.get(asset_id)
    if (
        asset is None
        or asset["source_kind"] != "world"
        or type(asset["world"]) is not dict
        or asset["world"]["asset_role"] not in roles
    ):
        raise WorkloadScenarioError(f"{label} does not reference the required world asset role")
    return asset


def _validate_frame_authority(
    value: object,
    *,
    asset_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    label = "ResolvedScenario.frame_authority"
    frame = _exact_object(value, _FRAME_AUTHORITY_FIELDS, label=label)
    for field, expected in (
        ("geodetic_frame_id", "WGS84"),
        ("ecef_frame_id", "ECEF"),
        ("enu_frame_id", "ENU"),
        ("ned_frame_id", "NED"),
    ):
        if frame[field] != expected:
            raise WorkloadScenarioError(f"{label}.{field} is unsupported")
    origin = _validate_coordinate(frame["origin"], label=f"{label}.origin")
    origin_ecef = _exact_object(
        frame["origin_ecef"], _ECEF_FIELDS, label=f"{label}.origin_ecef"
    )
    for field in ("x_m", "y_m", "z_m"):
        _number(origin_ecef[field], label=f"{label}.origin_ecef.{field}")
    ecef_to_enu = _validate_rotation(
        frame["ecef_to_enu_rotation"], label=f"{label}.ecef_to_enu_rotation"
    )
    enu_to_ecef = _validate_rotation(
        frame["enu_to_ecef_rotation"], label=f"{label}.enu_to_ecef_rotation"
    )
    enu_to_ned = _validate_rotation(
        frame["enu_to_ned_rotation"], label=f"{label}.enu_to_ned_rotation"
    )
    ned_to_enu = _validate_rotation(
        frame["ned_to_enu_rotation"], label=f"{label}.ned_to_enu_rotation"
    )
    if not _matrices_close(ecef_to_enu, _matrix_transpose(enu_to_ecef)):
        raise WorkloadScenarioError(f"{label} ECEF/ENU rotations are not inverses")
    if not _matrices_close(enu_to_ned, _ENU_TO_NED, tolerance=_MATRIX_TOLERANCE):
        raise WorkloadScenarioError(f"{label}.enu_to_ned_rotation is not authoritative")
    if not _matrices_close(ned_to_enu, _ENU_TO_NED, tolerance=_MATRIX_TOLERANCE):
        raise WorkloadScenarioError(f"{label}.ned_to_enu_rotation is not authoritative")

    longitude = math.radians(float(origin["wgs84"]["longitude_deg"]))
    latitude = math.radians(float(origin["wgs84"]["latitude_deg"]))
    expected_ecef_to_enu = (
        (-math.sin(longitude), math.cos(longitude), 0.0),
        (
            -math.sin(latitude) * math.cos(longitude),
            -math.sin(latitude) * math.sin(longitude),
            math.cos(latitude),
        ),
        (
            math.cos(latitude) * math.cos(longitude),
            math.cos(latitude) * math.sin(longitude),
            math.sin(latitude),
        ),
    )
    if not _matrices_close(ecef_to_enu, expected_ecef_to_enu, tolerance=2e-12):
        raise WorkloadScenarioError(f"{label} rotation does not match its WGS84 origin")

    _validate_bounds(frame["spatial_extent"], label=f"{label}.spatial_extent")
    geoid_id = _string(
        frame["geoid_correction_asset_id"],
        label=f"{label}.geoid_correction_asset_id",
        identifier=True,
    )
    terrain_id = _string(
        frame["terrain_height_asset_id"],
        label=f"{label}.terrain_height_asset_id",
        identifier=True,
    )
    for field in ("geoid_interpolation", "terrain_interpolation"):
        if frame[field] not in {"nearest", "bilinear"}:
            raise WorkloadScenarioError(f"{label}.{field} is unsupported")
    for field in ("geoid_precision_m", "terrain_precision_m"):
        precision = _number(frame[field], label=f"{label}.{field}")
        if precision <= 0.0:
            raise WorkloadScenarioError(f"{label}.{field} must be positive")
    geoid_asset = _require_world_asset(
        asset_map,
        geoid_id,
        label=f"{label}.geoid_correction_asset_id",
        roles=frozenset({"geoid_model"}),
    )
    terrain_asset = _require_world_asset(
        asset_map,
        terrain_id,
        label=f"{label}.terrain_height_asset_id",
        roles=frozenset({"terrain_model"}),
    )
    for asset_label, asset in (
        ("geoid_correction_asset_id", geoid_asset),
        ("terrain_height_asset_id", terrain_asset),
    ):
        metadata = asset["world"]
        has_metre_tolerance = any(
            item["kind"] == "absolute_tolerance"
            and item["unit"] == "m"
            and item["absolute_tolerance"] is not None
            for item in metadata["precision"]
        )
        if (
            metadata["media_type"] != "application/json"
            or metadata["selector_fragment"] is not None
            or not has_metre_tolerance
        ):
            raise WorkloadScenarioError(
                f"{label}.{asset_label} is not a resolved scalar-grid asset"
            )
    if any(not _close(float(origin["enu"][field]), 0.0, absolute=1e-12) for field in ("east_m", "north_m", "up_m")):
        raise WorkloadScenarioError(f"{label}.origin must be the ENU zero point")
    if any(
        not _close(float(origin["ecef"][field]), float(origin_ecef[field]), absolute=1e-6)
        for field in ("x_m", "y_m", "z_m")
    ):
        raise WorkloadScenarioError(f"{label}.origin_ecef differs from origin.ecef")

    # Private parsed values are kept only during validation and never returned.
    frame_context = dict(frame)
    frame_context["_enu_to_ecef"] = enu_to_ecef
    _validate_coordinate_in_frame(origin, frame=frame_context, label=f"{label}.origin")
    return frame_context


def _validate_providers(value: object) -> dict[str, dict[str, Any]]:
    providers = _array(value, label="ResolvedScenario.providers")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(providers):
        label = f"ResolvedScenario.providers[{index}]"
        provider = _exact_object(item, _PROVIDER_FIELDS, label=label)
        provider_id = _string(
            provider["provider_id"], label=f"{label}.provider_id", identifier=True
        )
        _enum(provider["runtime_stage"], _RUNTIME_STAGES, label=f"{label}.runtime_stage")
        _string_list(provider["roles"], label=f"{label}.roles", allowed=_PROVIDER_ROLES)
        _string_list(provider["capability_ids"], label=f"{label}.capability_ids")
        keys.append(provider_id)
        result[provider_id] = provider
    _sorted_record_keys(keys, label="ResolvedScenario.providers")
    return result


def _require_provider_role(
    providers: dict[str, dict[str, Any]],
    provider_id: str,
    role: str,
    *,
    label: str,
) -> None:
    provider = providers.get(provider_id)
    if provider is None or role not in provider["roles"]:
        raise WorkloadScenarioError(f"{label} does not reference a provider with role {role}")


def _validate_layers(
    base_value: object,
    layers_value: object,
    *,
    asset_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    base_layers = _array(base_value, label="ResolvedScenario.base_layers")
    base_map: dict[str, dict[str, Any]] = {}
    base_keys: list[str] = []
    for index, item in enumerate(base_layers):
        label = f"ResolvedScenario.base_layers[{index}]"
        layer = _exact_object(item, _LAYER_FIELDS, label=label)
        layer_id = _string(layer["layer_id"], label=f"{label}.layer_id", identifier=True)
        kind = _enum(
            layer["kind"], frozenset({"imagery", "terrain"}), label=f"{label}.kind"
        )
        asset_id = _string(layer["asset_id"], label=f"{label}.asset_id", identifier=True)
        if layer["visibility"] != "public":
            raise WorkloadScenarioError(f"{label}.visibility must be public")
        _boolean(layer["default_visible"], label=f"{label}.default_visible")
        asset = _require_world_asset(
            asset_map,
            asset_id,
            label=f"{label}.asset_id",
            roles=frozenset({"imagery_tiles" if kind == "imagery" else "terrain_tiles"}),
        )
        if asset["classification"] != "public":
            raise WorkloadScenarioError(f"{label} does not reference a public asset")
        base_keys.append(layer_id)
        base_map[layer_id] = layer
    _sorted_record_keys(base_keys, label="ResolvedScenario.base_layers")

    layers = _array(layers_value, label="ResolvedScenario.layers")
    layer_map: dict[str, dict[str, Any]] = {}
    layer_keys: list[str] = []
    for index, item in enumerate(layers):
        label = f"ResolvedScenario.layers[{index}]"
        layer = _exact_object(item, _LAYER_FIELDS, label=label)
        layer_id = _string(layer["layer_id"], label=f"{label}.layer_id", identifier=True)
        _enum(
            layer["kind"],
            frozenset({"buildings", "roads", "regions", "entities", "weather", "missions", "osm_scene", "osm_mesh"}),
            label=f"{label}.kind",
        )
        asset_id = _string(layer["asset_id"], label=f"{label}.asset_id", identifier=True)
        if layer["visibility"] != "public":
            raise WorkloadScenarioError(f"{label}.visibility must be public")
        _boolean(layer["default_visible"], label=f"{label}.default_visible")
        asset = _require_world_asset(
            asset_map, asset_id, label=f"{label}.asset_id", roles=frozenset({"layer_tiles"})
        )
        if asset["classification"] != "public":
            raise WorkloadScenarioError(f"{label} does not reference a public asset")
        layer_keys.append(layer_id)
        layer_map[layer_id] = layer
    _sorted_record_keys(layer_keys, label="ResolvedScenario.layers")
    osm_layers = [layer for layer in layer_map.values() if layer["kind"] == "osm_scene"]
    if len(osm_layers) > 1:
        raise WorkloadScenarioError("scenario must declare at most one osm_scene layer")
    for layer in osm_layers:
        metadata = asset_map[layer["asset_id"]]["world"]
        if metadata["source_frame"] != "WGS84" or metadata["media_type"] != "application/json":
            raise WorkloadScenarioError("osm_scene requires a WGS84 application/json asset")
    mesh_layers = [layer for layer in layer_map.values() if layer["kind"] == "osm_mesh"]
    if len(mesh_layers) > 1:
        raise WorkloadScenarioError("scenario must declare at most one osm_mesh layer")
    for layer in mesh_layers:
        metadata = asset_map[layer["asset_id"]]["world"]
        if metadata["source_frame"] != "asset_local" or metadata["media_type"] != "application/json":
            raise WorkloadScenarioError("osm_mesh requires an asset_local application/json manifest")
        if len(osm_layers) != 1:
            raise WorkloadScenarioError("osm_mesh requires its source osm_scene layer")
    return base_map, layer_map


def _validate_entities(
    value: object,
    *,
    providers: dict[str, dict[str, Any]],
    asset_map: dict[str, dict[str, Any]],
    frame: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    entities = _array(value, label="ResolvedScenario.entities", minimum=1)
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    allowed_authorities = {
        ("uav", "dynamic"): "gazebo_physics",
        ("ugv", "dynamic"): "sumo_traffic",
        ("pedestrian", "dynamic"): "sumo_traffic",
        ("static_asset", "static"): "scenario_static",
    }
    for index, item in enumerate(entities):
        label = f"ResolvedScenario.entities[{index}]"
        entity = _exact_object(item, _ENTITY_FIELDS, label=label)
        entity_id = _string(entity["entity_id"], label=f"{label}.entity_id", identifier=True)
        kind = _enum(
            entity["kind"],
            frozenset({"uav", "ugv", "pedestrian", "static_asset"}),
            label=f"{label}.kind",
        )
        owner_kind = _enum(
            entity["owner_kind"], frozenset({"scenario", "provider"}), label=f"{label}.owner_kind"
        )
        owner_id = _string(entity["owner_id"], label=f"{label}.owner_id", identifier=True)
        source_provider_id = _nullable_identifier(
            entity["source_provider_id"], label=f"{label}.source_provider_id"
        )
        authority = _enum(
            entity["authority_kind"],
            frozenset({"gazebo_physics", "sumo_traffic", "scenario_static"}),
            label=f"{label}.authority_kind",
        )
        state = _enum(
            entity["state"], frozenset({"static", "dynamic"}), label=f"{label}.state"
        )
        if allowed_authorities.get((kind, state)) != authority:
            raise WorkloadScenarioError(f"{label} kind/state/authority combination is invalid")
        if state == "static":
            if (
                owner_kind != "scenario"
                or owner_id != "scenario.compiler"
                or source_provider_id is not None
            ):
                raise WorkloadScenarioError(f"{label} static ownership is invalid")
        else:
            if (
                owner_kind != "provider"
                or source_provider_id != owner_id
                or owner_id not in providers
            ):
                raise WorkloadScenarioError(f"{label} dynamic ownership is invalid")
            _require_provider_role(
                providers,
                owner_id,
                "motion" if authority == "gazebo_physics" else "traffic",
                label=label,
            )
        model_asset_id = _string(
            entity["model_asset_id"], label=f"{label}.model_asset_id", identifier=True
        )
        allowed_roles = (
            frozenset({"entity_model", "building_render"})
            if state == "static"
            else frozenset({"entity_model"})
        )
        model_asset = _require_world_asset(
            asset_map, model_asset_id, label=f"{label}.model_asset_id", roles=allowed_roles
        )
        if state == "dynamic" and not _asset_has_audience(
            model_asset, "provider", owner_id
        ):
            raise WorkloadScenarioError(f"{label} model asset does not authorize its owner")
        pose = _validate_pose(entity["initial_pose"], label=f"{label}.initial_pose")
        _validate_pose_in_frame(pose, frame=frame, label=f"{label}.initial_pose")
        _boolean(
            entity["selected_launch_override"],
            label=f"{label}.selected_launch_override",
        )
        keys.append(entity_id)
        result[entity_id] = entity
    _sorted_record_keys(keys, label="ResolvedScenario.entities")
    return result


def _polygon_area(coordinates: list[dict[str, Any]]) -> float:
    points = [
        (float(coordinate["enu"]["east_m"]), float(coordinate["enu"]["north_m"]))
        for coordinate in coordinates
    ]
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
    ) * 0.5


def _validate_coordinate_array(
    value: object,
    *,
    label: str,
    minimum: int,
    frame: dict[str, Any],
) -> list[dict[str, Any]]:
    values = _array(value, label=label, minimum=minimum)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        coordinate = _validate_coordinate(item, label=f"{label}[{index}]")
        _validate_coordinate_in_frame(
            coordinate, frame=frame, label=f"{label}[{index}]"
        )
        result.append(coordinate)
    return result


def _validate_buildings(
    value: object,
    *,
    asset_map: dict[str, dict[str, Any]],
    entities: dict[str, dict[str, Any]],
    frame: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    buildings = _array(value, label="ResolvedScenario.buildings")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    entity_ids: list[str] = []
    for index, item in enumerate(buildings):
        label = f"ResolvedScenario.buildings[{index}]"
        building = _exact_object(item, _BUILDING_FIELDS, label=label)
        building_id = _string(
            building["building_id"], label=f"{label}.building_id", identifier=True
        )
        entity_id = _string(
            building["entity_id"], label=f"{label}.entity_id", identifier=True
        )
        asset_references = (
            ("source_asset_id", "building_source"),
            ("render_asset_id", "building_render"),
            ("collision_asset_id", "building_collision"),
        )
        referenced_ids: list[str] = []
        for field, role in asset_references:
            asset_id = _string(building[field], label=f"{label}.{field}", identifier=True)
            _require_world_asset(
                asset_map, asset_id, label=f"{label}.{field}", roles=frozenset({role})
            )
            referenced_ids.append(asset_id)
        if len(referenced_ids) != len(set(referenced_ids)):
            raise WorkloadScenarioError(f"{label} asset references must be distinct")
        anchor_east = _number(building["anchor_east_m"], label=f"{label}.anchor_east_m")
        anchor_north = _number(building["anchor_north_m"], label=f"{label}.anchor_north_m")
        base = _validate_coordinate_array(
            building["base_vertices"], label=f"{label}.base_vertices", minimum=3, frame=frame
        )
        top = _validate_coordinate_array(
            building["top_vertices"], label=f"{label}.top_vertices", minimum=3, frame=frame
        )
        if len(base) != len(top):
            raise WorkloadScenarioError(f"{label} base and top vertex counts differ")
        horizontal = [
            (float(point["enu"]["east_m"]), float(point["enu"]["north_m"]))
            for point in base
        ]
        if len(horizontal) != len(set(horizontal)) or _polygon_area(base) <= 0.0:
            raise WorkloadScenarioError(f"{label} footprint is degenerate")
        for base_point, top_point in zip(base, top):
            if not (
                _close(
                    float(base_point["enu"]["east_m"]),
                    float(top_point["enu"]["east_m"]),
                )
                and _close(
                    float(base_point["enu"]["north_m"]),
                    float(top_point["enu"]["north_m"]),
                )
                and float(top_point["enu"]["up_m"])
                > float(base_point["enu"]["up_m"])
            ):
                raise WorkloadScenarioError(f"{label} extrusion vertices are inconsistent")
        expected_anchor = min(horizontal)
        if not (
            _close(anchor_east, expected_anchor[0])
            and _close(anchor_north, expected_anchor[1])
        ):
            raise WorkloadScenarioError(f"{label} anchor is not canonical")
        entity = entities.get(entity_id)
        if (
            entity is None
            or entity["kind"] != "static_asset"
            or entity["state"] != "static"
            or entity["owner_kind"] != "scenario"
            or entity["authority_kind"] != "scenario_static"
            or entity["model_asset_id"] != building["render_asset_id"]
        ):
            raise WorkloadScenarioError(f"{label}.entity_id is not its static render entity")
        keys.append(building_id)
        entity_ids.append(entity_id)
        result[building_id] = building
    _sorted_record_keys(keys, label="ResolvedScenario.buildings")
    if len(entity_ids) != len(set(entity_ids)):
        raise WorkloadScenarioError("ResolvedScenario buildings must use unique entities")
    return result


def _validate_roads(
    value: object, *, frame: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    roads = _array(value, label="ResolvedScenario.roads")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(roads):
        label = f"ResolvedScenario.roads[{index}]"
        road = _exact_object(item, _ROAD_FIELDS, label=label)
        road_id = _string(road["road_id"], label=f"{label}.road_id", identifier=True)
        _enum(road["kind"], _ROAD_KINDS, label=f"{label}.kind")
        _number(road["width_m"], label=f"{label}.width_m", minimum=0.0, exclusive_minimum=True)
        points = _validate_coordinate_array(
            road["terrain_points"], label=f"{label}.terrain_points", minimum=2, frame=frame
        )
        if any(
            not _close(float(point["agl_m"]), 0.0, absolute=1e-6)
            for point in points
        ):
            raise WorkloadScenarioError(f"{label}.terrain_points are not terrain-bound")
        distinct = {
            (float(point["enu"]["east_m"]), float(point["enu"]["north_m"]))
            for point in points
        }
        if len(distinct) < 2:
            raise WorkloadScenarioError(f"{label}.terrain_points are degenerate")
        keys.append(road_id)
        result[road_id] = road
    _sorted_record_keys(keys, label="ResolvedScenario.roads")
    return result


def _validate_regions(
    value: object, *, frame: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    regions = _array(value, label="ResolvedScenario.regions")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(regions):
        label = f"ResolvedScenario.regions[{index}]"
        region = _exact_object(item, _REGION_FIELDS, label=label)
        region_id = _string(
            region["region_id"], label=f"{label}.region_id", identifier=True
        )
        kind = _enum(region["kind"], _REGION_KINDS, label=f"{label}.kind")
        attenuation = region["communications_shadow_attenuation_db"]
        if kind == "communications_shadow":
            _number(
                attenuation,
                label=f"{label}.communications_shadow_attenuation_db",
                minimum=0.0,
                exclusive_minimum=True,
            )
        elif attenuation is not None:
            raise WorkloadScenarioError(f"{label} attenuation is invalid for region kind")
        anchor_east = _number(region["anchor_east_m"], label=f"{label}.anchor_east_m")
        anchor_north = _number(region["anchor_north_m"], label=f"{label}.anchor_north_m")
        lower = _validate_coordinate_array(
            region["lower_vertices"], label=f"{label}.lower_vertices", minimum=3, frame=frame
        )
        upper = _validate_coordinate_array(
            region["upper_vertices"], label=f"{label}.upper_vertices", minimum=3, frame=frame
        )
        if len(lower) != len(upper):
            raise WorkloadScenarioError(f"{label} lower and upper vertex counts differ")
        horizontal = [
            (float(point["enu"]["east_m"]), float(point["enu"]["north_m"]))
            for point in lower
        ]
        if len(horizontal) != len(set(horizontal)) or _polygon_area(lower) <= 0.0:
            raise WorkloadScenarioError(f"{label} footprint is degenerate")
        for lower_point, upper_point in zip(lower, upper):
            if not (
                _close(
                    float(lower_point["enu"]["east_m"]),
                    float(upper_point["enu"]["east_m"]),
                )
                and _close(
                    float(lower_point["enu"]["north_m"]),
                    float(upper_point["enu"]["north_m"]),
                )
                and float(upper_point["enu"]["up_m"])
                > float(lower_point["enu"]["up_m"])
            ):
                raise WorkloadScenarioError(f"{label} volume vertices are inconsistent")
        expected_anchor = min(horizontal)
        if not (
            _close(anchor_east, expected_anchor[0])
            and _close(anchor_north, expected_anchor[1])
        ):
            raise WorkloadScenarioError(f"{label} anchor is not canonical")
        keys.append(region_id)
        result[region_id] = region
    _sorted_record_keys(keys, label="ResolvedScenario.regions")
    return result


def _validate_launch_sites(
    value: object,
    *,
    selected_launch_site_id: str | None,
    entities: dict[str, dict[str, Any]],
    frame: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    launch_sites = _array(value, label="ResolvedScenario.launch_sites")
    if selected_launch_site_id is None:
        if launch_sites or any(entity["selected_launch_override"] for entity in entities.values()):
            raise WorkloadScenarioError("no-launch scenario cannot declare a launch override")
        return {}
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    selected: list[str] = []
    for index, item in enumerate(launch_sites):
        label = f"ResolvedScenario.launch_sites[{index}]"
        launch = _exact_object(item, _LAUNCH_SITE_FIELDS, label=label)
        launch_id = _string(
            launch["launch_site_id"], label=f"{label}.launch_site_id", identifier=True
        )
        primary_id = _string(
            launch["primary_uav_entity_id"],
            label=f"{label}.primary_uav_entity_id",
            identifier=True,
        )
        allowed = _string_list(
            launch["allowed_uav_entity_ids"], label=f"{label}.allowed_uav_entity_ids"
        )
        if primary_id not in allowed:
            raise WorkloadScenarioError(f"{label} primary UAV is not allowed")
        for entity_id in allowed:
            entity = entities.get(entity_id)
            if (
                entity is None
                or entity["kind"] != "uav"
                or entity["state"] != "dynamic"
                or entity["owner_kind"] != "provider"
            ):
                raise WorkloadScenarioError(f"{label} references a non-dynamic UAV")
        pose = _validate_pose(launch["pose"], label=f"{label}.pose")
        _validate_pose_in_frame(pose, frame=frame, label=f"{label}.pose")
        _number(
            launch["pad_radius_m"],
            label=f"{label}.pad_radius_m",
            minimum=0.0,
            exclusive_minimum=True,
        )
        if _boolean(launch["selected"], label=f"{label}.selected"):
            selected.append(launch_id)
        keys.append(launch_id)
        result[launch_id] = launch
    _sorted_record_keys(keys, label="ResolvedScenario.launch_sites")
    if selected != [selected_launch_site_id]:
        raise WorkloadScenarioError("ResolvedScenario launch selection is inconsistent")
    selected_launch = result.get(selected_launch_site_id)
    if selected_launch is None:
        raise WorkloadScenarioError("ResolvedScenario selected launch site is absent")
    selected_primary = selected_launch["primary_uav_entity_id"]
    override_ids = {
        entity_id
        for entity_id, entity in entities.items()
        if entity["selected_launch_override"]
    }
    if override_ids != {selected_primary}:
        raise WorkloadScenarioError(
            "ResolvedScenario selected launch override is not exact"
        )
    if entities[selected_primary]["initial_pose"] != selected_launch["pose"]:
        raise WorkloadScenarioError(
            "ResolvedScenario selected UAV pose differs from selected launch"
        )
    return result


def _validate_sensors(
    value: object,
    *,
    providers: dict[str, dict[str, Any]],
    entities: dict[str, dict[str, Any]],
    frame: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    sensors = _array(value, label="ResolvedScenario.sensors")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(sensors):
        label = f"ResolvedScenario.sensors[{index}]"
        sensor = _exact_object(item, _SENSOR_FIELDS, label=label)
        sensor_id = _string(sensor["sensor_id"], label=f"{label}.sensor_id", identifier=True)
        provider_id = _string(
            sensor["provider_id"], label=f"{label}.provider_id", identifier=True
        )
        _require_provider_role(providers, provider_id, "sensor", label=label)
        parent_id = _string(
            sensor["parent_entity_id"], label=f"{label}.parent_entity_id", identifier=True
        )
        if parent_id not in entities:
            raise WorkloadScenarioError(f"{label}.parent_entity_id is absent")
        if sensor["kind"] != "camera":
            raise WorkloadScenarioError(f"{label}.kind is unsupported")
        pose = _validate_pose(sensor["initial_pose"], label=f"{label}.initial_pose")
        _validate_pose_in_frame(pose, frame=frame, label=f"{label}.initial_pose")
        for field in ("horizontal_fov_deg", "vertical_fov_deg"):
            _number(
                sensor[field],
                label=f"{label}.{field}",
                minimum=0.0,
                maximum=180.0,
                exclusive_minimum=True,
                exclusive_maximum=True,
            )
        _integer(
            sensor["resolution_width_px"], label=f"{label}.resolution_width_px", minimum=1
        )
        _integer(
            sensor["resolution_height_px"], label=f"{label}.resolution_height_px", minimum=1
        )
        keys.append(sensor_id)
        result[sensor_id] = sensor
    _sorted_record_keys(keys, label="ResolvedScenario.sensors")
    return result


def _validate_semantic_targets(
    value: object,
    *,
    entities: dict[str, dict[str, Any]],
    sensors: dict[str, dict[str, Any]],
    frame: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    targets = _array(value, label="ResolvedScenario.semantic_targets")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(targets):
        label = f"ResolvedScenario.semantic_targets[{index}]"
        target = _exact_object(item, _TARGET_FIELDS, label=label)
        target_id = _string(target["target_id"], label=f"{label}.target_id", identifier=True)
        parent_id = _string(
            target["parent_entity_id"], label=f"{label}.parent_entity_id", identifier=True
        )
        parent = entities.get(parent_id)
        if (
            parent is None
            or parent["kind"] != "static_asset"
            or parent["state"] != "static"
            or parent["owner_kind"] != "scenario"
            or parent["authority_kind"] != "scenario_static"
        ):
            raise WorkloadScenarioError(f"{label}.parent_entity_id is not scenario-static")
        sensor_id = _string(
            target["required_sensor_id"], label=f"{label}.required_sensor_id", identifier=True
        )
        sensor = sensors.get(sensor_id)
        if sensor is None:
            raise WorkloadScenarioError(f"{label}.required_sensor_id is absent")
        pose = _validate_pose(target["pose"], label=f"{label}.pose")
        _validate_pose_in_frame(pose, frame=frame, label=f"{label}.pose")
        geometry = _exact_object(
            target["geometry"], _TARGET_GEOMETRY_FIELDS, label=f"{label}.geometry"
        )
        if geometry["shape"] != "box":
            raise WorkloadScenarioError(f"{label}.geometry.shape is unsupported")
        for field in ("size_x_m", "size_y_m", "size_z_m"):
            _number(
                geometry[field],
                label=f"{label}.geometry.{field}",
                minimum=0.0,
                exclusive_minimum=True,
            )
        surface_normal = _exact_object(
            target["surface_normal_target"],
            _TARGET_SURFACE_NORMAL_FIELDS,
            label=f"{label}.surface_normal_target",
        )
        normal_components = tuple(
            _number(
                surface_normal[field],
                label=f"{label}.surface_normal_target.{field}",
            )
            for field in ("x", "y", "z")
        )
        if (
            sum(
                abs(abs(component) - 1.0) <= 1e-12
                for component in normal_components
            )
            != 1
            or sum(abs(component) <= 1e-12 for component in normal_components) != 2
        ):
            raise WorkloadScenarioError(
                f"{label}.surface_normal_target must select one signed box axis"
            )
        minimum_distance = _number(
            target["min_distance_m"], label=f"{label}.min_distance_m", minimum=0.0
        )
        maximum_distance = _number(
            target["max_distance_m"],
            label=f"{label}.max_distance_m",
            minimum=0.0,
            exclusive_minimum=True,
        )
        if minimum_distance > maximum_distance:
            raise WorkloadScenarioError(f"{label} distance range is invalid")
        maximum_angle = _number(
            target["max_view_angle_deg"],
            label=f"{label}.max_view_angle_deg",
            minimum=0.0,
            maximum=180.0,
            exclusive_minimum=True,
            exclusive_maximum=True,
        )
        margin = _number(
            target["fov_margin_deg"], label=f"{label}.fov_margin_deg", minimum=0.0
        )
        _number(
            target["dwell_time_s"],
            label=f"{label}.dwell_time_s",
            minimum=0.0,
            exclusive_minimum=True,
        )
        evidence = _string_list(
            target["required_evidence"],
            label=f"{label}.required_evidence",
            allowed=_EVIDENCE_KINDS,
        )
        if not evidence:
            raise WorkloadScenarioError(f"{label}.required_evidence must be non-empty")
        half_fov = min(
            float(sensor["horizontal_fov_deg"]), float(sensor["vertical_fov_deg"])
        ) * 0.5
        if maximum_angle + margin >= half_fov:
            raise WorkloadScenarioError(f"{label} does not fit inside required sensor FOV")
        keys.append(target_id)
        result[target_id] = target
    _sorted_record_keys(keys, label="ResolvedScenario.semantic_targets")
    return result


def _validate_weather(value: object) -> dict[str, dict[str, Any]]:
    samples = _array(value, label="ResolvedScenario.weather", minimum=1)
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(samples):
        label = f"ResolvedScenario.weather[{index}]"
        weather = _exact_object(item, _WEATHER_FIELDS, label=label)
        sample_id = _string(
            weather["sample_id"], label=f"{label}.sample_id", identifier=True
        )
        if weather["mode"] != "deterministic_constant":
            raise WorkloadScenarioError(f"{label}.mode is unsupported")
        wind = _exact_object(weather["wind"], _WIND_FIELDS, label=f"{label}.wind")
        for field in ("east_mps", "north_mps", "up_mps"):
            _number(wind[field], label=f"{label}.wind.{field}")
        _number(
            weather["visibility_m"],
            label=f"{label}.visibility_m",
            minimum=0.0,
            exclusive_minimum=True,
        )
        precipitation = _enum(
            weather["precipitation"], _PRECIPITATION_KINDS, label=f"{label}.precipitation"
        )
        rate = _number(
            weather["precipitation_rate_mm_per_h"],
            label=f"{label}.precipitation_rate_mm_per_h",
            minimum=0.0,
        )
        _number(weather["temperature_c"], label=f"{label}.temperature_c")
        _number(
            weather["pressure_pa"],
            label=f"{label}.pressure_pa",
            minimum=0.0,
            exclusive_minimum=True,
        )
        if (precipitation == "none") != (rate == 0.0):
            raise WorkloadScenarioError(f"{label} precipitation and rate disagree")
        keys.append(sample_id)
        result[sample_id] = weather
    _sorted_record_keys(keys, label="ResolvedScenario.weather")
    return result


def _validate_engine_bindings(
    value: object,
    *,
    providers: dict[str, dict[str, Any]],
    asset_map: dict[str, dict[str, Any]],
    entities: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    bindings = _array(value, label="ResolvedScenario.engine_frame_bindings")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(bindings):
        label = f"ResolvedScenario.engine_frame_bindings[{index}]"
        binding = _exact_object(item, _ENGINE_BINDING_FIELDS, label=label)
        binding_id = _string(
            binding["binding_id"], label=f"{label}.binding_id", identifier=True
        )
        provider_id = _string(
            binding["provider_id"], label=f"{label}.provider_id", identifier=True
        )
        engine = _enum(
            binding["engine"], frozenset({"gazebo", "sumo"}), label=f"{label}.engine"
        )
        source_frame = _frame_id(
            binding["source_frame_id"], label=f"{label}.source_frame_id"
        )
        target_frame = _frame_id(
            binding["target_frame_id"], label=f"{label}.target_frame_id"
        )
        if source_frame == target_frame:
            raise WorkloadScenarioError(f"{label} source and target frames must differ")
        translation = _exact_object(
            binding["translation_m"], _ECEF_FIELDS, label=f"{label}.translation_m"
        )
        for field in ("x_m", "y_m", "z_m"):
            _number(translation[field], label=f"{label}.translation_m.{field}")
        rotation_matrix = _validate_rotation(
            binding["rotation_matrix"], label=f"{label}.rotation_matrix"
        )
        quaternion = _validate_quaternion(
            binding["rotation_quaternion"], label=f"{label}.rotation_quaternion"
        )
        if not _matrices_close(
            rotation_matrix, _rotation_from_quaternion(quaternion), tolerance=2e-9
        ):
            raise WorkloadScenarioError(f"{label} matrix and quaternion disagree")
        scene_asset_id = _nullable_identifier(
            binding["scene_asset_id"], label=f"{label}.scene_asset_id"
        )
        if engine == "gazebo":
            _require_provider_role(providers, provider_id, "motion", label=label)
            if scene_asset_id is None:
                raise WorkloadScenarioError(f"{label} Gazebo binding requires scene_asset_id")
            scene_asset = _require_world_asset(
                asset_map,
                scene_asset_id,
                label=f"{label}.scene_asset_id",
                roles=frozenset({"other"}),
            )
            if not _asset_has_audience(scene_asset, "provider", provider_id):
                raise WorkloadScenarioError(f"{label} scene asset does not authorize provider")
        else:
            _require_provider_role(providers, provider_id, "traffic", label=label)
            if scene_asset_id is not None:
                raise WorkloadScenarioError(f"{label} SUMO binding cannot name a scene asset")
        keys.append(binding_id)
        result[binding_id] = binding
    _sorted_record_keys(keys, label="ResolvedScenario.engine_frame_bindings")
    if any(entity["authority_kind"] == "gazebo_physics" for entity in entities.values()) and not any(
        binding["engine"] == "gazebo" for binding in result.values()
    ):
        raise WorkloadScenarioError(
            "ResolvedScenario Gazebo entities lack an engine frame binding"
        )
    return result


def _validate_sumo(
    value: object,
    *,
    providers: dict[str, dict[str, Any]],
    asset_map: dict[str, dict[str, Any]],
    entities: dict[str, dict[str, Any]],
    engine_bindings: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    sumo_bindings = [
        binding for binding in engine_bindings.values() if binding["engine"] == "sumo"
    ]
    sumo_entity_ids = {
        entity_id
        for entity_id, entity in entities.items()
        if entity["authority_kind"] == "sumo_traffic"
    }
    if value is None:
        if sumo_bindings or sumo_entity_ids:
            raise WorkloadScenarioError(
                "ResolvedScenario SUMO authority exists without SUMO configuration"
            )
        return None
    label = "ResolvedScenario.sumo"
    sumo = _exact_object(value, _SUMO_FIELDS, label=label)
    provider_id = _string(sumo["provider_id"], label=f"{label}.provider_id", identifier=True)
    _require_provider_role(providers, provider_id, "traffic", label=label)
    asset_fields = (
        ("config_asset_id", "sumo_config"),
        ("network_asset_id", "sumo_network"),
        ("routes_asset_id", "sumo_routes"),
        ("additional_asset_id", "sumo_additional"),
    )
    sumo_asset_ids: list[str] = []
    for field, role in asset_fields:
        asset_id = _string(sumo[field], label=f"{label}.{field}", identifier=True)
        asset = _require_world_asset(
            asset_map, asset_id, label=f"{label}.{field}", roles=frozenset({role})
        )
        if not _asset_has_audience(asset, "provider", provider_id):
            raise WorkloadScenarioError(f"{label}.{field} does not authorize provider")
        sumo_asset_ids.append(asset_id)
    if len(sumo_asset_ids) != len(set(sumo_asset_ids)):
        raise WorkloadScenarioError(f"{label} asset references must be distinct")
    frame_binding_id = _string(
        sumo["frame_binding_id"], label=f"{label}.frame_binding_id", identifier=True
    )
    frame_binding = engine_bindings.get(frame_binding_id)
    if (
        frame_binding is None
        or frame_binding["engine"] != "sumo"
        or frame_binding["provider_id"] != provider_id
        or frame_binding["source_frame_id"] != "sumo_net"
        or frame_binding["target_frame_id"] != "ENU"
    ):
        raise WorkloadScenarioError(f"{label}.frame_binding_id is not SUMO-to-ENU authority")
    canonical_bindings = [
        binding
        for binding in sumo_bindings
        if binding["source_frame_id"] == "sumo_net"
        and binding["target_frame_id"] == "ENU"
    ]
    if len(canonical_bindings) != 1:
        raise WorkloadScenarioError(f"{label} requires exactly one SUMO-to-ENU binding")
    if any(
        binding is not canonical_bindings[0]
        and (
            binding["source_frame_id"] == "sumo_net"
            or binding["target_frame_id"] == "ENU"
        )
        for binding in sumo_bindings
    ):
        raise WorkloadScenarioError(f"{label} has ambiguous SUMO frame authority")

    object_bindings = _array(sumo["object_bindings"], label=f"{label}.object_bindings")
    object_keys: list[str] = []
    bound_entity_ids: list[str] = []
    for index, item in enumerate(object_bindings):
        item_label = f"{label}.object_bindings[{index}]"
        binding = _exact_object(item, _SUMO_OBJECT_FIELDS, label=item_label)
        object_id = _string(binding["sumo_object_id"], label=f"{item_label}.sumo_object_id")
        if object_id.strip() != object_id or _SUMO_OBJECT_ID.fullmatch(object_id) is None:
            raise WorkloadScenarioError(f"{item_label}.sumo_object_id is invalid")
        entity_id = _string(
            binding["entity_id"], label=f"{item_label}.entity_id", identifier=True
        )
        kind = _enum(
            binding["kind"], frozenset({"vehicle", "person"}), label=f"{item_label}.kind"
        )
        entity = entities.get(entity_id)
        expected_kind = "ugv" if kind == "vehicle" else "pedestrian"
        if (
            entity is None
            or entity["kind"] != expected_kind
            or entity["state"] != "dynamic"
            or entity["authority_kind"] != "sumo_traffic"
            or entity["owner_id"] != provider_id
        ):
            raise WorkloadScenarioError(f"{item_label}.entity_id is invalid for SUMO kind")
        object_keys.append(object_id)
        bound_entity_ids.append(entity_id)
    _sorted_record_keys(object_keys, label=f"{label}.object_bindings")
    if len(bound_entity_ids) != len(set(bound_entity_ids)):
        raise WorkloadScenarioError(f"{label} object entities must be unique")
    if set(bound_entity_ids) != sumo_entity_ids:
        raise WorkloadScenarioError(f"{label} object bindings do not close over SUMO entities")
    if any(entities[entity_id]["owner_id"] != provider_id for entity_id in sumo_entity_ids):
        raise WorkloadScenarioError(f"{label} does not own every SUMO entity")
    return sumo


def _validate_network(
    value: object,
    *,
    providers: dict[str, dict[str, Any]],
    entities: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if value is None:
        return None
    label = "ResolvedScenario.network"
    network = _exact_object(value, _NETWORK_FIELDS, label=label)
    provider_id = _string(
        network["provider_id"], label=f"{label}.provider_id", identifier=True
    )
    _require_provider_role(providers, provider_id, "wireless_network", label=label)
    if providers[provider_id]["runtime_stage"] != "network":
        raise WorkloadScenarioError("scenario.network.provider_id must name a network-stage Provider")

    profiles = _array(network["radio_profiles"], label=f"{label}.radio_profiles", minimum=1)
    profile_map: dict[str, dict[str, Any]] = {}
    profile_keys: list[str] = []
    for index, item in enumerate(profiles):
        item_label = f"{label}.radio_profiles[{index}]"
        profile = _exact_object(item, _RADIO_PROFILE_FIELDS, label=item_label)
        profile_id = _string(
            profile["radio_profile_id"],
            label=f"{item_label}.radio_profile_id",
            identifier=True,
        )
        if _string(
            profile["provider_id"], label=f"{item_label}.provider_id", identifier=True
        ) != provider_id:
            raise WorkloadScenarioError(f"{item_label}.provider_id differs from network")
        _enum(
            profile["wifi_standard"],
            frozenset({"802.11n", "802.11ac", "802.11ax"}),
            label=f"{item_label}.wifi_standard",
        )
        for field in ("frequency_ghz", "channel_width_mhz"):
            _number(
                profile[field],
                label=f"{item_label}.{field}",
                minimum=0.0,
                exclusive_minimum=True,
            )
        for field in ("tx_power_dbm", "rx_sensitivity_dbm"):
            _number(profile[field], label=f"{item_label}.{field}")
        profile_keys.append(profile_id)
        profile_map[profile_id] = profile
    _sorted_record_keys(profile_keys, label=f"{label}.radio_profiles")

    nodes = _array(network["node_bindings"], label=f"{label}.node_bindings", minimum=1)
    node_map: dict[str, dict[str, Any]] = {}
    node_keys: list[str] = []
    endpoint_ids: list[str] = []
    entity_ids: list[str] = []
    used_profile_ids: set[str] = set()
    for index, item in enumerate(nodes):
        item_label = f"{label}.node_bindings[{index}]"
        node = _exact_object(item, _NETWORK_NODE_FIELDS, label=item_label)
        node_id = _string(node["node_id"], label=f"{item_label}.node_id", identifier=True)
        entity_id = _string(
            node["entity_id"], label=f"{item_label}.entity_id", identifier=True
        )
        endpoint_id = _string(
            node["endpoint_id"], label=f"{item_label}.endpoint_id", identifier=True
        )
        profile_id = _string(
            node["radio_profile_id"],
            label=f"{item_label}.radio_profile_id",
            identifier=True,
        )
        if entity_id not in entities or profile_id not in profile_map:
            raise WorkloadScenarioError(f"{item_label} references an absent entity or profile")
        node_keys.append(node_id)
        endpoint_ids.append(endpoint_id)
        entity_ids.append(entity_id)
        used_profile_ids.add(profile_id)
        node_map[node_id] = node
    _sorted_record_keys(node_keys, label=f"{label}.node_bindings")
    if len(endpoint_ids) != len(set(endpoint_ids)):
        raise WorkloadScenarioError(f"{label} node endpoints must be unique")
    if len(entity_ids) != len(set(entity_ids)):
        raise WorkloadScenarioError(f"{label} node entities must be unique")
    if used_profile_ids != set(profile_map):
        raise WorkloadScenarioError(f"{label} radio profiles are not closed over nodes")

    links = _array(network["links"], label=f"{label}.links")
    link_keys: list[str] = []
    for index, item in enumerate(links):
        item_label = f"{label}.links[{index}]"
        link = _exact_object(item, _NETWORK_LINK_FIELDS, label=item_label)
        link_id = _string(link["link_id"], label=f"{item_label}.link_id", identifier=True)
        source = _string(
            link["source_node_id"], label=f"{item_label}.source_node_id", identifier=True
        )
        destination = _string(
            link["destination_node_id"],
            label=f"{item_label}.destination_node_id",
            identifier=True,
        )
        if source == destination or source not in node_map or destination not in node_map:
            raise WorkloadScenarioError(f"{item_label} has invalid endpoint nodes")
        _integer(
            link["data_rate_bps"], label=f"{item_label}.data_rate_bps", minimum=1
        )
        _integer(
            link["propagation_delay_ns"],
            label=f"{item_label}.propagation_delay_ns",
            minimum=0,
        )
        link_keys.append(link_id)
    _sorted_record_keys(link_keys, label=f"{label}.links")
    return network


def _validate_expected_public_assets(value: object) -> dict[str, dict[str, Any]]:
    assets = _array(value, label="ResolvedScenario.expected_public_assets")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(assets):
        label = f"ResolvedScenario.expected_public_assets[{index}]"
        asset = _exact_object(item, _EXPECTED_PUBLIC_ASSET_FIELDS, label=label)
        asset_id = _string(asset["asset_id"], label=f"{label}.asset_id", identifier=True)
        _enum(asset["kind"], _EXPECTED_PUBLIC_ASSET_KINDS, label=f"{label}.kind")
        _string(
            asset["producer_requirement_id"],
            label=f"{label}.producer_requirement_id",
            identifier=True,
        )
        _media_type(asset["media_type"], label=f"{label}.media_type")
        _validate_measurement_metadata(asset["units"], asset["precision"], label=label)
        keys.append(asset_id)
        result[asset_id] = asset
    _sorted_record_keys(keys, label="ResolvedScenario.expected_public_assets")
    return result


def _validate_missions(
    value: object,
    *,
    selected_launch_site_id: str,
    launch_sites: dict[str, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
    expected_assets: dict[str, dict[str, Any]],
    source_assets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requirements = _array(value, label="ResolvedScenario.mission_requirements")
    result: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for index, item in enumerate(requirements):
        label = f"ResolvedScenario.mission_requirements[{index}]"
        requirement = _exact_object(item, _MISSION_FIELDS, label=label)
        requirement_id = _string(
            requirement["requirement_id"],
            label=f"{label}.requirement_id",
            identifier=True,
        )
        kind = _enum(requirement["kind"], _MISSION_KINDS, label=f"{label}.kind")
        dependencies = _string_list(
            requirement["dependencies"], label=f"{label}.dependencies", allow_empty=True
        )
        if requirement_id in dependencies:
            raise WorkloadScenarioError(f"{label} depends on itself")
        launch_id = _nullable_identifier(
            requirement["launch_site_id"], label=f"{label}.launch_site_id"
        )
        target_id = _nullable_identifier(
            requirement["target_id"], label=f"{label}.target_id"
        )
        public_asset_id = _nullable_identifier(
            requirement["expected_public_asset_id"],
            label=f"{label}.expected_public_asset_id",
        )
        if kind in {"takeoff", "return_to_launch", "land"}:
            if (
                launch_id != selected_launch_site_id
                or launch_id not in launch_sites
                or target_id is not None
                or public_asset_id is not None
            ):
                raise WorkloadScenarioError(f"{label} launch mission fields are invalid")
        elif kind == "observation":
            if launch_id is not None or target_id not in targets or public_asset_id is not None:
                raise WorkloadScenarioError(f"{label} observation mission fields are invalid")
        else:
            if launch_id is not None or target_id is not None or public_asset_id not in expected_assets:
                raise WorkloadScenarioError(f"{label} upload mission fields are invalid")
        keys.append(requirement_id)
        result[requirement_id] = requirement
    _sorted_record_keys(keys, label="ResolvedScenario.mission_requirements")

    for requirement_id, requirement in result.items():
        if any(dependency not in result for dependency in requirement["dependencies"]):
            raise WorkloadScenarioError(
                f"ResolvedScenario mission {requirement_id} has an absent dependency"
            )
    dependency_counts = {
        requirement_id: len(requirement["dependencies"])
        for requirement_id, requirement in result.items()
    }
    dependents: dict[str, list[str]] = {requirement_id: [] for requirement_id in result}
    for requirement_id, requirement in result.items():
        for dependency in requirement["dependencies"]:
            dependents[dependency].append(requirement_id)
    ready = [
        requirement_id
        for requirement_id, count in dependency_counts.items()
        if count == 0
    ]
    visited_count = 0
    while ready:
        requirement_id = ready.pop()
        visited_count += 1
        for dependent in dependents[requirement_id]:
            dependency_counts[dependent] -= 1
            if dependency_counts[dependent] == 0:
                ready.append(dependent)
    if visited_count != len(result):
        raise WorkloadScenarioError(
            "ResolvedScenario mission requirements contain a cycle"
        )

    if set(expected_assets) & set(source_assets):
        raise WorkloadScenarioError(
            "ResolvedScenario expected public assets reuse source asset IDs"
        )
    for asset_id, asset in expected_assets.items():
        producer = result.get(asset["producer_requirement_id"])
        if (
            producer is None
            or producer["kind"] != "upload_or_buffer"
            or producer["expected_public_asset_id"] != asset_id
        ):
            raise WorkloadScenarioError(
                f"ResolvedScenario expected public asset {asset_id} lacks its producer"
            )
    upload_asset_ids = {
        requirement["expected_public_asset_id"]
        for requirement in result.values()
        if requirement["kind"] == "upload_or_buffer"
    }
    if upload_asset_ids != set(expected_assets):
        raise WorkloadScenarioError(
            "ResolvedScenario upload missions and public assets are not closed"
        )
    return result


def _json_scalar(value: object, *, label: str) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    raise WorkloadScenarioError(f"{label} must be a finite JSON scalar")


def _validate_task(
    value: object,
    *,
    providers: dict[str, dict[str, Any]],
    assets: tuple[dict[str, Any], ...],
    entities: dict[str, dict[str, Any]],
    sensors: dict[str, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    label = "ResolvedScenario.task"
    task = _exact_object(value, _TASK_FIELDS, label=label)
    for field in ("task_id", "package_id", "verifier_id"):
        _string(task[field], label=f"{label}.{field}", identifier=True)
    for field in ("package_config", "package_schema", "instruction"):
        _file_ref(task[field], label=f"{label}.{field}")
    for field in (
        "required_capability_ids",
        "required_tool_ids",
        "logical_endpoint_ids",
        "asset_ids",
    ):
        _string_list(task[field], label=f"{label}.{field}", allow_empty=True)
    task_asset_ids = tuple(
        asset["asset_id"] for asset in assets if asset["source_kind"] == "task"
    )
    if tuple(task["asset_ids"]) != task_asset_ids:
        raise WorkloadScenarioError(
            "ResolvedScenario task asset IDs differ from the asset catalog"
        )
    asset_map = {asset["asset_id"]: asset for asset in assets}

    tools = _array(task["tools"], label=f"{label}.tools")
    tool_keys: list[str] = []
    tool_ids: set[str] = set()
    endpoint_ids: set[str] = set()
    agent_ids: set[str] = set()
    for index, item in enumerate(tools):
        item_label = f"{label}.tools[{index}]"
        binding = _exact_object(item, _TASK_TOOL_FIELDS, label=item_label)
        binding_id = _string(
            binding["binding_id"], label=f"{item_label}.binding_id", identifier=True
        )
        agent_id = _string(
            binding["agent_id"], label=f"{item_label}.agent_id", identifier=True
        )
        tool_id = _string(binding["tool_id"], label=f"{item_label}.tool_id", identifier=True)
        endpoint_id = _string(
            binding["endpoint_id"], label=f"{item_label}.endpoint_id", identifier=True
        )
        if binding_id != f"{agent_id}.{tool_id}":
            raise WorkloadScenarioError(f"{item_label}.binding_id is not canonical")
        _file_ref(binding["request_schema"], label=f"{item_label}.request_schema")
        _file_ref(binding["response_schema"], label=f"{item_label}.response_schema")
        _integer(binding["timeout_ms"], label=f"{item_label}.timeout_ms", minimum=1)
        _boolean(binding["idempotent"], label=f"{item_label}.idempotent")
        tool_keys.append(binding_id)
        tool_ids.add(tool_id)
        endpoint_ids.add(endpoint_id)
        agent_ids.add(agent_id)
    _sorted_record_keys(tool_keys, label=f"{label}.tools")
    if set(task["required_tool_ids"]) - tool_ids:
        raise WorkloadScenarioError("ResolvedScenario required tools are not completely bound")

    queries = _array(task["queries"], label=f"{label}.queries")
    query_keys: list[str] = []
    for index, item in enumerate(queries):
        item_label = f"{label}.queries[{index}]"
        binding = _exact_object(item, _TASK_QUERY_FIELDS, label=item_label)
        binding_id = _string(
            binding["binding_id"], label=f"{item_label}.binding_id", identifier=True
        )
        agent_id = _string(
            binding["agent_id"], label=f"{item_label}.agent_id", identifier=True
        )
        query_type = _string(
            binding["query_type"], label=f"{item_label}.query_type", identifier=True
        )
        endpoint_id = _string(
            binding["endpoint_id"], label=f"{item_label}.endpoint_id", identifier=True
        )
        if binding_id != f"{agent_id}.{query_type}":
            raise WorkloadScenarioError(f"{item_label}.binding_id is not canonical")
        _file_ref(binding["request_schema"], label=f"{item_label}.request_schema")
        _file_ref(binding["response_schema"], label=f"{item_label}.response_schema")
        _integer(binding["timeout_ms"], label=f"{item_label}.timeout_ms", minimum=1)
        query_keys.append(binding_id)
        endpoint_ids.add(endpoint_id)
        agent_ids.add(agent_id)
    _sorted_record_keys(query_keys, label=f"{label}.queries")

    observations = _array(task["observations"], label=f"{label}.observations")
    observation_keys: list[str] = []
    for index, item in enumerate(observations):
        item_label = f"{label}.observations[{index}]"
        binding = _exact_object(item, _TASK_OBSERVATION_FIELDS, label=item_label)
        binding_id = _string(
            binding["binding_id"], label=f"{item_label}.binding_id", identifier=True
        )
        agent_id = _string(
            binding["agent_id"], label=f"{item_label}.agent_id", identifier=True
        )
        observation_id = _string(
            binding["observation_id"],
            label=f"{item_label}.observation_id",
            identifier=True,
        )
        endpoint_id = _string(
            binding["endpoint_id"], label=f"{item_label}.endpoint_id", identifier=True
        )
        if binding_id != f"{agent_id}.{observation_id}":
            raise WorkloadScenarioError(f"{item_label}.binding_id is not canonical")
        _file_ref(binding["schema_file"], label=f"{item_label}.schema_file")
        _integer(binding["timeout_ms"], label=f"{item_label}.timeout_ms", minimum=1)
        projection_value = binding["inspection"]
        if projection_value is not None:
            projection_label = f"{item_label}.inspection"
            projection = _exact_object(
                projection_value,
                _INSPECTION_OBSERVATION_FIELDS,
                label=projection_label,
            )
            if projection["projection_kind"] != "inspection":
                raise WorkloadScenarioError(f"{projection_label}.projection_kind is unsupported")
            for field in (
                "observation_id",
                "work_order_id",
                "target_id",
                "simulation_asset_id",
                "sensor_id",
            ):
                _string(
                    projection[field],
                    label=f"{projection_label}.{field}",
                    identifier=True,
                )
            if projection["observation_id"] != observation_id:
                raise WorkloadScenarioError(
                    f"{projection_label}.observation_id differs from binding"
                )
            _media_type(projection["media_type"], label=f"{projection_label}.media_type")
            minimum_distance = _number(
                projection["min_distance_m"],
                label=f"{projection_label}.min_distance_m",
                minimum=0.0,
                exclusive_minimum=True,
            )
            maximum_distance = _number(
                projection["max_distance_m"],
                label=f"{projection_label}.max_distance_m",
                minimum=0.0,
                exclusive_minimum=True,
            )
            minimum_angle = _number(
                projection["min_view_angle_deg"],
                label=f"{projection_label}.min_view_angle_deg",
                minimum=0.0,
                maximum=180.0,
            )
            maximum_angle = _number(
                projection["max_view_angle_deg"],
                label=f"{projection_label}.max_view_angle_deg",
                minimum=0.0,
                maximum=180.0,
            )
            if minimum_distance > maximum_distance or minimum_angle > maximum_angle:
                raise WorkloadScenarioError(f"{projection_label} metric range is invalid")
            earliest = _integer(
                projection["earliest_time_ns"],
                label=f"{projection_label}.earliest_time_ns",
                minimum=0,
            )
            latest = _integer(
                projection["latest_time_ns"],
                label=f"{projection_label}.latest_time_ns",
                minimum=1,
            )
            if earliest >= latest:
                raise WorkloadScenarioError(f"{projection_label} time window is empty")
            target = targets.get(projection["target_id"])
            sensor = sensors.get(projection["sensor_id"])
            simulation_asset = asset_map.get(projection["simulation_asset_id"])
            parent = None if target is None else entities.get(target["parent_entity_id"])
            if (
                endpoint_id not in providers
                or target is None
                or sensor is None
                or sensor["provider_id"] != endpoint_id
                or target["required_sensor_id"] != sensor["sensor_id"]
                or parent is None
                or parent["owner_kind"] != "scenario"
                or simulation_asset is None
                or simulation_asset["source_kind"] != "task"
                or not _asset_has_audience(simulation_asset, "provider", endpoint_id)
                or float(projection["min_distance_m"])
                != float(target["min_distance_m"])
                or float(projection["max_distance_m"])
                != float(target["max_distance_m"])
                or float(projection["max_view_angle_deg"])
                != float(target["max_view_angle_deg"])
            ):
                raise WorkloadScenarioError(
                    f"{projection_label} differs from scenario authority"
                )

        urban_value = binding["urban"]
        flight_value = binding["flight"]
        if sum(
            value is not None
            for value in (projection_value, urban_value, flight_value)
        ) > 1:
            raise WorkloadScenarioError(
                f"{item_label} cannot declare multiple observation projections"
            )
        if urban_value is not None:
            urban_label = f"{item_label}.urban"
            urban = _exact_object(
                urban_value,
                _URBAN_OBSERVATION_FIELDS,
                label=urban_label,
            )
            if urban["projection_kind"] != "urban_recovery":
                raise WorkloadScenarioError(
                    f"{urban_label}.projection_kind is unsupported"
                )
            urban_observation_id = _string(
                urban["observation_id"],
                label=f"{urban_label}.observation_id",
                identifier=True,
            )
            if urban_observation_id != observation_id:
                raise WorkloadScenarioError(
                    f"{urban_label}.observation_id differs from binding"
                )
            observation_kind = _enum(
                urban["observation_kind"],
                frozenset({"camera", "telemetry", "safety", "mailbox"}),
                label=f"{urban_label}.observation_kind",
            )
            vehicle_id = _nullable_identifier(
                urban["vehicle_id"], label=f"{urban_label}.vehicle_id"
            )
            camera_id = _nullable_identifier(
                urban["camera_id"], label=f"{urban_label}.camera_id"
            )
            if observation_kind == "camera":
                valid_shape = vehicle_id is not None and camera_id is not None
            elif observation_kind in {"telemetry", "safety"}:
                valid_shape = vehicle_id is not None and camera_id is None
            else:
                valid_shape = vehicle_id is None and camera_id is None
            if not valid_shape:
                raise WorkloadScenarioError(
                    f"{urban_label} identity shape differs from observation kind"
                )
        if flight_value is not None:
            flight_label = f"{item_label}.flight"
            flight = _exact_object(
                flight_value,
                _FLIGHT_OBSERVATION_FIELDS,
                label=flight_label,
            )
            if flight["projection_kind"] != "flight":
                raise WorkloadScenarioError(
                    f"{flight_label}.projection_kind is unsupported"
                )
            flight_observation_id = _string(
                flight["observation_id"],
                label=f"{flight_label}.observation_id",
                identifier=True,
            )
            observation_kind = _enum(
                flight["observation_kind"],
                frozenset({"telemetry", "gnss"}),
                label=f"{flight_label}.observation_kind",
            )
            vehicle_id = _string(
                flight["vehicle_id"],
                label=f"{flight_label}.vehicle_id",
                identifier=True,
            )
            vehicle = entities.get(vehicle_id)
            if (
                flight_observation_id != observation_id
                or observation_id != f"flight.{observation_kind}.{vehicle_id}"
                or endpoint_id not in providers
                or vehicle is None
                or vehicle["kind"] != "uav"
                or vehicle["state"] != "dynamic"
                or vehicle["owner_kind"] != "provider"
                or vehicle["owner_id"] != endpoint_id
            ):
                raise WorkloadScenarioError(
                    f"{flight_label} differs from scenario authority"
                )
        observation_keys.append(binding_id)
        endpoint_ids.add(endpoint_id)
        agent_ids.add(agent_id)
    _sorted_record_keys(observation_keys, label=f"{label}.observations")

    provider_ids = set(providers)
    logical_ids = set(task["logical_endpoint_ids"])
    if logical_ids & provider_ids or logical_ids != endpoint_ids - provider_ids:
        raise WorkloadScenarioError(
            "ResolvedScenario task logical endpoints are not exactly closed"
        )

    goals = _array(task["goals"], label=f"{label}.goals", minimum=1)
    goal_keys: list[str] = []
    for index, item in enumerate(goals):
        item_label = f"{label}.goals[{index}]"
        goal = _exact_object(item, _GOAL_FIELDS, label=item_label)
        goal_id = _string(goal["goal_id"], label=f"{item_label}.goal_id", identifier=True)
        verifier_id = _string(
            goal["verifier_id"], label=f"{item_label}.verifier_id", identifier=True
        )
        if verifier_id != task["verifier_id"]:
            raise WorkloadScenarioError(f"{item_label}.verifier_id differs from task verifier")
        _string(goal["metric_id"], label=f"{item_label}.metric_id", identifier=True)
        _enum(
            goal["operator"],
            frozenset({"ge", "gt", "le", "lt", "eq"}),
            label=f"{item_label}.operator",
        )
        _number(goal["threshold"], label=f"{item_label}.threshold")
        _enum(
            goal["evidence"],
            frozenset({"authoritative_state", "event_log", "artifact"}),
            label=f"{item_label}.evidence",
        )
        parameters = _array(goal["parameters"], label=f"{item_label}.parameters")
        parameter_names: list[str] = []
        for parameter_index, parameter_value in enumerate(parameters):
            parameter_label = f"{item_label}.parameters[{parameter_index}]"
            parameter = _exact_object(
                parameter_value, _NAMED_VALUE_FIELDS, label=parameter_label
            )
            name = _string(
                parameter["name"], label=f"{parameter_label}.name", identifier=True
            )
            _json_scalar(parameter["value"], label=f"{parameter_label}.value")
            parameter_names.append(name)
        if len(parameter_names) != len(set(parameter_names)):
            raise WorkloadScenarioError(f"{item_label} parameter names must be unique")
        goal_keys.append(goal_id)
    _sorted_record_keys(goal_keys, label=f"{label}.goals")
    _digest(task["task_contract_digest"], label=f"{label}.task_contract_digest")
    return task, agent_ids


def _validate_asset_audience_closure(
    assets: tuple[dict[str, Any], ...],
    *,
    providers: dict[str, dict[str, Any]],
    task: dict[str, Any],
    known_agent_ids: set[str],
) -> None:
    provider_ids = set(providers)
    verifier_id = task["verifier_id"]
    required_public_world_audiences = {
        ("harness", "harness"),
        *(("provider", provider_id) for provider_id in provider_ids),
        *(("agent", agent_id) for agent_id in known_agent_ids),
        ("verifier", verifier_id),
    }
    for asset in assets:
        label = f"ResolvedScenario asset {asset['asset_id']}"
        actual = {
            (audience["role"], audience["workload_id"])
            for audience in asset["audiences"]
        }
        for role, workload_id in actual:
            if role == "harness" and workload_id != "harness":
                raise WorkloadScenarioError(f"{label} names an invalid harness audience")
            if role == "provider" and workload_id not in provider_ids:
                raise WorkloadScenarioError(f"{label} names a disabled provider audience")
            if role == "verifier" and workload_id != verifier_id:
                raise WorkloadScenarioError(f"{label} names another verifier audience")
        if (
            asset["source_kind"] == "world"
            and asset["classification"] == "public"
            and not required_public_world_audiences <= actual
        ):
            raise WorkloadScenarioError(
                f"{label} does not authorize every declared runtime workload"
            )


def _authorized_assets(
    assets: tuple[dict[str, Any], ...],
    *,
    role: Literal["harness", "provider", "agent", "verifier"],
    workload_id: str,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        asset
        for asset in assets
        if _asset_has_audience(asset, role, workload_id)
    )


def validate_workload_scenario(
    value: object,
    *,
    expected_seed: int,
    expected_digest: str,
    role: Literal["harness", "provider", "agent", "verifier"],
    workload_id: str,
    projected_assets: object,
    provider_capabilities: tuple[str, ...] | None = None,
) -> ValidatedWorkloadScenario:
    """Validate one complete serialized scenario and its workload projection."""

    if type(role) is not str or role not in {
        "harness",
        "provider",
        "agent",
        "verifier",
    }:
        raise WorkloadScenarioError("workload role is unsupported")
    workload_id = _string(workload_id, label="workload_id", identifier=True)
    expected_seed = _integer(
        expected_seed,
        label="expected_seed",
        minimum=0,
        maximum=_MAX_SEED,
    )
    expected_digest = _digest(expected_digest, label="contract.scenario_digest")

    scenario = _exact_object(value, _SCENARIO_FIELDS, label="ResolvedScenario")
    if scenario["schema_version"] != "aero-bench.resolved-scenario/v4":
        raise WorkloadScenarioError("ResolvedScenario schema version is unsupported")
    seed = _integer(
        scenario["seed"],
        label="ResolvedScenario.seed",
        minimum=0,
        maximum=_MAX_SEED,
    )
    if seed != expected_seed:
        raise WorkloadScenarioError("ResolvedScenario seed differs from workload seed")
    declared_digest = _digest(
        scenario["scenario_digest"], label="ResolvedScenario.scenario_digest"
    )
    if declared_digest != expected_digest:
        raise WorkloadScenarioError("workload scenario_digest differs from scenario")
    body = dict(scenario)
    body.pop("scenario_digest")
    if hashlib.sha256(_canonical_json(body)).hexdigest() != declared_digest:
        raise WorkloadScenarioError("scenario_digest does not match scenario content")

    _file_ref(
        scenario["source_world_package"],
        label="ResolvedScenario.source_world_package",
    )
    if scenario["world_schema_version"] != "aero-bench.world/v2":
        raise WorkloadScenarioError("ResolvedScenario world schema is unsupported")
    _string(scenario["world_id"], label="ResolvedScenario.world_id", identifier=True)
    _digest(scenario["world_digest"], label="ResolvedScenario.world_digest")
    _digest(
        scenario["source_asset_digest"],
        label="ResolvedScenario.source_asset_digest",
    )
    declared_asset_digest = _digest(
        scenario["scenario_asset_digest"],
        label="ResolvedScenario.scenario_asset_digest",
    )
    selected_launch_site_id = _nullable_identifier(
        scenario["selected_launch_site_id"],
        label="ResolvedScenario.selected_launch_site_id",
    )

    providers = _validate_providers(scenario["providers"])
    assets = _validated_assets(scenario["assets"])
    if hashlib.sha256(_canonical_json(list(assets))).hexdigest() != declared_asset_digest:
        raise WorkloadScenarioError(
            "scenario_asset_digest does not match the scenario asset catalog"
        )
    asset_map = {asset["asset_id"]: asset for asset in assets}
    frame = _validate_frame_authority(scenario["frame_authority"], asset_map=asset_map)
    _validate_layers(scenario["base_layers"], scenario["layers"], asset_map=asset_map)
    entities = _validate_entities(
        scenario["entities"],
        providers=providers,
        asset_map=asset_map,
        frame=frame,
    )
    motion_provider_ids = {
        provider_id for provider_id, provider in providers.items()
        if provider["runtime_stage"] == "motion"
    }
    dynamic_owner_ids = {
        entity["owner_id"] for entity in entities.values()
        if entity["state"] == "dynamic" and entity["owner_kind"] == "provider"
    }
    if motion_provider_ids != dynamic_owner_ids:
        raise WorkloadScenarioError(
            "motion stage Provider IDs must exactly equal provider-owned dynamic entity owner IDs"
        )
    _validate_buildings(
        scenario["buildings"], asset_map=asset_map, entities=entities, frame=frame
    )
    _validate_roads(scenario["roads"], frame=frame)
    _validate_regions(scenario["regions"], frame=frame)
    launch_sites = _validate_launch_sites(
        scenario["launch_sites"],
        selected_launch_site_id=selected_launch_site_id,
        entities=entities,
        frame=frame,
    )
    sensors = _validate_sensors(
        scenario["sensors"], providers=providers, entities=entities, frame=frame
    )
    targets = _validate_semantic_targets(
        scenario["semantic_targets"],
        entities=entities,
        sensors=sensors,
        frame=frame,
    )
    _validate_weather(scenario["weather"])
    engine_bindings = _validate_engine_bindings(
        scenario["engine_frame_bindings"],
        providers=providers,
        asset_map=asset_map,
        entities=entities,
    )
    _validate_sumo(
        scenario["sumo"],
        providers=providers,
        asset_map=asset_map,
        entities=entities,
        engine_bindings=engine_bindings,
    )
    _validate_network(scenario["network"], providers=providers, entities=entities)
    expected_public_assets = _validate_expected_public_assets(
        scenario["expected_public_assets"]
    )
    _validate_missions(
        scenario["mission_requirements"],
        selected_launch_site_id=selected_launch_site_id,
        launch_sites=launch_sites,
        targets=targets,
        expected_assets=expected_public_assets,
        source_assets=asset_map,
    )
    task, known_agent_ids = _validate_task(
        scenario["task"],
        providers=providers,
        assets=assets,
        entities=entities,
        sensors=sensors,
        targets=targets,
    )
    if selected_launch_site_id is None and (
        task["package_id"] != "logistics.arrivals.v1"
        or len(providers) != 1
        or any(provider["runtime_stage"] != "business_environment" for provider in providers.values())
        or set(next(iter(providers.values()))["capability_ids"]) != {
            "logistics.facilities.state", "logistics.orders.authority",
            "logistics.orders.scheduled-arrivals",
        }
        or any(entity["state"] != "static" for entity in entities.values())
        or sensors or targets or scenario["mission_requirements"]
        or engine_bindings or scenario["sumo"] is not None or scenario["network"] is not None
        or task["tools"] or task["queries"] or task["observations"]
    ):
        raise WorkloadScenarioError("no-launch scenario requires the static Business-only arrivals profile")
    _validate_asset_audience_closure(
        assets,
        providers=providers,
        task=task,
        known_agent_ids=known_agent_ids,
    )

    if role == "harness" and workload_id != "harness":
        raise WorkloadScenarioError("harness workload identity is invalid")
    if role == "provider" and workload_id not in providers:
        raise WorkloadScenarioError("provider workload is absent from ResolvedScenario")
    if role == "verifier" and workload_id != task["verifier_id"]:
        raise WorkloadScenarioError("verifier workload identity is invalid")
    if role == "agent" and workload_id not in known_agent_ids and not any(
        _asset_has_audience(asset, "agent", workload_id) for asset in assets
    ):
        raise WorkloadScenarioError("agent workload is absent from ResolvedScenario")

    if provider_capabilities is not None:
        if type(provider_capabilities) is not tuple:
            raise WorkloadScenarioError("provider_capabilities must be a tuple")
        normalized_capabilities = tuple(
            _string(
                capability,
                label=f"provider_capabilities[{index}]",
                identifier=True,
            )
            for index, capability in enumerate(provider_capabilities)
        )
        if not normalized_capabilities or len(normalized_capabilities) != len(
            set(normalized_capabilities)
        ):
            raise WorkloadScenarioError(
                "provider_capabilities must be non-empty and unique"
            )
        provider = providers.get(workload_id)
        if provider is None or tuple(provider["capability_ids"]) != tuple(
            sorted(normalized_capabilities)
        ):
            raise WorkloadScenarioError(
                "ResolvedScenario provider capabilities differ from workload provider"
            )

    expected_assets = _authorized_assets(assets, role=role, workload_id=workload_id)
    projected = _array(projected_assets, label="workload scenario_assets")
    canonical_projected = _canonical_projected_assets(projected)
    if canonical_projected != expected_assets:
        raise WorkloadScenarioError(
            "workload scenario_assets are not the exact authorized projection"
        )
    return ValidatedWorkloadScenario(
        scenario_digest=declared_digest,
        scenario=scenario,
        assets=expected_assets,
    )


__all__ = [
    "ValidatedWorkloadScenario",
    "WorkloadScenarioError",
    "validate_workload_scenario",
]
