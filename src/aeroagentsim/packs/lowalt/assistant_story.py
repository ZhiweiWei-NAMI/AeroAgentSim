"""Lowalt-specific missing-step explanations and committed-position observations."""

from __future__ import annotations

import copy
import json
import math


def missing(expectation: dict, events: list[dict], approaches: dict, terminal: bool, last_ns: int | None) -> dict | None:
    from aeroagentsim.assistant.story import _related, _explanation

    where = expectation["match"]["where"]
    schema = expectation["match"]["schema"]
    related = [e for e in events if _related(e, where)]
    decision_failures = [e for e in related if e["schema"] == "lowalt.incident.decision_failed"]
    if decision_failures:
        failure = decision_failures[-1]
        return _explanation("Recorded decision failure: " + failure["payload"]["reason"],
                            "记录到决策失败：" + failure["payload"]["reason"], [failure])
    if schema == "lowalt.incident.noticed" and where.get("incident") in approaches:
        approach = approaches[where["incident"]]
        return _explanation(f"No notice event recorded. Closest recorded watcher approach: {approach['vehicle']}, {approach['distance_m']:.1f} m at {approach['at_ns'] / 1e9:g} s (notice radius {approach['notice_radius_m']:g} m). Sampled positions do not prove what happened between records.",
                            f"未记录发现事件。观察机最近记录接近：{approach['vehicle']}，在 {approach['at_ns'] / 1e9:g} 秒距事件 {approach['distance_m']:.1f} 米（观察半径 {approach['notice_radius_m']:g} 米）。采样位置不能证明记录之间发生的情况。", [approach])
    if schema.startswith("lowalt.handoff."):
        refusals = [e for e in related if e["schema"] == "lowalt.handoff.declined" or e["schema"] == "lowalt.handoff.offer" and e["payload"].get("accepted") is False]
        if refusals:
            reason = "; ".join(str(e["payload"]["reason"]) for e in refusals if "reason" in e["payload"])
            return _explanation("Recorded handoff refusal" + (": " + reason if reason else "; no reason recorded"),
                                "记录到交接拒绝" + ("：" + reason if reason else "；未记录原因"), refusals)
        if schema == "lowalt.handoff.declined":
            accepted = [e for e in related if e["schema"] == "lowalt.handoff.accepted"]
            if accepted:
                return _explanation("An acceptance was recorded; the conditional decline branch has no recorded event.", "记录到接受；条件性拒绝分支没有记录事件。", accepted)
        transferred = [e for e in related if e["schema"] == "lowalt.handoff.transferred" and "point" in e["payload"] and e["payload"]["point"] is None]
        if schema == "lowalt.handoff.dropped" and transferred:
            return _explanation("The task was transferred directly with point: null; no parcel drop was recorded.", "任务通过 point: null 直接转交；未记录包裹放下事件。", transferred)
    decisions = [e for e in related if e["schema"] == "lowalt.incident.decision"]
    if decisions and schema != "lowalt.incident.decision":
        decision = decisions[-1]
        payload = decision["payload"]
        details = {k: payload[k] for k in ("mode", "choice", "reason", "allowed", "blocked") if k in payload}
        return _explanation("Recorded responder decision: " + json.dumps(details, ensure_ascii=False) + ". No matching event for this step was recorded.",
                            "记录的响应决策：" + json.dumps(details, ensure_ascii=False) + "。此步骤没有匹配的记录事件。", [decision])
    failures = [e for e in related if e["schema"] == "lowalt.task.failed"]
    if failures:
        reason = failures[-1]["payload"].get("reason")
        return _explanation("Recorded task failure" + (": " + str(reason) if reason is not None else "; no reason recorded"),
                            "记录到任务失败" + ("：" + str(reason) if reason is not None else "；未记录原因"), [failures[-1]])
    return None


def story_setup(document: dict) -> dict:
    cooperation = [engine["config"]["cooperation"] for engine in document.get("engines", {}).values()
                   if engine["plugin"] == "lowalt_tasks" and "cooperation" in engine["config"]]
    declarations = {}
    if cooperation:
        from .cooperation import EVENTS
        for schema, declaration in EVENTS.items():
            declarations[schema] = {
                "template": declaration.get("template", declaration.get("text")),
                "template_zh": declaration.get("template_zh", declaration.get("text_zh")),
                "category": declaration.get("category", "emergency"), "severity": declaration.get("severity", "info"),
                "entities": declaration.get("entities", []),
                **({"position": declaration["position"]} if "position" in declaration else {}),
            }
    return {"context": cooperation, "declarations": declarations}


def observe_fact(context: list[dict], fact: dict, approaches: dict) -> dict:
    result = copy.deepcopy(approaches)
    if fact["field"] != "lowalt.position":
        return result
    position = fact["value"]
    if not isinstance(position, list) or len(position) != 3:
        raise ValueError("Recorded lowalt position is not an ENU vector / 记录的低空位置不是 ENU 向量")
    for cooperation in context:
        if fact["entity"] not in cooperation["watchers"]["vehicles"]:
            continue
        for incident in cooperation["incidents"]:
            at_ns = fact["at_ns"]
            if at_ns < round(incident["at_s"] * 1e9):
                continue
            distance = math.dist(position[:2], incident["position"][:2])
            previous = result.get(incident["id"])
            if previous is None or distance < previous["distance_m"]:
                result[incident["id"]] = {"vehicle": fact["entity"], "distance_m": distance,
                                          "at_ns": at_ns, "cut": fact["cut"],
                                          "notice_radius_m": cooperation["watchers"]["notice_radius_m"]}
    return result
