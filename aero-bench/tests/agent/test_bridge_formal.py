from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import yaml

from aero_bench.agent.bridge import SessionBridge, compile_tools
from aero_bench.agent.runtime import AgentContext, GatewayClient
from aero_bench.agent.session_contracts import ToolCallRequest, tool_catalog_digest
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.gateway import GatewayDispatchError, GatewayDispatcher
from aero_bench.gateway.contracts import DecisionSummaryRequest
from aero_bench.runtime import CommandRequest, EventLedger, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.model_gateway_evidence import (
    _validate_command_audit,
    _validate_decision_summary_audit,
)
from aero_bench.world.resolved import scenario_assets_for_workload
from tests.agent.support import descriptor
from tests.support import build_bundle, digest, promote_bundle_to_formal, resolve_bundle
from tests.test_gateway_dispatcher import RecordingToolEndpoint, principal


def test_formal_bridge_binds_each_action_to_its_model_summary(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    for name, properties in (
        ("goto-request", {"target": {"type": "string"}}),
        ("goto-response", {"accepted": {"type": "boolean"}}),
    ):
        path = bundle.root / f"schemas/{name}.json"
        path.write_bytes(
            canonical_json_bytes(
                {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                }
            )
        )
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    for grant in agent["tools"]:
        for field in ("request_schema", "response_schema"):
            grant[field]["sha256"] = digest(bundle.root / grant[field]["path"])
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False))
    promote_bundle_to_formal(bundle)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    assert run.execution_scope == "formal_benchmark"
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
    output = tmp_path / "output"
    output.mkdir()
    context = AgentContext(
        contract=contract,
        token="e" * 64,
        gateway_host="unused",
        gateway_port=17732,
        bundle_root=bundle.root,
        artifact_root=output,
    )
    catalog = compile_tools(contract, lambda ref: (bundle.root / ref.path).read_bytes())
    session = descriptor().model_copy(
        update={
            "run_id": run.run_id,
            "agent_id": agent.agent_id,
            "task_id": run.task.task_id,
            "tools": catalog,
            "tools_digest": tool_catalog_digest(catalog),
        }
    )
    bridge = SessionBridge(context, session)
    endpoint = RecordingToolEndpoint("flight")
    now = [SimulationTime(tick=0, sim_time_ns=0)]
    ledger = EventLedger(run_id=run.run_id)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: now[0],
        ledger=ledger,
    )
    authenticated = principal(run)

    class DispatcherGateway(GatewayClient):
        def _request(self, operation, payload):
            if operation == "decision.summary":
                result = asyncio.run(
                    dispatcher.record_decision_summary(
                        authenticated,
                        DecisionSummaryRequest.model_validate(payload["summary"]),
                    )
                )
            elif operation == "command.invoke":
                result = asyncio.run(
                    dispatcher.invoke(
                        authenticated, CommandRequest.model_validate(payload["command"])
                    )
                )
            else:
                raise AssertionError(operation)
            response = result.model_dump(mode="json")
            self._audit(
                operation,
                {
                    "operation": operation,
                    **{key: value for key, value in payload.items() if key != "token"},
                },
                response,
            )
            return response

    bridge.gateway = DispatcherGateway(context, audit=bridge._audit_gateway)
    result = bridge.execute_tool(
        ToolCallRequest(
            call_id="call_formal",
            name="action_flight_goto",
            arguments={
                "target": "asset-1",
                "decision_summary": "Visit the declared inspection target.",
            },
        ),
        timeout_s=30,
    )
    assert result.success
    assert [call.operation for call in result.audit.gateway_calls] == [
        "decision.summary",
        "command.invoke",
    ]
    summary = json.loads(result.audit.gateway_calls[0].request_json)["summary"]
    request = endpoint.requests[0]
    assert summary["command_id"] == request.command_id == result.audit.operation_id
    assert summary["decision_summary"] == "Visit the declared inspection target."
    assert {item.name: item.value for item in request.arguments} == {
        "target": "asset-1"
    }
    assert (
        ledger.records[0].event.interaction.interaction_type
        == "agent.decision_summary.v1"
    )
    ledger.verify()
    consumed: set[str] = set()
    _validate_decision_summary_audit(
        result.audit.gateway_calls[0],
        outer_result=result,
        records=ledger.records,
        run=run,
        manifest=SimpleNamespace(agent_id=agent.agent_id),
        consumed=consumed,
    )
    _validate_command_audit(
        result.audit.gateway_calls[1],
        outer_result=result,
        records=ledger.records,
        run=run,
        manifest=SimpleNamespace(agent_id=agent.agent_id),
        consumed=consumed,
    )
    assert ledger.records[0].event.event_id in consumed

    with pytest.raises(ValueError, match="declared schema"):
        bridge.execute_tool(
            ToolCallRequest(
                call_id="call_no_summary",
                name="action_flight_goto",
                arguments={"target": "asset-1"},
            ),
            timeout_s=30,
        )
    with pytest.raises(GatewayDispatchError, match="explicit decision summary"):
        bridge.gateway.command(
            command_id="command.no-summary",
            tool_id="flight.goto",
            at=now[0],
            arguments={"target": "asset-1"},
        )
    bridge.gateway.decision_summary(
        summary_id="summary.stale",
        command_id="command.stale",
        at=now[0],
        summary="A prior tick decision.",
    )
    now[0] = SimulationTime(tick=1, sim_time_ns=run.environment.clock.step_ns)
    with pytest.raises(GatewayDispatchError, match="not authoritative"):
        bridge.gateway.command(
            command_id="command.stale",
            tool_id="flight.goto",
            at=now[0],
            arguments={"target": "asset-1"},
        )
    assert len(endpoint.requests) == 1
