"""Ordinary platform live admission, explicit sealing and engine-free replay."""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aerokernel import CommandRequest, IngressReceipt, Instant, Stamp
from aerokernel.compact import expand_record
from aerokernel.errors import KernelError
from aerokernel.journal import iter_records, replay
from fastapi.testclient import TestClient

from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.services.app import create_app


@pytest.fixture
def realtime() -> dict[str, Any]:
    scenario = load_scenario(Path("scenarios/realtime-ingress.yaml"))
    document = copy.deepcopy(scenario.document)
    document["registry"]["snapshot"] = str(
        scenario.base / document["registry"]["snapshot"]
    )
    document["bindings"]["commands"] = []
    document["engines"]["sensor"]["ingress"]["initial_watermark_ns"] = 0
    return document


def command(value: float = 24.0, key: str | None = None) -> CommandRequest:
    return CommandRequest(
        "aas.telemetry.observe", "sensor", Instant(50_000_000), {"value": value}, key
    )


def stamp(ns: int = 50_000_000) -> Stamp:
    return Stamp("sensor-clock", ns, 1, "sensor-ns")


def assert_replay(session: RunSession, directory: Path) -> None:
    before = session.simulation.kernel.records
    with (
        patch(
            "aeroagentsim.platform.plugins.EngineCatalog.build",
            side_effect=AssertionError("live factory called"),
        ),
        patch(
            "aeroagentsim.engines.telemetry.Telemetry.on_inputs",
            side_effect=AssertionError("engine invoked"),
        ),
        patch("time.monotonic", side_effect=AssertionError("live clock called")),
    ):
        restored = replay(directory / "journal.jsonl")
    assert restored.records == before
    assert restored.view().cut == session.simulation.kernel.view().cut
    ref = session.scenario.manifest.entities[0]
    assert restored.view().field(
        (ref, "aas.telemetry.value"), Instant(session.now_ns)
    ) == session.simulation.kernel.view().field(
        (ref, "aas.telemetry.value"), Instant(session.now_ns)
    )
    assert not restored.incomplete


@pytest.mark.parametrize("lateness", ["reject", "delay"])
def test_live_policy_receipt_seal_and_offline_replay(
    realtime: dict[str, Any], tmp_path: Path, lateness: str
) -> None:
    realtime["engines"]["sensor"]["ingress"]["lateness"] = lateness
    directory = tmp_path / "run"
    with RunSession(load_scenario(realtime), directory) as session:
        session.start()
        receipt = session.submit_live("sensor", command(key="first"), stamp())
        assert isinstance(receipt, IngressReceipt)
        assert receipt.stream_id == "sensor"
        assert receipt.disposition == "accepted"
        assert receipt.boundary_ns == 50_000_000
        assert receipt.delay_ns == 0
        assert session.submit_live("sensor", command(key="first"), stamp()) == receipt
        assert session.simulation.kernel.records[-1]["type"] == "live_ingress"
        with pytest.raises(KernelError, match="IDEMPOTENCY_CONFLICT"):
            session.submit_live("sensor", command(99.0, "first"), stamp())
        session.advance_watermark(100_000_000)
        session.run_until(100_000_000)
        assert receipt.command_id is not None
        assert (
            session.simulation.kernel.action(receipt.command_id).status == "succeeded"
        )
        late = session.submit_live("sensor", command(17.0, "late"), stamp())
        before_duplicate = session.simulation.kernel.records
        assert session.submit_live("sensor", command(17.0, "late"), stamp()) == late
        assert session.simulation.kernel.records == before_duplicate
        assert late.disposition == ("rejected" if lateness == "reject" else "delayed")
        if lateness == "reject":
            assert late.command_id is None and late.code == "LATE_INGRESS"
            assert late.boundary_ns is None and late.delay_ns is None
        else:
            assert late.boundary_ns == 100_000_001
            assert late.activation_ns == 100_000_001
            assert late.delay_ns == 50_000_001
        session.advance_watermark(300_000_000)
        session.run()
        assert_replay(session, directory)
        header = json.loads((directory / "journal.jsonl").read_text().splitlines()[0])
        assert (
            header["configuration"]["scenario"]["engines"]["sensor"]["ingress"][
                "lateness"
            ]
            == lateness
        )
        assert header["ingress_policy"] is None
        assert (header["major"], header["minor"], header["semantic_version"]) == (
            2,
            1,
            4,
        )
        assert header["provenance"] == "lean"
        assert header["ingress_streams"][0]["fields"]["id"] == "sensor"


def test_watermark_wait_accepts_concurrent_input(
    realtime: dict[str, Any], tmp_path: Path
) -> None:
    with RunSession(load_scenario(realtime), tmp_path / "run") as session:
        session.start()
        with ThreadPoolExecutor() as pool:
            advancing = pool.submit(session.run_until, 100_000_000)
            # Wait for the actual run-limit record, rather than guessing timing.
            deadline = time.monotonic() + 2
            while session.simulation.kernel.records[-1]["type"] != "run_limit":
                assert time.monotonic() < deadline
                time.sleep(0.005)
            assert not advancing.done()
            accepted = session.submit_live("sensor", command(), stamp())
            session.advance_watermark(100_000_000)
            assert advancing.result(timeout=2).instant.ns == 100_000_000
        assert accepted.disposition == "accepted"
        assert accepted.command_id is not None
        assert (
            session.simulation.kernel.action(accepted.command_id).status == "succeeded"
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("lateness", "unknown"),
        ("timeout_s", 0),
        ("initial_watermark_ns", None),
        ("mapping_id", "missing"),
    ],
)
def test_policy_validation_has_engine_path(
    realtime: dict[str, Any], key: str, value: Any
) -> None:
    realtime["engines"]["sensor"]["ingress"][key] = value
    with pytest.raises(ScenarioError, match=r"engines\.sensor\.ingress"):
        load_scenario(realtime)


def test_missing_and_conflicting_policies(realtime: dict[str, Any]) -> None:
    missing = copy.deepcopy(realtime)
    del missing["engines"]["sensor"]["ingress"]
    missing["run"]["pacing"] = "realtime"
    with pytest.raises(ScenarioError, match=r"engines\.sensor\.ingress"):
        Simulation(load_scenario(missing))
    realtime["engines"]["second"] = copy.deepcopy(realtime["engines"]["sensor"])
    realtime["engines"]["second"]["ingress"]["lateness"] = "delay"
    scenario = load_scenario(realtime)
    assert {stream.policy.lateness for stream in scenario.ingress_streams} == {
        "reject",
        "delay",
    }
    realtime["engines"]["sensor"]["ingress"]["stream_id"] = "shared"
    realtime["engines"]["second"]["ingress"]["stream_id"] = "shared"
    with pytest.raises(ScenarioError, match=r"engines\.second\.ingress.*inconsistent"):
        load_scenario(realtime)


def test_source_mapping_and_engine_target(realtime: dict[str, Any]) -> None:
    simulation = Simulation(load_scenario(realtime))
    try:
        simulation.start()
        with pytest.raises(KernelError, match="CLOCK_MAPPING"):
            simulation.submit_live(
                "sensor", command(), Stamp("canonical", 10, 1, "canonical")
            )
        with pytest.raises(KernelError, match="CLOCK_MAPPING"):
            simulation.submit_live(
                "sensor", command(), Stamp("wrong-clock", 10, 1, "sensor-ns")
            )
        with pytest.raises(KernelError, match="COMMAND_ROUTE"):
            simulation.submit_live(
                "sensor",
                CommandRequest(
                    "aas.telemetry.observe", "other", Instant(10), {"value": 1}
                ),
                stamp(),
            )
        assert not any(
            r["type"].startswith("live_ingress") for r in simulation.kernel.records
        )
    finally:
        simulation.close()


def test_http_live_ingress_and_replay(realtime: dict[str, Any], tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs")) as client:
        created = client.post("/v1/runs", json=realtime)
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        url = f"/v1/runs/{run_id}"
        body = {
            "engine": "sensor",
            "schema": "aas.telemetry.observe",
            "target": "sensor",
            "at_ns": 50_000_000,
            "payload": {"value": 24.0},
            "source_stamp": {
                "clock_id": "sensor-clock",
                "numerator": 50_000_000,
                "denominator": 1,
                "mapping_id": "sensor-ns",
            },
            "idempotency_key": "http-first",
        }
        accepted = client.post(url + "/ingress", json=body)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["disposition"] == "accepted"
        assert client.post(url + "/ingress", json=body).json() == accepted.json()
        assert (
            client.post(
                url + "/watermark", json={"watermark_ns": 100_000_000}
            ).status_code
            == 200
        )
        late = client.post(
            url + "/ingress", json={**body, "idempotency_key": "http-late"}
        )
        assert late.status_code == 200, late.text
        assert late.json()["disposition"] == "rejected"
        assert late.json()["code"] == "LATE_INGRESS"
        bad = client.post(url + "/ingress", json={**body, "source_stamp": {}})
        assert bad.status_code == 422
        assert (
            client.post(url + "/watermark", json={"watermark_ns": 0}).status_code == 422
        )
        assert (
            client.post(
                url + "/watermark", json={"watermark_ns": 300_000_000}
            ).status_code
            == 200
        )
        deadline = time.monotonic() + 3
        while True:
            status = next(
                r for r in client.get("/v1/runs").json() if r["id"] == run_id
            )["status"]
            if status == "completed":
                break
            assert status != "faulted"
            assert time.monotonic() < deadline
            time.sleep(0.01)
        directory = tmp_path / "runs" / run_id
        records = [
            expand_record(record)
            for record in iter_records(directory / "journal.jsonl")
        ]
        with (
            patch(
                "aeroagentsim.platform.plugins.EngineCatalog.build",
                side_effect=AssertionError("live factory called"),
            ),
            patch("time.monotonic", side_effect=AssertionError("live clock called")),
        ):
            restored = replay(directory / "journal.jsonl")
        assert [r["index"] for r in restored.records] == [
            r["index"] for r in records[1:]
        ]
        assert [
            r
            for r in restored.records
            if r["type"]
            in {"live_ingress", "live_ingress_rejection", "watermark", "seal"}
        ] == [
            r
            for r in records
            if r["type"]
            in {"live_ingress", "live_ingress_rejection", "watermark", "seal"}
        ]
        assert not restored.incomplete
        assert restored.action(accepted.json()["command_id"]).status == "succeeded"
        assert client.post(url + "/ingress", json=body).status_code == 409


@pytest.mark.parametrize("pacing", ["fast", "realtime"])
def test_missing_watermark_faults_instead_of_pacing_fallback(
    realtime: dict[str, Any], tmp_path: Path, pacing: str
) -> None:
    realtime["run"]["pacing"] = pacing
    realtime["engines"]["sensor"]["ingress"]["timeout_s"] = 0.05
    directory = tmp_path / "run"
    session = RunSession(load_scenario(realtime), directory)
    session.start()
    with pytest.raises(KernelError, match="WATERMARK_TIMEOUT"):
        session.run_until(100_000_000)
    assert session.storage.metadata()["status"] == "faulted"
    assert session.closed
    assert replay(directory / "journal.jsonl").incomplete


def test_engines_have_distinct_watermarks_and_source_mappings(
    realtime: dict[str, Any], tmp_path: Path
) -> None:
    realtime["entities"].append(
        {"id": "sensor-2", "type": "aas:TelemetryStream", "facts": {}}
    )
    second = copy.deepcopy(realtime["engines"]["sensor"])
    second["config"]["entity"] = "sensor-2"
    second["ingress"]["mapping_id"] = "canonical"
    realtime["engines"]["sensor2"] = second
    for section in ("lifecycle", "rules"):
        original = realtime["bindings"][section][0]
        original["ids"] = "sensor-1"
        additional = copy.deepcopy(original)
        additional["ids"] = "sensor-2"
        additional["controller" if section == "lifecycle" else "writer"] = "sensor2"
        realtime["bindings"][section].append(additional)
    with RunSession(load_scenario(realtime), tmp_path / "run") as session:
        session.start()
        first = session.submit_live("sensor", command(), stamp())
        second_receipt = session.submit_live(
            "sensor2",
            CommandRequest(
                "aas.telemetry.observe", "sensor2", Instant(50_000_000), {"value": 25.0}
            ),
            Stamp("canonical", 50_000_000, 1, "canonical"),
        )
        assert first.disposition == second_receipt.disposition == "accepted"
        session.advance_watermark(300_000_000, stream_id="sensor")
        session.advance_watermark(300_000_000, stream_id="sensor2")
        session.run()
        assert first.command_id is not None and second_receipt.command_id is not None
        assert session.simulation.kernel.action(first.command_id).status == "succeeded"
        assert (
            session.simulation.kernel.action(second_receipt.command_id).status
            == "succeeded"
        )
        assert_replay(session, tmp_path / "run")


@pytest.mark.parametrize("allowance", [0, 5])
def test_mapped_source_progress_and_inclusive_lateness_tail(
    realtime: dict[str, Any], tmp_path: Path, allowance: int
) -> None:
    realtime["engines"]["sensor"]["ingress"]["allowed_lateness_ns"] = allowance
    realtime["clock_mappings"][1].update(offset_ns=7, p=2, q=3)
    with RunSession(load_scenario(realtime), tmp_path / "mapped") as session:
        session.start()
        kernel = session.simulation.kernel
        progress = Stamp("sensor-clock", 150, 1, "sensor-ns")  # P = 107ns
        closed = 107 - allowance
        session.advance_source_progress(progress)
        assert kernel.ingress_watermarks == {"sensor": closed}
        before = kernel.records
        session.advance_source_progress(progress)
        assert kernel.records == before
        with pytest.raises(KernelError, match="INGRESS_WATERMARK"):
            session.advance_source_progress(Stamp("sensor-clock", 147, 1, "sensor-ns"))
        assert kernel.records == before

        def attempt(ns: int) -> IngressReceipt:
            return session.submit_live(
                "sensor",
                CommandRequest(
                    "aas.telemetry.observe", "sensor", Instant(ns), {"value": 3.0}
                ),
                Stamp("sensor-clock", (ns - 7) * 3, 2, "sensor-ns"),
            )

        assert attempt(closed).disposition == "rejected"
        assert attempt(closed + 1).disposition == "accepted"
        if allowance:
            assert attempt(107).disposition == "accepted"
        session.advance_watermark(200)
        assert kernel.ingress_watermarks["sensor"] == 200
        before = kernel.records
        # Explicit closure is not weakened by a later mapped progress assertion.
        with pytest.raises(KernelError, match="INGRESS_WATERMARK"):
            session.advance_source_progress(progress)
        assert kernel.records == before
        with pytest.raises(KernelError, match="CLOCK_MAPPING"):
            session.advance_source_progress(Stamp("canonical", 300, 1, "canonical"))
        assert kernel.records == before
        session.run_until(200)
        restored = replay(kernel.journal.bytes)
        assert restored.records == kernel.records
        assert restored.ingress_watermarks == kernel.ingress_watermarks


@pytest.mark.parametrize("value", [None, True, False, -1, 1.5, "20"])
def test_shorthand_allowed_lateness_requires_authored_integer(
    realtime: dict[str, Any], value: Any
) -> None:
    realtime["engines"]["sensor"]["ingress"]["allowed_lateness_ns"] = value
    with pytest.raises(
        ScenarioError, match=r"engines\.sensor\.ingress\.allowed_lateness_ns"
    ):
        load_scenario(realtime)
