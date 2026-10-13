"""Round-4 assistant regressions: real compilation, no model or running server."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.assistant.api import create_router
from aeroagentsim.assistant.compiler import CompileContext, compile_plan, resolve_base, validate_compiled
from aeroagentsim.assistant.harness import Harness, without_state_assertions
from aeroagentsim.assistant.prompts import plan_status
from aeroagentsim.assistant.scene import diff, summarize
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore


def op(name, identity, **params):
    return {"id": identity, "op": name, "params": params}


@pytest.fixture
def project(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Research")
    sessions = SessionStore(tmp_path / "recordings")
    identifier = sessions.create(draft["id"])["id"]
    tools = ToolRunner(store, TemplateRegistry(), sessions, tmp_path / "recordings")
    return tools, identifier, draft


def propose(tools, identifier, operations, **extra):
    return tools.draft_plan(identifier, base={"workspace": tools.sessions.get(identifier)["workspace_id"]},
                            title="Charging expansion", summary="Adds a central charging station and couriers",
                            operations=operations, **extra)


def validate(tools, identifier, version):
    report = validate_compiled(tools.context(identifier), tools.sessions.plan_directory(identifier, version))
    assert report["valid"], report
    tools.sessions.validated(identifier, version, report["valid"], report["errors"])


def answer(tools, identifier, text, locale="en-US"):
    harness = Harness.__new__(Harness)
    harness.tools, harness.sessions = tools, tools.sessions
    harness._answer(identifier, {"content": text}, context={"locale": locale})
    return tools.sessions.get(identifier)["events"][-1]["text"]


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r4_003_empty_base_and_noop_alternative_cannot_masquerade_as_expansion(project, locale):
    tools, _, _ = project
    draft = tools.store.import_template("food-delivery", {}, registry=tools.templates)
    identifier = tools.sessions.create(draft["id"])["id"]
    with tools.sessions.edit(identifier) as session:
        session["locale"] = locale
    result = propose(tools, identifier, [])
    validate(tools, identifier, result["version"])
    base = tools.sessions.plan(identifier)
    assert base["operations"] == []
    assert base["what_changes"] == []
    assert sum(f["count"] for f in base["scene"]["fleets"]) == 2
    assert len(base["scene"]["places"]) == 7
    assert base["title"] == ("场景未改变" if locale == "zh-CN" else "Unchanged scene")
    assert "expansion" not in base["summary"] and "Adds" not in base["summary"]
    variants = [{"label": label, "title": "Expansion " + label, "summary": "Adds charging",
                 "operations": operations, "assumptions": []} for label, operations in [
                     ("No change", [op("set_duration", "same", seconds=base["scene"]["duration_s"])]),
                     ("One station", [op("add_place", "pad", id="central", role="charging_pad", at="center")])]]
    alternatives = tools.propose_alternatives(identifier, variants)
    noop, effective = alternatives["variants"]
    assert noop["what_changes"] == [] and effective["what_changes"]
    assert tools.sessions.plan(identifier)["version"] == base["version"]  # No implicit selection.
    unchanged_variant = tools.sessions.plan(identifier, noop["version"])
    assert unchanged_variant["title"] == base["title"]
    status = plan_status(tools.sessions.get(identifier), base, zh=locale == "zh-CN")
    assert ("没有改变" if locale == "zh-CN" else "makes no changes") in status
    assert ("先比较备选方案" if locale == "zh-CN" else "Compare the alternatives") in status
    assert "press Confirm" not in status and "点击“确认并应用”" not in status
    reply = answer(tools, identifier, "Plan revision v1 is validated; press Confirm on the board.", locale)
    assert "press Confirm" not in reply
    assert tools.store.get(draft["id"]) == draft


def test_t2_r4_003_api_rejects_unchanged_confirmation_without_saving_a_version(project, tmp_path):
    tools, identifier, draft = project
    result = propose(tools, identifier, [])
    validate(tools, identifier, result["version"])
    app = FastAPI()
    app.include_router(create_router(tools.store, run_root=tmp_path / "api", templates=tools.templates,
                                    sessions=tools.sessions, harness=SimpleNamespace(tools=tools)))
    with TestClient(app) as client:
        response = client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": result["version"]})
    assert response.status_code == 422 and "no changes" in response.json()["detail"]
    assert tools.sessions.get(identifier)["applied"] == []
    assert tools.store.get(draft["id"]) == draft


@pytest.mark.parametrize("template,vector", [("traffic-accident", [[2, 1]]), ("food-delivery", [2, 1, 1])])
def test_t3_r4_003_compile_review_reports_every_changed_wind_component_and_direction(tmp_path, template, vector):
    ctx = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    resolved = resolve_base(ctx, {"template": template})
    for engine in resolved.document["engines"].values():
        if engine["plugin"] == "environment":
            for identity, fields in engine["config"]["profiles"].items():
                for field, profile in fields.items():
                    if field in {"lowalt.wind", "oo:digital_twin.wind.horizontalVelocity"}:
                        profile["value"] = copy.deepcopy(vector)
                        for entity in resolved.document["entities"]:
                            if entity["id"] == identity and field in entity["facts"]:
                                entity["facts"][field] = copy.deepcopy(vector)
    result = compile_plan(ctx, {"template": template}, [op("set_wind", "wind", speed_mps=3)], tmp_path / "plan", resolved=resolved)
    assert result.ok, result.errors
    row = next(row for row in result.diff_base["changed"] if row["id"] == "environment")
    fields = {field["name"].rsplit(".", 1)[-1]: field for field in row["fields"]}
    assert fields["east_mps"]["before"] == 2 and fields["east_mps"]["after"] == 3
    assert fields["north_mps"]["before"] == 1 and fields["north_mps"]["after"] == 0
    assert fields["direction_deg"]["before"] == pytest.approx(math.degrees(math.atan2(1, 2)))
    assert fields["direction_deg"]["after"] == 0
    for key, unit in [("east_mps", "m/s"), ("north_mps", "m/s"), ("direction_deg", "°")]:
        assert unit in fields[key]["label"] and unit in fields[key]["before_text"]
        assert fields[key]["label_zh"] != fields[key]["label"]
    if template == "food-delivery":
        assert fields["up_mps"]["before"] == 1 and fields["up_mps"]["after"] == 0
    report = validate_compiled(ctx, tmp_path / "plan")
    assert report["valid"], report


def test_t3_r4_003_equal_speed_and_multiple_samples_do_not_hide_rotations(tmp_path):
    ctx = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    resolved = resolve_base(ctx, {"template": "traffic-accident"})
    document = resolved.document
    profile = document["engines"]["weather"]["config"]["profiles"]["wind.region"]["oo:digital_twin.wind.horizontalVelocity"]
    profile["value"] = [[2, 1], [1, 2]]
    before = summarize(document, directory=resolved.directory)
    profile["value"] = [[1, 2], [2, 1]]
    after = summarize(document, directory=resolved.directory)
    assert before["environment"]["wind"]["speed_mps"] == after["environment"]["wind"]["speed_mps"]
    review = next(row for row in diff(before, after)["changed"] if row["id"] == "environment")
    directions = [f for f in review["fields"] if f["name"].endswith("direction_deg")]
    assert len(directions) == 2 and all(f["before"] != f["after"] for f in directions)
    profile["mode"] = "gust"
    profile["gusts"] = [{"start_ns": 10_000_000_000, "end_ns": 15_000_000_000, "value": [[2, 1]]}]
    before = summarize(document, directory=resolved.directory)
    profile["gusts"][0]["value"] = [[1, 2]]
    after = summarize(document, directory=resolved.directory)
    review = next(row for row in diff(before, after)["changed"] if row["id"] == "environment")
    gust_direction = next(f for f in review["fields"] if ".gusts." in f["name"] and f["name"].endswith("direction_deg"))
    assert gust_direction["before"] != gust_direction["after"]
    assert "°" in gust_direction["before_text"] and "度" in gust_direction["before_text_zh"]


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r4_005_preserves_workflow_steps_accurate_status_and_markdown(project, locale):
    tools, identifier, _ = project
    result = propose(tools, identifier, [op("add_place", "pad", id="parity-pad", role="charging_pad", at=[75, 0, 0], capacity=4, power_w=6000)])
    validate(tools, identifier, result["version"])
    plan = tools.sessions.plan(identifier)
    zh = locale == "zh-CN"
    table = "| Property | Current value |\n| --- | --- |\n| Power | 6000 W |"
    steps = ("请先：\n1. 点击校验。\n2. 保存项目版本，然后初始化。" if zh else
             "To proceed:\n1. Click Validate in Configure.\n2. Save a project version, then initialize it.")
    correct = "方案修订 v1 校验通过。" if zh else "Plan revision v1 is validated."
    wrong = "当前草稿可初始化，无需校验。" if zh else "The current draft can be initialized. Nothing needs validating."
    source = table + "\n\n" + correct + "\n\n" + steps + "\n\n" + wrong
    workspace = {"revision": 3, "dirty": True, "validation": None, "latest_version": None}
    cleaned = without_state_assertions(source, session=tools.sessions.get(identifier), plan=plan, workspace=workspace)
    assert table.replace(" ", "") in cleaned.replace(" ", "")
    assert steps in cleaned and correct in cleaned
    conditional = "保存有效版本后再初始化。" if zh else "You can initialize after saving a valid project version."
    assert without_state_assertions(conditional, session=tools.sessions.get(identifier), plan=plan, workspace=workspace) == conditional
    assert ("可初始化" if zh else "can be initialized") not in cleaned
    assert ("无需校验" if zh else "Nothing needs validating") not in cleaned
    assert "\n1.\n" not in cleaned and "\n2.\n" not in cleaned
    # An operation table with version references retains its complete cells.
    operations = "| Operation | Reason |\n| --- | --- |\n| Change release times | Plan revision v1 is validated; keep the existing fleet. |"
    kept = without_state_assertions(operations, session=tools.sessions.get(identifier), plan=plan, workspace=workspace)
    assert kept.count("|") == operations.count("|")
    assert "keep the existing fleet" in kept


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r4_006_region_revision_has_one_effects_section_without_repeating_scene_creation(project, monkeypatch, locale):
    tools, identifier, _ = project
    first = propose(tools, identifier, [op("add_place", "pad", id="central-pad", role="charging_pad", at="center")])
    assert first["ok"]
    source = Path(__file__).parents[1] / "authoring/fixtures/wujiaochang.osm.xml"
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", json.dumps({"local": str(source)}))
    region = tools.revise_plan(identifier, add=[op("set_region", "region", extract="local", bounds=list(extract_bounds(source)), alt=0, level_height_m=3.5)])
    assert region["ok"], region
    validate(tools, identifier, region["version"])
    heading = "实际变更：" if locale == "zh-CN" else "What this changes:"
    narrative = "范围和原点将改变。" if locale == "zh-CN" else "The geographic bounds and origin will change."
    reply = answer(tools, identifier, narrative + "\n\n" + heading + "\n- Add central-pad\n- Add fleet\n\n" + heading + "\n- Add central-pad", locale)
    assert reply.count(heading) == 1 and narrative in reply
    assert "central-pad" not in reply
    assert ("ENU 原点" if locale == "zh-CN" else "ENU origin") in reply
    assert ("方案修订 v2" if locale == "zh-CN" else "Plan revision v2") in reply
