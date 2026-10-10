"""Public examples retain their complete authored registry without source access."""

from pathlib import Path

import pytest

from aeroagentsim.scenario import load_scenario


def test_converted_scenarios_load_without_aerograph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    for name in (
        "p1-slice", "p1-scale", "p1-spectrum", "predicates-demo",
        "visual/p1-slice-city", "visual/p1-scale-city", "agents/llm-dispatch",
    ):
        scenario = load_scenario(Path("scenarios") / f"{name}.yaml")
        assert "compile" not in scenario.document["registry"]
        if name == "agents/llm-dispatch":
            assert scenario.registry.field("aas.agent.available").declaring_type == "oo:UAV"
            assert scenario.registry.field("aas.agent.destination").declaring_type == "oo:Order"
            assert scenario.registry.message("aas.agent.assign").kind == "command"
            assert scenario.registry.message("aas.agent.record").kind == "event"
