"""Client tests for the Logistics Business provider session.

The client is driven against the REAL service module from
``containers/logistics-business/service.py`` over a genuine local
``JsonLineRpcServer``/``JsonLineRpcTransport`` loopback, so every prepare,
reset, staged step, command, query, snapshot, finalization and shutdown frame
crosses the same JSON-line wire a materialized run would use. The inputs are
synthetic single-package fixtures; this is a focused unit round trip, not a
formal milestone run.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    ImplementationIdentity,
    NamedValue,
)
from aero_bench.gateway.contracts import QueryRequest
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.logistics_business import (
    LOGISTICS_ORDER_CREATE_TOOL,
    ORDER_CREATED_EVENT_SCHEMA,
    PROTOCOL_VERSION,
    LogisticsBusinessConfig,
    LogisticsBusinessProvider,
    LogisticsBusinessProviderError,
    pinned_package_digest,
)
from aero_bench.providers.logistics_business.queries import (
    LOGISTICS_QUERY_FACILITIES,
    LogisticsBusinessQuery,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcServer, JsonLineRpcTransport
from aero_bench.runtime.contracts import (
    CommandRequest,
    ProviderFinalizationRequest,
    SimulationTime,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes

from tests.providers.test_logistics_business_service import (
    _SERVICE,
    CAPABILITIES,
    IMAGE,
    PROVIDER_ID,
    RUN_ID,
    SEED,
    SESSION_TOKEN,
    _make_service,
    _requirement,
    _resolved_scenario,
    _stage_request,
)


def _manifest(config_digest: str) -> ProviderManifest:
    return ProviderManifest(
        provider_id=PROVIDER_ID,
        adapter="logistics.business",
        implementation=ImplementationIdentity(
            component_id="logistics.business",
            kind="mechanical_fixture",
            source_uri="https://github.com/moby/moby",
            source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
            version="test-fixture-1",
        ),
        runtime_image=IMAGE,
        config_digest=config_digest,
        capabilities=CAPABILITIES,
        protocol_schema=FileRef(path="protocol.json", sha256="f" * 64),
        artifact_requirements=(ArtifactRequirement.model_validate(_requirement()),),
    )


def _config() -> LogisticsBusinessConfig:
    from tests.providers.test_logistics_business_service import _config_document

    return LogisticsBusinessConfig.model_validate(_config_document())


async def _client(service) -> LogisticsBusinessProvider:
    endpoint = RuntimeEndpoint(host="127.0.0.1", port=0)
    return LogisticsBusinessProvider(
        config=_config(),
        manifest=_manifest(service._workload_identity.config_digest),
        runtime_endpoint=endpoint,
        run_id=RUN_ID,
        session_token=SESSION_TOKEN,
        scenario=_resolved_scenario(),
    )


def _args(*pairs: tuple[str, object]) -> tuple[NamedValue, ...]:
    return tuple(
        NamedValue(name=name, value=value) for name, value in sorted(pairs)
    )


def _run(coro) -> None:
    asyncio.run(coro)


def _online_create_args(
    *, order_id: str, actor_id: str = "logistics.business"
) -> tuple[tuple[str, object], ...]:
    return (
        ("order_id", order_id),
        ("origin_facility_id", "facility-1"),
        ("destination_facility_id", "facility-2"),
        ("hub_handoff_facility_id", "hub-3"),
        ("cargo_mass_kg", 1),
        ("release_time_s", 1),
        ("deadline_s", 900),
        ("actor_id", actor_id),
    )


def _swap_create_argument(
    request: CommandRequest, **changes: object
) -> CommandRequest:
    """Return a copy of a create request with selected arguments replaced.

    ``model_copy`` deliberately does not re-run strict validators, so the
    tampered request (duplicate or missing arguments, or an argument that no
    longer matches the accepted arrival) is exactly the adversarial surface the
    provider must reject without any live pool/event mutation.
    """
    arguments = {item.name: item.value for item in request.arguments}
    for name, value in changes.items():
        arguments[name] = value
    return request.model_copy(
        update={
            "arguments": tuple(
                NamedValue(name=name, value=value)
                for name, value in sorted(arguments.items())
            )
        }
    )


def _duplicate_create_argument_request(request: CommandRequest) -> CommandRequest:
    first = next(arg for arg in request.arguments if arg.name == "order_id")
    return request.model_copy(update={"arguments": (*request.arguments, first)})


def _drop_create_argument_request(request: CommandRequest) -> CommandRequest:
    dropped = tuple(
        arg for arg in request.arguments if arg.name != "release_time_s"
    )
    return request.model_copy(update={"arguments": dropped})


class _AdversarialCreateClient(LogisticsBusinessProvider):
    """Create client that validates the arrival against a tampered command.

    The workload still produces the genuine arrival record over the real
    loopback wire. The client then validates that response against a command
    whose create argument(s) were swapped before ``_handle_order_created`` runs,
    proving the provider binds the accepted arrival to the EXACT command content
    it is validating -- never merely to any order/actor the principal controls.
    """

    def __init__(self, *, tamper, **kwargs):
        super().__init__(**kwargs)
        self._tamper = tamper
        self.rejected: list[str] = []
        self.accepted_order_ids: list[str] = []

    def _handle_order_created(self, receipts, request, response):
        changed = self._tamper(request)
        try:
            result = super()._handle_order_created(receipts, changed, response)
        except LogisticsBusinessProviderError as exc:
            self.rejected.append(str(exc))
            raise
        self.accepted_order_ids.append(self._orders_pool[-1].order_id)
        return result


def _two_business_central_config_document() -> dict[str, object]:
    """A single native principal explicitly bound to TWO business actors."""

    from aero_bench.tasks.logistics.contracts import lower_logistics_task_package
    from tests.tasks.test_logistics_package import _package_document

    package = lower_logistics_task_package(
        _package_document(
            actors=[
                {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
                {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
                {"actor_id": "logistics.business", "role": "business"},
                {"actor_id": "logistics.business-audit", "role": "business"},
            ]
        )
    ).model_dump(mode="json")
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": package,
        "observation": None,
        "principal_bindings": [
            {
                "principal_id": "central.business",
                "actor_id": "logistics.business",
                "role": "business",
            },
            {
                "principal_id": "central.business",
                "actor_id": "logistics.business-audit",
                "role": "business",
            },
            {
                "principal_id": "logistics.dispatcher",
                "actor_id": "logistics.dispatcher",
                "role": "dispatcher",
            },
            {
                "principal_id": "agent.provider",
                "actor_id": "fleet-alpha:1",
                "role": "aircraft_agent",
            },
        ],
    }


def test_provider_config_is_strict_domain_declaration() -> None:
    raw = _config().model_dump(mode="json")
    assert raw["task_package"] is not None
    assert "runtime_image" not in raw
    assert "endpoint" not in raw
    assert "protocol_version" not in raw
    assert "capabilities" not in raw
    with pytest.raises(ValidationError, match="unexpected"):
        LogisticsBusinessConfig.model_validate({**raw, "unexpected": True})
    with pytest.raises(ValidationError):
        LogisticsBusinessConfig.model_validate(
            {**raw, "runtime_image": {"image": IMAGE}}
        )
    with pytest.raises(ValidationError):
        LogisticsBusinessConfig.model_validate(
            {**raw, "endpoint": {"host": "logistics", "port": 18435}}
        )


def test_provider_config_binds_only_canonical_grants() -> None:
    document = _config().model_dump(mode="json")
    document["principal_bindings"] = [
        {
            "principal_id": "intruder.agent",
            "actor_id": "unknown.actor",
            "role": "aircraft_agent",
        }
    ]
    with pytest.raises(ValidationError, match="canonical OrderActorGrant"):
        LogisticsBusinessConfig.model_validate(document)


def test_provider_config_allows_central_principal_multiple_actors() -> None:
    """One native principal may be explicitly bound to several canonical actors."""

    from tests.providers.test_logistics_business_service import (
        _central_agent_config_document,
    )

    config = LogisticsBusinessConfig.model_validate(
        _central_agent_config_document()
    )
    pairs = sorted(
        (binding.principal_id, binding.actor_id, binding.role)
        for binding in config.principal_bindings
    )
    assert pairs == [
        ("central.fleet", "fleet-alpha:1", "aircraft_agent"),
        ("central.fleet", "fleet-alpha:2", "aircraft_agent"),
        ("logistics.business", "logistics.business", "business"),
        ("logistics.dispatcher", "logistics.dispatcher", "dispatcher"),
    ]


def test_provider_config_rejects_duplicate_principal_actor_pairs() -> None:
    """The (principal_id, actor_id) pair, not the principal alone, is unique."""

    document = _config().model_dump(mode="json")
    document["principal_bindings"] = [
        {
            "principal_id": "agent.provider",
            "actor_id": "fleet-alpha:1",
            "role": "aircraft_agent",
        },
        {
            "principal_id": "agent.provider",
            "actor_id": "fleet-alpha:1",
            "role": "aircraft_agent",
        },
    ]
    with pytest.raises(ValidationError, match="unique"):
        LogisticsBusinessConfig.model_validate(document)


def test_provider_config_rejects_role_drift_from_canonical_grant() -> None:
    """A binding may carry only the exact canonical grant role for its actor."""

    document = _config().model_dump(mode="json")
    document["principal_bindings"] = [
        {
            "principal_id": "agent.provider",
            "actor_id": "fleet-alpha:1",
            "role": "dispatcher",
        }
    ]
    with pytest.raises(ValidationError, match="expected"):
        LogisticsBusinessConfig.model_validate(document)


def test_provider_rejects_placeholder_credentials() -> None:
    scenario = _resolved_scenario()
    with pytest.raises(ValueError, match="run_id"):
        LogisticsBusinessProvider(
            config=_config(),
            manifest=_manifest("f" * 64),
            runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=18435),
            run_id="0" * 64,
            session_token=SESSION_TOKEN,
            scenario=scenario,
        )
    with pytest.raises(ValueError, match="session_token"):
        LogisticsBusinessProvider(
            config=_config(),
            manifest=_manifest("f" * 64),
            runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=18435),
            run_id=RUN_ID,
            session_token="0" * 64,
            scenario=scenario,
        )
    with pytest.raises(ValueError, match="scene"):
        LogisticsBusinessProvider(
            config=_config(),
            manifest=_manifest("f" * 64),
            runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=18435),
            run_id=RUN_ID,
            session_token=SESSION_TOKEN,
            scenario=type(scenario).model_validate(
                scenario.model_dump(mode="json")
            ).model_copy(update={"world_id": "world.other"}),
        )


def test_provider_rejects_manifest_provider_id_mismatch() -> None:
    drifted = _manifest("f" * 64)
    with pytest.raises(ValueError, match="manifest/provider configuration"):
        LogisticsBusinessProvider(
            config=_config(),
            manifest=drifted.model_copy(update={"provider_id": "other.provider"}),
            runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=18435),
            run_id=RUN_ID,
            session_token=SESSION_TOKEN,
            scenario=_resolved_scenario(),
        )


def test_provider_prepare_reset_stage_roundtrip(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            probe_frame = await JsonLineRpcTransport.connect(
                RuntimeEndpoint(host="127.0.0.1", port=port),
                component="test-harness",
            )
            probe = await probe_frame.request("probe", {})
            assert probe["status"] == "accepting"
            await probe_frame.close()

            await client.prepare()
            assert client.manifest.provider_id == PROVIDER_ID
            receipt = await client.reset(seed=SEED)
            assert receipt.reached == SimulationTime(tick=0, sim_time_ns=0)
            assert receipt.state_digest != "0" * 64
            assert len(receipt.events) == 1

            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            request = _stage_request(tick=1, sim_time_ns=1_000_000_000)
            result = await client.step_stage(request)
            assert result.step_receipt.reached == target
            assert result.step_receipt_digest == step_receipt_digest_value(
                result.step_receipt
            )
            assert result.contribution.samples == ()
            assert result.contribution.attribute_updates == ()
            assert client._last_time == target

            snapshot = await client.snapshot_digest()
            assert snapshot == (await client.snapshot_digest())
            assert snapshot != "0" * 64
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_command_lifecycle_and_history_replay(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))

            lifecycle: tuple[
                tuple[str, str, str, tuple[tuple[str, object], ...]]
            ] = (
                (
                    "command.offer",
                    "logistics.order.offer",
                    "logistics.dispatcher",
                    (
                        ("order_id", "order-1"),
                        ("assignee_id", "fleet-alpha:1"),
                        ("actor_id", "logistics.dispatcher"),
                        ("expected_version", 0),
                    ),
                ),
                (
                    "command.accept",
                    "logistics.order.accept",
                    "agent.provider",
                    (
                        ("order_id", "order-1"),
                        ("actor_id", "fleet-alpha:1"),
                        ("expected_version", 1),
                    ),
                ),
            )
            for command_id, tool_id, agent_id, arguments in lifecycle:
                result = await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id=command_id,
                        agent_id=agent_id,
                        tool_id=tool_id,
                        issued_at=target,
                        arguments=_args(*arguments),
                    )
                )
                assert tuple(item.phase for item in result.receipts) == (
                    "received",
                    "accepted",
                    "applied",
                    "completed",
                )
                assert len(result.events) == 1
                event = result.events[0]
                assert event.payload_schema_id == _SERVICE.TRANSITION_EVENT_SCHEMA
                fields = {item.name: item.value for item in event.payload}
                assert fields["command_id"] == command_id
                assert fields["order_id"] == "order-1"
            assert len(client._history) == 2
            order_state = next(
                order
                for order in service._machine.ledger.orders
                if order.order_id == "order-1"
            )
            assert order_state.status.value == "accepted"
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_physical_command_is_explicit_failure(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            result = await client.handle_command(
                CommandRequest(
                    run_id=RUN_ID,
                    command_id="command.pickup",
                    agent_id="agent.provider",
                    tool_id="logistics.order.pick_up",
                    issued_at=target,
                    arguments=_args(
                        ("order_id", "order-1"),
                        ("expected_version", 0),
                        ("evidence_ref", "evidence.photo.1"),
                    ),
                )
            )
            assert tuple(item.phase for item in result.receipts) == (
                "received",
                "failed",
            )
            assert "physical evidence interface pending" in (
                result.receipts[-1].detail or ""
            )
            assert result.events == ()
            assert client._history == ()
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_query_roundtrip_over_real_wire(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))

            query_result = await client.query_business(
                LogisticsBusinessQuery(
                    run_id=RUN_ID,
                    query_id="query.orders",
                    kind="orders",
                    issued_at=target,
                )
            )
            assert query_result.orders[0].order_id == "order-1"
            assert query_result.orders[0].status == "created"

            detail = await client.query_business(
                LogisticsBusinessQuery(
                    run_id=RUN_ID,
                    query_id="query.detail",
                    kind="order",
                    issued_at=target,
                    order_id="order-1",
                )
            )
            assert detail.orders[0].hub_handoff_facility_id == "hub-3"

            facility_rows = await client.query(
                QueryRequest(
                    run_id=RUN_ID,
                    query_id="query.facilities",
                    agent_id="logistics.business",
                    query_type=LOGISTICS_QUERY_FACILITIES,
                    issued_at=target,
                    arguments=(),
                )
            )
            payload = {item.name: item.value for item in facility_rows.payload}
            assert set(payload) == {"facility_count", "facilities_json"}
            assert payload["facility_count"] == 4
            assert facility_rows.payload_digest == hashlib.sha256(
                canonical_json_bytes(payload)
            ).hexdigest()
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_finalize_writes_declared_artifact(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            terminal = SimulationTime(tick=0, sim_time_ns=0)
            root = "d" * 64
            receipt = await client.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=RUN_ID,
                    terminal_event="run.completed",
                    terminal_time=terminal,
                    event_chain_root=root,
                )
            )
            assert receipt.event_chain_root == root
            assert receipt.artifacts[0].artifact_id == "artifact.logistics"
            artifact = tmp_path / "artifacts" / "logistics/state.json"
            assert artifact.is_file()
            assert artifact.stat().st_size > 0
            await client.shutdown()
            assert service.shutdown_requested.is_set()
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_central_principal_controls_two_aircraft_over_real_wire(
    tmp_path: Path,
) -> None:
    """The client is driven against a central-agent provider over the real wire.

    One native principal is explicitly bound to two declared aircraft actors and
    selects each one via the required ``actor_id`` argument; the authoritative
    history records the exact selected actor for each acceptance.
    """

    from tests.providers.test_logistics_business_service import (
        _central_agent_config_document,
        _make_service_with_config,
    )

    async def run() -> None:
        config_document = _central_agent_config_document()
        service = _make_service_with_config(tmp_path, config_document)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=LogisticsBusinessConfig.model_validate(config_document),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            request = _stage_request(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(request)

            lifecycle: tuple[
                tuple[str, str, str, tuple[tuple[str, object], ...]]
            ] = (
                (
                    "central.offer.1",
                    "logistics.order.offer",
                    "logistics.dispatcher",
                    (
                        ("order_id", "order-1"),
                        ("assignee_id", "fleet-alpha:1"),
                        ("actor_id", "logistics.dispatcher"),
                        ("expected_version", 0),
                    ),
                ),
                (
                    "central.accept.1",
                    "logistics.order.accept",
                    "central.fleet",
                    (
                        ("order_id", "order-1"),
                        ("actor_id", "fleet-alpha:1"),
                        ("expected_version", 1),
                    ),
                ),
                (
                    "central.offer.2",
                    "logistics.order.offer",
                    "logistics.dispatcher",
                    (
                        ("order_id", "order-2"),
                        ("assignee_id", "fleet-alpha:2"),
                        ("actor_id", "logistics.dispatcher"),
                        ("expected_version", 0),
                    ),
                ),
                (
                    "central.accept.2",
                    "logistics.order.accept",
                    "central.fleet",
                    (
                        ("order_id", "order-2"),
                        ("actor_id", "fleet-alpha:2"),
                        ("expected_version", 1),
                    ),
                ),
            )
            for command_id, tool_id, agent_id, arguments in lifecycle:
                result = await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id=command_id,
                        agent_id=agent_id,
                        tool_id=tool_id,
                        issued_at=target,
                        arguments=_args(*arguments),
                    )
                )
                assert tuple(item.phase for item in result.receipts) == (
                    "received",
                    "accepted",
                    "applied",
                    "completed",
                )

            assert len(client._history) == 4
            second_accept = client._history[-1].transition
            assert second_accept.order_id == "order-2"
            assert second_accept.actor_id == "fleet-alpha:2"
            orders = {
                order.order_id: order
                for order in service._machine.ledger.orders
            }
            assert orders["order-1"].status.value == "accepted"
            assert orders["order-2"].status.value == "accepted"
            assert orders["order-1"].assignee_id == "fleet-alpha:1"
            assert orders["order-2"].assignee_id == "fleet-alpha:2"
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_pinned_package_digest_is_stable() -> None:
    package = _config().task_package.model_dump(mode="json")
    first = pinned_package_digest(package)
    second = pinned_package_digest(dict(package))
    assert first == second
    assert first == hashlib.sha256(canonical_json_bytes(package)).hexdigest()
    assert first != "0" * 64
    drifted = dict(package)
    drifted["task_id"] = "another.task"
    assert first != pinned_package_digest(drifted)


def test_provider_rejects_stage_request_with_wrong_scenario(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            from aero_bench.world.resolved import ResolvedScenario
            from containers.selfcheck_scenario import (
                ScenarioProvider,
                build_resolved_scenario,
            )

            drifted_raw = build_resolved_scenario(
                seed=SEED + 1,
                providers=(
                    ScenarioProvider(PROVIDER_ID, ("mission",), CAPABILITIES, "business_environment"),
                    ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
                ),
                dynamic_provider_id="flight",
                verifier_id="logistics.verifier",
                world_id="world.city-demo",
            )
            drifted = ResolvedScenario.model_validate(drifted_raw)
            request = _stage_request(
                tick=1,
                sim_time_ns=1_000_000_000,
                scenario=drifted,
            )
            with pytest.raises(
                LogisticsBusinessProviderError, match="scenario digest differs"
            ):
                await client.step_stage(request)
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_online_order_create_is_authenticated_over_real_wire(
    tmp_path: Path,
) -> None:
    """Authenticated online-order creation crosses the real JSON-line wire.

    The business principal issues a ``logistics.order.create`` command; the
    completed response carries the accepted arrival record whose source identity
    is bound to the authenticated principal, the client appends the order to its
    live replay pool, and the authoritative service ledger exposes it.
    """

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))

            result = await client.handle_command(
                CommandRequest(
                    run_id=RUN_ID,
                    command_id="command.create-online",
                    agent_id="logistics.business",
                    tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                    issued_at=target,
                    arguments=_args(
                        ("order_id", "order-online"),
                        ("origin_facility_id", "facility-1"),
                        ("destination_facility_id", "facility-2"),
                        ("hub_handoff_facility_id", "hub-3"),
                        ("cargo_mass_kg", 1),
                        ("release_time_s", 1),
                        ("deadline_s", 900),
                        ("actor_id", "logistics.business"),
                    ),
                )
            )
            assert tuple(item.phase for item in result.receipts) == (
                "received",
                "accepted",
                "applied",
                "completed",
            )
            assert len(result.events) == 1
            event = result.events[0]
            assert event.payload_schema_id == ORDER_CREATED_EVENT_SCHEMA
            fields = {item.name: item.value for item in event.payload}
            assert fields["command_id"] == "command.create-online"
            assert fields["order_id"] == "order-online"
            assert fields["source_identity"] == "logistics.business"
            assert fields["actor_id"] == "logistics.business"
            assert fields["arrival_record_hash"] != "0" * 64
            # The live order pool now includes the online creation, and the
            # authoritative service ledger exposes it as an available order.
            assert client._orders_pool[-1].order_id == "order-online"
            ledger = {
                order.order_id: order
                for order in service._machine.ledger.orders
            }
            assert ledger["order-online"].status.value == "created"
            assert ledger["order-online"].release_time_s == 1.0
            # A lifecycle command targeting the created order proves the client
            # replays against the live order pool (no order ever absent).
            offered = await client.handle_command(
                CommandRequest(
                    run_id=RUN_ID,
                    command_id="command.offer-online",
                    agent_id="logistics.dispatcher",
                    tool_id="logistics.order.offer",
                    issued_at=target,
                    arguments=_args(
                        ("order_id", "order-online"),
                        ("assignee_id", "fleet-alpha:1"),
                        ("actor_id", "logistics.dispatcher"),
                        ("expected_version", 0),
                    ),
                )
            )
            assert tuple(item.phase for item in offered.receipts) == (
                "received",
                "accepted",
                "applied",
                "completed",
            )
            assert client._history[-1].transition.order_id == "order-online"
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_rejects_arrival_order_id_not_bound_to_command(
    tmp_path: Path,
) -> None:
    """A created order whose id differs from the request being validated fails.

    The genuine arrival record crosses the real loopback wire; the client
    validation surface sees a create command whose requested ``order_id`` no
    longer matches the accepted arrival. The provider must reject atomically and
    leave its live order pool and history untouched.
    """

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = _AdversarialCreateClient(
                tamper=lambda request: _swap_create_argument(
                    request, order_id="order-unrequested"
                ),
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            with pytest.raises(
                LogisticsBusinessProviderError, match="order_id"
            ):
                await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id="command.create-online",
                        agent_id="logistics.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(order_id="order-online")
                        ),
                    )
                )
            assert client.accepted_order_ids == []
            assert len(client.rejected) == 1
            assert "order_id" in client.rejected[0]
            assert client._orders_pool == client._config.task_package.orders
            assert client._history == ()
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_rejects_arrival_actor_not_bound_to_command(
    tmp_path: Path,
) -> None:
    """A created order introduced by a different actor than requested fails."""

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = _AdversarialCreateClient(
                tamper=lambda request: _swap_create_argument(
                    request, actor_id="logistics.dispatcher"
                ),
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            with pytest.raises(
                LogisticsBusinessProviderError, match="requested actor"
            ):
                await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id="command.create-online",
                        agent_id="logistics.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(order_id="order-online")
                        ),
                    )
                )
            assert client.accepted_order_ids == []
            assert client._orders_pool == client._config.task_package.orders
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


@pytest.mark.parametrize(
    "changed_field,swapped_value",
    [
        ("origin_facility_id", "facility-other"),
        ("destination_facility_id", "facility-other"),
        ("hub_handoff_facility_id", "hub-other"),
        ("cargo_mass_kg", 99),
        ("release_time_s", 99),
        ("deadline_s", 99),
    ],
)
def test_provider_rejects_arrival_order_field_not_bound_to_command(
    tmp_path: Path, changed_field: str, swapped_value: object
) -> None:
    """Every canonical OrderRequest field in the create command must bind to the
    accepted arrival record; swapping any one of them is rejected atomically."""

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = _AdversarialCreateClient(
                tamper=lambda request: _swap_create_argument(
                    request, **{changed_field: swapped_value}
                ),
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            with pytest.raises(
                LogisticsBusinessProviderError, match=changed_field
            ):
                await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id=f"command.create-online",
                        agent_id="logistics.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(order_id="order-online")
                        ),
                    )
                )
            assert client.accepted_order_ids == []
            assert client._orders_pool == client._config.task_package.orders
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_rejects_duplicate_create_argument(tmp_path: Path) -> None:
    """A duplicate create argument in the request surface is rejected atomically."""

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = _AdversarialCreateClient(
                tamper=_duplicate_create_argument_request,
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            with pytest.raises(
                LogisticsBusinessProviderError, match="not unique"
            ):
                await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id="command.create-online",
                        agent_id="logistics.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(order_id="order-online")
                        ),
                    )
                )
            assert client.accepted_order_ids == []
            assert client._orders_pool == client._config.task_package.orders
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_rejects_missing_create_argument(tmp_path: Path) -> None:
    """A create command missing one canonical field is rejected atomically."""

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = _AdversarialCreateClient(
                tamper=_drop_create_argument_request,
                config=_config(),
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            with pytest.raises(
                LogisticsBusinessProviderError, match="canonical create surface"
            ):
                await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id="command.create-online",
                        agent_id="logistics.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(order_id="order-online")
                        ),
                    )
                )
            assert client.accepted_order_ids == []
            assert client._orders_pool == client._config.task_package.orders
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_two_business_actors_same_principal_exact_actor_recorded(
    tmp_path: Path,
) -> None:
    """One principal bound to two business actors may create as either actor.

    Each online creation records the exact selected actor and appends the order
    to the live pool; the client never confuses the sibling actor bindings.
    """

    from tests.providers.test_logistics_business_service import (
        _make_service_with_config,
    )

    config_document = _two_business_central_config_document()
    config = LogisticsBusinessConfig.model_validate(config_document)

    async def run() -> None:
        service = _make_service_with_config(tmp_path, config_document)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = LogisticsBusinessProvider(
                config=config,
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))

            actor_plan = (
                (
                    "command.create-central-a",
                    "order-central-a",
                    "logistics.business",
                ),
                (
                    "command.create-central-b",
                    "order-central-b",
                    "logistics.business-audit",
                ),
            )
            for command_id, order_id, actor_id in actor_plan:
                result = await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id=command_id,
                        agent_id="central.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(
                                order_id=order_id, actor_id=actor_id
                            )
                        ),
                    )
                )
                assert tuple(item.phase for item in result.receipts) == (
                    "received",
                    "accepted",
                    "applied",
                    "completed",
                )
                fields = {item.name: item.value for item in result.events[0].payload}
                assert fields["actor_id"] == actor_id
            assert [order.order_id for order in client._orders_pool] == [
                "order-1",
                "order-central-a",
                "order-central-b",
            ]
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_provider_rejects_sibling_business_actor_of_same_principal(
    tmp_path: Path,
) -> None:
    """A create command naming one business actor can never be credited with an
    arrival introduced by its sibling business actor of the same principal."""

    from tests.providers.test_logistics_business_service import (
        _make_service_with_config,
    )

    config_document = _two_business_central_config_document()
    config = LogisticsBusinessConfig.model_validate(config_document)

    async def run() -> None:
        service = _make_service_with_config(tmp_path, config_document)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        try:
            client = _AdversarialCreateClient(
                tamper=lambda request: _swap_create_argument(
                    request, actor_id="logistics.business-audit"
                ),
                config=config,
                manifest=_manifest(service._workload_identity.config_digest),
                runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
                run_id=RUN_ID,
                session_token=SESSION_TOKEN,
                scenario=_resolved_scenario(),
            )
            await client.prepare()
            await client.reset(seed=SEED)
            target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
            await client.step_stage(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            with pytest.raises(
                LogisticsBusinessProviderError, match="requested actor"
            ):
                await client.handle_command(
                    CommandRequest(
                        run_id=RUN_ID,
                        command_id="command.create-sibling",
                        agent_id="central.business",
                        tool_id=LOGISTICS_ORDER_CREATE_TOOL,
                        issued_at=target,
                        arguments=_args(
                            *_online_create_args(
                                order_id="order-sibling",
                                actor_id="logistics.business",
                            )
                        ),
                    )
                )
            assert client.accepted_order_ids == []
            assert len(client.rejected) == 1
            assert "requested actor" in client.rejected[0]
            assert client._orders_pool == config.task_package.orders
            assert client._history == ()
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


__all__ = ["_config", "_manifest", "_run"]
