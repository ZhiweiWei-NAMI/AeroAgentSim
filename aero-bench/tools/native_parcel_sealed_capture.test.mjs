import test from 'node:test';
import assert from 'node:assert/strict';
import { readonlyReplayCredentials, assertVerifiedParcelHistory, SealedCaptureRequestAudit } from './native_parcel_sealed_capture.mjs';
const runId = 'a'.repeat(64);
const access = () => ({ schema_version: 'aero-bench.replay-access-response/v1', read_only: true,
  credentials: { schema_version: 'aero-bench.run-access-credentials/v1', run_id: runId,
    operator_token: 'b'.repeat(64), csrf_token: 'c'.repeat(64) } });
const trace = () => ({ run_id: runId, execution_scope: 'formal_benchmark', phase: 'verified', time: { tick: 300 },
  verifier_public: { run_id: runId, status: 'passed', coverage_complete: true,
    goals: ['parcel.pickup', 'parcel.transport', 'parcel.dropoff', 'parcel.delivered', 'carrier.terminal']
      .map(goal_id => ({ goal_id, passed: true })) },
  scene_states: Array.from({ length: 300 }, (_, index) => ({ run_id: runId, at: { tick: index + 1 } })) });
test('retains the actual read-only credentials without inventing runtime identity or status', () => {
  const actual = access(); assert.equal(readonlyReplayCredentials(actual, runId), actual.credentials);
  assert.equal('start_id' in actual.credentials, false);
});
test('rejects write access, foreign credentials, and malformed credential contracts', () => {
  for (const mutate of [value => value.read_only = false, value => value.credentials.run_id = 'd'.repeat(64),
    value => value.credentials.operator_token = '', value => value.credentials.schema_version = 'wrong']) {
    const value = access(); mutate(value); assert.throws(() => readonlyReplayCredentials(value, runId));
  }
});
test('accepts a complete measured same-run horizon and its verified public report', () => {
  assert.doesNotThrow(() => assertVerifiedParcelHistory(trace(), runId, 300));
});
test('rejects missing or failed public verification rather than accepting physics success alone', () => {
  for (const mutate of [value => value.verifier_public = null, value => value.verifier_public.status = 'failed',
    value => value.verifier_public.coverage_complete = false, value => value.verifier_public.goals[4].passed = false,
    value => value.phase = 'sealed']) {
    const value = trace(); mutate(value); assert.throws(() => assertVerifiedParcelHistory(value, runId, 300));
  }
});
test('rejects truncated, skipped, or foreign scene history', () => {
  for (const mutate of [value => value.scene_states.shift(), value => value.scene_states[60].at.tick = 63,
    value => value.scene_states[0].run_id = 'd'.repeat(64), value => value.time.tick = 299]) {
    const value = trace(); mutate(value); assert.throws(() => assertVerifiedParcelHistory(value, runId, 300));
  }
});
test('audits actual requests and excludes credentials from read-only replay evidence', () => {
  const audit = new SealedCaptureRequestAudit('http://127.0.0.1:8769');
  audit.observe('http://127.0.0.1:5416/index.html', 'GET', 1);
  audit.observe(`http://127.0.0.1:8769/v1/runs/${runId}/replay-access?secret=private`, 'POST', 2);
  audit.observe(`http://127.0.0.1:8769/v1/runs/${runId}/public/trace`, 'GET', 3);
  assert.doesNotThrow(() => audit.assertReadOnly());
  assert.equal(audit.summary().start_requests, 0);
  assert.equal(audit.summary().requests.length, 2);
  assert.equal(JSON.stringify(audit.summary()).includes('private'), false);
});
test('rejects observed Start, status, stream, and runtime control traffic', () => {
  for (const [path, method] of [['/v1/runs', 'POST'], [`/v1/runs/${runId}`, 'GET'],
    [`/v1/runs/${runId}/events`, 'GET'], [`/v1/runs/${runId}/control`, 'POST']]) {
    const audit = new SealedCaptureRequestAudit('http://127.0.0.1:8769');
    audit.observe(`http://127.0.0.1:8769${path}`, method, 1);
    assert.throws(() => audit.assertReadOnly());
    assert.equal(audit.summary().forbidden_requests, 1);
  }
});
