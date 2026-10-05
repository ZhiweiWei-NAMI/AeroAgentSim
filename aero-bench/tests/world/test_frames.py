"""Strict WGS84 <-> ENU conversion and validation tests."""

from __future__ import annotations

import math
import random

import pytest

from pydantic import ValidationError

from aero_bench.world.frames import (
    ENU_FRAME_ID,
    WGS84_FRAME_ID,
    EnuPosition,
    LocalFrameOrigin,
    Wgs84Position,
    enu_to_geodetic,
    geodetic_to_enu,
)
from tests.world.support import frame_origin


# An independent reference implementation used to cross-check the
# production forward conversion.
def _reference_enu_from_geodetic(
    posit: Wgs84Position, origin: LocalFrameOrigin
) -> EnuPosition:
    a = 6_378_137.0
    f = 1.0 / 298.257_223_563
    e2 = f * (2.0 - f)

    def ecef(p: Wgs84Position) -> tuple[float, float, float]:
        lon = math.radians(p.longitude_deg)
        lat = math.radians(p.latitude_deg)
        n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
        return (
            (n + p.altitude_m) * math.cos(lat) * math.cos(lon),
            (n + p.altitude_m) * math.cos(lat) * math.sin(lon),
            (n * (1.0 - e2) + p.altitude_m) * math.sin(lat),
        )

    px, py, pz = ecef(posit)
    ox, oy, oz = ecef(
        Wgs84Position(
            frame_id="WGS84",
            longitude_deg=origin.longitude_deg,
            latitude_deg=origin.latitude_deg,
            altitude_m=origin.altitude_m,
        )
    )
    dx, dy, dz = px - ox, py - oy, pz - oz
    lon = math.radians(origin.longitude_deg)
    lat = math.radians(origin.latitude_deg)
    sl, cl = math.sin(lon), math.cos(lon)
    sp, cp = math.sin(lat), math.cos(lat)
    return EnuPosition(
        frame_id="ENU",
        east_m=-sl * dx + cl * dy,
        north_m=-sp * cl * dx - sp * sl * dy + cp * dz,
        up_m=cp * cl * dx + cp * sl * dy + sp * dz,
    )


ORIGIN = frame_origin()


def test_documented_origin_round_trip_is_exact() -> None:
    back = enu_to_geodetic(
        EnuPosition(frame_id=ENU_FRAME_ID, east_m=0.0, north_m=0.0, up_m=0.0),
        ORIGIN,
    )
    assert back.longitude_deg == pytest.approx(ORIGIN.longitude_deg, abs=1e-12)
    assert back.latitude_deg == pytest.approx(ORIGIN.latitude_deg, abs=1e-12)
    assert back.altitude_m == pytest.approx(ORIGIN.altitude_m, abs=1e-9)


def test_known_displacement_matches_local_approximation() -> None:
    # At the origin, +up must raise altitude; east/north displace by the
    # local metre-per-degree scales to first order (0.5% tolerance for the
    # first-order approximation).
    one_km_up = enu_to_geodetic(
        EnuPosition(frame_id=ENU_FRAME_ID, east_m=0.0, north_m=0.0, up_m=1_000.0),
        ORIGIN,
    )
    assert one_km_up.altitude_m == pytest.approx(ORIGIN.altitude_m + 1_000.0, abs=1e-6)
    assert one_km_up.longitude_deg == pytest.approx(ORIGIN.longitude_deg, abs=1e-9)

    one_km_east = enu_to_geodetic(
        EnuPosition(frame_id=ENU_FRAME_ID, east_m=1_000.0, north_m=0.0, up_m=0.0),
        ORIGIN,
    )
    metres_per_degree_longitude = 111_320.0 * math.cos(
        math.radians(ORIGIN.latitude_deg)
    )
    delta_degrees = one_km_east.longitude_deg - ORIGIN.longitude_deg
    assert delta_degrees * metres_per_degree_longitude == pytest.approx(
        1_000.0, rel=0.005
    )

    one_km_north = enu_to_geodetic(
        EnuPosition(frame_id=ENU_FRAME_ID, east_m=0.0, north_m=1_000.0, up_m=0.0),
        ORIGIN,
    )
    assert one_km_north.latitude_deg - ORIGIN.latitude_deg == pytest.approx(
        1_000.0 / 110_574.0, rel=0.005
    )


def test_round_trip_error_bounded_over_local_volume() -> None:
    samples = (
        (0.0, 0.0, 0.0),
        (1_000.0, -2_000.0, 300.0),
        (-5_000.0, 5_000.0, 1_200.0),
        (12_345.0, -6_789.0, -120.0),
        (0.01, 0.01, 0.01),
    )
    for east, north, up in samples:
        posit = enu_to_geodetic(
            EnuPosition(frame_id=ENU_FRAME_ID, east_m=east, north_m=north, up_m=up),
            ORIGIN,
        )
        restored = geodetic_to_enu(posit, ORIGIN)
        assert restored.east_m == pytest.approx(east, abs=1e-6)
        assert restored.north_m == pytest.approx(north, abs=1e-6)
        assert restored.up_m == pytest.approx(up, abs=1e-6)


def test_geodetic_to_enu_matches_reference_math() -> None:
    posit = Wgs84Position(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=116.4102,
        latitude_deg=39.9207,
        altitude_m=125.0,
    )
    actual = geodetic_to_enu(posit, ORIGIN)
    expected = _reference_enu_from_geodetic(posit, ORIGIN)
    assert actual.east_m == pytest.approx(expected.east_m, abs=1e-9)
    assert actual.north_m == pytest.approx(expected.north_m, abs=1e-9)
    assert actual.up_m == pytest.approx(expected.up_m, abs=1e-9)


def test_wide_geodetic_round_trip_error_bounded() -> None:
    samples = (
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=0.0,
            latitude_deg=0.0,
            altitude_m=0.0,
        ),
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=-179.5,
            latitude_deg=-45.0,
            altitude_m=2_500.0,
        ),
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=179.999,
            latitude_deg=0.001,
            altitude_m=10.0,
        ),
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=-45.0,
            latitude_deg=89.0,
            altitude_m=12_000.0,
        ),
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=135.0,
            latitude_deg=-89.0,
            altitude_m=-500.0,
        ),
    )
    for posit in samples:
        restored = enu_to_geodetic(geodetic_to_enu(posit, ORIGIN), ORIGIN)
        # Angular error bounded by 1e-8 degrees (about 1 mm on the surface);
        # altitude error bounded by 1e-6 m.
        assert restored.longitude_deg == pytest.approx(posit.longitude_deg, abs=1e-8)
        assert restored.latitude_deg == pytest.approx(posit.latitude_deg, abs=1e-8)
        assert restored.altitude_m == pytest.approx(posit.altitude_m, abs=1e-6)


def test_randomized_round_trip_error_contract_holds() -> None:
    """Bounded convergence contract over a randomized global sample.

    The inverse is iterative; every conversion must converge (never raise)
    and satisfy the published error bounds, otherwise this test fails.
    """
    generator = random.Random(20260829)
    for _ in range(500):
        origin = LocalFrameOrigin(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=generator.uniform(-180.0, 180.0),
            latitude_deg=generator.uniform(-89.9, 89.9),
            altitude_m=generator.uniform(-400.0, 9_000.0),
        )
        posit = Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=generator.uniform(-180.0, 180.0),
            latitude_deg=generator.uniform(-89.99, 89.99),
            altitude_m=generator.uniform(-400.0, 12_000.0),
        )
        restored = enu_to_geodetic(geodetic_to_enu(posit, origin), origin)
        assert restored.longitude_deg == pytest.approx(posit.longitude_deg, abs=1e-8)
        assert restored.latitude_deg == pytest.approx(posit.latitude_deg, abs=1e-8)
        assert restored.altitude_m == pytest.approx(posit.altitude_m, abs=1e-6)


def test_antimeridian_crossing_keeps_positions_local() -> None:
    origin = LocalFrameOrigin(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=179.9999,
        latitude_deg=0.0001,
        altitude_m=0.0,
    )
    posit = Wgs84Position(
        frame_id=WGS84_FRAME_ID,
        longitude_deg=-179.9999,
        latitude_deg=0.0002,
        altitude_m=10.0,
    )
    enu = geodetic_to_enu(posit, origin)
    # The positions straddle the antimeridian by about 0.0002 degrees, so
    # the local east offset must be small and positive, never ~world-scale.
    assert 0.0 < enu.east_m < 100.0
    restored = enu_to_geodetic(enu, origin)
    assert restored.longitude_deg == pytest.approx(posit.longitude_deg, abs=1e-9)


def test_pole_origins_are_rejected() -> None:
    with pytest.raises(ValidationError, match="poles"):
        LocalFrameOrigin(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=0.0,
            latitude_deg=90.0,
            altitude_m=0.0,
        )
    with pytest.raises(ValidationError, match="poles"):
        LocalFrameOrigin(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=0.0,
            latitude_deg=-90.0,
            altitude_m=0.0,
        )


@pytest.mark.parametrize("latitude_deg", (90.0, -90.0))
def test_pole_geodetic_positions_are_rejected(latitude_deg: float) -> None:
    with pytest.raises(ValidationError, match="poles"):
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=0.0,
            latitude_deg=latitude_deg,
            altitude_m=0.0,
        )


def test_geodetic_positions_validate_ranges_and_finiteness() -> None:
    with pytest.raises(ValidationError, match="longitude"):
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=180.5,
            latitude_deg=0.0,
            altitude_m=0.0,
        )
    with pytest.raises(ValidationError, match="latitude"):
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=0.0,
            latitude_deg=90.5,
            altitude_m=0.0,
        )
    with pytest.raises(ValidationError, match="finite"):
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=float("nan"),
            latitude_deg=0.0,
            altitude_m=0.0,
        )
    with pytest.raises(ValidationError, match="finite"):
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=0.0,
            latitude_deg=0.0,
            altitude_m=float("inf"),
        )
    with pytest.raises(ValidationError, match="finite"):
        EnuPosition(frame_id=ENU_FRAME_ID, east_m=float("nan"), north_m=0.0, up_m=0.0)


def test_conversion_rejects_non_finite_inputs() -> None:
    with pytest.raises(ValidationError, match="finite"):
        geodetic_to_enu(
            Wgs84Position(
                frame_id=WGS84_FRAME_ID,
                longitude_deg=float("nan"),
                latitude_deg=0.0,
                altitude_m=0.0,
            ),
            ORIGIN,
        )


def test_positions_require_and_validate_frame_ids() -> None:
    # The frame id is a required, closed-set field: a geodetic triple
    # without one, with an unknown one, or with the local frame tag is
    # rejected; the same holds for local ENU triples.
    with pytest.raises(ValidationError):
        Wgs84Position(longitude_deg=0.0, latitude_deg=0.0, altitude_m=0.0)
    with pytest.raises(ValidationError):
        Wgs84Position(
            frame_id="ECEF", longitude_deg=0.0, latitude_deg=0.0, altitude_m=0.0
        )
    with pytest.raises(ValidationError):
        Wgs84Position(
            frame_id=ENU_FRAME_ID, longitude_deg=0.0, latitude_deg=0.0, altitude_m=0.0
        )
    with pytest.raises(ValidationError):
        LocalFrameOrigin(longitude_deg=0.0, latitude_deg=0.0, altitude_m=0.0)
    with pytest.raises(ValidationError):
        EnuPosition(east_m=0.0, north_m=0.0, up_m=0.0)
    with pytest.raises(ValidationError):
        EnuPosition(frame_id=WGS84_FRAME_ID, east_m=0.0, north_m=0.0, up_m=0.0)


def test_conversions_return_frame_tagged_positions() -> None:
    enu = geodetic_to_enu(
        Wgs84Position(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=116.4102,
            latitude_deg=39.9207,
            altitude_m=125.0,
        ),
        ORIGIN,
    )
    assert enu.frame_id == ENU_FRAME_ID
    back = enu_to_geodetic(enu, ORIGIN)
    assert back.frame_id == WGS84_FRAME_ID
