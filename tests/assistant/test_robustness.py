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
    assert '"count":3' in restored["summary"] and '"capacity":2' in restored["summary"]
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
