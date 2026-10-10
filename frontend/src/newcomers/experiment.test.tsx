import {beforeAll, beforeEach, afterEach, describe, expect, it, vi} from 'vitest';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import ExperimentPanel from './ExperimentPanel';
import type {KnobValue, ScenarioTemplate, TemplateKnob} from './api';
import type {ExperimentReport} from './experiment-api';

const fetchMock = vi.fn();
const jsonResponse = (status: number, payload: unknown) => ({
  ok: status >= 200 && status < 300, status,
  text: async () => JSON.stringify(payload),
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {writable: true, value: vi.fn(() => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}}))});
});
afterEach(() => { vi.unstubAllGlobals(); });
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });

const knob = (id: string, label: string, extra: Partial<TemplateKnob> = {}): TemplateKnob =>
  ({id, label, type: 'integer', default: 3, min: 1, max: 20, ...extra});

const template: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1',
  id: 'parcel-delivery', title: 'Parcel delivery', summary: 'Deliver parcels across the city.',
  category: 'logistics', difficulty: 'beginner', duration_s: 120, scenario: 'parcel.yaml',
  observe: [], try: [], kpis: ['tasks_completed'],
  knobs: [knob('fleet_size', 'Fleet size'), knob('battery_kwh', 'Battery capacity', {type: 'choice', options: [1, 2, 4], default: 2})],
};

const report: ExperimentReport = {
  id: 'exp-abc123', contract: 'aeroagentsim.experiment/v1',
  template_id: 'parcel-delivery', template_title: 'Parcel delivery', status: 'completed',
  seeds: 2, jobs: 1, horizon_s: null, knobs: {}, vary: {fleet_size: [3, 6]},
  total: 4, completed: 4, failed: 0, error: null,
  runs: [
    {config_id: 'config-0', seed: 0, run: '/runs/x', run_id: 'run-a', status: 'completed', error: null, kpis: {tasks_completed: 5}, kpi_meta: {}},
    {config_id: 'config-0', seed: 1, run: '/runs/y', run_id: 'run-b', status: 'completed', error: null, kpis: {tasks_completed: 7}, kpi_meta: {}},
    {config_id: 'config-1', seed: 0, run: '/runs/z', run_id: 'run-c', status: 'completed', error: null, kpis: {tasks_completed: 11}, kpi_meta: {}},
    {config_id: 'config-1', seed: 1, run: '/runs/w', run_id: 'run-d', status: 'completed', error: null, kpis: {tasks_completed: 13}, kpi_meta: {}},
  ],
  table: [
    {config_id: 'config-0', knobs: {fleet_size: 3}, metrics: {tasks_completed: {mean: 6, std: 1.4142135623730951, n: 2, unit: '', label: 'Tasks completed', best: false}}},
    {config_id: 'config-1', knobs: {fleet_size: 6}, metrics: {tasks_completed: {mean: 12, std: 1.4142135623730951, n: 2, unit: '', label: 'Tasks completed', best: true}}},
  ],
};

describe('ExperimentPanel', () => {
  it('runs the guided workflow: typed varied settings posted, polling of the completed report, best highlighted and open-run link', async () => {
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') return jsonResponse(201, {...report, status: 'running', completed: 0, failed: 0, runs: [], table: report.table.map(row => ({...row, metrics: {}}))});
      return jsonResponse(200, report);
    });
    render(<ExperimentPanel base="http://api.test" templates={[template]} />);

    // Choose the numeric knob to vary and add two typed values.
    fireEvent.click(screen.getByTestId('exp-knob-fleet_size'));
    const adder = screen.getByTestId('exp-add-fleet_size') as HTMLInputElement;
    fireEvent.change(adder, {target: {value: '3'}});
    fireEvent.click(screen.getByTestId('exp-add-fleet_size-button'));
    fireEvent.change(adder, {target: {value: '6'}});
    fireEvent.click(screen.getByTestId('exp-add-fleet_size-button'));
    expect(screen.getByTestId('experiment-preview')).toHaveTextContent(/2 configurations × 3 repeats = 6 runs/);

    fireEvent.click(screen.getByTestId('experiment-start'));
    await waitFor(()=>expect(fetchMock).toHaveBeenCalled());
        // Correct endpoint and typed varied settings, no raw JSON anywhere.
    const [postedUrl, postedInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(postedUrl).toBe('http://api.test/v1/templates/parcel-delivery/experiments');
    const body = JSON.parse(postedInit!.body as string) as {vary: Record<string, KnobValue[]>; seeds: number; jobs: number};
    expect(body).toEqual({vary: {fleet_size: [3, 6]}, seeds: 3, jobs: 1});

    // Polling fetches the report without overlapping.
    // First poll (2 s interval) fetches the completed report and stops polling.
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === 'http://api.test/v1/experiments/exp-abc123')).toBe(true), {timeout: 4000});
    await waitFor(() => expect(screen.getByTestId('experiment-report')).toHaveTextContent(/Completed — 4 of 4 runs succeeded/), {timeout: 4000});
    // Best cell (backend-marked only) is highlighted; the other is not.
    const best = screen.getByTestId('exp-cell-config-1-tasks_completed');
    expect(best).toHaveClass('is-best');
    expect(screen.getByTestId('exp-cell-config-0-tasks_completed')).not.toHaveClass('is-best');
    expect(best).toHaveTextContent(/12 ± 1\.41/);
    // Open the actual child run on the Simple replay route.
    const link = screen.getByTestId('exp-open-run-config-1-seed-1');
    expect(link).toHaveAttribute('href', '/runs/run-d?mode=replay&view=simple&template=parcel-delivery&api=http%3A%2F%2Fapi.test');
    // Terminal status stops further polling (exactly one GET happened after start).
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method !== 'POST')).toHaveLength(1);
  });

  it('surfaces an unavailable experiment service truthfully', async () => {
    fetchMock.mockResolvedValue(jsonResponse(404, 'Not Found'));
    render(<ExperimentPanel base="http://api.test" templates={[template]} />);
    fireEvent.click(screen.getByTestId('exp-knob-fleet_size'));
    fireEvent.change(screen.getByTestId('exp-add-fleet_size'), {target: {value: '3'}});
    fireEvent.click(screen.getByTestId('exp-add-fleet_size-button'));
    fireEvent.change(screen.getByTestId('exp-add-fleet_size'), {target: {value: '6'}});
    fireEvent.click(screen.getByTestId('exp-add-fleet_size-button'));
    fireEvent.click(screen.getByTestId('experiment-start'));
    await waitFor(() => expect(screen.getByTestId('experiment-error')).toHaveTextContent(/could not be started/));
  });

});
