import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Project, WorkbenchApi } from '../../api';
import { ApiError } from '../../api';
import { AddFleet, type FleetEditing } from './AddFleet';

const L = (en: string, _zh: string) => en;
const draft = { entities: [{ id: 'depot', type: 'lowalt:Depot', facts: { 'lowalt.site_position': [100, 20, 3] } }] };
const project: Project = { id: 'fleet-workspace', name: 'Fleet workspace', revision: 7, scenario: draft, updated_at: '', latest_version: null, dirty: true };
beforeEach(() => sessionStorage.clear());
afterEach(cleanup);

function setup(editing?: FleetEditing, revision = 7) {
  const updated = { ...project, revision: revision + 1 };
  const review = { diff: { added: [], changed: [], removed: [] }, notes: [] };
  const api = { request: vi.fn().mockImplementation(async (_url, init) => init.body.dry_run ? review : updated), project: vi.fn().mockResolvedValue(updated) };
  const onProject = vi.fn(), onClose = vi.fn();
  const view = render(<AddFleet api={api as unknown as WorkbenchApi} project={{ ...project, revision }} draft={draft} onProject={onProject} L={L} onClose={onClose} editing={editing} />);
  return { ...view, api, onProject, onClose, updated };
}

it('restores fleet role, raw values and typed start after closing and reopening at a newer revision (T1-R3-003 / T2-R3-003)', async () => {
  const first = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Parcel drone' }));
  fireEvent.change(screen.getByLabelText('fleet id'), { target: { value: 'research-fleet' } });
  fireEvent.change(screen.getByLabelText('Count'), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText('Battery (%)'), { target: { value: '82' } });
  fireEvent.change(screen.getByLabelText('payload capacity (kg)'), { target: { value: '1.5' } });
  fireEvent.change(screen.getByLabelText('Start place'), { target: { value: 'enu' } });
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '42.' } });
  fireEvent.change(screen.getByLabelText('north (m)'), { target: { value: '-5' } });
  fireEvent.change(screen.getByLabelText('up (m)'), { target: { value: '12' } });
  first.unmount();
  const second = setup(undefined, 8);
  expect(screen.getByRole('button', { name: 'Parcel drone' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByLabelText('fleet id')).toHaveValue('research-fleet');
  expect(screen.getByLabelText('Count')).toHaveValue('7');
  expect(screen.getByLabelText('east (m)')).toHaveValue('42.');
  expect(screen.getByText('Restored unsaved inputs')).toBeTruthy();
  expect(second.api.request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  expect(second.api.request.mock.calls[0][1].body).toMatchObject({ revision: 8, operations: [{ op: 'add_fleet', params: {
    id: 'research-fleet', role: 'parcel_drone', count: 7, start: [42, -5, 12], battery_pct: 82, payload_capacity_kg: 1.5,
  } }] });
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(second.onProject).toHaveBeenCalledWith(second.updated));
  expect(Object.keys(sessionStorage)).toHaveLength(0);
});

it('keeps an invalid start visible on reopening and lets Discard clear it without applying a default', () => {
  const first = setup();
  fireEvent.change(screen.getByLabelText('Start place'), { target: { value: 'enu' } });
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '' } });
  first.unmount();
  const second = setup();
  expect(screen.getByLabelText('east (m)')).toHaveValue('');
  expect(screen.getByRole('alert')).toHaveTextContent('three finite coordinates');
  expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(second.api.request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(Object.keys(sessionStorage)).toHaveLength(0);
  expect(screen.getByLabelText('Start place')).toHaveValue('centre');
});

it('retains pending fleet edits on a rejected apply and sends a count-only edit without replacing member starts or battery', async () => {
  const first = setup({ id: 'couriers', count: 3, start: [10, 20, 3], battery_pct: 80 });
  fireEvent.change(screen.getByLabelText('Count'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  first.api.request.mockRejectedValueOnce(new ApiError(422, JSON.stringify({ message: 'Fleet cannot shrink / 机群无法缩减' })));
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Fleet cannot shrink'));
  expect(first.onProject).not.toHaveBeenCalled();
  expect(first.api.request.mock.calls[1][1].body.operations[0].params).toEqual({ fleet: 'couriers', count: 2 });
  first.unmount();
  setup({ id: 'couriers', count: 3, start: [10, 20, 3], battery_pct: 80 });
  expect(screen.getByLabelText('Count')).toHaveValue('2');
  expect(screen.getByText('Restored unsaved inputs')).toBeTruthy();
});
