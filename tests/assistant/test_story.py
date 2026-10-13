from __future__ import annotations

import json
import sys
import types
from dataclasses import asdict
from pathlib import Path

import pytest
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


@pytest.mark.parametrize("released,completed", [(0, 0), (2, 2), (3, 2), (6, 6)])
def test_counted_outcome_retains_the_configured_target(tmp_path: Path, released: int, completed: int) -> None:
    tasks = [f"meal-{i}" for i in range(1, 7)]
    created = {"kind": "food", "priority": 1, "origin": "kitchen", "destination": "customer"}
    records = [{"at_ns": (i + 1) * 1_000_000_000, "items": [event("lowalt.task.created", {"task": f"meal-{i + 1}", **created}, (i + 1) * 1_000_000_000)]} for i in range(released)]
    records += [{"at_ns": (i + 10) * 1_000_000_000, "items": [event("lowalt.task.completed", {"task": f"meal-{i + 1}", "vehicle": "uav-1", "latency_s": 9.0}, (i + 10) * 1_000_000_000)]} for i in range(completed)]
    counted = expectation("lowalt.task.completed", {"task": {"$in": tasks}}, "meals")
    counted["match"].update(count=6, released_schema="lowalt.task.created")
    releases = expectation("lowalt.task.created", {"task": {"$in": tasks}}, "release")
    releases["match"].update(count=6, released_schema="lowalt.task.created")
    outcome, release = run_story(journal_run(tmp_path / "run", records), [counted, releases])["expectations"]
    assert outcome["status"] == ("happened" if completed == 6 else "missed")
    assert release["status"] == ("happened" if released == 6 else "missed")
    assert outcome["progress"] == {"done": completed, "total": 6, "released": released}
    if completed < 6:
        assert f"{completed} of 6 recorded" in outcome["explanation"]["text"]
        assert f"{released} of the 6 configured tasks were released" in outcome["explanation"]["text"]


def test_traffic_receipts_and_capture_failure_are_visible_without_expectations(tmp_path):
    run = journal_run(tmp_path / "traffic", [
        {"at_ns": 20_000_000_000, "items": [event("traffic.incident.activated", {"incident": "incident.01"}, 20_000_000_000)]},
        {"at_ns": 23_100_000_000, "items": [event("traffic.award.committed", {"incident": "incident.01", "actor": "uav.bravo"}, 23_100_000_000)]},
        {"at_ns": 57_100_000_000, "items": [event("traffic.capture.failed", {"actor": "uav.bravo", "reason": "renderer unavailable"}, 57_100_000_000)]},
    ])
    empty = run_story(run, [])
    assert empty["assessment"] == "unassessed"
    assert empty["coverage"]["recorded_events"] == 3
    assert [e["schema"] for e in empty["events"]] == ["traffic.incident.activated", "traffic.award.committed", "traffic.capture.failed"]
    assert empty["failures"][0]["payload"]["reason"] == "renderer unavailable"
    outcome = run_story(run, [expectation("traffic.capture.stored", {"actor": {"$in": ["uav.alpha", "uav.bravo"]}})])
    assert outcome["assessment"] == "incomplete"
    assert outcome["expectations"][0]["status"] == "missed"
    assert "renderer unavailable" in outcome["expectations"][0]["explanation"]["text"]


def test_story_caches_completed_runs_and_reads_only_new_commits(tmp_path, monkeypatch):
    run = journal_run(tmp_path / "incremental", [
        {"at_ns": 1, "items": []},
        {"at_ns": 2, "items": [event("traffic.incident.activated", {"incident": "incident.01"}, 2)]},
    ], status="running", committed_cut=1)
    storage = RunStorage(run)
    final = storage.entries[-1]
    starts = []
    original = RunStorage.records

    def read(self, start=0, limit=1000):
        starts.append(start)
        return original(self, start, limit)

    monkeypatch.setattr(RunStorage, "records", read)
    expected = [expectation("traffic.incident.activated", {"incident": "incident.01"})]
    assert run_story(run, expected)["expectations"][0]["status"] == "pending"
    assert starts == [0]
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    assert run_story(run, expected)["events"] == []
    assert starts == [0]
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before
    storage.publish_prefix(final["offset"] + final["length"], final["index"])
    assert run_story(run, expected)["expectations"][0]["status"] == "happened"
    assert starts == [0, 2]
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["status"] = "completed"
    (run / "manifest.json").write_text(json.dumps(manifest))
    assert run_story(run, expected)["terminal"]
    assert starts == [0, 2]
    first = storage.entries[1]
    storage.publish_prefix(first["offset"] + first["length"], first["index"])
    assert run_story(run, expected)["events"] == []


def test_form_version_story_derives_expectations_from_pinned_scenario(tmp_path):
    import copy
    from aeroagentsim.assistant.sessions import SessionStore
    from aeroagentsim.assistant.tools import ToolRunner
    from aeroagentsim.authoring.templates import TemplateRegistry
    from aeroagentsim.scenario import load_scenario

    prepared = TemplateRegistry().apply("traffic-accident")
    loaded = load_scenario(prepared.document, base=prepared.base)
    document = copy.deepcopy(loaded.document)
    document["registry"].pop("compile", None)
    document["registry"]["snapshot"] = "registry.snapshot.json"
    run_id = "run-" + "b" * 32
    directory = journal_run(tmp_path / run_id, [], document=document)
    loaded.compiled.write_snapshot(directory / "registry.snapshot.json")
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest.update(studio_workspace="fixture-workspace", workspace_version=2)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    sessions = SessionStore(tmp_path)
    session = sessions.create("fixture-workspace")
    tools = ToolRunner(None, TemplateRegistry(), sessions, tmp_path)
    story = tools.run_story(session["id"], run_id)
    assert story["expectations_source"] == "pinned_run_scenario"
    assert story["workspace_version"] == 2 and story["plan_version"] is None
    assert any("traffic-incident" in e["id"] for e in story["expectations"])


def test_model_story_compaction_preserves_coverage_and_failure(tmp_path):
    from aeroagentsim.assistant.tools import compact_json

    run = journal_run(tmp_path / "many", [{"at_ns": i, "items": [event("traffic.incident.activated", {"incident": "incident.01"}, i)]} for i in range(1, 40)] + [
        {"at_ns": 50, "items": [event("traffic.capture.failed", {"actor": "uav.bravo", "reason": "real failure"}, 50)]}])
    compact = json.loads(compact_json(run_story(run, [])))
    assert compact["coverage"]["recorded_events"] == 40
    assert compact["events_sampled"]
    assert compact["failure_count"] == 1
    assert compact["failures"][0]["payload"]["reason"] == "real failure"


def test_traffic_award_actor_receipt_matches_template_before_capture_timeout(tmp_path):
    from aeroagentsim.assistant.scene import expectations, summarize
    from aeroagentsim.authoring.templates import TemplateRegistry
    from aeroagentsim.scenario import load_scenario

    prepared = TemplateRegistry().apply("traffic-accident")
    loaded = load_scenario(prepared.document, base=prepared.base)
    expected = expectations(loaded.document, summarize(loaded.document, directory=prepared.base))
    award = next(row for row in expected if row["match"]["schema"] == "traffic.award.committed")
    capture = next(row for row in expected if row["match"]["schema"] == "traffic.capture.stored")
    run = journal_run(tmp_path / "traffic", [
        {"at_ns": 23_066_666_667, "items": [event("traffic.award.committed", {"actor": "uav.bravo"}, 23_066_666_667)]},
        {"at_ns": 57_066_666_667, "items": [event("traffic.capture.failed", {"actor": "uav.bravo", "incident": "incident.01", "reason": "viewer asset initialization deadline exceeded"}, 57_066_666_667)]},
    ])
    story = run_story(run, [award, capture])
    recorded, missed = story["expectations"]
    assert recorded["status"] == "happened" and recorded["at_ns"] == 23_066_666_667
    assert recorded["evidence"]["payload"] == {"actor": "uav.bravo"} and recorded["explanation"] is None
    assert missed["status"] == "missed" and "deadline exceeded" in missed["explanation"]["text"]
    assert story["coverage"]["schemas"]["traffic.award.committed"] == 1


def test_later_capture_failure_does_not_explain_an_unrecorded_award(tmp_path):
    run = journal_run(tmp_path / "traffic", [{"at_ns": 57_000_000_000, "items": [
        event("traffic.capture.failed", {"actor": "uav.bravo", "incident": "incident.01", "reason": "capture timeout"}, 57_000_000_000),
    ]}])
    missing = run_story(run, [expectation("traffic.award.committed", {"actor": "uav.bravo"})])["expectations"][0]
    assert missing["status"] == "missed"
    assert missing["explanation"] == {"text": "No recorded event.", "text_zh": "没有记录事件。", "evidence": []}


def test_story_tool_context_caps_large_failure_payloads_without_losing_counts():
    from aeroagentsim.assistant.tools import compact_json

    failures = [{"schema": "aas.behaviour.conflict", "text": "A recorded conflict", "at_ns": i,
                 "payload": {"reason": "busy", "predicate_context": "internal" * 1000}} for i in range(288)]
    failures.append({"schema": "traffic.capture.failed", "text": "Capture failed", "at_ns": 57_000_000_000,
                     "payload": {"reason": "{'status': 'timeout', 'reason': 'viewer asset initialization deadline exceeded'}", "child_receipt": "internal" * 1000}})
    story = {"run_id": "run-real", "status": "completed", "coverage": {"recorded_events": 9211},
             "expectations": [], "events": [], "failures": failures, "details_url": "/recorded-details"}
    raw = compact_json(story)
    result = json.loads(raw)
    assert len(raw) <= 6500 and "predicate_context" not in raw and "child_receipt" not in raw
    assert result["coverage"]["recorded_events"] == 9211 and result["failure_count"] == 289
    assert result["failures"][0]["count"] == 1 and result["failures"][1]["count"] == 288
    assert result["failures"][0]["reason"] == "viewer asset initialization deadline exceeded"
    assert result["details_url"] == "/recorded-details"
