import test from 'node:test';
import assert from 'node:assert/strict';
import { readRuntimeStatusResponse } from './native_parcel_status_read.mjs';
const response = (status, body) => ({ ok: () => status === 200, status: () => status, json: async () => body });
const unavailable = code => response(503, { error: { code, detail: 'actual availability failure' } });
function harness(replies, timeoutMs = 20) {
  let clock = 0, calls = 0; const failures = [], waits = [];
  return { failures, waits, calls: () => calls,
    options: { request: async () => { calls++; return replies[Math.min(calls - 1, replies.length - 1)]; },
      timeoutMs, backoffMs: 5, now: () => clock,
      wait: async ms => { waits.push(ms); clock += ms; }, onFailure: async receipt => failures.push(receipt) } };
}
test('retains a temporary failure and returns only the next actual successful response', async () => {
  const actual = response(200, { snapshot: { phase: 'running', tick: 276 } });
  const h = harness([unavailable('control.unavailable'), actual]);
  assert.equal(await readRuntimeStatusResponse(h.options), actual);
  assert.equal(h.calls(), 2); assert.deepEqual(h.waits, [5]);
  assert.equal(h.failures[0].response.error.code, 'control.unavailable');
  assert.equal(h.failures[0].retrying, true);
});
test('bounds service capacity re-reads and retains the final unavailable receipt', async () => {
  const h = harness([unavailable('service.capacity')], 12);
  await assert.rejects(readRuntimeStatusResponse(h.options), /503 service.capacity/);
  assert.equal(h.calls(), 3); assert.deepEqual(h.waits, [5, 5]);
  assert.equal(h.failures.at(-1).retrying, false);
});
test('does not retry unknown service failures or non-temporary statuses', async () => {
  for (const failed of [unavailable('unknown.failure'), response(500, { error: { code: 'projection.invalid', detail: 'actual invalid projection' } })]) {
    const h = harness([failed]);
    await assert.rejects(readRuntimeStatusResponse(h.options));
    assert.equal(h.calls(), 1); assert.deepEqual(h.waits, []);
    assert.equal(h.failures[0].retrying, false);
  }
});
test('does not hide malformed availability responses', async () => {
  const h = harness([{ ok: () => false, status: () => 503, json: async () => { throw new SyntaxError('actual invalid JSON'); } }]);
  await assert.rejects(readRuntimeStatusResponse(h.options), /actual invalid JSON/);
  assert.equal(h.calls(), 1); assert.deepEqual(h.waits, []);
});
