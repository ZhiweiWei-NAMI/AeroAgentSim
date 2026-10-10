from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml

import aero_bench.authoring.city_native_registration as city_registration
from aero_bench.authoring.city_native_registration import (
    CityNativeInputLock,
    CityNativeRegistrationBlocked,
    CityNativeRegistrationError,
    assess_city_native_registration,
    load_city_native_input_lock,
    verify_city_native_registration,
)
from aero_bench.world import frame_math
from aero_bench.world.contracts import WorldPackage
from aero_bench.serialization import canonical_json_bytes
from tools.audit_huangpu_native_execution import (
    HuangpuNativeExecutionAuditError,
    _audit_seal_root,
    _write_new,
)
from tools.build_huangpu_native_world import (
    _measure_sumo_network,
    _origin,
    _provider_coverage,
)
from tools.rebind_huangpu_native_registration import issue_lock
from tools.validate_huangpu_native_provider_contracts import (
    HuangpuNativeProviderContractError,
    _validate_grid_surface_coverage,
)


ROOT = Path(__file__).resolve().parents[2]
FAILED_LOCK_PATH = (
    ROOT / "validation/codex-takeover-20261001/B/huangpu-city-native-input-lock.json"
)
LOCK_PATH = (
    ROOT / "validation/codex-takeover-20261001/B/huangpu-city-native-input-lock-v6.json"
)
CAPACITY_PATH = (
    ROOT
    / "validation/codex-takeover-20261001/B/huangpu-native-artifact-capacity-v3.json"
)
BUNDLE_PATH = ROOT / "validation/codex-takeover-20261001/B/huangpu-native-world-v4"
RUNTIME_CAPACITY_PATH = (
    ROOT
    / "validation/codex-takeover-20261001/B/huangpu-native-runtime-capacity-v6.json"
)


@pytest.fixture(scope="module")
def definition() -> CityNativeInputLock:
    return load_city_native_input_lock(LOCK_PATH)


@pytest.fixture(scope="module")
def readiness(definition: CityNativeInputLock):
    return assess_city_native_registration(ROOT, definition)


def test_verified_huangpu_sources_expose_exact_native_blockers(readiness) -> None:
    evidence = readiness.source_evidence
    assert readiness.status == "blocked"
    assert evidence.verified_file_count == 27
    assert evidence.building_count == 414
    assert evidence.building_render_bytes == 101_076_596
    assert evidence.building_runtime_asset_count == 459
    assert evidence.building_runtime_asset_bytes == 5_971_864
    assert evidence.mesh_runtime_asset_count == 52
    assert evidence.mesh_runtime_asset_bytes == 6_745_174
    assert evidence.compiled_road_count == 289
    assert evidence.road_width_count == 289
    assert evidence.effective_fixture_count == 1_143
    assert evidence.native_road_ribbons_checked == 2_646
    assert evidence.native_traffic_frame_count == 481
    assert evidence.flight_preview_frame_count == 601
    assert evidence.traffic_artifact_class == "offline-engineering-preview"
    assert evidence.flight_preview_physical_simulation is False
    assert evidence.provider_registry_adapters == (
        "inspection.business",
        "logistics.business",
        "ns3.rpc",
        "px4.gazebo",
        "sumo.traci",
    )
    assert readiness.world_id == "world.shanghai-huangpu-east-v1"
    assert (
        readiness.world_digest
        == "18e3ac14954a515f85e0753ba050452a58fe933cbcfd1923fcd65b51b4560eed"
    )
    assert readiness.blocking_codes == (
        "provider.weather",
        "provider.airspace",
        "task.physical-logistics",
    )
    assert (
        readiness.run_id
        == "44f2ebb7ba410a37041d6b8b74374a81899a13a80a8ff8c3199688ca635806da"
    )


def test_native_city_inspection_lowering_preserves_physical_boundaries() -> None:
    bundle = BUNDLE_PATH
    resolved = json.loads((bundle / "resolved-run.json").read_bytes())
    scenario = resolved["scenario"]
    assert [item["target_id"] for item in scenario["semantic_targets"]] == [
        "target.city-facade.lower",
        "target.city-facade.upper",
    ]
    assert {(item["region_id"], item["kind"]) for item in scenario["regions"]} == {
        ("region.city-no-fly-east", "no_fly"),
        ("region.city-operational-geofence", "geofence"),
    }
    network_nodes = {
        item["node_id"]: item for item in scenario["network"]["node_bindings"]
    }
    assert network_nodes["node.operations"]["entity_id"] == (
        "entity.launch.huangpu-research"
    )
    lowering = json.loads(
        (bundle / "provenance/city-inspection-lowering.json").read_bytes()
    )
    route = lowering["route_model"]
    assert route["measurement_status"] == (
        "theoretical_assumptions_not_flight_evidence"
    )
    assert route["outbound_horizontal_m"] > 50.1
    assert route["roof_clearance_m"] >= 5.0
    assert lowering["formal_execution_started"] is False

    sdf_world = (
        ET.parse(bundle / "world/gazebo/huangpu-native.sdf").getroot().find("world")
    )
    assert sdf_world is not None
    models = {item.get("name"): item for item in sdf_world.findall("model")}
    assert "launch.huangpu-research" in models
    assert "station.operations" in models
    ground_sizes = {
        item.text for item in models["ground"].findall("./link/*/geometry/plane/size")
    }
    assert ground_sizes == {"1100.0 1100.0"}
    assert all(abs(point[0]) <= 550.0 for point in route["navigation_enu_m"])
    assert scenario["frame_authority"]["spatial_extent"] == {
        "min_east_m": -525.0,
        "max_east_m": 515.0,
        "min_north_m": -525.0,
        "max_north_m": 518.0,
        "min_up_m": -5.0,
        "max_up_m": 200.0,
        "vertical_reference": "enu_up",
    }
    terrain = json.loads((bundle / "world/terrain/heights.json").read_bytes())
    geoid = json.loads((bundle / "world/geoid/grid.json").read_bytes())
    for grid in (terrain, geoid):
        assert grid["east_axis_m"] == [-525.0, 0.0, 515.0]
        assert grid["north_axis_m"] == [-525.0, 0.0, 518.0]


def test_native_city_artifact_capacity_covers_declared_horizon() -> None:
    capacity = json.loads(CAPACITY_PATH.read_bytes())
    assert capacity["status"] == (
        "sized-from-retained-failed-runtime-evidence-not-executed"
    )
    source = capacity["source_evidence"]
    assert source["accepted_as_task_result"] is False
    assert source["run_id"] == (
        "85e8ccd462f3552f219a86a72a34d55e4fdeae48f9e114075eaae73f8a6d5cfd"
    )
    assert source["event_log"]["completed_tick_count"] == 47
    assert source["event_log"]["entity_state_count_min"] == 525
    assert source["event_log"]["entity_state_count_max"] == 525
    assert source["scene_states"]["declared_entity_count"] == 525
    assert source["scene_states"]["sample_count_min"] == 525
    assert source["scene_states"]["sample_count_max"] == 525

    target = capacity["target"]
    assert target["entity_count"] == 525
    assert target["max_steps"] == 600
    assert target["step_ns"] == 500_000_000
    assert target["maximum_simulation_horizon_ns"] == 300_000_000_000
    model = capacity["model"]
    assert model["event_log_projection_bytes"] == 2_353_733_405
    assert model["scene_states_projection_bytes"] == 475_447_200
    limits = capacity["artifact_limits"]
    assert limits == {
        "artifact.event-log": 3 * 1024**3,
        "artifact.scene-states": 1024**3,
    }
    assert limits["artifact.event-log"] > model["event_log_with_headroom_bytes"]
    assert limits["artifact.scene-states"] > model["scene_states_with_headroom_bytes"]
    assert capacity["runner_limits"] == {
        "artifact_volume_size_bytes": 8 * 1024**3,
        "input_volume_size_bytes": 8 * 1024**3,
        "scratch_size_bytes": 8 * 1024**3,
    }
    assert capacity["capacity"]["sealed_input_upper_bound_bytes"] == 4_817_223_431
    assert capacity["capacity"]["producer_maximum_bytes"] == 4_296_015_872

    resolved = json.loads((BUNDLE_PATH / "resolved-run.json").read_bytes())
    resolved_limits = {
        item["artifact_id"]: item["max_size_bytes"]
        for item in resolved["artifact_requirements"]
        if item["artifact_id"] in limits
    }
    assert resolved_limits == limits
    assert (
        sum(item["max_size_bytes"] for item in resolved["artifact_requirements"])
        == capacity["capacity"]["artifact_requirements_total_bytes"]
    )

    task = yaml.safe_load((BUNDLE_PATH / "task/task.yaml").read_text())
    package = json.loads((BUNDLE_PATH / "task/inspection-package.json").read_bytes())
    environment = yaml.safe_load(
        (BUNDLE_PATH / "environment/environment.yaml").read_text()
    )
    for requirements in (
        task["verifier"]["artifact_requirements"],
        package["artifact_requirements"],
        environment["harness_artifact_requirements"],
    ):
        applied = {
            item["artifact_id"]: item["max_size_bytes"]
            for item in requirements
            if item["artifact_id"] in limits
        }
        assert applied == limits

    runner = yaml.safe_load(
        (
            ROOT / "validation/codex-takeover-20261001/B/huangpu-native-runner-v6.yaml"
        ).read_text()
    )
    for field, value in capacity["runner_limits"].items():
        assert runner[field] == value


def test_native_city_runtime_capacity_covers_retention_and_delivery() -> None:
    report = json.loads(RUNTIME_CAPACITY_PATH.read_bytes())
    assert report["status"] == (
        "modelled-from-failed-runtime-evidence-not-executed"
    )
    assert report["formal_execution_started"] is False
    assert report["accepted_as_task_result"] is False
    assert report["failed_run"] == {
        "authoritative_seal": False,
        "elapsed_seconds_approx": 3300,
        "entity_count": 525,
        "harness_exit_code": 137,
        "harness_oom_killed": True,
        "public_trace": None,
        "run_id": "b364b5f70d9ae0c8480d1f59078ef4649ca787eaf8418207a9b4945e4081f778",
        "scenario_digest": "82aa5a3906dfde75d23060ab36fac23663762425296504f69786bb243a70998f",
        "terminal_simulation_time_ns": 150_000_000_000,
        "terminal_tick": 300,
        "verification": None,
        "world_digest": "18e3ac14954a515f85e0753ba050452a58fe933cbcfd1923fcd65b51b4560eed",
    }

    memory = report["harness_memory_model"]
    assert memory["configured_entity_count"] == 525
    assert memory["configured_max_steps"] == 600
    assert memory["retained_event_amplification_factor"] == 4
    assert memory["retained_event_graph_bound_bytes"] == 12 * 1024**3
    assert memory["seal_serialization_buffers_bound_bytes"] == 4 * 1024**3
    assert memory["peak_with_headroom_bytes"] == 20 * 1024**3
    assert memory["selected_harness_memory_mib"] == 32 * 1024

    timeout = report["runtime_timeout_model"]
    assert timeout["linear_full_horizon_seconds"] == 6600
    assert timeout["full_horizon_with_25_percent_headroom_seconds"] == 8250
    assert timeout["selected_runtime_timeout_seconds"] == 9000
    assert report["host_feasibility"]["feasible_at_measurement"] is True
    assert report["host_feasibility"]["available_memory_margin_bytes"] > 0

    delivery = report["public_trace_delivery_model"]
    calibration = delivery["calibration"]
    assert calibration["completed_ticks"] == 47
    assert calibration["entity_count"] == 525
    assert calibration["components"]["scene_states"]["item_bytes_max"] == 792_411
    assert calibration["components"]["trajectories"]["sample_bytes_max"] == 890
    terminal = delivery["projected_terminal_horizon"]
    declared = delivery["projected_declared_horizon"]
    assert terminal["public_trace_upper_bound_bytes"] == 286_813_455
    assert terminal["within_server_trace_limit"] is True
    assert declared["public_trace_upper_bound_bytes"] == 561_680_655
    assert declared["within_frontend_trace_limit"] is True

    resolved = json.loads((BUNDLE_PATH / "resolved-run.json").read_bytes())
    assert resolved["environment"]["harness"]["resources"]["memory_mib"] == 32768
    runner = yaml.safe_load(
        (
            ROOT / "validation/codex-takeover-20261001/B/huangpu-native-runner-v6.yaml"
        ).read_text()
    )
    assert runner["runtime_timeout_seconds"] == 9000


def test_native_city_provider_coverage_contains_exact_sumo_surface() -> None:
    scene_root = ROOT / "validation/scene-compiler-shanghai-huangpu-east-v1"
    manifest = json.loads((scene_root / "manifest.json").read_bytes())
    network = (
        ROOT
        / "validation/frontend-opus-20260930/ground/closed-loop-v6/round-3/refined/network.net.xml"
    )
    measurement = _measure_sumo_network(network, _origin(manifest))
    assert measurement["net_offset"] == [0.0, 0.0]
    assert measurement["surface_bounds_m"] == {
        "min_east_m": -505.7267,
        "max_east_m": 504.0643,
        "min_north_m": -503.7853,
        "max_north_m": 507.6304,
    }
    assert measurement["margin_m"] == 10.0
    coverage = _provider_coverage(measurement)
    assert coverage.model_dump(mode="json") == {
        "min_east_m": -525.0,
        "max_east_m": 515.0,
        "min_north_m": -525.0,
        "max_north_m": 518.0,
        "min_up_m": -5.0,
        "max_up_m": 200.0,
        "vertical_reference": "enu_up",
    }


def test_native_city_rebind_preserves_sources_and_replaces_only_world(
    definition: CityNativeInputLock,
) -> None:
    rebound = issue_lock(
        root=ROOT,
        base_lock_path=FAILED_LOCK_PATH,
        world_bundle=BUNDLE_PATH,
        authored_catalog_path=(
            ROOT
            / "validation/codex-takeover-20261001/B/huangpu-native-authoring-v4/merged-catalog.json"
        ),
        suite_path=None,
        runner_config_path=None,
    )
    assert rebound.source_files() == definition.source_files()
    assert rebound.world == definition.world
    assert rebound.execution is None


def test_native_city_execution_plan_passes_read_only_preflight() -> None:
    report = json.loads(
        (
            ROOT
            / "validation/codex-takeover-20261001/B/huangpu-native-execution-plan-v6.json"
        ).read_bytes()
    )
    assert report["status"] == "preflight-ready-not-executed"
    assert report["formal_execution_started"] is False
    assert report["preflight"] == {
        "blockers": [],
        "execution_scope": "formal_benchmark",
        "executor_kind": "docker_reference",
        "ready": True,
        "run_id": ("44f2ebb7ba410a37041d6b8b74374a81899a13a80a8ff8c3199688ca635806da"),
    }
    assert report["registration_blocking_codes"] == [
        "provider.weather",
        "provider.airspace",
        "task.physical-logistics",
    ]
    assert report["runtime_workload_count"] == 6
    workloads = report["workloads"]
    assert [(item["workload_id"], item["role"]) for item in workloads] == [
        ("harness", "harness"),
        ("flight", "provider"),
        ("network", "provider"),
        ("traffic", "provider"),
        ("business", "provider"),
        ("participant.agent", "agent"),
        ("inspection.verifier", "verifier"),
    ]
    assert all(item["bundle_input_count"] > 0 for item in workloads)
    assert workloads[-1]["image"] not in {item["image"] for item in workloads[:-1]}

    provider_check = json.loads(
        (
            ROOT
            / "validation/codex-takeover-20261001/B/huangpu-native-provider-contract-check-v6.json"
        ).read_bytes()
    )
    assert provider_check["status"] == "validated-not-executed"
    assert provider_check["run_id"] == report["run_id"]
    providers = provider_check["providers"]
    assert providers["flight"]["inspection_count"] == 2
    assert providers["flight"]["launch_contact"] == {
        "model_name": "launch.huangpu-research",
        "token": "launch_pad.launch.huangpu-research",
    }
    assert providers["traffic"]["object_binding_count"] == 108
    assert providers["network"]["node_count"] == 2
    assert providers["network"]["propagation_volume_count"] == 414
    assert providers["business"]["artifact_type"] == "business.state"
    coverage = provider_check["coordinate_coverage"]
    assert coverage["sumo_surface"] == {
        "network_path": "world/sumo/network.net.xml",
        "network_sha256": (
            "38bd3d8a2609cecef7f0c98608e0d58eb9f5123448fd675a6e1d8dff6ccfeffc"
        ),
        "lane_count": 2_719,
        "junction_count": 636,
        "surface_point_count": 15_055,
        "surface_bounds_enu_m": {
            "min_east_m": -505.7267,
            "max_east_m": 504.0643,
            "min_north_m": -503.7853,
            "max_north_m": 507.6304,
        },
    }
    for grid_name in ("geoid_grid", "terrain_grid"):
        assert coverage[grid_name]["covers_sumo_surface"] is True
        assert coverage[grid_name]["coverage_bounds_enu_m"] == {
            "min_east_m": -525.0,
            "max_east_m": 515.0,
            "min_north_m": -525.0,
            "max_north_m": 518.0,
        }
    assert provider_check["formal_execution_started"] is False


def test_provider_contract_check_rejects_datum_grid_shorter_than_sumo_surface() -> None:
    grid = frame_math.ScalarGridSampler(
        east_axis_m=(-500.0, 0.0, 500.0),
        north_axis_m=(-500.0, 0.0, 500.0),
        values_m=((0.0, 0.0, 0.0),) * 3,
    )
    with pytest.raises(
        HuangpuNativeProviderContractError,
        match="terrain scalar grid does not cover the SUMO network surface",
    ):
        _validate_grid_surface_coverage(
            grid=grid,
            grid_path="world/terrain/heights.json",
            interpolation="bilinear",
            label="terrain scalar grid",
            surface_bounds={
                "min_east_m": -505.7267,
                "max_east_m": 504.0643,
                "min_north_m": -503.7853,
                "max_north_m": 507.6304,
            },
        )


def test_native_execution_audit_rehashes_closed_seal_inventory(
    tmp_path: Path,
) -> None:
    seal_root = tmp_path / "runtime-seal"
    artifact_path = seal_root / "provider/evidence.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_payload = b"{}\n"
    artifact_path.write_bytes(artifact_payload)
    artifact = {
        "artifact_id": "artifact.test-evidence",
        "artifact_type": "test.evidence",
        "producer_id": "provider.test",
        "visibility": "private",
        "relative_path": "provider/evidence.json",
        "sha256": hashlib.sha256(artifact_payload).hexdigest(),
        "size_bytes": len(artifact_payload),
    }
    body = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": "a" * 64,
        "attempt_id": "attempt.test",
        "execution_scope": "formal_benchmark",
        "artifacts": [artifact],
        "event_chain_root": "b" * 64,
    }
    manifest = {
        **body,
        "manifest_digest": hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
    }
    (seal_root / "seal-manifest.json").write_bytes(
        canonical_json_bytes(manifest) + b"\n"
    )

    sealed, inventory = _audit_seal_root(
        seal_root,
        manifest_name="seal-manifest.json",
        label="test seal",
    )
    assert sealed.manifest_digest == manifest["manifest_digest"]
    assert inventory == {"artifact_count": 1, "artifact_size_bytes": 3}

    artifact_path.write_bytes(b'{"tampered":true}\n')
    with pytest.raises(
        HuangpuNativeExecutionAuditError,
        match="test seal artifact bytes differ",
    ):
        _audit_seal_root(
            seal_root,
            manifest_name="seal-manifest.json",
            label="test seal",
        )


def test_native_execution_audit_refuses_to_replace_existing_report(
    tmp_path: Path,
) -> None:
    report = tmp_path / "audit.json"
    existing = b"existing evidence\n"
    report.write_bytes(existing)
    with pytest.raises(
        HuangpuNativeExecutionAuditError,
        match="audit report must be fresh",
    ):
        _write_new(report, {"status": "replacement"})
    assert report.read_bytes() == existing


@pytest.mark.parametrize(
    ("original", "replacement", "failure"),
    (
        (
            'name="launch.huangpu-research"',
            'name="launch_huangpu_research"',
            "no model matching launch site",
        ),
        (
            "<size>1100.0 1100.0</size>",
            "<size>1000 1000</size>",
            "ground plane does not cover",
        ),
    ),
)
def test_native_city_gazebo_contract_rejects_physical_scene_drift(
    tmp_path: Path,
    original: str,
    replacement: str,
    failure: str,
) -> None:
    bundle = BUNDLE_PATH
    world = WorldPackage.model_validate_json(
        (bundle / "world/package.json").read_bytes()
    )
    scene = next(
        item
        for item in world.assets
        if item.artifact.artifact_id == "asset.gazebo-city"
    )
    source = bundle / scene.artifact.selector
    drifted_text = source.read_text(encoding="utf-8").replace(original, replacement)
    assert drifted_text != source.read_text(encoding="utf-8")
    drifted = tmp_path / "drifted.sdf"
    drifted.write_text(drifted_text, encoding="utf-8")
    with pytest.raises(CityNativeRegistrationError, match=failure):
        city_registration._verify_gazebo_scene_contract(
            world=world,
            resolved_selectors={scene.artifact.selector: drifted},
        )


def test_pinned_byte_size_drift_fails_before_registration(
    definition: CityNativeInputLock,
) -> None:
    payload = definition.model_dump(mode="json")
    payload["raw_osm"]["size_bytes"] += 1
    drifted = CityNativeInputLock.model_validate(payload)
    with pytest.raises(CityNativeRegistrationError, match="size mismatch"):
        assess_city_native_registration(ROOT, drifted)


def test_required_domains_cannot_omit_native_baseline(
    definition: CityNativeInputLock,
) -> None:
    payload = definition.model_dump(mode="json")
    payload["required_execution_domains"] = [
        "business",
        "flight",
        "network",
        "traffic",
        "weather-physics",
    ]
    with pytest.raises(ValueError, match="baseline execution domains"):
        CityNativeInputLock.model_validate(payload)


def test_duplicate_input_lock_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"aero-bench.city-native-input-lock/v1",'
        '"schema_version":"aero-bench.city-native-input-lock/v1"}',
        encoding="utf-8",
    )
    with pytest.raises(CityNativeRegistrationError, match="duplicate JSON object key"):
        load_city_native_input_lock(path)


def test_executable_registration_refuses_blocked_readiness(
    monkeypatch: pytest.MonkeyPatch,
    definition: CityNativeInputLock,
    readiness,
) -> None:
    monkeypatch.setattr(
        city_registration,
        "assess_city_native_registration",
        lambda root, candidate: readiness,
    )
    with pytest.raises(CityNativeRegistrationBlocked, match="provider.weather"):
        verify_city_native_registration(ROOT, definition)
