from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET

import pytest
import yaml
from jsonschema import Draft202012Validator

from aero_bench.serialization import canonical_json_bytes
from tools import build_urban_infrastructure_inspection_v1 as builder
from tools import build_agent_inspection_v1 as agent_builder


def test_private_truth_uses_the_unique_canonical_evidence_encoding(tmp_path) -> None:
    schemas = builder._schemas(tmp_path, agent_acceptance=True)
    builder._task_package(
        tmp_path,
        builder._ref(tmp_path, schemas["observation"]),
    )

    truth = (tmp_path / "private/truth.json").read_bytes()

    assert truth == canonical_json_bytes(json.loads(truth))
    assert not truth.endswith(b"\n")


def test_gazebo_navigation_altitude_matches_declared_amsl_origin(tmp_path) -> None:
    builder._materialize_world_assets(tmp_path)
    world = ET.fromstring(builder._gazebo_world())
    elevation = float(world.findtext("world/spherical_coordinates/elevation"))
    geoid = json.loads((tmp_path / "world/geoid/grid.json").read_text())
    assert elevation == 50.0 - geoid["values_m"][1][1]


def test_building_publication_is_osm_with_authoritative_coordinates() -> None:
    from aero_bench.world.frame_math import EnuTransform

    transform = EnuTransform.from_origin(longitude_deg=builder.SHANGHAI_LONGITUDE_DEG, latitude_deg=builder.SHANGHAI_LATITUDE_DEG, altitude_m=50.0)
    document = builder._osm_building(100.0, 200.0, 20.0, 40.0, 18.0)
    assert document["version"] == 0.6
    elements = document["elements"]
    assert elements[-1]["nodes"] == [-1, -2, -3, -4, -1]
    assert elements[-1]["tags"] == {"building": "yes", "height": "18.0"}
    for node, expected in zip(elements[:4], ((90.0, 180.0), (110.0, 180.0), (110.0, 220.0), (90.0, 220.0)), strict=True):
        point = transform.geodetic_to_enu(longitude_deg=node["lon"], latitude_deg=node["lat"], altitude_m=50.0)
        assert abs(point.x - expected[0]) < 0.01
        assert abs(point.y - expected[1]) < 0.01


def test_agent_acceptance_surfaces_match_private_truth_and_include_clean_target(
    tmp_path,
) -> None:
    schemas = builder._schemas(tmp_path, agent_acceptance=True)
    package, _artifacts = builder._task_package(
        tmp_path,
        builder._ref(tmp_path, schemas["observation"]),
        agent_acceptance=True,
    )

    observations = {item.target_id: item for item in package.observations}
    assert {
        target_id: observation.simulation_asset_id
        for target_id, observation in observations.items()
    } == builder.AGENT_TARGET_ASSET_BY_ID

    truth = json.loads((tmp_path / "private/truth.json").read_text())
    assert {item["target_id"]: item["defect_id"] for item in truth} == (
        builder.AGENT_DEFECT_BY_TARGET_ID
    )
    assert "target.06" not in {item["target_id"] for item in truth}
    assert all(
        not item["defect_id"].endswith(item["target_id"].rsplit(".", 1)[1])
        for item in truth
    )

    assets = {item.asset_id: item for item in package.assets}
    expected_patch_pose = {
        "target.01": "-0.046 0 0.18 0 0 0",
        "target.06": None,
        "target.08": "-0.046 0 -0.18 0 0 0",
    }
    for target_id, observation in observations.items():
        asset = assets[observation.simulation_asset_id]
        sdf = ET.parse(tmp_path / asset.file.path)
        patch = sdf.find("model/link/visual[@name='surface_damage_patch']")
        expected = expected_patch_pose[target_id]
        if expected is None:
            assert patch is None
        else:
            assert patch is not None
            assert patch.findtext("pose") == expected


def test_agent_acceptance_bounds_are_derived_from_declared_geometry(tmp_path) -> None:
    schemas = builder._schemas(tmp_path)
    package, _artifacts = builder._task_package(
        tmp_path,
        builder._ref(tmp_path, schemas["observation"]),
        agent_acceptance=True,
    )

    assert math.isclose(
        package.bounds.mission.shortest_path_m,
        1386.2763058692663,
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    assert package.bounds.link.upload_deadline_s == 540.0
    assert package.bounds.total_defects == 2
    assert package.bounds.geometrically_visible_defects == 2
    assert tuple(goal.metric_id for goal in package.goals) == tuple(
        f"inspection.formal.{component_id}"
        for component_id in builder.FORMAL_V2_COMPONENT_IDS
    )
    assert math.isclose(
        package.bounds.imaging.projected_defect_pixels,
        6.991621076552699,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert package.bounds.imaging.projected_defect_pixels > (
        package.bounds.imaging.minimum_resolvable_pixels
    )


def test_agent_instruction_requires_api_discovery_without_leaking_answers() -> None:
    instruction = builder._instruction_text(agent_acceptance=True)

    assert "query_business_work_orders" in instruction
    assert "query_business_work_order" in instruction
    assert "action_network_send" in instruction
    assert "work-order." not in instruction
    assert "target.01" not in instruction
    assert "target.06" not in instruction
    assert "target.08" not in instruction
    assert "<target two-digit suffix>" not in instruction
    assert "defect.surface-damage.upper" in instruction
    assert "defect.surface-damage.lower" in instruction
    assert "no dark patch as no detection" in instruction
    assert "Query the order status to confirm completion." not in instruction
    assert "queued the artifact" in instruction
    assert "not delivery and not work-order completion" in instruction
    assert "wait_duration" in instruction
    assert "inbound" in instruction
    assert str(builder.OPERATIONS_EAST_M) not in instruction
    assert str(builder.OPERATIONS_NORTH_M) not in instruction
    assert str(int(builder.OPERATIONS_NORTH_M)) not in instruction


def test_agent_session_budget_covers_measured_completion_remainder() -> None:
    assert builder.AGENT_MAX_TOOL_CALLS == 144
    assert builder.AGENT_MAX_MODEL_REQUESTS == 145
    assert builder.AGENT_MAX_MODEL_REQUESTS == builder.AGENT_MAX_TOOL_CALLS + 1
    assert builder.AGENT_MAX_IMAGE_OBSERVATIONS == 12
    assert builder.AGENT_MODEL_CALL_TIMEOUT_S == 180
    assert builder.AGENT_SESSION_WALL_TIMEOUT_S == 21_600
    assert builder.AGENT_RUNNER_RUNTIME_TIMEOUT_S == 22_800
    assert builder.AGENT_VERIFIER_TIMEOUT_S == 600
    assert (
        builder.AGENT_RUNNER_RUNTIME_TIMEOUT_S - builder.AGENT_SESSION_WALL_TIMEOUT_S
        == 1_200
    )


def test_agent_builder_defaults_to_a_separate_release() -> None:
    args = agent_builder.parser().parse_args(
        ["--runtime-source-revision", "a" * 64]
    )

    assert args.agent_acceptance is True
    assert args.output_root == str(builder.AGENT_ACCEPTANCE_OUTPUT)
    assert args.output_root != str(
        builder.REPO_ROOT / "releases/urban-infrastructure-inspection-v1"
    )


def test_formal_image_lock_rejects_non_runnable_registry(tmp_path) -> None:
    images = builder._pending_images(agent_acceptance=True)
    assert set(images) == {
        "harness",
        "flight",
        "network",
        "traffic",
        "business",
        "verifier",
        "agent",
        "agent_driver",
    }
    lock = tmp_path / "images.json"
    lock.write_text(json.dumps({"images": images}))

    with pytest.raises(ValueError, match="registry.invalid"):
        builder._load_images(lock, agent_acceptance=True)


def test_agent_acceptance_formal_lock_resolves_driver_and_all_artifacts(
    tmp_path,
) -> None:
    images = {
        name: f"registry.example/aero-bench/{name}@sha256:{index:064x}"
        for index, name in enumerate(
            sorted(builder._pending_images(agent_acceptance=True)), start=1
        )
    }
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": "aero-bench.runtime-image-lock/v1",
                "images": images,
            }
        )
    )
    revision = builder._managed_source_closure()[0]

    root = builder.build(
        argparse.Namespace(
            output_root=str(tmp_path / "formal"),
            runtime_source_revision=revision,
            images_lock=str(lock),
            agent_acceptance=True,
            force=False,
        )
    )

    assert (root / "suite.yaml").is_file()
    resolved = json.loads((root / "resolved-run.json").read_text())
    assert len(resolved["artifact_requirements"]) == 15
    assert sum(
        item["max_size_bytes"] for item in resolved["artifact_requirements"]
    ) == 1_664_151_552
    assert {
        item["artifact_type"] for item in resolved["artifact_requirements"]
    } >= {"model.session-manifest", "model.interactions"}
    source_lock = json.loads((root / "source-lock.json").read_text())
    assert source_lock["status"] == "formal_digest_pinned"
    assert source_lock["resolved_run_id"] == resolved["run_id"]
    assert source_lock["participant_source"] == {
        "source_revision": revision,
        "source_uri": builder.SOURCE_URI,
        "version": "0.4.0-agent-bridge.1",
    }
    emitted_image_lock = json.loads((root / "image-lock.json").read_text())
    assert emitted_image_lock["images"] == images
    assert emitted_image_lock["agent"] == source_lock["participant_source"]
    runner = yaml.safe_load((root / "runner.local.yaml").read_text())
    assert runner["runtime_timeout_seconds"] == builder.AGENT_RUNNER_RUNTIME_TIMEOUT_S
    assert runner["verifier_timeout_seconds"] == builder.AGENT_VERIFIER_TIMEOUT_S
    assert runner["readiness_timeout_seconds"] == 480


def test_public_agent_artifact_schemas_bind_detections_to_real_frames(
    tmp_path,
) -> None:
    path = builder._agent_artifact_schemas(tmp_path)
    document = json.loads(path.read_text())

    assert document["schema_version"] == "aero-bench.agent-artifact-schemas/v1"
    schemas = document["artifact_schemas"]
    assert set(schemas) == {"artifact.detections", "artifact.report"}
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        assert schema["type"] == "array"

    detection = schemas["artifact.detections"]["items"]
    assert set(detection["required"]) == {
        "schema_version",
        "source_artifact_id",
        "run_id",
        "work_order_id",
        "observation_id",
        "frame_id",
        "image_sha256",
        "defect_id",
        "target_id",
    }
    assert detection["properties"]["schema_version"]["const"] == (
        "aero-bench.inspection-detection/v1"
    )
    assert schemas["artifact.report"]["items"]["properties"]["schema_version"][
        "const"
    ] == "aero-bench.inspection-report/v2"
    report_detection = schemas["artifact.report"]["items"]["properties"][
        "detections"
    ]["items"]
    assert set(report_detection["required"]) == {
        "defect_id",
        "target_id",
        "frame_id",
        "image_sha256",
    }
    encoded = path.read_text()
    assert "work-order.01" not in encoded
    assert "target.01" not in encoded
    assert "defect.surface-damage" not in encoded


def test_agent_acceptance_source_bundle_resolves_with_all_limited_apis(
    tmp_path,
) -> None:
    output = tmp_path / "agent-inspection-v1"
    root = builder.build(
        argparse.Namespace(
            output_root=str(output),
            runtime_source_revision=builder._managed_source_closure()[0],
            images_lock=None,
            agent_acceptance=True,
            force=False,
        )
    )

    assert root == output
    assert not (root / "suite.yaml").exists()
    assert not (root / "resolved-run.json").exists()
    source_lock = json.loads((root / "source-lock.json").read_text())
    assert source_lock["build_profile"] == builder.AGENT_ACCEPTANCE_PROFILE
    assert source_lock["task_id"] == builder.AGENT_TASK_ID
    assert source_lock["required_target_ids"] == list(builder.REQUIRED_TARGET_IDS)
    assert source_lock["formal_component_ids"] == list(
        builder.FORMAL_V2_COMPONENT_IDS
    )

    task = yaml.safe_load((root / "templates/task/task.yaml").read_text())
    agent = yaml.safe_load((root / "templates/agent/participant.yaml").read_text())
    environment = yaml.safe_load(
        (root / "templates/environment/environment.yaml").read_text()
    )
    assert task["task_id"] == builder.AGENT_TASK_ID
    assert len(task["goals"]) == 15
    package = json.loads((root / "task/inspection-package.json").read_text())
    assert package["schema_version"] == "aero-bench.inspection-task/v4"
    assert {
        item["query_type"] for item in agent["queries"]
    } == {"business.work-order", "business.work-orders"}
    assert {
        item["observation_id"] for item in agent["observations"]
    } >= {
        "flight.gnss.uav.inspector",
        "flight.telemetry.uav.inspector",
        "observation.01",
        "observation.06",
        "observation.08",
    }
    task_asset_ids = {item["asset_id"] for item in task["assets"]}
    assert task_asset_ids >= {
        "asset.agent-artifact-schemas",
        "asset.inspection-panel-clean",
    }
    assert "asset.agent-driver-config" not in task_asset_ids
    assert agent["workload"]["implementation"] == {
        "kind": "production",
        "component_id": builder.AGENT_ID,
        "source_uri": builder.SOURCE_URI,
        "source_revision": builder._managed_source_closure()[0],
        "version": "0.4.0-agent-bridge.1",
    }
    assert agent["workload"]["runtime"]["command"] == ["bridge", "run"]
    driver = agent["driver"]
    assert driver["driver_id"] == builder.AGENT_DRIVER_ID
    assert driver["bridge_port"] == builder.AGENT_DRIVER_BRIDGE_PORT
    assert driver["workload"]["implementation"]["component_id"] == (
        builder.AGENT_DRIVER_ID
    )
    assert driver["workload"]["implementation"]["version"] == (
        "0.4.0-astra-driver.1"
    )
    assert driver["workload"]["runtime"]["command"] == ["driver", "run"]
    assert {
        (
            item["artifact_id"],
            item["artifact_type"],
            item["producer_id"],
            item["visibility"],
            item["relative_path"],
            item["max_size_bytes"],
        )
        for item in driver["artifact_requirements"]
    } == {
        (
            "artifact.model-session-manifest",
            "model.session-manifest",
            builder.AGENT_DRIVER_ID,
            "private",
            "model.session-manifest.json",
            1024 * 1024,
        ),
        (
            "artifact.model-interactions",
            "model.interactions",
            builder.AGENT_DRIVER_ID,
            "private",
            "model.interactions.jsonl",
            64 * 1024 * 1024,
        ),
    }
    package_evidence = {
        item["evidence_kind"]: item for item in package["artifact_requirements"]
    }
    assert package_evidence["model_session_manifest"] == next(
        item
        for item in driver["artifact_requirements"]
        if item["artifact_type"] == "model.session-manifest"
    ) | {"evidence_kind": "model_session_manifest"}
    assert package_evidence["model_interactions"] == next(
        item
        for item in driver["artifact_requirements"]
        if item["artifact_type"] == "model.interactions"
    ) | {"evidence_kind": "model_interactions"}
    flight = next(
        provider
        for provider in environment["providers"]
        if provider["provider_id"] == builder.FLIGHT_ID
    )
    assert {"flight.gnss", "flight.telemetry"} <= set(flight["capabilities"])
    assert {
        (
            item["artifact_id"],
            item["artifact_type"],
            item["relative_path"],
        )
        for item in flight["artifact_requirements"]
    } >= {
        (
            "artifact.camera-frame-data",
            "camera-frame-data",
            "flight/camera-frames.bin",
        )
    }
    assert all(
        item["workload"]["runtime"]["image"].startswith("registry.invalid/")
        for item in environment["providers"]
    )
    observation_schema = json.loads((root / "schemas/observation.json").read_text())
    assert observation_schema["properties"]["schema_version"]["const"] == (
        "aero-bench.observation.rgb/v1"
    )
    assert {"frame_id", "image_sha256", "image_base64"} <= set(
        observation_schema["required"]
    )
    assert "external_renderer" not in json.dumps(observation_schema)
    assert "reserved" not in json.dumps(observation_schema)
    order_schema = json.loads(
        (root / "schemas/business-work-order-result.json").read_text()
    )
    assert {
        "work_order_id",
        "target_id",
        "upload_deadline_ns",
        "longitude_deg",
        "latitude_deg",
        "east_m",
        "north_m",
        "up_m",
        "completion_authority",
    } <= set(order_schema["required"])
    assert not any(
        "truth" in name or "defect" in name
        for name in order_schema["properties"]
    )
    driver_config = json.loads((root / "configs/agent-driver.json").read_text())
    assert driver_config == {
        "schema_version": "aero-bench.agent-driver-config/v1",
        "policy": {
            "schema_version": "aero-bench.agent-session-policy/v1",
            "model": "gpt-6-astra",
            "reasoning_effort": "low",
            "max_model_requests": 145,
            "max_tool_calls": 144,
            "max_image_observations": 12,
            "model_call_timeout_s": 180,
            "session_wall_timeout_s": 21_600,
            "max_output_tokens": 8_192,
        },
        "initial_input": builder.AGENT_INITIAL_INPUT,
        "codex_cli_version": builder.CODEX_CLI_VERSION,
        "codex_binary_sha256": builder.CODEX_BINARY_SHA256,
    }
    driver_schema = json.loads(
        (root / "schemas/agent-driver-config.json").read_text()
    )
    Draft202012Validator.check_schema(driver_schema)
    Draft202012Validator(driver_schema).validate(driver_config)
    px4 = json.loads((root / "configs/px4.json").read_text())
    assert px4["maximum_agent_decision_wall_time_ms"] == (
        builder.AGENT_SESSION_WALL_TIMEOUT_S * 1000
    )
    assert px4["heartbeat_timeout_fixed_margin_ms"] == 30_000
