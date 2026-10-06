"""Small exact-owner, current-barrier and durable-service regressions."""
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
from .linked_replay import Admission
from .linked_contract import STEP_NS, filtered_source_action, load_contract
from .linked_engine import execute_linked


class ReceiptAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.profile = {'command': {'policy_delay_ns': 0},
                        'service_record_policy': 'persist until explicit pad lease expiry',
                        'pad_service_lifetime': {'valid_until_ns': 655 * STEP_NS}}
        self.episode = {'nodes': [{'owner': 'u', 'life_epoch': 0, 'birth_ns': 0, 'death_ns': None}]}
        self.receipt = {'status': 'accepted', 'command_id': 'c', 'receiver_owner': 'u',
            'receiver_epoch': 0, 'action': 'land', 'receive_ns': STEP_NS,
            'accepted_ns': STEP_NS, 'deadline_ns': 5 * STEP_NS}

    def admission(self):
        return Admission(self.profile, {}, {'c': {'accepted': self.receipt}}, self.episode)

    def test_exact_epoch_and_strict_received_prefix(self):
        self.assertFalse(self.admission().command('c', 'u', 1)['allow'])
        self.assertTrue(self.admission().command('c', 'u', 2)['allow'])
        self.receipt['receiver_epoch'] = 99
        self.assertFalse(self.admission().command('c', 'u', 2)['allow'])

    def test_actual_executor_slot_and_expiry(self):
        r = self.admission().command('c', 'u', 4)
        self.assertEqual(r['evidence']['controller_decisions'][0]['execution_decision_ns'], 4 * STEP_NS)
        self.assertFalse(self.admission().command('c', 'u', 5)['allow'])
        self.assertFalse(self.admission().command('c', 'u', 6)['allow'])

    def test_service_fact_delivery_deadline_is_not_use_expiry(self):
        self.assertTrue(self.admission().received_fact('c', 'u', 6)['allow'])
        self.receipt['receiver_epoch'] = 1
        self.assertFalse(self.admission().received_fact('c', 'u', 6)['allow'])

    def test_pad_request_state_requires_exact_available_rx(self):
        profile = {'mechanism': 'pad_service', 'actor_ids': ['u', 'v'],
            'pad_owner': 'pad', 'primary_station': 'tower',
            'service_record_policy': 'persist until revocation',
            'pad_service_lifetime': {'valid_until_ns': 655 * STEP_NS},
            'service_delay_ns': 5*STEP_NS, 'priority_order': ['u', 'v']}
        episode = {'nodes': [{'owner': 'pad', 'life_epoch': 0, 'birth_ns': 0, 'death_ns': None}]}
        receipt = {**self.receipt, 'receiver_owner': 'pad'}
        admission = Admission(profile, {}, {'pad-request:u': {'accepted': receipt}}, episode)
        engine = SimpleNamespace(weather_rows=[], _handle_set_runtime_state=Mock())
        interpreter = SimpleNamespace(entity_states={},
            event_states={k: SimpleNamespace(fired=False) for k in
                ('pad_priority_arbitration', 'station_recovered')})
        admission.observe(1, engine, interpreter, [])
        self.assertEqual(engine._handle_set_runtime_state.call_args.args[0]['state_patch']
            ['facility_state'], {'request_count': 0, 'requester_ids': []})
        admission.observe(2, engine, interpreter, [])
        self.assertEqual(engine._handle_set_runtime_state.call_args.args[0]['state_patch']
            ['facility_state'], {'request_count': 1, 'requester_ids': ['u']})
        self.assertEqual(engine._handle_set_runtime_state.call_args.args[1], 2)

    def test_renderer_and_script_request_claims_cannot_replace_rx(self):
        action = {'type': 'set_runtime_state', 'action_id': 'source_contention',
            'entity_id': 'pad', 'state_patch': {'facility_state':
                {'request_count': 2, 'requester_ids': ['u', 'v']}}}
        self.assertIsNone(filtered_source_action(action, 20,
            profile={'mechanism': 'pad_service', 'pad_owner': 'pad', 'actor_ids': ['u','v']}))
        self.assertEqual(action['state_patch']['facility_state']['request_count'], 2)


class HoldDecisionQueueTests(unittest.TestCase):
    def test_short_original_tick_evidence_is_queued_without_future_truth(self):
        root = Path(__file__).resolve().parents[3]
        profile, _, script = load_contract(root, 'L6-4_v1', 1)
        owner = profile['actor_ids'][0]
        event = next(e for e in script['events']
                     if e['event_id'] == 'multi_uav_hold_entry:local:' + owner)
        admission = Admission(profile, {}, {})
        admission.bad = Mock(side_effect=lambda tick, **_: {'allow': tick == 404,
            'evidence': {'decision_tick': tick, 'unit': '1', 'source': 'focused test control'}})
        args = (None, None)
        self.assertFalse(admission.gate(event, 403, *args)['allow'])
        first = admission.gate(event, 404, *args)
        self.assertTrue(first['allow'])
        queued = admission.gate(event, 405, *args)
        self.assertTrue(queued['allow'])
        self.assertEqual(queued['evidence']['queued_decision']['decision_tick'], 404)
        self.assertEqual(queued['evidence']['action_check_tick'], 405)
        self.assertEqual(admission.bad.call_count, 2)
        fresh_episode = Admission(profile, {}, {})
        fresh_episode.bad = Mock(return_value={'allow': False, 'evidence': {'availability': 'UNKNOWN'}})
        self.assertFalse(fresh_episode.gate(event, 405, *args)['allow'])

    def test_queue_does_not_change_other_scenario_admission(self):
        profile = {'mechanism': 'local_c2'}
        admission = Admission(profile, {}, {})
        admission.bad = Mock(return_value={'allow': False, 'evidence': {}})
        self.assertFalse(admission.gate({'event_id': 'c2_loss'}, 404, None, None)['allow'])
        self.assertEqual(admission.pending_hold_decisions, {})


class LandingAcquisitionTests(unittest.TestCase):
    """Real authored scene/route; admission times here are focused test controls."""
    def replay(self, dwell, *, reject_landing=False):
        root = Path(__file__).resolve().parents[3]
        profile, scene, script = load_contract(root, 'L6-4_v1', 0)
        def gate(event, tick, *_):
            key = event['event_id']
            allowed = (tick >= 690 if key.startswith('alternate_channel:local:') else
                (not reject_landing and tick >= 780) if key.startswith('lifecycle_landing_') else True)
            return {'allow': allowed, 'evidence': {'scope': 'test admission control'}}
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'script.json'
            path.write_text(json.dumps(script))
            return execute_linked(scene, script, path, profile['episode_id'], seed=0,
                decision_gate=gate, landing_dwell_ticks=dwell,
                action_filter=lambda a,t: filtered_source_action(a,t,profile=profile))

    def test_late_admitted_landing_finishes_without_changing_observed_prefix(self):
        capped, complete = self.replay(None), self.replay(5)
        self.assertGreater(complete['engine'].duration_ticks, 900)
        self.assertEqual(capped['engine'].trajectory_rows,
            [r for r in complete['engine'].trajectory_rows if r['tick'] <= 900])
        actors = {'uav_digital_l6_4_v1', 'uav_digital_l6_4_v1_secondary'}
        tail = [r for r in complete['engine'].trajectory_rows
            if r['entity_id'] in actors and r['tick'] >= complete['engine'].duration_ticks-4]
        self.assertEqual({r['entity_id'] for r in tail}, actors)
        self.assertTrue(all(r['state'] == 'landed' and r['vel_mps'] == [0.0,0.0,0.0] for r in tail))

    def test_unreceived_landing_does_not_extend_acquisition(self):
        blocked = self.replay(5, reject_landing=True)
        self.assertEqual(blocked['engine'].duration_ticks, 900)
        self.assertFalse(any(a['action'].get('post_activity_type') == 'landed'
                             for a in blocked['audit']))


if __name__ == '__main__':
    unittest.main()
