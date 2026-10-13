"""Regressions for the live DeepSeek failures and authoritative scene scopes."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aeroagentsim.assistant.capabilities import CAPABILITY_TOPICS, capabilities
from aeroagentsim.assistant.compiler import CompileContext, apply_plan, compile_plan, validate_compiled
from aeroagentsim.assistant.harness import Harness
from aeroagentsim.assistant.operations import OPERATION_SPECS, validate_operation
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner, compact_json, tool_schemas
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore


def operation(name, identity, **params):
    return {"id": identity, "op": name, "params": params}


@pytest.fixture
def project(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Research")
    sessions = SessionStore(tmp_path / "recordings")
    session = sessions.create(draft["id"])
    tools = ToolRunner(store, TemplateRegistry(), sessions, tmp_path / "recordings")
    return tools, session["id"], draft


def propose(tools, session, operations, **extra):
    return tools.draft_plan(session, base={"workspace": tools.sessions.get(session)["workspace_id"]},
                            title="Research proposal", summary="Authored proposal", operations=operations, **extra)


def test_tool_enum_topics_roles_kinds_and_applicability():
    schemas = {row["function"]["name"]: row["function"]["parameters"] for row in tool_schemas()}
    assert schemas["list_capabilities"]["properties"]["topic"]["enum"] == CAPABILITY_TOPICS
    assert "charging_pad" in schemas["find_places"]["properties"]["role"]["enum"]
    assert "open_questions" in schemas["revise_plan"]["properties"]
    assert "kitchen" in OPERATION_SPECS["add_place"]["parameters"]["properties"]["preparation_s"]["description"]
    assert "food" in OPERATION_SPECS["add_orders"]["parameters"]["properties"]["kind"]["enum"]
    assert "person_fall" in OPERATION_SPECS["add_incident"]["parameters"]["properties"]["kind"]["enum"]


@pytest.mark.parametrize("topic,expected", [("wind", "environment"), ("orders", "workload"), ("operation", None), ("operations", None)])
def test_deepseek_topic_aliases(topic, expected):
    result = capabilities(topic, templates=TemplateRegistry(), algorithms={})
    assert result.get("topic") == expected
    if expected:
        assert result["operations"]


def test_dependencies_resolve_places_fleets_members_and_tasks(project, tmp_path):
    tools, session, draft = project
    operations = [
        operation("set_task", "edit-task", task="meals-1", priority=8),
        operation("add_orders", "orders", id="meals", kind="food", count=1, **{"from": ["kitchen"], "to": ["customer"], "release": {"mode": "scheduled", "times_s": [0]}}),
        operation("set_vehicle", "edit-member", vehicle="couriers-1", battery_pct=65),
        operation("add_fleet", "fleet", id="couriers", role="meal_courier", count=2, start="kitchen"),
        operation("add_place", "customer", id="customer", role="customer", at="kitchen"),
        operation("add_place", "kitchen", id="kitchen", role="kitchen", at=[10, 20, 0], preparation_s=12),
    ]
    result = compile_plan(tools.context(session), {"workspace": draft["id"]}, operations, tmp_path / "dependencies")
    assert result.ok, result.errors
    assert [op["id"] for op in result.operations[1:]] == [op["id"] for op in operations]
    task = next(e for e in result.document["entities"] if e["id"] == "meals-1")
    assert task["facts"]["lowalt.task_priority"] == 8
    vehicle = next(e for e in result.document["entities"] if e["id"] == "couriers-1")
    assert vehicle["facts"]["lowalt.soc"] == 0.65
    assert validate_compiled(tools.context(session), tmp_path / "dependencies")["valid"]
    assert tools.store.get(draft["id"]) == draft


def test_duplicate_creation_names_owner_and_retains_failed_draft(project):
    tools, session, draft = project
    result = propose(tools, session, [operation("add_place", "a", id="south-pad", role="charging_pad", at="center"),
                                      operation("add_place", "b", id="south-pad", role="charging_pad", at="center")])
    assert not result["ok"]
    assert result["errors"][0]["op_id"] == "op-2"
    assert "op-1 in THIS draft" in result["errors"][0]["message"]
    assert result["operations"] == [{"id": "op-1", "op": "add_place", "target": "south-pad"}, {"id": "op-2", "op": "add_place", "target": "south-pad"}]
    assert "Nothing was applied" in result["hint"]
    with pytest.raises(ValueError, match="ambiguous.*op-1.*op-2"):
        tools.revise_plan(session, remove=["south-pad"])
    repaired = tools.revise_plan(session, remove=["op-2"], open_questions=[])
    assert repaired["ok"]
    assert tools.store.get(draft["id"]) == draft


def test_remove_by_workload_target_and_invalid_selector_diagnostics(project):
    tools, session, _ = project
    propose(tools, session, [operation("add_place", "a", id="pad", role="charging_pad", at="center")])
    with pytest.raises(ValueError, match="op-1.*pad"):
        tools.revise_plan(session, remove=["unknown"])
    assert tools.revise_plan(session, remove=["pad"], open_questions=[])["ok"]
    plan = {"base": {}, "title": "Meals", "summary": "Meals", "assumptions": [], "open_questions": [],
            "operations": [operation("add_orders", "op-9", id="meals", kind="food")]}
    assert ToolRunner._revision(plan, {"remove": ["meals"]})["operations"] == []


def test_failed_compile_contract_lookup_and_saved_entity_query(project):
    tools, session, draft = project
    # More than six facilities puts the station beyond the sampled scene details.
    operations = [operation("add_place", f"p-{i}", id=f"customer-{i}", role="customer", at=[i, 0, 0]) for i in range(8)]
    operations += [operation("add_place", "pad", id="pad-central", role="charging_pad", at=[50, 0, 0], capacity=4)]
    result = propose(tools, session, operations)
    plan = tools.sessions.plan(session)
    directory = tools.sessions.plan_directory(session, plan["version"])
    assert result["ok"] and validate_compiled(tools.context(session), directory)["valid"]
    applied = apply_plan(tools.context(session), directory, base=plan["base"], workspace_id=draft["id"], name="Saved", note="Test")
    tools.sessions.applied(session, {"plan_version": plan["version"], **applied})
    saved = tools.get_current_scene(session)
    assert "pad-central" in saved["scene"]["entity_ids"]
    exact = tools.execute(session, "get_current_scene", {"id": "pad-central"})
    assert exact["participant"]["entity"]["facts"]["lowalt.site_capacity"] == 4
    assert exact["participant"]["place"]["position"] == [50, 0, 0]
    assert json.loads(compact_json(saved))["scene"]["entity_ids"][-1] == "weather"
    assert tools.find_places(session, near="pad-central", role="charging_pad")[0]["distance_m"] >= 0
    broken = tools.revise_plan(session, add=[operation("add_orders", "orders", id="bad", kind="parcel", count=1, **{"from": ["absent"], "to": ["pad-central"], "release": {"mode": "scheduled", "times_s": [0]}})])
    assert not broken["ok"]
    assert tools.lookup_contract(session, "UAV")["types"]


def test_partial_restore_and_saved_draft_scope(project):
    tools, session, draft = project
    propose(tools, session, [operation("add_place", "pad", id="pad", role="charging_pad", at="center", capacity=2),
                            operation("add_fleet", "fleet", id="extra", role="meal_courier", count=3, start="pad")], assumptions=["Three added UAVs and capacity two"])
    first = tools.sessions.plan(session)
    updated = copy.deepcopy(first["operations"])
    updated[0]["params"]["capacity"] = 3
    updated[1]["params"]["count"] = 2
    tools.revise_plan(session, update=updated, summary="Capacity 3; two UAVs", assumptions=["Two added UAVs"])
    tools.restore_operations(session, 1, "Restore fleet and pad", [op["id"] for op in updated])
    restored = tools.sessions.plan(session)
    assert restored["operations"] == first["operations"]
    assert restored["assumptions"] == []
    assert "count 3" in restored["summary"] and "capacity 2" in restored["summary"]
    assert "Add place" in restored["summary"] and "Add fleet" in restored["summary"]
    assert "add_place" not in restored["summary"] and "{" not in restored["summary"]
    assert restored["operation_summaries"][1]["label_zh"] == "添加机群"
    directory = tools.sessions.plan_directory(session, restored["version"])
    assert validate_compiled(tools.context(session), directory)["valid"]
    applied = apply_plan(tools.context(session), directory, base=restored["base"], workspace_id=draft["id"], name="Saved", note="Test")
    tools.sessions.applied(session, {"plan_version": restored["version"], **applied})
    saved = tools.store.get(draft["id"])
    changed = copy.deepcopy(saved["scenario"])
    next(e for e in changed["entities"] if e["id"] == "pad")["facts"].update({"lowalt.site_position": [50, 0, 0], "lowalt.site_capacity": 4})
    tools.store.save(draft["id"], {"revision": saved["revision"], "scenario": changed})
    assert tools.explain_participant(session, "pad")["place"]["details"]["lowalt.site_capacity"] == 4
    assert tools.explain_participant(session, "pad", scope="proposed")["place"]["details"]["lowalt.site_capacity"] == 2
    agent = Harness.__new__(Harness)
    agent.sessions, agent.tools = tools.sessions, tools
    tools.sessions.append(session, {"role": "user", "content": "What is pad's current position? I changed it in the Form. Answer from the saved draft."})
    agent._answer(session, {"content": "pad was deleted by the Form change."})
    answer = tools.sessions.get(session)["events"][-1]["text"]
    assert "exist in the saved draft" in answer and "deleted" not in answer
    assert "lowalt.site_capacity" in answer and "50" in answer


def test_region_preview_apply_and_uncovered_bounds(project, monkeypatch, tmp_path):
    tools, session, draft = project
    source = Path(__file__).parents[1] / "authoring/fixtures/wujiaochang.osm.xml"
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", json.dumps({"local": str(source)}))
    (tools.store.directory(draft["id"]) / "network.net.xml").write_text("previous network artifact")
    catalogue = tools.execute(session, "list_capabilities", {"topic": "region"})
    assert catalogue["extracts"][0]["id"] == "local"
    bounds = list(extract_bounds(source))
    result = propose(tools, session, [operation("set_region", "region", extract="local", bounds=bounds),
                                     operation("add_place", "pad", id="pad", role="charging_pad", at="center")])
    assert result["ok"], result
    plan = tools.sessions.plan(session)
    directory = tools.sessions.plan_directory(session, plan["version"])
    assert "region" not in tools.store.get(draft["id"])
    assert "scene" not in tools.store.get(draft["id"])
    assert any(r["id"] == "region" for r in plan["diff_base"]["changed"])
    assert any(r["id"] == "network.net.xml" for r in plan["diff_base"]["removed"])
    assert validate_compiled(tools.context(session), directory)["valid"]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from aeroagentsim.assistant.api import create_router

    app = FastAPI()
    app.include_router(create_router(tools.store, run_root=tools.run_root, templates=tools.templates, sessions=tools.sessions, harness=SimpleNamespace(tools=tools)))
    with TestClient(app) as client:
        response = client.get(f"/v1/assistant/sessions/{session}/plans/{plan['version']}/scenario")
    assert response.status_code == 200, response.text
    preview = response.json()
    form_preview = tools.store.region(draft["id"], {"extract": "local", "bounds": bounds, "alt": 0, "level_height_m": 3, "dry_run": True}, source)["preview"]
    assert preview["preview"]["geometry"] == form_preview["geometry"]
    assert preview["preview"]["geometry"]["buildings"]["features"] and preview["preview"]["geometry"]["roads"]
    assert preview["scenario"]["origin"] == preview["scene"]["origin"] == preview["preview"]["origin"]
    assert preview["region"]["bounds"] == bounds and preview["scene_source"]["kind"] == "region"
    assert "scene" not in tools.store.get(draft["id"])
    apply_plan(tools.context(session), directory, base=plan["base"], workspace_id=draft["id"], name="Region", note="Test")
    saved = tools.store.get(draft["id"])
    assert saved["region"]["bounds"] == bounds
    assert saved["scenario"]["origin"] == saved["scene"]["origin"]
    assert (tools.store.directory(draft["id"]) / "region.osm.xml").is_file()
    assert not (tools.store.directory(draft["id"]) / "region.plan.json").exists()
    assert not (tools.store.directory(draft["id"]) / "network.net.xml").exists()
    bad = compile_plan(tools.context(session), {"workspace": draft["id"]}, [operation("set_region", "bad-region", extract="local", bounds=[121.47, 31.22, 121.48, 31.23])], tmp_path / "bad-region")
    assert not bad.ok and bad.errors[0]["op_id"] == "bad-region" and bad.errors[0]["field"] == "bounds"
    assert "coverage" in bad.errors[0]["message"] and "Region" in bad.errors[0]["message"]
    assert tools.store.get(draft["id"]) == saved


def test_tool_shape_diagnostics_name_operation_and_valid_options(project):
    tools, session, _ = project
    with pytest.raises(ValueError, match="op-invalid.*params.role.*charging_pad"):
        tools.execute(session, "revise_plan", {"add": [operation("add_place", "op-invalid", id="place", role="unknown", at="center")]})
    with pytest.raises(ValueError, match="valid fields.*open_questions"):
        tools.execute(session, "revise_plan", {"unknown": []})
    result = propose(tools, session, [operation("add_place", "pad", id="pad", role="charging_pad", at="center", preparation_s=30)])
    assert not result["ok"] and result["errors"][0]["field"] == "preparation_s"
    assert "kitchen" in result["errors"][0]["message"]


def test_watch_reply_uses_selected_applied_alternative_not_old_prose(tmp_path):
    templates = TemplateRegistry()
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.import_template("food-delivery", {}, registry=templates)
    sessions = SessionStore(tmp_path / "recordings")
    session = sessions.create(draft["id"])["id"]
    tools = ToolRunner(store, templates, sessions, tmp_path / "recordings")
    propose(tools, session, [operation("add_place", "pad", id="pad", role="charging_pad", at="center", capacity=2),
                            operation("add_fleet", "fleet", id="extra", role="meal_courier", count=3, start="pad")])
    first = sessions.plan(session)
    variants = []
    for count in (2, 4):
        fleet = copy.deepcopy(first["operations"][1])
        fleet["params"]["count"] = count
        variants.append({"label": str(count), "title": f"Add {count}", "summary": f"Add {count}", "assumptions": [], "changes": {"update": [fleet]}})
    result = tools.propose_alternatives(session, variants)
    chosen = result["variants"][0]["version"]
    sessions.choose(session, chosen)
    plan = sessions.plan(session)
    directory = sessions.plan_directory(session, chosen)
    assert validate_compiled(tools.context(session), directory)["valid"]
    applied = apply_plan(tools.context(session), directory, base=plan["base"], workspace_id=draft["id"], name="Chosen", note="Two added")
    sessions.applied(session, {"plan_version": chosen, **applied})
    sessions.append(session, {"role": "user", "content": "What should I watch for when I run it?"})
    agent = Harness.__new__(Harness)
    agent.sessions, agent.tools = sessions, tools
    agent._answer(session, {"content": "Current v1 adds three UAVs, five in total."})
    answer = sessions.get(session)["events"][-1]["text"]
    assert "Total UAVs: 4" in answer and "extra: 2" in answer
    assert "capacity 2" in answer
    assert "adds three" not in answer and "five in total" not in answer


def parcel_operations():
    return [operation("add_place", "depot-op", id="depot", role="depot", at=[0, -100, 0]),
            operation("add_place", "customer-op", id="customer", role="customer", at=[0, 100, 0]),
            operation("add_orders", "orders-op", id="parcel_flow", kind="parcel", count=2,
                      **{"from": ["depot"], "to": ["customer"], "release": {"mode": "scheduled", "times_s": [0, 30]}})]


def test_set_orders_revision_retains_creation_recipe_and_can_be_revised_again(project):
    tools, session, _ = project
    assert propose(tools, session, parcel_operations())["ok"]
    prior = tools.sessions.plan(session)
    creation = next(op for op in prior["operations"] if op["op"] == "add_orders")
    edit = operation("set_orders", creation["id"], id="parcel_flow", release={"mode": "scheduled", "times_s": [0, 40]})
    assert tools.revise_plan(session, update=[edit])["ok"]
    revised = tools.sessions.plan(session)
    retained = next(op for op in revised["operations"] if op["id"] == creation["id"])
    assert retained["op"] == "add_orders" and retained["params"]["kind"] == "parcel" and retained["params"]["count"] == 2
    assert retained["params"]["from"] == ["depot"] and retained["params"]["release"]["times_s"] == [0, 40]
    # Selecting the creator's op id is also unambiguous and does not erase it.
    assert tools.revise_plan(session, update=[operation("set_orders", creation["id"], id=creation["id"], priority=7)])["ok"]
    final = tools.sessions.plan(session)
    assert next(w for w in final["scene"]["workloads"] if w["id"] == "parcel_flow")["release"]["times_s"] == [0, 40]
    assert "2 releases, 0–40 s" in final["summary"] and "set_orders" not in final["summary"]


@pytest.mark.parametrize("selector", ["parcel_flow", "orders-op"])
def test_same_plan_set_orders_resolves_creation_even_before_it(project, tmp_path, selector):
    tools, session, draft = project
    edits = [operation("set_orders", "timing-op", id=selector, release={"mode": "scheduled", "times_s": [0, 1]})]
    edits.extend(parcel_operations())
    edits.append(operation("set_duration", "duration-op", seconds=2))
    result = compile_plan(tools.context(session), {"workspace": draft["id"]}, edits, tmp_path / "same-plan")
    assert result.ok, result.errors
    arrivals = next(g for e in result.document["engines"].values() if e["plugin"] == "lowalt_tasks" for g in e["config"]["generators"] if g["id"] == "parcel_flow")["arrivals"]
    assert arrivals["times_ns"] == [0, 1_000_000_000]


def test_missing_workload_error_lists_workload_creation_operation_ids(project, tmp_path):
    tools, session, draft = project
    result = compile_plan(tools.context(session), {"workspace": draft["id"]}, parcel_operations() + [operation("set_orders", "bad-op", id="wrong-workload", priority=2)], tmp_path / "missing")
    assert not result.ok
    error = next(e for e in result.errors if e["op_id"] == "bad-op")
    assert "orders-op" in error["message"] and "parcel_flow" in error["message"] and "retain" in error["message"]


def test_duplicate_readd_after_failure_can_recover_by_editing_retained_operations(project):
    tools, session, draft = project
    ops = parcel_operations()
    ops[-1]["params"]["from"] = ["missing-depot"]
    assert not propose(tools, session, ops)["ok"]
    repeated = tools.revise_plan(session, add=[ops[0]])
    assert not repeated["ok"]
    error = next(e for e in repeated["errors"] if "already added by op-1" in e["message"])
    assert "THIS draft" in error["message"] and "revise_plan.update" in error["message"]
    repaired = tools.revise_plan(session, remove=["op-4"], update=[{**parcel_operations()[-1], "id": "op-3"}])
    assert repaired["ok"] and tools.store.get(draft["id"]) == draft


def test_kitchen_only_preparation_guidance_is_in_both_plan_tool_schemas(project):
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in tool_schemas()}
    for tool, field in (("draft_plan", "operations"), ("revise_plan", "add"), ("revise_plan", "update")):
        branches = schemas[tool]["properties"][field]["items"]["anyOf"]
        place = next(b for b in branches if b["properties"]["op"]["enum"] == ["add_place"])
        description = place["properties"]["params"]["properties"]["preparation_s"]["description"]
        assert "Kitchens only" in description and "仅厨房" in description
    tools, session, _ = project
    result = propose(tools, session, [operation("add_place", "bad", id="depot", role="depot", at="center", preparation_s=30)])
    assert not result["ok"] and result["errors"][0]["field"] == "preparation_s"
    assert "kitchen" in result["errors"][0]["message"]


@pytest.mark.parametrize("timed", [
    operation("add_gust", "gust", start_s=170, end_s=200, speed_mps=14),
    operation("add_incident", "incident", id="fall", kind="person_fall", at="center", time_s=3),
    operation("add_orders", "orders", id="late-orders", kind="parcel", count=2, **{"from": ["depot"], "to": ["customer"], "release": {"mode": "scheduled", "times_s": [0, 30]}}),
    operation("add_orders", "orders", id="late-orders", kind="parcel", count=2, **{"from": ["depot"], "to": ["customer"], "release": {"mode": "poisson", "rate_per_s": 1, "end_s": 20}}),
])
def test_timed_operations_beyond_final_duration_are_actionable_errors(project, tmp_path, timed):
    tools, session, draft = project
    result = compile_plan(tools.context(session), {"workspace": draft["id"]}, parcel_operations()[:2] + [timed, operation("set_duration", "duration", seconds=2)], tmp_path / "unreachable")
    assert not result.ok
    error = next(e for e in result.errors if e["op_id"] == timed["id"] and e.get("code") == "RUN_HORIZON")
    assert "2 s run" in error["message"] and "set the run duration to at least" in error["message"] and "请移至运行范围内" in error["message"]
    assert tools.store.get(draft["id"]) == draft


def test_later_duration_edit_makes_gust_and_poisson_defaults_reachable(project, tmp_path):
    tools, session, draft = project
    ops = parcel_operations()
    ops[-1]["params"]["release"] = {"mode": "poisson", "rate_per_s": 0.1}
    ops.extend([operation("add_gust", "gust", start_s=170, end_s=200, speed_mps=14), operation("set_duration", "duration", seconds=320)])
    result = compile_plan(tools.context(session), {"workspace": draft["id"]}, ops, tmp_path / "reachable")
    assert result.ok, result.errors
    assert result.document["run"]["until_ns"] == 320_000_000_000
    arrivals = next(g for e in result.document["engines"].values() if e["plugin"] == "lowalt_tasks" for g in e["config"]["generators"] if g["id"] == "parcel_flow")["arrivals"]
    assert arrivals["end_ns"] == 320_000_000_000
    assert not any(w.get("code") == "RUN_HORIZON" for w in result.warnings)


def test_duration_shrink_flags_existing_template_schedules(project, tmp_path):
    tools, session, _ = project
    result = compile_plan(tools.context(session), {"template": "food-delivery"}, [operation("set_duration", "duration", seconds=2)], tmp_path / "short-template")
    assert result.ok, result.errors
    assert any(w.get("code") == "RUN_HORIZON" and "2 s run" in w["message"] for w in result.warnings)


def test_duplicate_key_revision_args_recover_without_losing_the_retained_plan(project):
    from types import SimpleNamespace

    tools, session, _ = project
    assert propose(tools, session, [operation("add_place", "pad-op", id="pad", role="charging_pad", at="center", capacity=1)])["ok"]
    current = tools.sessions.plan(session)
    updated = copy.deepcopy(current["operations"][0])
    updated["params"]["capacity"] = 3
    requests = []
    bad = '{"update":' + json.dumps([updated]) + ',"open_questions":[],"open_questions":[]}'
    good = json.dumps({"update": [updated], "open_questions": []})
    def call_args(args):
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "revise", "type": "function", "function": {"name": "revise_plan", "arguments": args}}]}
    replies = iter([call_args(bad), call_args(good), {"role": "assistant", "content": "Capacity is three."}])
    def complete(messages, schemas, **kwargs):
        requests.append(copy.deepcopy(messages))
        return {"message": next(replies), "usage": {"total_tokens": 1}}
    agent = Harness.__new__(Harness)
    agent.sessions, agent.tools, agent.max_calls = tools.sessions, tools, 3
    agent.connection = SimpleNamespace(provider=lambda: SimpleNamespace(complete=complete))
    turn = tools.sessions.begin_turn(session, "Change pad capacity to three")
    agent.run_turn(session, turn["turn"])
    rejected = json.loads(requests[1][-1]["content"])
    assert "duplicate keys" in rejected["error"] and "open_questions" in rejected["schema"]["fields"]
    assert rejected["current_operations"] == [{"id": updated["id"], "op": "add_place", "target": "pad"}]
    revised = tools.sessions.plan(session)
    assert revised["version"] == 2 and revised["compile"]["ok"]
    assert len(revised["operations"]) == 1 and revised["operations"][0]["params"]["capacity"] == 3
    assert not tools.sessions.get(session)["busy"]


def test_revision_summary_uses_active_locale_and_bounded_release_prose():
    from aeroagentsim.assistant.tools import operation_summary

    ops = parcel_operations() + [operation("add_fleet", "fleet-op", id="extra", role="parcel_drone", count=3, start="depot")]
    english = operation_summary(ops, "en")
    chinese = operation_summary(ops, "zh-CN")
    assert "Add orders" in english and "2 releases, 0–30 s" in english and "count 3" in english
    assert "添加订单" in chinese and "2 次，0–30 s" in chinese and "数量 3" in chinese
    assert "Add orders" not in chinese and "添加订单" not in english
    assert all("{" not in text and "add_orders" not in text and "orders-op" not in text for text in (english, chinese))


def test_scheduled_operator_event_beyond_duration_is_rejected(project, tmp_path):
    tools, session, _ = project
    result = compile_plan(tools.context(session), {"template": "food-delivery"}, [
        operation("schedule_event", "late-event", event="restaurant_delay", at_s=40, params={"restaurant": "restaurant-west", "delay_s": 10}),
        operation("set_duration", "duration", seconds=2),
    ], tmp_path / "late-event")
    assert not result.ok
    assert any(e["op_id"] == "late-event" and e["field"] == "params.at_s" and e.get("code") == "RUN_HORIZON" for e in result.errors)


def test_legacy_failed_set_orders_revision_recovers_real_ancestor_creation(project):
    tools, session, _ = project
    assert propose(tools, session, parcel_operations())["ok"]
    creation = copy.deepcopy(tools.sessions.plan(session)["operations"][-1])
    assert tools.revise_plan(session, update=[operation("set_orders", creation["id"], id="parcel_flow", release={"mode": "scheduled", "times_s": [0, 40]})])["ok"]
    # The recorded R2 failure already has set_orders in the retained creation's
    # slot. Reopening it must use its ancestor's real recipe, without defaults.
    with tools.sessions.edit(session) as data:
        legacy = data["plans"]["2"]
        legacy["operations"][-1] = operation("set_orders", creation["id"], id="parcel_flow", release={"mode": "scheduled", "times_s": [0, 40]})
        legacy["compile"]["ok"] = False
        legacy["validation"]["state"] = "invalid"
    result = tools.revise_plan(session, add=[operation("set_duration", "duration", seconds=320)])
    assert result["ok"], result
    plan = tools.sessions.plan(session)
    restored = next(op for op in plan["operations"] if op["id"] == creation["id"])
    assert restored["op"] == "add_orders" and restored["params"]["count"] == 2 and restored["params"]["kind"] == "parcel"
    assert restored["params"]["release"]["times_s"] == [0, 40] and restored["params"]["from"] == ["depot"]
    assert any("from plan v1" in note for note in result["notes"])
