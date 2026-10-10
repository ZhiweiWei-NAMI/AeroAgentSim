import { useEffect } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../i18n/I18nProvider';
import { BehavioursPanel } from './BehavioursPanel';

vi.mock('../behaviours/BehaviourEditor', () => ({ BehaviourEditor: ({ value, onValidityChange }: any) => {
  useEffect(() => { onValidityChange(true); }, [onValidityChange]);
  return <div>Editing package {value.id}</div>;
} }));
beforeEach(() => { window.localStorage.setItem('aeroagentsim.locale', 'en-US'); });
const workspace: any = { id: 'w', scenario: {} };
const props = { workspace, api: { request: vi.fn() } as any, types: [], fields: [], relations: [], onChange: vi.fn(), onApply: vi.fn(), save: vi.fn(async () => workspace) };
const view = (scenario: any, valid: (valid: boolean) => void) => <I18nProvider><BehavioursPanel {...props} scenario={scenario} onValidityChange={valid} /></I18nProvider>;

it('keeps hooks stable and reports malformed collections as invalid', () => {
  const validity = vi.fn();
  const rendered = render(view({ behaviours: { unsupported: true } }, validity));
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(screen.getByRole('alert')).toHaveTextContent('Unsupported behaviour collection retained');
  rendered.rerender(view({ behaviours: [] }, validity));
  expect(validity).toHaveBeenLastCalledWith(true);
  expect(screen.getByText('No behaviour packages yet. Add one, give it a package ID, then define a condition or state chain.')).toBeInTheDocument();
});

it('selects a surviving package after the previously selected entry is removed', () => {
  const validity = vi.fn(), first = { id: 'first' }, second = { id: 'second' };
  const rendered = render(view({ behaviours: [first, second] }, validity));
  fireEvent.change(screen.getByRole('combobox', { name: 'Behaviour package' }), { target: { value: '1' } });
  expect(screen.getByText('Editing package second')).toBeInTheDocument();
  rendered.rerender(view({ behaviours: [first] }, validity));
  expect(screen.getByText('Editing package first')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Behaviour package' })).toHaveValue('0');
});

it('clears stale invalidity when an unsupported entry is removed', () => {
  const validity = vi.fn();
  const rendered = render(view({ behaviours: [42] }, validity));
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(screen.getByRole('alert')).toHaveTextContent('Unsupported package entry retained: 42');
  rendered.rerender(view({ behaviours: [] }, validity));
  expect(validity).toHaveBeenLastCalledWith(true);
});
