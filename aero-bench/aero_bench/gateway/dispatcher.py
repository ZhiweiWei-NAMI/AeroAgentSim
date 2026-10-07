from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    FileRef,
    NamedValue,
    ObservationGrant,
    QueryGrant,
    ToolGrant,
)
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.gateway.contracts import (
    AgentPrincipal,
    DecisionSummaryReceipt,
    DecisionSummaryRequest,
    ObservationEnvelope,
    ProviderObservationEndpoint,
    ProviderQueryEndpoint,
    ProviderQueryResult,
    ProviderToolEndpoint,
    ProviderToolResult,
    QueryRequest,
    QueryResult,
    ToolResult,
)
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    ProviderEvent,
    SimulationTime,
)
from aero_bench.runtime.hooks import RuntimeHook, ValidatedObservation
from aero_bench.runtime.events import (
    AgentInteractionType,
    PUBLIC_AGENT_INTERACTION_FIELDS,
    RunEventAudience,
    RunEvent,
    RunEventSourceKind,
)
from aero_bench.runtime.ledger import (
    RESERVED_RUNTIME_EVENT_TYPES,
    EventLedger,
)
from aero_bench.serialization import canonical_json_bytes


class GatewayDispatchError(RuntimeError):
    pass


_FORBIDDEN_REASONING_FIELDS = frozenset(
    {"chain_of_thought", "hidden_reasoning", "reasoning_trace"}
)
_COMMAND_PHASES = frozenset(
    {"received", "accepted", "applied", "completed", "failed"}
)


@dataclass(slots=True)
class _CommandDispatch:
    request_digest: str
    authentication_id: str
    idempotent: bool
    tool_call_event_id: str | None = None
    gateway_dispatch_event_id: str | None = None
    result: ToolResult | None = None


@dataclass(slots=True)
class _CommandPhaseIndex:
    """Latest lifecycle event plus the latest explicit diagnostic detail."""

    event: RunEvent
    phase: str
    explicit_detail: str | None


class GatewayDispatcher:
    """Authorizes, validates, times, and audits Provider-facing calls."""

    def __init__(
        self,
        *,
        run: ResolvedRunSpec,
        bundle_root: str | Path,
        tool_endpoints: dict[str, ProviderToolEndpoint],
        observation_endpoints: dict[str, ProviderObservationEndpoint],
        query_endpoints: dict[tuple[str, str], ProviderQueryEndpoint] | None = None,
        authoritative_time: Callable[[], SimulationTime],
        ledger: EventLedger,
        runtime_hook: RuntimeHook | None = None,
    ):
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("GatewayDispatcher.run must be ResolvedRunSpec")
        if not isinstance(ledger, EventLedger):
            raise TypeError("GatewayDispatcher.ledger must be EventLedger")
        if ledger.run_id != run.run_id:
            raise ValueError("GatewayDispatcher ledger belongs to another ResolvedRun")
        if not callable(authoritative_time):
            raise TypeError("GatewayDispatcher.authoritative_time must be callable")
        self._run = run
        self._reader = BundleReader(Path(bundle_root))
        declared_provider_ids = {
            provider.provider_id for provider in run.environment.providers
        }
        unknown_tool_endpoints = set(tool_endpoints) - declared_provider_ids
        unknown_observation_endpoints = (
            set(observation_endpoints) - declared_provider_ids
        )
        query_endpoints = {} if query_endpoints is None else query_endpoints
        declared_query_endpoints = {
            (grant.provider_id, grant.query_type)
            for agent in run.agents
            for grant in agent.queries
        }
        unknown_query_endpoints = set(query_endpoints) - declared_query_endpoints
        if (
            unknown_tool_endpoints
            or unknown_query_endpoints
            or unknown_observation_endpoints
        ):
            raise ValueError(
                "gateway endpoints name undeclared providers: "
                f"tools={sorted(unknown_tool_endpoints)}, "
                f"queries={sorted(unknown_query_endpoints)}, "
                f"observations={sorted(unknown_observation_endpoints)}"
            )
        self._tool_endpoints = dict(tool_endpoints)
        self._query_endpoints = dict(query_endpoints)
        self._observation_endpoints = dict(observation_endpoints)
        self._authoritative_time = authoritative_time
        # The caller owns this exact instance and passes it to HarnessCoordinator.
        # The Gateway must never create or replace the authoritative ledger.
        self._ledger = ledger
        self._runtime_hook = runtime_hook
        self._requests: dict[str, _CommandDispatch] = {}
        self._query_ids: dict[str, tuple[str, str]] = {}
        self._decision_summaries: dict[
            str, tuple[str, str, DecisionSummaryReceipt]
        ] = {}
        # The Harness appends barrier and Provider events to this same ledger.
        # Keep the Gateway indexes append-only and lazily ingest any external
        # suffix before a query, so no query needs to rescan the full history.
        self._indexed_record_count = 0
        self._command_phase_indexes: dict[str, _CommandPhaseIndex] = {}
        self._latest_agent_observation_event_ids: dict[tuple[str, str], str] = {}
        self._sync_event_indexes()

    async def invoke(
        self, principal: AgentPrincipal, request: CommandRequest
    ) -> ToolResult:
        self._validate_principal(principal, request)
        agent = self._declared_agent(principal.agent_id)
        grant = next(
            (grant for grant in agent.tools if grant.tool_id == request.tool_id),
            None,
        )
        if grant is None:
            raise GatewayDispatchError("tool is not granted to this agent")

        request_digest = self._request_digest(request)
        previous = self._requests.get(request.command_id)
        if previous is not None:
            if previous.request_digest != request_digest:
                raise GatewayDispatchError(
                    "command_id was reused with a different request"
                )
            if previous.authentication_id != principal.authentication_id:
                raise GatewayDispatchError(
                    "command_id was reused by a different authentication identity"
                )
            if not previous.idempotent:
                raise GatewayDispatchError("non-idempotent command_id was reused")
            if previous.result is None:
                raise GatewayDispatchError(
                    "idempotent command was already dispatched but has no validated "
                    "result; use a new command_id"
                )
            return previous.result

        current_time = self._current_time()
        if request.issued_at != current_time:
            raise GatewayDispatchError(
                "command issued_at must equal the authoritative simulation time"
            )
        try:
            self._require_command_decision_summary(
                principal=principal,
                request=request,
            )
        except GatewayDispatchError:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=current_time,
                failure_class="decision_summary_missing",
            )
            raise

        try:
            self._validate_named_payload(
                grant.request_schema, request.arguments, "tool request"
            )
        except GatewayDispatchError:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=current_time,
                failure_class="request_validation_failed",
            )
            raise

        endpoint = self._tool_endpoints.get(grant.provider_id)
        if endpoint is None:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=current_time,
                failure_class="provider_unavailable",
            )
            raise GatewayDispatchError(
                f"declared tool Provider is unavailable: {grant.provider_id}"
            )

        dispatch = _CommandDispatch(
            request_digest=request_digest,
            authentication_id=principal.authentication_id,
            idempotent=grant.idempotent,
        )
        # Consume the command ID before any external side effect. A retry after
        # transport loss therefore cannot dispatch a second command.
        self._requests[request.command_id] = dispatch
        dispatch.tool_call_event_id = self._append_command_issued(
            principal=principal,
            request=request,
            grant=grant,
            request_digest=request_digest,
            time=current_time,
        )
        dispatch.gateway_dispatch_event_id = self._append_gateway_dispatch(
            principal=principal,
            request=request,
            grant=grant,
            request_digest=request_digest,
            time=current_time,
            parent_event_id=dispatch.tool_call_event_id,
        )

        try:
            outcome = await asyncio.wait_for(
                endpoint.invoke(request),
                timeout=grant.timeout_ms / 1000,
            )
        except asyncio.TimeoutError as error:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="provider_timeout",
            )
            raise GatewayDispatchError(
                f"tool call exceeded its {grant.timeout_ms} ms contract timeout"
            ) from error
        except Exception as error:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="provider_exception",
            )
            raise GatewayDispatchError(
                "Provider tool call failed after command dispatch"
            ) from error

        try:
            self._require_authority_unchanged(current_time)
        except GatewayDispatchError as error:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="authority_advanced_during_call",
            )
            raise GatewayDispatchError(
                "authoritative simulation time advanced during Provider call"
            ) from error

        try:
            if not isinstance(outcome, ProviderToolResult):
                raise GatewayDispatchError(
                    "Provider returned an invalid internal tool result"
                )
            result = outcome.result
            receipts = self._validate_command_receipts(
                request=request,
                grant=grant,
                result=result,
                expected_time=current_time,
            )
            self._validate_result_payload(
                principal=principal,
                request=request,
                grant=grant,
                result=result,
                expected_time=current_time,
            )
            provider_events = self._validate_provider_events(
                events=outcome.events,
                request=request,
                grant=grant,
                receipts=receipts,
                expected_time=current_time,
            )
        except GatewayDispatchError:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="result_validation_failed",
            )
            raise
        except Exception as error:
            self._append_command_failure(
                principal=principal,
                request=request,
                grant=grant,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="result_validation_failed",
            )
            raise GatewayDispatchError("Provider result validation failed") from error

        receipt_event_ids = self._append_command_receipts(
            principal=principal,
            request=request,
            grant=grant,
            request_digest=request_digest,
            receipts=receipts,
            parent_event_id=dispatch.gateway_dispatch_event_id,
        )
        provider_event_ids = self._append_provider_events(
            provider_events,
            request=request,
            parent_event_id=(
                receipt_event_ids[-1]
                if receipt_event_ids
                else dispatch.gateway_dispatch_event_id
            ),
        )

        result_event_id = self._append_command_validated(
            principal=principal,
            request=request,
            grant=grant,
            request_digest=request_digest,
            result=result,
            time=current_time,
            parent_event_id=(
                provider_event_ids[-1]
                if provider_event_ids
                else receipt_event_ids[-1]
                if receipt_event_ids
                else dispatch.gateway_dispatch_event_id
            ),
            causal_event_ids=tuple(
                event_id
                for event_id in (
                    dispatch.tool_call_event_id,
                    dispatch.gateway_dispatch_event_id,
                    *receipt_event_ids,
                    *provider_event_ids,
                )
                if event_id is not None
            ),
        )
        if result.observation is not None:
            observation_grant = next(
                item
                for item in agent.observations
                if item.observation_id == result.observation.observation_id
            )
            observation_event_id = self._append_observation_validated(
                principal=principal,
                grant=observation_grant,
                envelope=result.observation,
                request_digest=request_digest,
                time=current_time,
                correlation_id=request.command_id,
                parent_event_id=result_event_id,
            )
            await self._notify_validated_observation(
                principal=principal,
                grant=observation_grant,
                envelope=result.observation,
                request_digest=request_digest,
                expected_time=current_time,
                correlation_id=request.command_id,
                parent_event_id=observation_event_id,
            )
        dispatch.result = result
        return result

    def command_status(
        self,
        principal: AgentPrincipal,
        *,
        command_id: str,
        requested_at: SimulationTime,
    ) -> CommandReceipt:
        """Return the latest sealed lifecycle receipt for the caller's command."""

        self._validate_principal_identity(principal)
        if requested_at != self._current_time():
            raise GatewayDispatchError(
                "command status requested_at must equal authoritative simulation time"
            )
        dispatch = self._requests.get(command_id)
        if (
            dispatch is None
            or dispatch.authentication_id != principal.authentication_id
            or dispatch.result is None
            or not dispatch.result.receipts
        ):
            raise GatewayDispatchError("command status is unavailable to this agent")
        latest = dispatch.result.receipts[-1]
        self._sync_event_indexes()
        index = self._command_phase_indexes.get(command_id)
        if index is None:
            return latest
        # Physical proof events repeat the lifecycle phase but do not carry its
        # diagnostic text. Preserve the latest explicit detail, falling back to
        # the Provider result detail exactly as the previous history scan did.
        detail = (
            index.explicit_detail
            if index.explicit_detail is not None
            else latest.detail
        )
        event = index.event
        latest = CommandReceipt(
            run_id=self._run.run_id,
            command_id=command_id,
            provider_id=event.provider_id,
            phase=index.phase,
            time=event.time,
            detail=detail,
        )
        return latest

    async def query(
        self,
        principal: AgentPrincipal,
        request: QueryRequest,
    ) -> QueryResult:
        """Authorize one uniquely identified, read-only Provider query."""

        self._validate_principal_identity(principal)
        if not isinstance(request, QueryRequest):
            raise GatewayDispatchError("query request is invalid")
        if request.run_id != principal.run_id or request.agent_id != principal.agent_id:
            raise GatewayDispatchError("query identity does not match agent principal")
        agent = self._declared_agent(principal.agent_id)
        grant = next(
            (
                item
                for item in agent.queries
                if item.query_type == request.query_type
            ),
            None,
        )
        if grant is None:
            raise GatewayDispatchError("query is not granted to this agent")
        current_time = self._current_time()
        if request.issued_at != current_time:
            raise GatewayDispatchError(
                "query issued_at must equal the authoritative simulation time"
            )

        self._sync_event_indexes()
        request_digest = self._digest(request.model_dump(mode="json"))
        previous = self._query_ids.get(request.query_id)
        if previous is not None:
            previous_authentication_id, previous_digest = previous
            if previous_authentication_id != principal.authentication_id:
                raise GatewayDispatchError(
                    "query_id was reused by a different authentication identity"
                )
            if previous_digest != request_digest:
                raise GatewayDispatchError("query_id was reused with another request")
            raise GatewayDispatchError("query_id was reused")
        self._query_ids[request.query_id] = (
            principal.authentication_id,
            request_digest,
        )
        request_event_id = self._append_query_requested(
            principal=principal,
            grant=grant,
            request=request,
            request_digest=request_digest,
            time=current_time,
        )

        try:
            self._validate_named_payload(
                grant.request_schema, request.arguments, "query request"
            )
        except Exception as error:
            self._append_query_failure(
                principal=principal,
                grant=grant,
                request=request,
                request_digest=request_digest,
                time=current_time,
                failure_class="request_validation_failed",
                parent_event_id=request_event_id,
            )
            if isinstance(error, GatewayDispatchError):
                raise
            raise GatewayDispatchError("Query request validation failed") from error

        endpoint = self._query_endpoints.get((grant.provider_id, grant.query_type))
        if endpoint is None:
            self._append_query_failure(
                principal=principal,
                grant=grant,
                request=request,
                request_digest=request_digest,
                time=current_time,
                failure_class="provider_unavailable",
                parent_event_id=request_event_id,
            )
            raise GatewayDispatchError(
                f"declared query Provider is unavailable: {grant.provider_id}"
            )
        try:
            provider_result = await asyncio.wait_for(
                endpoint.query(request), timeout=grant.timeout_ms / 1000
            )
        except asyncio.TimeoutError as error:
            self._append_query_failure(
                principal=principal,
                grant=grant,
                request=request,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="provider_timeout",
                parent_event_id=request_event_id,
            )
            raise GatewayDispatchError(
                f"query call exceeded its {grant.timeout_ms} ms contract timeout"
            ) from error
        except Exception as error:
            self._append_query_failure(
                principal=principal,
                grant=grant,
                request=request,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="provider_exception",
                parent_event_id=request_event_id,
            )
            raise GatewayDispatchError("Query Provider call failed") from error

        try:
            self._require_authority_unchanged(current_time)
            self._validate_provider_query_result(
                provider_result,
                request=request,
                grant=grant,
                expected_time=current_time,
            )
        except Exception as error:
            failure_class = (
                "authority_advanced_during_call"
                if self._current_time() != current_time
                else "query_validation_failed"
            )
            self._append_query_failure(
                principal=principal,
                grant=grant,
                request=request,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class=failure_class,
                parent_event_id=request_event_id,
            )
            if isinstance(error, GatewayDispatchError):
                raise
            raise GatewayDispatchError("Query result validation failed") from error

        result = QueryResult(
            run_id=provider_result.run_id,
            query_id=provider_result.query_id,
            agent_id=principal.agent_id,
            query_type=provider_result.query_type,
            provider_id=grant.provider_id,
            observed_at=provider_result.observed_at,
            payload_schema=grant.response_schema,
            payload=provider_result.payload,
            payload_digest=provider_result.payload_digest,
        )
        self._append_query_validated(
            principal=principal,
            grant=grant,
            request=request,
            result=result,
            request_digest=request_digest,
            time=current_time,
            parent_event_id=request_event_id,
        )
        return result

    async def observe(
        self,
        principal: AgentPrincipal,
        *,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        self._validate_principal_identity(principal)
        agent = self._declared_agent(principal.agent_id)
        grant = next(
            (
                grant
                for grant in agent.observations
                if grant.observation_id == observation_id
            ),
            None,
        )
        if grant is None:
            raise GatewayDispatchError("observation is not granted to this agent")

        current_time = self._current_time()
        if requested_at != current_time:
            raise GatewayDispatchError(
                "observation requested_at must equal the authoritative simulation time"
            )
        endpoint = self._observation_endpoints.get(grant.provider_id)
        request_digest = self._observation_request_digest(
            principal=principal,
            observation_id=observation_id,
            requested_at=requested_at,
        )
        correlation_id = f"observation.{request_digest}"
        request_event_id = self._append_observation_requested(
            principal=principal,
            grant=grant,
            observation_id=observation_id,
            request_digest=request_digest,
            time=current_time,
            correlation_id=correlation_id,
        )
        if endpoint is None:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=observation_id,
                request_digest=request_digest,
                time=current_time,
                failure_class="provider_unavailable",
            )
            raise GatewayDispatchError(
                f"declared observation Provider is unavailable: {grant.provider_id}"
            )
        try:
            envelope = await asyncio.wait_for(
                endpoint.observe(
                    run_id=self._run.run_id,
                    agent_id=principal.agent_id,
                    observation_id=observation_id,
                    requested_at=requested_at,
                ),
                timeout=grant.timeout_ms / 1000,
            )
        except asyncio.TimeoutError as error:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=observation_id,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="provider_timeout",
            )
            raise GatewayDispatchError(
                "observation call exceeded its "
                f"{grant.timeout_ms} ms contract timeout"
            ) from error
        except Exception as error:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=observation_id,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="provider_exception",
            )
            raise GatewayDispatchError("Observation Provider call failed") from error

        try:
            self._require_authority_unchanged(current_time)
        except GatewayDispatchError as error:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=observation_id,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="authority_advanced_during_call",
            )
            raise GatewayDispatchError(
                "authoritative simulation time advanced during Observation call"
            ) from error

        try:
            self._validate_observation(
                envelope,
                grant,
                agent_id=principal.agent_id,
                expected_time=requested_at,
            )
        except GatewayDispatchError:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=observation_id,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="observation_validation_failed",
            )
            raise
        except Exception as error:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=observation_id,
                request_digest=request_digest,
                time=self._failure_time(current_time),
                failure_class="observation_validation_failed",
            )
            raise GatewayDispatchError("Observation validation failed") from error

        observation_event_id = self._append_observation_validated(
            principal=principal,
            grant=grant,
            envelope=envelope,
            request_digest=request_digest,
            time=current_time,
            correlation_id=correlation_id,
            parent_event_id=request_event_id,
        )
        await self._notify_validated_observation(
            principal=principal,
            grant=grant,
            envelope=envelope,
            request_digest=request_digest,
            expected_time=current_time,
            correlation_id=correlation_id,
            parent_event_id=observation_event_id,
        )
        return envelope

    async def record_decision_summary(
        self,
        principal: AgentPrincipal,
        summary: DecisionSummaryRequest,
    ) -> DecisionSummaryReceipt:
        self._validate_principal_identity(principal)
        if not isinstance(summary, DecisionSummaryRequest):
            raise GatewayDispatchError("decision summary request is invalid")
        if (
            summary.run_id != principal.run_id
            or summary.agent_id != principal.agent_id
        ):
            raise GatewayDispatchError(
                "decision summary identity does not match agent principal"
            )

        request_digest = self._digest(summary.model_dump(mode="json"))
        summary_key = f"{principal.agent_id}:{summary.summary_id}"
        previous = self._decision_summaries.get(summary_key)
        if previous is not None:
            authentication_id, previous_digest, receipt = previous
            if (
                authentication_id != principal.authentication_id
                or previous_digest != request_digest
            ):
                raise GatewayDispatchError(
                    "summary_id was reused with different identity or content"
                )
            return receipt

        if summary.command_id is not None and summary.command_id in self._requests:
            raise GatewayDispatchError(
                "decision summary must precede its correlated tool call"
            )
        current_time = self._current_time()
        if summary.at != current_time:
            raise GatewayDispatchError(
                "decision summary time must equal authoritative simulation time"
            )

        causal_event_ids: list[str] = []
        for observation_id in summary.observation_ids:
            event_id = self._latest_agent_observation_event_id(
                agent_id=principal.agent_id,
                observation_id=observation_id,
            )
            if event_id is None:
                raise GatewayDispatchError(
                    "decision summary references an unavailable Agent observation"
                )
            causal_event_ids.append(event_id)
        correlation_id = summary.command_id or (
            f"decision.{principal.agent_id}.{summary.summary_id}"
        )
        previous_correlation_event_id = self._ledger.latest_event_id(correlation_id)
        if previous_correlation_event_id is not None:
            causal_event_ids.append(previous_correlation_event_id)
        parent_event_id = max(causal_event_ids) if causal_event_ids else None
        event_id = self._append_event(
            source=principal.agent_id,
            source_kind="agent",
            workload_id=principal.agent_id,
            event_type="agent.decision-summary",
            interaction_type="agent.decision_summary.v1",
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            causal_event_ids=tuple(causal_event_ids),
            time=current_time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "summary_id": summary.summary_id,
                "command_id": summary.command_id,
                "observation_ids_json": canonical_json_bytes(
                    list(summary.observation_ids)
                ).decode("utf-8"),
                "decision_summary": summary.decision_summary,
                "request_digest": request_digest,
            },
            payload_schema_id="agent.decision_summary.v1",
            agent_id=principal.agent_id,
            command_id=summary.command_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )
        receipt = DecisionSummaryReceipt(
            schema_version="aero-bench.decision-summary-receipt/v1",
            run_id=principal.run_id,
            agent_id=principal.agent_id,
            summary_id=summary.summary_id,
            at=current_time,
            event_id=event_id,
        )
        self._decision_summaries[summary_key] = (
            principal.authentication_id,
            request_digest,
            receipt,
        )
        return receipt

    def _latest_agent_observation_event_id(
        self,
        *,
        agent_id: str,
        observation_id: str,
    ) -> str | None:
        self._sync_event_indexes()
        return self._latest_agent_observation_event_ids.get(
            (agent_id, observation_id)
        )

    async def _notify_validated_observation(
        self,
        *,
        principal: AgentPrincipal,
        grant: ObservationGrant,
        envelope: ObservationEnvelope,
        request_digest: str,
        expected_time: SimulationTime,
        correlation_id: str,
        parent_event_id: str,
    ) -> None:
        if self._runtime_hook is None:
            return
        observation = ValidatedObservation(
            run_id=self._run.run_id,
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            observation_id=envelope.observation_id,
            time=envelope.time,
            request_digest=request_digest,
            payload_digest=envelope.payload_digest,
        )
        try:
            await self._runtime_hook.on_validated_observation(observation)
            self._require_authority_unchanged(expected_time)
        except Exception as error:
            self._append_observation_failure(
                principal=principal,
                grant=grant,
                observation_id=envelope.observation_id,
                request_digest=request_digest,
                time=self._failure_time(expected_time),
                failure_class="runtime_hook_failed",
                correlation_id=correlation_id,
                parent_event_id=parent_event_id,
            )
            raise GatewayDispatchError(
                "validated observation runtime hook failed"
            ) from error

    def _sync_event_indexes(self) -> None:
        """Ingest only ledger records appended since the last Gateway query."""

        record_count = self._ledger.record_count
        if record_count < self._indexed_record_count:
            raise GatewayDispatchError("authoritative event ledger was truncated")
        if record_count == self._indexed_record_count:
            return
        for record in self._ledger.iter_records(self._indexed_record_count):
            self._index_record(record.event)
        self._indexed_record_count = record_count

    def _index_record(self, event: RunEvent) -> None:
        """Update append-only Gateway lookup indexes for one authoritative event."""

        if event.event_type == "query.requested":
            fields = {item.name: item.value for item in event.payload}
            query_id = fields.get("query_id")
            authentication_id = fields.get("authentication_id")
            request_digest = fields.get("request_digest")
            if (
                not isinstance(query_id, str)
                or not query_id
                or not isinstance(authentication_id, str)
                or not authentication_id
                or not isinstance(request_digest, str)
                or not request_digest
            ):
                raise GatewayDispatchError(
                    "authoritative query request event is incomplete"
                )
            identity: tuple[str, str] = (authentication_id, request_digest)
            previous = self._query_ids.get(query_id)
            if previous is not None and previous != identity:
                raise GatewayDispatchError(
                    "authoritative ledger reuses query_id with another identity"
                )
            self._query_ids[query_id] = identity

        if event.command_id is not None and event.provider_id is not None:
            phase: str | None = None
            detail: object | None = None
            for attribute in event.payload:
                if attribute.name == "phase":
                    phase = attribute.value
                elif attribute.name == "detail":
                    detail = attribute.value
            if phase in _COMMAND_PHASES:
                previous = self._command_phase_indexes.get(event.command_id)
                explicit_detail = (
                    detail
                    if isinstance(detail, str)
                    else previous.explicit_detail
                    if previous is not None
                    else None
                )
                self._command_phase_indexes[event.command_id] = _CommandPhaseIndex(
                    event=event,
                    phase=phase,
                    explicit_detail=explicit_detail,
                )

        if (
            event.agent_id is not None
            and event.observation_id is not None
            and event.interaction is not None
            and event.interaction.interaction_type == "agent.observation.v1"
        ):
            self._latest_agent_observation_event_ids[
                (event.agent_id, event.observation_id)
            ] = event.event_id

    def _require_command_decision_summary(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
    ) -> None:
        if self._run.execution_scope != "formal_benchmark":
            return
        event_id = self._ledger.latest_event_id(request.command_id)
        if event_id is None:
            raise GatewayDispatchError(
                "formal command requires an explicit decision summary"
            )
        event = self._ledger.event_by_id(event_id)
        if event is None:
            raise GatewayDispatchError(
                "formal command decision summary event is unavailable"
            )
        if (
            event.agent_id != principal.agent_id
            or event.time != request.issued_at
            or event.interaction is None
            or event.interaction.interaction_type
            != "agent.decision_summary.v1"
        ):
            raise GatewayDispatchError(
                "formal command decision summary is not authoritative"
            )

    def _validate_principal(
        self, principal: AgentPrincipal, request: CommandRequest
    ) -> None:
        self._validate_principal_identity(principal)
        if request.run_id != principal.run_id:
            raise GatewayDispatchError("command run_id does not match agent principal")
        if request.agent_id != principal.agent_id:
            raise GatewayDispatchError(
                "command agent_id does not match agent principal"
            )

    def _validate_principal_identity(self, principal: AgentPrincipal) -> None:
        if not isinstance(principal, AgentPrincipal):
            raise GatewayDispatchError("authenticated agent principal is required")
        if principal.run_id != self._run.run_id:
            raise GatewayDispatchError("agent principal belongs to another run")

    def _declared_agent(self, agent_id: str):
        agent = next(
            (agent for agent in self._run.agents if agent.agent_id == agent_id),
            None,
        )
        if agent is None:
            raise GatewayDispatchError("command agent is not declared")
        return agent

    def _validate_command_receipts(
        self,
        *,
        request: CommandRequest,
        grant: ToolGrant,
        result: ToolResult,
        expected_time: SimulationTime,
    ) -> tuple[CommandReceipt, ...]:
        if not isinstance(result, ToolResult):
            raise GatewayDispatchError("Provider returned an invalid tool result")
        if result.command_id != request.command_id:
            raise GatewayDispatchError("Provider returned another command_id")
        if not result.receipts:
            raise GatewayDispatchError("Provider returned no command receipts")
        phases = tuple(receipt.phase for receipt in result.receipts)
        valid_phases = {
            ("received", "accepted"),
            ("received", "accepted", "applied", "completed"),
            ("received", "failed"),
            ("received", "accepted", "failed"),
            ("received", "accepted", "applied", "failed"),
        }
        if phases not in valid_phases:
            raise GatewayDispatchError(
                "command receipts must preserve received/accepted/applied/completed stages"
            )
        for receipt in result.receipts:
            if receipt.run_id != request.run_id:
                raise GatewayDispatchError("command receipt belongs to another run")
            if receipt.command_id != request.command_id:
                raise GatewayDispatchError("command receipt belongs to another command")
            if receipt.provider_id != grant.provider_id:
                raise GatewayDispatchError(
                    "command receipt came from the wrong Provider"
                )
            if receipt.time != expected_time:
                raise GatewayDispatchError(
                    "command receipt time must equal the authoritative simulation time"
                )
        return result.receipts

    def _validate_provider_events(
        self,
        *,
        events: tuple[ProviderEvent, ...],
        request: CommandRequest,
        grant: ToolGrant,
        receipts: tuple[CommandReceipt, ...],
        expected_time: SimulationTime,
    ) -> tuple[ProviderEvent, ...]:
        if events and "applied" not in {receipt.phase for receipt in receipts}:
            raise GatewayDispatchError(
                "Provider events require an applied command receipt"
            )
        fingerprints: set[bytes] = set()
        for event in events:
            if not isinstance(event, ProviderEvent):
                raise GatewayDispatchError("Provider returned an invalid command event")
            if event.event_id in RESERVED_RUNTIME_EVENT_TYPES:
                raise GatewayDispatchError(
                    "Provider command event uses a reserved event type"
                )
            if event.provider_id != grant.provider_id:
                raise GatewayDispatchError("command event came from the wrong Provider")
            if event.time != expected_time:
                raise GatewayDispatchError(
                    "command event time must equal the authoritative simulation time"
                )
            payload = self._named_payload(event.payload)
            schema_id = payload.get("schema_id")
            if not isinstance(schema_id, str) or not schema_id:
                raise GatewayDispatchError(
                    "command event payload does not identify its schema"
                )
            if payload.get("command_id") != request.command_id:
                raise GatewayDispatchError(
                    "command event payload belongs to another command"
                )
            if "run_id" in payload and payload["run_id"] != request.run_id:
                raise GatewayDispatchError("command event belongs to another run")
            if "provider_id" in payload and payload["provider_id"] != grant.provider_id:
                raise GatewayDispatchError(
                    "command event payload names another Provider"
                )
            fingerprint = canonical_json_bytes(event.model_dump(mode="json"))
            if fingerprint in fingerprints:
                raise GatewayDispatchError(
                    "Provider returned a duplicate command event"
                )
            fingerprints.add(fingerprint)
        return events

    def _validate_result_payload(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
        grant: ToolGrant,
        result: ToolResult,
        expected_time: SimulationTime,
    ) -> None:
        if result.receipts[-1].phase == "completed":
            self._validate_named_payload(
                grant.response_schema, result.response, "tool response"
            )
        elif result.response:
            raise GatewayDispatchError(
                "failed tool calls cannot return a success response"
            )
        if result.observation is None:
            return
        agent = self._declared_agent(principal.agent_id)
        observation_grant = next(
            (
                item
                for item in agent.observations
                if item.observation_id == result.observation.observation_id
            ),
            None,
        )
        if observation_grant is None:
            raise GatewayDispatchError("tool returned an ungranted observation")
        self._validate_observation(
            result.observation,
            observation_grant,
            agent_id=principal.agent_id,
            expected_time=expected_time,
        )

    def _validate_observation(
        self,
        envelope: ObservationEnvelope,
        grant: ObservationGrant,
        *,
        agent_id: str,
        expected_time: SimulationTime,
    ) -> None:
        if not isinstance(envelope, ObservationEnvelope):
            raise GatewayDispatchError("Provider returned an invalid observation")
        if envelope.run_id != self._run.run_id:
            raise GatewayDispatchError("observation belongs to another run")
        if envelope.agent_id != agent_id:
            raise GatewayDispatchError("observation identity does not match its grant")
        if envelope.observation_id != grant.observation_id:
            raise GatewayDispatchError("observation identity does not match its grant")
        if envelope.payload_schema != grant.schema_file:
            raise GatewayDispatchError("observation schema does not match its grant")
        if envelope.time != expected_time:
            raise GatewayDispatchError(
                "observation time does not match the authoritative request or receipt"
            )
        self._validate_named_payload(grant.schema_file, envelope.payload, "observation")
        payload = self._named_payload(envelope.payload)
        actual_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if actual_digest != envelope.payload_digest:
            raise GatewayDispatchError("observation payload digest is invalid")

    def _validate_provider_query_result(
        self,
        result: ProviderQueryResult,
        *,
        request: QueryRequest,
        grant: QueryGrant,
        expected_time: SimulationTime,
    ) -> None:
        if not isinstance(result, ProviderQueryResult):
            raise GatewayDispatchError("Provider returned an invalid query result")
        if result.run_id != request.run_id:
            raise GatewayDispatchError("query result belongs to another run")
        if result.query_id != request.query_id:
            raise GatewayDispatchError("Provider returned another query_id")
        if result.query_type != request.query_type:
            raise GatewayDispatchError("query result type does not match its grant")
        if result.observed_at != expected_time:
            raise GatewayDispatchError(
                "query result time must equal the authoritative simulation time"
            )
        self._validate_named_payload(
            grant.response_schema, result.payload, "query response"
        )
        payload = self._named_payload(result.payload)
        actual_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if actual_digest != result.payload_digest:
            raise GatewayDispatchError("query payload digest is invalid")

    def _validate_named_payload(
        self,
        schema_reference: FileRef,
        values: tuple[NamedValue, ...],
        label: str,
    ) -> None:
        if any(item.name in _FORBIDDEN_REASONING_FIELDS for item in values):
            raise GatewayDispatchError(
                f"{label} cannot contain hidden model reasoning fields"
            )
        schema = self._reader.validate_schema(schema_reference)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(self._named_payload(values)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path) or "<root>"
            raise GatewayDispatchError(
                f"{label} schema validation failed at {location}: {first.message}"
            )

    @staticmethod
    def _named_payload(values: tuple[NamedValue, ...]) -> dict[str, object]:
        payload = {item.name: item.value for item in values}
        if len(payload) != len(values):
            raise GatewayDispatchError("payload field names must be unique")
        return payload

    @staticmethod
    def _request_digest(request: CommandRequest) -> str:
        return hashlib.sha256(
            canonical_json_bytes(request.model_dump(mode="json"))
        ).hexdigest()

    @staticmethod
    def _observation_request_digest(
        *,
        principal: AgentPrincipal,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "run_id": principal.run_id,
                    "agent_id": principal.agent_id,
                    "authentication_id": principal.authentication_id,
                    "observation_id": observation_id,
                    "requested_at": requested_at.model_dump(mode="json"),
                }
            )
        ).hexdigest()

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    def _append_command_issued(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
        grant: ToolGrant,
        request_digest: str,
        time: SimulationTime,
    ) -> str:
        return self._append_event(
            source=principal.agent_id,
            source_kind="agent",
            workload_id=principal.agent_id,
            event_type="command.issued",
            interaction_type="agent.tool_call.v1",
            correlation_id=request.command_id,
            parent_event_id=self._ledger.latest_event_id(request.command_id),
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "command_id": request.command_id,
                "tool_id": request.tool_id,
                "provider_id": grant.provider_id,
                "request_digest": request_digest,
                "request_schema_digest": grant.request_schema.sha256,
                "response_schema_digest": grant.response_schema.sha256,
                "arguments_json": canonical_json_bytes(
                    [item.model_dump(mode="json") for item in request.arguments]
                ).decode("utf-8"),
            },
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            command_id=request.command_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_gateway_dispatch(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
        grant: ToolGrant,
        request_digest: str,
        time: SimulationTime,
        parent_event_id: str,
    ) -> str:
        return self._append_event(
            event_type="gateway.dispatch",
            interaction_type="gateway.dispatch.v1",
            correlation_id=request.command_id,
            parent_event_id=parent_event_id,
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "command_id": request.command_id,
                "tool_id": request.tool_id,
                "provider_id": grant.provider_id,
                "request_digest": request_digest,
                "timeout_ms": grant.timeout_ms,
            },
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            command_id=request.command_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_command_receipts(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
        grant: ToolGrant,
        request_digest: str,
        receipts: tuple[CommandReceipt, ...],
        parent_event_id: str | None,
    ) -> tuple[str, ...]:
        event_ids: list[str] = []
        parent = parent_event_id
        for receipt in receipts:
            event_id = self._append_event(
                source=receipt.provider_id,
                source_kind="provider",
                workload_id=receipt.provider_id,
                event_type="command.receipt",
                interaction_type="provider.command.v1",
                correlation_id=request.command_id,
                parent_event_id=parent,
                time=receipt.time,
                payload={
                    "run_id": principal.run_id,
                    "agent_id": principal.agent_id,
                    "authentication_id": principal.authentication_id,
                    "command_id": request.command_id,
                    "tool_id": request.tool_id,
                    "provider_id": grant.provider_id,
                    "request_digest": request_digest,
                    "phase": receipt.phase,
                    "receipt_json": canonical_json_bytes(
                        receipt.model_dump(mode="json")
                    ).decode("utf-8"),
                },
                agent_id=principal.agent_id,
                provider_id=grant.provider_id,
                command_id=request.command_id,
                visibility=self._agent_private_visibility(principal.agent_id),
            )
            event_ids.append(event_id)
            parent = event_id
        return tuple(event_ids)

    def _append_provider_events(
        self,
        events: tuple[ProviderEvent, ...],
        *,
        request: CommandRequest,
        parent_event_id: str | None,
    ) -> tuple[str, ...]:
        event_ids: list[str] = []
        parent = parent_event_id
        for event in events:
            if event.event_id.endswith(".applied.physical"):
                interaction_type: AgentInteractionType = "mavlink.command_ack.v1"
            elif event.event_id.endswith(".physical"):
                interaction_type = "physical.effect.v1"
            else:
                interaction_type = "provider.command.v1"
            if event.event_id.endswith(".applied.physical"):
                command_event_id = self._append_event(
                    source=event.provider_id,
                    source_kind="provider",
                    workload_id=event.provider_id,
                    event_type=f"{event.event_id}.mavlink-command",
                    interaction_type="mavlink.command.v1",
                    correlation_id=request.command_id,
                    parent_event_id=parent,
                    time=event.time,
                    payload={item.name: item.value for item in event.payload},
                    payload_schema_id=event.payload_schema_id,
                    agent_id=request.agent_id,
                    provider_id=event.provider_id,
                    command_id=request.command_id,
                    visibility=self._agent_private_visibility(request.agent_id),
                )
                event_ids.append(command_event_id)
                parent = command_event_id
            event_id = self._append_event(
                source=event.provider_id,
                source_kind="provider",
                workload_id=event.provider_id,
                event_type=event.event_id,
                interaction_type=interaction_type,
                correlation_id=request.command_id,
                parent_event_id=parent,
                time=event.time,
                payload={item.name: item.value for item in event.payload},
                payload_schema_id=event.payload_schema_id,
                agent_id=request.agent_id,
                provider_id=event.provider_id,
                command_id=request.command_id,
                visibility=self._agent_private_visibility(request.agent_id),
            )
            event_ids.append(event_id)
            parent = event_id
        return tuple(event_ids)

    def _append_command_validated(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
        grant: ToolGrant,
        request_digest: str,
        result: ToolResult,
        time: SimulationTime,
        parent_event_id: str | None,
        causal_event_ids: tuple[str, ...],
    ) -> str:
        payload = {
            "run_id": principal.run_id,
            "agent_id": principal.agent_id,
            "authentication_id": principal.authentication_id,
            "command_id": request.command_id,
            "tool_id": request.tool_id,
            "provider_id": grant.provider_id,
            "request_digest": request_digest,
            "result_digest": self._digest(result.model_dump(mode="json")),
            "response_digest": self._digest(
                [item.model_dump(mode="json") for item in result.response]
            ),
            "response_schema_digest": grant.response_schema.sha256,
            "response_json": canonical_json_bytes(
                [item.model_dump(mode="json") for item in result.response]
            ).decode("utf-8"),
            "result_json": canonical_json_bytes(
                result.model_dump(mode="json")
            ).decode("utf-8"),
            "outcome": result.receipts[-1].phase,
        }
        if result.observation is not None:
            payload["observation_id"] = result.observation.observation_id
        return self._append_event(
            event_type="command.validated",
            interaction_type="agent.tool_result.v1",
            correlation_id=request.command_id,
            parent_event_id=parent_event_id,
            causal_event_ids=causal_event_ids,
            time=time,
            payload=payload,
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            command_id=request.command_id,
            observation_id=(
                result.observation.observation_id
                if result.observation is not None
                else None
            ),
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_command_failure(
        self,
        *,
        principal: AgentPrincipal,
        request: CommandRequest,
        grant: ToolGrant,
        request_digest: str,
        time: SimulationTime,
        failure_class: str,
    ) -> None:
        self._append_event(
            event_type="gateway.failure",
            interaction_type="agent.tool_result.v1",
            correlation_id=request.command_id,
            parent_event_id=self._ledger.latest_event_id(request.command_id),
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "command_id": request.command_id,
                "tool_id": request.tool_id,
                "provider_id": grant.provider_id,
                "request_digest": request_digest,
                "failure_class": failure_class,
            },
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            command_id=request.command_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_query_requested(
        self,
        *,
        principal: AgentPrincipal,
        grant: QueryGrant,
        request: QueryRequest,
        request_digest: str,
        time: SimulationTime,
    ) -> str:
        return self._append_event(
            source=principal.agent_id,
            source_kind="agent",
            workload_id=principal.agent_id,
            event_type="query.requested",
            interaction_type="agent.query_call.v1",
            correlation_id=request.query_id,
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "query_id": request.query_id,
                "query_type": request.query_type,
                "provider_id": grant.provider_id,
                "request_schema_digest": grant.request_schema.sha256,
                "response_schema_digest": grant.response_schema.sha256,
                "request_digest": request_digest,
                "request_json": canonical_json_bytes(
                    request.model_dump(mode="json")
                ).decode("utf-8"),
            },
            payload_schema_id="agent.query_call.v1",
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            query_id=request.query_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_query_validated(
        self,
        *,
        principal: AgentPrincipal,
        grant: QueryGrant,
        request: QueryRequest,
        result: QueryResult,
        request_digest: str,
        time: SimulationTime,
        parent_event_id: str,
    ) -> str:
        return self._append_event(
            source=grant.provider_id,
            source_kind="provider",
            workload_id=grant.provider_id,
            event_type="query.validated",
            interaction_type="agent.query_result.v1",
            correlation_id=request.query_id,
            parent_event_id=parent_event_id,
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "query_id": request.query_id,
                "query_type": request.query_type,
                "provider_id": grant.provider_id,
                "request_schema_digest": grant.request_schema.sha256,
                "response_schema_digest": grant.response_schema.sha256,
                "request_digest": request_digest,
                "payload_digest": result.payload_digest,
                "outcome": "succeeded",
                "result_json": canonical_json_bytes(
                    result.model_dump(mode="json")
                ).decode("utf-8"),
            },
            payload_schema_id="agent.query_result.v1",
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            query_id=request.query_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_query_failure(
        self,
        *,
        principal: AgentPrincipal,
        grant: QueryGrant,
        request: QueryRequest,
        request_digest: str,
        time: SimulationTime,
        failure_class: str,
        parent_event_id: str,
    ) -> None:
        self._append_event(
            event_type="gateway.failure",
            interaction_type="agent.query_result.v1",
            correlation_id=request.query_id,
            parent_event_id=parent_event_id,
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "query_id": request.query_id,
                "query_type": request.query_type,
                "provider_id": grant.provider_id,
                "request_schema_digest": grant.request_schema.sha256,
                "response_schema_digest": grant.response_schema.sha256,
                "request_digest": request_digest,
                "failure_class": failure_class,
                "outcome": "failed",
            },
            payload_schema_id="agent.query_result.v1",
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            query_id=request.query_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_observation_requested(
        self,
        *,
        principal: AgentPrincipal,
        grant: ObservationGrant,
        observation_id: str,
        request_digest: str,
        time: SimulationTime,
        correlation_id: str,
    ) -> str:
        return self._append_event(
            source=principal.agent_id,
            source_kind="agent",
            workload_id=principal.agent_id,
            event_type="observation.requested",
            correlation_id=correlation_id,
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "observation_id": observation_id,
                "provider_id": grant.provider_id,
                "schema_digest": grant.schema_file.sha256,
                "request_digest": request_digest,
            },
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            observation_id=observation_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_observation_validated(
        self,
        *,
        principal: AgentPrincipal,
        grant: ObservationGrant,
        envelope: ObservationEnvelope,
        request_digest: str,
        time: SimulationTime,
        correlation_id: str,
        parent_event_id: str,
    ) -> str:
        observation_payload = self._named_payload(envelope.payload)
        payload_json = canonical_json_bytes(observation_payload).decode("utf-8")
        event_payload = {
            "run_id": principal.run_id,
            "agent_id": principal.agent_id,
            "authentication_id": principal.authentication_id,
            "observation_id": envelope.observation_id,
            "provider_id": grant.provider_id,
            "schema_digest": grant.schema_file.sha256,
            "schema_path": grant.schema_file.path,
            "request_digest": request_digest,
            "payload_digest": envelope.payload_digest,
            "payload_json": payload_json,
            "envelope_json": canonical_json_bytes(
                envelope.model_dump(mode="json")
            ).decode("utf-8"),
        }
        observation_event_id = self._append_event(
            source=grant.provider_id,
            source_kind="provider",
            workload_id=grant.provider_id,
            event_type="observation.validated",
            interaction_type="agent.observation.v1",
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            time=time,
            payload=event_payload,
            payload_schema_id="agent.observation.v1",
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            observation_id=envelope.observation_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )
        frame_id = observation_payload.get("frame_id")
        if not isinstance(frame_id, str) or not frame_id:
            return observation_event_id
        return self._append_event(
            source=grant.provider_id,
            source_kind="provider",
            workload_id=grant.provider_id,
            event_type="sensor.frame-ref",
            interaction_type="sensor.frame_ref.v2",
            correlation_id=correlation_id,
            parent_event_id=observation_event_id,
            time=time,
            payload=event_payload,
            payload_schema_id="sensor.frame_ref.v2",
            frame_id=frame_id,
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            observation_id=envelope.observation_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_observation_failure(
        self,
        *,
        principal: AgentPrincipal,
        grant: ObservationGrant,
        observation_id: str,
        request_digest: str,
        time: SimulationTime,
        failure_class: str,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> None:
        correlation = correlation_id or f"observation.{request_digest}"
        self._append_event(
            event_type="gateway.failure",
            correlation_id=correlation,
            parent_event_id=(
                parent_event_id or self._ledger.latest_event_id(correlation)
            ),
            time=time,
            payload={
                "run_id": principal.run_id,
                "agent_id": principal.agent_id,
                "authentication_id": principal.authentication_id,
                "observation_id": observation_id,
                "provider_id": grant.provider_id,
                "schema_digest": grant.schema_file.sha256,
                "request_digest": request_digest,
                "failure_class": failure_class,
            },
            agent_id=principal.agent_id,
            provider_id=grant.provider_id,
            observation_id=observation_id,
            visibility=self._agent_private_visibility(principal.agent_id),
        )

    def _append_event(
        self,
        *,
        event_type: str,
        time: SimulationTime,
        payload: dict[str, object],
        source: str = "gateway",
        source_kind: RunEventSourceKind = "gateway",
        workload_id: str = "gateway",
        payload_schema_id: str | None = None,
        interaction_type: AgentInteractionType | None = None,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
        causal_event_ids: tuple[str, ...] = (),
        visibility: tuple[RunEventAudience, ...] | None = None,
        frame_id: str | None = None,
        agent_id: str | None = None,
        provider_id: str | None = None,
        vehicle_id: str | None = None,
        entity_id: str | None = None,
        command_id: str | None = None,
        query_id: str | None = None,
        observation_id: str | None = None,
    ) -> str:
        self._sync_event_indexes()
        previous = self._ledger.latest_record
        if previous is not None and self._time_before(time, previous.event.time):
            raise GatewayDispatchError(
                "gateway event time moved backwards from the ledger tail"
            )
        record = self._ledger.append_event(
            source=source,
            source_kind=source_kind,
            workload_id=workload_id,
            event_type=event_type,
            time=time,
            payload=tuple(
                NamedValue(name=name, value=value)
                for name, value in sorted(payload.items())
            ),
            payload_schema_id=payload_schema_id,
            interaction_type=interaction_type,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            causal_event_ids=causal_event_ids,
            visibility=visibility,
            frame_id=frame_id,
            agent_id=agent_id,
            provider_id=provider_id,
            vehicle_id=vehicle_id,
            entity_id=entity_id,
            command_id=command_id,
            query_id=query_id,
            observation_id=observation_id,
        )
        if (
            self._run.execution_scope == "executor_validation"
            and self._run.scenario.task.package_id == "urban.uav-recovery-demo.v1"
            and interaction_type in PUBLIC_AGENT_INTERACTION_FIELDS
            and agent_id is not None
        ):
            allowed = PUBLIC_AGENT_INTERACTION_FIELDS[interaction_type]
            self._ledger.append_event(
                source=source, source_kind=source_kind, workload_id=workload_id,
                event_type="public.agent-interaction", time=time,
                payload=tuple(NamedValue(name=name, value=value) for name, value in sorted(payload.items()) if name in allowed),
                payload_schema_id=interaction_type, interaction_type=interaction_type,
                correlation_id=correlation_id, parent_event_id=record.event.event_id,
                visibility=(RunEventAudience(scope="public", audience_id=None),),
                agent_id=agent_id, provider_id=provider_id, command_id=command_id,
                query_id=query_id,
                observation_id=observation_id,
            )
        self._sync_event_indexes()
        return record.event.event_id

    @staticmethod
    def _agent_private_visibility(
        agent_id: str,
    ) -> tuple[RunEventAudience, ...]:
        return (
            RunEventAudience(scope="agent", audience_id=agent_id),
            RunEventAudience(scope="private", audience_id=None),
        )

    def _require_authority_unchanged(self, expected_time: SimulationTime) -> None:
        current_time = self._current_time()
        if current_time != expected_time:
            raise GatewayDispatchError(
                "authoritative simulation time advanced during Provider call"
            )

    def _failure_time(self, expected_time: SimulationTime) -> SimulationTime:
        """Return a safe failure timestamp after an awaited external call."""
        try:
            current_time = SimulationTime.model_validate(self._authoritative_time())
        except Exception:
            current_time = expected_time
        if self._time_before(current_time, expected_time):
            current_time = expected_time
        latest_record = self._ledger.latest_record
        if latest_record is not None:
            ledger_time = latest_record.event.time
            if self._time_before(current_time, ledger_time):
                current_time = ledger_time
        return current_time

    def _current_time(self) -> SimulationTime:
        try:
            current = SimulationTime.model_validate(self._authoritative_time())
        except Exception as error:
            raise GatewayDispatchError(
                "authoritative simulation time is unavailable"
            ) from error
        latest_record = self._ledger.latest_record
        if latest_record is not None:
            previous = latest_record.event.time
            if self._time_before(current, previous):
                raise GatewayDispatchError(
                    "authoritative simulation time moved backwards"
                )
        return current

    @staticmethod
    def _time_before(left: SimulationTime, right: SimulationTime) -> bool:
        return left.tick < right.tick or left.sim_time_ns < right.sim_time_ns


__all__ = ["GatewayDispatchError", "GatewayDispatcher"]
