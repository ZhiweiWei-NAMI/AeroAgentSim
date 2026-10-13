"""Scenario host for catalog plugins, optional containers and offline replay."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

from aerokernel import CommandRequest, Instant, Journal, replay

from aeroagentsim.platform.plugins import EngineCatalog
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import Scenario, load_scenario

from . import register
from .container import DockerContainer


class AdapterRun(Simulation):
    """The shared simulation with optional native-container lifecycle ownership."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        containers: bool = False,
        journal: Journal | None = None,
    ) -> None:
        self.resources = ExitStack()
        self.closed = False
        try:
            engines = {}
            for engine_id, item in sorted(scenario.engines.items()):
                config = dict(item["config"])
                if containers and "image" in config:
                    container = self.resources.enter_context(
                        DockerContainer(config["image"])
                    )
                    config["host"], config["port"] = container.endpoint
                engines[engine_id] = {**item, "config": config}
            super().__init__(
                replace(scenario, engines=engines),
                journal=Journal() if journal is None else journal,
                catalog=register(EngineCatalog()),
            )
        except BaseException:
            self.resources.close()
            raise

    def __enter__(self) -> AdapterRun:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            super().close()
        finally:
            self.resources.close()


def observed_flight(
    run: AdapterRun, *, deadline_ns: int = 100_000_000_000
) -> list[dict[str, Any]]:
    """Advance each next action only after an observed terminal kernel receipt."""
    phases: tuple[tuple[str, dict[str, Any]], ...] = (
        ("arm", {}),
        ("takeoff", {"altitude_m": 5.0}),
        ("goto", {"position_enu": [8.0, 0.0, 5.0], "yaw_deg": 90.0}),
        ("land", {}),
    )
    results: list[dict[str, Any]] = []
    for action, params in phases:
        view = run.kernel.view()
        command_id = run.kernel.submit(
            CommandRequest(
                f"adapters.px4_gazebo.{action}",
                "flight",
                Instant(view.instant.ns + 1),
                {"entity": "aircraft", **params},
            )
        )
        while True:
            current_ns = run.kernel.view().instant.ns
            if current_ns >= deadline_ns:
                raise RuntimeError(f"flight {action} missed simulated deadline")
            view = run.run_until(min(current_ns + 200_000_000, deadline_ns))
            status = view.action(command_id).status
            if status == "succeeded":
                results.append(
                    {
                        "action": action,
                        "command_id": command_id,
                        "terminal_ns": view.instant.ns,
                    }
                )
                break
            if status in {"failed", "rejected", "canceled"}:
                raise RuntimeError(
                    f"flight {action} returned {status}: {view.action(command_id)}"
                )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--containers", action="store_true")
    parser.add_argument("--flight", action="store_true")
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    scenario = load_scenario(args.scenario)
    journal = Journal(args.journal)
    with AdapterRun(scenario, containers=args.containers, journal=journal) as run:
        run.start()
        started = time.perf_counter()
        if args.flight:
            observed_flight(run, deadline_ns=scenario.until_ns)
        else:
            for ns in range(
                scenario.advance_ns, scenario.until_ns + 1, scenario.advance_ns
            ):
                run.run_until(ns)
            if run.kernel.view().instant.ns < scenario.until_ns:
                run.run_until(scenario.until_ns)
        simulated_ns = run.kernel.view().instant.ns
        wall_s = time.perf_counter() - started
    replayed = replay(args.journal)
    if replayed.view().instant.ns != simulated_ns or replayed.incomplete:
        raise RuntimeError("offline replay did not reconstruct completed run")
    print(
        json.dumps(
            {
                "simulated_ns": simulated_ns,
                "wall_s": wall_s,
                "rtf": simulated_ns / 1e9 / wall_s,
                "replay": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
