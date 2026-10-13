import { Profiler } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordsPanel } from './RecordsPanel';
import type { WorkbenchApi } from './api';
afterEach(() => { cleanup(); vi.useRealTimers(); });
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
