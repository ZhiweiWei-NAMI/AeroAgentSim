"""Native-time link maintenance must not alter PX4 failsafes or infer flight state."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from service.heartbeat import Heartbeat
from service.runtime import Runtime


def message_type(*args):
    return SimpleNamespace(name=args[0], fields=json.loads(args[5]), ids=args[1:5])


def test_simulation_cadence_sends_real_sdk_gcs_heartbeats_to_all_vehicles():
    async def scenario():
        sent = []

        async def send_a(message):
            sent.append(("a", message))

        async def send_b(message):
            sent.append(("b", message))

        heartbeat = Heartbeat(
            {
                "a": SimpleNamespace(
                    mavlink_direct=SimpleNamespace(send_message=send_a)
                ),
                "b": SimpleNamespace(
                    mavlink_direct=SimpleNamespace(send_message=send_b)
                ),
            },
            message_type,
        )
        await heartbeat.pulse(0)
        await heartbeat.pulse(0)
        await heartbeat.pulse(999_999_999)
        assert [v for v, m in sent] == ["a", "b"]
        await heartbeat.pulse(1_000_000_000)
        assert [v for v, m in sent] == ["a", "b", "a", "b"]
        assert heartbeat.next_ns == 2_000_000_000
        for _, m in sent:
            assert m.name == "HEARTBEAT"
            assert m.fields == {
                "type": 6,
                "autopilot": 8,
                "base_mode": 0,
                "custom_mode": 0,
                "system_status": 4,
                "mavlink_version": 3,
            }
            assert m.ids == (
                0,
                0,
                0,
                0,
            )  # SDK chooses configured source; broadcast payload.

    asyncio.run(scenario())


def test_heartbeat_send_failure_prevents_native_integration_and_frontier_commit():
    async def scenario():
        sent = []

        async def failed_send(message):
            sent.append(message)
            raise RuntimeError("native MAVSDK send rejected")

        telemetry = SimpleNamespace(check=lambda: None)
        telemetry.heartbeat = Heartbeat(
            {
                "v0": SimpleNamespace(
                    mavlink_direct=SimpleNamespace(send_message=failed_send)
                )
            },
            message_type,
        )
        runtime = Runtime()
        runtime.config = SimpleNamespace(physics_step_ns=4_000_000)
        runtime.processes = SimpleNamespace(check=lambda: None)
        runtime.telemetry = telemetry

        async def dispatch(sim_ns):
            assert sim_ns == 0

        runtime.commands = SimpleNamespace(dispatch=dispatch)
        runtime.gazebo = SimpleNamespace(sim_ns=10_000_000_000)  # No step method.
        with pytest.raises(RuntimeError, match="native MAVSDK send rejected"):
            await runtime.advance({"to_sim_ns": 20_000_000})
        assert len(sent) == 1
        assert telemetry.heartbeat.next_ns == 0
        assert runtime.sim_ns == 0

    asyncio.run(scenario())
