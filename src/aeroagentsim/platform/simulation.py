"""Run ownership and execution around the independent kernel."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import cast

from aerokernel import CommandRequest, IngressReceipt, Journal, Kernel, Stamp
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

    def __init__(self, scenario: Scenario, *, journal: Journal | None = None) -> None:
        try:
            self._construct(scenario, journal=journal)
        except StopIteration as exc:
            raise ScenarioError(
                "scenario.bind: unexpected exhausted iterator during Simulation construction"
            ) from exc

    def _construct(self, scenario: Scenario, *, journal: Journal | None) -> None:
        self.scenario = scenario
        catalog = EngineCatalog()
        partitions: dict[str, Partition] = {}
        engines: list[Engine] = []
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
                    f"engines.{engine_id}.config: plugin exhausted an iterator during construction"
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
                    f"engines.{engine_id}.ingress: real_time requires an explicit ingress policy"
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
            configuration={
                "scenario_digest": scenario.digest,
                "registry_snapshot_digest": scenario.compiled.digest,
                "scenario": scenario.document,
            },
        )
        self.kernel.bind(scenario.registry, scenario.manifest, tuple(engines))

    def start(self) -> StateView:
        return self.kernel.start()

    def run_until(self, ns: int) -> StateView:
        return self.kernel.run_until(ns)

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
                    "ingress.stream_id: explicit stream required for ambiguous or absent binding",
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
    ) -> None:
        self.scenario = (
            scenario if isinstance(scenario, Scenario) else load_scenario(scenario)
        )
        self.storage = RunStorage(directory)
        if not prepared:
            self.storage.prepare(self.scenario)
        self.simulation = Simulation(
            self.scenario,
            journal=Journal(
                directory / "journal.jsonl",
                durability=self.scenario.document["outputs"]["durability"],
            ),
        )
        self.storage._atomic(
            "manifest.json",
            {**self.storage.metadata(), "epoch": self.scenario.manifest.epoch},
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
                    "behaviour_digests": [
                        {"package": p["digest"], "ir": p["ir_digest"]} for p in packages
                    ],
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

    def _status(self, status: str, *, error: str | None = None) -> None:
        with self._storage_lock:
            self.storage.status(status, error=error)

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

    def run_until(self, ns: int) -> StateView:
        if self.closed:
            raise RuntimeError("run session is closed")
        if not self.started:
            self.start()
        try:
            view = self.simulation.run_until(ns)
            self.now_ns = ns
            self._index()
            return view
        except Exception as exc:
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
                error=f"{previous + '; ' if previous else ''}cleanup: {type(failure).__name__}: {failure}",
            )
            raise failure

    def __enter__(self) -> RunSession:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
