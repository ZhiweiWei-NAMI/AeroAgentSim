"""Round-6 guidance uses real draft, selected-plan and recorded-event state."""
from __future__ import annotations

import re

import pytest

from aeroagentsim.assistant.compiler import apply_plan
from aeroagentsim.assistant.harness import Harness, story_evidence
from aeroagentsim.assistant.story import failure_summary

from .test_round4_regressions import answer, op, project, propose, validate


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r6_001_form_edits_need_only_the_real_draft_workflow(project, locale):
    tools, identifier, draft = project
    zh = locale == "zh-CN"
    proposal = propose(tools, identifier, [op("add_place", "pad", id="parity-pad", role="charging_pad", at="center", capacity=2, power_w=5000)])
    assert proposal["ok"], proposal
    validate(tools, identifier, proposal["version"])
    plan = tools.sessions.plan(identifier)
    result = apply_plan(tools.context(identifier), tools.sessions.plan_directory(identifier, plan["version"]),
                        base=plan["base"], workspace_id=draft["id"], name=plan["title"], note="Research")
    tools.sessions.applied(identifier, {**result, "plan_version": plan["version"], "applied_at": "2026-10-13T00:00:00Z"})
    edited = tools.store.operations(draft["id"], {"operations": [op("update_place", "form", id="parity-pad", at=[75, 0, 0], capacity=4, power_w=6000)]}, templates=tools.templates)
    assert edited["dirty"] and edited.get("validation") is None
    entity = tools.get_current_scene(identifier, id="parity-pad")["participant"]["entity"]
    assert entity["facts"]["lowalt.site_capacity"] == 4
    source = ("| 字段 | 值 |\n| --- | --- |\n| 位置 | [75,0,0] |\n| 功率 | 6000 W |\n\n"
              "1. 打开方案板，重新校验。\n2. 重写并编译方案修订，更新 op-1。\n3. 确认并应用后检查报告。\n4. 初始化场景。\nSuggestions: 重写方案以匹配表单修改" if zh else
              "| Field | Value |\n| --- | --- |\n| Position | [75,0,0] |\n| Power | 6000 W |\n\n"
              "1. Open the plan board and re-validate.\n2. Draft/compile a plan revision that captures the Form edits. Update op-1.\n3. Check the apply report after confirmation.\n4. Initialize the scene.\nSuggestions: Rewrite the plan to match the Form edits")
    reply = answer(tools, identifier, source, locale)
    assert "[75,0,0]" in reply and "6000 W" in reply
    assert "op-1" not in reply and "plan board" not in reply and "方案板" not in reply
    assert "Draft/compile" not in reply and "重写" not in reply
    assert tools.sessions.get(identifier)["events"][-1]["suggestions"] == []
    assert re.findall(r"^(\d+)\.", reply, re.M) == ["1", "2", "3", "4", "5"]
    labels = ["打开配置", "校验", "保存版本", "初始化 v…", "启动"] if zh else ["Open Configure", "Click Validate", "Save version", "Initialize v…", "Click Start"]
    steps = "\n".join(line for line in reply.splitlines() if re.match(r"^\d+\.", line)).lower()
    assert all(label.lower() in steps for label in labels)
    assert [steps.index(label.lower()) for label in labels] == sorted(steps.index(label.lower()) for label in labels)
    # A valid revision skips validation; saving it moves initialization to v2.
    report = tools.store.validate(draft["id"], revision=edited["revision"])
    assert report["valid"], report
    reply = answer(tools, identifier, source, locale)
    assert ("点击“校验”" if zh else "Click Validate") not in reply
    assert "重写" not in reply and "Draft/compile" not in reply
    assert ("保存版本" if zh else "Save version") in reply
    version = tools.store.save_version(draft["id"], {"revision": edited["revision"]})
    assert version["version"] == 2
    reply = answer(tools, identifier, source, locale)
    assert ("初始化 v2" if zh else "Initialize v2") in reply
    assert ("保存版本" if zh else "Save version") not in reply
    assert tools.sessions.plan(identifier)["version"] == 1


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r6_002_changed_base_does_not_claim_an_alternative_is_selected(project, locale):
    tools, _, _ = project
    workspace = tools.store.import_template("food-delivery", {}, registry=tools.templates)
    identifier = tools.sessions.create(workspace["id"], locale=locale)["id"]
    base = propose(tools, identifier, [op("set_fleet", "fleet-size", fleet="fleet", count=3)])
    assert base["ok"], base
    validate(tools, identifier, base["version"])
    fleet_operation = next(row["id"] for row in tools.sessions.plan(identifier)["operations"] if row["op"] == "set_fleet")
    variants = tools.propose_alternatives(identifier, [
        {"label": label, "title": label, "summary": "Four UAVs", "assumptions": [], "changes": {
            "update": [op("set_fleet", fleet_operation, fleet="fleet", count=4)],
            "add": [op("add_place", "pad", id="central-pad", role="charging_pad", at="center", capacity=capacity)]}}
        for label, capacity in [("One slot", 1), ("Two slots", 2)]])
    zh = locale == "zh-CN"
    reply = answer(tools, identifier, "请点击确认并应用四架无人机的基案。" if zh else "Press Confirm on the plan board to apply the four-UAV base.", locale)
    assert ("v1 包含 3 架无人机" if zh else "v1 contains 3 UAVs") in reply
    assert ("v2 包含 4 架无人机" if zh else "v2 contains 4 UAVs") in reply
    assert ("v3 包含 4 架无人机" if zh else "v3 contains 4 UAVs") in reply
    assert ("当前仍选中此基案" if zh else "this base is still selected") in reply
    assert ("选择此方案" if zh else "Choose this option") in reply
    assert "Press Confirm on the plan board" not in reply
    assert tools.sessions.plan(identifier)["version"] == 1
    choice = variants["variants"][0]["version"]
    validate(tools, identifier, choice)
    tools.sessions.choose(identifier, choice)
    reply = answer(tools, identifier, "点击确认并应用基案 v1。" if zh else "Click Confirm to save base plan revision v1.", locale)
    assert ("v2 包含 4 架无人机" if zh else "v2 contains 4 UAVs") in reply
    assert "base plan revision v1" not in reply and "基案 v1" not in reply
    assert ("确认并应用" if zh else "Confirm and apply") in reply
    assert tools.store.get(workspace["id"]) == workspace


def conflict(a, b, at):
    return {"schema": "lowalt.airspace.conflict", "at_ns": at * 1_000_000_000,
            "text": f"Drone {a} and Drone {b} are too close", "text_zh": f"无人机 {a}与无人机 {b}间隔过近",
            "payload": {"a": f"drone-{a}", "b": f"drone-{b}"}}


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r6_003_group_total_and_pair_counts_have_separate_captions(project, locale):
    tools, identifier, _ = project
    failures = [conflict(1, 2, 45), conflict(2, 1, 51), conflict(2, 3, 123), conflict(1, 3, 129), conflict(2, 3, 130)]
    group = failure_summary(failures)[0]
    assert group["count"] == 5
    assert {tuple(row["entities"]): row["count"] for row in group["pairs"]} == {("drone-1", "drone-2"): 2, ("drone-2", "drone-3"): 2, ("drone-1", "drone-3"): 1}
    assert "Drone" not in group["text"]
    evidence = story_evidence("recorded-run", {"status": "completed", "coverage": {"recorded_events": 5}, "failures": failures, "expectations": [], "events": failures})
    tools.sessions.append(identifier, {"role": "user", "content": "Summarize the run"})
    harness = Harness.__new__(Harness)
    harness.tools, harness.sessions = tools, tools.sessions
    harness._answer(identifier, {"content": "Untrusted summary"}, evidence, {"locale": locale})
    reply = tools.sessions.get(identifier)["events"][-1]["text"]
    assert ("空域冲突 (×5)" if locale == "zh-CN" else "Airspace conflicts (×5)") in reply
    assert reply.count("(×2)") == 2 and reply.count("(×1)") == 1
    assert not re.search(r"(?:Drone|无人机).*×5", reply)
    assert "130 s:" not in reply


def test_t2_r6_003_failure_without_pair_is_explicitly_a_last_example():
    group = failure_summary([{"schema": "traffic.capture.failed", "text": "Bravo capture failed", "at_ns": 20,
                              "payload": {"actor": "bravo", "reason": "camera unavailable"}}])[0]
    assert group["text"] == "Capture failures" and group["pairs"] == []
    assert group["last_example"]["text"] == "Bravo capture failed"
    assert group["reason"] == "camera unavailable"
