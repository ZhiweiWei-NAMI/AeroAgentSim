from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from aero_bench.agent.runtime import AgentContext
from aero_bench.config.models import NamedValue
from aero_bench.gateway.contracts import ObservationEnvelope, ToolResult
from aero_bench.runtime.contracts import CommandReceipt, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import STEP_NS
from aero_bench.tasks.urban_recovery_demo.participant import (
    RecoveryMessage, UrbanParticipant, UrbanParticipantConfig, UrbanParticipantError,
)


def test_recovery_heartbeat_has_no_incident_fields() -> None:
    message = RecoveryMessage(
        schema_version="aero-bench.urban-recovery-message/v1",
        kind="heartbeat",
        message_id="heartbeat.0000000000000000.0001",
        sender_agent_id="uav.policy.01",
    )
    assert message.forbidden_polygons == ()


def test_recovery_alert_requires_engine_incident_identity() -> None:
    with pytest.raises(ValidationError, match="engine incident identity"):
        RecoveryMessage(
            schema_version="aero-bench.urban-recovery-message/v1",
            kind="alert",
            message_id="alert.0000000000000001.0001",
            sender_agent_id="uav.policy.01",
            vehicle_id="uav.recovery.01",
            incident_region_id="region.no-fly",
        )


def test_recovery_reply_requires_explicit_public_constraints() -> None:
    with pytest.raises(ValidationError, match="recovery reply"):
        RecoveryMessage(
            schema_version="aero-bench.urban-recovery-message/v1",
            kind="recovery_reply",
            message_id="reply.0000000000000001.0001",
            sender_agent_id="groundstation.rule",
            vehicle_id="uav.recovery.01",
            incident_region_id="region.no-fly",
            planner_version="aero-bench.recovery-a-star/v1",
            cause_message_id="alert.0000000000000001.0001",
        )


def _participant(role="groundstation", vehicle="uav.01") -> UrbanParticipant:
    context = object.__new__(AgentContext)
    context.contract = SimpleNamespace(
        run_id="a" * 64, agent=SimpleNamespace(
            agent_id="groundstation.rule" if role == "groundstation" else f"uav.policy.{vehicle[-2:]}"
        )
    )
    config = UrbanParticipantConfig(
        role=role,
        endpoint_id="endpoint.groundstation" if role == "groundstation" else f"endpoint.{vehicle}",
        groundstation_endpoint_id="endpoint.groundstation",
        mailbox_observation_id="network.mailbox.endpoint.groundstation" if role == "groundstation" else f"network.mailbox.endpoint.{vehicle}",
        heartbeat_interval_ns=1_000_000_000, injection_start_ns=60_000_000_000,
        response_timeout_ns=10_000_000_000, maximum_retries=3,
        vehicle_radius_m=1.0, obstacle_margin_m=5.0, cruise_agl_m=45.0,
        origin_latitude_deg=31.2304, origin_longitude_deg=121.4737,
        origin_ellipsoid_height_m=50.0, origin_amsl_m=20.0,
        **({
            "recovery_goal_enu_m": {"x": -220.0, "y": 250.0, "z": 45.0},
            "forbidden_polygons": (((-70.0, 80.0), (70.0, 80.0), (70.0, 220.0), (-70.0, 220.0)),),
            "vehicle_endpoint_ids": {"uav.02": "endpoint.uav.02", "uav.01": "endpoint.uav.01"},
            "vehicle_agent_ids": {"uav.02": "uav.policy.02", "uav.01": "uav.policy.01"},
        } if role == "groundstation" else {
            "vehicle_id": vehicle,
            "telemetry_observation_id": f"observation.{vehicle}.telemetry",
            "safety_observation_id": f"observation.{vehicle}.safety",
        }),
    )
    return UrbanParticipant(context, config)


def _at(tick):
    return SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)


def _mailbox(participant, at, deliveries=()):
    # Unit-test records only; these are not simulator or sealed-run evidence.
    encoded = canonical_json_bytes(sorted(deliveries, key=lambda item: item["message_id"]))
    return {
        "schema_version": "aero-bench.network-mailbox-observation/v1",
        "run_id": participant.context.run_id, "provider_id": "network",
        "agent_id": participant.context.agent_id,
        "observation_id": participant.config.mailbox_observation_id,
        "mailbox_endpoint_id": participant.config.endpoint_id,
        "time_tick": at.tick, "sim_time_ns": at.sim_time_ns,
        "stage": "network" if at.tick else "reset",
        "input_scene_state_digest": "b" * 64 if at.tick else None,
        "motion_barrier_digest": "c" * 64 if at.tick else None,
        "network_step_receipt_digest": "d" * 64,
        "message_count": len(deliveries), "messages_digest": hashlib.sha256(encoded).hexdigest(),
        "messages_json": encoded.decode(),
    }


def _delivery(participant, message, at, **changes):
    raw = canonical_json_bytes(message.model_dump(mode="json"))
    source = "endpoint.groundstation" if message.sender_agent_id == "groundstation.rule" else f"endpoint.uav.{message.sender_agent_id[-2:]}"
    return {
        "work_order_id": f"urban.recovery.{participant.context.run_id[:16]}",
        "message_id": message.message_id, "command_id": f"command.{message.message_id}",
        "sender_agent_id": message.sender_agent_id,
        "source": source, "destination": participant.config.endpoint_id,
        "payload_base64": base64.b64encode(raw).decode(),
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "send_time": _at(at.tick - 1).model_dump(mode="json"),
        "arrival_time": at.model_dump(mode="json"), "path": [source, participant.config.endpoint_id],
        **changes,
    }


def _telemetry(participant, at, **changes):
    return {
        "schema_version": "aero-bench.observation.urban-telemetry.v1",
        "vehicle_id": participant.config.vehicle_id, "simulation_time_ns": at.sim_time_ns,
        "pose_json": '{"x_m":0.0,"y_m":150.0,"z_m":45.0}',
        "armed": True, "in_air": True, "landed": False,
        "ground_contact": False, "collision_contact": False,
        **changes,
    }


def _safety(participant, at, **changes):
    return {
        "schema_version": "aero-bench.observation.urban-safety.v1",
        "vehicle_id": participant.config.vehicle_id, "simulation_time_ns": at.sim_time_ns,
        "source": "gazebo.system", "region_id": "region.no-fly.recovery",
        "airspace_state": "outside", "transition": "none", "transition_sequence": 0,
        "engine_sim_time_ns": 5_000_000_000 + at.sim_time_ns,
        "logical_origin_engine_ns": 5_000_000_000,
        "transition_engine_sim_time_ns": None,
        **changes,
    }


def _gateway(participant, *, deliveries=None, telemetry=None, safety=None):
    gateway = Mock()

    def command(**kwargs):
        return CommandReceipt(
            run_id=participant.context.run_id, command_id=kwargs["command_id"],
            provider_id="network" if kwargs["tool_id"] == "network.send" else "flight",
            phase="accepted", time=kwargs["at"],
        )

    def observe(*, observation_id, at):
        if observation_id == participant.config.mailbox_observation_id:
            payload = _mailbox(participant, at, (deliveries or {}).get(at.tick, ()))
        elif observation_id == participant.config.telemetry_observation_id:
            payload = _telemetry(participant, at, **(telemetry or {}))
        else:
            assert observation_id == participant.config.safety_observation_id
            payload = _safety(participant, at, **(safety or {}))
        return ObservationEnvelope(
            run_id=participant.context.run_id, agent_id=participant.context.agent_id,
            observation_id=observation_id, time=at,
            payload_schema={"path": "schemas/unit-test.json", "sha256": "e" * 64},
            payload=tuple(NamedValue(name=name, value=value) for name, value in sorted(payload.items())),
            payload_digest=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        )

    gateway.command.side_effect = command
    gateway.observe.side_effect = observe
    return gateway


def test_participant_accepts_gateway_tool_result_receipt_chain() -> None:
    participant = _participant("uav")
    gateway = _gateway(participant)
    emitted: list[ToolResult] = []

    def command(**kwargs):
        provider_id = (
            "network" if kwargs["tool_id"] == "network.send" else "flight"
        )
        receipts = tuple(
            CommandReceipt(
                run_id=participant.context.run_id,
                command_id=kwargs["command_id"],
                provider_id=provider_id,
                phase=phase,
                time=kwargs["at"],
            )
            for phase in ("received", "accepted")
        )
        result = ToolResult(
            command_id=kwargs["command_id"],
            receipts=receipts,
            response=(),
        )
        emitted.append(result)
        return result

    gateway.command.side_effect = command

    command_ids = participant.step(gateway, _at(0))

    assert len(command_ids) == 2
    assert [result.command_id for result in emitted] == list(command_ids)
    assert all(
        [receipt.phase for receipt in result.receipts] == ["received", "accepted"]
        for result in emitted
    )


def test_groundstation_heartbeats_have_unique_ids_and_explicit_authorized_routes() -> None:
    participant = _participant()
    gateway = _gateway(participant)
    first = SimulationTime(tick=0, sim_time_ns=0)
    command_ids = participant.step(gateway, first)
    assert len(command_ids) == 2
    calls = gateway.command.call_args_list
    assert [call.kwargs["arguments"]["destination"] for call in calls] == [
        "endpoint.uav.01", "endpoint.uav.02",
    ]
    messages = []
    for call in calls:
        arguments = call.kwargs["arguments"]
        raw = base64.b64decode(arguments["payload_base64"], validate=True)
        assert hashlib.sha256(raw).hexdigest() == arguments["payload_sha256"]
        message = RecoveryMessage.model_validate(json.loads(raw))
        assert message.kind == "heartbeat"
        assert message.vehicle_id is None
        assert message.sender_agent_id == "groundstation.rule"
        messages.append(message.message_id)
    assert len(set(messages)) == 2
    assert gateway.complete_turn.call_args.kwargs["command_ids"] == command_ids
    participant.step(gateway, SimulationTime(tick=1, sim_time_ns=200_000_000))
    assert gateway.command.call_count == 2
    for tick in range(2, 6):
        participant.step(gateway, _at(tick))
    assert gateway.command.call_count == 4
    assert len({call.kwargs["arguments"]["message_id"] for call in gateway.command.call_args_list}) == 4


@pytest.mark.parametrize("destination", [None, "", "endpoint.unauthorized"])
def test_groundstation_rejects_missing_or_unauthorized_heartbeat_destination(destination) -> None:
    participant = _participant()
    gateway = Mock()
    message = RecoveryMessage(
        schema_version="aero-bench.urban-recovery-message/v1", kind="heartbeat",
        message_id="heartbeat.test", sender_agent_id="groundstation.rule",
    )
    with pytest.raises(UrbanParticipantError, match="authorized destination"):
        participant._send(gateway, message=message, at=SimulationTime(tick=0, sim_time_ns=0), destination=destination)
    gateway.command.assert_not_called()


def test_groundstation_cannot_impersonate_a_uav_sender() -> None:
    participant = _participant()
    gateway = Mock()
    message = RecoveryMessage(
        schema_version="aero-bench.urban-recovery-message/v1", kind="heartbeat",
        message_id="heartbeat.test", sender_agent_id="uav.policy.01",
    )
    with pytest.raises(UrbanParticipantError, match="sender"):
        participant._send(gateway, message=message, at=SimulationTime(tick=0, sim_time_ns=0), destination="endpoint.uav.01")
    gateway.command.assert_not_called()
