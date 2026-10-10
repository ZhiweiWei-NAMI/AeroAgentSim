import '@testing-library/jest-dom';
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { I18nProvider, useI18n, initialLocale, STORAGE_KEY } from './I18nProvider';
import { translate } from './translate';

function LocaleProbe() {
  const { locale, setLocale, t } = useI18n();
  return <div><span data-testid="locale-value">{locale}</span><span>{t('viewerTitle')}</span>
    <span>{t('Show all {count} workspaces', { count: 7 })}</span>
    <button onClick={() => setLocale('zh-CN')}>switch-zh</button><button onClick={() => setLocale('en-US')}>switch-en</button></div>;
}
beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/'); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });
test('defaults to English, switches and persists Chinese with interpolation and document language', () => {
  const view = render(<I18nProvider><LocaleProbe /></I18nProvider>);
  expect(screen.getByText('Journal viewer')).toBeInTheDocument();
  fireEvent.click(screen.getByText('switch-zh'));
  expect(screen.getByTestId('locale-value')).toHaveTextContent('zh-CN');
  expect(screen.getByText('显示全部 7 个工作区')).toBeInTheDocument();
  expect(localStorage.getItem(STORAGE_KEY)).toBe('zh-CN');
  expect(document.documentElement.lang).toBe('zh-CN');
  view.unmount(); render(<I18nProvider><LocaleProbe /></I18nProvider>);
  expect(screen.getByTestId('locale-value')).toHaveTextContent('zh-CN');
});
test('URL lang=zh overrides saved English and an explicit switch updates the override', () => {
  localStorage.setItem(STORAGE_KEY, 'en-US'); window.history.replaceState(null, '', '/studio?lang=zh&workspace=demo');
  render(<I18nProvider><LocaleProbe /></I18nProvider>);
  expect(screen.getByTestId('locale-value')).toHaveTextContent('zh-CN');
  fireEvent.click(screen.getByText('switch-en'));
  expect(new URLSearchParams(location.search).get('lang')).toBe('en');
  expect(new URLSearchParams(location.search).get('workspace')).toBe('demo');
  expect(initialLocale()).toBe('en-US');
});
test('invalid locales and unavailable browser storage safely fall back to English', () => {
  localStorage.setItem(STORAGE_KEY, 'broken'); expect(initialLocale()).toBe('en-US');
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw Error('storage disabled'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error('storage disabled'); });
  expect(() => render(<I18nProvider><LocaleProbe /></I18nProvider>)).not.toThrow();
  fireEvent.click(screen.getByText('switch-zh')); expect(screen.getByTestId('locale-value')).toHaveTextContent('zh-CN');
});
test('default English context and pure helpers interpolate without regular-expression replacement surprises', () => {
  render(<LocaleProbe />);
  expect(screen.getByText('Show all 7 workspaces')).toBeInTheDocument();
  expect(translate('en-US', '{name}', { name: '$&' })).toBe('$&');
});
