from __future__ import annotations

from pathlib import Path

import pytest

from aeroagentsim.assistant.compiler import CompileContext, compile_plan
from aeroagentsim.assistant.scene import diff, expectations, explain_participant, explain_relation, interactions, summarize
from aeroagentsim.authoring.workspace import WorkspaceStore
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


def test_diff_preserves_localized_labels_for_every_change_group():
    before = {"places": [
        {"id": "changed", "label": "Pad", "label_zh": "充电坪", "position": [0, 0, 0]},
        {"id": "removed", "label": "Depot", "label_zh": "仓库"},
    ]}
    after = {"places": [
        {"id": "changed", "label": "Pad", "label_zh": "充电坪", "position": [10, 0, 0]},
        {"id": "added", "label": "Customer", "label_zh": "客户"},
    ]}
    result = diff(before, after)
    assert result["added"][0]["label_zh"] == "客户"
    assert result["changed"][0]["label_zh"] == "充电坪"
    assert result["removed"][0]["label_zh"] == "仓库"


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
    paused = next(s for s in steps if s["text"] == "Pause spraying when wind exceeds the limit")
    resumed = next(s for s in steps if s["text"] == "Resume spraying when wind drops within the limit")
    assert (paused["at_s"], resumed["at_s"]) == (90, 125)
    assert (paused["text_zh"], resumed["text_zh"]) == ("风速超限时暂停喷洒", "风速降至上限内后恢复喷洒")
    assert rows[paused["expect"]]["match"] == {"schema": "lowalt.weather.hold", "where": {"vehicle": {"$in": ["drone-1"]}, "wind_mps": 8.0}, "count": 1}
    assert rows[resumed["expect"]]["match"] == {"schema": "lowalt.weather.release", "where": {"vehicle": {"$in": ["drone-1"]}, "wind_mps": 2.0}, "count": 1}
    assert rows[resumed["expect"]]["depends_on"] == [paused["expect"]]
    wind_setup = [s for s in steps if s["layer"] == "setup" and s["id"].startswith("event:wind-gust-")]
    assert [s["at_s"] for s in wind_setup] == [90, 125]
    assert all(s["expect"] is None for s in wind_setup)
    assert not any(r["match"]["schema"] in {"lowalt.spraying.paused", "lowalt.spraying.resumed"} for r in rows.values())
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


@pytest.mark.parametrize("change", ["within_limit", "unbound", "past_horizon"])
def test_gust_expectations_require_a_weather_transition(change: str) -> None:
    document, _ = _template_scene("crop-spraying")
    gust = document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]["gusts"][0]
    if change == "within_limit":
        gust["value"] = [4.0, 0.0, 0.0]
    elif change == "unbound":
        packages = [p.get("document", p) for p in document["behaviours"]]
        package = next(p for p in packages if p["id"] == "lowalt.rules")
        package["bindings"] = [b for b in package["bindings"] if b["chain"] != "wind_limit"]
    else:
        gust["end_ns"] = document["run"]["until_ns"] + 1_000_000_000
    summary = summarize(document)
    schemas = {r["match"]["schema"] for r in expectations(document, summary)}
    assert ("lowalt.weather.hold" in schemas) == (change == "past_horizon")
    assert "lowalt.weather.release" not in schemas


def test_patrol_and_air_taxi_steps_match_real_mission_events() -> None:
    document, summary = _template_scene("security-patrol")
    rows = {r["id"]: r for r in expectations(document, summary)}
    steps = interactions(summary)["steps"]
    visit = next(s for s in steps if "checkpoints in order" in s["text"])
    assert rows[visit["expect"]]["match"]["schema"] == "lowalt.patrol.visited"
    assert rows[visit["expect"]]["match"]["where"] == {"task": {"$in": ["patrol-1", "patrol-2", "patrol-3"]}}
    assert any("investigation flight" in s["text"] for s in steps)
    assert all("pick up" not in s["text"].lower() for s in steps)

    document, summary = _template_scene("air-taxi")
    rows = {r["id"]: r for r in expectations(document, summary)}
    steps = interactions(summary)["steps"]
    board = next(s for s in steps if "Board passengers" in s["text"])
    wait = next(s for s in steps if "Wait for a pad" in s["text"])
    land = next(s for s in steps if "Land passengers" in s["text"])
    assert [rows[s["expect"]]["match"]["schema"] for s in (board, wait, land)] == ["lowalt.task.picked_up", "lowalt.mobility.pad_wait", "lowalt.task.completed"]
    assert wait["branch"] is not None
    assert wait["expect"] not in rows[land["expect"]]["depends_on"]
    assert not any(r["match"]["schema"].startswith("lowalt.weather.") for r in rows.values())


@pytest.mark.parametrize("template,point,at_s,params,schemas", [
    ("crop-spraying", "pause-spraying", 10, {"task": "field-1", "duration_s": 20.0}, ["lowalt.spraying.paused", "lowalt.spraying.resumed"]),
    ("powerline-inspection", "report-defect", 210, {"task": "line-survey-3", "site": "line-end"}, ["lowalt.inspection.defect_reported"]),
    ("medical-delivery", "prioritize-medical", 10, {"task": "urgent-3", "priority": 200}, ["lowalt.medical.prioritized"]),
])
def test_scheduled_operator_steps_match_business_payloads(template, point, at_s, params, schemas) -> None:
    document, _ = _template_scene(template)
    payload = dict(params)
    for key in ("task", "site"):
        if key in payload:
            entity = next(e for e in document["entities"] if e["id"] == payload[key])
            payload[key] = {"$ref": {"id": entity["id"], "type_id": entity["type"], "generation": 0}}
    document["bindings"]["commands"].append({"schema": "aas.runtime.inject_event", "target": "behaviour", "at_ns": at_s * 1_000_000_000,
                                             "payload": {"injection_point": point, "payload": payload}})
    summary = summarize(document)
    rows = {r["id"]: r for r in expectations(document, summary)}
    for schema in schemas:
        row = next(r for r in rows.values() if r["match"]["schema"] == schema)
        assert row["match"]["where"] == {k: params[k] for k in ("task", "site", "priority") if k in params}
        assert any(s["expect"] == row["id"] for s in interactions(summary)["steps"])
    assert all(s["expect"] is None or s["expect"] in rows for s in interactions(summary)["steps"])


def test_random_release_states_how_many_orders_arrive(tmp_path) -> None:
    # Six configured slots at 0.01 orders/s over a 100 s window are about two orders, not six.
    context = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    sparse = compile_plan(context, {"template": "food-delivery"}, [{"id": "op-1", "op": "set_control", "params": {"control": "order_rate", "value": 0.01}}], tmp_path / "sparse")
    release = sparse.summary["workloads"][0]["release"]
    assert sparse.ok and release["expected_released"] < 6 and "set_orders" in release["note"] and "set_orders" in release["note_zh"]
    assert any(w["field"] == "workloads.meal.release" for w in sparse.warnings)
    default = compile_plan(context, {"template": "food-delivery"}, [], tmp_path / "default")
    assert default.summary["workloads"][0]["release"]["expected_released"] == 6 and "note" not in default.summary["workloads"][0]["release"]


def test_traffic_scene_has_actual_wind_vehicles_tasks_and_accident(tmp_path):
    context = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    result = compile_plan(context, {"template": "traffic-accident"}, [
        {"id": "east-wind", "op": "set_wind", "params": {"speed_mps": 3}},
        {"id": "accident-time", "op": "set_control", "params": {"control": "accident_time_s", "value": 20}},
    ], tmp_path / "traffic")
    assert result.ok, result.errors
    assert result.summary["environment"]["wind"]["speed_mps"] == 3
    assert not any(e["plugin"] == "lowalt_tasks" for e in result.document["engines"].values())
    fleets = {f["id"]: f for f in result.summary["fleets"]}
    assert fleets["traffic-uavs"]["count"] == 8
    assert fleets["traffic-road-vehicles"]["count"] == 63
    assert fleets["traffic-uavs"]["member_states"][0]["position"] is not None
    assert fleets["traffic-road-vehicles"]["battery_pct"] is None
    assert sum(w["count"] for w in result.summary["workloads"]) == sum(e["type"] == "aas:TrafficTask" for e in result.document["entities"])
    event = next(e for e in result.summary["events"] if e["kind"] == "traffic_accident")
    assert event["at_s"] == 20 and event["place"] == "incident.01"
    schemas = {e["match"]["schema"] for e in result.expectations}
    assert {"traffic.incident.activated", "traffic.award.committed", "traffic.capture.stored"} <= schemas
    assert not any(s.startswith("lowalt.") for s in schemas)
