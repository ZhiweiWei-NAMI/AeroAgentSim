"""Focused engineering-profile policy smoke tests; not sealed-run evidence."""
from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from aero_bench.tasks.urban_recovery_demo.contracts import DemoTaskPackage
from aero_bench.tasks.urban_recovery_demo.participant import (
    RecoveryMessage,
    UrbanParticipant,
    UrbanParticipantConfig,
    UrbanParticipantError,
)
from aero_bench.tasks.urban_recovery_demo.planner import RecoveryPlanningError
from tests.test_urban_recovery_network_verifier import _package
from tests.test_urban_recovery_participant import (
    _at,
    _gateway,
    _participant,
)
from tests.test_urban_recovery_participant_lifecycle import _alert, _reply


def _engineering_participant(
    *, variant: str = "baseline", final_tick: int = 10
) -> UrbanParticipant:
    formal = _participant("uav")
    config = UrbanParticipantConfig.model_validate(
        {
            **formal.config.model_dump(mode="json"),
            "execution_profile": "engineering",
            "recovery_variant": variant,
            "final_tick": final_tick,
            "injection_start_ns": 1_000_000_000,
            "patrol_enu_m": {"x": -330.0, "y": -350.0, "z": 20.0},
            "incident_enu_m": {"x": -300.0, "y": -330.0, "z": 20.0},
            "launch_enu_m": {"x": -320.0, "y": -350.0, "z": 20.0},
        }
    )
    return UrbanParticipant(formal.context, config)


def test_inspection_visits_target_dwells_returns_and_finishes_only_after_disarm():
    participant = _engineering_participant(final_tick=600)
    participant.config = participant.config.model_copy(update={"mission_mode": "inspection"})
    participant._advance_mission_after_completion("flight.takeoff")
    assert participant._mission_phase == "patrol"
    participant._mission_phase = "patrol_goto"
    participant._advance_mission_after_completion("flight.goto")
    assert participant._mission_phase == "inspection"
    gateway = _gateway(participant)
    assert participant._dispatch_next_flight_action(gateway, _at(100)) is None
    assert participant._dispatch_next_flight_action(gateway, _at(124)) is None
    assert participant._dispatch_next_flight_action(gateway, _at(125)) is not None
    assert participant._mission_phase == "return_goto"
    participant._advance_mission_after_completion("flight.goto")
    assert participant._mission_phase == "land"
    participant._advance_mission_after_completion("flight.land")
    assert participant._mission_phase == "disarm"
    participant._advance_mission_after_completion("flight.disarm")
    assert participant._mission_phase == "done"


def test_engineering_package_supports_short_horizon_and_early_policy_times() -> None:
    formal = _package()
    assert formal.execution_profile == "formal"
    assert formal.recovery_variant == "baseline"
    document = formal.model_dump(mode="json")
    engineering = DemoTaskPackage.model_validate(
        {
            **document,
            "execution_profile": "engineering",
            "recovery_variant": "direct-recovery",
            "duration_ns": 120_000_000_000,
            "final_tick": 600,
            "recovery": {
                **document["recovery"],
                "injection_start_ns": 5_000_000_000,
                "landing_deadline_ns": 100_000_000_000,
            },
        }
    )
    assert engineering.final_tick == 600
    with pytest.raises(ValidationError, match="formal demo"):
        DemoTaskPackage.model_validate(
            {
                **engineering.model_dump(mode="json"),
                "execution_profile": "formal",
                "recovery_variant": "baseline",
            }
        )


def test_direct_recovery_commands_delivered_goal_without_running_a_star(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    participant = _engineering_participant(variant="direct-recovery")
    alert = _alert()
    participant._pending_alert = alert
    participant._alert_attempts[alert.message_id] = alert
    planner = Mock(side_effect=AssertionError("A* must not run"))
    monkeypatch.setattr(participant, "_plan_from_reply", planner)

    reply = _reply(alert, planner_version="aero-bench.recovery-direct/v1")
    decision = participant._accept_recovery_reply(telemetry={}, reply=reply)
    assert participant._pending_waypoints == [reply.goal_enu_m]
    assert decision is not None and "not executed" in decision
    planner.assert_not_called()

    gateway = _gateway(participant)
    participant._dispatch_next_flight_action(gateway, _at(1))
    assert gateway.command.call_args.kwargs["tool_id"] == "flight.goto"
    assert "direct-recovery ablation" in gateway.decision_summary.call_args.kwargs["summary"]
    assert "without executing A*" in gateway.decision_summary.call_args.kwargs["summary"]


def test_no_recovery_observes_incident_without_alert_hold_or_replanning() -> None:
    participant = _engineering_participant(variant="no-recovery", final_tick=2)
    participant._last_turn_tick = 0
    participant._mission_phase = "await_recovery"
    at = _at(1)
    gateway = _gateway(
        participant,
        safety={
            "transition": "entered",
            "transition_sequence": 1,
            "airspace_state": "inside",
            "transition_engine_sim_time_ns": 5_000_000_000
            + at.sim_time_ns
            - 4_000_000,
        },
    )

    participant.step(gateway, at)
    assert participant._pending_alert is None
    assert participant._pending_hold is False
    assert participant._pending_waypoints == []
    assert participant._mission_phase == "await_recovery"
    assert {call.kwargs["tool_id"] for call in gateway.command.call_args_list} == {
        "network.send"
    }
    raw = base64.b64decode(
        gateway.command.call_args.kwargs["arguments"]["payload_base64"], validate=True
    )
    assert RecoveryMessage.model_validate(json.loads(raw)).kind == "heartbeat"
    assert "not a successful recovery" in gateway.decision_summary.call_args.kwargs["summary"]


def test_engineering_final_tick_records_actual_incomplete_state_and_finishes() -> None:
    participant = _engineering_participant(variant="no-recovery", final_tick=1)
    participant._last_turn_tick = 0
    participant._mission_phase = "await_recovery"
    gateway = _gateway(participant, safety={"airspace_state": "inside"})

    assert participant.step(gateway, _at(1)) == ()
    gateway.command.assert_not_called()
    assert gateway.complete_turn.call_args.kwargs["disposition"] == "finished"
    summary = gateway.decision_summary.call_args.kwargs["summary"]
    assert '"landed":false' in summary
    assert '"mission_phase":"await_recovery"' in summary
    assert "without claiming landing or command completion" in summary
    assert "not a successful recovery" in summary


def test_engineering_operational_recovery_failures_stop_without_masking_bad_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timed_out = _engineering_participant()
    timed_out._pending_alert = _alert()
    timed_out._last_alert_ns = 0
    timed_out._alert_retries = timed_out.config.maximum_retries
    assert timed_out._retry_alert(_gateway(timed_out), _at(50)) is None
    assert timed_out._pending_alert is None
    assert timed_out._flight_progression_stopped is True
    assert "retry budget" in timed_out._mission_failures[-1]

    no_path = _engineering_participant()
    alert = _alert()
    no_path._pending_alert = alert
    no_path._alert_attempts[alert.message_id] = alert
    no_path._mission_phase = "await_recovery"
    monkeypatch.setattr(
        no_path._planner,
        "plan",
        Mock(side_effect=RecoveryPlanningError("no collision-free path")),
    )
    no_path._accept_recovery_reply(
        telemetry={"pose_json": '{"x_m":0.0,"y_m":150.0}'},
        reply=_reply(alert),
    )
    assert no_path._pending_alert is None
    assert no_path._mission_phase == "await_recovery"
    assert no_path._flight_progression_stopped is True
    assert "A* recovery planning failed" in no_path._mission_failures[-1]

    malformed = _engineering_participant()
    alert = _alert()
    malformed._pending_alert = alert
    malformed._alert_attempts[alert.message_id] = alert
    with pytest.raises(UrbanParticipantError, match="planning failed"):
        malformed._accept_recovery_reply(
            telemetry={"pose_json": "not-json"}, reply=_reply(alert)
        )
    assert malformed._flight_progression_stopped is False


def test_engineering_failed_flight_receipt_stops_flight_but_heartbeats_continue() -> None:
    participant = _engineering_participant(final_tick=10)
    gateway = _gateway(participant)
    valid_command = gateway.command.side_effect

    def command(**kwargs):
        receipt = valid_command(**kwargs)
        if kwargs["tool_id"] == "flight.arm":
            return receipt.model_copy(update={"phase": "failed"})
        return receipt

    gateway.command.side_effect = command
    participant.step(gateway, _at(0))
    for tick in range(1, 6):
        participant.step(gateway, _at(tick))

    assert participant._flight_progression_stopped is True
    assert participant._mission_phase == "arm"
    assert [call.kwargs["tool_id"] for call in gateway.command.call_args_list] == [
        "flight.arm",
        "network.send",
        "network.send",
    ]

    malformed = _engineering_participant()
    malformed_gateway = _gateway(malformed)
    valid_command = malformed_gateway.command.side_effect
    malformed_gateway.command.side_effect = lambda **kwargs: valid_command(
        **kwargs
    ).model_copy(update={"run_id": "f" * 64})
    with pytest.raises(UrbanParticipantError, match="identity/time"):
        malformed.step(malformed_gateway, _at(0))
