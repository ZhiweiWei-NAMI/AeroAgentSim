"""Small admission adapter around the existing authored motion executor."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from Dataset.tools.l6_v2.engine import EpisodeStateEngine, EventScriptInterpreter, _handlers


def execute_linked(scene, script, script_path, episode_id, *, seed, duration_ticks=900,
                   decision_gate=None, observe_tick=None, action_filter=None,
                   landing_dwell_ticks=None):
    if json.loads(Path(script_path).read_text(encoding='utf-8-sig')) != script:
        raise ValueError('pinned script file differs from the supplied versioned contract')
    if type(seed) is not int or type(duration_ticks) is not int or duration_ticks < 1:
        raise ValueError('seed and duration must be explicit integers')
    if landing_dwell_ticks is not None and (type(landing_dwell_ticks) is not int or landing_dwell_ticks < 1):
        raise ValueError('landing dwell must be an explicit positive original-tick count')
    engine = EpisodeStateEngine(copy.deepcopy(scene), copy.deepcopy(script), Path(script_path),
                                duration_ticks, variation_seed=seed)
    admission_rows, cache, last_status = [], {}, {}

    class AdmittedInterpreter(EventScriptInterpreter):
        def _check_event_guard(self, event_def, tick):
            if not super()._check_event_guard(event_def, tick):
                return False
            if decision_gate is None:
                return True
            key = (event_def['event_id'], tick)
            if key not in cache:
                result = decision_gate(event_def, tick, engine, self)
                if set(result) != {'allow', 'evidence'} or type(result['allow']) is not bool:
                    raise ValueError('admission callback requires exact bool allow and evidence')
                cache[key] = copy.deepcopy(result)
                status = result['allow']
                if event_def['event_id'] not in last_status or last_status[event_def['event_id']] != status or status:
                    admission_rows.append({'event_id': event_def['event_id'], 'decision_tick': tick,
                                           **copy.deepcopy(result)})
                    last_status[event_def['event_id']] = status
            return cache[key]['allow']

    interpreter = AdmittedInterpreter(Path(script_path), episode_id=episode_id)
    handlers, audit, clock = _handlers(engine), [], [0]

    def dispatch(action):
        original = copy.deepcopy(action)
        resolved = original if action_filter is None else action_filter(copy.deepcopy(original), clock[0])
        if resolved is None:
            audit.append({'tick': clock[0], 'action': original,
                          'result': {'status': 'omitted_by_explicit_configuration'}})
            # Interpreter success means configured orchestration proceeded;
            # the audit explicitly contains no physical action execution.
            return {'status': 'ok', 'disposition': 'explicitly_omitted'}
        if resolved['type'] not in handlers:
            raise ValueError('unsupported authored action: ' + resolved['type'])
        result = handlers[resolved['type']](resolved, clock[0])
        record = {'tick': clock[0], 'action': copy.deepcopy(resolved), 'result': copy.deepcopy(result)}
        if resolved['type'] == 'move_entity' and result['status'] == 'ok':
            terminal = max(engine.keyframes[result['entity_id']], key=lambda row: row[0])
            record['motion_schedule'] = {'terminal_tick': terminal[0],
                                         'endpoint_enu_m': copy.deepcopy(terminal[1])}
            if landing_dwell_ticks is not None and resolved.get('post_activity_type') == 'landed':
                # Extend acquisition only after the admitted action supplies
                # an actual schedule; no future planned command changes a prefix.
                engine.duration_ticks = max(engine.duration_ticks,
                                            terminal[0] + landing_dwell_ticks)
        audit.append(record)
        if result['status'] != 'ok':
            raise RuntimeError(result)
        return result

    for action_type in handlers:
        interpreter.register_handler(action_type, dispatch)
    tick = 0
    while tick <= engine.duration_ticks:
        clock[0] = tick
        cache.clear()  # run-local, current-tick evaluations only
        engine._apply_pending_runtime_state_patches(tick)
        for weather in engine.weather_transitions.get(tick, []):
            engine.weather = weather
        current = engine._record_tick_rows(tick)
        for row in current:
            interpreter.update_entity_state(row['entity_id'], row['pos_enu'], {}, row['vel_mps'])
            if row.get('label_class') == 'pedestrian':
                interpreter.update_entity_activity(row['entity_id'], row['activity_type'])
        interpreter.update_weather_state(engine.weather_rows[-1])
        if observe_tick is not None:
            observe_tick(tick, engine, interpreter, current)
        for fired in interpreter.tick(tick):
            if fired['result']['status'] != 'ok':
                raise RuntimeError(fired)
        tick += 1
    return {'engine': engine, 'interpreter': interpreter, 'audit': audit,
            'gate_evidence': admission_rows}
