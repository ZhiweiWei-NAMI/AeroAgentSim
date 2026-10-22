import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import type { Project } from '../api';
import { ComponentsSection } from './ComponentsSection';
import { TasksSection } from './TasksSection';
import type { SectionProps } from './types';

type GatedProject = Project & { capabilities: { lowalt: boolean; region: boolean; network: boolean; city: boolean } };
const L = (en: string, _zh: string) => en;
const base: GatedProject = {
  id: 'gated', name: 'Gated project', revision: 1, updated_at: '', latest_version: null, dirty: true,
  scenario: {}, capabilities: { lowalt: false, region: false, network: false, city: false },
};

function props(project: GatedProject): SectionProps {
  return {
    api: {} as never, studioApi: {} as never, project, draft: project.scenario,
    edit: vi.fn(), replaceDraft: vi.fn(), zh: false, locale: 'en-US', L, issues: [],
    onProject: vi.fn(), saveDraft: async () => project, reportValidity: vi.fn(), goTo: vi.fn(),
  };
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); localStorage.setItem('aeroagentsim.locale', 'en-US'); });
afterEach(cleanup);

it('does not treat an engine merely named tasks as the low-altitude task engine', () => {
  const scenario = { engines: { tasks: { plugin: 'sumo', config: { generators: [{ id: 'road-orders', tasks: ['car-1'] }], cooperation: { watchers: { vehicles: ['car-1'] } } } } } };
  const project = { ...base, scenario };
  render(<I18nProvider><TasksSection {...props(project)} /></I18nProvider>);
  expect(screen.queryByText('Order streams')).toBeNull();
  expect(screen.queryByText('Cooperation')).toBeNull();
  expect(screen.queryByText('The tasks engine exists but declares no generators array; add one in File (YAML).')).toBeNull();
  expect(screen.getByTestId('wb-lowalt-missing')).toHaveTextContent('Standard low-altitude components are optional');
  expect(screen.queryByText(/This scenario has no low-altitude components/)).toBeNull();
});

it('finds the low-altitude task engine by plugin under any id and hides the opt-in', () => {
  const scenario = { engines: { dispatch: { plugin: 'lowalt_tasks', config: { generators: [{ id: 'parcels', kind: 'parcel', tasks: ['p1'] }] } } } };
  const project = { ...base, scenario, capabilities: { ...base.capabilities, lowalt: true } };
  render(<I18nProvider><TasksSection {...props(project)} /></I18nProvider>);
  expect(screen.getByText('Order streams')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Cooperation', level: 4 })).toBeVisible();
  expect(screen.queryByTestId('wb-lowalt-missing')).toBeNull();
});

it('keeps the generic engine tree reachable while framing installation as an opt-in', () => {
  const scenario = { engines: { tasks: { plugin: 'sumo', config: { step_ns: 1_000_000_000 } } } };
  const project = { ...base, scenario };
  render(<I18nProvider><ComponentsSection {...props(project)} /></I18nProvider>);
  const card = screen.getByTestId('wb-lowalt-missing');
  expect(card).toHaveTextContent('Standard low-altitude components are optional');
  expect(card).not.toHaveTextContent('nothing to attach to');
  fireEvent.click(screen.getByText('Advanced', { selector: '.wb-form-section > details > summary' }));
  expect(screen.getByText('tasks', { selector: 'b' })).toBeVisible();
  expect(screen.getByText('sumo')).toBeVisible();
});

it('does not prompt a low-altitude project to install the pack', () => {
  const scenario = { engines: {
    airspace: { plugin: 'lowalt_airspace', config: {} },
    dispatch: { plugin: 'lowalt_tasks', config: {} },
  } };
  const project = { ...base, scenario, capabilities: { ...base.capabilities, lowalt: true } };
  render(<I18nProvider><ComponentsSection {...props(project)} /></I18nProvider>);
  expect(screen.queryByTestId('wb-lowalt-missing')).toBeNull();
});
