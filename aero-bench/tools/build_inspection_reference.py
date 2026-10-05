#!/usr/bin/env python3
"""Build the explicit two-target, deterministic current-contract inspection profile."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.agent.inspection_reference import (  # noqa: E402
    InspectionReferencePolicy,
    POLICY_ASSET_ID,
    REFERENCE_AGENT_VERSION,
    ReferenceWorkOrder,
)
from aero_bench.config.models import AgentSpec, EnvironmentSpec, TaskSpec  # noqa: E402
from aero_bench.config.resolver import resolve_suite  # noqa: E402
from aero_bench.providers.inspection_business.config import InspectionBusinessConfig  # noqa: E402
from aero_bench.providers.registry import builtin_provider_registry  # noqa: E402
from aero_bench.runner.contracts import RunnerConfig  # noqa: E402
from aero_bench.tasks.inspection.contracts import InspectionTaskPackage  # noqa: E402
from aero_bench.tasks.inspection.formal_v2_contracts import (
    InspectionFormalVerifierConfigV3,
)  # noqa: E402
from aero_bench.tasks.inspection.integration import InspectionTaskPackageResolver  # noqa: E402
from aero_bench.world.alignment import GeneratorIdentity, alignment_manifest  # noqa: E402
from aero_bench.world.contracts import WorldPackageContent, world_package  # noqa: E402
from tools import build_urban_infrastructure_inspection_v1 as urban  # noqa: E402
from tools.build_agent_inspection_images import _reference_image_lock_payload  # noqa: E402


PROFILE_ID = "inspection.reference.v1"
TARGET_IDS = ("target.01", "target.05")
MAX_STEPS = 600
DEADLINE_S = 300.0
EVENT_LOG_BYTES = 512 * 1024 * 1024


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _reference_package(root: Path, schemas: dict[str, Path], world):
    package, artifacts = urban._task_package(
        root, urban._ref(root, schemas["observation"])
    )
    raw = package.model_dump(mode="json")
    raw["task_id"] = PROFILE_ID
    template_order = raw["work_orders"][0]
    template_observation = raw["observations"][0]
    raw["work_orders"], raw["observations"] = [], []
    for target_id in TARGET_IDS:
        suffix = target_id.rsplit(".", 1)[1]
        order = {
            **template_order,
            "work_order_id": f"work-order.{suffix}",
            "target_id": target_id,
            "required_observation_id": f"observation.{suffix}",
            "upload_deadline_ns": int(DEADLINE_S * 1e9),
        }
        observation = copy.deepcopy(template_observation)
        observation.update(
            {"observation_id": f"observation.{suffix}", "target_id": target_id}
        )
        observation["trigger"].update(
            {
                "observation_id": f"observation.{suffix}",
                "target_id": target_id,
                "latest_time_ns": int(DEADLINE_S * 1e9),
            }
        )
        raw["work_orders"].append(order)
        raw["observations"].append(observation)
    # Both declared Provider-private SDF instances contain the same damaged
    # surface. The participant must still determine presence from camera bytes.
    truth = [
        {
            "source_artifact_id": "artifact.truth",
            "target_id": target_id,
            "defect_id": f"defect.surface-damage.{target_id.rsplit('.', 1)[1]}",
            "geometrically_visible": True,
        }
        for target_id in TARGET_IDS
    ]
    truth_path = urban._write_bytes(
        root, "private/truth.json", urban.canonical_json_bytes(truth)
    )
    for asset in raw["assets"]:
        if asset["asset_id"] == "asset.inspection-truth":
            asset["file"] = urban._ref(root, truth_path)
    launch = (
        urban.LAUNCH_ALPHA_EAST_M,
        urban.LAUNCH_ALPHA_NORTH_M,
        urban.UAV_LAUNCH_REFERENCE_UP_M,
    )
    entity_by_id = {item.entity_id: item for item in world.entities}
    surfaces = []
    for target_id in TARGET_IDS:
        target = next(
            item for item in world.semantic_targets if item.target_id == target_id
        )
        parent = entity_by_id[target.parent_entity_id].pose
        surfaces.append(
            (
                parent.east_m + target.pose.x_m,
                parent.north_m + target.pose.y_m,
                parent.up_m + target.pose.z_m,
            )
        )
    points = (launch, *surfaces, launch)
    radii = (0.0, *(urban.MAX_STANDOFF_M for _ in surfaces), 0.0)
    distance_lower_bound = sum(
        max(0.0, math.dist(start, end) - radii[index] - radii[index + 1])
        for index, (start, end) in enumerate(zip(points, points[1:]))
    )
    raw["bounds"]["mission"] = {
        "shortest_path_m": distance_lower_bound,
        "max_speed_mps": 10.0,
        "fixed_time_s": 30.0,
        "inspection_dwell_s": 4.0,
        "deadline_s": DEADLINE_S,
    }
    raw["bounds"]["link"].update(
        {
            "minimum_payload_bytes": 128,
            "upload_deadline_s": DEADLINE_S,
        }
    )
    raw["bounds"]["imaging"] = {
        "defect_size_m": urban.DEFECT_MINIMUM_DIMENSION_M,
        "standoff_distance_m": urban.MAX_STANDOFF_M,
        "focal_length_m": urban.CAMERA_EQUIVALENT_FOCAL_LENGTH_M,
        "pixel_pitch_m": urban.PIXEL_PITCH_MODEL_M,
        "minimum_resolvable_pixels": 4.0,
    }
    raw["bounds"]["total_defects"] = len(truth)
    raw["bounds"]["geometrically_visible_defects"] = len(truth)
    artifacts["event_log"]["max_size_bytes"] = EVENT_LOG_BYTES
    for artifact in raw["artifact_requirements"]:
        if artifact["evidence_kind"] == "event_log":
            artifact["max_size_bytes"] = EVENT_LOG_BYTES
    return InspectionTaskPackage.model_validate(raw), artifacts


def _reference_world(root: Path):
    world = urban._world(root, urban._materialize_world_assets(root))
    raw = world.model_dump(mode="json")
    for field in ("world_digest", "asset_digest"):
        del raw[field]
    raw["world_id"] = "world.inspection-reference-v1"
    # The declared operations radio is anchored to the real launch pad, not the
    # distant urban operations building used by the three-target release.
    for binding in raw["network"]["node_bindings"]:
        if binding["node_id"] == "node.operations":
            binding["entity_id"] = "entity.launch.alpha"
    extent = world.frame.spatial_extent
    pad = next(
        item for item in world.entities if item.entity_id == "entity.launch.alpha"
    )
    corners = itertools.product(
        (extent.min_east_m, extent.max_east_m),
        (extent.min_north_m, extent.max_north_m),
        (extent.min_up_m, extent.max_up_m),
    )
    maximum_distance = max(
        math.dist(corner, (pad.pose.east_m, pad.pose.north_m, pad.pose.up_m))
        for corner in corners
    )
    delay_ns = math.ceil(maximum_distance / 299_792_458 * 1e9)
    raw["network"]["links"][0]["propagation_delay_ns"] = delay_ns
    raw["mission_requirements"] = [
        item
        for item in raw["mission_requirements"]
        if item["target_id"] is None or item["target_id"] == TARGET_IDS[0]
    ]
    observation_requirement = copy.deepcopy(
        next(
            item
            for item in raw["mission_requirements"]
            if item["target_id"] == TARGET_IDS[0]
        )
    )
    observation_requirement.update(
        {
            "requirement_id": "mission.observe.05",
            "target_id": TARGET_IDS[1],
            "dependencies": ["mission.observe.01"],
        }
    )
    raw["mission_requirements"].append(observation_requirement)
    for requirement in raw["mission_requirements"]:
        if requirement["requirement_id"] == "mission.upload":
            requirement["dependencies"] = ["mission.land"]
        elif requirement["requirement_id"] == "mission.return":
            requirement["dependencies"] = ["mission.observe.05"]
    content = WorldPackageContent.model_validate(raw).model_dump(mode="python")
    del content["schema_version"]
    world = world_package(**content)
    urban._write_json(root, "world/package.json", world.model_dump(mode="json"))
    alignment = alignment_manifest(
        reader=urban._bundle_reader(root),
        world=world,
        generator=GeneratorIdentity(
            generator_id="aero-bench",
            version=PROFILE_ID,
            source_revision=urban._runtime_revision(root),
        ),
    )
    urban._write_json(root, "world/alignment.json", alignment.model_dump(mode="json"))
    return world, delay_ns, maximum_distance


def build(output: Path, images_lock: Path) -> Path:
    root = output.resolve()
    if root.exists():
        raise ValueError("reference output must be a new directory")
    lock = _read_json(images_lock)
    revision, source_files = urban._managed_source_closure()
    if lock.get("participant_profile") != "reference_inspection":
        raise ValueError(
            "reference bundle requires its explicit participant image profile"
        )
    validated_lock = _reference_image_lock_payload(
        revision=lock["runtime_source_revision"],
        images=lock["images"],
    )
    if lock != validated_lock:
        raise ValueError(
            "reference image lock differs from its strict canonical contract"
        )
    if revision != lock["runtime_source_revision"]:
        raise ValueError(
            "image source revision differs from current managed source closure"
        )
    root.mkdir(parents=True)
    urban._write_text(root, ".runtime-source-revision", revision + "\n")
    urban._write_json(
        root,
        "provenance/runtime-source-lock.json",
        {
            "schema_version": "aero-bench.runtime-source-lock/v1",
            "source_uri": urban.SOURCE_URI,
            "source_revision_kind": "content_closure_sha256",
            "source_revision": revision,
            "selectors": ["aero_bench/**", "containers/**"],
            "files": source_files,
        },
    )
    urban._write_text(
        root,
        "instruction.md",
        (
            "# Deterministic inspection reference v1\n\n"
            "Execute the two declared work orders through the Gateway. Take off above "
            "12.5 m AGL, follow the public west-face route, capture two distinct actual "
            "RGB frames with two seconds of dwell, classify the red panel's pixels, "
            "return and land at the declared operations radio, deliver the canonical "
            "frame-bound report, then disarm. "
            "The independent verifier evaluates all 15 Formal Inspection v2 criteria. "
            "This profile does not evaluate a language model or the full city.\n"
        ),
    )
    schemas = urban._schemas(root, agent_acceptance=True)
    world, delay_ns, maximum_distance = _reference_world(root)
    package, artifacts = _reference_package(root, schemas, world)
    package_raw = package.model_dump(mode="json")
    package_raw["bounds"]["link"]["minimum_latency_s"] = delay_ns / 1e9
    package = InspectionTaskPackage.model_validate(package_raw)
    urban._write_json(
        root, "task/inspection-package.json", package.model_dump(mode="json")
    )
    _goals, bindings = urban._goals()
    configs = urban._provider_configs(root, package, bindings)
    verifier_raw = _read_json(configs["verifier"])
    verifier_raw["target_ids"] = list(TARGET_IDS)
    verifier_raw["network_deadline_s"] = DEADLINE_S
    verifier = InspectionFormalVerifierConfigV3.model_validate(verifier_raw)
    urban._write_json(root, "configs/verifier.json", verifier.model_dump(mode="json"))
    # The Business config embeds the final package, including its derived link
    # bound; it must not retain an earlier package digest.
    business = InspectionBusinessConfig(
        schema_version="aero-bench.inspection-business/v1",
        provider_id=urban.BUSINESS_ID,
        task_package=package,
    )
    urban._write_json(root, "configs/business.json", business.model_dump(mode="json"))
    task_path, environment_path, agent_path = urban._runtime_specs(
        root,
        revision=revision,
        images=lock["images"],
        agent_metadata=lock["agent"],
        schemas=schemas,
        configs=configs,
        package=package,
        artifacts=artifacts,
        formal=True,
    )
    policy = InspectionReferencePolicy(
        schema_version="aero-bench.inspection-reference-policy/v1",
        vehicle_id=urban.UAV_ID,
        work_orders=tuple(
            ReferenceWorkOrder(
                work_order_id=f"work-order.{target_id.rsplit('.', 1)[1]}",
                target_id=target_id,
                observation_id=f"observation.{target_id.rsplit('.', 1)[1]}",
                defect_id=f"defect.surface-damage.{target_id.rsplit('.', 1)[1]}",
            )
            for target_id in TARGET_IDS
        ),
        source_endpoint_id="endpoint.uav",
        destination_endpoint_id="endpoint.operations",
        cruise_up_m=20.0,
        standoff_m=10.0,
        dwell_s=2.0,
        delivery_wait_s=30.0,
    )
    policy_path = urban._write_json(
        root, "agent/reference-policy.json", policy.model_dump(mode="json")
    )
    task_raw = yaml.safe_load(task_path.read_text())
    task_raw["task_id"] = PROFILE_ID
    task_raw["assets"].append(
        {
            "asset_id": POLICY_ASSET_ID,
            "file": urban._ref(root, policy_path),
            "classification": "public",
            "audiences": [{"role": "agent", "workload_ids": [urban.AGENT_ID]}],
        }
    )
    task = TaskSpec.model_validate(task_raw)
    urban._write_yaml(root, "task/task.yaml", task.model_dump(mode="json"))
    environment_raw = yaml.safe_load(environment_path.read_text())
    environment_raw["environment_id"] = PROFILE_ID
    environment_raw["clock"]["max_steps"] = MAX_STEPS
    environment = EnvironmentSpec.model_validate(environment_raw)
    urban._write_yaml(
        root, "environment/environment.yaml", environment.model_dump(mode="json")
    )
    agent_raw = yaml.safe_load(agent_path.read_text())
    agent_raw["observations"] = [
        item
        for item in agent_raw["observations"]
        if item["observation_id"] in {"observation.01", "network.mailbox.endpoint.uav"}
    ]
    observation_grant = copy.deepcopy(
        next(
            item
            for item in agent_raw["observations"]
            if item["observation_id"] == "observation.01"
        )
    )
    observation_grant["observation_id"] = "observation.05"
    agent_raw["observations"].append(observation_grant)
    agent_raw["queries"] = [
        {
            "query_type": "business.work-order",
            "provider_id": urban.BUSINESS_ID,
            "request_schema": urban._ref(root, schemas["business-work-order-query"]),
            "response_schema": urban._ref(root, schemas["business-work-order-result"]),
            "timeout_ms": 180_000,
        }
    ]
    agent = AgentSpec.model_validate(agent_raw)
    if agent.workload.implementation.version != REFERENCE_AGENT_VERSION:
        raise ValueError("reference Agent image has the wrong implementation version")
    urban._write_yaml(root, "agent/participant.yaml", agent.model_dump(mode="json"))
    suite = {
        "schema_version": "aero-bench.suite/v2",
        "suite_id": PROFILE_ID,
        "aggregator_id": "macro",
        "execution_scope": "formal_benchmark",
        "cases": [
            {
                "case_id": "inspection.reference.formal",
                "task": urban._ref(root, task_path),
                "environment": urban._ref(root, environment_path),
                "agents": [urban._ref(root, agent_path)],
                "world_package": urban._ref(root, root / "world/package.json"),
                "launch_site_ids": ["launch.alpha"],
                "seeds": [20260930],
                "axes": [],
            }
        ],
    }
    suite_path = urban._write_yaml(root, "suite.yaml", suite)
    runs = resolve_suite(
        str(suite_path),
        executor_kind="docker_reference",
        task_package_resolvers=(InspectionTaskPackageResolver(),),
        provider_registry=builtin_provider_registry(),
    )
    if len(runs) != 1:
        raise ValueError("reference suite must resolve exactly one run")
    run = runs[0]
    urban._write_json(root, "resolved-run.json", run.model_dump(mode="json"))
    urban._write_json(root, "image-lock.json", lock)
    runner = RunnerConfig(
        schema_version="aero-bench.runner-config/v1",
        executor_kind="docker_reference",
        output_root=str(root / "execution"),
        runtime_timeout_seconds=7200,
        verifier_timeout_seconds=600,
        readiness_timeout_seconds=480,
        docker_binary="docker",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=2 * 1024**3,
        artifact_volume_size_bytes=2 * 1024**3,
        scratch_size_bytes=8 * 1024**3,
        pids_limit=2048,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        gateway_bind_host="0.0.0.0",
        volume_keeper_image=lock["images"]["verifier"],
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
    )
    urban._write_yaml(root, "runner.local.yaml", runner.model_dump(mode="json"))
    urban._write_json(
        root,
        "source-lock.json",
        {
            "schema_version": "aero-bench.inspection-reference-source-lock/v1",
            "profile": PROFILE_ID,
            "runtime_source_revision": revision,
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "world_digest": world.world_digest,
            "required_target_ids": list(TARGET_IDS),
            "formal_component_ids": list(urban.FORMAL_V2_COMPONENT_IDS),
            "propagation_delay_ns": delay_ns,
            "maximum_radio_distance_m": maximum_distance,
            "model_agent_acceptance": False,
            "full_city_acceptance": False,
        },
    )
    (root / ".runtime-source-revision").unlink()
    print(
        json.dumps(
            {
                "output_root": str(root),
                "run_id": run.run_id,
                "profile": PROFILE_ID,
                "status": "resolved_not_executed",
            },
            sort_keys=True,
        )
    )
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--images-lock", required=True, type=Path)
    arguments = parser.parse_args()
    build(arguments.output_root, arguments.images_lock)


if __name__ == "__main__":
    main()
