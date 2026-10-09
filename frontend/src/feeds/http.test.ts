import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpViewerFeed, RunsApi, validateCommit, validateHeader } from './http';
import fixture from './contract-fixture.json';
import type { FeedCommit } from '../contracts/viewer-feed';
const commit = (index: number): FeedCommit => ({ commitIndex: index, at: { ns: '9223372036854775815', microstep: index }, created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
const page = (commits: FeedCommit[], next: number, status = 'running') => new Response(JSON.stringify({ commits, next, status, ...(['completed','stopped','faulted','interrupted'].includes(status) ? {finalCursor: next} : {}) }), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => { vi.unstubAllGlobals(); });
describe('HTTP viewer transport', () => {
  it('accepts the authored contract fixture with a nonspatial record', () => {
    const scene={id:'authored-city',city:{kind:'traffic-city',url:'/v1/studio/demo-assets/scene.json'}};
    expect(validateHeader({...fixture.header,scene}).scene).toEqual(scene);
    expect(validateHeader(fixture.header).presentation).toHaveLength(1);
    expect(validateCommit(fixture.commit).at.ns).toBe('9223372036854775815');
  });
  it('pages replay from actual journal indices without losing nanoseconds', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(page([commit(1), commit(2)], 3, 'completed')).mockResolvedValueOnce(page([], 3, 'completed'));
    vi.stubGlobal('fetch', fetcher); const received: FeedCommit[] = [];
    await new HttpViewerFeed(new RunsApi('http://api'), 'recorded').subscribe(0, value => received.push(value));
    expect(received.map(value => value.commitIndex)).toEqual([1, 2]);
    expect(received[0].at.ns).toBe('9223372036854775815');
    expect(fetcher.mock.calls[1][0]).toContain('from=3');
  });
  it('tails fragmented CRLF SSE and ignores a repeated committed prefix', async () => {
    const encoder = new TextEncoder();
    const text = `event: commit\r\ndata: ${JSON.stringify(commit(1))}\r\n\r\nevent: commit\r\ndata: ${JSON.stringify(commit(2))}\r\n\r\nevent: end\r\ndata: {"status":"completed","finalCursor":3}\r\n\r\n`;
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      for (let index = 0; index < text.length; index += 7) controller.enqueue(encoder.encode(text.slice(index, index + 7)));
      controller.close();
    } });
    const fetcher = vi.fn().mockResolvedValueOnce(page([commit(1)], 2)).mockResolvedValueOnce(page([], 2)).mockResolvedValueOnce(new Response(stream));
    vi.stubGlobal('fetch', fetcher); const received: number[] = [];
    await new HttpViewerFeed(new RunsApi('http://api'), 'live', 'live').subscribe(0, value => received.push(value.commitIndex));
    expect(received).toEqual([1, 2]);
    expect(fetcher.mock.calls[2][0]).toContain('from=2');
  });
  it('rejects gaps, malformed pages and HTTP failures instead of supplying data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(page([commit(2)], 3)));
    await expect(new HttpViewerFeed(new RunsApi('http://api'), 'gap').subscribe(0, () => {})).rejects.toThrow('Journal gap');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('offline', { status: 503 })));
    await expect(new RunsApi('http://api').runs()).rejects.toThrow('HTTP 503');
  });
  it('fails malformed header units and number-encoded timestamps', () => {
    const value = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'x', registryDigest: 'digest', types: [], fields: [], presentation: [], start: { ns: 42, microstep: 0 } };
    expect(() => validateHeader(value)).toThrow('string');
  });
  it('carries waiting metadata through commit pages and a status SSE event without commits', async () => {
    const waiting = { stream_ids: ['operator'], at_ns: '2000000000' };
    const seen: Array<[string, unknown]> = [];
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ commits: [commit(1)], next: 2, status: 'waiting_for_input', waiting })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ commits: [], next: 2, status: 'waiting_for_input', waiting })))
      .mockResolvedValueOnce(new Response(`event: status\r\ndata: ${JSON.stringify({ status: 'waiting_for_input', waiting })}\r\n\r\nevent: status\r\ndata: ${JSON.stringify({ status: 'running' })}\r\n\r\n`))
      .mockResolvedValue(new Response('', { status: 503 }));
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    const feed = new HttpViewerFeed(new RunsApi('http://api'), 'live', 'live', (status, context) => seen.push([status, context]));
    await expect(feed.subscribe(0, () => {}, controller.signal)).rejects.toThrow();
    expect(seen.map(([status]) => status)).toEqual(['waiting_for_input', 'waiting_for_input', 'waiting_for_input', 'running']);
    expect(seen[0][1]).toEqual(waiting); expect(seen[1][1]).toEqual(waiting); expect(seen[2][1]).toEqual(waiting); expect(seen[3][1]).toBeUndefined();
    controller.abort();
  });
  it('treats input_timeout as a terminal commit-page status with waiting context', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ commits: [], next: 1, status: 'input_timeout', finalCursor: 1, waiting: { stream_ids: ['operator'], at_ns: '3000000000' } })));
    vi.stubGlobal('fetch', fetcher);
    const seen: Array<[string, unknown]> = [];
    const controller = new AbortController();
    await new HttpViewerFeed(new RunsApi('http://api'), 'recorded', 'live', (status, context) => seen.push([status, context])).subscribe(0, () => {}, controller.signal);
    expect(seen.map(([status]) => status)).toEqual(['input_timeout']);
    expect((seen[0][1] as {stream_ids: string[]}).stream_ids).toEqual(['operator']);
    expect(fetcher).toHaveBeenCalledTimes(1); // Terminal status ends polling; no SSE reconnect loop.
  });
});
