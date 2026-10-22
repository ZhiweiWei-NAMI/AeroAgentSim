"""Load the external example directly from its src tree; no installation needed."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import aeroagentsim.plugin_api as plugin_api
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.plugin_api import discover_scenario_plugins
from aeroagentsim.scenarios import TEMPLATES


def test_example_plugin_lists_and_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "examples/plugins/aeroagentsim-example-scenario/src"
    )
    monkeypatch.syspath_prepend(str(source))
    example = importlib.import_module("aeroagentsim_example_scenario")
    # Keep unrelated installed distributions out of this source-tree example test.
    monkeypatch.setattr(plugin_api, "entry_points", lambda *, group: ())
    registry = TemplateRegistry(
        plugins=(*discover_scenario_plugins(), example.plugin())
    )

    assert [item["id"] for item in registry.list()] == sorted(
        (*TEMPLATES, "example-patrol")
    )
    assert registry.plugin_of("example-patrol") == "example-patrol"
    directory = source / "aeroagentsim_example_scenario/templates/example-patrol"
    assert registry.directory_of("example-patrol") == directory.resolve()
    assert "lowalt.operator_mode" in registry.hooks
    assert "example-patrol.patrol_interval" in registry.hooks
    before = (directory / "scenario.yaml").read_bytes()

    # apply() runs load_scenario on each prepared document, including behaviours.
    default = registry.apply("example-patrol")
    assert default.document["id"] == "example-patrol"
    assert default.base == directory.resolve()
    assert default.knobs == {"run_mode": "scheduled", "patrol_interval_s": 100}
    schedule = default.document["engines"]["tasks"]["config"]["generators"][0]
    assert schedule["arrivals"]["times_ns"] == [0, 100_000_000_000, 200_000_000_000]
    assert default.document["ingress_streams"][0]["initial_watermark_ns"] == (
        default.document["run"]["until_ns"]
    )

    changed = registry.apply("example-patrol", {"patrol_interval_s": 60})
    schedule = changed.document["engines"]["tasks"]["config"]["generators"][0]
    assert changed.knobs["patrol_interval_s"] == 60
    assert schedule["arrivals"]["times_ns"] == [0, 60_000_000_000, 120_000_000_000]
    assert default.document["engines"]["tasks"]["config"]["generators"][0] != schedule

    interactive = registry.apply("example-patrol", {"run_mode": "interactive"})
    operator = interactive.document["ingress_streams"][0]
    assert operator["initial_watermark_ns"] == 0
    assert "timeout_s" not in operator
    with pytest.raises(ValueError, match="knob patrol_interval_s"):
        registry.apply("example-patrol", {"patrol_interval_s": 0})
    assert (directory / "scenario.yaml").read_bytes() == before
