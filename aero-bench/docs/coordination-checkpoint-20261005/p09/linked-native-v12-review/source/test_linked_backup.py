"""Focused authority/geometry controls; synthetic receipts are not native proof."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from .linked_backup import adopt_backup_contact, arrival_request_authority, validate_backup_request
from .linked_contract import load_contract, STEP_NS, filtered_source_action

ROOT = Path(__file__).resolve().parents[3]


class BackupContactTests(TestCase):
    def test_geometry_migration_is_only_v2_and_retains_speeds_and_original_destinations(self):
        p, _, script = load_contract(ROOT, 'L2-1_v2', 0)
        actions = {a['action_id']: a for e in script['events'] for a in e['actions']}
        loiter, resume = actions['move_uav_backup_loiter'], actions['move_uav_resume_patrol']
        self.assertEqual(loiter['waypoints_enu_m'][-1], [6308., 6134., 30.])
        self.assertEqual(resume['waypoints_enu_m'][0], loiter['waypoints_enu_m'][-1])
        self.assertEqual(resume['waypoints_enu_m'][-1], [6330.975, 6139.542, 30.])
        self.assertEqual([loiter['velocity_mps'], resume['velocity_mps']], [4., 7.])
        p1, _, s1 = load_contract(ROOT, 'L2-1_v1', 0)
        self.assertNotIn('backup_contact_geometry', p1)
        self.assertFalse(any('p09_original_waypoints_enu_m' in a for e in s1['events'] for a in e['actions']))

    def test_arrival_is_owned_sample_after_motion_and_before_request_decision(self):
        p, _, script = load_contract(ROOT, 'L2-1_v2', 0)
        owner, = p['actor_ids']
        target = p['backup_contact_geometry']['contact_enu_m']
        run = {'engine': SimpleNamespace(trajectory_rows=[
            {'entity_id': 'other', 'tick': 301, 'pos_enu': target},
            {'entity_id': owner, 'tick': 300, 'pos_enu': target},
            {'entity_id': owner, 'tick': 301, 'pos_enu': [0., 0., 30.]},
            {'entity_id': owner, 'tick': 400, 'pos_enu': target},
        ])}
        proof = arrival_request_authority(p, script, run, 300 * STEP_NS, STEP_NS)
        self.assertEqual(proof['owner'], owner)
        self.assertEqual(proof['sample_ns'], 400 * STEP_NS)
        self.assertEqual(proof['available_ns'], 401 * STEP_NS)
        self.assertEqual(proof['decision_ns'], 402 * STEP_NS)
        run['engine'].trajectory_rows.pop()
        self.assertIsNone(arrival_request_authority(p, script, run, 300 * STEP_NS, STEP_NS))
        self.assertIsNone(arrival_request_authority(p, script, run, None, STEP_NS))

    def test_backup_response_rejects_wrong_receiver_or_unavailable_sender_evidence(self):
        p, _, _ = load_contract(ROOT, 'L2-1_v2', 0)
        owner, = p['actor_ids']; station = p['backup_station']
        proof = {'type': 'owner-local available endpoint arrival', 'owner': owner,
                 'move_event_ns': 10, 'sample_ns': 20, 'available_ns': 30, 'decision_ns': 40}
        item = {'submission': {'source': owner, 'receiver': station, 'action': 'request_backup_path',
                    'send_ns': 40, 'evidence_ns': 30, 'deadline_ns': 70, 'sender_decision_evidence': proof},
                'accepted': {'source_owner': owner, 'receiver_owner': station, 'accepted_ns': 50}}
        validate_backup_request(item, owner, station)
        validate_backup_request({'accepted': None}, owner, station)
        for mutate in ('receiver', 'availability', 'source'):
            changed = copy.deepcopy(item)
            if mutate == 'receiver': changed['accepted']['receiver_owner'] = 'failed_primary'
            if mutate == 'source': changed['submission']['source'] = 'peer'
            if mutate == 'availability': changed['submission']['sender_decision_evidence']['available_ns'] = 41
            with self.assertRaisesRegex(ValueError, 'exact prior actor-owned'):
                validate_backup_request(changed, owner, station)

    def test_failed_primary_visual_claim_is_omitted_and_scene_drift_is_explicit(self):
        p, scene, script = load_contract(ROOT, 'L2-1_v2', 0)
        action = {'type': 'set_visual_state', 'entity_id': p['primary_station'],
                  'action_id': 'set_tower_backup', 'visual_state': {'mode': 'backup_link'}}
        self.assertIsNone(filtered_source_action(action, 500, profile=p))
        backup = next(e for e in scene['entities'] if e['entity_id'] == p['backup_station'])
        backup['placement']['resolved_position_enu_m'][0] += 1
        with self.assertRaisesRegex(ValueError, 'reviewed authored backup-station position'):
            adopt_backup_contact(script, p, scene)
