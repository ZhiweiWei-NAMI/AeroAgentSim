"""One process per run; control signals take effect only at sealed boundaries."""

from __future__ import annotations

import threading
import time
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from aerokernel.errors import KernelError

from aeroagentsim.platform.ingress import submission
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import Scenario
from aeroagentsim.scenario.loader import contract, integer

from .storage import RunStorage


def execute(
    scenario: Scenario,
    directory: Path,
    paused: Any,
    stopped: Any,
    ingress: Connection | None = None,
) -> None:
    session: RunSession | None = None
    storage = RunStorage(directory)
    wait = threading.Event()
    outcome = "completed"
    error: str | None = None
    ingress_done = threading.Event()
    ingress_thread: threading.Thread | None = None

    def accept_ingress(active: RunSession, connection: Connection) -> None:
        while not ingress_done.is_set():
            if not connection.poll(0.02):
                continue
            try:
                operation, body = connection.recv()
            except EOFError:
                return
            try:
                if operation == "ingress":
                    engine, command, stamp = submission(body)
                    result = active.submit_live(engine, command, stamp).to_data()
                elif operation == "watermark":
                    spec = contract(body, "watermark", {"watermark_ns"})
                    ns = integer(spec["watermark_ns"], "watermark.watermark_ns")
                    active.advance_watermark(ns)
                    result = {
                        "contract": "aeroagentsim.watermark-receipt/v1",
                        "watermark_ns": ns,
                    }
                else:
                    raise ValueError("Unknown worker input operation")
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
        session.start()
        if ingress is not None:
            ingress_thread = threading.Thread(
                target=accept_ingress, args=(session, ingress), daemon=True
            )
            ingress_thread.start()
        while session.now_ns < scenario.until_ns and not stopped.is_set():
            if paused.is_set():
                storage.status("paused")
                while paused.is_set() and not stopped.is_set():
                    wait.wait(0.02)
                if stopped.is_set():
                    break
                storage.status("running")
                # A resume changes only host pacing; physical integration is frozen.
                session.started_wall = time.perf_counter() - session.now_ns / 1e9
            session.run_until(
                min(session.now_ns + scenario.advance_ns, scenario.until_ns)
            )
            if scenario.pacing == "realtime":
                deadline = session.started_wall + session.now_ns / 1e9
                while (
                    time.perf_counter() < deadline
                    and not stopped.is_set()
                    and not paused.is_set()
                ):
                    wait.wait(min(0.02, max(0.0, deadline - time.perf_counter())))
        outcome = "stopped" if stopped.is_set() else "completed"
    except Exception as exc:  # noqa: BLE001 - persist the actual worker fault
        outcome = "faulted"
        error = storage.metadata().get("error") or f"{type(exc).__name__}: {exc}"
    finally:
        ingress_done.set()
        if ingress_thread is not None:
            ingress_thread.join()
        if ingress is not None:
            ingress.close()
        if session is not None:
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001 - retain actual cleanup failure
                outcome = "faulted"
                error = f"{error + '; ' if error else ''}cleanup: {type(exc).__name__}: {exc}"
        storage.index()
        storage.status(outcome, error=error)
