"""Packaged map input and explicitly configured external authoring sources."""

from __future__ import annotations

import json
import os
from pathlib import Path


def configured_ontology() -> Path:
    value = os.environ.get("AEROAGENTSIM_AEROGRAPH_ROOT")
    if not value:
        raise ValueError(
            "Studio requires AEROAGENTSIM_AEROGRAPH_ROOT pointing to a real "
            "AeroGraph source checkout; see docs/platform/assets.md"
        )
    path = Path(value).resolve()
    if not (path / "semantic-directory/data").is_dir():
        raise ValueError(f"AEROAGENTSIM_AEROGRAPH_ROOT: no ontology source at {path}")
    return path


def configured_extracts() -> dict[str, Path]:
    """An explicit configuration replaces the packaged bounded historical extract."""
    value = os.environ.get("AEROAGENTSIM_OSM_EXTRACTS")
    if value is None:
        return {"wujiaochang": Path(__file__).parent / "assets/wujiaochang.osm.xml"}
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
