from __future__ import annotations

import asyncio
import inspect
import os
import re
import signal
import stat
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from jsonschema import Draft202012Validator

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import ArtifactRequirement, FileRef
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.gateway import (
    AgentCredentialStore,
    GatewayDispatcher,
    GatewayRpcService,
)
from aero_bench.gateway.contracts import (
    ObservationEnvelope,
    ProviderObservationEndpoint,
    ProviderQueryEndpoint,
    ProviderQueryResult,
    ProviderToolEndpoint,
    ProviderToolResult,
    ToolResult,
    QueryRequest,
)
from aero_bench.gateway.dispatcher import GatewayDispatchError
from aero_bench.providers import builtin_provider_registry
from aero_bench.providers.contracts import ProviderCommandResult
from aero_bench.providers.rpc import JsonLineRpcServer
from aero_bench.tasks.registry import (
    builtin_runtime_hook_factories,
    builtin_task_package_resolvers,
)
from aero_bench.runtime.bootstrap import (
    HarnessBootstrap,
    HarnessBootstrapError,
    bootstrap_harness,
)
from aero_bench.runtime.contracts import CommandRequest, SceneState, SimulationTime
from aero_bench.runtime.harness import AbortFailureClass, HarnessCoordinator
from aero_bench.runtime.hooks import RuntimeHook, resolve_runtime_hook
from aero_bench.runtime.ledger import ledger_jsonl_bytes
from aero_bench.runtime.scene_history import scene_state_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.bounds import calculate_theoretical_bounds
from aero_bench.tasks.inspection.contracts import TheoreticalBoundsEvidence
from aero_bench.tasks.inspection.integration import (
    INSPECTION_PACKAGE_ID,
    resolve_inspection_run_context,
)


_GATEWAY_BIND_HOST_ENV: Final[str] = "AERO_BENCH_GATEWAY_BIND_HOST"
_GATEWAY_PORT_ENV: Final[str] = "AERO_BENCH_GATEWAY_PORT"
_CREDENTIALS_ENV: Final[str] = "AERO_BENCH_AGENT_CREDENTIALS"
_STREAM_INGEST_TOKEN_ENV: Final[str] = "AERO_BENCH_STREAM_INGEST_TOKEN"
_RUNTIME_CONTROL_TOKEN_ENV: Final[str] = "AERO_BENCH_RUNTIME_CONTROL_TOKEN"
_PORT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?:[1-9][0-9]{3,4})$")
_OUTPUT_PATH_ERROR = "Harness artifact path must be normalized and relative"


class HarnessRuntimeError(RuntimeError):
    """A runtime failure that must not be converted into a success event."""


class HarnessRuntimeBlocked(HarnessRuntimeError):
    """A declared run cannot start with the capabilities of this service."""


@dataclass(frozen=True, slots=True)
class _HarnessArtifactPlan:
    event_requirement: ArtifactRequirement
    scene_state_history_requirement: ArtifactRequirement
    static_artifacts: tuple[tuple[ArtifactRequirement, bytes], ...] = ()
    task_local_business_requirement: ArtifactRequirement | None = None


class _ProviderToolAdapter(ProviderToolEndpoint):
    """Keep authoritative Provider events outside the Agent-visible result."""

    def __init__(self, session: object):
        handler = getattr(session, "handle_command", None)
        if not callable(handler):
            raise TypeError("tool endpoint does not expose handle_command")
        self._handler = handler

    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        outcome = self._handler(request)
        if not inspect.isawaitable(outcome):
            raise GatewayDispatchError(
                "Provider command handler did not return an awaitable"
            )
        outcome = await outcome
        if not isinstance(outcome, ProviderCommandResult):
            raise GatewayDispatchError(
                "Provider command handler returned an invalid internal result"
            )
        # ProviderSession has no response generator. An empty response is valid
        # only after startup has proved that every granted response schema accepts
        # the empty object. No response data is invented here.
        return ProviderToolResult(
            result=ToolResult(
                command_id=request.command_id,
                receipts=outcome.receipts,
                response=(),
            ),
            events=outcome.events,
        )


class _ProviderObservationAdapter(ProviderObservationEndpoint):
    """Forward an explicitly implemented Provider observation endpoint."""

    def __init__(self, session: object):
        observe = getattr(session, "observe", None)
        if not callable(observe):
            raise TypeError("Provider does not expose an observe endpoint")
        self._observe = observe

    async def observe(
        self,
        *,
        run_id: str,
        agent_id: str,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        result = self._observe(
            run_id=run_id,
            agent_id=agent_id,
            observation_id=observation_id,
            requested_at=requested_at,
        )
        if not inspect.isawaitable(result):
            raise GatewayDispatchError(
                "Provider observation endpoint did not return an awaitable"
            )
        envelope = await result
        if not isinstance(envelope, ObservationEnvelope):
            raise GatewayDispatchError(
                "Provider observation endpoint returned an invalid envelope"
            )
        return envelope


class _ProviderQueryAdapter(ProviderQueryEndpoint):
    """Forward only an explicitly implemented, read-only Provider query."""

    def __init__(self, session: object):
        query = getattr(session, "query", None)
        if not callable(query):
            raise TypeError("Provider does not expose a query endpoint")
        self._query = query

    async def query(self, request: QueryRequest) -> ProviderQueryResult:
        result = self._query(request)
        if not inspect.isawaitable(result):
            raise GatewayDispatchError(
                "Provider query endpoint did not return an awaitable"
            )
        result = await result
        if not isinstance(result, ProviderQueryResult):
            raise GatewayDispatchError(
                "Provider query endpoint returned an invalid result"
            )
        return result


class HarnessRuntimeService:
    """Run the production Harness lifecycle around one bootstrapped ResolvedRun."""

    def __init__(
        self,
        *,
        bootstrap: HarnessBootstrap,
        bundle: BundleReader,
        gateway_bind_host: str,
        gateway_port: int,
        gateway: GatewayRpcService,
        artifact_plan: _HarnessArtifactPlan,
        runtime_hook: RuntimeHook | None = None,
    ):
        if not isinstance(bootstrap, HarnessBootstrap):
            raise TypeError("bootstrap must be HarnessBootstrap")
        if not isinstance(bundle, BundleReader):
            raise TypeError("bundle must be BundleReader")
        if not isinstance(gateway, GatewayRpcService):
            raise TypeError("gateway must be GatewayRpcService")
        self._bootstrap = bootstrap
        self._bundle = bundle
        self._gateway_bind_host = gateway_bind_host
        self._gateway_port = gateway_port
        self._gateway = gateway
        self._artifact_plan = artifact_plan
        self._runtime_hook = runtime_hook

    @property
    def bootstrap(self) -> HarnessBootstrap:
        return self._bootstrap

    @property
    def run(self) -> ResolvedRunSpec:
        return self._bootstrap.run

    @property
    def coordinator(self) -> HarnessCoordinator:
        return self._bootstrap.coordinator

    @property
    def gateway(self) -> GatewayRpcService:
        return self._gateway

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "HarnessRuntimeService":
        values: Mapping[str, str] = os.environ if environment is None else environment
        gateway_bind_host, gateway_port = _gateway_environment(values)
        try:
            bootstrap = bootstrap_harness(
                values,
                registry=builtin_provider_registry(),
                task_package_resolvers=builtin_task_package_resolvers(),
            )
        except HarnessBootstrapError:
            raise
        except Exception as error:
            raise HarnessRuntimeError("Harness bootstrap failed") from error

        bundle = BundleReader(Path(values["AERO_BENCH_BUNDLE_DIR"]))
        artifact_plan = _validate_harness_artifacts(bootstrap.run, bundle=bundle)
        _validate_gateway_schema(
            bundle, bootstrap.run.environment.gateway.protocol_schema
        )
        credentials = AgentCredentialStore.from_environment(bootstrap.run, values)
        stream_ingest_token = values.get(_STREAM_INGEST_TOKEN_ENV)
        if not isinstance(stream_ingest_token, str) or not stream_ingest_token:
            raise HarnessRuntimeError("stream ingestion credential is unavailable")
        runtime_control_token = values.get(_RUNTIME_CONTROL_TOKEN_ENV)
        if not isinstance(runtime_control_token, str) or not runtime_control_token:
            raise HarnessRuntimeError("runtime control credential is unavailable")
        runtime_hook = None
        if bootstrap.run.execution_scope == "formal_benchmark":
            try:
                runtime_hook = resolve_runtime_hook(
                    run=bootstrap.run,
                    reader=bundle,
                    providers=bootstrap.coordinator.provider_sessions,
                    ledger=bootstrap.ledger,
                    runtime_hook_time=lambda: bootstrap.coordinator.runtime_hook_time,
                    factories=builtin_runtime_hook_factories(),
                )
            except Exception as error:
                raise HarnessRuntimeBlocked(
                    "task runtime hook could not be constructed from the resolved run"
                ) from error
        logical_sessions = (
            getattr(runtime_hook, "logical_sessions", {})
            if runtime_hook is not None
            else {}
        )
        tool_endpoints, query_endpoints, observation_endpoints = _build_provider_endpoints(
            run=bootstrap.run,
            coordinator=bootstrap.coordinator,
            bundle=bundle,
            logical_sessions=logical_sessions,
        )
        if runtime_hook is not None:
            bootstrap.coordinator.install_runtime_hook(runtime_hook)
        dispatcher = GatewayDispatcher(
            run=bootstrap.run,
            bundle_root=bundle.root,
            tool_endpoints=tool_endpoints,
            query_endpoints=query_endpoints,
            observation_endpoints=observation_endpoints,
            authoritative_time=lambda: bootstrap.coordinator.current,
            ledger=bootstrap.ledger,
            runtime_hook=runtime_hook,
        )
        gateway = GatewayRpcService(
            run=bootstrap.run,
            credential_store=credentials,
            dispatcher=dispatcher,
            coordinator=bootstrap.coordinator,
            stream_ingest_token=stream_ingest_token,
            runtime_control_token=runtime_control_token,
        )
        return cls(
            bootstrap=bootstrap,
            bundle=bundle,
            gateway_bind_host=gateway_bind_host,
            gateway_port=gateway_port,
            gateway=gateway,
            artifact_plan=artifact_plan,
            runtime_hook=runtime_hook,
        )

    async def run_forever(self) -> None:
        """Execute the Harness lifecycle until a valid termination is requested."""

        termination = self._gateway.termination_requested
        loop = asyncio.get_running_loop()
        installed_signals: list[signal.Signals] = []
        prepared = False
        server: Any | None = None
        normal_termination = False
        signal_requested = False
        runtime_failure_requested = False
        cancelled = False
        termination_ack_drained = False
        termination_ack_failed = False
        run_failure: BaseException | None = None
        cleanup_failures: list[BaseException] = []

        def request_termination() -> None:
            nonlocal signal_requested
            signal_requested = True
            termination.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, request_termination)
            except (NotImplementedError, RuntimeError):
                continue
            installed_signals.append(signum)

        async def handle_request(
            operation: str, payload: Mapping[str, Any]
        ) -> Mapping[str, Any]:
            nonlocal normal_termination, runtime_failure_requested
            try:
                response = await self._gateway(operation, payload)
                if (
                    operation == "turn.complete"
                    and response.get("status") == "terminated"
                ):
                    normal_termination = True
                return response
            finally:
                if self.coordinator.failed:
                    runtime_failure_requested = True
                    termination.set()
                elif self.coordinator.shutdown_complete:
                    termination.set()

        def response_drained(
            operation: str, response: Mapping[str, Any], drained: bool
        ) -> None:
            nonlocal termination_ack_drained, termination_ack_failed
            if operation != "turn.complete" or response.get("status") != "terminated":
                return
            if drained:
                termination_ack_drained = True
            else:
                termination_ack_failed = True

        try:
            await self.coordinator.prepare()
            prepared = True
            server = await JsonLineRpcServer(
                handle_request,
                response_drained=response_drained,
            ).start(
                host=self._gateway_bind_host,
                port=self._gateway_port,
            )
            await termination.wait()
        except BaseException as error:
            run_failure = error
            cancelled = isinstance(error, asyncio.CancelledError)
        finally:
            if server is not None:
                try:
                    await server.graceful_close(
                        timeout_seconds=_drain_deadline_seconds(self.run)
                    )
                except BaseException as error:
                    cleanup_failures.append(error)

            for signum in installed_signals:
                loop.remove_signal_handler(signum)

            if not normal_termination:
                failure_class: AbortFailureClass
                if cancelled:
                    failure_class = "cancelled"
                elif runtime_failure_requested or (
                    run_failure is not None and prepared
                ):
                    failure_class = "runtime_failed"
                elif signal_requested or termination.is_set():
                    failure_class = "signal"
                elif not prepared:
                    failure_class = "prepare_failed"
                else:
                    failure_class = "runtime_failed"
                try:
                    report = await self.coordinator.abort(failure_class=failure_class)
                    if report.provider_failures:
                        cleanup_failures.append(
                            HarnessRuntimeError(
                                "Provider abort failed for declared sessions: "
                                + ",".join(report.provider_failures)
                            )
                        )
                except BaseException as error:
                    cleanup_failures.append(error)

            if normal_termination and (
                termination_ack_failed or not termination_ack_drained
            ):
                cleanup_failures.append(
                    HarnessRuntimeError(
                        "terminated Gateway response was not drained by the writer"
                    )
                )

            if self.coordinator.shutdown_complete and (
                not normal_termination
                or (termination_ack_drained and not cleanup_failures)
            ):
                try:
                    scene_history_payload = self.coordinator.seal_scene_state_history()
                    _write_harness_artifacts(
                        root=self._bootstrap.artifact_root,
                        plan=self._artifact_plan,
                        records=self._bootstrap.ledger.records,
                        scene_state_history_payload=scene_history_payload,
                        runtime_hook=self._runtime_hook,
                    )
                    self.coordinator.discard_scene_state_staging()
                except BaseException as error:
                    cleanup_failures.append(error)

        if run_failure is not None:
            raise run_failure
        if cleanup_failures:
            raise cleanup_failures[0]


def _drain_deadline_seconds(run: ResolvedRunSpec) -> float:
    timeout_values = [run.environment.clock.provider_timeout_ms]
    for agent in run.agents:
        timeout_values.extend(grant.timeout_ms for grant in agent.tools)
        timeout_values.extend(grant.timeout_ms for grant in agent.queries)
        timeout_values.extend(grant.timeout_ms for grant in agent.observations)
    return max(timeout_values) / 1000


def _gateway_environment(environment: Mapping[str, str]) -> tuple[str, int]:
    host = _required_environment_value(environment, _GATEWAY_BIND_HOST_ENV)
    port_value = _required_environment_value(environment, _GATEWAY_PORT_ENV)
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", host) is None:
        raise HarnessRuntimeError(
            f"{_GATEWAY_BIND_HOST_ENV} must be a normalized host name"
        )
    if _PORT_PATTERN.fullmatch(port_value) is None:
        raise HarnessRuntimeError(
            f"{_GATEWAY_PORT_ENV} must be a canonical decimal port"
        )
    port = int(port_value)
    if not 1024 <= port <= 65535:
        raise HarnessRuntimeError(f"{_GATEWAY_PORT_ENV} is outside the allowed range")
    return host, port


def _required_environment_value(environment: Mapping[str, str], key: str) -> str:
    try:
        value = environment[key]
    except (KeyError, TypeError) as error:
        raise HarnessRuntimeError(
            f"missing required environment value: {key}"
        ) from error
    if not isinstance(value, str) or not value:
        raise HarnessRuntimeError(
            f"environment value must be a non-empty string: {key}"
        )
    return value


def _validate_harness_artifacts(
    run: ResolvedRunSpec,
    *,
    bundle: BundleReader,
) -> _HarnessArtifactPlan:
    requirements = run.environment.harness_artifact_requirements
    for requirement in requirements:
        _validate_output_path(requirement.relative_path)
    _validate_distinct_output_paths(requirements)

    if run.task.package.package_id != INSPECTION_PACKAGE_ID:
        event_requirements = tuple(
            requirement
            for requirement in requirements
            if requirement.artifact_type == "event.log"
        )
        scene_history_requirements = tuple(
            requirement
            for requirement in requirements
            if requirement.artifact_type == "scene.state-history"
        )
        if (
            len(requirements) != 2
            or len(event_requirements) != 1
            or len(scene_history_requirements) != 1
        ):
            raise HarnessRuntimeBlocked(
                "Harness contracts must declare exactly event.log and scene.state-history"
            )
        event_requirement = event_requirements[0]
        scene_history_requirement = scene_history_requirements[0]
        _validate_event_log_requirement(event_requirement)
        _validate_scene_state_history_requirement(scene_history_requirement)
        return _HarnessArtifactPlan(
            event_requirement=event_requirement,
            scene_state_history_requirement=scene_history_requirement,
        )

    try:
        package, resolved_bounds, _ = resolve_inspection_run_context(
            reader=bundle,
            task=run.task,
            environment=run.environment,
            agents=run.agents,
            scenario=run.scenario,
        )
    except Exception as error:
        raise HarnessRuntimeBlocked(
            "Inspection Harness artifacts cannot be resolved from the pinned bundle"
        ) from error

    task_local_business = bool(run.scenario.task.logical_endpoint_ids)
    business_requirements = tuple(
        requirement
        for requirement in package.artifact_requirements
        if requirement.evidence_kind == "business_state"
    )
    if len(business_requirements) != 1:
        raise HarnessRuntimeBlocked(
            "Inspection requires exactly one business_state artifact"
        )
    business_requirement = business_requirements[0]
    if task_local_business and business_requirement.producer_id != "harness":
        raise HarnessRuntimeBlocked(
            "task-local Inspection business state must be produced by the Harness"
        )
    if not task_local_business and business_requirement.producer_id == "harness":
        raise HarnessRuntimeBlocked(
            "external Inspection business state cannot be attributed to the Harness"
        )

    package_requirements = tuple(
        requirement
        for requirement in package.artifact_requirements
        if requirement.producer_id == "harness"
    )
    expected_harness_count = 4 if task_local_business else 3
    if (
        len(package_requirements) != expected_harness_count
        or len(requirements) != expected_harness_count
    ):
        raise HarnessRuntimeBlocked(
            "Inspection Harness artifact count differs from task ownership"
        )
    declared_by_id = {item.artifact_id: item for item in requirements}
    package_by_id = {item.artifact_id: item for item in package_requirements}
    if set(declared_by_id) != set(package_by_id) or any(
        declared_by_id[artifact_id].model_dump(mode="json")
        != package_by_id[artifact_id].model_dump(mode="json", exclude={"evidence_kind"})
        for artifact_id in declared_by_id.keys() & package_by_id.keys()
    ):
        raise HarnessRuntimeBlocked(
            "Inspection Harness artifacts disagree with the pinned task package"
        )

    event_requirements = tuple(
        declared_by_id[item.artifact_id]
        for item in package_requirements
        if item.evidence_kind == "event_log"
    )
    scene_history_requirements = tuple(
        declared_by_id[item.artifact_id]
        for item in package_requirements
        if item.evidence_kind == "scene_state_history"
    )
    bounds_requirements = tuple(
        declared_by_id[item.artifact_id]
        for item in package_requirements
        if item.evidence_kind == "theoretical_bounds"
    )
    if (
        len(event_requirements) != 1
        or len(scene_history_requirements) != 1
        or len(bounds_requirements) != 1
    ):
        raise HarnessRuntimeBlocked(
            "Inspection requires event_log, scene_state_history, and theoretical_bounds Harness artifacts"
        )
    event_requirement = event_requirements[0]
    scene_history_requirement = scene_history_requirements[0]
    bounds_requirement = bounds_requirements[0]
    _validate_event_log_requirement(event_requirement)
    _validate_scene_state_history_requirement(scene_history_requirement)
    if (
        bounds_requirement.artifact_type != "theoretical.bounds"
        or bounds_requirement.producer_id != "harness"
        or bounds_requirement.visibility != "public"
        or bounds_requirement.source_asset_id is not None
    ):
        raise HarnessRuntimeBlocked(
            "Inspection theoretical_bounds must be a public Harness artifact"
        )

    bounds_evidence = TheoreticalBoundsEvidence(
        source_artifact_id=bounds_requirement.artifact_id,
        run_id=run.run_id,
        bounds=calculate_theoretical_bounds(
            resolved_bounds.bounds,
            network_delivery_required=package.network_delivery_required,
        ),
        provider_configs=resolved_bounds.provider_configs,
    )
    payload = canonical_json_bytes(bounds_evidence.model_dump(mode="json"))
    if len(payload) > bounds_requirement.max_size_bytes:
        raise HarnessRuntimeBlocked(
            "Inspection theoretical_bounds exceeds its declared max_size_bytes"
        )
    if task_local_business and (
        business_requirement.source_asset_id is not None
        or business_requirement.producer_id != "harness"
    ):
        raise HarnessRuntimeBlocked(
            "task-local business_state must be a Harness runtime artifact"
        )
    return _HarnessArtifactPlan(
        event_requirement=event_requirement,
        scene_state_history_requirement=scene_history_requirement,
        static_artifacts=((bounds_requirement, payload),),
        task_local_business_requirement=(
            declared_by_id[business_requirement.artifact_id]
            if task_local_business
            else None
        ),
    )


def _validate_event_log_requirement(requirement: ArtifactRequirement) -> None:
    if (
        requirement.artifact_type != "event.log"
        or requirement.producer_id != "harness"
        or requirement.visibility != "private"
        or requirement.source_asset_id is not None
    ):
        raise HarnessRuntimeBlocked(
            "Harness runtime only supports one private harness event.log artifact"
        )


def _validate_scene_state_history_requirement(
    requirement: ArtifactRequirement,
) -> None:
    if (
        requirement.artifact_type != "scene.state-history"
        or requirement.producer_id != "harness"
        or requirement.visibility != "public"
        or requirement.source_asset_id is not None
    ):
        raise HarnessRuntimeBlocked(
            "Harness scene state history must be a public harness scene.state-history artifact"
        )


def _validate_distinct_output_paths(
    requirements: tuple[ArtifactRequirement, ...],
) -> None:
    paths = tuple(_validate_output_path(item.relative_path) for item in requirements)
    if len(paths) != len(set(paths)):
        raise HarnessRuntimeBlocked("Harness artifact paths must be unique")
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if path in other.parents or other in path.parents:
                raise HarnessRuntimeBlocked(
                    "Harness artifact path cannot contain another artifact path"
                )


def _validate_gateway_schema(bundle: BundleReader, reference: FileRef) -> None:
    try:
        bundle.validate_schema(reference)
    except Exception as error:
        raise HarnessRuntimeBlocked(
            "declared Gateway protocol schema is unavailable or invalid"
        ) from error


def _build_provider_endpoints(
    *,
    run: ResolvedRunSpec,
    coordinator: HarnessCoordinator,
    bundle: BundleReader,
    logical_sessions: Mapping[str, object],
) -> tuple[
    dict[str, ProviderToolEndpoint],
    dict[tuple[str, str], ProviderQueryEndpoint],
    dict[str, ProviderObservationEndpoint],
]:
    sessions = coordinator.provider_sessions
    if not isinstance(sessions, Mapping):
        raise HarnessRuntimeBlocked(
            "HarnessCoordinator does not expose its validated Provider sessions"
        )
    declared_provider_ids = {
        provider.provider_id for provider in run.environment.providers
    }
    if set(sessions) != declared_provider_ids:
        raise HarnessRuntimeBlocked(
            "HarnessCoordinator Provider sessions do not match ResolvedRun"
        )
    expected_logical_ids = set(run.scenario.task.logical_endpoint_ids)
    if set(logical_sessions) != expected_logical_ids:
        raise HarnessRuntimeBlocked(
            "task-local endpoint sessions do not match ResolvedScenario"
        )
    if set(logical_sessions) & set(sessions):
        raise HarnessRuntimeBlocked(
            "task-local endpoint IDs collide with physical Provider sessions"
        )
    endpoint_sessions: dict[str, object] = {**sessions, **logical_sessions}

    tool_endpoints: dict[str, ProviderToolEndpoint] = {}
    query_endpoints: dict[tuple[str, str], ProviderQueryEndpoint] = {}
    observation_endpoints: dict[str, ProviderObservationEndpoint] = {}
    for agent in run.agents:
        for grant in agent.tools:
            session = _session_for_grant(endpoint_sessions, grant.provider_id)
            _validate_async_method(session, "handle_command", grant.tool_id)
            _validate_schema(bundle, grant.request_schema, "tool request")
            response_schema = _validate_schema(
                bundle, grant.response_schema, "tool response"
            )
            if not Draft202012Validator(response_schema).is_valid({}):
                raise HarnessRuntimeBlocked(
                    f"tool response schema cannot be produced by ProviderSession: {grant.tool_id}"
                )
            tool_endpoints.setdefault(grant.provider_id, _ProviderToolAdapter(session))
        for grant in agent.queries:
            session = _session_for_grant(endpoint_sessions, grant.provider_id)
            _validate_async_method(session, "query", grant.query_type)
            _validate_schema(bundle, grant.request_schema, "query request")
            _validate_schema(bundle, grant.response_schema, "query response")
            query_endpoints[(grant.provider_id, grant.query_type)] = (
                _ProviderQueryAdapter(session)
            )
        for grant in agent.observations:
            session = _session_for_grant(endpoint_sessions, grant.provider_id)
            _validate_async_method(session, "observe", grant.observation_id)
            _validate_schema(bundle, grant.schema_file, "observation")
            observation_endpoints.setdefault(
                grant.provider_id, _ProviderObservationAdapter(session)
            )
    return tool_endpoints, query_endpoints, observation_endpoints


def _session_for_grant(
    sessions: Mapping[str, object], provider_id: str
) -> object:
    try:
        session = sessions[provider_id]
    except KeyError as error:
        raise HarnessRuntimeBlocked(
            f"declared Provider is unavailable for gateway grant: {provider_id}"
        ) from error
    return session


def _validate_async_method(
    session: object, method_name: str, grant_id: str
) -> None:
    method = getattr(session, method_name, None)
    if not callable(method) or not inspect.iscoroutinefunction(method):
        raise HarnessRuntimeBlocked(
            f"Provider grant {grant_id} has no asynchronous {method_name} endpoint"
        )


def _validate_schema(
    bundle: BundleReader, reference: FileRef, label: str
) -> dict[str, object]:
    try:
        return bundle.validate_schema(reference)
    except Exception as error:
        raise HarnessRuntimeBlocked(
            f"declared {label} schema is unavailable or invalid"
        ) from error


def _validate_output_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not value
        or value.strip() != value
        or str(path) != value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise HarnessRuntimeBlocked(_OUTPUT_PATH_ERROR)
    return path


def _write_harness_artifacts(
    *,
    root: Path,
    plan: _HarnessArtifactPlan,
    records: tuple[Any, ...],
    scene_state_history: tuple[SceneState, ...] | None = None,
    scene_state_history_payload: bytes | None = None,
    runtime_hook: RuntimeHook | None = None,
) -> None:
    if not records or records[-1].event.event_type not in {
        "run.completed",
        "run.aborted",
    }:
        raise HarnessRuntimeError(
            "cannot write Harness artifacts before a completed or aborted run"
        )
    terminal_event_type = records[-1].event.event_type
    history_empty = (
        not scene_state_history_payload
        if scene_state_history_payload is not None
        else not scene_state_history
    )
    aborted_before_first_motion = (
        terminal_event_type == "run.aborted"
        and history_empty
        and not any(
            record.event.event_type == "scene.state.committed" for record in records
        )
    )
    if history_empty and not aborted_before_first_motion:
        raise HarnessRuntimeError(
            "an empty scene state history requires an abort before first motion"
        )

    event_payload = ledger_jsonl_bytes(records)
    scene_history_payload = (
        scene_state_history_payload
        if scene_state_history_payload is not None
        else scene_state_jsonl_bytes(
            scene_state_history or (),
            aborted_before_first_motion=aborted_before_first_motion,
        )
    )
    if scene_history_payload.count(b"\n") != sum(
        record.event.event_type == "scene.state.committed" for record in records
    ):
        raise HarnessRuntimeError("SceneState history differs from the ledger commit inventory")
    dynamic_artifacts: tuple[tuple[ArtifactRequirement, bytes], ...] = ()
    business_requirement = plan.task_local_business_requirement
    if business_requirement is not None:
        evidence = getattr(runtime_hook, "task_local_business_evidence", None)
        if not callable(evidence):
            raise HarnessRuntimeError(
                "task-local business artifact has no runtime state source"
            )
        document = evidence(
            source_artifact_id=business_requirement.artifact_id,
            event_chain_root=records[-1].event_hash,
        )
        dynamic_artifacts = (
            (business_requirement, canonical_json_bytes(document)),
        )
    artifacts = (
        (plan.event_requirement, event_payload),
        (plan.scene_state_history_requirement, scene_history_payload),
        *plan.static_artifacts,
        *dynamic_artifacts,
    )
    _write_artifact_payloads(root=root, artifacts=artifacts)


def _write_artifact_payloads(
    *,
    root: Path,
    artifacts: tuple[tuple[ArtifactRequirement, bytes], ...],
) -> None:
    requirements = tuple(requirement for requirement, _ in artifacts)
    _validate_distinct_output_paths(requirements)
    normalized = tuple(
        (requirement, _validate_output_path(requirement.relative_path), payload)
        for requirement, payload in artifacts
    )
    for requirement, _, payload in normalized:
        if len(payload) > requirement.max_size_bytes:
            raise HarnessRuntimeError(
                f"Harness artifact exceeds max_size_bytes: {requirement.artifact_id}"
            )

    try:
        root_mode = root.lstat().st_mode
    except OSError as error:
        raise HarnessRuntimeError("Harness artifact root is unavailable") from error
    if stat.S_ISLNK(root_mode):
        raise HarnessRuntimeError("Harness artifact root cannot be a symbolic link")
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise HarnessRuntimeError("Harness artifact root is not a directory")

    expected_paths = tuple(path for _, path, _ in normalized)
    _validate_prewrite_inventory(resolved_root, expected_paths)
    destinations: list[Path] = []
    staged: list[tuple[Path, Path]] = []
    try:
        for _, path, payload in normalized:
            parent = _create_output_parent(resolved_root, path.parent)
            destination = parent / path.name
            try:
                destination.lstat()
            except FileNotFoundError:
                pass
            else:
                raise HarnessRuntimeError(
                    f"Harness artifact destination already exists: {path}"
                )

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=parent
            )
            temporary = Path(temporary_name)
            staged.append((temporary, destination))
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

        for temporary, destination in staged:
            os.replace(temporary, destination)
            destinations.append(destination)
        staged.clear()
        for destination in destinations:
            os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
        _validate_output_inventory(resolved_root, artifacts)
    except BaseException:
        for destination in destinations:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for temporary, _ in staged:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _create_output_parent(root: Path, relative_parent: PurePosixPath) -> Path:
    current = root
    for component in relative_parent.parts:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise HarnessRuntimeError("Harness artifact path contains a non-directory")
        os.chmod(current, 0o700)
    return current


def _validate_prewrite_inventory(
    root: Path,
    expected_paths: tuple[PurePosixPath, ...],
) -> None:
    allowed_directories = {
        parent.as_posix()
        for path in expected_paths
        for parent in path.parents
        if parent != PurePosixPath(".")
    }
    expected = {path.as_posix() for path in expected_paths}
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        mode = candidate.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise HarnessRuntimeError(
                f"Harness artifact root contains a symbolic link: {relative}"
            )
        if stat.S_ISDIR(mode):
            if relative not in allowed_directories:
                raise HarnessRuntimeError(
                    "Harness artifact root contains an undeclared directory: "
                    f"{relative}"
                )
            continue
        if relative in expected:
            raise HarnessRuntimeError(
                f"Harness artifact destination already exists: {relative}"
            )
        if not stat.S_ISREG(mode):
            raise HarnessRuntimeError(
                f"Harness artifact root contains a special file: {relative}"
            )
        raise HarnessRuntimeError(
            f"Harness artifact root contains an undeclared file: {relative}"
        )


def _validate_output_inventory(
    root: Path,
    artifacts: tuple[tuple[ArtifactRequirement, bytes], ...],
) -> None:
    expected_payloads = {
        requirement.relative_path: (requirement, payload)
        for requirement, payload in artifacts
    }
    expected = set(expected_payloads)
    actual: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        mode = candidate.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise HarnessRuntimeError(
                f"Harness artifact is a symbolic link: {relative}"
            )
        if stat.S_ISDIR(mode):
            if not any(path.startswith(relative + "/") for path in expected):
                raise HarnessRuntimeError(
                    f"Harness artifact root contains an undeclared directory: {relative}"
                )
            continue
        if not stat.S_ISREG(mode):
            raise HarnessRuntimeError(f"Harness artifact is a special file: {relative}")
        if candidate.stat().st_nlink != 1:
            raise HarnessRuntimeError(f"Harness artifact is hard-linked: {relative}")
        if stat.S_IMODE(mode) != 0o600:
            raise HarnessRuntimeError(f"Harness artifact mode is not 600: {relative}")
        actual.add(relative)
    if actual != expected:
        raise HarnessRuntimeError(
            "Harness artifact inventory does not match the declared contract: "
            f"missing={sorted(expected - actual)}, undeclared={sorted(actual - expected)}"
        )
    for relative, (requirement, payload) in expected_payloads.items():
        destination = root / relative
        if destination.stat().st_size > requirement.max_size_bytes:
            raise HarnessRuntimeError(
                f"Harness artifact exceeds max_size_bytes: {requirement.artifact_id}"
            )
        if destination.read_bytes() != payload:
            raise HarnessRuntimeError(
                f"Harness artifact bytes changed while writing: {requirement.artifact_id}"
            )


async def run_harness(
    environment: Mapping[str, str] | None = None,
) -> None:
    service = HarnessRuntimeService.from_environment(environment)
    await service.run_forever()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in ([], ["serve"]):
        print("usage: harness-runtime [serve]", file=sys.stderr)
        return 2
    try:
        asyncio.run(run_harness())
    except HarnessRuntimeBlocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    except (HarnessBootstrapError, HarnessRuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = [
    "HarnessRuntimeBlocked",
    "HarnessRuntimeError",
    "HarnessRuntimeService",
    "main",
    "run_harness",
]
