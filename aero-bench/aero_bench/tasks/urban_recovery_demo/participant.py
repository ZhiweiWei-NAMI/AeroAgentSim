"""Restricted rule participants for the urban recovery demonstration.

The participant is deliberately a thin policy layer over the Agent Gateway.  It
never creates telemetry, airspace transitions, network deliveries, or flight
success.  All observations and command outcomes remain Gateway-owned.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import time
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from aero_bench.agent.runtime import AgentContext, AgentFrameworkError, GatewayClient
from aero_bench.config.models import Identifier, StrictModel
from aero_bench.gateway.contracts import ToolResult
from aero_bench.providers.ns3.provider import NetworkMailboxMessage, NetworkMailboxObservationPayload
from aero_bench.runtime.contracts import CommandReceipt, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import (
    DemoVector,
    FINAL_TICK,
    STEP_NS,
)
from aero_bench.tasks.urban_recovery_demo.planner import (
    RecoveryPlanner,
    RecoveryPlanningError,
)
from aero_bench.world.frame_math import EnuTransform, Vector3


RECOVERY_MESSAGE_SCHEMA = "aero-bench.urban-recovery-message/v1"
TELEMETRY_SCHEMA = "aero-bench.observation.urban-telemetry.v1"
SAFETY_SCHEMA = "aero-bench.observation.urban-safety.v1"


class UrbanParticipantError(AgentFrameworkError):
    """A participant input or authoritative Gateway result is invalid."""


def _validate_public_polygons(polygons: tuple[tuple[tuple[float, float], ...], ...]) -> None:
    for polygon in polygons:
        if len(set(polygon)) < 3 or any(
            not math.isfinite(value) or not -500.0 <= value <= 500.0
            for point in polygon for value in point
        ):
            raise ValueError("recovery constraints must be finite polygons inside the Shanghai crop")


class RecoveryMessage(StrictModel):
    """Typed payload exchanged through the real ns-3 network provider."""

    schema_version: Literal["aero-bench.urban-recovery-message/v1"]
    kind: Literal["heartbeat", "alert", "recovery_reply"]
    message_id: Identifier
    sender_agent_id: Identifier
    vehicle_id: Identifier | None = None
    incident_region_id: Identifier | None = None
    incident_sequence: int | None = Field(default=None, ge=0)
    planner_version: Literal[
        "aero-bench.recovery-a-star/v1",
        "aero-bench.recovery-direct/v1",
    ] | None = None
    goal_enu_m: DemoVector | None = None
    forbidden_polygons: tuple[tuple[tuple[float, float], ...], ...] = ()
    cause_message_id: Identifier | None = None

    @model_validator(mode="after")
    def kind_fields_are_closed(self) -> "RecoveryMessage":
        _validate_public_polygons(self.forbidden_polygons)
        if self.kind == "heartbeat":
            if any(
                value is not None
                for value in (
                    self.vehicle_id,
                    self.incident_region_id,
                    self.incident_sequence,
                    self.planner_version,
                    self.goal_enu_m,
                    self.cause_message_id,
                )
            ) or self.forbidden_polygons:
                raise ValueError("heartbeat message contains incident fields")
        elif self.kind == "alert":
            if (
                self.vehicle_id is None
                or self.incident_region_id is None
                or self.incident_sequence is None
            ):
                raise ValueError("alert message lacks engine incident identity")
            if any(
                value is not None
                for value in (self.planner_version, self.goal_enu_m, self.cause_message_id)
            ) or self.forbidden_polygons:
                raise ValueError("alert message contains recovery fields")
        else:
            if (
                self.vehicle_id is None
                or self.incident_region_id is None
                or self.incident_sequence is None
                or self.planner_version is None
                or self.goal_enu_m is None
                or not self.forbidden_polygons
                or self.cause_message_id is None
            ):
                raise ValueError("recovery reply lacks deterministic planning inputs")
        return self


def _payload_for_message(message: RecoveryMessage) -> str:
    raw = canonical_json_bytes(message.model_dump(mode="json"))
    return base64.b64encode(raw).decode("ascii")


def _message_from_base64(value: object) -> RecoveryMessage:
    if not isinstance(value, str):
        raise UrbanParticipantError("mailbox payload is not base64 text")
    try:
        raw = base64.b64decode(value, validate=True)
        if not raw or base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("non-canonical base64")
        document = json.loads(raw)
        if canonical_json_bytes(document) != raw:
            raise ValueError("non-canonical JSON")
        return RecoveryMessage.model_validate(document)
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as error:
        raise UrbanParticipantError("mailbox payload is not a valid recovery message") from error


def _payload_map(envelope: object) -> dict[str, object]:
    payload = getattr(envelope, "payload", None)
    if not isinstance(payload, tuple):
        raise UrbanParticipantError("Gateway observation payload is not canonical")
    result: dict[str, object] = {}
    for item in payload:
        if item.name in result:
            raise UrbanParticipantError("Gateway observation contains duplicate fields")
        result[item.name] = item.value
    return result


class UrbanParticipantConfig(StrictModel):
    """Only public task inputs needed by one restricted participant."""

    role: Literal["uav", "groundstation"]
    execution_profile: Literal["formal", "engineering"] = "formal"
    recovery_variant: Literal["baseline", "direct-recovery", "no-recovery"] = "baseline"
    mission_mode: Literal["recovery", "inspection"] = "recovery"
    final_tick: int = Field(default=FINAL_TICK, gt=0, le=FINAL_TICK)
    endpoint_id: Identifier
    groundstation_endpoint_id: Identifier
    vehicle_id: Identifier | None = None
    telemetry_observation_id: Identifier | None = None
    safety_observation_id: Identifier | None = None
    mailbox_observation_id: Identifier
    heartbeat_interval_ns: Literal[1_000_000_000]
    injection_start_ns: int = Field(ge=0, le=120_000_000_000)
    response_timeout_ns: int = Field(gt=0, le=30_000_000_000)
    maximum_retries: int = Field(ge=0, le=5)
    vehicle_radius_m: float = Field(gt=0, le=5, allow_inf_nan=False)
    obstacle_margin_m: float = Field(gt=0, le=50, allow_inf_nan=False)
    cruise_agl_m: float = Field(gt=0, le=200, allow_inf_nan=False)
    origin_latitude_deg: float = Field(gt=-90, lt=90, allow_inf_nan=False)
    origin_longitude_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    origin_ellipsoid_height_m: float = Field(allow_inf_nan=False)
    origin_amsl_m: float = Field(allow_inf_nan=False)
    patrol_enu_m: DemoVector | None = None
    incident_enu_m: DemoVector | None = None
    launch_enu_m: DemoVector | None = None
    recovery_goal_enu_m: DemoVector | None = None
    forbidden_polygons: tuple[tuple[tuple[float, float], ...], ...] = ()
    vehicle_endpoint_ids: dict[Identifier, Identifier] = Field(default_factory=dict)
    vehicle_agent_ids: dict[Identifier, Identifier] = Field(default_factory=dict)

    @model_validator(mode="after")
    def role_inputs_are_closed(self) -> "UrbanParticipantConfig":
        if self.mission_mode == "inspection" and self.execution_profile != "engineering":
            raise ValueError("inspection mission mode is engineering-only")
        _validate_public_polygons(self.forbidden_polygons)
        if self.execution_profile == "formal" and (
            self.recovery_variant != "baseline"
            or self.final_tick != FINAL_TICK
            or self.injection_start_ns < 60_000_000_000
        ):
            raise ValueError(
                "formal participant requires the 3,000-tick baseline profile and formal injection timing"
            )
        if self.mailbox_observation_id != f"network.mailbox.{self.endpoint_id}":
            raise ValueError("participant mailbox must name its own endpoint")
        if self.role == "uav":
            if self.endpoint_id == self.groundstation_endpoint_id:
                raise ValueError("UAV cannot own the groundstation endpoint")
            if any(
                value is None
                for value in (
                    self.vehicle_id,
                    self.telemetry_observation_id,
                    self.safety_observation_id,
                )
            ):
                raise ValueError("UAV participant requires all own observation IDs")
        elif any(
            value is not None
            for value in (
                self.vehicle_id,
                self.telemetry_observation_id,
                self.safety_observation_id,
                self.patrol_enu_m,
                self.incident_enu_m,
                self.launch_enu_m,
            )
        ):
            raise ValueError("groundstation participant cannot claim UAV observations or mission vectors")
        if self.role == "groundstation":
            if self.recovery_goal_enu_m is None or not self.forbidden_polygons:
                raise ValueError(
                    "groundstation participant requires public recovery constraints"
                )
            if (
                self.endpoint_id != self.groundstation_endpoint_id
                or len(self.vehicle_endpoint_ids) != 2
                or len(set(self.vehicle_endpoint_ids.values())) != 2
                or self.endpoint_id in self.vehicle_endpoint_ids.values()
                or set(self.vehicle_agent_ids) != set(self.vehicle_endpoint_ids)
                or set(self.vehicle_agent_ids.values()) != {"uav.policy.01", "uav.policy.02"}
            ):
                raise ValueError("groundstation requires exact distinct vehicle/Agent/endpoint bindings")
        elif (
            self.recovery_goal_enu_m is not None
            or self.forbidden_polygons
            or self.vehicle_endpoint_ids
            or self.vehicle_agent_ids
        ):
            raise ValueError("UAV participant cannot carry groundstation routing inputs")
        if self.origin_ellipsoid_height_m != self.origin_amsl_m + 30.0:
            raise ValueError("origin vertical datum must include the declared 30 m geoid")
        return self


class UrbanParticipant:
    """One deterministic participant process using only its Gateway grants."""

    def __init__(self, context: AgentContext, config: UrbanParticipantConfig) -> None:
        if not isinstance(context, AgentContext):
            raise TypeError("UrbanParticipant requires an AgentContext")
        self.context = context
        self.config = config
        expected_agents = {"groundstation.rule"} if config.role == "groundstation" else {"uav.policy.01", "uav.policy.02"}
        if context.agent_id not in expected_agents:
            raise UrbanParticipantError("participant role differs from its Gateway Agent identity")
        self._last_turn_tick = -1
        self._observed_at: dict[str, SimulationTime] = {}
        self._received_message_ids: set[str] = set()
        self._pending_alert: RecoveryMessage | None = None
        self._alert_attempts: dict[str, RecoveryMessage] = {}
        self._last_alert_ns: int | None = None
        self._alert_retries = 0
        self._planned_incidents: set[tuple[str, int]] = set()
        self._entered_sequences: set[int] = set()
        self._replied_causes: set[str] = set()
        self._planned_causes: set[str] = set()
        self._pending_waypoints: list[DemoVector] = []
        self._active_flight_command_id: str | None = None
        self._active_flight_tool_id: str | None = None
        self._mission_phase = "arm" if config.role == "uav" else "station"
        self._inspection_started_ns: int | None = None
        self._pending_hold = False
        self._flight_progression_stopped = False
        self._failed_command_ids: list[str] = []
        self._mission_failures: list[str] = []
        self._last_heartbeat_ns: int | None = None
        self._command_counter = 0
        self._planner = RecoveryPlanner(
            vehicle_radius_m=config.vehicle_radius_m,
            obstacle_margin_m=config.obstacle_margin_m,
        )
        self._transform = EnuTransform.from_origin(
            longitude_deg=config.origin_longitude_deg,
            latitude_deg=config.origin_latitude_deg,
            altitude_m=config.origin_ellipsoid_height_m,
        )

    def _id(self, prefix: str, at: SimulationTime) -> str:
        self._command_counter += 1
        return f"{self.context.agent_id}.{prefix}.{at.tick:016d}.{self._command_counter:04d}"

    def _observe(
        self, gateway: GatewayClient, observation_id: str, at: SimulationTime
    ) -> dict[str, object]:
        envelope = gateway.observe(observation_id=observation_id, at=at)
        if envelope.run_id != self.context.run_id or envelope.agent_id != self.context.agent_id:
            raise UrbanParticipantError("observation identity does not belong to this Agent")
        if envelope.observation_id != observation_id or envelope.time != at:
            raise UrbanParticipantError("observation identity/time does not equal the requested barrier")
        payload = _payload_map(envelope)
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != envelope.payload_digest:
            raise UrbanParticipantError("observation payload differs from its Gateway digest")
        self._observed_at[observation_id] = at
        return payload

    def _record_command_decision(
        self, gateway: GatewayClient, *, command_id: str, at: SimulationTime, summary: str
    ) -> None:
        gateway.decision_summary(
            summary_id=self._id("command-decision", at), at=at, summary=summary,
            command_id=command_id,
            observation_ids=tuple(sorted(
                observation_id for observation_id, observed_at in self._observed_at.items()
                if observed_at == at
            )),
        )

    def _validate_command_receipt(
        self,
        result: CommandReceipt | ToolResult,
        *,
        command_id: str,
        provider_id: str,
        at: SimulationTime,
    ) -> bool:
        if isinstance(result, ToolResult):
            if result.command_id != command_id or not result.receipts:
                raise UrbanParticipantError(
                    "command receipt differs from its authorized identity/time"
                )
            receipts = result.receipts
            require_issued_time = True
        elif isinstance(result, CommandReceipt):
            receipts = (result,)
            require_issued_time = False
        else:
            raise UrbanParticipantError(
                "command receipt differs from its authorized identity/time"
            )
        for receipt in receipts:
            time_is_invalid = (
                receipt.time != at
                if require_issued_time
                else receipt.time.tick > at.tick
                or receipt.time.sim_time_ns > at.sim_time_ns
            )
            if (
                receipt.run_id != self.context.run_id
                or receipt.command_id != command_id
                or receipt.provider_id != provider_id
                or time_is_invalid
            ):
                raise UrbanParticipantError(
                    "command receipt differs from its authorized identity/time"
                )
        if receipts[-1].phase == "failed":
            if self.config.execution_profile == "engineering":
                return False
            raise UrbanParticipantError("authorized Provider command failed")
        return True

    def _record_engineering_mission_failure(
        self, reason: str, *, stops_flight: bool = True
    ) -> None:
        self._mission_failures.append(reason)
        if stops_flight and self.config.role == "uav":
            self._flight_progression_stopped = True

    def _record_engineering_command_failure(
        self, *, command_id: str, stops_flight: bool
    ) -> None:
        self._failed_command_ids.append(command_id)
        self._record_engineering_mission_failure(
            f"authoritative failed command receipt: {command_id}",
            stops_flight=stops_flight,
        )

    def _send(
        self,
        gateway: GatewayClient,
        *,
        message: RecoveryMessage,
        at: SimulationTime,
        destination: str | None = None,
        decision_detail: str | None = None,
    ) -> str:
        command_id = self._id("network.send", at)
        resolved_destination = destination if destination is not None else (
            self.config.groundstation_endpoint_id
            if self.config.role == "uav"
            else self.config.vehicle_endpoint_ids.get(str(message.vehicle_id), "")
        )
        allowed_destinations = (
            {self.config.groundstation_endpoint_id}
            if self.config.role == "uav"
            else set(self.config.vehicle_endpoint_ids.values())
        )
        if not resolved_destination or resolved_destination not in allowed_destinations:
            raise UrbanParticipantError("network message has no authorized destination endpoint")
        if message.sender_agent_id != self.context.agent_id:
            raise UrbanParticipantError("network message sender differs from its Gateway identity")
        self._record_command_decision(
            gateway, command_id=command_id, at=at,
            summary=(
                decision_detail
                if decision_detail is not None
                else (
                    f"submit {message.kind} {message.message_id} to {resolved_destination}; "
                    f"incident={message.incident_region_id}/{message.incident_sequence}; "
                    f"cause={message.cause_message_id}; delivery requires ns-3 mailbox evidence"
                )
            ),
        )
        receipt = gateway.command(
            command_id=command_id,
            tool_id="network.send",
            at=at,
            arguments={
                "work_order_id": f"urban.recovery.{self.context.run_id[:16]}",
                "message_id": message.message_id,
                "source": self.config.endpoint_id,
                "destination": resolved_destination,
                "payload_base64": _payload_for_message(message),
                "payload_sha256": hashlib.sha256(
                    canonical_json_bytes(message.model_dump(mode="json"))
                ).hexdigest(),
                "traffic_class": "best_effort",
                "priority": 0,
                "reliability": "best_effort",
            },
        )
        if not self._validate_command_receipt(
            receipt, command_id=command_id, provider_id="network", at=at
        ):
            self._record_engineering_command_failure(
                command_id=command_id, stops_flight=True
            )
        return command_id

    def _goto_arguments(self, vector: DemoVector) -> dict[str, object]:
        longitude, latitude, ellipsoid_height = self._transform.enu_to_geodetic(
            Vector3(vector.x, vector.y, vector.z)
        )
        return {
            "vehicle_id": self.config.vehicle_id,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "altitude_amsl_m": ellipsoid_height - 30.0,
            "yaw_deg": 0.0,
        }

    def _plan_from_reply(
        self,
        *,
        telemetry: Mapping[str, object],
        reply: RecoveryMessage,
    ) -> tuple[DemoVector, ...] | None:
        try:
            pose = json.loads(str(telemetry["pose_json"]))
            start = (float(pose["x_m"]), float(pose["y_m"]))
            if not all(math.isfinite(value) for value in start):
                raise ValueError("telemetry pose is not finite")
            goal = reply.goal_enu_m
            if goal is None:
                raise ValueError("reply has no goal")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise UrbanParticipantError(
                "deterministic recovery planning failed"
            ) from error
        try:
            return self._planner.plan(
                start_enu_m=start,
                goal_enu_m=(goal.x, goal.y),
                forbidden_polygons=reply.forbidden_polygons,
                altitude_m=goal.z,
                allow_blocked_start=True,
            )
        except RecoveryPlanningError as error:
            if self.config.execution_profile == "engineering":
                self._record_engineering_mission_failure(
                    f"A* recovery planning failed: {error}"
                )
                return None
            raise UrbanParticipantError(
                "deterministic recovery planning failed"
            ) from error

    def _dispatch_flight(
        self,
        gateway: GatewayClient,
        *,
        tool_id: str,
        arguments: Mapping[str, object],
        at: SimulationTime,
        decision_detail: str | None = None,
    ) -> str:
        if self._active_flight_command_id is not None:
            raise UrbanParticipantError("UAV attempted overlapping physical flight commands")
        command_id = self._id(tool_id, at)
        self._record_command_decision(
            gateway, command_id=command_id, at=at,
            summary=(
                decision_detail
                if decision_detail is not None
                else f"submit {tool_id} using authorized public state: {canonical_json_bytes(dict(arguments)).decode('utf-8')}"
            ),
        )
        receipt = gateway.command(
            command_id=command_id,
            tool_id=tool_id,
            at=at,
            arguments=arguments,
        )
        if not self._validate_command_receipt(
            receipt, command_id=command_id, provider_id="flight", at=at
        ):
            self._record_engineering_command_failure(
                command_id=command_id, stops_flight=True
            )
            return command_id
        self._active_flight_command_id = command_id
        self._active_flight_tool_id = tool_id
        return command_id

    def _advance_mission_after_completion(self, tool_id: str) -> None:
        if tool_id == "flight.arm":
            self._mission_phase = "takeoff"
        elif tool_id == "flight.takeoff":
            self._mission_phase = (
                "incident_wait" if self.config.vehicle_id == "uav.01" and self.config.mission_mode == "recovery" else "patrol"
            )
        elif tool_id == "flight.goto":
            if self._mission_phase == "incident_goto":
                self._mission_phase = "await_recovery"
            elif self._mission_phase == "patrol_goto":
                self._mission_phase = "inspection" if self.config.mission_mode == "inspection" else "return"
            elif self._mission_phase == "recovery_goto":
                self._mission_phase = (
                    "recovery" if self._pending_waypoints else "return"
                )
            elif self._mission_phase == "return_goto":
                self._mission_phase = "land"
        elif tool_id == "flight.land":
            self._mission_phase = "disarm"
        elif tool_id == "flight.disarm":
            self._mission_phase = "done"

    def _refresh_active_flight_command(
        self, gateway: GatewayClient, at: SimulationTime
    ) -> None:
        if self._active_flight_command_id is None:
            return
        status = gateway.command_status(
            command_id=self._active_flight_command_id,
            at=at,
        )
        receipt_ok = self._validate_command_receipt(
            status, command_id=self._active_flight_command_id, provider_id="flight", at=at
        )
        if not receipt_ok:
            failed_command_id = self._active_flight_command_id
            self._active_flight_command_id = None
            self._active_flight_tool_id = None
            self._record_engineering_command_failure(
                command_id=failed_command_id, stops_flight=True
            )
            return
        if status.phase == "completed":
            tool_id = self._active_flight_tool_id
            if tool_id is None:
                raise UrbanParticipantError("active flight command lost its tool identity")
            self._active_flight_command_id = None
            self._active_flight_tool_id = None
            self._advance_mission_after_completion(tool_id)

    def _dispatch_next_flight_action(
        self, gateway: GatewayClient, at: SimulationTime
    ) -> str | None:
        if (
            self.config.role != "uav"
            or self._active_flight_command_id is not None
            or self._flight_progression_stopped
        ):
            return None
        vehicle_id = self.config.vehicle_id
        if vehicle_id is None:
            raise UrbanParticipantError("UAV mission has no vehicle identity")
        if self._pending_hold:
            self._pending_hold = False
            return self._dispatch_flight(
                gateway,
                tool_id="flight.hold",
                arguments={"vehicle_id": vehicle_id},
                at=at,
            )
        if self._pending_waypoints:
            waypoint = self._pending_waypoints.pop(0)
            self._mission_phase = "recovery_goto"
            arguments = self._goto_arguments(waypoint)
            return self._dispatch_flight(
                gateway,
                tool_id="flight.goto",
                arguments=arguments,
                at=at,
                decision_detail=(
                    "direct-recovery ablation: submit flight.goto directly to the "
                    "delivered recovery goal without executing A*: "
                    f"{canonical_json_bytes(arguments).decode('utf-8')}"
                    if self.config.recovery_variant == "direct-recovery"
                    else None
                ),
            )
        if self._mission_phase == "arm":
            return self._dispatch_flight(
                gateway,
                tool_id="flight.arm",
                arguments={"vehicle_id": vehicle_id},
                at=at,
            )
        if self._mission_phase == "takeoff":
            return self._dispatch_flight(
                gateway,
                tool_id="flight.takeoff",
                arguments={"vehicle_id": vehicle_id, "altitude_m": self.config.cruise_agl_m},
                at=at,
            )
        if (
            self._mission_phase == "incident_wait"
            and at.sim_time_ns >= self.config.injection_start_ns
        ):
            self._mission_phase = "incident_goto"
            incident = self.config.incident_enu_m
            if incident is None:
                incident = DemoVector(
                    x=0.0, y=150.0, z=self.config.cruise_agl_m
                )
            return self._dispatch_flight(
                gateway,
                tool_id="flight.goto",
                arguments=self._goto_arguments(incident),
                at=at,
            )
        if self._mission_phase == "patrol":
            self._mission_phase = "patrol_goto"
            patrol = self.config.patrol_enu_m
            if patrol is None:
                patrol = DemoVector(
                    x=250.0, y=120.0, z=self.config.cruise_agl_m
                )
            return self._dispatch_flight(
                gateway,
                tool_id="flight.goto",
                arguments=self._goto_arguments(patrol),
                at=at,
            )
        if self._mission_phase == "inspection":
            if self._inspection_started_ns is None:
                self._inspection_started_ns = at.sim_time_ns
            if at.sim_time_ns - self._inspection_started_ns < 5_000_000_000:
                return None
            self._mission_phase = "return"
        if self._mission_phase == "return":
            self._mission_phase = "return_goto"
            launch = self.config.launch_enu_m
            if launch is None:
                launch = DemoVector(
                    x=-320.0 if vehicle_id == "uav.01" else 320.0,
                    y=-350.0,
                    z=self.config.cruise_agl_m,
                )
            return self._dispatch_flight(
                gateway,
                tool_id="flight.goto",
                arguments=self._goto_arguments(launch),
                at=at,
            )
        if self._mission_phase == "land":
            return self._dispatch_flight(
                gateway,
                tool_id="flight.land",
                arguments={"vehicle_id": vehicle_id},
                at=at,
            )
        if self._mission_phase == "disarm":
            return self._dispatch_flight(
                gateway,
                tool_id="flight.disarm",
                arguments={"vehicle_id": vehicle_id},
                at=at,
            )
        return None

    def _start_alert(
        self, gateway: GatewayClient, *, safety: Mapping[str, object], at: SimulationTime
    ) -> str:
        sequence = safety.get("transition_sequence")
        if type(sequence) is not int or sequence < 0 or sequence in self._entered_sequences:
            raise UrbanParticipantError("UAV received an invalid or duplicate airspace entry")
        if self._pending_alert is not None:
            raise UrbanParticipantError("UAV received overlapping unresolved airspace incidents")
        alert = RecoveryMessage(
            schema_version=RECOVERY_MESSAGE_SCHEMA, kind="alert",
            message_id=self._id("alert", at), sender_agent_id=self.context.agent_id,
            vehicle_id=self.config.vehicle_id, incident_region_id=safety.get("region_id"),
            incident_sequence=sequence,
        )
        command_id = self._send(gateway, message=alert, at=at)
        self._entered_sequences.add(sequence)
        self._pending_hold = True
        self._pending_alert = alert
        self._alert_attempts[alert.message_id] = alert
        self._last_alert_ns = at.sim_time_ns
        self._alert_retries = 0
        return command_id

    def _retry_alert(self, gateway: GatewayClient, at: SimulationTime) -> str | None:
        alert = self._pending_alert
        if alert is None:
            return None
        if self._last_alert_ns is None:
            raise UrbanParticipantError("pending alert has no recorded submission time")
        if at.sim_time_ns - self._last_alert_ns < self.config.response_timeout_ns:
            return None
        if self._alert_retries >= self.config.maximum_retries:
            if self.config.execution_profile == "engineering":
                self._record_engineering_mission_failure(
                    "recovery reply timed out after the declared retry budget"
                )
                self._pending_alert = None
                self._last_alert_ns = None
                return None
            raise UrbanParticipantError("recovery reply timed out after the declared retry budget")
        # ns-3 owns delivery. An accepted send is not an acknowledgement; retry
        # the same incident with a fresh message ID, never a reused transport ID.
        retry = alert.model_copy(update={"message_id": self._id("alert-retry", at)})
        command_id = self._send(gateway, message=retry, at=at)
        self._alert_attempts[retry.message_id] = retry
        self._last_alert_ns = at.sim_time_ns
        self._alert_retries += 1
        return command_id

    def _accept_recovery_reply(
        self, *, telemetry: Mapping[str, object], reply: RecoveryMessage
    ) -> str | None:
        alert = self._alert_attempts.get(str(reply.cause_message_id))
        if (
            alert is None
            or reply.sender_agent_id != "groundstation.rule"
            or reply.vehicle_id != self.config.vehicle_id
            or reply.incident_region_id != alert.incident_region_id
            or reply.incident_sequence != alert.incident_sequence
        ):
            raise UrbanParticipantError("recovery reply does not bind an authorized submitted incident")
        expected_planner = (
            "aero-bench.recovery-direct/v1"
            if self.config.recovery_variant == "direct-recovery"
            else "aero-bench.recovery-a-star/v1"
        )
        if (
            self.config.recovery_variant == "no-recovery"
            or reply.planner_version != expected_planner
        ):
            raise UrbanParticipantError(
                "recovery reply strategy differs from the declared recovery variant"
            )
        incident = (str(alert.incident_region_id), int(alert.incident_sequence))
        if incident in self._planned_incidents:
            return None
        if self._pending_alert is None or (
            self._pending_alert.incident_region_id, self._pending_alert.incident_sequence
        ) != incident:
            raise UrbanParticipantError("recovery reply is not for the pending incident")
        planning_failed = False
        if self.config.recovery_variant == "direct-recovery":
            goal = reply.goal_enu_m
            if goal is None:
                raise UrbanParticipantError("direct recovery reply has no delivered goal")
            waypoints = (goal,)
            decision = (
                "direct-recovery ablation selected the delivered goal directly; "
                "A* planning was not executed"
            )
        else:
            path = self._plan_from_reply(telemetry=telemetry, reply=reply)
            planning_failed = path is None
            waypoints = () if path is None else path[1:]
            decision = None
        self._planned_causes.add(str(reply.cause_message_id))
        self._planned_incidents.add(incident)
        self._pending_waypoints.extend(waypoints)
        if not planning_failed:
            self._mission_phase = "recovery" if self._pending_waypoints else "return"
        self._pending_alert = None
        self._last_alert_ns = None
        return decision

    def _validate_uav_observations(
        self, telemetry: Mapping[str, object], safety: Mapping[str, object], at: SimulationTime
    ) -> None:
        for payload, schema in ((telemetry, TELEMETRY_SCHEMA), (safety, SAFETY_SCHEMA)):
            if (
                payload.get("schema_version") != schema
                or payload.get("vehicle_id") != self.config.vehicle_id
                or payload.get("simulation_time_ns") != at.sim_time_ns
            ):
                raise UrbanParticipantError("UAV public observation identity/time is invalid")
        engine_time = safety.get("engine_sim_time_ns")
        origin = safety.get("logical_origin_engine_ns")
        transition_time = safety.get("transition_engine_sim_time_ns")
        if (
            safety.get("source") != "gazebo.system"
            or safety.get("airspace_state") not in {"inside", "outside"}
            or safety.get("transition") not in {"entered", "exited", "none"}
            or type(engine_time) is not int or type(origin) is not int
            or origin < 0 or engine_time - origin != at.sim_time_ns
        ):
            raise UrbanParticipantError("UAV safety observation lacks engine authority")
        if safety["transition"] == "none":
            if transition_time is not None or safety.get("transition_sequence") != 0:
                raise UrbanParticipantError("UAV safety non-event contains transition identity")
        elif (
            type(transition_time) is not int
            or not max(origin, engine_time - STEP_NS) < transition_time <= engine_time
            or type(safety.get("transition_sequence")) is not int
            or safety["transition_sequence"] < 1
            or safety["airspace_state"] != ("inside" if safety["transition"] == "entered" else "outside")
        ):
            raise UrbanParticipantError("UAV safety transition is outside its closed engine window")

    def _validate_final_uav_state(
        self, telemetry: Mapping[str, object], safety: Mapping[str, object]
    ) -> None:
        if (
            self._mission_phase != "done"
            or self._active_flight_command_id is not None
            or self._pending_alert is not None
            or self._pending_waypoints
            or self._pending_hold
            or telemetry.get("armed") is not False
            or telemetry.get("in_air") is not False
            or telemetry.get("landed") is not True
            or telemetry.get("ground_contact") is not True
            or telemetry.get("collision_contact") is not False
            or safety.get("airspace_state") != "outside"
        ):
            raise UrbanParticipantError("final UAV observation does not prove landed, disarmed completion")

    def _engineering_final_uav_summary(
        self, telemetry: Mapping[str, object], safety: Mapping[str, object]
    ) -> str:
        actual_state = {
            "active_flight_command_id": self._active_flight_command_id,
            "airspace_state": safety.get("airspace_state"),
            "armed": telemetry.get("armed"),
            "collision_contact": telemetry.get("collision_contact"),
            "failed_command_count": len(self._failed_command_ids),
            "flight_progression_stopped": self._flight_progression_stopped,
            "ground_contact": telemetry.get("ground_contact"),
            "in_air": telemetry.get("in_air"),
            "landed": telemetry.get("landed"),
            "mission_failure_count": len(self._mission_failures),
            "mission_phase": self._mission_phase,
            "mission_mode": self.config.mission_mode,
            "inspection_started_ns": self._inspection_started_ns,
            "recent_failed_command_ids": tuple(self._failed_command_ids[-8:]),
            "recent_mission_failures": tuple(self._mission_failures[-8:]),
            "recovery_variant": self.config.recovery_variant,
        }
        if self.config.recovery_variant == "no-recovery" and (
            safety.get("airspace_state") == "inside"
            or self._mission_phase == "await_recovery"
        ):
            outcome = (
                "; this remaining inside/waiting state is the expected "
                "no-recovery control outcome, not a successful recovery"
            )
        elif self.config.recovery_variant == "no-recovery":
            outcome = (
                "; no-recovery is an ablation control outcome and is not labeled "
                "as a successful recovery"
            )
        else:
            outcome = ""
        return (
            "engineering horizon reached; final authoritative observations "
            "recorded without claiming landing or command completion; actual_state="
            f"{canonical_json_bytes(actual_state).decode('utf-8')}{outcome}"
        )

    def _complete_turn(
        self,
        gateway: GatewayClient,
        at: SimulationTime,
        command_ids: list[str],
        observed_ids: list[str],
        *,
        summary_override: str | None = None,
    ) -> tuple[str, ...]:
        final = at.tick == self.config.final_tick
        summary = summary_override
        if summary is None:
            summary = (
                "final tick public observations recorded; no further commands submitted"
                if final else f"mission_mode={self.config.mission_mode}; mission_phase={self._mission_phase}; UAV evaluated recorded telemetry, safety and mailbox"
                if self.config.role == "uav" else "groundstation rule evaluated its ns-3 mailbox"
            )
        gateway.decision_summary(
            summary_id=self._id("decision", at), at=at,
            summary=summary,
            command_id=None,
            observation_ids=tuple(sorted(observed_ids)),
        )
        gateway.complete_turn(
            completion_id=self._id("turn", at), at=at,
            disposition="finished" if final else "advance",
            command_ids=tuple(command_ids), observation_ids=tuple(sorted(observed_ids)),
        )
        self._last_turn_tick = at.tick
        return tuple(command_ids)

    def step(self, gateway: GatewayClient, at: SimulationTime) -> tuple[str, ...]:
        """Execute one turn and return command IDs submitted during that turn."""

        if (
            at.tick != self._last_turn_tick + 1
            or at.tick > self.config.final_tick
            or at.sim_time_ns != at.tick * STEP_NS
        ):
            raise UrbanParticipantError("participant turn is outside the exact sequential demo clock")
        command_ids: list[str] = []
        observed_ids: list[str] = [self.config.mailbox_observation_id]
        mailbox = self._observe(gateway, self.config.mailbox_observation_id, at)
        messages = self._mailbox_messages(mailbox, at=at)
        summary_override: str | None = None
        failure_count = len(self._mission_failures)

        if self.config.role == "uav":
            assert self.config.telemetry_observation_id is not None
            assert self.config.safety_observation_id is not None
            telemetry = self._observe(gateway, self.config.telemetry_observation_id, at)
            safety = self._observe(gateway, self.config.safety_observation_id, at)
            observed_ids.extend(
                (self.config.telemetry_observation_id, self.config.safety_observation_id)
            )
            self._validate_uav_observations(telemetry, safety, at)
            self._refresh_active_flight_command(gateway, at)
            final_engineering_observation = (
                self.config.execution_profile == "engineering"
                and at.tick == self.config.final_tick
            )
            if not final_engineering_observation:
                for message in messages:
                    if message.kind == "recovery_reply":
                        decision = self._accept_recovery_reply(
                            telemetry=telemetry, reply=message
                        )
                        if decision is not None:
                            summary_override = decision
            if at.tick == self.config.final_tick:
                if self.config.execution_profile == "formal":
                    self._validate_final_uav_state(telemetry, safety)
                else:
                    summary_override = self._engineering_final_uav_summary(
                        telemetry, safety
                    )
                return self._complete_turn(
                    gateway, at, command_ids, observed_ids,
                    summary_override=summary_override,
                )
            if safety.get("transition") == "entered":
                if self.config.recovery_variant == "no-recovery":
                    sequence = safety.get("transition_sequence")
                    if sequence in self._entered_sequences:
                        raise UrbanParticipantError(
                            "UAV received an invalid or duplicate airspace entry"
                        )
                    self._entered_sequences.add(int(sequence))
                    summary_override = (
                        "no-recovery ablation: observed the authoritative airspace "
                        "incident and suppressed the recovery alert, flight hold, "
                        "and replanning; waiting inside the region is a control "
                        "outcome, not a successful recovery"
                    )
                else:
                    command_ids.append(
                        self._start_alert(gateway, safety=safety, at=at)
                    )
            retry_command = self._retry_alert(gateway, at)
            if retry_command is not None:
                command_ids.append(retry_command)
            flight_command_id = self._dispatch_next_flight_action(gateway, at)
            if flight_command_id is not None:
                command_ids.append(flight_command_id)
        else:
            if at.tick == self.config.final_tick:
                if (
                    self.config.execution_profile == "formal"
                    and any(message.kind == "alert" for message in messages)
                ):
                    raise UrbanParticipantError(
                        "final tick contains an unresolved recovery alert"
                    )
                if self.config.execution_profile == "engineering":
                    summary_override = (
                        "engineering horizon reached; final authoritative mailbox "
                        "observation recorded without claiming recovery completion; "
                        f"recovery_variant={self.config.recovery_variant}; "
                        f"delivered_message_count={len(messages)}; "
                        f"delivered_message_kinds={canonical_json_bytes(tuple(sorted({message.kind for message in messages}))).decode('utf-8')}"
                    )
                return self._complete_turn(
                    gateway, at, command_ids, observed_ids,
                    summary_override=summary_override,
                )
            for message in messages:
                if message.kind != "alert":
                    continue
                if self.config.recovery_variant == "no-recovery":
                    summary_override = (
                        "no-recovery ablation: observed the delivered incident alert "
                        "and suppressed the recovery reply and replanning"
                    )
                    continue
                direct = self.config.recovery_variant == "direct-recovery"
                reply = RecoveryMessage(
                    schema_version=RECOVERY_MESSAGE_SCHEMA,
                    kind="recovery_reply",
                    message_id=self._id("recovery-reply", at),
                    sender_agent_id=self.context.agent_id,
                    vehicle_id=message.vehicle_id,
                    incident_region_id=message.incident_region_id,
                    incident_sequence=message.incident_sequence,
                    planner_version=(
                        "aero-bench.recovery-direct/v1"
                        if direct else "aero-bench.recovery-a-star/v1"
                    ),
                    goal_enu_m=self.config.recovery_goal_enu_m,
                    forbidden_polygons=self.config.forbidden_polygons,
                    cause_message_id=message.message_id,
                )
                command_ids.append(
                    self._send(
                        gateway,
                        message=reply,
                        at=at,
                        decision_detail=(
                            "direct-recovery ablation: submit the delivered recovery "
                            "goal for direct execution without A* planning; delivery "
                            "still requires real ns-3 mailbox evidence"
                            if direct else None
                        ),
                    )
                )
                self._replied_causes.add(message.message_id)

        if self._last_heartbeat_ns is None or at.sim_time_ns - self._last_heartbeat_ns >= self.config.heartbeat_interval_ns:
            if self.config.role == "groundstation":
                for endpoint_id in sorted(self.config.vehicle_endpoint_ids.values()):
                    heartbeat = RecoveryMessage(
                        schema_version=RECOVERY_MESSAGE_SCHEMA,
                        kind="heartbeat",
                        message_id=self._id("heartbeat", at),
                        sender_agent_id=self.context.agent_id,
                    )
                    command_ids.append(
                        self._send(
                            gateway,
                            message=heartbeat,
                            at=at,
                            destination=endpoint_id,
                        )
                    )
            else:
                heartbeat = RecoveryMessage(
                    schema_version=RECOVERY_MESSAGE_SCHEMA,
                    kind="heartbeat",
                    message_id=self._id("heartbeat", at),
                    sender_agent_id=self.context.agent_id,
                )
                command_ids.append(self._send(gateway, message=heartbeat, at=at))
            self._last_heartbeat_ns = at.sim_time_ns
        new_failures = self._mission_failures[failure_count:]
        if new_failures:
            failure_summary = (
                "engineering mission failure recorded; "
                f"reasons={canonical_json_bytes(tuple(new_failures)).decode('utf-8')}; "
                + (
                    "flight progression stopped; observations and heartbeats continue"
                    if self.config.role == "uav"
                    else "observations and heartbeats continue"
                )
            )
            summary_override = (
                failure_summary
                if summary_override is None
                else f"{summary_override}; {failure_summary}"
            )
        return self._complete_turn(
            gateway, at, command_ids, observed_ids,
            summary_override=summary_override,
        )

    def _mailbox_messages(
        self, payload: Mapping[str, object], *, at: SimulationTime
    ) -> tuple[RecoveryMessage, ...]:
        try:
            batch = NetworkMailboxObservationPayload.model_validate(payload)
        except (TypeError, ValueError) as error:
            raise UrbanParticipantError("mailbox batch is not closed by a valid network receipt") from error
        if (
            batch.run_id != self.context.run_id
            or batch.provider_id != "network"
            or batch.agent_id != self.context.agent_id
            or batch.observation_id != self.config.mailbox_observation_id
            or batch.mailbox_endpoint_id != self.config.endpoint_id
            or batch.time_tick != at.tick
            or batch.sim_time_ns != at.sim_time_ns
        ):
            raise UrbanParticipantError("mailbox batch differs from its authorized identity/time")
        messages: list[RecoveryMessage] = []
        for value in json.loads(batch.messages_json):
            delivery = NetworkMailboxMessage.model_validate(value)
            message = _message_from_base64(delivery.payload_base64)
            if (
                delivery.message_id != message.message_id
                or delivery.sender_agent_id != message.sender_agent_id
                or delivery.work_order_id != f"urban.recovery.{self.context.run_id[:16]}"
                or delivery.destination != self.config.endpoint_id
                or message.message_id in self._received_message_ids
            ):
                raise UrbanParticipantError("mailbox message identity, work order, or replay check failed")
            if self.config.role == "uav":
                if (
                    delivery.sender_agent_id != "groundstation.rule"
                    or delivery.source != self.config.groundstation_endpoint_id
                    or message.kind not in {"heartbeat", "recovery_reply"}
                    or (message.kind == "recovery_reply" and message.vehicle_id != self.config.vehicle_id)
                ):
                    raise UrbanParticipantError("UAV mailbox sender/source or message role is unauthorized")
            else:
                vehicles = [
                    vehicle for vehicle, endpoint in self.config.vehicle_endpoint_ids.items()
                    if endpoint == delivery.source
                ]
                if (
                    len(vehicles) != 1
                    or self.config.vehicle_agent_ids[vehicles[0]] != delivery.sender_agent_id
                    or message.kind not in {"heartbeat", "alert"}
                    or (message.kind == "alert" and message.vehicle_id != vehicles[0])
                ):
                    raise UrbanParticipantError("groundstation mailbox sender/source or message role is unauthorized")
            messages.append(message)
        self._received_message_ids.update(message.message_id for message in messages)
        return tuple(messages)

    def _wait_for_barrier(
        self, gateway: GatewayClient, at: SimulationTime, *, timeout_seconds: float
    ) -> None:
        # An early turn.complete returns waiting, not permission to observe the
        # next tick. Only the Gateway's public clock confirms the barrier commit.
        deadline = time.monotonic() + timeout_seconds
        while True:
            probe = gateway.probe()
            if (
                probe.get("schema_version") != "aero-bench.gateway-probe/v1"
                or probe.get("run_id") != self.context.run_id
                or probe.get("status") != "ready"
            ):
                raise UrbanParticipantError("Gateway clock identity is invalid")
            current = SimulationTime.model_validate(probe.get("current"))
            if current == at:
                return
            if (
                current.tick != at.tick - 1
                or current.sim_time_ns != at.sim_time_ns - STEP_NS
            ):
                raise UrbanParticipantError("Gateway clock skipped or regressed a participant barrier")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UrbanParticipantError(f"Gateway did not commit participant barrier {at.tick}")
            time.sleep(min(0.02, remaining))

    def run(self, *, timeout_seconds: float = 120.0) -> None:
        """Observe reset through the configured closed horizon, with no extra tick."""

        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise UrbanParticipantError("Gateway timeout must be finite and positive")
        gateway = self.context.gateway()
        try:
            gateway.connect(timeout_seconds=timeout_seconds)
            for tick in range(self.config.final_tick + 1):
                at = SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
                self._wait_for_barrier(gateway, at, timeout_seconds=timeout_seconds)
                self.step(gateway, at)
        finally:
            gateway.close()


__all__ = [
    "RECOVERY_MESSAGE_SCHEMA",
    "RecoveryMessage",
    "UrbanParticipant",
    "UrbanParticipantConfig",
    "UrbanParticipantError",
]
