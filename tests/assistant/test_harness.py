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


def test_questions_from_a_run_carry_its_recorded_story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": "The watcher passed 210 m away."}])
    story = {"status": "completed", "terminal": True, "events": [{"schema": "lowalt.task.completed", "text": "Meal 1 delivered", "at_ns": 5}],
             "expectations": [{"id": "incident:fall:noticed", "text": "A watcher may notice", "status": "missed", "at_ns": None,
                               "explanation": {"text": "Closest recorded watcher approach: 210 m"}}]}
    tools.run_story = lambda session, run_id: {**story, "run_id": run_id}  # type: ignore[attr-defined]
    turn = agent.sessions.begin_turn(identifier, "Why didn't this happen?", {"run_id": "run-1"})
    agent.run_turn(identifier, turn["turn"], {"run_id": "run-1"})
    state = provider.requests[0]["messages"][1]["content"]
    evidence = json.loads(next(line for line in state.splitlines() if line.startswith("run_evidence: "))[len("run_evidence: "):])
    assert evidence["expectations"][0]["status"] == "missed" and evidence["expectations"][0]["recorded"] == "Closest recorded watcher approach: 210 m"
    assert evidence["events"] == [{"schema": "lowalt.task.completed", "text": "Meal 1 delivered", "at_ns": 5}]


def test_exhausted_turn_still_closes_with_an_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Some models keep calling tools after the budget ends; the user still gets a closing reply.
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call(), call()], max_calls=1)
    turn = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, turn["turn"])
    events = agent.sessions.get(identifier)["events"]
    reply = next(e for e in events if e["kind"] == "assistant")
    assert "No proposal" in reply["text"] and len(tools.executed) == 1
    assert any(e["kind"] == "notice" for e in events) and events[-1]["kind"] == "turn_finished"


def test_validation_refreshes_between_tool_calls_and_before_publication(tmp_path, monkeypatch):
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call(), {"role": "assistant", "content": "Validation is still pending. Wait before confirming."}])
    with agent.sessions.edit(identifier) as session:
        session["plan_version"] = 1
        session["plans"]["1"] = {"version": 1, "base": {}, "title": "Current", "summary": "", "operations": [], "assumptions": [], "open_questions": [],
                                  "compile": {"ok": True}, "validation": {"state": "pending", "errors": []}, "scene": {}}
    original = tools.execute

    def executed(*args):
        result = original(*args)
        agent.sessions.validated(identifier, 1, True, [])
        return result

    monkeypatch.setattr(tools, "execute", executed)
    turn = agent.sessions.begin_turn(identifier, "Prepare a proposal")
    agent.run_turn(identifier, turn["turn"])
    state = provider.requests[-1]["messages"][1]["content"]
    assert '"state":"valid"' in state
    answer = next(e["text"] for e in agent.sessions.get(identifier)["events"] if e["kind"] == "assistant")
    assert "pending" not in answer and "validated" in answer and "Confirm" in answer


def test_run_summary_cannot_deny_recorded_failure(tmp_path, monkeypatch):
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": "No events were recorded; everything succeeded."}])
    story = {"run_id": "run-real", "status": "completed", "terminal": True, "expectations": [], "events": [], "assessment": "unassessed",
             "coverage": {"recorded_events": 335, "schemas": {"traffic.incident.activated": 1, "traffic.capture.failed": 1}},
             "failures": [{"schema": "traffic.capture.failed", "text": "A capture failed", "at_ns": 57_100_000_000, "payload": {"actor": "uav.bravo", "reason": "renderer failed"}}]}
    tools.run_story = lambda session, run: story
    context = {"run_id": "run-real"}
    turn = agent.sessions.begin_turn(identifier, "Summarise what happened in this run compared with the plan.", context)
    agent.run_turn(identifier, turn["turn"], context)
    answer = next(e["text"] for e in agent.sessions.get(identifier)["events"] if e["kind"] == "assistant")
    assert "335" in answer and "renderer failed" in answer and "57.1 s" in answer
    assert "does not establish success" in answer and "everything succeeded" not in answer


def test_duplicate_json_keys_recover_with_schema_and_retained_operation_hint(tmp_path, monkeypatch):
    from aeroagentsim.assistant.tools import tool_schemas

    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call('{"query":"a","query":"b"}'), call('{"query":"fixed"}'), {"role": "assistant", "content": "Corrected."}])
    monkeypatch.setattr("aeroagentsim.assistant.harness.tool_schemas", tool_schemas)
    with agent.sessions.edit(identifier) as session:
        session["plan_version"] = 1
        session["plans"]["1"] = {"version": 1, "base": {}, "operations": [{"id": "op-1", "op": "add_place", "params": {"id": "south-pad"}}],
                                    "title": "Draft", "summary": "Draft", "assumptions": [], "open_questions": [],
                                    "compile": {"ok": False, "errors": [{"message": "Invalid draft"}]}, "validation": {"state": "invalid"}, "scene": {}}
    turn = agent.sessions.begin_turn(identifier, "Fix the tool request")
    agent.run_turn(identifier, turn["turn"])
    rejected = json.loads(provider.requests[1]["messages"][-1]["content"])
    assert rejected["code"] == "TOOL_ARGUMENTS" and "duplicate keys" in rejected["error"]
    assert "query" in rejected["schema"]["fields"]
    assert rejected["current_operations"] == [{"id": "op-1", "op": "add_place", "target": "south-pad"}]
    assert "do not re-add" in rejected["hint"] and tools.executed == [("find_places", {"query": "fixed"})]
    assert not agent.sessions.get(identifier)["busy"]


def test_large_summary_is_bounded_localized_and_links_recorded_details(tmp_path, monkeypatch):
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": "当前没有已运行的记录。"}])
    failures = [{"schema": "aas.behaviour.conflict", "text": "A conflict", "text_zh": "冲突", "at_ns": i,
                 "payload": {"predicate_context": "secret-internals" * 1000, "reason": "busy"}} for i in range(288)]
    failures.append({"schema": "traffic.capture.failed", "text": "Capture failed", "text_zh": "拍摄失败", "at_ns": 57_066_666_667,
                     "payload": {"reason": "{'status': 'timeout', 'reason': 'viewer asset initialization deadline exceeded'}"}})
    story = {"run_id": "run-chosen", "status": "completed", "terminal": True, "assessment": "incomplete",
             "coverage": {"recorded_events": 9211, "schemas": {"traffic.award.committed": 1}}, "events": [], "failures": failures,
             "expectations": [{"id": "award", "text": "Response award committed", "text_zh": "响应机分配已确认", "status": "happened", "at_ns": 23_066_666_667},
                              {"id": "capture", "text": "Capture stored", "text_zh": "图像已存储", "status": "missed", "at_ns": None}],
             "details_url": "/v1/assistant/sessions/session/story/run-chosen?locale=en"}
    requested = []
    def recorded(session, run_id, locale=None):
        requested.append((run_id, locale))
        return story
    tools.run_story = recorded
    with agent.sessions.edit(identifier) as session:
        session["locale"] = "zh-CN"
    context = {"run_id": "run-chosen", "locale": "en"}
    turn = agent.sessions.begin_turn(identifier, "Summarise what happened in this run compared with the plan.", context)
    agent.run_turn(identifier, turn["turn"], context)
    answer = next(e["text"] for e in agent.sessions.get(identifier)["events"] if e["kind"] == "assistant")
    assert requested == [("run-chosen", "en")]
    assert len(answer) <= 6000 and "9211" in answer and "289" in answer and "×288" in answer
    assert "Response award committed: recorded (23.0667 s)" in answer
    assert "viewer asset initialization deadline exceeded" in answer and "predicate_context" not in answer and "secret-internals" not in answer
    assert "[Full Story and event evidence]" in answer and story["details_url"] in answer
    assert "当前没有" not in answer and "拍摄失败" not in answer


def test_explicit_run_cannot_be_replaced_by_model_tool_selection(tmp_path, monkeypatch):
    requested_call = call('{"run_id":"run-other"}')
    requested_call["tool_calls"][0]["function"]["name"] = "run_story"
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [requested_call, {"role": "assistant", "content": "There is no run."}])
    tools.run_story = lambda session, run_id: {"run_id": run_id, "status": "completed", "coverage": {"recorded_events": 7}, "expectations": [], "events": []}
    context = {"run_id": "run-chosen"}
    turn = agent.sessions.begin_turn(identifier, "Summarise this run", context)
    agent.run_turn(identifier, turn["turn"], context)
    rejected = json.loads(provider.requests[1]["messages"][-1]["content"])
    assert "run-chosen" in rejected["error"] and not tools.executed
    answer = next(e["text"] for e in agent.sessions.get(identifier)["events"] if e["kind"] == "assistant")
    assert "Run run-chosen (completed) recorded 7 events" in answer and "There is no run" not in answer
