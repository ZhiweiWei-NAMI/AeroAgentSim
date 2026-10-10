"""Scoped source-contract and prefix-availability regression checks."""
import json
from pathlib import Path
import unittest

from .linked_contract import load_contract, filtered_source_action, STEP_NS
from .linked_observations import consecutive_rain, proximity_evidence

ROOT = Path(__file__).resolve().parents[3]


class ScopedContractTests(unittest.TestCase):
    def test_only_l5_migrates_the_original_guard(self):
        profile, _, script = load_contract(ROOT, 'L5-1_v1', 0)
        original = json.loads((ROOT / profile['source_script']).read_text())
        old = next(t for t in original['triggers'] if t['type'] == 'weather_state')
        new = next(t for t in script['triggers'] if t['trigger_id'] == old['trigger_id'])
        self.assertEqual(old['sustain_ticks'], 5)
        self.assertEqual(new['min_true_ticks'], 5)
        self.assertNotIn('sustain_ticks', new)
        other, _, other_script = load_contract(ROOT, 'X1_rain_to_c2loss_to_forced_landing', 0)
        self.assertEqual(other_script['triggers'], json.loads((ROOT / other['source_script']).read_text())['triggers'])
        self.assertEqual(len(profile['weather_owner_ids']), 2)

    def test_rain_needs_five_contiguous_available_samples(self):
        rows = [{'tick': t, 'rain': .5} for t in range(5)]
        self.assertFalse(consecutive_rain(rows, 5 * STEP_NS, 'u', threshold=.5, count=5)['predicate'])
        result = consecutive_rain(rows, 6 * STEP_NS, 'u', threshold=.5, count=5)
        self.assertTrue(result['predicate'])
        self.assertLess(result['available_ns'], result['observation_ns'])
        rows[2]['rain'] = .49
        self.assertFalse(consecutive_rain(rows, 6 * STEP_NS, 'u', threshold=.5, count=5)['predicate'])
        rows[4]['rain'] = None
        self.assertIsNone(consecutive_rain(rows, 6 * STEP_NS, 'u', threshold=.5, count=5)['predicate'])

    def test_gap_breaks_rain_count_and_missing_pose_is_unknown(self):
        rows = [{'tick': t, 'rain': .8} for t in (0, 1, 3, 4, 5)]
        self.assertFalse(consecutive_rain(rows, 7 * STEP_NS, 'u', threshold=.5, count=5)['predicate'])
        self.assertIsNone(proximity_evidence([], 'a', 'b', metric='3d', threshold_m=11.485)['predicate'])

    def test_injected_fault_hold_not_removed_at_original_fault_tick(self):
        profile, _, _ = load_contract(ROOT, 'L2-1_v1', 0)
        action = {'type': 'set_runtime_state', 'entity_id': profile['actor_ids'][0],
                  'action_id': 'p09_actor_observed_station_loss:state',
                  'state_patch': {'control_state': {'safe_hold_active': True}}}
        self.assertIsNotNone(filtered_source_action(action, 260, profile=profile))


if __name__ == '__main__':
    unittest.main()
