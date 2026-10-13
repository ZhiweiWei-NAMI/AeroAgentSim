"""One process per run; control signals take effect only at sealed boundaries."""

from __future__ import annotations

import gc
import threading
import time
from collections.abc import Callable
from multiprocessing import parent_process
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from aerokernel import IngressWait
from aerokernel.errors import KernelError

from aeroagentsim.platform.ingress import receipt_data, source_stamp, submission
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import Scenario
from aeroagentsim.scenario.loader import contract, integer, text

from .storage import RunStorage


class HostPacer:
    """Gate natural commits, without changing the pinned advance schedule."""

    def __init__(
        self,
        factor: Callable[[], float],
        committed_ns: Callable[[], int],
        paused: Any,
        stopped: Any,
        status: Callable[[str], None],
        publish: Callable[[], None],
    ) -> None:
        self.factor = factor
        self.committed_ns = committed_ns
        self.paused, self.stopped, self.status = paused, stopped, status
        self.publish = publish
        self.wait = threading.Event()
        self.current_factor = factor()
        self.reset()

    def reset(self) -> None:
        self.anchor_wall = time.perf_counter()
        self.anchor_ns = float(self.committed_ns())

    def active(self) -> bool:
        if self.paused.is_set() and not self.stopped.is_set():
            self.status("paused")
            while self.paused.is_set() and not self.stopped.is_set():
                self.publish()
                self.wait.wait(0.01)
            if not self.stopped.is_set():
                self.current_factor = self.factor()
                self.reset()
                self.status("running")
        return not self.stopped.is_set()

    def before_boundary(self, target_ns: int) -> bool:
        while self.active():
            self.publish()
            now = time.perf_counter()
            factor = self.factor()
            if factor != self.current_factor:
                # A slower rate takes effect at this committed boundary. Do
                # not spend accumulated high-speed credit after a slowdown:
                # it can otherwise advance minutes at the newly selected .05x.
                self.anchor_ns = float(self.committed_ns())
                self.anchor_wall, self.current_factor = now, factor
            if factor == 0:
                return True
            remaining = (
                (target_ns - self.anchor_ns) / 1e9 / factor
                - (now - self.anchor_wall)
            )
            if remaining <= 0:
                return True
            self.wait.wait(min(0.01, remaining))
        return False


def close_inherited_network_sockets() -> None:
    """A forked simulation owns its IPC, never the parent's HTTP transports.

    Keeping a duplicate accepted socket alive after Uvicorn closes it lets a
    browser reuse that connection and send requests no server will ever read.
    Close the Python socket objects, so later GC cannot close a reused fd.
    Multiprocessing's Unix-domain input channel remains owned by this worker.
    """
    import socket

    for value in gc.get_objects():
        if isinstance(value, socket.socket) and value.family in (socket.AF_INET, socket.AF_INET6):
            value.close()


def superseded_watermark(body: Any, watermarks: Any) -> dict[str, Any] | None:
    """Compare an explicitly observed frontier without writing a stale closure.

    A browser and another source producer may race. The conditional receipt
    reports the real newer frontier and explicitly says no advance occurred.
    Unconditional calls retain the kernel's strict monotonicity contract.
    """
    if not isinstance(body, dict) or "expected_watermark_ns" not in body:
        return None
    spec = contract(body, "watermark", {"stream_id", "watermark_ns", "expected_watermark_ns"})
    stream = text(spec["stream_id"], "watermark.stream_id")
    expected = integer(spec["expected_watermark_ns"], "watermark.expected_watermark_ns")
    requested = integer(spec["watermark_ns"], "watermark.watermark_ns")
    if expected < 0 or requested < expected:
        raise ValueError("watermark: requested closure must follow the observed frontier")
    if stream not in watermarks:
        raise ValueError(f"watermark: unknown source {stream}")
    current = watermarks[stream]
    if current < expected:
        raise ValueError("watermark: observed frontier is ahead of the source")
    if current == expected:
        return None
    return {"contract": "aeroagentsim.watermark-receipt/v2", "stream_id": stream,
            "watermark_ns": current, "advanced": False}


def execute(
    scenario: Scenario,
    directory: Path,
    paused: Any,
    stopped: Any,
    ingress: Connection | None = None,
    started: Any | None = None,
    speed: Any | None = None,
) -> None:
    """Run a prepared scenario.

    With ``started``, the worker commits the header and t=0 state, reports
    ``initialized`` and advances simulated time only after ``started`` is set.
    ``speed`` (shared double) is the host pacing factor: simulated seconds per
    wall second, or 0 for as fast as possible. Pacing never changes results.
    """
    forked = parent_process() is not None
    if forked:
        # A run must not repeatedly collect the HTTP host's retained graphs or
        # execute its abandoned finalizers. Keep new run objects collectable.
        gc.disable()
    close_inherited_network_sockets()
    if forked:
        gc.freeze()
        gc.enable()
    session: RunSession | None = None
    storage = RunStorage(directory)
    wait = threading.Event()
    outcome = "completed"
    error: str | None = None
    timed_out_wait: dict[str, Any] | None = None
    ingress_done = threading.Event()
    finishing = threading.Event()
    ingress_thread: threading.Thread | None = None
    reported_wait: IngressWait | None = None
    pacer: HostPacer | None = None
    last_publication = 0.0
    published_watermarks: dict[str, int] | None = None
    publication_lock = threading.Lock()
    execution_thread = threading.current_thread()

    def pace_factor() -> float:
        if speed is not None:
            return float(speed.value)
        return 1.0 if scenario.pacing == "realtime" else 0.0

    def on_wait(state: IngressWait | None) -> bool:
        nonlocal reported_wait
        assert pacer is not None
        if not pacer.active():
            return False
        if state != reported_wait:
            assert session is not None
            if state is None and reported_wait is not None:
                pacer.reset()
            session.report_wait(state)
            reported_wait = state
        return True

    def publish_committed(*, force: bool = False) -> None:
        nonlocal last_publication, published_watermarks
        assert session is not None
        now = time.perf_counter()
        # Max needs only a small viewer coordinate, not a file rewrite for
        # every wave. Paced waits and controls force their current cut out.
        if force or now - last_publication >= 0.05:
            coordinate = session.simulation.kernel.acknowledged_prefix
            watermarks = dict(session.simulation.kernel.ingress_watermarks)
            with publication_lock:
                published = session.storage._published_prefix
                if published is None or coordinate[1] >= published[1]:
                    session.storage.publish_prefix(*coordinate)
                    published_watermarks = watermarks
                    last_publication = now

    def on_boundary(target_ns: int) -> bool:
        # Flush the real prefix before sleeping; never make viewers wait for
        # the outer (usually ten-second) run_until limit.
        publish_committed(force=pace_factor() > 0 or paused.is_set())
        assert pacer is not None
        return pacer.before_boundary(target_ns)

    def pacing_status(value: str) -> None:
        nonlocal reported_wait
        publish_committed(force=True)
        storage.status(value)
        reported_wait = None

    def poll_controls() -> bool | None:
        # An ingress publication can use the same observer while the gate has
        # released the lock. Only the execution thread owns host clock changes.
        if threading.current_thread() is not execution_thread:
            return
        # Every available acknowledged cut is a control opportunity. A time
        # throttle can skip the last one before a costly atomic invocation.
        assert pacer is not None
        return pacer.active()

    def on_commit() -> None:
        publish_committed()

    def accept_ingress(active: RunSession, connection: Connection) -> None:
        while True:
            if not connection.poll(0.02):
                if ingress_done.is_set():
                    return
                continue
            try:
                operation, body = connection.recv()
            except EOFError:
                return
            try:
                if operation == "watermark":
                    superseded = superseded_watermark(body, active.simulation.kernel.ingress_watermarks)
                    if superseded is not None:
                        connection.send({"result": superseded})
                        continue
                if finishing.is_set() and operation != "progress":
                    raise ValueError("Run is closing; source progress is read-only")
                if operation == "progress":
                    # Read the last acknowledged publication without waiting
                    # behind an engine call or an atomic candidate validation.
                    assert published_watermarks is not None
                    result = {
                        "contract": "aeroagentsim.input-progress/v1",
                        "watermarks": dict(published_watermarks),
                        "status": storage.metadata()["status"],
                        "pause_requested": paused.is_set(),
                    }
                elif operation == "ingress":
                    validate_injection(active, body)
                    engine, command, stamp, stream_id = submission(body)
                    result = receipt_data(
                        active.submit_live(engine, command, stamp, stream_id=stream_id)
                    )
                elif operation == "watermark":
                    if paused.is_set():
                        raise ValueError("Run is paused; resume before advancing source progress")
                    spec = contract(
                        body,
                        "watermark",
                        set(),
                        {"watermark_ns", "source_stamp", "stream_id", "expected_watermark_ns"},
                    )
                    if ("watermark_ns" in spec) == ("source_stamp" in spec):
                        raise ValueError(
                            "watermark: select exactly one of watermark_ns or source_stamp"
                        )
                    stream_id = active.simulation.resolve_stream(
                        text(spec["stream_id"], "watermark.stream_id")
                        if "stream_id" in spec
                        else None
                    )
                    if "source_stamp" in spec:
                        active.advance_source_progress(
                            source_stamp(
                                spec["source_stamp"], "watermark.source_stamp"
                            ),
                            stream_id=stream_id,
                        )
                    else:
                        active.advance_watermark(
                            integer(spec["watermark_ns"], "watermark.watermark_ns"),
                            stream_id=stream_id,
                        )
                    result = {
                        "contract": "aeroagentsim.watermark-receipt/v2",
                        "stream_id": stream_id,
                        "watermark_ns": active.simulation.kernel.ingress_watermarks[
                            stream_id
                        ],
                    }
                    if "expected_watermark_ns" in spec:
                        result["advanced"] = True
                else:
                    raise ValueError("Unknown worker input operation")
                if operation != "progress":
                    publish_committed(force=True)
                response = {"result": result}
            except (ValueError, TypeError, RuntimeError) as exc:
                response = {
                    "error": {
                        "code": exc.code
                        if isinstance(exc, KernelError)
                        else "INGRESS_REQUEST",
                        "message": str(exc),
                    }
                }
            connection.send(response)

    try:
        session = RunSession(scenario, directory, prepared=True)
        # Share its incremental offsets. A second writer would rescan the large
        # header and entire WAL again when acknowledging Stop.
        storage = session.storage
        session.start()
        publish_committed(force=True)
        if ingress is not None:
            ingress_thread = threading.Thread(
                target=accept_ingress, args=(session, ingress), daemon=True
            )
            ingress_thread.start()
        if started is not None and not started.is_set():
            storage.status("initialized")
            while not started.is_set() and not stopped.is_set():
                wait.wait(0.02)
            if not stopped.is_set():
                storage.status("running")
        # Initialization time is not simulated time. Gate each natural kernel
        # boundary before computation, retaining the headless run-limit records.
        pacer = HostPacer(
            pace_factor,
            lambda: session.simulation.kernel.view().cut.instant.ns,
            paused,
            stopped,
            pacing_status,
            publish_committed,
        )
        while session.now_ns < scenario.until_ns and not stopped.is_set():
            # RunSession's public advance API has no host hooks. The kernel
            # still executes its identical schedule; RunSession owns artifacts
            # and finalization, while this worker publishes the live prefix.
            session.simulation.kernel.run_until(
                min(session.now_ns + scenario.advance_ns, scenario.until_ns),
                on_wait=on_wait,
                on_boundary=on_boundary,
                on_commit=on_commit,
                on_poll=poll_controls,
            )
            session.now_ns = session.simulation.kernel.sealed_ns
        outcome = "stopped" if stopped.is_set() else "completed"
    except Exception as exc:  # noqa: BLE001 - persist the actual worker fault
        if isinstance(exc, KernelError) and exc.code == "WATERMARK_TIMEOUT":
            outcome = "stopped" if stopped.is_set() else "input_timeout"
            if outcome == "input_timeout":
                timed_out_wait = {
                    "stream_ids": list(exc.context["stream_ids"]),
                    "at_ns": str(exc.context["target_ns"]),
                }
                error = str(exc)
        else:
            outcome = "faulted"
            error = storage.metadata().get("error") or f"{type(exc).__name__}: {exc}"
    finally:
        finishing.set()
        if session is not None:
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001 - retain actual cleanup failure
                outcome = "faulted"
                error = f"{error + '; ' if error else ''}cleanup: {type(exc).__name__}: {exc}"
            try:
                # Cleanup can fail after committing a real fault/terminal tail.
                publish_committed(force=True)
            except Exception as exc:  # noqa: BLE001 - retain publication failure
                outcome = "faulted"
                error = f"{error + '; ' if error else ''}publication / 发布: {type(exc).__name__}: {exc}"
            # Publish the closed kernel's real source frontier before its
            # terminal status. Keep progress RPC alive throughout cleanup.
            storage._atomic("input-progress.json", {
                "watermarks": dict(session.simulation.kernel.ingress_watermarks),
            })
        storage.status(outcome, error=error, waiting=timed_out_wait)
        ingress_done.set()
        if ingress_thread is not None:
            ingress_thread.join()
        if ingress is not None:
            ingress.close()


def validate_injection(active: RunSession, body: Any) -> None:
    """Validate the pinned injection manifest before runtime admission, without mutation."""
    from aeroagentsim.behaviours.records import INJECT

    if not isinstance(body, dict):
        raise TypeError("ingress: expected mapping")
    declared: list[tuple[str, dict[str, Any]]] = []
    for engine_id, engine in active.scenario.engines.items():
        if engine["plugin"] != "behaviour":
            continue
        for package in engine["config"]["packages"]:
            document = package.get("document", package)
            declared.extend(
                (engine_id, point) for point in document.get("injection_points", [])
            )
    schema = body.get("schema")
    if schema != INJECT and schema not in {point["command"] for _, point in declared}:
        return
    payload = contract(
        body.get("payload"), "ingress.payload", {"injection_point", "payload"}
    )
    point_id = text(payload["injection_point"], "ingress.payload.injection_point")
    candidates = [
        (engine, point)
        for engine, point in declared
        if point["id"] == point_id and point["command"] == schema
    ]
    if len(candidates) != 1:
        raise ValueError(
            "ingress.payload.injection_point: unknown or ambiguous injection point/command"
        )
    selected_engine, point = candidates[0]
    if (
        body.get("engine", selected_engine) != selected_engine
        or body.get("target") != point["target"]
        or point["target"] != selected_engine
    ):
        raise ValueError(
            "ingress.target: injection point belongs to another behaviour engine"
        )
    if body.get("stream_id") != point["stream_id"]:
        raise ValueError(
            "ingress.stream_id: injection point requires its declared named stream"
        )
    streams = [
        stream
        for stream in active.scenario.ingress_streams
        if stream.id == point["stream_id"] and selected_engine in stream.engine_ids
    ]
    if len(streams) != 1:
        raise ValueError(
            "ingress.stream_id: injection stream is not bound to the behaviour engine"
        )
    descriptor = active.scenario.registry.message(point["emits"])
    if descriptor.kind != "event":
        raise ValueError("ingress.injection_point: event descriptor required")
    active.scenario.registry.validate(descriptor.schema, payload["payload"])
