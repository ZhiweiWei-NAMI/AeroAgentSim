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

from .comparison import configuration_differences
from .projector import header
from .security import LOCAL_ORIGIN, RequestBoundary
from .storage import RunStorage, committed_prefix
from .worker import execute, superseded_watermark

TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
CONTROLS = {"start", "pause", "resume", "stop"}


async def close_workers(workers: dict[str, tuple[Any, Any, Any, Any, Any]], root: Path) -> None:
    """Signal every worker first; the shutdown deadline is shared across runs."""
    for process, paused, stopped, _, _ in workers.values():
        stopped.set()
        paused.clear()

    async def close(process: Any) -> None:
        await asyncio.to_thread(process.join, 10)
        forced = process.is_alive()
        if forced:
            process.terminate()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
        storage = RunStorage(root / process.name)
        if forced or storage.metadata()["status"] not in TERMINAL:
            storage.status("interrupted", error=(
                "worker did not close within shutdown deadline" if forced
                else "worker exited without recording a terminal status during shutdown"
            ))

    await asyncio.gather(*(close(process) for process, _, _, _, _ in workers.values()))


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
    workers: dict[str, tuple[Any, Any, Any, Any, Any]] = {}
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
        try:
            yield
        finally:
            await close_workers(workers, root)
            for connection, input_lock in inputs.values():
                connection.close()
            await asyncio.to_thread(experiments.close)
            if index_tasks:
                await asyncio.gather(*index_tasks)

    app = FastAPI(title="AeroAgentSim", lifespan=lifespan)
    app.state.templates = templates
    from .algorithm_catalog import mount_algorithm_catalog

    mount_algorithm_catalog(app)

    @app.get("/v1/assets/packs")
    def asset_packs() -> list[dict[str, Any]]:
        from aeroagentsim.assets import installed

        try:
            return installed()
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise HTTPException(503, str(error)) from error
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
            autostart = body.pop("autostart", None)
            if autostart is not None and type(autostart) is not bool:
                raise ValueError("autostart must be boolean")
            if "version" in body:
                return await launch_version(body, autostart=bool(autostart))
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

                    draft = app.state.studio.get(body["studio_workspace"])
                    revision = body.get("studio_revision")
                    if revision is not None and (type(revision) is not int or revision != draft["revision"]):
                        raise HTTPException(409, "Workspace changed; reopen and validate the current revision before running")
                    document_base = app.state.studio._base(
                        document, body["studio_workspace"]
                    )
                    document = await asyncio.to_thread(
                        normalize_wire,
                        document,
                        app.state.studio.catalog,
                        base=document_base,
                    )
                    saved = await asyncio.to_thread(
                        normalize_wire, draft["scenario"],
                        app.state.studio.catalog,
                        base=document_base,
                    )
                    if document != saved:
                        raise HTTPException(409, "Save and validate the current scenario before running its workspace revision")
                scenario = await asyncio.to_thread(
                    load_scenario, document, base=document_base
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        return await launch(
            scenario,
            workspace_id=body.get("studio_workspace"),
            workspace_revision=body.get("studio_revision"),
            autostart=True if autostart is None else autostart,
        )

    def template_pin(source: dict[str, Any]) -> dict[str, Any]:
        """Display titles of the template a version came from, beside its knobs."""
        try:
            metadata = templates.get(source["id"])
        except KeyError:
            return {"id": source["id"], "title": source["id"], "knobs": source.get("knobs", {})}
        return {
            "id": source["id"],
            "title": metadata["title"],
            **({"title_zh": metadata["title_zh"]} if "title_zh" in metadata else {}),
            "knobs": source.get("knobs", {}),
        }

    async def launch_version(body: dict[str, Any], *, autostart: bool) -> dict[str, Any]:
        """Run one immutable saved workspace version; initialized unless autostart."""
        if not hasattr(app.state, "studio"):
            raise HTTPException(501, "Studio workspaces are not mounted on this server")
        if set(body) - {"studio_workspace", "version"}:
            raise HTTPException(422, "A version run accepts studio_workspace, version and autostart only")
        workspace_id, version = body.get("studio_workspace"), body.get("version")
        if not isinstance(workspace_id, str) or type(version) is not int:
            raise HTTPException(422, "studio_workspace (string) and version (integer) are required")
        try:
            frozen = await asyncio.to_thread(app.state.studio.version, workspace_id, version)
            scenario = await asyncio.to_thread(
                load_scenario, frozen["scenario"], base=app.state.studio.version_directory(workspace_id, version)
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        return await launch(
            scenario,
            workspace_id=workspace_id,
            workspace_version=frozen,
            autostart=autostart,
        )

    async def warm_feed(path: Path, process: Any) -> None:
        from .feed_index import update

        try:
            while process.is_alive():
                prefix = committed_prefix(path)
                if prefix is None or prefix["index"] < 1:
                    # Startup has not acknowledged a world cut yet. Scanning
                    # visible WAL or building a projector now duplicates the
                    # cold initialization and can race the first publication.
                    await asyncio.sleep(0.02)
                    continue
                complete = await asyncio.to_thread(update, path, max_batches=1)
                await asyncio.sleep(0.25 if complete else 0.02)
            while not await asyncio.to_thread(update, path, max_batches=1):
                await asyncio.sleep(0.02)
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
        workspace_revision: int | None = None,
        workspace_version: dict[str, Any] | None = None,
        template: dict[str, Any] | None = None,
        autostart: bool = True,
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
        workspace_metadata: dict[str, Any] = {}
        if workspace_version is not None:
            draft = app.state.studio.get(workspace_id)
            workspace_metadata = {
                "studio_workspace": workspace_id,
                "studio_revision": workspace_version["revision"],
                "workspace_version": workspace_version["version"],
                "workspace_digest": workspace_version["digest"],
                "workspace_name": draft["name"],
                **({"template": template_pin(workspace_version["template"])} if workspace_version.get("template") else {}),
                **({"derived_from": workspace_version["derived_from"]} if workspace_version.get("derived_from") else {}),
            }
        elif workspace_id is not None and hasattr(app.state, "studio"):
            draft = app.state.studio.get(workspace_id)
            revision = draft["revision"]
            if workspace_revision is not None and (
                type(workspace_revision) is not int or workspace_revision != revision
            ):
                raise HTTPException(
                    409,
                    "Workspace changed; reopen and validate the current revision before running",
                )
            workspace_metadata = {
                "studio_workspace": workspace_id,
                "studio_revision": revision,
                "workspace_name": draft["name"],
            }
        storage = RunStorage(path)
        storage.prepare(scenario)
        if workspace_metadata:
            storage._atomic(
                "manifest.json", {**storage.metadata(), **workspace_metadata}
            )
        if template is not None:
            metadata = storage.metadata()
            metadata["template"] = template
            storage._atomic("manifest.json", metadata)
            from .template_execution import snapshot_template_scene

            snapshot_template_scene(template["id"], scenario.document, path)
        if workspace_id is not None and hasattr(app.state, "studio"):
            from aeroagentsim.authoring.replay import snapshot_scene

            snapshot_scene(app.state.studio, scenario, path, workspace_id, workspace_version)
        paused, stopped, started = context.Event(), context.Event(), context.Event()
        # Host pacing factor: simulated seconds per wall second; 0 = as fast as possible.
        speed = context.Value("d", 1.0 if scenario.pacing == "realtime" else 0.0)
        if autostart:
            started.set()
        host_input, worker_input = context.Pipe()
        process = context.Process(
            target=execute,
            args=(scenario, path, paused, stopped, worker_input, started, speed),
            name=run_id,
        )
        process.start()
        workers[run_id] = (process, paused, stopped, started, speed)
        inputs[run_id] = (host_input, threading.Lock())
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
        # An initialized run is handed out only after it reports that state.
        while not autostart and storage.metadata()["status"] not in {"initialized", *TERMINAL}:
            if not process.is_alive():
                break
            await asyncio.sleep(0.01)
        return storage.metadata()

    def run_summary(path: Path, *, include_kpis: bool = True) -> dict[str, Any]:
        import math

        from .feed_index import kpi_result as project_run
        from .feed_index import results_state
        from .storage import summary_configuration

        metadata = json.loads((path / "manifest.json").read_text())
        document = summary_configuration(path)
        template = metadata.get("template")
        knobs: dict[str, Any] = {}
        if template:
            # Template metadata records the controls originally applied. Other
            # editors can change their sources before the version is pinned.
            # Read the immutable run document, never the current workspace or defaults.
            pinned = json.loads((path / "scenario.json").read_text())
            declared = next((item["knobs"] for item in templates.list() if item["id"] == template["id"]), [])
            missing = object()

            def at(pointer: str) -> Any:
                value: Any = pinned
                for part in pointer[1:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    if isinstance(value, dict):
                        value = value.get(part, missing)
                    elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                        value = value[int(part)]
                    else:
                        return missing
                return value

            def numeric(value: Any) -> bool:
                return type(value) in (int, float) and math.isfinite(value)

            engines = pinned.get("engines", {})
            for knob in declared:
                value: Any = missing
                hook = knob.get("apply")
                if "pointer" in knob:
                    value = at(knob["pointer"])
                elif hook in {"traffic.wind_east", "traffic.wind_north"}:
                    motion = engines.get("air_motion", {})
                    model = motion.get("config", {}).get("consumption_model", {})
                    if motion.get("plugin") == "kinematic" and model.get("plugin") == "wind":
                        source = model.get("config", {})
                        profiles = [engine.get("config", {}).get("profiles", {}).get(source.get("entity"), {}).get(source.get("field"))
                                    for engine in engines.values() if engine.get("plugin") == "environment"]
                        profiles = [profile for profile in profiles if profile is not None]
                        if len(profiles) == 1 and isinstance(profiles[0], dict) and profiles[0].get("mode") in {"calm", "constant", "gust"}:
                            vector = profiles[0].get("value")
                            row = source.get("vector_index")
                            if row is not None:
                                vector = vector[row] if type(row) is int and isinstance(vector, list) and 0 <= row < len(vector) else missing
                            if isinstance(vector, list) and len(vector) in {2, 3} and all(numeric(component) for component in vector):
                                value = vector[0 if hook == "traffic.wind_east" else 1]
                elif hook == "traffic.physics_hz":
                    road = at("/engines/road_motion/config/step_ns")
                    air = at("/engines/air_motion/config/step_ns")
                    if type(road) is int and road > 0 and air == road:
                        frequency = 1e9 / road
                        rounded = round(frequency, 6)
                        value = rounded if rounded > 0 and round(1e9 / rounded) == road else frequency
                elif hook == "lowalt.operator_mode":
                    operator = [stream for stream in pinned.get("ingress_streams", []) if stream.get("id") == "operator"]
                    until = at("/run/until_ns")
                    if len(operator) == 1 and type(until) is int:
                        watermark = operator[0].get("initial_watermark_ns")
                        if type(watermark) is int:
                            value = "interactive" if watermark < until else "scheduled"
                elif hook == "traffic.accident_time":
                    durations = [action.get("duration_ns") for package in pinned.get("behaviours", [])
                                 for chain_id, chain in package.get("document", package).get("chains", {}).items() if chain_id == "traffic.incident_report"
                                 for transition in chain.get("transitions", []) if transition.get("id") == "schedule"
                                 for action in transition.get("actions", []) if action.get("id") == "accident-time"]
                    if len(durations) == 1 and type(durations[0]) is int:
                        value = durations[0] / 1e9
                elif hook == "traffic.decision_mode":
                    decisions = engines.get("decisions", {})
                    config = decisions.get("config", {})
                    if decisions.get("plugin") == "traffic_decisions" and config.get("mode") == "stub":
                        value = "scripted"
                    elif decisions.get("plugin") == "langgraph" and config.get("provider", {}).get("mode") == "live":
                        value = "live"
                # Unresolved/changed compound sources are omitted, never filled
                # with historical template values. Their exact configuration
                # remains available in the pinned configuration comparison.
                if type(value) in (str, bool) or numeric(value):
                    knobs[knob["id"]] = value
        kpis = project_run(path, include_series=False) if include_kpis else None
        return {
            **metadata,
            **results_state(path, metadata),
            "title": template["title"] if template else document["id"],
            **(
                {
                    "title_zh": template["title_zh"],
                    "template_title_zh": template["title_zh"],
                }
                if template and "title_zh" in template
                else {}
            ),
            "knobs": knobs,
            "algorithms": document.get("algorithms", {}),
            **(
                {"kpis": kpis["final"]}
                if kpis is not None
                else {}
            ),
            **(
                {"template_id": template["id"], "template_title": template["title"]}
                if template
                else {}
            ),
        }

    @app.get("/v1/runs")
    def runs(include_kpis: bool = True) -> list[dict[str, Any]]:
        return [
            run_summary(path, include_kpis=include_kpis)
            for path in sorted(
                (path for path in root.iterdir() if (path / "manifest.json").exists()),
                key=lambda path: (
                    (path / "scenario.json").stat().st_mtime_ns,
                    path.name,
                ),
            )
        ]

    @app.get("/v1/runs/compare")
    def compare(ids: str = Query(...), include_kpis: bool = True) -> dict[str, Any]:
        from aerokernel.codec import decode_record

        from .comparison import compare_runs

        identifiers = ids.split(",")
        if not 2 <= len(identifiers) <= 20 or len(set(identifiers)) != len(identifiers):
            raise HTTPException(
                422, "Provide 2–20 distinct run ids separated by commas"
            )
        paths = {run_id: directory(run_id) for run_id in identifiers}
        rows = [run_summary(path, include_kpis=include_kpis) for path in paths.values()]
        recorded_ends = {}
        for run_id, path in paths.items():
            storage = RunStorage(path)
            storage.refresh_committed()
            if not storage.entries or storage.entries[-1]["index"] == 0:
                raise HTTPException(409, f"Run {run_id} has not committed a world state yet")
            record = storage.records(storage.entries[-1]["index"], 1)[0]
            instant = decode_record(record["instant"])
            recorded_ends[run_id] = {"at_ns": str(instant.ns), "commit_index": record["index"],
                                     "status": storage.metadata()["status"]}
        configuration = {
            # compare_runs reports each run's own final measurements; it does
            # not truncate unequal runs to a common simulated-time horizon.
            "alignment": "recorded_run_end",
            "recorded_ends": recorded_ends,
            "config_diff": configuration_differences({
                run_id: json.loads((path / "scenario.json").read_text())
                for run_id, path in paths.items()
            }),
        }
        if not include_kpis:
            return {"runs": rows, **configuration}
        try:
            return {**compare_runs(rows), **configuration}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/runs/{run_id}")
    def run_record(run_id: str, include_kpis: bool = False) -> dict[str, Any]:
        return run_summary(directory(run_id), include_kpis=include_kpis)

    @app.get("/v1/runs/{run_id}/admissions")
    def run_admissions(run_id: str) -> list[dict[str, Any]]:
        from .admissions import admissions

        return admissions(directory(run_id))

    @app.post("/v1/runs/{run_id}/derive-workspace", status_code=201)
    async def derive_workspace(run_id: str, request: Request) -> dict[str, Any]:
        """Modify and retry: a new draft from this run's configuration."""
        path = directory(run_id)
        body = await request.json() if await request.body() else {}
        if not isinstance(body, dict) or set(body) - {"name"}:
            raise HTTPException(422, "derive-workspace accepts an optional name only")
        if not hasattr(app.state, "studio"):
            from aeroagentsim.authoring.api import mount_studio

            mount_studio(app, root / "workspaces", run_root=root)
            from aeroagentsim.assistant.api import mount_assistant

            mount_assistant(app, run_root=root)
        try:
            draft = await asyncio.to_thread(
                app.state.studio.derive, path, body.get("name"), registry=templates
            )
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TypeError, OSError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"workspace_id": draft["id"], "derived_from": draft["derived_from"]}

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

    @app.get("/v1/templates/{template_id}/guide")
    def template_guide(template_id: str) -> dict[str, Any]:
        """The template's own README, for the scenario guide in the console."""
        template_detail(template_id)
        directory = (templates.root / template_id).resolve()
        guide = directory / "README.md"
        if not guide.is_file():
            raise HTTPException(404, "Template has no guide")
        return {"id": template_id, "markdown": guide.read_text(encoding="utf-8")}

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
            from aeroagentsim.assistant.api import mount_assistant

            mount_assistant(app, run_root=root)
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
            template=prepared.run_metadata(),
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
                    visual["asset"] = (
                        str(request.base_url).rstrip("/") + visual["asset"]
                    )
            for model in result.get("modelCatalog", {}).values():
                for key in ("url", "lodUrl"):
                    if key in model:
                        model[key] = str(request.base_url).rstrip("/") + model[key]
        return result

    @app.get("/v1/runs/{run_id}/commits")
    def commits(
        run_id: str,
        from_index: int = Query(0, alias="from", ge=0),
        limit: int = Query(256, ge=1, le=4096),
    ) -> dict[str, Any]:
        path = directory(run_id)
        metadata = json.loads((path / "manifest.json").read_text())
        from .feed_index import page, results_state

        result = page(path, max(1, from_index), limit)
        return {
            "commits": result,
            **results_state(path, metadata),
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
            from .feed_index import results_state, stream_page

            last_status: tuple[str, str] | None = None
            def projected_frames(start: int) -> list[tuple[int, str]]:
                return [
                    (commit["commitIndex"], f"id: {commit['commitIndex']}\nevent: commit\ndata: {json.dumps(commit, separators=(',', ':'))}\n\n")
                    for commit in stream_page(path, start, 32)
                ]

            projection: asyncio.Task[list[tuple[int, str]]] | None = None
            try:
                while not await request.is_disconnected():
                    # Deliver a small prefix immediately instead of making the
                    # first live cut wait for hundreds of subsequent cuts.
                    prefix = committed_prefix(path)
                    # Idle live polls need no SQLite transaction, source
                    # signature checks, or projector task. The acknowledged
                    # coordinate tells us whether any real cut is available.
                    projection = (
                        asyncio.create_task(asyncio.to_thread(projected_frames, cursor))
                        if prefix is None or prefix["index"] >= cursor else None
                    )
                    while True:
                        # Controls must not wait behind a large projection or a
                        # replay backlog. Their small manifest is authoritative.
                        metadata = json.loads((path / "manifest.json").read_text())
                        status = metadata["status"]
                        state = {
                            "status": status,
                            **await asyncio.to_thread(results_state, path, metadata),
                            **({"waiting": metadata["waiting"]} if "waiting" in metadata else {}),
                        }
                        state_key = (status, json.dumps(state, sort_keys=True))
                        if state_key != last_status:
                            yield f"event: status\ndata: {json.dumps(state, separators=(',', ':'))}\n\n"
                            last_status = state_key
                        if projection is None or projection.done():
                            break
                        await asyncio.wait({projection}, timeout=0.02)
                        if await request.is_disconnected():
                            return
                    records = projection.result() if projection is not None else []
                    projection = None
                    for index, frame in records:
                        cursor = index + 1
                        yield frame
                    if not records and status in TERMINAL:
                        final_cursor = metadata["final_cursor"]
                        if cursor >= final_cursor:
                            state.update(await asyncio.to_thread(results_state, path, metadata))
                            yield f"event: end\ndata: {json.dumps({**state, 'finalCursor': final_cursor})}\n\n"
                            return
                    if not records:
                        yield ": waiting\n\n"
                        await asyncio.sleep(0.02)
            finally:
                if projection is not None:
                    projection.cancel()
                    await asyncio.gather(projection, return_exceptions=True)

        return StreamingResponse(
            tail(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    def worker_input_call(run_id: str, operation: str, body: Any) -> dict[str, Any]:
        path = directory(run_id)
        status = json.loads((path / "manifest.json").read_text())["status"]

        def closed_progress(state: str) -> dict[str, Any] | None:
            if state not in TERMINAL or not (path / "input-progress.json").is_file():
                return None
            watermarks = json.loads((path / "input-progress.json").read_text())["watermarks"]
            if operation == "watermark":
                try:
                    return superseded_watermark(body, watermarks)
                except (ValueError, TypeError) as exc:
                    raise HTTPException(422, str(exc)) from exc
            if operation != "progress":
                return None
            # A queued GET can cross the worker's final publication. Read its
            # retained real frontier rather than rejecting that read with 409.
            return {
                "contract": "aeroagentsim.input-progress/v1",
                "watermarks": watermarks,
                "status": state,
                "pause_requested": False,
            }

        final_progress = closed_progress(status)
        if final_progress is not None:
            return final_progress
        if run_id not in inputs or status in TERMINAL:
            raise HTTPException(409, "Run has no active worker")
        connection, lock = inputs[run_id]
        with lock:
            # Another progress read may have held this lock while cleanup
            # finished; inspect the real terminal publication after acquiring it.
            final_progress = closed_progress(json.loads((path / "manifest.json").read_text())["status"])
            if final_progress is not None:
                return final_progress
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

    @app.get("/v1/runs/{run_id}/inputs")
    async def input_progress(run_id: str) -> dict[str, Any]:
        """Read the active kernel source frontier, including progress without events."""
        path = directory(run_id)
        status = json.loads((path / "manifest.json").read_text())["status"]
        if status in TERMINAL:
            retained = path / "input-progress.json"
            if retained.is_file():
                watermarks = json.loads(retained.read_text())["watermarks"]
            else:
                # Older runs have no sidecar: recover their actual journal
                # frontier without loading engines or resubmitting input.
                from aerokernel.journal import replay
                kernel = await asyncio.to_thread(replay, path / "journal.jsonl")
                watermarks = dict(kernel.ingress_watermarks)
            return {"contract": "aeroagentsim.input-progress/v1", "watermarks": watermarks,
                    "status": status, "pause_requested": False}
        return await asyncio.to_thread(worker_input_call, run_id, "progress", {})

    @app.post("/v1/runs/{run_id}/ingress")
    async def live_ingress(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Admit typed input after worker-side injection-manifest validation."""
        return await asyncio.to_thread(worker_input_call, run_id, "ingress", body)

    @app.post("/v1/runs/{run_id}/watermark")
    async def watermark(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(worker_input_call, run_id, "watermark", body)

    from .artifacts import mount_artifacts

    mount_artifacts(app, directory, worker_input_call)

    @app.get("/v1/runs/{run_id}/speed")
    async def get_speed(run_id: str) -> dict[str, Any]:
        directory(run_id)
        if run_id not in workers:
            raise HTTPException(409, "Run has no active worker")
        value = workers[run_id][4].value
        return {"id": run_id, "factor": value if value > 0 else None}

    @app.post("/v1/runs/{run_id}/speed")
    async def set_speed(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Host pacing only: how fast simulated time advances against the wall clock."""
        path = directory(run_id)
        if run_id not in workers or json.loads((path / "manifest.json").read_text())["status"] in TERMINAL:
            raise HTTPException(409, "Run has no active worker")
        if set(body) != {"factor"}:
            raise HTTPException(422, "speed: factor required (positive number, or null for as fast as possible)")
        factor = body["factor"]
        if factor is not None and (type(factor) not in (int, float) or not math.isfinite(factor) or not 0.05 <= factor <= 1000):
            raise HTTPException(422, "speed.factor: number between 0.05 and 1000, or null")
        workers[run_id][4].value = 0.0 if factor is None else float(factor)
        return {"id": run_id, "factor": factor}

    @app.post("/v1/runs/{run_id}/{operation}")
    async def control(run_id: str, operation: str) -> dict[str, Any]:
        path = directory(run_id)
        if operation not in CONTROLS:
            raise HTTPException(404, "Unknown control operation")
        status = json.loads((path / "manifest.json").read_text())["status"]
        if run_id not in workers or status in TERMINAL:
            raise HTTPException(409, "Run has no active worker")
        _, paused, stopped, started, _ = workers[run_id]
        if operation == "start":
            if status != "initialized" or started.is_set():
                raise HTTPException(409, f"Only an initialized run can be started; this run is {status}")
            started.set()
        elif operation in {"pause", "resume"} and status == "initialized":
            raise HTTPException(409, "Start the initialized run before pausing or resuming it")
        elif operation == "pause":
            paused.set()
        elif operation == "resume":
            paused.clear()
        else:
            stopped.set()
            paused.clear()
        return {
            "id": run_id,
            "requested": operation,
            "status": json.loads((path / "manifest.json").read_text())["status"],
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
        from aeroagentsim.assistant.api import mount_assistant

        mount_assistant(app, run_root=root)

    if frontend is not None:
        from fastapi.staticfiles import StaticFiles

        if not (frontend / "index.html").exists():
            raise ValueError(f"Frontend build missing: {frontend}; run npm run build")
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
        # Files copied from frontend/public: decoders, textures and assistant artwork.
        for public in ("assistant", "decoders", "textures", "data"):
            if (frontend / public).is_dir():
                app.mount(f"/{public}", StaticFiles(directory=frontend / public), name=f"public-{public}")

        @app.get("/logo192.png", include_in_schema=False)
        @app.get("/manifest.json", include_in_schema=False)
        @app.get("/favicon.ico", include_in_schema=False)
        def public_asset(request: Request) -> FileResponse:
            asset = frontend / request.url.path.lstrip("/")
            if not asset.is_file():
                raise HTTPException(404, "Console asset not found")
            return FileResponse(
                asset, headers={"Cache-Control": "public, max-age=3600"}
            )

        @app.get("/w/{path:path}")
        @app.get("/docs/platform/{path:path}")
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
