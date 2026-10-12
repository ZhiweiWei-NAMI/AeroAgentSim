from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aeroagentsim.assistant.compiler import CompileContext, PlanConflict, apply_plan, compile_plan, validate_compiled
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario import load_scenario


@pytest.fixture
def context(tmp_path: Path) -> CompileContext:
    return CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)


def op(name: str, **params) -> dict:
    return {"op": name, "params": params}


def compile_ops(context: CompileContext, tmp_path: Path, operations: list[dict], template="food-delivery"):
    return compile_plan(context, {"template": template}, [{"id": f"op-{i + 1}", **operation} for i, operation in enumerate(operations)], tmp_path / "plan")


@pytest.mark.parametrize("template", ["traffic-accident", "parcel-delivery", "medical-delivery", "powerline-inspection", "crop-spraying", "air-taxi", "security-patrol", "city-day", "food-delivery"])
def test_every_template_round_trips(context: CompileContext, tmp_path: Path, template: str) -> None:
    result = compile_ops(context, tmp_path, [], template)
    assert result.ok, result.errors
    loaded = load_scenario(result.document, base=result.files_dir)
    assert loaded.document["id"] == template
    validation = validate_compiled(context, tmp_path / "plan")
    assert validation["valid"], validation
    assert result.digest.startswith("sha256:")
    assert result.diff_base == {"added": [], "changed": [], "removed": [], "entities": {"added": [], "changed": [], "removed": []}}


def test_explicit_fleets_preserve_roles_and_single_vehicle_state(context: CompileContext, tmp_path: Path) -> None:
    result = compile_ops(context, tmp_path, [op("add_fleet", id="logistics", role="parcel_drone", count=2, start="plaza"),
                                          op("set_vehicle", vehicle="logistics-2", battery_pct=22)])
    assert result.ok, result.errors
    loaded = load_scenario(result.document, base=result.files_dir)
    tasks = loaded.document["engines"]["tasks"]["config"]
    assert tasks["vehicle_task_kinds"]["logistics-1"] == ["parcel"]
    assert tasks["vehicle_task_kinds"]["drone-1"] == ["food"]
    vehicle = next(e for e in loaded.document["entities"] if e["id"] == "logistics-2")
    assert vehicle["facts"]["lowalt.soc"] == 0.22
    assert vehicle["facts"]["lowalt.motion_energy_j"] == 0.22 * vehicle["facts"]["lowalt.capacity_j"]


def test_order_operations_use_real_generators(context: CompileContext, tmp_path: Path) -> None:
    result = compile_ops(context, tmp_path, [op("add_place", id="depot", role="vertiport", at="plaza"),
        op("add_orders", id="parcels", kind="parcel", count=2, **{"from": ["depot"], "to": ["customer-1"]}, release={"mode": "scheduled", "times_s": [0, 30]}),
        op("set_task", task="parcels-2", destination="customer-2", release_s=40, priority=8)])
    assert result.ok, result.errors
    tasks = result.document["engines"]["tasks"]["config"]["generators"]
    second = next(g for g in tasks if g["tasks"] == ["parcels-2"])
    assert second["destinations"] == ["customer-2"]
    assert second["arrivals"]["times_ns"] == [40_000_000_000]
    assert second["priority"] == 8
    assert len({w["id"] for w in result.summary["workloads"]}) == len(result.summary["workloads"])
    assert validate_compiled(context, tmp_path / "plan")["valid"]


@pytest.mark.parametrize("operation,field", [
    (op("set_duration", seconds=True), "params.seconds"),
    (op("set_control", control="nonexistent", value=2), "control"),
    (op("set_control", control="fleet_size", value=0), "value"),
    (op("set_rule", rule="battery_reserve_pct", value=101), "value"),
    (op("set_rule", rule="medical_preemption", value=1), "value"),
    (op("set_algorithm", slot="allocation", value="invented"), "value"),
    (op("add_place", id="weather", role="kitchen", at="plaza"), "id"),
    (op("add_place", id="new-place", role="kitchen", at="missing"), "at"),
    (op("add_gust", start_s=20, end_s=10, speed_mps=2), "end_s"),
    (op("set_fleet", fleet="missing", count=2), "fleet"),
    (op("set_vehicle", vehicle="drone-1", battery_pct=10), "vehicle"),
    (op("remove_place", id="restaurant-west"), "id"),
    (op("remove_orders", id="missing"), "id"),
    (op("add_orders", id="survey", kind="inspection", count=1, **{"from": ["restaurant-west"], "to": ["customer-1"]}, release={"mode": "scheduled", "times_s": [0]}), "kind"),
    (op("schedule_event", event="invented", at_s=0, params={}), "event"),
])
def test_errors_locate_operation(context: CompileContext, tmp_path: Path, operation: dict, field: str) -> None:
    operation["id"] = "op-42"
    result = compile_plan(context, {"template": "food-delivery"}, [operation], tmp_path / "plan")
    assert not result.ok
    assert result.errors[0]["op_id"] == "op-42"
    assert result.errors[0]["field"] == field


def test_scheduled_operator_adds_real_injection(context: CompileContext, tmp_path: Path) -> None:
    result = compile_ops(context, tmp_path, [op("schedule_event", event="restaurant_delay", at_s=40, params={"restaurant": "restaurant-west", "delay_s": 10})])
    assert result.ok, result.errors
    command = result.document["bindings"]["commands"][-1]
    assert command["at_ns"] == 40_000_000_000
    assert command["payload"]["injection_point"] == "restaurant_delay"
    assert any(e["match"]["schema"] == "lowalt.operator.restaurant_delay" for e in result.expectations)


def test_compile_isolated_files_and_revision_conflict(context: CompileContext, tmp_path: Path) -> None:
    draft = context.store.import_template("food-delivery", {}, registry=context.templates)
    directory = context.store.directory(draft["id"])
    before = (directory / "draft.json").read_bytes()
    base = {"workspace": draft["id"]}
    result = compile_plan(context, base, [{"id": "op-1", **op("set_duration", seconds=180)}], tmp_path / "plan")
    assert result.ok, result.errors
    assert not (result.files_dir / "draft.json").exists()
    assert (directory / "draft.json").read_bytes() == before
    context.store.save(draft["id"], {"name": "Changed while discussing"})
    with pytest.raises(PlanConflict, match="draft changed"):
        apply_plan(context, tmp_path / "plan", base=base, workspace_id=draft["id"], name="Confirmed", note="Plan")


@pytest.mark.parametrize("workspace", [False, True])
def test_apply_validated_draft_and_version(context: CompileContext, tmp_path: Path, workspace: bool) -> None:
    if workspace:
        draft = context.store.import_template("food-delivery", {}, registry=context.templates)
        base = {"workspace": draft["id"]}
        workspace_id = draft["id"]
    else:
        base, workspace_id = {"template": "food-delivery"}, None
    result = compile_plan(context, base, [{"id": "op-1", **op("set_control", control="fleet_size", value=3)}], tmp_path / "plan")
    assert result.ok, result.errors
    assert validate_compiled(context, tmp_path / "plan")["valid"]
    report = apply_plan(context, tmp_path / "plan", base=base, workspace_id=workspace_id, name="Confirmed", note="Agreed plan")
    assert report["validation"]["valid"]
    assert report["version"] == 1
    draft = context.store.get(report["workspace_id"])
    assert draft["name"] == "Confirmed"
    assert draft["template"]["knobs"]["fleet_size"] == 3
    assert len(context.store.versions(report["workspace_id"])) == 1
    assert not list(context.store.directory(report["workspace_id"]).glob("journal*"))


def test_changed_plan_files_cannot_apply(context: CompileContext, tmp_path: Path) -> None:
    result = compile_ops(context, tmp_path, [])
    assert result.ok
    (result.files_dir / "unexpected.txt").write_text("changed")
    with pytest.raises(PlanConflict, match="files changed"):
        apply_plan(context, tmp_path / "plan", base={"template": "food-delivery"}, workspace_id=None, name="Scene", note="Plan")


COOP_INSTALLED = importlib.util.find_spec("aeroagentsim.packs.lowalt.cooperation") is not None


@pytest.mark.skipif(not COOP_INSTALLED, reason="Cooperation module is supplied by the parallel capability area; integrate it before this test")
@pytest.mark.parametrize("template", ["food-delivery", "city-day"])
def test_flagship_food_parcel_incident_handoff(context: CompileContext, tmp_path: Path, template: str) -> None:
    operations = [op("add_place", id="plan-kitchen", role="kitchen", at="plaza"),
        op("add_place", id="plan-customer", role="customer", at="clinic"),
        op("add_fleet", id="couriers", role="meal_courier", count=2, start="plaza"),
        op("add_fleet", id="logistics", role="parcel_drone", count=2, start="plaza"),
        op("add_orders", id="lunch", kind="food", count=2, **{"from": ["plan-kitchen"], "to": ["plan-customer"]}, release={"mode": "scheduled", "times_s": [0, 30]}),
        op("add_orders", id="packages", kind="parcel", count=2, **{"from": ["plan-kitchen"], "to": ["plan-customer"]}, release={"mode": "scheduled", "times_s": [0, 30]}),
        op("add_place", id="handoff-east", role="handoff_point", at="east"),
        op("add_incident", id="person-fall", kind="person_fall", at="street-east", time_s=90),
        op("set_cooperation", watchers={"vehicles": ["logistics"], "notice_radius_m": 150},
           responder={"mode": "prescribed", "order": ["request_handoff", "respond_now", "finish_then_respond", "report_only"]},
           helpers={"vehicles": ["couriers"], "mode": "prescribed", "rules": {"accept_when": "idle", "min_battery_pct": 25}})]
    result = compile_ops(context, tmp_path, operations, template)
    assert result.ok, result.errors
    assert validate_compiled(context, tmp_path / "plan")["valid"]
    coop = result.summary["cooperation"]
    assert coop["watchers"]["vehicles"] == ["logistics-1", "logistics-2"]
    assert coop["helpers"]["vehicles"] == ["couriers-1", "couriers-2"]
    assert any(e["type"] == "lowalt:Incident" for e in result.document["entities"])
    assert any(e["type"] == "lowalt:HandoffPoint" for e in result.document["entities"])
    assert any(e["match"]["schema"] == "lowalt.handoff.transferred" for e in result.expectations)
    assert any(s["branch"] is not None for s in result.interactions["steps"])


@pytest.mark.skipif(not COOP_INSTALLED, reason="Cooperation module is built in the parallel area")
def test_agent_cooperation_requires_connection(context: CompileContext, tmp_path: Path) -> None:
    result = compile_ops(context, tmp_path, [op("set_cooperation", responder={"mode": "agent"})])
    assert not result.ok
    assert result.errors[0]["op_id"] == "op-1"
    assert "Agent decisions need a connected model" in result.errors[0]["message"]
