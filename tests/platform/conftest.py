from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario


@pytest.fixture(scope="session")
def pinned(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    scenario = load_scenario(Path("scenarios/p1-slice.yaml"))
    directory = tmp_path_factory.mktemp("pinned")
    path = directory / "snapshot.json"
    scenario.compiled.write_snapshot(path)
    document = copy.deepcopy(scenario.document)
    document["registry"].pop("compile")
    document["registry"]["snapshot"] = str(path)
    return document


@pytest.fixture
def document(pinned: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(pinned)


@pytest.fixture(scope="session")
def slice_run(
    pinned: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> tuple[RunSession, Path]:
    directory = tmp_path_factory.mktemp("slice") / "run"
    session = RunSession(load_scenario(pinned), directory)
    session.run()
    session.close()
    return session, directory
