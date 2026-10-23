"""Viewer labels/grouping must use the recorded registry snapshot."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim import presentation_api
from aeroagentsim.platform import RunSession
from aeroagentsim.services.app import create_app
from aeroagentsim.services.projector import header


def test_pinned_type_browsing_metadata(slice_run: tuple[RunSession, Path]) -> None:
    _, directory = slice_run
    raw_types = json.loads((directory / "registry.snapshot.json").read_text())[
        "details"
    ]["raw_types"]
    run_header = header(directory)
    for info in run_header["types"]:
        source = raw_types.get(info["typeId"])
        if source is not None:
            assert info["displayName"] == source["name"]
            assert info["directory"] == source["navigation"]["view"]
        else:
            # A scenario-authored type is still browsable by its exact ID and
            # declared inheritance; it is not assigned a guessed directory.
            assert info["displayName"] == info["typeId"]
            assert "directory" not in info


def test_commits_pages_do_not_repeat_presentation_discovery(
    slice_run: tuple[RunSession, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source = slice_run
    directory = tmp_path / "run"
    shutil.copytree(source, directory)
    calls = {"entry_points": 0, "discover": 0}
    original_entries = presentation_api.entry_points
    original_discover = presentation_api.discover

    def entries(*args: Any, **kwargs: Any) -> Any:
        calls["entry_points"] += 1
        return original_entries(*args, **kwargs)

    def discover(*args: Any, **kwargs: Any) -> Any:
        calls["discover"] += 1
        return original_discover(*args, **kwargs)

    monkeypatch.setattr(presentation_api, "entry_points", entries)
    monkeypatch.setattr(presentation_api, "discover", discover)
    presentation_api.clear_presentation_plugin_cache()
    try:
        with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
            response = client.get("/v1/runs/run/commits", params={"from": 1, "limit": 1})
            assert response.status_code == 200, response.text
            first = response.json()
            assert first["commits"] and first["next"] == 2
            assert calls == {"entry_points": 1, "discover": 1}
            baseline = dict(calls)
            for start in (1, 2, 1, 2):
                response = client.get("/v1/runs/run/commits", params={"from": start, "limit": 1})
                assert response.status_code == 200, response.text
                assert response.json()["commits"][0]["commitIndex"] == start
                if start == 1:
                    assert response.json()["commits"] == first["commits"]
                    assert response.json()["next"] == first["next"]
                assert calls == baseline
    finally:
        presentation_api.clear_presentation_plugin_cache()
