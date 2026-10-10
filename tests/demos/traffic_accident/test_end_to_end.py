"""The shipped full scene: real owner receipts, PNG bytes and engine-free replay."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from aerokernel import CommandRequest, Instant, Stamp
from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records, replay
from aerokernel.state import Fact
from aerokernel.values import thaw
from fastapi.testclient import TestClient

from aeroagentsim.behaviours.evaluation import Evaluator
from aeroagentsim.observations.artifacts import ArtifactStore
from aeroagentsim.observations.renderer import BrowserRenderer
from aeroagentsim.packs.traffic_accident.decisions import Decisions
from aeroagentsim.platform import RunSession
from aeroagentsim.platform.plugins import EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.app import create_app
from aeroagentsim.services.storage import RunStorage
from tests.demos.conftest import SCENARIO, document

# Full-scene runs plus engine-free replay take minutes; marked slow so fast
# suites can deselect them, while the final gate still collects this module.
pytestmark = pytest.mark.slow


def trace(path: Path) -> list[tuple[int, str, dict[str, Any]]]:
    result = []
    # Stream codec expansion, then expand compact fact rows for diagnostics.
    for raw in iter_records(path / "journal.jsonl"):
        record = expand_record(raw)
        for item in record.get("items", []):
            if "message" in item and "proposal" in item:
                message = decode_record(item["message"])
                if message.kind == "event" and (
                    message.schema_id.startswith("traffic.")
                    or message.schema_id.startswith("aas.behaviour.")
                ):
                    result.append(
                        (
                            message.at.ns,
                            message.schema_id,
                            cast(dict[str, Any], thaw(message.payload)),
                        )
                    )
    return result


def assert_chain(
    path: Path, *, source: str, operator_activation_ns: int = 3_133_333_334
) -> None:
    rows = trace(path)
    events: dict[str, list[dict[str, Any]]] = {}
    for _, schema, payload in rows:
        events.setdefault(schema, []).append(payload)
    expected = (
        "traffic.incident.activated",
        "traffic.incident.detected",
        "traffic.report.sent",
        "traffic.broadcast",
        "traffic.proposal.bid",
        "traffic.award.proposed",
        "traffic.award.committed",
        "traffic.capture.requested",
        "traffic.capture.stored",
        "traffic.capture.upload_verified",
        "traffic.capture.accepted",
    )
    times = [min(ns for ns, key, _ in rows if key == schema) for schema in expected]
    assert times == sorted(times)
    assert times[0] == (8_000_000_000 if source == "timer" else operator_activation_ns)
    if source == "timer":
        # Pin the original entered-edge timeline: the level trigger must not
        # change the scheduled award, real capture, or verified upload times.
        assert times[5] == 13_700_000_001
        assert times[6] == 15_066_666_667
        assert times[7] == times[8] == 49_066_666_667
        assert times[9] == times[10] == 49_266_666_667
    bids = events["traffic.proposal.bid"]
    assert {p["actor"]["$ref"]["id"]: p["accept"] for p in bids} == {
        "uav.alpha": False,
        "uav.bravo": True,
    }
    assert {p["actor"]["$ref"]["id"] for p in events["traffic.award.proposed"]} == {
        "uav.bravo"
    }
    transitions = [
        p
        for _, key, p in rows
        if key == "aas.behaviour.transitioned"
        and p["op"] == "assert"
        and p["templateId"] == "traffic.capture_execute"
        and p["transitionId"] is not None
    ]
    assert [p["transitionId"] for p in transitions] == [
        "goto",
        "hold",
        "dwell",
        "capture",
        "stored",
        "accepted",
    ]
    dwell = next(
        ns
        for ns, key, p in rows
        if key == "aas.behaviour.transitioned"
        and p["op"] == "assert"
        and p["templateId"] == "traffic.capture_execute"
        and p["transitionId"] == "dwell"
    )
    first_arrival = min(
        ns
        for ns, key, p in rows
        if key == "aas.behaviour.predicate_evaluated"
        and p["predicateId"] == "traffic.p.arrived"
        and p["roles"]["pose"]["$ref"]["id"] == "uav.bravo"
        and p["status"] == "known"
        and p["value"] is True
        and p["op"] == "assert"
    )
    assert first_arrival <= dwell <= times[7]
    assert times[7] - first_arrival >= 3_000_000_000
    completion = [
        p
        for p in events["aas.behaviour.completed"]
        if p["op"] == "assert" and p["templateId"] == "traffic.capture_execute"
    ]
    assert len(completion) == 1 and completion[0]["state"] == "completed"
    assert {c["status"] for c in completion[0]["children"].values()} == {"succeeded"}
    record = ArtifactStore(path).get("incident-capture-01/episode-0")
    png = ArtifactStore(path).read(record["digest"])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert record["renderer_mode"] == "browser"
    assert record["request"]["actor"]["id"] == "uav.bravo"
    assert (
        record["request"]["source_cut"]
        == record["request"]["camera"]["snapshot"]["cut"]
    )
    poses = {
        entity["id"]: entity["position"]
        for entity in record["request"]["camera"]["snapshot"]["entities"]
    }
    initial = {
        entity["id"]: entity["facts"]
        for entity in json.loads((path / "scenario.json").read_text())["entities"]
    }
    assert poses["uav.alpha"] != initial["uav.alpha"]["he.aircraft.position_enu_m"]
    assert (
        poses["vehicle.reporter"]
        != initial["vehicle.reporter"]["traffic.road.position_enu_m"]
    )
    assert poses["uav.bravo"] == pytest.approx(completion[0]["variables"]["target"])
    assert poses["uav.bravo"][2] == 70.0
    # The target is localized at accident activation; the physical owner may
    # advance before its next stop boundary. Verify the stopped car is in view.
    footprint = poses["uav.bravo"][2] * math.tan(
        math.radians(record["request"]["camera"]["fov"] / 2)
    )
    assert (
        math.dist(poses["uav.bravo"][:2], poses["vehicle.incident.a"][:2]) < footprint
    )
    accepted = events["traffic.capture.accepted"][0]
    assert accepted["asset_id"] == record["digest"]
    assert "png_sha256" not in accepted
    assert RunStorage(path).metadata()["status"] == "completed"


def test_shipped_cli_chain_and_zero_call_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aeroagentsim.services.cli",
            "run",
            str(SCENARIO / "scenario.yaml"),
            "--out",
            str(tmp_path / "runs"),
        ],
        capture_output=True,
        text=True,
        timeout=2100,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    path = Path(json.loads(result.stdout)["run"])
    assert_chain(path, source="timer")
    assert_replay(path, monkeypatch)


def assert_replay(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The header is separate; compare every subsequent physical/logical record."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("replay called a live evaluator, decision or renderer")

    monkeypatch.setattr(EngineCatalog, "build", forbidden)
    monkeypatch.setattr(Evaluator, "run", forbidden)
    monkeypatch.setattr(Decisions, "on_inputs", forbidden)
    monkeypatch.setattr(BrowserRenderer, "render", forbidden)
    restored = replay(path / "journal.jsonl")
    # Kernel replay already checks canonical encodings against its reconstructed
    # commits. Compare every expanded record, including all physical facts/causes,
    # without repeating those costly canonical serialization passes here. The
    # header is the first yielded record; the rest stream from iter_records.
    stream = iter_records(path / "journal.jsonl")
    assert next(stream) == restored.header
    for record, replayed in zip(stream, restored.iter_records(), strict=True):
        assert expand_record(record) == replayed
    assert not restored.incomplete


def test_live_http_accident_runs_full_chain(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO / "scenario.yaml")
    doc = scenario.document
    doc["ingress_streams"][0]["initial_watermark_ns"] = 0
    doc["ingress_streams"][0]["timeout_s"] = 120
    incident = next(
        ref for ref in scenario.manifest.entities if ref.id == "incident.01"
    )
    with TestClient(
        create_app(tmp_path / "runs", scenario_root=SCENARIO),
        base_url="http://localhost",
    ) as client:
        created = client.post("/v1/runs", json=doc)
        assert created.status_code == 201, created.text
        identity = created.json()["id"]
        route = f"/v1/runs/{identity}"
        injected = client.post(
            route + "/ingress",
            json={
                "schema": "aas.runtime.inject_event",
                "engine": "behaviour",
                "target": "behaviour",
                "at_ns": 3_000_000_000,
                "stream_id": "operator",
                "source_stamp": {
                    "clock_id": "canonical",
                    "mapping_id": "canonical",
                    "numerator": 3_000_000_000,
                    "denominator": 1,
                },
                "payload": {
                    "injection_point": "accident",
                    "payload": {
                        "incident": {"$ref": incident.to_data()},
                        "reason": "operator injection while worker is running",
                    },
                },
            },
        )
        assert injected.status_code == 200, injected.text
        assert injected.json()["disposition"] == "accepted"
        closed = client.post(
            route + "/watermark",
            json={"stream_id": "operator", "watermark_ns": 90_000_000_000},
        )
        assert closed.status_code == 200, closed.text
        deadline = time.monotonic() + 2100
        while time.monotonic() < deadline:
            metadata = next(
                run for run in client.get("/v1/runs").json() if run["id"] == identity
            )
            if metadata["status"] in {"completed", "faulted", "interrupted"}:
                break
            time.sleep(0.5)
        assert metadata["status"] == "completed", metadata
    assert_chain(tmp_path / "runs" / identity, source="operator")


def test_late_operator_accident_reports_when_detection_is_already_true(
    tmp_path: Path,
) -> None:
    doc = document()
    package = cast(
        dict[str, Any], yaml.safe_load((SCENARIO / "behaviours.yaml").read_text())
    )
    transitions = package["chains"]["traffic.incident_report"]["transitions"]
    schedule = next(t for t in transitions if t["id"] == "schedule")
    # Only defer the automatic accident; keep real motion, decisions, capture
    # and upload owners. The operator is now the sole activation source.
    schedule["actions"][0]["duration_ns"] = 120_000_000_000
    doc["behaviours"] = [package]
    doc["ingress_streams"][0]["initial_watermark_ns"] = 0
    scenario = load_scenario(doc, base=SCENARIO)
    refs = {ref.id: ref for ref in scenario.manifest.entities}
    path = tmp_path / "late-operator"
    with RunSession(scenario, path) as session:
        session.start()
        session.advance_watermark(29_000_000_000, stream_id="operator")
        view = session.run_until(29_000_000_000)

        def value(identity: str, field: str) -> Any:
            fact = view.field((refs[identity], field), view.instant)
            assert isinstance(fact, Fact)
            return thaw(fact.value)

        assert value("incident.01", "traffic.incident.active") is False
        # All physical detection conditions were satisfied before injection.
        for identity in ("vehicle.reporter", "vehicle.incident.a", "vehicle.incident.b"):
            assert value(identity, "traffic.road.speed_mps") == 0.0
        radius = package["predicates"]["traffic.p.detected"]["parameters"][
            "detect_radius_m"
        ]
        assert math.dist(
            value("vehicle.reporter", "traffic.road.position_enu_m"),
            value("vehicle.incident.a", "traffic.road.position_enu_m"),
        ) <= radius
        receipt = session.submit_live(
            "behaviour",
            CommandRequest(
                "aas.runtime.inject_event",
                "behaviour",
                Instant(30_000_000_000),
                {
                    "injection_point": "accident",
                    "payload": {
                        "incident": {"$ref": refs["incident.01"].to_data()},
                        "reason": "late operator regression",
                    },
                },
            ),
            Stamp("canonical", 30_000_000_000, 1, "canonical"),
            stream_id="operator",
        )
        assert receipt.disposition == "accepted"
        session.advance_watermark(scenario.until_ns, stream_id="operator")
        session.run()

    rows = trace(path)
    report_transitions = [
        payload
        for _, schema, payload in rows
        if schema == "aas.behaviour.transitioned"
        and payload["templateId"] == "traffic.incident_report"
        and payload["op"] == "assert"
        and payload["transitionId"] is not None
    ]
    assert [p["transitionId"] for p in report_transitions] == [
        "schedule",
        "inject",
        "detect",
        "report",
    ]
    detect = report_transitions[2]
    assert detect["state"] == "reporting"
    evaluation = detect["trigger"]["evaluation"]
    known = [
        payload
        for _, schema, payload in rows
        if schema == "aas.behaviour.predicate_evaluated"
        and payload["contextId"] == evaluation["contextId"]
        and payload["op"] == "assert"
        and payload["status"] == "known"
    ]
    # The first committed evaluation at staging is already true, not a new
    # false-to-true edge. Requiring entered leaves this real chain staged.
    assert known[0]["value"] is True
    assert evaluation == known[0]
    assert_chain(path, source="operator", operator_activation_ns=30_133_333_334)
