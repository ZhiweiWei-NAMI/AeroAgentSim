"""Optional City Studio REST hook; all execution goes through /v1/runs."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response

from .catalog import engines
from .inputs import configured_extracts, configured_ontology
from .workspace import WorkspaceStore

T = TypeVar("T")


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

    sources = configured_extracts() if extracts is None else extracts
    router = APIRouter(prefix="/v1/studio", tags=["studio"])

    @lru_cache(maxsize=1)
    def source_catalog() -> list[dict[str, Any]]:
        return [
            {"id": key, "name": key, "bounds": extract_bounds(path)}
            for key, path in sources.items()
        ]

    @router.get("/catalog")
    def catalog() -> dict[str, Any]:
        def read() -> dict[str, Any]:
            from aeroagentsim.platform.plugins import EngineCatalog

            items = engines()
            engine_catalog = EngineCatalog()
            for item in items:
                if item["available"]:
                    factory = engine_catalog.factory(item["id"])
                    if hasattr(factory, "capability_descriptor"):
                        descriptor = factory.capability_descriptor
                        if not isinstance(descriptor, dict):
                            raise TypeError(
                                f"plugin {item['id']}.capability_descriptor: mapping required"
                            )
                        item["capability_descriptor"] = descriptor
            import yaml

            from .templates import demo_source

            profiles = {
                path.stem: yaml.safe_load(path.read_bytes())
                for path in (demo_source("traffic-accident") / "profiles").glob(
                    "*.yaml"
                )
            }
            return {
                "extracts": source_catalog(),
                "engines": items,
                "demo_profiles": profiles,
            }

        return checked(read)

    @router.get("/types")
    def types(q: str = "", workspace: str | None = None) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            from .catalog import SnapshotCatalog

            selected = store.catalog_for(workspace)
            result = selected.search(q)
            if isinstance(selected, SnapshotCatalog):
                result["relations"] = selected.relations(q)
                return result
            result["relations"] = [
                {**record.data, "source": record.location()}
                for rows in selected.sources().relations.values()
                for record in rows
                if q.casefold() in str(record.data).casefold()
            ]
            return result

        return checked(read)

    @router.get("/types/{type_id:path}")
    def type_detail(type_id: str, workspace: str | None = None) -> dict[str, Any]:
        return checked(lambda: store.catalog_for(workspace).type_detail(type_id))

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

    @router.post("/workspaces/{identifier}/templates/{name}")
    def import_demo(identifier: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) - {"console", "capture_mode"} or body.get(
            "capture_mode", "city"
        ) not in {"city", "primitive-test"}:
            raise HTTPException(
                422, "demo: console and city/primitive-test capture_mode only"
            )
        return checked(
            lambda: store.import_demo(
                identifier,
                name,
                console=body.get("console") is True,
                primitive=body.get("capture_mode") == "primitive-test",
            )
        )

    @router.get("/demo-assets/{name:path}")
    def demo_asset(name: str) -> FileResponse:
        from .demo import city_file

        return checked(lambda: FileResponse(city_file(name)))

    @router.get("/demo-capture-assets")
    def demo_capture_assets() -> Response:
        from .demo import capture_manifest

        return checked(
            lambda: Response(capture_manifest(), media_type="application/json")
        )

    @router.post("/workspaces/{identifier}/decision-profile")
    def decision_profile(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        from .demo import live_decisions

        def apply() -> dict[str, Any]:
            draft = store.get(identifier)
            if draft["scenario"]["id"] != "traffic-accident" or set(body) not in (
                {"provider"},
                {"mode"},
            ):
                raise ValueError(
                    "Traffic live profile requires a saved demo and provider"
                )
            if body.get("mode") == "scripted":
                import yaml

                from .templates import demo_source

                source = yaml.safe_load(
                    (demo_source("traffic-accident") / "scenario.yaml").read_bytes()
                )
                draft["scenario"]["engines"]["decisions"] = source["engines"][
                    "decisions"
                ]
                draft["scenario"]["engines"]["decisions"]["config"]["fixture_path"] = (
                    str(store.directory(identifier) / "fixtures/decisions.json")
                )
            elif "provider" in body:
                live_decisions(draft["scenario"], body["provider"])
            else:
                raise ValueError("Select scripted or an explicit live provider")
            return store.save(identifier, {"scenario": draft["scenario"]})

        return checked(apply)

    @router.post("/workspaces/{identifier}/behaviours")
    def edit_behaviour(identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        return checked(lambda: store.edit_behaviour(identifier, body))

    @router.get("/workspaces/{identifier}/behaviours/{index}/export")
    def export_behaviour(identifier: str, index: int) -> dict[str, Any]:
        return checked(lambda: store.export_behaviour(identifier, index))

    @router.post("/workspaces/{identifier}/behaviours/{index}/validate")
    def validate_behaviour(
        identifier: str, index: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        if body:
            raise HTTPException(422, "behaviour validation: save the draft first")
        return checked(lambda: store.validate_behaviour(identifier, index))

    @router.get("/runs/{run_id}/configuration")
    def run_configuration(run_id: str) -> dict[str, Any]:
        """Read pinned authoring inputs and actual WAL identity, never build an engine."""

        def read() -> dict[str, Any]:
            if run_root is None or not re.fullmatch(r"run-[a-f0-9]{32}", run_id):
                raise FileNotFoundError("studio run: not found")
            path = (run_root / run_id).resolve()
            if not path.is_relative_to(run_root.resolve()):
                raise ValueError("run path escapes configured root")
            with (path / "journal.jsonl").open("rb") as stream:
                line = stream.readline()
            if not line.endswith(b"\n"):
                raise ValueError("run WAL header is not committed yet")
            header = json.loads(line)
            if header.get("type") != "header" or header.get("index") != 0:
                raise ValueError("run WAL lacks its identity header")
            scenario = json.loads((path / "scenario.json").read_bytes())
            return {
                "service_run_id": run_id,
                "kernel_run_id": header["run_id"],
                "epoch": header["epoch"],
                "scenario": scenario,
                "resolved_bindings": header["resolved_bindings"],
            }

        return checked(read)

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
    ontology = (
        ontology_root
        if ontology_root is not None
        else configured_ontology()
        if os.environ.get("AEROAGENTSIM_AEROGRAPH_ROOT")
        else None
    )
    store = WorkspaceStore(root, ontology)
    app.include_router(create_router(store, extracts=extracts, run_root=run_root))
    app.state.studio = store
    return store
