"""Strict, frozen WorldPackage contracts for the World Simulation vertical.

These models define only the static world declaration. The schema is frozen at
`aero-bench.world/v2`; `v1` payloads are rejected. Every collection is an
explicit required field, even when the intended value is empty. Digests are
mandatory and self-verified: `asset_digest` covers the canonical asset manifest
sorted by asset ID, while `world_digest` covers the ordered full package
content except the digest envelope itself.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world import frame_math
from aero_bench.world.frames import LocalFrameOrigin

WORLD_SCHEMA_VERSION = "aero-bench.world/v2"

EntityKind: TypeAlias = Literal["uav", "ugv", "pedestrian", "static_asset"]
AuthorityKind: TypeAlias = Literal[
    "gazebo_physics", "sumo_traffic", "scenario_static"
]
BaseLayerKind: TypeAlias = Literal["imagery", "terrain"]
LayerKind: TypeAlias = Literal[
    "buildings", "roads", "regions", "entities", "weather", "missions", "osm_scene", "osm_mesh"
]
RoadKind: TypeAlias = Literal[
    "vehicle_lane",
    "pedestrian_path",
    "runway",
    "taxiway",
    "service_road",
]
RegionKind: TypeAlias = Literal["geofence", "no_fly", "communications_shadow"]
VisibilityKind: TypeAlias = Literal["public", "private"]
AudienceKind: TypeAlias = Literal["public", "provider", "entity", "viewer", "verifier"]
InterpolationKind: TypeAlias = Literal["nearest", "bilinear"]
HeightReferenceKind: TypeAlias = Literal["wgs84_ellipsoid", "amsl", "agl"]
ScenarioRoleKind: TypeAlias = Literal[
    "mission",
    "motion",
    "sensor",
    "static_scene",
    "traffic",
    "wireless_network",
]
WifiStandardKind: TypeAlias = Literal["802.11n", "802.11ac", "802.11ax"]
SourceFrameKind: TypeAlias = Literal[
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
]
SensorKind: TypeAlias = Literal["camera"]
EvidenceKind: TypeAlias = Literal[
    "sensor_frame",
    "image",
    "report",
    "network_upload_or_buffer",
    "bbox_2d",
    "segmentation_mask",
    "pose_6d",
    "track",
]
PrecipitationKind: TypeAlias = Literal["none", "drizzle", "rain", "snow", "hail"]
EngineKind: TypeAlias = Literal["gazebo", "sumo"]
MissionRequirementKind: TypeAlias = Literal[
    "takeoff",
    "observation",
    "upload_or_buffer",
    "return_to_launch",
    "land",
]
ExpectedPublicAssetKind: TypeAlias = Literal[
    "viewer_layer",
    "mission_output",
    "evidence_bundle",
]
PrecisionKind: TypeAlias = Literal["exact_bytes", "absolute_tolerance"]
SumoObjectKind: TypeAlias = Literal["vehicle", "person"]
AssetRole: TypeAlias = Literal[
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
]

ALLOWED_AUTHORITY_KINDS: dict[
    tuple[EntityKind, Literal["static", "dynamic"]], frozenset[AuthorityKind]
] = {
    ("uav", "dynamic"): frozenset({"gazebo_physics"}),
    ("ugv", "dynamic"): frozenset({"sumo_traffic"}),
    ("pedestrian", "dynamic"): frozenset({"sumo_traffic"}),
    ("static_asset", "static"): frozenset({"scenario_static"}),
}

ASSET_ROLE_SOURCE_FRAMES: dict[AssetRole, frozenset[SourceFrameKind]] = {
    "geoid_model": frozenset({"raster_pixel"}),
    "terrain_model": frozenset({"raster_pixel"}),
    "imagery_tiles": frozenset({"raster_pixel", "WGS84"}),
    "terrain_tiles": frozenset({"raster_pixel", "ENU"}),
    "layer_tiles": frozenset({"ENU", "WGS84", "raster_pixel", "asset_local"}),
    "building_source": frozenset({"asset_local"}),
    # A glTF/GLB render mesh carries asset-local metre vertices (glTF 2.0
    # coordinate convention); "WGS84" remains only for render assets whose own
    # content is geodetic (e.g. legacy coordinate JSON). The AssetRecord
    # validator forbids claiming WGS84 for glTF media types.
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

#: Media types whose payload follows the glTF 2.0 asset-local metre frame.
_GLTF_MEDIA_TYPES = frozenset({"model/gltf-binary", "model/gltf+json"})

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]*\Z")
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+\Z")
_UNIT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./^-]*\Z")
_LICENSE_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9.+-]*|LicenseRef-[A-Za-z0-9][A-Za-z0-9.+-]*)\Z"
)
_SELECTOR_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_SELECTOR_PATTERN = re.compile(
    rf"{_SELECTOR_SEGMENT}(?:/{_SELECTOR_SEGMENT})*"
    rf"(?:#{_SELECTOR_SEGMENT}(?:/{_SELECTOR_SEGMENT})*)?\Z"
)
_FRAME_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*\Z"
)
_SUMO_OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")


def _require_real_digest(value: str) -> str:
    if value == "0" * 64:
        raise ValueError("sha256 must be a real digest, not a placeholder")
    return value


def _require_finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _require_unique(label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


def _polygon_area(points: tuple["EnuPoint", ...]) -> float:
    area = 0.0
    count = len(points)
    for index, point in enumerate(points):
        nxt = points[(index + 1) % count]
        area += point.east_m * nxt.north_m - nxt.east_m * point.north_m
    return abs(area) * 0.5


def _validate_unit_quaternion(
    *,
    qw: float,
    qx: float,
    qy: float,
    qz: float,
) -> None:
    frame_math.UnitQuaternion(qw, qx, qy, qz)


def _validate_selector(value: str) -> str:
    if value.strip() != value:
        raise ValueError("selector must not have leading or trailing whitespace")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("selector must not contain control characters")
    if "\\" in value or "//" in value or _SELECTOR_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "selector must match the documented relative artifact grammar "
            "(segment ('/' segment)* ('#' fragment)?), not a URL or path escape"
        )
    return value


def _validate_frame_id(value: str) -> str:
    if value.strip() != value:
        raise ValueError("frame ids must not have surrounding whitespace")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("frame ids must not contain control characters")
    if _FRAME_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("frame ids must be normalized slash-delimited frame tokens")
    return value


def _validate_measurement_metadata(
    *,
    units: tuple["UnitDeclaration", ...],
    precision: tuple["PrecisionMetadata", ...],
    label: str,
) -> None:
    _require_unique(f"{label} unit quantity", [unit.quantity for unit in units])
    _require_unique(
        f"{label} precision quantity",
        [item.quantity for item in precision],
    )
    unit_by_quantity = {unit.quantity: unit.unit for unit in units}
    precision_quantities = {item.quantity for item in precision}
    if set(unit_by_quantity) != precision_quantities:
        raise ValueError(f"{label} units and precision quantities must match exactly")
    for item in precision:
        if unit_by_quantity[item.quantity] != item.unit:
            raise ValueError(
                f"{label} precision unit must match the declared unit for quantity "
                f"{item.quantity}"
            )


def _validate_point_in_bounds(
    *,
    point: "EnuPoint",
    bounds: "SpatialBounds",
    label: str,
) -> None:
    if not bounds.min_east_m <= point.east_m <= bounds.max_east_m:
        raise ValueError(f"{label} east_m must fall inside WorldFrame.spatial_extent")
    if not bounds.min_north_m <= point.north_m <= bounds.max_north_m:
        raise ValueError(f"{label} north_m must fall inside WorldFrame.spatial_extent")


def _validate_altitude_in_bounds(
    *,
    altitude_m: float,
    bounds: "SpatialBounds",
    label: str,
) -> None:
    if not bounds.min_up_m <= altitude_m <= bounds.max_up_m:
        raise ValueError(f"{label} must fall inside WorldFrame.spatial_extent")


def _validate_pose_in_bounds(
    *,
    pose: "WorldPose",
    bounds: "SpatialBounds",
    label: str,
) -> None:
    _validate_point_in_bounds(
        point=EnuPoint(east_m=pose.east_m, north_m=pose.north_m),
        bounds=bounds,
        label=label,
    )
    _validate_altitude_in_bounds(
        altitude_m=pose.up_m,
        bounds=bounds,
        label=f"{label} up_m",
    )


class ArtifactSelector(StrictModel):
    """Strict artifact identity; never an arbitrary URL."""

    artifact_id: Identifier
    selector: Annotated[str, Field(max_length=512)]
    sha256: Sha256

    @field_validator("selector")
    @classmethod
    def strict_selector_grammar(cls, value: str) -> str:
        return _validate_selector(value)

    @field_validator("sha256")
    @classmethod
    def real_digest(cls, value: str) -> str:
        return _require_real_digest(value)


class EnuPoint(StrictModel):
    east_m: float
    north_m: float

    @field_validator("east_m", "north_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("ENU point", value)


class SpatialBounds(StrictModel):
    min_east_m: float
    max_east_m: float
    min_north_m: float
    max_north_m: float
    min_up_m: float
    max_up_m: float
    vertical_reference: Literal["enu_up"]

    @field_validator(
        "min_east_m",
        "max_east_m",
        "min_north_m",
        "max_north_m",
        "min_up_m",
        "max_up_m",
    )
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("spatial bounds", value)

    @model_validator(mode="after")
    def ordered_bounds(self) -> "SpatialBounds":
        for label, low, high in (
            ("east", self.min_east_m, self.max_east_m),
            ("north", self.min_north_m, self.max_north_m),
            ("up", self.min_up_m, self.max_up_m),
        ):
            if low >= high:
                raise ValueError(f"spatial bounds {label}_m require min < max")
        return self


class AssetAudience(StrictModel):
    audience_kind: AudienceKind
    audience_id: str

    @field_validator("audience_id")
    @classmethod
    def valid_audience_id(cls, value: str) -> str:
        if value.strip() != value or not value:
            raise ValueError("audience_id must be a non-empty normalized token")
        return value

    @model_validator(mode="after")
    def scope_matches_kind(self) -> "AssetAudience":
        if self.audience_kind == "public":
            if self.audience_id != "all":
                raise ValueError("public audience must use audience_id 'all'")
        elif (
            self.audience_id == "all"
            or _IDENTIFIER_RE.fullmatch(self.audience_id) is None
        ):
            raise ValueError(
                "non-public audience_id must be an Identifier and cannot be 'all'"
            )
        return self


class UnitDeclaration(StrictModel):
    quantity: Identifier
    unit: Annotated[str, Field(min_length=1, max_length=32)]

    @field_validator("unit")
    @classmethod
    def valid_unit(cls, value: str) -> str:
        if _UNIT_RE.fullmatch(value) is None:
            raise ValueError("unit must be a normalized engineering unit token")
        return value


class PrecisionMetadata(StrictModel):
    quantity: Identifier
    kind: PrecisionKind
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    exact_bytes: Annotated[int, Field(gt=0)] | None
    absolute_tolerance: Annotated[float, Field(gt=0)] | None

    @field_validator("absolute_tolerance")
    @classmethod
    def finite_tolerance(cls, value: float | None) -> float | None:
        if value is None:
            return value
        return _require_finite("precision metadata", value)

    @field_validator("unit")
    @classmethod
    def valid_unit(cls, value: str) -> str:
        if _UNIT_RE.fullmatch(value) is None:
            raise ValueError("unit must be a normalized engineering unit token")
        return value

    @model_validator(mode="after")
    def kind_specific_precision(self) -> "PrecisionMetadata":
        if self.kind == "exact_bytes":
            if self.exact_bytes is None or self.absolute_tolerance is not None:
                raise ValueError(
                    "exact_bytes precision requires exact_bytes and forbids absolute_tolerance"
                )
        else:
            if self.absolute_tolerance is None or self.exact_bytes is not None:
                raise ValueError(
                    "absolute_tolerance precision requires absolute_tolerance and forbids exact_bytes"
                )
        return self


class AssetProvenance(StrictModel):
    source_kind: Literal["bundled_offline"]
    recorded_by: Identifier
    source_dataset: Annotated[str, Field(min_length=1, max_length=128)]
    source_version: Annotated[str, Field(min_length=1, max_length=64)]
    runtime_download: Literal[False]


class AssetRecord(StrictModel):
    artifact: ArtifactSelector
    asset_role: AssetRole
    byte_size: Annotated[int, Field(gt=0)]
    media_type: Annotated[str, Field(min_length=3, max_length=128)]
    visibility: VisibilityKind
    audiences: tuple[AssetAudience, ...] = Field(min_length=1)
    units: tuple[UnitDeclaration, ...] = Field(min_length=1)
    source_frame: SourceFrameKind
    precision: tuple[PrecisionMetadata, ...] = Field(min_length=1)
    provenance: AssetProvenance
    license_id: Annotated[str, Field(min_length=1, max_length=128)]
    license_file: ArtifactSelector

    @field_validator("media_type")
    @classmethod
    def valid_media_type(cls, value: str) -> str:
        if _MEDIA_TYPE_RE.fullmatch(value) is None:
            raise ValueError("media_type must be a normalized MIME type")
        return value

    @field_validator("license_id")
    @classmethod
    def valid_license_id(cls, value: str) -> str:
        if _LICENSE_RE.fullmatch(value) is None:
            raise ValueError("license_id must be SPDX or LicenseRef-*")
        return value

    @model_validator(mode="after")
    def audience_precision_visibility_and_frame(self) -> "AssetRecord":
        audience_keys = [
            f"{audience.audience_kind}:{audience.audience_id}"
            for audience in self.audiences
        ]
        _require_unique("asset audience", audience_keys)
        _validate_measurement_metadata(
            units=self.units,
            precision=self.precision,
            label="asset",
        )
        has_public = any(
            audience.audience_kind == "public" and audience.audience_id == "all"
            for audience in self.audiences
        )
        if self.visibility == "public" and not has_public:
            raise ValueError(
                "public assets must explicitly include the public audience"
            )
        if self.visibility == "private" and has_public:
            raise ValueError("private assets cannot include the public audience")
        if self.asset_role == "license" and not self.media_type.startswith("text/"):
            raise ValueError("license assets must use a text/* media type")
        if self.source_frame not in ASSET_ROLE_SOURCE_FRAMES[self.asset_role]:
            raise ValueError(
                f"{self.asset_role} assets must use one of "
                f"{sorted(ASSET_ROLE_SOURCE_FRAMES[self.asset_role])} source frames"
            )
        if (
            self.asset_role == "building_render"
            and self.media_type in _GLTF_MEDIA_TYPES
            and self.source_frame == "WGS84"
        ):
            raise ValueError(
                "a glTF building_render is expressed in the glTF 2.0 "
                "asset-local Y-up metre frame and must not be labelled WGS84; "
                "declare source_frame asset_local"
            )
        return self


class VerticalDatumBinding(StrictModel):
    geoid_correction_asset_id: Identifier
    terrain_height_asset_id: Identifier
    geoid_interpolation: InterpolationKind
    terrain_interpolation: InterpolationKind
    geoid_precision_m: Annotated[float, Field(gt=0)]
    terrain_precision_m: Annotated[float, Field(gt=0)]

    @field_validator("geoid_precision_m", "terrain_precision_m")
    @classmethod
    def finite_precision(cls, value: float) -> float:
        return _require_finite("vertical datum precision", value)


class WorldFrame(StrictModel):
    geodetic_frame_id: Literal["WGS84"]
    ecef_frame_id: Literal["ECEF"]
    enu_frame_id: Literal["ENU"]
    ned_frame_id: Literal["NED"]
    transform_chain: Literal["WGS84->ECEF->ENU->NED"]
    datum: Literal["WGS84"]
    semi_major_axis_m: Literal[6_378_137.0]
    inverse_flattening: Literal[298.257_223_563]
    ellipsoid_height_reference: Literal["wgs84_ellipsoid"]
    amsl_height_reference: Literal["orthometric_msl"]
    agl_height_reference: Literal["terrain_relative"]
    origin: LocalFrameOrigin
    origin_height_reference: Literal["wgs84_ellipsoid"]
    spatial_extent: SpatialBounds
    vertical_datum: VerticalDatumBinding

    @model_validator(mode="after")
    def origin_matches_declared_frame(self) -> "WorldFrame":
        if self.origin.frame_id != self.geodetic_frame_id:
            raise ValueError(
                "world frame origin must use the declared geodetic_frame_id"
            )
        if self.origin_height_reference != self.ellipsoid_height_reference:
            raise ValueError(
                "world frame origin_height_reference must remain WGS84 ellipsoid height"
            )
        return self


class WorldPose(StrictModel):
    frame_id: Literal["ENU"]
    east_m: float
    north_m: float
    up_m: float
    qw: float
    qx: float
    qy: float
    qz: float

    @field_validator("east_m", "north_m", "up_m")
    @classmethod
    def finite_position(cls, value: float) -> float:
        return _require_finite("world pose", value)

    @model_validator(mode="after")
    def unit_quaternion(self) -> "WorldPose":
        _validate_unit_quaternion(
            qw=self.qw,
            qx=self.qx,
            qy=self.qy,
            qz=self.qz,
        )
        return self


class ParentRelativePose(StrictModel):
    frame_id: Literal["parent"]
    x_m: float
    y_m: float
    z_m: float
    qw: float
    qx: float
    qy: float
    qz: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite_position(cls, value: float) -> float:
        return _require_finite("parent-relative pose", value)

    @model_validator(mode="after")
    def unit_quaternion(self) -> "ParentRelativePose":
        _validate_unit_quaternion(
            qw=self.qw,
            qx=self.qx,
            qy=self.qy,
            qz=self.qz,
        )
        return self


class ViewerLayerSource(StrictModel):
    layer_id: Identifier
    kind: BaseLayerKind
    asset_id: Identifier
    visibility: Literal["public"]
    default_visible: bool


class PublicLayer(StrictModel):
    layer_id: Identifier
    kind: LayerKind
    asset_id: Identifier
    visibility: Literal["public"]
    default_visible: bool


class BuildingGeometry(StrictModel):
    footprint_enu_m: tuple[EnuPoint, ...] = Field(min_length=3)
    base_altitude_m: float
    top_altitude_m: float
    height_reference: HeightReferenceKind | Literal["enu_up"]

    @field_validator("base_altitude_m", "top_altitude_m")
    @classmethod
    def finite_altitude(cls, value: float) -> float:
        return _require_finite("building geometry altitude", value)

    @model_validator(mode="after")
    def valid_geometry(self) -> "BuildingGeometry":
        points = [(point.east_m, point.north_m) for point in self.footprint_enu_m]
        if len(set(points)) != len(points):
            raise ValueError("building footprint points must be unique")
        if _polygon_area(self.footprint_enu_m) <= 0.0:
            raise ValueError("building footprint must enclose positive area")
        if self.top_altitude_m <= self.base_altitude_m:
            raise ValueError("building top_altitude_m must exceed base_altitude_m")
        return self


class BuildingSpec(StrictModel):
    building_id: Identifier
    entity_id: Identifier
    source_asset_id: Identifier
    render_asset_id: Identifier
    collision_asset_id: Identifier
    geometry: BuildingGeometry

    @model_validator(mode="after")
    def distinct_asset_ids(self) -> "BuildingSpec":
        _require_unique(
            "building asset reference",
            [
                self.source_asset_id,
                self.render_asset_id,
                self.collision_asset_id,
            ],
        )
        return self


class RoadSpec(StrictModel):
    road_id: Identifier
    kind: RoadKind
    centerline_enu_m: tuple[EnuPoint, ...] = Field(min_length=2)
    width_m: Annotated[float, Field(gt=0)]

    @field_validator("width_m")
    @classmethod
    def finite_width(cls, value: float) -> float:
        return _require_finite("road width", value)

    @model_validator(mode="after")
    def valid_centerline(self) -> "RoadSpec":
        if len({(point.east_m, point.north_m) for point in self.centerline_enu_m}) < 2:
            raise ValueError(
                "road centerline must contain at least two distinct points"
            )
        return self


class RegionGeometry(StrictModel):
    footprint_enu_m: tuple[EnuPoint, ...] = Field(min_length=3)
    min_altitude_m: float
    max_altitude_m: float
    height_reference: HeightReferenceKind | Literal["enu_up"]

    @field_validator("min_altitude_m", "max_altitude_m")
    @classmethod
    def finite_altitude(cls, value: float) -> float:
        return _require_finite("region geometry altitude", value)

    @model_validator(mode="after")
    def valid_geometry(self) -> "RegionGeometry":
        points = [(point.east_m, point.north_m) for point in self.footprint_enu_m]
        if len(set(points)) != len(points):
            raise ValueError("region footprint points must be unique")
        if _polygon_area(self.footprint_enu_m) <= 0.0:
            raise ValueError("region footprint must enclose positive area")
        if self.max_altitude_m <= self.min_altitude_m:
            raise ValueError("region max_altitude_m must exceed min_altitude_m")
        return self


class RegionSpec(StrictModel):
    region_id: Identifier
    kind: RegionKind
    geometry: RegionGeometry
    communications_shadow_attenuation_db: Annotated[float, Field(gt=0)] | None

    @field_validator("communications_shadow_attenuation_db")
    @classmethod
    def finite_attenuation(cls, value: float | None) -> float | None:
        if value is None:
            return value
        return _require_finite("communications shadow attenuation", value)

    @model_validator(mode="after")
    def kind_specific_attenuation(self) -> "RegionSpec":
        if self.kind == "communications_shadow":
            if self.communications_shadow_attenuation_db is None:
                raise ValueError(
                    "communications_shadow regions require communications_shadow_attenuation_db"
                )
        elif self.communications_shadow_attenuation_db is not None:
            raise ValueError(
                "geofence and no_fly regions must not declare communications shadow attenuation"
            )
        return self


class LaunchSiteSpec(StrictModel):
    launch_site_id: Identifier
    primary_uav_entity_id: Identifier
    allowed_uav_entity_ids: tuple[Identifier, ...] = Field(min_length=1)
    pose: WorldPose
    pad_radius_m: Annotated[float, Field(gt=0)]

    @field_validator("pad_radius_m")
    @classmethod
    def finite_radius(cls, value: float) -> float:
        return _require_finite("launch site radius", value)

    @model_validator(mode="after")
    def unique_uavs(self) -> "LaunchSiteSpec":
        _require_unique(
            "launch site allowed_uav_entity_id",
            list(self.allowed_uav_entity_ids),
        )
        if self.primary_uav_entity_id not in self.allowed_uav_entity_ids:
            raise ValueError(
                "launch site primary_uav_entity_id must be included in allowed_uav_entity_ids"
            )
        return self


class EntitySpec(StrictModel):
    entity_id: Identifier
    kind: EntityKind
    provider_id: Identifier | None
    authority_kind: AuthorityKind
    state: Literal["static", "dynamic"]
    model_asset_id: Identifier
    pose: WorldPose

    @model_validator(mode="after")
    def state_and_authority_match(self) -> "EntitySpec":
        allowed = ALLOWED_AUTHORITY_KINDS.get((self.kind, self.state))
        if allowed is None or self.authority_kind not in allowed:
            raise ValueError(
                f"entity kind/state {(self.kind, self.state)!r} accepts authority kinds "
                f"{sorted(allowed or [])}, not {self.authority_kind!r}"
            )
        if self.state == "static" and self.provider_id is not None:
            raise ValueError("static entities are owned by the scenario compiler")
        if self.state == "dynamic" and self.provider_id is None:
            raise ValueError("dynamic entities require an owning provider_id")
        return self


class SensorSpec(StrictModel):
    sensor_id: Identifier
    provider_id: Identifier
    parent_entity_id: Identifier
    kind: SensorKind
    pose: ParentRelativePose
    horizontal_fov_deg: Annotated[float, Field(gt=0, lt=180)]
    vertical_fov_deg: Annotated[float, Field(gt=0, lt=180)]
    resolution_width_px: Annotated[int, Field(gt=0)]
    resolution_height_px: Annotated[int, Field(gt=0)]

    @field_validator("horizontal_fov_deg", "vertical_fov_deg")
    @classmethod
    def finite_fov(cls, value: float) -> float:
        return _require_finite("sensor field of view", value)


class TargetGeometry(StrictModel):
    shape: Literal["box"]
    size_x_m: Annotated[float, Field(gt=0)]
    size_y_m: Annotated[float, Field(gt=0)]
    size_z_m: Annotated[float, Field(gt=0)]

    @field_validator("size_x_m", "size_y_m", "size_z_m")
    @classmethod
    def finite_size(cls, value: float) -> float:
        return _require_finite("target geometry", value)


class TargetSurfaceNormal(StrictModel):
    x: float
    y: float
    z: float

    @field_validator("x", "y", "z")
    @classmethod
    def finite_component(cls, value: float) -> float:
        return _require_finite("target surface normal", value)

    @model_validator(mode="after")
    def canonical_box_axis(self) -> "TargetSurfaceNormal":
        components = (self.x, self.y, self.z)
        if sum(abs(abs(component) - 1.0) <= 1e-12 for component in components) != 1:
            raise ValueError("target surface normal must select one signed box axis")
        if sum(abs(component) <= 1e-12 for component in components) != 2:
            raise ValueError("target surface normal must select one signed box axis")
        return self


class SemanticTargetSpec(StrictModel):
    target_id: Identifier
    parent_entity_id: Identifier
    pose: ParentRelativePose
    geometry: TargetGeometry
    surface_normal_target: TargetSurfaceNormal
    required_sensor_id: Identifier
    min_distance_m: Annotated[float, Field(ge=0)]
    max_distance_m: Annotated[float, Field(gt=0)]
    max_view_angle_deg: Annotated[float, Field(gt=0, lt=180)]
    fov_margin_deg: Annotated[float, Field(ge=0)]
    dwell_time_s: Annotated[float, Field(gt=0)]
    required_evidence: tuple[EvidenceKind, ...] = Field(min_length=1)

    @field_validator(
        "min_distance_m",
        "max_distance_m",
        "max_view_angle_deg",
        "fov_margin_deg",
        "dwell_time_s",
    )
    @classmethod
    def finite_values(cls, value: float) -> float:
        return _require_finite("semantic target", value)

    @model_validator(mode="after")
    def valid_distances(self) -> "SemanticTargetSpec":
        if self.min_distance_m > self.max_distance_m:
            raise ValueError(
                "semantic target min_distance_m must not exceed max_distance_m"
            )
        _require_unique("semantic target evidence", list(self.required_evidence))
        return self


class WindVector(StrictModel):
    east_mps: float
    north_mps: float
    up_mps: float

    @field_validator("east_mps", "north_mps", "up_mps")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("wind vector", value)


class WeatherSample(StrictModel):
    sample_id: Identifier
    mode: Literal["deterministic_constant"]
    wind: WindVector
    visibility_m: Annotated[float, Field(gt=0)]
    precipitation: PrecipitationKind
    precipitation_rate_mm_per_h: Annotated[float, Field(ge=0)]
    temperature_c: float
    pressure_pa: Annotated[float, Field(gt=0)]

    @field_validator("visibility_m", "temperature_c", "pressure_pa")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("weather sample", value)

    @model_validator(mode="after")
    def precipitation_consistency(self) -> "WeatherSample":
        if (self.precipitation == "none") != (self.precipitation_rate_mm_per_h == 0.0):
            raise ValueError(
                "precipitation 'none' requires rate 0 and a positive rate requires "
                "a precipitation kind"
            )
        return self


class RigidOffset(StrictModel):
    x_m: float
    y_m: float
    z_m: float
    qw: float
    qx: float
    qy: float
    qz: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite_translation(cls, value: float) -> float:
        return _require_finite("rigid offset", value)

    @model_validator(mode="after")
    def unit_quaternion(self) -> "RigidOffset":
        _validate_unit_quaternion(
            qw=self.qw,
            qx=self.qx,
            qy=self.qy,
            qz=self.qz,
        )
        return self


class EngineFrameBinding(StrictModel):
    binding_id: Identifier
    provider_id: Identifier
    engine: EngineKind
    scene_asset_id: Identifier | None
    source_frame_id: Annotated[str, Field(min_length=1, max_length=64)]
    target_frame_id: Annotated[str, Field(min_length=1, max_length=64)]
    transform: RigidOffset

    @field_validator("source_frame_id", "target_frame_id")
    @classmethod
    def normalized_frame_id(cls, value: str) -> str:
        return _validate_frame_id(value)

    @model_validator(mode="after")
    def distinct_frames(self) -> "EngineFrameBinding":
        if self.source_frame_id == self.target_frame_id:
            raise ValueError(
                "engine frame binding source_frame_id must differ from target_frame_id"
            )
        if self.engine == "gazebo" and self.scene_asset_id is None:
            raise ValueError("Gazebo frame bindings require scene_asset_id")
        if self.engine == "sumo" and self.scene_asset_id is not None:
            raise ValueError("SUMO frame bindings cannot duplicate a scene asset")
        return self


class ProviderRequirement(StrictModel):
    provider_id: Identifier
    roles: tuple[ScenarioRoleKind, ...] = Field(min_length=1)
    required_capability_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_and_sorted(self) -> "ProviderRequirement":
        role_values = list(self.roles)
        capability_values = list(self.required_capability_ids)
        _require_unique("provider requirement role", role_values)
        _require_unique("provider requirement capability_id", capability_values)
        if role_values != sorted(role_values):
            raise ValueError("provider requirement roles must be sorted")
        if capability_values != sorted(capability_values):
            raise ValueError(
                "provider requirement required_capability_ids must be sorted"
            )
        return self


class SumoEntityBinding(StrictModel):
    sumo_object_id: str
    entity_id: Identifier
    kind: SumoObjectKind

    @field_validator("sumo_object_id")
    @classmethod
    def valid_object_id(cls, value: str) -> str:
        if value.strip() != value or _SUMO_OBJECT_ID_RE.fullmatch(value) is None:
            raise ValueError("sumo_object_id must be a normalized SUMO object token")
        return value


class SumoConfiguration(StrictModel):
    provider_id: Identifier
    config_asset_id: Identifier
    network_asset_id: Identifier
    routes_asset_id: Identifier
    additional_asset_id: Identifier
    frame_binding_id: Identifier
    object_bindings: tuple[SumoEntityBinding, ...]

    @model_validator(mode="after")
    def unique_declarations(self) -> "SumoConfiguration":
        _require_unique(
            "SUMO asset reference",
            [
                self.config_asset_id,
                self.network_asset_id,
                self.routes_asset_id,
                self.additional_asset_id,
            ],
        )
        _require_unique(
            "SUMO object id",
            [binding.sumo_object_id for binding in self.object_bindings],
        )
        _require_unique(
            "SUMO bound entity_id",
            [binding.entity_id for binding in self.object_bindings],
        )
        return self


class WirelessRadioProfile(StrictModel):
    radio_profile_id: Identifier
    provider_id: Identifier
    wifi_standard: WifiStandardKind
    frequency_ghz: Annotated[float, Field(gt=0)]
    channel_width_mhz: Annotated[float, Field(gt=0)]
    tx_power_dbm: float
    rx_sensitivity_dbm: float

    @field_validator(
        "frequency_ghz",
        "channel_width_mhz",
        "tx_power_dbm",
        "rx_sensitivity_dbm",
    )
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("wireless radio profile", value)


class NetworkNodeBinding(StrictModel):
    node_id: Identifier
    entity_id: Identifier
    endpoint_id: Identifier
    radio_profile_id: Identifier


class NetworkLinkBinding(StrictModel):
    link_id: Identifier
    source_node_id: Identifier
    destination_node_id: Identifier
    data_rate_bps: Annotated[int, Field(gt=0)]
    propagation_delay_ns: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def endpoints_are_distinct(self) -> "NetworkLinkBinding":
        if self.source_node_id == self.destination_node_id:
            raise ValueError("network links require distinct endpoint nodes")
        return self


class NetworkConfiguration(StrictModel):
    provider_id: Identifier
    radio_profiles: tuple[WirelessRadioProfile, ...] = Field(min_length=1)
    node_bindings: tuple[NetworkNodeBinding, ...] = Field(min_length=1)
    links: tuple[NetworkLinkBinding, ...]

    @model_validator(mode="after")
    def unique_declarations(self) -> "NetworkConfiguration":
        _require_unique(
            "network radio profile id",
            [profile.radio_profile_id for profile in self.radio_profiles],
        )
        _require_unique(
            "network radio profile provider_id",
            [
                f"{profile.radio_profile_id}:{profile.provider_id}"
                for profile in self.radio_profiles
            ],
        )
        _require_unique(
            "network node id",
            [binding.node_id for binding in self.node_bindings],
        )
        _require_unique(
            "network endpoint id",
            [binding.endpoint_id for binding in self.node_bindings],
        )
        _require_unique(
            "network node entity_id",
            [binding.entity_id for binding in self.node_bindings],
        )
        _require_unique("network link id", [link.link_id for link in self.links])
        node_ids = {binding.node_id for binding in self.node_bindings}
        if any(
            link.source_node_id not in node_ids
            or link.destination_node_id not in node_ids
            for link in self.links
        ):
            raise ValueError("network links must reference declared node bindings")
        return self


class MissionRequirement(StrictModel):
    requirement_id: Identifier
    kind: MissionRequirementKind
    dependencies: tuple[Identifier, ...]
    launch_site_id: Identifier | None
    target_id: Identifier | None
    expected_public_asset_id: Identifier | None

    @model_validator(mode="after")
    def kind_specific_fields(self) -> "MissionRequirement":
        _require_unique("mission dependency", list(self.dependencies))
        if self.requirement_id in self.dependencies:
            raise ValueError("mission requirement cannot depend on itself")
        if self.kind in {"takeoff", "return_to_launch", "land"}:
            if self.launch_site_id is None:
                raise ValueError(
                    f"{self.kind} mission requirements require launch_site_id"
                )
            if self.target_id is not None or self.expected_public_asset_id is not None:
                raise ValueError(
                    f"{self.kind} mission requirements only allow launch_site_id"
                )
        elif self.kind == "observation":
            if self.target_id is None:
                raise ValueError("observation mission requirements require target_id")
            if (
                self.launch_site_id is not None
                or self.expected_public_asset_id is not None
            ):
                raise ValueError(
                    "observation mission requirements only allow target_id"
                )
        else:
            if self.expected_public_asset_id is None:
                raise ValueError(
                    "upload_or_buffer mission requirements require expected_public_asset_id"
                )
            if self.launch_site_id is not None or self.target_id is not None:
                raise ValueError(
                    "upload_or_buffer mission requirements only allow expected_public_asset_id"
                )
        return self


class ExpectedPublicAsset(StrictModel):
    asset_id: Identifier
    kind: ExpectedPublicAssetKind
    producer_requirement_id: Identifier
    media_type: Annotated[str, Field(min_length=3, max_length=128)]
    units: tuple[UnitDeclaration, ...] = Field(min_length=1)
    precision: tuple[PrecisionMetadata, ...] = Field(min_length=1)

    @field_validator("media_type")
    @classmethod
    def valid_media_type(cls, value: str) -> str:
        if _MEDIA_TYPE_RE.fullmatch(value) is None:
            raise ValueError("media_type must be a normalized MIME type")
        return value

    @model_validator(mode="after")
    def precision_matches_units(self) -> "ExpectedPublicAsset":
        _validate_measurement_metadata(
            units=self.units,
            precision=self.precision,
            label="expected public asset",
        )
        return self


class WorldPackageContent(StrictModel):
    schema_version: Literal["aero-bench.world/v2"]
    world_id: Identifier
    frame: WorldFrame
    assets: tuple[AssetRecord, ...] = Field(min_length=1)
    provider_requirements: tuple[ProviderRequirement, ...]
    base_layers: tuple[ViewerLayerSource, ...]
    layers: tuple[PublicLayer, ...]
    buildings: tuple[BuildingSpec, ...]
    roads: tuple[RoadSpec, ...]
    regions: tuple[RegionSpec, ...]
    launch_sites: tuple[LaunchSiteSpec, ...]
    entities: tuple[EntitySpec, ...] = Field(min_length=1)
    sensors: tuple[SensorSpec, ...]
    semantic_targets: tuple[SemanticTargetSpec, ...]
    weather: tuple[WeatherSample, ...] = Field(min_length=1)
    engine_frame_bindings: tuple[EngineFrameBinding, ...]
    sumo: SumoConfiguration | None
    network: NetworkConfiguration | None
    mission_requirements: tuple[MissionRequirement, ...]
    expected_public_assets: tuple[ExpectedPublicAsset, ...]

    @model_validator(mode="after")
    def content_is_consistent(self) -> "WorldPackageContent":
        asset_ids = [asset.artifact.artifact_id for asset in self.assets]
        _require_unique("asset_id", asset_ids)
        asset_selector_paths = [
            asset.artifact.selector.partition("#")[0] for asset in self.assets
        ]
        _require_unique("asset selector file", asset_selector_paths)
        license_selector_paths = {
            asset.license_file.selector.partition("#")[0] for asset in self.assets
        }
        if set(asset_selector_paths) & license_selector_paths:
            raise ValueError(
                "asset payload and license selectors must reference disjoint files"
            )
        provider_ids = [
            requirement.provider_id for requirement in self.provider_requirements
        ]
        _require_unique("provider requirement provider_id", provider_ids)
        if provider_ids != sorted(provider_ids):
            raise ValueError("provider_requirements must be sorted by provider_id")
        _require_unique("base layer id", [layer.layer_id for layer in self.base_layers])
        _require_unique("layer id", [layer.layer_id for layer in self.layers])
        _require_unique(
            "building id", [building.building_id for building in self.buildings]
        )
        _require_unique(
            "building entity_id", [building.entity_id for building in self.buildings]
        )
        _require_unique("road id", [road.road_id for road in self.roads])
        _require_unique("region id", [region.region_id for region in self.regions])
        _require_unique(
            "launch site id",
            [launch_site.launch_site_id for launch_site in self.launch_sites],
        )
        _require_unique("entity id", [entity.entity_id for entity in self.entities])
        _require_unique("sensor id", [sensor.sensor_id for sensor in self.sensors])
        _require_unique(
            "target id", [target.target_id for target in self.semantic_targets]
        )
        _require_unique(
            "weather sample id", [sample.sample_id for sample in self.weather]
        )
        _require_unique(
            "engine frame binding id",
            [binding.binding_id for binding in self.engine_frame_bindings],
        )
        _require_unique(
            "mission requirement id",
            [requirement.requirement_id for requirement in self.mission_requirements],
        )
        _require_unique(
            "expected public asset id",
            [asset.asset_id for asset in self.expected_public_assets],
        )

        asset_map = {asset.artifact.artifact_id: asset for asset in self.assets}
        entity_map = {entity.entity_id: entity for entity in self.entities}
        sensor_map = {sensor.sensor_id: sensor for sensor in self.sensors}
        launch_site_map = {
            launch_site.launch_site_id: launch_site for launch_site in self.launch_sites
        }
        target_map = {target.target_id: target for target in self.semantic_targets}
        mission_map = {
            requirement.requirement_id: requirement
            for requirement in self.mission_requirements
        }
        binding_map = {
            binding.binding_id: binding for binding in self.engine_frame_bindings
        }
        provider_requirement_map = {
            requirement.provider_id: requirement
            for requirement in self.provider_requirements
        }
        radio_profile_map = (
            {
                profile.radio_profile_id: profile
                for profile in self.network.radio_profiles
            }
            if self.network is not None
            else {}
        )

        def require_provider_role(
            provider_id: str, role: ScenarioRoleKind, label: str
        ) -> None:
            requirement = provider_requirement_map.get(provider_id)
            if requirement is None:
                raise ValueError(
                    f"{label} provider_id must reference provider_requirements"
                )
            if role not in requirement.roles:
                raise ValueError(
                    f"{label} provider_id must declare provider role {role}"
                )

        for asset in self.assets:
            for audience in asset.audiences:
                if (
                    audience.audience_kind == "provider"
                    and audience.audience_id not in provider_requirement_map
                ):
                    raise ValueError(
                        "asset provider audience must reference provider_requirements"
                    )
                if audience.audience_kind == "entity":
                    entity = entity_map.get(audience.audience_id)
                    if entity is None:
                        raise ValueError(
                            "asset entity audience must reference a declared entity"
                        )
                    if entity.state != "dynamic" or entity.provider_id is None:
                        raise ValueError(
                            "asset entity audience requires a provider-owned dynamic entity"
                        )

        for layer in self.base_layers:
            asset = asset_map.get(layer.asset_id)
            if asset is None:
                raise ValueError(
                    f"base layer {layer.layer_id} references unknown asset_id"
                )
            if asset.visibility != "public":
                raise ValueError("base layers must reference public assets")
            expected_role: AssetRole = (
                "imagery_tiles" if layer.kind == "imagery" else "terrain_tiles"
            )
            if asset.asset_role != expected_role:
                raise ValueError("base layer asset_role does not match base layer kind")

        for layer in self.layers:
            asset = asset_map.get(layer.asset_id)
            if asset is None:
                raise ValueError(f"layer {layer.layer_id} references unknown asset_id")
            if asset.visibility != "public":
                raise ValueError("public layers must reference public assets")
            if asset.asset_role != "layer_tiles":
                raise ValueError("public layers must reference layer_tiles assets")

        osm_layers = [layer for layer in self.layers if layer.kind == "osm_scene"]
        if len(osm_layers) > 1:
            raise ValueError("world must declare at most one osm_scene layer")
        for layer in osm_layers:
            asset = asset_map[layer.asset_id]
            if asset.source_frame != "WGS84" or asset.media_type != "application/json":
                raise ValueError("osm_scene requires a WGS84 application/json asset")

        mesh_layers = [layer for layer in self.layers if layer.kind == "osm_mesh"]
        if len(mesh_layers) > 1:
            raise ValueError("world must declare at most one osm_mesh layer")
        for layer in mesh_layers:
            asset = asset_map[layer.asset_id]
            if asset.source_frame != "asset_local" or asset.media_type != "application/json":
                raise ValueError("osm_mesh requires an asset_local application/json manifest")
            if len(osm_layers) != 1:
                raise ValueError("osm_mesh requires its source osm_scene layer")

        geoid_asset = asset_map.get(self.frame.vertical_datum.geoid_correction_asset_id)
        terrain_asset = asset_map.get(self.frame.vertical_datum.terrain_height_asset_id)
        if geoid_asset is None or geoid_asset.asset_role != "geoid_model":
            raise ValueError(
                "vertical datum geoid_correction_asset_id must reference a geoid_model asset"
            )
        if terrain_asset is None or terrain_asset.asset_role != "terrain_model":
            raise ValueError(
                "vertical datum terrain_height_asset_id must reference a terrain_model asset"
            )

        for building in self.buildings:
            for field_name, asset_id, expected_role in (
                ("source_asset_id", building.source_asset_id, "building_source"),
                ("render_asset_id", building.render_asset_id, "building_render"),
                (
                    "collision_asset_id",
                    building.collision_asset_id,
                    "building_collision",
                ),
            ):
                asset = asset_map.get(asset_id)
                if asset is None or asset.asset_role != expected_role:
                    raise ValueError(
                        f"building {building.building_id} {field_name} must reference {expected_role}"
                    )
            entity = entity_map.get(building.entity_id)
            if (
                entity is None
                or entity.kind != "static_asset"
                or entity.state != "static"
                or entity.authority_kind != "scenario_static"
                or entity.provider_id is not None
            ):
                raise ValueError(
                    "building entity_id must reference a compiler-owned static entity"
                )
            if entity.model_asset_id != building.render_asset_id:
                raise ValueError(
                    "building entity_id must point to a static entity that uses the building render asset"
                )

        for launch_site in self.launch_sites:
            for entity_id in launch_site.allowed_uav_entity_ids:
                entity = entity_map.get(entity_id)
                if entity is None or entity.kind != "uav" or entity.state != "dynamic":
                    raise ValueError(
                        "launch sites must reference declared dynamic uav entities"
                    )
            primary_entity = entity_map.get(launch_site.primary_uav_entity_id)
            if (
                primary_entity is None
                or primary_entity.kind != "uav"
                or primary_entity.state != "dynamic"
            ):
                raise ValueError(
                    "launch sites primary_uav_entity_id must reference a declared dynamic uav entity"
                )

        for entity in self.entities:
            asset = asset_map.get(entity.model_asset_id)
            if asset is None:
                raise ValueError("entities must reference declared assets")
            expected_roles: frozenset[AssetRole]
            if entity.kind == "static_asset" and entity.authority_kind == "scenario_static":
                expected_roles = frozenset({"entity_model", "building_render"})
            else:
                expected_roles = frozenset({"entity_model"})
            if asset.asset_role not in expected_roles:
                raise ValueError("entities must reference valid model assets")
            if entity.state == "dynamic" and not any(
                audience.audience_kind == "public"
                or (
                    audience.audience_kind == "provider"
                    and audience.audience_id == entity.provider_id
                )
                or (
                    audience.audience_kind == "entity"
                    and audience.audience_id == entity.entity_id
                )
                for audience in asset.audiences
            ):
                raise ValueError(
                    "dynamic entity model asset must authorize its owning provider"
                )
            if entity.authority_kind == "gazebo_physics":
                if entity.provider_id is None:
                    raise ValueError("gazebo entities require provider_id")
                require_provider_role(
                    entity.provider_id,
                    "motion",
                    f"entity {entity.entity_id}",
                )
            elif entity.authority_kind == "sumo_traffic":
                if entity.provider_id is None:
                    raise ValueError("SUMO entities require provider_id")
                require_provider_role(
                    entity.provider_id,
                    "traffic",
                    f"entity {entity.entity_id}",
                )

        for sensor in self.sensors:
            if sensor.parent_entity_id not in entity_map:
                raise ValueError(
                    "sensor parent_entity_id must reference a declared entity"
                )
            require_provider_role(
                sensor.provider_id,
                "sensor",
                f"sensor {sensor.sensor_id}",
            )

        for target in self.semantic_targets:
            parent = entity_map.get(target.parent_entity_id)
            if (
                parent is None
                or parent.kind != "static_asset"
                or parent.state != "static"
                or parent.authority_kind != "scenario_static"
                or parent.provider_id is not None
            ):
                raise ValueError(
                    "semantic target parent_entity_id must reference a declared static entity"
                )
            sensor = sensor_map.get(target.required_sensor_id)
            if sensor is None:
                raise ValueError(
                    "semantic target required_sensor_id must reference a declared sensor"
                )
            half_fov = min(sensor.horizontal_fov_deg, sensor.vertical_fov_deg) * 0.5
            if target.max_view_angle_deg + target.fov_margin_deg >= half_fov:
                raise ValueError(
                    "semantic target max_view_angle_deg plus fov_margin_deg must fit inside the sensor FOV"
                )

        if any(
            entity.authority_kind == "gazebo_physics" for entity in self.entities
        ) and not any(
            binding.engine == "gazebo" for binding in self.engine_frame_bindings
        ):
            raise ValueError(
                "gazebo_physics entities require declared Gazebo engine frame bindings"
            )
        for binding in self.engine_frame_bindings:
            expected_role = "motion" if binding.engine == "gazebo" else "traffic"
            require_provider_role(
                binding.provider_id,
                expected_role,
                f"engine frame binding {binding.binding_id}",
            )
            if binding.engine == "gazebo":
                scene_asset = asset_map.get(binding.scene_asset_id)
                if scene_asset is None or scene_asset.asset_role != "other":
                    raise ValueError(
                        "Gazebo scene_asset_id must reference an 'other' world asset"
                    )
                if not any(
                    audience.audience_kind == "provider"
                    and audience.audience_id == binding.provider_id
                    for audience in scene_asset.audiences
                ):
                    raise ValueError(
                        "Gazebo scene asset must authorize its motion provider"
                    )

        sumo_bindings = [
            binding
            for binding in self.engine_frame_bindings
            if binding.engine == "sumo"
        ]
        sumo_entities = {
            entity.entity_id
            for entity in self.entities
            if entity.authority_kind == "sumo_traffic"
        }
        if self.sumo is None:
            if sumo_bindings or sumo_entities:
                raise ValueError(
                    "SUMO frame bindings and entities require a sumo configuration"
                )
        else:
            sumo_enu_bindings = [
                binding
                for binding in sumo_bindings
                if binding.source_frame_id == "sumo_net"
                and binding.target_frame_id == "ENU"
            ]
            if len(sumo_enu_bindings) != 1:
                raise ValueError(
                    "enabled SUMO requires exactly one frame binding with "
                    "source_frame_id 'sumo_net' and target_frame_id 'ENU'"
                )
            if any(
                binding.source_frame_id == "sumo_net"
                or binding.target_frame_id == "ENU"
                for binding in sumo_bindings
                if binding not in sumo_enu_bindings
            ):
                raise ValueError(
                    "SUMO frame bindings may reference 'sumo_net' or 'ENU' only "
                    "through the declared sumo_net->ENU binding"
                )
            sumo_binding = binding_map.get(self.sumo.frame_binding_id)
            if sumo_binding is None or sumo_binding not in sumo_enu_bindings:
                raise ValueError(
                    "sumo.frame_binding_id must reference the declared "
                    "SUMO-to-ENU frame binding"
                )
            require_provider_role(self.sumo.provider_id, "traffic", "sumo")
            if sumo_binding.provider_id != self.sumo.provider_id:
                raise ValueError(
                    "sumo.frame_binding_id must reference an engine frame binding "
                    "owned by sumo.provider_id"
                )

            for field_name, asset_id, expected_role in (
                ("config_asset_id", self.sumo.config_asset_id, "sumo_config"),
                ("network_asset_id", self.sumo.network_asset_id, "sumo_network"),
                ("routes_asset_id", self.sumo.routes_asset_id, "sumo_routes"),
                (
                    "additional_asset_id",
                    self.sumo.additional_asset_id,
                    "sumo_additional",
                ),
            ):
                asset = asset_map.get(asset_id)
                if asset is None or asset.asset_role != expected_role:
                    raise ValueError(f"SUMO {field_name} must reference {expected_role}")
                if not any(
                    audience.audience_kind == "public"
                    or (
                        audience.audience_kind == "provider"
                        and audience.audience_id == self.sumo.provider_id
                    )
                    for audience in asset.audiences
                ):
                    raise ValueError(
                        f"SUMO {field_name} must authorize sumo.provider_id"
                    )

            bound_sumo_entities = {
                binding.entity_id for binding in self.sumo.object_bindings
            }
            if bound_sumo_entities != sumo_entities:
                raise ValueError(
                    "SUMO object bindings must cover exactly the declared "
                    "sumo_traffic entities"
                )
            for binding in self.sumo.object_bindings:
                entity = entity_map.get(binding.entity_id)
                if entity is None:
                    raise ValueError(
                        "SUMO object bindings must reference declared entities"
                    )
                if binding.kind == "vehicle":
                    if (
                        entity.kind != "ugv"
                        or entity.state != "dynamic"
                        or entity.authority_kind != "sumo_traffic"
                    ):
                        raise ValueError(
                            "SUMO vehicle bindings must reference dynamic ugv entities"
                        )
                elif (
                    entity.kind != "pedestrian"
                    or entity.state != "dynamic"
                    or entity.authority_kind != "sumo_traffic"
                ):
                    raise ValueError(
                        "SUMO person bindings must reference dynamic pedestrian entities"
                    )
                if entity.provider_id != self.sumo.provider_id:
                    raise ValueError(
                        "SUMO object bindings must reference entities owned by "
                        "sumo.provider_id"
                    )

        if self.network is not None:
            require_provider_role(
                self.network.provider_id, "wireless_network", "network"
            )
            for profile in self.network.radio_profiles:
                if profile.provider_id != self.network.provider_id:
                    raise ValueError(
                        "network radio profiles must use the declared "
                        "network.provider_id"
                    )
            for binding in self.network.node_bindings:
                if binding.entity_id not in entity_map:
                    raise ValueError(
                        "network node bindings must reference declared entities"
                    )
                profile = radio_profile_map.get(binding.radio_profile_id)
                if profile is None:
                    raise ValueError(
                        "network node bindings must reference declared radio profiles"
                    )

        expected_public_asset_ids = {
            asset.asset_id for asset in self.expected_public_assets
        }
        if expected_public_asset_ids & set(asset_map):
            raise ValueError(
                "expected_public_assets must not reuse source asset manifest IDs"
            )

        for requirement in self.mission_requirements:
            for dependency in requirement.dependencies:
                if dependency not in mission_map:
                    raise ValueError(
                        "mission dependencies must reference declared requirements"
                    )
            if (
                requirement.kind in {"takeoff", "return_to_launch", "land"}
                and requirement.launch_site_id not in launch_site_map
            ):
                raise ValueError(
                    f"{requirement.kind} mission requirements must reference declared launch sites"
                )
            if (
                requirement.kind == "observation"
                and requirement.target_id not in target_map
            ):
                raise ValueError(
                    "observation mission requirements must reference declared targets"
                )
            if (
                requirement.kind == "upload_or_buffer"
                and requirement.expected_public_asset_id
                not in expected_public_asset_ids
            ):
                raise ValueError(
                    "upload_or_buffer mission requirements must reference declared expected_public_assets"
                )

        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(requirement_id: str) -> None:
            if requirement_id in visited:
                return
            if requirement_id in visiting:
                raise ValueError(
                    "mission requirements must form an acyclic dependency DAG"
                )
            visiting.add(requirement_id)
            requirement = mission_map[requirement_id]
            for dependency in requirement.dependencies:
                visit(dependency)
            visiting.remove(requirement_id)
            visited.add(requirement_id)

        for requirement_id in mission_map:
            visit(requirement_id)

        for expected_asset in self.expected_public_assets:
            requirement = mission_map.get(expected_asset.producer_requirement_id)
            if requirement is None or requirement.kind != "upload_or_buffer":
                raise ValueError(
                    "expected_public_assets producer_requirement_id must reference an upload_or_buffer mission requirement"
                )
            if requirement.expected_public_asset_id != expected_asset.asset_id:
                raise ValueError(
                    "expected_public_assets must match the referenced upload_or_buffer mission requirement"
                )

        bounds = self.frame.spatial_extent
        for entity in self.entities:
            _validate_pose_in_bounds(
                pose=entity.pose,
                bounds=bounds,
                label=f"entity {entity.entity_id} pose",
            )
        for launch_site in self.launch_sites:
            _validate_pose_in_bounds(
                pose=launch_site.pose,
                bounds=bounds,
                label=f"launch site {launch_site.launch_site_id} pose",
            )
        for building in self.buildings:
            for index, point in enumerate(building.geometry.footprint_enu_m):
                _validate_point_in_bounds(
                    point=point,
                    bounds=bounds,
                    label=f"building {building.building_id} footprint[{index}]",
                )
            if building.geometry.height_reference == "enu_up":
                _validate_altitude_in_bounds(
                    altitude_m=building.geometry.base_altitude_m,
                    bounds=bounds,
                    label=f"building {building.building_id} base_altitude_m",
                )
                _validate_altitude_in_bounds(
                    altitude_m=building.geometry.top_altitude_m,
                    bounds=bounds,
                    label=f"building {building.building_id} top_altitude_m",
                )
        for road in self.roads:
            for index, point in enumerate(road.centerline_enu_m):
                _validate_point_in_bounds(
                    point=point,
                    bounds=bounds,
                    label=f"road {road.road_id} centerline[{index}]",
                )
        for region in self.regions:
            for index, point in enumerate(region.geometry.footprint_enu_m):
                _validate_point_in_bounds(
                    point=point,
                    bounds=bounds,
                    label=f"region {region.region_id} footprint[{index}]",
                )
            if region.geometry.height_reference == "enu_up":
                _validate_altitude_in_bounds(
                    altitude_m=region.geometry.min_altitude_m,
                    bounds=bounds,
                    label=f"region {region.region_id} min_altitude_m",
                )
                _validate_altitude_in_bounds(
                    altitude_m=region.geometry.max_altitude_m,
                    bounds=bounds,
                    label=f"region {region.region_id} max_altitude_m",
                )
        return self


def _content_document(content: WorldPackageContent) -> dict[str, object]:
    return content.model_dump(mode="json")


def _asset_manifest_document(content: WorldPackageContent) -> list[dict[str, object]]:
    return [
        asset.model_dump(mode="json")
        for asset in sorted(
            content.assets,
            key=lambda asset: asset.artifact.artifact_id,
        )
    ]


def world_digest_value(content: WorldPackageContent) -> str:
    """SHA-256 over the canonical JSON of the full ordered world content."""

    return hashlib.sha256(canonical_json_bytes(_content_document(content))).hexdigest()


def asset_digest_value(content: WorldPackageContent) -> str:
    """SHA-256 over the canonical JSON of the canonical asset manifest."""

    return hashlib.sha256(
        canonical_json_bytes(_asset_manifest_document(content))
    ).hexdigest()


class WorldPackage(StrictModel):
    schema_version: Literal["aero-bench.world/v2"]
    world_id: Identifier
    frame: WorldFrame
    assets: tuple[AssetRecord, ...] = Field(min_length=1)
    provider_requirements: tuple[ProviderRequirement, ...]
    base_layers: tuple[ViewerLayerSource, ...]
    layers: tuple[PublicLayer, ...]
    buildings: tuple[BuildingSpec, ...]
    roads: tuple[RoadSpec, ...]
    regions: tuple[RegionSpec, ...]
    launch_sites: tuple[LaunchSiteSpec, ...]
    entities: tuple[EntitySpec, ...] = Field(min_length=1)
    sensors: tuple[SensorSpec, ...]
    semantic_targets: tuple[SemanticTargetSpec, ...]
    weather: tuple[WeatherSample, ...] = Field(min_length=1)
    engine_frame_bindings: tuple[EngineFrameBinding, ...]
    sumo: SumoConfiguration | None
    network: NetworkConfiguration | None
    mission_requirements: tuple[MissionRequirement, ...]
    expected_public_assets: tuple[ExpectedPublicAsset, ...]
    asset_digest: Sha256
    world_digest: Sha256

    @field_validator("asset_digest", "world_digest")
    @classmethod
    def real_digest(cls, value: str) -> str:
        return _require_real_digest(value)

    @model_validator(mode="after")
    def digests_match_content(self) -> "WorldPackage":
        content = WorldPackageContent(
            schema_version=self.schema_version,
            world_id=self.world_id,
            frame=self.frame,
            assets=self.assets,
            provider_requirements=self.provider_requirements,
            base_layers=self.base_layers,
            layers=self.layers,
            buildings=self.buildings,
            roads=self.roads,
            regions=self.regions,
            launch_sites=self.launch_sites,
            entities=self.entities,
            sensors=self.sensors,
            semantic_targets=self.semantic_targets,
            weather=self.weather,
            engine_frame_bindings=self.engine_frame_bindings,
            sumo=self.sumo,
            network=self.network,
            mission_requirements=self.mission_requirements,
            expected_public_assets=self.expected_public_assets,
        )
        for label, declared, computed in (
            ("asset_digest", self.asset_digest, asset_digest_value(content)),
            ("world_digest", self.world_digest, world_digest_value(content)),
        ):
            if declared != computed:
                raise ValueError(f"{label} does not match the declared world content")
        return self


def world_package(
    *,
    world_id: str,
    frame: WorldFrame,
    assets: tuple[AssetRecord, ...],
    provider_requirements: tuple[ProviderRequirement, ...],
    base_layers: tuple[ViewerLayerSource, ...],
    layers: tuple[PublicLayer, ...],
    buildings: tuple[BuildingSpec, ...],
    roads: tuple[RoadSpec, ...],
    regions: tuple[RegionSpec, ...],
    launch_sites: tuple[LaunchSiteSpec, ...],
    entities: tuple[EntitySpec, ...],
    sensors: tuple[SensorSpec, ...],
    semantic_targets: tuple[SemanticTargetSpec, ...],
    weather: tuple[WeatherSample, ...],
    engine_frame_bindings: tuple[EngineFrameBinding, ...],
    sumo: SumoConfiguration | None,
    network: NetworkConfiguration | None,
    mission_requirements: tuple[MissionRequirement, ...],
    expected_public_assets: tuple[ExpectedPublicAsset, ...],
) -> WorldPackage:
    """Construct a sealed `WorldPackage`, computing both mandatory digests."""

    content = WorldPackageContent(
        schema_version=WORLD_SCHEMA_VERSION,
        world_id=world_id,
        frame=frame,
        assets=assets,
        provider_requirements=provider_requirements,
        base_layers=base_layers,
        layers=layers,
        buildings=buildings,
        roads=roads,
        regions=regions,
        launch_sites=launch_sites,
        entities=entities,
        sensors=sensors,
        semantic_targets=semantic_targets,
        weather=weather,
        engine_frame_bindings=engine_frame_bindings,
        sumo=sumo,
        network=network,
        mission_requirements=mission_requirements,
        expected_public_assets=expected_public_assets,
    )
    return WorldPackage(
        schema_version=content.schema_version,
        world_id=content.world_id,
        frame=content.frame,
        assets=content.assets,
        provider_requirements=content.provider_requirements,
        base_layers=content.base_layers,
        layers=content.layers,
        buildings=content.buildings,
        roads=content.roads,
        regions=content.regions,
        launch_sites=content.launch_sites,
        entities=content.entities,
        sensors=content.sensors,
        semantic_targets=content.semantic_targets,
        weather=content.weather,
        engine_frame_bindings=content.engine_frame_bindings,
        sumo=content.sumo,
        network=content.network,
        mission_requirements=content.mission_requirements,
        expected_public_assets=content.expected_public_assets,
        asset_digest=asset_digest_value(content),
        world_digest=world_digest_value(content),
    )


__all__ = [
    "ALLOWED_AUTHORITY_KINDS",
    "ArtifactSelector",
    "AssetAudience",
    "AssetProvenance",
    "AssetRecord",
    "AssetRole",
    "AuthorityKind",
    "BaseLayerKind",
    "BuildingGeometry",
    "BuildingSpec",
    "EngineFrameBinding",
    "EngineKind",
    "EnuPoint",
    "EntityKind",
    "EntitySpec",
    "EvidenceKind",
    "ExpectedPublicAsset",
    "ExpectedPublicAssetKind",
    "HeightReferenceKind",
    "InterpolationKind",
    "LayerKind",
    "LaunchSiteSpec",
    "MissionRequirement",
    "MissionRequirementKind",
    "NetworkLinkBinding",
    "NetworkNodeBinding",
    "ParentRelativePose",
    "PrecipitationKind",
    "ProviderRequirement",
    "PrecisionKind",
    "PrecisionMetadata",
    "PublicLayer",
    "RegionGeometry",
    "RegionKind",
    "RegionSpec",
    "RoadKind",
    "RoadSpec",
    "RigidOffset",
    "ScenarioRoleKind",
    "SensorKind",
    "SensorSpec",
    "SemanticTargetSpec",
    "SourceFrameKind",
    "SpatialBounds",
    "SumoConfiguration",
    "SumoEntityBinding",
    "SumoObjectKind",
    "TargetGeometry",
    "TargetSurfaceNormal",
    "UnitDeclaration",
    "VerticalDatumBinding",
    "ViewerLayerSource",
    "VisibilityKind",
    "WifiStandardKind",
    "WirelessRadioProfile",
    "WORLD_SCHEMA_VERSION",
    "WeatherSample",
    "WindVector",
    "WorldFrame",
    "WorldPackage",
    "WorldPackageContent",
    "WorldPose",
    "NetworkConfiguration",
    "asset_digest_value",
    "world_digest_value",
    "world_package",
]
