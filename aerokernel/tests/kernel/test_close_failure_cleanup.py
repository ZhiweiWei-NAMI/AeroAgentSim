"""Close failures never skip resources or acknowledge an unpublished stop."""

from __future__ import annotations

from pathlib import Path

import pytest

from aerokernel import (
    BindingManifest,
    CommandRequest,
    Instant,
    Journal,
    Kernel,
    KernelError,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
)
from aerokernel.sdk import SimpleEngine


class ClosingEngine(SimpleEngine):
    def __init__(
        self, engine_id: str, calls: list[str], *, commands: tuple[str, ...] = ()
    ) -> None:
        super().__init__(Partition(engine_id, engine_id, commands=commands))
        self.calls = calls
        self.failure: BaseException | None = None

    def close(self) -> None:
        self.calls.append(f"engine:{self.partition.engine_id}")
        if self.failure is not None:
            raise self.failure
        super().close()


class ClosingJournal(Journal):
    def __init__(self, path: Path, calls: list[str]) -> None:
        super().__init__(path, durability="fsync")
        self.calls = calls
        self.failure: BaseException | None = None

    def close(self) -> None:
        self.calls.append("journal")
        super().close()
        if self.failure is not None:
            raise self.failure


@pytest.fixture(params=("lean", "full"))
def closing_run(request, tmp_path):
    calls: list[str] = []
    journal = ClosingJournal(tmp_path / "run.jsonl", calls)
    engines = tuple(ClosingEngine(eid, calls) for eid in ("c", "a", "b"))
    kernel = Kernel(journal=journal, provenance=request.param)
    kernel.bind(MemoryRegistry(()), BindingManifest("run", "epoch"), engines)
    kernel.start()
    kernel.run_until(1, on_boundary=lambda ns: False)
    assert kernel._store.run_target == 1
    assert kernel.sealed_ns == 0
    yield kernel, engines, journal, calls
    # Release the real file even if a regression skips kernel cleanup.
    Journal.close(journal)


def assert_closed_once(kernel, journal, calls) -> None:
    expected = ["engine:a", "engine:b", "engine:c", "journal"]
    assert calls == expected
    assert kernel._closed
    assert journal.sink.closed
    prefix = kernel.acknowledged_prefix
    records = kernel.records
    kernel.close()
    kernel.close()
    assert calls == expected
    assert kernel.acknowledged_prefix == prefix
    assert kernel.records == records


def test_stop_append_io_failure_closes_every_resource(closing_run, monkeypatch):
    kernel, engines, journal, calls = closing_run
    prefix = kernel.acknowledged_prefix
    acknowledged = journal.bytes
    records = kernel.records
    state = kernel._store
    failure = OSError("stop fsync failed")
    sync_calls = []
    notifications = []
    notify = kernel._ingress_condition.notify_all

    def notify_closed():
        notifications.append(kernel._closed)
        notify()

    monkeypatch.setattr(kernel._ingress_condition, "notify_all", notify_closed)

    def fail_sync(fd):
        sync_calls.append(fd)
        raise failure

    monkeypatch.setattr("aerokernel.journal.os.fsync", fail_sync)
    with pytest.raises(KernelError, match="JOURNAL_IO") as caught:
        kernel.close()

    assert caught.value.__cause__ is failure
    assert journal.failed
    assert kernel._store is state
    assert kernel._store.run_target == 1
    assert kernel.records == records
    assert kernel.acknowledged_prefix == prefix
    assert journal.bytes == acknowledged
    assert all(engine.closed for engine in engines)
    assert_closed_once(kernel, journal, calls)
    assert len(sync_calls) == 1
    assert notifications == [True]


@pytest.mark.parametrize("faulted", (False, True))
def test_already_tainted_wal_still_closes_every_resource(
    closing_run, monkeypatch, faulted
):
    kernel, engines, journal, calls = closing_run
    prefix = kernel.acknowledged_prefix
    acknowledged = journal.bytes
    records = kernel.records
    sync_calls = []

    def fail_sync(fd):
        sync_calls.append(fd)
        raise OSError("run-limit fsync failed")

    monkeypatch.setattr("aerokernel.journal.os.fsync", fail_sync)
    with pytest.raises(KernelError, match="JOURNAL_IO"):
        kernel.run_until(2)
    assert journal.failed
    assert not kernel._store.faulted
    kernel._store.faulted = faulted

    if faulted:
        kernel.close()
    else:
        with pytest.raises(KernelError, match="JOURNAL_TAINTED"):
            kernel.close()

    assert kernel.acknowledged_prefix == prefix
    assert journal.bytes == acknowledged
    assert kernel.records == records
    assert kernel._store.run_target == 1
    assert all(engine.closed for engine in engines)
    assert_closed_once(kernel, journal, calls)
    assert len(sync_calls) == 1


@pytest.mark.parametrize("error_type", (KernelError, OSError, KeyboardInterrupt))
@pytest.mark.parametrize("in_handler", (False, True))
def test_stop_error_remains_primary_with_all_cleanup_failures(
    closing_run, monkeypatch, error_type, in_handler
):
    kernel, engines, journal, calls = closing_run
    prefix = kernel.acknowledged_prefix
    acknowledged = journal.bytes
    records = kernel.records
    primary = (
        KernelError("STOP_FAILED", "stop append failed", detail="original")
        if error_type is KernelError
        else error_type("stop append failed")
    )
    cause = OSError("original stop cause")
    context = ValueError("original stop context")
    first = RuntimeError("engine a close failed")
    second = RuntimeError("engine b close failed")
    last = OSError("journal close failed")
    engines[1].failure = first
    engines[2].failure = second
    journal.failure = last
    append_calls = []

    def fail_append(record, **kwargs):
        append_calls.append(record["type"])
        try:
            raise context
        except ValueError:
            raise primary from cause

    monkeypatch.setattr(journal, "append", fail_append)

    def close_with_primary():
        with pytest.raises(error_type) as caught:
            kernel.close()
        assert caught.value is primary

    if in_handler:
        try:
            raise RuntimeError("outer failure")
        except RuntimeError:
            close_with_primary()
    else:
        close_with_primary()

    assert primary.__cause__ is cause
    failures = (("engine:a", first), ("engine:b", second), ("journal", last))
    if isinstance(primary, KernelError):
        assert primary.context["detail"] == "original"
        assert primary.context["cleanup_errors"] == failures
        assert primary.__context__ is context
    else:
        cleanup = primary.__context__
        assert isinstance(cleanup, KernelError)
        assert cleanup.code == "CLOSE_FAILED"
        assert cleanup.context["cleanup_errors"] == failures
        assert cleanup.__context__ is context
    assert engines[0].closed
    assert kernel.acknowledged_prefix == prefix
    assert journal.bytes == acknowledged
    assert kernel.records == records
    assert kernel._store.run_target == 1
    assert_closed_once(kernel, journal, calls)
    assert append_calls == ["run_stop"]


@pytest.mark.parametrize("journal_fails", (False, True))
def test_engine_failures_are_aggregated_without_skipping_journal(
    closing_run, journal_fails
):
    kernel, engines, journal, calls = closing_run
    prefix = kernel.acknowledged_prefix
    first = RuntimeError("engine a close failed")
    second = RuntimeError("engine b close failed")
    engines[1].failure = first
    engines[2].failure = second
    failures = [("engine:a", first), ("engine:b", second)]
    if journal_fails:
        journal.failure = OSError("journal close failed")
        failures.append(("journal", journal.failure))

    with pytest.raises(KernelError, match="CLOSE_FAILED") as caught:
        kernel.close()

    assert caught.value.__cause__ is first
    assert caught.value.context["cleanup_errors"] == tuple(failures)
    assert engines[0].closed
    assert kernel.records[-1]["type"] == "run_stop"
    assert kernel._store.run_target is None
    assert kernel.acknowledged_prefix[0] > prefix[0]
    assert kernel.acknowledged_prefix[1] == prefix[1] + 1
    assert_closed_once(kernel, journal, calls)


def test_journal_only_failure_retains_original_exception(closing_run):
    kernel, engines, journal, calls = closing_run
    failure = OSError("journal close failed")
    journal.failure = failure

    with pytest.raises(OSError) as caught:
        kernel.close()

    assert caught.value is failure
    assert all(engine.closed for engine in engines)
    assert kernel.records[-1]["type"] == "run_stop"
    assert_closed_once(kernel, journal, calls)


def test_engine_interrupt_still_closes_later_engines_and_journal(closing_run):
    kernel, engines, journal, calls = closing_run
    failure = KeyboardInterrupt("engine a interrupted cleanup")
    engines[1].failure = failure

    with pytest.raises(KeyboardInterrupt) as caught:
        kernel.close()

    assert caught.value is failure
    assert engines[0].closed and engines[2].closed
    assert_closed_once(kernel, journal, calls)


def test_journal_interrupt_preserves_earlier_engine_failure(closing_run):
    kernel, engines, journal, calls = closing_run
    first = RuntimeError("engine a close failed")
    failure = KeyboardInterrupt("journal interrupted cleanup")
    engines[1].failure = first
    journal.failure = failure

    with pytest.raises(KeyboardInterrupt) as caught:
        kernel.close()

    assert caught.value is failure
    cleanup = failure.__context__
    assert isinstance(cleanup, KernelError)
    assert cleanup.context["cleanup_errors"] == (("engine:a", first),)
    assert engines[0].closed and engines[2].closed
    assert_closed_once(kernel, journal, calls)


def test_stop_callback_failure_keeps_acknowledged_stop(closing_run):
    kernel, engines, journal, calls = closing_run
    prefix = kernel.acknowledged_prefix
    primary = RuntimeError("stop publication callback failed")
    journal.failure = OSError("journal close failed")

    def fail_callback():
        raise primary

    kernel._host_commit = fail_callback
    with pytest.raises(RuntimeError) as caught:
        kernel.close()

    assert caught.value is primary
    cleanup = primary.__context__
    assert isinstance(cleanup, KernelError)
    assert cleanup.context["cleanup_errors"] == (("journal", journal.failure),)
    assert all(engine.closed for engine in engines)
    assert kernel.records[-1]["type"] == "run_stop"
    assert kernel._store.run_target is None
    assert kernel.acknowledged_prefix[0] > prefix[0]
    assert kernel.acknowledged_prefix[1] == prefix[1] + 1
    assert_closed_once(kernel, journal, calls)


def test_reentrant_close_during_stop_publication_is_noop(closing_run):
    kernel, engines, journal, calls = closing_run
    prefix = kernel.acknowledged_prefix
    kernel._host_commit = kernel.close

    kernel.close()

    assert all(engine.closed for engine in engines)
    assert kernel.records[-1]["type"] == "run_stop"
    assert kernel.acknowledged_prefix[1] == prefix[1] + 1
    assert_closed_once(kernel, journal, calls)


def test_pending_ingress_stop_failure_preserves_command_ack(tmp_path, monkeypatch):
    calls: list[str] = []
    journal = ClosingJournal(tmp_path / "run.jsonl", calls)
    engine = ClosingEngine("engine", calls, commands=("do",))
    kernel = Kernel(journal=journal)
    try:
        kernel.bind(
            MemoryRegistry(
                (), messages=(MessageDescriptor("do", "command", {"type": "integer"}),)
            ),
            BindingManifest("run", "epoch"),
            (engine,),
        )
        kernel.start()
        command_id = kernel.submit(CommandRequest("do", "engine", Instant(3), 1))
        assert kernel._store.run_target is None
        assert command_id in kernel._store.pending_ingress
        prefix = kernel.acknowledged_prefix
        acknowledged = journal.bytes
        records = kernel.records

        def fail_sync(fd):
            raise OSError("pending-ingress stop fsync failed")

        monkeypatch.setattr("aerokernel.journal.os.fsync", fail_sync)
        with pytest.raises(KernelError, match="JOURNAL_IO"):
            kernel.close()
        kernel.close()

        assert calls == ["engine:engine", "journal"]
        assert engine.closed and journal.sink.closed
        assert journal.failed
        assert kernel.acknowledged_prefix == prefix
        assert journal.bytes == acknowledged
        assert kernel.records == records
        assert command_id in kernel._store.pending_ingress
        assert kernel.action(command_id).status == "pending"
    finally:
        Journal.close(journal)


@pytest.mark.parametrize("state", ("unbound", "bound", "idle", "faulted"))
def test_close_without_stop_record_is_idempotent(tmp_path, state):
    calls: list[str] = []
    journal = ClosingJournal(tmp_path / "run.jsonl", calls)
    engine = ClosingEngine("engine", calls)
    kernel = Kernel(journal=journal)
    try:
        if state != "unbound":
            kernel.bind(
                MemoryRegistry(()), BindingManifest("run", "epoch"), (engine,)
            )
        if state in {"idle", "faulted"}:
            kernel.start()
        if state == "faulted":
            kernel.run_until(1, on_boundary=lambda ns: False)
            kernel._store.faulted = True
        acknowledged = journal.bytes
        records = kernel.records if state != "unbound" else ()

        kernel.close()
        kernel.close()

        expected = ["journal"] if state == "unbound" else ["engine:engine", "journal"]
        assert calls == expected
        assert kernel._closed
        assert journal.sink.closed
        assert journal.bytes == acknowledged
        if state != "unbound":
            assert engine.closed
            assert kernel.records == records
    finally:
        Journal.close(journal)
