from __future__ import annotations


# ruff: noqa: E402

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aero_bench.providers.registry import builtin_provider_registry

from aero_bench.config.models import AgentSpec, EnvironmentSpec, TaskSpec
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.inspection_business.config import InspectionBusinessConfig
from aero_bench.providers.ns3.config import Ns3Config
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import InspectionTaskPackage
from aero_bench.tasks.inspection.integration import InspectionTaskPackageResolver


SOURCE_URI = "https://github.com/ZhiweiWei-NAMI/AERO_BENCH"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ref(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
    }


def _write_json(root: Path, relative: str, value: object) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")
    return _ref(root, path)


def _write_evidence_json(root: Path, relative: str, value: object) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    return _ref(root, path)


def _write_text(root: Path, relative: str, value: str) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return _ref(root, path)


def _write_yaml(root: Path, relative: str, value: object) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return _ref(root, path)


def _runtime(
    *,
    image: str,
    command: list[str],
    component_id: str,
    revision: str,
    version: str,
    cpu_millicores: int,
    memory_mib: int,
    source_uri: str = SOURCE_URI,
    gpu_count: int = 0,
) -> dict[str, object]:
    return {
        "runtime": {"image": image, "command": command},
        "resources": {
            "cpu_millicores": cpu_millicores,
            "memory_mib": memory_mib,
            "gpu_count": gpu_count,
        },
        "implementation": {
            "component_id": component_id,
            "kind": "production",
            "source_uri": source_uri,
            "source_revision": revision,
            "version": version,
        },
    }


def _artifact(
    artifact_id: str,
    artifact_type: str,
    producer_id: str,
    visibility: str,
    relative_path: str,
    *,
    max_size_bytes: int = 16_777_216,
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


def _object_schema(
    properties: dict[str, object], required: list[str]
) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build(args: argparse.Namespace) -> Path:
    root = Path(args.output_root).resolve()
    target_source = REPO_ROOT / "releases/inspection-v1/assets/inspection-target.sdf"
    target_bytes = target_source.read_bytes()
    if root.exists():
        existing = tuple(path for path in root.rglob("*") if path.is_file())
        allowed = {root / "assets/inspection-target.sdf"}
        unexpected = tuple(path for path in existing if path not in allowed)
        if unexpected and not args.force:
            raise ValueError(
                "release output already contains generated files; pass --force to replace"
            )
        if args.force:
            for child in tuple(root.iterdir()):
                if child.name != "assets":
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            assets = root / "assets"
            if assets.exists():
                for child in tuple(assets.iterdir()):
                    if child.name != "inspection-target.sdf":
                        if child.is_dir():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
    root.mkdir(parents=True, exist_ok=True)
    target_path = root / "assets/inspection-target.sdf"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(target_bytes)
    target_ref = _ref(root, target_path)

    instruction_ref = _write_text(
        root,
        "instruction.md",
        "# Inspection v1 task\n\n"
        "Inspect target `target.1` using observation `observation.1`. The defect "
        "taxonomy contains `defect.surface-damage`; report it only from the geometry "
        "observation data layer. Visual pixels, if needed later, come from an external "
        "renderer rather than Gazebo PNG capture. Use the granted Business, Flight, Network, "
        "and Observation interfaces. Submit the exact sealed report bytes through "
        "`network.send`; command receipts are not delivery or task success.\n",
    )

    observation_schema = _object_schema(
        {
            "schema_version": {"const": "aero-bench.observation.geometry.v1"},
            "target_id": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"},
            "camera_id": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"},
            "distance_m": {"type": "number", "exclusiveMinimum": 0},
            "view_angle_deg": {"type": "number", "minimum": 0},
            "visual_kind": {"const": "external_renderer"},
            "visual_status": {"const": "reserved"},
        },
        [
            "schema_version",
            "target_id",
            "camera_id",
            "distance_m",
            "view_angle_deg",
            "visual_kind",
            "visual_status",
        ],
    )
    observation_schema_ref = _write_json(
        root, "schemas/observation.json", observation_schema
    )
    generic_protocol_ref = _write_json(
        root,
        "schemas/provider-rpc.json",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
    )
    gateway_schema_ref = _write_json(
        root,
        "schemas/gateway-rpc.json",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
    )
    generic_response_ref = _write_json(
        root,
        "schemas/tool-response.json",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
    )

    identifier = {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"}
    sha256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    tool_schemas = {
        "business-claim": _object_schema(
            {"actor_id": identifier, "work_order_id": identifier},
            ["actor_id", "work_order_id"],
        ),
        "business-start": _object_schema(
            {"actor_id": identifier, "work_order_id": identifier},
            ["actor_id", "work_order_id"],
        ),
        "business-submit": _object_schema(
            {
                "actor_id": identifier,
                "observation_id": identifier,
                "report_payload_digest": sha256,
                "work_order_id": identifier,
            },
            [
                "actor_id",
                "observation_id",
                "report_payload_digest",
                "work_order_id",
            ],
        ),
        "flight-arm": _object_schema({"vehicle_id": identifier}, ["vehicle_id"]),
        "flight-takeoff": _object_schema(
            {
                "vehicle_id": identifier,
                "altitude_m": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 5.0,
                },
            },
            ["vehicle_id", "altitude_m"],
        ),
        "flight-hold": _object_schema({"vehicle_id": identifier}, ["vehicle_id"]),
        "network-send": _object_schema(
            {
                "destination": identifier,
                "message_id": identifier,
                "payload_base64": {"type": "string", "minLength": 4},
                "payload_sha256": sha256,
                "priority": {"type": "integer", "minimum": 0},
                "reliability": {"enum": ["best_effort", "reliable"]},
                "source": identifier,
                "traffic_class": identifier,
                "work_order_id": identifier,
            },
            [
                "destination",
                "message_id",
                "payload_base64",
                "payload_sha256",
                "priority",
                "reliability",
                "source",
                "traffic_class",
                "work_order_id",
            ],
        ),
    }
    tool_schema_refs = {
        name: _write_json(root, f"schemas/{name}.json", schema)
        for name, schema in tool_schemas.items()
    }

    truth = [
        {
            "source_artifact_id": "artifact.truth",
            "defect_id": "defect.surface-damage",
            "target_id": "target.1",
            "geometrically_visible": True,
        }
    ]
    truth_ref = _write_evidence_json(root, "private/truth.json", truth)

    bounds = {
        "mission": {
            "shortest_path_m": 0.0,
            "max_speed_mps": 5.0,
            "fixed_time_s": 0.5,
            "inspection_dwell_s": 0.5,
            "deadline_s": 10.0,
        },
        "link": {
            "minimum_payload_bytes": 256,
            "max_bandwidth_bps": 1_000_000.0,
            "minimum_latency_s": 0.05,
            "upload_deadline_s": 4.0,
        },
        "imaging": {
            "defect_size_m": 0.22,
            "standoff_distance_m": 1.88,
            "focal_length_m": 0.02,
            "pixel_pitch_m": 0.000003,
            "minimum_resolvable_pixels": 4.0,
        },
        "total_defects": 1,
        "geometrically_visible_defects": 1,
    }

    package_artifacts = [
        (
            "business_state",
            _artifact(
                "artifact.business",
                "business.state",
                "business",
                "public",
                "public/business/state.json",
            ),
        ),
        (
            "event_log",
            _artifact(
                "artifact.event-log",
                "event.log",
                "harness",
                "private",
                "private/harness/event.log",
            ),
        ),
        (
            "trajectory",
            _artifact(
                "artifact.trajectory",
                "trajectory",
                "flight",
                "public",
                "public/flight/trajectory.json",
            ),
        ),
        (
            "delivery",
            _artifact(
                "artifact.delivery",
                "network.delivery",
                "network",
                "public",
                "public/network/delivery.json",
            ),
        ),
        (
            "observation",
            _artifact(
                "artifact.observation",
                "observation",
                "flight",
                "private",
                "private/flight/observation.json",
            ),
        ),
        (
            "truth",
            _artifact(
                "artifact.truth",
                "truth.dataset",
                "bundle",
                "private",
                "private/bundle/truth.json",
                source_asset_id="asset.inspection-truth",
            ),
        ),
        (
            "detection",
            _artifact(
                "artifact.detection",
                "inspection.detection",
                "participant.agent",
                "public",
                "public/agent/detections.json",
            ),
        ),
        (
            "report",
            _artifact(
                "artifact.report",
                "inspection.report",
                "participant.agent",
                "public",
                "public/agent/report.json",
            ),
        ),
        (
            "theoretical_bounds",
            _artifact(
                "artifact.bounds",
                "theoretical.bounds",
                "harness",
                "public",
                "public/harness/theoretical-bounds.json",
            ),
        ),
    ]
    bound_fields = (
        "mission.shortest_path_m",
        "mission.max_speed_mps",
        "mission.fixed_time_s",
        "mission.inspection_dwell_s",
        "mission.deadline_s",
        "link.minimum_payload_bytes",
        "link.max_bandwidth_bps",
        "link.minimum_latency_s",
        "link.upload_deadline_s",
        "imaging.defect_size_m",
        "imaging.standoff_distance_m",
        "imaging.focal_length_m",
        "imaging.pixel_pitch_m",
        "imaging.minimum_resolvable_pixels",
        "total_defects",
        "geometrically_visible_defects",
    )

    package_raw = {
        "schema_version": "aero-bench.inspection-task/v1",
        "task_id": "inspection.v1.surface-damage",
        "verifier_id": "inspection.verifier",
        "actors": [
            {"actor_id": "participant.agent", "role": "agent"},
            {"actor_id": "flight", "role": "observation_provider"},
            {"actor_id": "business", "role": "business"},
            {"actor_id": "inspection.verifier", "role": "verifier"},
        ],
        "assets": [
            {
                "asset_id": "asset.inspection-target",
                "file": target_ref,
                "visibility": "world",
                "media_type": "application/vnd.gazebo.sdf+xml",
            },
            {
                "asset_id": "asset.inspection-truth",
                "file": truth_ref,
                "visibility": "verifier",
                "media_type": "application/json",
            },
        ],
        "work_orders": [
            {
                "work_order_id": "work-order.1",
                "target_id": "target.1",
                "required_observation_id": "observation.1",
                "arrival_tolerance_m": 2.5,
                "report_artifact_type": "inspection.report",
                "upload_deadline_ns": 8_000_000_000,
            }
        ],
        "observations": [
            {
                "observation_id": "observation.1",
                "target_id": "target.1",
                "world_asset_id": "asset.inspection-target",
                "metadata_schema": observation_schema_ref,
                "trigger": {
                    "observation_id": "observation.1",
                    "target_id": "target.1",
                    "provider_id": "flight",
                    "camera_id": "camera.mono.1",
                    "min_distance_m": 0.5,
                    "max_distance_m": 5.0,
                    "min_view_angle_deg": 0.0,
                    "max_view_angle_deg": 30.0,
                    "earliest_time_ns": 0,
                    "latest_time_ns": 10_000_000_000,
                },
            }
        ],
        "bounds": bounds,
        "bound_sources": [
            {
                "bound_field": field,
                "provider_id": "business",
                "config_pointer": "/task_package/bounds/" + "/".join(field.split(".")),
            }
            for field in bound_fields
        ],
        "artifact_requirements": [
            {**artifact, "evidence_kind": evidence_kind}
            for evidence_kind, artifact in package_artifacts
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
    package = InspectionTaskPackage.model_validate(package_raw)
    package_schema_ref = _write_json(
        root,
        "schemas/inspection-package.json",
        InspectionTaskPackage.model_json_schema(),
    )
    package_ref = _write_json(
        root, "task/inspection-package.json", package.model_dump(mode="json")
    )

    px4_config_raw = {
        "schema_version": "aero-bench.px4-gazebo/v1",
        "provider_id": "flight",
        "px4": {
            "version": "v1.17.0-alpha1-1551-g381149fb01",
            "commit": "381149fb012762f5e38c4a7fdc1b905b28038970",
        },
        "gazebo": {
            "version": "8.11.0",
            "commit": "1be3cc376fec778cc725b4eeea463245affa56d3",
        },
        "mavsdk": {
            "version": "3.17.2",
            "commit": "9e3ca17faa84aa868caea10a3bbdab7e53810ced",
        },
        "world_name": "default",
        "world_sdf": "default.sdf",
        "physics_step_ns": 4_000_000,
        "step_length_ns": 2_000_000_000,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [
            {
                "vehicle_id": "uav.1",
                "system_id": 1,
                "mavsdk_udp_port": 14540,
                "px4_mavlink_udp_port": 14580,
                "mavsdk_grpc_port": 15040,
                "sys_autostart": 4001,
                "model": "gz_x500_mono_cam",
                "gazebo_model_name": "x500_mono_cam_0",
                "gazebo_resource": "x500_mono_cam",
                "initial_pose": {
                    "x_m": 0.0,
                    "y_m": 0.0,
                    "z_m": 0.1,
                    "roll_rad": 0.0,
                    "pitch_rad": 0.0,
                    "yaw_rad": 0.0,
                },
            }
        ],
        "required_commands": ["px4", "gz", "mavsdk-server"],
        "command_timeout_ms": 120_000,
        "maximum_agent_decision_wall_time_ms": 120_000,
        "heartbeat_timeout_fixed_margin_ms": 10_000,
        "inspection": {
            "work_order_id": "work-order.1",
            "target_id": "target.1",
            "target_position": {"x_m": 2.0, "y_m": 0.03, "z_m": 0.6},
            "target_model_asset_id": "asset.inspection-target",
            "observation_id": "observation.1",
            "camera_id": "camera.mono.1",
            "camera_vehicle_id": "uav.1",
            "camera_mount": {
                "x_m": 0.12,
                "y_m": 0.03,
                "z_m": 0.242,
                "roll_rad": 0.0,
                "pitch_rad": 0.0,
                "yaw_rad": 0.0,
            },
            "metadata_schema": observation_schema_ref,
            "media_type": "application/json",
            "min_distance_m": 0.5,
            "max_distance_m": 5.0,
            "min_view_angle_deg": 0.0,
            "max_view_angle_deg": 30.0,
            "earliest_time_ns": 0,
            "latest_time_ns": 10_000_000_000,
        },
    }
    px4_config = Px4GazeboConfig.model_validate(px4_config_raw)
    px4_schema_ref = _write_json(
        root, "schemas/px4-config.json", Px4GazeboConfig.model_json_schema()
    )
    px4_config_ref = _write_json(
        root, "configs/px4.json", px4_config.model_dump(mode="json")
    )

    ns3_config = Ns3Config.model_validate(
        {
            "schema_version": "aero-bench.ns3/v1",
            "provider_id": "network",
            "ns3": {
                "version": "3.48",
                "commit": "d2add90b452d600cfb4859baed8e9ea633519447",
            },
            "nodes": [
                {"node_id": "agent.uplink"},
                {"node_id": "business.receiver"},
            ],
            "links": [
                {
                    "link_id": "inspection.link",
                    "source": "agent.uplink",
                    "destination": "business.receiver",
                    "data_rate_bps": 1_000_000,
                    "propagation_delay_ns": 50_000_000,
                }
            ],
            "required_commands": ["ns3"],
            "command_timeout_ms": 120_000,
        }
    )
    ns3_schema_ref = _write_json(
        root, "schemas/ns3-config.json", Ns3Config.model_json_schema()
    )
    ns3_config_ref = _write_json(
        root, "configs/ns3.json", ns3_config.model_dump(mode="json")
    )

    business_config = InspectionBusinessConfig(
        schema_version="aero-bench.inspection-business/v1",
        provider_id="business",
        task_package=package,
    )
    business_schema_ref = _write_json(
        root,
        "schemas/business-config.json",
        InspectionBusinessConfig.model_json_schema(),
    )
    business_config_ref = _write_json(
        root, "configs/business.json", business_config.model_dump(mode="json")
    )

    verifier_config_ref = _write_json(
        root,
        "configs/verifier.json",
        {
            "schema_version": "aero-bench.inspection-verifier/v1",
            "package_id": "inspection.v1",
        },
    )
    verifier_schema_ref = _write_json(
        root,
        "schemas/verifier-config.json",
        _object_schema(
            {
                "schema_version": {"const": "aero-bench.inspection-verifier/v1"},
                "package_id": {"const": "inspection.v1"},
            },
            ["schema_version", "package_id"],
        ),
    )

    all_artifacts = [artifact for _, artifact in package_artifacts]
    verifier_output = _artifact(
        "artifact.verification.report",
        "verification.report",
        "inspection.verifier",
        "public",
        "public/verifier/report.json",
        max_size_bytes=1_048_576,
    )
    task_raw = {
        "schema_version": "aero-bench.task/v1",
        "task_id": package.task_id,
        "package": {
            "package_id": "inspection.v1",
            "config": {"file": package_ref, "schema_file": package_schema_ref},
        },
        "instruction": instruction_ref,
        "required_capabilities": [
            "business.work-order",
            "flight.command",
            "network.delivery",
            "observation.capture",
        ],
        "required_tools": [
            "business.claim",
            "business.start",
            "business.submit",
            "flight.arm",
            "flight.takeoff",
            "flight.hold",
            "network.send",
        ],
        "assets": [
            {
                "asset_id": "asset.inspection-target",
                "file": target_ref,
                "classification": "private",
                "audiences": [{"role": "provider", "workload_ids": ["flight"]}],
            },
            {
                "asset_id": "asset.inspection-truth",
                "file": truth_ref,
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
            "workload": _runtime(
                image=args.verifier_image,
                command=["verify"],
                component_id="inspection.verifier",
                revision=args.revision,
                version="0.2.0-inspection-verifier.1",
                cpu_millicores=1000,
                memory_mib=1024,
            ),
            "config": {
                "file": verifier_config_ref,
                "schema_file": verifier_schema_ref,
            },
            "artifact_requirements": all_artifacts,
            "output_artifacts": [verifier_output],
        },
    }
    task = TaskSpec.model_validate(task_raw)
    task_ref = _write_yaml(root, "task/task.yaml", task.model_dump(mode="json"))

    trajectory_requirement = next(
        artifact
        for artifact in all_artifacts
        if artifact["artifact_id"] == "artifact.trajectory"
    )
    observation_requirement = next(
        artifact
        for artifact in all_artifacts
        if artifact["artifact_id"] == "artifact.observation"
    )
    delivery_requirement = next(
        artifact
        for artifact in all_artifacts
        if artifact["artifact_id"] == "artifact.delivery"
    )
    business_requirement = next(
        artifact
        for artifact in all_artifacts
        if artifact["artifact_id"] == "artifact.business"
    )
    event_requirement = next(
        artifact
        for artifact in all_artifacts
        if artifact["artifact_id"] == "artifact.event-log"
    )
    bounds_requirement = next(
        artifact
        for artifact in all_artifacts
        if artifact["artifact_id"] == "artifact.bounds"
    )
    environment_raw = {
        "schema_version": "aero-bench.environment/v1",
        "environment_id": "inspection.v1.reference",
        "harness": _runtime(
            image=args.harness_image,
            command=["serve"],
            component_id="aero-bench.harness",
            revision=args.revision,
            version="0.2.0-harness.1",
            cpu_millicores=1000,
            memory_mib=1024,
        ),
        "clock": {
            "authority": "provider_barrier",
            "step_ns": 2_000_000_000,
            "max_steps": 3,
            "provider_timeout_ms": 180_000,
        },
        "gateway": {"protocol_schema": gateway_schema_ref, "port": 17432},
        "providers": [
            {
                "provider_id": "flight",
                "adapter": "px4.gazebo",
                "port": 17601,
                "workload": _runtime(
                    image=args.px4_image,
                    command=["provider", "serve"],
                    component_id="px4.gazebo",
                    revision=args.revision,
                    version="0.2.0-px4-gazebo.1",
                    cpu_millicores=2000,
                    memory_mib=4096,
                ),
                "config": {"file": px4_config_ref, "schema_file": px4_schema_ref},
                "protocol_schema": generic_protocol_ref,
                "capabilities": ["flight.command", "observation.capture"],
                "artifact_requirements": [
                    trajectory_requirement,
                    observation_requirement,
                ],
            },
            {
                "provider_id": "network",
                "adapter": "ns3.rpc",
                "port": 17602,
                "workload": _runtime(
                    image=args.ns3_image,
                    command=["provider", "serve"],
                    component_id="ns3.rpc",
                    revision=args.revision,
                    version="0.2.0-ns3.1",
                    cpu_millicores=1000,
                    memory_mib=1024,
                ),
                "config": {"file": ns3_config_ref, "schema_file": ns3_schema_ref},
                "protocol_schema": generic_protocol_ref,
                "capabilities": ["network.delivery"],
                "artifact_requirements": [delivery_requirement],
            },
            {
                "provider_id": "business",
                "adapter": "inspection.business",
                "port": 17603,
                "workload": _runtime(
                    image=args.business_image,
                    command=["provider", "serve"],
                    component_id="inspection.business",
                    revision=args.revision,
                    version="0.2.0-inspection-business.1",
                    cpu_millicores=500,
                    memory_mib=512,
                ),
                "config": {
                    "file": business_config_ref,
                    "schema_file": business_schema_ref,
                },
                "protocol_schema": generic_protocol_ref,
                "capabilities": ["business.work-order"],
                "artifact_requirements": [business_requirement],
            },
        ],
        "harness_artifact_requirements": [event_requirement, bounds_requirement],
    }
    environment = EnvironmentSpec.model_validate(environment_raw)
    environment_ref = _write_yaml(
        root, "environment/environment.yaml", environment.model_dump(mode="json")
    )

    agent_raw = {
        "schema_version": "aero-bench.agent/v2", "queries": [],
        "agent_id": "participant.agent",
        "workload": _runtime(
            image=args.agent_image,
            command=["run"],
            component_id="participant.agent",
            revision=args.agent_revision,
            version=args.agent_version,
            source_uri=args.agent_source_uri,
            cpu_millicores=args.agent_cpu_millicores,
            memory_mib=args.agent_memory_mib,
            gpu_count=args.agent_gpu_count,
        ),
        "tools": [
            {
                "tool_id": "business.claim",
                "provider_id": "business",
                "request_schema": tool_schema_refs["business-claim"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
            {
                "tool_id": "business.start",
                "provider_id": "business",
                "request_schema": tool_schema_refs["business-start"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
            {
                "tool_id": "business.submit",
                "provider_id": "business",
                "request_schema": tool_schema_refs["business-submit"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
            {
                "tool_id": "flight.arm",
                "provider_id": "flight",
                "request_schema": tool_schema_refs["flight-arm"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
            {
                "tool_id": "flight.takeoff",
                "provider_id": "flight",
                "request_schema": tool_schema_refs["flight-takeoff"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
            {
                "tool_id": "flight.hold",
                "provider_id": "flight",
                "request_schema": tool_schema_refs["flight-hold"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
            {
                "tool_id": "network.send",
                "provider_id": "network",
                "request_schema": tool_schema_refs["network-send"],
                "response_schema": generic_response_ref,
                "timeout_ms": 120_000,
                "idempotent": False,
            },
        ],
        "observations": [
            {
                "observation_id": "observation.1",
                "provider_id": "flight",
                "schema_file": observation_schema_ref,
                "timeout_ms": 120_000,
            }
        ],
        "artifact_requirements": [
            next(
                artifact
                for artifact in all_artifacts
                if artifact["artifact_id"] == "artifact.detection"
            ),
            next(
                artifact
                for artifact in all_artifacts
                if artifact["artifact_id"] == "artifact.report"
            ),
        ],
    }
    agent = AgentSpec.model_validate(agent_raw)
    agent_ref = _write_yaml(
        root, "agent/participant.yaml", agent.model_dump(mode="json")
    )

    suite_raw = {
        "schema_version": "aero-bench.suite/v1",
        "suite_id": "inspection.v1.reference",
        "aggregator_id": "macro",
        "execution_scope": "formal_benchmark",
        "cases": [
            {
                "case_id": "inspection.surface-damage",
                "task": task_ref,
                "environment": environment_ref,
                "agents": [agent_ref],
                "seeds": [19],
                "axes": [],
            }
        ],
    }
    suite_path = root / "suite.yaml"
    _write_yaml(root, "suite.yaml", suite_raw)
    runs = resolve_suite(
        str(suite_path),
        executor_kind="docker_reference",
        task_package_resolvers=(InspectionTaskPackageResolver(),),
        provider_registry=builtin_provider_registry(),
    )
    if len(runs) != 1:
        raise RuntimeError("Inspection v1 release must resolve to exactly one run")
    _write_json(root, "resolved-run.json", runs[0].model_dump(mode="json"))
    print(
        json.dumps(
            {
                "run_id": runs[0].run_id,
                "status": "resolved",
                "suite": str(suite_path),
            },
            sort_keys=True,
        )
    )
    return suite_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "--output-root",
        default=str(REPO_ROOT / "releases/inspection-v1"),
    )
    result.add_argument("--revision", required=True)
    result.add_argument("--harness-image", required=True)
    result.add_argument("--px4-image", required=True)
    result.add_argument("--ns3-image", required=True)
    result.add_argument("--business-image", required=True)
    result.add_argument("--verifier-image", required=True)
    result.add_argument("--agent-image", required=True)
    result.add_argument("--agent-source-uri", required=True)
    result.add_argument("--agent-revision", required=True)
    result.add_argument("--agent-version", required=True)
    result.add_argument("--agent-cpu-millicores", type=int, default=1000)
    result.add_argument("--agent-memory-mib", type=int, default=1024)
    result.add_argument("--agent-gpu-count", type=int, default=0)
    result.add_argument("--force", action="store_true")
    return result


def main() -> None:
    build(parser().parse_args())


if __name__ == "__main__":
    main()
