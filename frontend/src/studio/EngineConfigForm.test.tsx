import { useState } from 'react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { EngineConfigForm, engineDescriptorText } from './EngineConfigForm';
import { I18nProvider } from '../i18n/I18nProvider';

function Editor({ schema, initial = {}, changed = () => {}, validity = () => {} }: { schema?: unknown; initial?: Record<string, unknown>; changed?: (value: Record<string, unknown>) => void; validity?: (value: boolean) => void }) {
  const [value, setValue] = useState(initial);
  return <I18nProvider><EngineConfigForm schema={schema} value={value} onChange={next => { setValue(next); changed(next); }} onValidityChange={validity} /></I18nProvider>;
}

beforeEach(() => { window.localStorage.setItem('aeroagentsim.locale', 'en-US'); });
beforeAll(()=>{Object.defineProperty(window,'matchMedia',{writable:true,value:vi.fn(()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}}))});});
function choose(label:string, option:string) {
 fireEvent.mouseDown(screen.getByRole('combobox',{name:label}));
 fireEvent.click(screen.getByTitle(option));
}
describe('engine configuration descriptors', () => {
  it('edits nested objects, numbers, integer and typed enum members without applying defaults', () => {
    const changed = vi.fn(), validity = vi.fn();
    render(<Editor changed={changed} validity={validity} schema={{ type: 'object', required: ['rate'], properties: {
      rate: { type: 'number', minimum: 0 }, count: { type: 'integer' },
      nested: { type: 'object', properties: { label: { type: 'string' } } },
      mode: { type: 'integer', enum: [1, 2] }, enabled: { type: 'boolean', enum: [true, false] },
    } }} />);
    expect(changed).not.toHaveBeenCalled();
    expect(validity).toHaveBeenLastCalledWith(false);
    fireEvent.change(screen.getByLabelText('Config.rate'), { target: { value: '2.5' } });
    expect(changed).toHaveBeenLastCalledWith({ rate: 2.5 });
    expect(validity).toHaveBeenLastCalledWith(true);
    fireEvent.change(screen.getByLabelText('Config.nested.label'), { target: { value: 'source' } });
    choose('Config.mode','2');
    choose('Config.enabled','false');
    expect(changed).toHaveBeenLastCalledWith({ rate: 2.5, nested: { label: 'source' }, mode: 2, enabled: false });
    fireEvent.change(screen.getByLabelText('Config.count'), { target: { value: '3.5' } });
    expect(screen.getByRole('alert')).toHaveTextContent('expected integer');
    expect(validity).toHaveBeenLastCalledWith(false);
    fireEvent.change(screen.getByLabelText('Config.count'), { target: { value: '' } });
    expect(changed).toHaveBeenLastCalledWith({ rate: 2.5, nested: { label: 'source' }, mode: 2, enabled: false });
    fireEvent.click(screen.getByLabelText('Clear Config.rate'));
    expect(changed).toHaveBeenLastCalledWith({ nested: { label: 'source' }, mode: 2, enabled: false });
    expect(validity).toHaveBeenLastCalledWith(false);
  });

  it('labels fields with descriptor titles and units, plus genuine friendly names for demo parameters', () => {
    render(<Editor schema={{ type: 'object', properties: {
      max_speed_m_s: { type: 'number' },
      max_accel_m_s2: { type: 'number', unit: { symbol: 'm/s²' } },
      reserve_j: { type: 'number', unit: 'J', default: 10000 },
      custom: { type: 'number', title: 'Custom rate' },
    } }} />);
    expect(screen.getByText('Max speed (m/s)')).toBeInTheDocument();
    expect(screen.getByText('Max acceleration (m/s²)')).toBeInTheDocument();
    expect(screen.getByText('Energy reserve (J)')).toBeInTheDocument();
    expect(screen.getByText('Custom rate')).toBeInTheDocument();
  });

  it('resets only to explicitly declared defaults and clears values without one', () => {
    const changed = vi.fn();
    render(<Editor changed={changed} initial={{ reserve_j: 5000, speed: 8 }} schema={{ type: 'object', properties: {
      reserve_j: { type: 'number', unit: 'J', default: 10000 },
      speed: { type: 'number' },
    } }} />);
    expect(changed).not.toHaveBeenCalled();
    // Declared default differs from authored value -> reset restores the actual default.
    fireEvent.click(screen.getByLabelText('Reset Config.reserve_j'));
    expect(changed).toHaveBeenLastCalledWith({ reserve_j: 10000, speed: 8 });
    expect(screen.queryByLabelText('Reset Config.reserve_j')).toBeNull();
    expect(screen.queryByLabelText('Clear Config.reserve_j')).toBeNull();
    // No declared default -> clear removes the authored value entirely.
    fireEvent.click(screen.getByLabelText('Clear Config.speed'));
    expect(changed).toHaveBeenLastCalledWith({reserve_j:10000});
  });

  it('keeps optional booleans absent until authored and removes them explicitly', () => {
    const changed = vi.fn();
    render(<Editor changed={changed} schema={{ type: 'object', properties: { enabled: { type: 'boolean' } } }} />);
    expect(screen.getByRole('combobox',{name:'Config.enabled'})).toHaveValue('');
    expect(changed).not.toHaveBeenCalled();
    choose('Config.enabled','false');
    expect(changed).toHaveBeenLastCalledWith({ enabled: false });
    fireEvent.click(screen.getByLabelText('Clear Config.enabled'));
    expect(changed).toHaveBeenLastCalledWith({});
  });

  it.each([undefined, { type: 'object', properties: { rows: { type: 'array', items: { type: 'number' } } } }, { type: 'object', oneOf: [] }])('retains the raw editor for absent/unsupported descriptors (%j)', schema => {
    const changed = vi.fn(), validity = vi.fn();
    render(<Editor schema={schema} changed={changed} validity={validity} />);
    fireEvent.click(screen.getByTestId('details-trigger'));
    const raw = screen.getByLabelText('Plugin configuration JSON');
    fireEvent.change(raw, { target: { value: '{bad json' } });
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(validity).toHaveBeenLastCalledWith(false);
    expect(changed).not.toHaveBeenCalled();
    fireEvent.change(raw, { target: { value: '{"rows":[1.5],"enabled":false}' } });
    expect(changed).toHaveBeenLastCalledWith({ rows: [1.5], enabled: false });
    expect(validity).toHaveBeenLastCalledWith(true);
    fireEvent.change(raw, { target: { value: '[]' } });
    expect(screen.getByRole('alert')).toHaveTextContent('must be a JSON object');
    expect(validity).toHaveBeenLastCalledWith(false);
  });

  it('does not silently coerce existing values or drop opaque configuration members', () => {
    const changed = vi.fn();
    render(<Editor changed={changed} initial={{ rate: '2.5', opaque: ['retained'] }} schema={{ type: 'object', properties: { rate: { type: 'number' }, label: { type: 'string' } } }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('expected number');
    fireEvent.change(screen.getByLabelText('Config.label'), { target: { value: 'source' } });
    expect(changed).toHaveBeenLastCalledWith({ rate: '2.5', opaque: ['retained'], label: 'source' });
  });

  it('uses explicit translation keys for known dynamic descriptor titles and hints', () => {
    const t = vi.fn((key: string) => key === 'Maximum speed' ? '最大速度' : key === 'Energy capacity in joules.' ? '能量容量，单位为焦耳。' : key);
    expect(engineDescriptorText('Maximum speed', t)).toBe('最大速度');
    expect(t).toHaveBeenLastCalledWith('Maximum speed');
    expect(engineDescriptorText('Energy capacity in joules.', t)).toBe('能量容量，单位为焦耳。');
    expect(t).toHaveBeenLastCalledWith('Energy capacity in joules.');
    expect(engineDescriptorText('vendor-specific title', t)).toBe('vendor-specific title');
  });

  it('shows nested descriptor hints and does not repeat an existing unit', () => {
    render(<Editor schema={{ type: 'object', properties: {
      energy: { type: 'object', title: 'Energy', description: 'Energy capacity in joules.', properties: { capacity_j: { type: 'number' } } },
      speed: { type: 'number', title: 'Speed (m/s)', unit: 'm/s' },
    } }} />);
    expect(screen.getByText('Energy capacity in joules.')).toBeInTheDocument();
    expect(screen.getByText('Speed (m/s)')).toBeInTheDocument();
    expect(screen.queryByText('Speed (m/s) (m/s)')).toBeNull();
  });

  it('falls back safely when plugin descriptor labels are not strings', () => {
    render(<Editor schema={{ type: 'object', properties: { rate: { type: 'number', title: { unsafe: true } } } }} />);
    expect(screen.getByText('This descriptor needs features outside the form editor. Edit its configuration as JSON.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit configuration JSON' })).toBeInTheDocument();
  });

  it('rejects prototype-named extra properties when they are not declared', () => {
    render(<Editor initial={JSON.parse('{"__proto__":"authored"}')} schema={{ type: 'object', additionalProperties: false, properties: {} }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Config.__proto__: unknown property');
  });

  it('handles member names that match prototype names as ordinary authored keys', () => {
    const changed = vi.fn();
    const schema = JSON.parse('{"type":"object","properties":{"__proto__":{"type":"string"}}}');
    render(<Editor changed={changed} schema={schema} />);
    fireEvent.change(screen.getByLabelText('Config.__proto__'), { target: { value: 'authored' } });
    expect(JSON.stringify(changed.mock.lastCall?.[0])).toBe('{"__proto__":"authored"}');
  });
});
