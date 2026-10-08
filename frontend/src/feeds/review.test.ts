import { describe, it, expect, vi, afterEach } from 'vitest';
import { HttpViewerFeed, RunsApi, validateCommit } from './http';
const commit = () => ({ commitIndex: 1, at: { ns: '0', microstep: 0 }, created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
const end = () => new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('event: end\ndata: {"status":"completed","finalCursor":1}\n\n')); controller.close(); } }));
afterEach(() => { vi.unstubAllGlobals(); });
describe('P1 review transport counterexamples', () => {
  it('H7 retries a rejected stream opening from acknowledged cursor', async () => {
    const api = new RunsApi('http://example'); vi.spyOn(api, 'request').mockResolvedValue({ commits: [], next: 1, status: 'running' });
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(end()); vi.stubGlobal('fetch', fetch);
    await expect(new HttpViewerFeed(api, 'run', 'live').subscribe(1, () => {})).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it('H7 retries rejected reader operations', async () => {
    const api = new RunsApi('http://example'); vi.spyOn(api, 'request').mockResolvedValue({ commits: [], next: 1, status: 'running' });
    const broken = new Response(new ReadableStream({ pull(controller) { controller.error(new TypeError('connection lost')); } }));
    const fetch = vi.fn().mockResolvedValueOnce(broken).mockResolvedValueOnce(end()); vi.stubGlobal('fetch', fetch);
    await expect(new HttpViewerFeed(api, 'run', 'live').subscribe(1, () => {})).resolves.toBeUndefined(); expect(fetch).toHaveBeenCalledTimes(2);
  });
  it('H10 rejects an untagged unsafe integer and accepts its exact wire representation', () => {
    const fact = { entity: { id: 'metric', generation: 0 }, fieldId: 'count', producer: 'source', validFrom: { ns: '0', microstep: 0 }, value: 9223372036854776000 };
    expect(() => validateCommit({ ...commit(), facts: [fact] })).toThrow();
    expect(validateCommit({ ...commit(), facts: [{ ...fact, value: { $integer: '9223372036854775815' } }] }).facts[0].value).toEqual({ $integer: '9223372036854775815' });
  });
});

it('preserves authored decimal tokens when posting scenario JSON',async()=>{
  const fetch=vi.fn(async()=>new Response(JSON.stringify({id:'run',scenario:'scalar',status:'created',until_ns:'10'})));
  vi.stubGlobal('fetch',fetch);
  await new RunsApi('http://localhost').startSource('{"id":"scalar","power":-80.0}');
  expect(fetch.mock.calls[0]).toBeDefined();
  expect((fetch.mock.calls as unknown[][])[0][1]).toMatchObject({body:'{"scenario":{"id":"scalar","power":-80.0}}'});
});
