"""Low-altitude presentation data and pure committed-record normalization.

Vocabulary is a compatibility move: wording and generic endpoint nouns retain
v1's meaning. An endpoint noun is not evidence of an unrecorded facility.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from aeroagentsim.presentation_api import PRESENTATION_FORMAT, PresentationPlugin, json_data

# Finite shipped display vocabulary only; arbitrary authored names/prose stay intact.
_LABELS_ZH = {
    "Customer": "客户",
    "Shared city weather": "城市天气",
    "Vertiport West Plaza": "西广场起降场",
    "Vertiport East Roof": "东侧屋顶起降场",
    "Vertiport North Roof": "北侧屋顶起降场",
    "Vertiport Bund Roof": "外滩屋顶起降场",
    "West Plaza charging pad": "西广场充电桩",
    "East rooftop charging pad": "东侧屋顶充电桩",
    "Bund rooftop charging pad": "外滩屋顶充电桩",
    "East pad": "东侧充电桩",
    "North pad": "北侧充电桩",
    "Hospital rooftop pad": "医院屋顶起降位",
    "Clinic rooftop": "诊所屋顶",
    "Customer rooftop east": "东侧客户屋顶",
    "North road power-line start": "北侧道路输电线路起点",
    "North road survey return point": "北侧道路巡检返航点",
    "Powerline corridor": "输电线路走廊",
    "Power-line survey drone": "输电线路巡检无人机",
    "Field sprayer": "农田喷洒无人机",
    "Field-edge battery station": "田边换电站",
    "Field-edge battery swap pad": "田边换电位",
    "Field entry": "农田入口",
    "Field exit": "农田出口",
    "Field start": "农田起点",
    "Field end": "农田终点",
    "Huangpu edge demonstration field": "黄浦边缘示范农田",
    "Guard return": "巡逻返航点",
    "Incident site": "事件现场",
    "Temporary incident no-fly zone": "事故临时禁飞区",
    "Huangpu road flight corridor": "黄浦道路飞行走廊",
    "East west corridor": "东西向走廊",
    "North corridor": "北侧走廊",
    "Landmark rooftop exclusion": "地标屋顶禁飞区",
    "Edge coordinator": "边缘协调器",
    "Lane incident": "事故车道",
    "Lane adjacent": "相邻车道",
    "Adjacent lane bypass": "相邻车道绕行路线",
}
_NUMBERED_LABELS_ZH = {
    "Drone": "无人机", "UAV": "无人机", "Survey drone": "巡检无人机",
    "Patrol drone": "巡逻无人机", "Sprayer": "喷洒无人机", "Air taxi": "空中出租车",
    "Customer": "客户", "Customer rooftop": "客户屋顶", "Vertiport": "起降场",
    "Pad": "充电桩", "Charging pad": "充电桩", "Depot": "配送站",
    "Vehicle": "车辆", "Car": "车辆", "Task": "任务",
    "Parcel mission": "包裹配送任务", "Medical mission": "医疗配送任务",
    "Passenger mission": "载客任务", "Spraying mission": "喷洒任务",
    "Inspection mission": "巡检任务", "Patrol mission": "巡逻任务",
    "Incident Response mission": "事故响应任务", "Route vehicle": "车辆路线",
    "Road vehicle": "道路车辆", "Road task vehicle": "道路车辆任务",
    "Medical delivery": "医疗配送", "District patrol": "街区巡逻",
    "Background patrol": "背景巡逻", "Incident capture": "事故现场图像",
    "Incident": "事故", "Bg": "背景机",
}
_NUMBERED_LABEL = re.compile(
    "(" + "|".join(re.escape(name) for name in _NUMBERED_LABELS_ZH) + r") ([A-Z]?\d+)\Z"
)
_ENUMS_ZH = {
    "kind": {"person_fall": "行人跌倒", "fire": "火情"},
    "choice": {
        "respond_now": "立即响应", "request_handoff": "请求交接",
        "finish_then_respond": "完成任务后响应", "report_only": "仅上报",
        "accept": "接受", "decline": "拒绝",
    },
    "mode": {"prescribed": "预设规则", "agent": "智能体决策"},
    "algorithm": {
        "nearest": "就近分配", "auction": "竞价分配", "hungarian": "批量最优匹配",
        "llm": "语言模型智能体", "straight": "直线航线", "corridor_astar": "安全走廊航线",
        "coverage": "区域覆盖", "survey": "巡检航点", "patrol": "巡逻环线",
        "altitude_layers": "高度分层", "hold_and_go": "等待放行", "threshold_return": "低电量返航",
    },
    "action": {
        "altitude_layers": "高度分层", "hold_and_go": "等待放行",
        "altitude": "调整高度", "hold": "等待", "release": "放行", "reroute": "改道",
        "stop": "停止", "already resolving": "正在解除冲突", "observed separation": "已恢复安全间隔",
    },
    "reason": {
        "battery low": "电量不足", "battery reserve": "预留电量不足",
        "battery below reserve": "电量低于预留阈值", "active no-fly zone": "禁飞区生效",
        "no-fly zone": "禁飞区限制", "vertiport closed": "起降场关闭",
        "wind exceeds vehicle limit": "风速超过飞行器上限",
        "wind within vehicle limit": "风速恢复到允许范围", "active interval": "设定生效时段",
        "incident": "事故影响", "cleared": "限制已解除", "weather": "天气限制",
        "wind subsided": "风速减弱", "zone closed": "空域关闭",
        "separation conflict": "间隔冲突", "charging finished": "充电完成",
        "charging algorithm": "充电策略", "traffic incident": "交通事故影响",
        "motion failed": "飞行指令失败", "motion rejected": "飞行指令被拒绝",
        "motion canceled": "飞行指令被取消",
        "control diversion made no progress": "控制绕行未取得进展",
        "no route to assigned target": "没有通往指定目标的航线",
        "no progress toward assigned target": "未向指定目标取得进展",
        "route planner returned no route": "航线规划器未返回可用航线",
    },
}



TASK_KIND_CATEGORY = {
    "parcel": "logistics",
    "food": "logistics",
    "medical": "emergency",
    "inspection": "inspection",
    "survey": "inspection",
    "spraying": "agriculture",
    "passenger": "mobility",
    "patrol": "security",
    "incident_response": "emergency",
}

# Plain nouns and completion phrases per task kind, used by task narratives.
TASK_NOUN = {
    "parcel": "parcel order",
    "food": "meal order",
    "medical": "urgent medical order",
    "inspection": "inspection job",
    "survey": "survey job",
    "spraying": "spraying job",
    "passenger": "passenger trip",
    "patrol": "patrol round",
    "incident_response": "incident response",
}
TASK_DONE = {
    "parcel": "delivered the parcel to",
    "food": "delivered the meal to",
    "medical": "delivered urgent supplies to",
    "inspection": "finished inspecting",
    "survey": "finished surveying",
    "spraying": "finished spraying",
    "passenger": "dropped off the passenger at",
    "patrol": "finished patrolling",
    "incident_response": "responded at",
}

TASK_NOUN_ZH = {
    "parcel": "包裹配送任务",
    "food": "外卖订单",
    "medical": "紧急医疗配送任务",
    "inspection": "巡检任务",
    "survey": "测绘任务",
    "spraying": "喷洒任务",
    "passenger": "载客任务",
    "patrol": "巡逻任务",
    "incident_response": "事故响应任务",
}
TASK_DONE_ZH = {
    "parcel": "完成包裹投递",
    "food": "送达餐食",
    "medical": "送达紧急医疗物资",
    "inspection": "完成巡检",
    "survey": "完成测绘",
    "spraying": "完成喷洒",
    "passenger": "送达乘客",
    "patrol": "完成巡逻",
    "incident_response": "完成事故响应",
}

LOWALT_DEFAULTS: dict[str, dict[str, Any]] = {
    "lowalt.task.created": {
        "template": "{task} task created",
        "template_zh": "任务{task}已创建",
        "category": "mixed",
        "severity": "info",
        "entities": ["task"],
        "position": "task",
    },
    "lowalt.task.assigned": {
        "template": "{vehicle} assigned to {task} using {algorithm}",
        "template_zh": "通过{algorithm}将{task}分配给{vehicle}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.started": {
        "template": "{vehicle} started {task}",
        "template_zh": "{vehicle}开始执行{task}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.picked_up": {
        "template": "{vehicle} picked up {task}",
        "template_zh": "{vehicle}接取任务{task}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.completed": {
        "template": "{vehicle} completed {task} in {latency_s} seconds",
        "template_zh": "{vehicle}完成{task}，耗时 {latency_s} 秒",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.failed": {
        "template": "{task} failed: {reason}",
        "template_zh": "{task}失败：{reason}",
        "category": "mixed",
        "severity": "warning",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.airspace.conflict": {
        "template": "{a} and {b} separation conflict: {horizontal_m} m horizontal, {vertical_m} m vertical",
        "template_zh": "{a}与{b}间隔冲突：水平 {horizontal_m} 米，垂直 {vertical_m} 米",
        "category": "airspace",
        "severity": "warning",
        "entities": ["a", "b"],
    },
    "lowalt.airspace.conflict_resolved": {
        "template": "{a} and {b} conflict resolved: {action}",
        "template_zh": "{a}与{b}的冲突已解除：{action}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["a", "b"],
    },
    "lowalt.airspace.geofence_entered": {
        "template": "{vehicle} entered geofence zone {zone}",
        "template_zh": "{vehicle}进入电子围栏区域{zone}",
        "category": "airspace",
        "severity": "warning",
        "entities": ["vehicle", "zone"],
        "position": "vehicle",
    },
    "lowalt.airspace.zone_activated": {
        "template": "Zone {zone} activated: {reason}",
        "template_zh": "区域{zone}已启用：{reason}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["zone"],
    },
    "lowalt.airspace.zone_deactivated": {
        "template": "Zone {zone} deactivated: {reason}",
        "template_zh": "区域{zone}已停用：{reason}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["zone"],
    },
    "lowalt.energy.low": {
        "template": "{vehicle} battery low: {soc} state of charge",
        "template_zh": "{vehicle}电量不足，剩余电量比例为 {soc}",
        "category": "energy",
        "severity": "warning",
        "entities": ["vehicle"],
        "position": "vehicle",
    },
    "lowalt.energy.charging_started": {
        "template": "{vehicle} charging started at {pad}: {soc} state of charge",
        "template_zh": "{vehicle}在{pad}开始充电，剩余电量比例为 {soc}",
        "category": "energy",
        "severity": "info",
        "entities": ["vehicle", "pad"],
        "position": "vehicle",
    },
    "lowalt.energy.charging_finished": {
        "template": "{vehicle} charging finished at {pad}: {soc} state of charge",
        "template_zh": "{vehicle}在{pad}完成充电，剩余电量比例为 {soc}",
        "category": "energy",
        "severity": "info",
        "entities": ["vehicle", "pad"],
        "position": "vehicle",
    },
    "lowalt.weather.hold": {
        "template": "Weather hold: {reason}",
        "template_zh": "因天气暂停飞行：{reason}",
        "category": "weather",
        "severity": "warning",
        "entities": ["vehicle", "zone"],
    },
    "lowalt.weather.release": {
        "template": "Weather released: {reason}",
        "template_zh": "天气限制已解除：{reason}",
        "category": "weather",
        "severity": "notice",
        "entities": ["vehicle", "zone"],
    },
    # Registry table spells the release alias without the lowalt prefix.
    "weather.release": {
        "template": "Weather released: {reason}",
        "template_zh": "天气限制已解除：{reason}",
        "category": "weather",
        "severity": "notice",
        "entities": ["vehicle", "zone"],
    },
}


def _chinese_label(label: str) -> str:
    """Legacy pack callers retain the same finite vocabulary, not a new copy."""
    if label in _LABELS_ZH:
        return _LABELS_ZH[label]
    match = _NUMBERED_LABEL.fullmatch(label)
    if match:
        return f"{_NUMBERED_LABELS_ZH[match[1]]} {match[2]}"
    match = re.fullmatch(r"(Incident drone|Route uav) (Alpha|Bravo|Bg \d+)", label)
    if match:
        family = "事故响应无人机" if match[1] == "Incident drone" else "无人机路线"
        name = match[2].replace("Bg ", "背景机 ")
        return f"{family} {name}"
    match = re.fullmatch(r"Road (task vehicle|vehicle) (reporter|(?:incident )?[ab])", label)
    if match:
        family = "道路车辆任务" if match[1] == "task vehicle" else "道路车辆"
        name = "报告车" if match[2] == "reporter" else match[2].replace("incident ", "事故 ")
        return f"{family} {name}"
    return label


def _identity(value: Any) -> str | None:
    if type(value) is str and value:
        return value
    if isinstance(value, Mapping):
        ref = value.get("$ref", value)
        if isinstance(ref, Mapping) and type(ref.get("id")) is str and ref["id"]:
            return ref["id"]
    return None


def _localized(en: str, zh: str) -> dict[str, str]:
    return {"en": en, "zh": zh}


def _enum(en: dict[str, str], zh: dict[str, str]) -> dict[str, dict[str, str]]:
    return {key: _localized(value, zh[key]) for key, value in en.items()}


def _record(members: dict[str, Any]) -> dict[str, Any]:
    return {"type": "record", "members": members, "required": [], "extra": True}


class TaskContext:
    """Only committed task kinds/endpoints; never an engine or apply-hook replay."""

    id = "lowalt.task-context"
    version = "1"
    fact_fields = ("lowalt.task_origin", "lowalt.task_destination")

    def __init__(self, schemas: tuple[str, ...], declarations: dict[str, Any]) -> None:
        self.input_schemas = schemas
        self.output_schemas = {}
        for schema in schemas:
            names = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", declarations[schema]["template"]))
            names.update(declarations[schema].get("entities", ()))
            if "position" in declarations[schema]:
                names.add(declarations[schema]["position"])
            members = {name: {"type": "string"} for name in names}
            members.update({name: {"type": "string"} for name in ("_kind", "_origin", "_destination")})
            if "latency_s" in members:
                members["latency_s"] = {"type": "number"}
            self.output_schemas[schema] = _record(members)

    def initial(self) -> dict[str, Any]:
        return {"task_kinds": {}, "task_info": {}}

    def consume(self, state: Any, record: Mapping[str, Any]) -> dict[str, Any] | None:
        if record["kind"] == "fact":
            endpoint = _identity(record["value"])
            if endpoint is None:
                raise ValueError(f"{record['field']}: facility identity required")
            slot = {"lowalt.task_origin": "origin", "lowalt.task_destination": "destination"}[record["field"]]
            state["task_info"].setdefault(record["entity"]["id"], {})[slot] = endpoint
            return None
        payload = json_data(record["payload"])
        task = _identity(payload.get("task"))
        if record["schema"] == "lowalt.task.created":
            kind = payload.get("kind")
            if kind not in TASK_KIND_CATEGORY or task is None:
                raise ValueError("lowalt.task.created: task and known kind required")
            state["task_kinds"][task] = kind
            state["task_info"][task] = {
                "kind": str(kind), "origin": _identity(payload.get("origin")) or "",
                "destination": _identity(payload.get("destination")) or "",
            }
        info = state["task_info"].get(task or "", {})
        return {"payload": {
            "_kind": info.get("kind", state["task_kinds"].get(task, "")),
            "_origin": info.get("origin", ""), "_destination": info.get("destination", ""),
            **payload,
        }}


_RESCUE_LINES = {
    "noticed": ("Possible rooftop rescue noticed", "发现屋顶疑似求援"),
    "arrived": ("{actor} reached the {role}", "{actor} 到达{role}"),
    "relay_ready": ("{actor} is holding the relay", "{actor} 已就位并保持中继"),
    "relay_interrupted": ("{actor} relay was interrupted", "{actor} 中继被中断"),
    "delivery_completed": ("{actor} completed delivery", "{actor} 完成物资投送"),
    "roles_released": ("Rescue roles released", "救援角色已释放"),
    "deadline_missed": ("Rescue deadline missed", "救援已超过截止时间"),
    "model_failed": ("Rescue decision failed", "救援决策失败"),
    "communication_failed": ("Rescue communication failed", "救援通信失败"),
    "communication_unavailable": ("Rescue radio is unavailable", "救援无线通信不可用"),
    "plan_rejected": ("Rescue plan rejected", "救援方案被拒绝"),
    "plan_computed": ("Plan: {assignments}", "方案：{assignments}"),
    "plan_empty": ("Plan: no further action", "方案：无需后续行动"),
    "plan_infeasible": ("No feasible rescue plan", "无可行救援方案"),
    "observation": ("{actor} observation: {evidence} (authored truth)", "{actor} 观测：{evidence}（作者设定真值）"),
    "report": ("Report received by command centre", "指挥中心收到复查报告"),
    "delivery_ack": ("Delivery acknowledgement received by command centre", "指挥中心收到投送回执"),
    "outcome_success": ("Rescue resolved successfully", "救援成功解决"),
    "outcome_failure": ("Rescue ended without success", "救援未成功"),
    "relay_handover": ("{actor} is handing over the relay to {relay}", "{actor} 正在向 {relay} 交接中继"),
    "execution_blocked": ("{actor} rescue execution blocked: {reason}", "{actor} 救援执行受阻：{reason}"),
}
_RESCUE_WARNINGS = {
    "model_failed", "communication_failed", "deadline_missed", "plan_rejected",
    "relay_interrupted", "communication_unavailable", "execution_blocked",
}


class RescueRecords:
    """Normalize the pack's nested JSON envelope, not rendered prose.

    Variants select templates in DATA. An ACK or link never becomes an outcome.
    """

    id = "lowalt.rescue-records"
    version = "1"
    input_schemas = ("rescue.event",)
    fact_fields: tuple[str, ...] = ()
    output_schemas = {"rescue.event": _record({
        **{name: {"type": "string"} for name in ("event", "vehicle", "actor", "role", "evidence", "relay", "reason")},
        "assignments": {"type": "array", "items": _record({"vehicle": {"type": "string"}, "role": {"type": "string"}})},
        "_links": {"type": "array", "items": {"type": "string"}},
    })}

    def initial(self) -> dict[str, Any]:
        return {}

    def consume(self, state: Any, record: Mapping[str, Any]) -> dict[str, Any]:
        payload = record["payload"]
        if type(payload.get("data_json")) is not str:
            raise ValueError("rescue.event: data_json string required")
        data = json_data(json.loads(payload["data_json"]))
        if not isinstance(data, dict) or type(data.get("kind")) is not str or not _identity(data.get("event")):
            raise ValueError("rescue.event: kind and event identity required")
        kind = data["kind"]
        vehicle = data.get("vehicle")
        if vehicle is not None and not _identity(vehicle):
            raise ValueError("rescue.event: vehicle identity required")
        data["actor"] = vehicle or ""
        links = [data["event"]]
        if vehicle:
            links.append(vehicle)
        if kind == "plan_computed":
            plan = data.get("plan")
            if not isinstance(plan, dict) or not isinstance(plan.get("assignments"), list) or len(plan["assignments"]) > 4096:
                raise ValueError("rescue.event: bounded plan assignments required")
            assignments = plan["assignments"]
            if plan.get("status") not in {"feasible", "infeasible"} or (assignments and plan["status"] != "feasible"):
                raise ValueError("rescue.event: explicit feasible/infeasible plan status required")
            for row in assignments:
                if not isinstance(row, dict) or not _identity(row.get("vehicle")) or row.get("role") not in {"inspect", "relay", "deliver"}:
                    raise ValueError("rescue.event: assignment vehicle and known role required")
            links.extend(row["vehicle"] for row in assignments)
            data["assignments"] = assignments
            kind = kind if assignments else "plan_empty" if plan.get("status") == "feasible" else "plan_infeasible"
        elif kind == "observation":
            if data.get("evidence") not in {"positive", "negative", "uncertain"}:
                raise ValueError("rescue.event: known observation evidence required")
        elif kind == "message_delivered":
            kind = data.get("message_kind")
            if kind not in {"report", "delivery_ack"}:
                raise ValueError("rescue.event: known message kind required")
        elif kind == "outcome":
            if type(data.get("success")) is not bool:
                raise ValueError("rescue.event: recorded boolean outcome required")
            kind = "outcome_success" if data["success"] else "outcome_failure"
        elif kind == "relay_handover":
            if not _identity(data.get("relay")):
                raise ValueError("rescue.event: relay identity required")
            links.append(data["relay"])
        elif kind == "execution_blocked" and type(data.get("reason")) is not str:
            raise ValueError("rescue.event: execution reason required")
        if kind not in _RESCUE_LINES:
            raise ValueError(f"rescue.event: unknown event kind {kind!r}")
        data["_links"] = links
        # An unspecified point is a generic noun, not an inferred role.
        data.setdefault("role", "")
        return {"payload": data, "variant": kind}


def _declarations() -> dict[str, Any]:
    # Overlay remains authoritative until its owning mission PR removes the
    # legacy exports. Do not maintain a second copy of that vocabulary.
    from .overlay import EVENT_NARRATIVES

    declarations = {key: dict(value) for key, value in LOWALT_DEFAULTS.items()}
    for key, value in EVENT_NARRATIVES.items():
        own = declarations.get(key, {})
        declarations[key] = {
            "template": value["text"], "template_zh": value["text_zh"],
            "category": "mixed" if value.get("category_by_kind") else value["category"],
            "severity": value["severity"], "entities": value.get("entities", own.get("entities", [])),
            **({"position": value.get("position", own.get("position"))} if "position" in value or "position" in own else {}),
        }
        if key.startswith("lowalt.task."):
            declarations[key].update(
                category_field="_kind", category_map=dict(TASK_KIND_CATEGORY),
                selectors={
                    "noun": {"path": ["_kind"], "prefer_payload": True, "enum": _enum(TASK_NOUN, TASK_NOUN_ZH), "fallback": _localized("task", "任务")},
                    "done": {"path": ["_kind"], "prefer_payload": True, "enum": _enum(TASK_DONE, TASK_DONE_ZH), "fallback": _localized("completed the task at", "完成任务")},
                    "origin": {"path": ["_origin"], "prefer_payload": True, "label": True, "fallback": _localized("its pickup", "取件点")},
                    "destination": {"path": ["_destination"], "prefer_payload": True, "label": True, "fallback": _localized("its destination", "目的地")},
                },
            )
    assignments = {"path": ["assignments"], "join": {
        "template": "{vehicle} {role}", "template_zh": "{vehicle} {role}",
        "selectors": {"vehicle": {"path": ["vehicle"], "label": True},
                      "role": {"path": ["role"], "enum": _enum({"inspect": "inspect", "relay": "relay", "deliver": "deliver"}, {"inspect": "复查", "relay": "中继", "deliver": "投送"})}},
        "separator": _localized(", ", "，"),
    }}
    selectors = {
        "actor": {"path": ["actor"], "label": True},
        "role": {"path": ["role"], "enum": _enum({"inspect": "inspection point", "relay": "relay point", "deliver": "delivery point"}, {"inspect": "复查点", "relay": "中继点", "deliver": "投送点"}), "fallback": _localized("rescue point", "救援点")},
        "evidence": {"path": ["evidence"], "enum": _enum({"positive": "person confirmed", "negative": "no person", "uncertain": "uncertain"}, {"positive": "确认有人", "negative": "无人", "uncertain": "不确定"})},
        "relay": {"path": ["relay"], "label": True}, "assignments": assignments,
        "reason": {"path": ["reason"], "enum": {key: _localized(key, value) for key, value in _ENUMS_ZH["reason"].items()}},
    }
    declarations["rescue.event"] = {
        "template": "Rescue event", "template_zh": "救援事件", "category": "emergency", "severity": "info",
        "entities": ["vehicle", "event"], "entity_list": "_links", "selectors": selectors,
        "variants": {kind: {"template": en, "template_zh": zh, "severity": "warning" if kind in _RESCUE_WARNINGS else "info"} for kind, (en, zh) in _RESCUE_LINES.items()},
    }
    return declarations


def plugin() -> PresentationPlugin:
    from .overlay import cooperation_overlay, overlay
    from .presentation_controls import CONTROL_READERS

    declarations = _declarations()
    enum_fields = {name: {value: _localized(value, label) for value, label in values.items()} for name, values in _ENUMS_ZH.items()}
    source_fields = {row["id"]: set(row["schema"].get("members", {}))
                     for row in (*overlay()["messages"], *cooperation_overlay()["messages"])}
    source_fields["weather.release"] = source_fields["lowalt.weather.release"]
    schemas = tuple(key for key in declarations if key.startswith("lowalt.task."))
    return PresentationPlugin(
        name="lowalt", api_version=1, revision="1",
        data={
            "format": PRESENTATION_FORMAT,
            "labels": {key: _localized(key, value) for key, value in _LABELS_ZH.items()},
            "numbered_labels": {key: _localized(key, value) for key, value in _NUMBERED_LABELS_ZH.items()},
            "label_groups": [
                {"prefix": _localized(prefix, zh), "names": {name: _localized(name, name) for name in ("Alpha", "Bravo")}, "numbered": {"Bg": _localized("Bg", "背景机")}}
                for prefix, zh in (("Incident drone", "事故响应无人机"), ("Route uav", "无人机路线"))
            ] + [
                {"prefix": _localized(prefix, zh), "names": {name: _localized(name, "报告车" if name == "reporter" else name.replace("incident ", "事故 ")) for name in ("reporter", "a", "b", "incident a", "incident b")}, "numbered": {}}
                for prefix, zh in (("Road task vehicle", "道路车辆任务"), ("Road vehicle", "道路车辆"))
            ],
            "enums": {key: {name: values for name, values in enum_fields.items() if name in fields}
                      for key, fields in source_fields.items()},
            "narratives": declarations,
            "header": {
                "engine_gusts": [{"plugin": "environment", "fields": ["lowalt.wind"]}],
                "lighting": [{"type_id": "lowalt:Environment", "field_id": "lowalt.light", "values": ["day", "dusk"]}],
            },
        },
        normalizers=(TaskContext(schemas, declarations), RescueRecords()),
        control_readers=CONTROL_READERS,
    )
