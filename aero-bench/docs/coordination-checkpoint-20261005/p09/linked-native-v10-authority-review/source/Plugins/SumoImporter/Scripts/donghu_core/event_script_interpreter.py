#!/usr/bin/env python3
"""Declarative JSON event script interpreter for Aero simulation.

Loads a JSON event script and evaluates triggers / fires actions each tick.
The interpreter stays decoupled from AeroSimClient — action handlers are
registered by the host (EpisodeRenderHost) so the interpreter is unit-testable
without a running UE instance.

Schema: event_script_v1
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TriggerState:
    trigger_id: str
    is_active: bool = False
    consecutive_true_ticks: int = 0
    last_fired_tick: int = -1
    last_condition_true_tick: int = -1
    fire_count: int = 0


@dataclass
class EventState:
    event_id: str
    fired: bool = False
    last_fired_tick: int = -1
    fire_count: int = 0


@dataclass
class EntityState:
    entity_id: str
    position_enu_m: tuple[float, float, float]
    rotation_deg: dict[str, float] = field(default_factory=dict)
    velocity_enu_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    activity_type: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compare(actual: float, operator: str, expected: float) -> bool:
    if operator == "gte":
        return actual >= expected
    if operator == "lte":
        return actual <= expected
    if operator == "gt":
        return actual > expected
    if operator == "lt":
        return actual < expected
    if operator == "eq":
        return abs(actual - expected) < 1e-6
    return False


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


class EventScriptInterpreter:
    """Loads a JSON event script and evaluates triggers / fires actions each tick.

    Does NOT own an RPC client — actions are dispatched through registered
    handler callbacks so the interpreter stays decoupled from EpisodeRenderHost.

    Usage::

        interp = EventScriptInterpreter(Path("event_script.json"))
        interp.register_handler("set_weather", my_set_weather)
        interp.register_handler("spawn_entity", my_spawn_entity)
        ...
        for tick in range(900):
            interp.update_weather_state(weather_payload)
            interp.update_entity_state("drone_01", (1,2,3), {}, (0,0,0))
            results = interp.tick(tick)
        event_trace = interp.get_event_log()
    """

    def __init__(
        self,
        script_path: Path,
        *,
        parameters: dict[str, Any] | None = None,
        episode_id: str = "",
    ) -> None:
        raw = json.loads(script_path.read_text(encoding="utf-8"))
        self.script = dict(raw)
        self.episode_id = episode_id or self.script.get("episode_id", "")

        # Merge user-supplied parameters over script defaults
        self.parameters: dict[str, Any] = dict(self.script.get("parameters") or {})
        if parameters:
            self.parameters.update(parameters)
        self._resolve_all_param_refs()
        self.event_tick_step = self._resolve_event_tick_step()

        # Per-tick mutable state
        self.trigger_states: dict[str, TriggerState] = {}
        self.event_states: dict[str, EventState] = {}
        self.emitted_events: set[str] = set()
        self.weather_state: dict[str, Any] = {}
        self.entity_states: dict[str, EntityState] = {}
        self.entity_activity_states: dict[str, str] = {}
        self.actions_executed_this_tick: list[dict[str, Any]] = []
        self.event_log: list[dict[str, Any]] = []

        # Registered action handlers: action_type -> callable(action_dict)
        self.action_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

        self._init_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_handler(
        self, action_type: str, handler: Callable[[dict[str, Any]], Any]
    ) -> None:
        """Register a callable that receives the resolved action dict."""
        self.action_handlers[action_type] = handler

    def update_weather_state(self, payload: dict[str, Any]) -> None:
        self.weather_state = dict(payload or {})

    def update_entity_state(
        self,
        entity_id: str,
        position_enu_m: list[float] | tuple[float, float, float],
        rotation_deg: dict[str, float] | None = None,
        velocity_enu_mps: list[float] | tuple[float, float, float] | None = None,
    ) -> None:
        px, py, pz = (
            float(position_enu_m[0]),
            float(position_enu_m[1]),
            float(position_enu_m[2]) if len(position_enu_m) > 2 else 0.0,
        )
        vx, vy, vz = (0.0, 0.0, 0.0)
        if velocity_enu_mps is not None:
            v = velocity_enu_mps
            vx, vy, vz = (
                float(v[0]),
                float(v[1]),
                float(v[2]) if len(v) > 2 else 0.0,
            )
        self.entity_states[entity_id] = EntityState(
            entity_id=entity_id,
            position_enu_m=(px, py, pz),
            rotation_deg=dict(rotation_deg or {}),
            velocity_enu_mps=(vx, vy, vz),
            activity_type=self.entity_activity_states.get(entity_id, ""),
        )

    def update_entity_activity(self, entity_id: str, activity_type: str) -> None:
        activity = str(activity_type or "").strip().lower()
        self.entity_activity_states[str(entity_id)] = activity
        state = self.entity_states.get(str(entity_id))
        if state is not None:
            state.activity_type = activity

    def get_entity_activity(self, entity_id: str) -> str:
        state = self.entity_states.get(str(entity_id))
        if state is not None and state.activity_type:
            return state.activity_type
        return self.entity_activity_states.get(str(entity_id), "")

    def tick(self, tick: int) -> list[dict[str, Any]]:
        """Evaluate all triggers, fire eligible events, execute actions.

        Returns a list of dicts describing what was fired/executed this tick.
        """
        self.actions_executed_this_tick.clear()

        # 1. Evaluate all declared triggers
        for trigger_def in self.script.get("triggers") or []:
            self._evaluate_trigger(trigger_def, tick)

        # 2. Evaluate trigger_ref events (sorted by priority: lower = higher priority)
        events = list(self.script.get("events") or [])
        events.sort(key=lambda e: int(e.get("priority", 10)))

        for event_def in events:
            trigger_ref = event_def.get("trigger_ref")
            if not trigger_ref:
                continue
            ts = self.trigger_states.get(trigger_ref)
            if ts is None or not ts.is_active:
                continue
            if not self._check_event_guard(event_def, tick):
                continue
            self._try_fire_event(event_def, tick, reason="trigger")

        # 3. Process events that were emitted THIS tick via on_fire.emit_events
        #    (must happen AFTER step 2 so that intra-tick chains work)
        for event_def in events:
            if event_def["event_id"] in self.emitted_events:
                if not self._check_event_guard(event_def, tick):
                    continue
                self._try_fire_event(event_def, tick, reason="emitted")
        self.emitted_events.clear()

        return list(self.actions_executed_this_tick)

    def get_event_log(self) -> list[dict[str, Any]]:
        return list(self.event_log)

    # ------------------------------------------------------------------
    # Internal: state initialisation
    # ------------------------------------------------------------------

    def _init_state(self) -> None:
        for trigger_def in self.script.get("triggers") or []:
            tid = trigger_def["trigger_id"]
            self.trigger_states[tid] = TriggerState(trigger_id=tid)
        for event_def in self.script.get("events") or []:
            eid = event_def["event_id"]
            self.event_states[eid] = EventState(event_id=eid)

    # ------------------------------------------------------------------
    # Internal: $param resolution
    # ------------------------------------------------------------------

    def _resolve_value(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$param."):
            key = value[len("$param.") :]
            return self.parameters.get(key, value)
        if isinstance(value, list):
            return [self._resolve_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self._resolve_value(v) for k, v in value.items()}
        return value

    def _resolve_all_param_refs(self) -> None:
        """Recursively resolve all $param references in-place after merging user parameters."""

        def _walk(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v) for v in obj]
            return self._resolve_value(obj)

        self.script["triggers"] = _walk(self.script.get("triggers") or [])
        self.script["events"] = _walk(self.script.get("events") or [])

    def _resolve_event_tick_step(self) -> int:
        contract = dict(self.parameters.get("semantic_event_contract") or {})
        raw = (
            self.parameters.get("event_capture_tick_step")
            or self.parameters.get("semantic_event_tick_step")
            or contract.get("event_capture_tick_step")
            or contract.get("semantic_event_tick_step")
            or contract.get("capture_tick_step")
        )
        if raw in (None, "", []) and contract.get("schema") == "low_altitude_event_chain_contract_v1":
            raw = 5
        try:
            return max(1, int(raw or 1))
        except (TypeError, ValueError):
            return 1

    def _event_tick_allowed(self, tick: int) -> bool:
        return self.event_tick_step <= 1 or tick % self.event_tick_step == 0
        if "action_sequences" in self.script:
            self.script["action_sequences"] = _walk(self.script["action_sequences"])

    # ------------------------------------------------------------------
    # Internal: trigger evaluation
    # ------------------------------------------------------------------

    def _evaluate_trigger(self, trigger_def: dict[str, Any], tick: int) -> bool:
        tid = trigger_def["trigger_id"]
        state = self.trigger_states[tid]
        ttype = trigger_def["type"]

        active = False
        if ttype == "tick":
            target_tick = int(trigger_def.get("tick", 0))
            active = tick >= target_tick
        elif ttype == "weather_state":
            active = self._eval_weather_trigger(trigger_def)
        elif ttype == "entity_proximity":
            active = self._eval_proximity_trigger(trigger_def)
        elif ttype == "event_fired":
            ev_id = trigger_def["event_id"]
            ev_state = self.event_states.get(ev_id)
            active = ev_state is not None and ev_state.fired
        elif ttype == "event_fired_after":
            ev_id = trigger_def["event_id"]
            ev_state = self.event_states.get(ev_id)
            delay_ticks = int(trigger_def.get("delay_ticks", 0))
            active = (
                ev_state is not None
                and ev_state.fired
                and ev_state.last_fired_tick >= 0
                and tick - ev_state.last_fired_tick >= delay_ticks
            )
        elif ttype == "composite":
            active = self._eval_composite_trigger(trigger_def, tick)
        # Unknown trigger types default to inactive

        # Track when the raw condition (before min_true/sustain) was last true
        raw_condition_active = active

        # min_true_ticks: must be active for N consecutive ticks before firing
        prev_consecutive = state.consecutive_true_ticks
        if active:
            state.consecutive_true_ticks += 1
        else:
            state.consecutive_true_ticks = 0

        if raw_condition_active:
            state.last_condition_true_tick = tick

        min_true = int(trigger_def.get("min_true_ticks", 0))
        if min_true > 0 and state.consecutive_true_ticks < min_true:
            active = False

        # sustain_ticks: remain active for N ticks after condition becomes false
        sustain = int(trigger_def.get("sustain_ticks", 0))
        if sustain > 0 and not active and prev_consecutive > 0:
            if state.last_condition_true_tick >= 0 and tick - state.last_condition_true_tick <= sustain:
                active = True
                state.consecutive_true_ticks = 1  # keep sustain chain alive

        state.is_active = active
        if active:
            state.last_fired_tick = tick
            state.fire_count += 1
        return active

    def _eval_weather_trigger(self, td: dict[str, Any]) -> bool:
        param = str(td.get("parameter", "rain"))
        expected = float(td.get("value", 0.0))
        aliases = {
            "fog": ("fog", "fog_density"),
            "visibility_m": ("visibility_m", "visibility"),
        }
        actual_raw = 0.0
        for key in aliases.get(param, (param,)):
            if key in self.weather_state:
                actual_raw = self.weather_state.get(key, 0.0)
                break
        actual = float(actual_raw)
        op = str(td.get("operator", "gte"))
        return _compare(actual, op, expected)

    def _eval_proximity_trigger(self, td: dict[str, Any]) -> bool:
        a = self.entity_states.get(str(td.get("entity_a", "")))
        b = self.entity_states.get(str(td.get("entity_b", "")))
        if a is None or b is None:
            return False
        dx = a.position_enu_m[0] - b.position_enu_m[0]
        dy = a.position_enu_m[1] - b.position_enu_m[1]
        dz = a.position_enu_m[2] - b.position_enu_m[2]
        horizontal = math.hypot(dx, dy)
        vertical = abs(dz)
        metric = str(td.get("metric", "xy")).lower()
        if metric == "xy_plus_z":
            horizontal_limit = float(td.get("horizontal_distance_m", td.get("distance_m", 0.0)))
            vertical_limit = float(td.get("vertical_distance_m", td.get("distance_m", 0.0)))
            return horizontal <= horizontal_limit and vertical <= vertical_limit
        if metric == "3d":
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        else:
            dist = horizontal
        return _compare(dist, str(td.get("operator", "lte")), float(td.get("distance_m", 0.0)))

    def _eval_composite_trigger(self, td: dict[str, Any], tick: int) -> bool:
        op = str(td.get("operator", "AND")).upper()
        child_ids: list[str] = td.get("children") or []
        results: list[bool] = []
        for child_id in child_ids:
            child_state = self.trigger_states.get(str(child_id))
            results.append(bool(child_state and child_state.is_active))
        if op == "AND":
            return len(results) > 0 and all(results)
        if op == "OR":
            return any(results)
        return False

    def _find_trigger(self, trigger_id: str) -> dict[str, Any] | None:
        for td in self.script.get("triggers") or []:
            if td.get("trigger_id") == trigger_id:
                return td
        return None

    # ------------------------------------------------------------------
    # Internal: event firing
    # ------------------------------------------------------------------

    def _check_event_guard(self, event_def: dict[str, Any], tick: int) -> bool:
        """Return True if the event is allowed to fire (max_fire_count + cooldown checks)."""
        es = self.event_states.get(event_def["event_id"])
        if es is None:
            return False
        max_fire = event_def.get("max_fire_count", 1)
        if max_fire > 0 and es.fire_count >= max_fire:
            return False
        cooldown = event_def.get("cooldown_ticks", 0)
        if cooldown > 0 and es.fire_count > 0 and tick - es.last_fired_tick < cooldown:
            return False
        return True

    def _try_fire_event(self, event_def: dict[str, Any], tick: int, reason: str) -> bool:
        # Check require_conditions: all named triggers must be active
        for cond_ref in event_def.get("require_conditions") or []:
            ts = self.trigger_states.get(cond_ref)
            if ts is None or not ts.is_active:
                return False
        if not self._event_tick_allowed(tick):
            return False

        # Execute actions
        for action_def in event_def.get("actions") or []:
            resolved = self._resolve_action(action_def)
            atype = resolved.get("type", "")
            handler = self.action_handlers.get(atype)
            if handler is not None:
                try:
                    result = handler(resolved)
                except Exception as exc:
                    result = {"status": "error", "message": str(exc)}
            else:
                result = {"status": "skipped", "reason": f"no handler for action type '{atype}'"}

            self.actions_executed_this_tick.append(
                {
                    "event_id": event_def["event_id"],
                    "action_id": resolved.get("action_id", ""),
                    "type": atype,
                    "result": result,
                    "tick": tick,
                }
            )

        # Update state
        es = self.event_states[event_def["event_id"]]
        es.fired = True
        es.last_fired_tick = tick
        es.fire_count += 1

        # Emit dependent events
        on_fire = event_def.get("on_fire") or {}
        for ev_id in on_fire.get("emit_events") or []:
            self.emitted_events.add(str(ev_id))

        # Log event trace row
        log_cfg = event_def.get("log_event")
        if log_cfg:
            self.event_log.append(
                self._build_event_trace_row(event_def, log_cfg, tick, reason)
            )

        return True

    def _resolve_action(self, action_def: dict[str, Any]) -> dict[str, Any]:
        seq_ref = action_def.get("sequence_ref")
        if seq_ref:
            seq = (self.script.get("action_sequences") or {}).get(seq_ref) or {}
            return {"type": "sequence", "steps": seq.get("steps", [])}
        return dict(action_def)

    # ------------------------------------------------------------------
    # Internal: event trace row builder
    # ------------------------------------------------------------------

    def _build_event_trace_row(
        self,
        event_def: dict[str, Any],
        log_cfg: dict[str, Any],
        tick: int,
        reason: str,
    ) -> dict[str, Any]:
        sim_time_s = round(float(tick) * 0.1, 3)  # assumes 10 Hz; caller can override
        frame_id = f"{self.episode_id}:tick:{tick}" if self.episode_id else f"tick:{tick}"
        topic = str(log_cfg.get("topic", event_def["event_id"]))
        chain_id = str(log_cfg.get("chain_id", event_def.get("chain_id", event_def["event_id"])))
        target_ids = list(log_cfg.get("target_ids") or [])
        semantic_fields: dict[str, Any] = {}
        for field_name in (
            "intent",
            "intent_stage",
            "causal_chain_id",
            "causal_predecessor_intent",
            "target_roles",
        ):
            value = log_cfg.get(field_name)
            if value in (None, "", []):
                value = event_def.get(field_name)
            if value not in (None, "", []):
                semantic_fields[field_name] = copy.deepcopy(value)
        contract = dict(self.parameters.get("semantic_event_contract") or {})
        capture_boundary = dict(self.parameters.get("capture_boundary") or contract.get("capture_boundary") or {})
        pad_boundary_policy = self.parameters.get("pad_boundary_policy")
        if pad_boundary_policy in (None, "", []):
            pad_boundary_policy = contract.get("pad_boundary_policy")
        capture_fields = {
            "capture_boundary_id": capture_boundary.get("boundary_id") or capture_boundary.get("capture_boundary_id"),
            "uav_boundary_crossing_required": contract.get("uav_boundary_crossing_required"),
            "inspect_fov_coverage_required": contract.get("inspect_fov_coverage_required"),
            "pad_boundary_policy": pad_boundary_policy,
        }
        capture_fields = {
            key: copy.deepcopy(value)
            for key, value in capture_fields.items()
            if value not in (None, "", [])
        }
        metadata = dict(log_cfg.get("metadata") or {})
        metadata.update({key: copy.deepcopy(value) for key, value in semantic_fields.items() if key not in metadata})
        metadata.update({key: copy.deepcopy(value) for key, value in capture_fields.items() if key not in metadata})
        if self.event_tick_step > 1:
            metadata.setdefault("event_tick_step", self.event_tick_step)
            metadata.setdefault("event_tick_alignment_policy", "fire_only_on_event_tick_step_grid")
        payload = {
            "activated_tick": tick,
            "category": log_cfg.get("category", ""),
            "causal_delay_ticks": 0,
            "duration_ticks": 0,
            "end_tick": tick,
            "event_id": topic,
            "phase": log_cfg.get("phase", ""),
            "roi_id": log_cfg.get("roi_id", ""),
            "sequence_no": len(self.event_log) + 1,
            "source_kind": "scheduled",
            "source_tick": tick,
            "source_topic": topic,
            "start_tick": tick,
            "title": log_cfg.get("title", topic),
        }
        payload.update(copy.deepcopy(semantic_fields))
        payload.update(copy.deepcopy(capture_fields))
        if self.event_tick_step > 1:
            payload["event_tick_step"] = self.event_tick_step
            payload["event_tick_alignment_policy"] = "fire_only_on_event_tick_step_grid"

        row = {
            "activated_frame_id": frame_id,
            "activated_tick": tick,
            "agent_id": "",
            "causal_delay_ticks": 0,
            "chain_id": chain_id,
            "depth": 0,
            "effect_refs": [],
            "episode_id": self.episode_id,
            "frame_id": frame_id,
            "instance_id": f"evt_{topic}",
            "metadata": metadata,
            "parent_event_id": "",
            "payload": payload,
            "published_event_refs": [],
            "recovered_frame_id": "",
            "recovered_tick": None,
            "render_hints": {
                "overlay": log_cfg.get("overlay", ""),
                "severity": log_cfg.get("severity", "info"),
            },
            "sample_id": f"event_trace:{tick}:{event_def['event_id']}",
            "schema_name": "event_trace",
            "schema_version": "v1",
            "scope": {
                "bbox": [],
                "center": [0.0, 0.0, 0.0],
                "entities": list(target_ids[:4]),
                "fields": [],
                "kind": "entity",
                "relations": [],
                "target_id": target_ids[0] if target_ids else "",
                "world_features": [],
            },
            "semantic_class": "state_event",
            "sensor_id": "",
            "sim_time_s": sim_time_s,
            "source_event_id": topic,
            "source_frame_id": frame_id,
            "source_kind": "scheduled",
            "source_tick": tick,
            "source_topic": topic,
            "state_diff_refs": [],
            "status": "active",
            "target_ids": target_ids,
            "tick": tick,
            "topic": topic,
        }
        row.update(copy.deepcopy(semantic_fields))
        row.update(copy.deepcopy(capture_fields))
        return row
