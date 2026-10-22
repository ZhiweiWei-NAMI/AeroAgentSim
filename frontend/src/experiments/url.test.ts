import { afterEach, expect, it } from 'vitest';
import { experimentIdFromLocation, setExperimentQuery } from './url';

afterEach(() => window.history.replaceState({}, '', '/'));

it('writes a bookmarkable experiment id with records=1 and clears only the experiment key', () => {
  window.history.replaceState({}, '', '/w/project-1?panel=space&gallery=1');
  setExperimentQuery('exp-9');
  expect(experimentIdFromLocation()).toBe('exp-9');
  expect(window.location.search).toContain('experiment=exp-9');
  expect(window.location.search).toContain('records=1');
  expect(window.location.search).toContain('panel=space');
  expect(window.location.search).not.toContain('gallery=1');
  setExperimentQuery(undefined);
  expect(experimentIdFromLocation()).toBeUndefined();
  expect(window.location.search).not.toContain('experiment=');
  expect(window.location.search).toContain('panel=space');
});
