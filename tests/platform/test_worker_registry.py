"""Single ownership, resource release and shared deadlines without an HTTP server."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel import BindingManifest, Kernel, MemoryRegistry

from aeroagentsim.services.lifecycle import (
    WorkerFailure,
    WorkerLimits,
    WorkerRegistry,
    bounded_input,
)
from aeroagentsim.services.storage import RunStorage


class Process:
    def __init__(self, context: Context, **kwargs: Any) -> None:
        self.context, self.name, self.args = context, kwargs["name"], kwargs["args"]
        self.pid: int | None = None
        self.exitcode: int | None = None
        self.running = False
        self.close_calls = self.terminate_calls = self.kill_calls = 0
        self.joins: list[float] = []

    def start(self) -> None:
        if self.context.fail_start:
            raise OSError("authored process-start failure")
        self.pid, self.running = len(self.context.processes), True

    def is_alive(self) -> bool:
        assert not self.close_calls, "polled an already released process handle"
        return self.running

    def join(self, timeout: float) -> None:
        assert timeout == 0, "registry queued a blocking per-run join"
        self.joins.append(timeout)

    def terminate(self) -> None:
        # Every run is signalled before escalation, even beyond executor capacity.
        assert all(process.args[3].is_set() and not process.args[2].is_set()
                   for process in self.context.processes)
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.running, self.exitcode = False, -9

    def close(self) -> None:
        assert not self.running
        self.close_calls += 1
        assert self.close_calls == 1


class Context:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.processes: list[Process] = []
        self.pipes: list[tuple[Any, Any]] = []
        self.fail_start = fail_start

    @staticmethod
    def Event() -> Any:
        raise AssertionError("registry created a kill-poisonable shared Event")

    @staticmethod
    def Value(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("registry created a kill-poisonable locked Value")

    @staticmethod
    def RawValue(kind: str, value: float | int) -> Any:
        return multiprocessing.RawValue(kind, value)

    def Pipe(self) -> tuple[Any, Any]:
        pair = multiprocessing.Pipe()
        self.pipes.append(pair)
        return pair

    def Process(self, **kwargs: Any) -> Process:
        process = Process(self, **kwargs)
        self.processes.append(process)
        return process


def prepared(path: Path) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({"id": path.name, "status": "created"}))
    (path / "index.json").write_text("[]")
    return path


def limits(**changes: float) -> WorkerLimits:
    return WorkerLimits(**{
        "startup_timeout_s": 0.1, "input_timeout_s": 0.05,
        "shutdown_grace_s": 0.01, "terminate_grace_s": 0.01,
        "kill_grace_s": 0.01, "cleanup_timeout_s": 0.05, **changes,
    })


async def released(registry: WorkerRegistry) -> None:
    async def wait() -> None:
        while registry.active_count or registry.task_count:
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 2)


def _exit_holding_shared_locks(
    scenario: Any, path: Path, paused: Any, stopped: Any, ingress: Any,
    started: Any, speed: Any, ready: Any, inherited: Any, cleanup: float,
) -> None:
    # Always strand a real Event lock. Also poison the actual lifecycle flags
    # and speed if a regression ever reintroduces locking implementations.
    for flag in (scenario.legacy_flag, paused, stopped, started, ready):
        condition = getattr(flag, "_cond", None)
        if condition is not None:
            condition.acquire()
        else:
            get_lock = getattr(getattr(flag, "_flag", flag), "get_lock", None)
            if get_lock is not None:
                get_lock().acquire()
    get_lock = getattr(speed, "get_lock", None)
    if get_lock is not None:
        get_lock().acquire()
    os._exit(17)  # Deliberately bypass semaphore release and normal finalizers.


def _host_after_lock_owner_exit(path: Path, access: str) -> None:
    async def check() -> None:
        context = multiprocessing.get_context("fork")
        legacy_flag = context.Event()
        registry = WorkerRegistry(
            path, limits(), context=context, target=_exit_holding_shared_locks,
        )
        worker = registry.start(
            SimpleNamespace(pacing="realtime", legacy_flag=legacy_flag),  # type: ignore[arg-type]
            prepared(path / "poisoned"), autostart=False,
        )
        # No event-loop yield until the child is dead: neither the monitor nor
        # a timing race can release it before the host's critical flag access.
        worker.process.join(2)
        assert worker.process.exitcode == 17
        assert not legacy_flag._cond.acquire(False), "child did not strand its flag lock"
        try:
            if access == "startup":
                with pytest.raises(WorkerFailure) as raised:
                    await registry.wait_started(worker)
                assert raised.value.detail["code"] == "WORKER_EXITED"
            elif access == "controls":
                assert not worker.started.is_set() and not worker.paused.is_set()
                worker.started.set()
                worker.paused.set()
                assert worker.started.is_set() and worker.paused.is_set()
                worker.paused.clear()
                worker.stopped.set()
                assert worker.stopped.is_set() and not worker.paused.is_set()
            elif access == "speed":
                assert worker.speed.value == 1.0
                worker.speed.value = 0.25
                assert worker.speed.value == 0.25
            else:
                assert access == "shutdown"
                await registry.close()
        finally:
            await registry.close()
        await released(registry)
        assert worker.released and worker.connection.closed
        assert RunStorage(worker.path).metadata()["host_lifecycle"]["exitcode"] == 17
    asyncio.run(check())


@pytest.mark.parametrize("access", ["startup", "controls", "speed", "shutdown"])
def test_child_exit_holding_flag_locks_cannot_block_host(tmp_path: Path, access: str) -> None:
    # An asyncio timeout cannot rescue an event loop blocked in Event.is_set().
    # Isolate the host too, so the regression fails deterministically, not hangs.
    context = multiprocessing.get_context("fork")
    host = context.Process(target=_host_after_lock_owner_exit, args=(tmp_path, access))
    host.start()
    try:
        host.join(5)
        assert not host.is_alive(), f"host {access} blocked on a dead child's shared lock"
        assert host.exitcode == 0
    finally:
        if host.is_alive():
            host.kill()
            host.join(2)
        host.close()


def test_worker_has_one_owner_and_completed_handles_are_released(tmp_path: Path) -> None:
    async def check() -> None:
        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        path = prepared(tmp_path / "one")
        source: Any = SimpleNamespace(pacing="fast")
        worker = registry.start(source, path)
        assert registry.get("one") is worker
        with pytest.raises(ValueError, match="already has an owner"):
            registry.start(source, path)
        assert len(context.processes) == 1
        assert context.pipes[0][1].closed  # Parent never retains the child endpoint.
        # The child is told to close its copy of this run's host endpoint.
        assert worker.connection in worker.process.args[8]
        RunStorage(path).status("completed")
        worker.process.running, worker.process.exitcode = False, 0
        await released(registry)
        assert registry.get("one") is None
        assert worker.released and worker.connection.closed
        assert worker.process.close_calls == 1 and worker.process.joins == [0]
        assert worker.projection is worker.monitor is worker.rpc_thread is None
        assert RunStorage(path).metadata()["status"] == "completed"
        with pytest.raises(ValueError, match="already has an owner"):
            registry.start(source, path)
        assert len(context.processes) == 1
        await registry.close()
        await registry.close()
        assert worker.process.close_calls == 1
        with pytest.raises(WorkerFailure, match="shutting down"):
            registry.start(source, prepared(tmp_path / "after-close"))
    asyncio.run(check())


def test_fork_child_closes_other_runs_host_endpoints(tmp_path: Path) -> None:
    async def check() -> None:
        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        source: Any = SimpleNamespace(pacing="fast")
        first = registry.start(source, prepared(tmp_path / "first"))
        second = registry.start(source, prepared(tmp_path / "second"))
        assert first.connection in second.process.args[8]
        await registry.close()
        await released(registry)
    asyncio.run(check())


def test_many_workers_share_each_shutdown_phase_and_release_all_handles(tmp_path: Path) -> None:
    async def check() -> None:
        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        source: Any = SimpleNamespace(pacing="fast")
        workers = [registry.start(source, prepared(tmp_path / f"run-{index}"))
                   for index in range(80)]
        for worker in workers:
            worker.paused.set()
        # This outer bound is a regression assertion, not a measured benchmark.
        await asyncio.wait_for(registry.close(), 2)
        await released(registry)
        for worker in workers:
            assert worker.process.terminate_calls == worker.process.kill_calls == 1
            assert worker.process.close_calls == 1 and worker.connection.closed
            metadata = RunStorage(worker.path).metadata()
            assert metadata["status"] == "interrupted" and metadata["final_cursor"] == 1
            assert metadata["host_lifecycle"]["termination_confirmed"] is True
            assert metadata["host_lifecycle"]["forced"] is True
            assert "shutdown deadline" in metadata["error"]
    asyncio.run(check())


def test_dead_worker_keeps_only_real_ack_and_does_not_scan_during_reaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        path = prepared(tmp_path / "prefix")
        kernel = Kernel()
        kernel.bind(MemoryRegistry(()), BindingManifest("registry", "0"), ())
        kernel.start()
        kernel.run_until(1)
        kernel.close()
        (path / "journal.jsonl").write_bytes(kernel.journal.bytes)
        storage = RunStorage(path)
        storage.index()
        acknowledged = storage.entries[-2]
        storage.publish_prefix(acknowledged["offset"] + acknowledged["length"], acknowledged["index"])
        before = {name: (path / name).read_bytes()
                  for name in ("journal.jsonl", "committed-prefix.json")}
        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        worker = registry.start(SimpleNamespace(pacing="fast"), path)  # type: ignore[arg-type]

        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("worker reaping scanned a journal")

        with monkeypatch.context() as patch:
            patch.setattr(RunStorage, "index", forbidden)
            worker.process.running, worker.process.exitcode = False, 17
            await released(registry)
        metadata = storage.metadata()
        assert metadata["status"] == "interrupted"
        assert metadata["host_lifecycle"]["exitcode"] == 17
        assert metadata["final_cursor"] == acknowledged["index"] + 1
        assert {name: (path / name).read_bytes() for name in before} == before
        reader = RunStorage(path)
        reader.refresh_committed()
        assert reader.entries[-1]["index"] == acknowledged["index"]
        await registry.close()
    asyncio.run(check())


def test_no_ack_never_promotes_an_indexed_visible_tail(tmp_path: Path) -> None:
    async def check() -> None:
        path = prepared(tmp_path / "no-ack")
        (path / "index.json").write_text('[{"index": 9, "offset": 0, "length": 1}]')
        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        worker = registry.start(SimpleNamespace(pacing="fast"), path)  # type: ignore[arg-type]
        worker.process.running, worker.process.exitcode = False, 1
        await released(registry)
        assert json.loads((path / "index.json").read_text()) == []
        assert RunStorage(path).metadata()["final_cursor"] == 1
        assert not (path / "committed-prefix.json").exists()
        await registry.close()
    asyncio.run(check())


def test_stalled_projection_is_cancelled_without_retaining_completed_tasks(tmp_path: Path) -> None:
    async def check() -> None:
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def warm(worker: Any) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        worker = registry.start(
            SimpleNamespace(pacing="fast"), prepared(tmp_path / "cache"), warm=warm  # type: ignore[arg-type]
        )
        await entered.wait()
        worker.process.running, worker.process.exitcode = False, 0
        await released(registry)
        assert cancelled.is_set() and worker.connection.closed
        assert worker.projection is worker.monitor is None
        await registry.close()
    asyncio.run(check())


def test_failed_process_start_releases_both_pipe_ends_and_owner(tmp_path: Path) -> None:
    async def check() -> None:
        context = Context(fail_start=True)
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        path = prepared(tmp_path / "start-failure")
        with pytest.raises(WorkerFailure) as raised:
            registry.start(SimpleNamespace(pacing="fast"), path)  # type: ignore[arg-type]
        assert raised.value.status_code == 503
        assert raised.value.detail["outcome"] == "not_submitted"
        assert registry.active_count == registry.task_count == 0
        assert all(connection.closed for connection in context.pipes[0])
        assert context.processes[0].close_calls == 1
        assert RunStorage(path).metadata()["status"] == "interrupted"
        await registry.close()
    asyncio.run(check())


def test_partially_started_process_keeps_an_owner_and_reports_uncertainty(tmp_path: Path) -> None:
    async def check() -> None:
        context = Context()
        registry = WorkerRegistry(tmp_path, limits(), context=context)
        original = context.Process

        def partial(**kwargs: Any) -> Process:
            process = original(**kwargs)

            def start() -> None:
                process.pid, process.running = 1, True
                raise OSError("authored error after child creation")

            process.start = start
            return process

        context.Process = partial  # type: ignore[method-assign]
        with pytest.raises(WorkerFailure) as raised:
            registry.start(SimpleNamespace(pacing="fast"), prepared(tmp_path / "partial"))  # type: ignore[arg-type]
        assert raised.value.detail["outcome"] == "uncertain"
        assert registry.active_count == 1
        await registry.close()
        await released(registry)
        assert context.processes[0].close_calls == 1
        assert all(connection.closed for connection in context.pipes[0])
    asyncio.run(check())


def test_expired_adapter_deadline_is_refused_before_executor_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(deadline: float) -> dict[str, Any]:
        raise AssertionError("already expired input reached its callback")

    def forbidden_dispatch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("already expired input reached the executor")

    monkeypatch.setattr(asyncio, "to_thread", forbidden_dispatch)

    async def check() -> None:
        with pytest.raises(WorkerFailure) as raised:
            await bounded_input("expired", forbidden, timeout_s=1.0, deadline=-1.0)
        assert raised.value.status_code == 504
        assert raised.value.detail["outcome"] == "not_submitted"
        assert raised.value.detail["retryable"] is False
    asyncio.run(check())


def test_expired_dispatch_is_never_submitted_to_a_worker(tmp_path: Path) -> None:
    registry = WorkerRegistry(tmp_path, limits(), context=Context())
    with pytest.raises(WorkerFailure) as raised:
        registry.input_call("not-dispatched", "ingress", {}, deadline=-1.0)
    assert raised.value.status_code == 504
    assert raised.value.detail["outcome"] == "not_submitted"
    assert registry.active_count == registry.task_count == 0


def test_observed_fault_and_terminal_waiting_survive_forced_cleanup(tmp_path: Path) -> None:
    async def check() -> None:
        registry = WorkerRegistry(tmp_path, limits(), context=Context())
        fault = prepared(tmp_path / "fault")
        (fault / "manifest.json").write_text(json.dumps({
            "id": "fault", "status": "running",
            "execution_outcome": {"status": "faulted", "error": "actual engine fault"},
        }))
        timed = prepared(tmp_path / "timed")
        waiting = {"stream_ids": ["sensor"], "at_ns": "10"}
        RunStorage(timed).status("input_timeout", error="actual watermark timeout", waiting=waiting)
        for path in (fault, timed):
            registry.start(SimpleNamespace(pacing="fast"), path)  # type: ignore[arg-type]
        await registry.close()
        await released(registry)
        assert RunStorage(fault).metadata()["status"] == "faulted"
        assert RunStorage(fault).metadata()["error"] == "actual engine fault"
        metadata = RunStorage(timed).metadata()
        assert metadata["status"] == "input_timeout" and metadata["waiting"] == waiting
        assert metadata["error"] == "actual watermark timeout"
    asyncio.run(check())


def test_unconfirmed_termination_keeps_status_unknown_and_retains_reaper(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    async def check() -> None:
        registry = WorkerRegistry(tmp_path, limits(), context=Context())
        path = prepared(tmp_path / "survivor")
        worker = registry.start(SimpleNamespace(pacing="fast"), path)  # type: ignore[arg-type]
        worker.process.kill = lambda: None  # Authored unkillable-OS-task counterexample.
        await asyncio.wait_for(registry.close(), 2)
        assert registry.get("survivor") is worker and not worker.released
        assert RunStorage(path).metadata()["status"] == "created"
        assert "host_lifecycle" not in RunStorage(path).metadata()
        assert "termination is not confirmed" in caplog.text
        # A continuing embedding loop still reaps the child if it later exits.
        worker.process.running, worker.process.exitcode = False, -9
        await released(registry)
        assert worker.connection.closed and worker.process.close_calls == 1
        await registry.close()
    asyncio.run(check())


@pytest.mark.parametrize("name", list(vars(WorkerLimits())))
@pytest.mark.parametrize("value", [None, True, 0, -1, float("nan"), float("inf"), 1e100, 10**1000, "1"])
def test_operator_limits_reject_unbounded_or_coerced_values(name: str, value: Any) -> None:
    with pytest.raises(ValueError, match="positive finite seconds"):
        WorkerLimits(**{name: value})
