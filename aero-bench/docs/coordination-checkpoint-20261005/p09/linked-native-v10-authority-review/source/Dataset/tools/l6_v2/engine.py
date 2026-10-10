"""Execute one explicitly configured L6 arm on the shipped engine.

The engine is deterministic given (scene, script, in-memory injection). Every arm
declares its mechanism as: retime a trigger, omit authored actions, and/or inject
explicit actions at a tick. Nothing is inferred from identifiers or intent names.
"""
from __future__ import annotations

from dataclasses import asdict
import copy
from typing import Any, Mapping

from Dataset.tools.l6_v2.common import load, project_path  # noqa: F401  (registers sys.path)
from Dataset.tools.batch_generate import EpisodeStateEngine
from donghu_core.event_script_interpreter import EventScriptInterpreter


def _handlers(engine: EpisodeStateEngine) -> dict[str, Any]:
    return {
        'move_entity': engine._handle_move_entity,
        'set_visual_state': engine._handle_set_visual_state,
        'set_runtime_state': engine._handle_set_runtime_state,
        'spawn_entity': engine._handle_spawn_entity,
        'set_weather': engine._handle_set_weather,
        'set_pedestrian_activity': engine._handle_set_pedestrian_activity,
        'remove_entity': engine._handle_remove_entity,
        'capture_screenshot': engine._handle_capture_screenshot,
    }


def execute(scene_path: str, script_path: str, episode_id: str,
            *, end_tick: int = 900,
            retime: Mapping[str, Any] | None = None,
            omit_action_ids: tuple[str, ...] = (),
            cancel_event_ids: tuple[str, ...] = (),
            injections: tuple[Mapping[str, Any], ...] = (),
            snapshot_ticks: tuple[int, ...] = (),
            fork_tick: int = 0) -> dict[str, Any]:
    """Replay from zero; apply exactly the declared edit after the fork sample.

    Only listed events are cancelled. Unmet dependency triggers remain authored.
    """
    scene = load(project_path(scene_path))
    script = load(project_path(script_path))
    engine = EpisodeStateEngine(scene, script, project_path(script_path), end_tick)
    interpreter = EventScriptInterpreter(project_path(script_path), episode_id=episode_id)

    events = {e['event_id']: e for e in interpreter.script['events']}
    source_actions = {a['action_id'] for e in events.values() for a in e.get('actions', [])}
    omit, cancelled = set(omit_action_ids), set(cancel_event_ids)
    if cancelled - events.keys():
        raise ValueError(f'unknown cancellation targets: {sorted(cancelled - events.keys())}')
    if omit - source_actions:
        raise ValueError(f'unknown action omission targets: {sorted(omit - source_actions)}')
    if not 0 <= fork_tick <= end_tick:
        raise ValueError('fork outside execution horizon')
    if any(not fork_tick <= int(i['tick']) <= end_tick for i in injections):
        raise ValueError('injection outside fork/end interval')
    retimed_trigger = None
    dispositions = []

    def apply_edits():
        nonlocal retimed_trigger
        for event_id in sorted(cancelled):
            if interpreter.event_states[event_id].fired:
                raise ValueError(f'cannot cancel already fired event {event_id}')
            dispositions.append({'event_id': event_id, 'tick': fork_tick,
                                 'status': 'explicitly_cancelled_before_dispatch'})
        interpreter.script['events'] = [e for e in interpreter.script['events']
                                        if e['event_id'] not in cancelled]
        if retime:
            trigger_id = str(retime['trigger_id'])
            matching = [t for t in interpreter.script['triggers'] if t['trigger_id'] == trigger_id]
            if len(matching) != 1:
                raise ValueError(f'retime target not found: {trigger_id}')
            dependents = [e['event_id'] for e in events.values() if e.get('trigger_ref') == trigger_id]
            if any(interpreter.event_states[e].fired for e in dependents):
                raise ValueError(f'cannot retime already fired event trigger {trigger_id}')
            trigger = matching[0]
            field = str(retime['field'])
            trigger[field] = int(trigger[field]) + int(retime['delta'])
            retimed_trigger = {**dict(retime), 'new_value': trigger[field], 'applied_tick': fork_tick}
    handlers = _handlers(engine)
    clock = [0]
    audit: list[dict[str, Any]] = []
    checkpoints: dict[str, dict] = {}

    def motion_effect(action, result):
        if action['type'] != 'move_entity' or result['status'] != 'ok':
            return {}
        frames = engine.keyframes[result['entity_id']]
        terminal = max(frames, key=lambda r: r[0])
        return {'motion_schedule': {'terminal_tick': terminal[0], 'endpoint_enu_m': copy.deepcopy(terminal[1])}}

    def dispatch_source(action: Mapping[str, Any]) -> dict[str, Any]:
        resolved = copy.deepcopy(dict(action))
        if clock[0] >= fork_tick and resolved['action_id'] in omit:
            audit.append({'tick': clock[0], 'origin': 'source_script',
                          'action': resolved, 'result': {'status': 'omitted_by_configuration'}})
            return {'status': 'ok'}
        result = handlers[resolved['type']](resolved, clock[0])
        audit.append({'tick': clock[0], 'origin': 'source_script',
                      'action': copy.deepcopy(resolved), 'result': copy.deepcopy(result),
                      **motion_effect(resolved, result)})
        if result['status'] != 'ok':
            raise RuntimeError(result)
        return result

    for action_type in handlers:
        interpreter.register_handler(action_type, dispatch_source)

    for tick in range(end_tick + 1):
        clock[0] = tick
        engine._apply_pending_runtime_state_patches(tick)
        for weather in engine.weather_transitions.get(tick, []):
            engine.weather = weather
        current = engine._record_tick_rows(tick)
        for row in current:
            interpreter.update_entity_state(row['entity_id'], row['pos_enu'], {}, row['vel_mps'])
            if row.get('label_class') == 'pedestrian':
                interpreter.update_entity_activity(row['entity_id'], row['activity_type'])
        interpreter.update_weather_state(engine.weather_rows[-1])
        if tick in snapshot_ticks:
            checkpoints[str(tick)] = snapshot(engine, interpreter, tick, current)
        if tick == fork_tick:
            apply_edits()
        for fired in interpreter.tick(tick):
            if fired['result']['status'] != 'ok':
                raise RuntimeError(fired)
        for injection in injections:
            if int(injection['tick']) != tick:
                continue
            action = copy.deepcopy({k: v for k, v in injection.items() if k != 'tick'})
            result = handlers[action['type']](action, tick)
            audit.append({'tick': tick, 'origin': 'intervention',
                          'action': copy.deepcopy(action), 'result': copy.deepcopy(result),
                          **motion_effect(action, result)})
            if result['status'] != 'ok':
                raise RuntimeError(result)

    return {
        'engine': engine,
        'interpreter': interpreter,
        'audit': audit,
        'snapshots': checkpoints,
        'retimed_trigger': retimed_trigger,
        'omitted_action_ids': sorted(omit),
        'cancelled_event_ids': sorted(cancelled),
        'event_dispositions': dispositions,
        'event_dispatches': [{'event_id': event_id, 'last_fired_tick': state.last_fired_tick}
                             for event_id, state in interpreter.event_states.items()],
        'injection_count': sum(1 for i in injections),
    }


def snapshot(engine: EpisodeStateEngine, interpreter: EventScriptInterpreter,
             tick: int, sampled_states: list[dict[str, Any]]) -> dict[str, Any]:
    """Sampled dynamic state plus the execution internals needed to reason about it."""
    return {
        'tick': tick,
        'phase': 'after_state_sample_before_actions',
        'sampled_states': copy.deepcopy(sampled_states),
        'entity_definitions': copy.deepcopy(engine.entities),
        'keyframes': copy.deepcopy(engine.keyframes),
        'weather': copy.deepcopy(engine.weather),
        'pending_runtime_state_patches': copy.deepcopy(engine.pending_runtime_state_patches),
        'last_yaw_by_entity': copy.deepcopy(engine.last_yaw_by_entity),
        'interpreter': {
            'trigger_states': {k: asdict(v) for k, v in interpreter.trigger_states.items()},
            'event_states': {k: asdict(v) for k, v in interpreter.event_states.items()},
            'entity_states': {k: asdict(v) for k, v in interpreter.entity_states.items() if v is not None},
            'emitted_events': sorted(interpreter.emitted_events),
            'entity_activity_states': copy.deepcopy(interpreter.entity_activity_states),
            'weather_state': copy.deepcopy(interpreter.weather_state),
        },
    }
