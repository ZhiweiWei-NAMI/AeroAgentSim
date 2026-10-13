import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { OperatorSource } from './operator-source';

const active: OperatorSource[] = [];
beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] }); });
afterEach(() => { active.splice(0).forEach(source => source.dispose()); vi.useRealTimers(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function producer() {
  const path = '/v1/runs/run';
  let current = 93_000_000_000, boundary = 94_000_000_000;
  let progressStatus = 'waiting_for_input', stateStatus = 'waiting_for_input';
  const onReceipt = vi.fn(), onError = vi.fn();
  const request = vi.fn(async (url: string, options?: RequestInit): Promise<unknown> => {
    if (url.endsWith('/configuration')) return { scenario: {
      run: { advance_ns: 1_000_000_000, until_ns: 360_000_000_000 },
      ingress_streams: [{ id: 'operator', initial_watermark_ns: 0 }],
    } };
    if (url === `${path}/inputs`) return { status: progressStatus, pause_requested: false, watermarks: { operator: current } };
    if (url === path) return { status: stateStatus, waiting: { stream_ids: ['operator'], at_ns: String(boundary) } };
    if (url === `${path}/speed`) return { factor: null };
    if (url === `${path}/watermark`) {
      const body = JSON.parse(String(options?.body));
      expect(body.expected_watermark_ns).toBe(current);
      current = body.watermark_ns;
      return { watermark_ns: current };
    }
    throw Error(`Unexpected source request ${url}`);
  });
  const source = new OperatorSource({ request }, 'run', { onReceipt, onError });
  active.push(source);
  return { path, source, request, onReceipt, onError,
    wait: (ns: number) => { boundary = ns; },
    status: (progress: string, state = progress) => { progressStatus = progress; stateStatus = state; },
  };
}

it('T4-R3-003: reads the live 94 s boundary directly and advances from the observed 93 s frontier', async () => {
  const s = producer();
  await s.source.start(); await vi.advanceTimersByTimeAsync(100);
  expect(s.onReceipt.mock.calls).toEqual([['94000000000']]);
  expect(s.request.mock.calls.some(([path]) => path.includes('/commits'))).toBe(false);
  expect(s.request).toHaveBeenCalledWith(s.path, expect.objectContaining({ cache: 'no-store' }));
  s.wait(95_000_000_000); await vi.advanceTimersByTimeAsync(40);
  expect(s.onReceipt.mock.calls).toEqual([['94000000000'], ['95000000000']]);
});

it('T4-R3-003: re-reads a boundary already closed rather than manufacturing the next one', async () => {
  const s = producer(); s.wait(93_000_000_000);
  await s.source.start(); await vi.advanceTimersByTimeAsync(100);
  expect(s.onReceipt).not.toHaveBeenCalled();
  s.wait(94_000_000_000); await vi.advanceTimersByTimeAsync(40);
  expect(s.onReceipt).toHaveBeenCalledWith('94000000000');
});

it.each(['inputs', 'boundary', 'speed'])('T4-R3-003: resume starts a fresh poll while a cancelled %s read remains unsettled', async kind => {
  const s = producer(), stale = deferred<unknown>();
  const readPath = kind === 'inputs' ? `${s.path}/inputs` : kind === 'boundary' ? s.path : `${s.path}/speed`;
  const original = s.request.getMockImplementation()!;
  let first = true, signal: AbortSignal | undefined;
  s.request.mockImplementation((path, options) => {
    if (path === readPath && first) { first = false; signal = options?.signal as AbortSignal; return stale.promise; }
    return original(path, options);
  });
  await s.source.start(); await vi.advanceTimersByTimeAsync(0);
  expect(first).toBe(false);
  await s.source.hold(); expect(signal?.aborted).toBe(true);
  s.source.resume(); await vi.advanceTimersByTimeAsync(1);
  expect(s.onReceipt.mock.calls).toEqual([['94000000000']]);
  // Even a late terminal status belongs to the cancelled generation.
  stale.resolve(kind === 'speed' ? { factor: 0 } : { status: 'completed', pause_requested: false, watermarks: { operator: 360_000_000_000 } });
  await vi.advanceTimersByTimeAsync(1);
  s.wait(95_000_000_000); await vi.advanceTimersByTimeAsync(40);
  expect(s.onReceipt.mock.calls).toEqual([['94000000000'], ['95000000000']]);
  expect(s.onError.mock.calls.every(([error]) => error === undefined)).toBe(true);
});

it('T4-R3-003: a cancelled poll cannot clear the fresh poll or create overlapping writes', async () => {
  const s = producer(), stale = deferred<unknown>(), fresh = deferred<unknown>();
  const original = s.request.getMockImplementation()!;
  let reads = 0;
  s.request.mockImplementation((path, options) => {
    if (path === `${s.path}/inputs`) {
      reads++;
      if (reads === 1) return stale.promise;
      if (reads === 2) return fresh.promise;
    }
    return original(path, options);
  });
  await s.source.start(); await vi.advanceTimersByTimeAsync(0);
  await s.source.hold(); s.source.resume(); await vi.advanceTimersByTimeAsync(0);
  expect(reads).toBe(2);
  stale.resolve({ status: 'completed' }); await vi.advanceTimersByTimeAsync(100);
  expect(reads).toBe(2); expect(s.onReceipt).not.toHaveBeenCalled();
  fresh.resolve({ status: 'waiting_for_input', pause_requested: false, watermarks: { operator: 93_000_000_000 } });
  await vi.advanceTimersByTimeAsync(1);
  expect(s.onReceipt.mock.calls).toEqual([['94000000000']]);
});

it('does not advance across a real pause even when the independent progress read still says running', async () => {
  const s = producer(); s.status('waiting_for_input', 'paused');
  await s.source.start(); await vi.advanceTimersByTimeAsync(2000);
  expect(s.onReceipt).not.toHaveBeenCalled();
  s.status('waiting_for_input'); await vi.advanceTimersByTimeAsync(1000);
  expect(s.onReceipt).toHaveBeenCalledWith('94000000000');
});

it('surfaces an in-flight write failure during a hold without recording an acknowledgement', async () => {
  const s = producer(), original = s.request.getMockImplementation()!;
  let reject!: (error: Error) => void;
  const write = new Promise<unknown>((_resolve, fail) => { reject = fail; });
  s.request.mockImplementation((path, options) => path.endsWith('/watermark') ? write : original(path, options));
  await s.source.start(); await vi.advanceTimersByTimeAsync(0);
  const held = s.source.hold(); reject(Error('HTTP 422: source advance rejected'));
  await held;
  expect(s.onReceipt).not.toHaveBeenCalled();
  expect(s.onError).toHaveBeenLastCalledWith('Error: HTTP 422: source advance rejected');
});

it.each([undefined, null])('rejects missing or null waiting metadata (%s) visibly and recovers from the real boundary', async waiting => {
  const s = producer(), original = s.request.getMockImplementation()!;
  s.request.mockImplementation((path, options) => path === s.path ? Promise.resolve({ status: 'waiting_for_input', waiting }) : original(path, options));
  await s.source.start(); await vi.advanceTimersByTimeAsync(1);
  expect(s.onReceipt).not.toHaveBeenCalled();
  expect(s.onError.mock.calls.some(([error]) => typeof error === 'string')).toBe(true);
  s.request.mockImplementation(original); await vi.advanceTimersByTimeAsync(1000);
  expect(s.onReceipt).toHaveBeenCalledWith('94000000000');
  expect(s.onError).toHaveBeenLastCalledWith(undefined);
});
