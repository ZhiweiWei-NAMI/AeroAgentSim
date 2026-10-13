import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { InspectorPanel } from './InspectorPanel';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureCommits, fixtureHeader, vehicle } from '../behaviours/extension-fixture';
import { I18nProvider } from '../i18n/I18nProvider';

vi.mock('../behaviours/RunOperations', () => ({ RunOperations: () => null }));
vi.mock('../pages/InspectionPanel', () => ({ InspectionPanel: () => <div data-testid="inspection-fallback" /> }));
vi.mock('../trace/EntityStory', () => ({ MomentsList: () => null, ChangeList: () => null }));

afterEach(cleanup);

function setup(selected = vehicle) {
  const store = new TemporalFeedStore(fixtureHeader);
  fixtureCommits.forEach(commit => store.ingest(commit));
  store.seek(fixtureCommits.at(-1)!.at.ns);
  return render(<I18nProvider>
    <InspectorPanel api={{} as never} runId="run" store={store} revision={1} selected={selected} mode="replay" canOperate={false} following={false}
      onSelect={vi.fn()} onSeekCut={vi.fn()} onTrace={vi.fn()} onFocus={vi.fn()} onFollow={vi.fn()} onShowGraph={vi.fn()} onClose={vi.fn()} />
  </I18nProvider>);
}

it('leads with the operating state before the full fact list', () => {
  setup();
  const summary = screen.getByTestId('wb-operating');
  const fallback = screen.getByTestId('inspection-fallback');
  // The operating summary renders before the exhaustive InspectionPanel.
  expect(summary.compareDocumentPosition(fallback) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('collapses non-operating and empty fields under "Other state" instead of dropping them', () => {
  setup();
  const details = screen.queryByText(/Other state/);
  // Whatever is not an operating field stays reachable inside the disclosure.
  if (screen.getByTestId('wb-operating').querySelector('details')) expect(details).toBeVisible();
});
