"""Atomic session histories, immutable plan versions and long-poll notifications."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from .connection import now

TextSource = Literal["model", "platform", "unknown"]
TurnStatus = Literal["running", "completed", "budget_exhausted", "provider_error", "error", "cancelled", "interrupted", "unknown"]


def text_provenance(record: dict[str, Any], text: str, *, default: TextSource = "unknown") -> None:
    """Missing authorship is unknown, including on older records."""
    sources = {"model", "platform", "unknown"}
    parts = record.get("parts")
    if parts is None:
        source = record.get("source", default)
        source = source if source in sources else "unknown"
        parts = [{"text": text, "source": source}] if text else []
    else:
        parts = [{"text": part["text"], "source": part.get("source") if part.get("source") in sources else "unknown"} for part in parts]
    record["parts"] = parts
    authors = {part["source"] for part in parts}
    if len(authors) == 1:
        record["source"] = next(iter(authors))
    elif authors:
        record.pop("source", None)
    else:
        record.setdefault("source", default)


def restore_provenance(session: dict[str, Any]) -> None:
    session.setdefault("turns", [])
    for event in session["events"]:
        if event["kind"] in {"assistant", "notice", "error"}:
            text_provenance(event, event.get("text", event.get("message", "")))
        if event["kind"] in {"assistant", "turn_finished"}:
            event.setdefault("status", "unknown")
    for message in session["transcript"]:
        if message.get("role") == "assistant":
            text_provenance(message, message.get("content") or "")
    for turn in session["turns"]:
        text_provenance(turn, "")
        turn.setdefault("status", "unknown")


class SessionBusy(ValueError):
    pass


class SessionStore:
    def __init__(self, run_root: Path) -> None:
        self.root = run_root / "assistant" / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._cancelled: set[tuple[str, int]] = set()
        for path in sorted(self.root.glob("*/session.json")):
            session = json.loads(path.read_text(encoding="utf-8"))
            self.directory(session["id"])
            restore_provenance(session)
            self._sessions[session["id"]] = session
            if session["busy"]:
                self._event(session, "notice", status="interrupted", text="The previous turn was interrupted by a service restart / 上一轮对话因服务重启中断")
                session["busy"] = False
                for turn in session["turns"]:
                    if turn["turn"] == session["turn"]:
                        turn["status"] = "interrupted"
                self._event(session, "turn_finished", turn=session["turn"], status="interrupted")
                answered = {m.get("tool_call_id") for m in session["transcript"] if m.get("role") == "tool"}
                for message in list(session["transcript"]):
                    for call in message.get("tool_calls", []):
                        if call["id"] not in answered:
                            session["transcript"].append({"role": "tool", "tool_call_id": call["id"], "content": '{"error":"Service restarted / 服务已重启"}'})
                with self.condition:
                    self._save(session)

    def directory(self, identifier: str) -> Path:
        if not re.fullmatch(r"assist-[a-f0-9]{32}", identifier):
            raise FileNotFoundError("Session not found / 会话不存在")
        return self.root / identifier

    def plan_directory(self, identifier: str, version: int) -> Path:
        if type(version) is not int or version < 1:
            raise ValueError("Plan version must be a positive integer / 方案版本必须为正整数")
        return self.directory(identifier) / "plans" / f"v{version}"

    def _save(self, session: dict[str, Any]) -> None:
        directory = self.directory(session["id"])
        directory.mkdir(parents=True, exist_ok=True)
        pending = directory / "session.pending.json"
        pending.write_text(json.dumps(session, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        pending.replace(directory / "session.json")
        self.condition.notify_all()

    def _read(self, identifier: str) -> dict[str, Any]:
        self.directory(identifier)
        if identifier not in self._sessions:
            raise FileNotFoundError("Session not found / 会话不存在")
        return self._sessions[identifier]

    @contextmanager
    def edit(self, identifier: str) -> Iterator[dict[str, Any]]:
        with self.condition:
            session = copy.deepcopy(self._read(identifier))
            yield session
            session["updated_at"] = now()
            self._save(session)
            self._sessions[identifier] = session

    def create(self, workspace_id: str | None = None, locale: str = "en") -> dict[str, Any]:
        if not isinstance(locale, str) or not locale.strip() or len(locale) > 32:
            raise ValueError("A short locale string is required / 需要简短的语言标识")
        session = {
            "id": "assist-" + uuid.uuid4().hex, "created_at": now(), "updated_at": now(),
            "workspace_id": workspace_id, "title": "New scenario / 新场景", "locale": locale,
            "transcript": [], "events": [], "turns": [], "last_seq": 0, "plans": {},
            "plan_version": None, "alternatives": [], "applied": [], "busy": False,
            "turn": 0, "next_op": 1, "next_version": 1,
        }
        with self.condition:
            self._save(session)
            self._sessions[session["id"]] = session
        return copy.deepcopy(session)

    def get(self, identifier: str) -> dict[str, Any]:
        with self.lock:
            session = copy.deepcopy(self._read(identifier))
            restore_provenance(session)
            return session

    def list(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {"id": s["id"], "title": s["title"], "workspace_id": s["workspace_id"],
                 "updated_at": s["updated_at"], "plan_version": s["plan_version"],
                 "applied_versions": [r["version"] for r in s["applied"]]}
                for s in sorted(self._sessions.values(), key=lambda s: s["updated_at"], reverse=True)
                if workspace_id is None or s["workspace_id"] == workspace_id
            ]

    @staticmethod
    def _event(session: dict[str, Any], kind: str, **data: Any) -> dict[str, Any]:
        session["last_seq"] += 1
        event = {"seq": session["last_seq"], "at": now(), "kind": kind, **data}
        if kind in {"assistant", "notice", "error"}:
            text_provenance(event, event.get("text", event.get("message", "")), default="platform" if kind != "assistant" else "unknown")
            if session["busy"]:
                event["turn"] = session["turn"]
                for turn in session.get("turns", []):
                    if turn["turn"] == session["turn"]:
                        turn["parts"].extend(copy.deepcopy(event["parts"]))
                        text_provenance(turn, "")
        session["events"].append(event)
        return event

    def emit(self, identifier: str, kind: str, **data: Any) -> dict[str, Any]:
        with self.edit(identifier) as session:
            event = self._event(session, kind, **data)
        return copy.deepcopy(event)

    def append(self, identifier: str, message: dict[str, Any]) -> None:
        message = copy.deepcopy(message)
        if message.get("role") == "assistant":
            text_provenance(message, message.get("content") or "")
        with self.edit(identifier) as session:
            session["transcript"].append(message)

    def record_model_reply(self, identifier: str, content: str | None) -> None:
        """Keep only final message text, never a provider envelope or connection data."""
        with self.edit(identifier) as session:
            for turn in session.get("turns", []):
                if turn["turn"] == session["turn"]:
                    turn["model_reply"] = content

    def answer(self, identifier: str, parts: list[dict[str, str]], suggestions: list[str], *,
               model_reply: str | None, status: TurnStatus) -> None:
        text = "\n\n".join(part["text"] for part in parts)
        message: dict[str, Any] = {"role": "assistant", "content": text + ("\nSuggestions: " + " | ".join(suggestions) if suggestions else ""),
                                   "parts": copy.deepcopy(parts), "model_reply": model_reply, "status": status}
        text_provenance(message, text)
        with self.edit(identifier) as session:
            message["turn"] = session["turn"]
            session["transcript"].append(message)
            self._event(session, "assistant", text=text, parts=parts, suggestions=suggestions, status=status)

    def begin_turn(self, identifier: str, text: str, context: dict[str, Any] | None = None) -> dict[str, int]:
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError("Message must contain 1–16000 characters / 消息需包含 1–16000 个字符")
        with self.edit(identifier) as session:
            if session["busy"]:
                raise SessionBusy("A turn is already running / 已有一轮对话正在处理")
            session["busy"] = True
            session["turn"] += 1
            session.setdefault("turns", []).append({"turn": session["turn"], "status": "running", "model_reply": None, "parts": []})
            if context and context.get("locale"):
                session["locale"] = context["locale"]
            self._event(session, "user", text=text, **({"context": context} if context else {}))
            session["transcript"].append({"role": "user", "content": text})
            self._event(session, "turn_started", turn=session["turn"])
            result = {"turn": session["turn"], "seq": session["last_seq"]}
        return result

    def finish_turn(self, identifier: str, turn: int, *, status: TurnStatus = "unknown", **statistics: Any) -> None:
        statistics = {key: statistics[key] for key in ("elapsed_s", "model_calls", "tokens") if key in statistics}
        with self.edit(identifier) as session:
            if session["turn"] == turn:
                session["busy"] = False
            for record in session.get("turns", []):
                if record["turn"] == turn:
                    record.update(status=status, **statistics)
            self._event(session, "turn_finished", turn=turn, status=status, **statistics)
        with self.lock:
            self._cancelled.discard((identifier, turn))

    def cancel(self, identifier: str) -> bool:
        with self.lock:
            session = self._read(identifier)
            if session["busy"]:
                self._cancelled.add((identifier, session["turn"]))
            return session["busy"]

    def cancelled(self, identifier: str, turn: int) -> bool:
        with self.lock:
            return (identifier, turn) in self._cancelled

    def events(self, identifier: str, after: int = 0, wait: float = 0) -> dict[str, Any]:
        if type(after) is not int or after < 0 or not 0 <= wait <= 25:
            raise ValueError("Invalid event cursor or wait (0–25 s) / 事件游标或等待时间无效（0–25 秒）")
        deadline = time.monotonic() + wait
        with self.condition:
            session = self._read(identifier)
            while session["last_seq"] <= after and time.monotonic() < deadline:
                self.condition.wait(deadline - time.monotonic())
                session = self._read(identifier)
            events = copy.deepcopy([e for e in session["events"] if e["seq"] > after])
            for event in events:
                if event["kind"] in {"assistant", "notice", "error"}:
                    text_provenance(event, event.get("text", event.get("message", "")))
                if event["kind"] in {"assistant", "turn_finished"}:
                    event.setdefault("status", "unknown")
            return {"events": events, "last_seq": session["last_seq"], "busy": session["busy"]}

    def allocate(self, identifier: str, operations: list[dict[str, Any]], *, retained: set[str] | None = None) -> tuple[int, list[dict[str, Any]]]:
        operations = copy.deepcopy(operations)
        retained = set() if retained is None else retained
        with self.edit(identifier) as session:
            seen: set[str] = set()
            for op in operations:
                identifier_op = op.get("id")
                if identifier_op not in retained:
                    identifier_op = f"op-{session['next_op']}"
                    session["next_op"] += 1
                    op["id"] = identifier_op
                if identifier_op in seen:
                    raise ValueError("Duplicate operation id / 操作标识重复")
                seen.add(identifier_op)
            version = session["next_version"]
            session["next_version"] += 1
        return version, operations

    def put_plan(self, identifier: str, plan: dict[str, Any], *, current: bool = True) -> None:
        with self.edit(identifier) as session:
            key = str(plan["version"])
            if key in session["plans"]:
                raise ValueError("Plan versions are immutable / 方案版本不可覆盖")
            session["plans"][key] = copy.deepcopy(plan)
            if current:
                session["plan_version"] = plan["version"]
                session["alternatives"] = []
                session["title"] = plan["title"]
            self._event(session, "plan", version=plan["version"], note=plan["note"], variant=plan["variant"])

    def plan(self, identifier: str, version: int | None = None) -> dict[str, Any]:
        with self.lock:
            session = self._read(identifier)
            selected = session["plan_version"] if version is None else version
            if str(selected) not in session["plans"]:
                raise FileNotFoundError("Plan not found / 方案不存在")
            return copy.deepcopy(session["plans"][str(selected)])

    def validated(self, identifier: str, version: int, valid: bool, errors: list[Any]) -> None:
        with self.edit(identifier) as session:
            session["plans"][str(version)]["validation"] = {"state": "valid" if valid else "invalid", "errors": copy.deepcopy(errors)}
            self._event(session, "plan_validated", version=version, valid=valid, errors=errors)

    def choose(self, identifier: str, version: int) -> None:
        with self.edit(identifier) as session:
            if session["busy"]:
                raise SessionBusy("Wait for the current turn / 请等待当前对话完成")
            plan = session["plans"].get(str(version))
            if plan is None:
                raise FileNotFoundError("Plan not found / 方案不存在")
            session["plan_version"] = version
            session["title"] = plan["title"]
            session["alternatives"] = []
            self._event(session, "plan", version=version, note="Selected plan / 已选择方案", variant=plan["variant"])

    def applied(self, identifier: str, record: dict[str, Any]) -> None:
        with self.edit(identifier) as session:
            session["applied"].append(copy.deepcopy(record))
            session["workspace_id"] = record["workspace_id"]
            self._event(session, "applied", **{k: record[k] for k in ("plan_version", "workspace_id", "version", "report")})
            self._event(session, "notice", text=f"Configured scene · version {record['version']} · not running / 场景已配置 · 版本 {record['version']} · 尚未运行", report=record["report"])

    def expectations_for(self, workspace_id: str, version: int) -> list[Any] | None:
        with self.lock:
            for session in self._sessions.values():
                for record in reversed(session["applied"]):
                    if record["workspace_id"] == workspace_id and record["version"] == version:
                        return copy.deepcopy(session["plans"][str(record["plan_version"])]["expectations"])
        return None
