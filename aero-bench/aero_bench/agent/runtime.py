from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from aero_bench.config.models import ArtifactRequirement, NamedValue
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.gateway.contracts import (
    DecisionSummaryReceipt,
    DecisionSummaryRequest,
    ObservationEnvelope,
    QueryRequest,
    QueryResult,
    ToolResult,
)
from aero_bench.providers.rpc import canonical_json_line, parse_json_object
from aero_bench.runtime.contracts import (
    AgentTurnCompletion,
    AgentTurnDecision,
    CommandReceipt,
    CommandRequest,
    SimulationTime,
)
from aero_bench.serialization import canonical_json_bytes


class AgentFrameworkError(RuntimeError):
    pass


class GatewayRequestError(AgentFrameworkError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"Gateway request failed ({code}): {detail}")


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise AgentFrameworkError(f"required Agent environment is unavailable: {name}")
    return value


def _normalized_root(environment: Mapping[str, str], name: str) -> Path:
    raw = _required_environment(environment, name)
    path = Path(raw)
    if not path.is_absolute() or os.fspath(path) != os.path.normpath(os.fspath(path)):
        raise AgentFrameworkError(f"{name} must be an absolute normalized path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise AgentFrameworkError(f"{name} is unavailable") from error
    if resolved != path or not resolved.is_dir():
        raise AgentFrameworkError(f"{name} must be a real directory")
    return resolved


def _load_contract(path: Path) -> AgentWorkloadContract:
    try:
        raw = path.read_bytes()
        value = parse_json_object(raw)
        if raw != canonical_json_bytes(value) + b"\n":
            raise ValueError("AgentWorkloadContract is not canonical JSON")
        return AgentWorkloadContract.model_validate(value)
    except Exception as error:
        raise AgentFrameworkError("AgentWorkloadContract is invalid") from error


class AgentContext:
    """Executor-issued identity, grants, and filesystem roots for one submission.

    This object is an adapter boundary only. It contains no benchmark-specific
    policy, action plan, model call, or expected answer.
    """

    def __init__(
        self,
        *,
        contract: AgentWorkloadContract,
        token: str,
        gateway_host: str,
        gateway_port: int,
        bundle_root: Path,
        artifact_root: Path,
    ) -> None:
        self.contract = contract
        self.token = token
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.bundle_root = bundle_root
        self.artifact_root = artifact_root
        self._requirements = {
            requirement.artifact_id: requirement
            for requirement in contract.agent.artifact_requirements
        }
        if len(self._requirements) != len(contract.agent.artifact_requirements):
            raise AgentFrameworkError(
                "Agent artifact requirements repeat an artifact_id"
            )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "AgentContext":
        values = os.environ if environment is None else environment
        contract_path = Path(_required_environment(values, "AERO_BENCH_CONTRACT"))
        if not contract_path.is_absolute():
            raise AgentFrameworkError("AERO_BENCH_CONTRACT must be absolute")
        contract = _load_contract(contract_path)
        try:
            gateway_port = int(_required_environment(values, "AERO_BENCH_GATEWAY_PORT"))
            seed = int(_required_environment(values, "AERO_BENCH_SEED"))
        except ValueError as error:
            raise AgentFrameworkError("Agent numeric environment is invalid") from error
        if not 1024 <= gateway_port <= 65535:
            raise AgentFrameworkError("Agent Gateway port is outside the valid range")
        if (
            _required_environment(values, "AERO_BENCH_ROLE") != "agent"
            or _required_environment(values, "AERO_BENCH_RUN_ID") != contract.run_id
            or _required_environment(values, "AERO_BENCH_WORKLOAD_ID")
            != contract.workload_id
            or seed != contract.seed
            or gateway_port != contract.gateway.port
        ):
            raise AgentFrameworkError(
                "executor environment differs from AgentWorkloadContract"
            )
        token = _required_environment(values, "AERO_BENCH_AGENT_TOKEN")
        if (
            len(token) != 64
            or any(character not in "0123456789abcdef" for character in token)
            or token == "0" * 64
        ):
            raise AgentFrameworkError("executor-issued Agent token is invalid")
        context = cls(
            contract=contract,
            token=token,
            gateway_host=_required_environment(values, "AERO_BENCH_GATEWAY_HOST"),
            gateway_port=gateway_port,
            bundle_root=_normalized_root(values, "AERO_BENCH_BUNDLE_DIR"),
            artifact_root=_normalized_root(values, "AERO_BENCH_ARTIFACT_DIR"),
        )
        context._validate_inputs()
        if any(context.artifact_root.iterdir()):
            raise AgentFrameworkError("Agent artifact root is not empty at startup")
        return context

    @property
    def run_id(self) -> str:
        return self.contract.run_id

    @property
    def agent_id(self) -> str:
        return self.contract.agent.agent_id

    @property
    def seed(self) -> int:
        return self.contract.seed

    def instruction_bytes(self) -> bytes:
        return self._read_bundle_file(
            self.contract.instruction.path,
            self.contract.instruction.sha256,
        )

    def asset_bytes(self, asset_id: str) -> bytes:
        asset = next(
            (
                asset
                for asset in self.contract.scenario_assets
                if asset.asset_id == asset_id
            ),
            None,
        )
        if asset is None:
            raise AgentFrameworkError(f"Agent asset is not granted: {asset_id}")
        return self._read_bundle_file(asset.file.path, asset.file.sha256)

    def write_artifact(self, artifact_id: str, payload: bytes) -> None:
        requirement = self._requirements.get(artifact_id)
        if requirement is None:
            raise AgentFrameworkError(f"Agent artifact is undeclared: {artifact_id}")
        if not isinstance(payload, bytes):
            raise TypeError("Agent artifact payload must be bytes")
        if len(payload) > requirement.max_size_bytes:
            raise AgentFrameworkError(
                f"Agent artifact exceeds size bound: {artifact_id}"
            )
        destination = self._artifact_destination(requirement)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.resolve(strict=True) != destination.parent:
            raise AgentFrameworkError("Agent artifact parent contains a symbolic link")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(destination, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except OSError as error:
            raise AgentFrameworkError(
                f"Agent artifact could not be written: {artifact_id}"
            ) from error

    def write_canonical_json_artifact(self, artifact_id: str, value: object) -> None:
        self.write_artifact(artifact_id, canonical_json_bytes(value))

    def gateway(self) -> "GatewayClient":
        return GatewayClient(self)

    def _validate_inputs(self) -> None:
        self._read_bundle_file(
            self.contract.instruction.path,
            self.contract.instruction.sha256,
        )
        for asset in self.contract.scenario_assets:
            self._read_bundle_file(asset.file.path, asset.file.sha256)

    def _read_bundle_file(self, relative_path: str, expected_digest: str) -> bytes:
        import hashlib

        relative = PurePosixPath(relative_path)
        candidate = self.bundle_root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            payload = resolved.read_bytes()
        except (OSError, RuntimeError) as error:
            raise AgentFrameworkError(
                f"Agent input is unavailable: {relative_path}"
            ) from error
        if resolved != candidate or not resolved.is_file():
            raise AgentFrameworkError(f"Agent input path is unsafe: {relative_path}")
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise AgentFrameworkError(f"Agent input digest changed: {relative_path}")
        return payload

    def _artifact_destination(self, requirement: ArtifactRequirement) -> Path:
        relative = PurePosixPath(requirement.relative_path)
        destination = self.artifact_root.joinpath(*relative.parts)
        if not destination.is_relative_to(self.artifact_root):
            raise AgentFrameworkError("Agent artifact path escapes its output root")
        return destination


class GatewayClient:
    """Synchronous strict JSON-line client for one participant framework."""

    MAX_FRAME_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        context: AgentContext,
        *,
        audit: Callable[[str, dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> None:
        if not isinstance(context, AgentContext):
            raise TypeError("GatewayClient requires an AgentContext")
        self._context = context
        self._connection: socket.socket | None = None
        self._buffer = b""
        self._audit = audit

    def __enter__(self) -> "GatewayClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def connect(self, *, timeout_seconds: float = 120.0) -> None:
        if self._connection is not None:
            raise AgentFrameworkError("Gateway client is already connected")
        deadline = time.monotonic() + timeout_seconds
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                connection = socket.create_connection(
                    (self._context.gateway_host, self._context.gateway_port),
                    timeout=min(5.0, max(0.1, deadline - time.monotonic())),
                )
                connection.settimeout(300.0)
                self._connection = connection
                probe = self.probe()
                if (
                    probe.get("status") != "ready"
                    or probe.get("run_id") != self._context.run_id
                ):
                    raise AgentFrameworkError("Gateway probe identity is invalid")
                return
            except OSError as error:
                last_error = error
                time.sleep(0.2)
            except BaseException:
                self.close()
                raise
        raise AgentFrameworkError("Gateway did not become ready") from last_error

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        self._buffer = b""
        if connection is not None:
            connection.close()

    def probe(self) -> dict[str, Any]:
        return self._request("probe", {})

    def command(
        self,
        *,
        command_id: str,
        tool_id: str,
        at: SimulationTime,
        arguments: Mapping[str, object],
    ) -> ToolResult:
        grants = tuple(
            grant
            for grant in self._context.contract.agent.tools
            if grant.tool_id == tool_id
        )
        if len(grants) != 1:
            raise AgentFrameworkError(f"Agent tool is not granted: {tool_id}")
        request = CommandRequest(
            run_id=self._context.run_id,
            command_id=command_id,
            agent_id=self._context.agent_id,
            tool_id=tool_id,
            issued_at=at,
            arguments=tuple(
                NamedValue(name=name, value=value)
                for name, value in sorted(arguments.items())
            ),
        )
        response = self._request(
            "command.invoke",
            {
                "token": self._context.token,
                "command": request.model_dump(mode="json"),
            },
        )
        try:
            return ToolResult.model_validate(response)
        except (TypeError, ValueError) as error:
            raise AgentFrameworkError(
                "Gateway returned an invalid ToolResult"
            ) from error

    def command_status(
        self,
        *,
        command_id: str,
        at: SimulationTime,
    ) -> CommandReceipt:
        response = self._request(
            "command.status",
            {
                "token": self._context.token,
                "command_id": command_id,
                "requested_at": at.model_dump(mode="json"),
            },
        )
        try:
            return CommandReceipt.model_validate(response)
        except (TypeError, ValueError) as error:
            raise AgentFrameworkError(
                "Gateway returned an invalid CommandReceipt"
            ) from error

    def query(
        self,
        *,
        query_id: str,
        query_type: str,
        at: SimulationTime,
        arguments: Mapping[str, object],
    ) -> QueryResult:
        grants = tuple(
            grant
            for grant in self._context.contract.agent.queries
            if grant.query_type == query_type
        )
        if len(grants) != 1:
            raise AgentFrameworkError(f"Agent query is not granted: {query_type}")
        request = QueryRequest(
            run_id=self._context.run_id,
            query_id=query_id,
            agent_id=self._context.agent_id,
            query_type=query_type,
            issued_at=at,
            arguments=tuple(
                NamedValue(name=name, value=value)
                for name, value in sorted(arguments.items())
            ),
        )
        response = self._request(
            "query.invoke",
            {
                "token": self._context.token,
                "query": request.model_dump(mode="json"),
            },
        )
        try:
            return QueryResult.model_validate(response)
        except (TypeError, ValueError) as error:
            raise AgentFrameworkError(
                "Gateway returned an invalid QueryResult"
            ) from error

    def observe(
        self,
        *,
        observation_id: str,
        at: SimulationTime,
    ) -> ObservationEnvelope:
        if not any(
            grant.observation_id == observation_id
            for grant in self._context.contract.agent.observations
        ):
            raise AgentFrameworkError(
                f"Agent observation is not granted: {observation_id}"
            )
        response = self._request(
            "observation.get",
            {
                "token": self._context.token,
                "observation_id": observation_id,
                "requested_at": at.model_dump(mode="json"),
            },
        )
        try:
            return ObservationEnvelope.model_validate(response)
        except (TypeError, ValueError) as error:
            raise AgentFrameworkError(
                "Gateway returned an invalid ObservationEnvelope"
            ) from error

    def decision_summary(
        self,
        *,
        summary_id: str,
        at: SimulationTime,
        summary: str,
        command_id: str | None = None,
        observation_ids: Sequence[str] = (),
    ) -> DecisionSummaryReceipt:
        request = DecisionSummaryRequest(
            schema_version="aero-bench.decision-summary-request/v1",
            run_id=self._context.run_id,
            agent_id=self._context.agent_id,
            summary_id=summary_id,
            at=at,
            decision_summary=summary,
            command_id=command_id,
            observation_ids=tuple(sorted(observation_ids)),
        )
        response = self._request(
            "decision.summary",
            {
                "token": self._context.token,
                "summary": request.model_dump(mode="json"),
            },
        )
        try:
            return DecisionSummaryReceipt.model_validate(response)
        except (TypeError, ValueError) as error:
            raise AgentFrameworkError(
                "Gateway returned an invalid DecisionSummaryReceipt"
            ) from error

    def complete_turn(
        self,
        *,
        completion_id: str,
        at: SimulationTime,
        disposition: str,
        command_ids: Sequence[str] = (),
        observation_ids: Sequence[str] = (),
    ) -> AgentTurnDecision:
        completion = AgentTurnCompletion(
            schema_version="aero-bench.agent-turn-completion/v1",
            run_id=self._context.run_id,
            agent_id=self._context.agent_id,
            completion_id=completion_id,
            at=at,
            disposition=disposition,
            command_ids=tuple(sorted(command_ids)),
            observation_ids=tuple(sorted(observation_ids)),
        )
        response = self._request(
            "turn.complete",
            {
                "token": self._context.token,
                "completion": completion.model_dump(mode="json"),
            },
        )
        try:
            return AgentTurnDecision.model_validate(response)
        except (TypeError, ValueError) as error:
            raise AgentFrameworkError(
                "Gateway returned an invalid AgentTurnDecision"
            ) from error

    def _request(self, operation: str, payload: Mapping[str, object]) -> dict[str, Any]:
        connection = self._connection
        if connection is None:
            raise AgentFrameworkError("Gateway client is not connected")
        try:
            connection.sendall(canonical_json_line({"operation": operation, **payload}))
            while b"\n" not in self._buffer:
                chunk = connection.recv(65_536)
                if not chunk:
                    raise AgentFrameworkError("Gateway closed the connection")
                self._buffer += chunk
                if len(self._buffer) > self.MAX_FRAME_BYTES:
                    raise AgentFrameworkError("Gateway response exceeds frame limit")
            frame, self._buffer = self._buffer.split(b"\n", 1)
            response = parse_json_object(frame)
        except OSError as error:
            raise AgentFrameworkError("Gateway transport failed") from error
        if self._audit is not None:
            self._audit(
                operation,
                {
                    "operation": operation,
                    **{key: value for key, value in payload.items() if key != "token"},
                },
                response,
            )
        error = response.get("error")
        if isinstance(error, dict):
            code = error.get("code", "gateway.failed")
            detail = error.get("detail", "Gateway request failed")
            raise GatewayRequestError(str(code), str(detail))
        return response


__all__ = [
    "AgentContext",
    "AgentFrameworkError",
    "GatewayClient",
    "GatewayRequestError",
]
