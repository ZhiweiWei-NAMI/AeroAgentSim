from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import cast

import pytest

from aero_bench.control.contracts import StartRunRequest
from aero_bench.control.manager import (
    ControlManagerError,
    ControlRunManager,
    _ManagedRun,
)
from aero_bench.runner.session import RunExecutionSession


@dataclass(frozen=True)
class _Transition:
    sequence: int


class _TransitioningSession:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._phase = "starting"
        self._transitions = [_Transition(0)]
        self.wait_started = threading.Event()
        self.stop_received = threading.Event()
        self.stop_control_ids: list[str] = []

    @property
    def phase(self) -> str:
        with self._condition:
            return self._phase

    def advance(self, phase: str) -> None:
        with self._condition:
            self._phase = phase
            self._transitions.append(_Transition(len(self._transitions)))
            self._condition.notify_all()

    def wait_for_transitions(
        self, sequence: int, *, timeout_seconds: float,
    ) -> tuple[_Transition, ...]:
        self.wait_started.set()
        with self._condition:
            available = tuple(item for item in self._transitions if item.sequence > sequence)
            if available:
                return available
            self._condition.wait(timeout_seconds)
            return tuple(item for item in self._transitions if item.sequence > sequence)

    def control_runtime(self, operation: str, *, control_id: str) -> object:
        assert operation == "stop"
        self.stop_control_ids.append(control_id)
        self.stop_received.set()
        return object()


class _FailingStopSession(_TransitioningSession):
    def __init__(self, *, phase_race: bool) -> None:
        super().__init__()
        self._phase = "running"
        self.phase_race = phase_race

    def control_runtime(self, operation: str, *, control_id: str) -> object:
        assert operation == "stop"
        self.stop_control_ids.append(control_id)
        if self.phase_race:
            self.advance("sealing")
        self.stop_received.set()
        raise RuntimeError("stop failed")


def _empty_manager() -> ControlRunManager:
    manager = object.__new__(ControlRunManager)
    manager._lock = threading.RLock()
    manager._managed = {}
    manager._starts = {}
    manager._shutdown_requested = False
    return manager


def test_shutdown_stops_run_that_transitions_from_starting_to_running() -> None:
    manager = _empty_manager()
    session = _TransitioningSession()

    def execute() -> None:
        assert session.wait_started.wait(1)
        session.advance("running")
        assert session.stop_received.wait(1)
        session.advance("cancelled")

    execution_thread = threading.Thread(target=execute, name="test-execution")
    managed = _ManagedRun(
        start_id="start.shutdown-race",
        session=cast(RunExecutionSession, session),
        operator_token="operator",
        csrf_token="csrf",
        thread=execution_thread,
    )
    manager._managed["run"] = managed
    execution_thread.start()

    manager.shutdown(join_timeout_seconds=2)

    assert not execution_thread.is_alive()
    assert len(session.stop_control_ids) == 1
    assert session.stop_control_ids[0].startswith("service-shutdown-")
    assert managed.shutdown_stop_requested is True
    manager.shutdown(join_timeout_seconds=0)
    assert len(session.stop_control_ids) == 1


def test_shutdown_rejects_later_run_start() -> None:
    manager = _empty_manager()
    manager.shutdown(join_timeout_seconds=0)

    request = StartRunRequest(
        schema_version="aero-bench.start-run-request/v1",
        start_id="start.after-shutdown",
        run_id="a" * 64,
    )
    with pytest.raises(ControlManagerError, match="shutting down") as caught:
        manager.start(request)
    assert caught.value.code == "service.shutting_down"


@pytest.mark.parametrize(
    ("phase_race", "expected_failures"),
    [
        (False, ("shutdown_stop_failed",)),
        (True, ()),
    ],
)
def test_shutdown_distinguishes_stop_failure_from_terminal_phase_race(
    phase_race: bool,
    expected_failures: tuple[str, ...],
) -> None:
    manager = _empty_manager()
    session = _FailingStopSession(phase_race=phase_race)
    release_execution = threading.Event()

    def execute() -> None:
        assert release_execution.wait(2)
        session.advance("completed")

    execution_thread = threading.Thread(target=execute, name="test-failing-stop")
    managed = _ManagedRun(
        start_id="start.shutdown-failure",
        session=cast(RunExecutionSession, session),
        operator_token="operator",
        csrf_token="csrf",
        thread=execution_thread,
    )
    manager._managed["run"] = managed
    execution_thread.start()

    manager.shutdown(join_timeout_seconds=0.01)
    assert session.stop_received.wait(1)
    assert managed.shutdown_thread is not None
    managed.shutdown_thread.join(timeout=1)
    assert not managed.shutdown_thread.is_alive()
    assert managed.management_failure_classes == expected_failures
    assert len(session.stop_control_ids) == 1

    release_execution.set()
    execution_thread.join(timeout=1)
    assert not execution_thread.is_alive()


def test_shutdown_rejects_negative_join_timeout() -> None:
    manager = _empty_manager()
    with pytest.raises(ValueError, match="cannot be negative"):
        manager.shutdown(join_timeout_seconds=-0.1)
