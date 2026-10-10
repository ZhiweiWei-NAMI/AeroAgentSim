import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { StudioApi, type Workspace } from '../studio/api';
import { displayTime } from '../pages/display-time';
import { RunsApi, type RunInfo } from '../feeds/http';
import { PageHeader } from './PageState';
import { CheckIcon, StudioIcon } from './icons';
import { useI18n } from '../i18n/I18nProvider';
import './console.css';
import { recentWorkspaces, workspaceUpdated } from '../studio/workspace-list';
import { readableName } from '../studio/guided-model';
import './home-gallery.css';
import TemplateGallery from '../newcomers/TemplateGallery';
import Welcome, { WELCOME_EVENT, WELCOME_STEPS } from '../newcomers/Welcome';

interface GalleryState { status: 'loading' | 'ready' | 'error'; list: Workspace[]; error?: string }
interface RunsState { status: 'loading' | 'ready' | 'error'; runs: RunInfo[]; error?: string }
function WorkspaceCard({ workspace, apiBase }: { workspace: Workspace; apiBase: string }) {
  const { t, locale } = useI18n();
  const entityCount = Array.isArray(workspace.scenario?.entities) ? workspace.scenario.entities.length : undefined;
  const engines = workspace.scenario?.engines && typeof workspace.scenario.engines === 'object' ? Object.keys(workspace.scenario.engines).map(id => t(readableName(id))).join(', ') : undefined;
  return <Link className="console-card console-card-accent home-workspace-card" to={`/studio?workspace=${encodeURIComponent(workspace.id)}&api=${encodeURIComponent(apiBase)}`} data-testid="workspace-card">
    <h3>{t(workspace.name)}</h3><div className="console-card-meta"><span className="console-badge console-badge-workspace">{t('Workspace')}</span><span>{workspaceUpdated(workspace, locale)}</span>{entityCount !== undefined && <span>{t('{count} entities', { count: entityCount })}</span>}{engines && <span>{t('Controls: {engines}', { engines })}</span>}{workspace.validation?.valid === true && <span>{t('Ready to run')}</span>}{workspace.validation?.valid === false && <span>{t('Needs a check')}</span>}</div>
  </Link>;
}
export default function HomePage() {
  const { t, locale } = useI18n();
  const { search } = useLocation();
  const query = useMemo(() => new URLSearchParams(search), [search]);
  const apiBase = query.get('api') ?? window.location.origin, suffix = `?api=${encodeURIComponent(apiBase)}`;
  const api = useMemo(() => new StudioApi(apiBase), [apiBase]), runsApi = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const [workspaces, setWorkspaces] = useState<GalleryState>({ status: 'loading', list: [] });
  const [showAll, setShowAll] = useState(false), gallery = recentWorkspaces(workspaces.list);
  const [runs, setRuns] = useState<RunsState>({ status: 'loading', runs: [] });
  const attempt = useRef(0);
  const load = () => {
    const current = ++attempt.current;
    setWorkspaces({ status: 'loading', list: [] }); setRuns({ status: 'loading', runs: [] });
    void api.request<Workspace[]>('/v1/studio/workspaces').then(list => { if (attempt.current === current) setWorkspaces(Array.isArray(list) ? { status: 'ready', list } : { status: 'error', list: [], error: t('Unexpected workspace list shape') }); }).catch(error => { if (attempt.current === current) setWorkspaces({ status: 'error', list: [], error: String(error) }); });
    void runsApi.runs().then(list => { if (attempt.current === current) setRuns({ status: 'ready', runs: list }); }).catch(error => { if (attempt.current === current) setRuns({ status: 'error', runs: [], error: String(error) }); });
  };
  useEffect(load, []);
  const retry = <button type="button" className="console-btn" onClick={load}>{t('Retry')}</button>;
  return <>
    <div className="console-page" data-testid="home-page">
      <PageHeader eyebrow={t('Welcome')} title={t('Pick a scenario. See what happens.')} description={t('Start with ready-made settings, watch the vehicles, then change one thing and compare.')} />
      <section aria-labelledby="templates-heading"><h2 className="console-section-title" id="templates-heading">{t('Choose your scenario')}</h2><TemplateGallery apiBase={apiBase} /></section>
      <section aria-labelledby="quickstart-heading"><h2 className="console-section-title" id="quickstart-heading">{t('How it works')}</h2><div className="console-card"><ul className="console-list">{WELCOME_STEPS.map(step => <li key={step.title}><CheckIcon /><strong>{t(step.title)}.</strong> {t(step.body)}</li>)}</ul><div className="console-card-meta">
        <Link className="console-btn" to={`/studio${suffix}`}><StudioIcon size={14} />{t('Go to Studio')}</Link>
        <button type="button" className="console-btn" data-testid="open-welcome" onClick={() => document.dispatchEvent(new CustomEvent(WELCOME_EVENT))}>{t('Show the welcome walkthrough')}</button>
        <Link className="console-btn" to={`/studio?template=traffic-accident&api=${encodeURIComponent(apiBase)}`} data-testid="open-traffic-demo">{t('Open traffic demo in Studio')}</Link>
      </div></div></section>
      <section aria-labelledby="gallery-heading"><h2 className="console-section-title" id="gallery-heading">{t('Recent workspaces')}</h2><div data-testid="scenario-gallery">
        {workspaces.status === 'loading' && <div className="console-loading">{t('Loading workspaces…')}</div>}
        {workspaces.status === 'error' && <div className="console-error-card" role="alert"><strong>{t('Could not load workspaces.')}</strong><pre>{workspaces.error}</pre>{retry}</div>}
        {workspaces.status === 'ready' && (workspaces.list.length === 0 ? <div className="console-empty">{t('No saved drafts yet. Choose “Open in Studio” on a scenario to make it your own.')}</div> : <div className="console-grid console-grid-cards">{(showAll ? gallery : gallery.slice(0, 6)).map(workspace => <WorkspaceCard key={workspace.id} workspace={workspace} apiBase={apiBase} />)}</div>)}
      </div>{gallery.length > 6 && <button className="console-btn" onClick={() => setShowAll(!showAll)}>{showAll ? t('Show recent six') : t('Show all {count} workspaces', { count: gallery.length })}</button>}</section>
      <section aria-labelledby="runs-heading"><h2 className="console-section-title" id="runs-heading">{t('Recent runs')}</h2>
        {runs.status === 'loading' && <div className="console-loading">{t('Loading runs…')}</div>}
        {runs.status === 'error' && <div className="console-error-card" role="alert"><strong>{t('Could not load runs.')}</strong><pre>{runs.error}</pre>{retry}</div>}
        {runs.status === 'ready' && (runs.runs.length === 0 ? <div className="console-empty">{t('No runs recorded. Run a scenario above to see it here.')}</div> : <ul className="console-list">{[...runs.runs].reverse().slice(0, 6).map(run => <li key={run.id}><Link to={`/inspect/${encodeURIComponent(run.id)}${suffix}&mode=replay&view=simple`}>{locale === 'zh-CN' ? run.template_title_zh ?? t(run.template_title ?? readableName(run.scenario)) : run.template_title ?? readableName(run.scenario)}</Link><span className="console-badge console-badge-workspace">{t(run.status)}</span><span>{displayTime(run.until_ns)}</span><Link to={`/runs/${encodeURIComponent(run.id)}${suffix}&mode=live&view=simple`}>{t('Watch live')}</Link></li>)}</ul>)}
      </section>
    </div><Welcome />
  </>;
}
