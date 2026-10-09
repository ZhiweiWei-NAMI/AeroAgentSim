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
from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records, replay
from aerokernel.values import thaw
from fastapi.testclient import TestClient

from aeroagentsim.behaviours.evaluation import Evaluator
from aeroagentsim.observations.artifacts import ArtifactStore
from aeroagentsim.observations.renderer import BrowserRenderer
from aeroagentsim.packs.traffic_accident.decisions import Decisions
from aeroagentsim.platform.plugins import EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.app import create_app
from aeroagentsim.services.storage import RunStorage
from tests.demos.conftest import SCENARIO

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


def assert_chain(path: Path, *, source: str) -> None:
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
    assert times[0] == (8_000_000_000 if source == "timer" else 3_133_333_334)
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
    assert events["traffic.capture.accepted"][0]["png_sha256"] == record["digest"]
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
    with TestClient(create_app(tmp_path / "runs", scenario_root=SCENARIO)) as client:
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
