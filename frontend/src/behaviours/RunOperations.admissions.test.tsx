import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { ConsoleNotifications } from '../console/Notifications';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { mergeAdmissionReceipts, RunOperations } from './RunOperations';

// Typed-form rendering is covered by its owner; these tests cover receipt persistence.
vi.mock('./TypedPayloadForm', () => ({ TypedPayloadForm: () => <div data-testid="typed-payload" /> }));
beforeEach(() => { vi.spyOn(document, 'hidden', 'get').mockReturnValue(false); });
afterEach(() => { cleanup(); window.localStorage.clear(); vi.useRealTimers(); vi.restoreAllMocks(); });

function runStore() {
  const store = new TemporalFeedStore({ ...fixtureHeader, kernelRunId: 'kernel.actual' });
  store.ingest(emptyCommit(7)); store.seek('0', 7);
  return store;
}
function mount(api: RunsApi,locale='en-US',onSeek=vi.fn()) {
  window.localStorage.setItem('aeroagentsim.locale', locale);
  return render(<I18nProvider><MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><ConsoleNotifications><RunOperations api={api} runId="service/run id" store={runStore()} onSelect={() => {}} onSeek={onSeek} mode="live" interactive={false} /></ConsoleNotifications></MemoryRouter></I18nProvider>);
}
function recordedApi(rows:unknown[]) {
  const api=new RunsApi('http://api');
  vi.spyOn(api,'request').mockImplementation(async path=>{
    if(path.includes('/admissions?'))return {cursor:456,receipts:rows};
    if(path.endsWith('/configuration'))return {scenario:{}};
    if(path.includes('/commits?'))return {status:'paused',commits:[],next:8};
    throw Error(`Unexpected request ${path}`);
  });
  return api;
}

it('RUN-008: restores journaled admission receipts on mount so a refresh keeps the admitted operation visible', async () => {
  const api = new RunsApi('http://api');
  vi.spyOn(api, 'request').mockImplementation(async path => {
    if (String(path).includes('/admissions?')) return { cursor: 260, receipts: [{ id: 'cmd-9', submitted_at: '2026-10-13T08:00:00Z', target_cut: 130, kind: 'ingress', status: 'admitted' }] };
    if (String(path).endsWith('/configuration')) return { scenario: {} };
    if (String(path).includes('/commits?')) return { status: 'paused', commits: [], next: 8 };
    throw Error(`Unexpected request ${path}`);
  });
  mount(api);
  const summary = await screen.findByText(/Admission receipts · 1/);
  expect(summary).toBeVisible();
  // Awaiting execution is stated plainly; success or failure is never invented.
  expect(screen.getByText(/awaiting execution/i)).toBeVisible();
  expect(screen.getByText(/cmd-9 · 2026-10-13T08:00:00Z · target cut 130/)).toBeVisible();
});

it('RUN-008: restores a journaled applied outcome in one receipt', async () => {
  const api=recordedApi([{id:'cmd-9',status:'applied',target_cut:130}]);
  mount(api);
  await waitFor(() => expect(screen.getByText(/Admission receipts · 1/)).toBeVisible());
  expect(screen.getByText(/applied/i)).toBeVisible();
  expect(screen.getByText('Executed. Its records appear in the timeline.')).toBeVisible();
});

it.each(['en-US','zh-CN'])('T3-002/T4-04 and T3-005: restores the execution result, cut and Instant timestamp in %s',async locale=>{
  const rows=[{id:'cmd-9',command_id:'cmd-9',status:'applied',disposition:'accepted',submitted_at:{ns:'16000000000',microstep:4},target_cut:226,execution_cut:228,execution_status:'succeeded',execution_result:{injection_point:'restaurant_delay'},idempotency_key:'actual-request'}];
  const api=recordedApi(rows),onSeek=vi.fn();
  mount(api,locale,onSeek);
  const receipt=(await screen.findByText(locale==='en-US'?'Admission #1':'受理 #1')).closest('article')!;
  expect(receipt).toHaveAttribute('data-receipt-state','ok');
  expect(receipt).toHaveTextContent(locale==='en-US'?'submitted at 16 s · microstep 4':'提交于 16 秒 · 微步 4');
  expect(receipt).toHaveTextContent(locale==='en-US'?'Executed.':'已执行');
  expect(receipt).not.toHaveTextContent(/awaiting execution|等待执行/);
  expect(within(receipt).getByRole('region',{name:locale==='en-US'?'Execution result':'执行结果'})).toHaveTextContent(locale==='en-US'?'Restaurant Delay':'餐厅出餐延迟');
  fireEvent.click(within(receipt).getByRole('button',{name:locale==='en-US'?'cut 228':'提交 228'}));
  expect(onSeek).toHaveBeenCalledWith(228);
  fireEvent.click(within(receipt).getByTestId('details-trigger'));
  const dialog=await screen.findByRole('dialog');
  expect(dialog).toHaveClass('wb-glass');
  expect(dialog).toHaveTextContent('actual-request');
  expect(within(dialog).getByText(locale==='en-US'?'Raw record':'原始记录').closest('details')).not.toHaveAttribute('open');
});

it('T3-002: an execution failure takes precedence over an applied admission status',async()=>{
  mount(recordedApi([{id:'cmd-9',status:'applied',execution_status:'failed',execution_cut:228,detail:'Engine rejected the event'}]));
  const receipt=(await screen.findByText('Admission #1')).closest('article')!;
  expect(receipt).toHaveAttribute('data-receipt-state','bad');
  expect(receipt).toHaveTextContent('execution failed: Engine rejected the event');
  expect(receipt).not.toHaveTextContent('awaiting execution');
});

it.each(['en-US','zh-CN'])('T3-R6-001: shows initial loading without an empty history or zero count in %s',async locale=>{
  const api=recordedApi([]),request=vi.mocked(api.request),normal=request.getMockImplementation()!;
  let resolve!: (page:unknown)=>void;
  request.mockImplementation((path,options)=>path.includes('/admissions?')?new Promise(done=>{resolve=done;}):normal(path,options));
  mount(api,locale);
  expect(screen.getByText(locale==='en-US'?'Loading command history…':'正在加载命令历史…')).toHaveAttribute('role','status');
  expect(screen.queryByText(/Admission receipts · 0|受理回执 · 0|No admission receipts yet\.|还没有受理回执/)).toBeNull();
  await act(async()=>resolve({cursor:58,receipts:[{id:'cmd-recorded',status:'applied',execution_status:'succeeded'}]}));
  expect(screen.getByText(locale==='en-US'?'Admission receipts · 1':'受理回执 · 1')).toBeVisible();
  expect(screen.queryByText(/Loading command history|正在加载命令历史/)).toBeNull();
});

it('T3-R6-001: confirms an empty history only after the first successful read',async()=>{
  mount(recordedApi([]));
  expect(screen.getByText('Loading command history…')).toBeVisible();
  expect(await screen.findByText('Admission receipts · 0')).toBeVisible();
  expect(screen.getByText('No admission receipts yet.')).toBeVisible();
});

it('T3-R6-001: changing runs clears confirmed emptiness until the new run has been read',async()=>{
  const api=recordedApi([]),request=vi.mocked(api.request),normal=request.getMockImplementation()!;
  let resolve!: (value:unknown)=>void;
  request.mockImplementation((path,options)=>path.includes('/new-run/admissions?')?new Promise(done=>{resolve=done;}):normal(path,options));
  const store=runStore();
  const ui=(runId:string)=><I18nProvider><MemoryRouter future={{v7_startTransition:true,v7_relativeSplatPath:true}}><ConsoleNotifications><RunOperations api={api} runId={runId} store={store} onSelect={vi.fn()} onSeek={vi.fn()} mode="live" interactive={false}/></ConsoleNotifications></MemoryRouter></I18nProvider>;
  const view=render(ui('old-run'));
  await screen.findByText('Admission receipts · 0');
  view.rerender(ui('new-run'));
  expect(screen.getByText('Loading command history…')).toBeVisible();
  expect(screen.queryByText('Admission receipts · 0')).toBeNull();
  expect(screen.queryByText('No admission receipts yet.')).toBeNull();
  await act(async()=>resolve({cursor:12,receipts:[{id:'new-run-command',status:'admitted'}]}));
  expect(screen.getByText('Admission receipts · 1')).toBeVisible();
});

it.each(['en-US','zh-CN'])('T3-R6-001: shows a query error with Retry and recovers the recorded history in %s',async locale=>{
  const api=recordedApi([]),request=vi.mocked(api.request),normal=request.getMockImplementation()!;
  let failed=true;
  request.mockImplementation((path,options)=>path.includes('/admissions?')&&failed?Promise.reject(Error('HTTP 500: Journal unavailable')):normal(path,options));
  mount(api,locale);
  const error=await screen.findByRole('alert');expect(error).toHaveTextContent('HTTP 500: Journal unavailable');expect(error).toBeVisible();
  expect(screen.queryByText('No admission receipts yet.')).toBeNull();
  expect(screen.queryByText(/Admission receipts · 0|受理回执 · 0/)).toBeNull();
  failed=false;
  fireEvent.click(within(error).getByRole('button',{name:locale==='en-US'?'Retry':'重试'}));
  expect(await screen.findByText(locale==='en-US'?'Admission receipts · 0':'受理回执 · 0')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('T3-R6-002 / T3-R5-003: keeps one read in flight, advances the cursor and never downgrades execution', async () => {
  vi.useFakeTimers();
  const executed = { id: 'cmd-9', status: 'applied', execution_status: 'succeeded', execution_cut: 58, target_cut: 56,
    execution_result: { injection_point: 'restaurant_delay' } };
  const api = recordedApi([]);
  const request=vi.mocked(api.request),normal=request.getMockImplementation()!;
  let finishFirst!: (value:unknown)=>void;
  const admissions=vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{finishFirst=resolve;}))
    .mockResolvedValue({cursor:116,receipts:[{id:'cmd-9',status:'admitted',execution_status:'admitted',target_cut:56}]});
  request.mockImplementation((path,options)=>path.includes('/admissions?')?admissions(path,options):normal(path,options));
  await act(async()=>{mount(api);});
  await act(async () => { await vi.advanceTimersByTimeAsync(26000); });
  expect(admissions).toHaveBeenCalledTimes(1);
  expect(admissions.mock.calls[0][0]).toBe('/v1/runs/service%2Frun%20id/admissions?after=0');
  await act(async()=>finishFirst({cursor:116,receipts:[executed]}));
  const receipt = screen.getByText('Admission #1').closest('article')!;
  expect(receipt).toHaveTextContent('Executed.');
  expect(receipt).toHaveTextContent('Execution · succeeded');
  await act(async () => { await vi.advanceTimersByTimeAsync(4999); });
  expect(admissions).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(admissions).toHaveBeenCalledTimes(2);
  expect(admissions.mock.calls[1][0]).toBe('/v1/runs/service%2Frun%20id/admissions?after=116');
  expect(screen.getByText('Admission receipts · 1')).toBeVisible();
  expect(receipt).toHaveTextContent('Execution · succeeded');
  expect(receipt).not.toHaveTextContent('awaiting execution');
  expect(within(receipt).getByRole('button', { name: 'cut 58' })).toBeVisible();
});

it('T3-R6-002: backs off to 30 seconds while hidden, then reads immediately on return',async()=>{
  vi.useFakeTimers();
  const api=recordedApi([]),request=vi.mocked(api.request);
  const reads=()=>request.mock.calls.filter(([path])=>path.includes('/admissions?')).length;
  await act(async()=>{mount(api);});
  expect(reads()).toBe(1);
  vi.mocked(vi.spyOn(document,'hidden','get')).mockReturnValue(true);
  fireEvent(document,new Event('visibilitychange'));
  await act(async()=>{await vi.advanceTimersByTimeAsync(29999);});
  expect(reads()).toBe(1);
  await act(async()=>{await vi.advanceTimersByTimeAsync(1);});
  expect(reads()).toBe(2);
  vi.mocked(vi.spyOn(document,'hidden','get')).mockReturnValue(false);
  await act(async()=>{fireEvent(document,new Event('visibilitychange'));});
  expect(reads()).toBe(3);
});

it('T3-R6-002: rejects malformed/backward cursors visibly and retries from the last successful cursor',async()=>{
  vi.useFakeTimers();
  const api=recordedApi([]),request=vi.mocked(api.request),normal=request.getMockImplementation()!;
  const reads=vi.fn().mockResolvedValueOnce({cursor:10,receipts:[]}).mockResolvedValueOnce({cursor:9,receipts:[]})
    .mockResolvedValue({cursor:12,receipts:[{id:'new',status:'admitted'}]});
  request.mockImplementation((path,options)=>path.includes('/admissions?')?reads(path):normal(path,options));
  await act(async()=>{mount(api);});
  await act(async()=>{await vi.advanceTimersByTimeAsync(5000);});
  expect(screen.getByRole('alert')).toHaveTextContent('Command history response is malformed.');
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'Retry'}));});
  expect(reads.mock.calls[2][0]).toContain('after=10');
  expect(screen.getByText('Admission receipts · 1')).toBeVisible();
});

it('T3-R5-003: merges the POST and journal receipt by command id, preserving failed execution evidence', () => {
  const failed = { id: 'cmd-9', status: 'applied', stored: true, execution_status: 'failed', execution_cut: 58, detail: 'Engine rejected the event' };
  const rows = mergeAdmissionReceipts([{ command_id: 'cmd-9', status: 'accepted', disposition: 'accepted' }], [failed]);
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject(failed);
  expect(mergeAdmissionReceipts(rows, [{ command_id: 'cmd-9', status: 'accepted', disposition: 'accepted' }])[0]).toMatchObject(failed);
  expect(mergeAdmissionReceipts(rows, [])).toEqual(rows);
});
