import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {I18nProvider, useI18n} from '../i18n/I18nProvider';
import {TemplateApi, type BusinessEvent, type ScenarioTemplate} from './api';
import {EventFeed} from './EventFeed';
import {KpiPanel} from './KpiPanel';
import {CompareRuns} from './CompareRuns';
import {eventNarrative} from './presentation';
import {categoryMetadata, severityLabel} from './categories';
import {metricHelp} from './metric-help';
import {CAUSE_TRACE_EVENT} from '../trace/bus';

vi.mock('../viewport/ViewerPresentation', () => ({ViewerPresentation: () => <div />}));
beforeEach(() => {
  localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  Object.defineProperty(window, 'matchMedia', {writable: true, value: vi.fn(() => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}}))});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
function LocaleToggle() { const {locale, setLocale} = useI18n(); return <button aria-label="switch-language" onClick={() => setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')}>switch</button>; }
function renderLocalized(ui: React.ReactNode) { return render(<I18nProvider><MemoryRouter>{ui}<LocaleToggle /></MemoryRouter></I18nProvider>); }
const template: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1', id: 'bilingual', title: 'Delivery scenario', title_zh: '配送场景',
  summary: 'Deliver parcels.', summary_zh: '将包裹送达客户。', category: 'logistics', difficulty: 'beginner', duration_s: 120, scenario: 'delivery.yaml',
  observe: ['Watch the deliveries.'], observe_zh: ['观察配送进度。'], try: ['Change the mode.'], try_zh: ['试试加急配送。'], kpis: ['tasks_completed'],
  knobs: [
    {id: 'count', label: 'Number of drones', label_zh: '无人机数量', type: 'integer', default: 2, min: 1, max: 20},
    {id: 'mode', label: 'Delivery mode', label_zh: '配送方式', description: 'Select a mode.', description_zh: '选择配送方式。', type: 'choice', default: 'normal', options: ['normal', 'rush'], options_zh: ['普通', '加急']},
    {id: 'enabled', label: 'Allow charging', label_zh: '允许充电', type: 'boolean', default: true},
  ],
};
const catalog = {allocation: [{id: 'nearest', label: 'Nearest drone', label_zh: '最近的无人机', description: 'Choose the nearest idle drone.', description_zh: '选择最近的空闲无人机。'}, {id: 'auction', label: 'Auction', label_zh: '竞价分配', description: 'Vehicles bid.', description_zh: '载具参与竞价。'}], path_planning: [], conflict_resolution: [], charging: []};
function mockFetch(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    const found = Object.entries(routes).find(([path]) => url.endsWith(path));
    return found ? new Response(JSON.stringify(found[1]), {status: 200}) : new Response('not found', {status: 404});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}





it('localizes metric labels and explanations without changing metric ids, measurements or units', () => {
  renderLocalized(<KpiPanel values={{tasks_completed: {value: 4, unit: 'count', label: 'Tasks completed'}, mean_task_latency_s: {value: 3, unit: 's', label: 'Mean task latency'}, custom_metric: {value: null, unit: 'kJ', label: 'Special measurement', label_zh: '特殊测量'}}} ids={['tasks_completed', 'mean_task_latency_s', 'custom_metric']} series={[]} />);
  expect(screen.getByText('已完成任务')).toBeInTheDocument();
  expect(screen.getByText('平均任务用时')).toBeInTheDocument();
  expect(screen.getByText('3 s')).toBeInTheDocument();
  expect(screen.getByText('特殊测量')).toBeInTheDocument();
  expect(screen.getByTitle('未测量')).toHaveTextContent('—');
  fireEvent.click(screen.getByRole('button', {name: '了解“平均任务用时”'}));
  expect(screen.getByText('从任务出现到完成所需的平均时间。越短越好。')).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: '了解“特殊测量”'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: 'switch-language'}));
  expect(screen.getByText('Mean task latency')).toBeInTheDocument();
  expect(screen.getByText('Average time between a task appearing and being completed. Lower is better.')).toBeInTheDocument();
});

it('uses Chinese feed narratives and enum labels, with safe identity replacement and English fallback', () => {
  const event: BusinessEvent = {at_ns: '1000000000', category: 'logistics', severity: 'notice', text: 'drone.1 delivered a parcel.', text_zh: 'drone.1 已送达包裹。', entities: ['drone.1']};
  const fallback: BusinessEvent = {at_ns: '2000000000', category: 'weather', severity: 'warning', text: 'Rain was recorded.', entities: []};
  const onSelect = vi.fn();
  const names = new Map([['drone.1', '无人机一号']]);
  renderLocalized(<EventFeed events={[event, fallback]} entityNames={names} onSelect={onSelect} />);
  expect(screen.getByText('无人机一号 已送达包裹。')).toBeInTheDocument();
  expect(screen.getByText('Rain was recorded.')).toBeInTheDocument();
  expect(document.querySelector('.newc-severity')).toHaveTextContent('提示');
  fireEvent.click(screen.getByRole('button', {name: '物流配送'}));
  expect(screen.queryByText('Rain was recorded.')).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: /无人机一号 已送达包裹/}));
  expect(onSelect).toHaveBeenCalledWith(event);
  expect(eventNarrative(event, 'zh-CN', names)).toBe('无人机一号 已送达包裹。');
  expect(event.text).toBe('drone.1 delivered a parcel.');
  const causes: unknown[] = [];
  const listener = (event: Event) => causes.push((event as CustomEvent).detail);
  window.addEventListener(CAUSE_TRACE_EVENT, listener);
  fireEvent.click(screen.getByRole('button', {name: '为什么会发生这件事？'}));
  fireEvent.click(screen.getByRole('button', {name: 'switch-language'}));
  fireEvent.click(screen.getByRole('button', {name: 'Why did this happen?'}));
  window.removeEventListener(CAUSE_TRACE_EVENT, listener);
  expect(causes).toEqual([0, 1].map(() => ({kind: 'event', atNs: event.at_ns, text: event.text, entities: event.entities})));
  expect(categoryMetadata('airspace', 'zh-CN').label).toBe('空域');
  expect(severityLabel('critical', 'zh-CN')).toBe('紧急');
  expect(metricHelp('not_a_metric', 'zh-CN')).toBeUndefined();
});

it('uses additive titles and choice labels throughout a comparison without altering compared settings', async () => {
  const runs = [{id: 'r-1', title: 'Normal run', title_zh: '普通配送', template_id: 'bilingual', kpis: {tasks_completed: {value: 4, unit: 'count', label: 'Tasks completed'}}, knobs: {mode: 'normal'}, algorithms: {}}, {id: 'r-2', title: 'Rush run', title_zh: '加急配送', template_id: 'bilingual', kpis: {tasks_completed: {value: 5, unit: 'count', label: 'Tasks completed'}}, knobs: {mode: 'rush'}, algorithms: {}}];
  const fetchMock = mockFetch({'/v1/runs/compare?ids=r-1,r-2': {runs}, '/v1/templates': [template], '/v1/algorithms': catalog});
  renderLocalized(<CompareRuns api={new TemplateApi('http://api.test')} ids={['r-1', 'r-2']} />);
  expect(await screen.findByRole('heading', {name: '运行 1 · 普通配送'})).toBeInTheDocument();
  expect(screen.getByRole('heading', {name: '运行 2 · 加急配送'})).toBeInTheDocument();
  expect(await screen.findByText('配送方式')).toBeInTheDocument();
  expect(screen.getByText('普通')).toBeInTheDocument();
  expect(screen.getByText('加急')).toBeInTheDocument();
  expect(screen.getAllByText('已完成任务')).toHaveLength(2);
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('compare?ids=r-1,r-2'))).toBe(true);
  expect(runs.map(run => run.knobs.mode)).toEqual(['normal', 'rush']);
});
