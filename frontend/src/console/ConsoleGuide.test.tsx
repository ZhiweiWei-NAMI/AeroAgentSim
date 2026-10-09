import { expect, it } from 'vitest';
import { loadGuideSource, resolveGuidePath } from './ConsoleGuide';

// One representative parameterized test: every legacy alias (retired
// docs/platform tree) and every new full directory path must resolve to an
// actual doc whose raw source is nonempty and carries a markdown heading.
it.each([
  ['console.md', 'concepts/views.md'],
  ['RUNTIME.md', 'concepts/architecture.md'],
  ['langgraph.md', 'guides/agents.md'],
  ['observations.md', 'guides/visualization.md'],
  ['predicates.md', 'guides/predicates.md'],
  ['behaviours.md', 'guides/behaviours.md'],
  ['studio.md', 'getting-started/first-scenario.md'],
  ['engines.md', 'concepts/plugins.md'],
  ['demo-traffic-accident.md', 'examples/traffic-accident.md'],
  ['docs/platform/guides/agents.md', 'guides/agents.md'],
  ['docs/getting-started/quickstart.md', 'getting-started/quickstart.md'],
  ['getting-started/first-scenario.md', 'getting-started/first-scenario.md'],
  ['concepts/plugins.md', 'concepts/plugins.md'],
  ['guides/visualization.md', 'guides/visualization.md'],
  ['reference/scenario.md', 'reference/scenario.md'],
  ['examples/traffic-accident.md', 'examples/traffic-accident.md'],
])('resolves %j to actual doc %j with nonempty heading', async (requested, expected) => {
  expect(resolveGuidePath(requested)).toBe(expected);
  const text = String(await loadGuideSource(expected));
  expect(text.trim().length).toBeGreaterThan(0);
  expect(text).toMatch(/^#\s+\S/);
});

it('errors instead of falling back for unknown guides and bare quickstart resolution', async () => {
  expect(resolveGuidePath('docs/platform/retired.md')).toBeNull();
  expect(resolveGuidePath('guides/nope.md')).toBeNull();
  expect(resolveGuidePath('docs/design-notes.md')).toBeNull();
  await expect(loadGuideSource('guides/nope.md')).rejects.toThrow('Unknown guide');
  // Bare names without a legacy alias resolve via the real docs tree.
  expect(resolveGuidePath('quickstart')).toBe('getting-started/quickstart.md');
  expect(resolveGuidePath('first-scenario')).toBe('getting-started/first-scenario.md');
  await expect(loadGuideSource('getting-started/quickstart.md')).resolves.toMatch(/^#\s+\S/);
});
