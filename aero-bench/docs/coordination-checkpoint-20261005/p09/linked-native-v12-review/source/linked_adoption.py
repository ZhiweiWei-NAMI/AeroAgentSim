"""Explicit instance migrations; original authored scripts remain untouched.

Narrative aggregates have no actor actions. Local events retain exact owner,
weather operands or receipt authority. Absolute rendezvous is a preloaded plan,
not inferred from private actor observations.
"""
from __future__ import annotations

import copy


def after(script, event_id, predecessor, delay=0):
    trigger_id = 'p09.trigger:' + event_id
    script['triggers'].append({'trigger_id': trigger_id, 'type': 'event_fired_after',
                              'event_id': predecessor, 'delay_ticks': delay})
    return trigger_id


def local_copy(source, owner, phase, actions, trigger_ref):
    event = copy.deepcopy(source)
    event_id = source['event_id'] + ':local:' + owner
    event.update(event_id=event_id, trigger_ref=trigger_ref,
                 actions=copy.deepcopy(actions), p09_owner_id=owner, p09_phase=phase)
    event.pop('require_conditions', None)
    event['causal_temporal_policy'] = 'actor_available_evidence_required'
    event.pop('causal_predecessor_event_ids', None)
    if 'log_event' in event:
        event['log_event'].update(topic=event_id, target_ids=[owner])
    return event


def aggregate(source, children):
    source['actions'] = []
    source['p09_local_children'] = list(children)
    source['p09_authority'] = 'narrative aggregate of completed owner events; no physical dispatch'


def split_weather_responses(script, profile):
    additions = []
    for source in list(script['events']):
        setters = [a for a in source['actions'] if a['type'] == 'set_weather']
        actors = sorted({a['entity_id'] for a in source['actions'] if a['type'] != 'set_weather'})
        if not setters or not actors:
            continue
        if len(setters) != 1 or not setters[0].get('overrides'):
            raise ValueError('weather response requires one explicit weather override')
        update = copy.deepcopy(source)
        update_id = 'p09.weather-update:' + source['event_id']
        update.update(event_id=update_id, actions=setters,
                      p09_authority='authored exogenous environmental update')
        update['log_event'] = {'topic': update_id, 'title': 'Authored environmental update',
                               'target_ids': [], 'intent': 'weather_update'}
        additions.append(update)
        children = []
        expected = copy.deepcopy(setters[0]['overrides'])
        for owner in actors:
            event_id = source['event_id'] + ':local:' + owner
            actions = [a for a in source['actions'] if a.get('entity_id') == owner]
            local = local_copy(source, owner, 'weather_response', actions,
                               after(script, event_id, update_id))
            local.update(p09_weather_expected=expected, p09_weather_update_event=update_id)
            additions.append(local)
            children.append(event_id)
        source['trigger_ref'] = after(script, source['event_id'], update_id)
        aggregate(source, children)
        profile['corrections'].append({'event_id': source['event_id'],
            'change': 'separate environmental update, owner-available weather response, and action-free narrative aggregate',
            'weather_update_event': update_id, 'owner_response_events': children,
            'expected_weather_operands': expected})
    script['events'].extend(additions)
    profile['weather_response_version'] = 'p09.actor-available-weather/v1'


def authored_nominal_ticks(script):
    """Declared tick/after plan on its original five-tick execution grid."""
    triggers = {t['trigger_id']: t for t in script['triggers']}
    pending = {e['event_id']: triggers[e['trigger_ref']] for e in script['events']}
    times = {}
    while pending:
        advanced = False
        for event_id, trigger in list(pending.items()):
            if trigger['type'] == 'tick':
                due = trigger['tick']
            elif trigger['type'] == 'event_fired_after' and trigger['event_id'] in times:
                due = times[trigger['event_id']] + trigger['delay_ticks']
            else:
                continue
            times[event_id] = ((due + 4) // 5) * 5
            del pending[event_id]
            advanced = True
        if not advanced:
            raise ValueError('absolute rendezvous requires a complete authored tick/after plan')
    return times


def split_cochannel_owners(script, profile, step_ns):
    originals = {e['event_id']: e for e in script['events']}
    triggers = {t['trigger_id']: t for t in script['triggers']}
    nominal = authored_nominal_ticks(script)
    rendezvous_tick = nominal['alternate_channel']
    profile['rendezvous'] = {'version': 'p09.preloaded-absolute-rendezvous/v1',
        'tick': rendezvous_tick, 'time_ns': rendezvous_tick * step_ns,
        'owners': [profile['primary_station'], *profile['actor_ids']],
        'authority': 'absolute original authored nominal plan installed before fault; independent of actor watchdogs/RX',
        'channel_number': profile['backup_channel']}
    additions = []
    for phase in ('multi_uav_hold_entry', 'multi_uav_safe_hold', 'alternate_channel'):
        source = originals[phase]
        source_trigger = triggers[source['trigger_ref']]
        children = []
        for owner in profile['actor_ids']:
            actions = [a for a in source['actions'] if a.get('entity_id') == owner]
            event_id = phase + ':local:' + owner
            if phase == 'multi_uav_hold_entry':
                trigger_ref = source['trigger_ref']
            else:
                predecessor = source_trigger['event_id'] + ':local:' + owner
                trigger_ref = after(script, event_id, predecessor, source_trigger['delay_ticks'])
            local = local_copy(source, owner, phase, actions, trigger_ref)
            local['log_event']['title'] = {
                'multi_uav_hold_entry': 'Owner enters hold after its available heartbeat degradation decision',
                'multi_uav_safe_hold': 'Owner retains its preloaded safe-hold policy',
                'alternate_channel': 'Owner accepts its received backup-path response command',
            }[phase] + ': ' + owner
            additions.append(local)
            children.append(event_id)
        if phase == 'alternate_channel':
            station_actions = [a for a in source['actions']
                               if a.get('entity_id') == profile['primary_station']]
            trigger_id = 'p09.trigger:backup-rendezvous'
            script['triggers'].append({'trigger_id': trigger_id, 'type': 'tick', 'tick': rendezvous_tick})
            additions.append({'event_id': 'p09.backup-rendezvous', 'trigger_ref': trigger_id,
                'max_fire_count': 1, 'actions': copy.deepcopy(station_actions),
                'p09_authority': profile['rendezvous']['authority']})
        aggregate(source, children)
    for owner in profile['actor_ids']:
        landing = originals['lifecycle_landing_' + owner]
        trigger = triggers[landing['trigger_ref']]
        landing['trigger_ref'] = after(script, landing['event_id'],
            'alternate_channel:local:' + owner, trigger['delay_ticks'])
        landing['causal_predecessor_event_ids'] = ['alternate_channel:local:' + owner]
    script['events'].extend(additions)
    profile['owner_control_version'] = 'p09.independent-owner-receipts/v1'
    profile['pending_successor_wait_ticks'] = profile['command']['deadline_ns'] // step_ns + 5
    profile['corrections'].append({'profile': profile['owner_control_version'],
        'change': 'owner-local hold/safe-hold/RX; action-free aggregates; preloaded absolute radio rendezvous; station commands require actual status report and execution ACK'})


def split_pad_requests(script, profile):
    source = next(e for e in script['events'] if e['event_id'] == 'dual_uav_pad_contention')
    additions = []
    children = []
    for owner in profile['actor_ids']:
        actions = [a for a in source['actions'] if a.get('entity_id') == owner]
        move, = [a for a in actions if a['type'] == 'move_entity']
        approach = local_copy(source, owner, 'pad_approach', actions, source['trigger_ref'])
        approach['log_event']['title'] = 'Owner approaches its declared pad endpoint after local degradation: ' + owner
        additions.append(approach)
        children.append(approach['event_id'])
        event_id = 'p09.pad-request-intent:local:' + owner
        event = local_copy(source, owner, 'pad_request_intent', [],
                           after(script, event_id, approach['event_id']))
        event['event_id'] = event_id
        event['log_event']['topic'] = event_id
        event['log_event']['title'] = 'Owner requests pad service after its available arrival observation: ' + owner
        event['p09_arrival_target_enu_m'] = copy.deepcopy(move['waypoints_enu_m'][-1])
        event['p09_approach_event'] = approach['event_id']
        additions.append(event)
    aggregate(source, children)
    script['events'].extend(additions)
    profile['pad_owner_policy'] = {'version': 'p09.pad-request-owner-admission/v2',
        'approach': 'original per-owner route after original timer and owner-local watchdog; queue decision to original five-tick action slot',
        'request': 'owner-local endpoint pose sampled after admitted approach, available one original tick later and strictly before decision',
        'arrival_tolerance_m': 1e-5,
        'arbitration': 'pad-local accepted requests AND original available 3D proximity guard; no physical custody claim'}
    profile['corrections'].append({'profile': profile['pad_owner_policy']['version'],
        'change': 'owner-local approach precedes own arrival/request; action-free contention aggregate cannot block approach on an unreceived request'})
    recovery = next(e for e in script['events'] if e['event_id'] == 'station_recovered')
    # Explicit new authored operating policy, not a time inferred at runtime
    # from an actor-private reroute. The original seed00 source replay repairs
    # at tick655; this adopted policy fixes that absolute time before all seeds.
    profile['repair_schedule'] = {'version': 'p09.x5.preloaded-repair-and-service-expiry/v1',
        'tick': 655, 'time_ns': 65_500_000_000,
        'owners': [profile['primary_station'], profile['pad_owner']],
        'authority': 'explicit absolute station repair and pad lease-expiry plan installed before fault',
        'basis': 'original same-source seed00 replay station_recovered tick655; fixed authored policy, not observed repair measurement',
        'actor_recovery': 'requires each actor own available healthy heartbeat observation'}
    trigger_id = 'p09.trigger:x5-authored-repair'
    script['triggers'].append({'trigger_id': trigger_id, 'type': 'tick', 'tick': 655})
    recovery['trigger_ref'] = trigger_id
    recovery['p09_authority'] = profile['repair_schedule']['authority']
    recovery['causal_temporal_policy'] = 'explicit_authored_exogenous_schedule'
    recovery.pop('causal_predecessor_event_ids', None)
    recovery.pop('causal_predecessor_intent', None)
    recovery['log_event'].pop('causal_predecessor_intent', None)
    actor_actions = []
    for owner in profile['actor_ids']:
        actions = [a for a in recovery['actions'] if a.get('entity_id') == owner]
        event_id = 'station_recovered:local:' + owner
        event = local_copy(recovery, owner, 'pad_local_recovery', actions,
                           after(script, event_id, recovery['event_id']))
        event['p09_authority'] = 'owner-local available healthy heartbeat observation'
        event['log_event']['title'] = 'Owner observes healthy heartbeat after station repair: ' + owner
        actor_actions.extend(actions)
        script['events'].append(event)
    recovery['actions'] = [a for a in recovery['actions'] if a not in actor_actions]
    profile['service_record_policy'] = 'accepted requests/results persist until preloaded absolute pad lease-expiry tick655; expiry is known locally before fault'
    profile['pad_service_lifetime'] = {
        'version': 'p09.x5.pad-service-half-open-lifetime/v1',
        'valid_until_tick': 655, 'valid_until_ns': profile['repair_schedule']['time_ns'],
        'scope': 'pad requests and allocation results',
        'semantics': 'accepted facts usable only strictly before expiry; original tick is 0.1s',
        'authority': profile['repair_schedule']['authority']}
    reroute = next(e for e in script['events'] if e['event_id'] == 'second_uav_reroute')
    original_trigger = next(t for t in script['triggers'] if t['trigger_id'] == reroute['trigger_ref'])
    if (original_trigger['type'] != 'event_fired_after'
            or original_trigger['event_id'] != 'pad_priority_arbitration'
            or original_trigger['delay_ticks'] != 30):
        raise ValueError('X5 source reroute timer differs from the scoped 30-original-tick contract')
    profile['pad_result_timer'] = {
        'version': 'p09.x5.received-arbitration-time-reroute/v1',
        'delay_original_ticks': original_trigger['delay_ticks'],
        'original_trigger': copy.deepcopy(original_trigger),
        'clock': 'absolute simulation ns from trusted pad result submission bound to actual native RX',
        'admission': 'receiver must have exact unexpired result before action; timer due from received arbitration_ns plus original delay',
        'migration': 'retain arbitration-relative 30-tick narrative delay; remove receiver global event-table access; late/unreceived/expired result never admits reroute'}
    trigger_id = 'p09.trigger:received-pad-result-reroute'
    script['triggers'].append({'trigger_id': trigger_id, 'type': 'tick', 'tick': 0})
    reroute.update(trigger_ref=trigger_id, p09_owner_id=profile['actor_ids'][1],
                   p09_phase='pad_received_result_timer',
                   causal_temporal_policy='received_pad_result_and_original_delay_required')
    reroute.pop('causal_predecessor_event_ids', None)
    reroute.pop('causal_predecessor_intent', None)
    reroute['log_event'].pop('causal_predecessor_intent', None)
    reroute['log_event']['title'] = 'Second UAV reroutes after its received pad result timer'
    profile['corrections'].append({'event_id': reroute['event_id'],
        'change': 'bind original arbitration-relative timer to received allocation payload, with exact half-open service lifetime',
        'timer_policy': profile['pad_result_timer'], 'lifetime_policy': profile['pad_service_lifetime']})
    profile['corrections'].append({'event_id': 'station_recovered',
        'change': 'replace unreceived actor-private reroute timer with explicit preloaded absolute repair/expiry policy; actor flags recover only on own healthy heartbeat',
        'profile': profile['repair_schedule']})
