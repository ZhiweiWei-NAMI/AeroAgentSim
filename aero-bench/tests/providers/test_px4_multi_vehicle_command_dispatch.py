from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aero_bench.providers import rpc
from aero_bench.world import frame_math, workload_scenario


sys.modules["rpc"] = rpc
sys.modules["aero_frame_math"] = frame_math
sys.modules["workload_scenario"] = workload_scenario
_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_service_multi_test", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


def _record(vehicle_id: str, command_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        vehicle_id=vehicle_id,
        command_id=command_id,
        tool_id="flight.hold",
        arguments={"vehicle_id": vehicle_id},
        stage="staged",
        applied_at=None,
        command_audit_status=None,
        command_audit=None,
        last_state_digest=None,
        last_metrics={},
        failure_latched=False,
        deadline_sim_time_ns=10_000,
        settle_start=None,
        settle_end=None,
        settle_count=0,
    )


class _BarrierStack:
    def __init__(self) -> None:
        self.applied: list[str] = []
        self.dispatch_started = asyncio.Event()
        self.release_commands = asyncio.Event()

    async def multi_step(
        self,
        iterations: int,
        *,
        execution_started: asyncio.Event | None = None,
        command_dispatched: asyncio.Event | None = None,
    ) -> None:
        assert iterations == 2
        assert execution_started is not None
        assert command_dispatched is not None
        execution_started.set()
        await command_dispatched.wait()
        await asyncio.sleep(0)

    async def apply_command(self, request: dict[str, Any]) -> None:
        arguments = request["arguments"]
        vehicle_id = next(
            item["value"]
            for item in arguments
            if item["name"] == "vehicle_id"
        )
        self.applied.append(str(vehicle_id))
        self.dispatch_started.set()
        await self.release_commands.wait()

    async def wait_for_telemetry(self) -> None:
        return None


class _SelectiveFailureStack(_BarrierStack):
    def __init__(self, fail_vehicle_id: str) -> None:
        super().__init__()
        self.fail_vehicle_id = fail_vehicle_id

    async def apply_command(self, request: dict[str, Any]) -> None:
        arguments = request["arguments"]
        vehicle_id = next(
            item["value"]
            for item in arguments
            if item["name"] == "vehicle_id"
        )
        await super().apply_command(request)
        if vehicle_id == self.fail_vehicle_id:
            raise _SERVICE.Px4CommandPhaseError(
                last_success="accepted",
                detail=f"simulated failure for {vehicle_id}",
            )




def test_different_vehicles_can_stage_at_the_same_barrier() -> None:
    async def run() -> None:
        service = object.__new__(_SERVICE.Px4GazeboService)
        service._config = SimpleNamespace(
            provider_id="flight",
            run_id="a" * 64,
            vehicles=(
                {"vehicle_id": "uav.1", "system_id": 1},
                {"vehicle_id": "uav.2", "system_id": 2},
            ),
            physical_completion_policy={"physical_sim_timeout_ns": 100},
            inspections=(),
            flight_observations=(),
            urban_camera_observations=(
                {
                    "agent_id": "uav.policy.02",
                    "observation_id": "observation.uav.2.telemetry",
                    "observation_kind": "telemetry",
                    "vehicle_id": "uav.2",
                },
            ),
        )
        service._current = (0, 0)
        service._lifecycle = _SERVICE.ProviderLifecycle.BARRIER_PAUSED
        service._command_records_by_vehicle = {}
        service._command_record = None
        service._command_ids = set()
        service._finalization = None
        service._observation_envelopes = {}
        service._stack = SimpleNamespace(
            accept_command=lambda request: asyncio.sleep(0),
        )

        async def fresh_physical_state(
            *, vehicle_id: str, target: tuple[int, int]
        ) -> SimpleNamespace:
            return SimpleNamespace(sim_time_ns=target[1], pose={})

        service._fresh_physical_state = fresh_physical_state

        async def observe_telemetry(**kwargs: object) -> dict[str, object]:
            assert service._lifecycle == _SERVICE.ProviderLifecycle.ACTION_STAGED
            assert kwargs["agent_id"] == "uav.policy.02"
            assert kwargs["requested_at"] == (0, 0)
            return {"observation": {"kind": "authoritative-telemetry"}}

        service._observe_urban_telemetry = observe_telemetry

        def command(command_id: str, vehicle_id: str) -> dict[str, object]:
            return {
                "provider_id": "flight",
                "run_id": "a" * 64,
                "session_token": "9" * 64,
                "request": {
                    "run_id": "a" * 64,
                    "command_id": command_id,
                    "tool_id": "flight.hold",
                    "arguments": [{"name": "vehicle_id", "value": vehicle_id}],
                },
            }

        first = await service._dispatch("command", command("command.one", "uav.1"))
        observation = await service._dispatch(
            "observe",
            {
                "provider_id": "flight",
                "run_id": "a" * 64,
                "session_token": "9" * 64,
                "agent_id": "uav.policy.02",
                "observation_id": "observation.uav.2.telemetry",
                "requested_at": {"tick": 0, "sim_time_ns": 0},
            },
        )
        second = await service._dispatch("command", command("command.two", "uav.2"))

        assert observation == {
            "observation": {"kind": "authoritative-telemetry"}
        }
        assert [item["phase"] for item in first["receipts"]] == [
            "received",
            "accepted",
        ]
        assert [item["phase"] for item in second["receipts"]] == [
            "received",
            "accepted",
        ]
        assert set(service._command_records_by_vehicle) == {"uav.1", "uav.2"}
        assert service._command_record is None
        assert service._lifecycle == _SERVICE.ProviderLifecycle.ACTION_STAGED

        rejected = await service._dispatch(
            "command", command("command.three", "uav.1")
        )
        assert [item["phase"] for item in rejected["receipts"]] == [
            "received",
            "failed",
        ]

    asyncio.run(run())


def test_two_vehicles_dispatch_from_one_paused_barrier() -> None:
    async def run() -> None:
        service = object.__new__(_SERVICE.Px4GazeboService)
        stack = _BarrierStack()
        service._stack = stack
        service._command_records_by_vehicle = {
            "uav.1": _record("uav.1", "command.one"),
            "uav.2": _record("uav.2", "command.two"),
        }
        service._command_record = None
        service._lifecycle = _SERVICE.ProviderLifecycle.ACTION_STAGED
        # Non-horizon identity: no urban engineering task, unbounded steps.
        service._workload_identity = SimpleNamespace(
            scenario=SimpleNamespace(scenario={}),
            clock_max_steps=None,
            clock_step_ns=10,
        )

        async def fresh_physical_state(
            *, vehicle_id: str, target: tuple[int, int]
        ) -> SimpleNamespace:
            return SimpleNamespace(
                vehicle_id=vehicle_id,
                state_digest=f"state.{vehicle_id}.{target[0]}",
            )

        service._fresh_physical_state = fresh_physical_state
        service._drain_prior_command_audit_records = lambda vehicle_id: None
        service._correlate_command_audit = lambda **kwargs: (
            _SERVICE.COMMAND_AUDIT_APPLIED_STATUS,
            {},
            None,
        )
        service._evaluate_physical_completion = lambda **kwargs: (False, {}, {})
        service._deferred_command_event = lambda **kwargs: {
            "command_id": kwargs["command_id"],
            "phase": kwargs["phase"],
        }
        service._deferred_command_proof_event = lambda **kwargs: {
            "command_id": kwargs["record"].command_id,
            "phase": kwargs["phase"],
        }
        config = SimpleNamespace(physical_completion_policy={})

        advance = asyncio.create_task(
            service._advance_with_pending_commands(
                config=config,
                iterations=2,
                target=(1, 10),
            )
        )
        await stack.dispatch_started.wait()
        await asyncio.sleep(0)
        assert set(stack.applied) == {"uav.1", "uav.2"}
        stack.release_commands.set()
        events = await advance

        assert len(events) == 4
        assert [
            record.stage for record in service._command_records_by_vehicle.values()
        ] == ["applied", "applied"]
        assert service._command_record is None
        assert service._lifecycle == _SERVICE.ProviderLifecycle.BARRIER_PAUSED

    asyncio.run(run())


def test_one_vehicle_failure_does_not_latch_the_other() -> None:
    async def run() -> None:
        service = object.__new__(_SERVICE.Px4GazeboService)
        stack = _SelectiveFailureStack("uav.1")
        service._stack = stack
        service._command_records_by_vehicle = {
            "uav.1": _record("uav.1", "command.one"),
            "uav.2": _record("uav.2", "command.two"),
        }
        service._command_record = None
        service._lifecycle = _SERVICE.ProviderLifecycle.ACTION_STAGED
        # Non-horizon identity: no urban engineering task, unbounded steps.
        service._workload_identity = SimpleNamespace(
            scenario=SimpleNamespace(scenario={}),
            clock_max_steps=None,
            clock_step_ns=10,
        )

        async def fresh_physical_state(
            *, vehicle_id: str, target: tuple[int, int]
        ) -> SimpleNamespace:
            return SimpleNamespace(
                vehicle_id=vehicle_id,
                state_digest=f"state.{vehicle_id}.{target[0]}",
            )

        service._fresh_physical_state = fresh_physical_state
        service._drain_prior_command_audit_records = lambda vehicle_id: None
        service._correlate_command_audit = lambda **kwargs: (
            _SERVICE.COMMAND_AUDIT_APPLIED_STATUS,
            {},
            None,
        )
        service._evaluate_physical_completion = lambda **kwargs: (False, {}, {})
        service._deferred_command_event = lambda **kwargs: {
            "command_id": kwargs["command_id"],
            "phase": kwargs["phase"],
        }
        service._deferred_command_proof_event = lambda **kwargs: {
            "command_id": kwargs["record"].command_id,
            "phase": kwargs["phase"],
        }
        config = SimpleNamespace(physical_completion_policy={})

        advance = asyncio.create_task(
            service._advance_with_pending_commands(
                config=config,
                iterations=2,
                target=(1, 10),
            )
        )
        await stack.dispatch_started.wait()
        stack.release_commands.set()
        events = await advance

        assert set(stack.applied) == {"uav.1", "uav.2"}
        assert service._command_records_by_vehicle["uav.1"].stage == "failed"
        assert service._command_records_by_vehicle["uav.1"].failure_latched is True
        assert service._command_records_by_vehicle["uav.2"].stage == "applied"
        assert service._command_records_by_vehicle["uav.2"].failure_latched is False
        assert {event["phase"] for event in events} == {"failed", "applied"}

    asyncio.run(run())
