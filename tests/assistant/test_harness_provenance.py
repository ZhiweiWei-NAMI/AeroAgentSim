"""W1-E provenance contracts with scripted providers and no workspace compilation."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from aeroagentsim.agents.provider import ProviderError
from aeroagentsim.assistant.api import session_data
from aeroagentsim.assistant.harness import Harness, recent_history
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner

from .test_harness import call, harness


STATUSES = {
    "running", "completed", "budget_exhausted", "provider_error", "error",
    "cancelled", "interrupted", "unknown",
}
WIRE_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id"}


def assert_parts(parts: list[dict[str, Any]], text: str, sources: set[str]) -> None:
    assert parts
    assert all(isinstance(part["text"], str) and part["text"] for part in parts)
    assert {part["source"] for part in parts} == sources
    assert "\n\n".join(part["text"] for part in parts) == text


def assert_event(event: dict[str, Any], sources: set[str], status: str) -> None:
    text = event["message"] if event["kind"] == "error" else event["text"]
    assert_parts(event["parts"], text, sources)
    assert event["status"] == status
    # A homogeneous shortcut is optional; a mixed reply must not claim one source.
    if "source" in event:
        assert len(sources) == 1 and event["source"] in sources


def assert_terminal(
    session: dict[str, Any], status: str, model_reply: str | None,
    *, calls: int, tokens: int | None = None,
) -> dict[str, Any]:
    assert status in STATUSES and status != "running"
    assert not session["busy"]
    turn = session["turns"][-1]
    assert turn["turn"] == session["turn"]
    assert turn["status"] == status
    assert turn["model_reply"] == model_reply
    assert isinstance(turn["parts"], list)
    assert turn["model_calls"] == calls
    assert turn["elapsed_s"] >= 0
    if tokens is not None:
        assert turn["tokens"] == tokens
    finished = session["events"][-1]
    assert finished["kind"] == "turn_finished"
    assert finished["turn"] == turn["turn"] and finished["status"] == status
    assert finished["model_calls"] == calls
    if tokens is not None:
        assert finished["tokens"] == tokens
    return turn


def assert_final_record(session: dict[str, Any], event: dict[str, Any], raw: str | None) -> None:
    record = session["transcript"][-1]
    assert record["role"] == "assistant" and not record.get("tool_calls")
    assert record["model_reply"] == raw
    assert record["parts"] == event["parts"]
    expected = event["text"]
    if event["suggestions"]:
        expected += "\nSuggestions: " + " | ".join(event["suggestions"])
    assert record["content"] == expected


def selected_plan(agent: Harness, identifier: str, *, effects: bool = False) -> None:
    """Install already-compiled facts without invoking a compiler or validator."""
    plan = {
        "version": 1, "base": {"template": "fixture"}, "title": "Recorded proposal",
        "summary": "", "operations": [], "assumptions": [], "open_questions": [],
        "note": "Fixture", "variant": None, "parent": None, "created_at": "fixture",
        "compile": {"ok": True, "operations": []},
        "validation": {"state": "valid", "errors": []},
        "scene": {"fleets": [{"id": "couriers", "count": 3}], "places": []},
    }
    if effects:
        plan["_model_prose"] = {"summary": "Pre-compilation prose"}
        plan["what_changes"] = [{"text": "Install low-altitude components", "text_zh": "安装低空组件"}]
    with agent.sessions.edit(identifier) as session:
        session["plan_version"] = 1
        session["plans"]["1"] = plan


def test_running_turn_then_exact_raw_reply_and_model_parts(tmp_path, monkeypatch):
    raw = "  A proposed place.\n\nSuggestions: Defaults | Two options | Later | Inspect | Fifth  \n"
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": raw}])
    started = agent.sessions.begin_turn(identifier, "Propose a place")
    running = agent.sessions.get(identifier)["turns"][-1]
    assert running["turn"] == started["turn"] and running["status"] == "running"
    assert running["model_reply"] is None and running["parts"] == []

    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "completed", raw, calls=1, tokens=7)
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert reply["text"] == "A proposed place."
    assert reply["suggestions"] == ["Defaults", "Two options", "Later", "Inspect"]
    assert_event(reply, {"model"}, "completed")
    assert_final_record(session, reply, raw)
    assert turn["parts"] == reply["parts"]
    assert len(provider.requests) == 1 and tools.executed == []
    assert session["workspace_id"] is None and session["applied"] == []


@pytest.mark.parametrize("same_as_grounded", [False, True])
def test_deterministic_run_summary_is_platform_even_when_provider_text_matches(tmp_path, monkeypatch, same_as_grounded):
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [])
    selected_plan(agent, identifier)
    story = {
        "run_id": "run-recorded", "status": "completed", "terminal": True,
        "coverage": {"recorded_events": 7}, "expectations": [], "events": [], "failures": [],
    }
    tools.run_story = lambda *args, **kwargs: story
    expected = (
        "Run run-recorded (completed) recorded 7 events.\n\n"
        "No expectations were configured; this does not establish success.\n\n"
        f"[Full Story and event evidence](/v1/assistant/sessions/{identifier}/story/run-recorded)"
    )
    raw = expected if same_as_grounded else "  Everything succeeded without any events.\nSuggestions: Inspect | Later  \n"
    provider.replies = iter([{"role": "assistant", "content": raw}])
    context = {"run_id": "run-recorded", "locale": "en"}
    started = agent.sessions.begin_turn(identifier, "Summarise what happened in this run.", context)
    agent.run_turn(identifier, started["turn"], context)
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "completed", raw, calls=1, tokens=7)
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert reply["text"] == expected
    assert reply["suggestions"] == ([] if same_as_grounded else ["Inspect", "Later"])
    assert_event(reply, {"platform"}, "completed")
    assert_final_record(session, reply, raw)
    assert turn["parts"] == reply["parts"] and tools.executed == []


@pytest.mark.parametrize("effects", [False, True])
def test_unchanged_model_body_and_appended_compiler_effects_or_status_are_separate_parts(tmp_path, monkeypatch, effects):
    raw = "  Keep every existing drone.\nSuggestions: Inspect | Later  \n"
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": raw}])
    selected_plan(agent, identifier, effects=effects)
    before = agent.sessions.get(identifier)
    started = agent.sessions.begin_turn(identifier, "Describe the selected proposal")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "completed", raw, calls=1, tokens=7)
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert_event(reply, {"model", "platform"}, "completed")
    assert reply["parts"][0] == {"text": "Keep every existing drone.", "source": "model"}
    platform = "\n\n".join(part["text"] for part in reply["parts"] if part["source"] == "platform")
    assert "3 UAVs" in platform and "validated" in platform and "Confirm and apply" in platform
    if effects:
        assert "What this changes:\n- Install low-altitude components" in platform
    assert reply["suggestions"] == ["Inspect", "Later"]
    assert_final_record(session, reply, raw)
    assert turn["parts"] == reply["parts"]
    assert tools.executed == [] and session["plans"] == before["plans"]
    assert session["workspace_id"] is None and session["applied"] == []


@pytest.mark.parametrize("body,effects", [
    ("Validation is still pending. Keep every existing drone.", False),
    ("Keep every existing drone.\n\nWhat this changes:\n- Invented fleet deletion", True),
])
def test_filtered_body_is_conservatively_platform_and_raw_reply_is_not_rewritten(tmp_path, monkeypatch, body, effects):
    raw = "  " + body + "\nSuggestions: Inspect | Later  \n"
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": raw}])
    selected_plan(agent, identifier, effects=effects)
    started = agent.sessions.begin_turn(identifier, "Describe the proposal")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    assert_terminal(session, "completed", raw, calls=1, tokens=7)
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert "Keep every existing drone." in reply["text"]
    assert "pending" not in reply["text"] and "Invented fleet deletion" not in reply["text"]
    assert_event(reply, {"platform"}, "completed")
    assert_final_record(session, reply, raw)
    assert tools.executed == []


def test_direct_answer_records_raw_before_filtering_on_the_final_transcript_record(tmp_path, monkeypatch):
    raw = " \tValidation is still pending. Keep every existing drone.\nSuggestions: Later  \n"
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [])
    selected_plan(agent, identifier)
    agent.sessions.begin_turn(identifier, "Describe the proposal")
    observed_raw = []
    original_grounded = agent._grounded_reply

    def observe_before_grounding(*args, **kwargs):
        observed_raw.append(agent.sessions.get(identifier)["turns"][-1]["model_reply"])
        return original_grounded(*args, **kwargs)

    monkeypatch.setattr(agent, "_grounded_reply", observe_before_grounding)
    agent._answer(identifier, {"role": "assistant", "content": raw})
    session = agent.sessions.get(identifier)
    assert observed_raw == [raw]
    reply = session["events"][-1]
    assert reply["kind"] == "assistant" and "pending" not in reply["text"]
    assert_event(reply, {"platform"}, "completed")
    assert_final_record(session, reply, raw)
    assert session["turns"][-1]["model_reply"] == raw
    assert provider.requests == [] and tools.executed == []


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_wind_correction_is_platform_with_exact_raw_provider_reply(tmp_path, monkeypatch, locale):
    raw = ("  风来自东方。\n保留所有无人机。\nSuggestions: 详情 | 稍后 \n" if locale == "zh-CN" else
           "  Wind comes from the east.\nKeep every drone.\nSuggestions: Details | Later \n")
    agent, _, fake_tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": raw}])
    tools = ToolRunner(object(), object(), agent.sessions, tmp_path)
    monkeypatch.setattr(tools, "get_current_scene", fake_tools.get_current_scene)
    monkeypatch.setattr(tools, "execute", fake_tools.execute)
    story = {
        "run_id": "run-wind", "status": "completed", "expectations": [], "events": [],
        "wind": {"profiles": {"world.lowalt.wind": {
            "vectors": [{"east_mps": 5, "north_mps": 0, "up_mps": 0, "direction_deg": 0}],
            "gusts": [],
        }}},
    }
    monkeypatch.setattr(tools, "run_story", lambda *args, **kwargs: story)
    agent.tools = tools
    context = {"run_id": "run-wind", "locale": locale}
    started = agent.sessions.begin_turn(identifier, "Explain the wind direction", context)
    agent.run_turn(identifier, started["turn"], context)
    session = agent.sessions.get(identifier)
    assert_terminal(session, "completed", raw, calls=1, tokens=7)
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert ("吹向东" if locale == "zh-CN" else "toward east") in reply["text"]
    assert ("保留所有无人机。" if locale == "zh-CN" else "Keep every drone.") in reply["text"]
    assert "from the east" not in reply["text"] and "来自东方" not in reply["text"]
    assert_event(reply, {"platform"}, "completed")
    assert_final_record(session, reply, raw)
    assert fake_tools.executed == [] and session["applied"] == []


@pytest.mark.parametrize("raw", [None, "", " \t\n"])
def test_budget_tool_calls_without_text_close_directly_as_platform_not_answer(tmp_path, monkeypatch, raw):
    final = call()
    final["content"] = raw
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [final], max_calls=0)
    answer_calls = []
    original_answer = agent._answer

    def record_answer(*args, **kwargs):
        answer_calls.append((args, kwargs))
        return original_answer(*args, **kwargs)

    monkeypatch.setattr(agent, "_answer", record_answer)
    started = agent.sessions.begin_turn(identifier, "Draft a proposal")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "budget_exhausted", raw, calls=1, tokens=7)
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert "No proposal" in reply["text"] and reply["suggestions"] == []
    assert_event(reply, {"platform"}, "budget_exhausted")
    assert_final_record(session, reply, raw)
    notices = [event for event in session["events"] if event["kind"] == "notice"]
    assert notices
    for notice in notices:
        assert_event(notice, {"platform"}, "budget_exhausted")
    assert turn["parts"] == [part for event in session["events"] if event["kind"] in {"assistant", "notice"} for part in event["parts"]]
    assert answer_calls == [] and tools.executed == []
    assert len(provider.requests) == 1 and provider.requests[0]["tools"] == []
    assert session["workspace_id"] is None and session["applied"] == []


@pytest.mark.parametrize("still_calls_tools", [False, True])
def test_budget_with_own_text_is_budget_exhausted_even_for_compliant_final_reply(tmp_path, monkeypatch, still_calls_tools):
    raw = "  I have not finished a proposal.\nSuggestions: Continue | Inspect  \n"
    final = call() if still_calls_tools else {"role": "assistant"}
    final["content"] = raw
    # Reject the first call without executing even a fake tool, then exhaust the budget.
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [call("{broken"), final], max_calls=1)
    started = agent.sessions.begin_turn(identifier, "Draft a proposal")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "budget_exhausted", raw, calls=2, tokens=14)
    replies = [event for event in session["events"] if event["kind"] == "assistant"]
    assert len(replies) == 1
    reply = replies[0]
    assert reply["text"] == "I have not finished a proposal."
    assert reply["suggestions"] == ["Continue", "Inspect"]
    assert_event(reply, {"model"}, "budget_exhausted")
    assert_final_record(session, reply, raw)
    notices = [event for event in session["events"] if event["kind"] == "notice"]
    assert notices
    for notice in notices:
        assert_event(notice, {"platform"}, "budget_exhausted")
    assert {part["source"] for part in turn["parts"]} == {"model", "platform"}
    assert turn["parts"] == [part for event in session["events"] if event["kind"] in {"assistant", "notice"} for part in event["parts"]]
    assert len(provider.requests) == 2 and provider.requests[-1]["tools"] == []
    assert tools.executed == [] and session["applied"] == []


@pytest.mark.parametrize("code", ["WALL_TIMEOUT", "HTTP_STATUS", "TRANSPORT", "PROTOCOL"])
def test_provider_failures_are_platform_errors_not_successful_assistant_replies(tmp_path, monkeypatch, code):
    sentinel = "PRIVATE_UPSTREAM_DETAIL_SENTINEL"
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [ProviderError(code, sentinel)])
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "provider_error", None, calls=1)
    errors = [event for event in session["events"] if event["kind"] == "error"]
    assert len(errors) == 1 and errors[0]["code"] == code
    assert_event(errors[0], {"platform"}, "provider_error")
    assert turn["parts"] == errors[0]["parts"]
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert not any(message["role"] == "assistant" for message in session["transcript"])
    assert sentinel not in json.dumps(session, ensure_ascii=False)
    assert tools.executed == [] and session["applied"] == []


@pytest.mark.parametrize("envelope", [
    None, [], {}, {"message": None}, {"message": "MALFORMED_REPLY_SENTINEL"},
    {"message": {"role": "user", "content": "MALFORMED_REPLY_SENTINEL"},
     "headers": {"Authorization": "MALFORMED_REPLY_SENTINEL"}},
])
def test_malformed_provider_envelopes_are_platform_protocol_errors(tmp_path, monkeypatch, envelope):
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [])
    monkeypatch.setattr(provider, "complete", lambda *args, **kwargs: envelope)
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "provider_error", None, calls=1)
    error = next(event for event in session["events"] if event["kind"] == "error")
    assert error["code"] == "PROTOCOL"
    assert_event(error, {"platform"}, "provider_error")
    assert turn["parts"] == error["parts"]
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert not any(message["role"] == "assistant" for message in session["transcript"])
    assert "MALFORMED_REPLY_SENTINEL" not in json.dumps(session)
    assert tools.executed == []


def test_intermediate_tool_narration_is_not_inferred_as_final_reply_after_provider_failure(tmp_path, monkeypatch):
    intermediate = call("{broken")
    intermediate["content"] = "I will look for a place before answering."
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [intermediate, ProviderError("TRANSPORT", "Private upstream failure")])
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    assert_terminal(session, "provider_error", None, calls=2, tokens=7)
    error = next(event for event in session["events"] if event["kind"] == "error")
    assert_event(error, {"platform"}, "provider_error")
    assert not any(event["kind"] == "assistant" for event in session["events"])
    intermediate_record = next(message for message in session["transcript"] if message.get("tool_calls"))
    assert intermediate_record["content"] == intermediate["content"]
    assert intermediate_record.get("model_reply") is None
    assert tools.executed == []


@pytest.mark.parametrize("raw", [None, "", " \t\n", " \t\nSuggestions: Later | Inspect  \n"])
def test_empty_final_preserves_raw_content_before_protocol_error(tmp_path, monkeypatch, raw):
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": raw}])
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "provider_error", raw, calls=1, tokens=7)
    error = next(event for event in session["events"] if event["kind"] == "error")
    assert error["code"] == "PROTOCOL"
    assert_event(error, {"platform"}, "provider_error")
    assert turn["parts"] == error["parts"]
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert not any(message["role"] == "assistant" for message in session["transcript"])
    assert tools.executed == []


def test_raw_final_survives_a_transformation_failure_without_a_successful_assistant_event(tmp_path, monkeypatch):
    raw = " \tThe final provider content.\nSuggestions: Inspect | Later \n"
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [{"role": "assistant", "content": raw}])

    def broken_grounding(*args, **kwargs):
        raise RuntimeError("Fixture transformation failure")

    monkeypatch.setattr(agent, "_grounded_reply", broken_grounding)
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    assert_terminal(session, "error", raw, calls=1, tokens=7)
    error = next(event for event in session["events"] if event["kind"] == "error")
    assert error["code"] == "TURN_ERROR"
    assert_event(error, {"platform"}, "error")
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert not any(message["role"] == "assistant" for message in session["transcript"])
    assert tools.executed == []


@pytest.mark.parametrize("error,code", [(ValueError("Invalid fixture configuration"), "CONFIGURATION"), (RuntimeError("Private fixture failure"), "TURN_ERROR")])
def test_non_provider_failures_have_error_status_and_platform_provenance(tmp_path, monkeypatch, error, code):
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [error])
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    assert_terminal(session, "error", None, calls=1)
    failure = next(event for event in session["events"] if event["kind"] == "error")
    assert failure["code"] == code
    assert_event(failure, {"platform"}, "error")
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert tools.executed == []


@pytest.mark.parametrize("after_provider", [False, True])
def test_cancellation_is_terminal_platform_status_and_never_executes_tools(tmp_path, monkeypatch, after_provider):
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [])
    started = agent.sessions.begin_turn(identifier, "Draft")
    if after_provider:
        def cancelled(*args, **kwargs):
            agent.sessions.cancel(identifier)
            return {"message": call(), "usage": {"total_tokens": 7}}

        monkeypatch.setattr(provider, "complete", cancelled)
    else:
        assert agent.sessions.cancel(identifier)
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    turn = assert_terminal(session, "cancelled", None, calls=int(after_provider), tokens=7 if after_provider else None)
    notices = [event for event in session["events"] if event["kind"] == "notice"]
    assert notices
    for notice in notices:
        assert_event(notice, {"platform"}, "cancelled")
    assert turn["parts"] == [part for event in notices for part in event["parts"]]
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert tools.executed == [] and session["applied"] == []
    assert agent.sessions.begin_turn(identifier, "Try again")["turn"] == started["turn"] + 1


@pytest.mark.parametrize("tool_calls,max_calls", [(False, 10), (True, 10), (True, 0)])
def test_cancelled_returned_text_is_audited_only_if_final_and_is_never_displayed(tmp_path, monkeypatch, tool_calls, max_calls):
    raw = " \tReturned provider text before cancellation.\nSuggestions: Inspect | Later  \n"
    message = call() if tool_calls else {"role": "assistant"}
    message["content"] = raw
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [], max_calls=max_calls)

    def cancelled(*args, **kwargs):
        agent.sessions.cancel(identifier)
        return {"message": message, "usage": {"total_tokens": 7}}

    monkeypatch.setattr(provider, "complete", cancelled)
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    expected_raw = raw if not tool_calls or max_calls == 0 else None
    turn = assert_terminal(session, "cancelled", expected_raw, calls=1, tokens=7)
    notices = [event for event in session["events"] if event["kind"] == "notice"]
    assert [event["status"] for event in notices] == (["budget_exhausted", "cancelled"] if max_calls == 0 else ["cancelled"])
    for notice in notices:
        assert_event(notice, {"platform"}, notice["status"])
    assert turn["parts"] == [part for event in notices for part in event["parts"]]
    assert not any(event["kind"] == "assistant" for event in session["events"])
    assert not any(record["role"] == "assistant" for record in session["transcript"])
    assert tools.executed == [] and session["applied"] == []


def test_raw_content_not_transport_envelope_or_header_credentials_persists_in_store_and_api(tmp_path, monkeypatch):
    raw = " \tOnly this provider message is the raw reply.\nSuggestions: Inspect | Later  \n"
    sentinels = ["ENVELOPE_SENTINEL", "HEADER_CREDENTIAL_SENTINEL", "MESSAGE_METADATA_SENTINEL", "USAGE_METADATA_SENTINEL"]
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [{
        "role": "assistant", "content": raw,
        "metadata": {"credential": sentinels[2]}, "headers": {"Authorization": sentinels[1]},
    }])
    original_complete = provider.complete

    def with_transport_metadata(*args, **kwargs):
        response = original_complete(*args, **kwargs)
        response.update({"request_id": sentinels[0], "headers": {"Authorization": sentinels[1]}, "api_key": sentinels[1]})
        response["usage"]["private_metadata"] = sentinels[3]
        return response

    monkeypatch.setattr(provider, "complete", with_transport_metadata)
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.run_turn(identifier, started["turn"])
    session = agent.sessions.get(identifier)
    assert_terminal(session, "completed", raw, calls=1, tokens=7)
    disk = json.loads((agent.sessions.directory(identifier) / "session.json").read_text(encoding="utf-8"))
    restarted = SessionStore(tmp_path)
    public = session_data(restarted, agent.connection, identifier)
    polled = restarted.events(identifier)
    for view in (session, disk, restarted.get(identifier), public, polled):
        serialized = json.dumps(view, ensure_ascii=False)
        assert all(sentinel not in serialized for sentinel in sentinels)
    assert public["turns"] == session["turns"]
    for key in ("turn", "status", "model_reply", "parts", "elapsed_s", "model_calls", "tokens"):
        assert disk["turns"][-1][key] == session["turns"][-1][key]
    assert public["events"] == polled["events"] == session["events"]
    assert "transcript" not in public
    assert public["turns"][-1]["model_reply"] == raw
    assert disk["transcript"][-1]["model_reply"] == raw
    assert tools.executed == []


@pytest.mark.parametrize("reload", [False, True])
def test_legacy_read_reload_api_and_events_normalize_unknown_without_inventing_raw(tmp_path, monkeypatch, reload):
    agent, _, _, identifier = harness(tmp_path, monkeypatch, [])
    legacy = agent.sessions.get(identifier)
    legacy.pop("turns", None)
    legacy.update({"turn": 1, "last_seq": 4, "busy": False})
    legacy["transcript"] = [
        {"role": "user", "content": "Legacy request"},
        {"role": "assistant", "content": "Historical composite.\nSuggestions: Later"},
    ]
    legacy["events"] = [
        {"seq": 1, "at": "fixture", "kind": "user", "text": "Legacy request"},
        {"seq": 2, "at": "fixture", "kind": "turn_started", "turn": 1},
        {"seq": 3, "at": "fixture", "kind": "assistant", "text": "Historical composite.", "suggestions": ["Later"]},
        {"seq": 4, "at": "fixture", "kind": "turn_finished", "turn": 1, "elapsed_s": 0.1, "model_calls": 1},
    ]
    if reload:
        path = agent.sessions.directory(identifier) / "session.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        store = SessionStore(tmp_path)
    else:
        # Exercise normal read normalization, not just the on-disk migration path.
        agent.sessions._sessions[identifier] = copy.deepcopy(legacy)
        store = agent.sessions
    session = store.get(identifier)
    assert session["turns"] == []
    assert session["events"][-1]["status"] == "unknown"
    reply = next(event for event in session["events"] if event["kind"] == "assistant")
    assert_event(reply, {"unknown"}, "unknown")
    assert reply["text"] == "Historical composite." and reply["suggestions"] == ["Later"]
    record = session["transcript"][-1]
    assert record["content"] == legacy["transcript"][-1]["content"]
    assert {part["source"] for part in record["parts"]} == {"unknown"}
    assert record.get("model_reply") is None
    public = session_data(store, agent.connection, identifier)
    polled = store.events(identifier, after=2)
    assert public["turns"] == [] and "transcript" not in public
    assert public["events"] == session["events"]
    assert polled["events"] == session["events"][2:]
    assert polled["last_seq"] == 4 and not polled["busy"]
    assert store.get(identifier) == session  # Read normalization is idempotent.


def test_restart_marks_running_turn_interrupted_with_platform_notice_and_no_raw_reply(tmp_path, monkeypatch):
    agent, _, tools, identifier = harness(tmp_path, monkeypatch, [])
    started = agent.sessions.begin_turn(identifier, "Draft")
    agent.sessions.append(identifier, call())
    assert agent.sessions.get(identifier)["turns"][-1]["status"] == "running"
    restarted = SessionStore(tmp_path)
    session = restarted.get(identifier)
    assert not session["busy"]
    turn = session["turns"][-1]
    assert turn["turn"] == started["turn"] and turn["status"] == "interrupted"
    assert turn["model_reply"] is None
    notices = [event for event in session["events"] if event["kind"] == "notice"]
    assert len(notices) == 1
    assert_event(notices[0], {"platform"}, "interrupted")
    assert turn["parts"] == notices[0]["parts"]
    assert not any(event["kind"] == "assistant" for event in session["events"])
    pending_result = session["transcript"][-1]
    assert pending_result["role"] == "tool" and pending_result["tool_call_id"] == "call-1"
    assert "error" in json.loads(pending_result["content"])
    assert [event["seq"] for event in session["events"]] == list(range(1, session["last_seq"] + 1))
    public = session_data(restarted, agent.connection, identifier)
    assert public["turns"] == session["turns"]
    assert restarted.events(identifier)["events"] == session["events"]
    assert SessionStore(tmp_path).get(identifier) == session  # A second restart adds no new interruption.
    assert restarted.begin_turn(identifier, "Continue")["turn"] == started["turn"] + 1
    assert tools.executed == []


@pytest.mark.parametrize("compact", [False, True])
def test_recent_history_strips_provenance_and_turn_metadata_but_keeps_wire_pairs(compact):
    sentinel = "PROVENANCE_ONLY_SENTINEL"
    metadata = {
        "source": "platform", "parts": [{"text": sentinel, "source": "platform"}],
        "status": "completed", "turn": 17, "model_reply": sentinel,
        "statistics": {"private": sentinel}, "turns": [{"private": sentinel}],
        "elapsed_s": 0.1, "model_calls": 1, "tokens": 7,
        "headers": {"Authorization": sentinel}, "metadata": {"private": sentinel},
    }
    expected = [
        {"role": "user", "content": "Find a place"},
        call('{"query":"pad"}'),
        {"role": "tool", "name": "find_places", "tool_call_id": "call-1", "content": '{"places":[]}'},
        {"role": "assistant", "content": "No matching places."},
    ]
    transcript = [{**copy.deepcopy(message), **copy.deepcopy(metadata)} for message in expected]
    if compact:
        transcript[:0] = [{"role": "user", "content": "Older request", **copy.deepcopy(metadata)},
                         {"role": "assistant", "content": "Older answer", **copy.deepcopy(metadata)}]
    before = copy.deepcopy(transcript)
    history = recent_history(transcript, recent_turns=1)
    if compact:
        assert history[0]["role"] == "system" and "Older request" in history[0]["content"]
        assert history[1:] == expected
    else:
        assert history == expected
    assert all(set(message) <= WIRE_KEYS for message in history)
    assert sentinel not in json.dumps(history)
    assert transcript == before


def test_provenance_and_raw_reply_metadata_are_not_forwarded_on_next_provider_request(tmp_path, monkeypatch):
    raw = "  A proposed place.\nSuggestions: One | Two | Three | Four | RAW_ONLY_FIFTH_SENTINEL \n"
    agent, provider, tools, identifier = harness(tmp_path, monkeypatch, [
        {"role": "assistant", "content": raw},
        call('{"query":"pad"}'),
        {"role": "assistant", "content": "A follow-up answer."},
    ])
    selected_plan(agent, identifier)
    first = agent.sessions.begin_turn(identifier, "Describe the proposal")
    agent.run_turn(identifier, first["turn"])
    first_session = agent.sessions.get(identifier)
    first_record = first_session["transcript"][-1]
    assert first_record["model_reply"] == raw and first_record["parts"]
    assert "Confirm and apply" in first_record["content"]
    assert "RAW_ONLY_FIFTH_SENTINEL" not in first_record["content"]
    first_turn = copy.deepcopy(first_session["turns"][-1])
    second = agent.sessions.begin_turn(identifier, "Find a pad")
    agent.run_turn(identifier, second["turn"])
    session = agent.sessions.get(identifier)
    assert_terminal(session, "completed", "A follow-up answer.", calls=2, tokens=14)
    assert session["turns"][0] == first_turn
    assert [turn["turn"] for turn in session["turns"]] == [first["turn"], second["turn"]]
    assert len(provider.requests) == 3
    for request in provider.requests[1:]:
        assert all(set(message) <= WIRE_KEYS for message in request["messages"])
        assert "RAW_ONLY_FIFTH_SENTINEL" not in json.dumps(request["messages"])
        prior = next(message for message in request["messages"] if message["role"] == "assistant" and not message.get("tool_calls"))
        assert prior == {"role": "assistant", "content": first_record["content"]}
    wire = provider.requests[-1]["messages"]
    assert wire[-2]["tool_calls"][0]["id"] == wire[-1]["tool_call_id"] == "call-1"
    assert wire[-1]["role"] == "tool" and tools.executed == [("find_places", {"query": "pad"})]
    assert session["workspace_id"] is None and session["applied"] == []
