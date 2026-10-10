import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { StudioApi, type Workspace } from '../studio/api';
import { displayTime } from '../pages/display-time';
import { RunsApi, type RunInfo } from '../feeds/http';
import { PageHeader } from './PageState';
import { CheckIcon, StudioIcon } from './icons';
import './console.css';
import { recentWorkspaces, workspaceUpdated } from '../studio/workspace-list';
import { readableName } from '../studio/guided-model';
import './home-gallery.css';
import TemplateGallery from '../newcomers/TemplateGallery';
import Welcome, { WELCOME_EVENT, WELCOME_STEPS } from '../newcomers/Welcome';

/* HomePage — newcomer-first home. The primary surface is the plain-language
 * scenario gallery (GET /v1/templates); workspaces and runs stay below as the
 * "already familiar" surfaces. Everything is truthful: no fake counts, no
 * invented templates — the gallery shows its own loading/error/empty states. */

interface GalleryState { status: 'loading' | 'ready' | 'error'; list: Workspace[]; error?: string }
interface RunsState { status: 'loading' | 'ready' | 'error'; runs: RunInfo[]; error?: string }

function WorkspaceCard({ workspace, apiBase }: { workspace: Workspace; apiBase: string }) {
  const entityCount = Array.isArray(workspace.scenario?.entities) ? workspace.scenario.entities.length : undefined;
  const engines = workspace.scenario?.engines && typeof workspace.scenario.engines === 'object'
    ? Object.keys(workspace.scenario.engines).map(readableName).join(', ') : undefined;
  return (
    <Link
      className="console-card console-card-accent home-workspace-card"
      to={`/studio?workspace=${encodeURIComponent(workspace.id)}&api=${encodeURIComponent(apiBase)}`}
      data-testid="workspace-card"
    >
      <h3>{workspace.name}</h3>
      <div className="console-card-meta">
        <span className="console-badge console-badge-workspace">Workspace</span>
        <span>{workspaceUpdated(workspace)}</span>
        {entityCount !== undefined && <span>{entityCount} entities</span>}
        {engines && <span>Controls: {engines}</span>}
        {workspace.validation?.valid === true && <span>Ready to run</span>}
        {workspace.validation?.valid === false && <span>Needs a check</span>}
      </div>
    </Link>
  );
}

export default function HomePage() {
  const { search } = useLocation();
  const query = useMemo(() => new URLSearchParams(search), [search]);
  const apiBase = query.get('api') ?? window.location.origin;
  const suffix = `?api=${encodeURIComponent(apiBase)}`;
  const api = useMemo(() => new StudioApi(apiBase), [apiBase]);
  const runsApi = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const [workspaces, setWorkspaces] = useState<GalleryState>({ status: 'loading', list: [] });
  const [showAll, setShowAll] = useState(false);
  const gallery = recentWorkspaces(workspaces.list);
  const [runs, setRuns] = useState<RunsState>({ status: 'loading', runs: [] });
  const attempt = useRef(0);

  const load = () => {
    const current = ++attempt.current;
    setWorkspaces({ status: 'loading', list: [] });
    setRuns({ status: 'loading', runs: [] });
    void api.request<Workspace[]>('/v1/studio/workspaces')
      .then(list => { if (attempt.current === current) setWorkspaces(Array.isArray(list) ? { status: 'ready', list } : { status: 'error', list: [], error: 'Unexpected workspace list shape' }); })
      .catch(error => { if (attempt.current === current) setWorkspaces({ status: 'error', list: [], error: String(error) }); });
    void runsApi.runs()
      .then(list => { if (attempt.current === current) setRuns({ status: 'ready', runs: list }); })
      .catch(error => { if (attempt.current === current) setRuns({ status: 'error', runs: [], error: String(error) }); });
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const retry = (
    <button type="button" className="console-btn" onClick={load}>Retry</button>
  );

  return (
    <>
      <div className="console-page" data-testid="home-page">
        <PageHeader eyebrow="Welcome" title="Pick a scenario. See what happens." description="Start with ready-made settings, watch the vehicles, then change one thing and compare." />

        <section aria-labelledby="templates-heading"><h2 className="console-section-title" id="templates-heading">Choose your scenario</h2><TemplateGallery apiBase={apiBase}/></section>

        <section aria-labelledby="quickstart-heading">
          <h2 className="console-section-title" id="quickstart-heading">How it works</h2>
          <div className="console-card">
            <ul className="console-list">
              {WELCOME_STEPS.map(step => (
                <li key={step.title}><CheckIcon /> <strong>{step.title}.</strong> {step.body}</li>
              ))}
            </ul>
            <div className="console-card-meta">
              <Link className="console-btn" to={`/studio${suffix}`}><StudioIcon size={14} /> Go to Studio</Link>
              <button
                type="button"
                className="console-btn"
                data-testid="open-welcome"
                onClick={() => document.dispatchEvent(new CustomEvent(WELCOME_EVENT))}
              >
                Show the welcome walkthrough
              </button>
              {/* Legacy entry point (now template-backed); kept as a secondary
                  Studio link so existing selectors and bookmarks still work. */}
              <Link
                className="console-btn"
                to={`/studio?template=traffic-accident&api=${encodeURIComponent(apiBase)}`}
                data-testid="open-traffic-demo"
              >
                Open traffic demo in Studio
              </Link>
            </div>
          </div>
        </section>

        <section aria-labelledby="gallery-heading">
          <h2 className="console-section-title" id="gallery-heading">Recent workspaces</h2>
          <div data-testid="scenario-gallery">
            {workspaces.status === 'loading' && (
              <div className="console-loading">Loading workspaces…</div>
            )}
            {workspaces.status === 'error' && (
              <div className="console-error-card" role="alert">
                <strong>Could not load workspaces.</strong>
                <pre>{workspaces.error}</pre>
                {retry}
              </div>
            )}
            {workspaces.status === 'ready' && (workspaces.list.length === 0 ? (
              <div className="console-empty">
                No saved drafts yet. Choose “Open in Studio” on a scenario to make it your own.
              </div>
            ) : (
              <div className="console-grid console-grid-cards">
                {(showAll ? gallery : gallery.slice(0, 6)).map(workspace => (
                  <WorkspaceCard key={workspace.id} workspace={workspace} apiBase={apiBase} />
                ))}
              </div>
            ))}
          </div>
          {gallery.length > 6 && <button className="console-btn" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show recent six' : `Show all ${gallery.length} workspaces`}</button>}
        </section>

        <section aria-labelledby="runs-heading">
          <h2 className="console-section-title" id="runs-heading">Recent runs</h2>
          {runs.status === 'loading' && <div className="console-loading">Loading runs…</div>}
          {runs.status === 'error' && (
            <div className="console-error-card" role="alert">
              <strong>Could not load runs.</strong>
              <pre>{runs.error}</pre>
              {retry}
            </div>
          )}
          {runs.status === 'ready' && (runs.runs.length === 0 ? (
            <div className="console-empty">No runs recorded. Run a scenario above to see it here.</div>
          ) : (
            <ul className="console-list">
              {[...runs.runs].reverse().slice(0, 6).map(run => (
                <li key={run.id}>
                  <Link to={`/inspect/${encodeURIComponent(run.id)}${suffix}&mode=replay&view=simple`}>{run.template_title??readableName(run.scenario)}</Link>
                  <span className="console-badge console-badge-workspace">{run.status}</span>
                  <span>{displayTime(run.until_ns)}</span>
                  <Link to={`/runs/${encodeURIComponent(run.id)}${suffix}&mode=live&view=simple`}>Watch live</Link>
                </li>
              ))}
            </ul>
          ))}
        </section>
      </div>
      <Welcome />
    </>
  );
}
