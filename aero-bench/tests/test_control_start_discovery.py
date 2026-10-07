"""Forward catalog recovery: the authenticated catalog carries the non-secret
Start idempotency identity for exactly the runs this process still manages.

``CatalogRun.start_id`` is null until the run is created through
``POST /v1/runs``; after creation it equals the start_id that created the run.
It never carries credentials, and starting again with the same start_id replays
the cached response without a second execution thread.
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from aero_bench.control.contracts import StartRunRequest
from aero_bench.control.manager import ControlManagerError, ControlRunManager
from aero_bench.executor import Executor
from tests.support import build_bundle, resolve_bundle

_SUITE_SHA256 = "e" * 64


class _RefusingExecutor(Executor):
    """Executor whose plans are rejected: no workload ever runs in these tests."""

    def run(self, request: object) -> dict[str, object]:
        raise RuntimeError("executor must not be invoked in this test")


def _manager(output_root: Path) -> tuple[ControlRunManager, tuple]:
    bundle = build_bundle(output_root / "bundle")
    runs = resolve_bundle(bundle.suite, executor_kind="docker_reference")
    manager = ControlRunManager(
        runs=runs,
        suite_sha256=_SUITE_SHA256,
        bundle_root=bundle.suite.parent,
        output_root=output_root / "out",
        executor=_RefusingExecutor(),
        runtime_timeout_seconds=60,
        verifier_timeout_seconds=60,
    )
    return manager, runs


def _start_request(run_id: str, start_id: str) -> StartRunRequest:
    return StartRunRequest(
        schema_version="aero-bench.start-run-request/v1",
        start_id=start_id,
        run_id=run_id,
    )


def test_catalog_start_id_is_null_until_the_run_is_created() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        try:
            assert all(run.start_id is None for run in manager.catalog.runs)
            response = manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
            assert response.run_id == runs[0].run_id
            served = {run.run_id: run.start_id for run in manager.catalog.runs}
            assert served[runs[0].run_id] == "start.forward-reconnect"
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_catalog_start_id_is_scoped_to_the_created_run_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        try:
            manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
            served = {run.run_id: run.start_id for run in manager.catalog.runs}
            assert served[runs[0].run_id] == "start.forward-reconnect"
            # Every other catalog run of this compilation stays null: the
            # identity is scoped to the exact created run, never broadcast.
            assert all(
                start_id is None
                for run_id, start_id in served.items()
                if run_id != runs[0].run_id
            )
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_catalog_serializes_no_credentials() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        try:
            response = manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
            operator_token = response.credentials.operator_token
            csrf_token = response.credentials.csrf_token
            assert operator_token and csrf_token and operator_token != csrf_token
            dump = json.dumps(manager.catalog.model_dump(mode="json"))
            assert operator_token not in dump
            assert csrf_token not in dump
            assert "operator_token" not in dump
            assert "csrf_token" not in dump
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_start_with_the_same_start_id_replays_without_a_new_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        try:
            first = manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
            managed = manager._managed[runs[0].run_id]
            first_thread = managed.thread
            assert first_thread is not None and first_thread.is_alive()

            # Same start_id + run_id through the same POST /v1/runs path: the
            # manager replays the cached response instead of a new execution.
            replay = manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
            assert replay.run_id == first.run_id
            assert replay.credentials.operator_token == first.credentials.operator_token
            assert replay.credentials.csrf_token == first.credentials.csrf_token
            assert manager._managed[runs[0].run_id] is managed
            assert managed.thread is first_thread
            assert len(manager._starts) == 1
            served = {run.run_id: run.start_id for run in manager.catalog.runs}
            assert served[runs[0].run_id] == "start.forward-reconnect"
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_start_with_an_unknown_run_id_fails_explicitly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, _runs = _manager(Path(tmp))
        try:
            with pytest.raises(ControlManagerError, match="configured catalog") as caught:
                manager.start(_start_request("f" * 64, "start.forward-reconnect"))
            assert caught.value.code == "catalog.run_unknown"
            assert manager._managed == {}
            assert all(run.start_id is None for run in manager.catalog.runs)
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_start_id_reuse_for_another_run_fails_explicitly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        try:
            manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
            with pytest.raises(ControlManagerError, match="already used") as caught:
                manager.start(_start_request(runs[1].run_id, "start.forward-reconnect"))
            assert caught.value.code == "start.id_conflict"
            # The refused start never became the other run's identity.
            served = {run.run_id: run.start_id for run in manager.catalog.runs}
            assert served[runs[1].run_id] is None
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_malformed_start_id_is_refused_by_the_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        try:
            with pytest.raises(ValidationError):
                _start_request(runs[0].run_id, "start.bad identifier!")
            assert manager._managed == {}
        finally:
            manager.shutdown(join_timeout_seconds=2)


def test_shutdown_terminates_the_run_started_for_discovery() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager, runs = _manager(Path(tmp))
        manager.start(_start_request(runs[0].run_id, "start.forward-reconnect"))
        thread = manager._managed[runs[0].run_id].thread
        assert thread is not None
        served = {run.run_id: run.start_id for run in manager.catalog.runs}
        assert served[runs[0].run_id] == "start.forward-reconnect"
        manager.shutdown(join_timeout_seconds=5)
        assert not thread.is_alive()
