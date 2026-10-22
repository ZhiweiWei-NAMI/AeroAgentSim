"""Failed construction releases all owned resources without replacing its cause."""

from __future__ import annotations

import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aerokernel import Journal, Kernel, Partition
from aerokernel.errors import KernelError
from aerokernel.sdk import ContextEngine

from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import Scenario, ScenarioError, load_scenario


class ServiceEngine(ContextEngine):
    def __init__(self, identifier: str) -> None:
        super().__init__(Partition(identifier, identifier))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.mark.parametrize("failure", ["factory", "binding"])
def test_failed_construction_closes_all_returned_engine_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    scenario = replace(
        load_scenario(Path("scenarios/realtime-streams.yaml")),
        engines={name: {"plugin": "test-service", "config": {}} for name in ("a", "z")},
        ingress_streams=(),
    )
    built: list[ServiceEngine] = []

    def build(self: EngineCatalog, plugin: str, context: EngineBuild) -> ServiceEngine:
        if context.id == "z" and failure == "factory":
            raise ValueError("invalid later engine")
        engine = ServiceEngine(context.id)
        built.append(engine)
        return engine

    def failed_bind(self: Kernel, *args: object) -> None:
        raise KernelError("TEST_BIND", "invalid binding after construction")

    monkeypatch.setattr(EngineCatalog, "build", build)
    monkeypatch.setattr(Kernel, "bind", failed_bind)
    journal = Journal(tmp_path / "journal.jsonl")
    try:
        with pytest.raises(
            (ScenarioError, KernelError), match="invalid later engine|invalid binding"
        ):
            Simulation(scenario, journal=journal)
        assert len(built) == (1 if failure == "factory" else 2)
        assert all(engine.close_calls == 1 for engine in built)
        assert all(not engine.thread.is_alive() for engine in built)
        assert all(engine.server.fileno() == -1 for engine in built)
    finally:
        for engine in built:
            if engine.thread.is_alive():
                engine.close()
        journal.close()


class TrackingProvider:
    """A no-server stand-in for an engine-owned provider connection."""

    def __init__(self, identifier: str, events: list[str]) -> None:
        self.identifier = identifier
        self.events = events
        self.close_calls = 0
        self.closed = False
        self.close_error: BaseException | None = None

    def close(self) -> None:
        self.events.append(f"close:provider:{self.identifier}")
        self.close_calls += 1
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class TrackingEngine(ContextEngine):
    def __init__(self, identifier: str, events: list[str]) -> None:
        super().__init__(Partition(identifier, identifier))
        self.events = events
        self.provider = TrackingProvider(identifier, events)
        self.close_calls = 0
        self.close_error: BaseException | None = None
        self.reported_version: str | None = "tracking/1"

    @property
    def version(self) -> str | None:
        self.events.append(f"version:{self.partition.id}")
        return self.reported_version

    def close(self) -> None:
        self.events.append(f"close:engine:{self.partition.id}")
        self.close_calls += 1
        try:
            self.provider.close()
        finally:
            super().close()
        if self.close_error is not None:
            raise self.close_error


class TrackingJournal(Journal):
    def __init__(self, path: Path, events: list[str]) -> None:
        super().__init__(path)
        self.events = events
        self.close_calls = 0
        self.close_error: BaseException | None = None

    def close(self) -> None:
        self.events.append("close:journal")
        self.close_calls += 1
        # Failure injection must not merely pretend that the file was closed.
        super().close()
        if self.close_error is not None:
            raise self.close_error


class ConstructionAbort(BaseException):
    """Exercise cleanup failures outside the Exception hierarchy."""


class NoNotesFailure(RuntimeError):
    # Force the Python 3.10 fallback even when running on a newer interpreter.
    add_note = None


def tracking_scenario() -> Scenario:
    scenario = load_scenario(
        Path(__file__).resolve().parents[2] / "scenarios/realtime-streams.yaml"
    )
    return load_scenario(
        {
            **scenario.document,
            "bindings": {"lifecycle": []},
            "engines": {
                name: {"plugin": "test-tracking", "config": {}}
                for name in ("a", "b", "c")
            },
            "ingress_streams": [],
        },
        base=scenario.base,
    )


def install_tracking_engines(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    close_errors: dict[str, BaseException] | None = None,
    provider_errors: dict[str, BaseException] | None = None,
    factory_error: BaseException | None = None,
    invalid_middle_version: bool = False,
) -> list[TrackingEngine]:
    built: list[TrackingEngine] = []

    def build(
        self: EngineCatalog, plugin: str, context: EngineBuild
    ) -> TrackingEngine:
        assert plugin == "test-tracking"
        events.append(f"build:{context.id}")
        if context.id == "c" and factory_error is not None:
            raise factory_error
        engine = TrackingEngine(context.id, events)
        engine.close_error = (close_errors or {}).get(context.id)
        engine.provider.close_error = (provider_errors or {}).get(context.id)
        if context.id == "b" and invalid_middle_version:
            engine.reported_version = None
        built.append(engine)
        return engine

    monkeypatch.setattr(EngineCatalog, "build", build)
    return built


def assert_failed_construction_closed(
    built: list[TrackingEngine], journal: TrackingJournal, events: list[str]
) -> None:
    expected: list[str] = []
    for engine in reversed(built):
        expected.extend(
            (
                f"close:engine:{engine.partition.id}",
                f"close:provider:{engine.partition.id}",
            )
        )
    expected.append("close:journal")
    assert [event for event in events if event.startswith("close:")] == expected
    assert all(engine.close_calls == 1 and engine.closed for engine in built)
    assert all(
        engine.provider.close_calls == 1 and engine.provider.closed for engine in built
    )
    assert journal.close_calls == 1
    assert journal.sink.closed


def failure_diagnostics(error: BaseException) -> str:
    """Read notes or Python 3.10 contexts, rejecting accidental context cycles."""
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None:
        assert id(current) not in seen, "cleanup created a cyclic exception context"
        seen.add(id(current))
        messages.append(str(current))
        messages.extend(getattr(current, "__notes__", ()))
        current = current.__context__
    return "\n".join(messages)


def test_real_partial_bind_retains_primary_and_closes_every_returned_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    errors = {
        "a": OSError("engine a close failed"),
        "c": RuntimeError("engine c close failed"),
    }
    built = install_tracking_engines(
        monkeypatch, events, close_errors=errors, invalid_middle_version=True
    )
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    journal.close_error = OSError("journal close failed")
    observed: list[BaseException] = []
    real_bind = Kernel.bind

    def observe_bind(self: Kernel, *args: Any, **kwargs: Any) -> None:
        events.append("bind")
        try:
            real_bind(self, *args, **kwargs)
        except BaseException as exc:
            observed.append(exc)
            raise

    monkeypatch.setattr(Kernel, "bind", observe_bind)
    try:
        with pytest.raises(KernelError) as caught:
            Simulation(tracking_scenario(), journal=journal)
        assert events[:4] == ["build:a", "build:b", "build:c", "bind"]
        assert [engine.partition.id for engine in built] == ["a", "b", "c"]
        # The real bind admitted a's version, rejected b, and never reached c.
        assert [event for event in events if event.startswith("version:")] == [
            "version:a",
            "version:b",
        ]
        assert len(observed) == 1 and caught.value is observed[0]
        assert caught.value.code == "ENGINE_VERSION"
        assert_failed_construction_closed(built, journal, events)
        assert journal.acknowledged_bytes == 0
        diagnostics = failure_diagnostics(caught.value)
        for failure in (*errors.values(), journal.close_error):
            assert str(failure) in diagnostics
    finally:
        Journal.close(journal)


def test_header_append_failure_keeps_identity_despite_all_closers_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    errors = {name: RuntimeError(f"engine {name} close failed") for name in "abc"}
    built = install_tracking_engines(monkeypatch, events, close_errors=errors)
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    journal.close_error = OSError("journal close failed after header rejection")
    primary = OSError("header append failed")
    attempted: list[str] = []

    def failed_append(record: dict[str, Any], **kwargs: Any) -> bytes:
        attempted.append(record["type"])
        raise primary

    monkeypatch.setattr(journal, "append", failed_append)
    try:
        with pytest.raises(OSError) as caught:
            Simulation(tracking_scenario(), journal=journal)
        assert caught.value is primary
        assert attempted == ["header"]
        assert [engine.partition.id for engine in built] == ["a", "b", "c"]
        assert_failed_construction_closed(built, journal, events)
        assert journal.acknowledged_bytes == 0
        assert journal.bytes == b""
        diagnostics = failure_diagnostics(primary)
        for failure in (*errors.values(), journal.close_error):
            assert str(failure) in diagnostics
    finally:
        Journal.close(journal)


@pytest.mark.parametrize("primary_type", [KeyboardInterrupt, SystemExit])
def test_baseexception_factory_and_close_failures_do_not_skip_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_type: type[BaseException],
) -> None:
    events: list[str] = []
    primary = primary_type("factory c interrupted")
    engine_error = KeyboardInterrupt("engine a close interrupted")
    provider_error = SystemExit("provider b close exited")
    built = install_tracking_engines(
        monkeypatch,
        events,
        close_errors={"a": engine_error},
        provider_errors={"b": provider_error},
        factory_error=primary,
    )
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    journal.close_error = ConstructionAbort("journal close aborted")
    try:
        with pytest.raises(primary_type) as caught:
            Simulation(tracking_scenario(), journal=journal)
        assert caught.value is primary
        assert [engine.partition.id for engine in built] == ["a", "b"]
        assert_failed_construction_closed(built, journal, events)
        diagnostics = failure_diagnostics(primary)
        for failure in (engine_error, provider_error, journal.close_error):
            assert str(failure) in diagnostics
    finally:
        Journal.close(journal)


@pytest.mark.parametrize(
    "failure", ["factory-stop", "bind-stop", "factory-value", "factory-scenario"]
)
def test_cleanup_preserves_located_normalization_code_and_original_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    events: list[str] = []
    if failure.endswith("stop"):
        original: BaseException = StopIteration("plugin exhausted")
    elif failure == "factory-value":
        original = KernelError("VALUE_SCHEMA", "plugin configuration rejected")
    else:
        original = ScenarioError("engines.c.config: plugin rejected", code="PLUGIN")
    errors = {name: RuntimeError(f"engine {name} cleanup failed") for name in "abc"}
    built = install_tracking_engines(
        monkeypatch,
        events,
        close_errors=errors,
        factory_error=None if failure == "bind-stop" else original,
    )
    if failure == "bind-stop":
        def exhausted_bind(self: Kernel, *args: Any, **kwargs: Any) -> None:
            raise original

        monkeypatch.setattr(Kernel, "bind", exhausted_bind)
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    journal.close_error = OSError("journal cleanup failed")
    try:
        with pytest.raises(ScenarioError) as caught:
            Simulation(tracking_scenario(), journal=journal)
        if failure == "factory-scenario":
            assert caught.value is original
        else:
            assert caught.value.__cause__ is original
        location = "scenario.bind:" if failure == "bind-stop" else "engines.c.config:"
        assert str(caught.value).startswith(location)
        assert caught.value.code == getattr(original, "code", None)
        expected_ids = ["a", "b", "c"] if failure == "bind-stop" else ["a", "b"]
        assert [engine.partition.id for engine in built] == expected_ids
        assert_failed_construction_closed(built, journal, events)
        diagnostics = failure_diagnostics(caught.value)
        for name in expected_ids:
            assert str(errors[name]) in diagnostics
        assert str(journal.close_error) in diagnostics
    finally:
        Journal.close(journal)


def test_python310_cleanup_context_fallback_is_acyclic_and_keeps_prior_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    prior = LookupError("context predating cleanup")
    primary = NoNotesFailure("factory c failed without add_note")
    primary.__context__ = prior
    errors = {
        "a": RuntimeError("engine a cleanup failed"),
        "b": OSError("engine b cleanup failed"),
    }
    built = install_tracking_engines(
        monkeypatch, events, close_errors=errors, factory_error=primary
    )
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    journal.close_error = RuntimeError("journal cleanup failed")
    try:
        with pytest.raises(NoNotesFailure) as caught:
            Simulation(tracking_scenario(), journal=journal)
        assert caught.value is primary
        assert not getattr(primary, "__notes__", ())
        assert_failed_construction_closed(built, journal, events)
        diagnostics = failure_diagnostics(primary)
        assert str(prior) in diagnostics
        for failure in (*errors.values(), journal.close_error):
            assert str(failure) in diagnostics
        assert isinstance(primary.__context__, RuntimeError)
        assert primary.__context__ is not journal.close_error
    finally:
        Journal.close(journal)


def test_successful_bind_transfers_cleanup_to_kernel_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    built = install_tracking_engines(monkeypatch, events)
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    simulation: Simulation | None = None
    try:
        simulation = Simulation(tracking_scenario(), journal=journal)
        assert [engine.partition.id for engine in built] == ["a", "b", "c"]
        assert not any(event.startswith("close:") for event in events)
        assert journal.acknowledged_bytes > 0
        simulation.close()
        simulation.close()
        assert all(engine.close_calls == 1 and engine.closed for engine in built)
        assert all(
            engine.provider.close_calls == 1 and engine.provider.closed
            for engine in built
        )
        assert journal.close_calls == 1
        assert journal.sink.closed
    finally:
        if simulation is not None:
            simulation.close()
        Journal.close(journal)


def test_broken_exception_diagnostics_cannot_interrupt_remaining_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnprintableError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("cleanup exception formatter failed")

    class ThrowingNotesError(RuntimeError):
        def add_note(self, note: str) -> None:
            raise ConstructionAbort("custom add_note failed")

    events: list[str] = []
    primary = ThrowingNotesError("factory c failed")
    later_error = RuntimeError("later engine a cleanup failed")
    built = install_tracking_engines(
        monkeypatch,
        events,
        close_errors={"a": later_error, "b": UnprintableError()},
        factory_error=primary,
    )
    journal = TrackingJournal(tmp_path / "journal.jsonl", events)
    journal.close_error = OSError("journal cleanup still attempted")
    try:
        with pytest.raises(ThrowingNotesError) as caught:
            Simulation(tracking_scenario(), journal=journal)
        assert caught.value is primary
        assert [engine.partition.id for engine in built] == ["a", "b"]
        assert_failed_construction_closed(built, journal, events)
        diagnostics = failure_diagnostics(primary)
        assert "UnprintableError" in diagnostics
        assert str(later_error) in diagnostics
        assert str(journal.close_error) in diagnostics
    finally:
        Journal.close(journal)
