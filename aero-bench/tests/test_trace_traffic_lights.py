"""Unit-only projection checks for opaque SUMO TraCI controller IDs."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.events import RunEventAudience
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.projector import (
    PublicProjectorError,
    _project_traffic_light_frames,
    project_public_run_event,
)


def _project(signal_ids: tuple[str, ...], *, payload_time_ns: int = 200_000_000):
    states = [
        {
            "signal_id": signal_id,
            "state": "GGgrrrrrrGgg",
            "phase_index": 0,
            "next_switch_s": 39.0,
            "program_id": "0",
            "telemetry_source": "sumo-traci",
        }
        for signal_id in signal_ids
    ]
    document = canonical_json_bytes(states)
    digest = hashlib.sha256(document).hexdigest()
    ledger = EventLedger(run_id=hashlib.sha256(b"traffic-light-unit").hexdigest())
    record = ledger.append_event(
        source="traffic",
        source_kind="provider",
        workload_id="traffic",
        provider_id="traffic",
        event_type="public.traffic-light",
        payload_schema_id="sumo.traffic_light.v1",
        interaction_type="sumo.traffic_light.v1",
        time=SimulationTime(tick=1, sim_time_ns=200_000_000),
        visibility=(RunEventAudience(scope="public", audience_id=None),),
        payload=(
            NamedValue(name="simulation_time_ns", value=payload_time_ns),
            NamedValue(name="snapshot_digest", value=digest),
            NamedValue(name="traffic_lights_json", value=document.decode("utf-8")),
        ),
    )
    public = project_public_run_event(record.event, public_event_ids={record.event.event_id})
    assert public.interaction_type == "sumo.traffic_light.v1"
    assert public.payload_schema_id == "sumo.traffic_light.v1"
    assert tuple(item.name for item in public.public_payload) == (
        "simulation_time_ns", "snapshot_digest", "traffic_lights_json",
    )
    run = SimpleNamespace(scenario=SimpleNamespace(sumo=SimpleNamespace(provider_id="traffic")))
    return _project_traffic_light_frames(run, (record,)), states, digest


def test_numeric_and_external_sumo_ids_are_preserved_verbatim() -> None:
    frames, states, digest = _project(("152878617", "GS_12:0", "junction.a"))
    assert len(frames) == 1
    frame = frames[0]
    assert [state.model_dump(mode="json") for state in frame.states] == states
    assert frame.snapshot_digest == digest
    assert frame.at == SimulationTime(tick=1, sim_time_ns=200_000_000)
    assert frame.provider_id == "traffic"


@pytest.mark.parametrize("signal_ids", [("",), ("152878617", "152878617"), ("z", "152878617")])
def test_empty_duplicate_or_unordered_ids_remain_invalid(signal_ids: tuple[str, ...]) -> None:
    with pytest.raises(PublicProjectorError, match="state is invalid"):
        _project(signal_ids)


def test_sumo_frame_retains_exact_recorded_time_binding() -> None:
    with pytest.raises(PublicProjectorError, match="payload binding is invalid"):
        _project(("152878617",), payload_time_ns=400_000_000)
