import { expect, it } from 'vitest';
import type { ComparisonRun, KpiValues } from './api';
import {
  comparisonConfiguration, comparisonRefreshPolicy, deriveResultState, finalCursors,
  readComparison, resolveMetricDirection, COMPARISON_REFRESH_MS,
} from './comparison-read';

const ends = {
  a: { at_ns: '10000000000', commit_index: 2, status: 'completed' },
  b: { at_ns: '20000000000', commit_index: 3, status: 'completed' },
};
const configuration = { alignment: 'recorded_run_end', recorded_ends: ends, config_diff: [] };
const run = (id: string, kpis: KpiValues = {}): ComparisonRun => ({ id, title: id, kpis, knobs: {}, algorithms: {} });

it('keeps finality/cursor fields from one response and reports an aligned final cut', () => {
  const value = {
    runs: [
      { ...run('a'), status: 'completed', results_final: true, results_cursor: 3, final_commit_index: 2 },
      { ...run('b'), status: 'completed', results_final: true, results_cursor: 4, final_commit_index: 3 },
    ],
    ...configuration,
  };
  const read = readComparison(value, ['a', 'b']);
  expect(read.runs.map(entry => entry.state)).toEqual(['final', 'final']);
  expect(read.runs[0]).toMatchObject({ status: 'completed', resultsFinal: true, resultsCursor: 3, finalCommitIndex: 2, cursor: 2 });
  expect(read.configuration?.config_diff).toEqual([]);
  expect(read.configurationError).toBeUndefined();
  expect(finalCursors(read)).toEqual([2, 3]);
  expect(comparisonRefreshPolicy(read)).toEqual({ active: false, reason: 'final' });
});

it('treats older servers without finality fields as provisional and keeps refreshing', () => {
  const read = readComparison({ runs: [run('a'), run('b')], ...configuration }, ['a', 'b']);
  expect(read.runs.map(entry => entry.state)).toEqual(['provisional', 'provisional']);
  expect(read.runs[0].resultsFinal).toBeUndefined();
  expect(comparisonRefreshPolicy(read)).toEqual({ active: true, intervalMs: COMPARISON_REFRESH_MS, reason: 'provisional' });
});

it('accepts the backend comparison envelope and additive fields without losing evidence', () => {
  const value = {
    contract: 'aeroagentsim.run-comparison/v1',
    runs: [
      { ...run('a'), title_zh: '运行甲', status: 'completed', results_final: true, results_cursor: 3, final_commit_index: 2, extra_summary: { future: true } },
      { ...run('b'), status: 'completed', results_final: true, results_cursor: 4, final_commit_index: 3 },
    ],
    kpis: [], differences: { knobs: [], algorithms: [] },
    ...configuration,
    recorded_ends: { ...ends, a: { ...ends.a, extra_end: true } },
    config_diff: [{ path: '/knobs/mode', values: { a: null }, present: { a: true, b: false }, extra_diff: true }],
    extra_response: true,
  };
  const read = readComparison(value, ['a', 'b']);
  expect(read.runs.map(entry => entry.state)).toEqual(['final', 'final']);
  expect(read.runs[0].run.title_zh).toBe('运行甲');
  expect(read.configurationError).toBeUndefined();
  expect(read.configuration?.config_diff[0]).toMatchObject({ values: { a: null }, present: { a: true, b: false } });
});

it('accepts summaries without KPIs from include_kpis=false without inventing measurements', () => {
  const read = readComparison({
    runs: ['a', 'b'].map(id => ({ id, title: id, knobs: {}, algorithms: {}, status: 'completed' })),
    ...configuration,
  }, ['a', 'b']);
  expect(read.runs.map(entry => entry.run.kpis)).toEqual([{}, {}]);
  expect(read.runs.map(entry => entry.state)).toEqual(['provisional', 'provisional']);
  expect(read.configurationError).toBeUndefined();
});

it.each([
  {},
  { results_cursor: 3 },
  { final_commit_index: 2 },
  { results_cursor: 3, final_commit_index: 2 },
  { results_final: true },
  { results_final: true, results_cursor: 3 },
  { results_final: true, final_commit_index: 2 },
])('keeps incomplete finality evidence provisional: %j', evidence => {
  const read = readComparison({ runs: [{ ...run('a'), status: 'completed', ...evidence }], ...configuration }, ['a']);
  expect(read.runs[0].state).toBe('provisional');
  expect(comparisonRefreshPolicy(read).active).toBe(true);
});

it('still rejects malformed supplied measurements and settings', () => {
  expect(() => readComparison({ runs: [{ ...run('a'), kpis: null }] })).toThrow();
  expect(() => readComparison({ runs: [{ ...run('a'), knobs: { fleet: null } }] })).toThrow();
});

it('keeps an advancing or aggregating run provisional', () => {
  expect(deriveResultState({ status: 'running', results_final: false, results_cursor: 3, final_commit_index: null })).toBe('provisional');
  expect(deriveResultState({ status: 'completed', results_final: false, results_cursor: 3, final_commit_index: 2, recorded_end: ends.a })).toBe('provisional');
});

it('never calls a mismatched final cursor/end final', () => {
  const value = {
    runs: [{ ...run('a'), status: 'completed', results_final: true, results_cursor: 9, final_commit_index: 2 },
      { ...run('b'), status: 'completed', results_final: true, results_cursor: 4, final_commit_index: 3 }],
    ...configuration,
  };
  const read = readComparison(value, ['a', 'b']);
  expect(read.runs[0].state).toBe('unknown');
  expect(comparisonRefreshPolicy(read)).toEqual({ active: false, reason: 'unknown' });
  expect(finalCursors(read)).toEqual([3]);
});

it('treats contradictory or malformed evidence as unknown, not final', () => {
  const value = {
    runs: [{ ...run('a'), status: 'completed', results_final: 'yes', results_cursor: 3, final_commit_index: 2 },
      { ...run('b'), status: 'completed', results_final: true, results_cursor: '4', final_commit_index: 3 }],
    ...configuration,
  };
  expect(readComparison(value, ['a', 'b']).runs.map(entry => entry.state)).toEqual(['unknown', 'unknown']);
  expect(deriveResultState({ status: 'completed', results_final: true, results_cursor: 3, final_commit_index: 2 })).toBe('unknown');
});

it('does not discard malformed status or accept a nonterminal recorded end as final', () => {
  const evidence = { results_final: true, results_cursor: 3, final_commit_index: 2 };
  const read = readComparison({ runs: [{ ...run('a'), status: 42, ...evidence }], ...configuration }, ['a']);
  expect(read.runs[0].state).toBe('unknown');
  expect(deriveResultState({ ...evidence, recorded_end: { ...ends.a, status: 'running' } })).toBe('unknown');
});

it('labels only the processed KPI cut, not an aggregating run’s terminal target', () => {
  const read = readComparison({
    runs: [{ ...run('a'), status: 'completed', results_final: false, results_cursor: 2, final_commit_index: 2 }, run('b')],
    ...configuration,
  }, ['a', 'b']);
  expect(read.runs[0]).toMatchObject({ state: 'provisional', cursor: 1, finalCommitIndex: 2 });
  expect(read.runs[1].cursor).toBeUndefined();
});

it('keeps the runs when only the configuration half is unavailable', () => {
  const read = readComparison({ runs: [run('a'), run('b')] }, ['a', 'b']);
  expect(read.runs).toHaveLength(2);
  expect(read.configuration).toBeUndefined();
  expect(read.configurationError).toMatch(/malformed/);
  expect(() => comparisonConfiguration({ config_diff: [] })).toThrow('malformed');
});

it('preserves a measured zero separately from a missing value', () => {
  const kpis: Record<'a' | 'b', KpiValues> = {
    a: { tasks_completed: { value: 0, unit: 'count', label: 'Tasks completed', direction: 'max' } },
    b: { tasks_completed: { value: null, unit: 'count', label: 'Tasks completed', direction: 'max' } },
  };
  const read = readComparison({ runs: [run('a', kpis.a), run('b', kpis.b)], ...configuration }, ['a', 'b']);
  expect(read.runs[0].run.kpis.tasks_completed.value).toBe(0);
  expect(read.runs[1].run.kpis.tasks_completed.value).toBeNull();
});

it('only allows best-value emphasis for one agreed direction', () => {
  const lower = run('a', { energy_used_kj: { value: 900, unit: 'kJ', label: 'Energy', direction: 'min' } });
  const higher = run('b', { energy_used_kj: { value: 700, unit: 'kJ', label: 'Energy', direction: 'min' } });
  expect(resolveMetricDirection([lower, higher], 'energy_used_kj')).toEqual({ direction: 'min', defined: true, consistent: true });
  expect(resolveMetricDirection([
    lower,
    run('b', { energy_used_kj: { value: 700, unit: 'kJ', label: 'Energy', direction: 'max' } }),
  ], 'energy_used_kj')).toEqual({ direction: 'neutral', defined: false, consistent: false });
  expect(resolveMetricDirection([run('a'), run('b')], 'tasks_completed', { tasks_completed: 'max' }))
    .toEqual({ direction: 'max', defined: true, consistent: true });
  expect(resolveMetricDirection([run('a'), run('b')], 'custom_metric'))
    .toEqual({ direction: 'neutral', defined: false, consistent: true });
});

it.each([
  ['higher', 'max'], ['lower', 'min'],
] as const)('normalizes descriptor direction %s without requiring a known metric ID', (direction, expected) => {
  const row = {value: 3, unit: 'm', label: 'External metric', direction};
  expect(resolveMetricDirection([run('a', {external: row}), run('b', {external: {...row, value: 0}})], 'external'))
    .toEqual({direction: expected, defined: true, consistent: true});
});

it.each([null, undefined])('never replaces an unknown descriptor direction (%s) with a builtin ID guess', direction => {
  const row = {value: 3, unit: 'kJ', label: 'Energy', label_zh: '能量', direction};
  expect(resolveMetricDirection([run('a', {energy_used_kj: row}), run('b', {energy_used_kj: row})], 'energy_used_kj', {energy_used_kj: 'min'}))
    .toEqual({direction: 'neutral', defined: false, consistent: true});
});

it('treats neutral, missing and conflicting directions as disagreement, not an implicit vote', () => {
  const row = {value: 3, unit: 'kJ', label: 'Energy', direction: 'lower' as const};
  for (const other of [{...row, direction: null}, {...row, direction: undefined}, {...row, direction: 'higher' as const}]) {
    expect(resolveMetricDirection([run('a', {energy_used_kj: row}), run('b', {energy_used_kj: other})], 'energy_used_kj'))
      .toEqual({direction: 'neutral', defined: false, consistent: false});
  }
  expect(resolveMetricDirection([run('a', {energy_used_kj: row}), run('b', {energy_used_kj: {...row, direction: 'min'}})], 'energy_used_kj'))
    .toEqual({direction: 'min', defined: true, consistent: true});
});
