import { fireEvent, render, screen } from '@testing-library/react';
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
  expect(screen.getByText(/Searching "UAV"/i)).toBeTruthy();
  expect(screen.getByRole('button', { name: /Add standard low-altitude components/ })).toBeTruthy();
  expect(screen.queryByText('Add place')).toBeNull(); // gated until the components exist
});

it('shows zero counts and a real add entry point on a blank project, inventing nothing', () => {
  setup({});
  expect(screen.getByText('The world has no objects yet')).toBeTruthy();
  expect(screen.getByText(/nothing is invented for you/i)).toBeTruthy();
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

it('labels the position leaf as ENU east/north/up in metres and shows writers read-only', () => {
  setup({ entities: [{ id: 'uav-1', type: 'oo:UAV', facts: { 'aas.studio.position_enu_m': [1, 2, 3], 'aas.studio.energy_j': 90000 } }] });
  const edits = screen.getAllByRole('button', { name: 'Edit' });
  fireEvent.click(edits[edits.length - 1]); // the instance row's Edit, after the fleet section's
  expect(screen.getByLabelText(/position ENU \[east, north, up\]/)).toBeTruthy();
  expect(screen.getAllByText(/written by the motion engine energy model|authored initial value/).length).toBeGreaterThan(0);
});

it('shows an airspace as polygon/floor/ceiling, not a moving point (T1-018)', () => {
  setup({ entities: [{ id: 'zone-a', type: 'aas:StudioAirspace', facts: { 'aas.studio.airspace': { polygon: [[0, 0], [40, 0], [40, 30]], floor_m: 0, ceiling_m: 120 } } }] });
  const edits = screen.getAllByRole('button', { name: 'Edit' });
  fireEvent.click(edits[edits.length - 1]);
  expect(screen.getByLabelText(/airspace: polygon, floor_m, ceiling_m/)).toBeTruthy();
  expect(screen.queryByLabelText(/position ENU/)).toBeNull();
  expect(screen.getByText(/no single moving point/i)).toBeTruthy();
});

it('keeps the fleet remove control and generation rule explanation', () => {
  setup({ entities: [], generators: { fleet: { count: 3, type: 'oo:UAV', prefix: 'fleet-' } } });
  expect(screen.getByText(/owned by the template/i)).toBeTruthy();
  // Per-member removal with the server dependency preview; no client-side fleet wipe (T1-009/T4-08).
  expect(screen.getAllByRole('button', { name: 'Remove' }).length).toBe(3);
});
