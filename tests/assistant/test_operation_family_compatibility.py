"""Regression pins for the builtin extraction review; no runtime is started."""

from __future__ import annotations

import builtins
import copy
import io
import json
import os
import socket
import urllib.request
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

import aeroagentsim.assistant_api as api
from aeroagentsim.assistant.catalogue import OperationCatalogue, position_fields
from aeroagentsim.assistant.compiler import (
    CompileContext as HostContext,
    PlanConflict,
    apply_patches,
    apply_plan,
    compile_operations,
    compile_plan,
    resolve_base,
    validate_compiled,
)
from aeroagentsim.assistant.operations import OperationError, obj
from aeroagentsim.assistant.scene import _SceneSummary, _family_summary, _position, expectations, interactions, summarize
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner
from aeroagentsim.assistant_api import (
    AssistantOperationPlugin,
    AssistantReadSpec,
    CompileContext,
    CompileResult,
    OperationSpec,
    ResourceProposal,
)
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.packs.lowalt.assistant import plugin as lowalt_plugin
from aeroagentsim.packs.traffic_accident.assistant import plugin as traffic_plugin
from aeroagentsim.scenario import load_scenario

SECOND = 1_000_000_000


@pytest.fixture
def host(tmp_path: Path) -> HostContext:
    return HostContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)


def install(monkeypatch, plugin: AssistantOperationPlugin) -> None:
    monkeypatch.setattr(api, "entry_points", lambda **kwargs: [SimpleNamespace(name=plugin.name, load=lambda: plugin)])


def forbidden_io(*args, **kwargs):
    raise AssertionError("Dependency ordering and compilation must remain pure")


def pure_compile(monkeypatch, document: dict, operations: list[dict]):
    catalogue = OperationCatalogue()
    context = CompileContext(document)
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "open", forbidden_io)
        blocked.setattr(io, "open", forbidden_io)
        blocked.setattr(os, "open", forbidden_io)
        blocked.setattr(socket, "socket", forbidden_io)
        blocked.setattr(socket, "create_connection", forbidden_io)
        blocked.setattr(urllib.request, "urlopen", forbidden_io)
        result = compile_operations(context, operations, catalogue=catalogue)
        changed = apply_patches(document, result.patches)
    return result, changed


# Finding 1: repeated legacy IDs are not cross-provider collisions.
@pytest.mark.parametrize("existing,second_speed", [(False, None), (True, None), (True, 9)])
def test_multi_profile_gusts_keep_legacy_cards_and_expectation_ids(host, tmp_path, existing, second_speed):
    base = {"template": "crop-spraying"}
    scene = resolve_base(host, base)
    weather = scene.document["engines"]["environment"]["config"]["profiles"]
    profile = weather["weather"]["lowalt.wind"]
    if not existing:
        profile.pop("gusts", None)
        profile["mode"] = "constant"
    second = copy.deepcopy(next(e for e in scene.document["entities"] if e["id"] == "weather"))
    second["id"] = "weather-secondary"
    scene.document["entities"].append(second)
    weather[second["id"]] = copy.deepcopy(weather["weather"])
    if second_speed is not None:
        weather[second["id"]]["lowalt.wind"]["gusts"][0]["value"] = [float(second_speed), 0.0, 0.0]
    operations = [] if existing else [{"id": "gust", "op": "add_gust", "params": {"start_s": 60, "end_s": 70, "speed_mps": 9}}]
    before = copy.deepcopy(scene.document)
    result = compile_plan(host, base, operations, tmp_path / "plan", resolved=scene)
    assert result.ok, result.errors
    assert scene.document == before
    gusts = [event for event in result.summary["events"] if event["kind"] == "wind_gust"]
    assert len(gusts) == 2
    start = 90 if existing else 60
    assert [event["id"] for event in gusts] == [f"wind-gust-{start * SECOND}"] * 2
    wind = result.summary["environment"]["wind"]
    assert len(wind["profiles"]) == len(wind["gusts"]) == 2
    assert set(result.document["engines"]["environment"]["config"]["profiles"]) == {"weather", "weather-secondary"}
    weather_rows = [row for row in result.expectations if row["match"]["schema"].startswith("lowalt.weather.")]
    prefix = f"event:wind-gust-{start * SECOND}:fleet"
    assert [row["id"] for row in weather_rows] == [prefix + ":paused", prefix + ":resumed"] * 2
    if second_speed is None:
        assert weather_rows[:2] == weather_rows[2:]
    else:
        assert [row["match"]["where"]["wind_mps"] for row in weather_rows] == [8.0, 2.0, 9.0, 2.0]
    assert weather_rows[1]["depends_on"] == [prefix + ":paused"]
    assert validate_compiled(host, tmp_path / "plan")["valid"]


def test_another_provider_cannot_reuse_a_builtin_summary_id():
    document = {"id": "memory", "run": {"until_ns": SECOND}, "entities": [], "engines": {}}
    first = AssistantOperationPlugin(name="first", read=AssistantReadSpec(summarize=lambda context: {"events": [{"id": "same"}, {"id": "same"}]}))
    second = AssistantOperationPlugin(name="second", read=AssistantReadSpec(summarize=lambda context: {"events": [{"id": "same"}]}))
    own = summarize(document, catalogue=OperationCatalogue((first,), aliases={}))
    assert own["events"] == [{"id": "same"}, {"id": "same"}]
    with pytest.raises(ValueError, match="Conflicting assistant summary ids in events"):
        summarize(document, catalogue=OperationCatalogue((first, second), aliases={}))


@pytest.mark.parametrize("projection", ["summary", "expectations", "interactions"])
def test_projection_failures_return_located_compile_diagnostics(host, tmp_path, monkeypatch, projection):
    def fail():
        raise ValueError("broken contributed projection")

    def read(context):
        if projection == "summary" and context.document["run"]["until_ns"] == 123 * SECOND:
            fail()
        return {}

    def expected(document, summary):
        if projection == "expectations":
            fail()
        return []

    def diagram(summary):
        if projection == "interactions":
            fail()
        return {}

    install(monkeypatch, AssistantOperationPlugin(name="example", read=AssistantReadSpec(summarize=read, expectations=expected, interactions=diagram)))
    result = compile_plan(host, {"template": "food-delivery"}, [{"id": "duration", "op": "set_duration", "params": {"seconds": 123}}], tmp_path / "plan")
    assert not result.ok
    assert result.errors == [{"op_id": "duration", "field": projection, "message": "ValueError: broken contributed projection"}]
    assert result.summary == result.interactions == result.diff_base == {}
    assert result.expectations == []
    assert json.loads((tmp_path / "plan" / "compile.json").read_text())["errors"] == result.errors


# Finding 2: an approved tombstone must survive staging and Confirm.
def resource_plugin() -> AssistantOperationPlugin:
    def proposal(content, context, params):
        return CompileResult(resources=(ResourceProposal("notes/obsolete.txt", content),))

    return AssistantOperationPlugin(name="example", operations=tuple(
        OperationSpec(id="example." + name, parameters=obj({}), title=name, title_zh="资源提案",
                      description="Propose a resource change", description_zh="提议资源修改", compile=partial(proposal, content))
        for name, content in (("remove", None), ("restore", b"restored"))
    ))


def removal_plan(host, tmp_path, monkeypatch, *, restore=False):
    install(monkeypatch, resource_plugin())
    draft = host.store.import_template("food-delivery", {}, registry=host.templates)
    directory = host.store.directory(draft["id"])
    (directory / "notes").mkdir()
    (directory / "notes/obsolete.txt").write_bytes(b"obsolete")
    (directory / "notes/keep.txt").write_bytes(b"keep")
    base = {"workspace": draft["id"], "revision": draft["revision"]}
    operations = [{"id": "remove", "op": "example.remove", "params": {}}]
    if restore:
        operations.append({"id": "restore", "op": "example.restore", "params": {}})
    result = compile_plan(host, base, operations, tmp_path / "plan")
    assert result.ok, result.errors
    assert (directory / "notes/obsolete.txt").read_bytes() == b"obsolete"
    return draft, directory, base, result


@pytest.mark.parametrize("restore", [False, True])
def test_confirm_applies_exact_resource_removals_and_keeps_older_versions(host, tmp_path, monkeypatch, restore):
    draft, directory, base, result = removal_plan(host, tmp_path, monkeypatch, restore=restore)
    assert host.store.validate(draft["id"])["valid"]
    original = host.store.save_version(draft["id"], {"revision": draft["revision"]})
    old_files = host.store.version_directory(draft["id"], original["version"])
    assert (result.files_dir / "notes/obsolete.txt").exists() == restore
    assert any(row["id"] == "notes/obsolete.txt" for row in result.diff_base["removed"])
    assert validate_compiled(host, tmp_path / "plan")["valid"]
    report = apply_plan(host, tmp_path / "plan", base=base, workspace_id=draft["id"], name="Confirmed", note="Resource proposal")
    new_files = host.store.version_directory(draft["id"], report["version"])
    assert report["version"] == original["version"] + 1
    assert host.store.get(draft["id"])["name"] == "Confirmed"
    assert (old_files / "notes/obsolete.txt").read_bytes() == b"obsolete"
    assert (directory / "notes/obsolete.txt").exists() == restore
    assert (new_files / "notes/obsolete.txt").exists() == restore
    if restore:
        assert (directory / "notes/obsolete.txt").read_bytes() == b"restored"
        assert (new_files / "notes/obsolete.txt").read_bytes() == b"restored"
    assert (directory / "notes/keep.txt").read_bytes() == (new_files / "notes/keep.txt").read_bytes() == b"keep"


def test_confirm_rejects_a_removal_ancestor_aliased_into_an_immutable_version(host, tmp_path, monkeypatch):
    draft, directory, base, result = removal_plan(host, tmp_path, monkeypatch)
    assert host.store.validate(draft["id"])["valid"]
    original = host.store.save_version(draft["id"], {"revision": draft["revision"]})
    old_files = host.store.version_directory(draft["id"], original["version"])
    old_bytes = {path.relative_to(old_files): path.read_bytes() for path in old_files.rglob("*") if path.is_file()}
    assert validate_compiled(host, tmp_path / "plan")["valid"]
    assert not (result.files_dir / "notes/obsolete.txt").exists()
    notes = directory / "notes"
    assert notes.resolve() == directory.resolve() / "notes"
    notes.rename(directory / "notes-before-alias")
    notes.symlink_to(old_files / "notes", target_is_directory=True)
    assert (notes / "obsolete.txt").resolve().is_relative_to(directory.resolve() / "versions")
    before = (directory / "draft.json").read_bytes()
    versions = host.store.versions(draft["id"])

    with pytest.raises(PlanConflict, match="Resource removal"):
        apply_plan(host, tmp_path / "plan", base=base, workspace_id=draft["id"], name="Confirmed", note="Must not apply")

    assert (directory / "draft.json").read_bytes() == before
    assert notes.is_symlink()
    assert (old_files / "notes/obsolete.txt").read_bytes() == b"obsolete"
    assert {path.relative_to(old_files): path.read_bytes() for path in old_files.rglob("*") if path.is_file()} == old_bytes
    assert host.store.versions(draft["id"]) == versions


@pytest.mark.parametrize("conflict", ["revision", "workspace", "files", "removals", "symlink"])
def test_resource_removals_remain_behind_conflict_and_path_checks(host, tmp_path, monkeypatch, conflict):
    draft, directory, base, result = removal_plan(host, tmp_path, monkeypatch)
    workspace = draft["id"]
    if conflict == "revision":
        host.store.save(workspace, {"name": "Changed while discussing"})
    elif conflict == "workspace":
        workspace = "another-workspace"
    elif conflict == "files":
        (result.files_dir / "unexpected.txt").write_bytes(b"changed")
    elif conflict == "removals":
        path = tmp_path / "plan" / "compile.json"
        record = json.loads(path.read_text())
        record["contributions"]["resources"][0]["path"] = "notes/keep.txt"
        path.write_text(json.dumps(record))
    else:
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"outside")
        target = directory / "notes/obsolete.txt"
        assert target.resolve().is_relative_to(directory.resolve())
        target.unlink()
        target.symlink_to(outside)
    before = (directory / "draft.json").read_bytes()
    with pytest.raises(PlanConflict):
        apply_plan(host, tmp_path / "plan", base=base, workspace_id=workspace, name="Confirmed", note="Must not apply")
    assert (directory / "draft.json").read_bytes() == before
    assert (directory / "notes/obsolete.txt").read_bytes() == (b"outside" if conflict == "symlink" else b"obsolete")
    assert (directory / "notes/keep.txt").read_bytes() == b"keep"
    assert host.store.versions(draft["id"]) == []


# Finding 3: cards are not permission to manufacture another family's evidence.
@pytest.mark.parametrize("domain", [None, "example"])
@pytest.mark.parametrize("ownership_free", [False, True])
def test_a_family_with_no_expectations_does_not_acquire_them_from_lowalt(domain, ownership_free):
    event = {"id": "example-event", "kind": "signal", "label": "External signal", "label_zh": "外部信号",
             "schema": "example.signal", "params": {}, "at_s": 1, "place": None}
    if domain is not None:
        event["domain"] = domain
    external = AssistantOperationPlugin(name="example", read=AssistantReadSpec(summarize=lambda context: {"events": [event]}))
    catalogue = OperationCatalogue((lowalt_plugin(), traffic_plugin(), external))
    document = {"id": "memory", "run": {"until_ns": 10 * SECOND}, "engines": {}, "entities": []}
    summary = summarize(document, catalogue=catalogue)
    if ownership_free:
        summary = json.loads(json.dumps(summary))
    assert summary["events"] == [event]
    assert expectations(document, summary, catalogue=catalogue) == []
    assert interactions(summary, catalogue=catalogue)["steps"] == []


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("builtin_event", [False, True])
def test_session_restart_preserves_read_ownership_for_a_no_expectations_family(tmp_path, monkeypatch, legacy, builtin_event):
    from aeroagentsim.assistant.catalogue import callbacks

    event = {"id": "example-event", "kind": "signal", "label": "External signal", "label_zh": "外部信号",
             "schema": "example.signal", "params": {}, "at_s": 1, "place": "target"}
    external = AssistantOperationPlugin(name="example", read=AssistantReadSpec(summarize=lambda context: {"events": [event]}))
    install(monkeypatch, external)
    document = {"id": "memory", "run": {"until_ns": 10 * SECOND}, "engines": {},
                "entities": [{"id": identity, "type": "example:Actor", "facts": {}} for identity in ("system", "target")]}
    if builtin_event:
        document["behaviours"] = [{"id": "fixture", "injection_points": [{"id": "own-signal", "emits": "memory.signal"}]}]
        document["bindings"] = {"commands": [{"schema": "aas.runtime.inject_event", "at_ns": SECOND,
                                              "payload": {"injection_point": "own-signal", "payload": {}}}]}
    summary = summarize(document)
    expected, diagram = expectations(document, summary), interactions(summary)
    assert [row["id"] for row in expected] == (["event:scheduled-1"] if builtin_event else [])
    sessions = SessionStore(tmp_path)
    identifier = sessions.create()["id"]
    sessions.put_plan(identifier, {"version": 1, "title": "External proposal", "note": "No expectations", "variant": None,
                                   "compile": {"ok": True}, "scene": summary, "_document": document,
                                   "expectations": expected, "interactions": diagram})
    path = sessions.directory(identifier) / "session.json"
    serialized = json.loads(path.read_text())
    stored = serialized["plans"]["1"]
    assert type(stored["scene"]) is dict
    assert event in stored["scene"]["events"]
    assert stored["_scene_contributions"]["example"]["events"] == [event]
    assert stored["expectations"] == expected and stored["interactions"] == diagram
    if legacy:
        stored.pop("_scene_contributions")
        path.write_text(json.dumps(serialized))

    restarted = SessionStore(tmp_path)
    tools = ToolRunner(None, None, restarted, tmp_path)
    assert restarted.plan(identifier)["expectations"] == expected
    if not legacy:
        restored = restarted.plan(identifier)["scene"]
        assert _family_summary(restored, "example")["events"] == [event]
        assert expectations(document, restored) == expected
    for scope in ("auto", "proposed"):
        restored_document, restored = tools._scene(identifier, scope)
        assert restored_document == document and restored == summary
        assert _family_summary(restored, "example")["events"] == [event]
        assert event not in _family_summary(restored, "lowalt")["events"]
        assert expectations(restored_document, restored) == expected
        assert interactions(restored) == diagram
        assert tools.explain_relation(identifier, "system", "target", scope=scope)["steps"] == []
        watched = [line for callback in callbacks("watch_scene") for line in callback(restored_document, restored)]
        assert not any("External signal" in line for line in watched)


def activation(event) -> dict:
    return {"id": "event:" + event["id"], "text": "Traffic accident", "text_zh": "交通事故", "layer": "setup",
            "match": {"schema": "traffic.incident.activated", "where": event["params"]}, "approx_s": event["at_s"],
            "precheck": None, "depends_on": []}


def test_traffic_owns_activation_setup_and_lanes_without_lowalt_reads():
    catalogue = OperationCatalogue((AssistantOperationPlugin(name="lowalt"), traffic_plugin()))
    prepared = TemplateRegistry().apply("traffic-accident")
    document = load_scenario(prepared.document, base=prepared.base).document
    summary = summarize(document, directory=prepared.base, catalogue=catalogue)
    event = summary["events"][0]
    expected = expectations(document, summary, catalogue=catalogue)
    assert expected[0] == activation(event)
    assert {row["match"]["schema"] for row in expected} >= {"traffic.incident.detected", "traffic.capture.stored"}
    diagram = interactions(summary, catalogue=catalogue)
    assert diagram["steps"][0] == {"id": "event:" + event["id"], "from": "system", "to": event["place"],
                                     "text": "Traffic accident", "text_zh": "交通事故", "layer": "setup", "decided_by": None,
                                     "branch": None, "at_s": event["at_s"], "expect": "event:" + event["id"]}
    assert [lane["id"] for lane in diagram["lanes"]] == ["system", "traffic-uavs", "traffic-road-vehicles", event["place"]]
    assert diagram["lanes"][0] == {"id": "system", "label": "Task allocation", "label_zh": "任务分配", "kind": "system"}
    assert diagram["lanes"][-1] == {"id": event["place"], "label": "Traffic accident", "label_zh": "交通事故", "kind": "incident"}


@pytest.mark.parametrize("workload_id", ["parcels", "event:parcels"])
def test_default_mixed_projection_order_and_copy_match_the_legacy_composition(workload_id):
    catalogue = OperationCatalogue((lowalt_plugin(), traffic_plugin()))
    traffic_event = {"id": "traffic-incident:incident.01", "domain": "traffic", "kind": "traffic_accident", "label": "Traffic accident", "label_zh": "交通事故",
                     "at_s": 20, "place": "incident.01", "schema": "traffic.incident.activated", "params": {"incident": "incident.01"}}
    summary = {"duration_s": 100, "algorithms": {}, "cooperation": None,
               "fleets": [{"id": "couriers", "members": ["couriers-1"], "label": "Parcel drone", "label_zh": "包裹无人机", "task_kinds": ["parcel"]},
                          {"id": "traffic-uavs", "domain": "traffic", "members": ["uav.01"], "label": "Traffic UAVs", "label_zh": "交通无人机", "vehicle_class": "aas:TrafficUAV", "task_kinds": []}],
               "places": [{"id": "depot", "label": "Depot", "label_zh": "仓库"}, {"id": "customer", "label": "Customer", "label_zh": "客户"}],
               "workloads": [{"id": workload_id, "kind": "parcel", "count": 1, "tasks": ["parcel-1"], "from": ["depot"], "to": ["customer"],
                              "release": {"mode": "scheduled", "times_s": [0]}, "label": "Parcel", "label_zh": "包裹"}],
               "events": [traffic_event,
                          {"id": "scheduled-1", "kind": "restaurant_delay", "label": "Kitchen delay", "label_zh": "厨房延迟", "at_s": 30,
                           "place": "depot", "schema": "lowalt.operator.restaurant_delay", "params": {"restaurant": "depot", "delay_s": 3}},
                          {"id": "wind-gust-40000000000", "kind": "wind_gust", "label": "Wind gust: 9 m/s", "label_zh": "阵风：风速 9 米/秒",
                           "at_s": 40, "end_s": 50, "speed_mps": 9, "after_speed_mps": 2, "effects": []}],
               "traffic": {"incidents": [{"id": "incident.01"}], "bound_chains": ["traffic.incident_report"]}}
    # This authored compatibility fixture explicitly owns its untagged cards;
    # an ownership-free JSON summary must not guess that they belong to lowalt.
    sections = ("fleets", "places", "workloads", "events", "rules", "routine")
    summary = _SceneSummary(summary, contributions={
        provider: {section: [row for row in summary.get(section, []) if row.get("domain") == domain] for section in sections}
        for provider, domain in (("lowalt", None), ("traffic", "traffic"))
    })
    expected = expectations({}, summary, catalogue=catalogue)
    assert [row["id"] for row in expected] == ["task:parcel-1:created", "task:parcel-1:assigned", "task:parcel-1:picked_up", "task:parcel-1:completed",
                                               "event:traffic-incident:incident.01", "event:scheduled-1", "traffic:traffic.incident.detected", "traffic:traffic.broadcast"]
    assert expected[4] == activation(traffic_event)
    assert [(row["text"], row["text_zh"]) for row in expected[-2:]] == [
        ("If permitted: Accident detected", "若条件允许：发现交通事故"),
        ("If permitted: Accident report broadcast to nearby responders", "若条件允许：事故报告已广播给附近的响应机"),
    ]
    diagram = interactions(summary, catalogue=catalogue)
    assert [lane["id"] for lane in diagram["lanes"]] == ["system", "couriers", "traffic-uavs", "depot", "customer", "incident.01"]
    assert [step["id"] for step in diagram["steps"]] == [f"{workload_id}:appears", f"{workload_id}:allocation", f"{workload_id}:pickup", f"{workload_id}:delivery",
                                                       "event:traffic-incident:incident.01", "event:scheduled-1", "event:wind-gust-40000000000",
                                                       "event:wind-gust-40000000000:calm", "traffic:traffic.incident.detected", "traffic:traffic.broadcast"]
    assert diagram["steps"][4]["text_zh"] == "交通事故"
    assert diagram["steps"][4]["decided_by"] is None


# Finding 4: ordering is business logic, not a side effect of I/O staging.
@pytest.mark.parametrize("duration_op", ["set_duration", "lowalt.set_duration", "traffic.set_duration"])
def test_pure_and_host_compilation_use_the_same_final_poisson_horizon(host, tmp_path, monkeypatch, duration_op):
    base = {"template": "food-delivery"}
    scene = resolve_base(host, base)
    operations = [{"id": "orders", "op": "add_orders", "params": {"id": "extra", "kind": "parcel", "count": 1,
                   "from": ["restaurant-west"], "to": ["customer-1"], "release": {"mode": "poisson", "rate_per_s": 1}}},
                  {"id": "duration", "op": duration_op, "params": {"seconds": 500}}]
    before = copy.deepcopy(operations)
    pure, changed = pure_compile(monkeypatch, scene.document, operations)
    hosted = compile_plan(host, base, operations, tmp_path / "plan", resolved=scene)
    assert hosted.ok, hosted.errors
    assert not pure.resources and not [issue for issue in pure.issues if issue["severity"] == "error"]
    def workload(document):
        return next(group for group in document["engines"]["tasks"]["config"]["generators"] if group.get("id") == "extra")
    assert workload(changed) == workload(hosted.document)
    assert workload(changed)["arrivals"]["end_ns"] == 500 * SECOND
    assert operations == before and hosted.operations == before


@pytest.mark.parametrize("canonical", [False, True])
def test_pure_and_host_compilation_share_forward_creation_and_workload_op_references(host, tmp_path, monkeypatch, canonical):
    base = {"template": "food-delivery"}
    scene = resolve_base(host, base)
    operations = [{"id": "edit", "op": "set_orders", "params": {"id": "create-work", "priority": 8}},
                  {"id": "create-work", "op": "add_orders", "params": {"id": "late-work", "kind": "parcel", "count": 1,
                   "from": ["late-depot"], "to": ["customer-1"], "release": {"mode": "scheduled", "times_s": [0]}}},
                  {"id": "create-place", "op": "add_place", "params": {"id": "late-depot", "role": "depot", "at": [0, 0, 0]}}]
    if canonical:
        for operation in operations:
            operation["op"] = "lowalt." + operation["op"]
    pure, changed = pure_compile(monkeypatch, scene.document, operations)
    hosted = compile_plan(host, base, operations, tmp_path / "plan", resolved=scene)
    assert hosted.ok, hosted.errors
    assert [row["id"] for row in pure.effective_operations] == ["create-place", "create-work", "edit"]
    def workload(document):
        return next(group for group in document["engines"]["tasks"]["config"]["generators"] if group.get("id") == "late-work")
    assert workload(changed) == workload(hosted.document)
    assert workload(changed)["priority"] == 8
    assert next(entity for entity in changed["entities"] if entity["id"] == "late-depot")["facts"]["lowalt.site_position"] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("name", ["add_place", "lowalt.add_place"])
@pytest.mark.parametrize("legacy_tag", [False, True])
def test_installed_lowalt_replacement_gets_no_builtin_edges_in_either_compile_path(host, tmp_path, monkeypatch, name, legacy_tag):
    def annotate(context, params):
        return CompileResult(resources=(ResourceProposal(f"notes/{params['id']}.txt", params["at"].encode()),))

    replacement = AssistantOperationPlugin(name="lowalt", operations=(OperationSpec(
        id="lowalt.add_place", parameters=obj({"id": {"type": "string"}, "at": {"type": "string"}}, ("id", "at")),
        title="Annotation", title_zh="标注", description="Write an independent annotation", description_zh="写入独立标注",
        compile=annotate, display={"legacy_operation": "add_place"} if legacy_tag else {},
    ),))
    install(monkeypatch, replacement)
    base = {"template": "food-delivery"}
    scene = resolve_base(host, base)
    operations = [{"id": "first", "op": name, "params": {"id": "a", "at": "b"}},
                  {"id": "second", "op": name, "params": {"id": "b", "at": "a"}}]
    before = copy.deepcopy(operations)
    catalogue = OperationCatalogue()
    assert catalogue.provider(name) is replacement
    assert catalogue.provider("traffic.set_duration").dependencies is not None
    assert catalogue.dependencies(CompileContext(scene.document), operations) == {0: set(), 1: set()}

    pure, changed = pure_compile(monkeypatch, scene.document, operations)
    hosted = compile_plan(host, base, operations, tmp_path / "plan", resolved=scene)

    assert hosted.ok, hosted.errors
    assert not pure.issues and changed == scene.document
    assert pure.resources == (ResourceProposal("notes/a.txt", b"b"), ResourceProposal("notes/b.txt", b"a"))
    assert [row["id"] for row in pure.effective_operations] == ["first", "second"]
    assert (hosted.files_dir / "notes/a.txt").read_bytes() == b"b"
    assert (hosted.files_dir / "notes/b.txt").read_bytes() == b"a"
    assert operations == before and hosted.operations == before


def test_both_compile_paths_reject_the_same_creation_cycle(host, tmp_path, monkeypatch):
    base = {"template": "food-delivery"}
    scene = resolve_base(host, base)
    operations = [{"id": "a", "op": "add_place", "params": {"id": "a", "role": "depot", "at": "b"}},
                  {"id": "b", "op": "add_place", "params": {"id": "b", "role": "depot", "at": "a"}}]
    with pytest.raises(OperationError, match="Circular creation references") as raised:
        pure_compile(monkeypatch, scene.document, operations)
    hosted = compile_plan(host, base, operations, tmp_path / "plan", resolved=scene)
    assert not hosted.ok
    assert hosted.errors[0]["field"] == raised.value.field == "operations"
    assert hosted.errors[0]["message"] == "OperationError: " + str(raised.value)


# Finding 5: priorities must not be an accident of provider concatenation.
POSITION_FIELDS = ["lowalt.position", "lowalt.site_position", "lowalt.incident_position", "he.aircraft.position_enu_m",
                   "traffic.road.position_enu_m", "traffic.incident.position_enu_m", "aas.studio.position_enu_m"]


@pytest.mark.parametrize("first", range(len(POSITION_FIELDS)))
@pytest.mark.parametrize("reverse", [False, True])
def test_position_precedence_matches_cards_in_any_provider_order(first, reverse):
    providers = (lowalt_plugin(), traffic_plugin())
    catalogue = OperationCatalogue(tuple(reversed(providers)) if reverse else providers)
    assert position_fields(catalogue=catalogue) == POSITION_FIELDS
    facts = {field: [index, 2, 3] for index, field in enumerate(POSITION_FIELDS) if index >= first}
    entity = {"id": "uav.01", "type": "aas:TrafficUAV", "facts": facts}
    document = {"id": "memory", "run": {"until_ns": SECOND}, "engines": {}, "entities": [entity]}
    summary = summarize(document, catalogue=catalogue)
    card_position = next(fleet for fleet in summary["fleets"] if fleet["id"] == "traffic-uavs")["member_states"][0]["position"]
    assert _position(entity, catalogue=catalogue) == card_position == facts[POSITION_FIELDS[first]]


@pytest.mark.parametrize("field", POSITION_FIELDS[3:6])
def test_find_places_near_a_participant_prefers_real_position_over_studio(tmp_path, monkeypatch, field):
    entity = {"id": "participant-1", "type": "example:Participant", "facts": {field: [10, 20, 30], "aas.studio.position_enu_m": [1000, 2000, 3000]}}
    document = {"id": "memory", "run": {"until_ns": SECOND}, "engines": {}, "entities": [entity]}
    summary = summarize(document)
    sessions = SimpleNamespace(get=lambda identifier: {"plan_version": None, "workspace_id": "fixture"})
    tools = ToolRunner(None, None, sessions, tmp_path)
    monkeypatch.setattr(tools, "_scene", lambda *args: (document, summary))
    import aeroagentsim.assistant.places as places
    monkeypatch.setattr(places, "find_places", lambda **kwargs: kwargs)
    assert tools.find_places("session", near="participant-1")["near"] == [10, 20, 30]


# Finding 6: the intentional diagnostic correction, not the old TypeError.
@pytest.mark.parametrize("name", ["set_region", "lowalt.set_region"])
def test_region_form_rejection_pins_the_corrected_bilingual_operation_error(host, tmp_path, name):
    assert not host.allow_region
    operation = {"id": "region", "op": name, "params": {"extract": "fixture", "bounds": [121.51, 31.301, 121.52, 31.305]}}
    result = compile_plan(host, {"template": "food-delivery"}, [operation], tmp_path / "plan")
    assert not result.ok
    assert result.errors == [{"op_id": "region", "field": "op", "message": "set_region is an assistant proposal operation. Use the Region map's /region preview/apply endpoint for form edits / set_region 用于助手方案；表单请使用区域地图的 /region 预览和应用接口"}]
