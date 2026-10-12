from __future__ import annotations

import json
import sys
import types
from dataclasses import asdict
from pathlib import Path

from aerokernel import Journal
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp

from aeroagentsim.assistant.story import run_story
from aeroagentsim.services.storage import RunStorage

DECLARATIONS = {
    "lowalt.handoff.offer": {"template": "{helper} offer accepted={accepted}: {reason}", "template_zh": "{helper}提议接受={accepted}：{reason}", "category": "emergency", "severity": "info", "entities": ["helper", "incident"]},
    "lowalt.handoff.accepted": {"template": "{helper} accepted", "template_zh": "{helper}接受", "category": "emergency", "severity": "info", "entities": ["helper", "incident"]},
    "lowalt.incident.decision": {"template": "{vehicle} chose {choice}: {reason}", "template_zh": "{vehicle}选择 {choice}：{reason}", "category": "emergency", "severity": "info", "entities": ["vehicle", "incident"]},
    "lowalt.incident.noticed": {"template": "{vehicle} noticed {incident}", "template_zh": "{vehicle}发现 {incident}", "category": "emergency", "severity": "info", "entities": ["vehicle", "incident"]},
}


def event(schema: str, payload: dict, at_ns: int) -> dict:
    msg = Message(id=f"msg:{schema}:{at_ns}", kind="event", schema_id=schema, source_kind="partition", source="tasks",
                  sequence=at_ns, at=Instant(at_ns), available=Instant(at_ns), payload=payload, causes=())
    return {"kind": "operation", "partition": "tasks", "proposal": Emit(kind="event", schema_id=schema, target_or_topic="lowalt.events", at=msg.at, payload=payload), "message": msg}


def expectation(schema: str, where: dict, identity="expected", at=None) -> dict:
    return {"id": identity, "text": "Expected step", "text_zh": "预期步骤", "layer": "behaviour", "match": {"schema": schema, "where": where}, "approx_s": at, "precheck": None, "depends_on": []}


def journal_run(directory: Path, records: list[dict], *, status="completed", document=None, committed_cut=None) -> Path:
    directory.mkdir()
    registry = MemoryRegistry((TypeDescriptor("lowalt:UAV"),),
                              (FieldDescriptor("lowalt.position", "lowalt:UAV", {"type": "vector", "length": 3, "items": {"type": "number"}}, {"unit": "m", "frame": "enu", "role": "state"}),))
    (directory / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    (directory / "scenario.json").write_text(json.dumps({"id": "story", "entities": [], "event_narratives": DECLARATIONS, **(document or {})}))
    (directory / "manifest.json").write_text(json.dumps({"id": "recorded-run", "status": status}))
    journal = Journal(directory / "journal.jsonl")
    journal.append({"type": "header", "format": "aerokernel.journal", "major": 1, "minor": 2, "index": 0,
                    "budget": asdict(journal.budget), "instant": encode(Instant(0))})
    offsets = [journal.acknowledged_bytes]
    for i, record in enumerate(records, 1):
        journal.append(encode({"type": "commit", "index": i, "instant": Instant(record["at_ns"]), "items": record["items"], "batches": []}))
        offsets.append(journal.acknowledged_bytes)
    journal.close()
    storage = RunStorage(directory)
    storage.index()
    if committed_cut is not None:
        storage.publish_prefix(offsets[committed_cut], committed_cut)
    return directory


def test_story_matches_events_and_terminal_absence(tmp_path: Path) -> None:
    run = journal_run(tmp_path / "run", [{"at_ns": 10_000_000_000, "items": [event("lowalt.task.created", {"task": "package-1", "kind": "parcel", "priority": 1, "origin": "depot", "destination": "customer"}, 10_000_000_000)]}])
    story = run_story(run, [expectation("lowalt.task.created", {"task": "package-1"}, "created"), expectation("lowalt.task.completed", {"task": "package-1"}, "delivery")])
    created, delivery = story["expectations"]
    assert created["status"] == "happened" and created["cut"] == 1
    assert created["at_ns"] == 10_000_000_000
    assert delivery["status"] == "missed"
    assert delivery["explanation"]["text"] == "No recorded event."
    assert story["events"][0]["schema"] == "lowalt.task.created"
    assert story["events"][0]["text_zh"]


def test_live_prefix_omits_unacknowledged_event(tmp_path: Path) -> None:
    run = journal_run(tmp_path / "run", [
        {"at_ns": 1, "items": []},
        {"at_ns": 2, "items": [event("lowalt.task.created", {"task": "package-1", "kind": "parcel", "priority": 1}, 2)]},
    ], status="running", committed_cut=1)
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    story = run_story(run, [expectation("lowalt.task.created", {"task": "package-1"})])
    assert story["expectations"][0]["status"] == "pending"
    assert story["events"] == []
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before


def test_declined_offer_explains_missing_transfer(tmp_path: Path) -> None:
    run = journal_run(tmp_path / "run", [{"at_ns": 20, "items": [event("lowalt.handoff.offer", {"incident": "fall", "helper": "courier-1", "accepted": False, "reason": "busy"}, 20)]}])
    story = run_story(run, [expectation("lowalt.handoff.transferred", {"incident": "fall"})])
    missed = story["expectations"][0]
    assert missed["status"] == "missed"
    assert "busy" in missed["explanation"]["text"]
    assert missed["explanation"]["evidence"][0]["cut"] == 1


def test_blocked_decision_reason_is_recorded_evidence(tmp_path: Path) -> None:
    run = journal_run(tmp_path / "run", [{"at_ns": 30, "items": [event("lowalt.incident.decision", {"incident": "fall", "vehicle": "parcel-1", "mode": "agent", "choice": "report_only", "reason": "invalid agent reply; prescribed fallback", "blocked": {"respond_now": "battery below minimum"}, "allowed": ["report_only"]}, 30)]}])
    story = run_story(run, [expectation("lowalt.incident.photographed", {"incident": "fall"})])
    explanation = story["expectations"][0]["explanation"]
    assert "battery below minimum" in explanation["text"]
    assert "prescribed fallback" in explanation["text"]


def test_stopped_before_scheduled_step(tmp_path: Path) -> None:
    run = journal_run(tmp_path / "run", [{"at_ns": 10_000_000_000, "items": []}], status="stopped")
    story = run_story(run, [expectation("lowalt.incident.occurred", {"incident": "fall"}, at=90)])
    assert "before the configured step at 90" in story["expectations"][0]["explanation"]["text"]


def test_closest_approach_uses_recorded_positions_after_incident(tmp_path: Path, monkeypatch) -> None:
    module = types.ModuleType("aeroagentsim.packs.lowalt.cooperation")
    module.EVENTS = DECLARATIONS
    monkeypatch.setitem(sys.modules, "aeroagentsim.packs.lowalt.cooperation", module)
    ref = EntityRef(run_id="story", epoch="0", id="watcher-1", generation=0, type_id="lowalt:UAV")
    def position(at_ns: int, x: float) -> dict:
        return {"kind": "operation", "partition": "motion", "proposal": FactWrite(key=(ref, "lowalt.position"), value=[x, 0.0, 125.0], acquired=Stamp(clock_id="c", numerator=at_ns, denominator=1, mapping_id="m"), valid=Interval(Instant(at_ns), None))}
    coop = {"incidents": [{"id": "fall", "position": [0, 0, 0], "at_s": 10}], "watchers": {"vehicles": ["watcher-1"], "notice_radius_m": 150}}
    run = journal_run(tmp_path / "run", [{"at_ns": 0, "items": [position(0, 0)]}, {"at_ns": 11_000_000_000, "items": [position(11_000_000_000, 400)]}, {"at_ns": 12_000_000_000, "items": [position(12_000_000_000, 250)]}], document={"engines": {"tasks": {"plugin": "lowalt_tasks", "config": {"cooperation": coop}}}})
    story = run_story(run, [expectation("lowalt.incident.noticed", {"incident": "fall"})])
    explanation = story["expectations"][0]["explanation"]
    assert "250.0 m at 12 s" in explanation["text"]
    assert explanation["evidence"][0]["cut"] == 3
