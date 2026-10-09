"""Optional loopback REST/SSE host with independently owned kernel workers."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario

from .projector import header, project
from .storage import RunStorage
from .subjects import projection_context
from .worker import execute

TERMINAL = {"completed", "stopped", "faulted", "interrupted"}


def create_app(
    root: Path = Path("runs"),
    *,
    scenario_root: Path | None = None,
    frontend: Path | None = None,
    studio_root: Path | None = None,
) -> FastAPI:
    """Scenario paths are scoped to scenario_root; no arbitrary host-file API."""
    root = root.resolve()
    scenario_root = Path.cwd() if scenario_root is None else scenario_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Linux local P1 worker: fork preserves the compiled immutable registry without
    # pickling MappingProxyType or re-reading a concurrently changing source tree.
    context = multiprocessing.get_context("fork")
    workers: dict[str, tuple[Any, Any, Any]] = {}
    inputs: dict[str, tuple[Any, threading.Lock]] = {}
    for path in root.iterdir():
        if (path / "manifest.json").exists():
            storage = RunStorage(path)
            if storage.metadata()["status"] not in TERMINAL:
                storage.offset = 0
                storage.entries = []
                storage.index()
                storage.status(
                    "interrupted",
                    error="service restarted; journal replay is available; execution is not resumed",
                )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for process, paused, stopped in workers.values():
            stopped.set()
            paused.clear()
        for process, _, _ in workers.values():
            await asyncio.to_thread(process.join, 10)
            if process.is_alive():
                # Preserve an explicitly interrupted prefix rather than invent cleanup.
                process.terminate()
                await asyncio.to_thread(process.join, 2)
                RunStorage(root / process.name).status(
                    "interrupted", error="worker did not close within shutdown deadline"
                )
        for connection, input_lock in inputs.values():
            connection.close()

    app = FastAPI(title="AeroAgentSim", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:4179",
            "http://localhost:4179",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    def directory(run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise HTTPException(404, "Unknown run")
        path = root / run_id
        if not (path / "manifest.json").exists():
            raise HTTPException(404, "Unknown run")
        return path

    @app.post("/v1/runs", status_code=201)
    async def start(request: Request) -> dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex}"
        path = (root / run_id).resolve()
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TypeError("request body must be a scenario or scenario mapping")
            if "scenario_path" in body:
                path = (scenario_root / body["scenario_path"]).resolve()
                if not path.is_relative_to(scenario_root):
                    raise ValueError(
                        "scenario_path must be under configured scenario root"
                    )
                scenario = await asyncio.to_thread(load_scenario, path)
            else:
                document = body.get("scenario", body)
                document_base = scenario_root
                if (
                    hasattr(app.state, "studio")
                    and "studio_workspace" in body
                    and isinstance(document, dict)
                ):
                    from aeroagentsim.authoring.wire import normalize_wire

                    app.state.studio.get(body["studio_workspace"])
                    document_base = app.state.studio._base(
                        document, body["studio_workspace"]
                    )
                    document = await asyncio.to_thread(
                        normalize_wire,
                        document,
                        app.state.studio.catalog,
                        base=document_base,
                    )
                scenario = await asyncio.to_thread(
                    load_scenario, document, base=document_base
                )
            from .artifacts import scope_capture_storage

            scenario = scope_capture_storage(scenario, path)
            # Validate engine declarations/bindings before returning a created run.
            validation = Simulation(scenario, run_directory=path)
            validation.close()
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        if not path.is_relative_to(root):
            raise HTTPException(422, "run storage path escapes configured output root")
        storage = RunStorage(path)
        storage.prepare(scenario)
        if hasattr(app.state, "studio") and "studio_workspace" in body:
            from aeroagentsim.authoring.replay import snapshot_scene

            snapshot_scene(
                app.state.studio, scenario, path, body.get("studio_workspace")
            )
        paused, stopped = context.Event(), context.Event()
        host_input, worker_input = context.Pipe()
        process = context.Process(
            target=execute,
            args=(scenario, path, paused, stopped, worker_input),
            name=run_id,
        )
        process.start()
        workers[run_id] = (process, paused, stopped)
        inputs[run_id] = (host_input, threading.Lock())
        worker_input.close()
        return storage.metadata()

    @app.get("/v1/runs")
    def runs() -> list[dict[str, Any]]:
        return [
            RunStorage(path).metadata()
            for path in sorted(root.iterdir())
            if (path / "manifest.json").exists()
        ]

    @app.get("/v1/runs/{run_id}/header")
    def run_header(run_id: str, request: Request) -> dict[str, Any]:
        path = directory(run_id)
        result = header(path)
        if hasattr(app.state, "studio"):
            from aeroagentsim.authoring.replay import scene_header

            result.update(scene_header(path, str(request.base_url)))
        return result

    @app.get("/v1/runs/{run_id}/commits")
    def commits(
        run_id: str,
        from_index: int = Query(0, alias="from", ge=0),
        limit: int = Query(256, ge=1, le=4096),
    ) -> dict[str, Any]:
        path = directory(run_id)
        storage = RunStorage(path)
        metadata = storage.metadata()
        records = storage.records(max(1, from_index), limit)
        subjects = projection_context(path, max(1, from_index))
        result = [project(record, subjects=subjects) for record in records]
        return {
            "commits": result,
            "next": result[-1]["commitIndex"] + 1 if result else max(1, from_index),
            "status": metadata["status"],
            **(
                {"finalCursor": metadata["final_cursor"]}
                if "final_cursor" in metadata
                else {}
            ),
        }

    @app.get("/v1/runs/{run_id}/stream")
    async def stream(
        run_id: str, request: Request, from_index: int = Query(0, alias="from", ge=0)
    ) -> StreamingResponse:
        path = directory(run_id)
        last_id = request.headers.get("last-event-id")
        try:
            cursor = max(from_index, int(last_id) + 1 if last_id is not None else 1)
        except ValueError as exc:
            raise HTTPException(422, "Last-Event-ID must be a journal index") from exc

        async def tail() -> AsyncIterator[str]:
            nonlocal cursor
            subjects = projection_context(path, cursor)
            while not await request.is_disconnected():
                storage = RunStorage(path)
                records = storage.records(cursor, 256)
                for record in records:
                    commit = project(record, subjects=subjects)
                    cursor = commit["commitIndex"] + 1
                    yield f"id: {commit['commitIndex']}\nevent: commit\ndata: {json.dumps(commit, separators=(',', ':'))}\n\n"
                metadata = storage.metadata()
                status = metadata["status"]
                if not records and status in TERMINAL:
                    final_cursor = metadata["final_cursor"]
                    if cursor >= final_cursor:
                        yield f"event: end\ndata: {json.dumps({'status': status, 'finalCursor': final_cursor})}\n\n"
                        return
                if not records:
                    yield ": waiting\n\n"
                    await asyncio.sleep(0.1)

        return StreamingResponse(
            tail(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    def worker_input_call(run_id: str, operation: str, body: Any) -> dict[str, Any]:
        path = directory(run_id)
        if run_id not in inputs or RunStorage(path).metadata()["status"] in TERMINAL:
            raise HTTPException(409, "Run has no active worker")
        connection, lock = inputs[run_id]
        with lock:
            if not workers[run_id][0].is_alive():
                raise HTTPException(409, "Run worker has exited")
            try:
                connection.send((operation, body))
                while not connection.poll(0.05):
                    if not workers[run_id][0].is_alive():
                        raise HTTPException(
                            409,
                            "Worker exited before acknowledging input; inspect journal",
                        )
                reply = connection.recv()
            except (EOFError, BrokenPipeError, ConnectionResetError) as exc:
                raise HTTPException(
                    409, "Worker closed before acknowledging input; inspect journal"
                ) from exc
        if "error" in reply:
            raise HTTPException(422, reply["error"])
        return dict(reply["result"])

    @app.post("/v1/runs/{run_id}/ingress")
    async def live_ingress(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Admit typed input after worker-side injection-manifest validation."""
        return await asyncio.to_thread(worker_input_call, run_id, "ingress", body)

    @app.post("/v1/runs/{run_id}/watermark")
    async def watermark(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(worker_input_call, run_id, "watermark", body)

    from .artifacts import mount_artifacts

    mount_artifacts(app, directory, worker_input_call)

    @app.post("/v1/runs/{run_id}/{operation}")
    def control(run_id: str, operation: str) -> dict[str, Any]:
        path = directory(run_id)
        if operation not in {"pause", "resume", "stop"}:
            raise HTTPException(404, "Unknown control operation")
        storage = RunStorage(path)
        if run_id not in workers or storage.metadata()["status"] in TERMINAL:
            raise HTTPException(409, "Run has no active worker")
        _, paused, stopped = workers[run_id]
        if operation == "pause":
            paused.set()
        elif operation == "resume":
            paused.clear()
        else:
            stopped.set()
            paused.clear()
        return {
            "id": run_id,
            "requested": operation,
            "status": storage.metadata()["status"],
        }

    # Optional authoring hook: execution continues to use the existing run API.
    if studio_root is not None or os.environ.get("AEROAGENTSIM_STUDIO_ROOT"):
        from aeroagentsim.authoring.api import mount_studio

        mount_studio(
            app,
            studio_root
            if studio_root is not None
            else Path(os.environ["AEROAGENTSIM_STUDIO_ROOT"]),
            run_root=root,
        )

    if frontend is not None:
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        if not (frontend / "index.html").exists():
            raise ValueError(f"Frontend build missing: {frontend}; run npm run build")
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/runs/{path:path}")
        @app.get("/agents/{path:path}")
        @app.get("/runs")
        @app.get("/studio")
        @app.get("/")
        def page(path: str = "") -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app
