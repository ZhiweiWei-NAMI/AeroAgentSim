"""Packaged map input and explicitly configured external authoring sources."""

from __future__ import annotations

import json
import os
from pathlib import Path
from functools import lru_cache


def configured_ontology() -> Path:
    value = os.environ.get("AEROAGENTSIM_AEROGRAPH_ROOT")
    if not value:
        raise ValueError(
            "Studio requires AEROAGENTSIM_AEROGRAPH_ROOT pointing to a real "
            "AeroGraph source checkout; see docs/getting-started/install.md"
        )
    path = Path(value).resolve()
    if not (path / "semantic-directory/data").is_dir():
        raise ValueError(f"AEROAGENTSIM_AEROGRAPH_ROOT: no ontology source at {path}")
    return path


def configured_extracts() -> dict[str, Path]:
    """An explicit configuration replaces the packaged bounded historical extract."""
    value = os.environ.get("AEROAGENTSIM_OSM_EXTRACTS")
    if value is None:
        from .templates import demo_source

        return {"huangpu-lite": demo_source("traffic-accident") / "inputs/huangpu-lite.osm.xml",
                "wujiaochang": Path(__file__).parent / "assets/wujiaochang.osm.xml"}
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "AEROAGENTSIM_OSM_EXTRACTS: expected JSON id-to-path map"
        ) from exc
    if not isinstance(data, dict) or not data:
        raise ValueError("AEROAGENTSIM_OSM_EXTRACTS: nonempty id-to-path map required")
    sources: dict[str, Path] = {}
    for key, filename in data.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(filename, str)
            or not filename
        ):
            raise ValueError(
                "AEROAGENTSIM_OSM_EXTRACTS: nonempty string ids/paths required"
            )
        path = Path(filename).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"AEROAGENTSIM_OSM_EXTRACTS[{key}]: {path}")
        sources[key] = path
    return sources


@lru_cache(maxsize=16)
def _extract_statistics(filename: str, identity: tuple) -> dict:
    from .scene import compile_scene, extract_bounds

    path = Path(filename)
    bounds = extract_bounds(path)
    scene = compile_scene(path, bounds, alt=0.0, level_height_m=3.0)
    return {"bounds": bounds, "buildings": sum(f["geometry"]["type"] in {"Polygon", "MultiPolygon"} for f in scene["geojson"]["features"]),
            "roads": len(scene["roads"])}


def extract_catalogue(sources: dict[str, Path]) -> list[dict]:
    """Counts come from the same real geometry compiler as the Region map."""
    labels = {"huangpu-lite": ("Huangpu lite", "黄浦轻量地图"), "wujiaochang": ("Wujiaochang", "五角场")}
    keys = sorted(sources, key=lambda key: 0 if key == "huangpu-lite" else 1)
    rows = []
    for index, key in enumerate(keys):
        path = sources[key].resolve()
        en, zh = labels.get(key, (key, key))
        row = {"id": key, "name": key, "label": en, "label_zh": zh, "default": False}
        try:
            stat = path.stat()
            row.update(_extract_statistics(str(path), (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)))
        except (ValueError, OSError) as error:
            # A broken configured source stays visible with its real diagnostic;
            # it cannot hide the usable sources or acquire invented bounds/counts.
            row["diagnostics"] = [str(error)]
        rows.append(row)
    default = next((row for row in rows if "bounds" in row), None)
    if default is not None:
        default["default"] = True
    return rows
