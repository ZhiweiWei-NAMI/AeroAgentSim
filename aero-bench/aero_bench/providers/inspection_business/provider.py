from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from aero_bench.config.models import NamedValue, Sha256, StrictModel
from aero_bench.gateway.contracts import (
    ProviderQueryResult,
    QueryRequest,
)
from aero_bench.providers.contracts import (
    ProviderCommandResult,
    ProviderManifest,
    ProviderSession,
)
from aero_bench.providers.inspection_business.config import InspectionBusinessConfig
from aero_bench.providers.inspection_business.protocol import (
    PROTOCOL_VERSION,
    InspectionBusinessTransport,
    connect_runtime,
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
from aero_bench.trace.vocabulary import (
    PUBLIC_EVENT_EVENT_TYPE,
    PUBLIC_PROVIDER_EVENT_TYPES,
    PUBLIC_STATUS_EVENT_TYPE,
)
from aero_bench.tasks.inspection.business import replay_business_history
from aero_bench.tasks.inspection.contracts import (
    BusinessHistoryRecord,
    BusinessQuery,
    BusinessQueryResult,
    WorkOrderView,
)
from aero_bench.world.resolved import ResolvedScenario


BUSINESS_HISTORY_SCHEMA = "aero-bench.business-history/v1"
BUSINESS_HISTORY_EVENT_SCHEMA = "inspection.business-history.v1"
_PUBLIC_EVENT_SCHEMA = "public.event.v3"
_PUBLIC_STATUS_SCHEMA = "public.status.v3"


class InspectionBusinessProviderError(RuntimeError):
    pass


class InspectionBusinessProviderNotReady(InspectionBusinessProviderError):
    pass


class InspectionBusinessReadiness(StrictModel):
    status: Literal["ready"]
    provider_id: str
    protocol_version: str
    runtime_image: str
    config_digest: Sha256


class InspectionBusinessSnapshotResponse(StrictModel):
    snapshot_digest: Sha256


def pinned_package_digest(task_package: Mapping[str, Any]) -> str:
    """The digest that binds a session to one canonical task package."""

    return hashlib.sha256(canonical_json_bytes(dict(task_package))).hexdigest()


class InspectionBusinessProvider(ProviderSession):
    """Client for the real deterministic Inspection Business provider workload.

    The provider workload owns the canonical WorkOrder state machine and the
    business history hash chain. This process contains no business state
    generator; every receipt, query result, and snapshot digest comes from the
    JSON-line provider service. Commands carry the Harness authoritative
    SimulationTime and never advance time.

    The session is the only legitimate RPC client: it connects to the
    materializer-owned endpoint over the real transport and presents the
    executor-issued, run-scoped session token on every frame, including the
    first ``prepare``. The executor injects the same token into the provider
    workload alone, so the workload pins it before any state exists and an
    arbitrary peer on the provider network cannot race the first prepare or
    forge the attested principal by replaying public identity fields.
    """

    _STATE_SCHEMA = "inspection.business.state.v1"

    def __init__(
        self,
        *,
        config: InspectionBusinessConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        session_token: str,
        scenario: ResolvedScenario,
    ):
        if manifest.provider_id != config.provider_id:
            raise ValueError(
                "Inspection Business manifest/provider configuration IDs differ"
            )
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise TypeError(
                "Inspection Business runtime_endpoint must be a RuntimeEndpoint"
            )
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Inspection Business run_id must be a SHA-256 digest"
            ) from error
        if validated_run_id == "0" * 64:
            raise ValueError(
                "Inspection Business run_id cannot be a placeholder digest"
            )
        try:
            validated_token = TypeAdapter(Sha256).validate_python(session_token)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Inspection Business session_token must be a SHA-256 digest"
            ) from error
        if validated_token == "0" * 64:
            raise ValueError(
                "Inspection Business session_token cannot be a placeholder digest"
            )
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("Inspection Business scenario must be a ResolvedScenario")
        target_ids = {target.target_id for target in scenario.semantic_targets}
        work_order_target_ids = {
            order.target_id for order in config.task_package.work_orders
        }
        if not work_order_target_ids <= target_ids:
            raise ValueError(
                "Inspection Business work orders name unresolved semantic targets"
            )
        self._config = config
        self._manifest = manifest
        self._runtime_endpoint = runtime_endpoint
        self._run_id = validated_run_id
        self._scenario = scenario
        # Executor-issued, run-scoped channel credential. The registry owns its
        # issuance; the workload pins it against its own injected copy and
        # rejects any frame that cannot present it.
        self._session_token = validated_token
        self._transport: InspectionBusinessTransport | None = None
        self._prepared = False
        self._last_time: SimulationTime | None = None
        self._scenario_digest: str | None = None
        self._accepted_stage_input_count = 0
        self._history: tuple[BusinessHistoryRecord, ...] = ()

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    async def _request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._transport is None:
            self._transport = await connect_runtime(self._runtime_endpoint)
        # The pinned executor-issued token wins on every frame: a payload key
        # can never replace or shadow the credential this session presents.
        return await self._transport.request(
            operation, {**dict(payload), "session_token": self._session_token}
        )

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise InspectionBusinessProviderNotReady(
                "Inspection Business provider has not completed readiness"
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
        }

    async def prepare(self) -> None:
        if self._prepared:
            raise InspectionBusinessProviderError(
                "Inspection Business provider prepare called twice"
            )
        response = await self._request("prepare", self._config_payload())
        try:
            readiness = InspectionBusinessReadiness.model_validate(response)
        except ValidationError as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business provider readiness response is invalid"
            ) from exc
        if readiness.status != "ready":
            raise InspectionBusinessProviderError(
                f"Inspection Business provider did not become ready: "
                f"{readiness.status!r}"
            )
        if readiness.provider_id != self._config.provider_id:
            raise InspectionBusinessProviderError(
                "Inspection Business readiness provider_id differs from declaration"
            )
        if readiness.protocol_version != PROTOCOL_VERSION:
            raise InspectionBusinessProviderError(
                "Inspection Business provider protocol version mismatch"
            )
        if readiness.runtime_image != self._manifest.runtime_image:
            raise InspectionBusinessProviderError(
                "Inspection Business provider image identity mismatch"
            )
        if readiness.config_digest != self._manifest.config_digest:
            raise InspectionBusinessProviderError(
                "Inspection Business provider config digest mismatch"
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
            raise InspectionBusinessProviderError(
                f"Inspection Business {operation} response does not contain a "
                "valid StepReceipt"
            ) from exc
        if receipt.run_id != self._run_id:
            raise InspectionBusinessProviderError(
                "Inspection Business StepReceipt run identity mismatch"
            )
        if receipt.provider_id != self._config.provider_id:
            raise InspectionBusinessProviderError(
                "Inspection Business StepReceipt provider identity mismatch"
            )
        if receipt.reached != target:
            raise InspectionBusinessProviderError(
                "Inspection Business backend did not reach the requested time"
            )
        if not any(
            event.payload_schema_id == self._STATE_SCHEMA for event in receipt.events
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business response omitted the authoritative "
                "inspection.business.state.v1 event"
            )
        self._validate_public_events(receipt.events)
        return receipt

    @staticmethod
    def _validate_public_events(events: tuple[ProviderEvent, ...]) -> None:
        public_events = tuple(
            event for event in events if event.event_id in PUBLIC_PROVIDER_EVENT_TYPES
        )
        statuses = tuple(
            event
            for event in public_events
            if event.event_id == PUBLIC_STATUS_EVENT_TYPE
        )
        if len(statuses) != 1:
            raise InspectionBusinessProviderError(
                "Inspection Business response must contain one public v3 status"
            )
        for event in public_events:
            if event.event_id == PUBLIC_STATUS_EVENT_TYPE:
                expected_schema = _PUBLIC_STATUS_SCHEMA
            elif event.event_id == PUBLIC_EVENT_EVENT_TYPE:
                expected_schema = _PUBLIC_EVENT_SCHEMA
            else:
                raise InspectionBusinessProviderError(
                    "Inspection Business emitted another Provider's public event type"
                )
            if event.payload_schema_id != expected_schema:
                raise InspectionBusinessProviderError(
                    "Inspection Business emitted a legacy public event schema"
                )

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
        self._history = ()
        return receipt

    def _validate_stage_request(
        self, request: BusinessEnvironmentStageRequest
    ) -> BusinessEnvironmentStageRequest:
        """Reject anything that is not a canonical, causally bound stage input."""

        if not isinstance(request, BusinessEnvironmentStageRequest):
            raise InspectionBusinessProviderError(
                "Inspection Business requires a BusinessEnvironmentStageRequest"
            )
        try:
            canonical = BusinessEnvironmentStageRequest.model_validate(
                request.model_dump(mode="json")
            )
            canonical_scene_state = SceneState.model_validate(
                request.scene_state.model_dump(mode="json")
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business stage request fails strict revalidation"
            ) from exc
        if canonical != request or canonical_scene_state != request.scene_state:
            raise InspectionBusinessProviderError(
                "Inspection Business stage request is not canonically serialized"
            )
        if request.run_id != self._run_id:
            raise InspectionBusinessProviderError(
                "Inspection Business stage request belongs to another run"
            )
        if request.provider_id != self._config.provider_id:
            raise InspectionBusinessProviderError(
                "Inspection Business stage request provider identity mismatch"
            )
        if (
            canonical_scene_state.run_id != request.run_id
            or canonical_scene_state.scenario_digest != request.scenario_digest
            or canonical_scene_state.at != request.target
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business SceneState binding is inconsistent"
            )
        computed_scene_state_digest = scene_state_digest_value(canonical_scene_state)
        if (
            canonical_scene_state.scene_state_digest != computed_scene_state_digest
            or request.scene_state_digest != computed_scene_state_digest
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business SceneState digest does not match its content"
            )
        predecessor_stages = tuple(
            predecessor.stage for predecessor in request.predecessor_barriers
        )
        if predecessor_stages not in {("motion",), ("motion", "network")}:
            raise InspectionBusinessProviderError(
                "Inspection Business predecessors must be motion or motion then network"
            )
        motion_barrier = request.predecessor_barriers[0]
        if (
            motion_barrier.stage != "motion"
            or motion_barrier.barrier_digest
            != canonical_scene_state.stage_barrier.barrier_digest
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business motion predecessor does not bind the SceneState"
            )
        if (
            self._scenario_digest is not None
            and request.scenario_digest != self._scenario_digest
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business stage request scenario digest changed mid-session"
            )
        if self._last_time is not None and (
            request.target.tick <= self._last_time.tick
            or request.target.sim_time_ns <= self._last_time.sim_time_ns
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business stage target must advance monotonically"
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
        state_events = tuple(
            event
            for event in receipt.events
            if event.payload_schema_id == self._STATE_SCHEMA
        )
        if len(state_events) != 1:
            raise InspectionBusinessProviderError(
                "Inspection Business staged receipt must contain exactly one "
                "authoritative state event"
            )
        state_event = state_events[0]
        fields = {item.name: item.value for item in state_event.payload}
        if len(fields) != len(state_event.payload):
            raise InspectionBusinessProviderError(
                "Inspection Business state event has duplicate payload names"
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
            raise InspectionBusinessProviderError(
                "Inspection Business authoritative state event is not bound to the "
                "accepted staged input"
            )

    def _parse_stage_result(
        self,
        response: Mapping[str, Any],
        *,
        request: BusinessEnvironmentStageRequest,
    ) -> BusinessEnvironmentStageResult:
        if set(response) != {"result"}:
            raise InspectionBusinessProviderError(
                "Inspection Business staged response must contain exactly result"
            )
        raw_result = response["result"]
        if not isinstance(raw_result, Mapping):
            raise InspectionBusinessProviderError(
                "Inspection Business staged response result must be a JSON object"
            )
        try:
            result = BusinessEnvironmentStageResult.model_validate(raw_result)
            canonical = BusinessEnvironmentStageResult.model_validate(
                result.model_dump(mode="json")
            )
        except (TypeError, ValueError) as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business staged response is invalid"
            ) from exc
        if (
            canonical != result
            or canonical_json_bytes(result.model_dump(mode="json"))
            != canonical_json_bytes(dict(raw_result))
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business staged response is not canonical"
            )
        if (
            result.run_id != request.run_id
            or result.scenario_digest != request.scenario_digest
            or result.provider_id != request.provider_id
            or result.target != request.target
            or result.input_scene_state_digest != request.scene_state_digest
            or result.predecessor_barriers != request.predecessor_barriers
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business staged response binding is inconsistent"
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
            raise InspectionBusinessProviderError(
                "Inspection Business staged StepReceipt digest is inconsistent"
            )
        if result.contribution.samples or result.contribution.attribute_updates:
            raise InspectionBusinessProviderError(
                "Inspection Business has no declared SceneState entity ownership"
            )
        self._validate_stage_state_event(receipt, request)
        return canonical

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
        # Only a fully validated provider result may advance local session state
        # or pin the otherwise external resolved-scenario digest.
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
            raise InspectionBusinessProviderError(
                "Inspection Business command returned no command receipts"
            )
        for receipt in receipts:
            if receipt.run_id != request.run_id:
                raise InspectionBusinessProviderError(
                    "Inspection Business command receipt belongs to another run"
                )
            if receipt.command_id != request.command_id:
                raise InspectionBusinessProviderError(
                    "Inspection Business command receipt has the wrong command_id"
                )
            if receipt.provider_id != provider_id:
                raise InspectionBusinessProviderError(
                    "Inspection Business command receipt has the wrong provider_id"
                )
            if receipt.time != request.issued_at:
                raise InspectionBusinessProviderError(
                    "Inspection Business command receipt time must equal the "
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
            raise InspectionBusinessProviderError(
                "Inspection Business command phases must separately record "
                "received, accepted, applied, and completed (or an ordered failure)"
            )

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        self._require_prepared()
        if request.run_id != self._run_id:
            raise InspectionBusinessProviderError(
                "Inspection Business command request belongs to another run"
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
            raise InspectionBusinessProviderError(
                "Inspection Business command response receipts must be a JSON array"
            )
        try:
            receipts = tuple(
                CommandReceipt.model_validate(item) for item in raw_receipts
            )
        except ValidationError as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business command response contains an invalid receipt"
            ) from exc
        self._validate_command_receipts(receipts, request, self._config.provider_id)

        completed = receipts[-1].phase == "completed"
        expected_fields = {"receipts"}
        if completed:
            expected_fields.add("history_record")
        if completed and request.tool_id == "business.complete":
            expected_fields.add("state_receipt")
        if set(response) != expected_fields:
            raise InspectionBusinessProviderError(
                "Inspection Business command response fields do not match its outcome"
            )
        if not completed:
            return ProviderCommandResult(receipts=receipts)

        state_events: tuple[ProviderEvent, ...] = ()
        if request.tool_id == "business.complete":
            state_receipt = self._parse_receipt(
                {"receipt": response["state_receipt"]},
                target=request.issued_at,
                operation="command",
            )
            state_events = tuple(
                event
                for event in state_receipt.events
                if event.event_id in PUBLIC_PROVIDER_EVENT_TYPES
            )
            expected_public_events = (PUBLIC_STATUS_EVENT_TYPE,)
            if self._manifest.artifact_requirements[0].visibility == "public":
                expected_public_events += (PUBLIC_EVENT_EVENT_TYPE,)
            if tuple(event.event_id for event in state_events) != expected_public_events:
                raise InspectionBusinessProviderError(
                    "Inspection Business complete response omitted terminal public state"
                )
        try:
            history_record = BusinessHistoryRecord.model_validate(
                response["history_record"]
            )
        except (KeyError, ValidationError) as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business command response has no valid history record"
            ) from exc
        command = history_record.command.command
        transition = history_record.transition
        if history_record.command.run_id != request.run_id:
            raise InspectionBusinessProviderError(
                "Inspection Business history record belongs to another run"
            )
        if command.command_id != request.command_id:
            raise InspectionBusinessProviderError(
                "Inspection Business history record belongs to another command"
            )
        if (
            command.actor_id != request.agent_id
            or transition.actor_id != request.agent_id
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business history principal differs from the command"
            )
        if (
            transition.run_id != request.run_id
            or transition.time != request.issued_at
            or transition.event != command.kind
            or request.tool_id != f"business.{command.kind}"
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business history transition is not bound to the command"
            )
        candidate_history = (*self._history, history_record)
        try:
            replay_business_history(
                self._config.task_package,
                run_id=self._run_id,
                history=candidate_history,
            )
        except ValueError as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business command history chain is invalid"
            ) from exc
        self._history = candidate_history

        event = ProviderEvent(
            provider_id=self._config.provider_id,
            event_id="business.transition",
            time=transition.time,
            payload_schema_id=BUSINESS_HISTORY_EVENT_SCHEMA,
            payload=(
                NamedValue(
                    name="business_history_record_hash",
                    value=history_record.record_hash,
                ),
                NamedValue(
                    name="business_history_sequence",
                    value=history_record.sequence,
                ),
                NamedValue(name="command_id", value=command.command_id),
                NamedValue(name="schema_id", value=BUSINESS_HISTORY_SCHEMA),
            ),
        )
        return ProviderCommandResult(
            receipts=receipts,
            events=(event, *state_events),
        )

    async def query_business(self, query: BusinessQuery) -> BusinessQueryResult:
        self._require_prepared()
        if query.run_id != self._run_id:
            raise InspectionBusinessProviderError(
                "Inspection Business query belongs to another run"
            )
        if self._last_time is None or query.issued_at != self._last_time:
            raise InspectionBusinessProviderError(
                "Inspection Business query time is not authoritative"
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
            result = BusinessQueryResult.model_validate(response.get("result"))
        except ValidationError as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business query response has no valid result"
            ) from exc
        if result.run_id != self._run_id or result.query_id != query.query_id:
            raise InspectionBusinessProviderError(
                "Inspection Business query result identity mismatch"
            )
        if result.observed_at != query.issued_at:
            raise InspectionBusinessProviderError(
                "Inspection Business query result time mismatch"
            )
        return result

    async def query(self, request: QueryRequest) -> ProviderQueryResult:
        """Return an Agent-safe projection of authoritative work-order state."""

        self._require_prepared()
        if not isinstance(request, QueryRequest):
            raise InspectionBusinessProviderError(
                "Inspection Business query request is invalid"
            )
        arguments = {item.name: item.value for item in request.arguments}
        if len(arguments) != len(request.arguments):
            raise InspectionBusinessProviderError(
                "Inspection Business query arguments are not unique"
            )
        if request.query_type == "business.work-orders":
            if arguments:
                raise InspectionBusinessProviderError(
                    "work-order summary query does not accept arguments"
                )
            business_query = BusinessQuery(
                run_id=request.run_id,
                query_id=request.query_id,
                kind="summary",
                issued_at=request.issued_at,
            )
        elif request.query_type == "business.work-order":
            if set(arguments) != {"work_order_id"} or not isinstance(
                arguments["work_order_id"], str
            ):
                raise InspectionBusinessProviderError(
                    "work-order detail query requires work_order_id"
                )
            business_query = BusinessQuery(
                run_id=request.run_id,
                query_id=request.query_id,
                kind="work_order",
                issued_at=request.issued_at,
                work_order_id=arguments["work_order_id"],
            )
        else:
            raise InspectionBusinessProviderError(
                "Inspection Business query type is unsupported"
            )

        state_result = await self.query_business(business_query)
        if request.query_type == "business.work-orders":
            rows = [
                {
                    "claimed_by": item.claimed_by,
                    "last_tick": item.last_time.tick,
                    "last_time_ns": item.last_time.sim_time_ns,
                    "status": item.status.value,
                    "target_id": item.target_id,
                    "version": item.version,
                    "work_order_id": item.work_order_id,
                }
                for item in state_result.work_orders
            ]
            payload = (
                NamedValue(name="work_order_count", value=len(rows)),
                NamedValue(
                    name="work_orders_json",
                    value=canonical_json_bytes(rows).decode("utf-8"),
                ),
            )
        else:
            payload = self._work_order_detail_payload(state_result.work_orders[0])

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

    def _work_order_detail_payload(
        self, view: WorkOrderView
    ) -> tuple[NamedValue, ...]:
        work_order_id = view.work_order_id
        target_id = view.target_id
        work_order = next(
            (
                item
                for item in self._config.task_package.work_orders
                if item.work_order_id == work_order_id
            ),
            None,
        )
        target = next(
            (
                item
                for item in self._scenario.semantic_targets
                if item.target_id == target_id
            ),
            None,
        )
        observation = next(
            (
                item
                for item in self._config.task_package.observations
                if work_order is not None
                and item.observation_id == work_order.required_observation_id
            ),
            None,
        )
        if work_order is None or target is None or observation is None:
            raise InspectionBusinessProviderError(
                "Inspection Business work-order projection is incomplete"
            )
        position = target.pose.position
        orientation = target.pose.orientation_enu
        fields: dict[str, object] = {
            "agl_m": position.agl_m,
            "amsl_m": position.amsl_m,
            "arrival_tolerance_m": work_order.arrival_tolerance_m,
            "claimed_by": view.claimed_by,
            "completion_authority": "business",
            "east_m": position.enu.east_m,
            "ellipsoid_height_m": position.wgs84.ellipsoid_height_m,
            "height_datum": "wgs84-ellipsoid+amsl+agl",
            "last_tick": view.last_time.tick,
            "last_time_ns": view.last_time.sim_time_ns,
            "latitude_deg": position.wgs84.latitude_deg,
            "longitude_deg": position.wgs84.longitude_deg,
            "max_observation_distance_m": observation.trigger.max_distance_m,
            "max_view_angle_deg": observation.trigger.max_view_angle_deg,
            "min_observation_distance_m": observation.trigger.min_distance_m,
            "min_view_angle_deg": observation.trigger.min_view_angle_deg,
            "navigation_coordinate_frame": "WGS84+ENU",
            "network_delivery_required": self._config.task_package.network_delivery_required,
            "north_m": position.enu.north_m,
            "observation_earliest_time_ns": observation.trigger.earliest_time_ns,
            "observation_latest_time_ns": observation.trigger.latest_time_ns,
            "observation_id": view.observation_id,
            "orientation_qw": orientation.qw,
            "orientation_qx": orientation.qx,
            "orientation_qy": orientation.qy,
            "orientation_qz": orientation.qz,
            "report_artifact_type": work_order.report_artifact_type,
            "report_payload_digest": view.report_payload_digest,
            "required_observation_id": work_order.required_observation_id,
            "status": view.status.value,
            "target_id": target.target_id,
            "up_m": position.enu.up_m,
            "upload_deadline_ns": work_order.upload_deadline_ns,
            "version": view.version,
            "work_order_id": work_order.work_order_id,
        }
        return tuple(
            NamedValue(name=name, value=value) for name, value in sorted(fields.items())
        )

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt:
        self._require_prepared()
        if request.run_id != self._run_id or request.terminal_time != self._last_time:
            raise InspectionBusinessProviderError(
                "Inspection Business finalization identity or time mismatch"
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
            raise InspectionBusinessProviderError(
                "Inspection Business finalization response is invalid"
            ) from exc
        if (
            receipt.run_id != self._run_id
            or receipt.provider_id != self._config.provider_id
            or receipt.event_chain_root != request.event_chain_root
        ):
            raise InspectionBusinessProviderError(
                "Inspection Business finalization receipt identity mismatch"
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
            raise InspectionBusinessProviderError(
                "Inspection Business finalized artifacts differ from declaration"
            )
        return receipt

    async def snapshot_digest(self) -> str:
        self._require_prepared()
        response = await self._request(
            "snapshot",
            {"provider_id": self._config.provider_id, "run_id": self._run_id},
        )
        try:
            snapshot = InspectionBusinessSnapshotResponse.model_validate(response)
        except ValidationError as exc:
            raise InspectionBusinessProviderError(
                "Inspection Business snapshot response has no valid digest"
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
                    raise InspectionBusinessProviderError(
                        "Inspection Business provider did not acknowledge shutdown"
                    )
        finally:
            await self._transport.close()
            self._prepared = False
            self._transport = None
            self._last_time = None
            self._scenario_digest = None
            self._accepted_stage_input_count = 0
            self._history = ()


__all__ = [
    "BUSINESS_HISTORY_EVENT_SCHEMA",
    "BUSINESS_HISTORY_SCHEMA",
    "PROTOCOL_VERSION",
    "InspectionBusinessProvider",
    "InspectionBusinessProviderError",
    "InspectionBusinessProviderNotReady",
    "InspectionBusinessReadiness",
    "InspectionBusinessSnapshotResponse",
    "pinned_package_digest",
]
