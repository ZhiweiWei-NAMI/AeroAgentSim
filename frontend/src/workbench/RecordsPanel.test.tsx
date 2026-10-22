import { Profiler } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordsPanel } from './RecordsPanel';
import { I18nProvider } from '../i18n/I18nProvider';
import type { WorkbenchApi } from './api';
import type { ExperimentReport } from '../experiments/api';
afterEach(() => { cleanup(); vi.useRealTimers(); localStorage.clear(); window.history.replaceState({}, '', '/'); });
const experimentReport: ExperimentReport = {
  id: 'exp-1',
  contract: 'aeroagentsim.experiment/v1',
  template_id: 'parcel-delivery',
  template_title: 'Urban parcel delivery',
  status: 'interrupted',
  seeds: 2,
  jobs: 1,
  horizon_s: 300,
  knobs: {},
  vary: { allocator: ['nearest', 'auction'] },
  total: 4,
  completed: 1,
  failed: 2,
  error: 'experiment interrupted by a server restart',
  runs: [
    { config_id: 'config-0', seed: 0, run: '/runs/a', run_id: 'run-a', status: 'completed', error: null, kpis: { tasks_completed: 4 }, kpi_meta: { tasks_completed: { unit: 'count', label: 'Tasks completed' } } },
    { config_id: 'config-0', seed: 1, run: null, run_id: null, status: 'faulted', error: 'network failed before the run started', kpis: {}, kpi_meta: {} },
    { config_id: 'config-1', seed: 0, run: '/runs/c', run_id: 'run-c', status: 'interrupted', error: 'experiment interrupted by a server restart', kpis: {}, kpi_meta: {} },
  ],
  table: [
    { config_id: 'config-0', knobs: { allocator: 'nearest' }, metrics: { tasks_completed: { mean: 4, std: null, n: 1, unit: 'count', label: 'Tasks completed', best: true } } },
    { config_id: 'config-1', knobs: { allocator: 'auction' }, metrics: {} },
  ],
};
it('limits comparison to four without silently replacing a selected run', async () => {
  const compare = vi.fn();
  const rows = Array.from({ length: 5 }, (_, i) => ({ id: `run-${i}`, status: 'stopped', scenario: 'food-delivery', studio_workspace: 'project' }));
  render(<RecordsPanel api={{ runs: vi.fn().mockResolvedValue(rows) } as unknown as WorkbenchApi} refreshKey={0} onOpen={vi.fn()} onCompare={compare} onDerive={vi.fn()} onClose={vi.fn()} />);
  const checks = await screen.findAllByRole('checkbox');
  for (const checkbox of checks.slice(0, 4)) fireEvent.click(checkbox);
  expect(checks[4]).toBeDisabled(); expect(checks[0]).toBeChecked();
  fireEvent.click(screen.getByTestId('wb-compare'));
  expect(compare).toHaveBeenCalledWith(['run-4', 'run-3', 'run-2', 'run-1']);
  fireEvent.click(checks[0]); expect(checks[4]).toBeEnabled();
});
it('shows loading and actual request failures instead of an empty run list', async () => {
  let reject!: (error: Error) => void;
  render(<RecordsPanel api={{ runs: () => new Promise((_, fail) => { reject = fail; }) } as unknown as WorkbenchApi} refreshKey={0} onOpen={vi.fn()} onCompare={vi.fn()} onDerive={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading run records');
  reject(Error('records unavailable'));
  expect(await screen.findByRole('alert')).toHaveTextContent('records unavailable');
  expect(screen.queryByText('No runs yet')).toBeNull();
});
it('keeps record order immutable and skips unchanged polls while showing real finalization changes', async () => {
  vi.useFakeTimers();
  const source = [{ id: 'older', status: 'completed', scenario: 'food' }, { id: 'newer', status: 'completed', scenario: 'food', results_final: false }];
  const runs = vi.fn(async () => source.map(row => ({ ...row }))), commits = vi.fn();
  await act(async () => {
    render(<Profiler id="records" onRender={commits}><RecordsPanel api={{ runs } as unknown as WorkbenchApi} refreshKey={0}
      onOpen={vi.fn()} onCompare={vi.fn()} onDerive={vi.fn()} onClose={vi.fn()} /></Profiler>);
  });
  expect(screen.getAllByRole('checkbox')[0]).toHaveAccessibleName('Select newer for comparison');
  expect(source.map(row => row.id)).toEqual(['older', 'newer']);
  expect(screen.getByText('finalizing…')).toBeVisible();
  const count = commits.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(commits).toHaveBeenCalledTimes(count);
  source[1].results_final = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(screen.queryByText('finalizing…')).toBeNull();
  expect(commits.mock.calls.length).toBeGreaterThan(count);
});

it.each([
  ['zh-CN','Restaurant meal delivery','餐厅外卖配送 · 变体'],
  ['en-US','Restaurant meal delivery','Restaurant meal delivery · variant'],
  ['zh-CN','My authored project','My authored project · 变体'],
])('T4-R6-003: derives a localized default while preserving authored names (%s / %s)',async(locale,name,expected)=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const source={id:'source',status:'completed',scenario:'food-delivery',workspace_name:name,template_title:'Restaurant meal delivery',template_title_zh:'餐厅外卖配送'};
  const derive=vi.fn().mockResolvedValue(undefined);
  render(<I18nProvider><RecordsPanel api={{runs:vi.fn().mockResolvedValue([source])} as unknown as WorkbenchApi} refreshKey={0}
    onOpen={vi.fn()} onCompare={vi.fn()} onDerive={derive} onClose={vi.fn()}/></I18nProvider>);
  const button=await screen.findByRole('button',{name:locale==='zh-CN'?'修改后再试':'Modify & retry'});
  await act(async()=>{fireEvent.click(button);});
  expect(derive).toHaveBeenCalledWith(source,expected);
});

it('reads a bookmarked experiment id from the URL and shows reported failed and interrupted cells', async () => {
  window.history.replaceState({}, '', '/?records=1&experiment=exp-1');
  const request = vi.fn(async (path: string) => {
    if (path === '/v1/experiments/exp-1') return experimentReport;
    throw Error(`unexpected request ${path}`);
  });
  render(<RecordsPanel api={{ runs: vi.fn().mockResolvedValue([]), request } as unknown as WorkbenchApi} refreshKey={0}
    onOpen={vi.fn()} onCompare={vi.fn()} onDerive={vi.fn()} onClose={vi.fn()} />);
  const results = await screen.findByTestId('experiment-results');
  expect(within(results).getAllByText('Interrupted').length).toBeGreaterThan(0);
  expect(within(results).getByText('Faulted')).toBeInTheDocument();
  expect(within(results).getByText('unavailable')).toBeInTheDocument();
  expect(request).toHaveBeenCalledWith('/v1/experiments/exp-1', expect.objectContaining({ signal: expect.anything() }));
  expect(screen.getByTestId('wb-experiments-toggle')).toHaveAttribute('aria-expanded', 'true');
});

it('opens the experiment entry from Records without changing the run list', async () => {
  const templates = vi.fn().mockResolvedValue([]);
  render(<RecordsPanel api={{ runs: vi.fn().mockResolvedValue([]), templates } as unknown as WorkbenchApi} refreshKey={0}
    onOpen={vi.fn()} onCompare={vi.fn()} onDerive={vi.fn()} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByTestId('wb-experiments-toggle'));
  expect(await screen.findByTestId('experiment-panel')).toBeInTheDocument();
  expect(await screen.findByText('No templates are available on this server.')).toBeInTheDocument();
  expect(templates).toHaveBeenCalledTimes(1);
});
