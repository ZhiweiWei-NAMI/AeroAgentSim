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


def _template_scene(identifier: str):
    prepared = TemplateRegistry().apply(identifier)
    loaded = load_scenario(prepared.document, base=prepared.base)
    return loaded.document, summarize(loaded.document, directory=prepared.base)


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
        assert {"schema", "where"} <= row["match"].keys() <= {"schema", "where", "count", "released_schema"}
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


def test_crop_spraying_uses_field_work_steps() -> None:
    document, summary = _template_scene("crop-spraying")
    steps = interactions(summary)["steps"]
    assert any("field entry" in s["text"] for s in steps)
    spray = next(s for s in steps if "spray passes" in s["text"])
    assert spray["text_zh"] == "沿农田路线逐趟喷洒"
    assert all("pick up" not in s["text"].lower() for s in steps)
    rows = {r["id"]: r for r in expectations(document, summary)}
    assert rows[spray["expect"]]["match"] == {"schema": "lowalt.spraying.applied", "where": {"task": "field-1"}}
    assert summary["routine"][0]["kind"] == "spraying"
    assert "spray it in passes" in summary["routine"][0]["text"]


def test_powerline_inspection_uses_route_observations() -> None:
    document, summary = _template_scene("powerline-inspection")
    steps = interactions(summary)["steps"]
    observed = next(s for s in steps if "observe inspection segments" in s["text"])
    assert observed["text_zh"] == "沿路线飞行并观测巡检区段"
    assert all("pick up" not in s["text"].lower() for s in steps)
    rows = {r["id"]: r for r in expectations(document, summary)}
    match = rows[observed["expect"]]["match"]
    assert match == {"schema": "lowalt.inspection.observed", "where": {"task": {"$in": summary["workloads"][0]["tasks"]}},
                     "count": 3, "released_schema": "lowalt.task.created"}
    assert not any(r["match"]["schema"] == "lowalt.inspection.defect_reported" for r in rows.values())


@pytest.mark.parametrize("template", ["food-delivery", "crop-spraying", "powerline-inspection", "security-patrol",
                                     "air-taxi", "medical-delivery", "traffic-accident", "city-day"])
def test_template_step_expectations_exist(template: str) -> None:
    document, summary = _template_scene(template)
    rows = expectations(document, summary)
    identities = {r["id"] for r in rows}
    assert len(identities) == len(rows)
    diagram = interactions(summary)
    lanes = {lane["id"] for lane in diagram["lanes"]}
    for step in diagram["steps"]:
        assert step["expect"] is None or step["expect"] in identities
        assert step["from"] in lanes and step["to"] in lanes
    for row in rows:
        assert set(row["depends_on"]) <= identities
    assert {r["kind"] for r in summary["routine"]} == {w["kind"] for w in summary["workloads"]}
    assert len(summary["routine"]) == len({w["kind"] for w in summary["workloads"]})


def test_work_observations_require_a_configured_work_route() -> None:
    document, _ = _template_scene("crop-spraying")
    document["engines"]["tasks"]["config"]["work_routes"] = {}
    summary = summarize(document)
    rows = expectations(document, summary)
    assert not any(r["match"]["schema"] == "lowalt.spraying.applied" for r in rows)
    assert not any("spray passes" in s["text"] for s in interactions(summary)["steps"])
