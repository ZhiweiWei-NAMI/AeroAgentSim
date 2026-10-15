"""Region plans use the same workspace path as the map, in an isolated preview."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from aeroagentsim.authoring.inputs import configured_extracts, extract_catalogue
from aeroagentsim.authoring.scene import extract_bounds, preview_geometry
from aeroagentsim.authoring.workspace import WorkspaceStore

from .operations import OperationError


def catalogue() -> list[dict]:
    return extract_catalogue(configured_extracts())


def _source(extract: str) -> Path:
    sources = configured_extracts()
    if extract not in sources:
        raise OperationError("extract", f"Unknown extract {extract!r}; valid options: {list(sources)}. Use list_capabilities(topic='region') or Form → Scene & space → Region / 请选择已配置源，或打开表单的场景与空间→区域")
    return sources[extract]


def _digest(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview(ctx, document: dict, params: dict, files: Path) -> dict:
    source = _source(params["extract"])
    coverage = extract_bounds(source)
    west, south, east, north = params["bounds"]
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise OperationError("bounds", "Use [west,south,east,north] with west < east and south < north in WGS84 degrees / 请提供有效 WGS84 范围")
    if not (coverage[0] <= west and coverage[1] <= south and east <= coverage[2] and north <= coverage[3]):
        raise OperationError("bounds", f"Requested bounds {params['bounds']} are outside extract {params['extract']!r}, coverage {list(coverage)}. Configure a covering extract or select covered bounds in Form → Scene & space → Region / 请求超出源覆盖范围，请配置覆盖源或在区域地图中选择源内范围")
    if document.get("city") is not None:
        raise OperationError("bounds", "This scene uses fixed-city assets; a different region would misalign them. Use a project without fixed city assets in the Region map / 此场景使用固定城市素材，改变区域会导致错位；请在无固定城市素材的项目中选择区域")
    region = {"extract": params["extract"], "bounds": list(params["bounds"]),
              "alt": params.get("alt", 0), "level_height_m": params.get("level_height_m", 3.0)}
    source_digest = _digest(source)
    network_invalidated = (files / "network.net.xml").is_file()
    with tempfile.TemporaryDirectory(prefix="region-preview-", dir=files.parent) as temporary:
        staged = WorkspaceStore(Path(temporary), ctx.store.ontology_root)
        identity = "studio-" + "0" * 32
        directory = staged.directory(identity)
        shutil.copytree(files, directory)
        # All scenario file references must keep pointing into the preview.
        from .compiler import _rebase

        staged._write({"id": identity, "name": "Region preview", "scenario": _rebase(document, files, directory)})
        result = staged.region(identity, region, source)
        shutil.copyfile(directory / "region.osm.xml", files / "region.osm.xml")
    (files / "network.net.xml").unlink(missing_ok=True)
    record = {"region": result["region"], "scene": result["scene"], "scene_source": result["scene_source"],
              "source_digest": source_digest, "previous_origin": copy.deepcopy(document.get("origin")), "network_invalidated": network_invalidated}
    scene = result["scene"]
    record["preview"] = {"extract": region["extract"], "bounds": scene["bounds"], "origin": scene["origin"],
                         "previous_origin": record["previous_origin"], "network_invalidated": network_invalidated,
                         "buildings": sum(f["geometry"]["type"] in {"Polygon", "MultiPolygon"} for f in scene["geojson"]["features"]),
                         "roads": len(scene["roads"]), "attribution": scene["attribution"], "diagnostics": scene["diagnostics"],
                         "warnings_summary": scene["warnings_summary"],
                         "geometry": preview_geometry(scene)}
    (files / "region.plan.json").write_text(json.dumps(record, ensure_ascii=False, allow_nan=False))
    document["origin"] = result["scenario"]["origin"]
    return record


def apply(store: WorkspaceStore, workspace: str, files: Path) -> dict | None:
    path = files / "region.plan.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    source = _source(record["region"]["extract"])
    if _digest(source) != record["source_digest"]:
        raise OperationError("extract", "Region extract changed since preview; preview this plan again / 区域源在预览后改变，请重新预览方案")
    return store.region(workspace, record["region"], source)
