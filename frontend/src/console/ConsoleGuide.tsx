import { useEffect, useState } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { PageHeader, PageState } from './PageState';
import './concept-help.css';

const guides = import.meta.glob('../../../docs/{getting-started,concepts,guides,reference,examples}/*.md', { query: '?raw', import: 'default' });
const legacyGuides: Record<string,string> = {
  'console.md':'getting-started/first-scenario.md', 'RUNTIME.md':'concepts/architecture.md',
  'observations.md':'guides/visualization.md', 'langgraph.md':'guides/agents.md',
  'demo-traffic-accident.md':'examples/traffic-accident.md',
  'predicates.md':'guides/predicates.md', 'behaviours.md':'guides/behaviours.md',
  'studio.md':'getting-started/first-scenario.md', 'engines.md':'guides/plugins.md',
};
export default function ConsoleGuide() {
  const { pathname, search } = useLocation();
  const requested = new URLSearchParams(search).get('guide')?.split('/').at(-1) ?? pathname.slice('/docs/platform/'.length);
  const name = requested.endsWith('.md') ? requested : `${requested}.md`;
  const guide = legacyGuides[name] ?? `guides/${name}`;
  const [text, setText] = useState<string>(), [error, setError] = useState('');
  useEffect(() => {
    let active = true; setText(undefined); setError('');
    const load = guides[`../../../docs/${guide}`];
    if (!load) { setError('This guide is not available in this build.'); return; }
    void load().then(value => { if (active) setText(String(value)); }).catch(problem => { if (active) setError(String(problem)); });
    return () => { active = false; };
  }, [guide]);
  return <div className="console-page"><PageHeader eyebrow="Documentation" title={text?.match(/^#\s+(.+)$/m)?.[1] ?? 'Console guide'} actions={<Link className="console-btn" to="/studio">Back to Studio</Link>} />
    {error ? <PageState kind="error" title="Could not open the guide" description={error} /> : text === undefined ? <PageState kind="loading" title="Loading guide" /> : <pre className="console-guide-text">{text}</pre>}
  </div>;
}
