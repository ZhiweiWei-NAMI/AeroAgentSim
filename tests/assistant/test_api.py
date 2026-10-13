"""Assistant HTTP contract through TestClient, with scripted providers only."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.assistant.api import create_router
from aeroagentsim.assistant.connection import ConnectionStore
from aeroagentsim.assistant.harness import Harness
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner, tool_schemas


class Provider:
    model = "scripted"

    def __init__(self) -> None:
        self.started, self.release = threading.Event(), threading.Event()
        self.block = False

    def complete(self, messages: Any, tools: Any, **kwargs: Any) -> dict[str, Any]:
        if self.block:
            self.started.set()
            assert self.release.wait(2)
        return {"message": {"role": "assistant", "content": "A proposal remains a draft / 提议仍是草稿"}, "usage": {"total_tokens": 2}}


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from aeroagentsim.assistant import compiler

    monkeypatch.setenv("AEROAGENTSIM_PROVIDER_PROFILES", "{}")
    for name in ("AEROAGENTSIM_ASSISTANT_BASE_URL", "AEROAGENTSIM_ASSISTANT_MODEL", "AEROAGENTSIM_ASSISTANT_API_KEY_ENV", "AEROAGENTSIM_ASSISTANT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    provider = Provider()
    connection = ConnectionStore(tmp_path, provider_factory=lambda *args: provider)
    sessions = SessionStore(tmp_path)
    store = SimpleNamespace(get=lambda identifier: {"id": identifier, "revision": 1, "scenario": {}}, directory=lambda identifier: tmp_path / identifier)
    tools = ToolRunner(store, SimpleNamespace(), sessions, tmp_path)
    monkeypatch.setattr(tools, "context", lambda identifier: None)
    monkeypatch.setattr(compiler, "resolve_base", lambda ctx, base: SimpleNamespace(label="Real fixture / 真实测试数据", revision=1 if "workspace" in base else None))

    def compile(ctx: Any, base: Any, operations: Any, directory: Path, *, resolved: Any = None) -> Any:
        directory.mkdir(parents=True)
        data = dict(ok=True, errors=[], warnings=[], notes=[], operations=operations, document={"id": "fixture", "entities": []}, digest="fixture", summary={"fleets": [], "places": []}, diff_base={"added": [], "changed": [], "removed": []}, interactions=[], expectations=[{"schema": "fixture.incident"}])
        return SimpleNamespace(**data, to_data=lambda: copy.deepcopy(data))

    monkeypatch.setattr(compiler, "compile_plan", compile)
    validated = threading.Event()

    def validate(ctx: Any, directory: Path) -> dict[str, Any]:
        return {"valid": True, "errors": [], "issues": []}

    monkeypatch.setattr(compiler, "validate_compiled", validate)
    original = sessions.validated

    def record_validation(*args: Any) -> None:
        original(*args)
        validated.set()

    monkeypatch.setattr(sessions, "validated", record_validation)
    applied: list[Any] = []

    def apply(ctx: Any, directory: Path, **kwargs: Any) -> dict[str, Any]:
        applied.append(kwargs)
        return {"workspace_id": "fixture-project", "version": 1, "revision": 2, "validation": {"valid": True}, "report": {"created": ["fixture-entity"]}}

    monkeypatch.setattr(compiler, "apply_plan", apply)
    harness = Harness(sessions, connection, tools)
    app = FastAPI()
    app.include_router(create_router(store, run_root=tmp_path, templates=SimpleNamespace(), connection=connection, sessions=sessions, harness=harness))
    with TestClient(app) as client:
        yield {"client": client, "harness": harness, "sessions": sessions, "connection": connection, "provider": provider, "tools": tools, "validated": validated, "applied": applied, "root": tmp_path}


def draft(api: dict[str, Any], identifier: str) -> dict[str, Any]:
    return api["tools"].draft_plan(identifier, base={"template": "fixture"}, title="Fixture / 测试", summary="Fixture / 测试", operations=[{"op": "set_duration", "params": {"seconds": 300}}], assumptions=[], open_questions=[], note="Initial / 初始")


def pin_scenario(directory: Path) -> None:
    """Story reads weather from a run's fixed input, independently of the plan."""
    document = {
        "id": "fixture", "entities": [], "run": {"until_ns": 300_000_000_000},
        "engines": {"weather": {"plugin": "environment", "config": {
            "profiles": {"world": {"lowalt.wind": {
                "mode": "constant", "value": [5, 0, 0],
            }}},
        }}},
    }
    (directory / "scenario.json").write_text(json.dumps(document))


def test_connection_masking_and_session_wire_shape(api: dict[str, Any]) -> None:
    client = api["client"]
    assert not client.get("/v1/assistant/connection").json()["configured"]
    response = client.post("/v1/assistant/connection", json={"base_url": "http://localhost/v1", "model": "scripted", "api_key": "PRIVATE_KEY"})
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert "PRIVATE_KEY" not in response.text
    session = client.post("/v1/assistant/sessions", json={"locale": "zh-CN"}).json()
    assert session["plan"] is None and session["events"] == []
    assert session["connection"] == {"configured": True, "model": "scripted", "status": "ok"}
    assert "transcript" not in session and "PRIVATE_KEY" not in json.dumps(session)
    assert client.get("/v1/assistant/sessions").json()[0]["id"] == session["id"]
    assert client.post("/v1/assistant/sessions", json={"unexpected": True}).status_code == 422
    assert client.get("/v1/assistant/sessions/missing").status_code == 404


def test_busy_409_cancel_and_event_poll(api: dict[str, Any]) -> None:
    api["connection"].set("http://localhost/v1", "scripted")
    client, provider = api["client"], api["provider"]
    identifier = client.post("/v1/assistant/sessions", json={}).json()["id"]
    provider.block = True
    response = client.post(f"/v1/assistant/sessions/{identifier}/messages", json={"text": "Draft a scene"})
    assert response.status_code == 202 and provider.started.wait(1)
    assert client.post(f"/v1/assistant/sessions/{identifier}/messages", json={"text": "Again"}).status_code == 409
    assert client.post(f"/v1/assistant/sessions/{identifier}/cancel", json={}).json() == {"cancelled": True}
    provider.release.set()
    after = response.json()["seq"]
    events = []
    for _ in range(4):
        polled = client.get(f"/v1/assistant/sessions/{identifier}/events?after={after}&wait=1").json()
        events.extend(polled["events"])
        after = polled["last_seq"]
        if not polled["busy"]:
            break
    assert any(e["kind"] == "turn_finished" for e in events)
    assert not api["sessions"].get(identifier)["busy"]
    assert client.get(f"/v1/assistant/sessions/{identifier}/events?wait=26").status_code == 422


def test_only_confirm_writes_and_duplicate_confirm_is_idempotent(api: dict[str, Any]) -> None:
    client = api["client"]
    identifier = client.post("/v1/assistant/sessions", json={}).json()["id"]
    result = draft(api, identifier)
    assert api["validated"].wait(1)
    assert api["applied"] == []
    plan = client.get(f"/v1/assistant/sessions/{identifier}/plans/{result['version']}").json()
    assert plan["validation"]["state"] == "valid" and "_document" not in plan
    preview = client.get(f"/v1/assistant/sessions/{identifier}/plans/1/scenario").json()
    assert preview == {"scenario": {"id": "fixture", "entities": []}, "name": "Fixture / 测试", "fixture_digests": {}}
    assert api["applied"] == []
    confirmed = client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": 1})
    assert confirmed.status_code == 200 and len(api["applied"]) == 1
    assert confirmed.json()["workspace_id"] == "fixture-project"
    again = client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": 1})
    assert again.json() == confirmed.json() and len(api["applied"]) == 1
    session = client.get(f"/v1/assistant/sessions/{identifier}").json()
    assert session["workspace_id"] == "fixture-project"
    assert [e["kind"] for e in session["events"]][-2:] == ["applied", "notice"]
    names = {tool["function"]["name"] for tool in tool_schemas()}
    assert not names & {"apply", "apply_plan", "initialize", "start", "stop", "operate"}


def test_apply_pending_invalid_and_conflict(api: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from aeroagentsim.assistant import compiler

    client = api["client"]
    identifier = client.post("/v1/assistant/sessions", json={}).json()["id"]
    draft(api, identifier)
    assert api["validated"].wait(1)
    with api["sessions"].edit(identifier) as session:
        session["plans"]["1"]["validation"] = {"state": "pending", "errors": []}
    assert client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": 1}).status_code == 409
    api["sessions"].validated(identifier, 1, False, ["Invalid / 无效"])
    assert client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": 1}).status_code == 422
    api["sessions"].validated(identifier, 1, True, [])

    def conflict(*args: Any, **kwargs: Any) -> Any:
        raise compiler.PlanConflict("Draft changed / 草稿已变化")

    monkeypatch.setattr(compiler, "apply_plan", conflict)
    assert client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": 1}).status_code == 409
    assert api["applied"] == [] and api["sessions"].get(identifier)["workspace_id"] is None


def test_story_reads_applied_version_and_recorded_run(api: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from aeroagentsim.assistant import story

    client = api["client"]
    identifier = client.post("/v1/assistant/sessions", json={}).json()["id"]
    draft(api, identifier)
    assert api["validated"].wait(1)
    client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": 1})
    run_id = "run-" + "a" * 32
    directory = api["root"] / run_id
    directory.mkdir()
    pin_scenario(directory)
    (directory / "manifest.json").write_text(json.dumps({"id": run_id, "studio_workspace": "fixture-project", "workspace_version": 1, "status": "completed"}))
    called: list[Any] = []

    def recorded(run_dir: Path, expectations: Any, *, locale: str) -> dict[str, Any]:
        called.append((run_dir, expectations, locale))
        return {"steps": [{"status": "not_happened", "reason": "Recorded decline / 已记录的拒绝"}]}

    monkeypatch.setattr(story, "run_story", recorded)
    monkeypatch.setattr(story, "pinned_expectations", lambda run_dir: [{"schema": "fixture.incident"}])
    response = client.get(f"/v1/assistant/sessions/{identifier}/story/{run_id}")
    assert response.status_code == 200 and response.json()["plan_version"] == 1
    assert response.json()["wind"]["speed_mps"] == 5
    assert response.json()["steps"][0]["status"] == "not_happened"
    assert called == [(directory, [{"schema": "fixture.incident"}], "en")]
    localized = client.get(f"/v1/assistant/sessions/{identifier}/story/{run_id}?locale=zh-CN")
    assert localized.status_code == 200
    assert called[-1] == (directory, [{"schema": "fixture.incident"}], "zh-CN")
    assert api["sessions"].get(identifier)["locale"] == "en"
    assert client.get(f"/v1/assistant/sessions/{identifier}/story/{run_id}?locale=").status_code == 422
    (directory / "manifest.json").write_text(json.dumps({"id": run_id, "studio_workspace": "other", "workspace_version": 1, "status": "completed"}))
    assert client.get(f"/v1/assistant/sessions/{identifier}/story/{run_id}").status_code == 404


def test_message_accepts_explicit_run_and_locale_and_rejects_conflicts(api, monkeypatch):
    client = api["client"]
    identifier = client.post("/v1/assistant/sessions", json={"workspace_id": "fixture-project", "locale": "zh-CN"}).json()["id"]
    submitted = []
    def capture(session, text, context):
        submitted.append((session, text, context))
        return {"turn": len(submitted), "seq": 1}
    monkeypatch.setattr(api["harness"], "submit", capture)
    endpoint = f"/v1/assistant/sessions/{identifier}/messages"
    text = "Summarise what happened in this run compared with the plan."
    response = client.post(endpoint, json={"text": text, "run_id": "run-explicit", "locale": "en", "context": {"selected_entity": "uav.bravo"}})
    assert response.status_code == 202, response.text
    assert submitted[-1] == (identifier, text, {"run_id": "run-explicit", "locale": "en", "selected_entity": "uav.bravo"})
    assert client.post(endpoint, json={"text": text, "context": {"run_id": "run-explicit", "locale": "en"}}).status_code == 202
    assert submitted[-1][2] == {"run_id": "run-explicit", "locale": "en"}
    for body in ({"text": text, "run_id": "run-explicit", "context": {"run_id": "run-other"}},
                 {"text": text, "locale": "en", "context": {"locale": "zh-CN"}}, {"text": text, "run_id": None}, {"text": text, "locale": ""}):
        assert client.post(endpoint, json=body).status_code == 422
    assert len(submitted) == 2
    assert "run_id/locale" in client.get("/openapi.json").json()["paths"]["/v1/assistant/sessions/{identifier}/messages"]["post"]["description"]


def test_unbound_session_can_read_explicit_completed_form_run(api, monkeypatch):
    from aeroagentsim.assistant import story

    identifier = api["client"].post("/v1/assistant/sessions", json={"locale": "zh-CN"}).json()["id"]
    run_id = "run-" + "c" * 32
    directory = api["root"] / run_id
    directory.mkdir()
    pin_scenario(directory)
    (directory / "manifest.json").write_text(json.dumps({"id": run_id, "studio_workspace": "fixture-project", "workspace_version": 2, "status": "completed"}))
    calls = []
    monkeypatch.setattr(story, "pinned_expectations", lambda path: [{"id": "pinned-version-2"}])
    def recorded(path, expectations, *, locale):
        calls.append((path, expectations, locale))
        return {"locale": locale, "coverage": {"recorded_events": 9211}, "expectations": [], "events": []}
    monkeypatch.setattr(story, "run_story", recorded)
    response = api["client"].get(f"/v1/assistant/sessions/{identifier}/story/{run_id}?locale=en")
    assert response.status_code == 200, response.text
    assert response.json()["wind"]["profiles"]["weather.world.lowalt.wind"]["vectors"] == [
        {"east_mps": 5, "north_mps": 0, "up_mps": 0, "direction_deg": 0},
    ]
    assert response.json()["status"] == "completed" and response.json()["workspace_version"] == 2 and response.json()["plan_version"] is None
    assert response.json()["coverage"]["recorded_events"] == 9211 and response.json()["locale"] == "en"
    assert calls == [(directory, [{"id": "pinned-version-2"}], "en")]
