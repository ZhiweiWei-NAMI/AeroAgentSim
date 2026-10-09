"""Failed construction releases actual services before a Simulation can be returned."""

import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from aerokernel import Journal, Kernel, Partition
from aerokernel.errors import KernelError
from aerokernel.sdk import ContextEngine

from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import ScenarioError, load_scenario


class ServiceEngine(ContextEngine):
    def __init__(self, identifier: str) -> None:
        super().__init__(Partition(identifier, identifier))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.mark.parametrize("failure", ["factory", "binding"])
def test_failed_construction_closes_all_returned_engine_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    scenario = replace(
        load_scenario(Path("scenarios/realtime-streams.yaml")),
        engines={name: {"plugin": "test-service", "config": {}} for name in ("a", "z")},
        ingress_streams=(),
    )
    built: list[ServiceEngine] = []

    def build(self: EngineCatalog, plugin: str, context: EngineBuild) -> ServiceEngine:
        if context.id == "z" and failure == "factory":
            raise ValueError("invalid later engine")
        engine = ServiceEngine(context.id)
        built.append(engine)
        return engine

    def failed_bind(self: Kernel, *args: object) -> None:
        raise KernelError("TEST_BIND", "invalid binding after construction")

    monkeypatch.setattr(EngineCatalog, "build", build)
    monkeypatch.setattr(Kernel, "bind", failed_bind)
    journal = Journal(tmp_path / "journal.jsonl")
    try:
        with pytest.raises(
            (ScenarioError, KernelError), match="invalid later engine|invalid binding"
        ):
            Simulation(scenario, journal=journal)
        assert len(built) == (1 if failure == "factory" else 2)
        assert all(engine.close_calls == 1 for engine in built)
        assert all(not engine.thread.is_alive() for engine in built)
        assert all(engine.server.fileno() == -1 for engine in built)
    finally:
        for engine in built:
            if engine.thread.is_alive():
                engine.close()
        journal.close()
