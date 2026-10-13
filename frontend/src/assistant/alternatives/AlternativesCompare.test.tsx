import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AlternativesCompare } from './AlternativesCompare';
import type { AssistantApi, PlanVersion } from '../api';
import { I18nProvider, STORAGE_KEY } from '../../i18n/I18nProvider';

afterEach(() => { cleanup(); localStorage.removeItem(STORAGE_KEY); });

const plan = (version: number, placeLabel: string, placeLabelZh: string): PlanVersion => ({
  version, created_at: '', note: '', variant: version === 5 ? 'A' : 'B', parent: null,
  base: {}, base_label: 'base', title: `Plan ${version}`, summary: `Summary ${version}`,
  operations: [], assumptions: [], open_questions: [],
  compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' },
  scene: { places: [{ id: 'pad-west', role: 'origin', label: placeLabel, label_zh: placeLabelZh }], fleets: [], workloads: [], events: [] },
  diff_previous: null,
}) as unknown as PlanVersion;

const api = {
  plan: vi.fn(async (_id: string, version: number) => version === 5 ? plan(5, 'West pad', '西发射台') : plan(6, 'West pad', '西发射台')),
} as unknown as AssistantApi;

it('marks which option is under review', async () => {
  render(<I18nProvider><AlternativesCompare api={api} sessionId="s1" versions={[5, 6]} current={5} onSelect={() => {}} onClose={() => {}} /></I18nProvider>);
  expect(await screen.findByTestId('as-alt-active-5')).toHaveTextContent(/under review/);
  expect(screen.queryByTestId('as-alt-active-6')).toBeNull();
  expect(screen.getByTestId('as-alt-5')).toHaveClass('is-current');
});

it('shows shared items in the current locale, not always in Chinese', async () => {
  const renderIn = (locale: string) => {
    localStorage.setItem(STORAGE_KEY, locale);
    return render(<I18nProvider><AlternativesCompare api={api} sessionId="s1" versions={[5, 6]} current={5} onSelect={() => {}} onClose={() => {}} /></I18nProvider>);
  };
  const en = renderIn('en-US');
  const shared = await en.findAllByText('West pad'); // shared list (1) + place labels come only via scenes; summary + common list
  expect(shared.length).toBeGreaterThanOrEqual(1);
  expect(en.queryAllByText('西发射台').length).toBe(0);
  en.unmount();
  const zh = renderIn('zh-CN');
  expect((await zh.findAllByText('西发射台')).length).toBeGreaterThanOrEqual(1);
  expect(zh.queryAllByText('West pad').length).toBe(0);
  const panel = zh.getByTestId('as-alternatives');
  expect(within(panel).getAllByText(/查看中|under review/).length).toBe(1);
});
