from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    ImplementationIdentity,
    NamedValue,
)
from aero_bench.gateway import QueryRequest
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.inspection_business import (
    PROTOCOL_VERSION,
    InspectionBusinessConfig,
    InspectionBusinessProvider,
    InspectionBusinessProviderError,
    pinned_package_digest,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcServer
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    BusinessEnvironmentStageResult,
    CommandRequest,
    ProviderEvent,
    SceneContribution,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    stage_barrier_digest_value,
    step_receipt_digest_value,
)
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.business import InspectionBusinessService
from aero_bench.tasks.inspection.contracts import (
    BusinessCommandEnvelope,
    BusinessQuery,
    ClaimWorkOrderCommand,
)
from tests.test_inspection_business import _package
from tests.world.support import deterministic_inspection_scenario


RUN_ID = "a" * 64
IMAGE = "registry.test/inspection-business@sha256:" + "1" * 64
CONFIG_DIGEST = "f" * 64
STATE_SCHEMA = "inspection.business.state.v1"
# The executor-issued, run-scoped credential the registry hands the session.
SESSION_TOKEN = "9" * 64
PREPARE_FIELDS = frozenset(
    {
        "provider_id",
        "run_id",
        "protocol_version",
        "runtime_image",
        "config_digest",
        "artifact_requirements",
        "session_token",
        "task_id",
        "package_digest",
        "task_package",
    }
)


def artifact_requirement() -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id="artifact.business",
        artifact_type="business.state",
        producer_id="business",
        visibility="private",
        relative_path="business/state.json",
        max_size_bytes=1_048_576,
        source_asset_id=None,
    )


def manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id="business",
        adapter="inspection.business",
        implementation=ImplementationIdentity(
            component_id="inspection.business",
            kind="mechanical_fixture",
            source_uri="https://github.com/moby/moby",
            source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
            version="test-fixture-1",
        ),
        runtime_image=IMAGE,
        config_digest=CONFIG_DIGEST,
        capabilities=("business.work-order",),
        protocol_schema=FileRef(path="business.json", sha256="f" * 64),
        artifact_requirements=(artifact_requirement(),),
    )


def config() -> InspectionBusinessConfig:
    return InspectionBusinessConfig(
        schema_version="aero-bench.inspection-business/v1",
        provider_id="business",
        task_package=_package(),
    )


def state_receipt(target: SimulationTime, digest: str) -> dict[str, object]:
    return StepReceipt(
        run_id=RUN_ID,
        provider_id="business",
        reached=target,
        state_digest=digest,
        events=(
            ProviderEvent(
                provider_id="business",
                event_id=f"state.{target.tick}",
                time=target,
                payload_schema_id=STATE_SCHEMA,
            ),
            ProviderEvent(
                provider_id="business",
                event_id="public.status",
                time=target,
                payload_schema_id="public.status.v3",
            ),
        ),
    ).model_dump(mode="json")


@dataclass
class StubProviderService:
    """Server-side test double standing in for the real provider workload.

    It answers over the genuine JsonLineRpcServer/JsonLineRpcTransport wire
    path; the production session has no transport seam to substitute.
    """

    frames: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    readiness: dict[str, object] | None = None
    empty_command_receipts: bool = False
    machine: InspectionBusinessService | None = None
    staged_input_count: int = 0

    async def __call__(
        self, operation: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.frames.append((operation, dict(payload)))
        if operation == "prepare":
            if self.readiness is not None:
                return dict(self.readiness)
            return {
                "status": "ready",
                "provider_id": "business",
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": IMAGE,
                "config_digest": CONFIG_DIGEST,
            }
        if operation == "reset":
            self.machine = InspectionBusinessService(
                config().task_package, run_id=RUN_ID
            )
            self.staged_input_count = 0
            return {
                "receipt": state_receipt(
                    SimulationTime(tick=0, sim_time_ns=0), "c" * 64
                )
            }
        if operation == "step_stage":
            request = BusinessEnvironmentStageRequest.model_validate(
                payload["request"]
            )
            self.staged_input_count += 1
            state_digest = "d" * 64
            receipt = StepReceipt(
                run_id=RUN_ID,
                provider_id="business",
                reached=request.target,
                state_digest=state_digest,
                events=(
                    ProviderEvent(
                        provider_id="business",
                        event_id=f"state.{request.target.tick}",
                        time=request.target,
                        payload_schema_id=STATE_SCHEMA,
                        payload=tuple(
                            NamedValue(name=name, value=value)
                            for name, value in sorted(
                                {
                                    "current_time_ns": request.target.sim_time_ns,
                                    "stage_input_count": self.staged_input_count,
                                    "stage_input_motion_barrier_digest": (
                                        request.predecessor_barriers[0].barrier_digest
                                    ),
                                    "stage_input_predecessor_barriers": (
                                        canonical_json_bytes(
                                            [
                                                item.model_dump(mode="json")
                                                for item in request.predecessor_barriers
                                            ]
                                        ).decode("utf-8")
                                    ),
                                    "stage_input_scenario_digest": request.scenario_digest,
                                    "stage_input_scene_state_digest": request.scene_state_digest,
                                    "state_digest": state_digest,
                                }.items()
                            )
                        ),
                    ),
                    ProviderEvent(
                        provider_id="business",
                        event_id="public.status",
                        time=request.target,
                        payload_schema_id="public.status.v3",
                    ),
                ),
            )
            contribution_fields = {
                "schema_version": "aero-bench.scene-contribution/v1",
                "run_id": RUN_ID,
                "scenario_digest": request.scenario_digest,
                "at": request.target,
                "stage": "business_environment",
                "provider_id": "business",
                "samples": (),
                "attribute_updates": (),
            }
            unsigned = SceneContribution.model_construct(
                **contribution_fields,
                payload_digest="0" * 64,
                contribution_digest="0" * 64,
            )
            payload_digest = scene_contribution_payload_digest_value(unsigned)
            contribution = SceneContribution(
                **contribution_fields,
                payload_digest=payload_digest,
                contribution_digest=scene_contribution_digest_value(
                    unsigned.model_copy(update={"payload_digest": payload_digest})
                ),
            )
            result = BusinessEnvironmentStageResult(
                schema_version="aero-bench.provider-stage-result/v1",
                run_id=RUN_ID,
                scenario_digest=request.scenario_digest,
                provider_id="business",
                target=request.target,
                stage="business_environment",
                step_receipt=receipt,
                step_receipt_digest=step_receipt_digest_value(receipt),
                contribution=contribution,
                input_scene_state_digest=request.scene_state_digest,
                predecessor_barriers=request.predecessor_barriers,
            )
            return {"result": result.model_dump(mode="json")}
        if operation == "command":
            if self.empty_command_receipts:
                return {"receipts": []}
            request = CommandRequest.model_validate(payload["request"])
            assert self.machine is not None
            result = self.machine.apply(
                BusinessCommandEnvelope(
                    run_id=RUN_ID,
                    command=ClaimWorkOrderCommand(
                        command_id=request.command_id,
                        work_order_id="wo.1",
                        actor_id=request.agent_id,
                        issued_at=request.issued_at,
                    ),
                )
            )
            return {
                "receipts": [
                    {
                        "run_id": RUN_ID,
                        "command_id": request.command_id,
                        "provider_id": "business",
                        "phase": phase,
                        "time": request.issued_at.model_dump(mode="json"),
                    }
                    for phase in ("received", "accepted", "applied", "completed")
                ],
                "history_record": result.history_record.model_dump(mode="json"),
            }
        if operation == "query":
            query = BusinessQuery.model_validate(payload["query"])
            return {
                "result": {
                    "run_id": RUN_ID,
                    "query_id": query.query_id,
                    "observed_at": query.issued_at.model_dump(mode="json"),
                    "work_orders": [
                        {
                            "work_order_id": "wo.1",
                            "target_id": "asset.1",
                            "status": "claimed",
                            "version": 1,
                            "last_time": query.issued_at.model_dump(mode="json"),
                            "claimed_by": "agent.1",
                        }
                    ],
                }
            }
        if operation == "snapshot":
            return {"snapshot_digest": "e" * 64}
        if operation == "shutdown":
            return {"status": "stopped"}
        raise AssertionError(operation)


@contextlib.asynccontextmanager
async def _serving() -> AsyncIterator[tuple[StubProviderService, RuntimeEndpoint]]:
    service = StubProviderService()
    server = JsonLineRpcServer(service)
    handle = await server.start(host="127.0.0.1", port=0)
    port = handle.sockets[0].getsockname()[1]
    try:
        yield service, RuntimeEndpoint(host="127.0.0.1", port=port)
    finally:
        await server.graceful_close()
        await handle.wait_closed()


def _provider(endpoint: RuntimeEndpoint) -> InspectionBusinessProvider:
    return InspectionBusinessProvider(
        config=config(),
        manifest=manifest(),
        runtime_endpoint=endpoint,
        run_id=RUN_ID,
        session_token=SESSION_TOKEN,
        scenario=deterministic_inspection_scenario(),
    )


def _stage_request() -> BusinessEnvironmentStageRequest:
    source = deterministic_inspection_scenario()
    static_entity = next(entity for entity in source.entities if entity.state == "static")
    scenario = source.model_copy(update={"entities": (static_entity,)})
    assembler = SceneStateAssembler(scenario)
    target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
    barrier_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario.scenario_digest,
        "at": target,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": (),
        "receipts": (),
        "receipt_digests": (),
    }
    unsigned = StageBarrier.model_construct(
        **barrier_fields,
        barrier_digest="0" * 64,
    )
    barrier = StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(unsigned),
    )
    scene_state = assembler.assemble(
        run_id=RUN_ID,
        at=target,
        barrier=barrier,
        contributions=(),
        previous_scene_state=None,
    )
    return BusinessEnvironmentStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=RUN_ID,
        scenario_digest=scenario.scenario_digest,
        provider_id="business",
        target=target,
        stage="business_environment",
        scene_state=scene_state,
        scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(
                stage="motion",
                barrier_digest=barrier.barrier_digest,
            ),
        ),
    )


def test_inspection_business_config_is_strict_domain_declaration() -> None:
    raw = config().model_dump(mode="json")
    assert raw["task_package"] == _package().model_dump(mode="json")
    assert "runtime_image" not in raw
    assert "endpoint" not in raw
    assert "protocol_version" not in raw
    with pytest.raises(ValidationError, match="unexpected"):
        InspectionBusinessConfig.model_validate({**raw, "unexpected": True})
    with pytest.raises(ValidationError):
        InspectionBusinessConfig.model_validate(
            {**raw, "runtime_image": {"image": IMAGE}}
        )
    with pytest.raises(ValidationError):
        InspectionBusinessConfig.model_validate(
            {**raw, "endpoint": {"host": "business", "port": 17435}}
        )
    with pytest.raises(ValidationError):
        InspectionBusinessConfig.model_validate(
            {**raw, "protocol_version": "aero-bench.inspection-business-rpc/v1"}
        )


def test_agent_query_projects_authoritative_public_work_order_data() -> None:
    async def run() -> None:
        async with _serving() as (_, endpoint):
            provider = _provider(endpoint)
            await provider.prepare()
            await provider.reset(seed=23)
            now = SimulationTime(tick=0, sim_time_ns=0)
            summary = await provider.query(
                QueryRequest(
                    run_id=RUN_ID,
                    query_id="query.summary",
                    agent_id="agent.1",
                    query_type="business.work-orders",
                    issued_at=now,
                )
            )
            detail = await provider.query(
                QueryRequest(
                    run_id=RUN_ID,
                    query_id="query.detail",
                    agent_id="agent.1",
                    query_type="business.work-order",
                    issued_at=now,
                    arguments=(NamedValue(name="work_order_id", value="wo.1"),),
                )
            )
            await provider.shutdown()

        summary_payload = {item.name: item.value for item in summary.payload}
        assert summary_payload["work_order_count"] == 1
        assert "wo.1" in summary_payload["work_orders_json"]
        detail_payload = {item.name: item.value for item in detail.payload}
        assert detail_payload["work_order_id"] == "wo.1"
        assert detail_payload["target_id"] == "asset.1"
        assert detail_payload["navigation_coordinate_frame"] == "WGS84+ENU"
        assert detail_payload["height_datum"] == "wgs84-ellipsoid+amsl+agl"
        assert detail_payload["arrival_tolerance_m"] > 0
        assert detail_payload["required_observation_id"] == "obs.1"
        assert detail_payload["upload_deadline_ns"] > 0
        serialized = canonical_json_bytes(detail_payload)
        assert b"truth" not in serialized
        assert b"history" not in serialized

    asyncio.run(run())


def test_prepare_payload_binds_task_identity_and_session_token() -> None:
    async def run() -> None:
        async with _serving() as (service, endpoint):
            provider = _provider(endpoint)
            await provider.prepare()
        operation, payload = service.frames[0]
        assert operation == "prepare"
        assert set(payload) == PREPARE_FIELDS
        assert "endpoint" not in payload
        task_package = _package().model_dump(mode="json")
        assert payload["task_id"] == "inspection.task"
        assert payload["package_digest"] == pinned_package_digest(task_package)
        assert (
            payload["package_digest"]
            == hashlib.sha256(canonical_json_bytes(task_package)).hexdigest()
        )
        assert payload["task_package"] == task_package
        assert payload["protocol_version"] == PROTOCOL_VERSION
        assert payload["runtime_image"] == IMAGE
        assert payload["config_digest"] == CONFIG_DIGEST
        assert payload["artifact_requirements"] == [
            artifact_requirement().model_dump(mode="json")
        ]
        assert payload["session_token"] == SESSION_TOKEN

    asyncio.run(run())


def test_inspection_business_provider_binds_workload_identity() -> None:
    asyncio.run(_test_inspection_business_provider_binds_workload_identity())


async def _test_inspection_business_provider_binds_workload_identity() -> None:
    async with _serving() as (service, endpoint):
        provider = _provider(endpoint)
        await provider.prepare()
        reset = await provider.reset(seed=23)
        assert reset.reached == SimulationTime(tick=0, sim_time_ns=0)
        request = _stage_request()
        target = request.target
        stage_result = await provider.step_stage(request)
        assert stage_result.step_receipt.reached == target
        command = CommandRequest(
            run_id=RUN_ID,
            command_id="command.claim",
            agent_id="agent.1",
            tool_id="business.claim",
            issued_at=target,
        )
        outcome = await provider.handle_command(command)
        assert [item.phase for item in outcome.receipts] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        assert len(outcome.events) == 1
        event = outcome.events[0]
        assert event.provider_id == "business"
        assert event.event_id == "business.transition"
        assert event.time == target
        assert service.machine is not None
        assert {item.name: item.value for item in event.payload} == {
            "business_history_record_hash": service.machine.history[0].record_hash,
            "business_history_sequence": 0,
            "command_id": command.command_id,
            "schema_id": "aero-bench.business-history/v1",
        }
        result = await provider.query_business(
            BusinessQuery(
                run_id=RUN_ID,
                query_id="query.1",
                kind="work_order",
                work_order_id="wo.1",
                issued_at=target,
            )
        )
        assert result.work_orders[0].status.value == "claimed"
        assert await provider.snapshot_digest()
        await provider.shutdown()
        assert [operation for operation, _ in service.frames] == [
            "prepare",
            "reset",
            "step_stage",
            "command",
            "query",
            "snapshot",
            "shutdown",
        ]
    # Every frame presents the same executor-issued token, prepare included.
    tokens = {
        payload.get("session_token")
        for _, payload in service.frames
        if payload.get("session_token") is not None
    }
    assert len(tokens) == 1


def test_inspection_business_pinned_token_wins_over_payload_override() -> None:
    """Counterexample: a payload key can never replace the pinned credential.

    The executor-issued session token is bound at construction; a frame that
    tries to smuggle a different ``session_token`` inside the payload must
    still present the pinned one on the wire.
    """

    async def run() -> None:
        async with _serving() as (service, endpoint):
            provider = _provider(endpoint)
            await provider.prepare()
            response = await provider._request("snapshot", {"session_token": "8" * 64})
            assert response == {"snapshot_digest": "e" * 64}
            await provider.shutdown()
        for _, payload in service.frames:
            assert payload["session_token"] == SESSION_TOKEN

    asyncio.run(run())


def test_inspection_business_provider_rejects_readiness_mismatch() -> None:
    asyncio.run(_test_inspection_business_provider_rejects_readiness_mismatch())


async def _test_inspection_business_provider_rejects_readiness_mismatch() -> None:
    for readiness in (
        {"provider_id": "other"},
        {"protocol_version": "aero-bench.other/v1"},
        {"runtime_image": "registry.test/other@sha256:" + "2" * 64},
        {"config_digest": "e" * 64},
    ):
        async with _serving() as (service, endpoint):
            service.readiness = {
                "status": "ready",
                "provider_id": "business",
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": IMAGE,
                "config_digest": CONFIG_DIGEST,
                **readiness,
            }
            provider = _provider(endpoint)
            with pytest.raises(InspectionBusinessProviderError):
                await provider.prepare()


def test_inspection_business_provider_rejects_bad_command_receipt_phases() -> None:
    asyncio.run(_test_inspection_business_provider_rejects_bad_command_receipt_phases())


async def _test_inspection_business_provider_rejects_bad_command_receipt_phases() -> (
    None
):
    async with _serving() as (service, endpoint):
        service.empty_command_receipts = True
        provider = _provider(endpoint)
        await provider.prepare()
        await provider.reset(seed=23)
        with pytest.raises(
            InspectionBusinessProviderError, match="no command receipts"
        ):
            await provider.handle_command(
                CommandRequest(
                    run_id=RUN_ID,
                    command_id="command.claim",
                    agent_id="agent.1",
                    tool_id="business.claim",
                    issued_at=SimulationTime(tick=1, sim_time_ns=1_000_000_000),
                )
            )


def test_inspection_business_provider_rejects_non_monotonic_steps() -> None:
    asyncio.run(_test_inspection_business_provider_rejects_non_monotonic_steps())


async def _test_inspection_business_provider_rejects_non_monotonic_steps() -> None:
    async with _serving() as (_, endpoint):
        provider = _provider(endpoint)
        await provider.prepare()
        await provider.reset(seed=23)
        request = _stage_request()
        await provider.step_stage(request)
        with pytest.raises(InspectionBusinessProviderError, match="monotonically"):
            await provider.step_stage(request)
