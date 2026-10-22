import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { EventFeed } from './EventFeed';

beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/?run=run-7&mode=replay'); });
afterEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/'); });

it('honestly links the Records panel when no raw disclosure action is supplied', () => {
  render(<EventFeed events={[]} available={false} />);
  expect(screen.queryByText(/Expert view/i)).toBeNull();
  const link = screen.getByTestId('event-feed-raw-records');
  expect(link).toHaveAttribute('href', '/?run=run-7&mode=replay&records=1');
  expect(link).toHaveTextContent('Open the run’s Records panel');
  expect(screen.getByRole('status')).not.toHaveTextContent('Open its raw records');
  expect(link.getAttribute('href')).not.toBe('#');
});

it.each(['en-US', 'zh-CN'])('preserves the workspace, run, API and cut in the Records-panel fallback (%s)', locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  window.history.replaceState(null, '', '/w/project-1?run=run-7&mode=replay&api=https%3A%2F%2Fapi.example&cut=3&t=9007199254740993');
  render(<I18nProvider><EventFeed events={[]} available={false} /></I18nProvider>);
  const link = screen.getByTestId('event-feed-raw-records');
  const target = new URL(link.getAttribute('href')!, window.location.origin);
  expect(target.pathname).toBe('/w/project-1');
  expect(Object.fromEntries(target.searchParams)).toEqual({
    run: 'run-7', mode: 'replay', api: 'https://api.example', cut: '3', t: '9007199254740993', records: '1',
  });
  expect(link).toHaveTextContent(locale === 'zh-CN' ? '打开运行的“记录”面板' : 'Open the run’s Records panel');
  expect(screen.getByRole('status')).not.toHaveTextContent(/原始记录|raw records/);
});

it('keeps the destination reachable when the URL carries no run id', () => {
  window.history.replaceState(null, '', '/');
  render(<EventFeed events={[]} available={false} />);
  expect(screen.getByTestId('event-feed-raw-records')).toHaveAttribute('href', '/?records=1');
});

it('uses bilingual wording and honours an explicit raw-record destination', () => {
  localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  render(<I18nProvider><EventFeed events={[]} available={false} rawRecordsHref="#typed-records" /><pre id="typed-records">ordinary typed message</pre></I18nProvider>);
  expect(screen.queryByText(/专家视图/)).toBeNull();
  expect(screen.getByText(/没有可读的事件摘要/)).toBeInTheDocument();
  expect(screen.getByText(/原始记录/)).toBeInTheDocument();
  expect(screen.getByTestId('event-feed-raw-records')).toHaveAttribute('href', '#typed-records');
  expect(screen.getByTestId('event-feed-raw-records')).toHaveTextContent('查看已记录的活动');
});

it('still reports an explicit explore action rather than inventing a second destination', () => {
  const explore = vi.fn();
  render(<EventFeed events={[]} available={false} onExplore={explore} />);
  fireEvent.click(screen.getByRole('button', { name: 'Explore recorded activity' }));
  expect(explore).toHaveBeenCalledOnce();
  expect(screen.queryByTestId('event-feed-raw-records')).toBeNull();
});
