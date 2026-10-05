from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    Identifier,
    NamedValue,
    Sha256,
    StrictModel,
)
from aero_bench.gateway.contracts import ObservationEnvelope
from aero_bench.providers.contracts import (
    ProviderCommandResult,
    ProviderManifest,
    ProviderSession,
)
from aero_bench.providers.ns3.config import (
    BUILDING_ATTENUATION_DB,
    LOG_DISTANCE_EXPONENT,
    NETWORK_MODEL,
    PROPAGATION_MODEL,
    RX_NOISE_FIGURE_DB,
    THERMAL_NOISE_DENSITY_DBM_HZ,
    Ns3Config,
    ns3_capabilities,
)
from aero_bench.providers.ns3.protocol import (
    DELIVERY_EVENT_SCHEMA,
    MAILBOX_OBSERVATION_PREFIX,
    MAILBOX_OBSERVATION_SCHEMA,
    MAX_MAILBOX_DELIVERIES_PER_BARRIER,
    MAX_MAILBOX_OBSERVATION_BYTES,
    MAX_NETWORK_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    STATE_SCHEMA,
    JsonLineTransport,
    Ns3Transport,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    FinalizedArtifact,
    NetworkStageRequest,
    NetworkStageResult,
    ProviderEvent,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    ProviderStageRequest,
    SceneContribution,
    SimulationTime,
    StageBarrierDigest,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.vocabulary import (
    PUBLIC_EVENT_EVENT_TYPE,
    PUBLIC_NETWORK_LINK_EVENT_TYPE,
    PUBLIC_PROVIDER_EVENT_TYPES,
)
from aero_bench.world.resolved import ResolvedScenario


_PUBLIC_EVENT_SCHEMA = "public.event.v3"
_PUBLIC_NETWORK_LINK_SCHEMA = "public.network.link.v3"


class Ns3ProviderError(RuntimeError):
    pass


class Ns3ProviderNotReady(Ns3ProviderError):
    pass


class Ns3Readiness(StrictModel):
    status: Literal["ready"]
    provider_id: Identifier
    protocol_version: str
    runtime_image: str
    config_digest: Sha256
    scenario_digest: Sha256
    network_projection_digest: Sha256
    network_model: Literal["wifi-adhoc-scene-mobility/v1"]
    propagation_model: Literal["log-distance-with-scene-volumes/v1"]
    log_distance_exponent: float = Field(ge=1.0, le=8.0, strict=True)
    building_attenuation_db: float = Field(gt=0.0, le=1_000.0, strict=True)
    rx_noise_figure_db: float = Field(ge=0.0, le=100.0, strict=True)
    thermal_noise_density_dbm_hz: float = Field(
        ge=-300.0, le=0.0, strict=True
    )
    propagation_volume_count: int = Field(ge=0, strict=True)
    clock_step_ns: int = Field(ge=1, le=2**63 - 1, strict=True)
    clock_max_steps: int = Field(ge=1, strict=True)
    supported_wifi_standards: tuple[Literal["802.11n", "802.11ac", "802.11ax"], ...]
    radio_profile_count: int = Field(ge=1, le=254, strict=True)
    node_count: int = Field(ge=2, le=254, strict=True)
    link_count: int = Field(ge=1, le=253, strict=True)
    ns3_version: str
    ns3_commit: str


class NetworkQos(StrictModel):
    traffic_class: Identifier
    priority: int = Field(ge=0)
    reliability: Literal["best_effort", "reliable"]

    @model_validator(mode="after")
    def only_supported_udp_semantics(self) -> "NetworkQos":
        if (
            self.traffic_class != "best_effort"
            or self.priority != 0
            or self.reliability != "best_effort"
        ):
            raise ValueError(
                "ns-3 provider only supports best_effort traffic with priority 0"
            )
        return self


class NetworkMessage(StrictModel):
    message_id: Identifier
    source: Identifier
    destination: Identifier
    payload_base64: str = Field(min_length=1)
    qos: NetworkQos
    send_time: SimulationTime
    payload_sha256: Sha256

    @field_validator("payload_base64")
    @classmethod
    def valid_base64(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("payload_base64 must contain strict base64") from exc
        if not raw:
            raise ValueError("network payload must not be empty")
        if len(raw) > MAX_NETWORK_PAYLOAD_BYTES:
            raise ValueError(
                f"network payload exceeds {MAX_NETWORK_PAYLOAD_BYTES} bytes"
            )
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("payload_base64 must use canonical base64 encoding")
        return value

    @model_validator(mode="after")
    def payload_digest_matches(self) -> "NetworkMessage":
        raw = base64.b64decode(self.payload_base64, validate=True)
        if hashlib.sha256(raw).hexdigest() != self.payload_sha256:
            raise ValueError("payload_sha256 does not match payload_base64")
        return self


class NetworkSendArguments(StrictModel):
    work_order_id: Identifier
    message_id: Identifier
    source: Identifier
    destination: Identifier
    payload_base64: str = Field(min_length=1)
    payload_sha256: Sha256
    traffic_class: Identifier
    priority: int = Field(ge=0, strict=True)
    reliability: Literal["best_effort", "reliable"]

    def message_at(self, issued_at: SimulationTime) -> NetworkMessage:
        return NetworkMessage(
            message_id=self.message_id,
            source=self.source,
            destination=self.destination,
            payload_base64=self.payload_base64,
            payload_sha256=self.payload_sha256,
            qos=NetworkQos(
                traffic_class=self.traffic_class,
                priority=self.priority,
                reliability=self.reliability,
            ),
            send_time=issued_at,
        )


class NetworkCommandBinding(StrictModel):
    command_id: Identifier
    agent_id: Identifier
    work_order_id: Identifier
    message: NetworkMessage


class NetworkTraceRecord(StrictModel):
    run_id: Sha256
    message_id: Identifier
    source: Identifier
    destination: Identifier
    payload_sha256: Sha256
    send_time: SimulationTime
    outcome: str = Field(pattern=r"^(delivered|dropped)$")
    arrival_time: SimulationTime | None = None
    drop_reason: str | None = None
    path: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def outcome_contract(self) -> "NetworkTraceRecord":
        if self.outcome == "delivered":
            if self.arrival_time is None:
                raise ValueError("delivered network trace requires arrival_time")
            if (
                self.arrival_time.tick < self.send_time.tick
                or self.arrival_time.sim_time_ns < self.send_time.sim_time_ns
            ):
                raise ValueError("network arrival precedes send time")
            if self.drop_reason is not None:
                raise ValueError("delivered network trace cannot have drop_reason")
        elif self.arrival_time is not None:
            raise ValueError("dropped network trace cannot have arrival_time")
        elif not self.drop_reason:
            raise ValueError("dropped network trace requires drop_reason")
        return self


class NetworkSubmission(StrictModel):
    """Acceptance evidence returned before the simulator has advanced.

    A submission is deliberately not a NetworkTraceRecord. ns-3 can only
    establish delivery or loss after its event queue has run through the
    relevant network stage. Callback-confirmed delivery is returned atomically
    in that stage response.
    """

    run_id: Sha256
    work_order_id: Identifier
    message_id: Identifier
    source: Identifier
    destination: Identifier
    payload_sha256: Sha256
    send_time: SimulationTime
    status: Literal["queued"]


class DeliveredPayload(StrictModel):
    work_order_id: Identifier
    trace: NetworkTraceRecord
    payload_base64: str = Field(min_length=1)

    @field_validator("payload_base64")
    @classmethod
    def strict_base64(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
            if not raw:
                raise ValueError("delivered payload must not be empty")
            if len(raw) > MAX_NETWORK_PAYLOAD_BYTES:
                raise ValueError(
                    f"delivered payload exceeds {MAX_NETWORK_PAYLOAD_BYTES} bytes"
                )
        except (ValueError, binascii.Error) as exc:
            raise ValueError("delivered payload must contain strict base64") from exc
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("delivered payload must use canonical base64 encoding")
        return value

    @model_validator(mode="after")
    def trace_digest_matches(self) -> "DeliveredPayload":
        raw = base64.b64decode(self.payload_base64, validate=True)
        if hashlib.sha256(raw).hexdigest() != self.trace.payload_sha256:
            raise ValueError("delivered payload does not match network trace digest")
        if self.trace.outcome != "delivered":
            raise ValueError("only delivered network traces may release payloads")
        return self


class NetworkMailboxBinding(StrictModel):
    """One immutable Agent/observation/address authorization."""

    agent_id: Identifier
    observation_id: Identifier
    endpoint_id: Identifier
    schema_file: FileRef

    @model_validator(mode="after")
    def observation_names_exact_endpoint(self) -> "NetworkMailboxBinding":
        if self.observation_id != f"{MAILBOX_OBSERVATION_PREFIX}{self.endpoint_id}":
            raise ValueError(
                "network mailbox observation_id must name its exact endpoint"
            )
        return self


class NetworkMailboxMessage(StrictModel):
    """Callback-confirmed payload exposed only to its destination mailbox."""

    work_order_id: Identifier
    message_id: Identifier
    command_id: Identifier
    sender_agent_id: Identifier
    source: Identifier
    destination: Identifier
    payload_base64: str = Field(min_length=1)
    payload_sha256: Sha256
    send_time: SimulationTime
    arrival_time: SimulationTime
    path: tuple[Identifier, ...]

    @field_validator("payload_base64")
    @classmethod
    def canonical_payload(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("mailbox payload must contain strict base64") from error
        if not raw or len(raw) > MAX_NETWORK_PAYLOAD_BYTES:
            raise ValueError("mailbox payload size is outside the network contract")
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("mailbox payload must use canonical base64 encoding")
        return value

    @model_validator(mode="after")
    def delivery_is_consistent(self) -> "NetworkMailboxMessage":
        raw = base64.b64decode(self.payload_base64, validate=True)
        if hashlib.sha256(raw).hexdigest() != self.payload_sha256:
            raise ValueError("mailbox payload digest does not match its bytes")
        if (
            self.arrival_time.tick < self.send_time.tick
            or self.arrival_time.sim_time_ns < self.send_time.sim_time_ns
        ):
            raise ValueError("mailbox arrival precedes message submission")
        return self


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON object key: {key}")
        document[key] = value
    return document


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _decode_mailbox_messages(value: str) -> tuple[NetworkMailboxMessage, ...]:
    try:
        document = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("messages_json is not strict JSON") from error
    if not isinstance(document, list):
        raise ValueError("messages_json must encode an array")
    if value != canonical_json_bytes(document).decode("utf-8"):
        raise ValueError("messages_json must use canonical JSON encoding")
    try:
        return tuple(NetworkMailboxMessage.model_validate(item) for item in document)
    except ValidationError as error:
        raise ValueError("messages_json contains an invalid mailbox message") from error


class NetworkMailboxObservationPayload(StrictModel):
    """Gateway-schema payload for deliveries closed by one network barrier."""

    schema_version: Literal["aero-bench.network-mailbox-observation/v1"]
    run_id: Sha256
    provider_id: Identifier
    agent_id: Identifier
    observation_id: Identifier
    mailbox_endpoint_id: Identifier
    time_tick: int = Field(ge=0, strict=True)
    sim_time_ns: int = Field(ge=0, strict=True)
    stage: Literal["reset", "network"]
    input_scene_state_digest: Sha256 | None
    motion_barrier_digest: Sha256 | None
    network_step_receipt_digest: Sha256
    message_count: int = Field(
        ge=0, le=MAX_MAILBOX_DELIVERIES_PER_BARRIER, strict=True
    )
    messages_digest: Sha256
    messages_json: str = Field(
        min_length=2,
        max_length=MAX_MAILBOX_OBSERVATION_BYTES,
        json_schema_extra={"contentMediaType": "application/json"},
    )

    @model_validator(mode="after")
    def batch_is_canonical_and_closed(self) -> "NetworkMailboxObservationPayload":
        if self.observation_id != (
            f"{MAILBOX_OBSERVATION_PREFIX}{self.mailbox_endpoint_id}"
        ):
            raise ValueError("mailbox observation identity changed after resolution")
        identity_digests = (
            self.network_step_receipt_digest,
            self.messages_digest,
            self.input_scene_state_digest,
            self.motion_barrier_digest,
        )
        if any(value == "0" * 64 for value in identity_digests if value is not None):
            raise ValueError("mailbox observation contains a placeholder digest")
        if self.stage == "reset":
            if (
                self.time_tick != 0
                or self.sim_time_ns != 0
                or self.input_scene_state_digest is not None
                or self.motion_barrier_digest is not None
                or self.message_count != 0
            ):
                raise ValueError("reset mailbox observation must bind time zero only")
        elif (
            self.time_tick == 0
            or self.input_scene_state_digest is None
            or self.motion_barrier_digest is None
        ):
            raise ValueError(
                "network mailbox observation must bind SceneState and motion barrier"
            )

        messages = _decode_mailbox_messages(self.messages_json)
        if len(messages) != self.message_count:
            raise ValueError("mailbox message_count differs from messages_json")
        expected_digest = hashlib.sha256(
            canonical_json_bytes(
                [message.model_dump(mode="json") for message in messages]
            )
        ).hexdigest()
        if self.messages_digest != expected_digest:
            raise ValueError("mailbox messages_digest differs from messages_json")
        message_ids = tuple(message.message_id for message in messages)
        if message_ids != tuple(sorted(message_ids)) or len(message_ids) != len(
            set(message_ids)
        ):
            raise ValueError("mailbox messages must be sorted and unique")
        for message in messages:
            if message.destination != self.mailbox_endpoint_id:
                raise ValueError("mailbox contains a message for another endpoint")
            if (
                message.arrival_time.tick != self.time_tick
                or message.arrival_time.sim_time_ns > self.sim_time_ns
            ):
                raise ValueError(
                    "mailbox contains a message outside its closed network barrier"
                )
        return self


class Ns3SnapshotResponse(StrictModel):
    snapshot_digest: Sha256


class Ns3StageResponse(StrictModel):
    """Strict wire response for one scene-bound network stage."""

    receipt: StepReceipt
    deliveries: tuple[DeliveredPayload, ...]
    input_scene_state_digest: Sha256
    predecessor_barriers: tuple[StageBarrierDigest, ...] = Field(min_length=1)


class Ns3Provider(ProviderSession):
    """Client for a real ns-3 discrete-event message-in-the-loop provider.

    Every frame, including the first ``prepare``, presents the executor-issued
    run-scoped session token. The workload pins the same token from
    ``AERO_BENCH_PROVIDER_TOKEN`` and rejects any peer that cannot present it.
    """

    _STATE_SCHEMA = STATE_SCHEMA
    _DELIVERY_EVENT = "network.delivery"
    _DELIVERY_EVENT_SCHEMA = DELIVERY_EVENT_SCHEMA

    def __init__(
        self,
        *,
        config: Ns3Config,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        session_token: str,
        scenario: ResolvedScenario,
    ):
        if manifest.provider_id != config.provider_id:
            raise ValueError("ns-3 manifest/provider configuration IDs differ")
        if manifest.adapter != "ns3.rpc":
            raise ValueError("ns-3 manifest adapter must be ns3.rpc")
        expected_capabilities = ns3_capabilities(config.supported_wifi_standards)
        if manifest.capabilities != expected_capabilities:
            raise ValueError(
                "ns-3 manifest capabilities must exactly match the configured "
                "Wi-Fi link model"
            )
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise TypeError("ns-3 runtime_endpoint must be a RuntimeEndpoint")
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("ns-3 scenario must be a ResolvedScenario")
        scenario_provider = next(
            (
                provider
                for provider in scenario.providers
                if provider.provider_id == config.provider_id
            ),
            None,
        )
        if (
            scenario_provider is None
            or scenario_provider.roles != ("wireless_network",)
            or scenario_provider.capability_ids != manifest.capabilities
        ):
            raise ValueError(
                "ns-3 provider identity differs from ResolvedScenario authority"
            )
        network = scenario.network
        if network is None or network.provider_id != config.provider_id:
            raise ValueError("ns-3 network authority is absent from ResolvedScenario")
        network_endpoint_ids = frozenset(
            binding.endpoint_id for binding in network.node_bindings
        )
        if len(network_endpoint_ids) != len(network.node_bindings):
            raise ValueError("ns-3 network endpoints are not bijective")

        mailbox_bindings: list[NetworkMailboxBinding] = []
        mailbox_owner_by_endpoint: dict[str, str] = {}
        for observation in scenario.task.observations:
            reserved_mailbox_name = observation.observation_id.startswith(
                MAILBOX_OBSERVATION_PREFIX
            )
            if observation.endpoint_id != config.provider_id:
                if reserved_mailbox_name:
                    raise ValueError(
                        "network mailbox observation names another Provider"
                    )
                continue
            if not reserved_mailbox_name or observation.inspection is not None:
                raise ValueError(
                    "ns-3 observations must be dedicated network mailbox grants"
                )
            endpoint_id = observation.observation_id[
                len(MAILBOX_OBSERVATION_PREFIX) :
            ]
            if endpoint_id not in network_endpoint_ids:
                raise ValueError(
                    "network mailbox observation names an undeclared endpoint"
                )
            previous_owner = mailbox_owner_by_endpoint.get(endpoint_id)
            if previous_owner is not None:
                raise ValueError(
                    "a network mailbox endpoint may be granted to only one Agent"
                )
            mailbox_owner_by_endpoint[endpoint_id] = observation.agent_id
            mailbox_bindings.append(
                NetworkMailboxBinding(
                    agent_id=observation.agent_id,
                    observation_id=observation.observation_id,
                    endpoint_id=endpoint_id,
                    schema_file=observation.schema_file,
                )
            )
        mailbox_bindings.sort(
            key=lambda binding: (binding.agent_id, binding.observation_id)
        )
        agent_mailbox_endpoints: dict[str, set[str]] = {}
        for binding in mailbox_bindings:
            agent_mailbox_endpoints.setdefault(binding.agent_id, set()).add(
                binding.endpoint_id
            )
        for tool in scenario.task.tools:
            if tool.endpoint_id != config.provider_id:
                continue
            if tool.tool_id != "network.send":
                raise ValueError("ns-3 owns only the network.send Agent tool")
            if not agent_mailbox_endpoints.get(tool.agent_id):
                raise ValueError(
                    "network.send Agent lacks an authorized source mailbox endpoint"
                )

        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError("ns-3 run_id must be a SHA-256 digest") from error
        if validated_run_id == "0" * 64:
            raise ValueError("ns-3 run_id cannot be a placeholder digest")
        try:
            validated_token = TypeAdapter(Sha256).validate_python(session_token)
        except (TypeError, ValueError) as error:
            raise ValueError("ns-3 session_token must be a SHA-256 digest") from error
        if validated_token == "0" * 64:
            raise ValueError("ns-3 session_token cannot be a placeholder digest")
        self._config = config
        self._scenario = scenario
        self._manifest = manifest
        self._runtime_endpoint = runtime_endpoint
        self._run_id = validated_run_id
        self._session_token = validated_token
        self._network_endpoint_ids = network_endpoint_ids
        self._mailbox_bindings = {
            (binding.agent_id, binding.observation_id): binding
            for binding in mailbox_bindings
        }
        self._agent_mailbox_endpoints = {
            agent_id: frozenset(endpoint_ids)
            for agent_id, endpoint_ids in agent_mailbox_endpoints.items()
        }
        self._mailbox_batches: dict[
            tuple[str, str, int, int], NetworkMailboxObservationPayload
        ] = {}
        self._transport: Ns3Transport | None = None
        self._prepared = False
        self._last_time: SimulationTime | None = None
        self._clock_step_ns: int | None = None
        self._clock_max_steps: int | None = None
        self._link_count: int | None = None
        self._scenario_digest: str | None = None
        self._network_projection_digest: str | None = None
        self._message_bindings: dict[str, NetworkCommandBinding] = {}
        self._command_ids: set[str] = set()
        self._delivered_message_ids: set[str] = set()

    @staticmethod
    async def connect_runtime(endpoint: RuntimeEndpoint) -> Ns3Transport:
        """Connect to the materializer-owned endpoint for a production session."""

        return await JsonLineTransport.connect(endpoint)

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

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
            raise Ns3ProviderNotReady("ns-3 provider has not completed readiness")

    async def prepare(self) -> None:
        if self._prepared:
            raise Ns3ProviderError("ns-3 provider prepare called twice")
        artifact_requirements = self._artifact_requirements()
        response = await self._request(
            "prepare",
            {
                "provider_id": self._config.provider_id,
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": self._manifest.runtime_image,
                "config_digest": self._manifest.config_digest,
                "network_model": self._config.network_model,
                "supported_wifi_standards": list(
                    self._config.supported_wifi_standards
                ),
                "artifact_requirements": artifact_requirements,
            },
        )
        try:
            readiness = Ns3Readiness.model_validate(response)
        except ValidationError as exc:
            raise Ns3ProviderError(
                "ns-3 provider readiness response is invalid"
            ) from exc
        if readiness.status != "ready":
            raise Ns3ProviderError(
                f"ns-3 provider did not become ready: {readiness.status!r}"
            )
        if readiness.provider_id != self._config.provider_id:
            raise Ns3ProviderError(
                "ns-3 readiness provider_id differs from declaration"
            )
        if readiness.protocol_version != PROTOCOL_VERSION:
            raise Ns3ProviderError("ns-3 provider protocol version mismatch")
        if readiness.runtime_image != self._manifest.runtime_image:
            raise Ns3ProviderError("ns-3 provider image identity mismatch")
        if readiness.config_digest != self._manifest.config_digest:
            raise Ns3ProviderError("ns-3 provider config digest mismatch")
        if readiness.network_model != NETWORK_MODEL:
            raise Ns3ProviderError("ns-3 provider network model mismatch")
        if (
            readiness.propagation_model != PROPAGATION_MODEL
            or readiness.log_distance_exponent != LOG_DISTANCE_EXPONENT
            or readiness.building_attenuation_db != BUILDING_ATTENUATION_DB
            or readiness.rx_noise_figure_db != RX_NOISE_FIGURE_DB
            or readiness.thermal_noise_density_dbm_hz
            != THERMAL_NOISE_DENSITY_DBM_HZ
        ):
            raise Ns3ProviderError("ns-3 provider propagation model mismatch")
        if readiness.supported_wifi_standards != self._config.supported_wifi_standards:
            raise Ns3ProviderError("ns-3 provider Wi-Fi standard set mismatch")
        if readiness.scenario_digest != self._scenario.scenario_digest:
            raise Ns3ProviderError("ns-3 provider scenario identity mismatch")
        if readiness.network_projection_digest == "0" * 64:
            raise Ns3ProviderError("ns-3 network projection identity is a placeholder")
        if (
            readiness.clock_max_steps
            > (2**63 - 1) // readiness.clock_step_ns
        ):
            raise Ns3ProviderError("ns-3 provider clock horizon exceeds ns-3 time")
        if readiness.radio_profile_count > readiness.node_count:
            raise Ns3ProviderError("ns-3 provider projection counts are impossible")
        if readiness.ns3_version != self._config.ns3.version:
            raise Ns3ProviderError("ns-3 version identity does not match configuration")
        if readiness.ns3_commit != self._config.ns3.commit:
            raise Ns3ProviderError("ns-3 commit identity does not match configuration")
        self._clock_step_ns = readiness.clock_step_ns
        self._clock_max_steps = readiness.clock_max_steps
        self._link_count = readiness.link_count
        self._scenario_digest = readiness.scenario_digest
        self._network_projection_digest = readiness.network_projection_digest
        self._prepared = True

    def _artifact_requirements(self) -> list[dict[str, object]]:
        requirements = self._manifest.artifact_requirements
        if len(requirements) != 1:
            raise Ns3ProviderError(
                "ns-3 provider requires exactly one declared network.delivery artifact"
            )
        requirement = requirements[0]
        if not isinstance(requirement, ArtifactRequirement):
            raise Ns3ProviderError("ns-3 artifact requirement is not strict")
        if (
            requirement.artifact_type != "network.delivery"
            or requirement.producer_id != self._config.provider_id
            or requirement.source_asset_id is not None
        ):
            raise Ns3ProviderError(
                "ns-3 network.delivery may be public or private and source_asset_id "
                "must be null"
            )
        return [requirement.model_dump(mode="json")]

    def _parse_stage_receipt(
        self, receipt: StepReceipt, request: NetworkStageRequest
    ) -> StepReceipt:
        if (
            receipt.run_id != request.run_id
            or receipt.provider_id != self._config.provider_id
        ):
            raise Ns3ProviderError("ns-3 StepReceipt identity mismatch")
        if receipt.reached != request.target:
            raise Ns3ProviderError(
                "ns-3 backend did not reach the requested sim_time_ns"
            )
        if any(event.time != request.target for event in receipt.events):
            raise Ns3ProviderError(
                "ns-3 staged receipt events must occur at the stage target"
            )
        state_events = tuple(
            event
            for event in receipt.events
            if event.payload_schema_id == self._STATE_SCHEMA
        )
        if len(state_events) != 1:
            raise Ns3ProviderError(
                "ns-3 stage omitted its unique authoritative state event"
            )
        evidence = {item.name: item.value for item in state_events[0].payload}
        staged_evidence_digests = (
            evidence.get("staged_node_positions_digest"),
            evidence.get("staged_link_attenuations_digest"),
            evidence.get("staged_link_obstructions_digest"),
            evidence.get("link_state_digest"),
        )
        if (
            evidence.get("accepted_scene_state_digest")
            != request.scene_state_digest
            or evidence.get("motion_predecessor_barrier_digest")
            != request.predecessor_barriers[0].barrier_digest
            or evidence.get("staged_positions_applied") is not True
            or evidence.get("staged_position_binding")
            != "scene-state-constant-position-mobility/v1"
            or evidence.get("network_model") != NETWORK_MODEL
            or evidence.get("network_projection_digest")
            != self._network_projection_digest
            or evidence.get("link_state_count") != self._link_count
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or value == "0" * 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in staged_evidence_digests
            )
        ):
            raise Ns3ProviderError(
                "ns-3 state event does not bind the staged SceneState input"
            )
        for event in receipt.events:
            if event.event_id not in PUBLIC_PROVIDER_EVENT_TYPES:
                continue
            if event.event_id == PUBLIC_NETWORK_LINK_EVENT_TYPE:
                expected_schema = _PUBLIC_NETWORK_LINK_SCHEMA
            elif event.event_id == PUBLIC_EVENT_EVENT_TYPE:
                expected_schema = _PUBLIC_EVENT_SCHEMA
            else:
                raise Ns3ProviderError(
                    "ns-3 emitted another Provider's public event type"
                )
            if event.payload_schema_id != expected_schema:
                raise Ns3ProviderError("ns-3 emitted a legacy public event schema")
        return receipt

    async def reset(self, *, seed: int) -> StepReceipt:
        self._require_prepared()
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not 1 <= seed <= 0xFFFFFFFF
        ):
            raise ValueError("ns-3 reset seed must be in [1, 2^32-1]")
        response = await self._request(
            "reset",
            {"provider_id": self._config.provider_id, "seed": seed},
        )
        try:
            receipt = StepReceipt.model_validate(response.get("receipt"))
        except ValidationError as exc:
            raise Ns3ProviderError(
                "ns-3 reset response does not contain a valid StepReceipt"
            ) from exc
        if receipt.provider_id != self._config.provider_id:
            raise Ns3ProviderError("ns-3 reset receipt identity mismatch")
        if receipt.reached != SimulationTime(tick=0, sim_time_ns=0):
            raise Ns3ProviderError("ns-3 reset must return zero simulation time")
        if not any(
            event.payload_schema_id == self._STATE_SCHEMA for event in receipt.events
        ):
            raise Ns3ProviderError(
                "ns-3 reset omitted the authoritative state event"
            )
        if receipt.run_id != self._run_id:
            raise Ns3ProviderError("ns-3 reset receipt run identity mismatch")
        self._last_time = receipt.reached
        self._message_bindings.clear()
        self._command_ids.clear()
        self._delivered_message_ids.clear()
        self._mailbox_batches.clear()
        self._record_mailbox_batches(
            at=receipt.reached,
            stage="reset",
            input_scene_state_digest=None,
            motion_barrier_digest=None,
            network_step_receipt_digest=step_receipt_digest_value(receipt),
            deliveries=(),
        )
        return receipt

    def _canonical_network_stage_request(
        self, request: ProviderStageRequest
    ) -> NetworkStageRequest:
        if not isinstance(request, NetworkStageRequest):
            raise Ns3ProviderError("ns-3 accepts only NetworkStageRequest")
        try:
            canonical = NetworkStageRequest.model_validate(request.model_dump(mode="json"))
        except ValidationError as exc:
            raise Ns3ProviderError("ns-3 stage request is not canonical") from exc
        if canonical != request:
            raise Ns3ProviderError("ns-3 stage request is not canonically serialized")
        return canonical

    @staticmethod
    def _empty_network_contribution(
        request: NetworkStageRequest,
    ) -> SceneContribution:
        fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": request.run_id,
            "scenario_digest": request.scenario_digest,
            "at": request.target,
            "stage": "network",
            "provider_id": request.provider_id,
            "samples": (),
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
            contribution_digest=scene_contribution_digest_value(contribution_candidate),
        )

    @staticmethod
    def _mailbox_key(
        binding: NetworkMailboxBinding,
        at: SimulationTime,
    ) -> tuple[str, str, int, int]:
        return (
            binding.agent_id,
            binding.observation_id,
            at.tick,
            at.sim_time_ns,
        )

    def _mailbox_message(
        self, delivery: DeliveredPayload
    ) -> NetworkMailboxMessage:
        trace = delivery.trace
        if trace.arrival_time is None:
            raise Ns3ProviderError("delivered mailbox message has no arrival time")
        command_binding = self._message_bindings.get(trace.message_id)
        if command_binding is None:
            raise Ns3ProviderError("delivered mailbox message has no command binding")
        return NetworkMailboxMessage(
            work_order_id=delivery.work_order_id,
            message_id=trace.message_id,
            command_id=command_binding.command_id,
            sender_agent_id=command_binding.agent_id,
            source=trace.source,
            destination=trace.destination,
            payload_base64=delivery.payload_base64,
            payload_sha256=trace.payload_sha256,
            send_time=trace.send_time,
            arrival_time=trace.arrival_time,
            path=trace.path,
        )

    def _record_mailbox_batches(
        self,
        *,
        at: SimulationTime,
        stage: Literal["reset", "network"],
        input_scene_state_digest: str | None,
        motion_barrier_digest: str | None,
        network_step_receipt_digest: str,
        deliveries: tuple[DeliveredPayload, ...],
    ) -> None:
        deliveries_by_endpoint: dict[str, list[DeliveredPayload]] = {}
        for delivery in deliveries:
            destination = delivery.trace.destination
            if destination not in self._network_endpoint_ids:
                raise Ns3ProviderError(
                    "ns-3 released a delivery for an undeclared network endpoint"
                )
            deliveries_by_endpoint.setdefault(destination, []).append(delivery)

        pending: dict[
            tuple[str, str, int, int], NetworkMailboxObservationPayload
        ] = {}
        ordered_bindings = sorted(
            self._mailbox_bindings.values(),
            key=lambda binding: (binding.agent_id, binding.observation_id),
        )
        for binding in ordered_bindings:
            messages = tuple(
                self._mailbox_message(delivery)
                for delivery in deliveries_by_endpoint.get(binding.endpoint_id, ())
            )
            if len(messages) > MAX_MAILBOX_DELIVERIES_PER_BARRIER:
                raise Ns3ProviderError(
                    "network mailbox delivery batch exceeds its declared bound"
                )
            message_document = [
                message.model_dump(mode="json") for message in messages
            ]
            messages_json = canonical_json_bytes(message_document).decode("utf-8")
            messages_digest = hashlib.sha256(
                canonical_json_bytes(message_document)
            ).hexdigest()
            payload = NetworkMailboxObservationPayload(
                schema_version=MAILBOX_OBSERVATION_SCHEMA,
                run_id=self._run_id,
                provider_id=self._config.provider_id,
                agent_id=binding.agent_id,
                observation_id=binding.observation_id,
                mailbox_endpoint_id=binding.endpoint_id,
                time_tick=at.tick,
                sim_time_ns=at.sim_time_ns,
                stage=stage,
                input_scene_state_digest=input_scene_state_digest,
                motion_barrier_digest=motion_barrier_digest,
                network_step_receipt_digest=network_step_receipt_digest,
                message_count=len(messages),
                messages_digest=messages_digest,
                messages_json=messages_json,
            )
            key = self._mailbox_key(binding, at)
            if key in self._mailbox_batches or key in pending:
                raise Ns3ProviderError(
                    "network mailbox barrier was already committed"
                )
            pending[key] = payload
        if stage == "network":
            # Agent turns precede the next motion stage, so only Observation[k+1]
            # remains addressable. Repeated reads at that barrier stay idempotent.
            self._mailbox_batches.clear()
        self._mailbox_batches.update(pending)

    async def step_stage(
        self, request: ProviderStageRequest
    ) -> NetworkStageResult:
        self._require_prepared()
        canonical_request = self._canonical_network_stage_request(request)
        if canonical_request.run_id != self._run_id:
            raise Ns3ProviderError("ns-3 stage request belongs to another run")
        if self._scenario_digest is None:
            raise Ns3ProviderNotReady("ns-3 scenario identity is unavailable")
        if canonical_request.scenario_digest != self._scenario_digest:
            raise Ns3ProviderError("ns-3 stage request belongs to another scenario")
        if canonical_request.provider_id != self._config.provider_id:
            raise Ns3ProviderError("ns-3 stage request names another provider")
        if self._last_time is None:
            raise Ns3ProviderNotReady("ns-3 provider has not been reset")
        if self._clock_step_ns is None or self._clock_max_steps is None:
            raise Ns3ProviderNotReady("ns-3 provider clock identity is unavailable")
        expected = SimulationTime(
            tick=self._last_time.tick + 1,
            sim_time_ns=self._last_time.sim_time_ns + self._clock_step_ns,
        )
        if (
            canonical_request.target != expected
            or canonical_request.target.tick > self._clock_max_steps
        ):
            raise Ns3ProviderError(
                "ns-3 stage target must equal the next authorized clock barrier"
            )
        predecessors = canonical_request.predecessor_barriers
        if (
            len(predecessors) != 1
            or predecessors[0].stage != "motion"
            or predecessors[0].barrier_digest
            != canonical_request.scene_state.stage_barrier.barrier_digest
        ):
            raise Ns3ProviderError(
                "ns-3 stage predecessors must be exactly the SceneState motion barrier"
            )
        response = await self._request(
            "step_stage", {"request": canonical_request.model_dump(mode="json")}
        )
        try:
            staged_response = Ns3StageResponse.model_validate(response)
        except ValidationError as exc:
            raise Ns3ProviderError("ns-3 staged response is invalid") from exc
        if (
            staged_response.input_scene_state_digest
            != canonical_request.scene_state_digest
            or staged_response.predecessor_barriers != predecessors
        ):
            raise Ns3ProviderError("ns-3 staged response input binding mismatch")
        raw_receipt = self._parse_stage_receipt(
            staged_response.receipt, canonical_request
        )
        deliveries, delivered_message_ids = self._validate_deliveries(
            staged_response.deliveries, canonical_request.target
        )
        delivery_events = tuple(
            self._delivery_event(delivery, canonical_request.target)
            for delivery in deliveries
        )
        try:
            receipt = StepReceipt.model_validate(
                raw_receipt.model_copy(
                    update={"events": raw_receipt.events + delivery_events}
                ).model_dump(mode="json")
            )
        except ValidationError as exc:
            raise Ns3ProviderError("ns-3 staged receipt is not canonical") from exc
        contribution = self._empty_network_contribution(canonical_request)
        receipt_digest = step_receipt_digest_value(receipt)
        result = NetworkStageResult(
            schema_version="aero-bench.provider-stage-result/v1",
            run_id=canonical_request.run_id,
            scenario_digest=canonical_request.scenario_digest,
            provider_id=self._config.provider_id,
            target=canonical_request.target,
            stage="network",
            step_receipt=receipt,
            step_receipt_digest=receipt_digest,
            contribution=contribution,
            input_scene_state_digest=canonical_request.scene_state_digest,
            predecessor_barriers=predecessors,
        )
        self._record_mailbox_batches(
            at=canonical_request.target,
            stage="network",
            input_scene_state_digest=canonical_request.scene_state_digest,
            motion_barrier_digest=predecessors[0].barrier_digest,
            network_step_receipt_digest=receipt_digest,
            deliveries=deliveries,
        )
        self._last_time = receipt.reached
        self._delivered_message_ids.update(delivered_message_ids)
        return result

    async def _submit_message(
        self, binding: NetworkCommandBinding
    ) -> NetworkSubmission:
        self._require_prepared()
        if self._last_time is None:
            raise Ns3ProviderNotReady("ns-3 provider has not been reset")
        message = binding.message
        if message.send_time != self._last_time:
            raise Ns3ProviderError(
                "network message send time must equal the current provider barrier"
            )
        response = await self._request(
            "submit_message",
            {
                "message": message.model_dump(mode="json"),
                "work_order_id": binding.work_order_id,
            },
        )
        try:
            submission = NetworkSubmission.model_validate(response.get("submission"))
        except ValidationError as exc:
            raise Ns3ProviderError(
                "ns-3 message response does not contain a valid queued submission"
            ) from exc
        if (
            submission.run_id != self._run_id
            or submission.work_order_id != binding.work_order_id
            or submission.message_id != message.message_id
            or submission.source != message.source
            or submission.destination != message.destination
            or submission.payload_sha256 != message.payload_sha256
            or submission.send_time != message.send_time
        ):
            raise Ns3ProviderError(
                "ns-3 submission does not identify the submitted message"
            )
        return submission

    def _validate_deliveries(
        self, raw: object, target: SimulationTime
    ) -> tuple[tuple[DeliveredPayload, ...], set[str]]:
        if not isinstance(raw, (list, tuple)):
            raise Ns3ProviderError("ns-3 delivery response must contain a JSON array")
        try:
            deliveries = tuple(DeliveredPayload.model_validate(item) for item in raw)
        except ValidationError as exc:
            raise Ns3ProviderError(
                "ns-3 delivery response contains invalid evidence"
            ) from exc
        message_ids: set[str] = set()
        ordered_message_ids = tuple(
            delivery.trace.message_id for delivery in deliveries
        )
        if ordered_message_ids != tuple(sorted(ordered_message_ids)):
            raise Ns3ProviderError(
                "ns-3 delivery evidence must be ordered by message_id"
            )
        for delivery in deliveries:
            trace = delivery.trace
            binding = self._message_bindings.get(trace.message_id)
            if binding is None:
                raise Ns3ProviderError("ns-3 released an unsubmitted message")
            if (
                trace.message_id in message_ids
                or trace.message_id in self._delivered_message_ids
            ):
                raise Ns3ProviderError("ns-3 released duplicate delivery evidence")
            if (
                delivery.work_order_id != binding.work_order_id
                or trace.run_id != self._run_id
                or trace.source != binding.message.source
                or trace.destination != binding.message.destination
                or trace.payload_sha256 != binding.message.payload_sha256
                or trace.send_time != binding.message.send_time
                or delivery.payload_base64 != binding.message.payload_base64
            ):
                raise Ns3ProviderError(
                    "ns-3 delivery identity changed after submission"
                )
            if (
                trace.arrival_time is None
                or trace.arrival_time.tick > target.tick
                or trace.arrival_time.sim_time_ns > target.sim_time_ns
            ):
                raise Ns3ProviderError(
                    "ns-3 released a delivery before its arrival event"
                )
            message_ids.add(trace.message_id)
        return deliveries, message_ids

    async def observe(
        self,
        *,
        run_id: str,
        agent_id: str,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        self._require_prepared()
        if self._last_time is None:
            raise Ns3ProviderNotReady("ns-3 provider has not been reset")
        if run_id != self._run_id or requested_at != self._last_time:
            raise Ns3ProviderError("network mailbox observation identity or time mismatch")
        binding = self._mailbox_bindings.get((agent_id, observation_id))
        if binding is None:
            raise Ns3ProviderError(
                "network mailbox observation is not authorized for this Agent"
            )
        batch = self._mailbox_batches.get(
            self._mailbox_key(binding, requested_at)
        )
        if batch is None:
            raise Ns3ProviderError(
                "network mailbox is unavailable before its barrier is closed"
            )
        payload_document = batch.model_dump(mode="json")
        payload = tuple(
            NamedValue(name=name, value=value)
            for name, value in sorted(payload_document.items())
        )
        payload_digest = hashlib.sha256(
            canonical_json_bytes(payload_document)
        ).hexdigest()
        return ObservationEnvelope(
            run_id=self._run_id,
            agent_id=agent_id,
            observation_id=observation_id,
            time=requested_at,
            payload_schema=binding.schema_file,
            payload=payload,
            payload_digest=payload_digest,
        )

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        self._require_prepared()
        if self._last_time is None:
            raise Ns3ProviderNotReady("ns-3 provider has not been reset")
        if request.run_id != self._run_id:
            raise Ns3ProviderError("network.send request belongs to another run")
        if request.tool_id != "network.send":
            raise Ns3ProviderError(
                f"ns-3 provider does not own tool {request.tool_id!r}"
            )
        if request.issued_at != self._last_time:
            raise Ns3ProviderError(
                "network.send issued_at must equal the current provider barrier"
            )
        if request.command_id in self._command_ids:
            raise Ns3ProviderError("network.send command_id was already applied")
        raw_arguments: dict[str, object] = {}
        for item in request.arguments:
            if item.name in raw_arguments:
                raise Ns3ProviderError("network.send argument names must be unique")
            raw_arguments[item.name] = item.value
        try:
            arguments = NetworkSendArguments.model_validate(raw_arguments)
            message = arguments.message_at(request.issued_at)
        except ValidationError as error:
            raise Ns3ProviderError("network.send arguments are invalid") from error
        authorized_sources = self._agent_mailbox_endpoints.get(request.agent_id)
        if not authorized_sources or message.source not in authorized_sources:
            raise Ns3ProviderError(
                "network.send source is not authorized for this Agent"
            )
        if message.destination not in self._network_endpoint_ids:
            raise Ns3ProviderError(
                "network.send destination is not a declared network endpoint"
            )
        if arguments.message_id in self._message_bindings:
            raise Ns3ProviderError("network.send message_id was already submitted")

        binding = NetworkCommandBinding(
            command_id=request.command_id,
            agent_id=request.agent_id,
            work_order_id=arguments.work_order_id,
            message=message,
        )
        await self._submit_message(binding)
        self._message_bindings[message.message_id] = binding
        self._command_ids.add(request.command_id)
        receipts = tuple(
            CommandReceipt(
                run_id=self._run_id,
                command_id=request.command_id,
                provider_id=self._config.provider_id,
                phase=phase,
                time=request.issued_at,
                detail=detail,
            )
            for phase, detail in (
                ("received", "network.send received"),
                ("accepted", "network message contract accepted"),
                ("applied", "network message queued in ns-3"),
                ("completed", "queue acceptance recorded; delivery is pending"),
            )
        )
        return ProviderCommandResult(receipts=receipts)

    def _delivery_event(
        self, delivery: DeliveredPayload, stage_target: SimulationTime
    ) -> ProviderEvent:
        trace = delivery.trace
        binding = self._message_bindings[trace.message_id]
        if trace.arrival_time is None:  # guarded by staged delivery validation
            raise Ns3ProviderError("delivered network trace has no arrival time")
        source_artifact_id = self._manifest.artifact_requirements[0].artifact_id
        return ProviderEvent(
            provider_id=self._config.provider_id,
            event_id=self._DELIVERY_EVENT,
            time=stage_target,
            payload_schema_id=self._DELIVERY_EVENT_SCHEMA,
            payload=(
                NamedValue(name="schema_id", value=self._DELIVERY_EVENT_SCHEMA),
                NamedValue(name="run_id", value=self._run_id),
                NamedValue(name="provider_id", value=self._config.provider_id),
                NamedValue(name="command_id", value=binding.command_id),
                NamedValue(name="agent_id", value=binding.agent_id),
                NamedValue(name="source_artifact_id", value=source_artifact_id),
                NamedValue(name="work_order_id", value=binding.work_order_id),
                NamedValue(name="message_id", value=trace.message_id),
                NamedValue(name="payload_digest", value=trace.payload_sha256),
                NamedValue(name="sent_tick", value=trace.send_time.tick),
                NamedValue(name="sent_time_ns", value=trace.send_time.sim_time_ns),
                NamedValue(name="delivered_tick", value=trace.arrival_time.tick),
                NamedValue(
                    name="delivered_time_ns",
                    value=trace.arrival_time.sim_time_ns,
                ),
            ),
        )

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt:
        self._require_prepared()
        if request.run_id != self._run_id or request.terminal_time != self._last_time:
            raise Ns3ProviderError("ns-3 finalization identity or time mismatch")
        response = await self._request(
            "finalize",
            {
                "provider_id": self._config.provider_id,
                "request": request.model_dump(mode="json"),
            },
        )
        try:
            receipt = ProviderFinalizationReceipt.model_validate(
                response.get("receipt")
            )
        except ValidationError as exc:
            raise Ns3ProviderError("ns-3 finalization response is invalid") from exc
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
            raise Ns3ProviderError("ns-3 finalization receipt identity is invalid")
        return receipt

    async def snapshot_digest(self) -> str:
        self._require_prepared()
        response = await self._request(
            "snapshot", {"provider_id": self._config.provider_id}
        )
        try:
            snapshot = Ns3SnapshotResponse.model_validate(response)
        except ValidationError as exc:
            raise Ns3ProviderError(
                "ns-3 snapshot response has no valid digest"
            ) from exc
        return TypeAdapter(Sha256).validate_python(snapshot.snapshot_digest)

    async def shutdown(self) -> None:
        if self._transport is None:
            return
        try:
            if self._prepared:
                response = await self._request(
                    "shutdown", {"provider_id": self._config.provider_id}
                )
                if response.get("status") != "stopped":
                    raise Ns3ProviderError("ns-3 provider did not acknowledge shutdown")
        finally:
            await self._transport.close()
            self._prepared = False
            self._transport = None
