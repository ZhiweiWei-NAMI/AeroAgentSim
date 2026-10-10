import { useEffect, useState } from 'react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { I18nProvider } from '../../i18n/I18nProvider';
import { DomainPluginsStep, parameterSchema } from './DomainPluginsStep';

vi.mock('../EngineConfigForm', async importOriginal => ({
  ...await importOriginal<object>(),
  EngineConfigForm: ({ value, onValidityChange }: any) => {
    const [valid, setValid] = useState(value.invalid !== true);
    useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
    return <div><span>Editable engine configuration</span><button type="button" onClick={() => setValid(false)}>Invalidate configuration</button></div>;
  },
}));
vi.mock('../../behaviours/ExpressionControl', () => ({ ExpressionControl: () => <div>Editable weather profiles</div> }));
beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) });
});
beforeEach(() => { window.localStorage.setItem('aeroagentsim.locale', 'en-US'); });
const engine = { id: 'kinematic', available: true, description: 'installed plugin', config_schema: { type: 'object', properties: {} }, capability_descriptor: { fields: [] } };
const view = (scenario: any, onValidityChange = vi.fn(), engines = [engine]) => <I18nProvider><DomainPluginsStep scenario={scenario} engines={engines} onChange={vi.fn()} onValidityChange={onValidityChange} /></I18nProvider>;

function choose(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }));
  fireEvent.click(screen.getByTitle(option));
}

describe('DomainPluginsStep draft controls', () => {
  it('names authored physics parameters with units and explains nanoseconds without supplying defaults', () => {
    const schema = parameterSchema({ max_speed_m_s: 20, step_ns: 1000000000, energy: { capacity_j: 100, per_m_j: 2 }, unknown: 9 }) as any;
    expect(schema.properties.max_speed_m_s.title).toBe('Max speed (m/s)');
    expect(schema.properties.step_ns.description).toBe('One second is 1,000,000,000 nanoseconds.');
    expect(schema.properties.energy.properties.capacity_j.title).toBe('Energy capacity (J)');
    expect(schema.properties.unknown).toBeUndefined();
    expect(schema.properties.max_speed_m_s.default).toBeUndefined();
  });
  it('makes unavailable configurations and weather profiles genuinely read-only', () => {
    const scenario = { engines: { weather: { plugin: 'weather', config: { profiles: { custom: [1] } } } } };
    render(view(scenario, vi.fn(), [{ ...engine, id: 'weather', available: false }]));
    fireEvent.click(screen.getByText('Weather & environment'));
    expect(screen.getByText('This plugin is not available in the current deployment; its configuration is shown read-only.')).toBeInTheDocument();
    expect(screen.queryByText('Editable engine configuration')).toBeNull();
    expect(screen.queryByText('Editable weather profiles')).toBeNull();
    expect(screen.getByRole('button', { name: 'View configuration JSON' })).toBeInTheDocument();
  });

  it('ignores stale invalidity from partitions no longer in the current scenario', () => {
    const validity = vi.fn();
    const rendered = render(view({ engines: { air_motion: { plugin: 'kinematic', config: {} } } }, validity));
    fireEvent.click(screen.getByRole('button', { name: 'Invalidate configuration' }));
    expect(validity).toHaveBeenLastCalledWith(false);
    rendered.rerender(view({ engines: {} }, validity));
    expect(validity).toHaveBeenLastCalledWith(true);
  });

  it('does not let an unused replacement draft block scenario validation', () => {
    const validity = vi.fn();
    render(view({ engines: { air_motion: { plugin: 'kinematic', config: {} } } }, validity));
    fireEvent.click(screen.getByRole('button', { name: 'Advanced plugin replacement' }));
    choose('Profile partition', 'Air motion (air_motion) · kinematic');
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Invalidate configuration' }));
    expect(within(dialog).getByRole('button', { name: 'Apply profile to draft' })).toBeDisabled();
    expect(validity).toHaveBeenLastCalledWith(true);
  });

  it('does not carry invalidity from the old plugin into a replacement supplied externally', () => {
    const validity = vi.fn(), engines = [engine, { ...engine, id: 'kinematic_alt' }];
    const rendered = render(view({ engines: { air_motion: { plugin: 'kinematic', config: {} } } }, validity, engines));
    fireEvent.click(screen.getByRole('button', { name: 'Invalidate configuration' }));
    expect(validity).toHaveBeenLastCalledWith(false);
    rendered.rerender(view({ engines: { air_motion: { plugin: 'kinematic_alt', config: {} } } }, validity, engines));
    expect(validity).toHaveBeenLastCalledWith(true);
  });
});
