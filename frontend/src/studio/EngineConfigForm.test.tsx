import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { EngineConfigForm } from './EngineConfigForm';

function Editor({ schema, initial = {}, changed = () => {}, validity = () => {} }: { schema?: unknown; initial?: Record<string, unknown>; changed?: (value: Record<string, unknown>) => void; validity?: (value: boolean) => void }) {
  const [value, setValue] = useState(initial);
  return <EngineConfigForm schema={schema} value={value} onChange={next => { setValue(next); changed(next); }} onValidityChange={validity} />;
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
    fireEvent.change(screen.getByLabelText('Config.mode'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('Config.enabled'), { target: { value: '1' } });
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

  it('keeps optional booleans absent until authored and removes them explicitly', () => {
    const changed = vi.fn();
    render(<Editor changed={changed} schema={{ type: 'object', properties: { enabled: { type: 'boolean' } } }} />);
    expect(screen.getByLabelText('Config.enabled')).toHaveValue('');
    expect(changed).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Config.enabled'), { target: { value: 'false' } });
    expect(changed).toHaveBeenLastCalledWith({ enabled: false });
    fireEvent.change(screen.getByLabelText('Config.enabled'), { target: { value: '' } });
    expect(changed).toHaveBeenLastCalledWith({});
  });

  it.each([undefined, { type: 'object', properties: { rows: { type: 'array', items: { type: 'number' } } } }, { type: 'object', oneOf: [] }])('retains the raw editor for absent/unsupported descriptors (%j)', schema => {
    const changed = vi.fn(), validity = vi.fn();
    render(<Editor schema={schema} changed={changed} validity={validity} />);
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

  it('handles member names that match prototype names as ordinary authored keys', () => {
    const changed = vi.fn();
    const schema = JSON.parse('{"type":"object","properties":{"__proto__":{"type":"string"}}}');
    render(<Editor changed={changed} schema={schema} />);
    fireEvent.change(screen.getByLabelText('Config.__proto__'), { target: { value: 'authored' } });
    expect(JSON.stringify(changed.mock.lastCall?.[0])).toBe('{"__proto__":"authored"}');
  });
});
