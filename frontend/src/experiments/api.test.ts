import { describe, expect, it, vi } from 'vitest';
import { ExperimentApi, ExperimentDecodeError, decodeExperiment, type ExperimentTransport } from './api';

function payload(): Record<string, unknown> {
  return {
    id: 'exp-1',
    contract: 'aeroagentsim.experiment/v1',
    template_id: 'parcel-delivery',
    template_title: 'Urban parcel delivery',
    template_title_zh: '城市包裹配送',
    status: 'completed',
    seeds: 2,
    jobs: 1,
    horizon_s: 300,
    knobs: {},
    vary: { allocator: ['nearest', 'auction'] },
    total: 4,
    completed: 3,
    failed: 1,
    error: null,
    runs: [
      {
        config_id: 'config-0', seed: 0, run: '/runs/a', run_id: 'run-a', status: 'completed', error: null,
        kpis: { tasks_completed: 4, energy_used_kj: null },
        kpi_meta: { tasks_completed: { unit: 'count', label: 'Tasks completed' } },
      },
      {
        config_id: 'config-0', seed: 1, run: null, run_id: null, status: 'faulted', error: 'worker failed',
        kpis: {}, kpi_meta: {},
      },
    ],
    table: [
      {
        config_id: 'config-0',
        knobs: { allocator: 'nearest' },
        metrics: {
          tasks_completed: { mean: 4, std: null, n: 1, unit: 'count', label: 'Tasks completed', best: true },
          energy_used_kj: { mean: null, std: null, n: 0, unit: 'kJ', label: 'Energy used', best: false },
        },
      },
    ],
  };
}

describe('decodeExperiment', () => {
  it('keeps null means, null sample deviations and null run ids without filling them in', () => {
    const report = decodeExperiment(payload());
    expect(report.template_title_zh).toBe('城市包裹配送');
    expect(report.table[0].metrics.tasks_completed.std).toBeNull();
    expect(report.table[0].metrics.energy_used_kj.mean).toBeNull();
    expect(report.table[0].metrics.energy_used_kj.n).toBe(0);
    expect(report.runs[1].run_id).toBeNull();
    expect(report.runs[1].run).toBeNull();
    expect(report.runs[1].status).toBe('faulted');
  });

  it('rejects a different contract or a malformed aggregate instead of guessing', () => {
    expect(() => decodeExperiment({ ...payload(), contract: 'something/else' })).toThrow(ExperimentDecodeError);
    const malformed = payload();
    malformed.table = [{ config_id: 'config-0', knobs: {}, metrics: { tasks_completed: { mean: '4', std: null, n: 1, unit: 'count', label: 'Tasks completed', best: false } } }];
    expect(() => decodeExperiment(malformed)).toThrow(ExperimentDecodeError);
  });
});

describe('ExperimentApi', () => {
  it('posts one plan and decodes the returned report', async () => {
    const request = vi.fn().mockResolvedValue(payload());
    const api = new ExperimentApi({ request: request as unknown as ExperimentTransport['request'] });
    const plan = { vary: { allocator: ['nearest', 'auction'] }, seeds: 2, jobs: 1, horizon_s: 300 };
    const report = await api.create('parcel-delivery', plan);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith('/v1/templates/parcel-delivery/experiments', expect.objectContaining({ method: 'POST', body: plan }));
    expect(report.id).toBe('exp-1');
    expect(report.completed).toBe(3);
  });

  it('reads one experiment by id', async () => {
    const request = vi.fn().mockResolvedValue(payload());
    const api = new ExperimentApi({ request: request as unknown as ExperimentTransport['request'] });
    const report = await api.get('exp-1');
    expect(request).toHaveBeenCalledWith('/v1/experiments/exp-1', expect.any(Object));
    expect(report.id).toBe('exp-1');
  });
});
