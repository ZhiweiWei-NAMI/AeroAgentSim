"""Run ownership and execution around the independent kernel."""

from __future__ import annotations

import time
from pathlib import Path

from aerokernel import Journal, Kernel
from aerokernel.engine import Engine, Partition
from aerokernel.state import StateView

from aeroagentsim.scenario import Scenario, ScenarioError, load_scenario
from aeroagentsim.services.storage import RunStorage

from .kernel_compat import register_relation_records
from .plugins import EngineBuild, EngineCatalog


class Simulation:
    """Bind arbitrary registered types and plugins; the kernel owns mutable state."""

    def __init__(self, scenario: Scenario, *, journal: Journal | None = None) -> None:
        register_relation_records()
        self.scenario = scenario
        catalog = EngineCatalog()
        partitions: dict[str, Partition] = {}
        engines: list[Engine] = []
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
            journal=journal,
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

    def start(self) -> StateView:
        self.started_wall = time.perf_counter()
        self.storage.status("running")
        view = self.simulation.start()
        self.started = True
        self.storage.index()
        return view

    def run_until(self, ns: int) -> StateView:
        if not self.started:
            self.start()
        try:
            view = self.simulation.run_until(ns)
            self.now_ns = ns
            self.storage.index()
            return view
        except Exception as exc:
            self.storage.index()
            self.storage.status("faulted", error=f"{type(exc).__name__}: {exc}")
            raise

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
        self.storage.status("completed")
        return view

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.simulation.close()
        except Exception as exc:
            self.storage.status("faulted", error=f"cleanup: {exc}")
            raise
        finally:
            self.storage.index()

    def __enter__(self) -> RunSession:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
