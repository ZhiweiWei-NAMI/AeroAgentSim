import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpViewerFeed, RunsApi, validateCommit, validateHeader } from './http';
import fixture from './contract-fixture.json';
import type { FeedCommit } from '../contracts/viewer-feed';
const commit = (index: number): FeedCommit => ({ commitIndex: index, at: { ns: '9223372036854775815', microstep: index }, created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
const page = (commits: FeedCommit[], next: number, status = 'running') => new Response(JSON.stringify({ commits, next, status, ...(['completed','stopped','faulted','interrupted'].includes(status) ? {finalCursor: next} : {}) }), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => { vi.unstubAllGlobals(); });
describe('HTTP viewer transport', () => {
  it('accepts the GLM-authored pinned contract fixture with a nonspatial record', () => {
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
});
