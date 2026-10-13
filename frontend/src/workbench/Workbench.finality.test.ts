import { expect, it } from 'vitest';
import { resultsPending } from './Workbench';

it('shows aggregation in progress only for a terminal status with results_final=false (C6 / AST-022)', () => {
  expect(resultsPending('completed', { results_final: false })).toBe(true);
  expect(resultsPending('stopped', { results_final: false })).toBe(true);
  expect(resultsPending('completed', { results_final: true })).toBe(false);
  // Older servers without the field never show a pending state.
  expect(resultsPending('completed', {})).toBe(false);
  expect(resultsPending('completed', undefined)).toBe(false);
  expect(resultsPending('running', { results_final: false })).toBe(false);
  expect(resultsPending(undefined, { results_final: false })).toBe(false);
});
