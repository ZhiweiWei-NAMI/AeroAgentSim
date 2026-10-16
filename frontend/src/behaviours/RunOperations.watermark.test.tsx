import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { ConsoleNotifications } from '../console/Notifications';
import { RunOperations } from './RunOperations';
import type { OperatorInput } from '../workbench/useOperatorInput';

it('keeps a delayed read-only status poll out of the top-bar action queue', async () => {
 const api = new RunsApi('http://api'), store = new TemporalFeedStore(fixtureHeader);
 store.ingest(emptyCommit(1)); store.seek('0');
 let release!: (value: unknown) => void;
 const pending = new Promise(resolve => { release = resolve; });
 const request = vi.spyOn(api, 'request').mockImplementation(async path => {
  if (path.endsWith('/configuration')) return { scenario: {} };
  if (path.includes('/commits?')) return pending;
  if (path.endsWith('/stop')) return { status: 'stopped' };
  throw Error(path);
 });
 let tail: Promise<unknown> = Promise.resolve();
 const input: OperatorInput = { serialize: action => { const result = tail.then(action, action); tail = result; return result; } };
 try {
  render(<ConsoleNotifications><RunOperations api={api} runId="run" store={store} mode="live" onSelect={()=>{}} onSeek={()=>{}} inputSource={input}/></ConsoleNotifications>);
  await waitFor(() => expect(request.mock.calls.some(([path]) => path.includes('/commits?'))).toBe(true));
  void input.serialize(() => api.request('/v1/runs/run/stop', { method: 'POST' }));
  await waitFor(() => expect(request.mock.calls.some(([path]) => path.endsWith('/stop'))).toBe(true));
 } finally {
  await act(async () => { release({ status: 'stopped' }); }); cleanup(); request.mockRestore();
 }
});

it('retains the actual newer source frontier when a background read arrives after command preflight', async () => {
 const api = new RunsApi('http://api'), point = { id:'restaurant_delay', stream_id:'operator', command:'aas.runtime.inject_event', target:'behaviour', emits:'lowalt.operator.restaurant_delay' };
 const header = { ...fixtureHeader, messages:[
  { id:point.command, kind:'command', schema:{ type:'record', members:{}, required:[] } },
  { id:point.emits, kind:'event', schema:{ type:'record', members:{ restaurant:{ type:'string' }, delay_s:{ type:'number' } }, required:['restaurant','delay_s'] } },
 ] };
 const store = new TemporalFeedStore(header); store.ingest(emptyCommit(1)); store.seek('0');
 let reads = 0, release!: (value: unknown) => void;
 const pending = new Promise(resolve => { release = resolve; });
 const progress = (value: number) => ({ status:'paused', pause_requested:true, watermarks:{ operator:value } });
 const request = vi.spyOn(api, 'request').mockImplementation(async path => {
  if (path.endsWith('/configuration')) return { scenario:{ registry:{ messages:header.messages }, run:{ advance_ns:1_000_000_000, until_ns:90_000_000_000 }, ingress_streams:[{ id:'operator', initial_watermark_ns:0 }], behaviours:[{ document:{ injection_points:[point] } }] } };
  if (path.includes('/commits?')) return { status:'paused' };
  if (path.endsWith('/inputs')) { reads++; return reads === 2 ? pending : progress(reads === 1 ? 1_000_000_000 : 2_000_000_000); }
  if (path.endsWith('/ingress')) return { disposition:'accepted' };
  throw Error(path);
 });
 try {
  // Finish configuration and payload initialization before entering details;
  // this regression exercises out-of-order progress reads after initialization.
  await act(async () => {
   render(<ConsoleNotifications><RunOperations api={api} runId="food" store={store} mode="live" onSelect={()=>{}} onSeek={()=>{}}/></ConsoleNotifications>);
  });
  fireEvent.change(screen.getByLabelText('Payload.restaurant'), { target:{ value:'restaurant-east' } });
  fireEvent.change(screen.getByLabelText('Payload.delay_s'), { target:{ value:'45' } });
  await waitFor(() => expect(screen.getByTestId('inject-restaurant_delay')).toBeEnabled());
  await waitFor(() => expect(reads).toBe(2), { timeout:3000 });
  fireEvent.click(screen.getByTestId('inject-restaurant_delay'));
  await waitFor(() => expect(request.mock.calls.some(([path]) => path.endsWith('/ingress'))).toBe(true));
  const body = JSON.parse(String(request.mock.calls.find(([path]) => path.endsWith('/ingress'))![1]!.body));
  expect(body.at_ns).toBe(2_000_000_001);
  await act(async () => { release(progress(500_000_000)); });
  fireEvent.click(screen.getByRole('button', { name:'Source timing' }));
  expect(screen.getByText(/Operator source progress is closed through 00:02\.0/)).toBeVisible();
 } finally {
  await act(async () => { release(progress(500_000_000)); }); cleanup(); request.mockRestore();
 }
});

it('requires a complete authored payload and blocks historical commands without changing the recorded selection', async () => {
  const api = new RunsApi('http://api');
  const point = {id:'restaurant_delay',stream_id:'operator',command:'aas.runtime.inject_event',target:'behaviour',emits:'lowalt.operator.restaurant_delay'};
  const header = {...fixtureHeader, messages:[
    {id:point.command,kind:'command',schema:{type:'record',members:{},required:[]}},
    {id:point.emits,kind:'event',schema:{type:'record',members:{restaurant:{type:'string'},delay_s:{type:'number',minimum:0.001}},required:['restaurant','delay_s']}},
  ]};
  const store = new TemporalFeedStore(header);store.ingest(emptyCommit(1));store.seek('0',1);
  const request = vi.spyOn(api,'request').mockImplementation(async(path) => {
    if(path.endsWith('/configuration'))return {scenario:{registry:{messages:header.messages},run:{advance_ns:1_000_000_000,until_ns:90_000_000_000},ingress_streams:[{id:'operator',initial_watermark_ns:0}],behaviours:[{document:{injection_points:[point]}}]}};
    if(path.includes('/commits?'))return {status:'paused'};
    if(path.endsWith('/inputs'))return {status:'paused',watermarks:{operator:0},pause_requested:true};
    if(path.endsWith('/ingress'))return {disposition:'accepted'};
    throw Error(`Unexpected request: ${path}`);
  });
  try {
    const props={api,runId:'food',store,onSelect:()=>{},onSeek:()=>{},mode:'live' as const};
    // A point renders before its payload-default effect has settled. Flush
    // configuration/initialization before editing, then await source readiness.
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view=render(<ConsoleNotifications><RunOperations {...props}/></ConsoleNotifications>);
    });
    await screen.findByRole('button',{name:'Source timing'});
    expect(screen.getByTestId('inject-restaurant_delay')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Payload.restaurant'),{target:{value:'restaurant-east'}});
    fireEvent.change(screen.getByLabelText('Payload.delay_s'),{target:{value:'0'}});
    expect(screen.getByTestId('inject-restaurant_delay')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Payload.delay_s'),{target:{value:'45'}});
    await waitFor(()=>expect(screen.getByTestId('inject-restaurant_delay')).toBeEnabled());
    // Live batches can arrive before their next paint. This is not a seek.
    store.liveFollow=true;store.ingest(emptyCommit(2));
    view.rerender(<ConsoleNotifications><RunOperations {...props}/></ConsoleNotifications>);
    expect(screen.getByTestId('inject-restaurant_delay')).toBeEnabled();
    expect(screen.queryByRole('button',{name:'Stop simulation'})).toBeNull();
    view.rerender(<ConsoleNotifications><RunOperations {...props} commitCut={1}/></ConsoleNotifications>);
    expect(screen.getByTestId('inject-restaurant_delay')).toBeDisabled();
    expect(screen.getByText(/Return to live before submitting/)).toBeVisible();
    expect(screen.queryByRole('button',{name:'Stop simulation'})).toBeNull();
    expect(request.mock.calls.some(([path])=>path.endsWith('/ingress'))).toBe(false);
    view.rerender(<ConsoleNotifications><RunOperations {...props}/></ConsoleNotifications>);
    await waitFor(()=>expect(screen.getByTestId('inject-restaurant_delay')).toBeEnabled());
  } finally {cleanup();request.mockRestore();}
});

it('prefills a selected matching entity from the declared subject and sends its original string identity',async()=>{
 const api=new RunsApi('http://api'),point={id:'restaurant_delay',stream_id:'operator',command:'aas.runtime.inject_event',target:'behaviour',emits:'lowalt.operator.restaurant_delay'};
 const header={...fixtureHeader,types:[...fixtureHeader.types,{typeId:'Restaurant',displayName:'Restaurant',ancestors:['Site']},{typeId:'UAV',displayName:'Drone',ancestors:[]}],messages:[
  {id:point.command,kind:'command',schema:{type:'record',members:{},required:[]}},
  {id:point.emits,kind:'event',subjects:[{path:['restaurant'],type_id:'Restaurant'}],schema:{type:'record',members:{restaurant:{type:'string'},delay_s:{type:'number'}},required:['restaurant','delay_s']}},
 ]};
 const store=new TemporalFeedStore({...header,messages:header.messages.map(({subjects,...row})=>row)}),commit=emptyCommit(1);commit.created=[{id:'east',generation:0,typeId:'Restaurant'},{id:'drone',generation:0,typeId:'UAV'}];store.ingest(commit);store.seek('0',1);
 const request=vi.spyOn(api,'request').mockImplementation(async(path)=>{
  if(path.endsWith('/configuration'))return {scenario:{registry:{messages:header.messages},run:{advance_ns:1_000_000_000,until_ns:90_000_000_000},ingress_streams:[{id:'operator',initial_watermark_ns:0}],behaviours:[{document:{injection_points:[point]}}]}};
  if(path.includes('/commits?'))return {status:'paused'};
  if(path.endsWith('/inputs'))return {status:'paused',watermarks:{operator:0},pause_requested:true};
  if(path.endsWith('/ingress'))return {disposition:'accepted'};
  throw Error(path);
 });
 try{
  render(<ConsoleNotifications><RunOperations api={api} runId="food" store={store} selected={{id:'east',generation:0}} onSelect={()=>{}} onSeek={()=>{}} mode="live"/></ConsoleNotifications>);
  const picker=await screen.findByLabelText('Payload.restaurant');await waitFor(()=>expect(picker).toHaveValue('east'));expect(picker.tagName).toBe('SELECT');expect(picker).not.toHaveTextContent('drone');
  fireEvent.change(screen.getByLabelText('Payload.delay_s'),{target:{value:'45'}});await waitFor(()=>expect(screen.getByTestId('inject-restaurant_delay')).toBeEnabled());fireEvent.click(screen.getByTestId('inject-restaurant_delay'));
  await waitFor(()=>expect(request.mock.calls.some(([path])=>path.endsWith('/ingress'))).toBe(true));const body=JSON.parse(String(request.mock.calls.find(([path])=>path.endsWith('/ingress'))![1]!.body));expect(body.payload.payload.restaurant).toBe('east');
 }finally{cleanup();request.mockRestore();}
});
