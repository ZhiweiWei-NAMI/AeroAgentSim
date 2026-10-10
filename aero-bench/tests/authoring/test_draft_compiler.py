from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
import yaml

import pytest

from aero_bench.authoring.compilation_contracts import CityCompileRequest
from aero_bench.authoring.draft_compiler import CityDraftCompiler, load_published_compilation
from aero_bench.authoring.native_registry import (
    NativeSceneDefinition, NativeSceneRegistry, verify_native_scene,
)
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.projector import project_public_scenario
from tools import build_inspection_reference as builder
from tools.build_agent_inspection_images import REFERENCE_COMPONENTS, _reference_image_lock_payload


def ref(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def registration_document(root: Path) -> dict:
    run = json.loads((root / "resolved-run.json").read_bytes())
    world = json.loads((root / "world/package.json").read_bytes())
    runs = resolve_suite(str(root / "suite.yaml"), executor_kind="docker_reference",
                         provider_registry=builtin_provider_registry(),
                         task_package_resolvers=builtin_task_package_resolvers())
    scene_path = root / "city-presentation/inspection-reference-v1.json"
    scene_path.parent.mkdir(exist_ok=True)
    scene_path.write_bytes(canonical_json_bytes(project_public_scenario(runs[0]).model_dump(mode="json")))
    files = [{"file": ref(root, path), "size_bytes": path.stat().st_size}
             for path in sorted(root.rglob("*")) if path.is_file() and path.name not in {
                 "resolved-run.json", "runner.local.yaml", "source-lock.json", "image-lock.json",
             }]
    render_ids = {item["render_asset_id"] for item in world["buildings"]}
    presentation = []
    for asset in world["assets"]:
        if asset["artifact"]["artifact_id"] in render_ids:
            presentation.append({
                "file": {"path": asset["artifact"]["selector"].split("#")[0],
                         "sha256": asset["artifact"]["sha256"]},
                "size_bytes": asset["byte_size"],
                "world_asset_ids": [asset["artifact"]["artifact_id"]],
            })
    return {
        "schema_version": "aero-bench.native-scene-registration/v1",
        "registration_id": "inspection.reference.unit",
        "profile_id": "inspection.reference.v1",
        "scene_path": "/city-presentation/inspection-reference-v1.json",
        "scene_document": {"file": ref(root, scene_path), "size_bytes": scene_path.stat().st_size},
        "suite": ref(root, root / "suite.yaml"),
        "world": ref(root, root / "world/package.json"),
        "alignment": ref(root, root / "world/alignment.json"),
        "world_id": world["world_id"], "world_digest": world["world_digest"],
        "files": files, "presentation": presentation,
        "reference_draft": {
            "purpose": "scenario-authoring", "schema_version": "aero-bench.city-workspace/v3",
            "name": "Explicit inspection reference", "seed": run["seed"],
            "scenePath": "/city-presentation/inspection-reference-v1.json",
            "environment": {
                "cloudCover": 0, "precipitation": "none", "precipitationRateMmPerH": 0,
                "visibilityM": 10000, "windMps": 0, "windDirectionDeg": 0,
                "timeOfDay": "day", "reflectionsEnabled": True,
            },
            "fleet": [{"id": "uav.inspector", "assetId": "model:holybro-x500", "count": 1,
                       "homeFacilityId": None, "batteryWh": 500, "reserveRatio": 0.2}],
            "traffic": {"vehicles": 20, "pedestrians": 5, "bicycles": 0},
            "facilities": [], "airspace": [],
            "algorithms": {"mode": "centralized", "assignment": "external", "routing": "external",
                           "energy": "external", "parameters": {"profile": "inspection.reference.v1"}},
            "deployment": {"executor": "docker_reference", "imageRef": run["agents"][0]["workload"]["runtime"]["image"]},
            "events": [], "actionRules": [], "stateKeyframes": [], "labelRules": [],
            "orders": [],
            "orderGeneration": {"seed": run["seed"], "maxOrders": 0, "startAtS": 0,
                                "endAtS": 3600, "cargoMinKg": 0.1, "cargoMaxKg": 1,
                                "deadlineLeadS": 600},
            "performanceProfiles": [], "authoredLandscape": [],
        },
    }


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("draft-native-template")
    revision, _ = builder.urban._managed_source_closure()
    images = {component.key: f"localhost:5000/unit-only/{component.key}@sha256:{index:064x}"
              for index, component in enumerate(REFERENCE_COMPONENTS, 1)}
    lock = root / "unit-lock.json"
    lock.write_bytes(canonical_json_bytes(_reference_image_lock_payload(revision=revision, images=images)))
    # This fixture compiles the real package generator with unit-only image pins.
    # No test image is executed or counted as native/formal acceptance evidence.
    bundle = builder.build(root / "bundle", lock)
    document = registration_document(bundle)
    return bundle, document


def request(scene, **changes):
    raw = {
        "schema_version": "aero-bench.city-compile-request/v1",
        "registration_id": scene.definition.registration_id,
        "registration_sha256": scene.registration_sha256,
        "draft": scene.definition.reference_draft.snapshot(),
    }
    raw.update(changes)
    return raw


@pytest.fixture
def restriction_scene(template, tmp_path):
    source, _ = template
    root = tmp_path / "capable-template"
    shutil.copytree(source, root)
    env_path = root / "environment/environment.yaml"
    env = yaml.safe_load(env_path.read_text())
    provider = next(item for item in env["providers"] if item["adapter"] == "sumo.traci")
    provider["capabilities"].append("sumo.traffic.restrictions")
    env_path.write_text(yaml.safe_dump(env))
    suite_path = root / "suite.yaml"
    suite = yaml.safe_load(suite_path.read_text())
    suite["cases"][0]["environment"] = ref(root, env_path)
    suite_path.write_text(yaml.safe_dump(suite))
    document = registration_document(root)
    return verify_native_scene(root, NativeSceneDefinition.model_validate(document))


def test_traffic_restriction_lowers_into_pinned_config_and_run_id(restriction_scene, tmp_path):
    scene = restriction_scene
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    raw = request(scene)
    baseline = compiler.compile(CityCompileRequest.model_validate(raw))
    raw["draft"]["events"] = [{"id": "restrict.1", "type": "traffic.restricted", "atS": 1,
        "targetId": "A1A2", "payload": {"disallowedClasses": ["passenger"]}}]
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "compiled"
    assert result.runs[0].run_id != baseline.runs[0].run_id
    _, bundle, runs = load_published_compilation(compiler.output_root, result.compilation_id)
    run = runs[0]
    provider = next(item for item in run.environment.providers if item.adapter == "sumo.traci")
    config_path = bundle / provider.config.file.path
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == provider.config.file.sha256
    config = json.loads(config_path.read_bytes())
    assert config["restrictions"] == [{"event_id": "restrict.1", "at_tick": 2,
        "edge_id": "A1A2", "disallowed_classes": ["passenger"]}]
    raw["draft"]["events"][0]["atS"] = 1.5
    changed = compiler.compile(CityCompileRequest.model_validate(raw))
    assert changed.runs[0].run_id != result.runs[0].run_id


@pytest.mark.parametrize("change,field", [
    ({"atS": 0}, "atS"), ({"atS": 0.75}, "atS"), ({"atS": 301}, "atS"),
    ({"targetId": "absent-edge"}, "targetId"),
    ({"payload": {"disallowedClasses": ["bus"]}}, "payload"),
])
def test_traffic_restriction_rejects_unexecutable_edits(restriction_scene, tmp_path, change, field):
    scene = restriction_scene
    raw = request(scene)
    raw["draft"]["events"] = [{"id": "restrict.1", "type": "traffic.restricted", "atS": 1,
        "targetId": "A1A2", "payload": {"disallowedClasses": ["passenger"]}, **change}]
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "blocked"
    assert f"/draft/events/0/{field}" in {item.field for item in result.blockers}


def test_compile_applies_seed_and_preserves_every_authoring_field(template, tmp_path):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    raw = request(scene)
    raw["draft"]["seed"] = 7654
    raw["draft"]["name"] = "Saved edited draft"
    raw["draft"]["environment"].update(precipitation="rain", precipitationRateMmPerH=6, timeOfDay="night")
    raw["draft"]["stateKeyframes"] = [{"id": "frame.1", "atS": 1, "entityId": "uav.inspector",
                                     "position": {"x": 7, "y": 3, "z": 1}, "label": "Authored only"}]
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "compiled"
    assert result.executed is result.verified is False
    bundle, suite = compiler.bundle(result.compilation_id)
    runs = resolve_suite(str(bundle / suite.path), executor_kind="docker_reference",
                         task_package_resolvers=builtin_task_package_resolvers(),
                         provider_registry=builtin_provider_registry())
    assert runs[0].run_id == result.runs[0].run_id
    assert runs[0].seed == runs[0].scenario.seed == 7654
    snapshot = json.loads((bundle / "authoring/workspace-snapshot.json").read_bytes())
    assert snapshot["draft"] == raw["draft"]
    assert snapshot["semantics"]["environment"] == "visual_authoring_only_not_provider_weather"
    assert snapshot["semantics"]["orders"] == "authored_demand_only_not_created_assigned_or_dispatched"
    assert snapshot["semantics"]["orderGeneration"] == "authoring_configuration_only_not_order_creation"
    assert snapshot["semantics"]["performanceProfiles"] == "declared_estimates_not_measured_or_mesh_inferred"
    assert snapshot["semantics"]["authoredLandscape"] == "visual_authoring_only_not_surveyed_or_provider_state"
    original_world = (root / definition["world"]["path"]).read_bytes()
    assert (bundle / definition["world"]["path"]).read_bytes() == original_world
    assert all(not (os.stat(path).st_mode & 0o222) for path in bundle.rglob("*") if path.is_file())
    asset = next(item for item in runs[0].scenario.assets if item.asset_id == "asset.city-authoring-snapshot")
    assert asset.classification == "private"
    assert all(audience.role == "verifier" for audience in asset.audiences)
    assert compiler.compile(CityCompileRequest.model_validate(raw)) == result


def test_all_retained_and_applied_edits_change_run_identity(template, tmp_path):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    ids = []
    for field, value in (("seed", 12), ("name", "Changed label"), ("environment", {
        **scene.definition.reference_draft.environment.model_dump(mode="json"), "timeOfDay": "night",
    })):
        raw = request(scene)
        raw["draft"][field] = value
        ids.append(compiler.compile(CityCompileRequest.model_validate(raw)).runs[0].run_id)
    assert len(set(ids)) == 3


@pytest.mark.parametrize("field,value,expected_field", [
    ("traffic", {"vehicles": 1, "pedestrians": 5, "bicycles": 0}, "/draft/traffic/vehicles"),
    ("fleet", [], "/draft/fleet"),
    ("algorithms", {"mode": "distributed", "assignment": "external", "routing": "external",
                    "energy": "external", "parameters": {"profile": "inspection.reference.v1"}}, "/draft/algorithms/mode"),
    ("events", [{"id": "e", "type": "weather.changed", "atS": 0, "targetId": "", "payload": {}}], "/draft/events/0/type"),
    ("actionRules", [{"id": "a", "eventType": "weather.changed", "action": "takeoff",
                      "executorRole": "vehicle", "arguments": {}}], "/draft/actionRules"),
    ("labelRules", [{"id": "l", "field": "battery", "operator": "lt", "value": 0.2,
                     "label": "Low"}], "/draft/labelRules"),
    ("orderGeneration", {"seed": 7, "maxOrders": 1, "startAtS": 0, "endAtS": 3600,
                         "cargoMinKg": 0.1, "cargoMaxKg": 1, "deadlineLeadS": 600},
     "/draft/orderGeneration/maxOrders"),
    ("performanceProfiles", [{"fleetEntryId": "uav.inspector", "sourceLabel": "operator",
                              "provenance": "authored estimate",
                              "aircraftBody": {"xM": 0.5, "yM": 0.3, "zM": 0.5},
                              "cruiseSpeedMps": 12, "cruisePowerW": 420,
                              "hoverPowerW": 500, "chargeEfficiency": 0.9}],
     "/draft/performanceProfiles"),
    ("authoredLandscape", [{"id": "green.1", "label": "Courtyard",
                             "provenance": "authored", "kind": "green",
                             "polygon": [{"x": 0, "z": 0}, {"x": 5, "z": 0},
                                         {"x": 0, "z": 5}]}],
     "/draft/authoredLandscape"),
])
def test_unsupported_execution_inputs_are_not_ignored(template, tmp_path, field, value, expected_field):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    raw = request(scene)
    raw["draft"][field] = value
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "blocked" and result.suite is None and result.runs == ()
    assert expected_field in {item.field for item in result.blockers}
    with pytest.raises(ValueError, match="blocked compilation"):
        compiler.bundle(result.compilation_id)


def test_authored_orders_get_their_own_pointer_blocker(template, tmp_path):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    raw = request(scene)
    raw["draft"]["facilities"] = [
        {"id": "facility.source", "name": "Source", "kind": "vertiport",
         "position": {"x": 0, "z": 0}, "rotationDeg": 0, "widthM": 10,
         "depthM": 10, "heightM": 2, "capacity": 1, "chargingPowerW": 0},
        {"id": "facility.destination", "name": "Destination", "kind": "vertiport",
         "position": {"x": 20, "z": 0}, "rotationDeg": 0, "widthM": 10,
         "depthM": 10, "heightM": 2, "capacity": 1, "chargingPowerW": 0},
        {"id": "facility.hub", "name": "Hub", "kind": "hub",
         "position": {"x": 10, "z": 0}, "rotationDeg": 0, "widthM": 10,
         "depthM": 10, "heightM": 2, "capacity": 1, "chargingPowerW": 0},
    ]
    raw["draft"]["orders"] = [{
        "id": "order.1", "sourceFacilityId": "facility.source",
        "destinationFacilityId": "facility.destination",
        "hubHandoffFacilityId": "facility.hub", "cargoKg": 1,
        "releaseAtS": 0, "deliverByS": 60,
    }]
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "blocked"
    fields = {item.field for item in result.blockers}
    assert "/draft/facilities" in fields
    assert "/draft/orders" in fields


@pytest.mark.parametrize("event_kind,consumer", [
    ("order.created", "inspection.business"),
    ("weather.changed", "Weather Provider"),
    ("airspace.activated", "Airspace Provider"),
    ("charger.outage", "charger state"),
    ("traffic.restricted", "sumo.traci"),
])
def test_editor_events_require_a_declared_native_consumer(
    template, tmp_path, event_kind, consumer,
):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    raw = request(scene)
    raw["draft"]["facilities"] = [{
        "id": "facility.charger", "name": "Charger", "kind": "charger",
        "position": {"x": 0, "z": 0}, "rotationDeg": 0, "widthM": 10,
        "depthM": 10, "heightM": 2, "capacity": 1, "chargingPowerW": 500,
    }]
    raw["draft"]["airspace"] = [{
        "id": "zone.1", "name": "Authored zone",
        "polygon": [{"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 0, "z": 10}],
        "floorM": 0, "ceilingM": 20, "startsAtS": 0, "endsAtS": None,
        "source": {"kind": "manual", "label": "Unit test"},
    }]
    target = {"airspace.activated": "zone.1", "charger.outage": "facility.charger"}
    raw["draft"]["events"] = [{
        "id": "event.1", "type": event_kind, "atS": 1.5,
        "targetId": target.get(event_kind, ""), "payload": {"authored": True},
    }]
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    event_blockers = [item for item in result.blockers if item.field.startswith("/draft/events")]
    assert len(event_blockers) == 1
    assert event_blockers[0].code == "event.provider_capability_unavailable"
    assert event_blockers[0].field == "/draft/events/0/type"
    assert consumer in event_blockers[0].message
    assert result.status == "blocked" and result.suite is None and result.runs == ()
    assert result.executed is result.verified is False
    assert not (compiler.output_root / result.compilation_id / "bundle").exists()
    retained = json.loads((compiler.output_root / result.compilation_id / "request.json").read_bytes())
    assert retained["draft"]["events"] == raw["draft"]["events"]


def test_every_scheduled_event_gets_a_blocker_and_changes_request_identity(template, tmp_path):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    raw = request(scene)
    raw["draft"]["events"] = [
        {"id": "event.1", "type": "weather.changed", "atS": 1,
         "targetId": "", "payload": {"windMps": 5}},
        {"id": "event.2", "type": "traffic.restricted", "atS": 2,
         "targetId": "edge.1", "payload": {"allowed": []}},
    ]
    first = compiler.compile(CityCompileRequest.model_validate(raw))
    assert {item.field for item in first.blockers} == {
        "/draft/events/0/type", "/draft/events/1/type",
    }
    raw["draft"]["events"][0]["atS"] = 3
    second = compiler.compile(CityCompileRequest.model_validate(raw))
    assert first.compilation_id != second.compilation_id
    assert first.draft_sha256 != second.draft_sha256
    assert first.runs == second.runs == ()


@pytest.mark.parametrize("mutation,code", [
    ("scene", "scene.presentation_mismatch"), ("pin", "scene.identity_changed"),
    ("kubernetes", "executor.prerequisites_unavailable"), ("empty_image", "agent.image_missing"),
    ("image", "agent.image_unregistered"), ("seed", "seed.provider_range"),
])
def test_identity_and_executor_blockers(template, tmp_path, mutation, code):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    raw = request(scene)
    if mutation == "scene":
        raw["draft"]["scenePath"] = "/city-presentation/default-scene-v1.json"
    elif mutation == "pin":
        raw["registration_sha256"] = "a" * 64
    elif mutation == "kubernetes":
        raw["draft"]["deployment"]["executor"] = "kubernetes_cluster"
    elif mutation == "empty_image":
        raw["draft"]["deployment"]["imageRef"] = ""
    elif mutation == "image":
        raw["draft"]["deployment"]["imageRef"] = "localhost:5000/other@sha256:" + "a" * 64
    elif mutation == "seed":
        raw["draft"]["seed"] = 0
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert code in {item.code for item in result.blockers}
    assert result.status == "blocked"


def test_unregistered_city_never_selects_reference(template, tmp_path):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    raw = request(scene)
    raw["registration_id"] = "shanghai.huangpu"
    compiler = CityDraftCompiler(tmp_path / "compilations")
    result = compiler.compile(CityCompileRequest.model_validate(raw))
    assert result.status == "blocked"
    assert result.blockers[0].code == "scene.unregistered"
    assert not (compiler.output_root / result.compilation_id / "bundle").exists()


@pytest.mark.parametrize("mutation,match", [
    ("world", "native world identity"), ("presentation", "rendering asset"),
    ("coverage", "every native building"), ("traffic", "SUMO"),
    ("fleet", "native X500"), ("policy", "external fixed policy"),
    ("generation", "order generation"), ("landscape", "authored logistics"),
])
def test_registry_rejects_unproved_native_bindings(template, mutation, match):
    root, definition = template
    raw = copy.deepcopy(definition)
    if mutation == "world":
        raw["world_digest"] = "a" * 64
    elif mutation == "presentation":
        raw["presentation"][0]["file"]["sha256"] = "a" * 64
        match = "presentation must be pinned"
    elif mutation == "coverage":
        raw["presentation"].pop()
    elif mutation == "traffic":
        raw["reference_draft"]["traffic"]["vehicles"] = 0
    elif mutation == "fleet":
        raw["reference_draft"]["fleet"][0]["count"] = 2
    elif mutation == "policy":
        raw["reference_draft"]["algorithms"]["assignment"] = "greedy"
    elif mutation == "generation":
        raw["reference_draft"]["orderGeneration"]["maxOrders"] = 1
    elif mutation == "landscape":
        raw["reference_draft"]["authoredLandscape"] = [{
            "id": "green.1", "label": "Courtyard", "provenance": "authored",
            "kind": "green", "polygon": [
                {"x": 0, "z": 0}, {"x": 5, "z": 0}, {"x": 0, "z": 5},
            ],
        }]
    with pytest.raises(ValueError, match=match):
        verify_native_scene(root, NativeSceneDefinition.model_validate(raw))


def test_compile_http_catalog_and_negative_path(template, tmp_path):
    import http.client
    import threading
    from aero_bench.authoring.api import make_server

    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    # Other authoring routes are outside this HTTP unit's scope.
    with make_server("127.0.0.1", 0, None, compiler=compiler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            connection.request("GET", "/authoring/v1/native-scenes")
            response = connection.getresponse()
            catalog = json.loads(response.read())
            assert response.status == 200
            assert catalog["registrations"][0]["registration_sha256"] == scene.registration_sha256
            selected = catalog["registrations"][0]
            connection.request("GET", selected["scene_url"])
            response = connection.getresponse()
            scene_bytes = response.read()
            assert response.status == 200
            assert hashlib.sha256(scene_bytes).hexdigest() == selected["scene_sha256"]
            assert len(scene_bytes) == selected["scene_size_bytes"]
            asset = json.loads(scene_bytes)["assets"][0]
            connection.request("GET", f"/authoring/v1/native-scenes/{scene.definition.registration_id}/assets/{asset['sha256']}")
            response = connection.getresponse()
            asset_bytes = response.read()
            assert response.status == 200 and hashlib.sha256(asset_bytes).hexdigest() == asset["sha256"]
            connection.request("GET", f"/authoring/v1/native-scenes/{scene.definition.registration_id}/assets/{scene.definition.world.sha256}")
            response = connection.getresponse()
            assert response.status == 404
            response.read()
            raw = request(scene)
            raw["draft"]["deployment"]["imageRef"] = ""
            connection.request("POST", "/authoring/v1/compilations", canonical_json_bytes(raw),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            result = json.loads(response.read())
            assert response.status == 422
            assert result["status"] == "blocked" and result["executed"] is False
            connection.request("GET", f"/authoring/v1/compilations/{result['compilation_id']}")
            response = connection.getresponse()
            assert response.status == 200 and json.loads(response.read()) == result
            connection.request("POST", "/authoring/v1/compilations", b'{"a":1,"a":2}',
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 400
            response.read()
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_published_compilation_loads_after_compiler_session_and_binds_control(template, tmp_path):
    from aero_bench.control.manager import ControlRunManager

    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    result = compiler.compile(CityCompileRequest.model_validate(request(scene)))
    published, bundle, runs = load_published_compilation(compiler.output_root, result.compilation_id)
    assert published == result and runs[0].run_id == result.runs[0].run_id
    manager = ControlRunManager.from_compilation(
        compilation_root=compiler.output_root, compilation_id=result.compilation_id,
        runner_config_path=root / "runner.local.yaml", output_root=tmp_path / "execution",
    )
    assert manager.catalog.runs[0].run_id == result.runs[0].run_id
    assert manager.catalog.suite_sha256 == result.suite.sha256
    assert manager._bundle_root == bundle
    assert manager._output_root != bundle


def test_published_compilation_rejects_tampered_resolved_run_and_input(template, tmp_path):
    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations", NativeSceneRegistry((scene,)))
    result = compiler.compile(CityCompileRequest.model_validate(request(scene)))
    bundle, _ = compiler.bundle(result.compilation_id)
    recorded = bundle / "compiled-resolved-runs.json"
    recorded.chmod(0o600)
    recorded.write_bytes(b'[]')
    with pytest.raises(ValueError, match="recorded ResolvedRun"):
        load_published_compilation(compiler.output_root, result.compilation_id)
    input_file = bundle / definition["world"]["path"]
    input_file.chmod(0o600)
    input_file.write_bytes(input_file.read_bytes() + b' ')
    with pytest.raises(ValueError, match="sha256 mismatch"):
        load_published_compilation(compiler.output_root, result.compilation_id)


def test_control_rejects_blocked_compilation_before_execution(template, tmp_path):
    from aero_bench.control.manager import ControlRunManager

    root, definition = template
    scene = verify_native_scene(root, NativeSceneDefinition.model_validate(definition))
    compiler = CityDraftCompiler(tmp_path / "compilations")
    result = compiler.compile(CityCompileRequest.model_validate(request(scene)))
    with pytest.raises(ValueError, match="blocked compilation"):
        ControlRunManager.from_compilation(
            compilation_root=compiler.output_root, compilation_id=result.compilation_id,
            runner_config_path=root / "runner.local.yaml", output_root=tmp_path / "execution",
        )
    assert not (tmp_path / "execution").exists()


def test_reference_registration_publishes_only_pinned_inputs(template, tmp_path):
    from aero_bench.authoring.reference_registration import publish_reference_registration

    root, _ = template
    manifest = publish_reference_registration(
        suite=root / "suite.yaml", alignment=root / "world/alignment.json",
        output=tmp_path / "registered", registration_id="inspection.reference.explicit",
        scene_path="/city-presentation/inspection-reference-explicit.json", name="Native reference",
        battery_wh=500, reserve_ratio=0.2,
    )
    registry = NativeSceneRegistry.from_manifest(manifest)
    scene = registry.get("inspection.reference.explicit")
    assert scene is not None
    assert scene.definition.reference_draft.schema_version == "aero-bench.city-workspace/v3"
    assert scene.definition.reference_draft.orderGeneration.maxOrders == 0
    assert scene.definition.reference_draft.orders == ()
    public = scene.public()
    assert public.scene_schema_version == "aero-bench.public-scenario/v1"
    assert public.scene_sha256 == scene.definition.scene_document.file.sha256
    assert (manifest.parent / "reference-workspace.json").is_file()
    assert not (manifest.parent / "runner.local.yaml").exists()
    assert not (manifest.parent / "resolved-run.json").exists()
    compiler = CityDraftCompiler(tmp_path / "compiled", registry)
    assert compiler.compile(CityCompileRequest.model_validate(request(scene))).status == "compiled"


def test_registration_requires_pinned_exact_native_presentation(template, tmp_path):
    root, definition = template
    raw = copy.deepcopy(definition)
    raw.pop("scene_document")
    with pytest.raises(ValueError, match="scene_document"):
        NativeSceneDefinition.model_validate(raw)
    raw = copy.deepcopy(definition)
    raw["scene_document"]["file"]["path"] = "city-presentation/other.json"
    with pytest.raises(ValueError, match="selected presentation path"):
        NativeSceneDefinition.model_validate(raw)
    import shutil
    shutil.copytree(root, tmp_path / "template")
    copied = tmp_path / "template"
    scene_document = copied / definition["scene_document"]["file"]["path"]
    document = json.loads(scene_document.read_bytes())
    document["seed"] += 1
    scene_document.write_bytes(canonical_json_bytes(document))
    raw = copy.deepcopy(definition)
    raw["scene_document"] = {"file": ref(copied, scene_document), "size_bytes": scene_document.stat().st_size}
    for index, item in enumerate(raw["files"]):
        if item["file"]["path"] == raw["scene_document"]["file"]["path"]:
            raw["files"][index] = raw["scene_document"]
    with pytest.raises(ValueError, match="exact public native scenario"):
        verify_native_scene(copied, NativeSceneDefinition.model_validate(raw))
