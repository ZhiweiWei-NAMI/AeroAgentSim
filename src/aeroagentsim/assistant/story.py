"""Expectation outcomes and explanations from acknowledged journal records."""

from __future__ import annotations

import copy
import json
import math
import threading
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.values import thaw

from aeroagentsim.services.narratives import NarrativeProjector, _entity_id
from aeroagentsim.services.storage import RunStorage

TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
@dataclass
class StoryIndex:
    projector: NarrativeProjector
    cooperation: list[dict]
    cursor: int = 0
    last_ns: int | None = None
    events: list[dict] = field(default_factory=list)
    by_schema: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    seen: set[str] = field(default_factory=set)
    approaches: dict[str, dict] = field(default_factory=dict)
    lock: Any = field(default_factory=threading.RLock)


# Derived in-memory indexes only: the acknowledged journal is always authoritative.
_INDEXES: OrderedDict[tuple, StoryIndex] = OrderedDict()
_INDEX_LOCK = threading.RLock()


@lru_cache(maxsize=16)
def _pinned_expectations(run_dir: Path, identities: tuple) -> list[dict]:
    from .scene import expectations, summarize

    document = json.loads((run_dir / "scenario.json").read_text())
    return expectations(document, summarize(document, directory=run_dir))


def pinned_expectations(run_dir: Path) -> list[dict]:
    """The fixed run input supplies expectations for both assistant and Form versions."""
    run_dir = run_dir.resolve()
    identities = tuple(_identity(run_dir / name) for name in ("scenario.json", "runtime.registry.json", "behaviour.ir.json"))
    return copy.deepcopy(_pinned_expectations(run_dir, identities))


def _identity(path: Path) -> tuple | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _index(run_dir: Path, storage: RunStorage) -> tuple[tuple, StoryIndex]:
    journal = run_dir / "journal.jsonl"
    stat = journal.stat() if journal.exists() else None
    key = (str(run_dir.resolve()), *(_identity(run_dir / name) for name in
           ("scenario.json", "runtime.registry.json", "behaviour.ir.json")),
           (stat.st_dev, stat.st_ino) if stat else None)
    with _INDEX_LOCK:
        indexed = _INDEXES.get(key)
        end = storage.entries[-1]["index"] + 1 if storage.entries else 0
        if indexed is not None and indexed.cursor > end:
            del _INDEXES[key]
            indexed = None  # A shorter acknowledged prefix must never expose later cached events.
        if indexed is None:
            document = json.loads((run_dir / "scenario.json").read_text())
            projector = NarrativeProjector.from_directory(run_dir)
            coop = [e["config"]["cooperation"] for e in document.get("engines", {}).values()
                    if e["plugin"] == "lowalt_tasks" and "cooperation" in e["config"]]
            if coop:
                from aeroagentsim.packs.lowalt.cooperation import EVENTS
                for schema, declaration in EVENTS.items():
                    projector._declarations[schema] = {
                        "template": declaration.get("template", declaration.get("text")),
                        "template_zh": declaration.get("template_zh", declaration.get("text_zh")),
                        "category": declaration.get("category", "emergency"),
                        "severity": declaration.get("severity", "info"), "entities": declaration.get("entities", []),
                        **({"position": declaration["position"]} if "position" in declaration else {}),
                    }
            indexed = StoryIndex(projector, coop)
            _INDEXES[key] = indexed
        _INDEXES.move_to_end(key)
        while len(_INDEXES) > 8:
            _INDEXES.popitem(last=False)
        return key, indexed


def _extend(storage: RunStorage, indexed: StoryIndex) -> None:
    end = storage.entries[-1]["index"] + 1 if storage.entries else 0
    while indexed.cursor < end:
        records = storage.records(indexed.cursor, min(500, end - indexed.cursor))
        if not records:
            raise ValueError("Committed Story index has a journal gap / 已提交故事日志存在缺口")
        for raw in records:
            record = decode_record(raw)
            instant = record.get("instant")
            if instant is not None:
                indexed.last_ns = max(indexed.last_ns, instant.ns) if indexed.last_ns is not None else instant.ns
            narration = {row["message_id"]: row for row in indexed.projector.consume_decoded(record)}
            for item in record.get("items", []):
                proposal = item.get("proposal")
                if isinstance(proposal, FactWrite) and proposal.key[1] == "lowalt.position":
                    position = thaw(proposal.value)
                    if not isinstance(position, list) or len(position) != 3:
                        raise ValueError("Recorded lowalt position is not an ENU vector / 记录的低空位置不是 ENU 向量")
                    for coop in indexed.cooperation:
                        if proposal.key[0].id not in coop["watchers"]["vehicles"]:
                            continue
                        for incident in coop["incidents"]:
                            at_ns = proposal.valid.start.ns
                            if at_ns < round(incident["at_s"] * 1e9):
                                continue
                            distance = math.dist(position[:2], incident["position"][:2])
                            previous = indexed.approaches.get(incident["id"])
                            if previous is None or distance < previous["distance_m"]:
                                indexed.approaches[incident["id"]] = {"vehicle": proposal.key[0].id, "distance_m": distance,
                                    "at_ns": at_ns, "cut": record["index"], "notice_radius_m": coop["watchers"]["notice_radius_m"]}
                message = item.get("message")
                if not isinstance(proposal, Emit) or proposal.kind != "event" or not isinstance(message, Message) or message.kind != "event" or message.id in indexed.seen:
                    continue
                payload = thaw(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("Recorded event payload is not an object / 记录的事件载荷不是对象")
                row = narration.get(message.id)
                declaration = indexed.projector._declarations.get(message.schema_id)
                identities = list(dict.fromkeys(identity for name in ("incident", "handoff", "task", "vehicle", "actor", "requester", "helper", "point")
                                               if name in payload and (identity := _entity_id(payload[name])) is not None))
                event = {"schema": message.schema_id, "text": row["text"] if row else "Recorded event " + message.schema_id,
                         "text_zh": row.get("text_zh") if row else "记录事件 " + message.schema_id,
                         "at_ns": message.at.ns, "cut": record["index"], "entities": row["entities"] if row else identities,
                         "payload": _value(payload), "hidden": bool(declaration and declaration.get("hidden")),
                         "severity": row.get("severity") if row else None}
                indexed.events.append(event)
                indexed.by_schema[message.schema_id].append(event)
                indexed.seen.add(message.id)
                indexed.last_ns = max(indexed.last_ns, message.at.ns) if indexed.last_ns is not None else message.at.ns
            indexed.cursor = record["index"] + 1


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        identity = _entity_id(value)
        return identity if identity is not None else {k: _value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_value(v) for v in value]
    return value


def _matches(value: Any, pattern: Any) -> bool:
    if isinstance(pattern, dict) and set(pattern) == {"$in"}:
        return any(_value(value) == _value(option) for option in pattern["$in"])
    if isinstance(pattern, dict):
        return isinstance(value, dict) and all(k in value and _matches(value[k], v) for k, v in pattern.items())
    return _value(value) == _value(pattern)


def _explanation(en: str, zh: str, evidence: list[dict] | None = None) -> dict:
    return {"text": en, "text_zh": zh, "evidence": copy.deepcopy(evidence) if evidence is not None else []}


def _related(event: dict, where: dict) -> bool:
    shared = set(where) & set(event["payload"]) & {"incident", "handoff", "task", "vehicle", "actor", "capture"}
    return bool(shared) and all(_matches(event["payload"][key], where[key]) for key in shared)


def _missing(expectation: dict, events: list[dict], approaches: dict[str, dict], terminal: bool, last_ns: int | None) -> dict:
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
    traffic_failures = [e for e in related if e["schema"] in {"traffic.capture.failed", "traffic.decision.failed", "traffic.award.conflict"}]
    if traffic_failures:
        failure = traffic_failures[-1]
        return _explanation(failure["text"] + ": " + json.dumps(failure["payload"], ensure_ascii=False),
                            (failure.get("text_zh") or failure["text"]) + "：" + json.dumps(failure["payload"], ensure_ascii=False), [failure])
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
    key, indexed = _index(run_dir, storage)
    with indexed.lock:
        try:
            _extend(storage, indexed)
        except Exception:
            # A failed projection is never cached as a successful, partially decoded story.
            with _INDEX_LOCK:
                if _INDEXES.get(key) is indexed:
                    del _INDEXES[key]
            raise
        return _outcomes(manifest, expectations, indexed, terminal, locale)


def _outcomes(manifest: dict, expectations: list[dict], indexed: StoryIndex, terminal: bool, locale: str) -> dict:
    events, approaches, last_ns = indexed.events, indexed.approaches, indexed.last_ns
    outcomes = []
    for expected in expectations:
        match = expected["match"]
        found = [e for e in indexed.by_schema.get(match["schema"], []) if _matches(e["payload"], match["where"])]
        need = match.get("count", 1)
        released = sum(1 for e in indexed.by_schema.get(match["released_schema"], []) if _matches(e["payload"], match["where"])) if match.get("released_schema") else None
        # A counted expectation ("all 6 meals delivered") happens at its last required event.
        event = found[need - 1] if len(found) >= need else None
        outcome = {"id": expected["id"], "text": expected["text"], "text_zh": expected["text_zh"],
                   "status": "happened" if event is not None else "missed" if terminal else "pending",
                   "at_ns": event["at_ns"] if event is not None else None, "cut": event["cut"] if event is not None else None,
                   "evidence": {"text": event["text"], "text_zh": event["text_zh"], "schema": event["schema"], "payload": event["payload"]} if event is not None else None,
                   "explanation": None}
        if need > 1:
            outcome["progress"] = {"done": len(found), "total": need, **({"released": released} if released is not None else {})}
        if event is None and need > 1:
            release_note = (f" {released} of the {need} configured tasks were released.", f"已配置的 {need} 个任务中发布了 {released} 个。") if released is not None else ("", "")
            outcome["explanation"] = _explanation(f"{len(found)} of {need} recorded" + (" before the run ended." if terminal else " so far.") + release_note[0],
                                                  f"已记录 {len(found)}/{need}" + ("，运行已结束。" if terminal else "。") + release_note[1], found[-3:])
        elif event is None:
            outcome["explanation"] = _missing(expected, events, approaches, terminal, last_ns)
        outcomes.append(outcome)
    # A conditional branch is "not taken" once its alternative happened — not a failure.
    happened = {row["id"] for row in outcomes if row["status"] == "happened"}
    for expected, outcome in zip(expectations, outcomes):
        if outcome["status"] != "happened" and any(other in happened for other in expected.get("alternatives", [])):
            outcome["status"] = "not_taken"
    failures = [e for e in events if e["schema"].endswith((".failed", ".decision_failed", ".conflict"))]
    assessment = "unassessed" if not outcomes else "incomplete" if failures or any(r["status"] in {"missed", "pending"} for r in outcomes) else "satisfied"
    return {"run_id": manifest.get("id"), "status": manifest.get("status"), "terminal": terminal, "locale": locale,
            "expectations": outcomes, "events": [{k: v for k, v in e.items() if k != "hidden"} for e in events if not e["hidden"]],
            "coverage": {"committed_records": indexed.cursor, "recorded_events": len(events), "schemas": dict(Counter(e["schema"] for e in events))},
            "assessment": assessment, "failures": copy.deepcopy(failures),
            "detail": "No configured expectations; recorded events remain available, no success conclusion is implied / 未配置预期，仍可读取事件记录，不能据此判断成功" if not outcomes else None,
            "last_at_ns": last_ns}
