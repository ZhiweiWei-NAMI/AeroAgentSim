import { describe, expect, it } from 'vitest';

import * as validators from './generated/contract-validators';
import {
  assertStartRunRequest,
  isStartRunRequest,
} from './generated/contract-validators';

describe('generated contract validators', () => {
  it('compiles every lazily compiled schema under strict Ajv', () => {
    const guards = Object.entries(validators).filter(([name]) => name.startsWith('is'));
    expect(guards.length).toBeGreaterThan(20);
    // The first call compiles the schema; strict-mode schema errors would throw here.
    for (const [name, guard] of guards) expect((guard as (value: unknown) => boolean)(null), name).toBe(false);
  });

  it('loads the closed Ajv graph and enforces strict request schemas', () => {
    const request = {
      schema_version: 'aero-bench.start-run-request/v1',
      start_id: 'start.1',
      run_id: 'a'.repeat(64),
    };

    expect(isStartRunRequest(request)).toBe(true);
    expect(() => assertStartRunRequest(request)).not.toThrow();
    expect(isStartRunRequest({ ...request, run_id: 'not-a-digest' })).toBe(false);
    expect(isStartRunRequest({ ...request, unexpected: true })).toBe(false);
  });
});
