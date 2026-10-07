from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import sys
import traceback
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import Field, StrictStr, ValidationError

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.gateway.contracts import (
    AgentPrincipal,
    DecisionSummaryRequest,
    QueryRequest,
)
from aero_bench.gateway.dispatcher import GatewayDispatchError, GatewayDispatcher
from aero_bench.providers.rpc import parse_json_object
from aero_bench.runtime.contracts import (
    AgentTurnCompletion,
    CommandRequest,
    SimulationTime,
)
from aero_bench.runtime.control import RuntimeControlOperation
from aero_bench.runtime.harness import HarnessCoordinator, HarnessRuntimeError
from aero_bench.runtime.events import ProcessStreamChunk
from aero_bench.serialization import canonical_json_bytes


_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIALS_ENV: Final[str] = "AERO_BENCH_AGENT_CREDENTIALS"
_STREAM_INGEST_TOKEN_ENV: Final[str] = "AERO_BENCH_STREAM_INGEST_TOKEN"
_RUNTIME_CONTROL_TOKEN_ENV: Final[str] = "AERO_BENCH_RUNTIME_CONTROL_TOKEN"


class GatewayServiceError(RuntimeError):
    """A non-secret, stable error that can cross the Gateway wire boundary."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class GatewayErrorBody(StrictModel):
    code: Identifier
    detail: str


class GatewayErrorResponse(StrictModel):
    error: GatewayErrorBody


class GatewayProbeResponse(StrictModel):
    schema_version: Literal["aero-bench.gateway-probe/v1"]
    status: Literal["ready"]
    run_id: Sha256
    current: SimulationTime


class _CommandInvokeRequest(StrictModel):
    token: StrictStr
    command: CommandRequest


class _CommandStatusRequest(StrictModel):
    token: StrictStr
    command_id: Identifier
    requested_at: SimulationTime


class _ObservationGetRequest(StrictModel):
    token: StrictStr
    observation_id: Identifier
    requested_at: SimulationTime


class _QueryInvokeRequest(StrictModel):
    token: StrictStr
    query: QueryRequest


class _DecisionSummaryRecordRequest(StrictModel):
    token: StrictStr
    summary: DecisionSummaryRequest


class _ProcessStreamIngestRequest(StrictModel):
    token: StrictStr
    chunk: ProcessStreamChunk


class _ProcessStreamIngestResponse(StrictModel):
    schema_version: Literal["aero-bench.process-stream-ingest-response/v1"]
    run_id: Sha256
    workload_id: Identifier
    stream: Literal["stdout", "stderr"]
    sequence: int
    event_id: Identifier
    closed: bool


class _RuntimeControlRequest(StrictModel):
    token: StrictStr
    action: RuntimeControlOperation
    control_id: Identifier | None


class _RuntimeProjectionRequest(StrictModel):
    token: StrictStr
    after_scene_tick: Annotated[int, Field(ge=0)]
    after_event_sequence: Annotated[int, Field(ge=-1)]
    max_scene_states: Annotated[int, Field(ge=1, le=16)]
    max_events: Annotated[int, Field(ge=1, le=256)]


class _TurnCompleteRequest(StrictModel):
    token: StrictStr
    completion: AgentTurnCompletion


class AgentCredentialStore:
    """Strict, run-bound lookup for the credentials injected into Agents.

    The store accepts the canonical JSON object injected as
    ``AERO_BENCH_AGENT_CREDENTIALS``. It never exposes the token object after
    construction; authentication returns only an ``AgentPrincipal``.
    """

    def __init__(self, run: ResolvedRunSpec, credentials_json: bytes | str):
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("AgentCredentialStore.run must be ResolvedRunSpec")
        self._run_id = run.run_id
        self._tokens = self._parse_credentials(run, credentials_json)

    @classmethod
    def from_canonical_json(
        cls, run: ResolvedRunSpec, credentials_json: bytes | str
    ) -> "AgentCredentialStore":
        return cls(run, credentials_json)

    @classmethod
    def from_environment(
        cls,
        run: ResolvedRunSpec,
        environment: Mapping[str, str],
    ) -> "AgentCredentialStore":
        try:
            credentials_json = environment[_CREDENTIALS_ENV]
        except (KeyError, TypeError) as error:
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials are unavailable"
            ) from error
        if not isinstance(credentials_json, str) or not credentials_json:
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials are unavailable"
            )
        return cls(run, credentials_json)

    @property
    def run_id(self) -> str:
        return self._run_id

    def authenticate(self, token: str) -> AgentPrincipal:
        if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
            raise GatewayServiceError("authentication.failed", "authentication failed")

        matched_agent_id: str | None = None
        for agent_id, expected_token in self._tokens.items():
            # Compare every stored token so valid-token lookup does not reveal
            # which agent was found through an early return.
            if hmac.compare_digest(token, expected_token):
                matched_agent_id = agent_id
        if matched_agent_id is None:
            raise GatewayServiceError("authentication.failed", "authentication failed")
        authentication_id = "auth." + hashlib.sha256(token.encode("ascii")).hexdigest()
        return AgentPrincipal(
            run_id=self._run_id,
            agent_id=matched_agent_id,
            authentication_id=authentication_id,
        )

    @staticmethod
    def _parse_credentials(
        run: ResolvedRunSpec, credentials_json: bytes | str
    ) -> dict[str, str]:
        if isinstance(credentials_json, str):
            encoded = credentials_json.encode("utf-8")
        elif isinstance(credentials_json, bytes):
            encoded = credentials_json
        else:
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials are invalid"
            )

        try:
            value = parse_json_object(encoded)
            canonical = canonical_json_bytes(value)
        except Exception as error:
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials are invalid"
            ) from error
        if encoded not in (canonical, canonical + b"\n"):
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials are not canonical JSON"
            )

        expected_agent_ids = {agent.agent_id for agent in run.agents}
        if set(value) != expected_agent_ids:
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials do not match the run"
            )

        tokens: dict[str, str] = {}
        for agent_id in sorted(expected_agent_ids):
            token = value[agent_id]
            if (
                not isinstance(token, str)
                or _TOKEN_PATTERN.fullmatch(token) is None
                or token == "0" * 64
            ):
                raise GatewayServiceError(
                    "credentials.invalid", "agent credentials are invalid"
                )
            tokens[agent_id] = token
        if len(set(tokens.values())) != len(tokens):
            raise GatewayServiceError(
                "credentials.invalid", "agent credentials are not unique"
            )
        return tokens


class _GatewayRoundGate:
    """Serialize turn submission around concurrent Provider-facing calls."""

    def __init__(self, *, current: SimulationTime):
        self._condition = asyncio.Condition()
        self._active_operations = 0
        self._turn_in_progress = False
        self._closed = False
        self._round_at = current
        self._submitted_agents: set[str] = set()

    async def begin_operation(
        self,
        *,
        agent_id: str,
        at: SimulationTime,
    ) -> None:
        async with self._condition:
            while self._turn_in_progress:
                await self._condition.wait()
            if self._closed:
                raise GatewayServiceError(
                    "gateway.closed", "Gateway operations are closed"
                )
            if at != self._round_at:
                raise GatewayServiceError(
                    "turn.stale", "Gateway call is not for the current turn"
                )
            if agent_id in self._submitted_agents:
                raise GatewayServiceError(
                    "turn.submitted", "Agent turn is already submitted"
                )
            self._active_operations += 1

    async def begin_turn(self, *, agent_id: str, at: SimulationTime) -> None:
        async with self._condition:
            while self._turn_in_progress:
                await self._condition.wait()
            if self._closed:
                raise GatewayServiceError(
                    "gateway.closed", "Gateway operations are closed"
                )
            if at != self._round_at:
                raise GatewayServiceError(
                    "turn.stale", "Turn completion is not for the current turn"
                )
            if agent_id in self._submitted_agents:
                raise GatewayServiceError(
                    "turn.submitted", "Agent turn is already submitted"
                )
            self._turn_in_progress = True
            try:
                while self._active_operations:
                    await self._condition.wait()
            except BaseException:
                self._turn_in_progress = False
                self._condition.notify_all()
                raise

    async def end_operation(self) -> None:
        async with self._condition:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._condition.notify_all()

    async def abort_turn(self, *, permanent: bool) -> None:
        async with self._condition:
            if permanent:
                self._closed = True
            self._turn_in_progress = False
            self._condition.notify_all()

    async def end_turn(
        self,
        *,
        agent_id: str,
        status: str,
        at: SimulationTime,
        permanent: bool,
    ) -> None:
        async with self._condition:
            if status == "waiting":
                self._submitted_agents.add(agent_id)
            elif status == "advanced":
                self._round_at = at
                self._submitted_agents.clear()
            elif status == "terminated":
                self._closed = True
                self._submitted_agents.clear()
            if permanent:
                self._closed = True
            self._turn_in_progress = False
            self._condition.notify_all()


class GatewayRpcService:
    """Strict JSON-line Gateway handler for Agent-to-Harness calls."""

    def __init__(
        self,
        *,
        run: ResolvedRunSpec,
        credential_store: AgentCredentialStore,
        dispatcher: GatewayDispatcher,
        coordinator: HarnessCoordinator,
        stream_ingest_token: str | None = None,
        runtime_control_token: str | None = None,
        termination_requested: Any | None = None,
    ):
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("GatewayRpcService.run must be ResolvedRunSpec")
        if not isinstance(credential_store, AgentCredentialStore):
            raise TypeError(
                "GatewayRpcService.credential_store must be AgentCredentialStore"
            )
        if credential_store.run_id != run.run_id:
            raise ValueError("Gateway credentials belong to another run")
        if stream_ingest_token is not None and (
            not isinstance(stream_ingest_token, str)
            or _TOKEN_PATTERN.fullmatch(stream_ingest_token) is None
            or stream_ingest_token == "0" * 64
        ):
            raise ValueError("stream ingestion token is invalid")
        if runtime_control_token is not None and (
            not isinstance(runtime_control_token, str)
            or _TOKEN_PATTERN.fullmatch(runtime_control_token) is None
            or runtime_control_token == "0" * 64
        ):
            raise ValueError("runtime control token is invalid")
        if termination_requested is None:
            termination_requested = asyncio.Event()

        self._run = run
        self._credentials = credential_store
        self._dispatcher = dispatcher
        self._coordinator = coordinator
        self._stream_ingest_token = stream_ingest_token
        self._runtime_control_token = runtime_control_token
        self._termination_requested = termination_requested
        self._round_gate = _GatewayRoundGate(
            current=self._current_time(),
        )

    @property
    def termination_requested(self) -> Any:
        return self._termination_requested

    async def __call__(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            response = await self.handle(operation, payload)
        except GatewayServiceError as error:
            # Keep the Agent-facing response stable, but preserve the causal
            # chain in the Harness log.  Otherwise an observation/runtime-hook
            # failure is reduced to the unhelpful ``dispatch.failed`` text.
            self._log_failure(operation, error)
            return GatewayErrorResponse(
                error=GatewayErrorBody(code=error.code, detail=error.detail)
            ).model_dump(mode="json")
        except Exception as error:
            self._log_failure(operation, error)
            return GatewayErrorResponse(
                error=GatewayErrorBody(
                    code="service.failed", detail="Gateway service failed"
                )
            ).model_dump(mode="json")
        return response

    @staticmethod
    def _log_failure(operation: str, error: BaseException) -> None:
        print(
            "gateway request failed "
            f"operation={operation!r} "
            f"exception={type(error).__name__}: {error}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exception(error, file=sys.stderr)

    async def handle(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if not isinstance(operation, str):
            raise GatewayServiceError("request.invalid", "Gateway operation is invalid")
        if not isinstance(payload, Mapping):
            raise GatewayServiceError("request.invalid", "Gateway request is invalid")

        if operation == "probe":
            return self._probe(payload)
        if operation == "process.stream.ingest":
            return self._process_stream_ingest(payload)
        if operation == "runtime.control":
            return await self._runtime_control(payload)
        if operation == "runtime.projection":
            return self._runtime_projection(payload)
        if operation == "command.invoke":
            return await self._command_invoke(payload)
        if operation == "command.status":
            return await self._command_status(payload)
        if operation == "query.invoke":
            return await self._query_invoke(payload)
        if operation == "observation.get":
            return await self._observation_get(payload)
        if operation == "decision.summary":
            return await self._decision_summary(payload)
        if operation == "turn.complete":
            return await self._turn_complete(payload)
        raise GatewayServiceError(
            "operation.unsupported", "Gateway operation is unsupported"
        )

    def _probe(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_exact_keys(payload, ())
        response = GatewayProbeResponse(
            schema_version="aero-bench.gateway-probe/v1",
            status="ready",
            run_id=self._run.run_id,
            current=self._current_time(),
        )
        return response.model_dump(mode="json")

    def _process_stream_ingest(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._require_exact_keys(payload, ("chunk", "token"))
        try:
            request = _ProcessStreamIngestRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "process stream ingestion request is invalid"
            ) from error
        expected_token = self._stream_ingest_token
        if (
            expected_token is None
            or _TOKEN_PATTERN.fullmatch(request.token) is None
            or not hmac.compare_digest(request.token, expected_token)
        ):
            raise GatewayServiceError("authentication.failed", "authentication failed")
        try:
            event_id = self._coordinator.ingest_process_stream(request.chunk)
        except (HarnessRuntimeError, TypeError, ValueError) as error:
            raise GatewayServiceError(
                "stream.rejected", "process stream chunk was rejected"
            ) from error
        response = _ProcessStreamIngestResponse(
            schema_version="aero-bench.process-stream-ingest-response/v1",
            run_id=request.chunk.run_id,
            workload_id=request.chunk.workload_id,
            stream=request.chunk.stream,
            sequence=request.chunk.sequence,
            event_id=event_id,
            closed=request.chunk.final,
        )
        return response.model_dump(mode="json")

    def _runtime_projection(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_exact_keys(
            payload,
            (
                "after_event_sequence",
                "after_scene_tick",
                "max_events",
                "max_scene_states",
                "token",
            ),
        )
        try:
            request = _RuntimeProjectionRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "runtime projection request is invalid"
            ) from error
        expected_token = self._runtime_control_token
        if (
            expected_token is None
            or _TOKEN_PATTERN.fullmatch(request.token) is None
            or not hmac.compare_digest(request.token, expected_token)
        ):
            raise GatewayServiceError("authentication.failed", "authentication failed")
        try:
            batch = self._coordinator.runtime_projection(
                after_scene_tick=request.after_scene_tick,
                after_event_sequence=request.after_event_sequence,
                max_scene_states=request.max_scene_states,
                max_events=request.max_events,
            )
        except (HarnessRuntimeError, TypeError, ValueError) as error:
            raise GatewayServiceError(
                "projection.rejected", "runtime projection request was rejected"
            ) from error
        return batch.model_dump(mode="json")

    async def _runtime_control(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._require_exact_keys(payload, ("action", "control_id", "token"))
        try:
            request = _RuntimeControlRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "runtime control request is invalid"
            ) from error
        expected_token = self._runtime_control_token
        if (
            expected_token is None
            or _TOKEN_PATTERN.fullmatch(request.token) is None
            or not hmac.compare_digest(request.token, expected_token)
        ):
            raise GatewayServiceError("authentication.failed", "authentication failed")
        try:
            receipt = await self._coordinator.control_runtime(
                request.action,
                control_id=request.control_id,
            )
        except (HarnessRuntimeError, TypeError, ValueError) as error:
            raise GatewayServiceError(
                "control.rejected", "runtime control request was rejected"
            ) from error
        if request.action == "stop":
            self._termination_requested.set()
        return receipt.model_dump(mode="json")

    async def _command_invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_exact_keys(payload, ("command", "token"))
        try:
            request = _CommandInvokeRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "command.invoke request is invalid"
            ) from error
        principal = self._credentials.authenticate(request.token)
        if (
            request.command.run_id != principal.run_id
            or request.command.agent_id != principal.agent_id
        ):
            raise GatewayServiceError(
                "authorization.denied", "request identity is not authorized"
            )
        await self._round_gate.begin_operation(
            agent_id=principal.agent_id,
            at=request.command.issued_at,
        )
        try:
            return await self._dispatch_command(principal, request.command)
        finally:
            await self._round_gate.end_operation()

    async def _dispatch_command(
        self, principal: AgentPrincipal, request: CommandRequest
    ) -> dict[str, Any]:
        try:
            result = await self._dispatcher.invoke(principal, request)
        except GatewayDispatchError as error:
            raise GatewayServiceError(
                "dispatch.failed", "command dispatch failed"
            ) from error
        return result.model_dump(mode="json")

    async def _command_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_exact_keys(payload, ("command_id", "requested_at", "token"))
        try:
            request = _CommandStatusRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "command.status request is invalid"
            ) from error
        principal = self._credentials.authenticate(request.token)
        await self._round_gate.begin_operation(
            agent_id=principal.agent_id,
            at=request.requested_at,
        )
        try:
            try:
                receipt = self._dispatcher.command_status(
                    principal,
                    command_id=request.command_id,
                    requested_at=request.requested_at,
                )
            except GatewayDispatchError as error:
                raise GatewayServiceError(
                    "dispatch.failed", "command status lookup failed"
                ) from error
            return receipt.model_dump(mode="json")
        finally:
            await self._round_gate.end_operation()

    async def _observation_get(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_exact_keys(payload, ("observation_id", "requested_at", "token"))
        try:
            request = _ObservationGetRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "observation.get request is invalid"
            ) from error
        principal = self._credentials.authenticate(request.token)
        await self._round_gate.begin_operation(
            agent_id=principal.agent_id,
            at=request.requested_at,
        )
        try:
            return await self._dispatch_observation(
                principal,
                observation_id=request.observation_id,
                requested_at=request.requested_at,
            )
        finally:
            await self._round_gate.end_operation()

    async def _query_invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_exact_keys(payload, ("query", "token"))
        try:
            request = _QueryInvokeRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "query.invoke request is invalid"
            ) from error
        principal = self._credentials.authenticate(request.token)
        if (
            request.query.run_id != principal.run_id
            or request.query.agent_id != principal.agent_id
        ):
            raise GatewayServiceError(
                "authorization.denied", "request identity is not authorized"
            )
        await self._round_gate.begin_operation(
            agent_id=principal.agent_id,
            at=request.query.issued_at,
        )
        try:
            try:
                result = await self._dispatcher.query(principal, request.query)
            except GatewayDispatchError as error:
                raise GatewayServiceError(
                    "dispatch.failed", "query dispatch failed"
                ) from error
            return result.model_dump(mode="json")
        finally:
            await self._round_gate.end_operation()

    async def _dispatch_observation(
        self,
        principal: AgentPrincipal,
        *,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> dict[str, Any]:
        try:
            envelope = await self._dispatcher.observe(
                principal,
                observation_id=observation_id,
                requested_at=requested_at,
            )
        except GatewayDispatchError as error:
            raise GatewayServiceError(
                "dispatch.failed", "observation dispatch failed"
            ) from error
        return envelope.model_dump(mode="json")

    async def _decision_summary(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._require_exact_keys(payload, ("summary", "token"))
        try:
            request = _DecisionSummaryRecordRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "decision.summary request is invalid"
            ) from error
        principal = self._credentials.authenticate(request.token)
        if (
            request.summary.run_id != principal.run_id
            or request.summary.agent_id != principal.agent_id
        ):
            raise GatewayServiceError(
                "authorization.denied", "request identity is not authorized"
            )
        await self._round_gate.begin_operation(
            agent_id=principal.agent_id,
            at=request.summary.at,
        )
        try:
            try:
                receipt = await self._dispatcher.record_decision_summary(
                    principal,
                    request.summary,
                )
            except GatewayDispatchError as error:
                raise GatewayServiceError(
                    "dispatch.failed", "decision summary recording failed"
                ) from error
            return receipt.model_dump(mode="json")
        finally:
            await self._round_gate.end_operation()

    async def _turn_complete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_exact_keys(payload, ("completion", "token"))
        try:
            request = _TurnCompleteRequest.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as error:
            raise GatewayServiceError(
                "request.invalid", "turn.complete request is invalid"
            ) from error
        principal = self._credentials.authenticate(request.token)
        if (
            request.completion.run_id != principal.run_id
            or request.completion.agent_id != principal.agent_id
        ):
            raise GatewayServiceError(
                "authorization.denied", "request identity is not authorized"
            )
        completion = request.completion
        await self._round_gate.begin_turn(
            agent_id=principal.agent_id,
            at=completion.at,
        )
        finalized = False
        terminal = False
        try:
            try:
                decision = await self._coordinator.submit_agent_turn(completion)
            except HarnessRuntimeError as error:
                raise GatewayServiceError(
                    "turn.failed", "agent turn submission failed"
                ) from error
            terminal = decision.status == "terminated"
            response = decision.model_dump(mode="json")
            await self._round_gate.end_turn(
                agent_id=principal.agent_id,
                status=decision.status,
                at=decision.at,
                permanent=self._coordinator.failed,
            )
            finalized = True
            if decision.status == "terminated" or self._coordinator.failed:
                self._termination_requested.set()
            return response
        finally:
            if not finalized:
                await self._round_gate.abort_turn(
                    permanent=terminal or self._coordinator.failed
                )
                if terminal or self._coordinator.failed:
                    self._termination_requested.set()

    def _current_time(self) -> SimulationTime:
        return self._coordinator.current

    @staticmethod
    def _require_exact_keys(
        payload: Mapping[str, Any], expected: tuple[str, ...]
    ) -> None:
        if set(payload) != set(expected):
            raise GatewayServiceError(
                "request.invalid", "Gateway request fields are invalid"
            )


__all__ = [
    "AgentCredentialStore",
    "GatewayErrorBody",
    "GatewayErrorResponse",
    "GatewayProbeResponse",
    "GatewayRpcService",
    "GatewayServiceError",
]
