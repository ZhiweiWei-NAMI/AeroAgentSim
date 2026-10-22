"""Bounded experiment shutdown, actual outcomes and acknowledged-prefix evidence."""

from __future__ import annotations

import copy
import json
import math
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aeroagentsim.services import experiments
from aeroagentsim.services.experiments import ExperimentError, ExperimentManager


class _UncopiableAxis(list[Any]):
    def __iter__(self):
        raise AssertionError("axis copied before count admission")

    def __deepcopy__(self, memo):
        raise AssertionError("axis deep-copied before count admission")


class _Registry:
    """Only orchestration metadata: workers in these tests do not load a scenario."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.applied = 0

    def apply(self, template_id, knobs):
        self.applied += 1
        return SimpleNamespace(
            metadata={"knobs": []},
            document={"run": {"seed": 42, "until_ns": 1, "advance_ns": 1}},
            knobs=dict(knobs),
            base=self.base,
            run_metadata=lambda: {"id": template_id, "title": "Probe"},
        )


@pytest.mark.parametrize(
    ("axis_sizes", "options", "message"),
    [
        ([1], {"jobs": experiments.MAX_JOBS + 1}, "jobs"),
        ([1], {"seeds": experiments.MAX_SEEDS + 1}, "seeds"),
        ([experiments.MAX_CONFIGURATIONS + 1], {}, "configurations"),
        ([17, 17], {}, "configurations"),
        ([experiments.MAX_CONFIGURATIONS], {"seeds": 17}, "total runs"),
        ([1], {"jobs": True}, "jobs"),
        ([1], {"seeds": True}, "seeds"),
    ],
)
def test_counts_rejected_before_axes_copy_product_or_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, axis_sizes, options, message
) -> None:
    registry = _Registry(tmp_path)
    manager = ExperimentManager(tmp_path / "runs", registry)

    def forbidden_product(axes):
        pytest.fail("factorial materialized before count admission")

    monkeypatch.setattr(experiments, "_product", forbidden_product)
    vary = {
        f"axis-{index}": _UncopiableAxis([None] * size)
        for index, size in enumerate(axis_sizes)
    }
    with pytest.raises(ExperimentError, match=message):
        manager.create("probe", vary=vary, **options)
    assert registry.applied == 0
    assert not (tmp_path / "runs").exists()
    manager.close()


def test_closed_manager_rejects_new_admission(tmp_path: Path) -> None:
    registry = _Registry(tmp_path)
    manager = ExperimentManager(tmp_path / "runs", registry)
    manager.close()
    manager.close()
    with pytest.raises(ExperimentError, match="closed"):
        manager.create("probe", vary={})
    assert registry.applied == 0


def test_coordinator_thread_start_failure_is_faulted_and_releases_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoStart:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread budget exhausted")

    monkeypatch.setattr(experiments, "threading", SimpleNamespace(
        Lock=threading.Lock, Event=threading.Event, Thread=NoStart,
    ))
    root = tmp_path / "runs"
    manager = ExperimentManager(root, _Registry(tmp_path))
    entries = []
    save = manager._save

    def observe_save(entry):
        entries.append(entry)
        save(entry)

    monkeypatch.setattr(manager, "_save", observe_save)
    with pytest.raises(ExperimentError, match="thread budget exhausted"):
        manager.create("probe", vary={}, seeds=2)
    assert entries[-1]["done"].is_set() and manager._experiments == {}
    manager.close()
    stored = list((root / ".experiments").glob("*/experiment.json"))
    assert len(stored) == 1
    report = json.loads(stored[0].read_text())
    assert report["status"] == "faulted" and "coordinator start" in report["error"]
    assert report["completed"] == 0 and report["failed"] == 2
    assert all(not run["attempted"] and run["status"] == "interrupted" for run in report["runs"])


def _run_files(path: Path, *, status: str = "running", error=None) -> dict[str, int]:
    """A minimal storage prefix, with complete AND partial unacknowledged tails."""
    path.mkdir(parents=True)
    lines = [json.dumps({"index": index}).encode() + b"\n" for index in range(3)]
    acknowledged = b"".join(lines[:2])
    (path / "journal.jsonl").write_bytes(b"".join(lines) + b'{"index":3')
    prefix = {"bytes": len(acknowledged), "index": 1}
    (path / "committed-prefix.json").write_text(json.dumps(prefix))
    offset = 0
    entries = []
    for index, line in enumerate(lines):
        entries.append({"index": index, "offset": offset, "length": len(line)})
        offset += len(line)
    (path / "index.json").write_text(json.dumps(entries))
    metadata = {
        "id": path.name, "status": status, "kernel_run_id": "probe",
        "configuration_digest": "recorded-configuration", "epoch": "0",
    }
    if error is not None:
        metadata["error"] = error
    if status == "input_timeout":
        metadata["waiting"] = {"stream_ids": ["source"], "at_ns": "10"}
    (path / "manifest.json").write_text(json.dumps(metadata))
    return prefix


def _blocked_cell_worker(arguments, cancelled, output) -> None:
    # A native/model invocation need not observe the cooperative event, and on
    # POSIX may ignore SIGTERM. The kill phase must still reap this child.
    if os.name == "posix":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    path = Path(arguments[2])
    _run_files(path)
    (path / "blocked.ready").write_text(str(os.getpid()))
    threading.Event().wait()


class _TrackedProcess:
    def __init__(self, process, force, reaped) -> None:
        self.process = process
        self.force = force
        self.reaped = reaped
        self.closed = False

    @property
    def terminated(self):
        return self.force.value >= 1

    @property
    def killed(self):
        return self.force.value >= 2

    def start(self):
        self.process.start()

    def is_alive(self):
        return False if self.closed else self.process.is_alive()

    def join(self, timeout=None):
        self.process.join(timeout=timeout)

    def terminate(self):
        self.force.value = max(self.force.value, 1)

    def kill(self):
        # Test cleanup must not kill the subreaper itself and orphan its tree.
        self.force.value = 2

    def close(self):
        self.process.close()
        self.closed = True


class _BlockedContext:
    def __init__(self, context, target=_blocked_cell_worker, *, supervisor=None) -> None:
        self.context = context
        self.target = target
        self.supervisor = supervisor or experiments._cell_supervisor
        self.processes: list[_TrackedProcess] = []

    def RawValue(self, *args):
        return self.context.RawValue(*args)

    def Process(self, **kwargs):
        assert kwargs["target"] is experiments._cell_supervisor
        kwargs["target"] = self.supervisor
        _, *arguments = kwargs["args"]
        kwargs["args"] = (self.target, *arguments)
        force, reaped = arguments[-2:]
        process = _TrackedProcess(self.context.Process(**kwargs), force, reaped)
        self.processes.append(process)
        return process


def _wait_for(predicate, timeout=10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timed out waiting for the test worker handshake")
        time.sleep(0.01)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper regression")
def test_close_kills_blocked_cells_without_backlog_and_preserves_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _BlockedContext(multiprocessing.get_context("spawn"))
    monkeypatch.setattr(
        experiments, "multiprocessing",
        SimpleNamespace(get_context=lambda method: context),
    )
    root = tmp_path / "runs"
    manager = ExperimentManager(root, _Registry(tmp_path))
    created = manager.create("probe", vary={}, seeds=5, jobs=2)
    closer = None
    try:
        _wait_for(lambda: len(list(root.glob("*/blocked.ready"))) == 2)
        assert len(context.processes) == 2  # No third submission behind two blocks.
        before = {
            path.parent: (
                (path.parent / "journal.jsonl").read_bytes(),
                (path.parent / "committed-prefix.json").read_bytes(),
            )
            for path in root.glob("*/blocked.ready")
        }
        started = time.monotonic()
        closer = threading.Thread(target=manager.close, daemon=True)
        closer.start()
        closer.join(timeout=6)
        assert not closer.is_alive(), "close waited forever on a blocked cell"
        assert time.monotonic() - started < 6
        assert len(context.processes) == 2
        assert all(process.closed and not process.is_alive() for process in context.processes)
        assert all(process.terminated for process in context.processes)
        if os.name == "posix":
            assert all(process.killed for process in context.processes)
        final = manager.wait(created["id"])
        assert final["cancellation_policy"]["supervision"] == "linux_subreaper"
        assert final["status"] == "interrupted"
        assert final["completed"] == 0 and final["failed"] == final["total"] == 5
        assert len(final["runs"]) == 5
        attempted = [run for run in final["runs"] if run["attempted"]]
        unsubmitted = [run for run in final["runs"] if not run["attempted"]]
        assert len(attempted) == 2 and len(unsubmitted) == 3
        assert all(run["run"] is None and run["status"] == "interrupted" for run in unsubmitted)
        assert all(run["kpis"] == {} for run in final["runs"])
        assert final["outcome_counts"] == {"interrupted": 5}
        assert len(final["sample_exclusions"]) == 5
        assert all(metric["mean"] is None and metric["n"] == 0
                   for metric in final["table"][0]["metrics"].values())
        for run in attempted:
            path = Path(run["run"])
            journal, ack = before[path]
            assert (path / "journal.jsonl").read_bytes() == journal
            assert (path / "committed-prefix.json").read_bytes() == ack
            assert run["cut"] == json.loads(ack) and run["cursor"] == 2
            assert run["termination_confirmed"] is True
            assert run["supervision"] == "linux_subreaper"
            assert "cleanup_limitations" not in run
            assert [row["index"] for row in json.loads((path / "index.json").read_text())] == [0, 1]
            metadata = json.loads((path / "manifest.json").read_text())
            assert metadata["status"] == "interrupted" and metadata["final_cursor"] == 2
    finally:
        # A failing assertion must not strand the intentionally blocked workers.
        for process in context.processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        if closer is None:
            closer = threading.Thread(target=manager.close, daemon=True)
            closer.start()
        closer.join(timeout=2)


def _supervisor_with_ignored_sigchld(*args) -> None:
    # An embedding host may ignore SIGCHLD; the supervisor must restore waitable
    # children before relying on waitpid for ownership/reaping proof.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    experiments._cell_supervisor(*args)


def _detached_renderer_worker(arguments, cancelled, output) -> None:
    """Exercise BrowserRenderer's real session/pipe setup without Node/Chromium."""
    from aeroagentsim.observations import renderer as rendering

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    path = Path(arguments[2])
    _run_files(path)
    browser_script = (
        "import os, signal, sys, threading\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "threading.Event().wait()\n"
    )
    bridge_script = (
        "import os, signal, subprocess, sys, threading\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], "
        "start_new_session=True)\n"
        "fd = int(os.environ['AEROAGENTSIM_RENDERER_READY_FD'])\n"
        "os.write(fd, b'{\"contract\":\"aeroagentsim.viewer-ready/v1\"}\\n')\n"
        "os.close(fd)\n"
        # Like a bridge stuck awaiting page.evaluate: stdin EOF does not stop it.
        "threading.Event().wait()\n"
    )
    popen = subprocess.Popen

    def start_bridge(command, **kwargs):
        assert kwargs["start_new_session"] is True
        return popen([
            sys.executable, "-c", bridge_script, browser_script, str(path / "browser.pid"),
        ], **kwargs)

    rendering.subprocess.Popen = start_bridge
    os.environ.pop("AEROAGENTSIM_CHROMIUM", None)
    renderer = rendering.BrowserRenderer("http://unused.invalid", node_modules=path)
    bridge = renderer._start()
    browser_file = path / "browser.pid"
    _wait_for(lambda: browser_file.exists() and bool(browser_file.read_text()))
    browser_pid = int(browser_file.read_text())
    (path / "renderer-pids.json").write_text(json.dumps({
        "cell": os.getpid(), "bridge": bridge.pid, "browser": browser_pid,
        "bridge_session": os.getsid(bridge.pid), "browser_session": os.getsid(browser_pid),
    }))
    (path / "blocked.ready").write_text("ready")
    if arguments[0].get("test_exit_without_renderer_cleanup"):
        return  # A dead invocation must not make a still-live helper disappear.
    renderer._read_reply(time.monotonic() + 300, "blocked render")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper regression")
@pytest.mark.parametrize("exit_without_cleanup", [False, True])
def test_close_reaps_separately_sessioned_renderer_and_browser_descendants(
    exit_without_cleanup: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RendererRegistry(_Registry):
        def apply(self, template_id, knobs):
            prepared = super().apply(template_id, knobs)
            prepared.document["test_exit_without_renderer_cleanup"] = exit_without_cleanup
            return prepared

    context = _BlockedContext(
        multiprocessing.get_context("spawn"), _detached_renderer_worker,
        supervisor=_supervisor_with_ignored_sigchld,
    )
    monkeypatch.setattr(experiments, "multiprocessing", SimpleNamespace(get_context=lambda method: context))
    monkeypatch.setattr(experiments, "_CANCEL_GRACE_S", 0.05)
    monkeypatch.setattr(experiments, "_TERMINATE_GRACE_S", 0.05)
    root = tmp_path / "runs"
    manager = ExperimentManager(root, RendererRegistry(tmp_path))
    unrelated = subprocess.Popen([
        sys.executable, "-c", "import threading; threading.Event().wait()",
    ], start_new_session=True)
    closer = None
    try:
        created = manager.create("probe", vary={}, seeds=1)
        _wait_for(lambda: len(list(root.glob("*/blocked.ready"))) == 1)
        path = next(root.glob("*/blocked.ready")).parent
        identities = json.loads((path / "renderer-pids.json").read_text())
        assert identities["bridge_session"] == identities["bridge"]
        assert identities["browser_session"] == identities["browser"]
        assert identities["bridge_session"] != identities["browser_session"]
        before = {name: (path / name).read_bytes()
                  for name in ("journal.jsonl", "committed-prefix.json")}
        closer = threading.Thread(target=manager.close, daemon=True)
        closer.start()
        closer.join(timeout=4)
        assert not closer.is_alive(), "close stranded a renderer/browser subtree"
        result = manager.wait(created["id"])["runs"][0]
        assert result["termination_confirmed"] is True and result["kpis"] == {}
        assert all(process.closed and process.reaped.value for process in context.processes)
        assert all(not Path(f"/proc/{identities[name]}").exists()
                   for name in ("cell", "bridge", "browser"))
        assert unrelated.poll() is None, "another process owner's helper was signalled"
        assert {name: (path / name).read_bytes() for name in before} == before
    finally:
        for process in context.processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        if closer is None:
            closer = threading.Thread(target=manager.close, daemon=True)
            closer.start()
        closer.join(timeout=2)
        unrelated.kill()
        unrelated.wait(timeout=2)


def _supervisor_without_subreaper(worker, arguments, *args) -> None:
    """Apply capability failures inside spawn (parent monkeypatches do not carry)."""
    reason = arguments[0]["test_supervision_failure"]
    with pytest.MonkeyPatch.context() as patch:
        if reason in {"darwin", "win32"}:
            # Do not change the real sys.platform used by multiprocessing spawn.
            patch.setattr(experiments, "sys", SimpleNamespace(platform=reason))
        elif reason == "prctl":
            import ctypes

            patch.setattr(ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(prctl=lambda *a: -1))
        else:
            read_text = Path.read_text

            def without_proc(path, *a, **kw):
                if str(path).startswith("/proc/"):
                    raise OSError("procfs unavailable")
                return read_text(path, *a, **kw)

            patch.setattr(Path, "read_text", without_proc)
        experiments._cell_supervisor(worker, arguments, *args)


def _completed_cell_worker(arguments, cancelled, output) -> None:
    path = Path(arguments[2])
    _run_files(path, status="completed")
    experiments._publish_cell_result(output, {"result": experiments._run_result(path)})


@pytest.mark.parametrize("reason", ["darwin", "win32", "prctl", "proc"])
@pytest.mark.parametrize("blocked", [False, True], ids=["completed", "cancelled"])
def test_unavailable_subreaper_runs_cells_and_cancels_best_effort(
    reason: str, blocked: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if reason in {"prctl", "proc"} and sys.platform != "linux":
        pytest.skip("Linux capability failure regression")

    class FallbackRegistry(_Registry):
        def apply(self, template_id, knobs):
            prepared = super().apply(template_id, knobs)
            prepared.document["test_supervision_failure"] = reason
            return prepared

    context = _BlockedContext(
        multiprocessing.get_context("spawn"),
        _blocked_cell_worker if blocked else _completed_cell_worker,
        supervisor=_supervisor_without_subreaper,
    )
    monkeypatch.setattr(experiments, "multiprocessing", SimpleNamespace(get_context=lambda method: context))
    root = tmp_path / "runs"
    manager = ExperimentManager(root, FallbackRegistry(tmp_path))
    closer = None
    try:
        created = manager.create("probe", vary={}, seeds=1)
        if blocked:
            _wait_for(lambda: bool(list(root.glob("*/blocked.ready"))))
            cell_pid = int(next(root.glob("*/blocked.ready")).read_text())
            closer = threading.Thread(target=manager.close, daemon=True)
            closer.start()
            closer.join(timeout=6)
            assert not closer.is_alive(), "best-effort cancellation stranded the cell"
        else:
            _wait_for(lambda: manager.get(created["id"])["status"] not in {"created", "running"})
        report = manager.wait(created["id"])
        run = report["runs"][0]
        policy = report["cancellation_policy"]
        assert report["status"] == ("interrupted" if blocked else "completed")
        assert run["status"] == ("interrupted" if blocked else "completed")
        assert run["supervision"] == policy["supervision"] == "best_effort"
        assert run["termination_scope"] == policy["termination_scope"] == "cell_process"
        assert "Detached renderer/browser descendants" in run["cleanup_limitations"]
        assert policy["cleanup_limitations"] == run["cleanup_limitations"]
        assert run["termination_confirmed"] is True
        assert all(process.closed and process.reaped.value for process in context.processes)
        if blocked:
            assert all(process.terminated for process in context.processes)
            if os.name == "posix":
                assert all(process.killed for process in context.processes)
                with pytest.raises(ProcessLookupError):
                    os.kill(cell_pid, 0)
        stored = json.loads((root / ".experiments" / created["id"] / "experiment.json").read_text())
        assert stored["runs"][0]["supervision"] == "best_effort"
        assert stored["cancellation_policy"]["supervision"] == "best_effort"
    finally:
        for process in context.processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        if closer is None:
            closer = threading.Thread(target=manager.close, daemon=True)
            closer.start()
        closer.join(timeout=2)


@pytest.mark.parametrize("has_groups", [False, True], ids=["windows", "posix"])
@pytest.mark.parametrize("exits_on_term", [False, True])
def test_best_effort_escalates_process_and_supported_group_without_claiming_tree_cleanup(
    has_groups: bool, exits_on_term: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiprocessing.connection as connection

    calls = []

    class Process:
        pid = 1234
        sentinel = object()
        exited = False

        def start(self):
            calls.append("start")

        def terminate(self):
            calls.append("terminate")
            self.exited = exits_on_term

        def kill(self):
            calls.append("kill")
            self.exited = True

        def join(self):
            assert self.exited
            calls.append("join")

        def close(self):
            calls.append("close")

    process = Process()
    current = SimpleNamespace(daemon=True)
    monkeypatch.setattr(experiments, "multiprocessing", SimpleNamespace(
        current_process=lambda: current,
        get_context=lambda method: SimpleNamespace(Process=lambda **kw: process),
    ))
    monkeypatch.setattr(connection, "wait", lambda *a, **kw: [process.sentinel] if process.exited else [])
    monkeypatch.setattr(experiments, "signal", SimpleNamespace(
        signal=lambda *a: None, SIGTERM=15, SIGINT=2, SIG_IGN=1, SIGKILL=9,
    ))
    fake_os = SimpleNamespace()
    if has_groups:
        fake_os.getpgid = lambda pid: pid
        fake_os.killpg = lambda pid, signum: calls.append(("group", pid, signum))
    monkeypatch.setattr(experiments, "os", fake_os)
    ticks = iter(range(100))
    monkeypatch.setattr(experiments, "time", SimpleNamespace(
        monotonic=lambda: next(ticks) * 0.5, sleep=lambda delay: None,
    ))
    reaped = SimpleNamespace(value=0)
    experiments._best_effort_supervisor(
        None, (), None, "unused", SimpleNamespace(value=1), reaped,
    )
    assert not current.daemon
    assert "terminate" in calls
    if not exits_on_term:
        assert "kill" in calls
    if has_groups:
        assert ("group", process.pid, 15) in calls
        assert ("group", process.pid, 9) in calls
        assert calls.index(("group", process.pid, 9)) < calls.index("join")
    assert calls[-2:] == ["join", "close"] and reaped.value == 1


@pytest.mark.parametrize("mode", ["linux_subreaper", "best_effort"])
def test_supervision_mode_is_persisted_in_run_manifest(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_session(monkeypatch)
    path = tmp_path / "run"
    metadata = experiments._supervision_metadata(mode)
    experiments._child_run(
        {"run": {}}, str(tmp_path), str(path), {"id": "probe", "title": "Probe"},
        {"id": "experiment", **metadata}, "config-0", 0,
    )
    manifest = json.loads((path / "manifest.json").read_text())
    for key, value in metadata.items():
        assert manifest[key] == manifest["experiment"][key] == value


class _UncooperativeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.terminated = self.killed = self.closed = False

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.alive = False

    def join(self, timeout=None):
        assert timeout == 0, "unbounded join is forbidden"

    def close(self):
        self.closed = True


def _entry() -> dict[str, Any]:
    return {
        "lock": threading.Lock(), "cancelled": threading.Event(),
        "cancelling": False, "ready": [],
        "report": {"runs": [], "completed": 0, "failed": 0, "error": None},
    }


@pytest.mark.parametrize("count", [1, 8])
def test_shutdown_phase_deadlines_are_shared_not_per_child(
    count: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def monotonic():
        calls.append(None)
        return len(calls) * 0.5

    monkeypatch.setattr(experiments, "time", SimpleNamespace(monotonic=monotonic))
    manager = ExperimentManager(tmp_path / "runs", _Registry(tmp_path))
    entry = _entry()
    processes = [_UncooperativeProcess() for _ in range(count)]
    active = [{
        "process": process, "config_index": 0, "seed": seed,
        "path": tmp_path / f"run-{seed}", "output": tmp_path / f"result-{seed}.json",
    } for seed, process in enumerate(processes)]
    manager._stop_active(entry, active)
    assert not active and entry["cancelled"].is_set()
    assert all(process.terminated and process.killed and process.closed for process in processes)
    assert len(calls) <= 10  # Independent of count, with no jobs-times-grace join.
    assert len(entry["report"]["runs"]) == count


@pytest.mark.parametrize("status", ["completed", "faulted", "input_timeout", "stopped", "interrupted"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_recovery_preserves_every_actual_terminal_outcome_and_original_error(
    status: str, cancelled: bool, tmp_path: Path
) -> None:
    from aeroagentsim.services.feed_index import _results_state

    path = tmp_path / "run"
    original_error = None if status == "completed" else f"original {status}"
    prefix = _run_files(path, status=status, error=original_error)
    (path / "index.json").write_text("not a valid derived index")
    metadata = json.loads((path / "manifest.json").read_text())
    metadata["final_cursor"] = 1  # Terminal status preceded the newer ACK.
    metadata["recovery_error"] = "previous interrupted reconciliation"
    (path / "manifest.json").write_text(json.dumps(metadata))
    names = ["journal.jsonl", "committed-prefix.json"]
    before = {name: (path / name).read_bytes() for name in names}
    output = tmp_path / "result.json"
    output.write_text(json.dumps({"worker_error": "exception could not be transported"}))
    entry = _entry()
    if cancelled:
        entry["cancelled"].set()
        entry["cancelling"] = True
    result = ExperimentManager._recover_cell(entry, {"path": path, "output": output})
    assert result["status"] == status and result["error"] == original_error
    assert result["cut"] == prefix and result["cursor"] == 2
    assert result["kpis"] == {}
    assert result["collection_error"]
    if status == "input_timeout":
        assert result["waiting"] == {"stream_ids": ["source"], "at_ns": "10"}
    assert {name: (path / name).read_bytes() for name in names} == before
    repaired = json.loads((path / "manifest.json").read_text())
    assert repaired == {
        key: (2 if key == "final_cursor" else value)
        for key, value in metadata.items() if key != "recovery_error"
    }
    assert [row["index"] for row in json.loads((path / "index.json").read_text())] == [0, 1]
    assert _results_state(path, repaired, {"next": 2}) == {
        "results_final": True, "results_cursor": 2, "final_commit_index": 1,
    }


def test_terminal_manifest_overrides_stale_worker_result(tmp_path: Path) -> None:
    path = tmp_path / "run"
    _run_files(path, status="input_timeout", error="source deadline expired")
    output = tmp_path / "result.json"
    output.write_text(json.dumps({"result": {
        "status": "faulted", "error": "transport failure",
        "kpis": {"tasks_completed": 0}, "kpi_meta": {},
    }}))
    result = ExperimentManager._recover_cell(_entry(), {"path": path, "output": output})
    assert result["status"] == "input_timeout"
    assert result["error"] == "source deadline expired"
    assert result["waiting"]["stream_ids"] == ["source"]
    assert result["kpis"] == {}


@pytest.mark.parametrize("ack", ["missing", "invalid", "valid"])
@pytest.mark.parametrize("status", ["running", "completed", "faulted", "input_timeout"])
def test_dead_writer_recovery_never_promotes_visible_tail_or_trusts_index(
    ack: str, status: str, tmp_path: Path
) -> None:
    path = tmp_path / "run"
    original_error = "original failure" if status in {"faulted", "input_timeout"} else None
    prefix = _run_files(path, status=status, error=original_error)
    (path / "index.json").write_text("corrupt derived cache")
    if ack == "missing":
        (path / "committed-prefix.json").unlink()
    elif ack == "invalid":
        (path / "committed-prefix.json").write_text('{"bytes":-1,"index":1}')
    journal = (path / "journal.jsonl").read_bytes()
    entry = _entry()
    entry["cancelled"].set()
    entry["cancelling"] = True
    result = ExperimentManager._recover_cell(entry, {
        "path": path, "output": tmp_path / "absent.json",
    })
    metadata = json.loads((path / "manifest.json").read_text())
    expected_status = "interrupted" if status == "running" else status
    assert result["status"] == metadata["status"] == expected_status
    if original_error is not None:
        assert result["error"] == metadata["error"] == original_error
    if status == "input_timeout":
        assert result["waiting"] == {"stream_ids": ["source"], "at_ns": "10"}
    assert result["kpis"] == {}
    assert (path / "journal.jsonl").read_bytes() == journal
    index = json.loads((path / "index.json").read_text())
    if ack == "valid":
        assert [row["index"] for row in index] == [0, 1]
        assert result["cut"] == prefix and metadata["final_cursor"] == 2
    else:
        assert index == [] and result["cut"] is None
        assert "final_cursor" not in metadata
    if ack == "invalid":
        assert metadata["recovery_error"] and result["collection_error"]


@pytest.mark.parametrize("kind", ["beyond_eof", "wrong_index", "partial_record"])
@pytest.mark.parametrize("status", ["running", "faulted", "completed"])
def test_invalid_ack_reconciliation_cannot_be_reported_as_a_known_cut(
    kind: str, status: str, tmp_path: Path,
) -> None:
    path = tmp_path / "run"
    prefix = _run_files(path, status=status)
    if kind == "beyond_eof":
        prefix["bytes"] = (path / "journal.jsonl").stat().st_size + 1
    elif kind == "wrong_index":
        prefix["index"] += 1
    else:
        prefix["bytes"] -= 1
    ack = path / "committed-prefix.json"
    ack.write_text(json.dumps(prefix))
    before = ack.read_bytes()
    entry = _entry()
    entry["cancelling"] = True
    result = ExperimentManager._recover_cell(entry, {
        "path": path, "output": tmp_path / "absent.json",
    })
    assert result["status"] == ("interrupted" if status == "running" else status)
    assert result["kpis"] == {}
    assert result["cut"] is result["cursor"] is None
    assert result["collection_error"]
    assert ack.read_bytes() == before
    assert "final_cursor" not in json.loads((path / "manifest.json").read_text())


def _patch_session(
    monkeypatch, *, until_ns=1, failure=None, startup_failure=None,
    close_failure=None, block_close=False, cancel_on_wait=None,
):
    from aeroagentsim import scenario
    from aeroagentsim.platform import simulation
    from aeroagentsim.services import artifacts, template_execution
    from aeroagentsim.services.storage import RunStorage

    sessions = []

    class Kernel:
        def __init__(self, prefix):
            self.acknowledged_prefix = (prefix["bytes"], prefix["index"])
            self.sealed_ns = 0

        def run_until(self, ns, **hooks):
            assert hooks["on_poll"]()
            assert hooks["on_boundary"](ns)
            if cancel_on_wait is not None:
                cancel_on_wait.set()
                assert hooks["on_wait"](SimpleNamespace(stream_ids=["source"], target_ns=ns)) is False
                return
            if failure is not None:
                raise failure
            self.sealed_ns = ns
            hooks["on_commit"]()

    class Simulation:
        def __init__(self, prefix):
            self.kernel = Kernel(prefix)

        def start(self):
            if startup_failure is not None:
                raise startup_failure

    class Session:
        def __init__(self, scenario, path):
            prefix = _run_files(path, status="created")
            self.path = path
            self.storage = RunStorage(path)
            self.simulation = Simulation(prefix)
            self.started = self.closed = False
            self.now_ns = 0
            self.waits = []
            sessions.append(self)

        def start(self):
            pytest.fail("owner must observe raw startup failures before cleanup")

        def report_wait(self, state):
            self.waits.append(state)

        def close(self):
            self.closed = True
            if block_close:
                (self.path / "cleanup.ready").write_text("ready")
                threading.Event().wait()
            if close_failure is not None:
                raise close_failure

    compiled = SimpleNamespace(until_ns=until_ns, advance_ns=1)
    monkeypatch.setattr(scenario, "load_scenario", lambda *args, **kwargs: compiled)
    monkeypatch.setattr(artifacts, "scope_capture_storage", lambda scenario, path: scenario)
    monkeypatch.setattr(template_execution, "execution_document", lambda template, doc, base: doc)
    monkeypatch.setattr(simulation, "RunSession", Session)
    return sessions


def _child(path, cancelled=None):
    return experiments._child_run(
        {"run": {}}, str(path.parent), str(path),
        {"id": "probe", "title": "Probe", "knobs": {}},
        {"id": "experiment"}, "config-0", 0, cancelled=cancelled,
    )


def test_prestart_cancellation_cannot_complete_a_zero_duration_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = _patch_session(monkeypatch, until_ns=0)
    cancelled = threading.Event()
    cancelled.set()
    result = _child(tmp_path / "run", cancelled)
    assert not sessions[0].started and sessions[0].closed
    assert result["status"] == "interrupted" and result["kpis"] == {}


def test_source_wait_is_cooperatively_interruptible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = threading.Event()
    sessions = _patch_session(monkeypatch, cancel_on_wait=cancelled)
    result = _child(tmp_path / "run", cancelled)
    assert sessions[0].started and sessions[0].closed and sessions[0].waits
    assert result["status"] == "interrupted" and result["kpis"] == {}
    assert result["cut"]["index"] == 1 and result["cursor"] == 2


def test_child_retains_input_timeout_without_fabricated_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aerokernel.errors import KernelError

    failure = KernelError("WATERMARK_TIMEOUT", "source timed out", stream_ids=["source"], target_ns=10)
    sessions = _patch_session(monkeypatch, failure=failure)
    result = _child(tmp_path / "run")
    assert sessions[0].closed
    assert result["status"] == "input_timeout" and "WATERMARK_TIMEOUT" in result["error"]
    assert result["waiting"] == {"stream_ids": ["source"], "at_ns": "10"}
    assert result["kpis"] == {} and result["kpi_meta"] == {}


def test_completed_child_uses_explicit_snapshot_and_retains_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aeroagentsim import runs
    from aeroagentsim.services import comparison

    _patch_session(monkeypatch)
    marker = object()
    descriptors = {"tasks_completed": {
        "value": 0, "unit": "count", "label": "Completed",
        "definition_id": "tasks/completed", "definition_revision": "recorded-revision",
    }}

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def snapshot(self):
            return marker

    def project(cuts):
        assert list(cuts.values()) == [marker]
        return {"runs": [{
            "cut": {"bytes": 22, "index": 1}, "cursor": 2,
            "kpis": copy.deepcopy(descriptors), "engine_versions": {"engine": "recorded"},
            "projection": {"mode": "current_code_recomputation", "revision": None},
        }]}

    monkeypatch.setattr(runs, "open_run", lambda path: Reader())
    monkeypatch.setattr(comparison, "compare_runs_at_cuts", project)
    result = _child(tmp_path / "run")
    assert result["status"] == "completed" and result["error"] is None
    assert result["kpis"] == {"tasks_completed": 0}
    assert result["kpi_meta"]["tasks_completed"]["definition_revision"] == "recorded-revision"
    assert result["basis"]["engine_versions"] == {"engine": "recorded"}
    assert result["basis"]["projection"]["revision"] is None
    assert result["cut"] == {"bytes": 22, "index": 1} and result["cursor"] == 2


def _sample(config, seed, value, descriptor=None, *, status="completed", **extra):
    return {
        "config_id": config, "seed": seed, "run": f"/recorded/{config}-{seed}",
        "run_id": f"{config}-{seed}", "status": status, "error": None,
        "cut": {"bytes": 100 + seed, "index": 10 + seed}, "cursor": 11 + seed,
        "basis": {"engine_versions": None, "model_revisions": None},
        "kpis": {"mean_task_latency_s": value},
        "kpi_meta": {"mean_task_latency_s": descriptor or {}},
        **extra,
    }


def _aggregate(tmp_path, samples, *, seeds=2):
    manager = ExperimentManager(tmp_path, _Registry(tmp_path))
    report = {
        "seeds": seeds, "runs": samples,
        "table": [{"config_id": key, "knobs": {}, "metrics": {}}
                  for key in dict.fromkeys(run["config_id"] for run in samples)],
    }
    manager._aggregate_locked({"report": report})
    return report


def test_aggregation_retains_run_cut_definition_revisions_and_exclusions(tmp_path: Path) -> None:
    descriptor = {"unit": "s", "definition": "latency", "revision": "v2"}
    report = _aggregate(tmp_path, [
        _sample("c0", 0, 2, descriptor), _sample("c0", 1, 4, descriptor),
        _sample("c1", 0, 5, descriptor),
        _sample("c1", 1, 0, descriptor, status="faulted", error="native failure"),
    ])
    first, second = [row["metrics"]["mean_task_latency_s"] for row in report["table"]]
    assert first["mean"] == 3 and first["std"] == pytest.approx(math.sqrt(2))
    assert first["n"] == 2 and first["best"]
    assert second["mean"] == 5 and second["n"] == 1 and not second["best"]
    assert second["std"] is None and second["compatibility"] == "compatible"
    assert second["definition"] == "latency" and second["revision"] == "v2"
    assert second["samples"][0]["run_id"] == "c1-0"
    assert second["samples"][0]["cut"] == {"bytes": 100, "index": 10}
    assert second["sample_counts"] == {"requested": 2, "included": 1, "excluded": 1, "pending": 0}
    assert second["sample_exclusions"][0]["reason"] == "faulted"
    assert report["outcome_counts"] == {"completed": 3, "faulted": 1}
    assert report["sample_exclusions"][0]["error"] == "native failure"
    assert report["alignment"] == "recorded_run_end"
    csv_row = next(row for row in report["csv"]
                   if row["configuration"] == "c1" and row["metric"] == "mean_task_latency_s")
    assert list(csv_row)[:8] == ["configuration", "knobs", "metric", "mean", "std", "n", "unit", "best"]
    assert json.loads(csv_row["samples"])[0]["cursor"] == 11
    assert json.loads(csv_row["sample_exclusions"])[0]["reason"] == "faulted"


@pytest.mark.parametrize(("field", "other", "message"), [
    ("unit", "ms", "units"), ("definition", "different", "definitions"),
    ("revision", "v3", "revisions"),
])
@pytest.mark.parametrize("across_configurations", [False, True])
def test_known_descriptor_conflicts_rejected_even_with_legacy_and_null_samples(
    field, other, message, across_configurations: bool, tmp_path: Path
) -> None:
    known = {"unit": "s", "definition": "latency", "revision": "v2"}
    incompatible = {**known, field: other}
    config = "c1" if across_configurations else "c0"
    samples = [
        _sample("c0", 0, 2, known),
        _sample("c0", 1, 4, {}),
        _sample(config, 2, None, incompatible),
    ]
    with pytest.raises(ValueError, match=f"incompatible {message}"):
        _aggregate(tmp_path, samples, seeds=3)


def test_legacy_unknown_null_missing_and_failed_samples_remain_distinct(tmp_path: Path) -> None:
    missing = _sample("c0", 2, None)
    missing["kpis"] = {}
    report = _aggregate(tmp_path, [
        _sample("c0", 0, 0, {"unit": "s"}),
        _sample("c0", 1, None), missing,
        _sample("c0", 3, 999, status="input_timeout", error="source wait"),
    ], seeds=5)
    metric = report["table"][0]["metrics"]["mean_task_latency_s"]
    assert metric["mean"] == 0 and metric["n"] == 1 and metric["std"] is None
    assert metric["definition"] is None and metric["revision"] is None
    assert metric["compatibility"] == metric["comparison_compatibility"] == "unknown"
    assert metric["samples"][0]["descriptor"] == {"unit": "s", "definition": None, "revision": None}
    assert metric["samples"][0]["basis"]["model_revisions"] is None
    assert [item["reason"] for item in metric["sample_exclusions"]] == [
        "unmeasured", "missing_measurement", "input_timeout",
    ]
    assert metric["sample_counts"] == {"requested": 5, "included": 1, "excluded": 3, "pending": 1}


def test_successful_status_with_collection_error_is_not_a_sample(tmp_path: Path) -> None:
    report = _aggregate(tmp_path, [
        _sample("c0", 0, 0, collection_error="projection interrupted"),
    ], seeds=1)
    metric = report["table"][0]["metrics"]["mean_task_latency_s"]
    assert metric["mean"] is None and metric["n"] == 0
    assert metric["sample_exclusions"][0]["reason"] == "collection_error"
    assert report["outcome_counts"] == {"completed": 1}


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "0"])
def test_invalid_measurements_are_rejected_not_coerced(tmp_path: Path, value) -> None:
    with pytest.raises(ValueError, match="Invalid measured KPI"):
        _aggregate(tmp_path, [_sample("c0", 0, value)])


def test_close_does_not_wait_for_registry_validation_and_late_launch_is_rejected(tmp_path: Path) -> None:
    validating, release = threading.Event(), threading.Event()

    class BlockingRegistry(_Registry):
        def apply(self, template_id, knobs):
            validating.set()
            assert release.wait(timeout=5)
            return super().apply(template_id, knobs)

    manager = ExperimentManager(tmp_path / "runs", BlockingRegistry(tmp_path))
    errors = []

    def create():
        try:
            manager.create("probe", vary={})
        except Exception as exc:
            errors.append(exc)

    creator = threading.Thread(target=create, daemon=True)
    creator.start()
    closer = None
    try:
        assert validating.wait(timeout=2)
        closer = threading.Thread(target=manager.close, daemon=True)
        closer.start()
        closer.join(timeout=1)
        assert not closer.is_alive(), "close waited on a registry apply hook"
    finally:
        release.set()
        creator.join(timeout=2)
        if closer is not None:
            closer.join(timeout=2)
    assert not creator.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ExperimentError)
    assert "closed" in str(errors[0])
    assert manager._experiments == {} and not (tmp_path / "runs").exists()


def test_lock_free_cancellation_and_parent_owned_recovery_state(tmp_path: Path) -> None:
    flag = SimpleNamespace(value=0)
    cancelled = experiments._Cancellation(flag)
    assert not cancelled.is_set()
    cancelled.set()
    assert flag.value == 1 and cancelled.is_set()

    class PoisonedChildEvent:
        def is_set(self):
            pytest.fail("parent consulted a lock potentially held by a dead child")

    path = tmp_path / "run"
    _run_files(path)
    entry = _entry()
    entry.update(cancelled=PoisonedChildEvent(), cancelling=True)
    result = ExperimentManager._recover_cell(entry, {
        "path": path, "output": tmp_path / "absent.json",
    })
    assert result["status"] == "interrupted"


def test_no_artifact_recovery_until_blocked_live_siblings_are_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ExperimentManager(tmp_path / "runs", _Registry(tmp_path))
    entry = _entry()
    processes = [_UncooperativeProcess(), _UncooperativeProcess()]
    processes[0].alive = False
    active = [{
        "process": process, "config_index": 0, "seed": seed,
        "path": tmp_path / f"run-{seed}", "output": tmp_path / f"result-{seed}.json",
    } for seed, process in enumerate(processes)]
    recovered = []

    def recover(entry, cell):
        assert not any(process.alive for process in processes)
        recovered.append(cell["seed"])
        return {"run": None, "run_id": None, "status": "interrupted",
                "error": "cancelled", "kpis": {}, "kpi_meta": {}}

    ticks = iter(range(100))
    monkeypatch.setattr(experiments, "time", SimpleNamespace(monotonic=lambda: next(ticks) * 0.5))
    monkeypatch.setattr(manager, "_recover_cell", recover)
    manager._collect(entry, active)
    assert recovered == [] and len(entry["ready"]) == 1 and len(active) == 1
    manager._stop_active(entry, active)
    assert sorted(recovered) == [0, 1] and not active and entry["ready"] == []


@pytest.mark.parametrize("status", ["created", "running", "waiting_for_input", "completed", "input_timeout", None])
def test_unconfirmed_termination_retains_live_observed_status_and_control_errors(
    status, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DeniedProcess(_UncooperativeProcess):
        def terminate(self):
            raise PermissionError("terminate denied")

        def kill(self):
            raise PermissionError("kill denied")

    path = tmp_path / "run"
    if status is not None:
        _run_files(path, status=status, error="original" if status == "input_timeout" else None)
    before = {file.name: file.read_bytes() for file in path.glob("*")}
    process = DeniedProcess()
    active = [{"process": process, "path": path, "output": tmp_path / "absent.json",
               "config_index": 0, "seed": 0}]
    entry = _entry()
    ticks = iter(range(100))
    monkeypatch.setattr(experiments, "time", SimpleNamespace(monotonic=lambda: next(ticks) * 0.5))
    manager = ExperimentManager(tmp_path / "runs", _Registry(tmp_path))
    manager._stop_active(entry, active)
    row = entry["report"]["runs"][0]
    assert row["status"] == status and row["termination_confirmed"] is False
    assert row["cancellation_requested"] is True and row["attempted"] is True
    assert len(row["control_errors"]) == 2
    assert "terminate denied" in row["control_errors"][0]
    assert "kill denied" in row["control_errors"][1]
    assert row["kpis"] == {} and entry["report"]["shutdown_error"]
    if status == "input_timeout":
        assert row["error"] == "original"
    assert {file.name: file.read_bytes() for file in path.glob("*")} == before


def test_dead_supervisor_without_reaping_proof_does_not_finalize_writer(tmp_path: Path) -> None:
    class LostSupervisor(_UncooperativeProcess):
        termination_confirmed = False

    path = tmp_path / "run"
    _run_files(path)
    before = {file.name: file.read_bytes() for file in path.glob("*")}
    process = LostSupervisor()
    process.alive = False
    active = [{"process": process, "path": path, "output": tmp_path / "absent.json",
               "config_index": 0, "seed": 0}]
    entry = _entry()
    manager = ExperimentManager(tmp_path / "runs", _Registry(tmp_path))
    manager._collect(entry, active)
    assert not active and process.closed
    manager._recover_ready(entry)
    result = entry["report"]["runs"][0]
    assert result["termination_confirmed"] is False
    assert result["status"] == "running" and result["collection_error"]
    assert {file.name: file.read_bytes() for file in path.glob("*")} == before


def test_child_owner_refreshes_final_ack_without_overwriting_terminal_failure(tmp_path: Path) -> None:
    path = tmp_path / "run"
    prefix = _run_files(path, status="input_timeout", error="original timeout")
    (path / "index.json").write_text("corrupt derived cache")
    metadata = experiments._finish_run(path, "faulted", "later collection error", recovering=False)
    assert metadata["status"] == "input_timeout" and metadata["error"] == "original timeout"
    assert metadata["waiting"] == {"stream_ids": ["source"], "at_ns": "10"}
    assert metadata["final_cursor"] == 2
    assert json.loads((path / "committed-prefix.json").read_text()) == prefix
    assert [row["index"] for row in json.loads((path / "index.json").read_text())] == [0, 1]


@pytest.mark.parametrize("explicit_unknown", [False, True])
def test_partial_payload_uses_missing_actual_cut_but_preserves_explicit_unknown(
    explicit_unknown: bool, tmp_path: Path
) -> None:
    path = tmp_path / "run"
    prefix = _run_files(path, status="completed")
    payload = {"kpis": {}, "kpi_meta": {}}
    if explicit_unknown:
        payload["cut"] = None
    output = tmp_path / "result.json"
    output.write_text(json.dumps({"result": payload}))
    result = ExperimentManager._recover_cell(_entry(), {"path": path, "output": output})
    assert result["status"] == "completed" and result["error"] is None
    assert result["cut"] == (None if explicit_unknown else prefix)
    assert result["cursor"] == 2


def test_manifest_write_failure_still_closes_owned_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aeroagentsim.services.storage import RunStorage

    sessions = _patch_session(monkeypatch)
    atomic = RunStorage._atomic
    failed = []

    def fail_template_write(self, name, value):
        if name == "manifest.json" and "template" in value and not failed:
            failed.append(True)
            raise OSError("template manifest publication failed")
        return atomic(self, name, value)

    monkeypatch.setattr(RunStorage, "_atomic", fail_template_write)
    result = _child(tmp_path / "run")
    assert sessions[0].closed and result["status"] == "faulted"
    assert "template manifest publication failed" in result["error"]
    assert result["kpis"] == {}


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["start", "run", "close"])
def test_base_exception_never_fabricates_completed_outcome(
    kind, phase: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = kind("test interruption")
    argument = {"start": "startup_failure", "run": "failure", "close": "close_failure"}[phase]
    sessions = _patch_session(monkeypatch, **{argument: failure})
    path = tmp_path / "run"
    with pytest.raises(kind):
        _child(path)
    assert sessions[0].closed
    metadata = json.loads((path / "manifest.json").read_text())
    assert metadata["status"] == "interrupted"
    assert kind.__name__ in metadata["error"]
    if phase != "close":
        assert metadata["execution_outcome"]["status"] == "interrupted"
    else:
        assert "execution_outcome" not in metadata  # Never prepublish successful execution.
    recovered = ExperimentManager._recover_cell(_entry(), {
        "path": path, "output": tmp_path / "absent.json",
    })
    assert recovered["status"] == "interrupted" and recovered["kpis"] == {}


def _terminal_cleanup_ack_worker(arguments, cancelled, output) -> None:
    """Stop after cleanup persisted a fault, published a newer ACK, then stalled."""
    from aeroagentsim.platform import simulation

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    patch = pytest.MonkeyPatch()
    _patch_session(patch, cancel_on_wait=cancelled)

    def fail_close(self):
        self.closed = True
        # RunSession.close() persists its cleanup fault using the PREVIOUS ACK.
        self.storage.status("faulted", error="cleanup: RuntimeError: renderer cleanup failed")
        lines = (self.path / "journal.jsonl").read_bytes().splitlines(keepends=True)
        # Model the stop record acknowledged during cleanup. _child_run will
        # publish this newer coordinate before it enters _finish_run().
        self.simulation.kernel.acknowledged_prefix = (sum(map(len, lines[:3])), 2)
        raise RuntimeError("renderer cleanup failed")

    def block_reconciliation(path, *args, **kwargs):
        index = json.loads((path / "index.json").read_text())
        (path / "index.json").write_text(json.dumps(index[:2]))
        (path / "reconciliation.ready").write_text("ready")
        threading.Event().wait()

    patch.setattr(simulation.RunSession, "close", fail_close)
    patch.setattr(experiments, "_finish_run", block_reconciliation)
    experiments._cell_worker(arguments, cancelled, output)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper regression")
def test_killed_terminal_cleanup_reconciles_newer_ack_and_projection_finality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.services.feed_index import _results_state

    context = _BlockedContext(multiprocessing.get_context("spawn"), _terminal_cleanup_ack_worker)
    monkeypatch.setattr(experiments, "multiprocessing", SimpleNamespace(get_context=lambda method: context))
    monkeypatch.setattr(experiments, "_CANCEL_GRACE_S", 0.05)
    monkeypatch.setattr(experiments, "_TERMINATE_GRACE_S", 0.05)
    root = tmp_path / "runs"
    manager = ExperimentManager(root, _Registry(tmp_path))
    created = manager.create("probe", vary={}, seeds=1)
    closer = None
    try:
        _wait_for(lambda: len(list(root.glob("*/reconciliation.ready"))) == 1)
        path = next(root.glob("*/reconciliation.ready")).parent
        original = json.loads((path / "manifest.json").read_text())
        prefix = json.loads((path / "committed-prefix.json").read_text())
        assert original["status"] == "faulted" and original["final_cursor"] == 2
        assert prefix["index"] == 2  # New final cursor must be 3, not 2.
        assert _results_state(path, original, {"next": 3})["results_final"] is False
        before = {name: (path / name).read_bytes()
                  for name in ("journal.jsonl", "committed-prefix.json")}
        closer = threading.Thread(target=manager.close, daemon=True)
        closer.start()
        closer.join(timeout=4)
        assert not closer.is_alive()
        result = manager.wait(created["id"])["runs"][0]
        repaired = json.loads((path / "manifest.json").read_text())
        assert result["status"] == repaired["status"] == original["status"]
        assert result["error"] == repaired["error"] == original["error"]
        assert result["cut"] == prefix and result["cursor"] == repaired["final_cursor"] == 3
        assert result["termination_confirmed"] is True and result["kpis"] == {}
        assert [row["index"] for row in json.loads((path / "index.json").read_text())] == [0, 1, 2]
        assert _results_state(path, repaired, {"next": 3}) == {
            "results_final": True, "results_cursor": 3, "final_commit_index": 2,
        }
        assert {name: (path / name).read_bytes() for name in before} == before
    finally:
        for process in context.processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        if closer is None:
            closer = threading.Thread(target=manager.close, daemon=True)
            closer.start()
        closer.join(timeout=2)


def _failure_cleanup_worker(arguments, cancelled, output) -> None:
    from aerokernel.errors import KernelError

    if os.name == "posix":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    stage = arguments[0]["test_failure_stage"]
    kind = arguments[0]["test_failure_kind"]
    failure = (KernelError("WATERMARK_TIMEOUT", "original source timeout", stream_ids=["source"], target_ns=10)
               if kind == "input_timeout" else RuntimeError("original native failure"))
    patch = pytest.MonkeyPatch()
    _patch_session(patch, block_close=True, **{
        "startup_failure" if stage == "start" else "failure": failure,
    })
    experiments._cell_worker(arguments, cancelled, output)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper regression")
@pytest.mark.parametrize(("stage", "kind"), [("start", "faulted"), ("run", "faulted"), ("run", "input_timeout")])
def test_observed_failure_survives_killed_blocked_cleanup(
    stage: str, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailureRegistry(_Registry):
        def apply(self, template_id, knobs):
            prepared = super().apply(template_id, knobs)
            prepared.document.update(test_failure_stage=stage, test_failure_kind=kind)
            return prepared

    context = _BlockedContext(multiprocessing.get_context("spawn"), _failure_cleanup_worker)
    monkeypatch.setattr(experiments, "multiprocessing", SimpleNamespace(get_context=lambda method: context))
    monkeypatch.setattr(experiments, "_CANCEL_GRACE_S", 0.05)
    monkeypatch.setattr(experiments, "_TERMINATE_GRACE_S", 0.05)
    root = tmp_path / "runs"
    manager = ExperimentManager(root, FailureRegistry(tmp_path))
    created = manager.create("probe", vary={}, seeds=1)
    closer = None
    try:
        _wait_for(lambda: len(list(root.glob("*/cleanup.ready"))) == 1)
        path = next(root.glob("*/cleanup.ready")).parent
        before = json.loads((path / "manifest.json").read_text())
        assert before["status"] == "running" and before["execution_outcome"]["status"] == kind
        original_error = before["execution_outcome"]["error"]
        closer = threading.Thread(target=manager.close, daemon=True)
        closer.start()
        closer.join(timeout=4)
        assert not closer.is_alive()
        final = manager.wait(created["id"])
        result = final["runs"][0]
        assert result["status"] == kind and result["error"] == original_error
        assert result["cleanup_interrupted"] is True and result["cleanup_error"]
        assert result["termination_confirmed"] is True and result["kpis"] == {}
        if kind == "input_timeout":
            assert result["waiting"] == {"stream_ids": ["source"], "at_ns": "10"}
    finally:
        for process in context.processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        if closer is None:
            closer = threading.Thread(target=manager.close, daemon=True)
            closer.start()
        closer.join(timeout=2)
