"""A session owns returned resources even when persistence or finalization fails."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from aerokernel import (
    CommandRequest,
    IngressWait,
    Instant,
    Journal,
    Kernel,
    Partition,
    Stamp,
    Timing,
)
from aerokernel.errors import KernelError
from aerokernel.journal import iter_records
from aerokernel.sdk import ContextEngine
from aerokernel.values import canonical_json, thaw

from aeroagentsim.behaviours import compat
from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.platform import simulation as runtime
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import Scenario, load_scenario
from aeroagentsim.services import feed_index
from aeroagentsim.services.storage import RunStorage


class OwnedProvider:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class OwnedEngine(ContextEngine):
    def __init__(self, identifier: str, *, command: str | None = None) -> None:
        super().__init__(
            Partition(
                identifier,
                identifier,
                commands=() if command is None else (command,),
                timing=Timing() if command is None else Timing("real_time"),
            )
        )
        self.provider = OwnedProvider()
        self.close_calls = 0
        self.close_failure: BaseException | None = None

    def close(self) -> None:
        self.close_calls += 1
        self.provider.close()
        super().close()
        if self.close_failure is not None:
            raise self.close_failure


@dataclass
class Ownership:
    engines: list[OwnedEngine] = field(default_factory=list)
    journals: list[Journal] = field(default_factory=list)
    closed_kernels: list[Kernel] = field(default_factory=list)

    def assert_released(self, *, engines: int = 2) -> None:
        assert len(self.engines) == engines
        assert all(engine.closed and engine.close_calls == 1 for engine in self.engines)
        assert all(engine.provider.close_calls == 1 for engine in self.engines)
        assert len(self.journals) == 1
        assert self.journals[0].sink.closed
        assert len(self.closed_kernels) == 1
        assert self.closed_kernels[0]._closed


@pytest.fixture
def ownership(monkeypatch: pytest.MonkeyPatch) -> Ownership:
    owned = Ownership()
    original_journal = Journal.__init__
    original_close = Kernel.close

    def journal(self: Journal, *args: Any, **kwargs: Any) -> None:
        original_journal(self, *args, **kwargs)
        owned.journals.append(self)

    def build(self: EngineCatalog, plugin: str, context: EngineBuild) -> OwnedEngine:
        engine = OwnedEngine(context.id, command=context.config.get("command"))
        owned.engines.append(engine)
        return engine

    def close(self: Kernel) -> None:
        owned.closed_kernels.append(self)
        original_close(self)

    monkeypatch.setattr(Journal, "__init__", journal)
    monkeypatch.setattr(EngineCatalog, "build", build)
    monkeypatch.setattr(Kernel, "close", close)
    return owned


@pytest.fixture
def owned_scenario() -> Scenario:
    original = load_scenario(Path("scenarios/realtime-streams.yaml"))
    document = copy.deepcopy(original.document)
    document["engines"] = {
        name: {"plugin": "test-owned", "config": {}} for name in ("a", "z")
    }
    document["bindings"] = {"lifecycle": []}
    document.pop("ingress_streams", None)
    return load_scenario(document, base=original.base)


def diagnostics(failure: BaseException) -> str:
    """Inspect public exception notes/context, independently of runtime formatting."""
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = failure
    while current is not None:
        assert id(current) not in seen, "exception context must be acyclic"
        seen.add(id(current))
        messages.append(str(current))
        messages.extend(getattr(current, "__notes__", ()))
        current = current.__context__
    return "; ".join(messages)


@pytest.mark.parametrize(
    "stage",
    [
        "configuration",
        "serialization",
        "metadata_read",
        "manifest",
        "run_metadata",
        "behaviour_manifest",
        "behaviour_ir",
        "compat_compile",
        "compat_ir",
    ],
)
def test_every_post_simulation_artifact_failure_releases_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
    stage: str,
) -> None:
    # Factory injection isolates ownership from behaviour/compiler validation.
    scenario = replace(
        owned_scenario,
        engines={
            **owned_scenario.engines,
            "b": {
                "plugin": "behaviour",
                "config": {"packages": [{"package_id": "test-package"}]},
            },
            "c": {"plugin": "workflow", "config": {}},
        },
    )
    directory = tmp_path / "run"
    prepared = stage == "configuration"
    if prepared:
        RunStorage(directory).prepare(scenario)
    primary = OSError(f"injected {stage}")
    original_atomic = RunStorage._atomic
    original_metadata = RunStorage.metadata
    original_write = Path.write_bytes
    original_json = runtime.canonical_json
    manifest_writes = 0

    def atomic(self: RunStorage, name: str, value: Any) -> None:
        nonlocal manifest_writes
        if ownership.engines:
            if name == "manifest.json":
                manifest_writes += 1
            if (
                (stage == "configuration" and name == "scenario.json")
                or (
                    stage == "manifest"
                    and name == "manifest.json"
                    and manifest_writes == 1
                )
                or (
                    stage == "behaviour_manifest"
                    and name == "manifest.json"
                    and manifest_writes == 2
                )
                or (stage == "run_metadata" and name == "run-metadata.json")
            ):
                raise primary
        original_atomic(self, name, value)

    def metadata(self: RunStorage) -> dict[str, Any]:
        if stage == "metadata_read" and ownership.engines:
            raise primary
        return original_metadata(self)

    def write(path: Path, data: bytes) -> int:
        if (stage == "behaviour_ir" and path.name == "behaviour.ir.json") or (
            stage == "compat_ir" and path.name == "behaviour.compat.ir.json"
        ):
            raise primary
        return original_write(path, data)

    def serialize(*args: Any, **kwargs: Any) -> bytes:
        if stage == "serialization" and ownership.engines:
            raise primary
        return original_json(*args, **kwargs)

    def compile_workflow(config: dict[str, Any]) -> dict[str, Any]:
        if stage == "compat_compile":
            raise primary
        return {"format": "test.compat/v1"}

    monkeypatch.setattr(RunStorage, "_atomic", atomic)
    monkeypatch.setattr(RunStorage, "metadata", metadata)
    monkeypatch.setattr(Path, "write_bytes", write)
    monkeypatch.setattr(runtime, "canonical_json", serialize)
    monkeypatch.setattr(compat, "compile_workflow", compile_workflow)
    with pytest.raises(OSError) as caught:
        RunSession(scenario, directory, prepared=prepared, provenance="full")
    assert caught.value is primary
    ownership.assert_released(engines=4)
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] != "completed"


def test_constructor_primary_survives_cleanup_index_and_status_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
) -> None:
    primary = OSError("manifest write failed")
    original_atomic = RunStorage._atomic

    def atomic(self: RunStorage, name: str, value: Any) -> None:
        if ownership.engines and name == "manifest.json":
            for engine in ownership.engines:
                engine.close_failure = RuntimeError(
                    f"close {engine.partition.id} failed"
                )
            raise primary
        original_atomic(self, name, value)

    def index(self: RunStorage, **kwargs: Any) -> None:
        raise OSError("index failed")

    def status(self: RunStorage, value: str, **kwargs: Any) -> None:
        raise OSError("fault status failed")

    monkeypatch.setattr(RunStorage, "_atomic", atomic)
    monkeypatch.setattr(RunStorage, "index", index)
    monkeypatch.setattr(RunStorage, "status", status)
    with pytest.raises(OSError) as caught:
        RunSession(owned_scenario, tmp_path / "run")
    assert caught.value is primary
    ownership.assert_released()
    text = diagnostics(primary)
    for detail in (
        "close a failed", "close z failed", "index failed", "fault status failed"
    ):
        assert detail in text


@pytest.mark.parametrize("secondary_stage", ["index", "metadata", "status"])
def test_close_keeps_cleanup_primary_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
    secondary_stage: str,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    primary = OSError("primary cleanup failure")
    cause = RuntimeError("original cleanup cause")
    primary.__cause__ = cause
    original_close = session.simulation.close

    def close() -> None:
        original_close()
        raise primary

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"secondary {secondary_stage} failure")

    monkeypatch.setattr(session.simulation, "close", close)
    monkeypatch.setattr(session.storage, secondary_stage, fail)
    with pytest.raises(OSError) as caught:
        session.close()
    assert caught.value is primary
    assert primary.__cause__ is cause
    assert f"secondary {secondary_stage} failure" in diagnostics(primary)
    assert session.closed
    ownership.assert_released()
    session.close()
    ownership.assert_released()


def test_index_only_close_failure_remains_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    primary = OSError("index only failure")

    def index(**kwargs: Any) -> None:
        raise primary

    monkeypatch.setattr(session.storage, "index", index)
    with pytest.raises(OSError) as caught:
        session.close()
    assert caught.value is primary
    assert session.closed
    ownership.assert_released()
    session.close()
    ownership.assert_released()


@pytest.mark.parametrize("stage", ["status", "index"])
def test_start_artifact_failure_closes_session_without_masking_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
    stage: str,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    primary = OSError(f"start {stage} failure")
    original_status = session.storage.status

    def status(value: str, **kwargs: Any) -> None:
        if value == "faulted":
            raise RuntimeError("secondary fault status failure")
        if stage == "status":
            raise primary
        original_status(value, **kwargs)

    def index(**kwargs: Any) -> None:
        raise primary

    monkeypatch.setattr(session.storage, "status", status)
    if stage == "index":
        monkeypatch.setattr(session.storage, "index", index)
    with pytest.raises(OSError) as caught:
        session.start()
    assert caught.value is primary
    assert "secondary fault status failure" in diagnostics(primary)
    assert session.closed
    ownership.assert_released()


@pytest.mark.parametrize("operation", ["start", "advance", "timeout"])
def test_execution_primary_survives_all_finalization_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
    operation: str,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    if operation != "start":
        session.start()
    primary = (
        KernelError(
            "WATERMARK_TIMEOUT", "input timed out", stream_ids=("source",), target_ns=1
        )
        if operation == "timeout"
        else RuntimeError("execution failed")
    )
    original_close = session.simulation.close
    original_status = session.storage.status
    statuses: list[str] = []

    def execute(*args: Any, **kwargs: Any) -> Any:
        raise primary

    def close() -> None:
        original_close()
        raise OSError("cleanup failed")

    def index(**kwargs: Any) -> None:
        raise OSError("index failed")

    def status(value: str, **kwargs: Any) -> None:
        statuses.append(value)
        if value != "running":
            raise OSError("terminal status failed")
        original_status(value, **kwargs)

    monkeypatch.setattr(
        session.simulation, "start" if operation == "start" else "run_until", execute
    )
    monkeypatch.setattr(session.simulation, "close", close)
    monkeypatch.setattr(session.storage, "index", index)
    monkeypatch.setattr(session.storage, "status", status)
    with pytest.raises(type(primary)) as caught:
        session.start() if operation == "start" else session.run_until(1)
    assert caught.value is primary
    text = diagnostics(primary)
    for detail in ("cleanup failed", "index failed", "terminal status failed"):
        assert detail in text
    assert statuses[-1] == ("input_timeout" if operation == "timeout" else "faulted")
    assert session.closed
    ownership.assert_released()


@pytest.mark.parametrize("operation", ["submit", "watermark", "progress", "wait"])
def test_publication_failure_after_start_releases_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
    operation: str,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    session.start()
    primary = OSError("publication index failed")

    def index(**kwargs: Any) -> None:
        raise primary

    def accepted(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(session.storage, "index", index)
    monkeypatch.setattr(session.simulation, "submit_live", accepted)
    monkeypatch.setattr(session.simulation, "advance_watermark", accepted)
    monkeypatch.setattr(session.simulation, "advance_source_progress", accepted)
    with pytest.raises(OSError) as caught:
        if operation == "submit":
            session.submit_live(
                "a",
                CommandRequest("aas.stream.observe", "a", Instant(0), {"value": 1.0}),
                Stamp("canonical", 0, 1, "canonical"),
            )
        elif operation == "watermark":
            session.advance_watermark(1)
        elif operation == "progress":
            session.advance_source_progress(Stamp("canonical", 1, 1, "canonical"))
        else:
            session.report_wait(None)
    assert caught.value is primary
    assert session.closed
    ownership.assert_released()


def test_timeout_escaping_context_keeps_input_timeout_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    primary = KernelError(
        "WATERMARK_TIMEOUT", "input timed out", stream_ids=("source",), target_ns=1
    )

    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise primary

    monkeypatch.setattr(session.simulation, "run_until", timeout)
    with pytest.raises(KernelError) as caught:
        with session:
            session.run_until(1)
    assert caught.value is primary
    assert session.closed
    ownership.assert_released()
    metadata = session.storage.metadata()
    assert metadata["status"] == "input_timeout"
    assert metadata["waiting"] == {"stream_ids": ["source"], "at_ns": "1"}


class LegacyFailure(RuntimeError):
    # Exercise the Python 3.10 context fallback even on a newer interpreter.
    add_note = None


@pytest.mark.parametrize(
    "failure_type", [RuntimeError, LegacyFailure, KeyboardInterrupt]
)
def test_context_body_primary_survives_cleanup_and_marks_faulted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
    failure_type: type[BaseException],
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    session.start()
    primary = failure_type("context body failed")
    cause = ValueError("original cause")
    original_context = ValueError("original context")
    primary.__cause__ = cause
    primary.__context__ = original_context
    for engine in ownership.engines:
        engine.close_failure = RuntimeError(f"close {engine.partition.id} failed")
    with pytest.raises(failure_type) as caught:
        with session:
            raise primary
    assert caught.value is primary
    assert primary.__cause__ is cause
    text = diagnostics(primary)
    assert "close a failed" in text and "close z failed" in text
    assert "original context" in text
    ownership.assert_released()
    metadata = session.storage.metadata()
    assert metadata["status"] == "faulted"
    assert "context body failed" in metadata["error"]
    assert "close a failed" in metadata["error"]
    assert "close z failed" in metadata["error"]


def test_close_inside_unrelated_handler_preserves_legacy_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
) -> None:
    session = RunSession(owned_scenario, tmp_path / "run")
    primary = LegacyFailure("cleanup primary")
    original_context = ValueError("original context")
    primary.__context__ = original_context
    original_close = session.simulation.close

    def close() -> None:
        original_close()
        context = primary.__context__
        try:
            raise primary
        except BaseException:
            # Like Kernel.close, preserve its own diagnostics before returning.
            primary.__context__ = context
            raise

    def index(**kwargs: Any) -> None:
        raise OSError("secondary index failure")

    monkeypatch.setattr(session.simulation, "close", close)
    monkeypatch.setattr(session.storage, "index", index)
    try:
        raise RuntimeError("unrelated outer handler")
    except RuntimeError:
        with pytest.raises(LegacyFailure) as caught:
            session.close()
    assert caught.value is primary
    text = diagnostics(primary)
    assert "secondary index failure" in text
    assert "original context" in text
    ownership.assert_released()


def test_close_preserves_exception_that_forbids_context_restoration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
) -> None:
    class FixedContextFailure(RuntimeError):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "__context__":
                raise RuntimeError("context is not writable")
            super().__setattr__(name, value)

    session = RunSession(owned_scenario, tmp_path / "run")
    primary = FixedContextFailure("cleanup primary")
    original_close = session.simulation.close

    def close() -> None:
        original_close()
        raise primary

    monkeypatch.setattr(session.simulation, "close", close)
    with pytest.raises(FixedContextFailure) as caught:
        session.close()
    assert caught.value is primary
    ownership.assert_released()


@pytest.mark.parametrize("owner", ["simulation", "session"])
@pytest.mark.parametrize("level", ["lean", "full"])
@pytest.mark.parametrize("codec", ["json", "positional-deflate"])
def test_provenance_override_matches_effective_configuration_and_historical_header(
    tmp_path: Path,
    owned_scenario: Scenario,
    ownership: Ownership,
    owner: str,
    level: str,
    codec: str,
) -> None:
    authored = "full" if level == "lean" else "lean"
    scenario = replace(
        owned_scenario,
        provenance=authored,
        document={**owned_scenario.document, "provenance": authored},
    )
    original = copy.deepcopy(scenario.document)
    directory = tmp_path / "run"
    if owner == "session":
        session = RunSession(scenario, directory, provenance=level, journal_codec=codec)
        simulation = session.simulation
        close = session.close
    else:
        directory.mkdir()
        simulation = Simulation(
            scenario,
            journal=Journal(directory / "journal.jsonl", codec=codec),
            provenance=level,
        )
        close = simulation.close
    try:
        assert scenario.document == original
        assert scenario.provenance == authored
        assert simulation.scenario.provenance == level
        assert simulation.scenario.document["provenance"] == level
        assert simulation.kernel.provenance == level
        assert thaw(simulation.kernel.configuration) == {
            "scenario": simulation.scenario.document
        }
        header = simulation.kernel.header
        assert header["configuration"]["scenario"] == simulation.scenario.document
        if level == "full":
            # Historical full omission is intentional.
            assert "provenance" not in header
            expected_version = (1, 2) if codec == "json" else (2, 0)
        else:
            assert header["provenance"] == "lean"
            expected_version = (1, 4) if codec == "json" else (2, 1)
        assert (header["major"], header["minor"]) == expected_version
        assert ("codec" in header) == (codec == "positional-deflate")
        assert "run_metadata" not in header and "contract" not in header
    finally:
        close()
    ownership.assert_released()
    recorded = next(iter_records(directory / "journal.jsonl"))
    assert recorded == simulation.kernel.header
    if owner == "session":
        document = (directory / "scenario.json").read_bytes()
        digest = hashlib.sha256(document).hexdigest()
        assert json.loads(document) == simulation.scenario.document
        assert session.storage.metadata()["configuration_digest"] == digest
        pins = json.loads((directory / "run-metadata.json").read_text())
        assert pins == {
            "contract": "aeroagentsim.run-metadata/v1",
            "provenance": level,
            "configuration_digest": digest,
        }


@pytest.mark.parametrize("level", ["lean", "full"])
def test_prepared_override_repins_snapshot_digest_and_preserves_host_metadata(
    tmp_path: Path,
    owned_scenario: Scenario,
    ownership: Ownership,
    level: str,
) -> None:
    authored = "full" if level == "lean" else "lean"
    scenario = replace(
        owned_scenario,
        provenance=authored,
        document={**owned_scenario.document, "provenance": authored},
    )
    original = copy.deepcopy(scenario.document)
    directory = tmp_path / "run"
    storage = RunStorage(directory)
    storage.prepare(scenario)
    storage._atomic(
        "manifest.json",
        {**storage.metadata(), "template": "kept", "studio": {"draft": "original"}},
    )
    with RunSession(scenario, directory, prepared=True, provenance=level) as session:
        effective = canonical_json(session.scenario.document)
        assert (directory / "scenario.json").read_bytes() == effective
        manifest = session.storage.metadata()
        assert manifest["configuration_digest"] == hashlib.sha256(effective).hexdigest()
        assert manifest["provenance"] == level
        assert manifest["template"] == "kept"
        assert manifest["studio"] == {"draft": "original"}
        assert manifest["status"] == "created"
        header = session.simulation.kernel.header
        assert header["configuration"]["scenario"] == json.loads(effective)
    assert scenario.document == original
    assert (directory / "scenario.yaml").read_text() == scenario.source
    ownership.assert_released()


def test_prepared_default_does_not_rewrite_effective_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_scenario: Scenario,
    ownership: Ownership,
) -> None:
    directory = tmp_path / "run"
    RunStorage(directory).prepare(owned_scenario)
    original = (directory / "scenario.json").read_bytes()
    original_atomic = RunStorage._atomic

    def atomic(self: RunStorage, name: str, value: Any) -> None:
        assert name != "scenario.json", "no override must not repin the snapshot"
        original_atomic(self, name, value)

    monkeypatch.setattr(RunStorage, "_atomic", atomic)
    with RunSession(owned_scenario, directory, prepared=True):
        assert (directory / "scenario.json").read_bytes() == original
    ownership.assert_released()


@pytest.fixture
def live_owned_scenario() -> Scenario:
    authored = load_scenario(
        Path(__file__).resolve().parents[2] / "scenarios/realtime-streams.yaml"
    )
    document = copy.deepcopy(authored.document)
    document["bindings"]["commands"] = []
    for stream in document["ingress_streams"]:
        stream["initial_watermark_ns"] = 0
    document["outputs"]["durability"] = "fsync"
    return load_scenario(document, base=authored.base)


def invoke_live_operation(session: RunSession, operation: str) -> None:
    if operation == "submit":
        session.submit_live(
            "fast",
            CommandRequest(
                "aas.stream.observe",
                "fast",
                Instant(50_000_000),
                {"value": 21.5},
                idempotency_key="failed-admission",
            ),
            Stamp("fast-clock", 50_000_000, 1, "fast-ns"),
            stream_id="fast-source",
        )
    elif operation == "watermark":
        session.advance_watermark(1_000_000, stream_id="fast-source")
    else:
        assert operation == "progress"
        # The pinned 20ms lateness bound makes this a 1ms closed prefix.
        session.advance_source_progress(
            Stamp("fast-clock", 21_000_000, 1, "fast-ns"), stream_id="fast-source"
        )


@pytest.mark.parametrize("operation", ["submit", "watermark", "progress"])
def test_real_live_wal_failure_faults_session_without_ack_or_frontier_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_owned_scenario: Scenario,
    ownership: Ownership,
    operation: str,
) -> None:
    session = RunSession(live_owned_scenario, tmp_path / "run")
    try:
        session.start()
        kernel = session.simulation.kernel
        journal = kernel.journal
        assert journal.durability == "fsync"
        prefix = kernel.acknowledged_prefix
        acknowledged = journal.bytes
        records = kernel.records
        cut = kernel.view().cut
        sealed = kernel.sealed_ns
        watermarks = dict(kernel.ingress_watermarks)
        frontiers = {
            engine.partition.id: (engine.logical, engine.native_ns)
            for engine in ownership.engines
        }
        descriptor = journal.sink.fileno()
        original_close = journal.close
        journal_closes: list[Journal] = []
        sync_calls: list[int] = []
        failure = OSError(f"{operation} WAL fsync failed")

        def close_journal() -> None:
            journal_closes.append(journal)
            original_close()

        def fail_sync(fd: int) -> None:
            sync_calls.append(fd)
            raise failure

        monkeypatch.setattr(journal, "close", close_journal)
        # Header, construction artifacts and start all succeeded with real fsync.
        monkeypatch.setattr("aerokernel.journal.os.fsync", fail_sync)
        with pytest.raises(KernelError, match="JOURNAL_IO") as caught:
            invoke_live_operation(session, operation)
        assert caught.value.code == "JOURNAL_IO"
        assert caught.value.__cause__ is failure
        assert journal.failed
        assert session.closed
        assert kernel.acknowledged_prefix == prefix
        assert journal.bytes == acknowledged
        assert kernel.records == records
        assert kernel.view().cut == cut
        assert kernel.sealed_ns == sealed
        assert dict(kernel.ingress_watermarks) == watermarks
        assert {
            engine.partition.id: (engine.logical, engine.native_ns)
            for engine in ownership.engines
        } == frontiers
        # Real write/flush happened, but its visible tail was never acknowledged.
        assert (session.storage.directory / "journal.jsonl").stat().st_size > prefix[0]
        assert sync_calls == [descriptor]
        assert journal_closes == [journal]
        assert session.storage.metadata()["status"] == "faulted"
        ownership.assert_released()
        session.close()
        ownership.assert_released()
        assert journal_closes == [journal]
        assert sync_calls == [descriptor]
    finally:
        session.close()


@pytest.mark.parametrize("operation", ["submit", "watermark", "progress"])
@pytest.mark.parametrize("fail_final_index", [False, True])
@pytest.mark.parametrize("sidecar", [False, True])
def test_fault_finalization_and_status_index_only_the_kernel_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_owned_scenario: Scenario,
    ownership: Ownership,
    operation: str,
    fail_final_index: bool,
    sidecar: bool,
) -> None:
    directory = tmp_path / "run"
    session = RunSession(live_owned_scenario, directory)
    try:
        session.start()
        kernel = session.simulation.kernel
        # Leave one real ACK unindexed: finalization must extend to it, not just
        # freeze the last host index or scan through the failed physical tail.
        kernel.advance_watermark(500_000, stream_id="fast-source")
        prefix = kernel.acknowledged_prefix
        acknowledged = kernel.journal.bytes
        assert session.storage.offset < prefix[0]
        if sidecar:
            session.storage.publish_prefix(*prefix)
        original_index = session.storage.index
        indexed_through: list[int | None] = []
        failure = OSError("injected live fsync failure")

        def fail_sync(fd: int) -> None:
            raise failure

        def index(**kwargs: Any) -> None:
            indexed_through.append(kwargs.get("through_bytes"))
            if fail_final_index and len(indexed_through) == 1:
                raise OSError("injected final index failure")
            original_index(**kwargs)

        monkeypatch.setattr("aerokernel.journal.os.fsync", fail_sync)
        monkeypatch.setattr(session.storage, "index", index)
        with pytest.raises(KernelError, match="JOURNAL_IO") as caught:
            invoke_live_operation(session, operation)
        assert caught.value.__cause__ is failure
        assert kernel.acknowledged_prefix == prefix
        assert indexed_through == [prefix[0], prefix[0]]
        if fail_final_index:
            assert "injected final index failure" in diagnostics(caught.value)
        # Both the normal close pass and the status retry must keep the complete
        # unacknowledged record on disk, but exclude it from committed evidence.
        physical = (directory / "journal.jsonl").read_bytes()
        assert physical[: prefix[0]] == acknowledged
        assert len(physical) > prefix[0]
        assert list(iter_records(directory / "journal.jsonl"))[-1]["index"] == (
            prefix[1] + 1
        )
        storage = RunStorage(directory)
        assert storage.offset == prefix[0]
        assert [entry["index"] for entry in storage.entries] == list(range(prefix[1] + 1))
        assert [record["index"] for record in storage.records(0, prefix[1] + 2)] == (
            list(range(prefix[1] + 1))
        )
        metadata = storage.metadata()
        assert metadata["status"] == "faulted"
        assert metadata["final_cursor"] == prefix[1] + 1
        if sidecar:
            # A later worker-owned status call must honor its published ACK too.
            storage.status("faulted", error=metadata["error"])
            assert RunStorage(directory).offset == prefix[0]
        else:
            assert not (directory / "committed-prefix.json").exists()
        cuts = feed_index.page(directory, 1, prefix[1] + 2)
        assert [cut["commitIndex"] for cut in cuts] == list(range(1, prefix[1] + 1))
        state = feed_index.results_state(directory)
        assert state["results_final"] is True
        assert state["results_cursor"] == prefix[1] + 1
        assert state["final_commit_index"] == prefix[1]
        ownership.assert_released()
        session.close()
        ownership.assert_released()
        assert (directory / "journal.jsonl").read_bytes() == physical
    finally:
        session.close()


@pytest.mark.parametrize("operation", ["submit", "watermark", "progress"])
@pytest.mark.parametrize("stage", ["write", "flush", "fsync"])
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_live_io_abort_taints_closes_and_preserves_the_original_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_owned_scenario: Scenario,
    ownership: Ownership,
    operation: str,
    stage: str,
    failure_type: type[BaseException],
) -> None:
    directory = tmp_path / "run"
    session = RunSession(live_owned_scenario, directory)
    try:
        session.start()
        # Pending ingress makes Kernel.close attempt run_stop. Taint must block
        # that append rather than committing anything after the uncertain write.
        session.submit_live(
            "slow",
            CommandRequest(
                "aas.stream.observe", "slow", Instant(50_000_000), {"value": 18.0}
            ),
            Stamp("slow-clock", 24_999_500, 1, "slow-ticks"),
            stream_id="slow-source",
        )
        kernel = session.simulation.kernel
        journal = kernel.journal
        prefix = kernel.acknowledged_prefix
        acknowledged = journal.bytes
        records = kernel.records
        watermarks = dict(kernel.ingress_watermarks)
        primary = failure_type(f"{operation} {stage} interrupted")
        cause = RuntimeError("original abort cause")
        primary.__cause__ = cause
        for engine in ownership.engines:
            engine.close_failure = RuntimeError(f"close {engine.partition.id} failed")
        original_io = os.fsync if stage == "fsync" else getattr(journal.sink, stage)
        original_flush = journal.sink.flush
        original_append = journal.append
        interrupted = False
        appends: list[tuple[str, bool]] = []

        def interrupt(*args: Any, **kwargs: Any) -> Any:
            nonlocal interrupted
            result = original_io(*args, **kwargs)
            if not interrupted:
                interrupted = True
                if stage == "write":
                    original_flush()
                raise primary
            return result

        def append(record: dict[str, Any], **kwargs: Any) -> bytes:
            appends.append((record["type"], journal.failed))
            return original_append(record, **kwargs)

        monkeypatch.setattr(journal, "append", append)
        if stage == "fsync":
            monkeypatch.setattr("aerokernel.journal.os.fsync", interrupt)
        else:
            monkeypatch.setattr(journal.sink, stage, interrupt)
        with pytest.raises(failure_type) as caught:
            invoke_live_operation(session, operation)
        assert caught.value is primary
        assert primary.__cause__ is cause
        assert interrupted
        assert session._failure is primary
        assert session.closed and journal.failed
        assert kernel.acknowledged_prefix == prefix
        assert journal.bytes == acknowledged
        assert kernel.records == records
        assert dict(kernel.ingress_watermarks) == watermarks
        assert len(appends) == 2
        assert appends[0][1] is False
        assert appends[1] == ("run_stop", True)
        text = diagnostics(primary)
        for detail in ("JOURNAL_TAINTED", "close fast failed", "close slow failed"):
            assert detail in text
        physical = (directory / "journal.jsonl").read_bytes()
        assert physical[: prefix[0]] == acknowledged
        assert len(physical) > prefix[0]
        assert RunStorage(directory).offset == prefix[0]
        metadata = session.storage.metadata()
        assert metadata["status"] == "faulted"
        assert metadata["final_cursor"] == prefix[1] + 1
        assert str(primary) in metadata["error"]
        ownership.assert_released()
        with pytest.raises(KernelError, match="JOURNAL_TAINTED"):
            original_append({"type": "must-not-write"})
        with pytest.raises(RuntimeError, match="started open run session"):
            invoke_live_operation(session, operation)
        session.close()
        ownership.assert_released()
        assert (directory / "journal.jsonl").read_bytes() == physical
    finally:
        session.close()


@pytest.mark.parametrize("operation", ["submit", "watermark", "progress"])
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit, ValueError])
def test_live_path_abort_is_terminal_but_validation_rejection_is_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_owned_scenario: Scenario,
    ownership: Ownership,
    operation: str,
    failure_type: type[BaseException],
) -> None:
    session = RunSession(live_owned_scenario, tmp_path / "run")
    try:
        session.start()
        kernel = session.simulation.kernel
        journal = kernel.journal
        prefix = kernel.acknowledged_prefix
        acknowledged = journal.bytes
        primary = failure_type("live policy path interrupted or rejected")

        def reject(*args: Any, **kwargs: Any) -> Any:
            raise primary

        # Inject before Journal.append, independently of its failed flag.
        with monkeypatch.context() as patch:
            patch.setattr(kernel, "_policy", reject)
            with pytest.raises(failure_type) as caught:
                invoke_live_operation(session, operation)
        assert caught.value is primary
        assert kernel.acknowledged_prefix == prefix
        assert journal.bytes == acknowledged
        if isinstance(primary, Exception):
            assert not session.closed and not journal.failed
            assert not journal.sink.closed
            assert not ownership.closed_kernels
            assert all(engine.close_calls == 0 for engine in ownership.engines)
            assert all(engine.provider.close_calls == 0 for engine in ownership.engines)
            invoke_live_operation(session, operation)
            assert kernel.acknowledged_prefix[0] > prefix[0]
            assert not session.closed and not journal.failed
        else:
            assert session.closed and journal.failed
            assert session._failure is primary
            assert session.storage.metadata()["status"] == "faulted"
            ownership.assert_released()
    finally:
        session.close()
    ownership.assert_released()


@pytest.mark.parametrize(
    ("rejection", "code"),
    [
        ("request", "VALUE_SCHEMA"),
        ("mapping", "CLOCK_MAPPING"),
        ("watermark", "INGRESS_WATERMARK"),
    ],
)
def test_ordinary_live_rejection_keeps_untainted_session_open_and_usable(
    tmp_path: Path,
    live_owned_scenario: Scenario,
    ownership: Ownership,
    rejection: str,
    code: str,
) -> None:
    session = RunSession(live_owned_scenario, tmp_path / "run")
    try:
        session.start()
        session.advance_watermark(1_000_000, stream_id="fast-source")
        kernel = session.simulation.kernel
        journal = kernel.journal
        prefix = kernel.acknowledged_prefix
        acknowledged = journal.bytes
        records = kernel.records
        watermarks = dict(kernel.ingress_watermarks)
        with pytest.raises(KernelError) as caught:
            if rejection == "request":
                session.submit_live(
                    "fast",
                    CommandRequest(
                        "aas.stream.observe",
                        "fast",
                        Instant(50_000_000),
                        {"value": "not-a-number"},
                    ),
                    Stamp("fast-clock", 50_000_000, 1, "fast-ns"),
                    stream_id="fast-source",
                )
            elif rejection == "mapping":
                session.advance_source_progress(
                    Stamp("canonical", 21_000_000, 1, "canonical"),
                    stream_id="fast-source",
                )
            else:
                session.advance_watermark(0, stream_id="fast-source")
        assert caught.value.code == code
        assert not journal.failed
        assert not journal.sink.closed
        assert not session.closed
        assert kernel.acknowledged_prefix == prefix
        assert journal.bytes == acknowledged
        assert kernel.records == records
        assert dict(kernel.ingress_watermarks) == watermarks
        assert not ownership.closed_kernels
        assert all(engine.close_calls == 0 for engine in ownership.engines)
        assert all(engine.provider.close_calls == 0 for engine in ownership.engines)

        receipt = session.submit_live(
            "fast",
            CommandRequest(
                "aas.stream.observe",
                "fast",
                Instant(50_000_000),
                {"value": 21.5},
                idempotency_key="after-rejection",
            ),
            Stamp("fast-clock", 50_000_000, 1, "fast-ns"),
            stream_id="fast-source",
        )
        assert receipt.disposition == "accepted" and receipt.command_id is not None
        session.advance_source_progress(
            Stamp("fast-clock", 22_000_000, 1, "fast-ns"), stream_id="fast-source"
        )
        session.advance_watermark(2_000_000, stream_id="slow-source")
        # Exercise real advancement before the queued command's 50ms delivery.
        session.run_until(1_000_000)
        assert session.now_ns == kernel.sealed_ns == 1_000_000
        assert dict(kernel.ingress_watermarks) == {
            "fast-source": 2_000_000,
            "slow-source": 2_000_000,
        }
        assert kernel.acknowledged_prefix[0] > prefix[0]
        assert kernel.acknowledged_prefix[1] > prefix[1]
        assert not journal.failed
        assert not session.closed
        assert not ownership.closed_kernels
    finally:
        session.close()
    ownership.assert_released()
    session.close()
    ownership.assert_released()


@pytest.mark.parametrize("failure_type", [OSError, LegacyFailure])
def test_real_wait_callback_keeps_first_artifact_fault_after_reentrant_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_owned_scenario: Scenario,
    ownership: Ownership,
    failure_type: type[Exception],
) -> None:
    session = RunSession(live_owned_scenario, tmp_path / "run")
    try:
        session.start()
        primary = failure_type("wait callback index failed")
        original_index = session.storage.index
        index_calls = 0
        waits: list[IngressWait | None] = []

        def index_once(**kwargs: Any) -> None:
            nonlocal index_calls
            index_calls += 1
            if index_calls == 1:
                raise primary
            original_index(**kwargs)

        def on_wait(wait: IngressWait | None) -> bool:
            waits.append(wait)
            session.report_wait(wait)
            return True

        # A real run_limit is committed before the zero-watermark wait callback.
        monkeypatch.setattr(session.storage, "index", index_once)
        with pytest.raises(failure_type) as caught:
            session.run_until(1, on_wait=on_wait)
        assert caught.value is primary
        assert len(waits) == 1
        wait = waits[0]
        assert wait is not None and wait.target_ns == 1
        assert wait.stream_ids == ("fast-source", "slow-source")
        assert index_calls >= 2
        assert session.closed
        ownership.assert_released()
        text = diagnostics(primary)
        assert "wait callback index failed" in text
        assert "JOURNAL_IO" in text
        metadata = session.storage.metadata()
        assert metadata["status"] == "faulted"
        assert "wait callback index failed" in metadata["error"]
        assert "JOURNAL_IO" in metadata["error"]
        session.close()
        ownership.assert_released()
    finally:
        session.close()
