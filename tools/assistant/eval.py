"""Evaluate a running assistant API without constructing a local simulator."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONVERSATIONS = {
    "flagship": ["I want to combine food delivery and logistics. A parcel drone notices a person falling, asks a nearby meal courier to take over its parcel, then photographs the incident.", "Make the incident happen later", "Use two logistics UAVs instead", "Let the courier decide whether to accept", "Go back to the previous version of that arrangement", "What if the nearby courier is busy?", "Show me two options", "Use sensible defaults", "Keep everything else the same"],
    "vague": ["I want to test a few drones", "Use sensible defaults"],
    "parcel": ["Set up routine parcel delivery with three drones and six orders between existing rooftops", "Keep everything else the same, but lower the battery of one drone"],
    "inspection": ["I want cooperative power-line inspection: two drones inspect the line and a third can help if a person falls nearby", "Show me two options for the helper's decision"],
    "incident": ["I want to study incident response only: two patrol drones, one person falling at 90 seconds, and a short hover to photograph the scene", "Use sensible defaults"],
    "existing": ["Keep my project exactly as it is, and change only one parcel order's delivery destination to another existing customer", "Keep everything else the same"],
    "chinese": ["我想组合外卖和物流配送。一架物流无人机发现街上有人跌倒，请附近送餐无人机接续包裹后拍摄现场。", "让事故晚一点发生，其他安排不变", "让送餐无人机自主决定是否接受", "给我两种方案"],
}
TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}


class Client:
    def __init__(self, base: str, *, timeout: float = 30) -> None:
        self.base, self.timeout = base.rstrip("/"), timeout

    def request(self, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("AEROAGENTSIM_API_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.base + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = json.loads(error.read()).get("detail", f"HTTP {error.code}")
            raise RuntimeError(f"{path}: HTTP {error.code}: {detail}") from error

    def wait_turn(self, session_id: str, after: int, turn: int, *, timeout: float = 1200) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.request(f"/v1/assistant/sessions/{session_id}/events?after={after}&wait=25")
            events.extend(result["events"])
            after = result["last_seq"]
            if any(e["kind"] == "turn_finished" and e["turn"] == turn for e in events):
                return events
            if not result["busy"]:
                raise RuntimeError("Turn ended without a recorded turn_finished event / 对话结束但未记录 turn_finished")
        raise TimeoutError("Conversation exceeded evaluation deadline / 对话超出评测时间限制")

    def wait_validation(self, session_id: str, version: int, *, timeout: float = 180) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            plan = self.request(f"/v1/assistant/sessions/{session_id}/plans/{version}")
            if plan["validation"]["state"] != "pending":
                return plan
            time.sleep(0.2)
        raise TimeoutError("Plan validation exceeded evaluation deadline / 方案校验超出评测时间限制")


def evaluate(client: Client, name: str, *, workspace_id: str | None, apply: bool, run: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"locale": "zh-CN" if name == "chinese" else "en"}
    if name == "existing":
        if workspace_id is None:
            raise ValueError("The existing conversation requires --workspace-id / existing 对话需要 --workspace-id")
        body["workspace_id"] = workspace_id
    session = client.request("/v1/assistant/sessions", body)
    identifier = session["id"]
    report: dict[str, Any] = {"name": name, "session_id": identifier, "turns": []}
    for text in CONVERSATIONS[name]:
        started = time.monotonic()
        before = client.request(f"/v1/assistant/sessions/{identifier}")
        submitted = client.request(f"/v1/assistant/sessions/{identifier}/messages", {"text": text})
        events = client.wait_turn(identifier, before["last_seq"], submitted["turn"])
        current = client.request(f"/v1/assistant/sessions/{identifier}")
        plan = current["plan"]
        if plan is not None:
            plan = client.wait_validation(identifier, plan["version"])
        finished = next(e for e in events if e["kind"] == "turn_finished" and e["turn"] == submitted["turn"])
        replies = [e["text"] for e in events if e["kind"] == "assistant"]
        versions = sorted({e["version"] for e in events if e["kind"] == "plan"})
        plans = [client.request(f"/v1/assistant/sessions/{identifier}/plans/{v}") for v in versions]
        report["turns"].append({"text": text, "latency_s": round(time.monotonic() - started, 3), "model_elapsed_s": finished["elapsed_s"], "model_calls": finished["model_calls"], "tokens": finished.get("tokens"), "tools_used": [e["name"] for e in events if e["kind"] == "tool" and e["status"] == "running"], "tool_errors": [e for e in events if e["kind"] == "tool" and e["status"] == "error"], "provider_errors": [e for e in events if e["kind"] == "error"], "compile_failures": [p["compile"] for p in plans if not p["compile"]["ok"]], "questions_in_reply": sum(t.count("?") + t.count("？") for t in replies), "open_questions": len(plan["open_questions"]) if plan else None, "plan_version": plan["version"] if plan else None, "validation": plan["validation"] if plan else None, "reply": replies})
    session = client.request(f"/v1/assistant/sessions/{identifier}")
    plan = session["plan"]
    report["final_plan_summary"] = plan["scene"] if plan else None
    report["alternatives"] = session["alternatives"]
    if apply:
        if plan is None:
            raise ValueError("No final plan to apply / 没有可确认的最终方案")
        client.wait_validation(identifier, plan["version"])
        record = client.request(f"/v1/assistant/sessions/{identifier}/apply", {"plan_version": plan["version"]})
        report["applied"] = record
        if run:
            initialized = client.request("/v1/runs", {"studio_workspace": record["workspace_id"], "version": record["version"], "autostart": False})
            run_id = initialized["id"]
            client.request(f"/v1/runs/{run_id}/start", {})
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                metadata = next(r for r in client.request("/v1/runs?include_kpis=false") if r["id"] == run_id)
                if metadata["status"] in TERMINAL:
                    report["run"] = metadata
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError(f"Run {run_id} did not finish / 运行未完成")
            report["story"] = client.request(f"/v1/assistant/sessions/{identifier}/story/{run_id}")
    return report


def markdown(reports: list[dict[str, Any]]) -> str:
    lines = ["# Assistant evaluation / 助手评测", "", "| Conversation / 对话 | Turns / 轮数 | Model calls / 模型调用 | Tool errors / 工具错误 | Compile failures / 编译失败 | Total latency (s) / 总耗时（秒） |", "|---|---:|---:|---:|---:|---:|"]
    for report in reports:
        turns = report.get("turns", [])
        lines.append(f"| {report['name']} | {len(turns)} | {sum(t['model_calls'] for t in turns)} | {sum(len(t['tool_errors']) for t in turns)} | {sum(len(t['compile_failures']) for t in turns)} | {sum(t['latency_s'] for t in turns):.2f} |")
        if "error" in report:
            lines.extend(["", f"{report['name']}: {report['error']}"])
    for report in reports:
        lines.extend(["", f"## {report['name']}", "", "```json", json.dumps({k: report[k] for k in ("final_plan_summary", "applied", "run", "story") if k in report}, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--conversations", nargs="+", default=list(CONVERSATIONS))
    parser.add_argument("--workspace-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--run", action="store_true")
    arguments = parser.parse_args()
    names = [name for group in arguments.conversations for name in group.split(",")]
    if set(names) - CONVERSATIONS.keys():
        parser.error("Unknown conversation / 对话名称不存在")
    if arguments.run and not arguments.apply:
        parser.error("--run requires --apply / --run 需要 --apply")
    client = Client(arguments.api)
    reports = []
    for name in names:
        try:
            reports.append(evaluate(client, name, workspace_id=arguments.workspace_id, apply=arguments.apply, run=arguments.run))
        except (RuntimeError, ValueError, TimeoutError, OSError, KeyError) as error:
            reports.append({"name": name, "error": str(error)})
    arguments.out.mkdir(parents=True, exist_ok=True)
    (arguments.out / "report.json").write_text(json.dumps({"api": arguments.api, "conversations": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    (arguments.out / "report.md").write_text(markdown(reports), encoding="utf-8")
    if any("error" in report for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
