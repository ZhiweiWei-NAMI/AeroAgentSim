import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { PlanBoard } from './PlanBoard';
import type { AssistantApi, PlanVersion, Session } from './api';
import type { AssistantSession } from './useAssistant';
import { I18nProvider } from '../i18n/I18nProvider';

afterEach(() => cleanup());

const plan = (version: number, variant?: string): PlanVersion => ({
  version, created_at: '', note: variant ?? '', variant: variant ?? null, parent: version > 1 ? 1 : null,
  base: {}, base_label: 'your scene', title: `Plan ${variant ?? version}`, summary: `Summary ${version}`,
  operations: [], assumptions: ['Three UAVs fly the route'], open_questions: [],
  compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' },
  scene: {
    places: [{ id: 'pad-west', role: 'origin', label: 'West pad' }, { id: 'tower', role: 'destination', label: 'Tower' }],
    fleets: [], workloads: [], events: [],
  },
  diff_base: { added: [], changed: [], removed: [] },
  diff_previous: version === 5 ? {
    added: [{ kind: 'cooperation', id: 'coop', label: 'Fixed handoff rule' }], changed: [], removed: [],
  } : {
    added: [], removed: [],
    // v6 differs only in who decides: the cooperation rule becomes agent-decided.
    changed: [{ kind: 'cooperation', id: 'coop', label: 'Courier decides', fields: [{ name: 'mode', before: 'prescribed', after: 'agent' }] }],
  },
  interactions: undefined, expectations: [],
}) as unknown as PlanVersion;

const session: Session = {
  id: 's1', created_at: '', updated_at: '', workspace_id: null, title: 't', busy: false, last_seq: 0,
  events: [], plan: plan(6, 'B'), plan_versions: [
    { version: 1, note: '', compile_ok: true, validation_state: 'valid', created_at: '' },
    { version: 5, note: 'A', variant: 'A', compile_ok: true, validation_state: 'valid', created_at: '' },
    { version: 6, note: 'B', variant: 'B', compile_ok: true, validation_state: 'valid', created_at: '' },
  ], alternatives: [5, 6], applied: [],
};

const assistant = {
  session, events: [], busy: false, error: undefined, pending: undefined, plan: session.plan, applied: undefined,
  send: vi.fn().mockResolvedValue({ turn: 1, seq: 1 }), cancel: vi.fn(), choose: vi.fn().mockResolvedValue(session),
  apply: vi.fn(), refresh: vi.fn(),
} as unknown as AssistantSession;

const api = {
  plan: vi.fn(async (_id: string, version: number) => plan(version, version === 5 ? 'A' : 'B')),
} as unknown as AssistantApi;

const renderBoard = () => render(<Harness />);

function Harness() {
  const [viewing, setViewing] = useState<number | undefined>();
  return <I18nProvider>
    <PlanBoard api={api} assistant={assistant} stage="proposed" terminal={false} initializing={false}
      canInitialize={false} onInitialize={() => {}} onApplied={() => {}} onHover={() => {}} onStep={() => {}}
      onContract={() => {}} onSeek={() => {}} viewing={viewing} onViewVersion={setViewing} onClose={() => {}} />
  </I18nProvider>;
}

it('compares alternatives side by side without applying or choosing anything', async () => {
  renderBoard();
  fireEvent.click(await screen.findByTestId('as-alt-compare'));
  // Both variants appear in one view.
  expect(await screen.findByTestId('as-alt-5')).toBeVisible();
  expect(screen.getByTestId('as-alt-6')).toBeVisible();
  // Common items appear once per column; differences are listed per option.
  expect(screen.getAllByText('West pad').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('Fixed handoff rule')).toBeVisible();
  expect(screen.getByText('Courier decides')).toBeVisible();
  // Assumptions are shown per option.
  expect(screen.getAllByText('Three UAVs fly the route').length).toBeGreaterThanOrEqual(2);
  // Read-only: the comparison panel offers no apply and no choose; only the footer keeps the explicit confirm path.
  const panel = screen.getByTestId('as-alternatives');
  expect(within(panel).queryByTestId('as-confirm')).toBeNull();
  expect(within(panel).queryByTestId('as-choose')).toBeNull();
  expect(assistant.apply).not.toHaveBeenCalled();
  expect(assistant.choose).not.toHaveBeenCalled();
  // Opening one for review only switches which version the board shows; nothing applies.
  fireEvent.click(screen.getByTestId('as-alt-review-5'));
  await waitFor(() => expect(screen.getByTestId('as-alt-review-5')).toBeDisabled());
  expect(api.plan).toHaveBeenCalledWith('s1', 5, expect.anything());
  expect(assistant.apply).not.toHaveBeenCalled();
});

it('keeps the comparison closed by default and explains the read-only rule', async () => {
  renderBoard();
  expect(screen.queryByTestId('as-alternatives')).toBeNull();
  fireEvent.click(await screen.findByTestId('as-alt-compare'));
  expect(await screen.findByTestId('as-alternatives')).toHaveTextContent(/Confirm and apply/);
  // Closing restores the plain board.
  fireEvent.click(screen.getByRole('button', { name: /Close the side-by-side comparison/i }));
  expect(screen.queryByTestId('as-alternatives')).toBeNull();
});
