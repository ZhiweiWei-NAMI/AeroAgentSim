#!/usr/bin/env python3
"""Lower the verified Huangpu WorldPackage into one formal inspection run.

The lowering reuses the exact production inspection contracts and digest-pinned
OCI images from an accepted reference contract bundle.  It replaces only the
scenario-specific task, Provider configuration, participant policy, and Suite
identity.  The resulting Suite resolves against the source-derived Huangpu
WorldPackage through the normal capability and feasibility preflight.

This tool does not start Docker.  It records a resolved, runnable input set and
an explicit runner configuration; formal execution remains a separate action.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.agent.inspection_reference import (  # noqa: E402
    InspectionReferencePolicy,
)
from aero_bench.artifacts import seal_manifest_size_upper_bound  # noqa: E402
from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeRegistrationError,
    assess_city_native_registration,
    load_city_native_input_lock,
)
from aero_bench.config.loader import sha256_file  # noqa: E402
from aero_bench.config.models import (  # noqa: E402
    AgentSpec,
    EnvironmentSpec,
    SuiteSpec,
    TaskSpec,
)
from aero_bench.config.resolver import resolve_suite  # noqa: E402
from aero_bench.providers.inspection_business.config import (  # noqa: E402
    InspectionBusinessConfig,
)
from aero_bench.providers.ns3.config import Ns3Config  # noqa: E402
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig  # noqa: E402
from aero_bench.providers.registry import builtin_provider_registry  # noqa: E402
from aero_bench.providers.sumo.config import SumoConfig  # noqa: E402
from aero_bench.runner.contracts import RunnerConfig  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.tasks.inspection.contracts import (  # noqa: E402
    InspectionTaskPackage,
)
from aero_bench.tasks.inspection.formal_v2_contracts import (  # noqa: E402
    InspectionFormalVerifierConfigV3,
)
from aero_bench.tasks.registry import builtin_task_package_resolvers  # noqa: E402
from aero_bench.world.contracts import WorldPackage  # noqa: E402


TASK_ID = "inspection.huangpu-native.v1"
PACKAGE_ID = "inspection.v1"
SUITE_ID = "inspection.huangpu-native.v1"
CASE_ID = "inspection.huangpu-native.formal"
ENVIRONMENT_ID = "inspection.huangpu-native.v1"
SEED = 20261001

AGENT_ID = "participant.agent"
VERIFIER_ID = "inspection.verifier"
FLIGHT_ID = "flight"
NETWORK_ID = "network"
TRAFFIC_ID = "traffic"
BUSINESS_ID = "business"
UAV_ID = "uav.inspector"
SENSOR_ID = "camera.inspection.front"
LAUNCH_ID = "launch.huangpu-research"
GEOFENCE_ID = "region.city-operational-geofence"
NO_FLY_ID = "region.city-no-fly-east"
TARGET_IDS = (
    "target.city-facade.lower",
    "target.city-facade.upper",
)
OBSERVATION_IDS = (
    "observation.city-facade.lower",
    "observation.city-facade.upper",
)
WORK_ORDER_IDS = (
    "work-order.city-facade.lower",
    "work-order.city-facade.upper",
)
DEFECT_IDS = (
    "defect.surface-damage.lower",
    "defect.surface-damage.upper",
)

CRUISE_UP_M = 20.0
STANDOFF_M = 10.0
DWELL_S = 2.0
MISSION_DEADLINE_S = 300.0
REFERENCE_SPEED_CEILING_MPS = 10.0
ENGINEERING_CRUISE_MPS = 5.0
ENGINEERING_HORIZONTAL_ACCELERATION_MPS2 = 1.4
ENGINEERING_VERTICAL_SPEED_MPS = 2.0
ENGINEERING_VERTICAL_ACCELERATION_MPS2 = 1.0
MINIMUM_ROOF_CLEARANCE_M = 5.0

BUILD_REPORT_SCHEMA = "aero-bench.huangpu-native-inspection-build/v1"
IMAGE_LOCK_SCHEMA = "aero-bench.inspection-reference-image-lock/v1"
CAPACITY_PLAN_SCHEMA = "aero-bench.huangpu-native-artifact-capacity/v1"
RUNTIME_CAPACITY_PLAN_SCHEMA = "aero-bench.huangpu-native-runtime-capacity/v1"
SIZED_ARTIFACT_IDS = (
    "artifact.event-log",
    "artifact.scene-states",
)
RUNNER_CAPACITY_FIELDS = (
    "input_volume_size_bytes",
    "artifact_volume_size_bytes",
    "scratch_size_bytes",
)
REQUIRED_IMAGE_KEYS = (
    "agent",
    "business",
    "flight",
    "harness",
    "network",
    "traffic",
    "verifier",
)
GENERATED_PATHS = (
    "agent",
    "assets",
    "configs",
    "environment",
    "private",
    "provenance",
    "schemas",
    "task",
    "instruction.md",
    "suite.yaml",
    "resolved-run.json",
    "compiled-resolved-runs.json",
)


class HuangpuNativeInspectionBuildError(ValueError):
    """The exact city package cannot be lowered to the declared formal run."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-lock", required=True)
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--contract-template", required=True)
    parser.add_argument("--image-lock", required=True)
    parser.add_argument("--capacity-plan", required=True)
    parser.add_argument("--runtime-capacity-plan", required=True)
    parser.add_argument("--runner-config", required=True)
    parser.add_argument("--run-output-root", required=True)
    parser.add_argument("--report", required=True)
    return parser


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HuangpuNativeInspectionBuildError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise HuangpuNativeInspectionBuildError(f"{label} must be an array")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(path.read_bytes()), label)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HuangpuNativeInspectionBuildError(
            f"invalid {label}: {path}: {exc}"
        ) from exc


def _read_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HuangpuNativeInspectionBuildError(
            f"invalid {label}: {path}: {exc}"
        ) from exc


def _write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _write_json(path: Path, document: object, *, pretty: bool = False) -> Path:
    if pretty:
        payload = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
    else:
        payload = canonical_json_bytes(document) + b"\n"
    return _write_bytes(path, payload)


def _write_yaml(path: Path, document: object) -> Path:
    payload = yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")
    return _write_bytes(path, payload)


def _ref(root: Path, path: Path) -> dict[str, str]:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(resolved_root):
        raise HuangpuNativeInspectionBuildError(
            f"referenced file is outside the generated bundle: {resolved}"
        )
    return {
        "path": resolved.relative_to(resolved_root).as_posix(),
        "sha256": sha256_file(resolved),
    }


def _repository_relative(path: Path) -> str:
    resolved = path.resolve(strict=True)
    root = REPOSITORY_ROOT.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise HuangpuNativeInspectionBuildError(
            f"path must remain inside the repository: {resolved}"
        )
    return resolved.relative_to(root).as_posix()


def _require_new_path(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        raise HuangpuNativeInspectionBuildError(
            f"{label} already exists; refusing to overwrite: {path}"
        )


def _copy_regular_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise HuangpuNativeInspectionBuildError(
            f"template input is not one regular file: {source}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(source) != sha256_file(destination):
        raise HuangpuNativeInspectionBuildError(
            f"copied template bytes changed: {source}"
        )


def _load_images(path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    document = _read_json(path, "runtime image lock")
    if document.get("schema_version") != IMAGE_LOCK_SCHEMA:
        raise HuangpuNativeInspectionBuildError(
            "runtime image lock uses an unsupported schema"
        )
    raw_images = _mapping(document.get("images"), "runtime image lock images")
    if tuple(sorted(raw_images)) != REQUIRED_IMAGE_KEYS:
        raise HuangpuNativeInspectionBuildError(
            "runtime image lock does not contain the exact reference workload set"
        )
    images: dict[str, str] = {}
    for name in REQUIRED_IMAGE_KEYS:
        value = raw_images[name]
        if (
            not isinstance(value, str)
            or "@sha256:" not in value
            or len(value.rsplit("@sha256:", 1)[1]) != 64
        ):
            raise HuangpuNativeInspectionBuildError(
                f"runtime image {name} is not digest-pinned"
            )
        images[name] = value
    if images["verifier"] in {
        value for key, value in images.items() if key != "verifier"
    }:
        raise HuangpuNativeInspectionBuildError(
            "independent verifier image is reused by another workload"
        )
    return images, document


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HuangpuNativeInspectionBuildError(f"{label} must be a positive integer")
    return value


def _verify_capacity_pin(item: object, label: str) -> None:
    record = _mapping(item, label)
    path = record.get("path")
    digest = record.get("sha256")
    size_bytes = record.get("size_bytes")
    if not isinstance(path, str) or not path:
        raise HuangpuNativeInspectionBuildError(f"{label} omits its repository path")
    candidate = REPOSITORY_ROOT / path
    if candidate.is_symlink():
        raise HuangpuNativeInspectionBuildError(
            f"{label} cannot reference a symbolic link"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HuangpuNativeInspectionBuildError(f"{label} is unavailable") from exc
    if not resolved.is_file() or not resolved.is_relative_to(REPOSITORY_ROOT):
        raise HuangpuNativeInspectionBuildError(
            f"{label} must reference one regular repository file"
        )
    if not isinstance(digest, str) or sha256_file(resolved) != digest:
        raise HuangpuNativeInspectionBuildError(f"{label} digest mismatch")
    if _positive_int(size_bytes, f"{label} size") != resolved.stat().st_size:
        raise HuangpuNativeInspectionBuildError(f"{label} size mismatch")


def _load_capacity_plan(path: Path, world: WorldPackage) -> dict[str, Any]:
    plan = _read_json(path, "artifact capacity plan")
    if plan.get("schema_version") != CAPACITY_PLAN_SCHEMA:
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan uses an unsupported schema"
        )
    if plan.get("formal_execution_started") is not False:
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan must remain pre-execution evidence"
        )
    source = _mapping(plan.get("source_evidence"), "capacity source evidence")
    if source.get("accepted_as_task_result") is not False:
        raise HuangpuNativeInspectionBuildError(
            "failed capacity evidence cannot be accepted as a task result"
        )
    _verify_capacity_pin(source.get("event_log"), "capacity event log")
    _verify_capacity_pin(source.get("scene_states"), "capacity scene states")
    target = _mapping(plan.get("target"), "capacity target")
    _verify_capacity_pin(target.get("resolved_run"), "capacity target ResolvedRun")
    _verify_capacity_pin(target.get("runner_config"), "capacity target runner")
    if target.get("world_digest") != world.world_digest:
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan targets a different WorldPackage digest"
        )
    if _positive_int(target.get("entity_count"), "capacity target entity count") != len(
        world.entities
    ):
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan targets a different entity inventory size"
        )

    limits = _mapping(plan.get("artifact_limits"), "artifact capacity limits")
    if set(limits) != set(SIZED_ARTIFACT_IDS):
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan must size exactly the harness histories"
        )
    for artifact_id in SIZED_ARTIFACT_IDS:
        _positive_int(limits[artifact_id], f"{artifact_id} capacity")
    runner_limits = _mapping(plan.get("runner_limits"), "runner capacity limits")
    if set(runner_limits) != set(RUNNER_CAPACITY_FIELDS):
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan has an unexpected runner limit set"
        )
    for field in RUNNER_CAPACITY_FIELDS:
        _positive_int(runner_limits[field], f"runner {field}")

    event = _mapping(source.get("event_log"), "capacity event-log measurement")
    scene = _mapping(source.get("scene_states"), "capacity scene-state measurement")
    model = _mapping(plan.get("model"), "artifact capacity model")
    max_steps = _positive_int(target.get("max_steps"), "capacity maximum steps")
    event_projection = (
        _positive_int(event.get("tick_zero_bytes"), "capacity tick-zero bytes")
        + _positive_int(
            event.get("runtime_tick_bytes_max"), "capacity maximum event tick bytes"
        )
        * max_steps
    )
    scene_projection = (
        _positive_int(scene.get("line_bytes_max"), "capacity maximum scene-state bytes")
        * max_steps
    )
    if model.get("event_log_projection_bytes") != event_projection:
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity event-log projection is inconsistent"
        )
    if model.get("scene_states_projection_bytes") != scene_projection:
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity scene-state projection is inconsistent"
        )
    if int(limits["artifact.event-log"]) <= event_projection:
        raise HuangpuNativeInspectionBuildError(
            "event-log capacity has no projection headroom"
        )
    if int(limits["artifact.scene-states"]) <= scene_projection:
        raise HuangpuNativeInspectionBuildError(
            "scene-state capacity has no projection headroom"
        )
    return plan


def _load_runtime_capacity_plan(
    path: Path,
    *,
    world: WorldPackage,
    artifact_capacity_path: Path,
) -> dict[str, Any]:
    plan = _read_json(path, "runtime capacity plan")
    if plan.get("schema_version") != RUNTIME_CAPACITY_PLAN_SCHEMA:
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan uses an unsupported schema"
        )
    if (
        plan.get("status")
        != "modelled-from-failed-runtime-evidence-not-executed"
        or plan.get("formal_execution_started") is not False
        or plan.get("accepted_as_task_result") is not False
    ):
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan is not bounded pre-execution evidence"
        )
    inputs = _mapping(plan.get("inputs"), "runtime capacity inputs")
    expected_inputs = {
        "artifact_capacity_plan",
        "runtime_observations",
        "target_resolved_run",
        "target_runner_config",
        "v5_runner_summary",
        "v5_failure_diagnostic",
        "v5_failed_inventory",
        "v5_traffic_evidence",
        "calibration_resolved_run",
        "calibration_event_log",
        "calibration_scene_states",
    }
    if set(inputs) != expected_inputs:
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan has an unexpected input inventory"
        )
    for label, pin in inputs.items():
        _verify_capacity_pin(pin, f"runtime capacity input {label}")
    artifact_pin = _mapping(
        inputs["artifact_capacity_plan"], "runtime artifact-capacity input"
    )
    if (
        artifact_pin.get("sha256") != sha256_file(artifact_capacity_path)
        or artifact_pin.get("size_bytes") != artifact_capacity_path.stat().st_size
    ):
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan targets a different artifact capacity plan"
        )

    failed = _mapping(plan.get("failed_run"), "runtime capacity failed run")
    if (
        failed.get("world_digest") != world.world_digest
        or failed.get("entity_count") != len(world.entities)
        or failed.get("harness_oom_killed") is not True
        or failed.get("authoritative_seal") is not False
        or failed.get("verification") is not None
        or failed.get("public_trace") is not None
    ):
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan is not bound to the failed city dimensions"
        )
    memory = _mapping(plan.get("harness_memory_model"), "harness memory model")
    selected_memory = _positive_int(
        memory.get("selected_harness_memory_mib"), "selected harness memory"
    )
    if selected_memory * 1024**2 < _positive_int(
        memory.get("peak_with_headroom_bytes"), "harness peak with headroom"
    ):
        raise HuangpuNativeInspectionBuildError(
            "selected harness memory is below the modelled peak"
        )
    timeout = _mapping(plan.get("runtime_timeout_model"), "runtime timeout model")
    selected_timeout = _positive_int(
        timeout.get("selected_runtime_timeout_seconds"),
        "selected runtime timeout",
    )
    if selected_timeout < _positive_int(
        timeout.get("full_horizon_with_25_percent_headroom_seconds"),
        "runtime horizon with headroom",
    ):
        raise HuangpuNativeInspectionBuildError(
            "selected runtime timeout is below its declared horizon"
        )
    host = _mapping(plan.get("host_feasibility"), "host feasibility")
    if (
        host.get("feasible_at_measurement") is not True
        or not isinstance(host.get("available_memory_margin_bytes"), int)
        or host["available_memory_margin_bytes"] <= 0
    ):
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan did not establish host feasibility"
        )
    delivery = _mapping(
        plan.get("public_trace_delivery_model"), "public trace delivery model"
    )
    terminal = _mapping(
        delivery.get("projected_terminal_horizon"),
        "terminal public trace projection",
    )
    declared = _mapping(
        delivery.get("projected_declared_horizon"),
        "declared public trace projection",
    )
    if (
        terminal.get("within_server_trace_limit") is not True
        or declared.get("within_frontend_trace_limit") is not True
    ):
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan predicts an undeliverable public trace"
        )
    revision = _mapping(plan.get("executor_revision"), "executor revision")
    if (
        revision.get("profile") != "docker_reference"
        or revision.get("harness_memory_mib") != selected_memory
        or revision.get("runtime_timeout_seconds") != selected_timeout
        or any(
            revision.get(field) is not False
            for field in (
                "domain_specs_changed",
                "world_inputs_changed",
                "task_inputs_changed",
                "provider_images_changed",
                "artifact_limits_changed",
            )
        )
    ):
        raise HuangpuNativeInspectionBuildError(
            "runtime capacity plan is not an executor-only revision"
        )
    return plan


def _validate_resolved_capacity(run: object, plan: dict[str, Any]) -> None:
    limits = _mapping(plan.get("artifact_limits"), "artifact capacity limits")
    requirements = tuple(getattr(run, "artifact_requirements"))
    requirements_by_id = {item.artifact_id: item for item in requirements}
    for artifact_id in SIZED_ARTIFACT_IDS:
        if artifact_id not in requirements_by_id:
            raise HuangpuNativeInspectionBuildError(
                f"resolved run omits sized artifact {artifact_id}"
            )
        if requirements_by_id[artifact_id].max_size_bytes != limits[artifact_id]:
            raise HuangpuNativeInspectionBuildError(
                f"resolved {artifact_id} capacity differs from the capacity plan"
            )
    total_bytes = sum(item.max_size_bytes for item in requirements)
    producer_totals: dict[str, int] = {}
    for requirement in requirements:
        if requirement.producer_id != "bundle":
            producer_totals[requirement.producer_id] = (
                producer_totals.get(requirement.producer_id, 0)
                + requirement.max_size_bytes
            )
    capacity = _mapping(plan.get("capacity"), "artifact capacity summary")
    if capacity.get("artifact_requirements_total_bytes") != total_bytes:
        raise HuangpuNativeInspectionBuildError(
            "resolved artifact total differs from the capacity plan"
        )
    producer_maximum = max(producer_totals.values())
    if capacity.get("producer_maximum_bytes") != producer_maximum:
        raise HuangpuNativeInspectionBuildError(
            "resolved producer maximum differs from the capacity plan"
        )
    manifest_bytes = seal_manifest_size_upper_bound(
        run_id=getattr(run, "run_id"),
        execution_scope=getattr(run, "execution_scope"),
        requirements=requirements,
    )
    if capacity.get("seal_manifest_upper_bound_bytes") != manifest_bytes:
        raise HuangpuNativeInspectionBuildError(
            "resolved seal-manifest bound differs from the capacity plan"
        )
    sealed_input_bytes = total_bytes + manifest_bytes
    if capacity.get("sealed_input_upper_bound_bytes") != sealed_input_bytes:
        raise HuangpuNativeInspectionBuildError(
            "resolved sealed-input bound differs from the capacity plan"
        )
    runner_limits = _mapping(plan.get("runner_limits"), "runner capacity limits")
    if runner_limits["input_volume_size_bytes"] < sealed_input_bytes:
        raise HuangpuNativeInspectionBuildError(
            "runner input volume is smaller than the sealed-input bound"
        )
    if runner_limits["artifact_volume_size_bytes"] < producer_maximum:
        raise HuangpuNativeInspectionBuildError(
            "runner artifact volume is smaller than one producer maximum"
        )


def _validate_resolved_runtime_capacity(
    run: object, plan: dict[str, Any]
) -> None:
    revision = _mapping(plan.get("executor_revision"), "executor revision")
    resources = getattr(getattr(run, "environment"), "harness").resources
    expected_memory = _positive_int(
        revision.get("harness_memory_mib"), "executor harness memory"
    )
    if resources.memory_mib != expected_memory:
        raise HuangpuNativeInspectionBuildError(
            "resolved Harness memory differs from the runtime capacity plan"
        )


def _apply_sized_artifact_limits(
    requirements: object,
    capacity_plan: dict[str, Any],
    *,
    label: str,
) -> None:
    artifact_limits = _mapping(
        capacity_plan.get("artifact_limits"), "artifact capacity limits"
    )
    requirements_by_id: dict[str, dict[str, Any]] = {}
    for item in _array(requirements, label):
        requirement = _mapping(item, f"{label} item")
        artifact_id = requirement.get("artifact_id")
        if not isinstance(artifact_id, str):
            raise HuangpuNativeInspectionBuildError(
                f"{label} contains an artifact without an ID"
            )
        if artifact_id in requirements_by_id:
            raise HuangpuNativeInspectionBuildError(
                f"{label} repeats artifact {artifact_id}"
            )
        requirements_by_id[artifact_id] = requirement
    if not set(SIZED_ARTIFACT_IDS).issubset(requirements_by_id):
        raise HuangpuNativeInspectionBuildError(
            f"{label} omits a sized harness artifact"
        )
    for artifact_id in SIZED_ARTIFACT_IDS:
        requirements_by_id[artifact_id]["max_size_bytes"] = _positive_int(
            artifact_limits[artifact_id], f"{artifact_id} capacity"
        )


def _inspect_local_images(images: dict[str, str]) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for name, reference in images.items():
        completed = subprocess.run(
            ["docker", "image", "inspect", reference],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            raise HuangpuNativeInspectionBuildError(
                f"digest-pinned image is unavailable locally: {name}: "
                f"{detail[-1] if detail else 'docker image inspect failed'}"
            )
        try:
            inspected = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise HuangpuNativeInspectionBuildError(
                f"Docker returned invalid image metadata for {name}"
            ) from exc
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise HuangpuNativeInspectionBuildError(
                f"Docker returned an ambiguous image identity for {name}"
            )
        identity = _mapping(inspected[0], f"Docker image metadata for {name}")
        repo_digests = identity.get("RepoDigests")
        if not isinstance(repo_digests, list) or reference not in repo_digests:
            raise HuangpuNativeInspectionBuildError(
                f"local image metadata does not contain the locked digest: {name}"
            )
        image_id = identity.get("Id")
        if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
            raise HuangpuNativeInspectionBuildError(
                f"local image has no content identity: {name}"
            )
        evidence[name] = {
            "reference": reference,
            "local_image_id": image_id,
        }
    return evidence


def _world_position(world: WorldPackage, target_id: str) -> tuple[float, float, float]:
    target = next(
        (item for item in world.semantic_targets if item.target_id == target_id),
        None,
    )
    if target is None:
        raise HuangpuNativeInspectionBuildError(
            f"native WorldPackage omits required target {target_id}"
        )
    parent = next(
        (item for item in world.entities if item.entity_id == target.parent_entity_id),
        None,
    )
    if parent is None:
        raise HuangpuNativeInspectionBuildError(
            f"target parent entity is absent: {target.parent_entity_id}"
        )
    return (
        parent.pose.east_m + target.pose.x_m,
        parent.pose.north_m + target.pose.y_m,
        parent.pose.up_m + target.pose.z_m,
    )


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (
        third[0] - first[0]
    )


def _on_segment(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> bool:
    return (
        min(first[0], second[0]) - 1e-9 <= point[0] <= max(first[0], second[0]) + 1e-9
        and min(first[1], second[1]) - 1e-9
        <= point[1]
        <= max(first[1], second[1]) + 1e-9
        and abs(_orientation(first, second, point)) <= 1e-9
    )


def _segments_intersect(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
    fourth: tuple[float, float],
) -> bool:
    values = (
        _orientation(first, second, third),
        _orientation(first, second, fourth),
        _orientation(third, fourth, first),
        _orientation(third, fourth, second),
    )
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(
        abs(value) <= 1e-9 and _on_segment(point, edge_first, edge_second)
        for value, point, edge_first, edge_second in (
            (values[0], third, first, second),
            (values[1], fourth, first, second),
            (values[2], first, third, fourth),
            (values[3], second, third, fourth),
        )
    )


def _point_in_polygon(
    point: tuple[float, float], polygon: tuple[tuple[float, float], ...]
) -> bool:
    if any(
        _on_segment(point, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    ):
        return True
    inside = False
    x_value, y_value = point
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if (first[1] > y_value) == (second[1] > y_value):
            continue
        intersection = (second[0] - first[0]) * (y_value - first[1]) / (
            second[1] - first[1]
        ) + first[0]
        if x_value < intersection:
            inside = not inside
    return inside


def _segment_intersects_polygon(
    first: tuple[float, float],
    second: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    if _point_in_polygon(first, polygon) or _point_in_polygon(second, polygon):
        return True
    return any(
        _segments_intersect(
            first,
            second,
            polygon[index],
            polygon[(index + 1) % len(polygon)],
        )
        for index in range(len(polygon))
    )


def _motion_time(
    distance_m: float, speed_mps: float, acceleration_mps2: float
) -> float:
    threshold = speed_mps**2 / acceleration_mps2
    if distance_m < threshold:
        return 2.0 * math.sqrt(distance_m / acceleration_mps2)
    return distance_m / speed_mps + speed_mps / acceleration_mps2


def _route_model(world: WorldPackage) -> dict[str, object]:
    launch = next(
        (item for item in world.launch_sites if item.launch_site_id == LAUNCH_ID),
        None,
    )
    if launch is None:
        raise HuangpuNativeInspectionBuildError(
            f"native WorldPackage omits launch site {LAUNCH_ID}"
        )
    target_positions = tuple(_world_position(world, item) for item in TARGET_IDS)
    sensor = next(
        (item for item in world.sensors if item.sensor_id == SENSOR_ID),
        None,
    )
    if sensor is None:
        raise HuangpuNativeInspectionBuildError(
            f"native WorldPackage omits sensor {SENSOR_ID}"
        )
    navigation_points = tuple(
        (
            position[0] - STANDOFF_M - sensor.pose.x_m,
            position[1] - sensor.pose.y_m,
            position[2] - sensor.pose.z_m,
        )
        for position in target_positions
    )
    if any(abs(item[0] - navigation_points[0][0]) > 0.05 for item in navigation_points):
        raise HuangpuNativeInspectionBuildError(
            "reference participant requires west-aligned city targets"
        )

    launch_xy = (launch.pose.east_m, launch.pose.north_m)
    first_corner = (navigation_points[0][0], launch.pose.north_m)
    target_xy = (navigation_points[0][0], navigation_points[0][1])
    outbound_legs = ((launch_xy, first_corner), (first_corner, target_xy))
    outbound_horizontal_m = sum(math.dist(*item) for item in outbound_legs)
    complete_horizontal_m = 2.0 * outbound_horizontal_m
    if outbound_horizontal_m <= 50.1:
        raise HuangpuNativeInspectionBuildError(
            "city route cannot meet the formal pre-observation ground-track threshold"
        )

    geofence = next(
        (item for item in world.regions if item.region_id == GEOFENCE_ID),
        None,
    )
    no_fly = next(
        (item for item in world.regions if item.region_id == NO_FLY_ID),
        None,
    )
    if geofence is None or geofence.kind != "geofence":
        raise HuangpuNativeInspectionBuildError("city operational geofence is absent")
    if no_fly is None or no_fly.kind != "no_fly":
        raise HuangpuNativeInspectionBuildError("city no-fly prism is absent")
    geofence_polygon = tuple(
        (item.east_m, item.north_m) for item in geofence.geometry.footprint_enu_m
    )
    no_fly_polygon = tuple(
        (item.east_m, item.north_m) for item in no_fly.geometry.footprint_enu_m
    )
    route_points = (launch_xy, first_corner, target_xy)
    if any(not _point_in_polygon(item, geofence_polygon) for item in route_points):
        raise HuangpuNativeInspectionBuildError(
            "reference route exits the operational geofence"
        )
    if any(
        _segment_intersects_polygon(first, second, no_fly_polygon)
        for first, second in outbound_legs
    ):
        raise HuangpuNativeInspectionBuildError(
            "reference route intersects the declared no-fly prism"
        )

    intersected_buildings: list[dict[str, object]] = []
    for building in world.buildings:
        footprint = tuple(
            (item.east_m, item.north_m) for item in building.geometry.footprint_enu_m
        )
        if any(
            _segment_intersects_polygon(first, second, footprint)
            for first, second in outbound_legs
        ):
            intersected_buildings.append(
                {
                    "building_id": building.building_id,
                    "top_altitude_m": building.geometry.top_altitude_m,
                }
            )
    maximum_roof_m = max(
        (float(item["top_altitude_m"]) for item in intersected_buildings),
        default=0.0,
    )
    roof_clearance_m = CRUISE_UP_M - maximum_roof_m
    if roof_clearance_m < MINIMUM_ROOF_CLEARANCE_M:
        raise HuangpuNativeInspectionBuildError(
            "declared cruise altitude has insufficient source-building clearance: "
            f"{roof_clearance_m:.3f} m"
        )

    center_distance_m = math.dist(launch_xy, target_xy)
    shortest_path_lower_bound_m = 2.0 * max(0.0, center_distance_m - 12.0)
    horizontal_time_s = 2.0 * sum(
        _motion_time(
            math.dist(*item),
            ENGINEERING_CRUISE_MPS,
            ENGINEERING_HORIZONTAL_ACCELERATION_MPS2,
        )
        for item in outbound_legs
    )
    vertical_distances_m = (
        CRUISE_UP_M,
        CRUISE_UP_M - navigation_points[0][2],
        CRUISE_UP_M - navigation_points[0][2],
        CRUISE_UP_M - navigation_points[1][2],
        CRUISE_UP_M - navigation_points[1][2],
        CRUISE_UP_M,
    )
    vertical_time_s = sum(
        _motion_time(
            item,
            ENGINEERING_VERTICAL_SPEED_MPS,
            ENGINEERING_VERTICAL_ACCELERATION_MPS2,
        )
        for item in vertical_distances_m
    )
    motion_leg_count = len(outbound_legs) * 2 + len(vertical_distances_m)
    engineering_total_s = (
        horizontal_time_s
        + vertical_time_s
        + len(TARGET_IDS) * DWELL_S
        + motion_leg_count
        + 2.0
    )
    if engineering_total_s >= MISSION_DEADLINE_S:
        raise HuangpuNativeInspectionBuildError(
            "engineering route estimate exceeds the unchanged mission deadline"
        )
    return {
        "launch_enu_m": list((*launch_xy, launch.pose.up_m)),
        "target_enu_m": [list(item) for item in target_positions],
        "navigation_enu_m": [list(item) for item in navigation_points],
        "outbound_horizontal_m": outbound_horizontal_m,
        "complete_horizontal_m": complete_horizontal_m,
        "shortest_path_lower_bound_m": shortest_path_lower_bound_m,
        "cruise_up_m": CRUISE_UP_M,
        "intersected_buildings_at_cruise": intersected_buildings,
        "maximum_intersected_roof_m": maximum_roof_m,
        "roof_clearance_m": roof_clearance_m,
        "engineering_assumptions": {
            "horizontal_speed_mps": ENGINEERING_CRUISE_MPS,
            "horizontal_acceleration_mps2": (ENGINEERING_HORIZONTAL_ACCELERATION_MPS2),
            "vertical_speed_mps": ENGINEERING_VERTICAL_SPEED_MPS,
            "vertical_acceleration_mps2": ENGINEERING_VERTICAL_ACCELERATION_MPS2,
            "settle_allowance_s_per_motion_leg": 1.0,
        },
        "engineering_estimated_total_s": engineering_total_s,
        "mission_deadline_s": MISSION_DEADLINE_S,
        "measurement_status": "theoretical_assumptions_not_flight_evidence",
    }


def _instruction() -> str:
    return """# Huangpu native facade inspection v1

Execute both declared facade work orders through the Gateway. Use only public
scenario geometry for navigation and actual camera observations for defect
classification. Take off above 12.5 m AGL, remain inside the operational
geofence, avoid the declared no-fly prism, and obtain two distinct physical
frames for each target. Return to the selected launch pad and land before
sending the aggregate report from `endpoint.uav` to `endpoint.operations`.
Wait for actual ns-3 delivery and Business completion; a queued receipt is not
completion. Write the declared detection and report artifacts, remain stopped
for two seconds, disarm, and finish. Command acknowledgements are not physical
completion.
"""


def _task_package(
    root: Path,
    template: Path,
    route: dict[str, object],
    capacity_plan: dict[str, Any],
) -> tuple[InspectionTaskPackage, Path, Path]:
    target_asset = root / "assets/inspection-target.sdf"
    _copy_regular_file(template / "assets/inspection-target.sdf", target_asset)
    truth = [
        {
            "source_artifact_id": "artifact.truth",
            "defect_id": defect_id,
            "target_id": target_id,
            "geometrically_visible": True,
        }
        for defect_id, target_id in zip(DEFECT_IDS, TARGET_IDS, strict=True)
    ]
    truth_path = _write_bytes(root / "private/truth.json", canonical_json_bytes(truth))

    raw = _read_json(
        template / "task/inspection-package.json",
        "reference inspection task package",
    )
    raw["task_id"] = TASK_ID
    _apply_sized_artifact_limits(
        raw.get("artifact_requirements"),
        capacity_plan,
        label="inspection package artifact requirements",
    )
    assets = _array(raw.get("assets"), "inspection assets")
    for asset in assets:
        item = _mapping(asset, "inspection asset")
        if item.get("asset_id") == "asset.inspection-target-model":
            item["file"] = _ref(root, target_asset)
        elif item.get("asset_id") == "asset.inspection-truth":
            item["file"] = _ref(root, truth_path)
        else:
            raise HuangpuNativeInspectionBuildError(
                f"unsupported reference inspection asset: {item.get('asset_id')}"
            )
    raw["work_orders"] = [
        {
            "work_order_id": work_order_id,
            "target_id": target_id,
            "required_observation_id": observation_id,
            "arrival_tolerance_m": 14.0,
            "report_artifact_type": "inspection.report",
            "upload_deadline_ns": int(MISSION_DEADLINE_S * 1e9),
        }
        for work_order_id, target_id, observation_id in zip(
            WORK_ORDER_IDS,
            TARGET_IDS,
            OBSERVATION_IDS,
            strict=True,
        )
    ]
    observation_schema = _ref(root, root / "schemas/observation.json")
    raw["observations"] = [
        {
            "observation_id": observation_id,
            "target_id": target_id,
            "simulation_asset_id": "asset.inspection-target-model",
            "metadata_schema": observation_schema,
            "media_type": "application/json",
            "trigger": {
                "observation_id": observation_id,
                "target_id": target_id,
                "provider_id": FLIGHT_ID,
                "camera_id": SENSOR_ID,
                "min_distance_m": 8.0,
                "max_distance_m": 12.0,
                "min_view_angle_deg": 0.0,
                "max_view_angle_deg": 12.0,
                "earliest_time_ns": 0,
                "latest_time_ns": int(MISSION_DEADLINE_S * 1e9),
            },
        }
        for observation_id, target_id in zip(OBSERVATION_IDS, TARGET_IDS, strict=True)
    ]
    bounds = _mapping(raw.get("bounds"), "inspection bounds")
    bounds["mission"] = {
        "shortest_path_m": route["shortest_path_lower_bound_m"],
        "max_speed_mps": REFERENCE_SPEED_CEILING_MPS,
        "fixed_time_s": 30.0,
        "inspection_dwell_s": len(TARGET_IDS) * DWELL_S,
        "deadline_s": MISSION_DEADLINE_S,
    }
    bounds["link"] = {
        "minimum_payload_bytes": 128,
        "max_bandwidth_bps": 6_000_000.0,
        "minimum_latency_s": 0.02,
        "upload_deadline_s": MISSION_DEADLINE_S,
    }
    bounds["imaging"] = {
        "defect_size_m": 0.22,
        "standoff_distance_m": 12.0,
        "focal_length_m": 0.0011440834488904417,
        "pixel_pitch_m": 0.000003,
        "minimum_resolvable_pixels": 4.0,
    }
    bounds["total_defects"] = len(TARGET_IDS)
    bounds["geometrically_visible_defects"] = len(TARGET_IDS)
    package = InspectionTaskPackage.model_validate(raw)
    package_path = _write_json(
        root / "task/inspection-package.json",
        package.model_dump(mode="json"),
    )
    return package, package_path, truth_path


def _configs(
    root: Path,
    template: Path,
    world: WorldPackage,
    package: InspectionTaskPackage,
) -> dict[str, Path]:
    px4_raw = _read_json(template / "configs/px4.json", "reference PX4 config")
    vehicle = next(item for item in world.entities if item.entity_id == UAV_ID)
    vehicles = _array(px4_raw.get("vehicles"), "PX4 vehicles")
    if len(vehicles) != 1:
        raise HuangpuNativeInspectionBuildError(
            "reference PX4 config must declare exactly one vehicle"
        )
    px4_vehicle = _mapping(vehicles[0], "PX4 vehicle")
    px4_vehicle["initial_pose"] = {
        "x_m": vehicle.pose.east_m,
        "y_m": vehicle.pose.north_m,
        "z_m": vehicle.pose.up_m,
        "roll_rad": 0.0,
        "pitch_rad": 0.0,
        "yaw_rad": 0.0,
    }
    px4 = Px4GazeboConfig.model_validate(px4_raw)
    ns3 = Ns3Config.model_validate(
        _read_json(template / "configs/ns3.json", "reference ns-3 config")
    )
    sumo = SumoConfig.model_validate(
        _read_json(template / "configs/sumo.json", "reference SUMO config")
    )
    business = InspectionBusinessConfig(
        schema_version="aero-bench.inspection-business/v1",
        provider_id=BUSINESS_ID,
        task_package=package,
    )
    verifier_raw = _read_json(
        template / "configs/verifier.json",
        "reference verifier config",
    )
    verifier_raw.update(
        {
            "package_id": PACKAGE_ID,
            "vehicle_id": UAV_ID,
            "sensor_id": SENSOR_ID,
            "target_ids": list(TARGET_IDS),
            "geofence_ids": [GEOFENCE_ID],
            "no_fly_ids": [NO_FLY_ID],
            "network_deadline_s": MISSION_DEADLINE_S,
        }
    )
    verifier = InspectionFormalVerifierConfigV3.model_validate(verifier_raw)
    models = {
        "px4": px4,
        "ns3": ns3,
        "sumo": sumo,
        "business": business,
        "verifier": verifier,
    }
    return {
        name: _write_json(
            root / f"configs/{name}.json",
            model.model_dump(mode="json"),
        )
        for name, model in models.items()
    }


def _policy(root: Path) -> Path:
    policy = InspectionReferencePolicy.model_validate(
        {
            "schema_version": "aero-bench.inspection-reference-policy/v1",
            "vehicle_id": UAV_ID,
            "work_orders": [
                {
                    "work_order_id": work_order_id,
                    "target_id": target_id,
                    "observation_id": observation_id,
                    "defect_id": defect_id,
                }
                for work_order_id, target_id, observation_id, defect_id in zip(
                    WORK_ORDER_IDS,
                    TARGET_IDS,
                    OBSERVATION_IDS,
                    DEFECT_IDS,
                    strict=True,
                )
            ],
            "source_endpoint_id": "endpoint.uav",
            "destination_endpoint_id": "endpoint.operations",
            "cruise_up_m": CRUISE_UP_M,
            "standoff_m": STANDOFF_M,
            "dwell_s": DWELL_S,
            "delivery_wait_s": 30.0,
        }
    )
    return _write_json(
        root / "agent/reference-policy.json",
        policy.model_dump(mode="json"),
    )


def _runtime_specs(
    root: Path,
    template: Path,
    images: dict[str, str],
    image_lock: dict[str, Any],
    capacity_plan: dict[str, Any],
    runtime_capacity_plan: dict[str, Any],
    configs: dict[str, Path],
    package_path: Path,
    policy_path: Path,
) -> tuple[Path, Path, Path, Path]:
    instruction_path = _write_bytes(root / "instruction.md", _instruction().encode())

    task_raw = _read_yaml(template / "task/task.yaml", "reference TaskSpec")
    task_raw["task_id"] = TASK_ID
    task_package = _mapping(task_raw.get("package"), "TaskSpec package")
    task_package["package_id"] = PACKAGE_ID
    task_config = _mapping(task_package.get("config"), "TaskSpec package config")
    task_config["file"] = _ref(root, package_path)
    task_raw["instruction"] = _ref(root, instruction_path)
    task_assets = _array(task_raw.get("assets"), "TaskSpec assets")
    asset_paths = {
        "asset.inspection-target-model": root / "assets/inspection-target.sdf",
        "asset.inspection-truth": root / "private/truth.json",
        "asset.reference-inspection-policy": policy_path,
    }
    if {item.get("asset_id") for item in task_assets if isinstance(item, dict)} != set(
        asset_paths
    ):
        raise HuangpuNativeInspectionBuildError(
            "reference TaskSpec contains an unexpected task asset set"
        )
    for item in task_assets:
        asset = _mapping(item, "TaskSpec asset")
        asset["file"] = _ref(root, asset_paths[str(asset["asset_id"])])
    verifier = _mapping(task_raw.get("verifier"), "TaskSpec verifier")
    _apply_sized_artifact_limits(
        verifier.get("artifact_requirements"),
        capacity_plan,
        label="verifier artifact requirements",
    )
    verifier_config = _mapping(verifier.get("config"), "TaskSpec verifier config")
    verifier_config["file"] = _ref(root, configs["verifier"])
    verifier_workload = _mapping(verifier.get("workload"), "verifier workload")
    _mapping(verifier_workload.get("runtime"), "verifier runtime")["image"] = images[
        "verifier"
    ]
    task = TaskSpec.model_validate(task_raw)
    task_path = _write_yaml(root / "task/task.yaml", task.model_dump(mode="json"))

    environment_raw = _read_yaml(
        template / "environment/environment.yaml",
        "reference EnvironmentSpec",
    )
    environment_raw["environment_id"] = ENVIRONMENT_ID
    target_capacity = _mapping(capacity_plan.get("target"), "capacity target")
    clock = _mapping(environment_raw.get("clock"), "EnvironmentSpec clock")
    if clock.get("max_steps") != target_capacity.get("max_steps"):
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan uses a different maximum step count"
        )
    if clock.get("step_ns") != target_capacity.get("step_ns"):
        raise HuangpuNativeInspectionBuildError(
            "artifact capacity plan uses a different clock step"
        )
    _apply_sized_artifact_limits(
        environment_raw.get("harness_artifact_requirements"),
        capacity_plan,
        label="harness artifact requirements",
    )
    harness = _mapping(environment_raw.get("harness"), "harness workload")
    _mapping(harness.get("runtime"), "harness runtime")["image"] = images["harness"]
    revision = _mapping(
        runtime_capacity_plan.get("executor_revision"), "executor revision"
    )
    _mapping(harness.get("resources"), "harness resources")["memory_mib"] = (
        _positive_int(
            revision.get("harness_memory_mib"), "executor harness memory"
        )
    )
    provider_keys = {
        FLIGHT_ID: ("flight", "px4"),
        NETWORK_ID: ("network", "ns3"),
        TRAFFIC_ID: ("traffic", "sumo"),
        BUSINESS_ID: ("business", "business"),
    }
    providers = _array(environment_raw.get("providers"), "EnvironmentSpec providers")
    if {item.get("provider_id") for item in providers if isinstance(item, dict)} != set(
        provider_keys
    ):
        raise HuangpuNativeInspectionBuildError(
            "reference EnvironmentSpec contains an unexpected Provider set"
        )
    for item in providers:
        provider = _mapping(item, "EnvironmentSpec Provider")
        provider_id = str(provider["provider_id"])
        image_key, config_key = provider_keys[provider_id]
        workload = _mapping(provider.get("workload"), "Provider workload")
        _mapping(workload.get("runtime"), "Provider runtime")["image"] = images[
            image_key
        ]
        config = _mapping(provider.get("config"), "Provider config")
        config["file"] = _ref(root, configs[config_key])
    environment = EnvironmentSpec.model_validate(environment_raw)
    environment_path = _write_yaml(
        root / "environment/environment.yaml",
        environment.model_dump(mode="json"),
    )

    agent_raw = _read_yaml(template / "agent/participant.yaml", "reference AgentSpec")
    agent_workload = _mapping(agent_raw.get("workload"), "agent workload")
    _mapping(agent_workload.get("runtime"), "agent runtime")["image"] = images["agent"]
    agent_metadata = _mapping(image_lock.get("agent"), "image lock agent metadata")
    implementation = _mapping(
        agent_workload.get("implementation"), "agent implementation"
    )
    for field in ("source_uri", "source_revision", "version"):
        value = agent_metadata.get(field)
        if not isinstance(value, str) or not value:
            raise HuangpuNativeInspectionBuildError(
                f"image lock agent metadata omits {field}"
            )
        implementation[field] = value
    observations = _array(agent_raw.get("observations"), "AgentSpec observations")
    flight_observations = [
        item
        for item in observations
        if isinstance(item, dict) and item.get("provider_id") == FLIGHT_ID
    ]
    if len(flight_observations) != len(OBSERVATION_IDS):
        raise HuangpuNativeInspectionBuildError(
            "reference AgentSpec must expose exactly two flight observations"
        )
    for item, observation_id in zip(flight_observations, OBSERVATION_IDS, strict=True):
        item["observation_id"] = observation_id
    agent = AgentSpec.model_validate(agent_raw)
    agent_path = _write_yaml(
        root / "agent/participant.yaml", agent.model_dump(mode="json")
    )

    suite_raw = _read_yaml(template / "suite.yaml", "reference SuiteSpec")
    suite_raw["suite_id"] = SUITE_ID
    cases = _array(suite_raw.get("cases"), "SuiteSpec cases")
    if len(cases) != 1:
        raise HuangpuNativeInspectionBuildError(
            "reference SuiteSpec must contain exactly one case"
        )
    case = _mapping(cases[0], "SuiteSpec case")
    case.update(
        {
            "case_id": CASE_ID,
            "task": _ref(root, task_path),
            "environment": _ref(root, environment_path),
            "agents": [_ref(root, agent_path)],
            "world_package": _ref(root, root / "world/package.json"),
            "launch_site_ids": [LAUNCH_ID],
            "seeds": [SEED],
            "axes": [],
        }
    )
    suite = SuiteSpec.model_validate(suite_raw)
    suite_path = _write_yaml(root / "suite.yaml", suite.model_dump(mode="json"))
    return task_path, environment_path, agent_path, suite_path


def _runner_config(
    path: Path,
    output_root: Path,
    verifier_image: str,
    capacity_plan: dict[str, Any],
    runtime_capacity_plan: dict[str, Any],
) -> RunnerConfig:
    if not output_root.is_absolute():
        raise HuangpuNativeInspectionBuildError(
            "--run-output-root must be an absolute path"
        )
    runner_limits = _mapping(
        capacity_plan.get("runner_limits"), "runner capacity limits"
    )
    revision = _mapping(
        runtime_capacity_plan.get("executor_revision"), "executor revision"
    )
    runner = RunnerConfig.model_validate(
        {
            "schema_version": "aero-bench.runner-config/v1",
            "executor_kind": "docker_reference",
            "output_root": str(output_root),
            "runtime_timeout_seconds": _positive_int(
                revision.get("runtime_timeout_seconds"),
                "executor runtime timeout",
            ),
            "verifier_timeout_seconds": 1800,
            "readiness_timeout_seconds": 480,
            "docker_binary": "docker",
            "input_mount_path": "/run/aero-input",
            "artifact_mount_path": "/run/aero-artifacts",
            "seal_mount_path": "/run/aero-seal",
            "volume_keeper_mount_path": "/run/aero-held",
            "input_volume_size_bytes": _positive_int(
                runner_limits["input_volume_size_bytes"],
                "runner input volume capacity",
            ),
            "artifact_volume_size_bytes": _positive_int(
                runner_limits["artifact_volume_size_bytes"],
                "runner artifact volume capacity",
            ),
            "scratch_size_bytes": _positive_int(
                runner_limits["scratch_size_bytes"],
                "runner scratch capacity",
            ),
            "pids_limit": 2048,
            "workload_uid": 65532,
            "workload_gid": 65532,
            "provider_bind_host": "0.0.0.0",
            "gateway_bind_host": "0.0.0.0",
            "volume_keeper_image": verifier_image,
            "volume_keeper_command": ["/bin/sleep", "infinity"],
            "volume_keeper_cpu_millicores": 50,
            "volume_keeper_memory_mib": 64,
            "attempt_id": None,
            "model_auth_file": None,
            "model_https_proxy": None,
        }
    )
    _write_yaml(path, runner.model_dump(mode="json", exclude_none=True))
    return runner


def _copy_template_schemas(template: Path, root: Path) -> dict[str, str]:
    source = template / "schemas"
    if source.is_symlink() or not source.is_dir():
        raise HuangpuNativeInspectionBuildError(
            "reference contract template has no regular schemas directory"
        )
    schema_digests: dict[str, str] = {}
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.is_symlink() or path.suffix != ".json":
            raise HuangpuNativeInspectionBuildError(
                f"reference schema entry is unsupported: {path}"
            )
        destination = root / "schemas" / path.name
        _copy_regular_file(path, destination)
        schema_digests[path.name] = sha256_file(destination)
    if len(schema_digests) != 20:
        raise HuangpuNativeInspectionBuildError(
            "reference contract template must contain exactly 20 schemas"
        )
    return schema_digests


def _install(staging: Path, bundle_root: Path) -> list[Path]:
    installed: list[Path] = []
    for relative in GENERATED_PATHS[:10]:
        source = staging / relative
        if not source.exists():
            raise HuangpuNativeInspectionBuildError(
                f"generated payload is incomplete: {relative}"
            )
    for relative in GENERATED_PATHS[:10]:
        source = staging / relative
        destination = bundle_root / relative
        os.replace(source, destination)
        installed.append(destination)
    return installed


def _cleanup(paths: list[Path]) -> None:
    for path in reversed(paths):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()


def build(
    *,
    input_lock: Path,
    bundle_root: Path,
    contract_template: Path,
    image_lock_path: Path,
    capacity_plan_path: Path,
    runtime_capacity_plan_path: Path,
    runner_config_path: Path,
    run_output_root: Path,
    report_path: Path,
) -> dict[str, object]:
    definition = load_city_native_input_lock(input_lock)
    world_only_definition = definition.model_copy(update={"execution": None})
    readiness = assess_city_native_registration(REPOSITORY_ROOT, world_only_definition)
    if definition.world is None:
        raise HuangpuNativeInspectionBuildError(
            "city input lock has no native WorldPackage binding"
        )
    expected_bundle = REPOSITORY_ROOT / definition.world.bundle_root
    if bundle_root.resolve(strict=True) != expected_bundle.resolve(strict=True):
        raise HuangpuNativeInspectionBuildError(
            "--bundle-root differs from the registered WorldPackage bundle_root"
        )
    world_path = bundle_root / "world/package.json"
    world = WorldPackage.model_validate_json(world_path.read_bytes())
    if world.world_id != definition.world.world_id:
        raise HuangpuNativeInspectionBuildError(
            "native WorldPackage identity differs from the input lock"
        )
    if any(code.startswith("source.") for code in readiness.blocking_codes):
        raise HuangpuNativeInspectionBuildError(
            "accepted city source verification is blocked"
        )
    capacity_plan = _load_capacity_plan(capacity_plan_path, world)
    runtime_capacity_plan = _load_runtime_capacity_plan(
        runtime_capacity_plan_path,
        world=world,
        artifact_capacity_path=capacity_plan_path,
    )
    route = _route_model(world)
    images, image_lock = _load_images(image_lock_path)
    local_images = _inspect_local_images(images)
    contract_template = contract_template.resolve(strict=True)
    _require_new_path(runner_config_path, "runner config")
    _require_new_path(report_path, "build report")
    for relative in GENERATED_PATHS:
        _require_new_path(bundle_root / relative, f"generated bundle path {relative}")

    installed: list[Path] = []
    runner_written = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=".huangpu-native-inspection-",
            dir=bundle_root.parent,
        ) as temporary:
            staging = Path(temporary) / "payload"
            staging.mkdir()
            # Resolution needs the registered world bytes at their final
            # relative path.  Copy only package.json into staging for FileRef
            # creation; the complete world remains immutable at bundle_root.
            (staging / "world").mkdir()
            (staging / "provenance").mkdir()
            _copy_regular_file(world_path, staging / "world/package.json")
            schema_digests = _copy_template_schemas(contract_template, staging)
            package, package_path, _truth_path = _task_package(
                staging,
                contract_template,
                route,
                capacity_plan,
            )
            configs = _configs(
                staging,
                contract_template,
                world,
                package,
            )
            policy_path = _policy(staging)
            _runtime_specs(
                staging,
                contract_template,
                images,
                image_lock,
                capacity_plan,
                runtime_capacity_plan,
                configs,
                package_path,
                policy_path,
            )
            # world/package.json is already present in the immutable bundle.
            shutil.rmtree(staging / "world")
            installed = _install(staging, bundle_root)

        runs = resolve_suite(
            str(bundle_root / "suite.yaml"),
            executor_kind="docker_reference",
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=builtin_provider_registry(),
        )
        if len(runs) != 1:
            raise HuangpuNativeInspectionBuildError(
                "city Suite did not resolve to exactly one run"
            )
        run = runs[0]
        if not run.feasibility.feasible:
            raise HuangpuNativeInspectionBuildError(
                f"city run is infeasible: {run.feasibility.failed_conditions}"
            )
        _validate_resolved_capacity(run, capacity_plan)
        _validate_resolved_runtime_capacity(run, runtime_capacity_plan)
        _write_json(
            bundle_root / "resolved-run.json",
            run.model_dump(mode="json"),
        )
        installed.append(bundle_root / "resolved-run.json")
        _write_json(
            bundle_root / "compiled-resolved-runs.json",
            [run.model_dump(mode="json")],
        )
        installed.append(bundle_root / "compiled-resolved-runs.json")

        source_revision = image_lock.get("runtime_source_revision")
        if not isinstance(source_revision, str) or len(source_revision) != 64:
            raise HuangpuNativeInspectionBuildError(
                "image lock has no exact runtime source revision"
            )
        provenance = {
            "schema_version": BUILD_REPORT_SCHEMA,
            "status": "formal-city-inspection-resolved-not-executed",
            "world_id": world.world_id,
            "world_digest": world.world_digest,
            "task_id": TASK_ID,
            "suite_id": SUITE_ID,
            "case_id": CASE_ID,
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "execution_scope": run.execution_scope,
            "executor_kind": run.executor_kind,
            "runtime_source_revision": source_revision,
            "route_model": route,
            "feasibility": run.feasibility.model_dump(mode="json"),
            "schema_digests": schema_digests,
            "local_images": local_images,
            "artifact_capacity": {
                "plan": {
                    "path": _repository_relative(capacity_plan_path),
                    "sha256": sha256_file(capacity_plan_path),
                    "size_bytes": capacity_plan_path.stat().st_size,
                },
                "artifact_limits": capacity_plan["artifact_limits"],
                "capacity": capacity_plan["capacity"],
                "runner_limits": capacity_plan["runner_limits"],
            },
            "runtime_capacity": {
                "plan": {
                    "path": _repository_relative(runtime_capacity_plan_path),
                    "sha256": sha256_file(runtime_capacity_plan_path),
                    "size_bytes": runtime_capacity_plan_path.stat().st_size,
                },
                "executor_revision": runtime_capacity_plan["executor_revision"],
                "host_feasibility": runtime_capacity_plan["host_feasibility"],
                "public_trace_delivery_model": runtime_capacity_plan[
                    "public_trace_delivery_model"
                ],
            },
            "formal_execution_started": False,
            "unsupported_full-city_domains": [
                "weather-physics",
                "airspace-enforcement",
                "physical-logistics",
            ],
        }
        provenance_path = _write_json(
            bundle_root / "provenance/city-inspection-lowering.json",
            provenance,
            pretty=True,
        )

        runner_config_path.parent.mkdir(parents=True, exist_ok=True)
        _runner_config(
            runner_config_path,
            run_output_root.resolve(),
            images["verifier"],
            capacity_plan,
            runtime_capacity_plan,
        )
        runner_written = True
        report = {
            **provenance,
            "suite": {
                "path": _repository_relative(bundle_root / "suite.yaml"),
                "sha256": sha256_file(bundle_root / "suite.yaml"),
                "size_bytes": (bundle_root / "suite.yaml").stat().st_size,
            },
            "runner_config": {
                "path": _repository_relative(runner_config_path),
                "sha256": sha256_file(runner_config_path),
                "size_bytes": runner_config_path.stat().st_size,
            },
            "image_lock": {
                "path": _repository_relative(image_lock_path),
                "sha256": sha256_file(image_lock_path),
                "size_bytes": image_lock_path.stat().st_size,
            },
            "capacity_plan": {
                "path": _repository_relative(capacity_plan_path),
                "sha256": sha256_file(capacity_plan_path),
                "size_bytes": capacity_plan_path.stat().st_size,
            },
            "runtime_capacity_plan": {
                "path": _repository_relative(runtime_capacity_plan_path),
                "sha256": sha256_file(runtime_capacity_plan_path),
                "size_bytes": runtime_capacity_plan_path.stat().st_size,
            },
            "provenance_path": _repository_relative(provenance_path),
        }
        _write_json(report_path, report, pretty=True)
        return report
    except Exception:
        if report_path.exists():
            report_path.unlink()
        if runner_written and runner_config_path.exists():
            runner_config_path.unlink()
        _cleanup(installed)
        raise


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build(
            input_lock=Path(args.input_lock),
            bundle_root=Path(args.bundle_root),
            contract_template=Path(args.contract_template),
            image_lock_path=Path(args.image_lock),
            capacity_plan_path=Path(args.capacity_plan),
            runtime_capacity_plan_path=Path(args.runtime_capacity_plan),
            runner_config_path=Path(args.runner_config),
            run_output_root=Path(args.run_output_root),
            report_path=Path(args.report),
        )
    except (
        HuangpuNativeInspectionBuildError,
        CityNativeRegistrationError,
        ValueError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "run_id": report["run_id"],
                "scenario_digest": report["scenario_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
