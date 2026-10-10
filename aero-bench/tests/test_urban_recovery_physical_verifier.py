from __future__ import annotations

import hashlib
from copy import deepcopy
from types import SimpleNamespace
from typing import Callable

import pytest

from aero_bench.config.models import NamedValue
from aero_bench.providers.px4_gazebo.config import PhysicalCompletionPolicy
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo import verifier as urban
from aero_bench.world.frame_math import wgs84_amsl_delta_enu


RUN_ID = "a" * 64
VEHICLE_ID = "uav.01"
COMMAND_ID = "command.arm.01"


def _policy() -> PhysicalCompletionPolicy:
    return PhysicalCompletionPolicy(
        schema_version="aero-bench.px4-physical-completion-policy/v2",
        physical_sim_timeout_ns=60_000_000_000,
        min_settle_samples=3,
        settle_duration_ns=1_000_000_000,
        takeoff_altitude_tolerance_m=1.0,
        goto_horizontal_tolerance_m=0.75,
        goto_vertical_tolerance_m=0.5,
        goto_minimum_progress_m=1.0,
        hold_drift_radius_m=1.5,
        max_horizontal_settled_speed_m_s=0.8,
        max_vertical_settled_speed_m_s=0.5,
        landing_max_speed_m_s=0.35,
        landing_max_height_proxy_m=0.5,
        disarm_requires_contact=True,
        arm_allowed_modes=("ALTCTL", "HOLD", "MANUAL", "POSCTL", "READY", "STABILIZED"),
        disarm_allowed_modes=("HOLD", "LAND", "MANUAL", "POSCTL", "READY", "STABILIZED"),
        takeoff_allowed_modes=("HOLD", "MISSION", "POSCTL", "TAKEOFF"),
        goto_allowed_modes=("HOLD", "MISSION", "OFFBOARD", "POSCTL"),
        hold_allowed_modes=("HOLD", "LOITER", "POSCTL"),
        land_allowed_modes=("HOLD", "LAND", "POSCTL", "RETURN_TO_LAUNCH"),
    )


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        physical_completion_policy=_policy(),
        vehicles=(
            SimpleNamespace(
                vehicle_id=VEHICLE_ID,
                system_id=1,
                initial_pose=SimpleNamespace(z_m=0.0),
            ),
        ),
    )


def _state(
    *,
    x_m: float = 0.0,
    y_m: float = 0.0,
    z_m: float = 0.0,
    longitude_deg: float = 121.0,
    latitude_deg: float = 31.0,
    altitude_m: float = 20.0,
    north_m_s: float = 0.0,
    east_m_s: float = 0.0,
    down_m_s: float = 0.0,
    mode: str = "READY",
    armed: bool = False,
    in_air: bool = False,
    landed: bool = True,
    ground_contact: bool = True,
    collision_contact: bool = False,
) -> dict[str, object]:
    contacts = []
    if ground_contact:
        contacts.append("launch_pad.pad.01")
    if collision_contact:
        contacts.append("building.wall.01")
    return {
        "raw": {
            "flight_mode": mode,
            "armed": armed,
            "in_air": in_air,
            "landed": landed,
            "landed_state": "ON_GROUND" if landed else "IN_AIR",
            "contacts": sorted(contacts),
            "ground_contact": ground_contact,
            "collision_contact": collision_contact,
        },
        "pose": {
            "x_m": x_m,
            "y_m": y_m,
            "z_m": z_m,
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "yaw_rad": 0.0,
        },
        "wgs": {
            "longitude_deg": longitude_deg,
            "latitude_deg": latitude_deg,
            "altitude_m": altitude_m,
        },
        "velocity": {
            "north_m_s": north_m_s,
            "east_m_s": east_m_s,
            "down_m_s": down_m_s,
        },
        "angular": {"x_rad_s": 0.0, "y_rad_s": 0.0, "z_rad_s": 0.0},
    }


def _command_case(
    tool_id: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    baseline = _state()
    if tool_id == "flight.arm":
        return baseline, _state(mode="HOLD", armed=True), {"vehicle_id": VEHICLE_ID}
    if tool_id == "flight.disarm":
        baseline = _state(mode="HOLD", armed=True)
        return baseline, _state(mode="HOLD"), {"vehicle_id": VEHICLE_ID}
    if tool_id == "flight.takeoff":
        return (
            _state(mode="HOLD", armed=True),
            _state(
                z_m=20.0,
                altitude_m=40.0,
                mode="TAKEOFF",
                armed=True,
                in_air=True,
                landed=False,
                ground_contact=False,
            ),
            {"vehicle_id": VEHICLE_ID, "altitude_m": 20.0},
        )
    if tool_id == "flight.goto":
        target_longitude = 121.0001
        target_altitude = 40.0
        delta = wgs84_amsl_delta_enu(
            origin_longitude_deg=121.0,
            origin_latitude_deg=31.0,
            origin_amsl_m=20.0,
            target_longitude_deg=target_longitude,
            target_latitude_deg=31.0,
            target_amsl_m=target_altitude,
        )
        baseline = _state(
            mode="HOLD", armed=True, in_air=True, landed=False, ground_contact=False
        )
        current = _state(
            x_m=delta.x,
            y_m=delta.y,
            z_m=delta.z,
            longitude_deg=target_longitude,
            altitude_m=target_altitude,
            mode="HOLD",
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        )
        return (
            baseline,
            current,
            {
                "vehicle_id": VEHICLE_ID,
                "latitude_deg": 31.0,
                "longitude_deg": target_longitude,
                "altitude_amsl_m": target_altitude,
                "yaw_deg": 0.0,
            },
        )
    if tool_id == "flight.hold":
        baseline = _state(
            x_m=5.0,
            y_m=6.0,
            z_m=20.0,
            altitude_m=40.0,
            mode="HOLD",
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        )
        return baseline, deepcopy(baseline), {"vehicle_id": VEHICLE_ID}
    if tool_id == "flight.land":
        baseline = _state(
            z_m=20.0,
            altitude_m=40.0,
            mode="HOLD",
            armed=True,
            in_air=True,
            landed=False,
            ground_contact=False,
        )
        return baseline, _state(mode="LAND"), {"vehicle_id": VEHICLE_ID}
    raise AssertionError(tool_id)


def _violate_predicate(tool_id: str, current: dict[str, object]) -> None:
    raw = current["raw"]
    assert isinstance(raw, dict)
    if tool_id == "flight.arm":
        raw["armed"] = False
    elif tool_id == "flight.disarm":
        raw["armed"] = True
    elif tool_id == "flight.takeoff":
        wgs = current["wgs"]
        assert isinstance(wgs, dict)
        wgs["altitude_m"] = 30.0
    elif tool_id == "flight.goto":
        wgs = current["wgs"]
        assert isinstance(wgs, dict)
        wgs["longitude_deg"] = 121.0
        wgs["altitude_m"] = 20.0
    elif tool_id == "flight.hold":
        pose = current["pose"]
        assert isinstance(pose, dict)
        pose["x_m"] = float(pose["x_m"]) + 2.0
    elif tool_id == "flight.land":
        raw["ground_contact"] = False
        raw["contacts"] = []


@pytest.mark.parametrize(
    "tool_id",
    (
        "flight.arm",
        "flight.disarm",
        "flight.takeoff",
        "flight.goto",
        "flight.hold",
        "flight.land",
    ),
)
def test_each_physical_completion_predicate_is_recomputed_from_state_and_args(
    tool_id: str,
) -> None:
    baseline, current, arguments = _command_case(tool_id)
    passed, _ = urban._physical_completion_state(
        tool_id,
        arguments,
        vehicle_id=VEHICLE_ID,
        baseline=baseline,
        current=current,
        config=_config(),
    )
    assert passed

    invalid = deepcopy(current)
    _violate_predicate(tool_id, invalid)
    passed, _ = urban._physical_completion_state(
        tool_id,
        arguments,
        vehicle_id=VEHICLE_ID,
        baseline=baseline,
        current=invalid,
        config=_config(),
    )
    assert not passed


def _proof_document(
    *,
    phase: str,
    baseline_tick: int,
    applied_tick: int,
    current_tick: int,
    baseline: dict[str, object],
    current: dict[str, object],
    config: SimpleNamespace,
    settling: dict[str, object],
) -> dict[str, object]:
    arguments = {"vehicle_id": VEHICLE_ID}
    _, metrics = urban._physical_completion_state(
        "flight.arm",
        arguments,
        vehicle_id=VEHICLE_ID,
        baseline=baseline,
        current=current,
        config=config,
    )
    return {
        "command_id": COMMAND_ID,
        "tool_id": "flight.arm",
        "vehicle_id": VEHICLE_ID,
        "phase": phase,
        "policy_schema_version": config.physical_completion_policy.schema_version,
        "baseline_tick": baseline_tick,
        "baseline_sim_time_ns": baseline_tick * urban.STEP_NS,
        "baseline_state_digest": urban._physical_state_digest(
            VEHICLE_ID, baseline_tick, baseline
        ),
        "current_tick": current_tick,
        "current_sim_time_ns": current_tick * urban.STEP_NS,
        "current_state_digest": urban._physical_state_digest(
            VEHICLE_ID, current_tick, current
        ),
        "accepted_tick": baseline_tick,
        "accepted_sim_time_ns": baseline_tick * urban.STEP_NS,
        "applied_tick": applied_tick,
        "applied_sim_time_ns": applied_tick * urban.STEP_NS,
        "ack_audit_status": "decoded_command_ack_accepted",
        "decoded_command_audit": {},
        "thresholds": urban._physical_thresholds("flight.arm", config),
        "metrics": metrics,
        "settling": settling,
    }


def _append_proof(
    ledger: EventLedger,
    proof: dict[str, object],
) -> None:
    phase = str(proof["phase"])
    ledger.append_event(
        source="flight",
        event_type=f"px4.command.physical.{phase}",
        time=SimulationTime(
            tick=int(proof["current_tick"]),
            sim_time_ns=int(proof["current_sim_time_ns"]),
        ),
        provider_id="flight",
        command_id=COMMAND_ID,
        payload_schema_id=urban._PHYSICAL_PROOF_SCHEMA,
        payload=tuple(
            NamedValue(name=name, value=value)
            for name, value in (
                ("schema_id", urban._PHYSICAL_PROOF_SCHEMA),
                ("command_id", COMMAND_ID),
                ("tool_id", "flight.arm"),
                ("phase", phase),
                (
                    "proof_json",
                    canonical_json_bytes(proof).decode("utf-8"),
                ),
            )
        ),
    )


def _physical_command_fixture(
    *,
    baseline_tick: int = 0,
    completed_tick: int | None = None,
    unsatisfied_tick: int | None = None,
    alter_completed: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    dict[str, tuple[object, dict[str, object]]],
    EventLedger,
    dict[int, dict[str, dict[str, object]]],
    SimpleNamespace,
]:
    config = _config()
    applied_tick = baseline_tick + 1
    if completed_tick is None:
        completed_tick = baseline_tick + 7
    baseline = _state()
    settled = _state(mode="HOLD", armed=True)
    trajectory: dict[int, dict[str, dict[str, object]]] = {
        0: {VEHICLE_ID: deepcopy(baseline)},
        baseline_tick: {VEHICLE_ID: deepcopy(baseline)},
    }
    for tick in range(applied_tick, completed_tick + 1):
        trajectory[tick] = {VEHICLE_ID: deepcopy(settled)}
    if unsatisfied_tick is not None:
        trajectory[unsatisfied_tick] = {VEHICLE_ID: deepcopy(baseline)}

    ledger = EventLedger(run_id=RUN_ID, wall_time_ns=lambda: 1)
    issue = ledger.append_event(
        source="uav.policy.01",
        event_type="unit.flight-command-issued",
        time=SimulationTime(
            tick=baseline_tick, sim_time_ns=baseline_tick * urban.STEP_NS
        ),
        provider_id="flight",
        command_id=COMMAND_ID,
        payload=(NamedValue(name="tool_id", value="flight.arm"),),
    )
    applied = _proof_document(
        phase="applied",
        baseline_tick=baseline_tick,
        applied_tick=applied_tick,
        current_tick=applied_tick,
        baseline=trajectory[baseline_tick][VEHICLE_ID],
        current=trajectory[applied_tick][VEHICLE_ID],
        config=config,
        settling=urban._empty_physical_settling(),
    )
    completed = _proof_document(
        phase="completed",
        baseline_tick=baseline_tick,
        applied_tick=applied_tick,
        current_tick=completed_tick,
        baseline=trajectory[baseline_tick][VEHICLE_ID],
        current=trajectory[completed_tick][VEHICLE_ID],
        config=config,
        settling={
            "start_tick": applied_tick + 1,
            "start_sim_time_ns": (applied_tick + 1) * urban.STEP_NS,
            "end_tick": completed_tick,
            "end_sim_time_ns": completed_tick * urban.STEP_NS,
            "sample_count": completed_tick - applied_tick,
            "sample_duration_ns": (completed_tick - applied_tick - 1)
            * urban.STEP_NS,
        },
    )
    if alter_completed is not None:
        alter_completed(completed)
    _append_proof(ledger, applied)
    _append_proof(ledger, completed)
    return (
        {COMMAND_ID: (issue, {"vehicle_id": VEHICLE_ID})},
        ledger,
        trajectory,
        config,
    )


def _validate_fixture(
    monkeypatch: pytest.MonkeyPatch,
    fixture: tuple[
        dict[str, tuple[object, dict[str, object]]],
        EventLedger,
        dict[int, dict[str, dict[str, object]]],
        SimpleNamespace,
    ],
) -> dict[str, tuple[SimulationTime, SimulationTime]]:
    monkeypatch.setattr(
        urban,
        "_validate_decoded_command_audit",
        lambda *_args, **_kwargs: "d" * 64,
    )
    issues, ledger, trajectory, config = fixture
    return urban._validate_physical_commands(  # type: ignore[arg-type]
        issues, ledger, trajectory, config
    )


def test_real_producer_shaped_completion_proof_passes_exact_dwell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = _validate_fixture(monkeypatch, _physical_command_fixture())
    assert completions[COMMAND_ID] == (
        SimulationTime(tick=1, sim_time_ns=200_000_000),
        SimulationTime(tick=7, sim_time_ns=1_400_000_000),
    )


def test_arbitrary_finite_thresholds_do_not_replace_declared_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def alter(proof: dict[str, object]) -> None:
        thresholds = proof["thresholds"]
        assert isinstance(thresholds, dict)
        thresholds["settle_duration_ns"] = 200_000_000

    with pytest.raises(ValueError, match="thresholds differ from declared policy"):
        _validate_fixture(
            monkeypatch, _physical_command_fixture(alter_completed=alter)
        )


def test_one_tick_cannot_claim_one_second_of_settling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def alter(proof: dict[str, object]) -> None:
        proof["settling"] = {
            "start_tick": 2,
            "start_sim_time_ns": 400_000_000,
            "end_tick": 2,
            "end_sim_time_ns": 400_000_000,
            "sample_count": 999,
            "sample_duration_ns": 1_000_000_000,
        }

    with pytest.raises(ValueError, match="required physical settling coverage"):
        _validate_fixture(
            monkeypatch,
            _physical_command_fixture(completed_tick=2, alter_completed=alter),
        )


def test_provider_passed_flags_cannot_override_sealed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _physical_command_fixture()
    _, _, trajectory, _ = fixture
    current_raw = trajectory[7][VEHICLE_ID]["raw"]
    assert isinstance(current_raw, dict)
    current_raw["flight_mode"] = "UNDECLARED"

    def alter(proof: dict[str, object]) -> None:
        proof["current_state_digest"] = urban._physical_state_digest(
            VEHICLE_ID, 7, trajectory[7][VEHICLE_ID]
        )
        metrics = proof["metrics"]
        assert isinstance(metrics, dict)
        metrics["flight_mode"] = "UNDECLARED"
        metrics["allowlisted_mode"] = True

    issues, ledger, _, config = fixture
    # Rebuild only the completion proof so its state digest binds the tampered sealed state.
    records = ledger.records
    rebuilt = EventLedger(run_id=RUN_ID, wall_time_ns=lambda: 1)
    rebuilt.append(records[0].event)
    rebuilt.append(records[1].event)
    completed = _proof_document(
        phase="completed",
        baseline_tick=0,
        applied_tick=1,
        current_tick=7,
        baseline=trajectory[0][VEHICLE_ID],
        current=trajectory[7][VEHICLE_ID],
        config=config,
        settling={
            "start_tick": 2,
            "start_sim_time_ns": 400_000_000,
            "end_tick": 7,
            "end_sim_time_ns": 1_400_000_000,
            "sample_count": 6,
            "sample_duration_ns": 1_000_000_000,
        },
    )
    alter(completed)
    _append_proof(rebuilt, completed)
    with pytest.raises(ValueError, match="metrics differ from sealed trajectory states"):
        _validate_fixture(monkeypatch, (issues, rebuilt, trajectory, config))


def test_unsatisfied_sample_breaks_claimed_settling_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="required physical settling coverage"):
        _validate_fixture(
            monkeypatch,
            _physical_command_fixture(unsatisfied_tick=4),
        )


def test_qualifying_completion_at_exact_final_horizon_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_tick = urban.FINAL_TICK - 7
    completions = _validate_fixture(
        monkeypatch,
        _physical_command_fixture(
            baseline_tick=baseline_tick,
            completed_tick=urban.FINAL_TICK,
        ),
    )
    assert completions[COMMAND_ID][1] == SimulationTime(
        tick=urban.FINAL_TICK,
        sim_time_ns=urban.DURATION_NS,
    )


def test_decoded_audit_accepts_real_producer_kind_values() -> None:
    ingress = {"kind": "command_ack_ingress", "command": 400, "result": 0}
    disposition = {
        "kind": "command_ack_disposition",
        "command": 400,
        "status": "matched_outstanding_command",
    }
    records = [ingress, disposition]
    lines = [canonical_json_bytes(record) + b"\n" for record in records]
    audit = {
        "schema_version": "aero-bench.px4-decoded-command-audit/v2",
        "vehicle_system_id": 1,
        "expected_mav_cmd": 400,
        "expected_wire_type": "COMMAND_LONG",
        "records": records,
        "record_sha256s": [
            hashlib.sha256(canonical_json_bytes(record)).hexdigest()
            for record in records
        ],
        "journal_sha256": hashlib.sha256(b"".join(lines)).hexdigest(),
        "terminal_ack_ingress_record": ingress,
        "terminal_ack_ingress_record_sha256": hashlib.sha256(
            canonical_json_bytes(ingress)
        ).hexdigest(),
        "terminal_ack_disposition_record": disposition,
        "terminal_ack_disposition_record_sha256": hashlib.sha256(
            canonical_json_bytes(disposition)
        ).hexdigest(),
    }
    assert urban._validate_decoded_command_audit(
        audit,
        tool_id="flight.arm",
        vehicle_id=VEHICLE_ID,
        config=_config(),  # type: ignore[arg-type]
    ) == hashlib.sha256(canonical_json_bytes(audit)).hexdigest()
