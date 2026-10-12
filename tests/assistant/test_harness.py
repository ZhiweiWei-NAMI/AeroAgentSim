"""Bounded tool turns with a scripted provider and no model network calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.agents.provider import ProviderError
from aeroagentsim.assistant.connection import ConnectionStore
from aeroagentsim.assistant.harness import Harness, recent_history
from aeroagentsim.assistant.sessions import SessionStore


class Provider:
    model = "fixture"

    def __init__(self, replies: list[Any]) -> None:
        self.replies = iter(replies)
        self.requests: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], tools: list[Any], **kwargs: Any) -> dict[str, Any]:
        self.requests.append({"messages": list(messages), "tools": tools, **kwargs})
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        return {"message": result, "usage": {"total_tokens": 7}}


class Tools:
    on_plan: Any = None
    agent_profile: str | None = None

    def __init__(self) -> None:
        self.executed: list[Any] = []

    def get_current_scene(self, identifier: str) -> dict[str, Any]:
        return {"scene": None}

    def execute(self, identifier: str, name: str, arguments: dict[str, Any]) -> Any:
        if set(arguments) - {"query"}:
            raise ValueError("unknown field / 未声明字段")
        self.executed.append((name, arguments))
        return {"places": [{"id": "real-place"}]}


def call(arguments: str = "{}") -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "find_places", "arguments": arguments}}]}


def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list[Any], *, max_calls: int = 10) -> tuple[Harness, Provider, Tools, str]:
    import aeroagentsim.assistant.harness as module

    monkeypatch.setenv("AEROAGENTSIM_PROVIDER_PROFILES", "{}")
    for name in ("AEROAGENTSIM_ASSISTANT_BASE_URL", "AEROAGENTSIM_ASSISTANT_MODEL", "AEROAGENTSIM_ASSISTANT_API_KEY_ENV", "AEROAGENTSIM_ASSISTANT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    provider = Provider(replies)
    connection = ConnectionStore(tmp_path, provider_factory=lambda *args: provider)
    connection.set("http://localhost/v1", "fixture")
    sessions = SessionStore(tmp_path)
    identifier = sessions.create()["id"]
    tools = Tools()
    monkeypatch.setattr(module, "tool_schemas", lambda: [{"type": "function", "function": {"name": "find_places", "parameters": {"type": "object"}}}])
    return Harness(sessions, connection, tools, max_calls=max_calls), provider, tools, identifier  # type: ignore[arg-type]


def test_tool_result_then_final_reply_and_statistics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call('{"query":"incident"}'), {"role": "assistant", "content": "A proposed place.\nSuggestions: Defaults | Two options | Later | Inspect | Fifth"}])
    turn = agent.sessions.begin_turn(identifier, "Propose a place")
    agent.run_turn(identifier, turn["turn"])
    session = agent.sessions.get(identifier)
    assert not session["busy"] and tools.executed == [("find_places", {"query": "incident"})]
    reply = next(e for e in session["events"] if e["kind"] == "assistant")
    assert reply["text"] == "A proposed place." and len(reply["suggestions"]) == 4
    assert session["events"][-1]["model_calls"] == 2 and session["events"][-1]["tokens"] == 14
    assert provider.requests[1]["messages"][-1]["role"] == "tool"
    assert provider.requests[0]["timeout_s"] == 90 and provider.requests[0]["max_tokens"] == 2500


@pytest.mark.parametrize("arguments", ["{broken", "null", '{"query":"a","query":"b"}', '{"query":NaN}', '{"extra":1}'])
def test_invalid_arguments_are_returned_to_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: str) -> None:
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call(arguments), {"role": "assistant", "content": "I will fix that / 我会修正"}])
    turn = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, turn["turn"])
    response = provider.requests[1]["messages"][-1]
    assert response["role"] == "tool" and "error" in json.loads(response["content"])
    assert not tools.executed
    assert any(e["kind"] == "tool" and e["status"] == "error" for e in agent.sessions.get(identifier)["events"])


def test_step_budget_asks_once_without_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call(), call(), {"role": "assistant", "content": "Budget finished / 预算已用尽"}], max_calls=2)
    turn = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, turn["turn"])
    assert len(provider.requests) == 3 and provider.requests[-1]["tools"] == []
    assert len(tools.executed) == 2 and not agent.sessions.get(identifier)["busy"]


def test_cancellation_after_model_response_prevents_tool_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [])
    turn = agent.sessions.begin_turn(identifier, "Draft")

    def cancelled(*args: Any, **kwargs: Any) -> dict[str, Any]:
        agent.sessions.cancel(identifier)
        return {"message": call(), "usage": None}

    monkeypatch.setattr(provider, "complete", cancelled)
    agent.run_turn(identifier, turn["turn"])
    assert not tools.executed and not agent.sessions.get(identifier)["busy"]
    assert any(e["kind"] == "notice" for e in agent.sessions.get(identifier)["events"])
    assert agent.sessions.begin_turn(identifier, "Try again")["turn"] == 2


@pytest.mark.parametrize("error", [ProviderError("WALL_TIMEOUT", "secret upstream text"), ProviderError("HTTP_STATUS", "upstream returned HTTP 401 secret"), ProviderError("TRANSPORT", "secret")])
def test_provider_errors_release_session_and_mask_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: ProviderError) -> None:
    agent, _, _, identifier = harness(tmp_path, monkeypatch, [error])
    turn = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, turn["turn"])
    events = agent.sessions.get(identifier)["events"]
    failure = next(e for e in events if e["kind"] == "error")
    assert failure["code"] == error.code and "secret" not in failure["message"]
    assert events[-1]["kind"] == "turn_finished" and not agent.sessions.get(identifier)["busy"]


def test_compaction_keeps_tool_call_result_pairs() -> None:
    transcript = []
    for index in range(12):
        transcript.extend([{"role": "user", "content": f"turn {index}"}, call(), {"role": "tool", "tool_call_id": "call-1", "content": "x" * 9000}, {"role": "assistant", "content": "done"}])
    compacted = recent_history(transcript, recent_turns=2)
    assert compacted[0]["role"] == "system"
    assert sum(m["role"] == "user" for m in compacted) == 2
    assert sum(m["role"] == "tool" for m in compacted) == 2
    assert all(len(m.get("content") or "") < 7000 for m in compacted)
