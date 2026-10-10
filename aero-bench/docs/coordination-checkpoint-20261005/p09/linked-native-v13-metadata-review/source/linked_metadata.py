"""Versioned route metadata and separation of legacy planning proofs.

This migration does not change movement actions, receipt admission or physics.
The old terminal planning bound is not evidence about a delayed native receipt.
"""
from __future__ import annotations

import copy
import math

VERSION = 'p09.adopted-route-and-terminal-metadata/v1'
LANDING_INPUTS = ('landing_reference_enu_m', 'home_pose_origin_z_m',
                  'touchdown_altitude_tolerance_m', 'touchdown_dwell_ticks', 'max_speed_mps')


def retire_terminal_planning_proofs(script, profile):
    migrated = []
    for event in script['events']:
        for action in event['actions']:
            old = action.get('terminal_feasibility')
            if old is None:
                continue
            if 'dispatch_upper_bound_tick' not in old:
                raise ValueError('Expected the exact legacy planning proof before version migration')
            action['p09_original_terminal_feasibility'] = {
                'source_script': profile['source_script'],
                'source_script_sha256': profile['source_script_sha256'],
                'disposition': 'Historical source planning annotation; excluded from current feasibility conclusions',
                'proof': copy.deepcopy(old)}
            action['terminal_feasibility'] = {
                'model': 'p09.landing-reference-operating-inputs/v1',
                'planning_proof_status': 'NOT_CERTIFIED_FOR_RECEIPT_ADMITTED_DISPATCH',
                **{key: copy.deepcopy(old[key]) for key in LANDING_INPUTS if key in old}}
            migrated.append({'event_id': event['event_id'], 'action_id': action['action_id'],
                             'old_dispatch_upper_bound_tick': old['dispatch_upper_bound_tick']})
    profile['terminal_annotation_migration'] = {'version': VERSION,
        'scope': 'Legacy dispatch/available-time/speed proof is historical; physical landing reference and dwell inputs retained',
        'actions': migrated}


def synchronize_l2_v2_route(script, scene, profile):
    owner, = profile['actor_ids']
    if profile['scenario_id'] != 'L2-1_v2':
        raise ValueError('Route metadata migration is scoped to L2-1_v2')
    entity, = [entity for entity in scene['entities'] if entity['entity_id'] == owner]
    ids = ['move_uav_l2_1_v2_takeoff_entry', 'move_uav_backup_loiter',
           'move_uav_resume_patrol', 'move_uav_l2_1_v2_landing_return']
    actions = {action['action_id']: action for event in script['events'] for action in event['actions']}
    route = []
    for action_id in ids:
        action = actions[action_id]
        if action['type'] != 'move_entity' or action['entity_id'] != owner:
            raise ValueError('Adopted route action has the wrong owner/type')
        points = action['waypoints_enu_m']
        if route and route[-1] != points[0]:
            raise ValueError('Adopted lifecycle movement paths are not contiguous')
        route.extend(copy.deepcopy(points if not route else points[1:]))
    if route[0] != entity['placement']['resolved_position_enu_m'] or len(route) != 9:
        raise ValueError('Adopted L2-1_v2 route must retain its exact home and nine lifecycle points')
    if 'p09_original_route_metadata' in entity:
        raise ValueError('Route metadata is already migrated')
    entity['p09_original_route_metadata'] = {key: copy.deepcopy(entity[key]) for key in
        ('route_waypoints_enu_m', 'planned_route_waypoints_enu_m', 'motion_contract')}
    entity['route_waypoints_enu_m'] = copy.deepcopy(route[1:])
    entity['planned_route_waypoints_enu_m'] = copy.deepcopy(route[1:])
    # Retain the existing source definition: home + scene route + event paths,
    # in script order, rather than substitute a different length convention.
    motion_points = [route[0], *entity['route_waypoints_enu_m']]
    for event in script['events']:
        for action in event['actions']:
            if action.get('entity_id') == owner and 'waypoints_enu_m' in action:
                motion_points.extend(action['waypoints_enu_m'])
    entity['motion_contract']['planned_displacement_m'] = round(
        sum(math.dist(a, b) for a, b in zip(motion_points, motion_points[1:])), 3)
    parameters = script['parameters']
    keys = ('uav_corridor_segment_details', 'uav_corridor_segments', 'uav_corridor_segment_count')
    parameters['p09_original_corridor_metadata'] = {key: copy.deepcopy(parameters[key]) for key in keys}
    old = parameters['uav_corridor_segment_details']
    owner_indices = [i for i, segment in enumerate(old) if segment['entity_id'] == owner]
    if len(owner_indices) != 9 or owner_indices != list(range(owner_indices[0], owner_indices[-1] + 1)):
        raise ValueError('Expected nine consecutive original owner corridor segments')
    segments = [{'entity_id': owner, 'segment_index': i, 'segment_start_enu_m': a,
                 'segment_end_enu_m': b, 'assigned_altitude_m': entity['uav_corridor']['assigned_altitude_m']}
                for i, (a, b) in enumerate(zip(route, route[1:]))]
    parameters['uav_corridor_segment_details'] = old[:owner_indices[0]] + segments + old[owner_indices[-1]+1:]
    parameters['uav_corridor_segment_count'] = len(parameters['uav_corridor_segment_details'])
    owner_summary, = [item for item in parameters['uav_corridor_segments'] if item['entity_id'] == owner]
    owner_summary['point_count'] = len(route)
    for rule in scene['validation_rules']:
        if rule['rule'] == 'uav_corridor_contract':
            rule['p09_original_corridor_segment_count'] = rule['corridor_segment_count']
            rule['corridor_segment_count'] = parameters['uav_corridor_segment_count']
    changed_corridors = []
    for corridor in scene['entities']:
        placement = corridor['placement']
        if placement.get('source_uav_entity_id') != owner:
            continue
        index = int(corridor['entity_id'].rsplit('_', 1)[1])
        a, b = route[index:index+2]
        length = max(1., math.dist(a, b))
        mid = [round((x+y)/2, 3) for x, y in zip(a, b)]
        revised = {**placement, 'center_enu_m': mid, 'resolved_position_enu_m': mid,
            'extent_m': [round(length*.5, 3), 4., 4.],
            'size_m': [round(length, 3), 8., 8.], 'scale_xyz': [round(length, 3), 8., 8.],
            'rotation_deg': {'pitch_deg': 0., 'yaw_deg': round(math.degrees(math.atan2(b[1]-a[1], b[0]-a[0])), 3), 'roll_deg': 0.},
            'segment_start_enu_m': a, 'segment_end_enu_m': b,
            'altitude_layer_m': round((a[2]+b[2])*.5, 3)}
        if revised != placement:
            corridor['p09_original_placement'] = copy.deepcopy(placement)
            corridor['placement'] = revised
            changed_corridors.append(corridor['entity_id'])
    profile['route_metadata_migration'] = {'version': VERSION, 'owner': owner,
        'route_source_action_ids': ids, 'full_route_enu_m': route,
        'changed_corridor_entities': changed_corridors,
        'current_vs_original': 'Enabled scene corridors and route arrays describe adopted actions; old plan is explicit p09_original provenance',
        'native_receipt_replay_required': False,
        'native_receipt_basis': 'No move action changes; corrected logical corridor entities are not ns-3 endpoints',
        'ue_dependency': 'Mission route metadata plus enabled corridor03/04 geometry from tick0; changed scene requires capture-input review'}
