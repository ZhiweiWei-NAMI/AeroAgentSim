"""Exactly one host owner per worker, with bounded waits and shared shutdown phases."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import multiprocessing
import socket
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .storage import committed_prefix

if TYPE_CHECKING:
    from aeroagentsim.scenario import Scenario

TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
_POLL_SECONDS = 0.02
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerLimits:
    """Operator wall-clock budgets, not simulated-time or ingress-watermark limits."""

    startup_timeout_s: float = 60.0
    input_timeout_s: float = 10.0
    shutdown_grace_s: float = 10.0
    terminate_grace_s: float = 2.0
    kill_grace_s: float = 2.0
    cleanup_timeout_s: float = 1.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if (
                type(value) not in (int, float)
                or not 0 < value <= threading.TIMEOUT_MAX
                or not math.isfinite(value)
            ):
                raise ValueError(
                    f"{name}: positive finite seconds up to threading.TIMEOUT_MAX required"
                )


class WorkerFailure(Exception):
    """A host failure is not an admission rejection or an execution receipt."""

    def __init__(
        self,
        run_id: str,
        status_code: int,
        code: str,
        error: str,
        *,
        outcome: str = "not_submitted",
    ) -> None:
        super().__init__(error)
        self.status_code = status_code
        self.detail = {
            "id": run_id, "code": code, "error": error,
            "outcome": outcome, "retryable": False,
        }


class _SharedFlag:
    """Single-writer shared byte, mirroring experiments._Cancellation.

    The host alone writes paused/stopped/started; the worker alone writes ready.
    Readers never acquire a lock, wait for a writer or perform read-modify-write.
    In particular, killing a reader or the ready writer cannot poison a semaphore.
    These are independent signals, not a transactional snapshot or a receipt.
    """

    def __init__(self, flag: Any) -> None:
        self._flag = flag

    def set(self) -> None:
        self._flag.value = 1

    def clear(self) -> None:
        self._flag.value = 0

    def is_set(self) -> bool:
        return bool(self._flag.value)


async def bounded_input(
    run_id: str,
    call: Callable[[float], dict[str, Any]],
    *,
    timeout_s: float,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Bound executor dispatch and the entire exchange with one absolute deadline.

    The synchronous callback receives that same deadline for serialization and
    transport waits. Even if cancellation loses an executor-dispatch race, an
    expired queued callback must not begin work, let alone submit worker input.
    """
    now = time.monotonic()
    deadline = min(deadline, now + timeout_s) if deadline is not None else now + timeout_s
    if deadline <= now:
        raise WorkerFailure(
            run_id, 504, "WORKER_INPUT_TIMEOUT", "Input deadline expired before submission"
        )

    def dispatch() -> dict[str, Any]:
        if time.monotonic() >= deadline:
            raise WorkerFailure(
                run_id, 504, "WORKER_INPUT_TIMEOUT", "Input deadline expired before submission"
            )
        return call(deadline)

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(dispatch), timeout=max(0.0, deadline - time.monotonic())
        )
    except asyncio.TimeoutError as exc:
        # A running call may already have sent input; never assert rejection.
        raise WorkerFailure(
            run_id, 504, "WORKER_INPUT_TIMEOUT",
            "Host input deadline expired before its result was observed; inspect "
            "admissions, source progress and journal; do not automatically resubmit",
            outcome="uncertain",
        ) from exc


@dataclass(eq=False)
class WorkerHandle:
    run_id: str
    path: Path
    process: Any
    paused: Any
    stopped: Any
    started: Any
    speed: Any
    ready: Any
    connection: Any
    launched_at: float = field(default_factory=time.monotonic)
    exited: threading.Event = field(default_factory=threading.Event)
    input_lock: Any = field(default_factory=threading.Lock)
    channel_lock: Any = field(default_factory=threading.Lock)
    channel_busy: bool = False
    channel_closing: bool = False
    rpc_thread: threading.Thread | None = None
    projection: asyncio.Task[None] | None = None
    monitor: asyncio.Task[None] | None = None
    abort: asyncio.Task[None] | None = None
    forced: bool = False
    stop_reason: str | None = None
    released: bool = False

    @property
    def alive(self) -> bool:
        # Only the event-loop owner polls/closes process handles. RPC threads use
        # exited instead, so they cannot race multiprocessing.Process.close().
        return bool(not self.exited.is_set() and not self.released and self.process.is_alive())


class WorkerRegistry:
    """Own processes, both IPC endpoints during fork, RPCs and derived-cache tasks.

    A timed-out RPC keeps its serialization lock until its single exchange drains.
    No request is resent, and a late reply cannot become another request's receipt.
    Blocking pipe I/O uses a dedicated daemon thread, never the shared asyncio
    executor: even a partial frame/blocked send cannot strand a host request or
    executor shutdown. The dead worker's channel is shut down before release.
    """

    def __init__(
        self,
        root: Path,
        limits: WorkerLimits | None = None,
        *,
        context: Any = None,
        target: Callable[..., None] | None = None,
    ) -> None:
        self.root = root
        self.limits = limits or WorkerLimits()
        # Preserve the compiled immutable registry without pickling MappingProxyType.
        self.context = context or multiprocessing.get_context("fork")
        self.target = target
        self._workers: dict[str, WorkerHandle] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = threading.Lock()
        self._closing = False
        self._controlling = False
        self._close_lock = asyncio.Lock()

    def get(self, run_id: str) -> WorkerHandle | None:
        with self._lock:
            return self._workers.get(run_id)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._workers)

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    def _task(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(work)
        self._tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    _LOG.error(
                        "Worker lifecycle task failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )

        task.add_done_callback(finished)
        return task

    def start(
        self,
        scenario: Scenario,
        path: Path,
        *,
        autostart: bool = True,
        warm: Callable[[WorkerHandle], Coroutine[Any, Any, None]] | None = None,
    ) -> WorkerHandle:
        """Transfer ownership before starting; never start a run a second time."""
        from .worker import execute

        asyncio.get_running_loop()  # Reject missing task ownership before starting a child.
        run_id = path.name
        with self._lock:
            if self._closing:
                raise WorkerFailure(run_id, 503, "HOST_CLOSING", "Service is shutting down")
            if run_id in self._workers:
                raise ValueError(f"Worker {run_id} already has an owner")
            inherited = tuple(worker.connection for worker in self._workers.values())
        # A small exclusive ownership marker also rejects a second launch after
        # handles are released, without retaining every historical id in memory.
        # This is an ownership claim, never a startup ACK or completion receipt.
        try:
            with (path / "worker-owner.json").open("x", encoding="utf-8") as marker:
                json.dump({"contract": "aeroagentsim.worker-owner/v1", "id": run_id}, marker)
        except FileExistsError as exc:
            raise ValueError(f"Worker {run_id} already has an owner") from exc
        paused, stopped, started, ready = (
            _SharedFlag(self.context.RawValue("b", 0)) for _ in range(4)
        )
        # The event-loop owner is the sole pacing writer; workers only read.
        # No shared semaphore may be held by a killable child, even for a read.
        speed = self.context.RawValue("d", 1.0 if scenario.pacing == "realtime" else 0.0)
        if autostart:
            started.set()
        host_input, worker_input = self.context.Pipe()
        try:
            process = self.context.Process(
                target=self.target or execute,
                args=(scenario, path, paused, stopped, worker_input, started, speed,
                      ready, (*inherited, host_input), self.limits.cleanup_timeout_s),
                name=run_id,
            )
        except BaseException:
            host_input.close()
            worker_input.close()
            raise
        worker = WorkerHandle(
            run_id, path, process, paused, stopped, started, speed, ready, host_input
        )
        with self._lock:
            self._workers[run_id] = worker
        try:
            process.start()
        except BaseException as exc:
            worker.stop_reason = "Worker process start failed"
            partial_start = process.pid is not None
            if partial_start:
                # Even a partially successful process start retains an owner.
                worker.monitor = self._task(self._monitor(worker))
                self._abort(worker, worker.stop_reason)
            else:
                try:
                    self._observe_exit(worker)
                finally:
                    self._release(worker)
            if isinstance(exc, Exception):
                raise WorkerFailure(
                    run_id, 503, "WORKER_START_FAILED", worker.stop_reason,
                    outcome="uncertain" if partial_start else "not_submitted",
                ) from exc
            raise
        finally:
            worker_input.close()
        worker.monitor = self._task(self._monitor(worker))
        if warm is not None:
            worker.projection = self._task(warm(worker))
        return worker

    async def wait_started(self, worker: WorkerHandle) -> dict[str, Any]:
        """Wait for the real initialized world ACK, not a validation simulation."""
        deadline = worker.launched_at + self.limits.startup_timeout_s
        while True:
            if worker.ready.is_set():
                return self._metadata(worker)
            if not worker.alive:
                self._observe_exit(worker)
                metadata = self._metadata(worker)
                faulted = metadata["status"] == "faulted"
                raise WorkerFailure(
                    worker.run_id, 422 if faulted else 500,
                    "WORKER_STARTUP_FAILED" if faulted else "WORKER_EXITED",
                    metadata.get("error") or "Worker exited before acknowledging initialization",
                    outcome="uncertain",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort(worker, "Worker startup exceeded operator deadline")
                raise WorkerFailure(
                    worker.run_id, 504, "WORKER_STARTUP_TIMEOUT",
                    "Worker startup exceeded operator deadline; termination requested; "
                    "inspect the run, do not automatically launch it again",
                    outcome="uncertain",
                )
            await asyncio.sleep(min(_POLL_SECONDS, remaining))

    def input_call(
        self, run_id: str, operation: str, body: Any, *, deadline: float | None = None,
    ) -> dict[str, Any]:
        """One bounded exchange, including lock acquisition, send and full reply.

        An HTTP caller supplies its absolute deadline before executor dispatch;
        queue delay cannot extend the budget or cause a late unsent submission.
        """
        now = time.monotonic()
        deadline = min(deadline, now + self.limits.input_timeout_s) if deadline is not None else (
            now + self.limits.input_timeout_s
        )
        if deadline <= now:
            raise WorkerFailure(
                run_id, 504, "WORKER_INPUT_TIMEOUT", "Input deadline expired before submission"
            )
        worker = self.get(run_id)
        if worker is None or self._closing or worker.exited.is_set():
            raise WorkerFailure(run_id, 409, "WORKER_EXITED", "Run has no active worker")
        if not worker.input_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise WorkerFailure(
                run_id, 504, "WORKER_INPUT_BUSY",
                "Input channel is still awaiting a prior reply; this request was not submitted",
            )
        if worker.exited.is_set() or worker.released or self._closing:
            worker.input_lock.release()
            raise WorkerFailure(run_id, 409, "WORKER_EXITED", "Run worker has exited")
        done = threading.Event()
        state_lock = threading.Lock()
        state: dict[str, Any] = {"attempted": False, "expired": False}

        def exchange() -> None:
            try:
                with state_lock:
                    if state["expired"] or time.monotonic() >= deadline:
                        return
                    if worker.channel_closing:
                        raise EOFError("Worker input channel is closing")
                    # A send can partially succeed before raising or blocking.
                    state["attempted"] = True
                worker.connection.send((operation, body))
                while not worker.connection.poll(_POLL_SECONDS):
                    if worker.exited.is_set():
                        raise EOFError("Worker exited before replying")
                state["reply"] = worker.connection.recv()
            except Exception as exc:  # noqa: BLE001 - transport/decoding never leaks a thread traceback
                state["error"] = exc
            finally:
                with worker.channel_lock:
                    worker.channel_busy = False
                    if worker.channel_closing:
                        try:
                            worker.connection.close()
                        except OSError as exc:
                            if "reply" not in state:
                                state.setdefault("error", exc)
                worker.rpc_thread = None
                done.set()
                worker.input_lock.release()

        try:
            with worker.channel_lock:
                if worker.channel_closing:
                    raise WorkerFailure(run_id, 409, "WORKER_EXITED", "Run worker has exited")
                worker.channel_busy = True
            thread = threading.Thread(target=exchange, name=f"input-{run_id}", daemon=True)
            worker.rpc_thread = thread
            thread.start()
        except BaseException:
            with worker.channel_lock:
                if worker.channel_busy:
                    worker.channel_busy = False
                    if worker.channel_closing:
                        worker.connection.close()
            worker.rpc_thread = None
            worker.input_lock.release()
            raise
        if not done.wait(max(0.0, deadline - time.monotonic())):
            with state_lock:
                state["expired"] = True
                attempted = state["attempted"]
            raise WorkerFailure(
                run_id, 504, "WORKER_INPUT_TIMEOUT",
                "Input acknowledgement exceeded operator deadline; inspect admissions, "
                "source progress and journal; do not automatically resubmit",
                outcome="uncertain" if attempted else "not_submitted",
            )
        if "error" in state:
            raise WorkerFailure(
                run_id, 409, "WORKER_INPUT_CLOSED",
                "Worker closed before acknowledging input; inspect journal; "
                "do not automatically resubmit",
                outcome="uncertain" if state["attempted"] else "not_submitted",
            ) from state["error"]
        if "reply" not in state:
            raise WorkerFailure(
                run_id, 504, "WORKER_INPUT_TIMEOUT", "Input deadline expired before submission"
            )
        reply = state["reply"]
        if not isinstance(reply, dict) or not ("error" in reply or "result" in reply):
            raise WorkerFailure(
                run_id, 502, "WORKER_INPUT_PROTOCOL", "Invalid worker reply; inspect journal",
                outcome="uncertain",
            )
        # Keep the worker's existing kernel error / admission receipt unchanged.
        return reply

    @staticmethod
    def _metadata(worker: WorkerHandle) -> dict[str, Any]:
        return dict(json.loads((worker.path / "manifest.json").read_text()))

    def _observe_exit(self, worker: WorkerHandle) -> None:
        if worker.exited.is_set():
            return
        if worker.process.pid is not None:
            worker.process.join(0)
        worker.exited.set()
        # Never index an arbitrary visible tail on process death. The small ACK
        # supplies the final cursor; projections reconcile their offsets on read.
        metadata = self._metadata(worker)
        if metadata["status"] not in TERMINAL:
            observed = metadata.get("execution_outcome", {})
            metadata["status"] = (
                observed["status"] if observed.get("status") in {"faulted", "input_timeout"}
                else "interrupted"
            )
            metadata.pop("waiting", None)
            if observed.get("waiting") is not None:
                metadata["waiting"] = observed["waiting"]
            metadata["error"] = metadata.get("error") or observed.get("error") or (
                worker.stop_reason or "Worker exited without recording a terminal status"
            )
        prefix = committed_prefix(worker.path)
        metadata["final_cursor"] = max(1, prefix["index"] + 1) if prefix is not None else 1
        metadata["host_lifecycle"] = {
            "exitcode": worker.process.exitcode,
            "termination_confirmed": True,
            "forced": worker.forced,
            "reason": worker.stop_reason,
        }
        if prefix is None:
            self._atomic(worker.path, "index.json", [])
        self._atomic(worker.path, "manifest.json", metadata)

    @staticmethod
    def _atomic(path: Path, name: str, value: Any) -> None:
        target = path / name
        temporary = path / (name + ".host-writing")
        temporary.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
        temporary.replace(target)

    async def _monitor(self, worker: WorkerHandle) -> None:
        try:
            while worker.alive:
                await asyncio.sleep(_POLL_SECONDS)
            # File/cache work for a dead run must not hold up process control
            # for its live siblings during a shared shutdown phase.
            while self._controlling:
                await asyncio.sleep(_POLL_SECONDS)
            self._observe_exit(worker)
        finally:
            if not worker.alive:
                await self._finish(worker)

    async def _finish(self, worker: WorkerHandle) -> None:
        try:
            deadline = time.monotonic() + self.limits.cleanup_timeout_s
            # Allow a buffered final reply and the last cache batch to drain together.
            while time.monotonic() < deadline:
                rpc = worker.rpc_thread
                rpc_pending = rpc is not None and rpc.is_alive()
                projection_pending = worker.projection is not None and not worker.projection.done()
                if not rpc_pending and not projection_pending:
                    break
                await asyncio.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        finally:
            if worker.projection is not None and not worker.projection.done():
                worker.projection.cancel()
            self._release(worker)

    def _release(self, worker: WorkerHandle) -> None:
        if worker.released:
            return
        try:
            with worker.channel_lock:
                worker.channel_closing = True
                # Wake a partial recv/send even if a descendant retained the peer.
                try:
                    channel = socket.socket(fileno=worker.connection.fileno())
                    try:
                        channel.shutdown(socket.SHUT_RDWR)
                    finally:
                        channel.detach()  # Connection remains the sole fd owner.
                except (OSError, ValueError):
                    pass
                # Do not close/reuse an fd underneath a blocked I/O thread. That
                # exchange owns the final close after shutdown wakes its syscall.
                if not worker.channel_busy:
                    worker.connection.close()
        finally:
            try:
                rpc = worker.rpc_thread
                if rpc is not None:
                    if rpc.ident is not None:
                        rpc.join(0)
                    worker.rpc_thread = None
            finally:
                worker.process.close()
                worker.released = True
                worker.projection = worker.monitor = worker.abort = None
                with self._lock:
                    if self._workers.get(worker.run_id) is worker:
                        del self._workers[worker.run_id]

    def _abort(self, worker: WorkerHandle, reason: str) -> None:
        worker.stop_reason = reason
        worker.stopped.set()
        worker.paused.clear()
        if worker.alive and worker.abort is None:
            worker.forced = True
            worker.process.terminate()
            worker.abort = self._task(self._escalate(worker))

    async def _escalate(self, worker: WorkerHandle) -> None:
        await self._wait_processes([worker], self.limits.terminate_grace_s)
        if worker.alive:
            worker.process.kill()
            await self._wait_processes([worker], self.limits.kill_grace_s)
        if worker.alive:
            _LOG.error("Worker %s termination is not confirmed", worker.run_id)

    @staticmethod
    async def _wait_processes(workers: list[WorkerHandle], seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while any(worker.alive for worker in workers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(_POLL_SECONDS, remaining))

    async def close(self) -> None:
        """Signal all first; no executor queue of per-run blocking joins."""
        async with self._close_lock:
            with self._lock:
                self._closing = True
                workers = list(self._workers.values())
            self._controlling = True
            try:
                for worker in workers:
                    worker.stopped.set()
                    worker.paused.clear()
                await self._wait_processes(workers, self.limits.shutdown_grace_s)
                for worker in workers:
                    if worker.alive:
                        worker.forced = True
                        worker.stop_reason = "Worker did not close within shutdown deadline"
                        worker.process.terminate()
                await self._wait_processes(workers, self.limits.terminate_grace_s)
                for worker in workers:
                    if worker.alive:
                        worker.process.kill()
                await self._wait_processes(workers, self.limits.kill_grace_s)
            finally:
                self._controlling = False
            # Metadata and derived work cannot delay escalation for live siblings.
            for worker in workers:
                if not worker.alive and not worker.released:
                    self._observe_exit(worker)
                elif worker.alive:
                    _LOG.error("Worker %s termination is not confirmed", worker.run_id)
            tasks = set(self._tasks)
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=self.limits.cleanup_timeout_s)
                live_monitors = {worker.monitor for worker in workers if worker.alive}
                for task in pending:
                    # An unconfirmed survivor still has exactly one owner and
                    # reaper if the embedding event loop continues after close.
                    if task not in live_monitors:
                        task.cancel()
            # Cancellation must not prevent release of a confirmed-dead child.
            for worker in workers:
                if not worker.alive and not worker.released:
                    self._release(worker)
            await asyncio.sleep(0)  # Run cancellation/discard callbacks, never join indefinitely.
