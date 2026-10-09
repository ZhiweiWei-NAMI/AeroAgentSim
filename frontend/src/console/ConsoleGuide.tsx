import { useEffect, useState } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { PageHeader, PageState } from './PageState';
import './concept-help.css';

const guides = import.meta.glob('../../../docs/platform/*.md', { query: '?raw', import: 'default' });
export default function ConsoleGuide() {
  const { pathname, search } = useLocation();
  const requested = new URLSearchParams(search).get('guide')?.split('/').at(-1) ?? pathname.slice('/docs/platform/'.length);
  const name = requested.endsWith('.md') ? requested : `${requested}.md`;
  const [text, setText] = useState<string>(), [error, setError] = useState('');
  useEffect(() => {
    let active = true; setText(undefined); setError('');
    const load = guides[`../../../docs/platform/${name}`];
    if (!load) { setError('This guide is not available in this build.'); return; }
    void load().then(value => { if (active) setText(String(value)); }).catch(problem => { if (active) setError(String(problem)); });
    return () => { active = false; };
  }, [name]);
  return <div className="console-page"><PageHeader eyebrow="Documentation" title={text?.match(/^#\s+(.+)$/m)?.[1] ?? 'Console guide'} actions={<Link className="console-btn" to="/studio">Back to Studio</Link>} />
    {error ? <PageState kind="error" title="Could not open the guide" description={error} /> : text === undefined ? <PageState kind="loading" title="Loading guide" /> : <pre className="console-guide-text">{text}</pre>}
  </div>;
}
