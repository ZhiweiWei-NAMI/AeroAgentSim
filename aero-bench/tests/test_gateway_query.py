from __future__ import annotations

from tests.support import fixture_provider_registry

import asyncio
import hashlib
import json

import pytest
import yaml

from pydantic import ValidationError

from aero_bench.agent.runtime import AgentContext, GatewayClient
from aero_bench.config.models import AgentSpec, NamedValue
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.executor.planning import build_execution_plan
from aero_bench.gateway import (
    AgentPrincipal,
    GatewayDispatchError,
    GatewayDispatcher,
    ProviderQueryResult,
    QueryRequest,
)
from aero_bench.runtime import EventLedger, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.resolved import scenario_assets_for_workload
from tests.support import build_bundle, digest, resolve_bundle


QUERY_TYPE = "business.work-orders"


def _query_run(tmp_path):
    bundle = build_bundle(tmp_path)
    request_schema_path = bundle.root / "schemas/query-request.json"
    response_schema_path = bundle.root / "schemas/query-response.json"
    request_schema_path.write_bytes(
        canonical_json_bytes(
            {"type": "object", "additionalProperties": False, "properties": {}}
        )
        + b"\n"
    )
    response_schema_path.write_bytes(
        canonical_json_bytes(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"available": {"type": "boolean"}},
                "required": ["available"],
            }
        )
        + b"\n"
    )
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["queries"] = [
        {
            "query_type": QUERY_TYPE,
            "provider_id": "business",
            "request_schema": {
                "path": "schemas/query-request.json",
                "sha256": digest(request_schema_path),
            },
            "response_schema": {
                "path": "schemas/query-response.json",
                "sha256": digest(response_schema_path),
            },
            "timeout_ms": 30_000,
        }
    ]
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    return bundle, run


def _principal(run) -> AgentPrincipal:
    return AgentPrincipal(
        run_id=run.run_id,
        agent_id="reference.agent",
        authentication_id="auth.test",
    )


def _request(run, *, query_id: str = "query.one", query_type: str = QUERY_TYPE):
    return QueryRequest(
        run_id=run.run_id,
        query_id=query_id,
        agent_id="reference.agent",
        query_type=query_type,
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
    )


class _QueryEndpoint:
    def __init__(self, *, wrong_digest: bool = False) -> None:
        self.requests: list[QueryRequest] = []
        self.wrong_digest = wrong_digest

    async def query(self, request: QueryRequest) -> ProviderQueryResult:
        self.requests.append(request)
        payload = (NamedValue(name="available", value=True),)
        return ProviderQueryResult(
            run_id=request.run_id,
            query_id=request.query_id,
            query_type=request.query_type,
            observed_at=request.issued_at,
            payload=payload,
            payload_digest=(
                "f" * 64
                if self.wrong_digest
                else hashlib.sha256(
                    canonical_json_bytes({"available": True})
                ).hexdigest()
            ),
        )


def _dispatcher(bundle, run, endpoint, *, current=None):
    current = current or SimulationTime(tick=0, sim_time_ns=0)
    ledger = EventLedger(run_id=run.run_id)
    return GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={},
        query_endpoints={("business", QUERY_TYPE): endpoint},
        authoritative_time=lambda: current,
        ledger=ledger,
    )


def test_query_is_grant_bound_validated_and_audited(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    endpoint = _QueryEndpoint()
    dispatcher = _dispatcher(bundle, run, endpoint)

    result = asyncio.run(dispatcher.query(_principal(run), _request(run)))

    grant = run.agents[0].queries[0]
    assert result.provider_id == "business"
    assert result.payload_schema == grant.response_schema
    assert endpoint.requests == [_request(run)]
    assert [record.event.event_type for record in dispatcher._ledger.records] == [
        "query.requested",
        "query.validated",
    ]
    assert [
        record.event.interaction.interaction_type
        for record in dispatcher._ledger.records
    ] == ["agent.query_call.v1", "agent.query_result.v1"]
    assert all(
        record.event.query_id == "query.one"
        and record.event.interaction.query_id == "query.one"
        for record in dispatcher._ledger.records
    )
    payload = {
        item.name: item.value for item in dispatcher._ledger.records[-1].event.payload
    }
    assert payload["query_id"] == "query.one"
    assert payload["query_type"] == QUERY_TYPE
    assert json.loads(payload["result_json"])["payload_digest"] == result.payload_digest
    dispatcher._ledger.verify()


def test_agent_v2_requires_explicit_unique_query_grants(tmp_path) -> None:
    _, run = _query_run(tmp_path)
    document = run.agents[0].model_dump(mode="json")
    document.pop("queries")
    with pytest.raises(ValidationError, match="queries"):
        AgentSpec.model_validate(document)
    duplicate = run.agents[0].model_dump(mode="json")
    duplicate["queries"].append(dict(duplicate["queries"][0]))
    with pytest.raises(ValidationError, match="query_type values must be unique"):
        AgentSpec.model_validate(duplicate)


def test_query_schemas_are_materialized_for_agent_and_harness(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=bundle.root,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )
    agent = next(item for item in plan.runtime_workloads if item.role == "agent")
    harness = next(item for item in plan.runtime_workloads if item.role == "harness")
    expected = {"schemas/query-request.json", "schemas/query-response.json"}
    assert expected <= {item.source.path for item in agent.bundle_inputs}
    assert expected <= {item.source.path for item in harness.bundle_inputs}


def test_query_id_is_consumed_exactly_once(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    endpoint = _QueryEndpoint()
    dispatcher = _dispatcher(bundle, run, endpoint)
    request = _request(run)

    asyncio.run(dispatcher.query(_principal(run), request))
    with pytest.raises(GatewayDispatchError, match="query_id was reused"):
        asyncio.run(dispatcher.query(_principal(run), request))

    assert endpoint.requests == [request]


def test_query_id_reservation_is_rebuilt_from_authoritative_ledger(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    first_endpoint = _QueryEndpoint()
    first = _dispatcher(bundle, run, first_endpoint)
    request = _request(run)
    asyncio.run(first.query(_principal(run), request))

    second_endpoint = _QueryEndpoint()
    rebuilt = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={},
        query_endpoints={("business", QUERY_TYPE): second_endpoint},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=first._ledger,
    )
    with pytest.raises(GatewayDispatchError, match="query_id was reused"):
        asyncio.run(rebuilt.query(_principal(run), request))

    assert second_endpoint.requests == []


def test_ungranted_or_stale_query_never_reaches_provider(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    endpoint = _QueryEndpoint()
    dispatcher = _dispatcher(bundle, run, endpoint)

    with pytest.raises(GatewayDispatchError, match="not granted"):
        asyncio.run(
            dispatcher.query(
                _principal(run),
                _request(run, query_type="business.private-history"),
            )
        )
    stale = _request(run, query_id="query.stale").model_copy(
        update={"issued_at": SimulationTime(tick=1, sim_time_ns=1)}
    )
    with pytest.raises(GatewayDispatchError, match="authoritative simulation time"):
        asyncio.run(dispatcher.query(_principal(run), stale))
    assert endpoint.requests == []


def test_query_identity_is_bound_to_authenticated_principal(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    endpoint = _QueryEndpoint()
    dispatcher = _dispatcher(bundle, run, endpoint)
    forged = _request(run).model_copy(update={"agent_id": "agent.other"})

    with pytest.raises(GatewayDispatchError, match="agent principal"):
        asyncio.run(dispatcher.query(_principal(run), forged))

    assert endpoint.requests == []


def test_invalid_provider_query_payload_fails_closed_and_is_audited(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    endpoint = _QueryEndpoint(wrong_digest=True)
    dispatcher = _dispatcher(bundle, run, endpoint)

    with pytest.raises(GatewayDispatchError, match="payload digest"):
        asyncio.run(dispatcher.query(_principal(run), _request(run)))

    assert [record.event.event_type for record in dispatcher._ledger.records] == [
        "query.requested",
        "gateway.failure",
    ]
    failure = {
        item.name: item.value for item in dispatcher._ledger.records[-1].event.payload
    }
    assert failure["failure_class"] == "query_validation_failed"
    assert dispatcher._ledger.records[-1].event.interaction.interaction_type == (
        "agent.query_result.v1"
    )
    dispatcher._ledger.verify()


def test_agent_sdk_emits_query_invoke_and_validates_result(tmp_path) -> None:
    bundle, run = _query_run(tmp_path)
    agent = run.agents[0]
    contract = AgentWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="agent",
        run_id=run.run_id,
        seed=run.seed,
        clock=run.environment.clock,
        workload_id=agent.agent_id,
        task_id=run.task.task_id,
        instruction=run.task.instruction,
        gateway=run.environment.gateway,
        agent=agent,
        scenario_digest=run.scenario.scenario_digest,
        scenario=run.scenario,
        scenario_assets=scenario_assets_for_workload(
            run.scenario, role="agent", workload_id=agent.agent_id
        ),
    )
    context = AgentContext(
        contract=contract,
        token="a" * 64,
        gateway_host="gateway",
        gateway_port=run.environment.gateway.port,
        bundle_root=bundle.root,
        artifact_root=tmp_path,
    )

    class RecordingClient(GatewayClient):
        def _request(self, operation, payload):
            self.sent = (operation, payload)
            query = payload["query"]
            values = {"available": True}
            return {
                "run_id": run.run_id,
                "query_id": query["query_id"],
                "agent_id": agent.agent_id,
                "query_type": QUERY_TYPE,
                "provider_id": "business",
                "observed_at": query["issued_at"],
                "payload_schema": agent.queries[0].response_schema.model_dump(
                    mode="json"
                ),
                "payload": [{"name": "available", "value": True}],
                "payload_digest": hashlib.sha256(
                    canonical_json_bytes(values)
                ).hexdigest(),
            }

    client = RecordingClient(context)
    result = client.query(
        query_id="query.sdk",
        query_type=QUERY_TYPE,
        at=SimulationTime(tick=0, sim_time_ns=0),
        arguments={},
    )

    assert client.sent[0] == "query.invoke"
    assert client.sent[1]["query"]["query_type"] == QUERY_TYPE
    assert result.query_id == "query.sdk"
