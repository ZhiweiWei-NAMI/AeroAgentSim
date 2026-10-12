"""Deterministic OpenAI-compatible model for the assistant browser journey."""

from __future__ import annotations

import argparse
import copy
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# All scripted arrangements live here; callbacks only select and revise these data.
SCRIPT: dict[str, Any] = {
    "model": "assistant-scripted",
    "flagship": {
        "base": {"template": "food-delivery"},
        "title": "Meals, parcels and a street incident / 外卖、包裹与街道事故",
        "summary": "Two parcel drones can ask meal couriers to take over a delivery before photographing an incident / 两架包裹无人机可请求送餐无人机接续配送，再拍摄事故现场",
        "note": "First proposed arrangement / 首次提议",
        "operations": [
            {"op": "add_place", "params": {"id": "parcel-port", "role": "vertiport", "at": "plaza", "label": "Parcel vertiport / 包裹起降点"}},
            {"op": "add_place", "params": {"id": "handoff-east", "role": "handoff_point", "at": "east", "label": "East roof handoff / 东侧屋顶交接点"}},
            {"op": "add_fleet", "params": {"id": "logistics", "role": "parcel", "count": 2, "start": "parcel-port", "battery_pct": 90.0, "payload_capacity_kg": 3.0, "task_kinds": ["parcel"]}},
            {"op": "add_orders", "params": {"id": "parcel-orders", "kind": "parcel", "count": 3, "from": ["parcel-port"], "to": ["customer-1", "customer-2", "customer-3"], "release": {"start_s": 0.0, "interval_s": 20.0}, "payload_kg": 1.0}},
            {"op": "add_incident", "params": {"id": "incident-1", "kind": "person_fall", "at": "road", "time_s": 90.0, "label": "Person fell on the street / 行人在街道跌倒"}},
            {"op": "set_cooperation", "params": {
                "watchers": {"vehicles": ["logistics-1", "logistics-2"], "notice_radius_m": 150.0},
                "responder": {"mode": "prescribed", "order": ["request_handoff", "finish_then_respond", "report_only"], "rules": {"require_replacement": True, "protected_task_kinds": ["medical"], "min_battery_pct": 30.0, "max_response_distance_m": 600.0}},
                "helpers": {"vehicles": ["drone-1", "drone-2"], "ask_radius_m": 500.0, "mode": "prescribed", "rules": {"accept_when": "idle", "min_battery_pct": 25.0}, "selection": "nearest"},
                "handoff_points": ["handoff-east"], "photograph": {"dwell_s": 5.0, "altitude_m": 35.0},
            }},
        ],
        "assumptions": ["Keep the starting point's meal workload and weather / 保留起始模板的外卖订单与天气", "Helpers accept only when idle; a carried parcel requires an accepted replacement / 接替者仅空闲时接受；已携带包裹须有人接替"],
        "open_questions": [{"question": "Should the meal courier decide for itself whether to accept? / 送餐无人机是否自主决定接受？", "options": ["Keep prescribed idle acceptance / 保留空闲接受规则", "Let the courier decide / 让送餐无人机自主决定"]}],
    },
    "rules": [
        {"matches": ["food delivery and logistics", "外卖和物流"], "action": "flagship"},
        {"matches": ["incident happen later", "事故晚一点"], "action": "later"},
        {"matches": ["courier decide", "自主决定"], "action": "agent"},
        {"matches": ["previous version", "上一个版本"], "action": "restore"},
        {"matches": ["responsible for", "负责什么"], "action": "participant"},
        {"matches": ["two options", "两种方案"], "action": "alternatives"},
        {"matches": ["two logistics uavs"], "action": "fleet"},
    ],
    "agent_goal": "Keep your own orders on time; help when you reasonably can. / 优先按时完成自己的订单，并在合理条件下帮助他人。",
    "reply": "Review the compiled proposal and its validation on the plan board. Confirm configures the scene; Initialize and Start are separate. / 请在方案板查看编译结果与校验状态。确认会配置场景；初始化和启动需另外操作。\nSuggestions: Use sensible defaults | Make the incident happen later | Let the courier decide",
    "default_reply": "Describe the drones, deliveries or incident you want to study; I can propose a scene. / 请描述要研究的无人机、配送或事故，我可以提出场景方案。",
}


def state_plan(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        if message.get("role") == "system":
            for line in str(message.get("content", "")).splitlines():
                if line.startswith("plan: "):
                    return json.loads(line[6:])
    return None


def tool_call(name: str, arguments: dict[str, Any], index: int) -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [{"id": f"script-{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def replacement(plan: dict[str, Any], name: str) -> dict[str, Any]:
    return copy.deepcopy(next(op for op in plan["operations"] if op["op"] == name))


def completion(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages", [])
    system = str(messages[0].get("content", "")) if messages else ""
    if system.startswith("AEROAGENTSIM-COOPERATION-DECISION"):
        # Read the declared allowed options, not blocked options mentioned elsewhere.
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        allowed: list[str] = []
        for content in (str(m.get("content", "")) for m in messages):
            try:
                data = json.loads(content)
            except ValueError:
                continue
            if isinstance(data, dict):
                candidate = data.get("allowed", data.get("allowed_options"))
                if isinstance(candidate, list):
                    allowed.extend(item if isinstance(item, str) else item["choice"] for item in candidate)
        if not allowed:
            match = re.search(r"(?:allowed_options|allowed|Allowed options)\s*[:=]\s*(\[[^\]]*\])", prompt, re.IGNORECASE)
            if match:
                allowed = json.loads(match.group(1))
        choice = next((option for option in ("accept", "request_handoff", "decline", "report_only", "finish_then_respond", "respond_now") if option in allowed), None)
        if choice is None:
            raise ValueError("No supported allowed option / 没有支持的允许选项")
        return {"role": "assistant", "content": json.dumps({"choice": choice, "reason": "Chosen from the engine's allowed options / 从引擎允许的选项中选择"}, ensure_ascii=False)}
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    text = str(messages[last_user].get("content", "")).casefold() if last_user >= 0 else ""
    results = [m for m in messages[last_user + 1:] if m.get("role") == "tool"]
    if body.get("tools") == []:
        return {"role": "assistant", "content": SCRIPT["reply"]}
    action = next((row["action"] for row in SCRIPT["rules"] if any(fragment in text for fragment in row["matches"])), None)
    index = len(messages)
    if action == "flagship":
        if not results:
            return tool_call("list_starting_points", {}, index)
        if len(results) == 1:
            return tool_call("draft_plan", copy.deepcopy(SCRIPT["flagship"]), index)
        return {"role": "assistant", "content": SCRIPT["reply"]}
    if results:
        latest = json.loads(results[-1]["content"])
        if "error" in latest or latest.get("ok") is False:
            return {"role": "assistant", "content": "The tool reported an error; this proposal is not ready to confirm. / 工具报告错误；此方案尚不能确认。"}
        return {"role": "assistant", "content": SCRIPT["reply"]}
    plan = state_plan(messages)
    if plan and action in {"later", "agent", "restore", "alternatives", "fleet", "participant"}:
        if action == "later":
            operation = replacement(plan, "add_incident")
            operation["params"]["time_s"] = 150.0
            return tool_call("revise_plan", {"note": "Incident at 150 s / 事故改为第 150 秒", "update": [operation]}, index)
        if action == "agent":
            operation = replacement(plan, "set_cooperation")
            operation["params"]["helpers"].update(mode="agent", goal=SCRIPT["agent_goal"])
            question = SCRIPT["flagship"]["open_questions"][0]["question"]
            return tool_call("revise_plan", {"note": "Helpers decide whether to accept / 接替者自主决定是否接受", "update": [operation], "resolve_questions": [question] if any((q if isinstance(q, str) else q["question"]) == question for q in plan["open_questions"]) else []}, index)
        if action == "restore":
            operation = replacement(plan, "set_cooperation")
            return tool_call("restore_operations", {"from_version": max(1, plan["version"] - 1), "op_ids": [operation["id"]], "note": "Restore the previous cooperation arrangement / 恢复此前协作安排"}, index)
        if action == "participant":
            fleet = replacement(plan, "add_fleet")["params"]["id"]
            return tool_call("explain_participant", {"id": f"{fleet}-1"}, index)
        if action == "fleet":
            operation = replacement(plan, "add_fleet")
            operation["params"]["count"] = 2
            return tool_call("revise_plan", {"note": "Use two parcel UAVs / 使用两架包裹无人机", "update": [operation]}, index)
        variants = []
        for label, mode in (("A", "prescribed"), ("B", "agent")):
            operation = replacement(plan, "set_cooperation")
            operation["params"]["helpers"]["mode"] = mode
            if mode == "agent":
                operation["params"]["helpers"]["goal"] = SCRIPT["agent_goal"]
            else:
                operation["params"]["helpers"].pop("goal", None)
            variants.append({"label": label, "title": f"{mode} helpers / 接替者模式：{mode}", "summary": "Keep the incident and deliveries; change only helper decisions / 保留事故和配送，仅改变接替者决策", "changes": {"note": "Compare helper decisions / 比较接替者决策", "update": [operation]}, "assumptions": plan["assumptions"]})
        return tool_call("propose_alternatives", {"variants": variants}, index)
    return {"role": "assistant", "content": SCRIPT["default_reply"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_json(self, status: int, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self.send_json(200, {"object": "list", "data": [{"id": SCRIPT["model"], "object": "model", "owned_by": "scripted-test"}]})
        else:
            self.send_json(404, {"error": "Unknown endpoint / 端点不存在"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_json(404, {"error": "Unknown endpoint / 端点不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            message = completion(body)
        except (ValueError, TypeError, KeyError, StopIteration):
            self.send_json(422, {"error": "Invalid scripted request or missing state / 测试请求无效或缺少状态"})
            return
        self.send_json(200, {"id": "chatcmpl-scripted", "object": "chat.completion", "model": SCRIPT["model"], "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    arguments = parser.parse_args()
    server = ThreadingHTTPServer((arguments.host, arguments.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
