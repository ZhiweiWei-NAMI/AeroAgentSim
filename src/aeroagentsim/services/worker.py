"""One process per run; control signals take effect only at sealed boundaries."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import Scenario

from .storage import RunStorage


def execute(scenario: Scenario, directory: Path, paused: Any, stopped: Any) -> None:
    session: RunSession | None = None
    storage = RunStorage(directory)
    wait = threading.Event()
    try:
        session = RunSession(scenario, directory, prepared=True)
        session.start()
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
        storage.status("stopped" if stopped.is_set() else "completed")
    except Exception as exc:  # noqa: BLE001 - persist the actual worker fault
        storage.status("faulted", error=f"{type(exc).__name__}: {exc}")
    finally:
        if session is not None:
            session.close()
