"""Report persisted episode completion separately from process completion."""
import json
from pathlib import Path


def persisted_episode_status(path, scenario, seed):
    path = Path(path)
    summary = json.loads(path.read_text(encoding='utf-8'))
    if summary['scenario_id'] != scenario or summary['seed'] != seed:
        raise ValueError('Persisted summary belongs to another scenario/seed')
    missing = summary['unfired_events']
    if not isinstance(missing, list) or not all(isinstance(event, str) for event in missing):
        raise ValueError('Persisted summary requires an explicit missing-event list')
    return {'summary': str(path), 'unfired_events': missing,
            'process_completed': True,
            'semantic_status': 'UNFIRED_EVENTS' if missing else 'STORY_COMPLETE_PENDING_REVIEW',
            'missing_event_disposition': summary['missing_event_disposition']}
