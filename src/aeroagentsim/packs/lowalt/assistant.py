"""Low-altitude assistant operations: pure semantic edits and contributed reads."""

from __future__ import annotations

import copy
import difflib
import importlib
import math
import posixpath
import re
from functools import partial
from typing import Any

import yaml
from aerokernel.ids import EntityRef

from aeroagentsim.algorithms.llm import engine_config
from aeroagentsim.assistant.operations import (
    STRING, OperationError, enum, number, obj, validate_parameters,
)
from aeroagentsim.assistant.wind import from_deg
from aeroagentsim.assistant_api import (
    AssistantOperationPlugin, AssistantReadSpec, CompileContext, CompileResult,
    OperationSpec, Prerequisite, ResourceProposal, document_patches, thaw,
)
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.services.narratives import TASK_NOUN_ZH, _entity_id

from .everyday import _operators
from .overlay import TASK_KINDS, overlay
from .rules import WIND_LIMITS
from .tasks import FOOD_FIELDS
from .template import vehicle_facts


SECOND = 1_000_000_000

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
    "vertiport": "Vertiport", "charging_pad": "ChargingPad", "depot": "Depot", "kitchen": "Restaurant",
    "customer": "Customer", "handoff_point": "HandoffPoint", "incident_site": "Site",
    "inspection_start": "Site", "inspection_end": "Site",
}


IDENTIFIER = {**STRING, "description": "Stable scene identifier / 稳定场景标识"}
STRINGS = {"type": "array", "items": STRING, "minItems": 1, "uniqueItems": True}
POSITION = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
AT = {"anyOf": [STRING, POSITION], "description": "Facility id, catalogue place, centre/center/middle (ENU origin), or [east,north,up] metres / 设施标识、目录地点、中间（ENU 原点）或米制坐标"}
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


_spec("add_lowalt_components", "controls", "Install the standard lowalt engines, algorithm slots and rules without places, fleets or orders.", "安装标准低空引擎、算法槽和规则，不添加设施、机群或订单。", obj({}), {})
_spec("set_control", "controls", "Set a declared starting-point control.", "设置起点模板已声明的控件。", obj({"control": STRING, "value": {}}, ("control", "value")), {"control": "fleet_size", "value": 4})
_spec("set_duration", "controls", "Set the run horizon in seconds.", "设置仿真时长（秒）。", obj({"seconds": number(exclusiveMinimum=0)}, ("seconds",)), {"seconds": 360})
_spec("set_run_mode", "controls", "Choose scheduled or interactive operator input.", "选择预设或交互操作输入。", obj({"mode": enum(["scheduled", "interactive"])}, ("mode",)), {"mode": "scheduled"})
_spec("set_wind", "events", "Set the background wind speed and optionally the meteorological direction it comes FROM (clockwise from north).", "设置背景风速，可选设置风的来向（自北顺时针）。", obj({"speed_mps": number(), "from_deg": number(0, 360)}, ("speed_mps",)), {"speed_mps": 3, "from_deg": 270})
_spec("add_gust", "events", "Add a timed wind gust.", "添加定时阵风。", obj({"start_s": number(), "end_s": number(exclusiveMinimum=0), "speed_mps": number()}, ("start_s", "end_s", "speed_mps")), {"start_s": 60, "end_s": 90, "speed_mps": 9})
_spec("remove_gust", "events", "Remove a configured gust by its zero-based index or start time from matching wind profiles.", "按从零开始的索引或开始时间删除匹配风场中的阵风。",
      {**obj({"index": {"type": "integer", "minimum": 0}, "start_s": number()}),
       "anyOf": [obj({"index": {"type": "integer", "minimum": 0}}, ("index",)), obj({"start_s": number()}, ("start_s",))]}, {"start_s": 60})
_spec("set_weather", "events", "Set rain, visibility and time-of-day light. Rain, visibility and light: Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it. Wind and gusts are simulated inputs that drive flight holds and energy.", "设置降雨、能见度和时段光照。降雨、能见度与光照仅为视觉呈现——显示在三维视图和无人机拍摄中；飞行、感知和任务结果不使用它。风与阵风是驱动飞行暂停和能耗的仿真输入。",
      {"type": "object", "properties": {"precipitation_mm_h": number(0, 100), "visibility_m": {"anyOf": [number(50, 50000), {"type": "null"}]}, "light": enum(["day", "dusk"])}, "required": [], "additionalProperties": False},
      {"precipitation_mm_h": 4, "visibility_m": 500, "light": "day"})
_spec("set_rule", "rules", "Change an operational safety or medical rule.", "修改运行安全或医疗规则。", obj({"rule": enum(["battery_reserve_pct", "safety_distance_m", "wind_limit_mps", "medical_preemption"]), "value": {"anyOf": [number(), {"type": "boolean"}]}}, ("rule", "value")), {"rule": "battery_reserve_pct", "value": 30})
_spec("set_algorithm", "algorithms", "Select an installed decision algorithm for a slot.", "为算法槽选择已安装的决策算法。", obj({"slot": enum(["allocation", "path_planning", "conflict_resolution", "charging"]), "value": STRING}, ("slot", "value")), {"slot": "allocation", "value": "nearest"})
place = {"id": IDENTIFIER, "role": enum(PLACE_TYPES), "at": AT, "label": STRING, "label_zh": STRING,
         "capacity": {**COUNT, "default": 1},
         "preparation_s": number(exclusiveMinimum=0, default=30, description="Kitchens only (role kitchen / lowalt:Restaurant) / 仅厨房适用"),
         "power_w": number(exclusiveMinimum=0, default=5000, description="Charging pads only / 仅充电站适用")}
_spec("add_place", "places", "Place a facility at a catalogue place or ENU position.", "在目录地点或 ENU 坐标放置设施。", obj(place, ("id", "role", "at")), {"id": "locker", "role": "handoff_point", "at": "east"})
_spec("update_place", "places", "Change a facility's position, label or capacity.", "修改设施的位置、名称或容量。", obj({k: v for k, v in place.items() if k != "role"}, ("id",)), {"id": "locker", "at": "north"})
_spec("remove_place", "places", "Remove an unused facility.", "删除未被使用的设施。", obj({"id": IDENTIFIER}, ("id",)), {"id": "locker"})
fleet = {"id": IDENTIFIER, "role": enum(ROLES), "count": COUNT, "start": AT,
         "battery_pct": number(0, 100), "payload_capacity_kg": number(exclusiveMinimum=0), "task_kinds": KINDS}
_spec("add_fleet", "participants", "Add an explicit group of UAVs with separate task eligibility.", "添加具有独立任务资格的无人机群。", obj(fleet, ("id", "role", "count", "start")), {"id": "logistics", "role": "parcel_drone", "count": 2, "start": "plaza"})
_spec("set_fleet", "participants", "Resize or adjust an existing UAV group.", "调整已有无人机群的数量或初始状态。", obj({"fleet": IDENTIFIER, **{k: fleet[k] for k in ("count", "battery_pct", "start")}}, ("fleet",)), {"fleet": "logistics", "count": 3})
_spec("remove_fleet", "participants", "Remove a UAV group and its dependent references, preserving other fleets, places and workloads.", "删除无人机群及其依赖引用，保留其他机群、设施和工作量。", obj({"fleet": IDENTIFIER}, ("fleet",)), {"fleet": "logistics"})
_spec("set_vehicle", "participants", "Adjust one UAV's battery or starting point.", "调整一架无人机的电量或起点。", obj({"vehicle": IDENTIFIER, **{k: fleet[k] for k in ("battery_pct", "start")}}, ("vehicle",)), {"vehicle": "logistics-1", "battery_pct": 22})
orders = {"id": IDENTIFIER, "kind": enum(TASK_KINDS), "count": COUNT, "from": STRINGS, "to": STRINGS, "release": RELEASE,
          "priority": {"type": "integer", "minimum": 0, "default": 1}, "payload_kg": number(default=1), "delivery_window_s": number(exclusiveMinimum=0, default=240)}
_spec("add_orders", "tasks", "Schedule tasks between real facilities; meals require kitchens and work tasks require existing routes.", "在真实设施之间安排任务；餐食需要厨房，作业任务需要已有路线。", obj(orders, ("id", "kind", "count", "from", "to", "release")), {"id": "parcels", "kind": "parcel", "count": 2, "from": ["depot"], "to": ["customer-1"], "release": {"mode": "scheduled", "times_s": [0, 30]}})
_spec("set_orders", "tasks", "Revise an existing workload without replacing other workloads.", "修改已有工作量并保留其他工作量。", obj({k: v for k, v in orders.items() if k != "kind"}, ("id",)), {"id": "parcels", "priority": 5})
_spec("remove_orders", "tasks", "Remove a workload and its task entities.", "删除一组工作量及其任务实体。", obj({"id": IDENTIFIER}, ("id",)), {"id": "parcels"})
_spec("set_task", "tasks", "Change a single task's destination, release time or priority.", "修改单个任务的目的地、释放时间或优先级。", obj({"task": IDENTIFIER, "destination": STRING, "release_s": number(), "priority": {"type": "integer", "minimum": 0}}, ("task",)), {"task": "parcels-1", "release_s": 15})
incident = {"id": IDENTIFIER, "kind": enum(["person_fall", "fire"]), "at": AT, "time_s": number(), "label": STRING, "label_zh": STRING,
            "radius_m": number(1, 500, description="Affected-area radius; default 30 for fire, 6 for person_fall / 受影响半径"),
            "end_s": {"anyOf": [number(exclusiveMinimum=0), {"type": "null"}], "description": "Optional end time; must be after time_s and within the run; null removes the end (open-ended) / 可选结束时间；须晚于 time_s 且在运行内；null 表示移除结束（开放）"}}
_spec("add_incident", "events", "Schedule a point incident such as a person falling or a building fire. Fire smoke and flames are visual and scheduling markers; combustion and smoke spread are not simulated.", "安排点状事件，例如行人跌倒或建筑火情。火情的烟雾与火焰是视觉与调度标记；不模拟燃烧与烟雾扩散。", obj(incident, ("id", "kind", "at", "time_s")), {"id": "fire", "kind": "fire", "at": "street-east", "time_s": 90})
_spec("set_incident", "events", "Revise a scheduled incident.", "修改已安排的事件。", obj(incident, ("id",)), {"id": "fall", "time_s": 120})
_spec("remove_incident", "events", "Remove a scheduled incident.", "删除已安排的事件。", obj({"id": IDENTIFIER}, ("id",)), {"id": "fall"})
_spec("schedule_event", "events", "Schedule a declared injection or lowalt operator event with its real payload.", "使用真实载荷安排已声明的注入点或低空操作事件。", obj({"event": STRING, "at_s": number(), "params": {"type": "object"}}, ("event", "at_s", "params")), {"event": "restaurant_delay", "at_s": 45, "params": {"restaurant": "restaurant-west", "delay_s": 20}})
_spec("set_cooperation", "responsibilities", "Merge who notices, decides and helps; agent choices need a connected model.", "合并观察、决策和协助责任；智能体决策需要已连接模型。", COOPERATION, {"watchers": {"vehicles": ["logistics"], "notice_radius_m": 150}, "helpers": {"vehicles": ["couriers"], "mode": "prescribed"}})
_spec("remove_cooperation", "responsibilities", "Remove cooperation behaviours and their incident setup.", "删除协同行为及其事件设置。", obj({}), {})
_spec("set_region", "region", "Select WGS84 bounds from a configured OSM extract. Preview uses the Region map compiler; confirmation changes the ENU origin and invalidates the road network.", "从已配置 OSM 源选择 WGS84 范围。预览使用区域地图编译器；确认会改变 ENU 原点并使道路网络失效。",
      obj({"extract": STRING, "bounds": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
           "description": "[west, south, east, north], WGS84 degrees, wholly inside extract coverage / 必须完全位于源覆盖范围内"},
           "alt": {"type": "number", "default": 0}, "level_height_m": number(exclusiveMinimum=0, default=3.0)}, ("extract", "bounds")),
      {"extract": "wujiaochang", "bounds": [121.51, 31.301, 121.52, 31.305]})
OPERATION_SPECS["set_region"]["assistant_only"] = True


def _fail(field: str, en: str, zh: str) -> None:
    raise OperationError(field, en + " / " + zh)


def _engine(document: dict, plugin: str) -> tuple[str, dict]:
    engines = [(k, v["config"]) for k, v in document.get("engines", {}).items() if v.get("plugin") == plugin]
    if len(engines) != 1:
        _fail("base", f"Starting scene needs exactly one {plugin} engine", f"起点场景需要一个 {plugin} 引擎")
    return engines[0]


def _closest(identity: Any, candidates: list[str]) -> tuple[str, str]:
    # Models guess plausible ids ("vertiport-a", "meal-fleet"); naming the real ones lets them repair in one step.
    close = difflib.get_close_matches(str(identity), candidates, n=5, cutoff=0.45)
    return (f". Closest existing ids: {', '.join(close)}", "。相近的现有标识：" + "、".join(close)) if close else ("", "")


def _entity(document: dict, identity: str, field_name: str) -> dict:
    found = next((e for e in document["entities"] if e["id"] == identity), None)
    if found is None:
        en, zh = _closest(identity, [e["id"] for e in document["entities"]])
        _fail(field_name, f"Entity {identity!r} does not exist{en}", f"该实体不存在{zh}")
    return found


def _position(document: dict, value: Any, field_name: str, ctx: CompileContext) -> list[float]:
    if isinstance(value, list):
        return [float(v) for v in value]
    # Explicit scene ids take precedence over place catalogue ids.
    entity = next((e for e in document["entities"] if e["id"] == value), None)
    if entity is not None:
        for key in ("lowalt.site_position", "lowalt.position", "lowalt.incident_position"):
            if key in entity["facts"]:
                return list(entity["facts"][key])
        _fail(field_name, f"{value} has no lowalt position", "此对象没有低空空间坐标")
    if value.casefold() in {"center", "centre", "middle", "region_center", "enu_origin", "中间", "中心"}:
        # compile_scene centres its ENU frame on the selected region. With no
        # geodetic region this is still the authored local ENU origin.
        return [0.0, 0.0, 0.0]
    for engine in document.get("engines", {}).values():
        cooperation = engine.get("config", {}).get("cooperation", {})
        point = next((p for p in cooperation.get("handoff_points", []) + cooperation.get("incidents", []) if p["id"] == value), None)
        if point is not None:
            return list(point["position"])
    place = next((p for p in thaw(ctx.data.get("places", ())) if p["key"] == value), None)
    if place is None:
        en, zh = _closest(value, [p["key"] for p in thaw(ctx.data.get("places", ()))] + [e["id"] for e in document["entities"]])
        _fail(field_name, f"Unknown place {value!r}; use find_places{en}", f"地点不存在，请使用 find_places 查找{zh}")
    return list(place["position"])


def _unique(document: dict, identity: str, field_name: str = "id") -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", identity):
        _fail(field_name, "Use a stable alphanumeric identifier", "请使用稳定的字母数字标识")
    if any(e["id"] == identity for e in document["entities"]):
        _fail(field_name, f"Identifier {identity!r} is already used", "此标识已被使用")
    if _fleet_entities(document, identity):
        _fail(field_name, "Identifier is already used by a fleet", "此标识已被机群使用")
    cooperation = next((e["config"].get("cooperation") for e in document.get("engines", {}).values() if e["plugin"] == "lowalt_tasks"), None)
    if cooperation and any(p["id"] == identity for p in cooperation.get("handoff_points", []) + cooperation.get("incidents", [])):
        _fail(field_name, "Identifier is already used by cooperation", "此标识已被协同配置使用")
    fleet = document.get("generators", {}).get("fleet")
    if fleet and identity.startswith(fleet["prefix"]):
        _fail(field_name, "Identifier overlaps the generated base fleet prefix", "此标识与基础机群生成前缀冲突")


def _overlay(document: dict) -> None:
    for category, rows in overlay().items():
        selected = document["registry"].setdefault(category, [])
        known = {row["id"] for row in selected}
        selected.extend(copy.deepcopy(row) for row in rows if row["id"] not in known)


def _rule_package(document: dict, ctx: CompileContext) -> dict:
    for spec in document.get("behaviours", []):
        package = spec
        if "path" in spec:
            package = yaml.load(_resource(ctx, spec["path"]), Loader=UniqueLoader)
        if package.get("id") == "lowalt.rules":
            if package is not spec:
                document["behaviours"][document["behaviours"].index(spec)] = package
            return package
    _fail("rule", "Starting scene has no lowalt operational rule package", "起点场景没有低空运行规则包")


def _fleet_entities(document: dict, identity: str) -> list[dict]:
    """A plan fleet is its members `<fleet>-<n>` (generated base-fleet members are not a plan fleet)."""
    pattern = re.compile(rf"^{re.escape(identity)}-\d+$")
    generator = document.get("generators", {}).get("fleet")
    return [e for e in document["entities"] if e["type"] == "lowalt:UAV" and pattern.match(e["id"])
            and not (generator and e["id"].startswith(generator["prefix"]))]


def _vehicles(document: dict, values: list[str], field_name: str) -> list[str]:
    result: list[str] = []
    for value in values:
        members = _fleet_entities(document, value)
        base = document.get("generators", {}).get("fleet")
        if base and value in {"fleet", base["prefix"].rstrip("-.")}:
            ids = [base["prefix"] + str(i + 1) for i in range(base["count"])]
        elif members:
            ids = [e["id"] for e in members]
        else:
            entity = _entity(document, value, field_name)
            if entity["type"] != "lowalt:UAV":
                _fail(field_name, f"{value} is not a lowalt UAV", "此实体不是低空无人机")
            ids = [value]
        for identity in ids:
            if identity not in result:
                result.append(identity)
    return result


def _cooperation_module() -> Any:
    try:
        return importlib.import_module("aeroagentsim.packs.lowalt.cooperation")
    except ImportError as exc:
        raise OperationError("cooperation", "Cooperation capability is not installed; integrate packs/lowalt/cooperation.py / 协同能力尚未安装，请集成 cooperation.py") from exc


def _cooperation(document: dict) -> dict:
    _, tasks = _engine(document, "lowalt_tasks")
    if "cooperation" not in tasks:
        tasks["cooperation"] = {
            "handoff_points": [], "incidents": [],
            "watchers": {"vehicles": [], "notice_radius_m": 150.0},
            "responder": {"mode": "prescribed", "order": ["respond_now", "request_handoff", "finish_then_respond", "report_only"],
                          "rules": {"protected_task_kinds": ["medical"], "require_replacement": True,
                                    "min_battery_pct": 30.0, "max_response_distance_m": 600.0}},
            "helpers": {"vehicles": [], "ask_radius_m": 500.0, "mode": "prescribed",
                        "rules": {"accept_when": "idle", "min_battery_pct": 25.0}, "selection": "nearest"},
            "photograph": {"dwell_s": 5.0, "altitude_m": 35.0},
        }
    return tasks["cooperation"]


def _check_incident_time(incident: dict, document: dict, field_name: str) -> None:
    """Validate every final merged incident against rounded integer nanoseconds."""
    end_ns = round(incident["end_s"] * SECOND) if "end_s" in incident else None
    start_ns = round(incident.get("at_s", 0) * SECOND)
    until_ns = document["run"]["until_ns"]
    if end_ns is not None and end_ns <= start_ns:
        _fail(field_name, "Incident end must follow its start", "事件结束必须晚于开始")
    if end_ns is not None and end_ns > until_ns:
        _fail(field_name, f"Incident end is beyond the {until_ns / SECOND:g} s run", f"事件结束超出 {until_ns / SECOND:g} 秒运行")


def _drop_generated(document: dict, coop: dict) -> None:
    ids = {("lowalt:HandoffPoint", point["id"]) for point in coop.get("handoff_points", [])}
    for incident in coop.get("incidents", []):
        ids |= {("lowalt:Incident", incident["id"]), ("lowalt:Handoff", "handoff-" + incident["id"])}
    document["entities"] = [e for e in document["entities"] if (e["type"], e["id"]) not in ids]
    bindings = document.get("bindings", {})
    for key in ("rules", "lifecycle"):
        if key in bindings:
            bindings[key] = [row for row in bindings[key] if (row.get("type"), row.get("ids")) not in ids]


def _merge(target: dict, changes: dict) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _group_id(group: dict) -> str:
    if "id" in group:
        return group["id"]
    tasks = group["tasks"]
    if len(tasks) > 1 and all(re.sub(r"[-.]\d+$", "", t) == re.sub(r"[-.]\d+$", "", tasks[0]) for t in tasks):
        return re.sub(r"[-.]\d+$", "", tasks[0])
    return tasks[0] if tasks else group["kind"]


def _group(tasks: dict, identity: str) -> dict:
    matches = [g for g in tasks["generators"] if _group_id(g) == identity]
    if len(matches) != 1:
        _fail("id", f"Workload {identity!r} is absent or ambiguous; valid workload ids: {[_group_id(g) for g in tasks['generators']]}. Keep its add_orders creation and edit it with revise_plan.update, or add a set_orders edit after it", "工作量不存在或有歧义；请保留创建该工作量的 add_orders 操作并修改该操作，或在其后添加 set_orders")
    return matches[0]


def _arrivals(release: dict, count: int, duration_ns: int) -> dict:
    if release["mode"] == "scheduled":
        times = release["times_s"]
        if len(times) != count or times != sorted(times):
            _fail("release.times_s", "Provide one nondecreasing release time per task", "每个任务需要一个非递减释放时间")
        return {"mode": "scheduled", "times_ns": [round(t * SECOND) for t in times]}
    start = round(release.get("start_s", 0) * SECOND)
    end = round(release.get("end_s", duration_ns / SECOND) * SECOND)
    if end <= start:
        _fail("release.end_s", "Release end must follow start", "释放结束必须晚于开始")
    return {"mode": "poisson", "rate_per_s": float(release["rate_per_s"]), "start_ns": start, "end_ns": end,
            **({"burst_size": release["burst_size"]} if "burst_size" in release else {})}


def _endpoints(document: dict, values: list[str], field_name: str, food: bool = False) -> None:
    for identity in values:
        entity = _entity(document, identity, field_name)
        if "lowalt.site_position" not in entity["facts"]:
            _fail(field_name, f"{identity} is not a facility; add_place first", "此实体不是设施，请先使用 add_place")
        if food and entity["type"] != "lowalt:Restaurant":
            _fail(field_name, f"Meal origin {identity} must be a kitchen", "餐食起点必须是厨房")


def _food_bindings(document: dict, owner: str) -> None:
    for type_id, fields in (("lowalt:Restaurant", ["lowalt.food_preparation_s"]),
                            ("lowalt:FoodOrder", [f for f in FOOD_FIELDS if f != "lowalt.food_preparation_s"])):
        rule = next((r for r in document["bindings"]["rules"] if r["writer"] == owner and r["type"] == type_id), None)
        if rule is None:
            document["bindings"]["rules"].append({"writer": owner, "type": type_id, "fields": fields})
        else:
            rule["fields"] = list(dict.fromkeys([*rule["fields"], *fields]))


def _add_orders(document: dict, p: dict) -> None:
    owner, tasks = _engine(document, "lowalt_tasks")
    if any(_group_id(g) == p["id"] for g in tasks["generators"]):
        _fail("id", "Workload id is already used", "工作量标识已被使用")
    kind = p["kind"]
    if kind not in {"parcel", "food", "medical", "passenger"} and kind not in tasks.get("work_routes", {}):
        _fail("kind", f"{kind} needs a matching work route already authored in the base", "此任务类型需要起点场景中已有匹配作业路线")
    _endpoints(document, p["from"], "from", food=kind == "food")
    _endpoints(document, p["to"], "to")
    if any(not any(a != b for b in p["to"]) for a in p["from"]):
        _fail("to", "Every origin needs a distinct destination", "每个起点需要不同的目的地")
    group = {"id": p["id"], "kind": kind, "tasks": [f"{p['id']}-{i + 1}" for i in range(p["count"])],
             "origins": p["from"], "destinations": p["to"], "priority": p.get("priority", 1),
             "payload_kg": float(p.get("payload_kg", 1)), "arrivals": _arrivals(p["release"], p["count"], document["run"]["until_ns"])}
    if kind == "food":
        group["delivery_window_s"] = float(p.get("delivery_window_s", 240))
        _overlay(document)
        _food_bindings(document, owner)
    for i, identity in enumerate(group["tasks"]):
        _unique(document, identity)
        document["entities"].append({"id": identity, "type": "lowalt:FoodOrder" if kind == "food" else "lowalt:Task",
            "label": f"{kind.title()} order {i + 1}", "label_zh": f"{_task_label_zh(kind)} {i + 1}",
            "facts": {"lowalt.task_state": "unreleased", "lowalt.task_kind": kind, "lowalt.task_priority": group["priority"],
                      "lowalt.task_origin": p["from"][0], "lowalt.task_destination": p["to"][0], "lowalt.task_created_ns": 0,
                      "lowalt.task_vehicle": "", "lowalt.task_payload_kg": group["payload_kg"]}})
    tasks["generators"].append(group)


def _task_label_zh(kind: str) -> str:
    return TASK_NOUN_ZH[kind]


def _allocation_provider(ctx: CompileContext, document: dict) -> None:
    if document.get("algorithms", {}).get("allocation") != "llm":
        return
    if ctx.agent_profile is None:
        _fail("value", "Agent decisions need a connected model", "智能体决策需要已连接模型")
    _, tasks = _engine(document, "lowalt_tasks")
    configuration = {"provider": {"profile": ctx.agent_profile}}
    tasks.setdefault("algorithm_config", {})["allocation"] = configuration
    document["engines"]["lowalt_allocation"] = {"plugin": "langgraph", "config": engine_config(configuration)}


def _admit_task_kind(document: dict, kind: str) -> None:
    """Older starting points declare a narrower task-kind enum; admit a supported kind in canonical order."""
    for row in document.get("registry", {}).get("fields", []):
        enum = row.get("schema", {}).get("enum") if row.get("id") == "lowalt.task_kind" else None
        if isinstance(enum, list) and kind not in enum and kind in TASK_KINDS:
            row["schema"]["enum"] = [value for value in TASK_KINDS if value in enum or value == kind]


def _wind_vector(speed: float, from_deg: float, key: str) -> list:
    """ENU vector of the wind coming FROM from_deg (meteorological, clockwise from north)."""
    east, north = -speed * math.sin(math.radians(from_deg)), -speed * math.cos(math.radians(from_deg))
    east = 0.0 if abs(east) < speed * 1e-12 else east
    north = 0.0 if abs(north) < speed * 1e-12 else north
    return [east, north, 0.0] if key == "lowalt.wind" else [[east, north]]


def _sync_initial_wind(document: dict, profiles: list[tuple[str, str, dict]]) -> None:
    """An authored initial wind must equal its profile at t=0, including a zero-start gust."""
    for identity, key, profile in profiles:
        entity = _entity(document, identity, "speed_mps")
        if key in entity["facts"]:
            value = next((g["value"] for g in profile.get("gusts", []) if g["start_ns"] <= 0 < g["end_ns"]), profile["value"])
            entity["facts"][key] = copy.deepcopy(value)


def _apply_operation(ctx: CompileContext, document: dict, name: str, p: dict,
                     warnings: list[dict], op_id: str | None) -> list[dict] | None:
    if name == "set_duration":
        document["run"]["until_ns"] = round(p["seconds"] * SECOND)
        if document["run"]["until_ns"] < 1:
            _fail("seconds", "Duration must be at least one nanosecond", "时长必须至少为一纳秒")
        for stream in document.get("ingress_streams", []):
            if stream["initial_watermark_ns"] != 0:
                stream["initial_watermark_ns"] = document["run"]["until_ns"]
        for tasks in [e["config"] for e in document["engines"].values() if e["plugin"] == "lowalt_tasks"]:
            for incident in tasks.get("cooperation", {}).get("incidents", []):
                _check_incident_time(incident, document, "seconds")
        return
    if name == "set_run_mode":
        streams = document.get("ingress_streams", [])
        if not any(stream["id"] == "operator" for stream in streams):
            _fail("mode", "Starting scene has no interactive ingress stream", "起点场景没有交互输入流")
        for stream in streams:
            if stream["id"] == "operator":
                stream["initial_watermark_ns"] = 0 if p["mode"] == "interactive" else document["run"]["until_ns"]
                stream.pop("timeout_s", None)
        return
    if name in {"set_wind", "add_gust", "remove_gust"}:
        profiles = [(identity, key, value) for e in document["engines"].values() if e["plugin"] == "environment"
                    for identity, profile in e["config"].get("profiles", {}).items() for key, value in profile.items()
                    if key in {"lowalt.wind", "oo:digital_twin.wind.horizontalVelocity"}]
        if not profiles:
            _fail("params" if name == "remove_gust" else "speed_mps", "Starting scene has no lowalt wind profile", "起点场景没有低空风场配置")
        if name == "remove_gust":
            selector = "index" if "index" in p else "start_s"
            matches = []
            for _, _, profile in profiles:
                gusts = profile.get("gusts", [])
                indices = ([p["index"]] if p["index"] < len(gusts) else []) if selector == "index" else [
                    index for index, gust in enumerate(gusts) if gust["start_ns"] == round(p["start_s"] * SECOND)]
                if len(indices) > 1:
                    _fail(selector, "Gust start is ambiguous; select its index", "阵风开始时间不唯一，请使用索引")
                if indices:
                    matches.append((profile, indices[0]))
            if not matches:
                _fail(selector, "No configured gust matches this selection", "没有匹配此选择的已配置阵风")
            for profile, index in matches:
                del profile["gusts"][index]
                if not profile["gusts"]:
                    profile.pop("gusts")
                    profile["mode"] = "constant"
            _sync_initial_wind(document, profiles)
            return
        if name == "add_gust" and p["end_s"] <= p["start_s"]:
            _fail("end_s", "Gust end must follow start", "阵风结束必须晚于开始")
        if name == "add_gust" and round(p["end_s"] * SECOND) <= round(p["start_s"] * SECOND):
            _fail("end_s", "Gust interval must span at least one nanosecond", "阵风间隔必须至少一纳秒")
        for _, key, profile in profiles:
            if name == "set_wind" and "from_deg" in p:
                vector = _wind_vector(p["speed_mps"], p["from_deg"], key)
            else:
                current = profile["value"] if key == "lowalt.wind" else profile["value"][0]
                direction = from_deg(current[0], current[1])
                vector = _wind_vector(p["speed_mps"], 270 if direction is None else direction, key)
            if name == "set_wind":
                profile["value"] = vector
            else:
                if any(p["start_s"] * SECOND < g["end_ns"] and p["end_s"] * SECOND > g["start_ns"] for g in profile.get("gusts", [])):
                    _fail("start_s", "Gust overlaps an existing interval; choose a separate interval", "阵风与已有时段重叠，请选择独立时段")
                profile["mode"] = "gust"
                profile.setdefault("gusts", []).append({"start_ns": round(p["start_s"] * SECOND), "end_ns": round(p["end_s"] * SECOND), "value": vector})
        _sync_initial_wind(document, profiles)
        return
    if name == "set_weather":
        # Visual-only presentation journaled on the existing lowalt:Environment
        # entity that already carries wind (spec §6): AeroGraph's
        # oo:PrecipitationField is a separate field-type entity and visibility
        # has no environmental-field declaration, so no second weather entity.
        _overlay(document)
        environments_with_profiles = [(name, engine["config"]) for name, engine in document["engines"].items()
                                      if engine["plugin"] == "environment"]
        environments = [e for e in document["entities"] if e["type"] == "lowalt:Environment"]
        configured = [(name, config) for name, config in environments_with_profiles
                      if any(e["id"] in config.get("profiles", {}) for e in environments)]
        if not configured:
            _fail("base", "Starting scene has no lowalt weather entity", "起点场景没有低空气象实体")
        owner, config = configured[0]
        environments = [e for e in environments if e["id"] in config["profiles"]]
        values = {}
        if "precipitation_mm_h" in p:
            values["lowalt.precipitation"] = float(p["precipitation_mm_h"]) / 3600  # kg m-2 s-1
        if "visibility_m" in p:
            values["lowalt.visibility"] = 50000.0 if p["visibility_m"] is None else float(p["visibility_m"])
        if "light" in p:
            values["lowalt.light"] = p["light"]
        for key, value in values.items():
            if key not in config["produces"]:
                config["produces"].append(key)
            for environment in environments:
                config["profiles"].setdefault(environment["id"], {})[key] = {"mode": "constant", "value": value}
                environment["facts"][key] = copy.deepcopy(value)
            rule = next((r for r in document["bindings"].setdefault("rules", []) if r.get("writer") == owner and r.get("type") == "lowalt:Environment"), None)
            if rule is None:
                document["bindings"].setdefault("rules", []).append({"writer": owner, "type": "lowalt:Environment", "fields": [key]})
            elif key not in rule["fields"]:
                rule["fields"].append(key)
        return
    if name == "set_algorithm":
        installed = thaw(ctx.data.get("algorithms", {}))
        if p["value"] not in {a["id"] for a in installed[p["slot"]]}:
            _fail("value", f"Valid algorithms for slot {p['slot']}: {[a['id'] for a in installed[p['slot']]]}", "请选择此槽已安装的算法")
        if "algorithms" not in document:
            _fail("slot", "Starting scene has no lowalt algorithm slots", "起点场景没有低空算法槽")
        if p["value"] == "llm" and ctx.agent_profile is None:
            _fail("value", "Agent decisions need a connected model", "智能体决策需要已连接模型")
        document["algorithms"][p["slot"]] = p["value"]
        if p["value"] == "llm":
            _allocation_provider(ctx, document)
        return
    if name == "set_rule":
        value, rule = p["value"], p["rule"]
        if rule == "medical_preemption":
            if type(value) is not bool:
                _fail("value", "medical_preemption requires true or false", "医疗抢占规则需要布尔值")
            _engine(document, "lowalt_tasks")[1][rule] = value
            return
        if type(value) not in (int, float) or (rule == "battery_reserve_pct" and not 0 <= value <= 100) or (rule == "safety_distance_m" and value <= 0):
            _fail("value", "Use a valid numeric rule value (reserve 0–100; distance > 0)", "请使用有效规则数值（预留电量 0–100；间隔大于 0）")
        predicates = _rule_package(document, ctx)["predicates"]
        if rule == "battery_reserve_pct":
            predicates["battery_below_reserve"]["parameters"]["reserve_percent"] = float(value)
        elif rule == "safety_distance_m":
            predicates["closer_than_safety_distance"]["parameters"]["horizontal_m"] = float(value)
        else:
            parameters = predicates["wind_above_limit"]["parameters"]
            for key in parameters:
                parameters[key] = float(value)
        return
    if name in {"add_place", "update_place", "remove_place"}:
        if name == "add_place":
            _engine(document, "lowalt_tasks")
            _unique(document, p["id"])
            if "preparation_s" in p and p["role"] != "kitchen":
                _fail("preparation_s", "Preparation time applies only to kitchens", "准备时间只适用于厨房")
            if "power_w" in p and p["role"] != "charging_pad":
                _fail("power_w", "Charging power applies only to charging pads", "充电功率只适用于充电位")
            if p["role"] == "handoff_point":
                if "capacity" in p:
                    _fail("capacity", "Handoff storage capacity is not modeled", "交接储物容量尚未建模")
                coop = _cooperation(document)
                coop["handoff_points"].append({"id": p["id"], "position": _position(document, p["at"], "at", ctx),
                    "label": p.get("label", p["id"]), "label_zh": p.get("label_zh", "交接点 " + p["id"])})
                return
            _overlay(document)
            # The role is the contract type (lowalt:Restaurant, lowalt:Customer…); a separate role fact would
            # need a writer binding the simulator does not have.
            facts = {"lowalt.site_position": _position(document, p["at"], "at", ctx), "lowalt.site_open": True, "lowalt.site_capacity": p.get("capacity", 1)}
            if p["role"] == "kitchen":
                facts["lowalt.food_preparation_s"] = float(p.get("preparation_s", 30))
                # A base without meals has no writer for kitchen facts until one is declared.
                _food_bindings(document, _engine(document, "lowalt_tasks")[0])
            if p["role"] == "charging_pad":
                facts["lowalt.pad_power_w"] = float(p.get("power_w", 5000))
            document["entities"].append({"id": p["id"], "type": "lowalt:" + PLACE_TYPES[p["role"]],
                "label": p.get("label", p["id"]), "label_zh": p.get("label_zh", p["id"]), "facts": facts})
            return
        _, tasks = _engine(document, "lowalt_tasks")
        handoffs = tasks.get("cooperation", {}).get("handoff_points", [])
        point = next((r for r in handoffs if r["id"] == p["id"]), None)
        if point is not None:
            for key in ("capacity", "preparation_s", "power_w"):
                if key in p:
                    _fail(key, "This setting is not modeled on handoff points", "交接点不模拟此设置")
            if name == "remove_place":
                if tasks["cooperation"].get("incidents"):
                    _fail("id", "Handoff point is used by cooperation; revise cooperation first", "交接点正被协同配置使用，请先修改协同配置")
                handoffs.remove(point)
            else:
                if "at" in p:
                    point["position"] = _position(document, p["at"], "at", ctx)
                for key in ("label", "label_zh"):
                    if key in p:
                        point[key] = p[key]
            return
        entity = _entity(document, p["id"], "id")
        if "lowalt.site_position" not in entity["facts"]:
            _fail("id", "Select a facility", "请选择设施")
        if name == "remove_place":
            # Search authored references outside the entity being removed.
            rest = copy.deepcopy(document)
            rest["entities"].remove(next(e for e in rest["entities"] if e["id"] == p["id"]))
            def referenced(value: Any) -> bool:
                if isinstance(value, dict):
                    return any(referenced(v) for v in value.values())
                if isinstance(value, list):
                    return any(referenced(v) for v in value)
                return value == p["id"]
            if referenced(rest):
                _fail("id", "Place is referenced by tasks, rules or events; revise those first", "地点被任务、规则或事件引用，请先修改引用")
            document["entities"].remove(entity)
        else:
            if "at" in p:
                entity["facts"]["lowalt.site_position"] = _position(document, p["at"], "at", ctx)
            for key in ("label", "label_zh"):
                if key in p:
                    entity[key] = p[key]
            for key, fact in (("capacity", "lowalt.site_capacity"), ("preparation_s", "lowalt.food_preparation_s"), ("power_w", "lowalt.pad_power_w")):
                if key in p:
                    if key == "preparation_s" and entity["type"] != "lowalt:Restaurant" or key == "power_w" and entity["type"] != "lowalt:ChargingPad":
                        _fail(key, "Parameter does not belong to this facility type", "此参数不属于该设施类型")
                    entity["facts"][fact] = p[key] if key == "capacity" else float(p[key])
        return
    if name == "add_fleet":
        _, tasks = _engine(document, "lowalt_tasks")
        if _fleet_entities(document, p["id"]):
            _fail("id", "Fleet id already used", "机群标识已被使用")
        _unique(document, p["id"])
        _overlay(document)
        vehicle_class, kinds, label, label_zh = ROLES[p["role"]]
        template = document.get("generators", {}).get("fleet")
        initial = template["facts"] if template is not None else vehicle_facts()
        base_kinds = tasks.setdefault("vehicle_kinds", {}).get(initial["lowalt.vehicle_class"], list(kinds))
        overrides = tasks.setdefault("vehicle_task_kinds", {})
        for i in range(template["count"] if template is not None else 0):
            overrides.setdefault(template["prefix"] + str(i + 1), list(base_kinds))
        tasks["vehicle_kinds"].setdefault(vehicle_class, list(kinds))
        tasks.setdefault("cruise_altitudes", {}).setdefault(vehicle_class, 125.0)
        for i in range(p["count"]):
            identity = f"{p['id']}-{i + 1}"
            _unique(document, identity)
            facts = copy.deepcopy(initial)
            facts.update({"lowalt.position": _position(document, p["start"], "start", ctx), "lowalt.vehicle_class": vehicle_class,
                          "lowalt.wind_limit_mps": WIND_LIMITS[vehicle_class]})
            if "battery_pct" in p:
                facts["lowalt.soc"] = p["battery_pct"] / 100
                facts["lowalt.motion_energy_j"] = facts["lowalt.capacity_j"] * facts["lowalt.soc"]
            facts["lowalt.payload_capacity_kg"] = float(p.get("payload_capacity_kg", 150 if vehicle_class == "passenger" else initial["lowalt.payload_capacity_kg"]))
            document["entities"].append({"id": identity, "type": "lowalt:UAV", "label": f"{label} {i + 1}", "label_zh": f"{label_zh} {i + 1}", "vehicle_class": vehicle_class, "facts": facts})
            overrides[identity] = list(p.get("task_kinds", kinds))
        return
    if name in {"set_fleet", "set_vehicle"}:
        _, tasks = _engine(document, "lowalt_tasks")
        fleet = document.get("generators", {}).get("fleet")
        if name == "set_fleet" and fleet and p["fleet"] in {"fleet", fleet["prefix"].rstrip("-.")}:
            if "count" in p:
                fleet["count"] = p["count"]
                old_overrides = tasks.get("vehicle_task_kinds", {})
                base_kinds = tasks.get("vehicle_kinds", {}).get(fleet["facts"]["lowalt.vehicle_class"])
                if base_kinds is not None:
                    for i in range(fleet["count"]):
                        old_overrides.setdefault(fleet["prefix"] + str(i + 1), list(base_kinds))
            if "battery_pct" in p:
                fleet["facts"]["lowalt.soc"] = p["battery_pct"] / 100
                fleet["facts"]["lowalt.motion_energy_j"] = fleet["facts"]["lowalt.capacity_j"] * p["battery_pct"] / 100
            if "start" in p:
                fleet["starts"] = [_position(document, p["start"], "start", ctx)]
            return
        if name == "set_vehicle":
            if fleet and p["vehicle"].startswith(fleet["prefix"]):
                _fail("vehicle", "Base fleet members are regenerated from one prototype; adjust set_fleet or add an explicit fleet for individual states", "基础机群成员由单一原型重新生成；请调整 set_fleet 或添加显式机群以设置单机状态")
            members = [_entity(document, p["vehicle"], "vehicle")]
            if members[0]["type"] != "lowalt:UAV":
                _fail("vehicle", "Select a lowalt UAV", "请选择低空无人机")
        else:
            members = _fleet_entities(document, p["fleet"])
            if not members:
                prefixes = sorted({re.sub(r"-\d+$", "", e["id"]) for e in document["entities"] if e.get("type") == "lowalt:UAV"})
                en, zh = _closest(p["fleet"], prefixes)
                _fail("fleet", f"Fleet does not exist; use its summary id{en}", f"机群不存在，请使用摘要中的标识{zh}")
            count = p.get("count", len(members))
            if count > len(members):
                prototype = members[0]
                for i in range(len(members), count):
                    identity = f"{p['fleet']}-{i + 1}"
                    _unique(document, identity)
                    entity = copy.deepcopy(prototype)
                    entity["id"] = identity
                    # Members are named "<role label> <n>"; new members continue the numbering.
                    entity.update(label=re.sub(r"\s*\d+$", "", prototype.get("label", p["fleet"])) + f" {i + 1}",
                                  label_zh=re.sub(r"\s*\d+$", "", prototype.get("label_zh", p["fleet"])) + f" {i + 1}")
                    document["entities"].append(entity)
                    members.append(entity)
                    tasks.setdefault("vehicle_task_kinds", {})[identity] = list(tasks["vehicle_task_kinds"][prototype["id"]])
            elif count < len(members):
                removed = {e["id"] for e in members[count:]}
                coop = tasks.get("cooperation", {})
                if removed & set(coop.get("watchers", {}).get("vehicles", []) + coop.get("helpers", {}).get("vehicles", [])):
                    _fail("count", "Removed vehicles are referenced by cooperation; revise responsibilities first", "待删除无人机被协同配置引用，请先调整责任")
                document["entities"] = [e for e in document["entities"] if e["id"] not in removed]
                for identity in removed:
                    tasks.get("vehicle_task_kinds", {}).pop(identity, None)
                members = members[:count]
        for entity in members:
            if "battery_pct" in p:
                entity["facts"]["lowalt.soc"] = p["battery_pct"] / 100
                entity["facts"]["lowalt.motion_energy_j"] = entity["facts"]["lowalt.capacity_j"] * p["battery_pct"] / 100
            if "start" in p:
                entity["facts"]["lowalt.position"] = _position(document, p["start"], "start", ctx)
        return
    if name == "add_orders":
        _admit_task_kind(document, p["kind"])
        _add_orders(document, p)
        return
    if name in {"set_orders", "remove_orders"}:
        _, tasks = _engine(document, "lowalt_tasks")
        group = _group(tasks, p["id"])
        if name == "remove_orders":
            task_ids = set(group["tasks"])
            if any(_entity_id(c.get("payload", {}).get("payload", {}).get("task")) in task_ids for c in document["bindings"].get("commands", [])):
                _fail("id", "Workload is referenced by a scheduled operator event", "工作量被预设操作事件引用")
            document["entities"] = [e for e in document["entities"] if e["id"] not in task_ids]
            tasks["generators"].remove(group)
            return
        if "delivery_window_s" in p and group["kind"] != "food":
            _fail("delivery_window_s", "Delivery windows apply only to meal workloads", "配送时间窗只适用于餐食工作量")
        release = p.get("release")
        if release is None:
            arrivals = group["arrivals"]
            release = {"mode": arrivals["mode"]}
            if arrivals["mode"] == "scheduled":
                release["times_s"] = [t / SECOND for t in arrivals["times_ns"]]
            else:
                release.update(rate_per_s=arrivals["rate_per_s"], start_s=arrivals["start_ns"] / SECOND)
                if "end_ns" in arrivals:
                    release["end_s"] = arrivals["end_ns"] / SECOND
                if "burst_size" in arrivals:
                    release["burst_size"] = arrivals["burst_size"]
        new = {"id": p["id"], "kind": group["kind"], "count": p.get("count", len(group["tasks"])),
               "from": p.get("from", group["origins"]), "to": p.get("to", group["destinations"]), "release": release,
               "priority": p.get("priority", group["priority"]), "payload_kg": p.get("payload_kg", group["payload_kg"])}
        if group["kind"] == "food":
            new["delivery_window_s"] = p.get("delivery_window_s", group["delivery_window_s"])
        old_ids = set(group["tasks"])
        old_entities = {e["id"]: copy.deepcopy(e) for e in document["entities"] if e["id"] in old_ids}
        document["entities"] = [e for e in document["entities"] if e["id"] not in old_ids]
        tasks["generators"].remove(group)
        _add_orders(document, new)
        for i, entity in enumerate(document["entities"]):
            if entity["id"] in old_entities:
                original = old_entities[entity["id"]]
                for parameter, fact in (("priority", "lowalt.task_priority"), ("payload_kg", "lowalt.task_payload_kg"),
                                        ("from", "lowalt.task_origin"), ("to", "lowalt.task_destination")):
                    if parameter in p:
                        original["facts"][fact] = entity["facts"][fact]
                document["entities"][i] = original
        return
    if name == "set_task":
        _, tasks = _engine(document, "lowalt_tasks")
        entity = _entity(document, p["task"], "task")
        group = next((g for g in tasks["generators"] if p["task"] in g["tasks"]), None)
        if group is None:
            _fail("task", "Select a lowalt task from a workload", "请选择工作量中的低空任务")
        group.setdefault("id", _group_id(group))
        single = copy.deepcopy(group)
        index = group["tasks"].index(p["task"])
        single["tasks"] = [p["task"]]
        single["id"] = p["task"]
        if group["arrivals"]["mode"] == "scheduled":
            time_ns = group["arrivals"]["times_ns"].pop(index)
            single["arrivals"] = {"mode": "scheduled", "times_ns": [time_ns]}
        elif "release_s" not in p:
            _fail("release_s", "A single task in a rate workload needs an explicit release time when edited", "修改速率工作量中的单个任务需要明确释放时间")
        if "release_s" in p:
            single["arrivals"] = {"mode": "scheduled", "times_ns": [round(p["release_s"] * SECOND)]}
        if "destination" in p:
            _endpoints(document, [p["destination"]], "destination")
            if p["destination"] in single["origins"]:
                _fail("destination", "Destination must differ from every possible origin", "目的地必须不同于所有可能起点")
            single["destinations"] = [p["destination"]]
            entity["facts"]["lowalt.task_destination"] = p["destination"]
        if "priority" in p:
            single["priority"] = p["priority"]
            entity["facts"]["lowalt.task_priority"] = p["priority"]
        group["tasks"].remove(p["task"])
        if not group["tasks"]:
            tasks["generators"].remove(group)
        tasks["generators"].append(single)
        return
    if name in {"add_incident", "set_incident", "remove_incident"}:
        coop = _cooperation(document)
        incident = next((r for r in coop["incidents"] if r["id"] == p["id"]), None)
        if name == "add_incident":
            if incident is not None:
                _fail("id", "Incident id already used", "事件标识已被使用")
            _unique(document, p["id"])
            incident = {"id": p["id"], "kind": p["kind"],
                        "label": p.get("label", "Building fire" if p["kind"] == "fire" else "Person fell on the street"),
                        "label_zh": p.get("label_zh", "建筑火情" if p["kind"] == "fire" else "行人在街道跌倒")}
            incident.setdefault("radius_m", 30 if p["kind"] == "fire" else 6)
            coop["incidents"].append(incident)
        elif incident is None:
            _fail("id", "Incident does not exist", "事件不存在")
        if name == "remove_incident":
            coop["incidents"].remove(incident)
            _drop_generated(document, {**coop, "incidents": [incident], "handoff_points": []})
            return
        if "at" in p:
            incident["position"] = _position(document, p["at"], "at", ctx)
        if "time_s" in p:
            incident["at_s"] = float(p["time_s"])
        for key in ("kind", "label", "label_zh", "radius_m"):
            if key in p:
                incident[key] = p[key]
        if "end_s" in p:
            if p["end_s"] is None:
                # An explicit null removes the end; the incident becomes open-ended.
                incident.pop("end_s", None)
            else:
                end = float(p["end_s"])
                if end <= incident.get("at_s", 0):
                    _fail("end_s", "Incident end must follow its start", "事件结束必须晚于开始")
                if round(end * SECOND) > document["run"]["until_ns"]:
                    _fail("end_s", f"Incident end is beyond the {document['run']['until_ns'] / SECOND:g} s run", f"事件结束超出 {document['run']['until_ns'] / SECOND:g} 秒运行")
                incident["end_s"] = end
        _check_incident_time(incident, document, "end_s" if "end_s" in p else "at_s")
        # Generated incident and handoff rows follow the configuration: drop the owned
        # ones so expansion rebuilds them with the edited values.
        _drop_generated(document, coop)
        return
    if name == "set_cooperation":
        coop = _cooperation(document)
        for key in ("watchers", "helpers"):
            if "vehicles" in p.get(key, {}):
                p[key]["vehicles"] = _vehicles(document, p[key]["vehicles"], f"{key}.vehicles")
        if "handoff_points" in p:
            points = []
            for point in p["handoff_points"]:
                if isinstance(point, str):
                    existing = next((r for r in coop["handoff_points"] if r["id"] == point), None)
                    if existing:
                        points.append(copy.deepcopy(existing))
                        continue
                    existing_site = next((e for e in document["entities"] if e["id"] == point), None)
                    point_id = "handoff-" + point if existing_site is not None and existing_site["type"] != "lowalt:HandoffPoint" else point
                    if point_id != point:
                        warnings.append({"op_id": op_id, "field": "handoff_points", "message": f"Handoff at {point} uses a separate point entity {point_id} at the same position / 在 {point} 交接使用同位置的独立交接点实体 {point_id}"})
                    point = {"id": point_id, "at": point}
                point = copy.deepcopy(point)
                if "at" in point and "position" in point:
                    _fail("handoff_points", "Supply at or position, not both", "仅提供 at 或 position 其中一个")
                if "at" in point:
                    point["position"] = _position(document, point.pop("at"), "handoff_points.at", ctx)
                elif "position" not in point:
                    _fail("handoff_points", "Provide at or position for each handoff point", "每个交接点需要 at 或 position")
                else:
                    point["position"] = _position(document, point["position"], "handoff_points.position", ctx)
                point.setdefault("label", point["id"])
                point.setdefault("label_zh", "交接点 " + point["id"])
                points.append(point)
            p["handoff_points"] = points
        # Generated points and incidents follow the configuration: drop the old ones so expansion rebuilds them.
        _drop_generated(document, coop)
        _merge(coop, p)
        if any(coop.get(k, {}).get("mode") == "agent" for k in ("responder", "helpers")):
            if ctx.agent_profile is None:
                _fail("mode", "Agent decisions need a connected model", "智能体决策需要已连接模型")
            coop["provider"] = {"profile": ctx.agent_profile}
        return
    if name == "remove_cooperation":
        _engine(document, "lowalt_tasks")[1].pop("cooperation", None)
        removed_types = {"lowalt:Incident", "lowalt:Handoff", "lowalt:HandoffPoint"}
        document["entities"] = [e for e in document["entities"] if e["type"] not in removed_types]
        bindings = document["bindings"]
        bindings["rules"] = [r for r in bindings.get("rules", []) if r["type"] not in removed_types]
        bindings["lifecycle"] = [r for r in bindings.get("lifecycle", []) if r["type"] not in removed_types]
        for rule in bindings["rules"]:
            rule["fields"] = [f for f in rule["fields"] if not f.startswith(("lowalt.incident_", "lowalt.handoff_"))]
        removed_engines = {identity for identity, engine in document["engines"].items()
                           if engine["plugin"] == "langgraph" and engine["config"].get("factory", "").startswith("aeroagentsim.packs.lowalt.cooperation:")}
        for identity in removed_engines:
            del document["engines"][identity]
        return
    if name == "schedule_event":
        _schedule_event(document, p, ctx)
        return
    _fail("op", f"Unsupported operation {name}", "此操作尚未实现")


def _schedule_event(document: dict, p: dict, ctx: CompileContext) -> None:
    points: dict[str, str] = {}
    specs = document.get("behaviours", [])
    for spec in specs:
        package = yaml.load(_resource(ctx, spec["path"]), Loader=UniqueLoader) if "path" in spec else spec
        points.update({r["id"]: r["emits"] for r in package.get("injection_points", [])})
    point_id = p["event"]
    standard = {n: "lowalt.operator." + n for n in ("close_vertiport", "add_urgent_order", "restaurant_delay", "change_dropoff", "prioritize_medical", "investigate")}
    schema_id = points.get(point_id, standard.get(point_id))
    if schema_id is None:
        _fail("event", "Choose a base injection point or standard lowalt operator event", "请选择起点注入点或标准低空操作事件")
    if point_id not in points:
        _engine(document, "lowalt_tasks")
        _overlay(document)
        package = next((s for s in specs if s.get("id") == "lowalt.operator"), None)
        if package is not None:
            package["injection_points"].append({"id": point_id, "stream_id": "operator", "command": "aas.runtime.inject_event", "target": "behaviour", "emits": schema_id, "topic": "lowalt.events"})
        else:
            _operators(document, {point_id: schema_id}, [])
    descriptor = next((m for m in document["registry"].get("messages", []) if m["id"] == schema_id), None)
    if descriptor is None:
        _fail("event", "Injection schema is not declared in the scene registry", "场景注册表没有声明此注入模式")
    def json_schema(schema: dict) -> dict:
        kind = schema["type"]
        if kind == "record":
            return {"type": "object", "properties": {k: json_schema(v) for k, v in schema["members"].items()}, "required": schema["required"], "additionalProperties": schema.get("extra", False)}
        if kind == "ref":
            return {"type": "string", "minLength": 1}
        if kind == "vector":
            return {"type": "array", "minItems": schema["length"], "maxItems": schema["length"], "items": json_schema(schema["items"])}
        return copy.deepcopy(schema)
    validate_parameters(p["params"], json_schema(descriptor["schema"]), "params")
    for key in ("site", "task", "origin", "destination", "restaurant"):
        if key in p["params"]:
            _entity(document, p["params"][key], f"params.{key}")
    types = thaw(ctx.data.get("types", {}))
    def typed_payload(value: Any, schema: dict, field_name: str) -> Any:
        kind = schema["type"]
        if kind == "ref":
            entity = _entity(document, value, field_name)
            remaining = [entity["type"]]
            seen = set()
            while remaining:
                current = remaining.pop()
                if current == schema["target_type"]:
                    return {"$ref": EntityRef(document["id"], "0", entity["id"], 0, entity["type"]).to_data()}
                if current not in seen:
                    seen.add(current)
                    remaining.extend(types.get(current, {}).get("parents", []))
            _fail(field_name, f"Select an entity of type {schema['target_type']}", "请选择符合目标契约类型的实体")
        if kind == "number":
            return float(value)
        if kind == "record":
            return {k: typed_payload(v, schema["members"][k], field_name + "." + k) for k, v in value.items()}
        if kind in {"array", "vector"}:
            return [typed_payload(v, schema["items"], field_name + "[]") for v in value]
        return value
    payload = typed_payload(p["params"], descriptor["schema"], "params")
    document["bindings"].setdefault("commands", []).append({"schema": "aas.runtime.inject_event", "target": "behaviour", "at_ns": round(p["at_s"] * SECOND), "payload": {"injection_point": point_id, "payload": payload}})


def _legacy_operations(operations) -> list[dict]:
    """Normalize only shipped operation namespaces, never external suffixes."""
    rows = thaw(operations)
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("op"), str):
            family, separator, name = row["op"].partition(".")
            if separator and family in {"lowalt", "traffic"} and name in OPERATION_SPECS:
                row["op"] = name
    return rows


def _workload_creators(operations) -> dict[str, str]:
    return {row["id"]: row["params"]["id"] for row in _legacy_operations(operations)
            if isinstance(row, dict) and row.get("op") == "add_orders" and isinstance(row.get("params"), dict)
            and isinstance(row["params"].get("id"), str) and isinstance(row.get("id"), str)}


def operation_dependencies(context: CompileContext, operations) -> dict[int, tuple[int, ...]]:
    """Pure legacy creation/region/horizon semantics, shared by both host paths."""
    operations, document = _legacy_operations(operations), thaw(context.document)
    creators: dict[str, int] = {}
    existing = {e["id"] for e in document.get("entities", [])}
    existing.update(_group_id(group) for spec in document.get("engines", {}).values() if spec.get("plugin") == "lowalt_tasks"
                    for group in spec["config"].get("generators", []))
    for i, operation in enumerate(operations):
        if not isinstance(operation, dict) or not isinstance(operation.get("params"), dict):
            continue
        p, name = operation["params"], operation.get("op")
        if name in {"add_place", "add_fleet", "add_orders", "add_incident"} and isinstance(p.get("id"), str):
            creators.setdefault(p["id"], i)
            if name in {"add_fleet", "add_orders"} and type(p.get("count")) is int and 0 < p["count"] <= 1000:
                for member in range(1, p["count"] + 1):
                    creators.setdefault(f"{p['id']}-{member}", i)
        if name == "set_cooperation":
            for point in p.get("handoff_points", []):
                if isinstance(point, dict) and isinstance(point.get("id"), str):
                    creators.setdefault(point["id"], i)

    selectors = {"at", "start", "from", "to", "fleet", "vehicle", "task", "destination", "vehicles", "handoff_points", "site", "origin", "restaurant"}
    def references(value, key=""):
        if isinstance(value, dict):
            return set().union(*(references(v, k) for k, v in value.items())) if value else set()
        if isinstance(value, list):
            return set().union(*(references(v, key) for v in value)) if value else set()
        return {value} if key in selectors and isinstance(value, str) else set()

    deps: dict[int, set[int]] = {i: set() for i in range(len(operations))}
    installers = [i for i, o in enumerate(operations) if isinstance(o, dict) and o.get("op") == "add_lowalt_components"]
    regions = [i for i, o in enumerate(operations) if isinstance(o, dict) and o.get("op") == "set_region"]
    durations = [i for i, o in enumerate(operations) if isinstance(o, dict) and o.get("op") == "set_duration"]
    workloads = _workload_creators(operations)
    for i, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        params = operation.get("params", {})
        refs = references(params)
        if operation.get("op") in {"update_place", "remove_place", "set_orders", "remove_orders", "set_incident", "remove_incident"} and isinstance(params, dict):
            target = params.get("id")
            refs.add(workloads.get(target, target) if operation.get("op") in {"set_orders", "remove_orders"} and isinstance(target, str) and target not in existing else target)
        deps[i].update(creators[r] for r in refs if r not in existing and r in creators and creators[r] != i)
        if installers and i not in installers:
            deps[i].add(installers[0])
        if regions and i not in regions and i not in installers:
            deps[i].add(regions[-1])
        # Release defaults use the final requested horizon, regardless of list order.
        if operation.get("op") in {"add_orders", "set_orders", "set_task", "add_gust", "add_incident", "set_incident", "schedule_event"}:
            deps[i].update(durations)
    return {index: tuple(sorted(required)) for index, required in deps.items()}


_COMPONENT_OPERATIONS = {
    "add_place", "update_place", "remove_place", "add_fleet", "set_fleet", "remove_fleet", "set_vehicle",
    "add_orders", "set_orders", "remove_orders", "set_task", "set_rule", "set_algorithm", "set_cooperation",
    "remove_cooperation", "add_incident", "set_incident", "remove_incident",
}
_COMPONENT_NOTE = "Prepended add_lowalt_components: standard lowalt engines, algorithm slots and rules; no places, fleets or orders; existing entities and origin preserved / 已前置 add_lowalt_components：安装标准低空引擎、算法槽和规则，不生成设施、机群或订单，保留已有实体和原点"
_COMPONENT_WARNING = "Required drone scene engines, decision methods and safety rules are included; review them before confirming / 已包含必需的无人机场景引擎、决策方法与安全规则，请审阅后再确认"


def _needs_components(name: str, context: CompileContext, params) -> bool:
    engines = context.document.get("engines", {}).values()
    if any(engine.get("plugin") == "lowalt_tasks" for engine in engines):
        return False
    return (name in _COMPONENT_OPERATIONS
            or name in {"set_wind", "add_gust"} and not any(engine.get("plugin") == "environment" for engine in engines)
            or name == "schedule_event" and str(params.get("event", "")).startswith(("lowalt", "restaurant", "close_vertiport", "add_urgent", "change_dropoff", "prioritize_medical", "investigate")))


def _resource(context: CompileContext, reference: str) -> bytes:
    normalized = posixpath.normpath(reference)
    name = context.data.get("resource_names", {}).get(normalized, normalized)
    if name not in context.resources:
        _fail("resources", f"Source resource {reference!r} was not supplied by the host", "宿主未提供来源资源")
    return context.resources[name]


def compile_operation(name: str, context: CompileContext, params) -> CompileResult:
    """Pure edit of a snapshot; resource-dependent recipes arrive already staged."""
    document, values = thaw(context.document), thaw(params)
    warnings: list[dict] = []
    references: list[dict] = []
    controls = None
    resources = ()
    prepared = context.data.get("prepared_operation")
    if prepared is not None:
        document = thaw(prepared["document"])
        warnings = thaw(prepared["warnings"])
        references = thaw(prepared["references"])
        controls = thaw(prepared["controls"])
        resources = tuple(ResourceProposal(**row) for row in thaw(prepared.get("resources", ())))
    elif name in {"set_region", "add_lowalt_components", "set_control", "remove_fleet"}:
        _fail("resources", "This builtin recipe needs host-prepared resource proposals", "此内置操作需要宿主预备的资源提案")
    else:
        if name in {"set_orders", "remove_orders"}:
            creators = _workload_creators(context.data.get("operations", ()))
            existing = {_group_id(group) for spec in document["engines"].values() if spec["plugin"] == "lowalt_tasks"
                        for group in spec["config"].get("generators", [])}
            if values["id"] in creators and values["id"] not in existing:
                values["id"] = creators[values["id"]]
        _apply_operation(context, document, name, values, warnings, context.operation_id)
    return CompileResult(patches=document_patches(thaw(context.document), document), resources=resources,
                         issues=tuple({"severity": "warning", **row} for row in warnings),
                         effects=tuple({"group": "removed", **row} for row in references),
                         references=tuple(references), controls=controls)


def _staging(*args):
    from .assistant_host import LowaltStaging
    return LowaltStaging(*args)


def _workspace_operations(*args):
    from .assistant_host import workspace_operations
    return workspace_operations(*args)


def _recover_operations(*args):
    from .assistant_host import recover_operations
    return recover_operations(*args)


def _revise_operations(*args):
    from .assistant_host import revise_operations
    return revise_operations(*args)


def plugin() -> AssistantOperationPlugin:
    from . import assistant_display as display
    from . import assistant_scene as scene
    from . import assistant_story as story
    from .assistant_places import place_catalogue

    operations = []
    for name, row in OPERATION_SPECS.items():
        prerequisite = ()
        if name in _COMPONENT_OPERATIONS | {"set_wind", "add_gust", "schedule_event"}:
            prerequisite = (Prerequisite("lowalt.add_lowalt_components", when=partial(_needs_components, name),
                                        automatic_id="auto-lowalt-components", display_operation="add_lowalt_components",
                                        note=_COMPONENT_NOTE, warning=_COMPONENT_WARNING),)
        en, zh = display.BRIEF[name]
        operations.append(OperationSpec(
            id="lowalt." + name, parameters=row["parameters"], title=en, title_zh=zh,
            description=row["description"], description_zh=row["description_zh"],
            prerequisites=prerequisite, compile=partial(compile_operation, name), topic=row["topic"],
            display={**row, "legacy_operation": name},
        ))
    return AssistantOperationPlugin(
        name="lowalt", api_version=1, operations=tuple(operations),
        read=AssistantReadSpec(summarize=scene.summarize, expectations=scene.expectations,
                               interactions=scene.interactions, missing=story.missing,
                               _callbacks={"projection_order": scene.projection_order,
                                           "repeated_expectation_ids": scene.repeated_expectation_ids,
                                           "participant": scene.explain_participant, "watch_scene": display.watch_scene,
                                          "fleet_status": display.fleet_status, "ground_wind": display.ground_wind_prose,
                                          "story_setup": story.story_setup, "observe_fact": story.observe_fact, "places": place_catalogue,
                                          "workspace_operations": _workspace_operations, "recover_operations": _recover_operations,
                                          "revise_operations": _revise_operations}),
        metadata=display.metadata(), dependencies=operation_dependencies, _host=_staging,
    )
