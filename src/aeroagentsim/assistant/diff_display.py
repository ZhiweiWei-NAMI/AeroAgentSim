"""Presentation metadata for operation diffs; raw identities and values survive."""

from __future__ import annotations

import re
from typing import Any

from .operations import ROLES


KIND_LABELS = {
    "fleet": ("Fleet", "机群"), "place": ("Facility", "设施"),
    "workload": ("Workload", "工作量"), "event": ("Event", "事件"),
    "rule": ("Rule", "规则"), "scene": ("Scene settings", "场景设置"),
    "components": ("Execution components", "执行组件"), "network": ("Road network", "道路网络"),
    "entity": ("Object", "对象"), "generator": ("Generator", "生成器"),
    "binding": ("Binding", "绑定"), "engine": ("Engine", "引擎"),
    "behaviour": ("Behaviour", "行为"), "relation": ("Relation", "关系"),
    "assignment": ("Assignment", "任务分配"),
}
FIELD_LABELS = {
    "wind.speed_mps": ("Wind speed", "风速"), "speed_mps": ("Wind speed", "风速"),
    "from_deg": ("Wind from direction (clockwise from north)", "风的来向（自北顺时针）"),
    "precipitation_mm_h": ("Rain (mm/h)", "降雨（毫米/小时）"),
    "visibility_m": ("Visibility (m)", "能见度（米）"),
    "light": ("Light", "光照"),
    "radius_m": ("Affected radius", "受影响半径"),
    "end_s": ("Ends at", "结束时间"),
    "east_mps": ("Eastward wind velocity", "向东风速分量"),
    "north_mps": ("Northward wind velocity", "向北风速分量"),
    "up_mps": ("Upward wind velocity", "向上风速分量"),
    "direction_deg": ("Wind travel direction (toward, counterclockwise from east)", "风的流向（吹向，自东向逆时针）"),
    "vectors": ("Wind velocity and direction", "风速分量与风向"),
    "after_speed_mps": ("Wind speed after event", "事件结束后风速"),
    "allocation": ("Allocation algorithm", "分配算法"),
    "path_planning": ("Route planning algorithm", "航线规划算法"),
    "conflict_resolution": ("Conflict resolution algorithm", "冲突消解算法"),
    "charging": ("Charging algorithm", "充电算法"),
    "duration_s": ("Duration", "时长"), "run_mode": ("Run mode", "运行模式"),
    "title": ("Title", "标题"), "city": ("City", "城市"),
    "count": ("Count", "数量"), "members": ("Members", "成员"),
    "member_states": ("Member initial states", "成员初始状态"),
    "battery_pct": ("Battery charge", "电量"), "battery_reserve_pct": ("Battery reserve", "预留电量"),
    "min_battery_pct": ("Minimum battery charge", "最低电量"),
    "soc": ("Battery charge fraction", "电量比例"),
    "safety_distance_m": ("Safety distance", "安全间隔"), "wind_limit_mps": ("Wind limit", "风速上限"),
    "medical_preemption": ("Medical task preemption", "医疗任务抢占"),
    "position": ("Position", "位置"), "start_position": ("Starting position", "起始位置"),
    "site_position": ("Facility position", "设施位置"), "incident_position": ("Incident position", "事件位置"),
    "points_enu_m": ("Route points", "路线坐标"), "origin": ("Geographic origin", "地理原点"),
    "bounds": ("Region bounds", "区域范围"), "lat": ("Latitude", "纬度"),
    "lon": ("Longitude", "经度"), "alt": ("Altitude", "海拔"),
    "start": ("Starting facility", "起始设施"), "place": ("Facility", "设施"),
    "role": ("Role", "角色"), "vehicle_class": ("Vehicle class", "飞行器类别"),
    "task_kinds": ("Eligible task types", "可执行任务类型"),
    "label": ("Name", "名称"), "label_zh": ("Chinese name", "中文名称"),
    "id": ("Identifier", "标识"), "type": ("Object type", "对象类型"),
    "kind": ("Type", "类型"), "contract": ("Object contract", "对象契约"),
    "fire": ("Fire", "火情"), "person_fall": ("Person fell", "行人跌倒"),
    "day": ("Day", "白天"), "dusk": ("Dusk", "黄昏"),
    "details": ("Initial properties", "初始属性"), "facts": ("Initial properties", "初始属性"),
    "tasks": ("Tasks", "任务"), "task_states": ("Task initial states", "任务初始状态"),
    "from": ("Origins", "出发点"), "to": ("Destinations", "目的地"),
    "destination": ("Destination", "目的地"), "release": ("Release schedule", "发布时间安排"),
    "mode": ("Mode", "模式"), "times_s": ("Release times", "发布时间"),
    "at_s": ("Event time", "事件时间"), "start_s": ("Start time", "开始时间"),
    "end_s": ("End time", "结束时间"), "release_s": ("Release time", "发布时间"),
    "rate_per_s": ("Arrival rate", "到达速率"), "expected_released": ("Expected releases", "预计发布数量"),
    "burst_size": ("Burst size", "批次大小"), "priority": ("Priority", "优先级"),
    "payload_kg": ("Payload", "载荷"), "payload_capacity_kg": ("Payload capacity", "载荷容量"),
    "capacity": ("Capacity", "容量"), "site_capacity": ("Facility capacity", "设施容量"),
    "preparation_s": ("Preparation time", "准备时长"), "food_preparation_s": ("Meal preparation time", "备餐时长"),
    "power_w": ("Charging power", "充电功率"), "charge_power_w": ("Charging power", "充电功率"),
    "pad_power_w": ("Charging power", "充电功率"),
    "protected": ("Protected task", "受保护任务"), "work_route": ("Work route", "作业路线"),
    "watchers": ("Observers", "观察者"), "responder": ("Responder", "响应者"),
    "helpers": ("Helpers", "协助者"), "vehicles": ("Vehicles", "飞行器"),
    "notice_radius_m": ("Observation radius", "观察半径"), "ask_radius_m": ("Help request radius", "求助半径"),
    "max_response_distance_m": ("Maximum response distance", "最大响应距离"),
    "require_replacement": ("Require a replacement", "需要替代执行者"),
    "protected_task_kinds": ("Protected task types", "受保护任务类型"),
    "photograph": ("Photograph", "拍照"), "dwell_s": ("Dwell time", "停留时长"),
    "altitude_m": ("Flight altitude", "飞行高度"), "goal": ("Goal", "目标"),
    "order": ("Response action", "响应行动"), "rules": ("Rules", "规则"),
    "selection": ("Helper selection", "协助者选择"), "accept_when": ("Accept help requests", "接受求助条件"),
    "params": ("Event parameters", "事件参数"), "gusts": ("Wind gusts", "阵风"),
    "zones": ("Airspace zones", "空域区域"), "effects": ("Effects", "影响"),
    "plugin": ("Algorithm", "算法"), "config": ("Parameters", "参数"),
    "engines": ("Engines", "引擎"),
    "wind_speed": ("Wind speed", "风速"), "wind_limit": ("Wind limit", "风速上限"),
    "order_rate": ("Order rate", "订单速率"), "fleet_size": ("Fleet size", "机群规模"),
}
UNITS_ZH = {"m/s": "米/秒", "m": "米", "s": "秒", "ns": "纳秒", "kg": "千克", "W": "瓦", "J": "焦耳", "%": "%", "°": "度", "/s": "每秒"}
ENUM_LABELS = {
    "scheduled": ("Scheduled", "预设"), "interactive": ("Interactive", "交互"),
    "poisson": ("Random arrivals (Poisson)", "随机到达（泊松）"),
    "agent": ("Agent decision", "智能体决策"), "prescribed": ("Prescribed rules", "预设规则"),
    "respond_now": ("Respond now", "立即响应"), "request_handoff": ("Request handoff", "请求交接"),
    "finish_then_respond": ("Finish task, then respond", "完成任务后响应"), "report_only": ("Report only", "仅报告"),
    "idle": ("When idle", "空闲时"), "always": ("Always", "始终"),
    "nearest": ("Nearest helper", "最近的协助者"), "first": ("First available helper", "首位可用协助者"),
    "fire": ("Fire", "火情"), "person_fall": ("Person fell", "行人跌倒"), "day": ("Day", "白天"), "dusk": ("Dusk", "黄昏"),
    **{name: labels[2:] for name, labels in ROLES.items()},
}
POSITION_FIELDS = {"position", "start_position", "site_position", "incident_position", "points_enu_m", "position_enu_m"}
DISPLAY_FIELDS = {"label", "label_zh", "before_text", "after_text"}


def _humanize(name: str) -> str:
    return re.sub(r"[_.:/]+", " ", name).strip().capitalize()


def _metadata(name: str, kind: str, identity: str) -> tuple[str, str, str | None]:
    key = name.rsplit(".", 1)[-1]
    if kind == "rule" and key == "value":
        key = identity
    if identity == "algorithms" and key == "plugin":
        key = name.split(".", 1)[0]
    en, zh = FIELD_LABELS.get(name, FIELD_LABELS.get(key, (_humanize(name), _humanize(name))))
    unit = {"lat": "°", "lon": "°", "bounds": "°", "direction_deg": "°", "from_deg": "°", "alt": "m", "wind_speed": "m/s", "wind_limit": "m/s", "order_rate": "/s", "rate_per_s": "/s", "precipitation_mm_h": "mm/h"}.get(key)
    if key in POSITION_FIELDS:
        unit = "m"
    if unit is None:
        unit = next((symbol for suffix, symbol in (("_mps", "m/s"), ("_pct", "%"), ("_kg", "kg"), ("_ns", "ns"), ("_s", "s"), ("_m", "m"), ("_w", "W"), ("_j", "J")) if key.endswith(suffix)), None)
    return en, zh, unit


def _number_text(value: int | float) -> str:
    # Geographic coordinates need more precision than general format's six
    # significant digits, or different bounds can look identical in a review.
    return str(value) if isinstance(value, int) else format(value, ".12g")


def _text(value: Any, name: str, kind: str, identity: str, algorithms: dict, *, zh: bool = False) -> str:
    if value is None or isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return "—"
    key = name.rsplit(".", 1)[-1]
    _, _, unit = _metadata(name, kind, identity)
    symbol = UNITS_ZH[unit] if zh and unit else unit
    if isinstance(value, bool):
        return ("开启" if value else "关闭") if zh else ("On" if value else "Off")
    if isinstance(value, (int, float)):
        return _number_text(value) + (f" {symbol}" if symbol else "")
    slot = name.split(".", 1)[0] if identity == "algorithms" else None
    if isinstance(value, str):
        if slot in algorithms and (key == slot or key == "plugin"):
            row = next((r for r in algorithms[slot] if r["id"] == value), None)
            if row is not None:
                return row.get("label_zh", row["label"]) if zh else row["label"]
        if key in {"mode", "run_mode", "role", "order", "selection", "accept_when", "kind", "light"} and value in ENUM_LABELS:
            return ENUM_LABELS[value][int(zh)]
        return value
    if isinstance(value, list):
        if key in POSITION_FIELDS and value and all(type(v) in (int, float) for v in value):
            return "(" + ", ".join(_number_text(v) for v in value) + f") {symbol}"
        return ", ".join(_text(v, name, kind, identity, algorithms, zh=zh) for v in value) if value else "—"
    if isinstance(value, dict):
        entries = []
        for child, content in value.items():
            child_name = f"{name}.{child}" if name else child
            en, translated, _ = _metadata(child_name, kind, identity)
            entries.append(f"{translated if zh else en}: {_text(content, child_name, kind, identity, algorithms, zh=zh)}")
        return "; ".join(entries) if entries else "—"
    return str(value)


def enrich_diff(difference: dict) -> dict:
    """Add readable copy once, including rows appended by the plan compiler."""
    algorithms = None
    for group in ("added", "changed", "removed"):
        for row in difference[group]:
            row["label"] = row.get("label") or row["id"]
            row["label_zh"] = row.get("label_zh") or row["id"]
            en, zh = KIND_LABELS.get(row["kind"], (_humanize(row["kind"]), _humanize(row["kind"])))
            row.update(kind_label=en, kind_label_zh=zh)
            fields = row.get("fields", [])
            # A parent summary repeats information when more specific fields exist.
            if fields:
                row["fields"] = [f for f in fields if not any(other["name"].startswith(f["name"] + ".") for other in fields)]
            for field in row.get("fields", []):
                if DISPLAY_FIELDS <= field.keys():
                    continue
                if row["id"] == "algorithms" and algorithms is None:
                    from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog

                    algorithms = discover_algorithm_catalog()
                name = field["name"]
                en, zh, unit = _metadata(name, row["kind"], row["id"])
                field.update(label=en + (f" ({unit})" if unit else ""),
                             label_zh=zh + (f"（{UNITS_ZH[unit]}）" if unit else ""))
                for side in ("before", "after"):
                    field[side + "_text"] = _text(field.get(side), name, row["kind"], row["id"], algorithms or {})
                    field[side + "_text_zh"] = _text(field.get(side), name, row["kind"], row["id"], algorithms or {}, zh=True)
    return difference
