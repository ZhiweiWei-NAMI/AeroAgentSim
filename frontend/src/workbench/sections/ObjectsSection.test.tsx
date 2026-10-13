import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { ObjectsSection } from './ObjectsSection';
import type { SectionProps } from './types';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; vi.clearAllMocks(); });

const base = {
  api: {}, studioApi: {}, project: { id: 'w', revision: 1, capabilities: { lowalt: true, region: false, network: false, city: false } },
  edit: vi.fn(), replaceDraft: vi.fn(),
  focus: undefined, L: (en: string, _zh?: string) => en, issues: [], onProject: vi.fn(), saveDraft: vi.fn(), reportValidity: vi.fn(),
} as unknown as SectionProps;

function setup(draft: Record<string, unknown>) {
  return render(<I18nProvider><ObjectsSection {...({ ...base, draft } as SectionProps)} /></I18nProvider>);
}

it('opens with the low-altitude install flow when capabilities.lowalt is false (T4-11)', () => {
  render(<I18nProvider><ObjectsSection {...({ ...base, project: { id: 'w', revision: 1 }, draft: {} } as SectionProps)} /></I18nProvider>);
  expect(screen.getByText(/Start with standard low-altitude components/)).toBeTruthy();
  expect(screen.getByText(/Adds the wind engine/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /Add standard low-altitude components/ })).toBeTruthy();
  expect(screen.queryByText('Add place')).toBeNull(); // gated until the components exist
  expect(screen.queryByTestId('wb-objects-empty')).toBeNull();
  expect(screen.queryByText(/No fleets yet/)).toBeNull();
  expect(screen.getAllByRole('button', { name: 'Add objects' })).toHaveLength(1);
});

it('shows zero counts and a real add entry point on a blank project, inventing nothing', () => {
  setup({});
  expect(screen.getByText('The world has no objects yet')).toBeTruthy();
  expect(screen.getByText('Add a place, fleet or object to start.')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: 'Add objects' }).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText('Explicit instances 0')).toBeTruthy();
  expect(screen.getByText('Will generate 0')).toBeTruthy();
  expect(screen.getByText('Expected total at t=0 0')).toBeTruthy();
});

it('separates explicit instances from generator members in the counts', () => {
  setup({ entities: [{ id: 'a', type: 't', facts: {} }, { id: 'b', type: 't', facts: {} }], generators: { fleet: { count: 2, type: 't' } } });
  expect(screen.getByText('Explicit instances 2')).toBeTruthy();
  expect(screen.getByText('Will generate 2')).toBeTruthy();
  expect(screen.getByText('Expected total at t=0 4')).toBeTruthy();
});

it('edits a generic position as three numeric inputs and keeps its strict vector (T1-019)', () => {
  setup({ entities: [{ id: 'uav-1', type: 'oo:UAV', facts: { 'aas.studio.position_enu_m': [1, 2, 3], 'aas.studio.energy_j': 90000 } }] });
  const edits = screen.getAllByRole('button', { name: 'Edit' });
  fireEvent.click(edits[edits.length - 1]); // the instance row's Edit, after the fleet section's
  expect(screen.getByRole('group', { name: 'Position ENU (m)' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '12' } });
  fireEvent.change(screen.getByLabelText('north (m)'), { target: { value: '34' } });
  fireEvent.change(screen.getByLabelText('up (m)'), { target: { value: '56' } });
  fireEvent.blur(screen.getByLabelText('up (m)'));
  expect(base.edit).toHaveBeenLastCalledWith('/entities/0/facts/aas.studio.position_enu_m', [12, 34, 56]);
  expect(screen.getAllByText(/written by the motion engine energy model|authored initial value/).length).toBeGreaterThan(0);
});

it('shows an airspace as polygon/floor/ceiling, not a moving point (T1-018)', async () => {
  setup({ entities: [{ id: 'zone-a', type: 'aas:StudioAirspace', facts: { 'aas.studio.airspace': { polygon: [[0, 0], [40, 0], [40, 30]], floor_m: 0, ceiling_m: 120 } } }] });
  const edits = screen.getAllByRole('button', { name: 'Edit' });
  fireEvent.click(edits[edits.length - 1]);
  fireEvent.click(screen.getByText('Advanced'));
  fireEvent.click(screen.getAllByText('aas.studio.airspace')[0]);
  expect(await screen.findByLabelText(/^floor_m/)).toHaveValue('0');
  expect(screen.getByLabelText(/^ceiling_m/)).toHaveValue('120');
  expect(screen.queryByRole('group', { name: 'Position ENU (m)' })).toBeNull();
  expect(screen.getByText(/no single moving point/i)).toBeTruthy();
});

it('keeps the fleet remove control and generation rule explanation', () => {
  setup({ entities: [], generators: { fleet: { count: 3, type: 'oo:UAV', prefix: 'fleet-' } } });
  expect(screen.getByText(/owned by the template/i)).toBeTruthy();
  // Per-member removal with the server dependency preview; no client-side fleet wipe (T1-009/T4-08).
  expect(screen.getAllByRole('button', { name: 'Remove' }).length).toBe(3);
});

it('opens the place editor at the saved ENU position instead of moving it to the origin', () => {
  setup({ entities: [{ id: 'pad-west', type: 'lowalt:ChargingPad', facts: { 'lowalt.site_position': [-25, 40, 10], 'lowalt.pad_power_w': 5000 } }] });
  fireEvent.click(within(screen.getAllByRole('table')[0]).getByRole('button', { name: 'Edit' }));
  const editor = within(screen.getByTestId('wb-add-place'));
  expect(editor.getByLabelText('position')).toHaveValue('enu');
  expect(editor.getByLabelText('east (m)')).toHaveValue('-25');
  expect(editor.getByLabelText('north (m)')).toHaveValue('40');
  expect(editor.getByLabelText('up (m)')).toHaveValue('10');
  expect(editor.getByLabelText('charging power (W)')).toHaveValue('5000');
});

it('keeps Add objects available after the first generic object without installing lowalt (T1-R2-002)', async () => {
  const props = { ...base, project: { id: 'w', revision: 2 },
    draft: { entities: [{ id: 'point', type: 'aas:StudioFacility', facts: {} }] },
    studioApi: { request: vi.fn().mockResolvedValue({ types: [{ id: 'aas:StudioFacility', parents: [], abstract: false }] }) },
  } as unknown as SectionProps;
  const view = render(<I18nProvider><ObjectsSection {...props} /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Add objects' }));
  expect(await screen.findByRole('option', { name: 'aas:StudioFacility' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Close add objects' }));
  view.rerender(<I18nProvider><ObjectsSection {...props} project={{ ...props.project, revision: 3 }} /></I18nProvider>);
  expect(screen.getByRole('button', { name: 'Add objects' })).toBeEnabled();
  expect(screen.queryByTestId('wb-objects-empty')).toBeNull();
});

it('does not write comma-separated or missing coordinates into the draft (T1-019)', () => {
  setup({ entities: [{ id: 'point', type: 'aas:StudioFacility', facts: { 'aas.studio.position_enu_m': [1, 2, 3] } }] });
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '12, 34, 56' } });
  fireEvent.blur(screen.getByLabelText('east (m)'));
  expect(base.edit).not.toHaveBeenCalled();
  expect(base.reportValidity).toHaveBeenLastCalledWith('/entities/0/facts/aas.studio.position_enu_m', false);
  expect(screen.getByRole('alert')).toHaveTextContent('Enter three finite numbers');
});

it('uses the position field declared by a generic presentation contract', () => {
  setup({ entities: [{ id: 'point', type: 'custom:Thing', facts: { 'custom.location': [1, 2, 3] } }],
    presentation: [{ typeId: 'custom:Thing', positionField: 'custom.location' }] });
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '-12' } });
  fireEvent.blur(screen.getByLabelText('east (m)'));
  expect(base.edit).toHaveBeenLastCalledWith('/entities/0/facts/custom.location', [-12, 2, 3]);
});

it('routes lowalt instance position edits through update_place, without adding a studio fact (T2-AS-003)', async () => {
  const pad = { id: 'pad', type: 'lowalt:ChargingPad', label: 'Central pad', label_zh: '中央充电坪',
    facts: { 'lowalt.site_position': [0, 0, 0], 'lowalt.site_capacity': 2, 'lowalt.pad_power_w': 5000 } };
  const updated = { ...base.project, revision: 2, scenario: { entities: [{ ...pad, facts: { ...pad.facts, 'lowalt.site_position': [12, 34, 56] } }] } };
  const api = { request: vi.fn().mockResolvedValue({ diff: { added: [], changed: [{ kind: 'place', id: 'pad' }], removed: [] }, notes: [] }), project: vi.fn().mockResolvedValue(updated) };
  render(<I18nProvider><ObjectsSection {...({ ...base, api, draft: { entities: [pad] } } as unknown as SectionProps)} /></I18nProvider>);
  fireEvent.click(within(screen.getAllByRole('table')[1]).getByRole('button', { name: 'Edit' }));
  const editor = within(screen.getByTestId('wb-add-place'));
  expect(editor.getByLabelText('capacity (parallel slots)')).toHaveValue('2');
  expect(editor.getByLabelText('charging power (W)')).toHaveValue('5000');
  for (const [label, value] of [['east (m)', '12'], ['north (m)', '34'], ['up (m)', '56']]) fireEvent.change(editor.getByLabelText(label), { target: { value } });
  fireEvent.click(editor.getByRole('button', { name: 'Preview' }));
  const apply = await editor.findByRole('button', { name: 'Apply' });
  expect(api.request.mock.calls[0][1].body.operations[0]).toMatchObject({ op: 'update_place', params: { id: 'pad', at: [12, 34, 56] } });
  expect(api.request.mock.calls[0][1].body.operations[0].params).not.toHaveProperty('role');
  expect(base.edit).not.toHaveBeenCalled();
  fireEvent.click(apply);
  await waitFor(() => expect(base.onProject).toHaveBeenLastCalledWith(updated));
  expect(updated.scenario.entities[0].facts).not.toHaveProperty('aas.studio.position_enu_m');
});
