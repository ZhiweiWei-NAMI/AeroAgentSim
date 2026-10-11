import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { StudioApi, type Workspace } from '../studio/api';
import { displayTime } from '../pages/display-time';
import { RunsApi, type RunInfo, isTerminalStatus } from '../feeds/http';
import { PageHeader } from './PageState';
import { ChevronIcon, GraphIcon, RunsIcon, StudioIcon } from './icons';
import { useI18n } from '../i18n/I18nProvider';
import { recentWorkspaces, workspaceUpdated } from '../studio/workspace-list';
import { readableName } from '../studio/guided-model';
import { readableLabel } from '../pages/inspection-format';
import TemplateGallery from '../newcomers/TemplateGallery';
import './console.css';
import './home-gallery.css';

interface GalleryState { status: 'loading' | 'ready' | 'error'; list: Workspace[]; error?: string }
interface RunsState { status: 'loading' | 'ready' | 'error'; runs: RunInfo[]; error?: string }
function WorkspaceCard({ workspace, apiBase }: { workspace: Workspace; apiBase: string }) {
  const { t, locale } = useI18n();
  const entityCount = Array.isArray(workspace.scenario?.entities) ? workspace.scenario.entities.length : undefined;
  return <Link className="home-workspace-card" to={`/studio?workspace=${encodeURIComponent(workspace.id)}&api=${encodeURIComponent(apiBase)}`} data-testid="workspace-card">
    <span className="home-workspace-icon"><StudioIcon size={20} /></span>
    <div className="home-workspace-info"><h3>{t(workspace.name)}</h3><div className="home-workspace-meta"><span>{workspaceUpdated(workspace, locale)}</span>{entityCount !== undefined && <span>{t('{count} entities', { count: entityCount })}</span>}</div></div>
    {workspace.validation && <span className={`home-validation ${workspace.validation.valid ? 'is-valid' : ''}`}>{workspace.validation.valid ? t('Ready to run') : t('Needs a check')}</span>}<ChevronIcon />
  </Link>;
}
export default function HomePage() {
  const { t, locale } = useI18n();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const { search } = useLocation(), navigate = useNavigate();
  const query = useMemo(() => new URLSearchParams(search), [search]);
  const apiBase = query.get('api') ?? window.location.origin, suffix = `?api=${encodeURIComponent(apiBase)}`;
  const api = useMemo(() => new StudioApi(apiBase), [apiBase]), runsApi = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const [workspaces, setWorkspaces] = useState<GalleryState>({ status: 'loading', list: [] });
  const [showAll, setShowAll] = useState(false), [searchText, setSearchText] = useState('');
  const [runs, setRuns] = useState<RunsState>({ status: 'loading', runs: [] });
  const [creating, setCreating] = useState(false), [createError, setCreateError] = useState<string>();
  const attempt = useRef(0), createLock = useRef(false), workspaceSearch = useRef<HTMLInputElement>(null);
  const load = useCallback(() => {
    const current = ++attempt.current;
    setWorkspaces({ status: 'loading', list: [] }); setRuns({ status: 'loading', runs: [] });
    void api.request<Workspace[]>('/v1/studio/workspaces').then(list => { if (attempt.current === current) setWorkspaces(Array.isArray(list) ? { status: 'ready', list } : { status: 'error', list: [], error: t('Unexpected workspace list shape') }); }).catch(error => { if (attempt.current === current) setWorkspaces({ status: 'error', list: [], error: String(error) }); });
    void runsApi.runs(undefined, false).then(list => { if (attempt.current === current) setRuns({ status: 'ready', runs: list }); }).catch(error => { if (attempt.current === current) setRuns({ status: 'error', runs: [], error: String(error) }); });
  }, [api, runsApi, t]);
  useEffect(() => { load(); return () => { attempt.current += 1; }; }, [load]);
  const createWorkspace = async () => {
    if (createLock.current) return;
    createLock.current = true; setCreating(true); setCreateError(undefined);
    try {
      const workspace = await api.request<Workspace>('/v1/studio/workspaces', { name: t('New research scenario') });
      navigate(`/studio?workspace=${encodeURIComponent(workspace.id)}&api=${encodeURIComponent(apiBase)}`);
    } catch (error) { setCreateError(String(error)); }
    finally { createLock.current = false; setCreating(false); }
  };
  const gallery = recentWorkspaces(workspaces.list).filter(workspace => `${workspace.name} ${workspace.id}`.toLocaleLowerCase().includes(searchText.toLocaleLowerCase()));
  const orderedRuns = [...runs.runs].reverse().sort((a, b) => Number(isTerminalStatus(a.status)) - Number(isTerminalStatus(b.status)));
  const ongoingRun = orderedRuns.find(run => !isTerminalStatus(run.status));
  const runHref = (run: RunInfo) => `/runs/${encodeURIComponent(run.id)}${suffix}&mode=${isTerminalStatus(run.status) ? 'replay' : 'live'}&view=expert`;
  const retry = <button type="button" className="console-btn" onClick={load}>{t('Retry')}</button>;
  return <div className="console-page home-workbench" data-testid="home-page">
    <PageHeader eyebrow={copy('OPERATIONS / WORKSPACE', '操作台 / 工作区')} title={copy('Your research workspace', '研究工作台')} description={copy('Build scenarios, operate simulations, investigate the results.', '构建场景、操作仿真、追溯结果。')} actions={<button className="console-btn console-btn-primary" disabled={creating} onClick={() => void createWorkspace()}><span aria-hidden>＋</span>{creating ? copy('Creating…', '正在创建…') : copy('Create scenario', '创建场景')}</button>} />
    {createError && <div className="console-error-card" role="alert">{createError}</div>}
    <section className="home-launchpad" aria-label={copy('Workspace actions', '工作区操作')}>
      <button className="home-launch-action" onClick={() => { workspaceSearch.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }); workspaceSearch.current?.focus(); }}><StudioIcon size={22} /><span><strong>{copy('Open workspace', '打开工作区')}</strong><small>{copy('Continue a saved scenario', '继续编辑已有场景')}</small></span><ChevronIcon /></button>
      <Link className="home-launch-action" to={ongoingRun ? runHref(ongoingRun) : `/runs${suffix}`}><RunsIcon size={22} /><span><strong>{ongoingRun ? copy('Continue run', '继续运行') : copy('Open runs', '打开运行记录')}</strong><small>{ongoingRun ? readableLabel(ongoingRun.status, locale) : copy('Live sessions and recorded results', '实时会话与已记录结果')}</small></span><ChevronIcon /></Link>
      <Link className="home-launch-action" to={`/aerograph${suffix}`}><GraphIcon size={22} /><span><strong>{copy('Explore AeroGraph', '浏览 AeroGraph')}</strong><small>{copy('Entities, relationships and behaviours', '实体、关系与行为')}</small></span><ChevronIcon /></Link>
    </section>
    <div className="home-operations-grid">
      <section className="home-panel" aria-labelledby="gallery-heading"><div className="home-panel-heading"><h2 id="gallery-heading">{t('Recent workspaces')}</h2>{workspaces.status === 'ready' && <span className="home-count">{workspaces.list.length}</span>}<input ref={workspaceSearch} className="home-search" type="search" aria-label={copy('Search workspaces', '搜索工作区')} placeholder={copy('Search workspaces…', '搜索工作区…')} value={searchText} onChange={event => setSearchText(event.target.value)} /></div><div data-testid="scenario-gallery">
        {workspaces.status === 'loading' && <div className="home-panel-state console-loading">{t('Loading workspaces…')}</div>}
        {workspaces.status === 'error' && <div className="console-error-card" role="alert"><strong>{t('Could not load workspaces.')}</strong><pre>{workspaces.error}</pre>{retry}</div>}
        {workspaces.status === 'ready' && (gallery.length === 0 ? <div className="home-panel-state"><StudioIcon size={28} /><p>{searchText ? copy('No matching workspaces.', '未找到匹配的工作区。') : copy('Create a scenario to start your workspace.', '创建场景，开始你的工作区。')}</p>{!searchText && <button className="console-btn" disabled={creating} onClick={() => void createWorkspace()}>{copy('Create scenario', '创建场景')}</button>}</div> : <div>{(showAll ? gallery : gallery.slice(0, 5)).map(workspace => <WorkspaceCard key={workspace.id} workspace={workspace} apiBase={apiBase} />)}</div>)}
      </div>{gallery.length > 5 && <button className="home-panel-more" onClick={() => setShowAll(!showAll)}>{showAll ? copy('Show recent workspaces', '显示最近工作区') : t('Show all {count} workspaces', { count: gallery.length })}<ChevronIcon /></button>}</section>
      <section className="home-panel" aria-labelledby="runs-heading"><div className="home-panel-heading"><h2 id="runs-heading">{t('Recent runs')}</h2><Link to={`/runs${suffix}`}>{copy('View all', '查看全部')} <span aria-hidden>↗</span></Link></div>
        {runs.status === 'loading' && <div className="home-panel-state console-loading">{t('Loading runs…')}</div>}
        {runs.status === 'error' && <div className="console-error-card" role="alert"><strong>{t('Could not load runs.')}</strong><pre>{runs.error}</pre>{retry}</div>}
        {runs.status === 'ready' && (runs.runs.length === 0 ? <div className="home-panel-state"><RunsIcon size={28} /><p>{copy('Runs will appear here when you launch a scenario.', '启动场景后，运行记录将在此显示。')}</p></div> : <div className="home-run-list">{orderedRuns.slice(0, 5).map(run => <Link className="home-run-row" key={run.id} to={runHref(run)}><span className={`home-run-state ${isTerminalStatus(run.status) ? 'is-terminal' : 'is-active'}`} aria-hidden /><span className="home-run-name"><strong>{locale === 'zh-CN' ? run.template_title_zh ?? t(run.template_title ?? readableName(run.scenario)) : run.template_title ?? readableName(run.scenario)}</strong><small>{run.id}</small></span><span className="home-run-status">{readableLabel(run.status, locale)}<small>{copy('Limit', '时限')} {displayTime(run.until_ns)}</small></span><ChevronIcon /></Link>)}</div>)}
      </section>
    </div>
    <section className="home-examples" aria-labelledby="templates-heading"><div className="home-examples-heading"><div><p className="console-page-header-eyebrow">{copy('SCENARIO LIBRARY', '场景库')}</p><h2 id="templates-heading">{copy('Start from an example', '从示例开始')}</h2></div><span>{copy('Ready to run, ready to adapt.', '即刻运行，自由调整。')}</span></div><TemplateGallery apiBase={apiBase} /></section>
  </div>;
}
