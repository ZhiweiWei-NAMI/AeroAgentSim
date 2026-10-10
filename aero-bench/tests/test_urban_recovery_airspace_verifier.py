"""Airspace verifier wire tests using callbacks only, never a Gazebo execution."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import ProviderEvent, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo import verifier as urban
from aero_bench.tasks.urban_recovery_demo.contracts import FINAL_TICK, STEP_NS
from tests.test_urban_airspace_stream import ORIGIN, _message, _stack, _SERVICE
from tools.build_urban_recovery_demo import _demo_package


def _ledger_record(event):
    return SimpleNamespace(event=SimpleNamespace(
        source="flight", provider_id="flight", event_type=event.event_id,
        time=event.time, payload_schema_id=event.payload_schema_id, payload=event.payload,
    ))


@pytest.fixture(scope="module")
def wire(tmp_path_factory):
    root = tmp_path_factory.mktemp("unit-airspace-wire")
    source = root / "unit-source.json"
    source.write_bytes(b'{"unit_only":true}\n')
    package = _demo_package(root, source)
    stack = _stack(root)
    service = object.__new__(_SERVICE.Px4GazeboService)
    service._stack = stack
    observations = {}
    records = []
    for tick in range(FINAL_TICK + 1):
        if tick in (301, 401):
            stack._on_airspace_topic_message(_message(
                stack, (tick - 1) * STEP_NS + 4_000_000,
                transition="entered" if tick == 301 else "exited",
                sequence=1 if tick == 301 else 2, inside=tick == 301,
            ))
        at = SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
        for role in package.roles:
            if role.role != "uav":
                continue
            inside = role.vehicle_id == "uav.01" and 301 <= tick < 401
            stack._on_airspace_topic_message(_message(stack, at.sim_time_ns, inside=inside, vehicle=role.vehicle_id))
            payload = dict(stack.airspace_observation(vehicle_id=role.vehicle_id, requested_at=(tick, at.sim_time_ns)))
            payload["position_enu_m"] = canonical_json_bytes(payload["position_enu_m"]).decode()
            observations[(role.agent_id, role.safety_observation_id, tick)] = (
                SimpleNamespace(event=SimpleNamespace(time=at, source="flight", provider_id="flight")), payload,
            )
        records.extend(_ledger_record(ProviderEvent.model_validate(event)) for event in service._airspace_provider_events(config=stack._config, target=(tick, at.sim_time_ns)))
    authority = SimpleNamespace(**stack._config.airspace_transition)
    config = SimpleNamespace(airspace_transition=authority, vehicles=tuple(SimpleNamespace(**vehicle) for vehicle in stack._config.vehicles))
    run = SimpleNamespace(scenario=SimpleNamespace(world_digest=authority.world_digest))
    return package, SimpleNamespace(records=tuple(records)), observations, run, config, ORIGIN


def test_verifier_accepts_real_provider_model_names_and_nonzero_epoch(wire):
    entered, exited = urban._validate_airspace(*wire)
    assert entered[0].engine_model == "uav.01"
    assert entered[0].engine_sim_time_ns == ORIGIN + 60_004_000_000
    assert exited[0].engine_sim_time_ns == ORIGIN + 80_004_000_000


@pytest.mark.parametrize("field,value", [
    ("engine_model", "gazebo.system-plugin"), ("world_sha256", "f" * 64),
    ("region_sha256", "f" * 64), ("region_id", "region.other"),
    ("sequence", 3), ("engine_sim_time_ns", ORIGIN + 60_004_000_001),
    ("vehicle_id", "uav.02"),
])
def test_foreign_or_off_grid_transition_fails_even_if_its_json_is_canonical(wire, field, value):
    package, ledger, observations, run, config, origin = wire
    ledger = copy.deepcopy(ledger)
    record = next(record for record in ledger.records if record.event.payload_schema_id == urban._AIRSPACE_EVENT_SCHEMA)
    payload = urban._payload(record)
    transition = json.loads(payload["transition_json"])
    transition[field] = value
    payload["transition_json"] = canonical_json_bytes(transition).decode()
    record.event.payload = tuple(NamedValue(name=name, value=value) for name, value in sorted(payload.items()))
    with pytest.raises(ValueError, match="transition identity"):
        urban._validate_airspace(package, ledger, observations, run, config, origin)


@pytest.mark.parametrize("vehicle,tick,field,value", [
    ("01", 301, "transition_sequence", True),
    ("01", 302, "transition_sequence", 1),
    ("01", 302, "logical_origin_engine_ns", ORIGIN + STEP_NS),
    ("01", 302, "world_sha256", "f" * 64),
    ("01", 302, "region_sha256", "f" * 64),
    ("02", 302, "airspace_state", "inside"),
    ("02", 302, "transition", "entered"),
    ("02", 3000, "source", "policy.generated"),
])
def test_both_uavs_safety_observations_must_bind_native_authority(wire, vehicle, tick, field, value):
    package, ledger, observations, run, config, origin = wire
    observations = dict(observations)
    key = (f"uav.policy.{vehicle}", f"observation.uav.{vehicle}.safety", tick)
    record, payload = observations[key]
    observations[key] = record, {**payload, field: value}
    with pytest.raises(ValueError, match="safety observation differs"):
        urban._validate_airspace(package, ledger, observations, run, config, origin)


def test_airspace_cannot_choose_a_different_epoch_from_the_physics_journal(wire):
    with pytest.raises(ValueError, match="transition identity"):
        urban._validate_airspace(*wire[:-1], ORIGIN + STEP_NS)


def test_verifier_does_not_sort_an_out_of_order_entry_and_exit_into_a_pass(wire):
    package, ledger, *rest = wire
    with pytest.raises(ValueError, match="transition identity"):
        urban._validate_airspace(package, SimpleNamespace(records=ledger.records[::-1]), *rest)


def test_airspace_event_name_must_match_the_typed_transition(wire):
    package, ledger, *rest = wire
    ledger = copy.deepcopy(ledger)
    ledger.records[0].event.event_type = "gazebo.airspace.exited"
    with pytest.raises(ValueError, match="transition identity"):
        urban._validate_airspace(package, ledger, *rest)
