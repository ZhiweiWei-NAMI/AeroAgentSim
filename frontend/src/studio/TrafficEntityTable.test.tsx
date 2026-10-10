import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../i18n/I18nProvider';
import { TrafficEntityTable } from './TrafficEntityTable';

beforeEach(() => { window.localStorage.setItem('aeroagentsim.locale', 'en-US'); });
const view = (props: Parameters<typeof TrafficEntityTable>[0]) => <I18nProvider><TrafficEntityTable {...props} /></I18nProvider>;

describe('TrafficEntityTable initial values', () => {
  it('exposes a keyboard-accessible expansion button and preserves typed enum members', () => {
    const changed = vi.fn();
    const scenario = { opaque: { keep: true }, entities: [{ id: 'uav.alpha', type: 'uav', facts: { mode: 1, enabled: false } }] };
    render(view({ scenario, onChange: changed, registryFields: [
      { id: 'mode', schema: { type: 'integer', enum: [1, 2] } },
      { id: 'enabled', schema: { type: 'boolean', enum: [true, false] } },
    ] }));
    const expand = screen.getByRole('button', { name: 'Show initial fields for uav.alpha' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(expand);
    expect(screen.getByRole('button', { name: 'Hide initial fields for uav.alpha' })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.change(screen.getByRole('combobox', { name: 'Field mode for entity uav.alpha' }), { target: { value: '1' } });
    expect(changed.mock.lastCall?.[0]).toEqual({ ...scenario, entities: [{ ...scenario.entities[0], facts: { mode: 2, enabled: false } }] });
    fireEvent.change(screen.getByRole('combobox', { name: 'Field enabled for entity uav.alpha' }), { target: { value: '0' } });
    expect(changed.mock.lastCall?.[0].entities[0].facts.enabled).toBe(true);
  });

  it('resolves role schemas and updates hidden groups when catalog fields change', () => {
    const scenario = { entities: [
      { id: 'background', type: 'actor', facts: { role: 'background' } },
      { id: 'without-role', type: 'actor', facts: {} },
    ] };
    const props = { scenario, onChange: vi.fn(), schemas: { role: { type: 'string', enum: ['background', 'candidate'] } } };
    const rendered = render(view({ ...props, registryFields: [] }));
    fireEvent.click(screen.getByLabelText('Hide background actors'));
    expect(screen.getByRole('button', { name: 'Show initial fields for background' })).toBeInTheDocument();
    rendered.rerender(view({ ...props, registryFields: [{ id: 'role', schema: { schema_ref: 'role' } }] }));
    expect(screen.queryByRole('button', { name: 'Show initial fields for background' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Show initial fields for without-role' })).toBeInTheDocument();
  });

  it('does not render an invalid authored boolean as an unchecked checkbox', () => {
    const changed = vi.fn();
    render(view({ scenario: { entities: [{ id: 'actor', type: 'actor', facts: { enabled: 'opaque-value' } }] }, onChange: changed, registryFields: [{ id: 'enabled', schema: { type: 'boolean' } }] }));
    fireEvent.click(screen.getByRole('button', { name: 'Show initial fields for actor' }));
    expect(screen.queryByRole('checkbox', { name: 'Field enabled for entity actor' })).toBeNull();
    expect(screen.getByRole('button', { name: 'View value' })).toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
  });

  it('finds authored generations inside nested references without inventing missing ones', () => {
    const scenario = { entities: [
      { id: 'target', type: 'actor', facts: {} },
      { id: 'unknown', type: 'actor', facts: {} },
      { id: 'source', type: 'actor', facts: { nested: { record: [{ $ref: { id: 'target', generation: 7 } }] } } },
    ] };
    render(view({ scenario, onChange: vi.fn() }));
    expect(screen.getByRole('button', { name: 'Show initial fields for target' }).closest('tr')).toHaveTextContent('7');
    expect(screen.getByRole('button', { name: 'Show initial fields for unknown' }).closest('tr')).toHaveTextContent('—');
    fireEvent.click(screen.getByRole('button', { name: 'Show initial fields for unknown' }));
    expect(screen.getByText('This entity has no initial fields.')).toBeInTheDocument();
  });
});
