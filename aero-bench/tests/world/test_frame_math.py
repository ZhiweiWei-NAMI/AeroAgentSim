"""Frame math and strict frame contracts for the coordinate foundation."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from aero_bench.world import frame_math
from aero_bench.world.frames import (
    ENU_FRAME_ID,
    ECEF_FRAME_ID,
    NED_FRAME_ID,
    EcefPosition,
    EnuPosition,
    FrameRotation,
    WorldFrameError,
    LocalFrameOrigin,
    UnitQuaternion as FrameUnitQuaternion,
    Vector3 as FrameVector,
    WGS84_FRAME_ID,
    Wgs84Position,
    apply_orientation,
    apply_rigid_transform,
    compose_rigid_transforms,
    compose_unit_quaternions,
    ecef_to_enu,
    ecef_to_geodetic as frame_ecef_to_geodetic,
    enu_to_geodetic,
    enu_to_ecef_position,
    enu_to_ned,
    geodetic_to_ecef as frame_wgs84_to_ecef,
    geodetic_to_enu,
    frame_rotation_from_quaternion,
    frame_unit_quaternion_from_rotation_matrix,
    invert_rigid_transform,
    invert_unit_quaternion,
    ned_to_enu,
    rotate_vector,
    rotate_vector_with_quaternion,
    RigidTransform,
)


def _load_golden() -> dict:
    golden = Path(__file__).parent / "fixtures" / "aero-frame-golden-pyproj-3.6.1.json"
    with golden.open("r", encoding="utf-8") as fd:
        return json.load(fd)


def _surface_error_m(
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
) -> float:
    dlat = math.radians(lat_b - lat_a)
    dlon = math.radians(lon_b - lon_a)
    mean_lat = math.radians((lat_a + lat_b) / 2.0)
    north = frame_math.WGS84_SEMI_MAJOR_AXIS_M * dlat
    east = frame_math.WGS84_SEMI_MAJOR_AXIS_M * math.cos(mean_lat) * dlon
    return math.hypot(north, east)


def test_pyproj_golden_enu_ned_ecef_vectors_match() -> None:
    data = _load_golden()
    origin = frame_math.EnuTransform.from_origin(
        longitude_deg=data["origin"]["longitude_deg"],
        latitude_deg=data["origin"]["latitude_deg"],
        altitude_m=data["origin"]["ellipsoid_height_m"],
    )

    for sample in data["samples"]:
        long_deg, lat_deg, alt_m = sample["wgs84"]
        ecef = frame_math.geodetic_to_ecef(long_deg, lat_deg, alt_m)
        enu = origin.geodetic_to_enu(
            longitude_deg=long_deg,
            latitude_deg=lat_deg,
            altitude_m=alt_m,
        )
        ned = frame_math.enu_to_ned(enu)

        expected_ecef = sample["ecef_m"]
        expected_forward_enu = sample["forward_enu_m"]
        expected_ned = sample["ned_m"]

        assert ecef.x == pytest.approx(expected_ecef[0], abs=1e-6)
        assert ecef.y == pytest.approx(expected_ecef[1], abs=1e-6)
        assert ecef.z == pytest.approx(expected_ecef[2], abs=1e-6)
        assert enu.x == pytest.approx(expected_forward_enu[0], abs=1e-6)
        assert enu.y == pytest.approx(expected_forward_enu[1], abs=1e-6)
        assert enu.z == pytest.approx(expected_forward_enu[2], abs=1e-6)
        assert ned.x == pytest.approx(expected_ned[0], abs=1e-6)
        assert ned.y == pytest.approx(expected_ned[1], abs=1e-6)
        assert ned.z == pytest.approx(expected_ned[2], abs=1e-6)


def test_round_trip_envelope_error_bounded_under_one_centimeter() -> None:
    data = _load_golden()
    origin = frame_math.EnuTransform.from_origin(
        longitude_deg=data["origin"]["longitude_deg"],
        latitude_deg=data["origin"]["latitude_deg"],
        altitude_m=data["origin"]["ellipsoid_height_m"],
    )

    for sample in data["samples"]:
        expected_lon, expected_lat, expected_alt = sample["wgs84"]
        ecef = frame_math.geodetic_to_ecef(expected_lon, expected_lat, expected_alt)
        enu = origin.geodetic_to_enu(
            longitude_deg=expected_lon,
            latitude_deg=expected_lat,
            altitude_m=expected_alt,
        )
        ned = frame_math.enu_to_ned(enu)
        enu_back = frame_math.ned_to_enu(ned)
        ecef_back = origin.ecef_from_enu(enu_back)

        restored = frame_math.ecef_to_geodetic(ecef_back.x, ecef_back.y, ecef_back.z)

        ecef_error = math.dist(ecef.as_tuple(), ecef_back.as_tuple())
        lon_error = _surface_error_m(
            expected_lon,
            expected_lat,
            restored[0],
            restored[1],
        )

        assert ecef_error < 0.01
        assert lon_error < 0.01
        assert restored[2] == pytest.approx(expected_alt, abs=0.01)


def test_wgs84_amsl_delta_does_not_enter_orthometric_height_into_ecef() -> None:
    first = frame_math.wgs84_amsl_delta_enu(
        origin_longitude_deg=120.0,
        origin_latitude_deg=30.0,
        origin_amsl_m=42.0,
        target_longitude_deg=120.0001,
        target_latitude_deg=30.0001,
        target_amsl_m=57.0,
    )
    shifted = frame_math.wgs84_amsl_delta_enu(
        origin_longitude_deg=120.0,
        origin_latitude_deg=30.0,
        origin_amsl_m=1_042.0,
        target_longitude_deg=120.0001,
        target_latitude_deg=30.0001,
        target_amsl_m=1_057.0,
    )

    assert shifted.x == pytest.approx(first.x, abs=1e-12)
    assert shifted.y == pytest.approx(first.y, abs=1e-12)
    assert first.z == pytest.approx(15.0, abs=1e-12)
    assert shifted.z == pytest.approx(15.0, abs=1e-12)
    assert frame_math.ENU_TO_NED_ROTATION.apply(first) == frame_math.enu_to_ned(
        first
    )
    assert frame_math.NED_TO_ENU_ROTATION.apply(
        frame_math.enu_to_ned(first)
    ).as_tuple() == pytest.approx(first.as_tuple())


def test_vector_quaternion_and_rigid_transform_inverses_are_consistent() -> None:
    vector = frame_math.Vector3(1.0, -3.0, 2.0)
    quaternion = frame_math.UnitQuaternion(
        w=0.7071067811865476,
        x=0.0,
        y=0.0,
        z=0.7071067811865476,
    )
    rotation = quaternion.to_rotation_matrix()
    rotated = rotation.apply(vector)
    recovered = rotation.inverse().apply(rotated)

    assert rotated.x == pytest.approx(3.0, abs=1e-12)
    assert rotated.y == pytest.approx(1.0, abs=1e-12)
    assert rotated.z == pytest.approx(2.0, abs=1e-12)
    assert recovered.x == pytest.approx(vector.x, abs=1e-12)
    assert recovered.y == pytest.approx(vector.y, abs=1e-12)
    assert recovered.z == pytest.approx(vector.z, abs=1e-12)

    rigid = frame_math.RigidTransform(
        rotation=rotation,
        translation=frame_math.Vector3(100.0, -50.0, 10.0),
    )
    moved = rigid.apply_position(vector)
    restored = rigid.inverse().apply_position(moved)

    assert moved == frame_math.Vector3(103.0, -49.0, 12.0)
    assert restored.x == pytest.approx(vector.x, abs=1e-12)
    assert restored.y == pytest.approx(vector.y, abs=1e-12)
    assert restored.z == pytest.approx(vector.z, abs=1e-12)

    wrapped_vector = FrameVector(x_m=1.0, y_m=-3.0, z_m=2.0)
    wrapped_quaternion = FrameUnitQuaternion(
        qw=0.7071067811865476,
        qx=0.0,
        qy=0.0,
        qz=0.7071067811865476,
    )
    wrapped_rotation = frame_rotation_from_quaternion(wrapped_quaternion)
    wrapped_rotated = rotate_vector_with_quaternion(wrapped_vector, wrapped_quaternion)
    wrapped_matrix_rotated = rotate_vector(wrapped_vector, wrapped_rotation)
    wrapped_rigid = RigidTransform(
        source_frame_id=ENU_FRAME_ID,
        target_frame_id=ECEF_FRAME_ID,
        rotation=wrapped_rotation,
        translation=FrameVector(x_m=100.0, y_m=-50.0, z_m=10.0),
    )
    wrapped_moved = apply_rigid_transform(wrapped_vector, wrapped_rigid)
    wrapped_restored = apply_rigid_transform(
        wrapped_moved,
        invert_rigid_transform(wrapped_rigid),
    )

    assert wrapped_rotated.x_m == pytest.approx(wrapped_matrix_rotated.x_m, abs=1e-12)
    assert wrapped_rotated.y_m == pytest.approx(wrapped_matrix_rotated.y_m, abs=1e-12)
    assert wrapped_rotated.z_m == pytest.approx(wrapped_matrix_rotated.z_m, abs=1e-12)
    assert wrapped_restored.x_m == pytest.approx(wrapped_vector.x_m, abs=1e-12)
    assert wrapped_restored.y_m == pytest.approx(wrapped_vector.y_m, abs=1e-12)
    assert wrapped_restored.z_m == pytest.approx(wrapped_vector.z_m, abs=1e-12)

    composed_orientation = compose_unit_quaternions(
        wrapped_quaternion, wrapped_quaternion
    )
    math_composed = frame_math.UnitQuaternion(
        w=wrapped_quaternion.qw,
        x=wrapped_quaternion.qx,
        y=wrapped_quaternion.qy,
        z=wrapped_quaternion.qz,
    ).compose(
        frame_math.UnitQuaternion(
            w=wrapped_quaternion.qw,
            x=wrapped_quaternion.qx,
            y=wrapped_quaternion.qy,
            z=wrapped_quaternion.qz,
        )
    )
    assert composed_orientation == FrameUnitQuaternion(
        qw=math_composed.w,
        qx=math_composed.x,
        qy=math_composed.y,
        qz=math_composed.z,
    )
    expected_inverse = frame_math.UnitQuaternion(
        w=wrapped_quaternion.qw,
        x=wrapped_quaternion.qx,
        y=wrapped_quaternion.qy,
        z=wrapped_quaternion.qz,
    ).inverse()
    assert invert_unit_quaternion(wrapped_quaternion) == FrameUnitQuaternion(
        qw=expected_inverse.w,
        qx=expected_inverse.x,
        qy=expected_inverse.y,
        qz=expected_inverse.z,
    )

    mapped_orientation = apply_orientation(wrapped_quaternion, wrapped_rigid)
    mapped_by_matrix = compose_unit_quaternions(
        frame_unit_quaternion_from_rotation_matrix(wrapped_rotation),
        wrapped_quaternion,
    )
    mapped_orientation_again = apply_orientation(
        mapped_orientation,
        invert_rigid_transform(wrapped_rigid),
    )
    assert mapped_orientation == mapped_by_matrix
    assert mapped_orientation_again.qw == pytest.approx(
        wrapped_quaternion.qw, abs=1e-12
    )
    assert mapped_orientation_again.qx == pytest.approx(
        wrapped_quaternion.qx, abs=1e-12
    )
    assert mapped_orientation_again.qy == pytest.approx(
        wrapped_quaternion.qy, abs=1e-12
    )
    assert mapped_orientation_again.qz == pytest.approx(
        wrapped_quaternion.qz, abs=1e-12
    )


def test_strict_rejection_paths_cover_invalid_inputs() -> None:
    with pytest.raises(frame_math.FrameMathError, match="finite"):
        frame_math.geodetic_to_ecef(math.nan, 0.0, 0.0)

    with pytest.raises(frame_math.FrameMathError, match="did not converge"):
        frame_math.ecef_to_geodetic(1.0, 2.0, 3.0, max_iterations=1)

    with pytest.raises(WorldFrameError, match="polar axis"):
        frame_ecef_to_geodetic(
            EcefPosition(frame_id=ECEF_FRAME_ID, x_m=0.0, y_m=0.0, z_m=1.0)
        )

    with pytest.raises(frame_math.FrameMathError, match="orthogonal"):
        frame_math.RotationMatrix(
            rows=((1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        )

    with pytest.raises(frame_math.FrameMathError, match="zero"):
        frame_math.UnitQuaternion(0.0, 0.0, 0.0, 0.0)

    with pytest.raises(ValidationError, match="frame_id"):
        EcefPosition(frame_id=WGS84_FRAME_ID, x_m=0.0, y_m=0.0, z_m=0.0)

    with pytest.raises(ValidationError, match="unit"):
        FrameUnitQuaternion(qw=0.1, qx=0.1, qy=0.1, qz=0.1)

    with pytest.raises(ValidationError, match="orthogonal"):
        FrameRotation(matrix=((1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)))


def test_math_rejects_unsupported_max_iteration_types() -> None:
    with pytest.raises(WorldFrameError, match="positive integer"):
        frame_math.ecef_to_geodetic(1.0, 2.0, 3.0, max_iterations=False)

    with pytest.raises(WorldFrameError, match="positive integer"):
        frame_math.ecef_to_geodetic(1.0, 2.0, 3.0, max_iterations=0.5)


def test_math_accepts_near_poles_and_canonicalizes_longitude_edges() -> None:
    near_north = frame_math.geodetic_to_ecef(12.0, 89.99999995, 10.0)
    on_pole = frame_math.geodetic_to_ecef(12.0, 90.0, 10.0)
    wrapped_plus = frame_math.geodetic_to_ecef(180.0, 0.0, 1.0)
    wrapped_minus = frame_math.geodetic_to_ecef(-180.0, 0.0, 1.0)

    assert math.isfinite(near_north.x)
    assert math.isfinite(on_pole.x)
    assert abs(on_pole.x) < 1e-9
    assert abs(on_pole.y) < 1e-9
    assert wrapped_plus.x == pytest.approx(wrapped_minus.x, abs=1e-9)
    assert wrapped_plus.y == pytest.approx(wrapped_minus.y, abs=1e-9)
    assert wrapped_plus.z == pytest.approx(wrapped_minus.z, abs=1e-9)


def test_quaternion_sign_canonicalized_from_rotation_matrix() -> None:
    original = frame_math.UnitQuaternion(0.0, 0.0, 0.0, -1.0)
    matrix = original.to_rotation_matrix()
    reconstructed = frame_math.UnitQuaternion.from_rotation_matrix(matrix)

    assert reconstructed.z >= 0.0
    assert reconstructed.w == pytest.approx(0.0, abs=1e-12)


def test_math_enutransform_is_compiled_from_origin_only() -> None:
    with pytest.raises(TypeError):
        frame_math.EnuTransform(
            0.0,
            0.0,
            0.0,
            frame_math.Vector3(0.0, 0.0, 0.0),
            frame_math.RotationMatrix(
                rows=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            ),
            frame_math.RotationMatrix(
                rows=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            ),
        )


def test_rigid_transform_composition_tracks_frame_ids() -> None:
    identity = FrameUnitQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0)
    identity_rotation = frame_rotation_from_quaternion(identity)

    world_to_enu = RigidTransform(
        source_frame_id=ECEF_FRAME_ID,
        target_frame_id=ENU_FRAME_ID,
        rotation=identity_rotation,
        translation=FrameVector(x_m=1.0, y_m=2.0, z_m=3.0),
    )
    enu_to_body = RigidTransform(
        source_frame_id=ENU_FRAME_ID,
        target_frame_id="BODY",
        rotation=identity_rotation,
        translation=FrameVector(x_m=-1.0, y_m=-2.0, z_m=-3.0),
    )
    body_transform = compose_rigid_transforms(world_to_enu, enu_to_body)
    inverse_transform = invert_rigid_transform(body_transform)

    assert body_transform.source_frame_id == ECEF_FRAME_ID
    assert body_transform.target_frame_id == "BODY"
    assert inverse_transform.source_frame_id == "BODY"
    assert inverse_transform.target_frame_id == ECEF_FRAME_ID

    with pytest.raises(WorldFrameError):
        compose_rigid_transforms(enu_to_body, world_to_enu)


def test_existing_frame_api_contracts_continue_to_work() -> None:
    origin = LocalFrameOrigin(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=8.545594,
        latitude_deg=47.397742,
        altitude_m=535.0,
    )
    wgs84 = Wgs84Position(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=8.564330438547662,
        latitude_deg=47.4104593429827,
        altitude_m=655.313452873379,
    )

    enu = geodetic_to_enu(wgs84, origin)
    back = enu_to_geodetic(enu, origin)
    ecef = frame_wgs84_to_ecef(wgs84)
    restored = frame_ecef_to_geodetic(ecef)
    alt_ref = ecef_to_enu(ecef, origin)
    world_ref = enu_to_ecef_position(alt_ref, origin)

    assert enu.frame_id == ENU_FRAME_ID
    assert back.frame_id == WGS84_FRAME_ID
    assert ecef.frame_id == ECEF_FRAME_ID
    assert restored.frame_id == WGS84_FRAME_ID
    assert world_ref.frame_id == ECEF_FRAME_ID

    assert restored.altitude_m == pytest.approx(wgs84.altitude_m, abs=1e-9)
    assert back.longitude_deg == pytest.approx(wgs84.longitude_deg, abs=1e-8)
    assert back.latitude_deg == pytest.approx(wgs84.latitude_deg, abs=1e-8)


def test_api_ned_interop_round_trips_with_enu() -> None:
    enu = EnuPosition(frame_id=ENU_FRAME_ID, east_m=12.5, north_m=-18.25, up_m=35.0)
    ned = enu_to_ned(enu)
    restored = ned_to_enu(ned)

    assert ned.frame_id == NED_FRAME_ID
    assert restored.frame_id == ENU_FRAME_ID
    assert restored == EnuPosition(
        frame_id=ENU_FRAME_ID,
        east_m=12.5,
        north_m=-18.25,
        up_m=35.0,
    )
