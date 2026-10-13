"""Bounded host shutdown and read-only live-history observation, without a server."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from aerokernel import BindingManifest, Kernel, MemoryRegistry

from aeroagentsim.services import cli
from aeroagentsim.services.app import close_workers, create_app
from aeroagentsim.services.storage import RunStorage


def test_serve_cli_sets_a_bounded_graceful_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = object()
    calls = []
    monkeypatch.setattr("aeroagentsim.services.app.create_app", lambda *args, **kwargs: app)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["aeroagentsim", "serve", "--out", str(tmp_path), "--port", "18482"])
    cli.main()
    assert calls == [((app,), {"host": "127.0.0.1", "port": 18482, "timeout_graceful_shutdown": 5})]


def test_uvicorn_shutdown_cancels_an_open_stream_before_lifespan_cleanup() -> None:
    async def check() -> None:
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def stream() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(stream())
        await entered.wait()
        server = uvicorn.Server(uvicorn.Config("unused:app", timeout_graceful_shutdown=0.01))
        server.servers = []  # Exercise shutdown only; no listener is created.
        server.server_state.tasks.add(task)

        class Lifespan:
            async def shutdown(self) -> None:
                await asyncio.wait_for(cancelled.wait(), 1)

        server.lifespan = Lifespan()
        await asyncio.wait_for(server.shutdown(), 2)
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and cancelled.is_set()

    asyncio.run(check())


def test_workers_share_shutdown_deadline_and_record_forced_interruption(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    workers: dict[str, tuple[Any, Any, Any, Any, Any]] = {}

    class Process:
        def __init__(self, name: str, forced: bool) -> None:
            self.name, self.forced, self.alive = name, forced, True
            self.killed = False

        def join(self, timeout: int) -> None:
            assert all(stopped.is_set() and not paused.is_set() for _, paused, stopped, _, _ in workers.values())
            if timeout == 10:
                # Sequential per-run joins would fail this barrier.
                barrier.wait(timeout=1)
                if not self.forced:
                    self.alive = False
                    RunStorage(tmp_path / self.name).status("stopped")

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            assert self.forced

        def kill(self) -> None:
            self.killed, self.alive = True, False

    for name, forced in (("clean", False), ("forced", True)):
        path = tmp_path / name
        path.mkdir()
        (path / "manifest.json").write_text(json.dumps({"id": name, "status": "waiting_for_input"}))
        paused, stopped = threading.Event(), threading.Event()
        paused.set()
        workers[name] = (Process(name, forced), paused, stopped, None, None)
    asyncio.run(close_workers(workers, tmp_path))
    assert RunStorage(tmp_path / "clean").metadata()["status"] == "stopped"
    forced = RunStorage(tmp_path / "forced").metadata()
    assert workers["forced"][0].killed is True
    assert forced["status"] == "interrupted" and "shutdown deadline" in forced["error"]


def test_live_history_stream_and_disconnect_do_not_change_simulation_input_state(tmp_path: Path) -> None:
    app = create_app(tmp_path / "records")
    path = tmp_path / "records" / "run"
    path.mkdir()
    registry = MemoryRegistry(())
    kernel = Kernel()
    kernel.bind(registry, BindingManifest("history", "0"), ())
    kernel.start()
    kernel.run_until(1)
    kernel.close()
    (path / "journal.jsonl").write_bytes(kernel.journal.bytes)
    (path / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    (path / "scenario.json").write_text(json.dumps({"registry": {"messages": []}}))
    (path / "manifest.json").write_text(json.dumps({"id": "run", "status": "waiting_for_input", "scenario": "history", "until_ns": "20"}))
    (path / "input-progress.json").write_text(json.dumps({"watermarks": {"operator": 1}}))
    storage = RunStorage(path)
    storage.index()
    before = {name: (path / name).read_bytes() for name in ("manifest.json", "input-progress.json", "journal.jsonl")}
    route = next(route for route in app.routes if getattr(route, "path", None) == "/v1/runs/{run_id}/stream")

    class Request:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return False

    async def inspect() -> None:
        response = await route.endpoint("run", Request(), from_index=1)
        async with asyncio.timeout(2):
            assert "event: status" in await anext(response.body_iterator)
            await response.body_iterator.aclose()

    asyncio.run(inspect())
    assert {name: (path / name).read_bytes() for name in before} == before
