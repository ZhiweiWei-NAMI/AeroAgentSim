#!/usr/bin/env python3
"""Model the next Huangpu native executor budget from retained real evidence.

The command verifies the failed v5 run, the earlier 525-entity serialization
calibration, the exact harness retention implementation in the pinned OCI
image, and a fresh host-memory snapshot. It then emits one immutable report
for an executor-only resource revision. It does not start a workload and does
not treat either failed run as benchmark success.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.artifacts.contracts import (  # noqa: E402
    ArtifactRecord,
    SealManifest,
)
from aero_bench.config.resolver import ResolvedRunSpec  # noqa: E402
from aero_bench.runner.contracts import RunnerConfig  # noqa: E402
from aero_bench.runtime.ledger import LedgerRecord  # noqa: E402
from aero_bench.runtime.scene_history import (  # noqa: E402
    read_scene_state_history_jsonl,
)
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.trace.projector import (  # noqa: E402
    SealedPublicArtifacts,
    _project_mission_status,
    _project_network_event,
    _project_network_frames,
    _project_traffic_light_frames,
    _project_trajectories,
    project_public_run_event,
    project_public_scenario,
)


REPORT_SCHEMA = "aero-bench.huangpu-native-runtime-capacity/v1"
OBSERVATION_SCHEMA = "aero-bench.huangpu-native-runtime-observations/v1"
ARTIFACT_CAPACITY_SCHEMA = "aero-bench.huangpu-native-artifact-capacity/v1"
GIB = 1024**3
MIB = 1024**2
RETAINED_EVENT_AMPLIFICATION = 4
HEADROOM_NUMERATOR = 5
HEADROOM_DENOMINATOR = 4
SELECTED_HARNESS_MEMORY_MIB = 32 * 1024
SELECTED_RUNTIME_TIMEOUT_SECONDS = 9000
PUBLIC_TRACE_FIXED_RESERVE_BYTES = 8 * MIB
FRONTEND_TRACE_LIMIT_BYTES = GIB
PUBLIC_ASSET_LIMIT_BYTES = 512 * MIB
REPLAY_MANIFEST_LIMIT_BYTES = 8 * MIB
SOURCE_PATHS = (
    "aero_bench/runtime/ledger.py",
    "aero_bench/runtime/scene_history.py",
    "aero_bench/runtime/service.py",
)
PUBLIC_EVENT_TYPES = frozenset(
    {"public.network-link", "public.status", "public.traffic-light"}
)


class HuangpuNativeRuntimeCapacityError(ValueError):
    """The retained evidence cannot justify a bounded executor revision."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--artifact-capacity-plan", type=Path, required=True)
    parser.add_argument("--runtime-observations", type=Path, required=True)
    parser.add_argument("--target-resolved-run", type=Path, required=True)
    parser.add_argument("--target-runner-config", type=Path, required=True)
    parser.add_argument("--v5-runner-summary", type=Path, required=True)
    parser.add_argument("--v5-failure-diagnostic", type=Path, required=True)
    parser.add_argument("--v5-failed-inventory", type=Path, required=True)
    parser.add_argument("--v5-traffic-evidence", type=Path, required=True)
    parser.add_argument("--calibration-resolved-run", type=Path, required=True)
    parser.add_argument("--calibration-event-log", type=Path, required=True)
    parser.add_argument("--calibration-scene-states", type=Path, required=True)
    parser.add_argument("--host-meminfo", type=Path, default=Path("/proc/meminfo"))
    parser.add_argument("--report", type=Path, required=True)
    return parser


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HuangpuNativeRuntimeCapacityError(
                f"JSON object contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HuangpuNativeRuntimeCapacityError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise HuangpuNativeRuntimeCapacityError(f"{label} must be a JSON array")
    return value


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HuangpuNativeRuntimeCapacityError(f"{label} must be a positive integer")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_bytes(), object_pairs_hook=_reject_duplicate_pairs
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"invalid {label}: {path}: {exc}"
        ) from exc
    return _mapping(value, label)


def _repository_file(root: Path, path: Path, label: str) -> tuple[Path, str]:
    if path.is_symlink():
        raise HuangpuNativeRuntimeCapacityError(
            f"{label} cannot be a symbolic link: {path}"
        )
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"{label} is unavailable: {path}"
        ) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise HuangpuNativeRuntimeCapacityError(
            f"{label} must be a regular repository file: {path}"
        )
    return resolved, resolved.relative_to(root).as_posix()


def _pin(root: Path, path: Path, label: str) -> tuple[Path, dict[str, object]]:
    resolved, relative = _repository_file(root, path, label)
    return resolved, {
        "path": relative,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "size_bytes": resolved.stat().st_size,
    }


def _load_runner(path: Path) -> RunnerConfig:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"invalid runner config: {path}: {exc}"
        ) from exc
    return RunnerConfig.model_validate(document)


def _ceil_fraction(value: int, numerator: int, denominator: int) -> int:
    return (value * numerator + denominator - 1) // denominator


def _canonical_size(value: object) -> int:
    return len(canonical_json_bytes(value))


def _model_dump_size(value: object) -> int:
    return _canonical_size(value.model_dump(mode="json"))  # type: ignore[attr-defined]


def _array_size(values: tuple[object, ...]) -> int:
    return _canonical_size(
        [value.model_dump(mode="json") for value in values]  # type: ignore[attr-defined]
    )


def _parse_meminfo(path: Path) -> tuple[dict[str, int], str]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"host memory snapshot is unavailable: {path}"
        ) from exc
    values: dict[str, int] = {}
    for raw in content.decode("ascii").splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+([0-9]+)(?:\s+kB)?", raw)
        if match is not None:
            values[match.group(1)] = int(match.group(2)) * 1024
    for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
        if key not in values:
            raise HuangpuNativeRuntimeCapacityError(
                f"host memory snapshot omits {key}"
            )
    return values, hashlib.sha256(content).hexdigest()


def _source_literal(path: Path, name: str) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"cannot inspect source constant {name}: {path}"
        ) from exc
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                value = _integer_expression(node.value)
            except (ValueError, TypeError, ZeroDivisionError) as exc:
                raise HuangpuNativeRuntimeCapacityError(
                    f"source constant {name} is not a literal"
                ) from exc
            return _positive_int(value, f"source constant {name}")
    raise HuangpuNativeRuntimeCapacityError(f"source constant {name} is absent")


def _integer_expression(node: ast.expr) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp):
        left = _integer_expression(node.left)
        right = _integer_expression(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
    raise ValueError("unsupported integer expression")


def _last_json_line(path: Path, label: str) -> dict[str, Any]:
    last = b""
    try:
        with path.open("rb") as stream:
            for line in stream:
                if line.strip():
                    last = line
    except OSError as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"cannot read {label}: {path}"
        ) from exc
    if not last:
        raise HuangpuNativeRuntimeCapacityError(f"{label} is empty")
    try:
        return _mapping(json.loads(last), f"{label} terminal record")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"{label} terminal record is invalid"
        ) from exc


def _validate_v5_failure(
    *,
    observations: dict[str, Any],
    summary: dict[str, Any],
    diagnostic: dict[str, Any],
    inventory: dict[str, Any],
    traffic_terminal: dict[str, Any],
    target_run: ResolvedRunSpec,
) -> tuple[int, int]:
    run_id = observations.get("run_id")
    if (
        observations.get("schema_version") != OBSERVATION_SCHEMA
        or observations.get("accepted_as_task_result") is not False
        or observations.get("formal_execution_passed") is not False
        or run_id != target_run.run_id
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "runtime observations do not identify the failed target run"
        )
    runs = _array(summary.get("runs"), "v5 runner summary runs")
    if (
        len(runs) != 1
        or _mapping(runs[0], "v5 runner result").get("run_id") != run_id
        or runs[0].get("status") != "error"
        or runs[0].get("failure_classes") != ["wait_runtime_failed"]
        or runs[0].get("seal") is not None
        or runs[0].get("verification") is not None
        or runs[0].get("public_trace") is not None
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "v5 runner summary is not the expected failed unsealed run"
        )
    error = diagnostic.get("error")
    if (
        diagnostic.get("run_id") != run_id
        or diagnostic.get("stage") != "wait_runtime"
        or not isinstance(error, str)
        or "harness" not in error
        or "||true|137|" not in error
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "v5 diagnostic lacks the authoritative harness OOM exit"
        )
    if (
        inventory.get("run_id") != run_id
        or inventory.get("authoritative_seal") is not False
        or sorted(_array(inventory.get("missing_artifact_ids"), "missing artifacts"))
        != sorted(
            ["artifact.bounds", "artifact.event-log", "artifact.scene-states"]
        )
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "v5 failed-attempt inventory does not expose the missing harness outputs"
        )
    time = _mapping(traffic_terminal.get("terminal_time"), "traffic terminal time")
    if (
        traffic_terminal.get("run_id") != run_id
        or traffic_terminal.get("terminal_event") != "run.completed"
        or time.get("tick") != 300
        or time.get("sim_time_ns") != 150_000_000_000
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "v5 traffic evidence did not complete the measured tick-300 horizon"
        )
    terminal = _mapping(
        observations.get("terminal_observation"), "terminal observation"
    )
    if (
        terminal.get("terminal_tick") != 300
        or terminal.get("harness_exit_code") != 137
        or terminal.get("harness_oom_killed") is not True
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "runtime observations do not retain the terminal OOM"
        )
    return (
        _positive_int(
            terminal.get("elapsed_since_runner_start_approx_seconds"),
            "v5 terminal elapsed time",
        ),
        _positive_int(
            observations.get("configured_harness_memory_mib"),
            "v5 harness memory",
        ),
    )


def _validate_retention_sources(
    root: Path, observations: dict[str, Any]
) -> dict[str, object]:
    inspection = _mapping(
        observations.get("harness_image_source_inspection"),
        "harness image source inspection",
    )
    recorded = _mapping(inspection.get("files"), "inspected harness source files")
    source_pins: dict[str, object] = {}
    for relative in SOURCE_PATHS:
        source, pin = _pin(root, root / relative, f"harness source {relative}")
        if recorded.get(relative) != pin["sha256"]:
            raise HuangpuNativeRuntimeCapacityError(
                f"workspace source differs from the inspected harness image: {relative}"
            )
        source_pins[relative] = pin
        text = source.read_text(encoding="utf-8")
        required_fragments = {
            "aero_bench/runtime/ledger.py": (
                "self._records: list[LedgerRecord]",
                "self._events_by_id: dict[str, RunEvent]",
                "def ledger_jsonl_bytes",
                "EventLedger.from_records(records)",
                "io.BytesIO()",
            ),
            "aero_bench/runtime/scene_history.py": (
                "class IncrementalSceneStateHistory",
                "def seal(",
                "content = self._read_bytes(0, self._total_size_bytes)",
            ),
            "aero_bench/runtime/service.py": (
                "def _write_harness_artifacts",
                "event_payload = ledger_jsonl_bytes(records)",
                "scene_history_payload =",
            ),
        }[relative]
        if any(fragment not in text for fragment in required_fragments):
            raise HuangpuNativeRuntimeCapacityError(
                f"harness retention implementation changed: {relative}"
            )
    return {
        "harness_image": inspection.get("image"),
        "local_image_id": inspection.get("local_image_id"),
        "image_inspection_method": inspection.get("method"),
        "source_files": source_pins,
        "verified_behaviour": [
            "EventLedger retains every LedgerRecord plus event-ID and correlation indexes for the run lifetime.",
            "IncrementalSceneStateHistory stages records on disk, then seal() reads the complete history into one bytes object and validates it.",
            "Harness finalization keeps the sealed scene bytes while ledger_jsonl_bytes rebuilds ledger indexes and materializes the complete event-log bytes buffer.",
        ],
    }


def _dummy_public_resources(run_id: str) -> SealedPublicArtifacts:
    artifact = ArtifactRecord(
        artifact_id="artifact.capacity-calibration",
        artifact_type="capacity.calibration",
        producer_id="capacity-model",
        visibility="public",
        relative_path="capacity/calibration.json",
        sha256="1" * 64,
        size_bytes=1,
    )
    body = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": run_id,
        "attempt_id": "attempt.capacity-calibration",
        "execution_scope": "formal_benchmark",
        "artifacts": [artifact.model_dump(mode="json")],
        "event_chain_root": "2" * 64,
    }
    digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return SealedPublicArtifacts(SealManifest(**body, manifest_digest=digest))


def _public_records(path: Path) -> tuple[LedgerRecord, ...]:
    records: list[LedgerRecord] = []
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise HuangpuNativeRuntimeCapacityError(
            f"calibration event log is unavailable: {path}"
        ) from exc
    with stream:
        for line_number, raw in enumerate(stream, start=1):
            try:
                document = _mapping(json.loads(raw), "calibration ledger record")
                event = _mapping(document.get("event"), "calibration RunEvent")
                visibility = _array(event.get("visibility"), "RunEvent visibility")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HuangpuNativeRuntimeCapacityError(
                    f"invalid calibration event-log line {line_number}"
                ) from exc
            if any(
                isinstance(item, dict) and item.get("scope") == "public"
                for item in visibility
            ):
                records.append(LedgerRecord.model_validate(document))
    if not records:
        raise HuangpuNativeRuntimeCapacityError(
            "calibration event log has no public events"
        )
    return tuple(records)


def _component_stats(values: tuple[object, ...]) -> dict[str, int]:
    sizes = [_model_dump_size(value) for value in values]
    if not sizes:
        raise HuangpuNativeRuntimeCapacityError(
            "public trace calibration component is empty"
        )
    return {
        "item_count": len(values),
        "array_bytes": _array_size(values),
        "item_bytes_min": min(sizes),
        "item_bytes_max": max(sizes),
        "item_bytes_mean": sum(sizes) // len(sizes),
    }


def _array_projection(max_item_bytes: int, count: int) -> int:
    return max_item_bytes * count + max(0, count - 1) + 2


def _public_trace_model(
    *,
    calibration_run: ResolvedRunSpec,
    target_run: ResolvedRunSpec,
    scene_path: Path,
    event_path: Path,
    max_steps: int,
    server_trace_limit: int,
) -> dict[str, object]:
    states = read_scene_state_history_jsonl(scene_path)
    if not states or any(
        state.run_id != calibration_run.run_id
        or state.scenario_digest != calibration_run.scenario.scenario_digest
        for state in states
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "scene calibration differs from its ResolvedRun"
        )
    records = _public_records(event_path)
    if any(record.event.run_id != calibration_run.run_id for record in records):
        raise HuangpuNativeRuntimeCapacityError(
            "public event calibration differs from its ResolvedRun"
        )
    counts = {
        event_type: sum(
            record.event.event_type == event_type for record in records
        )
        for event_type in PUBLIC_EVENT_TYPES
    }
    if counts != {event_type: len(states) for event_type in PUBLIC_EVENT_TYPES}:
        raise HuangpuNativeRuntimeCapacityError(
            "public calibration lacks one traffic, network, and status event per tick"
        )

    resources = _dummy_public_resources(calibration_run.run_id)
    trajectories = _project_trajectories(states)
    network_frames = _project_network_frames(calibration_run, states)
    traffic_frames = _project_traffic_light_frames(calibration_run, records)
    network_events = tuple(
        _project_network_event(
            calibration_run,
            record,
            scene_states=states,
            resources=resources,
        )
        for record in records
        if record.event.event_type == "public.network-link"
    )
    mission_status = tuple(
        _project_mission_status(calibration_run, record)
        for record in records
        if record.event.event_type == "public.status"
    )
    public_ids = {record.event.event_id for record in records}
    events = tuple(
        project_public_run_event(record.event, public_event_ids=public_ids)
        for record in records
    )
    scenario_bytes = _model_dump_size(project_public_scenario(target_run))
    state_sizes = [_model_dump_size(state) for state in states]
    trajectory_sample_sizes = [
        _model_dump_size(sample)
        for trajectory in trajectories
        for sample in trajectory.samples
    ]
    if not trajectory_sample_sizes:
        raise HuangpuNativeRuntimeCapacityError(
            "public trajectory calibration has no dynamic samples"
        )
    event_tick_bytes: dict[int, int] = {}
    for event in events:
        event_tick_bytes[event.at.tick] = (
            event_tick_bytes.get(event.at.tick, 0) + _model_dump_size(event) + 1
        )
    if sorted(event_tick_bytes) != list(range(1, len(states) + 1)):
        raise HuangpuNativeRuntimeCapacityError(
            "public event calibration ticks are not contiguous"
        )

    components = {
        "scene_states": {
            "item_count": len(states),
            "array_bytes": _array_size(states),
            "item_bytes_min": min(state_sizes),
            "item_bytes_max": max(state_sizes),
            "item_bytes_mean": sum(state_sizes) // len(state_sizes),
        },
        "trajectories": {
            **_component_stats(trajectories),
            "trajectory_count": len(trajectories),
            "sample_count": len(trajectory_sample_sizes),
            "sample_bytes_min": min(trajectory_sample_sizes),
            "sample_bytes_max": max(trajectory_sample_sizes),
            "sample_bytes_mean": (
                sum(trajectory_sample_sizes) // len(trajectory_sample_sizes)
            ),
        },
        "network_frames": _component_stats(network_frames),
        "traffic_light_frames": _component_stats(traffic_frames),
        "network_events": _component_stats(network_events),
        "mission_status_history": _component_stats(mission_status),
        "events": {
            **_component_stats(events),
            "events_per_tick": len(events) // len(states),
            "tick_bytes_min": min(event_tick_bytes.values()),
            "tick_bytes_max": max(event_tick_bytes.values()),
            "tick_bytes_mean": sum(event_tick_bytes.values()) // len(states),
        },
    }

    def projection(ticks: int) -> dict[str, object]:
        trajectory_count = len(trajectories)
        trajectory_bytes = (
            trajectory_count
            * (64 + ticks * (max(trajectory_sample_sizes) + 1))
            + 2
        )
        component_bounds = {
            "scenario_bytes": scenario_bytes,
            "scene_states_bytes": _array_projection(max(state_sizes), ticks),
            "trajectories_bytes": trajectory_bytes,
            "network_frames_bytes": _array_projection(
                int(components["network_frames"]["item_bytes_max"]), ticks
            ),
            "traffic_light_frames_bytes": _array_projection(
                int(components["traffic_light_frames"]["item_bytes_max"]), ticks
            ),
            "network_events_bytes": _array_projection(
                int(components["network_events"]["item_bytes_max"]), ticks
            ),
            "mission_status_history_bytes": _array_projection(
                int(components["mission_status_history"]["item_bytes_max"]), ticks
            ),
            "events_bytes": (
                ticks * max(event_tick_bytes.values()) + 2
            ),
            "non_scaling_trace_envelope_reserve_bytes": (
                PUBLIC_TRACE_FIXED_RESERVE_BYTES
            ),
        }
        total = sum(component_bounds.values())
        return {
            "ticks": ticks,
            "component_upper_bounds": component_bounds,
            "public_trace_upper_bound_bytes": total,
            "server_trace_limit_bytes": server_trace_limit,
            "frontend_trace_limit_bytes": FRONTEND_TRACE_LIMIT_BYTES,
            "within_server_trace_limit": total <= server_trace_limit,
            "within_frontend_trace_limit": total <= FRONTEND_TRACE_LIMIT_BYTES,
        }

    terminal_projection = projection(300)
    declared_projection = projection(max_steps)
    if not terminal_projection["within_server_trace_limit"]:
        raise HuangpuNativeRuntimeCapacityError(
            "measured tick-300 terminal horizon exceeds the current delivery cap"
        )
    if not declared_projection["within_frontend_trace_limit"]:
        raise HuangpuNativeRuntimeCapacityError(
            "declared horizon exceeds the frontend public-trace byte contract"
        )
    return {
        "calibration": {
            "run_id": calibration_run.run_id,
            "scenario_digest": calibration_run.scenario.scenario_digest,
            "completed_ticks": len(states),
            "entity_count": len(states[0].declared_entity_ids),
            "public_event_counts": counts,
            "components": components,
        },
        "target_scenario_bytes": scenario_bytes,
        "projected_terminal_horizon": terminal_projection,
        "projected_declared_horizon": declared_projection,
        "asset_limit_bytes": PUBLIC_ASSET_LIMIT_BYTES,
        "replay_manifest_limit_bytes": REPLAY_MANIFEST_LIMIT_BYTES,
        "model_scope": (
            "Component-wise upper bound from the largest canonical item in the "
            "47-tick real calibration, plus an 8 MiB reserve for trace members "
            "that do not scale with tick count. Exact v6 sealed bytes remain required."
        ),
    }


def _runtime_memory_total_mib(run: ResolvedRunSpec, harness_memory_mib: int) -> int:
    total = harness_memory_mib
    total += sum(provider.workload.resources.memory_mib for provider in run.environment.providers)
    total += sum(agent.workload.resources.memory_mib for agent in run.agents)
    total += sum(
        agent.driver.workload.resources.memory_mib
        for agent in run.agents
        if agent.driver is not None
    )
    return total


def build_report(
    *,
    root: Path,
    artifact_capacity_path: Path,
    observations_path: Path,
    target_resolved_path: Path,
    target_runner_path: Path,
    summary_path: Path,
    diagnostic_path: Path,
    inventory_path: Path,
    traffic_path: Path,
    calibration_resolved_path: Path,
    calibration_event_path: Path,
    calibration_scene_path: Path,
    host_meminfo_path: Path,
) -> dict[str, object]:
    root = root.resolve(strict=True)
    pinned: dict[str, dict[str, object]] = {}
    paths: dict[str, Path] = {}
    for key, path, label in (
        ("artifact_capacity_plan", artifact_capacity_path, "artifact capacity plan"),
        ("runtime_observations", observations_path, "runtime observations"),
        ("target_resolved_run", target_resolved_path, "target ResolvedRun"),
        ("target_runner_config", target_runner_path, "target runner config"),
        ("v5_runner_summary", summary_path, "v5 runner summary"),
        ("v5_failure_diagnostic", diagnostic_path, "v5 failure diagnostic"),
        ("v5_failed_inventory", inventory_path, "v5 failed inventory"),
        ("v5_traffic_evidence", traffic_path, "v5 traffic evidence"),
        (
            "calibration_resolved_run",
            calibration_resolved_path,
            "calibration ResolvedRun",
        ),
        ("calibration_event_log", calibration_event_path, "calibration event log"),
        (
            "calibration_scene_states",
            calibration_scene_path,
            "calibration scene states",
        ),
    ):
        paths[key], pinned[key] = _pin(root, path, label)

    capacity = _read_json(paths["artifact_capacity_plan"], "artifact capacity plan")
    observations = _read_json(paths["runtime_observations"], "runtime observations")
    summary = _read_json(paths["v5_runner_summary"], "v5 runner summary")
    diagnostic = _read_json(paths["v5_failure_diagnostic"], "v5 failure diagnostic")
    inventory = _read_json(paths["v5_failed_inventory"], "v5 failed inventory")
    target_run = ResolvedRunSpec.model_validate_json(
        paths["target_resolved_run"].read_bytes()
    )
    calibration_run = ResolvedRunSpec.model_validate_json(
        paths["calibration_resolved_run"].read_bytes()
    )
    target_runner = _load_runner(paths["target_runner_config"])
    traffic_terminal = _last_json_line(
        paths["v5_traffic_evidence"], "v5 traffic evidence"
    )
    v5_elapsed_seconds, v5_harness_memory_mib = _validate_v5_failure(
        observations=observations,
        summary=summary,
        diagnostic=diagnostic,
        inventory=inventory,
        traffic_terminal=traffic_terminal,
        target_run=target_run,
    )
    retention = _validate_retention_sources(root, observations)

    if (
        capacity.get("schema_version") != ARTIFACT_CAPACITY_SCHEMA
        or capacity.get("formal_execution_started") is not False
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "artifact capacity input is not the accepted pre-execution model"
        )
    target = _mapping(capacity.get("target"), "artifact capacity target")
    limits = _mapping(capacity.get("artifact_limits"), "artifact limits")
    event_limit = _positive_int(limits.get("artifact.event-log"), "event limit")
    scene_limit = _positive_int(
        limits.get("artifact.scene-states"), "scene-state limit"
    )
    max_steps = _positive_int(target.get("max_steps"), "declared maximum steps")
    entity_count = len(target_run.scenario.entities)
    if (
        target.get("world_digest") != target_run.scenario.world_digest
        or target.get("entity_count") != entity_count
        or target_run.environment.clock.max_steps != max_steps
        or target_run.environment.harness.resources.memory_mib
        != v5_harness_memory_mib
    ):
        raise HuangpuNativeRuntimeCapacityError(
            "artifact model and failed v5 target have different execution dimensions"
        )

    samples = _array(observations.get("samples"), "runtime memory samples")
    observed_gib = [
        float(_mapping(sample, "runtime memory sample").get("harness_memory_gib"))
        for sample in samples
    ]
    if not observed_gib or any(not math.isfinite(value) or value <= 0 for value in observed_gib):
        raise HuangpuNativeRuntimeCapacityError("runtime memory samples are invalid")
    observed_rss_bytes = math.ceil(max(observed_gib) * GIB)
    source = _mapping(capacity.get("source_evidence"), "capacity source evidence")
    event_source = _mapping(source.get("event_log"), "event calibration")
    tick_zero = _positive_int(event_source.get("tick_zero_bytes"), "tick-zero bytes")
    event_tick_max = _positive_int(
        event_source.get("runtime_tick_bytes_max"), "maximum event tick bytes"
    )
    terminal_tick = 300
    raw_terminal_event_projection = tick_zero + event_tick_max * terminal_tick
    measured_ratio = observed_rss_bytes / raw_terminal_event_projection
    if RETAINED_EVENT_AMPLIFICATION < math.ceil(measured_ratio):
        raise HuangpuNativeRuntimeCapacityError(
            "selected retained-event amplification is below the measured ratio"
        )
    retained_event_bound = RETAINED_EVENT_AMPLIFICATION * event_limit
    serialization_bound = event_limit + scene_limit
    pre_headroom_peak = retained_event_bound + serialization_bound
    with_headroom = _ceil_fraction(
        pre_headroom_peak, HEADROOM_NUMERATOR, HEADROOM_DENOMINATOR
    )
    selected_memory_bytes = SELECTED_HARNESS_MEMORY_MIB * MIB
    if selected_memory_bytes < with_headroom:
        raise HuangpuNativeRuntimeCapacityError(
            "selected harness memory is below the modelled peak with headroom"
        )

    linear_full_horizon_seconds = _ceil_fraction(
        v5_elapsed_seconds * max_steps, 1, terminal_tick
    )
    runtime_with_headroom_seconds = _ceil_fraction(
        linear_full_horizon_seconds, HEADROOM_NUMERATOR, HEADROOM_DENOMINATOR
    )
    if SELECTED_RUNTIME_TIMEOUT_SECONDS < runtime_with_headroom_seconds:
        raise HuangpuNativeRuntimeCapacityError(
            "selected runtime timeout is below the full-horizon projection"
        )

    meminfo, meminfo_sha256 = _parse_meminfo(host_meminfo_path)
    prior_runtime_mib = _runtime_memory_total_mib(target_run, v5_harness_memory_mib)
    revised_runtime_mib = _runtime_memory_total_mib(
        target_run, SELECTED_HARNESS_MEMORY_MIB
    )
    keeper_count_bound = 8
    keeper_memory_mib = keeper_count_bound * target_runner.volume_keeper_memory_mib
    declared_peak_bytes = (revised_runtime_mib + keeper_memory_mib) * MIB
    available_bytes = meminfo["MemAvailable"]
    host_margin_bytes = available_bytes - declared_peak_bytes
    if host_margin_bytes <= 0:
        raise HuangpuNativeRuntimeCapacityError(
            "host available memory is below the revised declared runtime budget"
        )

    server_trace_limit = _source_literal(
        root / "aero_bench/control/manager.py", "_MAX_PUBLIC_TRACE_BYTES"
    )
    if server_trace_limit != GIB:
        raise HuangpuNativeRuntimeCapacityError(
            "server public-trace cap is not aligned with the frontend 1 GiB cap"
        )
    public_trace = _public_trace_model(
        calibration_run=calibration_run,
        target_run=target_run,
        scene_path=paths["calibration_scene_states"],
        event_path=paths["calibration_event_log"],
        max_steps=max_steps,
        server_trace_limit=server_trace_limit,
    )

    return {
        "schema_version": REPORT_SCHEMA,
        "status": "modelled-from-failed-runtime-evidence-not-executed",
        "formal_execution_started": False,
        "accepted_as_task_result": False,
        "inputs": pinned,
        "failed_run": {
            "run_id": target_run.run_id,
            "scenario_digest": target_run.scenario.scenario_digest,
            "world_digest": target_run.scenario.world_digest,
            "entity_count": entity_count,
            "terminal_tick": terminal_tick,
            "terminal_simulation_time_ns": 150_000_000_000,
            "elapsed_seconds_approx": v5_elapsed_seconds,
            "harness_exit_code": 137,
            "harness_oom_killed": True,
            "authoritative_seal": False,
            "verification": None,
            "public_trace": None,
        },
        "retention_implementation": retention,
        "harness_memory_model": {
            "configured_entity_count": entity_count,
            "configured_max_steps": max_steps,
            "event_log_limit_bytes": event_limit,
            "scene_state_history_limit_bytes": scene_limit,
            "observed_harness_rss_max_bytes": observed_rss_bytes,
            "observed_harness_rss_display_gib": max(observed_gib),
            "raw_event_projection_at_tick_300_bytes": raw_terminal_event_projection,
            "observed_rss_to_raw_event_ratio": measured_ratio,
            "retained_event_amplification_factor": RETAINED_EVENT_AMPLIFICATION,
            "retained_event_graph_bound_bytes": retained_event_bound,
            "seal_serialization_buffers_bound_bytes": serialization_bound,
            "pre_headroom_peak_bytes": pre_headroom_peak,
            "headroom_numerator": HEADROOM_NUMERATOR,
            "headroom_denominator": HEADROOM_DENOMINATOR,
            "peak_with_headroom_bytes": with_headroom,
            "selected_harness_memory_mib": SELECTED_HARNESS_MEMORY_MIB,
            "selected_harness_memory_bytes": selected_memory_bytes,
            "assumptions": [
                "The failed tick-300 RSS sample is a lower bound because the container was later OOM-killed during terminal serialization.",
                "A 4x retained-event factor rounds the measured ratio upward and applies it to the full 3 GiB event artifact contract.",
                "The complete 3 GiB event bytes and 1 GiB scene-history bytes may coexist with the retained event graph during Harness finalization.",
                "Twenty-five percent headroom is added before selecting the next power-of-two executor memory budget.",
            ],
        },
        "runtime_timeout_model": {
            "v5_tick_300_elapsed_seconds_approx": v5_elapsed_seconds,
            "declared_max_steps": max_steps,
            "linear_full_horizon_seconds": linear_full_horizon_seconds,
            "full_horizon_with_25_percent_headroom_seconds": (
                runtime_with_headroom_seconds
            ),
            "selected_runtime_timeout_seconds": SELECTED_RUNTIME_TIMEOUT_SECONDS,
            "verifier_timeout_seconds_unchanged": target_runner.verifier_timeout_seconds,
        },
        "host_feasibility": {
            "measurement_source": str(host_meminfo_path),
            "measurement_sha256": meminfo_sha256,
            "mem_total_bytes": meminfo["MemTotal"],
            "mem_available_bytes": available_bytes,
            "swap_total_bytes": meminfo["SwapTotal"],
            "swap_free_bytes": meminfo["SwapFree"],
            "prior_declared_runtime_memory_mib": prior_runtime_mib,
            "revised_declared_runtime_memory_mib": revised_runtime_mib,
            "volume_keeper_count_upper_bound": keeper_count_bound,
            "volume_keeper_memory_mib_each": target_runner.volume_keeper_memory_mib,
            "revised_declared_peak_with_keepers_bytes": declared_peak_bytes,
            "available_memory_margin_bytes": host_margin_bytes,
            "feasible_at_measurement": True,
            "launch_requirement": (
                "Recheck MemAvailable immediately before launch and require at "
                "least the revised declared peak with keeper allowance."
            ),
        },
        "public_trace_delivery_model": public_trace,
        "executor_revision": {
            "profile": "docker_reference",
            "harness_memory_mib": SELECTED_HARNESS_MEMORY_MIB,
            "runtime_timeout_seconds": SELECTED_RUNTIME_TIMEOUT_SECONDS,
            "verifier_timeout_seconds": target_runner.verifier_timeout_seconds,
            "domain_specs_changed": False,
            "world_inputs_changed": False,
            "task_inputs_changed": False,
            "provider_images_changed": False,
            "artifact_limits_changed": False,
            "expected_identity_change": (
                "ResolvedRun identity changes because the explicit Harness ResourceBudget is hashed."
            ),
        },
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
        raise HuangpuNativeRuntimeCapacityError(
            f"runtime capacity report must be fresh; refusing to overwrite: {path}"
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
        report = build_report(
            root=args.root,
            artifact_capacity_path=args.artifact_capacity_plan,
            observations_path=args.runtime_observations,
            target_resolved_path=args.target_resolved_run,
            target_runner_path=args.target_runner_config,
            summary_path=args.v5_runner_summary,
            diagnostic_path=args.v5_failure_diagnostic,
            inventory_path=args.v5_failed_inventory,
            traffic_path=args.v5_traffic_evidence,
            calibration_resolved_path=args.calibration_resolved_run,
            calibration_event_path=args.calibration_event_log,
            calibration_scene_path=args.calibration_scene_states,
            host_meminfo_path=args.host_meminfo,
        )
        _write_new(args.report, report)
    except (HuangpuNativeRuntimeCapacityError, OSError, TypeError, ValueError) as exc:
        print(f"city runtime capacity modelling failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "formal_execution_started": False,
                "harness_memory_mib": report["executor_revision"]["harness_memory_mib"],
                "report": str(args.report),
                "runtime_timeout_seconds": report["executor_revision"][
                    "runtime_timeout_seconds"
                ],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
