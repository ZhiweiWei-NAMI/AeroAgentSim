"""Optional City Studio REST hook; all execution goes through /v1/runs."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from .catalog import engines
from .workspace import WorkspaceStore

T = TypeVar("T")
MAIN = Path("/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim")
DEFAULT_EXTRACTS = {
    "wujiaochang": MAIN / "sumo_wujiaochang/osm_bbox.osm.xml",
    "berlin": MAIN / "sumo_berlin/map.osm",
}


def checked(action: Callable[[], T]) -> T:
    try:
        return action()
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


def create_router(
    store: WorkspaceStore,
    *,
    extracts: dict[str, Path] | None = None,
    run_root: Path | None = None,
) -> APIRouter:
    """Only configured local extracts are exposed, never arbitrary host-file paths."""
    from .scene import extract_bounds, generate_sumo

    sources = DEFAULT_EXTRACTS if extracts is None else extracts
    router = APIRouter(prefix="/v1/studio", tags=["studio"])

    @lru_cache(maxsize=1)
    def source_catalog() -> list[dict[str, Any]]:
        return [
            {"id": key, "name": key, "bounds": extract_bounds(path)}
            for key, path in sources.items()
        ]

    @router.get("/catalog")
    def catalog() -> dict[str, Any]:
        return checked(lambda: {"extracts": source_catalog(), "engines": engines()})

    @router.get("/types")
    def types(q: str = "") -> dict[str, Any]:
        return checked(lambda: store.catalog.search(q))

    @router.get("/types/{type_id:path}")
    def type_detail(type_id: str) -> dict[str, Any]:
        return checked(lambda: store.catalog.type_detail(type_id))

    @router.get("/workspaces")
    def workspaces() -> list[dict[str, Any]]:
        return checked(store.list)

    @router.post("/workspaces", status_code=201)
    def create(body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"name"}:
            raise HTTPException(422, "workspace: name required; unknown keys rejected")
        return checked(lambda: store.create(body["name"]))

    @router.get("/workspaces/{identifier}")
    def get(identifier: str) -> dict[str, Any]:
        return checked(lambda: store.get(identifier))

    @router.post("/workspaces/{identifier}")
    def save(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        return checked(lambda: store.save(identifier, body))

    @router.post("/workspaces/{identifier}/region")
    def region(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        key = body.get("extract")
        if not isinstance(key, str) or key not in sources:
            raise HTTPException(
                422, "region.extract: select a configured local OSM extract"
            )
        return checked(lambda: store.region(identifier, body, sources[key]))

    @router.post("/workspaces/{identifier}/place")
    def place(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        return checked(lambda: store.place(identifier, body))

    @router.post("/workspaces/{identifier}/validate")
    def validate(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) - {"scenario"} or (
            "scenario" in body and not isinstance(body["scenario"], dict)
        ):
            raise HTTPException(422, "validation: only scenario may be supplied")
        return checked(lambda: store.validate(identifier, body.get("scenario")))

    @router.get("/workspaces/{identifier}/export", response_class=PlainTextResponse)
    def export(identifier: str) -> str:
        return checked(lambda: store.export(identifier))

    @router.post("/workspaces/{identifier}/import")
    def import_yaml(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"yaml"} or not isinstance(body["yaml"], str):
            raise HTTPException(422, "import: yaml string required")
        return checked(lambda: store.import_yaml(identifier, body["yaml"]))

    @router.get("/workspaces/{identifier}/buildings.geojson")
    def buildings(identifier: str) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            draft = store.get(identifier)
            if "scene" not in draft:
                raise ValueError("scene: select and compile a region first")
            return dict(draft["scene"]["geojson"])

        return checked(read)

    @router.post("/workspaces/{identifier}/network")
    def network(identifier: str) -> dict[str, Any]:
        def compile_network() -> dict[str, Any]:
            with store.lock:
                draft = store.get(identifier)
                directory = store.directory(identifier)
                if "region" not in draft:
                    raise ValueError("network: compile a region first")
                result = generate_sumo(
                    directory / "region.osm.xml", directory / "network.net.xml"
                )
                draft["network"] = result
                store._write(draft)
                return result

        return checked(compile_network)

    @router.get("/workspaces/{identifier}/network.net.xml")
    def network_file(identifier: str) -> FileResponse:
        def read() -> FileResponse:
            store.get(identifier)
            path = store.directory(identifier) / "network.net.xml"
            if not path.is_file():
                raise FileNotFoundError("network: generate a SUMO network first")
            return FileResponse(path, media_type="application/xml")

        return checked(read)

    @router.get("/runs/{run_id}/buildings.geojson")
    def run_buildings(run_id: str) -> FileResponse:
        if run_root is None or not re.fullmatch(r"run-[a-f0-9]{32}", run_id):
            raise HTTPException(404, "studio run: not found")
        path = (run_root / run_id / "studio-buildings.geojson").resolve()
        if not path.is_relative_to(run_root.resolve()) or not path.is_file():
            raise HTTPException(404, "studio run geometry: not found")
        return FileResponse(path, media_type="application/geo+json")

    return router


def mount_studio(
    app: FastAPI,
    root: Path,
    *,
    ontology_root: Path | None = None,
    extracts: dict[str, Path] | None = None,
    run_root: Path | None = None,
) -> WorkspaceStore:
    """Serve hook, with local source roots configurable by the integrator."""
    ontology = ontology_root or Path(
        os.environ.get("AEROAGENTSIM_AEROGRAPH_ROOT", "/mnt/data2/weizhiwei/AeroGraph")
    )
    store = WorkspaceStore(root, ontology)
    app.include_router(create_router(store, extracts=extracts, run_root=run_root))
    app.state.studio = store
    return store
