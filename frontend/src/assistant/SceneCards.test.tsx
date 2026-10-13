import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { SceneCards } from './SceneCards';
import type { SceneSummary } from './api';

afterEach(cleanup);

const base = {} as unknown as SceneSummary;

const renderCards = (scene: Partial<SceneSummary>) => render(
  <I18nProvider><SceneCards scene={{ ...base, ...scene } as SceneSummary} /></I18nProvider>,
);

it('shows a wind entry with a missing speed as “not set”, never 0 m/s', () => {
  renderCards({ environment: { wind: { gusts: [] } } });
  const env = screen.getByText(/wind/);
  expect(env.textContent).toMatch(/not set/);
  expect(env.textContent).not.toMatch(/0\s*m\/s|0 米\/秒/);
});

it('still renders an explicit numeric speed and keeps gusts untouched', () => {
  renderCards({ environment: { wind: { speed_mps: 14, gusts: [{ start_s: 0, end_s: 30, speed_mps: 14 }] } } });
  expect(screen.getByText(/14 m\/s wind/)).toBeTruthy();
  expect(screen.getByText(/gust 14 m\/s/)).toBeTruthy();
});

it('uses the Chinese wording for a missing speed', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  renderCards({ environment: { wind: {} } });
  expect(screen.getByText(/风速未设置/)).toBeTruthy();
  expect(screen.queryByText(/米\/秒/)).toBeNull();
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
});
