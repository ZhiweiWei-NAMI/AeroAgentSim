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


def without_state_assertions(text: str, *, expanded: bool = False, session: dict | None = None,
                             plan: dict | None = None, workspace: dict | None = None,
                             evidence: dict | None = None, authoritative_workflow: bool = False) -> str:
    """Remove contradicted declarations, preserving instructions and Markdown layout."""
    def contradicted(sentence: str) -> bool:
        plain = re.sub(r"[*_`]", "", sentence).strip()
        if authoritative_workflow:
            action = re.search(r"validat|re-?validat|sav\w*.*version|initializ|\bstart\b|confirm|compil|rewrit|revis|op-\d|校验|验证|保存.*版本|初始化|启动|确认|编译|重写|修订|更新.*操作", plain, re.I)
            instruction = re.search(r"^(?:please\s+)?(?:click|press|open|select|choose|validate|save|initialize|start|draft|compile|rewrite|revise|update|check|return|go|after|once|before)\b|\b(?:must|need to|should|required|steps)\b|请|点击|打开|选择|先|校验|保存|初始化|启动|编译|重写|修改|更新|检查|步骤", plain, re.I)
            plan_rewrite = re.search(r"initializ|初始化", plain, re.I) and re.search(r"revis|rewrit|compil|plan board|方案板|修订|重写|编译", plain, re.I)
            if action and instruction or plan_rewrite:
                return True
        if plan is not None:
            from .presentation import unchanged_plan

            # Confirmation instructions have one owner: the selected revision's
            # status below, including its compiled fleet and alternatives.
            if re.search(r"(?:press|click|choose|ready|can|should|once|after).*\bconfirm\b|\bconfirm\b.*(?:board|plan|revision|drone|UAV|save)|点击.*确认|确认并应用", plain, re.I):
                return True
            if unchanged_plan(plan) and re.search(r"(?:press|click)\s+confirm|点击.*确认", plain, re.I):
                return True
            if (session or {}).get("alternatives") and plan["version"] not in session["alternatives"] and re.search(r"\bconfirm\b|确认并应用", plain, re.I):
                return True
            if plan["validation"]["state"] == "valid" and re.search(
                r"^(?:please\s+)?wait\s+(?:before\s+confirming\b|for\s+(?:the\s+)?(?:plan\s+)?validation\b)|^(?:请)?等待校验",
                plain, re.I,
            ):
                return True
        # An imperative or conditional describes a workflow, not current readiness.
        if re.match(r"(?:please\s+)?(?:click|press|open|select|choose|validate|save|initialize|start|wait|after|once|if|before)\b|请|点击|打开|选择|先|(?:校验|验证)(?=当前|草稿|场景|配置|并|后|[。.!])|保存(?=项目|草稿|场景|配置|版本|为|后|[。.!])|初始化(?=场景|版本|后|[。.!，,])|启动后|如果|通过后", plain, re.I):
            return False
        if re.search(r"\b(?:must|need to|should)\b.*\b(?:click|press|validate|save)\b|需要.*(?:点击|校验|验证|保存)", plain, re.I):
            return False
        if expanded and re.search(r"engines?|components?|引擎|组件", plain, re.I) and re.search(r"\bno\b|\bnot\b|without|没有|不添加|不新增|未添加|未新增", plain, re.I):
            return True
        if workspace is not None:
            current = workspace.get("validation")
            valid_draft = bool(current and current.get("revision") == workspace["revision"] and current.get("valid") is True)
            latest = workspace.get("latest_version")
            ready = workspace["dirty"] is False and latest is not None and latest.get("validation", {}).get("valid") is True
            conditional = re.search(r"\b(?:after|once|when|if)\b|之后|后再|通过后|保存后|如果", plain, re.I)
            if not ready and not conditional and re.search(r"\b(?:can|ready to|may)\b.*\b(?:initializ\w*|start|run)\b|(?:可|能够).*?(?:初始化|启动|运行)", plain, re.I):
                return True
            if workspace["dirty"] and not valid_draft and re.search(r"nothing.*validat|no.*(?:need|require).*validat|already validated|无需.*(?:校验|验证)|已经?校验|已通过校验", plain, re.I):
                return True
            version = re.search(r"(?:project|workspace|saved)\s+version\s+(?:(?:is|still|remains|was|v)\s*)*(\d+)|(?:项目|工作区)(?:的)?版本\s*(?:(?:仍是|仍为|为|是|已保存为)\s*)?v?\s*(\d+)", plain, re.I)
            if version and re.search(r"applied|saved|latest|current|still|pinned|固定|已应用|已保存|最新|当前|仍是", plain, re.I):
                claimed = int(version.group(1) or version.group(2))
                if evidence and re.search(r"\brun\b|运行|仿真", plain, re.I):
                    actual = evidence.get("workspace_version")
                elif re.search(r"latest|current|still|最新|当前|仍是", plain, re.I):
                    actual = latest["version"] if latest else None
                else:
                    known = [r["version"] for r in (session or {}).get("applied", [])]
                    return bool(known) and claimed not in known
                if actual is not None and claimed != actual:
                    return True
        if plan is not None:
            reference = re.search(r"\bplan\s+(?:(?:revision|version)\s+)?v?(\d+)|(?:方案|计划)(?:修订|版本)?\s*v?(\d+)", plain, re.I)
            target = plan
            if reference:
                revision = int(reference.group(1) or reference.group(2))
                target = (session or {}).get("plans", {}).get(str(revision))
            if target is not None:
                state = target["validation"]["state"]
                if state != "valid" and re.search(r"(?:is|has been|already)\s+validated|passed.*(?:check|validat)|校验通过|已通过校验|已校验", plain, re.I):
                    return True
                if state == "valid" and re.search(r"validation\s+(?:(?:is|still)\s+)*(?:pending|failed)|still.*validat|等待校验|校验失败", plain, re.I):
                    return True
                from .presentation import unchanged_plan

                if unchanged_plan(target) and re.search(r"(?:ready|can|may|should|press|click).*confirm|可.*确认|点击.*确认|应该.*确认", plain, re.I):
                    return True
        return False

    def clean(content: str) -> str:
        # Split prose only; numbered prefixes and decimal values stay attached.
        parts = re.split(r"(?<=[。！？])|(?<=[.!?])(?=\s+[^\d\s])", content)
        kept = [part for part in parts if not contradicted(part)]
        return content if len(kept) == len(parts) else "".join(kept).strip()

    lines = []
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            lines.append(line)
        elif fence or not line.strip():
            lines.append(line)
        elif line.lstrip().startswith("|"):
            # Filter inside cells, never split or merge a Markdown table row.
            lines.append("|".join(clean(cell) for cell in line.split("|")))
        else:
            prefix = re.match(r"^(\s*(?:[-+*]|\d+[.)])\s+|\s*#{1,6}\s+|\s*>\s*)", line)
            body = line[prefix.end():] if prefix else line
            cleaned = clean(body)
            if cleaned:
                lines.append((prefix.group() if prefix else "") + cleaned)
    # Filtering can remove an asserted state in the middle of a numbered workflow.
    # Renumber surviving steps, keeping independent/nested lists and code intact.
    counters, fence = {}, False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if fence:
            continue
        number = re.match(r"^(\s*)(\d+)([.)])\s+(.*)$", line)
        if number:
            indent = len(number[1])
            counters = {depth: count for depth, count in counters.items() if depth <= indent}
            counters[indent] = counters.get(indent, 0) + 1
            lines[index] = f"{number[1]}{counters[indent]}{number[3]} {number[4]}"
        elif line.strip() and not line[:1].isspace():
            counters.clear()
    return "\n".join(lines).strip()


def without_effects_section(text: str) -> str:
    """The effects section has one owner: the compiler's current revision diff."""
    lines, skipping, fence = [], False, False
    heading = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:实际变更|实际改动|What this changes|Actual changes)\s*[:：]?(?:\*\*)?\s*$", re.I)
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if not fence and heading.match(line):
            skipping = True
            continue
        if skipping:
            if not line.strip() or re.match(r"\s*(?:[-+*]|\d+[.)]|\|)", line):
                continue
            skipping = False
        lines.append(line)
    return "\n".join(lines).strip()


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
            **{k: story[k] for k in ("assessment", "coverage", "workspace_version", "plan_version", "expectations_source", "detail", "details_url", "locale", "wind") if k in story},
            "failure_count": len(story.get("failures", [])), "failures": failure_summary(story.get("failures", [])),
            "expectations": [{k: row.get(k) for k in ("id", "text", "text_zh", "status", "at_ns", "progress")} | {"recorded": (row.get("explanation") or {}).get("text"), "recorded_zh": (row.get("explanation") or {}).get("text_zh")} for row in story["expectations"]],
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

    def _grounded_reply(self, identifier: str, text: str, evidence: dict | None, context: dict[str, Any] | None = None) -> str:
        """Fixed observation actions render recorded facts instead of historical model prose."""
        session = self.sessions.get(identifier)
        question = next((m["content"] for m in reversed(session["transcript"]) if m["role"] == "user"), "")
        zh = str((context or {}).get("locale", session["locale"])).startswith("zh")
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
            assessed = sum(row["status"] not in {"not_evaluated", "not_taken"} for row in rows)
            lines = [f"运行 {evidence['run_id']}（{evidence['status']}）记录了 {coverage['recorded_events']} 个事件。" if zh else f"Run {evidence['run_id']} ({evidence['status']}) recorded {coverage['recorded_events']} events."]
            if rows:
                lines.append(f"预期步骤已发生 {happened}/{assessed}；以各步骤证据为准。" if zh else f"{happened}/{assessed} expected steps were recorded; assess the evidence for each step.")
            else:
                lines.append("没有配置预期，不能据此判断全部成功。" if zh else "No expectations were configured; this does not establish success.")
            for row in rows[:10]:
                label = (row.get("text_zh") if zh else row["text"]) or row["text"]
                at = f" ({row['at_ns'] / 1e9:g} s)" if row.get("at_ns") is not None else ""
                status = ({"happened": "已记录", "missed": "未记录", "pending": "等待记录", "not_taken": "未走此分支", "not_evaluated": "未评估"} if zh else
                          {"happened": "recorded", "missed": "not recorded", "pending": "pending", "not_taken": "branch not taken", "not_evaluated": "not evaluated"})[row["status"]]
                lines.append(f"- {label[:240]}: {status}{at}")
                if (row.get("progress") or {}).get("configured"):
                    explanation = row.get("recorded_zh" if zh else "recorded")
                    if explanation:
                        lines.append(explanation)
            count = evidence.get("failure_count", len(evidence.get("failures", [])))
            if count:
                lines.append(f"记录到 {count} 条失败或冲突，按类型汇总：" if zh else f"{count} failure or conflict records, grouped by type:")
            for failure in evidence.get("failures", []):
                label = failure.get("text_zh") if zh else failure["text"]
                reason = failure.get("reason")
                lines.append(f"- {label or failure['text']} (×{failure['count']})")
                for pair in failure.get("pairs", []):
                    caption = pair.get("text_zh") if zh else pair["text"]
                    lines.append(f"  - {caption or pair['text']} (×{pair['count']})")
                if not failure.get("pairs"):
                    example = failure.get("last_example")
                    if example:
                        caption = example.get("text_zh") if zh else example["text"]
                        lines.append(("  最后一条记录示例：" if zh else "  Last recorded example: ") + f"{failure['last_at_ns'] / 1e9:g} s: {caption or example['text']}" + (f" — {reason}" if reason else ""))
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

    def _answer(self, identifier: str, message: dict[str, Any], evidence: dict | None = None, context: dict[str, Any] | None = None) -> None:
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("PROTOCOL", "empty final completion")
        text, suggestions = split_suggestions(content)
        if not text:
            raise ProviderError("PROTOCOL", "empty final completion")
        grounded = self._grounded_reply(identifier, text, evidence, context)
        used_grounded_facts = grounded != text
        text = grounded
        session = self.sessions.get(identifier)
        plan = session["plans"].get(str(session["plan_version"]))
        zh = str((context or {}).get("locale", session["locale"])).startswith("zh")
        has_scene = plan is not None and plan["compile"]["ok"] or plan is None and session["workspace_id"] is not None
        if isinstance(self.tools, ToolRunner) and (evidence is not None and "wind" in evidence or evidence is None and has_scene):
            from .wind import ground_wind_prose

            if re.search(r"\bwind\b|\bgust\b|风", text, re.I) and re.search(r"north|south|east|west|direction|[东西南北]|风向|流向", text, re.I):
                if evidence is not None:
                    wind = evidence["wind"]
                else:
                    _, scene = self.tools._scene(identifier)
                    wind = scene["environment"]["wind"]
                text = ground_wind_prose(text, wind, zh=zh)
        if plan is not None and not (used_grounded_facts and evidence is not None):
            # Validation can finish while the provider is writing its completion.
            # Render status from the store, replacing the provider's status prose.
            # New plans retain the original model prose only for audit. Legacy
            # plans are recognized by a prerequisite missing from their recipe.
            expanded = "_model_prose" in plan or any(op["op"] == "add_lowalt_components" and
                not any(requested["op"] == "add_lowalt_components" for requested in plan["operations"])
                for op in plan["compile"].get("operations", []))
            workspace = self.tools.workspace_state(identifier) if isinstance(self.tools, ToolRunner) else None
            authoritative_workflow = workspace is not None and any(record["plan_version"] == plan["version"] for record in session["applied"])
            if not used_grounded_facts:
                text = without_state_assertions(text, expanded=expanded, session=session, plan=plan,
                                                workspace=workspace, evidence=evidence,
                                                authoritative_workflow=authoritative_workflow)
            if authoritative_workflow:
                suggestions = [cleaned for suggestion in suggestions if (cleaned := without_state_assertions(
                    suggestion, session=session, plan=plan, workspace=workspace, authoritative_workflow=True))]
            zh = str((context or {}).get("locale", session["locale"])).startswith("zh")
            unapplied = not any(record["plan_version"] == plan["version"] for record in session["applied"])
            if unapplied and plan.get("what_changes"):
                from .presentation import change_summaries

                has_effects = without_effects_section(text) != text
                if expanded or has_effects:
                    text = without_effects_section(text)
                    difference = plan.get("diff_previous") if plan.get("parent") else None
                    rows = change_summaries(difference) if difference is not None else plan["what_changes"]
                    if rows:
                        key = "text_zh" if zh else "text"
                        effects = ("实际变更：\n" if zh else "What this changes:\n") + "\n".join("- " + row[key] for row in rows)
                        text = (text + "\n\n" if text else "") + effects
            status = plan_status(session, plan, workspace=workspace, evidence=evidence, zh=zh)
            text = (text + "\n\n" if text else "") + status
        self.sessions.append(identifier, {"role": "assistant", "content": text + ("\nSuggestions: " + " | ".join(suggestions) if suggestions else "")})
        self.sessions.emit(identifier, "assistant", text=text, suggestions=suggestions)

    def _closing(self, identifier: str, context: dict[str, Any] | None = None) -> str:
        """A grounded closing line from the session's current plan, for a turn the model did not close."""
        session = self.sessions.get(identifier)
        zh = str((context or {}).get("locale", session.get("locale", ""))).startswith("zh")
        plan = session["plans"].get(str(session["plan_version"]))
        if plan is None:
            return "这一轮还没有形成方案，请再发一条消息继续。" if zh else "No proposal was finished in this turn; send another message to continue."
        workspace = self.tools.workspace_state(identifier) if isinstance(self.tools, ToolRunner) else None
        return plan_status(session, plan, workspace=workspace, zh=zh)

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
                    self._answer(identifier, message, evidence, context)
                    break
                if exhausted:
                    # Some models keep asking for tools even when none are offered; the turn still
                    # ends with an answer, the model's own text if it gave any, else the plan's state.
                    content = message.get("content")
                    self._answer(identifier, {"role": "assistant", "content": content if isinstance(content, str) and content.strip() else self._closing(identifier, context)}, evidence, context)
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
