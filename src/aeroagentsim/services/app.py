"""Optional loopback REST/SSE host with independently owned kernel workers."""

from __future__ import annotations

import asyncio
import json
import logging
import math
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
from fastapi.responses import FileResponse, StreamingResponse

from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import Scenario, load_scenario

from .projector import header
from .security import LOCAL_ORIGIN, RequestBoundary
from .storage import RunStorage
from .worker import execute

TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}


def create_app(
    root: Path = Path("runs"),
    *,
    scenario_root: Path | None = None,
    frontend: Path | None = None,
    studio_root: Path | None = None,
    templates_root: Path | None = None,
) -> FastAPI:
    """Scenario paths are scoped to scenario_root; no arbitrary host-file API."""
    root = root.resolve()
    scenario_root = Path.cwd() if scenario_root is None else scenario_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    from aeroagentsim.authoring.templates import TemplateRegistry

    templates = TemplateRegistry(templates_root)
    from .experiments import ExperimentManager

    experiments = ExperimentManager(root, templates)
    # Linux local worker: fork preserves the compiled immutable registry without
    # pickling MappingProxyType or re-reading a concurrently changing source tree.
    context = multiprocessing.get_context("fork")
    workers: dict[str, tuple[Any, Any, Any]] = {}
    inputs: dict[str, tuple[Any, threading.Lock]] = {}
    index_tasks: list[asyncio.Task[None]] = []
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
        await asyncio.to_thread(experiments.close)
        if index_tasks:
            await asyncio.gather(*index_tasks)

    app = FastAPI(title="AeroAgentSim", lifespan=lifespan)
    from .algorithm_catalog import mount_algorithm_catalog

    mount_algorithm_catalog(app)
    console_origin = os.environ.get("AEROAGENTSIM_CONSOLE_URL")
    app.add_middleware(RequestBoundary, console_origin=console_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[console_origin.rstrip("/")] if console_origin else [],
        allow_origin_regex=LOCAL_ORIGIN,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID", "Authorization"],
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
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TypeError("request body must be a scenario or scenario mapping")
            if "scenario_path" in body:
                scenario_file = (scenario_root / body["scenario_path"]).resolve()
                if not scenario_file.is_relative_to(scenario_root):
                    raise ValueError(
                        "scenario_path must be under configured scenario root"
                    )
                scenario = await asyncio.to_thread(load_scenario, scenario_file)
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
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        return await launch(scenario, workspace_id=body.get("studio_workspace"))

    async def warm_feed(path: Path, process: Any) -> None:
        from .feed_index import update

        try:
            while process.is_alive():
                await asyncio.to_thread(update, path)
                await asyncio.sleep(0.25)
            await asyncio.to_thread(update, path)
        except Exception:
            # The derived cache never changes worker success or source data.
            # A foreground projection request exposes this same error to callers.
            logging.getLogger(__name__).exception(
                "Viewer index failed for %s", path.name
            )

    async def launch(
        scenario: Scenario,
        *,
        workspace_id: str | None = None,
        template: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex}"
        path = (root / run_id).resolve()
        from .artifacts import scope_capture_storage

        try:
            scenario = scope_capture_storage(scenario, path)
            validation = await asyncio.to_thread(
                Simulation, scenario, run_directory=path
            )
            validation.close()
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        storage = RunStorage(path)
        storage.prepare(scenario)
        if template is not None:
            metadata = storage.metadata()
            metadata["template"] = template
            storage._atomic("manifest.json", metadata)
            from .template_execution import snapshot_template_scene

            snapshot_template_scene(template["id"], scenario.document, path)
        if workspace_id is not None and hasattr(app.state, "studio"):
            from aeroagentsim.authoring.replay import snapshot_scene

            snapshot_scene(app.state.studio, scenario, path, workspace_id)
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
        if template is not None:
            index_tasks.append(asyncio.create_task(warm_feed(path, process)))
        worker_input.close()
        # The console requests identity/injection configuration immediately after
        # creation. A created directory is not an acknowledged worker startup:
        # wait for its real, completely indexed WAL header before handing it out.
        while not await asyncio.to_thread(storage.records, 0, 1):
            if not process.is_alive():
                metadata = storage.metadata()
                if metadata["status"] in TERMINAL:
                    return metadata
                raise HTTPException(
                    500,
                    {
                        "id": run_id,
                        "error": "Worker exited before committing its WAL header",
                    },
                )
            await asyncio.sleep(0.01)
        return storage.metadata()

    def run_summary(path: Path) -> dict[str, Any]:
        from .feed_index import kpi_result as project_run

        metadata = RunStorage(path).metadata()
        document = json.loads((path / "scenario.json").read_text())
        template = metadata.get("template")
        return {
            **metadata,
            "title": template["title"] if template else document["id"],
            "knobs": template["knobs"] if template else {},
            "algorithms": document.get("algorithms", {}),
            "kpis": project_run(path, include_series=False)["final"],
            **(
                {"template_id": template["id"], "template_title": template["title"]}
                if template
                else {}
            ),
        }

    @app.get("/v1/runs")
    def runs() -> list[dict[str, Any]]:
        return [
            run_summary(path)
            for path in sorted(root.iterdir())
            if (path / "manifest.json").exists()
        ]

    @app.get("/v1/runs/compare")
    def compare(ids: str = Query(...)) -> dict[str, Any]:
        from .comparison import compare_runs

        identifiers = ids.split(",")
        if not 2 <= len(identifiers) <= 20 or len(set(identifiers)) != len(identifiers):
            raise HTTPException(
                422, "Provide 2–20 distinct run ids separated by commas"
            )
        rows = [run_summary(directory(run_id)) for run_id in identifiers]
        try:
            return compare_runs(rows)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/runs/{run_id}/kpis")
    def run_kpis(run_id: str) -> dict[str, Any]:
        from .feed_index import kpi_result as project_run

        return {**project_run(directory(run_id)), "run_id": run_id}

    @app.get("/v1/templates")
    def template_list() -> list[dict[str, Any]]:
        return [template_detail(item["id"]) for item in templates.list()]

    @app.get("/v1/templates/{template_id}")
    def template_detail(template_id: str) -> dict[str, Any]:
        try:
            metadata = templates.get(template_id)
            if "thumbnail" in metadata:
                metadata["thumbnail_url"] = f"/v1/templates/{template_id}/thumbnail"
            return metadata
        except KeyError as exc:
            raise HTTPException(404, "Unknown template") from exc

    @app.get("/v1/templates/{template_id}/thumbnail")
    def template_thumbnail(template_id: str) -> FileResponse:
        metadata = template_detail(template_id)
        if "thumbnail" not in metadata:
            raise HTTPException(404, "Template has no thumbnail")
        directory = (templates.root / template_id).resolve()
        image = (directory / metadata["thumbnail"]).resolve()
        if not image.is_relative_to(directory):
            raise HTTPException(422, "thumbnail must be inside its template directory")
        if not image.is_file():
            raise HTTPException(404, "Declared template thumbnail is missing")
        return FileResponse(image, media_type="image/png")

    async def template_input(template_id: str, request: Request) -> Any:
        template_detail(template_id)
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {"knobs", "workspace"}:
                raise ValueError("Body must contain knobs and optional workspace only")
            if "workspace" in body and type(body["workspace"]) is not bool:
                raise ValueError("workspace must be boolean")
            knobs = body.get("knobs", {})
            if not isinstance(knobs, dict):
                raise ValueError("knobs must be a JSON object")
            return body, await asyncio.to_thread(templates.apply, template_id, knobs)
        except (ValueError, TypeError, OSError, KeyError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def template_workspace(template_id: str, knobs: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(app.state, "studio"):
            from aeroagentsim.authoring.api import mount_studio

            mount_studio(app, root / "workspaces", run_root=root)
        return dict(
            app.state.studio.import_template(template_id, knobs, registry=templates)
        )

    @app.post("/v1/templates/{template_id}/workspace", status_code=201)
    async def create_template_workspace(
        template_id: str, request: Request
    ) -> dict[str, Any]:
        _, prepared = await template_input(template_id, request)
        try:
            draft = await asyncio.to_thread(
                template_workspace, template_id, prepared.knobs
            )
            return {"workspace_id": draft["id"]}
        except (ValueError, TypeError, OSError, KeyError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/templates/{template_id}/runs", status_code=201)
    async def run_template(template_id: str, request: Request) -> dict[str, Any]:
        from .template_execution import execution_document

        body, prepared = await template_input(template_id, request)
        workspace_id = None
        try:
            if body.get("workspace", False):
                draft = await asyncio.to_thread(
                    template_workspace, template_id, prepared.knobs
                )
                workspace_id = draft["id"]
                base = app.state.studio._base(draft["scenario"], workspace_id)
                document = await asyncio.to_thread(
                    execution_document, template_id, draft["scenario"], base
                )
                scenario = await asyncio.to_thread(
                    load_scenario,
                    document,
                    base=base,
                )
            else:
                document = await asyncio.to_thread(
                    execution_document, template_id, prepared.document, prepared.base
                )
                scenario = await asyncio.to_thread(
                    load_scenario, document, base=prepared.base
                )
        except (ValueError, TypeError, OSError, KeyError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        metadata = await launch(
            scenario,
            workspace_id=workspace_id,
            template={
                "id": template_id,
                "title": templates.get(template_id)["title"],
                "knobs": prepared.knobs,
            },
        )
        return {
            "run_id": metadata["id"],
            **({"workspace_id": workspace_id} if workspace_id is not None else {}),
        }

    @app.post("/v1/templates/{template_id}/experiments", status_code=201)
    async def create_experiment(template_id: str, request: Request) -> dict[str, Any]:
        template_detail(template_id)
        try:
            body = await request.json()
            allowed = {"vary", "seeds", "jobs", "horizon_s", "horizon", "knobs"}
            if not isinstance(body, dict) or set(body) - allowed:
                raise ValueError(
                    "Experiment body accepts vary, seeds, jobs, horizon_s and knobs"
                )
            if "horizon" in body and "horizon_s" in body:
                raise ValueError("Provide one horizon value")
            for key in ("seeds", "jobs"):
                if key in body and (type(body[key]) is not int or body[key] < 1):
                    raise ValueError(f"{key}: positive integer required")
            if "knobs" in body and not isinstance(body["knobs"], dict):
                raise ValueError("knobs: JSON object required")
            horizon = body.get("horizon_s", body.get("horizon"))
            if ("horizon" in body or "horizon_s" in body) and horizon is None:
                raise ValueError("horizon_s: positive finite seconds required")
            if horizon is not None and (
                type(horizon) not in (int, float)
                or not math.isfinite(horizon)
                or horizon <= 0
            ):
                raise ValueError("horizon_s: positive finite seconds required")
            return dict(
                await asyncio.to_thread(
                    experiments.create,
                    template_id,
                    vary=body.get("vary", {}),
                    seeds=body.get("seeds", 3),
                    jobs=body.get("jobs", 1),
                    horizon_s=horizon,
                    knobs=body.get("knobs"),
                )
            )
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/experiments/{experiment_id}")
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        if Path(experiment_id).name != experiment_id or experiment_id in {".", ".."}:
            raise HTTPException(404, "Unknown experiment")
        try:
            return dict(experiments.get(experiment_id))
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(404, "Unknown experiment") from exc

    @app.get("/v1/runs/{run_id}/header")
    def run_header(run_id: str, request: Request) -> dict[str, Any]:
        path = directory(run_id)
        result = header(path)
        if not os.environ.get("AEROAGENTSIM_AEROGRAPH_ROOT"):
            result["catalogNotice"] = "Type catalog limited to this scenario's snapshot"
        if hasattr(app.state, "studio"):
            from aeroagentsim.authoring.replay import scene_header

            result.update(scene_header(path, str(request.base_url)))
        if result.get("city") == "huangpu":
            from aeroagentsim.authoring.demo import city_presentation

            result["scene"] = city_presentation(str(request.base_url))
            for binding in result["presentation"]:
                visual = binding.get("visual", {})
                if str(visual.get("asset", "")).startswith("/v1/studio/demo-assets/"):
                    visual["asset"] = str(request.base_url).rstrip("/") + visual["asset"]
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
        from .feed_index import page

        result = page(path, max(1, from_index), limit)
        return {
            "commits": result,
            "next": result[-1]["commitIndex"] + 1 if result else max(1, from_index),
            "status": metadata["status"],
            **({"waiting": metadata["waiting"]} if "waiting" in metadata else {}),
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
            from .feed_index import page

            last_status: tuple[str, str] | None = None
            while not await request.is_disconnected():
                storage = RunStorage(path)
                records = await asyncio.to_thread(page, path, cursor, 256)
                for commit in records:
                    cursor = commit["commitIndex"] + 1
                    yield f"id: {commit['commitIndex']}\nevent: commit\ndata: {json.dumps(commit, separators=(',', ':'))}\n\n"
                metadata = storage.metadata()
                status = metadata["status"]
                state = {
                    "status": status,
                    **(
                        {"waiting": metadata["waiting"]}
                        if "waiting" in metadata
                        else {}
                    ),
                }
                state_key = (status, json.dumps(state, sort_keys=True))
                if state_key != last_status:
                    yield f"event: status\ndata: {json.dumps(state, separators=(',', ':'))}\n\n"
                    last_status = state_key
                if not records and status in TERMINAL:
                    final_cursor = metadata["final_cursor"]
                    if cursor >= final_cursor:
                        yield f"event: end\ndata: {json.dumps({**state, 'finalCursor': final_cursor})}\n\n"
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
        from fastapi.staticfiles import StaticFiles

        if not (frontend / "index.html").exists():
            raise ValueError(f"Frontend build missing: {frontend}; run npm run build")
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/inspect/{path:path}")
        @app.get("/inspect")
        @app.get("/aerograph")
        @app.get("/runs/{path:path}")
        @app.get("/agents/{path:path}")
        @app.get("/runs")
        @app.get("/studio")
        @app.get("/")
        def page(path: str = "") -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app
