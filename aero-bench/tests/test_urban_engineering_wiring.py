"""Small engineering wiring tests; these fixtures are not simulator evidence."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
import yaml

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import AgentSpec, ClockSpec, TaskSpec
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.providers.px4_gazebo.provider import Px4GazeboProvider
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.tasks.urban_recovery_demo.integration import _validate_participant_inputs
from aero_bench.tasks.urban_recovery_demo.verifier import UrbanRecoveryVerifierConfig
from aero_bench.world.resolved import (
    ResolvedEntity,
    ResolvedProvider,
    ResolvedScenario,
    ResolvedTaskBinding,
)
from tools.build_urban_recovery_demo import (
    _demo_package,
    _derive_engineering_ground_scene,
    _pose,
    _provider_configs,
    _region,
    _runtime_artifacts,
    _schemas,
    _task_environment,
    parser,
    write_json,
)
from tools.run_urban_recovery_ablation import markdown_result_report, recorded_result


@pytest.mark.parametrize("variant", ["baseline", "direct-recovery", "no-recovery"])
@pytest.mark.parametrize("mission_mode", ["recovery", "inspection"])
def test_short_engineering_bundle_has_supported_artifacts_and_bound_policy(tmp_path, variant, mission_mode):
    source = tmp_path / "unit-only-source.json"
    source.write_text('{"unit_only":true}\n')
    package = _demo_package(tmp_path, source, execution_profile="engineering", recovery_variant=variant)
    schemas = _schemas(tmp_path)
    configs = {key: write_json(tmp_path, f"configs/{key}.json", {"unit_only": True}) for key in ("px4", "ns3", "sumo")}
    names = ("groundstation.rule", "uav.policy.01", "uav.policy.02", "flight", "network", "traffic", "harness", "verifier")
    images = {name: f"unit.test/{name}@sha256:{index:064x}" for index, name in enumerate(names, 1)}
    task_path, environment_path, agent_paths = _task_environment(
        root=tmp_path, package=package, schemas=schemas, configs=configs, images=images, revision="a" * 40, mission_mode=mission_mode,
    )
    task = TaskSpec.model_validate(yaml.safe_load(task_path.read_text()))
    agents = tuple(AgentSpec.model_validate(yaml.safe_load(path.read_text())) for path in agent_paths)
    _validate_participant_inputs(reader=BundleReader(tmp_path), task=task, package=package, agents=agents)
    environment = yaml.safe_load(environment_path.read_text())
    assert environment["harness"]["resources"]["memory_mib"] == (32768 if mission_mode == "inspection" else 8192)
    assert task.verifier.workload.resources.memory_mib == (32768 if mission_mode == "inspection" else 4096)
    assert environment["clock"]["max_steps"] == package.final_tick == 600
    assert package.duration_ns == 120_000_000_000
    assert len(task.assets) == 3  # no unproduced deep-audit inputs
    assert {item.artifact_type for item in task.verifier.artifact_requirements} == {
        "event.log", "scene.state-history", "trajectory", "observation",
        "sensor-frame", "camera-frame-data", "network.delivery",
        "sumo.traffic.evidence",
    }
    flight = next(item for item in environment["providers"] if item["provider_id"] == "flight")
    flight_artifacts = {
        item["artifact_type"]: item for item in flight["artifact_requirements"]
    }
    assert set(flight_artifacts) == {
        "trajectory", "observation", "sensor-frame", "camera-frame-data"
    }
    assert {
        artifact_type: (
            item["visibility"], item["relative_path"], item["max_size_bytes"]
        )
        for artifact_type, item in flight_artifacts.items()
        if artifact_type != "trajectory"
    } == {
        "observation": ("private", "flight/observations.json", 32 * 1024 * 1024),
        "sensor-frame": ("public", "flight/sensor-frames.json", 32 * 1024 * 1024),
        "camera-frame-data": (
            "public", "flight/camera-frame-data.bin", 256 * 1024 * 1024
        ),
    }
    assert sum(
        item["max_size_bytes"] for item in _runtime_artifacts("formal").values()
    ) <= 8 * 1024 * 1024 * 1024
    verifier = UrbanRecoveryVerifierConfig.model_validate(json.loads((tmp_path / "configs/verifier.json").read_bytes()))
    assert verifier.execution_profile == "engineering" and verifier.final_tick == 600


def test_generated_urban_flight_inventory_serializes_through_provider(tmp_path):
    source = tmp_path / "unit-only-source.json"
    source.write_text('{"unit_only":true}\n')
    package = _demo_package(tmp_path, source, execution_profile="engineering")
    schemas = _schemas(tmp_path)
    world = SimpleNamespace(
        regions=(
            _region(
                "region.no-fly.recovery",
                "no_fly",
                [[-310.0, -340.0], [-290.0, -340.0], [-290.0, -320.0]],
                10.0,
                160.0,
            ),
        ),
        entities=(
            SimpleNamespace(entity_id="uav.01", pose=_pose(-320.0, -350.0, 0.15)),
            SimpleNamespace(entity_id="uav.02", pose=_pose(300.0, -350.0, 0.15)),
        ),
        world_digest="d" * 64,
    )
    configs = _provider_configs(tmp_path, world, "a" * 40)
    names = (
        "groundstation.rule", "uav.policy.01", "uav.policy.02", "flight",
        "network", "traffic", "harness", "verifier",
    )
    images = {
        name: f"unit.test/{name}@sha256:{index:064x}"
        for index, name in enumerate(names, 1)
    }
    _task_path, environment_path, agent_paths = _task_environment(
        root=tmp_path,
        package=package,
        schemas=schemas,
        configs=configs,
        images=images,
        revision="a" * 40,
    )
    environment = yaml.safe_load(environment_path.read_text())
    flight = next(
        item for item in environment["providers"] if item["provider_id"] == "flight"
    )
    manifest = ProviderManifest.model_validate(
        {
            "provider_id": flight["provider_id"],
            "adapter": flight["adapter"],
            "implementation": flight["workload"]["implementation"],
            "runtime_image": flight["workload"]["runtime"]["image"],
            "config_digest": flight["config"]["file"]["sha256"],
            "capabilities": flight["capabilities"],
            "protocol_schema": flight["protocol_schema"],
            "artifact_requirements": flight["artifact_requirements"],
        }
    )
    agent_specs = tuple(
        AgentSpec.model_validate(yaml.safe_load(path.read_text()))
        for path in agent_paths
    )
    urban_observations = tuple(
        SimpleNamespace(endpoint_id=observation.provider_id, inspection=None, urban=object(), flight=None)
        for agent in agent_specs
        for observation in agent.observations
        if observation.provider_id == "flight"
    )
    scenario = ResolvedScenario.model_construct(
        scenario_digest="e" * 64,
        providers=(ResolvedProvider.model_construct(provider_id="flight"),),
        entities=tuple(
            ResolvedEntity.model_construct(
                entity_id=vehicle_id,
                kind="uav",
                owner_kind="provider",
                owner_id="flight",
            )
            for vehicle_id in ("uav.01", "uav.02")
        ),
        task=ResolvedTaskBinding.model_construct(
            package_id=package.package_id,
            observations=urban_observations,
        ),
    )
    flight_config = Px4GazeboConfig.model_validate_json(configs["px4"].read_bytes())
    assert {
        (vehicle.model, vehicle.gazebo_resource)
        for vehicle in flight_config.vehicles
    } == {("gz_x500", "x500")}
    provider = Px4GazeboProvider(
        config=flight_config,
        manifest=manifest,
        runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=17601),
        run_id="f" * 64,
        session_token="1" * 64,
        scenario=scenario,
        clock=ClockSpec.model_validate(environment["clock"]),
    )

    payload = json.loads(json.dumps(provider._config_payload()))

    assert payload["artifact_requirements"] == flight["artifact_requirements"]
    assert {
        item["artifact_type"] for item in payload["artifact_requirements"]
    } == {"trajectory", "observation", "sensor-frame", "camera-frame-data"}


def test_builder_cli_defaults_to_engineering():
    args = parser().parse_args(["--geoid", "g", "--terrain", "t", "--sumo-dir", "s", "--images-lock", "i"])
    assert args.profile == "engineering" and args.variant == "baseline"


def test_engineering_ground_preserves_scene_and_requires_flat_models(tmp_path):
    source = tmp_path / "source.sdf"
    source.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<sdf version="1.10">
  <world name="urban_scene">
    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>31.2304</latitude_deg>
      <longitude_deg>121.4737</longitude_deg>
      <elevation>20</elevation>
      <heading_deg>0</heading_deg>
    </spherical_coordinates>
    <model name="building_way_1_component_0">
      <static>true</static>
      <link name="source_footprint">
        <collision name="source_footprint_collision">
          <geometry><polyline><height>12</height><point>0 0</point><point>1 0</point><point>1 1</point></polyline></geometry>
        </collision>
      </link>
    </model>
  </world>
</sdf>
""",
        encoding="utf-8",
    )
    terrain = write_json(
        tmp_path,
        "terrain.json",
        {
            "schema_version": "aero-bench.scalar-grid/v1",
            "frame_id": "ENU",
            "east_axis_m": [-500.0, 0.0, 500.0],
            "north_axis_m": [-500.0, 0.0, 500.0],
            "values_m": [[20.0] * 3 for _ in range(3)],
        },
    )
    geoid = write_json(
        tmp_path,
        "geoid.json",
        {
            "schema_version": "aero-bench.scalar-grid/v1",
            "frame_id": "ENU",
            "east_axis_m": [-500.0, 0.0, 500.0],
            "north_axis_m": [-500.0, 0.0, 500.0],
            "values_m": [[30.0] * 3 for _ in range(3)],
        },
    )
    manifest = {
        "crop": {
            "enu_bounds_m": {
                "min_east_m": -500.0,
                "max_east_m": 500.0,
                "min_north_m": -500.0,
                "max_north_m": 500.0,
            }
        },
        "origin": {
            "latitude_deg": 31.2304,
            "longitude_deg": 121.4737,
            "ellipsoid_height_m": 50.0,
            "amsl_m": 20.0,
        },
        "selection": {"building_count": 1},
    }
    destination = tmp_path / "generated.sdf"
    provenance = _derive_engineering_ground_scene(
        source=source,
        destination=destination,
        terrain=terrain,
        geoid=geoid,
        scene_manifest=manifest,
        physics_step_ns=4_000_000,
    )

    source_world = next(iter(ET.parse(source).getroot()))
    generated_world = next(iter(ET.parse(destination).getroot()))
    source_model = source_world.findall("model")[0]
    generated_models = generated_world.findall("model")
    source_model.tail = None
    generated_models[0].tail = None
    assert ET.tostring(generated_models[0]) == ET.tostring(source_model)
    ground = generated_models[1]
    assert ground.attrib == {"name": "ground_plane"}
    assert ground.findtext("pose") == "0 0 0 0 0 0"
    assert ground.findtext("link/collision/geometry/plane/normal") == "0 0 1"
    assert ground.findtext("link/collision/geometry/plane/size") == "1000 1000"
    physics = generated_world.find("physics")
    assert physics is not None
    assert physics.attrib == {"name": "deterministic_4ms", "type": "ignored"}
    assert physics.findtext("max_step_size") == "0.004"
    assert physics.findtext("real_time_factor") == "0"
    assert provenance["source_scene"]["sha256"] != provenance["generated_scene"]["sha256"]
    assert provenance["physics"] == {
        "declaration": "generated",
        "name": "deterministic_4ms",
        "type": "ignored",
        "physics_step_ns": 4_000_000,
        "max_step_size_s": "0.004",
        "real_time_factor": 0.0,
    }
    assert provenance["ground_plane"]["elevation_enu_up_m"] == 0.0
    assert provenance["authored_flat_simulation_assumption"] == {
        "declared": True,
        "measured_terrain": False,
        "statement": (
            "Engineering-only physical ground compiled from the supplied constant "
            "terrain and geoid models; it is not measured Shanghai terrain."
        ),
    }

    conflicting_source = tmp_path / "conflicting.sdf"
    conflicting_source.write_text(
        source.read_text(encoding="utf-8").replace(
            "  </world>",
            "    <physics name=\"conflicting\" type=\"ignored\">"
            "<max_step_size>0.002</max_step_size></physics>\n  </world>",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conflicts with PHYSICS_STEP_NS"):
        _derive_engineering_ground_scene(
            source=conflicting_source,
            destination=tmp_path / "conflicting-generated.sdf",
            terrain=terrain,
            geoid=geoid,
            scene_manifest=manifest,
            physics_step_ns=4_000_000,
        )

    terrain_document = json.loads(terrain.read_text())
    terrain_document["values_m"][1][1] = 20.1
    write_json(tmp_path, "terrain.json", terrain_document)
    with pytest.raises(ValueError, match="not flat"):
        _derive_engineering_ground_scene(
            source=source,
            destination=tmp_path / "rejected.sdf",
            terrain=terrain,
            geoid=geoid,
            scene_manifest=manifest,
            physics_step_ns=4_000_000,
        )


def test_empty_recorded_result_does_not_invent_final_states_or_mission_success():
    trace = {"scene_states": [], "trajectories": [], "events": [], "network_events": [],
             "time": {"tick": 0, "sim_time_ns": 0}, "terminal": {"kind": "aborted"}, "provider_status": []}
    result = recorded_result(trace)
    assert result["final_uav_states"] == []
    assert result["sampled_uav_distance_m"] == {}
    assert result["mission_verdict"] == "not_scored"


@pytest.mark.parametrize("recorded, ground", [(False, False), (False, True), (True, True)])
def test_markdown_comparison_distinguishes_missing_data_from_recorded_zero(recorded, ground):
    summary = {
        "recorded_time": {"sim_time_ns": 200_000_000},
        "sampled_uav_distance_m": {"uav.01": 0.0, "uav.02": 1.25},
        "provider_status": [{"provider_id": "flight", "state": "stopped"}],
        "last_agent_decisions": {"uav.policy.01": {"recorded_summary": "unit-only"}},
        "network_event_counts": {"delivered": 2},
    } if recorded else None
    text = markdown_result_report({
        "browser_acceptance": "not_run", "formal_acceptance": "not_run",
        "results": [{"variant": "baseline", "status": "unit-only", "duration_ns": 30_000_000_000,
                     "recorded_result": summary, "replay_manifest": None, "replay_status": "not_published",
                     "engineering_ground": {"unit_only": True} if ground else None,
                     "configured_components": ["flight", "uav.policy.01"]}],
    })
    assert "not a formal benchmark pass" in text
    assert "Browser acceptance: not_run" in text
    assert ("not measured Shanghai terrain" in text) == ground
    if recorded:
        assert "30.0 / 0.2 | 0.00 | 1.25" in text
        assert "flight=stopped" in text and '"delivered": 2' in text
    else:
        assert "30.0 / not recorded | not recorded | not recorded" in text
        assert "Recorded Provider states: not recorded" in text
