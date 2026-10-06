"""Targeted causal regressions. Synthetic controls are not provider evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import Mock
import json

from .linked_contract import load_contract, STEP_NS, filtered_source_action
from .linked_replay import Admission, event_times, make_network_plan
from .linked_engine import execute_linked

ROOT = Path(__file__).resolve().parents[3]


def execute(profile, scene, script, **kwargs):
    with TemporaryDirectory() as folder:
        path = Path(folder) / 'event_script.json'
        path.write_text(json.dumps(script))
        return execute_linked(scene, script, path, profile['episode_id'],
            seed=profile['seed'], action_filter=lambda a, t:
                filtered_source_action(a, t, profile=profile), **kwargs)


class OwnerAuthorityTests(TestCase):
    def setUp(self):
        self.profile, self.scene, self.script = load_contract(ROOT, 'L6-4_v2', 1)
        self.a, self.b = self.profile['actor_ids']

    def local(self, phase, owner):
        return next(e for e in self.script['events']
                    if e.get('p09_owner_id') == owner and e.get('p09_phase') == phase)

    def test_peer_watchdog_cannot_block_or_admit_owner(self):
        admission = Admission(self.profile, {}, {})
        admission.bad = Mock(side_effect=lambda tick, owner: {
            'allow': owner == self.a and tick == 404, 'evidence': {'owner': owner}})
        self.assertTrue(admission.gate(self.local('multi_uav_hold_entry', self.a), 404, None, None)['allow'])
        self.assertFalse(admission.gate(self.local('multi_uav_hold_entry', self.b), 404, None, None)['allow'])
        self.assertTrue(admission.gate(self.local('multi_uav_hold_entry', self.a), 405, None, None)['allow'])
        self.assertEqual(set(admission.pending_hold_decisions), {self.a})

    def test_each_owner_moves_only_on_own_receipt(self):
        admission = Admission(self.profile, {}, {})
        admission.command = Mock(side_effect=lambda mid, owner, tick: {
            'allow': owner == self.a, 'evidence': {'receiver': owner}})
        self.assertTrue(admission.gate(self.local('alternate_channel', self.a), 845, None, None)['allow'])
        self.assertFalse(admission.gate(self.local('alternate_channel', self.b), 845, None, None)['allow'])
        for phase in ('multi_uav_hold_entry', 'multi_uav_safe_hold', 'alternate_channel'):
            aggregate = next(e for e in self.script['events'] if e['event_id'] == phase)
            self.assertEqual(aggregate['actions'], [])
            for owner in (self.a, self.b):
                title = self.local(phase, owner)['log_event']['title']
                self.assertIn(owner, title)
                self.assertNotIn('both', title)

    def test_preloaded_rendezvous_is_independent_of_rx_and_late_local_decision(self):
        again, _, _ = load_contract(ROOT, 'L6-4_v2', 2)
        self.assertEqual(self.profile['rendezvous'], again['rendezvous'])
        self.assertEqual(self.profile['rendezvous']['tick'], 460)
        run = execute(self.profile, self.scene, self.script)
        _, _, messages, controls = make_network_plan(self.profile, self.script, run,
            event_times(run), {})
        self.assertFalse(any(m['message_id'].startswith('command:') for m in messages))
        channels = [c for c in controls if c['operation'] == 'channel']
        self.assertEqual({c['time_ns'] for c in channels}, {460 * STEP_NS})
        self.assertEqual({c['owner'] for c in channels}, set(self.profile['rendezvous']['owners']))


class PendingSuccessorTests(TestCase):
    def test_late_local_recovery_opens_receipt_horizon_and_both_land(self):
        profile, scene, script = load_contract(ROOT, 'L6-4_v2', 1)
        def gate(event, tick, *_):
            allow = tick >= 845 if event.get('p09_phase') == 'alternate_channel' else True
            return {'allow': allow, 'evidence': {'scope': 'synthetic admission control'}}
        run = execute(profile, scene, script, decision_gate=gate,
            landing_dwell_ticks=5, pending_successor_wait_ticks=profile['pending_successor_wait_ticks'])
        times = event_times(run)
        self.assertEqual(times['alternate_channel:local:' + profile['actor_ids'][0]], 845 * STEP_NS)
        for owner in profile['actor_ids']:
            self.assertGreater(times['lifecycle_landing_' + owner], 900 * STEP_NS)
            tail = [r for r in run['engine'].trajectory_rows
                    if r['entity_id'] == owner and r['tick'] >= run['engine'].duration_ticks - 4]
            self.assertEqual(len(tail), 5)
            self.assertTrue(all(r['state'] == 'landed' and r['vel_mps'] == [0., 0., 0.] for r in tail))
        # Actual future commands need a received actor ACK, not event presence.
        _, _, absent, _ = make_network_plan(profile, script, run, times, {})
        self.assertFalse(any(m['message_id'].startswith('command:lifecycle_landing_') for m in absent))
        ack = {f'backup-execution-ack:{owner}': {
            'accepted': {'accepted_ns': 847 * STEP_NS},
            'submission': {'source': owner, 'receiver': profile['primary_station'],
                'send_ns': 846 * STEP_NS, 'application_payload': {
                    'kind': 'execution_report', 'owner': owner,
                    'executed_event_id': 'alternate_channel:local:' + owner,
                    'executed_ns': 845 * STEP_NS}}} for owner in profile['actor_ids']}
        episode, _, planned, _ = make_network_plan(profile, script, run, times, ack)
        landings = [m for m in planned if m['message_id'].startswith('command:lifecycle_landing_')]
        self.assertEqual(len(landings), 2)
        self.assertTrue(all(m['send_ns'] < episode['duration_ns'] for m in landings))
        expected = {m['receiver']: m['send_ns'] for m in landings}
        for owner in profile['actor_ids']:
            run['interpreter'].event_states['alternate_channel:local:' + owner].last_fired_tick = 850
        _, _, replanned, _ = make_network_plan(profile, script, run, times, ack)
        self.assertEqual({m['receiver']: m['send_ns'] for m in replanned
            if m['message_id'].startswith('command:lifecycle_landing_')}, expected)
        ack[f'backup-execution-ack:{profile["actor_ids"][0]}']['submission']['application_payload']['owner'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'exact received execution report'):
            make_network_plan(profile, script, run, times, ack)

    def test_unreceived_successor_stays_unadmitted_at_finite_wait_horizon(self):
        profile, scene, script = load_contract(ROOT, 'L6-4_v2', 1)
        def gate(event, tick, *_):
            allow = not event['event_id'].startswith('lifecycle_landing_')
            if event.get('p09_phase') == 'alternate_channel':
                allow = tick >= 845
            return {'allow': allow, 'evidence': {'scope': 'synthetic no-delivery control'}}
        run = execute(profile, scene, script, decision_gate=gate,
            landing_dwell_ticks=5, pending_successor_wait_ticks=profile['pending_successor_wait_ticks'])
        self.assertEqual(run['engine'].duration_ticks, 958)
        self.assertFalse(any(key.startswith('lifecycle_landing_') for key in event_times(run)))


class WeatherAvailabilityTests(TestCase):
    def test_environment_changes_before_owner_response_for_both_existing_variants(self):
        for scenario in ('L5-1_v1', 'L5-1_v2'):
            profile, scene, script = load_contract(ROOT, scenario, 0)
            admission = Admission(profile, {}, {})
            run = execute(profile, scene, script, decision_gate=admission.gate,
                          observe_tick=admission.observe)
            times = event_times(run)
            for event in script['events']:
                if event.get('p09_phase') != 'weather_response':
                    continue
                self.assertIn(event['event_id'], times)
                actual = times[event['event_id']]
                update = times[event['p09_weather_update_event']]
                self.assertGreaterEqual(actual, update + 3 * STEP_NS)
                rows = [r for r in run['gate_evidence']
                        if r['event_id'] == event['event_id'] and r['allow']]
                obs = rows[-1]['evidence']['observation']
                self.assertLess(obs['available_ns'], actual)
                self.assertGreater(obs['sample_ns'], update)
                for key, value in event['p09_weather_expected'].items():
                    self.assertEqual(obs['weather'][key], value)


class PadOwnerTests(TestCase):
    def setUp(self):
        self.profile, self.scene, self.script = load_contract(ROOT, 'X5_comm_failure_to_pad_contention', 0)
        self.a, self.b = self.profile['actor_ids']

    def local(self, phase, owner):
        return next(e for e in self.script['events']
                    if e.get('p09_phase') == phase and e.get('p09_owner_id') == owner)

    def test_approach_needs_own_bad_decision_not_peer_or_pad_receipt(self):
        admission = Admission(self.profile, {}, {})
        admission.bad = Mock(side_effect=lambda tick, owner: {
            'allow': owner == self.a, 'evidence': {'owner': owner}})
        self.assertTrue(admission.gate(self.local('pad_approach', self.a), 280, None, None)['allow'])
        self.assertFalse(admission.gate(self.local('pad_approach', self.b), 280, None, None)['allow'])
        aggregate = next(e for e in self.script['events'] if e['event_id'] == 'dual_uav_pad_contention')
        self.assertEqual(aggregate['actions'], [])
        self.assertEqual(len(aggregate['p09_local_children']), 2)
        for owner in (self.a, self.b):
            self.assertIn(owner, self.local('pad_approach', owner)['log_event']['title'])
            self.assertNotIn('Two UAVs', self.local('pad_approach', owner)['log_event']['title'])
            self.assertIn(owner, self.local('pad_request_intent', owner)['log_event']['title'])

    def test_pad_result_requires_sender_owned_admission_and_both_received_requests(self):
        run = execute(self.profile, self.scene, self.script)
        times = event_times(run)
        arbitration = times['pad_priority_arbitration']
        receipts = {f'pad-request:{owner}': {'accepted': {
            'source_owner': owner, 'receiver_owner': self.profile['pad_owner'],
            'accepted_ns': arbitration - STEP_NS}} for owner in (self.a, self.b)}
        _, _, absent, _ = make_network_plan(self.profile, self.script, run, times, receipts)
        self.assertFalse(any(m['message_id'].startswith('pad-result:') for m in absent))
        run['gate_evidence'] = [{'event_id': 'pad_priority_arbitration',
            'decision_tick': arbitration // STEP_NS, 'allow': True,
            'evidence': {'scope': 'synthetic pad-local service decision'}}]
        _, _, present, _ = make_network_plan(self.profile, self.script, run, times, receipts)
        results = [m for m in present if m['message_id'].startswith('pad-result:')]
        self.assertEqual(len(results), 2)
        for result in results:
            authority = result['sender_decision_evidence']
            self.assertEqual(authority['owner'], self.profile['pad_owner'])
            self.assertEqual(set(authority['request_receipts']), {self.a, self.b})
            self.assertEqual(authority['decision'], run['gate_evidence'][0])
        receipts[f'pad-request:{self.a}']['accepted']['receiver_owner'] = 'wrong_pad'
        with self.assertRaisesRegex(ValueError, 'both exact prior request receipts'):
            make_network_plan(self.profile, self.script, run, times, receipts)

    def test_request_waits_for_own_arrival_pose_and_strict_availability(self):
        event = self.local('pad_request_intent', self.a)
        interpreter = Mock()
        state = Mock(fired=True, last_fired_tick=280)
        interpreter.event_states = {event['p09_approach_event']: state}
        admission = Admission(self.profile, {}, {})
        row = {'entity_id': self.a, 'pos_enu': event['p09_arrival_target_enu_m']}
        admission.pose_prefix = [(500, [row])]
        self.assertFalse(admission.gate(event, 501, None, interpreter)['allow'])
        decision = admission.gate(event, 502, None, interpreter)
        self.assertTrue(decision['allow'])
        self.assertLess(decision['evidence']['local_pose']['available_ns'], 502 * STEP_NS)
        row['pos_enu'] = [0., 0., 0.]
        self.assertFalse(admission.gate(event, 502, None, interpreter)['allow'])

    def test_arbitration_cannot_use_request_presence_without_receipt(self):
        event = next(e for e in self.script['events'] if e['event_id'] == 'pad_priority_arbitration')
        admission = Admission(self.profile, {}, {})
        admission.received_fact = Mock(return_value={'allow': False, 'evidence': {'receipt': None}})
        self.assertFalse(admission.gate(event, 559, None, None)['allow'])

    def test_repair_is_preloaded_and_actor_recovery_requires_own_observation(self):
        profile2, _, _ = load_contract(ROOT, 'X5_comm_failure_to_pad_contention', 2)
        self.assertEqual(self.profile['repair_schedule'], profile2['repair_schedule'])
        source = next(e for e in self.script['events'] if e['event_id'] == 'station_recovered')
        trigger = next(t for t in self.script['triggers'] if t['trigger_id'] == source['trigger_ref'])
        self.assertEqual(trigger['type'], 'tick')
        self.assertEqual(trigger['tick'], 655)
        self.assertTrue(all(a['entity_id'] not in self.profile['actor_ids'] for a in source['actions']))
        admission = Admission(self.profile, {}, {})
        admission.bad = Mock(side_effect=lambda tick, owner, healthy: {
            'allow': owner == self.a and healthy, 'evidence': {'owner': owner}})
        self.assertTrue(admission.gate(self.local('pad_local_recovery', self.a), 670, None, None)['allow'])
        self.assertFalse(admission.gate(self.local('pad_local_recovery', self.b), 670, None, None)['allow'])
        for owner in (self.a, self.b):
            title = self.local('pad_local_recovery', owner)['log_event']['title']
            self.assertIn(owner, title)
            self.assertIn('observes healthy heartbeat', title)


if __name__ == '__main__':
    main()
