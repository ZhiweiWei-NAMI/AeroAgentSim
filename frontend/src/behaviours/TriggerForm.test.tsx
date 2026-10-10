import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { beforeEach, it, expect, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { AuthoringScope } from './BehaviourEditor';
import { TriggerForm } from './TriggerForm';

beforeEach(() => window.localStorage.setItem('aeroagentsim.locale', 'en-US'));
function Harness({ initial, validity }: { initial: unknown; validity: (valid: boolean) => void }) {
  const [value, setValue] = useState(initial);
  return <I18nProvider><AuthoringScope onValidityChange={validity}><TriggerForm label="Rule" initial predicates={['ready']} value={value} onChange={setValue}/></AuthoringScope><pre data-testid="value">{JSON.stringify(value)}</pre></I18nProvider>;
}

it('switches trigger category while preserving unsupported authored extensions', () => {
  const change = vi.fn();
  render(<I18nProvider><TriggerForm label="Rule" predicates={['ready']} value={{ predicate: 'ready', edge: 'entered', future: { nullable: null } }} onChange={change}/></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Rule category'), { target: { value: 'event' } });
  expect(change).toHaveBeenLastCalledWith({ event: '', future: { nullable: null } });
});

it('reports invalid exact-nanosecond buffers and keeps large integers lossless', () => {
  const validity = vi.fn();
  render(<Harness initial={{ timer: 'start', after_ns: 1, future: 'keep' }} validity={validity}/>);
  const input = screen.getByLabelText('Rule after_ns (exact integer nanoseconds)');
  fireEvent.change(input, { target: { value: '1.5' } });
  expect(input).toHaveValue('1.5');
  expect(screen.getByRole('alert')).toHaveTextContent('Exact integer nanoseconds required');
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(screen.getByTestId('value')).toHaveTextContent('"after_ns":1');
  fireEvent.change(input, { target: { value: '9007199254740993' } });
  expect(validity).toHaveBeenLastCalledWith(true);
  expect(screen.getByTestId('value')).toHaveTextContent('"after_ns":{"$integer":"9007199254740993"}');
  fireEvent.change(input, { target: { value: '' } });
  expect(screen.getByTestId('value')).toHaveTextContent('{"timer":"start","future":"keep"}');
});

it('includes raw JSON buffers in scope validity and clears errors on external replacement', () => {
  const validity = vi.fn(), change = vi.fn();
  const view = render(<I18nProvider><AuthoringScope onValidityChange={validity}><TriggerForm label="Rule" predicates={[]} value={{}} onChange={change}/></AuthoringScope></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  fireEvent.change(screen.getByLabelText('Rule trigger JSON'), { target: { value: '{"event":' } });
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(change).not.toHaveBeenCalled();
  view.rerender(<I18nProvider><AuthoringScope onValidityChange={validity}><TriggerForm label="Rule" predicates={[]} value={null} onChange={change}/></AuthoringScope></I18nProvider>);
  expect(screen.getByLabelText('Rule trigger JSON')).toHaveValue('null');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('cleans up removed correlation buffers without transferring them to another key', () => {
  const validity = vi.fn();
  render(<Harness initial={{ event: 'observed', correlation: { first: 1, second: 1 } }} validity={validity}/>);
  fireEvent.change(screen.getByLabelText('Rule correlation value 0'), { target: { value: 'bad' } });
  expect(validity).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: 'Rule correlation remove first' }));
  expect(screen.getByLabelText('Rule correlation value 0')).toHaveValue('1');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(validity).toHaveBeenLastCalledWith(true);
});

it('rejects duplicate correlation-key buffers and commits only a valid rename', () => {
  const change = vi.fn(), validity = vi.fn();
  render(<I18nProvider><AuthoringScope onValidityChange={validity}><TriggerForm label="Rule" predicates={[]} value={{ event: 'observed', correlation: { task: 'first', other: 'second' }, future: true }} onChange={change}/></AuthoringScope></I18nProvider>);
  const key = screen.getByLabelText('Rule correlation key 0');
  fireEvent.change(key, { target: { value: 'other' } });
  fireEvent.blur(key);
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a unique, non-empty key');
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(key, { target: { value: 'renamed' } });
  fireEvent.blur(key);
  expect(change).toHaveBeenLastCalledWith({ event: 'observed', correlation: { renamed: 'first', other: 'second' }, future: true });
  expect(validity).toHaveBeenLastCalledWith(true);
});
