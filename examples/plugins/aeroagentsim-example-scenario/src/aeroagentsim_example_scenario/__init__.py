"""One packaged patrol template and a deterministic scheduling knob."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from aeroagentsim.plugin_api import ScenarioPlugin, TemplateSource


def _patrol_interval(document: dict[str, Any], base: Path, value: Any) -> None:
    if type(value) is not int or not 30 <= value <= 120:
        raise ValueError("knob patrol_interval_s: expected an integer from 30 to 120")
    patrol = next(
        generator
        for generator in document["engines"]["tasks"]["config"]["generators"]
        if generator["kind"] == "patrol"
    )
    patrol["arrivals"]["times_ns"] = [
        index * value * 1_000_000_000 for index in range(len(patrol["tasks"]))
    ]


def plugin() -> ScenarioPlugin:
    directory = files(__name__).joinpath("templates", "example-patrol")
    # TemplateSource needs a persistent filesystem directory, not a Traversable.
    if not isinstance(directory, Path):
        raise RuntimeError("example-patrol: install as an unpacked filesystem package")
    return ScenarioPlugin(
        name="example-patrol",
        api_version=1,
        templates=(TemplateSource(directory=directory.resolve()),),
        apply_hooks={"example-patrol.patrol_interval": _patrol_interval},
    )
