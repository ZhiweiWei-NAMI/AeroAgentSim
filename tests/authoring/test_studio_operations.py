"""C9--C13 REST contracts using the real deterministic scenario compiler."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.assistant.compiler import CompileContext, _rebase, compile_plan
from aeroagentsim.assistant.operations import operation_catalogue, validate_operation
from aeroagentsim.assistant.scene import diff
from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.scene import extract_bounds
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog

SOURCE = Path(__file__).parent / "fixtures/wujiaochang.osm.xml"


def op(operation_id: str, name: str, **params) -> dict:
    return {"id": operation_id, "op": name, "params": params}


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("selector", [{"index": 0}, {"start_s": 20}])
def test_remove_gust_reviews_exact_interval_and_preserves_remaining_gust(api, selector):
    store, _, client = api
    draft = store.create("Reviewed weather")
    path = f"/v1/studio/workspaces/{draft['id']}"
    response = client.post(path + "/operations", json={"operations": [
        op("components", "add_lowalt_components"),
        op("wind", "set_wind", speed_mps=2),
        op("first", "add_gust", start_s=20, end_s=25, speed_mps=9),
        op("second", "add_gust", start_s=40, end_s=45, speed_mps=12),
    ]})
    assert response.status_code == 200, response.text
    saved = store.get(draft["id"])
    request = {"operations": [op("remove", "remove_gust", **selector)], "revision": saved["revision"], "dry_run": True}
    before = files(store.directory(draft["id"]))
    review = client.post(path + "/operations", json=request)
    assert review.status_code == 200, review.text
    assert store.get(draft["id"]) == saved and files(store.directory(draft["id"])) == before
    removed = [row for row in review.json()["diff"]["removed"] if row["kind"] == "event"]
    assert len(removed) == 1 and removed[0]["label"] == "Wind gust: 9 m/s"
    assert removed[0]["label_zh"] == "阵风：风速 9 米/秒"
    assert not any(row["id"] == "wind-gust-40000000000" for group in ("added", "removed") for row in review.json()["diff"][group])
    request["dry_run"] = False
    applied = client.post(path + "/operations", json=request)
    assert applied.status_code == 200, applied.text
    profile = applied.json()["scenario"]["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]
    assert profile["value"] == [2, 0, 0] and profile["gusts"] == [{"start_ns": 40_000_000_000, "end_ns": 45_000_000_000, "value": [12, 0, 0]}]
    assert store.validate(draft["id"])["valid"]
    # The last removal restores a valid constant profile and keeps the baseline.
    last = client.post(path + "/operations", json={"operations": [op("last", "remove_gust", start_s=40)], "revision": applied.json()["revision"]})
    assert last.status_code == 200, last.text
    profile = last.json()["scenario"]["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]
    assert profile == {"mode": "constant", "value": [2, 0, 0]}
    assert store.validate(draft["id"])["valid"]


@pytest.mark.parametrize("selector", [{}, {"index": True}, {"index": -1}, {"start_s": None}, {"index": 0, "start_s": 20}, {"index": 99}, {"start_s": 99}])
def test_remove_gust_invalid_selection_does_not_mutate_workspace(api, selector):
    store, _, client = api
    draft = store.create("Invalid gust removal")
    path = f"/v1/studio/workspaces/{draft['id']}"
    saved = client.post(path + "/operations", json={"operations": [op("components", "add_lowalt_components"), op("gust", "add_gust", start_s=20, end_s=25, speed_mps=9)]})
    assert saved.status_code == 200, saved.text
    before = store.get(draft["id"])
    response = client.post(path + "/operations", json={"operations": [op("invalid-remove", "remove_gust", **selector)], "revision": before["revision"]})
    assert response.status_code == 422, response.text
    assert store.get(draft["id"]) == before


@pytest.fixture
def api(tmp_path: Path):
    store = WorkspaceStore(tmp_path / "workspaces", None)
    app = FastAPI()
    app.state.templates = TemplateRegistry()
    app.include_router(create_router(store, extracts={"fixture": SOURCE}))
    with TestClient(app) as client:
        yield store, app.state.templates, client


def test_catalog_basemap_defaults_overrides_and_disable(api, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, client = api
    monkeypatch.delenv("AEROAGENTSIM_BASEMAP_URL", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_BASEMAP_ATTRIBUTION", raising=False)
    result = client.get("/v1/studio/catalog").json()
    assert result["basemap"] == {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "attribution": "© OpenStreetMap contributors", "max_zoom": 19}
    assert result["extracts"][0]["bounds"] == extract_bounds(SOURCE)
    monkeypatch.setenv("AEROAGENTSIM_BASEMAP_URL", "https://example.test/{z}/{x}/{y}")
    monkeypatch.setenv("AEROAGENTSIM_BASEMAP_ATTRIBUTION", "Local tiles")
    assert client.get("/v1/studio/catalog").json()["basemap"] == {"url": "https://example.test/{z}/{x}/{y}", "attribution": "Local tiles", "max_zoom": 19}
    monkeypatch.setenv("AEROAGENTSIM_BASEMAP_URL", "")
    assert client.get("/v1/studio/catalog").json()["basemap"] is None


def test_operation_catalog_matches_assistant_specs_and_examples(api) -> None:
    _, _, client = api
    response = client.get("/v1/studio/operations")
    assert response.status_code == 200
    specs = operation_catalogue()
    assert len(response.json()) == len(specs)
    for row, spec in zip(response.json(), specs):
        assert set(row) == {"op", "topic", "label", "label_zh", "schema", "example"}
        assert row["op"] == spec["name"] and row["schema"] == spec["parameters"]
        assert row["label"] and row["label_zh"]
        assert validate_operation(row["example"])[0] == row["op"]
    add_place = next(row for row in response.json() if row["op"] == "add_place")
    assert {"charging_pad", "depot", "kitchen", "customer"} <= set(add_place["schema"]["properties"]["role"]["enum"])


def test_dry_run_and_apply_identical_to_assistant_compile(api, tmp_path: Path) -> None:
    store, templates, client = api
    draft = store.create("Forms and assistant")
    path = f"/v1/studio/workspaces/{draft['id']}"
    operations = [op("station", "add_place", id="pad", role="charging_pad", at="center", capacity=2, power_w=7500)]
    baseline = files(store.root)
    expected = compile_plan(CompileContext(store, templates, None), {"workspace": draft["id"]}, operations, tmp_path / "assistant-plan")
    assert expected.ok, expected.errors
    # C11 exposes compiler warnings alongside notes so automatic prerequisites
    # stay visible in both the review and the saved workspace.
    assert len(expected.warnings) == 1
    warning = expected.warnings[0]
    assert warning["op_id"] == "auto-lowalt-components" and warning["field"] == "operations"
    assert warning["message"] not in expected.notes
    expected_notes = expected.notes + [warning["message"]]
    preview = client.post(path + "/operations", json={"operations": operations, "revision": draft["revision"], "dry_run": True})
    assert preview.status_code == 200, preview.json()
    assert preview.json()["diff"] == {key: expected.diff_base[key] for key in ("added", "changed", "removed")}
    assert preview.json()["notes"] == expected_notes
    assert files(store.root) == baseline
    response = client.post(path + "/operations", json={"operations": operations, "revision": draft["revision"]})
    assert response.status_code == 200, response.json()
    saved = response.json()
    assert saved == client.get(path).json()
    assert saved["scenario"] == _rebase(expected.document, expected.files_dir, store.directory(draft["id"]))
    assert saved["revision"] == draft["revision"] + 1
    assert saved["capabilities"] == {"lowalt": True, "region": False, "network": False, "city": False}
    assert saved["notes"] == expected_notes
    assert store.versions(draft["id"]) == []
    assert not list(store.directory(draft["id"]).glob("journal*"))


def test_forms_add_restaurant_then_orders_and_explicit_fleets_without_a_base_fleet(api) -> None:
    store, _, client = api
    draft = store.create("Stepwise forms")
    path = f"/v1/studio/workspaces/{draft['id']}"
    facilities = [
        op("restaurant", "add_place", id="kitchen", role="kitchen", at=[0, 0, 10], preparation_s=17),
        op("depot", "add_place", id="depot", role="depot", at=[0, 0, 10]),
        op("customer", "add_place", id="customer", role="customer", at=[40, 0, 10]),
        op("pad", "add_place", id="pad", role="charging_pad", at=[0, 0, 10]),
    ]
    response = client.post(path + "/operations", json={"operations": facilities})
    assert response.status_code == 200, response.json()
    saved = response.json()
    assert next(e for e in saved["scenario"]["entities"] if e["id"] == "kitchen")["facts"]["lowalt.food_preparation_s"] == 17.0
    assert "generators" not in saved["scenario"]
    operations = [
        op("duration", "set_duration", seconds=60),
        op("couriers", "add_fleet", id="couriers", role="meal_courier", count=1, start="kitchen", battery_pct=80),
        op("logistics", "add_fleet", id="logistics", role="parcel_drone", count=1, start="depot"),
        op("meals", "add_orders", id="meals", kind="food", count=1, **{"from": ["kitchen"], "to": ["customer"]}, release={"mode": "scheduled", "times_s": [0]}),
        op("parcels", "add_orders", id="parcels", kind="parcel", count=2, **{"from": ["depot"], "to": ["customer"]}, release={"mode": "poisson", "rate_per_s": 0.5, "start_s": 0, "end_s": 30}),
        op("wind", "set_wind", speed_mps=2),
        op("gust", "add_gust", start_s=20, end_s=25, speed_mps=14),
        op("reserve", "set_rule", rule="battery_reserve_pct", value=30),
    ]
    response = client.post(path + "/operations", json={"operations": operations, "revision": saved["revision"]})
    assert response.status_code == 200, response.json()
    document = response.json()["scenario"]
    assert "generators" not in document
    assert len([e for e in document["entities"] if e["type"] == "lowalt:UAV"]) == 2
    assert document["engines"]["tasks"]["config"]["dispatch_min_soc"] == 0.3
    assert next(e for e in document["entities"] if e["id"] == "kitchen")["facts"]["lowalt.food_preparation_s"] == 17.0
    simulation = Simulation(load_scenario(document, base=store.directory(draft["id"])))
    try:
        simulation.start()
        simulation.run_until(10_000_000_000)
    finally:
        simulation.close()


def test_forms_cooperation_and_incident_match_assistant(api, tmp_path: Path) -> None:
    store, templates, client = api
    draft = store.create("Cooperation")
    operations = [
        op("fleet", "add_fleet", id="watchers", role="patrol_drone", count=2, start="center"),
        op("incident", "add_incident", id="fall", kind="person_fall", at=[5, 10, 0], time_s=1),
        op("cooperation", "set_cooperation", watchers={"vehicles": ["watchers-1"], "notice_radius_m": 200}, helpers={"vehicles": ["watchers-2"], "mode": "prescribed"}, responder={"mode": "prescribed"}),
    ]
    expected = compile_plan(CompileContext(store, templates, None), {"workspace": draft["id"]}, operations, tmp_path / "assistant")
    assert expected.ok, expected.errors
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/operations", json={"operations": operations})
    assert response.status_code == 200, response.json()
    assert response.json()["scenario"] == _rebase(expected.document, expected.files_dir, store.directory(draft["id"]))
    assert next(e for e in response.json()["scenario"]["entities"] if e["id"] == "fall")["type"] == "lowalt:Incident"


@pytest.mark.parametrize("operation,field", [
    (op("invalid-place", "add_place", id="pad", role="charging_pad", at=[None, 0, 0]), "params.at"),
    (op("invalid-power", "add_place", id="pad", role="charging_pad", at="center", power_w=0), "params.power_w"),
    (op("invalid-role", "add_place", id="pad", role="missing", at="center"), "params.role"),
    (op("missing-endpoints", "add_orders", id="orders", kind="parcel", count=1, **{"from": ["missing"], "to": ["other"]}, release={"mode": "scheduled", "times_s": [0]}), "from"),
    (op("invalid-gust", "add_gust", start_s=4, end_s=3, speed_mps=10), "end_s"),
    (op("unknown", "not_an_operation"), "op"),
])
def test_operation_errors_are_bilingual_structured_and_atomic(api, operation: dict, field: str) -> None:
    store, _, client = api
    draft = store.create("Invalid")
    before = files(store.root)
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/operations", json={"operations": [operation]})
    assert response.status_code == 422, response.json()
    detail = response.json()["detail"]
    assert set(detail) == {"operation", "field", "message"}
    assert detail["operation"] == operation["id"] and detail["field"] == field
    assert " / " in detail["message"]
    assert files(store.root) == before


@pytest.mark.parametrize("dry_run", [True, False])
def test_revision_conflict_and_late_failure_do_not_apply_any_operation(api, dry_run: bool) -> None:
    store, _, client = api
    draft = store.create("Conflict")
    path = f"/v1/studio/workspaces/{draft['id']}/operations"
    operations = [op("first", "add_place", id="pad", role="charging_pad", at="center"), op("second", "add_place", id="pad", role="customer", at="center")]
    before = files(store.root)
    response = client.post(path, json={"operations": operations[:1], "revision": draft["revision"] - 1, "dry_run": dry_run})
    assert response.status_code == 409
    response = client.post(path, json={"operations": operations, "revision": draft["revision"], "dry_run": dry_run})
    assert response.status_code == 422 and response.json()["detail"]["operation"] == "second"
    assert files(store.root) == before


@pytest.mark.parametrize("body", [
    {"operations": [] , "dry_run": None},
    {"operations": [], "revision": True},
    {"operations": [], "extra": 1},
    {"operations": [{"id": "bad-envelope", "op": "set_duration"}]},
    {"operations": [{"id": "bad-envelope", "op": "set_duration", "params": {}, "extra": 1}]},
])
def test_operation_request_envelope_is_strict(api, body: dict) -> None:
    store, _, client = api
    draft = store.create("Envelope")
    before = files(store.root)
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/operations", json=body)
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"operation", "field", "message"}
    assert files(store.root) == before


def test_saved_scenario_keeps_region_crop_and_network_can_use_it(api, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _, client = api
    draft = store.create("Region save")
    path = f"/v1/studio/workspaces/{draft['id']}"
    request = {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 20, "level_height_m": 3.5}
    preview = client.post(path + "/region", json={**request, "dry_run": True})
    assert preview.status_code == 200
    missing = client.post(path + "/network")
    assert missing.status_code == 422 and "dry_run=false" in missing.json()["detail"]
    assert "应用区域" in missing.json()["detail"]
    applied = client.post(path + "/region", json=request).json()
    crop = store.directory(draft["id"]) / "region.osm.xml"
    original_crop = crop.read_bytes()
    scenario = copy.deepcopy(applied["scenario"])
    scenario["run"]["until_ns"] = 5_000_000_000
    saved = client.post(path, json={"scenario": scenario, "revision": applied["revision"]})
    assert saved.status_code == 200, saved.json()
    reopened = client.get(path).json()
    assert reopened["region"] == applied["region"] and reopened["scene"] == applied["scene"]
    assert reopened["capabilities"] == {"lowalt": False, "region": True, "network": False, "city": False}
    assert crop.read_bytes() == original_crop

    def generate(osm: Path, output: Path) -> dict:
        assert osm == crop and osm.read_bytes() == original_crop
        output.write_text('<net><edge id="source-road"><lane id="source-road_0" shape="0,0 1,1"/></edge></net>')
        return {"edge_count": 1, "lane_count": 1}

    monkeypatch.setattr("aeroagentsim.authoring.scene.generate_sumo", generate)
    response = client.post(path + "/network")
    assert response.status_code == 200 and response.json()["edge_count"] == 1
    assert client.get(path).json()["capabilities"]["network"] is True
    crop.unlink()
    response = client.post(path + "/network")
    assert response.status_code == 422 and "reapply" in response.json()["detail"]
    reopened = client.get(path).json()
    assert reopened["capabilities"]["network"] is False
    assert reopened["network_state"] == "stale"


def test_region_parameters_and_network_invalidation_are_authoritative_after_refresh(api, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _, client = api
    draft = store.create("Region refresh")
    url = f"/v1/studio/workspaces/{draft['id']}"
    params = {"extract": "fixture", "bounds": extract_bounds(SOURCE), "alt": 25, "level_height_m": 6}
    response = client.post(url + "/region", json=params)
    assert response.status_code == 200, response.json()
    assert client.get(url).json()["region"] == params

    def generate(osm: Path, output: Path) -> dict:
        output.write_bytes(SOURCE.with_name("wujiaochang.net.xml").read_bytes())
        return {"status": "generated", "edge_count": 1, "lane_count": 2}

    monkeypatch.setattr("aeroagentsim.authoring.scene.generate_sumo", generate)
    response = client.post(url + "/network")
    assert response.status_code == 200, response.json()
    assert client.get(url).json()["network_state"] == "generated"
    updated = client.post(url + "/region", json={**params, "alt": 30})
    assert updated.status_code == 200, updated.json()
    reopened = WorkspaceStore(store.root, None).summary(draft["id"])
    assert reopened["region"] == {**params, "alt": 30}
    assert "network" not in reopened and reopened["capabilities"]["network"] is False
    assert reopened["network_state"] == "stale"
    assert client.get(url + "/network.net.xml").status_code == 422


def test_fixed_city_capability_comes_from_resolved_city_assets(api) -> None:
    store, _, client = api
    draft = store.create("Fixed city")
    assert client.get(f"/v1/studio/workspaces/{draft['id']}").json()["capabilities"]["city"] is False
    scenario = {**draft["scenario"], "city": "huangpu"}
    response = client.post(f"/v1/studio/workspaces/{draft['id']}", json={"scenario": scenario})
    assert response.status_code == 200
    assert client.get(f"/v1/studio/workspaces/{draft['id']}").json()["capabilities"]["city"] is True


def test_operation_preview_fields_have_readable_labels_values_and_units(api) -> None:
    store, _, client = api
    draft = store.create("Readable review")
    path = f"/v1/studio/workspaces/{draft['id']}"
    baseline = client.post(path + "/operations", json={"operations": [
        op("pad", "add_place", id="pad", role="charging_pad", at="center", label="West charger", label_zh="西侧充电站", power_w=5000),
        op("unused", "add_place", id="unused", role="depot", at=[20, 0, 0]),
        op("wind", "set_wind", speed_mps=2),
        op("duration", "set_duration", seconds=300),
        op("algorithm", "set_algorithm", slot="allocation", value="nearest"),
        op("reserve", "set_rule", rule="battery_reserve_pct", value=30),
    ]})
    assert baseline.status_code == 200, baseline.text
    saved = store.get(draft["id"])
    before_files = files(store.root)
    response = client.post(path + "/operations", json={"revision": saved["revision"], "dry_run": True, "operations": [
        op("wind", "set_wind", speed_mps=5.5),
        op("duration", "set_duration", seconds=900),
        op("algorithm", "set_algorithm", slot="allocation", value="auction"),
        op("reserve", "set_rule", rule="battery_reserve_pct", value=35),
        op("move", "update_place", id="pad", at=[-20, 10, 0], capacity=3, power_w=7500),
        op("add", "add_place", id="new", role="depot", at=[30, 0, 0], label="East depot", label_zh="东侧仓库"),
        op("remove", "remove_place", id="unused"),
    ]})
    assert response.status_code == 200, response.text
    difference = response.json()["diff"]
    changed = {row["id"]: {field["name"]: field for field in row["fields"]} for row in difference["changed"]}
    wind = changed["environment"]["wind.speed_mps"]
    assert wind["before"] == 2 and wind["after"] == 5.5
    assert wind["label"] == "Wind speed (m/s)" and wind["label_zh"] == "风速（米/秒）"
    assert wind["before_text"] == "2 m/s" and wind["after_text"] == "5.5 m/s"
    assert wind["after_text_zh"] == "5.5 米/秒"
    duration = changed["duration_s"]["duration_s"]
    assert duration["before_text"] == "300 s" and duration["after_text"] == "900 s"
    allocation = changed["algorithms"]["allocation"]
    catalog = {row["id"]: row for row in discover_algorithm_catalog()["allocation"]}
    assert allocation["label"] == "Allocation algorithm" and allocation["label_zh"] == "分配算法"
    assert allocation["before_text"] == catalog["nearest"]["label"]
    assert allocation["after_text"] == catalog["auction"]["label"]
    assert allocation["after_text_zh"] == catalog["auction"]["label_zh"]
    assert changed["battery_reserve_pct"]["value"]["after_text"] == "35 %"
    assert changed["pad"]["position"]["after_text"] == "(-20, 10, 0) m"
    assert "7500 W" in changed["pad"]["details"]["after_text"]
    for group in difference.values():
        for row in group:
            assert row["label"] and row["label_zh"] and row["kind_label"] and row["kind_label_zh"]
            for field in row.get("fields", []):
                assert all(isinstance(field[key], str) and field[key] for key in ("label", "label_zh", "before_text", "after_text"))
    assert next(r for r in difference["added"] if r["id"] == "new")["kind_label_zh"] == "设施"
    assert next(r for r in difference["removed"] if r["id"] == "unused")["kind_label"] == "Facility"
    assert files(store.root) == before_files and store.get(draft["id"]) == saved


def test_readable_diff_preserves_null_zero_false_and_uses_ids_for_missing_labels() -> None:
    difference = diff(
        {"environment": {"wind": {"speed_mps": None}}, "rules": [{"id": "medical_preemption", "value": None}], "places": [{"id": "old"}], "origin": {"lon": 121.4794284}},
        {"environment": {"wind": {"speed_mps": 0}}, "rules": [{"id": "medical_preemption", "value": False}], "places": [{"id": "new"}], "origin": {"lon": 121.4845353}},
    )
    for group, identity in (("added", "new"), ("removed", "old")):
        row = next(r for r in difference[group] if r["id"] == identity)
        assert row["label"] == row["label_zh"] == identity
        assert row["kind"] == "place" and row["kind_label"] == "Facility" and row["kind_label_zh"] == "设施"
    fields = {r["id"]: r["fields"][0] for r in difference["changed"]}
    wind = fields["environment"]
    assert wind["before"] is None and wind["after"] == 0
    assert wind["before_text"] == "—" and wind["after_text"] == "0 m/s"
    preemption = fields["medical_preemption"]
    assert preemption["before"] is None and preemption["after"] is False
    assert preemption["before_text"] == "—" and preemption["after_text"] == "Off"
    assert preemption["after_text_zh"] == "关闭"
    assert fields["origin"]["before_text"] == "121.4794284 °" and fields["origin"]["after_text"] == "121.4845353 °"


def test_algorithm_diff_uses_slot_catalog_for_plugin_names_and_config_units(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def catalog():
        calls.append(True)
        return {"allocation": [
            {"id": "nearest", "label": "Installed nearest", "label_zh": "已安装就近分配"},
            {"id": "auction", "label": "Installed auction", "label_zh": "已安装竞价分配"},
        ]}

    monkeypatch.setattr("aeroagentsim.services.algorithm_catalog.discover_algorithm_catalog", catalog)
    difference = diff({"algorithms": {"allocation": {"plugin": "nearest", "config": {"distance_m": 2}}}},
                      {"algorithms": {"allocation": {"plugin": "auction", "config": {"distance_m": 4}}}})
    fields = {f["name"]: f for f in difference["changed"][0]["fields"]}
    plugin = fields["allocation.plugin"]
    assert plugin["label"] == "Allocation algorithm" and plugin["label_zh"] == "分配算法"
    assert plugin["before_text"] == "Installed nearest" and plugin["after_text"] == "Installed auction"
    assert plugin["after_text_zh"] == "已安装竞价分配"
    assert fields["allocation.config.distance_m"]["after_text"] == "4 m"
    assert len(calls) == 1


def test_assistant_region_append_and_component_rows_get_readable_metadata(api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, templates, _ = api
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", json.dumps({"fixture": str(SOURCE.resolve())}))
    draft = store.create("Readable geographic preview")
    ctx = CompileContext(store, templates, None, allow_region=True)
    result = compile_plan(ctx, {"workspace": draft["id"]}, [
        op("region", "set_region", extract="fixture", bounds=extract_bounds(SOURCE), alt=20, level_height_m=3.5),
        op("pad", "add_place", id="pad", role="charging_pad", at="center"),
    ], tmp_path / "plan")
    assert result.ok, result.errors
    component = next(r for r in result.diff_base["added"] if r["kind"] == "components")
    assert component["kind_label"] == "Execution components" and component["kind_label_zh"] == "执行组件"
    region = next(r for r in result.diff_base["changed"] if r["id"] == "region")
    fields = {f["name"]: f for f in region["fields"]}
    assert fields["bounds"]["label"] == "Region bounds (°)" and fields["bounds"]["before_text"] == "—"
    assert fields["origin"]["before"] is None and fields["origin"]["before_text"] == "—"
    assert "Altitude: 20 m" in fields["origin"]["after_text"]
    preview = json.loads((result.files_dir / "region.plan.json").read_text())["preview"]
    assert preview["warnings_summary"] == {"missing_height": 1, "other": 1}
    assert len(preview["diagnostics"]) == 2


def test_fleet_removal_dependency_rows_have_labels_and_readable_kinds(api) -> None:
    store, _, client = api
    draft = store.create("Readable removals")
    path = f"/v1/studio/workspaces/{draft['id']}"
    added = client.post(path + "/operations", json={"operations": [
        op("fleet", "add_fleet", id="logistics", role="parcel_drone", count=2, start="center"),
    ]})
    assert added.status_code == 200, added.text
    response = client.post(path + "/operations", json={"dry_run": True, "operations": [op("remove", "remove_fleet", fleet="logistics")]})
    assert response.status_code == 200, response.text
    removed = response.json()["diff"]["removed"]
    assert any("path" in r for r in removed)
    for row in removed:
        assert row["label"] and row["label_zh"] and row["kind_label"] and row["kind_label_zh"]
        if "path" in row:
            assert row["label_zh"] == row["id"]

