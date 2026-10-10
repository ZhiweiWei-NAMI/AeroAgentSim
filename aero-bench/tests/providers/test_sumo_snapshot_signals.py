from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from aero_bench.config.models import NamedValue
from aero_bench.providers.sumo.provider import (
    SumoProvider,
    SumoProviderError,
    SumoTrafficLightState,
)
from aero_bench.runtime.contracts import ProviderEvent, SimulationTime, StepReceipt
from aero_bench.serialization import canonical_json_bytes


def _signal(signal_id: str = "junction.1") -> SumoTrafficLightState:
    return SumoTrafficLightState(
        signal_id=signal_id,
        state="Gr",
        phase_index=0,
        next_switch_s=20.0,
        program_id="0",
        telemetry_source="sumo-traci",
    )


def _receipt(signals: tuple[SumoTrafficLightState, ...]) -> StepReceipt:
    snapshot = {
        "simulation_time_ns": 500_000_000,
        "entities": [],
        "traffic_lights": [signal.model_dump(mode="json") for signal in signals],
    }
    return StepReceipt(
        run_id="a" * 64,
        provider_id="traffic",
        reached=SimulationTime(tick=1, sim_time_ns=500_000_000),
        state_digest=hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest(),
        events=(),
    )


def _traffic_light_receipt(
    signals: tuple[SumoTrafficLightState, ...],
    *,
    payload_schema_id: str = "sumo.traffic_light.v1",
) -> StepReceipt:
    receipt = _receipt(signals)
    event = ProviderEvent(
        provider_id=receipt.provider_id,
        event_id="public.traffic-light",
        time=receipt.reached,
        payload_schema_id=payload_schema_id,
        payload=(
            NamedValue(name="snapshot_digest", value=receipt.state_digest),
            NamedValue(
                name="simulation_time_ns", value=receipt.reached.sim_time_ns
            ),
            NamedValue(
                name="traffic_lights_json",
                value=canonical_json_bytes(
                    [signal.model_dump(mode="json") for signal in signals]
                ).decode("utf-8"),
            ),
        ),
    )
    return receipt.model_copy(update={"events": (event,)})


def test_snapshot_digest_includes_authoritative_traffic_signals() -> None:
    signals = (_signal(),)
    provider = object.__new__(SumoProvider)
    provider._validate_samples_against_receipt((), _receipt(signals), signals)
    with pytest.raises(SumoProviderError, match="reconstruct"):
        provider._validate_samples_against_receipt((), _receipt(signals), ())
    changed = (_signal().model_copy(update={"state": "rG"}),)
    with pytest.raises(SumoProviderError, match="reconstruct"):
        provider._validate_samples_against_receipt((), _receipt(signals), changed)


def test_public_signal_event_requires_current_traffic_light_schema() -> None:
    signals = (_signal(),)
    current = _traffic_light_receipt(signals)
    assert SumoProvider._traffic_light_event_states(current) == signals

    legacy = _traffic_light_receipt(
        signals, payload_schema_id="public.traffic-light.v1"
    )
    with pytest.raises(SumoProviderError, match="binding is invalid"):
        SumoProvider._traffic_light_event_states(legacy)


def test_snapshot_rejects_duplicate_or_unsorted_signals() -> None:
    provider = object.__new__(SumoProvider)
    for signals in ((_signal(), _signal()), (_signal("z"), _signal("a"))):
        with pytest.raises(SumoProviderError, match="sorted and unique"):
            provider._validate_samples_against_receipt((), _receipt(signals), signals)


def test_signal_contract_rejects_nonfinite_time_and_unknown_fields() -> None:
    for updates in ({"next_switch_s": float("nan")}, {"invented": True}):
        with pytest.raises(ValidationError):
            SumoTrafficLightState.model_validate({**_signal().model_dump(), **updates})
