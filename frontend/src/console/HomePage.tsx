import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { StudioApi, type Workspace } from '../studio/api';
import { displayTime } from '../pages/display-time';
import { RunsApi, type RunInfo } from '../feeds/http';
import { useLocation } from 'react-router-dom';
import { PageHeader } from './PageState';
import { CheckIcon, ExternalIcon, StudioIcon } from './icons';
import './console.css';
import { recentWorkspaces, workspaceUpdated } from '../studio/workspace-list';
import { readableName } from '../studio/guided-model';
import './home-gallery.css';

/* HomePage — real gallery over StudioApi /v1/studio/workspaces and RunsApi /v1/runs.
 * Loading / error / empty states are truthful: no fake counts, no invented
 * workspaces. The only authored demo content is the clearly labelled flagship
 * template card, which opens a real template-backed workspace in Studio. */

const FLAGSHIP_TEMPLATE = 'traffic-accident';

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
        {engines && <span>engines: {engines}</span>}
        {workspace.validation?.valid === true && <span>validation: valid</span>}
        {workspace.validation?.valid === false && <span>validation: issues</span>}
      </div>
    </Link>
  );
}

export default function HomePage() {
  // Preserve the `api` query parameter (and anything else the root app needs)
  // when leaving the home page; the run context itself is owned by the root.
  const { search } = useLocation();
  const query = useMemo(() => new URLSearchParams(search), [search]);
  const apiBase = query.get('api') ?? (['3000','4179'].includes(window.location.port) ? 'http://127.0.0.1:8002' : window.location.origin);
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
        <PageHeader eyebrow="Build · run · explore" title="Your simulation workspace" description="Configure agents through AeroGraph. Run them on one shared discrete-event runtime. Explore the same state in graph and 3D views." />

        <section aria-labelledby="demo-heading">
          <h2 className="console-section-title" id="demo-heading">Start here</h2>
          <div className="console-grid console-grid-2">
            <div className="console-card console-card-accent">
              <h3>Traffic accident (demo template)</h3>
              <p>
                An authored flagship demo scenario: a city accident reported by a vehicle, coordinated by an edge agent and inspected by two UAVs. Open an editable draft, validate its rules, then run and inject the accident.
              </p>
              <Link
                className="console-btn console-btn-primary"
                to={`/studio?template=${FLAGSHIP_TEMPLATE}&api=${encodeURIComponent(apiBase)}`}
                data-testid="open-traffic-demo"
              >
                Open traffic demo in Studio <ExternalIcon size={14} />
              </Link>
            </div>
            <div className="console-card">
              <h3>Quickstart</h3>
              <ul className="console-list">
                <li><CheckIcon /> Create or open a workspace in Studio; a workspace holds one versioned scenario.</li>
                <li><CheckIcon /> Place entities, facilities and airspaces by AeroGraph type, and configure engines with explicit writer bindings.</li>
                <li><CheckIcon /> Validate the scenario, then run it; every run gets an inspectable journal.</li>
                <li><CheckIcon /> Inspect runs live or in replay, with synchronized graph and 3D views.</li>
              </ul>
              <div className="console-card-meta">
                <Link className="console-btn" to={`/studio${suffix}`}><StudioIcon size={14} /> Go to Studio</Link>
              </div>
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
                No stored workspaces yet. Open the traffic demo template in Studio to create your first one.
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
            <div className="console-empty">No runs recorded. Run a scenario from Studio to see it here.</div>
          ) : (
            <ul className="console-list">
              {runs.runs.slice(0, 8).map(run => (
                <li key={run.id}>
                  <Link to={`/inspect/${encodeURIComponent(run.id)}${suffix}`}>{readableName(run.scenario)}</Link>
                  <span className="console-badge console-badge-workspace">{run.status}</span>
                  <span>{displayTime(run.until_ns)}</span>
                  <Link to={`/runs/${encodeURIComponent(run.id)}${suffix}`}>viewer</Link>
                </li>
              ))}
            </ul>
          ))}
        </section>

        <section aria-labelledby="arch-heading">
          <h2 className="console-section-title" id="arch-heading">Architecture &amp; capabilities</h2>
          <div className="console-grid console-grid-cards">
            <div className="console-card">
              <h3>AeroGraph</h3>
              <p>
                A typed entity graph shared by every plugin: entities, facilities,
                airspaces and relations with explicit field ownership.
                Browsable in Studio and available to Inspect views.
              </p>
            </div>
            <div className="console-card">
              <h3>Shared runtime, single-writer plugins</h3>
              <p>
                Domain plugins are interchangeable and each field has exactly one
                writer. Ship PX4, Gazebo, SUMO, ns3, weather or kinematic models
                side by side, with writer bindings declared in the scenario.
              </p>
            </div>
            <div className="console-card">
              <h3>Versioned runs &amp; journals</h3>
              <p>
                Every run produces a durable, replayable commit journal. Inspect
                any moment, follow live mode, or re-open a finished run without
                re-executing it.
              </p>
            </div>
            <div className="console-card">
              <h3>LLM &amp; LangGraph agents</h3>
              <p>
                Behaviour chains and agent workflows run on the same runtime, so
                LLM-driven decisions are journalled alongside
                physics-driven state.
              </p>
            </div>
            <div className="console-card">
              <h3>Graph + 3D inspection</h3>
              <p>
                AeroGraph structure and the 3D city view are two readings of the
                same feed — graph navigation, entity inspection and playback stay
                consistent across Studio and run consoles.
              </p>
            </div>
            <div className="console-card">
              <h3>Open-source research</h3>
              <p>
                Scenarios, plugins and the console are open source. Connect the console to your own simulation service.
              </p>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
