"""Refresh recovers real admission ids and outcomes without runtime writes."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Instant, Stamp
from aerokernel.sdk import EngineContext
from fastapi.testclient import TestClient

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.engines.telemetry import Telemetry
from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.admissions import admissions
from aeroagentsim.services.app import create_app


@pytest.fixture
def realtime() -> dict[str, Any]:
    authored = load_scenario(Path("scenarios/realtime-ingress.yaml"))
    document = copy.deepcopy(authored.document)
    document["registry"]["snapshot"] = str(authored.base / document["registry"]["snapshot"])
    document["bindings"]["commands"] = []
    document["engines"]["sensor"]["ingress"]["initial_watermark_ns"] = 0
    return document


def request(key: str) -> CommandRequest:
    return CommandRequest("aas.telemetry.observe", "sensor", Instant(50_000_000), {"value": 24.0}, key)


def test_admitted_unexecuted_command_survives_terminal_refresh(
    realtime: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runs" / "run-admission"
    with RunSession(load_scenario(realtime), path) as session:
        session.start()
        stamp = Stamp("sensor-clock", 50_000_000, 1, "sensor-ns")
        receipt = session.submit_live("sensor", request("once"), stamp)
        assert session.submit_live("sensor", request("once"), stamp) == receipt
        # A published but unacknowledged record is excluded from live reads.
        previous = session.storage.entries[-2]
        session.storage.publish_prefix(previous["offset"] + previous["length"], previous["index"])
        assert admissions(path) == []
        session.storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
        rows = admissions(path)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == receipt.command_id and row["status"] == "admitted"
        assert row["submitted_at"]["ns"] == "0"
        assert type(row["submitted_at"]["microstep"]) is int
        assert row["target_cut"] is None
        assert row["boundary_ns"] == receipt.boundary_ns and row["activation_ns"] == receipt.activation_ns
        assert row["journal_index"] == receipt.journal_index
        assert row["kind"] == "aas.telemetry.observe"
    session.storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
    session.storage.status("stopped")
    rows[0]["detail"] = "Admitted; no terminal execution receipt observed / 已受理，未观察到执行终态回执"
    before = {name: (path / name).read_bytes() for name in ("journal.jsonl", "index.json", "manifest.json")}

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Admission read invoked the simulation")

    monkeypatch.setattr(Simulation, "start", forbidden)
    monkeypatch.setattr(Simulation, "run_until", forbidden)
    for _ in range(2):
        with TestClient(create_app(path.parent), base_url="http://localhost") as client:
            response = client.get(f"/v1/runs/{path.name}/admissions")
            assert response.status_code == 200
            assert response.json() == rows
            assert client.get("/v1/runs/missing/admissions").status_code == 404
    assert {name: (path / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("outcome", ["succeeded", "rejected", "failed"])
def test_execution_outcomes_join_the_same_admission(
    realtime: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    if outcome != "succeeded":
        descriptor = next(row for row in realtime["registry"]["messages"] if row["id"] == "aas.telemetry.observe")
        descriptor["result_schema"] = {"type": "record", "members": {"reason": {"type": "string"}},
                                       "required": ["reason"], "extra": False}
        def on_inputs(self: Telemetry, ctx: EngineContext) -> None:
            for delivery in ctx.inbox:
                command = ctx.remember(delivery, lambda value: value)
                if outcome == "rejected":
                    ctx.reject(command, {"reason": "authored test rejection"})
                else:
                    ctx.accept(command)
                    ctx.execute(command)
                    ctx.fail(command, {"reason": "authored test failure"})
        monkeypatch.setattr(Telemetry, "on_inputs", on_inputs)
    path = tmp_path / "run"
    with RunSession(load_scenario(realtime), path) as session:
        session.start()
        receipt = session.submit_live("sensor", request("observed"), Stamp("sensor-clock", 50_000_000, 1, "sensor-ns"))
        assert admissions(path)[0]["status"] == "admitted"
        session.advance_watermark(100_000_000)
        session.run_until(100_000_000)
        rows = admissions(path)
        assert len(rows) == 1 and rows[0]["id"] == receipt.command_id
        assert rows[0]["execution_status"] == outcome
        assert rows[0]["status"] == {"succeeded": "applied", "rejected": "rejected", "failed": "failed"}[outcome]
        assert rows[0]["target_cut"] > receipt.journal_index
        if outcome != "succeeded":
            assert json.loads(rows[0]["detail"])["reason"].startswith("authored test")


def test_late_rejection_has_stable_journal_identity(realtime: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "run"
    with RunSession(load_scenario(realtime), path) as session:
        session.start()
        session.advance_watermark(100_000_000)
        receipt = session.submit_live("sensor", request("late"), Stamp("sensor-clock", 50_000_000, 1, "sensor-ns"))
        assert receipt.command_id is None
        assert session.submit_live("sensor", request("late"), Stamp("sensor-clock", 50_000_000, 1, "sensor-ns")) == receipt
        rows = admissions(path)
        assert len(rows) == 1 and rows == admissions(path)
        assert rows[0]["status"] == "rejected" and rows[0]["detail"] == "LATE_INGRESS"
        assert rows[0]["target_cut"] is None and rows[0]["command_id"] is None
        assert rows[0]["idempotency_key"] == "late"


def test_food_injection_is_recovered_by_its_real_command_id(tmp_path: Path) -> None:
    prepared = TemplateRegistry().apply("food-delivery", {"run_mode": "interactive"})
    path = tmp_path / "food"
    with RunSession(load_scenario(prepared.document, base=prepared.base), path) as session:
        session.start()
        command = CommandRequest("aas.runtime.inject_event", "behaviour", Instant(1),
                                 {"injection_point": "restaurant_delay", "payload": {"restaurant": "restaurant-east", "delay_s": 15.0}}, "delay-once")
        receipt = session.submit_live("behaviour", command, Stamp("canonical", 1, 1, "canonical"), stream_id="operator")
        assert admissions(path)[0]["id"] == receipt.command_id
        session.advance_watermark(1_000_000_000, stream_id="operator")
        session.run_until(1_000_000_000)
        row = next(row for row in admissions(path) if row["id"] == receipt.command_id)
        assert row["kind"] == "aas.runtime.inject_event" and row["status"] == "applied"
        assert row["stream_id"] == "operator" and row["execution_status"] == "succeeded"
