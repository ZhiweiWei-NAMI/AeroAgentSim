"""Pure-stdlib WGS84 and rigid-frame math primitives.

This module contains the canonical numerical implementation used by the frame
wrappers in ``aero_bench.world.frames``. It intentionally has no project-level
imports (Pydantic, config models, provider bindings), and only provides explicit
finite checks and strict failure behavior.

Conventions:

- Geodetic longitudes are always represented in degrees on
  ``[-180.0, 180.0)`` with exact ``+180.0`` normalized to ``-180.0``.
- Geodetic latitudes are finite and strictly inside ``(-90.0, 90.0)`` for any
  ENU conversion; this keeps local tangent axes well-defined.
- ENU uses axes ``(east, north, up)``.
- NED uses axes ``(north, east, down)``.
- Rotation matrices are required to be finite, orthonormal (within strict
  tolerance), and right-handed.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Final


WGS84_SEMI_MAJOR_AXIS_M: Final = 6_378_137.0
"""WGS84 semi-major axis in metres."""

WGS84_FLATTENING: Final = 1.0 / 298.257_223_563
"""WGS84 inverse flattening ratio denominator."""

WGS84_ECCENTRICITY_SQUARED: Final = WGS84_FLATTENING * (2.0 - WGS84_FLATTENING)

INVERSE_ITERATION_LIMIT: Final = 16
"""Default fixed-point passes for inverse geodetic conversion."""

_ORTHONORMAL_TOLERANCE: Final = 1e-12
_UNIT_QUATERNION_TOLERANCE: Final = 1e-9


class FrameMathError(ValueError):
    """Raised when coordinate math cannot return a verified result."""


def _canonical_longitude(longitude_deg: float) -> float:
    if longitude_deg == 180.0:
        return -180.0
    return longitude_deg


def _normalize_longitude(longitude_deg: float) -> float:
    return ((longitude_deg + 180.0) % 360.0) - 180.0


def _require_finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise FrameMathError(f"{name} must be finite")
    return value


def _canonical_quaternion_sign(
    w: float, x: float, y: float, z: float
) -> tuple[float, float, float, float]:
    def is_negative(value: float) -> bool:
        return math.copysign(1.0, value) < 0.0

    if w < 0.0 or (
        w == 0.0
        and (
            is_negative(x)
            or (x == 0.0 and (is_negative(y) or (y == 0.0 and is_negative(z))))
        )
    ):
        return -w, -x, -y, -z
    return w, x, y, z


def _require_geodetic_inputs(
    *,
    longitude_deg: float,
    latitude_deg: float,
    altitude_m: float,
    allow_poles: bool,
) -> tuple[float, float, float]:
    longitude_deg = _require_finite("longitude_deg", longitude_deg)
    latitude_deg = _require_finite("latitude_deg", latitude_deg)
    altitude_m = _require_finite("altitude_m", altitude_m)
    if not -180.0 <= longitude_deg <= 180.0:
        raise FrameMathError("longitude_deg must be in [-180, 180]")
    if latitude_deg == 90.0 or latitude_deg == -90.0:
        if allow_poles:
            return _canonical_longitude(longitude_deg), latitude_deg, altitude_m
        raise FrameMathError(
            "latitude_deg must be strictly inside (-90, 90); exact poles are unsupported"
        )
    if not -90.0 < latitude_deg < 90.0:
        raise FrameMathError("latitude_deg must be strictly inside (-90, 90)")
    return _canonical_longitude(longitude_deg), latitude_deg, altitude_m


def _require_rotation_matrix_row(
    row: tuple[float, float, float], *, row_name: str
) -> None:
    if len(row) != 3:
        raise FrameMathError(f"{row_name} must contain exactly 3 values")
    for value in row:
        _require_finite(f"rotation matrix {row_name}", value)


@dataclass(frozen=True, slots=True)
class ScalarGridSampler:
    """Finite, rectangular ENU scalar grid with canonical interpolation."""

    east_axis_m: tuple[float, ...]
    north_axis_m: tuple[float, ...]
    values_m: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if len(self.east_axis_m) < 2 or len(self.north_axis_m) < 2:
            raise FrameMathError("scalar grid axes require at least two values")
        if any(
            right <= left
            for left, right in zip(
                self.east_axis_m, self.east_axis_m[1:]
            )
        ):
            raise FrameMathError("scalar grid east axis must be strictly increasing")
        if any(
            right <= left
            for left, right in zip(
                self.north_axis_m, self.north_axis_m[1:]
            )
        ):
            raise FrameMathError("scalar grid north axis must be strictly increasing")
        for value in self.east_axis_m:
            _require_finite("scalar grid east axis", value)
        for value in self.north_axis_m:
            _require_finite("scalar grid north axis", value)
        if len(self.values_m) != len(self.north_axis_m):
            raise FrameMathError("scalar grid row count must match north axis")
        for row in self.values_m:
            if len(row) != len(self.east_axis_m):
                raise FrameMathError("scalar grid column count must match east axis")
            for value in row:
                _require_finite("scalar grid value", value)

    @staticmethod
    def _nearest_index(axis: tuple[float, ...], coordinate: float) -> int:
        index = bisect_left(axis, coordinate)
        if index <= 0:
            return 0
        if index >= len(axis):
            return len(axis) - 1
        left = axis[index - 1]
        right = axis[index]
        return index - 1 if abs(coordinate - left) <= abs(right - coordinate) else index

    @staticmethod
    def _bracket(
        axis: tuple[float, ...], coordinate: float
    ) -> tuple[int, int, float]:
        upper = bisect_right(axis, coordinate)
        lower_index = max(0, min(len(axis) - 2, upper - 1))
        upper_index = lower_index + 1
        start = axis[lower_index]
        end = axis[upper_index]
        return lower_index, upper_index, (coordinate - start) / (end - start)

    def sample(self, east_m: float, north_m: float, interpolation: str) -> float:
        east_m = _require_finite("scalar grid east coordinate", east_m)
        north_m = _require_finite("scalar grid north coordinate", north_m)
        if not self.east_axis_m[0] <= east_m <= self.east_axis_m[-1]:
            raise FrameMathError("east coordinate lies outside scalar grid coverage")
        if not self.north_axis_m[0] <= north_m <= self.north_axis_m[-1]:
            raise FrameMathError("north coordinate lies outside scalar grid coverage")
        if interpolation == "nearest":
            east_index = self._nearest_index(self.east_axis_m, east_m)
            north_index = self._nearest_index(self.north_axis_m, north_m)
            return self.values_m[north_index][east_index]
        if interpolation != "bilinear":
            raise FrameMathError("unsupported scalar grid interpolation")
        east0, east1, east_fraction = self._bracket(self.east_axis_m, east_m)
        north0, north1, north_fraction = self._bracket(
            self.north_axis_m, north_m
        )
        lower_left = self.values_m[north0][east0]
        lower_right = self.values_m[north0][east1]
        upper_left = self.values_m[north1][east0]
        upper_right = self.values_m[north1][east1]
        lower = lower_left + (lower_right - lower_left) * east_fraction
        upper = upper_left + (upper_right - upper_left) * east_fraction
        return lower + (upper - lower) * north_fraction


@dataclass(frozen=True, slots=True)
class Vector3:
    """A finite 3D vector."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        _require_finite("vector.x", self.x)
        _require_finite("vector.y", self.y)
        _require_finite("vector.z", self.z)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def __add__(self, other: Vector3) -> Vector3:
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vector3) -> Vector3:
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __neg__(self) -> Vector3:
        return Vector3(-self.x, -self.y, -self.z)

    def __mul__(self, scale: float) -> Vector3:
        return Vector3(self.x * scale, self.y * scale, self.z * scale)

    def __rmul__(self, scale: float) -> Vector3:
        return self.__mul__(scale)

    def dot(self, other: Vector3) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Vector3) -> Vector3:
        return Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def norm2(self) -> float:
        return self.dot(self)

    def norm(self) -> float:
        return math.sqrt(self.norm2())


@dataclass(frozen=True, slots=True)
class RotationMatrix:
    """Orthogonal 3x3 rotation matrix."""

    rows: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    def __post_init__(self) -> None:
        if len(self.rows) != 3:
            raise FrameMathError("rotation matrix must contain exactly 3 rows")
        for index, row in enumerate(self.rows):
            _require_rotation_matrix_row(row, row_name=f"row[{index}]")

        for row in self.rows:
            row_norm = math.sqrt(sum(component * component for component in row))
            if not math.isfinite(row_norm):
                raise FrameMathError("rotation row norm must be finite")
            if abs(row_norm - 1.0) > _ORTHONORMAL_TOLERANCE:
                raise FrameMathError("rotation rows must be unit length")

        for i in range(3):
            for j in range(i + 1, 3):
                row_i = Vector3(*self.rows[i])
                row_j = Vector3(*self.rows[j])
                if abs(row_i.dot(row_j)) > _ORTHONORMAL_TOLERANCE:
                    raise FrameMathError("rotation rows must be orthogonal")

        determinant = self.determinant()
        if abs(determinant - 1.0) > _ORTHONORMAL_TOLERANCE:
            raise FrameMathError("rotation matrix must be right-handed")

    def determinant(self) -> float:
        return (
            self.rows[0][0] * self.rows[1][1] * self.rows[2][2]
            + self.rows[0][1] * self.rows[1][2] * self.rows[2][0]
            + self.rows[0][2] * self.rows[1][0] * self.rows[2][1]
            - self.rows[0][2] * self.rows[1][1] * self.rows[2][0]
            - self.rows[0][1] * self.rows[1][0] * self.rows[2][2]
            - self.rows[0][0] * self.rows[1][2] * self.rows[2][1]
        )

    @classmethod
    def identity(cls) -> RotationMatrix:
        return cls(rows=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))

    def apply(self, vector: Vector3) -> Vector3:
        return Vector3(
            self.rows[0][0] * vector.x
            + self.rows[0][1] * vector.y
            + self.rows[0][2] * vector.z,
            self.rows[1][0] * vector.x
            + self.rows[1][1] * vector.y
            + self.rows[1][2] * vector.z,
            self.rows[2][0] * vector.x
            + self.rows[2][1] * vector.y
            + self.rows[2][2] * vector.z,
        )

    def compose(self, right: RotationMatrix) -> RotationMatrix:
        return RotationMatrix(
            rows=(
                (
                    self.rows[0][0] * right.rows[0][0]
                    + self.rows[0][1] * right.rows[1][0]
                    + self.rows[0][2] * right.rows[2][0],
                    self.rows[0][0] * right.rows[0][1]
                    + self.rows[0][1] * right.rows[1][1]
                    + self.rows[0][2] * right.rows[2][1],
                    self.rows[0][0] * right.rows[0][2]
                    + self.rows[0][1] * right.rows[1][2]
                    + self.rows[0][2] * right.rows[2][2],
                ),
                (
                    self.rows[1][0] * right.rows[0][0]
                    + self.rows[1][1] * right.rows[1][0]
                    + self.rows[1][2] * right.rows[2][0],
                    self.rows[1][0] * right.rows[0][1]
                    + self.rows[1][1] * right.rows[1][1]
                    + self.rows[1][2] * right.rows[2][1],
                    self.rows[1][0] * right.rows[0][2]
                    + self.rows[1][1] * right.rows[1][2]
                    + self.rows[1][2] * right.rows[2][2],
                ),
                (
                    self.rows[2][0] * right.rows[0][0]
                    + self.rows[2][1] * right.rows[1][0]
                    + self.rows[2][2] * right.rows[2][0],
                    self.rows[2][0] * right.rows[0][1]
                    + self.rows[2][1] * right.rows[1][1]
                    + self.rows[2][2] * right.rows[2][1],
                    self.rows[2][0] * right.rows[0][2]
                    + self.rows[2][1] * right.rows[1][2]
                    + self.rows[2][2] * right.rows[2][2],
                ),
            )
        )

    def inverse(self) -> RotationMatrix:
        return RotationMatrix(
            rows=(
                (self.rows[0][0], self.rows[1][0], self.rows[2][0]),
                (self.rows[0][1], self.rows[1][1], self.rows[2][1]),
                (self.rows[0][2], self.rows[1][2], self.rows[2][2]),
            )
        )


ENU_TO_NED_ROTATION: Final = RotationMatrix(
    rows=((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0))
)
"""Canonical right-handed ENU-to-NED axis rotation."""

NED_TO_ENU_ROTATION: Final = ENU_TO_NED_ROTATION.inverse()
"""Canonical right-handed NED-to-ENU axis rotation."""


@dataclass(frozen=True, slots=True)
class UnitQuaternion:
    """Finite unit quaternion used for explicit frame rotations."""

    w: float
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        _require_finite("quaternion.w", self.w)
        _require_finite("quaternion.x", self.x)
        _require_finite("quaternion.y", self.y)
        _require_finite("quaternion.z", self.z)
        norm = math.sqrt(
            self.w * self.w + self.x * self.x + self.y * self.y + self.z * self.z
        )
        if norm <= 0.0:
            raise FrameMathError("quaternion norm is zero")
        if abs(norm - 1.0) > _UNIT_QUATERNION_TOLERANCE:
            raise FrameMathError("quaternion must be unit length")

    def inverse(self) -> UnitQuaternion:
        w, x, y, z = _canonical_quaternion_sign(self.w, -self.x, -self.y, -self.z)
        return UnitQuaternion(w=w, x=x, y=y, z=z)

    def compose(self, right: UnitQuaternion) -> UnitQuaternion:
        w, x, y, z = _canonical_quaternion_sign(
            self.w * right.w - self.x * right.x - self.y * right.y - self.z * right.z,
            self.w * right.x + self.x * right.w + self.y * right.z - self.z * right.y,
            self.w * right.y - self.x * right.z + self.y * right.w + self.z * right.x,
            self.w * right.z + self.x * right.y - self.y * right.x + self.z * right.w,
        )
        return UnitQuaternion(w=w, x=x, y=y, z=z)

    def rotate(self, vector: Vector3) -> Vector3:
        matrix = self.to_rotation_matrix()
        return matrix.apply(vector)

    def to_rotation_matrix(self) -> RotationMatrix:
        w, x, y, z = _canonical_quaternion_sign(self.w, self.x, self.y, self.z)
        return RotationMatrix(
            rows=(
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
        )

    @classmethod
    def from_rotation_matrix(
        cls,
        rotation: RotationMatrix,
    ) -> UnitQuaternion:
        m00, m01, m02 = rotation.rows[0]
        m10, m11, m12 = rotation.rows[1]
        m20, m21, m22 = rotation.rows[2]
        trace = m00 + m11 + m22

        if trace > 0.0:
            s = math.sqrt(trace + 1.0) * 2.0
            w = 0.25 * s
            x = (m21 - m12) / s
            y = (m02 - m20) / s
            z = (m10 - m01) / s
        elif m00 >= m11 and m00 >= m22:
            s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
            w = (m21 - m12) / s
            x = 0.25 * s
            y = (m01 + m10) / s
            z = (m02 + m20) / s
        elif m11 >= m22:
            s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
            w = (m02 - m20) / s
            x = (m01 + m10) / s
            y = 0.25 * s
            z = (m12 + m21) / s
        else:
            s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
            w = (m10 - m01) / s
            x = (m02 + m20) / s
            y = (m12 + m21) / s
            z = 0.25 * s

        qw, qx, qy, qz = _canonical_quaternion_sign(w, x, y, z)
        return UnitQuaternion(w=qw, x=qx, y=qy, z=qz)


def quaternion_to_rpy(
    quaternion: UnitQuaternion,
) -> tuple[float, float, float]:
    """Return intrinsic roll, pitch, yaw radians for a unit quaternion."""

    if not isinstance(quaternion, UnitQuaternion):
        raise FrameMathError("quaternion_to_rpy requires a UnitQuaternion")
    w, x, y, z = quaternion.w, quaternion.x, quaternion.y, quaternion.z
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (roll, pitch, yaw)


def rpy_to_quaternion(
    *, roll_rad: float, pitch_rad: float, yaw_rad: float
) -> UnitQuaternion:
    """Return the canonical unit quaternion for intrinsic roll, pitch, yaw."""

    roll = _require_finite("roll_rad", roll_rad) / 2.0
    pitch = _require_finite("pitch_rad", pitch_rad) / 2.0
    yaw = _require_finite("yaw_rad", yaw_rad) / 2.0
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    w, x, y, z = _canonical_quaternion_sign(
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )
    return UnitQuaternion(w=w, x=x, y=y, z=z)


@dataclass(frozen=True, slots=True)
class RigidTransform:
    """Right-handed rotation + translation in 3D, immutable."""

    rotation: RotationMatrix
    translation: Vector3

    def apply_vector(self, vector: Vector3) -> Vector3:
        return self.rotation.apply(vector)

    def apply_position(self, position: Vector3) -> Vector3:
        return self.apply_vector(position) + self.translation

    def compose(self, other: RigidTransform) -> RigidTransform:
        composed_rotation = self.rotation.compose(other.rotation)
        composed_translation = self.apply_position(other.translation)
        return RigidTransform(
            rotation=composed_rotation, translation=composed_translation
        )

    def inverse(self) -> RigidTransform:
        inverse_rotation = self.rotation.inverse()
        inverse_translation = inverse_rotation.apply(-self.translation)
        return RigidTransform(
            rotation=inverse_rotation,
            translation=inverse_translation,
        )

    def apply_orientation(self, orientation: UnitQuaternion) -> UnitQuaternion:
        source_rotation = UnitQuaternion.from_rotation_matrix(self.rotation)
        return source_rotation.compose(orientation)


def geodetic_to_ecef(
    longitude_deg: float, latitude_deg: float, altitude_m: float
) -> Vector3:
    """Convert geodetic coordinates to ECEF Cartesian metres."""

    longitude_deg, latitude_deg, altitude_m = _require_geodetic_inputs(
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        altitude_m=altitude_m,
        allow_poles=True,
    )
    longitude = math.radians(longitude_deg)
    latitude = math.radians(latitude_deg)
    prime_vertical_radius = WGS84_SEMI_MAJOR_AXIS_M / math.sqrt(
        1.0 - WGS84_ECCENTRICITY_SQUARED * math.sin(latitude) ** 2
    )
    return Vector3(
        (prime_vertical_radius + altitude_m) * math.cos(latitude) * math.cos(longitude),
        (prime_vertical_radius + altitude_m) * math.cos(latitude) * math.sin(longitude),
        (prime_vertical_radius * (1.0 - WGS84_ECCENTRICITY_SQUARED) + altitude_m)
        * math.sin(latitude),
    )


def ecef_to_geodetic(
    x: float,
    y: float,
    z: float,
    *,
    max_iterations: int = INVERSE_ITERATION_LIMIT,
) -> tuple[float, float, float]:
    """Inverse of ``geodetic_to_ecef`` as longitudes/latitudes/heights."""

    _require_finite("ecef_x", x)
    _require_finite("ecef_y", y)
    _require_finite("ecef_z", z)
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise FrameMathError("max_iterations must be a positive integer")
    if max_iterations <= 0:
        raise FrameMathError("max_iterations must be a positive integer")

    distance = math.hypot(x, y)
    if distance == 0.0:
        raise FrameMathError("ECEF position lies on the polar axis")

    longitude = math.atan2(y, x)
    latitude = math.atan2(z, distance * (1.0 - WGS84_ECCENTRICITY_SQUARED))
    altitude = 0.0
    converged = False

    for _ in range(max_iterations):
        sin_latitude = math.sin(latitude)
        prime_vertical_radius = WGS84_SEMI_MAJOR_AXIS_M / math.sqrt(
            1.0 - WGS84_ECCENTRICITY_SQUARED * sin_latitude**2
        )
        next_latitude = math.atan2(
            z + WGS84_ECCENTRICITY_SQUARED * prime_vertical_radius * sin_latitude,
            distance,
        )
        next_altitude = distance / math.cos(next_latitude) - prime_vertical_radius
        if next_latitude == latitude and next_altitude == altitude:
            latitude = next_latitude
            altitude = next_altitude
            converged = True
            break
        latitude = next_latitude
        altitude = next_altitude

    if not converged:
        raise FrameMathError(
            f"inverse geodetic iteration did not converge within {max_iterations} passes"
        )

    normalized_longitude = _normalize_longitude(math.degrees(longitude))
    return (
        _canonical_longitude(normalized_longitude),
        math.degrees(latitude),
        altitude,
    )


def _ecef_to_enu_rotation(longitude_deg: float, latitude_deg: float) -> RotationMatrix:
    _require_geodetic_inputs(
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        altitude_m=0.0,
        allow_poles=False,
    )
    longitude = math.radians(longitude_deg)
    latitude = math.radians(latitude_deg)
    sin_longitude = math.sin(longitude)
    cos_longitude = math.cos(longitude)
    sin_latitude = math.sin(latitude)
    cos_latitude = math.cos(latitude)
    return RotationMatrix(
        rows=(
            (-sin_longitude, cos_longitude, 0.0),
            (
                -sin_latitude * cos_longitude,
                -sin_latitude * sin_longitude,
                cos_latitude,
            ),
            (
                cos_latitude * cos_longitude,
                cos_latitude * sin_longitude,
                sin_latitude,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class EnuTransform:
    """Compiled ENU transform for one geographic origin."""

    origin_longitude_deg: float
    origin_latitude_deg: float
    origin_altitude_m: float
    origin_ecef: Vector3 = field(init=False)
    ecef_to_enu: RotationMatrix = field(init=False)
    enu_to_ecef: RotationMatrix = field(init=False)

    def __post_init__(self) -> None:
        longitude_deg, latitude_deg, altitude_m = _require_geodetic_inputs(
            longitude_deg=self.origin_longitude_deg,
            latitude_deg=self.origin_latitude_deg,
            altitude_m=self.origin_altitude_m,
            allow_poles=False,
        )
        object.__setattr__(self, "origin_longitude_deg", longitude_deg)
        object.__setattr__(self, "origin_latitude_deg", latitude_deg)
        object.__setattr__(self, "origin_altitude_m", altitude_m)
        object.__setattr__(
            self,
            "origin_ecef",
            geodetic_to_ecef(
                longitude_deg=longitude_deg,
                latitude_deg=latitude_deg,
                altitude_m=altitude_m,
            ),
        )
        object.__setattr__(
            self,
            "ecef_to_enu",
            _ecef_to_enu_rotation(
                longitude_deg=longitude_deg,
                latitude_deg=latitude_deg,
            ),
        )
        object.__setattr__(self, "enu_to_ecef", self.ecef_to_enu.inverse())

    @classmethod
    def from_origin(
        cls, *, longitude_deg: float, latitude_deg: float, altitude_m: float
    ) -> EnuTransform:
        return cls(
            origin_longitude_deg=longitude_deg,
            origin_latitude_deg=latitude_deg,
            origin_altitude_m=altitude_m,
        )

    def ecef_from_enu(self, enu: Vector3) -> Vector3:
        return self.enu_to_ecef.apply(enu) + self.origin_ecef

    def enu_from_ecef(self, ecef: Vector3) -> Vector3:
        return self.ecef_to_enu.apply(ecef - self.origin_ecef)

    def geodetic_to_enu(
        self,
        *,
        longitude_deg: float,
        latitude_deg: float,
        altitude_m: float,
    ) -> Vector3:
        ecef = geodetic_to_ecef(
            longitude_deg=longitude_deg,
            latitude_deg=latitude_deg,
            altitude_m=altitude_m,
        )
        return self.enu_from_ecef(ecef)

    def enu_to_geodetic(self, enu: Vector3) -> tuple[float, float, float]:
        ecef = self.ecef_from_enu(enu)
        return ecef_to_geodetic(ecef.x, ecef.y, ecef.z)


def wgs84_amsl_delta_enu(
    *,
    origin_longitude_deg: float,
    origin_latitude_deg: float,
    origin_amsl_m: float,
    target_longitude_deg: float,
    target_latitude_deg: float,
    target_amsl_m: float,
) -> Vector3:
    """Return a local ENU delta without treating AMSL as ellipsoid height.

    WGS84 longitude/latitude supply the horizontal position. Both horizontal
    points are projected at zero ellipsoid height, while the vertical component
    is the explicit orthometric-height difference. This is suitable for PX4
    absolute-altitude telemetry and commands, whose altitude datum is AMSL.
    """

    origin_longitude_deg, origin_latitude_deg, _ = _require_geodetic_inputs(
        longitude_deg=origin_longitude_deg,
        latitude_deg=origin_latitude_deg,
        altitude_m=0.0,
        allow_poles=False,
    )
    target_longitude_deg, target_latitude_deg, _ = _require_geodetic_inputs(
        longitude_deg=target_longitude_deg,
        latitude_deg=target_latitude_deg,
        altitude_m=0.0,
        allow_poles=False,
    )
    origin_amsl_m = _require_finite("origin_amsl_m", origin_amsl_m)
    target_amsl_m = _require_finite("target_amsl_m", target_amsl_m)
    horizontal = EnuTransform.from_origin(
        longitude_deg=origin_longitude_deg,
        latitude_deg=origin_latitude_deg,
        altitude_m=0.0,
    ).geodetic_to_enu(
        longitude_deg=target_longitude_deg,
        latitude_deg=target_latitude_deg,
        altitude_m=0.0,
    )
    return Vector3(horizontal.x, horizontal.y, target_amsl_m - origin_amsl_m)


def enu_to_ned(enu: Vector3) -> Vector3:
    """Convert ENU ``(east, north, up)`` to NED ``(north, east, down)``."""

    return ENU_TO_NED_ROTATION.apply(enu)


def ned_to_enu(ned: Vector3) -> Vector3:
    """Convert NED ``(north, east, down)`` to ENU ``(east, north, up)``."""

    return NED_TO_ENU_ROTATION.apply(ned)


__all__ = [
    "FrameMathError",
    "ScalarGridSampler",
    "INVERSE_ITERATION_LIMIT",
    "Vector3",
    "RotationMatrix",
    "UnitQuaternion",
    "RigidTransform",
    "EnuTransform",
    "ENU_TO_NED_ROTATION",
    "NED_TO_ENU_ROTATION",
    "WGS84_ECCENTRICITY_SQUARED",
    "WGS84_FLATTENING",
    "WGS84_SEMI_MAJOR_AXIS_M",
    "_canonical_longitude",
    "_normalize_longitude",
    "ecef_to_geodetic",
    "geodetic_to_ecef",
    "quaternion_to_rpy",
    "rpy_to_quaternion",
    "wgs84_amsl_delta_enu",
    "enu_to_ned",
    "ned_to_enu",
]
