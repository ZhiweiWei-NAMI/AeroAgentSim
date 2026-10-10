import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { TypedPayloadForm } from './TypedPayloadForm';

beforeEach(() => window.localStorage.setItem('aeroagentsim.locale', 'en-US'));
function Harness({ schema, initial }: { schema: unknown; initial: unknown }) {
  const [value, setValue] = useState(initial);
  return <I18nProvider><TypedPayloadForm schema={schema} value={value} onChange={setValue}/><pre data-testid="value">{value === undefined ? 'unset' : JSON.stringify(value)}</pre></I18nProvider>;
}

it('shows invalid integer text while retaining the last valid payload and reporting invalidity', () => {
  const change = vi.fn(), validity = vi.fn();
  render(<I18nProvider><TypedPayloadForm schema={{ type: 'integer' }} value={{ $integer: '9007199254740993' }} onChange={change} onValidityChange={validity}/></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '12.5' } });
  expect(screen.getByLabelText('Payload')).toHaveValue('12.5');
  expect(screen.getByRole('alert')).toHaveTextContent('Enter an exact integer');
  expect(screen.getByLabelText('Payload')).toHaveAttribute('aria-invalid', 'true');
  expect(change).not.toHaveBeenCalled();
  expect(validity).toHaveBeenLastCalledWith(false);
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '9007199254740995' } });
  expect(change).toHaveBeenLastCalledWith({ $integer: '9007199254740995' });
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('accepts only finite JSON number tokens, not JavaScript-only numeric syntax', () => {
  const change = vi.fn(), validity = vi.fn();
  render(<I18nProvider><TypedPayloadForm schema={{ type: 'number' }} value={{ $number: '1.0' }} onChange={change} onValidityChange={validity}/></I18nProvider>);
  for (const value of ['0x10', ' ', '01', '1e999']) {
    fireEvent.change(screen.getByLabelText('Payload'), { target: { value } });
    expect(screen.getByLabelText('Payload')).toHaveValue(value);
    expect(change).not.toHaveBeenCalled();
    expect(validity).toHaveBeenLastCalledWith(false);
  }
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '1.25e2' } });
  expect(change).toHaveBeenLastCalledWith({ $number: '1.25e2' });
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '' } });
  expect(change).toHaveBeenLastCalledWith(undefined);
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('resets stale numeric errors on external value or schema changes, not equivalent rerenders', () => {
  const change = vi.fn(), validity = vi.fn();
  const view = render(<I18nProvider><TypedPayloadForm schema={{ type: 'integer' }} value={3} onChange={change} onValidityChange={validity}/></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: 'bad' } });
  view.rerender(<I18nProvider><TypedPayloadForm schema={{ type: 'integer' }} value={3} onChange={change} onValidityChange={validity}/></I18nProvider>);
  expect(screen.getByLabelText('Payload')).toHaveValue('bad');
  expect(validity).toHaveBeenLastCalledWith(false);
  view.rerender(<I18nProvider><TypedPayloadForm schema={{ type: 'integer' }} value={4} onChange={change} onValidityChange={validity}/></I18nProvider>);
  expect(screen.getByLabelText('Payload')).toHaveValue('4');
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: 'bad' } });
  view.rerender(<I18nProvider><TypedPayloadForm schema={{ type: 'boolean', enum: [false, true] }} value={true} onChange={change} onValidityChange={validity}/></I18nProvider>);
  expect(screen.getByLabelText('Payload')).toHaveValue('1');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('resets raw record errors when the payload is externally replaced', () => {
  const schema = { type: 'record', members: { reason: { type: 'string' } } };
  const change = vi.fn(), validity = vi.fn();
  const view = render(<I18nProvider><TypedPayloadForm schema={schema} value={{ reason: 'old' }} onChange={change} onValidityChange={validity}/></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Extra payload fields' }));
  fireEvent.change(screen.getByLabelText('Payload JSON'), { target: { value: '[]' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Record payload must be an object');
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(change).not.toHaveBeenCalled();
  view.rerender(<I18nProvider><TypedPayloadForm schema={schema} value={{ reason: 'new' }} onChange={change} onValidityChange={validity}/></I18nProvider>);
  expect(screen.getByLabelText('Payload.reason')).toHaveValue('new');
  expect(screen.getByLabelText('Payload JSON')).toHaveValue('{\n  "reason": "new"\n}');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('preserves explicit null until an edit and permits clearing a scalar value', () => {
  render(<Harness schema={{ type: 'string', nullable: true }} initial={null}/>);
  expect(screen.getByTestId('value')).toHaveTextContent('null');
  expect(screen.getByText('Current value is explicit null; edit to replace it.')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: 'hello' } });
  expect(screen.getByTestId('value')).toHaveTextContent('"hello"');
  fireEvent.click(screen.getByRole('button', { name: 'Payload set null' }));
  expect(screen.getByTestId('value')).toHaveTextContent('null');
  fireEvent.click(screen.getByRole('button', { name: 'Payload clear value' }));
  expect(screen.getByTestId('value')).toHaveTextContent('unset');
});

it('retains unrecognized enum values and uses scalar boolean/numeric choices without coercing IDs', () => {
  const change = vi.fn();
  const view = render(<I18nProvider><TypedPayloadForm schema={{ type: 'string', enum: ['queued', 'done'] }} value="future-state" onChange={change}/></I18nProvider>);
  expect(screen.getByLabelText('Payload')).toHaveValue('current');
  expect(screen.getByRole('option', { name: 'Current value: "future-state"' })).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '1' } });
  expect(change).toHaveBeenLastCalledWith('done');
  view.rerender(<I18nProvider><TypedPayloadForm schema={{ type: 'boolean', enum: [false, true] }} value={null} onChange={change}/></I18nProvider>);
  expect(screen.getByLabelText('Payload')).toHaveValue('current');
  expect(screen.getByRole('option', { name: 'Explicit null' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '0' } });
  expect(change).toHaveBeenLastCalledWith(false);
  view.rerender(<I18nProvider><TypedPayloadForm schema={{ type: 'number', enum: [1, 2] }} value={{ $number: '1.0' }} onChange={change}/></I18nProvider>);
  expect(screen.getByLabelText('Payload')).toHaveValue('0');
  fireEvent.change(screen.getByLabelText('Payload'), { target: { value: '1' } });
  expect(change).toHaveBeenLastCalledWith({ $number: '2' });
});

it('localizes known schema titles and member descriptions without changing member IDs', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  const schema = { type: 'record', title: 'Report details', title_zh: '报告详情', description: 'Operator event data', description_zh: '操作员事件数据', members: { reason: { type: 'string', title: 'Reason', title_zh: '原因', description: 'Why the event occurred', description_zh: '事件发生的原因' } } };
  render(<I18nProvider><TypedPayloadForm schema={schema} value={{ reason: 'test' }} onChange={() => {}}/></I18nProvider>);
  expect(screen.getByText('报告详情')).toBeTruthy();
  expect(screen.getByText('操作员事件数据')).toBeTruthy();
  expect(screen.getByText('事件发生的原因')).toBeTruthy();
  expect(screen.queryByText('Report details')).toBeNull();
  expect(screen.getByRole('textbox')).toHaveValue('test');
});
