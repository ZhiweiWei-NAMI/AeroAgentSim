"""Strict WGS84 frame contracts and conversion helpers.

The public model layer remains the single source of schema and validation logic.
All numerical transformation logic is delegated to ``aero_bench.world.frame_math``
so this module stays a strict wrapper.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import field_validator, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.world import frame_math

WGS84_FRAME_ID: Final = "WGS84"
ENU_FRAME_ID: Final = "ENU"
ECEF_FRAME_ID: Final = "ECEF"
NED_FRAME_ID: Final = "NED"

GeodeticFrameId = Literal[WGS84_FRAME_ID]
EnuFrameId = Literal[ENU_FRAME_ID]
EcefFrameId = Literal[ECEF_FRAME_ID]
NedFrameId = Literal[NED_FRAME_ID]

WorldFrameError = frame_math.FrameMathError


def _require_finite(name: str, value: float) -> float:
    return frame_math._require_finite(name, value)


def _canonical_longitude(value: float) -> float:
    return frame_math._canonical_longitude(value)


class LocalFrameOrigin(StrictModel):
    """The explicit ENU origin; there is no implicit default origin."""

    frame_id: GeodeticFrameId
    longitude_deg: float
    latitude_deg: float
    altitude_m: float

    @field_validator("longitude_deg")
    @classmethod
    def valid_longitude(cls, value: float) -> float:
        _require_finite("longitude_deg", value)
        if not -180.0 <= value <= 180.0:
            raise WorldFrameError("origin longitude must be in [-180, 180]")
        return _canonical_longitude(value)

    @field_validator("latitude_deg")
    @classmethod
    def valid_latitude(cls, value: float) -> float:
        _require_finite("latitude_deg", value)
        if not -90.0 < value < 90.0:
            raise WorldFrameError(
                "origin latitude must be strictly inside (-90, 90); the local "
                "ENU basis is undefined at the poles"
            )
        return value

    @field_validator("altitude_m")
    @classmethod
    def finite_altitude(cls, value: float) -> float:
        return _require_finite("altitude_m", value)


class Wgs84Position(StrictModel):
    """A strict non-polar geodetic position in WGS84 coordinates."""

    frame_id: GeodeticFrameId
    longitude_deg: float
    latitude_deg: float
    altitude_m: float

    @field_validator("longitude_deg")
    @classmethod
    def valid_longitude(cls, value: float) -> float:
        _require_finite("longitude_deg", value)
        if not -180.0 <= value <= 180.0:
            raise WorldFrameError("longitude must be in [-180, 180]")
        return _canonical_longitude(value)

    @field_validator("latitude_deg")
    @classmethod
    def valid_latitude(cls, value: float) -> float:
        _require_finite("latitude_deg", value)
        if not -90.0 < value < 90.0:
            raise WorldFrameError(
                "latitude must be strictly inside (-90, 90); exact poles are "
                "unsupported by the WGS84/ENU inverse conversion"
            )
        return value

    @field_validator("altitude_m")
    @classmethod
    def finite_altitude(cls, value: float) -> float:
        return _require_finite("altitude_m", value)


class EnuPosition(StrictModel):
    """A strict local tangent-plane position in metres."""

    frame_id: EnuFrameId
    east_m: float
    north_m: float
    up_m: float

    @field_validator("east_m", "north_m", "up_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("position component", value)


class EcefPosition(StrictModel):
    """A strict Earth-fixed Cartesian position in metres."""

    frame_id: EcefFrameId
    x_m: float
    y_m: float
    z_m: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("position component", value)


class NedPosition(StrictModel):
    """A strict local NED position in metres."""

    frame_id: NedFrameId
    north_m: float
    east_m: float
    down_m: float

    @field_validator("north_m", "east_m", "down_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("position component", value)


class Vector3(StrictModel):
    """Strict 3D vector with immutable contract."""

    x_m: float
    y_m: float
    z_m: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("vector component", value)


class UnitQuaternion(StrictModel):
    """Strict unit quaternion contract."""

    qw: float
    qx: float
    qy: float
    qz: float

    @field_validator("qw", "qx", "qy", "qz")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("quaternion component", value)

    @model_validator(mode="after")
    def unit_norm(self) -> "UnitQuaternion":
        frame_math.UnitQuaternion(self.qw, self.qx, self.qy, self.qz)
        return self


class FrameRotation(StrictModel):
    """Strict 3x3 rotation matrix contract."""

    matrix: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    @field_validator("matrix")
    @classmethod
    def row_shape_and_finite(cls, value: tuple) -> tuple:
        if len(value) != 3:
            raise ValueError("matrix must be 3x3")
        for index, row in enumerate(value):
            if len(row) != 3:
                raise ValueError(f"matrix row {index} must have 3 values")
            for item in row:
                _require_finite("matrix value", item)
        return value

    @model_validator(mode="after")
    def orthonormal(self) -> "FrameRotation":
        frame_math.RotationMatrix(self.matrix)
        return self


class RigidTransform(StrictModel):
    """Strict rigid transform contract with unit rotation and finite translation.

    Translation is represented in target-frame coordinates.
    """

    source_frame_id: str
    target_frame_id: str
    rotation: FrameRotation
    translation: Vector3

    @field_validator("source_frame_id", "target_frame_id")
    @classmethod
    def valid_frame_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise WorldFrameError("frame_id must be a non-empty string")
        return value


def _compile_origin(origin: LocalFrameOrigin) -> frame_math.EnuTransform:
    return frame_math.EnuTransform.from_origin(
        longitude_deg=origin.longitude_deg,
        latitude_deg=origin.latitude_deg,
        altitude_m=origin.altitude_m,
    )


def _to_math_rigid_transform(transform: RigidTransform) -> frame_math.RigidTransform:
    return frame_math.RigidTransform(
        rotation=frame_math.RotationMatrix(transform.rotation.matrix),
        translation=frame_math.Vector3(
            transform.translation.x_m,
            transform.translation.y_m,
            transform.translation.z_m,
        ),
    )


def geodetic_to_ecef(posit: Wgs84Position) -> EcefPosition:
    """Convert WGS84 geodetic to ECEF."""

    ecef = frame_math.geodetic_to_ecef(
        longitude_deg=posit.longitude_deg,
        latitude_deg=posit.latitude_deg,
        altitude_m=posit.altitude_m,
    )
    return EcefPosition(
        frame_id=ECEF_FRAME_ID,
        x_m=ecef.x,
        y_m=ecef.y,
        z_m=ecef.z,
    )


def ecef_to_geodetic(posit: EcefPosition) -> Wgs84Position:
    """Convert ECEF to WGS84 geodetic."""

    longitude_deg, latitude_deg, altitude_m = frame_math.ecef_to_geodetic(
        posit.x_m,
        posit.y_m,
        posit.z_m,
    )
    return Wgs84Position(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        altitude_m=altitude_m,
    )


def geodetic_to_enu(posit: Wgs84Position, origin: LocalFrameOrigin) -> EnuPosition:
    """Convert a WGS84 geodetic position into the origin's local ENU frame."""

    vector = _compile_origin(origin).geodetic_to_enu(
        longitude_deg=posit.longitude_deg,
        latitude_deg=posit.latitude_deg,
        altitude_m=posit.altitude_m,
    )
    return EnuPosition(
        frame_id=ENU_FRAME_ID,
        east_m=vector.x,
        north_m=vector.y,
        up_m=vector.z,
    )


def enu_to_geodetic(posit: EnuPosition, origin: LocalFrameOrigin) -> Wgs84Position:
    """Convert local ENU position back into WGS84 geodetic coordinates."""

    longitude_deg, latitude_deg, altitude_m = _compile_origin(origin).enu_to_geodetic(
        frame_math.Vector3(posit.east_m, posit.north_m, posit.up_m)
    )
    return Wgs84Position(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        altitude_m=altitude_m,
    )


def ecef_to_enu(posit: EcefPosition, origin: LocalFrameOrigin) -> EnuPosition:
    vector = _compile_origin(origin).enu_from_ecef(
        frame_math.Vector3(posit.x_m, posit.y_m, posit.z_m)
    )
    return EnuPosition(
        frame_id=ENU_FRAME_ID,
        east_m=vector.x,
        north_m=vector.y,
        up_m=vector.z,
    )


def enu_to_ecef_position(posit: EnuPosition, origin: LocalFrameOrigin) -> EcefPosition:
    vector = _compile_origin(origin).ecef_from_enu(
        frame_math.Vector3(posit.east_m, posit.north_m, posit.up_m)
    )
    return EcefPosition(
        frame_id=ECEF_FRAME_ID,
        x_m=vector.x,
        y_m=vector.y,
        z_m=vector.z,
    )


def enu_to_ned(posit: EnuPosition) -> NedPosition:
    ned = frame_math.enu_to_ned(
        frame_math.Vector3(posit.east_m, posit.north_m, posit.up_m)
    )
    return NedPosition(
        frame_id=NED_FRAME_ID,
        north_m=ned.x,
        east_m=ned.y,
        down_m=ned.z,
    )


def ned_to_enu(posit: NedPosition) -> EnuPosition:
    enu = frame_math.ned_to_enu(
        frame_math.Vector3(posit.north_m, posit.east_m, posit.down_m)
    )
    return EnuPosition(
        frame_id=ENU_FRAME_ID,
        east_m=enu.x,
        north_m=enu.y,
        up_m=enu.z,
    )


def frame_rotation_from_quaternion(quaternion: UnitQuaternion) -> FrameRotation:
    matrix = frame_math.UnitQuaternion(
        w=quaternion.qw,
        x=quaternion.qx,
        y=quaternion.qy,
        z=quaternion.qz,
    ).to_rotation_matrix()
    return FrameRotation(matrix=matrix.rows)


def frame_unit_quaternion_from_rotation_matrix(
    rotation: FrameRotation,
) -> UnitQuaternion:
    math_quaternion = frame_math.UnitQuaternion.from_rotation_matrix(
        frame_math.RotationMatrix(rotation.matrix)
    )
    return UnitQuaternion(
        qw=math_quaternion.w,
        qx=math_quaternion.x,
        qy=math_quaternion.y,
        qz=math_quaternion.z,
    )


def rotate_vector(vector: Vector3, rotation: FrameRotation) -> Vector3:
    rotated = frame_math.RotationMatrix(rotation.matrix).apply(
        frame_math.Vector3(vector.x_m, vector.y_m, vector.z_m)
    )
    return Vector3(x_m=rotated.x, y_m=rotated.y, z_m=rotated.z)


def rotate_vector_with_quaternion(
    vector: Vector3,
    quaternion: UnitQuaternion,
) -> Vector3:
    quat = frame_math.UnitQuaternion(
        w=quaternion.qw,
        x=quaternion.qx,
        y=quaternion.qy,
        z=quaternion.qz,
    )
    rotated = quat.rotate(frame_math.Vector3(vector.x_m, vector.y_m, vector.z_m))
    return Vector3(x_m=rotated.x, y_m=rotated.y, z_m=rotated.z)


def invert_rotation(rotation: FrameRotation) -> FrameRotation:
    return FrameRotation(
        matrix=frame_math.RotationMatrix(rotation.matrix).inverse().rows
    )


def invert_unit_quaternion(quaternion: UnitQuaternion) -> UnitQuaternion:
    inverse = frame_math.UnitQuaternion(
        w=quaternion.qw,
        x=quaternion.qx,
        y=quaternion.qy,
        z=quaternion.qz,
    ).inverse()
    return UnitQuaternion(
        qw=inverse.w,
        qx=inverse.x,
        qy=inverse.y,
        qz=inverse.z,
    )


def compose_unit_quaternions(
    left: UnitQuaternion,
    right: UnitQuaternion,
) -> UnitQuaternion:
    composed = frame_math.UnitQuaternion(
        w=left.qw,
        x=left.qx,
        y=left.qy,
        z=left.qz,
    ).compose(
        frame_math.UnitQuaternion(
            w=right.qw,
            x=right.qx,
            y=right.qy,
            z=right.qz,
        )
    )
    return UnitQuaternion(
        qw=composed.w,
        qx=composed.x,
        qy=composed.y,
        qz=composed.z,
    )


def apply_orientation(
    orientation: UnitQuaternion,
    transform: RigidTransform,
) -> UnitQuaternion:
    mapped = _to_math_rigid_transform(transform).apply_orientation(
        frame_math.UnitQuaternion(
            w=orientation.qw,
            x=orientation.qx,
            y=orientation.qy,
            z=orientation.qz,
        )
    )
    return UnitQuaternion(
        qw=mapped.w,
        qx=mapped.x,
        qy=mapped.y,
        qz=mapped.z,
    )


def compose_rigid_transforms(
    first: RigidTransform,
    second: RigidTransform,
) -> RigidTransform:
    if first.target_frame_id != second.source_frame_id:
        raise WorldFrameError(
            "rigid transforms are not chainable; first target must match second source"
        )

    composed = _to_math_rigid_transform(second).compose(_to_math_rigid_transform(first))
    return RigidTransform(
        source_frame_id=first.source_frame_id,
        target_frame_id=second.target_frame_id,
        rotation=FrameRotation(matrix=composed.rotation.rows),
        translation=Vector3(
            x_m=composed.translation.x,
            y_m=composed.translation.y,
            z_m=composed.translation.z,
        ),
    )


def apply_rigid_transform(
    point: Vector3,
    transform: RigidTransform,
) -> Vector3:
    rigid = _to_math_rigid_transform(transform)
    translated = rigid.apply_position(
        frame_math.Vector3(point.x_m, point.y_m, point.z_m)
    )
    return Vector3(
        x_m=translated.x,
        y_m=translated.y,
        z_m=translated.z,
    )


def invert_rigid_transform(transform: RigidTransform) -> RigidTransform:
    rigid = _to_math_rigid_transform(transform)
    inverted = rigid.inverse()
    return RigidTransform(
        source_frame_id=transform.target_frame_id,
        target_frame_id=transform.source_frame_id,
        rotation=FrameRotation(matrix=inverted.rotation.rows),
        translation=Vector3(
            x_m=inverted.translation.x,
            y_m=inverted.translation.y,
            z_m=inverted.translation.z,
        ),
    )


__all__ = [
    "GeodeticFrameId",
    "EnuFrameId",
    "EcefFrameId",
    "NedFrameId",
    "ECEF_FRAME_ID",
    "ENU_FRAME_ID",
    "NED_FRAME_ID",
    "WGS84_FRAME_ID",
    "LocalFrameOrigin",
    "Wgs84Position",
    "EnuPosition",
    "EcefPosition",
    "NedPosition",
    "Vector3",
    "FrameRotation",
    "UnitQuaternion",
    "RigidTransform",
    "WorldFrameError",
    "apply_rigid_transform",
    "ecef_to_enu",
    "ecef_to_geodetic",
    "enu_to_ecef_position",
    "enu_to_geodetic",
    "enu_to_ned",
    "geodetic_to_ecef",
    "geodetic_to_enu",
    "frame_rotation_from_quaternion",
    "frame_unit_quaternion_from_rotation_matrix",
    "compose_rigid_transforms",
    "compose_unit_quaternions",
    "apply_orientation",
    "invert_unit_quaternion",
    "invert_rotation",
    "invert_rigid_transform",
    "ned_to_enu",
    "rotate_vector",
    "rotate_vector_with_quaternion",
]
