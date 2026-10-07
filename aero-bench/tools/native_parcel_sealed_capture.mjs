const PARCEL_GOALS = ['parcel.pickup', 'parcel.transport', 'parcel.dropoff', 'parcel.delivered', 'carrier.terminal'];
const TOKEN = /^[0-9a-f]{64}$/;

/** Record actual control requests without headers, query strings, or bodies. */
export class SealedCaptureRequestAudit {
  constructor(endpoint) { this.endpoint = new URL(endpoint).origin; this.requests = []; }
  observe(url, method, elapsedS) {
    const target = new URL(url);
    if (target.origin !== this.endpoint) return;
    const path = target.pathname;
    const forbidden = (path === '/v1/runs' && method === 'POST')
      || /^\/v1\/runs\/[0-9a-f]{64}(?:\/(?:events|controls\/[^/]+))?$/.test(path);
    this.requests.push({ method, path, elapsed_s: elapsedS, forbidden });
  }
  summary() {
    return { requests: this.requests, start_requests: this.requests.filter(item => item.method === 'POST' && item.path === '/v1/runs').length,
      forbidden_requests: this.requests.filter(item => item.forbidden).length };
  }
  assertReadOnly() {
    if (this.summary().forbidden_requests !== 0) throw new Error('Sealed-only capture issued a runtime Start, status, stream, or control request');
  }
}

/** Accept only credentials actually issued for read-only access to this run. */
export function readonlyReplayCredentials(access, runId) {
  const credentials = access?.credentials;
  if (access?.schema_version !== 'aero-bench.replay-access-response/v1' || access.read_only !== true
      || credentials?.schema_version !== 'aero-bench.run-access-credentials/v1'
      || credentials.run_id !== runId || !TOKEN.test(credentials.operator_token)
      || !TOKEN.test(credentials.csrf_token)) {
    throw new Error('Read-only replay access is not bound to the expected run and credential contract');
  }
  return credentials;
}

/** A sealed replay must cover the declared horizon, including every recorded tick. */
export function assertVerifiedParcelHistory(trace, runId, expectedLastTick) {
  const report = trace?.verifier_public;
  if (trace?.run_id !== runId || trace.execution_scope !== 'formal_benchmark' || trace.phase !== 'verified'
      || report?.run_id !== runId || report.status !== 'passed' || report.coverage_complete !== true
      || !PARCEL_GOALS.every(id => report.goals?.filter(goal => goal.goal_id === id && goal.passed === true).length === 1)) {
    throw new Error('Sealed parcel capture requires the same-run verified public report with all five goals passed');
  }
  const states = trace.scene_states;
  const first = states?.[0]?.at?.tick;
  if (!Array.isArray(states) || !Number.isInteger(first) || first < 0 || first > 1
      || trace.time?.tick !== expectedLastTick || states.length !== expectedLastTick - first + 1
      || !states.every((state, index) => state.run_id === runId && state.at.tick === first + index)) {
    throw new Error('Sealed parcel history does not cover every tick through the declared horizon');
  }
}
