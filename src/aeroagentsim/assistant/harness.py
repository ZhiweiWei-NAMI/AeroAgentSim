"""A bounded, cancellable provider loop with persistent UI and tool events."""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from typing import Any

from aeroagentsim.agents.provider import ProviderError
from aeroagentsim.agents.tools import strict_json

from .connection import ConnectionStore, provider_detail
from .prompts import SYSTEM_PROMPT, plan_status, state_message
from .sessions import SessionStore
from .tools import ToolRunner, compact_json, tool_label, tool_schemas

LOG = logging.getLogger(__name__)


def recent_history(transcript: list[dict[str, Any]], recent_turns: int = 8) -> list[dict[str, Any]]:
    starts = [i for i, m in enumerate(transcript) if m.get("role") == "user"]
    cut = starts[-recent_turns] if len(starts) > recent_turns else 0
    messages = []
    if cut:
        older = [{"role": m["role"], "text": str(m.get("content", ""))[:500]} for m in transcript[:cut] if m.get("role") == "user" or (m.get("role") == "assistant" and not m.get("tool_calls"))]
        messages.append({"role": "system", "content": "Earlier conversation summary (data): " + json.dumps(older[-12:], ensure_ascii=False)})
    for message in copy.deepcopy(transcript[cut:]):
        content = message.get("content")
        if isinstance(content, str) and len(content) > 6500:
            message["content"] = compact_json({"truncated": True, "preview": content[:5800]})
        messages.append(message)
    return messages


def split_suggestions(content: str) -> tuple[str, list[str]]:
    match = re.search(r"(?:^|\n)Suggestions:\s*([^\n]*)\s*$", content, re.IGNORECASE)
    if match is None:
        return content.strip(), []
    suggestions = [part.strip()[:120] for part in match.group(1).split("|") if part.strip()][:4]
    return content[:match.start()].strip(), suggestions


def run_evidence(tools: ToolRunner, identifier: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    """A question asked from a run carries that run's recorded story, so answers start from evidence, not recall."""
    run_id = (context or {}).get("run_id")
    if not isinstance(run_id, str):
        return None
    try:
        locale = (context or {}).get("locale")
        story = tools.run_story(identifier, run_id, **({"locale": locale} if locale is not None else {}))
    except (OSError, ValueError, KeyError) as error:
        return {"run_id": run_id, "unavailable": str(error)}
    return story_evidence(run_id, story)


def story_evidence(run_id: str, story: dict) -> dict:
    from .story import failure_summary

    return {"run_id": run_id, "status": story.get("status"), "terminal": story.get("terminal"),
            **{k: story[k] for k in ("assessment", "coverage", "workspace_version", "plan_version", "expectations_source", "detail", "details_url", "locale") if k in story},
            "failure_count": len(story.get("failures", [])), "failures": failure_summary(story.get("failures", [])),
            "expectations": [{k: row.get(k) for k in ("id", "text", "text_zh", "status", "at_ns", "progress")} | {"recorded": (row.get("explanation") or {}).get("text")} for row in story["expectations"]],
            "events": [{k: e[k] for k in ("schema", "text", "text_zh", "at_ns") if k in e} for e in story["events"][-12:]]}


class Harness:
    def __init__(self, sessions: SessionStore, connection: ConnectionStore, tools: ToolRunner, *, max_calls: int = 10) -> None:
        self.sessions, self.connection, self.tools, self.max_calls = sessions, connection, tools, max_calls
        self._validation_lock = threading.Lock()
        self._validating: set[tuple[str, int]] = set()
        self.tools.on_plan = self.validate_later
        for row in sessions.list():
            for plan in sessions.get(row["id"])["plans"].values():
                if plan["compile"]["ok"] and plan["validation"]["state"] == "pending":
                    self.validate_later(row["id"], plan["version"])

    def submit(self, identifier: str, text: str, context: dict[str, Any] | None = None) -> dict[str, int]:
        if not self.connection.get()["configured"]:
            raise ValueError("Connect a model before sending a message / 发送消息前请先连接模型")
        result = self.sessions.begin_turn(identifier, text, context)
        thread = threading.Thread(target=self.run_turn, args=(identifier, result["turn"], context), daemon=True, name=f"aas-assistant-{result['turn']}")
        try:
            thread.start()
        except RuntimeError:
            self.sessions.emit(identifier, "error", code="TURN_START", message="Could not start the conversation turn / 无法启动对话处理")
            self.sessions.finish_turn(identifier, result["turn"], elapsed_s=0.0, model_calls=0)
            raise
        return result

    def validate_later(self, identifier: str, version: int) -> None:
        key = (identifier, version)
        with self._validation_lock:
            if key in self._validating:
                return
            self._validating.add(key)

        def validate() -> None:
            try:
                from .compiler import validate_compiled

                result = validate_compiled(self.tools.context(identifier), self.sessions.plan_directory(identifier, version))
                if type(result["valid"]) is not bool:
                    raise ValueError("Validator must return a boolean")
                errors = result["errors"]
                if not result["valid"] and not errors:
                    errors = result["issues"]
                self.sessions.validated(identifier, version, result["valid"], errors)
            except Exception as error:
                LOG.error("Assistant validation failed (%s)", type(error).__name__)
                self.sessions.validated(identifier, version, False, [{"code": "VALIDATION_ERROR", "message": f"Validation could not finish ({type(error).__name__}) / 校验未能完成（{type(error).__name__}）"}])
            finally:
                with self._validation_lock:
                    self._validating.discard(key)

        threading.Thread(target=validate, daemon=True, name=f"aas-plan-validation-{version}").start()

    def _cancelled(self, identifier: str, turn: int) -> bool:
        if not self.sessions.cancelled(identifier, turn):
            return False
        self.sessions.emit(identifier, "notice", text="Turn cancelled; compiled proposals remain available / 对话已取消；已编译的方案仍可查看")
        return True

    def _grounded_reply(self, identifier: str, text: str, evidence: dict | None) -> str:
        """Fixed observation actions render recorded facts instead of historical model prose."""
        session = self.sessions.get(identifier)
        question = next((m["content"] for m in reversed(session["transcript"]) if m["role"] == "user"), "")
        zh = str(session["locale"]).startswith("zh")
        if evidence is not None and re.search(r"summari[sz]e|what is happening|总结|现在发生", question, re.I):
            details_url = evidence.get("details_url") or f"/v1/assistant/sessions/{identifier}/story/{evidence['run_id']}"
            if "unavailable" in evidence:
                return ((f"无法读取运行 {evidence['run_id']} 的记录：" if zh else f"Could not read the records for run {evidence['run_id']}: ") + evidence["unavailable"][:600] +
                        f"\n\n[{'查看记录' if zh else 'Recorded details'}]({details_url})")
            if evidence.get("coverage") is None:
                return text
            coverage = evidence["coverage"]
            rows = evidence.get("expectations", [])
            happened = sum(row["status"] == "happened" for row in rows)
            lines = [f"运行 {evidence['run_id']}（{evidence['status']}）记录了 {coverage['recorded_events']} 个事件。" if zh else f"Run {evidence['run_id']} ({evidence['status']}) recorded {coverage['recorded_events']} events."]
            if rows:
                lines.append(f"预期步骤已发生 {happened}/{len(rows)}；以各步骤证据为准。" if zh else f"{happened}/{len(rows)} expected steps were recorded; assess the evidence for each step.")
            else:
                lines.append("没有配置预期，不能据此判断全部成功。" if zh else "No expectations were configured; this does not establish success.")
            for row in rows[:10]:
                label = (row.get("text_zh") if zh else row["text"]) or row["text"]
                at = f" ({row['at_ns'] / 1e9:g} s)" if row.get("at_ns") is not None else ""
                status = ({"happened": "已记录", "missed": "未记录", "pending": "等待记录", "not_taken": "未走此分支"} if zh else
                          {"happened": "recorded", "missed": "not recorded", "pending": "pending", "not_taken": "branch not taken"})[row["status"]]
                lines.append(f"- {label[:240]}: {status}{at}")
            count = evidence.get("failure_count", len(evidence.get("failures", [])))
            if count:
                lines.append(f"记录到 {count} 条失败或冲突，按类型汇总：" if zh else f"{count} failure or conflict records, grouped by type:")
            for failure in evidence.get("failures", []):
                label = failure.get("text_zh") if zh else failure["text"]
                reason = failure.get("reason")
                lines.append(f"- {failure['last_at_ns'] / 1e9:g} s: {label or failure['text']} (×{failure['count']})" + (f" — {reason}" if reason else ""))
            details = f"\n\n[{'查看完整 Story 与事件证据' if zh else 'Full Story and event evidence'}]({details_url})"
            result = "\n\n".join(lines)
            if len(result) > 5500:
                result = result[:5200] + ("\n\n更多记录见详情。" if zh else "\n\nMore records are available in details.")
            return result + details
        if isinstance(self.tools, ToolRunner) and question.strip() in {"What should I watch for when I run it?", "运行时我应该关注什么？"}:
            from .scene import expectations

            document, scene = self.tools._scene(identifier)
            selected = session["plan_version"]
            saved = any(r["plan_version"] == selected for r in session["applied"])
            lines = [("按当前已保存场景观察：" if saved else "按当前选中方案观察：") if zh else ("Watch the current saved scene:" if saved else "Watch the selected proposal:")]
            total = sum(f["count"] for f in scene["fleets"] if f.get("vehicle_class") != "aas:TrafficRoadVehicle")
            lines.append(f"无人机总数：{total}。" if zh else f"Total UAVs: {total}.")
            for fleet in scene["fleets"]:
                lines.append(f"- {fleet['id']}: {fleet['count']} × {fleet['label_zh'] if zh else fleet['label']}")
            for place in scene["places"]:
                if place["role"] == "charging_pad":
                    lines.append(f"- {place['id']}: " + ("容量 " if zh else "capacity ") + str(place["details"]["lowalt.site_capacity"]) + ", ENU " + json.dumps(place["position"]))
            lines.extend("- " + row["text_zh" if zh else "text"] for row in expectations(document, scene)[:12])
            lines.append("这些步骤取决于运行时条件，完成情况以事件记录为准。" if zh else "These steps depend on runtime conditions; recorded events establish the outcome.")
            return "\n\n".join(lines)
        if isinstance(self.tools, ToolRunner) and re.search(r"saved draft|current position|changed.*Form|已保存|表单.*修改", question, re.I):
            # Reject an absence/deletion inference for a real participant named in the reply.
            current = self.tools.get_current_scene(identifier)
            identities = (current.get("scene") or {}).get("entity_ids", [])
            if re.search(r"deleted|removed|no longer exists|不存在|已删除|被删|已移除", text, re.I):
                named = [identity for identity in identities if identity in text or identity in question]
                if named:
                    facts = [self.tools.get_current_scene(identifier, id=identity)["participant"]["entity"] for identity in named]
                    return ("已保存草稿中仍存在这些对象：\n\n" if zh else "These participants exist in the saved draft:\n\n") + "\n\n".join(json.dumps(entity, ensure_ascii=False) for entity in facts)
        return text

    def _answer(self, identifier: str, message: dict[str, Any], evidence: dict | None = None) -> None:
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("PROTOCOL", "empty final completion")
        text, suggestions = split_suggestions(content)
        if not text:
            raise ProviderError("PROTOCOL", "empty final completion")
        grounded = self._grounded_reply(identifier, text, evidence)
        used_grounded_facts = grounded != text
        text = grounded
        session = self.sessions.get(identifier)
        plan = session["plans"].get(str(session["plan_version"]))
        if plan is not None and not (used_grounded_facts and evidence is not None):
            # Validation can finish while the provider is writing its completion.
            # Render status from the store, replacing the provider's status prose.
            if not used_grounded_facts and evidence is None:
                sentences = re.split(r"(?<=[。！？])|(?<=[.!?])\s+|\n", text)
                text = "\n".join(s.strip() for s in sentences if s.strip() and not re.search(r"\b(?:validat\w*|pending)\b|wait.*confirm|before confirming|校验|验证|等待.*确认", s, re.I))
            status = plan_status(session, plan, zh=str(session["locale"]).startswith("zh"))
            text = (text + "\n\n" if text else "") + status
        self.sessions.append(identifier, {"role": "assistant", "content": text + ("\nSuggestions: " + " | ".join(suggestions) if suggestions else "")})
        self.sessions.emit(identifier, "assistant", text=text, suggestions=suggestions)

    def _closing(self, identifier: str) -> str:
        """A grounded closing line from the session's current plan, for a turn the model did not close."""
        session = self.sessions.get(identifier)
        zh = str(session.get("locale", "")).startswith("zh")
        plan = session["plans"].get(str(session["plan_version"]))
        if plan is None:
            return "这一轮还没有形成方案，请再发一条消息继续。" if zh else "No proposal was finished in this turn; send another message to continue."
        return plan_status(session, plan, zh=zh)

    def run_turn(self, identifier: str, turn: int, context: dict[str, Any] | None = None) -> None:
        started = time.monotonic()
        model_calls, tokens, usage_seen = 0, 0, False
        try:
            session = self.sessions.get(identifier)
            scene = self.tools.get_current_scene(identifier)
            evidence = run_evidence(self.tools, identifier, context)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, state_message(session, scene, context, evidence)] + recent_history(session["transcript"])
            provider = self.connection.provider()
            schemas = tool_schemas()
            for step in range(self.max_calls + 1):
                if self._cancelled(identifier, turn):
                    break
                # Plan selection/application and asynchronous validation are not model history.
                # Refresh the authoritative state for every completion, including the final one.
                session = self.sessions.get(identifier)
                if step:
                    scene = self.tools.get_current_scene(identifier)
                messages[1] = state_message(session, scene, context, evidence)
                exhausted = step == self.max_calls
                if exhausted:
                    messages.append({"role": "system", "content": "Tool budget exhausted. Give a concise grounded final answer now; mention unfinished work and do not call tools. / 工具预算已用尽，请基于已有结果简要回答并说明尚未完成的工作。"})
                model_calls += 1
                reply = provider.complete(messages, [] if exhausted else schemas, timeout_s=90, max_tokens=2500)
                usage = reply.get("usage")
                if isinstance(usage, dict) and type(usage.get("total_tokens")) is int:
                    tokens += usage["total_tokens"]
                    usage_seen = True
                if self._cancelled(identifier, turn):
                    break
                message = reply["message"]
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    raise ProviderError("PROTOCOL", "invalid assistant message")
                calls = message.get("tool_calls")
                if not calls:
                    self._answer(identifier, message, evidence)
                    break
                if exhausted:
                    # Some models keep asking for tools even when none are offered; the turn still
                    # ends with an answer, the model's own text if it gave any, else the plan's state.
                    content = message.get("content")
                    self._answer(identifier, {"role": "assistant", "content": content if isinstance(content, str) and content.strip() else self._closing(identifier)}, evidence)
                    self.sessions.emit(identifier, "notice", text="The model exhausted this turn's tool budget; review the latest proposal or send another message / 模型已用尽本轮工具预算；请查看最新方案或发送下一条消息")
                    break
                if not isinstance(calls, list) or len(calls) > 12:
                    raise ProviderError("PROTOCOL", "invalid tool call batch")
                normalized = []
                for index, call in enumerate(calls):
                    call = call if isinstance(call, dict) else {}
                    function = call.get("function")
                    function = function if isinstance(function, dict) else {}
                    call_id = call.get("id")
                    if not isinstance(call_id, str) or not call_id or any(c["id"] == call_id for c in normalized):
                        call_id = f"call-{turn}-{step}-{index}"
                    name = function.get("name")
                    args = function.get("arguments", "")
                    normalized.append({"id": call_id, "type": "function", "function": {"name": name if isinstance(name, str) else "invalid_tool", "arguments": json.dumps(args, ensure_ascii=False, allow_nan=False) if isinstance(args, dict) else args if isinstance(args, str) else ""}})
                assistant = {"role": "assistant", "content": message.get("content") if isinstance(message.get("content"), str) else None, "tool_calls": normalized}
                self.sessions.append(identifier, assistant)
                messages.append(assistant)
                for call in normalized:
                    name, call_id = call["function"]["name"], call["id"]
                    try:
                        arguments = strict_json(call["function"]["arguments"])
                        if not isinstance(arguments, dict):
                            raise ValueError("Tool arguments must be an object / 工具参数必须是对象")
                        argument_error = None
                    except ValueError:
                        arguments = {}
                        argument_error = "Tool arguments must be valid JSON without duplicate keys or nonfinite values / 工具参数必须是有效 JSON，不能含重复键或非有限值"
                    label, label_zh = tool_label(name, arguments)
                    self.sessions.emit(identifier, "tool", call_id=call_id, name=name, status="running", label=label, label_zh=label_zh)
                    try:
                        if argument_error:
                            raise ValueError(argument_error)
                        if name == "run_story" and (context or {}).get("run_id") is not None and arguments.get("run_id") != context["run_id"]:
                            raise ValueError(f"run_id: this question refers to {context['run_id']}; read that run / 请读取问题明确指定的运行")
                        if self.sessions.cancelled(identifier, turn):
                            raise ValueError("Cancelled before tool execution / 工具执行前已取消")
                        result = self.tools.execute(identifier, name, arguments)
                        if name == "run_story" and isinstance(result, dict) and "expectations" in result:
                            evidence = story_evidence(arguments["run_id"], result)
                        status = "error" if isinstance(result, dict) and (result.get("ok") is False or "error" in result) else "ok"
                        detail = (f"Plan v{result['version']} / 方案 v{result['version']}" if isinstance(result, dict) and "version" in result else "Result recorded / 结果已记录")
                        if status == "error":
                            detail = "Compilation failed; inspect operation errors / 编译失败；请检查操作错误"
                    except (ValueError, TypeError, KeyError, FileNotFoundError) as error:
                        schema = next((s["function"]["parameters"] for s in schemas if s["function"]["name"] == name), None)
                        current = self.sessions.get(identifier)
                        plan = current["plans"].get(str(current["plan_version"]))
                        from .tools import operation_list

                        result = {"error": str(error), "code": "TOOL_ARGUMENTS", "tool": name,
                                  "schema": {"required": schema.get("required", []), "fields": {k: {a: v[a] for a in ("type", "enum", "description") if a in v} for k, v in schema.get("properties", {}).items()}} if schema else None,
                                  "current_operations": operation_list(plan["operations"]) if plan else [],
                                  "hint": "Use the declared fields/shapes; strict JSON rejects duplicate keys. Failed plans retain their operations: update/remove the listed op ids, do not re-add them / 按所列结构修正，JSON 不允许重复键；失败草稿保留操作，请按标识修改或移除，不要重复添加"}
                        status, detail = "error", str(error)[:240]
                    except Exception as error:
                        LOG.error("Assistant tool %s failed (%s)", name, type(error).__name__)
                        result = {"error": f"Tool failed ({type(error).__name__}); no success was recorded / 工具失败（{type(error).__name__}）；未记录成功", "code": "TOOL_ERROR"}
                        status, detail = "error", result["error"]
                    self.sessions.emit(identifier, "tool", call_id=call_id, name=name, status=status, label=label, label_zh=label_zh, detail=detail)
                    response = {"role": "tool", "tool_call_id": call_id, "content": compact_json(result)}
                    self.sessions.append(identifier, response)
                    messages.append(response)
        except ProviderError as error:
            self.sessions.emit(identifier, "error", code=error.code, message=provider_detail(error))
        except ValueError as error:
            self.sessions.emit(identifier, "error", code="CONFIGURATION", message=str(error))
        except Exception as error:
            LOG.error("Assistant turn failed (%s)", type(error).__name__)
            self.sessions.emit(identifier, "error", code="TURN_ERROR", message=f"Could not finish this turn ({type(error).__name__}); the session remains available / 本轮未能完成（{type(error).__name__}）；会话仍可继续")
        finally:
            self.sessions.finish_turn(identifier, turn, elapsed_s=round(time.monotonic() - started, 3), model_calls=model_calls, **({"tokens": tokens} if usage_seen else {}))
