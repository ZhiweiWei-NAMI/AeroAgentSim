"""Round-3 assistant regressions use real workspaces, compilation and validation, without a model or server."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aeroagentsim.assistant.api import public_plan
from aeroagentsim.assistant.compiler import CompileContext, apply_plan, compile_plan, validate_compiled
from aeroagentsim.assistant.harness import Harness
from aeroagentsim.assistant.prompts import plan_status, state_message
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner
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
    identifier = sessions.create(draft["id"])["id"]
    tools = ToolRunner(store, TemplateRegistry(), sessions, tmp_path / "recordings")

    def validate(session, version):
        result = validate_compiled(tools.context(session), sessions.plan_directory(session, version))
        sessions.validated(session, version, result["valid"], result["errors"] or result["issues"])

    tools.on_plan = validate
    return tools, identifier, draft


def propose(tools, identifier, operations, **extra):
    return tools.draft_plan(identifier, base={"workspace": tools.sessions.get(identifier)["workspace_id"]},
                            title="Research proposal", operations=operations, **{"summary": "Requested changes", **extra})


def applied_revision(project):
    tools, identifier, draft = project
    result = propose(tools, identifier, [
        operation("add_place", "pad", id="qa-pad", role="charging_pad", at="center", power_w=5000),
        operation("add_gust", "gust", start_s=170, end_s=200, speed_mps=14),
    ])
    assert result["ok"] and result["validation"]["state"] == "valid", result
    pad = copy.deepcopy(tools.sessions.plan(identifier)["operations"][0])
    pad["params"]["power_w"] = 6000
    result = tools.revise_plan(identifier, update=[pad])
    assert result["ok"] and result["validation"]["state"] == "valid", result
    plan = tools.sessions.plan(identifier)
    assert plan["version"] == 2
    record = apply_plan(tools.context(identifier), tools.sessions.plan_directory(identifier, plan["version"]),
                        base=plan["base"], workspace_id=draft["id"], name="Saved", note="Confirmed")
    tools.sessions.applied(identifier, {"plan_version": plan["version"], **record})
    assert record["version"] == 1
    return tools, identifier, tools.sessions.plan(identifier), record


def agent_for(tools, identifier, complete=None):
    agent = Harness.__new__(Harness)
    agent.sessions, agent.tools, agent.max_calls = tools.sessions, tools, 1
    def provider(session_id):
        assert session_id == identifier
        return SimpleNamespace(complete=complete)
    agent.connection = SimpleNamespace(provider=provider)
    return agent


@pytest.mark.parametrize("operations,initial,background", [
    ([operation("set_wind", "wind", speed_mps=3)], 3, 3),
    ([operation("set_wind", "wind", speed_mps=4), operation("add_gust", "gust", start_s=0, end_s=10, speed_mps=9)], 9, 4),
    ([operation("set_wind", "wind", speed_mps=4), operation("add_gust", "gust", start_s=10, end_s=20, speed_mps=9)], 4, 4),
    ([operation("set_wind", "wind", speed_mps=4), operation("add_gust", "gust", start_s=0, end_s=10, speed_mps=9),
      operation("remove_gust", "remove", index=0)], 4, 4),
])
def test_t2_r3_002_traffic_wind_preserves_the_real_initial_state_contract(tmp_path, operations, initial, background):
    context = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    directory = tmp_path / "plan"
    result = compile_plan(context, {"template": "traffic-accident"}, operations, directory)
    assert result.ok, result.errors
    entity = next(e for e in result.document["entities"] if e["id"] == "wind.region")
    field = "oo:digital_twin.wind.horizontalVelocity"
    profile = result.document["engines"]["weather"]["config"]["profiles"]["wind.region"][field]
    assert entity["facts"][field] == [[float(initial), 0.0]]
    assert profile["value"] == [[float(background), 0.0]]
    validation = validate_compiled(context, directory)
    assert validation["valid"], validation


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t4_r3_002_expanded_components_replace_contradictory_model_prose(project, monkeypatch, locale):
    tools, identifier, draft = project
    with tools.sessions.edit(identifier) as session:
        session["locale"] = locale
    source = Path(__file__).parents[1] / "authoring/fixtures/wujiaochang.osm.xml"
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", json.dumps({"local": str(source)}))
    false_prose = "No lowalt engines, drones, fleets, or orders are added, as requested."
    result = propose(tools, identifier, [
        operation("set_region", "region", extract="local", bounds=list(extract_bounds(source)), alt=0, level_height_m=3.5),
        operation("add_place", "pad", id="qa-pad", role="charging_pad", at="center", power_w=6000),
    ], summary=false_prose, assumptions=[false_prose], note="No simulation components beyond that.")
    assert result["ok"] and result["validation"]["state"] == "valid", result
    plan = tools.sessions.plan(identifier)
    assert plan["compile"]["operations"][0]["op"] == "add_lowalt_components"
    component = next(row for row in plan["diff_base"]["added"] if row["kind"] == "components")
    engines = sorted(plan["_document"]["engines"])
    assert engines == ["airspace", "behaviour", "energy", "environment", "motion", "tasks"]
    assert component["fields"][0]["after"] == engines
    assert "6" in component["label"] and "6" in plan["summary"]
    effects = "\n".join(row["text"] for row in plan["what_changes"])
    assert all(engine in effects for engine in engines)
    assert "qa-pad" in effects and "6000" in plan["summary"]
    assert plan["assumptions"] == [] and false_prose not in plan["summary"] and "No simulation" not in plan["note"]
    assert "_model_prose" not in public_plan(plan)
    assert result["operations"][0]["op"] == "add_lowalt_components" and result["what_changes"] == plan["what_changes"]
    assert tools.sessions.get(identifier)["events"][0]["note"] == plan["note"]
    assert plan["scene"]["fleets"] == plan["scene"]["workloads"] == []
    assert tools.store.get(draft["id"]) == draft  # Compilation never applies.
    agent = agent_for(tools, identifier)
    agent._answer(identifier, {"content": "No simulation components beyond that."}, context={"locale": locale})
    answer = tools.sessions.get(identifier)["events"][-1]["text"]
    assert "No simulation components" not in answer
    assert ("What this changes" if locale == "en-US" else "实际变更") in answer
    assert all(engine in answer for engine in engines)


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r3_008_revision_reply_names_plan_project_and_pinned_run_independently(project, locale):
    tools, identifier, applied, record = applied_revision(project)
    pad = copy.deepcopy(applied["operations"][0])
    pad["params"]["power_w"] = 7000
    result = tools.revise_plan(identifier, update=[pad])
    assert result["version"] == 3 and result["validation"]["state"] == "valid"
    plan = tools.sessions.plan(identifier)
    evidence = {"run_id": "recorded-project-v1", "workspace_version": record["version"], "plan_version": applied["version"]}
    session = tools.sessions.get(identifier)
    prompt = state_message(session, tools.get_current_scene(identifier), {"locale": locale}, evidence)["content"]
    identities = json.loads(next(line.removeprefix("identities: ") for line in prompt.splitlines() if line.startswith("identities: ")))
    assert identities["selected_plan_revision"] == 3
    assert identities["applications"][0]["plan_revision"] == 2 and identities["applications"][0]["project_version"] == 1
    assert identities["selected_run"]["project_version"] == 1 and identities["selected_run"]["plan_revision"] == 2
    model = ("已应用的项目版本仍是 v2（含 30/60 秒）。充电功率改为 7000 W。" if locale == "zh-CN" else
             "The applied project version is still v2 (with 30/60 seconds). Charging power changes to 7000 W.")
    agent_for(tools, identifier)._answer(identifier, {"content": model}, evidence, {"locale": locale})
    answer = tools.sessions.get(identifier)["events"][-1]["text"]
    assert ("项目版本仍是 v2" if locale == "zh-CN" else "project version is still v2") not in answer
    assert ("方案修订 v3" if locale == "zh-CN" else "Plan revision v3") in answer
    assert ("项目版本 v1" if locale == "zh-CN" else "project version v1") in answer
    assert ("方案修订 v2" if locale == "zh-CN" else "plan revision v2") in answer
    assert "7000 W" in answer
    assert plan["validation"]["state"] == "valid" and tools.workspace_state(identifier)["latest_version"]["version"] == 1


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r3_009_and_as_012_final_reply_reads_a_form_edit_made_during_completion(project, tmp_path, locale):
    tools, identifier, plan, _ = applied_revision(project)
    requests = []

    def complete(messages, schemas, **kwargs):
        requests.append(messages[1]["content"])
        draft = tools.store.get(plan["base"]["workspace"])
        changed = compile_plan(tools.context(identifier), {"workspace": draft["id"]},
                               [operation("remove_gust", "remove", index=0)], tmp_path / "form-edit")
        assert changed.ok, changed.errors
        tools.store.save(draft["id"], {"scenario": changed.document, "revision": draft["revision"]})
        text = ("仓库不能设置备餐时间。方案 v2 已应用，可初始化，无需校验或确认。" if locale == "zh-CN" else
                "Preparation time is kitchen-only. Plan v2 is confirmed and applied as project version 1; it can be initialized and run. Nothing needs validating or confirming.")
        return {"message": {"role": "assistant", "content": text}}

    context = {"locale": locale}
    turn = tools.sessions.begin_turn(identifier, "Keep the depot role and change preparation time.", context)
    agent = agent_for(tools, identifier, complete)
    agent.run_turn(identifier, turn["turn"], context)
    answer = next(event["text"] for event in tools.sessions.get(identifier)["events"] if event["kind"] == "assistant")
    assert requests and '"dirty":false' in requests[0]
    workspace = tools.workspace_state(identifier)
    assert workspace["dirty"] and workspace["validation"] is None and workspace["latest_version"]["version"] == 1
    assert ("草稿已有修改" if locale == "zh-CN" else "current draft has changed") in answer
    assert ("先校验并保存" if locale == "zh-CN" else "validate and save it") in answer
    assert ("无需校验" if locale == "zh-CN" else "Nothing needs validating") not in answer
    assert ("可初始化" if locale == "zh-CN" else "can be initialized") not in answer
    assert ("仓库不能设置备餐时间" if locale == "zh-CN" else "Preparation time is kitchen-only") in answer
    assert ("先校验并保存" if locale == "zh-CN" else "validate and save it") in agent._closing(identifier, context)
    assert plan["scene"]["environment"]["wind"]["gusts"]  # Historical plan is unchanged.


def test_t2_r3_009_status_follows_validation_and_latest_form_saved_version(project):
    tools, identifier, plan, _ = applied_revision(project)
    draft = tools.store.get(plan["base"]["workspace"])
    scenario = copy.deepcopy(draft["scenario"])
    scenario["run"]["until_ns"] = 620_000_000_000
    edited = tools.store.save(draft["id"], {"scenario": scenario, "revision": draft["revision"]})
    validation = tools.store.validate(draft["id"], revision=edited["revision"])
    assert validation["valid"], validation
    status = plan_status(tools.sessions.get(identifier), plan, workspace=tools.workspace_state(identifier))
    assert "current draft is validated" in status and "save it as a project version before initializing" in status
    latest = tools.store.save_version(draft["id"], {"revision": edited["revision"]})
    assert latest["version"] == 2
    status = plan_status(tools.sessions.get(identifier), plan, workspace=tools.workspace_state(identifier))
    assert "Plan revision v2 was applied as project version v1" in status
    assert "Current saved project version v2 can be initialized" in status
    # Reloaded stores return the same authoritative status.
    reloaded = ToolRunner(WorkspaceStore(tools.store.root, None), tools.templates, SessionStore(tools.run_root), tools.run_root)
    assert reloaded.workspace_state(identifier) == tools.workspace_state(identifier)


def test_t2_as_012_completion_uses_validation_that_finished_after_its_prompt(project):
    tools, identifier, _ = project
    tools.on_plan = None
    propose(tools, identifier, [operation("set_duration", "duration", seconds=300)])
    plan = tools.sessions.plan(identifier)
    assert plan["validation"]["state"] == "pending"
    assert plan["diff_base"]["changed"]  # This test requires a confirmable change.

    def complete(messages, schemas, **kwargs):
        assert "validation is running automatically" in messages[1]["content"]
        result = validate_compiled(tools.context(identifier), tools.sessions.plan_directory(identifier, plan["version"]))
        assert result["valid"], result
        tools.sessions.validated(identifier, plan["version"], result["valid"], result["errors"])
        return {"message": {"role": "assistant", "content": "Validation is pending. Wait before confirming."}}

    turn = tools.sessions.begin_turn(identifier, "Review this plan")
    agent_for(tools, identifier, complete).run_turn(identifier, turn["turn"])
    answer = next(e["text"] for e in tools.sessions.get(identifier)["events"] if e["kind"] == "assistant")
    assert tools.sessions.plan(identifier)["validation"]["state"] == "valid"
    assert "Plan revision v1" in answer and "it is validated" in answer
    assert "Click Confirm and apply" in answer
    assert "pending" not in answer and "Wait" not in answer
