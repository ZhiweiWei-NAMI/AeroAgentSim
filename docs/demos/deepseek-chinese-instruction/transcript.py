"""Render logs/ into transcript.md: the conversation, each plan version and the run result.

    python docs/demos/deepseek-chinese-instruction/transcript.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"


def main() -> None:
    session = json.loads((LOGS / "assistant-session.json").read_text(encoding="utf-8"))
    connection = json.loads((LOGS / "model-connection.json").read_text(encoding="utf-8"))
    run = json.loads((LOGS / "run" / "run-metadata.json").read_text(encoding="utf-8"))
    story = json.loads((LOGS / "run" / "story.json").read_text(encoding="utf-8"))
    out = ["# Transcript / 对话记录", "",
           f"Model / 模型: `{connection['model']}` via `{connection['base_url']}` (connection test: {connection['status']}, {connection['tested_at']})", "",
           f"Session / 会话: `{session['id']}` · project / 项目: `{session['workspace_id']}`", ""]
    for event in session["events"]:
        at = event["at"][11:19]
        kind = event["kind"]
        if kind == "user":
            out += [f"### {at} · User / 用户", "", f"> {event['text']}", ""]
        elif kind == "assistant":
            out += [f"### {at} · DeepSeek", "", event["text"], ""]
            if event.get("suggestions"):
                out += ["Suggested replies / 建议回复: " + " · ".join(event["suggestions"]), ""]
        elif kind == "tool" and event["status"] != "running":
            out += [f"- {at} tool `{event['name']}` → {event['status']}: {event.get('detail') or ''}"]
        elif kind == "plan":
            plan = session["plans"][str(event["version"])]
            out += ["", f"#### {at} · Plan v{event['version']}: {plan['title']}", "", plan["summary"], "", "Operations / 操作:", ""]
            out += [f"- `{op['op']}` `{json.dumps(op['params'], ensure_ascii=False)}`" for op in plan["operations"]]
            if plan["assumptions"]:
                out += ["", "Assumptions / 假设:", ""] + [f"- {item}" for item in plan["assumptions"]]
            if plan["open_questions"]:
                out += ["", "Open questions / 待确认问题:", ""]
                out += [f"- {q['question']} (options / 选项: {' / '.join(q.get('options', []))}; default / 默认: {q.get('default', '—')})" for q in plan["open_questions"]]
            out += [""]
        elif kind == "plan_validated":
            out += [f"- {at} plan v{event['version']} validated by the simulator / 仿真器校验: {'valid / 通过' if event['valid'] else 'invalid / 未通过'}", ""]
        elif kind == "turn_finished":
            out += [f"- {at} turn {event['turn']} finished / 本轮结束: {event['elapsed_s']} s, {event['model_calls']} model calls / 次模型调用, {event.get('tokens')} tokens", ""]
        elif kind == "applied":
            out += [f"### {at} · User confirmed / 用户确认", "", f"Plan v{event.get('plan_version', '?')} applied as project version {event.get('version', '?')} / 方案已应用为项目版本。", ""]
        elif kind == "notice":
            out += [f"- {at} notice / 提示: {event['text']}", ""]
    kpis = {k: v["value"] for k, v in (run.get("kpis") or {}).items() if v["value"] is not None}
    out += ["## Run result / 运行结果", "", f"Run / 运行 `{run['id']}`: **{run['status']}**", "",
            "| KPI | Value / 数值 |", "|---|---:|"]
    out += [f"| {key} | {round(value, 3) if isinstance(value, float) else value} |" for key, value in kpis.items() if value]
    out += ["", "| Expectation / 预期 | Status / 状态 | Progress / 进度 |", "|---|---|---|"]
    for item in story["expectations"]:
        progress = item.get("progress") or {}
        out += [f"| {item['text_zh']} | {item['status']} | {progress.get('done', '')}/{progress.get('total', '')} |"]
    (HERE / "transcript.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
