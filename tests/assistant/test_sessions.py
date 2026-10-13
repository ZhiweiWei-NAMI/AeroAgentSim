"""Session persistence and version semantics, with compilation replaced by a fixture."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aeroagentsim.assistant.sessions import SessionBusy, SessionStore
from aeroagentsim.assistant.tools import ToolRunner


def test_persistence_events_and_restart_interruption(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    identifier = store.create(locale="zh-CN")["id"]
    turn = store.begin_turn(identifier, "场景建议")
    store.append(identifier, {"role": "assistant", "content": None, "tool_calls": [{"id": "pending", "function": {"name": "find_places", "arguments": "{}"}}]})
    restarted = SessionStore(tmp_path)
    session = restarted.get(identifier)
    assert not session["busy"]
    assert session["transcript"][-1]["role"] == "tool"
    assert session["transcript"][-1]["tool_call_id"] == "pending"
    assert any(e["kind"] == "notice" for e in session["events"])
    assert [e["seq"] for e in session["events"]] == list(range(1, session["last_seq"] + 1))
    assert turn["turn"] == 1 and session["locale"] == "zh-CN"
    with pytest.raises(FileNotFoundError):
        restarted.get("../escape")


def test_long_poll_wakes_on_event_and_cancel_keeps_busy_until_finished(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    identifier = store.create()["id"]
    started = store.begin_turn(identifier, "plan")
    ready = threading.Event()
    result: list[dict[str, Any]] = []

    def poll() -> None:
        ready.set()
        result.append(store.events(identifier, started["seq"], 1))

    thread = threading.Thread(target=poll)
    thread.start()
    assert ready.wait(1)
    store.emit(identifier, "notice", text="done / 完成")
    thread.join(2)
    assert not thread.is_alive() and result[0]["events"][0]["kind"] == "notice"
    assert store.cancel(identifier)
    assert store.cancelled(identifier, started["turn"])
    with pytest.raises(SessionBusy):
        store.begin_turn(identifier, "second")
    store.finish_turn(identifier, started["turn"], elapsed_s=0.1, model_calls=1)
    assert not store.cancel(identifier) and not store.get(identifier)["busy"]


def test_failed_edit_is_atomic_and_ids_are_never_reused(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    identifier = store.create()["id"]
    before = store.get(identifier)
    with pytest.raises(ValueError):
        with store.edit(identifier) as session:
            session["title"] = "bad"
            raise ValueError("abort")
    assert store.get(identifier) == before
    v1, first = store.allocate(identifier, [{"op": "set_duration", "params": {"seconds": 300}}])
    v2, second = store.allocate(identifier, [{"op": "set_duration", "params": {"seconds": 500}}])
    assert (v1, v2) == (1, 2) and [first[0]["id"], second[0]["id"]] == ["op-1", "op-2"]


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ToolRunner, str]:
    from aeroagentsim.assistant import compiler

    sessions = SessionStore(tmp_path)
    identifier = sessions.create()["id"]
    tools = ToolRunner(SimpleNamespace(), SimpleNamespace(), sessions, tmp_path)
    monkeypatch.setattr(tools, "context", lambda identifier: None)
    monkeypatch.setattr(compiler, "resolve_base", lambda ctx, base: SimpleNamespace(label="Fixture / 测试", revision=None))

    def compile(ctx: Any, base: Any, operations: Any, directory: Path, *, resolved: Any = None) -> Any:
        directory.mkdir(parents=True)
        document = {"id": "test", "entities": [{"id": op["id"], "parameters": op["params"]} for op in operations]}
        data = dict(ok=True, errors=[], warnings=[], document=document, digest="fixture", summary={"fleets": [], "places": []}, diff_base={}, interactions=[], expectations=[{"schema": "fixture.event"}])
        return SimpleNamespace(**data, to_data=lambda: copy.deepcopy(data))

    monkeypatch.setattr(compiler, "compile_plan", compile)
    return tools, identifier


def initial(tools: ToolRunner, identifier: str) -> None:
    tools.draft_plan(identifier, base={"template": "fixture"}, title="Fixture / 测试", summary="Fixture / 测试", note="Initial / 初始", operations=[{"op": "set_duration", "params": {"seconds": 300}}, {"op": "set_wind", "params": {"speed_mps": 2}}], assumptions=["Initial / 初始"], open_questions=[])


def test_revise_restore_variants_choose_and_expectations(runner: tuple[ToolRunner, str]) -> None:
    tools, identifier = runner
    initial(tools, identifier)
    first = tools.sessions.plan(identifier)
    updated = copy.deepcopy(first["operations"][0])
    updated["params"]["seconds"] = 500
    tools.revise_plan(identifier, note="Later / 更晚", update=[updated], add=[{"op": "set_run_mode", "params": {"mode": "scheduled"}}])
    second = tools.sessions.plan(identifier)
    assert second["operations"][1] == first["operations"][1]
    assert second["operations"][2]["id"] == "op-3"
    tools.restore_operations(identifier, 1, "Restore duration / 恢复时长", ["op-1"])
    restored = tools.sessions.plan(identifier)
    assert restored["operations"][0] == first["operations"][0]
    assert restored["operations"][2] == second["operations"][2]
    result = tools.propose_alternatives(identifier, [{"label": label, "title": label, "summary": label, "changes": {"note": label, "update": [{"id": "op-1", "op": "set_duration", "params": {"seconds": seconds}}]}, "assumptions": []} for label, seconds in (("A", 400), ("B", 600))])
    assert tools.sessions.plan(identifier)["version"] == restored["version"]
    assert len(tools.sessions.get(identifier)["alternatives"]) == 2
    selected = result["variants"][1]["version"]
    tools.sessions.choose(identifier, selected)
    assert tools.sessions.plan(identifier)["operations"][0]["params"]["seconds"] == 600
    record = {"plan_version": selected, "workspace_id": "fixture-project", "version": 7, "revision": 8, "report": {"created": []}, "applied_at": "fixture"}
    tools.sessions.applied(identifier, record)
    assert tools.sessions.expectations_for("fixture-project", 7) == [{"schema": "fixture.event"}]
    assert tools.sessions.expectations_for("fixture-project", 6) is None
    restarted = SessionStore(tools.run_root)
    assert restarted.plan(identifier)["version"] == selected
    assert restarted.get(identifier)["workspace_id"] == "fixture-project"


def test_compile_failure_is_an_invalid_version(runner: tuple[ToolRunner, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from aeroagentsim.assistant import compiler

    tools, identifier = runner
    initial(tools, identifier)
    data = dict(ok=False, errors=[{"op_id": "op-1", "field": "seconds", "message": "bad"}], warnings=[], document=None, digest=None, summary=None, diff_base={}, interactions=[], expectations=[])
    monkeypatch.setattr(compiler, "compile_plan", lambda *args, **kwargs: SimpleNamespace(**data, to_data=lambda: data))
    result = tools.revise_plan(identifier, note="Invalid / 无效", update=[{"id": "op-1", "op": "set_duration", "params": {"seconds": -1}}])
    assert result["ok"] is False and result["errors"][0]["op_id"] == "op-1"
    assert tools.sessions.plan(identifier)["validation"]["state"] == "invalid"
    assert tools.sessions.plan(identifier, 1)["compile"]["ok"]
