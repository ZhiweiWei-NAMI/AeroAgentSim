import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Slider, Space, Table, Tag } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import type { EntityKey, FeedCommit } from '../contracts/viewer-feed';
import { HttpViewerFeed, RunsApi, type RunInfo } from '../feeds/http';
import { TemporalFeedStore as FeedStore } from '../feeds/temporal-store';
import { seconds } from '../feeds/format';
import { PlaybackClock } from '../viewport/clock';
import { ViewerPresentation } from '../viewport/ViewerPresentation';
import { EntityInspector } from '../viewport/EntityInspector';
import { entityId, resolveBinding } from '../viewport/bindings';
import '../viewport/viewer.css';

export default function RunsPage() {
  const location = useLocation(), navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const apiBase = query.get('api') ?? (['3000', '4179'].includes(window.location.port) ? 'http://127.0.0.1:8002' : window.location.origin);
  const api = useMemo(() => new RunsApi(apiBase), [apiBase]);
  const id = location.pathname.slice('/runs/'.length);
  const runId = location.pathname.startsWith('/runs/') && id ? decodeURIComponent(id) : undefined;
  const mode = query.get('mode') === 'live' ? 'live' : 'replay';
  const requestedEntity = query.get('entity');
  const [runs, setRuns] = useState<RunInfo[]>([]), [error, setError] = useState<string>();
  const [scenarioPath, setScenarioPath] = useState('scenarios/p1-slice.yaml'), [body, setBody] = useState('');
  const [session, setSession] = useState<{ store: FeedStore; clock: PlaybackClock }>();
  const [selected, setSelected] = useState<EntityKey>(), [cut, setCut] = useState<number>();
  const cutRef = useRef<number>();
  const follow = useRef(mode === 'live');
  const [status, setStatus] = useState('loading');
  const [, redraw] = useState(0);
  const tick = () => redraw(value => value + 1);
  const suffix = `?api=${encodeURIComponent(apiBase)}&mode=${mode}`;
  const refresh = () => { void api.runs().then(setRuns).catch(error => setError(String(error))); };
  useEffect(() => {
    const abort = new AbortController();
    void api.runs(abort.signal).then(setRuns).catch(error => { if (!abort.signal.aborted) setError(String(error)); });
    return () => abort.abort();
  }, [api]);
  useEffect(() => {
    if (!runId) { setSession(undefined); return; }
    const abort = new AbortController();
    setStatus('loading'); setError(undefined); setSession(undefined); setCut(undefined);cutRef.current=undefined; setSelected(undefined); follow.current = mode === 'live';
    const feed = new HttpViewerFeed(api, runId, mode, setStatus);
    void feed.header().then(header => {
      if (abort.signal.aborted) return;
      const store = new FeedStore(header), clock = new PlaybackClock(header.start.ns, header.end?.ns ?? header.start.ns);
      setSession({ store, clock });
      return feed.subscribe(0, commit => {
        store.ingest(commit);
        if (BigInt(commit.at.ns) > BigInt(clock.end)) clock.end = commit.at.ns;
        if (mode === 'live' && follow.current) clock.seek(clock.end);
        store.seek(clock.ns,cutRef.current);
        tick();
      }, abort.signal);
    }).catch(error => { if (!abort.signal.aborted) setError(String(error)); });
    return () => abort.abort();
  }, [api, runId, mode]);
  useEffect(() => {
    if (!selected && session) {
      const entity = [...session.store.entities.values()].find(entity => entity.key.id === requestedEntity) ?? [...session.store.entities.values()].find(entity => resolveBinding(session.store.header, entity.typeId)) ?? session.store.entities.values().next().value;
      if (entity) setSelected(entity.key);
    }
  });
  const start = async () => {
    try {
      setError(undefined);
      const run = await (body.trim() ? api.startSource(body) : api.start({ scenario_path: scenarioPath }));
      navigate(`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=live`); refresh();
    } catch (error) { setError(String(error)); }
  };
  const control = (action: 'pause' | 'resume' | 'stop') => {
    if (runId) void api.control(runId, action).then(refresh).catch(error => setError(String(error)));
  };
  if (!runId) return <div style={{ padding: 32 }}>
    <h1>AeroAgentSim Runs</h1><Space><Input aria-label="Scenario path" value={scenarioPath} onChange={event => setScenarioPath(event.target.value)} style={{ width: 360 }} />
      <Button type="primary" onClick={() => void start()}>Start scenario</Button><Button onClick={refresh}>Refresh runs</Button></Space>
    <details style={{ margin: '16px 0' }}><summary>Scenario JSON body</summary><Input.TextArea aria-label="Scenario JSON" rows={6} value={body} onChange={event => setBody(event.target.value)} /></details>
    {error && <Alert type="error" message={error} />}
    <Table rowKey="id" dataSource={runs} columns={[
      { title: 'Run', dataIndex: 'id', render: (id: string) => <Link to={`/runs/${encodeURIComponent(id)}${suffix}`}>{id}</Link> },
      { title: 'Scenario', dataIndex: 'scenario' }, { title: 'Status', dataIndex: 'status' },
      { title: 'Simulated limit', dataIndex: 'until_ns', render:(ns:string)=><span title={`${ns} ns`}>{seconds(ns)}</span> },
      { title: 'Open', render: (_, run) => <Space><Link to={`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=live`}>Live</Link><Link to={`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(apiBase)}&mode=replay`}>Replay</Link></Space> },
    ]} />
  </div>;
  const store = session?.store, clock = session?.clock;
  const current: FeedCommit | undefined = (()=>{if(!store||!clock)return undefined;let low=0,high=store.commits.length;while(low<high){const middle=(low+high)>>>1;if(BigInt(store.commits[middle].at.ns)<=BigInt(clock.ns))low=middle+1;else high=middle;}return store.commits[low-1];})();
  const seek = (index: number) => {
    if (!store || !clock || !store.commits[index]) return;
    follow.current = false; clock.pause(); clock.seek(store.commits[index].at.ns);cutRef.current=store.commits[index].commitIndex; setCut(store.commits[index].commitIndex);
    store.seek(clock.ns, store.commits[index].commitIndex); tick();
  };
  return <div className="viewer-demo">
    <header className="viewer-header"><div><Link to={`/runs${suffix}`}>AeroAgentSim / Runs</Link><div>{runId}</div></div>
      <Space><Tag>{status}</Tag><Select aria-label="Feed mode" value={mode} options={[{ value: 'live', label: 'Live' }, { value: 'replay', label: 'Replay' }]} onChange={value => navigate(`/runs/${encodeURIComponent(runId)}?api=${encodeURIComponent(apiBase)}&mode=${value}`)} />
      <Link to={`/agents/${encodeURIComponent(runId)}${suffix}`}>Agent decisions</Link>
      {(['pause', 'resume', 'stop'] as const).map(action => <Button key={action} onClick={() => control(action)}>{action}</Button>)}</Space></header>
    {error && <Alert type="error" message={error} />}
    {session && <><main className="viewer-main"><section className="viewer-stage">
      <ViewerPresentation store={session.store} clock={session.clock} selected={selected} mode="orbit" quality="med" trails={true} commitCut={cut}
        onSelect={setSelected} onTick={tick} onError={error => setError(String(error))} onQuality={() => {}} />
      <div className="viewport-caption" title={`${clock?.ns} ns`}>{store?.entities.size} entities · {store?.header.presentation.length ? store.header.presentation.map(binding=>{const field=store.header.fields.find(field=>field.fieldId===binding.positionField);return `${field?.frame ?? binding.frame} ${field?.unit ?? ''}`.trim();}).join(', ') : 'no spatial binding'} · {clock && seconds(clock.ns,store?.header.start.ns)}</div></section>
      <aside className="viewer-sidebar"><Select aria-label="Entity" showSearch optionFilterProp="label" style={{ width: '100%' }} value={selected && entityId(selected)} options={[...session.store.entities].map(([id, entity]) => ({ value: id, label: entity.key.id }))} onChange={id => setSelected(session.store.entities.get(id)?.key)} />
        <EntityInspector store={session.store} selected={selected} /></aside></main>
      <footer className="viewer-timeline"><Button aria-label={clock!.playing ? 'Pause playback' : 'Play'} onClick={() => { setCut(undefined);cutRef.current=undefined; follow.current = false; clock!.playing ? clock!.pause() : clock!.play(); tick(); }}>{clock!.playing ? 'Pause playback' : 'Play'}</Button>
        {mode === 'live' && <Button onClick={() => { follow.current = true; setCut(undefined);cutRef.current=undefined; clock!.seek(clock!.end); tick(); }}>Follow live</Button>}
        <Slider aria-label="Commit timeline" style={{ flex: 1 }} min={0} max={Math.max(0, store!.commits.length - 1)} value={cut === undefined ? Math.max(0, store!.commits.findIndex(commit => commit === current)) : store!.commits.findIndex(commit => commit.commitIndex === cut)} onChange={seek} />
        <span title={`${clock!.ns} ns`}>{seconds(clock!.ns,store!.header.start.ns)} · {store!.commits.length} commits</span></footer></>}
  </div>;
}
