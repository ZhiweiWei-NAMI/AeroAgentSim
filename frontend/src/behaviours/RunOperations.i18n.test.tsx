import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { ConsoleNotifications } from '../console/Notifications';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { RunOperations } from './RunOperations';
import type { ComponentProps } from 'react';

// Typed-form rendering is covered by its owner; keep these tests about run controls and receipts.
vi.mock('./TypedPayloadForm', () => ({ TypedPayloadForm: () => <div data-testid="typed-payload" /> }));
beforeEach(() => {
  // jsdom cannot measure pseudo-element scrollbars; regular computed styles still apply.
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => computedStyle(element));
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });

const point = { id: 'alarm', stream_id: 'operator', command: 'aas.runtime.inject_event', target: 'behaviour.actual', emits: 'traffic.alarm.actual' };
function runStore() {
  const store = new TemporalFeedStore({ ...fixtureHeader, kernelRunId: 'kernel.actual', messages: [
    { id: point.command, kind: 'command', schema: { type: 'record', members: {}, required: [] } },
    { id: point.emits, kind: 'event', schema: { type: 'record', members: { reason: { type: 'string', default: 'Operator report' }, request_id: { type: 'string' }, source_cut: { type: 'integer' } }, required: [] } },
  ] });
  store.ingest(emptyCommit(7)); store.seek('0', 7);
  return store;
}
function configuration() {
  return { scenario: { id: 'traffic-accident', run: { advance_ns: 1_000_000_000, until_ns: 90_000_000_000 }, ingress_streams: [{ id: 'operator', initial_watermark_ns: 0 }], behaviours: [{ document: { injection_points: [point] } }] } };
}
function mount(api: RunsApi, store: TemporalFeedStore, locale = 'zh-CN', props: Partial<ComponentProps<typeof RunOperations>> = {}) {
  window.localStorage.setItem('aeroagentsim.locale', locale);
  return render(<I18nProvider><MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><ConsoleNotifications><RunOperations api={api} runId="service/run id" store={store} onSelect={() => {}} onSeek={() => {}} mode="live" {...props} /></ConsoleNotifications></MemoryRouter></I18nProvider>);
}
const labels = {
  'zh-CN': { operations: '运行操作', pause: '暂停仿真', resume: '继续仿真', stop: '停止仿真', ingress: '提交类型化输入', pauseToast: '已请求暂停仿真。', resumeToast: '已请求继续仿真。', stopToast: '已请求停止仿真。', acceptedToast: '事件已受理，请在时间轴中查看响应。', advanced: '高级', details: '详情' },
  'en-US': { operations: 'Run operations', pause: 'Pause simulation', resume: 'Resume simulation', stop: 'Stop simulation', ingress: 'Submit typed ingress', pauseToast: 'Simulation pause requested.', resumeToast: 'Simulation resume requested.', stopToast: 'Simulation stop requested.', acceptedToast: 'Event accepted. Follow its response in the timeline.', advanced: 'Advanced', details: 'Details' },
};

it.each(['zh-CN', 'en-US'] as const)('localizes event notices and admission explanations while preserving schema, codes and payload (%s)', async locale => {
  const api = new RunsApi('http://api'), bodies: unknown[] = [], text = labels[locale];
  let receipt: unknown = { disposition: 'accepted', status: 'accepted', code: 'raw_ACCEPT' };
  vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/admissions')) return [];
    if (path.endsWith('/configuration')) return configuration();
    if (path.includes('/commits?')) return { status: 'running', commits: [], next: 8 };
    if (path.endsWith('/inputs')) return {status:'running',watermarks:{operator:0},pause_requested:false};
    if (path.endsWith('/ingress')) { bodies.push(JSON.parse(String(options?.body))); return receipt; }
    if (path.endsWith('/watermark')) return { watermark_ns: JSON.parse(String(options?.body)).watermark_ns };
    throw Error(`Unexpected request ${path}`);
  });
  mount(api, runStore(), locale);
  await waitFor(() => expect(screen.getByRole('button', { name: text.ingress })).toBeEnabled());
  expect(screen.getByTestId('injection-card')).not.toHaveTextContent(/configured plugin|配置的插件/);
  expect(screen.queryByText('Inject an event and follow the simulation’s response.')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: text.ingress }));
  await waitFor(() => expect(screen.getByText(text.acceptedToast)).toBeVisible());
  expect(bodies[0]).toMatchObject({ schema: point.command, target: point.target, stream_id: 'operator', at_ns: 1, payload: { injection_point: 'alarm', payload: { reason: 'Operator report', source_cut: 7, request_id: expect.any(String) } }, idempotency_key: expect.any(String) });
  expect(screen.getByText(locale === 'zh-CN' ? '事件已受理，后续处理结果请查看执行回执。' : 'Event accepted for processing. The execution receipts show what happened next.')).toBeVisible();
  await waitFor(() => expect(screen.getByRole('button', { name: text.ingress })).toBeEnabled());
  receipt = { disposition: 'rejected', status: 'rejected', code: 'raw_REJECT' };
  fireEvent.click(screen.getByRole('button', { name: text.ingress }));
  await waitFor(() => expect(screen.getByText(locale === 'zh-CN' ? '事件被拒绝（raw_REJECT），未执行。' : 'Event rejected (raw_REJECT): it was not executed.')).toBeVisible());
  const admission = screen.getByText(locale === 'zh-CN' ? '受理回执 · 2' : 'Admission receipts · 2').closest('details')!;
  fireEvent.click(within(admission).getAllByRole('button', { name: locale === 'zh-CN' ? /详\s*情/ : text.details })[1]);
  await waitFor(() => expect(screen.getByRole('dialog')).toBeVisible());
  fireEvent.click(within(screen.getByRole('dialog')).getByText(locale === 'zh-CN' ? '原始记录' : 'Raw record', { selector: 'summary' }));
  expect(within(screen.getByRole('dialog')).getByText(/"code"\s*:\s*"raw_REJECT"/)).toBeVisible();
});

it('explains missing operator timing, localizes transport validation and keeps generated idempotency keys out of authored fields', async () => {
  const api = new RunsApi('http://api'), bodies: string[] = [];
  vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/admissions')) return [];
    if (path.endsWith('/configuration')) return { scenario: { id: 'external', behaviours: [{ document: { injection_points: [point] } }] } };
    if (path.includes('/commits?')) return { status: 'running', commits: [], next: 8 };
    if (path.endsWith('/inputs')) return {status:'running',watermarks:{operator:0},pause_requested:false};
    if (path.endsWith('/ingress')) { bodies.push(String(options?.body)); return { disposition: 'accepted' }; }
    throw Error(`Unexpected request ${path}`);
  });
  mount(api, runStore());
  await waitFor(() => expect(screen.getByTestId('inject-alarm')).toBeEnabled());
  const submit = screen.getByTestId('inject-alarm');
  expect(screen.getByText('请在高级设置中填写必需的时序字段后再提交。')).toBeVisible();
  fireEvent.click(submit);
  await waitFor(() => expect(within(screen.getByRole('region', { name: '运行操作' })).getByRole('alert')).toHaveTextContent('at_ns：必须明确填写'));
  expect(bodies).toHaveLength(0);
  const card = screen.getByTestId('injection-card');
  fireEvent.click(within(card).getByTestId('details-trigger'));
  const dialog = await screen.findByRole('dialog');
  const transport = { at_ns: '9007199254740993', clock_id: 'raw-clock', mapping_id: 'raw-map', numerator: '9007199254740993', denominator: '1' };
  for (const [field, value] of Object.entries(transport)) fireEvent.change(within(dialog).getByLabelText(`输入 ${field}`), { target: { value } });
  expect(within(dialog).getByLabelText('输入 idempotency_key')).toHaveValue('');
  fireEvent.keyDown(document, { key: 'Escape' });
  await waitFor(() => expect(dialog).not.toBeVisible());
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toContain('"at_ns":9007199254740993');
  expect(bodies[0]).toContain('"numerator":9007199254740993');
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(JSON.parse(bodies[1]).idempotency_key).not.toBe(JSON.parse(bodies[0]).idempotency_key);
  fireEvent.click(within(card).getByTestId('details-trigger'));
  expect(within(screen.getByRole('dialog')).getByLabelText('输入 idempotency_key')).toHaveValue('');
});

it('localizes record pagination and seeking while keeping subjects and generation exact', async () => {
  const api = new RunsApi('http://api');
  vi.spyOn(api, 'request').mockImplementation(async path => path.endsWith('/configuration') ? { scenario: { id: 'external', behaviours: [] } } : { status: 'running', commits: [], next: 8 });
  const subject = { id: 'subject.raw', generation: '9007199254740993' };
  const store = new TemporalFeedStore(fixtureHeader);
  store.ingest({ ...emptyCommit(7), messages: Array.from({ length: 51 }, (_, index) => ({ id: `raw.message.${index}`, schemaId: 'traffic.record.actual', source: 'source.raw', at: { ns: '0', microstep: 0 }, kind: 'event' as const, subjects: [subject], payload: { reason: 'Operator report' } })), receipts: Array.from({ length: 101 }, (_, index) => ({ commandId: `command.raw.${index}`, status: 'success', result: {} })) });
  store.seek('0', 7);
  const select = vi.fn(), seek = vi.fn();
  mount(api, store, 'zh-CN', { onSelect: select, onSeek: seek });
  await waitFor(() => expect(screen.queryByText('正在检查运行状态…')).not.toBeInTheDocument());
  fireEvent.click(screen.getByText('当前时刻的决策 / LangGraph 与类型化事件记录'));
  expect(screen.getByText('51 条记录 · 第 1 / 2 页')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '下一页记录' }));
  expect(screen.getByText('51 条记录 · 第 2 / 2 页')).toBeVisible();
  expect(screen.getByRole('button', { name: '下一页记录' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'subject.raw · 代次 9007199254740993' }));
  expect(select).toHaveBeenCalledWith(subject);
  fireEvent.click(screen.getByRole('button', { name: /跳(?:转)?到记录时刻/ }));
  expect(seek).toHaveBeenCalledWith(7);
  fireEvent.click(screen.getByText('当前时刻的执行回执 · 101'));
  expect(screen.getByText('执行 #2')).toBeVisible();
  expect(screen.getByText('执行 #101')).toBeVisible();
  expect(screen.queryByText('执行 #1')).toBeNull();
});

it.each(['zh-CN', 'en-US'] as const)('leaves simulation control to the top bar and makes replay read-only (%s)', async locale => {
  const api = new RunsApi('http://api'), text = labels[locale];
  const request = vi.spyOn(api, 'request').mockImplementation(async path => path.endsWith('/configuration') ? configuration() : path.endsWith('/inputs') ? { status: 'running', watermarks: { operator: 0 }, pause_requested: false } : { status: 'running' });
  const view = mount(api, runStore(), locale, { mode: 'replay' });
  await waitFor(() => expect(screen.getByTestId('inject-alarm')).toBeVisible());
  [text.pause, text.resume, text.stop].forEach(name => expect(screen.queryByRole('button', { name })).toBeNull());
  expect(screen.getByRole('button', { name: text.ingress })).toBeDisabled();
  fireEvent.keyDown(document, { key: ' ' });
  await act(async () => {});
  expect(request.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
  view.unmount();
});
it('locks duplicate submissions and rechecks real run status before admitting events', async () => {
  const api = new RunsApi('http://api'); let status = 'running', finish!: (value: unknown) => void;
  const request = vi.spyOn(api, 'request').mockImplementation(async path => {
    if (path.endsWith('/admissions')) return [];
    if (path.endsWith('/configuration')) return configuration();
    if (path.includes('/commits?')) return { status };
    if (path.endsWith('/inputs')) return { status, watermarks: { operator: 0 }, pause_requested: false };
    if (path.endsWith('/ingress')) return new Promise(resolve => { finish = resolve; });
    throw Error(path);
  });
  mount(api, runStore(), 'en-US'); const submit = await screen.findByTestId('inject-alarm');
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit); fireEvent.click(submit);
  await waitFor(() => expect(request.mock.calls.filter(([path]) => path.endsWith('/ingress'))).toHaveLength(1));
  expect(submit).toBeDisabled();
  await act(async () => { finish({ disposition: 'accepted' }); });
  await waitFor(() => expect(submit).toBeEnabled()); status = 'completed'; fireEvent.click(submit);
  await waitFor(() => expect(submit).toBeDisabled());
  expect(request.mock.calls.filter(([path]) => path.endsWith('/ingress'))).toHaveLength(1);
});
