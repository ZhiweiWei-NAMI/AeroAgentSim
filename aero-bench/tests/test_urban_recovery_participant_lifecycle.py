"""Gateway policy unit tests, never formal simulator or sealed-run evidence."""
from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from aero_bench.runtime.contracts import CommandReceipt
from aero_bench.tasks.urban_recovery_demo.contracts import DemoVector, FINAL_TICK
from aero_bench.tasks.urban_recovery_demo.participant import (
    RecoveryMessage, UrbanParticipantConfig, UrbanParticipantError,
)
from tests.test_urban_recovery_participant import (
    _at, _delivery, _gateway, _mailbox, _participant, _safety, _telemetry,
)


def _alert(**changes):
    return RecoveryMessage.model_validate({
        "schema_version": "aero-bench.urban-recovery-message/v1", "kind": "alert",
        "message_id": "uav.policy.01.alert.test", "sender_agent_id": "uav.policy.01",
        "vehicle_id": "uav.01", "incident_region_id": "region.no-fly.recovery",
        "incident_sequence": 1, **changes,
    })


def _reply(alert, **changes):
    return RecoveryMessage.model_validate({
        "schema_version": "aero-bench.urban-recovery-message/v1", "kind": "recovery_reply",
        "message_id": f"reply.{alert.message_id}", "sender_agent_id": "groundstation.rule",
        "vehicle_id": alert.vehicle_id, "incident_region_id": alert.incident_region_id,
        "incident_sequence": alert.incident_sequence, "cause_message_id": alert.message_id,
        "planner_version": "aero-bench.recovery-a-star/v1",
        "goal_enu_m": {"x": -220.0, "y": 250.0, "z": 45.0},
        "forbidden_polygons": [[[-70.0, 80.0], [70.0, 80.0], [70.0, 220.0], [-70.0, 220.0]]],
        **changes,
    })


def test_groundstation_reply_binds_delivered_incident_sequence():
    participant = _participant()
    alert = _alert()
    gateway = _gateway(participant, deliveries={1: [_delivery(participant, alert, _at(1))]})
    participant.step(gateway, _at(0))
    commands = participant.step(gateway, _at(1))
    assert len(commands) == 1
    payload = gateway.command.call_args.kwargs["arguments"]["payload_base64"]
    reply = RecoveryMessage.model_validate(json.loads(base64.b64decode(payload)))
    assert reply.kind == "recovery_reply"
    assert reply.cause_message_id == alert.message_id
    assert reply.incident_sequence == alert.incident_sequence
    assert reply.vehicle_id == alert.vehicle_id


@pytest.mark.parametrize("change", [
    {"source": "endpoint.uav.02"}, {"sender_agent_id": "uav.policy.02"},
    {"message_id": "forged.outer.id"}, {"destination": "endpoint.uav.02"},
    {"payload_sha256": "f" * 64}, {"work_order_id": "urban.recovery.other-run"},
    {"arrival_time": {"tick": 2, "sim_time_ns": 400_000_000}},
])
def test_rejects_tampered_mailbox_delivery(change):
    participant = _participant()
    at = _at(1)
    payload = _mailbox(participant, at, [_delivery(participant, _alert(), at, **change)])
    with pytest.raises(UrbanParticipantError, match="mailbox"):
        participant._mailbox_messages(payload, at=at)
    assert not participant._received_message_ids


@pytest.mark.parametrize("change", [
    {"run_id": "f" * 64}, {"agent_id": "uav.policy.01"}, {"provider_id": "foreign"},
    {"time_tick": 2}, {"sim_time_ns": 300_000_000}, {"message_count": 1},
    {"messages_digest": "f" * 64}, {"network_step_receipt_digest": "0" * 64},
    {"input_scene_state_digest": None}, {"messages_json": "[ ]"},
])
def test_rejects_tampered_mailbox_batch(change):
    participant = _participant()
    with pytest.raises(UrbanParticipantError, match="mailbox"):
        participant._mailbox_messages({**_mailbox(participant, _at(1)), **change}, at=_at(1))


def test_rejects_replayed_delivery_on_later_barrier():
    participant = _participant()
    message = _alert()
    for tick in (1, 2):
        payload = _mailbox(participant, _at(tick), [_delivery(participant, message, _at(tick))])
        if tick == 1:
            assert participant._mailbox_messages(payload, at=_at(tick)) == (message,)
        else:
            with pytest.raises(UrbanParticipantError, match="replay"):
                participant._mailbox_messages(payload, at=_at(tick))


@pytest.mark.parametrize("change", [
    {"sender_agent_id": "uav.policy.02"}, {"vehicle_id": "uav.02"},
])
def test_groundstation_rejects_body_claiming_another_uav(change):
    participant = _participant()
    message = _alert(**change)
    delivery = _delivery(participant, message, _at(1), source="endpoint.uav.01")
    with pytest.raises(UrbanParticipantError, match="unauthorized"):
        participant._mailbox_messages(_mailbox(participant, _at(1), [delivery]), at=_at(1))


@pytest.mark.parametrize("changes", [
    {"sender_agent_id": "uav.policy.02"},
    {"vehicle_id": "uav.02"},
])
def test_uav_accepts_replies_only_from_its_groundstation(changes):
    participant = _participant("uav")
    message = _reply(_alert(), **changes)
    delivery = _delivery(participant, message, _at(1))
    with pytest.raises(UrbanParticipantError, match="unauthorized"):
        participant._mailbox_messages(_mailbox(participant, _at(1), [delivery]), at=_at(1))


def test_retry_uses_logical_time_unique_ids_and_fails_when_exhausted():
    participant = _participant("uav")
    gateway = _gateway(participant)
    participant._start_alert(gateway, safety=_safety(participant, _at(300), transition_sequence=1), at=_at(300))
    original = participant._pending_alert
    assert original is not None
    assert participant._retry_alert(gateway, _at(349)) is None
    for tick in (350, 400, 450):
        assert participant._retry_alert(gateway, _at(tick)) is not None
    attempts = tuple(participant._alert_attempts.values())
    assert len(attempts) == len({item.message_id for item in attempts}) == 4
    assert all(item.incident_sequence == original.incident_sequence for item in attempts)
    assert all(item.incident_region_id == original.incident_region_id for item in attempts)
    # All sends were accepted; without mailbox delivery there is still no reply.
    assert participant._pending_alert == original
    with pytest.raises(UrbanParticipantError, match="retry budget"):
        participant._retry_alert(gateway, _at(500))
    assert gateway.command.call_count == 4


@pytest.mark.parametrize("change", [
    {"cause_message_id": "unknown.alert"}, {"incident_sequence": 2},
    {"incident_region_id": "region.other"}, {"vehicle_id": "uav.02"},
    {"sender_agent_id": "uav.policy.02"},
])
def test_reply_cause_must_match_an_actual_submitted_incident(change):
    participant = _participant("uav")
    participant._start_alert(_gateway(participant), safety=_safety(participant, _at(300), transition_sequence=1), at=_at(300))
    with pytest.raises(UrbanParticipantError, match="submitted incident"):
        participant._accept_recovery_reply(
            telemetry=_telemetry(participant, _at(301)),
            reply=_reply(participant._pending_alert, **change),
        )
    assert not participant._planned_incidents


def test_delayed_reply_to_original_attempt_cancels_retry_without_duplicate_planning(monkeypatch):
    participant = _participant("uav")
    gateway = _gateway(participant)
    participant._start_alert(gateway, safety=_safety(participant, _at(300), transition_sequence=1), at=_at(300))
    original = participant._pending_alert
    participant._retry_alert(gateway, _at(350))
    retry = tuple(participant._alert_attempts.values())[-1]
    planner = Mock(return_value=(DemoVector(x=0, y=150, z=45), DemoVector(x=-220, y=250, z=45)))
    monkeypatch.setattr(participant, "_plan_from_reply", planner)
    participant._accept_recovery_reply(telemetry={}, reply=_reply(original))
    participant._accept_recovery_reply(telemetry={}, reply=_reply(retry))
    planner.assert_called_once()
    assert len(participant._pending_waypoints) == 1
    assert participant._retry_alert(gateway, _at(500)) is None


def test_goto_arguments_use_the_real_frame_api_and_declared_vertical_datum():
    participant = _participant("uav")
    vector = DemoVector(x=-220.0, y=250.0, z=45.0)
    arguments = participant._goto_arguments(vector)
    recovered = participant._transform.geodetic_to_enu(
        longitude_deg=arguments["longitude_deg"], latitude_deg=arguments["latitude_deg"],
        altitude_m=arguments["altitude_amsl_m"] + 30.0,
    )
    assert (recovered.x, recovered.y, recovered.z) == pytest.approx((vector.x, vector.y, vector.z), abs=1e-5)


def test_uav_alerts_for_a_real_sub_barrier_transition_with_nonzero_warmup_origin():
    participant = _participant("uav")
    participant._last_turn_tick = 300
    participant._mission_phase = "await_recovery"
    at = _at(301)
    gateway = _gateway(participant, safety={
        "transition": "entered", "transition_sequence": 1, "airspace_state": "inside",
        "transition_engine_sim_time_ns": 5_000_000_000 + at.sim_time_ns - 4_000_000,
    })
    participant.step(gateway, at)
    assert participant._pending_alert is not None
    assert participant._pending_alert.incident_sequence == 1
    assert any(call.kwargs["tool_id"] == "flight.hold" for call in gateway.command.call_args_list)


def test_agent_names_scope_all_message_and_command_ids():
    participants = [_participant("uav", vehicle) for vehicle in ("uav.01", "uav.02")]
    ids = [participant._id(prefix, _at(0)) for participant in participants for prefix in ("heartbeat", "flight.arm")]
    assert len(ids) == len(set(ids))


def test_final_groundstation_tick_observes_and_finishes_without_commands():
    participant = _participant()
    # Isolate the last turn; do not represent this as a simulated 600-second run.
    participant._last_turn_tick = FINAL_TICK - 1
    gateway = _gateway(participant)
    assert participant.step(gateway, _at(FINAL_TICK)) == ()
    gateway.command.assert_not_called()
    gateway.observe.assert_called_once()
    assert gateway.complete_turn.call_args.kwargs["disposition"] == "finished"
    assert gateway.complete_turn.call_args.kwargs["observation_ids"] == (participant.config.mailbox_observation_id,)


def test_final_groundstation_tick_rejects_an_unresolved_alert():
    participant = _participant()
    participant._last_turn_tick = FINAL_TICK - 1
    delivery = _delivery(participant, _alert(), _at(FINAL_TICK))
    gateway = _gateway(participant, deliveries={FINAL_TICK: [delivery]})
    with pytest.raises(UrbanParticipantError, match="unresolved recovery alert"):
        participant.step(gateway, _at(FINAL_TICK))
    gateway.command.assert_not_called()
    gateway.complete_turn.assert_not_called()


def _final_uav(changes=None):
    participant = _participant("uav")
    participant._last_turn_tick = FINAL_TICK - 1
    participant._mission_phase = "done"
    gateway = _gateway(participant, telemetry={
        "armed": False, "in_air": False, "landed": True,
        "ground_contact": True, "collision_contact": False, **(changes or {}),
    })
    return participant, gateway


def test_final_uav_tick_requires_three_authorized_observations_and_no_new_command():
    participant, gateway = _final_uav()
    assert participant.step(gateway, _at(FINAL_TICK)) == ()
    gateway.command.assert_not_called()
    assert gateway.observe.call_count == 3
    assert gateway.complete_turn.call_args.kwargs["disposition"] == "finished"
    assert len(gateway.complete_turn.call_args.kwargs["observation_ids"]) == 3


@pytest.mark.parametrize("change", [
    {"armed": True}, {"in_air": True}, {"landed": False},
    {"ground_contact": False}, {"collision_contact": True},
])
def test_final_uav_tick_never_substitutes_local_phase_for_physical_state(change):
    participant, gateway = _final_uav(change)
    with pytest.raises(UrbanParticipantError, match="final UAV observation"):
        participant.step(gateway, _at(FINAL_TICK))
    gateway.command.assert_not_called()
    gateway.complete_turn.assert_not_called()


def test_final_tick_can_observe_a_real_completed_disarm_receipt():
    participant, gateway = _final_uav()
    participant._mission_phase = "disarm"
    participant._active_flight_command_id = "disarm.final"
    participant._active_flight_tool_id = "flight.disarm"
    gateway.command_status.return_value = CommandReceipt(
        run_id=participant.context.run_id, command_id="disarm.final", provider_id="flight",
        time=_at(FINAL_TICK), phase="completed",
    )
    participant.step(gateway, _at(FINAL_TICK))
    gateway.command.assert_not_called()
    assert participant._mission_phase == "done"


@pytest.mark.parametrize("tick", [1, FINAL_TICK + 1])
def test_skipped_or_out_of_horizon_turn_cannot_observe_or_command(tick):
    participant = _participant()
    gateway = _gateway(participant)
    with pytest.raises(UrbanParticipantError, match="sequential demo clock"):
        participant.step(gateway, _at(tick))
    gateway.observe.assert_not_called()
    gateway.command.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("run_id", "b" * 64), ("command_id", "other.command"),
    ("provider_id", "flight"), ("time", _at(1)), ("phase", "failed"),
])
def test_network_submission_rejects_foreign_future_or_failed_receipts(field, value):
    participant = _participant()
    gateway = _gateway(participant)
    good_command = gateway.command.side_effect
    gateway.command.side_effect = lambda **kwargs: good_command(**kwargs).model_copy(update={field: value})
    with pytest.raises(UrbanParticipantError, match="receipt|Provider command failed"):
        participant.step(gateway, _at(0))
    gateway.complete_turn.assert_not_called()


@pytest.mark.parametrize("role", ["uav", "groundstation"])
def test_each_command_has_one_prior_correlated_summary_of_current_observations(role):
    participant = _participant(role)
    gateway = _gateway(participant)
    participant.step(gateway, _at(0))
    summaries = {}
    command_ids = set()
    expected_observations = tuple(sorted(
        observation_id for observation_id in (
            participant.config.mailbox_observation_id, participant.config.telemetry_observation_id,
            participant.config.safety_observation_id,
        ) if observation_id is not None
    ))
    for call in gateway.mock_calls:
        name, _, kwargs = call
        if name == "decision_summary" and kwargs["command_id"] is not None:
            command_id = kwargs["command_id"]
            assert command_id not in summaries
            assert command_id not in command_ids
            assert kwargs["observation_ids"] == expected_observations
            summaries[command_id] = kwargs
        elif name == "command":
            assert call.kwargs["command_id"] in summaries
            command_ids.add(call.kwargs["command_id"])
    assert command_ids == set(summaries)
    assert gateway.decision_summary.call_args.kwargs["command_id"] is None


def test_gateway_payload_digest_is_checked_before_policy_reads():
    participant = _participant()
    gateway = _gateway(participant)
    envelope = gateway.observe(observation_id=participant.config.mailbox_observation_id, at=_at(0))
    gateway.observe.side_effect = None
    gateway.observe.return_value = envelope.model_copy(update={"payload_digest": "f" * 64})
    with pytest.raises(UrbanParticipantError, match="Gateway digest"):
        participant.step(gateway, _at(0))
    gateway.command.assert_not_called()


def test_run_honors_connection_timeout_and_closes_on_failure(monkeypatch):
    participant = _participant()
    gateway = Mock()
    gateway.probe.return_value = {
        "schema_version": "aero-bench.gateway-probe/v1",
        "run_id": participant.context.run_id,
        "status": "ready",
        "current": _at(0).model_dump(mode="json"),
    }
    monkeypatch.setattr(participant.context, "gateway", lambda: gateway)
    monkeypatch.setattr(participant, "step", Mock(side_effect=UrbanParticipantError("unit failure")))
    with pytest.raises(UrbanParticipantError, match="unit failure"):
        participant.run(timeout_seconds=17)
    gateway.connect.assert_called_once_with(timeout_seconds=17)
    gateway.close.assert_called_once()


@pytest.mark.parametrize("change", [
    {"vehicle_agent_ids": {}},
    {"vehicle_endpoint_ids": {"uav.01": "endpoint.same", "uav.02": "endpoint.same"}},
    {"mailbox_observation_id": "network.mailbox.endpoint.uav.01"},
    {"forbidden_polygons": [[[0, 0], [0, 0], [0, 0]]]},
    {"forbidden_polygons": [[[501, 0], [0, 1], [0, 0]]]},
    {"forbidden_polygons": [[[float("inf"), 0], [0, 1], [0, 0]]]},
])
def test_participant_config_rejects_ambiguous_bindings_or_invalid_constraints(change):
    with pytest.raises(ValidationError):
        UrbanParticipantConfig.model_validate({**_participant().config.model_dump(mode="json"), **change})
