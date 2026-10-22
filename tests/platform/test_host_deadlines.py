"""Host deadlines are truthful even when initialization or pipe I/O cannot reply."""

from __future__ import annotations

import asyncio
import copy
import json
import multiprocessing
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.platform.simulation import RunSession, Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services import app as service
from aeroagentsim.services import worker as worker_module
from aeroagentsim.services.lifecycle import WorkerFailure, WorkerLimits, WorkerRegistry
from aeroagentsim.services.storage import RunStorage, committed_prefix


def limits(**changes: float) -> WorkerLimits:
    return WorkerLimits(**{
        "startup_timeout_s": 10.0, "input_timeout_s": 0.15,
        "shutdown_grace_s": 0.1, "terminate_grace_s": 0.1,
        "kill_grace_s": 0.1, "cleanup_timeout_s": 0.1, **changes,
    })


@pytest.fixture(autouse=True)
def local_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)


def test_hung_startup_returns_uncertainty_and_never_launches_again(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = tmp_path / "starts.jsonl"

    def hung(*args: Any) -> None:
        with calls.open("a") as stream:
            stream.write("started\n")
        threading.Event().wait(30)

    monkeypatch.setattr(service, "execute", hung)
    application = service.create_app(
        tmp_path / "runs", worker_limits=limits(startup_timeout_s=0.5)
    )
    with TestClient(application, base_url="http://localhost") as client:
        response = client.post("/v1/runs", json=document)
        assert response.status_code == 504, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "WORKER_STARTUP_TIMEOUT"
        assert detail["outcome"] == "uncertain" and detail["retryable"] is False
        assert "do not automatically" in detail["error"]
        run_id = detail["id"]
    assert calls.read_text().splitlines() == ["started"]
    metadata = RunStorage(tmp_path / "runs" / run_id).metadata()
    assert metadata["status"] == "interrupted" and metadata["final_cursor"] == 1
    assert metadata["host_lifecycle"]["termination_confirmed"] is True
    assert application.state.worker_registry.active_count == 0


def test_worker_exit_before_start_is_not_a_created_run_success(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "execute", lambda *args: os._exit(17))
    application = service.create_app(tmp_path / "runs", worker_limits=limits())
    with TestClient(application, base_url="http://localhost") as client:
        response = client.post("/v1/runs", json=document)
        assert response.status_code == 500, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "WORKER_EXITED"
        metadata = RunStorage(tmp_path / "runs" / detail["id"]).metadata()
        assert metadata["status"] == "interrupted"
        assert metadata["host_lifecycle"]["exitcode"] == 17
    assert application.state.worker_registry.active_count == 0


def test_real_initialization_fault_is_422_and_retains_its_run_identity(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid(*args: Any, **kwargs: Any) -> None:
        raise ValueError("authored factory configuration failure")

    monkeypatch.setattr(Simulation, "__init__", invalid)
    with TestClient(
        service.create_app(tmp_path / "runs", worker_limits=limits()), base_url="http://localhost"
    ) as client:
        response = client.post("/v1/runs", json=document)
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "WORKER_STARTUP_FAILED"
        assert "authored factory configuration failure" in detail["error"]
        assert RunStorage(tmp_path / "runs" / detail["id"]).metadata()["status"] == "faulted"


def test_launch_initializes_real_simulation_once_not_in_the_http_host(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = tmp_path / "initializations.jsonl"
    original = Simulation.__init__

    def observed(self: Simulation, *args: Any, **kwargs: Any) -> None:
        with calls.open("a") as stream:
            stream.write(str(os.getpid()) + "\n")
        original(self, *args, **kwargs)

    monkeypatch.setattr(Simulation, "__init__", observed)
    document["run"]["until_ns"] = 0
    with TestClient(
        service.create_app(tmp_path / "runs", worker_limits=limits()), base_url="http://localhost"
    ) as client:
        response = client.post("/v1/runs", json={"scenario": document, "autostart": False})
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "initialized"
        run_id = response.json()["id"]
        prefix = committed_prefix(tmp_path / "runs" / run_id)
        assert prefix is not None and prefix["index"] >= 1
        assert client.get(f"/v1/runs/{run_id}/header").status_code == 200
    assert len(calls.read_text().splitlines()) == 1
    assert int(calls.read_text()) != os.getpid()


def realtime_document() -> dict[str, Any]:
    scenario = load_scenario(Path("scenarios/realtime-ingress.yaml"))
    document = copy.deepcopy(scenario.document)
    document["registry"]["snapshot"] = str(scenario.base / document["registry"]["snapshot"])
    document["bindings"]["commands"] = []
    document["engines"]["sensor"]["ingress"]["initial_watermark_ns"] = 0
    document["engines"]["sensor"]["ingress"].pop("timeout_s", None)
    return document


def input_body() -> dict[str, Any]:
    return {
        "engine": "sensor", "schema": "aas.telemetry.observe", "target": "sensor",
        "at_ns": 50_000_000, "payload": {"value": 24.0}, "idempotency_key": "once",
        "source_stamp": {"clock_id": "sensor-clock", "mapping_id": "sensor-ns",
                         "numerator": 50_000_000, "denominator": 1},
    }


def test_admitted_input_timeout_is_uncertain_and_late_reply_cannot_leak_to_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("fork")
    admitted, release = context.Event(), context.Event()
    original = worker_module.receipt_data
    calls = tmp_path / "admissions.jsonl"

    def delayed(receipt: Any) -> dict[str, Any]:
        with calls.open("a") as stream:
            stream.write("admitted\n")
        admitted.set()  # submit_live has already returned; this is not a rejection.
        release.wait(30)
        return original(receipt)

    monkeypatch.setattr(worker_module, "receipt_data", delayed)
    with TestClient(
        service.create_app(tmp_path / "runs", worker_limits=limits(input_timeout_s=0.5)), base_url="http://localhost"
    ) as client:
        run = client.post("/v1/runs", json={"scenario": realtime_document(), "autostart": False})
        assert run.status_code == 201, run.text
        run_id = run.json()["id"]
        try:
            response = client.post(f"/v1/runs/{run_id}/ingress", json=input_body())
            assert admitted.is_set()
            assert response.status_code == 504, response.text
            detail = response.json()["detail"]
            assert detail["code"] == "WORKER_INPUT_TIMEOUT"
            assert detail["outcome"] == "uncertain" and detail["retryable"] is False
            # A second operation cannot consume the first call's future reply.
            queued = client.post(f"/v1/runs/{run_id}/watermark", json={"watermark_ns": 1})
            assert queued.status_code == 504, queued.text
            queued_detail = queued.json()["detail"]
            assert queued_detail["code"] in {"WORKER_INPUT_BUSY", "WORKER_INPUT_TIMEOUT"}
            if queued_detail["code"] == "WORKER_INPUT_BUSY":
                assert queued_detail["outcome"] == "not_submitted"
            else:
                # The outer host deadline can win the worker-thread diagnostic.
                assert queued_detail["outcome"] in {"not_submitted", "uncertain"}
            assert calls.read_text().splitlines() == ["admitted"]
        finally:
            release.set()
        progress = client.get(f"/v1/runs/{run_id}/inputs")
        assert progress.status_code == 200, progress.text
        assert progress.json()["contract"] == "aeroagentsim.input-progress/v1"
        assert "disposition" not in progress.json()
        assert progress.json()["watermarks"]["sensor"] == 0
        admissions = client.get(f"/v1/runs/{run_id}/admissions").json()
        assert len(admissions) == 1 and admissions[0]["idempotency_key"] == "once"
        assert calls.read_text().splitlines() == ["admitted"]


def test_exit_after_admission_is_not_reported_as_a_rejection_or_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module, "receipt_data", lambda receipt: os._exit(23))
    with TestClient(
        service.create_app(tmp_path / "runs", worker_limits=limits()), base_url="http://localhost"
    ) as client:
        run = client.post("/v1/runs", json={"scenario": realtime_document(), "autostart": False})
        assert run.status_code == 201, run.text
        run_id = run.json()["id"]
        response = client.post(f"/v1/runs/{run_id}/ingress", json=input_body())
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "WORKER_INPUT_CLOSED" and detail["outcome"] == "uncertain"
        assert detail["retryable"] is False
        assert len(list((tmp_path / "runs").glob("run-*"))) == 1
        progress = client.get(f"/v1/runs/{run_id}/inputs")
        assert progress.status_code == 409, progress.text


def transport_worker(
    scenario: Any, path: Path, paused: Any, stopped: Any, ingress: Any,
    started: Any, speed: Any, ready: Any, inherited: tuple[Any, ...], cleanup: float,
) -> None:
    for connection in inherited:
        connection.close()
    ready.set()
    if scenario.partial:
        ingress.recv()
        # poll() sees a frame header but recv() cannot finish this partial frame.
        os.write(ingress.fileno(), struct.pack("!i", 1024) + b"x")
    # Otherwise never read: a large host send blocks in the pipe transport.
    stopped.wait(30)
    ingress.close()


@pytest.mark.parametrize("partial", [False, True])
def test_deadline_covers_blocked_send_and_partial_reply_not_just_poll(
    tmp_path: Path, partial: bool,
) -> None:
    async def check() -> None:
        path = tmp_path / "transport"
        path.mkdir()
        (path / "manifest.json").write_text(json.dumps({"id": path.name, "status": "created"}))
        (path / "index.json").write_text("[]")
        registry = WorkerRegistry(tmp_path, limits(input_timeout_s=0.2), target=transport_worker)
        source: Any = SimpleNamespace(pacing="fast", partial=partial)
        worker = registry.start(source, path)
        try:
            await asyncio.wait_for(registry.wait_started(worker), 2)
            with pytest.raises(WorkerFailure) as raised:
                await asyncio.wait_for(
                    asyncio.to_thread(registry.input_call, path.name, "ingress", "x" * 2_000_000), 2
                )
            assert raised.value.status_code == 504
            assert raised.value.detail["outcome"] == "uncertain"
            with pytest.raises(WorkerFailure) as queued:
                await asyncio.wait_for(
                    asyncio.to_thread(registry.input_call, path.name, "progress", {}), 2
                )
            assert queued.value.detail["code"] == "WORKER_INPUT_BUSY"
            assert queued.value.detail["outcome"] == "not_submitted"
        finally:
            await asyncio.wait_for(registry.close(), 2)
        assert worker.released and worker.connection.closed
        assert registry.active_count == 0
    asyncio.run(check())


def test_executor_queue_delay_is_bounded_and_cannot_submit_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = service.create_app(tmp_path / "runs", worker_limits=limits(input_timeout_s=0.05))
    path = tmp_path / "runs" / "queued"
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({"id": "queued", "status": "initialized"}))
    calls = []
    entered, release = threading.Event(), threading.Event()

    def blocked() -> None:
        entered.set()
        release.wait(5)

    def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(args)
        raise AssertionError("queued expired input was dispatched after timeout")

    monkeypatch.setattr(application.state.worker_registry, "input_call", forbidden)
    endpoint = next(route.endpoint for route in application.routes
                    if getattr(route, "path", None) == "/v1/runs/{run_id}/ingress")

    async def check() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocker = loop.run_in_executor(None, blocked)
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            with pytest.raises(service.HTTPException) as raised:
                await asyncio.wait_for(endpoint("queued", {}), 1)
            assert raised.value.status_code == 504
            assert raised.value.detail["outcome"] == "uncertain"
            assert raised.value.detail["retryable"] is False
        finally:
            release.set()
            await blocker
        await asyncio.sleep(0)
        assert calls == []
    asyncio.run(check())


def test_http_compare_captures_once_per_row_and_never_uses_latest_kpis(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.runs import Run, open_run
    from aeroagentsim.services import feed_index
    from aeroagentsim.services.comparison import compare_runs_at_cuts
    from aeroagentsim.services.kpis import KPIProjector

    root = tmp_path / "runs"
    application = service.create_app(root)
    document["run"]["until_ns"] = 10_000_000
    source = load_scenario(document)
    with TestClient(application, base_url="http://localhost") as client, \
            RunSession(source, root / "a") as first, RunSession(source, root / "b") as second:
        first.start()
        second.start()
        for session in (first, second):
            session.storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
        with open_run(root / "a") as a, open_run(root / "b") as b:
            expected = compare_runs_at_cuts({"a": a.snapshot(), "b": b.snapshot()})
        captured: list[str] = []
        snapshot, consume = Run.snapshot, KPIProjector.consume
        advanced = False

        def capture(self: Run) -> Any:
            captured.append(self.directory.name)
            return snapshot(self)

        def advance(self: KPIProjector, record: dict[str, Any]) -> dict[str, Any]:
            nonlocal advanced
            if not advanced:
                advanced = True
                for session in (first, second):
                    session.run_until(1_000_000)
                    session.storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
            return consume(self, record)

        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("HTTP comparison projected a second/latest KPI cut")

        monkeypatch.setattr(Run, "snapshot", capture)
        monkeypatch.setattr(KPIProjector, "consume", advance)
        monkeypatch.setattr(feed_index, "kpi_result", forbidden)
        response = client.get("/v1/runs/compare?ids=a,b")
        assert response.status_code == 200, response.text
        result = response.json()
        assert captured == ["a", "b"] and advanced
        assert result["basis"] == "fixed_cuts" and result["alignment"] == "recorded_run_end"
        assert result["config_diff"] == expected["config_diff"]
        assert [metric["values"] for metric in result["kpis"]] == [
            metric["values"] for metric in expected["kpis"]
        ]
        for row, fixed in zip(result["runs"], expected["runs"]):
            assert row["cut"] == fixed["cut"] and row["cursor"] == row["cut"]["index"] + 1
            assert row["status"] == "running" and row["status_basis"] == "host_observation"
            end = result["recorded_ends"][row["id"]]
            assert end["commit_index"] == row["cut"]["index"]
            assert end["at_ns"] == expected["recorded_ends"][row["id"]]["at_ns"]
            assert end["status"] == "running" and end["status_basis"] == "host_observation"
            for metric in result["kpis"]:
                evidence = metric["evidence"][row["id"]]
                assert evidence["cut"] == row["cut"] and evidence["cursor"] == row["cursor"]
                assert evidence["status_basis"] == "host_observation"
        metadata = client.get("/v1/runs/compare?ids=a,b&include_kpis=false")
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["basis"] == "fixed_cuts"
        assert "kpis" not in metadata.json()
        assert all("kpis" not in row for row in metadata.json()["runs"])
