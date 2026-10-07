from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Callable, Mapping
from typing import Literal, TypeVar

from pydantic import TypeAdapter, ValidationError, model_validator

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import Identifier, NamedValue, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.contracts import ProviderCommandResult, ProviderSession
from aero_bench.providers.ns3.protocol import MAILBOX_OBSERVATION_PREFIX
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
)
from aero_bench.runtime.hooks import RuntimeHook, ValidatedObservation
from aero_bench.runtime.internal_commands import (
    InternalCommandAuthority,
    InternalCommandDispatcher,
)
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.business import InspectionBusinessService
from aero_bench.tasks.inspection.contracts import (
    BusinessCommandEnvelope,
    BusinessCommandResult,
    BusinessQuery,
    BusinessQueryResult,
    InspectionCommand,
    InspectionTaskPackage,
    TASK_LOCAL_BUSINESS_PRINCIPAL,
    WorkOrderStatus,
    WorkOrderView,
)
from aero_bench.tasks.inspection.integration import (
    INSPECTION_PACKAGE_ID,
    resolve_inspection_business_endpoint,
    resolve_inspection_run_context,
)


_NETWORK_DELIVERY_EVENT = "network.delivery"
_NETWORK_DELIVERY_SCHEMA = "inspection.network-delivery.v1"


class InspectionRuntimeHookError(RuntimeError):
    pass


class NetworkDeliveryEventPayload(StrictModel):
    schema_id: Literal["inspection.network-delivery.v1"]
    run_id: Sha256
    provider_id: Identifier
    command_id: Identifier
    agent_id: Identifier
    source_artifact_id: Identifier
    work_order_id: Identifier
    message_id: Identifier
    payload_digest: Sha256
    sent_tick: int
    sent_time_ns: int
    delivered_tick: int
    delivered_time_ns: int

    @model_validator(mode="after")
    def delivery_time_is_ordered(self) -> "NetworkDeliveryEventPayload":
        if (
            min(
                self.sent_tick,
                self.sent_time_ns,
                self.delivered_tick,
                self.delivered_time_ns,
            )
            < 0
        ):
            raise ValueError("network delivery times must be nonnegative")
        if (
            self.delivered_tick < self.sent_tick
            or self.delivered_time_ns < self.sent_time_ns
        ):
            raise ValueError("network delivery precedes submission")
        return self


_BUSINESS_TOOL_KINDS = {
    "business.claim": "claim",
    "business.start": "start",
    "business.observation_ready": "observation_ready",
    "business.submit": "submit",
    "business.complete": "complete",
    "business.fail": "fail",
    "business.cancel": "cancel",
}
_BUSINESS_REQUIRED_ARGUMENTS = {
    "claim": frozenset({"actor_id", "work_order_id"}),
    "start": frozenset({"actor_id", "work_order_id"}),
    "observation_ready": frozenset(
        {"actor_id", "work_order_id", "observation_id"}
    ),
    "submit": frozenset(
        {
            "actor_id",
            "work_order_id",
            "observation_id",
            "report_payload_digest",
        }
    ),
    "complete": frozenset({"actor_id", "work_order_id"}),
    "fail": frozenset({"actor_id", "work_order_id", "reason"}),
    "cancel": frozenset({"actor_id", "work_order_id", "reason"}),
}
_INSPECTION_COMMAND = TypeAdapter(InspectionCommand)
_ContractModel = TypeVar("_ContractModel", bound=StrictModel)


class TaskLocalBusinessSession:
    """Deterministic task-owned command/query endpoint outside the Provider barrier."""

    def __init__(
        self,
        *,
        package: InspectionTaskPackage,
        run_id: str,
        endpoint_id: str,
    ) -> None:
        self.endpoint_id = endpoint_id
        self._run_id = run_id
        business_actors = tuple(
            actor.actor_id for actor in package.actors if actor.role == "business"
        )
        if business_actors:
            raise ValueError(
                "task-local Business session cannot use a package Business actor"
            )
        self._service = InspectionBusinessService(
            package,
            run_id=run_id,
            allow_task_local_business_principal=True,
        )
        self._network_delivery_required = package.network_delivery_required

    @property
    def state(self):
        return self._service.state

    @property
    def history(self):
        return self._service.history

    def _envelope(self, request: CommandRequest) -> BusinessCommandEnvelope:
        if request.run_id != self._run_id:
            raise InspectionRuntimeHookError(
                "task-local business command belongs to another run"
            )
        kind = _BUSINESS_TOOL_KINDS.get(request.tool_id)
        if kind is None:
            raise InspectionRuntimeHookError(
                f"unsupported task-local business tool: {request.tool_id}"
            )
        arguments: dict[str, object] = {}
        for argument in request.arguments:
            if argument.name in arguments:
                raise InspectionRuntimeHookError(
                    f"duplicate task-local command argument: {argument.name}"
                )
            arguments[argument.name] = argument.value
        if set(arguments) != _BUSINESS_REQUIRED_ARGUMENTS[kind]:
            raise InspectionRuntimeHookError(
                f"task-local {kind} arguments do not match the package contract"
            )
        actor_id = arguments["actor_id"]
        work_order_id = arguments["work_order_id"]
        if not isinstance(actor_id, str) or not isinstance(work_order_id, str):
            raise InspectionRuntimeHookError(
                "task-local business identities must be strings"
            )
        if actor_id != request.agent_id:
            raise InspectionRuntimeHookError(
                "task-local business principal differs from command actor"
            )
        if kind in {"complete", "fail", "cancel"} and (
            actor_id != TASK_LOCAL_BUSINESS_PRINCIPAL or kind != "complete"
        ):
            raise InspectionRuntimeHookError(
                "task-local Business permits only reserved-principal completion"
            )
        command: dict[str, object] = {
            "kind": kind,
            "command_id": request.command_id,
            "work_order_id": work_order_id,
            "actor_id": actor_id,
            "issued_at": request.issued_at.model_dump(mode="json"),
        }
        for field in ("observation_id", "report_payload_digest", "reason"):
            if field in arguments:
                command[field] = arguments[field]
        try:
            parsed = _INSPECTION_COMMAND.validate_python(command)
        except (TypeError, ValueError) as error:
            raise InspectionRuntimeHookError(
                "task-local business command is invalid"
            ) from error
        return BusinessCommandEnvelope(run_id=self._run_id, command=parsed)

    @staticmethod
    def _transition_event(
        endpoint_id: str, result: BusinessCommandResult
    ) -> ProviderEvent:
        history = result.history_record
        return ProviderEvent(
            provider_id=endpoint_id,
            event_id="business.transition",
            time=result.transition.time,
            payload_schema_id="inspection.business-history.v1",
            payload=(
                NamedValue(
                    name="business_history_record_hash",
                    value=history.record_hash,
                ),
                NamedValue(name="business_history_sequence", value=history.sequence),
                NamedValue(name="command_id", value=result.command_id),
                NamedValue(name="schema_id", value="aero-bench.business-history/v1"),
            ),
        )

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        result = self._service.apply(self._envelope(request))
        transitions = [result]
        if result.transition.event == "submit" and not self._network_delivery_required:
            completion_id = self._command_id(
                "complete",
                {
                    "submit_command_id": request.command_id,
                    "work_order_id": result.transition.work_order_id,
                    "time": request.issued_at.model_dump(mode="json"),
                },
            )
            completion = _INSPECTION_COMMAND.validate_python(
                {
                    "kind": "complete",
                    "command_id": completion_id,
                    "work_order_id": result.transition.work_order_id,
                    "actor_id": TASK_LOCAL_BUSINESS_PRINCIPAL,
                    "issued_at": request.issued_at.model_dump(mode="json"),
                }
            )
            transitions.append(
                self._service.apply(
                    BusinessCommandEnvelope(
                        run_id=self._run_id,
                        command=completion,
                    )
                )
            )
        receipts = tuple(
            CommandReceipt(
                run_id=self._run_id,
                command_id=request.command_id,
                provider_id=self.endpoint_id,
                phase=phase,
                time=request.issued_at,
            )
            for phase in ("received", "accepted", "applied", "completed")
        )
        return ProviderCommandResult(
            receipts=receipts,
            events=tuple(
                self._transition_event(self.endpoint_id, transition)
                for transition in transitions
            ),
        )

    @staticmethod
    def _command_id(prefix: str, content: object) -> str:
        digest = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
        return f"internal.{prefix}.{digest[:32]}"

    async def query_business(self, query: BusinessQuery) -> BusinessQueryResult:
        return self._service.query(query)

    def evidence_document(
        self, *, source_artifact_id: str, event_chain_root: str
    ) -> dict[str, object]:
        history = self._service.history
        return {
            "source_artifact_id": source_artifact_id,
            "event_chain_root": event_chain_root,
            "business_history_root": (
                history[-1].record_hash if history else "0" * 64
            ),
            "history": [record.model_dump(mode="json") for record in history],
            "state": self._service.state.model_dump(mode="json"),
        }


class InspectionRuntimeHook(RuntimeHook):
    def __init__(
        self,
        *,
        run: ResolvedRunSpec,
        package: InspectionTaskPackage,
        providers: Mapping[str, ProviderSession],
        business_provider_id: str,
        business_session: object,
        logical_sessions: Mapping[str, object],
        dispatcher: InternalCommandDispatcher,
        runtime_hook_time: Callable[[], SimulationTime],
        timeout_ms: int,
        business_actor_id: str | None = None,
    ):
        work_orders_by_observation: dict[str, str] = {}
        for work_order in package.work_orders:
            if work_order.required_observation_id in work_orders_by_observation:
                raise ValueError(
                    "Inspection runtime hook requires an unambiguous observation-to-"
                    "work-order mapping"
                )
            work_orders_by_observation[work_order.required_observation_id] = (
                work_order.work_order_id
            )
        observation_providers = {
            observation.observation_id: observation.trigger.provider_id
            for observation in package.observations
        }
        scenario_network_provider_id = (
            None if run.scenario.network is None else run.scenario.network.provider_id
        )
        network_endpoint_ids = (
            set()
            if run.scenario.network is None
            else {
                binding.endpoint_id
                for binding in run.scenario.network.node_bindings
            }
        )
        mailbox_observations: set[tuple[str, str, str]] = set()
        flight_observations: set[tuple[str, str, str]] = set()
        for binding in run.scenario.task.observations:
            if binding.inspection is not None:
                continue
            if binding.flight is not None:
                flight_observations.add(
                    (binding.agent_id, binding.observation_id, binding.endpoint_id)
                )
                continue
            endpoint_id = (
                binding.observation_id[len(MAILBOX_OBSERVATION_PREFIX) :]
                if binding.observation_id.startswith(MAILBOX_OBSERVATION_PREFIX)
                else None
            )
            if (
                scenario_network_provider_id is None
                or binding.endpoint_id != scenario_network_provider_id
                or endpoint_id not in network_endpoint_ids
            ):
                raise ValueError(
                    "Inspection non-camera observation is not a resolved network mailbox"
                )
            mailbox_observations.add(
                (binding.agent_id, binding.observation_id, binding.endpoint_id)
            )
        if not callable(getattr(business_session, "query_business", None)) or not callable(
            getattr(business_session, "handle_command", None)
        ):
            raise ValueError(
                "Inspection business endpoint must implement deterministic command/query"
            )
        delivery_requirements = tuple(
            requirement
            for requirement in package.artifact_requirements
            if requirement.evidence_kind == "delivery"
        )
        if len(delivery_requirements) > 1:
            raise ValueError("Inspection runtime hook accepts at most one delivery artifact")
        delivery_requirement = delivery_requirements[0] if delivery_requirements else None
        if package.network_delivery_required != (delivery_requirement is not None):
            raise ValueError(
                "Inspection delivery evidence does not match its network requirement"
            )
        if mailbox_observations and (
            delivery_requirement is None
            or delivery_requirement.producer_id != scenario_network_provider_id
        ):
            raise ValueError(
                "Inspection mailbox observations require matching network delivery evidence"
            )
        if (
            delivery_requirement is not None
            and delivery_requirement.producer_id not in providers
        ):
            raise ValueError("Inspection delivery Provider is unavailable")
        if not callable(runtime_hook_time):
            raise TypeError("Inspection runtime hook time callback must be callable")

        self._run_id = run.run_id
        self._scenario_digest = run.scenario.scenario_digest
        self._package = package
        self._work_orders = {item.work_order_id: item for item in package.work_orders}
        self._work_orders_by_observation = work_orders_by_observation
        self._observation_providers = observation_providers
        self._mailbox_observations = frozenset(mailbox_observations)
        self._flight_observations = frozenset(flight_observations)
        self._business_endpoint_id = business_provider_id
        self._business_actor_id = business_actor_id or business_provider_id
        self._business_provider = business_session
        self._logical_sessions = dict(logical_sessions)
        self._network_delivery_required = package.network_delivery_required
        self._network_provider_id = (
            None if delivery_requirement is None else delivery_requirement.producer_id
        )
        self._delivery_artifact_id = (
            None if delivery_requirement is None else delivery_requirement.artifact_id
        )
        self._dispatcher = dispatcher
        self._runtime_hook_time = runtime_hook_time
        self._timeout_seconds = timeout_ms / 1000
        self._lock = asyncio.Lock()

    @property
    def logical_sessions(self) -> Mapping[str, object]:
        return dict(self._logical_sessions)

    def task_local_business_evidence(
        self, *, source_artifact_id: str, event_chain_root: str
    ) -> dict[str, object]:
        if not isinstance(self._business_provider, TaskLocalBusinessSession):
            raise InspectionRuntimeHookError(
                "business state is owned by an external Provider"
            )
        return self._business_provider.evidence_document(
            source_artifact_id=source_artifact_id,
            event_chain_root=event_chain_root,
        )

    async def on_validated_observation(self, observation: ValidatedObservation) -> None:
        async with self._lock:
            self._require_time(observation.time)
            if observation.run_id != self._run_id:
                raise InspectionRuntimeHookError(
                    "validated observation belongs to another run"
                )
            observation_key = (
                observation.agent_id,
                observation.observation_id,
                observation.provider_id,
            )
            if (
                observation_key in self._mailbox_observations
                or observation_key in self._flight_observations
            ):
                return
            work_order_id = self._work_orders_by_observation.get(
                observation.observation_id
            )
            expected_provider = self._observation_providers.get(
                observation.observation_id
            )
            if work_order_id is None or expected_provider is None:
                raise InspectionRuntimeHookError(
                    "validated observation is not declared by the Inspection package"
                )
            if observation.provider_id != expected_provider:
                raise InspectionRuntimeHookError(
                    "validated observation came from the wrong Provider"
                )
            state = await self._query_work_order(
                work_order_id,
                at=observation.time,
                purpose="observation",
            )
            if state.claimed_by != observation.agent_id:
                raise InspectionRuntimeHookError(
                    "validated observation does not belong to the work-order claimant"
                )
            if state.status is WorkOrderStatus.IN_PROGRESS:
                request = CommandRequest(
                    run_id=self._run_id,
                    command_id=self._command_id(
                        "observation-ready",
                        {
                            "work_order_id": work_order_id,
                            "observation_id": observation.observation_id,
                            "agent_id": observation.agent_id,
                            "payload_digest": observation.payload_digest,
                            "time": observation.time.model_dump(mode="json"),
                        },
                    ),
                    agent_id=observation.provider_id,
                    tool_id="business.observation_ready",
                    issued_at=observation.time,
                    arguments=(
                        NamedValue(name="actor_id", value=observation.provider_id),
                        NamedValue(
                            name="observation_id", value=observation.observation_id
                        ),
                        NamedValue(name="work_order_id", value=work_order_id),
                    ),
                )
                result = await self._dispatcher.invoke(
                    authority_id=self._observation_authority_id(
                        observation.provider_id
                    ),
                    request=request,
                )
                self._require_completed_transition(result, "observation_ready")
                return
            if (
                state.status
                in {
                    WorkOrderStatus.OBSERVATION_READY,
                    WorkOrderStatus.SUBMITTED,
                    WorkOrderStatus.COMPLETED,
                }
                and state.observation_id == observation.observation_id
            ):
                return
            raise InspectionRuntimeHookError(
                "work order is not eligible for observation_ready"
            )

    async def on_stage_barriers_closed(
        self,
        events: tuple[ProviderEvent, ...],
        *,
        scene_state: SceneState,
        stage_barriers: tuple[StageBarrier, ...],
        target: SimulationTime,
    ) -> None:
        async with self._lock:
            canonical_target = self._revalidate_contract(
                target,
                contract_type=SimulationTime,
                label="runtime hook target",
            )
            canonical_scene_state = self._revalidate_contract(
                scene_state,
                contract_type=SceneState,
                label="runtime hook SceneState",
            )
            canonical_stage_barriers = self._validate_stage_boundary(
                scene_state=canonical_scene_state,
                stage_barriers=stage_barriers,
                target=canonical_target,
            )
            self._require_time(canonical_target)
            canonical_events = self._revalidate_provider_events(
                events,
                target=canonical_target,
                stage_barriers=canonical_stage_barriers,
            )
            network_barrier = next(
                (
                    barrier
                    for barrier in canonical_stage_barriers
                    if barrier.stage == "network"
                ),
                None,
            )
            for event in canonical_events:
                if event.event_id != _NETWORK_DELIVERY_EVENT:
                    continue
                if (
                    not self._network_delivery_required
                    or self._network_provider_id is None
                ):
                    raise InspectionRuntimeHookError(
                        "network delivery evidence is not configured for this task"
                    )
                if network_barrier is None:
                    raise InspectionRuntimeHookError(
                        "network delivery arrived without a closed network barrier"
                    )
                if self._network_provider_id not in network_barrier.provider_ids:
                    raise InspectionRuntimeHookError(
                        "network delivery Provider is absent from the network barrier"
                    )
                if event.provider_id != self._network_provider_id:
                    raise InspectionRuntimeHookError(
                        "network delivery event came from the wrong Provider"
                    )
                delivery = self._parse_delivery(event, target=canonical_target)
                await self._handle_delivery(delivery, committed_at=canonical_target)

    def _validate_stage_boundary(
        self,
        *,
        scene_state: SceneState,
        stage_barriers: object,
        target: SimulationTime,
    ) -> tuple[StageBarrier, ...]:
        if not isinstance(stage_barriers, tuple):
            raise InspectionRuntimeHookError(
                "runtime hook stage barriers must be an ordered tuple"
            )
        canonical_barriers = tuple(
            self._revalidate_contract(
                barrier,
                contract_type=StageBarrier,
                label="runtime hook StageBarrier",
            )
            for barrier in stage_barriers
        )
        stages = tuple(barrier.stage for barrier in canonical_barriers)
        if stages not in {
            ("motion",),
            ("motion", "network"),
            ("motion", "business_environment"),
            ("motion", "network", "business_environment"),
        }:
            raise InspectionRuntimeHookError(
                "runtime hook stage barriers do not follow the closed-stage order"
            )
        if (
            scene_state.run_id != self._run_id
            or scene_state.scenario_digest != self._scenario_digest
            or scene_state.at != target
        ):
            raise InspectionRuntimeHookError(
                "runtime hook SceneState binding is inconsistent"
            )
        for index, barrier in enumerate(canonical_barriers):
            if (
                barrier.run_id != self._run_id
                or barrier.scenario_digest != self._scenario_digest
                or barrier.at != target
            ):
                raise InspectionRuntimeHookError(
                    "runtime hook StageBarrier binding is inconsistent"
                )
            expected_predecessors = tuple(
                (predecessor.stage, predecessor.barrier_digest)
                for predecessor in canonical_barriers[:index]
            )
            actual_predecessors = tuple(
                (predecessor.stage, predecessor.barrier_digest)
                for predecessor in barrier.predecessor_barriers
            )
            if actual_predecessors != expected_predecessors:
                raise InspectionRuntimeHookError(
                    "runtime hook StageBarrier predecessor chain is inconsistent"
                )
            if index == 0:
                if barrier.input_scene_state_digest is not None:
                    raise InspectionRuntimeHookError(
                        "motion StageBarrier cannot consume a SceneState"
                    )
            elif barrier.input_scene_state_digest != scene_state.scene_state_digest:
                raise InspectionRuntimeHookError(
                    "non-motion StageBarrier input SceneState is inconsistent"
                )
        motion_barrier = canonical_barriers[0]
        if (
            motion_barrier.stage != "motion"
            or motion_barrier != scene_state.stage_barrier
            or motion_barrier.barrier_digest
            != scene_state.stage_barrier.barrier_digest
        ):
            raise InspectionRuntimeHookError(
                "runtime hook motion StageBarrier does not bind the SceneState"
            )
        return canonical_barriers

    def _revalidate_provider_events(
        self,
        events: object,
        *,
        target: SimulationTime,
        stage_barriers: tuple[StageBarrier, ...],
    ) -> tuple[ProviderEvent, ...]:
        if not isinstance(events, tuple):
            raise InspectionRuntimeHookError(
                "runtime hook Provider events must be an ordered tuple"
            )
        closed_provider_ids = frozenset(
            provider_id
            for barrier in stage_barriers
            for provider_id in barrier.provider_ids
        )
        canonical_events: list[ProviderEvent] = []
        for event in events:
            canonical_event = self._revalidate_contract(
                event,
                contract_type=ProviderEvent,
                label="runtime hook ProviderEvent",
            )
            if canonical_event.time != target:
                raise InspectionRuntimeHookError(
                    "runtime hook ProviderEvent does not occur at the stage target"
                )
            if (
                canonical_event.event_id != _NETWORK_DELIVERY_EVENT
                and canonical_event.provider_id not in closed_provider_ids
            ):
                raise InspectionRuntimeHookError(
                    "runtime hook ProviderEvent is absent from the closed stages"
                )
            canonical_events.append(canonical_event)
        return tuple(canonical_events)

    @staticmethod
    def _revalidate_contract(
        value: object,
        *,
        contract_type: type[_ContractModel],
        label: str,
    ) -> _ContractModel:
        if not isinstance(value, contract_type):
            raise InspectionRuntimeHookError(f"{label} has the wrong contract type")
        try:
            canonical = contract_type.model_validate(value.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError) as error:
            raise InspectionRuntimeHookError(
                f"{label} fails strict revalidation"
            ) from error
        if canonical != value:
            raise InspectionRuntimeHookError(
                f"{label} is not canonically serialized"
            )
        return canonical

    async def _handle_delivery(
        self,
        delivery: NetworkDeliveryEventPayload,
        *,
        committed_at: SimulationTime,
    ) -> None:
        if delivery.work_order_id not in self._work_orders:
            raise InspectionRuntimeHookError(
                "network delivery names an undeclared work order"
            )
        state = await self._query_work_order(
            delivery.work_order_id,
            at=committed_at,
            purpose=f"delivery-{delivery.message_id}",
        )
        if state.status is WorkOrderStatus.COMPLETED:
            if (
                state.claimed_by == delivery.agent_id
                and state.report_payload_digest == delivery.payload_digest
            ):
                return
            raise InspectionRuntimeHookError(
                "completed work order conflicts with delivery identity"
            )
        if (
            state.status is not WorkOrderStatus.SUBMITTED
            or state.claimed_by != delivery.agent_id
            or state.report_payload_digest != delivery.payload_digest
        ):
            return
        request = CommandRequest(
            run_id=self._run_id,
            command_id=self._command_id(
                "complete",
                delivery.model_dump(mode="json"),
            ),
            agent_id=self._business_actor_id,
            tool_id="business.complete",
            issued_at=committed_at,
            arguments=(
                NamedValue(name="actor_id", value=self._business_actor_id),
                NamedValue(name="work_order_id", value=delivery.work_order_id),
            ),
        )
        result = await self._dispatcher.invoke(
            authority_id=self._complete_authority_id(),
            request=request,
        )
        self._require_completed_transition(result, "complete")

    def _parse_delivery(
        self,
        event: ProviderEvent,
        *,
        target: SimulationTime,
    ) -> NetworkDeliveryEventPayload:
        if event.payload_schema_id != _NETWORK_DELIVERY_SCHEMA:
            raise InspectionRuntimeHookError(
                "network delivery event uses the wrong payload schema"
            )
        raw = {item.name: item.value for item in event.payload}
        try:
            delivery = NetworkDeliveryEventPayload.model_validate(raw)
        except ValidationError as error:
            raise InspectionRuntimeHookError(
                "network delivery event payload is invalid"
            ) from error
        if (
            delivery.run_id != self._run_id
            or delivery.provider_id != event.provider_id
            or delivery.source_artifact_id != self._delivery_artifact_id
            or event.time != target
            or delivery.delivered_tick != target.tick
            or delivery.delivered_time_ns > target.sim_time_ns
        ):
            raise InspectionRuntimeHookError(
                "network delivery event identity is invalid"
            )
        return delivery

    async def _query_work_order(
        self,
        work_order_id: str,
        *,
        at: SimulationTime,
        purpose: str,
    ) -> WorkOrderView:
        query = BusinessQuery(
            run_id=self._run_id,
            query_id=self._command_id(
                "query",
                {
                    "purpose": purpose,
                    "work_order_id": work_order_id,
                    "time": at.model_dump(mode="json"),
                },
            ),
            kind="work_order",
            issued_at=at,
            work_order_id=work_order_id,
        )
        raw = self._business_provider.query_business(query)
        if not inspect.isawaitable(raw):
            raise InspectionRuntimeHookError(
                "Inspection Business query endpoint is not asynchronous"
            )
        try:
            raw_result = await asyncio.wait_for(raw, timeout=self._timeout_seconds)
        except asyncio.TimeoutError as error:
            raise InspectionRuntimeHookError(
                "Inspection Business query timed out"
            ) from error
        try:
            result = BusinessQueryResult.model_validate(
                raw_result.model_dump(mode="json")
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise InspectionRuntimeHookError(
                "Inspection Business query result is invalid"
            ) from error
        self._require_time(at)
        if (
            result.run_id != self._run_id
            or result.query_id != query.query_id
            or result.observed_at != at
            or len(result.work_orders) != 1
            or result.work_orders[0].work_order_id != work_order_id
        ):
            raise InspectionRuntimeHookError(
                "Inspection Business query result identity is invalid"
            )
        return result.work_orders[0]

    def _require_time(self, expected: SimulationTime) -> None:
        try:
            raw_current = self._runtime_hook_time()
        except Exception as error:
            raise InspectionRuntimeHookError(
                "runtime hook simulation time is unavailable"
            ) from error
        current = self._revalidate_contract(
            raw_current,
            contract_type=SimulationTime,
            label="runtime hook simulation time",
        )
        if current != expected:
            raise InspectionRuntimeHookError(
                "runtime hook time differs from the staged callback time"
            )

    @staticmethod
    def _require_completed_transition(
        result: ProviderCommandResult, transition: str
    ) -> None:
        if result.receipts[-1].phase != "completed" or not any(
            event.event_id == "business.transition" for event in result.events
        ):
            raise InspectionRuntimeHookError(
                f"Business Provider did not complete {transition}"
            )

    @staticmethod
    def _command_id(prefix: str, content: object) -> str:
        digest = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
        return f"internal.{prefix}.{digest[:32]}"

    @staticmethod
    def _observation_authority_id(provider_id: str) -> str:
        return f"inspection.observation-ready.{provider_id}"

    def _complete_authority_id(self) -> str:
        return f"inspection.complete.{self._business_endpoint_id}"


class InspectionRuntimeHookFactory:
    @property
    def package_id(self) -> str:
        return INSPECTION_PACKAGE_ID

    def create(
        self,
        *,
        run: ResolvedRunSpec,
        reader: BundleReader,
        providers: Mapping[str, ProviderSession],
        ledger: EventLedger,
        runtime_hook_time: Callable[[], SimulationTime],
    ) -> InspectionRuntimeHook:
        package, _, _ = resolve_inspection_run_context(
            reader=reader,
            task=run.task,
            environment=run.environment,
            agents=run.agents,
            scenario=run.scenario,
        )
        business_endpoint_id, task_local_business = (
            resolve_inspection_business_endpoint(
                package=package,
                task=run.task,
                environment=run.environment,
                agents=run.agents,
            )
        )
        expected_logical_endpoint_ids = (
            (business_endpoint_id,) if task_local_business else ()
        )
        if run.scenario.task.logical_endpoint_ids != expected_logical_endpoint_ids:
            raise ValueError(
                "Inspection runtime hook Business endpoint differs from "
                "ResolvedScenario ownership"
            )
        business_provider_id = business_endpoint_id
        logical_sessions: dict[str, object] = {}
        if task_local_business:
            if business_provider_id in providers:
                raise ValueError(
                    "Inspection task-local Business endpoint cannot have a Provider session"
                )
            business_session: object = TaskLocalBusinessSession(
                package=package,
                run_id=run.run_id,
                endpoint_id=business_provider_id,
            )
            logical_sessions[business_provider_id] = business_session
            business_actor_id = TASK_LOCAL_BUSINESS_PRINCIPAL
        else:
            business_session = providers.get(business_provider_id)
            if business_session is None:
                raise ValueError(
                    "Inspection external Business Provider is unavailable"
                )
            business_actor_id = business_provider_id
        observation_provider_ids = tuple(
            sorted(
                {
                    observation.trigger.provider_id
                    for observation in package.observations
                }
            )
        )
        authorities = tuple(
            InternalCommandAuthority(
                authority_id=InspectionRuntimeHook._observation_authority_id(
                    provider_id
                ),
                package_id=INSPECTION_PACKAGE_ID,
                actor_id=provider_id,
                actor_role="observation_provider",
                provider_id=business_provider_id,
                tool_id="business.observation_ready",
            )
            for provider_id in observation_provider_ids
        ) + (
            InternalCommandAuthority(
                authority_id=f"inspection.complete.{business_provider_id}",
                package_id=INSPECTION_PACKAGE_ID,
                actor_id=business_actor_id,
                actor_role="business",
                provider_id=business_provider_id,
                tool_id="business.complete",
            ),
        )
        timeout_ms = run.environment.clock.provider_timeout_ms
        dispatcher_sessions = {**providers, **logical_sessions}
        dispatcher = InternalCommandDispatcher(
            run_id=run.run_id,
            package_id=INSPECTION_PACKAGE_ID,
            authorities=authorities,
            providers=dispatcher_sessions,  # type: ignore[arg-type]
            authoritative_time=runtime_hook_time,
            ledger=ledger,
            timeout_ms=timeout_ms,
        )
        return InspectionRuntimeHook(
            run=run,
            package=package,
            providers=providers,
            business_provider_id=business_provider_id,
            business_session=business_session,
            logical_sessions=logical_sessions,
            dispatcher=dispatcher,
            runtime_hook_time=runtime_hook_time,
            timeout_ms=timeout_ms,
            business_actor_id=business_actor_id,
        )


__all__ = [
    "InspectionRuntimeHook",
    "InspectionRuntimeHookError",
    "InspectionRuntimeHookFactory",
    "NetworkDeliveryEventPayload",
    "TaskLocalBusinessSession",
]
