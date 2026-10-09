"""One process per run; control signals take effect only at sealed boundaries."""

from __future__ import annotations

import threading
import time
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
    timed_out_wait: dict[str, Any] | None = None
    ingress_done = threading.Event()
    ingress_thread: threading.Thread | None = None
    reported_wait: IngressWait | None = None

    def on_wait(state: IngressWait | None) -> bool:
        nonlocal reported_wait
        if stopped.is_set() or paused.is_set():
            return False
        if state != reported_wait:
            assert session is not None
            session.report_wait(state)
            reported_wait = state
        return True

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
                    validate_injection(active, body)
                    engine, command, stamp, stream_id = submission(body)
                    result = receipt_data(
                        active.submit_live(engine, command, stamp, stream_id=stream_id)
                    )
                elif operation == "watermark":
                    spec = contract(
                        body,
                        "watermark",
                        set(),
                        {"watermark_ns", "source_stamp", "stream_id"},
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
                reported_wait = None
                while paused.is_set() and not stopped.is_set():
                    wait.wait(0.02)
                if stopped.is_set():
                    break
                storage.status("running")
                # A resume changes only host pacing; physical integration is frozen.
                session.started_wall = time.perf_counter() - session.now_ns / 1e9
            session.run_until(
                min(session.now_ns + scenario.advance_ns, scenario.until_ns),
                on_wait=on_wait,
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
        storage.status(outcome, error=error, waiting=timed_out_wait)


def validate_injection(active: RunSession, body: Any) -> None:
    """Validate the pinned injection manifest before K4 admission, without mutation."""
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
