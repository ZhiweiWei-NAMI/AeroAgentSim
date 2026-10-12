"""Authored cooperation contracts, expansion and journaled model decisions."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, TypedDict

from aeroagentsim.agents.langgraph_client import JournalClient
from aeroagentsim.agents.provider import ProviderError

from .overlay import COOP_OPTIONS, HANDOFF_FIELDS, INCIDENT_FIELDS, TASK_KINDS, cooperation_overlay

FRAGMENT_PATH = Path(__file__).with_name("cooperation.snapshot.json")
CONTRACT_ERROR = (
    "cooperation: the scene's registry lacks the AeroGraph contracts oo:Incident/oo:CargoTransferReport; "
    "merge packs/lowalt/cooperation.snapshot.json into its registry snapshot"
)

EVENTS: dict[str, dict] = {}
for _name, _en, _zh, _entities, _position, _severity in (
    ("incident.occurred", "Incident {incident} occurred ({kind})", "事件{incident}发生（{kind}）", ["incident"], "position", "warning"),
    ("incident.noticed", "{vehicle} noticed {incident} at {distance_m} m", "{vehicle}在 {distance_m} 米处发现{incident}", ["incident", "vehicle"], "vehicle", "notice"),
    ("incident.decision", "{vehicle} chose {choice} for {incident}: {reason}", "{vehicle}对{incident}选择{choice}：{reason}", ["incident", "vehicle"], "vehicle", "notice"),
    ("incident.responding", "{vehicle} reached the response position above {incident}", "{vehicle}到达{incident}上方的响应位置", ["incident", "vehicle"], "position", "notice"),
    ("incident.photographed", "{vehicle} completed the observation of {incident}", "{vehicle}完成对{incident}的拍摄观测", ["incident", "vehicle"], "position", "info"),
    ("incident.reported", "{vehicle} reported {incident}", "{vehicle}上报{incident}", ["incident", "vehicle"], "vehicle", "notice"),
    ("handoff.requested", "{requester} requested {handoff} for {task}; candidates: {candidates}", "{requester}请求通过{handoff}交接{task}；候选：{candidates}", ["incident", "handoff", "task", "requester", "candidates"], "requester", "notice"),
    ("handoff.offer", "{helper} offered accepted={accepted} for {handoff}: {reason}", "{helper}对{handoff}回复接受={accepted}：{reason}", ["incident", "handoff", "task", "requester", "helper"], "helper", "notice"),
    ("handoff.accepted", "{helper} accepted {task} from {requester} at {point}; released: {others}", "{helper}接受在{point}从{requester}接取{task}；释放候选：{others}", ["incident", "handoff", "task", "requester", "helper", "point", "others"], "point", "notice"),
    ("handoff.declined", "{handoff} for {task} was declined: {reason}", "{task}的{handoff}被拒绝：{reason}", ["incident", "handoff", "task", "requester"], "requester", "warning"),
    ("handoff.dropped", "{requester} dropped {task} at {point} for {helper}", "{requester}将{task}放在{point}等待{helper}接取", ["incident", "handoff", "task", "requester", "helper", "point"], "point", "info"),
    ("handoff.transferred", "{helper} took over {task} from {requester} at {point}", "{helper}在{point}从{requester}接管{task}", ["incident", "handoff", "task", "requester", "helper", "point"], "helper", "info"),
):
    EVENTS["lowalt." + _name] = {
        "template": _en, "template_zh": _zh, "entities": _entities,
        "position": _position, "category": "emergency", "severity": _severity,
    }


def _object(value: Any, path: str, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: object required / 必须为对象")
    if extra := set(value) - required - optional:
        raise ValueError(f"{path}: unknown keys {sorted(extra)} / 未知配置项")
    if missing := required - set(value):
        raise ValueError(f"{path}: missing keys {sorted(missing)} / 缺少配置项")
    return value


def _text(value: Any, path: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{path}: nonempty string required / 必须为非空字符串")


def _number(value: Any, path: str, maximum: float | None = None) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{path}: finite nonnegative number required" + (f" (<= {maximum})" if maximum is not None else "") + " / 必须为有限非负数")


def _list(value: Any, path: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{path}: list required / 必须为列表")
    return value


def validate_vehicle_task_kinds(config: dict, *, vehicles: set[str]) -> None:
    if "vehicle_task_kinds" not in config:
        return
    overrides = config["vehicle_task_kinds"]
    if not isinstance(overrides, dict):
        raise ValueError("vehicle_task_kinds: object required / 必须为对象")
    for vehicle, kinds in overrides.items():
        path = f"vehicle_task_kinds.{vehicle}"
        if vehicle not in vehicles:
            raise ValueError(f"{path}: unknown UAV {vehicle} / 未知无人机")
        values = _list(kinds, path)
        if not values or any(type(kind) is not str or kind not in TASK_KINDS for kind in values) or len(set(values)) != len(values):
            raise ValueError(f"{path}: unique supported task kinds required / 必须为不重复的受支持任务类别")


def validate_config(config: dict, *, vehicles: set[str]) -> None:
    """Validate the cooperation object, without resolving operator credentials."""
    cfg = _object(config, "cooperation", {"handoff_points", "incidents", "watchers", "responder", "helpers", "photograph"}, {"provider"})
    ids: set[str] = set()
    for group, required in (("handoff_points", {"id", "position", "label", "label_zh"}), ("incidents", {"id", "position", "kind", "at_s", "label", "label_zh"})):
        for index, value in enumerate(_list(cfg[group], "cooperation." + group)):
            path = f"cooperation.{group}[{index}]"
            row = _object(value, path, required)
            for name in required - {"position", "at_s"}:
                _text(row[name], path + "." + name)
            if row["id"] in ids or row["id"] in vehicles:
                raise ValueError(f"{path}.id: duplicate entity {row['id']} / 实体标识重复")
            ids.add(row["id"])
            point = _list(row["position"], path + ".position")
            if len(point) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
                raise ValueError(f"{path}.position: finite ENU 3-vector required / 必须为有限的 ENU 三维坐标")
            if group == "incidents":
                _number(row["at_s"], path + ".at_s")
    watchers = _object(cfg["watchers"], "cooperation.watchers", {"vehicles", "notice_radius_m"})
    responder = _object(cfg["responder"], "cooperation.responder", {"mode", "order", "rules"}, {"goal"})
    helpers = _object(cfg["helpers"], "cooperation.helpers", {"vehicles", "ask_radius_m", "mode", "rules", "selection"}, {"goal"})
    for name, row in (("watchers", watchers), ("helpers", helpers)):
        values = _list(row["vehicles"], f"cooperation.{name}.vehicles")
        seen: set[str] = set()
        for index, identity in enumerate(values):
            path = f"cooperation.{name}.vehicles[{index}]"
            _text(identity, path)
            if identity not in vehicles:
                raise ValueError(f"{path}: unknown UAV {identity} / 未知无人机")
            if identity in seen:
                raise ValueError(f"{path}: duplicate UAV {identity} / 无人机重复")
            seen.add(identity)
    _number(watchers["notice_radius_m"], "cooperation.watchers.notice_radius_m")
    _number(helpers["ask_radius_m"], "cooperation.helpers.ask_radius_m")
    for name, row in (("responder", responder), ("helpers", helpers)):
        if type(row["mode"]) is not str or row["mode"] not in {"prescribed", "agent"}:
            raise ValueError(f"cooperation.{name}.mode: prescribed or agent required / 必须为 prescribed 或 agent")
        if "goal" in row:
            _text(row["goal"], f"cooperation.{name}.goal")
        elif row["mode"] == "agent":
            raise ValueError(f"cooperation.{name}.goal: required in agent mode / 智能体模式需要目标")
    order = _list(responder["order"], "cooperation.responder.order")
    if not order or any(type(v) is not str or v not in COOP_OPTIONS for v in order) or len(set(order)) != len(order):
        raise ValueError("cooperation.responder.order: unique supported response options required / 必须为不重复的受支持响应选项")
    rules = _object(responder["rules"], "cooperation.responder.rules", {"protected_task_kinds", "require_replacement", "min_battery_pct", "max_response_distance_m"})
    protected = _list(rules["protected_task_kinds"], "cooperation.responder.rules.protected_task_kinds")
    if any(type(v) is not str or v not in TASK_KINDS for v in protected) or len(set(protected)) != len(protected):
        raise ValueError("cooperation.responder.rules.protected_task_kinds: unique supported kinds required / 必须为不重复的受支持任务类别")
    if type(rules["require_replacement"]) is not bool:
        raise ValueError("cooperation.responder.rules.require_replacement: boolean required / 必须为布尔值")
    _number(rules["min_battery_pct"], "cooperation.responder.rules.min_battery_pct", 100)
    _number(rules["max_response_distance_m"], "cooperation.responder.rules.max_response_distance_m")
    rules = _object(helpers["rules"], "cooperation.helpers.rules", {"accept_when", "min_battery_pct"})
    if type(rules["accept_when"]) is not str or rules["accept_when"] not in {"idle", "always"}:
        raise ValueError("cooperation.helpers.rules.accept_when: idle or always required / 必须为 idle 或 always")
    _number(rules["min_battery_pct"], "cooperation.helpers.rules.min_battery_pct", 100)
    if type(helpers["selection"]) is not str or helpers["selection"] not in {"nearest", "first"}:
        raise ValueError("cooperation.helpers.selection: nearest or first required / 必须为 nearest 或 first")
    photo = _object(cfg["photograph"], "cooperation.photograph", {"dwell_s", "altitude_m"})
    for name in photo:
        _number(photo[name], "cooperation.photograph." + name)
    agent = any(row["mode"] == "agent" for row in (responder, helpers))
    if agent != ("provider" in cfg):
        raise ValueError("cooperation.provider: required iff a mode is agent / 仅智能体模式必须提供模型配置")
    if agent:
        provider = cfg["provider"]
        if isinstance(provider, dict) and set(provider) == {"profile"}:
            _text(provider["profile"], "cooperation.provider.profile")
        else:
            provider = _object(provider, "cooperation.provider", {"mode", "model", "responses"})
            if provider["mode"] != "stub":
                raise ValueError("cooperation.provider.mode: stub required / 必须为 stub")
            _text(provider["model"], "cooperation.provider.model")
            responses = provider["responses"]
            if not isinstance(responses, dict):
                raise ValueError("cooperation.provider.responses: named reply lists required / 必须为命名回复列表")
            for name, rows in responses.items():
                _text(name, "cooperation.provider.responses")
                for index, row in enumerate(_list(rows, "cooperation.provider.responses." + name)):
                    if not isinstance(row, dict):
                        raise ValueError(f"cooperation.provider.responses.{name}[{index}]: reply object required / 必须为回复对象")


def merge_contract_snapshot(snapshot: dict) -> dict:
    """Union compiled snapshots, retaining the fragment's source provenance."""
    fragment = json.loads(FRAGMENT_PATH.read_text())
    result = copy.deepcopy(snapshot)
    for group in ("types", "fields", "messages"):
        if group not in fragment["registry"]:
            continue
        rows = result["registry"].setdefault(group, [])
        existing = {row["id"]: row for row in rows}
        for row in fragment["registry"].get(group, []):
            if row["id"] in existing:
                if existing[row["id"]] != row:
                    raise ValueError(f"cooperation: conflicting registry {group} id {row['id']} / 契约内容冲突")
            else:
                rows.append(copy.deepcopy(row))
    schemas = result["registry"].setdefault("schemas", {})
    for identity, schema in fragment["registry"].get("schemas", {}).items():
        if identity in schemas and schemas[identity] != schema:
            raise ValueError(f"cooperation: conflicting registry schema {identity} / 模式冲突")
        schemas.setdefault(identity, copy.deepcopy(schema))
    def union(left: dict, right: dict, path: str) -> None:
        for key, value in right.items():
            if key not in left:
                left[key] = copy.deepcopy(value)
            elif isinstance(left[key], dict) and isinstance(value, dict):
                union(left[key], value, path + "." + key)
            elif left[key] != value and path.endswith(("raw_types", "types", "fields", "relations")):
                raise ValueError(f"cooperation: conflicting snapshot provenance {path}.{key} / 来源记录冲突")
    union(result.setdefault("details", {}), fragment.get("details", {}), "details")
    return result


def _is_a(type_id: str, parent: str, types: dict[str, dict], seen: set[str] | None = None) -> bool:
    if type_id == parent:
        return True
    seen = set() if seen is None else seen
    if type_id in seen:
        return False
    seen.add(type_id)
    return any(_is_a(p, parent, types, seen) for p in types.get(type_id, {}).get("parents", []))


def expand(document: dict) -> None:
    task_engines = [(name, engine["config"]) for name, engine in document["engines"].items() if engine["plugin"] == "lowalt_tasks"]
    if not any("cooperation" in cfg or "vehicle_task_kinds" in cfg for _, cfg in task_engines):
        return
    registry = document["registry"]
    types = {row["id"]: row for row in registry.get("types", [])}
    snapshot_path = Path(registry["snapshot"]) if "snapshot" in registry else None
    if snapshot_path is not None and snapshot_path.is_file():
        types.update({row["id"]: row for row in json.loads(snapshot_path.read_text())["registry"]["types"]})
    vehicles = {row["id"] for row in document["entities"] if _is_a(row["type"], "lowalt:UAV", types)}
    providers = []
    for engine_id, cfg in task_engines:
        validate_vehicle_task_kinds(cfg, vehicles=vehicles)
        if "cooperation" not in cfg:
            continue
        coop = cfg["cooperation"]
        validate_config(coop, vehicles=vehicles)
        if not {"oo:Incident", "oo:CargoTransferReport"} <= set(types):
            raise ValueError(CONTRACT_ERROR)
        descriptors = cooperation_overlay()
        for group, rows in descriptors.items():
            authored = registry.setdefault(group, [])
            known = {row["id"]: row for row in authored}
            for row in rows:
                if row["id"] in known and known[row["id"]] != row:
                    raise ValueError(f"cooperation: conflicting {group} descriptor {row['id']} / 描述符冲突")
                if row["id"] not in known:
                    authored.append(copy.deepcopy(row))
        entities = {row["id"]: row for row in document["entities"]}
        generated = []
        for point in coop["handoff_points"]:
            generated.append({"id": point["id"], "type": "lowalt:HandoffPoint", "label": point["label"], "label_zh": point["label_zh"], "facts": {"lowalt.site_position": point["position"], "lowalt.site_open": True, "lowalt.site_capacity": 1}})
        for incident in coop["incidents"]:
            labels = {key: incident[key] for key in ("label", "label_zh")}
            generated.append({"id": incident["id"], "type": "lowalt:Incident", **labels, "business": "emergency", "facts": {"lowalt.incident_kind": incident["kind"], "lowalt.incident_position": incident["position"], "lowalt.incident_state": "pending", "lowalt.incident_time_ns": round(incident["at_s"] * 1e9), "lowalt.incident_noticed_by": "", "lowalt.incident_responder": ""}})
            generated.append({"id": "handoff-" + incident["id"], "type": "lowalt:Handoff", **labels, "business": "emergency", "facts": {field: "idle" if field.endswith("_state") else "" for field in HANDOFF_FIELDS}})
        for row in generated:
            if row["id"] in entities:
                if entities[row["id"]] != row:
                    raise ValueError(f"cooperation: entity id collision {row['id']} / 实体标识冲突")
            else:
                document["entities"].append(copy.deepcopy(row))
                entities[row["id"]] = row
        bindings = document["bindings"]
        for name, fields in (("Incident", INCIDENT_FIELDS), ("Handoff", HANDOFF_FIELDS)):
            rule = {"writer": engine_id, "type": "lowalt:" + name, "fields": list(fields)}
            # Exact IDs allow separate task engines to own separate incidents.
            for incident in coop["incidents"]:
                row = {**rule, "ids": incident["id"] if name == "Incident" else "handoff-" + incident["id"]}
                if row not in bindings.setdefault("rules", []):
                    bindings["rules"].append(row)
                lifecycle = {"controller": engine_id, "type": "lowalt:" + name, "ids": row["ids"]}
                if lifecycle not in bindings.setdefault("lifecycle", []):
                    bindings["lifecycle"].append(lifecycle)
        for point in coop["handoff_points"]:
            row = {"writer": engine_id, "type": "lowalt:HandoffPoint", "ids": point["id"], "priority": 1, "fields": ["lowalt.site_position", "lowalt.site_open", "lowalt.site_capacity"]}
            if row not in bindings["rules"]:
                bindings["rules"].append(row)
        presentations = document.setdefault("presentation", [])
        for kind, field, color in (("Incident", "lowalt.incident_position", "#ff394f"), ("HandoffPoint", "lowalt.site_position", "#aa66ff")):
            if not any(row["typeId"] == "lowalt:" + kind for row in presentations):
                presentations.insert(0, {"typeId": "lowalt:" + kind, "positionField": field, "frame": "enu", "visual": {"kind": "marker", "color": color, "scale": 3.0}})
        declarations = document.setdefault("event_narratives", {})
        for schema, value in EVENTS.items():
            declarations.setdefault(schema, copy.deepcopy(value))
        if "provider" in coop:
            providers.append(coop["provider"])
    if any("cooperation" in cfg for _, cfg in task_engines):
        from .incident_capture import expand as expand_capture

        expand_capture(document)
    if providers:
        if any(provider != providers[0] for provider in providers):
            raise ValueError("cooperation.provider: all task engines must use the same provider / 所有任务引擎须使用同一模型配置")
        from aeroagentsim.agents.langgraph import record_descriptor
        descriptor = record_descriptor()
        if not any(row["id"] == descriptor["id"] for row in registry["messages"]):
            registry["messages"].append(descriptor)
        engine = {"plugin": "langgraph", "config": engine_config(providers[0])}
        if "lowalt_cooperation" in document["engines"] and document["engines"]["lowalt_cooperation"] != engine:
            raise ValueError("cooperation: lowalt_cooperation engine id is already used / 引擎标识已被占用")
        document["engines"].setdefault("lowalt_cooperation", engine)


def engine_config(provider: dict) -> dict:
    from aeroagentsim.algorithms.llm import engine_config as allocation_engine_config
    config = allocation_engine_config({"provider": provider})
    config.update(factory="aeroagentsim.packs.lowalt.cooperation:decision_graph", triggers=[{"schema": "lowalt.coop.decision_request", "topic": "lowalt.cooperation"}])
    config["grants"]["events"] = [{"schema": "lowalt.coop.decision_result", "topic": "lowalt.cooperation"}]
    if set(provider) == {"profile"}:
        config["provider"] = {"mode": "live", **provider}
    return config


class State(TypedDict):
    observation: dict[str, Any]
    trigger: dict[str, Any]
    outputs: list[dict[str, Any]]


def decision_graph(client: JournalClient, options: dict[str, Any]) -> Any:
    from langgraph.graph import END, START, StateGraph

    async def choose(state: State) -> dict[str, Any]:
        payload = state["trigger"]["payload"]
        def validate(value: dict[str, Any]) -> None:
            if set(value) != {"choice", "reason"} or type(value["choice"]) is not str or value["choice"] not in payload["allowed"] or type(value["reason"]) is not str or not 0 < len(value["reason"].strip()) <= 500:
                raise ValueError("reply requires an allowed choice and a short nonempty reason / 回复需要有效选项和简短理由")
        try:
            reply = await client.ask_json(
                payload["role"],
                "AEROAGENTSIM-COOPERATION-DECISION\nChoose one allowed action from the committed vehicle state and constraints. Reply only with JSON {\"choice\":\"<allowed option>\",\"reason\":\"<short sentence>\"}. Give the reason in English and Simplified Chinese. Do not claim an action has already happened.",
                {"role": payload["role"], "goal": payload["goal"], "state": json.loads(payload["state_json"]), "allowed": payload["allowed"], "constraints": payload["constraints"]},
                validate,
            )
        except (ProviderError, ValueError, TypeError, KeyError) as exc:
            reply = {"choice": "", "reason": f"Model failure / 模型失败: {exc}"}
        return {"outputs": [{"kind": "event", "schema": "lowalt.coop.decision_result", "topic": "lowalt.cooperation", "payload": {"incident": payload["incident"], "vehicle": payload["vehicle"], "role": payload["role"], **reply}}]}

    graph = StateGraph(State)
    graph.add_node("choose", choose)
    graph.add_edge(START, "choose")
    graph.add_edge("choose", END)
    return graph
