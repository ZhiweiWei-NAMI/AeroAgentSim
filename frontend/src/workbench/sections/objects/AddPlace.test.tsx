import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { AddPlace } from './AddPlace';
import type { PlaceEditing } from './model';
import type { PlacePickerMapProps } from '../space/PlacePickerMap';

vi.mock('../space/PlacePickerMap', async importOriginal => ({ ...await importOriginal<object>(), PlacePickerMap: ({ position, onPick }: PlacePickerMapProps) => <div>
  <button onClick={() => onPick(80, -25, true)}>Map click</button>
  <output aria-label="Map marker">{JSON.stringify(position)}</output>
</div> }));
afterEach(cleanup);
const en = (text: string) => text, zh = (_en: string, text: string) => text;
const project = { id: 'w', revision: 7 };
const applied = { ...project, revision: 8, scenario: { entities: [] } };
const kitchen = { id: 'kitchen', type: 'lowalt:Restaurant', label: 'Kitchen', label_zh: '厨房',
  facts: { 'lowalt.site_position': [100, 0, 10], 'lowalt.site_capacity': 1, 'lowalt.food_preparation_s': 3 } };

function setup(editing?: PlaceEditing, L: (en: string, zh: string) => string = en, extra: Record<string, unknown> = {}) {
  const api = { request: vi.fn().mockResolvedValue({ diff: { added: [], changed: [{ kind: 'place', id: editing?.id ?? 'pad' }], removed: [] }, notes: [] }),
    project: vi.fn().mockResolvedValue(applied) };
  const onProject = vi.fn(), onClose = vi.fn();
  const props = { api, project, draft: { entities: [kitchen, { id: 'charging-pad-1', type: 'lowalt:ChargingPad', facts: {} }] }, onProject, onClose, L, editing, ...extra };
  const view = render(<AddPlace {...props as unknown as ComponentProps<typeof AddPlace>} />);
  return { ...view, api, onProject, onClose, props };
}

it('defaults to exactly one selected role and a unique editable id without initial errors (D7/T4-R2-013)', () => {
  setup();
  const roles = within(screen.getByRole('group', { name: 'Place role' }));
  expect(roles.getAllByRole('button', { pressed: true })).toHaveLength(1);
  expect(roles.getByRole('button', { name: 'Charging pad' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByLabelText('id')).toHaveValue('charging-pad-2');
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(roles.getByRole('button', { name: 'Restaurant (kitchen)' }));
  expect(screen.getByLabelText('id')).toHaveValue('kitchen-1');
  fireEvent.change(screen.getByLabelText('id'), { target: { value: 'my-place' } });
  fireEvent.click(roles.getByRole('button', { name: 'Customer' }));
  expect(screen.getByLabelText('id')).toHaveValue('my-place');
  expect(roles.getAllByRole('button', { pressed: true })).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('id'), { target: { value: '' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a unique id');
  expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
});

it('prefills kitchen service values and sends the edited preparation time (T1-R2-006)', async () => {
  const { api, onProject } = setup({ id: 'kitchen', type: 'lowalt:Restaurant', position: [100, 0, 10], capacity: 1, preparation_s: 3, label: 'Kitchen', label_zh: '厨房' });
  expect(screen.getByText('Role: Restaurant (kitchen)')).toBeTruthy();
  expect(screen.getByLabelText('preparation time (s)')).toHaveValue('3');
  expect(screen.getByLabelText('capacity (parallel slots)')).toHaveValue('1');
  expect(screen.getByLabelText('Name (English)')).toHaveValue('Kitchen');
  fireEvent.change(screen.getByLabelText('preparation time (s)'), { target: { value: '12' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(onProject).toHaveBeenCalledWith(applied));
  expect(api.request.mock.calls[0][1].body.operations[0]).toMatchObject({ op: 'update_place', params: { id: 'kitchen', preparation_s: 12 } });
  expect(api.request.mock.calls[1][1].body.operations[0].params).toEqual({ id: 'kitchen', preparation_s: 12 });
});

it('exposes charging capacity and power together with their actual current values (T2-R2-007)', async () => {
  const { api } = setup({ id: 'pad', role: 'charging_pad', position: [-80, 0, 0], capacity: 2, power_w: 5000 });
  expect(screen.getByLabelText('capacity (parallel slots)')).toHaveValue('2');
  expect(screen.getByLabelText('charging power (W)')).toHaveValue('5000');
  fireEvent.change(screen.getByLabelText('capacity (parallel slots)'), { target: { value: '4' } });
  fireEvent.change(screen.getByLabelText('charging power (W)'), { target: { value: '9000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  expect(api.request.mock.calls[0][1].body.operations[0].params).toEqual({ id: 'pad', capacity: 4, power_w: 9000 });
  expect(screen.queryByLabelText('preparation time (s)')).toBeNull();
});

it('rejects nonpositive preparation and charging power visibly without sending defaults', () => {
  const { api } = setup();
  fireEvent.change(screen.getByLabelText('charging power (W)'), { target: { value: '0' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a positive number');
  expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Restaurant (kitchen)' }));
  fireEvent.change(screen.getByLabelText('preparation time (s)'), { target: { value: '-1' } });
  expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(api.request).not.toHaveBeenCalled();
});

it('selects only the current locale for roles and language-field labels (T4-R2-014)', () => {
  setup(undefined, zh);
  expect(screen.getByRole('button', { name: '充电坪' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByLabelText('名称（英文）')).toBeTruthy();
  expect(screen.queryByText('Charging pad')).toBeNull();
  expect(screen.queryByText('Name (Chinese)')).toBeNull();
});

it('keeps the current picker mounted after a click and follows typed coordinates, preserving height (D7)', async () => {
  const { api } = setup({ id: 'kitchen', role: 'kitchen', position: [100, 0, 10], preparation_s: 3 }, en, {
    project_detail: { region: { extract: 'test', bounds: [121, 31, 122, 32], alt: 0, level_height_m: 3.5 }, scene: { origin: { lat: 31.5, lon: 121.5, alt: 0 } } },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Pick on map' }));
  fireEvent.click(screen.getByRole('button', { name: 'Map click' }));
  expect(screen.getByLabelText('east (m)')).toHaveValue('80.0');
  expect(screen.getByLabelText('north (m)')).toHaveValue('-25.0');
  expect(screen.getByLabelText('up (m)')).toHaveValue('10');
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('[80,-25]');
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '90' } });
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('[90,-25]');
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  expect(api.request.mock.calls[0][1].body.operations[0].params.at).toEqual([90, -25, 10]);
});
