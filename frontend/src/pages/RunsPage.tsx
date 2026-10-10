import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Drawer, Select, Space, Tag } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import type { EntityKey } from '../contracts/viewer-feed';
import { awaitRunConfiguration, HttpViewerFeed, RunsApi, type RunInfo, type WaitingContext } from '../feeds/http';
import { TemporalFeedStore as FeedStore } from '../feeds/temporal-store';
import { displayTime } from './display-time';
import { ConceptHelp } from '../console/ConceptHelp';
import { PlaybackClock } from '../viewport/clock';
import { DualRunViews } from '../behaviours/DualRunViews';
import { RunOperations } from '../behaviours/RunOperations';
import { ArtifactsPanel } from '../observations/ArtifactsPanel';
import { CaptureConsole } from '../observations/CaptureConsole';
import { InspectionPanel } from './InspectionPanel';
import { CauseTracePanel } from '../trace/CauseTracePanel';
import { explain, type CauseRef } from '../trace/cause-trace';
import { subscribeCauseTrace } from '../trace/bus';
import { RunTimeline } from './RunTimeline';
import { readableLabel, entityLabel } from './inspection-format';
import { WaitingBanner } from './WaitingBanner';
import './inspect.css';
import { Details } from '../console/Details';
import { PageHeader, PageState } from '../console/PageState';
import { useConsoleNotice } from '../console/Notifications';
import { TemplateApi } from '../newcomers/api';
import { SimpleRunView } from '../newcomers/SimpleRunView';
import { RunsLibrary } from '../newcomers/RunsLibrary';
import type { RecordedBusinessEvent } from '../newcomers/projection';
import { entityId, resolveBinding } from '../viewport/bindings';
import '../viewport/viewer.css';

export default function RunsPage({route,interactive=true,inspectList=false}:{route?:{pathname:string;search:string};interactive?:boolean;inspectList?:boolean}) {
  const currentLocation = useLocation(), navigate = useNavigate();
  const location = route ?? currentLocation;
  const notify=useConsoleNotice();
  const query = new URLSearchParams(location.search);
  const apiBase = query.get('api') ?? window.location.origin;
  const api = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const templateApi=useMemo(()=>new TemplateApi(apiBase),[apiBase]);
  const id = location.pathname.match(/^\/(?:runs|inspect)\/([^/]+)$/)?.[1];
  const runId = id ? decodeURIComponent(id) : undefined;
  const mode = query.get('mode') === 'live' ? 'live' : 'replay';
  const requestedEntity = query.get('entity');
  const requestedGeneration = query.get('generation'), requestedEpoch = query.get('epoch');
  const requestedCut = query.get('cut');
  const [runs, setRuns] = useState<RunInfo[]>([]), [listLoaded,setListLoaded]=useState(false), [error, setError] = useState<string>();
  const [expert,setExpert]=useState(query.get('view')==='expert');
  const [operationsOpen,setOperationsOpen]=useState(false),[hasInjection,setHasInjection]=useState(false);
  const [focus,setFocus]=useState<{position:[number,number,number];revision:number}>();
  const [session, setSession] = useState<{ store: FeedStore; clock: PlaybackClock }>();
  const [camera,setCamera]=useState<'orbit'|'follow'|'chase'>('orbit');
  const [selected, setSelected] = useState<EntityKey>(), [cut, setCut] = useState<number>();
  const cutRef = useRef<number>();
  const follow = useRef(mode === 'live');
  const [status, setStatus] = useState('loading');
  const [waiting, setWaiting] = useState<WaitingContext>();
  const [, redraw] = useState(0);
  const tick = () => redraw(value => value + 1);
  const suffix = `?api=${encodeURIComponent(apiBase)}&mode=${mode}&view=${expert?'expert':'simple'}${query.get('template')?`&template=${encodeURIComponent(query.get('template')!)}`:''}`;
  const refresh = () => { void api.runs().then(rows=>{setRuns(rows);setListLoaded(true);setError(undefined);}).catch(error => setError(String(error))); };
  useEffect(() => {
    const abort = new AbortController();
    const load=()=>{void api.runs(abort.signal).then(rows=>{if(!abort.signal.aborted){setRuns(rows);setListLoaded(true);setError(undefined);}}).catch(error => { if (!abort.signal.aborted) setError(String(error)); });};
    load();
    const timer=!runId&&!query.has('compare')?setInterval(load,3000):undefined;
    return () => {abort.abort();if(timer!==undefined)clearInterval(timer);};
  }, [api,runId,location.search]);
  useEffect(() => {
    if (!runId) { setSession(undefined); return; }
    const abort = new AbortController();
    setStatus('loading'); setError(undefined); setSession(undefined); setCut(undefined);cutRef.current=undefined; setSelected(undefined); follow.current = mode === 'live'; setWaiting(undefined);
    let activeStore: FeedStore | undefined;
    const feed = new HttpViewerFeed(api, runId, mode, (value, context) => {
      setStatus(value); setWaiting(['waiting_for_input', 'input_timeout'].includes(value) ? context : undefined);
      if (activeStore) activeStore.liveFollow = mode === 'live' && follow.current && !['paused','completed','stopped','faulted','interrupted','input_timeout'].includes(value);
    }, setError);
    void feed.header().then(async header => {
      if (abort.signal.aborted) return;
      if (header.epoch === undefined || header.kernelRunId === undefined) {
        try {
          const configuration = await awaitRunConfiguration(api, runId, abort.signal) as {service_run_id:string;kernel_run_id:string;epoch:string};
          if (configuration.service_run_id !== runId || typeof configuration.epoch !== 'string' || typeof configuration.kernel_run_id !== 'string' || header.epoch!==undefined&&header.epoch!==configuration.epoch) throw Error('Run configuration identity disagrees with feed');
          header.epoch = configuration.epoch; header.kernelRunId = configuration.kernel_run_id;
        } catch (problem) { throw Error(`Run identity unavailable: ${String(problem)}`); }
      }
      if (abort.signal.aborted) return;
      if(header.scene?.city){header.scene.city.url=new URL(header.scene.city.url,apiBase+'/').href;if(header.scene.city.assetsBase)header.scene.city.assetsBase=new URL(header.scene.city.assetsBase,apiBase+'/').href;}
      const store = new FeedStore(header), clock = new PlaybackClock(header.start.ns, header.end?.ns ?? header.start.ns);
      activeStore = store; store.liveFollow = mode === 'live';
      if (requestedEpoch && header.epoch !== requestedEpoch) throw Error('Requested selection epoch disagrees with the run header');
      setSession({ store, clock });
      return feed.subscribe(0, commit => {
        store.ingest(commit);
        if (BigInt(commit.at.ns) > BigInt(clock.end)) clock.end = commit.at.ns;
        if (mode === 'live' && follow.current) clock.seek(clock.end);
        if (requestedCut && cutRef.current === undefined) {
          const index = Number(requestedCut);
          if (!Number.isSafeInteger(index) || index < 1) throw Error('Invalid requested journal cut');
          if (commit.commitIndex === index) { follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(commit.at.ns); cutRef.current = index; setCut(index); }
        }
        store.seek(clock.ns,cutRef.current);
        tick();
      }, abort.signal);
    }).catch(error => { if (!abort.signal.aborted) setError(String(error)); });
    return () => abort.abort();
  }, [api, runId, mode]);
  useEffect(() => {
    if (!selected && session) {
      const entities = [...session.store.entities.values()];
      let entity = requestedEntity ? entities.find(entity => entity.key.id === requestedEntity && (!requestedGeneration || String(entity.key.generation) === requestedGeneration)) : entities.find(entity => resolveBinding(session.store.header, entity.typeId));
      if (!entity && !requestedEntity) entity = entities[0];
      if (entity) { setSelected(entity.key); if (session.store.header.epoch !== undefined) session.store.select(entity.key); }
    }
  });
  // Cause-to-effect walk: a stack of references so the user can step back.
  const [traceStack, setTraceStack] = useState<CauseRef[]>([]);
  const traceRef = traceStack[traceStack.length - 1];
  const traceCount = session?.store.commits.length ?? 0;
  const traceModel = useMemo(() => session && traceRef ? explain(session.store, traceRef) : undefined, [session, traceRef, traceCount]);
  useEffect(() => { setTraceStack([]); }, [runId]);
  const openTraceRef = useRef<(ref: CauseRef, reset?: boolean) => void>(() => {});
  useEffect(() => subscribeCauseTrace(ref => openTraceRef.current(ref, true)), []);
  if (query.has('capture')) return <CaptureConsole apiBase={apiBase} assetManifestUrl={query.get('capture_assets')} />;
  if (!runId) return <RunsLibrary runs={runs} loaded={listLoaded} error={error} base={apiBase} inspect={inspectList} onRefresh={refresh} compare={query.has('compare')?query.get('compare')!.split(','):undefined}/>;
  const store = session?.store, clock = session?.clock;
  const seek = (index: number) => {
    if (!store || !clock || !store.commits[index]) return;
    follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(store.commits[index].at.ns);cutRef.current=store.commits[index].commitIndex; setCut(store.commits[index].commitIndex);
    store.seek(clock.ns, store.commits[index].commitIndex); tick();
  };
  const openTrace = (ref: CauseRef, reset = false) => {
    if (!store) return;
    const model = explain(store, ref);
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
    const model = store && previous ? explain(store, previous) : undefined;
    if (model) seekJournal(model.cut);
  };
  const select = (key: EntityKey) => { setSelected(key); if (store?.header.epoch !== undefined) store.select(key); };
  const seekJournal = (index: number) => { const offset = store?.commits.findIndex(commit => commit.commitIndex === index); if (offset === undefined || offset < 0) { setError(`Journal cut ${index} is not loaded`); return; } seek(offset); };
  const play=()=>{if(!session)return;setCut(undefined);cutRef.current=undefined;follow.current=false;session.store.liveFollow=false;session.clock.playing?session.clock.pause():session.clock.play();tick();};
  const live=()=>{if(!session)return;follow.current=true;session.store.liveFollow=!['paused','completed','stopped','faulted','interrupted','input_timeout'].includes(status);setCut(undefined);cutRef.current=undefined;session.clock.seek(session.clock.end);session.store.seek(session.clock.ns);tick();};
  const inject=()=>setOperationsOpen(true);
  const selectEvent=(event:RecordedBusinessEvent)=>{seek(event.offset);const entities=event.entities.flatMap(id=>[...store!.entities.values()].filter(row=>row.key.id===id));const entity=entities.find(row=>resolveBinding(store!.header,row.typeId))??entities[0];if(entity){select(entity.key);setCamera(resolveBinding(store!.header,entity.typeId)?'follow':'orbit');}if(event.position&&(!entity||!resolveBinding(store!.header,entity.typeId))){setCamera('orbit');setFocus(previous=>({position:event.position!,revision:(previous?.revision??0)+1}));}};
  const control=async(action:'pause'|'resume'|'stop')=>{try{await api.control(runId,action);notify({kind:'success',message:action==='stop'?'Stopping the run.':action==='pause'?'Pause requested.':'Resume requested.'});}catch(problem){setError(String(problem));notify({kind:'error',message:String(problem)});}};
  return <div className={`viewer-demo inspection-page${expert?'':' simple-view-page'}${expert&&traceRef?' has-trace':''}`}>

    <header className="viewer-header"><div><h1>{runs.find(run=>run.id===runId)?.template_title??(location.pathname.startsWith('/inspect')?'Explore your run':'Watch your scenario')}</h1><span>{expert?'Graph, city and recorded state':'See what happens, then explore the results'}</span><ConceptHelp topic="Synchronized views" description="Graph and 3D views share entity identities, selection and the same recorded timeline moment." guide="console.md"/></div>
      <Space><Tag>{status==='waiting_for_input'?'Waiting for your event':readableLabel(status)}</Tag><Button data-testid="expert-toggle" aria-pressed={expert} onClick={()=>setExpert(value=>!value)}>{expert?'Simple view':'Expert view'}</Button><Select aria-label="Feed mode" value={mode} options={[{ value: 'live', label: 'Live' }, { value: 'replay', label: 'Replay' }]} onChange={value => navigate(`/${location.pathname.startsWith('/inspect')?'inspect':'runs'}/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${value}&view=${expert?'expert':'simple'}${query.get('template')?`&template=${encodeURIComponent(query.get('template')!)}`:''}`)} />
      {expert&&<Link to={`/agents/${encodeURIComponent(runId)}${suffix}${cut === undefined ? '' : `&cut=${cut}`}${selected ? `&entity=${encodeURIComponent(selected.id)}&generation=${selected.generation}` : ''}${store?.header.epoch ? `&epoch=${encodeURIComponent(store.header.epoch)}` : ''}`}>Agent decisions</Link>}
      <Button onClick={()=>setOperationsOpen(true)}>Run controls</Button><Details title="Run details"><pre>{JSON.stringify({runId,header:store?.header,cursor:store?.viewCursor,selection:store?.selection},null,2)}</pre></Details></Space></header>
    {error && <Alert type="error" message={error} />}
    {session && <WaitingBanner waiting={waiting} terminal={status === 'input_timeout'} injectionHref="#run-operations" onInject={inject} />}
    {!session&&!error&&<PageState kind="loading" title="Connecting to the run" description="Loading its recorded entities and timeline." />}
    {session&&!expert&&<SimpleRunView api={templateApi} runId={runId} store={session.store} clock={session.clock} status={status} mode={mode} selected={selected} cut={cut} camera={camera} focus={focus} onSelect={select} onEvent={selectEvent} onTick={tick} onError={error=>setError(String(error))} onSeek={seek} onPlay={play} onLive={live} onControl={control} onInject={inject} hasInjection={hasInjection} followingLive={follow.current} untilNs={runs.find(run=>run.id===runId)?.until_ns} onExpert={()=>setExpert(true)}/> }
    {session && expert&& <><main className="viewer-main" data-testid="inspect-views" data-city-binding={session.store.header.scene?.city?.kind ?? 'absent'}><section className="viewer-stage">
      <nav aria-label="Demo camera presets"><span>Camera</span><ConceptHelp topic="Camera presets" description="A preset selects the named actor and follows its recorded pose; the graph selection changes with it." guide="console.md"/>{[{label:'Reporter',id:'vehicle.reporter'}, {label:'Edge overview',id:'edge.coordinator'}, {label:'Alpha',id:'uav.alpha'}, {label:'Bravo',id:'uav.bravo'}].filter(preset=>[...session.store.entities.values()].some(row=>row.key.id===preset.id)).map(preset=><button key={preset.label} onClick={()=>{const actor=[...session.store.entities.values()].find(row=>row.key.id===preset.id);if(!actor){setError(`Preset actor ${preset.id} is absent at this cut`);return;}select(actor.key);setCamera(preset.label==='Edge overview'?'orbit':'follow');}}>{preset.label}</button>)}</nav>
      <DualRunViews store={session.store} clock={session.clock} selected={selected} mode={camera} quality="med" trails={true} commitCut={cut}
        onSelect={select} onSeek={seekJournal} onTick={tick} onError={error => setError(String(error))} onQuality={() => {}} highlights={traceModel?.involved} />
      </section>
      <aside className="viewer-sidebar inspection-sidebar"><Select aria-label="Entity" showSearch optionFilterProp="label" className="inspection-entity-select" value={selected && entityId(selected)} options={[...session.store.entities].map(([id, entity]) => ({ value: id, label: entityLabel(entity) }))} onChange={id => { const key = session.store.entities.get(id)?.key; if (key) select(key); }} />
        <InspectionPanel store={session.store} selected={selected} onSeek={seekJournal} onOpenTrace={(ref, cut) => { void cut; openTrace(ref, true); }} /></aside></main>
      {traceRef && <CauseTracePanel store={session.store} model={traceModel} canGoBack={traceStack.length > 1} onOpen={ref => openTrace(ref)} onBack={traceBack} onClose={() => setTraceStack([])} onSelectEntity={select} onSeek={seekJournal} />}
      <RunTimeline store={session.store} clock={session.clock} cut={cut} mode={mode} onSeek={seek}
        onPlay={() => { setCut(undefined);cutRef.current=undefined;follow.current=false;session.store.liveFollow=false;session.clock.playing?session.clock.pause():session.clock.play();tick(); }}
        onLive={() => {follow.current=true;session.store.liveFollow=true;setCut(undefined);cutRef.current=undefined;session.clock.seek(session.clock.end);session.store.seek(session.clock.ns);tick();}} onTick={tick} />
    </>}
    {session&&<>
      <div className="simple-runtime-panels"><Button onClick={()=>setOperationsOpen(true)}>External events & controls</Button> <Details title="Photos" buttonLabel="Photos"><ArtifactsPanel api={api} runId={runId} store={session.store} commitCut={cut} onSeek={seekJournal}/></Details></div>
      <Drawer title="External events & run controls" aria-label="External events & run controls" open={operationsOpen} onClose={()=>setOperationsOpen(false)} width={720} forceRender>
        <section id="run-operations"><RunOperations api={api} runId={runId} store={session.store} selected={selected} commitCut={cut} onSelect={select} onSeek={seekJournal} mode={mode} interactive={interactive} onInjections={setHasInjection} onRequestInject={()=>setOperationsOpen(true)}/></section>
      </Drawer>
    </>}
  </div>;
}
