from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, TypeAdapter, ValidationError

from aero_bench.config.models import ClockSpec, Sha256, StrictModel
from aero_bench.providers.contracts import (
    ProviderCommandResult,
    ProviderManifest,
    ProviderSession,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.sumo.config import SumoConfig, TRAFFIC_RESTRICTION_CAPABILITY
from aero_bench.providers.sumo.restriction_evidence import (
    SumoRestrictionApplication, validate_restriction_application,
)
from aero_bench.providers.sumo.protocol import (
    PROTOCOL_VERSION,
    JsonLineTransport,
    SumoTransport,
)
from aero_bench.runtime.contracts import (
    CommandRequest,
    FinalizedArtifact,
    MotionStageRequest,
    MotionStageResult,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    ProviderStageRequest,
    SceneContribution,
    SimulationTime,
    StateSample,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world import frame_math
from aero_bench.world.resolved import ResolvedScenario


class SumoProviderError(RuntimeError):
    pass


class SumoProviderNotReady(SumoProviderError):
    pass


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


class SumoReadiness(StrictModel):
    status: Literal["ready"]
    provider_id: str
    protocol_version: str
    runtime_image: str
    scenario_digest: Sha256
    sumo_version: str
    sumo_commit: str


class SumoSnapshotResponse(StrictModel):
    snapshot_digest: Sha256


class SumoTrafficLightState(StrictModel):
    signal_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    phase_index: int = Field(ge=0)
    next_switch_s: float = Field(allow_inf_nan=False)
    program_id: str
    telemetry_source: Literal["sumo-traci"]


class SumoMotionStageResponse(StrictModel):
    schema_version: Literal["aero-bench.sumo-motion-stage-response/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: str
    target: SimulationTime
    stage: Literal["motion"]
    receipt: StepReceipt
    samples: tuple[StateSample, ...]
    traffic_lights: tuple[SumoTrafficLightState, ...]


class SumoProvider(ProviderSession):
    """Client for the real SUMO TraCI provider workload.

    SUMO and TraCI live in the provider workload. This process deliberately
    contains no simulator connection or local state generator; every receipt
    and snapshot digest comes from the JSON-line provider service.

    Every frame, including the first ``prepare``, presents the executor-issued
    run-scoped session token. The workload pins the same token from
    ``AERO_BENCH_PROVIDER_TOKEN`` and rejects any peer that cannot present it.
    """

    _STATE_SCHEMA = "sumo.state.v3"
    _STATE_ATTRIBUTE_NAMES = (
        "kind",
        "lane_id",
        "lifecycle",
        "road_id",
        "speed_mps",
        "sumo_object_id",
        "telemetry_source",
        "yaw_enu_rad",
    )

    def __init__(
        self,
        *,
        config: SumoConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        session_token: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ):
        if manifest.provider_id != config.provider_id:
            raise ValueError("SUMO manifest/provider configuration IDs differ")
        if (
            len(manifest.artifact_requirements) != 1
            or manifest.artifact_requirements[0].artifact_type
            != "sumo.traffic.evidence"
        ):
            raise ValueError(
                "SUMO manifest must declare exactly one traffic evidence artifact"
            )
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise TypeError("SUMO runtime_endpoint must be a RuntimeEndpoint")
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("SUMO scenario must be a ResolvedScenario")
        if not isinstance(clock, ClockSpec):
            raise TypeError("SUMO clock must be a ClockSpec")
        scenario_provider = next(
            (
                provider
                for provider in scenario.providers
                if provider.provider_id == config.provider_id
            ),
            None,
        )
        if scenario_provider is None:
            raise ValueError("SUMO provider is absent from ResolvedScenario")
        if scenario.sumo is None or scenario.sumo.provider_id != config.provider_id:
            raise ValueError("ResolvedScenario does not assign SUMO to this provider")
        binding_entity_ids = tuple(
            sorted(binding.entity_id for binding in scenario.sumo.object_bindings)
        )
        owned_entity_ids = tuple(
            sorted(
                entity.entity_id
                for entity in scenario.entities
                if entity.owner_kind == "provider"
                and entity.owner_id == config.provider_id
                and entity.authority_kind == "sumo_traffic"
                and entity.state == "dynamic"
            )
        )
        if not binding_entity_ids or binding_entity_ids != owned_entity_ids:
            raise ValueError(
                "SUMO object bindings do not close over provider-owned dynamic entities"
            )
        if config.step_length_ns != clock.step_ns:
            raise ValueError("SUMO step_length_ns must equal the run clock step_ns")
        if config.restrictions and TRAFFIC_RESTRICTION_CAPABILITY not in manifest.capabilities:
            raise ValueError("SUMO restrictions require the declared Provider capability")
        if any(item.at_tick > clock.max_steps for item in config.restrictions):
            raise ValueError("SUMO restriction tick exceeds the declared clock horizon")
        if runtime_endpoint.port == config.traci_port:
            raise ValueError("provider RPC and private TraCI ports must differ")
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError("SUMO run_id must be a SHA-256 digest") from error
        if validated_run_id == "0" * 64:
            raise ValueError("SUMO run_id cannot be a placeholder digest")
        try:
            validated_token = TypeAdapter(Sha256).validate_python(session_token)
        except (TypeError, ValueError) as error:
            raise ValueError("SUMO session_token must be a SHA-256 digest") from error
        if validated_token == "0" * 64:
            raise ValueError("SUMO session_token cannot be a placeholder digest")
        self._config = config
        self._scenario = scenario
        self._clock = clock
        self._owned_entity_ids = binding_entity_ids
        self._binding_kind_by_entity = {
            binding.entity_id: binding.kind
            for binding in scenario.sumo.object_bindings
        }
        self._binding_object_id_by_entity = {
            binding.entity_id: binding.sumo_object_id
            for binding in scenario.sumo.object_bindings
        }
        self._manifest = manifest
        self._runtime_endpoint = runtime_endpoint
        self._run_id = validated_run_id
        self._session_token = validated_token
        self._transport: SumoTransport | None = None
        self._prepared = False
        self._last_time: SimulationTime | None = None
        self._applied_restriction_ids: set[str] = set()

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    @staticmethod
    async def connect_runtime(endpoint: RuntimeEndpoint) -> SumoTransport:
        """Connect to the materializer-owned endpoint for a production session."""

        return await JsonLineTransport.connect(endpoint)

    async def _request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._transport is None:
            self._transport = await self.connect_runtime(self._runtime_endpoint)
        return await self._transport.request(
            operation, {**dict(payload), "session_token": self._session_token}
        )

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise SumoProviderNotReady("SUMO provider has not completed readiness")

    def _config_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self._config.provider_id,
            "run_id": self._run_id,
            "protocol_version": PROTOCOL_VERSION,
            "runtime_image": self._manifest.runtime_image,
            "config_digest": self._manifest.config_digest,
            "artifact_requirements": [
                item.model_dump(mode="json")
                for item in self._manifest.artifact_requirements
            ],
            "sumo": self._config.sumo.model_dump(mode="json"),
            "sumo_binary": self._config.sumo_binary,
            "traci_port": self._config.traci_port,
            "step_length_ns": self._config.step_length_ns,
            "sumo_args": list(self._config.sumo_args),
            "required_commands": list(self._config.required_commands),
            "command_timeout_ms": self._config.command_timeout_ms,
            "restrictions": [item.model_dump(mode="json") for item in self._config.restrictions],
        }

    async def prepare(self) -> None:
        if self._prepared:
            raise SumoProviderError("SUMO provider prepare called twice")
        response = await self._request("prepare", self._config_payload())
        try:
            readiness = SumoReadiness.model_validate(response)
        except ValidationError as exc:
            raise SumoProviderError(
                "SUMO provider readiness response is invalid"
            ) from exc
        if readiness.status != "ready":
            raise SumoProviderError(
                f"SUMO provider did not become ready: {readiness.status!r}"
            )
        if readiness.provider_id != self._config.provider_id:
            raise SumoProviderError(
                "SUMO readiness provider_id differs from declaration"
            )
        if readiness.protocol_version != PROTOCOL_VERSION:
            raise SumoProviderError("SUMO provider protocol version mismatch")
        if readiness.runtime_image != self._manifest.runtime_image:
            raise SumoProviderError("SUMO provider image identity mismatch")
        if readiness.scenario_digest != self._scenario.scenario_digest:
            raise SumoProviderError("SUMO provider scenario identity mismatch")
        if (
            readiness.sumo_version != self._config.sumo.version
            or readiness.sumo_commit != self._config.sumo.commit
        ):
            raise SumoProviderError(
                "SUMO runtime identity does not match configuration"
            )
        self._prepared = True

    def _parse_receipt(
        self,
        response: Mapping[str, Any],
        *,
        target: SimulationTime,
        operation: str,
    ) -> StepReceipt:
        try:
            receipt = StepReceipt.model_validate(response.get("receipt"))
        except ValidationError as exc:
            raise SumoProviderError(
                f"SUMO {operation} response does not contain a valid StepReceipt"
            ) from exc
        if receipt.run_id != self._run_id:
            raise SumoProviderError("SUMO StepReceipt run identity mismatch")
        if receipt.provider_id != self._config.provider_id:
            raise SumoProviderError("SUMO StepReceipt provider identity mismatch")
        if receipt.reached != target:
            raise SumoProviderError(
                "SUMO backend did not reach the requested sim_time_ns"
            )
        state_events = tuple(
            event
            for event in receipt.events
            if event.payload_schema_id == self._STATE_SCHEMA
        )
        traffic_events = tuple(
            event
            for event in receipt.events
            if event.event_id == "public.traffic-light"
        )
        restriction_events = tuple(event for event in receipt.events
                                   if event.payload_schema_id == "sumo.traffic.restricted.v2")
        if len(state_events) != 1 or len(traffic_events) > 1 or len(receipt.events) != 1 + len(traffic_events) + len(restriction_events):
            raise SumoProviderError(
                "SUMO response must contain one state event and at most one traffic-light event"
            )
        state_event = state_events[0]
        if (
            state_event.provider_id != self._config.provider_id
            or state_event.event_id != f"state.{target.tick}"
            or state_event.time != target
        ):
            raise SumoProviderError("SUMO state event binding is inconsistent")
        payload = {item.name: item.value for item in state_event.payload}
        if set(payload) != {
            "snapshot_digest",
            "evidence_path",
            "evidence_sha256",
            "simulation_time_ns",
        }:
            raise SumoProviderError("SUMO state event payload inventory is invalid")
        if (
            payload["snapshot_digest"] != receipt.state_digest
            or payload["evidence_path"]
            != self._manifest.artifact_requirements[0].relative_path
            or not isinstance(payload["evidence_sha256"], str)
            or payload["evidence_sha256"] == "0" * 64
            or not isinstance(payload["simulation_time_ns"], int)
            or isinstance(payload["simulation_time_ns"], bool)
            or payload["simulation_time_ns"] != target.sim_time_ns
        ):
            raise SumoProviderError("SUMO state event payload is inconsistent")
        try:
            TypeAdapter(Sha256).validate_python(payload["evidence_sha256"])
        except (TypeError, ValueError) as exc:
            raise SumoProviderError("SUMO evidence digest is invalid") from exc
        self._traffic_light_event_states(receipt)
        expected = {item.event_id: item for item in self._config.restrictions if item.at_tick == target.tick}
        observed = set()
        for event in restriction_events:
            values = {item.name: item.value for item in event.payload}
            if (set(values) != {"application_json", "evidence_sha256"}
                    or event.time != target or event.provider_id != self._config.provider_id
                    or values["evidence_sha256"] != payload["evidence_sha256"]):
                raise SumoProviderError("SUMO restriction event does not bind its native evidence")
            try:
                raw = values["application_json"]
                application = SumoRestrictionApplication.model_validate_json(raw)
                if canonical_json_bytes(application.model_dump(mode="json")).decode() != raw:
                    raise ValueError("SUMO restriction application JSON is not canonical")
                event_id = application.request.event_id
                if event_id not in expected or event_id in observed or event_id in self._applied_restriction_ids:
                    raise ValueError("SUMO restriction event is unscheduled or duplicated")
                if event.event_id != f"restriction.{event_id}":
                    raise ValueError("SUMO restriction event identity differs from its request")
                validate_restriction_application(application, expected=expected[event_id],
                                                 step_ns=self._clock.step_ns, require_route_effect=False)
            except (TypeError, ValueError) as exc:
                raise SumoProviderError("SUMO restriction application is invalid") from exc
            observed.add(event_id)
        if observed != set(expected):
            raise SumoProviderError("SUMO receipt omits a scheduled restriction application")
        self._applied_restriction_ids.update(observed)
        return receipt

    @staticmethod
    def _traffic_light_event_states(
        receipt: StepReceipt,
    ) -> tuple[SumoTrafficLightState, ...] | None:
        events = tuple(
            event for event in receipt.events if event.event_id == "public.traffic-light"
        )
        if not events:
            return None
        event = events[0]
        if (
            event.payload_schema_id != "sumo.traffic_light.v1"
            or event.time != receipt.reached
            or event.provider_id != receipt.provider_id
        ):
            raise SumoProviderError("SUMO traffic-light event binding is invalid")
        payload = {item.name: item.value for item in event.payload}
        if set(payload) != {"snapshot_digest", "simulation_time_ns", "traffic_lights_json"}:
            raise SumoProviderError("SUMO traffic-light event payload is invalid")
        raw_json = payload["traffic_lights_json"]
        if not isinstance(raw_json, str):
            raise SumoProviderError("SUMO traffic-light state is not JSON text")
        try:
            document = json.loads(
                raw_json,
                parse_constant=_reject_nonfinite_json,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SumoProviderError("SUMO traffic-light JSON is invalid") from exc
        if canonical_json_bytes(document).decode("utf-8") != raw_json:
            raise SumoProviderError("SUMO traffic-light JSON is not canonical")
        if (
            payload["snapshot_digest"] != receipt.state_digest
            or payload["simulation_time_ns"] != receipt.reached.sim_time_ns
            or not isinstance(document, list)
        ):
            raise SumoProviderError("SUMO traffic-light event is inconsistent")
        try:
            states = tuple(SumoTrafficLightState.model_validate(item) for item in document)
        except ValidationError as exc:
            raise SumoProviderError("SUMO traffic-light state is invalid") from exc
        ids = tuple(state.signal_id for state in states)
        if ids != tuple(sorted(set(ids))):
            raise SumoProviderError("SUMO traffic-light states must be sorted and unique")
        return states

    async def reset(self, *, seed: int) -> StepReceipt:
        self._require_prepared()
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("SUMO reset seed must be an integer")
        response = await self._request(
            "reset",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "seed": seed,
            },
        )
        target = SimulationTime(tick=0, sim_time_ns=0)
        self._applied_restriction_ids.clear()
        receipt = self._parse_receipt(response, target=target, operation="reset")
        self._last_time = target
        return receipt

    @staticmethod
    def _motion_contribution(
        request: MotionStageRequest,
        samples: tuple[StateSample, ...],
    ) -> SceneContribution:
        fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": request.run_id,
            "scenario_digest": request.scenario_digest,
            "at": request.target,
            "stage": "motion",
            "provider_id": request.provider_id,
            "samples": samples,
            "attribute_updates": (),
        }
        payload_candidate = SceneContribution.model_construct(
            **fields,
            payload_digest="0" * 64,
            contribution_digest="0" * 64,
        )
        payload_digest = scene_contribution_payload_digest_value(payload_candidate)
        contribution_candidate = SceneContribution.model_construct(
            **fields,
            payload_digest=payload_digest,
            contribution_digest="0" * 64,
        )
        return SceneContribution(
            **fields,
            payload_digest=payload_digest,
            contribution_digest=scene_contribution_digest_value(
                contribution_candidate
            ),
        )

    def _validate_samples_against_receipt(
        self,
        samples: tuple[StateSample, ...],
        receipt: StepReceipt,
        traffic_lights: tuple[SumoTrafficLightState, ...],
    ) -> None:
        signal_ids = tuple(signal.signal_id for signal in traffic_lights)
        if signal_ids != tuple(sorted(set(signal_ids))):
            raise SumoProviderError("SUMO traffic-light states must be sorted and unique")
        entities: list[dict[str, object]] = []
        expected_types = {
            "kind": "str",
            "lane_id": "str",
            "lifecycle": "str",
            "road_id": "str",
            "speed_mps": "float",
            "sumo_object_id": "str",
            "telemetry_source": "str",
            "yaw_enu_rad": "float",
        }
        for sample in samples:
            names = tuple(attribute.name for attribute in sample.attributes)
            if names != self._STATE_ATTRIBUTE_NAMES:
                raise SumoProviderError("SUMO sample attribute inventory is invalid")
            attributes = {attribute.name: attribute for attribute in sample.attributes}
            if any(
                attributes[name].value_type != value_type
                for name, value_type in expected_types.items()
            ):
                raise SumoProviderError("SUMO sample attribute types are invalid")
            values = {name: attribute.value for name, attribute in attributes.items()}
            lifecycle = values["lifecycle"]
            if (
                values["kind"] != self._binding_kind_by_entity[sample.entity_id]
                or values["sumo_object_id"]
                != self._binding_object_id_by_entity[sample.entity_id]
                or values["telemetry_source"] != "sumo-traci"
                or lifecycle not in {"pending", "active", "arrived", "removed"}
                or sample.mode != lifecycle
                or sample.armed is not None
                or sample.battery is not None
                or sample.health is not None
                or sample.angular_velocity_body is not None
                or sample.contacts
            ):
                raise SumoProviderError("SUMO sample semantics are inconsistent")
            speed_mps = values["speed_mps"]
            yaw_enu_rad = values["yaw_enu_rad"]
            if not isinstance(speed_mps, float) or not isinstance(yaw_enu_rad, float):
                raise SumoProviderError("SUMO sample motion attributes are invalid")
            velocity = sample.linear_velocity_enu
            velocity_norm = math.sqrt(
                velocity.east_mps**2
                + velocity.north_mps**2
                + velocity.up_mps**2
            )
            orientation = sample.pose.orientation_enu
            try:
                _, _, pose_yaw_rad = frame_math.quaternion_to_rpy(
                    frame_math.UnitQuaternion(
                        w=orientation.qw,
                        x=orientation.qx,
                        y=orientation.qy,
                        z=orientation.qz,
                    )
                )
            except frame_math.FrameMathError as exc:
                raise SumoProviderError("SUMO sample orientation is invalid") from exc
            yaw_error = math.atan2(
                math.sin(pose_yaw_rad - yaw_enu_rad),
                math.cos(pose_yaw_rad - yaw_enu_rad),
            )
            if (
                speed_mps < 0.0
                or not math.isclose(
                    velocity_norm, speed_mps, rel_tol=0.0, abs_tol=1e-9
                )
                or not math.isclose(yaw_error, 0.0, rel_tol=0.0, abs_tol=1e-9)
                or (
                    lifecycle != "active"
                    and (speed_mps != 0.0 or velocity_norm != 0.0)
                )
            ):
                raise SumoProviderError("SUMO sample motion is inconsistent")
            pose = sample.pose.model_dump(mode="json")
            position_enu = sample.pose.position.enu
            entities.append(
                {
                    "entity_id": sample.entity_id,
                    "sumo_object_id": values["sumo_object_id"],
                    "kind": values["kind"],
                    "lifecycle": lifecycle,
                    "pose": pose,
                    "position_enu_m": {
                        "east_m": position_enu.east_m,
                        "north_m": position_enu.north_m,
                        "up_m": position_enu.up_m,
                    },
                    "linear_velocity_enu": sample.linear_velocity_enu.model_dump(
                        mode="json"
                    ),
                    "linear_velocity_ned": sample.linear_velocity_ned.model_dump(
                        mode="json"
                    ),
                    "speed_mps": values["speed_mps"],
                    "yaw_enu_rad": values["yaw_enu_rad"],
                    "road_id": values["road_id"],
                    "lane_id": values["lane_id"],
                }
            )
        snapshot = {
            "simulation_time_ns": receipt.reached.sim_time_ns,
            "entities": entities,
            "traffic_lights": [signal.model_dump(mode="json") for signal in traffic_lights],
        }
        snapshot_digest = hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest()
        if snapshot_digest != receipt.state_digest:
            raise SumoProviderError(
                "SUMO motion samples do not reconstruct the receipted snapshot"
            )

    async def step_stage(
        self, request: ProviderStageRequest
    ) -> MotionStageResult:
        self._require_prepared()
        if not isinstance(request, MotionStageRequest):
            raise SumoProviderError("SUMO accepts only MotionStageRequest")
        try:
            canonical_request = MotionStageRequest.model_validate(
                request.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise SumoProviderError("SUMO motion stage request is invalid") from exc
        if canonical_request != request:
            raise SumoProviderError("SUMO motion stage request is not canonical")
        if request.run_id != self._run_id:
            raise SumoProviderError("SUMO motion stage belongs to another run")
        if request.scenario_digest != self._scenario.scenario_digest:
            raise SumoProviderError("SUMO motion stage belongs to another scenario")
        if request.provider_id != self._config.provider_id:
            raise SumoProviderError("SUMO motion stage names another provider")
        if self._last_time is None:
            raise SumoProviderError("SUMO reset must precede step_stage")
        expected = SimulationTime(
            tick=self._last_time.tick + 1,
            sim_time_ns=self._last_time.sim_time_ns + self._clock.step_ns,
        )
        if request.target != expected:
            raise SumoProviderError(
                "SUMO motion stage target must equal the next configured barrier"
            )
        response = await self._request(
            "step_stage",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": canonical_request.model_dump(mode="json"),
            },
        )
        try:
            staged = SumoMotionStageResponse.model_validate(response)
            canonical_staged = SumoMotionStageResponse.model_validate(
                staged.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise SumoProviderError("SUMO motion stage response is invalid") from exc
        if (
            staged != canonical_staged
            or canonical_json_bytes(staged.model_dump(mode="json"))
            != canonical_json_bytes(dict(response))
        ):
            raise SumoProviderError("SUMO motion stage response is not canonical")
        if (
            staged.run_id != request.run_id
            or staged.scenario_digest != request.scenario_digest
            or staged.provider_id != request.provider_id
            or staged.target != request.target
        ):
            raise SumoProviderError("SUMO motion stage response binding is inconsistent")
        receipt = self._parse_receipt(
            {"receipt": staged.receipt.model_dump(mode="json")},
            target=request.target,
            operation="step_stage",
        )
        traffic_event_states = self._traffic_light_event_states(receipt)
        if traffic_event_states is None or traffic_event_states != staged.traffic_lights:
            raise SumoProviderError(
                "SUMO traffic-light event does not match the TraCI state response"
            )
        sample_entity_ids = tuple(sample.entity_id for sample in staged.samples)
        if sample_entity_ids != self._owned_entity_ids:
            raise SumoProviderError(
                "SUMO motion samples do not close over owned dynamic entities"
            )
        for sample in staged.samples:
            if (
                sample.run_id != request.run_id
                or sample.scenario_digest != request.scenario_digest
                or sample.at != request.target
                or sample.stage != "motion"
                or sample.provider_id != request.provider_id
                or sample.sample_kind != "dynamic"
            ):
                raise SumoProviderError("SUMO motion sample binding is inconsistent")
        self._validate_samples_against_receipt(staged.samples, receipt, staged.traffic_lights)
        contribution = self._motion_contribution(request, staged.samples)
        result = MotionStageResult(
            schema_version="aero-bench.provider-stage-result/v1",
            run_id=request.run_id,
            scenario_digest=request.scenario_digest,
            provider_id=request.provider_id,
            target=request.target,
            stage="motion",
            step_receipt=receipt,
            step_receipt_digest=step_receipt_digest_value(receipt),
            contribution=contribution,
            predecessor_barriers=(),
        )
        self._last_time = receipt.reached
        return result

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        raise SumoProviderError(
            f"SUMO provider does not own tool {request.tool_id!r}; use TraCI traffic state"
        )

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt:
        self._require_prepared()
        if request.run_id != self._run_id or request.terminal_time != self._last_time:
            raise SumoProviderError("SUMO finalization identity or time mismatch")
        response = await self._request(
            "finalize",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": request.model_dump(mode="json"),
            },
        )
        try:
            receipt = ProviderFinalizationReceipt.model_validate(
                response.get("receipt")
            )
        except ValidationError as exc:
            raise SumoProviderError("SUMO finalization response is invalid") from exc
        expected = {
            requirement.artifact_id: requirement
            for requirement in self._manifest.artifact_requirements
        }
        actual: dict[str, FinalizedArtifact] = {
            artifact.artifact_id: artifact for artifact in receipt.artifacts
        }
        if (
            receipt.run_id != self._run_id
            or receipt.provider_id != self._config.provider_id
            or receipt.event_chain_root != request.event_chain_root
            or set(actual) != set(expected)
            or any(
                artifact.size_bytes > expected[artifact_id].max_size_bytes
                for artifact_id, artifact in actual.items()
            )
        ):
            raise SumoProviderError("SUMO finalization receipt identity is invalid")
        return receipt

    async def snapshot_digest(self) -> str:
        self._require_prepared()
        response = await self._request(
            "snapshot",
            {"provider_id": self._config.provider_id, "run_id": self._run_id},
        )
        try:
            snapshot = SumoSnapshotResponse.model_validate(response)
        except ValidationError as exc:
            raise SumoProviderError(
                "SUMO snapshot response has no valid digest"
            ) from exc
        return TypeAdapter(Sha256).validate_python(snapshot.snapshot_digest)

    async def shutdown(self) -> None:
        if self._transport is None:
            return
        try:
            if self._prepared:
                response = await self._request(
                    "shutdown",
                    {
                        "provider_id": self._config.provider_id,
                        "run_id": self._run_id,
                    },
                )
                if response.get("status") != "stopped":
                    raise SumoProviderError(
                        "SUMO provider did not acknowledge shutdown"
                    )
        finally:
            await self._transport.close()
            self._prepared = False
            self._transport = None
            self._last_time = None
