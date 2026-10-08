"""Decision authority, bounded failure and engine-free journal replay gates."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from aerokernel.journal import replay
from aerokernel.state import Fact

from aeroagentsim.agents.decision import RECORD_SCHEMA
from aeroagentsim.agents.provider import OpenAIProvider, ProviderError
from aeroagentsim.agents.tools import ToolCatalog, ToolGrant, strict_json
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project


@pytest.fixture
def document() -> dict[str, Any]:
    scenario = load_scenario("scenarios/agents/llm-dispatch.yaml")
    data = deepcopy(scenario.document)
    data["engines"]["zz_decision"]["config"]["points"] = [{"timer_ns": 1}]
    return data


def records(simulation: Simulation) -> list[dict[str, Any]]:
    result = []
    for line in simulation.kernel.journal.bytes.splitlines():
        record = json.loads(line)
        if record.get("type") == "header":
            continue
        for message in project(record)["messages"]:
            if message["schemaId"] == RECORD_SCHEMA:
                payload = message["payload"]
                result.append(
                    {
                        **payload,
                        "data": json.loads(payload["data_json"]),
                        "at": message["at"],
                    }
                )
    return result


def completion(calls: list[dict[str, Any]], tokens: int = 10) -> dict[str, Any]:
    message = {"role": "assistant", "content": None, "tool_calls": calls}
    return {
        "message": message,
        "raw": {"choices": [{"message": message}]},
        "usage": {"completion_tokens": tokens},
    }


def call(name: str, args: Any, identity: str = "call-1") -> dict[str, Any]:
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_malformed_retry_grants_recording_and_zero_call_replay(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    invocations: list[list[dict[str, Any]]] = []

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        invocations.append(deepcopy(messages))
        if len(invocations) == 1:
            return completion(
                [call("not_granted", {"decision_summary": "Try forbidden command"})]
            )
        assert json.loads(messages[-1]["content"])["status"] == "rejected"
        return completion(
            [call("noop", {"decision_summary": "No action required"}, "call-2")]
        )

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(2)
    rows = records(simulation)
    assert len(invocations) == 2
    assert [row["data"]["valid"] for row in rows if row["phase"] == "validation"] == [
        False,
        True,
    ]
    assert not [row for row in rows if row["phase"] == "command"]
    observation = next(row["data"] for row in rows if row["phase"] == "observation")
    assert all(item["field"] != "aas.p1.energy_j" for item in observation["fields"])
    assert {row["at"]["ns"] for row in rows} == {"1"}
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *a, **kw: pytest.fail("model called during replay"),
    )
    recovered = replay(simulation.kernel.journal.bytes)
    assert recovered.view().cut == simulation.kernel.view().cut
    assert not recovered.incomplete
    simulation.close()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("timeout", "WALL_TIMEOUT"),
        ("tokens", "TOKEN_BUDGET"),
        ("calls", "CALL_BUDGET"),
        ("retries", "RETRY_BUDGET"),
        ("rounds", "ROUND_BUDGET"),
        ("prompt", "PROMPT_BUDGET"),
    ],
)
def test_failure_budgets_never_synthesize_commands(
    document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected: str,
) -> None:
    budget = document["engines"]["zz_decision"]["config"]["budget"]
    if failure == "rounds":
        budget["max_rounds"] = 1
    if failure == "prompt":
        budget["max_prompt_bytes"] = 1
    if failure == "retries":
        budget["max_retries"] = 0

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        if failure == "timeout":
            raise ProviderError("WALL_TIMEOUT", "model deadline expired")
        if failure == "tokens":
            return completion(
                [call("noop", {"decision_summary": "none"})], max_tokens + 1
            )
        if failure == "calls":
            return completion(
                [
                    call("noop", {"decision_summary": "none"}, str(i))
                    for i in range(budget["max_calls"] + 1)
                ]
            )
        return completion([call("forbidden", {"decision_summary": "bad"})])

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(2)
    rows = records(simulation)
    assert (
        next(row["data"]["code"] for row in rows if row["phase"] == "failure")
        == expected
    )
    assert not [row for row in rows if row["phase"] in {"command", "finished"}]
    simulation.close()


def test_command_and_receipt_correlate_to_replayed_state(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    invocations = 0

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        nonlocal invocations
        invocations += 1
        return completion(
            [
                call(
                    tools[0]["function"]["name"],
                    {
                        "entity": "order-1",
                        "resource": "uav-1",
                        "target": [30.0, 0.0, 10.0],
                        "decision_summary": "Assign first pending order",
                    },
                )
            ]
        )

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(10_000_000_000)
    rows = records(simulation)
    receipts = [row["data"] for row in rows if row["phase"] == "receipt"]
    assert [row["status"] for row in receipts] == [
        "submitted",
        "accepted",
        "executing",
        "succeeded",
    ]
    assert len({row["command_id"] for row in receipts}) == 1
    assert all(row["call_id"] == "call-1" for row in receipts)
    view = simulation.kernel.view()
    ref = next(
        ref for ref in simulation.scenario.manifest.entities if ref.id == "order-1"
    )
    fact = view.field((ref, "aas.p1.order_state"), view.instant)
    assert isinstance(fact, Fact) and fact.value == "accepted"
    monkeypatch.setattr(
        OpenAIProvider,
        "complete",
        lambda *a, **kw: pytest.fail("model called during replay"),
    )
    recovered = replay(simulation.kernel.journal.bytes)
    assert recovered.view().field((ref, "aas.p1.order_state"), view.instant) == fact
    assert invocations == 1
    simulation.close()


def test_compiled_tools_and_argument_authority(document: dict[str, Any]) -> None:
    registry = load_scenario(document).registry
    catalog = ToolCatalog(
        registry,
        (ToolGrant("aas.agent.assign", "operations", {"entity": ["order-1"]}),),
    )
    schema = catalog.tools[0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert catalog.tools[0]["function"]["strict"] is True
    for arguments in (
        {
            "entity": "order-2",
            "resource": "uav-1",
            "target": [30, 0, 10],
            "decision_summary": "forbidden",
        },
        {"entity": "order-1", "resource": "uav-1", "target": [30, 0, 10]},
        {
            "entity": "order-1",
            "resource": "uav-1",
            "target": [30, 0, 10],
            "decision_summary": " ",
            "extra": True,
        },
    ):
        with pytest.raises(ValueError):
            catalog.validate(
                catalog.tools[0]["function"]["name"], json.dumps(arguments)
            )
    with pytest.raises(ValueError):
        strict_json('{"entity":"order-1","entity":"order-2"}')
    with pytest.raises(ValueError):
        strict_json('{"value":NaN}')


def test_ungranted_predicate_fails_binding(document: dict[str, Any]) -> None:
    document["engines"]["zz_decision"]["config"]["points"] = [
        {"predicate": {"entity": "uav-1", "field": "aas.p1.energy_j", "value": 1.0}}
    ]
    with pytest.raises(ValueError, match="granted"):
        Simulation(load_scenario(document))


def test_events_receipts_predicate_transitions_and_wait_preserve_time(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = document["engines"]["zz_decision"]["config"]
    cfg["grants"]["events"] = [{"schema": "aas.motion.arrived", "topic": "arrival"}]
    cfg["points"] += [
        {"event": "aas.motion.arrived"},
        {"receipt": "succeeded"},
        {
            "predicate": {
                "entity": "uav-1",
                "field": "aas.agent.available",
                "value": True,
            }
        },
    ]
    seen: list[dict[str, Any]] = []
    simulation: Simulation

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        observation = json.loads(messages[-1]["content"])
        seen.append(observation)
        held = simulation.kernel.view().instant
        import time

        time.sleep(0.02)
        assert simulation.kernel.view().instant == held
        if len(seen) == 1:
            return completion(
                [
                    call(
                        tools[0]["function"]["name"],
                        {
                            "entity": "order-1",
                            "resource": "uav-1",
                            "target": [30, 0, 10],
                            "decision_summary": "assign",
                        },
                    )
                ]
            )
        if len(seen) == 2:
            return completion(
                [
                    call(
                        "wait",
                        {
                            "duration_ns": 1_000_000_000,
                            "decision_summary": "wait for business acceptance",
                        },
                    )
                ]
            )
        return completion([call("noop", {"decision_summary": "observe real outcome"})])

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    assert not seen  # First true is not a predicate transition.
    simulation.run_until(10_000_000_000)
    assert (
        len(seen) >= 4
    )  # initial timer, arrival, terminal receipt/predicate, explicit wait timer
    assert any(obs["events"] for obs in seen)
    assert any(
        any(row["status"] == "succeeded" for row in obs["receipts"]) for obs in seen
    )
    simulation.close()


def test_absence_and_granted_relations_retain_knowledge_semantics(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = document["engines"]["zz_decision"]["config"]
    cfg["grants"]["relations"] = ["oo:relation:observation-subject"]
    cfg["points"] = [{"timer_ns": 1}, {"timer_ns": 9_000_000_000}]
    next(entity for entity in document["entities"] if entity["id"] == "uav-1")[
        "facts"
    ].pop("aas.agent.available")
    observations: list[dict[str, Any]] = []

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        observations.append(json.loads(messages[-1]["content"]))
        if len(observations) == 1:
            return completion(
                [
                    call(
                        tools[0]["function"]["name"],
                        {
                            "entity": "order-1",
                            "resource": "uav-2",
                            "target": [30, 0, 10],
                            "decision_summary": "produce an actual arrival observation",
                        },
                    )
                ]
            )
        return completion(
            [call("noop", {"decision_summary": "retain explicit missing fact"})]
        )

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(9_000_000_001)
    observation = observations[-1]
    absent = next(
        row
        for row in observation["fields"]
        if row["entity"]["id"] == "uav-1" and row["field"] == "aas.agent.available"
    )
    assert absent["status"] == "absent" and "value" not in absent
    assert observation["known_at"]["index"] > 0
    assert observation["valid_at"]["ns"] == "9000000000"
    assert observation["relations"][0]["edges"]
    assert all(
        int(edge["available_ns"]) <= 9_000_000_000
        for edge in observation["relations"][0]["edges"]
    )
    simulation.close()
