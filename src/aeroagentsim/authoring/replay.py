"""Freeze optional authoring geometry with a run, independent of later draft edits."""

from __future__ import annotations

import copy
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
    version: dict[str, Any] | None = None,
) -> None:
    """Only an exact authored scenario can attach its city's source geometry."""
    if version is not None and workspace_id is not None:
        attach(run, version, store.version_directory(workspace_id, version["version"]))
        return
    identifier = workspace_id if workspace_id is not None else scenario.document["id"]
    if not isinstance(identifier, str) or not identifier.startswith("studio-"):
        return
    with store.lock:
        if not (store.directory(identifier) / "draft.json").is_file():
            return
        draft = store.get(identifier)
        from aeroagentsim.scenario import load_scenario

        expected = load_scenario(
            draft["scenario"], base=store._base(draft["scenario"], identifier)
        ).document

        def authored(document: dict[str, Any]) -> dict[str, Any]:
            result = copy.deepcopy(document)
            for engine in result["engines"].values():
                if engine["plugin"] in {"capture", "traffic_camera_capture"}:
                    engine["config"].pop("run_directory", None)
                    engine["config"].get("renderer", {}).pop("service_run_id", None)
            return result

        if authored(expected) != authored(scenario.document):
            raise ValueError(
                "Scene cannot attach to a scenario that differs from its saved draft"
            )
        attach(run, draft, store.directory(identifier))


def attach(run: Path, draft: dict[str, Any], directory: Path) -> None:
    """Copy the draft's (or frozen version's) display geometry into the run."""
    from .scene import region_source

    if "scene" in draft:
        # Old versions may contain both sources. Never replace their frozen
        # region with a template scene created earlier during run preparation.
        (run / "console-scene.json").unlink(missing_ok=True)
        scene = draft["scene"]
        (run / "studio-scene.json").write_text(json.dumps(scene, allow_nan=False), encoding="utf-8")
        (run / "studio-buildings.geojson").write_text(json.dumps(scene["geojson"], allow_nan=False), encoding="utf-8")
        identity = region_source(draft.get("region", {}), scene)
        if draft.get("scenario", {}).get("city") is not None:
            identity["conflicting_city"] = draft["scenario"]["city"]
        (run / "scene-source.json").write_text(json.dumps(identity, allow_nan=False), encoding="utf-8")
        source = directory / "network.net.xml"
        if "network" in draft and source.is_file():
            shutil.copyfile(source, run / "studio-network.net.xml")
    elif draft.get("scenario", {}).get("city") == "huangpu":
        from .demo import city_presentation

        scene = draft.get("demo_console", {}).get("scene") or city_presentation()
        (run / "console-scene.json").write_text(
            json.dumps(scene, allow_nan=False),
            encoding="utf-8",
        )
        (run / "scene-source.json").write_text(json.dumps({"kind": "fixed-city", "id": "huangpu", "attribution": scene["attribution"]}), encoding="utf-8")
    else:
        (run / "console-scene.json").unlink(missing_ok=True)
        (run / "scene-source.json").unlink(missing_ok=True)


def scene_header(run: Path, base_url: str) -> dict[str, Any]:
    identity_path = run / "scene-source.json"
    identity = json.loads(identity_path.read_bytes()) if identity_path.is_file() else None
    if not (run / "studio-scene.json").is_file():
        if (run / "console-scene.json").is_file():
            return {
                "scene": json.loads((run / "console-scene.json").read_bytes()),
                **({"scene_source": identity} if identity is not None else {}),
            }
        return {}
    scene = json.loads((run / "studio-scene.json").read_text(encoding="utf-8"))
    if identity is None or identity.get("kind") != "region":
        from .scene import region_source

        identity = region_source({}, scene)
    url = f"{base_url.rstrip('/')}/v1/studio/runs/{run.name}"
    # Road ENU preview is retained in the scene artifact. SUMO net geometry must
    # not be displayed in ENU until its projection/netOffset mapping is bound.
    return {
        "scene_source": identity,
        "scene": {
            "id": run.name,
            "city": {"kind": "geojson", "url": url + "/buildings.geojson"},
            "attribution": scene["attribution"],
            **{key: scene[key] for key in ("origin", "bounds", "ground") if key in scene},
        }
    }
