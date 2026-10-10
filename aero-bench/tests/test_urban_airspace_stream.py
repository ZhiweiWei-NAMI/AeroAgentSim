"""Airspace stream unit fixtures; no Gazebo process or formal run is simulated."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest

from aero_bench.runtime.contracts import ProviderEvent, SimulationTime
from aero_bench.tasks.urban_recovery_demo.contracts import AirspaceTransition, STEP_NS
from aero_bench.tasks.urban_recovery_demo.runtime_hook import UrbanRecoveryRuntimeHook, UrbanRecoveryRuntimeHookError
from tests.providers.test_px4_gazebo_config import _SERVICE, _airspace
from tools.build_urban_recovery_demo import _demo_package


ORIGIN = 5_000_000_000


def _stack(tmp_path):
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(
        airspace_transition=_airspace(incident_vehicle="uav.01", region_id="region.no-fly.recovery"),
        vehicles=tuple({"vehicle_id": name, "gazebo_model_name": name} for name in ("uav.01", "uav.02")),
        provider_id="flight", step_length_ns=STEP_NS, physics_step_ns=4_000_000,
        command_timeout_ms=10,
    )
    stack._airspace_topic_active = True
    stack._time_origin_ns = ORIGIN
    return stack


def _message(stack, logical_time, *, transition="none", sequence=0, inside=False, vehicle="uav.01", **changes):
    config = stack._config.airspace_transition
    return SimpleNamespace(data=json.dumps({
        "schema_version": "aero-bench.gazebo-airspace-transition/v1", "source": "gazebo.system",
        "sequence": sequence, "transition": transition, "airspace_state": "inside" if inside else "outside",
        "sim_time_ns": ORIGIN + logical_time,
        "world_id": config["world_id"], "world_digest": config["world_digest"],
        "region_id": config["region_id"], "region_digest": config["region_digest"],
        "incident_vehicle": vehicle,
        "position_enu_m": {"east": 0.0 if inside else 20.0, "north": 0.0, "up": 10.0},
        **changes,
    }))


def test_zero_sequence_pulses_do_not_regress_the_transition_sequence(tmp_path):
    stack = _stack(tmp_path)
    stack._on_airspace_topic_message(_message(stack, 4_000_000, transition="entered", sequence=1, inside=True))
    stack._on_airspace_topic_message(_message(stack, STEP_NS, inside=True))
    assert stack._airspace_failure is None
    assert len(stack._airspace_events) == 1
    sample = stack.airspace_observation(vehicle_id="uav.01", requested_at=(1, STEP_NS))
    assert sample["transition"] == "entered"
    assert sample["transition_sequence"] == 1
    assert sample["transition_engine_sim_time_ns"] == ORIGIN + 4_000_000
    assert sample["engine_sim_time_ns"] == ORIGIN + STEP_NS
    assert sample["logical_origin_engine_ns"] == ORIGIN
    stack._on_airspace_topic_message(_message(stack, 2 * STEP_NS, inside=True))
    sample = stack.airspace_observation(vehicle_id="uav.01", requested_at=(2, 2 * STEP_NS))
    assert sample["transition"] == "none"
    assert sample["transition_engine_sim_time_ns"] is None
    assert sample["transition_sequence"] == 0


def test_both_declared_uavs_have_independent_engine_sequence_spaces(tmp_path):
    stack = _stack(tmp_path)
    for vehicle in ("uav.01", "uav.02"):
        stack._on_airspace_topic_message(_message(stack, 4_000_000, transition="entered", sequence=1, inside=True, vehicle=vehicle))
        stack._on_airspace_topic_message(_message(stack, STEP_NS, inside=True, vehicle=vehicle))
    assert stack._airspace_failure is None
    asyncio.run(stack.wait_for_airspace(requested_at=(1, STEP_NS)))
    events = stack.airspace_transition_events(requested_at=(1, STEP_NS))
    assert len(events) == 2
    assert {item["incident_vehicle"] for item in events} == {"uav.01", "uav.02"}
    assert stack.airspace_transition_events(requested_at=(1, STEP_NS)) == ()


@pytest.mark.parametrize("change", [
    {"vehicle": "ungranted.model"}, {"source": "policy.generated"},
    {"world_digest": "e" * 64}, {"region_digest": "e" * 64},
    {"sequence": 1},
])
def test_foreign_or_malformed_stream_is_terminal(tmp_path, change):
    stack = _stack(tmp_path)
    stack._on_airspace_topic_message(_message(stack, STEP_NS, **change))
    assert stack._airspace_failure is not None
    with pytest.raises(_SERVICE.Px4ServiceError, match="stream failed"):
        stack.airspace_observation(vehicle_id="uav.01", requested_at=(1, STEP_NS))


@pytest.mark.parametrize("sequence", [1, 3])
def test_event_sequence_cannot_repeat_or_skip(tmp_path, sequence):
    stack = _stack(tmp_path)
    stack._on_airspace_topic_message(_message(stack, 4_000_000, transition="entered", sequence=1, inside=True))
    stack._on_airspace_topic_message(_message(stack, 8_000_000, transition="exited", sequence=sequence))
    assert stack._airspace_failure is not None


def test_multiple_transitions_in_one_window_cannot_be_silently_collapsed(tmp_path):
    stack = _stack(tmp_path)
    stack._on_airspace_topic_message(_message(stack, 4_000_000, transition="entered", sequence=1, inside=True))
    stack._on_airspace_topic_message(_message(stack, 8_000_000, transition="exited", sequence=2))
    stack._on_airspace_topic_message(_message(stack, STEP_NS))
    with pytest.raises(_SERVICE.Px4ServiceError, match="cannot be collapsed"):
        stack.airspace_observation(vehicle_id="uav.01", requested_at=(1, STEP_NS))


def test_airspace_snapshot_memory_is_bounded_to_one_window(tmp_path):
    stack = _stack(tmp_path)
    for index in range(501):
        stack._on_airspace_topic_message(_message(stack, index * 4_000_000))
    assert stack._airspace_failure is None
    assert len(stack._airspace_samples["uav.01"]) == 51


def test_barrier_wait_fails_if_any_uav_is_missing(tmp_path):
    stack = _stack(tmp_path)
    stack._on_airspace_topic_message(_message(stack, STEP_NS))
    with pytest.raises(_SERVICE.Px4ServiceError, match="every vehicle"):
        asyncio.run(stack.wait_for_airspace(requested_at=(1, STEP_NS)))


def test_reset_retains_real_time_zero_samples_instead_of_clearing_them(tmp_path, monkeypatch):
    stack = _stack(tmp_path)
    for vehicle in ("uav.01", "uav.02"):
        stack._on_airspace_topic_message(_message(stack, 0, vehicle=vehicle))
    for name in ("stop", "verify_identities", "_start", "multi_step", "_wait_for_telemetry_cache"):
        monkeypatch.setattr(stack, name, AsyncMock())
    monkeypatch.setattr(stack, "_raw_sim_time_ns", AsyncMock(return_value=ORIGIN))
    asyncio.run(stack.reset(stack._config, seed=1))
    for vehicle in ("uav.01", "uav.02"):
        sample = stack.airspace_observation(vehicle_id=vehicle, requested_at=(0, 0))
        assert sample["engine_sim_time_ns"] == ORIGIN
        assert sample["simulation_time_ns"] == 0
        assert sample["airspace_state"] == "outside"


def _provider_events(tmp_path):
    stack = _stack(tmp_path)
    stack._on_airspace_topic_message(_message(stack, 60_004_000_000, transition="entered", sequence=1, inside=True))
    service = object.__new__(_SERVICE.Px4GazeboService)
    service._stack = stack
    target = SimulationTime(tick=301, sim_time_ns=301 * STEP_NS)
    events = tuple(ProviderEvent.model_validate(item) for item in service._airspace_provider_events(config=stack._config, target=(target.tick, target.sim_time_ns)))
    return stack, events, target


def test_service_reads_stack_owned_stream_and_emits_full_scalar_safe_transition(tmp_path):
    stack, events, _ = _provider_events(tmp_path)
    assert len(events) == 2
    assert events[0].payload_schema_id == "gazebo.airspace-transition.v1"
    payload = {item.name: item.value for item in events[0].payload}
    assert set(payload) == {"transition_json", "logical_origin_engine_ns"}
    transition = AirspaceTransition.model_validate_json(payload["transition_json"])
    assert transition.source == "gazebo.system"
    assert transition.vehicle_id == "uav.01"
    assert transition.engine_sim_time_ns == ORIGIN + 60_004_000_000
    assert payload["logical_origin_engine_ns"] == ORIGIN
    assert events[1].payload_schema_id == "public.event.v3"
    assert not hasattr(object.__new__(_SERVICE.Px4GazeboService), "_airspace_lock")


def _hook(tmp_path):
    source = tmp_path / "unit-source.json"
    source.write_bytes(b'{"unit_only":true}\n')
    hook = object.__new__(UrbanRecoveryRuntimeHook)
    hook._package = _demo_package(tmp_path, source)
    hook._world_digest = "a" * 64
    hook._airspace_origin_ns = None
    hook._airspace_transitions = []
    hook._airspace_sequences = set()
    return hook


def test_runtime_hook_decodes_the_actual_provider_wire_without_nested_namedvalues(tmp_path):
    _, events, target = _provider_events(tmp_path)
    hook = _hook(tmp_path)
    hook._accept_airspace_event(events[0], target)
    assert hook._airspace_transitions[0].transition == "entered"
    assert hook._airspace_origin_ns == ORIGIN
    with pytest.raises(UrbanRecoveryRuntimeHookError, match="sequence"):
        hook._accept_airspace_event(events[0], target)


def test_world_adds_a_monitor_for_each_declared_uav(tmp_path):
    source = tmp_path / "source.sdf"
    source.write_text('<sdf version="1.9"><world name="default" /></sdf>')
    destination = tmp_path / "prepared.sdf"
    vehicles = tuple({
        "gazebo_model_name": name, "gazebo_resource": "x500_base",
        "initial_pose": {"x_m": x, "y_m": 0, "z_m": 0.1, "roll_rad": 0, "pitch_rad": 0, "yaw_rad": 0},
    } for name, x in (("uav.01", -20), ("uav.02", 20)))
    _SERVICE.ensure_paused_world_sdf(source, destination, vehicles=vehicles, airspace_transition=_airspace(incident_vehicle="uav.01"))
    plugins = [
        item
        for item in ElementTree.parse(destination).getroot().findall("world/plugin")
        if item.attrib.get("name") == "aero_bench::gazebo::AirspaceTransition"
    ]
    assert {item.find("incident_vehicle").text for item in plugins} == {"uav.01", "uav.02"}
    assert len(plugins) == 2
