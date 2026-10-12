import { expect, it } from 'vitest';
import { linkedGuide } from './guides';

it('resolves relative guide links against the document that holds them', () => {
  expect(linkedGuide('../../../docs/examples/city-day.md#huangpu-placements-la3', 'scenarios/templates/air-taxi/README.md')).toBe('examples/city-day.md');
  expect(linkedGuide('behaviours.md', 'docs/guides/agents.md')).toBe('guides/behaviours.md');
  expect(linkedGuide('../reference/viewer-feed.md', 'docs/guides/visualization.md')).toBe('reference/viewer-feed.md');
  expect(linkedGuide('../../src/aeroagentsim/services/app.py', 'docs/guides/agents.md')).toBeNull();
  expect(linkedGuide('https://example.com/x.md', 'docs/guides/agents.md')).toBeNull();
  expect(linkedGuide('#section', 'docs/guides/agents.md')).toBeNull();
});
