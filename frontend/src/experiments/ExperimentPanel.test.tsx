import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ScenarioTemplate } from '../newcomers/api';
import type { WorkbenchApi } from '../workbench/api';
import type { ExperimentReport } from './api';
import { ExperimentPanel } from './ExperimentPanel';

const template: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1',
  id: 'parcel-delivery',
  title: 'Urban parcel delivery',
  title_zh: '城市包裹配送',
  summary: 'Drones carry parcels.',
  category: 'logistics',
  difficulty: 'beginner',
  duration_s: 300,
  scenario: 'scenario.yaml',
  observe: ['Orders appear.', 'Drones route around the square.'],
  try: ['Change the allocator.', 'Add drones.'],
  knobs: [
    { id: 'fleet_size', label: 'Number of drones', type: 'integer', default: 4, min: 1, max: 10 },
    { id: 'allocator', label: 'Task allocation algorithm', type: 'choice', default: 'nearest', options: ['nearest', 'auction'] },
    { id: 'seed', label: 'Seed', type: 'integer', default: 1, pointer: '/run/seed' },
  ],
  kpis: ['tasks_completed'],
};

const created: ExperimentReport = {
  id: 'exp-1',
  contract: 'aeroagentsim.experiment/v1',
  template_id: 'parcel-delivery',
  template_title: 'Urban parcel delivery',
  status: 'created',
  seeds: 3,
  jobs: 1,
  horizon_s: null,
  knobs: {},
  vary: { fleet_size: [4, 8], allocator: ['nearest', 'auction'] },
  total: 12,
  completed: 0,
  failed: 0,
  error: null,
  runs: [],
  table: [
    { config_id: 'config-0', knobs: { fleet_size: 4, allocator: 'nearest' }, metrics: {} },
    { config_id: 'config-1', knobs: { fleet_size: 4, allocator: 'auction' }, metrics: {} },
    { config_id: 'config-2', knobs: { fleet_size: 8, allocator: 'nearest' }, metrics: {} },
    { config_id: 'config-3', knobs: { fleet_size: 8, allocator: 'auction' }, metrics: {} },
  ],
};

const followed: ExperimentReport = {
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
    { config_id: 'config-0', seed: 0, run: '/runs/a', run_id: 'run-a', status: 'completed', error: null, kpis: { tasks_completed: 4 }, kpi_meta: {} },
    { config_id: 'config-0', seed: 1, run: null, run_id: null, status: 'faulted', error: 'worker failed', kpis: {}, kpi_meta: {} },
    { config_id: 'config-1', seed: 0, run: '/runs/c', run_id: 'run-c', status: 'interrupted', error: 'server restart', kpis: {}, kpi_meta: {} },
  ],
  table: [
    {
      config_id: 'config-0',
      knobs: { allocator: 'nearest' },
      metrics: {
        tasks_completed: { mean: 4, std: null, n: 1, unit: 'count', label: 'Tasks completed', best: true },
      },
    },
    { config_id: 'config-1', knobs: { allocator: 'auction' }, metrics: {} },
  ],
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.history.replaceState({}, '', '/');
});

function apiWith(request: unknown): WorkbenchApi {
  return { templates: vi.fn().mockResolvedValue([template]), request } as unknown as WorkbenchApi;
}

// The axis controls render after the template's knobs load; wait for them
// rather than racing the first render.
async function varyFleetSize(values: string) {
  const vary = await screen.findByRole('checkbox', { name: 'Vary Number of drones' });
  await waitFor(() => expect(vary).toBeEnabled());
  fireEvent.click(vary);
  fireEvent.change(await screen.findByTestId('experiment-values-fleet_size'), { target: { value: values } });
}

describe('ExperimentPanel', () => {
  it('previews typed axes and seeds, confirms once and posts the plan once', async () => {
    const request = vi.fn(async (path: string, init?: { method?: string }) => {
      if (path === '/v1/templates/parcel-delivery/experiments' && init?.method === 'POST') return created;
      if (path === '/v1/experiments/exp-1') return created;
      throw Error(`unexpected request ${path}`);
    });
    const onExperimentId = vi.fn();
    render(<ExperimentPanel api={apiWith(request)} onExperimentId={onExperimentId} onOpen={vi.fn()} onCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('experiment-template')).toHaveValue('parcel-delivery'));
    expect(screen.getByRole('checkbox', { name: 'Vary Seed' })).toBeDisabled();

    await varyFleetSize('4, 8');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Vary Task allocation algorithm' }));
    expect(screen.getByTestId('experiment-preview')).toHaveTextContent('12');
    expect(screen.getByTestId('experiment-preview')).toHaveTextContent('4 configurations × 3 seeds');

    fireEvent.click(screen.getByTestId('experiment-review'));
    expect(screen.getByTestId('experiment-review-panel')).toHaveTextContent('Create 12 runs');
    fireEvent.click(screen.getByTestId('experiment-confirm'));

    await waitFor(() => expect(screen.getByTestId('experiment-results')).toBeInTheDocument());
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith('/v1/templates/parcel-delivery/experiments', expect.objectContaining({
      method: 'POST',
      body: {
        vary: { fleet_size: [4, 8], allocator: ['nearest', 'auction'] },
        seeds: 3,
        jobs: 1,
      },
    }));
    expect(onExperimentId).toHaveBeenCalledWith('exp-1');
  });

  it('refuses a plan above the visible browser limit before any POST', async () => {
    const request = vi.fn();
    render(<ExperimentPanel api={apiWith(request)} onExperimentId={vi.fn()} onOpen={vi.fn()} onCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('experiment-template')).toHaveValue('parcel-delivery'));
    await varyFleetSize('1,2,3,4,5,6,7,8,9,10');
    expect(screen.getByTestId('experiment-preview')).toHaveTextContent('30');
    expect(screen.getByTestId('experiment-preview')).toHaveTextContent(/browser limit/i);
    expect(screen.getByText(/browser limit is 24/)).toBeInTheDocument();
    expect(screen.getByTestId('experiment-review')).toBeDisabled();
    expect(request).not.toHaveBeenCalled();
  });

  it('follows an existing id and shows failed/interrupted cells and null as unavailable', async () => {
    const request = vi.fn(async (path: string) => {
      if (path === '/v1/experiments/exp-1') return followed;
      throw Error(`unexpected request ${path}`);
    });
    const onOpen = vi.fn(), onCompare = vi.fn();
    render(<ExperimentPanel api={apiWith(request)} experimentId="exp-1" onExperimentId={vi.fn()} onOpen={onOpen} onCompare={onCompare} />);
    const results = await screen.findByTestId('experiment-results');
    expect(within(results).getAllByText('Interrupted').length).toBeGreaterThan(0);
    expect(within(results).getByText('Faulted')).toBeInTheDocument();
    const metric = within(results).getByTestId('experiment-metric-config-0-tasks_completed');
    const cells = metric.querySelectorAll('td');
    expect(cells[1]).toHaveTextContent('4');
    expect(cells[2]).toHaveTextContent('unavailable');
    expect(cells[3]).toHaveTextContent('1');
    expect(within(results).queryByText(/winner/i)).toBeNull();

    fireEvent.click(screen.getByTestId('experiment-open-run-a'));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'run-a', status: 'completed' }));

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select run-a for comparison' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select run-c for comparison' }));
    fireEvent.click(screen.getByTestId('experiment-compare'));
    expect(onCompare).toHaveBeenCalledWith(['run-a', 'run-c']);
  });

  it('reports an ambiguous submission once and never retries the POST automatically', async () => {
    const request = vi.fn(async (path: string) => {
      if (path === '/v1/templates/parcel-delivery/experiments') throw new TypeError('network down');
      throw Error(`unexpected request ${path}`);
    });
    render(<ExperimentPanel api={apiWith(request)} onExperimentId={vi.fn()} onOpen={vi.fn()} onCompare={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('experiment-template')).toHaveValue('parcel-delivery'));
    fireEvent.click(screen.getByTestId('experiment-review'));
    fireEvent.click(screen.getByTestId('experiment-confirm'));
    const unknown = await screen.findByTestId('experiment-ambiguous');
    expect(unknown).toHaveTextContent('submission status unknown; check Records');
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('experiment-confirm')).toBeNull();
  });
});
