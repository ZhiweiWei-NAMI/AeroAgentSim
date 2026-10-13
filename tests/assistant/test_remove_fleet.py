from __future__ import annotations

import copy
from pathlib import Path

import pytest

from aeroagentsim.assistant.capabilities import capabilities
from aeroagentsim.assistant.compiler import BaseScene, CompileContext, compile_plan
from aeroagentsim.assistant.operations import OperationError, operation_catalogue, validate_operation
from aeroagentsim.assistant.tools import workspace_operations
from aeroagentsim.authoring.removal import FleetRemovalPlan, references
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario import load_scenario


def op(name: str, **params) -> dict:
    return {"id": name, "op": name, "params": params}


def test_remove_fleet_has_one_required_selector_and_is_catalogued() -> None:
    spec = next(row for row in operation_catalogue("participants") if row["name"] == "remove_fleet")
    assert spec["parameters"]["required"] == ["fleet"]
    assert set(spec["parameters"]["properties"]) == {"fleet"}
    assert spec["parameters"]["additionalProperties"] is False
    assert validate_operation(spec["example"]) == ("remove_fleet", {"fleet": "logistics"})
    overview = capabilities(None, templates=TemplateRegistry(), algorithms={})
    assert "remove_fleet" in overview["operations"]["participants"]
    for params in ({}, {"id": "logistics"}, {"fleet": ""}, {"fleet": "logistics", "count": 0}):
        with pytest.raises(OperationError):
            validate_operation(op("remove_fleet", **params))
    added = {"id": "added", "op": "add_fleet", "params": {"id": "logistics", "role": "parcel_drone", "count": 2, "start": "center"}}
    assert workspace_operations([], [added]) == [{"id": "added", "op": "remove_fleet", "params": {"fleet": "logistics"}}]


@pytest.fixture
def scene(tmp_path: Path):
    ctx = CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    additions = [
        {"id": "logistics", "op": "add_fleet", "params": {"id": "logistics", "role": "parcel_drone", "count": 2, "start": "center"}},
        {"id": "other", "op": "add_fleet", "params": {"id": "logistics-extra", "role": "parcel_drone", "count": 1, "start": "center"}},
        op("set_cooperation", watchers={"vehicles": ["logistics", "logistics-extra"]}, helpers={"vehicles": ["logistics", "fleet"]}),
    ]
    seed = compile_plan(ctx, {"template": "parcel-delivery"}, additions, tmp_path / "seed")
    assert seed.ok, seed.errors
    document = copy.deepcopy(seed.document)
    task = next(entity for entity in document["entities"] if entity["type"] == "lowalt:Task")
    task["facts"]["lowalt.task_vehicle"] = "logistics-1"
    document["bindings"].setdefault("exact", []).extend([
        {"entity": "logistics-1", "field": "lowalt.position", "writer": "motion"},
        {"entity": "logistics-extra-1", "field": "lowalt.position", "writer": "motion"},
    ])
    document["bindings"]["rules"].append({"writer": "motion", "type": "lowalt:UAV", "fields": ["lowalt.position"], "ids": "logistics-[12]"})
    return ctx, BaseScene(document, seed.files_dir, "Removal", None, None, {}), task["id"]


def test_remove_fleet_compiles_dependency_diff_without_changing_other_content(scene, tmp_path: Path) -> None:
    ctx, base, task_id = scene
    original = copy.deepcopy(base.document)
    before_files = {path: path.read_bytes() for path in base.directory.rglob("*") if path.is_file()}
    result = compile_plan(ctx, {}, [op("remove_fleet", fleet="logistics")], tmp_path / "remove", resolved=base)
    assert result.ok, result.errors
    assert base.document == original
    assert {path: path.read_bytes() for path in base.directory.rglob("*") if path.is_file()} == before_files
    removed = {"logistics-1", "logistics-2"}
    assert result.diff_base["entities"]["removed"] == sorted(removed)
    assert not references(result.document, removed, map_keys=True)
    before = {entity["id"]: entity for entity in original["entities"]}
    after = {entity["id"]: entity for entity in result.document["entities"]}
    expected_task = copy.deepcopy(before[task_id])
    del expected_task["facts"]["lowalt.task_vehicle"]
    assert after[task_id] == expected_task
    assert all(after[identity] == entity for identity, entity in before.items() if identity not in removed | {task_id})
    assert result.document["generators"] == original["generators"]
    tasks = result.document["engines"]["tasks"]["config"]
    assert tasks["generators"] == original["engines"]["tasks"]["config"]["generators"]
    assert tasks["cooperation"]["watchers"]["vehicles"] == ["logistics-extra-1"]
    assert tasks["cooperation"]["helpers"]["vehicles"] == [identity for identity in original["engines"]["tasks"]["config"]["cooperation"]["helpers"]["vehicles"] if identity not in removed]
    assert result.document["presentation"] == original["presentation"]
    assert result.document["bindings"]["exact"] == [original["bindings"]["exact"][-1]]
    assert not any(row.get("ids") == "logistics-[12]" for row in result.document["bindings"]["rules"])
    assert {row["kind"] for row in result.diff_base["removed"]} >= {"fleet", "entity", "assignment", "binding", "engine"}
    paths = {row["path"] for row in result.diff_base["removed"] if "path" in row}
    assert "/engines/tasks/config/vehicle_task_kinds/logistics-1" in paths
    assert any(path.endswith("/facts/lowalt.task_vehicle") for path in paths)
    assert any(path.startswith("/bindings/exact/") for path in paths)


@pytest.mark.parametrize("selector", ["fleet", "drone"])
def test_generated_fleet_removal_does_not_resurrect_members_or_remove_explicit_fleets(scene, tmp_path: Path, selector: str) -> None:
    ctx, base, _ = scene
    original = copy.deepcopy(base.document)
    fleet = original["generators"]["fleet"]
    removed = {fleet["prefix"] + str(i + 1) for i in range(fleet["count"])}
    result = compile_plan(ctx, {}, [op("remove_fleet", fleet=selector)], tmp_path / "remove", resolved=base)
    assert result.ok, result.errors
    loaded = load_scenario(result.document, base=result.files_dir).document
    assert "generators" not in loaded
    assert not removed & {entity["id"] for entity in loaded["entities"]}
    assert not references(loaded, removed, map_keys=True)
    assert {entity["id"]: entity for entity in loaded["entities"]} == {entity["id"]: entity for entity in original["entities"] if entity["id"] not in removed}
    assert loaded["engines"]["tasks"]["config"]["generators"] == original["engines"]["tasks"]["config"]["generators"]
    assert any(row.get("path") == "/generators/fleet" for row in result.diff_base["removed"])


def test_missing_fleet_is_an_atomic_located_error(scene, tmp_path: Path) -> None:
    ctx, base, _ = scene
    result = compile_plan(ctx, {}, [op("remove_fleet", fleet="missing")], tmp_path / "remove", resolved=base)
    assert not result.ok
    assert result.errors[0]["op_id"] == "remove_fleet" and result.errors[0]["field"] == "fleet"
    assert result.diff_base == {}


def test_fleet_reference_plan_cleans_bindings_presentation_behaviours_and_shared_maps(tmp_path: Path) -> None:
    ref = {"$ref": {"id": "gone-1", "type_id": "lowalt:UAV"}}
    document = {
        "entities": [{"id": identity, "type": "lowalt:UAV", "facts": {}} for identity in ("gone-1", "gone-2", "keep-1")],
        "bindings": {"commands": [{"target": "tasks", "payload": {"actor": ref}}, {"target": "tasks", "payload": {"actor": "keep-1"}}],
                     "rules": [{"writer": "gone-1", "ids": "keep-*"}, {"writer": "tasks", "ids": "gone-*"}, {"writer": "tasks", "ids": "*"}]},
        "engines": {"tasks": {"plugin": "lowalt_tasks", "config": {"vehicle_task_kinds": {"gone-1": ["parcel"], "gone-2": ["parcel"], "keep-1": ["parcel"]}, "cooperation": {"watchers": {"vehicles": ["gone-1", "keep-1"]}}, "generators": [{"tasks": ["order-1"], "origins": ["place-1"], "destinations": ["place-2"]}]}}},
        "behaviours": [{"predicates": {"gone": {"entity": "gone-1"}, "keep": {"entity": "keep-1"}}, "chains": {"gone-chain": {"predicate": "gone"}}, "bindings": [{"chain": "gone-chain"}, {"match": {"actor": ref}}, {"predicate": "keep"}]}],
        "presentation": [{"entity": "gone-1"}, {"entity": "keep-1"}],
    }
    original = copy.deepcopy(document)
    cleaned, dependencies = FleetRemovalPlan(document, {"gone-1", "gone-2"}, tmp_path).build()
    assert document == original
    assert cleaned["presentation"] == [{"entity": "keep-1"}]
    assert cleaned["bindings"]["rules"] == [original["bindings"]["rules"][0], original["bindings"]["rules"][2]]
    assert cleaned["bindings"]["commands"] == [original["bindings"]["commands"][1]]
    assert cleaned["engines"]["tasks"]["config"]["generators"] == original["engines"]["tasks"]["config"]["generators"]
    assert cleaned["behaviours"][0]["chains"] == {}
    assert cleaned["behaviours"][0]["bindings"] == [{"predicate": "keep"}]
    assert {row["kind"] for row in dependencies} >= {"entity", "engine", "binding", "presentation", "behaviour"}
    assert sum(row["kind"] == "entity" for row in dependencies) == 2
