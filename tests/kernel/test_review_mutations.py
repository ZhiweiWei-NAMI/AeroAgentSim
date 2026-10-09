"""Control requests cannot publish a stale candidate over an advancing run."""

import threading

import pytest

from aerokernel import (
    BindingManifest,
    CommandRequest,
    Instant,
    Kernel,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    replay,
)
from aerokernel.journal import iter_records
from aerokernel.sdk import ContextEngine


@pytest.mark.parametrize("method", ["submit", "cancel"])
@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_control_candidate_and_publication_serialize_with_run(
    monkeypatch, method, provenance
):
    kernel = Kernel(provenance=provenance)
    kernel.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("C", "command", {"type": "integer"}),)
        ),
        BindingManifest("race", "0"),
        (ContextEngine(Partition("sink", "sink", commands=("C",))),),
    )
    kernel.start()
    command = CommandRequest("C", "sink", Instant(2), 1)
    command_id = kernel.submit(command)
    entered, release, running, finished = (threading.Event() for _ in range(4))
    original = kernel._publish
    errors = []

    def delayed(state, record):
        if record["type"] == ("ingress" if method == "submit" else "cancel"):
            entered.set()
            assert release.wait(3)
        original(state, record)

    monkeypatch.setattr(kernel, "_publish", delayed)

    def control():
        try:
            if method == "submit":
                kernel.submit(command)
            else:
                kernel.cancel(command_id)
        except Exception as exc:
            errors.append(exc)

    def run():
        running.set()
        try:
            kernel.run_until(1)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    requester, runner = threading.Thread(target=control), threading.Thread(target=run)
    try:
        requester.start()
        assert entered.wait(3)
        runner.start()
        assert running.wait(3)
        assert not finished.wait(0.05), "run crossed an unpublished control candidate"
    finally:
        release.set()
        requester.join(3)
        if runner.ident is not None:
            runner.join(3)
        kernel.close()
    assert not requester.is_alive() and not runner.is_alive()
    assert not errors
    assert kernel.sealed_ns == 1
    records = list(iter_records(kernel.journal.bytes))
    assert [row["index"] for row in records] == list(range(len(records)))
    restored = replay(kernel.journal.bytes)
    assert restored.records == kernel.records
    assert restored.sealed_ns == 1
