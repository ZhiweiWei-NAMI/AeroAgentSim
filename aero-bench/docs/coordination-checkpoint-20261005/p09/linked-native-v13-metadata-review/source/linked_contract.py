"""Versioned, explicit source bindings for the remaining P09 communication groups.

This module changes a private copy of each authored script. Published source
episodes and scripts are never edited. Profile values below are simulation
assumptions; they are not hardware observations or RF calibration.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

VERSION = 'p09.linked-source-adoption/v13-metadata'
STEP_NS = 100_000_000
GROUPS = {
    'L6-1_v1': 'local_c2', 'L6-1_v2': 'local_c2',
    'L2-1_v1': 'backup_station', 'L2-1_v2': 'backup_station',
    'L5-1_v1': 'local_weather', 'L5-1_v2': 'local_weather',
    'X1_rain_to_c2loss_to_forced_landing': 'rain_service_descent',
    'L6-4_v1': 'cochannel_backup', 'L6-4_v2': 'cochannel_backup',
    'X5_comm_failure_to_pad_contention': 'pad_service',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_contract(root, scenario_id, seed):
    if scenario_id not in GROUPS or type(seed) is not int or seed not in (0, 1, 2):
        raise ValueError('undeclared P09 source group/seed')
    root = Path(root)
    reviewed = json.loads((root / 'design/p09/source_group_plan/communication_bindings_review.json').read_text())
    group, = [g for g in reviewed['groups'] if g['scenario_id'] == scenario_id]
    script_path = root / group['source_event_script']
    scene_path = root / group['source_scene_setup']
    script = json.loads(script_path.read_text())
    scene = json.loads(scene_path.read_text())
    transport = group['transport_roles']
    actors = transport['actor_receivers_if_commands_are_bound']
    stations = transport['event_referenced_radio_towers']
    backup = transport['other_declared_radio_towers']
    mechanism = GROUPS[scenario_id]
    if mechanism != 'local_weather' and len(stations) != 1:
        raise ValueError('one exact authored primary station required')
    if mechanism == 'backup_station' and len(backup) != 1:
        raise ValueError('one exact authored backup station required')
    script = copy.deepcopy(script)
    corrections = []
    for trigger in script['triggers']:
        if (mechanism == 'local_weather' and trigger['type'] == 'weather_state'
                and trigger.get('sustain_ticks') == 5):
            trigger.pop('sustain_ticks')
            trigger['min_true_ticks'] = 5
            corrections.append({'trigger_id': trigger['trigger_id'],
                                'change': 'five consecutive original ticks, not post-false sustain'})
            event = next(e for e in script['events'] if e['trigger_ref'] == trigger['trigger_id'])
            if event.get('causal_temporal_policy') == 'same_tick_required':
                event['causal_temporal_policy'] = 'available_consecutive_guard_required'
                corrections.append({'event_id': event['event_id'],
                    'change': 'explicit L5 temporal-policy migration: threshold follows available consecutive samples'})
    if mechanism == 'backup_station':
        corrections.append({'profile': 'backup_discovery',
            'change': 'activate backup discovery at locally observed arrival at authored backup-loiter endpoint; avoid unsupported pre-contact ARP resolution'})
        # A fault-induced local hold is explicit, distinct from a planned hold.
        owner, = actors
        trigger_id = 'p09_actor_observed_station_loss'
        script['triggers'].append({'trigger_id': trigger_id, 'type': 'tick', 'tick': 260})
        script['events'].append({'event_id': trigger_id, 'trigger_ref': trigger_id,
                                'max_fire_count': 1, 'actions': [
                                    {'action_id': trigger_id + ':hold', 'type': 'set_visual_state',
                                     'entity_id': owner, 'visual_state': {'mode': 'hover'}},
                                    {'action_id': trigger_id + ':state', 'type': 'set_runtime_state',
                                     'entity_id': owner, 'delay_ticks': 1,
                                     'state_patch': {'control_state': {'safe_hold_active': True}}}],
                                'intent': 'receiver_observed_station_loss',
                                'intent_stage': '01.receiver_observed_station_loss',
                                'causal_chain_id': scenario_id + '.semantic_event_chain',
                                'causal_predecessor_intent': 'digital_anomaly',
                                'causal_predecessor_event_ids': ['station_degraded'],
                                'target_roles': ['mission_uav'],
                                'log_event': {'topic': trigger_id,
                                    'title': 'Actor watchdog observes missing station heartbeats',
                                    'target_ids': [owner], 'intent': 'receiver_observed_station_loss'}})
        event = next(e for e in script['events'] if e['event_id'] == 'uav_link_response')
        trigger = next(t for t in script['triggers'] if t['trigger_id'] == event['trigger_ref'])
        trigger['event_id'] = trigger_id
        event['causal_predecessor_event_ids'] = [trigger_id]
        event['causal_predecessor_intent'] = 'receiver_observed_station_loss'
        event['log_event']['causal_predecessor_intent'] = 'receiver_observed_station_loss'
        corrections.append({'trigger_id': trigger['trigger_id'],
                            'change': 'response delay starts at actor-observed loss, not station global truth'})
    identity = f'{scenario_id}__seed{seed:02d}'
    profile = {
        'schema_version': VERSION, 'episode_id': identity, 'scenario_id': scenario_id,
        'seed': seed, 'ns3_seed': seed + 1, 'ns3_run': 1, 'mechanism': mechanism,
        'actor_ids': actors, 'primary_station': None if not stations else stations[0],
        'backup_station': backup[0] if mechanism == 'backup_station' else None,
        'heartbeat': {'period_ns': STEP_NS, 'payload_bytes': 256, 'ttl_ns': 200_000_000,
                      'window_ns': 1_000_000_000, 'loss_limit': 0.05, 'consecutive_limit': 3},
        'receiver_state_namespace': 'p09.actor-heartbeat-watchdog/v1',
        'receiver_state_definitions': {
            'loss_ratio': 'unit1; mature expected local slots not received before strict generation+TTL; includes unsent expected slots',
            'consecutive_missing': 'packet; trailing mature missing sequence slots',
            'degraded': 'bool/UNKNOWN; loss_ratio>0.05 AND consecutive_missing>=3',
            'observation_ns': 'ns; local window close/available time, consumed strictly before actor decision'},
        'legacy_state_disposition': 'old global accepted-TX/reference and initial runtime flags are separate; no actor-control causality claim from those flags',
        'command': {'payload_bytes': 64, 'attempts': 3, 'period_ns': STEP_NS,
                    'deadline_ns': 2_000_000_000, 'policy_delay_ns': 0},
        'application_message_policy': 'modeled report payload frozen at submission and bound to exact native flow/packet RX through immutable ledger; not hardware ACK or PX4 receipt',
        'source_script': group['source_event_script'], 'source_scene': group['source_scene_setup'],
        'source_script_sha256': digest(script_path), 'source_scene_sha256': digest(scene_path),
        'corrections': corrections,
        'assumptions': ['receiver-visible periodic heartbeat is configured application traffic',
                        'actor observes local weather/nearby geometry after one original tick',
                        '10Hz authored waypoint engine, not PX4 aerodynamic flight'],
        'rain_rf': 'not amplified; dimensionless rain is not an observed mm/h rate',
        'rf_profile': 'R1:2412MHz/20MHz/HtMcs0/16dBm/NF7/n3/L0=40.09532929124565',
    }
    if mechanism == 'local_weather':
        profile['weather_owner_ids'] = sorted({a['entity_id'] for e in script['events']
                                               for a in e.get('actions', [])
                                               if a['type'] == 'move_entity'})
        profile['rain_guard'] = {'schema_version': 'p09.l5-1.rain-consecutive-original-ticks/v1',
                                 'sample_period_ns': STEP_NS, 'consecutive_samples': 5,
                                 'threshold': 0.5, 'duration_semantics':
                                 'five consecutive samples, spanning four sample intervals; no post-false hold'}
    if mechanism == 'backup_station':
        from .linked_backup import adopt_backup_contact
        adopt_backup_contact(script, profile, scene, root=root)
        profile['backup_discovery_policy'] = 'actor reaches its versioned backup-contact endpoint, samples its own actual arrival, observes it after one tick, then begins backup request strictly after availability'
        profile['landing_authority'] = 'preloaded onboard mission termination after receipt-admitted patrol resumption; no new failed-path landing command assumed'
        event = next(e for e in script['events'] if e['event_id'] == 'patrol_resume_via_backup_link')
        event['actions'].append({'type': 'set_runtime_state', 'action_id': 'p09_received_backup_path:active',
            'entity_id': profile['actor_ids'][0], 'delay_ticks': 1,
            'state_patch': {'communication_state': {'alternate_link_active': True}}})
    if mechanism == 'cochannel_backup':
        script['description'] = 'Cochannel UDP competition affects two UAV heartbeat flows; owner-local hold and receipt-bound backup response'
        scene['description'] = script['description']
        original_required = 'jamming > safe hold > channel' if scenario_id == 'L6-4_v1' else 'jamming variant > land'
        required = 'cochannel UDP competition > owner-local safe hold > received backup response > landing'
        semantic = script['parameters']['semantic_event_contract']
        rules = [r['contract'] for r in scene['validation_rules']
                 if 'contract' in r and r['contract'].get('required_event') == original_required]
        if semantic['required_event'] != original_required or len(rules) != 1:
            raise ValueError('cochannel narrative metadata differs from the scoped source contract')
        semantic['required_event'] = required
        rules[0]['required_event'] = required
        corrections.append({'change': 'script and scene descriptions/required_event describe actual cochannel UDP mechanism',
            'retained_identifiers': 'legacy wideband_jamming event/trigger/action/topic IDs preserve source references; they do not certify wideband jamming'})
        profile['hold_decision_queue'] = {'version': 'p09.cochannel-local-hold-queue/v1',
            'decision_grid_ns': STEP_NS, 'action_grid_original_ticks': 5,
            'semantics': 'retain each owner first available local degradation decision independently until next permitted authored action boundary; no peer watchdog access; preserve operands/time; run-local and single-fire'}
        corrections.append({'event_id': 'multi_uav_hold_entry',
            'change': 'queue original-tick actor hold decision to next authored action boundary instead of silently losing a short observed pulse'})
        profile['motion_acquisition'] = {'version': 'p09.complete-admitted-landings/v1',
            'nominal_duration_ticks': 900, 'landing_dwell_ticks': 5,
            'extension_basis': 'actual admitted landing motion terminal tick plus five original ticks; original speeds/routes unchanged'}
        profile.update(interference_offered_bps=8_000_000, backup_channel=6,
                       interference_class='legitimate cochannel UDP competition, not wideband jamming',
                       channel_switch_authority='explicit absolute rendezvous installed before fault; actor response commands still require received reports')
        event = next(e for e in script['events'] if e['event_id'] == 'wideband_jamming')
        event['log_event']['title'] = 'Declared cochannel UDP workload competes with two UAV heartbeat flows'
        for e in script['events']:
            if e['event_id'] in ('multi_uav_hold_entry', 'multi_uav_safe_hold'):
                e['log_event']['title'] = 'Two UAVs hold under observed cochannel heartbeat degradation' if e['event_id'] == 'multi_uav_hold_entry' else 'Local safe-hold policy retains two UAV control delays'
        corrections.append({'event_id': event['event_id'],
                            'change': 'legacy IDs retained; simulated cause and hold titles declare cochannel competition, not certified wideband jamming'})
    if mechanism == 'pad_service':
        profile['pad_request_transport'] = {**profile['command'], 'deadline_ns': 5_000_000_000}
        profile['pad_request_policy'] = {'version': 'p09.pad-cold-discovery/v1',
            'deadline_basis': 'ns-3 ARP WaitReplyTimeout=1s, MaxRetries=3 permits cold discovery retries; 5s authored service-request envelope, distinct from 2s flight-command deadline',
            'request_count_semantics': 'accepted active pad requests, recorded only after actual RX; rejected request retires at local service completion, not remote actor truth'}
        profile.update(pad_owner='pad_x5_primary', priority_order=['uav_x5_priority', 'uav_x5_second'],
                       service_delay_ns=5 * STEP_NS,
                       service_record_policy='accepted requests and allocation results persist until station_recovered revocation; transport deadline bounds delivery, not durable fact use',
                       pad_transport_assumption='explicit local pad Wi-Fi endpoint; independent of failed tower')
    if mechanism == 'rain_service_descent':
        profile['rain_service_assumption'] = 'authored wet-weather station service failure, not RF rain attenuation'
        event = next(e for e in script['events'] if e['event_id'] == 'rain_threshold')
        event['causal_temporal_policy'] = 'available_local_weather_required'
        event['log_event']['title'] = 'Available local rain observation triggers authored wet-weather station-service stress'
        corrections.append({'event_id': event['event_id'],
                            'change': 'local weather availability and service-failure assumption; original sustain_ticks retained'})
    from .linked_adoption import split_weather_responses, split_cochannel_owners, split_pad_requests
    if mechanism == 'local_weather':
        split_weather_responses(script, profile)
    if mechanism == 'cochannel_backup':
        split_cochannel_owners(script, profile, STEP_NS)
    if mechanism == 'pad_service':
        split_pad_requests(script, profile)
    from .linked_metadata import retire_terminal_planning_proofs
    retire_terminal_planning_proofs(script, profile)
    return profile, scene, script


def filtered_source_action(action, tick, *, profile):
    """Remove global-truth actor claims; retain moves, timings and local evidence.

    Omissions are logged by the execution adapter. They do not certify an
    unavailable command, alternate channel, or wideband attack.
    """
    action = copy.deepcopy(action)
    if (profile['mechanism'] == 'backup_station'
            and action['entity_id'] == profile['primary_station']
            and action['type'] == 'set_visual_state'
            and action['visual_state'].get('mode') == 'backup_link'):
        return None  # Failed primary has no report of the UAV-private backup RX.
    if (profile['mechanism'] == 'pad_service'
            and action['action_id'] == 'set_x5_uav_b_hold'):
        return None  # A pad-local decision cannot move an actor before result RX.
    if action['type'] != 'set_runtime_state':
        return action
    patch = action['state_patch']
    if profile['mechanism'] == 'pad_service' and action['entity_id'] == profile['pad_owner']:
        facility = patch.get('facility_state', {})
        for field in ('request_count', 'requester_ids'):
            facility.pop(field, None)
        if not facility:
            patch.pop('facility_state', None)
    if action['entity_id'] in profile['actor_ids']:
        communication = patch.get('communication_state', {})
        for field in ('communication_unavailable', 'station_unavailable'):
            communication.pop(field, None)
        if (profile['mechanism'] == 'backup_station'
                and not action['action_id'].startswith('p09_received_backup_path:')):
            communication.pop('alternate_link_active', None)
        if not communication:
            patch.pop('communication_state', None)
        if (profile['mechanism'] == 'backup_station' and tick == 260
                and not action['action_id'].startswith('p09_actor_observed_station_loss:')):
            patch.pop('control_state', None)
        security = patch.get('security_state', {})
        security.pop('jamming_active', None)
        if not security:
            patch.pop('security_state', None)
    if profile['mechanism'] == 'backup_station' and action['entity_id'] == profile['primary_station']:
        communication = patch.get('communication_state', {})
        if communication.get('station_unavailable') is False:
            communication.pop('station_unavailable')
        communication.pop('backup_link_active', None)
        if not communication:
            patch.pop('communication_state', None)
    return action if patch else None
