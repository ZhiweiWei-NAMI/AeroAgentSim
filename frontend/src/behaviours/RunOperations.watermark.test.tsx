import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { ConsoleNotifications } from '../console/Notifications';
import { RunOperations } from './RunOperations';
import { activateInjectionShortcut } from './injection-defaults';

it('pauses a waiting run by keyboard and recognizes resume back into the wait', async () => {
  const api = new RunsApi('http://api');
  let status = 'waiting_for_input';
  const runs = vi.spyOn(api, 'runs').mockImplementation(async () => [{ id: 'waiting', scenario: 'external', status, until_ns: '100' }]);
  const request = vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/configuration')) return { scenario: { id: 'external', behaviours: [] } };
    expect(options).toMatchObject({method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const action = path.split('/').at(-1);
    if (action === 'pause') status = 'paused';
    else if (action === 'resume') status = 'waiting_for_input';
    else throw Error(`Unexpected API request: ${path}`);
    return { requested: action, status };
  });
  try {
    const store = new TemporalFeedStore(fixtureHeader);
    store.ingest(emptyCommit(1)); store.seek('0', 1);
    await act(async () => { render(<ConsoleNotifications><RunOperations api={api} runId="waiting" store={store} onSelect={() => {}} onSeek={() => {}} mode="live" /></ConsoleNotifications>); });
    fireEvent.keyDown(document, { key: ' ' });
    await waitFor(() => expect(screen.getByText(/Requested: pause/)).toHaveTextContent('effective status: paused'));
    fireEvent.keyDown(document, { key: ' ' });
    await waitFor(() => expect(screen.getByText(/Requested: resume/)).toHaveTextContent('effective status: waiting_for_input'));
    expect(screen.getByText(/Requested: resume/)).not.toHaveTextContent('pending at safe boundary');
  } finally { cleanup(); request.mockRestore(); runs.mockRestore(); }
});

it('advances an idle operator source from acknowledged watermarks, capped at the authored end', async () => {
  vi.useFakeTimers();
  const api = new RunsApi('http://api'), watermarks: number[] = [];
  const request = vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/configuration')) return { scenario: {
      id: 'traffic-accident', run: { advance_ns: 1_000_000_000, until_ns: 2_500_000_000 },
      ingress_streams: [{ id: 'operator', initial_watermark_ns: 0 }], behaviours: [],
    } };
    if (!path.endsWith('/watermark')) throw Error(`Unexpected API request: ${path}`);
    const body = JSON.parse(String(options?.body)) as {stream_id: string; watermark_ns: number};
    expect(body.stream_id).toBe('operator');
    watermarks.push(body.watermark_ns);
    return {contract: 'aeroagentsim.watermark-receipt/v2', stream_id: 'operator', watermark_ns: body.watermark_ns};
  });
  try {
    const store = new TemporalFeedStore(fixtureHeader);
    store.ingest(emptyCommit(1)); store.seek('0', 1);
    await act(async () => { render(<ConsoleNotifications><RunOperations api={api} runId="idle" store={store} onSelect={() => {}} onSeek={() => {}} mode="live" /></ConsoleNotifications>); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(store.commits.at(-1)?.at.ns).toBe('0');
    expect(watermarks).toEqual([1_000_000_000, 2_000_000_000, 2_500_000_000]);
    fireEvent.click(screen.getByRole('button',{name:'Source timing'}));
    expect(screen.getByText(/closed through 00:02.5/)).toBeVisible();
  } finally { cleanup(); request.mockRestore(); vi.useRealTimers(); }
});

it('injects the authored accident in one click with real identity and automatic transport',async()=>{
 const api=new RunsApi('http://api'),bodies:unknown[]=[];
 const point={id:'accident',stream_id:'operator',command:'aas.runtime.inject_event',target:'behaviour',emits:'traffic.inject.accident'};
 const header={...fixtureHeader,kernelRunId:'actual-kernel',types:[...fixtureHeader.types,{typeId:'aas:TrafficIncident',displayName:'Incident',ancestors:[]}],messages:[
  {id:point.command,kind:'command',schema:{type:'record',members:{},required:[]}},
  {id:point.emits,kind:'event',schema:{type:'record',members:{incident:{type:'ref',target_type:'aas:TrafficIncident'},request_id:{type:'string'},source_cut:{type:'integer'},reason:{type:'string',default:'Operator report'}},required:[]}},
 ]};
 const store=new TemporalFeedStore(header),commit=emptyCommit(7);commit.created=[{id:'incident.real',generation:0,typeId:'aas:TrafficIncident'}];store.ingest(commit);store.seek('0');
 const request=vi.spyOn(api,'request').mockImplementation(async(path,options)=>{
  if(path.endsWith('/configuration'))return {scenario:{id:'traffic-accident',run:{advance_ns:1_000_000_000,until_ns:90_000_000_000},ingress_streams:[{id:'operator',initial_watermark_ns:0}],behaviours:[{document:{injection_points:[point]}}]}};
  if(path.endsWith('/ingress')){bodies.push(JSON.parse(String(options?.body)));return {disposition:'accepted'};}
  throw Error(`Unexpected request: ${path}`);
 });
 try{
  render(<ConsoleNotifications><RunOperations api={api} runId="actual-service" store={store} onSelect={()=>{}} onSeek={()=>{}} mode="live"/></ConsoleNotifications>);
  await waitFor(()=>expect(screen.getByTestId('inject-accident')).toBeEnabled());
  expect(screen.getByLabelText('Payload.reason')).toHaveValue('Operator report');
  expect(screen.queryByLabelText('Ingress at_ns')).toBeNull();
  activateInjectionShortcut(screen.getByTestId('injection-card'));
  await waitFor(()=>expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({schema:point.command,target:point.target,stream_id:'operator',at_ns:1,source_stamp:{clock_id:'canonical',mapping_id:'canonical',numerator:1,denominator:1},payload:{injection_point:'accident',payload:{incident:{$ref:{run_id:'actual-kernel',epoch:fixtureHeader.epoch,id:'incident.real',generation:0,type_id:'aas:TrafficIncident'}},source_cut:7,request_id:expect.any(String),reason:'Operator report'}},idempotency_key:expect.any(String)});
 }finally{cleanup();request.mockRestore();}
});
