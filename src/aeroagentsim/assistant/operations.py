"""Strict, bilingual operations accepted by the scenario compiler."""

from __future__ import annotations

import copy
import math
from typing import Any

from aeroagentsim.packs.lowalt.overlay import TASK_KINDS

ROLES = {
    "meal_courier": ("parcel", ["food"], "Meal courier", "送餐无人机"),
    "parcel_drone": ("parcel", ["parcel"], "Parcel drone", "包裹无人机"),
    "medical_drone": ("parcel", ["medical"], "Medical drone", "医疗无人机"),
    "inspection_drone": ("inspection", ["inspection", "survey"], "Inspection drone", "巡检无人机"),
    "patrol_drone": ("patrol", ["patrol", "incident_response"], "Patrol drone", "巡逻无人机"),
    "air_taxi": ("passenger", ["passenger"], "Air taxi", "空中出租车"),
    "sprayer": ("spraying", ["spraying"], "Sprayer", "植保无人机"),
}
PLACE_TYPES = {
    "vertiport": "Vertiport", "charging_pad": "ChargingPad", "kitchen": "Restaurant",
    "customer": "Customer", "handoff_point": "HandoffPoint", "incident_site": "Site",
    "inspection_start": "Site", "inspection_end": "Site",
}


def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def number(minimum: float = 0, maximum: float | None = None, **extra: Any) -> dict[str, Any]:
    return {"type": "number", "minimum": minimum, **({"maximum": maximum} if maximum is not None else {}), **extra}


def enum(values: Any) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


STRING = {"type": "string", "minLength": 1}
IDENTIFIER = {**STRING, "description": "Stable scene identifier / 稳定场景标识"}
STRINGS = {"type": "array", "items": STRING, "minItems": 1, "uniqueItems": True}
POSITION = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
AT = {"anyOf": [STRING, POSITION]}
COUNT = {"type": "integer", "minimum": 1, "maximum": 1000}
KINDS = {**STRINGS, "items": enum(TASK_KINDS)}
MODE = enum(["prescribed", "agent"])
RELEASE = {"anyOf": [
    obj({"mode": enum(["scheduled"]), "times_s": {"type": "array", "items": number(), "minItems": 1}}, ("mode", "times_s")),
    obj({"mode": enum(["poisson"]), "rate_per_s": number(exclusiveMinimum=0), "start_s": number(default=0),
         "end_s": number(exclusiveMinimum=0), "burst_size": COUNT}, ("mode", "rate_per_s")),
]}
RESPONDER = obj({
    "mode": MODE, "goal": STRING,
    "order": {**STRINGS, "items": enum(["respond_now", "request_handoff", "finish_then_respond", "report_only"])},
    "rules": obj({"protected_task_kinds": {"type": "array", "items": enum(TASK_KINDS), "uniqueItems": True},
                  "require_replacement": {"type": "boolean"}, "min_battery_pct": number(0, 100),
                  "max_response_distance_m": number(exclusiveMinimum=0)}),
})
HELPERS = obj({
    "vehicles": STRINGS, "ask_radius_m": number(exclusiveMinimum=0), "mode": MODE, "goal": STRING,
    "rules": obj({"accept_when": enum(["idle", "always"]), "min_battery_pct": number(0, 100)}),
    "selection": enum(["nearest", "first"]),
})
HANDOFF = obj({"id": IDENTIFIER, "at": AT, "position": POSITION, "label": STRING, "label_zh": STRING}, ("id",))
COOPERATION = obj({
    "watchers": obj({"vehicles": STRINGS, "notice_radius_m": number(exclusiveMinimum=0)}),
    "responder": RESPONDER, "helpers": HELPERS,
    "handoff_points": {"type": "array", "items": {"anyOf": [STRING, HANDOFF]}},
    "photograph": obj({"dwell_s": number(exclusiveMinimum=0), "altitude_m": number(exclusiveMinimum=0)}),
})

OPERATION_SPECS: dict[str, dict[str, Any]] = {}


def _spec(name: str, topic: str, en: str, zh: str, schema: dict[str, Any], example: dict[str, Any]) -> None:
    OPERATION_SPECS[name] = {"name": name, "topic": topic, "description": en, "description_zh": zh,
                             "parameters": schema, "defaults": {k: v["default"] for k, v in schema["properties"].items() if "default" in v},
                             "example": {"id": "op-1", "op": name, "params": example}}


_spec("set_control", "controls", "Set a declared starting-point control.", "设置起点模板已声明的控件。", obj({"control": STRING, "value": {}}, ("control", "value")), {"control": "fleet_size", "value": 4})
_spec("set_duration", "controls", "Set the run horizon in seconds.", "设置仿真时长（秒）。", obj({"seconds": number(exclusiveMinimum=0)}, ("seconds",)), {"seconds": 360})
_spec("set_run_mode", "controls", "Choose scheduled or interactive operator input.", "选择预设或交互操作输入。", obj({"mode": enum(["scheduled", "interactive"])}, ("mode",)), {"mode": "scheduled"})
_spec("set_wind", "events", "Set the background wind speed.", "设置背景风速。", obj({"speed_mps": number()}, ("speed_mps",)), {"speed_mps": 3})
_spec("add_gust", "events", "Add a timed wind gust.", "添加定时阵风。", obj({"start_s": number(), "end_s": number(exclusiveMinimum=0), "speed_mps": number()}, ("start_s", "end_s", "speed_mps")), {"start_s": 60, "end_s": 90, "speed_mps": 9})
_spec("set_rule", "rules", "Change an operational safety or medical rule.", "修改运行安全或医疗规则。", obj({"rule": enum(["battery_reserve_pct", "safety_distance_m", "wind_limit_mps", "medical_preemption"]), "value": {"anyOf": [number(), {"type": "boolean"}]}}, ("rule", "value")), {"rule": "battery_reserve_pct", "value": 30})
_spec("set_algorithm", "algorithms", "Select an installed decision algorithm for a slot.", "为算法槽选择已安装的决策算法。", obj({"slot": enum(["allocation", "path_planning", "conflict_resolution", "charging"]), "value": STRING}, ("slot", "value")), {"slot": "allocation", "value": "nearest"})
place = {"id": IDENTIFIER, "role": enum(PLACE_TYPES), "at": AT, "label": STRING, "label_zh": STRING,
         "capacity": {**COUNT, "default": 1}, "preparation_s": number(exclusiveMinimum=0, default=30), "power_w": number(exclusiveMinimum=0, default=5000)}
_spec("add_place", "places", "Place a facility at a catalogue place or ENU position.", "在目录地点或 ENU 坐标放置设施。", obj(place, ("id", "role", "at")), {"id": "locker", "role": "handoff_point", "at": "east"})
_spec("update_place", "places", "Change a facility's position, label or capacity.", "修改设施的位置、名称或容量。", obj({k: v for k, v in place.items() if k != "role"}, ("id",)), {"id": "locker", "at": "north"})
_spec("remove_place", "places", "Remove an unused facility.", "删除未被使用的设施。", obj({"id": IDENTIFIER}, ("id",)), {"id": "locker"})
fleet = {"id": IDENTIFIER, "role": enum(ROLES), "count": COUNT, "start": AT,
         "battery_pct": number(0, 100), "payload_capacity_kg": number(exclusiveMinimum=0), "task_kinds": KINDS}
_spec("add_fleet", "participants", "Add an explicit group of UAVs with separate task eligibility.", "添加具有独立任务资格的无人机群。", obj(fleet, ("id", "role", "count", "start")), {"id": "logistics", "role": "parcel_drone", "count": 2, "start": "plaza"})
_spec("set_fleet", "participants", "Resize or adjust an existing UAV group.", "调整已有无人机群的数量或初始状态。", obj({"fleet": IDENTIFIER, **{k: fleet[k] for k in ("count", "battery_pct", "start")}}, ("fleet",)), {"fleet": "logistics", "count": 3})
_spec("set_vehicle", "participants", "Adjust one UAV's battery or starting point.", "调整一架无人机的电量或起点。", obj({"vehicle": IDENTIFIER, **{k: fleet[k] for k in ("battery_pct", "start")}}, ("vehicle",)), {"vehicle": "logistics-1", "battery_pct": 22})
orders = {"id": IDENTIFIER, "kind": enum(TASK_KINDS), "count": COUNT, "from": STRINGS, "to": STRINGS, "release": RELEASE,
          "priority": {"type": "integer", "minimum": 0, "default": 1}, "payload_kg": number(default=1), "delivery_window_s": number(exclusiveMinimum=0, default=240)}
_spec("add_orders", "tasks", "Schedule tasks between real facilities; meals require kitchens and work tasks require existing routes.", "在真实设施之间安排任务；餐食需要厨房，作业任务需要已有路线。", obj(orders, ("id", "kind", "count", "from", "to", "release")), {"id": "parcels", "kind": "parcel", "count": 2, "from": ["depot"], "to": ["customer-1"], "release": {"mode": "scheduled", "times_s": [0, 30]}})
_spec("set_orders", "tasks", "Revise an existing workload without replacing other workloads.", "修改已有工作量并保留其他工作量。", obj({k: v for k, v in orders.items() if k != "kind"}, ("id",)), {"id": "parcels", "priority": 5})
_spec("remove_orders", "tasks", "Remove a workload and its task entities.", "删除一组工作量及其任务实体。", obj({"id": IDENTIFIER}, ("id",)), {"id": "parcels"})
_spec("set_task", "tasks", "Change a single task's destination, release time or priority.", "修改单个任务的目的地、释放时间或优先级。", obj({"task": IDENTIFIER, "destination": STRING, "release_s": number(), "priority": {"type": "integer", "minimum": 0}}, ("task",)), {"task": "parcels-1", "release_s": 15})
incident = {"id": IDENTIFIER, "kind": enum(["person_fall"]), "at": AT, "time_s": number(), "label": STRING, "label_zh": STRING}
_spec("add_incident", "events", "Schedule a point incident such as a person falling.", "安排点状事件，例如行人跌倒。", obj(incident, ("id", "kind", "at", "time_s")), {"id": "fall", "kind": "person_fall", "at": "street-east", "time_s": 90})
_spec("set_incident", "events", "Revise a scheduled incident.", "修改已安排的事件。", obj(incident, ("id",)), {"id": "fall", "time_s": 120})
_spec("remove_incident", "events", "Remove a scheduled incident.", "删除已安排的事件。", obj({"id": IDENTIFIER}, ("id",)), {"id": "fall"})
_spec("schedule_event", "events", "Schedule a declared injection or lowalt operator event with its real payload.", "使用真实载荷安排已声明的注入点或低空操作事件。", obj({"event": STRING, "at_s": number(), "params": {"type": "object"}}, ("event", "at_s", "params")), {"event": "restaurant_delay", "at_s": 45, "params": {"restaurant": "restaurant-west", "delay_s": 20}})
_spec("set_cooperation", "responsibilities", "Merge who notices, decides and helps; agent choices need a connected model.", "合并观察、决策和协助责任；智能体决策需要已连接模型。", COOPERATION, {"watchers": {"vehicles": ["logistics"], "notice_radius_m": 150}, "helpers": {"vehicles": ["couriers"], "mode": "prescribed"}})
_spec("remove_cooperation", "responsibilities", "Remove cooperation behaviours and their incident setup.", "删除协同行为及其事件设置。", obj({}), {})


class OperationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


def validate_parameters(value: Any, schema: dict[str, Any], field: str = "params") -> None:
    """Validate the catalogue's JSON Schema subset without an added dependency."""
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            try:
                validate_parameters(value, choice, field)
                return
            except OperationError:
                pass
        raise OperationError(field, "Value does not match an allowed shape / 值不符合允许的格式")
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
             "number": type(value) in (int, float), "integer": type(value) is int, "boolean": type(value) is bool}
    if kind is not None and not valid[kind]:
        raise OperationError(field, f"Expected {kind} / 需要 {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise OperationError(field, f"Choose from {schema['enum']} / 请从这些选项中选择")
    if kind in {"number", "integer"}:
        if not math.isfinite(value):
            raise OperationError(field, "Finite number required / 需要有限数值")
        for bound, comparison in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b), ("exclusiveMinimum", lambda a, b: a <= b)):
            if bound in schema and comparison(value, schema[bound]):
                raise OperationError(field, f"Must satisfy {bound} {schema[bound]} / 数值必须满足此边界")
    if kind == "string" and len(value.strip()) < schema.get("minLength", 0):
        raise OperationError(field, "Nonempty text required / 需要非空文本")
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise OperationError(field, "Invalid array length / 列表长度无效")
        if schema.get("uniqueItems") and any(v in value[:i] for i, v in enumerate(value)):
            raise OperationError(field, "Duplicate entries / 存在重复项")
        for i, item in enumerate(value):
            validate_parameters(item, schema.get("items", {}), f"{field}[{i}]")
    if kind == "object":
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                raise OperationError(f"{field}.{key}", "Unknown parameter / 未知参数")
        for key in schema.get("required", []):
            if key not in value:
                raise OperationError(f"{field}.{key}", "Required parameter / 缺少必需参数")
        for key, item in value.items():
            validate_parameters(item, properties.get(key, {}), f"{field}.{key}")


def validate_operation(operation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(operation, dict):
        raise OperationError("operation", "Operation must be an object / 操作必须是对象")
    names = [k for k in ("op", "name", "operation") if k in operation]
    if len(names) != 1 or set(operation) - {names[0], "id", "explain", "params"}:
        raise OperationError("operation", "Supply id, op, params and optional explain / 提供 id、op、params 和可选 explain")
    name = operation[names[0]]
    if not isinstance(name, str) or name not in OPERATION_SPECS:
        raise OperationError("op", f"Unknown operation {name!r} / 未知操作")
    for key in ("id", "explain"):
        if key in operation:
            validate_parameters(operation[key], STRING, key)
    params = operation.get("params", {})
    validate_parameters(params, OPERATION_SPECS[name]["parameters"])
    return name, copy.deepcopy(params)


def operation_catalogue(topic: str | None = None) -> list[dict[str, Any]]:
    return copy.deepcopy([v for v in OPERATION_SPECS.values() if topic is None or v["topic"] == topic])
