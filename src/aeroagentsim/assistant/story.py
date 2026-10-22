"""Expectation outcomes and explanations from acknowledged journal records."""

from __future__ import annotations

import copy
import ast
import json
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

from .catalogue import OperationCatalogue, metadata

TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
@dataclass
class StoryIndex:
    projector: NarrativeProjector
    contexts: dict[str, Any]
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
def _pinned_facts(run_dir: Path, identities: tuple) -> dict:
    from .scene import expectations, summarize

    document = json.loads((run_dir / "scenario.json").read_text())
    summary = summarize(document, directory=run_dir)
    return {"expectations": expectations(document, summary), "wind": summary.get("environment", {}).get("wind", {"speed_mps": None, "gusts": [], "profiles": {}})}


def pinned_expectations(run_dir: Path) -> list[dict]:
    """The fixed run input supplies expectations for both assistant and Form versions."""
    run_dir = run_dir.resolve()
    identities = tuple(_identity(run_dir / name) for name in ("scenario.json", "runtime.registry.json", "behaviour.ir.json"))
    return copy.deepcopy(_pinned_facts(run_dir, identities)["expectations"])


def pinned_wind(run_dir: Path) -> dict:
    """Share the pinned-input projection with expectations, including every vector."""
    run_dir = run_dir.resolve()
    identities = tuple(_identity(run_dir / name) for name in ("scenario.json", "runtime.registry.json", "behaviour.ir.json"))
    return copy.deepcopy(_pinned_facts(run_dir, identities)["wind"])


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
            contexts = {}
            catalogue = OperationCatalogue()
            for plugin in catalogue.plugins:
                setup = plugin.read._callbacks.get("story_setup") if plugin.read is not None else None
                if setup is not None:
                    contribution = setup(copy.deepcopy(document))
                    contexts[plugin.name] = contribution.get("context")
                    projector._declarations.update(contribution.get("declarations", {}))
            indexed = StoryIndex(projector, contexts)
            _INDEXES[key] = indexed
        _INDEXES.move_to_end(key)
        while len(_INDEXES) > 8:
            _INDEXES.popitem(last=False)
        return key, indexed


def _extend(storage: RunStorage, indexed: StoryIndex) -> None:
    catalogue = OperationCatalogue()
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
                if isinstance(proposal, FactWrite):
                    fact = {"field": proposal.key[1], "entity": proposal.key[0].id, "value": thaw(proposal.value),
                            "at_ns": proposal.valid.start.ns, "cut": record["index"]}
                    for plugin in catalogue.plugins:
                        observe = plugin.read._callbacks.get("observe_fact") if plugin.read is not None else None
                        if observe is not None and plugin.name in indexed.contexts:
                            indexed.approaches = observe(indexed.contexts[plugin.name], fact, indexed.approaches)
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


def failure_reason(payload: dict) -> str | None:
    """Keep the recorded business reason; internal receipt contexts stay in details."""
    reason = payload.get("reason")
    if isinstance(reason, str) and reason.lstrip().startswith("{") and len(reason) > 4000:
        return None  # The full receipt remains available in Story evidence.
    for _ in range(3):
        if isinstance(reason, dict):
            reason = reason.get("reason")
        elif isinstance(reason, str) and reason.lstrip().startswith("{") and len(reason) <= 4000:
            try:
                reason = json.loads(reason)
            except ValueError:
                try:
                    reason = ast.literal_eval(reason)
                except (ValueError, SyntaxError):
                    break
        else:
            break
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason[:220] + ("…" if len(reason) > 220 else "")


def failure_summary(failures: list[dict], *, limit: int = 6) -> list[dict]:
    """Group all failures, prioritising business failures over repeated conflicts."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in failures:
        groups[event["schema"]].append(event)
    priorities = metadata("failure_priority", {})
    captions = metadata("failure_labels", {})
    ordered = sorted(groups, key=lambda schema: (priorities.get(schema, 2 if schema.endswith(".conflict") else 1), schema))
    result = []
    for schema in ordered[:limit]:
        rows = groups[schema]
        last = max(rows, key=lambda row: row["at_ns"])
        label, label_zh = captions.get(schema, ("Recorded conflicts", "记录的冲突") if schema.endswith(".conflict") else ("Recorded failures", "记录的失败"))
        pairs: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in rows:
            payload = row.get("payload", {})
            a, b = _entity_id(payload.get("a")), _entity_id(payload.get("b"))
            if a is not None and b is not None:
                pairs[tuple(sorted((a, b)))].append(row)
        pair_counts = []
        for pair, events in pairs.items():
            example = max(events, key=lambda event: event["at_ns"])
            pair_counts.append({"entities": list(pair), "count": len(events), "text": example["text"][:240],
                                "text_zh": (example.get("text_zh") or example["text"])[:240]})
        result.append({"schema": schema, "count": len(rows),
                       "text": label, "text_zh": label_zh,
                       "pairs": pair_counts,
                       "last_example": {"text": last["text"][:240], "text_zh": (last.get("text_zh") or last["text"])[:240]},
                       "first_at_ns": min(row["at_ns"] for row in rows), "last_at_ns": last["at_ns"],
                       "reason": failure_reason(last.get("payload", {}))})
    return result


def _missing(expectation: dict, events: list[dict], approaches: dict[str, dict], terminal: bool, last_ns: int | None) -> dict:
    for plugin in OperationCatalogue().plugins:
        if plugin.read is not None and plugin.read.missing is not None:
            result = plugin.read.missing(expectation, events, approaches, terminal, last_ns)
            if result is not None:
                return result
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
        stochastic = match.get("count_basis") == "released"
        configured = need
        if stochastic:
            releases = {e["payload"]["task"]: e for e in indexed.by_schema[match["released_schema"]] if _matches(e["payload"], match["where"])}
            # Count distinct released tasks, so a duplicate receipt cannot stand in for another order.
            unique = {}
            for record in found:
                task = record["payload"]["task"]
                if task in releases and task not in unique:
                    unique[task] = record
            found = list(unique.values())
            released = need = len(releases)
        # A counted expectation ("all 6 meals delivered") happens at its last required event.
        event = found[need - 1] if need > 0 and len(found) >= need else None
        outcome = {"id": expected["id"], "text": expected["text"], "text_zh": expected["text_zh"],
                   "status": "not_evaluated" if stochastic and need == 0 and terminal else "happened" if event is not None else "missed" if terminal else "pending",
                   "at_ns": event["at_ns"] if event is not None else None, "cut": event["cut"] if event is not None else None,
                   "evidence": {"text": event["text"], "text_zh": event["text_zh"], "schema": event["schema"], "payload": event["payload"]} if event is not None else None,
                   "explanation": None}
        if need > 1 or stochastic:
            outcome["progress"] = {"done": len(found), "total": need, **({"released": released} if released is not None else {}), **({"configured": configured} if stochastic else {})}
        if stochastic:
            estimate = match["demand"].get("expected_released")
            note = (f"Stochastic arrivals: {released} tasks actually released from {configured} configured slots. Execution checks use released tasks; slots are a cap, not a fixed demand target.",
                    f"随机到达：{configured} 个配置名额中实际发布了 {released} 个任务。执行检查以实际发布的任务为准；名额是上限，不是固定需求目标。")
            if estimate is not None:
                note = (note[0] + f" Expected arrivals: about {estimate}.", note[1] + f"预计到达约 {estimate} 个。")
            if need == 0:
                note = (note[0] + " No tasks have been released; execution is not evaluated.", note[1] + "尚未发布任务，执行情况未评估。")
            elif event is None:
                note = (note[0] + f" {len(found)} of {need} recorded.", note[1] + f"已记录 {len(found)}/{need}。")
            outcome["explanation"] = _explanation(*note, found[-3:])
        elif event is None and need > 1:
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
    assessed = [row for row in outcomes if row["status"] != "not_evaluated"]
    assessment = "unassessed" if not assessed else "incomplete" if failures or any(r["status"] in {"missed", "pending"} for r in assessed) else "satisfied"
    return {"run_id": manifest.get("id"), "status": manifest.get("status"), "terminal": terminal, "locale": locale,
            "expectations": outcomes, "events": [{k: v for k, v in e.items() if k != "hidden"} for e in events if not e["hidden"]],
            "coverage": {"committed_records": indexed.cursor, "recorded_events": len(events), "schemas": dict(Counter(e["schema"] for e in events))},
            "assessment": assessment, "failures": copy.deepcopy(failures), "failure_summary": failure_summary(failures),
            "detail": "No configured expectations; recorded events remain available, no success conclusion is implied / 未配置预期，仍可读取事件记录，不能据此判断成功" if not outcomes else None,
            "last_at_ns": last_ns}
