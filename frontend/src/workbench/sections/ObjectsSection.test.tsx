import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { ObjectsSection } from './ObjectsSection';
import type { SectionProps } from './types';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; vi.clearAllMocks(); });

const base = {
  api: {}, studioApi: {}, project: { id: 'w', revision: 1 }, edit: vi.fn(), replaceDraft: vi.fn(),
  focus: undefined, L: (en: string, _zh?: string) => en, issues: [], onProject: vi.fn(), saveDraft: vi.fn(), reportValidity: vi.fn(),
} as unknown as SectionProps;

function setup(draft: Record<string, unknown>) {
  return render(<I18nProvider><ObjectsSection {...({ ...base, draft } as SectionProps)} /></I18nProvider>);
}

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
  fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
  expect(screen.getByLabelText('position ENU [east, north, up] (m)')).toBeTruthy();
  expect(screen.getAllByText(/written by the motion engine energy model|authored initial value/).length).toBeGreaterThan(0);
});

it('keeps the fleet remove control and generation rule explanation', () => {
  setup({ entities: [], generators: { fleet: { count: 3, type: 'oo:UAV' } } });
  expect(screen.getByText(/not existing instances/i)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Remove fleet' })).toBeTruthy();
});
