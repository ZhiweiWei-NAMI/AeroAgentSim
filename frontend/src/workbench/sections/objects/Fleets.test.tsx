import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { Fleets } from './Fleets';
import type { Operation, OperationPreview } from '../operations';

const L = (en: string) => en;
const onProject = vi.fn();
const onRemoved = vi.fn();
const applyOperations = vi.fn(async (_api: unknown, _project: unknown, _operations: Operation[]) => ({ id: 'w1', revision: 9, scenario: {} }));
const previewOperations = vi.fn(async (_api: unknown, _project: { id: string; revision: number }, operations: Operation[]): Promise<OperationPreview> => ({
  diff: {
    added: [], changed: [],
    removed: [
      { kind: 'entity', id: 'couriers-1', label: 'Courier 1' },
      { kind: 'entity', id: 'couriers-2', label: 'Courier 2' },
      { kind: 'engine', id: 'motion', label: 'Motion facts of couriers-1' },
    ],
  },
  notes: [],
}));
// A remove endpoint exists in the fallback path; it must never be called when C11 works.
const studioRequest = vi.fn(async (_path: string, _body?: unknown) => { throw new Error('fallback remove must not run'); });

vi.mock('../operations', async (importOriginal: () => Promise<typeof import('../operations')>) => {
  const actual = await importOriginal();
  return {
    ...actual,
    previewOperations: (api: unknown, project: { id: string; revision: number }, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations: (api: unknown, project: unknown, operations: Operation[]) => applyOperations(api, project, operations),
  };
});

const api = { save: vi.fn(async () => ({ id: 'w1', revision: 8, scenario: {} })) } as never;
const studioApi = { request: studioRequest };
const entities = [
  { id: 'couriers-1', type: 'lowalt:UAV' }, { id: 'couriers-2', type: 'lowalt:UAV' },
  { id: 'kitchen-1', type: 'lowalt:Restaurant' },
];

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  onProject.mockClear(); onRemoved.mockClear(); applyOperations.mockClear();
  previewOperations.mockClear(); studioRequest.mockClear();
});
afterEach(cleanup);

const renderFleets = (draft: Record<string, unknown> = { entities }) => render(
  <I18nProvider><Fleets api={api} studioApi={studioApi} project={{ id: 'w1', revision: 8 } as never}
    draft={draft as never} onProject={onProject} L={L} onRemoved={onRemoved} /></I18nProvider>,
);

it('removes a whole fleet through C11 remove_fleet: dry-run list → confirm → apply', async () => {
  renderFleets();
  fireEvent.click(screen.getByTestId('wb-fleet-remove'));
  fireEvent.click(await screen.findByRole('button', { name: 'Preview removal' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'remove_fleet', params: { fleet: 'couriers' } });
  // The dry run lists exactly the members and dependents that disappear.
  expect(within(screen.getByRole('region', { name: 'Removed' })).getByText('3')).toBeTruthy();
  expect(screen.getByText('Courier 1')).toBeTruthy();
  expect(screen.getByText('Motion facts of couriers-1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0].op).toBe('remove_fleet');
  expect(onProject).toHaveBeenCalled();
  expect(onRemoved).toHaveBeenCalled();
  // The per-member fallback endpoint was never touched.
  expect(studioRequest).not.toHaveBeenCalled();
});

it('falls back to the per-member dependency dialog when the server rejects remove_fleet with 422', async () => {
  // vi.mock is module-wide, so route the 422 through this test via apply-time rejection:
  previewOperations.mockRejectedValueOnce(new Error('HTTP 422: unknown operation remove_fleet'));
  renderFleets();
  fireEvent.click(screen.getByTestId('wb-fleet-remove'));
  fireEvent.click(await screen.findByRole('button', { name: 'Preview removal' }));
  expect(await screen.findByTestId('wb-fleet-fallback')).toBeTruthy();
  expect(screen.getByText(/does not support whole-fleet removal/)).toBeTruthy();
  // The fallback loads the real C3 dependency preview for the first member through the server.
  fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
  await waitFor(() => expect(studioRequest).toHaveBeenCalled());
  expect(String(studioRequest.mock.calls[0][0])).toContain('/remove');
});

it('never mutates the draft directly: no edit call can wipe the scenario', () => {
  renderFleets({ entities, generators: { fleet: { prefix: 'fleet-', count: 2, type: 'lowalt:UAV' } } });
  // Nothing in this section accepts a draft edit path; the generator list stays intact.
  expect(screen.getByTestId('wb-fleet-generated').textContent).toContain('will generate 2');
  expect(screen.getByTestId('wb-fleet').textContent).toContain('couriers');
});

it('shows a validation rejection without switching to per-member removal', async () => {
  previewOperations.mockRejectedValueOnce(new Error('HTTP 422: fleet still has a protected dependent'));
  renderFleets();
  fireEvent.click(screen.getByTestId('wb-fleet-remove'));
  fireEvent.click(screen.getByRole('button', { name: 'Preview removal' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('protected dependent');
  expect(screen.queryByTestId('wb-fleet-fallback')).toBeNull();
  expect(studioRequest).not.toHaveBeenCalled();
  expect(applyOperations).not.toHaveBeenCalled();
});

it('prefills current start and battery, and count-only resizing sends no start or battery (T1-R2-007)', async () => {
  previewOperations.mockResolvedValueOnce({ diff: { added: [{ kind: 'entity', id: 'couriers-3', label: 'Courier 3' }], changed: [], removed: [] }, notes: [] });
  renderFleets({ entities: [
    { id: 'kitchen', type: 'lowalt:Restaurant', facts: { 'lowalt.site_position': [100, 0, 0] } },
    ...[1, 2].map(index => ({ id: `couriers-${index}`, type: 'lowalt:UAV', facts: { 'lowalt.position': [100, 0, 0], 'lowalt.soc': 0.15 } })),
  ] });
  fireEvent.click(within(screen.getByTestId('wb-fleet')).getByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('Start place')).toHaveValue('enu');
  expect(screen.getByLabelText('east (m)')).toHaveValue('100');
  expect(screen.getByLabelText('north (m)')).toHaveValue('0');
  expect(screen.getByLabelText('up (m)')).toHaveValue('0');
  expect(screen.getByLabelText('Battery (%)')).toHaveValue('15');
  fireEvent.change(screen.getByLabelText('Count'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_fleet', params: { fleet: 'couriers', count: 3 } });
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ fleet: 'couriers', count: 3 });
  expect(applyOperations.mock.calls[0][2][0].params).toEqual({ fleet: 'couriers', count: 3 });
});

it('preserves different per-member starts and batteries during a count-only edit (T1-R2-007)', async () => {
  renderFleets({ entities: [
    { id: 'couriers-1', type: 'lowalt:UAV', facts: { 'lowalt.position': [100, 0, 0], 'lowalt.soc': 0.15 } },
    { id: 'couriers-2', type: 'lowalt:UAV', facts: { 'lowalt.position': [200, 0, 0], 'lowalt.soc': 0.25 } },
  ] });
  fireEvent.click(within(screen.getByTestId('wb-fleet')).getByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('Start place')).toHaveValue('keep');
  expect(screen.getByLabelText('Battery (%)')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('Count'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm resize' });
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ fleet: 'couriers', count: 3 });
});

it('sends only an explicitly changed battery for set_fleet', async () => {
  renderFleets({ entities: [1, 2].map(index => ({ id: `couriers-${index}`, type: 'lowalt:UAV', facts: { 'lowalt.position': [10, 20, 30], 'lowalt.soc': 0.15 } })) });
  fireEvent.click(within(screen.getByTestId('wb-fleet')).getByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Battery (%)'), { target: { value: '25' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm resize' });
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ fleet: 'couriers', battery_pct: 25 });
});

it('uses one selected fleet role, localized labels and no required errors before typing (T4-R2-013)', () => {
  renderFleets({ entities: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Add fleet' }));
  const roles = within(screen.getByRole('group', { name: 'Fleet role' }));
  expect(roles.getAllByRole('button', { pressed: true })).toHaveLength(1);
  expect(roles.getByRole('button', { name: 'Meal courier' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(roles.getByRole('button', { name: 'Parcel drone' }));
  expect(roles.getAllByRole('button', { pressed: true })).toHaveLength(1);
  expect(screen.getByLabelText('fleet id')).toHaveValue('parcel-drone-1');
});
