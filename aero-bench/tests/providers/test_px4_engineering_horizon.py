"""Unit-only horizon lifecycle/proof wiring, not simulator execution evidence."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from aero_bench.providers.px4_gazebo.provider import (
    COMMAND_HORIZON_INTERRUPTED_DETAIL,
    Px4GazeboProvider,
    Px4ProviderError,
    _DeferredCommandState,
)
from aero_bench.runtime.contracts import ProviderEvent, SimulationTime, StepReceipt
from tests.providers import test_px4_physical_completion as native
from tests.providers import test_px4_provider_command_lifecycle as host


class _PausedStack:
    def __init__(self):
        self.steps = 0

    async def multi_step(self, iterations, *, execution_started=None, command_dispatched=None):
        self.steps += 1
        if execution_started is not None:
            execution_started.set()
            await command_dispatched.wait()

    async def apply_command(self, request):
        pass

    async def wait_for_telemetry(self):
        pass


def _state(tick):
    return native._parsed_state(
        tick=tick, pose=(0.0, 0.0, 0.1), altitude_m=100.0,
        velocity_ned=(0.0, 0.0, 0.0), mode="TAKEOFF", armed=True, contact=False,
    )


def _service(tmp_path, *, stages=("applied",), package_id="urban.uav-recovery-demo.v1", max_steps=3):
    service = object.__new__(native._SERVICE.Px4GazeboService)
    identity = native._identity(tmp_path)
    service._workload_identity = replace(
        identity,
        clock_max_steps=max_steps,
        scenario=replace(identity.scenario, scenario={"task": {"package_id": package_id}}),
    )
    service._stack = _PausedStack()
    records = {}
    for index, stage in enumerate(stages, 1):
        record = native._record(
            tool_id="flight.takeoff", baseline=_state(0),
            arguments={"vehicle_id": f"uav.{index}", "altitude_m": 3.0},
        )
        record.vehicle_id = f"uav.{index}"
        record.command_id = f"command.{index}"
        record.stage = stage
        if stage == "staged":
            record.applied_at = None
        records[record.vehicle_id] = record
    service._command_records_by_vehicle = records
    service._command_record = None
    service._lifecycle = (
        native._SERVICE.ProviderLifecycle.ACTION_STAGED
        if "staged" in stages else native._SERVICE.ProviderLifecycle.BARRIER_PAUSED
    )

    async def fresh_state(*, vehicle_id, target):
        return replace(_state(target[0]), vehicle_id=vehicle_id)

    service._fresh_physical_state = fresh_state
    service._drain_prior_command_audit_records = lambda vehicle_id: None
    service._correlate_command_audit = lambda *, config, record: (
        native._SERVICE.COMMAND_AUDIT_APPLIED_STATUS, record.command_audit, None,
    )
    return service


def _host_provider(stage, *, max_steps=3, package_id="urban.uav-recovery-demo.v1"):
    provider = object.__new__(Px4GazeboProvider)
    provider._config = SimpleNamespace(
        provider_id="flight", vehicles=(SimpleNamespace(vehicle_id="uav.1", system_id=1),),
    )
    provider._scenario = SimpleNamespace(task=SimpleNamespace(package_id=package_id))
    provider._clock = SimpleNamespace(max_steps=max_steps, step_ns=native.STEP_LENGTH_NS)
    provider._failure_latched_by_vehicle = set()
    provider._consumed_terminal_ack_seq_by_vehicle = {}
    provider._pending_commands_by_vehicle = {
        "uav.1": _DeferredCommandState(
            command_id="command.1", tool_id="flight.takeoff", vehicle_id="uav.1",
            stage="accepted" if stage == "staged" else "applied",
            accepted_at=SimulationTime(tick=0, sim_time_ns=0),
            applied_at=None if stage == "staged" else SimulationTime(tick=1, sim_time_ns=10),
            audit_digest=None if stage == "staged" else host._canonical_digest(native._decoded_command_audit("flight.takeoff")),
        ),
    }
    return provider


def _advance(service):
    events = asyncio.run(service._advance_with_pending_commands(
        config=native._prepared_config(), iterations=2, target=(3, 30),
    ))
    return StepReceipt(
        run_id=native.RUN_ID, provider_id="flight",
        reached=SimulationTime(tick=3, sim_time_ns=30), state_digest="d" * 64,
        events=tuple(ProviderEvent.model_validate(event) for event in events),
    )


@pytest.mark.parametrize("stage", ["applied", "staged"])
def test_native_horizon_failure_proof_clears_host_pending_without_success(stage, tmp_path):
    service = _service(tmp_path, stages=(stage,))
    receipt = _advance(service)
    payloads = [{value.name: value.value for value in event.payload} for event in receipt.events]
    assert [payload["phase"] for payload in payloads] == ["failed", "failed"]
    assert payloads[0]["detail"] == COMMAND_HORIZON_INTERRUPTED_DETAIL
    proof = json.loads(payloads[1]["proof_json"])
    assert proof["current_tick"] == 3
    assert proof["current_sim_time_ns"] == 30
    assert proof["current_state_digest"] == _state(3).state_digest
    assert not service._command_records_by_vehicle
    assert service._stack.steps == 1
    provider = _host_provider(stage)
    provider._validate_deferred_command_events(receipt)
    assert not provider._pending_commands_by_vehicle
    assert not provider._failure_latched_by_vehicle


def test_horizon_processes_previously_applied_and_newly_staged_vehicles(tmp_path):
    service = _service(tmp_path, stages=("applied", "staged"))
    receipt = _advance(service)
    assert len(receipt.events) == 4
    assert not service._command_records_by_vehicle
    assert service._stack.steps == 1


@pytest.mark.parametrize("package_id,max_steps", [
    ("urban.uav-recovery-demo.v1", 3000),
    ("urban.uav-recovery-demo.v1", 4),
    ("inspection.infrastructure.v2", 3),
])
def test_horizon_interruption_is_not_a_formal_or_early_failure_exemption(package_id, max_steps, tmp_path):
    receipt = _advance(_service(tmp_path))
    provider = _host_provider("applied", max_steps=max_steps, package_id=package_id)
    with pytest.raises(Px4ProviderError, match="outside the urban engineering horizon"):
        provider._validate_deferred_command_events(receipt)
    assert provider._pending_commands_by_vehicle
    service = _service(tmp_path, package_id=package_id, max_steps=max_steps)
    assert not _advance(service).events
    assert service._command_records_by_vehicle


def test_proven_physical_completion_still_wins_at_horizon(tmp_path):
    service = _service(tmp_path)
    record = service._command_records_by_vehicle["uav.1"]
    record.settle_start = (1, 10)
    record.settle_end = (2, 20)
    record.settle_count = 2
    record.last_state_digest = _state(2).state_digest

    async def settled_state(*, vehicle_id, target):
        return native._parsed_state(
            tick=target[0], pose=(0.0, 0.0, 3.1), altitude_m=103.0,
            velocity_ned=(0.0, 0.0, 0.0), mode="TAKEOFF", armed=True, contact=False,
        )

    service._fresh_physical_state = settled_state
    receipt = _advance(service)
    phases = [dict((item.name, item.value) for item in event.payload)["phase"] for event in receipt.events]
    assert phases == ["completed", "completed"]
    assert not service._command_records_by_vehicle
    provider = _host_provider("applied")
    provider._validate_deferred_command_events(receipt)
    assert not provider._failure_latched_by_vehicle


def test_real_physical_timeout_at_horizon_retains_failure_latch(tmp_path):
    service = _service(tmp_path)
    record = service._command_records_by_vehicle["uav.1"]
    record.deadline_sim_time_ns = 30
    receipt = _advance(service)
    assert record.failure_latched
    assert service._command_records_by_vehicle
    provider = _host_provider("applied")
    provider._validate_deferred_command_events(receipt)
    assert provider._failure_latched_by_vehicle == {"uav.1"}


def test_horizon_marker_does_not_accept_a_missing_application_audit(tmp_path):
    receipt = _advance(_service(tmp_path, stages=("staged",)))
    events = list(receipt.events)
    payload = {item.name: item.value for item in events[1].payload}
    proof = json.loads(payload["proof_json"])
    proof.update(ack_audit_status=native._SERVICE.COMMAND_AUDIT_MISSING_STATUS, decoded_command_audit=None)
    events[1] = events[1].model_copy(update={"payload": tuple(
        item.model_copy(update={"value": json.dumps(proof, sort_keys=True, separators=(",", ":"))})
        if item.name == "proof_json" else item for item in events[1].payload
    )})
    provider = _host_provider("staged")
    with pytest.raises(Px4ProviderError, match="must prove command application"):
        provider._validate_deferred_command_events(receipt.model_copy(update={"events": tuple(events)}))
    assert provider._pending_commands_by_vehicle
