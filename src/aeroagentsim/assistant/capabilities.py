"""Compact model-facing catalogue grounded in installed starting points."""

from __future__ import annotations

import copy
from typing import Any

from .operations import ROLES, operation_catalogue

TOPICS = {
    "places": ("Facilities and named places", "设施和命名地点"),
    "participants": ("UAV participants", "无人机参与者"), "tasks": ("Orders and work", "订单和工作"),
    "events": ("Weather and scheduled events", "天气和预设事件"), "responsibilities": ("Cooperation decisions", "协同决策"),
    "rules": ("Safety and medical rules", "安全和医疗规则"), "algorithms": ("Installed algorithms", "已安装算法"),
    "controls": ("Starting-point controls", "起点控件"),
}
LIMITS = [
    {"text": "Lowalt participants are UAVs; there are no ground courier or ground vehicle authoring operations.", "text_zh": "低空参与者为无人机；没有地面快递员或地面车辆创建操作。"},
    {"text": "Incidents are points, without simulated human biomechanics.", "text_zh": "事件为点状设置，不模拟人体生物力学。"},
    {"text": "Image artifacts require a configured city camera; a photograph event alone is not an image.", "text_zh": "照片文件需要已配置的城市相机；拍摄事件本身不是照片。"},
    {"text": "Plans configure drafts and versions. Confirm never initializes or starts a run. Expectations are conditional, and stories use committed events.", "text_zh": "计划配置草稿和版本。确认不会初始化或启动运行。预期为条件性的，故事使用已提交事件。"},
    {"text": "Meals need kitchens. Inspection, survey, spraying, patrol and incident-response orders require existing matching work routes.", "text_zh": "餐食需要厨房。巡检、测绘、喷洒、巡逻和事件响应订单需要已有匹配作业路线。"},
    {"text": "Agent decisions require a connected provider. One generated base-fleet member cannot be edited independently; use an explicit added fleet for individual initial states.", "text_zh": "智能体决策需要已连接服务。基础生成机群中的单个成员无法独立编辑；单机初始状态请使用新增显式机群。"},
]
BRIEF = {
    "set_control": ("Set template control", "设置模板控件"), "set_duration": ("Set duration", "设置时长"),
    "set_run_mode": ("Set input mode", "设置输入模式"), "set_wind": ("Set wind", "设置风速"),
    "add_gust": ("Add gust", "添加阵风"), "set_rule": ("Set operating rule", "设置运行规则"),
    "set_algorithm": ("Select installed algorithm", "选择已安装算法"), "add_place": ("Add facility", "添加设施"),
    "update_place": ("Edit facility", "修改设施"), "remove_place": ("Remove unused facility", "删除未用设施"),
    "add_fleet": ("Add UAV group", "添加无人机群"), "set_fleet": ("Adjust UAV group", "调整无人机群"),
    "set_vehicle": ("Adjust one explicit UAV", "调整显式单机"), "add_orders": ("Add task workload", "添加任务工作量"),
    "set_orders": ("Edit workload", "修改工作量"), "set_task": ("Edit one task", "修改单个任务"),
    "remove_orders": ("Remove workload", "删除工作量"), "add_incident": ("Schedule point incident", "安排点状事件"),
    "set_incident": ("Edit incident", "修改事件"), "remove_incident": ("Remove incident", "删除事件"),
    "schedule_event": ("Schedule declared injection", "安排已声明注入"),
    "set_cooperation": ("Merge cooperation choices", "合并协同决策"), "remove_cooperation": ("Remove cooperation", "删除协同配置"),
}


def _excerpt(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length].rstrip() + "…"


def capabilities(topic: str | None, *, templates, algorithms) -> dict:
    if topic is not None and topic not in TOPICS:
        raise ValueError("Unknown capability topic / 未知能力主题: " + topic)
    installed = algorithms() if callable(algorithms) else algorithms
    if not isinstance(installed, dict):
        raise TypeError("algorithms must be the installed slot catalogue / algorithms 必须是已安装算法槽目录")
    metadata = templates.list()
    if topic is None:
        operations = {}
        for name in TOPICS:
            operations[name] = {s["name"]: {"description": BRIEF[s["name"]][0], "description_zh": BRIEF[s["name"]][1]} for s in operation_catalogue(name)}
        return {"topics": list(TOPICS), "operations": operations,
                "detail": "Select a topic for parameter schemas, ranges, defaults and examples / 选择主题查看参数、范围、默认值和示例",
                "operation_shape": {"id": "op-N", "op": "operation name", "params": {}, "explain": "optional / 可选"},
                "algorithms": {slot: [a["id"] for a in rows] for slot, rows in installed.items()},
                "starting_points": [{"id": m["id"], "summary": _excerpt(m["summary"], 40), "summary_zh": _excerpt(m.get("summary_zh", m["summary"]), 16)} for m in metadata],
                "limits": [
                    {"text": "UAVs only; incidents are points. Photos require the city camera.", "text_zh": "仅无人机；事件为点。照片需要城市相机。"},
                    {"text": "Meals need kitchens; work tasks need existing routes; agent choices need a connected model.", "text_zh": "餐食需厨房；作业需已有路线；智能体需连接模型。"},
                    {"text": "Confirm saves a draft/version. Runs start separately. Stories use committed evidence. Individual base-fleet edits need an explicit fleet.", "text_zh": "确认保存草稿版本，运行另行启动。故事据已提交证据。基础单机修改需显式机群。"},
                ]}
    result: dict[str, Any] = {"topic": topic, "label": TOPICS[topic][0], "label_zh": TOPICS[topic][1],
                              "operations": operation_catalogue(topic), "limits": copy.deepcopy(LIMITS)}
    if topic == "algorithms":
        result["algorithms"] = copy.deepcopy(installed)
    if topic == "participants":
        result["roles"] = {name: {"vehicle_class": role[0], "task_kinds": role[1], "label": role[2], "label_zh": role[3]} for name, role in ROLES.items()}
    if topic == "controls":
        result["starting_points"] = [{"id": m["id"], "title": m["title"], "title_zh": m.get("title_zh", m["title"]), "controls": copy.deepcopy(m["knobs"])} for m in metadata]
    return result
