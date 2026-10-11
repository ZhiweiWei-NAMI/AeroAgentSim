import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Drawer, Select } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import type { EntityKey } from '../contracts/viewer-feed';
import { awaitRunConfiguration, HttpViewerFeed, RunsApi, type RunInfo, type WaitingContext } from '../feeds/http';
import { TemporalFeedStore as FeedStore } from '../feeds/temporal-store';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { PlaybackClock } from '../viewport/clock';
import { DualRunViews, type RunViewLayout } from '../behaviours/DualRunViews';
import { RunExplorer, RunRecordInspector, type ExplorerSelection } from '../console/RunExplorer';
import { displayTime } from './display-time';
import './run-workbench.css';
import { RunOperations } from '../behaviours/RunOperations';
import { ArtifactsPanel } from '../observations/ArtifactsPanel';
const CaptureConsole = lazy(() => import('../observations/CaptureConsole').then(module => ({ default: module.CaptureConsole })));
import { InspectionPanel } from './InspectionPanel';
import { CauseTracePanel } from '../trace/CauseTracePanel';
import { explain, type CauseRef } from '../trace/cause-trace';
import { subscribeCauseTrace } from '../trace/bus';
import { RunTimeline } from './RunTimeline';
import { readableLabel, entityLabel } from './inspection-format';
import { WaitingBanner } from './WaitingBanner';
import './inspect.css';
import { Details } from '../console/Details';
import { PageState } from '../console/PageState';
import { useConsoleNotice } from '../console/Notifications';
import { TemplateApi } from '../newcomers/api';
import { SimpleRunView } from '../newcomers/SimpleRunView';
import { RunsLibrary } from '../newcomers/RunsLibrary';
import type { RecordedBusinessEvent } from '../newcomers/projection';
import { entityId, resolveBinding } from '../viewport/bindings';
import '../viewport/viewer.css';

export default function RunsPage({route,interactive=true,inspectList=false}:{route?:{pathname:string;search:string};interactive?:boolean;inspectList?:boolean}) {
  const { t, locale } = useI18n();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const currentLocation = useLocation(), navigate = useNavigate();
  const location = route ?? currentLocation;
  const notify=useConsoleNotice();
  const query = new URLSearchParams(location.search);
  const apiBase = query.get('api') ?? window.location.origin;
  const api = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const templateApi=useMemo(()=>new TemplateApi(apiBase),[apiBase]);
  const id = location.pathname.match(/^\/(?:runs|inspect)\/([^/]+)$/)?.[1];
  const runId = id ? decodeURIComponent(id) : undefined;
  const mode = query.get('mode') === 'replay' || (query.get('mode') !== 'live' && location.pathname.startsWith('/inspect')) ? 'replay' : 'live';
  const requestedEntity = query.get('entity');
  const requestedGeneration = query.get('generation'), requestedEpoch = query.get('epoch');
  const requestedCut = query.get('cut');
  const [runs, setRuns] = useState<RunInfo[]>([]), [listLoaded,setListLoaded]=useState(false), [error, setError] = useState<string>();
  const [transportError, setTransportError] = useState<string>();
  const requestedView = query.get('view');
  const [expert,setExpert]=useState(requestedView !== 'simple');
  useEffect(() => { setExpert(requestedView !== 'simple'); }, [runId, location.pathname, requestedView]);
  const [operationsOpen,setOperationsOpen]=useState(false),[hasInjection,setHasInjection]=useState(false);
  const [layout, setLayout] = useState<RunViewLayout>('split'), [explorerOpen, setExplorerOpen] = useState(true), [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectedRecord, setInspectedRecord] = useState<ExplorerSelection>();
  const [pendingControl, setPendingControl] = useState<'pause'|'resume'|'stop'>();
  const controlLock = useRef(false);
  const [focus,setFocus]=useState<{position:[number,number,number];revision:number}>();
  const [session, setSession] = useState<{ store: FeedStore; clock: PlaybackClock }>();
  const [camera,setCamera]=useState<'orbit'|'follow'|'chase'>('orbit');
  const [selected, setSelected] = useState<EntityKey>(), [cut, setCut] = useState<number>();
  const cutRef = useRef<number>();
  const selectionInitialized = useRef(false);
  const follow = useRef(mode === 'live');
  const [status, setStatus] = useState('loading');
  const [waiting, setWaiting] = useState<WaitingContext>();
  const [, redraw] = useState(0);
  const tick = () => redraw(value => value + 1);
  const suffix = `?api=${encodeURIComponent(apiBase)}&mode=${mode}&view=${expert?'expert':'simple'}${query.get('template')?`&template=${encodeURIComponent(query.get('template')!)}`:''}`;
  const refresh = () => { void api.runs().then(rows=>{setRuns(rows);setListLoaded(true);setError(undefined);}).catch(error => setError(String(error))); };
  useEffect(() => {
    const abort = new AbortController();
    const load=()=>{void api.runs(abort.signal, !runId).then(rows=>{if(!abort.signal.aborted){setRuns(rows);setListLoaded(true);setError(undefined);}}).catch(error => { if (!abort.signal.aborted) setError(String(error)); });};
    load();
    const timer=!runId&&!query.has('compare')?setInterval(load,3000):undefined;
    return () => {abort.abort();if(timer!==undefined)clearInterval(timer);};
  }, [api,runId,location.search]);
  useEffect(() => {
    if (!runId) { setSession(undefined); return; }
    const abort = new AbortController();
    let paintFrame: number | undefined;
    setStatus('loading'); setError(undefined); setTransportError(undefined); setSession(undefined); setCut(undefined);cutRef.current=undefined; setSelected(undefined); selectionInitialized.current = false; setInspectedRecord(undefined); follow.current = mode === 'live'; setWaiting(undefined);
    let activeStore: FeedStore | undefined;
    const feed = new HttpViewerFeed(api, runId, mode, (value, context) => {
      setStatus(value); setWaiting(['waiting_for_input', 'input_timeout'].includes(value) ? context : undefined);
      if (activeStore) activeStore.liveFollow = mode === 'live' && follow.current && !['paused','completed','stopped','faulted','interrupted','input_timeout'].includes(value);
    }, setTransportError);
    void feed.header().then(async header => {
      if (abort.signal.aborted) return;
      if (header.epoch === undefined || header.kernelRunId === undefined) {
        try {
          const configuration = await awaitRunConfiguration(api, runId, abort.signal) as {service_run_id:string;kernel_run_id:string;epoch:string};
          if (configuration.service_run_id !== runId || typeof configuration.epoch !== 'string' || typeof configuration.kernel_run_id !== 'string' || header.epoch!==undefined&&header.epoch!==configuration.epoch) throw Error(t('Run configuration identity disagrees with feed'));
          header.epoch = configuration.epoch; header.kernelRunId = configuration.kernel_run_id;
        } catch (problem) { throw Error(t('Run identity unavailable: {error}', {error:String(problem)})); }
      }
      if (abort.signal.aborted) return;
      if(header.scene?.city){header.scene.city.url=new URL(header.scene.city.url,apiBase+'/').href;if(header.scene.city.assetsBase)header.scene.city.assetsBase=new URL(header.scene.city.assetsBase,apiBase+'/').href;}
      const store = new FeedStore(header), clock = new PlaybackClock(header.start.ns, header.end?.ns ?? header.start.ns);
      activeStore = store; store.liveFollow = mode === 'live';
      if (requestedEpoch && header.epoch !== requestedEpoch) throw Error(t('Requested selection epoch disagrees with the run header'));
      setSession({ store, clock });
      return feed.subscribe(0, commit => {
        store.ingest(commit);
        if (BigInt(commit.at.ns) > BigInt(clock.end)) clock.end = commit.at.ns;
        if (mode === 'live' && follow.current) clock.seek(clock.end);
        if (requestedCut && cutRef.current === undefined) {
          const index = Number(requestedCut);
          if (!Number.isSafeInteger(index) || index < 1) throw Error(t('Invalid requested journal cut'));
          if (commit.commitIndex === index) { follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(commit.at.ns); cutRef.current = index; setCut(index); }
        }
        // A transport chunk can contain hundreds of commits. Retain each one,
        // then project the shared cursor once before the next browser paint.
        if (paintFrame === undefined) paintFrame = requestAnimationFrame(() => {
          paintFrame = undefined;
          if (abort.signal.aborted) return;
          try { store.seek(clock.ns,cutRef.current); tick(); }
          catch (error) { setError(String(error)); abort.abort(); }
        });
      }, abort.signal);
    }).catch(error => { if (!abort.signal.aborted) setError(String(error)); });
    return () => { abort.abort(); if (paintFrame !== undefined) cancelAnimationFrame(paintFrame); };
  }, [api, runId, mode]);
  useEffect(() => {
    if (!selected && session && !selectionInitialized.current) {
      const entities = [...session.store.entities.values()];
      let entity = requestedEntity ? entities.find(entity => entity.key.id === requestedEntity && (!requestedGeneration || String(entity.key.generation) === requestedGeneration)) : entities.find(entity => resolveBinding(session.store.header, entity.typeId));
      if (!entity && !requestedEntity) entity = entities[0];
      if (entity) { selectionInitialized.current = true; setSelected(entity.key); if (session.store.header.epoch !== undefined) session.store.select(entity.key); }
    }
  });
  // Cause-to-effect walk: a stack of references so the user can step back.
  const [traceStack, setTraceStack] = useState<CauseRef[]>([]);
  const traceRef = traceStack[traceStack.length - 1];
  const traceCount = session?.store.commits.length ?? 0;
  const traceModel = useMemo(() => session && traceRef ? explain(session.store, traceRef,locale) : undefined, [session, traceRef, traceCount, locale]);
  useEffect(() => { setTraceStack([]); }, [runId]);
  const openTraceRef = useRef<(ref: CauseRef, reset?: boolean) => void>(() => {});
  useEffect(() => subscribeCauseTrace(ref => openTraceRef.current(ref, true)), []);
  if (query.has('capture')) return <Suspense fallback={<PageState kind="loading" title={t('Loading console')} />}><CaptureConsole apiBase={apiBase} assetManifestUrl={query.get('capture_assets')} /></Suspense>;
  if (!runId) return <RunsLibrary runs={runs} loaded={listLoaded} error={error} base={apiBase} inspect={inspectList} onRefresh={refresh} compare={query.has('compare')?query.get('compare')!.split(','):undefined}/>;
  const store = session?.store, clock = session?.clock;
  const seek = (index: number) => {
    if (!store || !clock || !store.commits[index]) return;
    follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(store.commits[index].at.ns);cutRef.current=store.commits[index].commitIndex; setCut(store.commits[index].commitIndex);
    store.seek(clock.ns, store.commits[index].commitIndex); tick();
  };
  const openTrace = (ref: CauseRef, reset = false) => {
    if (!store) return;
    const model = explain(store, ref,locale);
    setExpert(true);
    setTraceStack(previous => reset ? [ref] : [...previous, ref]);
    if (model) {
      seekJournal(model.cut);
      const wanted = model.eventEntities ?? (ref.kind === 'event' ? ref.entities : undefined);
      const named = wanted?.length ? model.involved.find(key => key.id === wanted[0]) : undefined;
      const current = selected && model.involved.some(key => entityId(key) === entityId(selected));
      const focus = named ?? (current ? undefined : model.involved[0]);
      if (focus && reset) select(focus);
    }
  };
  openTraceRef.current = openTrace;
  const traceBack = () => {
    const previous = traceStack[traceStack.length - 2];
    setTraceStack(stack => stack.slice(0, -1));
    const model = store && previous ? explain(store, previous,locale) : undefined;
    if (model) seekJournal(model.cut);
  };
  const select = (key: EntityKey) => { selectionInitialized.current = true; setInspectedRecord(undefined); setSelected(key); if (store?.header.epoch !== undefined) store.select(key); };
  const clearSelection = () => { selectionInitialized.current = true; setSelected(undefined); setInspectedRecord(undefined); store?.select(undefined); setCamera('orbit'); };
  const seekJournal = (index: number) => { const offset = store?.commits.findIndex(commit => commit.commitIndex === index); if (offset === undefined || offset < 0) { setError(t('Journal cut {cut} is not loaded', {cut:index})); return; } seek(offset); };
  const play=()=>{if(!session)return;setCut(undefined);cutRef.current=undefined;follow.current=false;session.store.liveFollow=false;session.clock.playing?session.clock.pause():session.clock.play();tick();};
  const live=()=>{if(!session)return;follow.current=true;session.store.liveFollow=!['paused','completed','stopped','faulted','interrupted','input_timeout'].includes(status);setCut(undefined);cutRef.current=undefined;session.clock.seek(session.clock.end);session.store.seek(session.clock.ns);tick();};
  const inject=()=>setOperationsOpen(true);
  const selectEvent=(event:RecordedBusinessEvent)=>{seek(event.offset);const entities=event.entities.flatMap(id=>[...store!.entities.values()].filter(row=>row.key.id===id));const entity=entities.find(row=>resolveBinding(store!.header,row.typeId))??entities[0];if(entity){select(entity.key);setCamera(resolveBinding(store!.header,entity.typeId)?'follow':'orbit');}if(event.position&&(!entity||!resolveBinding(store!.header,entity.typeId))){setCamera('orbit');setFocus(previous=>({position:event.position!,revision:(previous?.revision??0)+1}));}};
  const runInfo = runs.find(run => run.id === runId);
  const canOperate = interactive && mode === 'live' && ['running','waiting_for_input','paused'].includes(status);
  const control = async (action: 'pause'|'resume'|'stop') => {
    if (controlLock.current || !canOperate) return;
    controlLock.current = true; setPendingControl(action);
    try {
      await api.control(runId, action);
      notify({kind:'success',message:action==='stop'?t('Stopping the run.'):action==='pause'?t('Pause requested.'):t('Resume requested.')});
    } catch(problem) { setError(String(problem)); notify({kind:'error',message:String(problem)}); }
    finally { controlLock.current = false; setPendingControl(undefined); }
  };
  const title = localized(runInfo, 'template_title', locale) ?? runInfo?.scenario ?? runId;
  return <div className={`viewer-demo inspection-page run-workbench${expert?'':' simple-view-page'}${expert&&traceRef?' has-trace':''}`}>
    <header className="run-commandbar">
      <div className="run-command-identity"><span className="run-command-eyebrow">{copy('SIMULATION / ', '仿真 / ')}{mode === 'live' ? copy('LIVE SESSION', '实时会话') : copy('RECORDED SESSION', '已记录会话')}</span><h1 title={title}>{title}</h1><div className="run-command-provenance"><code title={runId}>{runId}</code>{runInfo?.studio_workspace && <Link to={`/studio?workspace=${encodeURIComponent(runInfo.studio_workspace)}&api=${encodeURIComponent(apiBase)}`}>{runInfo.workspace_name ?? runInfo.studio_workspace}</Link>}{runInfo?.studio_revision !== undefined && <span>{copy('Revision', '修订')} {runInfo.studio_revision}</span>}</div></div>
      <div className="run-command-telemetry"><span className={`run-status-indicator status-${status}`}><i aria-hidden />{status === 'waiting_for_input' ? t('Waiting for your event') : readableLabel(status,locale)}</span><span className="run-sim-time"><small>{copy('Simulation time', '仿真时间')}</small><time>{store?.commits.at(-1) ? displayTime(store.commits.at(-1)!.at.ns, store.header.start.ns) : '—'}</time></span></div>
      <div className="run-command-actions" aria-label={copy('Simulation controls', '仿真控制')}><Button aria-label={status === 'paused' ? copy('Resume run', '继续运行') : copy('Pause run', '暂停运行')} aria-busy={pendingControl === 'pause' || pendingControl === 'resume'} disabled={!canOperate || !!pendingControl} loading={pendingControl === 'pause' || pendingControl === 'resume'} onClick={() => void control(status === 'paused' ? 'resume' : 'pause')}>{status === 'paused' ? copy('Resume run', '继续运行') : copy('Pause run', '暂停运行')}</Button><Button danger aria-label={copy('Stop run', '停止运行')} aria-busy={pendingControl === 'stop'} disabled={!canOperate || !!pendingControl} loading={pendingControl === 'stop'} onClick={() => void control('stop')}>{copy('Stop run', '停止运行')}</Button><Button type="primary" aria-label={copy('Inject event', '注入事件')} disabled={!hasInjection || !canOperate || !!pendingControl} onClick={inject}>{copy('Inject event', '注入事件')}</Button></div>
    </header>
    <div className="run-workspace-toolbar"><div className="run-view-tabs" role="group" aria-label={copy('Workspace view', '工作区视图')}><button aria-pressed={expert && layout === 'split'} onClick={() => {setExpert(true);setLayout('split');}}>{copy('Graph + 3D', '图 + 三维')}</button><button aria-pressed={expert && layout === 'graph'} onClick={() => {setExpert(true);setLayout('graph');}}>{copy('Graph', '图视图')}</button><button aria-pressed={expert && layout === 'spatial'} onClick={() => {setExpert(true);setLayout('spatial');}}>{copy('3D scene', '三维场景')}</button><button data-testid="expert-toggle" aria-pressed={!expert} onClick={() => setExpert(value => !value)}>{copy('Overview', '运行概览')}</button></div>
      <div className="run-workspace-tools">{expert && <><button aria-pressed={explorerOpen} onClick={() => setExplorerOpen(value => !value)}>{copy('Explorer', '资源浏览')}</button><button aria-pressed={inspectorOpen} onClick={() => setInspectorOpen(value => !value)}>{copy('Inspector', '检查器')}</button></>}<Select size="small" aria-label={t('Feed mode')} value={mode} options={[{ value: 'live', label: t('Live') }, { value: 'replay', label: t('Replay') }]} onChange={value => navigate(`/${location.pathname.startsWith('/inspect')?'inspect':'runs'}/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${value}&view=${expert?'expert':'simple'}${query.get('template')?`&template=${encodeURIComponent(query.get('template')!)}`:''}`)} /><Details title={t('Run details')} buttonLabel={t('Details')}><pre>{JSON.stringify({runId,workspace:runInfo?.studio_workspace,revision:runInfo?.studio_revision,configurationDigest:runInfo?.configuration_digest,header:store?.header,cursor:store?.viewCursor,selection:store?.selection},null,2)}</pre></Details></div>
    </div>
    {error && <Alert type="error" message={error} />}
    {transportError && <Alert type="warning" message={transportError} />}
    {session && <WaitingBanner waiting={waiting} terminal={status === 'input_timeout'} injectionHref="#run-operations" onInject={inject} />}
    {!session&&!error&&<PageState kind="loading" title={t('Connecting to the run')} description={t('Loading its recorded entities and timeline.')} />}
    {session&&!expert&&<SimpleRunView interactive={interactive} api={templateApi} runId={runId} store={session.store} clock={session.clock} status={status} mode={mode} selected={selected} cut={cut} camera={camera} focus={focus} onSelect={select} onEvent={selectEvent} onTick={tick} onError={error=>setError(String(error))} onSeek={seek} onPlay={play} onLive={live} onControl={control} onInject={inject} hasInjection={hasInjection} followingLive={follow.current} untilNs={runs.find(run=>run.id===runId)?.until_ns} onExpert={()=>setExpert(true)}/> }
    {session && expert&& <><main className="viewer-main run-workspace" data-testid="inspect-views" data-city-binding={session.store.header.scene?.city?.kind ?? 'absent'}>
      {explorerOpen && <RunExplorer store={session.store} selected={selected} onSelect={select} onInspect={record => {setInspectedRecord(record);if(record)setInspectorOpen(true);}} />}
      <section className="viewer-stage">
        <nav aria-label={copy('Scene camera', '场景相机')}><button disabled={!selected} onClick={clearSelection}>{copy('Clear selection', '取消选中')}</button><span>{t('Camera')}</span>{(['orbit','follow','chase'] as const).map(preset => <button key={preset} aria-pressed={camera === preset} disabled={preset !== 'orbit' && (!selected || !resolveBinding(session.store.header, session.store.entities.get(entityId(selected))?.typeId ?? ''))} onClick={() => setCamera(preset)}>{preset === 'orbit' ? copy('Orbit', '环绕') : preset === 'follow' ? copy('Follow selection', '跟随选中对象') : copy('Chase', '追踪')}</button>)}<span className="run-shared-cursor">{copy('Shared timeline', '共享时间轴')}<code>{session.store.viewCursor?.knownAt !== undefined ? `#${session.store.viewCursor.knownAt}` : '—'}</code></span></nav>
        <DualRunViews store={session.store} clock={session.clock} selected={selected} mode={camera} quality="med" trails={true} commitCut={cut} layout={layout}
          onSelect={select} onSeek={seekJournal} onTick={tick} onError={error => setError(String(error))} onQuality={() => {}} highlights={traceModel?.involved} />
      </section>
      {inspectorOpen && <aside className="viewer-sidebar inspection-sidebar run-context-inspector"><div className="run-pane-heading"><span>{copy('Inspector', '检查器')}</span><button aria-label={copy('Close inspector', '关闭检查器')} onClick={() => setInspectorOpen(false)}>×</button></div>
        {inspectedRecord ? <RunRecordInspector store={session.store} selection={inspectedRecord} onEntity={() => setInspectedRecord(undefined)} /> : <><Select aria-label={t('Entity')} allowClear onClear={clearSelection} showSearch optionFilterProp="label" className="inspection-entity-select" value={selected && entityId(selected)} options={[...session.store.entities].map(([id, entity]) => ({ value: id, label: entityLabel(entity,locale) }))} onChange={id => { const key = session.store.entities.get(id)?.key; if (key) select(key); }} /><InspectionPanel store={session.store} selected={selected} onSeek={seekJournal} onOpenTrace={(ref, cut) => { void cut; openTrace(ref, true); }} /></>}</aside>}
      </main>
      {traceRef && <CauseTracePanel store={session.store} model={traceModel} canGoBack={traceStack.length > 1} onOpen={ref => openTrace(ref)} onBack={traceBack} onClose={() => setTraceStack([])} onSelectEntity={select} onSeek={seekJournal} />}
      <div className="run-playback-label"><strong>{copy('PLAYBACK', '回放')}</strong><span>{copy('Review recorded state · simulation controls stay above', '查看已记录状态 · 仿真控制位于顶部')}</span><time>{displayTime(session.clock.ns, session.store.header.start.ns)}</time></div>
      <RunTimeline store={session.store} clock={session.clock} cut={cut} mode={mode} onSeek={seek}
        onPlay={play} onLive={live} onTick={tick} />
    </>}
    {session&&<>
      <div className="simple-runtime-panels run-secondary-actions"><Link to={`/agents/${encodeURIComponent(runId)}${suffix}${cut === undefined ? '' : `&cut=${cut}`}${selected ? `&entity=${encodeURIComponent(selected.id)}&generation=${selected.generation}` : ''}${store?.header.epoch ? `&epoch=${encodeURIComponent(store.header.epoch)}` : ''}`}>{t('Agent decisions')}</Link><Button onClick={()=>setOperationsOpen(true)}>{t('External events & controls')}</Button> <Details title={t('Photos')} buttonLabel={t('Photos')}><ArtifactsPanel api={api} runId={runId} store={session.store} commitCut={cut} onSeek={seekJournal}/></Details></div>
      <Drawer closable={{ 'aria-label': t('Close') }} title={t('External events & run controls')} aria-label={t('External events & run controls')} open={operationsOpen} onClose={()=>setOperationsOpen(false)} width={720} forceRender>
        <section id="run-operations"><RunOperations api={api} runId={runId} store={session.store} selected={selected} commitCut={cut} onSelect={select} onSeek={seekJournal} mode={mode} interactive={interactive} onInjections={setHasInjection} onRequestInject={()=>setOperationsOpen(true)}/></section>
      </Drawer>
    </>}
  </div>;
}
