import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { AddPlace } from './AddPlace';
import type { PlaceEditing } from './model';
import type { PlacePickerMapProps } from '../space/PlacePickerMap';

vi.mock('../space/PlacePickerMap', async importOriginal => ({ ...await importOriginal<object>(), PlacePickerMap: ({ position, onPick, L }: PlacePickerMapProps) => <div>
  <span role="status">{L('Click to place — east/north fill in automatically', '点击放置——东/北坐标会自动填入')}</span>
  <button onClick={() => onPick(80, -25, true)}>Map click</button>
  <output aria-label="Map marker">{JSON.stringify(position)}</output>
</div> }));
afterEach(cleanup);
beforeEach(() => sessionStorage.clear());
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
  expect(screen.getByLabelText('Id')).toHaveValue('charging-pad-2');
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(roles.getByRole('button', { name: 'Restaurant (kitchen)' }));
  expect(screen.getByLabelText('Id')).toHaveValue('kitchen-1');
  fireEvent.change(screen.getByLabelText('Id'), { target: { value: 'my-place' } });
  fireEvent.click(roles.getByRole('button', { name: 'Customer' }));
  expect(screen.getByLabelText('Id')).toHaveValue('my-place');
  expect(roles.getAllByRole('button', { pressed: true })).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('Id'), { target: { value: '' } });
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

it('Map click supplies east/north and preserves height; typed edits move the marker (T1-R3-004 / T2-R3-004 / T3-R3-001 / T4-R3-001)', async () => {
  const { api } = setup({ id: 'kitchen', role: 'kitchen', position: [100, 0, 10], preparation_s: 3 }, en, {
    project_detail: { region: { extract: 'test', bounds: [121, 31, 122, 32], alt: 0, level_height_m: 3.5 }, scene: { origin: { lat: 31.5, lon: 121.5, alt: 0 } } },
  });
  expect(screen.getByTestId('wb-place-picker')).toBeTruthy();
  expect(screen.getByText('Click the map to place it')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Map click' }));
  expect(screen.getByLabelText('Position from')).toHaveValue('map');
  expect(screen.getByLabelText('East (m)')).toHaveValue('80.0');
  expect(screen.getByLabelText('North (m)')).toHaveValue('-25.0');
  expect(screen.getByLabelText('Height (m)')).toHaveValue('10');
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('[80,-25]');
  fireEvent.change(screen.getByLabelText('East (m)'), { target: { value: '90' } });
  expect(screen.getByLabelText('Position from')).toHaveValue('typed');
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('[90,-25]');
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  expect(api.request.mock.calls[0][1].body.operations[0].params.at).toEqual([90, -25, 10]);
});

it('Typed is available without a region or facilities and sends the authoritative three inputs (T1-R3-004 / T3-R3-001)', async () => {
  const { api } = setup(undefined, en, { draft: { entities: [] } });
  fireEvent.change(screen.getByLabelText('Position from'), { target: { value: 'typed' } });
  expect(screen.queryByLabelText('Existing facility')).toBeNull();
  fireEvent.change(screen.getByLabelText('East (m)'), { target: { value: '150' } });
  fireEvent.change(screen.getByLabelText('North (m)'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText('Height (m)'), { target: { value: '7' } });
  expect(screen.getByLabelText('Position from')).toHaveValue('typed');
  expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  expect(api.request.mock.calls[0][1].body.operations[0].params.at).toEqual([150, 20, 7]);
});

const region = { region: { extract: 'test', bounds: [121, 31, 122, 32], alt: 0, level_height_m: 3.5 }, scene: { origin: { lat: 31.5, lon: 121.5, alt: 0 } } };

it('Existing facility copies its actual position into all inputs and the map marker (T2-R3-004 / T4-R3-001)', async () => {
  const { api } = setup(undefined, en, { project_detail: region });
  fireEvent.change(screen.getByLabelText('Position from'), { target: { value: 'facility' } });
  expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(screen.getByText('Choose an existing facility.')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Existing facility'), { target: { value: 'kitchen' } });
  expect(screen.getByLabelText('East (m)')).toHaveValue('100');
  expect(screen.getByLabelText('North (m)')).toHaveValue('0');
  expect(screen.getByLabelText('Height (m)')).toHaveValue('10');
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('[100,0]');
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Apply' });
  expect(api.request.mock.calls[0][1].body.operations[0].params.at).toEqual([100, 0, 10]);
});

it('uses the shared picker hint exactly once, in the active locale (D12)', () => {
  setup(undefined, zh, { project_detail: region });
  expect(screen.getAllByText('点击地图放置')).toHaveLength(1);
  expect(screen.queryByText(/点击放置——/)).toBeNull();
  expect(screen.queryByText('Click the map to place it')).toBeNull();
});

it('restores unapplied place inputs after close or Conversation and clears them only on apply or Discard (T2-R3-003)', async () => {
  const first = setup();
  fireEvent.change(screen.getByLabelText('Id'), { target: { value: 'unsaved-recovery-place' } });
  fireEvent.change(screen.getByLabelText('East (m)'), { target: { value: '123' } });
  fireEvent.change(screen.getByLabelText('Name (English)'), { target: { value: 'Unfinished station' } });
  first.unmount();
  const second = setup();
  expect(screen.getByLabelText('Id')).toHaveValue('unsaved-recovery-place');
  expect(screen.getByLabelText('East (m)')).toHaveValue('123');
  expect(screen.getByLabelText('Name (English)')).toHaveValue('Unfinished station');
  expect(screen.getByRole('note')).toHaveTextContent('Restored unsaved inputs');
  expect(second.api.request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(second.onProject).toHaveBeenCalled());
  expect(Object.keys(sessionStorage).filter(key => key.startsWith('aas.staged.'))).toHaveLength(0);
  second.unmount();
  setup();
  fireEvent.change(screen.getByLabelText('Id'), { target: { value: 'discard-this' } });
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(screen.getByLabelText('Id')).toHaveValue('charging-pad-2');
  expect(Object.keys(sessionStorage).filter(key => key.startsWith('aas.staged.'))).toHaveLength(0);
});
