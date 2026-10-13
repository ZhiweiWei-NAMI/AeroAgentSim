"""Blank projects compile real lowalt components and preserve authored state."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aeroagentsim.assistant.compiler import CompileContext, apply_plan, compile_plan, validate_compiled
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner
from aeroagentsim.authoring.inputs import configured_ontology
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.packs.lowalt.template import DEFAULT_ALGORITHMS, components_document
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario

SOURCE = Path(__file__).parents[1] / "authoring/fixtures/wujiaochang.osm.xml"


def op(name: str, operation_id: str = "op-1", **params) -> dict:
    return {"id": operation_id, "op": name, "params": params}


@pytest.mark.parametrize("source_registry", ["snapshot", "aerograph"])
def test_blank_centre_charging_station_compiles_validates_and_applies(tmp_path: Path, source_registry: str) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", configured_ontology() if source_registry == "aerograph" else None)
    draft = store.create("一个无人机充电站，放在中间")
    draft = store.region(draft["id"], {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 20.0, "level_height_m": 3.5}, SOURCE)
    context = CompileContext(store, TemplateRegistry(), None)
    base = {"workspace": draft["id"], "revision": draft["revision"]}
    result = compile_plan(context, base, [op("add_place", id="charging-centre", role="charging_pad", at="middle", power_w=6000)], tmp_path / "plan")
    assert result.ok, result.errors
    assert result.operations[0]["op"] == "add_lowalt_components"
    assert result.notes and "add_lowalt_components" in result.notes[0]
    assert any(row.get("op") == "add_lowalt_components" for row in result.diff_base["added"])
    assert result.document["origin"] == draft["scenario"]["origin"]
    assert result.summary["fleets"] == result.summary["workloads"] == []
    station = next(e for e in result.document["entities"] if e["id"] == "charging-centre")
    assert station["facts"]["lowalt.site_position"] == [0.0, 0.0, 0.0]
    assert station["facts"]["lowalt.pad_power_w"] == 6000.0
    assert {e["type"] for e in result.document["entities"]} == {"lowalt:Environment", "lowalt:ChargingPad"}
    assert store.get(draft["id"]) == draft
    assert validate_compiled(context, tmp_path / "plan")["valid"]
    report = apply_plan(context, tmp_path / "plan", base=base, workspace_id=draft["id"], name=draft["name"], note="Centre station")
    saved = store.get(draft["id"])
    assert report["version"] == 1
    assert saved["scene"] == draft["scene"] and saved["region"] == draft["region"]
    assert (store.directory(draft["id"]) / "region.osm.xml").is_file()
    loaded = load_scenario(saved["scenario"], base=store.directory(draft["id"]))
    simulation = Simulation(loaded)
    try:
        simulation.start()
        simulation.run_until(2_000_000_000)
    finally:
        simulation.close()


def test_explicit_base_has_only_wind_environment_and_is_idempotent(tmp_path: Path) -> None:
    base_document = components_document()
    assert [e["type"] for e in base_document["entities"]] == ["lowalt:Environment"]
    assert "generators" not in base_document
    assert base_document["engines"]["tasks"]["config"]["generators"] == []
    assert base_document["algorithms"] == DEFAULT_ALGORITHMS
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Components only")
    context = CompileContext(store, TemplateRegistry(), None)
    result = compile_plan(context, {"workspace": draft["id"]}, [op("add_lowalt_components"), op("add_lowalt_components", "op-2")], tmp_path / "plan")
    assert result.ok, result.errors
    assert result.notes == []
    assert len(result.document["entities"]) == 1
    assert len(result.document["engines"]) == 6
    assert validate_compiled(context, tmp_path / "plan")["valid"]


def test_existing_generic_motion_engine_and_entities_survive_installation(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Existing authoring")
    draft = store.place(draft["id"], {"id": "weather", "type": "oo:UAV", "kind": "entity", "position": [5, 10, 15], "engine": "kinematic"})
    authored = copy.deepcopy(draft["scenario"])
    result = compile_plan(CompileContext(store, TemplateRegistry(), None), {"workspace": draft["id"]}, [op("add_place", id="pad", role="charging_pad", at="center")], tmp_path / "plan")
    assert result.ok, result.errors
    assert result.document["engines"]["motion"] == authored["engines"]["motion"]
    assert next(e for e in result.document["entities"] if e["id"] == "weather") == authored["entities"][0]
    tasks = next(e["config"] for e in result.document["engines"].values() if e["plugin"] == "lowalt_tasks")
    assert tasks["motion"]["target"] != "motion"
    assert result.document["engines"][tasks["motion"]["target"]]["config"]["type_id"] == "lowalt:UAV"
    assert len([e for e in result.document["entities"] if e["type"] == "lowalt:Environment"]) == 1
    assert validate_compiled(CompileContext(store, TemplateRegistry(), None), tmp_path / "plan")["valid"]


def test_blank_assistant_board_shows_inserted_components_and_notes(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Blank assistant")
    sessions = SessionStore(tmp_path / "runs")
    session = sessions.create(draft["id"], locale="zh")
    tools = ToolRunner(store, TemplateRegistry(), sessions, tmp_path / "runs")
    response = tools.draft_plan(session["id"], base={"workspace": draft["id"]}, title="中心充电站", summary="添加一个充电站", operations=[op("add_place", id="pad", role="charging_pad", at="中间")])
    assert response["ok"], response
    plan = sessions.plan(session["id"])
    assert plan["compile"]["operations"][0]["op"] == "add_lowalt_components"
    assert plan["compile"]["notes"] == response["notes"]
    assert any(row["id"] == "lowalt_components" for row in plan["diff_base"]["added"])
    assert store.get(draft["id"]) == draft
    persisted = json.loads((sessions.plan_directory(session["id"], plan["version"]) / "compile.json").read_text())
    assert persisted["notes"] == response["notes"]


@pytest.mark.parametrize("name,params", [
    ("set_algorithm", {"slot": "allocation", "value": "nearest"}),
    ("set_wind", {"speed_mps": 4}),
    ("add_gust", {"start_s": 0.5, "end_s": 1.5, "speed_mps": 9}),
    ("set_rule", {"rule": "battery_reserve_pct", "value": 35}),
    ("add_incident", {"id": "fall", "kind": "person_fall", "at": "center", "time_s": 1}),
])
def test_blank_nonplace_operations_require_real_components(tmp_path: Path, name: str, params: dict) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Blank operation")
    context = CompileContext(store, TemplateRegistry(), None)
    result = compile_plan(context, {"workspace": draft["id"]}, [op(name, **params)], tmp_path / "plan")
    assert result.ok, result.errors
    assert result.operations[0]["op"] == "add_lowalt_components"
    assert validate_compiled(context, tmp_path / "plan")["valid"]
    if name == "set_rule":
        assert result.document["engines"]["tasks"]["config"]["dispatch_min_soc"] == 0.35


def test_empty_plan_keeps_blank_project_blank(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Blank")
    result = compile_plan(CompileContext(store, TemplateRegistry(), None), {"workspace": draft["id"]}, [], tmp_path / "plan")
    assert result.ok, result.errors
    assert result.document["engines"] == {} and result.document["entities"] == []
    assert result.notes == result.operations == []

