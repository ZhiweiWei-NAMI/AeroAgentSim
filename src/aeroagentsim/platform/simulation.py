"""Run ownership and execution around the independent kernel."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import NoReturn, cast

from aerokernel import (
    CommandRequest,
    IngressReceipt,
    IngressWait,
    Journal,
    Kernel,
    Stamp,
)
from aerokernel.engine import Engine, Partition
from aerokernel.errors import KernelError
from aerokernel.state import StateView
from aerokernel.values import canonical_json

from aeroagentsim.models import MotionModel, motion_model
from aeroagentsim.scenario import Scenario, ScenarioError, load_scenario

from .plugins import BUILTINS, EngineBuild, EngineCatalog


def _pin_provenance(scenario: Scenario, provenance: str | None) -> Scenario:
    effective = scenario.provenance if provenance is None else provenance
    if type(effective) is not str or effective not in {"lean", "full"}:
        raise ScenarioError("provenance: expected lean or full")
    if provenance is None:
        return scenario
    return replace(
        scenario,
        provenance=effective,
        document={**scenario.document, "provenance": effective},
    )


def _failure_details(
    failure: BaseException, *, excluding: BaseException | None = None
) -> str:
    """Include nested kernel cleanup diagnostics without retaining context cycles."""
    details: list[str] = []
    pending = [failure]
    seen = set() if excluding is None else {id(excluding)}
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        details.append(f"{type(error).__name__}: {error}")
        details.extend(getattr(error, "__notes__", ()))
        if isinstance(error, KernelError):
            pending.extend(
                cleanup for _, cleanup in error.context.get("cleanup_errors", ())
            )
        for linked in (error.__cause__, error.__context__):
            if linked is not None:
                pending.append(linked)
    return "; ".join(details)


def _retain_failure(
    primary: BaseException, secondary: BaseException, operation: str
) -> None:
    """Keep the primary error, with diagnostics also on Python 3.10."""
    if secondary is primary:
        return
    try:
        message = f"{operation}: {_failure_details(secondary, excluding=primary)}"
    except BaseException:
        # A plugin's exception formatter must not interrupt resource release.
        message = f"{operation}: {type(secondary).__name__} (diagnostics unavailable)"
    try:
        add_note = getattr(primary, "add_note", None)
        if callable(add_note):
            add_note(message)
            return
    except BaseException:
        pass  # Custom exception notes are also only best-effort diagnostics.
    try:
        context = RuntimeError(message)
        context.__context__ = primary.__context__
        primary.__context__ = context
    except BaseException:
        pass  # A custom exception may forbid attaching diagnostics entirely.


def _raise_primary(failure: BaseException) -> NoReturn:
    try:
        context = failure.__context__
    except BaseException:
        context = None
    try:
        raise failure
    except BaseException:
        # An active caller/secondary exception must not replace the Python 3.10
        # diagnostic chain when the primary is re-raised.
        try:
            failure.__context__ = context
        except BaseException:
            pass  # Custom exception diagnostics must not replace the primary.
        raise


def _is_builtin(catalog: EngineCatalog, plugin: str) -> bool:
    # This distribution also installs builtin factories as entry points. Only
    # the resolved callable, not its name or presence in entries, identifies one.
    return catalog.factory(plugin) is import_module(BUILTINS[plugin]).build


class Simulation:
    """Bind arbitrary registered types and plugins; the kernel owns mutable state."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        journal: Journal | None = None,
        run_directory: Path | None = None,
        provenance: str | None = None,
        catalog: EngineCatalog | None = None,
    ) -> None:
        self.engines: list[Engine] = []
        owned_journal = (
            Journal(codec="positional-deflate") if journal is None else journal
        )
        try:
            self._construct(
                scenario,
                journal=owned_journal,
                run_directory=run_directory,
                provenance=provenance,
                catalog=EngineCatalog() if catalog is None else catalog,
            )
        except BaseException as exc:
            # Ownership transfers to the kernel only after successful binding.
            # Failed binding may have registered just a subset of these engines.
            for engine in reversed(self.engines):
                try:
                    engine.close()
                except BaseException as cleanup:
                    _retain_failure(exc, cleanup, "construction engine cleanup")
            try:
                owned_journal.close()
            except BaseException as cleanup:
                _retain_failure(exc, cleanup, "construction journal cleanup")
            if isinstance(exc, StopIteration):
                raise ScenarioError(
                    "scenario.bind: unexpected exhausted iterator during "
                    "Simulation construction"
                ) from exc
            raise

    def _construct(
        self,
        scenario: Scenario,
        *,
        journal: Journal,
        run_directory: Path | None,
        provenance: str | None,
        catalog: EngineCatalog,
    ) -> None:
        # Pin overrides in the effective configuration as well as kernel policy;
        # never mutate the caller's authored scenario or historical header shape.
        scenario = _pin_provenance(scenario, provenance)
        self.scenario = scenario
        # Explicitly supplied journals keep their codec, including historical 1.x.
        partitions: dict[str, Partition] = {}
        self.ingress_targets: dict[str, set[str]] = {}
        # Allocate before factory order: planners and movers share object identity.
        models: dict[str, MotionModel] = {}
        for engine_id, item in sorted(scenario.engines.items()):
            if item["plugin"] == "kinematic":
                try:
                    if not _is_builtin(catalog, "kinematic"):
                        continue
                    models[engine_id] = motion_model(
                        item["config"], scenario.registry, scenario.manifest.entities
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    raise ScenarioError(
                        f"engines.{engine_id}.config: kinematic: {exc}"
                    ) from exc
        for engine_id, item in sorted(scenario.engines.items()):
            build = EngineBuild(
                engine_id,
                item["config"],
                scenario.registry,
                scenario.manifest,
                scenario.manifest.entities,
                scenario.initial,
                partitions,
                models,
                run_directory,
            )
            engine: Engine
            try:
                if (
                    item["plugin"] == "predicate"
                    and any(
                        item["plugin"] == "behaviour"
                        for item in scenario.engines.values()
                    )
                    and _is_builtin(catalog, "predicate")
                    and _is_builtin(catalog, "behaviour")
                ):
                    from aeroagentsim.engines.behaviour import RecordedPredicate

                    engine = cast(Engine, RecordedPredicate(build))
                else:
                    engine = catalog.build(item["plugin"], build)
            except ScenarioError:
                raise
            except StopIteration as exc:
                raise ScenarioError(
                    f"engines.{engine_id}.config: plugin exhausted an iterator "
                    "during construction"
                ) from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise ScenarioError(
                    f"engines.{engine_id}.config: {exc}",
                    code=getattr(exc, "code", None),
                ) from exc
            self.engines.append(engine)
            if any(p.timing.mode == "real_time" for p in engine.partitions) and not any(
                engine_id in stream.engine_ids for stream in scenario.ingress_streams
            ):
                raise ScenarioError(
                    f"engines.{engine_id}.ingress: real_time requires an "
                    "explicit ingress policy"
                )
            self.ingress_targets[engine_id] = {p.id for p in engine.partitions}
            for partition in engine.partitions:
                if partition.id in partitions:
                    raise ScenarioError(
                        f"engines.{engine_id}: duplicate partition {partition.id}"
                    )
                partitions[partition.id] = partition
        for ref in scenario.manifest.entities:
            owners = scenario.manifest.resolve(ref, scenario.registry, partitions)
            for field in scenario.initial[ref.id]:
                if field not in owners:
                    raise ScenarioError(
                        f"entities.{ref.id}.facts.{field}: no declared writer binding"
                    )
        self.kernel = Kernel(
            root_seed=scenario.seed,
            mappings=scenario.clock_mappings,
            journal=journal,
            ingress_streams=scenario.ingress_streams,
            provenance=scenario.provenance,
            configuration={
                "scenario": scenario.document,
            },
        )
        self.kernel.bind(scenario.registry, scenario.manifest, tuple(self.engines))

    def start(self) -> StateView:
        return self.kernel.start()

    def run_until(
        self, ns: int, *, on_wait: Callable[[IngressWait | None], bool] | None = None
    ) -> StateView:
        return self.kernel.run_until(ns, on_wait=on_wait)

    def resolve_stream(
        self, stream_id: str | None, engine_id: str | None = None
    ) -> str:
        """Resolve omitted Q1 addressing, without aliasing explicit stream IDs."""
        streams = self.kernel.ingress_streams
        if stream_id is not None:
            if stream_id not in streams:
                raise KernelError(
                    "INGRESS_STREAM", f"ingress.stream_id: unknown stream {stream_id!r}"
                )
            candidates = [stream_id]
        else:
            candidates = [
                stream.id
                for stream in streams.values()
                if engine_id is None or engine_id in stream.engine_ids
            ]
            if "default" in candidates:
                candidates = ["default"]
            if len(candidates) != 1:
                raise KernelError(
                    "INGRESS_STREAM",
                    "ingress.stream_id: explicit stream required for ambiguous "
                    "or absent binding",
                )
        selected = candidates[0]
        if engine_id is not None and engine_id not in streams[selected].engine_ids:
            raise KernelError(
                "INGRESS_BINDING",
                f"engines.{engine_id}.ingress: engine outside stream domain",
            )
        return selected

    def submit_live(
        self,
        engine_id: str | None,
        command: CommandRequest,
        stamp: Stamp,
        *,
        stream_id: str | None = None,
    ) -> IngressReceipt:
        if engine_id is not None:
            if engine_id not in self.ingress_targets:
                raise KernelError(
                    "COMMAND_ROUTE", f"engines.{engine_id}: unknown engine"
                )
            if command.target not in self.ingress_targets[engine_id]:
                raise KernelError(
                    "COMMAND_ROUTE",
                    f"engines.{engine_id}.ingress: target belongs to another engine",
                )
        selected = self.resolve_stream(stream_id, engine_id)
        return self.kernel.admit_live(command, stamp, stream_id=selected)

    def advance_watermark(self, ns: int, *, stream_id: str | None = None) -> None:
        self.kernel.advance_watermark(ns, stream_id=self.resolve_stream(stream_id))

    def advance_source_progress(
        self, stamp: Stamp, *, stream_id: str | None = None
    ) -> None:
        self.kernel.advance_source_progress(
            stamp, stream_id=self.resolve_stream(stream_id)
        )

    def close(self) -> None:
        self.kernel.close()


class RunSession:
    """Own artifacts and a fixed advance schedule independent of viewer reads."""

    def __init__(
        self,
        scenario: Scenario | Path | str,
        directory: Path,
        *,
        prepared: bool = False,
        journal_codec: str = "positional-deflate",
        provenance: str | None = None,
    ) -> None:
        # Storage is a host concern, not an import-time dependency of Simulation.
        from aeroagentsim.services.storage import RunStorage

        loaded = scenario if isinstance(scenario, Scenario) else load_scenario(scenario)
        self.scenario = _pin_provenance(loaded, provenance)
        self.storage = RunStorage(directory)
        if not prepared:
            self.storage.prepare(self.scenario)
        self.now_ns = 0
        self.closed = False
        self.started = False
        self.started_wall = 0.0
        self._failure: BaseException | None = None
        self._storage_lock = threading.RLock()
        self.simulation = Simulation(
            self.scenario,
            run_directory=directory,
            journal=Journal(
                directory / "journal.jsonl",
                durability=self.scenario.document["outputs"]["durability"],
                codec=journal_codec,
            ),
        )
        try:
            self._write_artifacts(
                directory, repin=prepared and provenance is not None
            )
        except BaseException as exc:
            # A failed constructor never returns ownership to the caller/worker.
            self._fault(exc)
            raise

    def _write_artifacts(self, directory: Path, *, repin: bool) -> None:
        document = canonical_json(self.scenario.document)
        digest = hashlib.sha256(document).hexdigest()
        if repin:
            # Prepared runs already have a snapshot of the authored default.
            # scenario.yaml remains the original source, not effective config.
            self.storage._atomic("scenario.json", self.scenario.document)
        self.storage._atomic(
            "manifest.json",
            {
                **self.storage.metadata(),
                "configuration_digest": digest,
                "epoch": self.scenario.manifest.epoch,
                "provenance": self.scenario.provenance,
                "journal_codec": self.simulation.kernel.journal.codec,
                "journal_major": self.simulation.kernel.header["major"],
                "journal_minor": self.simulation.kernel.header["minor"],
                **(
                    {"journal_codec_id": self.simulation.kernel.header["codec"]}
                    if "codec" in self.simulation.kernel.header
                    else {}
                ),
            },
        )
        # Host pins are additive/versioned run metadata, never new WAL fields.
        self.storage._atomic(
            "run-metadata.json",
            {
                "contract": "aeroagentsim.run-metadata/v1",
                "provenance": self.scenario.provenance,
                "configuration_digest": digest,
            },
        )
        packages = [
            p
            for engine in self.scenario.engines.values()
            if engine["plugin"] == "behaviour"
            for p in engine["config"]["packages"]
        ]
        if packages:
            metadata = self.storage.metadata()
            self.storage._atomic(
                "manifest.json",
                {
                    **metadata,
                    "epoch": self.scenario.manifest.epoch,
                    "provenance": self.scenario.provenance,
                    "behaviour_packages": [p["package_id"] for p in packages],
                },
            )
            (directory / "behaviour.ir.json").write_bytes(
                canonical_json(
                    {"packages": packages, "evaluator": "aerograph-predicate/1"}
                )
            )
        legacy = {
            name: item
            for name, item in self.scenario.engines.items()
            if item["plugin"] in {"workflow", "threshold"}
        }
        if legacy:
            from aeroagentsim.behaviours.compat import (
                compile_threshold,
                compile_workflow,
            )

            (directory / "behaviour.compat.ir.json").write_bytes(
                canonical_json(
                    {
                        name: (
                            compile_workflow(item["config"])
                            if item["plugin"] == "workflow"
                            else compile_threshold(item["config"])
                        )
                        for name, item in legacy.items()
                    }
                )
            )

    def _index(self) -> None:
        with self._storage_lock:
            self.storage.index()

    def _status(
        self,
        status: str,
        *,
        error: str | None = None,
        waiting: dict[str, object] | None = None,
    ) -> None:
        with self._storage_lock:
            self.storage.status(status, error=error, waiting=waiting)

    def report_wait(self, wait: IngressWait | None) -> None:
        """Publish actual input-wait metadata and its committed journal prefix."""
        try:
            with self._storage_lock:
                self.storage.index()
                self.storage.status(
                    "running" if wait is None else "waiting_for_input",
                    waiting=None
                    if wait is None
                    else {
                        "stream_ids": list(wait.stream_ids),
                        "at_ns": str(wait.target_ns),
                    },
                )
        except BaseException as exc:
            self._fault(exc)
            raise

    def _index_or_fault(self) -> None:
        try:
            self._index()
        except BaseException as exc:
            self._fault(exc)
            raise

    def submit_live(
        self,
        engine_id: str | None,
        command: CommandRequest,
        stamp: Stamp,
        *,
        stream_id: str | None = None,
    ) -> IngressReceipt:
        if not self.started or self.closed:
            raise RuntimeError("live ingress requires a started open run session")
        try:
            receipt = self.simulation.submit_live(
                engine_id, command, stamp, stream_id=stream_id
            )
        except BaseException as exc:
            # Ordinary ingress rejection is not a terminal session failure.
            if self.simulation.kernel.journal.failed:
                self._fault(exc)
            raise
        self._index_or_fault()
        return receipt

    def advance_watermark(self, ns: int, *, stream_id: str | None = None) -> None:
        if not self.started or self.closed:
            raise RuntimeError("watermark requires a started open run session")
        try:
            self.simulation.advance_watermark(ns, stream_id=stream_id)
        except BaseException as exc:
            if self.simulation.kernel.journal.failed:
                self._fault(exc)
            raise
        self._index_or_fault()

    def advance_source_progress(
        self, stamp: Stamp, *, stream_id: str | None = None
    ) -> None:
        if not self.started or self.closed:
            raise RuntimeError("source progress requires a started open run session")
        try:
            self.simulation.advance_source_progress(stamp, stream_id=stream_id)
        except BaseException as exc:
            if self.simulation.kernel.journal.failed:
                self._fault(exc)
            raise
        self._index_or_fault()

    def start(self) -> StateView:
        if self.closed:
            raise RuntimeError("run session is closed")
        if self.started:
            return self.simulation.kernel.view()
        self.started_wall = time.perf_counter()
        try:
            self._status("running")
            view = self.simulation.start()
            self.started = True
            self._index()
            return view
        except BaseException as exc:
            self._fault(exc)
            raise

    def run_until(
        self, ns: int, *, on_wait: Callable[[IngressWait | None], bool] | None = None
    ) -> StateView:
        if self.closed:
            raise RuntimeError("run session is closed")
        if not self.started:
            self.start()
        try:
            view = self.simulation.run_until(ns, on_wait=on_wait)
            self.now_ns = self.simulation.kernel.sealed_ns
            self._index()
            return view
        except BaseException as exc:
            primary = self._failure
            if self.closed and primary is not None and primary is not exc:
                # A publication callback may already have closed the journal;
                # a later kernel fault append must not replace its first error.
                _retain_failure(primary, exc, "execution finalization")
                self._fault(primary)
                _raise_primary(primary)
            if isinstance(exc, KernelError) and exc.code == "WATERMARK_TIMEOUT":
                self._close(exc)
                try:
                    self._status(
                        "input_timeout",
                        error=str(exc),
                        waiting={
                            "stream_ids": list(exc.context["stream_ids"]),
                            "at_ns": str(exc.context["target_ns"]),
                        },
                    )
                except BaseException as secondary:
                    _retain_failure(exc, secondary, "input timeout status")
                raise
            self._fault(exc)
            raise

    def _fault(self, failure: BaseException) -> None:
        with self._storage_lock:
            if self._failure is None:
                self._failure = failure
            primary = self._failure
        if primary is not failure:
            _retain_failure(primary, failure, "session fault")
        self._close(primary)
        try:
            self._status("faulted", error=_failure_details(primary))
        except BaseException as secondary:
            _retain_failure(primary, secondary, "fault status")

    def run(self) -> StateView:
        if not self.started:
            self.start()
        view = self.simulation.kernel.view()
        while self.now_ns < self.scenario.until_ns:
            view = self.run_until(
                min(self.now_ns + self.scenario.advance_ns, self.scenario.until_ns)
            )
            if self.scenario.pacing == "realtime":
                remaining = self.now_ns / 1e9 - (
                    time.perf_counter() - self.started_wall
                )
                if remaining > 0:
                    time.sleep(remaining)
        self.close()
        self._status("completed")
        return view

    def _close(self, primary: BaseException | None = None) -> BaseException | None:
        with self._storage_lock:
            if self.closed:
                return primary
            # Claim ownership before cleanup so repeated/reentrant close is safe.
            self.closed = True
        # Never hold the storage lock while acquiring the kernel ingress lock.
        for operation, cleanup in (
            ("simulation cleanup", self.simulation.close),
            ("run index", self._index),
        ):
            try:
                cleanup()
            except BaseException as exc:
                if primary is None:
                    primary = exc
                else:
                    _retain_failure(primary, exc, operation)
        return primary

    def close(self) -> None:
        failure = self._close()
        if failure is not None:
            try:
                previous = self.storage.metadata().get("error")
                self._status(
                    "faulted",
                    error=(
                        f"{previous + '; ' if previous else ''}cleanup: "
                        f"{_failure_details(failure)}"
                    ),
                )
            except BaseException as secondary:
                _retain_failure(failure, secondary, "cleanup status")
            _raise_primary(failure)

    def __enter__(self) -> RunSession:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *_: object) -> None:
        failure = _[1] if len(_) > 1 else None
        if isinstance(failure, BaseException):
            if not self.closed:
                self._fault(failure)
        else:
            self.close()
