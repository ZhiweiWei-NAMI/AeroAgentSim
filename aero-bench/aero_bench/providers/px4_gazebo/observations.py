from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel


FLIGHT_TELEMETRY_OBSERVATION_SCHEMA = "aero-bench.observation.flight-telemetry/v1"
FLIGHT_GNSS_OBSERVATION_SCHEMA = "aero-bench.observation.flight-gnss/v1"
RGB_OBSERVATION_SCHEMA = "aero-bench.observation.rgb/v1"

FiniteFloat = Annotated[float, Field(strict=True)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
FlightMode = Annotated[str, Field(strict=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
Availability = Literal["available", "missing"]


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


class FlightTelemetryObservation(StrictModel):
    """Agent-visible PX4/MAVSDK estimates at one closed provider barrier."""

    schema_version: Literal["aero-bench.observation.flight-telemetry/v1"]
    vehicle_id: Identifier
    observation_status: Literal["available"]
    simulation_time_ns: NonnegativeInt
    source_timestamp_status: Literal["missing"]
    source_timestamp_us: None
    source_to_sim_time_mapping: Literal["unavailable"]
    freshness_basis: Literal["latest_sample_received_before_barrier"]
    sample_received_monotonic_ns: NonnegativeInt
    observation_monotonic_ns: NonnegativeInt
    receipt_age_ms: FiniteFloat
    position_source: Literal["mavsdk.telemetry.position"]
    longitude_deg: FiniteFloat
    latitude_deg: FiniteFloat
    absolute_altitude_m: FiniteFloat
    absolute_altitude_reference: Literal["AMSL"]
    relative_altitude_m: FiniteFloat
    velocity_source: Literal["mavsdk.telemetry.position_velocity_ned"]
    north_m_s: FiniteFloat
    east_m_s: FiniteFloat
    down_m_s: FiniteFloat
    attitude_source: Literal["mavsdk.telemetry.attitude_euler"]
    roll_deg: FiniteFloat
    pitch_deg: FiniteFloat
    yaw_deg: FiniteFloat
    flight_mode: FlightMode
    armed: bool
    in_air: bool
    landed: bool
    landed_state: FlightMode
    battery_percent: FiniteFloat
    health_source: Literal["mavsdk.telemetry.health"]
    health_json: str

    @field_validator(
        "longitude_deg",
        "latitude_deg",
        "absolute_altitude_m",
        "relative_altitude_m",
        "north_m_s",
        "east_m_s",
        "down_m_s",
        "roll_deg",
        "pitch_deg",
        "yaw_deg",
        "battery_percent",
        "receipt_age_ms",
    )
    @classmethod
    def finite_values(cls, value: float) -> float:
        return _finite(value, label="flight telemetry value")

    @field_validator("health_json")
    @classmethod
    def canonical_health_flags(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError("health_json must be JSON") from error
        if (
            not isinstance(parsed, dict)
            or not parsed
            or any(not isinstance(name, str) for name in parsed)
            or any(type(flag) is not bool for flag in parsed.values())
            or json.dumps(
                parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            != value
        ):
            raise ValueError("health_json must contain canonical boolean flags")
        return value

    @model_validator(mode="after")
    def internally_consistent(self) -> "FlightTelemetryObservation":
        if not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg is outside [-180, 180]")
        if not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg is outside [-90, 90]")
        if not 0.0 <= self.battery_percent <= 100.0:
            raise ValueError("battery_percent is outside [0, 100]")
        if self.receipt_age_ms < 0.0:
            raise ValueError("receipt_age_ms cannot be negative")
        if self.sample_received_monotonic_ns > self.observation_monotonic_ns:
            raise ValueError("sample receipt occurs after observation")
        if self.landed != (self.landed_state == "ON_GROUND"):
            raise ValueError("landed differs from landed_state")
        return self


class FlightGnssObservation(StrictModel):
    """Raw GNSS signal and fix metadata, separate from fused position estimates."""

    schema_version: Literal["aero-bench.observation.flight-gnss/v1"]
    vehicle_id: Identifier
    observation_status: Literal["available"]
    simulation_time_ns: NonnegativeInt
    source: Literal["mavsdk.telemetry.raw_gps+gps_info"]
    source_timestamp_stream: Literal["raw_gps"]
    source_timestamp_us: NonnegativeInt
    source_time_basis: Literal["unix_epoch_or_autopilot_boot_unspecified"]
    gps_info_timestamp_status: Literal["missing"]
    gps_info_timestamp_us: None
    source_to_sim_time_mapping: Literal["unavailable"]
    freshness_basis: Literal["latest_sample_received_before_barrier"]
    sample_received_monotonic_ns: NonnegativeInt
    observation_monotonic_ns: NonnegativeInt
    receipt_age_ms: FiniteFloat
    position_status: Literal["available", "invalid_fix"]
    latitude_deg: FiniteFloat
    longitude_deg: FiniteFloat
    absolute_altitude_m: FiniteFloat
    absolute_altitude_reference: Literal["AMSL"]
    fix_type: Literal[
        "NO_GPS",
        "NO_FIX",
        "FIX_2D",
        "FIX_3D",
        "FIX_DGPS",
        "RTK_FLOAT",
        "RTK_FIXED",
    ]
    num_satellites: Annotated[int, Field(ge=0, le=255, strict=True)]
    hdop_status: Availability
    hdop: FiniteFloat | None
    vdop_status: Availability
    vdop: FiniteFloat | None
    velocity_status: Availability
    velocity_m_s: FiniteFloat | None
    course_over_ground_status: Availability
    course_over_ground_deg: FiniteFloat | None
    ellipsoid_altitude_status: Availability
    altitude_ellipsoid_m: FiniteFloat | None
    horizontal_uncertainty_status: Availability
    horizontal_uncertainty_m: FiniteFloat | None
    vertical_uncertainty_status: Availability
    vertical_uncertainty_m: FiniteFloat | None
    velocity_uncertainty_status: Availability
    velocity_uncertainty_m_s: FiniteFloat | None
    heading_uncertainty_status: Availability
    heading_uncertainty_deg: FiniteFloat | None
    yaw_status: Availability
    yaw_deg: FiniteFloat | None

    @field_validator(
        "latitude_deg",
        "longitude_deg",
        "absolute_altitude_m",
        "receipt_age_ms",
        "hdop",
        "vdop",
        "velocity_m_s",
        "course_over_ground_deg",
        "altitude_ellipsoid_m",
        "horizontal_uncertainty_m",
        "vertical_uncertainty_m",
        "velocity_uncertainty_m_s",
        "heading_uncertainty_deg",
        "yaw_deg",
    )
    @classmethod
    def finite_values(cls, value: float | None) -> float | None:
        return None if value is None else _finite(value, label="GNSS value")

    @model_validator(mode="after")
    def availability_matches_values(self) -> "FlightGnssObservation":
        if not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg is outside [-180, 180]")
        if not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg is outside [-90, 90]")
        if self.receipt_age_ms < 0.0:
            raise ValueError("receipt_age_ms cannot be negative")
        if self.sample_received_monotonic_ns > self.observation_monotonic_ns:
            raise ValueError("sample receipt occurs after observation")
        expected_position_status = (
            "invalid_fix" if self.fix_type in {"NO_GPS", "NO_FIX"} else "available"
        )
        if self.position_status != expected_position_status:
            raise ValueError("position_status differs from fix_type")
        pairs = (
            (self.hdop_status, self.hdop),
            (self.vdop_status, self.vdop),
            (self.velocity_status, self.velocity_m_s),
            (self.course_over_ground_status, self.course_over_ground_deg),
            (self.ellipsoid_altitude_status, self.altitude_ellipsoid_m),
            (self.horizontal_uncertainty_status, self.horizontal_uncertainty_m),
            (self.vertical_uncertainty_status, self.vertical_uncertainty_m),
            (self.velocity_uncertainty_status, self.velocity_uncertainty_m_s),
            (self.heading_uncertainty_status, self.heading_uncertainty_deg),
            (self.yaw_status, self.yaw_deg),
        )
        if any(
            (status == "available") != (value is not None) for status, value in pairs
        ):
            raise ValueError("GNSS availability flag differs from its value")
        return self


class InspectionRgbObservation(StrictModel):
    """Content-bound Gazebo RGB frame returned to an authorized Agent."""

    schema_version: Literal["aero-bench.observation.rgb/v1"]
    target_id: Identifier
    camera_id: Identifier
    distance_m: FiniteFloat
    view_angle_deg: FiniteFloat
    visual_kind: Literal["gazebo_rgb"]
    visual_status: Literal["captured"]
    frame_id: Identifier
    vehicle_id: Identifier
    engine_sim_time_ns: NonnegativeInt
    logical_origin_engine_ns: NonnegativeInt
    capture_pose_source: Literal["gazebo.pose.private_digest"]
    camera_pose_sha256: Sha256
    target_pose_sha256: Sha256
    image_sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0, strict=True)]
    selector: str
    width: Annotated[int, Field(gt=0, le=4096, strict=True)]
    height: Annotated[int, Field(gt=0, le=4096, strict=True)]
    media_type: Literal["image/png"]
    source: Literal["gazebo.camera"]
    image_base64: str

    @field_validator("distance_m", "view_angle_deg")
    @classmethod
    def finite_geometry(cls, value: float) -> float:
        return _finite(value, label="inspection RGB geometry")

    @model_validator(mode="after")
    def image_content_matches_identity(self) -> "InspectionRgbObservation":
        try:
            encoded = self.image_base64.encode("ascii")
            image = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, binascii.Error) as error:
            raise ValueError("image_base64 is not strict base64") from error
        if base64.b64encode(image) != encoded:
            raise ValueError("image_base64 is not canonical")
        if len(image) != self.size_bytes:
            raise ValueError("RGB size_bytes differs from image content")
        if hashlib.sha256(image).hexdigest() != self.image_sha256:
            raise ValueError("RGB image_sha256 differs from image content")
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("RGB image content is not PNG")
        if self.selector != f"frames/{self.frame_id}":
            raise ValueError("RGB selector differs from frame_id")
        if self.engine_sim_time_ns < self.logical_origin_engine_ns:
            raise ValueError("RGB engine time precedes the logical origin")
        if self.distance_m < 0.0 or not 0.0 <= self.view_angle_deg <= 180.0:
            raise ValueError("RGB observation geometry is invalid")
        return self


__all__ = [
    "FLIGHT_GNSS_OBSERVATION_SCHEMA",
    "FLIGHT_TELEMETRY_OBSERVATION_SCHEMA",
    "RGB_OBSERVATION_SCHEMA",
    "FlightGnssObservation",
    "FlightTelemetryObservation",
    "InspectionRgbObservation",
]
