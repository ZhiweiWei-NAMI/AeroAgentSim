"""Generate Dataset/episodes from grounded scene_setup/event_script pairs.

The render pipeline consumes Dataset/episodes, so this generator must keep those
episodes aligned with the scenario files that validation checks. It uses
scene_setup.json as the authoritative entity/asset/initial-pose source and
event_script.json as the event/motion source.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from pedestrian_activity_catalog import get_activity, normalize_activity_type
from Dataset.semantic_truth.core_semantic_registry import (
    get_governed_parameter_defaults,
)
from Dataset.tools.typed_entity_labels import label_for_declared_asset
from Dataset.tools.runtime_state_source_guard import forbidden_runtime_state_paths
from Dataset.tools.weather_fields import (
    WEATHER_ALIASES,
    WEATHER_BOOLEAN_FIELDS,
    WEATHER_FRACTION_FIELDS,
    WEATHER_NUMERIC_FIELDS,
    WEATHER_OVERRIDE_FIELDS,
)
from runtime_state_contract import (
    RUNTIME_STATE_FIELDS,
    invalid_runtime_state_value_paths,
    unconsumed_runtime_state_paths,
)

try:
    from sumo_ground_flow.explicit_vehicle_plan import (
        build_explicit_vehicle_plan,
        is_script_controlled_vehicle,
        planned_script_controlled_source_vehicle_ids,
        planned_source_vehicle_ids,
        write_explicit_vehicle_plan,
    )
except (
    ModuleNotFoundError
):  # pragma: no cover - supports package imports from repo root.
    from Dataset.tools.sumo_ground_flow.explicit_vehicle_plan import (
        build_explicit_vehicle_plan,
        is_script_controlled_vehicle,
        planned_script_controlled_source_vehicle_ids,
        planned_source_vehicle_ids,
        write_explicit_vehicle_plan,
    )


TICK_HZ = 10
DEFAULT_DURATION_TICKS = 900
CAPTURE_TICK_STEP = 5
MOVE_REALIZATION_EPS_M = 0.25
MOVE_REALIZATION_SPEED_EPS_MPS = 0.1
TERMINAL_REALIZATION_TOLERANCE_M = 0.75
TERMINAL_RESULT_KEYWORDS = (
    "land",
    "landing",
    "landed",
    "touchdown",
    "terminal_resolution",
    "forced_landing",
    "safe_stop",
)
INSPECT_UAV_LOOP_SPEED_MPS = 5.0
INSPECT_UAV_MIN_MOTION_RATIO = 1.05
GOVERNED_PARAMETERS = get_governed_parameter_defaults()
TERMINAL_REALIZATION_SPEED_MAX_MPS = float(
    GOVERNED_PARAMETERS["aircraft_stationary_speed_threshold_mps"]
)
UAV_TOUCHDOWN_THRESHOLD_M = float(GOVERNED_PARAMETERS["touchdown_threshold_m"])
UAV_AIRBORNE_THRESHOLD_M = float(GOVERNED_PARAMETERS["airborne_threshold_m"])
UAV_VERTICAL_STATE_EPS_M = 1.0
GROUND_MOTION_SPEED_EPS_MPS = 0.05
BACKGROUND_PEDESTRIAN_IDLE_ACTIVITIES = ("phone_call", "chatting")
WEATHER_PROFILES: dict[str, dict[str, Any]] = {
    "clear": {
        "condition": "clear",
        "rain": 0.0,
        "wetness": 0.0,
        "fog": 0.0,
        "fog_density": 0.0,
        "wind_speed": 2.0,
        "wind_direction_deg": 0.0,
        "visibility_m": 20000.0,
        "visibility": 20000.0,
        "temperature_c": 24.0,
        "illumination_lux": 12000.0,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
    "rain": {
        "condition": "rain",
        "rain": 0.55,
        "wetness": 0.75,
        "fog": 0.0,
        "fog_density": 0.0,
        "wind_speed": 4.0,
        "wind_direction_deg": 110.0,
        "visibility_m": 2200.0,
        "visibility": 2200.0,
        "temperature_c": 21.0,
        "illumination_lux": 2400.0,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
    "fog": {
        "condition": "fog",
        "rain": 0.0,
        "wetness": 0.0,
        "fog": 0.6,
        "fog_density": 0.6,
        "wind_speed": 2.0,
        "wind_direction_deg": 20.0,
        "visibility_m": 180.0,
        "visibility": 180.0,
        "temperature_c": 18.0,
        "illumination_lux": 1800.0,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
    "wind": {
        "condition": "wind",
        "rain": 0.0,
        "wetness": 0.0,
        "fog": 0.0,
        "fog_density": 0.0,
        "wind_speed": 12.5,
        "wind_direction_deg": 90.0,
        "visibility_m": 20000.0,
        "visibility": 20000.0,
        "temperature_c": 23.0,
        "illumination_lux": 10000.0,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
    "dusk": {
        "condition": "dusk",
        "rain": 0.0,
        "wetness": 0.0,
        "fog": 0.0,
        "fog_density": 0.0,
        "wind_speed": 2.0,
        "wind_direction_deg": 0.0,
        "visibility_m": 12000.0,
        "visibility": 12000.0,
        "temperature_c": 18.0,
        "illumination_lux": 80.0,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
    "heat": {
        "condition": "heat",
        "rain": 0.0,
        "wetness": 0.0,
        "fog": 0.0,
        "fog_density": 0.0,
        "wind_speed": 2.0,
        "wind_direction_deg": 0.0,
        "visibility_m": 18000.0,
        "visibility": 18000.0,
        "temperature_c": 46.0,
        "illumination_lux": 14000.0,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
    "light smoke": {
        "condition": "light smoke",
        "rain": 0.0,
        "wetness": 0.0,
        "fog": 0.18,
        "fog_density": 0.18,
        "wind_speed": 2.0,
        "wind_direction_deg": 35.0,
        "visibility_m": 3500.0,
        "visibility": 3500.0,
        "temperature_c": 25.0,
        "illumination_lux": 3200.0,
        "dust": 0.25,
        "hazard_source_active": False,
        "hazard_concentration_ppm": 0.0,
        "hazard_radius_m": 0.0,
    },
}
PRESERVED_ENTITY_FIELDS = (
    "semantic_scope",
    "entity_kind",
    "task_id",
    "role",
    "state_sequence",
    "semantic_role",
    "background_role",
    "contract_scenario_id",
    "contract_inspect_uav",
    "background_vehicle",
    "background_pedestrian",
    "ground_flow_contract",
    "event_actor_motion_contract",
    "contract_facility",
    "contract_logical_sidecar",
    "uav_corridor_role",
    "uav_corridor",
    "assigned_altitude_m",
    "inspect_altitude_code",
    "inspect_altitude_m",
    "min_path_length_m",
    "full_episode_presence",
    "observer_lifecycle",
    "ground_reference_z_m",
    "ground_reference_source",
    "lifecycle",
    "activation_tick",
    "deactivation_tick",
    "spawn_policy",
    "category",
    "route_waypoints_enu_m",
    "planned_route_waypoints_enu_m",
    "path_deviation_contract",
    "task_kind",
    "task_state",
    "inspect_altitude_code",
    "inspect_altitude_m",
    "min_path_length_m",
    "full_episode_presence",
    *RUNTIME_STATE_FIELDS,
)
PRESERVED_EVENT_VALIDATION_FIELDS = (
    "validation_event_type",
    "validation_reason",
    "validation_skip_checks",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def project_relative(path: Path, dataset_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(dataset_root.parent.resolve())).replace(
            "\\", "/"
        )
    except ValueError:
        return str(path.resolve())


def remove_prefix(value: str, prefix: str) -> str:
    return value[len(prefix) :] if value.startswith(prefix) else value


def event_log_candidate_ids(row: dict[str, Any], scenario_id: str) -> list[str]:
    prefix = f"evt_{scenario_id}_"
    candidates: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        normalized = remove_prefix(text, prefix)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    for key in ("event_id", "source_event_id", "topic"):
        add(row.get(key))
    for nested_key in ("payload", "metadata"):
        nested = row.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("event_id", "source_event_id", "topic"):
            add(nested.get(key))
    return candidates


def enrich_event_log_validation_fields(
    event_log: list[dict[str, Any]],
    script: dict[str, Any],
    scenario_id: str,
) -> None:
    event_defs = {
        str(event_def.get("event_id") or ""): event_def
        for event_def in script.get("events") or []
        if event_def.get("event_id")
    }
    for row in event_log:
        source_event: dict[str, Any] | None = None
        for event_id in event_log_candidate_ids(row, scenario_id):
            source_event = event_defs.get(event_id)
            if source_event is not None:
                break
        if source_event is None:
            continue

        copied: dict[str, Any] = {}
        for field in PRESERVED_EVENT_VALIDATION_FIELDS:
            value = source_event.get(field)
            if value not in (None, "", []):
                copied[field] = copy.deepcopy(value)
        if not copied:
            continue

        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            row["payload"] = payload
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            row["metadata"] = metadata
        for field, value in copied.items():
            row[field] = copy.deepcopy(value)
            payload[field] = copy.deepcopy(value)
            metadata[field] = copy.deepcopy(value)


def ceil_to_capture_tick(tick: int, step: int = CAPTURE_TICK_STEP) -> int:
    return int(math.ceil(float(tick) / float(step)) * step)


def event_trace_tick(row: dict[str, Any]) -> int:
    for key in ("tick", "activated_tick", "source_tick"):
        value = row.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def event_topic_from_def(event_def: dict[str, Any], scenario_id: str) -> str:
    log_event = dict(event_def.get("log_event") or {})
    topic = str(
        log_event.get("topic")
        or event_def.get("topic")
        or event_def.get("event_id")
        or ""
    ).strip()
    if topic.startswith("evt_"):
        return topic
    return f"evt_{scenario_id}_{topic}" if topic else ""


def event_title_from_def(event_def: dict[str, Any], trace_row: dict[str, Any]) -> str:
    log_event = dict(event_def.get("log_event") or {})
    payload = dict(trace_row.get("payload") or {})
    return str(
        log_event.get("title")
        or payload.get("title")
        or event_def.get("title")
        or event_def.get("event_id")
        or ""
    )


def event_targets_from_trace(
    trace_row: dict[str, Any], event_def: dict[str, Any] | None = None
) -> list[str]:
    targets: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in targets:
            targets.append(text)

    for value in trace_row.get("target_ids") or []:
        add(value)
    scope = dict(trace_row.get("scope") or {})
    add(scope.get("target_id"))
    for value in scope.get("entities") or []:
        add(value)
    if event_def:
        log_event = dict(event_def.get("log_event") or {})
        for value in log_event.get("target_ids") or []:
            add(value)
        for action in event_def.get("actions") or []:
            add(action.get("entity_id") or action.get("ped_id"))
    return targets


def action_entity_id(action: dict[str, Any], params: dict[str, Any]) -> str:
    return str(
        resolve_param(action.get("entity_id") or action.get("ped_id") or "", params)
        or ""
    ).strip()


def action_waypoints(
    action: dict[str, Any], params: dict[str, Any]
) -> list[list[float]]:
    raw = resolve_param(
        action.get("waypoints_enu_m") or action.get("waypoints") or [], params
    )
    return [vector3(point) for point in raw] if isinstance(raw, list) else []


def row_pos(row: dict[str, Any] | None) -> list[float] | None:
    if not row:
        return None
    value = row.get("pos_enu") or row.get("position_enu_m")
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) < 2
    ):
        return None
    return vector3(value)


def row_speed(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    velocity = row.get("vel_mps") or row.get("velocity_enu_mps") or [0.0, 0.0, 0.0]
    values = vector3(velocity)
    return math.sqrt(sum(float(value) ** 2 for value in values))


def truth_sample_summary(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"present": False}
    return {
        "present": True,
        "tick": int(row.get("tick") or 0),
        "position_enu_m": row.get("pos_enu") or row.get("position_enu_m"),
        "velocity_enu_mps": row.get("vel_mps") or row.get("velocity_enu_mps"),
        "state": row.get("state"),
        "activity_type": row.get("activity_type"),
        "label_class": row.get("label_class"),
        "entity_category": row.get("entity_category"),
    }


def trajectory_index(
    rows: Sequence[dict[str, Any]],
) -> dict[str, dict[int, dict[str, Any]]]:
    result: dict[str, dict[int, dict[str, Any]]] = {}
    for row in rows:
        entity_id = str(row.get("entity_id") or "").strip()
        if not entity_id:
            continue
        try:
            tick = int(row.get("tick") or 0)
        except (TypeError, ValueError):
            continue
        result.setdefault(entity_id, {})[tick] = row
    return result


def first_presence_tick(samples: dict[int, dict[str, Any]], tick: int) -> int | None:
    for candidate in sorted(samples):
        if candidate >= tick:
            return candidate
    return None


def first_motion_tick(
    samples: dict[int, dict[str, Any]], tick: int, baseline_pos: Sequence[float] | None
) -> int | None:
    if baseline_pos is None:
        first_tick = first_presence_tick(samples, tick)
        baseline_pos = row_pos(samples.get(first_tick or -1))
    if baseline_pos is None:
        return None
    for candidate in sorted(samples):
        if candidate < tick:
            continue
        pos = row_pos(samples.get(candidate))
        if pos is not None and distance3(pos, baseline_pos) > MOVE_REALIZATION_EPS_M:
            return candidate
        if row_speed(samples.get(candidate)) > MOVE_REALIZATION_SPEED_EPS_MPS:
            return candidate
    return None


def first_capture_motion_tick(
    samples: dict[int, dict[str, Any]], tick: int, baseline_pos: Sequence[float] | None
) -> int | None:
    motion_tick = first_motion_tick(samples, tick, baseline_pos)
    if motion_tick is None:
        return None
    for candidate in sorted(samples):
        if candidate >= motion_tick and candidate % CAPTURE_TICK_STEP == 0:
            return candidate
    return None


def terminal_reached_tick(
    samples: dict[int, dict[str, Any]], tick: int, terminal: Sequence[float]
) -> int | None:
    for candidate in sorted(samples):
        if candidate < tick:
            continue
        pos = row_pos(samples.get(candidate))
        if (
            pos is not None
            and distance3(pos, terminal) <= TERMINAL_REALIZATION_TOLERANCE_M
            and row_speed(samples.get(candidate)) <= TERMINAL_REALIZATION_SPEED_MAX_MPS
        ):
            return candidate
    return None


def event_requires_terminal_result(
    event_def: dict[str, Any] | None, trace_row: dict[str, Any]
) -> bool:
    payload = dict(trace_row.get("payload") or {})
    metadata = dict(trace_row.get("metadata") or {})
    text = " ".join(
        str(value or "").lower()
        for value in (
            payload.get("title"),
            trace_row.get("title"),
            metadata.get("intent"),
            trace_row.get("intent"),
            event_def.get("intent") if event_def else "",
            event_def.get("event_id") if event_def else "",
        )
    )
    return any(keyword in text for keyword in TERMINAL_RESULT_KEYWORDS)


def build_event_realizations(
    *,
    scenario_id: str,
    script: dict[str, Any],
    event_log: Sequence[dict[str, Any]],
    executed_actions: Sequence[dict[str, Any]],
    trajectory_rows: Sequence[dict[str, Any]],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    event_defs = {
        str(event_def.get("event_id") or ""): event_def
        for event_def in script.get("events") or []
        if event_def.get("event_id")
    }
    action_results_by_id = {
        str(item.get("action_id") or ""): item
        for item in executed_actions
        if str(item.get("action_id") or "")
    }

    def action_result_for(
        action_id: str, action_type: str, entity_id: str, dispatch_tick: int
    ) -> dict[str, Any] | None:
        if action_id and action_id in action_results_by_id:
            return action_results_by_id[action_id]
        for item in executed_actions:
            if str(item.get("type") or "") != action_type:
                continue
            if str(item.get("entity_id") or "") != entity_id:
                continue
            if int(item.get("tick", -1) or -1) == dispatch_tick:
                return item
        return None

    samples_by_entity = trajectory_index(trajectory_rows)
    rows: list[dict[str, Any]] = []

    for sequence_no, trace_row in enumerate(event_log, start=1):
        source_event: dict[str, Any] | None = None
        source_event_id = ""
        for candidate in event_log_candidate_ids(trace_row, scenario_id):
            source_event = event_defs.get(candidate)
            if source_event is not None:
                source_event_id = candidate
                break
        dispatch_tick = event_trace_tick(trace_row)
        result_ticks: list[int] = []
        evidence_ticks: list[int] = []
        action_realizations: list[dict[str, Any]] = []
        targets = event_targets_from_trace(trace_row, source_event)
        terminal_required = event_requires_terminal_result(source_event, trace_row)

        for action in (source_event or {}).get("actions") or []:
            action_type = str(action.get("type") or "")
            action_id = str(action.get("action_id") or "")
            entity_id = action_entity_id(action, params)
            executed = action_result_for(
                action_id, action_type, entity_id, dispatch_tick
            )
            result_payload = dict((executed or {}).get("result") or {})
            action_tick = int(
                result_payload.get("tick", dispatch_tick) or dispatch_tick
            )
            scheduled_tick = int(
                result_payload.get("scheduled_tick", action_tick) or action_tick
            )
            samples = samples_by_entity.get(entity_id, {})
            baseline = row_pos(samples.get(dispatch_tick)) or row_pos(
                samples.get(first_presence_tick(samples, dispatch_tick) or -1)
            )
            realization: dict[str, Any] = {
                "action_id": action_id,
                "action_type": action_type,
                "entity_id": entity_id,
                "dispatch_tick": action_tick,
                "scheduled_tick": scheduled_tick,
                "status": result_payload.get("status")
                or (executed or {}).get("status")
                or "",
            }
            if action_type == "move_entity":
                waypoints = action_waypoints(action, params)
                terminal = waypoints[-1] if waypoints else None
                motion_tick = first_motion_tick(samples, scheduled_tick, baseline)
                capture_motion_tick = first_capture_motion_tick(
                    samples, scheduled_tick, baseline
                )
                terminal_tick = (
                    terminal_reached_tick(samples, scheduled_tick, terminal)
                    if terminal
                    else None
                )
                result_tick = (
                    terminal_tick
                    if terminal_required and terminal_tick is not None
                    else motion_tick or scheduled_tick
                )
                evidence_tick = (
                    ceil_to_capture_tick(terminal_tick)
                    if terminal_required and terminal_tick is not None
                    else capture_motion_tick or ceil_to_capture_tick(result_tick)
                )
                realization.update(
                    {
                        "path_length_m": result_payload.get("path_length_m"),
                        "first_motion_tick": motion_tick,
                        "first_capture_motion_tick": capture_motion_tick,
                        "terminal_tick": terminal_tick,
                        "result_tick": result_tick,
                        "evidence_tick": min(
                            DEFAULT_DURATION_TICKS, int(evidence_tick)
                        ),
                        "terminal_required": terminal_required,
                        "terminal_enu_m": terminal,
                    }
                )
            elif action_type in {
                "set_visual_state",
                "set_pedestrian_activity",
                "spawn_entity",
            }:
                result_tick = min(DEFAULT_DURATION_TICKS, action_tick + 1)
                realization.update(
                    {
                        "result_tick": result_tick,
                        "evidence_tick": ceil_to_capture_tick(result_tick),
                        "expected_state": result_payload.get("mode")
                        or result_payload.get("activity_type"),
                    }
                )
            elif action_type == "set_runtime_state":
                result_tick = int(
                    result_payload.get("effective_tick")
                    or result_payload.get("scheduled_tick")
                    or min(DEFAULT_DURATION_TICKS, action_tick + 1)
                )
                realization.update(
                    {
                        "result_tick": result_tick,
                        "evidence_tick": ceil_to_capture_tick(result_tick),
                        "state_families": list(
                            result_payload.get("state_families") or []
                        ),
                    }
                )
            else:
                result_tick = action_tick
                realization.update(
                    {
                        "result_tick": result_tick,
                        "evidence_tick": ceil_to_capture_tick(result_tick),
                        "metadata_only": action_type
                        in {
                            "capture_screenshot",
                            "sequence",
                            "remove_entity",
                            "play_animation",
                            "spawn_crowd",
                            "set_weather",
                        },
                    }
                )
            if realization.get("result_tick") is not None:
                result_ticks.append(int(realization["result_tick"]))
            if realization.get("evidence_tick") is not None:
                evidence_ticks.append(int(realization["evidence_tick"]))
            action_realizations.append(realization)

        event_result_tick = max(result_ticks) if result_ticks else dispatch_tick
        event_evidence_tick = min(
            DEFAULT_DURATION_TICKS,
            max(evidence_ticks)
            if evidence_ticks
            else ceil_to_capture_tick(event_result_tick),
        )
        before_tick = max(0, event_evidence_tick - CAPTURE_TICK_STEP)
        after_tick = min(
            DEFAULT_DURATION_TICKS, event_evidence_tick + CAPTURE_TICK_STEP
        )
        snapshot_ticks = [before_tick, event_evidence_tick, after_tick]
        snapshots = {
            str(tick): {
                entity_id: truth_sample_summary(
                    samples_by_entity.get(entity_id, {}).get(tick)
                )
                for entity_id in targets
            }
            for tick in snapshot_ticks
        }

        rows.append(
            {
                "schema_name": "event_realization",
                "schema_version": "v1",
                "scenario_id": scenario_id,
                "sequence_no": sequence_no,
                "event_id": source_event_id
                or str(
                    trace_row.get("source_event_id") or trace_row.get("event_id") or ""
                ),
                "topic": str(
                    trace_row.get("topic") or trace_row.get("source_topic") or ""
                ),
                "title": event_title_from_def(source_event or {}, trace_row),
                "intent": trace_row.get("intent")
                or (trace_row.get("metadata") or {}).get("intent"),
                "dispatch_tick": dispatch_tick,
                "result_tick": event_result_tick,
                "evidence_tick": event_evidence_tick,
                "before_tick": before_tick,
                "after_tick": after_tick,
                "capture_grid_step": CAPTURE_TICK_STEP,
                "target_ids": targets,
                "terminal_result_required": terminal_required,
                "action_realizations": action_realizations,
                "source_truth_snapshots_by_tick": snapshots,
                "basis": "Dataset/episodes trajectories generated after event dispatch",
            }
        )
    return rows


def resolve_param(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$param."):
        return params.get(value[len("$param.") :], value)
    if isinstance(value, list):
        return [resolve_param(item, params) for item in value]
    if isinstance(value, dict):
        return {key: resolve_param(item, params) for key, item in value.items()}
    return value


def validate_runtime_state_patch(
    payload: Any, *, action_id: str = "set_runtime_state"
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{action_id}: runtime state patch must be a mapping")
    patch: dict[str, dict[str, Any]] = {}
    forbidden = sorted(set(forbidden_runtime_state_paths(payload, policy="engine_whole")))
    if forbidden:
        raise RuntimeError(
            f"{action_id}: runtime state payload contains forbidden semantic fields or paths: {forbidden}"
        )
    unknown = sorted(
        str(key) for key in payload if str(key) not in RUNTIME_STATE_FIELDS
    )
    if unknown:
        raise RuntimeError(
            f"{action_id}: unsupported runtime state families: {unknown}"
        )
    unconsumed = unconsumed_runtime_state_paths(payload)
    if unconsumed:
        raise RuntimeError(
            f"{action_id}: runtime state payload contains fields that no formal state-to-predicate rule consumes: "
            f"{unconsumed}"
        )
    invalid_values = invalid_runtime_state_value_paths(payload)
    if invalid_values:
        raise RuntimeError(
            f"{action_id}: runtime state payload contains values outside the governed field contract: "
            f"{invalid_values}"
        )
    for family in RUNTIME_STATE_FIELDS:
        if family not in payload:
            continue
        family_value = payload[family]
        if not isinstance(family_value, Mapping):
            raise RuntimeError(f"{action_id}: {family} must be a mapping")
        if not family_value:
            raise RuntimeError(
                f"{action_id}: {family} must not be an empty family patch"
            )
        patch[family] = copy.deepcopy(dict(family_value))
    if not patch:
        raise RuntimeError(
            f"{action_id}: runtime state patch must include at least one allowed state family"
        )
    return patch


def _runtime_state_patches_from_carrier(
    source: Mapping[str, Any],
    *,
    context: str,
) -> list[dict[str, dict[str, Any]]]:
    candidates: list[tuple[str, Any]] = []

    direct = {
        family: source[family] for family in RUNTIME_STATE_FIELDS if family in source
    }
    if direct:
        candidates.append(("top_level", direct))

    for nested_key in ("initial_state", "visual_state"):
        if nested_key not in source:
            continue
        nested = source.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        nested_direct = {
            family: nested[family]
            for family in RUNTIME_STATE_FIELDS
            if family in nested
        }
        if nested_direct:
            candidates.append((nested_key, nested_direct))
        if "runtime_state" in nested:
            candidates.append(
                (f"{nested_key}.runtime_state", nested.get("runtime_state"))
            )

    if "runtime_state" in source:
        candidates.append(("runtime_state", source.get("runtime_state")))

    return [
        validate_runtime_state_patch(payload, action_id=f"{context}.{location}")
        for location, payload in candidates
    ]


def runtime_state_fields_from_sources(
    *sources: Mapping[str, Any],
    context: str = "runtime_state_source",
) -> dict[str, dict[str, Any]]:
    preserved: dict[str, dict[str, Any]] = {}
    for source_index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            continue
        patches = _runtime_state_patches_from_carrier(
            source,
            context=f"{context}[{source_index}]",
        )
        for patch in patches:
            for family, family_value in patch.items():
                if family not in preserved:
                    preserved[family] = copy.deepcopy(family_value)
    return preserved


def runtime_state_patch_from_action(
    action: dict[str, Any], params: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    raw_patch = action.get("state_patch")
    if raw_patch is None:
        raw_patch = action.get("runtime_state")
    action_id = str(action.get("action_id") or "set_runtime_state")
    if raw_patch is None:
        raise RuntimeError(
            f"{action_id}: set_runtime_state requires state_patch or runtime_state"
        )
    resolved_patch = resolve_param(raw_patch, params)
    return validate_runtime_state_patch(resolved_patch, action_id=action_id)


def deep_merge_mapping(
    base: dict[str, Any], patch: Mapping[str, Any]
) -> dict[str, Any]:
    for key, value in patch.items():
        key_text = str(key)
        if isinstance(value, Mapping) and isinstance(base.get(key_text), dict):
            deep_merge_mapping(base[key_text], value)
        else:
            base[key_text] = copy.deepcopy(value)
    return base


def vector3(value: Any, default: Sequence[float] = (0.0, 0.0, 0.0)) -> list[float]:
    values = (
        list(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        else []
    )
    return [
        float(values[0] if len(values) > 0 else default[0]),
        float(values[1] if len(values) > 1 else default[1]),
        float(values[2] if len(values) > 2 else default[2]),
    ]


def distance3(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def path_length_m(points: Sequence[Sequence[float]]) -> float:
    return sum(distance3(a, b) for a, b in zip(points, points[1:]))


def _has_explicit_activity(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def infer_uav_move_activity(
    start_pos: Sequence[float],
    waypoints: Sequence[Sequence[float]],
    activity_type: Any,
    post_activity_type: Any,
    *,
    ground_reference_z_m: float,
    landing_reference_enu_m: Sequence[float] | None,
) -> tuple[Any, Any]:
    if not waypoints:
        raise RuntimeError("UAV move activity requires at least one waypoint")

    start = vector3(start_pos)
    normalized_waypoints = [vector3(waypoint, start) for waypoint in waypoints]
    terminal = normalized_waypoints[-1]
    total_path_m = path_length_m([start, *normalized_waypoints])
    if total_path_m <= MOVE_REALIZATION_EPS_M:
        raise RuntimeError("UAV move path is too short to establish a motion state")

    explicit_activity = str(activity_type or "").strip().casefold()
    explicit_post_activity = str(post_activity_type or "").strip().casefold()
    if explicit_activity == "landing" or explicit_post_activity == "landing":
        raise RuntimeError(
            "moving UAV state 'landing' is forbidden; use descending then landed"
        )
    terminal_is_landed = explicit_post_activity == "landed" or (
        explicit_activity != "takeoff"
        and landing_reference_enu_m is not None
        and distance3(terminal, vector3(landing_reference_enu_m)) <= 1e-6
    )
    if terminal_is_landed:
        if landing_reference_enu_m is None:
            raise RuntimeError(
                "landed post-state requires an explicit landing reference"
            )
        landing_reference = vector3(landing_reference_enu_m)
        if distance3(terminal, landing_reference) > 1e-6:
            raise RuntimeError(
                "landed post-state terminal does not match its landing reference"
            )
        approach = normalized_waypoints[-2] if len(normalized_waypoints) > 1 else start
        if approach[2] - terminal[2] <= UAV_VERTICAL_STATE_EPS_M:
            raise RuntimeError(
                "landed post-state requires a physically descending final leg"
            )

    if _has_explicit_activity(activity_type):
        return activity_type, post_activity_type

    delta_z_m = terminal[2] - start[2]
    moving_activity = "airborne"
    terminal_activity = "landed" if terminal_is_landed else "airborne"
    if (
        delta_z_m > UAV_VERTICAL_STATE_EPS_M
        and start[2] <= float(ground_reference_z_m) + UAV_TOUCHDOWN_THRESHOLD_M
        and terminal[2] > float(ground_reference_z_m) + UAV_AIRBORNE_THRESHOLD_M
    ):
        moving_activity = "takeoff"
    elif delta_z_m < -UAV_VERTICAL_STATE_EPS_M:
        moving_activity = "descending"

    return (
        moving_activity,
        post_activity_type
        if _has_explicit_activity(post_activity_type)
        else terminal_activity,
    )


def extend_loop_route(
    start: list[float],
    route: list[list[float]],
    *,
    velocity_mps: float,
    duration_ticks: int,
    min_motion_ratio: float,
) -> list[list[float]]:
    base: list[list[float]] = []
    for point in [start, *route]:
        candidate = vector3(point)
        if base and distance3(base[-1], candidate) < 0.05:
            continue
        base.append(candidate)
    if len(base) < 2:
        return [vector3(point) for point in route]
    if distance3(base[0], base[-1]) >= 0.05:
        base.append(list(base[0]))
    result = [list(point) for point in base[1:]]
    cycle = [list(point) for point in base[1:]]
    current = list(result[-1])
    current_length_m = path_length_m([start, *result])
    target_length_m = (
        float(velocity_mps)
        * (float(duration_ticks) / float(TICK_HZ))
        * float(min_motion_ratio)
    )
    while current_length_m < target_length_m:
        progressed = False
        for point in cycle:
            if distance3(current, point) < 0.05:
                continue
            result.append(list(point))
            current_length_m += distance3(current, point)
            current = list(point)
            progressed = True
            if current_length_m >= target_length_m:
                break
        if not progressed:
            break
    return result


def stable_unit_interval(seed_text: str) -> float:
    digest = hashlib.sha256(str(seed_text).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _stable_signed_unit(seed_text: str) -> float:
    return stable_unit_interval(seed_text) * 2.0 - 1.0


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _explicit_variation_fields(
    action: dict[str, Any], params: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    keys = ("trajectory_variation", "stagger", "density_profile")
    resolved: dict[str, Any] = {}
    explicit = False
    action_params = (
        dict(action.get("params") or {})
        if isinstance(action.get("params"), dict)
        else {}
    )
    for key in keys:
        if key in action:
            resolved[key] = resolve_param(action.get(key), params)
            explicit = True
            continue
        if key in action_params:
            resolved[key] = resolve_param(action_params.get(key), params)
            explicit = True
            continue
        if key in params:
            resolved[key] = resolve_param(params.get(key), params)
            explicit = True
    return explicit, resolved


def _variation_settings(
    label_class: str, action: dict[str, Any], params: dict[str, Any]
) -> dict[str, float] | None:
    explicit, fields = _explicit_variation_fields(action, params)
    if not explicit:
        return None

    max_tick_offset_ticks = 0
    tick_bias_ticks = 0
    velocity_jitter_ratio = 0.0
    velocity_bias = 0.0
    lateral_offset_m = 0.0
    longitudinal_offset_m = 0.0
    pedestrian_lateral_offset_m: float | None = None
    vehicle_lateral_offset_m: float | None = None
    pedestrian_longitudinal_offset_m: float | None = None
    vehicle_longitudinal_offset_m: float | None = None
    min_segment_m = 0.25

    trajectory_variation = fields.get("trajectory_variation")
    if isinstance(trajectory_variation, dict):
        max_tick_offset_ticks = max(
            0,
            _to_int(
                trajectory_variation.get(
                    "max_tick_offset_ticks",
                    trajectory_variation.get(
                        "tick_offset_ticks", max_tick_offset_ticks
                    ),
                ),
                max_tick_offset_ticks,
            ),
        )
        velocity_jitter_ratio = max(
            0.0,
            _to_float(
                trajectory_variation.get(
                    "velocity_jitter_ratio",
                    trajectory_variation.get(
                        "velocity_scale_jitter", velocity_jitter_ratio
                    ),
                ),
                velocity_jitter_ratio,
            ),
        )
        velocity_bias += _to_float(trajectory_variation.get("velocity_bias", 0.0), 0.0)
        lateral_offset_m = abs(
            _to_float(
                trajectory_variation.get("lateral_offset_m", lateral_offset_m),
                lateral_offset_m,
            )
        )
        longitudinal_offset_m = abs(
            _to_float(
                trajectory_variation.get(
                    "longitudinal_offset_m",
                    trajectory_variation.get("path_offset_m", longitudinal_offset_m),
                ),
                longitudinal_offset_m,
            )
        )
        if "pedestrian_lateral_offset_m" in trajectory_variation:
            pedestrian_lateral_offset_m = abs(
                _to_float(trajectory_variation.get("pedestrian_lateral_offset_m"), 0.0)
            )
        if "vehicle_lateral_offset_m" in trajectory_variation:
            vehicle_lateral_offset_m = abs(
                _to_float(trajectory_variation.get("vehicle_lateral_offset_m"), 0.0)
            )
        if "pedestrian_longitudinal_offset_m" in trajectory_variation:
            pedestrian_longitudinal_offset_m = abs(
                _to_float(
                    trajectory_variation.get("pedestrian_longitudinal_offset_m"), 0.0
                )
            )
        if "vehicle_longitudinal_offset_m" in trajectory_variation:
            vehicle_longitudinal_offset_m = abs(
                _to_float(
                    trajectory_variation.get("vehicle_longitudinal_offset_m"), 0.0
                )
            )
        min_segment_m = max(
            0.0,
            _to_float(
                trajectory_variation.get("min_segment_m", min_segment_m), min_segment_m
            ),
        )
    elif trajectory_variation is True:
        max_tick_offset_ticks = max(max_tick_offset_ticks, 6)
        velocity_jitter_ratio = max(velocity_jitter_ratio, 0.10)
    elif isinstance(trajectory_variation, (int, float)):
        max_tick_offset_ticks = max(
            max_tick_offset_ticks, abs(_to_int(trajectory_variation, 0))
        )

    stagger = fields.get("stagger")
    if isinstance(stagger, dict):
        max_tick_offset_ticks = max(
            max_tick_offset_ticks,
            abs(
                _to_int(
                    stagger.get(
                        "max_tick_offset_ticks", stagger.get("tick_offset_ticks", 0)
                    ),
                    0,
                )
            ),
        )
        tick_bias_ticks += _to_int(
            stagger.get("tick_bias_ticks", stagger.get("tick_bias", 0)), 0
        )
    elif isinstance(stagger, (int, float)):
        max_tick_offset_ticks = max(max_tick_offset_ticks, abs(_to_int(stagger, 0)))
    elif stagger is True:
        max_tick_offset_ticks = max(max_tick_offset_ticks, 6)

    density_profile = fields.get("density_profile")
    if isinstance(density_profile, dict):
        tick_bias_ticks += _to_int(
            density_profile.get("tick_bias_ticks", density_profile.get("tick_bias", 0)),
            0,
        )
        velocity_bias += _to_float(
            density_profile.get(
                "velocity_bias", density_profile.get("speed_bias", 0.0)
            ),
            0.0,
        )
        max_tick_offset_ticks = max(
            max_tick_offset_ticks,
            abs(
                _to_int(
                    density_profile.get(
                        "max_tick_offset_ticks",
                        density_profile.get("tick_offset_ticks", 0),
                    ),
                    0,
                )
            ),
        )
        velocity_jitter_ratio = max(
            velocity_jitter_ratio,
            max(
                0.0,
                _to_float(
                    density_profile.get(
                        "velocity_jitter_ratio",
                        density_profile.get("speed_jitter_ratio", 0.0),
                    ),
                    0.0,
                ),
            ),
        )
        lateral_offset_m = max(
            lateral_offset_m,
            abs(_to_float(density_profile.get("lateral_offset_m", 0.0), 0.0)),
        )
        longitudinal_offset_m = max(
            longitudinal_offset_m,
            abs(
                _to_float(
                    density_profile.get(
                        "longitudinal_offset_m",
                        density_profile.get("path_offset_m", 0.0),
                    ),
                    0.0,
                )
            ),
        )
    else:
        density_text = str(density_profile or "").strip().lower()
        if density_text in {"dense", "high", "crowded"}:
            tick_bias_ticks += 2
            velocity_bias -= 0.08
            max_tick_offset_ticks = max(max_tick_offset_ticks, 8)
            velocity_jitter_ratio = max(velocity_jitter_ratio, 0.04)
            lateral_offset_m = max(lateral_offset_m, 0.12)
            longitudinal_offset_m = max(longitudinal_offset_m, 0.25)
        elif density_text in {"sparse", "low", "light"}:
            tick_bias_ticks -= 2
            velocity_bias += 0.08
            max_tick_offset_ticks = max(max_tick_offset_ticks, 12)
            velocity_jitter_ratio = max(velocity_jitter_ratio, 0.06)
            lateral_offset_m = max(lateral_offset_m, 0.18)
            longitudinal_offset_m = max(longitudinal_offset_m, 0.45)
        elif density_text in {"medium", "normal", "balanced"}:
            max_tick_offset_ticks = max(max_tick_offset_ticks, 6)
            velocity_jitter_ratio = max(velocity_jitter_ratio, 0.03)
            lateral_offset_m = max(lateral_offset_m, 0.10)
            longitudinal_offset_m = max(longitudinal_offset_m, 0.20)

    if label_class == "pedestrian":
        max_lateral_offset_m = abs(
            pedestrian_lateral_offset_m
            if pedestrian_lateral_offset_m is not None
            else lateral_offset_m
        )
        max_longitudinal_offset_m = abs(
            pedestrian_longitudinal_offset_m
            if pedestrian_longitudinal_offset_m is not None
            else longitudinal_offset_m
        )
    elif label_class == "vehicle":
        if vehicle_lateral_offset_m is not None:
            max_lateral_offset_m = abs(vehicle_lateral_offset_m)
        else:
            max_lateral_offset_m = abs(lateral_offset_m) * 0.35
        max_longitudinal_offset_m = abs(
            vehicle_longitudinal_offset_m
            if vehicle_longitudinal_offset_m is not None
            else longitudinal_offset_m
        )
    else:
        max_lateral_offset_m = 0.0
        max_longitudinal_offset_m = 0.0

    return {
        "max_tick_offset_ticks": float(max_tick_offset_ticks),
        "tick_bias_ticks": float(tick_bias_ticks),
        "velocity_jitter_ratio": float(velocity_jitter_ratio),
        "velocity_bias": float(velocity_bias),
        "max_lateral_offset_m": float(max_lateral_offset_m),
        "max_longitudinal_offset_m": float(max_longitudinal_offset_m),
        "min_segment_m": float(min_segment_m),
    }


def _path_has_segment(
    start_pos: Sequence[float], waypoints: list[Any], min_segment_m: float
) -> bool:
    current = vector3(start_pos)
    for waypoint in waypoints:
        target = vector3(waypoint, current)
        if distance3(current, target) >= min_segment_m:
            return True
        current = target
    return False


def _path_planar_basis(
    start_pos: Sequence[float],
    waypoints: list[Any],
    min_segment_m: float,
) -> tuple[list[float], list[float], float] | None:
    current = vector3(start_pos)
    for waypoint in waypoints:
        target = vector3(waypoint, current)
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        planar = math.hypot(dx, dy)
        if planar >= min_segment_m:
            tangent = [dx / planar, dy / planar, 0.0]
            normal = [-dy / planar, dx / planar, 0.0]
            return tangent, normal, planar
        current = target
    return None


def _apply_waypoint_offsets(
    start_pos: Sequence[float],
    waypoints: list[Any],
    max_lateral_offset_m: float,
    max_longitudinal_offset_m: float,
    seed_text: str,
    min_segment_m: float,
) -> list[list[float]]:
    normalized = [vector3(waypoint) for waypoint in waypoints]
    if max_lateral_offset_m <= 0.0 and max_longitudinal_offset_m <= 0.0:
        return normalized
    basis = _path_planar_basis(start_pos, normalized, min_segment_m)
    if basis is None:
        return normalized
    tangent, normal, first_segment_m = basis
    lateral_offset = (
        max_lateral_offset_m * _stable_signed_unit(f"{seed_text}|lateral")
        if max_lateral_offset_m > 0.0
        else 0.0
    )
    longitudinal_limit = min(max_longitudinal_offset_m, first_segment_m * 0.4)
    longitudinal_offset = (
        longitudinal_limit * _stable_signed_unit(f"{seed_text}|longitudinal")
        if longitudinal_limit > 0.0
        else 0.0
    )
    varied = [
        [
            point[0] + normal[0] * lateral_offset + tangent[0] * longitudinal_offset,
            point[1] + normal[1] * lateral_offset + tangent[1] * longitudinal_offset,
            point[2],
        ]
        for point in normalized
    ]
    if varied:
        varied[-1] = list(normalized[-1])
    return varied


def variation_draw_root(scenario_id: str, entity_id: str, action_id: str,
                        variation_seed: int) -> str:
    """Identity of the authored-variation draws for one move action.

    Seed zero keeps the original deterministic draw identity. Nonzero seeds
    enter the draw identity; offsets stay inside the authored bounds. Existing
    ARM reproduction uses the default zero variation seed explicitly.
    """
    if variation_seed == 0:
        return f"{scenario_id}|{entity_id}|{action_id}"
    return f"{scenario_id}|seed{int(variation_seed):02d}|{entity_id}|{action_id}"


def _apply_move_variation(
    *,
    scenario_id: str,
    entity_id: str,
    action_id: str,
    variation_seed: int,
    label_class: str,
    tick: int,
    velocity_mps: float,
    start_pos: Sequence[float],
    waypoints: list[Any],
    action: dict[str, Any],
    params: dict[str, Any],
) -> tuple[int, float, list[list[float]]]:
    settings = _variation_settings(label_class, action, params)
    normalized_waypoints = [vector3(waypoint) for waypoint in waypoints]
    if settings is None:
        return tick, velocity_mps, normalized_waypoints
    if not normalized_waypoints:
        return tick, velocity_mps, normalized_waypoints

    min_segment_m = max(0.0, float(settings["min_segment_m"]))
    if min_segment_m > 0.0 and not _path_has_segment(
        start_pos, normalized_waypoints, min_segment_m
    ):
        return tick, velocity_mps, normalized_waypoints

    seed_root = variation_draw_root(scenario_id, entity_id, action_id, variation_seed)
    tick_offset = int(settings["tick_bias_ticks"])
    max_tick_offset = int(settings["max_tick_offset_ticks"])
    if max_tick_offset > 0:
        tick_offset += int(
            round(_stable_signed_unit(f"{seed_root}|tick") * max_tick_offset)
        )
    varied_tick = max(0, tick + tick_offset)

    velocity_scale = 1.0 + float(settings["velocity_bias"])
    velocity_jitter_ratio = float(settings["velocity_jitter_ratio"])
    if velocity_jitter_ratio > 0.0:
        velocity_scale += (
            _stable_signed_unit(f"{seed_root}|velocity") * velocity_jitter_ratio
        )
    velocity_scale = max(0.1, velocity_scale)
    varied_velocity = max(0.1, float(velocity_mps) * velocity_scale)

    varied_waypoints = _apply_waypoint_offsets(
        start_pos,
        normalized_waypoints,
        float(settings["max_lateral_offset_m"]),
        float(settings["max_longitudinal_offset_m"]),
        seed_root,
        max(min_segment_m, 1e-6),
    )
    return varied_tick, varied_velocity, varied_waypoints


def label_class_for(entity_id: str, logical_asset_id: str, category: str) -> str:
    # Identity spelling and coarse category do not override the asset's
    # declared label. Undeclared assets are explicit input errors.
    return label_for_declared_asset(logical_asset_id)


def position_from_placement(entity: dict[str, Any]) -> list[float]:
    placement = dict(entity.get("placement") or {})
    mode = str(entity.get("placement_mode") or "").lower()
    for key in ("resolved_position_enu_m", "position_enu_m", "center_enu_m"):
        if isinstance(placement.get(key), list):
            return vector3(placement[key])
    if mode == "polygon_prism" and isinstance(placement.get("polygon_enu_m"), list):
        points = [
            vector3(point)
            for point in placement["polygon_enu_m"]
            if isinstance(point, list)
        ]
        if points:
            return [
                sum(point[0] for point in points) / len(points),
                sum(point[1] for point in points) / len(points),
                float(placement.get("base_z_m", points[0][2])),
            ]
    logical_asset_id = str(entity.get("logical_asset_id") or "")
    category = str(entity.get("category") or "")
    if logical_asset_id.startswith("pedestrian.") or category == "pedestrian":
        raise RuntimeError(
            f"Pedestrian entity lacks resolved placement: {entity.get('entity_id')}"
        )
    return [50.0, 20.0, 0.0]


def _state_label(state: str, *, pedestrian: bool, moving: bool = False) -> str:
    if pedestrian:
        return normalize_activity_type(state, moving=moving)
    return str(state or ("moving" if moving else "idle"))


def _append_frame(
    frames: list[tuple[int, list[float], str]],
    tick: int,
    pos: list[float],
    state: str,
    *,
    pedestrian: bool,
    moving: bool = False,
) -> None:
    state = _state_label(state, pedestrian=pedestrian, moving=moving)
    if frames and frames[-1][0] == tick:
        frames[-1] = (tick, list(pos), state)
        return
    frames.append((tick, list(pos), state))


def keyframes_for(
    initial_pos: list[float],
    schedules: list[dict[str, Any]],
    initial_state: str,
    *,
    pedestrian: bool = False,
) -> list[tuple[int, list[float], str]]:
    current_activity = _state_label(initial_state, pedestrian=pedestrian)
    frames = [(0, list(initial_pos), current_activity)]
    current_pos = list(initial_pos)
    current_tick = 0
    for schedule in sorted(schedules, key=lambda item: int(item.get("tick", 0))):
        schedule_type = str(schedule.get("type") or "move")
        authored_start_tick = max(0, int(schedule.get("tick", 0)))
        if schedule_type == "activity" and schedule.get("start_pos_enu") is not None:
            # Apply an observed action now, never after the old route endpoint.
            # Non-stopping activities preserve the remaining motion geometry.
            activity = _state_label(str(schedule["activity_type"]), pedestrian=pedestrian)
            position = vector3(schedule["start_pos_enu"])
            future = [(t, p, activity) for t, p, _ in frames if t > authored_start_tick]
            frames = [frame for frame in frames if frame[0] < authored_start_tick]
            frames.append((authored_start_tick, position, activity))
            if schedule["stop_motion"] is False:
                frames.extend(future)
            elif schedule["stop_motion"] is not True:
                raise ValueError("activity stop_motion must be Boolean")
            current_tick, current_pos, current_activity = frames[-1]
            continue
        if (
            schedule_type == "move"
            and authored_start_tick < current_tick
            and schedule.get("start_pos_enu") is not None
        ):
            # A triggered maneuver supersedes the unfinished movement future.
            # Its dispatch-time position is the authoritative transition state.
            frames = [frame for frame in frames if frame[0] < authored_start_tick]
            current_tick = authored_start_tick
            current_pos = vector3(schedule.get("start_pos_enu"), current_pos)
        start_tick = max(current_tick, authored_start_tick)
        if start_tick > current_tick:
            _append_frame(
                frames, start_tick, current_pos, current_activity, pedestrian=pedestrian
            )
            current_tick = start_tick
        if schedule_type == "activity":
            current_activity = _state_label(
                str(schedule.get("activity_type") or current_activity),
                pedestrian=pedestrian,
            )
            _append_frame(
                frames, start_tick, current_pos, current_activity, pedestrian=pedestrian
            )
            continue
        velocity = max(0.1, float(schedule.get("velocity_mps") or 1.0))
        waypoints = list(schedule.get("waypoints_enu_m", []) or [])
        if not waypoints:
            continue
        moving_activity = _state_label(
            str(schedule.get("activity_type") or current_activity),
            pedestrian=pedestrian,
            moving=True,
        )
        post_activity = _state_label(
            str(
                schedule.get("post_activity_type")
                or ("waiting" if pedestrian else moving_activity)
            ),
            pedestrian=pedestrian,
        )
        _append_frame(
            frames,
            start_tick,
            current_pos,
            moving_activity,
            pedestrian=pedestrian,
            moving=True,
        )
        if schedule.get("start_pos_enu") is not None:
            current_pos = vector3(schedule.get("start_pos_enu"), current_pos)
            _append_frame(
                frames,
                start_tick,
                current_pos,
                moving_activity,
                pedestrian=pedestrian,
                moving=True,
            )
        for waypoint_index, waypoint in enumerate(waypoints):
            target = vector3(waypoint, current_pos)
            distance = distance3(current_pos, target)
            if distance <= 1e-6:
                continue
            current_tick += max(1, int(math.ceil(distance / velocity * TICK_HZ)))
            current_pos = target
            frame_activity = (
                post_activity
                if waypoint_index == len(waypoints) - 1
                else moving_activity
            )
            _append_frame(
                frames, current_tick, current_pos, frame_activity, pedestrian=pedestrian
            )
            current_activity = frame_activity
    return frames


def sample_keyframes(
    frames: list[tuple[int, list[float], str]], tick: int
) -> tuple[list[float], list[float], str]:
    if not frames:
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "idle"
    frames = sorted(frames, key=lambda item: item[0])
    if tick <= frames[0][0]:
        return list(frames[0][1]), [0.0, 0.0, 0.0], frames[0][2]
    for previous, current in zip(frames, frames[1:]):
        if previous[0] <= tick <= current[0]:
            span = max(1, current[0] - previous[0])
            alpha = (tick - previous[0]) / float(span)
            pos = [
                previous[1][i] + (current[1][i] - previous[1][i]) * alpha
                for i in range(3)
            ]
            dt_s = span / float(TICK_HZ)
            vel = [(current[1][i] - previous[1][i]) / dt_s for i in range(3)]
            if tick == current[0]:
                state = current[2]
            else:
                state = (
                    previous[2]
                    if math.sqrt(sum(value * value for value in vel)) > 0.05
                    else current[2]
                )
            return pos, vel, state
    return list(frames[-1][1]), [0.0, 0.0, 0.0], frames[-1][2]


def _row_xy_distance_from(row: dict[str, Any], start: Sequence[float]) -> float:
    pos = row.get("pos_enu")
    if not isinstance(pos, list) or len(pos) < 2:
        return 0.0
    return math.hypot(float(pos[0]) - float(start[0]), float(pos[1]) - float(start[1]))


def _entity_yaw_deg(entity: dict[str, Any]) -> float:
    scene_setup = dict(entity.get("scene_setup") or {})
    placement = dict(scene_setup.get("placement") or {})
    rotation = dict(placement.get("rotation_deg") or {})
    try:
        return float(rotation.get("yaw_deg") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _background_pedestrian_idle_activity(entity_id: str) -> str:
    digest = hashlib.sha256(str(entity_id).encode("utf-8")).digest()
    return BACKGROUND_PEDESTRIAN_IDLE_ACTIVITIES[
        digest[0] % len(BACKGROUND_PEDESTRIAN_IDLE_ACTIVITIES)
    ]



def preserved_fields_from(
    *sources: dict[str, Any],
    context: str = "preserved_entity_fields",
) -> dict[str, Any]:
    preserved: dict[str, Any] = {}
    runtime_fields = runtime_state_fields_from_sources(*sources, context=context)
    for field in PRESERVED_ENTITY_FIELDS:
        if field in RUNTIME_STATE_FIELDS:
            if field in runtime_fields:
                preserved[field] = copy.deepcopy(runtime_fields[field])
            continue
        for source in sources:
            if field in source and source[field] not in (None, ""):
                preserved[field] = copy.deepcopy(source[field])
                break
            for nested_key in ("initial_state", "visual_state"):
                nested = source.get(nested_key)
                if (
                    isinstance(nested, dict)
                    and field in nested
                    and nested[field] not in (None, "")
                ):
                    preserved[field] = copy.deepcopy(nested[field])
                    break
            if field in preserved:
                break
    return preserved


def weather_payload(
    profile: str, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    key = str(profile or "clear").strip().lower()
    payload = copy.deepcopy(WEATHER_PROFILES.get(key))
    if payload is None:
        raise RuntimeError(f"Unknown deterministic weather profile: {profile}")
    payload["condition"] = str(profile or payload.get("condition") or key)
    override_values = dict(overrides or {})
    unknown = set(override_values) - WEATHER_OVERRIDE_FIELDS
    if unknown:
        raise ValueError(f"weather profile {profile}: unknown override keys {sorted(unknown)}")
    for field, value in override_values.items():
        if field in WEATHER_BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"weather profile {profile}: invalid {field}")
        elif type(value) not in (int, float) or not math.isfinite(float(value)):
            raise ValueError(f"weather profile {profile}: invalid {field}")
    for canonical, alias in (("visibility_m", "visibility"), ("fog_density", "fog")):
        if (
            canonical in override_values
            and alias in override_values
            and override_values[canonical] != override_values[alias]
        ):
            raise ValueError(
                f"weather profile {profile}: conflicting {canonical} and {alias} overrides"
            )
    for field, value in override_values.items():
        payload[field] = value
        if field == "visibility_m":
            payload["visibility"] = value
        elif field == "visibility":
            payload["visibility_m"] = value
        elif field == "fog":
            payload["fog_density"] = value
        elif field == "fog_density":
            payload["fog"] = value
    if "visibility_m" not in payload and "visibility" in payload:
        payload["visibility_m"] = payload["visibility"]
    if "visibility" not in payload and "visibility_m" in payload:
        payload["visibility"] = payload["visibility_m"]
    if "fog" not in payload and "fog_density" in payload:
        payload["fog"] = payload["fog_density"]
    if "fog_density" not in payload and "fog" in payload:
        payload["fog_density"] = payload["fog"]
    for field in ("rain", "wetness", "fog_density", "wind_speed", "visibility_m"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"weather profile {profile}: missing or invalid {field}")
        if not math.isfinite(float(value)):
            raise ValueError(f"weather profile {profile}: non-finite {field}")
    for field in WEATHER_NUMERIC_FIELDS | WEATHER_ALIASES.keys():
        if field not in payload:
            continue
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"weather profile {profile}: invalid {field}")
    for field in WEATHER_BOOLEAN_FIELDS:
        if field in payload and not isinstance(payload[field], bool):
            raise ValueError(f"weather profile {profile}: invalid {field}")
    for field in WEATHER_FRACTION_FIELDS:
        if not 0.0 <= float(payload[field]) <= 1.0:
            raise ValueError(f"weather profile {profile}: {field} must be within [0, 1]")
    return payload


def initial_weather_state(scene_setup: dict[str, Any]) -> dict[str, Any]:
    profile = dict(scene_setup.get("weather_profile") or {})
    return weather_payload(str(profile.get("initial") or "clear"))


def scheduled_weather_transitions(
    scene_setup: dict[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    transitions: dict[int, list[dict[str, Any]]] = {}
    for transition in (scene_setup.get("weather_profile") or {}).get(
        "transitions"
    ) or []:
        tick = _to_int(transition.get("tick"), 0)
        if tick < 0:
            raise RuntimeError(f"Weather transition tick is negative: {transition}")
        transitions.setdefault(tick, []).append(
            weather_payload(
                str(transition.get("profile") or "clear"),
                dict(transition.get("overrides") or {}),
            )
        )
    return transitions


def row_activity_payload(label_class: str, state: str) -> dict[str, Any]:
    if label_class == "pedestrian":
        activity = get_activity(state)
        return {
            "activity_type": activity.activity_type,
            "animation_hint": activity.animation_hint,
            "posture": activity.posture,
            "social_state": activity.social_state,
        }
    return {
        "activity_type": state,
        "animation_hint": state,
        "posture": "standing",
        "social_state": "solo",
    }


def route_motion_schedule(
    *,
    scenario_id: str,
    entity_id: str,
    label_class: str,
    initial_pos: Sequence[float],
    route_waypoints: list[Any],
    initial_state: str,
    ground_flow_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not route_waypoints:
        return []
    velocity = 4.0
    if label_class == "uav":
        velocity = 5.0
    elif label_class == "vehicle":
        velocity = 6.0
    elif label_class == "pedestrian":
        velocity = 1.25
    ground_flow = dict(ground_flow_contract or {})
    is_continuous_ground_flow = (
        str(ground_flow.get("policy") or "") == "continuous_capture_ground_flow_v1"
    )
    if str(ground_flow.get("policy") or "") == "continuous_capture_ground_flow_v1":
        velocity = max(0.1, _to_float(ground_flow.get("speed_mps", velocity), velocity))
        route_waypoints = [vector3(waypoint) for waypoint in route_waypoints]
    activity_type = initial_state
    post_activity_type = initial_state
    if label_class == "pedestrian":
        activity_type = normalize_activity_type("walking", moving=True)
        post_activity_type = normalize_activity_type(initial_state or "waiting")
        if is_continuous_ground_flow:
            route_ticks = int(
                math.ceil(
                    path_length_m([vector3(initial_pos), *route_waypoints])
                    / velocity
                    * TICK_HZ
                )
            )
            try:
                contract_ticks = int(ground_flow.get("route_duration_ticks") or 0)
            except (TypeError, ValueError):
                contract_ticks = 0
            post_activity_type = (
                _background_pedestrian_idle_activity(entity_id)
                if contract_ticks > 0 and route_ticks < contract_ticks
                else activity_type
            )
    elif is_continuous_ground_flow:
        post_activity_type = activity_type
    return [
        {
            "type": "move",
            "tick": 0,
            "waypoints_enu_m": [vector3(waypoint) for waypoint in route_waypoints],
            "velocity_mps": velocity,
            "activity_type": activity_type,
            "post_activity_type": post_activity_type,
            "source": "scene_setup.route_waypoints_enu_m",
            "action_id": f"{scenario_id}.{entity_id}.route_waypoints",
            "start_pos_enu": vector3(initial_pos),
        }
    ]


def build_entities(
    scene_setup: dict[str, Any], script: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    scenario_id = str(script.get("scenario_id") or "")
    for scene_entity in scene_setup.get("entities") or []:
        entity_id = str(
            scene_entity.get("entity_id") or scene_entity.get("instance_id") or ""
        )
        if not entity_id:
            continue
        logical_asset_id = str(scene_entity.get("logical_asset_id") or "")
        category = str(scene_entity.get("category") or "")
        initial_state = dict(scene_entity.get("initial_state") or {})
        activation_tick = int(scene_entity.get("activation_tick") or 0)
        spawn_policy = str(scene_entity.get("spawn_policy") or "").lower()
        initial_activity = (
            initial_state.get("activity_type")
            or (initial_state.get("state_facets") or {})
            .get("activity", {})
            .get("activity_type")
            or initial_state.get("mode")
            or "idle"
        )
        if category == "pedestrian":
            initial_activity = normalize_activity_type(
                str(initial_activity or "waiting")
            )
        label_class = label_class_for(entity_id, logical_asset_id, category)
        position = position_from_placement(scene_entity)
        preserved = preserved_fields_from(
            scene_entity,
            context=f"{scenario_id}.entities[{entity_id}]",
        )
        lifecycle = scene_entity.get("lifecycle", {})
        requires_takeoff = lifecycle.get("requires_takeoff", False)
        if type(requires_takeoff) is not bool:
            raise RuntimeError(f"{entity_id}: lifecycle.requires_takeoff must be Boolean")
        # Authored routes are initial locomotion only for already active
        # entities that are not waiting for a takeoff command. Future event
        # membership and entity spelling are not initialization authorities.
        auto_route = bool(scene_entity.get("route_waypoints_enu_m")) and not (
            requires_takeoff or initial_activity == "preflight_on_pad"
            or spawn_policy == "event_script_only"
        )
        route = list(scene_entity.get("route_waypoints_enu_m") or [])
        inspect = (scene_entity.get("contract_inspect_uav") is True
                   or initial_state.get("role") == "U_inspect")
        if inspect and auto_route:
            if label_class != "uav" or len(route) < 2:
                raise RuntimeError(f"{entity_id}: inspector requires typed UAV and declared loop route")
            route = extend_loop_route(
                position, [vector3(point) for point in route],
                velocity_mps=INSPECT_UAV_LOOP_SPEED_MPS,
                duration_ticks=DEFAULT_DURATION_TICKS,
                min_motion_ratio=INSPECT_UAV_MIN_MOTION_RATIO,
            )
            initial_activity = "inspect_racetrack"
        entities[entity_id] = {
            "entity_id": entity_id,
            "pos": position,
            "label_class": label_class,
            "asset_id": logical_asset_id,
            "state": str(initial_activity or "idle"),
            "active_from": activation_tick
            if activation_tick > 0 or spawn_policy == "event_script_only"
            else 0,
            "inactive_from": None,
            "spawned": spawn_policy != "event_script_only",
            "initial_yaw_deg": _entity_yaw_deg({"scene_setup": scene_entity}),
            "scene_setup": copy.deepcopy(scene_entity),
            "schedules": (
                route_motion_schedule(
                    scenario_id=scenario_id,
                    entity_id=entity_id,
                    label_class=label_class,
                    initial_pos=position,
                    route_waypoints=route,
                    initial_state=str(initial_activity or "idle"),
                    ground_flow_contract=dict(
                        scene_entity.get("ground_flow_contract") or {}
                    ),
                )
                if auto_route
                else []
            ),
            **preserved,
        }
    return entities


class EpisodeStateEngine:
    def __init__(
        self,
        scene_setup: dict[str, Any],
        script: dict[str, Any],
        script_path: Path,
        duration_ticks: int,
        *,
        variation_seed: int = 0,
    ) -> None:
        # None reproduces the existing seed-independent draws (formal sources and
        # ARM factual replays); generate_episode passes its episode seed.
        self.variation_seed = variation_seed
        self.scene_setup = scene_setup
        self.script = script
        self.script_path = script_path
        self.duration_ticks = int(duration_ticks)
        self.scenario_id = str(script.get("scenario_id") or script_path.parent.name)
        self.params = dict(script.get("parameters") or {})
        self.entities = build_entities(scene_setup, script)
        self.keyframes: dict[str, list[tuple[int, list[float], str]]] = {
            entity_id: self._frames_for_entity(entity)
            for entity_id, entity in self.entities.items()
        }
        self.weather = initial_weather_state(scene_setup)
        self.weather_transitions = scheduled_weather_transitions(scene_setup)
        self.trajectory_rows: list[dict[str, Any]] = []
        self.weather_rows: list[dict[str, Any]] = []
        self.executed_actions: list[dict[str, Any]] = []
        self.pending_runtime_state_patches: dict[
            int, list[tuple[str, dict[str, dict[str, Any]]]]
        ] = {}
        self.last_yaw_by_entity = {
            entity_id: float(entity.get("initial_yaw_deg") or 0.0)
            for entity_id, entity in self.entities.items()
        }

    def _frames_for_entity(
        self, entity: dict[str, Any]
    ) -> list[tuple[int, list[float], str]]:
        return keyframes_for(
            vector3(entity["pos"]),
            list(entity.get("schedules") or []),
            str(entity.get("state") or "idle"),
            pedestrian=str(entity.get("label_class") or "") == "pedestrian",
        )

    def _refresh_entity_frames(self, entity_id: str) -> None:
        self.keyframes[entity_id] = self._frames_for_entity(self.entities[entity_id])

    def _entity_sample(
        self, entity_id: str, tick: int
    ) -> tuple[list[float], list[float], str]:
        entity = self.entities[entity_id]
        if tick < int(entity.get("active_from") or 0):
            return vector3(entity["pos"]), [0.0, 0.0, 0.0], "offstage"
        inactive_from = entity.get("inactive_from")
        if inactive_from is not None and tick >= int(inactive_from):
            return vector3(entity["pos"]), [0.0, 0.0, 0.0], "offstage"
        return sample_keyframes(self.keyframes[entity_id], tick)

    def _entity_active_at(self, entity_id: str, tick: int) -> bool:
        entity = self.entities[entity_id]
        if entity.get("spawned") is not True:
            return False
        if tick < int(entity.get("active_from") or 0):
            return False
        inactive_from = entity.get("inactive_from")
        return inactive_from is None or tick < int(inactive_from)

    def _entity_pos_at(self, entity_id: str, tick: int) -> list[float]:
        return self._entity_sample(entity_id, tick)[0]

    def _append_action_result(
        self, action: dict[str, Any], status: str, **extra: Any
    ) -> dict[str, Any]:
        result = {"status": status, **extra}
        self.executed_actions.append(
            {
                "tick": int(extra.get("tick", -1)),
                "action_id": str(action.get("action_id") or ""),
                "type": str(action.get("type") or ""),
                "entity_id": str(action.get("entity_id") or action.get("ped_id") or ""),
                "result": result,
            }
        )
        return result

    def _handle_move_entity(self, action: dict[str, Any], tick: int) -> dict[str, Any]:
        entity_id = str(resolve_param(action.get("entity_id", ""), self.params) or "")
        if entity_id not in self.entities:
            raise RuntimeError(f"move_entity references undeclared entity {entity_id}")
        entity = self.entities[entity_id]
        label_class = str(entity.get("label_class") or "")
        waypoints_raw = resolve_param(action.get("waypoints_enu_m", []), self.params)
        waypoints = (
            [vector3(point) for point in waypoints_raw]
            if isinstance(waypoints_raw, list)
            else []
        )
        if not waypoints:
            raise RuntimeError(
                f"move_entity action has no waypoints: {action.get('action_id')}"
            )
        action_id = str(
            action.get("action_id") or f"{self.scenario_id}.{entity_id}.move_at_{tick}"
        )
        activity_type = action.get("activity_type")
        post_activity_type = action.get("post_activity_type")
        if label_class == "pedestrian":
            activity_type = normalize_activity_type(
                str(activity_type or "walking"), moving=True
            )
            post_activity_type = normalize_activity_type(
                str(post_activity_type or "waiting")
            )
        current_pos = self._entity_pos_at(entity_id, tick)
        varied_tick, varied_velocity, varied_waypoints = _apply_move_variation(
            scenario_id=self.scenario_id,
            entity_id=entity_id,
            action_id=action_id,
            variation_seed=self.variation_seed,
            label_class=label_class,
            tick=tick,
            velocity_mps=_to_float(action.get("velocity_mps", 1.0), 1.0),
            start_pos=current_pos,
            waypoints=waypoints,
            action=action,
            params=self.params,
        )
        if label_class == "uav":
            terminal_contract = action.get("terminal_feasibility")
            landing_reference = (
                terminal_contract.get("landing_reference_enu_m")
                if isinstance(terminal_contract, Mapping)
                else None
            )
            activity_type, post_activity_type = infer_uav_move_activity(
                current_pos,
                varied_waypoints,
                activity_type,
                post_activity_type,
                ground_reference_z_m=float(entity["ground_reference_z_m"]),
                landing_reference_enu_m=landing_reference,
            )
        schedule = {
            "type": "move",
            "tick": varied_tick,
            "waypoints_enu_m": varied_waypoints,
            "velocity_mps": varied_velocity,
            "activity_type": activity_type,
            "post_activity_type": post_activity_type,
            "source": "event_script.move_entity",
            "source_event_tick": tick,
            "action_id": action_id,
            "start_pos_enu": current_pos,
        }
        entity.setdefault("schedules", []).append(schedule)
        self._refresh_entity_frames(entity_id)
        return self._append_action_result(
            action,
            "ok",
            tick=tick,
            scheduled_tick=varied_tick,
            entity_id=entity_id,
            path_length_m=round(path_length_m([current_pos, *varied_waypoints]), 6),
        )

    def _handle_set_visual_state(
        self, action: dict[str, Any], tick: int
    ) -> dict[str, Any]:
        entity_id = str(resolve_param(action.get("entity_id", ""), self.params) or "")
        if entity_id not in self.entities:
            raise RuntimeError(
                f"set_visual_state references undeclared entity {entity_id}"
            )
        visual_state = dict(action.get("visual_state") or {})
        mode = str(visual_state.get("mode") or action.get("mode") or "")
        if mode:
            entity = self.entities[entity_id]
            position = self._entity_pos_at(entity_id, tick)
            label = entity["label_class"]
            stop_motion = (not get_activity(mode).moving if label == "pedestrian"
                           else mode in {"hold", "hover"} if label == "uav"
                           else mode in {"stopped", "blocked", "parked"} if label == "vehicle"
                           else False)
            self.entities[entity_id]["state"] = mode
            self.entities[entity_id].setdefault("schedules", []).append(
                {
                    "type": "activity",
                    "tick": tick,
                    "activity_type": mode,
                    "source": "event_script.set_visual_state",
                    "start_pos_enu": position,
                    "stop_motion": stop_motion,
                    "action_id": str(action.get("action_id") or ""),
                }
            )
            self._refresh_entity_frames(entity_id)
        self.entities[entity_id].update(
            preserved_fields_from(
                visual_state,
                context=f"{self.scenario_id}.{action.get('action_id') or 'set_visual_state'}.visual_state",
            )
        )
        return self._append_action_result(
            action, "ok", tick=tick, entity_id=entity_id, mode=mode
        )

    def _handle_set_pedestrian_activity(
        self, action: dict[str, Any], tick: int
    ) -> dict[str, Any]:
        entity_id = str(
            resolve_param(
                action.get("entity_id", action.get("ped_id", "")), self.params
            )
            or ""
        )
        if entity_id not in self.entities:
            raise RuntimeError(
                f"set_pedestrian_activity references undeclared entity {entity_id}"
            )
        activity_type = normalize_activity_type(
            str(action.get("activity_type") or "waiting")
        )
        # ``set_pedestrian_activity`` changes the display label only.  The authored
        # script pauses a pedestrian by dispatching an explicit stopping activity and
        # lets an in-flight ``move_entity`` finish on its own; a label dispatched
        # mid-motion must not truncate the keyframes that move already published.
        # Verified against every shipped factual: adding start_pos_enu/stop_motion here
        # cuts the walk short and drops authored events (for example L4-3_v1
        # crowd_proximity_response and safe_landing never fire).
        self.entities[entity_id]["state"] = activity_type
        self.entities[entity_id].setdefault("schedules", []).append(
            {
                "type": "activity",
                "tick": tick,
                "activity_type": activity_type,
                "source": "event_script.set_pedestrian_activity",
                "action_id": str(action.get("action_id") or ""),
            }
        )
        self._refresh_entity_frames(entity_id)
        return self._append_action_result(
            action, "ok", tick=tick, entity_id=entity_id, activity_type=activity_type
        )

    def _handle_set_weather(self, action: dict[str, Any], tick: int) -> dict[str, Any]:
        profile = str(action.get("profile") or "clear")
        self.weather = weather_payload(profile, dict(action.get("overrides") or {}))
        return self._append_action_result(action, "ok", tick=tick, profile=profile)

    def _handle_set_runtime_state(
        self, action: dict[str, Any], tick: int
    ) -> dict[str, Any]:
        entity_id = str(resolve_param(action.get("entity_id", ""), self.params) or "")
        if not entity_id:
            raise RuntimeError(
                f"{action.get('action_id') or 'set_runtime_state'}: entity_id is required"
            )
        if entity_id not in self.entities:
            raise RuntimeError(
                f"set_runtime_state references undeclared entity {entity_id}"
            )
        patch = runtime_state_patch_from_action(action, self.params)
        delay_ticks = _to_int(action.get("delay_ticks"), 0)
        if delay_ticks < 0:
            raise RuntimeError(
                f"{action.get('action_id') or 'set_runtime_state'}: delay_ticks must be non-negative"
            )
        effective_tick = tick + max(1, delay_ticks)
        if effective_tick > self.duration_ticks:
            raise RuntimeError(
                f"{action.get('action_id') or 'set_runtime_state'}: effective tick "
                f"{effective_tick} exceeds episode duration {self.duration_ticks}"
            )
        self.pending_runtime_state_patches.setdefault(effective_tick, []).append(
            (entity_id, copy.deepcopy(patch))
        )
        return self._append_action_result(
            action,
            "ok",
            tick=tick,
            scheduled_tick=effective_tick,
            effective_tick=effective_tick,
            entity_id=entity_id,
            state_families=sorted(patch),
        )

    def _apply_pending_runtime_state_patches(self, tick: int) -> None:
        for entity_id, patch in self.pending_runtime_state_patches.pop(tick, []):
            entity = self.entities[entity_id]
            for family, family_patch in patch.items():
                current = entity.get(family)
                if not isinstance(current, dict):
                    current = {}
                entity[family] = deep_merge_mapping(current, family_patch)

    def _handle_spawn_entity(self, action: dict[str, Any], tick: int) -> dict[str, Any]:
        entity_id = str(resolve_param(action.get("entity_id", ""), self.params) or "")
        if entity_id not in self.entities:
            raise RuntimeError(f"spawn_entity references undeclared entity {entity_id}")
        position = vector3(
            resolve_param(
                action.get("position_enu_m", self.entities[entity_id]["pos"]),
                self.params,
            )
        )
        entity = self.entities[entity_id]
        if self._entity_active_at(entity_id, tick):
            raise RuntimeError(
                f"spawn_entity references already-active entity {entity_id} at tick {tick}"
            )
        entity["pos"] = position
        entity["active_from"] = tick
        entity["inactive_from"] = None
        entity["spawned"] = True
        if action.get("asset_id"):
            entity["asset_id"] = str(
                resolve_param(action.get("asset_id"), self.params)
                or entity.get("asset_id")
                or ""
            )
        entity.setdefault("schedules", []).append(
            {
                "type": "activity",
                "tick": tick,
                "activity_type": str(
                    (action.get("visual_state") or {}).get("mode")
                    or entity.get("state")
                    or "spawned"
                ),
                "source": "event_script.spawn_entity",
                "action_id": str(action.get("action_id") or ""),
            }
        )
        self._refresh_entity_frames(entity_id)
        return self._append_action_result(action, "ok", tick=tick, entity_id=entity_id)

    def _handle_remove_entity(self, action: dict[str, Any], tick: int) -> dict[str, Any]:
        entity_id = str(resolve_param(action.get("entity_id", ""), self.params) or "")
        if entity_id not in self.entities:
            raise RuntimeError(f"remove_entity references undeclared entity {entity_id}")
        if not self._entity_active_at(entity_id, tick):
            raise RuntimeError(
                f"remove_entity references inactive entity {entity_id} at tick {tick}"
            )
        self.entities[entity_id]["inactive_from"] = tick + 1
        return self._append_action_result(
            action,
            "ok",
            tick=tick,
            effective_tick=tick + 1,
            entity_id=entity_id,
        )

    def _handle_capture_screenshot(
        self, action: dict[str, Any], tick: int
    ) -> dict[str, Any]:
        return self._append_action_result(
            action,
            "ok",
            tick=tick,
            capture_id=str(action.get("action_id") or action.get("camera_id") or ""),
        )

    def _handle_noop(self, action: dict[str, Any], tick: int) -> dict[str, Any]:
        atype = str(action.get("type") or "")
        if atype in {"sequence", "play_animation", "spawn_crowd"}:
            return self._append_action_result(action, "ok", tick=tick)
        raise RuntimeError(f"Unhandled deterministic event action type: {atype}")

    def _row_for_entity(self, entity_id: str, tick: int) -> dict[str, Any]:
        entity = self.entities[entity_id]
        pos, vel, state = self._entity_sample(entity_id, tick)
        xy_speed = math.hypot(float(vel[0]), float(vel[1]))
        if xy_speed > GROUND_MOTION_SPEED_EPS_MPS:
            self.last_yaw_by_entity[entity_id] = math.degrees(
                math.atan2(float(vel[1]), float(vel[0]))
            )
        row = {
            "tick": tick,
            "entity_id": entity_id,
            "label_class": entity["label_class"],
            "asset_id": entity["asset_id"],
            "pos_enu": [round(float(value), 6) for value in pos],
            "vel_mps": [round(float(value), 6) for value in vel],
            "yaw_deg": round(float(self.last_yaw_by_entity[entity_id]), 6),
            "state": state,
            **row_activity_payload(str(entity["label_class"]), state),
        }
        row.update(preserved_fields_from(entity))
        return row

    def _record_tick_rows(self, tick: int) -> list[dict[str, Any]]:
        rows = [
            self._row_for_entity(entity_id, tick)
            for entity_id in sorted(self.entities)
            if self._entity_active_at(entity_id, tick)
        ]
        self.trajectory_rows.extend(rows)
        weather_row = {"tick": tick, **copy.deepcopy(self.weather)}
        self.weather_rows.append(weather_row)
        return rows

    def run(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        sys.path.insert(
            0,
            str(
                Path(__file__).resolve().parents[2]
                / "Plugins"
                / "SumoImporter"
                / "Scripts"
            ),
        )
        from donghu_core.event_script_interpreter import EventScriptInterpreter

        interpreter = EventScriptInterpreter(self.script_path)
        current_tick = 0
        interpreter.register_handler(
            "move_entity", lambda action: self._handle_move_entity(action, current_tick)
        )
        interpreter.register_handler(
            "set_visual_state",
            lambda action: self._handle_set_visual_state(action, current_tick),
        )
        interpreter.register_handler(
            "set_pedestrian_activity",
            lambda action: self._handle_set_pedestrian_activity(action, current_tick),
        )
        interpreter.register_handler(
            "set_weather", lambda action: self._handle_set_weather(action, current_tick)
        )
        interpreter.register_handler(
            "set_runtime_state",
            lambda action: self._handle_set_runtime_state(action, current_tick),
        )
        interpreter.register_handler(
            "spawn_entity",
            lambda action: self._handle_spawn_entity(action, current_tick),
        )
        interpreter.register_handler(
            "capture_screenshot",
            lambda action: self._handle_capture_screenshot(action, current_tick),
        )
        interpreter.register_handler(
            "remove_entity", lambda action: self._handle_remove_entity(action, current_tick)
        )
        interpreter.register_handler(
            "sequence", lambda action: self._handle_noop(action, current_tick)
        )
        interpreter.register_handler(
            "play_animation", lambda action: self._handle_noop(action, current_tick)
        )
        interpreter.register_handler(
            "spawn_crowd", lambda action: self._handle_noop(action, current_tick)
        )

        for tick in range(self.duration_ticks + 1):
            current_tick = tick
            self._apply_pending_runtime_state_patches(tick)
            for payload in self.weather_transitions.get(tick, []):
                self.weather = payload
            rows = self._record_tick_rows(tick)
            for row in rows:
                interpreter.update_entity_state(
                    row["entity_id"], row["pos_enu"], {}, row["vel_mps"]
                )
                if str(row.get("label_class") or "") == "pedestrian":
                    interpreter.update_entity_activity(
                        row["entity_id"], str(row.get("activity_type") or "")
                    )
            interpreter.update_weather_state(self.weather_rows[-1])
            for entry in interpreter.tick(tick):
                result = dict(entry.get("result") or {})
                action_type = str(entry.get("type") or "")
                if (
                    action_type in {"set_runtime_state", "set_visual_state"}
                    and result.get("status") != "ok"
                ):
                    raise RuntimeError(
                        f"{self.scenario_id}: {action_type} action failed at tick {tick}: "
                        f"{result.get('message') or result.get('reason') or result}"
                    )

        event_log = interpreter.get_event_log()
        enrich_event_log_validation_fields(event_log, self.script, self.scenario_id)
        fired_event_ids = {
            remove_prefix(
                str(row.get("topic") or row.get("source_event_id") or ""),
                f"evt_{self.scenario_id}_",
            )
            for row in event_log
        }
        logged_event_topics = {str(row.get("topic") or "") for row in event_log}
        missing: list[str] = []
        for event_def in self.script.get("events") or []:
            event_id = str(event_def.get("event_id") or "")
            if not event_id:
                continue
            log_event = dict(event_def.get("log_event") or {})
            topic = str(log_event.get("topic") or event_id)
            if topic not in logged_event_topics and event_id not in fired_event_ids:
                missing.append(event_id)
        if missing:
            raise RuntimeError(
                f"{self.scenario_id}: declared events did not fire in deterministic episode simulation: {missing}"
            )

        event_realizations = build_event_realizations(
            scenario_id=self.scenario_id,
            script=self.script,
            event_log=event_log,
            executed_actions=self.executed_actions,
            trajectory_rows=self.trajectory_rows,
            params=self.params,
        )

        roster: dict[str, dict[str, Any]] = {}
        for entity_id, entity in sorted(self.entities.items()):
            roster_entry = {
                "entity_id": entity_id,
                "label_class": entity["label_class"],
                "asset_id": entity["asset_id"],
                "initial_yaw_deg": round(float(entity["initial_yaw_deg"]), 6),
                **preserved_fields_from(entity),
            }
            if str(entity.get("spawn_policy") or "") == "event_script_only":
                # Scene setup uses an out-of-window sentinel for conditionally
                # spawned entities. Persist the tick actually established by
                # the deterministic event simulation instead.
                roster_entry["activation_tick"] = int(entity["active_from"])
            if entity.get("inactive_from") is not None:
                roster_entry["deactivation_tick"] = int(entity["inactive_from"])
            roster[entity_id] = roster_entry
        return (
            self.trajectory_rows,
            self.weather_rows,
            event_log,
            event_realizations,
            roster,
        )


def generate_episode(
    script_path: Path,
    episode_dir: Path,
    dataset_root: Path,
    seed: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if episode_dir.exists():
        shutil.rmtree(episode_dir)
    episode_dir.mkdir(parents=True, exist_ok=True)

    script = read_json(script_path)
    scene_setup = (
        read_json(script_path.with_name("scene_setup.json"))
        if script_path.with_name("scene_setup.json").exists()
        else {}
    )
    scenario_id = str(script.get("scenario_id") or script_path.parent.name)
    duration = int(
        script.get("parameters", {}).get("duration_ticks") or DEFAULT_DURATION_TICKS
    )

    engine = EpisodeStateEngine(scene_setup, script, script_path, duration, variation_seed=seed)
    trajectories, weather, event_log, event_realizations, roster = engine.run()
    write_jsonl(episode_dir / "trajectories.jsonl", trajectories)
    write_jsonl(episode_dir / "weather_meta.jsonl", weather)
    write_jsonl(episode_dir / "event_trace.jsonl", event_log)
    write_jsonl(episode_dir / "event_realization.jsonl", event_realizations)
    write_json(episode_dir / "global_entity_roster.json", roster)

    manifest = {
        "episode_id": episode_dir.name,
        "scenario_id": scenario_id,
        "duration_ticks": duration,
        "seed": seed,
        "n_events": len(event_log),
        "n_event_realizations": len(event_realizations),
        "n_entities": len(roster),
        "source_event_script_path": project_relative(script_path, dataset_root),
        "source_scene_setup_path": project_relative(
            script_path.with_name("scene_setup.json"), dataset_root
        ),
        "generator": "Dataset/tools/batch_generate.py",
        "artifacts": {
            "trajectories": project_relative(
                episode_dir / "trajectories.jsonl", dataset_root
            ),
            "weather_meta": project_relative(
                episode_dir / "weather_meta.jsonl", dataset_root
            ),
            "event_trace": project_relative(
                episode_dir / "event_trace.jsonl", dataset_root
            ),
            "event_realization": project_relative(
                episode_dir / "event_realization.jsonl", dataset_root
            ),
            "global_entity_roster": project_relative(
                episode_dir / "global_entity_roster.json", dataset_root
            ),
        },
    }
    write_json(episode_dir / "episode_manifest.json", manifest)
    explicit_vehicle_plan = build_explicit_vehicle_plan(episode_dir)
    script_controlled_vehicle_ids = {
        str(entity_id)
        for entity_id, entry in roster.items()
        if str(entry.get("label_class") or entry.get("category") or "") == "vehicle"
        and is_script_controlled_vehicle(entry)
    }
    unexpectedly_planned_vehicle_ids = (
        script_controlled_vehicle_ids
        & planned_source_vehicle_ids(explicit_vehicle_plan)
    ) | planned_script_controlled_source_vehicle_ids(explicit_vehicle_plan)
    if unexpectedly_planned_vehicle_ids:
        raise RuntimeError(
            f"{episode_dir.name}: script-controlled vehicles entered the explicit "
            f"SUMO plan: {sorted(unexpectedly_planned_vehicle_ids)}"
        )
    explicit_vehicle_review = write_explicit_vehicle_plan(
        episode_dir, explicit_vehicle_plan
    )
    manifest["artifacts"].update(
        {
            "sumo_explicit_vehicle_plan": project_relative(
                episode_dir / "sumo_explicit_vehicle_plan.json", dataset_root
            ),
            "vehicle_plan_review": project_relative(
                episode_dir / "vehicle_plan_review.json", dataset_root
            ),
            "vehicle_plan_review_markdown": project_relative(
                episode_dir / "vehicle_plan_review.md", dataset_root
            ),
        }
    )
    manifest["vehicle_source_policy"] = explicit_vehicle_plan["vehicle_source_policy"]
    manifest["sumo_explicit_vehicle_plan"] = {
        "schema": explicit_vehicle_plan["schema"],
        "vehicle_count": explicit_vehicle_review["vehicle_count"],
        "minimum_vehicle_count": explicit_vehicle_review["minimum_vehicle_count"],
        "seed_profile": explicit_vehicle_plan["seed_profile"],
        "traffic_profile": explicit_vehicle_plan["traffic_profile"],
        "review": "vehicle_plan_review.json",
    }
    write_json(episode_dir / "episode_manifest.json", manifest)

    summary: dict[str, Any] = {
        "n_frames": len({row["tick"] for row in trajectories}),
        "n_unique_entities": len(roster),
    }
    return manifest, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch generate grounded episodes")
    parser.add_argument("--dataset-root", default="Dataset")
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Episode output root. Required for isolated repair staging.",
    )
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument(
        "--seed",
        type=int,
        action="append",
        default=[],
        help="Specific seed number to generate. Repeatable; overrides --seeds when present.",
    )
    parser.add_argument(
        "--episode",
        action="append",
        default=[],
        help="Scenario id / episode name to generate. Repeatable.",
    )
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root).resolve()
    output_root = (
        args.output_root.resolve()
        if args.output_root is not None
        else dataset_root / "episodes"
    )
    scenario_scripts = sorted((dataset_root / "scenarios").rglob("event_script.json"))
    if args.episode:
        wanted = {str(value).replace("__seed00", "") for value in args.episode}
        scenario_scripts = [
            path
            for path in scenario_scripts
            if path.parent.name in wanted
            or str(read_json(path).get("scenario_id")) in wanted
        ]
    print(f"Found {len(scenario_scripts)} scenarios")

    total_episodes = 0
    total_events = 0
    selected_seeds = (
        sorted(set(int(seed) for seed in args.seed))
        if args.seed
        else list(range(max(1, int(args.seeds))))
    )
    for script_path in scenario_scripts:
        scenario_name = script_path.parent.name
        for seed in selected_seeds:
            episode_dir = output_root / f"{scenario_name}__seed{seed:02d}"
            manifest, summary = generate_episode(
                script_path,
                episode_dir,
                dataset_root,
                seed,
            )
            total_episodes += 1
            total_events += int(manifest["n_events"])
            print(
                f"  [OK] {episode_dir.name}: {manifest['n_events']} events, "
                f"{summary.get('n_frames', 0)} frames, {summary.get('n_unique_entities', 0)} entities"
            )
    print(f"\nGenerated {total_episodes} episodes with {total_events} total events")


if __name__ == "__main__":
    main()
