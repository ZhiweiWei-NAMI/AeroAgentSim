import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RunsApi } from '../feeds/http';
import { useOperatorInput } from './useOperatorInput';

// Run the same producer algorithm without a browser Worker transport. The
// actual transport and rendering independence are covered by Playwright.
vi.mock('./operator-source-client', async () => {
  const { OperatorSource } = await import('./operator-source');
  return { createOperatorSource: (api: RunsApi, runId: string, options: import('./operator-source').OperatorSourceOptions) => {
    const source = new OperatorSource(api, runId, options); void source.start(); return source;
  } };
});

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });
function source(initial = 0, authoredInitial = 0, end = 2_500_000_000) {
  vi.useFakeTimers({toFake:['setTimeout','clearTimeout','setInterval','clearInterval','Date','performance']});
  const api = new RunsApi('http://api');
  let current = initial, status = 'running', pause = false, offline = false, lag = false, factor: number | null = 1, boundary: number | undefined, streams = ['operator'];
  const writes: number[] = [];
  const request = vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
    if (path.endsWith('/configuration')) return { scenario: { run: { advance_ns: 1_000_000_000, until_ns: end }, ingress_streams: [{ id: 'operator', initial_watermark_ns: authoredInitial }] } };
    if (path.endsWith('/inputs')) { if (offline) throw Error('source offline'); return { status, pause_requested: pause, watermarks: { operator: lag ? initial : current } }; }
    if (path.endsWith('/speed')) return { factor };
    if (/^\/v1\/runs\/[^/]+$/.test(path)) return { status, waiting: { stream_ids: streams, at_ns: String(boundary ?? current + 1_000_000_000) } };
    if (path.endsWith('/watermark')) { const value = JSON.parse(String(options?.body)); writes.push(value.watermark_ns); current = value.watermark_ns; return { watermark_ns: current }; }
    throw Error(`Unexpected request ${path}`);
  });
  return { api, writes, request, status: (value: string) => { status = value; }, pause: (value: boolean) => { pause = value; }, offline: (value: boolean) => { offline = value; }, speed: (value: number | null) => { factor = value; }, wait: (value: number, ids = ['operator']) => { boundary = value; streams = ids; }, lag: () => { lag = true; } };
}
it('does not let a held informational read delay pause or publish its stale running state afterward', async () => {
  const s = source(); s.status('waiting_for_input'); s.speed(null);
  const request = s.request.getMockImplementation()!;
  let release!: (value: unknown) => void, first = true;
  const pending = new Promise(resolve => { release = resolve; });
  s.request.mockImplementation(async (path, options) => {
    if (path.endsWith('/inputs') && first) { first = false; return pending; }
    return request(path, options);
  });
  const view = renderHook(() => useOperatorInput(s.api, 'run', 'live'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  let paused = false;
  await act(async () => { await view.result.current.serialize(async () => { paused = true; s.status('paused'); s.pause(true); }); });
  expect(paused).toBe(true); expect(s.writes).toEqual([]);
  await act(async () => {
    release({ status:'waiting_for_input', pause_requested:false, watermarks:{ operator:0 } });
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(s.writes).toEqual([]); expect(view.result.current.error).toBeUndefined();
});
it('settles an in-flight input before an explicit pause and waits for the real resume',async()=>{
  const s=source(0,0,100_000_000_000);s.status('waiting_for_input');s.speed(null);
  const request=s.request.getMockImplementation()!;
  let acknowledge!:()=>void,first=true;
  s.request.mockImplementation(async(path,options)=>{
    if(path.endsWith('/watermark')&&first){first=false;await new Promise<void>(resolve=>{acknowledge=resolve;});}
    return request(path,options);
  });
  const view=renderHook(()=>useOperatorInput(s.api,'run','live'));
  await act(async()=>{await vi.advanceTimersByTimeAsync(10);});
  expect(acknowledge).toBeTypeOf('function');
  let paused=false,pause:Promise<unknown>|undefined;
  await act(async()=>{
    pause=view.result.current.serialize(async()=>{paused=true;s.status('paused');s.pause(true);});
    await Promise.resolve();
  });
  expect(paused).toBe(false);
  await act(async()=>{acknowledge();await pause!;await vi.advanceTimersByTimeAsync(2000);});
  expect(paused).toBe(true);expect(s.writes).toEqual([1_000_000_000]);
  await act(async()=>{await vi.advanceTimersByTimeAsync(2000);});
  expect(s.writes).toEqual([1_000_000_000]);
  await act(async()=>{await view.result.current.serialize(async()=>{s.status('waiting_for_input');s.pause(false);});await vi.advanceTimersByTimeAsync(40);});
  expect(s.writes.length).toBeGreaterThan(1);
});
it('advances only a waiting live source using its acknowledged frontier, capped at the authored end', async () => {
  const s = source();
  await act(async () => { renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); expect(s.writes).toEqual([]);
  s.status('waiting_for_input');
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(s.writes).toEqual([1_000_000_000, 2_000_000_000, 2_500_000_000]);
});

it.each([0.5, 5, 50, null])('follows the live simulation speed %s with no wall-second gate', async factor => {
  const s = source(0, 0, 100_000_000_000); s.status('waiting_for_input'); s.speed(factor);
  await act(async () => { renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(s.writes.length).toBe(factor === null ? 100 : 2 * factor!);
});
it('closes only the operator boundary requested by the kernel, including partial configured steps', async () => {
  const s = source(0, 0, 100_000_000_000); s.status('waiting_for_input'); s.speed(null); s.wait(125_000_000);
  await act(async () => { renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(s.writes).toEqual([125_000_000]);
  s.wait(2_000_000_000, ['another-source']);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([125_000_000]);
  s.wait(2_000_000_000);
  await act(async () => { await vi.advanceTimersByTimeAsync(40); });
  expect(s.writes).toEqual([125_000_000, 1_125_000_000, 2_000_000_000]);
});
it('T3-001: continues at the real simulation speed across viewing rerenders', async () => {
  const s = source(0, 0, 100_000_000_000); s.status('waiting_for_input');
  const view = renderHook(() => useOperatorInput(s.api, 'run', 'live'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([1_000_000_000]);
  view.rerender(); await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(s.writes.at(-1)).toBe(11_000_000_000);
  view.rerender(); s.speed(50);
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
  expect(s.writes.at(-1)).toBeGreaterThan(11_000_000_000);
  expect(s.writes.at(-1)).toBeLessThanOrEqual(71_000_000_000);
});
it('restores the real source progress after refresh, without needing any inspector component', async () => {
  const s = source(1_000_000_000); s.status('waiting_for_input');
  let view!: ReturnType<typeof renderHook>;
  await act(async () => { view = renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([2_000_000_000]);
  view.unmount();
  await act(async () => { renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([2_000_000_000, 2_500_000_000]);
});
it('retains real acknowledgements across refresh when the published input prefix lags, without duplicate closures',async()=>{
  const s=source(0,0,10_000_000_000);s.status('waiting_for_input');s.lag();
  const view=renderHook(()=>useOperatorInput(s.api,'run','live'));
  await act(async()=>{await vi.advanceTimersByTimeAsync(2200);});expect(s.writes).toEqual([1_000_000_000,2_000_000_000]);view.unmount();
  renderHook(()=>useOperatorInput(s.api,'run','live'));await act(async()=>{await vi.advanceTimersByTimeAsync(2200);});
  expect(s.writes).toEqual([1_000_000_000,2_000_000_000,3_000_000_000,4_000_000_000]);
});
it.each(['replay', 'initialized', 'paused', 'pause-requested', 'completed'] as const)('never closes input implicitly in %s', async condition => {
  const s = source(); s.status(condition === 'paused' ? 'paused' : condition === 'completed' ? 'completed' : condition === 'initialized' ? 'initialized' : 'waiting_for_input'); s.pause(condition === 'pause-requested');
  await act(async () => { renderHook(() => useOperatorInput(s.api, 'run', condition === 'replay' ? 'replay' : 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); }); expect(s.writes).toEqual([]);
  if (condition === 'replay') expect(s.request).not.toHaveBeenCalled();
});
it('T4-R3-003: closes the observed 94 s boundary from the real 93 s frontier without refresh', async () => {
  const s = source(93_000_000_000, 0, 360_000_000_000); s.status('waiting_for_input'); s.speed(null); s.wait(94_000_000_000);
  const view = renderHook(() => useOperatorInput(s.api, 'run', 'live'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(s.writes).toEqual([94_000_000_000]);
  view.rerender(); s.wait(95_000_000_000);
  await act(async () => { await vi.advanceTimersByTimeAsync(40); });
  expect(s.writes).toEqual([94_000_000_000, 95_000_000_000]);
  expect(view.result.current.error).toBeUndefined();
});
it('exposes read failures and recovers through the actual source', async () => {
  const s = source(); let view!: ReturnType<typeof renderHook<ReturnType<typeof useOperatorInput>, void>>;
  await act(async () => { view = renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  s.offline(true); await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(view.result.current.error).toContain('source offline');
  s.offline(false); s.status('waiting_for_input'); await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(view.result.current.error).toBeUndefined(); expect(s.writes).toEqual([1_000_000_000]);
});
it('uses the actual newer frontier when another producer closes the source during a browser advance', async () => {
  const s = source(); s.status('waiting_for_input');
  const request = s.request.getMockImplementation()!;
  s.request.mockImplementation(async (path, options) => {
    if (path.endsWith('/watermark')) {
      expect(JSON.parse(String(options?.body)).expected_watermark_ns).toBe(0);
      return { watermark_ns: 2_500_000_000, advanced: false };
    }
    return request(path, options);
  });
  const view = renderHook(() => useOperatorInput(s.api, 'run', 'live'));
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(s.request.mock.calls.filter(([path]) => path.endsWith('/watermark'))).toHaveLength(1);
  expect(view.result.current.error).toBeUndefined();
  expect(localStorage.getItem('aeroagentsim.operator-watermark/http%3A%2F%2Fapi/run')).toBe('2500000000');
});
it('reports a conditional receipt that does not advance the observed frontier', async () => {
  const s = source(); s.status('waiting_for_input');
  const request = s.request.getMockImplementation()!;
  s.request.mockImplementation(async (path, options) => path.endsWith('/watermark') ? { watermark_ns: 0, advanced: false } : request(path, options));
  const view = renderHook(() => useOperatorInput(s.api, 'run', 'live'));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(view.result.current.error).toContain('receipt mismatch');
  expect(localStorage.length).toBe(0);
});
it('serializes admitted events before watermark closure and stops on unmount', async () => {
  const s = source(); s.status('waiting_for_input'); let view!: ReturnType<typeof renderHook<ReturnType<typeof useOperatorInput>, void>>;
  await act(async () => { view = renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  let release!: () => void;
  void view.result.current.serialize(() => new Promise<void>(resolve => { release = resolve; }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([]);
  await act(async () => { release(); }); expect(s.writes).toEqual([1_000_000_000]);
  view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(3000); }); expect(s.writes).toEqual([1_000_000_000]);
});

it('does not poll a source authored closed through the run horizon', async () => {
  const s = source(2_500_000_000, 2_500_000_000);
  await act(async () => { renderHook(() => useOperatorInput(s.api, 'run', 'live')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(s.request.mock.calls.map(([path]) => path).filter(path => path.endsWith('/inputs'))).toEqual([]);
  expect(s.writes).toEqual([]);
});
it('queues a pause after an in-flight watermark and produces no further input once paused',async()=>{
 const s=source();s.status('waiting_for_input');s.speed(null);const original=s.request.getMockImplementation()!;
 let release!:()=>void,started=false,paused=false;const pending=new Promise<void>(resolve=>{release=resolve;});
 s.request.mockImplementation(async(path,options)=>{if(path.endsWith('/watermark')&&!started){started=true;await pending;}return original(path,options);});
 const view=renderHook(()=>useOperatorInput(s.api,'run','live'));await act(async()=>{await vi.advanceTimersByTimeAsync(1);});expect(started).toBe(true);
 const pause=view.result.current.serialize(async()=>{paused=true;s.status('paused');s.pause(true);});expect(paused).toBe(false);
 await act(async()=>{release();await pause;await vi.advanceTimersByTimeAsync(2000);});expect(paused).toBe(true);expect(s.writes).toEqual([1_000_000_000]);expect(view.result.current.error).toBeUndefined();
});
it('gives a newly attached run its own queue when a previous admission is still pending', async () => {
  const s = source(); s.status('waiting_for_input');
  let view!: ReturnType<typeof renderHook<ReturnType<typeof useOperatorInput>, { run: string }>>;
  await act(async () => { view = renderHook(({ run }) => useOperatorInput(s.api, run, 'live'), { initialProps: { run: 'first' } }); });
  let release!: () => void;
  void view.result.current.serialize(() => new Promise<void>(resolve => { release = resolve; }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([]);
  await act(async () => { view.rerender({ run: 'second' }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(s.writes).toEqual([1_000_000_000]);
  await act(async () => { release(); }); expect(s.writes).toEqual([1_000_000_000]);
});
