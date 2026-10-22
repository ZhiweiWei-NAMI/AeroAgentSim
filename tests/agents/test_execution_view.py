"""Read-side execution views of real, entirely offline decision/graph journals.

The decision scenario and completion builders are reused from test_decision;
graph config/run/completion fixtures are loaded lazily from test_langgraph.
OpenAIProvider.complete is always blocked or monkeypatched: no live provider I/O.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import FrozenInstanceError
from importlib import import_module
from types import ModuleType
from typing import Any

import pytest
from test_decision import call as decision_call
from test_decision import completion as decision_completion
from test_decision import document as decision_document  # noqa: F401 -- pytest fixture

from aeroagentsim.agents import decision as decision_module
from aeroagentsim.agents.decision import RECORD_SCHEMA as DECISION_SCHEMA
from aeroagentsim.agents.execution import (
    AgentExecution,
    from_agent_record,
    from_langgraph_records,
)
from aeroagentsim.agents.langgraph_client import ScriptedProvider
from aeroagentsim.agents.provider import OpenAIProvider, ProviderError
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

GRAPH_SCHEMA = "aas.langgraph.record"
ADAPTER_CASES = [
    pytest.param(from_agent_record, "decision_messages", id="decision"),
    pytest.param(from_langgraph_records, "graph_messages", id="langgraph"),
]
NEW_METADATA = {
    "execution_mode",
    "provider_mode",
    "provider_profile",
    "transport_transformations",
    "attempt",
    "failure_code",
    "validation",
}


def projected_messages(kernel: Any) -> list[dict[str, Any]]:
    return [
        message
        for record in kernel.iter_records()
        if record.get("type") != "header"
        for message in project(record)["messages"]
    ]


def original_records(kernel: Any, schema: str) -> list[dict[str, Any]]:
    # project() includes graph console aliases; only exact original schemas count.
    return [
        message
        for message in projected_messages(kernel)
        if message["schemaId"] == schema
    ]


def data(message: dict[str, Any]) -> dict[str, Any]:
    return json.loads(message["payload"]["data_json"])


def phase_records(
    messages: list[dict[str, Any]], phase: str
) -> list[dict[str, Any]]:
    return [message for message in messages if message["payload"]["phase"] == phase]


def legacy_records(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove additive metadata, retaining original identity/model/rejections."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip(item)
                for key, item in value.items()
                if key not in NEW_METADATA
            }
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    result = deepcopy(messages)
    for message in result:
        message["payload"]["data_json"] = json.dumps(strip(data(message)))
    return result


def run_decision(
    document: dict[str, Any], *, until_ns: int = 2
) -> list[dict[str, Any]]:
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        simulation.run_until(until_ns)
        return original_records(simulation.kernel, DECISION_SCHEMA)
    finally:
        simulation.close()


def assignment(name: str, identity: str = "assign-1") -> dict[str, Any]:
    return decision_call(
        name,
        {
            "entity": "order-1",
            "resource": "uav-1",
            "target": [30.0, 0.0, 10.0],
            "decision_summary": "Submit a real fixture command",
        },
        identity,
    )


@pytest.fixture(autouse=True)
def prohibit_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAS_LLM_BASE_URL", "http://provider.invalid/v1")
    monkeypatch.delenv("AAS_LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *args, **kwargs: pytest.fail("unexpected live provider call"),
    )


@pytest.fixture
def decision_messages(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, Any]]:
    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = decision_completion(
            [
                assignment(tools[0]["function"]["name"]),
                decision_call("noop", {"decision_summary": "Done"}, "noop-1"),
            ]
        )
        # This is not mode evidence, even though it resembles metadata/JSON mode.
        response["message"]["content"] = json.dumps(
            {"execution_mode": "structured_json", "model": "scripted"}
        )
        return response

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    return run_decision(decision_document, until_ns=10_000_000_000)


@pytest.fixture
def graph_module() -> ModuleType:
    # Only graph fixture consumers skip when the optional dependency is absent.
    pytest.importorskip("langgraph.graph")
    return import_module("test_langgraph")


@pytest.fixture
def graph_config(graph_module: ModuleType) -> dict[str, Any]:
    # Reuse the existing fixture's source, without calling a decorated fixture.
    return graph_module.config.__wrapped__()


@pytest.fixture
def graph_run(graph_module: ModuleType) -> Iterator[Callable[..., Any]]:
    kernels: list[Any] = []

    def run(config: dict[str, Any], provider: Any = None) -> tuple[Any, Any]:
        kernel, engine = graph_module.run(config, provider)
        kernels.append(kernel)
        return kernel, engine

    try:
        yield run
    finally:
        for kernel in kernels:
            kernel.close()


@pytest.fixture
def graph_messages(
    graph_config: dict[str, Any], graph_run: Callable[..., Any]
) -> list[dict[str, Any]]:
    kernel, engine = graph_run(graph_config)
    assert isinstance(engine.provider, ScriptedProvider)
    return original_records(kernel, GRAPH_SCHEMA)


def function_tools_graph(client: Any, options: dict[str, Any]) -> Any:
    """Record a tools request whose reply happens to contain ordinary JSON."""
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(dict)

    async def exchange(state: dict[str, Any]) -> dict[str, Any]:
        await client.complete(
            "tools",
            [{"role": "user", "content": "Return an ordinary JSON response"}],
            options["tools"],
        )
        return {"outputs": []}

    graph.add_node("exchange", exchange)
    graph.add_edge(START, "exchange")
    graph.add_edge("exchange", END)
    return graph


def late_invalid_output_graph(client: Any, options: dict[str, Any]) -> Any:
    """A valid earlier event must not escape a later invalid command batch."""
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(dict)

    async def finish(state: dict[str, Any]) -> dict[str, Any]:
        def validate(value: dict[str, Any]) -> None:
            if set(value) != {"value"} or type(value["value"]) is not int:
                raise ValueError("integer value required")

        values = [
            await client.ask_json(key, "Return JSON integer value", {}, validate)
            for key in ("alpha", "bravo")
        ]
        value = sum(item["value"] for item in values)
        return {
            "outputs": [
                {
                    "kind": "event",
                    "schema": "example.out",
                    "topic": "out",
                    "payload": {"value": value},
                },
                {
                    "kind": "command",
                    "schema": "example.save",
                    "target": "sink",
                    "payload": {"value": True},
                },
                {
                    "kind": "fact",
                    "entity": "record",
                    "field": "example.value",
                    "value": value,
                },
            ]
        }

    graph.add_node("finish", finish)
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    return graph


def test_decision_success_counts_exchanges_not_reply_tool_calls(
    decision_messages: list[dict[str, Any]],
) -> None:
    view = from_agent_record(decision_messages)
    observation = data(phase_records(decision_messages, "observation")[0])
    prompt = phase_records(decision_messages, "prompt")[0]
    response = phase_records(decision_messages, "response")[0]
    validations = phase_records(decision_messages, "validation")
    commands = phase_records(decision_messages, "command")
    receipts = phase_records(decision_messages, "receipt")

    assert view.version == 1 and view.engine == "decision"
    assert view.engine_id == "zz_decision" and view.decision_id == "zz_decision/1"
    assert view.invocation_id is None and view.graph_pin is None
    assert view.observation == observation
    assert view.observation_cut == observation["known_at"]
    assert view.mode == "function_tools"  # Not the JSON-looking response content.
    assert view.provider_mode == "live" and view.provider_profile == "default"
    assert view.model == "test-model" and view.transport_transformations == []
    assert view.outcome == "succeeded"
    assert view.outcome_code is None and view.failure is None
    assert view.message_ids == [message["id"] for message in decision_messages]

    assert len(view.calls) == 1
    call = view.calls[0]
    assert len(call.response["message"]["tool_calls"]) == 2
    assert call.identity is None and call.round == 0 and call.attempt is None
    assert call.request == data(prompt) and call.response == data(response)
    assert call.usage == data(response)["usage"] and call.latency_s is None
    assert call.valid is True and call.failure_code is None and call.error is None
    assert [validation.valid for validation in call.validations] == [True, True]
    assert [validation.call_id for validation in call.validations] == [
        "assign-1",
        "noop-1",
    ]
    assert [validation.message_id for validation in call.validations] == [
        message["id"] for message in validations
    ]
    assert call.message_ids == [prompt["id"], response["id"]] + [
        message["id"] for message in validations
    ]
    assert [
        (output.kind, output.data, output.message_id) for output in view.outputs
    ] == [("command", data(message), message["id"]) for message in commands]
    assert [(receipt.data, receipt.message_id) for receipt in view.receipts] == [
        (data(message), message["id"]) for message in receipts
    ]
    assert [receipt.data["status"] for receipt in view.receipts] == [
        "submitted",
        "accepted",
        "executing",
        "succeeded",
    ]
    command_ids = {receipt.data["command_id"] for receipt in view.receipts}
    assert len(command_ids) == 1
    assert view.outputs[0].message_id not in command_ids


@pytest.mark.parametrize(
    ("budget", "code"),
    [
        ({"max_retries": 0}, "RETRY_BUDGET"),
        ({"max_rounds": 1}, "ROUND_BUDGET"),
        ({"max_tokens": 10}, "TOKEN_BUDGET"),
    ],
)
def test_decision_later_failure_retains_partial_output_and_actual_receipts(
    decision_document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    budget: dict[str, int],
    code: str,
) -> None:
    decision_document["engines"]["zz_decision"]["config"]["budget"].update(budget)
    exchanges = []

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        exchanges.append(deepcopy(messages))
        return decision_completion(
            [
                assignment(tools[0]["function"]["name"]),
                decision_call("not_granted", {"decision_summary": "Reject"}, "bad-2"),
            ]
        )

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    messages = run_decision(decision_document, until_ns=10_000_000_000)
    view = from_agent_record(messages)
    assert len(exchanges) == 1 and len(view.calls) == 1
    assert view.outcome == "partial" and view.outcome_code == code
    assert view.failure == data(phase_records(messages, "failure")[0])
    assert view.failure["code"] == code
    assert len(view.outputs) == 1 and view.outputs[0].kind == "command"
    assert view.outputs[0].data["call_id"] == "assign-1"
    assert [validation.valid for validation in view.calls[0].validations] == [
        True,
        False,
    ]
    assert view.calls[0].valid is False
    assert view.calls[0].failure_code == "TOOL_REJECTED"
    # A later decision budget/preflight failure is not a provider-exchange error.
    assert view.calls[0].error is None
    assert [receipt.data for receipt in view.receipts] == [
        data(message) for message in phase_records(messages, "receipt")
    ]
    assert [receipt.data["status"] for receipt in view.receipts] == [
        "submitted",
        "accepted",
        "executing",
        "succeeded",
    ]
    assert all(receipt.data["call_id"] == "assign-1" for receipt in view.receipts)


def test_decision_zero_retries_fails_without_inventing_outputs(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    decision_document["engines"]["zz_decision"]["config"]["budget"]["max_retries"] = 0
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *args, **kwargs: decision_completion(
            [decision_call("not_granted", {"decision_summary": "Reject"})]
        ),
    )
    view = from_agent_record(run_decision(decision_document))
    assert view.outcome == "failed" and view.outcome_code == "RETRY_BUDGET"
    assert view.outputs == [] and view.receipts == []
    assert len(view.calls) == 1 and view.calls[0].valid is False
    assert view.calls[0].failure_code == "TOOL_REJECTED"
    assert view.calls[0].error is None


def test_decision_rejection_retry_keeps_separate_rounds(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = iter(
        [
            decision_completion(
                [decision_call("not_granted", {"decision_summary": "Reject"}, "bad")]
            ),
            decision_completion(
                [decision_call("noop", {"decision_summary": "Done"}, "good")]
            ),
        ]
    )
    monkeypatch.setattr(
        OpenAIProvider, "complete", lambda *args, **kwargs: next(replies)
    )
    messages = run_decision(decision_document)
    view = from_agent_record(messages)
    assert view.outcome == "succeeded" and view.outcome_code is None
    assert [call.round for call in view.calls] == [0, 1]
    assert [call.attempt for call in view.calls] == [None, None]
    assert [call.valid for call in view.calls] == [False, True]
    assert [call.failure_code for call in view.calls] == ["TOOL_REJECTED", None]
    assert [call.validations[0].call_id for call in view.calls] == ["bad", "good"]
    assert json.loads(view.calls[1].request["messages"][-1]["content"])["status"] == (
        "rejected"
    )
    assert view.outputs == [] and view.receipts == []


@pytest.mark.parametrize("code", ["HTTP_STATUS", "TRANSPORT"])
def test_decision_provider_fault_is_not_given_graph_retry_policy(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    exchanges = []

    def complete(*args: Any, **kwargs: Any) -> dict[str, Any]:
        exchanges.append(1)
        raise ProviderError(code, "offline fixture failure")

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    messages = run_decision(decision_document)
    view = from_agent_record(messages)
    assert len(exchanges) == 1 and len(view.calls) == 1
    assert view.outcome == "failed" and view.outcome_code == code
    assert view.outputs == [] and view.receipts == []
    assert view.calls[0].response is None and view.calls[0].usage is None
    assert view.calls[0].valid is None and view.calls[0].validations == []
    assert view.calls[0].failure_code == code
    assert view.calls[0].error == data(phase_records(messages, "failure")[0])


def test_opencode_profile_model_and_transport_policy_are_recorded_without_network(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_PROVIDER_CONFIG", raising=False)
    monkeypatch.setenv(
        "AEROAGENTSIM_PROVIDER_PROFILES",
        json.dumps(
            {
                "execution-view-offline": {
                    "base_url": "https://opencode.ai/zen/v1",
                    "model": "offline-deepseek-model",
                    "api_key_env": "EXECUTION_VIEW_NO_API_KEY",
                }
            }
        ),
    )
    monkeypatch.delenv("EXECUTION_VIEW_NO_API_KEY", raising=False)
    decision_document["engines"]["zz_decision"]["config"]["provider"] = {
        "profile": "execution-view-offline"
    }
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *args, **kwargs: decision_completion(
            [decision_call("noop", {"decision_summary": "Offline"})]
        ),
    )
    messages = run_decision(decision_document)
    view = from_agent_record(messages)
    assert view.mode == "function_tools" and view.provider_mode == "live"
    assert view.provider_profile == "execution-view-offline"
    assert view.model == "offline-deepseek-model"
    assert view.transport_transformations == ["opencode.ai:strip-function.strict:v1"]
    # The recorded semantic tools are not rewritten to resemble the wire payload.
    assert all(
        tool["function"]["strict"] is True for tool in view.calls[0].request["tools"]
    )


@pytest.mark.parametrize("model", ["ordinary-model", "lowalt-scripted-allocation/v1"])
def test_injected_decision_stub_uses_explicit_mode_or_historical_recorded_model(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    class OfflineProvider:
        mode = "stub"

        def __init__(self) -> None:
            self.model = model

        def complete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return decision_completion(
                [decision_call("noop", {"decision_summary": "Offline"})]
            )

    provider = OfflineProvider()
    monkeypatch.setattr(
        decision_module,
        "build",
        lambda build: decision_module.Decision(build, provider),
    )
    messages = run_decision(decision_document)
    view = from_agent_record(messages)
    assert view.mode == "scripted" and view.provider_mode == "stub"
    assert view.model == model and view.provider_profile is None
    assert view.transport_transformations is None  # Custom provider has no policy attr.
    old = from_agent_record(legacy_records(messages))
    assert old.provider_mode is None and old.transport_transformations is None
    assert old.mode == (
        "scripted" if model == "lowalt-scripted-allocation/v1" else "function_tools"
    )


def test_decision_legacy_records_retain_outputs_receipts_and_validation(
    decision_messages: list[dict[str, Any]],
) -> None:
    current = from_agent_record(decision_messages)
    old = from_agent_record(legacy_records(decision_messages))
    assert old.engine_id == current.engine_id and old.decision_id == current.decision_id
    assert old.message_ids == current.message_ids and old.mode == "function_tools"
    assert old.model == "test-model"
    assert old.provider_mode is None and old.provider_profile is None
    assert old.transport_transformations is None
    assert old.outputs == current.outputs and old.receipts == current.receipts
    assert old.outcome == current.outcome and old.calls[0].valid is True
    assert old.calls[0].attempt is None
    assert old.calls[0].validations == current.calls[0].validations


def test_decision_prefix_does_not_claim_unknown_validation_or_outcome(
    decision_messages: list[dict[str, Any]],
) -> None:
    observation = phase_records(decision_messages, "observation")[0]
    initial = from_agent_record(observation)
    assert initial.mode is None and initial.model is None
    assert initial.provider_mode is None and initial.provider_profile is None
    assert initial.transport_transformations is None
    assert initial.outcome is None
    assert initial.outcome_code is None and initial.failure is None
    assert initial.calls == [] and initial.outputs == [] and initial.receipts == []

    for phase in ("prompt", "response", "validation"):
        last = phase_records(decision_messages, phase)[0]
        prefix = decision_messages[: decision_messages.index(last) + 1]
        view = from_agent_record(prefix)
        assert view.outcome is None
        assert view.outcome_code is None and view.failure is None
        assert len(view.calls) == 1
        assert view.calls[0].attempt is None and view.calls[0].valid is None
        assert view.calls[0].failure_code is None and view.calls[0].error is None
        if phase == "validation":
            # One of two tool calls was checked; the whole exchange is not yet valid.
            assert [item.valid for item in view.calls[0].validations] == [True]
        else:
            assert view.calls[0].validations == []


def test_unknown_decision_metadata_and_validation_are_not_coerced(
    decision_messages: list[dict[str, Any]],
) -> None:
    prompt = deepcopy(phase_records(decision_messages, "prompt")[0])
    body = data(prompt)
    body.update(
        round=True,
        attempt="0",
        execution_mode="unknown",
        provider_mode="unknown",
        provider_profile=3,
        model=False,
        transport_transformations=["known", 3],
    )
    prompt["payload"]["data_json"] = json.dumps(body)
    view = from_agent_record(prompt)
    assert view.mode is None and view.model is None
    assert view.provider_mode is None and view.provider_profile is None
    assert view.transport_transformations is None
    assert view.calls[0].round is None and view.calls[0].attempt is None
    assert view.calls[0].valid is None and view.outcome is None

    messages = deepcopy(decision_messages)
    for message in phase_records(messages, "validation"):
        body = data(message)
        body["valid"] = "true"
        message["payload"]["data_json"] = json.dumps(body)
    view = from_agent_record(messages)
    assert view.calls[0].valid is None
    assert all(validation.valid is None for validation in view.calls[0].validations)


def test_recorded_mode_requires_supported_provider_or_model_evidence(
    decision_messages: list[dict[str, Any]],
) -> None:
    cases = [
        ("live", "test-model", "scripted", None),
        ("live", "scripted", "function_tools", "function_tools"),
        (None, "scripted", None, "scripted"),
    ]
    for provider_mode, model, execution_mode, expected in cases:
        # A prompt alone is sufficient: there is no reply content to interpret.
        prompt = deepcopy(phase_records(decision_messages, "prompt")[0])
        body = data(prompt)
        body.update(provider_mode=provider_mode, model=model)
        if execution_mode is None:
            body.pop("execution_mode", None)
        else:
            body["execution_mode"] = execution_mode
        prompt["payload"]["data_json"] = json.dumps(body)
        view = from_agent_record(prompt)
        assert view.mode == expected and view.provider_mode == provider_mode
        assert view.model == model


def test_graph_success_retains_original_calls_pin_and_output_evidence_ids(
    graph_config: dict[str, Any], graph_run: Callable[..., Any]
) -> None:
    kernel, engine = graph_run(graph_config)
    assert isinstance(engine.provider, ScriptedProvider) and engine.provider.calls == 2
    messages = original_records(kernel, GRAPH_SCHEMA)
    view = from_langgraph_records(messages)
    initial = data(phase_records(messages, "observation")[0])
    model_calls = phase_records(messages, "model_call")
    outputs = phase_records(messages, "output")

    assert view.version == 1 and view.engine == "langgraph"
    assert view.engine_id == "graph" and view.decision_id == "graph/1"
    assert view.invocation_id is None
    assert view.mode == "scripted" and view.provider_mode == "stub"
    assert view.provider_profile is None and view.model == "scripted"
    assert view.transport_transformations is None
    assert view.graph_pin == initial["pin"]
    assert view.observation == initial["initial"]["observation"]
    assert view.observation_cut == view.observation["known_at"]
    assert view.outcome == "succeeded"
    assert view.outcome_code is None and view.failure is None
    assert view.message_ids == [message["id"] for message in messages]
    assert len(view.calls) == 2
    for call, message in zip(view.calls, model_calls):
        source = data(message)
        assert call.identity == source["identity"]
        assert call.attempt == 0 and call.round is None
        assert call.request == source["request"] and call.response == source["response"]
        assert call.usage == {"completion_tokens": 2} and call.latency_s == 0.0
        assert call.valid is True and call.failure_code is None and call.error is None
        assert len(call.validations) == 1 and call.validations[0].valid is True
        assert call.validations[0].code is None and call.validations[0].call_id is None
        assert call.validations[0].message_id == message["id"]
        assert call.message_ids == [message["id"]]
    assert [output.kind for output in view.outputs] == ["event", "command", "fact"]
    assert [output.data for output in view.outputs] == [
        data(message) for message in outputs
    ]
    assert [output.message_id for output in view.outputs] == [
        message["id"] for message in outputs
    ]
    published_ids = {
        message["id"]
        for message in projected_messages(kernel)
        if message["schemaId"] in {"example.out", "example.save"}
    }
    assert len(published_ids) == 2
    assert published_ids.isdisjoint(output.message_id for output in view.outputs)
    # Kernel command lifecycle receipts are not invented graph receipt evidence rows.
    assert view.receipts == []


@pytest.mark.parametrize(
    ("provider_mode", "expected"),
    [("stub", "scripted"), ("live", "structured_json")],
)
def test_graph_mode_comes_from_recorded_provider_not_json_or_tool_reply_content(
    graph_config: dict[str, Any],
    graph_module: ModuleType,
    graph_run: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    provider_mode: str,
    expected: str,
) -> None:
    def reply(value: int) -> dict[str, Any]:
        response = graph_module.completion({"value": value})
        response["message"]["tool_calls"] = [
            decision_call("noop", {"decision_summary": "Not a request-mode marker"})
        ]
        return response

    if provider_mode == "stub":
        graph_config["provider"].update(
            model="test-model",
            responses={"alpha": [reply(2)], "bravo": [reply(3)]},
        )
    else:
        graph_config["provider"] = {"mode": "live", "profile": "default"}
        monkeypatch.setenv("AAS_LLM_MODEL", "test-model")
        monkeypatch.setattr(
            OpenAIProvider, "complete", lambda *args, **kwargs: reply(2)
        )
    kernel, _ = graph_run(graph_config)
    messages = original_records(kernel, GRAPH_SCHEMA)
    view = from_langgraph_records(messages)
    assert view.mode == expected and view.provider_mode == provider_mode
    assert view.model == "test-model"
    assert view.provider_profile == ("default" if provider_mode == "live" else None)
    assert view.transport_transformations == ([] if provider_mode == "live" else None)
    assert all(call.request["tools"] == [] for call in view.calls)
    assert all(call.response["message"]["tool_calls"] for call in view.calls)
    assert all(call.valid is True for call in view.calls)
    old = from_langgraph_records(legacy_records(messages))
    # Without a stub marker or historical scripted model, request.tools decides.
    assert old.mode == "structured_json" and old.provider_mode is None


def test_graph_recorded_tools_request_not_json_reply_establishes_function_tools(
    decision_messages: list[dict[str, Any]],
    graph_config: dict[str, Any],
    graph_module: ModuleType,
    graph_run: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_config["factory"] = __name__ + ":function_tools_graph"
    graph_config["options"] = {
        "tools": data(phase_records(decision_messages, "prompt")[0])["tools"]
    }
    graph_config["provider"] = {"mode": "live", "profile": "default"}
    monkeypatch.setenv("AAS_LLM_MODEL", "test-model")
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *args, **kwargs: graph_module.completion({"value": 2}),
    )
    kernel, _ = graph_run(graph_config)
    messages = original_records(kernel, GRAPH_SCHEMA)
    for records in (messages, legacy_records(messages)):
        view = from_langgraph_records(records)
        assert view.mode == "function_tools" and view.outcome == "succeeded"
        assert len(view.calls) == 1 and view.calls[0].request["tools"]
        assert "tool_calls" not in view.calls[0].response["message"]
        # complete() alone does not establish ask_json validation success.
        assert view.calls[0].valid is None and view.calls[0].validations == []


@pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        (("structured_json", "function_tools"), None),
        (("function_tools", "structured_json"), None),
        (("structured_json", "structured_json"), "structured_json"),
        (("function_tools", "function_tools"), "function_tools"),
    ],
)
def test_graph_aggregate_mode_requires_request_mode_agreement(
    legacy: bool, modes: tuple[str, str], expected: str | None
) -> None:
    tool = {
        "type": "function",
        "function": {"name": "noop", "parameters": {"type": "object"}},
    }
    messages = [
        {
            "id": f"call-{index}",
            "schemaId": GRAPH_SCHEMA,
            "payload": {
                "decision_id": "graph/1",
                "phase": "model_call",
                "data_json": json.dumps(
                    {
                        "execution_mode": mode,
                        "provider_mode": "live",
                        "request": {
                            "model": "test-model",
                            "tools": [] if mode == "structured_json" else [tool],
                        },
                    }
                ),
            },
        }
        for index, mode in enumerate(modes)
    ]
    view = from_langgraph_records(legacy_records(messages) if legacy else messages)
    assert view.mode == expected
    assert [call.mode for call in view.calls] == list(modes)


def test_graph_output_rejection_is_all_or_none(
    graph_config: dict[str, Any], graph_run: Callable[..., Any]
) -> None:
    graph_config["factory"] = __name__ + ":late_invalid_output_graph"
    kernel, _ = graph_run(graph_config)
    messages = original_records(kernel, GRAPH_SCHEMA)
    view = from_langgraph_records(messages)
    assert view.outcome == "failed" and view.outcome_code == "TOOL_REJECTED"
    assert view.failure == data(phase_records(messages, "failure")[0])
    rejected = view.failure["result"]["outputs"]
    assert rejected[0]["kind"] == "event" and rejected[0]["payload"] == {"value": 5}
    assert rejected[1]["kind"] == "command"
    assert rejected[1]["payload"] == {"value": True}
    assert view.outputs == [] and view.receipts == []
    assert len(view.calls) == 2 and all(call.valid is True for call in view.calls)
    assert all(call.failure_code is None and call.error is None for call in view.calls)
    assert not [
        message
        for message in projected_messages(kernel)
        if message["schemaId"] in {"example.out", "example.save"}
    ]


@pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
def test_graph_ask_json_retry_retains_attempt_rejection_and_legacy_identity(
    graph_config: dict[str, Any],
    graph_module: ModuleType,
    graph_run: Callable[..., Any],
    legacy: bool,
) -> None:
    graph_config["provider"]["responses"]["alpha"].insert(
        0, graph_module.completion({"value": False})
    )
    kernel, _ = graph_run(graph_config)
    originals = original_records(kernel, GRAPH_SCHEMA)
    recorded_calls = [
        data(message) for message in phase_records(originals, "model_call")
    ]
    assert [row["attempt"] for row in recorded_calls] == [0, 1, 0]
    assert [row["failure_code"] for row in recorded_calls] == [
        "TOOL_REJECTED",
        None,
        None,
    ]
    assert recorded_calls[0]["validation"]["valid"] is False
    assert recorded_calls[0]["validation"]["code"] == "TOOL_REJECTED"
    assert recorded_calls[0]["rejection"]["code"] == "TOOL_REJECTED"
    assert all(row["validation"]["valid"] is True for row in recorded_calls[1:])
    messages = legacy_records(originals) if legacy else originals
    view = from_langgraph_records(messages)
    calls = view.calls
    assert [call.identity[1:] for call in calls] == [
        ["alpha", "alpha", 0],
        ["alpha", "alpha", 1],
        ["bravo", "bravo", 0],
    ]
    assert [call.attempt for call in calls] == [0, 1, 0]
    assert all(call.round is None for call in calls)
    assert calls[0].valid is False and calls[0].failure_code == "TOOL_REJECTED"
    assert calls[0].error is None
    assert json.loads(calls[0].response["message"]["content"]) == {"value": False}
    assert calls[0].validations[0].valid is False
    assert calls[0].validations[0].code == "TOOL_REJECTED"
    assert calls[0].validations[0].message_id == calls[0].message_ids[0]
    assert json.loads(calls[1].request["messages"][-1]["content"])["status"] == (
        "rejected"
    )
    assert [call.valid for call in calls[1:]] == (
        [None, None] if legacy else [True, True]
    )
    if legacy:
        assert all(call.validations == [] for call in calls[1:])
        assert view.provider_mode is None and view.provider_profile is None
        assert view.transport_transformations is None
    assert view.mode == "scripted" and view.model == "scripted"
    assert view.outcome == "succeeded" and view.outcome_code is None
    assert len(view.outputs) == 3
    assert view.message_ids == [message["id"] for message in originals]
    assert [call.message_ids for call in calls] == [
        [message["id"]] for message in phase_records(originals, "model_call")
    ]


@pytest.mark.parametrize("code", ["HTTP_STATUS", "TRANSPORT"])
@pytest.mark.parametrize("recovers", [False, True], ids=["exhausted", "recovers"])
def test_graph_ask_json_transport_retry_keeps_each_attempt_and_failure_code(
    graph_config: dict[str, Any],
    graph_module: ModuleType,
    graph_run: Callable[..., Any],
    code: str,
    recovers: bool,
) -> None:
    error = {"error": {"code": code, "message": "offline fixture failure"}}
    graph_config["provider"]["responses"]["alpha"] = [
        error,
        graph_module.completion({"value": 2}) if recovers else deepcopy(error),
    ]
    kernel, _ = graph_run(graph_config)
    messages = original_records(kernel, GRAPH_SCHEMA)
    recorded_alpha = [
        row
        for message in phase_records(messages, "model_call")
        if (row := data(message))["identity"][2] == "alpha"
    ]
    assert [row["attempt"] for row in recorded_alpha] == [0, 1]
    assert recorded_alpha[0]["failure_code"] == code
    assert recorded_alpha[0]["error"]["code"] == code
    assert recorded_alpha[0].get("validation") is None
    if recovers:
        assert recorded_alpha[1]["failure_code"] is None
        assert recorded_alpha[1]["validation"]["valid"] is True
    else:
        assert recorded_alpha[1]["failure_code"] == code
        assert recorded_alpha[1]["error"]["code"] == code
        assert recorded_alpha[1].get("validation") is None
    view = from_langgraph_records(messages)
    alpha = [call for call in view.calls if call.identity[2] == "alpha"]
    assert len(alpha) == 2 and [call.attempt for call in alpha] == [0, 1]
    first = alpha[0]
    assert first.failure_code == code and first.error["code"] == code
    assert first.response is None and first.usage is None
    assert first.valid is None and first.validations == []
    assert first.latency_s == 0.0
    # Transport retries do not invent JSON rejection messages.
    assert alpha[1].request == first.request
    assert alpha[1].message_ids != first.message_ids
    if recovers:
        assert alpha[1].valid is True and alpha[1].failure_code is None
        assert alpha[1].error is None and alpha[1].validations[0].valid is True
        assert view.outcome == "succeeded" and view.outcome_code is None
        assert len(view.outputs) == 3
    else:
        assert alpha[1].valid is None and alpha[1].failure_code == code
        assert alpha[1].error["code"] == code
        assert view.outcome == "failed" and view.outcome_code == code
        assert view.failure["code"] == code
        assert view.outputs == []


def test_graph_ask_json_exhausted_validation_keeps_both_rejected_attempts(
    graph_config: dict[str, Any],
    graph_module: ModuleType,
    graph_run: Callable[..., Any],
) -> None:
    graph_config["provider"]["responses"]["alpha"] = [
        graph_module.completion({"value": False}),
        graph_module.completion({"value": "wrong type"}),
    ]
    kernel, _ = graph_run(graph_config)
    messages = original_records(kernel, GRAPH_SCHEMA)
    recorded_alpha = [
        row
        for message in phase_records(messages, "model_call")
        if (row := data(message))["identity"][2] == "alpha"
    ]
    assert [row["attempt"] for row in recorded_alpha] == [0, 1]
    assert all(row["failure_code"] == "TOOL_REJECTED" for row in recorded_alpha)
    assert all(row["validation"]["valid"] is False for row in recorded_alpha)
    assert all(row["validation"]["code"] == "TOOL_REJECTED" for row in recorded_alpha)
    view = from_langgraph_records(messages)
    alpha = [call for call in view.calls if call.identity[2] == "alpha"]
    assert [call.attempt for call in alpha] == [0, 1]
    assert [call.valid for call in alpha] == [False, False]
    assert [call.failure_code for call in alpha] == ["TOOL_REJECTED", "TOOL_REJECTED"]
    assert all(call.validations[0].code == "TOOL_REJECTED" for call in alpha)
    assert view.outcome == "failed" and view.outcome_code == "TOOL_REJECTED"
    assert view.outputs == [] and view.receipts == []


def test_unknown_graph_attempt_validation_and_metadata_stay_none(
    graph_messages: list[dict[str, Any]],
) -> None:
    message = deepcopy(phase_records(graph_messages, "model_call")[0])
    body = data(message)
    body.pop("attempt", None)
    body["identity"][-1] = "unknown"
    body.update(
        validation={"valid": "true", "code": 7},
        latency_s=-1,
        execution_mode="unknown",
        provider_mode="unknown",
        provider_profile=False,
        model=7,
        transport_transformations="unknown",
    )
    body["request"]["model"] = None
    body["response"]["usage"] = []
    message["payload"]["data_json"] = json.dumps(body)
    view = from_langgraph_records(message)
    assert view.mode is None and view.model is None
    assert view.provider_mode is None and view.provider_profile is None
    assert view.transport_transformations is None
    assert view.outcome is None
    assert view.outcome_code is None and view.failure is None
    assert view.calls[0].identity == body["identity"]
    assert view.calls[0].attempt is None and view.calls[0].round is None
    assert view.calls[0].usage is None and view.calls[0].latency_s is None
    assert view.calls[0].valid is None
    assert view.calls[0].validations[0].valid is None
    assert view.calls[0].validations[0].code is None


@pytest.mark.parametrize(("adapter", "record_fixture"), ADAPTER_CASES)
def test_single_payload_message_list_and_wrapper_input_forms(
    request: pytest.FixtureRequest,
    adapter: Callable[..., AgentExecution],
    record_fixture: str,
) -> None:
    messages = request.getfixturevalue(record_fixture)
    full = adapter(messages)
    call_phase = "prompt" if full.engine == "decision" else "model_call"
    evidence = phase_records(messages, call_phase)[0]
    single = adapter(evidence)
    raw = adapter(evidence["payload"])
    assert len(single.calls) == len(raw.calls) == 1
    assert single.engine_id == raw.engine_id == full.engine_id
    assert single.decision_id == raw.decision_id == full.decision_id
    assert single.message_ids == single.calls[0].message_ids == [evidence["id"]]
    assert raw.message_ids == raw.calls[0].message_ids == []
    assert single.calls[0].request == raw.calls[0].request
    assert single.outcome is None and raw.outcome is None

    payload_view = adapter([message["payload"] for message in messages])
    assert payload_view.decision_id == full.decision_id
    assert payload_view.message_ids == []
    assert all(output.message_id is None for output in payload_view.outputs)
    assert all(receipt.message_id is None for receipt in payload_view.receipts)
    assert all(
        validation.message_id is None
        for call in payload_view.calls
        for validation in call.validations
    )
    phase_only = [
        {
            "phase": message["payload"]["phase"],
            "code": message["payload"]["code"],
            "data": data(message),
        }
        for message in messages
    ]
    wrapped = adapter(
        {
            "decision_id": full.decision_id,
            "engine_id": full.engine_id,
            "invocation_id": "fixture-invocation",
            "records": phase_only,
        }
    )
    assert wrapped.decision_id == full.decision_id
    assert wrapped.engine_id == full.engine_id
    assert wrapped.invocation_id == "fixture-invocation" and wrapped.message_ids == []
    assert wrapped.observation == full.observation and wrapped.outcome == full.outcome
    assert len(wrapped.calls) == len(full.calls)
    assert len(wrapped.outputs) == len(full.outputs)
    assert adapter({"records": messages}) == full
    # Same evidence ID is not another row.
    assert adapter(messages + [messages[-1]]) == full


@pytest.mark.parametrize(("adapter", "record_fixture"), ADAPTER_CASES)
def test_frozen_execution_detaches_nested_mappings_lists_and_message_ids(
    request: pytest.FixtureRequest,
    adapter: Callable[..., AgentExecution],
    record_fixture: str,
) -> None:
    source = deepcopy(request.getfixturevalue(record_fixture))
    # Exercise actual mapping inputs too, not just isolation provided by json.loads.
    for message in source:
        body = message["payload"]
        body["data"] = json.loads(body.pop("data_json"))
    saved = deepcopy(source)
    view = adapter(source)
    expected = deepcopy(view)
    for message in source:
        message["payload"]["data"]["source-only"] = ["changed"]
        message["id"] = "changed-source-id"
        record = message["payload"]["data"]
        if isinstance(record.get("request"), dict):
            record["request"]["messages"][0]["content"] = "changed source request"
        elif message["payload"]["phase"] == "prompt":
            record["messages"][0]["content"] = "changed source prompt"
    assert view == expected

    source = deepcopy(saved)
    view = adapter(source)
    with pytest.raises(FrozenInstanceError):
        setattr(view, "engine_id", "another-engine")
    view.message_ids.append("view-only-id")
    view.observation["fields"].append({"view-only": True})
    view.observation_cut["view-only"] = True
    view.calls[0].request["messages"][0]["content"] = "changed detached request"
    view.calls[0].response["message"]["content"] = "changed detached response"
    view.calls[0].usage["view-only"] = True
    view.calls[0].message_ids.append("view-only-call-id")
    view.calls[0].validations[0].data["view-only"] = True
    view.outputs[0].data["view-only"] = True
    if view.calls[0].identity is not None:
        view.calls[0].identity.append("view-only")
    if view.graph_pin is not None:
        view.graph_pin["view-only"] = True
    if view.transport_transformations is not None:
        view.transport_transformations.append("view-only")
    if view.receipts:
        view.receipts[0].data["view-only"] = True
    assert source == saved
    assert adapter(source) == expected


@pytest.mark.parametrize("adapter", [from_agent_record, from_langgraph_records])
def test_empty_evidence_does_not_invent_mode_identity_metadata_or_outcome(
    adapter: Callable[..., AgentExecution],
) -> None:
    view = adapter([])
    assert view.version == 1
    assert view.engine_id is None and view.decision_id is None
    assert view.invocation_id is None
    assert view.mode is None and view.provider_mode is None
    assert view.provider_profile is None
    assert view.model is None and view.transport_transformations is None
    assert view.observation is None and view.observation_cut is None
    assert view.graph_pin is None
    assert view.calls == [] and view.outputs == [] and view.receipts == []
    assert view.outcome is None
    assert view.outcome_code is None and view.failure is None
    assert view.message_ids == []


def test_rejects_mixed_real_decisions(
    decision_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    decision_document["engines"]["zz_decision"]["config"]["points"] = [
        {"timer_ns": 1},
        {"timer_ns": 2},
    ]
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *args, **kwargs: decision_completion(
            [decision_call("noop", {"decision_summary": "Observe"})]
        ),
    )
    messages = run_decision(decision_document, until_ns=3)
    assert {message["payload"]["decision_id"] for message in messages} == {
        "zz_decision/1",
        "zz_decision/2",
    }
    with pytest.raises(ValueError, match="decision_id"):
        from_agent_record(messages)


@pytest.mark.parametrize(("adapter", "record_fixture"), ADAPTER_CASES)
def test_wrapper_cannot_override_a_different_recorded_decision(
    request: pytest.FixtureRequest,
    adapter: Callable[..., AgentExecution],
    record_fixture: str,
) -> None:
    messages = request.getfixturevalue(record_fixture)
    with pytest.raises(ValueError, match="decision_id"):
        adapter({"decision_id": "different-decision/99", "records": messages})


def test_graph_console_aliases_are_not_original_execution_evidence(
    graph_config: dict[str, Any], graph_run: Callable[..., Any]
) -> None:
    kernel, _ = graph_run(graph_config)
    messages = projected_messages(kernel)
    originals = [message for message in messages if message["schemaId"] == GRAPH_SCHEMA]
    aliases = [
        message for message in messages if message["schemaId"] == DECISION_SCHEMA
    ]
    assert aliases and originals
    assert all(data(message)["source_schema"] == GRAPH_SCHEMA for message in aliases)
    view = from_langgraph_records(originals)
    assert len(view.calls) == 2 and len(view.outputs) == 3
    assert not set(view.message_ids).intersection(message["id"] for message in aliases)
    with pytest.raises(ValueError, match="original|schema"):
        from_langgraph_records(originals + aliases)
    with pytest.raises(ValueError, match="original|aliases"):
        from_agent_record(aliases)
    with pytest.raises(ValueError, match="original|aliases"):
        from_langgraph_records(aliases[0]["payload"])
