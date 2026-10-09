"""Viewer labels/grouping must use the recorded registry snapshot."""
from __future__ import annotations

import json
from pathlib import Path

from aeroagentsim.platform import RunSession
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
