from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.providers import rpc as _rpc
from aero_bench.config.models import FileRef
from aero_bench.providers.px4_gazebo.observations import (
    FlightGnssObservation,
    FlightTelemetryObservation,
)
from aero_bench.providers.px4_gazebo.provider import Px4GazeboProvider
from aero_bench.runtime.contracts import SimulationTime


_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
sys.modules["rpc"] = _rpc
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_flight_observation_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


def _stack() -> object:
    stack = object.__new__(_SERVICE.RealPx4Stack)
    stack._config = SimpleNamespace(vehicles=({"vehicle_id": "uav.inspector"},))
    stack._telemetry_cache = {
        "uav.inspector": {
            "global_position": {
                "longitude_deg": 121.501,
                "latitude_deg": 31.201,
                "altitude_m": 16.5,
                "relative_altitude_m": 8.25,
            },
            "velocity": {
                "north_m_s": 1.5,
                "east_m_s": -0.5,
                "down_m_s": 0.25,
            },
            "attitude_euler": {
                "roll_deg": 1.0,
                "pitch_deg": -2.0,
                "yaw_deg": 90.0,
            },
            "flight_mode": "HOLD",
            "armed": True,
            "in_air": True,
            "landed_state": "IN_AIR",
            "battery_percent": 83.0,
            "health": '{"is_global_position_ok":true}',
            "gps_info": {"num_satellites": 14, "fix_type": "FIX_3D"},
            "raw_gps": {
                "timestamp_us": 12_500_000,
                "latitude_deg": 31.2009,
                "longitude_deg": 121.5009,
                "absolute_altitude_m": 16.3,
                "hdop": 0.8,
                "vdop": None,
                "velocity_m_s": 1.6,
                "course_over_ground_deg": 341.0,
                "altitude_ellipsoid_m": 27.1,
                "horizontal_uncertainty_m": 0.7,
                "vertical_uncertainty_m": None,
                "velocity_uncertainty_m_s": 0.2,
                "heading_uncertainty_deg": None,
                "yaw_deg": 90.0,
            },
        }
    }
    fields = stack._telemetry_cache["uav.inspector"]
    stack._telemetry_received_monotonic_ns = {
        "uav.inspector": {name: 1 for name in fields}
    }
    return stack


def test_agent_telemetry_contains_only_mavsdk_estimates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack()
    monkeypatch.setattr(_SERVICE.time, "monotonic_ns", lambda: 1_000_001)

    payload = stack.flight_telemetry_observation(
        "uav.inspector", simulation_time_ns=8_000_000_000
    )
    parsed = FlightTelemetryObservation.model_validate(payload)

    assert parsed.position_source == "mavsdk.telemetry.position"
    assert parsed.attitude_source == "mavsdk.telemetry.attitude_euler"
    assert parsed.source_timestamp_status == "missing"
    assert (
        not {
            "pose_json",
            "contacts_json",
            "ground_contact",
            "collision_contact",
            "position_enu_m",
        }
        & payload.keys()
    )
    assert all("gazebo" not in name for name in payload)


def test_gnss_observation_uses_raw_stream_and_marks_missing_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack()
    monkeypatch.setattr(_SERVICE.time, "monotonic_ns", lambda: 2_000_001)

    payload = stack.flight_gnss_observation(
        "uav.inspector", simulation_time_ns=8_000_000_000
    )
    parsed = FlightGnssObservation.model_validate(payload)

    assert parsed.source == "mavsdk.telemetry.raw_gps+gps_info"
    assert parsed.fix_type == "FIX_3D"
    assert parsed.num_satellites == 14
    assert parsed.source_timestamp_stream == "raw_gps"
    assert parsed.gps_info_timestamp_status == "missing"
    assert parsed.vdop_status == "missing" and parsed.vdop is None
    assert parsed.vertical_uncertainty_status == "missing"
    assert parsed.hdop_status == "available" and parsed.hdop == 0.8
    assert parsed.source_to_sim_time_mapping == "unavailable"


def test_gnss_observation_is_not_ready_without_real_raw_sample() -> None:
    stack = _stack()
    del stack._telemetry_cache["uav.inspector"]["raw_gps"]
    del stack._telemetry_received_monotonic_ns["uav.inspector"]["raw_gps"]

    with pytest.raises(_SERVICE.Px4ServiceError, match="not ready.*raw_gps"):
        stack.flight_gnss_observation("uav.inspector", simulation_time_ns=8_000_000_000)


def test_raw_gps_decoder_does_not_invent_optional_quality() -> None:
    sample = SimpleNamespace(
        timestamp_us=10_000,
        latitude_deg=31.2,
        longitude_deg=121.5,
        absolute_altitude_m=15.0,
        hdop=math.nan,
        vdop=-1.0,
    )

    decoded = _SERVICE.RealPx4Stack._decode_telemetry("raw_gps", sample)

    assert decoded["hdop"] is None
    assert decoded["vdop"] is None
    assert decoded["altitude_ellipsoid_m"] is None
    assert decoded["horizontal_uncertainty_m"] is None


@pytest.mark.parametrize(
    "field,sample",
    (
        (
            "raw_gps",
            SimpleNamespace(
                timestamp_us=0,
                latitude_deg=31.2,
                longitude_deg=121.5,
                absolute_altitude_m=15.0,
            ),
        ),
        (
            "gps_info",
            SimpleNamespace(
                num_satellites=None,
                fix_type=SimpleNamespace(name="FIX_3D"),
            ),
        ),
    ),
)
def test_required_gnss_fields_fail_closed(field: str, sample: object) -> None:
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE.RealPx4Stack._decode_telemetry(field, sample)


def test_host_provider_validates_declared_flight_observation() -> None:
    async def run() -> None:
        stack = _stack()
        payload = dict(
            stack.flight_gnss_observation(
                "uav.inspector", simulation_time_ns=8_000_000_000
            )
        )
        payload_digest = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        schema_file = FileRef(path="schemas/flight-gnss.json", sha256="a" * 64)
        observation_id = "flight.gnss.uav.inspector"
        at = SimulationTime(tick=4, sim_time_ns=8_000_000_000)

        class Transport:
            async def request(
                self, operation: str, request: object
            ) -> dict[str, object]:
                assert operation == "observe"
                return {
                    "observation": {
                        "run_id": "b" * 64,
                        "agent_id": "participant.agent",
                        "observation_id": observation_id,
                        "time": at.model_dump(mode="json"),
                        "payload_schema": schema_file.model_dump(mode="json"),
                        "payload": [
                            {"name": name, "value": value}
                            for name, value in payload.items()
                        ],
                        "payload_digest": payload_digest,
                    }
                }

        provider = object.__new__(Px4GazeboProvider)
        provider._prepared = True
        provider._config = SimpleNamespace(provider_id="flight")
        provider._scenario = SimpleNamespace(
            task=SimpleNamespace(
                observations=(
                    SimpleNamespace(
                        endpoint_id="flight",
                        observation_id=observation_id,
                        schema_file=schema_file,
                        flight=SimpleNamespace(
                            observation_kind="gnss",
                            observation_id=observation_id,
                            vehicle_id="uav.inspector",
                        ),
                        urban=None,
                        inspection=None,
                    ),
                )
            )
        )
        provider._run_id = "b" * 64
        provider._last_time = at
        provider._session_token = "c" * 64
        provider._transport = Transport()
        provider._manifest = SimpleNamespace(artifact_requirements=())

        envelope = await provider.observe(
            run_id="b" * 64,
            agent_id="participant.agent",
            observation_id=observation_id,
            requested_at=at,
        )

        assert envelope.payload_digest == payload_digest

    asyncio.run(run())


def test_service_resolves_flight_projection_without_treating_it_as_camera(
    tmp_path: Path,
) -> None:
    binding = {
        "binding_id": "participant.agent.flight.gnss.uav.inspector",
        "agent_id": "participant.agent",
        "observation_id": "flight.gnss.uav.inspector",
        "endpoint_id": "flight",
        "schema_file": {"path": "schemas/flight-gnss.json", "sha256": "a" * 64},
        "timeout_ms": 1_000,
        "inspection": None,
        "urban": None,
        "flight": {
            "projection_kind": "flight",
            "observation_id": "flight.gnss.uav.inspector",
            "observation_kind": "gnss",
            "vehicle_id": "uav.inspector",
        },
    }
    scenario = _SERVICE.ValidatedWorkloadScenario(
        scenario_digest="b" * 64,
        scenario={
            "task": {
                "package_id": "inspection.v1",
                "observations": [binding],
            }
        },
        assets=(),
    )
    vehicles = ({"vehicle_id": "uav.inspector"},)

    flight = _SERVICE._scenario_flight_observations(
        scenario=scenario,
        provider_id="flight",
        vehicles=vehicles,
    )
    inspections, model_paths = _SERVICE._scenario_inspections(
        scenario=scenario,
        provider_id="flight",
        vehicles=vehicles,
        bundle_root=tmp_path,
    )

    assert flight[0]["observation_kind"] == "gnss"
    assert inspections == ()
    assert model_paths == ()
