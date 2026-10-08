"""Freeze optional authoring geometry with a run, independent of later draft edits."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from aeroagentsim.scenario import Scenario

from .workspace import WorkspaceStore


def snapshot_scene(
    store: WorkspaceStore,
    scenario: Scenario,
    run: Path,
    workspace_id: str | None = None,
) -> None:
    """Only an exact authored scenario can attach its city's source geometry."""
    identifier = workspace_id if workspace_id is not None else scenario.document["id"]
    if not isinstance(identifier, str) or not identifier.startswith("studio-"):
        return
    with store.lock:
        if not (store.directory(identifier) / "draft.json").is_file():
            return
        draft = store.get(identifier)
        if "scene" not in draft or draft["scenario"] != scenario.document:
            return
        (run / "studio-scene.json").write_text(
            json.dumps(draft["scene"], allow_nan=False), encoding="utf-8"
        )
        (run / "studio-buildings.geojson").write_text(
            json.dumps(draft["scene"]["geojson"], allow_nan=False), encoding="utf-8"
        )
        source = store.directory(identifier) / "network.net.xml"
        if "network" in draft and source.is_file():
            shutil.copyfile(source, run / "studio-network.net.xml")


def scene_header(run: Path, base_url: str) -> dict[str, Any]:
    if not (run / "studio-scene.json").is_file():
        return {}
    scene = json.loads((run / "studio-scene.json").read_text(encoding="utf-8"))
    url = f"{base_url.rstrip('/')}/v1/studio/runs/{run.name}"
    # Road ENU preview is retained in the scene artifact. SUMO net geometry must
    # not be displayed in ENU until its projection/netOffset mapping is bound.
    return {
        "scene": {
            "id": run.name,
            "city": {"kind": "geojson", "url": url + "/buildings.geojson"},
            "attribution": scene["attribution"],
        }
    }
