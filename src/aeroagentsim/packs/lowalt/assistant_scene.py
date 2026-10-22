"""Scene cards, comparisons and conditional expectations from configured facts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import deque
from typing import Any

from aeroagentsim.assistant_api import CompileContext, thaw
from aeroagentsim.services.narratives import TASK_NOUN, TASK_NOUN_ZH, _chinese_label

from .assistant import PLACE_TYPES, ROLES

SECOND = 1_000_000_000
RULE_LABELS = {
    "battery_reserve_pct": ("Battery reserve", "预留电量", "%"),
    "safety_distance_m": ("Safety distance", "安全间隔", "m"),
    "wind_limit_mps": ("Wind limit", "风速上限", "m/s"),
    "medical_preemption": ("Medical preemption", "医疗任务抢占", None),
}
EVENT_LABELS = {
    "person_fall": ("Person falls", "行人跌倒"), "fire": ("Building fire", "建筑火情"),
    "close_vertiport": ("Close vertiport", "关闭起降场"),
    "add_urgent_order": ("Add urgent order", "新增紧急订单"), "restaurant_delay": ("Kitchen delay", "厨房延迟"),
    "change_dropoff": ("Change drop-off", "更改收货点"), "prioritize_medical": ("Prioritize medical task", "提高医疗任务优先级"),
    "investigate": ("Investigate incident", "调查事件"),
    "report_defect": ("Report an inspection defect", "报告巡检缺陷"),
    "pause_spraying": ("Pause spraying", "暂停喷洒"),
}
MISSION_STEPS = {
    "spraying": [
        ("field", "lowalt.task.picked_up", "from", "Fly to the field entry", "飞至农田入口"),
        ("spray", "lowalt.spraying.applied", "to", "Apply spray passes along the field route", "沿农田路线逐趟喷洒"),
        ("finish", "lowalt.task.completed", "to", "Finish the spray route at the field exit", "完成喷洒路线并到达农田出口"),
    ],
    "inspection": [
        ("route", "lowalt.task.picked_up", "from", "Reach the start of the inspection route", "到达巡检路线起点"),
        ("observe", "lowalt.inspection.observed", "to", "Follow the route and observe inspection segments", "沿路线飞行并观测巡检区段"),
        ("finish", "lowalt.task.completed", "to", "Finish the inspection route", "完成巡检路线"),
    ],
    "patrol": [
        ("route", "lowalt.task.picked_up", "from", "Reach the patrol route", "到达巡逻路线"),
        ("visit", "lowalt.patrol.visited", "to", "Visit patrol checkpoints in order", "按顺序巡访巡逻检查点"),
        ("finish", "lowalt.task.completed", "to", "Finish the patrol route", "完成巡逻路线"),
    ],
    "passenger": [
        ("board", "lowalt.task.picked_up", "from", "Board passengers at the origin vertiport", "在出发起降场接载乘客"),
        ("pad-wait", "lowalt.mobility.pad_wait", "to", "Wait for a pad if a vertiport is busy", "若起降场繁忙，则等待空闲起降位"),
        ("land", "lowalt.task.completed", "to", "Land passengers at the destination vertiport", "载客降落在目的地起降场"),
    ],
    "incident_response": [
        ("depart", "lowalt.task.picked_up", "from", "Reach the investigation departure point", "到达调查任务出发点"),
        ("investigate", "lowalt.task.completed", "to", "Fly to the incident site and finish the investigation flight", "飞至事件现场并完成调查飞行"),
    ],
}
ROUTINE_TEXT = {
    "food": ("Take the next ready meal order, fly to the customer, deliver it, and recharge when needed.", "接取下一份已备好的餐食订单，飞至客户处送达，并在需要时充电。"),
    "parcel": ("Take the next parcel, fly to its destination, deliver it, and recharge when needed.", "接取下一件包裹，飞至目的地送达，并在需要时充电。"),
    "medical": ("Take a priority medical task, fly the supplies to their destination, and recharge when needed.", "接取高优先级医疗任务，将物资飞送至目的地，并在需要时充电。"),
    "inspection": ("Take an inspection route, observe its segments, pause in strong wind, and recharge when needed.", "接取巡检路线，观测各区段，强风时暂停，并在需要时充电。"),
    "spraying": ("Take a field, spray it in passes, pause in strong wind, and recharge when needed.", "接取一块农田，逐趟喷洒，强风时暂停，并在需要时充电。"),
    "patrol": ("Take a patrol route, visit its checkpoints in order, and recharge when needed.", "接取巡逻路线，按顺序巡访检查点，并在需要时充电。"),
    "passenger": ("Board passengers at a vertiport, wait for busy pads, land at the destination, and recharge when needed.", "在起降场接载乘客，繁忙时等待起降位，降落在目的地，并在需要时充电。"),
    "incident_response": ("Take an investigation task, fly to the incident site, and recharge when needed.", "接取调查任务，飞至事件现场，并在需要时充电。"),
}


def _mission_steps(workload: dict) -> list[tuple]:
    kind = workload["kind"]
    if kind in {"food", "parcel", "medical"}:
        return [("pickup", "lowalt.task.picked_up", "from", "Pick up after release and preparation", "释放并准备好后接取任务"),
                ("delivery", "lowalt.task.completed", "to", "Complete through the real flight receipt flow", "通过真实飞行回执流程完成任务")]
    steps = MISSION_STEPS.get("inspection" if kind == "survey" else kind, [
        ("origin", "lowalt.task.picked_up", "from", "Reach the task origin", "到达任务起点"),
        ("finish", "lowalt.task.completed", "to", "Complete the configured route", "完成已配置路线"),
    ])
    return [s for s in steps if s[1] not in {"lowalt.spraying.applied", "lowalt.inspection.observed", "lowalt.patrol.visited"} or workload.get("work_route") is not None]


def _weather_steps(event: dict) -> list[tuple]:
    steps = []
    for effect in event.get("effects", []):
        prefix = f"event:{event['id']}:{effect['fleet']}"
        work, work_zh = ("spraying", "喷洒") if effect["spraying"] else ("flight", "飞行")
        where = {"vehicle": {"$in": effect["vehicles"]}}
        steps.append((prefix + ":paused", effect["fleet"], "lowalt.weather.hold", {**where, "wind_mps": event["speed_mps"]},
                      f"Pause {work} when wind exceeds the limit", f"风速超限时暂停{work_zh}", event["at_s"], []))
        if effect["resume"]:
            steps.append((prefix + ":resumed", effect["fleet"], "lowalt.weather.release", {**where, "wind_mps": event["after_speed_mps"]},
                          f"Resume {work} when wind drops within the limit", f"风速降至上限内后恢复{work_zh}", event["end_s"], [prefix + ":paused"]))
    return steps


def _operator_steps(event: dict, duration: float) -> list[tuple]:
    params = event.get("params", {})
    prefix = "event:" + event["id"]
    schema = event.get("schema")
    if schema == "lowalt.operator.report_defect":
        return [(prefix + ":defect", "lowalt.inspection.defect_reported", {k: params[k] for k in ("task", "site")},
                 "The defect report releases an inspection task for the asset", "缺陷报告释放该设施的巡检复查任务", event["at_s"], [prefix])]
    if schema == "lowalt.operator.prioritize_medical":
        return [(prefix + ":priority", "lowalt.medical.prioritized", {k: params[k] for k in ("task", "priority")},
                 "Raise the medical task priority after the urgent request", "收到紧急请求后提高医疗任务优先级", event["at_s"], [prefix])]
    if schema == "lowalt.operator.pause_spraying":
        where = {"task": params["task"]}
        steps = [(prefix + ":paused", "lowalt.spraying.paused", where, "Pause spraying for the requested duration", "按请求时长暂停喷洒", event["at_s"], [prefix])]
        resume = event["at_s"] + params["duration_s"]
        if resume < duration:
            steps.append((prefix + ":resumed", "lowalt.spraying.resumed", where, "Resume spraying after the operator pause expires", "操作员停喷时段结束后恢复喷洒", resume, [prefix + ":paused"]))
        return steps
    return []


def _tasks_config(document: dict) -> dict:
    return next((e["config"] for e in document.get("engines", {}).values() if e["plugin"] == "lowalt_tasks"), {})


def _position(entity: dict) -> list | None:
    facts = entity.get("facts", {})
    for key in ("lowalt.position", "lowalt.site_position", "lowalt.incident_position", "he.aircraft.position_enu_m", "traffic.road.position_enu_m", "traffic.incident.position_enu_m", "aas.studio.position_enu_m"):
        if key in facts:
            return facts[key]
    return None


def _plain_payload(value: Any) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            return value["$ref"]["id"]
        return {k: _plain_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain_payload(v) for v in value]
    return value


def _common(values: list[Any]) -> Any:
    return values[0] if values and all(v == values[0] for v in values) else None


def _workload_id(group: dict) -> str:
    if "id" in group:
        return group["id"]
    tasks = group["tasks"]
    prefix = re.sub(r"[-.]\d+$", "", tasks[0]) if tasks else group["kind"]
    return prefix if len(tasks) > 1 and all(re.sub(r"[-.]\d+$", "", t) == prefix for t in tasks) else (tasks[0] if tasks else prefix)


def _role(vehicle_class: str | None, kinds: list[str]) -> str | None:
    for role, (cls, accepted, _, _) in ROLES.items():
        if cls == vehicle_class and kinds and set(kinds) <= set(accepted):
            return role
    return {"parcel": "parcel_drone", "inspection": "inspection_drone", "spraying": "sprayer",
            "passenger": "air_taxi", "patrol": "patrol_drone"}.get(vehicle_class)


def summarize(context: CompileContext) -> dict:
    document = thaw(context.document)
    controls = thaw(context.controls)
    type_rows = thaw(context.data.get("types", {}))
    document = copy.deepcopy(document)
    entities = {e["id"]: e for e in document.get("entities", [])}
    tasks = _tasks_config(document)
    def contract(type_id: str) -> dict:
        visited = {type_id}
        queue = deque([type_id])
        ancestry = []
        while queue:
            current = queue.popleft()
            row = type_rows.get(current, {})
            for parent in row.get("parents", []):
                if parent not in visited:
                    visited.add(parent)
                    ancestry.append(parent)
                    queue.append(parent)
        return {"type": type_id, "ancestry": ancestry, "label": type_id}
    def label(identity: str | None) -> str | None:
        return entities.get(identity, {}).get("label", identity)
    wind_sources = [(engine, identity, key, profile) for engine, e in document.get("engines", {}).items() if e["plugin"] == "environment"
                    for identity, fields in e["config"].get("profiles", {}).items() for key, profile in fields.items()
                    if key in {"lowalt.wind", "oo:digital_twin.wind.horizontalVelocity"}]
    wind_profiles = [profile for _, _, _, profile in wind_sources]
    def wind_speed(value):
        vectors = value if isinstance(value[0], list) else [value]
        return _common([math.sqrt(sum(v * v for v in vector)) for vector in vectors])
    def wind_vectors(value):
        vectors = value if isinstance(value[0], list) else [value]
        # Keep every configured sample. Equal magnitudes do not imply equal direction.
        return [{"east_mps": v[0], "north_mps": v[1],
                 **({"up_mps": v[2]} if len(v) == 3 else {}),
                 "direction_deg": math.degrees(math.atan2(v[1], v[0])) % 360 if v[0] or v[1] else None}
                for v in vectors]
    wind_details = {engine + "." + identity + "." + key: {
        "mode": p["mode"], **({"vectors": wind_vectors(p["value"])} if "value" in p else {}),
        "gusts": [{"start_s": g["start_ns"] / SECOND, "end_s": g["end_ns"] / SECOND,
                   "vectors": wind_vectors(g["value"])} for g in p.get("gusts", [])]}
        for engine, identity, key, p in wind_sources}
    speeds = [wind_speed(p["value"]) for p in wind_profiles if "value" in p]
    gusts = [{"start_s": g["start_ns"] / SECOND, "end_s": g["end_ns"] / SECOND,
              "speed_mps": wind_speed(g["value"])}
             for p in wind_profiles for g in p.get("gusts", [])]
    weather = {}
    for engine, spec in document.get("engines", {}).items():
        if spec["plugin"] != "environment":
            continue
        for identity, profiles in spec["config"].get("profiles", {}).items():
            values = {}
            for field, name in (("lowalt.precipitation", "precipitation_mm_h"),
                                ("lowalt.visibility", "visibility_m"), ("lowalt.light", "light")):
                if field in profiles:
                    value = profiles[field]["value"]
                    values[name] = value * 3600 if field == "lowalt.precipitation" else value
            if values:
                weather[engine + "." + identity] = values
    zones = [{"id": e["id"], "label": e.get("label", e["id"]), "kind": e["facts"]["lowalt.zone_kind"]}
             for e in entities.values() if e["type"] == "lowalt:AirspaceZone"]
    places = []
    inverse = {"lowalt:" + value: key for key, value in PLACE_TYPES.items() if value != "Site"}
    for e in entities.values():
        if "lowalt.site_position" not in e["facts"]:
            continue
        places.append({"id": e["id"], "role": inverse.get(e["type"], "site"), "label": e.get("label", e["id"]),
                       "label_zh": e.get("label_zh", _chinese_label(e.get("label", e["id"]))), "position": e["facts"]["lowalt.site_position"],
                       "type": e["type"], "contract": contract(e["type"]), "details": copy.deepcopy(e["facts"])})
    groups: dict[str, list[dict]] = {}
    generator = document.get("generators", {}).get("fleet")
    # The generated member ids and positions are determined by the authored generator.
    if generator:
        generated = []
        for i in range(generator["count"]):
            identity = generator["prefix"] + str(i + 1)
            if identity not in entities:
                facts = copy.deepcopy(generator["facts"])
                start = generator["starts"][i % len(generator["starts"])]
                facts["lowalt.position"] = [start[0], start[1] + i // len(generator["starts"]) * generator["spacing_m"], start[2]]
                entity = {"id": identity, "type": generator["type"], "facts": facts}
                for key in ("label", "label_zh"):
                    if key in generator:
                        entity[key] = generator[key].format(index=i + 1)
                entities[identity] = entity
            generated.append(entities[identity])
        groups["fleet"] = generated
    for e in entities.values():
        if e["type"] != "lowalt:UAV" or generator and e["id"].startswith(generator["prefix"]):
            continue
        # Plan fleets name their members "<fleet>-<n>".
        member = re.match(r"^(.+)-\d+$", e["id"])
        groups.setdefault(member.group(1) if member else e["id"], []).append(e)
    fleets = []
    for identity, members in groups.items():
        first = members[0]
        cls = _common([e["facts"].get("lowalt.vehicle_class") for e in members])
        kinds = list(dict.fromkeys(kind for e in members for kind in tasks.get("vehicle_task_kinds", {}).get(e["id"], tasks.get("vehicle_kinds", {}).get(e["facts"].get("lowalt.vehicle_class"), []))))
        role = _role(cls, kinds)
        en, zh = ROLES[role][2:] if role in ROLES else (first.get("label", identity), first.get("label_zh", identity))
        start_position = _common([_position(e) for e in members])
        start = next((p for p in places if p["position"] == start_position), None)
        fleets.append({"id": identity, "role": role, "label": en, "label_zh": zh, "count": len(members),
                       "members": [e["id"] for e in members], "vehicle_class": cls, "task_kinds": kinds,
                       "start": {"place": start["id"] if start else None, "label": start["label"] if start else None},
                       "battery_pct": _common([e["facts"]["lowalt.soc"] * 100 if "lowalt.soc" in e["facts"] else None for e in members]),
                       "payload_capacity_kg": _common([e["facts"].get("lowalt.payload_capacity_kg") for e in members]),
                       "contract": contract(first["type"]),
                       "member_states": [{"id": e["id"], "label": e.get("label", e["id"]), "label_zh": e.get("label_zh", e.get("label", e["id"])), "position": _position(e),
                                          "battery_pct": e["facts"]["lowalt.soc"] * 100 if "lowalt.soc" in e["facts"] else None,
                                          "payload_capacity_kg": e["facts"].get("lowalt.payload_capacity_kg"),
                                          "task_kinds": tasks.get("vehicle_task_kinds", {}).get(e["id"], tasks.get("vehicle_kinds", {}).get(cls, []))} for e in members]})
    cooperation = copy.deepcopy(tasks.get("cooperation"))
    if cooperation is not None:
        for key in ("watchers", "helpers"):
            cooperation[key]["labels"] = {v: label(v) for v in cooperation[key].get("vehicles", [])}
        for point in cooperation.get("handoff_points", []):
            point.setdefault("label", label(point["id"]))
            point.setdefault("label_zh", _chinese_label(point["label"]))
        for incident in cooperation.get("incidents", []):
            labels = EVENT_LABELS.get(incident["kind"], (incident["id"], incident["id"]))
            incident.setdefault("label", label(incident["id"]) or labels[0])
            incident.setdefault("label_zh", entities.get(incident["id"], {}).get("label_zh", labels[1]))
    protected = tasks.get("cooperation", {}).get("responder", {}).get("rules", {}).get("protected_task_kinds", [])
    workloads = []
    for group in tasks.get("generators", []):
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
            # Configured orders are only the slots; random arrivals stop when the window closes.
            # Models read "count: 6" as six orders, so the expected number released is stated.
            burst = arrivals.get("burst_size", 1)
            window = (arrivals.get("end_ns", document["run"]["until_ns"]) - arrivals["start_ns"]) / SECOND
            expected = min(len(group["tasks"]), round((burst if burst > 1 else 0) + arrivals["rate_per_s"] * max(0.0, window - (2 if burst > 1 else 0))))
            release["expected_released"] = expected
            if expected < len(group["tasks"]):
                closes = release.get("end_s", document["run"]["until_ns"] / SECOND)
                release["note"] = (f"Random arrivals: only about {expected} of the {len(group['tasks'])} configured orders arrive before releases stop at "
                                   f"{closes:g} s. For exactly {len(group['tasks'])}, give this workload scheduled release times (set_orders) or raise the rate.")
                release["note_zh"] = (f"随机到达：发布在 {closes:g} 秒停止前，预计只有约 {expected} 个（共配置 {len(group['tasks'])} 个）订单到达。"
                                      f"若要正好 {len(group['tasks'])} 个，请用 set_orders 为该工作量指定发布时间，或提高速率。")
        kind = group["kind"]
        workloads.append({"id": _workload_id(group), "kind": kind, "label": TASK_NOUN.get(kind, kind), "label_zh": TASK_NOUN_ZH.get(kind, kind),
                          "count": len(group["tasks"]), "tasks": list(group["tasks"]), "from": list(group["origins"]), "to": list(group["destinations"]),
                          "release": release, "priority": group["priority"], "payload_kg": group["payload_kg"], "protected": kind in protected,
                          "work_route": copy.deepcopy(tasks.get("work_routes", {}).get(kind))})
    routine = []
    for kind in dict.fromkeys(w["kind"] for w in workloads):
        en, zh = ROUTINE_TEXT.get("inspection" if kind == "survey" else kind,
                                  ("Take the next task and complete its configured route.", "接取下一项任务并完成已配置路线。"))
        routine.append({"kind": kind, "text": en, "text_zh": zh})
    packages = thaw(context.data.get("packages", ()))
    events = []
    for incident in (cooperation or {}).get("incidents", []):
        events.append({"id": incident["id"], "kind": incident["kind"], "label": incident.get("label", EVENT_LABELS[incident["kind"]][0]),
                       "label_zh": incident.get("label_zh", EVENT_LABELS[incident["kind"]][1]), "at_s": incident["at_s"],
                       **({"end_s": incident["end_s"]} if "end_s" in incident else {}),
                       "radius_m": incident.get("radius_m"),
                       "place": incident["id"], "position": incident["position"], "layer": "setup"})
    for i, command in enumerate(document.get("bindings", {}).get("commands", [])):
        if command["schema"] != "aas.runtime.inject_event":
            continue
        point = command["payload"]["injection_point"]
        kind = point.replace("-", "_")
        params = _plain_payload(command["payload"].get("payload", {}))
        place = next((params[k] for k in ("site", "destination", "restaurant", "origin") if k in params), None)
        en, zh = EVENT_LABELS.get(kind, ("Scheduled event " + point, "预设事件 " + point))
        schema = next((p["emits"] for spec in packages for p in spec.get("injection_points", []) if p["id"] == point), None)
        events.append({"id": f"scheduled-{i + 1}", "kind": kind, "label": en, "label_zh": zh,
                       "at_s": command["at_ns"] / SECOND, "place": place,
                       "position": _position(entities[place]) if place in entities else None, "layer": "setup",
                       "injection_point": point, "params": copy.deepcopy(params), "schema": schema})
    rules = []
    package = next((p for p in packages if p.get("id") == "lowalt.rules"), None)
    if package is not None:
        predicates = package["predicates"]
        values = {"battery_reserve_pct": predicates["battery_below_reserve"]["parameters"]["reserve_percent"],
                  "safety_distance_m": predicates["closer_than_safety_distance"]["parameters"]["horizontal_m"],
                  "wind_limit_mps": copy.deepcopy(predicates["wind_above_limit"]["parameters"])}
        for identity, value in values.items():
            en, zh, unit = RULE_LABELS[identity]
            rules.append({"id": identity, "label": en, "label_zh": zh, "value": value, "unit": unit})
    if "medical_preemption" in tasks:
        rules.append({"id": "medical_preemption", "label": RULE_LABELS["medical_preemption"][0], "label_zh": RULE_LABELS["medical_preemption"][1], "value": tasks["medical_preemption"], "unit": None})
    wind_chain = (package or {}).get("chains", {}).get("wind_limit", {})
    wind_schemas = {a["schema"] for t in wind_chain.get("transitions", []) for a in t.get("actions", []) if a["kind"] == "emit"}
    wind_bound = any(b["chain"] == "wind_limit" for b in (package or {}).get("bindings", []))
    plugins = {e["plugin"] for e in document.get("engines", {}).values()}
    limits = (package or {}).get("predicates", {}).get("wind_above_limit", {}).get("parameters", {})
    duration = document["run"]["until_ns"] / SECOND
    for i, gust in enumerate(gusts):
        before = next((g["speed_mps"] for g in gusts if g["end_s"] == gust["start_s"]), _common(speeds))
        after = next((g["speed_mps"] for g in gusts if g["start_s"] == gust["end_s"]), _common(speeds))
        effects = []
        if wind_bound and {"behaviour", "lowalt_airspace"} <= plugins and "lowalt.weather.hold" in wind_schemas and gust["start_s"] < duration:
            for fleet in fleets:
                members = [v for v in fleet["members"] if (limit := limits.get(entities[v]["facts"].get("lowalt.vehicle_class", "") + "_limit_mps")) is not None
                           and before is not None and before <= limit < gust["speed_mps"]]
                if members:
                    resume = "lowalt.weather.release" in wind_schemas and gust["end_s"] < duration and after is not None and all(
                        after <= limits[entities[v]["facts"]["lowalt.vehicle_class"] + "_limit_mps"] for v in members)
                    effects.append({"fleet": fleet["id"], "vehicles": members, "spraying": fleet["vehicle_class"] == "spraying", "resume": resume})
        events.append({"id": f"wind-gust-{round(gust['start_s'] * SECOND)}", "kind": "wind_gust", "label": f"Wind gust: {gust['speed_mps']:g} m/s",
                       "label_zh": f"阵风：风速 {gust['speed_mps']:g} 米/秒", "at_s": gust["start_s"], "end_s": gust["end_s"],
                       "speed_mps": gust["speed_mps"], "after_speed_mps": after, "effects": effects, "place": None, "position": None, "layer": "setup"})
    streams = document.get("ingress_streams", [])
    run_mode = "interactive" if any(s["id"] == "operator" and s["initial_watermark_ns"] == 0 for s in streams) else "scheduled"
    return {"title": document.get("title", document.get("id")), "city": document.get("city"),
            "duration_s": document["run"]["until_ns"] / SECOND, "run_mode": run_mode,
            "environment": {"wind": {"speed_mps": _common(speeds), "gusts": gusts, "profiles": wind_details}, "zones": zones, **({"weather": weather} if weather else {})},
            "fleets": fleets, "places": places, "workloads": workloads, "events": events, "rules": rules, "routine": routine,
            "algorithms": copy.deepcopy(document.get("algorithms", {})), "cooperation": cooperation,
            "controls": copy.deepcopy(controls) if controls is not None else {},
            "origin": copy.deepcopy(document.get("origin")),
            "entity_ids": sorted(e["id"] for e in document.get("entities", [])),
            "entity_facts": {e["id"]: hashlib.sha256(json.dumps(e, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for e in document.get("entities", [])}}




def _precheck(ok: bool, en: str, zh: str) -> dict:
    return {"ok": ok, "text": "Estimate: " + en, "text_zh": "估计：" + zh}


def _segment_distance(point: list, a: list, b: list) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) if length else 0
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def _notice_estimate(summary: dict, incident: dict) -> dict | None:
    coop = summary["cooperation"]
    watchers = set(coop["watchers"]["vehicles"])
    places = {p["id"]: p["position"] for p in summary["places"]}
    distances = []
    for fleet in summary["fleets"]:
        for vehicle in fleet.get("member_states", []):
            if vehicle["id"] not in watchers or vehicle["position"] is None:
                continue
            distances.append(math.dist(vehicle["position"][:2], incident["position"][:2]))
            for work in summary["workloads"]:
                if work["kind"] not in vehicle["task_kinds"]:
                    continue
                for origin in work["from"]:
                    for destination in work["to"]:
                        if origin in places and destination in places:
                            distances.append(_segment_distance(incident["position"], places[origin], places[destination]))
    if not distances:
        return None
    closest = min(distances)
    radius = coop["watchers"]["notice_radius_m"]
    return _precheck(closest <= radius, f"A watcher's start or eligible straight route passes {closest:.1f} m away; notice radius {radius:g} m. Timing, actual routes and allocation may differ.",
                     f"观察机起点或可执行任务的直线路径距事件 {closest:.1f} 米；观察半径 {radius:g} 米。时序、真实路径和分配可能不同。")


def _helper_estimate(summary: dict) -> dict | None:
    coop = summary["cooperation"]
    states = {v["id"]: v for f in summary["fleets"] for v in f.get("member_states", [])}
    radius = coop["helpers"]["ask_radius_m"]
    minimum = coop["helpers"]["rules"]["min_battery_pct"]
    candidates = [(math.dist(states[a]["position"][:2], states[b]["position"][:2]), states[b]["battery_pct"])
                  for a in coop["watchers"]["vehicles"] for b in coop["helpers"]["vehicles"]
                  if a != b and a in states and b in states and states[a]["position"] is not None and states[b]["position"] is not None and states[b]["battery_pct"] is not None]
    if not candidates:
        return None
    closest = min(d for d, _ in candidates)
    ok = any(d <= radius and battery >= minimum for d, battery in candidates)
    return _precheck(ok, f"Nearest helper starts {closest:.1f} m away; ask radius {radius:g} m, minimum battery {minimum:g}%. Battery and positions change during the run; capacity and availability still constrain acceptance.",
                     f"最近协助机初始距离 {closest:.1f} 米；请求半径 {radius:g} 米，最低电量 {minimum:g}%。运行中电量和位置会变化，容量和空闲状态仍限制接受。")


def _response_estimate(summary: dict, incident: dict) -> dict | None:
    coop = summary["cooperation"]
    states = [v for f in summary["fleets"] for v in f.get("member_states", [])
              if v["id"] in coop["watchers"]["vehicles"] and v["position"] is not None and v["battery_pct"] is not None]
    if not states:
        return None
    minimum = coop["responder"]["rules"]["min_battery_pct"]
    radius = coop["responder"]["rules"]["max_response_distance_m"]
    eligible = [v for v in states if v["battery_pct"] >= minimum and math.dist(v["position"][:2], incident["position"][:2]) <= radius]
    return _precheck(bool(eligible), f"{len(eligible)} of {len(states)} watchers initially meet battery ≥ {minimum:g}% and distance ≤ {radius:g} m. Actual decisions use state at notice time and task protection.",
                     f"{len(states)} 架观察机中 {len(eligible)} 架初始满足电量 ≥ {minimum:g}%、距离 ≤ {radius:g} 米。真实决策使用发现时状态和任务保护规则。")


def expectations(document: dict, summary: dict) -> list[dict]:
    result = []
    def add(identity: str, schema: str, where: dict, en: str, zh: str, *, layer: str = "behaviour", at=None, precheck=None, depends=(), count: int | None = None, released: str | None = None, demand: dict | None = None, alternatives=()) -> str:
        match = {"schema": schema, "where": where, **({"count": count} if count is not None else {}), **({"released_schema": released} if released else {}),
                 **({"count_basis": "released", "demand": demand} if demand is not None else {})}
        result.append({"id": identity, "text": en, "text_zh": zh, "layer": layer, "match": match,
                       "approx_s": at, "precheck": precheck, "depends_on": list(depends), **({"alternatives": list(alternatives)} if alternatives else {})})
        return identity
    names = {e["id"]: (e.get("label", e["id"]), e.get("label_zh", e.get("label", e["id"]))) for e in document.get("entities", [])}
    for workload in summary["workloads"]:
        if workload.get("domain") is not None:
            continue
        times = workload["release"].get("times_s")
        stochastic = workload["release"]["mode"] == "poisson"
        counted = stochastic or len(workload["tasks"]) > 2
        counts = {"count": len(workload["tasks"]), "released": "lowalt.task.created"} if counted else {}
        if stochastic:
            counts["demand"] = workload["release"]
        scopes = [(f"workload:{workload['id']}", {"task": {"$in": list(workload["tasks"])}},
                   workload["label"], workload["label_zh"], times[0] if times else None)] if counted else [
            (f"task:{task}", {"task": task}, *names.get(task, (task, task)), times[i] if times and i < len(times) else None)
            for i, task in enumerate(workload["tasks"])]
        configured = len(workload["tasks"])
        for prefix, where, en, zh, at in scopes:
            released_en, released_zh = (f"{en[:1].upper()}{en[1:].rstrip('s')}s are released ({configured} configured)", f"{zh}按计划发布（共配置 {configured} 个）") if counted else (f"{en} is released", f"{zh}发布")
            assigned_en, assigned_zh = (f"Each configured {en.rstrip('s')} is assigned a UAV", f"每个已配置的{zh}都分配到无人机") if counted else (f"A UAV is assigned {en}", f"无人机获分配{zh}")
            if stochastic:
                released_en, released_zh = f"Random arrivals release {en} (up to {configured} configured slots)", f"随机到达发布{zh}（最多 {configured} 个配置名额）"
                assigned_en, assigned_zh = f"Each released {en.rstrip('s')} is assigned a UAV", f"每个实际发布的{zh}都分配到无人机"
            created = add(prefix + ":created", "lowalt.task.created", where, released_en, released_zh, layer="setup", at=at, **counts)
            assigned = add(prefix + ":assigned", "lowalt.task.assigned", where, assigned_en, assigned_zh, depends=[created], **counts)
            previous = assigned
            for _, schema, _, text, text_zh in _mission_steps(workload):
                suffix = schema.rsplit(".", 1)[-1]
                conditional = schema == "lowalt.mobility.pad_wait"
                qualifier, qualifier_zh = ("released", "实际发布") if stochastic else ("configured", "已配置")
                text, text_zh = (f"{text} (each {qualifier} {en.rstrip('s')})", f"{text_zh}（每个{qualifier_zh}的{zh}）") if counted else (f"{en}: {text}", f"{zh}：{text_zh}")
                if counted and schema == "lowalt.task.completed":
                    text, text_zh = (f"All {configured} configured {en} are completed",
                                     f"全部 {configured} 个已配置的{zh}都完成")
                    if stochastic:
                        text, text_zh = f"All released {en} are completed", f"全部实际发布的{zh}都完成"
                step_counts = ({"count": 1, "released": "lowalt.task.created"} if counted else {}) if conditional else counts
                identity = add(prefix + ":" + suffix, schema, where, text, text_zh, depends=[assigned if conditional else previous],
                               **step_counts)
                if not conditional:
                    previous = identity
    coop = summary.get("cooperation")
    if coop:
        order = coop["responder"]["order"]
        for incident in coop["incidents"]:
            identity = incident["id"]
            occurred = add(f"incident:{identity}:occurred", "lowalt.incident.occurred", {"incident": identity}, incident["label"], incident["label_zh"], layer="setup", at=incident["at_s"])
            noticed = add(f"incident:{identity}:noticed", "lowalt.incident.noticed", {"incident": identity}, "A watcher may notice the incident", "观察机可能发现事件", precheck=_notice_estimate(summary, incident), depends=[occurred])
            decision = add(f"incident:{identity}:decision", "lowalt.incident.decision", {"incident": identity}, "The responder chooses an allowed response", "响应机选择允许的响应方式", precheck=_response_estimate(summary, incident), depends=[noticed])
            if "request_handoff" in order and coop["helpers"]["vehicles"]:
                requested = add(f"incident:{identity}:requested", "lowalt.handoff.requested", {"incident": identity}, "If carrying an eligible task, the responder may ask helpers", "若携带可转交任务，响应机可能请求协助", precheck=_helper_estimate(summary), depends=[decision])
                offered = add(f"incident:{identity}:offer", "lowalt.handoff.offer", {"incident": identity}, "A candidate replies to the handoff request", "候选协助机回复交接请求", depends=[requested])
                accepted = add(f"incident:{identity}:accepted", "lowalt.handoff.accepted", {"incident": identity}, "If a helper accepts, one is selected", "若协助机接受，则选择其中一架", depends=[offered], alternatives=[f"incident:{identity}:declined"])
                add(f"incident:{identity}:declined", "lowalt.handoff.declined", {"incident": identity}, "If no helper accepts, the handoff is declined", "若无人接受，则交接请求被拒绝", depends=[offered], alternatives=[accepted])
                dropped = add(f"incident:{identity}:dropped", "lowalt.handoff.dropped", {"incident": identity}, "If already picked up, the parcel is left at the handoff point", "若已取件，包裹被放在交接点", depends=[accepted])
                add(f"incident:{identity}:transferred", "lowalt.handoff.transferred", {"incident": identity}, "The helper takes over the original delivery", "协助机接手原投递任务", depends=[accepted])
                del dropped
            if any(option in order for option in ("respond_now", "request_handoff", "finish_then_respond")):
                responding = add(f"incident:{identity}:responding", "lowalt.incident.responding", {"incident": identity}, "If permitted, the responder flies above the incident", "若条件允许，响应机飞至事件上方", depends=[decision], alternatives=[f"incident:{identity}:reported"] if "report_only" in order else [])
                add(f"incident:{identity}:photographed", "lowalt.incident.photographed", {"incident": identity}, "After responding and dwelling, the photograph event is recorded", "响应并悬停后记录拍摄事件", depends=[responding])
            if "report_only" in order:
                add(f"incident:{identity}:reported", "lowalt.incident.reported", {"incident": identity}, "If report-only is chosen, the incident is reported", "若选择仅报告，则报告事件", depends=[decision],
                    alternatives=[f"incident:{identity}:responding"] if any(option in order for option in ("respond_now", "request_handoff", "finish_then_respond")) else [])
    for event in summary["events"]:
        if event["kind"] == "person_fall":
            continue
        if event["kind"] == "wind_gust":
            for identity, _, schema, where, en, zh, at, depends in _weather_steps(event):
                add(identity, schema, where, en, zh, at=at, depends=depends, count=len(where["vehicle"]["$in"]))
            continue
        schema = event.get("schema")
        if schema is not None:
            add("event:" + event["id"], schema, event["params"], event["label"], event["label_zh"], layer="setup", at=event["at_s"])
            for identity, emitted, where, en, zh, at, depends in _operator_steps(event, summary["duration_s"]):
                add(identity, emitted, where, en, zh, at=at, depends=depends)
    return result


def interactions(summary: dict) -> dict:
    lanes = [{"id": "system", "label": "Task allocation", "label_zh": "任务分配", "kind": "system"}]
    for fleet in summary["fleets"]:
        lanes.append({"id": fleet["id"], "label": fleet["label"], "label_zh": fleet["label_zh"], "kind": "vehicle" if fleet["members"] == [fleet["id"]] else "fleet"})
        if summary.get("cooperation"):
            for state in fleet.get("member_states", []):
                if state["id"] != fleet["id"]:
                    lanes.append({"id": state["id"], "label": state.get("label", state["id"]), "label_zh": state.get("label_zh", state["id"]), "kind": "vehicle"})
    for place in summary["places"]:
        lanes.append({"id": place["id"], "label": place["label"], "label_zh": place["label_zh"], "kind": "place"})
    steps = []
    def step(identity, a, b, en, zh, *, layer="behaviour", mode="prescribed", branch=None, at=None, expect=None):
        steps.append({"id": identity, "from": a, "to": b, "text": en, "text_zh": zh, "layer": layer,
                      "decided_by": mode if layer == "behaviour" else None, "branch": branch, "at_s": at, "expect": expect})
    for work in summary["workloads"]:
        if work.get("domain") is not None:
            continue
        fleets = [f for f in summary["fleets"] if work["kind"] in f["task_kinds"]]
        to = fleets[0]["id"] if fleets else "system"
        origin, destination = work["from"][0], work["to"][0]
        prefix = "workload:" + work["id"] if len(work["tasks"]) > 2 else "task:" + work["tasks"][0] if work["tasks"] else None
        times = work["release"].get("times_s")
        step(work["id"] + ":appears", origin, "system", f"{work['count']} configured task slot{'s appear' if work['count'] != 1 else ' appears'} according to {'their' if work['count'] != 1 else 'its'} release schedule", f"{work['count']} 个已配置任务槽按释放计划出现", layer="setup", at=times[0] if times else None, expect=prefix + ":created" if prefix else None)
        step(work["id"] + ":allocation", "system", to, "Allocate an eligible UAV", "分配符合条件的无人机", mode="agent" if summary["algorithms"].get("allocation") == "llm" else "prescribed", expect=prefix + ":assigned" if prefix else None)
        for suffix, schema, target, en, zh in _mission_steps(work):
            branch = {"group": work["id"] + ":pad", "when": "a vertiport pad is busy / 起降位繁忙"} if schema == "lowalt.mobility.pad_wait" else None
            step(work["id"] + ":" + suffix, to, origin if target == "from" else destination, en, zh, branch=branch,
                 expect=prefix + ":" + schema.rsplit(".", 1)[-1] if prefix else None)
    coop = summary.get("cooperation")
    if coop:
        for incident in coop["incidents"]:
            identity = incident["id"]
            lanes.append({"id": identity, "label": incident["label"], "label_zh": incident["label_zh"], "kind": "person"})
            watchers = coop["watchers"]["vehicles"]
            helpers = coop["helpers"]["vehicles"]
            watcher = watchers[0] if watchers else "system"
            helper = helpers[0] if helpers else "system"
            point = coop["handoff_points"][0]["id"] if coop["handoff_points"] else "system"
            prefix = "incident:" + identity
            mode = coop["responder"]["mode"]
            step(prefix + ":fall", "system", identity, incident["label"], incident["label_zh"], layer="setup", at=incident["at_s"], expect=prefix + ":occurred")
            step(prefix + ":notice", identity, watcher, "Any configured watcher within the radius may notice", "半径内任一已配置观察机可能发现事件", expect=prefix + ":noticed")
            step(prefix + ":decide", watcher, watcher, "The first noticing watcher chooses among allowed options", "首架发现事件的观察机在允许选项中决策", mode=mode, expect=prefix + ":decision")
            if "request_handoff" in coop["responder"]["order"] and helpers:
                accepted = {"group": prefix + ":handoff", "when": "a candidate accepts / 候选协助机接受"}
                declined = {"group": prefix + ":handoff", "when": "no candidate accepts / 无候选协助机接受"}
                step(prefix + ":ask", watcher, helper, "If carrying an eligible task, ask all eligible nearby helpers", "若携带可转交任务，请求附近所有符合条件的协助机", mode=mode, expect=prefix + ":requested")
                step(prefix + ":accept", helper, watcher, "A helper accepts; select among acceptances", "协助机接受；从接受者中选择", mode=coop["helpers"]["mode"], branch=accepted, expect=prefix + ":accepted")
                step(prefix + ":decline", helper, watcher, "No acceptance; choose the next allowed response", "无人接受；选择下一允许响应", mode=coop["helpers"]["mode"], branch=declined, expect=prefix + ":declined")
                step(prefix + ":drop", watcher, point, "If picked up, leave the parcel at the nearest handoff point", "若已取件，在最近交接点放下包裹", branch=accepted, expect=prefix + ":dropped")
                step(prefix + ":take", point, helper, "Take over; unpicked tasks are reassigned directly", "接手任务；未取件任务直接重新分配", branch=accepted, expect=prefix + ":transferred")
            if any(o in coop["responder"]["order"] for o in ("respond_now", "request_handoff", "finish_then_respond")):
                step(prefix + ":photo", watcher, identity, "If responding is allowed, fly above the incident and record a photograph event", "若允许响应，飞至事件上方并记录拍摄事件", mode=mode, expect=prefix + ":photographed")
            if helpers and "request_handoff" in coop["responder"]["order"]:
                step(prefix + ":helper-delivery", helper, "system", "After transfer, complete the original task to its original destination", "转交后，将原任务完成到原目的地", branch={"group": prefix + ":handoff", "when": "handoff transferred / 任务已转交"})
            if "report_only" in coop["responder"]["order"]:
                step(prefix + ":report", watcher, "system", "If report-only is chosen, report the incident", "若选择仅报告，则报告事件", mode=mode, expect=prefix + ":reported")
    for event in summary["events"]:
        if event["kind"] == "person_fall":
            continue
        if event["kind"] == "wind_gust":
            step("event:" + event["id"], "system", "system", event["label"], event["label_zh"], layer="setup", at=event["at_s"])
            weather = _weather_steps(event)
            for identity, fleet, schema, _, en, zh, at, _ in weather:
                if schema == "lowalt.weather.hold":
                    step(identity, "system", fleet, en, zh, at=at, expect=identity)
            after = event["after_speed_mps"]
            if after is not None and event["end_s"] < summary["duration_s"]:
                step("event:" + event["id"] + ":calm", "system", "system", f"Wind changes to {after:g} m/s", f"风速变为 {after:g} 米/秒", layer="setup", at=event["end_s"])
            for identity, fleet, schema, _, en, zh, at, _ in weather:
                if schema == "lowalt.weather.release":
                    step(identity, "system", fleet, en, zh, at=at, expect=identity)
            continue
        step("event:" + event["id"], "system", event["place"] or "system", event["label"], event["label_zh"], layer="setup", at=event["at_s"],
             expect="event:" + event["id"] if event.get("schema") else None)
        for identity, _, where, en, zh, at, _ in _operator_steps(event, summary["duration_s"]):
            work = next((w for w in summary["workloads"] if where["task"] in w["tasks"]), None)
            fleet = next((f["id"] for f in summary["fleets"] if work and work["kind"] in f["task_kinds"]), "system")
            step(identity, "system", event["place"] or fleet, en, zh, at=at, expect=identity,
                 branch={"group": "event:" + event["id"] + ":accepted", "when": "the operator request is accepted / 操作请求被接受"})
    return {"lanes": lanes, "steps": steps}


def explain_participant(document: dict, summary: dict, entity_id: str) -> dict:
    entity = next((e for e in document.get("entities", []) if e["id"] == entity_id), None)
    fleet = next((f for f in summary["fleets"] if entity_id == f["id"] or entity_id in f["members"]), None)
    place = next((p for p in summary["places"] if p["id"] == entity_id), None)
    if entity is None and fleet is None and place is None:
        raise ValueError(f"Unknown participant {entity_id} / 参与对象不存在")
    members = fleet["members"] if fleet and entity_id == fleet["id"] else [entity_id]
    responsibilities = []
    coop = summary.get("cooperation")
    if coop:
        if set(members) & set(coop["watchers"]["vehicles"]):
            responsibilities.append({"role": "watcher", "text": "May notice nearby incidents and decide a response", "text_zh": "可能发现附近事件并决定响应", "config": copy.deepcopy(coop["responder"])})
        if set(members) & set(coop["helpers"]["vehicles"]):
            responsibilities.append({"role": "helper", "text": "May accept or decline a task handoff", "text_zh": "可能接受或拒绝任务交接", "config": copy.deepcopy(coop["helpers"])})
    state = next((v for f in summary["fleets"] for v in f.get("member_states", []) if v["id"] == entity_id), None)
    kinds = state["task_kinds"] if state else fleet["task_kinds"] if fleet else []
    workloads = [w for w in summary["workloads"] if w["kind"] in kinds or entity_id in w["from"] + w["to"] + w["tasks"]]
    return {"id": entity_id, "entity": copy.deepcopy(entity), "fleet": copy.deepcopy(fleet), "place": copy.deepcopy(place),
            "responsibilities": responsibilities, "workloads": copy.deepcopy(workloads), "rules": copy.deepcopy(summary["rules"]),
            "text": "Responsibilities and eligible work from the configured scene; assignment remains conditional.",
            "text_zh": "这些责任和可执行工作来自已配置场景；真实分配仍取决于运行条件。"}


def explain_relation(document: dict, summary: dict, a: str, b: str) -> dict:
    participants = [explain_participant(document, summary, identity) for identity in (a, b)]
    members = {p["id"]: set(p["fleet"]["members"]) if p["fleet"] and p["id"] == p["fleet"]["id"] else {p["id"]} for p in participants}
    steps = [s for s in interactions(summary)["steps"] if s["from"] in members[a] | {a} and s["to"] in members[b] | {b} or s["from"] in members[b] | {b} and s["to"] in members[a] | {a}]
    workloads = [w for w in summary["workloads"] if a in w["from"] + w["to"] and b in w["from"] + w["to"]]
    return {"a": a, "b": b, "steps": steps, "workloads": copy.deepcopy(workloads),
            "text": "Configured possible interactions; recorded events determine what happened." if steps or workloads else "No configured direct relation.",
            "text_zh": "这是已配置的可能交互；实际结果由记录事件决定。" if steps or workloads else "没有已配置的直接关系。"}
