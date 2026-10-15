"""Incident fire/radius/end and visual weather operations compile to journaled state."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aeroagentsim.assistant.compiler import CompileContext, apply_plan, compile_plan, validate_compiled
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.assistant.operations import validate_operation


@pytest.fixture
def context(tmp_path: Path) -> CompileContext:
    return CompileContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)


def op(name: str, **params) -> dict:
    return {"id": f"op-{name}-{params.get('id', 'weather')}", "op": name, "params": params}


def compile_ops(context: CompileContext, tmp_path: Path, operations: list[dict], template="food-delivery"):
    return compile_plan(context, {"template": template}, [{"id": f"op-{i + 1}", **operation} for i, operation in enumerate(operations)], tmp_path / "plan")


def test_incident_op_schemas_accept_fire_radius_and_end() -> None:
    assert validate_operation(op("add_incident", id="fire", kind="fire", at="east", time_s=10, radius_m=50, end_s=100))[0] == "add_incident"
    assert validate_operation(op("set_wind", speed_mps=5, **{"from_deg": 270}))[0] == "set_wind"
    assert validate_operation(op("set_weather", precipitation_mm_h=0, visibility_m=None, light="day"))[0] == "set_weather"
    with pytest.raises(Exception):
        validate_operation(op("add_incident", id="x", kind="smoke_plume", at="east", time_s=1))
    with pytest.raises(Exception):
        validate_operation(op("add_incident", id="x", kind="fire", at="east", time_s=1, radius_m=0))
    with pytest.raises(Exception):
        validate_operation(op("set_weather", precipitation_mm_h=101))
    with pytest.raises(Exception):
        validate_operation(op("set_weather", visibility_m=49))


def test_fire_incident_defaults_radius_and_labels(context: CompileContext, tmp_path: Path) -> None:
    result = compile_plan(context, {"template": "food-delivery"}, [
        op("add_incident", id="fire", kind="fire", at="street-east", time_s=30),
        op("add_incident", id="fall", kind="person_fall", at="plaza", time_s=10),
    ], tmp_path / "plan")
    assert result.ok, result.errors
    coop = next(e["config"]["cooperation"] for e in result.document["engines"].values() if e["plugin"] == "lowalt_tasks")
    by_id = {i["id"]: i for i in coop["incidents"]}
    assert by_id["fire"]["radius_m"] == 30 and by_id["fall"]["radius_m"] == 6
    assert by_id["fire"]["label"] == "Building fire" and by_id["fire"]["label_zh"] == "建筑火情"
    assert "end_s" not in by_id["fire"]
    incident = next(e for e in result.document["entities"] if e["id"] == "fire")
    facts = incident["facts"]
    assert facts["lowalt.incident_radius_m"] == 30.0
    assert facts["lowalt.incident_end_ns"] == -1
    assert facts["lowalt.incident_active"] is False
    assert validate_compiled(context, tmp_path / "plan")["valid"]


def test_incident_end_must_follow_start_inside_run(context: CompileContext, tmp_path: Path) -> None:
    early = compile_plan(context, {"template": "food-delivery"}, [
        op("add_incident", id="fire", kind="fire", at="east", time_s=300, end_s=100)], tmp_path / "p1")
    assert not early.ok and early.errors[0]["field"] == "end_s"
    beyond = compile_plan(context, {"template": "food-delivery"}, [
        op("add_incident", id="fire", kind="fire", at="east", time_s=10, end_s=400)], tmp_path / "p2")
    assert not beyond.ok and beyond.errors[0]["field"] == "end_s"


def test_wind_from_deg_converts_to_enu_vector_and_gusts_inherit(context: CompileContext, tmp_path: Path) -> None:
    result = compile_plan(context, {"template": "food-delivery"}, [
        op("set_wind", speed_mps=6, **{"from_deg": 315}),
        op("add_gust", start_s=60, end_s=90, speed_mps=9),
    ], tmp_path / "plan")
    assert result.ok, result.errors
    profile = next(e["config"]["profiles"]["weather"]["lowalt.wind"] for e in result.document["engines"].values() if e["plugin"] == "environment")
    # From 315° (northwest), speed 6: toward southeast.
    east, north = profile["value"][:2]
    assert east == pytest.approx(-6 * -2 ** 0.5 / 2)
    assert north == pytest.approx(-6 * 2 ** 0.5 / 2)
    gust = profile["gusts"][0]["value"]
    assert gust[:2] == pytest.approx([east * 1.5, north * 1.5])
    assert validate_compiled(context, tmp_path / "plan")["valid"]


def test_set_weather_journals_constant_profiles_and_light(context: CompileContext, tmp_path: Path) -> None:
    result = compile_plan(context, {"template": "food-delivery"}, [
        op("set_weather", precipitation_mm_h=4, visibility_m=500, light="dusk")], tmp_path / "plan")
    assert result.ok, result.errors
    environment = next(e["config"] for e in result.document["engines"].values() if e["plugin"] == "environment")
    assert set(environment["produces"]) >= {"lowalt.wind", "lowalt.precipitation", "lowalt.visibility", "lowalt.light"}
    profile = environment["profiles"]["weather"]
    assert profile["lowalt.precipitation"] == {"mode": "constant", "value": pytest.approx(4 / 3600)}
    assert profile["lowalt.visibility"] == {"mode": "constant", "value": 500.0}
    assert profile["lowalt.light"] == {"mode": "constant", "value": "dusk"}
    weather = next(e for e in result.document["entities"] if e["type"] == "lowalt:Environment")
    assert weather["facts"]["lowalt.light"] == "dusk"
    assert any(r["type"] == "lowalt:Environment" and "lowalt.light" in r["fields"] for r in result.document["bindings"]["rules"])
    changed = next(row for row in result.diff_base["changed"] if row["id"] == "environment")
    fields = {row["name"].rsplit(".", 1)[-1]: row for row in changed["fields"]}
    assert fields["precipitation_mm_h"]["after"] == 4
    assert fields["precipitation_mm_h"]["label"] == "Rain (mm/h)"
    assert fields["visibility_m"]["after"] == 500
    assert fields["light"]["after"] == "dusk"
    assert validate_compiled(context, tmp_path / "plan")["valid"]


def test_set_weather_null_visibility_clears_and_absent_is_clear(context: CompileContext, tmp_path: Path) -> None:
    result = compile_plan(context, {"template": "food-delivery"}, [
        op("set_weather", visibility_m=None)], tmp_path / "plan")
    assert result.ok, result.errors
    profile = next(e["config"]["profiles"]["weather"] for e in result.document["engines"].values() if e["plugin"] == "environment")
    assert profile["lowalt.visibility"]["value"] == 50000.0
    assert "lowalt.precipitation" not in profile
    assert "lowalt.light" not in profile


def test_set_weather_is_idempotent(context: CompileContext, tmp_path: Path) -> None:
    ops = [op("set_weather", precipitation_mm_h=4, light="dusk"), {**op("set_weather", precipitation_mm_h=4, light="dusk"), "id": "weather-again"}]
    result = compile_plan(context, {"template": "food-delivery"}, ops, tmp_path / "plan")
    assert result.ok, result.errors
    profile = next(e["config"]["profiles"]["weather"] for e in result.document["engines"].values() if e["plugin"] == "environment")
    assert profile["lowalt.precipitation"] == {"mode": "constant", "value": 4 / 3600}
    assert profile["lowalt.light"] == {"mode": "constant", "value": "dusk"}
    engine = next(e["config"] for e in result.document["engines"].values() if e["plugin"] == "environment")
    assert engine["produces"].count("lowalt.precipitation") == 1
    assert validate_compiled(context, tmp_path / "plan")["valid"]


def test_partial_weather_and_speed_edits_preserve_authored_conditions(context: CompileContext, tmp_path: Path) -> None:
    operations = [
        op("set_weather", precipitation_mm_h=4, visibility_m=800, light="dusk"),
        {**op("set_weather", light="day"), "id": "light-only"},
        op("set_wind", speed_mps=6, from_deg=315),
        {**op("set_wind", speed_mps=3), "id": "speed-only"},
    ]
    result = compile_plan(context, {"template": "food-delivery"}, operations, tmp_path / "plan")
    assert result.ok, result.errors
    profile = next(e["config"]["profiles"]["weather"] for e in result.document["engines"].values() if e["plugin"] == "environment")
    assert profile["lowalt.precipitation"]["value"] == pytest.approx(4 / 3600)
    assert profile["lowalt.visibility"]["value"] == 800
    assert profile["lowalt.light"]["value"] == "day"
    assert profile["lowalt.wind"]["value"] == pytest.approx([3 / 2**0.5, -3 / 2**0.5, 0])


def test_set_weather_without_weather_entity_fails(context: CompileContext, tmp_path: Path) -> None:
    draft = context.store.create("No weather")
    result = compile_plan(context, {"workspace": draft["id"], "revision": draft["revision"]}, [
        op("set_weather", light="day")], tmp_path / "plan")
    assert not result.ok
    assert "Starting scene has no lowalt weather entity" in result.errors[0]["message"]


COOP_INSTALLED = importlib.util.find_spec("aeroagentsim.packs.lowalt.cooperation") is not None


def _cooperation_ops(**extra) -> list[dict]:
    return [
        op("add_fleet", id="couriers", role="meal_courier", count=1, start="plaza"),
        op("add_fleet", id="logistics", role="parcel_drone", count=1, start="plaza"),
        op("add_incident", id="fall", kind="person_fall", at="street-east", time_s=60, **extra),
        op("set_cooperation", watchers={"vehicles": ["logistics"], "notice_radius_m": 150},
           responder={"mode": "prescribed", "order": ["respond_now", "report_only"]},
           helpers={"vehicles": ["couriers"], "mode": "prescribed", "rules": {"accept_when": "idle", "min_battery_pct": 25}}),
    ]


@pytest.mark.skipif(not COOP_INSTALLED, reason="Cooperation module is built in the parallel area")
def test_set_incident_radius_and_end_regenerate_generated_rows(context: CompileContext, tmp_path: Path) -> None:
    from aeroagentsim.assistant.scene import diff

    before = compile_ops(context, tmp_path, _cooperation_ops(radius_m=30, end_s=120))
    assert before.ok, before.errors
    after = compile_plan(context, {"template": "food-delivery"}, [
        {"id": f"op-{i + 1}", **operation} for i, operation in enumerate([
            *_cooperation_ops(radius_m=30, end_s=120),
            op("set_incident", id="fall", radius_m=80, end_s=150),
        ])], tmp_path / "plan2")
    assert after.ok, after.errors
    incident = next(e for e in after.document["entities"] if e["id"] == "fall")
    facts = incident["facts"]
    assert facts["lowalt.incident_radius_m"] == 80.0
    assert facts["lowalt.incident_end_ns"] == 150 * 10**9
    assert facts["lowalt.incident_active"] is False
    assert validate_compiled(context, tmp_path / "plan2")["valid"]
    events = {e["id"]: e for e in after.summary["events"]}
    assert events["fall"]["radius_m"] == 80 and events["fall"]["end_s"] == 150
    changed = next(row for row in diff(before.summary, after.summary)["changed"] if row["id"] == "fall")
    fields = {f["name"]: f for f in changed["fields"]}
    assert fields["radius_m"]["label"] == "Affected radius (m)"
    assert fields["radius_m"]["label_zh"] == "影响半径（米）"
    assert fields["radius_m"]["before"] == 30 and fields["radius_m"]["after"] == 80
    assert fields["end_s"]["label"] == "Ends at (s)"
    assert fields["end_s"]["label_zh"] == "结束时间（秒）"
    assert fields["end_s"]["after"] == 150


@pytest.mark.skipif(not COOP_INSTALLED, reason="Cooperation module is built in the parallel area")
def test_incident_kind_change_renders_kind_labels(context: CompileContext, tmp_path: Path) -> None:
    from aeroagentsim.assistant.scene import diff

    before = compile_ops(context, tmp_path, _cooperation_ops())
    assert before.ok, before.errors
    after = compile_plan(context, {"template": "food-delivery"}, [
        {"id": f"op-{i + 1}", **operation} for i, operation in enumerate([
            *_cooperation_ops(),
            op("set_incident", id="fall", kind="fire"),
        ])], tmp_path / "plan2")
    assert after.ok, after.errors
    changed = next(row for row in diff(before.summary, after.summary)["changed"] if row["id"] == "fall")
    field = next(f for f in changed["fields"] if f["name"] == "kind")
    assert field["label"] == "Kind" and field["label_zh"] == "事件类型"
    assert field["before_text"] == "Person fell" and field["before_text_zh"] == "行人跌倒"
    assert field["after_text"] == "Fire" and field["after_text_zh"] == "火情"


@pytest.mark.skipif(not COOP_INSTALLED, reason="Cooperation module is built in the parallel area")
def test_set_incident_null_end_clears_end_and_moving_start_past_end_fails(context: CompileContext, tmp_path: Path) -> None:
    result = compile_ops(context, tmp_path, [*_cooperation_ops(end_s=120), op("set_incident", id="fall", end_s=None)])
    assert result.ok, result.errors
    coop = next(e["config"]["cooperation"] for e in result.document["engines"].values() if e["plugin"] == "lowalt_tasks")
    assert "end_s" not in coop["incidents"][0]
    assert next(e for e in result.document["entities"] if e["id"] == "fall")["facts"]["lowalt.incident_end_ns"] == -1
    moved = compile_ops(context, tmp_path, [*_cooperation_ops(end_s=120), op("set_incident", id="fall", time_s=200)])
    assert not moved.ok and moved.errors[0]["field"] == "at_s"


@pytest.mark.skipif(not COOP_INSTALLED, reason="Cooperation module is built in the parallel area")
def test_incident_end_checks_cover_add_edit_and_duration(context: CompileContext, tmp_path: Path) -> None:
    partial = compile_ops(context, tmp_path, [*_cooperation_ops(end_s=120), op("set_incident", id="fall", end_s=400)])
    assert not partial.ok and partial.errors[0]["field"] == "end_s"
    boundary = compile_ops(context, tmp_path, [*_cooperation_ops(), op("set_incident", id="fall", end_s=360)])
    assert boundary.ok, boundary.errors
    beyond = compile_ops(context, tmp_path, [*_cooperation_ops(), op("set_incident", id="fall", end_s=360.000000001)])
    assert not beyond.ok and beyond.errors[0]["field"] == "end_s"
    # set_duration is ordered first, so shortening below an incident end fails
    # at that incident's operation with the run-horizon message.
    shortened = compile_ops(context, tmp_path, [*_cooperation_ops(end_s=120), op("set_duration", seconds=100)])
    assert not shortened.ok
    assert any(e["field"] == "end_s" and "run" in e["message"] and "运行" in e["message"] for e in shortened.errors)


@pytest.mark.parametrize("seconds,valid", [(120, True), (100, False)])
def test_duration_edit_checks_an_incident_already_saved_in_the_workspace(
    context: CompileContext, tmp_path: Path, seconds: int, valid: bool,
) -> None:
    operations = [op("add_incident", id="fire", kind="fire", at="east", time_s=20, end_s=120)]
    initial = compile_plan(context, {"template": "food-delivery"}, operations, tmp_path / "initial")
    assert initial.ok, initial.errors
    saved = apply_plan(context, tmp_path / "initial", base={"template": "food-delivery"},
                       workspace_id=None, name="Timed fire", note="Confirmed incident")
    draft = context.store.get(saved["workspace_id"])
    base = {"workspace": draft["id"], "revision": draft["revision"]}
    result = compile_plan(context, base, [op("set_duration", seconds=seconds)], tmp_path / "edited")
    assert result.ok is valid, result.errors
    if valid:
        assert validate_compiled(context, tmp_path / "edited")["valid"]
    else:
        assert result.errors[0]["field"] == "seconds"
        assert "Incident end is beyond" in result.errors[0]["message"]
    assert context.store.get(draft["id"])["revision"] == draft["revision"]


def test_light_schema_is_enum_and_authored_night_fails(context: CompileContext, tmp_path: Path) -> None:
    from aeroagentsim.packs.lowalt.overlay import overlay

    fields = {row["id"]: row for row in overlay()["fields"]}
    assert fields["lowalt.light"]["schema"] == {"type": "string", "enum": ["day", "dusk"]}
    with pytest.raises(Exception):
        validate_operation(op("set_weather", light="night"))
    from aeroagentsim.authoring.templates import TemplateRegistry
    from aeroagentsim.scenario import load_scenario

    prepared = TemplateRegistry().apply("food-delivery")
    environment = next(e for e in prepared.document["entities"] if e["type"] == "lowalt:Environment")
    environment["facts"]["lowalt.light"] = "night"
    from aeroagentsim.scenario import load_scenario

    with pytest.raises(Exception):
        load_scenario(prepared.document, base=prepared.base)
