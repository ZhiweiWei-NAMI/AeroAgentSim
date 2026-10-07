from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import (
    FileRef,
    Identifier,
    NamedValue,
    Sha256,
    StrictModel,
)
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    ProviderEvent,
    SimulationTime,
)
from aero_bench.serialization import canonical_json_bytes


class ToolContract(StrictModel):
    tool_id: Identifier
    request_schema: FileRef
    response_schema: FileRef
    idempotent: bool


class AgentPrincipal(StrictModel):
    """Authenticated, non-secret identity supplied by the transport boundary."""

    run_id: Sha256
    agent_id: Identifier
    authentication_id: Identifier


class DecisionSummaryRequest(StrictModel):
    """One explicit Agent-authored decision summary, never hidden reasoning."""

    schema_version: Literal["aero-bench.decision-summary-request/v1"]
    run_id: Sha256
    agent_id: Identifier
    summary_id: Identifier
    at: SimulationTime
    decision_summary: Annotated[str, Field(min_length=1, max_length=4096)]
    command_id: Identifier | None = None
    observation_ids: tuple[Identifier, ...] = ()

    @field_validator("decision_summary")
    @classmethod
    def explicit_nonblank_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decision_summary must contain explicit text")
        return value

    @model_validator(mode="after")
    def canonical_observation_references(self) -> "DecisionSummaryRequest":
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("decision summary observation_ids must be unique")
        if self.observation_ids != tuple(sorted(self.observation_ids)):
            raise ValueError("decision summary observation_ids must be sorted")
        return self


class DecisionSummaryReceipt(StrictModel):
    schema_version: Literal["aero-bench.decision-summary-receipt/v1"]
    run_id: Sha256
    agent_id: Identifier
    summary_id: Identifier
    at: SimulationTime
    event_id: Identifier


class ObservationEnvelope(StrictModel):
    run_id: Sha256
    agent_id: Identifier
    observation_id: Identifier
    time: SimulationTime
    payload_schema: FileRef
    payload: tuple[NamedValue, ...]
    payload_digest: Sha256

    @model_validator(mode="after")
    def unique_payload_fields(self) -> "ObservationEnvelope":
        names = [item.name for item in self.payload]
        if len(names) != len(set(names)):
            raise ValueError("observation payload names must be unique")
        return self


class QueryRequest(StrictModel):
    """One Agent-authored, side-effect-free query invocation."""

    run_id: Sha256
    query_id: Identifier
    agent_id: Identifier
    query_type: Identifier
    issued_at: SimulationTime
    arguments: tuple[NamedValue, ...] = ()

    @model_validator(mode="after")
    def unique_arguments(self) -> "QueryRequest":
        names = [item.name for item in self.arguments]
        if len(names) != len(set(names)):
            raise ValueError("query argument names must be unique")
        return self


class ProviderQueryResult(StrictModel):
    """Provider-authored query payload before the Gateway binds its grant."""

    run_id: Sha256
    query_id: Identifier
    query_type: Identifier
    observed_at: SimulationTime
    payload: tuple[NamedValue, ...]
    payload_digest: Sha256

    @model_validator(mode="after")
    def unique_payload_fields(self) -> "ProviderQueryResult":
        names = [item.name for item in self.payload]
        if len(names) != len(set(names)):
            raise ValueError("query payload names must be unique")
        return self


class QueryResult(StrictModel):
    """Grant-bound Provider result returned by the Gateway to one Agent."""

    run_id: Sha256
    query_id: Identifier
    agent_id: Identifier
    query_type: Identifier
    provider_id: Identifier
    observed_at: SimulationTime
    payload_schema: FileRef
    payload: tuple[NamedValue, ...]
    payload_digest: Sha256

    @model_validator(mode="after")
    def unique_payload_fields(self) -> "QueryResult":
        names = [item.name for item in self.payload]
        if len(names) != len(set(names)):
            raise ValueError("query payload names must be unique")
        payload = {item.name: item.value for item in self.payload}
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != self.payload_digest:
            raise ValueError("query payload digest is invalid")
        return self


class ToolResult(StrictModel):
    command_id: Identifier
    receipts: tuple[CommandReceipt, ...]
    response: tuple[NamedValue, ...]
    observation: ObservationEnvelope | None = None

    @model_validator(mode="after")
    def unique_response_fields(self) -> "ToolResult":
        names = [item.name for item in self.response]
        if len(names) != len(set(names)):
            raise ValueError("tool response names must be unique")
        return self


class ProviderToolResult(StrictModel):
    """Harness-internal Provider outcome with a separate public result."""

    result: ToolResult
    events: tuple[ProviderEvent, ...] = ()


class ProviderToolEndpoint(Protocol):
    async def invoke(self, request: CommandRequest) -> ProviderToolResult: ...


class ProviderObservationEndpoint(Protocol):
    async def observe(
        self,
        *,
        run_id: str,
        agent_id: str,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope: ...


class ProviderQueryEndpoint(Protocol):
    async def query(self, request: QueryRequest) -> ProviderQueryResult: ...


class ToolGateway(Protocol):
    async def invoke(
        self, principal: AgentPrincipal, request: CommandRequest
    ) -> ToolResult: ...

    async def observe(
        self,
        principal: AgentPrincipal,
        *,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope: ...

    async def query(
        self, principal: AgentPrincipal, request: QueryRequest
    ) -> QueryResult: ...

    async def record_decision_summary(
        self,
        principal: AgentPrincipal,
        summary: DecisionSummaryRequest,
    ) -> DecisionSummaryReceipt: ...
