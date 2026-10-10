#!/usr/bin/env python3
"""Size Huangpu harness artifacts from retained native-runtime evidence.

The failed v1 city attempt completed enough provider-barrier ticks to measure
the serialization cost of the full 525-entity scene.  This command verifies
that evidence, projects its largest completed-tick payload across the declared
run horizon, adds fixed headroom, and emits immutable artifact and Docker
volume limits for the next versioned lowering.  It does not start a workload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.artifacts import seal_manifest_size_upper_bound  # noqa: E402
from aero_bench.config.models import ArtifactRequirement  # noqa: E402
from aero_bench.runner.contracts import RunnerConfig  # noqa: E402


REPORT_SCHEMA = "aero-bench.huangpu-native-artifact-capacity/v1"
GIB = 1024**3
HEADROOM_NUMERATOR = 5
HEADROOM_DENOMINATOR = 4
MINIMUM_COMPLETED_TICKS = 32
SIZED_ARTIFACT_IDS = (
    "artifact.event-log",
    "artifact.scene-states",
)


class HuangpuNativeArtifactSizingError(ValueError):
    """Retained evidence cannot support a bounded city artifact projection."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--resolved-run", type=Path, required=True)
    parser.add_argument("--runner-config", type=Path, required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--scene-states", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HuangpuNativeArtifactSizingError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise HuangpuNativeArtifactSizingError(f"{label} must be a JSON array")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HuangpuNativeArtifactSizingError(
                f"JSON object contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(
            path.read_bytes(), object_pairs_hook=_reject_duplicate_pairs
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HuangpuNativeArtifactSizingError(
            f"invalid {label}: {path}: {exc}"
        ) from exc
    return _mapping(document, label)


def _repository_file(root: Path, path: Path, label: str) -> tuple[Path, str]:
    if path.is_symlink():
        raise HuangpuNativeArtifactSizingError(
            f"{label} cannot be a symbolic link: {path}"
        )
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HuangpuNativeArtifactSizingError(
            f"{label} is unavailable: {path}"
        ) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise HuangpuNativeArtifactSizingError(
            f"{label} must be a regular repository file: {path}"
        )
    return resolved, resolved.relative_to(root).as_posix()


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HuangpuNativeArtifactSizingError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HuangpuNativeArtifactSizingError(
            f"{label} must be a non-negative integer"
        )
    return value


def _ceil_fraction(value: int, numerator: int, denominator: int) -> int:
    return (value * numerator + denominator - 1) // denominator


def _ceil_to_quantum(value: int, quantum: int) -> int:
    if value <= 0 or quantum <= 0:
        raise HuangpuNativeArtifactSizingError(
            "capacity values and rounding quantum must be positive"
        )
    return ((value + quantum - 1) // quantum) * quantum


def _next_power_of_two(value: int) -> int:
    if value <= 0:
        raise HuangpuNativeArtifactSizingError("volume requirement must be positive")
    return 1 << (value - 1).bit_length()


def _measure_event_log(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    tick_bytes: dict[int, int] = {}
    entity_state_counts: dict[int, int] = {}
    run_ids: set[str] = set()
    line_count = 0
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise HuangpuNativeArtifactSizingError(
            f"cannot read retained event log: {path}"
        ) from exc
    with stream:
        for line_count, raw in enumerate(stream, start=1):
            digest.update(raw)
            if not raw.endswith(b"\n"):
                raise HuangpuNativeArtifactSizingError(
                    f"event log line {line_count} is not newline terminated"
                )
            try:
                envelope = _mapping(json.loads(raw), "event-log envelope")
                event = _mapping(envelope.get("event"), "event-log event")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HuangpuNativeArtifactSizingError(
                    f"invalid event log line {line_count}: {exc}"
                ) from exc
            expected_sequence = line_count - 1
            if envelope.get("sequence") != expected_sequence:
                raise HuangpuNativeArtifactSizingError(
                    f"event log sequence breaks at line {line_count}"
                )
            if event.get("sequence") != expected_sequence:
                raise HuangpuNativeArtifactSizingError(
                    f"nested event sequence breaks at line {line_count}"
                )
            time = _mapping(event.get("time"), "event time")
            tick = _nonnegative_int(time.get("tick"), "event tick")
            run_id = event.get("run_id")
            if not isinstance(run_id, str) or len(run_id) != 64:
                raise HuangpuNativeArtifactSizingError(
                    f"event log line {line_count} has no exact Run ID"
                )
            run_ids.add(run_id)
            tick_bytes[tick] = tick_bytes.get(tick, 0) + len(raw)
            if event.get("event_type") == "scene.entity-state":
                entity_state_counts[tick] = entity_state_counts.get(tick, 0) + 1
    if line_count == 0:
        raise HuangpuNativeArtifactSizingError("retained event log is empty")
    if len(run_ids) != 1:
        raise HuangpuNativeArtifactSizingError(
            "retained event log contains more than one Run ID"
        )
    completed_ticks = sorted(tick for tick in tick_bytes if tick > 0)
    if len(completed_ticks) < MINIMUM_COMPLETED_TICKS:
        raise HuangpuNativeArtifactSizingError(
            "retained event log has too few completed runtime ticks for sizing"
        )
    if completed_ticks != list(range(1, completed_ticks[-1] + 1)):
        raise HuangpuNativeArtifactSizingError(
            "retained event log runtime ticks are not contiguous from tick 1"
        )
    return {
        "run_id": next(iter(run_ids)),
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
        "line_count": line_count,
        "tick_min": min(tick_bytes),
        "tick_max": max(tick_bytes),
        "completed_tick_count": len(completed_ticks),
        "tick_zero_bytes": tick_bytes.get(0, 0),
        "runtime_tick_bytes_min": min(tick_bytes[tick] for tick in completed_ticks),
        "runtime_tick_bytes_max": max(tick_bytes[tick] for tick in completed_ticks),
        "runtime_tick_bytes_mean": (
            sum(tick_bytes[tick] for tick in completed_ticks) // len(completed_ticks)
        ),
        "entity_state_count_min": min(
            entity_state_counts.get(tick, 0) for tick in completed_ticks
        ),
        "entity_state_count_max": max(
            entity_state_counts.get(tick, 0) for tick in completed_ticks
        ),
    }


def _measure_scene_states(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    line_sizes: list[int] = []
    ticks: list[int] = []
    run_ids: set[str] = set()
    declared_entity_ids: tuple[str, ...] | None = None
    sample_counts: list[int] = []
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise HuangpuNativeArtifactSizingError(
            f"cannot read retained scene states: {path}"
        ) from exc
    with stream:
        for line_number, raw in enumerate(stream, start=1):
            digest.update(raw)
            if not raw.endswith(b"\n"):
                raise HuangpuNativeArtifactSizingError(
                    f"scene-state line {line_number} is not newline terminated"
                )
            try:
                state = _mapping(json.loads(raw), "scene state")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HuangpuNativeArtifactSizingError(
                    f"invalid scene-state line {line_number}: {exc}"
                ) from exc
            at = _mapping(state.get("at"), "scene-state time")
            ticks.append(_positive_int(at.get("tick"), "scene-state tick"))
            run_id = state.get("run_id")
            if not isinstance(run_id, str) or len(run_id) != 64:
                raise HuangpuNativeArtifactSizingError(
                    f"scene-state line {line_number} has no exact Run ID"
                )
            run_ids.add(run_id)
            declared = tuple(
                str(item)
                for item in _array(
                    state.get("declared_entity_ids"), "declared entity IDs"
                )
            )
            if len(declared) != len(set(declared)):
                raise HuangpuNativeArtifactSizingError(
                    "scene state repeats a declared entity ID"
                )
            if declared_entity_ids is None:
                declared_entity_ids = declared
            elif declared != declared_entity_ids:
                raise HuangpuNativeArtifactSizingError(
                    "declared scene entity inventory changes across completed ticks"
                )
            samples = _array(state.get("samples"), "scene-state samples")
            sample_counts.append(len(samples))
            line_sizes.append(len(raw))
    if len(line_sizes) < MINIMUM_COMPLETED_TICKS:
        raise HuangpuNativeArtifactSizingError(
            "retained scene history has too few completed ticks for sizing"
        )
    if ticks != list(range(1, ticks[-1] + 1)):
        raise HuangpuNativeArtifactSizingError(
            "retained scene-state ticks are not contiguous from tick 1"
        )
    if len(run_ids) != 1 or declared_entity_ids is None:
        raise HuangpuNativeArtifactSizingError(
            "retained scene history has no single Run ID and entity inventory"
        )
    return {
        "run_id": next(iter(run_ids)),
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
        "line_count": len(line_sizes),
        "tick_min": ticks[0],
        "tick_max": ticks[-1],
        "completed_tick_count": len(ticks),
        "line_bytes_min": min(line_sizes),
        "line_bytes_max": max(line_sizes),
        "line_bytes_mean": sum(line_sizes) // len(line_sizes),
        "declared_entity_count": len(declared_entity_ids),
        "declared_entity_ids": list(declared_entity_ids),
        "sample_count_min": min(sample_counts),
        "sample_count_max": max(sample_counts),
    }


def _load_runner(path: Path) -> RunnerConfig:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HuangpuNativeArtifactSizingError(
            f"invalid runner config: {path}: {exc}"
        ) from exc
    return RunnerConfig.model_validate(document)


def build_capacity_report(
    *,
    root: Path,
    resolved_run_path: Path,
    runner_config_path: Path,
    event_log_path: Path,
    scene_states_path: Path,
) -> dict[str, object]:
    root = root.resolve(strict=True)
    resolved_path, resolved_relative = _repository_file(
        root, resolved_run_path, "target ResolvedRun"
    )
    runner_path, runner_relative = _repository_file(
        root, runner_config_path, "target runner config"
    )
    event_path, event_relative = _repository_file(
        root, event_log_path, "retained event log"
    )
    scene_path, scene_relative = _repository_file(
        root, scene_states_path, "retained scene states"
    )
    resolved = _read_json(resolved_path, "target ResolvedRun")
    runner = _load_runner(runner_path)
    event = _measure_event_log(event_path)
    scene = _measure_scene_states(scene_path)
    if event["run_id"] != scene["run_id"]:
        raise HuangpuNativeArtifactSizingError(
            "retained event and scene histories have different Run IDs"
        )
    if event["completed_tick_count"] != scene["completed_tick_count"]:
        raise HuangpuNativeArtifactSizingError(
            "retained event and scene histories cover different completed ticks"
        )

    scenario = _mapping(resolved.get("scenario"), "target scenario")
    target_entity_ids = tuple(
        str(_mapping(item, "target entity").get("entity_id"))
        for item in _array(scenario.get("entities"), "target scenario entities")
    )
    if tuple(scene["declared_entity_ids"]) != target_entity_ids:
        raise HuangpuNativeArtifactSizingError(
            "retained scene entity inventory differs from the target scenario"
        )
    if event["entity_state_count_min"] != len(target_entity_ids) or event[
        "entity_state_count_max"
    ] != len(target_entity_ids):
        raise HuangpuNativeArtifactSizingError(
            "retained event log lacks one entity-state event per target entity and tick"
        )

    environment = _mapping(resolved.get("environment"), "target environment")
    clock = _mapping(environment.get("clock"), "target clock")
    max_steps = _positive_int(clock.get("max_steps"), "target maximum steps")
    step_ns = _positive_int(clock.get("step_ns"), "target clock step")

    event_projection = int(event["tick_zero_bytes"]) + (
        int(event["runtime_tick_bytes_max"]) * max_steps
    )
    scene_projection = int(scene["line_bytes_max"]) * max_steps
    event_with_headroom = _ceil_fraction(
        event_projection, HEADROOM_NUMERATOR, HEADROOM_DENOMINATOR
    )
    scene_with_headroom = _ceil_fraction(
        scene_projection, HEADROOM_NUMERATOR, HEADROOM_DENOMINATOR
    )
    artifact_limits = {
        "artifact.event-log": _ceil_to_quantum(event_with_headroom, GIB),
        "artifact.scene-states": _ceil_to_quantum(scene_with_headroom, GIB),
    }

    requirements = tuple(
        ArtifactRequirement.model_validate(item)
        for item in _array(
            resolved.get("artifact_requirements"), "target artifact requirements"
        )
    )
    requirement_ids = {item.artifact_id for item in requirements}
    if not set(SIZED_ARTIFACT_IDS).issubset(requirement_ids):
        raise HuangpuNativeArtifactSizingError(
            "target ResolvedRun omits a sized harness artifact"
        )
    updated_requirements = tuple(
        item.model_copy(update={"max_size_bytes": artifact_limits[item.artifact_id]})
        if item.artifact_id in artifact_limits
        else item
        for item in requirements
    )
    total_artifact_bytes = sum(item.max_size_bytes for item in updated_requirements)
    producer_totals: dict[str, int] = {}
    for requirement in updated_requirements:
        if requirement.producer_id != "bundle":
            producer_totals[requirement.producer_id] = (
                producer_totals.get(requirement.producer_id, 0)
                + requirement.max_size_bytes
            )
    run_id = resolved.get("run_id")
    execution_scope = resolved.get("execution_scope")
    if not isinstance(run_id, str) or len(run_id) != 64:
        raise HuangpuNativeArtifactSizingError("target ResolvedRun has no exact Run ID")
    if execution_scope not in {"executor_validation", "formal_benchmark"}:
        raise HuangpuNativeArtifactSizingError(
            "target ResolvedRun has an unsupported execution scope"
        )
    manifest_bytes = seal_manifest_size_upper_bound(
        run_id=run_id,
        execution_scope=execution_scope,
        requirements=updated_requirements,
    )
    sealed_input_bytes = total_artifact_bytes + manifest_bytes
    input_volume = max(
        runner.input_volume_size_bytes,
        _next_power_of_two(sealed_input_bytes),
    )
    artifact_volume = max(
        runner.artifact_volume_size_bytes,
        _next_power_of_two(max(producer_totals.values())),
    )

    world_digest = scenario.get("world_digest")
    scenario_digest = scenario.get("scenario_digest")
    if not isinstance(world_digest, str) or not isinstance(scenario_digest, str):
        raise HuangpuNativeArtifactSizingError(
            "target scenario omits its world or scenario digest"
        )
    event_public = {key: value for key, value in event.items() if key != "run_id"}
    scene_public = {
        key: value
        for key, value in scene.items()
        if key not in {"run_id", "declared_entity_ids"}
    }
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "sized-from-retained-failed-runtime-evidence-not-executed",
        "source_evidence": {
            "run_id": event["run_id"],
            "accepted_as_task_result": False,
            "event_log": {"path": event_relative, **event_public},
            "scene_states": {"path": scene_relative, **scene_public},
        },
        "target": {
            "basis_run_id": run_id,
            "resolved_run": {
                "path": resolved_relative,
                "sha256": hashlib.sha256(resolved_path.read_bytes()).hexdigest(),
                "size_bytes": resolved_path.stat().st_size,
            },
            "runner_config": {
                "path": runner_relative,
                "sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
                "size_bytes": runner_path.stat().st_size,
            },
            "world_digest": world_digest,
            "scenario_digest": scenario_digest,
            "entity_count": len(target_entity_ids),
            "max_steps": max_steps,
            "step_ns": step_ns,
            "maximum_simulation_horizon_ns": max_steps * step_ns,
        },
        "model": {
            "event_log_projection_bytes": event_projection,
            "scene_states_projection_bytes": scene_projection,
            "headroom_numerator": HEADROOM_NUMERATOR,
            "headroom_denominator": HEADROOM_DENOMINATOR,
            "rounding_quantum_bytes": GIB,
            "event_log_with_headroom_bytes": event_with_headroom,
            "scene_states_with_headroom_bytes": scene_with_headroom,
            "assumptions": [
                "The target retains the exact 525-entity inventory measured in every completed calibration tick.",
                "The largest completed-tick byte count bounds the same serializers over the declared 600-step horizon before headroom.",
                "The projection adds 25 percent headroom and rounds each harness history to the next GiB.",
                "The retained run failed and supplies capacity calibration only; it is not task-success evidence.",
            ],
        },
        "artifact_limits": artifact_limits,
        "capacity": {
            "artifact_requirements_total_bytes": total_artifact_bytes,
            "seal_manifest_upper_bound_bytes": manifest_bytes,
            "sealed_input_upper_bound_bytes": sealed_input_bytes,
            "producer_maximum_bytes": max(producer_totals.values()),
            "producer_totals_bytes": dict(sorted(producer_totals.items())),
        },
        "runner_limits": {
            "input_volume_size_bytes": input_volume,
            "artifact_volume_size_bytes": artifact_volume,
            "scratch_size_bytes": runner.scratch_size_bytes,
        },
        "formal_execution_started": False,
    }


def _write_new(path: Path, document: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise HuangpuNativeArtifactSizingError(
            f"capacity report must be fresh; refusing to overwrite: {path}"
        ) from None
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build_capacity_report(
            root=args.root,
            resolved_run_path=args.resolved_run,
            runner_config_path=args.runner_config,
            event_log_path=args.event_log,
            scene_states_path=args.scene_states,
        )
        _write_new(args.report, report)
    except (HuangpuNativeArtifactSizingError, OSError, ValueError) as exc:
        print(f"city artifact sizing failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "artifact_limits": report["artifact_limits"],
                "formal_execution_started": False,
                "report": str(args.report),
                "runner_limits": report["runner_limits"],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
