import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import type { Project, WorkbenchApi } from '../../api';
import { getOperationsState, mutateWorkspace, operation, previewOperations } from '../operations';
import { OrderStreams } from './OrderStreams';

beforeEach(() => { sessionStorage.clear(); localStorage.setItem('aeroagentsim.locale', 'en-US'); });
afterEach(cleanup);
const base = { id: 'orders-lock', revision: 1 } as Project;
const draft = { run: { until_ns: 600_000_000_000 }, entities: [] };
const config = { generators: [{ id: 'meal', kind: 'food', tasks: ['meal-1', 'meal-2'], arrivals: { mode: 'scheduled', times_ns: [0, 10_000_000_000] } }] };

function setup(api: WorkbenchApi) {
  const onProject = vi.fn();
  render(<I18nProvider><OrderStreams api={api} project={base} onProject={onProject} saveDraft={async () => base}
    zh={false} L={en => en} config={config} draft={draft} edit={() => undefined} /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: /meal.*Meal.*2 tasks/ }));
  return { onProject };
}

it('C11 order removal holds the same workspace gate until the authoritative read finishes', async () => {
  let read!: (value: Project) => void;
  const request = vi.fn(async (_path: string, init?: { body?: unknown }) => {
    if ((init?.body as { dry_run?: boolean }).dry_run) return { diff: { added: [], changed: [], removed: [{ kind: 'entity', id: 'meal-1', label: 'Meal order 1' }] }, notes: [] };
    expect(getOperationsState(base.id).applying).toBe(true);
    return { id: base.id, revision: 2 };
  });
  const project = vi.fn(() => new Promise<Project>(resolve => { read = resolve; }));
  const api = { request, project } as unknown as WorkbenchApi;
  const { onProject } = setup(api);
  fireEvent.click(screen.getByRole('button', { name: 'Remove stream…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Preview removal' }));
  const confirm = await screen.findByRole('button', { name: 'Confirm' });
  expect(screen.getByText('Meal order 1')).toBeVisible();
  expect(onProject).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(project).toHaveBeenCalledOnce());
  expect(getOperationsState(base.id).applying).toBe(true);
  expect(() => previewOperations(api, base, [operation('set_wind', { speed_mps: 3 })])).toThrow(/Applying/);
  const authoritative = { ...base, revision: 2 };
  await act(async () => read(authoritative));
  expect(onProject).toHaveBeenCalledWith(authoritative);
  expect(getOperationsState(base.id).applying).toBe(false);
  expect(request.mock.calls[1][1]?.body).toMatchObject({ operations: [{ op: 'remove_orders', params: { id: 'meal' } }], revision: 1 });
});

it('disables the order-removal entry point while another mutation is pending', async () => {
  const api = { request: vi.fn() } as unknown as WorkbenchApi;
  setup(api);
  let complete!: (value: Project) => void, pending!: Promise<Project>;
  act(() => { pending = mutateWorkspace(base, () => new Promise<Project>(resolve => { complete = resolve; })); });
  expect(screen.getByRole('button', { name: 'Remove stream…' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Remove stream…' }));
  expect(screen.queryByRole('button', { name: 'Preview removal' })).toBeNull();
  await act(async () => { complete(base); await pending; });
  expect(screen.getByRole('button', { name: 'Remove stream…' })).toBeEnabled();
});
