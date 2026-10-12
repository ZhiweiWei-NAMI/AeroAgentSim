import { expect, it } from 'vitest';
import { runStatus } from './Workbench';

it('shows the furthest lifecycle stage between the run record and the live stream', () => {
  expect(runStatus('initialized', 'running')).toBe('running');
  expect(runStatus('running', 'paused')).toBe('running');
  expect(runStatus('paused', 'running')).toBe('paused');
  expect(runStatus('running', 'completed')).toBe('completed');
  expect(runStatus('completed', 'running')).toBe('completed');
  expect(runStatus('initialized', 'loading')).toBe('initialized');
  expect(runStatus(undefined, 'running')).toBe('running');
});
