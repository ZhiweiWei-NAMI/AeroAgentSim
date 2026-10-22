import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { EventFeed } from './EventFeed';

beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/?run=run-7&mode=replay'); });
afterEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/'); });

it('offers a reachable raw-record view instead of recommending a nonexistent Expert view', () => {
  render(<EventFeed events={[]} available={false} />);
  expect(screen.queryByText(/Expert view/i)).toBeNull();
  const link = screen.getByTestId('event-feed-raw-records');
  expect(link).toHaveAttribute('href', '/agents/run-7?mode=replay');
  expect(link).toHaveTextContent('Explore recorded activity');
  expect(link.getAttribute('href')).not.toBe('#');
});

it('keeps the destination reachable when the URL carries no run id', () => {
  window.history.replaceState(null, '', '/');
  render(<EventFeed events={[]} available={false} />);
  expect(screen.getByTestId('event-feed-raw-records')).toHaveAttribute('href', '/?records=1');
});

it('uses bilingual wording and honours an explicit raw-record destination', () => {
  localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  render(<I18nProvider><EventFeed events={[]} available={false} rawRecordsHref="/agents/run-7?cut=3" /></I18nProvider>);
  expect(screen.queryByText(/专家视图/)).toBeNull();
  expect(screen.getByText(/没有可读的事件摘要/)).toBeInTheDocument();
  expect(screen.getByText(/原始记录/)).toBeInTheDocument();
  expect(screen.getByTestId('event-feed-raw-records')).toHaveAttribute('href', '/agents/run-7?cut=3');
  expect(screen.getByTestId('event-feed-raw-records')).toHaveTextContent('查看已记录的活动');
});

it('still reports an explicit explore action rather than inventing a second destination', () => {
  const explore = vi.fn();
  render(<EventFeed events={[]} available={false} onExplore={explore} />);
  fireEvent.click(screen.getByRole('button', { name: 'Explore recorded activity' }));
  expect(explore).toHaveBeenCalledOnce();
  expect(screen.queryByTestId('event-feed-raw-records')).toBeNull();
});
