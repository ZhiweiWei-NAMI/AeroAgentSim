import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import * as i18n from '../i18n/I18nProvider';
import { AuthoringScope } from './BehaviourEditor';
import { ExpressionControl } from './ExpressionControl';

beforeEach(() => window.localStorage.setItem('aeroagentsim.locale', 'en-US'));
afterEach(() => { vi.restoreAllMocks(); });
function Harness({ initial, validity }: { initial: unknown; validity?: (valid: boolean) => void }) {
  const [value, setValue] = useState(initial);
  return <I18nProvider><AuthoringScope onValidityChange={validity}><ExpressionControl label="Value" value={value} onChange={setValue}/></AuthoringScope><pre data-testid="value">{value === undefined ? 'unset' : JSON.stringify(value)}</pre></I18nProvider>;
}

it('clears explicit null and populated values to undefined instead of an empty string', () => {
  render(<Harness initial={null}/>);
  fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'unset' } });
  expect(screen.getByTestId('value')).toHaveTextContent('unset');
  fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'string' } });
  fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'hello' } });
  fireEvent.change(screen.getByLabelText('Value value type'), { target: { value: 'unset' } });
  expect(screen.getByTestId('value')).toHaveTextContent('unset');
});

it('adds, edits and removes ordered array items without changing the remaining values', () => {
  render(<Harness initial={['waiting', 'done']}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Value add item' }));
  fireEvent.change(screen.getByLabelText('Value[2]'), { target: { value: 'review' } });
  fireEvent.click(screen.getByRole('button', { name: 'Value remove item 0' }));
  expect(screen.getByTestId('value')).toHaveTextContent('["done","review"]');
  fireEvent.click(screen.getByRole('button', { name: 'Value remove item 1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Value remove item 0' }));
  expect(screen.getByText('No items yet. Add an item to begin.')).toBeTruthy();
});

it('does not transfer an invalid buffer to an equal-valued item after removal', () => {
  const validity = vi.fn();
  render(<Harness initial={[1, 1]} validity={validity}/>);
  fireEvent.change(screen.getByLabelText('Value[0]'), { target: { value: 'bad' } });
  expect(validity).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: 'Value remove item 0' }));
  expect(screen.getByLabelText('Value[0]')).toHaveValue('1');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('can create an array or record from unset and manage record keys', () => {
  render(<Harness initial={undefined}/>);
  fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'array' } });
  fireEvent.change(screen.getByLabelText('Value value type'), { target: { value: 'record' } });
  fireEvent.change(screen.getByLabelText('Value new key'), { target: { value: 'custom' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add key' }));
  fireEvent.change(screen.getByLabelText('Value.custom'), { target: { value: 'kept' } });
  expect(screen.getByTestId('value')).toHaveTextContent('{"custom":"kept"}');
  fireEvent.click(screen.getByRole('button', { name: 'Value remove key custom' }));
  expect(screen.getByTestId('value')).toHaveTextContent('{}');
});

it('retains invalid numeric buffers, blocks authoring and recovers on external reset', () => {
  const change = vi.fn(), validity = vi.fn();
  const view = render(<I18nProvider><AuthoringScope onValidityChange={validity}><ExpressionControl label="Value" value={2} onChange={change}/></AuthoringScope></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Value'), { target: { value: '2e' } });
  expect(screen.getByLabelText('Value')).toHaveValue('2e');
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a finite number using JSON syntax');
  expect(change).not.toHaveBeenCalled();
  expect(validity).toHaveBeenLastCalledWith(false);
  view.rerender(<I18nProvider><AuthoringScope onValidityChange={validity}><ExpressionControl label="Value" value={3} onChange={change}/></AuthoringScope></I18nProvider>);
  expect(screen.getByLabelText('Value')).toHaveValue('3');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(validity).toHaveBeenLastCalledWith(true);
  fireEvent.change(screen.getByLabelText('Value'), { target: { value: '' } });
  expect(change).toHaveBeenLastCalledWith(undefined);
});

it('keeps exact integer tokens and unsupported extensions on numeric edits', () => {
  const change = vi.fn(), validity = vi.fn();
  render(<I18nProvider><AuthoringScope onValidityChange={validity}><ExpressionControl label="Value" value={{ $integer: '9007199254740993', future: 'keep' }} onChange={change}/></AuthoringScope></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Value exact integer'), { target: { value: '1.5' } });
  expect(screen.getByLabelText('Value exact integer')).toHaveValue('1.5');
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Value exact integer'), { target: { value: '9007199254740995' } });
  expect(change).toHaveBeenLastCalledWith({ $integer: '9007199254740995', future: 'keep' });
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('translates array controls and interpolated accessible labels using literal keys', () => {
  const copy: Record<string, string> = { 'Value type': '值类型', 'Remove item': '移除项', 'Add item': '添加项', '{label} remove item {index}': '从 {label} 移除第 {index} 项', '{label} add item': '向 {label} 添加项' };
  vi.spyOn(i18n, 'useI18n').mockReturnValue({ locale: 'zh-CN', setLocale: vi.fn(), t: (key: string, vars: Record<string, unknown> = {}) => Object.entries(vars).reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), copy[key] ?? key) });
  const change = vi.fn();
  render(<ExpressionControl label="states" value={['waiting']} onChange={change}/>);
  expect(screen.getByRole('button', { name: '从 states 移除第 0 项' })).toHaveTextContent('移除项');
  fireEvent.click(screen.getByRole('button', { name: '向 states 添加项' }));
  expect(change).toHaveBeenLastCalledWith(['waiting', '']);
});
