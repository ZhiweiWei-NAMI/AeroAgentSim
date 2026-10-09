"""Run ownership and execution around the independent kernel."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from aerokernel import CommandRequest, Journal, Kernel, Stamp
from aerokernel.engine import Engine, Partition
from aerokernel.errors import KernelError
from aerokernel.state import StateView

from aeroagentsim.scenario import Scenario, ScenarioError, load_scenario
from aeroagentsim.services.storage import RunStorage

from .ingress import IngressJournal, IngressReceipt
from .plugins import EngineBuild, EngineCatalog


class Simulation:
    """Bind arbitrary registered types and plugins; the kernel owns mutable state."""

    def __init__(self, scenario: Scenario, *, journal: Journal | None = None) -> None:
        self.scenario = scenario
        catalog = EngineCatalog()
        partitions: dict[str, Partition] = {}
        engines: list[Engine] = []
        self.ingress_targets: dict[str, set[str]] = {}
        for engine_id, item in sorted(scenario.engines.items()):
            build = EngineBuild(
                engine_id,
                item["config"],
                scenario.registry,
                scenario.manifest,
                scenario.manifest.entities,
                scenario.initial,
                partitions,
            )
            try:
                engine = catalog.build(item["plugin"], build)
            except (ValueError, KeyError, TypeError) as exc:
                raise ScenarioError(f"engines.{engine_id}.config: {exc}") from exc
            engines.append(engine)
            if (
                any(p.timing.mode == "real_time" for p in engine.partitions)
                and engine_id not in scenario.ingress
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
        self.ingress_journal = IngressJournal(Journal() if journal is None else journal)
        self._submission_lock = threading.Lock()
        self.kernel = Kernel(
            root_seed=scenario.seed,
            mappings=scenario.clock_mappings,
            journal=self.ingress_journal,
            ingress_policy=next(iter(scenario.ingress.values())).policy
            if scenario.ingress
            else None,
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

    def submit_live(
        self, engine_id: str, command: CommandRequest, stamp: Stamp
    ) -> IngressReceipt:
        binding = self.scenario.ingress.get(engine_id)
        if binding is None:
            raise KernelError(
                "INGRESS_POLICY", f"engines.{engine_id}.ingress: no declared policy"
            )
        if command.target not in self.ingress_targets[engine_id]:
            raise KernelError(
                "COMMAND_ROUTE",
                f"engines.{engine_id}.ingress: target belongs to another engine",
            )
        if stamp.mapping_id != binding.mapping_id:
            raise KernelError(
                "CLOCK_MAPPING",
                f"engines.{engine_id}.ingress: source mapping differs from declaration",
            )
        with self._submission_lock:
            self.ingress_journal.last_rejection = None
            try:
                mid = self.kernel.submit_live(command, stamp)
            except KernelError as exc:
                if (
                    exc.code != "LATE_INGRESS"
                    or self.ingress_journal.last_rejection is None
                ):
                    raise
                return self.ingress_journal.last_rejection
            return self.ingress_journal.receipts[mid]

    def advance_watermark(self, ns: int) -> None:
        self.kernel.advance_watermark(ns)

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
        self, engine_id: str, command: CommandRequest, stamp: Stamp
    ) -> IngressReceipt:
        if not self.started or self.closed:
            raise RuntimeError("live ingress requires a started open run session")
        receipt = self.simulation.submit_live(engine_id, command, stamp)
        self._index()
        return receipt

    def advance_watermark(self, ns: int) -> None:
        if not self.started or self.closed:
            raise RuntimeError("watermark requires a started open run session")
        self.simulation.advance_watermark(ns)
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
