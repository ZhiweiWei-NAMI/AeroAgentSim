"""Run ownership and execution around the independent kernel."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

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
from aeroagentsim.services.storage import RunStorage

from .plugins import EngineBuild, EngineCatalog


class Simulation:
    """Bind arbitrary registered types and plugins; the kernel owns mutable state."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        journal: Journal | None = None,
        run_directory: Path | None = None,
        provenance: str | None = None,
    ) -> None:
        engines: list[Engine] = []
        owned_journal = (
            Journal(codec="positional-deflate") if journal is None else journal
        )
        try:
            self._construct(
                scenario,
                journal=owned_journal,
                engines=engines,
                run_directory=run_directory,
                provenance=provenance,
            )
        except Exception as exc:
            # Ownership transfers to the kernel only after successful binding.
            # Failed binding may have registered just a subset of these engines.
            failures: list[Exception] = []
            for engine in reversed(engines):
                try:
                    engine.close()
                except Exception as cleanup:  # noqa: BLE001 - close every engine before reporting
                    failures.append(cleanup)
            try:
                owned_journal.close()
            except Exception as cleanup:  # noqa: BLE001 - report journal cleanup with engine errors
                failures.append(cleanup)
            if failures:
                raise ScenarioError(
                    f"scenario.bind: {exc}; construction cleanup failed: "
                    + "; ".join(str(failure) for failure in failures),
                    code=getattr(exc, "code", None),
                ) from exc
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
        engines: list[Engine],
        run_directory: Path | None,
        provenance: str | None,
    ) -> None:
        self.scenario = scenario
        # The scenario setting is the pinned default; an explicit override must
        # be a valid value itself (no null/unknown coercion or fallback).
        effective = scenario.provenance if provenance is None else provenance
        if type(effective) is not str or effective not in {"lean", "full"}:
            raise ScenarioError("provenance: expected lean or full")
        # Explicitly supplied journals keep their codec, including historical 1.x.
        catalog = EngineCatalog()
        partitions: dict[str, Partition] = {}
        self.ingress_targets: dict[str, set[str]] = {}
        # Allocate before factory order: planners and movers share object identity.
        models: dict[str, MotionModel] = {}
        for engine_id, item in sorted(scenario.engines.items()):
            if item["plugin"] == "kinematic":
                try:
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
                if item["plugin"] == "predicate" and any(
                    item["plugin"] == "behaviour" for item in scenario.engines.values()
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
            engines.append(engine)
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
            provenance=effective,
            configuration={
                "scenario": scenario.document,
            },
        )
        self.kernel.bind(scenario.registry, scenario.manifest, tuple(engines))

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
        loaded = scenario if isinstance(scenario, Scenario) else load_scenario(scenario)
        # Pin the run-level override before persistence so the stored scenario
        # copy, the run manifest and the kernel all agree.
        if provenance is not None:
            if type(provenance) is not str or provenance not in {"lean", "full"}:
                raise ScenarioError("provenance: expected lean or full")
            loaded = replace(
                loaded,
                provenance=provenance,
                document={**loaded.document, "provenance": provenance},
            )
        self.scenario = loaded
        self.storage = RunStorage(directory)
        if not prepared:
            self.storage.prepare(self.scenario)
        self.simulation = Simulation(
            self.scenario,
            run_directory=directory,
            journal=Journal(
                directory / "journal.jsonl",
                durability=self.scenario.document["outputs"]["durability"],
                codec=journal_codec,
            ),
        )
        self.storage._atomic(
            "manifest.json",
            {
                **self.storage.metadata(),
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
        self.now_ns = 0
        self.closed = False
        self.started = False
        self.started_wall = 0.0
        self._storage_lock = threading.RLock()

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
        receipt = self.simulation.submit_live(
            engine_id, command, stamp, stream_id=stream_id
        )
        self._index()
        return receipt

    def advance_watermark(self, ns: int, *, stream_id: str | None = None) -> None:
        if not self.started or self.closed:
            raise RuntimeError("watermark requires a started open run session")
        self.simulation.advance_watermark(ns, stream_id=stream_id)
        self._index()

    def advance_source_progress(
        self, stamp: Stamp, *, stream_id: str | None = None
    ) -> None:
        if not self.started or self.closed:
            raise RuntimeError("source progress requires a started open run session")
        self.simulation.advance_source_progress(stamp, stream_id=stream_id)
        self._index()

    def start(self) -> StateView:
        if self.closed:
            raise RuntimeError("run session is closed")
        if self.started:
            return self.simulation.kernel.view()
        self.started_wall = time.perf_counter()
        self._status("running")
        try:
            view = self.simulation.start()
        except Exception as exc:
            self._fault(exc)
            raise
        self.started = True
        self._index()
        return view

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
        except Exception as exc:
            if isinstance(exc, KernelError) and exc.code == "WATERMARK_TIMEOUT":
                self.close()
                self._status(
                    "input_timeout",
                    error=str(exc),
                    waiting={
                        "stream_ids": list(exc.context["stream_ids"]),
                        "at_ns": str(exc.context["target_ns"]),
                    },
                )
                raise
            self._fault(exc)
            raise

    def _fault(self, failure: Exception) -> None:
        error = f"{type(failure).__name__}: {failure}"
        try:
            self.close()
        except Exception as cleanup:  # noqa: BLE001 - preserve both real failures
            error += f"; cleanup: {type(cleanup).__name__}: {cleanup}"
        self._index()
        self._status("faulted", error=error)

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

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: Exception | None = None
        try:
            self.simulation.close()
        except Exception as exc:  # noqa: BLE001 - finalize before terminal outcome
            failure = exc
        finally:
            self._index()
        if failure is not None:
            previous = self.storage.metadata().get("error")
            self._status(
                "faulted",
                error=(
                    f"{previous + '; ' if previous else ''}cleanup: "
                    f"{type(failure).__name__}: {failure}"
                ),
            )
            raise failure

    def __enter__(self) -> RunSession:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
