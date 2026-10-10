"""Candidate flow bindings; synthetic network plans are not ns-3 evidence."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from .linked_contract import load_contract
from .linked_replay import make_network_plan, run_one
from .station_egress_candidate import station_egress_contract

ROOT = Path(__file__).resolve().parents[3]


class StationEgressTests(TestCase):
    def test_all_six_profiles_preserve_rules_and_exact_aggregate_schedule(self):
        for scenario in ('L6-4_v1', 'L6-4_v2'):
            for seed in (0, 1, 2):
                with self.subTest(scenario=scenario, seed=seed):
                    original, _, original_script = load_contract(ROOT, scenario, seed)
                    profile, scene, script = station_egress_contract(ROOT, scenario, seed)
                    workload = profile['station_egress_workload']
                    self.assertEqual(workload['source_owner'], original['primary_station'])
                    self.assertEqual({f['receiver_owner'] for f in workload['flows']},
                                     set(original['actor_ids']))
                    self.assertEqual(len({f['flow_id'] for f in workload['flows']}), 2)
                    times = sorted(t for f in workload['flows'] for t in
                                   range(f['start_ns'], f['end_ns'], f['period_ns']))
                    self.assertEqual(times, list(range(24_000_000_000, 46_000_000_000, 1_200_000)))
                    for f in workload['flows']:
                        self.assertEqual(f['payload_bytes'], 1200)
                        self.assertEqual(f['period_ns'], 2_400_000)
                    for name in ('heartbeat', 'command', 'rf_profile', 'rendezvous',
                                 'backup_channel', 'hold_decision_queue', 'motion_acquisition'):
                        self.assertEqual(profile[name], original[name])
                    self.assertEqual(script['triggers'], original_script['triggers'])
                    self.assertEqual([e['actions'] for e in script['events']],
                                     [e['actions'] for e in original_script['events']])
                    self.assertIn('declared station-egress offered', scene['description'].lower())
                    self.assertNotIn('observed queue', scene['description'].lower())
                    self.assertNotIn('station_egress_workload', original)

    def fixture(self):
        profile, _, script = station_egress_contract(ROOT, 'L6-4_v1', 0)
        owners = [profile['primary_station'], *profile['actor_ids']]
        episode = {'duration_ns': 90_000_000_000, 'episode_id': profile['episode_id'],
                   'nodes': [{'owner': owner, 'life_epoch': index, 'birth_ns': 0,
                              'death_ns': None} for index, owner in enumerate(owners)]}
        run = {'engine': object(), 'interpreter': SimpleNamespace(event_states={})}
        return profile, script, episode, run

    def plan(self, profile, script, episode, run, healthy=False):
        with patch('Dataset.semantic_simulation.ns3_episode.linked_replay.episode_from_engine',
                   return_value=episode):
            return make_network_plan(profile, script, run, {'wideband_jamming': 24_000_000_000},
                                     {}, healthy=healthy)

    def test_network_plan_binds_station_and_each_receiver_epoch(self):
        profile, script, episode, run = self.fixture()
        _, flows, _, controls = self.plan(profile, script, episode, run)
        bulk = [f for f in flows if f['flow_id'].startswith('station-egress:')]
        self.assertEqual(len(bulk), 2)
        for f in bulk:
            self.assertEqual(f['src_owner'], profile['primary_station'])
            self.assertEqual(f['life_epoch'], 0)
            receiver = next(n for n in episode['nodes'] if n['owner'] == f['dst_owner'])
            self.assertEqual(f['dst_life_epoch'], receiver['life_epoch'])
        self.assertEqual({c['owner'] for c in controls},
                         {profile['primary_station'], *profile['actor_ids']})
        _, healthy_flows, _, healthy_controls = self.plan(profile, script, episode, run, healthy=True)
        self.assertFalse(any(f['flow_id'].startswith('station-egress:') for f in healthy_flows))
        self.assertEqual(healthy_controls, controls)

    def test_wrong_recipient_and_cross_generation_are_rejected(self):
        profile, script, episode, run = self.fixture()
        bad = copy.deepcopy(profile)
        bad['station_egress_workload']['flows'][0]['receiver_owner'] = 'not_an_actor'
        with self.assertRaisesRegex(ValueError, 'both declared actor recipients'):
            self.plan(bad, script, episode, run)
        episode['nodes'][0]['death_ns'] = 30_000_000_000
        with self.assertRaisesRegex(ValueError, 'endpoint generation'):
            self.plan(profile, script, episode, run)

    def test_undeclared_scenario_seed_rejected(self):
        for scenario, seed in [('L6-1_v1', 0), ('L6-4_v1', 3), ('L6-4_v1', True)]:
            with self.assertRaisesRegex(ValueError, 'undeclared'):
                station_egress_contract(ROOT, scenario, seed)

    def test_existing_entry_delegates_without_changing_contract(self):
        contract = ({'scenario_id': 'existing'}, {'scene': 1}, {'script': 1})
        with patch('Dataset.semantic_simulation.ns3_episode.linked_replay.load_contract',
                   return_value=contract), patch(
                   'Dataset.semantic_simulation.ns3_episode.linked_replay.run_contract',
                   return_value='receipt') as execute:
            self.assertEqual(run_one('existing', 1, 'output', 'provider', healthy=True), 'receipt')
            execute.assert_called_once_with(*contract, 'output', 'provider', healthy=True)
