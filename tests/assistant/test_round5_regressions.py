"""Round-5 assistant facts: compiled inputs and acknowledged journal evidence."""
from __future__ import annotations

import copy
import re

import pytest

from aeroagentsim.assistant.compiler import CompileContext, compile_plan, resolve_base
from aeroagentsim.assistant.diff_display import enrich_diff
from aeroagentsim.assistant.harness import Harness, story_evidence, without_state_assertions
from aeroagentsim.assistant.scene import diff, expectations, summarize
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.story import run_story
from aeroagentsim.assistant.tools import ToolRunner
from aeroagentsim.assistant.wind import ground_wind_prose
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore

from .test_story import event, journal_run


def op(name, **params):
    return {"id": name, "op": name, "params": params}


def wind_profiles(document):
    return [p for e in document["engines"].values() if e["plugin"] == "environment"
            for fields in e["config"]["profiles"].values() for key, p in fields.items()
            if key in {"lowalt.wind", "oo:digital_twin.wind.horizontalVelocity"}]


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
@pytest.mark.parametrize("east,north,en,zh", [(0, 14, "north", "北"), (14, 0, "east", "东"),
                                             (0, -14, "south", "南"), (-14, 0, "west", "西")])
@pytest.mark.parametrize("template", ["food-delivery", "traffic-accident"])
def test_t2_r5_003_compiled_cardinal_vectors_ground_gust_direction_in_both_locales(tmp_path, locale, east, north, en, zh, template):
    ctx = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    base = resolve_base(ctx, {"template": template})
    base.document["run"]["until_ns"] = 600_000_000_000
    for profile in wind_profiles(base.document):
        profile["mode"] = "gust"
        value = [[east, north]] if template == "traffic-accident" else [east, north, 0]
        profile["gusts"] = [{"start_ns": 170_000_000_000, "end_ns": 200_000_000_000, "value": value}]
    compiled = compile_plan(ctx, {"template": template}, [], tmp_path / "plan", resolved=base)
    assert compiled.ok, compiled.errors
    source = "- 阵风：14 米/秒，170–200 秒（来自东方）。\n保留所有无人机。" if locale == "zh-CN" else "- Wind gust: 14 m/s, 170–200 s (from the east).\nKeep every drone."
    actual = ground_wind_prose(source, compiled.summary["environment"]["wind"], zh=locale == "zh-CN")
    assert (f"吹向{zh}" if locale == "zh-CN" else f"toward {en}") in actual
    assert ("矢量流向" if locale == "zh-CN" else "vector travel direction") in actual
    assert "170–200" in actual
    assert (f"东 {east}, 北 {north}" if locale == "zh-CN" else f"east {east}, north {north}") in actual
    assert ("保留所有无人机。" if locale == "zh-CN" else "Keep every drone.") in actual
    assert "from the east" not in actual and "来自东方" not in actual
    assert ("toward" if locale == "zh-CN" else "吹向") not in actual


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r5_003_final_answer_uses_actual_compiled_gust_and_pinned_run_not_model_direction(tmp_path, locale):
    store, sessions = WorkspaceStore(tmp_path / "workspaces", None), SessionStore(tmp_path / "recordings")
    workspace = store.create("Research")
    identifier = sessions.create(workspace["id"], locale=locale)["id"]
    tools = ToolRunner(store, TemplateRegistry(), sessions, tmp_path / "recordings")
    proposal = tools.draft_plan(identifier, base={"workspace": workspace["id"]}, title="Gust", summary="Gust",
                                operations=[op("add_gust", start_s=170, end_s=200, speed_mps=14)])
    assert proposal["ok"], proposal
    harness = Harness.__new__(Harness)
    harness.tools, harness.sessions = tools, sessions
    source = "阵风：14 米/秒，170–200 秒（来自东方）。" if locale == "zh-CN" else "Wind gust: 14 m/s, 170–200 s (from the east)."
    harness._answer(identifier, {"content": source}, context={"locale": locale})
    actual = sessions.get(identifier)["events"][-1]["text"]
    assert ("吹向东" if locale == "zh-CN" else "toward east") in actual
    assert "from the east" not in actual and "来自东方" not in actual
    # A pinned run with the opposite vector must override a newer proposal's wind.
    wind = copy.deepcopy(sessions.plan(identifier)["scene"]["environment"]["wind"])
    for profile in wind["profiles"].values():
        for gust in profile["gusts"]:
            for vector in gust["vectors"]:
                vector["east_mps"] = -14
                vector["direction_deg"] = 180
    harness._answer(identifier, {"content": source}, evidence={"run_id": "run-pinned", "wind": wind}, context={"locale": locale})
    actual = sessions.get(identifier)["events"][-1]["text"]
    assert ("吹向西" if locale == "zh-CN" else "toward west") in actual
    assert ("吹向东" if locale == "zh-CN" else "toward east") not in actual


def test_t2_r5_003_keeps_each_equal_speed_vector_and_markdown_table(tmp_path):
    ctx = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    base = resolve_base(ctx, {"template": "traffic-accident"})
    for profile in wind_profiles(base.document):
        profile["value"] = [[14, 0], [-14, 0]]
    wind = summarize(base.document, directory=base.directory)["environment"]["wind"]
    table = "| Setting | Value |\n| --- | --- |\n| Background wind | from the east |"
    grounded = ground_wind_prose(table, wind)
    assert "toward east" in grounded and "toward west" in grounded
    assert table.count("|") == grounded.count("|")
    assert "from the east" not in grounded
    assert ground_wind_prose("```\nWind from the east\n```", wind) == "```\nWind from the east\n```"


def workload_summary(count=100, mode="poisson"):
    return {"workloads": [{"id": "parcels", "kind": "parcel", "label": "parcel orders", "label_zh": "包裹订单",
                           "tasks": [f"parcel-{i}" for i in range(count)],
                           "release": {"mode": mode, **({"rate_per_s": .08, "expected_released": 18} if mode == "poisson" else {"times_s": list(range(count))})}}],
            "events": [], "fleets": []}


def parcel_payload(schema, task):
    payload = {"task": task}
    if schema == "lowalt.task.created":
        payload.update(kind="parcel", priority=1, origin="depot", destination="customer")
    else:
        payload["vehicle"] = "parcel-drone-1"
        if schema == "lowalt.task.assigned":
            payload["algorithm"] = "nearest"
        elif schema == "lowalt.task.completed":
            payload["latency_s"] = 10
    return payload


def recorded_parcels(tmp_path, released, executed, *, conflict=False, status="completed", duplicate=False):
    records = []
    for schema, count in [("lowalt.task.created", released), ("lowalt.task.assigned", executed),
                          ("lowalt.task.picked_up", executed), ("lowalt.task.completed", executed)]:
        for index in range(count):
            at = len(records) + 1
            records.append({"at_ns": at, "items": [event(schema, parcel_payload(schema, f"parcel-{index}"), at)]})
    if duplicate:
        at = len(records) + 1
        records.append({"at_ns": at, "items": [event("lowalt.task.completed", parcel_payload("lowalt.task.completed", "parcel-0"), at)]})
    if conflict:
        at = len(records) + 1
        records.append({"at_ns": at, "items": [event("aas.behaviour.conflict", {"reason": "recorded real conflict"}, at)]})
    return journal_run(tmp_path / "run", records, status=status)


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r5_004_default_parcel_poisson_executes_20_of_20_not_20_of_100(tmp_path, locale):
    # Use the real default template, including its stochastic arrival estimate.
    ctx = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    base = resolve_base(ctx, {"template": "parcel-delivery"})
    summary = summarize(base.document, directory=base.directory)
    work = next(w for w in summary["workloads"] if w["kind"] == "parcel")
    assert len(work["tasks"]) == 100 and work["release"]["mode"] == "poisson"
    wanted = expectations(base.document, summary)
    records = []
    for schema in ("lowalt.task.created", "lowalt.task.assigned", "lowalt.task.picked_up", "lowalt.task.completed"):
        for task in work["tasks"][:20]:
            at = len(records) + 1
            records.append({"at_ns": at, "items": [event(schema, parcel_payload(schema, task), at)]})
    story = run_story(journal_run(tmp_path / "run", records), wanted, locale=locale)
    assert len(story["expectations"]) == 4
    assert all(row["status"] == "happened" for row in story["expectations"])
    assert story["assessment"] == "satisfied"
    for row in story["expectations"]:
        assert row["progress"] == {"done": 20, "total": 20, "released": 20, "configured": 100}
        assert "slots are a cap" in row["explanation"]["text"]
        assert "名额是上限" in row["explanation"]["text_zh"]
        assert f"about {work['release']['expected_released']}" in row["explanation"]["text"]


@pytest.mark.parametrize("mode", ["poisson", "scheduled"])
def test_t2_r5_004_stochastic_and_fixed_workloads_use_different_denominators(tmp_path, mode):
    expected = expectations({}, workload_summary(mode=mode))
    story = run_story(recorded_parcels(tmp_path, 20, 20), expected)
    assert all(row["progress"]["total"] == (20 if mode == "poisson" else 100) for row in story["expectations"])
    assert all(row["status"] == ("happened" if mode == "poisson" else "missed") for row in story["expectations"])


@pytest.mark.parametrize("count,released,executed,status", [(100, 0, 0, "completed"), (2, 1, 1, "completed"),
                                                           (100, 20, 19, "completed"), (100, 20, 19, "running")])
def test_t2_r5_004_zero_releases_small_workloads_and_missing_execution_are_honest(tmp_path, count, released, executed, status):
    story = run_story(recorded_parcels(tmp_path, released, executed, status=status), expectations({}, workload_summary(count=count)))
    assert len(story["expectations"]) == 4
    for row in story["expectations"]:
        assert row["progress"]["total"] == released
        target = "not_evaluated" if released == 0 else "happened" if row["id"].endswith(":created") or executed == released else "missed" if status == "completed" else "pending"
        assert row["status"] == target
    if not released:
        assert story["assessment"] == "unassessed"


def test_t2_r5_004_duplicate_completion_cannot_hide_a_missing_order_and_conflicts_stay_visible(tmp_path):
    expected = expectations({}, workload_summary())
    story = run_story(recorded_parcels(tmp_path, 20, 19, conflict=True, duplicate=True), expected)
    completion = next(row for row in story["expectations"] if row["id"].endswith(":completed"))
    assert completion["status"] == "missed" and completion["progress"]["done"] == 19
    assert story["assessment"] == "incomplete" and len(story["failures"]) == 1
    assert story["failures"][0]["payload"]["reason"] == "recorded real conflict"


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r5_004_summary_handles_unevaluated_checks_and_explains_the_release_basis(tmp_path, locale):
    expected = expectations({}, workload_summary())
    story = run_story(recorded_parcels(tmp_path, 0, 0), expected)
    sessions = SessionStore(tmp_path / "sessions")
    identifier = sessions.create(locale=locale)["id"]
    sessions.append(identifier, {"role": "user", "content": "Summarize the run"})
    harness = Harness.__new__(Harness)
    harness.sessions, harness.tools = sessions, object()
    harness._answer(identifier, {"content": "Unsupported success claim"}, story_evidence("run-test", story), {"locale": locale})
    text = sessions.get(identifier)["events"][-1]["text"]
    assert "0/0" in text and "0/4" not in text
    assert ("未评估" if locale == "zh-CN" else "not evaluated") in text
    assert ("名额是上限" if locale == "zh-CN" else "slots are a cap") in text


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_t2_r4_005_renumbers_filtered_workflow_and_keeps_validate(locale):
    source = ("先执行：\n1. 打开运行与记录。\n2. 当前草稿可初始化，无需校验。\n3. 校验当前草稿。\n4. 保存项目版本。\n5. 初始化，然后启动。" if locale == "zh-CN" else
              "To proceed:\n1. Open Run & recording.\n2. The current draft can be initialized; nothing needs validation.\n3. Validate the current draft.\n4. Save a project version.\n5. Initialize it, then Start.")
    workspace = {"revision": 3, "dirty": True, "validation": None, "latest_version": None}
    filtered = without_state_assertions(source, workspace=workspace)
    assert re.findall(r"^(\d+)\.", filtered, re.M) == ["1", "2", "3", "4"]
    assert ("2. 校验当前草稿。" if locale == "zh-CN" else "2. Validate the current draft.") in filtered
    assert ("无需校验" if locale == "zh-CN" else "can be initialized") not in filtered
    table = "| Step | Action |\n| --- | --- |\n| Validate | Click Validate. |"
    fence = "```\n1. first\n3. code example\n```"
    assert without_state_assertions(table + "\n\n" + fence, workspace=workspace) == table + "\n\n" + fence


def test_d16_catalog_labels_empty_values_and_most_specific_fields(monkeypatch):
    monkeypatch.setattr("aeroagentsim.services.algorithm_catalog.discover_algorithm_catalog", lambda: {
        "path_planning": [{"id": "corridor_astar", "label": "Corridor A*", "label_zh": "走廊 A*"}]})
    row = diff({"algorithms": {"path_planning": None}}, {"algorithms": {"path_planning": "corridor_astar"}})["changed"][0]
    field = row["fields"][0]
    assert field["before_text"] == "—" and field["after_text"] == "Corridor A*"
    assert field["after_text_zh"] == "走廊 A*"
    fields = [{"name": "wind", "before": None, "after": {"speed_mps": 0}},
              {"name": "wind.speed_mps", "before": None, "after": 0},
              *[{"name": f"empty_{index}", "before": empty, "after": empty} for index, empty in enumerate([None, "", "none", [], {}])],
              {"name": "vehicles", "before": [], "after": ["one", "two"]}]
    result = enrich_diff({"added": [], "changed": [{"kind": "scene", "id": "environment", "fields": fields}], "removed": []})
    fields = {field["name"]: field for field in result["changed"][0]["fields"]}
    assert "wind" not in fields
    assert fields["wind.speed_mps"]["label"] == "Wind speed (m/s)"
    assert fields["wind.speed_mps"]["before_text"] == "—" and fields["wind.speed_mps"]["after_text"] == "0 m/s"
    assert all(fields[f"empty_{index}"]["before_text"] == fields[f"empty_{index}"]["before_text_zh"] == "—" for index in range(5))
    assert fields["vehicles"]["after_text"] == "one, two"


def test_d16_new_wind_profile_changes_expand_to_human_vector_leaves():
    old = {"environment": {"wind": {"speed_mps": None, "profiles": {}}}}
    new = {"environment": {"wind": {"speed_mps": 0, "profiles": {"weather.wind": {
        "mode": "constant", "vectors": [{"east_mps": 0, "north_mps": 0, "direction_deg": None}], "gusts": []}}}}}
    fields = diff(old, new)["changed"][0]["fields"]
    names = {field["name"] for field in fields}
    assert "wind.profiles.weather.wind" not in names
    east = next(field for field in fields if field["name"].endswith("east_mps"))
    assert east["label"] == "Eastward wind velocity (m/s)" and east["after_text"] == "0 m/s"
