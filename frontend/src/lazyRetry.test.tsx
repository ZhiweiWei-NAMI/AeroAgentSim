import { Suspense, type ComponentType, type ReactElement } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { failedModuleUrl, lazyWithRetry, retryFailedLazyImports, retryModuleUrl } from './lazyRetry';
import { ConfigureBoundary } from './workbench/ConfigPanel';

beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => undefined); });
afterEach(() => { cleanup(); retryFailedLazyImports(); vi.restoreAllMocks(); });

function mount(Panel: ComponentType<{ title: string }>, locale = 'en-US') {
  const L = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  return render(<>
    <input aria-label="Sibling seed" defaultValue="9" />
    <ConfigureBoundary label={L('Algorithms & agents', '算法与智能体')} L={L}>
      <Suspense fallback={<p role="status">Loading panel</p>}><Panel title="Actual panel" /></Suspense>
    </ConfigureBoundary>
  </>);
}

it.each(['en-US', 'zh-CN'])('T4-R5-002 / T3-R5-002: Retry discards a rejected import and preserves sibling inputs in %s', async locale => {
  const component = ({ title }: { title: string }) => <p>{title}</p>;
  const load = vi.fn(async () => ({ default: component })).mockRejectedValueOnce(Error('Temporary download failure'));
  const Panel = lazyWithRetry(load);
  mount(Panel, locale);
  fireEvent.change(screen.getByLabelText('Sibling seed'), { target: { value: '17' } });
  expect(await screen.findByRole('alert')).toHaveTextContent('Temporary download failure');
  expect(load).toHaveBeenCalledTimes(1); // No hidden retry before the user asks.
  fireEvent.click(screen.getByRole('button', { name: locale === 'zh-CN' ? '重试' : 'Retry' }));
  expect(await screen.findByText('Actual panel')).toBeVisible();
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText('Sibling seed')).toHaveValue('17');
});

it('T4-R5-002: an explicit retry requests a fresh module URL and keeps the named export selector', async () => {
  const url = `${window.location.origin}/assets/AlgorithmsSection.js`;
  const module = { AlgorithmsSection: ({ title }: { title: string }) => <p>{title}</p> };
  const load = vi.fn(async (): Promise<typeof module> => { throw new TypeError(`Failed to fetch dynamically imported module: ${url}`); });
  const reimport = vi.fn(async (_url: string) => module).mockRejectedValueOnce(new TypeError(`Failed to fetch dynamically imported module: ${url}?aas-lazy-retry=1`));
  const Panel = lazyWithRetry(load, (value: typeof module) => value.AlgorithmsSection, reimport);
  mount(Panel);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByRole('alert');
  expect(reimport).toHaveBeenNthCalledWith(1, `${url}?aas-lazy-retry=1`);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('Actual panel')).toBeVisible();
  expect(reimport).toHaveBeenNthCalledWith(2, `${url}?aas-lazy-retry=2`);
  expect(load).toHaveBeenCalledTimes(1);
});

it('retains the resolved panel and form state when another failed import is retried', async () => {
  const loaded = vi.fn(async () => ({ default: () => <input aria-label="Resolved form" defaultValue="42" /> }));
  const Stable = lazyWithRetry(loaded);
  const failing = vi.fn(async () => ({ default: ({ title }: { title: string }) => <p>{title}</p> })).mockRejectedValueOnce(Error('Download failure'));
  const Failed = lazyWithRetry(failing);
  function View() {
    return <><Suspense fallback={<p>Waiting</p>}><Stable /></Suspense>
      <ConfigureBoundary label="Run" L={en => en}><Suspense fallback={<p>Loading panel</p>}><Failed title="Actual panel" /></Suspense></ConfigureBoundary></>;
  }
  const view = render(<View />);
  fireEvent.change(await screen.findByLabelText('Resolved form'), { target: { value: '43' } });
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('Actual panel');
  view.rerender(<View />);
  expect(screen.getByLabelText('Resolved form')).toHaveValue('43');
  expect(loaded).toHaveBeenCalledTimes(1);
});

it('shows Suspense while the re-import is pending, then renders the downloaded panel', async () => {
  type PanelModule = { default: ({ title }: { title: string }) => ReactElement };
  let finish!: (value: PanelModule) => void;
  const load = vi.fn(() => new Promise<PanelModule>(resolve => { finish = resolve; })).mockRejectedValueOnce(Error('Download failure'));
  const Panel = lazyWithRetry(load);
  mount(Panel);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(screen.getByRole('status')).toHaveTextContent('Loading panel');
  await act(async () => { finish({ default: ({ title }) => <p>{title}</p> }); });
  expect(screen.getByText('Actual panel')).toBeVisible();
});

it('limits fresh import URLs to same-origin scripts and preserves existing search parameters', () => {
  const url = `${window.location.origin}/assets/Run.js?v=5`;
  expect(failedModuleUrl(new TypeError(`error loading dynamically imported module: ${url}`))).toBe(url);
  expect(failedModuleUrl(new TypeError('Failed to fetch dynamically imported module: https://outside.example/Run.js'))).toBeUndefined();
  expect(failedModuleUrl(new Error('Component render failed'))).toBeUndefined();
  expect(retryModuleUrl(url, 2)).toBe(`${url}&aas-lazy-retry=2`);
});
