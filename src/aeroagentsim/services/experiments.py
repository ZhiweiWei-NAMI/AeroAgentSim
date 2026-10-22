"""Generic full-factorial experiment runner over scenario templates.

Every reported number comes from a real run (no invented or zero-filled
measurements): the coordinator validates typed knob variations against the
template registry in the parent, then executes each (configuration, seed)
cell as its own run — a direct child of the run root — in a private invocation
process owned by a spawned supervisor (a Linux subreaper where available,
otherwise best-effort process control). There is no queued process-pool backlog:
at most ``jobs`` cells are submitted at once, each with one supervisor.
Matched seeds 0..seeds-1 and an optional horizon override are
applied to the pinned scenario document BEFORE final ``load_scenario``
expansion, so unexpanded low-altitude generators observe them.

Aggregation reports mean ± sample std per numeric KPI per configuration with
the explicit observed sample count ``n``; missing measurements are nulls, and
the per-metric best highlight is descriptive only (explicit min/max direction
for common business metrics, never a combined ranking).
"""

from __future__ import annotations

import copy
import csv
import itertools
import json
import math
import multiprocessing
import os
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from aeroagentsim.scenario.loader import UniqueLoader

if TYPE_CHECKING:
    from aeroagentsim.authoring.templates import TemplateRegistry

CONTRACT = "aeroagentsim.experiment/v1"
#: Report directory under the run root; regular run directories stay direct
#: children of the root so the normal Runs listing still sees every run.
_EXPERIMENTS_DIRNAME = ".experiments"
#: Per-experiment admission limits, checked before copying axes or building a
#: factorial. Jobs are an explicit bound, not silently clamped to a safe value.
MAX_JOBS = 32
MAX_CONFIGURATIONS = 256
MAX_SEEDS = 1000
MAX_TOTAL_RUNS = 4096
#: Shutdown phases share one deadline across all active cells, not per child.
#: Cooperative hooks are polled at most every 50 ms during source waits; native
#: invocations/cleanup may not cooperate, so terminate and kill are necessary.
_CANCEL_POLL_S = 0.05
_CANCEL_GRACE_S = 1.0
_TERMINATE_GRACE_S = 1.0
_KILL_GRACE_S = 1.0
_TERMINAL = {"completed", "faulted", "stopped", "interrupted", "input_timeout"}
#: Descriptive best direction per common KPI. Deliberately excludes pure
#: counters such as tasks_created: "most tasks created" is not a quality
#: claim the platform can support, so no direction is asserted for it.
_BEST_DIRECTIONS: dict[str, str] = {
    "tasks_completed": "max",
    "completion_rate": "max",
    "on_time_rate": "max",
    "mean_task_latency_s": "min",
    "p95_task_latency_s": "min",
    "tasks_failed": "min",
    "energy_used_kj": "min",
    "distance_flown_km": "min",
    "separation_conflicts": "min",
    "geofence_violations": "min",
    "weather_holds": "min",
}


class ExperimentError(ValueError):
    """User-readable experiment specification error."""


class _Cancellation:
    """A one-way shared byte, without a child-owned lock to poison on kill.

    A multiprocessing.Event/locked Value is unsafe for forced cancellation:
    terminating a child inside is_set() can strand its semaphore forever.
    """

    def __init__(self, flag: Any) -> None:
        self._flag = flag

    def set(self) -> None:
        self._flag.value = 1

    def is_set(self) -> bool:
        return bool(self._flag.value)


def parse_vary(items: list[str]) -> dict[str, list[Any]]:
    """Parse repeated ``knob=a,b,c`` axes with typed UniqueLoader scalars.

    Standalone helper so the CLI and the HTTP layer share one explicit,
    portable implementation.
    """
    if not isinstance(items, list):
        raise ExperimentError("vary: list of KNOB=VALUE,VALUE strings required")
    axes: dict[str, list[Any]] = {}
    for item in items:
        if not isinstance(item, str):
            raise ExperimentError(f"--vary {item!r}: string required")
        key, separator, raw = item.partition("=")
        if not separator or not key:
            raise ExperimentError(f"--vary {item!r}: KNOB=VALUE,VALUE required")
        if key in axes:
            raise ExperimentError(f"--vary {key!r}: duplicate experiment axis")
        values: list[Any] = []
        for text in raw.split(","):
            try:
                value = yaml.load(text, Loader=UniqueLoader)
            except yaml.YAMLError as exc:
                raise ExperimentError(
                    f"--vary {key}={text!r}: invalid scalar ({exc})"
                ) from exc
            if isinstance(value, (dict, list)):
                raise ExperimentError(f"--vary {key}={text!r}: scalar required")
            values.append(value)
        axes[key] = values
    return axes


def _product(axes: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not axes:
        return [{}]
    keys = list(axes)
    return [dict(zip(keys, values)) for values in itertools.product(*axes.values())]


def _run_metadata(path: Path) -> dict[str, Any]:
    # A corrupt derived index must not hide a durable terminal outcome.
    metadata = json.loads((path / "manifest.json").read_text())
    if not isinstance(metadata, dict):
        raise ValueError("run manifest must be an object")
    return metadata


def _finish_run(
    path: Path,
    outcome: str,
    error: str | None,
    *,
    waiting: dict[str, Any] | None = None,
    recovering: bool = True,
) -> dict[str, Any]:
    """Reconcile a closed/dead writer without promoting an unacknowledged tail.

    RunStorage.status() intentionally indexes visible complete records. That is
    not a recovery operation: a terminated invocation can have written complete
    but unacknowledged bytes. Only the owner's ACK may extend the derived index.
    Terminal outcomes/errors/waiting stay authoritative, but even a terminal
    writer may have died between publishing its final ACK and rebuilding its
    derived index/final cursor. Recovery must finish that reconciliation too.
    """
    from .storage import committed_prefix

    metadata = _run_metadata(path)
    terminal = metadata.get("status") in _TERMINAL
    if terminal:
        outcome = metadata["status"]
    else:
        observed = metadata.get("execution_outcome") if recovering else None
        if isinstance(observed, dict) and observed.get("status") in {"faulted", "input_timeout", "interrupted"}:
            if outcome == "interrupted":
                metadata["cleanup_interrupted"] = True
                metadata["cleanup_error"] = error
            outcome = observed["status"]
            error, waiting = observed.get("error"), observed.get("waiting")

    def atomic(name: str, value: Any) -> None:
        target = path / name
        temporary = path / (name + ".writing")
        temporary.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
        temporary.replace(target)

    entries: list[dict[str, int]] = []
    metadata.pop("recovery_error", None)
    try:
        prefix = committed_prefix(path)
        if prefix is not None:
            # Rebuild only the ACK, without trusting even a syntactically valid
            # derived index. A crashed writer may have left that cache corrupt.
            offset = 0
            with (path / "journal.jsonl").open("rb") as stream:
                while offset < prefix["bytes"]:
                    line = stream.readline(prefix["bytes"] - offset)
                    if not line or not line.endswith(b"\n"):
                        raise ValueError("acknowledged WAL ends inside a record")
                    record = json.loads(line)
                    index = record["index"]
                    if type(index) is not int or index != len(entries):
                        raise ValueError("acknowledged WAL has non-contiguous indices")
                    entries.append({"index": index, "offset": offset, "length": len(line)})
                    offset += len(line)
            if not entries or entries[-1]["index"] != prefix["index"]:
                raise ValueError("acknowledged WAL index differs from its byte coordinate")
            metadata["final_cursor"] = prefix["index"] + 1
        else:
            # No ACK means no recoverable cut, regardless of visible bytes.
            metadata.pop("final_cursor", None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        entries = []
        metadata.pop("final_cursor", None)
        metadata["recovery_error"] = f"{type(exc).__name__}: {exc}"
    atomic("index.json", entries)
    metadata["status"] = outcome
    if not terminal:
        metadata.pop("waiting", None)
        if waiting is not None:
            metadata["waiting"] = waiting
        if error is not None:
            metadata["error"] = metadata.get("error") or error
        elif outcome == "completed":
            metadata.pop("error", None)
    atomic("manifest.json", metadata)
    return metadata


def _run_result(path: Path, *, collection_error: str | None = None) -> dict[str, Any]:
    """Read an actual outcome; only the live child projects completed evidence."""
    from .storage import committed_prefix

    metadata = _run_metadata(path)
    evidence_error = None
    try:
        cut = committed_prefix(path)
    except (OSError, ValueError) as exc:
        cut = None
        evidence_error = f"acknowledged cut: {type(exc).__name__}: {exc}"
    if metadata.get("recovery_error") is not None:
        # A syntactically valid sidecar is not an admitted cut when its byte /
        # index reconciliation failed. Preserve the sidecar, not a false cursor.
        cut = None
    return {
        "run": str(path),
        "run_id": metadata["id"],
        "status": metadata["status"],
        "error": metadata.get("error"),
        "waiting": metadata.get("waiting"),
        "execution_outcome": metadata.get("execution_outcome"),
        "cleanup_interrupted": metadata.get("cleanup_interrupted"),
        "cleanup_error": metadata.get("cleanup_error"),
        **(_supervision_metadata(metadata["supervision"])
           if metadata.get("supervision") else {}),
        "collection_error": collection_error or evidence_error or metadata.get("recovery_error"),
        "cut": cut,
        "cursor": cut["index"] + 1 if cut is not None else None,
        "basis": {
            "alignment": "recorded_run_end",
            "configuration_digest": metadata.get("configuration_digest"),
            "kernel_run_id": metadata.get("kernel_run_id"),
            "epoch": metadata.get("epoch"),
        },
        "kpis": {},
        "kpi_meta": {},
    }


def _child_run(
    document: dict[str, Any],
    base: str,
    directory: str,
    template: dict[str, Any],
    experiment: dict[str, Any],
    config_id: str,
    seed: int,
    cancelled: Any = None,
) -> dict[str, Any]:
    """Recompile and run one cell, publishing only kernel-acknowledged cuts.

    Host hooks do not change the pinned schedule. They allow source waits and
    safe boundaries to yield; an uncooperative plugin is killed by the parent.
    RunSession owns artifacts and cleanup; the host calls raw start/advance so
    it can persist failures before cleanup blocks. Projection runs in this same
    killable child, never on the shutdown coordinator.
    """
    from aerokernel.errors import KernelError

    from aeroagentsim.platform.simulation import RunSession
    from aeroagentsim.runs import open_run
    from aeroagentsim.scenario import load_scenario
    from aeroagentsim.services.artifacts import scope_capture_storage
    from aeroagentsim.services.template_execution import execution_document

    from .comparison import compare_runs_at_cuts

    path = Path(directory)
    scenario = load_scenario(
        execution_document(template["id"], document, Path(base)), base=Path(base)
    )
    scenario = scope_capture_storage(scenario, path)
    session = RunSession(scenario, path)
    outcome, error = "interrupted", None
    pending_failure: BaseException | None = None
    waiting = None
    reported_wait: Any = None
    last_publication = 0.0

    def stopped() -> bool:
        return cancelled is not None and cancelled.is_set()

    def publish(*, force: bool = False) -> None:
        nonlocal last_publication
        now = time.monotonic()
        if force or now - last_publication >= _CANCEL_POLL_S:
            session.storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
            last_publication = now

    def on_wait(state: Any) -> bool:
        nonlocal reported_wait
        publish(force=True)
        if state != reported_wait:
            session.report_wait(state)
            reported_wait = state
        return not stopped()

    def on_poll() -> bool:
        publish()
        return not stopped()

    def on_boundary(target_ns: int) -> bool:
        return on_poll()

    try:
        session.storage._atomic(
            "manifest.json",
            {
                **session.storage.metadata(),
                "template": template,
                **(_supervision_metadata(experiment["supervision"])
                   if experiment.get("supervision") else {}),
                "experiment": {**experiment, "config_id": config_id, "seed": seed},
            },
        )
        # A killed start must still leave its last real ACK, not a file-size cut.
        publish(force=True)
        if not stopped():
            # RunSession.start() closes on failure before re-raising; a native
            # close may block and hide the observed startup failure. The owner
            # therefore performs its start bookkeeping around raw Simulation
            # start and records failures before invoking RunSession cleanup.
            session.started_wall = time.perf_counter()
            session.storage._atomic("manifest.json", {
                **session.storage.metadata(), "status": "running",
            })
            session.simulation.start()
            session.started = True
            publish(force=True)
        while session.now_ns < scenario.until_ns and not stopped():
            session.simulation.kernel.run_until(
                min(session.now_ns + scenario.advance_ns, scenario.until_ns),
                on_wait=on_wait,
                on_boundary=on_boundary,
                on_commit=publish,
                on_poll=on_poll,
            )
            session.now_ns = session.simulation.kernel.sealed_ns
        if not session.started or session.now_ns < scenario.until_ns:
            outcome = "interrupted"
            error = "experiment cancelled at an acknowledged boundary"
        else:
            outcome, error = "completed", None
    except BaseException as exc:  # Persist interruption too; never finalize it as success.
        if not isinstance(exc, Exception):
            pending_failure = exc
            outcome = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "faulted"
            error = f"{type(exc).__name__}: {exc}"
        elif isinstance(exc, KernelError) and exc.code == "WATERMARK_TIMEOUT":
            outcome, error = "input_timeout", str(exc)
            waiting = {
                "stream_ids": list(exc.context["stream_ids"]),
                "at_ns": str(exc.context["target_ns"]),
            }
        else:
            outcome, error = "faulted", f"{type(exc).__name__}: {exc}"
        # Cleanup can invoke untrusted engine/native close methods and block.
        # Preserve an observed FAILURE before entering it, but never publish
        # success or a terminal host status before cleanup/ACK finalization.
        session.storage._atomic("manifest.json", {
            **session.storage.metadata(),
            "execution_outcome": {
                "status": outcome, "error": error, "waiting": waiting,
            },
        })
        publish(force=True)
    finally:
        try:
            session.close()
        except BaseException as exc:  # Final ACK/outcome must survive cleanup interruption.
            outcome = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "faulted"
            if not isinstance(exc, Exception):
                pending_failure = pending_failure or exc
            error = f"{error + '; ' if error else ''}cleanup: {type(exc).__name__}: {exc}"
        publish(force=True)
        _finish_run(path, outcome, error, waiting=waiting, recovering=False)
    if pending_failure is not None:
        raise pending_failure
    result = _run_result(path)
    if result["status"] == "completed" and not result["error"]:
        try:
            with open_run(path) as run:
                snapshot = run.snapshot()
                evidence = compare_runs_at_cuts({result["run_id"]: snapshot})["runs"][0]
            result["cut"] = evidence["cut"]
            result["cursor"] = evidence["cursor"]
            result["basis"].update({
                key: evidence.get(key)
                for key in (
                    "kernel_run_id", "epoch", "provenance", "configuration_digest",
                    "registry_digest", "engine_versions", "journal_identity", "projection",
                )
            })
            result["kpis"] = {
                name: row["value"] for name, row in evidence["kpis"].items()
            }
            result["kpi_meta"] = {
                name: {key: value for key, value in row.items() if key != "value"}
                for name, row in evidence["kpis"].items()
            }
        except Exception as exc:  # noqa: BLE001 - projection is not a run fault
            result["collection_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _publish_cell_result(output: str, result: dict[str, Any]) -> None:
    path = Path(output)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, allow_nan=False))
    temporary.replace(path)


def _cell_worker(arguments: tuple[Any, ...], cancelled: Any, output: str) -> None:
    """Publish a detached result atomically; no possibly blocking result pipe."""
    try:
        result = {"result": _child_run(*arguments, cancelled=cancelled)}
    except Exception as exc:  # noqa: BLE001 - exception objects need not pickle
        result = {"worker_error": f"{type(exc).__name__}: {exc}"}
    _publish_cell_result(output, result)


def _supervision_metadata(mode: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"supervision": mode}
    if mode == "best_effort":
        metadata.update({
            "termination_scope": "cell_process",
            "cleanup_limitations": "Detached renderer/browser descendants may not be reclaimed.",
        })
    return metadata


def _best_effort_worker(worker: Any, arguments: tuple[Any, ...], cancelled: Any, output: str) -> None:
    # A private session lets POSIX cancellation reach non-detached helpers too.
    # Failure to create it must not prevent a portable cell from running.
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    worker(arguments, cancelled, output)


def _best_effort_supervisor(
    worker: Any, arguments: tuple[Any, ...], cancelled: Any, output: str,
    force: Any, reaped: Any,
) -> None:
    """Confirm only invocation exit; detached descendants are not owned here."""
    # The private supervisor is spawned as a daemon for the Linux fork path.
    # Allow it to spawn the portable invocation; it joins that child itself.
    multiprocessing.current_process().daemon = False
    process = multiprocessing.get_context("spawn").Process(
        target=_best_effort_worker, args=(worker, arguments, cancelled, output),
    )
    terminate_requested = False

    def request_terminate(signum: int, frame: Any) -> None:
        nonlocal terminate_requested
        terminate_requested = True

    signal.signal(signal.SIGTERM, request_terminate)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGCHLD"):
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    try:
        process.start()
    except Exception as exc:  # No invocation has been started.
        process.close()
        reaped.value = 1
        _publish_cell_result(output, {
            "worker_error": f"subprocess supervision: {type(exc).__name__}: {exc}",
        })
        return

    from multiprocessing.connection import wait

    escalate_at: float | None = None
    has_groups = hasattr(os, "killpg") and hasattr(os, "getpgid")
    while True:
        # Observe exit without reaping yet: keep the POSIX PID/group ID pinned
        # until the final group signal, even if the invocation exits on TERM.
        exited = bool(wait([process.sentinel], timeout=_CANCEL_POLL_S / 5))
        if (terminate_requested or force.value) and escalate_at is None:
            escalate_at = time.monotonic() + _TERMINATE_GRACE_S
        phase = max(force.value, int(escalate_at is not None))
        if escalate_at is not None and time.monotonic() >= escalate_at:
            phase = 2
        if phase:
            if has_groups:
                try:
                    # Never signal the supervisor/server's inherited group if
                    # cancellation races the child's setsid() or it failed.
                    if os.getpgid(process.pid) == process.pid:
                        os.killpg(process.pid, signal.SIGKILL if phase >= 2 else signal.SIGTERM)
                except OSError:
                    pass  # Not evidence of group exit or descendant cleanup.
            if not exited:
                try:
                    (process.kill if phase >= 2 else process.terminate)()
                except OSError:
                    pass  # Leave confirmation unset while the cell is still alive.
        if exited and (not phase or not has_groups or phase >= 2):
            break
        if exited:
            # A ready sentinel would otherwise busy-spin during group grace.
            time.sleep(_CANCEL_POLL_S / 5)
    process.join()
    reaped.value = 1  # Only the cell process was verified/reaped, not its tree.
    process.close()


def _cell_supervisor(
    worker: Any, arguments: tuple[Any, ...], cancelled: Any, output: str,
    supervision: Any, force: Any, reaped: Any,
) -> None:
    """Own/reap a Linux subtree, or control the invocation best-effort.

    A new session/process group is not an ownership boundary: the renderer and
    Chromium can detach into their own sessions. A per-cell subreaper adopts
    their orphans instead, where supported. The coordinator requests escalation
    without killing this supervisor; fallback only confirms invocation exit.
    """
    terminate_requested = False

    def request_terminate(signum: int, frame: Any) -> None:
        nonlocal terminate_requested
        terminate_requested = True

    try:
        if sys.platform != "linux" or not hasattr(os, "fork"):
            raise OSError("experiment subprocess supervision requires Linux /proc")
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        # PR_SET_CHILD_SUBREAPER applies only to this private supervisor, not to
        # the server or another manager's workers. If unavailable, use portable
        # best-effort control without claiming this ownership boundary.
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        pid = os.getpid()
        children = Path(f"/proc/{pid}/task/{pid}/children")
        children.read_text()  # Check procfs availability before forking.
    except (OSError, AttributeError):
        mode = "best_effort"
    else:
        mode = "linux_subreaper"
    supervision.value = 1 if mode == "linux_subreaper" else 2
    document, base, directory, template, experiment, config_id, seed = arguments
    arguments = (
        document, base, directory, template,
        {**experiment, **_supervision_metadata(mode)}, config_id, seed,
    )
    if mode == "best_effort":
        _best_effort_supervisor(worker, arguments, cancelled, output, force, reaped)
        return

    try:
        signal.signal(signal.SIGTERM, request_terminate)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # Keep children waitable/PIDs pinned.
        worker_pid = os.fork()
    except Exception as exc:  # noqa: BLE001 - no invocation has been started
        reaped.value = 1
        _publish_cell_result(output, {
            "worker_error": f"subprocess supervision: {type(exc).__name__}: {exc}",
        })
        return

    if worker_pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            worker(arguments, cancelled, output)
        except BaseException:
            os._exit(1)
        os._exit(0)

    escalate_at: float | None = None
    while True:
        while True:
            try:
                exited, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                # With subreaper ownership, ECHILD proves that neither the cell
                # nor any adopted local helper remains, even as a zombie.
                reaped.value = 1
                return
            except InterruptedError:
                continue
            if exited == 0:
                break
            if exited == worker_pid:
                # Also clean up helpers leaked by an otherwise normal/faulted
                # cell exit; do not wait for a later manager.close().
                escalate_at = time.monotonic() + _TERMINATE_GRACE_S
        if terminate_requested and escalate_at is None:
            # Also handle multiprocessing's interpreter-exit SIGTERM without
            # getting stuck forever on a cell that ignores that signal.
            escalate_at = time.monotonic() + _TERMINATE_GRACE_S
        phase = max(force.value, int(escalate_at is not None))
        if escalate_at is not None and time.monotonic() >= escalate_at:
            phase = 2
        if phase:
            try:
                owned = children.read_text().split()
            except OSError:
                owned = []  # No proof of exit: keep the supervisor alive.
            for child in owned:
                try:
                    # Only direct, unreaped children are signalled. Their PIDs
                    # cannot be reused before our next waitpid. Killing a parent
                    # adopts its children here, regardless of setsid(), and the
                    # next pass signals/reaps those too.
                    os.kill(int(child), signal.SIGKILL if phase >= 2 else signal.SIGTERM)
                except OSError:
                    # The coordinator's budget will report unconfirmed exit if
                    # the OS denies control; never falsely acknowledge cleanup.
                    pass
        time.sleep(_CANCEL_POLL_S / 5)


class _CellProcess:
    """Process-compatible control of a cell without killing its supervisor."""

    def __init__(
        self, context: Any, arguments: tuple[Any, ...], cancelled: Any,
        output: str, *, name: str,
    ) -> None:
        self._force = context.RawValue("b", 0)
        self._reaped = context.RawValue("b", 0)
        self._supervision = context.RawValue("b", 0)
        self._process = context.Process(
            target=_cell_supervisor,
            args=(
                _cell_worker, arguments, cancelled, output,
                self._supervision, self._force, self._reaped,
            ),
            name=name,
            daemon=True,
        )

    @property
    def supervision(self) -> str | None:
        return {1: "linux_subreaper", 2: "best_effort"}.get(self._supervision.value)

    @property
    def termination_confirmed(self) -> bool:
        return bool(self._reaped.value)

    def start(self) -> None:
        self._process.start()

    def is_alive(self) -> bool:
        return bool(self._process.is_alive())

    def join(self, timeout: float | None = None) -> None:
        self._process.join(timeout=timeout)

    def terminate(self) -> None:
        self._force.value = max(self._force.value, 1)

    def kill(self) -> None:
        self._force.value = 2

    def close(self) -> None:
        self._process.close()


def _report_view(report: dict[str, Any]) -> dict[str, Any]:
    """Annotate legacy reports without reprojecting or rewriting measurements."""
    from .comparison import metric_identity

    result = copy.deepcopy(report)
    result.setdefault("alignment", "recorded_run_end")
    for run in result.get("runs", []):
        run.setdefault("cut", None)
        run.setdefault("cursor", None)
    for row in result.get("table", []):
        for metric in row.get("metrics", {}).values():
            identity = metric_identity(metric)
            metric.setdefault("definition", identity["definition"])
            metric.setdefault("revision", identity["revision"])
            # A historical aggregate's first descriptor is not evidence that
            # all of its contributing samples had compatible definitions.
            metric.setdefault("compatibility", "unknown")
            metric.setdefault("comparison_compatibility", "unknown")
            metric.setdefault("samples", None)
            metric.setdefault("sample_exclusions", None)
            metric.setdefault("sample_counts", {
                "requested": result.get("seeds"), "included": metric.get("n"),
                "excluded": None, "pending": None,
            })
    return result


def _write_report(directory: Path, report: dict[str, Any]) -> None:
    """Atomic-enough report trio: machine JSON, tidy CSV, human Markdown."""
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "experiment.json.tmp"
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    temporary.replace(directory / "experiment.json")
    rows = report["csv"]
    if rows:
        with (directory / "experiment.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        (directory / "experiment.csv").write_text("")
    lines = [
        f"# Experiment {report['id']}",
        "",
        f"- template: `{report['template_id']}` ({report['template_title']})",
        f"- contract: `{report['contract']}`",
        f"- status: **{report['status']}**",
        f"- configurations: {len(report['table'])} x seeds {report['seeds']}"
        f" = {report['total']} runs"
        f" ({report['completed']} completed, {report['failed']} failed)",
        "- horizon: "
        + (
            f"{report['horizon_s']} s"
            if report["horizon_s"] is not None
            else "template default"
        ),
        "",
    ]
    if report.get("error"):
        lines += [f"- error: {report['error']}", ""]
    lines += [
        "Best is descriptive per metric (min/max direction), not a ranking.",
        "Alignment is `recorded_run_end`, not a common horizon. Matched seeds do not "
        "control live-model randomness. Unknown metric definitions/revisions remain "
        "unknown compatibility; see JSON/CSV for per-sample run/cut evidence.",
        "",
    ]
    if report["runs"]:
        lines += [
            "| configuration | seed | run | status |",
            "| --- | --- | --- | --- |",
        ]
        for run in report["runs"]:
            link = (
                f"[{run['run_id']}](../../{Path(run['run']).name}/manifest.json)"
                if run["run"] is not None
                else "not created"
            )
            lines.append(
                f"| {run['config_id']} | {run['seed']} | {link} | {run['status']} |"
            )
        lines.append("")
    for row in report["table"]:
        lines.append(f"## {row['config_id']} — knobs `{json.dumps(row['knobs'])}`")
        lines.append("")
        if not row["metrics"]:
            lines.append("No numeric KPI observed for this configuration.")
            lines.append("")
            continue
        lines += [
            "| metric | mean | std | n | unit | best |",
            "| --- | --- | --- | --- | --- | --- |",
        ]

        def fmt(value: Any) -> str:
            return "null" if value is None else f"{value:.6g}"

        for name, metric in row["metrics"].items():
            mean = fmt(metric["mean"])
            if metric["best"]:
                mean = f"**{mean}**"
            lines.append(
                f"| {metric['label']} (`{name}`) | {mean} "
                f"| {fmt(metric['std'])} | {metric['n']} | {metric['unit'] or ''} "
                f"| {'yes' if metric['best'] else ''} |"
            )
        lines.append("")
    (directory / "experiment.md").write_text("\n".join(lines))


class ExperimentManager:
    """Owns experiment coordinators for one run root.

    ``create`` validates bounded counts before copying axes/materializing the
    factorial, then validates typed knobs synchronously. A coordinator owns at
    most ``jobs`` invocation processes per batch, each owned by a private spawned
    supervisor, with no queued submissions. Linux subreapers adopt/reap detached
    local helpers; best-effort fallback only confirms invocation exit. Artifact
    recovery/aggregation waits until the batch has no live siblings,
    so large dead-cell journals cannot delay control of a blocked live cell.

    ``close`` rejects further creates, signals all owned coordinators first,
    and allows 1 s cooperative grace, 1 s after terminate, then 1 s after kill
    (shared deadlines across active cells, plus at most 50 ms polling). These
    are process-wait budgets, not guarantees against stalled filesystem/OS
    operations or cancellation of already-issued external provider requests.
    No process belonging to another manager is signalled.
    """

    def __init__(self, root: Path, registry: "TemplateRegistry") -> None:
        self.root = Path(root).resolve()
        self.registry = registry
        self._lock = threading.Lock()
        self._admission_lock = threading.Lock()
        self._closed = False
        self._experiments: dict[str, dict[str, Any]] = {}

    def create(
        self,
        template_id: str,
        *,
        vary: dict[str, list[Any]],
        seeds: int = 3,
        jobs: int = 1,
        horizon_s: float | None = None,
        knobs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._admission_lock:
            if self._closed:
                raise ExperimentError("experiment manager is closed")
        # Registry/apply hooks can be slow or untrusted. They must not hold up
        # close; launch rechecks admission after validation, under a short lock.
        return self._create(
            template_id, vary=vary, seeds=seeds, jobs=jobs,
            horizon_s=horizon_s, knobs=knobs,
        )

    def _create(
        self,
        template_id: str,
        *,
        vary: dict[str, list[Any]],
        seeds: int = 3,
        jobs: int = 1,
        horizon_s: float | None = None,
        knobs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(template_id, str) or not template_id:
            raise ExperimentError("template id: nonempty string required")
        if type(seeds) is not int or seeds <= 0:
            raise ExperimentError("seeds: positive integer required")
        if type(jobs) is not int or jobs <= 0:
            raise ExperimentError("jobs: positive integer required")
        if seeds > MAX_SEEDS:
            raise ExperimentError(f"seeds: at most {MAX_SEEDS} required")
        if jobs > MAX_JOBS:
            raise ExperimentError(f"jobs: at most {MAX_JOBS} required")
        if horizon_s is not None and (
            type(horizon_s) not in (int, float)
            or not math.isfinite(horizon_s)
            or horizon_s <= 0
        ):
            raise ExperimentError("horizon_s: positive finite seconds required")
        if knobs is not None and not isinstance(knobs, dict):
            raise ExperimentError("knobs: mapping required")
        if not isinstance(vary, dict):
            raise ExperimentError("vary: mapping of knob -> values required")
        configuration_count = 1
        for name, values in vary.items():
            if not isinstance(name, str) or not name:
                raise ExperimentError("vary: knob names must be nonempty strings")
            if not isinstance(values, list) or not values:
                raise ExperimentError(
                    f"vary {name!r}: nonempty list of values required"
                )
            configuration_count *= len(values)
            if configuration_count > MAX_CONFIGURATIONS:
                raise ExperimentError(
                    f"configurations: at most {MAX_CONFIGURATIONS} required"
                )
        total = configuration_count * seeds
        if total > MAX_TOTAL_RUNS:
            raise ExperimentError(f"total runs: at most {MAX_TOTAL_RUNS} required")
        # No list(value), deepcopy, Cartesian product or registry application
        # occurs before every explicit count has passed admission.
        axes = {name: list(values) for name, values in vary.items()}
        base_knobs = dict(knobs or {})
        if overlap := axes.keys() & base_knobs.keys():
            raise ExperimentError(
                f"knobs and vary overlap: {', '.join(sorted(overlap))}"
            )
        configs: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        bases: list[str] = []
        controls: dict[str, str] = {}
        template: dict[str, Any] | None = None
        for combination in _product(axes):
            merged = {**base_knobs, **combination}
            # Typed validation (unknown knob, wrong type, out of bounds) and
            # structural apply-hook expansion happen here in the parent,
            # against the registry, long before any worker spawns.
            prepared = self.registry.apply(template_id, merged)
            for knob in prepared.metadata.get("knobs", []):
                pointer = knob.get("pointer")
                if pointer == "/run/seed":
                    if knob["id"] in merged:
                        raise ExperimentError(
                            f"{knob['id']}: use --seeds for matched experiment seeds"
                        )
                    controls[knob["id"]] = "seed"
                elif pointer == "/run/until_ns" and horizon_s is not None:
                    if knob["id"] in merged:
                        raise ExperimentError(
                            f"{knob['id']}: cannot vary a horizon overridden by --horizon"
                        )
                    controls[knob["id"]] = "until_ns"
            if template is None:
                template = prepared.run_metadata()
            document = copy.deepcopy(prepared.document)
            # Registry snapshots are stored relative to the template base so
            # the pinned document stays relocatable; child processes resolve
            # it against the recorded base directory.
            document["run"] = {**document["run"], "pacing": "fast"}
            documents.append(document)
            bases.append(str(prepared.base))
            config = copy.deepcopy(prepared.knobs)
            for name, control in controls.items():
                if control == "seed":
                    del config[
                        name
                    ]  # Seed is a replicate coordinate, not a fixed configuration.
                else:
                    assert horizon_s is not None
                    config[name] = round(float(horizon_s) * 1e9)
            configs.append(config)
        assert template is not None
        experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
        directory = self.root / _EXPERIMENTS_DIRNAME / experiment_id
        report: dict[str, Any] = {
            "id": experiment_id,
            "contract": CONTRACT,
            "template_id": template_id,
            "template_title": template["title"],
            **({"template_title_zh": template["title_zh"]} if "title_zh" in template else {}),
            "status": "created",
            "seeds": seeds,
            "jobs": jobs,
            "horizon_s": horizon_s,
            "knobs": copy.deepcopy(base_knobs),
            "vary": copy.deepcopy(axes),
            "total": total,
            "alignment": "recorded_run_end",
            "limits": {
                "jobs": MAX_JOBS,
                "configurations": MAX_CONFIGURATIONS,
                "seeds": MAX_SEEDS,
                "total": MAX_TOTAL_RUNS,
            },
            "cancellation_policy": {
                "poll_s": _CANCEL_POLL_S,
                "cooperative_grace_s": _CANCEL_GRACE_S,
                "terminate_grace_s": _TERMINATE_GRACE_S,
                "kill_grace_s": _KILL_GRACE_S,
                "scope": None,
                "supervision": None,  # Selected inside each spawned supervisor.
            },
            "completed": 0,
            "failed": 0,
            "error": None,
            "runs": [],
            "table": [
                {"config_id": f"config-{index}", "knobs": configs[index], "metrics": {}}
                for index in range(len(configs))
            ],
            "csv": [],
        }
        entry: dict[str, Any] = {
            "report": report,
            "directory": directory,
            "configs": configs,
            "documents": documents,
            "bases": bases,
            "seeds": seeds,
            "jobs": jobs,
            "horizon_s": horizon_s,
            "template_id": template_id,
            "controls": controls,
            "abort": threading.Event(),
            "cancelling": False,
            "ready": [],
            "done": threading.Event(),
            "lock": threading.Lock(),
        }
        return self._launch(entry)

    def _launch(self, entry: dict[str, Any]) -> dict[str, Any]:
        with self._admission_lock:
            if self._closed:
                raise ExperimentError("experiment manager is closed")
            return self._launch_locked(entry)

    def _launch_locked(self, entry: dict[str, Any]) -> dict[str, Any]:
        report = entry["report"]
        experiment_id = report["id"]
        self._save(entry)
        with self._lock:
            self._experiments[experiment_id] = entry
        coordinator = threading.Thread(
            target=self._coordinate,
            args=(experiment_id,),
            name=f"experiment-{experiment_id}",
            daemon=True,
        )
        try:
            coordinator.start()
        except Exception as exc:
            try:
                self._fill_unstarted(entry)
                with entry["lock"]:
                    report["status"] = "faulted"
                    report["error"] = f"coordinator start: {type(exc).__name__}: {exc}"
                    self._aggregate_locked(entry)
                self._save(entry)
            finally:
                with self._lock:
                    self._experiments.pop(experiment_id, None)
                entry["done"].set()
            raise ExperimentError(report["error"]) from exc
        with entry["lock"]:
            return self.public_report(report)

    @staticmethod
    def public_report(report: dict[str, Any]) -> dict[str, Any]:
        keys: tuple[str, ...] = (
            "id",
            "contract",
            "template_id",
            "template_title",
            "status",
            "seeds",
            "jobs",
            "horizon_s",
            "knobs",
            "vary",
            "total",
            "completed",
            "failed",
            "error",
            "runs",
            "table",
        )
        keys += tuple(
            key for key in (
                "template_title_zh", "alignment", "limits", "cancellation_policy",
                "sample_exclusions", "outcome_counts", "metric_compatibility",
                "shutdown_error", "aggregation_error",
            ) if key in report
        )
        return _report_view({key: report[key] for key in keys})

    def get(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._experiments.get(experiment_id)
        if entry is not None:
            with entry["lock"]:
                return self.public_report(entry["report"])
        stored = self.root / _EXPERIMENTS_DIRNAME / experiment_id / "experiment.json"
        if stored.is_file():
            report = json.loads(stored.read_text())
            if report.get("status") in {"created", "running"}:
                # A previous server died mid-run: stale progress is marked
                # interrupted, never synthesized as success.
                report["status"] = "interrupted"
                report["error"] = report.get("error") or (
                    "experiment interrupted by a server restart"
                )
                _write_report(stored.parent, report)
            return _report_view(report)
        raise KeyError(f"unknown experiment id: {experiment_id}")

    def wait(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._experiments.get(experiment_id)
        if entry is not None:
            entry["done"].wait()
            with entry["lock"]:
                return self.public_report(entry["report"])
        return self.get(experiment_id)

    def close(self) -> None:
        """Cancel owned cells using the shared-deadline policy above."""
        with self._admission_lock:
            self._closed = True
            with self._lock:
                entries = list(self._experiments.values())
            # Signal every experiment before waiting on any one coordinator.
            for entry in entries:
                entry["abort"].set()
        for entry in entries:
            entry["done"].wait()

    def _save(self, entry: dict[str, Any]) -> None:
        with entry["lock"]:
            _write_report(entry["directory"], entry["report"])

    def _coordinate(self, experiment_id: str) -> None:
        with self._lock:
            entry = self._experiments[experiment_id]
        report = entry["report"]
        active: list[dict[str, Any]] = []
        try:
            with entry["lock"]:
                report["status"] = "running"
            self._save(entry)
            context = multiprocessing.get_context("spawn")
            entry["cancelled"] = _Cancellation(context.RawValue("b", 0))
            self._drive(entry, context, active)
        except Exception as exc:  # noqa: BLE001 - always release wait/close callers
            with entry["lock"]:
                report["status"] = "faulted"
                report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if active:
                    self._stop_active(entry, active)
                else:
                    self._recover_ready(entry)
                self._fill_unstarted(entry)
                with entry["lock"]:
                    if report["status"] not in {"faulted", "interrupted"}:
                        report["status"] = "faulted" if report["failed"] else "completed"
                    if report["failed"]:
                        report["error"] = report["error"] or (
                            f"{report['failed']} of {report['total']} runs failed"
                        )
                    try:
                        self._aggregate_locked(entry)
                    except Exception as exc:  # noqa: BLE001 - no stale incompatible means
                        report["status"] = "faulted"
                        report["aggregation_error"] = f"{type(exc).__name__}: {exc}"
                        report["error"] = report["aggregation_error"]
                        for row in report["table"]:
                            row["metrics"] = {}
                        report["csv"] = self._csv_rows(report)
                self._save(entry)
            finally:
                # Even report I/O failure must not hang close()/wait().
                with self._lock:
                    self._experiments.pop(experiment_id, None)
                entry["done"].set()

    def _drive(
        self, entry: dict[str, Any], context: Any, active: list[dict[str, Any]]
    ) -> None:
        report = entry["report"]
        coordinates = iter(itertools.product(
            range(len(entry["configs"])), range(entry["seeds"])
        ))
        exhausted = False
        while True:
            self._collect(entry, active)
            if entry["abort"].is_set():
                # Ready outcomes are retained, but artifact parsing cannot hold
                # up termination of a blocked sibling in this bounded batch.
                if not active and len(report["runs"]) + len(entry["ready"]) == report["total"]:
                    self._recover_ready(entry)
                    return
                with entry["lock"]:
                    report["status"] = "interrupted"
                    report["error"] = report["error"] or "experiment cancelled"
                self._stop_active(entry, active)
                return
            if active:
                entry["abort"].wait(_CANCEL_POLL_S)
                continue
            if entry["ready"]:
                self._recover_ready(entry)
                with entry["lock"]:
                    self._aggregate_locked(entry)
                self._save(entry)
            while not exhausted and len(active) < entry["jobs"]:
                if entry["abort"].is_set():
                    break
                try:
                    config_index, seed = next(coordinates)
                except StopIteration:
                    exhausted = True
                    break
                arguments = self._arguments(entry, config_index, seed)
                output = entry["directory"] / f"cell-c{config_index}-s{seed}.json"
                process = _CellProcess(
                    context, arguments, entry["cancelled"], str(output),
                    name=f"{report['id']}-c{config_index}-s{seed}",
                )
                cell = {
                    "config_index": config_index,
                    "seed": seed,
                    "path": Path(arguments[2]),
                    "output": output,
                    "process": process,
                }
                try:
                    process.start()
                except Exception:
                    process.close()
                    raise
                active.append(cell)
            if exhausted and not active:
                return
            # Abort.wait wakes promptly. There are never more than jobs active
            # children or materialized submission arguments, and no result IPC.
            entry["abort"].wait(_CANCEL_POLL_S)

    def _stop_active(self, entry: dict[str, Any], active: list[dict[str, Any]]) -> None:
        """Cooperate, terminate, kill owned trees; never join forever."""
        entry["cancelling"] = True  # Parent-owned; never read a dead child's lock.
        entry["cancelled"].set()
        waiter = threading.Event()

        def collect_until(deadline: float) -> None:
            while active:
                self._collect(entry, active)
                remaining = deadline - time.monotonic()
                if not active or remaining <= 0:
                    return
                waiter.wait(min(_CANCEL_POLL_S, remaining))

        def signal_remaining(operation: str) -> None:
            for cell in active:
                try:
                    getattr(cell["process"], operation)()
                except OSError as exc:
                    # A process may exit between observation and signaling, or
                    # the OS may deny control. Keep the observed error separate
                    # from whatever outcome the run has already persisted.
                    cell.setdefault("control_errors", []).append(
                        f"{operation}: {type(exc).__name__}: {exc}"
                    )

        collect_until(time.monotonic() + _CANCEL_GRACE_S)
        signal_remaining("terminate")
        collect_until(time.monotonic() + _TERMINATE_GRACE_S)
        signal_remaining("kill")
        collect_until(time.monotonic() + _KILL_GRACE_S)
        # All process-control deadlines have finished before any potentially
        # large artifact recovery/index scan. No slow dead sibling can delay
        # signaling the batch's blocked live cells.
        self._recover_ready(entry)
        for cell in list(active):
            result = self._unconfirmed_cell(entry, cell)
            self._record(entry, cell["config_index"], cell["seed"], result)
            active.remove(cell)

    @staticmethod
    def _unconfirmed_cell(entry: dict[str, Any], cell: dict[str, Any]) -> dict[str, Any]:
        # OS-level unkillability or a lost supervisor is outside the process-wait
        # guarantee. Do not race an unconfirmed writer by editing its artifacts.
        error = "cell process tree exit unconfirmed; run artifacts left untouched"
        with entry["lock"]:
            entry["report"]["shutdown_error"] = error
        try:
            result = _run_result(cell["path"], collection_error=error)
        except (OSError, ValueError, KeyError):
            result = {
                "run": str(cell["path"]) if cell["path"].exists() else None,
                "run_id": None, "status": None, "error": None,
                "collection_error": error, "cut": None, "cursor": None,
                "kpis": {}, "kpi_meta": {},
            }
        # A cancellation request is not an observed terminal interruption.
        result["cancellation_requested"] = bool(entry.get("cancelling"))
        result["termination_confirmed"] = False
        result["attempted"] = True
        result["control_errors"] = cell.get("control_errors", [])
        mode = cell.get("supervision") or getattr(cell["process"], "supervision", None)
        if mode:
            result.update(_supervision_metadata(mode))
        return result

    def _fill_unstarted(self, entry: dict[str, Any]) -> None:
        recorded = {(run["config_id"], run["seed"]) for run in entry["report"]["runs"]}
        for index in range(len(entry["configs"])):
            for seed in range(entry["seeds"]):
                if (f"config-{index}", seed) in recorded:
                    continue
                self._record(entry, index, seed, {
                    "run": None, "run_id": None, "status": "interrupted",
                    "error": "experiment ended before this cell was submitted",
                    "collection_error": None, "attempted": False,
                    "cut": None, "cursor": None, "kpis": {}, "kpi_meta": {},
                })

    def _arguments(
        self, entry: dict[str, Any], config_index: int, seed: int
    ) -> tuple[Any, ...]:
        document = copy.deepcopy(entry["documents"][config_index])
        # Seed/horizon/pacing are applied to the pinned knob document BEFORE
        # the worker's final load_scenario expansion of lowalt generators.
        document["run"] = {
            **document["run"],
            "seed": seed,
            "pacing": "fast",
        }
        if entry["horizon_s"] is not None:
            document["run"]["until_ns"] = round(float(entry["horizon_s"]) * 1e9)
        template = {
            "id": entry["template_id"],
            "title": entry["report"]["template_title"],
            **(
                {"title_zh": entry["report"]["template_title_zh"]}
                if "template_title_zh" in entry["report"]
                else {}
            ),
            "knobs": copy.deepcopy(entry["configs"][config_index]),
        }
        for name, control in entry["controls"].items():
            template["knobs"][name] = document["run"][control]
        experiment_record = {
            "id": entry["report"]["id"],
            "contract": CONTRACT,
            "template_id": entry["template_id"],
        }
        directory = str(
            self.root
            / (
                f"{entry['template_id']}-{entry['report']['id']}"
                f"-c{config_index}-s{seed}"
            )
        )
        return (
            document,
            entry["bases"][config_index],
            directory,
            template,
            experiment_record,
            f"config-{config_index}",
            seed,
        )

    @staticmethod
    def _collect(entry: dict[str, Any], active: list[dict[str, Any]]) -> bool:
        """Reap supervisors and retain their scoped exit proof, without artifact I/O."""
        ready = entry.setdefault("ready", [])
        changed = False
        for cell in list(active):
            process = cell["process"]
            if process.is_alive():
                continue
            process.join(timeout=0)
            cell["termination_confirmed"] = getattr(process, "termination_confirmed", True)
            cell["supervision"] = getattr(process, "supervision", None)
            process.close()
            active.remove(cell)
            ready.append(cell)
            changed = True
        return changed

    def _recover_ready(self, entry: dict[str, Any]) -> None:
        """Recover a finished batch only after live siblings were controlled."""
        ready = entry.get("ready", [])
        while ready:
            cell = ready.pop(0)
            confirmed = cell.get("termination_confirmed", True)
            if not confirmed:
                result = self._unconfirmed_cell(entry, cell)
                self._record(entry, cell["config_index"], cell["seed"], result)
                continue
            try:
                result = self._recover_cell(entry, cell)
            except Exception as exc:  # noqa: BLE001 - preserve terminal status on recovery failure
                error = f"result recovery: {type(exc).__name__}: {exc}"
                try:
                    result = _run_result(cell["path"], collection_error=error)
                except (OSError, ValueError, KeyError):
                    result = {
                        "run": str(cell["path"]) if cell["path"].exists() else None,
                        "run_id": None, "status": None, "error": None,
                        "collection_error": error, "cut": None, "cursor": None,
                        "kpis": {}, "kpi_meta": {},
                    }
                if result["status"] not in _TERMINAL:
                    result["status"] = "interrupted" if entry.get("cancelling") else "faulted"
                    result["error"] = result.get("error") or error
            if cell.get("supervision"):
                result.update(_supervision_metadata(cell["supervision"]))
            result["termination_confirmed"] = True
            result["attempted"] = True
            result["cancellation_requested"] = bool(entry.get("cancelling"))
            if cell.get("control_errors"):
                result["control_errors"] = cell["control_errors"]
            self._record(entry, cell["config_index"], cell["seed"], result)

    @staticmethod
    def _recover_cell(entry: dict[str, Any], cell: dict[str, Any]) -> dict[str, Any]:
        """Retain durable terminal outcomes even when a child cannot return them."""
        payload: dict[str, Any] = {}
        error = None
        if cell["output"].is_file():
            try:
                payload = json.loads(cell["output"].read_text())
                if not isinstance(payload, dict):
                    raise ValueError("cell result must be an object")
            except (OSError, ValueError) as exc:
                error = f"result collection: {type(exc).__name__}: {exc}"
                payload = {}
        error = payload.get("worker_error") or error
        path = cell["path"]
        cancelled = bool(entry.get("cancelling"))
        if not (path / "manifest.json").is_file():
            return {
                "run": None, "run_id": None,
                "status": "interrupted" if cancelled else "faulted",
                "error": error or (
                    "experiment cancelled before run creation" if cancelled
                    else "cell exited without a persisted run outcome"
                ),
                "collection_error": error, "cut": None, "cursor": None,
                "kpis": {}, "kpi_meta": {},
            }
        _finish_run(
            path, "interrupted" if cancelled else "faulted",
            error or ("experiment cancelled" if cancelled else "cell exited unexpectedly"),
        )
        actual = _run_result(path)
        result = payload.get("result")
        if isinstance(result, dict):
            # The terminal manifest is authoritative, not process transport or
            # a projection exception. Preserve its original error and waiting.
            result = {**actual, **result, **{
                key: actual[key] for key in ("run", "run_id", "status", "error", "waiting")
            }}
            if result["status"] != "completed" or result.get("error"):
                result["kpis"], result["kpi_meta"] = {}, {}
            return result
        if actual["status"] == "completed":
            # It may have completed simulation but died in projection/cleanup
            # after persisting success. Never rerun projection on close's thread.
            actual["collection_error"] = error or (
                "completed run measurements unavailable after experiment cancellation"
                if cancelled else "completed run exited before publishing measurements"
            )
        elif error:
            actual["collection_error"] = error
        return actual

    @staticmethod
    def _record(
        entry: dict[str, Any], config_index: int, seed: int, result: dict[str, Any]
    ) -> None:
        with entry["lock"]:
            report = entry["report"]
            row = {
                **copy.deepcopy(result),
                "config_id": f"config-{config_index}", "seed": seed,
            }
            # Failure samples are not zero-valued KPI observations, even if a
            # stale/invalid worker result happens to contain numeric values.
            if row.get("status") != "completed" or row.get("error") or row.get("collection_error"):
                row["kpis"], row["kpi_meta"] = {}, {}
            report["runs"].append(row)
            mode = row.get("supervision")
            if mode:
                policy = report.setdefault("cancellation_policy", {})
                # Mixed capabilities must never upgrade an experiment's claim.
                if policy.get("supervision") != "best_effort":
                    policy.update(_supervision_metadata(mode))
                    policy["scope"] = (
                        "cell_process" if mode == "best_effort" else "owned_cell_process_trees"
                    )
            if row.get("status") == "completed" and not row.get("error") and not row.get("collection_error"):
                report["completed"] += 1
            else:
                report["failed"] += 1
                report["error"] = row.get("error") or row.get("collection_error") or report["error"]

    def _aggregate_locked(self, entry: dict[str, Any]) -> None:
        """Aggregate actual samples, validating descriptors before any means.

        Missing legacy definition/revision identities remain unknown; display
        defaults from METRICS are never evidence of a recorded metric revision.
        Matched seeds describe seeded simulation inputs, not live-model RNGs.
        """
        from .comparison import metric_compatibility, metric_identity
        from .kpis import METRICS

        report = entry["report"]
        by_config: dict[str, list[dict[str, Any]]] = {}
        descriptors: dict[str, list[dict[str, Any]]] = {}
        names = set(METRICS)
        outcome_counts: dict[str, int] = {}

        def exclusion_reason(run: dict[str, Any]) -> str | None:
            if run.get("status") != "completed":
                return run.get("status") or "unknown_outcome"
            if run.get("error"):
                return "run_error"
            if run.get("collection_error"):
                return "collection_error"
            return None

        def evidence(run: dict[str, Any]) -> dict[str, Any]:
            return {
                key: copy.deepcopy(run.get(key))
                for key in (
                    "config_id", "seed", "run", "run_id", "status", "cut", "cursor",
                    "basis", "error", "collection_error", "attempted",
                    "execution_outcome", "cleanup_interrupted", "cleanup_error",
                    "cancellation_requested", "termination_confirmed", "control_errors",
                    "supervision", "termination_scope", "cleanup_limitations",
                )
            }

        excluded_runs = []
        for run in report["runs"]:
            by_config.setdefault(run["config_id"], []).append(run)
            status = run.get("status") or "unknown"
            outcome_counts[status] = outcome_counts.get(status, 0) + 1
            reason = exclusion_reason(run)
            if reason is not None:
                excluded_runs.append({**evidence(run), "reason": reason})
                continue
            for name, value in (run.get("kpis") or {}).items():
                names.add(name)
                description = (run.get("kpi_meta") or {}).get(name) or {}
                descriptors.setdefault(name, []).append(description)
                if value is not None and (
                    type(value) not in (int, float) or not math.isfinite(value)
                ):
                    raise ValueError(f"Invalid measured KPI {name}: {value!r}")
        # Validate across all configurations as well as within each replicate
        # group. A null measurement still carries its known definition/unit.
        compatibility = {
            name: metric_compatibility(name, descriptors.get(name, []))
            for name in sorted(names)
        }
        new_metrics: dict[str, dict[str, Any]] = {}
        new_exclusions: dict[str, list[dict[str, Any]]] = {}
        for row in report["table"]:
            config_id = row["config_id"]
            runs = by_config.get(config_id, [])
            metrics: dict[str, dict[str, Any]] = {}
            requested = report.get("seeds", entry.get("seeds"))
            for name in sorted(names):
                values: list[float] = []
                samples: list[dict[str, Any]] = []
                exclusions: list[dict[str, Any]] = []
                descriptions: list[dict[str, Any]] = []
                for run in runs:
                    reason = exclusion_reason(run)
                    kpis = run.get("kpis") or {}
                    if reason is None:
                        if name not in kpis:
                            reason = "missing_measurement"
                        elif kpis[name] is None:
                            reason = "unmeasured"
                        description = (run.get("kpi_meta") or {}).get(name) or {}
                        if name in kpis:
                            descriptions.append(description)
                    if reason is not None:
                        exclusions.append({**evidence(run), "reason": reason})
                        continue
                    value = float(kpis[name])
                    values.append(value)
                    samples.append({
                        **evidence(run), "value": value,
                        "descriptor": metric_identity(description),
                    })
                count = len(values)
                mean = math.fsum(values) / count if count else None
                std: float | None = None
                if count > 1 and mean is not None:
                    variance = math.fsum((v - mean) ** 2 for v in values) / (count - 1)
                    std = math.sqrt(variance)
                identities = [metric_identity(item) for item in descriptions]
                identity = {
                    key: next((item[key] for item in identities if item[key] is not None), None)
                    for key in ("unit", "definition", "revision")
                }
                unit, label = METRICS.get(name, (None, name))
                label = next((item.get("label") for item in descriptions if item.get("label")), label)
                metrics[name] = {
                    "mean": mean, "std": std, "n": count,
                    "unit": identity["unit"] if identity["unit"] is not None else unit,
                    "label": label, "best": False,
                    "definition": identity["definition"],
                    "revision": identity["revision"],
                    "compatibility": metric_compatibility(name, descriptions),
                    "comparison_compatibility": compatibility[name],
                    "samples": samples,
                    "sample_exclusions": exclusions,
                    "sample_counts": {
                        "requested": requested, "included": count,
                        "excluded": len(exclusions),
                        "pending": max(0, requested - len(runs)) if type(requested) is int else None,
                    },
                }
            new_metrics[config_id] = metrics
            new_exclusions[config_id] = [
                item for item in excluded_runs if item["config_id"] == config_id
            ]
        # Publish the fully validated replacement atomically under entry's lock.
        for row in report["table"]:
            row["metrics"] = new_metrics[row["config_id"]]
            row["sample_exclusions"] = new_exclusions[row["config_id"]]
        report["alignment"] = "recorded_run_end"
        report["outcome_counts"] = outcome_counts
        report["sample_exclusions"] = excluded_runs
        report["metric_compatibility"] = compatibility
        # Legacy best flags remain descriptive; unknown compatibility is not an
        # assertion that model/resource revisions or horizons were controlled.
        for name in sorted(names):
            direction = _BEST_DIRECTIONS.get(name)
            if direction is None:
                continue
            candidates = [
                (row["config_id"], row["metrics"][name])
                for row in report["table"]
                if row["metrics"][name]["mean"] is not None
            ]
            if not candidates:
                continue
            _, best = min(
                candidates,
                key=lambda item: (
                    item[1]["mean"] if direction == "min" else -item[1]["mean"],
                    item[0],
                ),
            )
            for _, candidate in candidates:
                candidate["best"] = candidate["mean"] == best["mean"]
        report["csv"] = self._csv_rows(report)

    @staticmethod
    def _csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in report["table"]:
            if not row["metrics"]:
                rows.append(
                    {
                        "configuration": row["config_id"],
                        "knobs": json.dumps(row["knobs"], sort_keys=True),
                        "metric": "",
                        "mean": "",
                        "std": "",
                        "n": 0,
                        "unit": "",
                        "best": "",
                        "definition": "",
                        "revision": "",
                        "compatibility": "unknown",
                        "comparison_compatibility": "unknown",
                        "samples": "[]",
                        "sample_exclusions": json.dumps(row.get("sample_exclusions", []), sort_keys=True),
                    }
                )
                continue
            for name, metric in row["metrics"].items():
                rows.append(
                    {
                        "configuration": row["config_id"],
                        "knobs": json.dumps(row["knobs"], sort_keys=True),
                        "metric": name,
                        "mean": metric["mean"],
                        "std": metric["std"],
                        "n": metric["n"],
                        "unit": metric["unit"] or "",
                        "best": "yes" if metric["best"] else "",
                        "definition": json.dumps(metric.get("definition"), sort_keys=True),
                        "revision": json.dumps(metric.get("revision"), sort_keys=True),
                        "compatibility": metric.get("compatibility", "unknown"),
                        "comparison_compatibility": metric.get("comparison_compatibility", "unknown"),
                        "samples": json.dumps(metric.get("samples", []), sort_keys=True),
                        "sample_exclusions": json.dumps(metric.get("sample_exclusions", []), sort_keys=True),
                    }
                )
        return rows
