from __future__ import annotations

from pathlib import Path

import pytest

from aeroagentsim.assistant.scene import diff, expectations, explain_participant, explain_relation, interactions, summarize
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.scenario import load_scenario


@pytest.fixture
def compiled():
    prepared = TemplateRegistry().apply("food-delivery")
    return load_scenario(prepared.document, base=prepared.base), prepared


def test_complete_scene_shapes_are_grounded(compiled) -> None:
    loaded, prepared = compiled
    summary = summarize(loaded.document, directory=prepared.base, controls=prepared.knobs)
    assert {"title", "city", "duration_s", "run_mode", "environment", "fleets", "places", "workloads", "events", "rules", "algorithms", "cooperation", "controls"} <= summary.keys()
    fleet = summary["fleets"][0]
    assert fleet["role"] == "meal_courier"
    assert fleet["members"] == ["drone-1", "drone-2"]
    assert fleet["count"] == 2
    assert fleet["contract"]["ancestry"][0] == "oo:UAV"
    assert fleet["start"]["place"] is None  # two different authored starts
    assert {"id", "role", "label", "label_zh", "position", "type", "contract", "details"} <= summary["places"][0].keys()
    assert summary["cooperation"] is None
    assert summary["workloads"][0]["kind"] == "food"
    for row in expectations(loaded.document, summary):
        assert {"id", "text", "text_zh", "layer", "match", "approx_s", "precheck", "depends_on"} <= row.keys() <= {"id", "text", "text_zh", "layer", "match", "approx_s", "precheck", "depends_on", "alternatives"}
        assert {"schema", "where"} <= row["match"].keys() <= {"schema", "where", "count"}
    diagram = interactions(summary)
    lane_ids = {r["id"] for r in diagram["lanes"]}
    for step in diagram["steps"]:
        assert {"id", "from", "to", "text", "text_zh", "layer", "decided_by", "branch", "at_s", "expect"} == step.keys()
        assert step["from"] in lane_ids and step["to"] in lane_ids


def test_diff_tracks_configuration_and_real_entities(compiled) -> None:
    loaded, prepared = compiled
    before = summarize(loaded.document, directory=prepared.base)
    import copy
    changed = copy.deepcopy(loaded.document)
    changed["run"]["until_ns"] = 180_000_000_000
    vehicle = next(e for e in changed["entities"] if e["id"] == "drone-2")
    vehicle["facts"]["lowalt.soc"] = 0.2
    after = summarize(changed, directory=prepared.base)
    result = diff(before, after)
    assert result["entities"]["changed"] == ["drone-2"]
    assert any(r["id"] == "duration_s" for r in result["changed"])
    assert result["added"] == result["removed"] == []


def test_explanations_do_not_invent_assignments(compiled) -> None:
    loaded, prepared = compiled
    summary = summarize(loaded.document, directory=prepared.base)
    result = explain_participant(loaded.document, summary, "drone-1")
    assert result["responsibilities"] == []
    assert result["workloads"][0]["kind"] == "food"
    assert explain_relation(loaded.document, summary, "restaurant-west", "customer-1")["workloads"]
    with pytest.raises(ValueError, match="Unknown participant"):
        explain_participant(loaded.document, summary, "invented")


def test_unavailable_observations_stay_absent(compiled) -> None:
    loaded, prepared = compiled
    import copy
    document = copy.deepcopy(loaded.document)
    for e in document["entities"]:
        if e["type"] == "lowalt:UAV":
            e["facts"].pop("lowalt.soc")
    summary = summarize(document, directory=prepared.base)
    assert summary["fleets"][0]["battery_pct"] is None
