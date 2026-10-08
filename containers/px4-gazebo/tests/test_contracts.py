"""Contract tests that protect against false command success and wire corruption."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from service.commands import Commands, enu_to_wgs84
from service.rpc import decode


def wire(**changes):
    request = dict(
        protocol="aeroagentsim.px4/v1",
        major=1,
        minor=0,
        id=1,
        op="advance",
        payload={"to_sim_ns": 2**53 + 1},
    )
    request.update(changes)
    return (json.dumps(request) + "\n").encode()


def test_integer_time_never_rounds_through_float():
    assert decode(wire())["payload"]["to_sim_ns"] == 2**53 + 1


@pytest.mark.parametrize(
    "frame",
    [
        wire()[:-1],
        wire(major=True),
        wire(id=True),
        wire(protocol="aerokernel.rpc"),
        wire(minor=1),
        wire().replace(b'"id": 1', b'"id": 1, "id": 2'),
        wire().replace(str(2**53 + 1).encode(), b"NaN"),
        wire().replace(str(2**53 + 1).encode(), b"1e999"),
    ],
)
def test_invalid_frames_fail(frame):
    with pytest.raises(ValueError):
        decode(frame)


def test_enu_geodetic_origin_and_axis():
    origin = (47.397971057728974, 8.546163739800146, 0)
    assert enu_to_wgs84([0, 0, 0], origin) == pytest.approx(origin, abs=1e-7)
    lat, lon, _ = enu_to_wgs84([50, 0, 10], origin)
    assert abs(lat - origin[0]) < 1e-8
    assert lon > origin[1]


def test_action_ack_alone_never_completes_arm():
    async def scenario():
        async def arm():
            return None  # successful MAVSDK acknowledgment only

        telemetry = SimpleNamespace(
            sessions={"v0": SimpleNamespace(action=SimpleNamespace(arm=arm))},
            versions={"v0": {"armed": 1}},
            cache={"v0": {}},
        )
        commands = Commands(
            SimpleNamespace(origin_wgs84=(47, 8, 0)), telemetry, {"v0": 0}
        )
        result = commands.accept(
            dict(vehicle="v0", action="arm", params={}, command_id="arm1"), 0
        )
        assert result["status"] == "accepted"
        await commands.dispatch(0)
        assert commands.records["arm1"]["task"].done()
        sample = dict(
            vehicle="v0",
            armed=False,
            velocity_enu=[0, 0, 0],
            sim_ns=20_000_000,
            freshness={"mavsdk_receipt_age_wall_ns": {"armed": 0}},
        )
        commands.observe([sample], 20_000_000)
        assert commands.records["arm1"]["status"] == "running"
        sample["armed"] = True
        sample["sim_ns"] = 40_000_000
        commands.observe([sample], 40_000_000)
        assert commands.records["arm1"]["status"] == "running"  # stale cached version
        telemetry.versions["v0"]["armed"] = 2
        sample["sim_ns"] = 60_000_000
        commands.observe([sample], 60_000_000)
        assert commands.records["arm1"]["status"] == "succeeded"
        assert commands.drain()[-1]["sim_ns"] == 60_000_000
        await commands.stop()

    asyncio.run(scenario())


def test_action_rejection_is_failure_even_if_cached_state_matches():
    async def scenario():
        async def arm():
            raise RuntimeError("PX4 COMMAND_DENIED")

        telemetry = SimpleNamespace(
            sessions={"v0": SimpleNamespace(action=SimpleNamespace(arm=arm))},
            versions={"v0": {"armed": 1}},
            cache={"v0": {}},
        )
        commands = Commands(SimpleNamespace(), telemetry, {"v0": 0})
        commands.accept(
            dict(vehicle="v0", action="arm", params={}, command_id="arm1"), 0
        )
        await commands.dispatch(0)
        telemetry.versions["v0"]["armed"] = 2
        commands.observe(
            [dict(vehicle="v0", armed=True, velocity_enu=[0, 0, 0])], 20_000_000
        )
        assert commands.records["arm1"]["status"] == "failed"
        assert "COMMAND_DENIED" in commands.drain()[-1]["reason"]
        await commands.stop()

    asyncio.run(scenario())


def test_stale_receipt_cannot_complete_observed_arm():
    async def scenario():
        async def arm():
            return None

        telemetry = SimpleNamespace(
            sessions={"v0": SimpleNamespace(action=SimpleNamespace(arm=arm))},
            versions={"v0": {"armed": 1}},
            cache={"v0": {}},
        )
        commands = Commands(SimpleNamespace(), telemetry, {"v0": 0})
        commands.accept(
            dict(vehicle="v0", action="arm", params={}, command_id="arm1"), 0
        )
        await commands.dispatch(0)
        sample = dict(
            vehicle="v0",
            armed=False,
            velocity_enu=[0, 0, 0],
            sim_ns=20_000_000,
            freshness={"mavsdk_receipt_age_wall_ns": {"armed": 0}},
        )
        commands.observe([sample], 20_000_000)
        telemetry.versions["v0"]["armed"] = 2
        sample.update(armed=True, sim_ns=40_000_000)
        sample["freshness"]["mavsdk_receipt_age_wall_ns"]["armed"] = 30_000_000_001
        commands.observe([sample], 40_000_000)
        assert commands.records["arm1"]["status"] == "running"
        sample["freshness"]["mavsdk_receipt_age_wall_ns"]["armed"] = 0
        sample["sim_ns"] = 60_000_000
        commands.observe([sample], 60_000_000)
        assert commands.records["arm1"]["status"] == "succeeded"
        await commands.stop()

    asyncio.run(scenario())


def test_vertical_destination_uses_measured_px4_datum():
    async def scenario():
        observed = []

        async def goto(lat, lon, alt, yaw):
            observed.append((lat, lon, alt, yaw))

        telemetry = SimpleNamespace(
            sessions={"v0": SimpleNamespace(action=SimpleNamespace(goto_location=goto))}
        )
        commands = Commands(
            SimpleNamespace(origin_wgs84=(47, 8, 100)), telemetry, {"v0": 140}
        )
        await commands.issue(
            dict(
                vehicle="v0",
                action="goto_location",
                params={"position_enu": [50, 0, 10], "yaw_deg": 0},
            )
        )
        assert observed[0][2] == 150  # Actual PX4 datum, not SDF elevation=100.

    asyncio.run(scenario())


def test_malformed_hello_closes_client_socket(monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "px4_smoke_test", Path(__file__).parents[1] / "smoke.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    closed = []

    class BadHello:
        socket = SimpleNamespace(close=lambda: closed.append(True))

        def request(self, op, payload):
            raise ValueError("bad hello identity")

    monkeypatch.setattr(smoke, "Client", lambda host, port: BadHello())
    with pytest.raises(ValueError, match="bad hello"):
        smoke.ready_client(SimpleNamespace(host="localhost", port=9000))
    assert closed == [True]
