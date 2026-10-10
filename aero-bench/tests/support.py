from __future__ import annotations


import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from aero_bench.config.models import AgentSpec, Identifier, StrictModel, TaskSpec
from aero_bench.providers.registry import ProviderRegistration, builtin_provider_registry
from aero_bench.config.resolver import ExecutorKind, ResolvedRunSpec, resolve_suite
from aero_bench.runtime.contracts import (
    FinalizedArtifact,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection import InspectionTaskPackage
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.contracts import ProviderRequirement
from aero_bench.world.resolved import (
    ResolvedTaskScenarioProjection,
    _resolved_task_binding,
    scenario_digest_value,
)
from tests.world.support import materialize_world_package


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_finalization_receipt(
    manifest,
    request: ProviderFinalizationRequest,
) -> ProviderFinalizationReceipt:
    return ProviderFinalizationReceipt(
        schema_version="aero-bench.provider-finalization-receipt/v1",
        run_id=request.run_id,
        provider_id=manifest.provider_id,
        event_chain_root=request.event_chain_root,
        artifacts=tuple(
            FinalizedArtifact(
                artifact_id=requirement.artifact_id,
                sha256=hashlib.sha256(
                    f"{manifest.provider_id}:{requirement.artifact_id}".encode()
                ).hexdigest(),
                size_bytes=1,
            )
            for requirement in manifest.artifact_requirements
        ),
    )


def write_text(root: Path, relative_path: str, content: str) -> dict[str, str]:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return {"path": relative_path, "sha256": digest(destination)}


def runtime(
    digest_character: str,
    component_id: str,
    *,
    cpu_millicores: int = 250,
) -> dict[str, object]:
    return {
        "runtime": {
            "image": f"registry.invalid/aero@sha256:{digest_character * 64}",
            "command": ["/opt/aero/entrypoint"],
        },
        "resources": {
            "cpu_millicores": cpu_millicores,
            "memory_mib": 256,
            "gpu_count": 0,
        },
        "implementation": {
            "component_id": component_id,
            "kind": "mechanical_fixture",
            "source_uri": "https://example.invalid/aero-bench/mechanical-fixture",
            "source_revision": digest_character * 40,
            "version": "test-fixture-1",
        },
    }


def artifact_requirement(
    artifact_type: str,
    producer_id: str,
    visibility: str,
    *,
    artifact_id: str,
    relative_path: str,
    max_size_bytes: int,
    source_asset_id: str | None = None,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_id": producer_id,
        "visibility": visibility,
        "relative_path": relative_path,
        "max_size_bytes": max_size_bytes,
        "source_asset_id": source_asset_id,
    }


def resolve_bundle(suite_path: str | Path, *, executor_kind: ExecutorKind):
    return resolve_suite(
        str(suite_path),
        executor_kind=executor_kind,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )


class FixtureProviderConfig(StrictModel):
    provider_id: Identifier
    mode: str


def fixture_provider_registry(*, stage_overrides=None):
    """Explicit adapter registrations for the existing mechanical module fixtures."""
    registry = builtin_provider_registry()

    def unused_builder(*args, **kwargs):
        raise AssertionError("this fixture only projects registered runtime stages")

    stages = dict((
        ("fixture.flight-artifact-writer", "motion"),
        ("fixture.traffic-provider", "motion"),
        ("fixture.network-artifact-writer", "network"),
        ("fixture.business-artifact-writer", "business_environment"),
        ("fixture.observation-artifact-writer", "business_environment"),
        ("fixture.world-provider", "business_environment"),
    ))
    if stage_overrides is not None:
        if set(stage_overrides) - set(stages):
            raise ValueError("stage overrides name unregistered fixture adapters")
        stages.update(stage_overrides)
    for adapter, stage in stages.items():
        registry.register(ProviderRegistration(
            adapter=adapter,
            runtime_stage=stage,
            config_model=FixtureProviderConfig,
            session_builder=unused_builder,
        ))
    return registry


def as_formal_run(run: ResolvedRunSpec) -> ResolvedRunSpec:
    payload = run.model_dump(mode="json", exclude={"run_id"})
    payload["execution_scope"] = "formal_benchmark"
    implementations = [payload["environment"]["harness"]["implementation"]]
    for provider in payload["environment"]["providers"]:
        implementations.append(provider["workload"]["implementation"])
    for agent in payload["agents"]:
        implementations.append(agent["workload"]["implementation"])
    implementations.append(payload["task"]["verifier"]["workload"]["implementation"])
    for implementation in implementations:
        implementation["kind"] = "production"
        implementation["source_uri"] = "https://github.com/moby/moby"
    return _rebuild_run_task_and_agents(
        run,
        task=TaskSpec.model_validate(payload["task"]),
        agents=tuple(AgentSpec.model_validate(agent) for agent in payload["agents"]),
        payload=payload,
    )


def rebuild_run_agents(
    run: ResolvedRunSpec,
    agents: tuple[AgentSpec, ...],
) -> ResolvedRunSpec:
    """Rebind a test run after deliberately changing its Agent contracts."""

    return _rebuild_run_task_and_agents(run, task=run.task, agents=agents)


def with_fixture_provider_stages(run: ResolvedRunSpec) -> ResolvedRunSpec:
    """Validate the fixture's serialized stages against explicit registrations."""
    fixture_provider_registry().verify_runtime_stage_binding(
        environment_providers=run.environment.providers,
        resolved_providers=run.scenario.providers,
    )
    return run


def _rebuild_run_task_and_agents(
    run: ResolvedRunSpec,
    *,
    task: TaskSpec,
    agents: tuple[AgentSpec, ...],
    payload: dict[str, object] | None = None,
) -> ResolvedRunSpec:
    """Rebuild the scenario authority bound to a test Task and its Agents."""

    inspection_by_id = {
        binding.inspection.observation_id: binding.inspection
        for binding in run.scenario.task.observations
        if binding.inspection is not None
    }
    urban_by_id = {
        binding.urban.observation_id: binding.urban
        for binding in run.scenario.task.observations
        if binding.urban is not None
    }
    flight_by_id = {
        binding.flight.observation_id: binding.flight
        for binding in run.scenario.task.observations
        if binding.flight is not None
    }
    physical_capabilities = {
        capability
        for provider in run.scenario.providers
        for capability in provider.capability_ids
    }
    task_projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=run.scenario.task.logical_endpoint_ids,
        logical_capability_ids=tuple(
            sorted(set(task.required_capabilities) - physical_capabilities)
        ),
        observations=tuple(inspection_by_id[key] for key in sorted(inspection_by_id)),
        urban_observations=tuple(urban_by_id[key] for key in sorted(urban_by_id)),
        flight_observations=tuple(flight_by_id[key] for key in sorted(flight_by_id)),
    )
    task_binding = _resolved_task_binding(
        task=task,
        agents=agents,
        providers=run.scenario.providers,
        task_projection=task_projection,
    )
    scenario = run.scenario.model_copy(update={"task": task_binding})
    scenario = scenario.model_copy(
        update={"scenario_digest": scenario_digest_value(scenario)}
    )
    resolved_payload = (
        run.model_dump(mode="json", exclude={"run_id"})
        if payload is None
        else dict(payload)
    )
    resolved_payload["task"] = task.model_dump(mode="json")
    resolved_payload["agents"] = [agent.model_dump(mode="json") for agent in agents]
    resolved_payload["scenario"] = scenario.model_dump(mode="json")
    run_id = hashlib.sha256(canonical_json_bytes(resolved_payload)).hexdigest()
    return ResolvedRunSpec.model_validate({"run_id": run_id, **resolved_payload})


def promote_bundle_to_formal(bundle: "BundlePaths") -> None:
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    environment_path = bundle.root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))

    implementations = [
        task["verifier"]["workload"]["implementation"],
        environment["harness"]["implementation"],
        *(
            provider["workload"]["implementation"]
            for provider in environment["providers"]
        ),
        agent["workload"]["implementation"],
    ]
    for implementation in implementations:
        implementation["kind"] = "production"
        implementation["source_uri"] = "https://github.com/moby/moby"

    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False), encoding="utf-8")

    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["execution_scope"] = "formal_benchmark"
    case = suite["cases"][0]
    case["task"]["sha256"] = digest(bundle.task)
    case["environment"]["sha256"] = digest(environment_path)
    case["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class BundlePaths:
    root: Path
    suite: Path
    task: Path


def inspection_world_kwargs(kwargs: dict[str, object], _root: Path) -> None:
    kwargs["provider_requirements"] = (
        ProviderRequirement(
            provider_id="business",
            roles=("mission",),
            required_capability_ids=("business.work-order",),
        ),
        ProviderRequirement(
            provider_id="flight",
            roles=("motion",),
            required_capability_ids=(
                "flight.command",
                "gazebo.frames",
                "gazebo.physics",
            ),
        ),
        ProviderRequirement(
            provider_id="network",
            roles=("wireless_network",),
            required_capability_ids=("network.delivery", "wifi.802.11ax"),
        ),
        ProviderRequirement(
            provider_id="observation",
            roles=("sensor",),
            required_capability_ids=("camera.rgb", "observation.capture"),
        ),
        ProviderRequirement(
            provider_id="traffic",
            roles=("traffic",),
            required_capability_ids=("sumo.frames", "sumo.traffic"),
        ),
        ProviderRequirement(
            provider_id="world",
            roles=("static_scene",),
            required_capability_ids=("scene.geometry",),
        ),
    )
    kwargs["sensors"] = tuple(
        sensor.model_copy(
            update={
                "provider_id": "observation",
                "horizontal_fov_deg": 120.0,
                "vertical_fov_deg": 120.0,
            }
        )
        for sensor in kwargs["sensors"]
    )
    kwargs["semantic_targets"] = tuple(
        target.model_copy(
            update={
                "target_id": "asset.1",
                "min_distance_m": 1.0,
                "max_distance_m": 10.0,
                "max_view_angle_deg": 45.0,
            }
        )
        for target in kwargs["semantic_targets"]
    )
    kwargs["mission_requirements"] = tuple(
        requirement.model_copy(
            update={"target_id": "asset.1"} if requirement.kind == "observation" else {}
        )
        for requirement in kwargs["mission_requirements"]
    )
    network = kwargs["network"]
    kwargs["network"] = network.model_copy(
        update={
            "provider_id": "network",
            "radio_profiles": tuple(
                profile.model_copy(update={"provider_id": "network"})
                for profile in network.radio_profiles
            ),
        }
    )


def build_bundle(root: Path) -> BundlePaths:
    instruction = write_text(
        root, "instructions/inspection.md", "Inspect the declared asset.\n"
    )
    world_image = write_text(
        root,
        "private/inspection-image.sdf",
        '<?xml version="1.0"?>\n<sdf version="1.9"><model name="inspection_asset"><static>true</static></model></sdf>\n',
    )
    truth = write_text(root, "private/truth.json", '{"defects":["defect-1"]}\n')
    gateway_schema = write_text(root, "schemas/gateway.json", '{"type":"object"}\n')
    provider_protocol = write_text(root, "schemas/provider.json", '{"type":"object"}\n')
    bound_values = {
        "mission.shortest_path_m": 100.0,
        "mission.max_speed_mps": 10.0,
        "mission.fixed_time_s": 1.0,
        "mission.inspection_dwell_s": 2.0,
        "mission.deadline_s": 20.0,
        "link.minimum_payload_bytes": 1_000,
        "link.max_bandwidth_bps": 1_000_000.0,
        "link.minimum_latency_s": 0.01,
        "link.upload_deadline_s": 1.0,
        "imaging.defect_size_m": 0.01,
        "imaging.standoff_distance_m": 5.0,
        "imaging.focal_length_m": 0.02,
        "imaging.pixel_pitch_m": 0.000005,
        "imaging.minimum_resolvable_pixels": 4.0,
        "total_defects": 1,
        "geometrically_visible_defects": 1,
    }
    provider_schema = write_text(
        root,
        "schemas/provider-config.json",
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "profile": {"const": "nominal"},
                    "bounds": {
                        "type": "object",
                        "properties": {
                            field: {
                                "type": (
                                    "integer"
                                    if field
                                    in {
                                        "link.minimum_payload_bytes",
                                        "total_defects",
                                        "geometrically_visible_defects",
                                    }
                                    else "number"
                                )
                            }
                            for field in bound_values
                        },
                        "required": list(bound_values),
                        "additionalProperties": False,
                    },
                },
                "required": ["profile", "bounds"],
                "additionalProperties": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    provider_config = write_text(
        root,
        "configs/provider.json",
        json.dumps(
            {"profile": "nominal", "bounds": bound_values},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    verifier_schema = write_text(
        root,
        "schemas/verifier-config.json",
        '{"type":"object","properties":{"threshold":{"type":"number"}},"required":["threshold"],"additionalProperties":false}\n',
    )
    verifier_config = write_text(root, "configs/verifier.json", '{"threshold":1}\n')
    request_schema = write_text(
        root, "schemas/goto-request.json", '{"type":"object"}\n'
    )
    response_schema = write_text(
        root, "schemas/goto-response.json", '{"type":"object"}\n'
    )
    observation_schema = write_text(
        root, "schemas/observation.json", '{"type":"object"}\n'
    )

    package_schema = write_text(
        root,
        "schemas/inspection-package.json",
        json.dumps(
            InspectionTaskPackage.model_json_schema(),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    package_artifacts = [
        (
            "artifact.business",
            "business.state",
            "business",
            "private",
            "business_state",
            "business/state.json",
            1_048_576,
            None,
        ),
        (
            "artifact.event-log",
            "event.log",
            "harness",
            "private",
            "event_log",
            "harness/event.log",
            1_048_576,
            None,
        ),
        (
            "artifact.trajectory",
            "trajectory",
            "flight",
            "public",
            "trajectory",
            "flight/trajectory.json",
            1_048_576,
            None,
        ),
        (
            "artifact.delivery",
            "network.delivery",
            "network",
            "private",
            "delivery",
            "network/delivery.json",
            1_048_576,
            None,
        ),
        (
            "artifact.observation",
            "observation.metadata",
            "observation",
            "private",
            "observation",
            "observation/metadata.json",
            1_048_576,
            None,
        ),
        (
            "artifact.sensor-frame",
            "sensor-frame",
            "observation",
            "public",
            "sensor_frame",
            "observation/sensor-frames.json",
            1_048_576,
            None,
        ),
        (
            "artifact.camera-frame-data",
            "camera-frame-data",
            "observation",
            "public",
            "camera_frame_data",
            "observation/camera-frames.bin",
            1_048_576,
            None,
        ),
        (
            "artifact.truth",
            "truth.dataset",
            "bundle",
            "private",
            "truth",
            "bundle/truth.json",
            1_048_576,
            "inspection.truth",
        ),
        (
            "artifact.detection",
            "inspection.detection",
            "reference.agent",
            "public",
            "detection",
            "agent/detections.json",
            1_048_576,
            None,
        ),
        (
            "artifact.report",
            "inspection.report",
            "reference.agent",
            "public",
            "report",
            "agent/report.json",
            1_048_576,
            None,
        ),
        (
            "artifact.bounds",
            "theoretical.bounds",
            "harness",
            "public",
            "theoretical_bounds",
            "harness/theoretical-bounds.json",
            1_048_576,
            None,
        ),
        (
            "artifact.scene-states",
            "scene.state-history",
            "harness",
            "public",
            "scene_state_history",
            "harness/scene-states.jsonl",
            1_048_576,
            None,
        ),
    ]
    package_raw = {
        "schema_version": "aero-bench.inspection-task/v4",
        "task_id": "inspection.basic",
        "verifier_id": "inspection.verifier",
        "network_delivery_required": True,
        "actors": [
            {"actor_id": "reference.agent", "role": "agent"},
            {"actor_id": "observation", "role": "observation_provider"},
            {"actor_id": "business", "role": "business"},
            {"actor_id": "inspection.verifier", "role": "verifier"},
        ],
        "assets": [
            {
                "asset_id": "inspection.image",
                "file": world_image,
                "visibility": "world",
                "media_type": "application/vnd.gazebo.sdf+xml",
            },
            {
                "asset_id": "inspection.truth",
                "file": truth,
                "visibility": "verifier",
                "media_type": "application/json",
            },
        ],
        "work_orders": [
            {
                "work_order_id": "work-order.1",
                "target_id": "asset.1",
                "required_observation_id": "inspection.camera",
                "arrival_tolerance_m": 2.0,
                "report_artifact_type": "inspection.report",
                "upload_deadline_ns": 1_000_000_000,
            }
        ],
        "observations": [
            {
                "observation_id": "inspection.camera",
                "target_id": "asset.1",
                "simulation_asset_id": "inspection.image",
                "metadata_schema": observation_schema,
                "media_type": "application/json",
                "trigger": {
                    "observation_id": "inspection.camera",
                    "target_id": "asset.1",
                    "provider_id": "observation",
                    "camera_id": "camera.front",
                    "min_distance_m": 1.0,
                    "max_distance_m": 10.0,
                    "min_view_angle_deg": 0.0,
                    "max_view_angle_deg": 45.0,
                    "earliest_time_ns": 0,
                    "latest_time_ns": 2_000_000_000,
                },
            }
        ],
        "bounds": {
            "mission": {
                "shortest_path_m": 100.0,
                "max_speed_mps": 10.0,
                "fixed_time_s": 1.0,
                "inspection_dwell_s": 2.0,
                "deadline_s": 20.0,
            },
            "link": {
                "minimum_payload_bytes": 1_000,
                "max_bandwidth_bps": 1_000_000.0,
                "minimum_latency_s": 0.01,
                "upload_deadline_s": 1.0,
            },
            "imaging": {
                "defect_size_m": 0.01,
                "standoff_distance_m": 5.0,
                "focal_length_m": 0.02,
                "pixel_pitch_m": 0.000005,
                "minimum_resolvable_pixels": 4.0,
            },
            "total_defects": 1,
            "geometrically_visible_defects": 1,
        },
        "bound_sources": [
            {
                "bound_field": field,
                "provider_id": (
                    "flight"
                    if field.startswith("mission.")
                    else "network"
                    if field.startswith("link.")
                    else "observation"
                ),
                "config_pointer": f"/bounds/{field}",
            }
            for field in bound_values
        ],
        "artifact_requirements": [
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "producer_id": producer_id,
                "visibility": visibility,
                "relative_path": relative_path,
                "max_size_bytes": max_size_bytes,
                "source_asset_id": source_asset_id,
                "evidence_kind": evidence_kind,
            }
            for artifact_id, artifact_type, producer_id, visibility, evidence_kind, relative_path, max_size_bytes, source_asset_id in package_artifacts
        ],
        "goals": [
            {
                "goal_id": "inspection.success",
                "metric_id": "inspection.success_rate",
                "operator": "ge",
                "threshold": 1.0,
                "evidence": "artifact",
                "parameters": [],
            }
        ],
    }
    package_config = write_text(
        root,
        "tasks/inspection-package.json",
        json.dumps(package_raw, sort_keys=True, separators=(",", ":")) + "\n",
    )

    verifier_requirements = [
        artifact_requirement(
            artifact_type,
            producer_id,
            visibility,
            artifact_id=artifact_id,
            relative_path=relative_path,
            max_size_bytes=max_size_bytes,
            source_asset_id=source_asset_id,
        )
        for artifact_id, artifact_type, producer_id, visibility, _, relative_path, max_size_bytes, source_asset_id in package_artifacts
        if producer_id != "bundle"
    ]
    verifier_requirements.append(
        artifact_requirement(
            "truth.dataset",
            "bundle",
            "private",
            artifact_id="artifact.truth",
            relative_path="bundle/truth.json",
            max_size_bytes=1_048_576,
            source_asset_id="inspection.truth",
        )
    )
    task_raw = {
        "schema_version": "aero-bench.task/v1",
        "task_id": "inspection.basic",
        "package": {
            "package_id": "inspection.v1",
            "config": {"file": package_config, "schema_file": package_schema},
        },
        "instruction": instruction,
        "required_capabilities": [
            "business.work-order",
            "flight.command",
            "network.delivery",
            "observation.capture",
        ],
        "required_tools": ["flight.goto"],
        "assets": [
            {
                "asset_id": "inspection.image",
                "file": world_image,
                "classification": "private",
                "audiences": [{"role": "provider", "workload_ids": ["observation"]}],
            },
            {
                "asset_id": "inspection.truth",
                "file": truth,
                "classification": "private",
                "audiences": [
                    {"role": "verifier", "workload_ids": ["inspection.verifier"]}
                ],
            },
        ],
        "goals": [
            {
                "goal_id": "inspection.success",
                "verifier_id": "inspection.verifier",
                "metric_id": "inspection.success_rate",
                "operator": "ge",
                "threshold": 1.0,
                "evidence": "artifact",
                "parameters": [],
            }
        ],
        "verifier": {
            "verifier_id": "inspection.verifier",
            "workload": runtime("2", "inspection.verifier"),
            "config": {"file": verifier_config, "schema_file": verifier_schema},
            "artifact_requirements": verifier_requirements,
            "output_artifacts": [
                artifact_requirement(
                    "verification.report",
                    "inspection.verifier",
                    "public",
                    artifact_id="artifact.verification.report",
                    relative_path="verifier/report.json",
                    max_size_bytes=1_048_576,
                )
            ],
        },
    }
    task_reference = write_text(
        root, "tasks/task.yaml", yaml.safe_dump(task_raw, sort_keys=False)
    )

    providers = [
        (
            "flight",
            "fixture.flight-artifact-writer",
            17601,
            "4",
            ("flight.command", "gazebo.frames", "gazebo.physics"),
            (
                artifact_requirement(
                    "trajectory",
                    "flight",
                    "public",
                    artifact_id="artifact.trajectory",
                    relative_path="flight/trajectory.json",
                    max_size_bytes=1_048_576,
                ),
            ),
        ),
        (
            "network",
            "fixture.network-artifact-writer",
            17602,
            "6",
            ("network.delivery", "wifi.802.11ax"),
            (
                artifact_requirement(
                    "network.delivery",
                    "network",
                    "private",
                    artifact_id="artifact.delivery",
                    relative_path="network/delivery.json",
                    max_size_bytes=1_048_576,
                ),
            ),
        ),
        (
            "business",
            "fixture.business-artifact-writer",
            17603,
            "7",
            ("business.work-order",),
            (
                artifact_requirement(
                    "business.state",
                    "business",
                    "private",
                    artifact_id="artifact.business",
                    relative_path="business/state.json",
                    max_size_bytes=1_048_576,
                ),
            ),
        ),
        (
            "observation",
            "fixture.observation-artifact-writer",
            17604,
            "8",
            ("camera.rgb", "observation.capture"),
            (
                artifact_requirement(
                    "observation.metadata",
                    "observation",
                    "private",
                    artifact_id="artifact.observation",
                    relative_path="observation/metadata.json",
                    max_size_bytes=1_048_576,
                ),
                artifact_requirement(
                    "sensor-frame",
                    "observation",
                    "public",
                    artifact_id="artifact.sensor-frame",
                    relative_path="observation/sensor-frames.json",
                    max_size_bytes=1_048_576,
                ),
                artifact_requirement(
                    "camera-frame-data",
                    "observation",
                    "public",
                    artifact_id="artifact.camera-frame-data",
                    relative_path="observation/camera-frames.bin",
                    max_size_bytes=1_048_576,
                ),
            ),
        ),
        (
            "traffic",
            "fixture.traffic-provider",
            17605,
            "b",
            ("sumo.frames", "sumo.traffic"),
            (),
        ),
        (
            "world",
            "fixture.world-provider",
            17606,
            "c",
            ("scene.geometry",),
            (),
        ),
    ]
    environment_raw = {
        "schema_version": "aero-bench.environment/v1",
        "environment_id": "inspection.reference",
        "harness": runtime("3", "aero-bench.harness", cpu_millicores=500),
        "clock": {
            "authority": "provider_barrier",
            "step_ns": 100_000_000,
            "max_steps": 100,
            "provider_timeout_ms": 30_000,
        },
        "gateway": {"protocol_schema": gateway_schema, "port": 17432},
        "providers": [
            {
                "provider_id": provider_id,
                "adapter": adapter,
                "port": port,
                "workload": runtime(
                    image_digest,
                    adapter,
                    cpu_millicores=750,
                ),
                "config": {"file": provider_config, "schema_file": provider_schema},
                "protocol_schema": provider_protocol,
                "capabilities": list(capabilities),
                "artifact_requirements": list(requirements),
            }
            for provider_id, adapter, port, image_digest, capabilities, requirements in providers
        ],
        "harness_artifact_requirements": [
            artifact_requirement(
                "event.log",
                "harness",
                "private",
                artifact_id="artifact.event-log",
                relative_path="harness/event.log",
                max_size_bytes=1_048_576,
            ),
            artifact_requirement(
                "theoretical.bounds",
                "harness",
                "public",
                artifact_id="artifact.bounds",
                relative_path="harness/theoretical-bounds.json",
                max_size_bytes=1_048_576,
            ),
            artifact_requirement(
                "scene.state-history",
                "harness",
                "public",
                artifact_id="artifact.scene-states",
                relative_path="harness/scene-states.jsonl",
                max_size_bytes=1_048_576,
            ),
        ],
    }
    environment_reference = write_text(
        root,
        "environments/environment.yaml",
        yaml.safe_dump(environment_raw, sort_keys=False),
    )

    agent_raw = {
        "schema_version": "aero-bench.agent/v2",
        "agent_id": "reference.agent",
        "workload": runtime("5", "reference.agent", cpu_millicores=500),
        "tools": [
            {
                "tool_id": "flight.goto",
                "provider_id": "flight",
                "request_schema": request_schema,
                "response_schema": response_schema,
                "timeout_ms": 30_000,
                "idempotent": False,
            }
        ],
        "queries": [],
        "observations": [
            {
                "observation_id": "inspection.camera",
                "provider_id": "observation",
                "schema_file": observation_schema,
                "timeout_ms": 30_000,
            }
        ],
        "artifact_requirements": [
            artifact_requirement(
                "inspection.detection",
                "reference.agent",
                "public",
                artifact_id="artifact.detection",
                relative_path="agent/detections.json",
                max_size_bytes=1_048_576,
            ),
            artifact_requirement(
                "inspection.report",
                "reference.agent",
                "public",
                artifact_id="artifact.report",
                relative_path="agent/report.json",
                max_size_bytes=1_048_576,
            ),
        ],
    }
    agent_reference = write_text(
        root, "agents/reference.yaml", yaml.safe_dump(agent_raw, sort_keys=False)
    )

    _, world_reference, _ = materialize_world_package(
        root,
        mutate_kwargs=inspection_world_kwargs,
    )
    suite_raw = {
        "schema_version": "aero-bench.suite/v2",
        "suite_id": "inspection.reference",
        "aggregator_id": "macro",
        "execution_scope": "executor_validation",
        "cases": [
            {
                "case_id": "inspection.basic",
                "task": task_reference,
                "environment": environment_reference,
                "agents": [agent_reference],
                "world_package": world_reference.model_dump(mode="json"),
                "launch_site_ids": ["launch.alpha"],
                "seeds": [7, 11],
                "axes": [
                    {
                        "axis_id": "clock.step",
                        "target": "/environment/clock/step_ns",
                        "values": [100_000_000, 200_000_000],
                    }
                ],
            }
        ],
    }
    suite = root / "suite.yaml"
    suite.write_text(yaml.safe_dump(suite_raw, sort_keys=False), encoding="utf-8")
    return BundlePaths(root=root, suite=suite, task=root / "tasks/task.yaml")
