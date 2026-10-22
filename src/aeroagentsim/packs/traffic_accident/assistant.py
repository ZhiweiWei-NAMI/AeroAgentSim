"""Traffic assistant operations, scene reads and conditional receipt expectations."""

from __future__ import annotations

import copy
import re
from collections import deque
from functools import partial

from aeroagentsim.assistant_api import (
    AssistantOperationPlugin, AssistantReadSpec, CompileContext, OperationSpec, thaw,
)
from aeroagentsim.packs.lowalt.assistant_scene import POSITION_PRECEDENCE, _common, _plain_payload, _position
from aeroagentsim.services.narratives import _chinese_label

from .narratives import EVENT_NARRATIVES

SECOND = 1_000_000_000


def summarize(context: CompileContext) -> dict:
    document = thaw(context.document)
    entities = {entity["id"]: entity for entity in document.get("entities", [])}
    packages = thaw(context.data.get("packages", ()))
    types = thaw(context.data.get("types", {}))
    def contract(type_id: str) -> dict:
        ancestry, seen, pending = [], {type_id}, deque([type_id])
        while pending:
            for parent in types.get(pending.popleft(), {}).get("parents", []):
                if parent not in seen:
                    ancestry.append(parent)
                    seen.add(parent)
                    pending.append(parent)
        return {"type": type_id, "ancestry": ancestry, "label": type_id}
    fleets, workloads, events = [], [], []
    for type_id, identity, en, zh in (("aas:TrafficUAV", "traffic-uavs", "Traffic UAVs", "交通无人机"),
                                      ("aas:TrafficRoadVehicle", "traffic-road-vehicles", "Road vehicles", "道路车辆")):
        members = [e for e in entities.values() if e["type"] == type_id]
        if not members:
            continue
        # Energy is read in joules; no battery percentage is invented without a capacity.
        engines = [e["config"] for e in document.get("engines", {}).values() if e["config"].get("type_id") == type_id]
        capacity = next((e["energy"]["capacity_j"] for e in engines if "capacity_j" in e.get("energy", {})), None)
        states = [{"id": e["id"], "label": e.get("label", e["id"]), "label_zh": e.get("label_zh", _chinese_label(e.get("label", e["id"]))),
                   "position": _position(e, context.data.get("position_fields")), "battery_pct": e["facts"]["traffic.uav.energy_j"] / capacity * 100 if capacity is not None and capacity > 0 and "traffic.uav.energy_j" in e["facts"] else None,
                   "payload_capacity_kg": None, "task_kinds": [], "current_task": _plain_payload(e["facts"].get("traffic.actor.current_task")),
                   "details": copy.deepcopy(e["facts"])} for e in members]
        fleets.append({"id": identity, "role": None, "domain": "traffic", "label": en, "label_zh": zh, "count": len(members),
                       "members": [e["id"] for e in members], "member_states": states, "vehicle_class": type_id,
                       "task_kinds": [], "start": {"place": None, "label": None}, "battery_pct": _common([s["battery_pct"] for s in states]),
                       "payload_capacity_kg": None, "contract": contract(type_id)})
    groups: dict[str, list[dict]] = {}
    for e in entities.values():
        if e["type"] == "aas:TrafficTask":
            groups.setdefault(e["facts"]["traffic.task.kind"], []).append(e)
    for kind, members in groups.items():
        ids = [e["id"] for e in members]
        actors = [e["id"] for e in entities.values() if _plain_payload(e["facts"].get("traffic.actor.current_task")) in ids]
        workloads.append({"id": "traffic-tasks:" + kind, "domain": "traffic", "kind": kind, "label": kind.replace("_", " "), "label_zh": kind.replace("_", " "),
                          "count": len(ids), "tasks": ids, "from": actors, "to": [], "release": {"mode": "behaviour"}, "priority": None,
                          "payload_kg": None, "protected": None, "work_route": None,
                          "task_states": [{"id": e["id"], "details": copy.deepcopy(e["facts"])} for e in members]})
    chains = {name: chain for package in packages for name, chain in package.get("chains", {}).items()}
    bound = {binding["chain"] for package in packages for binding in package.get("bindings", [])}
    report = chains.get("traffic.incident_report", {}) if "traffic.incident_report" in bound else {}
    delay = next((a["duration_ns"] / SECOND for t in report.get("transitions", []) for a in t.get("actions", []) if a.get("id") == "accident-time" and a.get("kind") == "delay"), None)
    incidents = [e for e in entities.values() if e["type"] == "aas:TrafficIncident"]
    if report:
        for incident in incidents:
            events.append({"id": "traffic-incident:" + incident["id"], "domain": "traffic", "kind": "traffic_accident", "label": "Traffic accident", "label_zh": "交通事故",
                           "at_s": delay if incident["facts"].get("traffic.incident.source_kind") == "authored_staged" else None,
                           "place": incident["id"], "position": _position(incident, context.data.get("position_fields")), "layer": "setup", "schema": "traffic.incident.activated", "params": {"incident": incident["id"]}})
    return {"fleets": fleets, "workloads": workloads, "events": events,
            "routine": [{"kind": work["kind"], "text": f"Follow the configured {work['kind']} task and route; recorded events determine the outcome.", "text_zh": f"执行已配置的 {work['kind']} 任务和路线；结果以记录事件为准。"} for work in workloads],
            "traffic": {"bound_chains": sorted(bound & {name for name in chains if name.startswith("traffic.")}),
                        "incidents": [{"id": e["id"], "facts": copy.deepcopy(e["facts"])} for e in incidents]}}


def expectations(document: dict, summary: dict) -> list[dict]:
    result = []
    def add(identity: str, schema: str, where: dict, en: str, zh: str, *, layer: str = "behaviour", at=None, precheck=None, depends=(), count: int | None = None, released: str | None = None, demand: dict | None = None, alternatives=()) -> str:
        match = {"schema": schema, "where": where, **({"count": count} if count is not None else {}), **({"released_schema": released} if released else {}),
                 **({"count_basis": "released", "demand": demand} if demand is not None else {})}
        result.append({"id": identity, "text": en, "text_zh": zh, "layer": layer, "match": match,
                       "approx_s": at, "precheck": precheck, "depends_on": list(depends), **({"alternatives": list(alternatives)} if alternatives else {})})
        return identity
    for event in summary.get("events", []):
        if event.get("domain") == "traffic" and event.get("schema") is not None:
            add("event:" + event["id"], event["schema"], event["params"], event["label"], event["label_zh"],
                layer="setup", at=event["at_s"])
    traffic = summary.get("traffic", {})
    chains = traffic.get("bound_chains", [])
    if traffic.get("incidents") and "traffic.incident_report" in chains:
        stages = ["traffic.incident.detected", "traffic.broadcast"]
        if "traffic.capture_execute" in chains:
            stages += ["traffic.capture.requested", "traffic.capture.stored", "traffic.capture.accepted"]
        if any("bid" in chain or "award" in chain for chain in chains):
            stages[2:2] = ["traffic.bid.joined", "traffic.award.committed"]
        # Some receipts omit optional incident links: scope them to configured UAV actors.
        actors = [m for f in summary["fleets"] if f.get("domain") == "traffic" and f["vehicle_class"] == "aas:TrafficUAV" for m in f["members"]]
        for schema in stages:
            declaration = EVENT_NARRATIVES[schema]
            where = {"actor": {"$in": actors}} if schema.startswith(("traffic.capture.", "traffic.bid.", "traffic.award.")) else {"incident": {"$in": [e["id"] for e in traffic["incidents"]]}}
            en = re.sub(r"\{[^}]+\}", "a configured participant", declaration["template"])
            zh = re.sub(r"\{[^}]+\}", "已配置对象", declaration["template_zh"])
            add("traffic:" + schema, schema, where, "If permitted: " + en, "若条件允许：" + zh)
    return result


def projection_order(section: str, rows, summary) -> list[int]:
    if section == "lanes":
        priorities = {"system": 0, "vehicle": 10, "fleet": 10, "incident": 40}
        return [priorities[row["kind"]] for row in rows]
    return [10 if row["id"].startswith("event:") else 30 for row in rows]


def interactions(summary: dict) -> dict:
    lanes = [{"id": "system", "label": "Task allocation", "label_zh": "任务分配", "kind": "system"}]
    steps = []
    for fleet in summary.get("fleets", []):
        if fleet.get("domain") != "traffic":
            continue
        lanes.append({"id": fleet["id"], "label": fleet["label"], "label_zh": fleet["label_zh"],
                      "kind": "vehicle" if fleet["members"] == [fleet["id"]] else "fleet"})
        if summary.get("cooperation"):
            for state in fleet.get("member_states", []):
                if state["id"] != fleet["id"]:
                    lanes.append({"id": state["id"], "label": state.get("label", state["id"]),
                                  "label_zh": state.get("label_zh", state["id"]), "kind": "vehicle"})
    for event in summary.get("events", []):
        if event.get("domain") == "traffic":
            if not any(lane["id"] == event["place"] for lane in lanes):
                lanes.append({"id": event["place"], "label": event["label"], "label_zh": event["label_zh"], "kind": "incident"})
            steps.append({"id": "event:" + event["id"], "from": "system", "to": event["place"] or "system",
                          "text": event["label"], "text_zh": event["label_zh"], "layer": "setup", "decided_by": None,
                          "branch": None, "at_s": event["at_s"], "expect": "event:" + event["id"] if event.get("schema") else None})
    for expected in expectations({}, summary):
        if expected["id"].startswith("traffic:"):
            steps.append({"id": expected["id"], "from": "system", "to": "system", "text": expected["text"], "text_zh": expected["text_zh"],
                          "layer": "behaviour", "decided_by": "prescribed", "branch": None, "at_s": None, "expect": expected["id"]})
    return {"lanes": lanes, "steps": steps}


def plugin() -> AssistantOperationPlugin:
    from aeroagentsim.packs.lowalt.assistant import (
        OPERATION_SPECS, compile_operation, operation_dependencies, _builtin_dependency_applies, _staging,
    )
    from aeroagentsim.packs.lowalt.assistant_display import BRIEF

    names = ("set_control", "set_duration", "set_run_mode", "set_wind", "add_gust", "remove_gust", "schedule_event")
    positions = ["he.aircraft.position_enu_m", "traffic.road.position_enu_m", "traffic.incident.position_enu_m"]
    operations = tuple(OperationSpec(
        id="traffic." + name, parameters=OPERATION_SPECS[name]["parameters"], title=BRIEF[name][0], title_zh=BRIEF[name][1],
        description=OPERATION_SPECS[name]["description"], description_zh=OPERATION_SPECS[name]["description_zh"],
        compile=partial(compile_operation, name), topic=OPERATION_SPECS[name]["topic"],
        display={**OPERATION_SPECS[name], "legacy_operation": name, "namespaced_only": True},
    ) for name in names)
    return AssistantOperationPlugin(name="traffic", api_version=1, operations=operations,
                                    read=AssistantReadSpec(summarize=summarize, expectations=expectations, interactions=interactions,
                                                           missing=missing, priority={"events": -1},
                                                           _callbacks={"projection_order": projection_order}),
                                    metadata={"position_fields": positions,
                                              "position_field_priority": {name: POSITION_PRECEDENCE.index(name) for name in positions},
                                              "failure_labels": {"traffic.capture.failed": ["Capture failures", "拍摄失败"]},
                                              "failure_priority": {"traffic.capture.failed": 0}, "summary_defaults": {"traffic": {}}},
                                    dependencies=operation_dependencies, _host=_staging,
                                    _dependency_applies=_builtin_dependency_applies)


def missing(expectation: dict, events: list[dict], approaches: dict, terminal: bool, last_ns: int | None) -> dict | None:
    from aeroagentsim.assistant.story import _related, _explanation, failure_reason

    schema, where = expectation["match"]["schema"], expectation["match"]["where"]
    related = [event for event in events if _related(event, where)]
    # A later capture failure cannot explain an earlier award/bid/detection.
    failure_schemas = ({"traffic.capture.failed"} if schema.startswith("traffic.capture.") else
                       {"traffic.decision.failed", "traffic.award.conflict"} if schema == "traffic.award.committed" else set())
    traffic_failures = [e for e in related if e["schema"] in failure_schemas]
    if traffic_failures:
        failure = traffic_failures[-1]
        reason = failure_reason(failure["payload"])
        return _explanation(failure["text"] + (": " + reason if reason else ""),
                            (failure.get("text_zh") or failure["text"]) + ("：" + reason if reason else ""), [failure])
    return None
