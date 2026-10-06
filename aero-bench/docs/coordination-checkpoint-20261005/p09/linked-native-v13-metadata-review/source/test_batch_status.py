"""No simulation rerun is needed to test the summary aggregation contract."""
import json
import tempfile
import unittest
from pathlib import Path
from .batch_status import persisted_episode_status


class BatchStatusTests(unittest.TestCase):
    def test_completed_process_preserves_missing_story_events(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'summary.json'
            raw = {'scenario_id': 'L6-4_v1', 'seed': 2,
                   'unfired_events': ['hold', 'landing'],
                   'missing_event_disposition': 'requires resolution before adoption'}
            path.write_text(json.dumps(raw))
            result = persisted_episode_status(path, 'L6-4_v1', 2)
            self.assertTrue(result['process_completed'])
            self.assertEqual(result['unfired_events'], ['hold', 'landing'])
            self.assertEqual(result['semantic_status'], 'UNFIRED_EVENTS')
            raw['unfired_events'] = []
            path.write_text(json.dumps(raw))
            self.assertEqual(persisted_episode_status(path, 'L6-4_v1', 2)['semantic_status'],
                             'STORY_COMPLETE_PENDING_REVIEW')
            with self.assertRaisesRegex(ValueError, 'another scenario/seed'):
                persisted_episode_status(path, 'L6-4_v2', 2)
            del raw['unfired_events']
            path.write_text(json.dumps(raw))
            with self.assertRaises(KeyError):
                persisted_episode_status(path, 'L6-4_v1', 2)
