import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type { EntityKey } from '../contracts/viewer-feed';
import { RunsApi } from '../feeds/http';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { TemplateApi, type RunKpis, type ScenarioTemplate } from '../newcomers/api';
import { businessProjection, resultsAt, type RecordedBusinessEvent } from '../newcomers/projection';
import { runStatusLabel } from '../newcomers/presentation';
import { metricHelp, metricLabel } from '../newcomers/metric-help';
import { readableLabel } from '../pages/inspection-format';
import { displayTime } from '../pages/display-time';
import { CauseTracePanel } from '../trace/CauseTracePanel';
import { subscribeCauseTrace } from '../trace/bus';
import { explain, type CauseRef } from '../trace/cause-trace';
import { entityId, resolveBinding } from '../viewport/bindings';
import { displayName } from '../viewport/semantic';
import { worldPosition } from '../viewport/coordinates';
import type { Viewport } from '../viewport/viewport';
import { ApiError, TERMINAL, WorkbenchApi, type Project, type RunRecord } from './api';
import { ConfigPanel } from './ConfigPanel';
import { Dock, type DockTab } from './Dock';
import { Gallery } from './Gallery';
import { InspectorPanel } from './InspectorPanel';
import { ObjectsPanel } from './ObjectsPanel';
import { RecordsPanel } from './RecordsPanel';
import { runClockTime, sameEntity, useRunSession } from './useRunSession';
import { WaitingBanner } from '../pages/WaitingBanner';
import type { ApplyRecord } from '../assistant/api';
import { AssistantPanel } from '../assistant/AssistantPanel';
import type { EditMode } from '../assistant/ModeSwitch';
import { NeuralGlyph } from '../assistant/motion/NeuralGlyph';
import { StageEmpty } from '../assistant/motion/StageEmpty';
import { PlanBoard } from '../assistant/PlanBoard';
import { Contained } from '../assistant/Contained';
import { useAssistantMode } from '../assistant/useAssistantMode';
import '../assistant/motion/motion.css';
import '../assistant/assistant.css';
import '../viewport/viewer.css';
import '../pages/inspect.css';
import './workbench.css';
import './run-status.css';

const ViewportView = lazy(() => import('../viewport/ViewportView').then(module => ({ default: module.ViewportView })));
const ScenePreview = lazy(() => import('../studio/ScenePreview').then(module => ({ default: module.ScenePreview })));
const BehaviourRunGraph = lazy(() => import('../behaviours/BehaviourRunGraph').then(module => ({ default: module.BehaviourRunGraph })));
const NonspatialViews = lazy(() => import('../viewport/NonspatialViews').then(module => ({ default: module.NonspatialViews })));
const CompareRuns = lazy(() => import('../newcomers/CompareRuns').then(module => ({ default: module.CompareRuns })));
const AeroGraphPage = lazy(() => import('../pages/AeroGraphPage'));
const ArtifactsPanel = lazy(() => import('../observations/ArtifactsPanel').then(module => ({ default: module.ArtifactsPanel })));

type Toast = { id: number; kind: 'info' | 'success' | 'error'; text: string };
type Camera = 'orbit' | 'follow' | 'chase';

/**
 * The one scene. A fixed 3D world fills the screen; every tool hovers over it.
 * URL query carries context: run, mode, cut, sel, panel, records, compare, graph, gallery, contract.
 */
/** The run record is authoritative, but the live stream may already be further
 *  along the lifecycle (e.g. running right after Start) than the last poll. */
export function runStatus(recorded?: string, streamed?: string): string | undefined {
  const stage = (value?: string) => value === undefined || value === 'loading' ? -1 : value === 'initialized' ? 0 : TERMINAL.has(value) ? 2 : 1;
  if (recorded === undefined) return streamed;
  return stage(streamed) > stage(recorded) ? streamed : recorded;
}

/** C6: terminal status but KPIs/Story still aggregating — not finished yet. */
export function resultsPending(status: string | undefined, record: { results_final?: boolean } | undefined): boolean {
  return !!status && TERMINAL.has(status) && record?.results_final === false;
}

export default function Workbench() {
  const { locale, setLocale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const location = useLocation(), navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const projectId = /^\/w\/([^/?]+)/.exec(location.pathname)?.[1];
  const apiBase = query.get('api') ?? window.location.origin;
  const api = useMemo(() => new WorkbenchApi(apiBase), [apiBase]);
  const runsApi = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const templateApi = useMemo(() => new TemplateApi(apiBase), [apiBase]);
  const runId = query.get('run') ?? undefined;
  const panel = query.get('panel') ?? undefined;
  const assistantOpen = panel === 'assistant';
  const graph = query.get('graph');
  const update = useCallback((patch: Record<string, string | undefined>, push = false, path?: string) => {
    const next = new URLSearchParams(location.search);
    for (const [key, value] of Object.entries(patch)) { if (value === undefined) next.delete(key); else next.set(key, value); }
    const search = next.toString();
    navigate(`${path ?? location.pathname}${search ? `?${search}` : ''}`, { replace: !push });
  }, [location.pathname, location.search, navigate]);

  // Toasts
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toast = useCallback((kind: Toast['kind'], text: string) => { const id = Date.now() + Math.random(); setToasts(list => [...list.slice(-3), { id, kind, text }]); setTimeout(() => setToasts(list => list.filter(item => item.id !== id)), kind === 'error' ? 9000 : 4500); }, []);
  const problemText = (problem: unknown) => problem instanceof ApiError ? problem.detail : String(problem);

  // Project
  const [previewDraft, setPreviewDraft] = useState<{ id: string; scenario: Project['scenario']; dirty: boolean }>();
  const previewChanged = useCallback((id: string, scenario: Record<string, unknown>, dirty: boolean) => setPreviewDraft({ id, scenario: scenario as Project['scenario'], dirty }), []);
  const [inspectorCompact, setInspectorCompact] = useState(true);
  const [project, setProject] = useState<Project>(), [projectError, setProjectError] = useState<string>();
  useEffect(() => {
    setProject(undefined); setProjectError(undefined);
    if (!projectId) return;
    const abort = new AbortController();
    void api.project(decodeURIComponent(projectId), abort.signal).then(setProject).catch(problem => { if (!abort.signal.aborted) setProjectError(problemText(problem)); });
    return () => abort.abort();
  }, [api, projectId]);

  // Runs of this project: the attached run's record, and recovery of an active run after refresh.
  const [records, setRecords] = useState<RunRecord[]>(), [recordsKey, setRecordsKey] = useState(0), [recordsError, setRecordsError] = useState<string>();
  useEffect(() => {
    const abort = new AbortController();
    let loading = false;
    const load = async () => {
      if (loading || abort.signal.aborted) return;
      loading = true;
      try { const result = await api.runs(abort.signal); if (!abort.signal.aborted) { setRecords(result); setRecordsError(undefined); } }
      catch (problem) { if (!abort.signal.aborted) setRecordsError(problemText(problem)); }
      finally { loading = false; }
    };
    load(); const timer = setInterval(load, 5000);
    return () => { abort.abort(); clearInterval(timer); };
  }, [api, recordsKey]);
  const record = records?.find(row => row.id === runId);
  useEffect(() => {
    if (!project || runId || !records) return;
    const active = [...records].reverse().find(row => row.studio_workspace === project.id && !TERMINAL.has(row.status));
    if (active) update({ run: active.id, mode: 'live' });
  }, [project?.id, records !== undefined]);
  // A run opened by itself moves under its project, keeping the run context.
  useEffect(() => { if (!projectId && record?.studio_workspace) update({}, false, `/w/${encodeURIComponent(record.studio_workspace)}`); }, [projectId, record?.studio_workspace]);
  const requestedMode = query.get('mode');
  const mode: 'live' | 'replay' | undefined = requestedMode === 'live' || requestedMode === 'replay' ? requestedMode : record ? (TERMINAL.has(record.status) ? 'replay' : 'live') : undefined;
  const initialCut = query.get('cut') ? Number(query.get('cut')) : undefined;
  const session = useRunSession(apiBase, runId && mode ? runId : undefined, mode ?? 'replay', Number.isSafeInteger(initialCut) ? initialCut : undefined);
  // Runtime metadata drives simulation controls; the journal can arrive later.
  const status = runStatus(!recordsError ? record?.status : undefined, session?.status);
  // C6: a terminal status with still-aggregating KPIs/Story is not finished yet.
  const finalizing = resultsPending(status, record);
  useEffect(() => { setRecordsKey(value => value + 1); }, [session?.status, finalizing]);
  const waiting = !recordsError && record ? record.waiting : session?.waiting;
  const assistant = useAssistantMode({ apiBase, open: assistantOpen, sessionId: query.get('assistant') ?? undefined, projectId, project, locale, runId,
    terminal: TERMINAL.has(status ?? ''), workspaceVersion: record?.workspace_version, update });
  if (panel && !assistantOpen) assistant.lastSection.current = panel;
  const morphOf = (mode: EditMode) => assistant.morph ? assistant.morph.from === mode ? 'out' as const : 'in' as const : undefined;
  const switchOrigin = () => {
    const rect = document.querySelector('.as-switch')?.getBoundingClientRect() ?? document.querySelector('.wb-right')?.getBoundingClientRect();
    return rect ? { x: rect.left + rect.width / 2, y: rect.top + Math.min(rect.height / 2, 40) } : { x: innerWidth - 120, y: 100 };
  };
  const toggleAssistant = () => {
    if (assistantOpen) update({ panel: undefined });
    else if (project && panel) assistant.switchMode('chat', switchOrigin());
    else update({ panel: 'assistant', gallery: undefined });
  };
  const openForm = () => { if (assistantOpen && project) assistant.switchMode('form', switchOrigin()); else update({ panel: panel ? undefined : 'controls' }); };
  const [metadataBoundary, setMetadataBoundary] = useState<{ runId: string; ns: string }>();
  useEffect(() => {
    if (!runId || !waiting) return;
    setMetadataBoundary(previous => ({ runId, ns: previous?.runId === runId && BigInt(previous.ns) > BigInt(waiting.at_ns) ? previous.ns : waiting.at_ns }));
  }, [runId, waiting?.at_ns]);
  // Host pacing of the attached live run (simulation speed, not playback speed).
  const canSetSpeed = !!record && !TERMINAL.has(record.status);
  useEffect(() => {
    setSpeedState(undefined);
    if (!runId || mode !== 'live' || !canSetSpeed) return;
    const abort = new AbortController();
    void api.speed(runId, abort.signal).then(result => setSpeedState(result.factor)).catch(problem => { if (!abort.signal.aborted) toast('error', problemText(problem)); });
    return () => abort.abort();
  }, [api, runId, mode, canSetSpeed]);
  const changeSpeed = async (factor: number | null) => {
    if (!runId || speedPending) return;
    setSpeedPending(true);
    try { const result = await api.setSpeed(runId, factor); setSpeedState(result.factor); } catch (problem) { toast('error', problemText(problem)); } finally { setSpeedPending(false); }
  };
  const [, setFrame] = useState(0);
  const tick = useCallback(() => setFrame(value => value + 1), []);

  // Selection, camera and display-only state
  const [selected, setSelected] = useState<EntityKey>();
  const [selectionOrigin, setSelectionOrigin] = useState<'manual' | 'director'>('manual');
  const [operateRequest, setOperateRequest] = useState(0);
  const [camera, setCamera] = useState<Camera>('orbit'), [director, setDirector] = useState(true), [directorPaused, updateDirectorPaused] = useState(!!query.get('sel'));
  const directorPause = useRef(directorPaused);
  const setDirectorPaused = (value: boolean) => { directorPause.current = value; updateDirectorPaused(value); };
  const [focus, setFocus] = useState<{ position: [number, number, number]; revision: number }>();
  const viewport = useRef<Viewport>();
  const [leftOpen, setLeftOpen] = useState(true), [rightOpen, setRightOpen] = useState(true);
  const [dockOpen, setDockOpen] = useState(false), [dockTab, setDockTab] = useState<DockTab>('events');
  const [palette, setPalette] = useState(false), [initializing, setInitializing] = useState(false), [pending, setPending] = useState<string>();
  useEffect(() => {
    if (!pending) return;
    if (TERMINAL.has(status ?? '') || pending === 'pause' && status === 'paused' ||
      ['start', 'resume'].includes(pending) && ['running', 'waiting_for_input'].includes(status ?? '')) setPending(undefined);
  }, [pending, status]);
  useEffect(() => { setPending(undefined); }, [runId]);
  const [speed, setSpeedState] = useState<number | null>();
  const [speedPending, setSpeedPending] = useState(false);
  const [graphPosition, setGraphPosition] = useState<{ x: number; y: number }>();
  const graphDrag = useRef<{ x: number; y: number; left: number; top: number }>();
  const graphWindow = useRef<HTMLElement>(null);
  const fitGraphPosition = (x: number, y: number, rect: DOMRect) => {
    const frame = document.querySelector('[data-testid=wb-scene-frame]')?.getBoundingClientRect();
    const left = frame?.left ?? 12, top = frame?.top ?? 12;
    const inspector = document.querySelector('.wb-right')?.getBoundingClientRect();
    const right = Math.min(frame?.right ?? window.innerWidth - 12, inspector ? inspector.left - 12 : window.innerWidth - 12);
    return { x: Math.max(left, Math.min(right - rect.width, x)), y: Math.max(top, Math.min((frame?.bottom ?? window.innerHeight - 12) - rect.height, y)) };
  };
  useEffect(() => {
    if (!query.get('graph') || !graphWindow.current) return;
    const clamp = () => {
      const rect = graphWindow.current?.getBoundingClientRect();
      if (!rect) return;
      setGraphPosition(previous => {
        if (!previous) return previous;
        const { x, y } = fitGraphPosition(previous.x, previous.y, rect);
        return x === previous.x && y === previous.y ? previous : { x, y };
      });
    };
    const observer = new ResizeObserver(clamp); observer.observe(graphWindow.current);
    window.addEventListener('resize', clamp);
    return () => { observer.disconnect(); window.removeEventListener('resize', clamp); };
  }, [query.get('graph')]);
  useEffect(() => { setSelected(undefined); setSelectionOrigin('manual'); setTraceStack([]); setDirectorPaused(!!query.get('sel')); setCamera('orbit'); }, [runId]);
  useEffect(() => {
    if (!session || selected) return;
    const wanted = query.get('sel');
    const entity = wanted ? [...session.store.entities.values()].find(row => row.key.id === wanted) : undefined;
    if (entity) { setDirectorPaused(true); setSelectionOrigin('manual'); setSelected(entity.key); session.store.select(entity.key); }
  }, [session?.store, session?.revision]);
  const select = (key: EntityKey | undefined, fly = false) => {
    // Every explicit selection owns the camera until the user resumes the director.
    setDirectorPaused(true);
    setSelectionOrigin('manual');
    setSelected(key); session?.store.select(key);
    update({ sel: key?.id });
    const spatial = !!key && !!session && !!resolveBinding(session.store.header, session.store.entities.get(entityId(key))?.typeId ?? '');
    if (key && fly && session) { setDirectorPaused(true); setCamera(spatial ? 'follow' : 'orbit'); }
    else if (!spatial && camera === 'follow') setCamera('orbit');
  };
  const focusSelection = () => {
    if (!selected || !store || !clock) return;
    const entity = store.entities.get(entityId(selected));
    const binding = entity && resolveBinding(store.header, entity.typeId);
    if (!binding) return;
    const value = store.sample(selected, binding.positionField, clock.ns);
    if (!Array.isArray(value)) return;
    const position = worldPosition(value, binding.frame, store.header.origin);
    setDirectorPaused(true); setCamera('orbit');
    setFocus(previous => ({ position: [position.x, -position.z, position.y], revision: (previous?.revision ?? 0) + 1 }));
  };

  // Business projection: events and KPIs at the viewed moment.
  const store = session?.store, clock = session?.clock;
  const knownAt = store?.viewCursor?.knownAt ?? -1;
  const projected = useMemo(() => store && clock ? businessProjection(store.commits, knownAt, clock.ns) : undefined, [store, store?.commits.length, knownAt, clock?.ns]);
  const [template, setTemplate] = useState<ScenarioTemplate>();
  const templateId = project?.template?.id ?? record?.template_id;
  useEffect(() => { if (!templateId) { setTemplate(undefined); return; } const abort = new AbortController(); void templateApi.template(templateId, abort.signal).then(setTemplate).catch(() => setTemplate(undefined)); return () => abort.abort(); }, [templateApi, templateId]);
  const [apiKpis, setApiKpis] = useState<RunKpis>(), [kpisError, setKpisError] = useState<string>();
  const hasKpiFeed = !!store?.commits.some(commit => commit.kpis !== undefined);
  useEffect(() => {
    setApiKpis(undefined); setKpisError(undefined);
    if (!runId || hasKpiFeed) return;
    const abort = new AbortController();
    let loading = false;
    const load = async () => {
      if (loading || abort.signal.aborted) return;
      loading = true;
      try { const result = await templateApi.kpis(runId, abort.signal); if (!abort.signal.aborted) { setApiKpis(result); setKpisError(undefined); } }
      catch (problem) { if (!abort.signal.aborted) setKpisError(problemText(problem)); }
      finally { loading = false; }
    };
    load(); const timer = setInterval(() => { if (!TERMINAL.has(status ?? '')) load(); }, 3000);
    return () => { abort.abort(); clearInterval(timer); };
  }, [templateApi, runId, hasKpiFeed]);
  const atEnd = !!store && !!clock && knownAt === store.commits.at(-1)?.commitIndex && BigInt(clock.ns) >= BigInt(clock.end) && TERMINAL.has(status ?? '') && !finalizing;
  const fromApi = clock ? resultsAt(apiKpis, clock.ns, atEnd, knownAt) : { values: {}, series: [] };
  const kpis = hasKpiFeed && projected ? projected.values : fromApi.values;
  const series = hasKpiFeed && projected ? projected.series : fromApi.series;
  const kpiIds = template?.kpis ?? Object.keys(kpis);
  const kpiPanel = useRef<HTMLDivElement>(null), [kpiHeight, setKpiHeight] = useState(0);
  useLayoutEffect(() => {
    const node = kpiPanel.current;
    if (!node) { setKpiHeight(0); return; }
    const measure = () => setKpiHeight(node.getBoundingClientRect().height);
    measure(); const observer = new ResizeObserver(measure); observer.observe(node);
    return () => observer.disconnect();
  }, [!!session, kpiIds.length]);
  useLayoutEffect(() => {
    if (!graph || !dockOpen) return;
    const frame = document.querySelector<HTMLElement>('[data-testid=wb-scene-frame]');
    if (!frame) return;
    // Keep a useful plot when the graph and expanded timeline compete for height.
    const fit = () => { if (frame.getBoundingClientRect().height < 400) setDockOpen(false); };
    fit(); const observer = new ResizeObserver(fit); observer.observe(frame);
    return () => observer.disconnect();
  }, [!!graph, dockOpen]);
  const entityNames = useMemo(() => new Map(store ? [...store.entities.values()].map(entity => [entity.key.id, displayName(entity, store.header, locale)]) : []), [store, session?.revision, locale]);

  // Cause → effect trace
  const [traceStack, setTraceStack] = useState<CauseRef[]>([]);
  const traceRef = traceStack.at(-1);
  const traceModel = useMemo(() => store && traceRef ? explain(store, traceRef, locale) : undefined, [store, traceRef, store?.commits.length, locale]);
  const openTrace = (ref: CauseRef, reset = true) => {
    if (!store || !session) return;
    setTraceStack(previous => reset ? [ref] : [...previous, ref]);
    const model = explain(store, ref, locale);
    if (model) session.seekCut(model.cut);
    setDockTab('why'); setDockOpen(true); setDirectorPaused(true);
  };
  const openTraceRef = useRef(openTrace); openTraceRef.current = openTrace;
  useEffect(() => subscribeCauseTrace(ref => openTraceRef.current(ref)), []);

  const selectEvent = (event: RecordedBusinessEvent) => {
    if (!session || !store) return;
    session.seekOffset(event.offset); setDirectorPaused(true);
    const entities = event.entities.flatMap(id => [...store.entities.values()].filter(row => row.key.id === id));
    const entity = entities.find(row => resolveBinding(store.header, row.typeId)) ?? entities[0];
    if (entity) select(entity.key, true);
    else if (event.position) { setCamera('orbit'); setFocus(previous => ({ position: event.position!, revision: (previous?.revision ?? 0) + 1 })); }
  };

  // Simulation control (never playback)
  const initialize = async () => {
    if (!project?.latest_version) return;
    setInitializing(true);
    try {
      const run = await api.initialize(project.id, project.latest_version.version);
      // A scenario authored "as fast as possible" starts at a watchable 5×; the selector shows and changes it.
      try { const current = await api.speed(run.id); setSpeedState(current.factor === null ? (await api.setSpeed(run.id, 5)).factor : current.factor); }
      catch (problem) { toast('error', `${L('Simulation speed could not be loaded.', '无法读取仿真速度。')} ${problemText(problem)}`); }
      toast('success', L(`Initialized version ${project.latest_version.version}. The world is held at t = 0 — press Start to run it.`, `已初始化版本 ${project.latest_version.version}。世界停在 t = 0，按“启动”开始运行。`));
      setRecordsKey(value => value + 1);
      update({ run: run.id, mode: 'live', cut: undefined, sel: undefined, panel: assistantOpen ? 'assistant' : undefined }, true);
    } catch (problem) { toast('error', problemText(problem)); } finally { setInitializing(false); }
  };
  const control = async (action: 'start' | 'pause' | 'resume' | 'stop') => {
    if (!runId || pending) return;
    setPending(action);
    try {
      const apply = () => api.control(runId, action);
      // A pause/stop must follow the input acknowledgement already in flight.
      // Share the same queue used for source progress and event admission.
      await (session ? session.inputSource.serialize(apply) : apply());
      setRecordsKey(value => value + 1); if (action === 'start') session?.backToLive();
    }
    catch (problem) { toast('error', problemText(problem)); setPending(undefined); setRecordsKey(value => value + 1); }
  };

  // The assistant's Confirm wrote a draft and saved a version: show the real project.
  const applied = async (applyRecord: ApplyRecord) => {
    assistant.applyDone(applyRecord);
    toast('success', L(`Applied. Version ${applyRecord.version} is saved and ready to initialize.`, `已应用。版本 ${applyRecord.version} 已保存，可以初始化。`));
    if (applyRecord.workspace_id !== project?.id) { update({ panel: 'assistant', gallery: undefined }, true, `/w/${encodeURIComponent(applyRecord.workspace_id)}`); return; }
    try { setPreviewDraft(undefined); setProject(await api.project(project.id)); } catch (problem) { toast('error', problemText(problem)); }
  };

  // Projects and templates
  const createBlankProject = async () => {
    const created = await api.createProject(L('Untitled project', '未命名项目'));
    update({ gallery: undefined, guide: undefined, run: undefined, mode: undefined, cut: undefined, sel: undefined, assistant: undefined, records: undefined, compare: undefined, contract: undefined, graph: undefined, panel: 'space' }, true, `/w/${encodeURIComponent(created.id)}`);
  };
  const createFromTemplate = async (template: ScenarioTemplate) => {
    try { const created = await api.createFromTemplate(template.id); update({ gallery: undefined, run: undefined, mode: undefined, cut: undefined, sel: undefined, panel: 'controls' }, true, `/w/${encodeURIComponent(created.workspace_id)}`); }
    catch (problem) { toast('error', problemText(problem)); }
  };
  const derive = async (run: RunRecord) => {
    try { const created = await api.derive(run.id); toast('success', L('New draft created from that run. The original run is kept.', '已从该运行新建草稿，原运行保留。')); update({ records: undefined, run: undefined, mode: undefined, cut: undefined, sel: undefined, compare: undefined, panel: 'controls' }, true, `/w/${encodeURIComponent(created.workspace_id)}`); }
    catch (problem) { toast('error', problemText(problem)); }
  };

  // Keyboard: playback and views only. Simulation control is always an explicit click.
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.repeat || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      // A photo or technical-details dialog owns its keyboard and focus.
      if (target?.closest('[role="dialog"], [role="alertdialog"]') || document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      const editing = target && (target.isContentEditable || /^(TEXTAREA|SELECT)$/.test(target.tagName) ||
        target.tagName === 'INPUT' && !['range', 'checkbox', 'radio', 'button', 'submit'].includes((target as HTMLInputElement).type));
      if (editing && event.key !== 'Escape' && !((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k')) return;
      // Escape first leaves the field being edited; a second Escape closes the panel.
      if (editing && event.key === 'Escape') { target.blur(); return; }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPalette(value => !value); return; }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === ' ' && session) { event.preventDefault(); event.stopPropagation(); session.togglePlayback(); }
      else if (event.key === 'i' && session?.followingLive && mode === 'live' && ['running', 'paused', 'waiting_for_input'].includes(status ?? '')) { update({ panel: undefined }); setRightOpen(true); setOperateRequest(value => value + 1); }
      else if (event.key === 'g' && session) update({ graph: query.get('graph') ? undefined : '1' });
      else if (event.key === 'c' && project) openForm();
      else if (event.key === 'a') toggleAssistant();
      else if (event.key === 'r') update({ records: query.get('records') ? undefined : '1' });
      else if (event.key === 'f' && selected && store && resolveBinding(store.header, store.entities.get(entityId(selected))?.typeId ?? '')) { setDirectorPaused(true); setCamera(value => value === 'follow' ? 'orbit' : 'follow'); }
      else if (event.key === 'l' && session) session.backToLive();
      else if (event.key === 'Escape') { if (palette) setPalette(false); else if (query.get('compare')) update({ compare: undefined }); else if (query.get('contract')) update({ contract: undefined, type: undefined }); else if (query.get('graph')) update({ graph: undefined }); else if (query.get('records')) update({ records: undefined }); else if (query.get('panel')) update({ panel: undefined }); else if (selected) select(undefined); }
    };
    // Claim playback before nested operation components receive the same Space.
    addEventListener('keydown', key, true); return () => removeEventListener('keydown', key, true);
  });

  // The run is spatial when any entity has a position binding OR the header
  // declares a real city: GEO-013 — a GeoJSON/real city with zero objects must
  // still show the 3D city view, not collapse to the relation-only view.
  const spatial = !!store && (store.header.presentation.length > 0 || !!store.header.scene?.city ||
    [...store.entities.values()].some(entity => resolveBinding(store.header, entity.typeId)));
  const live = mode === 'live' && !!status && !TERMINAL.has(status);
  // Only an active run has a live moment to return to.
  const history = !!session && mode === 'live' && !session.followingLive && !TERMINAL.has(status ?? '');
  // A finished run seeked before its last record shows earlier values; say so, as for live history.
  const earlier = !!session && !!store && TERMINAL.has(status ?? '') && session.cut !== undefined && session.cut !== store.commits.at(-1)?.commitIndex;
  const canOperate = !!session && session.followingLive && mode === 'live' && ['running', 'paused', 'waiting_for_input'].includes(status ?? '');
  const showGallery = (!projectId && !runId && !query.has('contract') && !query.has('records') && !assistantOpen) || query.get('gallery') === '1';
  const title = (project ? project.name === template?.title ? localized(template, 'title', locale) : project.name : undefined) ?? (record ? localized(record, 'template_title', locale) ?? record.workspace_name ?? record.scenario : undefined);
  const contractType = query.has('contract') ? query.get('type') ?? '' : undefined;
  const selectedSpatial = !!selected && !!store && !!resolveBinding(store.header, store.entities.get(entityId(selected))?.typeId ?? '');
  const following = camera === 'follow' && selectedSpatial;
  // Keep the usable scene and panel bounds tied to the panels actually on screen.
  const boardOpen = assistantOpen && assistant.board && !query.get('records');
  const layout = { '--wb-left-space': query.get('records') ? 'calc(var(--wb-left) + 60px)' : boardOpen ? 'var(--as-board-width)' : leftOpen ? 'var(--wb-left)' : '0px',
    '--wb-right-space': panel ? assistantOpen ? 'var(--as-chat-width)' : 'var(--wb-config-width)' : rightOpen && !inspectorCompact ? 'var(--wb-right)' : '0px',
    '--wb-overlay-right-space': !panel && rightOpen && inspectorCompact ? '250px' : 'var(--wb-right-space)',
    '--wb-dock': dockOpen ? dockTab === 'photos' ? 'min(52vh, 560px)' : 'min(38vh, 360px)' : '92px', '--wb-kpi-height': `${kpiHeight}px`, '--wb-notice-height': history || session?.error || session?.transportError || session?.inputSource.error ? '58px' : '0px' } as CSSProperties;

  return <div className="wb" style={layout} data-testid="workbench" data-run={runId ?? ''} data-status={status ?? 'none'} data-mode={mode ?? ''} data-cut={session?.cut ?? ''}>
    {/* Stage: the one 3D world */}
    <div className="wb-stage" data-testid="wb-stage">
      <div className="wb-scene-frame" data-testid="wb-scene-frame" aria-hidden="true" />
      <Suspense fallback={<div className="wb-stage-empty"><div>{L('Preparing the 3D scene…', '正在准备三维场景…')}</div></div>}>
        {session && store && clock && spatial && <ViewportView key={runId} store={store} clock={clock} selected={selected} mode={camera} quality="med" trails commitCut={session.cut} highlights={traceModel?.involved}
          simple bare focus={focus} onViewport={handle => { viewport.current = handle; }}
          director={{ enabled: director, active: (live && session.followingLive) || clock.playing, paused: directorPaused, events: projected?.events ?? [] }}
          onDirectorPause={() => setDirectorPaused(true)} onDirectorSubject={key => { if (!directorPause.current) { setSelectionOrigin('director'); setSelected(key); store.select(key); } }}
          onCameraMode={value => { if (value !== 'cinematic') setCamera(value); }}
          onSelect={key => select(key)} onTick={tick} onError={problem => toast('error', String(problem))} onQuality={() => undefined}
          onChangeSource={() => { setRightOpen(false); update({ panel: 'space' }); }}
          onSeekCommit={(_, index) => session.seekCut(index)} />}
        {session && store && !spatial && <div className="wb-glass" style={{ position: 'absolute', inset: 'calc(var(--wb-top) + 36px) calc(var(--wb-right) + 36px) calc(var(--wb-dock) + 36px) calc(var(--wb-left) + 36px)', overflow: 'auto', padding: 12 }}>
          <p className="hint">{L('This world has no spatial objects; it is shown as relations and state.', '这个世界没有空间对象，以关系和状态的形式展示。')}</p>
          <NonspatialViews store={store} selected={selected} onSelect={key => select(key)} view="graph" /></div>}
        {!session && (project || assistant.previewWorkspace) && <><ScenePreview bare workspace={assistant.previewWorkspace ?? (previewDraft?.id === project!.id ? { ...project!, scenario: previewDraft.scenario } : project!)} api={apiBase} layers={['ground', 'buildings', 'roads', 'entities', 'airspace']} overlay={assistant.overlay} />
          {assistant.previewWorkspace && <><div className="as-stage-veil" aria-hidden /><div className="as-stage-tag" role="status" data-testid="as-stage-tag"><i />{assistant.viewing ? L(`Earlier plan v${assistant.viewing} · preview`, `早期方案 v${assistant.viewing} · 预览`) : L(`Proposed plan v${assistant.assistant.plan?.version ?? ''} · preview`, `提议方案 v${assistant.assistant.plan?.version ?? ''} · 预览`)}<em>{L('not applied', '未应用')}</em></div></>}
          {!assistant.previewWorkspace && project && <div className="wb-stage-note">{runId ? L('Connecting to the run…', '正在连接运行…') : assistant.stage === 'configured' && assistantOpen ? L(`Configured · version ${project.latest_version?.version ?? ''} · not running — Initialize to see the world at t = 0`, `已配置 · 版本 ${project.latest_version?.version ?? ''} · 未运行 — 初始化后查看 t = 0 时的世界`) : previewDraft?.id === project.id && previewDraft.dirty ? L('Unsaved draft preview · save a version to run these edits', '未保存草稿预览 · 保存版本后才能运行这些修改') : project.latest_version ? L('Draft preview · Initialize to see the authoritative world at t = 0', '草稿预览 · 初始化后可查看 t = 0 时的权威世界') : L('Draft preview · validate and save a version, then initialize', '草稿预览 · 校验并保存版本后再初始化')}</div>}</>}
      </Suspense>
      {!project && !session && !showGallery && assistantOpen && !assistant.previewWorkspace && <StageEmpty title={L('Your scene will appear here', '你的场景会显示在这里')} body={L('Describe a situation in the conversation. The proposed scene is previewed here before anything is created.', '在对话中描述一个情景。创建任何内容之前，提议的场景会先在这里预览。')} />}
      {!project && !session && !showGallery && !assistantOpen && <div className="wb-stage-empty"><div><h2>{projectError ? L('Project unavailable', '项目不可用') : projectId || runId ? L('Loading project…', '正在加载项目…') : L('Run records', '运行记录')}</h2>{projectError ? <p>{projectError}</p> : !projectId && !runId && <p>{L('Open a run from the list, or choose a scenario to create a project.', '从列表打开运行，或选择场景新建项目。')}</p>}</div></div>}

    </div>

    {/* Command bar */}
    <header className="wb-top wb-night" data-testid="wb-top">
      <div className="wb-identity">
      <a className="wb-brand" href="/" onClick={event => { event.preventDefault(); update({ gallery: '1' }); }}><span className="wb-brand-mark" />AeroAgentSim</a>
      <span className="wb-sep" />
      <div className="wb-project">
        <button onClick={() => update({ gallery: '1' })} title={title ?? L('Switch scenario or project', '切换场景或项目')} data-testid="wb-project-switch"><span>{title ?? L('Choose a scenario', '选择场景')}</span> <span aria-hidden>▾</span></button>
        <small>{project?.template ? `${localized(template, 'title', locale) ?? project.template.id}` : project?.derived_from ? L(`derived from ${project.derived_from.run_id}`, `派生自 ${project.derived_from.run_id}`) : project ? L('Custom project', '自定义项目') : ''}</small>
      </div>
      {project && <span className={`wb-chip ${project.dirty ? 'is-dirty' : 'is-version'}`} title={project.latest_version?.digest}>{project.latest_version ? `v${project.latest_version.version}` : L('no version', '无版本')}{project.dirty ? L(' · draft edited', ' · 草稿已修改') : ''}</span>}
      {record?.workspace_version !== undefined && <span className="wb-chip" title={record.workspace_digest}>{L('run of', '运行版本')} v{record.workspace_version}</span>}
      </div>
      <div className="wb-simulation">
      <span className={`wb-status s-${status ?? 'none'}`} data-testid="wb-status"><i />{status ? finalizing ? L('Finalizing results…', '结果汇总中…') : status === 'initialized' ? L('Initialized · t = 0', '已初始化 · t = 0') : runStatusLabel(status, locale) : L('No run', '无运行')}{mode === 'replay' && !finalizing && <span className="wb-tag" style={{ marginLeft: 6 }}>{L('replay', '回放')}</span>}</span>
      {session && !session.error && (mode === 'live' && status !== session.status || !session.journalComplete && (mode === 'replay' || TERMINAL.has(status ?? ''))) && <span className="wb-tag" role="status" data-testid="wb-catching-up">{L('Scene updating…', '场景更新中…')}</span>}
      {session?.pendingCut !== undefined && <span className="wb-tag" role="status" data-testid="wb-seeking">{L(`Loading moment #${session.pendingCut}…`, `正在加载时刻 #${session.pendingCut}…`)}</span>}
      <div className="wb-clock"><small>{mode === 'replay' ? L('Replay time', '回放时间') : L('Simulation time', '仿真时间')}</small><time className="wb-mono">{session ? displayTime(runClockTime(mode ?? 'replay', { ...session, waiting, reportedBoundaryNs: metadataBoundary && metadataBoundary.runId === runId && (!session.reportedBoundaryNs || BigInt(metadataBoundary.ns) > BigInt(session.reportedBoundaryNs)) ? metadataBoundary.ns : session.reportedBoundaryNs }), session.store.header.start.ns) : '--:--.-'}</time></div>
      <div className="wb-controls" aria-label={L('Simulation control', '仿真控制')}>
        {(!runId || TERMINAL.has(status ?? '') || mode === 'replay') && project && <button className="wb-btn primary" data-testid="wb-init" disabled={!project.latest_version || initializing} title={project.latest_version ? L('Create a run of the latest saved version, held at t = 0', '以最新保存的版本创建运行，停在 t = 0') : L('Validate and save a version first', '请先校验并保存版本')} onClick={() => void initialize()}>{initializing ? L('Initializing…', '初始化中…') : project.latest_version ? L(`Initialize v${project.latest_version.version}`, `初始化 v${project.latest_version.version}`) : L('Initialize', '初始化')}</button>}
        {mode === 'live' && status === 'initialized' && <button className="wb-btn go" data-testid="wb-start" disabled={!!pending} onClick={() => void control('start')}>▶ {pending === 'start' ? L('Starting…', '启动中…') : L('Start', '启动')}</button>}
        {mode === 'live' && ['running', 'waiting_for_input'].includes(status ?? '') && <button className="wb-btn" data-testid="wb-pause" disabled={!!pending} onClick={() => void control('pause')}>❚❚ {pending === 'pause' ? L('Pausing…', '暂停中…') : L('Pause', '暂停')}</button>}
        {mode === 'live' && status === 'paused' && <button className="wb-btn go" data-testid="wb-resume" disabled={!!pending} onClick={() => void control('resume')}>▶ {pending === 'resume' ? L('Resuming…', '恢复中…') : L('Resume', '恢复')}</button>}
        {live && speed !== undefined && <select className="wb-btn" disabled={speedPending} aria-label={L('Simulation speed', '仿真速度')} title={L('Simulation speed: how fast simulated time advances against the wall clock. Results do not change.', '仿真速度：仿真时间相对墙钟的推进速度，不影响结果。')} data-testid="wb-speed"
          value={speed === null ? 'max' : String(speed)} onChange={event => void changeSpeed(event.target.value === 'max' ? null : Number(event.target.value))}>
          {[...new Set([0.5, 1, 2, 5, 10, 50, 100, ...(speed ? [speed] : [])])].sort((a, b) => a - b).map(value => <option key={value} value={String(value)}>{value}×</option>)}
          <option value="max">{L('Max', '最快')}</option>
        </select>}
        {live && <button className="wb-btn danger" data-testid="wb-stop" disabled={!!pending} onClick={() => { if (confirm(L('Stop this run? It cannot be resumed; its record is kept.', '停止此次运行？停止后无法继续，记录会保留。'))) void control('stop'); }}>■ {L('Stop', '停止')}</button>}
      </div>
      </div>
      <div className="wb-tools">
      <button className="wb-btn ghost" onClick={() => setPalette(true)} title={L('Search objects and actions', '搜索对象与操作')}>⌕ <span className="wb-kbd">⌘K</span></button>
      {session && <button className="wb-btn" aria-pressed={!!graph} onClick={() => update({ graph: graph ? undefined : '1' })} data-testid="wb-graph-toggle">{L('Graph', '关系图')} <span className="wb-kbd">G</span></button>}
      <button className={`wb-btn as-top-btn${assistantOpen ? ' is-on' : ''}`} aria-pressed={assistantOpen} onClick={toggleAssistant} data-testid="wb-assistant-toggle" title={L('Build or change the scene by describing it', '用描述来搭建或修改场景')}><NeuralGlyph state={assistantOpen && assistant.assistant.busy ? 'thinking' : 'idle'} size={16} title="" />{L('Assistant', '助手')} <span className="wb-kbd">A</span></button>
      {project && <button className="wb-btn" aria-pressed={!!panel && !assistantOpen} onClick={openForm} data-testid="wb-configure">{L('Configure', '配置')} <span className="wb-kbd">C</span></button>}
      <button className="wb-btn" aria-pressed={!!query.get('records')} onClick={() => update({ records: query.get('records') ? undefined : '1' })} data-testid="wb-records-toggle">{L('Records', '记录')} <span className="wb-kbd">R</span></button>
      <button className="wb-btn ghost" onClick={() => setLocale(zh ? 'en-US' : 'zh-CN')} aria-label={L('Switch to Chinese', '切换到英文')}>{zh ? 'EN' : '中文'}</button>
      </div>
    </header>

    {/* KPI cards */}
    {session && kpiIds.length > 0 && <div ref={kpiPanel} className="wb-kpis" data-testid="wb-kpis">
      {kpiIds.slice(0, 5).map(id => { const kpi = kpis[id], label = metricLabel(id, id, locale, kpi?.label_zh); return <div key={id} className="wb-kpi wb-glass" data-final={finalizing ? 'aggregating' : 'yes'} title={[label, metricHelp(id, locale), kpi?.value == null ? L('Not recorded at this moment', '此时刻尚无记录') : undefined, finalizing ? L('Results are still being aggregated; this value is not final yet.', '结果仍在汇总中，此数值尚非最终值。') : undefined, id].filter(Boolean).join(' · ')}><small>{label}</small><strong className="wb-mono">{kpi?.value == null ? '—' : kpi.unit === 'ratio' ? Math.round(kpi.value * 100) : Number.isInteger(kpi.value) ? kpi.value : kpi.value.toFixed(kpi.value < 10 ? 2 : 1)}</strong><em>{kpi?.unit === 'count' ? '' : kpi?.unit === 'ratio' ? '%' : kpi?.unit}</em></div>; })}
    </div>}

    {earlier && <div className="wb-history" role="status" data-testid="wb-history">◷ {L('Viewing an earlier moment', '正在查看较早时刻')} · <span className="wb-mono">{store && clock ? displayTime(clock.ns, store.header.start.ns) : ''}</span><button className="wb-btn" onClick={session.backToLive} data-testid="wb-back-live">{L('Jump to the end', '跳到结束时刻')} <span className="wb-kbd">L</span></button></div>}
    {history && <div className="wb-history" role="status" data-testid="wb-history">◷ {L('Viewing history', '正在查看历史时刻')} · <span className="wb-mono">{store && clock ? displayTime(clock.ns, store.header.start.ns) : ''}</span><button className="wb-btn" onClick={session.backToLive} data-testid="wb-back-live">{L('Back to live', '返回实时')} <span className="wb-kbd">L</span></button></div>}
    {session && (status === 'input_timeout' || canOperate && status === 'waiting_for_input') && <div className="wb-waiting wb-glass" data-testid="wb-waiting"><WaitingBanner waiting={waiting} terminal={status === 'input_timeout'} onInject={() => { session.backToLive(); update({ panel: undefined }); setRightOpen(true); setOperateRequest(value => value + 1); }} /></div>}
    {finalizing && <div className="wb-history wb-finalizing" role="status" data-testid="wb-finalizing" style={{ background: 'rgba(31,122,224,.92)' }}>
      <span aria-hidden>◌</span> {L('Finalizing results… KPIs and story are still being aggregated from the final journal.', '结果汇总中…指标与经历仍在从最终日志聚合。')}
    </div>}
    {session?.error && <div className="wb-history" role="alert" style={{ background: 'rgba(190,40,30,.92)' }}>{session.error}</div>}
    {session?.transportError && !session.error && <div className="wb-history" role="status" style={{ background: 'rgba(160,110,0,.92)' }}>{L('Reconnecting to the journal stream…', '正在重新连接日志流…')} {session.transportError}</div>}

    {session?.inputSource.error && <div className="wb-history" role="alert" data-testid="wb-input-error">{L('Operator input source unavailable', '操作员输入源暂不可用')} · {session.inputSource.error}</div>}

    {/* Left: objects or records */}
    {query.get('records') ? <RecordsPanel api={api} projectId={project?.id} current={runId} refreshKey={recordsKey}
        onOpen={run => update({ run: run.id, mode: TERMINAL.has(run.status) ? 'replay' : 'live', cut: undefined, sel: undefined, records: undefined }, true, run.studio_workspace ? `/w/${encodeURIComponent(run.studio_workspace)}` : location.pathname)}
        onCompare={ids => update({ compare: ids.join(',') })} onDerive={derive} onClose={() => update({ records: undefined })} />
      : boardOpen ? <Contained resetKey={`${assistant.assistant.plan?.version}-${assistant.viewing}`} fallback={retry => <aside className="as-board wb-panel wb-glass" data-testid="as-board-failed" role="alert"><p className="as-quiet">{L('This plan version could not be shown. The conversation and project are unaffected.', '无法显示此方案版本，对话和项目不受影响。')}</p><button className="wb-btn" onClick={retry}>{L('Try again', '重试')}</button></aside>}><PlanBoard api={assistant.api} assistant={assistant.assistant} stage={assistant.stage} waiting={status === 'initialized'} runId={runId} story={assistant.story} storyError={assistant.storyError}
          startNs={store?.header.start.ns} terminal={TERMINAL.has(status ?? '')} initializing={initializing} canInitialize={!!project?.latest_version} onInitialize={() => void initialize()}
          onApplied={record => void applied(record)} onHover={assistant.setFocus} onStep={assistant.setFocus} onContract={type => update({ contract: 'assistant', type, workspace: project?.id })}
          onSeek={cut => session?.seekCut(cut)} viewing={assistant.viewing} onViewVersion={assistant.setViewing} onClose={assistant.closeBoard} /></Contained>
      : session && store && (leftOpen ? <ObjectsPanel store={store} revision={session.revision} selected={selected} selectionOrigin={selectionOrigin} onSelect={(key, fly) => select(key, fly)} onClose={() => setLeftOpen(false)} />
        : <button className="wb-collapsed-tab left wb-glass" onClick={() => setLeftOpen(true)}>☰ {L('Objects', '对象')}</button>)}
    {assistantOpen && !boardOpen && assistant.assistant.plan && !query.get('records') && !(session && store && leftOpen) && <button className="wb-collapsed-tab left wb-glass as-board-tab" onClick={assistant.openBoard} data-testid="as-board-reopen">◇ {L(`Plan v${assistant.assistant.plan.version}`, `方案 v${assistant.assistant.plan.version}`)}</button>}

    {/* Right: configuration or inspector */}
    {(assistantOpen || assistant.morph?.from === 'chat') && <AssistantPanel assistant={assistant.assistant} connection={assistant.connection.connection} connectionError={assistant.connection.error ?? assistant.error} connectionSaving={assistant.connection.saving}
        onConnect={assistant.connection.save} onTest={assistant.connection.test} project={project ? { id: project.id, name: title ?? project.name } : undefined}
        selected={selected ? { id: selected.id, label: entityNames.get(selected.id) ?? selected.id } : undefined} runId={runId} onClearSelected={() => select(undefined)}
        onClose={() => update({ panel: undefined })} onSwitch={project ? (mode, origin) => assistant.switchMode(mode, origin) : undefined} switching={!!assistant.morph} morph={morphOf('chat')}
        onShowPlan={version => { assistant.openBoard(); assistant.setViewing(version === assistant.assistant.plan?.version ? undefined : version); }} onShowChanges={() => { assistant.openBoard(); assistant.setViewing(undefined); }} />}
    {project && panel && (!assistantOpen || assistant.morph?.from === 'form') ? <ConfigPanel key={project.id} api={api} project={project} onProject={setProject} onDraft={previewChanged} onInitialize={() => void initialize()} initializing={initializing}
        section={assistantOpen ? assistant.lastSection.current : panel} onSection={section => update({ panel: section })} onClose={() => update({ panel: undefined })}
        onInspectContract={type => update({ contract: panel, type, workspace: project.id })}
        activeRunVersion={record?.studio_workspace === project.id ? record.workspace_version : undefined}
        onSwitch={(mode, origin) => assistant.switchMode(mode, origin)} switching={!!assistant.morph} morph={morphOf('form')} />
      : !assistantOpen && session && store && runId && (rightOpen ? <InspectorPanel api={runsApi} runId={runId} store={store} revision={session.revision} selected={selected} cut={session.cut} mode={mode ?? 'replay'} canOperate={canOperate} viewingHistory={history} operateRequest={operateRequest} inputSource={session.inputSource} onCompactChange={setInspectorCompact}
          onSelect={key => select(key)} onSeekCut={session.seekCut} onTrace={ref => openTrace(ref)} following={following}
          onFocus={focusSelection} onFollow={() => { setDirectorPaused(true); setCamera(value => value === 'follow' ? 'orbit' : 'follow'); }}
          onShowGraph={() => update({ graph: '1' })} onClose={() => setRightOpen(false)} />
        : <button className="wb-collapsed-tab right wb-glass" onClick={() => setRightOpen(true)}>{L('Inspector', '检查器')} ☰</button>)}

    {/* Camera pad: display only */}
    {session && spatial && <div className="wb-camera wb-night" aria-label={L('Camera', '相机')} data-testid="wb-camera">
      <button className="wb-btn" title={L('Zoom in', '放大')} onClick={() => { setDirectorPaused(true); setCamera('orbit'); viewport.current?.zoom(0.7); }}>＋</button>
      <button className="wb-btn" title={L('Zoom out', '缩小')} onClick={() => { setDirectorPaused(true); setCamera('orbit'); viewport.current?.zoom(1.4); }}>－</button>
      <button className="wb-btn" title={L('Rotate 45°', '旋转 45°')} onClick={() => { setDirectorPaused(true); setCamera('orbit'); viewport.current?.orbitBy(Math.PI / 4); }}>⟳</button>
      <button className="wb-btn" title={L('Reset view', '重置视角')} onClick={() => { setDirectorPaused(true); setCamera('orbit'); viewport.current?.resetView(); }}>⌂</button>
      <button className="wb-btn" title={L('Frame the whole region: city and objects together', '框选整个区域：城市与对象一起')} onClick={() => { setDirectorPaused(true); setCamera('orbit'); viewport.current?.fitRegion(); }}>⤢</button>
      <hr />
      <button className="wb-btn" aria-pressed={following} disabled={!selectedSpatial} title={L('Follow selection (F)', '跟随所选对象（F）')} onClick={() => { setDirectorPaused(true); setCamera(value => value === 'follow' ? 'orbit' : 'follow'); }}>◎</button>
      <button className="wb-btn" aria-pressed={director && !directorPaused} title={L('Event director: the camera visits what is happening', '事件导播：镜头自动跟随正在发生的事件')} onClick={() => { if (director && !directorPaused) setDirector(false); else { setDirector(true); setDirectorPaused(false); } }}>🎬</button>
    </div>}

    {/* Fit whole region is also offered from the camera pad title; the dock-level
        affordance keeps GEO-014 discoverable without opening the pad. */}

    {/* Floating relation graph: summoned, expandable, selection flies the camera */}
    {session && store && graph && <section ref={graphWindow} className={`wb-graph wb-glass${graph === '2' ? ' is-expanded' : ''}`} style={graph !== '2' && graphPosition ? { left: graphPosition.x, top: graphPosition.y } : undefined} aria-label={L('Relation graph', '运行关系图')} data-testid="wb-graph">
      <div className="wb-panel-head wb-graph-handle" title={L('Drag to move the graph window', '拖动可移动关系图窗口')}
        onPointerDown={event => { if (graph === '2' || (event.target as HTMLElement).closest('button')) return; const rect = event.currentTarget.parentElement!.getBoundingClientRect(); graphDrag.current = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={event => { const drag = graphDrag.current; if (!drag) return; const rect = event.currentTarget.parentElement!.getBoundingClientRect(); setGraphPosition(fitGraphPosition(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y, rect)); }}
        onPointerUp={() => { graphDrag.current = undefined; }} onPointerCancel={() => { graphDrag.current = undefined; }}><h3>{L('Relation graph', '运行关系图')}</h3>
        <button className="wb-x" title={graph === '2' ? L('Shrink', '缩小') : L('Expand', '展开')} onClick={() => update({ graph: graph === '2' ? '1' : '2' })}>{graph === '2' ? '⤡' : '⤢'}</button>
        <button className="wb-x" aria-label={L('Close graph', '关闭关系图')} onClick={() => update({ graph: undefined })}>✕</button></div>
      <div className="wb-graph-body"><Suspense fallback={<div className="wb-loading" role="status" data-testid="wb-graph-loading">{L('Loading relation graph…', '正在加载关系图…')}</div>}><BehaviourRunGraph store={store} selected={selected} onSelect={key => select(key, true)} onSeek={session.seekCut} highlights={traceModel?.involved} /></Suspense></div>
    </section>}

    {/* Bottom dock */}
    {session && <Dock session={session} events={projected?.events ?? []} kpiIds={kpiIds} kpis={kpis} series={series} entityNames={entityNames} selected={selected}
      tab={dockTab} onTab={setDockTab} open={dockOpen} onToggle={() => setDockOpen(value => !value)} onEvent={selectEvent} untilNs={record?.until_ns} finished={TERMINAL.has(status ?? '')}
      photos={runId && store ? <Suspense fallback={null}><ArtifactsPanel api={runsApi} runId={runId} store={store} commitCut={session.cut} live={mode === 'live' && !TERMINAL.has(status ?? '')} onSeek={session.seekCut} onInspect={() => setDirectorPaused(true)} /></Suspense> : undefined}
      why={traceRef && store ? <CauseTracePanel store={store} model={traceModel} canGoBack={traceStack.length > 1} onOpen={ref => openTrace(ref, false)} onBack={() => setTraceStack(stack => stack.slice(0, -1))} onClose={() => { setTraceStack([]); setDockTab('events'); }} onSelectEntity={key => select(key, true)} onSeek={session.seekCut} /> : undefined} />}
    {!session && project && !panel && <div className="wb-dock wb-glass" style={{ height: 'auto' }}><div className="wb-dock-bar">
      <span className="wb-label">{L('Next step', '下一步')}</span>
      <span className="wb-ticker">{!project.latest_version ? L('Open Configure, change what you need, validate and save a version.', '打开“配置”，修改需要的内容，校验并保存版本。') : project.dirty ? L(`The draft has edits beyond version ${project.latest_version.version}. Save a new version to run them, or initialize v${project.latest_version.version} as it is.`, `草稿在版本 ${project.latest_version.version} 之后还有修改。保存新版本才能运行这些修改，或直接初始化 v${project.latest_version.version}。`) : L(`Version ${project.latest_version.version} is ready. Initialize it to see the world at t = 0, then press Start.`, `版本 ${project.latest_version.version} 已就绪。初始化后查看 t = 0 时的世界，再按“启动”。`)}</span>
      <button className="wb-btn" onClick={() => update({ panel: 'controls' })}>{L('Configure', '配置')}</button>
    </div></div>}

    {/* Overlays */}
    {showGallery && <Gallery api={api} current={project?.id} onDescribe={() => update({ gallery: undefined, run: undefined, mode: undefined, cut: undefined, sel: undefined, panel: 'assistant', assistant: undefined, records: undefined }, true, '/')} initialGuide={query.get('guide') ?? undefined} onOpenProject={id => update({ gallery: undefined, run: undefined, mode: undefined, cut: undefined, sel: undefined, panel: undefined }, true, `/w/${encodeURIComponent(id)}`)} onCreate={createFromTemplate} onCreateBlank={createBlankProject} onClose={projectId ? () => update({ gallery: undefined }) : undefined} />}
    {query.get('compare') && <div className="wb-overlay" onClick={event => { if (event.target === event.currentTarget) update({ compare: undefined }); }}><div className="wb-overlay-card wb-glass"><Suspense fallback={null}><CompareRuns api={templateApi} ids={query.get('compare')!.split(',')} onBack={() => update({ compare: undefined })} /></Suspense></div></div>}
    {contractType !== undefined && <div className="wb-overlay" onClick={event => { if (event.target === event.currentTarget) update({ contract: undefined, type: undefined, workspace: undefined, panel: query.get('contract') ?? panel }); }}><div className="wb-overlay-card wb-glass">
      <div className="wb-hero"><div style={{ flex: 1 }}><h1>{L('Type contract', '类型契约')}</h1><p className="wb-mono">{contractType?.split(',')[0] || L('AeroGraph types, fields, relations and events', 'AeroGraph 类型、字段、关系与事件')}</p></div><button className="wb-btn primary" onClick={() => update({ contract: undefined, type: undefined, workspace: undefined, panel: query.get('contract') ?? panel })}>← {L('Back to the field you were editing', '返回正在编辑的字段')}</button></div>
      <Suspense fallback={null}><AeroGraphPage /></Suspense></div></div>}
    {palette && <Palette onClose={() => setPalette(false)} items={[
      ...(store ? [...store.entities.values()].map(entity => ({ id: `e:${entityId(entity.key)}`, label: displayName(entity, store.header, locale), hint: entity.typeId, run: () => select(entity.key, true) })) : []),
      ...(project ? [{ id: 'a:config', label: L('Configure scenario', '配置场景'), hint: 'C', run: () => update({ panel: 'controls' }) }] : []),
      { id: 'a:records', label: L('Run records', '运行记录'), hint: 'R', run: () => update({ records: '1' }) },
      { id: 'a:gallery', label: L('Scenarios and projects', '场景与项目'), hint: '', run: () => update({ gallery: '1' }) },
      ...(session ? [{ id: 'a:graph', label: L('Relation graph', '运行关系图'), hint: 'G', run: () => update({ graph: '1' }) }, { id: 'a:live', label: L('Back to live', '返回实时'), hint: 'L', run: () => session.backToLive() }] : []),
    ]} placeholder={L('Search objects, projects and actions…', '搜索对象、项目和操作…')} />}

    <div className="wb-toasts" aria-live="polite">
      {recordsError && <div className="wb-toast wb-glass error" role="alert">{L('Run records unavailable', '运行记录暂不可用')} · {recordsError}</div>}
      {kpisError && <div className="wb-toast wb-glass error" role="alert">{L('KPI updates unavailable', '指标更新暂不可用')} · {kpisError}</div>}
      {toasts.map(item => <div key={item.id} className={`wb-toast wb-glass ${item.kind}`}>{item.text}</div>)}
    </div>
  </div>;
}

function Palette({ items, onClose, placeholder }: { items: Array<{ id: string; label: string; hint: string; run: () => void }>; onClose: () => void; placeholder: string }) {
  const [query, setQuery] = useState(''), [index, setIndex] = useState(0);
  const shown = items.filter(item => `${item.label} ${item.hint}`.toLowerCase().includes(query.toLowerCase())).slice(0, 60);
  const choose = (item?: { run: () => void }) => { if (item) { item.run(); onClose(); } };
  return <div className="wb-overlay" style={{ alignItems: 'flex-start' }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="wb-palette wb-glass" role="dialog" aria-label={placeholder}>
      <input autoFocus value={query} placeholder={placeholder} onChange={event => { setQuery(event.target.value); setIndex(0); }}
        onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setIndex(value => Math.min(shown.length - 1, value + 1)); } else if (event.key === 'ArrowUp') { event.preventDefault(); setIndex(value => Math.max(0, value - 1)); } else if (event.key === 'Enter') choose(shown[index]); else if (event.key === 'Escape') onClose(); }} />
      <ul>{shown.map((item, position) => <li key={item.id}><button aria-selected={position === index} onMouseEnter={() => setIndex(position)} onClick={() => choose(item)}><span style={{ flex: 1 }}>{item.label}</span><span className="wb-tag wb-mono">{item.hint}</span></button></li>)}</ul>
    </div>
  </div>;
}

export { sameEntity };
