import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { MatchEditor, TypedParameters } from './BehaviourEditor';

beforeEach(() => window.localStorage.setItem('aeroagentsim.locale', 'en-US'));

it('MatchEditor exposes match selectors so a new binding is not rejected for missing match', () => {
  const onChange = vi.fn();
  render(<I18nProvider><MatchEditor row={{ id: 'b0', chain: 'response', match: {} }} index={0} onChange={onChange} types={[{ id: 'lowalt:UAV', parents: [], abstract: false }]} /></I18nProvider>);
  expect(screen.getByText(/No match yet/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('binding 0 new match role'), { target: { value: 'uav' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add match role' }));
  expect(onChange).toHaveBeenLastCalledWith({ uav: {} });
});

it('MatchEditor edits entity type, entity id and field constraints of a selector', () => {
  const onChange = vi.fn();
  render(<I18nProvider><MatchEditor row={{ id: 'b', chain: 'c', match: { uav: { is_a: 'lowalt:UAV' } } }} index={1} onChange={onChange} types={[{ id: 'lowalt:UAV', parents: [], abstract: false }]} /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('binding 1 match uav entity'), { target: { value: 'courier-1' } });
  expect(onChange).toHaveBeenLastCalledWith({ uav: { is_a: 'lowalt:UAV', entity: 'courier-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove role' }));
  expect(onChange).toHaveBeenLastCalledWith({});
});

it('TypedParameters edits predicate parameters as typed fields with add/remove', () => {
  const onChange = vi.fn();
  function Parameters() {
    const [value, setValue] = useState<unknown>({ reserve_percent: 20 });
    return <I18nProvider><TypedParameters label="parameters" value={value} onChange={next => { onChange(next); setValue(next); }} /></I18nProvider>;
  }
  render(<Parameters />);
  fireEvent.change(screen.getByLabelText('parameters reserve_percent type'), { target: { value: 'string' } });
  expect(onChange).toHaveBeenLastCalledWith({ reserve_percent: '' });
  fireEvent.change(screen.getByLabelText('parameters new parameter'), { target: { value: 'horizontal_m' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add parameter' }));
  expect(onChange).toHaveBeenLastCalledWith({ reserve_percent: '', horizontal_m: 0 });
  fireEvent.click(screen.getAllByRole('button', { name: 'Remove parameter' })[1]);
  expect(onChange).toHaveBeenLastCalledWith({ reserve_percent: '' });
});
