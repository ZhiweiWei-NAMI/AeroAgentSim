"""Optional city artifacts stay pinned to the run rather than the live draft."""

from __future__ import annotations

import json
from pathlib import Path

from aeroagentsim.authoring.replay import scene_header, snapshot_scene
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario import load_scenario


def test_geometry_is_copied_before_later_draft_changes(tmp_path: Path) -> None:
    store = WorkspaceStore(
        tmp_path / "workspaces", Path("/mnt/data2/weizhiwei/AeroGraph")
    )
    workspace = store.create("city")
    scene = {
        "origin": {"lat": 31.3, "lon": 121.5, "alt": 0.0},
        "geojson": {"type": "FeatureCollection", "features": []},
        "attribution": "© OpenStreetMap contributors · ODbL",
    }
    draft = store.get(workspace["id"])
    draft["scene"] = scene
    store._write(draft)
    scenario = load_scenario(draft["scenario"])
    run = tmp_path / ("run-" + "a" * 32)
    run.mkdir()
    snapshot_scene(store, scenario, run)
    draft["scene"]["attribution"] = "changed later"
    store._write(draft)
    result = scene_header(run, "http://127.0.0.1:8017/")
    assert result["scene"]["attribution"] == "© OpenStreetMap contributors · ODbL"
    assert result["scene"]["city"]["url"].endswith(
        f"/v1/studio/runs/{run.name}/buildings.geojson"
    )
    assert (
        json.loads((run / "studio-buildings.geojson").read_text()) == scene["geojson"]
    )


def test_changed_scenario_does_not_attach_a_different_draft_scene(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(
        tmp_path / "workspaces", Path("/mnt/data2/weizhiwei/AeroGraph")
    )
    workspace = store.create("city")
    scenario = load_scenario(workspace["scenario"])
    draft = store.get(workspace["id"])
    draft["scenario"]["run"]["seed"] = 2
    draft["scene"] = {"geojson": {}, "attribution": "another scenario"}
    store._write(draft)
    run = tmp_path / "run-other"
    run.mkdir()
    snapshot_scene(store, scenario, run)
    assert scene_header(run, "http://localhost") == {}
    assert not (run / "studio-scene.json").exists()
