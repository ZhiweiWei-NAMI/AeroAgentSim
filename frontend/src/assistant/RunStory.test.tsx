import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { RunStory } from './RunStory';
import type { Story } from './api';

afterEach(cleanup);

const story = (items: Story['expectations']): Story => ({
  run_id: 'r1', terminal: true, plan_version: 1, expectations: items, events: [],
});

const renderStory = (items: Story['expectations']) => render(
  <I18nProvider><RunStory story={story(items)} terminal startNs="0" /></I18nProvider>,
);

it('shows a server-declared not_evaluated check as neutral, never a green success', () => {
  renderStory([
    { id: 'deliveries', text: 'Deliveries completed', status: 'happened' },
    { id: 'gust', text: 'Wind gust held a vehicle', status: 'not_evaluated' },
  ] as Story['expectations']);
  const row = screen.getByTestId('as-story-not_evaluated');
  // Neutral grey: the happened/green styling is replaced, the tag says why.
  expect(row.className).toContain('is-unevaluated');
  expect(row.className).not.toContain('is-happened');
  expect(row.textContent).toMatch(/not evaluated/);
  // The score counts only the real check, and the bar omits the unevaluated one.
  expect(screen.getByTestId('as-story').textContent).toMatch(/1\s*\/\s*1/);
  expect(screen.getByTestId('as-story-unevaluated').textContent).toMatch(/1 check with nothing expected/);
});

it('treats a check whose expected count is 0 (progress total 0) as not evaluated too', () => {
  renderStory([{ id: 'gust', text: 'Wind gust held a vehicle', status: 'happened', progress: { done: 0, total: 0 } }]);
  const row = screen.getByTestId('as-story-happened');
  expect(row.className).toContain('is-unevaluated');
  expect(row.className).not.toContain('is-happened');
  expect(row.textContent).toMatch(/not evaluated/);
  expect(screen.getByTestId('as-story').textContent).toMatch(/0\s*\/\s*0/);
  expect(screen.queryByText('Everything the plan expected happened')).toBeNull();
});

it('does not claim success when the run has no evaluated expectations', () => {
  renderStory([]);
  expect(screen.getByText('Run ended')).toBeVisible();
  expect(screen.queryByText('Everything the plan expected happened')).toBeNull();
});

it('keeps the neutral wording in Chinese', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  renderStory([{ id: 'gust', text: 'Gust', text_zh: '阵风', status: 'not_evaluated' } as never]);
  expect(screen.getByTestId('as-story-not_evaluated').textContent).toMatch(/未评估/);
  expect(screen.getByTestId('as-story-unevaluated').textContent).toMatch(/未评估/);
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
});

it('still scores real successes and misses normally', () => {
  renderStory([
    { id: 'a', text: 'A', status: 'happened' },
    { id: 'b', text: 'B', status: 'missed' },
  ]);
  expect(screen.getByTestId('as-story-happened').className).toContain('is-happened');
  expect(screen.getByTestId('as-story-missed').className).toContain('is-missed');
  expect(screen.getByTestId('as-story').textContent).toMatch(/1 expected step did not happen/);
});
