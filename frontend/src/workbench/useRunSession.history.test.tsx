import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { emptyCommit, fixtureHeader } from '../behaviours/extension-fixture';
import type { FeedCommit } from '../contracts/viewer-feed';
import { RunsApi, type WaitingContext } from '../feeds/http';
import { useRunSession } from './useRunSession';

const feed = vi.hoisted(() => ({
  deliver: undefined as ((commit: FeedCommit) => void) | undefined,
  status: undefined as ((status: string, waiting?: WaitingContext) => void) | undefined,
  subscribe: vi.fn(), sources: vi.fn(),
}));
vi.mock('../feeds/http', async original => ({
  ...await original<object>(),
  HttpViewerFeed: class {
    constructor(_api: unknown, _run: string, _mode: string, status: typeof feed.status) { feed.status = status; }
    async header() { return { ...fixtureHeader, end: { ns: '360000000000', microstep: 0 } }; }
    subscribe(_from: number, deliver: typeof feed.deliver) {
      feed.deliver = deliver; feed.subscribe();
      return new Promise<void>(() => {});
    }
  },
}));
vi.mock('./operator-source-client', async () => {
  const { OperatorSource } = await import('./operator-source');
  return { createOperatorSource: (api: RunsApi, runId: string, options: import('./operator-source').OperatorSourceOptions) => {
    feed.sources(); const source = new OperatorSource(api, runId, options); void source.start(); return source;
  } };
});
beforeEach(() => {
  localStorage.clear(); feed.subscribe.mockClear(); feed.sources.mockClear(); feed.deliver = undefined; feed.status = undefined;
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'] });
});
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });

// Numeric boundaries and horizon come from T4's history-*.json evidence.
// The test models the real API contracts without running a simulation server.
it.each([undefined, 1])('T3-001 / T2-R3-007 / T4-R3-003: history (initial cut %s) keeps producing inputs and returns to the advancing live head', async initialCut => {
  let current = 93_000_000_000, commitIndex = 3;
  const writes: number[] = [];
  const request = vi.spyOn(RunsApi.prototype, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/configuration')) return { service_run_id: 'run', kernel_run_id: fixtureHeader.runId, epoch: fixtureHeader.epoch, scenario: {
      run: { advance_ns: 1_000_000_000, until_ns: 360_000_000_000 },
      ingress_streams: [{ id: 'operator', initial_watermark_ns: 0 }],
    } };
    if (path.endsWith('/inputs')) return { status: 'waiting_for_input', pause_requested: false, watermarks: { operator: current } };
    if (path === '/v1/runs/run') return { status: 'waiting_for_input', waiting: { stream_ids: ['operator'], at_ns: String(current + 1_000_000_000) } };
    if (path.endsWith('/speed')) return { factor: 1 };
    if (path.endsWith('/watermark')) {
      const body = JSON.parse(String(options?.body));
      expect(body.expected_watermark_ns).toBe(current);
      current = body.watermark_ns; writes.push(current);
      feed.deliver!(emptyCommit(commitIndex++, String(current)));
      return { watermark_ns: current };
    }
    throw Error(`Unexpected request ${path}`);
  });
  // Mount outside the asynchronous act so its effects start before we flush
  // the header/configuration reads and use the subscription's delivery callback.
  const view = renderHook(() => useRunSession('http://api', 'run', 'live', initialCut));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(feed.subscribe).toHaveBeenCalledTimes(1);
  expect(view.result.current).toBeDefined();
  act(() => {
    feed.status!('waiting_for_input', { stream_ids: ['operator'], at_ns: '94000000000' });
    feed.deliver!(emptyCommit(1)); feed.deliver!(emptyCommit(2, '93000000000'));
    view.result.current!.seekOffset(0);
  });
  expect(view.result.current!.followingLive).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(writes).toEqual([94_000_000_000]);
  expect(view.result.current!.clock.ns).toBe('0');
  expect(view.result.current!.store.viewCursor?.knownAt).toBe(1);
  act(() => view.result.current!.togglePlayback());
  expect(view.result.current!.clock.playing).toBe(true);
  act(() => view.result.current!.togglePlayback());
  expect(view.result.current!.clock.playing).toBe(false);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(writes).toEqual([94_000_000_000, 95_000_000_000]);
  expect(view.result.current!.clock.ns).toBe('0');
  act(() => view.result.current!.backToLive());
  expect(view.result.current!.clock.ns).toBe('95000000000');
  expect(view.result.current!.store.viewCursor?.knownAt).toBe(4);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(writes).toEqual([94_000_000_000, 95_000_000_000, 96_000_000_000]);
  expect(view.result.current!.clock.ns).toBe('96000000000');
  expect(view.result.current!.followingLive).toBe(true);
  expect(view.result.current!.status).toBe('waiting_for_input');
  expect(view.result.current!.inputSource.error).toBeUndefined();
  expect(feed.sources).toHaveBeenCalledTimes(1); expect(feed.subscribe).toHaveBeenCalledTimes(1);
  expect(request.mock.calls.some(([path]) => /\/(pause|resume)$/.test(path))).toBe(false);
});
