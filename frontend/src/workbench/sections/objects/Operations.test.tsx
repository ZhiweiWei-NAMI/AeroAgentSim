import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { LowaltStart } from './LowaltStart';
import { AddPlace } from './AddPlace';
import { AddFleet } from './AddFleet';
import type { WorkbenchApi } from '../../api';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; cleanup(); });

const L = (en: string, _zh?: string) => en;
const project = { id: 'w', revision: 7 };
const draft = { entities: [{ id: 'depot', type: 'lowalt:Depot', facts: { 'lowalt.site_position': [1, 2, 0] } }] } as never;
const emptyDiff = { diff: { added: [{ kind: 'fleet', id: 'couriers-1', label: 'Parcel drone 1' }], changed: [], removed: [] }, notes: [] };

function operationsApi(overrides: Record<string, unknown> = {}) {
  return {
    request: vi.fn().mockImplementation(async (_path: string, init?: { body?: any }) => {
      if (init?.body?.dry_run) return emptyDiff;
      return { ...project, revision: 8, capabilities: { lowalt: true, region: false, network: false, city: false } };
    }),
    project: vi.fn().mockResolvedValue({ ...project, revision: 8 }),
    ...overrides,
  };
}

it('LowaltStart previews the C11 dry-run diff and disables Apply until previewed again', async () => {
  const api = operationsApi();
  const onProject = vi.fn();
  render(<I18nProvider><LowaltStart api={api as unknown as WorkbenchApi} project={project as never} draft={{}} onProject={onProject} L={L} /></I18nProvider>);
  expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull(); // nothing without a shown diff
  fireEvent.click(screen.getByRole('button', { name: /Add standard low-altitude components/ }));
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  const [path, init] = (api.request as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(path).toBe('/v1/studio/workspaces/w/operations');
  expect(init.body.dry_run).toBe(true);
  expect(init.body.operations[0].op).toBe('add_lowalt_components');
  expect(await screen.findByText(/Server dry-run diff/)).toBeTruthy();
  expect(await screen.findByText('Parcel drone 1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
  expect(onProject.mock.calls[0][0].revision).toBe(8); // state comes from the server, not the form
});

it('AddPlace sends an add_place operation with role-specific fields only, after a dry run', async () => {
  const api = operationsApi();
  const onProject = vi.fn();
  render(<I18nProvider><AddPlace api={api as unknown as WorkbenchApi} project={project as never} draft={draft} onProject={onProject} L={L} onClose={() => undefined} /></I18nProvider>);
  // Prerequisite shown inline: no role chosen yet.
  expect(screen.getByText(/Choose a role to continue/)).toBeTruthy();
  const preview = screen.getByRole('button', { name: /Preview dry-run diff/ });
  expect(preview).toBeDisabled();
  fireEvent.click(screen.getByRole('radio', { name: /Charging pad/ }));
  fireEvent.change(screen.getByLabelText(/^id$/), { target: { value: 'pad-1' } });
  expect(preview).toBeEnabled();
  fireEvent.change(screen.getByLabelText('charging power (W)'), { target: { value: 'abc' } });
  expect(preview).toBeDisabled(); // invalid input blocks the operation visibly
  expect(screen.getByText(/Enter a positive number/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('charging power (W)'), { target: { value: '7000' } });
  fireEvent.click(preview);
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  const [, init] = (api.request as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(init.body.dry_run).toBe(true);
  expect(init.body.operations[0]).toMatchObject({ op: 'add_place', params: { id: 'pad-1', role: 'charging_pad', at: 'centre', power_w: 7000 } });
  expect(init.body.operations[0].params).not.toHaveProperty('preparation_s');
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
  const [applyPath, applyInit] = (api.request as ReturnType<typeof vi.fn>).mock.calls[1];
  expect(applyPath).toBe('/v1/studio/workspaces/w/operations');
  expect(applyInit.body.dry_run).toBeUndefined();
});

it('AddFleet previews a set_fleet shrink and names exactly the members to be removed', async () => {
  const api = operationsApi();
  const onProject = vi.fn();
  const fleetDraft = { entities: [
    { id: 'couriers-1', type: 'lowalt:UAV', facts: {} },
    { id: 'couriers-2', type: 'lowalt:UAV', facts: {} },
    { id: 'couriers-3', type: 'lowalt:UAV', facts: {} },
  ] } as never;
  render(<I18nProvider><AddFleet api={api as unknown as WorkbenchApi} project={project as never} draft={fleetDraft}
    onProject={onProject} L={L} onClose={() => undefined}
    editing={{ id: 'couriers', count: 3 }} /></I18nProvider>);
  expect(screen.getByText(/couriers-1, couriers-2, couriers-3/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('count (drones)'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: /Preview dry-run diff/ }));
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  const [, init] = (api.request as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(init.body.operations[0]).toMatchObject({ op: 'set_fleet', params: { fleet: 'couriers', count: 1 } });
  expect(await screen.findByText(/Shrinking this fleet removes: couriers-2, couriers-3/)).toBeTruthy();
});
