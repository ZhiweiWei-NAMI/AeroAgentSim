"""Expectation outcomes and explanations from acknowledged journal records."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.values import thaw

from aeroagentsim.services.narratives import NarrativeProjector, _entity_id
from aeroagentsim.services.storage import RunStorage

TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
KEY_PREFIXES = ("lowalt.incident.", "lowalt.handoff.", "lowalt.task.", "lowalt.food.", "lowalt.operator.")


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        identity = _entity_id(value)
        return identity if identity is not None else {k: _value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(v) for v in value]
    return value


def _matches(value: Any, pattern: Any) -> bool:
    if isinstance(pattern, dict):
        return isinstance(value, dict) and all(k in value and _matches(value[k], v) for k, v in pattern.items())
    return _value(value) == _value(pattern)


def _explanation(en: str, zh: str, evidence: list[dict] | None = None) -> dict:
    return {"text": en, "text_zh": zh, "evidence": copy.deepcopy(evidence) if evidence is not None else []}


def _related(event: dict, where: dict) -> bool:
    shared = set(where) & set(event["payload"]) & {"incident", "handoff", "task", "vehicle"}
    return bool(shared) and all(_matches(event["payload"][key], where[key]) for key in shared)


def _missing(expectation: dict, events: list[dict], approaches: dict[str, dict], terminal: bool, last_ns: int | None) -> dict:
    where = expectation["match"]["where"]
    schema = expectation["match"]["schema"]
    related = [e for e in events if _related(e, where)]
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
    approx = expectation.get("approx_s")
    if terminal and approx is not None and last_ns is not None and last_ns < round(approx * 1e9):
        return _explanation(f"The committed run ends at {last_ns / 1e9:g} s, before the configured step at {approx:g} s. No recorded event.",
                            f"已提交运行记录结束于 {last_ns / 1e9:g} 秒，早于配置的 {approx:g} 秒步骤。没有记录事件。")
    return _explanation("No recorded event.", "没有记录事件。")


def run_story(run_dir: Path, expectations: list[dict], *, locale: str = "en") -> dict:
    run_dir = Path(run_dir)
    storage = RunStorage(run_dir)
    storage.refresh_committed()
    manifest = storage.metadata()
    terminal = manifest.get("status") in TERMINAL
    document = json.loads((run_dir / "scenario.json").read_text())
    projector = NarrativeProjector.from_directory(run_dir)
    # The cooperation module owns its new narrative declarations. The service
    # projector remains the renderer, including strict missing-placeholder errors.
    coop_configs = [e["config"]["cooperation"] for e in document.get("engines", {}).values()
                    if e["plugin"] == "lowalt_tasks" and "cooperation" in e["config"]]
    if coop_configs:
        from aeroagentsim.packs.lowalt.cooperation import EVENTS
        for schema, declaration in EVENTS.items():
            projector._declarations[schema] = {
                "template": declaration.get("template", declaration.get("text")),
                "template_zh": declaration.get("template_zh", declaration.get("text_zh")),
                "category": declaration.get("category", "emergency"), "severity": declaration.get("severity", "info"),
                "entities": declaration.get("entities", []),
                **({"position": declaration["position"]} if "position" in declaration else {}),
            }
    events: list[dict] = []
    seen: set[str] = set()
    approaches: dict[str, dict] = {}
    last_ns: int | None = None
    cursor = 0
    while True:
        records = storage.records(cursor, 500)
        if not records:
            break
        for record in records:
            cursor = record["index"] + 1
            if "instant" in record:
                instant = decode_record(record["instant"])
                last_ns = instant.ns if hasattr(instant, "ns") else last_ns
            narration = projector.consume(record)
            narrated_index = 0
            for item in record.get("items", []):
                if "proposal" not in item:
                    continue
                proposal = decode_record(item["proposal"])
                if isinstance(proposal, FactWrite):
                    if proposal.key[1] == "lowalt.position":
                        position = thaw(proposal.value)
                        if not isinstance(position, list) or len(position) != 3:
                            raise ValueError("Recorded lowalt position is not an ENU vector / 记录的低空位置不是 ENU 向量")
                        for coop in coop_configs:
                            if proposal.key[0].id not in coop["watchers"]["vehicles"]:
                                continue
                            for incident in coop["incidents"]:
                                at_ns = proposal.valid.start.ns
                                if at_ns < round(incident["at_s"] * 1e9):
                                    continue
                                distance = math.dist(position[:2], incident["position"][:2])
                                previous = approaches.get(incident["id"])
                                if previous is None or distance < previous["distance_m"]:
                                    approaches[incident["id"]] = {"vehicle": proposal.key[0].id, "distance_m": distance,
                                        "at_ns": at_ns, "cut": record["index"], "notice_radius_m": coop["watchers"]["notice_radius_m"]}
                    continue
                if not isinstance(proposal, Emit) or proposal.kind != "event" or "message" not in item:
                    continue
                message = decode_record(item["message"])
                if not isinstance(message, Message) or message.kind != "event" or message.id in seen:
                    continue
                seen.add(message.id)
                payload = thaw(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("Recorded event payload is not an object / 记录的事件载荷不是对象")
                # Projector rows retain order but omit undeclared/hidden events.
                declaration = projector._declarations.get(message.schema_id)
                if declaration is not None and not declaration.get("hidden"):
                    row = narration[narrated_index]
                    narrated_index += 1
                    en, zh, entities = row["text"], row.get("text_zh"), row["entities"]
                else:
                    en, zh = "Recorded event " + message.schema_id, "记录事件 " + message.schema_id
                    entities = list(dict.fromkeys(identity for key in ("incident", "handoff", "task", "vehicle", "requester", "helper", "point") if key in payload and (identity := _entity_id(payload[key])) is not None))
                events.append({"schema": message.schema_id, "text": en, "text_zh": zh,
                               "at_ns": message.at.ns, "cut": record["index"], "entities": entities, "payload": _value(payload)})
                last_ns = max(last_ns, message.at.ns) if last_ns is not None else message.at.ns
    outcomes = []
    for expected in expectations:
        match = expected["match"]
        event = next((e for e in events if e["schema"] == match["schema"] and _matches(e["payload"], match["where"])), None)
        outcome = {"id": expected["id"], "text": expected["text"], "text_zh": expected["text_zh"],
                   "status": "happened" if event is not None else "missed" if terminal else "pending",
                   "at_ns": event["at_ns"] if event is not None else None, "cut": event["cut"] if event is not None else None,
                   "evidence": {"text": event["text"], "text_zh": event["text_zh"], "schema": event["schema"], "payload": event["payload"]} if event is not None else None,
                   "explanation": _missing(expected, events, approaches, terminal, last_ns) if event is None else None}
        outcomes.append(outcome)
    return {"run_id": manifest.get("id", run_dir.name), "status": manifest.get("status"), "terminal": terminal, "locale": locale,
            "expectations": outcomes, "events": [e for e in events if e["schema"].startswith(KEY_PREFIXES)],
            "last_at_ns": last_ns}
