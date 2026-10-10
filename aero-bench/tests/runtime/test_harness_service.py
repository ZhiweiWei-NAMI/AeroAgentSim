from __future__ import annotations

from tests.support import fixture_provider_registry

import asyncio
import hashlib
import inspect
import socket
import stat
from pathlib import Path

import pytest
import yaml

import aero_bench.runtime.service as runtime_service
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import ArtifactRequirement, FeasibilityAssessment
from aero_bench.config.resolver import resolve_suite
from aero_bench.executor.contracts import HarnessWorkloadContract
from aero_bench.providers import ProviderCommandResult, ProviderManifest
from aero_bench.providers.rpc import (
    JsonLineRpcTransport,
    ProviderRemoteError,
    ProviderRpcError,
)
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageResult,
    CommandReceipt,
    CommandRequest,
    EnuLinearVelocity,
    MotionStageResult,
    NedLinearVelocity,
    NetworkStageResult,
    SceneContribution,
    SimulationTime,
    StateSample,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    state_sample_digest_value,
    step_receipt_digest_value,
)
from aero_bench.runtime.ledger import EventLedger, ledger_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection import (
    InspectionTaskPackageResolver,
    TheoreticalBoundsEvidence,
    calculate_theoretical_bounds,
    resolve_inspection_run_context,
)
from aero_bench.world.resolved import ResolvedTaskScenarioProjection
from aero_bench.runtime.service import (
    HarnessRuntimeBlocked,
    HarnessRuntimeError,
    HarnessRuntimeService,
    _ProviderToolAdapter,
    _drain_deadline_seconds,
    _gateway_environment,
    _validate_harness_artifacts,
    _HarnessArtifactPlan,
    _write_artifact_payloads,
    _write_harness_artifacts,
)
from tests.support import (
    build_bundle,
    fake_finalization_receipt,
    resolve_bundle,
    with_fixture_provider_stages,
    write_text,
)


TEST_RUN_ID = "a" * 64


class _ServiceSession:
    def __init__(
        self,
        manifest: ProviderManifest,
        run_id: str,
        scenario,
        *,
        fail_prepare: bool = False,
        fail_step: bool = False,
    ):
        self.manifest = manifest
        self._run_id = run_id
        self._scenario = scenario
        self.fail_prepare = fail_prepare
        self.fail_step = fail_step
        self.shutdown_calls = 0

    async def prepare(self) -> None:
        if self.fail_prepare:
            raise RuntimeError("provider prepare failed")

    async def reset(self, *, seed: int) -> StepReceipt:
        return StepReceipt(
            run_id=self._run_id,
            provider_id=self.manifest.provider_id,
            reached=SimulationTime(tick=0, sim_time_ns=0),
            state_digest=hashlib.sha256(f"reset:{seed}".encode()).hexdigest(),
        )

    async def step_stage(self, request):
        if self.fail_step:
            raise RuntimeError("provider step failed")

        sample_fields = (
            {
                "schema_version": "aero-bench.state-sample/v1",
                "run_id": self._run_id,
                "scenario_digest": self._scenario.scenario_digest,
                "at": request.target,
                "stage": "motion",
                "entity_id": entity.entity_id,
                "provider_id": self.manifest.provider_id,
                "sample_kind": "dynamic",
                "pose": entity.initial_pose,
                "linear_velocity_enu": EnuLinearVelocity(
                    east_mps=0.0,
                    north_mps=0.0,
                    up_mps=0.0,
                ),
                "linear_velocity_ned": NedLinearVelocity(
                    north_mps=0.0,
                    east_mps=0.0,
                    down_mps=0.0,
                ),
            }
            for entity in self._scenario.entities
            if request.stage == "motion"
            and entity.state == "dynamic"
            and entity.owner_id == self.manifest.provider_id
        )
        samples = tuple(
            StateSample(
                **fields,
                sample_digest=state_sample_digest_value(
                    StateSample.model_construct(
                        **fields,
                        sample_digest="0" * 64,
                    )
                ),
            )
            for fields in sample_fields
        )
        contribution_fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": self._run_id,
            "scenario_digest": self._scenario.scenario_digest,
            "at": request.target,
            "stage": request.stage,
            "provider_id": self.manifest.provider_id,
            "samples": samples,
            "attribute_updates": (),
        }
        unsigned_contribution = SceneContribution.model_construct(
            **contribution_fields,
            payload_digest="0" * 64,
            contribution_digest="0" * 64,
        )
        payload_digest = scene_contribution_payload_digest_value(
            unsigned_contribution
        )
        contribution = SceneContribution(
            **contribution_fields,
            payload_digest=payload_digest,
            contribution_digest=scene_contribution_digest_value(
                unsigned_contribution.model_copy(
                    update={"payload_digest": payload_digest}
                )
            ),
        )
        receipt = StepReceipt(
            run_id=self._run_id,
            provider_id=self.manifest.provider_id,
            reached=request.target,
            state_digest=hashlib.sha256(
                f"{self.manifest.provider_id}:{request.stage}:{request.target.tick}".encode()
            ).hexdigest(),
        )
        result_fields = {
            "schema_version": "aero-bench.provider-stage-result/v1",
            "run_id": self._run_id,
            "scenario_digest": self._scenario.scenario_digest,
            "provider_id": self.manifest.provider_id,
            "target": request.target,
            "stage": request.stage,
            "step_receipt": receipt,
            "step_receipt_digest": step_receipt_digest_value(receipt),
            "contribution": contribution,
            "predecessor_barriers": (
                () if request.stage == "motion" else request.predecessor_barriers
            ),
        }
        if request.stage == "motion":
            return MotionStageResult(**result_fields)
        result_fields["input_scene_state_digest"] = request.scene_state_digest
        if request.stage == "network":
            return NetworkStageResult(**result_fields)
        return BusinessEnvironmentStageResult(**result_fields)

    async def handle_command(self, request):
        return ProviderCommandResult(
            receipts=(
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id=self.manifest.provider_id,
                    phase="received",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id=self.manifest.provider_id,
                    phase="accepted",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id=self.manifest.provider_id,
                    phase="applied",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id=self.manifest.provider_id,
                    phase="completed",
                    time=request.issued_at,
                ),
            )
        )

    async def observe(self, **kwargs):
        raise AssertionError(f"unexpected observation request: {kwargs}")

    async def finalize(self, request):
        return fake_finalization_receipt(self.manifest, request)

    async def snapshot_digest(self) -> str:
        return hashlib.sha256(b"snapshot").hexdigest()

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


class _ServiceRegistry:
    def verify_runtime_stage_binding(self, **bindings):
        fixture_provider_registry().verify_runtime_stage_binding(**bindings)

    def __init__(
        self,
        *,
        fail_prepare_provider: str | None = None,
        fail_step_provider: str | None = None,
    ):
        self.fail_prepare_provider = fail_prepare_provider
        self.fail_step_provider = fail_step_provider
        self.sessions: dict[str, _ServiceSession] = {}

    def build_session(
        self,
        *,
        provider,
        bundle,
        runtime_endpoint,
        run_id,
        credential,
        scenario,
        clock,
    ):
        manifest = ProviderManifest(
            provider_id=provider.provider_id,
            adapter=provider.adapter,
            implementation=provider.workload.implementation,
            runtime_image=provider.workload.runtime.image,
            config_digest=provider.config.file.sha256,
            capabilities=provider.capabilities,
            protocol_schema=provider.protocol_schema,
            artifact_requirements=provider.artifact_requirements,
        )
        session = _ServiceSession(
            manifest,
            run_id,
            scenario,
            fail_prepare=provider.provider_id == self.fail_prepare_provider,
            fail_step=provider.provider_id == self.fail_step_provider,
        )
        self.sessions[provider.provider_id] = session
        return session


class _MechanicalResolver:
    @property
    def package_id(self) -> str:
        return "mechanical.v1"

    def scenario_projection(self, *, reader, task, environment, agents):
        return ResolvedTaskScenarioProjection(
            logical_endpoint_ids=(),
            logical_capability_ids=(),
            observations=(),
        )

    def resolve(self, *, reader, task, environment, agents, scenario):
        return FeasibilityAssessment(
            package_id=self.package_id,
            feasible=True,
            success_upper_bound=1.0,
            failed_conditions=(),
            bounds=(),
        )


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def _runtime_environment(tmp_path: Path, bundle, run, *, port: int):
    contract_path = tmp_path / "contract.json"
    contract_path.write_bytes(
        canonical_json_bytes(
            HarnessWorkloadContract.from_run(run).model_dump(mode="json")
        )
        + b"\n"
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    environment = {
        "AERO_BENCH_CONTRACT": str(contract_path),
        "AERO_BENCH_BUNDLE_DIR": str(bundle.root),
        "AERO_BENCH_ARTIFACT_DIR": str(artifact_root),
        "AERO_BENCH_RUN_ID": run.run_id,
        "AERO_BENCH_SEED": str(run.seed),
        "AERO_BENCH_ROLE": "harness",
        "AERO_BENCH_WORKLOAD_ID": "harness",
        "AERO_BENCH_PROVIDER_ENDPOINTS": canonical_json_bytes(
            {
                provider.provider_id: {
                    "host": f"{provider.provider_id}.svc",
                    "port": provider.port,
                }
                for provider in run.environment.providers
            }
        ).decode(),
        "AERO_BENCH_PROVIDER_CREDENTIALS": canonical_json_bytes(
            {
                provider.provider_id: f"{index + 1:064x}"
                for index, provider in enumerate(run.environment.providers)
            }
        ).decode(),
        "AERO_BENCH_GATEWAY_BIND_HOST": "127.0.0.1",
        "AERO_BENCH_GATEWAY_PORT": str(port),
        "AERO_BENCH_AGENT_CREDENTIALS": canonical_json_bytes(
            {agent.agent_id: "a" * 64 for agent in run.agents}
        ).decode(),
        "AERO_BENCH_STREAM_INGEST_TOKEN": "b" * 64,
        "AERO_BENCH_RUNTIME_CONTROL_TOKEN": "c" * 64,
    }
    return environment, artifact_root


def _service_fixture(
    tmp_path: Path,
    *,
    port: int | None = None,
    fail_prepare_provider: str | None = None,
    fail_step_provider: str | None = None,
):
    bundle = build_bundle(tmp_path / "bundle")
    selected_port = _free_port() if port is None else port
    _prepare_service_bundle(bundle.root, gateway_port=selected_port)
    resolver = _MechanicalResolver()
    run = with_fixture_provider_stages(
        resolve_suite(
            str(bundle.suite),
            executor_kind="docker_reference",
            task_package_resolvers=(resolver,),
            provider_registry=fixture_provider_registry(),
        )[0]
    )
    environment, artifact_root = _runtime_environment(
        tmp_path, bundle, run, port=selected_port
    )
    registry = _ServiceRegistry(
        fail_prepare_provider=fail_prepare_provider,
        fail_step_provider=fail_step_provider,
    )
    return run, environment, registry, artifact_root


def _inspection_service_fixture(tmp_path: Path):
    bundle = build_bundle(tmp_path / "bundle")
    selected_port = _free_port()
    _prepare_inspection_service_bundle(bundle.root, gateway_port=selected_port)
    run = with_fixture_provider_stages(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    environment, artifact_root = _runtime_environment(
        tmp_path, bundle, run, port=selected_port
    )
    return bundle, run, environment, _ServiceRegistry(), artifact_root


def _prepare_inspection_service_bundle(root: Path, *, gateway_port: int) -> None:
    environment_path = root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    environment["gateway"]["port"] = gateway_port
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )

    suite_path = root / "suite.yaml"
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    suite["cases"][0]["environment"]["sha256"] = hashlib.sha256(
        environment_path.read_bytes()
    ).hexdigest()
    suite_path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")


def _prepare_service_bundle(root: Path, *, gateway_port: int) -> None:
    environment_path = root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    environment["gateway"]["port"] = gateway_port
    environment["harness_artifact_requirements"] = [
        requirement
        for requirement in environment["harness_artifact_requirements"]
        if requirement["artifact_id"] != "artifact.bounds"
    ]

    package_schema = write_text(
        root,
        "schemas/mechanical-package.json",
        '{"type":"object","additionalProperties":true}\n',
    )
    package_file = write_text(
        root,
        "tasks/mechanical-package.json",
        '{"schema_version":"aero-bench.mechanical-task/v1"}\n',
    )

    task_path = root / "tasks/task.yaml"
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["package"] = {
        "package_id": "mechanical.v1",
        "config": {"file": package_file, "schema_file": package_schema},
    }
    task["verifier"]["artifact_requirements"] = [
        requirement
        for requirement in task["verifier"]["artifact_requirements"]
        if requirement["artifact_id"] != "artifact.bounds"
    ]
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")

    suite = yaml.safe_load((root / "suite.yaml").read_text(encoding="utf-8"))
    case = suite["cases"][0]
    case["task"]["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    case["environment"]["sha256"] = hashlib.sha256(
        environment_path.read_bytes()
    ).hexdigest()
    (root / "suite.yaml").write_text(
        yaml.safe_dump(suite, sort_keys=False), encoding="utf-8"
    )


def test_gateway_environment_requires_explicit_canonical_bind_values() -> None:
    with pytest.raises(HarnessRuntimeError, match="GATEWAY_BIND_HOST"):
        _gateway_environment({})
    with pytest.raises(HarnessRuntimeError, match="canonical decimal"):
        _gateway_environment(
            {
                "AERO_BENCH_GATEWAY_BIND_HOST": "127.0.0.1",
                "AERO_BENCH_GATEWAY_PORT": "017432",
            }
        )
    assert _gateway_environment(
        {
            "AERO_BENCH_GATEWAY_BIND_HOST": "127.0.0.1",
            "AERO_BENCH_GATEWAY_PORT": "17432",
        }
    ) == ("127.0.0.1", 17432)


def test_unsupported_harness_artifact_blocks_before_runtime(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    unsupported = ArtifactRequirement(
        artifact_id="artifact.unsupported",
        artifact_type="unsupported.payload",
        producer_id="harness",
        visibility="private",
        relative_path="harness/unsupported.json",
        max_size_bytes=4096,
        source_asset_id=None,
    )
    environment = run.environment.model_copy(
        update={
            "harness_artifact_requirements": (
                *run.environment.harness_artifact_requirements,
                unsupported,
            )
        }
    )
    mutated_run = run.model_copy(update={"environment": environment})

    with pytest.raises(HarnessRuntimeBlocked, match="artifact count differs from task ownership"):
        _validate_harness_artifacts(
            mutated_run,
            bundle=BundleReader(bundle.root),
        )


def test_event_log_is_atomic_private_and_exactly_inventoried(tmp_path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    requirement = ArtifactRequirement(
        artifact_id="artifact.event-log",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="harness/event.log",
        max_size_bytes=4096,
        source_asset_id=None,
    )
    ledger = EventLedger(run_id=TEST_RUN_ID)
    zero = SimulationTime(tick=0, sim_time_ns=0)
    ledger.append_event(source="harness", event_type="run.started", time=zero)
    ledger.append_event(source="harness", event_type="run.completed", time=zero)

    _write_artifact_payloads(
        root=root,
        artifacts=((requirement, ledger_jsonl_bytes(ledger.records)),),
    )

    event_log = root / requirement.relative_path
    assert event_log.is_file()
    assert stat.S_IMODE(event_log.stat().st_mode) == 0o600
    assert not tuple(root.rglob(".*"))
    assert event_log.read_bytes().endswith(b"\n")


def test_event_log_never_invents_completion_on_error(tmp_path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    requirement = ArtifactRequirement(
        artifact_id="artifact.event-log",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="event.log",
        max_size_bytes=4096,
        source_asset_id=None,
    )
    ledger = EventLedger(run_id=TEST_RUN_ID)
    ledger.append_event(
        source="harness",
        event_type="run.started",
        time=SimulationTime(tick=0, sim_time_ns=0),
    )

    with pytest.raises(HarnessRuntimeError, match="completed or aborted"):
        _write_harness_artifacts(
            root=root,
            plan=_HarnessArtifactPlan(
                event_requirement=requirement,
                scene_state_history_requirement=requirement.model_copy(update={
                    "artifact_id": "artifact.scene-history",
                    "artifact_type": "scene.state-history",
                    "relative_path": "scene-history.jsonl",
                }),
            ),
            records=ledger.records,
            scene_state_history=(),
        )
    assert not tuple(root.iterdir())


def test_event_log_accepts_authoritative_abort_tail(tmp_path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    requirement = ArtifactRequirement(
        artifact_id="artifact.event-log",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="event.log",
        max_size_bytes=4096,
        source_asset_id=None,
    )
    ledger = EventLedger(run_id=TEST_RUN_ID)
    zero = SimulationTime(tick=0, sim_time_ns=0)
    ledger.append_event(source="harness", event_type="run.started", time=zero)
    ledger.append_event(source="harness", event_type="run.aborted", time=zero)

    _write_harness_artifacts(
        root=root,
        plan=_HarnessArtifactPlan(
            event_requirement=requirement,
            scene_state_history_requirement=requirement.model_copy(update={
                "artifact_id": "artifact.scene-history",
                "artifact_type": "scene.state-history",
                "relative_path": "scene-history.jsonl",
            }),
        ),
        records=ledger.records,
        scene_state_history=(),
    )

    assert (root / "event.log").is_file()
    assert (root / "scene-history.jsonl").read_bytes() == b""


class _CommandSession:
    async def handle_command(self, request: CommandRequest):
        return ProviderCommandResult(
            receipts=(
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id="flight",
                    phase="received",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id="flight",
                    phase="failed",
                    time=request.issued_at,
                    detail="provider rejected command",
                ),
            )
        )


def test_tool_adapter_preserves_real_provider_receipts() -> None:
    request = CommandRequest(
        run_id="a" * 64,
        command_id="command.one",
        agent_id="agent.one",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(),
    )

    internal = asyncio.run(_ProviderToolAdapter(_CommandSession()).invoke(request))
    result = internal.result

    assert internal.events == ()
    assert result.command_id == request.command_id
    assert tuple(receipt.phase for receipt in result.receipts) == (
        "received",
        "failed",
    )
    assert result.response == ()
    assert result.receipts[0].run_id == request.run_id
    assert result.receipts[1].detail == "provider rejected command"


async def _connect_to_gateway(port: int) -> JsonLineRpcTransport:
    endpoint = type("Endpoint", (), {"host": "127.0.0.1", "port": port})()
    for _ in range(100):
        try:
            return await JsonLineRpcTransport.connect(endpoint, component="gateway")
        except ProviderRpcError:
            await asyncio.sleep(0.005)
    raise AssertionError("Harness Gateway did not become available")


def _completion_payload(
    run,
    *,
    tick: int = 0,
    disposition: str = "finished",
    completion_id: str = "completion.one",
) -> dict[str, object]:
    return {
        "schema_version": "aero-bench.agent-turn-completion/v1",
        "run_id": run.run_id,
        "agent_id": run.agents[0].agent_id,
        "completion_id": completion_id,
        "at": {
            "tick": tick,
            "sim_time_ns": tick * run.environment.clock.step_ns,
        },
        "disposition": disposition,
        "command_ids": [],
        "observation_ids": [],
    }


async def _advance_once(transport: JsonLineRpcTransport, run) -> None:
    result = await transport.request(
        "turn.complete",
        {
            "token": "a" * 64,
            "completion": _completion_payload(
                run,
                disposition="advance",
                completion_id="completion.advance",
            ),
        },
    )
    assert result["status"] == "advanced"


def _service_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    registry: _ServiceRegistry,
    *,
    package_resolver=None,
) -> HarnessRuntimeService:
    resolver = _MechanicalResolver() if package_resolver is None else package_resolver
    monkeypatch.setattr(runtime_service, "builtin_provider_registry", lambda: registry)
    monkeypatch.setattr(
        runtime_service,
        "builtin_task_package_resolvers",
        lambda: (resolver,),
    )
    return HarnessRuntimeService.from_environment(environment)


def test_public_runtime_factory_has_no_provider_or_resolver_override() -> None:
    assert tuple(
        inspect.signature(HarnessRuntimeService.from_environment).parameters
    ) == ("environment",)


def test_normal_agent_termination_writes_completed_event_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, environment, registry, artifact_root = _service_fixture(tmp_path)

    async def scenario() -> None:
        service = _service_from_environment(monkeypatch, environment, registry)
        task = asyncio.create_task(service.run_forever())
        transport = await _connect_to_gateway(run.environment.gateway.port)
        try:
            await _advance_once(transport, run)
            result = await transport.request(
                "turn.complete",
                {
                    "token": "a" * 64,
                    "completion": _completion_payload(
                        run,
                        tick=1,
                        completion_id="completion.finished",
                    ),
                },
            )
            assert result["status"] == "terminated"
            await task
        finally:
            await transport.close()

    asyncio.run(scenario())
    ledger = EventLedger.read_jsonl(artifact_root / "harness/event.log")
    assert ledger.records[-1].event.event_type == "run.completed"
    assert all(session.shutdown_calls == 1 for session in registry.sessions.values())


def test_inspection_runtime_writes_canonical_event_log_and_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, run, environment, registry, artifact_root = _inspection_service_fixture(
        tmp_path
    )

    async def scenario() -> None:
        service = _service_from_environment(
            monkeypatch,
            environment,
            registry,
            package_resolver=InspectionTaskPackageResolver(),
        )
        task = asyncio.create_task(service.run_forever())
        transport = await _connect_to_gateway(run.environment.gateway.port)
        try:
            await _advance_once(transport, run)
            result = await transport.request(
                "turn.complete",
                {
                    "token": "a" * 64,
                    "completion": _completion_payload(
                        run,
                        tick=1,
                        completion_id="completion.finished",
                    ),
                },
            )
            assert result["status"] == "terminated"
            await task
        finally:
            await transport.close()

    asyncio.run(scenario())

    event_path = artifact_root / "harness/event.log"
    bounds_path = artifact_root / "harness/theoretical-bounds.json"
    assert {
        path.relative_to(artifact_root).as_posix()
        for path in artifact_root.rglob("*")
        if path.is_file()
    } == {
        "harness/event.log",
        "harness/scene-states.jsonl",
        "harness/theoretical-bounds.json",
    }
    assert stat.S_IMODE(event_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(bounds_path.stat().st_mode) == 0o600
    assert EventLedger.read_jsonl(event_path).records[-1].event.event_type == (
        "run.completed"
    )

    payload = bounds_path.read_bytes()
    evidence = TheoreticalBoundsEvidence.model_validate_json(payload)
    assert payload == canonical_json_bytes(evidence.model_dump(mode="json"))
    assert evidence.source_artifact_id == "artifact.bounds"
    assert evidence.run_id == run.run_id
    package, resolved_bounds, _ = resolve_inspection_run_context(
        reader=BundleReader(bundle.root),
        task=run.task,
        environment=run.environment,
        agents=run.agents,
        scenario=run.scenario,
    )
    assert evidence.bounds == calculate_theoretical_bounds(
        resolved_bounds.bounds,
        network_delivery_required=package.network_delivery_required,
    )
    assert evidence.provider_configs == resolved_bounds.provider_configs


def test_inspection_abort_still_writes_static_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run, environment, registry, artifact_root = _inspection_service_fixture(tmp_path)

    async def scenario() -> None:
        service = _service_from_environment(
            monkeypatch,
            environment,
            registry,
            package_resolver=InspectionTaskPackageResolver(),
        )
        task = asyncio.create_task(service.run_forever())
        service.gateway.termination_requested.set()
        await task

    asyncio.run(scenario())

    ledger = EventLedger.read_jsonl(artifact_root / "harness/event.log")
    assert ledger.records[-1].event.event_type == "run.aborted"
    evidence = TheoreticalBoundsEvidence.model_validate_json(
        (artifact_root / "harness/theoretical-bounds.json").read_bytes()
    )
    assert evidence.run_id == run.run_id


def test_explicit_termination_aborts_and_closes_every_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, environment, registry, artifact_root = _service_fixture(tmp_path)

    async def scenario() -> None:
        service = _service_from_environment(monkeypatch, environment, registry)
        task = asyncio.create_task(service.run_forever())
        service.gateway.termination_requested.set()
        await task

    asyncio.run(scenario())
    ledger = EventLedger.read_jsonl(artifact_root / "harness/event.log")
    assert ledger.records[-1].event.event_type == "run.aborted"
    assert all(session.shutdown_calls == 1 for session in registry.sessions.values())
    assert not any(
        record.event.event_type == "run.completed" for record in ledger.records
    )


def test_prepare_failure_aborts_partial_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, environment, registry, artifact_root = _service_fixture(
        tmp_path, fail_prepare_provider="flight"
    )

    async def scenario() -> None:
        service = _service_from_environment(monkeypatch, environment, registry)
        with pytest.raises(Exception, match="Provider prepare"):
            await service.run_forever()

    asyncio.run(scenario())
    ledger = EventLedger.read_jsonl(artifact_root / "harness/event.log")
    assert ledger.records[-1].event.event_type == "run.aborted"
    assert not any(
        record.event.event_type == "runtime.identity" for record in ledger.records
    )
    assert all(session.shutdown_calls == 1 for session in registry.sessions.values())


def test_gateway_bind_failure_aborts_after_provider_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = _free_port()
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", port))
    try:
        run, environment, registry, artifact_root = _service_fixture(
            tmp_path, port=port
        )

        async def scenario() -> None:
            service = _service_from_environment(monkeypatch, environment, registry)
            with pytest.raises(OSError):
                await service.run_forever()

        asyncio.run(scenario())
        ledger = EventLedger.read_jsonl(artifact_root / "harness/event.log")
        assert ledger.records[-1].event.event_type == "run.aborted"
        assert all(
            session.shutdown_calls == 1 for session in registry.sessions.values()
        )
    finally:
        occupied.close()


def test_coordinator_failure_triggers_runtime_failed_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, environment, registry, artifact_root = _service_fixture(
        tmp_path, fail_step_provider="flight"
    )

    async def scenario() -> None:
        service = _service_from_environment(monkeypatch, environment, registry)
        task = asyncio.create_task(service.run_forever())
        transport = await _connect_to_gateway(run.environment.gateway.port)
        try:
            with pytest.raises(ProviderRemoteError):
                await transport.request(
                    "turn.complete",
                    {
                        "token": "a" * 64,
                        "completion": {
                            **_completion_payload(run),
                            "disposition": "advance",
                        },
                    },
                )
            await task
        finally:
            await transport.close()

    asyncio.run(scenario())
    ledger = EventLedger.read_jsonl(artifact_root / "harness/event.log")
    aborted = ledger.records[-1].event
    assert aborted.event_type == "run.aborted"
    payload = {item.name: item.value for item in aborted.payload}
    assert payload["failure_class"] == "runtime_failed"


def test_drain_deadline_uses_the_slowest_declared_timeout(tmp_path: Path) -> None:
    run, _, _, _ = _service_fixture(tmp_path)

    assert _drain_deadline_seconds(run) == 30.0


def test_agent_inspection_harness_constructs_with_flight_sensor_grants(tmp_path):
    import argparse
    import json

    from aero_bench.runtime.hooks import ValidatedObservation
    from aero_bench.tasks.inspection.runtime_hook import InspectionRuntimeHookError
    from tests.runtime.test_harness_bootstrap import _environment
    from tools import build_urban_infrastructure_inspection_v1 as builder

    lock = tmp_path / "images.json"
    lock.write_text(json.dumps({
        "schema_version": "aero-bench.runtime-image-lock/v1",
        "images": {
            name: f"registry.example/test/{name}@sha256:{index:064x}"
            for index, name in enumerate(
                sorted(builder._pending_images(agent_acceptance=True)), start=1,
            )
        },
    }))
    root = builder.build(argparse.Namespace(
        output_root=str(tmp_path / "bundle"),
        runtime_source_revision=builder._managed_source_closure()[0],
        images_lock=str(lock), agent_acceptance=True, force=False,
    ))
    run = resolve_bundle(root / "suite.yaml", executor_kind="docker_reference")[0]
    contract_path = tmp_path / "harness.json"
    contract_path.write_bytes(canonical_json_bytes(
        HarnessWorkloadContract.from_run(run).model_dump(mode="json")
    ) + b"\n")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    environment = _environment(
        root, contract_path=contract_path, artifact_root=artifact_root,
    )
    environment.update({
        "AERO_BENCH_GATEWAY_BIND_HOST": "127.0.0.1",
        "AERO_BENCH_GATEWAY_PORT": str(run.environment.gateway.port),
        "AERO_BENCH_AGENT_CREDENTIALS": canonical_json_bytes({
            agent.agent_id: f"{index:064x}"
            for index, agent in enumerate(run.agents, start=1)
        }).decode(),
        "AERO_BENCH_STREAM_INGEST_TOKEN": "b" * 64,
        "AERO_BENCH_RUNTIME_CONTROL_TOKEN": "c" * 64,
    })
    service = HarnessRuntimeService.from_environment(environment)
    hook = service._runtime_hook
    bindings = [b for b in run.scenario.task.observations if b.flight is not None]
    assert {b.flight.observation_kind for b in bindings} == {"telemetry", "gnss"}
    initial_records = service.bootstrap.ledger.records
    for binding in bindings:
        observation = ValidatedObservation(
            run_id=run.run_id, agent_id=binding.agent_id,
            provider_id=binding.endpoint_id, observation_id=binding.observation_id,
            time=SimulationTime(tick=0, sim_time_ns=0),
            request_digest="d" * 64, payload_digest="e" * 64,
        )
        asyncio.run(hook.on_validated_observation(observation))
        with pytest.raises(InspectionRuntimeHookError, match="not declared"):
            asyncio.run(hook.on_validated_observation(observation.model_copy(
                update={"provider_id": "provider.ungranted"},
            )))
    assert service.bootstrap.ledger.records == initial_records
