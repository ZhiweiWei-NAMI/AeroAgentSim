import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { ConsoleNotifications } from '../console/Notifications';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { RunOperations } from './RunOperations';

// Typed-form rendering is covered by its owner; these tests cover receipt persistence.
vi.mock('./TypedPayloadForm', () => ({ TypedPayloadForm: () => <div data-testid="typed-payload" /> }));
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); });

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
    if(path.endsWith('/admissions'))return rows;
    if(path.endsWith('/configuration'))return {scenario:{}};
    if(path.includes('/commits?'))return {status:'paused',commits:[],next:8};
    throw Error(`Unexpected request ${path}`);
  });
  return api;
}

it('RUN-008: restores journaled admission receipts on mount so a refresh keeps the admitted operation visible', async () => {
  const api = new RunsApi('http://api');
  vi.spyOn(api, 'request').mockImplementation(async path => {
    if (String(path).endsWith('/admissions')) return [{ id: 'cmd-9', submitted_at: '2026-10-13T08:00:00Z', target_cut: 130, kind: 'ingress', status: 'admitted' }];
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

it('shows admission query errors instead of implying that no receipts exist',async()=>{
  const api=recordedApi([]),request=vi.mocked(api.request),normal=request.getMockImplementation()!;
  request.mockImplementation((path,options)=>path.endsWith('/admissions')?Promise.reject(Error('HTTP 500: Journal unavailable')):normal(path,options));
  mount(api);
  const error=await screen.findByText('HTTP 500: Journal unavailable');expect(error).toHaveAttribute('role','alert');expect(error).toBeVisible();
  expect(screen.queryByText('No admission receipts yet.')).toBeNull();
});
