/** The bundled platform documentation, resolvable by path or bare file name. */
const guides = import.meta.glob('../../../docs/{getting-started,concepts,guides,reference,examples}/*.md', { query: '?raw', import: 'default' }) as Record<string, () => Promise<unknown>>;

const GUIDE_DIRS = ['getting-started', 'concepts', 'guides', 'reference', 'examples'] as const;

// Legacy bare filenames from the retired docs/platform tree, mapped to the
// actual file in docs/{getting-started,concepts,guides,reference,examples}/.
const legacyGuides: Record<string,string> = {
  'console.md':'concepts/views.md', 'RUNTIME.md':'concepts/architecture.md',
  'langgraph.md':'guides/agents.md', 'observations.md':'guides/visualization.md',
  'predicates.md':'guides/predicates.md', 'behaviours.md':'guides/behaviours.md',
  'studio.md':'getting-started/first-scenario.md', 'engines.md':'concepts/plugins.md',
  'demo-traffic-accident.md':'examples/traffic-accident.md',
};

// Bare-name index over the real docs tree, so "first-scenario" or
// "quickstart" resolve without guessing a directory.
const guideBasenames: Record<string,string[]> = Object.keys(guides).reduce((index, key) => {
  const relative = key.replace(/^.*\/docs\//, '');
  const bare = relative.split('/').at(-1)!;
  (index[bare] ??= []).push(relative);
  return index;
}, {} as Record<string, string[]>);

export function resolveGuidePath(requested: string): string | null {
  let name = requested.trim().replace(/^\/+/, '');
  // Requests may still arrive with the retired docs/platform prefix attached.
  for (const prefix of ['docs/platform/', 'docs/']) {
    if (name.startsWith(prefix)) { name = name.slice(prefix.length); break; }
  }
  if (!name) return null;
  if (!name.endsWith('.md')) name = `${name}.md`;
  if (legacyGuides[name]) return legacyGuides[name];
  if (name.includes('/')) {
    const dir = name.split('/')[0];
    return GUIDE_DIRS.includes(dir as typeof GUIDE_DIRS[number]) && guides[`../../../docs/${name}`] ? name : null;
  }
  const matches = guideBasenames[name];
  return matches?.length === 1 ? matches[0] : null;
}

export function loadGuideSource(resolved: string): Promise<unknown> {
  const load = guides[`../../../docs/${resolved}`];
  if (!load) return Promise.reject(new Error(`Unknown guide: ${resolved}`));
  return load();
}

/** The bundled guide a relative Markdown link points at, read from the document at `from` (repository-relative). */
export function linkedGuide(href: string, from: string): string | null {
  if (/^[a-z][\w+.-]*:/i.test(href) || href.startsWith('#') || href.startsWith('/')) return null;
  const path = decodeURIComponent(new URL(href, `http://repo/${from}`).pathname).replace(/^\/+/, '');
  return path.startsWith('docs/') ? resolveGuidePath(path) : null;
}
