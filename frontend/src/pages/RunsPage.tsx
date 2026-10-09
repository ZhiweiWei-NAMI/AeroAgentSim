import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Space, Table, Tag } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import type { EntityKey } from '../contracts/viewer-feed';
import { awaitRunConfiguration, HttpViewerFeed, RunsApi, type RunInfo, type WaitingContext } from '../feeds/http';
import { TemporalFeedStore as FeedStore } from '../feeds/temporal-store';
import { displayTime } from './display-time';
import { ConceptHelp } from '../console/ConceptHelp';
import { PlaybackClock } from '../viewport/clock';
import { DualRunViews } from '../behaviours/DualRunViews';
import { RunOperations } from '../behaviours/RunOperations';
import { activateInjectionShortcut } from '../behaviours/injection-defaults';
import { ArtifactsPanel } from '../observations/ArtifactsPanel';
import { CaptureConsole } from '../observations/CaptureConsole';
import { InspectionPanel } from './InspectionPanel';
import { RunTimeline } from './RunTimeline';
import { readableLabel, entityLabel } from './inspection-format';
import { WaitingBanner } from './WaitingBanner';
import './inspect.css';
import { Details } from '../console/Details';
import { PageHeader, PageState } from '../console/PageState';
import { useConsoleNotice } from '../console/Notifications';
import { entityId, resolveBinding } from '../viewport/bindings';
import '../viewport/viewer.css';

export default function RunsPage({route,interactive=true,inspectList=false}:{route?:{pathname:string;search:string};interactive?:boolean;inspectList?:boolean}) {
  const currentLocation = useLocation(), navigate = useNavigate();
  const location = route ?? currentLocation;
  const notify=useConsoleNotice();
  const query = new URLSearchParams(location.search);
  const apiBase = query.get('api') ?? (['3000', '4179'].includes(window.location.port) ? 'http://127.0.0.1:8002' : window.location.origin);
  const api = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const id = location.pathname.match(/^\/(?:runs|inspect)\/([^/]+)$/)?.[1];
  const runId = id ? decodeURIComponent(id) : undefined;
  const mode = query.get('mode') === 'live' ? 'live' : 'replay';
  const requestedEntity = query.get('entity');
  const requestedGeneration = query.get('generation'), requestedEpoch = query.get('epoch');
  const requestedCut = query.get('cut');
  const [runs, setRuns] = useState<RunInfo[]>([]), [listLoaded,setListLoaded]=useState(false), [error, setError] = useState<string>();
  const [scenarioPath, setScenarioPath] = useState('scenarios/p1-slice.yaml'), [body, setBody] = useState('');
  const [session, setSession] = useState<{ store: FeedStore; clock: PlaybackClock }>();
  const [camera,setCamera]=useState<'orbit'|'follow'|'chase'>('orbit');
  const [selected, setSelected] = useState<EntityKey>(), [cut, setCut] = useState<number>();
  const cutRef = useRef<number>();
  const follow = useRef(mode === 'live');
  const [status, setStatus] = useState('loading');
  const [waiting, setWaiting] = useState<WaitingContext>();
  const [, redraw] = useState(0);
  const tick = () => redraw(value => value + 1);
  const suffix = `?api=${encodeURIComponent(apiBase)}&mode=${mode}`;
  const refresh = () => { void api.runs().then(rows=>{setRuns(rows);setListLoaded(true);}).catch(error => setError(String(error))); };
  useEffect(() => {
    const abort = new AbortController();
    const load=()=>{void api.runs(abort.signal).then(rows=>{if(!abort.signal.aborted){setRuns(rows);setListLoaded(true);}}).catch(error => { if (!abort.signal.aborted) setError(String(error)); });};
    load();
    const timer=!runId?setInterval(load,1000):undefined;
    return () => {abort.abort();if(timer!==undefined)clearInterval(timer);};
  }, [api,runId]);
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
      const entity = requestedEntity ? entities.find(entity => entity.key.id === requestedEntity && (!requestedGeneration || String(entity.key.generation) === requestedGeneration)) : entities.find(entity => resolveBinding(session.store.header, entity.typeId)) ?? entities[0];
      if (entity) { setSelected(entity.key); if (session.store.header.epoch !== undefined) session.store.select(entity.key); }
    }
  });
  const start = async () => {
    try {
      setError(undefined);
      const run = await (body.trim() ? api.startSource(body) : api.start({ scenario_path: scenarioPath }));
      notify({kind:'success',message:'Run started. Following live state.'});
      navigate(`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=live`); refresh();
    } catch (error) { setError(String(error)); notify({kind:'error',message:String(error)}); }
  };
  if (query.has('capture')) return <CaptureConsole apiBase={apiBase} assetManifestUrl={query.get('capture_assets')} />;
  if (!runId) return <div className="console-page" data-testid={inspectList?'inspect-page':'runs-page'}>
    <PageHeader eyebrow={inspectList?'Explore recorded state':'Operate'} title={inspectList?'Inspect':'Runs'} description={inspectList?'Open a live run or replay its synchronized AeroGraph and city views.':'Start a configured scenario, manage its simulation and follow live state.'} /><Space wrap><Input aria-label="Scenario path" value={scenarioPath} onChange={event => setScenarioPath(event.target.value)} style={{ width: 360 }} />
      <Button type="primary" onClick={() => void start()}>Start scenario</Button><Button onClick={refresh}>Refresh runs</Button></Space>
    <Details title="Scenario JSON body"><Input.TextArea aria-label="Scenario JSON" rows={6} value={body} onChange={event => setBody(event.target.value)} /></Details>
    {error && <Alert type="error" message={error} />}
    {!listLoaded&&!error&&<PageState kind="loading" title="Loading runs" />}{listLoaded&&!runs.length&&<PageState kind="empty" title="No runs yet" description="Open the traffic accident demo in Studio to validate and start your first run." />}
    <Table rowKey="id" dataSource={runs} columns={[
      { title: 'Scenario', render: (_, run) => <Link to={`/${inspectList?'inspect':'runs'}/${encodeURIComponent(run.id)}${suffix}`}>{readableLabel(run.scenario)}</Link> },
      { title: 'Status', dataIndex: 'status', render: (status: string, run) => <div><span>{status}</span>{['waiting_for_input', 'input_timeout'].includes(status) && <WaitingBanner waiting={run.waiting} terminal={status === 'input_timeout'} injectionHref={`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=live#run-operations`} />}</div> },
      { title: 'Simulated limit', dataIndex: 'until_ns', render:(ns:string)=><span title={`${ns} ns`}>{displayTime(ns)}</span> },
      { title: 'Open', render: (_, run) => <Space><Link to={`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=live`}>Live</Link><Link to={`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=replay`}>Replay</Link></Space> },
    ]} />
  </div>;
  const store = session?.store, clock = session?.clock;
  const seek = (index: number) => {
    if (!store || !clock || !store.commits[index]) return;
    follow.current = false; store.liveFollow = false; clock.pause(); clock.seek(store.commits[index].at.ns);cutRef.current=store.commits[index].commitIndex; setCut(store.commits[index].commitIndex);
    store.seek(clock.ns, store.commits[index].commitIndex); tick();
  };
  const select = (key: EntityKey) => { setSelected(key); if (store?.header.epoch !== undefined) store.select(key); };
  const seekJournal = (index: number) => { const offset = store?.commits.findIndex(commit => commit.commitIndex === index); if (offset === undefined || offset < 0) { setError(`Journal cut ${index} is not loaded`); return; } seek(offset); };
  return <div className="viewer-demo inspection-page">
    <header className="viewer-header"><div><h1>{location.pathname.startsWith('/inspect')?'Inspect simulation':'Live run console'}</h1><span>One timeline · AeroGraph + city</span><ConceptHelp topic="Synchronized views" description="Graph and 3D views share entity identities, selection and the same recorded timeline moment." guide="console.md"/></div>
      <Space><Tag>{status}</Tag><Select aria-label="Feed mode" value={mode} options={[{ value: 'live', label: 'Live' }, { value: 'replay', label: 'Replay' }]} onChange={value => navigate(`/${location.pathname.startsWith('/inspect')?'inspect':'runs'}/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${value}`)} />
      <Link to={`/agents/${encodeURIComponent(runId)}${suffix}${cut === undefined ? '' : `&cut=${cut}`}${selected ? `&entity=${encodeURIComponent(selected.id)}&generation=${selected.generation}` : ''}${store?.header.epoch ? `&epoch=${encodeURIComponent(store.header.epoch)}` : ''}`}>Agent decisions</Link>
      <a href="#run-operations">Run controls</a><Details title="Run details"><pre>{JSON.stringify({runId,header:store?.header,cursor:store?.viewCursor,selection:store?.selection},null,2)}</pre></Details></Space></header>
    {error && <Alert type="error" message={error} />}
    {session && <WaitingBanner waiting={waiting} terminal={status === 'input_timeout'} injectionHref="#run-operations" onInject={() => {
      const controls=document.getElementById('run-operations');
      controls?.scrollIntoView({behavior:'smooth'});
      if(controls)activateInjectionShortcut(controls);
    }} />}
    {!session&&!error&&<PageState kind="loading" title="Connecting to the run" description="Loading its recorded entities and timeline." />}
    {session && <><main className="viewer-main" data-testid="inspect-views" data-city-binding={session.store.header.scene?.city?.kind ?? 'absent'}><section className="viewer-stage">
      <nav aria-label="Demo camera presets"><span>Camera</span><ConceptHelp topic="Camera presets" description="A preset selects the named actor and follows its recorded pose; the graph selection changes with it." guide="console.md"/>{[{label:'Reporter',id:'vehicle.reporter'}, {label:'Edge overview',id:'edge.coordinator'}, {label:'Alpha',id:'uav.alpha'}, {label:'Bravo',id:'uav.bravo'}].filter(preset=>[...session.store.entities.values()].some(row=>row.key.id===preset.id)).map(preset=><button key={preset.label} onClick={()=>{const actor=[...session.store.entities.values()].find(row=>row.key.id===preset.id);if(!actor){setError(`Preset actor ${preset.id} is absent at this cut`);return;}select(actor.key);setCamera(preset.label==='Edge overview'?'orbit':'follow');}}>{preset.label}</button>)}</nav>
      <DualRunViews store={session.store} clock={session.clock} selected={selected} mode={camera} quality="med" trails={true} commitCut={cut}
        onSelect={select} onSeek={seekJournal} onTick={tick} onError={error => setError(String(error))} onQuality={() => {}} />
      </section>
      <aside className="viewer-sidebar inspection-sidebar"><Select aria-label="Entity" showSearch optionFilterProp="label" className="inspection-entity-select" value={selected && entityId(selected)} options={[...session.store.entities].map(([id, entity]) => ({ value: id, label: entityLabel(entity) }))} onChange={id => { const key = session.store.entities.get(id)?.key; if (key) select(key); }} />
        <InspectionPanel store={session.store} selected={selected} onSeek={seekJournal} /></aside></main>
      <RunTimeline store={session.store} clock={session.clock} cut={cut} mode={mode} onSeek={seek}
        onPlay={() => { setCut(undefined);cutRef.current=undefined;follow.current=false;session.store.liveFollow=false;session.clock.playing?session.clock.pause():session.clock.play();tick(); }}
        onLive={() => {follow.current=true;session.store.liveFollow=true;setCut(undefined);cutRef.current=undefined;session.clock.seek(session.clock.end);session.store.seek(session.clock.ns);tick();}} onTick={tick} />
      <section id="run-operations" className="run-inspection-panels"><RunOperations api={api} runId={runId} store={session.store} selected={selected} commitCut={cut} onSelect={select} onSeek={seekJournal} mode={mode} interactive={interactive} />
        <ArtifactsPanel api={api} runId={runId} store={session.store} commitCut={cut} onSeek={seekJournal} /></section></>}
  </div>;
}
