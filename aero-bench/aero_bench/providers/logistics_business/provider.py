from __future__ import annotations

import json

import hashlib
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter, ValidationError

from aero_bench.config.models import NamedValue, Sha256, StrictModel
from aero_bench.gateway.contracts import ProviderQueryResult, QueryRequest
from aero_bench.providers.contracts import (
    ProviderCommandResult,
    ProviderManifest,
    ProviderSession,
)
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.protocol import (
    LOGISTICS_ORDER_CREATE_TOOL,
    PROTOCOL_VERSION,
    LogisticsBusinessTransport,
    connect_runtime,
)
from aero_bench.providers.logistics_business.queries import (
    LOGISTICS_QUERY_FACILITIES,
    LOGISTICS_QUERY_ORDER_DETAIL,
    LOGISTICS_QUERY_ORDERS,
    LogisticsBusinessQuery,
    LogisticsBusinessQueryResult,
    LogisticsOrderView,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    BusinessEnvironmentStageResult,
    CommandReceipt,
    CommandRequest,
    FinalizedArtifact,
    ProviderEvent,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    SceneState,
    SimulationTime,
    StepReceipt,
    scene_state_digest_value,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_INGEST_OPERATION,
    LogisticsObservationBatch,
    ObservationIngestResult,
    ObservationIngressError,
    ObservationJournal,
    append_observations_batch,
    empty_observation_journal,
    observation_ingest_result,
)
from aero_bench.tasks.logistics.order_arrivals import (
    ORDER_INTRODUCING_ROLE,
    OrderArrivalEvent,
    validate_order_for_arrival,
)
from aero_bench.tasks.logistics.orders import (
    LogisticsHistoryRecord,
    OrderRequest,
    replay_logistics_history,
)
from aero_bench.world.resolved import ResolvedScenario


STATE_SCHEMA = "logistics.business.state.v1"
TRANSITION_EVENT_SCHEMA = "logistics.business.transition.v1"
ORDER_CREATED_EVENT_SCHEMA = "logistics.business.order-created.v1"

#: The exact canonical authenticated online-order creation command surface.
#: Mirrors the workload's REQUIRED_ARGUMENTS["create"] so the client never
#: accepts an arrival record for a create command that named a different actor
#: or a different canonical OrderRequest than the one actually accepted.
_ORDER_CREATE_ARGUMENTS: frozenset[str] = frozenset(
    {
        "actor_id",
        "order_id",
        "origin_facility_id",
        "destination_facility_id",
        "hub_handoff_facility_id",
        "cargo_mass_kg",
        "release_time_s",
        "deadline_s",
    }
)


def _is_numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class LogisticsBusinessProviderError(RuntimeError):
    pass


class LogisticsBusinessProviderNotReady(LogisticsBusinessProviderError):
    pass


class LogisticsBusinessReadiness(StrictModel):
    status: str
    provider_id: str
    protocol_version: str
    runtime_image: str
    config_digest: Sha256


class LogisticsBusinessSnapshotResponse(StrictModel):
    snapshot_digest: Sha256


def pinned_package_digest(task_package: Mapping[str, Any]) -> str:
    """Digest binding a session to one canonical task package on the wire."""

    return hashlib.sha256(canonical_json_bytes(dict(task_package))).hexdigest()


def _require_state_event(
    events: tuple[ProviderEvent, ...],
    *,
    provider_id: str,
    expected_time: SimulationTime,
) -> ProviderEvent:
    state_events = tuple(
        event for event in events if event.payload_schema_id == STATE_SCHEMA
    )
    if len(state_events) != 1:
        raise LogisticsBusinessProviderError(
            "logistics business response must contain exactly one "
            "logistics.business.state.v1 event"
        )
    state_event = state_events[0]
    if (
        state_event.provider_id != provider_id
        or state_event.time != expected_time
    ):
        raise LogisticsBusinessProviderError(
            "logistics business state event is not bound to the provider/time"
        )
    return state_event


class LogisticsBusinessProvider(ProviderSession):
    """Client for the real deterministic Logistics Business provider workload.

    The provider workload owns the canonical order ledger (``LogisticsOrdersService``),
    the facility-catalogue state, the staged business-environment receipts, and
    the append-only hash-chained history. Each receipt, query result and
    snapshot digest comes from the JSON-line provider service; this process
    contains no business-state generator.

    Commands carry the Harness authoritative ``SimulationTime`` and never
    advance the clock. Only non-physical order transitions (offer/accept/
    reject/assign/cancel/fail) and the authenticated online-order creation tool
    (``logistics.order.create``) are forwarded; physical pickup/handoff/deliver
    transitions are refused by the workload with an explicit not-yet-supported
    result until the authoritative physical-evidence interface is wired.

    The session is the only legitimate RPC client: it presents the executor-
    issued run-scoped session token on every frame including prepare.
    """

    def __init__(
        self,
        *,
        config: LogisticsBusinessConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        session_token: str,
        scenario: ResolvedScenario,
    ):
        if manifest.provider_id != config.provider_id:
            raise ValueError(
                "logistics business manifest/provider configuration IDs differ"
            )
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise TypeError(
                "logistics business runtime_endpoint must be a RuntimeEndpoint"
            )
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("logistics business scenario must be a ResolvedScenario")
        if config.task_package.scene.scene_id != scenario.world_id:
            raise ValueError(
                "logistics task package is not bound to the resolved scene"
            )
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError("logistics business run_id must be a SHA-256 digest") from error
        if validated_run_id == "0" * 64:
            raise ValueError("logistics business run_id cannot be a placeholder digest")
        try:
            validated_token = TypeAdapter(Sha256).validate_python(session_token)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "logistics business session_token must be a SHA-256 digest"
            ) from error
        if validated_token == "0" * 64:
            raise ValueError(
                "logistics business session_token cannot be a placeholder digest"
            )
        self._config = config
        self._manifest = manifest
        self._runtime_endpoint = runtime_endpoint
        self._run_id = validated_run_id
        self._scenario = scenario
        self._session_token = validated_token
        self._transport: LogisticsBusinessTransport | None = None
        self._prepared = False
        self._last_time: SimulationTime | None = None
        self._scenario_digest: str | None = None
        self._accepted_stage_input_count = 0
        self._applied_schedule_ids: set[str] = set()
        self._history: tuple[LogisticsHistoryRecord, ...] = ()
        # The live order pool the workload owns: initial package requests plus
        # authenticated online-created requests in causal arrival order. The
        # client replays lifecycle history against this pool (a forged history
        # that names an order never introduced is rejected).
        self._orders_pool: tuple[OrderRequest, ...] = config.task_package.orders
        # Observation-ingest acknowledgment tracking.  The client binds every
        # acknowledgment to the exact batch it submitted: the client keeps the
        # confirmed immutable ``ObservationJournal`` and computes the canonical
        # expected continuation from that journal and the submitted batch
        # (records, digest, sequence, replayed) with the accepted append kernel.
        # An acknowledgment that is not byte-for-byte the kernel expectation — a
        # wrong record, an omission, an inconsistent sequence, an opaque or
        # arbitrary changed journal digest, a recomputed-hash forgery or a false
        # replay — is rejected BEFORE the runtime hook can write an ingested
        # event.  Reset clears the confirmed journal exactly as the workload
        # reset does, so a later honest ingest starts from the empty journal.
        self._observation_journal: ObservationJournal | None = None

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    async def _request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._transport is None:
            self._transport = await connect_runtime(self._runtime_endpoint)
        return await self._transport.request(
            operation, {**dict(payload), "session_token": self._session_token}
        )

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise LogisticsBusinessProviderNotReady(
                "logistics business provider has not completed readiness"
            )

    def _config_payload(self) -> dict[str, Any]:
        task_package = self._config.task_package.model_dump(mode="json")
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
            "task_id": self._config.task_package.task_id,
            "package_digest": pinned_package_digest(task_package),
            "task_package": task_package,
            "capabilities": list(self._manifest.capabilities),
        }

    async def prepare(self) -> None:
        if self._prepared:
            raise LogisticsBusinessProviderError(
                "logistics business provider prepare called twice"
            )
        response = await self._request("prepare", self._config_payload())
        try:
            readiness = LogisticsBusinessReadiness.model_validate(response)
        except ValidationError as exc:
            raise LogisticsBusinessProviderError(
                "logistics business provider readiness response is invalid"
            ) from exc
        if readiness.status != "ready":
            raise LogisticsBusinessProviderError(
                f"logistics business provider did not become ready: {readiness.status!r}"
            )
        if readiness.provider_id != self._config.provider_id:
            raise LogisticsBusinessProviderError(
                "logistics business readiness provider_id differs from declaration"
            )
        if readiness.protocol_version != PROTOCOL_VERSION:
            raise LogisticsBusinessProviderError(
                "logistics business provider protocol version mismatch"
            )
        if readiness.runtime_image != self._manifest.runtime_image:
            raise LogisticsBusinessProviderError(
                "logistics business provider image identity mismatch"
            )
        if readiness.config_digest != self._manifest.config_digest:
            raise LogisticsBusinessProviderError(
                "logistics business provider config digest mismatch"
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
            raise LogisticsBusinessProviderError(
                f"logistics business {operation} response does not contain a "
                "valid StepReceipt"
            ) from exc
        if receipt.run_id != self._run_id:
            raise LogisticsBusinessProviderError(
                "logistics business StepReceipt run identity mismatch"
            )
        if receipt.provider_id != self._config.provider_id:
            raise LogisticsBusinessProviderError(
                "logistics business StepReceipt provider identity mismatch"
            )
        if receipt.reached != target:
            raise LogisticsBusinessProviderError(
                "logistics business backend did not reach the requested time"
            )
        _require_state_event(
            receipt.events,
            provider_id=self._config.provider_id,
            expected_time=target,
        )
        return receipt

    async def reset(self, *, seed: int) -> StepReceipt:
        self._require_prepared()
        response = await self._request(
            "reset",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "seed": seed,
            },
        )
        target = SimulationTime(tick=0, sim_time_ns=0)
        receipt = self._parse_receipt(response, target=target, operation="reset")
        self._last_time = target
        self._accepted_stage_input_count = 0
        self._applied_schedule_ids = set()
        self._history = ()
        self._orders_pool = self._config.task_package.orders
        # Reset clears the confirmed observation journal exactly as the
        # workload reset does; no acknowledged batch survives a reset.
        self._observation_journal = empty_observation_journal(
            run_id=self._run_id,
            provider_id=self._config.provider_id,
        )
        return receipt

    def _validate_stage_request(
        self, request: BusinessEnvironmentStageRequest
    ) -> BusinessEnvironmentStageRequest:
        if not isinstance(request, BusinessEnvironmentStageRequest):
            raise LogisticsBusinessProviderError(
                "logistics business requires a BusinessEnvironmentStageRequest"
            )
        try:
            canonical = BusinessEnvironmentStageRequest.model_validate(
                request.model_dump(mode="json")
            )
            canonical_scene_state = SceneState.model_validate(
                request.scene_state.model_dump(mode="json")
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise LogisticsBusinessProviderError(
                "logistics business stage request fails strict revalidation"
            ) from exc
        if canonical != request or canonical_scene_state != request.scene_state:
            raise LogisticsBusinessProviderError(
                "logistics business stage request is not canonically serialized"
            )
        if request.run_id != self._run_id:
            raise LogisticsBusinessProviderError(
                "logistics business stage request belongs to another run"
            )
        if request.provider_id != self._config.provider_id:
            raise LogisticsBusinessProviderError(
                "logistics business stage request provider identity mismatch"
            )
        if (
            canonical_scene_state.run_id != request.run_id
            or canonical_scene_state.scenario_digest != request.scenario_digest
            or canonical_scene_state.at != request.target
        ):
            raise LogisticsBusinessProviderError(
                "logistics business SceneState binding is inconsistent"
            )
        computed_scene_state_digest = scene_state_digest_value(canonical_scene_state)
        if (
            canonical_scene_state.scene_state_digest != computed_scene_state_digest
            or request.scene_state_digest != computed_scene_state_digest
        ):
            raise LogisticsBusinessProviderError(
                "logistics business SceneState digest does not match its content"
            )
        predecessor_stages = tuple(
            predecessor.stage for predecessor in request.predecessor_barriers
        )
        if predecessor_stages not in {("motion",), ("motion", "network")}:
            raise LogisticsBusinessProviderError(
                "logistics business predecessors must be motion or motion then network"
            )
        motion_barrier = request.predecessor_barriers[0]
        if (
            motion_barrier.stage != "motion"
            or motion_barrier.barrier_digest
            != canonical_scene_state.stage_barrier.barrier_digest
        ):
            raise LogisticsBusinessProviderError(
                "logistics business motion predecessor does not bind the SceneState"
            )
        if request.scenario_digest != self._scenario.scenario_digest:
            raise LogisticsBusinessProviderError(
                "logistics business stage request scenario digest differs from "
                "the resolved scenario"
            )
        if self._scenario_digest is not None and (
            request.scenario_digest != self._scenario_digest
        ):
            raise LogisticsBusinessProviderError(
                "logistics business stage request scenario digest changed mid-session"
            )
        if self._last_time is not None and (
            request.target.tick <= self._last_time.tick
            or request.target.sim_time_ns <= self._last_time.sim_time_ns
        ):
            raise LogisticsBusinessProviderError(
                "logistics business stage target must advance monotonically"
            )
        return canonical

    @staticmethod
    def _predecessor_payload(
        request: BusinessEnvironmentStageRequest,
    ) -> str:
        return canonical_json_bytes(
            [
                predecessor.model_dump(mode="json")
                for predecessor in request.predecessor_barriers
            ]
        ).decode("utf-8")

    def _validate_stage_state_event(
        self,
        receipt: StepReceipt,
        request: BusinessEnvironmentStageRequest,
    ) -> None:
        state_event = _require_state_event(
            receipt.events,
            provider_id=self._config.provider_id,
            expected_time=request.target,
        )
        fields = {item.name: item.value for item in state_event.payload}
        if len(fields) != len(state_event.payload):
            raise LogisticsBusinessProviderError(
                "logistics business state event has duplicate payload names"
            )
        expected = {
            "state_digest": receipt.state_digest,
            "stage_input_scenario_digest": request.scenario_digest,
            "stage_input_scene_state_digest": request.scene_state_digest,
            "stage_input_motion_barrier_digest": request.predecessor_barriers[
                0
            ].barrier_digest,
            "stage_input_predecessor_barriers": self._predecessor_payload(request),
            "stage_input_count": self._accepted_stage_input_count + 1,
            "current_time_ns": request.target.sim_time_ns,
        }
        if any(fields.get(name) != value for name, value in expected.items()):
            raise LogisticsBusinessProviderError(
                "logistics business authoritative state event is not bound to the "
                "accepted staged input"
            )

    def _parse_stage_result(
        self,
        response: Mapping[str, Any],
        *,
        request: BusinessEnvironmentStageRequest,
    ) -> BusinessEnvironmentStageResult:
        if set(response) != {"result"}:
            raise LogisticsBusinessProviderError(
                "logistics business staged response must contain exactly result"
            )
        raw_result = response["result"]
        if not isinstance(raw_result, Mapping):
            raise LogisticsBusinessProviderError(
                "logistics business staged response result must be a JSON object"
            )
        try:
            result = BusinessEnvironmentStageResult.model_validate(raw_result)
            canonical = BusinessEnvironmentStageResult.model_validate(
                result.model_dump(mode="json")
            )
        except (TypeError, ValueError) as exc:
            raise LogisticsBusinessProviderError(
                "logistics business staged response is invalid"
            ) from exc
        if (
            canonical != result
            or canonical_json_bytes(result.model_dump(mode="json"))
            != canonical_json_bytes(dict(raw_result))
        ):
            raise LogisticsBusinessProviderError(
                "logistics business staged response is not canonical"
            )
        if (
            result.run_id != request.run_id
            or result.scenario_digest != request.scenario_digest
            or result.provider_id != request.provider_id
            or result.target != request.target
            or result.input_scene_state_digest != request.scene_state_digest
            or result.predecessor_barriers != request.predecessor_barriers
        ):
            raise LogisticsBusinessProviderError(
                "logistics business staged response binding is inconsistent"
            )
        receipt = self._parse_receipt(
            {"receipt": result.step_receipt.model_dump(mode="json")},
            target=request.target,
            operation="step_stage",
        )
        if (
            receipt != result.step_receipt
            or result.step_receipt_digest != step_receipt_digest_value(receipt)
        ):
            raise LogisticsBusinessProviderError(
                "logistics business staged StepReceipt digest is inconsistent"
            )
        if result.contribution.samples or result.contribution.attribute_updates:
            raise LogisticsBusinessProviderError(
                "logistics business has no declared SceneState entity ownership"
            )
        self._validate_stage_state_event(receipt, request)
        self._validate_scheduled_events(receipt, request)
        return canonical

    def _validate_scheduled_events(self, receipt: StepReceipt, request: BusinessEnvironmentStageRequest) -> None:
        from aero_bench.providers.logistics_business.scheduled_arrivals import (
            SCHEDULED_ARRIVAL_EVENT_SCHEMA, ScheduledOrderApplication,
        )
        expected = tuple(item for item in self._config.scheduled_orders if item.at_tick == request.target.tick)
        if any(item.at_tick < request.target.tick and item.event_id not in self._applied_schedule_ids for item in self._config.scheduled_orders):
            raise LogisticsBusinessProviderError("Business stage skipped a scheduled creation tick")
        events = tuple(item for item in receipt.events if item.payload_schema_id == SCHEDULED_ARRIVAL_EVENT_SCHEMA)
        if len(events) != len(expected):
            raise LogisticsBusinessProviderError("scheduled creation receipt coverage differs from the pinned schedule")
        for event, scheduled in zip(events, expected, strict=True):
            if scheduled.event_id in self._applied_schedule_ids or len(event.payload) != 1 or event.payload[0].name != "application_json":
                raise LogisticsBusinessProviderError("duplicate or malformed scheduled creation receipt")
            try:
                raw = json.loads(event.payload[0].value)
                application = ScheduledOrderApplication.model_validate(raw)
            except (TypeError, ValueError) as exc:
                raise LogisticsBusinessProviderError("invalid scheduled creation application") from exc
            if (application.requested != scheduled or application.applied_at != request.target
                or application.motion_barrier_digest != request.predecessor_barriers[0].barrier_digest
                or application.scene_state_digest != request.scene_state_digest
                or application.arrival.kind != "created" or application.arrival.event_id != scheduled.event_id
                or application.arrival.order != scheduled.order or application.arrival.time != request.target
                or application.arrival.actor_id != scheduled.actor_id
                or application.arrival.source_identity != f"provider:{self._config.provider_id}:scheduled"
                or event.event_id != f"scheduled.{scheduled.event_id}"
                or canonical_json_bytes(raw).decode("utf-8") != event.payload[0].value):
                raise LogisticsBusinessProviderError("scheduled creation application disagrees with the pinned request/barrier")
        self._applied_schedule_ids.update(item.event_id for item in expected)
        self._orders_pool += tuple(item.order for item in expected)

    async def step_stage(
        self, request: BusinessEnvironmentStageRequest
    ) -> BusinessEnvironmentStageResult:
        self._require_prepared()
        canonical_request = self._validate_stage_request(request)
        result = self._parse_stage_result(
            await self._request(
                "step_stage",
                {
                    "provider_id": self._config.provider_id,
                    "run_id": self._run_id,
                    "protocol_version": PROTOCOL_VERSION,
                    "request": canonical_request.model_dump(mode="json"),
                },
            ),
            request=canonical_request,
        )
        self._last_time = result.step_receipt.reached
        if self._scenario_digest is None:
            self._scenario_digest = canonical_request.scenario_digest
        self._accepted_stage_input_count += 1
        return result

    @staticmethod
    def _validate_command_receipts(
        receipts: tuple[CommandReceipt, ...],
        request: CommandRequest,
        provider_id: str,
    ) -> None:
        if not receipts:
            raise LogisticsBusinessProviderError(
                "logistics business command returned no command receipts"
            )
        for receipt in receipts:
            if receipt.run_id != request.run_id:
                raise LogisticsBusinessProviderError(
                    "logistics business command receipt belongs to another run"
                )
            if receipt.command_id != request.command_id:
                raise LogisticsBusinessProviderError(
                    "logistics business command receipt has the wrong command_id"
                )
            if receipt.provider_id != provider_id:
                raise LogisticsBusinessProviderError(
                    "logistics business command receipt has the wrong provider_id"
                )
            if receipt.time != request.issued_at:
                raise LogisticsBusinessProviderError(
                    "logistics business command receipt time must equal the "
                    "authoritative simulation time"
                )
        phases = tuple(receipt.phase for receipt in receipts)
        allowed = {
            ("received", "failed"),
            ("received", "accepted", "failed"),
            ("received", "accepted", "applied", "failed"),
            ("received", "accepted", "applied", "completed"),
        }
        if tuple(phases) not in allowed:
            raise LogisticsBusinessProviderError(
                "logistics business command phases must separately record "
                "received, accepted, applied, and completed (or an ordered failure)"
            )

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        self._require_prepared()
        if request.run_id != self._run_id:
            raise LogisticsBusinessProviderError(
                "logistics business command request belongs to another run"
            )
        if self._last_time is None or request.issued_at != self._last_time:
            raise LogisticsBusinessProviderError(
                "logistics business command time is not authoritative"
            )
        response = await self._request(
            "command",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": request.model_dump(mode="json"),
            },
        )
        raw_receipts = response.get("receipts")
        if not isinstance(raw_receipts, list):
            raise LogisticsBusinessProviderError(
                "logistics business command response receipts must be a JSON array"
            )
        try:
            receipts = tuple(
                CommandReceipt.model_validate(item) for item in raw_receipts
            )
        except ValidationError as exc:
            raise LogisticsBusinessProviderError(
                "logistics business command response contains an invalid receipt"
            ) from exc
        self._validate_command_receipts(receipts, request, self._config.provider_id)

        completed = receipts[-1].phase == "completed"
        if request.tool_id == LOGISTICS_ORDER_CREATE_TOOL:
            expected_fields = {"receipts"}
            if completed:
                expected_fields.add("arrival_record")
            if set(response) != expected_fields:
                raise LogisticsBusinessProviderError(
                    "logistics business create command response fields do not "
                    "match its outcome"
                )
            if not completed:
                return ProviderCommandResult(receipts=receipts)
            return self._handle_order_created(receipts, request, response)

        expected_fields = {"receipts"}
        if completed:
            expected_fields.add("history_record")
        if set(response) != expected_fields:
            raise LogisticsBusinessProviderError(
                "logistics business command response fields do not match its outcome"
            )
        if not completed:
            return ProviderCommandResult(receipts=receipts)

        try:
            record = LogisticsHistoryRecord.model_validate(response["history_record"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LogisticsBusinessProviderError(
                "logistics business command response has no valid history record"
            ) from exc
        if record.transition.transition_id != request.command_id:
            raise LogisticsBusinessProviderError(
                "logistics business history record belongs to another command"
            )
        candidate_history = (*self._history, record)
        # Replay the appended history against the full live order pool (initial
        # package requests plus authenticated online creations) with canonical
        # actor grants to prove the on-wire record is a legal continuation (no
        # provider-side generation and no order that was never introduced).
        try:
            replay_logistics_history(
                self._orders_pool,
                actor_grants=self._config.task_package.actor_grants,
                history=candidate_history,
            )
        except ValueError as exc:
            raise LogisticsBusinessProviderError(
                "logistics business command history chain is invalid"
            ) from exc
        self._history = candidate_history

        event = ProviderEvent(
            provider_id=self._config.provider_id,
            event_id="logistics.transition",
            time=request.issued_at,
            payload_schema_id=TRANSITION_EVENT_SCHEMA,
            payload=(
                NamedValue(
                    name="logistics_history_record_hash",
                    value=record.record_hash,
                ),
                NamedValue(name="logistics_history_sequence", value=record.sequence),
                NamedValue(name="command_id", value=request.command_id),
                NamedValue(name="event", value=record.transition.event),
                NamedValue(
                    name="order_id",
                    value=record.transition.order_id,
                ),
            ),
        )
        return ProviderCommandResult(receipts=receipts, events=(event,))

    def _handle_order_created(
        self,
        receipts: tuple[CommandReceipt, ...],
        request: CommandRequest,
        response: Mapping[str, Any],
    ) -> ProviderCommandResult:
        """Validate one authenticated online-order creation and record it.

        The on-wire ``arrival_record`` is the accepted causal arrival journal
        event produced by the workload: it must be a ``created`` event whose id
        equals the command id, whose native time equals the authoritative
        simulation time, and whose source identity is bound to the attested
        native principal (the Gateway's ``agent_id``) -- never a client-claimed
        principal. The introducing actor must hold an explicit canonical
        ``business`` grant and must be reachable through the authenticated actor
        binding, and the embedded OrderRequest must pass the canonical
        catalogue/hub/payload validation. The order is then appended to the live
        pool so later lifecycle replay always has the order present.
        """
        try:
            arrival_record = OrderArrivalEvent.model_validate(
                response["arrival_record"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LogisticsBusinessProviderError(
                "logistics business create command response has no valid "
                "arrival record"
            ) from exc
        if arrival_record.kind != "created":
            raise LogisticsBusinessProviderError(
                "logistics business create command did not produce a creation "
                "arrival record"
            )
        if arrival_record.event_id != request.command_id:
            raise LogisticsBusinessProviderError(
                "logistics business arrival record belongs to another command"
            )
        if arrival_record.time != request.issued_at:
            raise LogisticsBusinessProviderError(
                "logistics business arrival record time is not the authoritative "
                "simulation time"
            )
        if arrival_record.source_identity != request.agent_id:
            raise LogisticsBusinessProviderError(
                "logistics business arrival record source identity is not bound "
                "to the authenticated actor"
            )
        if arrival_record.order is None:
            raise LogisticsBusinessProviderError(
                "logistics business arrival record must carry the full OrderRequest"
            )
        # Bind the accepted arrival record to the EXACT command surface: the
        # requested actor and every canonical OrderRequest field the session
        # actually issued must match the accepted record. A client that asks for
        # one order/actor may never be credited with a different order or with
        # an order introduced by another actor the same principal can control.
        self._validate_create_arrival_binding(request, arrival_record)
        binding = next(
            (
                item
                for item in self._config.principal_bindings
                if item.principal_id == request.agent_id
                and item.actor_id == arrival_record.actor_id
            ),
            None,
        )
        if binding is None:
            raise LogisticsBusinessProviderError(
                "logistics business arrival record actor is not bound to the "
                "authenticated principal"
            )
        if not any(
            grant.actor_id == arrival_record.actor_id
            and grant.role == ORDER_INTRODUCING_ROLE
            for grant in self._config.task_package.actor_grants
        ):
            raise LogisticsBusinessProviderError(
                "logistics business arrival record introducing actor does not "
                "hold the canonical business grant"
            )
        if any(
            order.order_id == arrival_record.order.order_id
            for order in self._orders_pool
        ):
            raise LogisticsBusinessProviderError(
                "logistics business command created a duplicate order id"
            )
        try:
            validate_order_for_arrival(
                self._config.task_package.facilities,
                self._config.task_package.fleet,
                arrival_record.order,
            )
        except (TypeError, ValueError) as exc:
            raise LogisticsBusinessProviderError(
                "logistics business arrival record order fails canonical "
                f"validation: {exc}"
            ) from exc
        self._orders_pool = (*self._orders_pool, arrival_record.order)

        assert arrival_record.actor_id is not None
        assert arrival_record.source_identity is not None
        event = ProviderEvent(
            provider_id=self._config.provider_id,
            event_id=arrival_record.event_id,
            time=request.issued_at,
            payload_schema_id=ORDER_CREATED_EVENT_SCHEMA,
            payload=(
                NamedValue(
                    name="arrival_record_hash",
                    value=arrival_record.record_hash,
                ),
                NamedValue(name="arrival_sequence", value=arrival_record.sequence),
                NamedValue(name="command_id", value=request.command_id),
                NamedValue(name="order_id", value=arrival_record.order_id),
                NamedValue(
                    name="source_identity",
                    value=arrival_record.source_identity,
                ),
                NamedValue(name="actor_id", value=arrival_record.actor_id),
            ),
        )
        return ProviderCommandResult(receipts=receipts, events=(event,))

    def _validate_create_arrival_binding(
        self,
        request: CommandRequest,
        arrival_record: OrderArrivalEvent,
    ) -> None:
        """Bind the accepted arrival record to the exact create command content.

        The provider trusts only the command surface the session actually
        issued: the requested ``actor_id`` and every canonical ``OrderRequest``
        field carried in ``request.arguments`` must match the corresponding
        accepted arrival record exactly.  A client can never submit a create
        command for one order/actor and have this provider accept an arrival
        record naming a different order, a different canonical OrderRequest, or
        a different actor the same native principal happens to control (e.g. one
        of several canonical actors bound to a single centralized principal).

        The check runs before any live pool/event mutation, so a rejected
        arrival leaves ``_orders_pool`` and the provider event surface untouched.
        """
        arguments: dict[str, Any] = {}
        for argument in request.arguments:
            if argument.name in arguments:
                raise LogisticsBusinessProviderError(
                    "logistics business create command arguments are not unique"
                )
            arguments[argument.name] = argument.value
        if set(arguments) != _ORDER_CREATE_ARGUMENTS:
            raise LogisticsBusinessProviderError(
                "logistics business create command does not carry the exact "
                "canonical create surface"
            )
        if arguments["actor_id"] != arrival_record.actor_id:
            raise LogisticsBusinessProviderError(
                "logistics business created order is not bound to the exact "
                "requested actor"
            )
        order = arrival_record.order
        if order is None:  # pragma: no cover - guarded by the caller
            raise LogisticsBusinessProviderError(
                "logistics business arrival record must carry the full OrderRequest"
            )
        identifier_fields = {
            "order_id": order.order_id,
            "origin_facility_id": order.origin_facility_id,
            "destination_facility_id": order.destination_facility_id,
            "hub_handoff_facility_id": order.hub_handoff_facility_id,
        }
        numeric_fields = {
            "cargo_mass_kg": order.cargo_mass_kg,
            "release_time_s": order.release_time_s,
            "deadline_s": order.deadline_s,
        }
        for field_name, expected_value in identifier_fields.items():
            actual_value = arguments[field_name]
            if not isinstance(actual_value, str) or actual_value != expected_value:
                raise LogisticsBusinessProviderError(
                    "logistics business created order does not match the "
                    f"command request for field {field_name!r}"
                )
        for field_name, expected_value in numeric_fields.items():
            actual_value = arguments[field_name]
            if not _is_numeric(actual_value) or actual_value != expected_value:
                raise LogisticsBusinessProviderError(
                    "logistics business created order does not match the "
                    f"command request for field {field_name!r}"
                )

    async def query_business(
        self, query: LogisticsBusinessQuery
    ) -> LogisticsBusinessQueryResult:
        self._require_prepared()
        if query.run_id != self._run_id:
            raise LogisticsBusinessProviderError(
                "logistics business query belongs to another run"
            )
        if self._last_time is None or query.issued_at != self._last_time:
            raise LogisticsBusinessProviderError(
                "logistics business query time is not authoritative"
            )
        response = await self._request(
            "query",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "query": query.model_dump(mode="json"),
            },
        )
        try:
            result = LogisticsBusinessQueryResult.model_validate(response.get("result"))
        except ValidationError as exc:
            raise LogisticsBusinessProviderError(
                "logistics business query response has no valid result"
            ) from exc
        if result.run_id != self._run_id or result.query_id != query.query_id:
            raise LogisticsBusinessProviderError(
                "logistics business query result identity mismatch"
            )
        if result.observed_at != query.issued_at:
            raise LogisticsBusinessProviderError(
                "logistics business query result time mismatch"
            )
        return result

    async def query(self, request: QueryRequest) -> ProviderQueryResult:
        self._require_prepared()
        if not isinstance(request, QueryRequest):
            raise LogisticsBusinessProviderError(
                "logistics business query request is invalid"
            )
        arguments = {item.name: item.value for item in request.arguments}
        if len(arguments) != len(request.arguments):
            raise LogisticsBusinessProviderError(
                "logistics business query arguments are not unique"
            )
        if request.query_type == LOGISTICS_QUERY_ORDERS:
            if arguments:
                raise LogisticsBusinessProviderError(
                    "logistics orders summary query does not accept arguments"
                )
            business_query = LogisticsBusinessQuery(
                run_id=request.run_id,
                query_id=request.query_id,
                kind="orders",
                issued_at=request.issued_at,
            )
        elif request.query_type == LOGISTICS_QUERY_ORDER_DETAIL:
            if set(arguments) != {"order_id"} or not isinstance(
                arguments["order_id"], str
            ):
                raise LogisticsBusinessProviderError(
                    "logistics order detail query requires order_id"
                )
            business_query = LogisticsBusinessQuery(
                run_id=request.run_id,
                query_id=request.query_id,
                kind="order",
                issued_at=request.issued_at,
                order_id=arguments["order_id"],
            )
        elif request.query_type == LOGISTICS_QUERY_FACILITIES:
            if arguments:
                raise LogisticsBusinessProviderError(
                    "logistics facilities summary query does not accept arguments"
                )
            business_query = LogisticsBusinessQuery(
                run_id=request.run_id,
                query_id=request.query_id,
                kind="facilities",
                issued_at=request.issued_at,
            )
        else:
            raise LogisticsBusinessProviderError(
                "logistics business query type is unsupported"
            )

        state_result = await self.query_business(business_query)
        if request.query_type == LOGISTICS_QUERY_ORDERS:
            rows = [view.model_dump(mode="json") for view in state_result.orders]
            payload = (
                NamedValue(name="order_count", value=len(rows)),
                NamedValue(
                    name="orders_json",
                    value=canonical_json_bytes(rows).decode("utf-8"),
                ),
            )
        elif request.query_type == LOGISTICS_QUERY_ORDER_DETAIL:
            if len(state_result.orders) != 1:
                raise LogisticsBusinessProviderError(
                    "logistics order detail query returned no authoritative order"
                )
            view = state_result.orders[0]
            payload = _order_detail_payload(view)
        else:
            rows = [view.model_dump(mode="json") for view in state_result.facilities]
            payload = (
                NamedValue(name="facility_count", value=len(rows)),
                NamedValue(
                    name="facilities_json",
                    value=canonical_json_bytes(rows).decode("utf-8"),
                ),
            )
        payload_document = {item.name: item.value for item in payload}
        return ProviderQueryResult(
            run_id=request.run_id,
            query_id=request.query_id,
            query_type=request.query_type,
            observed_at=request.issued_at,
            payload=payload,
            payload_digest=hashlib.sha256(
                canonical_json_bytes(payload_document)
            ).hexdigest(),
        )

    async def ingest_observations(
        self, batch: LogisticsObservationBatch
    ) -> ObservationIngestResult:
        """Private harness-to-business ingest of typed physical observations.

        This is the only legitimate observation-ingest path: the closed-motion
        runtime hook submits an immutable, fully typed
        :class:`LogisticsObservationBatch` (whose records were derived under
        explicit pose-reference calibrations) and the workload atomically
        journals it.  The session token authenticates every frame, exactly like
        prepare/stage/command; there is no Agent-facing tool and no arbitrary
        evidence string authority.

        The acknowledgment is bound to the exact submitted batch.  The client
        computes the *only* canonical continuation with the accepted immutable
        ``ObservationJournal`` kernel (``append_observations_batch`` then
        ``observation_ingest_result``) from its confirmed journal and the exact
        submitted batch, and the workload acknowledgment must equal that
        continuation exactly: the canonical journal digest, the accepted
        records (content, digest and 1-based sequence), the journal sequence
        count and the replay flag.  A wrong record, an omission, an
        inconsistent sequence, an opaque or arbitrary changed journal digest, a
        recomputed-hash forgery or a false replay is rejected BEFORE this call
        returns, so the runtime hook can never write an ``ingested`` ledger
        event from an acknowledgment that is not bound to what was actually
        submitted.  The confirmed journal is committed only after a validated
        real response; ``reset()`` clears it exactly as the workload reset does.
        """
        self._require_prepared()
        if not isinstance(batch, LogisticsObservationBatch):
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest requires a typed batch"
            )
        if batch.run_id != self._run_id:
            raise LogisticsBusinessProviderError(
                "logistics business observation batch belongs to another run"
            )
        if self._last_time is None or batch.at != self._last_time:
            raise LogisticsBusinessProviderError(
                "logistics business observation batch time is not the "
                "authoritative provider time"
            )
        submitted_digests = tuple(
            observation.observation_digest for observation in batch.observations
        )
        if len(submitted_digests) != len(set(submitted_digests)):
            raise LogisticsBusinessProviderError(
                "logistics business observation batch repeats an observation digest"
            )

        # The accepted append kernel computes the only canonical continuation
        # from the confirmed journal and the submitted batch.  A diverging
        # batch (foreign run, stale time, conflicting digest, missing/extra
        # records for a recorded barrier) raises here; the authoritative
        # workload is still asked to reject the frame so the rejection remains
        # service-owned, but a success response for such a batch would be a
        # contract violation and must never be acknowledged.
        kernel_conflict: ObservationIngressError | None = None
        expected: ObservationIngestResult | None = None
        expected_outcome = None
        try:
            expected_outcome = append_observations_batch(
                journal=self._observation_journal,
                batch=batch,
                provider_id=self._config.provider_id,
            )
            expected = observation_ingest_result(
                outcome=expected_outcome,
                provider_id=self._config.provider_id,
            )
        except ObservationIngressError as error:
            kernel_conflict = error

        response = await self._request(
            LOGISTICS_OBSERVATION_INGEST_OPERATION,
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "protocol_version": PROTOCOL_VERSION,
                "batch": batch.model_dump(mode="json"),
            },
        )
        try:
            result = ObservationIngestResult.model_validate(response.get("result"))
        except (KeyError, TypeError, ValueError) as exc:
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest response has no valid result"
            ) from exc
        if (
            result.run_id != self._run_id
            or result.provider_id != self._config.provider_id
        ):
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest result identity mismatch"
            )
        if result.at != batch.at:
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest result time mismatch"
            )
        if kernel_conflict is not None:
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest workload accepted a batch "
                "that diverges from the confirmed journal: "
                f"{kernel_conflict}"
            )
        assert expected is not None and expected_outcome is not None
        self._verify_expected_continuation(result, expected=expected)
        # Commit the confirmed journal only after the real response validated
        # against the canonical kernel continuation.
        self._observation_journal = expected_outcome.journal
        return result

    def _verify_expected_continuation(
        self,
        result: ObservationIngestResult,
        *,
        expected: ObservationIngestResult,
    ) -> None:
        """Require the acknowledgment to be the canonical kernel continuation.

        The workload acknowledgment must equal the accepted kernel's expected
        outcome exactly: the replay flag, the accepted records (content, digest
        and 1-based sequence), the canonical journal digest and the journal
        sequence count.  No opaque or arbitrary changed hash is ever treated as
        proof of acceptance.
        """
        if result.replayed != expected.replayed:
            if result.replayed:
                raise LogisticsBusinessProviderError(
                    "logistics business observation ingest false replay: the "
                    "workload claims a replay for a batch the confirmed journal "
                    "would accept freshly"
                )
            raise LogisticsBusinessProviderError(
                "logistics business observation barrier was already ingested; "
                "the workload returned a fresh acceptance for a known replay"
            )
        expected_records = expected.accepted_records
        expected_by_digest = {
            record.observation_digest: record.observation
            for record in expected_records
        }
        for record in result.accepted_records:
            expected_observation = expected_by_digest.get(record.observation_digest)
            if expected_observation is None:
                raise LogisticsBusinessProviderError(
                    "logistics business observation ingest acknowledgment does "
                    "not cover the exact submitted record set (wrong record or "
                    "omission)"
                )
            if record.observation != expected_observation:
                raise LogisticsBusinessProviderError(
                    "logistics business observation ingest acknowledgment "
                    "record content differs from the accepted kernel "
                    "continuation (forged recomputed-hash payload)"
                )
        if tuple(
            sorted(record.observation_digest for record in result.accepted_records)
        ) != tuple(
            sorted(record.observation_digest for record in expected_records)
        ):
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest acknowledgment does not "
                "cover the exact submitted record set (wrong record or omission)"
            )
        result_sequences = tuple(
            record.sequence for record in result.accepted_records
        )
        expected_sequences = tuple(
            record.sequence for record in expected_records
        )
        if result_sequences != expected_sequences:
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest acknowledgment does not "
                "continue the confirmed journal sequence exactly as the "
                "accepted kernel requires"
            )
        if result.journal_digest != expected.journal_digest:
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest acknowledgment journal "
                "digest does not match the canonical expected digest of the "
                "continued journal (an opaque or arbitrary changed hash is not "
                "an acceptance)"
            )
        if result.sequence_count != expected.sequence_count:
            raise LogisticsBusinessProviderError(
                "logistics business observation ingest sequence_count does not "
                "continue the confirmed journal exactly as the accepted kernel "
                "requires"
            )

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt:
        self._require_prepared()
        if request.run_id != self._run_id or request.terminal_time != self._last_time:
            raise LogisticsBusinessProviderError(
                "logistics business finalization identity or time mismatch"
            )
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
            raise LogisticsBusinessProviderError(
                "logistics business finalization response is invalid"
            ) from exc
        if (
            receipt.run_id != self._run_id
            or receipt.provider_id != self._config.provider_id
            or receipt.event_chain_root != request.event_chain_root
        ):
            raise LogisticsBusinessProviderError(
                "logistics business finalization receipt identity mismatch"
            )
        expected = {
            requirement.artifact_id: requirement
            for requirement in self._manifest.artifact_requirements
        }
        actual: dict[str, FinalizedArtifact] = {
            artifact.artifact_id: artifact for artifact in receipt.artifacts
        }
        if set(actual) != set(expected) or any(
            artifact.size_bytes > expected[artifact_id].max_size_bytes
            for artifact_id, artifact in actual.items()
        ):
            raise LogisticsBusinessProviderError(
                "logistics business finalized artifacts differ from declaration"
            )
        return receipt

    async def snapshot_digest(self) -> str:
        self._require_prepared()
        response = await self._request(
            "snapshot",
            {"provider_id": self._config.provider_id, "run_id": self._run_id},
        )
        try:
            snapshot = LogisticsBusinessSnapshotResponse.model_validate(response)
        except ValidationError as exc:
            raise LogisticsBusinessProviderError(
                "logistics business snapshot response has no valid digest"
            ) from exc
        return snapshot.snapshot_digest

    async def shutdown(self) -> None:
        if self._transport is None:
            return
        try:
            if self._prepared:
                response = await self._transport.request(
                    "shutdown",
                    {
                        "session_token": self._session_token,
                        "provider_id": self._config.provider_id,
                        "run_id": self._run_id,
                    },
                )
                if response.get("status") != "stopped":
                    raise LogisticsBusinessProviderError(
                        "logistics business provider did not acknowledge shutdown"
                    )
        finally:
            await self._transport.close()
            self._prepared = False
            self._transport = None
            self._last_time = None
            self._scenario_digest = None
            self._accepted_stage_input_count = 0
            self._history = ()
            self._orders_pool = ()


def _order_detail_payload(view: LogisticsOrderView) -> tuple[NamedValue, ...]:
    fields: dict[str, object] = {
        "order_id": view.order_id,
        "status": view.status,
        "version": view.version,
        "origin_facility_id": view.origin_facility_id,
        "destination_facility_id": view.destination_facility_id,
        "hub_handoff_facility_id": view.hub_handoff_facility_id,
        "cargo_mass_kg": view.cargo_mass_kg,
        "release_time_s": view.release_time_s,
        "deadline_s": view.deadline_s,
        "last_time_s": view.last_time_s,
        "assignee_id": view.assignee_id,
        "capacity_kg": view.capacity_kg,
        "evidence_ref": view.evidence_ref,
        "handoff_evidence_ref": view.handoff_evidence_ref,
        "hub_handoff_version": view.hub_handoff_version,
        "failure_reason": view.failure_reason,
    }
    return tuple(
        NamedValue(name=name, value=value) for name, value in sorted(fields.items())
    )


__all__ = [
    "ORDER_CREATED_EVENT_SCHEMA",
    "STATE_SCHEMA",
    "TRANSITION_EVENT_SCHEMA",
    "LogisticsBusinessProvider",
    "LogisticsBusinessProviderError",
    "LogisticsBusinessProviderNotReady",
    "LogisticsBusinessReadiness",
    "LogisticsBusinessSnapshotResponse",
    "pinned_package_digest",
]
