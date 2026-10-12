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
        "base": {
            "template": "food-delivery"
        },
        "title": "Meals, a parcel and a fallen person",
        "summary": "A parcel drone that notices a fallen person hands its parcel to a nearby meal courier at a street handoff point, then photographs the person. The courier completes the delivery.",
        "note": "First proposed arrangement",
        "operations": [
            {
                "op": "add_place",
                "params": {
                    "id": "parcel-depot",
                    "role": "vertiport",
                    "at": "plaza",
                    "label": "Parcel depot",
                    "label_zh": "包裹站"
                }
            },
            {
                "op": "add_place",
                "params": {
                    "id": "clinic-door",
                    "role": "customer",
                    "at": "clinic",
                    "label": "Clinic",
                    "label_zh": "诊所"
                }
            },
            {
                "op": "add_place",
                "params": {
                    "id": "handoff-street",
                    "role": "handoff_point",
                    "at": "street-plaza",
                    "label": "Street handoff point",
                    "label_zh": "街边交接点"
                }
            },
            {
                "op": "add_fleet",
                "params": {
                    "id": "logistics",
                    "role": "parcel_drone",
                    "count": 1,
                    "start": "parcel-depot",
                    "battery_pct": 90
                }
            },
            {
                "op": "add_orders",
                "params": {
                    "id": "parcels",
                    "kind": "parcel",
                    "count": 1,
                    "from": [
                        "parcel-depot"
                    ],
                    "to": [
                        "clinic-door"
                    ],
                    "release": {
                        "mode": "scheduled",
                        "times_s": [
                            0
                        ]
                    }
                }
            },
            {
                "op": "add_incident",
                "params": {
                    "id": "fall",
                    "kind": "person_fall",
                    "at": "street-hospital",
                    "time_s": 20,
                    "label": "A person has fallen",
                    "label_zh": "有人跌倒"
                }
            },
            {
                "op": "set_cooperation",
                "params": {
                    "watchers": {
                        "vehicles": [
                            "logistics"
                        ],
                        "notice_radius_m": 150
                    },
                    "responder": {
                        "mode": "prescribed",
                        "order": [
                            "request_handoff",
                            "respond_now",
                            "report_only"
                        ],
                        "rules": {
                            "require_replacement": True,
                            "protected_task_kinds": [
                                "medical"
                            ],
                            "min_battery_pct": 30
                        }
                    },
                    "helpers": {
                        "vehicles": [
                            "fleet"
                        ],
                        "ask_radius_m": 800,
                        "mode": "prescribed",
                        "rules": {
                            "accept_when": "always",
                            "min_battery_pct": 20
                        },
                        "selection": "nearest"
                    },
                    "photograph": {
                        "dwell_s": 5,
                        "altitude_m": 35
                    }
                }
            }
        ],
        "assumptions": [
            "The starting point's six meal orders and weather stay as they are",
            "Couriers always accept when asked and take the parcel after their current order",
            "The parcel drone leaves its delivery only once a courier has accepted it"
        ],
        "open_questions": [
            {
                "question": "Should the meal courier decide for itself whether to accept the parcel?",
                "options": [
                    "Keep the fixed rule",
                    "Let the courier decide"
                ]
            }
        ]
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
    "agent_goal": "Keep your own orders on time; help when you reasonably can.",
    "replies": {
        "later": "The person now falls at 30 s; everything else is unchanged. The parcel drone is further along its route by then, so check the notice estimate on the board.\nSuggestions: Let the courier decide | Show me two options",
        "agent": "Couriers now decide for themselves whether to take the parcel, with the goal of keeping their own orders on time. The decision appears as an agent step in the interaction sequence.\nSuggestions: Go back to the previous version | Show me two options",
        "restore": "Restored the previous cooperation arrangement; the rest of the latest version is kept.\nSuggestions: Let the courier decide | Make the incident happen later",
        "fleet": "There are now two parcel drones at the depot; only the first one carries the parcel.\nSuggestions: Make the incident happen later",
        "alternatives": "Here are two options that differ only in how couriers answer a handoff request. Compare them on the board and choose one.",
        "participant": "That is what this drone is responsible for in the plan, with the rules it follows.",
    },
    "reply": "Here is a concrete plan. The parcel drone's route passes about 23 m from where the person falls, inside its 150 m notice radius. The plan board shows who does what and what I assumed; nothing changes until you confirm.\nSuggestions: Make the incident happen later | Let the courier decide | Show me two options",
    "default_reply": "Describe the drones, deliveries or incident you want to study, and I will propose a concrete scene.",
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
            return {"role": "assistant", "content": "The platform rejected that change, so this proposal is not ready to confirm yet."}
        return {"role": "assistant", "content": SCRIPT["replies"].get(action or "", SCRIPT["reply"])}
    plan = state_plan(messages)
    if plan and action in {"later", "agent", "restore", "alternatives", "fleet", "participant"}:
        if action == "later":
            operation = replacement(plan, "add_incident")
            operation["params"]["time_s"] = operation["params"]["time_s"] + 10.0
            return tool_call("revise_plan", {"note": f"The person falls at {operation['params']['time_s']:g} s instead", "update": [operation]}, index)
        if action == "agent":
            operation = replacement(plan, "set_cooperation")
            operation["params"]["helpers"].update(mode="agent", goal=SCRIPT["agent_goal"])
            question = SCRIPT["flagship"]["open_questions"][0]["question"]
            return tool_call("revise_plan", {"note": "The couriers decide for themselves whether to accept", "update": [operation], "resolve_questions": [question] if any((q if isinstance(q, str) else q["question"]) == question for q in plan["open_questions"]) else []}, index)
        if action == "restore":
            operation = replacement(plan, "set_cooperation")
            return tool_call("restore_operations", {"from_version": max(1, plan["version"] - 1), "op_ids": [operation["id"]], "note": "Back to the previous cooperation arrangement"}, index)
        if action == "participant":
            fleet = replacement(plan, "add_fleet")["params"]["id"]
            return tool_call("explain_participant", {"id": f"{fleet}-1"}, index)
        if action == "fleet":
            operation = replacement(plan, "add_fleet")
            operation["params"]["count"] = 2
            return tool_call("revise_plan", {"note": "Two parcel drones instead of one", "update": [operation]}, index)
        variants = []
        for label, mode in (("A", "prescribed"), ("B", "agent")):
            operation = replacement(plan, "set_cooperation")
            operation["params"]["helpers"]["mode"] = mode
            if mode == "agent":
                operation["params"]["helpers"]["goal"] = SCRIPT["agent_goal"]
            else:
                operation["params"]["helpers"].pop("goal", None)
            title = "Couriers follow a fixed rule" if mode == "prescribed" else "Couriers decide for themselves"
            variants.append({"label": label, "title": title, "summary": "Same incident and deliveries; only how couriers answer a handoff request differs", "changes": {"note": title, "update": [operation]}, "assumptions": plan["assumptions"]})
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
