"""Readable parameter summaries, regenerated from the actual plan operations."""

from __future__ import annotations

from typing import Any


def unchanged_plan(plan: dict) -> bool:
    """Bound proposals and alternatives need actual effects, not just a recipe."""
    difference = plan.get("diff_base")
    return (plan["compile"]["ok"] and ("workspace" in plan["base"] or bool(plan.get("variant"))) and difference is not None
            and not any(difference[group] for group in ("added", "changed", "removed"))
            and not any(difference.get("entities", {}).values()))

LABELS = {
    "add_lowalt_components": ("Install low-altitude components", "安装低空组件"),
    "add_place": ("Add place", "添加地点"), "update_place": ("Edit place", "修改地点"), "remove_place": ("Remove place", "移除地点"),
    "add_fleet": ("Add fleet", "添加机群"), "set_fleet": ("Edit fleet", "修改机群"), "remove_fleet": ("Remove fleet", "移除机群"),
    "set_vehicle": ("Edit drone", "修改无人机"), "add_orders": ("Add orders", "添加订单"), "set_orders": ("Edit orders", "修改订单"),
    "remove_orders": ("Remove orders", "移除订单"), "set_task": ("Edit task", "修改任务"),
    "set_wind": ("Background wind", "背景风"), "add_gust": ("Wind gust", "阵风"), "remove_gust": ("Remove wind gust", "移除阵风"),
    "set_weather": ("Weather display", "天气显示"),
    "set_duration": ("Run duration", "运行时长"), "set_run_mode": ("Run mode", "运行模式"),
    "set_cooperation": ("Cooperation", "协作安排"), "remove_cooperation": ("Remove cooperation", "移除协作"),
    "add_incident": ("Schedule incident", "安排事件"), "set_incident": ("Edit incident", "修改事件"), "remove_incident": ("Remove incident", "移除事件"),
    "schedule_event": ("Schedule event", "安排操作事件"), "set_region": ("Map region", "地图区域"),
    "set_control": ("Scene setting", "场景设置"), "set_rule": ("Operational rule", "运行规则"), "set_algorithm": ("Decision algorithm", "决策算法"),
}
PARAMETERS = {
    "id": ("", ""), "fleet": ("", ""), "vehicle": ("", ""), "task": ("", ""),
    "role": ("role", "角色"), "kind": ("kind", "类型"), "at": ("position", "位置"), "start": ("start", "起点"),
    "index": ("gust index", "阵风索引"), "count": ("count", "数量"), "capacity": ("capacity", "容量"), "power_w": ("power", "功率"),
    "battery_pct": ("battery", "电量"), "payload_capacity_kg": ("payload capacity", "载荷容量"),
    "payload_kg": ("payload", "载荷"), "preparation_s": ("preparation", "准备时长"),
    "delivery_window_s": ("delivery window", "配送时间窗"), "priority": ("priority", "优先级"),
    "from": ("from", "从"), "to": ("to", "至"), "release": ("release", "发布时间"),
    "seconds": ("", ""), "speed_mps": ("speed", "风速"), "start_s": ("start", "开始"), "end_s": ("end", "结束"),
    "from_deg": ("from", "来向"), "precipitation_mm_h": ("rain", "降雨"), "visibility_m": ("visibility", "能见度"), "light": ("light", "光照"),
    "time_s": ("time", "时间"), "at_s": ("time", "时间"), "release_s": ("release", "发布时间"),
    "extract": ("source", "地图源"), "bounds": ("bounds", "范围"), "alt": ("altitude", "海拔"),
    "level_height_m": ("floor height", "层高"), "destination": ("destination", "目的地"),
    "task_kinds": ("task kinds", "任务类型"), "control": ("setting", "设置项"), "rule": ("rule", "规则"),
    "slot": ("slot", "算法用途"), "value": ("value", "取值"), "mode": ("mode", "模式"), "event": ("event", "事件"),
    "vehicles": ("drones", "无人机"), "notice_radius_m": ("notice radius", "观察半径"), "ask_radius_m": ("help radius", "协助半径"),
    "min_battery_pct": ("minimum battery", "最低电量"), "max_response_distance_m": ("response distance", "响应距离"),
    "protected_task_kinds": ("protected tasks", "受保护任务"), "require_replacement": ("replacement required", "需要替代者"),
    "watchers": ("watchers", "观察者"), "responder": ("responder", "响应者"), "helpers": ("helpers", "协助者"),
    "handoff_points": ("handoff points", "交接点"), "photograph": ("photograph", "拍摄"), "params": ("settings", "参数"),
    "goal": ("goal", "目标"), "rules": ("rules", "规则"), "order": ("response order", "响应次序"),
    "selection": ("selection", "选择方式"), "accept_when": ("accept when", "接受条件"), "dwell_s": ("dwell", "悬停"), "altitude_m": ("height", "高度"),
}
VALUES = {
    "charging_pad": ("charging pad", "充电坪"), "depot": ("depot", "仓库"), "kitchen": ("kitchen", "厨房"), "customer": ("customer", "客户"),
    "vertiport": ("vertiport", "起降场"), "handoff_point": ("handoff point", "交接点"), "incident_site": ("incident site", "事件地点"),
    "inspection_start": ("inspection start", "巡检起点"), "inspection_end": ("inspection end", "巡检终点"),
    "meal_courier": ("meal courier", "送餐无人机"), "parcel_drone": ("parcel drone", "包裹无人机"), "medical_drone": ("medical drone", "医疗无人机"),
    "inspection_drone": ("inspection drone", "巡检无人机"), "patrol_drone": ("patrol drone", "巡逻无人机"), "air_taxi": ("air taxi", "空中出租车"), "sprayer": ("sprayer", "植保无人机"),
    "parcel": ("parcel", "包裹"), "food": ("food", "餐食"), "medical": ("medical", "医疗"), "person_fall": ("person falling", "行人跌倒"),
    "fire": ("fire", "火情"), "day": ("day", "白天"), "dusk": ("dusk", "黄昏"),
    "inspection": ("inspection", "巡检"), "survey": ("survey", "测绘"), "spraying": ("spraying", "植保"), "passenger": ("passenger", "客运"), "patrol": ("patrol", "巡逻"), "incident_response": ("incident response", "事件响应"),
    "center": ("centre", "中心"), "centre": ("centre", "中心"), "middle": ("centre", "中心"),
    "prescribed": ("prescribed", "预设"), "agent": ("agent", "智能体"), "interactive": ("interactive", "交互"), "scripted": ("scripted", "预设"),
    "allocation": ("task allocation", "任务分配"), "path_planning": ("path planning", "路径规划"), "conflict_resolution": ("conflict resolution", "冲突消解"), "charging": ("charging", "充电"),
    "battery_reserve_pct": ("battery reserve", "预留电量"), "safety_distance_m": ("safety distance", "安全间距"), "wind_limit_mps": ("wind limit", "风速上限"), "medical_preemption": ("medical preemption", "医疗抢占"),
}
UNITS = {"seconds": "s", "start_s": "s", "end_s": "s", "time_s": "s", "at_s": "s", "release_s": "s", "preparation_s": "s", "delivery_window_s": "s", "dwell_s": "s",
         "radius_m": "m", "precipitation_mm_h": "mm/h", "visibility_m": "m",
         "speed_mps": "m/s", "battery_pct": "%", "min_battery_pct": "%", "power_w": "W", "payload_kg": "kg", "payload_capacity_kg": "kg",
         "alt": "m", "level_height_m": "m", "notice_radius_m": "m", "ask_radius_m": "m", "max_response_distance_m": "m", "altitude_m": "m"}


def _value(value: Any, key: str, zh: bool) -> str:
    language = int(zh)
    if key == "release" and isinstance(value, dict):
        times = value.get("times_s")
        if isinstance(times, list) and times and all(type(v) in (int, float) for v in times):
            return f"{len(times)} 次，{times[0]:g}–{times[-1]:g} s" if zh else f"{len(times)} releases, {times[0]:g}–{times[-1]:g} s"
        if value.get("mode") == "poisson" and type(value.get("rate_per_s")) in (int, float):
            parts = [f"{value['rate_per_s']:g}/s"]
            parts.extend(f"{PARAMETERS[k][language]} {_value(value[k], k, zh)}" for k in ("start_s", "end_s") if k in value)
            if "burst_size" in value:
                parts.append(("每批 " if zh else "burst ") + str(value["burst_size"]))
            return ", ".join(parts)
    if isinstance(value, dict):
        return ", ".join(f"{PARAMETERS.get(k, (k.replace('_', ' '), k))[language]} {_value(v, k, zh)}" for k, v in value.items())
    if isinstance(value, list):
        preview = ", ".join(_value(v, key, zh) for v in value[:4])
        return preview + (f" … ({len(value)})" if len(value) > 4 else "")
    if type(value) is bool:
        return ("是" if value else "否") if zh else ("yes" if value else "no")
    if isinstance(value, (int, float)):
        number = format(value, ".12g") if key in {"bounds", "lat", "lon"} else f"{value:g}"
        return number + (" " + UNITS[key] if key in UNITS else "")
    if isinstance(value, str):
        # Object ids are preserved verbatim; role and option values get labels.
        return VALUES.get(value, (value, value))[language] if key not in {"id", "fleet", "vehicle", "task", "extract", "from", "to"} else value
    return str(value)


def operation_summaries(operations: list[dict]) -> list[dict]:
    rows = []
    for op in operations:
        labels = LABELS.get(op["op"], ("Change", "修改"))
        texts = []
        for zh in (False, True):
            params = op["params"]
            parts = []
            name = params.get("label_zh" if zh else "label")
            if name:
                parts.append(name)
            for key, value in params.items():
                if key in {"label", "label_zh"}:
                    continue
                caption = PARAMETERS.get(key, (key.replace("_", " "), key))[int(zh)]
                parts.append((caption + " " if caption else "") + _value(value, key, zh))
            text = labels[int(zh)] + (": " + ", ".join(parts) if parts else "")
            texts.append(text[:320] + ("…" if len(text) > 320 else ""))
        rows.append({"id": op.get("id"), "label": labels[0], "label_zh": labels[1], "text": texts[0], "text_zh": texts[1]})
    return rows


def change_summaries(difference: dict) -> list[dict[str, str]]:
    """Describe actual compiled effects, including prerequisites the model did not request."""
    rows = []
    for group, verbs in (("added", ("Add", "新增")), ("changed", ("Change", "修改")), ("removed", ("Remove", "移除"))):
        for item in difference[group]:
            row = {"kind": item["kind"], "id": item["id"]}
            for zh in (False, True):
                suffix = "_zh" if zh else ""
                text = f"{verbs[int(zh)]} {item['kind_label' + suffix]}: {item['label' + suffix]} ({item['id']})"
                fields = [f"{field['label' + suffix]}: {field['before_text' + suffix]} → {field['after_text' + suffix]}"
                          for field in item.get("fields", [])]
                row["text" + suffix] = text + ("; " + "; ".join(fields) if fields else "")
            rows.append(row)
    return rows
