"""Scoped backup-contact geometry and actor-local request authority.

The route is an authored repair for L2-1_v2 only. Local pose samples are
modeled observations of executed motion, not a remote station's knowledge.
"""
from __future__ import annotations

import copy


def adopt_backup_contact(script, profile, scene, *, root):
    owner, = profile['actor_ids']
    profile['backup_request_policy'] = {
        'version': 'p09.backup-actor-available-arrival/v1',
        'owner': owner, 'sample_latency_original_ticks': 1,
        'decision': 'strictly after sample availability',
        'arrival_tolerance_m': 1e-5,
        'pose_source': 'executed owner trajectory, modeled local navigation observation',
        'station_authority': 'actual exact-owner request RX only; no actor arrival/global event knowledge',
    }
    resume = next(e for e in script['events'] if e['event_id'] == 'patrol_resume_via_backup_link')
    resume['log_event'].update(
        title='UAV resumes after a received response from its declared backup endpoint',
        target_ids=[owner, profile['backup_station']])
    profile['corrections'].append({'event_id': resume['event_id'],
        'change': 'failed primary tower does not confirm a receiver-private response; retain event ID and log exact backup endpoint'})
    if profile['scenario_id'] != 'L2-1_v2':
        return
    backup, = [e for e in scene['entities'] if e['entity_id'] == profile['backup_station']]
    if backup['placement']['resolved_position_enu_m'] != [6248.051, 6127.613, 0.0]:
        raise ValueError('L2-1_v2 contact repair requires the reviewed authored backup-station position')
    paths = {
        'move_uav_backup_loiter': (
            [[6341.975, 6137.542, 31.0], [6341.975, 6137.542, 30.0],
             [6351.975, 6147.542, 30.0], [6354.975, 6152.542, 30.0]],
            [[6341.975, 6137.542, 31.0], [6341.975, 6137.542, 30.0], [6308.0, 6134.0, 30.0]]),
        'move_uav_resume_patrol': (
            [[6354.975, 6152.542, 30.0], [6330.975, 6139.542, 30.0]],
            [[6308.0, 6134.0, 30.0], [6330.975, 6139.542, 30.0]]),
    }
    profile['backup_contact_geometry'] = {
        'version': 'p09.l2-1-v2.backup-contact-route/v2-metadata',
        'scope': 'L2-1_v2 only; original published script/scene unchanged',
        'backup_station': profile['backup_station'],
        'backup_antenna_enu_m': [6248.051, 6127.613, 3.0],
        'contact_enu_m': [6308.0, 6134.0, 30.0],
        'basis': 'unchanged-route native ARP cache drops and backup PREAMBLE_DETECT_FAILURE; no RF threshold/power change',
        'rf_scope': 'same R1 ns-3 PHY/MAC; contact feasibility is measured by native request/response RX, not by a calculated range alone',
    }
    for action_id, (expected, revised) in paths.items():
        actions = [a for e in script['events'] for a in e['actions']
                   if a.get('action_id') == action_id]
        if len(actions) != 1 or actions[0]['entity_id'] != owner or actions[0]['waypoints_enu_m'] != expected:
            raise ValueError('L2-1_v2 contact repair requires the exact reviewed original owner/route')
        action = actions[0]
        action['p09_original_corridor_validation'] = action.pop('uav_corridor_validation')
        action['p09_original_waypoints_enu_m'] = copy.deepcopy(expected)
        action['waypoints_enu_m'] = copy.deepcopy(revised)
    from .linked_route_geometry import check_contact_geometry
    check = check_contact_geometry(root, {key: revised for key, (_, revised) in paths.items()})
    profile['backup_contact_geometry']['spatial_check'] = check
    for event in script['events']:
        for action in event['actions']:
            if action['action_id'] not in paths:
                continue
            rows = [row for row in check['segments'] if row['action_id'] == action['action_id']]
            action['uav_corridor_validation'] = {
                'status': 'source_geometry_checked_p09_authored_contact_repair',
                'assigned_altitude_m': 30.0,
                'profile_version': profile['backup_contact_geometry']['version'],
                'checker': check['checker'], 'checked_segments': copy.deepcopy(rows),
                'air_segments_clear': all(row['clear'] for row in rows)}
    from .linked_metadata import synchronize_l2_v2_route
    synchronize_l2_v2_route(script, scene, profile)
    profile['corrections'].append({'profile': profile['backup_contact_geometry']['version'],
        'change': 'move the backup contact endpoint toward the already authored backup station; retain velocities, patrol destination and landing route'})


def arrival_request_authority(profile, script, run, move_event_ns, step_ns):
    """One sender-owned actual arrival sample; no planned/future evidence."""
    if move_event_ns is None:
        return None
    owner, = profile['actor_ids']
    move, = [a for e in script['events'] if e['event_id'] == 'uav_link_response'
             for a in e['actions'] if a['type'] == 'move_entity' and a['entity_id'] == owner]
    target = move['waypoints_enu_m'][-1]
    policy = profile['backup_request_policy']
    for row in run['engine'].trajectory_rows:
        if (row['entity_id'] != owner or row['tick'] * step_ns <= move_event_ns
            or not all(abs(x-y) < policy['arrival_tolerance_m'] for x,y in zip(row['pos_enu'], target))):
            continue
        available_ns = (row['tick'] + policy['sample_latency_original_ticks']) * step_ns
        return {'type': 'owner-local available endpoint arrival', 'owner': owner,
            'event_id': 'uav_link_response', 'move_event_ns': move_event_ns,
            'sample_ns': row['tick'] * step_ns, 'available_ns': available_ns,
            'decision_ns': available_ns + step_ns, 'position_enu_m': copy.deepcopy(row['pos_enu']),
            'authored_target_enu_m': copy.deepcopy(target),
            'policy_version': policy['version'], 'pose_source': policy['pose_source']}
    return None


def validate_backup_request(item, actor, station):
    if item is None or item['accepted'] is None:
        return
    receipt, submission = item['accepted'], item['submission']
    proof = submission['sender_decision_evidence']
    if (submission['source'] != actor or submission['receiver'] != station
        or submission['action'] != 'request_backup_path'
        or receipt['source_owner'] != actor or receipt['receiver_owner'] != station
        or not isinstance(proof, dict) or proof.get('owner') != actor
        or proof.get('type') != 'owner-local available endpoint arrival'
        or not proof['move_event_ns'] < proof['sample_ns'] < proof['available_ns'] < proof['decision_ns']
        or proof['decision_ns'] != submission['send_ns']
        or proof['available_ns'] != submission['evidence_ns']
        or not submission['send_ns'] <= receipt['accepted_ns'] < submission['deadline_ns']):
        raise ValueError('backup response requires exact prior actor-owned arrival request and matching station RX')
