"""JSON assistant HTTP surface; Confirm is the sole workspace writer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, FastAPI, HTTPException, Query

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceConflict

from .connection import ConnectionStore, now
from .harness import Harness
from .sessions import SessionBusy, SessionStore
from .tools import STRING, ToolRunner, object_schema, validate_arguments

T = TypeVar("T")


def checked(action: Callable[[], T]) -> T:
    from .compiler import PlanConflict

    try:
        return action()
    except (SessionBusy, PlanConflict, WorkspaceConflict) as error:
        raise HTTPException(409, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise HTTPException(422, str(error)) from error


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def session_data(sessions: SessionStore, connection: ConnectionStore, identifier: str) -> dict[str, Any]:
    session = sessions.get(identifier)
    plan = session["plans"].get(str(session["plan_version"]))
    settings = connection.get()
    return {
        **{k: session[k] for k in ("id", "created_at", "updated_at", "workspace_id", "title", "locale", "busy", "last_seq", "events", "alternatives", "applied")},
        "plan": public_plan(plan) if plan else None,
        "plan_versions": [{"version": p["version"], "note": p["note"], "variant": p["variant"], "parent": p["parent"], "created_at": p["created_at"], "compile_ok": p["compile"]["ok"], "validation_state": p["validation"]["state"]} for p in session["plans"].values()],
        "connection": {k: settings[k] for k in ("configured", "model", "status")},
    }


def create_router(store: Any, *, run_root: Path, templates: Any, connection: ConnectionStore | None = None, sessions: SessionStore | None = None, harness: Harness | None = None) -> APIRouter:
    connection = ConnectionStore(run_root) if connection is None else connection
    sessions = SessionStore(run_root) if sessions is None else sessions
    runner = ToolRunner(store, templates, sessions, run_root, agent_profile="assistant" if connection.get()["configured"] else None)
    harness = Harness(sessions, connection, runner) if harness is None else harness
    router = APIRouter(prefix="/v1/assistant", tags=["assistant"])

    @router.get("/connection")
    def get_connection() -> dict[str, Any]:
        return checked(connection.get)

    @router.post("/connection")
    def set_connection(body: dict[str, Any]) -> dict[str, Any]:
        def configure() -> dict[str, Any]:
            validate_arguments(object_schema({"base_url": STRING, "model": STRING, "api_key": STRING, "clear_key": {"type": "boolean"}}, ("base_url", "model")), body)
            connection.set(**body)
            harness.tools.agent_profile = "assistant"
            return connection.test()
        return checked(configure)

    @router.post("/connection/test")
    def test_connection(body: dict[str, Any]) -> dict[str, Any]:
        def test() -> dict[str, Any]:
            validate_arguments(object_schema({}), body)
            return connection.test()
        return checked(test)

    @router.post("/sessions")
    def create_session(body: dict[str, Any]) -> dict[str, Any]:
        def create() -> dict[str, Any]:
            validate_arguments(object_schema({"workspace_id": {"anyOf": [STRING, {"type": "null"}]}, "locale": {"type": "string", "minLength": 1, "maxLength": 32}}), body)
            if body.get("workspace_id") is not None:
                store.get(body["workspace_id"])
            session = sessions.create(**body)
            return session_data(sessions, connection, session["id"])
        return checked(create)

    @router.get("/sessions")
    def list_sessions(workspace_id: str | None = None) -> list[dict[str, Any]]:
        return checked(lambda: sessions.list(workspace_id))

    @router.get("/sessions/{identifier}")
    def get_session(identifier: str) -> dict[str, Any]:
        return checked(lambda: session_data(sessions, connection, identifier))

    @router.post("/sessions/{identifier}/messages", status_code=202)
    def message(identifier: str, body: dict[str, Any]) -> dict[str, int]:
        def submit() -> dict[str, int]:
            validate_arguments(object_schema({"text": {"type": "string", "minLength": 1, "maxLength": 16000}, "context": object_schema({"selected_entity": STRING, "run_id": STRING, "locale": {"type": "string", "minLength": 1, "maxLength": 32}})}, ("text",)), body)
            harness.tools.agent_profile = "assistant" if connection.get()["configured"] else None
            return harness.submit(identifier, body["text"], body.get("context"))
        return checked(submit)

    @router.get("/sessions/{identifier}/events")
    def events(identifier: str, after: int = Query(0, ge=0), wait: float = Query(0, ge=0, le=25)) -> dict[str, Any]:
        return checked(lambda: sessions.events(identifier, after, wait))

    @router.post("/sessions/{identifier}/cancel")
    def cancel(identifier: str, body: dict[str, Any]) -> dict[str, bool]:
        def cancel_turn() -> dict[str, bool]:
            validate_arguments(object_schema({}), body)
            return {"cancelled": sessions.cancel(identifier)}
        return checked(cancel_turn)

    @router.get("/sessions/{identifier}/plans/{version}")
    def get_plan(identifier: str, version: int) -> dict[str, Any]:
        return checked(lambda: public_plan(sessions.plan(identifier, version)))

    @router.get("/sessions/{identifier}/plans/{version}/scenario")
    def scenario(identifier: str, version: int) -> dict[str, Any]:
        def preview() -> dict[str, Any]:
            plan = sessions.plan(identifier, version)
            if not plan["compile"]["ok"]:
                raise ValueError("Cannot preview a plan that did not compile / 无法预览未编译成功的方案")
            result = {"scenario": plan["_document"], "name": plan["title"]}
            if "demo_console" in plan["_compile_data"]:
                result["demo_console"] = plan["_compile_data"]["demo_console"]
            return result
        return checked(preview)

    @router.post("/sessions/{identifier}/plans/{version}/choose")
    def choose(identifier: str, version: int, body: dict[str, Any]) -> dict[str, Any]:
        def select() -> dict[str, Any]:
            validate_arguments(object_schema({}), body)
            sessions.choose(identifier, version)
            return session_data(sessions, connection, identifier)
        return checked(select)

    @router.post("/sessions/{identifier}/apply")
    def apply(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        from .compiler import apply_plan

        def confirm() -> dict[str, Any]:
            validate_arguments(object_schema({"plan_version": {"type": "integer", "minimum": 1}}, ("plan_version",)), body)
            with sessions.edit(identifier) as session:
                if session["busy"]:
                    raise SessionBusy("Wait for the current turn before confirming / 请等待当前对话完成再确认")
                version = body["plan_version"]
                plan = session["plans"].get(str(version))
                if plan is None:
                    raise FileNotFoundError("Plan not found / 方案不存在")
                if version != session["plan_version"]:
                    raise SessionBusy("Choose this plan before confirming / 请先选择此方案再确认")
                if not plan["compile"]["ok"] or plan["validation"]["state"] == "invalid":
                    raise ValueError("This plan is invalid; fix the reported errors / 方案无效；请修复报告的错误")
                if plan["validation"]["state"] != "valid":
                    raise SessionBusy("Plan validation is still pending / 方案仍在等待校验")
                prior = next((record for record in session["applied"] if record["plan_version"] == version), None)
                if prior is not None:
                    return prior
                if session["workspace_id"] is not None and plan["base"].get("workspace") != session["workspace_id"]:
                    raise SessionBusy("Draft a revision against the bound project before confirming / 请基于已绑定项目提出修改后再确认")
                result = apply_plan(harness.tools.context(identifier), sessions.plan_directory(identifier, version), base=plan["base"], workspace_id=session["workspace_id"], name=plan["title"], note=plan["note"])
                record = {"plan_version": version, **{k: result[k] for k in ("workspace_id", "version", "revision", "report")}, "applied_at": now()}
                session["applied"].append(record)
                session["workspace_id"] = record["workspace_id"]
                sessions._event(session, "applied", **{k: record[k] for k in ("plan_version", "workspace_id", "version", "report")})
                sessions._event(session, "notice", text=f"Configured scene · version {record['version']} · not running / 场景已配置 · 版本 {record['version']} · 尚未运行", report=record["report"])
            return record
        return checked(confirm)

    @router.get("/sessions/{identifier}/story/{run_id}")
    def story(identifier: str, run_id: str) -> dict[str, Any]:
        return checked(lambda: harness.tools.run_story(identifier, run_id))

    return router


def mount_assistant(app: FastAPI, *, run_root: Path) -> Harness:
    existing = getattr(app.state, "assistant", None)
    if existing is not None:
        return existing
    connection = ConnectionStore(run_root)
    sessions = SessionStore(run_root)
    templates = getattr(app.state, "templates", None)
    if templates is None:
        templates = TemplateRegistry(None)
    runner = ToolRunner(app.state.studio, templates, sessions, run_root, agent_profile="assistant" if connection.get()["configured"] else None)
    harness = Harness(sessions, connection, runner)
    app.include_router(create_router(app.state.studio, run_root=run_root, templates=templates, connection=connection, sessions=sessions, harness=harness))
    app.state.assistant = harness
    app.state.assistant_sessions = sessions
    app.state.assistant_connection = connection
    return harness
