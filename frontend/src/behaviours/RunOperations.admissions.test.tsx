import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { ConsoleNotifications } from '../console/Notifications';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';
import { RunOperations } from './RunOperations';

// Typed-form rendering is covered by its owner; these tests cover receipt persistence.
vi.mock('./TypedPayloadForm', () => ({ TypedPayloadForm: () => <div data-testid="typed-payload" /> }));
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); });

function runStore() {
  const store = new TemporalFeedStore({ ...fixtureHeader, kernelRunId: 'kernel.actual' });
  store.ingest(emptyCommit(7)); store.seek('0', 7);
  return store;
}
function mount(api: RunsApi) {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  return render(<I18nProvider><MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><ConsoleNotifications><RunOperations api={api} runId="service/run id" store={runStore()} onSelect={() => {}} onSeek={() => {}} mode="live" interactive={false} /></ConsoleNotifications></MemoryRouter></I18nProvider>);
}

it('RUN-008: restores journaled admission receipts on mount so a refresh keeps the admitted operation visible', async () => {
  const api = new RunsApi('http://api');
  vi.spyOn(api, 'request').mockImplementation(async path => {
    if (String(path).endsWith('/admissions')) return [{ id: 'cmd-9', submitted_at: '2026-10-13T08:00:00Z', target_cut: 130, kind: 'ingress', status: 'admitted' }];
    if (String(path).endsWith('/configuration')) return { scenario: {} };
    if (String(path).includes('/commits?')) return { status: 'paused', commits: [], next: 8 };
    throw Error(`Unexpected request ${path}`);
  });
  mount(api);
  const summary = await screen.findByText(/Admission receipts · 1/);
  expect(summary).toBeVisible();
  // Awaiting execution is stated plainly; success or failure is never invented.
  expect(screen.getByText(/awaiting execution/i)).toBeVisible();
  expect(screen.getByText(/cmd-9 · 2026-10-13T08:00:00Z · target cut 130/)).toBeVisible();
});

it('RUN-008: merges a journaled outcome into the same receipt instead of adding an unrelated row', async () => {
  const api = new RunsApi('http://api');
  const admissions = vi.fn().mockResolvedValue([{ id: 'cmd-9', status: 'applied', target_cut: 130 }]);
  Object.defineProperty(api, 'admissions', { value: admissions });
  vi.spyOn(api, 'request').mockImplementation(async path => {
    if (String(path).endsWith('/configuration')) return { scenario: {} };
    if (String(path).includes('/commits?')) return { status: 'paused', commits: [], next: 8 };
    throw Error(`Unexpected request ${path}`);
  });
  mount(api);
  await waitFor(() => expect(admissions).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByText(/Admission receipts · 1/)).toBeVisible());
  expect(screen.getByText(/applied/i)).toBeVisible();
});
